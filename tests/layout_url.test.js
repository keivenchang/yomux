const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class TestClassList {
  constructor() {
    this.names = new Set();
  }

  add(...names) {
    names.forEach(name => this.names.add(name));
  }

  remove(...names) {
    names.forEach(name => this.names.delete(name));
  }

  toggle(name, force) {
    if (force === true) {
      this.names.add(name);
      return true;
    }
    if (force === false) {
      this.names.delete(name);
      return false;
    }
    if (this.names.has(name)) {
      this.names.delete(name);
      return false;
    }
    this.names.add(name);
    return true;
  }

  contains(name) {
    return this.names.has(name);
  }
}

class TestStyle {
  constructor() {
    this.properties = new Map();
  }

  setProperty(name, value) {
    this.properties.set(name, value);
  }

  removeProperty(name) {
    this.properties.delete(name);
  }

  getPropertyValue(name) {
    return this.properties.get(name) || '';
  }
}

class TestElement {
  constructor(id = '') {
    this.id = id;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.innerHTML = '';
    this.textContent = '';
    this.removed = false;
    this.parentElement = null;
    this.scrollTop = 0;
    this.clientHeight = 200;
    this.isConnected = true;
    this.rect = {width: 1200, height: 800, left: 0, top: 0, right: 1200, bottom: 800};
    this.style = new TestStyle();
    this.classList = new TestClassList();
    this.listeners = new Map();
  }

  get className() { return Array.from(this.classList.names).join(' '); }
  set className(value) {
    this.classList = new TestClassList();
    String(value || '').split(/\s+/).filter(Boolean).forEach(name => this.classList.add(name));
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener(type, listener) {
    const items = this.listeners.get(type) || [];
    this.listeners.set(type, items.filter(item => item !== listener));
  }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
  insertBefore(node, before) {
    const existingIndex = this.children.indexOf(node);
    if (existingIndex >= 0) this.children.splice(existingIndex, 1);
    const index = before ? this.children.indexOf(before) : -1;
    node.parentElement = this;
    if (index >= 0) this.children.splice(index, 0, node);
    else this.children.push(node);
    return node;
  }
  insertAdjacentElement(position, node) {
    if (position !== 'afterend' || !this.parentElement) return this.appendChild(node);
    const siblings = this.parentElement.children;
    const existingIndex = siblings.indexOf(node);
    if (existingIndex >= 0) siblings.splice(existingIndex, 1);
    const index = siblings.indexOf(this);
    node.parentElement = this.parentElement;
    siblings.splice(index + 1, 0, node);
    return node;
  }
  get lastElementChild() {
    return this.children[this.children.length - 1] || null;
  }
  get nextElementSibling() {
    const siblings = this.parentElement?.children || [];
    const index = siblings.indexOf(this);
    return index >= 0 ? siblings[index + 1] || null : null;
  }
  cloneNode() {
    const clone = new TestElement(`${this.id}-clone`);
    clone.dataset = {...this.dataset};
    clone.attributes = {...this.attributes};
    clone.innerHTML = this.innerHTML;
    clone.textContent = this.textContent;
    clone.rect = {...this.rect};
    return clone;
  }
  contains(node) { return node === this || this.children.includes(node); }
  getBoundingClientRect() { return this.rect; }
  insertAdjacentHTML() {}
  matches(selector) {
    if (selector === ':hover') return this.hovered === true;
    const dataPaneTabMatch = selector.match(/^\.pane-tab\[data-pane-tab="([^"]+)"\]$/);
    if (dataPaneTabMatch) {
      return this.classList.contains('pane-tab') && this.dataset.paneTab === dataPaneTabMatch[1];
    }
    if (selector === '[role="tree"]') return this.attributes.role === 'tree';
    if (selector === '.file-explorer-tree-panel') return this.classList.contains('file-explorer-tree-panel');
    if (selector === '.file-tree-row[data-path]') return this.classList.contains('file-tree-row') && Boolean(this.dataset.path);
    if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
    return false;
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
  querySelector(selector) {
    if (selector.startsWith(':scope > .')) {
      const className = selector.slice(':scope > .'.length);
      if (className.includes('[')) {
        return this.children.find(child => child.matches(`.${className}`)) || null;
      }
      return this.children.find(child => child.classList?.contains(className)) || null;
    }
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    if (selector.startsWith(':scope > .')) {
      const className = selector.slice(':scope > .'.length);
      const scopedSelector = className.includes('[') ? `.${className}` : null;
      return this.children.filter(child => (
        scopedSelector ? child.matches(scopedSelector) : child.classList?.contains(className)
      ));
    }
    const matches = [];
    const visit = node => {
      for (const child of node.children || []) {
        if (child.matches(selector)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
  remove() {
    this.removed = true;
    if (this.parentElement) {
      const index = this.parentElement.children.indexOf(this);
      if (index >= 0) this.parentElement.children.splice(index, 1);
    }
    this.parentElement = null;
  }
  scrollIntoView() {}
  getAttribute(name) { return this.attributes[name]; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  replaceChildren(...nodes) {
    this.children.forEach(node => {
      node.parentElement = null;
    });
    this.children = [];
    nodes.forEach(node => this.appendChild(node));
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}

function assertNoStandalonePrBadge(html, label) {
  assert.equal(html.includes('>PR<'), false, `${label} avoids redundant PR text`);
  assert.equal(/class="[^"]*pr-indicator[^"]*"[^>]*>PR</.test(html), false, `${label} never renders a standalone PR text pill`);
  assert.equal(html.includes('pr-indicator'), false, `${label} suppresses the separate PR number badge`);
}

function assertSingleCiBadge(html, label) {
  assert.ok(html.includes('>CI</span>'), `${label} renders a CI badge`);
  assert.equal((html.match(/>CI<\/span>/g) || []).length, 1, `${label} renders exactly one CI badge`);
}

function loadYolomux(search = '', sessions = ['1', '2', '3', '4', '5', '6'], protocol = 'http:', navigatorPlatform = 'Linux x86_64', accessRole = 'admin') {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const bootStart = source.indexOf("if (refreshMeta) {");
  assert.ok(bootStart > 0, 'could not find browser boot section');

  const bootstrap = JSON.stringify({
    sessions,
    availableAgents: [],
    accessRole,
    homePath: '/home/test',
    repoRoot: '/home/test/yolomux.dev',
    maxSessionTabs: 99,
    serverHostname: 'test-host',
  });
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new TestElement(id));
    const node = elements.get(id);
    if (id === 'yolomux-bootstrap') node.textContent = bootstrap;
    return node;
  };
  const context = {
    console,
    URLSearchParams,
    WebSocket: {OPEN: 1, CLOSING: 2, CLOSED: 3},
    clearInterval() {},
    clearTimeout() {},
    document: {
      addEventListener() {},
      body: element('body'),
      createElement: tag => new TestElement(tag),
      documentElement: element('html'),
      getElementById: element,
      querySelector: () => null,
      querySelectorAll: () => [],
      removeEventListener() {},
    },
    fetch() { return Promise.reject(new Error('fetch disabled in layout URL tests')); },
    history: {
      replaceState(_state, _title, url) {
        context.__lastUrl = url;
      },
    },
    location: {search, pathname: '/', hash: '', protocol, hostname: 'localhost', port: '7777', host: 'localhost:7777'},
    navigator: {platform: navigatorPlatform, userAgent: navigatorPlatform},
    Notification: {permission: 'denied'},
    performance: {now: () => 0},
    requestAnimationFrame(callback) { return callback(); },
    setInterval() {},
    setTimeout() {},
    window: {
      addEventListener() {},
      innerHeight: 800,
      innerWidth: 1200,
      removeEventListener() {},
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(`${source.slice(0, bootStart)}
globalThis.__layoutTestApi = {
  activeItemForSide,
  agentErrorIsBlocking,
  appModifier,
  appMenuTree,
  appShortcutText,
  t,
  tPlural,
  i18nActiveLocaleId,
  i18nSetCatalogForTest,
  setActiveLocaleForTest(locale) { i18nActiveLocale = locale; },
  createAppMenuCommand,
  backgroundTabItems,
  canPaneExpand,
  codeMirrorHtmlSemanticEmphasisExtension,
  codeMirrorApiIsUsable,
  codeMirrorLanguageExtension,
  codeMirrorHighlightExtension,
  codeMirrorMarkdownCodeLanguages,
  createEditableCodeMirrorState,
  codeMirrorPlainEditableExtensions,
  codeMirrorSearchMatches,
  codeMirrorSearchMatchSummary,
  emptyPlaceholderPaneState,
  emptyLayoutSlots,
  fileEditorPaneTabHtml,
  fileQuickOpenItem,
  fileQuickOpenItems,
  fileQuickOpenRootForSearch,
  fileQuickOpenRootsForSearch,
  fileQuickOpenScopeLabel,
  fileExplorerDirectoryIsIndexed,
  fileExplorerIndexBadgeText,
  setFileExplorerIndexedDirsForTest(paths) { setFileExplorerIndexedDirs(paths); },
  setFileExplorerIndexStatusForTest(root, status) { fileExplorerIndexStatus.set(normalizeStoredFileExplorerIndexedDir(root), status); },
  createTopbarSearch,
  codeMirrorDiffLayout,
  changesPaneTabHtml,
  changesPanelHtml,
  fileExplorerChangesPanelHtml,
  changeFileRowHtml,
  diffRefControlsHtml,
  diffRefSelectOptionsHtml,
  diffRefParams,
  diffRefFromSuggestions,
  diffRefToSuggestions,
  parseUnifiedDiffLineClasses,
  globalActivitySummaryHtml,
  yoagentSessionSummariesHtml,
  yoagentChatHtml,
  setYoagentDraftForTest(value) { yoagentDraft = String(value || ''); },
  setYoagentBusyForTest(value) { yoagentBusy = Boolean(value); },
  setYoagentErrorForTest(value) { yoagentError = String(value || ''); },
  setYoagentNoticeForTest(value) { yoagentNotice = value; },
  sessionActivitySummary,
  fitAppMenuPopover,
  finderDirectoryForItem,
  finderTargetPathForItem,
  activeFinderDirectoryPath,
  activeFinderTargetPath,
  activeTmuxDirectoryPath,
  cancelPendingFileExplorerActiveSync,
  fileExplorerRootForOpen,
  fileExplorerRootModeValue,
  setFileExplorerRootMode,
  fileExplorerLabel,
  fileExplorerPanelCloseClass,
  fileEditorPanelCloseClass,
  fileIconFor,
  fileIconClassFor,
  fileExplorerNeedsLeftDock,
  fileExplorerPaneTabHtml,
  firstEmptyPane,
  filePopoverRows,
  fuzzySearchScore,
  fuzzyHighlightHtml,
  fuzzySubsequenceMatch,
  fuzzySubsequenceScore,
  lineDiffRows,
  fileConflictCompareHtml,
  childPathParts,
  inactiveTabItems,
  infoItemId,
  itemInLayout,
  itemLabel,
  itemParam,
  resolveLayoutItem,
  itemIsBackgroundPaneTab,
  layoutFromParam,
  layoutParamValue,
  layoutSlotKeys,
  largestPaneSlotForFileEditor,
  layoutWithFileExplorerDockedLeft,
  layoutWithReplacedItem,
  layoutWithoutItem,
  layoutWithItems,
  setLayoutToSinglePane,
  setLayoutToSplitPanes,
  layoutTabsParamValue,
  layoutTreeKey,
  leafNode,
  yoagentItemId,
  fileExplorerItemId,
  prefsItemId,
  changesItemId,
  menuTabCommand,
  activatePaneTab,
  currentSessionActionTarget,
  setFocusedPanelItem,
  focusedPanelItemForTest() { return focusedPanelItem; },
  setAutoFocusEnabledForTest(value) { autoFocusEnabled = Boolean(value); },
  autoFocusEnabledForTest() { return autoFocusEnabled; },
  selectSession,
  selectPanelOnHover,
  focusTerminalWhenAutoFocus,
  focusPanel,
  focusTerminalFromUserAction,
  toggleFileExplorerShortcut,
  focusedTerminalForTest() { return focusedTerminal; },
  globalShortcutTargetAllowsAppAction,
  commandPaletteItemScore,
  commandPaletteCommandItems,
  commandPaletteItems,
  dedupeFileSearchResults,
  setCommandPaletteStateForTest(mode, query) { commandPaletteMode = mode; commandPaletteQuery = query || ''; },
  commandPaletteMatches,
  openFileQuickOpen,
  testElementForId(id) { return document.getElementById(id); },
  registerTerminalForTest(session, term, socket = {readyState: WebSocket.OPEN}) {
    terminals.set(session, {term, socket, container: document.getElementById('terminal-pane-' + session)});
  },
  tmuxSessionActionCommands,
  tmuxSessionViewCommands,
  tmuxSessionNameError,
  replaceTmuxSessionInClient,
  normalizedSessionOrder,
  normalizeLayoutSlots,
  paneIsPlaceholder,
  panelControlsHtml,
  platformWindowControlClass,
  positionPaneTabPopover,
  pathIsInsideDirectory,
  scrollFileTreeRowIntoView,
  bindPaneTabStrip,
  paneTabShouldPreserve,
  restorePaneTabPopover,
  syncPreservedPaneTab,
  paneTabPopoverItemToRestore,
  clearPaneTabDropPreview,
  showSessionContextMenu,
  bodyChildren() { return document.body.children; },
  defaultLayoutSlots,
  dedentSelectionText,
  dropIntentForEvent,
  dropIntentAllowsSession,
  directoryEntriesSignature,
  editorWrapValue,
  editorViewModeFor,
  editorPreviewModeAvailable,
  setFileEditorViewMode,
  updateFileEditorDiffButton,
  openFileDiffAvailable,
  activeEditorSchemeForTest() { return activeEditorScheme(); },
  configuredEditorSchemeForMode,
  editorSchemeCssVariables,
  editorThemeLabel,
  applyEditorCursorStyle,
  setFileEditorThemeMode,
  cycleEditorThemeMode,
  fileEditorThemeModeForTest() { return fileEditorThemeMode; },
  fileEditorCursorStyleForTest() { return fileEditorCursorStyle; },
  setFileEditorCursorStyleForTest(value) { fileEditorCursorStyle = value; },
  editorVisualHighlightHtml,
  editorVisualLineFragments,
  applyGlobalThemeMode,
  globalThemeLabel,
  globalThemeIsDark,
  nextGlobalThemeMode,
  terminalThemeForGlobalTheme,
  globalThemeModeForTest() { return globalThemeMode; },
  setGlobalThemeModeForTest(value) { globalThemeMode = normalizeGlobalThemeMode(value); },
  terminalThemeModeForTest() { return terminalThemeMode; },
  setTerminalThemeModeForTest(value) { terminalThemeMode = normalizeTerminalThemeMode(value); },
  expandPaneFromLayout,
  terminalThemeSettingForGlobalMode,
  sessionConfirmedGone,
  globalActivityCounts,
  globalActivityStatusLineHtml,
  setAutoApproveStateForTest(session, state) { autoApproveStates.set(session, state); },
  maxTabsPerPane,
  tabsToEvictForCap,
  recordTabActivation,
  setTabLastActivatedForTest(item, ts) { tabLastActivatedAt.set(item, ts); },
  infoBranchRows,
  fileContextMenuState,
  fileEditorItemFor,
  fileEntryChanged,
  fileItemPath,
  filePanelItemsForPath,
  imageOpenUsesSharedViewer,
  imageViewerItemFor,
  keyboardShortcutsHtml,
  openFileEditorItems,
  pullRequestStatusLabel,
  pullRequestApprovalIndicatorHtml,
  pullRequestCompactBadgesHtml,
  pullRequestNumberIndicatorHtml,
  pullRequestReviewInlineHtml,
  sessionStateHtml,
  openFileStatus,
  setOpenFileOwner,
  renderTransportWarning,
  renderFileEditorPanel,
  renderEditorPreviewPane,
  openFileIsMissing,
  setOpenFileStateForTest(path, state) { openFiles.set(path, state); },
  renderTreeChildrenForTest(container, parentPath, entries, depth = 0, entriesByDirPairs = []) {
    renderTreeChildren(container, parentPath, entries, depth, {entriesByDir: new Map(entriesByDirPairs)});
  },
  setFileExplorerRepoInfoForTest(path, repo) {
    fileExplorerRepoInfoCache.set(normalizeDirectoryPath(path), repo);
  },
  repoInfoPopoverHtml,
  fileTreeRepoSyncMeta,
  fileTreeDisplayParts,
  setUploadedFilesCollapsedForTest(value) { uploadedFilesCollapsed = Boolean(value); },
  setChangesFolderCollapsedForTest(keys) { changesFolderCollapsed = new Set((keys || []).map(String)); },
  rawFileUrl,
  rawFileDownloadUrl,
  markOpenFileDiffUnavailable,
  focusPreferencesSearch,
  focusFreshPreferencesSearchSoon,
  markPreferencesInteracted,
  preferencesSearchFreshForTest() { return preferencesSearchFresh; },
  setPreferencesSearchFreshForTest(value) { preferencesSearchFresh = Boolean(value); },
  setClientSettingsPatchForTest(patch) {
    clientSettings = mergeSettingObjects(clientSettings, patch || {});
  },
  preferenceItemMatches,
  preferenceSectionMatches,
  preferencesPanelHtmlForTest(query, collapsed = []) {
    preferencesSearchText = query || '';
    collapsedPreferenceSections = new Set(collapsed);
    preferencesResetConfirmVisible = false;
    return preferencesPanelHtml();
  },
  preferencesResetConfirmHtmlForTest() {
    preferencesResetConfirmVisible = true;
    return preferencesPanelHtml();
  },
  registerFileEditorLayoutItem,
  registerImageViewerLayoutItem,
  requestFileEditorPanelFocus,
  focusFileEditorPanelIfReady,
  minimizePaneFromLayout,
  removePaneFromLayout,
  removeSessionFromLayout,
  runtimeJitteredDelay,
  sessionPopoverHtml,
  setFileQuickOpenCandidatesForTest(root, files) {
    fileQuickOpenRoot = root;
    fileQuickOpenCandidates = files;
    fileQuickOpenLoading = false;
    fileQuickOpenError = '';
    commandPaletteMode = 'files';
  },
  setTabsMenuSearchTextForTest(value) { tabsMenuSearchText = String(value || ''); },
  sessionState,
  slotForNewFileEditorTab,
  slotForNewTmuxSession,
  slotForTabActivation,
  simpleCodeSyntaxHtml,
  smallLayoutSlotCandidate,
  splitPercentForPointer,
  layoutNodeMinWidth,
  layoutVisiblePaneCount,
  fileImagePreviewMinShowDelayMs,
  splitPercentForNewItem,
  setInfoBranchSort,
  handleDropDragOver,
  installFilePathDropTarget,
  showPaneTabDropPreview,
  showDropPreview,
  clearDropPreview,
  shouldPreserveSourceSlotForSplit,
  startSessionDrag,
  startFileTreeDrag,
  stopCustomDragPreview,
  syncInitialLayoutUrl,
  tabMenuDetailText,
  tabSearchFields,
  tabSearchScore,
  TAB_TYPES,
  tabTypeForItem,
  terminalWheelSignedLines,
  terminalWrappedLineLinks,
  transcriptPathRowHtml,
  splitNode,
  splitSessionAtSlot,
  splitSessionAtLayoutBoundary,
  splitSessionAtGutter,
  updateActiveSessionParam,
  paneTabDropIndex,
  paneTabDropPlacement,
  tabMenuItems,
  tmuxPaneTabHtml,
  paneTabs,
  paneStateWithTabs,
  windowStepVisibility,
  markdownSyntaxHtml,
  markdownTextWithSourceAnchors,
  moveSessionToSlot,
  openFileEditorPane,
  onFileTreeRowClick,
  pathRelativeToDirectory,
  replaceHtmlPreservingScroll,
  pruneFileExplorerSelectionForRoot,
  selectFileTreePath,
  selectFileTreeRange,
  updateFileTreeSelectionFromClick,
  currentSlots() { return layoutSlots; },
  fileExplorerSelectionForTest() {
    return {
      paths: Array.from(fileExplorerSelectedPaths).sort(),
      anchor: fileExplorerSelectionAnchor,
      manual: fileExplorerManualSelectionActive,
    };
  },
  setFileExplorerSelectionForTest(paths, anchor = null) {
    fileExplorerSelectedPaths.clear();
    for (const path of paths || []) fileExplorerSelectedPaths.add(path);
    fileExplorerSelectionAnchor = anchor;
    fileExplorerManualSelectionActive = false;
  },
  activeFileForTest() { return activeFile; },
  setFileExplorerExpandedForTest(paths) {
    fileExplorerExpanded.clear();
    for (const path of paths || []) fileExplorerExpanded.add(path);
  },
  setAutoApproveStateForTest(session, payload) {
    autoApproveStates.set(session, payload);
  },
  setTranscriptInfoForTest(session, info) {
    transcriptMeta.sessions = {...(transcriptMeta.sessions || {}), [session]: info};
  },
  setActivitySummaryPayloadForTest(payload) {
    activitySummaryPayload = payload;
  },
  applyServerMetadataPulsesForTest(session, pulses) {
    updateMetadataBadgePulses({sessions: {[session]: {metadata_badge_pulse_remaining_ms: pulses}}});
  },
  setInfoBranchSortForTest(key, dir = 'asc') {
    infoBranchSort = {key, dir};
  },
  setSessionFilesPayloadForTest(payload) {
    sessionFilesPayload = payload;
  },
  setFileExplorerSessionFilesPayloadForTest(payload) {
    fileExplorerSessionFilesPayload = payload;
  },
  setSessionFilesSortModeForTest(mode) {
    sessionFilesSortMode = mode;
  },
  runningAgentCount,
  updateDocumentTitle,
  documentTitleForTest() { return document.title; },
  serialize(slots) {
    return {
      tree: slots[layoutTreeKey],
      panes: Object.fromEntries(layoutSlotKeys(slots).map(slot => [
        slot,
        {
          tabs: paneTabs(slot, slots),
          active: activeItemForSide(slot, slots),
          ...(paneIsPlaceholder(slot, slots) ? {placeholder: true} : {}),
        },
      ])),
    };
  },
  setLayoutSlotsForTest(nextSlots) {
    layoutSlots = normalizeLayoutSlots(nextSlots);
    activeSessions = sessionsFromLayout();
    updateActiveSessionParam();
    return globalThis.__lastUrl;
  },
  syncInitialLayoutUrlForTest() {
    syncInitialLayoutUrl();
    return globalThis.__lastUrl;
  },
  setGridPreviewNodesForTest(nodes) {
    grid.querySelectorAll = () => nodes;
  },
  gridForTest() {
    return grid;
  },
  setLayoutColumnRectsForTest(rects) {
    grid.querySelector = selector => {
      const text = String(selector || '');
      const prefix = '.layout-column[data-slot="';
      const slot = text.startsWith(prefix) && text.endsWith('"]') ? text.slice(prefix.length, -2) : '';
      const rect = slot ? rects[slot] : null;
      if (!rect) return null;
      const node = document.createElement('div');
      node.rect = rect;
      return node;
    };
  },
  defaultLayoutForTest() {
    return globalThis.__layoutTestApi.serialize(defaultLayoutSlots());
  },
  customDragPreviewForTest() {
    return customDragPreview;
  },
  clampToViewport,
  createHoverPopoverForTest(options) {
    return createHoverPopover(options);
  },
  httpsWarningForTest() {
    return document.getElementById('httpsWarning');
  },
  bodyClassListForTest() {
    return document.body.classList;
  },
  documentElementStyleForTest() {
    return document.documentElement.style;
  },
};`, context);
  const api = context.__layoutTestApi;
  // Mirror production boot: preload the en catalog so t() resolves to English in tests (fetch is
  // disabled here, so the live applyLocale() never runs).
  try {
    api.i18nSetCatalogForTest('en', JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')));
  } catch (_) {}
  return api;
}

function tabElement(session, left, width, top = 0) {
  const tab = new TestElement(session);
  tab.dataset.paneTab = session;
  tab.rect = {left, right: left + width, top, bottom: top + 27, width, height: 27};
  return tab;
}

function tabStrip(tabs) {
  const strip = new TestElement('strip');
  strip.children = tabs;
  strip.rect = {left: 100, right: 406, top: 0, bottom: 28, width: 306, height: 28};
  strip.querySelectorAll = selector => {
    assert.equal(selector, '.pane-tab');
    return tabs;
  };
  return strip;
}

function dragEvent(clientX, session = '9') {
  return {
    clientX,
    clientY: 8,
    target: null,
    dataTransfer: {
      dropEffect: '',
      effectAllowed: '',
      dragImage: null,
      types: ['application/x-yolomux-session'],
      getData(type) {
        if (type === 'application/x-yolomux-session') return JSON.stringify({session, sourceSlot: 'right'});
        return '';
      },
      setData(type, value) {
        this[type] = value;
      },
      setDragImage(node, x, y) {
        this.dragImage = {node, x, y};
      },
    },
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this.propagationStopped = true;
    },
    stopImmediatePropagation() {
      this.propagationStopped = true;
    },
  };
}

function fileDragEvent(target, payload = {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, clientX = 20, clientY = 160) {
  const event = dragEvent(clientX);
  event.clientY = clientY;
  event.target = target;
  event.currentTarget = target;
  event.dataTransfer.types = ['application/x-yolomux-file', 'text/plain'];
  event.dataTransfer.getData = type => {
    if (type === 'application/x-yolomux-file') return JSON.stringify(payload);
    if (type === 'text/plain') return (payload.paths || [payload.path]).filter(Boolean).join('\n');
    return '';
  };
  return event;
}

function terminalLine(text, isWrapped = false) {
  return {
    isWrapped,
    translateToString() {
      return text;
    },
  };
}

function nestedSlots(api) {
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.leafNode('left'),
    api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 62.5),
    37.5,
  );
  slots.left = api.paneStateWithTabs(['5', '6'], '6');
  slots.slot1 = api.paneStateWithTabs(['1'], '1');
  slots.slot2 = api.paneStateWithTabs(['3'], '3');
  return api.normalizeLayoutSlots(slots);
}

function parseUrl(url) {
  const query = url.split('?')[1] || '';
  return new URLSearchParams(query);
}

function canonical(value) {
  return JSON.parse(JSON.stringify(value));
}

function makeFileTree(paths) {
  const tree = new TestElement('file-tree');
  tree.setAttribute('role', 'tree');
  tree.classList.add('file-explorer-tree-panel');
  tree.rect = {left: 0, top: 0, right: 260, bottom: 160, width: 260, height: 160};
  tree.clientHeight = 160;
  const rows = paths.map((path, index) => {
    const row = new TestElement(`row-${index}`);
    row.classList.add('file-tree-row');
    row.dataset.path = path;
    row.dataset.kind = 'file';
    row.rect = {left: 0, top: index * 20, right: 260, bottom: index * 20 + 18, width: 260, height: 18};
    tree.appendChild(row);
    return row;
  });
  return {tree, rows};
}

{
  const api = loadYolomux();
  api.renderTransportWarning();
  const warning = api.httpsWarningForTest();
  assert.equal(warning.hidden, false);
  assert.ok(warning.dataset.tip.includes('Highly recommend that you restart with'));
  assert.ok(warning.dataset.tip.includes('--port 7777 --self-signed'));
  assert.equal(warning.dataset.tip.includes('--host 0.0.0.0'), false);

  const secureApi = loadYolomux('', ['1'], 'https:');
  secureApi.renderTransportWarning();
  assert.equal(secureApi.httpsWarningForTest().hidden, true);
}

{
  const api = loadYolomux('', ['1', '2', '3']);
  const layout = api.defaultLayoutForTest();
  assert.deepStrictEqual(canonical(layout.panes), {left: {tabs: ['1', '2', '3'], active: '1'}});
  const url = api.syncInitialLayoutUrlForTest();
  const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
  assert.equal(params.get('sessions'), '1');
  assert.equal(params.get('layout'), 'left');
  assert.equal(params.get('tabs'), 'left:1,2,3');
}

{
  const api = loadYolomux('', []);
  const layout = api.defaultLayoutForTest();
  assert.deepStrictEqual(canonical(layout.panes), {left: {tabs: [], active: null, placeholder: true}});
  const url = api.syncInitialLayoutUrlForTest();
  const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
  assert.equal(params.get('layout'), 'left');
  assert.equal(params.get('tabs'), 'left:__empty_pane__');
}

{
  const api = loadYolomux('?sessions=3,2,1', ['1', '2', '3']);
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
    left: {tabs: ['3', '2', '1'], active: '3'},
  });
}

{
  const api = loadYolomux('?keep=1');
  const slots = nestedSlots(api);
  const url = api.setLayoutSlotsForTest(slots);
  const params = parseUrl(url);
  assert.equal(params.get('sessions'), '6,1,3');
  assert.equal(params.get('layout'), 'row@37.5(left,col@62.5(slot1,slot2))');
  assert.equal(params.get('tabs'), 'left:5,6*;slot1:1;slot2:3');
  assert.equal(params.get('keep'), '1');

  const decoded = api.layoutFromParam(params.get('layout'), params.get('tabs'));
  assert.deepStrictEqual(canonical(api.serialize(decoded)), canonical(api.serialize(slots)));

  const reloaded = loadYolomux(`?${url.split('?')[1] || ''}`);
  assert.deepStrictEqual(canonical(reloaded.serialize(reloaded.currentSlots())), canonical(api.serialize(slots)));
}

{
  const api = loadYolomux();
  const oldPayload = {
    tree: {
      split: 'row',
      children: [
        {slot: 'left'},
        {split: 'column', children: [{slot: 'slot1'}, {slot: 'slot2'}]},
      ],
    },
    slots: {
      left: {tabs: ['5', '6'], active: '6'},
      slot1: {tabs: ['1'], active: '1'},
      slot2: {tabs: ['3'], active: '3'},
    },
  };
  const decoded = api.layoutFromParam(`tree:${JSON.stringify(oldPayload)}`, '');
  const url = api.setLayoutSlotsForTest(decoded);
  const params = parseUrl(url);
  assert.equal(params.get('layout'), 'row@50(left,col@50(slot1,slot2))');
  assert.equal(params.get('tabs'), 'left:5,6*;slot1:1;slot2:3');

  const encodedOldSearch = `?sessions=6%2C1%2C3&layout=${encodeURIComponent(`tree:${JSON.stringify(oldPayload)}`)}`;
  const reloaded = loadYolomux(encodedOldSearch);
  assert.deepStrictEqual(canonical(reloaded.serialize(reloaded.currentSlots())), canonical(api.serialize(decoded)));
}

{
  const api = loadYolomux('?sessions=3&layout=left&tabs=left:3,2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {
      left: {tabs: ['3', '2'], active: '3'},
    },
  });
}

{
  const search = '?sessions=files,file%3A%2Fhome%2Fkeivenc%2FAGENTS.md,5&layout=row@20.7(slot2,row@42(slot3,slot1))&tabs=slot2:files;slot3:file%3A%2Fhome%2Fkeivenc%2FAGENTS.md;slot1:5';
  const api = loadYolomux(search, ['5']);
  const serialized = api.serialize(api.currentSlots());
  assert.deepStrictEqual(canonical(serialized.panes), {
    slot1: {tabs: ['5'], active: '5'},
    slot2: {tabs: ['__files__'], active: '__files__'},
    slot3: {tabs: ['file:/home/keivenc/AGENTS.md'], active: 'file:/home/keivenc/AGENTS.md'},
  });
  const url = api.syncInitialLayoutUrlForTest();
  const params = parseUrl(url);
  assert.equal(params.get('layout'), 'row@20.7(slot2,row@42(slot3,slot1))');
  assert.equal(params.get('tabs'), 'slot2:files;slot3:file:/home/keivenc/AGENTS.md;slot1:5');
}

{
  const search = '?sessions=files,6&layout=row@22(slot2,slot3)&tabs=slot2:files;slot3:prefs,6*,file%3A%2Fhome%2Fkeivenc%2FAGENTS.md,ant,file%3A%2Fhome%2Fkeivenc%2Fyolomux.dev%2FTODO.md,file%3A%2Fhome%2Fkeivenc%2Fcomponents_metrics_README.md,file%3A%2Fhome%2Fkeivenc%2Fyolomux.dev%2F20260528-022.png';
  const api = loadYolomux(search, ['6', 'ant']);
  const agentsItem = 'file:/home/keivenc/AGENTS.md';
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'slot2'}, {slot: 'slot3'}]},
    panes: {
      slot2: {tabs: ['__files__'], active: '__files__'},
      slot3: {
        tabs: [
          '__prefs__',
          '6',
          agentsItem,
          'ant',
          'file:/home/keivenc/yolomux.dev/TODO.md',
          'file:/home/keivenc/components_metrics_README.md',
          'file:/home/keivenc/yolomux.dev/20260528-022.png',
        ],
        active: '6',
      },
    },
  });
  api.activatePaneTab('slot3', agentsItem);
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'slot2'}, {slot: 'slot3'}]},
    panes: {
      slot2: {tabs: ['__files__'], active: '__files__'},
      slot3: {
        tabs: [
          '__prefs__',
          '6',
          agentsItem,
          'ant',
          'file:/home/keivenc/yolomux.dev/TODO.md',
          'file:/home/keivenc/components_metrics_README.md',
          'file:/home/keivenc/yolomux.dev/20260528-022.png',
        ],
        active: agentsItem,
      },
    },
  });
  const activeParams = parseUrl(api.syncInitialLayoutUrlForTest());
  assert.equal(activeParams.get('sessions'), 'files,file:/home/keivenc/AGENTS.md');
  assert.equal(activeParams.get('tabs').includes('slot2:files;slot3:prefs,6,file:/home/keivenc/AGENTS.md*'), true);

  const terminalToolbarBeforeFinderFocus = api.panelControlsHtml('6');
  api.setFocusedPanelItem('__files__');
  api.activatePaneTab('slot2', '__files__');
  assert.equal(api.panelControlsHtml('6'), terminalToolbarBeforeFinderFocus);
  assert.ok(terminalToolbarBeforeFinderFocus.includes('data-tab-name="terminal"'));
  assert.equal((terminalToolbarBeforeFinderFocus.match(/pane-actions-dots/g) || []).length, 1);
  assert.equal(terminalToolbarBeforeFinderFocus.includes('data-tab-name="summary"'), false);
}

{
  const api = loadYolomux('', ['1', '2']);
  const item = 'file:/home/keivenc/review.json';
  const paneTab = api.fileEditorPaneTabHtml(item);
  assert.ok(paneTab.includes('review.json'));
  assert.equal(paneTab.includes('agent-icon file'), false);
  api.setOpenFileOwner('/home/keivenc/review.json', item, {ownerSession: '1'});
  assert.ok(api.fileEditorPaneTabHtml(item).includes('file-tab-owner'), 'file tabs show owning session when known');
  assert.ok(api.fileEditorPaneTabHtml(item).includes('>1</span>'), 'single owning session is shown in file tab');
  api.setOpenFileOwner('/home/keivenc/review.json', item, {ownerSession: '2'});
  assert.ok(api.fileEditorPaneTabHtml(item).includes('>multi</span>'), 'multi-session file tabs distinguish duplicate names');
  assert.ok(api.fileEditorPaneTabHtml('file-preview:/home/keivenc/review.json').includes('file-tab-kind'), 'preview tabs are visually distinguishable from same-path editor tabs');

  assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-heading-1'));
  assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-bold'));
  const anchoredMarkdown = api.markdownTextWithSourceAnchors('| A | B |\n|---|---|\n| 1 | 2 |\n\n---');
  assert.equal(anchoredMarkdown.includes('markdown-source-anchor'), false, 'Markdown source is not mutated before marked parses GFM tables and rules');
  assert.ok(anchoredMarkdown.includes('|---|---|'), 'GFM table delimiter rows stay intact before parsing');
  assert.ok(anchoredMarkdown.includes('\n---'), 'thematic breaks stay intact before parsing');
  const editorCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(editorCss.includes('.markdown-body th { background: var(--panel2); }'), 'Markdown table headers get a readable preview background');
  assert.ok(editorCss.includes('.markdown-body hr { border: 0; border-top: 1px solid var(--line); margin: 12px 0; }'), 'Markdown thematic breaks render as preview rules');
  assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-comment'));
  assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-variable'));
  assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-attr'));
  assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-constant'));

  assert.deepStrictEqual(Array.from(api.editorVisualLineFragments('abcdefghijkl', 5, true)), ['abcde', 'fghij', 'kl']);
  assert.deepStrictEqual(Array.from(api.editorVisualLineFragments('abcdefghijkl', 5, false)), ['abcdefghijkl']);
  const gutterHtml = api.editorVisualHighlightHtml('bash', 'echo 1\nabcdefghijkl', {wrap: true, lineNumbers: true, columnCount: 5});
  assert.ok(gutterHtml.includes('editor-line-number">1</span'));
  assert.ok(gutterHtml.includes('editor-line-number">2</span'));
  assert.ok(gutterHtml.includes('editor-soft-wrap-marker">↪</span'));
  assert.ok(gutterHtml.includes('code-number'));
  const semanticRanges = [];
  const fakeApi = {
    Decoration: {
      mark(options) {
        return {
          range(from, to) {
            return {from, to, style: options.attributes.style};
          },
        };
      },
      set(ranges) {
        semanticRanges.push(...ranges);
        return ranges;
      },
    },
    ViewPlugin: {
      fromClass(Plugin) {
        const text = '<p><strong>bold</strong> <em>em</em></p>';
        const view = {
          visibleRanges: [{from: 0, to: text.length}],
          state: {
            doc: {
              length: text.length,
              sliceString(from, to) {
                return text.slice(from, to);
              },
            },
          },
        };
        return new Plugin(view).decorations;
      },
    },
  };
  api.codeMirrorHtmlSemanticEmphasisExtension(fakeApi, '/home/test/index.html');
  assert.deepStrictEqual(semanticRanges.map(range => [range.from, range.to]), [[11, 15], [29, 31]]);
  assert.ok(semanticRanges[0].style.includes('font-weight:700'));
  assert.ok(semanticRanges[1].style.includes('font-style:italic'));

  const brokenLanguageApi = {
    javascript() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
    markdown() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
    LanguageDescription: {
      of() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
    },
    StreamLanguage: {
      define() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
    },
  };
  assert.deepStrictEqual(canonical(api.codeMirrorLanguageExtension(brokenLanguageApi, '/home/test/app.js')), [], 'broken JS language support falls back to editable plain text');
  assert.deepStrictEqual(canonical(api.codeMirrorLanguageExtension(brokenLanguageApi, '/home/test/README.md')), [], 'broken Markdown language support falls back to editable plain text');
  assert.deepStrictEqual(canonical(api.codeMirrorMarkdownCodeLanguages(brokenLanguageApi)), [], 'broken fenced-code language descriptions are skipped');
  assert.deepStrictEqual(canonical(api.codeMirrorHighlightExtension({})), [], 'missing highlight support falls back without crashing');
  assert.equal(api.codeMirrorApiIsUsable({Compartment: class {}, EditorState: {create() {}, readOnly: {of() {}}}, EditorView: {theme() {}, editable: {of() {}}}, keymap: {of() {}}, drawSelection() {}, highlightActiveLine() {}, search() {}, openSearchPanel() {}}), true, 'CodeMirror API validation accepts critical editor/search exports');
  assert.equal(api.codeMirrorApiIsUsable({EditorState: {create() {}}, EditorView: {theme() {}}}), false, 'CodeMirror API validation rejects partial bundles');
  const parserCrashApi = {
    EditorState: {
      create(config) {
        if (JSON.stringify(config.extensions).includes('bad-language')) {
          throw new TypeError("Cannot read properties of undefined (reading 'parser')");
        }
        return {
          doc: {toString: () => String(config.doc || ''), length: String(config.doc || '').length},
          selection: {main: {head: 0}, ranges: []},
          extensions: config.extensions,
        };
      },
      readOnly: {of(value) { return ['readOnly', value]; }},
    },
    EditorView: {
      theme() { return 'theme'; },
      lineWrapping: 'wrap',
      editable: {of(value) { return ['editable', value]; }},
      updateListener: {of(listener) { return ['listener', listener]; }},
    },
    Compartment: class {
      of(extension) { return ['compartment', extension]; }
      reconfigure(extension) { return ['reconfigure', extension]; }
    },
    keymap: {of(entries) { return ['keymap', entries]; }},
    history() { return 'history'; },
    drawSelection() { return 'drawSelection'; },
    dropCursor() { return 'dropCursor'; },
    rectangularSelection() { return 'rectangularSelection'; },
    crosshairCursor() { return 'crosshairCursor'; },
    indentOnInput() { return 'indentOnInput'; },
    bracketMatching() { return 'bracketMatching'; },
    foldGutter() { return 'foldGutter'; },
    highlightActiveLine() { return 'highlightActiveLine'; },
    lineNumbers() { return 'lineNumbers'; },
    highlightActiveLineGutter() { return 'highlightActiveLineGutter'; },
    search() { return 'search'; },
    highlightSelectionMatches() { return 'highlightSelectionMatches'; },
    indentWithTab: 'indentWithTab',
    defaultKeymap: [],
    historyKeymap: [],
    searchKeymap: [],
    openSearchPanel() { return true; },
    markdown() { return 'bad-language'; },
  };
  const parserCrashPanel = {};
  const fallbackState = api.createEditableCodeMirrorState(parserCrashApi, parserCrashPanel, '/home/test/DOIT.2.md', '```bash\\necho hi\\n```');
  assert.equal(fallbackState.plain, true, 'CodeMirror parser crashes retry as plain editable state');
  assert.equal(fallbackState.state.doc.toString(), '```bash\\necho hi\\n```');
  assert.equal(JSON.stringify(fallbackState.state.extensions).includes('bad-language'), false, 'plain retry removes the failing language extension');
  assert.ok(api.codeMirrorPlainEditableExtensions(parserCrashApi, {}, '/home/test/DOIT.2.md').length > 0, 'plain editable fallback keeps editor controls available');

  const compareRows = canonical(api.lineDiffRows('one\ntwo\nfour', 'one\nthree\nfour'));
  assert.deepStrictEqual(compareRows.map(row => [row.leftKind, row.rightKind]), [
    ['same', 'same'],
    ['added', 'blank'],
    ['blank', 'removed'],
    ['same', 'same'],
  ], 'conflict compare dialog uses a real line diff');
  const compareHtml = api.fileConflictCompareHtml('one\ntwo', 'one\nthree');
  assert.ok(compareHtml.includes('file-compare-line added'), 'editor-only lines are marked green');
  assert.ok(compareHtml.includes('file-compare-line removed'), 'disk-only lines are marked red');
  assert.ok(compareHtml.includes('data-file-compare-scroll'), 'compare columns expose synchronized scroll targets');
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(
    source.includes('if (panel._cmGeneration !== generation) return null;'),
    'stale CodeMirror renders no-op instead of reporting a load failure',
  );
  assert.ok(
    source.includes('if (loaded === false) renderFileEditorRawPane(rawPane, path, state.content);'),
    'raw editor fallback is only rendered for real CodeMirror failures',
  );
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.editorPreviewModeAvailable('/home/test/README.md'), true);
  assert.equal(api.editorPreviewModeAvailable('/home/test/index.html'), true);
  assert.equal(api.editorPreviewModeAvailable('/home/test/app.py'), false);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('function sanitizeMarkdownPreviewHtml'), 'Markdown previews pass through a sanitizer');
  assert.ok(source.includes('MARKDOWN_PREVIEW_BLOCKED_TAGS'), 'Markdown sanitizer blocks executable/embedded HTML tags');
  assert.equal(source.includes('container.innerHTML = window.marked.parse'), false, 'Markdown previews are not inserted with unsanitized marked HTML');
  assert.ok(source.includes('container.replaceChildren(sanitizeMarkdownPreviewHtml(html));'), 'Markdown previews replace DOM with sanitized nodes');
  const htmlPreview = new TestElement('html-preview');
  htmlPreview.scrollTop = 37;
  htmlPreview.scrollLeft = 6;
  api.renderEditorPreviewPane(htmlPreview, '/home/test/index.html', '<style>h1{color:red}</style><h1>Hello</h1><script>window.bad = true</script>');
  assert.equal(htmlPreview.classList.contains('html-preview-body'), true);
  assert.equal(htmlPreview.classList.contains('code-preview-body'), false);
  assert.equal(htmlPreview.children.length, 2);
  assert.equal(htmlPreview.children[0].className, 'file-editor-html-js-notice');
  assert.equal(htmlPreview.children[0].children[1].href, '/api/fs/html-preview?path=%2Fhome%2Ftest%2Findex.html');
  assert.equal(htmlPreview.children[0].children[1].dataset.htmlPreviewAuth, '1');
  assert.equal((htmlPreview.children[0].children[1].listeners.get('click') || []).length, 1);
  assert.equal(htmlPreview.children[1].className, 'file-editor-html-preview');
  assert.equal(htmlPreview.children[1].attributes.sandbox, '', 'HTML preview iframe is sandboxed with scripts disabled');
  assert.ok(htmlPreview.children[1].srcdoc.includes('<h1>Hello</h1>'), 'HTML preview renders markup through srcdoc');
  assert.equal(htmlPreview.scrollTop, 37, 'HTML preview refresh preserves vertical scroll');
  assert.equal(htmlPreview.scrollLeft, 6, 'HTML preview refresh preserves horizontal scroll');
  api.setFileEditorViewMode('/home/test/app.py', 'split');
  assert.equal(api.editorViewModeFor('/home/test/app.py'), 'edit');
  api.setFileEditorViewMode('/home/test/README.md', 'split');
  assert.equal(api.editorViewModeFor('/home/test/README.md'), 'split');
  const changedPath = '/repo/app/README.md';
  const changedItem = api.fileEditorItemFor(changedPath);
  api.setOpenFileOwner(changedPath, changedItem, {ownerSession: '1'});
  api.setFileEditorViewMode(changedPath, 'edit', changedItem);
  api.setOpenFileOwner(changedPath, changedItem, {ownerSession: '1'});
  api.setFileEditorViewMode(changedPath, 'diff', changedItem);
  assert.equal(api.filePanelItemsForPath(changedPath).join(','), changedItem, 'Finder and Modified-files reuse one editor item for the same path');
  assert.equal(api.editorViewModeFor(changedPath, changedItem), 'diff', 'opening from Modified files flips the shared editor item to diff mode');
  assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, diff: ''}), false, 'unchanged files are not diffable');
  assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, diff: 'diff --git a/a b/a'}), true, 'changed repo files are diffable');
  assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, untracked: true, diff: 'diff --git a/a b/a'}), false, 'untracked/all-added files do not auto-open as all-green diffs');
  assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, diffOriginal: '', diff: 'diff --git a/a b/a\n--- /dev/null\n+++ b/a\n@@\n+one'}), false, 'new-file diffs do not count as normal editable diffs');
  const unavailableDiffState = {kind: 'text', diffLoaded: false, diffLoading: true, diff: 'stale'};
  api.markOpenFileDiffUnavailable(unavailableDiffState, 'not tracked');
  assert.equal(unavailableDiffState.diffLoaded, true, 'diff-unavailable is a completed attempt');
  assert.equal(unavailableDiffState.diffUnavailable, true);
  assert.equal(unavailableDiffState.diff, '');
  assert.equal(api.openFileDiffAvailable(unavailableDiffState), false);
  const diffButton = new TestElement('diff-button');
  api.setFileEditorViewMode(changedPath, 'edit', changedItem);
  api.updateFileEditorDiffButton(diffButton, changedPath, {kind: 'text', diffLoaded: true, diff: ''}, changedItem);
  assert.equal(diffButton.hidden, true, 'unchanged files do not show a Diff button');
  api.updateFileEditorDiffButton(diffButton, changedPath, {kind: 'text', diffLoaded: true, diff: 'diff --git a/a b/a'}, changedItem);
  assert.equal(diffButton.hidden, false, 'changed repo files show a Diff button');
  assert.equal(diffButton.disabled, false, 'changed repo Diff button is clickable');
  api.setFileEditorViewMode(changedPath, 'diff', changedItem);
  api.updateFileEditorDiffButton(diffButton, changedPath, {kind: 'text', diffLoading: true}, changedItem);
  assert.equal(diffButton.hidden, false, 'active diff view keeps a loading Diff button while refs load');
  assert.equal(diffButton.disabled, false, 'active diff view keeps Exit diff clickable while refs load');
  const codePath = '/repo/app/app.py';
  const codeItem = api.fileEditorItemFor(codePath);
  api.setFileEditorViewMode(codePath, 'diff', codeItem);
  api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', diffLoaded: true, diff: ''}, codeItem);
  assert.equal(diffButton.hidden, false, 'code files stuck in diff still show an Exit diff button');
  assert.equal(diffButton.disabled, false, 'Exit diff stays clickable even when no code-file diff is available');
  // #25: a .py (non-md/html) in NORMAL mode with no diff loaded yet still offers a clickable Diff
  // toggle, which lazily loads the diff on click (it only hides once a load confirms there is none).
  api.setFileEditorViewMode(codePath, 'edit', codeItem);
  api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text'}, codeItem);
  assert.equal(diffButton.hidden, false, '#25: a code file with no diff loaded yet still offers a Diff toggle');
  assert.equal(diffButton.disabled, false, '#25: the Diff toggle is clickable so it can lazily load the diff');
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.runtimeJitteredDelay(3000, 0), 3030);
  assert.equal(api.runtimeJitteredDelay(3000, 1), 3300);
  assert.ok(api.runtimeJitteredDelay(1250, 0.5) > 1250);
  assert.ok(api.runtimeJitteredDelay(1250, 0.5) < 1376);
}

{
  const api = loadYolomux('', ['1']);
  assert.deepStrictEqual(canonical(api.codeMirrorSearchMatches('foo bar foo', 'foo')), [
    {from: 0, to: 3},
    {from: 8, to: 11},
  ]);
  assert.equal(api.codeMirrorSearchMatchSummary('foo bar foo', 'foo', {from: 8, to: 11, head: 11}).text, '2/2');
  assert.equal(api.codeMirrorSearchMatchSummary('Foo foo', 'foo', {head: 0}, {caseSensitive: true}).text, '1/1');
  assert.equal(api.codeMirrorSearchMatchSummary('food foo', 'foo', {head: 0}, {wholeWord: true}).text, '1/1');
  assert.equal(api.codeMirrorSearchMatchSummary('abc', '[', {head: 0}, {regexp: true}).text, '0/0');
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function renderFileEditorPanel(');
  const end = source.indexOf('function loadFileEditorState(', start);
  assert.ok(start > 0 && end > start, 'could not locate renderFileEditorPanel body');
  assert.equal(source.slice(start, end).includes('.focus('), false, 'renderFileEditorPanel must not steal focus during refresh renders');
  assert.ok(source.includes('captureFileEditorPanelViewStateForItem(previous)'), 'switching pane tabs captures the outgoing CodeMirror viewport');
  assert.ok(source.includes('const scrollTop = scrollDOM?.scrollTop || 0;'), 'external CodeMirror reload preserves scrollTop');
  assert.ok(source.includes('view.requestMeasure({write: restoreScroll});'), 'external CodeMirror reload restores scroll after the document update');
  assert.ok(source.includes('view.requestMeasure'), 'CodeMirror viewport restore waits for a measured layout frame');
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const menuStart = source.indexOf('function bindAppMenuHover(');
  const menuEnd = source.indexOf('function openAppMenu(', menuStart);
  assert.ok(menuStart > 0 && menuEnd > menuStart, 'could not locate bindAppMenuHover body');
  const menuBody = source.slice(menuStart, menuEnd);
  assert.ok(menuBody.includes('canOpen: () => autoFocusEnabled || appMenuIsOpen()'), 'menu hover-open is cold-disabled by auto-focus but still switches while a menu is manually open');
  assert.ok(menuBody.includes('openAppMenuId === menuId'), 'old menu hover-close timers must not close a newer open menu');
  const activePreferenceStart = source.indexOf('function activePreferenceControl(');
  const activePreferenceEnd = source.indexOf('function clampPreferenceNumber(', activePreferenceStart);
  assert.ok(activePreferenceStart > 0 && activePreferenceEnd > activePreferenceStart, 'could not locate activePreferenceControl body');
  const activePreferenceBody = source.slice(activePreferenceStart, activePreferenceEnd);
  assert.ok(activePreferenceBody.includes('[data-preference-section-toggle]'), 'preference section buttons are preserved through search focusout');
  assert.ok(activePreferenceBody.includes('[data-preferences-reset-all]'), 'global reset button is preserved through search focusout');
  assert.ok(activePreferenceBody.includes('[data-preferences-reset-confirm]'), 'global reset confirmation button is preserved through focusout');
  assert.ok(source.includes('let sessionFilesRequestId = 0;'), 'Changes modified-file fetches have their own stale-response request id');
  assert.ok(source.includes('requestId === sessionFilesRequestId'), 'Changes modified-file fetches reject stale responses');
  assert.ok(source.includes('function activeChangesControl'), 'Changes panel renders can detect active controls');
  assert.ok(source.includes('!activeChangesControl(panel)'), 'background Changes renders preserve active selects and ref controls');
  assert.ok(source.includes('function sessionFilesRenderOptions'), 'modified-file fetch rendering distinguishes silent polls from explicit user refreshes');
  assert.ok(source.includes('const loadingPromise = (async () => {'), 'editor file loading keeps a promise handle for guarded cleanup');
  assert.ok(source.includes('if (current?.loadingPromise === loadingPromise) delete current.loadingPromise;'), 'editor file loading clears stale loading promises after failure or success');
  assert.ok(source.includes('let activitySummaryRequestId = 0;'), 'activity summary refreshes carry a stale-response request id');
  assert.ok(source.includes('if (activitySummaryRefreshing && options.force !== true) return;'), 'activity summary polling skips overlapping non-forced refreshes');
  assert.ok(source.includes('const notificationLastSentLimit = 512;'), 'notification signature cache has a bounded size');
  assert.ok(source.includes('setLimitedMapEntry(notificationLastSent, key, now, notificationLastSentLimit);'), 'notification signatures use the shared bounded-map helper');
  assert.ok(source.includes('existing?.delay === normalizedDelay'), 'runtime intervals keep their timer phase when refresh delays are unchanged');
  assert.ok(source.includes('item.fitFinalTimer'), 'terminal fit scheduling debounces resize bursts per terminal');
  assert.equal(source.includes('esm.sh'), false, 'CodeMirror loading never falls back to a third-party CDN');
  assert.ok(source.includes('CodeMirror local bundle is unavailable or incomplete'), 'CodeMirror loading reports local bundle failures clearly');
  assert.ok(source.includes('maybeHandleServerVersionChange(transcriptMeta.server_version)'), 'the metadata poll checks the live server version');
  assert.ok(/maybeHandleServerVersionChange[\s\S]*serverVersion === bootstrap\.version[\s\S]*boolSetting\('general\.reload_on_update'/.test(source), 'server-version reload is gated on the boot version and the reload_on_update preference');
  assert.ok(/maybeHandleServerVersionChange[\s\S]*boolSetting\('general\.reload_on_update_auto'[\s\S]*reloadIsSafe\(\)/.test(source), 'auto-reload only fires when enabled and reloadIsSafe()');
  assert.ok(/function reloadIsSafe\(\)[\s\S]*file\?\.dirty[\s\S]*isContentEditable/.test(source), 'reloadIsSafe refuses when an editor buffer is dirty or the user is typing');
  assert.ok(source.includes('id="summary-${session}" class="summary-preview markdown-body"'), 'the AI Transcript panel is a markdown-body container, not a raw <pre>');
  assert.ok(source.includes("AI Transcript for session '"), 'the AI Transcript panel head names the session');
  assert.ok(/function startSummaryStream[\s\S]*renderMarkdownPreviewInto\(node, raw\)/.test(source), 'the AI Transcript stream renders accumulated text through the markdown pipeline');
  assert.ok(/function createTopbarSearch[\s\S]*openFileQuickOpen\(\)/.test(source), 'the topbar universal search opens the unified quick-open/command palette (no forked logic)');
  assert.ok(/renderSessionButtons[\s\S]*appendChild\(createTopbarSearch\(\)\)/.test(source), 'the topbar search is mounted in the menubar middle area');
  assert.ok(/refreshFileIndexStatus[\s\S]{0,400}\/api\/fs\/index-status\?root=/.test(source), '#30/#31: the client warms the backend index and tracks build status via /api/fs/index-status');
  assert.ok(source.includes("=== 'building' ? 'indexing…' : 'indexed'"), '#31: the indexed badge shows "indexing…" while building, then a steady "indexed"');
  assert.ok(/payload && payload\.ready \? 'ready' : 'building'/.test(source), '#31: a ready index (incl. during a background TTL rebuild that keeps ready=true) stays "indexed", not "indexing"');
  assert.ok(/fileExplorerIndexStatus\.set\(normalized, 'building'\);\s*refreshFileIndexStatus\(normalized\)/.test(source), '#30: indexing a directory eagerly warms its backend index (no cold first-query live walk)');
  const loadAutoStatusesFn = source.slice(source.indexOf('async function loadAutoStatuses'), source.indexOf('async function loadAutoStatuses') + 1700);
  assert.ok(loadAutoStatusesFn.includes('updateDocumentTitle();') && loadAutoStatusesFn.includes('renderAutoApproveButtons();'), '#46: the auto-status poll re-syncs the YO markers (renderAutoApproveButtons) alongside the tab title, so a done pane stops spinning on the same poll');
  assert.ok(source.includes("{path: 'file_explorer.indexed_dirs'"), '#32: Preferences exposes an editable indexed-directories list');
  assert.ok(/function reconcileIndexedDirsFromSetting[\s\S]*setFileExplorerDirectoryIndexed\(dir, true\)[\s\S]*setFileExplorerDirectoryIndexed\(dir, false\)/.test(source), '#32: editing the indexed-dirs setting adds/removes indexed dirs (bi-directional sync)');
  assert.ok(source.includes('/api/fs/unindex?root='), '#32: removing an indexed dir wires to the backend unindex');
  assert.ok(source.slice(source.indexOf('function focusPreferencesSearch('), source.indexOf('function focusPreferencesSearchSoon(')).includes('panel && panel.isConnected !== false'), 'Preferences focus falls back to the rendered panel when called without a panel');
  const focusedPanelStart = source.indexOf('function setFocusedPanelItem(');
  const focusedPanelEnd = source.indexOf('function clearPendingFileEditorFocusExcept(', focusedPanelStart);
  assert.ok(focusedPanelStart > 0 && focusedPanelEnd > focusedPanelStart, 'could not locate setFocusedPanelItem body');
  const focusedPanelBody = source.slice(focusedPanelStart, focusedPanelEnd);
  assert.ok(focusedPanelBody.includes('options.focusPreferencesSearch !== false'), 'fresh Preferences search focus is part of shared pane focus');
  assert.ok(focusedPanelBody.includes('focusFreshPreferencesSearchSoon()'), 'shared pane focus targets fresh Preferences search');
  assert.ok(focusedPanelBody.includes('updateTypingIndicator(activeSession)'), 'shared pane focus refreshes every pane focus ring immediately');
  const panelShellStart = source.indexOf('function bindPanelShell(');
  const panelShellEnd = source.indexOf('function createPanel(', panelShellStart);
  assert.ok(panelShellStart > 0 && panelShellEnd > panelShellStart, 'could not locate bindPanelShell body');
  const panelShellBody = source.slice(panelShellStart, panelShellEnd);
  assert.ok(panelShellBody.includes('preferenceFocusTargetIsInteractive(event.target)'), 'clicking an existing Preferences control does not steal focus back to search');
  const resetAllStart = source.indexOf('function resetAllPreferences(');
  const resetAllEnd = source.indexOf('function createFileExplorerPanel(', resetAllStart);
  assert.ok(resetAllStart > 0 && resetAllEnd > resetAllStart, 'could not locate resetAllPreferences body');
  assert.equal(source.slice(resetAllStart, resetAllEnd).includes('focusSearch: true'), false, 'reset all is a settings interaction and does not re-arm fresh search focus');
  const refreshStart = source.indexOf('async function refreshOpenFilesIfChanged(');
  const refreshEnd = source.indexOf('function watchedFileExplorerDirectories(', refreshStart);
  assert.ok(refreshStart > 0 && refreshEnd > refreshStart, 'could not locate refreshOpenFilesIfChanged body');
  const refreshBody = source.slice(refreshStart, refreshEnd);
  assert.ok(refreshBody.includes('const fetched = await fetchFileEntryStatus(path);'), 'open-file refresh uses a structured file lookup');
  assert.ok(refreshBody.includes('if (fetched.missing)'), 'open-file refresh only marks missing after an explicit missing result');
  assert.ok(refreshBody.includes('markOpenFileExternalError'), 'open-file refresh keeps network/list errors separate from deletion');
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function updatePaneTabStrip(');
  const end = source.indexOf('function reconcilePaneTabChildren(', start);
  assert.ok(start > 0 && end > start, 'could not locate updatePaneTabStrip body');
  const body = source.slice(start, end);
  assert.equal(body.includes('replaceChildren(...'), false, 'routine pane-tab refresh must reconcile instead of rebuilding hovered tabs');
  assert.ok(source.includes('function reconcilePaneTabChildren('), 'pane-tab reconcile helper exists');
  assert.ok(source.includes('function paneTabShouldPreserve('), 'open pane-tab popovers keep their existing node');
}

{
  const api = loadYolomux('', ['1']);
  const tab = new TestElement('tab');
  const popover = new TestElement('popover');
  tab.className = 'pane-tab popover-open';
  tab.classList.add('pane-tab', 'popover-open');
  tab.dataset.paneTab = '1';
  tab.dataset.popoverHoverState = 'closing';
  tab.querySelector = selector => selector.includes('session-popover') ? popover : null;
  assert.equal(api.paneTabShouldPreserve(tab), true);

  const fresh = new TestElement('fresh');
  fresh.className = 'pane-tab active';
  fresh.role = 'button';
  fresh.tabIndex = 0;
  fresh.draggable = true;
  fresh.dataset.paneTab = '1';
  fresh.getAttribute = name => name === 'aria-label' ? 'Session 1' : undefined;
  api.syncPreservedPaneTab(tab, fresh);
  assert.ok(tab.className.includes('popover-open'));
  assert.equal(tab.dataset.popoverHoverState, 'closing');
  assert.equal(tab.getAttribute('aria-label'), 'Session 1');
}

{
  // GLOBAL activity status line: cross-session running / need-you / idle rollup in the top bar.
  const api = loadYolomux('', ['1', '2', '3', '4']);
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  api.setAutoApproveStateForTest('1', {screen: {key: 'working'}});
  api.setAutoApproveStateForTest('2', {screen: {key: 'needs-input'}});
  // '3' and '4' fall through to idle.
  const counts = api.globalActivityCounts();
  assert.equal(counts.running, 1, 'one working session counts as running');
  assert.equal(counts.attention, 1, 'one needs-input session counts as needing the user');
  assert.equal(counts.idle, 2, 'the remaining sessions are idle');
  assert.equal(counts.total, 4, 'all tmux sessions are counted');
  const html = api.globalActivityStatusLineHtml();
  assert.ok(/1 running/.test(html) && /topbar-activity-run/.test(html), 'status line shows running count');
  assert.ok(/1 need you/.test(html) && /topbar-activity-attn/.test(html), 'status line flags sessions needing the user');
  assert.ok(/2 idle/.test(html), 'status line shows the idle count');
  assert.ok(/\.topbar-activity\s*\{/.test(css), 'the top-bar activity line is styled');
  assert.ok(/\.topbar-activity\.has-attention/.test(css), 'the activity line highlights when a session needs the user');
}

{
  // Event-driven session-kill: a terminal WS close roster-confirms gone vs transient disconnect.
  const api = loadYolomux('', ['1', '2', '3']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(api.sessionConfirmedGone('2', ['1', '3']), true, 'a tmux session absent from the roster is confirmed gone');
  assert.equal(api.sessionConfirmedGone('2', ['1', '2', '3']), false, 'a session still in the roster is a transient disconnect, not gone');
  assert.equal(api.sessionConfirmedGone('2', null), false, 'a failed/empty roster fetch never declares a session gone (reconnect instead)');
  assert.equal(api.sessionConfirmedGone(api.fileEditorItemFor('/x/y.txt'), []), false, 'non-tmux items are never roster-pruned');
  assert.ok(source.includes('confirmSessionGoneOrReconnect(session, item);'), 'terminal WS close roster-confirms before reconnecting');
  assert.ok(/sessionConfirmedGone\(session, order\)\)\s*\{\s*pruneDeadSession\(session\);/.test(source), 'a confirmed-gone session is pruned immediately');
  assert.ok(/scheduleTerminalReconnect\(session, item\);\s*\}\s*$/m.test(source) || source.includes('scheduleTerminalReconnect(session, item);'), 'a transient disconnect still reconnects');
}

{
  // Git-aware Finder: repo-dir inline annotation (branch + ahead/behind/dirty) + hover popover.
  const api = loadYolomux('', ['1']);
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  api.setFileExplorerRepoInfoForTest('/repo/app', {root: '/repo/app', name: 'app', branch: 'feature/x', upstream: 'origin/feature/x', ahead: 2, behind: 1, dirty_count: 3});
  const sync = api.fileTreeRepoSyncMeta('/repo/app').map(part => part.text);
  assert.deepStrictEqual([...sync], ['↑2', '↓1', '●3'], 'repo sync meta shows ahead/behind/dirty markers');
  const parts = api.fileTreeDisplayParts('/repo/app', {kind: 'dir', is_repo: true, name: 'app'});
  assert.ok(parts.html.includes('file-tree-repo-branch') && parts.html.includes('feature/x'), 'repo dir annotation shows the branch');
  assert.ok(parts.html.includes('file-tree-repo-ahead') && parts.html.includes('↑2'), 'repo dir annotation shows ahead count inline');
  assert.ok(parts.html.includes('file-tree-repo-dirty') && parts.html.includes('●3'), 'repo dir annotation shows dirty count inline');
  // Rich hover popover (replaces the native title tooltip) carries branch / upstream / stat / path.
  const pop = api.repoInfoPopoverHtml({root: '/repo/app', name: 'app', branch: 'feature/x', upstream: 'origin/feature/x', ahead: 2, behind: 1, dirty_count: 3});
  assert.ok(pop.includes('feature/x') && pop.includes('2 ahead') && pop.includes('1 behind') && pop.includes('3 dirty'), 'repo popover shows branch + ahead/behind/dirty');
  assert.ok(pop.includes('/repo/app'), 'repo popover shows the repo root path');
  assert.equal(api.repoInfoPopoverHtml({}), '', 'no popover html without a repo root');
  assert.ok(/\.file-tree-repo-popover\s*\{[^}]*position:\s*fixed/.test(css), 'the repo hover popover is a styled floating element');
  assert.ok(/\.file-tree-repo-ahead/.test(css) && /\.file-tree-repo-dirty/.test(css), 'inline ahead/dirty markers are styled');
}

{
  // Max tabs per pane + LRU eviction (Preference).
  const api = loadYolomux('', ['1', '2', '3', '4', '5']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 3}});
  assert.equal(api.maxTabsPerPane(), 3, 'tab cap reads appearance.max_tabs_per_pane');
  api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 999}});
  assert.equal(api.maxTabsPerPane(), 30, 'tab cap is clamped to 30');
  api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 1}});
  assert.equal(api.maxTabsPerPane(), 2, 'tab cap is clamped to a minimum of 2');
  api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 3}});
  // Five tmux tabs, '5' is the active/newest; evict the 2 least-recently-used others.
  const tabs = ['1', '2', '3', '4', '5'];
  api.setTabLastActivatedForTest('1', 10);
  api.setTabLastActivatedForTest('2', 50);
  api.setTabLastActivatedForTest('3', 20);
  api.setTabLastActivatedForTest('4', 90);
  api.setTabLastActivatedForTest('5', 100);
  assert.deepStrictEqual([...api.tabsToEvictForCap(tabs, '5')], ['1', '3'], 'LRU eviction drops the two oldest non-active tabs');
  assert.deepStrictEqual([...api.tabsToEvictForCap(['1', '2'], '2')], [], 'no eviction when the pane is within the cap');
  // A dirty editor tab is never evicted; the next LRU tab goes instead.
  const editorItem = api.fileEditorItemFor('/x/draft.txt');
  api.setOpenFileStateForTest('/x/draft.txt', {kind: 'text', dirty: true, content: 'x', original: ''});
  api.setTabLastActivatedForTest(editorItem, 1);  // oldest, but dirty -> protected
  const withDirty = ['1', '2', editorItem, '5'];
  api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 2}});
  const evicted = api.tabsToEvictForCap(withDirty, '5');
  assert.ok(!evicted.includes(editorItem), 'a dirty/unsaved editor tab is never auto-closed');
  assert.ok(evicted.includes('1'), 'the oldest non-dirty tab is evicted instead of the dirty editor');
  assert.ok(source.includes('const evicted = tabsToEvictForCap(tabs, session);'), 'moveSessionToSlot enforces the tab cap when a tab joins a pane');
}

{
  // DOIT.6 #4: the session popover shows review status AND who reviewed.
  const api = loadYolomux('', ['5']);
  const approved = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'APPROVED', review_reviewers: [{login: 'alice', state: 'APPROVED'}]});
  assert.ok(/Approved by alice/.test(approved) && /pr-status-passing/.test(approved), 'popover shows "Approved by <login>"');
  const changes = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'CHANGES_REQUESTED', review_reviewers: [{login: 'bob', state: 'CHANGES_REQUESTED'}]});
  assert.ok(/Changes requested by bob/.test(changes) && /pr-status-failing/.test(changes), 'popover shows "Changes requested by <login>"');
  const required = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'REVIEW_REQUIRED', review_reviewers: []});
  assert.ok(/Review required/.test(required), 'popover shows "Review required" when unreviewed');
  assert.equal(api.pullRequestReviewInlineHtml({number: 12}), '', 'no review part without a decision');
  // Approved with no reviewer login still renders the status without a dangling "by".
  const approvedNoWho = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'APPROVED', review_reviewers: []});
  assert.ok(/Approved</.test(approvedNoWho) && !/ by /.test(approvedNoWho), 'approved with no reviewer omits the "by" clause');
}

{
  // DOIT.6 #1/#2/#3: tab badge legibility (light), drop the redundant PR pill, no duplicate tooltip.
  const api = loadYolomux('', ['5']);
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  // #1: light-theme overrides exist for the badge chips, incl. a readable (non-transparent) review-required.
  assert.ok(/body\.theme-light \.ci-indicator\.pr-review-required\s*\{[^}]*background:\s*#e7ebf1/.test(css), '#6: review-required chip is legible (light fill) in light theme');
  assert.ok(/body\.theme-light \.ci-indicator\.pr-number-chip/.test(css) && /body\.theme-light \.ci-indicator\.pr-review-approved/.test(css), '#6: light-theme overrides cover number + review chips');
  // #2: the ready-review "PR" state pill is dropped (PR chips convey it now); other states still render.
  assert.equal(api.sessionStateHtml({key: 'ready-review', short: 'PR', label: 'Ready for review', reason: 'checks pass'}), '', '#7: the redundant ready-review PR pill is suppressed');
  assert.ok(api.sessionStateHtml({key: 'needs-input', short: '?', label: 'Needs input', reason: 'waiting'}).includes('session-state-needs-input'), '#7: actionable states still render a badge');
  // #3: tab badge chips carry no native title (the custom popover is the single source).
  assert.ok(!api.pullRequestNumberIndicatorHtml('5', {number: 123}).includes('title='), '#8: the PR number chip has no native title tooltip');
  assert.ok(!api.pullRequestApprovalIndicatorHtml('5', {number: 123, state: 'open', review_decision: 'APPROVED'}).includes('title='), '#8: the approval chip has no native title tooltip');
  assert.ok(!api.pullRequestCompactBadgesHtml('5', {number: 123, state: 'open', review_decision: 'APPROVED'}).includes('title='), '#8: the compact PR badge row has no native title tooltips');
}

{
  // #38: tab APPROVAL badge driven by GitHub reviewDecision.
  const api = loadYolomux('', ['5']);
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  const approved = api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open', review_decision: 'APPROVED'});
  assert.ok(/pr-review-approved/.test(approved) && />Approved</.test(approved), '#38: approved PR shows a green Approved badge');
  const changes = api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open', review_decision: 'CHANGES_REQUESTED'});
  assert.ok(/pr-review-changes/.test(changes) && />Changes</.test(changes), '#38: changes-requested PR shows a red Changes badge');
  const required = api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open', review_decision: 'REVIEW_REQUIRED'});
  assert.ok(/pr-review-required/.test(required) && />Review</.test(required), '#38: review-required PR shows the neutral/crossed-out badge');
  // No badge without a PR, without a decision, or once merged.
  assert.equal(api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open'}), '', '#38: no review badge when reviewDecision is absent');
  assert.equal(api.pullRequestApprovalIndicatorHtml('5', null), '', '#38: no review badge without a PR');
  assert.equal(api.pullRequestApprovalIndicatorHtml('5', {number: 12345, merged: true, review_decision: 'APPROVED'}), '', '#38: no review badge once the PR is merged');
  // The compact badge row carries the approval badge alongside #/CI.
  assert.ok(/pr-review-approved/.test(api.pullRequestCompactBadgesHtml('5', {number: 12345, state: 'open', review_decision: 'APPROVED'})), '#38: the compact PR badge row includes the approval badge');
  assert.ok(/\.ci-indicator\.pr-review-approved/.test(css), '#38: approval badge has an approved (green) color class');
  assert.ok(/\.ci-indicator\.pr-review-required[\s\S]*?line-through/.test(css), '#38: the review-required badge is crossed out');
}

{
  const api = loadYolomux('', ['1']);
  const strip = new TestElement('strip');
  const tab = new TestElement('tab');
  const popover = new TestElement('popover');
  strip.className = 'pane-tabs';
  tab.className = 'pane-tab popover-open';
  tab.dataset.paneTab = '1';
  tab.dataset.popoverHoverState = 'open';
  popover.className = 'session-popover';
  strip.appendChild(tab);
  tab.appendChild(popover);
  api.restorePaneTabPopover(strip, '1');
  assert.equal(api.paneTabPopoverItemToRestore(strip), '1');
  assert.equal(api.bodyChildren().length, 0);
  assert.equal(api.paneTabShouldPreserve(tab), true);
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function renderTreeChildren(');
  const end = source.indexOf('function rawFileUrl(', start);
  assert.ok(start > 0 && end > start, 'could not locate renderTreeChildren body');
  const body = source.slice(start, end);
  assert.equal(body.includes('replaceChildren(...nextNodes)'), false, 'Finder refresh must reconcile existing rows instead of rebuilding them');
  assert.ok(source.includes('function updateFileTreeRowContents('), 'Finder row text/icon updates are localized');
  const updateStart = source.indexOf('function updateFileTreeRowContents(');
  const updateEnd = source.indexOf('function updateFileTreeRow(', updateStart);
  assert.ok(updateStart > 0 && updateEnd > updateStart, 'could not locate updateFileTreeRowContents body');
  const updateBody = source.slice(updateStart, updateEnd);
  assert.equal(updateBody.includes("name.textContent = nameText;\n    name.innerHTML = '';"), false, 'Finder row text must not be cleared after being written');
  assert.ok(updateBody.includes("name.innerHTML = '';\n    name.textContent = nameText;"), 'Finder row clears stale HTML before writing plain text');
  assert.ok(source.includes("refreshActivitySummary({force: true})"), 'YO!agent Refresh summary forces cached summaries to rebuild');
  assert.ok(source.includes("api/activity-summary${options.force ? '?force=1' : ''}"), 'YO!agent summary API supports a force refresh query');
  assert.ok(source.includes("data-yolo-rule-open"), 'Preferences exposes an Open button for the YOLO rule file');
  assert.ok(source.includes("apiFetch('/api/yoagent/reset'"), 'YO!agent clear conversation resets the server-side CLI session');
  assert.ok(source.includes("renderYoagentPanel({preserveDraft: false, scrollBottom: true})"), 'YO!agent send/clear clears the draft and scrolls chat to the bottom');
  assert.ok(source.includes('draggable="true" data-open-change-file='), 'Modified-files rows are draggable as file payloads');
  assert.ok(source.includes("event.dataTransfer.setData('application/x-yolomux-file'"), 'Modified-files drag carries the same file payload as Finder drag');
  assert.ok(source.includes("'Allow index'"), 'Finder directory context menu exposes Allow index');
  assert.ok(source.includes("'Disallow index'"), 'Finder directory context menu exposes Disallow index');
  assert.ok(source.includes("row.classList.toggle('indexed-directory', indexedDirectory)"), 'Finder row render marks indexed directories');
  assert.ok(source.includes("'file-icon-dir-indexed'"), 'Finder indexed directories use a distinct icon class');
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.fileImagePreviewMinShowDelayMs, 800);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(source.includes('Math.max(fileImagePreviewMinShowDelayMs, tabPopoverShowDelayMs)'), 'image previews share the tab-style delayed hover threshold');
  assert.ok(css.includes('--file-image-preview-max-size: 320px'), 'Finder image preview default max size is tokenized');
  assert.ok(/\.file-image-preview-popover[\s\S]*pointer-events:\s*none/.test(css), 'Finder image previews cannot keep themselves hovered over terminals');
  assert.ok(source.includes('preserveScroll: sameImage'), 'image viewer refreshes preserve scroll on unchanged images');
  assert.ok(source.includes('captureFileEditorPanelViewState(item, panel)'), 'CodeMirror editor viewport is captured before pane/tab renders');
  assert.ok(source.includes('restoreFileEditorPanelViewState(item, panel)'), 'CodeMirror editor viewport is restored after pane/tab renders');
  assert.ok(source.includes('refreshOpenEditorThemePanels'), 'global/editor theme changes update already-open CodeMirror panels');
  assert.ok(source.includes('function reconfigureCodeMirrorPanelTheme'), 'open CodeMirror panels reconfigure their theme compartment');
  assert.ok(source.includes('&& api?.Compartment'), 'CodeMirror API validation requires Compartment for in-place theme updates');
  assert.equal(source.includes('scheme: activeEditorScheme().id'), false, 'CodeMirror config signatures do not force panel rebuilds on theme-only changes');
  assert.ok(source.includes('function yoagentChatNetworkError'), 'YO!agent chat distinguishes network failures from normal HTTP errors');
  assert.ok(source.includes('focusInput: true'), 'YO!agent chat refocuses the input after responses and retryable failures');
  assert.ok(source.includes('function refreshYoagentSummaryRegions'), 'YO!agent metadata refresh can update summaries without rebuilding the chat input');
  assert.ok(source.includes('summaryOnly: true'), 'YO!agent metadata refresh requests summary-only panel updates');
  // #45: assistant replies are structured Markdown — flag the body and render it through marked.js.
  assert.ok(source.includes('function renderYoagentMessageMarkdown'), '#45: YO!agent assistant replies render their multi-section Markdown body');
  assert.ok(/\.yoagent-message\.assistant \.yoagent-message-body\[data-yoagent-markdown\]/.test(source), '#45: the markdown render pass targets flagged assistant message bodies');
  assert.ok(/renderMarkdownPreviewInto\(body, body\.textContent/.test(source), '#45: assistant message Markdown is rendered from the escaped-text fallback');
  assert.ok(source.includes("roleClass === 'assistant' ? 'yoagent-message-body markdown-body'"), '#45: assistant message bodies get the markdown-body class for formatting');
  // #42: editor controls (# / wrap / find / FROM-TO / diff / theme / save) move OFF the tab strip
  // onto a dedicated toolbar info line below the tabs; the tab strip keeps only tabs + frame controls.
  const editorToolbarIdx = source.indexOf('class="file-editor-toolbar" role="toolbar"');
  const editorFrameActionsIdx = source.indexOf('file-editor-frame-actions');
  const editorTabsIdx = source.indexOf('<div class="pane-tabs"', editorFrameActionsIdx);
  const editorGutterIdx = source.indexOf('<button type="button" class="file-editor-gutter-panel"');
  assert.ok(editorToolbarIdx > -1, '#42: editor controls render on a dedicated .file-editor-toolbar info line');
  assert.ok(editorGutterIdx > editorToolbarIdx, '#42: the # / line-numbers control lives in the toolbar row, not the tab strip');
  assert.ok(!/file-editor-gutter-panel|file-editor-find-panel|file-editor-diff-ref-panel|file-editor-wrap-panel/.test(source.slice(editorFrameActionsIdx, editorTabsIdx)), '#42: the editor tab strip is uncluttered — only tabs + frame controls remain');
  assert.ok(/\.panel\.file-editor-panel\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(css), '#42: the editor panel grid reserves a row for the toolbar between tabs and body');
  assert.ok(/\.file-editor-toolbar\[hidden\]\s*\{\s*display:\s*none/.test(css), '#42: the editor toolbar row collapses when no controls are visible');
  assert.ok(source.includes('const currentText = String(state.content || \'\');'), 'plain CodeMirror editor mode owns its current text value');
  assert.ok(source.includes('function setLimitedMapEntry'), 'long-lived frontend maps share a bounded LRU setter');
  assert.ok(source.includes('fileExplorerMemoryCacheLimit = 512'), 'file explorer memory caches are capped');
  assert.ok(source.includes('commandPaletteRecentKeyLimit = 100'), 'command palette recent-key cache is capped');
  assert.ok(source.includes('restoreElementScrollPosition(container, scrollTop, scrollLeft);'), 'editor preview renders preserve scroll position');
  assert.equal(source.includes("const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef});\n  installCodeMirrorDiffResizeObserver"), false, 'diff resize observer is not installed before the rebuild decision');
  assert.ok(source.includes('openedItem = await openFileInEditor(path, {name: label}, {userInitiated: true});'), 'quick-open waits for file opens and passes user intent');
  assert.ok(source.includes('focusQuickOpenedFile(openedItem);'), 'quick-open focuses the opened file after the async open resolves');
  assert.ok(source.includes('await Promise.resolve(action?.());'), 'command palette selection awaits async run handlers before focus settles');
  assert.ok(source.includes('function focusCommandPaletteTarget'), 'command palette has one shared post-run focus helper');
  assert.ok(source.includes('focusCommandPaletteTarget(item);'), 'command palette applies deterministic focus after async tab/session actions');
  assert.ok(source.includes('targetItem: item,'), 'command palette tab entries carry their layout focus target');
  assert.ok(source.includes("const defaultLightEditorScheme = 'vscode-light-plus';"), 'light editor defaults to the VS Code Light+ style scheme');
  assert.ok(source.includes("else if (!isFilePreviewItem(item)) setFileEditorViewMode(fullPath, 'edit', item);"), 'plain file opens reset stale diff mode back to edit');
  assert.ok(source.includes('applyMarkdownSourceLines(container, text);'), 'Markdown preview source anchors are attached after parsing');
}

{
  const api = loadYolomux('', ['1']);
  const section = new TestElement('section');
  section.rect = {left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500};
  const row = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  assert.equal(api.splitPercentForPointer(section, row, {clientX: 10, clientY: 0}), 32);
  assert.equal(api.splitPercentForPointer(section, row, {clientX: 990, clientY: 0}), 68);
  const nested = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 50);
  assert.equal(api.layoutNodeMinWidth(nested), 960);
}

{
  const api = loadYolomux('', ['1']);
  const item = api.registerFileEditorLayoutItem('/home/test/AGENTS.md');
  let focusCount = 0;
  const panel = {
    _cmView: {
      focus() {
        focusCount += 1;
      },
    },
  };
  const emptyPanel = {
    _cmView: null,
  };
  api.setAutoFocusEnabledForTest(true);
  api.setFocusedPanelItem('1');
  api.requestFileEditorPanelFocus(item);
  assert.equal(api.focusFileEditorPanelIfReady(emptyPanel, item), false);
  assert.equal(focusCount, 0);

  api.setFocusedPanelItem(item);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), true);
  assert.equal(focusCount, 1);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);

  api.requestFileEditorPanelFocus(item);
  assert.equal(api.focusFileEditorPanelIfReady(emptyPanel, item), false);
  assert.equal(focusCount, 1);

  api.setAutoFocusEnabledForTest(false);
  api.setFocusedPanelItem(item);
  api.requestFileEditorPanelFocus(item);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);
  assert.equal(focusCount, 1);

  api.setAutoFocusEnabledForTest(true);
  api.requestFileEditorPanelFocus(item);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), true);
  assert.equal(focusCount, 2);
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.autoFocusEnabledForTest(), false, 'auto-focus is off by default');
  api.testElementForId('terminal-pane-1').classList.add('active');
  api.setAutoFocusEnabledForTest(false);
  api.selectPanelOnHover('1');
  assert.equal(api.focusedPanelItemForTest(), null);
  api.selectPanelOnHover('__files__');
  assert.equal(api.focusedPanelItemForTest(), null);

  const enabledApi = loadYolomux('', ['1']);
  enabledApi.setAutoFocusEnabledForTest(true);
  enabledApi.testElementForId('terminal-pane-1').classList.add('active');
  enabledApi.selectPanelOnHover('1');
  assert.equal(enabledApi.focusedPanelItemForTest(), '1');
}

{
  const api = loadYolomux('', ['1']);
  let focusCount = 0;
  api.registerTerminalForTest('1', {focus() { focusCount += 1; }});
  api.setAutoFocusEnabledForTest(false);
  api.focusPanel('1');
  assert.equal(api.focusedPanelItemForTest(), '1');
  assert.equal(focusCount, 0);
  api.focusPanel('1', {userInitiated: true});
  assert.equal(api.focusedPanelItemForTest(), '1');
  assert.equal(focusCount, 1);
  api.selectSession('1');
  assert.equal(api.focusedPanelItemForTest(), '1');
  assert.equal(focusCount, 2, 'selecting an already visible tmux tab is an explicit user focus action');
}

{
  const api = loadYolomux('', ['1', '2']);
  assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,yoagent,files,preferences,changes,image-viewer,file-editor,file-preview');
  assert.equal(api.resolveLayoutItem('yosup'), api.yoagentItemId, 'legacy yosup URL param resolves to YO!agent');
  assert.equal(api.resolveLayoutItem('__yosup__'), api.yoagentItemId, 'legacy yosup item id resolves to YO!agent');
  assert.equal(api.itemParam(api.yoagentItemId), 'yoagent', 'new YO!agent URLs use the new param');
  assert.equal(api.tabTypeForItem('__files__').key, 'files');
  assert.equal(api.tabTypeForItem('__changes__').key, 'changes');
  assert.equal(api.tabTypeForItem('image:/home/test/screen.png').key, 'image-viewer');
  assert.equal(api.tabTypeForItem('file:/home/test/README.md').key, 'file-editor');
  assert.equal(api.fileItemPath('image:/home/test/screen.png'), '/home/test/screen.png');
  api.setSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
    repos: [{repo: '/repo/app', count: 2, touched_count: 2, added: 10, removed: 1, behind: 0, ahead: 2}],
    files: [
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
      {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: 'src/new.py', abs_path: '/repo/app/src/new.py', mtime: 200, added: 8, removed: 0},
    ],
  });
  assert.ok(api.changesPaneTabHtml().includes('changes-count-badge'));
  const changesHtml = api.changesPanelHtml();
  assert.ok(changesHtml.includes('/repo/app'));
  assert.ok(changesHtml.includes('2 files changed in &#39;1&#39;'), 'Changes pane summary names the session explicitly');
  assert.ok(changesHtml.includes('changes-comparison-title">Comparing HEAD with Working Tree'), 'Changes pane shows the comparison header');
  assert.equal((changesHtml.match(/files changed in &#39;1&#39;/g) || []).length, 1, '#24: the file-count/+/- summary appears exactly once (in the comparison card), not duplicated in the toolbar');
  assert.equal(changesHtml.includes('class="changes-summary"'), false, '#24: the standalone toolbar summary duplicate is removed');
  assert.ok(changesHtml.includes('class="changes-comparison-summary"'), '#24: the summary lives in the comparison card');
  assert.ok(changesHtml.includes('Behind 0 commits'), 'Changes pane shows behind count');
  assert.ok(changesHtml.includes('Ahead 2 commits'), 'Changes pane shows ahead count');
  assert.ok(changesHtml.includes('changes-tree-folder-name">src/'), 'Changes pane groups nested paths under folders');
  assert.ok(changesHtml.includes('data-changes-folder-toggle="1|/repo/app|src"'), 'Changes tree folders are collapsible by a stable key');
  assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'), 'file leaves keep the open-file action');
  assert.ok(changesHtml.includes('changes-diff-add">+8</span>'), 'changed-file rows include green added counts');
  assert.ok(changesHtml.includes('changes-file-agent'), 'changed-file rows show the agent icon slot');
  assert.ok(changesHtml.includes('changes-file-icon'), 'changed-file rows show a file-type icon slot');
  assert.ok(changesHtml.includes('changes-file-date'), 'changed-file rows wrap the date for skinny styling');
  api.setChangesFolderCollapsedForTest(['1|/repo/app|src']);
  const collapsedChangesHtml = api.changesPanelHtml();
  assert.ok(collapsedChangesHtml.includes('changes-tree-folder collapsed'), 'collapsed changed-file folders keep their state');
  assert.equal(collapsedChangesHtml.includes('data-open-change-file="/repo/app/src/new.py"'), false, 'collapsed changed-file folders hide file leaves');
  api.setChangesFolderCollapsedForTest([]);
  assert.ok(changesHtml.includes('data-diff-ref-from'), 'Changes pane exposes FROM ref picker');
  assert.ok(changesHtml.includes('data-diff-ref-to'), 'Changes pane exposes TO ref picker');
  assert.ok(changesHtml.includes('data-diff-ref-from-select'), 'Changes pane exposes a scrollable FROM commit picker');
  assert.ok(changesHtml.includes('data-diff-ref-to-select'), 'Changes pane exposes a newer-target TO picker');
  assert.equal(/<input[^>]*data-diff-ref-from/.test(changesHtml), false, 'FROM control is one select, not a duplicated input plus select');
  assert.equal(/<input[^>]*data-diff-ref-to/.test(changesHtml), false, 'TO control is one select, not a duplicated input plus select');
  assert.ok(changesHtml.includes('older base commit'), 'Changes pane FROM picker includes recent commit subjects');
  assert.equal(api.diffRefFromSuggestions().some(item => item.ref === 'current'), false, 'FROM picker does not suggest current as the older base');
  assert.equal(api.diffRefToSuggestions('HEAD').map(item => item.ref).join(','), 'current', 'TO picker only offers refs newer than the selected FROM base');
  const duplicateShaOptions = api.diffRefSelectOptionsHtml('abc123def4567890', {suggestions: [{ref: 'abc123d', short: 'abc123d', subject: 'same commit'}]});
  assert.equal((duplicateShaOptions.match(/<option/g) || []).length, 1, 'diff ref picker dedupes full SHA and short SHA for the same commit');
  assert.equal(duplicateShaOptions.includes('selected ref'), false, 'diff ref picker does not add a synthetic duplicate for a selected SHA already in suggestions');
  const changedFilesSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(changedFilesSource.includes("panel.addEventListener('dblclick', async event => {"), 'modified-file rows open from a double-click handler');
  const compactChangeHtml = api.changeFileRowHtml(
    {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
    {compact: true},
  );
  assert.ok(/changes-status[^>]*>M<\/span>[\s\S]*changes-file-name[^>]*>README\.md<\/span>[\s\S]*changes-file-agent[\s\S]*changes-file-meta[\s\S]*changes-diff-add[^>]*>\+2<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*changes-file-date/.test(compactChangeHtml), 'compact changed-file row order is status, file, AI icon, counts, date');
  assert.equal(changesHtml.includes('>codex<'), false, 'changed-file rows do not spell out the agent kind');
  assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'));
  assert.ok(changesHtml.includes('data-open-change-status="A"'), 'changed-file clicks carry status for deleted-file diff opens');
  assert.ok(changedFilesSource.includes("const isAddedChange = normalizedStatus === 'A' || normalizedStatus === 'U' || normalizedStatus === '?';"), 'added/untracked changed files open through editable mode first');
  assert.ok(changedFilesSource.includes("viewMode: isAddedChange ? 'edit' : 'diff'"), 'added changed files fall back to normal editor mode instead of forcing diff');
  api.setUploadedFilesCollapsedForTest(true);
  api.setSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1}],
    files: [
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
      {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: '20260531-028.png', abs_path: '/repo/app/20260531-028.png', mtime: 200, added: 0, removed: 0, uploaded: true},
    ],
  });
  const uploadedCollapsedHtml = api.changesPanelHtml();
  assert.ok(uploadedCollapsedHtml.includes('Uploaded files (1)'), 'uploaded files render under a named disclosure group');
  assert.equal(uploadedCollapsedHtml.includes('20260531-028.png</span>'), false, 'uploaded files are collapsed by default');
  api.setUploadedFilesCollapsedForTest(false);
  assert.ok(api.changesPanelHtml().includes('20260531-028.png</span>'), 'expanded uploaded group shows uploaded rows');
  api.setFileExplorerSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
    repos: [{repo: '/repo/app', count: 2, touched_count: 2, added: 2, removed: 1, behind: 0, ahead: 1}],
    files: [
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
    ],
  });
  assert.ok(api.fileExplorerChangesPanelHtml().includes('Modified files'), 'Finder embeds a modified-files panel');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('1 file changed in &#39;1&#39;'), 'Finder modified-files summary names the session explicitly');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('Ahead 1 commit'), 'Finder modified-files panel shows repo ahead counts');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('class="changes-title"'), 'Finder modified-files header has a responsive title cell');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('diff-ref-controls compact'), 'Finder modified-files panel exposes compact diff refs');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-diff-ref-from-select'), 'Finder compact modified-files header exposes the FROM commit picker');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-diff-ref-to-select'), 'Finder compact modified-files header exposes the TO picker');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('data-session-files-display-toggle'), false, '#41: the modified-files density toggle is removed');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-file-row compact'), '#41: the Finder modified-files panel is always compact');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-file-explorer-changes-close'), '#44: the Modified-files header has a close (X) button to hide the section');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('>Compact</button>'), false, 'Finder density toggle is an icon, not paired text buttons');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-add">+2</span>'), 'Finder modified-files panel shows green added counts');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-remove">-1</span>'), 'Finder modified-files panel shows red removed counts');
  const changedFilesCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.changes-status\s*\{[\s\S]*width:\s*13px/.test(changedFilesCss), 'modified-file status chips are skinny');
  assert.ok(/\.changes-file-name\s*\{[\s\S]*font-weight:\s*500/.test(changedFilesCss), 'modified-file names are not bold');
  assert.ok(/\.changes-tree-folder-row\s*\{[\s\S]*grid-template-columns:\s*12px 16px minmax\(0, 1fr\) auto/.test(changedFilesCss), 'modified-file folders use a compact GitLens-style tree row');
  assert.ok(changedFilesCss.includes('.changes-status-r,'), 'modified-file rename/copy statuses get distinct colors');
  assert.ok(changedFilesCss.includes('body.theme-light .changes-comparison-head'), 'light theme explicitly restyles the Changes comparison header');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*overflow-y:\s*scroll/.test(changedFilesCss), 'Finder modified-files scrollbar stays visible');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*scrollbar-gutter:\s*stable/.test(changedFilesCss), 'Finder modified-files reserves scrollbar gutter');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*container-type:\s*inline-size/.test(changedFilesCss), 'Finder modified-files header uses pane-width container queries');
  assert.ok(changedFilesCss.includes('@container (max-width: 520px)'), 'Finder modified-files header wraps before narrow pane widths overlap');
  assert.ok(changedFilesCss.includes('grid-template-areas:'), 'Finder modified-files narrow header uses explicit row areas');
  assert.ok(changedFilesCss.includes('body.theme-light .file-explorer'), 'light theme explicitly restyles the Finder tree');
  assert.ok(changedFilesCss.includes('body.theme-light .file-explorer-changes-panel'), 'light theme explicitly restyles Finder modified-files');
  assert.ok(changedFilesCss.includes('.file-tree-row.kind-file .file-tree-name'), 'Finder filenames resolve to row text colors instead of inherited stale colors');
  assert.ok(changedFilesCss.includes('flex-wrap: wrap;'), 'Finder toolbar wraps instead of clipping quick-access controls');
  assert.ok(/\.file-explorer-quick-access,\s*\.file-explorer-quick-access-panel\s*\{[\s\S]*flex:\s*0 0 auto/.test(changedFilesCss), 'Finder quick-access buttons do not shrink out of view');
  const fakeChangesScroll = {scrollTop: 45, scrollLeft: 3, innerHTML: ''};
  api.replaceHtmlPreservingScroll(fakeChangesScroll, '<div>updated</div>');
  assert.equal(fakeChangesScroll.innerHTML, '<div>updated</div>');
  assert.equal(fakeChangesScroll.scrollTop, 45, 'modified-files refresh preserves vertical scroll');
  assert.equal(fakeChangesScroll.scrollLeft, 3, 'modified-files refresh preserves horizontal scroll');
  const diffLines = api.parseUnifiedDiffLineClasses(`@@ -1,2 +1,3 @@
 one
-old
+new
+extra
`);
  assert.deepStrictEqual(Array.from(diffLines.added), [2, 3]);
  assert.deepStrictEqual(Array.from(diffLines.removed), [2]);
  const filesTab = api.fileExplorerPaneTabHtml();
  assert.equal(api.fileExplorerLabel(), 'File Explorer');
  assert.ok(filesTab.includes('File Explorer'));
  const appSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(appSource.includes("const editorViewModes = new Set(['edit', 'preview', 'split', 'diff'])"), 'file editor registers diff as a real view mode');
  assert.ok(appSource.includes('new api.MergeView'), 'wide diff mode uses CodeMirror MergeView');
  assert.ok(appSource.includes('api.unifiedMergeView'), 'narrow diff mode uses CodeMirror unified merge view');
  assert.equal(appSource.includes('allowInlineDiffs: true'), false, 'unified diff uses native deleted chunks so removed lines are not editable doc lines');
  // #17: deleted rows carry NO line number and stay read-only — done structurally, not via a CSS
  // transparent-text hack. The unified diff edits the MODIFIED document and overlays the original
  // through unifiedMergeView, so deleted lines are merge-decoration widgets (read-only, unnumbered),
  // never real numbered document lines. (No real CodeMirror in this Node harness, so this guards the
  // construction that produces the rendered behaviour, which was verified visually.)
  assert.ok(/api\.EditorState\.create\(\{\s*doc: currentText,\s*extensions: \[\s*api\.unifiedMergeView\(\{\s*original,/.test(appSource), 'unified diff edits the modified document and overlays the original via unifiedMergeView, so deleted rows are unnumbered read-only widgets');
  const diffGutterCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.equal(/\.cm-deletedLineGutter\s*\{[^}]*color:\s*transparent/.test(diffGutterCss), false, '#17: deleted-line numbers are suppressed natively by unifiedMergeView, not by a transparent-text gutter hack');
  assert.ok(appSource.includes('/api/fs/diff?path=${encodeURIComponent(path)}&${diffRefQueryString()}'), 'editor diff requests carry FROM/TO refs');
  assert.ok(appSource.includes("const diffTargetIsCurrent = !state.diffToRef || state.diffToRef === 'current';"), 'diff editor editability follows TO=current after the FROM/TO flip');
  assert.ok(appSource.includes('const diffEditsAllowed = diffTargetIsCurrent;'), 'diff editor allows edits on the new/current side');
  assert.ok(/function destroyCodeMirrorPanel[\s\S]*\.cm-diff-overview'\)\?\.remove\(\)/.test(appSource), '#26: tearing down the CodeMirror panel removes the diff scrollbar overview so its red/green ticks do not linger in edit/normal mode');
  const diffLayoutFn = appSource.slice(appSource.indexOf('function codeMirrorDiffLayout('), appSource.indexOf('function codeMirrorDiffLayout(') + 800);
  assert.ok(diffLayoutFn.includes("return 'inline';"), '#33: the diff always uses the unified (inline) layout');
  assert.equal(diffLayoutFn.includes("'side'"), false, '#33: the wide-pane side-by-side layout (which numbered deleted rows) is no longer selected, so deleted rows are unnumbered widgets at every width');
  assert.equal(api.codeMirrorDiffLayout({getBoundingClientRect: () => ({width: 2000})}), 'inline', '#33: even a very wide pane uses the unified (inline) diff, so deleted rows are never numbered');
  assert.equal(api.codeMirrorDiffLayout({getBoundingClientRect: () => ({width: 300})}), 'inline', '#33: a narrow pane also uses the unified diff');
  assert.ok(/if \(state\.diffLoading && state\._diffLoadingPromise\) return state\._diffLoadingPromise/.test(appSource), '#43: concurrent diff loads are deduped (callers await one in-flight load), so the panel never renders against an un-loaded original');
  assert.ok(/if \(!state\.diffLoaded && !state\.diffUnavailable\) await refreshOpenFileDiff/.test(appSource), '#43: the diff panel awaits the load before rendering, so an untracked/all-added file resolves to diffUnavailable and falls back to the plain editor instead of flashing all-green');
  assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, untracked: true, diff: 'diff --git a/a b/a\n--- /dev/null\n+++ b/a\n@@\n+x'}), false, '#43: an untracked/all-added file reports no diff, so it never enters diff view');
  assert.ok(/function openDraggedFilesInEditor[\s\S]*await refreshOpenFileDiff\(path[\s\S]*openFileDiffAvailable\(draggedState\)[\s\S]*setFileEditorViewMode\(path, 'diff'/.test(appSource), '#39: a dragged CHANGED file opens in the same unified diff view as double-click (routes through the shared refreshOpenFileDiff/diff path)');
  assert.ok(appSource.includes('data-file-explorer-new-folder'), 'Finder header exposes new-folder action');
  assert.ok(/switchFileExplorerChangesSession\(item\)/.test(appSource), 'tmux focus switches the Finder modified-files session immediately');
  assert.ok(/fetchSessionFiles\(\{destination: 'finder', session, silent: true, force: true\}\)/.test(appSource), 'tmux focus forces a fresh Finder modified-files fetch even if an older request is in flight');
  assert.equal(appSource.includes("state.kind === 'text' && !fileEditorAutosaveEnabled"), false, 'clean external file changes auto-reload even when autosave is off');
  assert.equal(appSource.includes('data-file-editor-close'), false, 'pane frame close uses the pane-close path, not active file-tab close');
  assert.equal(filesTab.includes('agent-icon file'), false);
  assert.ok(api.menuTabCommand('__files__').html.includes('app-menu-ui-icon-finder'));
  assert.ok(api.menuTabCommand('__prefs__').html.includes('app-menu-ui-icon-gear'));
  assert.ok(api.menuTabCommand('__info__').html.includes('app-menu-ui-icon-branch-info'));
  assert.ok(api.menuTabCommand('__yoagent__').html.includes('app-menu-ui-icon-yoagent'));
  assert.ok(api.menuTabCommand('__changes__').html.includes('app-menu-ui-icon-changes'));
  assert.ok(api.menuTabCommand('file:/home/test/README.md').html.includes('app-menu-ui-icon-document'));
  assert.equal(api.platformWindowControlClass('minimize'), 'pc-window-control pc-minimize');
  assert.equal(api.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(api.platformWindowControlClass('zoom'), 'pc-window-control pc-zoom');
  assert.equal(api.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(api.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');
  const pcPaneControls = api.panelControlsHtml('1');
  assert.ok(pcPaneControls.includes('pane-minimize pc-window-control pc-minimize'));
  assert.ok(pcPaneControls.includes('pane-expand pc-window-control pc-zoom'));
  assert.ok(pcPaneControls.includes('panel-detail-toggle pane-detail-toggle pc-window-control pc-minimize'));
  assert.equal(pcPaneControls.includes('>Info</button>'), false);
  assert.ok(pcPaneControls.indexOf('pane-detail-toggle') < pcPaneControls.indexOf('pane-minimize'));
  assert.ok(pcPaneControls.includes('hidden type="button" data-pane-expand="1"'));
  const expandablePcSlots = api.emptyLayoutSlots();
  expandablePcSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  expandablePcSlots.left = api.paneStateWithTabs(['1'], '1');
  expandablePcSlots.slot1 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(expandablePcSlots);
  assert.equal(api.canPaneExpand('1'), true);
  assert.ok(api.panelControlsHtml('1').includes('pane-expand pc-window-control pc-zoom'));
  assert.equal(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'), false);
  const placeholderPcSlots = api.emptyLayoutSlots();
  placeholderPcSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  placeholderPcSlots.left = api.paneStateWithTabs(['1'], '1');
  placeholderPcSlots.slot1 = api.emptyPlaceholderPaneState();
  api.setLayoutSlotsForTest(placeholderPcSlots);
  assert.equal(api.canPaneExpand('1'), false);
  assert.ok(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));

  const macApi = loadYolomux('', ['1'], 'http:', 'MacIntel');
  assert.equal(macApi.fileExplorerLabel(), 'Finder');
  assert.equal(macApi.platformWindowControlClass('minimize'), 'pc-window-control pc-minimize');
  assert.equal(macApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(macApi.platformWindowControlClass('zoom'), 'pc-window-control pc-zoom');
  assert.equal(macApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(macApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');
  const macPaneControls = macApi.panelControlsHtml('1');
  assert.ok(macPaneControls.includes('data-pane-minimize="1"'));
  assert.ok(macPaneControls.includes('data-detail-toggle="1"'));
  assert.ok(macPaneControls.includes('pane-minimize pc-window-control pc-minimize'));
  assert.ok(macPaneControls.indexOf('pane-detail-toggle') < macPaneControls.indexOf('pane-minimize'));
  assert.ok(macPaneControls.includes('data-pane-expand="1"'));
  assert.ok(macPaneControls.includes('pane-expand pc-window-control pc-zoom'));
  assert.ok(macPaneControls.includes('hidden type="button" data-pane-expand="1"'));
  const macFinderControls = macApi.panelControlsHtml('__files__');
  assert.ok(macFinderControls.includes('data-pane-close="__files__"'));
  assert.ok(macFinderControls.includes('pane-close pc-window-control pc-close'));
  assert.equal(macFinderControls.includes('data-pane-expand'), false);

  const forcedPcApi = loadYolomux('?platform=pc', ['1'], 'http:', 'MacIntel');
  assert.equal(forcedPcApi.fileExplorerLabel(), 'File Explorer');
  assert.equal(forcedPcApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(forcedPcApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(forcedPcApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');

  const forcedMacApi = loadYolomux('?platform=mac', ['1'], 'http:', 'Linux x86_64');
  assert.equal(forcedMacApi.fileExplorerLabel(), 'Finder');
  assert.equal(forcedMacApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(forcedMacApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(forcedMacApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');

  const singlePaneUrlApi = loadYolomux('?sessions=1&layout=left&tabs=left:1,2,3,4,5,6,ant', ['1', '2', '3', '4', '5', '6', 'ant']);
  assert.deepStrictEqual(Array.from(singlePaneUrlApi.layoutSlotKeys(singlePaneUrlApi.currentSlots())), ['left']);
  assert.deepStrictEqual(Array.from(singlePaneUrlApi.paneTabs('left')), ['1', '2', '3', '4', '5', '6', 'ant']);
  assert.equal(singlePaneUrlApi.canPaneExpand('1'), false);
  assert.ok(singlePaneUrlApi.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));

  const finderBesideSinglePaneUrlApi = loadYolomux(
    '?sessions=files,3&layout=row@22(slot1,left)&tabs=slot1:files;left:1,6,5,2,ant,4,3*',
    ['1', '2', '3', '4', '5', '6', 'ant'],
  );
  assert.deepStrictEqual(Array.from(finderBesideSinglePaneUrlApi.layoutSlotKeys(finderBesideSinglePaneUrlApi.currentSlots())), ['slot1', 'left']);
  assert.equal(finderBesideSinglePaneUrlApi.activeItemForSide('slot1'), '__files__');
  assert.deepStrictEqual(Array.from(finderBesideSinglePaneUrlApi.paneTabs('left')), ['1', '6', '5', '2', 'ant', '4', '3']);
  assert.equal(finderBesideSinglePaneUrlApi.activeItemForSide('left'), '3');
  assert.equal(finderBesideSinglePaneUrlApi.canPaneExpand('3'), false);
  assert.ok(finderBesideSinglePaneUrlApi.panelControlsHtml('3').includes('hidden type="button" data-pane-expand="3"'));

  const finderToggleSlots = api.emptyLayoutSlots();
  finderToggleSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 31);
  finderToggleSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  finderToggleSlots.right = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(finderToggleSlots);
  api.setFocusedPanelItem('1');
  const finderLayoutBeforeToggle = api.layoutParamValue(api.currentSlots());
  api.toggleFileExplorerShortcut();
  assert.equal(api.itemInLayout('__files__'), false, 'app shortcut hides the Finder pane');
  assert.equal(api.focusedPanelItemForTest(), '1', 'hiding Finder keeps focus on the active terminal');
  api.toggleFileExplorerShortcut();
  assert.equal(api.layoutParamValue(api.currentSlots()), finderLayoutBeforeToggle, 'app shortcut restores the prior Finder position and split size');
  assert.equal(api.focusedPanelItemForTest(), '1', 'restoring Finder keeps focus on the active terminal');

  const sidePreviewEditorItem = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/README.md');
  const sidePreviewSlots = api.emptyLayoutSlots();
  sidePreviewSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 20);
  sidePreviewSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  sidePreviewSlots.slot1 = api.paneStateWithTabs([sidePreviewEditorItem], sidePreviewEditorItem);
  sidePreviewSlots.slot2 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(sidePreviewSlots);
  api.setLayoutColumnRectsForTest({
    left: {left: 0, right: 220, top: 0, bottom: 800, width: 220, height: 800},
    slot1: {left: 230, right: 580, top: 0, bottom: 800, width: 350, height: 800},
    slot2: {left: 590, right: 1190, top: 0, bottom: 800, width: 600, height: 800},
  });
  assert.equal(api.largestPaneSlotForFileEditor(['slot1']), 'slot2', 'side preview chooses the next biggest existing non-Finder pane');

  const delayHtml = api.preferencesPanelHtmlForTest('delay', ['Performance']);
  assert.ok(delayHtml.includes('data-preference-section="Performance"'), 'delay search shows Performance');
  assert.equal(/data-preference-section="Performance"[\s\S]*preferences-settings" hidden/.test(delayHtml), false, 'search expands matching collapsed sections');
  assert.ok(delayHtml.includes('Metadata refresh interval'), 'delay search surfaces refresh interval settings');
  const preferencesCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(preferencesCss.startsWith('/* GENERATED by tools/static_build.py from static_src/'), 'generated CSS has a do-not-edit header');
  assert.ok(/\.preferences-search-button\s*\{[\s\S]*font:\s*700 var\(--ui-font-size-sm\)\/1\.1 var\(--ui-font\)/.test(preferencesCss), 'YOsearch uses the normal UI font, not condensed tab text');
  assert.ok(preferencesCss.includes('--file-explorer-changes-min-block-size: 96px'), 'modified-files resizer shares a stable min-size token');
  assert.ok(preferencesCss.includes('--drop-outline: #ffffff'), '#40: dark-mode drag preview/outline is white (light mode stays blue, asserted below)');
  assert.ok(/\.file-tree-repo-meta\s*\{[^}]*font-size: var\(--ui-font-size-2xs\)/.test(preferencesCss), '#37: the Finder repo/branch label is condensed to a smaller font so more files fit');
  assert.ok(preferencesCss.includes('--file-explorer-changes-size: 40%'), '#44: the Modified-files section defaults to 40% (2/5) of the Finder height');
  assert.ok(/body\.file-explorer-changes-hidden \.file-explorer-changes-panel/.test(preferencesCss), '#44: hiding the Modified-files section collapses both the panel and its resizer');
  assert.ok(/\.file-explorer-changes-panel \.changes-comparison-head\s*\{[^}]*flex-wrap: nowrap/.test(preferencesCss), '#44(d): the Finder comparison header is compacted to one tight line (header chrome takes less height)');
  assert.ok(/\.grid\.drop-preview::before/.test(preferencesCss), 'root layout drops have a full-layout preview overlay');
  assert.ok(/\.grid\.drop-preview-gutter::before\s*\{[\s\S]*--drop-preview-left/.test(preferencesCss), 'split-bar drops use explicit full-span preview geometry');
  assert.equal(/body\.theme-light\s+[^{]*(?:\.pane-tab|\.tabs|\.active-pane|\.panel-head)/.test(preferencesCss), false, 'global light theme does not restyle pane tab strips or active pane rings');
  assert.ok(preferencesCss.includes('--pane-tab-active-bg: #86d600'), 'focused active pane tab uses a brighter brand green fill');
  assert.ok(preferencesCss.includes('--pane-tab-active-accent: #86d600'), 'focused active pane tab accent token is the same green, not yellow/lime');
  assert.equal(preferencesCss.includes('box-shadow: inset 0 2px 0 var(--pane-tab-active-accent)'), false, 'focused active pane tabs do not paint a contrasting top line');
  assert.ok(preferencesCss.includes('--pane-tab-width: 180px'), 'pane tabs default to the compact 180px width');
  assert.ok(changedFilesSource.includes("numberSetting('appearance.tab_width', 180)"), 'runtime settings fallback keeps the 180px tab width default');
  assert.ok(/body\.theme-dark\s*\{[\s\S]*--pane-tab-strip-bg:\s*#1f3026/.test(preferencesCss), 'dark theme uses a greenish dark pane tab-strip background');
  assert.ok(/body\.theme-light\s*\{[\s\S]*--pane-tab-strip-bg:\s*#cfd4dd/.test(preferencesCss), 'light theme uses a bright pane tab-strip background');
  assert.ok(preferencesCss.includes('--pane-tab-unfocused-active-bg: #4f9e3a'), 'unfocused active tabs use a clearly-visible green, not gray (DOIT.6 #6: undimmed per-pane highlight)');
  assert.equal(preferencesCss.includes('--pane-tab-unfocused-active-bg: #aeb7c4'), false, 'gray unfocused-active pane tabs must not return');
  assert.ok(preferencesCss.includes('--pane-tab-panel-ring-width: 2px'), 'active pane ring uses a thin shared width token');
  assert.ok(/\.panel\.active-pane \.panel-head\s*\{[\s\S]*background:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'focused panes keep the same bright tab-strip background');
  assert.equal(/\.panel\.active-pane \.panel-head\s*\{[\s\S]*background:\s*var\(--pane-tab-panel-head-bg\)/.test(preferencesCss), false, 'focused panes do not recolor the tab strip green');
  assert.ok(preferencesCss.includes('.panel:not(.active-pane):not(.file-explorer-panel):not(.changes-panel) .pane-tab.active'), 'non-focused panes dim their active tab without touching Finder or Changes panes');
  assert.ok(preferencesCss.includes('--pane-split-gap: 0px'), 'pane split layout collapses gap through a shared token');
  assert.ok(preferencesCss.includes('--pane-resizer-size: 1px'), 'pane splitter reserves only the 1px separator line');
  assert.ok(preferencesCss.includes('--pane-resizer-bg: rgba(255, 225, 77, 0.72)'), 'dark pane splitter is a visible bright-yellow divider at rest');
  assert.ok(preferencesCss.includes('--pane-resizer-hover-bg: rgba(255, 225, 77, 0.96)'), 'dark pane splitter turns brighter on hover/resize');
  assert.ok(preferencesCss.includes('--pane-resizer-bg: rgba(217, 119, 6, 0.78)'), 'light pane splitter uses readable amber at rest');
  assert.ok(preferencesCss.includes('--pane-resizer-hover-line-size: 1.5px'), 'pane splitter hover thickens only modestly (1.5px) over the 1px resting line');
  assert.ok(preferencesCss.includes('--pane-tile-radius: 0'), 'adjacent panes meet flush with square corners (no rounded-corner seam wedges)');
  assert.ok(/\.topbar-search\s*\{[^}]*margin:\s*0 auto/.test(preferencesCss), '#29: topbar universal search is centered (auto margins both sides) between the menubar and the right-side actions, not right-aligned');
  assert.ok(/\.resizer-row::after\s*\{[^}]*inset-inline: -5px/.test(preferencesCss), '#34: the resizer has a wide invisible grab zone (~5px past the line) so it is easy to grab');
  assert.equal(/\.panel \{[^}]*border: 1px solid var\(--line\)/.test(preferencesCss), false, '#35: panes drop the per-pane border so the only divider is the 1px separator');
  assert.ok(preferencesCss.includes('box-shadow: inset 0 0 0 var(--pane-tab-panel-ring-width)'), '#35: the focused-pane green ring survives via an inset box-shadow (no seam width)');
  assert.ok(/\.grid\.drop-preview-root\.drop-preview-top::before,[^{]*\{[^}]*var\(--drop-preview-width/.test(preferencesCss), '#36: the root top/bottom drop preview spans only the non-Finder content width (never covers the docked Finder)');
  assert.ok(/\.layout-column\s*\{[\s\S]*gap:\s*var\(--pane-split-gap\)/.test(preferencesCss), 'pane split layout reads the compact gap token');
  assert.ok(/\.layout-resizer\s*\{[\s\S]*flex:\s*0 0 var\(--pane-resizer-size\)/.test(preferencesCss), 'pane splitters read the compact size token');
  assert.ok(/\.resizer-row::before\s*\{[\s\S]*left:\s*calc\(50% - \(var\(--pane-resizer-line-size\) \/ 2\)\)/.test(preferencesCss), 'row splitters draw a tokenized centered visible line');
  assert.ok(/\.resizer-row:hover::before,[\s\S]*var\(--pane-resizer-hover-line-size\)/.test(preferencesCss), 'row splitters widen on hover without increasing the resting seam');
  assert.ok(/\.layout-resizer:hover::before,[\s\S]*background:\s*transparent/.test(preferencesCss), 'resizer hover does not return to a solid fill');
  assert.ok(/\.resizer-row:hover::before,[^{]*\{[^}]*background: var\(--pane-resizer-hover-bg\)/.test(preferencesCss), '#27: row splitter hover is a solid straight yellow bar (no dashed gradient)');
  assert.ok(/\.resizer-column:hover::before,[^{]*\{[^}]*background: var\(--pane-resizer-hover-bg\)/.test(preferencesCss), '#27: column splitter hover is a solid straight yellow bar (no dashed gradient)');
  assert.ok(preferencesCss.includes('--pc-control-fg: #1f2937'), 'light mode uses a dark foreground for pane action controls');
  assert.ok(preferencesCss.includes('--pane-tab-panel-head-text: #1f2937'), 'light mode uses dark status text');
  assert.ok(/\.tabs \.pane-actions,\s*\n\.tabs \.panel-tab-overflow\s*\{[\s\S]*color:\s*var\(--pc-control-fg\)/.test(preferencesCss), 'pane actions use the shared platform-control foreground');
  assert.ok(/\.meta-path\s*\{[\s\S]*color:\s*var\(--pane-meta-path\)/.test(preferencesCss), 'status path color is theme-tokenized');
  assert.ok(/body\.editor-theme-light\s*\{[\s\S]*--drop-outline:\s*#1d4ed8/.test(preferencesCss), 'light editor panes switch drop-target outlines to readable blue');
  assert.ok(/\.file-editor-cross-split-panel,\s*\n\.file-editor-save-panel/.test(preferencesCss), 'side preview button uses the compact editor toolbar button sizing');
  assert.ok(/\.file-editor-panel-actions\s*\{[\s\S]*background:\s*color-mix\(in srgb, var\(--panel2\)/.test(preferencesCss), 'editor actions render as one compact gray toolbar');
  assert.ok(/\.file-editor-gutter-panel,\s*\n\.file-editor-wrap-panel,\s*\n\.file-editor-find-panel,\s*\n\.file-editor-diff-panel/.test(preferencesCss), 'diff button shares the compact editor toolbar sizing');
  assert.ok(preferencesCss.includes('--code-diff-add: #3fb950'), 'dark diff add base is vivid green');
  assert.ok(preferencesCss.includes('--code-diff-remove: #f85149'), 'dark diff remove base is vivid red');
  assert.equal(preferencesCss.includes('--code-diff-add: #98c379'), false, 'muted one-dark diff green must not return as the YOLOmux dark default');
  assert.equal(preferencesCss.includes('--code-diff-remove: #e06c75'), false, 'muted one-dark diff red must not return as the YOLOmux dark default');
  assert.ok(preferencesCss.includes('var(--code-diff-remove) 76%'), '#39: dark diff removed-line fill is high-contrast');
  assert.ok(preferencesCss.includes('var(--code-diff-add) 74%'), '#39: dark diff added-line fill is high-contrast');
  assert.ok(preferencesCss.includes('var(--code-diff-remove) 90%'), '#39: dark diff removed-token fill is stronger than the line tint');
  assert.ok(preferencesCss.includes('var(--code-diff-add) 88%'), '#39: dark diff added-token fill is stronger than the line tint');
  assert.ok(preferencesCss.includes('.file-editor-icon-side-split'), 'cross-pane side preview has a distinct icon');
  assert.ok(preferencesCss.includes('.panel.preview-linked:not(.active-pane)'), 'paired side-preview pane gets a thinner linked ring');
  assert.ok(/\.yoagent-global\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent global summary fits narrow panes');
  assert.ok(/\.yoagent-session-summaries\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent session summaries fit narrow panes');
  assert.ok(/\.yoagent-chat\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent chat fits narrow panes');
  assert.ok(/\.yoagent-chat\.empty\s*\{[\s\S]*grid-template-rows:\s*auto auto/.test(preferencesCss), 'empty YO!agent chat does not stretch an empty history row');
  assert.ok(preferencesCss.includes('body.editor-cursor-block .file-editor-codemirror .cm-cursor'), 'block cursor styling is available for CodeMirror');
  assert.ok(preferencesCss.includes('.file-editor-dialog-backdrop'), 'editor conflict and close decisions use the shared editor dialog');
  assert.equal(preferencesCss.includes('.app-menu-search-input'), false, 'Tabs menu no longer renders a sticky search input');
  assert.ok(preferencesCss.includes('.command-palette-detail .fuzzy-match'), 'command palette highlights fuzzy matches in detail text');
  assert.ok(preferencesCss.includes('.file-editor-diff-codemirror .cm-deletedChunk .cm-chunkButtons'), 'diff merge controls are positioned in the chunk margin');
  assert.ok(preferencesCss.includes('inset-inline-end: 8px !important'), 'diff merge controls sit on the right edge');
  assert.ok(preferencesCss.includes('.cm-diff-overview-tick'), 'diff overview ruler ticks are styled');
  assert.ok(/--diff-add-line-bg:\s*color-mix\(in srgb, var\(--code-diff-add\) 74%/.test(preferencesCss), '#39: diff added lines use a high-contrast green fill');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror\s*\{[\s\S]*--diff-add-line-bg:\s*#e6ffec/.test(preferencesCss), 'light diff added lines use GitHub-soft green');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror\s*\{[\s\S]*--diff-remove-line-bg:\s*#ffebe9/.test(preferencesCss), 'light diff removed lines use GitHub-soft red');
  assert.ok(/--diff-remove-line-bg:\s*color-mix\(in srgb, var\(--code-diff-remove\) 76%/.test(preferencesCss), '#39: diff removed lines use a high-contrast red fill');
  assert.equal(/\.cm-deletedLineGutter\s*\{[^}]*color:\s*transparent/.test(preferencesCss), false, 'deleted rows carry no number via unified-merge read-only widgets, not a transparent-text gutter hack');
  assert.ok(preferencesCss.includes('clip-path: inset(0 -100vw)'), 'diff line backgrounds extend to the full editor width');
  assert.ok(preferencesCss.includes('.file-editor-diff-codemirror .cm-insertedLine .cm-insertedText'), 'whole-line inserted rows suppress ragged token overlays');
  assert.ok(preferencesCss.includes('.file-tree-row.repo-non-main'), 'Finder repo rows have non-main branch styling');
  const preferencesHtml = api.preferencesPanelHtmlForTest('', []);
  assert.ok(preferencesHtml.indexOf('preferences-search-row') < preferencesHtml.indexOf('preferences-path-rows'), 'preferences search is first');
  assert.ok(preferencesHtml.includes('data-preferences-search-action>YOsearch</button>'), 'preferences search has an explicit YOsearch action');
  assert.equal(preferencesHtml.includes('preferences-global-reset'), false, 'preferences hide GLOBAL reset when every setting is already default');
  api.setClientSettingsPatchForTest({general: {auto_focus: true}});
  const modifiedPreferencesHtml = api.preferencesPanelHtmlForTest('', []);
  assert.ok(modifiedPreferencesHtml.indexOf('preferences-global-reset') > modifiedPreferencesHtml.indexOf('preferences-sections'), 'preferences global reset is below the setting sections');
  assert.ok(modifiedPreferencesHtml.includes('GLOBAL reset'), 'preferences reset is labeled as global');
  assert.ok(modifiedPreferencesHtml.includes('resets every Preferences value'), 'preferences reset carries a broad warning');
  assert.ok(modifiedPreferencesHtml.includes('data-preferences-reset-all'), 'preferences expose a global reset action after a setting changes');
  assert.equal(preferencesHtml.includes('data-preferences-reset-confirm'), false, 'preferences do not show the destructive confirmation until requested');
  const resetConfirmHtml = api.preferencesResetConfirmHtmlForTest();
  assert.ok(resetConfirmHtml.includes('data-preferences-reset-confirm'), 'reset-all requires a second continue action');
  assert.ok(resetConfirmHtml.includes('Continue reset'), 'reset-all confirmation names the continue action');
  assert.ok(resetConfirmHtml.includes('preferences-global-reset confirming'), 'reset-all confirmation makes the warning visibly change');
  assert.ok(preferencesHtml.includes('preferences-setting-control setting-type-number'), 'number controls are identifiable for compact sizing');
  assert.ok(preferencesHtml.includes('data-setting-path="file_explorer.image_preview_max_px"'), 'preferences expose Finder image preview sizing');
  assert.ok(preferencesHtml.includes('data-setting-path="uploads.max_bytes"'), 'preferences expose the upload size cap');
  api.setClientSettingsPatchForTest({uploads: {max_bytes: 64 * 1024 * 1024}});
  const largeUploadPreferencesHtml = api.preferencesPanelHtmlForTest('upload', []);
  assert.ok(largeUploadPreferencesHtml.includes('preferences-setting-advisory'), 'large upload cap shows an rsync recommendation');
  assert.ok(largeUploadPreferencesHtml.includes('rsync -avz'), 'large upload recommendation includes a copyable rsync command');
  assert.ok(preferencesHtml.includes('Auto-focus active pane'), 'auto-focus setting names the whole active pane/view');
  assert.ok(preferencesHtml.includes('enable hover-open menus'), 'auto-focus help covers menu hover behavior');
  assert.ok(preferencesHtml.includes('Off by default'), 'auto-focus help explains the default');
  assert.equal(preferencesHtml.includes('Auto-focus terminals'), false, 'auto-focus setting is not terminal-only');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.theme"'), 'preferences expose the global app theme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.terminal_theme"'), 'preferences expose the terminal color theme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_dark_color_scheme"'), 'preferences expose the dark editor scheme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_light_color_scheme"'), 'preferences expose the light editor scheme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_cursor_style"'), 'preferences expose the editor cursor style setting');
  assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave"'), 'preferences expose editor autosave');
  assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave_delay_seconds"'), 'preferences expose editor autosave delay');
  assert.ok(preferencesHtml.includes('data-setting-path="yoagent.backend"'), 'preferences expose YO!agent backend');
  assert.ok(preferencesHtml.includes('data-setting-path="yoagent.system_prompt"'), 'preferences expose YO!agent prompt');
  assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.system_prompt"[\s\S]*rows="12"/.test(preferencesHtml), 'YO!agent system prompt renders as a tall full-width row');
  assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.intro"[\s\S]*rows="12"/.test(preferencesHtml), 'YO!agent intro renders as a tall full-width row');
  assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.format"[\s\S]*rows="12"/.test(preferencesHtml), 'YO!agent format renders as a tall full-width row');
  assert.ok(/data-setting-path="file_explorer\.quick_access_paths"[\s\S]*data-setting-type="list"[\s\S]*rows="3"/.test(preferencesHtml), 'list settings keep compact textarea rows');
  assert.ok(/\.preferences-setting-row--wide\s*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/.test(preferencesCss), 'wide preference rows stack to one column');
  assert.ok(/\.preferences-setting-row--wide \.preferences-setting-control textarea\s*\{[\s\S]*grid-column:\s*1 \/ -1[\s\S]*min-height:\s*min\(34vh, 15lh\)/.test(preferencesCss), 'wide textarea controls span the row and stay tall');
  assert.ok(preferencesHtml.includes('No agent'), 'Preferences rename the local YO!agent backend to No agent');
  assert.equal(preferencesHtml.includes('Deterministic'), false, 'Preferences do not expose the internal deterministic backend label');
  assert.equal(preferencesHtml.includes('data-setting-path="appearance.editor_color_scheme"'), false, 'preferences do not show the legacy single mixed editor scheme setting');
  assert.ok(preferencesHtml.includes('<optgroup label="Dark">'), 'dark editor schemes are grouped under Dark');
  assert.ok(preferencesHtml.includes('<optgroup label="Light">'), 'light editor schemes are grouped under Light');
  assert.ok(preferencesHtml.indexOf('YOLOmux Dark') < preferencesHtml.indexOf('VS Code Dark+'), 'YOLOmux dark scheme appears first');
  assert.ok(preferencesHtml.indexOf('VS Code Light+') < preferencesHtml.indexOf('YOLOmux Light'), 'VS Code Light+ is the first light scheme');
  assert.ok(preferencesHtml.indexOf('YOLOmux Light') < preferencesHtml.indexOf('GitHub Light'), 'YOLOmux light scheme remains ahead of GitHub Light');
  assert.equal(api.globalThemeModeForTest(), 'dark');
  assert.equal(api.globalThemeIsDark(), true, 'global theme defaults dark');
  assert.equal(api.globalThemeLabel(), 'Dark');
  assert.equal(api.nextGlobalThemeMode(), 'light');
  assert.equal(api.terminalThemeModeForTest(), 'dark', 'terminal theme defaults dark');
  assert.equal(api.terminalThemeForGlobalTheme('light').background, '#11151d', 'light app theme keeps terminals dark by default');
  api.setTerminalThemeModeForTest('light');
  assert.equal(api.terminalThemeForGlobalTheme('dark').background, '#ffffff', 'terminal light theme is explicit opt-in');
  assert.equal(api.terminalThemeForGlobalTheme('dark').blue, '#0451a5');
  api.setTerminalThemeModeForTest('follow-app');
  assert.equal(api.terminalThemeForGlobalTheme('light').background, '#ffffff', 'follow-app maps to the resolved app theme');
  assert.equal(api.terminalThemeForGlobalTheme('dark').background, '#11151d');
  api.setTerminalThemeModeForTest('dark');
  api.setGlobalThemeModeForTest('light');
  api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
  assert.ok(api.bodyClassListForTest().contains('theme-light'), 'global light theme marks the body');
  assert.ok(api.bodyClassListForTest().contains('theme-resolved-light'), 'global light theme tracks the resolved mode');
  assert.equal(api.activeEditorSchemeForTest().label, 'VS Code Light+', 'inherited editor theme follows global light');
  assert.equal(api.configuredEditorSchemeForMode(true), 'dark');
  assert.equal(api.configuredEditorSchemeForMode(false), 'vscode-light-plus');
  api.setGlobalThemeModeForTest('dark');
  api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
  assert.equal(api.fileEditorThemeModeForTest(), 'inherit');
  assert.equal(api.activeEditorSchemeForTest().label, 'YOLOmux Dark');
  assert.equal(api.activeEditorSchemeForTest().activeLine, 'rgba(255, 255, 255, 0.04)');
  assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(87, 112, 148, 0.46)');
  assert.equal(api.activeEditorSchemeForTest().diff.addFg, '#3fb950');
  assert.equal(api.activeEditorSchemeForTest().diff.removeFg, '#f85149');
  assert.equal(api.activeEditorSchemeForTest().activeLine.includes('118, 185, 0'), false, 'YOLOmux dark active line is no longer green');
  assert.equal(api.activeEditorSchemeForTest().selection.includes('118, 185, 0'), false, 'YOLOmux dark selection is no longer green');
  api.setFileEditorThemeMode('github-light');
  assert.equal(api.fileEditorThemeModeForTest(), 'github-light');
  assert.equal(api.activeEditorSchemeForTest().label, 'GitHub Light');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--editor-scheme-bg'), '#ffffff');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--code-keyword'), '#cf222e');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-markdown-heading'), '#6f42c1');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-code-inline'), '#a40e26');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-code-inline-bg'), '#fff1d6');
  assert.notEqual(api.activeEditorSchemeForTest().syntax.heading, api.activeEditorSchemeForTest().syntax.link);
  assert.notEqual(api.activeEditorSchemeForTest().syntax.inlineCode, api.activeEditorSchemeForTest().syntax.heading);
  assert.equal(api.editorThemeLabel(), 'GitHub Light editor scheme');
  api.cycleEditorThemeMode();
  assert.equal(api.fileEditorThemeModeForTest(), 'inherit', 'theme toggle clears an explicit editor override back to global inheritance');
  api.cycleEditorThemeMode();
  assert.equal(api.fileEditorThemeModeForTest(), 'vscode-light-plus', 'theme toggle uses the selected VS Code Light+ scheme from inherited dark mode');
  api.setFileEditorThemeMode('light');
  assert.equal(api.fileEditorThemeModeForTest(), 'vscode-light-plus', 'legacy light storage value maps to VS Code Light+');
  assert.equal(api.activeEditorSchemeForTest().bg, '#ffffff', 'VS Code Light+ uses a bright white editor background');
  assert.equal(api.activeEditorSchemeForTest().previewBg, '#ffffff', 'VS Code Light+ preview background is bright white');
  assert.equal(api.activeEditorSchemeForTest().syntax.comment, '#008000', 'VS Code Light+ uses green comments');
  assert.equal(api.activeEditorSchemeForTest().syntax.control, '#af00db', 'VS Code Light+ uses purple control keywords');
  api.setFileEditorCursorStyleForTest('block');
  api.applyEditorCursorStyle();
  assert.ok(api.bodyClassListForTest().contains('editor-cursor-block'), 'block cursor style marks the body');
  api.setFileEditorCursorStyleForTest('line');
  api.applyEditorCursorStyle();
  assert.ok(api.bodyClassListForTest().contains('editor-cursor-line'), 'line cursor style marks the body');
  let focusedSearch = false;
  let selection = null;
  const search = {
    value: 'abc',
    focus(options) {
      focusedSearch = options?.preventScroll === true;
    },
    setSelectionRange(start, end) {
      selection = [start, end];
    },
  };
  assert.equal(api.focusPreferencesSearch({isConnected: true, querySelector: selector => selector === '[data-preferences-search]' ? search : null}), true);
  assert.equal(focusedSearch, true, 'Preferences search focus uses preventScroll');
  assert.deepStrictEqual(selection, [3, 3], 'Preferences search focus moves caret to the end');
  let freshFocusCount = 0;
  const freshPanel = {
    isConnected: true,
    querySelector: selector => selector === '[data-preferences-search]' ? {
      value: '',
      focus() { freshFocusCount += 1; },
      setSelectionRange() {},
    } : null,
  };
  api.setPreferencesSearchFreshForTest(true);
  api.focusFreshPreferencesSearchSoon(freshPanel);
  assert.ok(freshFocusCount > 0, 'fresh Preferences panes focus Search when the pane is focused');
  api.markPreferencesInteracted();
  freshFocusCount = 0;
  api.focusFreshPreferencesSearchSoon(freshPanel);
  assert.equal(freshFocusCount, 0, 'Preferences stops auto-focusing Search after a user changes settings or search text');
  const hugeItem = {path: 'appearance.editor_font_size', label: 'Editor font size', help: 'Font size used by editor text.', suffix: 'px'};
  assert.equal(api.preferenceItemMatches(hugeItem, 'huge'), true, 'size aliases match font settings');
  const popupItem = {path: 'performance.tab_popover_show_delay_ms', label: 'Tab detail hover delay', help: 'Initial delay before details open.', suffix: 'ms'};
  assert.equal(api.preferenceItemMatches(popupItem, 'tooltip'), true, 'tooltip aliases match popover settings');
  const explorerItem = {path: 'file_explorer.quick_access_paths', label: 'Quick paths', help: 'Pinned roots.', suffix: ''};
  assert.equal(api.preferenceItemMatches(explorerItem, 'bookmarks'), true, 'bookmark aliases match quick paths');
  assert.equal(api.preferenceSectionMatches({title: 'Notifications', items: []}, 'alerts'), true, 'section search uses aliases');

  const panelForPopover = {
    getBoundingClientRect() {
      return {left: 10, right: 500, top: 0, bottom: 500, width: 490, height: 500};
    },
  };
  const popoverForPosition = {
    getBoundingClientRect() {
      return {left: 0, right: 520, top: 0, bottom: 300, width: 520, height: 300};
    },
  };
  api.positionPaneTabPopover({
    getBoundingClientRect() {
      return {left: 34, right: 274, top: 40, bottom: 68, width: 240, height: 28};
    },
    querySelector(selector) {
      assert.equal(selector, ':scope > .session-popover');
      return popoverForPosition;
    },
    closest(selector) {
      assert.equal(selector, '.panel');
      return panelForPopover;
    },
  });
  const popoverStyle = api.documentElementStyleForTest();
  const popoverLeft = Number.parseInt(popoverStyle.getPropertyValue('--pane-tab-popover-left'), 10);
  assert.equal(popoverLeft, 34);
  assert.equal(popoverStyle.getPropertyValue('--pane-tab-popover-width'), '');
  assert.ok(popoverLeft + popoverForPosition.getBoundingClientRect().width <= 1200);
  assert.ok(popoverForPosition.getBoundingClientRect().width > panelForPopover.getBoundingClientRect().width);
  api.positionPaneTabPopover({
    getBoundingClientRect() {
      return {left: 1080, right: 1160, top: 40, bottom: 68, width: 80, height: 28};
    },
    querySelector() {
      return popoverForPosition;
    },
  });
  const clampedPopoverLeft = Number.parseInt(popoverStyle.getPropertyValue('--pane-tab-popover-left'), 10);
  assert.ok(clampedPopoverLeft + popoverForPosition.getBoundingClientRect().width <= 1200);
  const viewportClamp = api.clampToViewport(1190, 790, 100, 100, {edgeGap: 8});
  assert.equal(viewportClamp.left, 1092);
  assert.equal(viewportClamp.top, 692);
  const hoverAnchor = new TestElement('hover-anchor');
  const hoverPopover = new TestElement('hover-popover');
  hoverAnchor.appendChild(hoverPopover);
  let hoverOpens = 0;
  let hoverCloses = 0;
  const hoverController = api.createHoverPopoverForTest({
    anchor: hoverAnchor,
    popover: hoverPopover,
    showDelay: 0,
    hideDelay: 0,
    onOpen: () => { hoverOpens += 1; },
    onClose: () => { hoverCloses += 1; },
  });
  hoverController.openNow();
  assert.equal(hoverOpens, 1);
  assert.ok(hoverAnchor.classList.contains('popover-open'));
  hoverController.closeNow();
  assert.equal(hoverCloses, 1);
  assert.equal(hoverAnchor.classList.contains('popover-open'), false);
  const staleAnchor = new TestElement('stale-anchor');
  const stalePopover = new TestElement('stale-popover');
  staleAnchor.appendChild(stalePopover);
  let staleOpens = 0;
  const staleController = api.createHoverPopoverForTest({
    anchor: staleAnchor,
    popover: stalePopover,
    showDelay: 0,
    hideDelay: 0,
    onOpen: () => { staleOpens += 1; },
  });
  staleAnchor.hovered = false;
  staleController.openNow({type: 'pointerenter'});
  assert.equal(staleOpens, 0, 'stale delayed hover opens are suppressed after the pointer leaves');
  staleAnchor.hovered = true;
  staleController.openNow({type: 'pointerenter'});
  assert.equal(staleOpens, 1, 'active hover opens still work');
  const appMenuAnchor = new TestElement('app-menu-anchor');
  appMenuAnchor.rect = {left: 900, right: 980, top: 0, bottom: 28, width: 80, height: 28};
  const appMenuWrapper = new TestElement('app-menu-wrapper');
  appMenuWrapper.querySelector = selector => {
    assert.equal(selector, ':scope > .app-menu-button, :scope > .app-menu-command');
    return appMenuAnchor;
  };
  const appMenuPopover = new TestElement('app-menu-popover');
  appMenuPopover.parentElement = appMenuWrapper;
  appMenuPopover.rect = {left: 900, right: 1380, top: 28, bottom: 400, width: 480, height: 372};
  api.fitAppMenuPopover(appMenuPopover);
  assert.equal(appMenuPopover.style.getPropertyValue('--app-menu-fit-width'), '480px');
  assert.equal(appMenuPopover.style.getPropertyValue('--app-menu-fit-offset'), '-180px');
  const tabMenuCommand = api.menuTabCommand('1', {detail: 'Minimized'});
  assert.equal(tabMenuCommand.title, undefined);
  assert.equal(tabMenuCommand.detail, '');
  assert.equal(tabMenuCommand.ariaLabel, '1 - Minimized');
  assert.equal(tabMenuCommand.html.includes(' title='), false);
  const appMenuButton = api.createAppMenuCommand({label: 'Rename session', detail: 'Focus a tmux session first'});
  assert.equal(appMenuButton.title, undefined);
  assert.equal(appMenuButton.hasAttribute('title'), false);
  assert.equal(appMenuButton.getAttribute('aria-label'), 'Rename session - Focus a tmux session first');
  const checkedAppMenuButton = api.createAppMenuCommand({label: 'Hide tab metadata', checked: true});
  assert.equal(checkedAppMenuButton.className.includes('has-check'), false);
  assert.equal(checkedAppMenuButton.innerHTML.includes('>*<'), false);
  assert.equal(checkedAppMenuButton.dataset.checked, 'true');
  const appMenuIconButton = api.createAppMenuCommand({label: '+ Codex', iconHtml: '<span title="Codex">C</span>'});
  assert.equal(appMenuIconButton.innerHTML.includes('title='), false);
  const noMinimizedSlots = api.emptyLayoutSlots();
  noMinimizedSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  noMinimizedSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  noMinimizedSlots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(noMinimizedSlots);
  const tabMenu = api.appMenuTree().find(menu => menu.id === 'tabs');
  const tabMenuLabels = tabMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(tabMenuLabels.includes('Active'), false);
  assert.equal(tabMenuLabels.includes('Inactive'), false);
  assert.equal(tabMenuLabels.includes('Minimized'), false);
  assert.equal(tabMenuLabels.some(label => label.startsWith('No ')), false);
  assert.equal(tabMenu.items.filter(item => item.type === 'separator').length, 1);
  const menus = api.appMenuTree();
  assert.equal(menus.map(menu => menu.label).join(','), 'File,View,tmux,Tabs,Help');
  assert.equal(menus.some(menu => menu.id === 'yolo'), false);
  assert.equal(menus.some(menu => menu.id === 'settings'), false);
  const fileMenu = menus.find(menu => menu.id === 'file');
  const fileMenuLabels = fileMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(fileMenuLabels.includes('New tmux session'), false);
  assert.equal(fileMenuLabels.includes('Rename session'), false);
  assert.equal(fileMenuLabels.includes('Kill session'), false);
  assert.equal(fileMenuLabels.includes('Resume session'), false);
  assert.ok(fileMenuLabels.includes('YO!info'));
  assert.ok(fileMenuLabels.includes('YO!agent'));
  assert.ok(fileMenuLabels.includes('Preferences'));
  assert.ok(fileMenuLabels.indexOf('Preferences') < fileMenuLabels.indexOf('Log out'));
  assert.deepStrictEqual(canonical(fileMenu.items.slice(-3).map(item => item.type === 'separator' ? '---' : item.label)), ['Preferences', '---', 'Log out']);
  for (const label of [api.fileExplorerLabel(), 'YO!info', 'YO!agent', 'Open file', 'Preferences', 'Log out']) {
    const item = fileMenu.items.find(candidate => candidate.label === label);
    assert.ok(item?.iconHtml, `File menu ${label} uses the shared icon row`);
    assert.equal(item.className || '', '', `File menu ${label} does not use the raised tab-row scaffold`);
  }
  const tmuxMenu = menus.find(menu => menu.id === 'tmux');
  const tmuxMenuLabels = tmuxMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(tmuxMenu.items[0].label, 'YO off');
  assert.equal(tmuxMenu.items[0].keepOpen, true);
  assert.equal(tmuxMenuLabels.includes('New tmux session'), false);
  assert.ok(tmuxMenuLabels.includes('+ Claude'));
  assert.ok(tmuxMenuLabels.includes('+ Codex'));
  assert.ok(tmuxMenu.items.find(item => item.label === '+ Codex')?.detail !== 'Create tmux session');
  assert.ok(tmuxMenuLabels.includes("Transcript for session '1'"));
  assert.ok(tmuxMenuLabels.includes("AI Transcript for session '1'"));
  assert.ok(tmuxMenuLabels.includes("Event log for session '1'"));
  assert.ok(tmuxMenuLabels.includes('Pane details'));
  assert.ok(tmuxMenuLabels.includes("Rename tmux session '1'"));
  assert.ok(tmuxMenuLabels.includes("Kill tmux session '1'"));
  assert.equal(tmuxMenuLabels.includes("Enable YOLO for Tmux Session '1'"), false);
  assert.ok(tmuxMenuLabels.includes('Resume session'));
  assert.equal(tmuxMenu.badgeText, undefined);
  assert.ok(tmuxMenuLabels.includes('YOLO'));
  assert.ok(tmuxMenuLabels.indexOf('YOLO') > tmuxMenuLabels.indexOf('Resume session'), 'YOLO submenu stays at the bottom after session actions');
  const yoloMenu = tmuxMenu.items.find(item => item.label === 'YOLO');
  assert.equal(yoloMenu.type, 'submenu');
  assert.ok(yoloMenu.items.some(item => item.label === 'Open rule file'));
  assert.ok(yoloMenu.items.some(item => item.label === 'Reload rules'));
  assert.equal(yoloMenu.items.some(item => item.label === 'Sessions'), false);
  assert.equal(tmuxMenu.items.find(item => item.label === "Rename tmux session '1'").disabled, false);
  assert.equal(tmuxMenu.items.find(item => item.label === "Rename tmux session '1'").detail, '');
  api.setAutoApproveStateForTest('1', {enabled: true});
  const yoloTmuxMenu = api.appMenuTree().find(menu => menu.id === 'tmux');
  assert.equal(yoloTmuxMenu.badgeText, undefined);
  const yoloTabsMenu = api.appMenuTree().find(menu => menu.id === 'tabs');
  assert.equal(yoloTabsMenu.badgeText, '1');
  assert.equal(yoloTabsMenu.badgeTitle, '1 tmux session with YOLO enabled');
  assert.equal(yoloTmuxMenu.items[0].label, 'YO on');
  assert.equal(yoloTmuxMenu.items[0].keepOpen, true);
  assert.equal(yoloTmuxMenu.items[0].iconHtml.includes('session-yolo-marker'), true);
  assert.equal(yoloTmuxMenu.items.find(item => item.label === 'YOLO').items.some(item => item.label.startsWith('Sessions')), false);
  api.setAutoApproveStateForTest('1', {enabled: false});
  assert.equal(api.currentSessionActionTarget(), '1');
  const filesOnlySlots = api.emptyLayoutSlots();
  filesOnlySlots[api.layoutTreeKey] = api.leafNode('left');
  filesOnlySlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  api.setLayoutSlotsForTest(filesOnlySlots);
  const filesOnlyTmuxMenu = api.appMenuTree().find(menu => menu.id === 'tmux');
  assert.equal(filesOnlyTmuxMenu.items.find(item => item.label === 'Rename tmux session').disabled, true);
  assert.equal(filesOnlyTmuxMenu.items.find(item => item.label === 'Rename tmux session').detail, 'No tmux tab focused');
  const multiTmuxSlots = api.emptyLayoutSlots();
  multiTmuxSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 22);
  multiTmuxSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  multiTmuxSlots.slot1 = api.paneStateWithTabs(['1'], '1');
  multiTmuxSlots.slot2 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(multiTmuxSlots);
  api.setFocusedPanelItem('2');
  api.setFocusedPanelItem('__files__');
  assert.equal(api.currentSessionActionTarget(), '2');
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.startsWith('/* GENERATED by tools/static_build.py from static_src/'), 'generated JS has a do-not-edit header');
  assert.ok(source.includes('const mod = appModifier(event);'), 'global app shortcuts use one platform modifier');
  assert.equal(source.includes('const mod = event.ctrlKey || event.metaKey;'), false, 'global app shortcuts do not claim both Ctrl and Cmd');
  assert.ok(source.includes('function globalShortcutTargetAllowsPlatformAction(target)'), 'platform app shortcuts use a shared focus guard');
  assert.ok(source.includes("return isMacPlatform() || globalShortcutTargetAllowsAppAction(target);"), 'Mac app shortcuts bypass terminal focus so Cmd+P cannot fall through to browser Print');
  assert.ok(source.includes("if (mod && key === 'p' && platformActionAllowed)"), 'file quick-open is bound through the platform shortcut guard');
  assert.ok(source.includes('if (event.shiftKey) openCommandPalette();'), 'Shift plus app modifier opens the command palette');
  assert.ok(source.includes('else openFileQuickOpen();'), 'Plain app modifier plus P opens file quick-open');
  assert.ok(source.includes("if (event.key === ',')"), 'Preferences keeps best-effort comma shortcut in browser tabs');
  assert.ok(source.includes('selectSession(prefsItemId);'), 'Preferences shortcut opens the pane, while menu and palette remain fallbacks');
  assert.ok(source.includes("window.addEventListener('keydown', handleGlobalShortcutKeydown, true)"), 'global shortcuts run in capture phase before focused controls swallow them');
  assert.ok(source.includes("if (mod && key === 'w')"), 'Cmd/Ctrl+W is captured before browser/app defaults');
  assert.ok(source.includes('function itemCanCloseWithAppShortcut(item)'), 'global close shortcut is scoped through a shared item guard');
  assert.ok(source.includes('if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);'), 'Cmd/Ctrl+W cannot close Preferences, YO!agent, Finder, Changes, or tmux tabs');
  assert.ok(source.includes("(key === 'backspace' || key === 'delete') && globalShortcutTargetAllowsAppAction(event.target)"), 'Backspace/Delete close fallback stays out of text editing contexts');
  assert.equal(source.includes('Ctrl/Cmd'), false, 'served UI strings do not show Ctrl/Cmd combined shortcuts');
  assert.ok(source.includes('showFileSaveConflictDialog'), 'editor saves route conflicts through the shared conflict dialog');
  assert.ok(source.includes('autoSaveFileEditor'), 'editor autosave is wired into the built client');
  assert.ok(source.includes('promptExternalChangeBeforeEditing'), 'editing a changed-on-disk buffer prompts before continuing');
  const splitButtonIndex = source.indexOf('data-editor-mode="split"');
  const sidePreviewButtonIndex = source.indexOf('file-editor-cross-split-panel');
  const modeSeparatorIndex = source.indexOf('data-editor-toolbar-separator="mode"');
  assert.ok(splitButtonIndex > 0 && sidePreviewButtonIndex > splitButtonIndex && sidePreviewButtonIndex < modeSeparatorIndex, 'Open side preview sits directly in the editor mode button group after Split view');
  assert.ok(source.includes('editor.autosave_delay_seconds'), 'editor autosave delay is a persisted preference');
  assert.ok(source.includes('(commandPaletteIndex + 1) % commandPaletteItemsCache.length'), 'command palette arrow navigation wraps down');
  assert.ok(source.includes('item.splitRun'), 'command palette supports split-open actions');
  assert.ok(source.includes('function updateLinkedFilePreviewRings()'), 'side-preview ring state is centralized');
  assert.ok(source.includes("previewPanel.classList.add('preview-linked')"), 'focused editors mark their paired side-preview pane');
  const focusStart = source.indexOf('function setFocusedPanelItem(');
  const focusEnd = source.indexOf('function clearPendingFileEditorFocusExcept(', focusStart);
  assert.ok(source.slice(focusStart, focusEnd).includes('if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item);'));
  const viewMenu = menus.find(menu => menu.id === 'view');
  assert.equal(viewMenu.items.find(item => item.label === 'Hide tab metadata').iconHtml.includes('app-menu-ui-icon-tab-meta active'), true);
  assert.equal(viewMenu.items.find(item => item.label === 'Hide tab metadata').keepOpen, true);
  assert.equal(viewMenu.items.find(item => item.label === 'Alert').iconHtml.includes('app-menu-ui-icon-notify'), true);
  assert.equal(viewMenu.items.find(item => item.label === 'Alert').keepOpen, true);
  assert.equal(viewMenu.items.find(item => item.label === 'Refresh').iconHtml.includes('app-menu-ui-icon-refresh'), true);
  assert.equal(viewMenu.items.find(item => item.label === 'Refresh').keepOpen, undefined);
  assert.equal(viewMenu.items.find(item => item.label === 'Refresh').detail, undefined);
  const helpMenu = menus.find(menu => menu.id === 'help');
  const helpMenuLabels = helpMenu.items.map(item => item.label).filter(Boolean);
  assert.ok(helpMenuLabels.includes('Keyboard shortcuts'));
  assert.ok(helpMenuLabels.includes('Open README'));
  const shortcutsMenu = helpMenu.items.find(item => item.label === 'Keyboard shortcuts');
  assert.equal(shortcutsMenu.type, 'command');
  assert.equal(shortcutsMenu.detail, '?');
  assert.ok(source.includes('function keyboardShortcutCatalog()'), 'shortcut help is driven from one catalog');
  assert.ok(api.keyboardShortcutsHtml().includes('outside text'), 'shortcut overlay scopes the Backspace close-tab fallback');
  assert.equal(source.includes("key === 'k' && platformActionAllowed"), false, 'Cmd+K no longer opens the command palette');
  assert.ok(source.includes("!mod && globalShortcutTargetAllowsAppAction(event.target) && (event.key === '?'"), 'question-mark shortcut does not fire while typing in editors or terminals');
  assert.equal(api.appModifier({ctrlKey: true, metaKey: false, altKey: false}), true, 'PC app modifier is Ctrl');
  assert.equal(api.appModifier({ctrlKey: false, metaKey: true, altKey: false}), false, 'PC app modifier ignores Cmd/meta');
  const macShortcutApi = loadYolomux('?platform=mac', ['1'], 'http:', 'MacIntel');
  assert.equal(macShortcutApi.appModifier({ctrlKey: false, metaKey: true, altKey: false}), true, 'Mac app modifier is Cmd');
  assert.equal(macShortcutApi.appModifier({ctrlKey: true, metaKey: false, altKey: false}), false, 'Mac app modifier reserves Ctrl for tmux');
  assert.equal(macShortcutApi.appShortcutText('B'), '⌘+B');
  const pcOverrideShortcutApi = loadYolomux('?platform=pc', ['1'], 'http:', 'MacIntel');
  assert.equal(pcOverrideShortcutApi.appShortcutText('B'), 'Ctrl+B', 'platform override can force PC shortcut labels');
  assert.ok(Number.isFinite(api.fuzzySubsequenceScore('xy', 'hello X and blah Y')), 'fuzzy matcher allows ordered gaps');
  assert.equal(Number.isFinite(api.fuzzySubsequenceScore('xz', 'hello X and blah Y')), false, 'fuzzy matcher rejects missing characters');
  assert.ok(api.fuzzySearchScore('hel', ['helloXandYyy']) > api.fuzzySearchScore('hel', ['h e l']), 'fuzzy matcher ranks contiguous matches higher');
  assert.ok(api.fuzzySearchScore('READ', ['README.md']) > api.fuzzySearchScore('READ', ['src/README.md']), 'fuzzy matcher prefers primary field prefixes');
  assert.ok(api.fuzzySearchScore('yoagent', ['YO!agent']) > api.fuzzySearchScore('yoagent', ['1', 'y o agent in details']), 'punctuation-insensitive label prefixes beat scattered detail matches');
  assert.deepStrictEqual(Array.from(api.fuzzySubsequenceMatch('xy', 'helloXandYyy').indexes), [5, 9], 'fuzzy matcher exposes matched indexes for result highlighting');
  assert.ok(api.fuzzyHighlightHtml('xy', 'helloXandYyy').includes('<mark class="fuzzy-match">X</mark>'), 'palette results highlight matched characters');
  assert.ok(api.fuzzyHighlightHtml('10144', '#10144').includes('<mark class="fuzzy-match">10144</mark>'), 'contiguous fuzzy matches render as one box');
  assert.equal(api.commandPaletteMatches({group: 'Tabs', label: 'helloXandYyy', detail: ''}, 'xy'), true, 'command palette uses fuzzy matching');
  assert.equal(api.commandPaletteMatches({group: 'Tabs', label: 'helloXandYyy', detail: ''}, 'xz'), false, 'command palette rejects non-matches');
  assert.ok(
    api.commandPaletteItemScore({group: 'Tabs', label: 'YO!agent', detail: '', searchFields: ['YO!agent']}, 'yoagent') >
      api.commandPaletteItemScore({group: 'Tabs', label: '1', detail: 'y o agent buried in details', searchFields: ['1', 'y o agent buried in details']}, 'yoagent'),
    'command palette ranks YO!agent first for punctuation-insensitive prefix queries'
  );
  const paletteVirtualLabels = api.commandPaletteCommandItems().filter(item => [api.infoItemId, api.yoagentItemId, api.fileExplorerItemId, api.prefsItemId, api.changesItemId].map(api.itemLabel).includes(item.label));
  const expectedVirtualLabels = [api.infoItemId, api.yoagentItemId, api.fileExplorerItemId, api.prefsItemId, api.changesItemId].map(api.itemLabel);
  assert.equal(paletteVirtualLabels.length, expectedVirtualLabels.length, 'command palette lists each virtual tab once');
  assert.equal(expectedVirtualLabels.every(label => paletteVirtualLabels.some(item => item.label === label)), true, 'command palette includes all virtual tabs');
  assert.equal(paletteVirtualLabels.every(item => item.group === 'Tabs'), true, 'virtual tab palette entries come from the Tabs group, not duplicate menu commands');
  api.setFileQuickOpenCandidatesForTest('/repo/app', [
    {name: 'helloXandYyy.py', path: '/repo/app/src/helloXandYyy.py', relative_path: 'src/helloXandYyy.py'},
  ]);
  const quickItem = api.fileQuickOpenItems().find(item => item.label === 'helloXandYyy.py');
  assert.ok(quickItem, 'file quick-open uses the same command-palette item shell');
  assert.equal(api.commandPaletteMatches(quickItem, 'xy'), true, 'file quick-open uses fuzzy matching');
  assert.equal(api.fileQuickOpenRootForSearch(), '/home/test/yolomux.dev', 'file quick-open defaults to the active repo root when no session cwd is known');
  api.setTranscriptInfoForTest('1', {project: {git: {root: '/repo/workspace'}}, selected_pane: {current_path: '/repo/workspace/src'}});
  api.setFocusedPanelItem('1');
  assert.equal(api.fileQuickOpenRootForSearch(), '/repo/workspace', 'file quick-open searches the workspace root when tmux is inside a repo');
  api.setFileExplorerIndexedDirsForTest(['/repo/tools', '/repo/tools/src', '/repo/other']);
  assert.equal(api.fileExplorerDirectoryIsIndexed('/repo/tools'), true, 'Finder indexed directories are tracked by exact path');
  assert.equal(api.fileExplorerDirectoryIsIndexed('/repo/tools/src'), false, 'Finder compacts redundant child index marks under an indexed ancestor');
  assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/repo/workspace')), ['/repo/workspace', '/repo/other', '/repo/tools'], 'file quick-open adds indexed Finder directories and compacts nested search roots');
  assert.equal(api.fileQuickOpenScopeLabel('/repo/workspace'), '/repo/workspace + 2 indexed', 'file quick-open placeholder summarizes indexed search scope');
  api.setFileExplorerIndexedDirsForTest(['/home/test/dynamo']);
  assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test')), ['/home/test', '/home/test/dynamo'], 'an indexed child under the default root is still searched recursively');
  // #31: the Finder indexed badge reflects the cached build status (renders "indexing…" then a steady "indexed").
  api.setFileExplorerIndexStatusForTest('/home/test/dynamo', 'building');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), 'indexing…', '#31: a building index renders the "indexing…" badge');
  api.setFileExplorerIndexStatusForTest('/home/test/dynamo', 'ready');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), 'indexed', '#31: a ready index renders a steady "indexed" badge');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/not-indexed'), '', '#31: a non-indexed directory renders no badge');
  // #23: the topbar universal search renders a launcher button (opens the unified palette on click).
  const topbarSearch = api.createTopbarSearch();
  assert.ok(String(topbarSearch.className || '').includes('topbar-search'), '#23: topbar search renders a .topbar-search launcher');
  assert.equal(topbarSearch.type, 'button', '#23: the topbar search launcher is a button');
  assert.equal(topbarSearch.children.length, 3, '#23: the launcher renders an icon, a label, and a shortcut hint');
  assert.equal(api.tabMenuItems().some(item => item.type === 'search'), false, 'Tabs menu does not include its own search input');
  api.setTabsMenuSearchTextForTest('xy');
  assert.equal(api.tabSearchScore('1', 'xy') < 0, true, 'tab search uses fuzzy score and rejects non-matches');
  api.setTabsMenuSearchTextForTest('');
  assert.equal(helpMenu.items.find(item => item.label === 'Open README').disabled, false);
  assert.equal(api.tabMenuItems().map(item => item.label).filter(Boolean).includes('Current Tab'), false);
  const namedSessionApi = loadYolomux('', ['1', 'dynamo2']);
  const tmuxOnlySlots = namedSessionApi.emptyLayoutSlots();
  tmuxOnlySlots[namedSessionApi.layoutTreeKey] = namedSessionApi.leafNode('left');
  tmuxOnlySlots.left = namedSessionApi.paneStateWithTabs(['1', 'dynamo2'], '1');
  namedSessionApi.setLayoutSlotsForTest(tmuxOnlySlots);
  namedSessionApi.setTabsMenuSearchTextForTest('do2');
  const filteredNamedTabs = namedSessionApi.tabMenuItems().filter(item => item.type === 'command');
  assert.equal(filteredNamedTabs.length, 1);
  assert.ok(Number.isFinite(namedSessionApi.tabSearchScore('dynamo2', 'do2')), 'Tabs search matches the raw session name');
  namedSessionApi.setTabsMenuSearchTextForTest('');
  const tmuxTabActionLabels = namedSessionApi.tabMenuItems().map(item => item.label).filter(Boolean);
  assert.equal(tmuxTabActionLabels.includes('Current Tab'), false);
  assert.equal(tmuxTabActionLabels.includes('YOLO policy: enable'), false);
  namedSessionApi.activatePaneTab('left', 'dynamo2');
  assert.equal(namedSessionApi.currentSessionActionTarget(), 'dynamo2');
  assert.equal(namedSessionApi.appMenuTree().find(menu => menu.id === 'tmux').items.find(item => item.label === "Rename tmux session 'dynamo2'").disabled, false);
  assert.equal(namedSessionApi.appMenuTree().find(menu => menu.id === 'tmux').items[0].label, 'YO off');
  assert.equal(namedSessionApi.appMenuTree().find(menu => menu.id === 'tmux').items.some(item => item.label === "Enable YOLO for Tmux Session 'dynamo2'"), false);
  const sessionActions = api.tmuxSessionActionCommands('1');
  assert.deepStrictEqual(canonical(sessionActions.map(item => item.label)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Kill tmux session '1'"]);
  assert.equal(sessionActions.some(item => item.disabled), false);
  assert.equal(sessionActions.find(item => item.label === "Rename tmux session '1'").detail, '');
  const bodyChildCount = api.bodyChildren().length;
  api.showSessionContextMenu('1', 10, 10);
  const contextMenu = api.bodyChildren()[bodyChildCount];
  assert.deepStrictEqual(canonical(Array.from(contextMenu.children).map(child => child.textContent).filter(Boolean)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Transcript for session '1'", "AI Transcript for session '1'", "Event log for session '1'", "Kill tmux session '1'"]);
  assert.equal(contextMenu.children.some(child => child.className === 'terminal-context-menu-separator'), true);
  const contextButtons = Array.from(contextMenu.children).filter(child => child.textContent);
  assert.equal(contextButtons[contextButtons.length - 1].classList.contains('danger'), true, 'Kill is styled as the final destructive action');
  const sessionViews = api.tmuxSessionViewCommands('1');
  assert.deepStrictEqual(canonical(sessionViews.map(item => item.label)), ["Transcript for session '1'", "AI Transcript for session '1'", "Event log for session '1'", 'Pane details']);
  assert.equal(api.fileIconFor('screenshot.png'), '🖼');
  assert.equal(api.fileIconFor('run.sh'), '🐚');
  assert.equal(api.fileIconFor('main.rs'), '🧩');
  assert.equal(api.fileIconFor('config.yaml'), '⚙');
  assert.equal(api.fileIconFor('README'), '📝');
  assert.equal(api.fileIconFor('Dockerfile'), '⚙');
  assert.equal(api.fileIconFor('archive.tar'), '🗜');
  assert.equal(api.fileIconFor('unknown.bin'), '📄');
  assert.equal(api.fileIconClassFor('README.md'), 'file-icon-doc');
  assert.equal(api.fileIconClassFor('main.rs'), 'file-icon-code');
  assert.equal(api.fileIconClassFor('screenshot.png'), 'file-icon-image');
  const controlsHtml = api.panelControlsHtml('1');
  assert.equal(controlsHtml.includes('data-panel-tab-overflow'), false);
  assert.equal((controlsHtml.match(/pane-actions-dots/g) || []).length, 1, 'pane header has one merged ellipsis menu');
  assert.ok(controlsHtml.includes('title="YO!info" aria-label="YO!info"'));
  assert.ok(api.tmuxPaneTabHtml('1', null, {key: 'blocked', short: 'BLK', label: 'Blocked', reason: 'test'}).includes('tab-symbol'));
  assert.equal(api.tmuxSessionNameError('good_name-1.2'), '');
  assert.equal(api.tmuxSessionNameError('dynamo 2'), '');
  assert.equal(api.tmuxSessionNameError('bad/name').includes('letters'), true);
  assert.ok(api.panelControlsHtml('1').includes('data-pane-actions="1"'));
  assert.equal(api.panelControlsHtml('__files__').includes('data-pane-actions'), false);
  const readonlyApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly');
  assert.equal(readonlyApi.tmuxSessionActionCommands('1').every(item => item.disabled), true);
  readonlyApi.setAutoApproveStateForTest('1', {enabled: true});
  assert.equal(readonlyApi.menuTabCommand('1', {toggleYolo: true}).html.includes('data-auto-session'), false);
  api.setTranscriptInfoForTest('1', {
    project: {git: {cwd: '/home/test/yolomux.dev', root: '/home/test/yolomux.dev'}},
    panes: [{current_path: '/home/test/yolomux.dev/mock', command: 'bash'}],
    selected_pane: {current_path: '/home/test/yolomux.dev'},
  });
  assert.equal(api.fileExplorerRootModeValue(), 'fixed');
  assert.equal(api.activeTmuxDirectoryPath('1'), '/home/test/yolomux.dev');
  assert.equal(api.fileExplorerRootForOpen('1'), '/home/test');
  api.setFileExplorerRootMode('sync', {sync: false});
  assert.equal(api.fileExplorerRootModeValue(), 'sync');
  assert.equal(api.fileExplorerRootForOpen('1'), '/home/test/yolomux.dev');
  assert.equal(api.finderDirectoryForItem('1'), '');
  assert.equal(api.activeFinderDirectoryPath('1'), '');
  assert.equal(api.finderTargetPathForItem('1'), '');
  assert.equal(api.activeFinderTargetPath('1'), '');
  const fileItem = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/TODO.md');
  assert.equal(api.fileExplorerRootForOpen(fileItem), '/home/test');
  api.setFileExplorerRootMode('fixed', {sync: false});
  assert.equal(api.finderDirectoryForItem(fileItem), '/home/test/yolomux.dev');
  assert.equal(api.finderTargetPathForItem(fileItem), '/home/test/yolomux.dev/TODO.md');
  assert.equal(api.activeFinderTargetPath(fileItem), '/home/test/yolomux.dev/TODO.md');
  assert.equal(api.pathIsInsideDirectory('/home/test/yolomux.dev/mock', '/home/test'), true);
  assert.equal(api.pathIsInsideDirectory('/home/test2/yolomux.dev/mock', '/home/test'), false);
  assert.deepStrictEqual(canonical(api.childPathParts('/home/test', '/home/test/yolomux.dev/mock')), ['yolomux.dev', 'mock']);
  assert.deepStrictEqual(canonical(api.childPathParts('/home/test/yolomux.dev', '/home/test/yolomux.dev/TODO.md')), ['TODO.md']);
  const scrollContainer = {
    clientHeight: 100,
    isConnected: true,
    scrollTop: 0,
    getBoundingClientRect() {
      return {top: 0, bottom: 100, height: 100};
    },
  };
  const targetRow = {
    isConnected: true,
    getBoundingClientRect() {
      return {top: 420, bottom: 440, height: 20};
    },
  };
  assert.equal(api.scrollFileTreeRowIntoView(scrollContainer, targetRow), true);
  assert.equal(scrollContainer.scrollTop, 380);
  const visibleRow = {
    isConnected: true,
    getBoundingClientRect() {
      return {top: 40, bottom: 60, height: 20};
    },
  };
  assert.equal(api.scrollFileTreeRowIntoView(scrollContainer, visibleRow), true);
  assert.equal(scrollContainer.scrollTop, 380);

  const {tree, rows} = makeFileTree(['/repo/a.md', '/repo/b.md', '/repo/c.md', '/repo/d.md']);
  tree.scrollTop = 120;
  api.selectFileTreePath('/repo/a.md');
  api.updateFileTreeSelectionFromClick(rows[2], '/repo/c.md', {shiftKey: true, metaKey: false, ctrlKey: false});
  assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()), {
    paths: ['/repo/a.md', '/repo/b.md', '/repo/c.md'],
    anchor: '/repo/a.md',
    manual: true,
  });
  assert.equal(tree.scrollTop, 120);

  const fallbackTree = makeFileTree(['/repo/a.md', '/repo/b.md', '/repo/c.md']);
  api.setFileExplorerSelectionForTest(['/repo/b.md'], '/outside/old.md');
  api.updateFileTreeSelectionFromClick(fallbackTree.rows[2], '/repo/c.md', {shiftKey: true, metaKey: false, ctrlKey: false});
  assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()), {
    paths: ['/repo/b.md', '/repo/c.md'],
    anchor: '/repo/b.md',
    manual: true,
  });

  api.setFileExplorerSelectionForTest(['/old/a.md', '/repo/a.md'], '/old/a.md');
  api.pruneFileExplorerSelectionForRoot('/repo');
  assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()), {
    paths: ['/repo/a.md'],
    anchor: null,
    manual: false,
  });

  const clickOnlyTree = makeFileTree(['/repo/open-me.md']);
  api.onFileTreeRowClick(clickOnlyTree.rows[0], '/repo/open-me.md', {kind: 'file', name: 'open-me.md'}, {shiftKey: false, metaKey: false, ctrlKey: false});
  assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()).paths, ['/repo/open-me.md']);
  assert.equal(api.activeFileForTest(), null);

  const refreshTree = new TestElement('refresh-tree');
  refreshTree.setAttribute('role', 'tree');
  refreshTree.classList.add('file-explorer-tree-panel');
  api.setFileExplorerExpandedForTest(['/repo/src']);
  const rootEntries = [{name: 'src', kind: 'dir'}, {name: 'README.md', kind: 'file'}];
  api.renderTreeChildrenForTest(refreshTree, '/repo', rootEntries, 0, [
    ['/repo/src', [{name: 'a.py', kind: 'file'}, {name: 'b.py', kind: 'file'}]],
  ]);
  const srcRow = refreshTree.children[0];
  const srcChildren = refreshTree.children[1];
  const firstFileRow = srcChildren.children[0];
  refreshTree.scrollTop = 44;
  srcChildren.scrollTop = 12;
  api.renderTreeChildrenForTest(refreshTree, '/repo', rootEntries, 0, [
    ['/repo/src', [{name: 'a.py', kind: 'file'}, {name: 'c.py', kind: 'file'}]],
  ]);
  assert.equal(refreshTree.children[0], srcRow);
  assert.equal(refreshTree.children[1], srcChildren);
  assert.equal(srcChildren.children[0], firstFileRow);
  assert.equal(srcChildren.children.length, 2);
  assert.equal(srcChildren.children[1].dataset.path, '/repo/src/c.py');
  assert.equal(refreshTree.scrollTop, 44);
  assert.equal(srcChildren.scrollTop, 12);

  api.setFileExplorerSessionFilesPayloadForTest({
    loaded: true,
    repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 5, removed: 3}],
    files: [
      {abs_path: '/repo/README.md', status: 'M', added: 5, removed: 3},
      {abs_path: '/repo/app/a.py', repo: '/repo/app', status: 'M', added: 5, removed: 3},
    ],
  });
  const gitTree = new TestElement('git-tree');
  gitTree.setAttribute('role', 'tree');
  gitTree.classList.add('file-explorer-tree-panel');
  api.renderTreeChildrenForTest(gitTree, '/repo', [
    {name: 'app', kind: 'dir', is_repo: true, repo: {root: '/repo/app', name: 'app', branch: 'feature/x'}},
    {name: 'README.md', kind: 'file'},
  ]);
  const repoName = gitTree.children[0].querySelector(':scope > .file-tree-name');
  assert.equal(repoName.textContent, 'app [feature/x +5/-3]', 'repo rows show cached branch and aggregate numstat inline');
  assert.ok(repoName.innerHTML.includes('file-tree-repo-branch'), 'repo row branch is wrapped for monospace styling');
  assert.equal(gitTree.children[0].classList.contains('repo-non-main'), true, 'non-main repo rows stand out');
  assert.equal(gitTree.children[1].querySelector(':scope > .file-tree-name').textContent, 'README.md (+5/-3)', 'changed file rows show numstat inline');
  assert.equal(gitTree.children[1].querySelector(':scope > .file-tree-git-status').textContent, 'M');

  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.leafNode('slot2');
  slots.slot2 = api.paneStateWithTabs(['__files__'], '__files__');
  const next = api.layoutWithItems(slots, ['1']);
  assert.deepStrictEqual(canonical(api.serialize(next).panes), {
    slot2: {tabs: ['__files__'], active: '__files__'},
    slot1: {tabs: ['1'], active: '1'},
  });
  assert.equal(next[api.layoutTreeKey].split, 'row');
  assert.equal(next[api.layoutTreeKey].pct, 22);

  const placeholderSlots = api.emptyLayoutSlots();
  placeholderSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  placeholderSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  placeholderSlots.slot1 = api.emptyPlaceholderPaneState();
  api.setLayoutSlotsForTest(placeholderSlots);
  assert.equal(api.firstEmptyPane(), 'slot1');
  assert.equal(api.slotForTabActivation('1'), 'slot1');
  const filled = api.layoutWithItems(placeholderSlots, ['1']);
  assert.deepStrictEqual(canonical(api.serialize(filled).panes), {
    left: {tabs: ['__files__'], active: '__files__'},
    slot1: {tabs: ['1'], active: '1'},
  });
  const placeholderUrl = api.setLayoutSlotsForTest(placeholderSlots);
  const placeholderParams = parseUrl(placeholderUrl);
  assert.equal(placeholderParams.get('layout'), 'row@22(left,slot1)');
  assert.equal(placeholderParams.get('tabs'), 'left:files;slot1:__empty_pane__');
  const reloadedPlaceholder = loadYolomux(`?${placeholderUrl.split('?')[1] || ''}`, ['1']);
  assert.deepStrictEqual(canonical(reloadedPlaceholder.serialize(reloadedPlaceholder.currentSlots())), canonical(api.serialize(placeholderSlots)));

  const finderAndTmux = api.emptyLayoutSlots();
  finderAndTmux[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  finderAndTmux.left = api.paneStateWithTabs(['__files__'], '__files__');
  finderAndTmux.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(finderAndTmux);
  assert.equal(api.slotForNewTmuxSession('2'), 'slot1');
  assert.equal(api.slotForTabActivation('2'), 'slot1');
  api.removePaneFromLayout('1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
    left: {tabs: ['__files__'], active: '__files__'},
    slot1: {tabs: [], active: null, placeholder: true},
  });
  const normalSplit = api.emptyLayoutSlots();
  normalSplit[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  normalSplit.left = api.paneStateWithTabs(['1'], '1');
  const extraPaneItem = api.registerFileEditorLayoutItem('/home/test/a.md');
  normalSplit.slot1 = api.paneStateWithTabs(['2', extraPaneItem], '2');
  api.setLayoutSlotsForTest(normalSplit);
  api.removePaneFromLayout('2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
    left: {tabs: ['1'], active: '1'},
  });

  const tmuxAndFinder = api.emptyLayoutSlots();
  tmuxAndFinder[api.layoutTreeKey] = api.splitNode('row', api.leafNode('slot1'), api.leafNode('left'), 78);
  tmuxAndFinder.slot1 = api.paneStateWithTabs(['1'], '1');
  tmuxAndFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
  api.setLayoutSlotsForTest(tmuxAndFinder);
  api.removeSessionFromLayout('1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
    slot1: {tabs: [], active: null, placeholder: true},
    left: {tabs: ['__files__'], active: '__files__'},
  });

  const tmuxAboveFinder = api.emptyLayoutSlots();
  tmuxAboveFinder[api.layoutTreeKey] = api.splitNode('column', api.leafNode('slot1'), api.leafNode('left'), 48);
  tmuxAboveFinder.slot1 = api.paneStateWithTabs(['1'], '1');
  tmuxAboveFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
  api.setLayoutSlotsForTest(tmuxAboveFinder);
  api.removeSessionFromLayout('1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['__files__'], active: '__files__'}},
  });

  const finderAboveTmux = api.emptyLayoutSlots();
  finderAboveTmux[api.layoutTreeKey] = api.splitNode('column', api.leafNode('left'), api.leafNode('slot1'), 52);
  finderAboveTmux.left = api.paneStateWithTabs(['__files__'], '__files__');
  finderAboveTmux.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(finderAboveTmux);
  api.removePaneFromLayout('1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['__files__'], active: '__files__'}},
  });

  const activationSlots = api.emptyLayoutSlots();
  activationSlots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
    api.leafNode('slot1'),
    28,
  );
  activationSlots.slot2 = api.emptyPlaceholderPaneState();
  activationSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  activationSlots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(activationSlots);
  assert.equal(api.slotForTabActivation('2'), 'slot1');

  const middleFinderSlots = api.emptyLayoutSlots();
  middleFinderSlots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.leafNode('left'),
    api.splitNode('row', api.leafNode('slot2'), api.leafNode('slot1'), 22),
    35,
  );
  middleFinderSlots.left = api.paneStateWithTabs(['1'], '1');
  middleFinderSlots.slot2 = api.paneStateWithTabs(['__files__'], '__files__');
  middleFinderSlots.slot1 = api.paneStateWithTabs(['2'], '2');
  assert.equal(api.fileExplorerNeedsLeftDock(middleFinderSlots), true);
  const dockedFinder = api.normalizeLayoutSlots(middleFinderSlots);
  assert.deepStrictEqual(canonical(api.serialize(dockedFinder)), {
    tree: {
      split: 'row',
      pct: 22,
      children: [
        {slot: 'slot2'},
        {split: 'row', pct: 35, children: [{slot: 'left'}, {slot: 'slot1'}]},
      ],
    },
    panes: {
      slot2: {tabs: ['__files__'], active: '__files__'},
      left: {tabs: ['1'], active: '1'},
      slot1: {tabs: ['2'], active: '2'},
    },
  });
  api.setLayoutSlotsForTest(dockedFinder);
  assert.equal(api.fileExplorerNeedsLeftDock(), false);

  const verticalFinderBranch = api.emptyLayoutSlots();
  verticalFinderBranch[api.layoutTreeKey] = api.splitNode(
    'row',
    api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
    api.leafNode('slot1'),
    22,
  );
  verticalFinderBranch.slot2 = api.paneStateWithTabs(['__info__'], '__info__');
  verticalFinderBranch.left = api.paneStateWithTabs(['__files__'], '__files__');
  verticalFinderBranch.slot1 = api.paneStateWithTabs(['1'], '1');
  assert.equal(api.fileExplorerNeedsLeftDock(verticalFinderBranch), false);
  api.setLayoutSlotsForTest(verticalFinderBranch);
  assert.equal(api.fileExplorerNeedsLeftDock(), false);

  const noFinderSplit = api.emptyLayoutSlots();
  noFinderSplit[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  noFinderSplit.left = api.paneStateWithTabs(['1'], '1');
  noFinderSplit.slot1 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(noFinderSplit);
  assert.deepStrictEqual(canonical(api.serialize(api.layoutWithFileExplorerDockedLeft())), {
    tree: {
      split: 'row',
      pct: 22,
      children: [
        {slot: 'slot2'},
        {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'slot1'}]},
      ],
    },
    panes: {
      slot2: {tabs: ['__files__'], active: '__files__'},
      left: {tabs: ['1'], active: '1'},
      slot1: {tabs: ['2'], active: '2'},
    },
  });

  const expandedNormal = api.emptyLayoutSlots();
  expandedNormal[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  expandedNormal.left = api.paneStateWithTabs(['1'], '1');
  expandedNormal.slot1 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(expandedNormal);
  api.expandPaneFromLayout('2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'slot1'},
    panes: {slot1: {tabs: ['2', '1'], active: '2'}},
  });

  const expandedBesideFinder = api.emptyLayoutSlots();
  expandedBesideFinder[api.layoutTreeKey] = api.splitNode(
    'row',
    api.leafNode('left'),
    api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 50),
    22,
  );
  expandedBesideFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
  expandedBesideFinder.slot1 = api.paneStateWithTabs(['1'], '1');
  expandedBesideFinder.slot2 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(expandedBesideFinder);
  api.expandPaneFromLayout('2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot2: {tabs: ['2', '1'], active: '2'},
    },
  });

  const layoutCommands = api.emptyLayoutSlots();
  layoutCommands[api.layoutTreeKey] = api.splitNode(
    'row',
    api.leafNode('left'),
    api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50),
    22,
  );
  layoutCommands.left = api.paneStateWithTabs(['__files__'], '__files__');
  layoutCommands.slot1 = api.paneStateWithTabs(['1'], '1');
  layoutCommands.slot2 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(layoutCommands);
  api.setFocusedPanelItem('2');
  api.setLayoutToSinglePane();
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: ['1', '2'], active: '2'},
    },
  });
  api.setLayoutToSplitPanes();
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {split: 'row', pct: 50, children: [{slot: 'right'}, {slot: 'slot2'}]}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      right: {tabs: ['1'], active: '1'},
      slot2: {tabs: ['2'], active: '2'},
    },
  });

  const singlePaneBesideFinder = api.emptyLayoutSlots();
  singlePaneBesideFinder[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  singlePaneBesideFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
  singlePaneBesideFinder.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(singlePaneBesideFinder);
  assert.equal(api.canPaneExpand('1'), false);
  assert.ok(api.panelControlsHtml('1').includes('data-pane-expand'));
  assert.ok(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));
  api.expandPaneFromLayout('1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: ['1'], active: '1'},
    },
  });

  const placeholderBesideSinglePane = api.emptyLayoutSlots();
  placeholderBesideSinglePane[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 40);
  placeholderBesideSinglePane.left = api.paneStateWithTabs(['1'], '1');
  placeholderBesideSinglePane.slot1 = api.emptyPlaceholderPaneState();
  api.setLayoutSlotsForTest(placeholderBesideSinglePane);
  assert.equal(api.canPaneExpand('1'), false);
  assert.ok(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));
  api.expandPaneFromLayout('1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {
      left: {tabs: ['1'], active: '1'},
    },
  });

  const minimizedNormal = api.emptyLayoutSlots();
  minimizedNormal[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  minimizedNormal.left = api.paneStateWithTabs(['1'], '1');
  minimizedNormal.slot1 = api.paneStateWithTabs(['2', extraPaneItem], '2');
  api.setLayoutSlotsForTest(minimizedNormal);
  api.minimizePaneFromLayout('2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['1', '2', extraPaneItem], active: '1'}},
  });

  const finderOnly = api.emptyLayoutSlots();
  finderOnly[api.layoutTreeKey] = api.leafNode('left');
  finderOnly.left = api.paneStateWithTabs(['__files__'], '__files__');
  api.setLayoutSlotsForTest(finderOnly);
  assert.equal(api.slotForNewTmuxSession('2'), 'left');

  const dragSlots = api.emptyLayoutSlots();
  dragSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  dragSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  dragSlots.slot1 = api.paneStateWithTabs(['__info__'], '__info__');
  api.setLayoutSlotsForTest(dragSlots);
  const removed = api.layoutWithoutItem('__info__', {preserveEmptySlot: 'slot1'});
  assert.deepStrictEqual(canonical(api.serialize(removed).panes.slot1), {tabs: [], active: null, placeholder: true});
  const closed = api.normalizeLayoutSlots(api.layoutWithoutItem('__info__', {preserveRemovedSlot: true}));
  assert.deepStrictEqual(canonical(api.serialize(closed).panes), {
    left: {tabs: ['__files__'], active: '__files__'},
    slot1: {tabs: [], active: null, placeholder: true},
  });
  const moved = api.normalizeLayoutSlots(api.layoutWithoutItem('__info__'));
  assert.deepStrictEqual(canonical(api.serialize(moved).panes), {
    left: {tabs: ['__files__'], active: '__files__'},
  });
  api.splitSessionAtSlot('__info__', 'left', 'top', 'slot1');
  const split = api.serialize(api.currentSlots());
  assert.deepStrictEqual(canonical(Object.values(split.panes).filter(pane => pane.tabs.includes('__files__'))), [{tabs: ['__files__'], active: '__files__'}]);
  assert.equal(split.panes.slot1, undefined);
  assert.deepStrictEqual(canonical(Object.values(split.panes).filter(pane => pane.tabs.includes('__info__'))), [{tabs: ['__info__'], active: '__info__'}]);
  const infoSlot = Object.entries(split.panes).find(([, pane]) => pane.tabs.includes('__info__'))[0];
  assert.notEqual(infoSlot, 'slot1');
  assert.equal(api.shouldPreserveSourceSlotForSplit(infoSlot, 'slot1'), false);

  const finderCloseSlots = api.emptyLayoutSlots();
  finderCloseSlots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
    api.leafNode('slot1'),
    22,
  );
  finderCloseSlots.slot2 = api.paneStateWithTabs(['__info__'], '__info__');
  finderCloseSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  finderCloseSlots.slot1 = api.emptyPlaceholderPaneState();
  api.setLayoutSlotsForTest(finderCloseSlots);
  api.removeSessionFromLayout('__files__');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'slot2'},
    panes: {slot2: {tabs: ['__info__'], active: '__info__'}},
  });

  const autoPruneSlots = api.emptyLayoutSlots();
  autoPruneSlots[api.layoutTreeKey] = api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50);
  autoPruneSlots.slot2 = api.paneStateWithTabs(['1'], '1');
  autoPruneSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  const topColumn = new TestElement('top-column');
  topColumn.dataset.slot = 'slot2';
  topColumn.rect = {left: 0, top: 0, right: 500, bottom: 120, width: 500, height: 120};
  const finderColumn = new TestElement('finder-column');
  finderColumn.dataset.slot = 'left';
  finderColumn.rect = {left: 0, top: 126, right: 500, bottom: 250, width: 500, height: 124};
  api.setLayoutSlotsForTest(autoPruneSlots);
  api.setGridPreviewNodesForTest([topColumn, finderColumn]);
  assert.equal(api.smallLayoutSlotCandidate().slot, 'slot2');

  topColumn.rect = {left: 0, top: 0, right: 500, bottom: 260, width: 500, height: 260};
  api.setGridPreviewNodesForTest([topColumn, finderColumn]);
  assert.equal(api.smallLayoutSlotCandidate(), null);

  const allEmpty = api.emptyLayoutSlots();
  allEmpty[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 40);
  allEmpty.left = api.emptyPlaceholderPaneState();
  allEmpty.slot1 = api.emptyPlaceholderPaneState();
  assert.deepStrictEqual(canonical(api.serialize(api.normalizeLayoutSlots(allEmpty))), {
    tree: {slot: 'left'},
    panes: {left: {tabs: [], active: null, placeholder: true}},
  });

  const killedApi = loadYolomux('', ['2']);
  const killedSlots = killedApi.emptyLayoutSlots();
  killedSlots[killedApi.layoutTreeKey] = killedApi.splitNode('row', killedApi.leafNode('left'), killedApi.leafNode('slot1'), 22);
  killedSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
  killedSlots.slot1 = {tabs: ['1'], active: '1'};
  const killedNormalized = killedApi.normalizeLayoutSlots(killedSlots, {
    preserveRemovedItems: ['1'],
    preserveRemovedSlots: true,
  });
  assert.deepStrictEqual(canonical(killedApi.serialize(killedNormalized).panes), {
    left: {tabs: ['__files__'], active: '__files__'},
    slot1: {tabs: [], active: null, placeholder: true},
  });
  const killedNestedFinderSlots = killedApi.emptyLayoutSlots();
  killedNestedFinderSlots[killedApi.layoutTreeKey] = killedApi.splitNode(
    'row',
    killedApi.leafNode('slot1'),
    killedApi.splitNode('row', killedApi.leafNode('left'), killedApi.leafNode('slot2'), 22),
    58,
  );
  killedNestedFinderSlots.slot1 = {tabs: ['1'], active: '1'};
  killedNestedFinderSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
  killedNestedFinderSlots.slot2 = killedApi.paneStateWithTabs(['2'], '2');
  const killedNestedFinderNormalized = killedApi.normalizeLayoutSlots(killedNestedFinderSlots, {
    preserveRemovedItems: ['1'],
    preserveRemovedSlots: true,
  });
  assert.deepStrictEqual(canonical(killedApi.serialize(killedNestedFinderNormalized)), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot2: {tabs: ['2'], active: '2'},
    },
  });
  const staleFileOpenPath = '/home/test/AGENTS.md';
  const staleFileOpenItem = killedApi.registerFileEditorLayoutItem(staleFileOpenPath);
  const staleFileOpenSlots = killedApi.emptyLayoutSlots();
  staleFileOpenSlots[killedApi.layoutTreeKey] = killedApi.splitNode(
    'row',
    killedApi.leafNode('slot1'),
    killedApi.splitNode('row', killedApi.leafNode('left'), killedApi.leafNode('slot2'), 22),
    58,
  );
  staleFileOpenSlots.slot1 = killedApi.emptyPlaceholderPaneState();
  staleFileOpenSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
  staleFileOpenSlots.slot2 = killedApi.paneStateWithTabs([staleFileOpenItem], staleFileOpenItem);
  killedApi.setLayoutSlotsForTest(staleFileOpenSlots);
  killedApi.openFileEditorPane(staleFileOpenPath);
  assert.deepStrictEqual(canonical(killedApi.serialize(killedApi.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot2: {tabs: [staleFileOpenItem], active: staleFileOpenItem},
    },
  });

  const killedVerticalSlots = killedApi.emptyLayoutSlots();
  killedVerticalSlots[killedApi.layoutTreeKey] = killedApi.splitNode('column', killedApi.leafNode('slot1'), killedApi.leafNode('left'), 50);
  killedVerticalSlots.slot1 = {tabs: ['1'], active: '1'};
  killedVerticalSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
  const killedVerticalNormalized = killedApi.normalizeLayoutSlots(killedVerticalSlots, {
    preserveRemovedItems: ['1'],
    preserveRemovedSlots: true,
  });
  assert.deepStrictEqual(canonical(killedApi.serialize(killedVerticalNormalized)), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['__files__'], active: '__files__'}},
  });

  api.setLayoutSlotsForTest(finderOnly);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'middle'}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'left'}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'right'}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'top'}), true);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom'}), true);
  const editorItem = api.registerFileEditorLayoutItem('/home/test/AGENTS.md');
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'middle'}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'left'}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'right'}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'top'}), true);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'bottom'}), true);
  const changesOnly = api.emptyLayoutSlots();
  changesOnly[api.layoutTreeKey] = api.leafNode('left');
  changesOnly.left = api.paneStateWithTabs(['__changes__'], '__changes__');
  api.setLayoutSlotsForTest(changesOnly);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'middle'}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'left'}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'right'}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'top'}), true);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'bottom'}), true);
  api.setLayoutSlotsForTest(finderOnly);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'top', targetRect: {width: 300}}), false);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: {width: 300}}), false);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'top', targetRect: {width: 520}}), true);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: {width: 520}}), true);
  api.splitSessionAtSlot(editorItem, 'left', 'bottom');
  const editorSplit = api.serialize(api.currentSlots());
  assert.deepStrictEqual(canonical(Object.values(editorSplit.panes).filter(pane => pane.tabs.includes('__files__'))), [{tabs: ['__files__'], active: '__files__'}]);
  assert.deepStrictEqual(canonical(Object.values(editorSplit.panes).filter(pane => pane.tabs.includes(editorItem))), [{tabs: [editorItem], active: editorItem}]);
  assert.ok(JSON.stringify(editorSplit.tree).includes('"split":"column"'));

  const fullSpanA = api.registerFileEditorLayoutItem('/home/test/full-span-a.md');
  const fullSpanB = api.registerFileEditorLayoutItem('/home/test/full-span-b.md');
  const fullSpanC = api.registerFileEditorLayoutItem('/home/test/full-span-c.md');
  const fullSpanD = api.registerFileEditorLayoutItem('/home/test/full-span-d.md');
  const fullSpanSlots = api.emptyLayoutSlots();
  fullSpanSlots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 50),
    api.splitNode('column', api.leafNode('slot3'), api.leafNode('slot4'), 50),
    50,
  );
  fullSpanSlots.slot1 = api.paneStateWithTabs([fullSpanA], fullSpanA);
  fullSpanSlots.slot2 = api.paneStateWithTabs([fullSpanB], fullSpanB);
  fullSpanSlots.slot3 = api.paneStateWithTabs([fullSpanC], fullSpanC);
  fullSpanSlots.slot4 = api.paneStateWithTabs([fullSpanD], fullSpanD);
  api.setLayoutSlotsForTest(fullSpanSlots);
  api.splitSessionAtLayoutBoundary('__info__', 'right');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {
      split: 'row',
      pct: 50,
      children: [
        {
          split: 'row',
          pct: 50,
          children: [
            {split: 'column', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]},
            {split: 'column', pct: 50, children: [{slot: 'slot3'}, {slot: 'slot4'}]},
          ],
        },
        {slot: 'slot5'},
      ],
    },
    panes: {
      slot1: {tabs: [fullSpanA], active: fullSpanA},
      slot2: {tabs: [fullSpanB], active: fullSpanB},
      slot3: {tabs: [fullSpanC], active: fullSpanC},
      slot4: {tabs: [fullSpanD], active: fullSpanD},
      slot5: {tabs: ['__info__'], active: '__info__'},
    },
  });

  api.setLayoutSlotsForTest(fullSpanSlots);
  api.splitSessionAtGutter('__yoagent__', '', 'right');
  const gutterSplit = api.serialize(api.currentSlots());
  assert.equal(gutterSplit.tree.split, 'row');
  assert.deepStrictEqual(canonical(gutterSplit.tree.children[0]), {
    split: 'column',
    pct: 50,
    children: [{slot: 'slot1'}, {slot: 'slot2'}],
  });
  assert.deepStrictEqual(canonical(gutterSplit.tree.children[1]), {
    split: 'row',
    pct: 50,
    children: [
      {slot: 'slot5'},
      {split: 'column', pct: 50, children: [{slot: 'slot3'}, {slot: 'slot4'}]},
    ],
  });
  assert.deepStrictEqual(canonical(gutterSplit.panes.slot5), {tabs: ['__yoagent__'], active: '__yoagent__'});

  const dockedBoundarySlots = api.emptyLayoutSlots();
  dockedBoundarySlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  dockedBoundarySlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  dockedBoundarySlots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(dockedBoundarySlots);
  api.splitSessionAtLayoutBoundary('__info__', 'bottom');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {
      split: 'row',
      pct: 22,
      children: [
        {slot: 'left'},
        {split: 'column', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]},
      ],
    },
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: ['1'], active: '1'},
      slot2: {tabs: ['__info__'], active: '__info__'},
    },
  });

  const dockedBoundaryTopSlots = api.emptyLayoutSlots();
  dockedBoundaryTopSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  dockedBoundaryTopSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  dockedBoundaryTopSlots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(dockedBoundaryTopSlots);
  api.splitSessionAtLayoutBoundary('__yoagent__', 'top');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).tree), {
    split: 'row',
    pct: 22,
    children: [
      {slot: 'left'},
      {split: 'column', pct: 50, children: [{slot: 'slot2'}, {slot: 'slot1'}]},
    ],
  });

  api.setLayoutSlotsForTest(dockedBoundarySlots);
  api.setLayoutColumnRectsForTest({
    left: {left: 0, top: 0, right: 240, bottom: 800, width: 240, height: 800},
    slot1: {left: 240, top: 0, right: 1200, bottom: 800, width: 960, height: 800},
  });
  api.showDropPreview({boundary: 'root', zone: 'bottom', targetSlot: 'slot1', previewNode: api.gridForTest(), targetRect: {left: 0, top: 0, right: 1200, bottom: 800, width: 1200, height: 800}});
  assert.equal(api.gridForTest().style.getPropertyValue('--drop-preview-left'), '246px', 'bottom full-span preview starts after the docked Finder');
  assert.equal(api.gridForTest().style.getPropertyValue('--drop-preview-width'), '948px', 'bottom full-span preview spans only the non-Finder content');
  api.clearDropPreview();

  const dockedBoundaryMoveOnlyContent = api.emptyLayoutSlots();
  dockedBoundaryMoveOnlyContent[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  dockedBoundaryMoveOnlyContent.left = api.paneStateWithTabs(['__files__'], '__files__');
  dockedBoundaryMoveOnlyContent.slot1 = api.paneStateWithTabs(['__info__'], '__info__');
  api.setLayoutSlotsForTest(dockedBoundaryMoveOnlyContent);
  api.splitSessionAtLayoutBoundary('__info__', 'bottom', 'slot1');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot2: {tabs: ['__info__'], active: '__info__'},
    },
  });

  api.setLayoutSlotsForTest(fullSpanSlots);
  const boundarySlot = new TestElement('boundary-slot');
  boundarySlot.classList.add('drop-slot');
  boundarySlot.dataset.slot = 'slot4';
  boundarySlot.rect = {left: 620, top: 0, right: 1200, bottom: 800, width: 580, height: 800};
  const boundaryEvent = dragEvent(1192, '__info__');
  boundaryEvent.target = boundarySlot;
  boundaryEvent.clientY = 400;
  const boundaryIntent = api.dropIntentForEvent(boundaryEvent, {allowBoundary: true});
  assert.equal(boundaryIntent.boundary, 'root');
  assert.equal(boundaryIntent.zone, 'right');
  api.showDropPreview(boundaryIntent);
  assert.ok(api.gridForTest().classList.contains('drop-preview-root'), 'outer-edge session drags show a root full-span preview');
  assert.equal(api.gridForTest().dataset.dropLabel, 'full right');
  api.clearDropPreview();
  assert.equal(api.gridForTest().classList.contains('drop-preview-root'), false);
  assert.equal('dropLabel' in api.gridForTest().dataset, false);

  const resizer = new TestElement('root-resizer');
  resizer.classList.add('layout-resizer');
  resizer.dataset.splitPath = '';
  resizer.rect = {left: 598, top: 0, right: 602, bottom: 800, width: 4, height: 800};
  const gutterEvent = dragEvent(601, '__yoagent__');
  gutterEvent.target = resizer;
  gutterEvent.clientY = 400;
  const gutterIntent = api.dropIntentForEvent(gutterEvent, {allowBoundary: true});
  assert.equal(gutterIntent.boundary, 'gutter');
  assert.equal(gutterIntent.zone, 'right');
  assert.equal(gutterIntent.splitPath, '');
  api.showDropPreview(gutterIntent);
  assert.ok(api.gridForTest().classList.contains('drop-preview-gutter'), 'split-bar session drags show a full-span gutter preview');
  assert.equal(api.gridForTest().dataset.dropLabel, 'full span');
  assert.ok(api.gridForTest().style.getPropertyValue('--drop-preview-width'), 'gutter preview geometry is explicit');
  api.clearDropPreview();

  api.setLayoutSlotsForTest(finderOnly);
  const finderStrip = tabStrip([tabElement('__files__', 100, 120)]);
  api.bindPaneTabStrip(finderStrip, 'left');
  const event = dragEvent(125, '1');
  finderStrip.ondragover(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
  assert.equal(event.dataTransfer.dropEffect, 'none');
  assert.equal(finderStrip.classList.contains('tab-drop-preview'), false);
  const changesStrip = tabStrip([tabElement('__changes__', 100, 120)]);
  api.bindPaneTabStrip(changesStrip, 'left');
  api.setLayoutSlotsForTest(changesOnly);
  const changesEvent = dragEvent(125, editorItem);
  changesStrip.ondragover(changesEvent);
  assert.equal(changesEvent.defaultPrevented, true);
  assert.equal(changesEvent.propagationStopped, true);
  assert.equal(changesEvent.dataTransfer.dropEffect, 'none');
  assert.equal(changesStrip.classList.contains('tab-drop-preview'), false);
}

{
  const api = loadYolomux('', ['1']);
  const path = '/repo/app/common.py';
  const normalRows = api.filePopoverRows(path, {kind: 'text', size: 42}).join('');
  assert.equal((normalRows.match(/popover-copy-value/g) || []).length, 1);
  assert.equal(normalRows.includes('popover-subtitle'), false);
  assert.ok(normalRows.includes('file editor'));
  assert.equal(normalRows.includes('status'), false);
  const dirtyRows = api.filePopoverRows(path, {kind: 'text', dirty: true}).join('');
  assert.ok(dirtyRows.includes('status'));
  assert.ok(dirtyRows.includes('modified'));
}

{
  const api = loadYolomux('', ['1']);
  const signature = api.directoryEntriesSignature([
    {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
    {name: 'a.txt', kind: 'file', size: 1, mtime: 10},
  ]);
  assert.equal(signature, api.directoryEntriesSignature([
    {name: 'a.txt', kind: 'file', size: 1, mtime: 10},
    {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
  ]));
  assert.notEqual(signature, api.directoryEntriesSignature([
    {name: 'a.txt', kind: 'file', size: 1, mtime: 11},
    {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
  ]));
  assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 1}), false);
  assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 11, size: 1}), true);
  assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 2}), true);
}

{
  const api = loadYolomux('', ['1']);
  const imagePath = '/home/test/a.png';
  const viewerItem = api.registerImageViewerLayoutItem(imagePath);
  assert.equal(viewerItem, api.imageViewerItemFor(imagePath));
  assert.deepStrictEqual(canonical(api.openFileEditorItems()), [viewerItem]);
  assert.deepStrictEqual(canonical(api.filePanelItemsForPath(imagePath)), [viewerItem]);
  assert.equal(api.fileItemPath(viewerItem), imagePath);
  const fileItem = api.registerFileEditorLayoutItem(imagePath);
  assert.equal(fileItem, api.fileEditorItemFor(imagePath));
  assert.deepStrictEqual(canonical(api.openFileEditorItems()), [viewerItem, fileItem]);
  assert.deepStrictEqual(canonical(api.filePanelItemsForPath(imagePath)), [viewerItem, fileItem]);
  assert.equal(api.imageOpenUsesSharedViewer(), true);
  assert.equal(api.imageOpenUsesSharedViewer({forceNewTab: true}), false);
  assert.equal(api.imageOpenUsesSharedViewer({targetSlot: 'left'}), false);

  api.setOpenFileStateForTest(imagePath, {kind: 'error', dirty: false, externalMissing: true, error: 'file deleted or moved on disk'});
  assert.equal(api.openFileIsMissing(imagePath), true);
  const missingHtml = api.fileEditorPaneTabHtml(fileItem);
  assert.ok(missingHtml.includes('file-tab-missing-badge'), 'missing file tabs show a badge');
  assert.ok(missingHtml.includes('a.png'), 'missing file tabs still show the basename');
  assert.equal(api.openFileStatus({kind: 'text', externalError: 'network down'}).message.includes('file state unknown'), true);
  assert.equal(api.openFileStatus({kind: 'text', externalError: 'network down'}).message.includes('deleted'), false, 'network/list refresh errors are not reported as deletion');
}

{
  const api = loadYolomux('', ['1']);
  const state = api.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
  assert.equal(state.copyRelativeDisabled, false);
  assert.equal(state.openInNewTabDisabled, true);
  assert.equal(state.downloadDisabled, false);
  assert.equal(state.renameDisabled, false);
  assert.equal(state.deleteDisabled, false);
  const imageState = api.fileContextMenuState({kind: 'file', name: 'screen.png'}, ['/repo/app/screen.png'], ['screen.png']);
  assert.equal(imageState.openInNewTabDisabled, false);

  const readonlyApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly');
  const readonlyState = readonlyApi.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
  assert.equal(readonlyState.downloadDisabled, false);
  assert.equal(readonlyState.renameDisabled, true);
  assert.equal(readonlyState.deleteDisabled, true);
}

{
  const api = loadYolomux('', ['1']);
  const html = api.transcriptPathRowHtml('/tmp/yolomux/session.jsonl');
  assert.ok(html.includes('/tmp/yolomux/session.jsonl'));
  assert.ok(html.includes('data-copy-transcript-path'));
  assert.equal(api.transcriptPathRowHtml('').includes('no transcript path'), true);
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.editorWrapValue(false), 'off');
  assert.equal(api.editorWrapValue(true), 'soft');
  assert.equal(api.rawFileUrl('/repo/app/a b.txt', {v: 7}), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&v=7');
  assert.equal(api.rawFileDownloadUrl('/repo/app/a b.txt'), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&download=1');
}

{
  const api = loadYolomux('', ['1']);
  const pixelWheel = api.terminalWheelSignedLines({deltaY: 105, deltaMode: 0}, 40);
  assert.ok(pixelWheel > 2.5 && pixelWheel < 3.5, 'mouse-like pixel wheel remains about three lines');
  const touchpadTick = api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0}, 40);
  assert.ok(touchpadTick > 0 && touchpadTick < 0.2, 'small touchpad pixel deltas accumulate as fractions');
  assert.equal(api.terminalWheelSignedLines({deltaY: -3, deltaMode: 1}, 40), -3);
  assert.equal(api.terminalWheelSignedLines({deltaY: 1, deltaMode: 2}, 40), 12);
  assert.equal(api.terminalWheelSignedLines({deltaY: 999, deltaMode: 0}, 40), 12);
  assert.equal(api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0, ctrlKey: true}, 40), 0);
  assert.equal(api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0, shiftKey: true}, 40), 34);
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.agentErrorIsBlocking('codex transcript not found by process fd or cwd'), false);
  assert.equal(api.agentErrorIsBlocking('missing /home/test/.claude/sessions/123.json'), false);
  assert.equal(api.agentErrorIsBlocking('worker crashed'), true);
  assert.notEqual(api.sessionState('1', {agents: [{kind: 'codex', error: 'codex transcript not found by process fd or cwd'}]}).key, 'blocked');
  assert.notEqual(api.sessionState('1', {agents: [{kind: 'claude', error: 'missing /home/test/.claude/sessions/123.json'}]}).key, 'blocked');
  assert.equal(api.sessionState('1', {agents: [{kind: 'codex', error: 'worker crashed'}]}).key, 'blocked');
}

{
  const api = loadYolomux('', ['1', '2', '3']);
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  slots.left = api.paneStateWithTabs(['1', '__info__'], '1');
  slots.slot1 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(slots);

  assert.equal(api.itemIsBackgroundPaneTab('__info__'), true);
  assert.equal(api.itemIsBackgroundPaneTab('1'), false);
  assert.deepStrictEqual(canonical(api.backgroundTabItems()), ['__info__']);
  assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['__yoagent__', '__files__', '__prefs__', '__changes__', '3']);
}

{
  const api = loadYolomux('', ['1']);
  const firstEditor = api.registerFileEditorLayoutItem('/repo/app/one.md');
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  slots.left = api.paneStateWithTabs([firstEditor], firstEditor);
  slots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(slots);
  assert.equal(api.slotForNewFileEditorTab(), 'left');
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/repo/app'), 'file.txt');
  assert.equal(api.pathRelativeToDirectory('/repo/app/src/file.txt', '/repo/app'), 'src/file.txt');
  assert.equal(api.pathRelativeToDirectory('/repo/app', '/repo/app'), '.');
  assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/'), 'repo/app/file.txt');
  assert.equal(api.pathRelativeToDirectory('/other/file.txt', '/repo/app'), '/other/file.txt');
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.splitPercentForNewItem('1', 'left'), 50);
  assert.equal(api.splitPercentForNewItem('1', 'right'), 50);
  assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'left'), 50);
  assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right'), 50);
  assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right', 42), 42);
  assert.equal(api.splitPercentForNewItem('__files__', 'left'), 22);
  assert.equal(api.splitPercentForNewItem('__files__', 'right'), 78);
}

{
  const api = loadYolomux('', ['1']);
  assert.deepStrictEqual(canonical(api.windowStepVisibility([{window: "0", window_active: true}])), {prev: false, next: false});
  assert.deepStrictEqual(canonical(api.windowStepVisibility([
    {window: "0", window_active: true},
    {window: "1", window_active: false},
    {window: "2", window_active: false},
  ])), {prev: false, next: true});
  assert.deepStrictEqual(canonical(api.windowStepVisibility([
    {window: "0", window_active: false},
    {window: "1", window_active: true},
    {window: "2", window_active: false},
  ])), {prev: true, next: true});
  assert.deepStrictEqual(canonical(api.windowStepVisibility([
    {window: "0", window_active: false},
    {window: "1", window_active: false},
    {window: "2", window_active: true},
  ])), {prev: true, next: false});
}

{
  loadYolomux();
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function showAttentionAlert(');
  const end = source.indexOf('function dismissAttentionAlertsForSession(', start);
  assert.ok(start > 0 && end > start, 'could not locate showAttentionAlert body');
  const body = source.slice(start, end);
  assert.ok(body.includes('container: attentionAlerts'), 'attention notifications use the global fixed stack');
  assert.equal(body.includes('displayToastContainer(session)'), false, 'attention notifications do not use pane-local toast stacks');
  const attentionCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.attention-alerts\s*\{[\s\S]*top:\s*12px[\s\S]*right:\s*12px[\s\S]*left:\s*auto/.test(attentionCss), 'global attention stack is fixed to the top-right corner');
}

{
  const api = loadYolomux();
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]');
  api.setAutoApproveStateForTest('1', {screen: {key: 'working'}});
  api.setAutoApproveStateForTest('2', {screen: {key: 'working'}});
  api.setAutoApproveStateForTest('3', {screen: {key: 'idle'}});
  api.updateDocumentTitle();
  assert.equal(api.runningAgentCount(), 2);
  assert.equal(api.documentTitleForTest(), 'YOLOmux [2 running]');
  api.setAutoApproveStateForTest('1', {screen: {key: 'idle'}});
  api.setAutoApproveStateForTest('2', {screen: {key: 'idle'}});
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]');
}

{
  const api = loadYolomux();
  const info = {
    project: {
      git: {
        branch: 'main',
        head: '747c3fd0c6 ci: Update the dep for the whl publish to be automated (#9961)',
        github_repo: {url: 'https://github.com/ai-project/project'},
      },
      pull_request: null,
    },
  };
  const html = api.tmuxPaneTabHtml('4', info, null, true);
  const tabBadgeSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(tabBadgeSource.includes('function pullRequestNumberIndicatorHtml'), 'tab renders the PR number chip helper');
  assert.ok(/<span class="ci-indicator tab-symbol pr-number-chip"[^>]*>#9961<\/span>/.test(html), 'open PR tab renders the #number as a black chip');
  assert.ok(html.includes('>YO<'), 'tab includes YO marker');
  assert.equal(/session-yolo-marker[^"]*tab-symbol/.test(html), false, 'YO marker stays visible when metadata badges are hidden');
  assert.ok(html.includes('>4<'), 'tab includes session number');
  assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
  assertNoStandalonePrBadge(html, 'open PR tab');
  assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, merged: true}), '');
  assert.equal(html.includes('MERGED'), false, 'main fallback does not show merged status');
  assert.equal(html.includes('(#9961)'), false, 'tab title strips duplicated PR suffix');

  const blockedHtml = api.tmuxPaneTabHtml('4', info, {key: 'blocked', short: 'BLK', label: 'Blocked', reason: 'blocked command'}, false);
  assert.ok(blockedHtml.includes('--attention-animation-delay:'), 'red attention badges carry a synchronized animation delay');

  const genericWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'working'}, true);
  assert.equal(/session-yolo-marker[^"]*active[^"]*working/.test(genericWorkingHtml), false, 'generic working state does not pulse YO marker');

  api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}});
  const workingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
  assert.ok(/session-yolo-marker[^"]*active[^"]*working/.test(workingHtml), 'visible screen working pulses active YO marker');

  api.setAutoApproveStateForTest('4', {enabled: false, screen: {key: 'working'}});
  const autoOffWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
  assert.ok(/session-yolo-marker[^"]*\bworking\b/.test(autoOffWorkingHtml), 'a working agent spins its YO ball even when auto-approve is off');
  assert.equal(/session-yolo-marker[^"]*\bactive\b/.test(autoOffWorkingHtml), false, 'an auto-off working marker is not rendered as active');
  const yoloMarkerCss = fs.readFileSync('static/yolomux.css', 'utf8');
  // DOIT.6 #23: the YO ball spins ONLY when .working, and at the slow rotation setting (not a fast
  // hardcoded value); there is NO ambient idle-rotation rule, so an idle marker is static.
  assert.ok(/\.session-yolo-marker\.working\s*\{[^}]*--yolo-rotation-duration/.test(yoloMarkerCss), '#23: working YO spin is driven by the slow yolo_rotate_ms setting');
  assert.equal(/\.session-yolo-marker\.working\s*\{[^}]*--yolo-working-duration/.test(yoloMarkerCss), false, '#23: the fast hardcoded working duration is gone');
  assert.equal(yoloMarkerCss.includes('--yolo-working-duration'), false, '#23: the dead --yolo-working-duration token is removed');
  assert.equal(/\.session-yolo-marker:not\(\.inactive\):not\(\.locked\):not\(\.working\)/.test(yoloMarkerCss), false, '#23: the ambient idle-rotation rule is deleted (idle markers are static)');

  api.setAutoApproveStateForTest('4', {enabled: false, enabled_elsewhere: true, locked: true, lock_owner: {pid: 1234}, screen: {key: 'working'}});
  const externalHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
  assert.ok(/session-yolo-marker[^"]*locked/.test(externalHtml), 'YO owned by another server renders as yellow locked marker');
  assert.equal(/session-yolo-marker[^"]*active/.test(externalHtml), false, 'external YO is not shown as local active YO');
  assert.ok(externalHtml.includes('YOLO on elsewhere'), 'external YO marker title explains ownership is elsewhere');

  api.applyServerMetadataPulsesForTest('4', {main: 20000, pr: 20000});
  const metadataPulseHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
  assert.ok(metadataPulseHtml.includes('branch-indicator metadata-pulse'), 'MAIN badge pulses after metadata change');
  assert.equal(metadataPulseHtml.includes('pr-indicator metadata-pulse'), false, 'open PR number badge is not rendered or pulsed');

  const mergedInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 12, merged: true, checks: {state: 'success'}},
    },
  };
  api.applyServerMetadataPulsesForTest('8', {status: 20000});
  const mergedPulseHtml = api.tmuxPaneTabHtml('8', mergedInfo, {key: 'idle'}, true);
  assert.ok(mergedPulseHtml.includes('pr-status-merged metadata-pulse'), 'MERGED badge pulses after status change');

  [
    {session: '9', number: 13, state: 'failure', statusLabel: 'CI failing', statusClass: 'pr-status-failing', pulse: true, label: 'failing open PR'},
    {session: '10', number: 14, state: 'passing', statusLabel: 'open', statusClass: 'pr-status-passing', pulse: true, label: 'passing open PR'},
    {session: '11', number: 15, state: 'pending', statusLabel: 'open', statusClass: 'pr-status-pending', pulse: false, label: 'pending open PR'},
    {session: '12', number: 16, state: 'unknown', statusLabel: 'open', statusClass: '', pulse: false, label: 'unknown open PR'},
  ].forEach(({session, number, state, statusLabel, statusClass, pulse, label}) => {
    if (pulse) api.applyServerMetadataPulsesForTest(session, {ci: 20000});
    const ciHtml = api.tmuxPaneTabHtml(session, {
      project: {
        git: {branch: 'feature'},
        pull_request: {number, status_label: statusLabel, checks: {state}},
      },
    }, {key: 'idle'}, true);
    assertNoStandalonePrBadge(ciHtml, label);
    if (statusClass) assert.ok(ciHtml.includes(statusClass), `${label} renders ${statusClass}`);
    if (pulse) assert.ok(ciHtml.includes('metadata-pulse'), `${label} CI badge is marked after CI change`);
    if (state !== 'unknown') assertSingleCiBadge(ciHtml, label);
  });
}

{
  const api = loadYolomux();
  const info = {
    selected_pane: {current_path: '/home/test/project/project3'},
    project: {
      git: {branch: 'keivenc/GH-2132__reasoning-dangling-end-marker', root: '/home/test/project/project3'},
      pull_request: {
        number: 9981,
        title: 'fix(parser): parse dangling reasoning end markers',
        status_label: 'CI failing',
        checks: {state: 'failure'},
      },
      linear: [{identifier: 'GH-2132', title: 'DeepSeek V4 validation'}],
    },
  };
  api.setTranscriptInfoForTest('4', info);

  const detail = api.tabMenuDetailText('4', info);
  const searchFields = api.tabSearchFields('4');
  assert.ok(searchFields.includes('PR'), 'tab search fields include the literal PR token');
  assert.ok(searchFields.includes('PR#9981'), 'tab search fields include PR#number');
  assert.ok(searchFields.includes('#9981'), 'tab search fields include #number');
  assert.ok(searchFields.includes('9981'), 'tab search fields include bare PR number');
  assert.ok(detail.includes('GH-2132__reasoning-dangling-end-marker'), 'tab menu detail includes fuller branch name');
  assert.ok(detail.includes('~/project/project3'), 'tab menu detail includes compact path');
  assert.ok(detail.includes('#9981 CI failing'), 'tab menu detail includes PR and status');
  assert.ok(detail.includes('GH-2132'), 'tab menu detail includes Linear identifier');
  const linearIndex = detail.indexOf('GH-2132', detail.indexOf('~/project/project3'));
  assert.ok(linearIndex < detail.indexOf('#9981 CI failing'), 'tab menu detail lists Linear before PR');

  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.leafNode('left');
  slots.left = api.paneStateWithTabs(['4'], '4');
  api.setLayoutSlotsForTest(slots);
  const command = api.menuTabCommand('4');
  assert.ok(command.ariaLabel.includes('GH-2132__reasoning-dangling-end-marker'), 'tab menu row aria label carries detail');
  assert.ok(command.html.includes('fix(parser): parse dangling reasoning end markers'), 'tab menu row includes long PR title');
  assert.ok(command.html.includes('pane-tab-core'), 'tab menu row uses pane tab markup');

  const popover = api.sessionPopoverHtml('4', info, 'codex', true);
  assert.ok(popover.indexOf('popover-label">Linear') < popover.indexOf('popover-label">PR'), 'tab popover lists Linear before PR');
}

{
  const api = loadYolomux('', ['alpha', 'beta']);
  api.setActivitySummaryPayloadForTest({
    generated_at: '2026-05-31T12:00:00+00:00',
    global: {
      headline: "Your most recent work is about editor fixes, and you are currently making changes to yolomux.dev in order to finish editor fixes. So far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
      lines: [
        "Your most recent work is about editor fixes, and you are currently making changes to yolomux.dev in order to finish editor fixes. So far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
        'Session alpha: Codex is active in yolomux.dev; 2 files changed (+8/-1); editor fixes',
      ],
    },
    sessions: {
      alpha: {local: "Codex session alpha is active in yolomux.dev. It has been working on editor fixes. It currently has 2 files changed (+8/-1)."},
    },
  });
  assert.ok(api.globalActivitySummaryHtml().includes('YO!agent'), 'global activity summary uses the YO agent label');
  assert.ok(api.yoagentSessionSummariesHtml().includes('session alpha'), 'YO!agent renders per-session summaries');
  assert.ok(api.yoagentSessionSummariesHtml().includes('Codex session alpha is active'), 'YO!agent per-session summary uses the local roll-up');
  assert.equal(api.yoagentChatHtml().includes('data-yoagent-chat-form'), false, 'No-agent YO!agent hides the chat form');
  assert.ok(api.yoagentChatHtml().includes('Set a Claude or Codex backend in Preferences to chat.'), 'No-agent YO!agent points users to backend settings');
  api.setClientSettingsPatchForTest({yoagent: {backend: 'claude'}});
  const enabledChatHtml = api.yoagentChatHtml();
  assert.ok(enabledChatHtml.includes('data-yoagent-chat-form'), 'Claude-backed YO!agent panel includes a chat form');
  assert.ok(enabledChatHtml.includes('Ask YO!agent'), 'Claude-backed YO!agent chat starts with an empty prompt');
  assert.ok(enabledChatHtml.includes('yoagent-chat empty'), 'empty YO!agent chat uses the compact empty layout');
  assert.equal(enabledChatHtml.includes('yoagent-chat-toolbar'), false, 'YO!agent chat does not put Clear in a detached toolbar');
  assert.ok(enabledChatHtml.indexOf('yoagent-chat-send') < enabledChatHtml.indexOf('yoagent-chat-clear'), 'YO!agent Ask and Clear buttons render together in the footer row');
  api.setYoagentBusyForTest(true);
  assert.ok(api.yoagentChatHtml().includes('yoagent-chat-spinner'), 'YO!agent busy state includes an animated spinner');
  assert.ok(api.yoagentChatHtml().includes('thinking...'), 'YO!agent busy state uses concise thinking text');
  assert.ok(api.yoagentChatHtml().includes('session-yolo-marker active working'), 'YO!agent busy spinner reuses the YO tab working marker');
  api.setYoagentBusyForTest(false);
  api.setYoagentDraftForTest('half typed question');
  assert.ok(api.yoagentChatHtml().includes('value="half typed question"'), 'YO!agent chat draft survives summary refresh re-renders');
  api.setYoagentErrorForTest("Couldn't reach the YOLOmux server. Your question is still in the box; retry after the server is back.");
  assert.ok(api.yoagentChatHtml().includes('data-yoagent-retry'), 'YO!agent network failures show a retry action without losing the draft');
  api.setYoagentErrorForTest('');
  api.setYoagentNoticeForTest({backend: 'claude', reason: 'Claude CLI is not logged in. Run `claude login`.'});
  assert.ok(api.yoagentChatHtml().includes('yoagent-chat-notice'), 'YO!agent chat surfaces backend fallback notices');
  assert.ok(api.yoagentChatHtml().includes('claude'), 'YO!agent fallback notice includes the backend');
  assert.ok(api.yoagentChatHtml().includes('claude login'), 'YO!agent fallback notice includes the login action');
  assert.ok(api.globalActivitySummaryHtml().includes('3 files changed (+9/-2)'), 'global activity summary renders file totals');
  assert.ok(api.globalActivitySummaryHtml().includes('Your most recent work is about editor fixes'), 'global activity summary renders a human sentence');
  assert.equal(api.globalActivitySummaryHtml().includes('Session alpha'), false, 'global activity summary omits per-session detail lines');
  assert.equal(api.sessionActivitySummary('alpha').local, "Codex session alpha is active in yolomux.dev. It has been working on editor fixes. It currently has 2 files changed (+8/-1).");
  api.setTranscriptInfoForTest('alpha', {
    project: {
      git: {
        root: '/repo/alpha',
        other_branches: {
          branches: [
            {name: 'zeta', updated: 'yesterday', updated_ts: 100, subject: 'second item', linear_ids: ['GH-2']},
          ],
        },
      },
    },
  });
  api.setTranscriptInfoForTest('beta', {
    project: {
      git: {
        root: '/repo/beta',
        other_branches: {
          branches: [
            {name: 'alpha', updated: 'today', updated_ts: 200, subject: 'first item', linear_ids: ['GH-1']},
          ],
        },
      },
    },
  });

  assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.session)), ['beta', 'alpha']);
  api.setInfoBranchSortForTest('session', 'asc');
  assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.session)), ['alpha', 'beta']);
  api.setInfoBranchSort('session');
  assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.session)), ['beta', 'alpha']);
  api.setInfoBranchSortForTest('branch', 'asc');
  assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.branch)), ['alpha', 'zeta']);
}

{
  const api = loadYolomux();
  assert.equal(api.dedentSelectionText('  hello\n  world'), 'hello\nworld');
  assert.equal(api.dedentSelectionText('  hello\n    world'), 'hello\n  world');
  assert.equal(api.dedentSelectionText('\n  hello\n  world\n'), '\nhello\nworld\n');
  assert.equal(api.dedentSelectionText('hello\n  world'), 'hello\nworld');
  assert.equal(api.dedentSelectionText('● 1\n  2\n  3'), '1\n2\n3');
  assert.equal(api.dedentSelectionText('• answer'), 'answer');
  assert.equal(api.dedentSelectionText('• answer:\n\n  \"  hello\\n  world\"'), 'answer:\n\n\"  hello\\n  world\"');
}

{
  const api = loadYolomux();
  const lines = [
    terminalLine('https://ex'),
    terminalLine('ample.com/', true),
    terminalLine('abcdef', true),
  ];
  const term = {
    buffer: {
      active: {
        getLine(index) {
          return lines[index] || null;
        },
      },
    },
  };

  const middleLinks = api.terminalWrappedLineLinks(term, 2);
  assert.equal(middleLinks.length, 1);
  assert.equal(middleLinks[0].text, 'https://example.com/abcdef');
  assert.deepStrictEqual(canonical(middleLinks[0].range), {
    start: {x: 1, y: 1},
    end: {x: 6, y: 3},
  });

  const lastLinks = api.terminalWrappedLineLinks(term, 3);
  assert.equal(lastLinks.length, 1);
  assert.equal(lastLinks[0].text, 'https://example.com/abcdef');
}

{
  const api = loadYolomux();
  const strip = tabStrip([
    tabElement('1', 100, 100),
    tabElement('2', 203, 100),
    tabElement('3', 306, 100),
  ]);

  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 110, clientY: 8}, '9')), {index: 0, x: 2, y: 0, height: 27});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '9')), {index: 1, x: 103, y: 0, height: 27});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 390, clientY: 8}, '9')), {index: 3, x: 304, y: 0, height: 27});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '2')), {index: 1, x: 206, y: 0, height: 27});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(tabStrip([]), {clientX: 180, clientY: 8}, '9')), {index: 0, x: 80, y: 0, height: 28});
  assert.equal(api.paneTabDropIndex(strip, {clientX: 225, clientY: 8}, '9'), 1);

  const multiLineStrip = tabStrip([
    tabElement('1', 100, 100, 0),
    tabElement('2', 203, 100, 0),
    tabElement('3', 100, 100, 30),
    tabElement('4', 203, 100, 30),
  ]);
  multiLineStrip.rect = {left: 100, right: 406, top: 0, bottom: 58, width: 306, height: 58};
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 110, clientY: 38}, '9')), {index: 2, x: 2, y: 30, height: 27});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 225, clientY: 38}, '9')), {index: 3, x: 103, y: 30, height: 27});
}

{
  // DOIT.6 #24: View -> Theme is a submenu of discrete System/Dark/Light one-click items.
  const api = loadYolomux('', ['1']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const themeSubmenu = api.appMenuTree()
    .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
    .find(item => item.type === 'submenu' && item.label === 'Theme');
  assert.ok(themeSubmenu, '#24: View has a Theme submenu');
  const themeLabels = themeSubmenu.items.filter(item => item.type === 'command').map(item => item.label);
  assert.deepStrictEqual([...themeLabels], ['System', 'Dark', 'Light'], '#24: Theme submenu offers System/Dark/Light as discrete one-click items');
  assert.ok(themeSubmenu.items.some(item => item.label === 'Dark' && item.checked !== undefined), '#24: Theme items carry a checked state for the current mode');
  // Picking a theme moves the terminal palette in lockstep (symmetric for dark and light).
  assert.ok(/function setGlobalThemeMode[\s\S]*?patch\.appearance\.terminal_theme = terminalThemeSettingForGlobalMode\(next\)/.test(source), '#24: setGlobalThemeMode sets the terminal palette alongside the global theme');
}

{
  // DOIT.6 #25: file-search dedupe folds mirror + symlink copies, keeps different-content same-name.
  const api = loadYolomux('', ['1']);
  const deduped = api.dedupeFileSearchResults([
    {path: '/a/notes/DIS-1842.md', realpath: '/a/notes/DIS-1842.md', size: 100},
    {path: '/b/notes/DIS-1842.md', realpath: '/b/notes/DIS-1842.md', size: 100},   // content mirror -> folded
    {path: '/c/DIS-1842.md', realpath: '/c/DIS-1842.md', size: 250},                // different content -> kept
    {path: '/d/link.md', realpath: '/a/notes/DIS-1842.md', size: 100},              // symlink overlap -> folded
  ]).map(file => file.path);
  assert.deepStrictEqual([...deduped], ['/a/notes/DIS-1842.md', '/c/DIS-1842.md'], '#25: mirror + symlink copies fold; different-content same-name both survive');
  // Unknown-size hits dedupe only by path/realpath (never collapse two same-name unknown-size files).
  const unknown = api.dedupeFileSearchResults([
    {path: '/x/a.md', realpath: '/x/a.md'},
    {path: '/y/a.md', realpath: '/y/a.md'},
  ]).map(file => file.path);
  assert.deepStrictEqual([...unknown], ['/x/a.md', '/y/a.md'], '#25: unknown-size same-name files are not collapsed');
}

{
  // DOIT.8 Phase 0: i18n runtime — t()/tPlural() fallback + interpolation, active-over-en, pseudo.
  const api = loadYolomux('', ['1']);
  api.i18nSetCatalogForTest('en', {greet: 'Hi {name}', plain: 'Plain'});
  api.setActiveLocaleForTest('en');
  assert.equal(api.t('greet', {name: 'Al'}), 'Hi Al', 't() interpolates {params}');
  assert.equal(api.t('plain'), 'Plain', 't() returns the catalog value');
  assert.equal(api.t('missing.key'), 'missing.key', 't() falls back to the key when absent (never blank)');
  api.i18nSetCatalogForTest('en', {'files.one': '{count} file', 'files.other': '{count} files'});
  assert.equal(api.tPlural('files', 1), '1 file', 'tPlural picks the one category');
  assert.equal(api.tPlural('files', 3), '3 files', 'tPlural picks the other category');
  api.i18nSetCatalogForTest('en', {x: 'English'});
  api.i18nSetCatalogForTest('zz', {x: 'Zzz'});
  api.setActiveLocaleForTest('zz');
  assert.equal(api.t('x'), 'Zzz', 'active locale wins over the en fallback');
  assert.equal(api.t('y'), 'y', 'missing-in-active falls through en to the key');
}

{
  // DOIT.8 Phase 0: the Preferences General section + section titles render through t(); under the
  // en-XA pseudo-locale every extracted label is accented/padded, with no plain-English leakage.
  const api = loadYolomux('', ['1']);
  const enXA = JSON.parse(fs.readFileSync('static/locales/en-XA.json', 'utf8'));
  api.i18nSetCatalogForTest('en-XA', enXA);
  api.setActiveLocaleForTest('en-XA');
  const html = api.preferencesPanelHtmlForTest('');
  assert.ok(html.includes(enXA['pref.section.general']), 'pseudo-locale section title renders');
  assert.ok(html.includes(enXA['pref.general.auto_focus.label']), 'pseudo-locale General field label renders');
  assert.ok(html.includes(enXA['pref.general.language.help']), 'pseudo-locale field help renders');
  assert.equal(html.includes('Auto-focus active pane'), false, 'no plain-English General field label leaks under the pseudo-locale');
}

{
  // DOIT.6 #7: the default (files-mode) search bar blends matching commands/tabs into the results.
  const api = loadYolomux('', ['1']);
  const prefsLabel = api.itemLabel(api.prefsItemId);
  api.setFileQuickOpenCandidatesForTest('/repo/app', [
    {name: 'notes.py', path: '/repo/app/notes.py', relative_path: 'notes.py'},
  ]);
  api.setCommandPaletteStateForTest('files', prefsLabel);
  assert.ok(api.commandPaletteItems().some(item => item.group === 'Tabs' && item.label === prefsLabel), '#7: a command/tab matching a plain files-mode query is blended in (no > needed)');
  // `>` stays commands-only — no file candidates blended.
  api.setCommandPaletteStateForTest('files', `>${prefsLabel}`);
  assert.ok(!api.commandPaletteItems().some(item => item.path === '/repo/app/notes.py'), '#7: the > prefix stays commands-only');
  // An empty files-mode query must NOT dump the whole command corpus.
  api.setCommandPaletteStateForTest('files', '');
  assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: empty files-mode query shows files only (no command dump)');
  // `@` stays reserved for symbols (no command blend).
  api.setCommandPaletteStateForTest('files', '@thing');
  assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: @ stays reserved for symbols');
}

{
  // DOIT.6 #8-#13: renames, toggles, theme propagation, README preview.
  const api = loadYolomux('', ['1']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  // #8: Branch Info -> YO!info (single constant drives tab/menu/palette).
  assert.ok(source.includes("const infoTabLabel = 'YO!info'"), '#8: the info tab is renamed YO!info');
  // #9: File -> Finder toggles via toggleFinderPane (hide when in layout, else open).
  assert.ok(source.includes('menuCommand(fileExplorerLabel(), () => toggleFinderPane()'), '#9: File -> Finder uses the toggle');
  assert.ok(/function toggleFinderPane\(\)\s*\{[^}]*itemInLayout\(fileExplorerItemId\)[^}]*removeSessionFromLayout\(fileExplorerItemId\)/.test(source), '#9: toggle hides the Finder when it is already in the layout');
  // #11: View -> Theme writes terminal_theme alongside appearance.theme, mapped.
  assert.equal(api.terminalThemeSettingForGlobalMode('system'), 'follow-app', '#11: system maps to follow-app');
  assert.equal(api.terminalThemeSettingForGlobalMode('dark'), 'dark', '#11: dark maps to dark');
  assert.equal(api.terminalThemeSettingForGlobalMode('light'), 'light', '#11: light maps to light');
  assert.ok(source.includes('patch.appearance.terminal_theme = terminalThemeSettingForGlobalMode(next)'), '#11: the View theme toggle moves the terminal palette in lockstep');
  // #10: a global-theme change re-themes live editors via the compartment swap.
  assert.ok(/previousEditorSchemeId !== activeEditorScheme\(\)\.id\)\s*\{[^}]*refreshOpenEditorThemePanels\(\)/.test(source), '#10: theme change re-themes open editors');
  // #12: Preferences field renamed.
  assert.ok(source.includes("label: 'Global color theme'"), '#12: the Preferences field reads "Global color theme"');
  assert.equal(source.includes("label: 'Global app theme'"), false, '#12: no stale "Global app theme" label remains');
  // #13: Help -> README opens rendered markdown preview.
  assert.ok(source.includes("openFileInEditor(path, 'README.md', {viewMode: 'preview'})"), '#13: README opens in preview mode');
}

{
  // DOIT.6 #6: every pane keeps its active tab clearly green (no dimming); focused pane = brighter
  // lime + ring. Source-guards on the shared tokens + the un-dimmed unfocused-active rule.
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/--pane-tab-unfocused-active-bg:\s*#4f9e3a/.test(css), '#11: unfocused panes show a clearly-visible green active tab');
  assert.ok(/\.panel:not\(\.active-pane\):not\(\.file-explorer-panel\):not\(\.changes-panel\) \.pane-tab\.active\s*\{\s*opacity:\s*1/.test(css), '#11: unfocused active tabs are no longer dimmed');
  assert.ok(/--pane-tab-active-bg:\s*#86d600/.test(css), '#11: the focused pane keeps the brighter lime active tab as its extra cue');
}

{
  // DOIT.6 #5: same-strip drag-reorder works in BOTH directions. Dropping a tab anywhere onto a
  // neighbor moves it past that neighbor (no center-overshoot required for the left->right case).
  const api = loadYolomux('', ['6']);
  const strip = tabStrip([tabElement('6', 100, 100), tabElement('P', 203, 100)]);
  // DOIT.6 #26 (re-open of #12): a drop ANYWHERE on the neighbor reorders — BOTH halves, BOTH ways.
  // P spans 203-303 (center 253); L spans 100-200 (center 150).
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '6').index, 1, 'L dropped on R LEFT half reorders RIGHT (was the no-op pre-fix)');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 290, clientY: 8}, '6').index, 1, 'L dropped on R RIGHT half reorders RIGHT');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 120, clientY: 8}, 'P').index, 0, 'R dropped on L LEFT half reorders LEFT');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 190, clientY: 8}, 'P').index, 0, 'R dropped on L RIGHT half reorders LEFT');
  // Cross-pane drops keep the centered insert threshold (unchanged behavior).
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 230, clientY: 8}, '9').index, 1, 'cross-pane drop keeps the centered threshold');
}

{
  const api = loadYolomux();
  const strip = tabStrip([
    tabElement('1', 100, 100),
    tabElement('2', 203, 100),
    tabElement('3', 306, 100),
  ]);

  api.showPaneTabDropPreview(strip, {clientX: 225, clientY: 8}, '9');

  assert.ok(strip.classList.contains('drag-over'), 'tab strip shows drag target outline');
  assert.ok(strip.classList.contains('tab-drop-preview'), 'tab strip shows insertion preview');
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '103px');
  assert.equal(strip.style.getPropertyValue('--tab-drop-y'), '0px');
  assert.equal(strip.style.getPropertyValue('--tab-drop-height'), '27px');

  api.clearPaneTabDropPreview(strip);

  assert.equal(strip.classList.contains('drag-over'), false);
  assert.equal(strip.classList.contains('tab-drop-preview'), false);
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '');
  assert.equal(strip.style.getPropertyValue('--tab-drop-y'), '');
  assert.equal(strip.style.getPropertyValue('--tab-drop-height'), '');
}

{
  const api = loadYolomux();
  const slot = new TestElement('slot-left');
  slot.classList.add('drop-slot');
  slot.dataset.slot = 'left';
  slot.rect = {left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400};
  api.setGridPreviewNodesForTest([slot]);

  const event = fileDragEvent(slot, {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, 16, 200);
  api.handleDropDragOver(event);

  assert.ok(event.defaultPrevented, 'file dragover accepts Finder file drags');
  assert.ok(event.propagationStopped, 'file dragover owns pane split preview');
  assert.equal(event.dataTransfer.dropEffect, 'copy');
  assert.ok(slot.classList.contains('drag-over'), 'file dragover shows pane target outline');
  assert.ok(slot.classList.contains('drop-preview'), 'file dragover shows split preview');
  assert.ok(slot.classList.contains('drop-preview-left'), 'file dragover uses the pointer-aware split zone');
  assert.equal(slot.dataset.dropLabel, 'left');
}

{
  const api = loadYolomux();
  const slot = new TestElement('slot-left');
  slot.classList.add('drop-slot');
  slot.dataset.slot = 'left';
  slot.rect = {left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400};
  const terminal = new TestElement('terminal');
  slot.appendChild(terminal);
  api.setGridPreviewNodesForTest([slot]);
  api.installFilePathDropTarget('1', terminal);

  const event = fileDragEvent(terminal, {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, 16, 200);
  terminal.listeners.get('dragover')[0](event);

  assert.equal(event.defaultPrevented, false, 'terminal path target does not steal file drags from pane drop handling');
  assert.equal(event.propagationStopped, false, 'terminal path target lets file drags bubble into pane drop handling');
  api.handleDropDragOver(event);

  assert.ok(event.defaultPrevented, 'bubbled file dragover accepts Finder file drags');
  assert.ok(event.propagationStopped, 'bubbled file dragover owns pane split preview');
  assert.equal(event.dataTransfer.dropEffect, 'copy');
  assert.equal(terminal.classList.contains('path-drag-over'), false, 'terminal path insertion affordance is not shown for file-open drags');
  assert.ok(slot.classList.contains('drop-preview-left'), 'terminal path target also shows the pane split preview');
}

{
  const api = loadYolomux();
  const slot = new TestElement('slot-left');
  slot.classList.add('drop-slot');
  slot.dataset.slot = 'left';
  slot.rect = {left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400};
  const terminal = new TestElement('terminal');
  slot.appendChild(terminal);
  api.setGridPreviewNodesForTest([slot]);
  api.installFilePathDropTarget('1', terminal);

  const event = fileDragEvent(terminal, {path: '/home/test/assets', paths: ['/home/test/assets'], kind: 'dir', name: 'assets'}, 16, 200);
  terminal.listeners.get('dragover')[0](event);

  assert.ok(event.defaultPrevented, 'terminal path target accepts directory drags for path insertion');
  assert.ok(event.propagationStopped, 'terminal path target owns directory path insertion');
  assert.equal(event.dataTransfer.dropEffect, 'copy');
  assert.ok(terminal.classList.contains('path-drag-over'), 'terminal still shows path insertion affordance for directories');
  assert.ok(slot.classList.contains('drop-preview-left'), 'directory path target still shows the pane split preview');
}

{
  const api = loadYolomux();
  const row = new TestElement('pic-row');
  row.rect = {left: 10, top: 20, right: 250, bottom: 40, width: 240, height: 20};
  const event = fileDragEvent(row, {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, 30, 28);

  api.startFileTreeDrag(event, row, '/home/test/pic.png', {kind: 'file', name: 'pic.png'});

  assert.equal(event.dataTransfer.effectAllowed, 'copy');
  assert.equal(event.dataTransfer['text/plain'], '/home/test/pic.png');
  assert.equal(event.dataTransfer['application/x-yolomux-file'], JSON.stringify({
    path: '/home/test/pic.png',
    paths: ['/home/test/pic.png'],
    kind: 'file',
    name: 'pic.png',
  }));
  assert.ok(api.customDragPreviewForTest(), 'file drag preview is installed');
  assert.equal(event.dataTransfer.dragImage.node.className, 'transparent-drag-image');
  assert.equal(event.dataTransfer.dragImage.x, 0);
  assert.equal(event.dataTransfer.dragImage.y, 0);

  const slot = new TestElement('slot-left');
  slot.classList.add('drop-slot');
  slot.dataset.slot = 'left';
  slot.rect = {left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400};
  api.setGridPreviewNodesForTest([slot]);
  const protectedEvent = fileDragEvent(slot, {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, 16, 200);
  protectedEvent.dataTransfer.getData = () => '';
  api.handleDropDragOver(protectedEvent);

  assert.ok(protectedEvent.defaultPrevented, 'file dragover accepts protected-mode browser drag data');
  assert.ok(slot.classList.contains('drop-preview-left'), 'file dragover uses dragstart fallback when getData is unavailable before drop');

  api.stopCustomDragPreview();

  assert.equal(api.customDragPreviewForTest(), null, 'file drag preview is removed on cleanup');
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(source.includes('installFilePathDropTarget(session, panel);'), false, 'panel-level path drop target must not swallow pane split previews');
  assert.ok(source.includes('installTerminalFileDrop(session, container);'), 'terminal surface still accepts path insertion drops');
}

{
  const api = loadYolomux();
  const source = tabElement('4', 100, 140);
  source.rect = {left: 100, right: 240, top: 20, bottom: 47, width: 140, height: 27};
  source.classList.add('pane-tab');
  const event = dragEvent(125, '4');
  event.currentTarget = source;
  event.clientY = 31;

  api.startSessionDrag(event, '4', 'left');

  assert.equal(source.classList.contains('dragging'), false, 'source tab is not dimmed while dragging');
  assert.equal(event.dataTransfer.effectAllowed, 'move');
  assert.equal(event.dataTransfer['application/x-yolomux-session'], JSON.stringify({session: '4', sourceSlot: 'left'}));
  assert.equal(event.dataTransfer['text/plain'], '4');
  assert.ok(event.dataTransfer.dragImage, 'transparent native drag image is installed');
  assert.equal(event.dataTransfer.dragImage.node.className, 'transparent-drag-image');
  assert.equal(event.dataTransfer.dragImage.x, 0);
  assert.equal(event.dataTransfer.dragImage.y, 0);
  const preview = api.customDragPreviewForTest();
  assert.ok(preview, 'custom drag preview is installed');
  assert.equal(preview.style.opacity, '0.50');
  assert.ok(preview.classList.contains('drag-image'));
  assert.equal(preview.style.left, '100px');
  assert.equal(preview.style.top, '20px');
}

{
  const api = loadYolomux();
  const strip = tabStrip([
    tabElement('1', 100, 100),
    tabElement('2', 203, 100),
    tabElement('3', 306, 100),
  ]);
  const stalePanePreview = new TestElement('pane');
  stalePanePreview.classList.add('drag-over', 'drop-preview', 'drop-preview-top');
  stalePanePreview.dataset.dropLabel = 'top';
  stalePanePreview.style.setProperty('--tab-drop-x', 'old');
  stalePanePreview.style.setProperty('--tab-drop-y', 'old');
  stalePanePreview.style.setProperty('--tab-drop-height', 'old');
  api.setGridPreviewNodesForTest([stalePanePreview]);
  api.bindPaneTabStrip(strip, 'left');

  const event = dragEvent(225, '4');
  strip.ondragover(event);

  assert.ok(event.defaultPrevented, 'tab-strip dragover accepts the session drag');
  assert.ok(event.propagationStopped, 'tab-strip dragover does not bubble into pane split handling');
  assert.equal(event.dataTransfer.dropEffect, 'move');
  assert.equal(stalePanePreview.classList.contains('drag-over'), false);
  assert.equal(stalePanePreview.classList.contains('drop-preview'), false);
  assert.equal(stalePanePreview.classList.contains('drop-preview-top'), false);
  assert.equal('dropLabel' in stalePanePreview.dataset, false);
  assert.equal(stalePanePreview.style.getPropertyValue('--tab-drop-x'), '');
  assert.equal(stalePanePreview.style.getPropertyValue('--tab-drop-y'), '');
  assert.equal(stalePanePreview.style.getPropertyValue('--tab-drop-height'), '');
  assert.ok(strip.classList.contains('tab-drop-preview'), 'tab strip owns the active preview');
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '103px');
  assert.equal(strip.style.getPropertyValue('--tab-drop-y'), '0px');
  assert.equal(strip.style.getPropertyValue('--tab-drop-height'), '27px');
}

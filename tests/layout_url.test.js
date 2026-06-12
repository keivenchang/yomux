const assert = require('assert');
const fs = require('fs');
const UI_PINS = JSON.parse(fs.readFileSync('tests/ui_pins.json', 'utf8'));  // shared color pins (see test_ui_pins.py)
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
  constructor(id = '', tagName = 'div') {
    this.id = id;
    this.localName = tagName;
    this.tagName = String(tagName || 'div').toUpperCase();
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
  prepend(...nodes) {
    for (const node of nodes.reverse()) {
      this.insertBefore(node, this.children[0] || null);
    }
  }
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
  after(...nodes) {
    if (!this.parentElement) return;
    let before = this.nextElementSibling;
    for (const node of nodes) {
      this.parentElement.insertBefore(node, before || null);
      before = node.nextElementSibling;
    }
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
    const clone = new TestElement(`${this.id}-clone`, this.localName);
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
    if (selector === '[data-window-dir]') return this.dataset.windowDir !== undefined;
    if (selector === '[data-window-index]') return this.dataset.windowIndex !== undefined;
    if (selector === '[data-detail-toggle]') return this.dataset.detailToggle !== undefined;
    const dataDetailToggleMatch = selector.match(/^\[data-detail-toggle="([^"]+)"\]$/);
    if (dataDetailToggleMatch) return this.dataset.detailToggle === dataDetailToggleMatch[1];
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

class TestFile {
  constructor(parts, name, options = {}) {
    this.parts = parts;
    this.name = name;
    this.type = options.type || '';
    this.size = parts.reduce((total, part) => total + (Number(part?.size) || String(part || '').length), 0);
  }
}

class TestFormData {
  constructor() {
    this.fields = [];
  }

  append(name, value, filename = undefined) {
    this.fields.push({name, value, filename});
  }
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
    // Seed the en catalog the way production inlines bootstrap.strings, so localized labels (the brand
    // tab labels infoTabLabel()/yoagentTabLabel() etc.) resolve synchronously at first render under en.
    strings: {en: JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))},
  });
  const elements = new Map();
  const documentListeners = new Map();
  const windowListeners = new Map();
  const storage = new Map();
  const localStorage = {
    getItem(key) { return storage.has(String(key)) ? storage.get(String(key)) : null; },
    setItem(key, value) { storage.set(String(key), String(value)); },
    removeItem(key) { storage.delete(String(key)); },
  };
  const element = id => {
    if (!elements.has(id)) elements.set(id, new TestElement(id));
    const node = elements.get(id);
    if (id === 'yolomux-bootstrap') node.textContent = bootstrap;
    return node;
  };
  const context = {
    console,
    File: TestFile,
    FormData: TestFormData,
    URLSearchParams,
    WebSocket: {OPEN: 1, CLOSING: 2, CLOSED: 3},
    clearInterval() {},
    clearTimeout() {},
    document: {
      addEventListener(type, listener) {
        if (!documentListeners.has(type)) documentListeners.set(type, []);
        documentListeners.get(type).push(listener);
      },
      body: element('body'),
      createElement: tag => new TestElement('', tag),
      documentElement: element('html'),
      execCommand(command) {
        if (String(command || '').toLowerCase() !== 'copy') return false;
        let prevented = false;
        const event = {
          clipboardData: {
            setData(type, value) {
              if (type === 'text/plain') context.__clipboardText = String(value);
            },
          },
          preventDefault() { prevented = true; },
          stopPropagation() {},
          stopImmediatePropagation() {},
        };
        for (const listener of [...(documentListeners.get('copy') || [])]) listener(event);
        return prevented;
      },
      getElementById: element,
      querySelector: () => null,
      querySelectorAll: () => [],
      removeEventListener(type, listener) {
        const listeners = documentListeners.get(type) || [];
        documentListeners.set(type, listeners.filter(item => item !== listener));
      },
    },
    fetch() { return Promise.reject(new Error('fetch disabled in layout URL tests')); },
    getComputedStyle: () => ({direction: 'ltr'}),
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
      __listeners: windowListeners,
      addEventListener(type, listener) {
        if (!windowListeners.has(type)) windowListeners.set(type, []);
        windowListeners.get(type).push(listener);
      },
      innerHeight: 800,
      innerWidth: 1200,
      localStorage,
      removeEventListener(type, listener) {
        const listeners = windowListeners.get(type) || [];
        windowListeners.set(type, listeners.filter(item => item !== listener));
      },
    },
    localStorage,
    __clipboardText: '',
    // the OSC 52 clipboard bridge decodes base64 UTF-8; expose the host implementations.
    atob,
    btoa,
    TextDecoder,
    Uint8Array,
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
  aboutBrandHtml,
  showAboutModal,
  startupHelperCatalog,
  readStartupHelperIndex,
  writeStartupHelperIndex,
  showStartupHelperTip,
  t,
  tPlural,
  yoagentInlineMarkdown,
  yoagentTightMarkdown,
  joinAndNormalize,
  resolveLocalePref,
  i18nLocaleChoices,
  i18nIsRtl,
  relativeTimeFormat,
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
  editorNav,
  recordEditorNav,
  cursorStyleFileReference,
  fileEditorPaneTabHtml,
  fileQuickOpenItem,
  fileQuickOpenItems,
  fileQuickOpenExtraRootsForSearchQuery,
  fileQuickOpenRootMatchesPathAlias,
  fileQuickOpenRootForFile,
  fileQuickOpenRootForSearch,
  fileQuickOpenRootsForSearch,
  fileQuickOpenTargetSlot,
  fileQuickOpenSearchText,
  fileQuickOpenScopeLabel,
  fileExplorerDirectoryIsIndexed,
  fileExplorerIndexBadgeText,
  gitStatusRowClass,
  fileEditorGitActionControlsVisible,
  diffModeShouldFallBackToEdit,
  setFileExplorerIndexedDirsForTest(paths) { setFileExplorerIndexedDirs(paths); },
  setFileExplorerIndexStatusForTest(root, status) { fileExplorerIndexStatus.set(normalizeStoredFileExplorerIndexedDir(root), status); },
  createTopbarSearch,
  createTopbarNav,
  normalizeWatchedPrRef,
  watchedPrStatusSnapshot,
  watchedPrStatusText,
  watchedPrTransitionKeys,
  shouldNotifyTransitionKey,
  codeMirrorDiffLayout,
  buildDiffOverviewGradientForTest: buildDiffOverviewGradient,
  buildDiffOverviewGradientFromBandsForTest: buildDiffOverviewGradientFromBands,
  diffOverviewRowsFromCodeMirrorChunksForTest: diffOverviewRowsFromCodeMirrorChunks,
  diffOverviewRowsFromCodeMirrorRenderedWeightsForTest: diffOverviewRowsFromCodeMirrorRenderedWeights,
  diffOverviewScrollLooksCurrentOnlyForTest: diffOverviewScrollLooksCurrentOnly,
  diffOverviewCodeMirrorChunksForTest: diffOverviewCodeMirrorChunks,
  diffOverviewRemovedLineCountForTest: diffOverviewRemovedLineCount,
  updateCodeMirrorDiffOverviewForTest: updateCodeMirrorDiffOverview,
  setDiffExpandUnchangedForTest(value) { diffExpandUnchanged = value === true; },
  fileExplorerChangesPanelHtml,
  fileTreeExpandCollapseAllButtonsHtml,
  fileExplorerDirectoryPathsForRootForTest: fileExplorerDirectoryPathsForRoot,
  setAllFileTreeDirectoriesExpandedForTest: setAllFileTreeDirectoriesExpanded,
  fileExplorerSessionFilesTargetSessionForTest: fileExplorerSessionFilesTargetSession,
  sessionFilesCacheKeyForTest: sessionFilesCacheKey,
  noteFileExplorerChangesSessionInteractionForTest: noteFileExplorerChangesSessionInteraction,
  setFileExplorerChangesSelectedSessionForTest(value) { fileExplorerChangesSelectedSession = String(value || ''); },
  changesGroupsSnapshotHtmlForTest: changesGroupsSnapshotHtml,
  fileExplorerChangesCollapseToggleHtml,
  fileExplorerChangesAllReposCollapsedForTest: fileExplorerChangesAllReposCollapsed,
  toggleAllFileExplorerChangesForTest: toggleAllFileExplorerChanges,
  projectMetaHtml,
  diffRefControlsHtml,
  diffRefResetButtonHtml,
  diffRefSelectOptionsHtml,
  diffRefPopoverItems,
  diffRefParams,
  diffRefFromSuggestions,
  diffRefToSuggestions,
  fileDiffRefHistoryItems,
  fileRepoForPath,
  setDiffRefsByRepoForTest(repo, refs) {
    if (!repo) return;
    if (!refs) delete diffRefsByRepo[repo];
    else diffRefsByRepo[repo] = {from: refs.from, to: refs.to};
  },
  globalActivitySummaryHtml,
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
  commonAncestorPath,
  cancelPendingFileExplorerActiveSync,
  fileExplorerSyncPlanForTest: fileExplorerSyncPlan,
  fileExplorerSessionHighlightClassForPath,
  fileExplorerSessionHighlightClassForTest(path, kind = 'dir', preferredItem = null) {
    return fileExplorerSessionHighlightClassForPath(path, kind, {sessionHighlightSets: fileExplorerSessionHighlightSets(preferredItem)});
  },
  fileExplorerSessionHighlightSetsForTest(preferredItem = null) {
    const sets = fileExplorerSessionHighlightSets(preferredItem);
    return {repoRoots: [...sets.repoRoots], touchedDirs: [...sets.touchedDirs], expandedDirs: [...sets.expandedDirs]};
  },
  fileExplorerRootForOpen,
  fileExplorerRootModeValue,
  setFileExplorerRootMode,
  fileExplorerLabel,
  fileExplorerPanelCloseClass,
  fileEditorPanelCloseClass,
  fileIconFor,
  fileIconClassFor,
  fileExplorerNeedsLeftDock,
  visibleFileEditorWatchFilesForTest: visibleFileEditorWatchFiles,
  backgroundFileEditorWatchFilesForTest: backgroundFileEditorWatchFiles,
  clientServerWatchStateForTest: clientServerWatchState,
  fileExplorerPaneTabHtml,
  fetchDirectoryForTest: fetchDirectory,
  currentFileExplorerListErrorForTest: currentFileExplorerListError,
  setFileExplorerPushRefreshDepthForTest(value) { fileExplorerPushRefreshDepth = Math.max(0, Number(value) || 0); },
  setFileExplorerLastListErrorForTest(path, error = 'failed') { setFileExplorerListError(path, error, 500); },
  fetchFilePathInfoForTest: fetchFilePathInfo,
  flushFileExplorerFsBatchForTest: flushFileExplorerFsBatch,
  firstEmptyPane,
  filePopoverRows,
  fuzzySearchScore,
  fuzzyHighlightHtml,
  fuzzySubsequenceMatch,
  fuzzySubsequenceScore,
  lineDiffRows,
  fileConflictCompareHtml,
  childPathParts,
  debugModeEnabledForTest() { return debugModeEnabled; },
  debugPaneItemId,
  debugPanelHtmlForTest: debugPanelHtml,
  jsDebugEventsForTest() { return jsDebugEvents.map(event => ({...event})); },
  jsDebugTextForClipboardForTest: jsDebugTextForClipboard,
  recordJsDebugEventForTest: recordJsDebugEvent,
  inactiveTabItems,
  infoItemId,
  infoPanelSubTabForTest() { return infoPanelSubTab; },
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
  applyLayoutMode,
  setLayoutToSinglePane,
  setLayoutToSplitPanes,
  setLayoutToGridPanes,
  setLayoutToWallPanes,
  layoutModeValues,
  layoutTabsParamValue,
  layoutTreeKey,
  leafNode,
  yoagentItemId,
  fileExplorerItemId,
  prefsItemId,
  menuTabCommand,
  activatePaneTab,
  currentSessionActionTarget,
  setFocusedPanelItem,
  setFocusedTerminal,
  clearFocusedTerminal,
  handleFocusedTerminalCopyShortcutForTest: handleFocusedTerminalCopyShortcut,
  visualActivePaneItemForTest: visualActivePaneItem,
  lastActivePaneItemForTest() { return lastActivePaneItem; },
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
  installTerminalCopyShortcutForTest: installTerminalCopyShortcut,
  osc52ClipboardText,
  installTerminalOsc52BridgeForTest: installTerminalOsc52Bridge,
  setFetchForTest(fn) { globalThis.fetch = fn; },
  clipboardTextForTest() { return globalThis.__clipboardText; },
  clearClipboardTextForTest() { globalThis.__clipboardText = ''; },
  setBrowserSelectionForTest(text, anchorNode = null, focusNode = anchorNode) {
    const selection = {toString: () => String(text || ''), anchorNode, focusNode};
    globalThis.getSelection = () => selection;
    window.getSelection = globalThis.getSelection;
  },
  clearBrowserSelectionForTest() {
    delete globalThis.getSelection;
    delete window.getSelection;
  },
  bindClipboardPasteForTest: bindClipboardPaste,
  documentListenersForTest(type) { return [...(document.__listeners.get(type) || [])]; },
  commandPaletteItemScore,
  commandPaletteRankItems,
  commandPaletteCandidateItems,
  searchRankWeights,
  commandPaletteSearchQuery,
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
  seedSessionTeardownStateForTest(session) {
    const closed = {transcript: 0, summary: 0};
    transcriptStreams.set(session, {close() { closed.transcript += 1; }});
    summaryStreams.set(session, {close() { closed.summary += 1; }});
    autoApproveStates.set(session, {enabled: true});
    uploadResultsBySession.set(session, [{text: 'uploaded'}]);
    uploadCleanupTimers.set(session, 123);
    return closed;
  },
  stopSessionUiForTest: stopSessionUi,
  sessionTeardownStateForTest(session) {
    return {
      terminal: terminals.has(session),
      transcript: transcriptStreams.has(session),
      summary: summaryStreams.has(session),
      autoApprove: autoApproveStates.has(session),
      uploads: uploadResultsBySession.has(session),
      uploadTimer: uploadCleanupTimers.has(session),
    };
  },
  tmuxSessionActionCommands,
  tmuxSessionViewCommands,
  tmuxSessionNameError,
  replaceTmuxSessionInClient,
  normalizedSessionOrder,
  fileDropCategory,
  dropSuggestionsFor,
  composeDropSuggestion,
  normalizeLayoutSlots,
  compactLayoutSlots,
  layoutSlotsSignature,
  dockviewJsonFromLayoutSlots,
  layoutSlotsFromDockviewJson,
  dockviewLayoutContentSignature,
  isPinnableTab,
  tabIsPinned,
  orderPaneTabs,
  pinnedTabIconHtml,
  setTabPinned,
  toggleTabPinned,
  toggleActiveTabPinned,
  pinnedTabsForTest() { return [...pinnedTabItems]; },
  setPinnedTabsForTest(items) {
    pinnedTabItems = normalizePinnedTabItems(items || []);
    writeStoredPinnedTabs();
  },
  paneIsPlaceholder,
  panelControlsHtml,
  setPanelDetailsCollapsedForTest: setPanelDetailsCollapsed,
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
  showTabContextMenu,
  bodyChildren() { return document.body.children; },
  defaultLayoutSlots,
  layoutShapeSignature,
  dedentSelectionText,
  dropIntentForEvent,
  dropIntentAllowsSession,
  paneSwapAllowed,
  paneSwapIntentForEvent,
  paneSwapIntentAllowed,
  swapPaneSlots,
  directoryEntriesSignature,
  editorWrapValue,
  editorViewModeFor,
  editorPreviewModeAvailable,
  setFileEditorViewMode,
  fileEditorBlameControlsVisible,
  updateFileEditorBlameButton,
  updateFileEditorDiffButton,
  updateFileEditorDiffExpandButton,
  codeMirrorConfigSignature,
  openFileDiffAvailable,
  localizedDateTimeFormat,
  sessionFileTimeText,
  sessionFileRelativeTimeText,
  sessionFileDisplayTimeText,
  fileExplorerTreeDateModeLabel,
  fileExplorerTreeDateModeButtonLabel,
  fileExplorerTreeDateModeTitle,
  fileExplorerTreeDateModeForTest() { return fileExplorerTreeDateMode; },
  setFileExplorerTreeDateModeForTest(value) { fileExplorerTreeDateMode = normalizeFileExplorerTreeDateMode(value); },
  dateTimeHourCycleForTest() { return dateTimeHourCycle; },
  setDateTimeHourCycleForTest(value) { dateTimeHourCycle = normalizeDateTimeHourCycle(value); },
  activeEditorSchemeForTest() { return activeEditorScheme(); },
  configuredEditorSchemeForMode,
  editorSchemeCssVariables,
  editorThemeLabel,
  editorPreviewThemeStateForTest: editorPreviewThemeState,
  applyEditorCursorStyle,
  setFileEditorThemeMode,
  setFileEditorPreviewDisplayMode,
  cycleEditorThemeMode,
  fileEditorThemeModeForTest() { return fileEditorThemeMode; },
  fileEditorPreviewDisplayModeForTest() { return fileEditorPreviewDisplayMode; },
  fileEditorCursorStyleForTest() { return fileEditorCursorStyle; },
  setFileEditorCursorStyleForTest(value) { fileEditorCursorStyle = value; },
  editorVisualHighlightHtml,
  editorVisualLineFragments,
  applyGlobalThemeMode,
  globalThemeLabel,
  globalThemeIsDark,
  nextGlobalThemeMode,
  terminalThemeForGlobalTheme,
  terminalMinimumContrastRatio,
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
  markdownPreviewInputAllowed,
  keyboardShortcutsHtml,
  openFileEditorItems,
  pullRequestStatusLabel,
  pullRequestStatusDisplay,
  pullRequestLinkLabel,
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
  terminalTabLabel,
  terminalTabTitle,
  terminalTabDisplayLabel,
  tmuxWindowForTest: tmuxWindow,
  registerFileEditorLayoutItemForTest: registerFileEditorLayoutItem,
  setOpenFileStateForTest(path, state) { openFiles.set(path, state); },
  renderTreeChildrenForTest(container, parentPath, entries, depth = 0, entriesByDirPairs = [], options = {}) {
    renderTreeChildren(container, parentPath, entries, depth, {...options, entriesByDir: new Map(entriesByDirPairs)});
  },
  setFileExplorerRepoInfoForTest(path, repo) {
    fileExplorerRepoInfoCache.set(normalizeDirectoryPath(path), repo);
  },
  repoInfoPopoverHtml,
  fileTreeRepoSyncMeta,
  fileTreeDisplayParts,
  setUploadedFilesCollapsedForTest(value) { uploadedFilesCollapsed = Boolean(value); },
  setChangesFolderCollapsedForTest(keys) { changesFolderCollapsed = new Set((keys || []).map(String)); },
  changesFolderCollapsedForTest() { return Array.from(changesFolderCollapsed).sort(); },
  changesRepoCollapsedForTest() { return Array.from(changesRepoCollapsed).sort(); },
  rawFileUrl,
  rawFileDownloadUrl,
  displayQuickAccessPath,
  expandQuickAccessPath,
  markOpenFileDiffUnavailable,
  focusPreferencesSearch,
  renderPreferencesPanelsForTest: renderPreferencesPanels,
  renderPaneTabStrips,
  paneTabDisplayContext,
  fileTabParentDisambiguators,
  ensureFileTabStateForItem,
  setDragSessionForTest(session) { dragSession = session; },
  pendingTabStripRenderForTest() { return pendingTabStripRender; },
  pendingPreferencesRenderForTest() { return pendingPreferencesRender; },
  setPendingPreferencesRenderForTest(value) { pendingPreferencesRender = Boolean(value); },
  preferencesScrollActiveUntilForTest() { return preferencesScrollActiveUntil; },
  setPreferencesScrollActiveUntilForTest(value) { preferencesScrollActiveUntil = Number(value) || 0; },
  notePreferencesScrollActivityForTest: notePreferencesScrollActivity,
  renderPanels,
  pendingPanelsRenderForTest() { return Boolean(pendingLayoutRender); },
  pendingLayoutRenderForTest() { return pendingLayoutRender; },
  setPendingPanelsRenderForTest(value) {
    pendingLayoutRender = value ? {previousActive: [], prevShape: '', nextShape: '', options: {}, reason: 'test', forceFull: true} : null;
  },
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
  runtimeIntervalDelay,
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
  startPaneDrag,
  paneDragPayload,
  endSessionDrag,
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
  dockviewTabDropWouldNoop,
  dockviewTabEdgeReorderIntent,
  windowStepButtonFromEvent,
  tmuxWindowRecords,
  tmuxWindowBarLabelMode,
  tmuxWindowBarHtml,
  tmuxWindowAgentKeyForTest: tmuxWindowAgentKey,
  tabMenuItems,
  sortTabItemsForMenu,
  setTabsMenuSortMode,
  tmuxPaneTabHtml,
  paneTabs,
  paneStateWithTabs,
  markdownSyntaxHtml,
  markdownTextWithSourceAnchors,
  markdownTaskLineEntries,
  markdownTextWithTaskLineToggled,
  markdownPreviewBlockedTagsForTest() { return Array.from(MARKDOWN_PREVIEW_BLOCKED_TAGS); },
  moveSessionToSlot,
  openFileEditorPane,
  onFileTreeRowClick,
  pathRelativeToDirectory,
  replaceHtmlPreservingScroll,
  pruneFileExplorerSelectionForRoot,
  selectFileTreePath,
  selectFileTreeRange,
  updateFileTreeSelectionFromClick,
  fileExplorerKeyIntent,
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
  fileExplorerExpandedForTest() { return Array.from(fileExplorerExpanded).sort(); },
  setFileExplorerRootForTest(path) { fileExplorerRoot = normalizeDirectoryPath(path); },
  setFileExplorerDirListingForTest(path, entries) {
    fileExplorerDirListingCache.set(normalizeDirectoryPath(path), {entries, at: Date.now()});
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
  infoBranchColumnWidthForTest() { return infoBranchColumnWidthPx; },
  infoDescColumnWidthForTest() { return infoDescColumnWidthPx; },
  setInfoBranchColumnWidthForTest: setInfoBranchColumnWidth,
  setInfoDescColumnWidthForTest: setInfoDescColumnWidth,
  resetInfoBranchColumnWidthForTest: resetInfoBranchColumnWidth,
  resetInfoDescColumnWidthForTest: resetInfoDescColumnWidth,
  bindInfoColumnResizersForTest: bindInfoColumnResizers,
  storageValueForTest(key) { return localStorage.getItem(key); },
  windowListenersForTest(type) { return [...(window.__listeners?.get?.(type) || [])]; },
  setSessionFilesPayloadForTest(payload) {
    fileExplorerSessionFilesPayload = payload;
  },
  setFileExplorerSessionFilesPayloadForTest(payload) {
    fileExplorerSessionFilesPayload = payload;
  },
  setSessionFilesCachePayloadForTest(session, payload) {
    fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {
      payload: {...payload, session},
      signature: sessionFilesPayloadSignatureForPayload(payload),
    });
  },
  changedFileOwnerSessionForPathForTest: changedFileOwnerSessionForPath,
  fileTreeChangedAncestorStatsForTest(payload) {
    return Array.from(fileTreeChangedAncestorStats(payload).entries()).map(([path, stats]) => [path, {...stats}]);
  },
  updateFileTreeGitStatusRowsForTest(rows) {
    const previousQuerySelectorAll = document.querySelectorAll;
    document.querySelectorAll = selector => selector === '.file-tree-row[data-path]' ? rows : previousQuerySelectorAll(selector);
    try {
      updateFileTreeGitStatusRows();
    } finally {
      document.querySelectorAll = previousQuerySelectorAll;
    }
  },
  setFileExplorerModeForTest(mode) {
    fileExplorerMode = normalizeFileExplorerMode(mode);
  },
  fileExplorerModeForTest() { return fileExplorerMode; },
  buildTabberTree,
  renderTabberTree,
  fileExplorerModeSwitcherHtml,
  normalizeFileExplorerMode,
  setTabberActivityForTest(payload) { tabberActivityPayload = payload; },
  setTabberSessionFilesForTest(session, files) { tabberSessionFilesCache.set(session, {files, loaded: true}); },
  tabberRenderedRowsForTest() {
    const {entries, entriesByDir} = buildTabberTree();
    fileExplorerTabberExpanded.clear();
    const addAll = (list, parent) => {
      for (const e of list || []) {
        if (e.kind !== 'dir') continue;
        const path = parent === '/' ? '/' + e.name : parent + '/' + e.name;
        fileExplorerTabberExpanded.add(path);
        addAll(entriesByDir.get(normalizeDirectoryPath(path)), path);
      }
    };
    addAll(entries, '/');
    const el = document.createElement('div');
    el.className = 'changes-groups';
    renderTabberTree(el);
    renderTabberTree(el);
    return Array.from(el.querySelectorAll('.file-tree-row')).map(row => ({
      type: row.dataset.tabberType || '',
      name: (row.querySelector('.file-tree-name') || {}).textContent || '',
      openFile: row.dataset.tabberOpenFile || '',
      repoRoot: row.dataset.tabberRepoRoot || '',
    }));
  },
  tabberRenderedNamesForTest() {
    // Expand every dir node so windows + panes render too, then read back the visible row labels.
    const {entries, entriesByDir} = buildTabberTree();
    fileExplorerTabberExpanded.clear();
    const addAll = (list, parent) => {
      for (const e of list || []) {
        if (e.kind !== 'dir') continue;
        const path = parent === '/' ? '/' + e.name : parent + '/' + e.name;
        fileExplorerTabberExpanded.add(path);
        addAll(entriesByDir.get(normalizeDirectoryPath(path)), path);
      }
    };
    addAll(entries, '/');
    const el = document.createElement('div');
    el.className = 'changes-groups';
    renderTabberTree(el);
    renderTabberTree(el);
    const rows = el.querySelectorAll('.file-tree-row');
    return Array.from(rows).map(row => {
      const name = row.querySelector('.file-tree-name');
      return name ? (name.textContent || '') : '';
    });
  },
  setSessionFilesSortModeForTest(mode) {
    sessionFilesSortMode = normalizeSessionFilesSortMode(mode);
  },
  sortedSessionFiles,
  runningAgentCount,
  updateDocumentTitle,
  documentTitleForTest() { return document.title; },
  setDocumentTitleNowForTest(value) { window.__yolomuxDocumentTitleNowMs = Number(value); },
  modalClassForTest() { return document.getElementById('modal').className; },
  modalTitleForTest() { return document.getElementById('modalTitle').textContent; },
  modalBodyHtmlForTest() { return document.getElementById('modalBody').innerHTML; },
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
  context.document.__listeners = documentListeners;
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

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : String(status),
    json: async () => payload,
  };
}

function flushAsyncWork() {
  return new Promise(resolve => setImmediate(resolve));
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

let __testPass = 0;
let __testFail = 0;
function test(label, fn) {
  // Per-test isolation: a failing assertion is recorded and the rest of the suite still runs, so one
  // failure no longer hides every later check. A pass/fail summary + non-zero exit print at the end.
  try {
    fn();
    __testPass++;
  } catch (error) {
    __testFail++;
    // Fail the process IMMEDIATELY on any failing test. The tail .finally also sets this, but it is
    // skipped whenever the trailing async block does not settle (an await that never resolves) \u2014 which
    // silently let red tests exit 0 and pass the cps gate. Setting it here does not depend on the tail.
    process.exitCode = 1;
    console.error(`\u2717 ${label}: ${(error && error.message) || error}`);
  }
}

test('t@1160', () => {
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
});

test('t@1174', () => {
  const api = loadYolomux('', ['1', '2', '3']);
  const layout = api.defaultLayoutForTest();
  assert.deepStrictEqual(canonical(layout), {
    tree: {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'right'}]},
    panes: {
      left: {tabs: ['1', '2'], active: '1'},
      right: {tabs: ['3'], active: '3'},
    },
  });
  const url = api.syncInitialLayoutUrlForTest();
  const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
  assert.equal(params.get('sessions'), '1,3');
  assert.equal(params.get('layout'), 'row@50(left,right)');
  assert.equal(params.get('tabs'), 'left:1,2;right:3');
});

test('t@1191', () => {
  const api = loadYolomux('', []);
  const layout = api.defaultLayoutForTest();
  assert.deepStrictEqual(canonical(layout.panes), {left: {tabs: [], active: null, placeholder: true}});
  const url = api.syncInitialLayoutUrlForTest();
  const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
  assert.equal(params.get('layout'), 'left');
  assert.equal(params.get('tabs'), 'left:__empty_pane__');
});

test('t@1201', () => {
  const api = loadYolomux('?sessions=3,2,1', ['1', '2', '3']);
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'right'}]},
    panes: {
      left: {tabs: ['3', '2'], active: '3'},
      right: {tabs: ['1'], active: '1'},
    },
  });
});

test('t@1212', () => {
  const api = loadYolomux('?keep=1');
  assert.equal(api.layoutFromParam('', ''), null, 'empty layout param falls back to default layout');
  assert.equal(api.layoutFromParam(null, ''), null, 'null layout param falls back to default layout');
  assert.equal(api.layoutFromParam('not-a-layout', ''), null, 'garbage layout param falls back to default layout');
  const slots = nestedSlots(api);
  const url = api.setLayoutSlotsForTest(slots);
  const params = parseUrl(url);
  assert.equal(params.get('sessions'), '6,1,3');
  assert.equal(params.get('layout'), 'row@37.5(left,col@62.5(slot1,slot2))');
  assert.equal(params.get('tabs'), 'left:5,6*;slot1:1;slot2:3');
  assert.equal(params.get('keep'), '1');

  const decoded = api.layoutFromParam(params.get('layout'), params.get('tabs'));
  assert.deepStrictEqual(canonical(api.serialize(decoded)), canonical(api.serialize(slots)));

  const duplicateAcrossSlots = api.layoutFromParam('row@0(left,slot1)', 'left:1;slot1:1,2*');
  assert.deepStrictEqual(canonical(api.serialize(duplicateAcrossSlots)), {
    tree: {split: 'row', pct: 5, children: [{slot: 'left'}, {slot: 'slot1'}]},
    panes: {
      left: {tabs: ['1'], active: '1'},
      slot1: {tabs: ['2'], active: '2'},
    },
  }, 'layout parser dedupes duplicate sessions across slots and clamps low split percentages');
  const highPctLayout = api.layoutFromParam('row@120(left,slot1)', 'left:1;slot1:2');
  assert.equal(api.serialize(highPctLayout).tree.pct, 95, 'layout parser clamps high split percentages');

  const reloaded = loadYolomux(`?${url.split('?')[1] || ''}`);
  assert.deepStrictEqual(canonical(reloaded.serialize(reloaded.currentSlots())), canonical(api.serialize(slots)));
});

test('t@1243', () => {
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
});

test('t@1270', () => {
  const api = loadYolomux('?sessions=3&layout=left&tabs=left:3,2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {
      left: {tabs: ['3', '2'], active: '3'},
    },
  });
});

test('t@1280', () => {
for (const legacyChangesToken of ['changes', '__changes__']) {
  const api = loadYolomux(`?layout=left&tabs=left:${legacyChangesToken}`, ['1']);
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['__files__'], active: '__files__'}},
  });
  assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes-only URLs restore Finder diff mode');
}
});

test('t@1289', () => {
for (const yoagentToken of ['yoagent', '__yoagent__', '__yosup__']) {
  const api = loadYolomux(`?sessions=${yoagentToken}&layout=left&tabs=left:${yoagentToken}`, ['1']);
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['__info__'], active: '__info__'}},
  });
  assert.equal(api.infoPanelSubTabForTest(), 'yoagent', `${yoagentToken} deep-link pre-selects the YO!agent sub-tab`);
}
});

test('t@1298', () => {
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
});

test('t@1313', () => {
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
  const agentInfo = {agents: [{kind: 'codex', transcript: '/tmp/codex.jsonl'}], selected_pane: {process_label: 'codex'}};
  api.setTranscriptInfoForTest('6', agentInfo);
  assert.equal(api.terminalTabLabel('6', agentInfo), 'Term', 'terminal tab visible label is static');
  assert.equal(api.terminalTabTitle('6', agentInfo), 'terminal: codex', 'terminal tab title keeps the active process detail');
  const agentToolbar = api.panelControlsHtml('6');
  assert.ok(agentToolbar.includes('>Term</button>'), 'terminal top row shows the generic terminal label');
  assert.equal(agentToolbar.includes('>codex</button>') || agentToolbar.includes('>Codex</button>'), false, 'terminal top row does not render the agent marker as the terminal tab text');
  const sessionSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(sessionSource.includes('panel-agent-slot'), false, 'DOIT.57 T1: the agent badge is removed from the Info Bar (the window buttons carry the agent fact)');
  assert.equal(/function sessionAgentBadgeHtml/.test(sessionSource), false, 'DOIT.57 T1: the unused agent-badge helper is gone');
  assert.ok(/function terminalTabDisplayLabel\(session, info\)\s*\{\s*return 'Term';\s*\}/.test(sessionSource), 'DOIT.56 N3: terminal tab visible label is always static');
  assert.ok(/function terminalTabTitle\(session, info\)[\s\S]*terminalTabDetailLabel\(session, info\)/.test(sessionSource), 'DOIT.56 N3: terminal tab title still uses process/window detail');
});

test('t@1367', () => {
  // Dockview round-trip parity: slots -> Dockview JSON -> slots is idempotent on the compacted
  // form. The pane rewrite's bidirectional sync hinges on dockviewJsonFromLayoutSlots and
  // layoutSlotsFromDockviewJson being exact inverses (up to compaction); any drift silently
  // reshuffles panes/tabs on a Dockview-driven relayout, and is the single most fragile invariant
  // in the rewrite. See docs/GUI_SPEC.md (pane layout model).
  const api = loadYolomux('', ['1', '2', '3', '4']);
  const base = api.emptyLayoutSlots();
  base[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  base.left = api.paneStateWithTabs(['__files__'], '__files__');
  base.slot1 = api.paneStateWithTabs(['1', '2', '3'], '2');
  // Binary-tree shapes round-trip EXACTLY (tree + panes). Wall uses a flat N-ary split, which
  // Dockview's strictly-binary grid cannot represent — it re-nests as right-leaning binary splits
  // on the way back. That rebalances the split TREE but must never move a tab to the wrong pane,
  // so for every shape the pane->tabs assignment is the invariant that must hold.
  const binaryTreeModes = new Set(['single', 'split', 'grid']);
  for (const mode of ['single', 'split', 'grid', 'wall']) {
    api.setLayoutSlotsForTest(base);
    api.setFocusedPanelItem('2');
    api.applyLayoutMode(mode);
    const slots = api.currentSlots();
    const compacted = api.serialize(api.compactLayoutSlots(slots));
    const roundTripped = api.serialize(api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(slots)));
    assert.deepStrictEqual(
      canonical(roundTripped.panes),
      canonical(compacted.panes),
      `slots -> dockview JSON -> slots preserves every pane's tabs + active tab (${mode} layout)`
    );
    if (binaryTreeModes.has(mode)) {
      assert.deepStrictEqual(
        canonical(roundTripped.tree),
        canonical(compacted.tree),
        `binary-tree layout round-trips the split tree exactly (${mode} layout)`
      );
      assert.equal(
        api.layoutSlotsSignature(api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(slots))),
        api.layoutSlotsSignature(api.compactLayoutSlots(slots)),
        `binary-tree layout round-trips the full normalized signature (${mode} layout)`
      );
    }
  }
});

test('t@1410', () => {
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
  assert.equal(api.TAB_TYPES.some(type => type.key === 'file-preview'), false, 'side-preview file tabs are removed; preview uses the pop-out window');
  const duplicateParents = api.fileTabParentDisambiguators([
    api.fileEditorItemFor('/repo/app/src/config.json'),
    api.fileEditorItemFor('/repo/lib/src/config.json'),
    api.fileEditorItemFor('/repo/app/README.md'),
  ]);
  assert.deepStrictEqual([...duplicateParents.entries()].map(entry => entry.join('=')).sort(), [
    '/repo/app/src/config.json=app/src',
    '/repo/lib/src/config.json=lib/src',
  ], 'duplicate file tabs use the shortest unique parent suffix');
  const displayContext = api.paneTabDisplayContext([
    api.fileEditorItemFor('/repo/app/src/config.json'),
    api.fileEditorItemFor('/repo/lib/src/config.json'),
  ]);
  assert.equal(displayContext.fileParentLabels.get('/repo/app/src/config.json'), 'app/src');
  assert.ok(api.fileEditorPaneTabHtml(api.fileEditorItemFor('/repo/app/src/config.json'), {parentLabel: 'app/src'}).includes('file-tab-parent'), 'duplicate file tabs render the parent suffix in a muted slot');
  const loadingState = api.ensureFileTabStateForItem(api.fileEditorItemFor('/repo/app/src/pending.txt'));
  assert.equal(loadingState.kind, 'file', 'path-backed tabs use a file placeholder type, not literal "loading"');
  assert.equal(loadingState.loading, true, 'path-backed tabs still expose loading status until the file is fetched');
  const paneTabSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/bindDelayedSessionPopover\(tab, popover[\s\S]*maybeLoadFileTabForPopover\(tab, session\)/.test(paneTabSource), 'file tab popovers load stale/missing file state through the shared hover popover onOpen hook');
  assert.ok(/function loadFileEditorState[\s\S]*if \(panel\) renderFileEditorPanel\(panel, item\)/.test(paneTabSource), 'inactive file-tab hover loads can fetch without a mounted editor panel');

  assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-heading-1'));
  assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-bold'));
  const anchoredMarkdown = api.markdownTextWithSourceAnchors('| A | B |\n|---|---|\n| 1 | 2 |\n\n---');
  assert.equal(anchoredMarkdown.includes('markdown-source-anchor'), false, 'Markdown source is not mutated before marked parses GFM tables and rules');
  assert.ok(anchoredMarkdown.includes('|---|---|'), 'GFM table delimiter rows stay intact before parsing');
  assert.ok(anchoredMarkdown.includes('\n---'), 'thematic breaks stay intact before parsing');
  assert.deepStrictEqual(canonical(api.markdownTaskLineEntries('- [ ] Open\n- [x] Done\ntext')), [
    {line: 1, checked: false},
    {line: 2, checked: true},
  ], 'Markdown task lines are detected in source order for Preview checkbox binding');
  assert.equal(api.markdownTextWithTaskLineToggled('- [ ] Open\n- [x] Done', 1, true), '- [x] Open\n- [x] Done', 'Preview can toggle an unchecked task line to checked source');
  assert.equal(api.markdownTextWithTaskLineToggled('- [ ] Open\n- [x] Done', 2, false), '- [ ] Open\n- [ ] Done', 'Preview can toggle a checked task line to unchecked source');
  assert.equal(api.markdownTextWithTaskLineToggled('plain text', 1, true), null, 'Preview task toggles reject non-task source lines');
  assert.equal(api.markdownPreviewBlockedTagsForTest().includes('input'), false, 'Markdown sanitizer preserves checkbox inputs for task-list Preview controls');
  assert.ok(/bindMarkdownTaskCheckboxes\(container, text, markdownPath\)/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Markdown Preview wires rendered task checkboxes after parsing');
  assert.ok(/tagName === 'input'[\s\S]*getAttribute\('type'\)[\s\S]*checkbox/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Markdown sanitizer removes non-checkbox inputs while allowing task checkboxes');
  const editorCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(editorCss.includes('.markdown-body th { background: var(--panel2); }'), 'Markdown table headers get a readable preview background');
  assert.ok(editorCss.includes('.markdown-body hr { border: 0; border-top: 1px solid var(--line); margin: 12px 0; }'), 'Markdown thematic breaks render as preview rules');
  assert.ok(editorCss.includes('.markdown-body li.task-list-item > input[type="checkbox"]'), 'Markdown Preview task checkboxes have visible interactive styling');
  assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body[\s\S]*background:\s*#ffffff[\s\S]*color:\s*#111827/.test(editorCss), 'vanilla preview uses a neutral white email-friendly surface');
  assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body h1[\s\S]*color:\s*#111827[\s\S]*background:\s*transparent/.test(editorCss), 'vanilla preview headings do not use YOLOmux accent coloring');
  assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body a[\s\S]*color:\s*#0645ad/.test(editorCss), 'vanilla preview links use a conventional blue instead of scheme colors');
  assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body pre code \*[\s\S]*color:\s*inherit !important/.test(editorCss), 'vanilla preview strips syntax token colors inside code blocks');
  assert.ok(/\.markdown-body pre code \.hljs-keyword,[\s\S]*color:\s*var\(--code-keyword\) !important/.test(editorCss), 'themed markdown preview owns Highlight.js keyword color instead of relying on the external stylesheet');
  assert.ok(/\.markdown-body pre code \.hljs-string,[\s\S]*color:\s*var\(--code-string\) !important/.test(editorCss), 'themed markdown preview owns Highlight.js string color');
  const fileExplorerSource = (fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8'));
  const highlightStart = fileExplorerSource.indexOf('function codeMirrorHighlightExtension');
  const highlightEnd = fileExplorerSource.indexOf('function codeMirrorThemeExtension');
  const highlightBlock = fileExplorerSource.slice(highlightStart, highlightEnd);
  assert.ok(highlightBlock.includes("keyword: 'var(--code-keyword)'"), 'CodeMirror keywords use the same parent syntax variable as Preview');
  assert.ok(highlightBlock.includes("control: 'var(--code-control)'"), 'CodeMirror control keywords use the same parent syntax variable as Preview');
  assert.ok(highlightBlock.includes("string: 'var(--code-string)'"), 'CodeMirror strings use the same parent syntax variable as Preview');
  assert.ok(highlightBlock.includes("function: 'var(--code-function)'"), 'CodeMirror functions use the same parent syntax variable as Preview');
  assert.equal(highlightBlock.includes('scheme.syntax'), false, 'CodeMirror highlighting does not duplicate literal syntax colors outside the shared CSS variables');
  assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-comment'));
  assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-variable'));
  assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-attr'));
  assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-constant'));
  assert.ok(api.simpleCodeSyntaxHtml('python', 'class ToolParser:\n    def extract_tool_calls(self, model_output: str) -> DeltaMessage | None:').includes('<span class="code-type">ToolParser</span>'), 'Python class declarations use the shared type token');
  assert.ok(api.simpleCodeSyntaxHtml('python', 'class ToolParser:\n    def extract_tool_calls(self, model_output: str) -> DeltaMessage | None:').includes('<span class="code-type">str</span>'), 'Python annotations use the shared type token');
  assert.ok(api.simpleCodeSyntaxHtml('rust', 'pub struct Tool { pub name: String, }').includes('<span class="code-control">pub</span>'), 'Rust pub uses the shared control token');
  assert.ok(api.simpleCodeSyntaxHtml('rust', 'pub struct Tool { pub name: String, }').includes('<span class="code-type">String</span>'), 'Rust field types use the shared type token');

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
  assert.equal(api.codeMirrorApiIsUsable({Compartment: class {}, EditorState: {create() {}, readOnly: {of() {}}}, EditorView: {theme() {}, editable: {of() {}}, contentAttributes: {of() {}}}, keymap: {of() {}}, drawSelection() {}, highlightActiveLine() {}, search() {}, openSearchPanel() {}}), true, 'CodeMirror API validation accepts critical editor/search exports');
  assert.equal(api.codeMirrorApiIsUsable({Compartment: class {}, EditorState: {create() {}, readOnly: {of() {}}}, EditorView: {theme() {}, editable: {of() {}}}, keymap: {of() {}}, drawSelection() {}, highlightActiveLine() {}, search() {}, openSearchPanel() {}}), false, 'CodeMirror API validation rejects bundles without line-wrapping support');
  assert.equal(api.codeMirrorApiIsUsable({EditorState: {create() {}}, EditorView: {theme() {}}}), false, 'CodeMirror API validation rejects partial bundles');
  const codeMirrorBundlePackage = JSON.parse(fs.readFileSync('prototypes/codemirror-bundle/package.json', 'utf8'));
  const codeMirrorBundleLock = JSON.parse(fs.readFileSync('prototypes/codemirror-bundle/package-lock.json', 'utf8'));
  const codeMirrorDirectVersions = {
    '@codemirror/commands': '6.10.3',
    '@codemirror/lang-css': '6.3.1',
    '@codemirror/lang-html': '6.4.11',
    '@codemirror/lang-javascript': '6.2.5',
    '@codemirror/lang-json': '6.0.2',
    '@codemirror/lang-markdown': '6.5.0',
    '@codemirror/lang-python': '6.2.1',
    '@codemirror/lang-rust': '6.0.2',
    '@codemirror/lang-xml': '6.1.0',
    '@codemirror/lang-yaml': '6.1.3',
    '@codemirror/language': '6.12.3',
    '@codemirror/legacy-modes': '6.5.3',
    '@codemirror/merge': '6.12.1',
    '@codemirror/search': '6.7.0',
    '@codemirror/state': '6.6.0',
    '@codemirror/view': '6.43.0',
    'codemirror': '6.0.2',
    'esbuild': '0.28.0',
  };
  assert.deepEqual(codeMirrorBundlePackage.dependencies, codeMirrorDirectVersions, 'vendored CodeMirror manifest records exact direct versions');
  assert.deepEqual(codeMirrorBundleLock.packages[''].dependencies, codeMirrorDirectVersions, 'vendored CodeMirror lockfile root matches the exact direct versions');
  assert.equal(codeMirrorBundleLock.packages['node_modules/@codemirror/autocomplete'].version, '6.20.2', 'CodeMirror transitive autocomplete version is recorded');
  assert.equal(codeMirrorBundleLock.packages['node_modules/@codemirror/lint'].version, '6.9.6', 'CodeMirror transitive lint version is recorded');
  assert.equal(codeMirrorBundleLock.packages['node_modules/@lezer/highlight'].version, '1.2.3', 'Lezer highlight version is recorded');
  assert.equal(codeMirrorBundleLock.packages['node_modules/style-mod'].version, '4.1.3', 'style-mod version is recorded');
  assert.equal(codeMirrorBundleLock.packages['node_modules/w3c-keyname'].version, '2.2.8', 'w3c-keyname version is recorded');
  assert.ok(fs.readFileSync('prototypes/codemirror-entry.js', 'utf8').includes('cd prototypes/codemirror-bundle'), 'CodeMirror rebuild instructions use the checked-in bundle manifest');
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
});

test('t@1645', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(
    source.includes('if (panel._cmGeneration !== generation) return null;'),
    'stale CodeMirror renders no-op instead of reporting a load failure',
  );
  assert.ok(
    source.includes('if (loaded === false) renderFileEditorRawPane(rawPane, path, state.content);'),
    'raw editor fallback is only rendered for real CodeMirror failures',
  );
});

test('t@1657', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const withoutStorageHelpers = source
    .replace(/function storageGet\([\s\S]*?\n}\n\nfunction storageSet/, 'function storageSet')
    .replace(/function storageSet\([\s\S]*?\n}\n\nfunction safeJsonParse/, 'function safeJsonParse');
  assert.equal(withoutStorageHelpers.includes('localStorage.'), false, 'browser storage access goes through storageGet/storageSet helpers');
});

test('t@1665', () => {
  const api = loadYolomux('', ['1']);
  assert.equal(api.editorPreviewModeAvailable('/home/test/README.md'), true);
  assert.equal(api.editorPreviewModeAvailable('/home/test/index.html'), true);
  assert.equal(api.editorPreviewModeAvailable('/home/test/app.py'), false);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('function sanitizeMarkdownPreviewHtml'), 'Markdown previews pass through a sanitizer');
  assert.ok(source.includes('MARKDOWN_PREVIEW_BLOCKED_TAGS'), 'Markdown sanitizer blocks executable/embedded HTML tags');
  assert.equal(source.includes('container.innerHTML = window.marked.parse'), false, 'Markdown previews are not inserted with unsanitized marked HTML');
  // previews still insert ONLY sanitized nodes — sanitize into a fragment, linkify bare URLs
  // on that already-sanitized fragment (so linkify can't reintroduce unsafe markup), then replaceChildren.
  assert.ok(source.includes('const frag = sanitizeMarkdownPreviewHtml(html);'), 'Markdown previews sanitize into a fragment');
  assert.ok(source.includes('linkifyBareUrls(frag);'), 'Markdown previews linkify bare URLs on the sanitized fragment');
  assert.ok(source.includes('container.replaceChildren(frag);'), 'Markdown previews replace DOM with the sanitized (+linkified) nodes');
  assert.ok(/function linkifyBareUrls[\s\S]*markdownPreviewUrlAllowed\(url, 'a'\)/.test(source), 'linkifyBareUrls only links URLs that pass markdownPreviewUrlAllowed (safe schemes)');
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
  const fileHistory = [
    {ref: 'abc123def456', short: 'abc123d', subject: 'update file'},
    {ref: '987654321000', short: '9876543', subject: 'create file'},
  ];
  const trackedHistoryState = extra => ({kind: 'text', gitRoot: '/repo/app', gitTracked: true, gitHasHistory: true, gitHistory: fileHistory, ...extra});
  api.setFileEditorViewMode(changedPath, 'edit', changedItem);
  api.updateFileEditorDiffButton(diffButton, changedPath, trackedHistoryState({diffLoaded: true, diff: ''}), changedItem);
  assert.equal(diffButton.hidden, true, 'unchanged files do not show a Diff button');
  api.updateFileEditorDiffButton(diffButton, changedPath, trackedHistoryState({diffLoaded: true, diff: 'diff --git a/a b/a'}), changedItem);
  assert.equal(diffButton.hidden, false, 'changed repo files show a Diff button');
  assert.equal(diffButton.disabled, false, 'changed repo Diff button is clickable');
  api.setFileEditorViewMode(changedPath, 'diff', changedItem);
  api.updateFileEditorDiffButton(diffButton, changedPath, trackedHistoryState({diffLoading: true}), changedItem);
  assert.equal(diffButton.hidden, false, 'active diff view keeps a loading Diff button while refs load');
  assert.equal(diffButton.disabled, false, 'active diff view keeps Exit diff clickable while refs load');
  const codePath = '/repo/app/app.py';
  const codeItem = api.fileEditorItemFor(codePath);
  api.setFileEditorViewMode(codePath, 'diff', codeItem);
  api.updateFileEditorDiffButton(diffButton, codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem);
  assert.equal(diffButton.hidden, false, 'code files stuck in diff still show an Exit diff button');
  assert.equal(diffButton.disabled, false, 'Exit diff stays clickable even when no code-file diff is available');
  // #25: a .py (non-md/html) in NORMAL mode with no diff loaded yet still offers a clickable Diff
  // toggle, which lazily loads the diff on click (it only hides once a load confirms there is none).
  api.setFileEditorViewMode(codePath, 'edit', codeItem);
  api.updateFileEditorDiffButton(diffButton, codePath, trackedHistoryState({}), codeItem);
  assert.equal(diffButton.hidden, false, '#25: a code file with no diff loaded yet still offers a Diff toggle');
  assert.equal(diffButton.disabled, false, '#25: the Diff toggle is clickable so it can lazily load the diff');
  // A file git does not track (untracked / outside any repo) has no committed version to diff against,
  // so the Diff button stays hidden regardless of view mode (gitTracked falsey).
  api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', gitTracked: false}, codeItem);
  assert.equal(diffButton.hidden, true, 'untracked files never show a Diff button');
  assert.equal(api.fileEditorGitActionControlsVisible(codePath, {kind: 'text', gitTracked: false}, codeItem), false, 'non-git files show neither Diff nor Blame');
  api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', gitTracked: true, gitHasHistory: true, gitHistory: fileHistory}, codeItem);
  assert.equal(diffButton.hidden, true, 'files with no repo root never show a Differ button even if stale history metadata exists');
  assert.equal(diffButton.disabled, true, 'hidden no-repo Differ button is not clickable');
  assert.equal(api.fileEditorGitActionControlsVisible(codePath, {kind: 'text', gitTracked: true, gitHasHistory: false}, codeItem), false, 'creation-only files show neither Diff nor Blame');
  assert.equal(api.fileEditorGitActionControlsVisible(codePath, {kind: 'text', gitTracked: true, gitHasHistory: true}, codeItem), false, 'stale history booleans without file-level history show neither Diff nor Blame');
  assert.equal(api.fileEditorGitActionControlsVisible(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), false, 'files with history but no diff hide Diff after the no-diff result is known');
  assert.equal(api.fileEditorBlameControlsVisible(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), true, 'clean files with useful history still offer Blame in normal edit mode');
  assert.equal(api.fileEditorGitActionControlsVisible(codePath, trackedHistoryState({}), codeItem), true, 'files with history and unknown diff state show the Diff control');
  const blameButton = new TestElement('blame-button');
  api.setFileEditorViewMode(codePath, 'edit', codeItem);
  api.updateFileEditorBlameButton(blameButton, codePath, trackedHistoryState({}), codeItem);
  assert.equal(blameButton.hidden, false, 'Blame is visible in normal edit mode for files with useful history');
  assert.equal(blameButton.disabled, false, 'Blame is clickable only in normal edit mode');
  api.updateFileEditorBlameButton(blameButton, codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem);
  assert.equal(blameButton.hidden, false, 'Blame remains visible for clean files with useful history');
  assert.equal(blameButton.disabled, false, 'Blame remains clickable in normal edit mode after Diff confirms a clean file');
  api.setFileEditorViewMode(codePath, 'diff', codeItem);
  api.updateFileEditorBlameButton(blameButton, codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem);
  assert.equal(blameButton.hidden, false, 'Blame stays visible for files with useful history');
  assert.equal(blameButton.disabled, true, 'Blame is not clickable in diff mode');
  const mdPath = '/repo/app/README.md';
  const mdItem = api.fileEditorItemFor(mdPath);
  api.setFileEditorViewMode(mdPath, 'split', mdItem);
  api.updateFileEditorBlameButton(blameButton, mdPath, trackedHistoryState({}), mdItem);
  assert.equal(blameButton.disabled, true, 'Blame is not clickable in split mode');
  api.setFileEditorViewMode(mdPath, 'preview', mdItem);
  api.updateFileEditorBlameButton(blameButton, mdPath, trackedHistoryState({}), mdItem);
  assert.equal(blameButton.disabled, true, 'Blame is not clickable in preview mode');
  api.setFileEditorViewMode(codePath, 'diff', codeItem);
  assert.equal(api.fileEditorGitActionControlsVisible(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), true, 'active diff mode keeps the paired git controls visible so Exit diff remains available');
  // (RECURRING): pressing DIFF on a git-tracked file with useful history but a CLEAN working tree
  // (empty HEAD-vs-working diff) must NOT force-exit diff mode — the FROM/TO sha ref picker has to stay
  // reachable so the user can compare ARBITRARY refs. A clean README.md (many commits, no working changes)
  // used to snap back to edit and hide the picker entirely. codePath is in diff mode here.
  assert.equal(api.diffModeShouldFallBackToEdit(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), false, 'a clean file WITH useful git history stays in diff mode so the FROM/TO ref picker stays reachable');
  assert.equal(api.diffModeShouldFallBackToEdit(codePath, {kind: 'text', gitRoot: '/repo/app', gitTracked: true, gitHasHistory: false, gitHistory: [], diffLoaded: true, diff: ''}, codeItem), true, 'a file WITHOUT useful history still falls back to edit when its diff is empty');
  assert.equal(api.diffModeShouldFallBackToEdit(codePath, {kind: 'text', gitTracked: true, gitHasHistory: true, gitHistory: fileHistory, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem), true, 'files outside a repo cannot remain in Differ mode even with stale useful-history metadata');
  assert.equal(api.diffModeShouldFallBackToEdit(codePath, trackedHistoryState({diffLoaded: true, diff: 'diff --git a/a b/a'}), codeItem), false, 'a file with a real diff stays in diff mode (control)');
  assert.ok(/async function enterFileEditorDiffMode[\s\S]*!fileStateHasRepo\(path, state\)[\s\S]*fileStateHasRepo\(path, current\) && \(openFileDiffAvailable\(current\) \|\| fileStateHasUsefulGitHistory\(current\)\)[\s\S]*setFileEditorViewMode\(path, 'diff', item\)/.test(source), 'pressing Diff keeps clean files with useful history in diff mode after the default diff load finishes, but only for files under a repo');
  api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', gitTracked: false, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem);
  assert.equal(diffButton.hidden, true, 'untracked files stay diff-button-free even in diff mode');
  const diffExpandButton = new TestElement('diff-expand-button');
  api.setFileEditorViewMode(codePath, 'edit', codeItem);
  api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem);
  assert.equal(diffExpandButton.hidden, true, 'Expand unchanged is hidden outside Diff mode');
  api.setFileEditorViewMode(codePath, 'diff', codeItem);
  api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoading: true}, codeItem);
  assert.equal(diffExpandButton.hidden, true, 'Expand unchanged is hidden while the diff is still loading');
  api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoaded: true, diff: ''}, codeItem);
  assert.equal(diffExpandButton.hidden, true, 'Expand unchanged is hidden when the loaded diff has no unchanged-context folds to control');
  api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem);
  assert.equal(diffExpandButton.hidden, false, 'Expand unchanged is shown only for an active loaded diff');
  assert.equal(diffExpandButton.disabled, false, 'Expand unchanged is clickable for an active loaded diff');
  assert.equal(diffExpandButton.attributes['aria-pressed'], 'false', 'Expand unchanged reflects the persisted toggle state');
  assert.ok(/updateFileEditorDiffExpandButton\(diffExpandButton, path, state, item\);\s*if \(popoutPreviewButton\)/.test(source), 'rendering leaves Expand unchanged visibility owned by its dedicated updater');
  const noHistoryPath = '/repo/app/DOIT.37.md';
  api.setOpenFileStateForTest(noHistoryPath, {kind: 'text', gitTracked: true, gitHasHistory: false, gitHistory: []});
  assert.deepStrictEqual([...api.fileDiffRefHistoryItems(noHistoryPath)], [], 'file editor ref history is empty when the file has no file-level history');
  assert.deepStrictEqual([...api.diffRefFromSuggestions('/repo/app', noHistoryPath)], [], 'file editor FROM picker does not show unrelated repo history for a no-history file');
  const historyPath = '/repo/app/src/history.md';
  api.setOpenFileStateForTest(historyPath, {
    kind: 'text',
    gitTracked: true,
    gitHasHistory: true,
    gitHistory: [
      {ref: 'abc123def456', short: 'abc123d', subject: 'touch history.md'},
      {ref: '987654321000', short: '9876543', subject: 'create history.md'},
    ],
  });
  assert.deepStrictEqual(Array.from(api.diffRefFromSuggestions('/repo/app', historyPath)).map(item => item.short), ['HEAD', 'abc123d', '9876543'], 'file editor FROM picker uses file-specific history when it exists');
  assert.ok(api.diffRefControlsHtml({compact: true, repo: '/repo/app', path: historyPath}).includes(`data-diff-ref-path="${historyPath}"`), 'editor FROM/TO controls carry the file path for file-scoped history');
  assert.notEqual(
    api.codeMirrorConfigSignature(codePath, {mode: 'diff', expand: false}),
    api.codeMirrorConfigSignature(codePath, {mode: 'diff', expand: true}),
    'Diff expand/collapse changes the CodeMirror signature so the merge view rebuilds',
  );
  const editConfigSignature = JSON.parse(api.codeMirrorConfigSignature(codePath, {mode: 'edit'}));
  const diffConfigSignature = JSON.parse(api.codeMirrorConfigSignature(codePath, {mode: 'diff'}));
  assert.equal(Object.prototype.hasOwnProperty.call(editConfigSignature, 'wrap'), false, 'word wrap is live-reconfigured, not part of the CodeMirror rebuild signature');
  assert.equal(Object.prototype.hasOwnProperty.call(editConfigSignature, 'lineNumbers'), false, 'line numbers are live-reconfigured, not part of the CodeMirror rebuild signature');
  assert.equal(Object.prototype.hasOwnProperty.call(diffConfigSignature, 'wrap'), false, 'word wrap cannot force a diff editor rebuild');
  api.setFileEditorViewMode(codePath, 'edit', codeItem);
});

test('t@1835', () => {
  const api = loadYolomux('', ['1']);
  assert.equal(api.runtimeIntervalDelay(3000), 3000);
  assert.equal(api.runtimeIntervalDelay(1250), 1250);
  assert.equal(api.runtimeIntervalDelay(0), 1);
});

test('t@1842', () => {
  const api = loadYolomux('', ['1']);
  assert.deepStrictEqual(canonical(api.codeMirrorSearchMatches('foo bar foo', 'foo')), [
    {from: 0, to: 3},
    {from: 8, to: 11},
  ]);
  assert.equal(api.codeMirrorSearchMatchSummary('foo bar foo', 'foo', {from: 8, to: 11, head: 11}).text, '2/2');
  assert.equal(api.codeMirrorSearchMatchSummary('Foo foo', 'foo', {head: 0}, {caseSensitive: true}).text, '1/1');
  assert.equal(api.codeMirrorSearchMatchSummary('food foo', 'foo', {head: 0}, {wholeWord: true}).text, '1/1');
  assert.equal(api.codeMirrorSearchMatchSummary('abc', '[', {head: 0}, {regexp: true}).text, '0/0');
  assert.deepStrictEqual(canonical(api.codeMirrorSearchMatches('vllm_rust: v0.22.0 0b3ba (/home/keivenc/dynamo/vllm-0.22.0)', '/home/keivenc/dynamo/vllm-0.22.0')), [
    {from: 26, to: 58},
  ], 'absolute paths with dots and hyphens are searched as literal text');
});

test('t@1857', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function renderFileEditorPanel(');
  const end = source.indexOf('function loadFileEditorState(', start);
  assert.ok(start > 0 && end > start, 'could not locate renderFileEditorPanel body');
  assert.equal(source.slice(start, end).includes('.focus('), false, 'renderFileEditorPanel must not steal focus during refresh renders');
  assert.ok(source.includes('captureFileEditorPanelViewStateForItem(previous)'), 'switching pane tabs captures the outgoing CodeMirror viewport');
  assert.ok(source.includes('const scrollTop = scrollDOM?.scrollTop || 0;'), 'external CodeMirror reload preserves scrollTop');
  assert.ok(source.includes('view.requestMeasure({write: restoreScroll});'), 'external CodeMirror reload restores scroll after the document update');
  assert.ok(source.includes('view.requestMeasure'), 'CodeMirror viewport restore waits for a measured layout frame');
});

test('t@1869', () => {
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
  assert.equal(source.includes('let sessionFilesRequestId = 0;'), false, 'standalone Changes request id is removed');
  assert.ok(source.includes('const fileExplorerSessionFilesGuard = makeGenerationGuard();'), 'Finder diff fetches have their own stale-response generation guard');
  assert.ok(source.includes('const requestIsCurrent = fileExplorerSessionFilesGuard.begin();'), 'Finder diff fetches reject stale responses through the shared guard');
  assert.ok(source.includes('function activeChangesControl'), 'Finder diff renders can detect active controls');
  assert.ok(source.includes('!activeChangesControl(panel)'), 'background Changes renders preserve active selects and ref controls');
  assert.ok(source.includes('function sessionFilesRenderOptions'), 'modified-file fetch rendering distinguishes silent polls from explicit user refreshes');
  assert.ok(source.includes('const loadingPromise = (async () => {'), 'editor file loading keeps a promise handle for guarded cleanup');
  assert.ok(source.includes('if (current?.loadingPromise === loadingPromise) delete current.loadingPromise;'), 'editor file loading clears stale loading promises after failure or success');
  assert.ok(source.includes('const activitySummaryGuard = makeGenerationGuard();'), 'activity summary refreshes carry a stale-response generation guard');
  assert.ok(source.includes('if (activitySummaryRefreshing && options.force !== true) return;'), 'activity summary polling skips overlapping non-forced refreshes');
  assert.ok(source.includes('let transcriptMetaRefreshPromise = null;'), 'metadata refreshes keep one in-flight promise');
  assert.ok(source.includes('if (transcriptMetaRefreshPromise) return transcriptMetaRefreshPromise;'), 'metadata refreshes dedupe overlapping loads');
  assert.ok(source.includes('transcriptMetaLoading = true;'), 'metadata refreshes expose a loading state');
  assert.ok(source.includes('infoMetadataLoadingHtml()'), 'YO!info renders an explicit repo-metadata loading state');
  assert.ok(source.includes('const notificationLastSentLimit = 512;'), 'notification signature cache has a bounded size');
  assert.ok(source.includes('setLimitedMapEntry(notificationLastSent, key, now, notificationLastSentLimit);'), 'notification signatures use the shared bounded-map helper');
  assert.ok(source.includes('existing?.delay === normalizedDelay'), 'runtime intervals keep their timer phase when refresh delays are unchanged');
  assert.ok(/async function boot\(\)[\s\S]*?await loadAutoStatuses\(\);\s*bindClipboardPaste\(\);/.test(source), 'image paste binding is installed during boot and does not depend on background auto-status refresh');
  // C12 F3: terminal fit scheduling collapsed from rAF + 80ms + 250ms (three fits) to one rAF + a single
  // trailing fit; the redundant middle timer (fitFinalTimer) is gone.
  assert.equal(source.includes('item.fitFinalTimer'), false, 'C12 F3: the redundant third fit timer is removed');
  assert.ok(/function scheduleFit[\s\S]*?requestAnimationFrame\([\s\S]*?item\.fitTimer = setTimeout/.test(source), 'C12 F3: fit scheduling is one rAF plus a single trailing timeout');
  assert.equal(source.includes('esm.sh'), false, 'CodeMirror loading never falls back to a third-party CDN');
  assert.ok(source.includes('CodeMirror local bundle is unavailable or incomplete'), 'CodeMirror loading reports local bundle failures clearly');
  assert.ok(source.includes('maybeHandleServerVersionChange(transcriptMeta.server_version)'), 'the metadata poll checks the live server version');
  // #39: the new-session picker greys an installed-but-logged-out agent and names its login command;
  // the metadata poll refreshes agentAuth so it re-enables after the user logs in.
  assert.ok(/function agentLoggedIn\(agent\)[\s\S]*entry\.logged_in === true/.test(source), '#39: agentLoggedIn reads the per-agent logged_in flag');
  assert.ok(source.includes('const loggedOut = available && !agentLoggedIn(agent);'), '#39: the new-session picker computes a logged-out state per agent');
  assert.ok(/disabled: readOnlyMode \|\| !available \|\| loggedOut \|\| capped/.test(source), '#39: a logged-out agent is disabled in the picker');
  assert.ok(/loggedOut[\s\S]*?t\('menu\.tmux\.runLogin', \{command: agentLoginCommand\(agent\)\}\)/.test(source), '#39: a logged-out agent shows its login command as the menu detail (via t())');
  assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['menu.tmux.runLogin'], 'Run {command}', '#39/#121: the login-command detail renders "Run <command>" in English');
  assert.ok(source.includes('if (transcriptMeta.agentAuth) agentAuth = transcriptMeta.agentAuth;'), '#39: the metadata poll refreshes agent login status');
  // #41: the frontend mirrors the server's auto backend resolution (codex -> claude -> deterministic)
  // so the chat input enables to match what the backend will run, and defaults to auto.
  assert.ok(/function yoagentResolvedBackend\(\)[\s\S]*?for \(const agent of \['codex', 'claude'\]\)[\s\S]*?availableAgents\.has\(agent\) && agentLoggedIn\(agent\)/.test(source), '#41: yoagentResolvedBackend prefers codex then claude among logged-in agents');
  assert.ok(source.includes("initialSetting('yoagent.backend', 'auto')"), '#41: the YO!agent backend default is auto');
  assert.ok(/function yoagentChatEnabled\(\)[\s\S]*?yoagentResolvedBackend\(\)/.test(source), '#41: chat-enabled tracks the resolved backend');
  assert.ok(/maybeHandleServerVersionChange[\s\S]*serverVersion === bootstrap\.version[\s\S]*boolSetting\('general\.reload_on_update'/.test(source), 'server-version reload is gated on the boot version and the reload_on_update preference');
  assert.ok(/maybeHandleServerVersionChange[\s\S]*boolSetting\('general\.reload_on_update_auto'[\s\S]*reloadIsSafe\(\)/.test(source), 'auto-reload only fires when enabled and reloadIsSafe()');
  assert.ok(/function reloadIsSafe\(\)[\s\S]*file\?\.dirty[\s\S]*isContentEditable/.test(source), 'reloadIsSafe refuses when an editor buffer is dirty or the user is typing');
  // #40: YO!info and YO!agent are merged into ONE panel with a segmented sub-tab toggle; both sub-views
  // (the metadata table + the AI chat/summary) live in the single info panel and the active one is shown.
  const createInfoPanelSource = source.slice(source.indexOf('function createInfoPanel()'), source.indexOf('// The merged YO!info pane keeps its outer chrome'));
  assert.ok(/function createInfoPanel\(\)[\s\S]*?class="info-subtabs"[\s\S]*?data-info-subtab="info"[\s\S]*?data-info-subtab="yoagent"/.test(source), '#40: the merged info panel renders a YO!info/YO!agent sub-tab toggle');
  assert.ok(/function createInfoPanel\(\)[\s\S]*?data-info-subview="info"[\s\S]*?id="info-content"[\s\S]*?data-info-subview="yoagent"[\s\S]*?id="yoagent-content"/.test(source), '#40: the merged info panel hosts both the metadata and the YO!agent sub-views');
  assert.ok(/function createInfoPanel\(\)[\s\S]*?class="info-subtab-actions"[\s\S]*?data-info-subtab-action="info"[\s\S]*?data-info-subtab-action="yoagent"/.test(source), '#40: refresh actions live in the YO!info/YO!agent sub-tab bar');
  assert.equal(createInfoPanelSource.includes('class="panel-detail-row"'), false, '#40: the merged YO!info panel no longer renders a redundant title/info bar');
  assert.equal(createInfoPanelSource.includes('id="meta-'), false, '#40: the merged YO!info panel no longer renders a subtitle meta bar');
  assert.equal(/class="transcript-head info-head"/.test(source), false, '#40: the duplicate sub-view title bar is gone');
  assert.ok(/function createInfoPanel\(\)[\s\S]*?renderInfoPanel\(\);\s*renderYoagentPanel\(\);/.test(source), '#40: the merged panel renders both sub-views on creation');
  assert.ok(/renderAttached:\s*\(\) => \{[\s\S]*?applyInfoSubTab\(\);[\s\S]*?renderInfoPanel\(\);[\s\S]*?renderYoagentPanel\(\{preserveDraft: true, scrollBottom: false\}\);[\s\S]*?\}/.test(source), '#40/#YO!info: info tab registry hook renders both sub-views on attach');
  assert.ok(/function renderAttachedPanelContent\(item\)[\s\S]*?tabTypeForItem\(item\)\?\.renderAttached[\s\S]*?renderAttached\(item\)/.test(source), '#40/#YO!info: pooled panel attach dispatches through TAB_TYPES');
  assert.ok(/function renderDropSlot\(slot, session\)[\s\S]*?node\.appendChild\(panel\);\s*renderAttachedPanelContent\(session\);/.test(source), '#40/#YO!info: initial drop-slot attach renders YO!info before metadata polling');
  assert.ok(/function syncActivePanelsInPlace\(\)[\s\S]*?dropSlot\.replaceChildren\(desired\);[\s\S]*?updatePanelSlot\(desired, item, slot\);[\s\S]*?renderAttachedPanelContent\(item\);/.test(source), '#40/#YO!info: in-place panel swaps also render YO!info after attachment');
  assert.equal(source.includes('function createYoagentPanel('), false, '#40: the standalone YO!agent panel builder is gone');
  assert.ok(source.includes('function setInfoSubTab(') && source.includes('function applyInfoSubTab(') && source.includes('function relocalizeInfoPanelChrome(') && source.includes('async function openInfoSubTab('), '#40: sub-tab switch + locale + open helpers exist');
  assert.ok(/function setInfoSubTab[\s\S]*?writeStoredInfoSubTab\(next\)/.test(source), '#40: switching the sub-tab persists it (remembered across reloads)');
  assert.ok(/function openInfoSubTab[\s\S]*?selectSession\(infoItemId\)/.test(source), '#40: opening YO!agent activates the merged info pane');
  assert.ok(/function rerenderForLocale\(options = \{\}\)[\s\S]*?relocalizeInfoPanelChrome\(\)[\s\S]*?renderInfoPanel\(\)[\s\S]*?renderYoagentPanel\(\{preserveDraft: true, allowBusyRebuild: options\.localeChange === true\}\)[\s\S]*?relocalizeInfoPanelChrome\(\)/.test(source), '#40/#50: a language switch relabels persistent YO!info chrome and forces busy YO!agent UI to rebuild in the new locale');
  assert.equal(/function virtualPanelControlsHtml\(session\)[\s\S]*terminal-tab/.test(source), false, '#40: Preferences and YO!info virtual pane controls do not render a redundant active-tab pill');
  assert.ok(/function relocalizeInfoPanelChrome[\s\S]*?querySelectorAll\('\[data-info-subtab\]'\)[\s\S]*?button\.dataset\.infoSubtab === 'yoagent'[\s\S]*?data-info-refresh[\s\S]*?data-yoagent-refresh/.test(source), '#40/#50: the persistent YO!info/YO!agent sub-tab chrome and actions are localized in place by data attribute');
  assert.equal(/function relocalizeInfoPanelChrome[\s\S]*?info\.subtitle/.test(source), false, '#40/#50: no removed YO!info subtitle bar remains to relocalize');
  assert.ok(/let i18nApplyLocaleRequestId = 0/.test(source) && /async function applyLocale[\s\S]*?\+\+i18nApplyLocaleRequestId[\s\S]*?if \(requestId !== i18nApplyLocaleRequestId\) return/.test(source), '#50: overlapping language transitions cannot let an older catalog load repaint after the newer language choice');
  // Phase 1: the YO marker glyph is i18n-keyed (renders 優/优 under Chinese), not a hardcoded "YO".
  assert.ok(source.includes("esc(t('brand.marker'))"), 'the YO marker glyph renders via t(brand.marker)');
  // #81: a failed autosave-on-close falls through to the explicit save/discard/cancel dialog instead of
  // silently aborting the close.
  assert.ok(/if \(await saveFileEditor\(path, panel, \{autosave: true, closing: true\}\)\) return true;[\s\S]*?showFileEditorDecisionDialog/.test(source), '#81: autosave-on-close failure falls back to the close dialog');
  // #85/#86/#87/#88: toast removal honors countdownMs; reconnect confirmation is single-in-flight; the
  // repo popover is viewport-clamped; an equal-mtime unknown-size entry is treated as changed (re-stat).
  assert.ok(/removeAttentionAlert\(id\), options\.countdownMs \|\| toastDurationMs/.test(source), '#85: toast removal uses options.countdownMs');
  assert.ok(/function confirmSessionGoneOrReconnect[\s\S]*?if \(item\.confirmingGone\) return;[\s\S]*?item\.confirmingGone = true/.test(source), '#86: reconnect confirmation has an in-flight guard');
  assert.ok(/function showFileTreeRepoPopover[\s\S]*?clampToViewport\(/.test(source), '#87: the repo popover is clamped to the viewport');
  assert.ok(/function scheduleRepoRowHoverPopover[\s\S]*?setTimeout\([\s\S]*?showRepoRowHoverPopover\(row, path\)/.test(source), '#87: repo directory hover popovers are delayed through the shared popover delay timer');
  assert.equal(source.includes('row.onmouseenter = () => showRepoRowHoverPopover(row, fullPath);'), false, '#87: repo directory hover must not open the popover immediately on mouseenter');
  assert.ok(/function fileEntryChanged[\s\S]*?state\.size == null \|\| entry\.size == null\) return true/.test(source), '#88: unknown-size equal-mtime entries are treated as changed');
  // #73: the item-keyed editor maps are cleaned up on close + migrated on rename (no unbounded growth),
  // and the per-pane LRU timestamp survives a session rename.
  assert.ok(/function removeFilePanelOwner[\s\S]*?fileEditorViewState\.delete\(item\)[\s\S]*?tabLastActivatedAt\.delete\(item\)/.test(source), '#73: editor view-state + LRU timestamp are dropped on tab close');
  assert.ok(/function renameOpenFilePath[\s\S]*?fileEditorViewState\.set\(newKey[\s\S]*?tabLastActivatedAt\.set\(newKey/.test(source), '#73: editor view-state + LRU timestamp are migrated on rename');
  assert.ok(/function replaceSessionMetadata[\s\S]*?tabLastActivatedAt,\s*\n\s*\]\)/.test(source), '#73: the LRU timestamp is rekeyed across a session rename');
  const mergedInfoCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.info-subview\s*\{[\s\S]*?display:\s*none/.test(mergedInfoCss), '#40: inactive sub-views are hidden');
  assert.ok(/\.info-subview\.active\s*\{[\s\S]*?display:\s*flex/.test(mergedInfoCss), '#40: the active sub-view is shown');
  assert.ok(/\.info-subtab\.active\s*\{/.test(mergedInfoCss), '#40: the active sub-tab button is styled');
  assert.ok(/\.info-subtabs\s*\{[\s\S]*?background:\s*var\(--pane-bar-bg/.test(mergedInfoCss), '#40: YO!info sub-tabs use the same active pane bar background token');
  assert.ok(/\.info-subtab-actions\s*\{[\s\S]*?margin-inline-start:\s*auto/.test(mergedInfoCss), '#40: the active refresh action sits at the right side of the merged sub-tab bar');
  assert.ok(/\.info-subtab\.active\s*\{[\s\S]*?background:\s*var\(--pane-tab-active-bg/.test(mergedInfoCss), '#40: active YO!info sub-tab uses the pane active-tab color token');
  assert.ok(/\.info-subtab \.session-button-dir\s*\{[\s\S]*?color:\s*inherit/.test(mergedInfoCss), '#40: YO!info/YO!agent sub-tab labels inherit button contrast instead of forcing white text');
  assert.ok(/\.info-subtab\s*\{[\s\S]*?display:\s*inline-flex[\s\S]*?align-items:\s*center[\s\S]*?line-height:\s*1/.test(mergedInfoCss), '#40: YO!info/YO!agent sub-tab labels are vertically centered, including CJK labels');
  assert.ok(/body\.theme-light \.info-list,[\s\S]*?body\.theme-light \.info-watched\s*\{[\s\S]*?background:\s*#ffffff/.test(mergedInfoCss), '#40: the light-mode YO!info table uses a white surface');
  assert.ok(mergedInfoCss.includes('--info-branch-column-width: 320px'), 'YO!info Branch column has a named default width token');
  assert.ok(mergedInfoCss.includes('--info-desc-column-width: 310px'), 'YO!info desc column has a named default width token');
  assert.ok(/grid-template-columns:[\s\S]*var\(--info-session-column-width\)[\s\S]*var\(--info-path-column-width\)[\s\S]*minmax\(var\(--info-branch-column-width\), var\(--info-branch-column-width\)\)[\s\S]*var\(--info-pr-column-width\)[\s\S]*var\(--info-linear-column-width\)[\s\S]*minmax\(var\(--info-desc-column-width\), 1fr\)[\s\S]*var\(--info-updated-column-width\)/.test(mergedInfoCss), 'YO!info table columns use named width tokens');
  assert.ok(/min-width:\s*calc\([\s\S]*var\(--info-branch-column-width\)[\s\S]*var\(--info-desc-column-width\)[\s\S]*var\(--info-table-column-gap\) \* 6[\s\S]*var\(--info-table-inline-padding\) \* 2/.test(mergedInfoCss), 'YO!info table minimum width is derived from named column tokens');
  assert.ok(mergedInfoCss.includes('--info-column-resizer-hit-width: 24px'), 'YO!info column resize target has a named hit-width token');
  assert.ok(/\.info-resizable-header-cell\s*\{[\s\S]*?overflow:\s*visible/.test(mergedInfoCss), 'YO!info resize handles are not clipped by header cells');
  assert.ok(/\.info-column-resizer\s*\{[\s\S]*?width:\s*var\(--info-column-resizer-hit-width\)[\s\S]*?cursor:\s*col-resize/.test(mergedInfoCss), 'YO!info headers expose full-width column-resize handles');
  assert.ok(source.includes('data-info-column-resize="${esc(column)}"'), 'YO!info headers render resize handles through a shared helper');
  assert.ok(source.includes("resizeHandle('branch', t('info.resizeBranchColumn'))"), 'YO!info Branch header renders the resize handle');
  assert.ok(source.includes("resizeHandle('desc', t('info.resizeDescColumn'))"), 'YO!info desc header renders the resize handle');
  assert.ok(/function bindInfoColumnResizers[\s\S]*dataset\.infoColumnResize[\s\S]*setPointerCapture[\s\S]*storageSet\(config\.storageKey/.test(source), 'YO!info column drag persists resized widths through shared config');
  // #48: the merged info panel gets its own 3-row grid so the YO!info|YO!agent sub-tab row is always
  // visible (a real track) without a redundant detail/header bar.
  assert.ok(/\.info-panel\s*\{[\s\S]*?grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(mergedInfoCss), '#48: the info panel reserves a row for the sub-tab toggle');
  assert.ok(/\.info-panel\.details-collapsed\s*\{[\s\S]*?grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(mergedInfoCss), '#48: the sub-tab row survives a collapsed detail header');
  // #50: a language switch force-re-renders every localized surface and fires applyLocale optimistically.
  assert.ok(/function rerenderForLocale\(options = \{\}\)[\s\S]*?renderPreferencesPanels\(\{force: true\}\)[\s\S]*?renderBrandWordmark\(\)/.test(source), '#50: rerenderForLocale force-re-renders Preferences + the wordmark');
  assert.ok(/if \(path === 'general\.language'\) applyLocale\(resolveLocalePref\(value\)\)/.test(source), '#50: the language select switches locale optimistically, not on the poll');
  // #52: the wordmark YO/LO glyphs localize client-side (優樂 / 优乐) via t(brand.wordmark.*).
  assert.ok(/function renderBrandWordmark\(\)[\s\S]*?t\('brand\.wordmark\.yo'\)[\s\S]*?t\('brand\.wordmark\.lo'\)/.test(source), '#52: renderBrandWordmark localizes the YO/LO wordmark glyphs');
  // #47: tab drags use the native drag image (no JS clone-follow), and the drop-placement path reuses
  // cached tab rects during a drag instead of forcing sync layout (getBoundingClientRect) per move.
  assert.ok(/function startSessionDrag[\s\S]*?setDragImage\(source/.test(source), '#47: tab drags install the native drag image (the tab itself)');
  // C12 F2: dragstart must NOT force a layout reflow with getBoundingClientRect (it stalled the cold first drag).
  const startDragBody = source.slice(source.indexOf('function startSessionDrag'), source.indexOf('function endSessionDrag'));
  assert.equal(/\.getBoundingClientRect\(/.test(startDragBody), false, 'C12 F2: dragstart computes the grab offset without a getBoundingClientRect reflow');
  assert.ok(startDragBody.includes('event.offsetX') && startDragBody.includes('event.offsetY'), 'C12 F2: dragstart uses event.offsetX/offsetY for the drag-image offset');
  // C12 F1: a move of an already-running pane skips the blocking ensure-session round-trip.
  assert.ok(source.includes('function sessionTerminalIsLive('), 'C12 F1: a terminal-liveness helper exists');
  assert.ok(/if \(isTmuxSession\(session\) && !sessionTerminalIsLive\(session\)\) \{\s*const ensured = await ensureSession/.test(source), 'C12 F1: moveSessionToSlot only awaits ensureSession when the pane is not already live');
  assert.equal(source.includes('function startCustomDragPreview'), false, '#47: the tab clone-follow preview is removed');
  assert.ok(/function paneTabDropPlacement[\s\S]*?dragMeasureStrip\(strip\)/.test(source), '#47: drop placement measures the strip via the per-drag cache');
  assert.ok(/function dragMeasureStrip\([\s\S]*?dragSession != null[\s\S]*?dragTabRectCache/.test(source), '#47: the rect cache is only active during a live drag');
  assert.ok(source.includes('id="summary-${session}" class="summary-preview markdown-body"'), 'the YO!summary panel is a markdown-body container, not a raw <pre>');
  assert.ok(/transcript-head">\$\{esc\(t\('menu\.tmux\.aiTranscript'/.test(source), 'the YO!summary panel head names the session via the localized aiTranscript key');
  assert.ok(/function startSummaryStream[\s\S]*renderMarkdownPreviewInto\(node, raw\)/.test(source), 'the YO!summary stream renders accumulated text through the markdown pipeline');
  assert.ok(/function createTopbarSearch[\s\S]*openFileQuickOpen\(\)/.test(source), 'the topbar universal search opens the unified quick-open/command palette (no forked logic)');
  assert.ok(/renderSessionButtons[\s\S]*appendChild\(createTopbarSearch\(\)\)/.test(source), 'the topbar search is mounted in the menubar middle area');
  assert.ok(/refreshFileIndexStatus[\s\S]{0,400}\/api\/fs\/index-status\?root=/.test(source), '#30/#31: the client warms the backend index and tracks build status via /api/fs/index-status');
  assert.ok(source.includes("=== 'building' ? '…' : 'I'"), '#31: the indexed badge is compact when the date column is off');
  assert.ok(/function fileExplorerIndexBadgeText\(path\) \{[\s\S]*?fileExplorerTreeDateMode !== 'none'[\s\S]*?return ''/.test(source), '#31: Date/Ago rows hide the indexed status badge so it cannot overlap the date');
  assert.ok(source.includes("=== 'building' ? 'indexing…' : 'indexed'"), '#31: the indexed badge title keeps the full status text');
  assert.ok(/payload && payload\.ready \? 'ready' : 'building'/.test(source), '#31: a ready index (incl. during a background TTL rebuild that keeps ready=true) stays "indexed", not "indexing"');
  assert.ok(/fileExplorerIndexStatus\.set\(normalized, 'building'\);\s*refreshFileIndexStatus\(normalized\)/.test(source), '#30: indexing a directory eagerly warms its backend index (no cold first-query live walk)');
  const loadAutoStatusesFn = source.slice(source.indexOf('async function loadAutoStatuses'), source.indexOf('async function loadAutoStatuses') + 1700);
  assert.ok(loadAutoStatusesFn.includes('updateDocumentTitle();') && loadAutoStatusesFn.includes('renderAutoApproveButtons();'), '#46: the auto-status poll re-syncs the YO markers (renderAutoApproveButtons) alongside the tab title, so a done pane stops spinning on the same poll');
  assert.ok(source.includes("{path: 'file_explorer.indexed_dirs'"), '#32: Preferences exposes an editable indexed-directories list');
  assert.ok(/function reconcileIndexedDirsFromSetting[\s\S]*setFileExplorerDirectoryIndexed\(dir, true\)[\s\S]*setFileExplorerDirectoryIndexed\(dir, false\)/.test(source), '#32: editing the indexed-dirs setting adds/removes indexed dirs (bi-directional sync)');
  assert.ok(source.includes('/api/fs/unindex?root='), '#32: removing an indexed dir wires to the backend unindex');
  // C11: the indexed-dirs setting save MERGES a single add/remove into the shared list instead of
  // overwriting it with this page's set, so two browser origins don't clobber each other's indexed dirs.
  assert.ok(/persistIndexedDirsSetting\(indexed \? \{add: normalized\} : \{remove: normalized\}\)/.test(source), 'C11: indexed-dir save passes the single add/remove op (merge, not overwrite)');
  assert.ok(/function persistIndexedDirsSetting\(op = \{\}\)[\s\S]*?if \(op\.add\) set\.add\(op\.add\)[\s\S]*?if \(op\.remove\) set\.delete\(op\.remove\)/.test(source), 'C11: persistIndexedDirsSetting merges the op into the current shared setting');
  // C11 #3: the localStorage->setting migration is one-time (marker), so a stale per-origin cache can't
  // re-seed the durable shared setting after migration — the setting is the sole desired-root source.
  assert.ok(source.includes("storageSet(fileExplorerIndexedDirsMigratedKey, '1')"), 'C11 #3: indexed-dirs migration from localStorage is recorded as one-time');
  assert.ok(/if \(options\.initial && !migrated\)/.test(source), 'C11 #3: migration runs once, guarded by the marker');
  const focusPreferencesStart = source.indexOf('function focusPreferencesSearch(');
  const focusPreferencesEnd = source.indexOf('function preferencesScrollIsActive(', focusPreferencesStart);
  assert.ok(focusPreferencesStart > 0 && focusPreferencesEnd > focusPreferencesStart, 'could not locate focusPreferencesSearch body');
  assert.ok(source.slice(focusPreferencesStart, focusPreferencesEnd).includes('panel && panel.isConnected !== false'), 'explicit Preferences search focus falls back to the rendered panel when called without a panel');
  assert.equal(source.includes('function focusPreferencesSearchSoon('), false, 'Preferences no longer has delayed search auto-focus');
  assert.equal(source.includes('function focusFreshPreferencesSearchSoon('), false, 'Preferences no longer has fresh-pane search auto-focus');
  const focusedPanelStart = source.indexOf('function setFocusedPanelItem(');
  const focusedPanelEnd = source.indexOf('function clearPendingFileEditorFocusExcept(', focusedPanelStart);
  assert.ok(focusedPanelStart > 0 && focusedPanelEnd > focusedPanelStart, 'could not locate setFocusedPanelItem body');
  const focusedPanelBody = source.slice(focusedPanelStart, focusedPanelEnd);
  assert.equal(focusedPanelBody.includes('focusPreferencesSearch'), false, 'shared pane focus does not steal focus into Preferences search');
  assert.ok(focusedPanelBody.includes('updateTypingIndicator(activeSession)'), 'shared pane focus refreshes every pane focus ring immediately');
  const panelShellStart = source.indexOf('function bindPanelShell(');
  const panelShellEnd = source.indexOf('const head = panel.querySelector', panelShellStart);
  assert.ok(panelShellStart > 0 && panelShellEnd > panelShellStart, 'could not locate bindPanelShell body');
  const panelShellBody = source.slice(panelShellStart, panelShellEnd);
  assert.equal(panelShellBody.includes('focusPreferencesSearch'), false, 'panel pointer/focus events do not steal focus into Preferences search');
  assert.equal(source.includes('function preferenceFocusTargetIsInteractive'), false, 'the old Preferences search auto-focus target helper is removed');
  const resetAllStart = source.indexOf('function resetAllPreferences(');
  const resetAllEnd = source.indexOf('function createFileExplorerPanel(', resetAllStart);
  assert.ok(resetAllStart > 0 && resetAllEnd > resetAllStart, 'could not locate resetAllPreferences body');
  assert.equal(source.slice(resetAllStart, resetAllEnd).includes('focusSearch: true'), false, 'reset all is a settings interaction and does not re-arm fresh search focus');
  const refreshStart = source.indexOf('async function refreshOpenFilesIfChanged(');
  const refreshEnd = source.indexOf('function watchedFileExplorerDirectories(', refreshStart);
  assert.ok(refreshStart > 0 && refreshEnd > refreshStart, 'could not locate refreshOpenFilesIfChanged body');
  const refreshBody = source.slice(refreshStart, refreshEnd);
  assert.ok(refreshBody.includes('const fetched = await fetchFileEntryStatus(path);'), 'open-file refresh uses a structured file lookup');
  assert.ok(refreshBody.includes('refreshOpenFileFromFetchedStatus(path, state, fetched)'), 'open-file polling routes structured status through the shared refresh helper');
  const statusRefreshStart = source.indexOf('async function refreshOpenFileFromFetchedStatus(');
  const statusRefreshEnd = source.indexOf('async function refreshOpenFilesIfChanged(', statusRefreshStart);
  assert.ok(statusRefreshStart > 0 && statusRefreshEnd > statusRefreshStart, 'could not locate refreshOpenFileFromFetchedStatus body');
  const statusRefreshBody = source.slice(statusRefreshStart, statusRefreshEnd);
  assert.ok(statusRefreshBody.includes('if (fetched.missing)'), 'open-file refresh only marks missing after an explicit missing result');
  assert.ok(statusRefreshBody.includes('markOpenFileExternalError'), 'open-file refresh keeps network/list errors separate from deletion');
});

test('t@2069', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function updatePaneTabStrip(');
  const end = source.indexOf('function reconcilePaneTabChildren(', start);
  assert.ok(start > 0 && end > start, 'could not locate updatePaneTabStrip body');
  const body = source.slice(start, end);
  assert.equal(body.includes('replaceChildren(...'), false, 'routine pane-tab refresh must reconcile instead of rebuilding hovered tabs');
  assert.ok(source.includes('function reconcilePaneTabChildren('), 'pane-tab reconcile helper exists');
  assert.ok(source.includes('function paneTabShouldPreserve('), 'open pane-tab popovers keep their existing node');
  // The per-pane left-dropdown caret was removed (it wasted a line above the tab strip) — keep it gone.
  assert.equal(source.includes('pane-tabs-menu-caret'), false, 'no per-pane dropdown caret (removed by request)');
});

test('t@2082', () => {
  const api = loadYolomux('', ['1']);
  const tab = new TestElement('tab');
  const popover = new TestElement('popover');
  tab.className = 'pane-tab popover-open';
  tab.classList.add('pane-tab', 'popover-open');
  tab.dataset.paneTab = '1';
  tab.dataset.popoverHoverState = 'closing';
  tab.querySelector = selector => selector.includes('session-popover') ? popover : null;
  assert.equal(api.paneTabShouldPreserve(tab), true);
  const detachedTab = new TestElement('detached-tab');
  const detachedPopover = new TestElement('detached-popover');
  detachedTab.classList.add('pane-tab', 'dockview-pane-tab', 'popover-open');
  detachedTab.dataset.paneTab = '1';
  detachedTab.dataset.popoverHoverState = 'open';
  detachedTab.__yolomuxDetachedPopover = detachedPopover;
  detachedTab.querySelector = () => null;
  assert.equal(api.paneTabShouldPreserve(detachedTab), true, 'Dockview detached popovers keep their tab renderer from rebuilding');

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
});

test('t@2114', () => {
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
});

test('t@2134', () => {
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
});

test('t@2147', () => {
  // T5: stopping a session clears every session-keyed UI map and closes live streams.
  const api = loadYolomux('', ['1']);
  api.registerTerminalForTest('1', {dispose() {}}, {readyState: 1, close() {}});
  const closed = api.seedSessionTeardownStateForTest('1');
  api.stopSessionUiForTest('1');
  assert.deepEqual(api.sessionTeardownStateForTest('1'), {
    terminal: false,
    transcript: false,
    summary: false,
    autoApprove: false,
    uploads: false,
    uploadTimer: false,
  }, 'stopSessionUi clears all session-keyed UI state');
  assert.deepEqual(closed, {transcript: 1, summary: 1}, 'stopSessionUi closes both EventSource streams');
});

test('t@2164', () => {
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
  assert.ok(/\.session-popover\s*\{[\s\S]*background:\s*var\(--panel2\)[\s\S]*border:\s*1px solid var\(--active-control-soft-border\)/.test(css), 'session/tab popovers use a neutral card with theme-accent border, not a hardcoded green card');
  assert.ok(/\.popover-label\s*\{[\s\S]*color:\s*var\(--active-accent-bright\)/.test(css), 'session/tab popover labels follow the active theme color');
  assert.equal(/#081f08|#bddcaf|#a2c98d|rgba\(118,\s*185,\s*0,\s*0\.72\)/.test(css), false, 'old hardcoded green popup palette is gone');
  assert.ok(/\.file-tree-repo-ahead/.test(css) && /\.file-tree-repo-dirty/.test(css), 'inline ahead/dirty markers are styled');
});

test('t@2187', () => {
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
  api.setPinnedTabsForTest(['1']);
  assert.deepStrictEqual([...api.paneStateWithTabs(['2', '1', '3'], '3').tabs], ['1', '2', '3'], 'pinned tabs normalize to the front of their pane');
  assert.deepStrictEqual([...api.orderPaneTabs(['2', '1', '3'])], ['1', '2', '3'], 'shared tab-order helper preserves pinned-first ordering');
  const rawPinnedSlots = api.emptyLayoutSlots();
  rawPinnedSlots[api.layoutTreeKey] = api.leafNode('left');
  rawPinnedSlots.left = {tabs: ['2', '1', '3'], active: '3'};
  assert.deepStrictEqual([...api.normalizeLayoutSlots(rawPinnedSlots).left.tabs], ['1', '2', '3'], 'raw layout normalization also enforces pinned-first ordering');
  assert.ok(api.pinnedTabIconHtml('1').includes('pane-tab-pin-icon'), 'pinned tabs render a pin icon helper');
  assert.deepStrictEqual([...api.tabsToEvictForCap(['1', '2', '3', '4'], '4')], ['3', '2'], 'LRU eviction skips pinned tabs');
  assert.equal(api.isPinnableTab(api.fileExplorerItemId), false, 'Finder/Differ is not pinnable');
  assert.equal(api.isPinnableTab('2'), true, 'normal tmux tabs are pinnable');
  api.setPinnedTabsForTest([]);
  assert.ok(source.includes('const evicted = tabsToEvictForCap(tabs, session);'), 'moveSessionToSlot enforces the tab cap when a tab joins a pane');
});

test('t@2231', () => {
  // the session popover shows review status AND who reviewed.
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
});

test('t@2246', () => {
  // #1/#2/#3: tab badge legibility (light), drop the redundant PR pill, no duplicate tooltip.
  const api = loadYolomux('', ['5']);
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  // #1: light-theme overrides exist for the badge chips, incl. a readable (non-transparent) review-required.
  assert.ok(/\.ci-indicator\.pr-review-required\s*\{[^}]*color:\s*#172033[\s\S]*background:\s*#e7ebf1[\s\S]*opacity:\s*1/.test(css), '#6: review-required chip is filled with dark text, readable on bright active tabs');
  assert.ok(/body\.theme-light \.ci-indicator\.pr-review-required\s*\{[^}]*background:\s*#e7ebf1/.test(css), '#6: review-required chip is legible (light fill) in light theme');
  assert.ok(/body\.theme-light \.ci-indicator\.pr-number-chip/.test(css) && /body\.theme-light \.ci-indicator\.pr-review-approved/.test(css), '#6: light-theme overrides cover number + review chips');
  // #2: the ready-review "PR" state pill is dropped (PR chips convey it now); other states still render.
  assert.equal(api.sessionStateHtml({key: 'ready-review', short: 'PR', label: 'Ready for review', reason: 'checks pass'}), '', '#7: the redundant ready-review PR pill is suppressed');
  assert.ok(api.sessionStateHtml({key: 'needs-input', short: '?', label: 'Needs input', reason: 'waiting'}).includes('session-state-needs-input'), '#7: actionable states still render a badge');
  // #3: tab badge chips carry no native title (the custom popover is the single source).
  assert.ok(!api.pullRequestNumberIndicatorHtml('5', {number: 123}).includes('title='), '#8: the PR number chip has no native title tooltip');
  assert.ok(!api.pullRequestApprovalIndicatorHtml('5', {number: 123, state: 'open', review_decision: 'APPROVED'}).includes('title='), '#8: the approval chip has no native title tooltip');
  assert.ok(!api.pullRequestCompactBadgesHtml('5', {number: 123, state: 'open', review_decision: 'APPROVED'}).includes('title='), '#8: the compact PR badge row has no native title tooltips');
  const mergedBadges = api.pullRequestCompactBadgesHtml('5', {number: 123, merged: true});
  assert.ok(/pr-number-chip pr-status-merged/.test(mergedBadges) && />#123</.test(mergedBadges), 'merged PR tab uses one purple #number chip');
  assert.equal(mergedBadges.includes('MERGED'), false, 'merged PR tab omits the redundant MERGED text badge');
  assert.equal(api.pullRequestLinkLabel({number: 123, merged: true}), '#123', 'merged PR inline labels omit redundant MERGED text');
  assert.equal(api.pullRequestLinkLabel({number: 123, state: 'open'}), '#123', 'open PR inline labels omit redundant OPEN text');
});

test('t@2268', () => {
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
});

test('t@2288', () => {
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
});

test('t@2306', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function renderTreeChildren(');
  const end = source.indexOf('function rawFileUrl(', start);
  assert.ok(start > 0 && end > start, 'could not locate renderTreeChildren body');
  const body = source.slice(start, end);
  assert.equal(body.includes('replaceChildren(...nextNodes)'), false, 'Finder refresh must reconcile existing rows instead of rebuilding them');
  assert.equal(source.includes('function differFileTreeHtmlStr'), false, 'Differ must not maintain a parallel file-tree HTML renderer');
  assert.equal(source.includes('function changesGroupsHtmlStr'), false, 'Differ groups must render through the shared DOM renderer, not a duplicate HTML renderer');
  assert.equal(/function renderChangesPanels[\s\S]*renderChangesRoot\(/.test(source), false, 'standalone Changes render loop is removed');
  assert.ok(/function renderFileExplorerChangesPanel[\s\S]*renderChangesRoot\(/.test(source), 'Finder diff panel / embedded Differ refreshes through the shared incremental render root');
  assert.ok(source.includes('function updateFileTreeRowContents('), 'Finder row text/icon updates are localized');
  const updateStart = source.indexOf('function updateFileTreeRowContents(');
  const updateEnd = source.indexOf('function updateFileTreeRow(', updateStart);
  assert.ok(updateStart > 0 && updateEnd > updateStart, 'could not locate updateFileTreeRowContents body');
  const updateBody = source.slice(updateStart, updateEnd);
  assert.equal(updateBody.includes("name.textContent = nameText;\n    name.innerHTML = '';"), false, 'Finder row text must not be cleared after being written');
  assert.ok(updateBody.includes("name.innerHTML = '';\n    name.textContent = nameText;"), 'Finder row clears stale HTML before writing plain text');
  const sharedTreeCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.file-tree-row\.has-agent \.file-tree-name\s*\{[^}]*flex:\s*0 1 auto[\s\S]*min-width:\s*0/.test(sharedTreeCss), 'file-tree rows with agent metadata keep the AI marker directly beside the filename while allowing the filename to ellipsize');
  assert.equal(/\.file-tree-row\.has-agent \.file-tree-agent\s*\{[^}]*margin-inline-end:\s*auto/.test(sharedTreeCss), false, 'file-tree agent metadata must not use auto-margin that can push date columns out of view');
  assert.ok(/\.changes-file-list\s*\{[^}]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)[\s\S]*min-inline-size:\s*0/.test(sharedTreeCss), 'Differ file-list grid constrains shared file-tree rows to the pane width instead of max-content clipping the date column');
  assert.ok(/\.file-tree-row:has\(> \.file-tree-dir-count:not\(\[hidden\]\)\) > \.file-tree-dir-count,[\s\S]*?margin-inline-start:\s*auto/.test(sharedTreeCss), 'Finder/Differ rows push the first right-side metadata column, not the AI marker, to the right edge');
  assert.ok(/--file-tree-row-control-size:\s*calc\(var\(--file-explorer-font-size\) \+ 2px\)/.test(sharedTreeCss), 'Finder/Differ row controls scale from the Finder font size');
  assert.ok(/--file-tree-icon-size:\s*calc\(var\(--file-explorer-font-size\) \+ 2px\)/.test(sharedTreeCss), 'Finder/Differ icon boxes scale from the Finder font size');
  assert.equal(/--file-tree-row-control-size:\s*max\(18px/.test(sharedTreeCss), false, 'Finder/Differ row controls must not keep the old fixed 18px minimum');
  assert.ok(/\.file-tree-icon\s*\{[^}]*display:\s*inline-flex[\s\S]*flex:\s*0 0 var\(--file-tree-icon-size\)[\s\S]*font-size:\s*var\(--file-tree-icon-font-size\)[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ file icons use a centered box that follows the Finder font size');
  assert.ok(/\.file-tree-agent \.agent-icon\s*\{[^}]*width:\s*calc\(var\(--file-explorer-font-size\) \+ 3px\)[\s\S]*height:\s*calc\(var\(--file-explorer-font-size\) \+ 1px\)/.test(sharedTreeCss), 'Finder/Differ AI markers scale with the Finder font size instead of pinning the row height');
  assert.ok(/\.file-tree-diff\s*\{[^}]*justify-content:\s*flex-end[\s\S]*flex:\s*0 0 6\.5ch[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ diff counts reserve one shared column before the git status badge');
  assert.ok(/\.file-tree-git-status\s*\{[^}]*display:\s*inline-flex[\s\S]*align-items:\s*center[\s\S]*justify-content:\s*center[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ git status badges use the same centered box');
  assert.ok(/\.file-tree-git-status\s*\{[^}]*overflow:\s*hidden[\s\S]*white-space:\s*nowrap/.test(sharedTreeCss), 'Finder/Differ status badges cannot spill into the date column');
  assert.ok(/\.file-tree-date\s*\{[^}]*display:\s*inline-flex[\s\S]*align-items:\s*center[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ date cells align to the icon/status centerline');
  assert.ok(/\.file-tree-date\s*\{[^}]*justify-content:\s*flex-end[\s\S]*flex:\s*0 0 var\(--file-tree-date-column-width\)[\s\S]*inline-size:\s*var\(--file-tree-date-column-width\)/.test(sharedTreeCss), 'Finder/Differ date cells reserve a fixed right column so status badges line up');
  assert.ok(/\.file-tree-dir-count\[hidden\],[\s\S]*?\.file-tree-date\[hidden\]\s*\{[\s\S]*display:\s*none/.test(sharedTreeCss), '#46: hidden Finder/Differ metadata cells do not reserve their right-side columns');
  assert.ok(source.includes("refreshActivitySummary({force: true})"), 'YO!agent Refresh summary forces cached summaries to rebuild');
  assert.ok(source.includes("params.set('force', '1')"), 'YO!agent summary API supports a force refresh query');
  assert.ok(source.includes("params.set('locale', i18nActiveLocaleId())"), 'YO!agent summary API carries the active locale query');
  assert.ok(source.includes("data-yolo-rule-open"), 'Preferences exposes an Open button for the YOLO rule file');
  assert.ok(source.includes("apiFetchJson('/api/yoagent/reset'"), 'YO!agent clear conversation resets the server-side CLI session');
  assert.ok(source.includes("renderYoagentPanel({preserveDraft: false, scrollBottom: true})"), 'YO!agent send/clear clears the draft and scrolls chat to the bottom');
  assert.equal(source.includes('yoagentSessionSummariesHtml'), false, 'YO!agent default panel does not render the per-session SESSION detail card list');
  assert.ok(source.includes("row.draggable = entry.kind === 'file' || entry.kind === 'dir';") && source.includes('row.dataset.openChangeFile = changedFile.abs_path;'), 'Modified-files rows use the shared tree renderer and remain draggable as file payloads');
  assert.ok(source.includes("event.dataTransfer.setData('application/x-yolomux-file'"), 'Modified-files drag carries the same file payload as Finder drag');
  assert.ok(source.includes("'Allow index'"), 'Finder directory context menu exposes Allow index');
  assert.ok(source.includes("'Disallow index'"), 'Finder directory context menu exposes Disallow index');
  assert.ok(source.includes("row.classList.toggle('indexed-directory', indexedDirectory)"), 'Finder row render marks indexed directories');
  assert.ok(source.includes("'file-icon-dir-indexed'"), 'Finder indexed directories use a distinct icon class');
});

test('t@2355', () => {
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
  assert.equal(source.includes("panel?._cmPlainFallback ? [codeMirrorThemeExtension(api)]"), false, 'plain CodeMirror fallback theme toggles keep Markdown fallback syntax decorations');
  assert.equal(source.includes('scheme: activeEditorScheme().id'), false, 'CodeMirror config signatures do not force panel rebuilds on theme-only changes');
  assert.ok(source.includes('function yoagentChatNetworkError'), 'YO!agent chat distinguishes network failures from normal HTTP errors');
  assert.ok(source.includes('let yoagentFocusSerial = 0'), 'YO!agent chat tracks whether focus was abandoned while a request is pending');
  assert.ok(/focusInput:\s*shouldRestoreFocus && focusSerial === yoagentFocusSerial && yoagentDocumentHasFocus\(\)/.test(source), 'YO!agent responses only refocus when the user did not move away');
  assert.ok(/if \(options\.summaryOnly && refreshYoagentSummaryRegions\(node\)\) \{[\s\S]*?restoreYoagentChatInputFocus/.test(source), 'YO!agent summary refresh restores an already-focused composer input');
  assert.ok(source.includes('function refreshYoagentSummaryRegions'), 'YO!agent metadata refresh can update summaries without rebuilding the chat input');
  assert.ok(source.includes('function yoagentAutoRefreshStatusHtml'), 'YO!agent renders cached background-summary status when auto-refresh is enabled');
  assert.ok(source.includes('summaryOnly: true'), 'YO!agent metadata refresh requests summary-only panel updates');
  assert.ok(source.includes("params.set('locale', i18nActiveLocaleId())"), 'YO!agent activity-summary requests carry the active UI locale');
  assert.ok(source.includes('function yoagentSessionFromHref'), 'YO!agent Markdown session links parse the target session from the query string');
  assert.ok(/function handleYoagentSessionLinkClick[\s\S]*?selectSession\(session, \{userInitiated: true\}\)/.test(source), 'YO!agent Markdown session links select the matching tab');
  assert.ok(source.includes('function installYoagentSessionLinks'), 'YO!agent Markdown session links install a scoped click handler');
  assert.ok(/function linkYoagentSessionCodeReferences[\s\S]*?sessions\.includes\(session\)[\s\S]*?\(tmux\\s\+\)\?session\\s\*\$[\s\S]*?link\.href = `\?yoagent-session=\$\{encodeURIComponent\(session\)\}`/.test(source), 'YO!agent inline `tmux session `code`` references are converted to clickable session links');
  assert.ok(/function renderYoagentMessageMarkdown[\s\S]*?renderMarkdownPreviewInto\(body, yoagentTightMarkdown[\s\S]*?installYoagentSessionLinks\(body\)/.test(source), 'YO!agent Markdown rendering makes session links clickable after sanitization');
  assert.ok(/function rerenderForLocale\(options = \{\}\)[\s\S]*?allowBusyRebuild: options\.localeChange === true[\s\S]*?refreshActivitySummary\(\{force: true, silent: true, localeChange: true\}\)/.test(source), 'language switches force the busy YO!agent UI and activity summary through the new locale');
  // #45: assistant replies are structured Markdown — flag the body and render it through marked.js.
  assert.ok(source.includes('function renderYoagentMessageMarkdown'), '#45: YO!agent assistant replies render their multi-section Markdown body');
  assert.ok(source.includes('data-yoagent-global-markdown'), 'YO!agent global summary lines are flagged for markdown rendering');
  assert.ok(/\.yoagent-global \[data-yoagent-global-markdown\][\s\S]*?renderMarkdownPreviewInto\(body, yoagentTightMarkdown/.test(source), 'YO!agent global summary markdown is rendered through the sanitizer');
  assert.ok(/\.yoagent-message\.assistant \.yoagent-message-body\[data-yoagent-markdown\]/.test(source), '#45: the markdown render pass targets flagged assistant message bodies');
  assert.ok(/renderMarkdownPreviewInto\(body, yoagentTightMarkdown\(body\.textContent/.test(source), '#45/#129: assistant message Markdown is rendered (tightened) from the escaped-text fallback');
  assert.ok(source.includes("roleClass === 'assistant' ? 'yoagent-message-body markdown-body'"), '#45: assistant message bodies get the markdown-body class for formatting');
  // #42: editor controls (# / wrap / find / FROM-TO / diff / theme / save) move OFF the tab strip
  // onto a dedicated toolbar info line below the tabs; the tab strip keeps only tabs + frame controls.
  const editorToolbarIdx = source.indexOf('class="file-editor-toolbar" role="toolbar"');
  const editorLeftZoneIdx = source.indexOf('file-editor-toolbar-left', editorToolbarIdx);
  const editorCenterZoneIdx = source.indexOf('file-editor-toolbar-center', editorToolbarIdx);
  const editorRightZoneIdx = source.indexOf('file-editor-toolbar-right', editorToolbarIdx);
  const editorFrameActionsIdx = source.indexOf('file-editor-frame-actions');
  const editorTabsIdx = source.indexOf('<div class="pane-tabs"', editorFrameActionsIdx);
  const editorGutterIdx = source.indexOf('<button type="button" class="file-editor-gutter-panel"');
  assert.ok(editorToolbarIdx > -1, '#42: editor controls render on a dedicated .file-editor-toolbar info line');
  assert.ok(editorGutterIdx > editorToolbarIdx, '#42: the # / line-numbers control lives in the toolbar row, not the tab strip');
  assert.ok(editorToolbarIdx < editorLeftZoneIdx && editorLeftZoneIdx < editorCenterZoneIdx && editorCenterZoneIdx < editorRightZoneIdx, 'editor toolbar renders shared left/center/right parent zones');
  const editorToolbarTemplateEnd = source.indexOf('<div class="file-editor-panel-body panel-overlay-root">', editorToolbarIdx);
  const editorToolbarTemplate = source.slice(editorToolbarIdx, editorToolbarTemplateEnd);
  assert.ok(
    editorToolbarTemplate.indexOf('class="file-editor-theme-panel"') < editorToolbarTemplate.indexOf('data-editor-mode="edit"'),
    'editor toolbar renders the Bright/Dark/Vanilla selector immediately before Edit'
  );
  assert.ok(
    editorToolbarTemplate.indexOf('class="file-editor-reload-panel"') > editorToolbarTemplate.indexOf('data-editor-mode="edit"'),
    'editor toolbar keeps Reload with the trailing command buttons'
  );
  assert.equal(source.includes("cycleEditorThemeMode({includeVanilla: mode === 'preview' || mode === 'split'})"), false, 'editor theme button never falls back to two-state dark/light based on view mode');
  assert.ok(/file-editor-theme-panel'\)\?\.addEventListener\('click'[\s\S]*cycleEditorThemeMode\(\{includeVanilla: true\}\)/.test(source), 'editor theme button always cycles Bright/Dark/Vanilla');
  assert.ok(/updateEditorThemeButton\(themeButton, \{includeVanilla: true\}\)/.test(source), 'editor theme button always renders the visible three-state label');
  assert.ok(!/file-editor-gutter-panel|file-editor-find-panel|file-editor-diff-ref-panel|file-editor-wrap-panel/.test(source.slice(editorFrameActionsIdx, editorTabsIdx)), '#42: the editor tab strip is uncluttered — only tabs + frame controls remain');
  assert.ok(/\.panel\.file-editor-panel\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(css), '#42: the editor panel grid reserves a row for the toolbar between tabs and body');
  assert.ok(/\.file-editor-toolbar\[hidden\]\s*\{\s*display:\s*none/.test(css), '#42: the editor toolbar row collapses when no controls are visible');
  // Editor toolbar alignment: left/center/right are owned by parent groups, not per-button spacer hacks.
  assert.ok(/\.file-editor-toolbar-zone\s*\{[^}]*display:\s*inline-flex[\s\S]*align-items:\s*center/.test(css), 'editor toolbar children inherit shared zone behavior');
  assert.ok(/\.file-editor-toolbar-left\s*\{[^}]*flex:\s*0 1 auto/.test(css), 'editor toolbar left zone stays pinned left');
  assert.ok(/\.file-editor-toolbar-center\s*\{[^}]*position:\s*absolute[\s\S]*left:\s*50%[\s\S]*transform:\s*translate\(-50%, -50%\)/.test(css), 'editor toolbar center zone stays centered');
  assert.ok(/\.file-editor-toolbar-right\s*\{[^}]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(css), 'editor toolbar right zone is the only spacer-backed zone');
  assert.ok(/\.file-editor-diff-panel\s*\{[^}]*min-width:\s*44px/.test(css), 'editor toolbar gives Differ text-button width');
  assert.ok(/\.file-editor-toolbar\s*\{[^}]*justify-content:\s*flex-start/.test(css), 'editor toolbar left-aligns # and Differ by default, including after browser refresh');
  const toolbarCssStart = css.indexOf('.file-editor-toolbar {');
  const toolbarCssEnd = css.indexOf('.file-editor-preview-font-panel button', toolbarCssStart);
  assert.equal(css.slice(toolbarCssStart, toolbarCssEnd).includes('margin-inline-end: auto'), false, 'editor toolbar has no per-button end-spacer rules that can move Differ');
  assert.ok(/\.file-editor-toolbar\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(css), '#3: editor toolbar background matches the pane chrome bar (--pane-bar-bg: bright focused / gray unfocused)');
  assert.ok(/\.file-editor-diff-panel\.active,[\s\S]*?\.file-editor-diff-panel\[aria-pressed="true"\]\s*\{[\s\S]*?background:\s*var\(--pane-ctl-pressed-bg/.test(css), 'Diff active state uses the shared pressed control color');
  const editorPressedStart = css.indexOf('.file-editor-mode-control-panel button.active');
  const editorPressedBlock = css.slice(editorPressedStart, css.indexOf('{', editorPressedStart));
  assert.ok(editorPressedBlock.includes('.file-editor-gutter-panel.active') && editorPressedBlock.includes('.file-editor-find-panel[aria-pressed="true"]') && editorPressedBlock.includes('.file-editor-wrap-panel[aria-pressed="true"]'), '#, Search, and wrap active states share the pressed control treatment');
  assert.ok(source.includes('>Differ</button>'), 'editor Diff toolbar button renders as Differ text');
  assert.ok(source.includes('toggleEditorFind(panel);'), 'Search toolbar button toggles the CodeMirror search panel');
  assert.ok(source.includes('const currentText = String(state.content || \'\');'), 'plain CodeMirror editor mode owns its current text value');
  assert.ok(source.includes('function setLimitedMapEntry'), 'long-lived frontend maps share a bounded LRU setter');
  assert.ok(source.includes('fileExplorerMemoryCacheLimit = 512'), 'file explorer memory caches are capped');
  assert.ok(source.includes('commandPaletteRecentKeyLimit = 100'), 'command palette recent-key cache is capped');
  assert.ok(source.includes('restoreElementScrollPosition(container, scrollTop, scrollLeft);'), 'editor preview renders preserve scroll position');
  assert.equal(source.includes("const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef});\n  installCodeMirrorDiffResizeObserver"), false, 'diff resize observer is not installed before the rebuild decision');
  assert.ok(/function openFileQuickOpenPath\(path, options = \{\}\)[\s\S]*const targetSlot = fileQuickOpenTargetSlot\(\);[\s\S]*openedItem = await openFileInEditor\(path, \{name: label\}, targetSlot[\s\S]*\? \{targetSlot, userInitiated: true\}[\s\S]*: \{userInitiated: true\}\)/.test(source), 'quick-open normal file opens pass the active pane target slot');
  assert.ok(/function fileQuickOpenTargetSlot\(\)\s*\{\s*return focusedActivationSlot\(\);/.test(source), 'DOIT.56 N2: quick-open uses the shared focused-pane activation target');
  assert.ok(/function slotForTabActivation\(item\)\s*\{[\s\S]*return focusedActivationSlot\(\) \|\| largestNonFileExplorerPaneSlot\(\)/.test(source), 'DOIT.56 N2: new virtual/file tabs prefer the focused non-Finder pane before largest-pane fallback');
  assert.ok(/async function openFileEditorPane\(path, options = \{\}\)[\s\S]*const activationSlot = slotForTabActivation\(item\);[\s\S]*await moveSessionToSlot\(item, activationSlot/.test(source), 'DOIT.56 N2: generic file opens share the same focused-pane activation target');
  assert.ok(/if \(options\.split === true\)[\s\S]*targetZone: targetSlot \? 'middle' : 'right'/.test(source), 'quick-open split-open keeps its explicit split behavior');
  assert.ok(source.includes('focusQuickOpenedFile(openedItem);'), 'quick-open focuses the opened file after the async open resolves');
  assert.ok(source.includes('await Promise.resolve(action?.());'), 'command palette selection awaits async run handlers before focus settles');
  assert.ok(source.includes('function focusCommandPaletteTarget'), 'command palette has one shared post-run focus helper');
  assert.ok(source.includes('focusCommandPaletteTarget(item);'), 'command palette applies deterministic focus after async tab/session actions');
  assert.ok(source.includes('targetItem: item,'), 'command palette tab entries carry their layout focus target');
  assert.ok(source.includes("const defaultLightEditorScheme = 'yolomux-light';"), 'light editor defaults to the brand YOLOmux Light scheme');
  assert.ok(source.includes("else setFileEditorViewMode(fullPath, 'edit', item);"), 'plain file opens reset stale diff mode back to edit');
  assert.ok(source.includes('applyMarkdownSourceLines(container, text);'), 'Markdown preview source anchors are attached after parsing');
  assert.ok(source.includes('function codeMirrorMarkdownFallbackSyntaxExtension'), 'Markdown edit mode has a parser-independent CodeMirror coloring fallback');
  assert.ok(/function codeMirrorThemeExtensions[\s\S]*codeMirrorMarkdownFallbackSyntaxExtension\(api, path\)/.test(source), 'Markdown fallback coloring is wired into live CodeMirror edit views');
  assert.ok(css.includes('.cm-content .md-heading'), 'Markdown fallback color classes apply inside CodeMirror edit content');
  assert.ok(/gutterButton\.hidden = state\.kind !== 'text' \|\| mode === 'preview'/.test(source), 'preview mode hides the line-number button because no CodeMirror gutter is shown');
  assert.ok(/wrapButton\.hidden = state\.kind !== 'text' \|\| mode === 'preview'/.test(source), 'preview mode hides the wrap button because no CodeMirror editor is shown');
  assert.ok(/findButton && mode === 'preview'/.test(source), 'preview mode hides Search because the CodeMirror search panel is not available there');
  assert.equal(source.includes('file-editor-pure-preview'), false, 'old side-preview-only editor mode class is removed');
  assert.equal(source.includes('isFilePreviewItem'), false, 'old file-preview tab type is removed from runtime');
  assert.ok(/function updatePanelSlot[\s\S]*panel\.dataset\.layoutItem = session[\s\S]*isFileEditorItem\(session\)[\s\S]*renderFileEditorPanel\(panel, session, \{updateActiveFile: !dockviewLayoutActive\(\)\}\)/.test(source), 'switching a pane to a file editor tab re-renders editor chrome without making Dockview background renders active');
});

test('t@2463', () => {
  const api = loadYolomux('', ['1']);
  const section = new TestElement('section');
  section.rect = {left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500};
  const row = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  assert.equal(api.splitPercentForPointer(section, row, {clientX: 10, clientY: 0}), 32);
  assert.equal(api.splitPercentForPointer(section, row, {clientX: 990, clientY: 0}), 68);
  const nested = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 50);
  assert.equal(api.layoutNodeMinWidth(nested), 960);
});

test('t@2474', () => {
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
});

test('t@2515', () => {
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
});

test('t@2532', () => {
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
  api.selectSession('1', {userInitiated: true});
  assert.equal(api.focusedPanelItemForTest(), '1');
  assert.equal(focusCount, 2, 'selecting an already visible tmux tab is an explicit user focus action');
});

test('t@2548', () => {
  const api = loadYolomux('', ['1']);
  api.setFocusedTerminal('1');
  assert.equal(api.focusedPanelItemForTest(), '1');
  assert.equal(api.lastActivePaneItemForTest(), '1');
  assert.equal(api.visualActivePaneItemForTest(), '1');
  api.clearFocusedTerminal('1');
  assert.equal(api.focusedPanelItemForTest(), null, 'terminal blur clears keyboard pane focus');
  assert.equal(api.lastActivePaneItemForTest(), '1', 'terminal blur keeps the visual active pane');
  assert.equal(api.visualActivePaneItemForTest(), '1', 'visual active pane survives terminal blur');
});

test('t@2560', () => {
  const api = loadYolomux('', ['1', '2']);
  api.setFileExplorerTreeDateModeForTest('date');
  assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,files,preferences,image-viewer,file-editor');
  assert.equal(api.debugModeEnabledForTest(), false, 'JS Debug pane is off without the debug=1 URL flag');
  assert.equal(api.resolveLayoutItem('debug'), 'debug', 'debug layout item is ignored while the URL flag is off');
  // #40: YO!info and YO!agent are merged into the single info item; the legacy yoagent/yosup aliases
  // resolve to it so saved layouts and bookmarked ?…=yoagent URLs open the merged pane.
  assert.equal(api.resolveLayoutItem('yoagent'), api.infoItemId, 'yoagent alias resolves to the merged YO!info item');
  assert.equal(api.resolveLayoutItem('yosup'), api.infoItemId, 'legacy yosup URL param resolves to the merged item');
  assert.equal(api.resolveLayoutItem('__yosup__'), api.infoItemId, 'legacy yosup item id resolves to the merged item');
  assert.equal(api.resolveLayoutItem('__yoagent__'), api.infoItemId, 'legacy yoagent item id resolves to the merged item');
  api.setFileExplorerModeForTest('files');
  assert.equal(api.resolveLayoutItem('changes'), api.fileExplorerItemId, 'legacy changes URL param resolves to the Finder pane');
  assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes URL param preselects Finder diff mode');
  api.setFileExplorerModeForTest('files');
  assert.equal(api.resolveLayoutItem('__changes__'), api.fileExplorerItemId, 'legacy changes item id resolves to the Finder pane');
  assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes item id preselects Finder diff mode');
  assert.equal(api.resolveLayoutItem('files'), api.fileExplorerItemId, 'files alias still resolves to Finder');
  assert.equal(api.itemParam(api.infoItemId), 'info', 'the merged pane uses the info param');
  assert.equal(api.tabTypeForItem('__files__').key, 'files');
  assert.equal(api.tabTypeForItem('__changes__'), null, 'standalone Changes tab type is removed');
  assert.equal(api.tabTypeForItem('image:/home/test/screen.png').key, 'image-viewer');
  assert.equal(api.tabTypeForItem('file:/home/test/README.md').key, 'file-editor');
  assert.equal(api.fileItemPath('image:/home/test/screen.png'), '/home/test/screen.png');
  api.setSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
    repos: [{repo: '/repo/app', count: 3, touched_count: 4, added: 10, removed: 1, behind: 0, ahead: 2}],
    files: [
      {session: '1', agent: 'codex', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
      {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: 'src/new.py', abs_path: '/repo/app/src/new.py', mtime: 200, added: 8, removed: 0, diff_tracked: true},
      {session: '1', agent: 'codex', status: '?', repo: '/repo/app', path: 'src/raw.txt', abs_path: '/repo/app/src/raw.txt', mtime: 220, added: 4, removed: 0, diff_tracked: false},
      {session: '1', agent: 'codex', status: 'T', repo: '/repo/app', path: 'src/touched-only.py', abs_path: '/repo/app/src/touched-only.py', mtime: 300, added: 0, removed: 0, source: 'transcript'},
    ],
  });
  api.setFileExplorerModeForTest('diff');
  const changesHtml = api.fileExplorerChangesPanelHtml();
  assert.ok(changesHtml.includes('/repo/app'));
  const repoHeadStart = changesHtml.indexOf('class="changes-repo-head"');
  const repoHead = changesHtml.slice(repoHeadStart, changesHtml.indexOf('</button>', repoHeadStart));
  assert.ok(/changes-repo-caret[\s\S]*changes-repo-title[^>]*>\/repo\/app<[\s\S]*changes-repo-totals[\s\S]*changes-diff-add[^>]*>\+10<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*changes-repo-count[^>]*>3<\/span>/.test(repoHead), 'repo disclosure header shows repo name, tracked +added, -removed, and file count');
  assert.equal(/Behind|Ahead/.test(repoHead), false, 'ahead/behind stays out of the repo disclosure header');
  assert.ok(changesHtml.includes('1 repo, 3 files changed in &#39;1&#39;'), 'Finder diff summary names repo count, file count, and session explicitly');
  const comparisonSummaryStart = changesHtml.indexOf('class="changes-comparison-summary"');
  const comparisonSummary = changesHtml.slice(comparisonSummaryStart, changesHtml.indexOf('</div>', comparisonSummaryStart));
  assert.equal(/changes-summary-totals|changes-diff-add|changes-diff-remove|changes-repo-count/.test(comparisonSummary), false, 'Finder diff summary does not repeat global +line/-line/file totals');
  assert.ok(/changes-repo-refs[\s\S]*changes-repo-compare-title[\s\S]*Comparing[\s\S]*data-diff-ref-from[\s\S]*to[\s\S]*data-diff-ref-to/.test(changesHtml), 'Finder diff shows a per-repo comparison row with inline FROM/TO controls');
  assert.equal((changesHtml.match(/repo, 3 files changed in &#39;1&#39;/g) || []).length, 1, '#24: the repo/file-count summary appears exactly once (in the comparison card), not duplicated in the toolbar');
  assert.equal(changesHtml.includes('class="changes-summary"'), false, '#24: the standalone toolbar summary duplicate is removed');
  assert.ok(changesHtml.includes('class="changes-comparison-summary"'), '#24: the summary lives in the comparison card');
  assert.equal(changesHtml.includes('touched-only.py'), false, 'Differ hides transcript-only T rows that have no real diff');
  const fullToolbarSlice = changesHtml.slice(changesHtml.indexOf('changes-toolbar'), changesHtml.indexOf('</div>', changesHtml.indexOf('changes-toolbar')));
  assert.equal(fullToolbarSlice.includes('data-session-files-session'), false, 'full Differ body toolbar no longer repeats the Session dropdown');
  assert.ok(/data-session-files-sort[\s\S]*data-file-explorer-tree-dates[\s\S]*data-session-files-refresh/.test(fullToolbarSlice), 'full Differ body toolbar keeps Sort, date mode, and Reload');
  assert.ok(/data-file-explorer-tree-dates[\s\S]*data-file-tree-expand-collapse-all="expand"[\s\S]*data-file-tree-expand-collapse-all="collapse"[\s\S]*data-session-files-refresh/.test(fullToolbarSlice), 'full Differ body toolbar orders Date, Expand all, Collapse all, Reload');
  assert.ok(changesHtml.includes('Behind 0 commits'), 'Finder diff shows behind count');
  assert.ok(changesHtml.includes('Ahead 2 commits'), 'Finder diff shows ahead count');
  assert.ok(changesHtml.includes('file-tree-name">src'), 'Finder diff groups nested paths under folders');
  assert.ok(changesHtml.includes('data-changes-folder-toggle="/repo/app/src"'), 'Changes tree folders are collapsible by a stable key');
  assert.ok(changesHtml.includes('data-open-change-directory="/repo/app/src"'), 'Changes tree folders carry the absolute directory path for the context menu');
  assert.ok(changesHtml.includes('data-change-rel="src"'), 'Changes tree folders carry the relative directory path for copy');
  assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'), 'file leaves keep the open-file action');
  assert.ok(changesHtml.includes('changes-diff-add">+8</span>'), 'changed-file rows include green added counts');
  assert.ok(changesHtml.includes('changes-diff-add-neutral">+4</span>'), 'non-git-diff raw added counts stay visible but neutral');
  assert.ok(changesHtml.includes('changes-file-agent'), 'changed-file rows show the agent icon slot');
  assert.ok(changesHtml.includes('file-tree-row kind-file git-modified has-agent'), 'changed-file rows use the shared file-tree row renderer and inline-agent layout');
  assert.ok(/file-tree-git-status"[^>]*title="M: modified"[^>]*aria-label="M: modified"[^>]*>M<\/span>/.test(changesHtml), 'changed-file rows show and label the M status badge in the shared file-tree status slot');
  assert.ok(/file-tree-git-status"[^>]*title="A: added"[^>]*aria-label="A: added"[^>]*>A<\/span>/.test(changesHtml), 'added changed-file rows label the A status badge');
  assert.ok(changesHtml.includes('file-tree-dir-count">2</span>'), 'changed-file folders show a recursive changed-file count from the shared row renderer');
  assert.ok(changesHtml.includes('file-tree-icon'), 'changed-file rows show a file-type icon slot');
  assert.ok(changesHtml.includes('file-tree-date'), 'changed-file rows wrap the date for skinny styling');
  assert.ok(/class="file-tree-row kind-dir[^"]*"[^>]*data-path="\/repo\/app\/src"[\s\S]*<span class="file-tree-date"[^>]*>[^<]+<\/span>/.test(changesHtml), 'Differ directory rows show the same non-empty date slot as Finder');
  assert.ok(/class="[^"]*file-explorer-date-toggle[^"]*changes-date-toggle[^"]*active[^"]*"[^>]*data-file-explorer-tree-dates[^>]*>Date<\/button>/.test(changesHtml), 'Finder diff toolbar exposes the active-colored shared Finder date-mode button');
  const collapseToggleHtml = api.fileExplorerChangesCollapseToggleHtml();
  assert.ok(collapseToggleHtml.includes('data-session-files-collapse-toggle'), 'Differ top row exposes a collapse/expand all toggle');
  assert.ok(collapseToggleHtml.includes('Collapse all'), 'Differ collapse toggle starts in collapse-all state');
  assert.equal(collapseToggleHtml.includes('▤'), false, 'Differ collapse toggle does not use the old square-looking Finder icon');
  assert.equal(api.fileExplorerChangesAllReposCollapsedForTest(), false, 'Differ repos start expanded');
  api.toggleAllFileExplorerChangesForTest();
  assert.equal(api.fileExplorerChangesAllReposCollapsedForTest(), true, 'Differ collapse toggle collapses every repo');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), false, 'collapsed Differ repo hides file leaves');
  assert.ok(api.fileExplorerChangesCollapseToggleHtml().includes('Expand all'), 'Differ collapse toggle switches to expand-all state');
  api.toggleAllFileExplorerChangesForTest();
  assert.equal(api.fileExplorerChangesAllReposCollapsedForTest(), false, 'Differ collapse toggle expands every repo again');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), 'expanded Differ repo shows file leaves again');
  api.setChangesFolderCollapsedForTest(['/repo/app/src']);
  const collapsedChangesHtml = api.fileExplorerChangesPanelHtml();
  assert.ok(collapsedChangesHtml.includes('file-tree-row kind-dir collapsed'), 'collapsed changed-file folders keep their state');
  assert.equal(collapsedChangesHtml.includes('data-open-change-file="/repo/app/src/new.py"'), false, 'collapsed changed-file folders hide file leaves');
  const differExpandCollapseSource = {closest: selector => selector === '.file-explorer-changes-panel' ? {} : null};
  api.setAllFileTreeDirectoriesExpandedForTest(differExpandCollapseSource, false);
  assert.deepStrictEqual(canonical(api.changesRepoCollapsedForTest()), ['/repo/app'], 'Differ Collapse all collapses every repo root section');
  assert.deepStrictEqual(canonical(api.changesFolderCollapsedForTest()), ['/repo/app/src'], 'Differ Collapse all records every changed directory row as collapsed');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), false, 'Differ Collapse all hides changed file leaves');
  api.setAllFileTreeDirectoriesExpandedForTest(differExpandCollapseSource, true);
  assert.deepStrictEqual(canonical(api.changesRepoCollapsedForTest()), [], 'Differ Expand all expands repo root sections');
  assert.deepStrictEqual(canonical(api.changesFolderCollapsedForTest()), [], 'Differ Expand all expands changed directory rows');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), 'Differ Expand all shows changed file leaves');
  api.setChangesFolderCollapsedForTest([]);
  assert.ok(changesHtml.includes('data-diff-ref-from'), 'Finder diff exposes FROM ref picker');
  assert.ok(changesHtml.includes('data-diff-ref-to'), 'Finder diff exposes TO ref picker');
  assert.ok(changesHtml.includes('data-diff-ref-input'), 'Finder diff exposes text ref pickers');
  assert.ok(changesHtml.includes('data-diff-ref-reset'), 'Finder diff exposes the shared FROM/TO reset button');
  assert.ok(changesHtml.includes(`aria-label="${api.t('diff.ref.reset')}"`), 'Diff reset buttons carry an aria-label');
  assert.ok(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(changesHtml), 'Finder diff FROM ref picker is a text input with the compact suggestion popup');
  assert.ok(/<input(?=[^>]*data-diff-ref-to)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(changesHtml), 'Finder diff TO ref picker is a text input with the compact suggestion popup');
  assert.equal(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*list=)[^>]*>/.test(changesHtml), false, 'FROM ref picker does not use the browser-native datalist popup');
  assert.equal(/<datalist/.test(changesHtml), false, 'diff ref pickers render no native datalist menu');
  // C6: the FROM/TO controls are now scoped to each repo header (data-diff-ref-repo), not one global pair.
  assert.ok(changesHtml.includes('data-diff-ref-repo="/repo/app"'), 'C6: each repo header carries its own scoped FROM/TO controls');
  const toolbarSlice = changesHtml.slice(changesHtml.indexOf('changes-toolbar'), changesHtml.indexOf('</div>', changesHtml.indexOf('changes-toolbar')));
  assert.equal(toolbarSlice.includes('data-diff-ref'), false, 'C6: the global FROM/TO pair is gone from the Changes toolbar (now per-repo)');
  assert.ok(changesHtml.includes('changes-repo-compare-title'), 'C6: each repo header shows its own comparison title');
  // C6: per-repo suggestions — an unknown repo offers only HEAD as a FROM base (no cross-repo SHAs).
  assert.equal(api.diffRefFromSuggestions('/no/such/repo').length, 1, 'C6: an unknown repo offers only HEAD as a FROM base');
  assert.equal(/<select[^>]*data-diff-ref-from/.test(changesHtml), false, 'FROM control is a text input, not a select');
  assert.equal(/<select[^>]*data-diff-ref-to/.test(changesHtml), false, 'TO control is a text input, not a select');
  assert.ok(api.diffRefFromSuggestions('/repo/app').some(item => item.subject === 'older base commit'), 'Finder diff FROM picker has recent commit subjects available to the popup');
  assert.equal(api.diffRefFromSuggestions().some(item => item.ref === 'current'), false, 'FROM picker does not suggest current as the older base');
  assert.equal(api.diffRefToSuggestions('HEAD').map(item => item.ref).join(','), 'current', 'TO picker only offers refs newer than the selected FROM base');
  api.setDiffRefsByRepoForTest('/repo/app', {from: '611d3bb', to: '56c8fc4'});
  assert.equal(api.fileRepoForPath('/repo/app/README.md'), '/repo/app', 'C6: editor diff refs use the exact changed-file repo when the file is in the Modified-files payload');
  api.setSessionFilesPayloadForTest({session: '1', loaded: true, errors: [], refs_by_repo: {}, repos: [{repo: '/repo/app'}], files: []});
  assert.equal(api.fileRepoForPath('/repo/app/src/unchanged.py'), '/repo/app', 'C6: editor diff refs can infer the repo from the Modified-files repo header even when the file row is absent');
  api.setSessionFilesPayloadForTest({session: '1', loaded: true, errors: [], refs_by_repo: {}, repos: [], files: []});
  api.setOpenFileStateForTest('/repo/app/src/unchanged.py', {kind: 'text', diffRepo: '/repo/app'});
  const loadedEditorRepo = api.fileRepoForPath('/repo/app/src/unchanged.py');
  assert.equal(loadedEditorRepo, '/repo/app', 'C6: editor diff refs fall back to the repo returned by a previous /api/fs/diff payload');
  assert.equal(api.diffRefParams(loadedEditorRepo).from, '611d3bb', 'C6: an editor-only diff repo still picks the repo-scoped FROM ref');
  assert.equal(api.diffRefParams(loadedEditorRepo).to, '56c8fc4', 'C6: an editor-only diff repo still picks the repo-scoped TO ref');
  const duplicateShaOptions = api.diffRefSelectOptionsHtml('abc123def4567890', {suggestions: [{ref: 'abc123d', short: 'abc123d', subject: 'same commit'}]});
  assert.equal((duplicateShaOptions.match(/<option/g) || []).length, 1, 'diff ref picker dedupes full SHA and short SHA for the same commit');
  assert.equal(duplicateShaOptions.includes('selected ref'), false, 'diff ref picker does not add a synthetic duplicate for a selected SHA already in suggestions');
  const manyDiffRefs = Array.from({length: 120}, (_, index) => ({ref: `${String(index).padStart(7, 'a')}abcdef`, short: `r${index}`, subject: `commit ${index}`}));
  const changedFilesSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(api.diffRefPopoverItems('', {compact: true, suggestions: manyDiffRefs, showAll: true}).length, 12, 'compact diff-ref popups are capped to avoid huge menus');
  assert.equal(api.diffRefPopoverItems('', {compact: false, suggestions: manyDiffRefs, showAll: true}).length, 18, 'full diff-ref popups are capped to a compact menu size');
  assert.deepEqual(api.diffRefPopoverItems('commit 117', {compact: true, suggestions: manyDiffRefs}).map(item => item.subject), ['commit 117'], 'typing filters the diff-ref popup to matching refs/subjects');
  assert.ok(/function diffRefComparisonLineHtml[\s\S]*diffRefResetButtonHtml\(refs\)/.test(changedFilesSource), 'Editor and Differ FROM/TO rows share the same reset button helper');
  assert.ok(/function diffRefControlsHtml[\s\S]*diffRefResetButtonHtml\(refs\)/.test(changedFilesSource), 'compact Editor FROM/TO controls use the shared reset button helper');
  assert.ok(/function diffRefResetButtonHtml[\s\S]*t\('pref\.reset\.row'\)[\s\S]*<\/button>/.test(changedFilesSource), 'Diff reset button visibly says Reset');
  assert.equal(/function diffRefResetButtonHtml[\s\S]*>⇤<\/button>/.test(changedFilesSource), false, 'Diff reset button does not use a glyph-only label');
  assert.equal(/function diffRefResetButtonHtml[\s\S]*>↺<\/button>/.test(changedFilesSource), false, 'Diff reset button does not use the reload-looking circular arrow');
  assert.ok(/function commitDiffRefControls[\s\S]*dataset\?\.diffRefPath[\s\S]*setRepoDiffRefs\(repo, fromInput\?\.value, toInput\?\.value, \{path\}\)/.test(changedFilesSource), 'editor diff ref commits carry the file path so no-history files do not fall back to repo history');
  assert.ok(/function syncDiffRefControlValues[\s\S]*dataset\?\.diffRefPath[\s\S]*diffRefFromSuggestions\(repo, path\)[\s\S]*diffRefToSuggestions\(refs\.from, repo, path\)/.test(changedFilesSource), 'editor diff ref sync keeps using file-scoped history after rerenders');
  assert.ok(/status\.textContent !== statusText[\s\S]*date\.textContent !== dateText/.test(changedFilesSource), 'Differ/Finder metadata slots avoid rewriting unchanged status/date text');
  assert.ok(/setClassNameIfChanged\(icon,[\s\S]*file-icon-dir-indexed/.test(changedFilesSource), 'indexed directory icon class is not rewritten when unchanged');
  // the picker-open is now the shared openDiffRefPickerForInput helper (used by both the
  // changes panel and the file-editor diff-ref toolbar) instead of two inline copies.
  assert.ok(/function openDiffRefPickerForInput\([^)]*\)\s*\{[\s\S]*?showDiffRefPicker\(input, \{showAll: true\}\)/.test(changedFilesSource), 'clicking/focusing a filled ref input still shows available options (via shared openDiffRefPickerForInput)');
  assert.ok(changedFilesSource.includes('openDiffRefPickerForInput(diffRefInput, diffRefInput.closest('), 'changes panel routes ref-input focus/pointer through the shared picker-open helper');
  assert.ok(/const minWidth = Math\.min\(compact \? 880 : 960, viewportWidth - 16\)/.test(changedFilesSource), 'diff-ref popup is wide enough for normal 80-char commit subjects');
  assert.ok(/const maxWidth = compact \? 1040 : 1120/.test(changedFilesSource), 'diff-ref popup can expand beyond the old narrow 620px cap');
  assert.ok(/document\.addEventListener\('scroll', event =>[\s\S]*positionDiffRefPopover\(diffRefPopoverInput, context\.compact\)/.test(changedFilesSource), 'scrolling around an open diff-ref popup repositions it instead of closing it');
  assert.equal(changedFilesSource.includes('.showPicker('), false, 'diff refs do not use the browser-native popup API');
  api.setFileExplorerSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    refs_by_repo: {'/repo/app': [{ref: 'HEAD', short: 'HEAD', subject: 'base commit'}, {ref: 'current', short: 'current', subject: 'working tree'}]},
    repos: [{repo: '/repo/app', count: 0, touched_count: 1, added: 0, removed: 0, from_ref: 'HEAD', to_ref: 'current', behind: 0, ahead: 0}],
    files: [],
  });
  const emptyExplicitRepoHtml = api.fileExplorerChangesPanelHtml();
  assert.ok(emptyExplicitRepoHtml.includes('/repo/app'), 'Differ keeps an explicit repo section visible even when the selected refs have zero file diffs');
  assert.ok(/changes-repo-refs[\s\S]*data-diff-ref-from[\s\S]*data-diff-ref-to/.test(emptyExplicitRepoHtml), 'empty explicit repo section still exposes FROM/TO controls');
  assert.ok(emptyExplicitRepoHtml.includes('No Differ results for this session.'), 'empty explicit repo section explains that the selected refs have no visible file rows');
  assert.equal(emptyExplicitRepoHtml.includes('data-open-change-file='), false, 'empty explicit repo section does not invent transcript-only file rows');
  assert.ok(changedFilesSource.includes("panel.addEventListener('dblclick', async event => {"), 'modified-file rows open from a double-click handler');
  const compactChangeHtml = api.changesGroupsSnapshotHtmlForTest([
    {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
  ], {compact: true});
  const gitStatusClassCases = {
    A: 'git-untracked',
    U: 'git-untracked',
    '?': 'git-untracked',
    D: 'git-deleted',
    S: 'git-staged',
    M: 'git-modified',
    T: 'git-transcript',
  };
  for (const [status, expectedClass] of Object.entries(gitStatusClassCases)) {
    assert.equal(api.gitStatusRowClass(status), expectedClass, `shared git status row class for ${status}`);
  }
  assert.ok(/file-tree-name[^>]*>README\.md<\/span>[\s\S]*file-tree-agent[\s\S]*changes-file-agent[\s\S]*file-tree-diff[\s\S]*changes-diff-add[^>]*>\+2<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*file-tree-git-status[^>]*>M<\/span>[\s\S]*file-tree-date/.test(compactChangeHtml), 'compact changed-file row order is file, AI icon, counts, status, date');
  assert.ok(/file-tree-git-status[^>]*title="M: modified"[^>]*aria-label="M: modified"[^>]*>M<\/span>/.test(compactChangeHtml), 'compact changed-file M badge explains itself on hover');
  assert.equal(changesHtml.includes('>codex<'), false, 'changed-file rows do not spell out the agent kind');
  assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'));
  assert.ok(changesHtml.includes('data-open-change-status="A"'), 'changed-file clicks carry status for deleted-file diff opens');
  assert.ok(changedFilesSource.includes("const isAddedChange = normalizedStatus === 'A' || normalizedStatus === 'U' || normalizedStatus === '?';"), 'added/untracked changed files open through editable mode first');
  assert.ok(changedFilesSource.includes("const isTouchedOnly = normalizedStatus === 'T';"), 'touched-only transcript rows are recognized separately from diffable rows');
  assert.ok(changedFilesSource.includes("const initialMode = isAddedChange || isTouchedOnly ? 'edit' : 'diff';"), 'added/untracked/touched rows open through editable mode first');
  assert.ok(changedFilesSource.includes("viewMode: initialMode"), 'non-diff rows fall back to normal editor mode instead of forcing diff');
  assert.ok(/async function openChangedFileInDiff\([^]*?const payloadRepoRefs = \(\(\) => \{[^]*?\}\)\(\);[\s\S]*?noteFileExplorerChangesSessionInteraction\(ownerSession\)[\s\S]*?openFileInEditor/.test(changedFilesSource), 'opening a changed-file row commits its owner session to the Differ panel after preserving row FROM/TO refs');
  const touchedOnlyHtml = api.changesGroupsSnapshotHtmlForTest([
    {session: '1', agent: 'codex', status: 'T', repo: '/repo/app', path: 'src/merged.py', abs_path: '/repo/app/src/merged.py', mtime: 100, source: 'transcript'},
  ], {compact: true});
  assert.ok(touchedOnlyHtml.includes('git-transcript') && touchedOnlyHtml.includes('>T</span>'), 'touched-only transcript rows carry a neutral T status badge');
  assert.ok(/file-tree-git-status[^>]*title="T: touched by AI transcript"[^>]*aria-label="T: touched by AI transcript"[^>]*>T<\/span>/.test(touchedOnlyHtml), 'touched-only T badge explains itself on hover');
  // C5: agent attribution renders 0-to-N icons from item.agents (Claude before Codex), with a screen-
  // reader label when more than one appears.
  const zeroAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: [], status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
  assert.equal(zeroAgentRow.includes('changes-file-agent'), false, 'C5: a file with no transcript attribution renders zero agent icons');
  const oneAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: ['codex'], status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
  assert.ok(oneAgentRow.includes('agent-icon codex') && !oneAgentRow.includes('agent-icon claude'), 'C5: one agent renders exactly one icon');
  assert.ok(/agent-icon codex"[^>]*aria-label="modified by Codex [^"]* ago"[^>]*title="modified by Codex [^"]* ago"/.test(oneAgentRow), 'C5: Codex icon hover names who modified the file and when');
  const twoAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: ['codex', 'claude'], status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
  assert.ok(twoAgentRow.includes('agent-icon claude') && twoAgentRow.includes('agent-icon codex'), 'C5: a file touched by both agents renders both icons');
  assert.ok(twoAgentRow.indexOf('agent-icon claude') < twoAgentRow.indexOf('agent-icon codex'), 'C5: agent icons order Claude before Codex');
  assert.ok(/changes-file-agent(?![^>]*title=)[^>]*aria-label="modified by Claude [^"]* ago, modified by Codex [^"]* ago"/.test(twoAgentRow), 'C5: the multi-agent slot is labeled for screen readers without a generic native tooltip');
  assert.ok(/agent-icon claude"[^>]*title="modified by Claude [^"]* ago"/.test(twoAgentRow), 'C5: Claude icon hover names who modified the file and when');
  assert.ok(/agent-icon codex"[^>]*title="modified by Codex [^"]* ago"/.test(twoAgentRow), 'C5: Codex icon hover stays contextual in multi-agent rows');
  const legacyAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
  assert.ok(legacyAgentRow.includes('agent-icon codex'), 'C5: legacy scalar agent payloads still render their icon');
  // C5: image rows carry size + relative path and DROP the native title (the rich hover preview replaces it);
  // non-image rows keep the full-path title.
  const imageRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: [], status: 'A', repo: '/repo/app', path: 'pic.png', abs_path: '/repo/app/pic.png', mtime: 1, size: 4096}], {});
  assert.ok(imageRow.includes('file-tree-row kind-file git-untracked'), 'C5: added uploaded rows use the same git-untracked class as Finder rows');
  assert.ok(/file-tree-git-status[^>]*title="A: added"[^>]*aria-label="A: added"[^>]*>A<\/span>/.test(imageRow), 'C5: added status badge explains itself on hover');
  assert.ok(imageRow.includes('data-change-size="4096"'), 'C5: image rows carry the file size for preview gating');
  assert.ok(imageRow.includes('data-change-rel="pic.png"'), 'C5: rows carry the relative path for Copy path');
  assert.equal(/title="[^"]*pic\.png"/.test(imageRow), false, 'C5: image rows drop the native title so it does not duplicate the hover preview');
  // C5 / per-render row binder + shared Finder selection/context menu for Modified-files rows.
  assert.ok(changedFilesSource.includes('function bindChangedFileRowBehaviors('), 'C5: a per-render binder hooks Modified-files rows');
  assert.ok(/function bindChangedFileRowBehaviors\([\s\S]*?bindFileImagePreview\(row, path/.test(changedFilesSource), 'C5: image rows get the Finder hover preview');
  assert.equal(changedFilesSource.includes('function selectChangedFileRow('), false, 'Differ no longer has bespoke single-row selected state');
  assert.equal(changedFilesSource.includes('function showChangedFileContextMenu('), false, 'Differ file rows no longer fork a safe-only context menu');
  assert.ok(/data-open-change-file[\s\S]{0,260}updateFileTreeSelectionFromClick\(fileRow,\s*fileRow\.dataset\.path/.test(changedFilesSource), 'Differ click selection routes through the shared Finder selection parent');
  assert.ok(/data-open-change-file[\s\S]{0,360}showFileTreeContextMenu\(fileRow,\s*path,\s*changedFileRowEntry\(fileRow\)/.test(changedFilesSource), 'Differ file right-click routes through the shared Finder context menu, including Delete');
  assert.ok(/async function deleteFileTreePath[\s\S]*fetchSessionFiles\(\{destination: 'finder', session: fileExplorerSessionFilesTargetSession\(\), silent: true, force: true\}\)/.test(changedFilesSource), 'shared delete refreshes session-files so Differ rows disappear immediately');
  assert.ok(changedFilesSource.includes('function showChangedDirectoryContextMenu('), 'C5: Modified-files folder rows have a right-click menu');
  const dirCtxStart = changedFilesSource.indexOf('function showChangedDirectoryContextMenu(');
  const dirCtxBody = changedFilesSource.slice(dirCtxStart, changedFilesSource.indexOf('\nfunction ', dirCtxStart + 1));
  for (const action of ['Copy relative path', 'Copy full path', 'Expand ']) {
    assert.ok(dirCtxBody.includes(action), `C5: the changed-directory menu offers "${action}"`);
  }
  assert.ok(dirCtxBody.includes('copyChangedPath(rel || path'), 'C5: directory Copy relative path uses the folder-relative path when present');
  assert.ok(/function openChangedDirectoryInFinder\([\s\S]*?openFileExplorerPane\(\)[\s\S]*?setFileExplorerMode\('files'\)[\s\S]*?expandFileExplorerTreesToPath\(path\)[\s\S]*?selectFileTreePath\(path\)/.test(changedFilesSource), 'C5: Modified-files folder menu switches to Finder mode and expands the directory in-place');
  assert.equal(/'Open in new tab'|'Download'|'Rename'|'Delete'|"Open in new tab"|"Download"|"Rename"|"Delete"/.test(dirCtxBody), false, 'C5: the Modified-files folder menu stays directory-only and non-destructive');
  assert.ok(/contextmenu'[\s\S]*?data-open-change-file[\s\S]*?showFileTreeContextMenu[\s\S]*?data-open-change-directory[\s\S]*?showChangedDirectoryContextMenu/.test(changedFilesSource), 'right-click dispatches file rows through the shared Finder menu and folder rows through the directory menu');
  assert.ok(changedFilesSource.includes("multiple ? 'Copy full paths' : 'Copy full path'"), 'Finder context menu uses Copy full path label');
  const fileExplorerSource = (fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8'));
  assert.equal(/Copy raw paths?/.test(fileExplorerSource), false, 'Finder context menu no longer exposes a duplicate raw path action');
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
  api.setFileExplorerModeForTest('diff');
  const uploadedCollapsedHtml = api.fileExplorerChangesPanelHtml();
  assert.ok(uploadedCollapsedHtml.includes('Uploaded files (1)'), 'uploaded files render under a named disclosure group');
  assert.equal(uploadedCollapsedHtml.includes('20260531-028.png</span>'), false, 'uploaded files are collapsed by default');
  api.setUploadedFilesCollapsedForTest(false);
  const uploadedExpandedHtml = api.fileExplorerChangesPanelHtml();
  assert.ok(uploadedExpandedHtml.includes('20260531-028.png</span>'), 'expanded uploaded group shows uploaded rows');
  const uploadedSectionHtml = uploadedExpandedHtml.slice(uploadedExpandedHtml.indexOf('changes-uploaded-group'));
  assert.ok(uploadedSectionHtml.includes('file-tree-row'), 'expanded uploaded group uses the shared Finder/Differ tree row renderer');
  assert.ok(uploadedSectionHtml.includes('file-tree-icon file-icon-image'), 'uploaded image rows use the shared image icon class');
  assert.equal(uploadedSectionHtml.includes('changes-file-row'), false, 'uploaded rows no longer use the legacy row renderer with separators');
  assert.equal(/changes-file-path[^>]*>20260531-028\.png</.test(uploadedExpandedHtml), false, 'uploaded Differ rows do not repeat the basename as the secondary path line');
  api.setFileExplorerModeForTest('files');
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
  assert.ok(api.fileExplorerChangesPanelHtml().includes('Differ:'), 'Finder embeds a Differ panel');
  assert.ok(/class="changes-title">Differ: &#39;1&#39;<\/span>/.test(api.fileExplorerChangesPanelHtml()), 'C7: the embedded Differ title uses the compact session title');
  api.setFileExplorerModeForTest('diff');
  api.setDiffRefsByRepoForTest('/repo/app', {from: 'abc1111', to: 'current'});
  const firstDiffCacheKey = api.sessionFilesCacheKeyForTest('1');
  api.setDiffRefsByRepoForTest('/repo/app', {from: 'def2222', to: 'current'});
  const secondDiffCacheKey = api.sessionFilesCacheKeyForTest('1');
  assert.notEqual(firstDiffCacheKey, secondDiffCacheKey, 'Differ cache key changes when repo FROM/TO refs change for the same session');
  api.setDiffRefsByRepoForTest('/repo/app', null);
  api.setFileExplorerModeForTest('files');
  {
    const stickyApi = loadYolomux('', ['1', '2']);
    stickyApi.setFileExplorerChangesSelectedSessionForTest('1');
    assert.equal(stickyApi.fileExplorerSessionFilesTargetSessionForTest(), '1', 'Finder Modified-files target starts from the committed session');
    stickyApi.noteFileExplorerChangesSessionInteractionForTest('2');
    assert.equal(stickyApi.fileExplorerSessionFilesTargetSessionForTest(), '2', 'explicit session interaction updates the Finder Modified-files target');
  }
  {
    const hoverApi = loadYolomux('', ['5', '6']);
    hoverApi.setTranscriptInfoForTest('5', {
      project: {git: {cwd: '/home/test/yolomux.dev/src', root: '/home/test/yolomux.dev'}},
      selected_pane: {current_path: '/home/test/yolomux.dev/src'},
    });
    hoverApi.setTranscriptInfoForTest('6', {
      project: {git: {cwd: '/home/test/other.dev/src', root: '/home/test/other.dev'}},
      selected_pane: {current_path: '/home/test/other.dev/src'},
    });
    hoverApi.setSessionFilesPayloadForTest({session: '5', repos: [{repo: '/home/test/yolomux.dev'}], files: []});
    hoverApi.noteFileExplorerChangesSessionInteractionForTest('5');
    hoverApi.setFileExplorerRootMode('sync', {sync: false});
    hoverApi.setAutoFocusEnabledForTest(true);
    hoverApi.selectPanelOnHover('6');
    assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'hover/autofocus does not commit the Finder Modified-files target');
    assert.equal(hoverApi.activeTmuxDirectoryPath(), '/home/test/yolomux.dev/src', 'hover/autofocus does not become the Finder tmux-directory source');
    assert.deepStrictEqual(canonical(hoverApi.fileExplorerSyncPlanForTest()), {
      session: '5',
      root: '/home/test/yolomux.dev',
      affectedDirs: ['/home/test/yolomux.dev'],
      expandPaths: [],
    }, 'hover/autofocus keeps Finder Sync planned from the explicit session');
    hoverApi.setFocusedTerminal('6');
    assert.equal(hoverApi.activeTmuxDirectoryPath(), '/home/test/yolomux.dev/src', 'passive xterm focus does not become the Finder tmux-directory source');
    hoverApi.selectSession('6');
    assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'passive selectSession does not commit the Finder Modified-files target');
    hoverApi.selectSession('6', {userInitiated: true});
    assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '6', 'explicit selectSession can commit the Finder Modified-files target');
    hoverApi.setFileExplorerChangesSelectedSessionForTest('5');
    hoverApi.noteFileExplorerChangesSessionInteractionForTest('6');
    assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '6', 'explicit session interaction can still commit the Finder Modified-files target');
  }
  // C15/C6: the redundant global "N files changed in '1'" summary and global comparison line are gone;
  // each repo owns its own compact comparison line instead.
  const compactFinderPanel = api.fileExplorerChangesPanelHtml();
  const compactHeadStart = compactFinderPanel.indexOf('class="changes-repo-head"');
  const compactRepoHead = compactFinderPanel.slice(compactHeadStart, compactFinderPanel.indexOf('</button>', compactHeadStart));
  assert.ok(/changes-repo-totals[\s\S]*changes-diff-add[^>]*>\+2<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*changes-repo-count[^>]*>2<\/span>/.test(compactRepoHead), 'Finder Modified-files repo header shows the repo aggregate totals');
  assert.equal(compactFinderPanel.includes('files changed in'), false, 'C15: the compact header drops the redundant file-count/session summary');
  assert.equal(/changes-comparison-head compact[\s\S]*?Comparing/.test(compactFinderPanel), false, 'C6: Finder Modified-files has no global comparison line');
  assert.ok(/changes-repo-refs compact[\s\S]*?diff-ref-inline[\s\S]*?data-diff-ref-from[\s\S]*?data-diff-ref-to/.test(compactFinderPanel), 'C6: Finder Modified-files exposes per-repo inline FROM/TO controls');
  assert.ok(/changes-repo-refs compact[\s\S]*?data-diff-ref-repo="\/repo\/app"/.test(compactFinderPanel), 'C6: the inline comparison inputs are scoped to the repo');
  assert.ok(/changes-repo-refs compact[\s\S]*?Ahead 1 commit/.test(compactFinderPanel), 'C6: ahead/behind lives on the repo comparison row');
  assert.equal(compactFinderPanel.includes('changes-repo-popover'), false, 'C6: repo comparison details are visible, not hidden in a hover popover');
  // C15: ahead/behind is hidden when 0 (the popover shows only the non-zero ahead, not "Behind 0 commits").
  assert.equal(compactFinderPanel.includes('Behind 0 commit'), false, 'C15: 0-commit ahead/behind is not printed');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('class="changes-title"'), 'Finder modified-files header has a responsive title cell');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('diff-ref-controls compact'), 'Finder modified-files panel exposes compact diff refs');
  // C8/C13 follow-up: Finder embedded Differ now uses the same compact select control pattern as Finder's
  // own A-Z/Z-A/recent/oldest sort control, defaulting to recent.
  const finderSortPanel = api.fileExplorerChangesPanelHtml();
  assert.ok(/<select class="[^"]*file-explorer-sort-select[^"]*changes-sort-select[^"]*changes-sort-select-compact[^"]*"[^>]*data-session-files-sort/.test(finderSortPanel), 'Finder embedded Differ sort uses the shared compact select styling');
  assert.ok(/data-file-explorer-tree-dates[\s\S]*data-file-tree-expand-collapse-all="expand"[\s\S]*data-file-tree-expand-collapse-all="collapse"[\s\S]*data-session-files-refresh/.test(finderSortPanel), 'Finder embedded Differ header orders Date, Expand all, Collapse all, Reload');
  const sortLabels = {az: 'finder.sort.az', za: 'finder.sort.za', newest: 'finder.sort.newest', oldest: 'finder.sort.oldest'};
  for (const [value, key] of Object.entries(sortLabels)) {
    const label = api.t(key).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    assert.ok(new RegExp(`<option value="${value}"[^>]*>${label}</option>`).test(finderSortPanel), `Finder embedded Differ sort offers ${value}`);
  }
  assert.ok(new RegExp(`<option value="newest"[^>]* selected[^>]*>${api.t('finder.sort.newest')}</option>`).test(finderSortPanel), 'Finder embedded Differ sort defaults to recent');
  assert.equal(/changes-sort-toggle|changes-sort-seg|changes-sort-divider/.test(finderSortPanel), false, 'Finder embedded Differ no longer uses a separate segmented sort control');
  const sortItems = [
    {path: 'b.txt', repo: '/repo/app', mtime: 100},
    {path: 'a.txt', repo: '/repo/app', mtime: 200},
    {path: 'c.txt', repo: '/repo/app', mtime: 50},
  ];
  api.setSessionFilesSortModeForTest('az');
  assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'Differ A-Z sorts by path ascending');
  api.setSessionFilesSortModeForTest('za');
  assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['c.txt', 'b.txt', 'a.txt'], 'Differ Z-A sorts by path descending');
  api.setSessionFilesSortModeForTest('newest');
  assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'Differ recent sorts by newest mtime first');
  api.setSessionFilesSortModeForTest('oldest');
  assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['c.txt', 'b.txt', 'a.txt'], 'Differ oldest sorts by oldest mtime first');
  api.setSessionFilesSortModeForTest('mtime');
  assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'legacy Differ mtime value maps to New');
  api.setSessionFilesSortModeForTest('name');
  assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'legacy Differ name value maps to A-Z');
  const renderedSortItems = [
    {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'b.txt', abs_path: '/repo/app/b.txt', mtime: 100},
    {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 200},
    {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'c.txt', abs_path: '/repo/app/c.txt', mtime: 50},
  ];
  api.setFileExplorerSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    refs_by_repo: {},
    repos: [{repo: '/repo/app', count: 3, touched_count: 3, added: 0, removed: 0}],
    files: renderedSortItems,
  });
  const renderedDifferOrder = () => Array.from(api.fileExplorerChangesPanelHtml().matchAll(/data-open-change-file="\/repo\/app\/([^"]+)"/g)).map(match => match[1]);
  api.setSessionFilesSortModeForTest('az');
  assert.deepEqual(renderedDifferOrder(), ['a.txt', 'b.txt', 'c.txt'], 'rendered Differ tree A-Z follows the Differ sort select');
  api.setSessionFilesSortModeForTest('za');
  assert.deepEqual(renderedDifferOrder(), ['c.txt', 'b.txt', 'a.txt'], 'rendered Differ tree Z-A follows the Differ sort select');
  api.setSessionFilesSortModeForTest('newest');
  assert.deepEqual(renderedDifferOrder(), ['a.txt', 'b.txt', 'c.txt'], 'rendered Differ tree New follows the Differ sort select');
  api.setSessionFilesSortModeForTest('oldest');
  assert.deepEqual(renderedDifferOrder(), ['c.txt', 'b.txt', 'a.txt'], 'rendered Differ tree Old follows the Differ sort select');
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
  api.setSessionFilesSortModeForTest('newest');
  assert.ok(/class="[^"]*file-explorer-date-toggle[^"]*changes-date-toggle[^"]*active[^"]*"[^>]*data-file-explorer-tree-dates[^>]*>Date<\/button>/.test(finderSortPanel), 'Finder embedded Differ header exposes the active-colored shared date-mode button');
  assert.ok(/class="changes-refresh"[^>]*>Reload<\/button>/.test(finderSortPanel), 'C13: the Reload button reads "Reload" (via i18n, not hardcoded)');
  assert.ok(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(api.fileExplorerChangesPanelHtml()), 'Finder compact modified-files header exposes the FROM text picker with the compact popup');
  assert.ok(/<input(?=[^>]*data-diff-ref-to)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(api.fileExplorerChangesPanelHtml()), 'Finder compact modified-files header exposes the TO text picker with the compact popup');
  assert.equal(/<datalist/.test(api.fileExplorerChangesPanelHtml()), false, 'Finder compact diff refs do not render native datalists');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('data-session-files-display-toggle'), false, '#41: the modified-files density toggle is removed');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('file-tree-row kind-file compact'), '#41: the Finder modified-files panel is always compact');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-file-explorer-changes-close'), '#44: the Modified-files header has a close (X) button to hide the section');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('>Compact</button>'), false, 'Finder density toggle is an icon, not paired text buttons');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-add">+2</span>'), 'Finder modified-files panel shows green added counts');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-remove">-1</span>'), 'Finder modified-files panel shows red removed counts');
  // C9: the per-session detail bar shows a "+N repos" chip when the session touches more than one repo,
  // and no chip for a single-repo session. Clicking the chip opens a popover that scopes the Finder.
  const multiRepoInfo = {
    agents: [], selected_pane: {current_path: '/repo/app'},
    project: {
      git: {root: '/repo/app', branch: 'main', dirty_count: 0}, pull_request: null, linear: [],
      repos: [
        {root: '/repo/app', branch: 'main', dirty_count: 0, primary: true},
        {root: '/repo/lib', branch: 'feature', dirty_count: 2, ahead: 1, primary: false},
      ],
    },
  };
  const multiMetaHtml = api.projectMetaHtml('1', multiRepoInfo);
  assert.ok(/data-repo-chip="1"/.test(multiMetaHtml), 'C9: a multi-repo session shows a +N repos chip');
  assert.ok(multiMetaHtml.includes('+1 '), 'C9: the chip counts the EXTRA repos (2 repos -> +1)');
  const singleRepoInfo = {...multiRepoInfo, project: {...multiRepoInfo.project, repos: [multiRepoInfo.project.repos[0]]}};
  assert.equal(api.projectMetaHtml('1', singleRepoInfo).includes('meta-repo-chip'), false, 'C9: a single-repo session shows no chip');
  const c9Src = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(c9Src.includes('function showRepoChipMenu('), 'C9: the +N repos chip opens a popover');
  assert.ok(/showRepoChipMenu\([\s\S]*?openFileExplorerAt\(root\)/.test(c9Src), 'C9: clicking a repo row scopes the Finder to that repo');
  // C10: Finder delete shortcut — Command-Delete on Mac, plain Delete on PC, gated to the Finder surface
  // and taking precedence over the global Mod+Delete tab-close.
  assert.ok(c9Src.includes('function handleFileExplorerDeleteShortcut('), 'C10: a Finder delete-shortcut handler exists');
  const globalShortcutBody = c9Src.slice(c9Src.indexOf('function handleGlobalShortcutKeydown(event)'), c9Src.indexOf("window.addEventListener('keydown', handleGlobalShortcutKeydown, true)"));
  assert.ok(globalShortcutBody.indexOf('handleFocusedTerminalCopyShortcut(event)') >= 0, 'terminal copy guard is part of the global shortcut path');
  assert.ok(globalShortcutBody.indexOf('handleFocusedTerminalCopyShortcut(event)') < globalShortcutBody.indexOf('handleFileExplorerDeleteShortcut(event)'), 'terminal copy guard runs before other global shortcuts');
  assert.ok(globalShortcutBody.indexOf('handleFileExplorerDeleteShortcut(event)') < globalShortcutBody.indexOf("if (mod && key === 'w')"), 'C10: Finder delete runs before the global tab-close shortcut');
  const c10Body = c9Src.slice(c9Src.indexOf('function handleFileExplorerDeleteShortcut('), c9Src.indexOf('function beginFileTreeRename('));
  assert.ok(c10Body.includes('isMacPlatform()') && c10Body.includes('event.metaKey === true'), 'C10: Mac requires Command (metaKey)');
  assert.ok(/key !== 'delete' \|\| event\.metaKey/.test(c10Body), 'C10: PC requires a plain Delete (no modifiers)');
  assert.ok(c10Body.includes('eventTargetIsFileExplorerSurface(event.target)'), 'C10: the shortcut is scoped to the Finder surface');
  assert.ok(c10Body.includes('globalShortcutTargetAllowsAppAction(event.target)'), 'C10: the shortcut is suppressed in text/rename inputs');
  assert.ok(c10Body.includes('fileExplorerSelectedPaths') && c10Body.includes('deleteFileTreePath('), 'C10: it deletes the selected paths via the existing delete flow');
  assert.ok(c10Body.includes('readOnlyMode'), 'C10: readonly is blocked through the existing delete guard');
  const changedFilesCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.file-tree-git-status\s*\{[\s\S]*width:\s*13px/.test(changedFilesCss), 'modified-file status chips use the shared skinny Finder badge');
  // #46: Modified-files rows match the Finder file tree — the row uses the file-explorer font size and
  // the filename carries no semibold/bold weight (regular, not big bold white).
  assert.equal(changedFilesCss.includes('.changes-file-row'), false, '#46: modified-file rows use shared Finder file-tree rows, not standalone changes-file-row CSS');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*--changes-indent-line:\s*rgba\(148,\s*163,\s*184,\s*0\.50\)/.test(changedFilesCss), 'Differ/Finder changes trees use a brighter dark-mode guide line');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*--changes-repo-head-bg:\s*color-mix\(in srgb,\s*var\(--panel2\) 88%,\s*var\(--text\) 12%\)/.test(changedFilesCss), 'Differ/Finder changes repo headers use a brighter dark-mode scoped background token');
  assert.ok(/body\.theme-light \.file-explorer-changes-panel,[\s\S]*--changes-indent-line:\s*var\(--tree-indent-line\);[\s\S]*--changes-repo-head-bg:\s*var\(--panel2\)/.test(changedFilesCss), 'light-mode Differ/Finder changes tree guide and repo header tokens stay unchanged');
  assert.equal(/(?:^|\n)\.changes-file-name\s*\{[^}]*font-weight/.test(changedFilesCss), false, '#46: modified-file names carry no bold/semibold weight override');
  assert.equal(changedFilesCss.includes('.changes-tree-folder'), false, 'Differ folders use the shared Finder tree renderer, not a stale changes-tree-folder CSS path');
  assert.equal(changedFilesSource.includes('function changeFileRowHtml('), false, 'Differ rows are not rendered through the legacy standalone row builder');
  assert.ok(/\.changes-repo-title\s*\{[\s\S]*color:\s*var\(--accent-gold\)[\s\S]*font-weight:\s*800/.test(changedFilesCss), 'Modified-files repo names are gold and bold');
  assert.ok(/\.changes-repo-totals\s*\{[\s\S]*margin-inline-start:\s*auto/.test(changedFilesCss), 'Modified-files repo totals are pinned to the right side of the disclosure header');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*--changes-folder-text:\s*var\(--accent-gold\)/.test(changedFilesCss), 'Finder diff inherits the same gold subdirectory token');
  assert.ok(/body\.theme-light\b[\s\S]*?--tree-indent-line:\s*rgba\(100,\s*116,\s*139,\s*0\.12\)/.test(changedFilesCss), 'light-mode tree connector lines are subdued (shared --tree-indent-line token)');
  assert.ok(/\.file-tree-git-status\.file-tree-git-status-unknown\s*\{[\s\S]*?background:\s*rgba\(226,\s*232,\s*240,\s*0\.10\)/.test(changedFilesCss), 'shared Finder/Differ unknown status chips are faint-neutral in dark mode');
  // Purple is RESERVED for MERGED PR status: the untracked/unknown change badge must NOT be purple
  // (it was #a78bfa, reading as a merged indicator next to the PR badges); the merged PR status keeps it.
  assert.ok(/\.file-tree-row\.git-untracked:not\(\.selected\) \.file-tree-git-status:not\(\[hidden\]\)\s*\{[\s\S]*background:\s*var\(--git-untracked-badge\)/.test(changedFilesCss), 'untracked badge uses the shared git status token, not the merged PR token');
  assert.ok(/--input-bg\s*:/.test(changedFilesCss), 'input background token is defined instead of relying on a dark fallback');
  assert.equal(/var\(--input-bg,\s*#[0-9a-fA-F]{3,8}\)/.test(changedFilesCss), false, 'input controls do not hide dark literals inside token fallbacks');
  assert.equal(/z-index:\s*(?:260|220)\b/.test(changedFilesCss), false, 'file editor z-index collision literals are routed through named tokens');
  assert.equal(changedFilesCss.includes('#a78bfa'), false, 'the old untracked purple is gone — no non-merged status uses purple');
  assert.ok(changedFilesCss.includes('body.theme-light .changes-comparison-head'), 'light theme explicitly restyles the Changes comparison header');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*overflow-y:\s*scroll/.test(changedFilesCss), 'Finder modified-files scrollbar stays visible');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*scrollbar-gutter:\s*stable/.test(changedFilesCss), 'Finder modified-files reserves scrollbar gutter');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*container-type:\s*inline-size/.test(changedFilesCss), 'Finder modified-files header uses pane-width container queries');
  assert.ok(changedFilesCss.includes('@container (max-width: 520px)'), 'Finder modified-files header wraps before narrow pane widths overlap');
  assert.ok(changedFilesCss.includes('grid-template-areas:'), 'Finder modified-files narrow header uses explicit row areas');
  assert.ok(changedFilesCss.includes('body.theme-light .file-explorer'), 'light theme explicitly restyles the Finder tree');
  assert.ok(changedFilesCss.includes('body.theme-light .file-explorer-changes-panel'), 'light theme explicitly restyles Finder modified-files');
  assert.ok(changedFilesCss.includes('.file-tree-row.kind-file .file-tree-name'), 'Finder filenames resolve to row text colors instead of inherited stale colors');
  assert.ok(/\.file-explorer-changes-panel \.changes-refresh::before[\s\S]*?\{\s*content:\s*"↻"/.test(changedFilesCss), 'Finder embedded Differ refresh paints a visible refresh icon');
  assert.ok(/\.file-explorer-date-reload-cluster \.changes-refresh::before\s*\{[\s\S]*content:\s*"↻"/.test(changedFilesCss), 'Finder date/reload cluster refresh paints the same visible refresh icon');
  assert.ok(/body\.theme-light \.file-explorer-changes-panel \.changes-refresh[\s\S]*?\{\s*background:\s*transparent/.test(changedFilesCss), 'light-mode embedded Differ refresh is not a blank white square');
  assert.ok(changedFilesCss.includes('--file-hover-bg: #fff2a8'), 'light-mode Finder/Differ row hover uses a yellow highlighter fill');
  assert.ok(/\.file-tree-row:not\(\.selected\):hover\s*\{[\s\S]*background:\s*var\(--file-hover-bg\)[\s\S]*box-shadow:\s*inset 4px 0 0 var\(--file-hover-border\)/.test(changedFilesCss), 'Finder/Differ hover rows use the shared yellow highlighter tokens without overriding selected rows');
  assert.ok(/\.file-tree-row\.current-file:not\(\.selected\)\s*\{[\s\S]*color:\s*var\(--file-selection-text\)[\s\S]*background:\s*var\(--file-selection-bg\)[\s\S]*box-shadow:\s*inset 4px 0 0 var\(--file-selection-border\)/.test(changedFilesCss), 'Finder Sync current file reuses the selected-row color tokens');
  assert.ok(c9Src.includes('function scheduleFileExplorerActiveFileReveal('), 'Finder active-file reveal uses a shared helper');
  assert.ok(/function showFileEditorPaneForPath\([\s\S]*?scheduleFileExplorerActiveFileReveal\(path\)/.test(c9Src), 'opening an editor file reveals it in the current Finder root');
  assert.ok(/const previousActiveFile = activeFile;[\s\S]*?if \(previousActiveFile !== path\) scheduleFileExplorerActiveFileReveal\(path\)/.test(c9Src), 'switching editor tabs reveals the newly active file without re-expanding on every render');
  assert.ok(changedFilesCss.includes('flex-wrap: wrap;'), 'Finder toolbar wraps instead of clipping quick-access controls');
  assert.ok(/\.file-explorer-quick-access,\s*\.file-explorer-quick-access-panel\s*\{[\s\S]*flex:\s*0 0 auto/.test(changedFilesCss), 'Finder quick-access buttons do not shrink out of view');
  const fakeChangesScroll = {scrollTop: 45, scrollLeft: 3, innerHTML: ''};
  api.replaceHtmlPreservingScroll(fakeChangesScroll, '<div>updated</div>');
  assert.equal(fakeChangesScroll.innerHTML, '<div>updated</div>');
  assert.equal(fakeChangesScroll.scrollTop, 45, 'modified-files refresh preserves vertical scroll');
  assert.equal(fakeChangesScroll.scrollLeft, 3, 'modified-files refresh preserves horizontal scroll');
  // #149/#150: the edit view no longer auto-loads the diff or paints inline diff decorations.
  // Changes are shown ONLY in the explicit diff VIEW (the MergeView). The inline-decoration helpers and
  // the edit-mode auto-load are removed; parseUnifiedDiffLineClasses/codeMirrorDiffLineExtension are gone.
  const editSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(/function parseUnifiedDiffLineClasses/.test(editSrc), false, '#150: parseUnifiedDiffLineClasses is deleted (no inline diff decorations)');
  assert.equal(/function codeMirrorDiffLineExtension/.test(editSrc), false, '#150: codeMirrorDiffLineExtension is deleted');
  assert.equal(/state\.diffLineClasses/.test(editSrc), false, '#150: the dead state.diffLineClasses is removed');
  const renderPanelBody = editSrc.slice(editSrc.indexOf('function renderFileEditorPanel'), editSrc.indexOf('function renderFileEditorPanel') + 4000);
  assert.equal(/!state\.diffLoaded && !state\.diffLoading && !state\.diffUnavailable/.test(renderPanelBody), false, '#149: renderFileEditorPanel no longer auto-loads the diff on open/render');
  const filesTab = api.fileExplorerPaneTabHtml();
  assert.equal(api.fileExplorerLabel(), 'File Explorer');
  assert.ok(filesTab.includes('File Explorer'));
  const appSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(appSource.includes("const editorViewModes = new Set(['edit', 'preview', 'split', 'diff'])"), 'file editor registers diff as a real view mode');
  assert.ok(appSource.includes('new api.MergeView'), 'wide diff mode uses CodeMirror MergeView');
  assert.ok(appSource.includes('api.unifiedMergeView'), 'narrow diff mode uses CodeMirror unified merge view');
  assert.ok(appSource.includes('function reconfigureCodeMirrorPanelEditorOptions'), 'word wrap and line-number toggles live-reconfigure CodeMirror instead of rebuilding');
  assert.ok(appSource.includes('function codeMirrorLineWrappingExtension'), 'word wrap has an explicit CodeMirror line-wrapping helper');
  assert.ok(appSource.includes("api.EditorView?.contentAttributes?.of?.({class: 'cm-lineWrapping'})"), 'word wrap falls back to CodeMirror contentAttributes when EditorView.lineWrapping is not exported');
  assert.ok(appSource.includes("&& (api?.EditorView?.lineWrapping || api?.EditorView?.contentAttributes?.of)"), 'CodeMirror API validation requires a usable wrapping extension path');
  assert.ok(/function codeMirrorWrapMarkerExtension\(api\)[\s\S]{0,140}const scheme = activeEditorScheme\(\)/.test(appSource), 'wrap marker plugin defines its active editor scheme before using scheme.dark');
  const wrapApplyStart = appSource.indexOf('function applyEditorWrapPreference');
  const wrapApplyBody = appSource.slice(wrapApplyStart, appSource.indexOf('function setEditorWrapEnabled', wrapApplyStart));
  assert.ok(wrapApplyBody.includes('codeMirrorCurrentText(panel)'), 'wrap toggles capture the live CodeMirror document before any fallback render');
  assert.ok(wrapApplyBody.indexOf('reconfigureCodeMirrorPanelEditorOptions(panel)') > 0, 'wrap toggles try the live CodeMirror reconfigure path');
  assert.ok(wrapApplyBody.indexOf('reconfigureCodeMirrorPanelEditorOptions(panel)') < wrapApplyBody.indexOf('renderFileEditorPanel(panel'), 'wrap toggles reconfigure before falling back to a full render');
  assert.equal(appSource.includes('allowInlineDiffs: true'), false, 'unified diff uses native deleted chunks so removed lines are not editable doc lines');
  // #17: deleted rows carry NO line number and stay read-only — done structurally, not via a CSS
  // transparent-text hack. The unified diff edits the MODIFIED document and overlays the original
  // through unifiedMergeView, so deleted lines are merge-decoration widgets (read-only, unnumbered),
  // never real numbered document lines. (No real CodeMirror in this Node harness, so this guards the
  // construction that produces the rendered behaviour, which was verified visually.)
  assert.ok(appSource.includes('const unifiedMergeOptions = {') && appSource.includes('original,'), 'unified diff options carry the original document for deleted-line widgets');
  assert.ok(appSource.includes('doc: currentText,'), 'unified diff edits the modified/current document');
  assert.ok(appSource.includes('api.unifiedMergeView(unifiedMergeOptions)'), 'unified diff overlays the original via unifiedMergeView, so deleted rows are unnumbered read-only widgets');
  const diffGutterCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.equal(/\.cm-deletedLineGutter\s*\{[^}]*color:\s*transparent/.test(diffGutterCss), false, '#17: deleted-line numbers are suppressed natively by unifiedMergeView, not by a transparent-text gutter hack');
  // C6: editor diffs carry the FILE'S REPO refs (per-repo), not a single global pair.
  // When explicit fromRef/toRef are provided (e.g. from the Modified-files panel click), they take precedence;
  // otherwise falls back to diffRefQueryString(fileRepoForPath(path)) so per-repo selections still apply.
  assert.ok(appSource.includes('diffRefQueryString(fileRepoForPath(path))'), 'editor diff requests fall back to per-repo FROM/TO refs');
  assert.ok(appSource.includes("const explicitFromRef = options.fromRef || ''") && appSource.includes("const explicitToRef = options.toRef || ''"), 'editor diff accepts explicit FROM/TO override from Modified-files panel click');
  assert.ok(appSource.includes('state.diffPinnedFromRef = explicitFromRef ||') && appSource.includes('state.diffPinnedToRef = explicitToRef ||'), 'explicit Modified-files refs are pinned on the open file state');
  assert.ok(appSource.includes('state.diffPinnedFromRef || state.diffPinnedToRef'), 'later diff refreshes reuse explicit file-level refs instead of falling back to HEAD/current');
  assert.ok(appSource.includes("state.diffPinnedFromRef = '';") && appSource.includes("state.diffPinnedToRef = '';"), 'repo-level FROM/TO changes clear any file-level explicit ref pins');
  assert.ok(appSource.includes("const diffTargetIsCurrent = !state.diffToRef || state.diffToRef === 'current';"), 'diff editor editability follows TO=current after the FROM/TO flip');
  assert.ok(appSource.includes('const diffEditsAllowed = diffTargetIsCurrent;'), 'diff editor allows edits on the new/current side');
  assert.ok(/function destroyCodeMirrorPanel[\s\S]*\.cm-diff-overview'\)\?\.remove\(\)/.test(appSource), '#26: tearing down the CodeMirror panel removes the diff scrollbar overview so its red/green rail does not linger in edit/normal mode');
  // the right overview is one linear-gradient derived from CodeMirror's rendered diff-row sequence.
  assert.ok(/function buildDiffOverviewGradientFromBands[\s\S]*linear-gradient\(to bottom/.test(appSource), 'diff overview builds one linear-gradient from non-overlapping row bands');
  assert.ok(/function diffOverviewCodeMirrorChunks[\s\S]*view\?\.state\?\.values[\s\S]*fromA[\s\S]*fromB/.test(appSource), 'diff overview reads CodeMirror merge chunks from the live EditorView state');
  assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*const chunks = diffOverviewCodeMirrorChunks\(view, panel\)[\s\S]*diffOverviewRowsFromCodeMirrorRenderedWeights\(view, chunks, currentText, original, container\)[\s\S]*\|\| diffOverviewRowsFromCodeMirrorChunks\(chunks, currentText, original\)/.test(appSource), 'diff overview derives the right rail from CodeMirror chunk rows, with text visual-row weights for wrap');
  assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*if \(!chunkRows && view\) \{[\s\S]*scheduleDiffOverviewReadinessRebuild\(panel\);[\s\S]*return;/.test(appSource), '#50: live CodeMirror diff views wait for chunks instead of drawing the raw-diff fallback rail');
  assert.ok(/function diffOverviewScrollLooksCurrentOnly[\s\S]*scrollTarget\.scrollHeight[\s\S]*diffOverviewLineHeight[\s\S]*return scrollRows < threshold/.test(appSource), '#55/#56/#57: diff overview detects CodeMirror current-side-only scroll geometry before deleted widgets are mounted');
  assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*diffOverviewScrollLooksCurrentOnly\(chunkRows, scrollTarget, view, container\)[\s\S]*scheduleDiffOverviewSettledRebuild\(panel\)[\s\S]*return;/.test(appSource), '#55/#56/#57: pre-snap current-side-only editors defer the rail until CodeMirror mounts deleted rows');
  assert.equal(appSource.includes('diffOverviewRowsFromCurrentSideOnly'), false, '#55/#56/#57: current-side-only pre-snap state does not draw a temporary green rail');
  assert.equal(appSource.includes('diffOverviewRowsFromCodeMirrorGeometry'), false, 'diff overview does not infer red bands from lineBlockAt pixel gaps');
  assert.ok(/function updateCodeMirrorDiffOverviewGeometry[\s\S]*scrollTarget\.clientHeight[\s\S]*overview\.style\.height/.test(appSource), 'diff overview rail is dynamically sized to the native vertical scrollbar track, excluding the horizontal scrollbar area');
  assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*overview\.style\.background = gradient/.test(appSource), 'diff overview writes the gradient onto the rail itself');
  assert.equal(appSource.includes("cm-diff-overview-tick"), false, 'diff overview no longer creates per-chunk tick DOM');
  assert.equal(appSource.includes('diffOverviewRenderedRows'), false, 'diff overview no longer samples rendered DOM rows for color ticks');
  // ...and the neutral viewport indicator still REBUILDS when a fold expands/collapses (geometry/height change).
  assert.ok(/function codeMirrorDiffOverviewListener[\s\S]*?update\.geometryChanged \|\| update\.heightChanged[\s\S]*?scheduleDiffOverviewRebuild/.test(appSource), 'B3: a CM updateListener rebuilds the overview on geometry/fold change');
  assert.ok(/panel\._diffOverviewCtx = \{container, state, currentText, original\}/.test(appSource), 'B3: the overview build stores its context so the fold-rebuild can recompute from live geometry');
  const parseOverviewStops = gradient => {
    const stops = [];
    const pattern = /(#[0-9a-f]{6}|transparent)\s+([0-9.]+)%\s+([0-9.]+)%/gi;
    let match = pattern.exec(String(gradient || ''));
    while (match) {
      stops.push({
        color: match[1].toLowerCase(),
        startText: match[2],
        endText: match[3],
        start: Number.parseFloat(match[2]),
        end: Number.parseFloat(match[3]),
      });
      match = pattern.exec(String(gradient || ''));
    }
    return stops;
  };
  const changedOverviewStops = gradient => parseOverviewStops(gradient).filter(stop => stop.color !== 'transparent');
  const assertNoOverviewStopOverlap = stops => {
    for (let index = 1; index < stops.length; index += 1) {
      assert.equal(stops[index - 1].end <= stops[index].start, true, 'adjacent diff overview stops never overlap');
    }
  };
  const positionedDiff = [
    'diff --git a/a.txt b/a.txt',
    '--- a/a.txt',
    '+++ b/a.txt',
    '@@ -11 +11 @@',
    '-old display row ten',
    '+new display row eleven',
  ].join('\n');
  const positionedGradient = api.buildDiffOverviewGradientForTest(positionedDiff, 100);
  assert.ok(positionedGradient.startsWith('linear-gradient(to bottom,'), 'diff overview gradient has the expected CSS function shape');
  const positionedStops = changedOverviewStops(positionedGradient);
  assert.deepEqual(positionedStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
    {color: UI_PINS.diffOverviewDelete, start: '10.000', end: '11.000'},
    {color: UI_PINS.diffOverviewAdd, start: '11.000', end: '12.000'},
  ], 'diff overview maps consecutive removed/added rows to adjacent one-line gradient slices');
  assert.equal(positionedStops[0].endText, positionedStops[1].startText, 'consecutive red/green rows share one exact boundary');
  assertNoOverviewStopOverlap(positionedStops);
  assert.deepEqual([...new Set(positionedStops.map(stop => stop.color))].sort(), [UI_PINS.diffOverviewAdd, UI_PINS.diffOverviewDelete], 'diff overview uses only the red and green row colors for changed stops');
  assert.equal(/rgba|var\(|\bred\b|\bgreen\b/.test(positionedGradient), false, 'diff overview gradient uses literal row colors only');
  assert.equal(api.buildDiffOverviewGradientForTest('', 100), null, 'empty diffs do not draw an overview gradient');
  const makeLines = (count, label) => Array.from({length: count}, (_, index) => `${label} ${index + 1}`).join('\n');
  const largePrefix = `${makeLines(21, 'same')}\n`;
  const largeRemoved = makeLines(540, 'removed');
  const largeAdded = makeLines(199, 'added');
  const largeTail = makeLines(50, 'tail');
  const largeOriginal = `${largePrefix}${largeRemoved}\n${largeTail}`;
  const largeCurrent = `${largePrefix}${largeAdded}\n${largeTail}`;
  const largeChunk = {
    fromA: largePrefix.length,
    toA: largePrefix.length + largeRemoved.length,
    fromB: largePrefix.length,
    toB: largePrefix.length + largeAdded.length,
  };
  const largeRows = api.diffOverviewRowsFromCodeMirrorChunksForTest([largeChunk], largeCurrent, largeOriginal);
  assert.deepEqual(largeRows.bands, [
    {kind: 'remove', start: 21, end: 561},
    {kind: 'add', start: 561, end: 760},
  ], 'large CodeMirror replacement chunks render as normal prefix, red deleted widget rows, then green changed current rows');
  assert.equal(largeRows.currentLineCount, 270, 'large replacement current side includes all current rows');
  assert.equal(largeRows.deletedRows, 540, 'large replacement deleted CodeMirror widget rows are counted in the denominator');
  assert.equal(largeRows.totalRows, 810, 'large replacement overview denominator is current rows plus CodeMirror deleted widget rows');
  const currentOnlyView = {
    defaultLineHeight: 20,
    state: {doc: {lines: 270}},
    contentDOM: {classList: {contains: () => false}},
  };
  assert.equal(api.diffOverviewScrollLooksCurrentOnlyForTest(largeRows, {scrollHeight: 270 * 20}, currentOnlyView, null), true, '#55/#56/#57 live scroller height near current rows selects the pre-snap model');
  assert.equal(api.diffOverviewScrollLooksCurrentOnlyForTest(largeRows, {scrollHeight: 810 * 20}, currentOnlyView, null), false, '#55/#56/#57 full diff scroller height keeps the red+green model after CodeMirror mounts deleted widgets');
  const currentOnlyContainer = new TestElement('current-only-diff-container');
  const currentOnlyScroller = new TestElement('current-only-diff-scroller');
  currentOnlyScroller.className = 'cm-scroller';
  currentOnlyScroller.clientHeight = 400;
  currentOnlyScroller.scrollHeight = 270 * 20;
  currentOnlyContainer.appendChild(currentOnlyScroller);
  api.setDiffExpandUnchangedForTest(true);
  api.updateCodeMirrorDiffOverviewForTest(
    {
      _cmMode: 'diff',
      _diffOverviewWaitingForDeletedRows: true,
      _cmMergeView: {chunks: [largeChunk]},
      _cmView: {
        scrollDOM: currentOnlyScroller,
        defaultLineHeight: 20,
        state: {doc: {lines: 270}, values: [[largeChunk]]},
        contentDOM: {classList: {contains: () => false}},
      },
    },
    currentOnlyContainer,
    {diff: ''},
    largeCurrent,
    largeOriginal,
  );
  assert.equal(currentOnlyContainer.querySelector('.cm-diff-overview'), null, '#55/#56/#57 current-side-only CodeMirror geometry draws no temporary rail before the deleted widgets settle');
  const largeStops = changedOverviewStops(api.buildDiffOverviewGradientFromBandsForTest(largeRows.bands, largeRows.totalRows));
  assert.deepEqual(largeStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
    {color: UI_PINS.diffOverviewDelete, start: '2.593', end: '69.259'},
    {color: UI_PINS.diffOverviewAdd, start: '69.259', end: '93.827'},
  ], 'large replacement gradient allocates the green band CodeMirror paints, not only literal raw additions');
  assert.equal(largeStops[0].endText, largeStops[1].startText, 'large replacement red and green bands share one boundary and never overlap');
  assertNoOverviewStopOverlap(largeStops);
  const makeOverviewFixtureLines = (count, label) => Array.from({length: count}, (_, index) => `${label} ${String(index + 1).padStart(3, '0')}`);
  // Screenshot #48 repro shape from:
  //   git diff e01be55 7f5a7e8 -- static_src/js/yolomux/99_terminal_boot.js
  // The hunk starts at line 164, removes 30 rows, then adds 80 current-side rows.
  const screenshotPrefix = makeOverviewFixtureLines(163, 'same');
  const screenshotRemoved = makeOverviewFixtureLines(30, 'removed');
  const screenshotAdded = makeOverviewFixtureLines(80, 'added');
  const screenshotTail = makeOverviewFixtureLines(40, 'tail');
  const screenshotOriginal = [...screenshotPrefix, ...screenshotRemoved, ...screenshotTail].join('\n');
  const screenshotCurrent = [...screenshotPrefix, ...screenshotAdded, ...screenshotTail].join('\n');
  const lineStartsForTest = text => {
    const starts = [0];
    for (let index = 0; index < text.length; index += 1) {
      if (text.charCodeAt(index) === 10) starts.push(index + 1);
    }
    return starts;
  };
  const offsetForLineForTest = (starts, lineNumber) => starts[Math.max(0, lineNumber - 1)] ?? starts[starts.length - 1] ?? 0;
  const screenshotOriginalStarts = lineStartsForTest(screenshotOriginal);
  const screenshotCurrentStarts = lineStartsForTest(screenshotCurrent);
  const screenshotChunk = {
    fromA: offsetForLineForTest(screenshotOriginalStarts, 164),
    toA: offsetForLineForTest(screenshotOriginalStarts, 194),
    fromB: offsetForLineForTest(screenshotCurrentStarts, 164),
    toB: offsetForLineForTest(screenshotCurrentStarts, 244),
  };
  const screenshotRows = api.diffOverviewRowsFromCodeMirrorChunksForTest([screenshotChunk], screenshotCurrent, screenshotOriginal);
  const expectedScreenshotStops = changedOverviewStops(api.buildDiffOverviewGradientFromBandsForTest(screenshotRows.bands, screenshotRows.totalRows));
  assert.deepEqual(screenshotRows.bands, [
    {kind: 'remove', start: 163, end: 193},
    {kind: 'add', start: 193, end: 273},
  ], '#48 exact 99_terminal_boot.js e01be55..7f5a7e8 renders red deleted rows followed immediately by green current rows');
  assert.equal(expectedScreenshotStops[0].endText, expectedScreenshotStops[1].startText, '#48 red/green bands share one exact boundary');
  const screenshotContainer = new TestElement('screenshot-diff-container');
  const screenshotScroller = new TestElement('screenshot-diff-scroller');
  screenshotScroller.className = 'cm-scroller';
  screenshotScroller.clientHeight = 400;
  screenshotScroller.scrollHeight = 20000;
  screenshotContainer.appendChild(screenshotScroller);
  const screenshotDoc = {
    length: screenshotCurrent.length,
    lines: screenshotCurrentStarts.length,
    line(number) {
      return {from: offsetForLineForTest(screenshotCurrentStarts, number)};
    },
  };
  const screenshotPanel = {
    _cmMode: 'diff',
    _cmMergeView: {chunks: [screenshotChunk]},
    _cmView: {
      scrollDOM: screenshotScroller,
      state: {doc: screenshotDoc, values: [[screenshotChunk]]},
      contentHeight: 20000,
      lineBlockAt(pos) {
        const line = screenshotCurrentStarts.findIndex((start, index) => start <= pos && (screenshotCurrentStarts[index + 1] ?? Infinity) > pos) + 1;
        const lineNumber = Math.max(1, line);
        if (lineNumber < 164) return {top: (lineNumber - 1) * 20, bottom: lineNumber * 20};
        if (lineNumber <= 243) {
          const inflatedTop = 10000 + (lineNumber - 164) * 20;
          return {top: inflatedTop, bottom: inflatedTop + 20};
        }
        const tailTop = 11600 + (lineNumber - 244) * 20;
        return {top: tailTop, bottom: tailTop + 20};
      },
    },
  };
  api.setDiffExpandUnchangedForTest(true);
  api.updateCodeMirrorDiffOverviewForTest(screenshotPanel, screenshotContainer, {diff: ''}, screenshotCurrent, screenshotOriginal);
  const screenshotOverview = screenshotContainer.querySelector('.cm-diff-overview');
  const screenshotStops = changedOverviewStops(screenshotOverview?.style?.background || '');
  assert.deepEqual(
    screenshotStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})),
    expectedScreenshotStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})),
    '#48 production overview ignores misleading lineBlockAt gaps and matches the CodeMirror row stream exactly',
  );
  assertNoOverviewStopOverlap(screenshotStops);
  const wrappedCurrent = 'same\nwrapped current line\nnext';
  const wrappedOriginal = 'same\nold line\nnext';
  const wrappedCurrentStarts = lineStartsForTest(wrappedCurrent);
  const wrappedOriginalStarts = lineStartsForTest(wrappedOriginal);
  const wrappedChunk = {
    fromA: offsetForLineForTest(wrappedOriginalStarts, 2),
    toA: offsetForLineForTest(wrappedOriginalStarts, 3),
    fromB: offsetForLineForTest(wrappedCurrentStarts, 2),
    toB: offsetForLineForTest(wrappedCurrentStarts, 3),
  };
  const wrappedDoc = {
    length: wrappedCurrent.length,
    lines: wrappedCurrentStarts.length,
    line(number) {
      return {from: offsetForLineForTest(wrappedCurrentStarts, number)};
    },
  };
  const wrappedView = {
    defaultCharacterWidth: 8,
    defaultLineHeight: 20,
    contentDOM: {
      clientWidth: 64,
      classList: {contains: name => name === 'cm-lineWrapping'},
      getBoundingClientRect: () => ({width: 64}),
    },
    state: {doc: wrappedDoc},
    lineBlockAt(pos) {
      if (pos >= offsetForLineForTest(wrappedCurrentStarts, 3)) return {top: 80, bottom: 100};
      if (pos >= offsetForLineForTest(wrappedCurrentStarts, 2)) return {top: 20, bottom: 80};
      return {top: 0, bottom: 20};
    },
  };
  const wrappedRows = api.diffOverviewRowsFromCodeMirrorRenderedWeightsForTest(
    wrappedView,
    [wrappedChunk],
    wrappedCurrent,
    wrappedOriginal,
    null,
  );
  assert.deepEqual(wrappedRows.bands, [
    {kind: 'remove', start: 1, end: 2},
    {kind: 'add', start: 2, end: 5},
  ], '#49 wrap weights the green current line by its text visual-row estimate while keeping deleted rows explicit');
  assert.equal(wrappedRows.totalRows, 6, '#49 wrap denominator uses visual row weights: normal + red + wrapped green + normal');
  const deletedWrapCurrent = 'ok\nnew\nend';
  const deletedWrapOriginal = 'ok\nold deleted line\nend';
  const deletedWrapCurrentStarts = lineStartsForTest(deletedWrapCurrent);
  const deletedWrapOriginalStarts = lineStartsForTest(deletedWrapOriginal);
  const deletedWrapChunk = {
    fromA: offsetForLineForTest(deletedWrapOriginalStarts, 2),
    toA: offsetForLineForTest(deletedWrapOriginalStarts, 3),
    fromB: offsetForLineForTest(deletedWrapCurrentStarts, 2),
    toB: offsetForLineForTest(deletedWrapCurrentStarts, 3),
  };
  const deletedWrapDoc = {
    length: deletedWrapCurrent.length,
    lines: deletedWrapCurrentStarts.length,
    line(number) {
      return {from: offsetForLineForTest(deletedWrapCurrentStarts, number)};
    },
  };
  const deletedWrapView = {
    defaultCharacterWidth: 8,
    defaultLineHeight: 20,
    contentDOM: {
      clientWidth: 24,
      classList: {contains: name => name === 'cm-lineWrapping'},
      getBoundingClientRect: () => ({width: 24}),
    },
    state: {doc: deletedWrapDoc},
    lineBlockAt(pos) {
      if (pos >= offsetForLineForTest(deletedWrapCurrentStarts, 3)) return {top: 40, bottom: 60};
      if (pos >= offsetForLineForTest(deletedWrapCurrentStarts, 2)) return {top: 20, bottom: 40};
      return {top: 0, bottom: 20};
    },
  };
  const deletedWrapRows = api.diffOverviewRowsFromCodeMirrorRenderedWeightsForTest(
    deletedWrapView,
    [deletedWrapChunk],
    deletedWrapCurrent,
    deletedWrapOriginal,
    null,
  );
  assert.deepEqual(deletedWrapRows.bands, [
    {kind: 'remove', start: 1, end: 7},
    {kind: 'add', start: 7, end: 8},
  ], '#49 wrap estimates unmounted deleted rows from original text width instead of squeezing them to one row');
  assert.equal(deletedWrapRows.totalRows, 9, '#49 wrapped deleted rows contribute to the full rail denominator');
  assert.equal(appSource.includes("scrollTarget.addEventListener?.('scroll', rebuild"), false, '#51/#52: scrolling updates only the viewport box, not the red/green gradient');
  const notReadyContainer = new TestElement('not-ready-diff-container');
  const notReadyScroller = new TestElement('not-ready-diff-scroller');
  notReadyScroller.className = 'cm-scroller';
  notReadyContainer.appendChild(notReadyScroller);
  const notReadyPanel = {
    _cmMode: 'diff',
    _cmView: {scrollDOM: notReadyScroller, state: {doc: wrappedDoc, values: []}},
  };
  const notReadyDiff = [
    'diff --git a/a.txt b/a.txt',
    '--- a/a.txt',
    '+++ b/a.txt',
    '@@ -1,3 +1,3 @@',
    '-old one',
    '-old two',
    '+new one',
    '+new two',
    ' context',
  ].join('\n');
  api.setDiffExpandUnchangedForTest(true);
  api.updateCodeMirrorDiffOverviewForTest(
    notReadyPanel,
    notReadyContainer,
    {diff: notReadyDiff},
    'new one\nnew two\ncontext',
    'old one\nold two\ncontext',
  );
  assert.equal(notReadyContainer.querySelector('.cm-diff-overview'), null, '#50: live CodeMirror diff with missing chunks does not draw the raw fallback rail before the settled rebuild');
  const multiHunkDiff = [
    'diff --git a/a.txt b/a.txt',
    '--- a/a.txt',
    '+++ b/a.txt',
    '@@ -6 +6 @@',
    '-old near top',
    '+new near top',
    '@@ -50 +50 @@',
    '-old far down',
    '+new far down',
  ].join('\n');
  const multiHunkStops = changedOverviewStops(api.buildDiffOverviewGradientForTest(multiHunkDiff, 100));
  assert.deepEqual(multiHunkStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
    {color: UI_PINS.diffOverviewDelete, start: '5.000', end: '6.000'},
    {color: UI_PINS.diffOverviewAdd, start: '6.000', end: '7.000'},
    {color: UI_PINS.diffOverviewDelete, start: '50.000', end: '51.000'},
    {color: UI_PINS.diffOverviewAdd, start: '51.000', end: '52.000'},
  ], 'diff overview leaves real uncolored gaps between separate hunks');
  assert.equal(multiHunkStops.some(stop => stop.start > 7 && stop.start < 50), false, 'diff overview does not color the unchanged gap between hunks');
  assertNoOverviewStopOverlap(multiHunkStops);
  const replacementDiff = [
    'diff --git a/a.txt b/a.txt',
    '--- a/a.txt',
    '+++ b/a.txt',
    '@@ -1,3 +1,3 @@',
    '-old one',
    '-old two',
    '+new one',
    '+new two',
    ' context',
  ].join('\n');
  assert.equal(api.diffOverviewRemovedLineCountForTest(replacementDiff), 2, 'diff overview denominator includes deleted rows because they occupy editor rows');
  api.setDiffExpandUnchangedForTest(false);
  const overviewContainer = new TestElement('overview-container');
  api.updateCodeMirrorDiffOverviewForTest(null, overviewContainer, {diff: replacementDiff}, 'new one\nnew two\ncontext', 'old one\nold two\ncontext');
  assert.equal(overviewContainer.querySelector('.cm-diff-overview'), null, 'collapsed unchanged diff view omits the right overview colors entirely');
  api.setDiffExpandUnchangedForTest(true);
  api.updateCodeMirrorDiffOverviewForTest(null, overviewContainer, {diff: replacementDiff}, 'new one\nnew two\ncontext', 'old one\nold two\ncontext');
  const overview = overviewContainer.querySelector('.cm-diff-overview');
  assert.ok(overview, 'diff overview renders a rail for replacement hunks');
  assert.ok(String(overview.style.background || '').includes('linear-gradient'), 'diff overview paints the red/green rail as one gradient');
  assert.equal(overview.querySelectorAll('.cm-diff-overview-tick').length, 0, 'diff overview creates no per-chunk tick children');
  const overviewStops = changedOverviewStops(overview.style.background);
  assert.deepEqual(overviewStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
    {color: UI_PINS.diffOverviewDelete, start: '0.000', end: '40.000'},
    {color: UI_PINS.diffOverviewAdd, start: '40.000', end: '80.000'},
  ], 'diff overview replacement gradient follows rendered red rows, then rendered green rows, linearly');
  assertNoOverviewStopOverlap(overviewStops);
  const viewportContainer = new TestElement('viewport-container');
  const viewportScroller = new TestElement('viewport-scroller');
  viewportScroller.className = 'cm-scroller';
  viewportScroller.scrollTop = 50;
  viewportScroller.clientHeight = 100;
  viewportScroller.scrollHeight = 500;
  viewportContainer.appendChild(viewportScroller);
  api.updateCodeMirrorDiffOverviewForTest(null, viewportContainer, {diff: replacementDiff}, 'new one\nnew two\ncontext', 'old one\nold two\ncontext');
  const viewportOverview = viewportContainer.querySelector('.cm-diff-overview');
  const viewportIndicator = viewportOverview.querySelector('.cm-diff-overview-viewport');
  assert.ok(viewportIndicator, 'diff overview includes a viewport indicator when a scroll container is available');
  assert.equal(viewportIndicator.style.top, '10%', 'diff overview viewport indicator follows scrollTop / scrollHeight');
  assert.equal(viewportIndicator.style.height, '20%', 'diff overview viewport indicator follows clientHeight / scrollHeight');
  assert.equal(appSource.includes('splitLaneIndexes'), false, 'diff overview does not use an overlapping-lane model');
  assert.equal(appSource.includes('Math.max(0.8'), false, 'diff overview does not inflate one-line ticks in percent space, which made adjacent red/green bands overlap');
  // a diff-only toolbar toggle shows ALL context (omits collapseUnchanged) vs collapsing runs.
  assert.ok(appSource.includes('file-editor-diff-expand-panel'), 'B4: the diff toolbar has an expand/collapse-all-unchanged toggle');
  assert.ok(/function toggleDiffExpandUnchanged[\s\S]*?setDiffExpandUnchanged/.test(appSource), 'B4: the toggle flips + persists diffExpandUnchanged and re-renders');
  assert.ok(/diffExpandUnchanged \? \{\} : \{collapseUnchanged: \{margin: 3, minSize: 8\}\}/.test(appSource), 'B4: expanded omits collapseUnchanged so every unchanged line shows (both diff layouts)');
  assert.ok(/mode: 'diff'[^;]*expand: diffExpandUnchanged/.test(appSource), 'B4: the diff config signature includes expand so toggling rebuilds the diff view');
  const diffLayoutFn = appSource.slice(appSource.indexOf('function codeMirrorDiffLayout('), appSource.indexOf('function codeMirrorDiffLayout(') + 800);
  assert.ok(diffLayoutFn.includes("return 'inline';"), '#33: the diff always uses the unified (inline) layout');
  assert.equal(diffLayoutFn.includes("'side'"), false, '#33: the wide-pane side-by-side layout (which numbered deleted rows) is no longer selected, so deleted rows are unnumbered widgets at every width');
  assert.equal(api.codeMirrorDiffLayout({getBoundingClientRect: () => ({width: 2000})}), 'inline', '#33: even a very wide pane uses the unified (inline) diff, so deleted rows are never numbered');
  assert.equal(api.codeMirrorDiffLayout({getBoundingClientRect: () => ({width: 300})}), 'inline', '#33: a narrow pane also uses the unified diff');
  assert.equal(appSource.includes('{wrap: false}'), false, '#47: expanded diff honors the live Word Wrap setting instead of forcing wrap off');
  assert.equal(appSource.includes('view.lineBlockAt(doc.line(line).from)'), false, '#48: diff overview must not infer deleted-row color from CodeMirror pixel gaps');
  assert.ok(/if \(state\.diffLoading && state\._diffLoadingPromise\) return state\._diffLoadingPromise/.test(appSource), '#43: concurrent diff loads are deduped (callers await one in-flight load), so the panel never renders against an un-loaded original');
  assert.ok(/if \(!state\.diffLoaded && !state\.diffUnavailable\) \{[\s\S]{0,320}await refreshOpenFileDiff\(path, \{silent: true, renderOnComplete: false\}\);[\s\S]{0,160}if \(panel\._cmGeneration !== generation\) return null/.test(appSource), '#43/Q4: unresolved diffs await the deduped payload and continue in the same generation instead of flashing an edit view');
  const unresolvedDiffBranch = appSource.slice(appSource.indexOf('async function ensureCodeMirrorDiffPanel('), appSource.indexOf('if (!openFileDiffAvailable(state))', appSource.indexOf('async function ensureCodeMirrorDiffPanel(')));
  assert.equal(unresolvedDiffBranch.includes("forceMode: 'edit'"), false, 'unresolved diff payloads must not bail to a temporary edit-mode CodeMirror view');
  assert.ok(/CodeMirror diff language parser failed; retrying plain diff editor/.test(appSource), 'diff CodeMirror build has the same parser-failure plain retry safety net as edit mode');
  const diffPanelBody = appSource.slice(appSource.indexOf('async function ensureCodeMirrorDiffPanel('), appSource.indexOf('async function ensureCodeMirrorPanel(', appSource.indexOf('async function ensureCodeMirrorDiffPanel(')));
  assert.ok(/catch \(error\) \{[\s\S]{0,360}CodeMirror diff editor unavailable; showing read-only raw text[\s\S]{0,260}container\.hidden = true;[\s\S]{0,260}return false;/.test(diffPanelBody), 'diff CodeMirror build failures fall back to raw text instead of leaving an emptied blank pane');
  assert.ok(/ensureCodeMirrorPanel\(panel, item, path, state\)\.then\(loaded => \{[\s\S]{0,160}renderFileEditorRawPane\(rawPane, path, state\.content\);[\s\S]{0,160}\}\)\.catch\(error => \{[\s\S]{0,360}renderFileEditorRawPane\(rawPane, path, state\.content\);/.test(appSource), 'CodeMirror render promise rejections are caught and fall back to raw text');
  assert.equal(appSource.includes("fileEditorEmptyState('No diff'"), false, 'clean selected refs render the normal editor instead of a No diff empty state');
  assert.ok(/if \(!openFileDiffAvailable\(state\)\)[\s\S]{0,360}return ensureCodeMirrorPanel\(panel, item, path, state, \{forceMode: 'edit'\}\)/.test(appSource), 'clean selected refs fall back to the normal editable CodeMirror view');
  assert.ok(/function diffModeShouldFallBackToEdit[\s\S]*!state\.diffLoading[\s\S]*!openFileDiffAvailable\(state\)[\s\S]*!fileStateHasUsefulGitHistory\(state\)/.test(appSource), 'once a diff load confirms no usable diff, diff mode exits to edit ONLY when the file has no useful history — a file WITH history stays in diff mode so the FROM/TO ref picker is reachable');
  const refreshDiffStart = appSource.indexOf('async function refreshOpenFileDiff(');
  const openFileEditorStart = appSource.indexOf('async function openFileInEditor(', refreshDiffStart);
  assert.ok(refreshDiffStart > 0 && openFileEditorStart > refreshDiffStart, '#43: refreshOpenFileDiff body is locatable');
  const refreshDiffBody = appSource.slice(refreshDiffStart, openFileEditorStart);
  const diffLoadingClearIndex = refreshDiffBody.indexOf('state.diffLoading = false;');
  const diffPanelRenderIndex = refreshDiffBody.indexOf('renderFileEditorPanel(panel, item);');
  assert.ok(diffLoadingClearIndex >= 0 && diffPanelRenderIndex > diffLoadingClearIndex, '#43: diff-load completion clears diffLoading before repainting the panel, so the expanded-context toolbar button is not left disabled');
  assert.ok(refreshDiffBody.includes("options.renderOnComplete !== false && editorViewModeFor(path, item) === 'diff'"), 'awaited diff builders can suppress the completion re-render that otherwise supersedes their generation');
  assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, untracked: true, diff: 'diff --git a/a b/a\n--- /dev/null\n+++ b/a\n@@\n+x'}), false, '#43: an untracked/all-added file reports no diff, so it never enters diff view');
  assert.ok(/function openDraggedFilesInEditor[\s\S]*await refreshOpenFileDiff\(path[\s\S]*openFileDiffAvailable\(draggedState\)[\s\S]*setFileEditorViewMode\(path, 'diff'/.test(appSource), '#39: a dragged CHANGED file opens in the same unified diff view as double-click (routes through the shared refreshOpenFileDiff/diff path)');
  assert.ok(appSource.includes('data-file-explorer-new-folder'), 'Finder header exposes new-folder action');
  const focusPanelBody = appSource.slice(appSource.indexOf('function setFocusedPanelItem('), appSource.indexOf('let autoFocusNavTimer'));
  assert.equal(/switchFileExplorerChangesSession/.test(focusPanelBody), false, 'passive focus/hover no longer switches the Finder Modified-files session');
  assert.equal(appSource.includes('sessionFilesTargetSession({followActive: true})'), false, 'Finder Modified-files session selection never follows passive hover/autofocus');
  assert.ok(/function noteFileExplorerChangesSessionInteraction\(session\)/.test(appSource), 'explicit session interactions can commit the Finder Modified-files target');
  assert.ok(/function activatePaneTab\([^]*?noteFileExplorerChangesSessionInteraction\(session\)/.test(appSource), 'clicking a tmux pane tab commits the Finder Modified-files target');
  assert.ok(/function activatePaneTab\([^]*?isFileEditorItem\(session\)[^]*?changedFileOwnerSessionForPath\(path, \{owners\}\)[^]*?owners\.length === 1[^]*?noteFileExplorerChangesSessionInteraction\(owner\)/.test(appSource), 'clicking a file tab commits an exact-path Differ owner, falling back to a single owner only');
  const exactOwnerApi = loadYolomux('', ['1', '2', '5']);
  const exactPath = '/home/test/frontend-crates/tests/parity/toolcalling/table.py';
  const similarPath = '/home/test/frontend-crates/conformance/utils/tests/parity/toolcalling/table.py';
  const exactItem = exactOwnerApi.fileEditorItemFor(exactPath);
  exactOwnerApi.setFileExplorerSessionFilesPayloadForTest({session: '5', loaded: true, files: [], repos: [], errors: []});
  exactOwnerApi.setSessionFilesCachePayloadForTest('1', {loaded: true, files: [{abs_path: similarPath, status: 'M'}], repos: [], errors: []});
  exactOwnerApi.setSessionFilesCachePayloadForTest('2', {loaded: true, files: [{abs_path: exactPath, status: 'M'}], repos: [], errors: []});
  exactOwnerApi.setOpenFileOwner(exactPath, exactItem, {ownerSession: '1'});
  exactOwnerApi.setOpenFileOwner(exactPath, exactItem, {ownerSession: '2'});
  const exactOwnerSlots = exactOwnerApi.emptyLayoutSlots();
  exactOwnerSlots[exactOwnerApi.layoutTreeKey] = exactOwnerApi.leafNode('left');
  exactOwnerSlots.left = exactOwnerApi.paneStateWithTabs([exactItem, '5'], '5');
  exactOwnerApi.setLayoutSlotsForTest(exactOwnerSlots);
  exactOwnerApi.activatePaneTab('left', exactItem, {userInitiated: true});
  assert.equal(exactOwnerApi.fileExplorerSessionFilesTargetSessionForTest(), '2', 'file-tab click chooses the one session with the exact changed absolute path');
  exactOwnerApi.setSessionFilesCachePayloadForTest('1', {loaded: true, files: [{abs_path: exactPath, status: 'M'}], repos: [], errors: []});
  assert.equal(exactOwnerApi.changedFileOwnerSessionForPathForTest(exactPath, {owners: ['1', '2']}), '', 'same absolute file changed by multiple sessions remains ambiguous');
  const terminalInputStart = appSource.indexOf('function startTerminal(');
  const terminalInputEnd = appSource.indexOf('function updateTypingIndicator(', terminalInputStart);
  const terminalInputBody = appSource.slice(terminalInputStart, terminalInputEnd);
  assert.ok(terminalInputBody.includes("container.addEventListener('keydown', () => noteTerminalExplicitInput(session), {capture: true});"), 'terminal keydown commits the Finder Modified-files target');
  assert.ok(terminalInputBody.includes("container.addEventListener('paste', () => noteTerminalExplicitInput(session), {capture: true});"), 'terminal paste commits the Finder Modified-files target');
  assert.equal(/term\.onData\(data => \{[^]*?noteFileExplorerChangesSessionInteraction\(session\)/.test(terminalInputBody), false, 'xterm data transport does not commit Finder because hover focus can emit focus/mouse reports');
  assert.ok(/fetchSessionFiles\(\{destination: 'finder', session, silent: true, force: true\}\)/.test(appSource), 'explicit session changes force a fresh Finder modified-files fetch even if an older request is in flight');
  assert.ok(/function sessionFilesCacheKey\(session\)[\s\S]*sessionFilesRequestQueryString\(\)/.test(appSource), 'Differ cached payloads are keyed by session plus effective FROM/TO/refs query');
  assert.ok(/const cached = fileExplorerSessionFilesCache\.get\(sessionFilesCacheKey\(session\)\)/.test(appSource), 'Differ session switches do not reuse payloads from a different ref pair');
  assert.ok(/fileExplorerSessionFilesCache\.set\(sessionFilesCacheKey\(session\), \{payload: nextPayload, signature\}\)/.test(appSource), 'Differ stores cached payloads under the same ref-aware key it reads');
  assert.ok(/function sessionFilesPayloadIsFinderWorktree\([\s\S]*from_ref \|\| 'HEAD'[\s\S]*to_ref \|\| 'current'/.test(appSource), 'Finder file mode can preserve an already-loaded HEAD/current payload for sync planning');
  assert.ok(/fileExplorerMode !== 'diff' && sessionFilesPayloadIsFinderWorktree\(fileExplorerSessionFilesPayload, session\)/.test(appSource), 'Finder file mode does not blank the current worktree payload when committing a session');
  assert.ok(/function sessionFilesRequestQueryString\(\)[\s\S]*fileExplorerMode !== 'diff'[\s\S]*from=HEAD&to=current[\s\S]*diffRefQueryString\(\)\}\$\{sessionFilesRefsQuery\(\)\}/.test(appSource), 'Finder file mode requests only current worktree status while Differ follows selected refs');
  assert.ok(/function setFileExplorerMode\([\s\S]*fetchSessionFiles\(\{destination: 'finder', session: fileExplorerSessionFilesTargetSession\(\), silent: true, force: true\}\)/.test(appSource), 'switching back from Differ to Finder forces a fresh worktree-status fetch');
  assert.ok(/refreshFileExplorerTrees\(\);\s*fetchSessionFiles\(\{destination: 'finder', session: fileExplorerSessionFilesTargetSession\(\), silent: true, force: true\}\)/.test(appSource), 'Finder Reload refreshes the modified-file overlay as well as the directory tree');
  assert.equal(appSource.includes("state.kind === 'text' && !fileEditorAutosaveEnabled"), false, 'clean external file changes auto-reload even when autosave is off');
  assert.equal(appSource.includes('data-file-editor-close'), false, 'pane frame close uses the pane-close path, not active file-tab close');
  assert.equal(filesTab.includes('agent-icon file'), false);
  assert.ok(api.menuTabCommand('__files__').html.includes('app-menu-ui-icon-finder'));
  assert.ok(api.menuTabCommand('__prefs__').html.includes('app-menu-ui-icon-gear'));
  assert.ok(api.menuTabCommand('__info__').html.includes('app-menu-ui-icon-branch-info'));
  // #40: a legacy __yoagent__ reference resolves to the merged YO!info item (so it shows the info icon).
  assert.ok(api.menuTabCommand('__yoagent__').html.includes('app-menu-ui-icon-branch-info'));
  assert.equal(api.menuTabCommand('__changes__').html.includes('app-menu-ui-icon-changes'), false, 'retired standalone Differ no longer has a menu/tab icon');
  assert.ok(api.menuTabCommand('file:/home/test/README.md').html.includes('app-menu-ui-icon-document'));
  assert.equal(api.platformWindowControlClass('minimize'), 'pc-window-control pc-minimize');
  assert.equal(api.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(api.platformWindowControlClass('zoom'), 'pc-window-control pc-zoom');
  assert.equal(api.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(api.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');
  const pcSingleSlots = api.emptyLayoutSlots();
  pcSingleSlots[api.layoutTreeKey] = api.leafNode('left');
  pcSingleSlots.left = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(pcSingleSlots);
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
  const macFinderFields = macApi.tabSearchFields(macApi.fileExplorerItemId);
  assert.ok(macFinderFields.includes('Finder'), 'Finder tab indexes its visible macOS name');
  assert.ok(macFinderFields.includes('File Explorer'), 'Finder tab also indexes the File Explorer alias');
  assert.equal(macApi.commandPaletteMatches({group: 'Tabs', label: 'Finder', detail: '', searchFields: macFinderFields}, 'File Explorer'), true, 'typing File Explorer finds the Finder command palette row');

  const forcedPcApi = loadYolomux('?platform=pc', ['1'], 'http:', 'MacIntel');
  assert.equal(forcedPcApi.fileExplorerLabel(), 'File Explorer');
  assert.ok(forcedPcApi.tabSearchFields(forcedPcApi.fileExplorerItemId).includes('Finder'), 'File Explorer tab also indexes the Finder alias');
  assert.equal(forcedPcApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(forcedPcApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(forcedPcApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');

  const forcedMacApi = loadYolomux('?platform=mac', ['1'], 'http:', 'Linux x86_64');
  assert.equal(forcedMacApi.fileExplorerLabel(), 'Finder');
  assert.equal(forcedMacApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
  assert.equal(forcedMacApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
  assert.equal(forcedMacApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');

  const watchApi = loadYolomux('', ['1']);
  const visiblePath = '/repo/README.md';
  const backgroundPath = '/repo/NOTES.md';
  const visibleItem = watchApi.registerFileEditorLayoutItem(visiblePath);
  const backgroundItem = watchApi.registerFileEditorLayoutItem(backgroundPath);
  const watchSlots = watchApi.emptyLayoutSlots();
  watchSlots[watchApi.layoutTreeKey] = watchApi.splitNode('row', watchApi.leafNode('left'), watchApi.leafNode('right'));
  watchSlots.left = watchApi.paneStateWithTabs([backgroundItem, visibleItem], visibleItem);
  watchSlots.right = watchApi.paneStateWithTabs(['1'], '1');
  watchApi.setLayoutSlotsForTest(watchSlots);
  assert.deepStrictEqual([...watchApi.visibleFileEditorWatchFilesForTest()], [visiblePath], 'active visible editor files use the fast watch list');
  assert.deepStrictEqual([...watchApi.backgroundFileEditorWatchFilesForTest()], [backgroundPath], 'background editor tabs use the slower watch list');
  assert.deepStrictEqual([...watchApi.clientServerWatchStateForTest().files], [visiblePath], 'watch payload sends active editor files under files');
  assert.deepStrictEqual([...watchApi.clientServerWatchStateForTest().background_files], [backgroundPath], 'watch payload sends background editor files separately');
  watchApi.activatePaneTab('left', backgroundItem, {userInitiated: true});
  assert.deepStrictEqual([...watchApi.visibleFileEditorWatchFilesForTest()], [backgroundPath], 'activating a background editor promotes it to the fast watch list');
  assert.deepStrictEqual([...watchApi.backgroundFileEditorWatchFilesForTest()], [visiblePath], 'the previously visible editor moves to the slower watch list');

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

  const fileEditorItem = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/README.md');
  const fileEditorSlots = api.emptyLayoutSlots();
  fileEditorSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 20);
  fileEditorSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  fileEditorSlots.slot1 = api.paneStateWithTabs([fileEditorItem], fileEditorItem);
  fileEditorSlots.slot2 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(fileEditorSlots);
  api.setLayoutColumnRectsForTest({
    left: {left: 0, right: 220, top: 0, bottom: 800, width: 220, height: 800},
    slot1: {left: 230, right: 580, top: 0, bottom: 800, width: 350, height: 800},
    slot2: {left: 590, right: 1190, top: 0, bottom: 800, width: 600, height: 800},
  });
  assert.equal(api.largestPaneSlotForFileEditor(['slot1']), 'slot2', 'file editor helpers choose the next biggest existing non-Finder pane');

  const delayHtml = api.preferencesPanelHtmlForTest('delay', ['Performance']);
  assert.ok(delayHtml.includes('data-preference-section="Performance"'), 'delay search shows Performance');
  assert.equal(/data-preference-section="Performance"[\s\S]*preferences-settings" hidden/.test(delayHtml), false, 'search expands matching collapsed sections');
  assert.ok(delayHtml.includes('Server SSE: editor file-change poll'), 'delay search surfaces server SSE timing settings');
  const preferencesCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(preferencesCss.startsWith('/* GENERATED by tools/static_build.py from static_src/'), 'generated CSS has a do-not-edit header');
  assert.ok(/\.preferences-section-toggle\s*\{[\s\S]*color:\s*var\(--pane-tab-text\)[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Preferences section headers use the same background token as the pane tab container');
  assert.ok(/\.preferences-search-button\s*\{[\s\S]*font:\s*700 var\(--ui-font-size-sm\)\/1\.1 var\(--ui-font\)/.test(preferencesCss), 'YOsearch uses the normal UI font, not condensed tab text');
  assert.ok(preferencesCss.includes('--file-explorer-changes-min-block-size: 96px'), 'modified-files resizer shares a stable min-size token');
  assert.ok(preferencesCss.includes('--drop-outline: #ffffff'), '#40: dark-mode drag preview/outline is white (light mode stays blue, asserted below)');
  assert.ok(preferencesCss.includes('--text-selection-bg: #2563eb'), 'dark mode browser text selection uses a prominent blue fill');
  assert.ok(/body\.theme-light\s*\{[\s\S]*?--text-selection-bg:\s*#93c5fd/.test(preferencesCss), 'light mode browser text selection uses a visible blue fill');
  assert.ok(/::selection\s*\{(?=[^}]*background:\s*var\(--text-selection-bg\))(?![^}]*color:)[^}]*\}/.test(preferencesCss), 'browser text selection paints background only, preserving text color');
  assert.ok(/::-moz-selection\s*\{(?=[^}]*background:\s*var\(--text-selection-bg\))(?![^}]*color:)[^}]*\}/.test(preferencesCss), 'Firefox text selection paints background only, preserving text color');
  assert.equal(preferencesCss.includes('--text-selection-text'), false, 'global selection no longer defines selected-text color');
  assert.ok(/\.file-tree-repo-meta\s*\{[^}]*font-size: var\(--ui-font-size-2xs\)/.test(preferencesCss), '#37: the Finder repo/branch label is condensed to a smaller font so more files fit');
  assert.ok(/\.file-explorer-hidden-toggle,\s*\n\.file-explorer-root-mode-toggle,\s*\n\.file-explorer-header-action\s*\{[\s\S]*white-space:\s*nowrap/.test(preferencesCss), 'Finder toolbar text buttons never wrap localized labels vertically');
  assert.ok(/\.file-explorer-root-mode-toggle\s*\{[\s\S]*?width:\s*auto[\s\S]*?min-width:\s*38px[\s\S]*?flex:\s*0 0 auto/.test(preferencesCss), 'Finder root-mode button sizes to localized label content');
  assert.ok(/\.file-explorer-root-mode-toggle-panel\s*\{[\s\S]*?width:\s*auto[\s\S]*?min-width:\s*38px[\s\S]*?flex:\s*0 0 auto/.test(preferencesCss), 'Finder panel root-mode button sizes to localized label content');
  assert.ok(/\.file-explorer-toolbar\s*\{[\s\S]*flex-direction:\s*column[\s\S]*justify-content:\s*flex-start/.test(preferencesCss), 'Finder toolbar is a deliberate stacked-row column instead of one wrapping row');
  assert.ok(/\.file-explorer-toolbar-row\s*\{[\s\S]*inline-size:\s*100%/.test(preferencesCss), 'Finder toolbar rows span the pane width');
  assert.ok(/\.file-explorer-toolbar-spacer\s*\{[\s\S]*flex:\s*1 1 auto/.test(preferencesCss), 'Finder toolbar uses explicit spacers to pin right-side controls');
  assert.ok(/\.file-explorer-actions-row \.file-explorer-date-reload-cluster\s*\{[\s\S]*display:\s*inline-flex/.test(preferencesCss), 'Finder row 3 keeps date, tree expand/collapse, and reload grouped');
  assert.ok(/\.file-explorer-date-toggle\[data-date-mode="none"\]\s*\{[\s\S]*text-decoration-line:\s*line-through/.test(preferencesCss), 'Finder/Differ None date mode renders as crossed-out Date');
  assert.ok(/\.file-explorer-primary-row \.file-explorer-path-inline\s*\{[\s\S]*flex:\s*1 1 0[\s\S]*min-width:\s*0[\s\S]*min-inline-size:\s*0/.test(preferencesCss), 'Finder path fills the primary row and can shrink without wrapping controls');
  assert.ok(/body:not\(\.file-explorer-mode-diff\) \.file-explorer-primary-row \.file-explorer-toolbar-spacer\s*\{[\s\S]*flex:\s*0 0 0/.test(preferencesCss), 'Finder files mode lets the path input consume the space before Copy and close');
  assert.ok(/\.file-explorer-mode-switcher\s*\{[\s\S]*display:\s*inline-flex/.test(preferencesCss), 'Finder/Differ mode switcher is a segmented inline control');
  assert.ok(/\.file-explorer-mode-toggle\s*\{[\s\S]*height:\s*22px[\s\S]*padding:\s*0 5px[\s\S]*font-family:\s*var\(--control-font\)[\s\S]*font-stretch:\s*condensed/.test(preferencesCss), 'Finder/Differ mode buttons use compact horizontal condensed button sizing');
  assert.equal(/\.file-explorer-mode-label\s*\{[\s\S]*writing-mode:\s*vertical-rl/.test(preferencesCss), false, 'Finder/Differ mode labels are regular left-to-right text');
  assert.ok(/\.file-explorer-mode-toggle\s*\{[\s\S]*background:\s*color-mix\(in srgb,\s*var\(--active-control-bg\)/.test(preferencesCss), 'Finder/Differ mode buttons use shared active-control accent styling');
  assert.ok(/\.file-explorer-mode-toggle\[aria-pressed="true"\]\s*\{[\s\S]*background:\s*var\(--active-control-bg\)/.test(preferencesCss), 'Finder/Differ mode button is filled from the active-control token when pressed');
  assert.ok(/\.file-explorer-folder-icon\s*\{[\s\S]*border:\s*1\.5px solid currentColor[\s\S]*\.file-explorer-folder-icon::before/.test(preferencesCss), 'Finder new-folder button renders a folder icon instead of a square glyph');
  assert.ok(/\.file-explorer-path,[\s\S]*?\.file-explorer-path-inline\s*\{[\s\S]*color:\s*var\(--text\)[\s\S]*border:\s*1px solid var\(--line\)/.test(preferencesCss), 'Finder path uses normal text contrast and visible input chrome');
  const finderPanelBundle = fs.readFileSync('static/yolomux.js', 'utf8');
  const finderPanelStart = finderPanelBundle.indexOf('function createFileExplorerPanel');
  const finderPanelSource = finderPanelBundle.slice(
    finderPanelStart,
    finderPanelBundle.indexOf('function bindFileExplorerPanel', finderPanelStart),
  );
  assert.ok(/file-explorer-toolbar-row file-explorer-primary-row[\s\S]*file-explorer-toolbar-row file-explorer-scope-row[\s\S]*file-explorer-toolbar-row file-explorer-actions-row file-explorer-mode-files-only/.test(finderPanelSource), 'Finder panel toolbar renders primary, scope, and files-only actions rows in order');
  assert.equal(finderPanelSource.includes('file-explorer-diff-row'), false, 'Differ title is folded into the shared primary row');
  assert.equal(finderPanelSource.includes('file-explorer-panel-title'), false, 'Finder panel no longer prints redundant Finder/Differ title text');
  assert.ok(/file-explorer-toolbar-row file-explorer-primary-row[\s\S]*fileExplorerModeSwitcherHtml\(\)[\s\S]*fileExplorerDiffSessionControlHtml\(fileExplorerSessionFilesTargetSession\(\)\)[\s\S]*<input class="file-explorer-path-inline file-explorer-mode-files-only"[\s\S]*file-explorer-path-copy-panel[\s\S]*fileExplorerChangesCollapseToggleHtml\(\)[\s\S]*file-explorer-frame-controls/.test(finderPanelSource), 'Finder panel primary row renders mode switcher, diff-mode Session select, path, copy, Differ collapse toggle, and close control');
  assert.ok(/function fileExplorerDiffSessionControlHtml[\s\S]*file-explorer-diff-session-control file-explorer-mode-diff-only changes-control[\s\S]*changes\.session[\s\S]*sessionFilesSessionSelectHtml\(session/.test(finderPanelBundle), 'Differ mode keeps the Session dropdown in the top Finder/Differ row');
  assert.ok(/file-explorer-toolbar-row file-explorer-scope-row[\s\S]*file-explorer-hidden-toggle file-explorer-hidden-toggle-panel[\s\S]*file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel[\s\S]*file-explorer-quick-access-panel/.test(finderPanelSource), 'Finder scope row renders .*, Sync, then quick-root buttons');
  assert.ok(finderPanelBundle.includes("t('finder.toolbar.syncTitle')"), 'Finder Sync button has a dedicated tooltip/aria label string');
  assert.ok(finderPanelSource.includes('title="${esc(t(\'finder.toolbar.syncTitle\'))}"') && finderPanelSource.includes('${esc(t(\'finder.toolbar.syncLabel\'))}</button>'), 'Finder Sync panel button uses the full tooltip while keeping the compact visible label');
  assert.equal(api.displayQuickAccessPath('/'), '/*', 'Finder root quick-access button labels root as /*');
  assert.equal(api.displayQuickAccessPath('/*'), '/*', 'Finder accepts /* as the root quick-access label');
  assert.equal(api.expandQuickAccessPath('/'), '/', 'Finder / quick-access opens the root directory');
  assert.equal(api.expandQuickAccessPath('/*'), '/', 'Finder /* quick-access opens the root directory, not a literal glob path');
  assert.equal(api.displayQuickAccessPath('/tmp'), '/tmp', 'Finder quick-access labels absolute paths such as /tmp with their leading slash');
  assert.ok(/const modes = \[[\s\S]*mode: 'files'[\s\S]*mode: 'diff'[\s\S]*data-file-explorer-mode-set="\$\{esc\(item\.mode\)\}"/.test(finderPanelBundle), 'Finder/Differ switcher renders both mode buttons from one segmented source');
  assert.ok(finderPanelSource.includes("fileExplorerTreeDateButtonHtml('changes-date-toggle')"), 'Finder panel toolbar uses the shared date-mode button helper with the Differ sizing class');
  assert.ok(/file-explorer-sort-select[\s\S]*file-explorer-date-reload-cluster[\s\S]*fileExplorerTreeDateButtonHtml\('changes-date-toggle'\)[\s\S]*fileTreeExpandCollapseAllButtonsHtml\('changes-date-toggle'\)[\s\S]*data-file-explorer-refresh[\s\S]*changes\.refresh/.test(finderPanelSource), 'Finder date-mode button, Expand all, Collapse all, and Reload form a trailing cluster in the files-only action row');
  assert.equal(finderPanelSource.includes('file-explorer-repo-summary'), false, 'Finder files-only action row no longer prints repo/path text between sort and date display');
  const finderActionsRowStart = finderPanelSource.indexOf('file-explorer-toolbar-row file-explorer-actions-row');
  const finderActionsRowSource = finderPanelSource.slice(finderActionsRowStart, finderPanelSource.indexOf('</div>', finderActionsRowStart));
  assert.ok(/data-file-explorer-new-file[\s\S]*data-file-explorer-new-folder[\s\S]*file-explorer-folder-icon/.test(finderActionsRowSource), 'Finder files-only action row renders new file, then a folder-icon new-folder button');
  assert.equal(finderActionsRowSource.includes('data-file-explorer-collapse'), false, 'Finder files-only action row no longer renders the old standalone left-side collapse button');
  const treeExpandCollapseButtons = api.fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle');
  assert.ok(/data-file-tree-expand-collapse-all="expand"[\s\S]*data-file-tree-expand-collapse-all="collapse"/.test(treeExpandCollapseButtons), 'shared tree expand/collapse helper renders Expand all before Collapse all');
  assert.ok(/data-file-tree-expand-collapse-all="expand"[\s\S]*file-tree-expand-collapse-icon[\s\S]*data-file-tree-expand-collapse-all="collapse"[\s\S]*file-tree-expand-collapse-icon/.test(treeExpandCollapseButtons), 'shared tree expand/collapse helper renders toolbar SVG icons');
  assert.equal(treeExpandCollapseButtons.includes('▦') || treeExpandCollapseButtons.includes('▤'), false, 'shared tree expand/collapse helper does not use square text glyphs');
  assert.ok(/\.file-tree-expand-collapse-all\s*\{[\s\S]*box-sizing:\s*border-box[\s\S]*width:\s*16px[\s\S]*max-width:\s*16px[\s\S]*flex:\s*0 0 16px[\s\S]*padding:\s*0/.test(preferencesCss), 'shared tree expand/collapse toolbar icon buttons stay narrow');
  assert.ok(/\.changes-toolbar \.file-tree-expand-collapse-all\.changes-date-toggle,[\s\S]*\.file-explorer-date-reload-cluster \.file-tree-expand-collapse-all\.changes-date-toggle\s*\{[\s\S]*max-inline-size:\s*16px[\s\S]*height:\s*20px/.test(preferencesCss), 'Differ/Finder toolbar context cannot widen tree expand/collapse icon buttons');
  assert.equal(finderActionsRowSource.includes('data-file-explorer-mode-set'), false, 'Finder files-only action row no longer repeats the mode switcher');
  assert.equal(/data-file-explorer-refresh[\s\S]*file-explorer-collapse/.test(finderPanelSource), false, 'Finder no longer has a standalone left-side refresh button before collapse');
  assert.equal(preferencesCss.includes('--file-explorer-changes-size: 40%'), false, 'Finder diff mode no longer keeps the old 40% stacked Modified-files section cap');
  assert.ok(/body\.file-explorer-mode-files \.file-explorer-changes-panel/.test(preferencesCss), 'files mode hides the Finder changes panel and resizer');
  assert.ok(/body\.file-explorer-mode-diff \.file-explorer-tree-panel/.test(preferencesCss), 'diff mode hides the Finder tree panel');
  assert.ok(/body\.file-explorer-mode-diff \.file-explorer-changes-panel[\s\S]*?\{[\s\S]*flex:\s*1 1 auto[\s\S]*max-block-size:\s*none/.test(preferencesCss), 'diff mode lets the Finder changes panel fill the pane');
  assert.ok(/body\.file-explorer-mode-tabber \.file-explorer-changes-panel/.test(preferencesCss), 'DOIT.58 B1: tabber mode fills the pane like diff (tree hidden, changes panel full)');
  assert.ok(/body\.file-explorer-mode-tabber \.file-explorer-tree-panel/.test(preferencesCss), 'DOIT.58 B1: tabber mode hides the Finder tree panel');
  assert.ok(/\.file-explorer-changes-panel \.changes-comparison-head\s*\{[^}]*flex-wrap: nowrap/.test(preferencesCss), '#44(d): the Finder comparison header is compacted to one tight line (header chrome takes less height)');
  assert.ok(/\.grid\.drop-preview::before/.test(preferencesCss), 'root layout drops have a full-layout preview overlay');
  assert.ok(/\.grid\.drop-preview-gutter::before\s*\{[\s\S]*--drop-preview-left/.test(preferencesCss), 'split-bar drops use explicit full-span preview geometry');
  // C15: the Finder↔Modified-files resizer reuses the shared --pane-resizer-* tokens (thin yellow line at
  // rest, hover brightens) instead of its old special-cased hardcoded-green strip.
  const resizerStart = preferencesCss.indexOf('.file-explorer-changes-resizer {');
  const resizerBeforeStart = preferencesCss.indexOf('.file-explorer-changes-resizer::before', resizerStart);
  const resizerEnd = preferencesCss.indexOf('.file-explorer-changes-panel {', resizerBeforeStart);
  const resizerBlock = preferencesCss.slice(resizerStart, resizerEnd);
  assert.ok(resizerBlock.includes('var(--pane-resizer-bg)'), 'C15: the Finder resizer draws the shared thin yellow line at rest');
  assert.ok(resizerBlock.includes('var(--pane-resizer-hover-bg)'), 'C15: the Finder resizer brightens via the shared hover token');
  assert.ok(resizerBlock.includes('var(--drop-outline-shadow)'), 'C15: the Finder resizer hover uses the shared outline-shadow token');
  assert.equal(/118,\s*185,\s*0|255,\s*255,\s*255,\s*0\.04/.test(resizerBlock), false, 'C15: the hardcoded green gradient + white border are gone from the Finder resizer');
  // Light theme drives the BASE pane-tab/ring styling via tokens, not rule overrides; but #28
  // adds targeted hover/link contrast overrides (expected), so guard only the base tab + ring.
  assert.equal(/body\.theme-light\s+\.pane-tab\s*\{/.test(preferencesCss), false, 'light theme does not restyle the base pane tab (tokens drive it)');
  assert.equal(/body\.theme-light\s+\.panel\.active-pane\s*\{/.test(preferencesCss), false, 'light theme does not restyle the active-pane ring directly');
  assert.ok(/body\.theme-light \.meta a/.test(preferencesCss), '#28: light theme adds a contrast override for detail-row links');
  assert.ok(/body\.theme-light \.pane-tab:hover/.test(preferencesCss), '#28: light theme fixes the near-white pane-tab hover border');
  assert.ok(/body\.theme-light \.tabs \.pane-actions:hover/.test(preferencesCss), '#28: light theme fixes the white tab-overflow hover glyph');
  assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-bright:\s*#4f9e3a/.test(preferencesCss), '#31: the active pane tab has a light-mode green (via the active-accent token) so a theme switch repaints it');
  assert.ok(/body\.theme-light \.panel\.active-pane \.panel-detail-row \.session-button-name/.test(preferencesCss), '#35: the active-pane detail-row header label is forced dark in light mode (was light-on-light)');
  assert.ok(fs.readFileSync('static/yolomux.js', 'utf8').includes('session-button-dir pane-tab-info-label'), '#27: the YO!info tab label uses the themed .session-button-dir color treatment');
  assert.ok(preferencesCss.includes('--active-accent-bright: #86d600'), 'focused active pane tab uses a brighter brand green fill (via the active-accent token)');
  assert.ok(preferencesCss.includes('--pane-tab-active-accent: var(--active-accent-bright)'), 'focused active pane tab accent token derives from the shared active-accent (same green as the fill)');
  assert.equal(preferencesCss.includes('box-shadow: inset 0 2px 0 var(--pane-tab-active-accent)'), false, 'focused active pane tabs do not paint a contrasting top line');
  assert.ok(preferencesCss.includes('--pane-tab-width: 180px'), 'pane tabs default to the compact 180px width');
  assert.ok(changedFilesSource.includes("numberSetting('appearance.tab_width', 180)"), 'runtime settings fallback keeps the 180px tab width default');
  assert.ok(preferencesCss.includes('--active-accent-dim: color-mix(in srgb, var(--active-accent) 26%, var(--panel))'), 'dark/root theme uses the brighter shared pane tab-strip background (active-accent-dim)');
  assert.equal(/body\.theme-dark\s*\{[^}]*--pane-tab-strip-bg\s*:/.test(preferencesCss), false, 'dark theme inherits the shared pane tab-strip token instead of restating a separate color');
  assert.equal(preferencesCss.includes('--pane-tab-strip-hover-bg:'), false, 'dark theme no longer defines a separate pane tab-container hover token');
  const themeLightTokenBlock = preferencesCss.match(/body\.theme-light\s*\{[^}]*\}/)?.[0] || '';
  assert.equal(themeLightTokenBlock.includes('--pane-tab-strip-hover-bg'), false, 'light theme does not define the dark-only pane tab-container hover token');
  assert.ok(/body\.theme-light\s*\{[\s\S]*--active-accent-dim:\s*#e1edda/.test(preferencesCss), 'light theme uses a greenish-light pane tab-strip background (active-accent-dim)');
  assert.ok(/--pane-tab-unfocused-active-bg:\s*var\(--pane-tab-active-bg\)/.test(preferencesCss), 'unfocused active tabs use the SAME full green as the focused active tab (+ images 003/004: undimmed, un-lightened per-pane highlight)');
  assert.equal(preferencesCss.includes('--pane-tab-unfocused-active-bg: #aeb7c4'), false, 'gray unfocused-active pane tabs must not return');
  assert.ok(preferencesCss.includes('--pane-tab-panel-ring-width: 4px'), 'the pane ring uses the 4px width token');
  assert.ok(preferencesCss.includes('--pane-ring-opacity: 75%'), 'the pane ring default opacity is prominent enough in both themes');
  assert.ok(preferencesCss.includes('--pane-active-ring-opacity: 75%'), 'the active pane ring shares the default ring opacity token');
  // Light mode uses a BLUE pane separator (dark mode keeps amber/yellow).
  assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-resizer-bg:\s*rgba\(37, 99, 235/.test(preferencesCss), 'light mode uses a blue pane separator');
  // Pane chrome bars (strip, detail row, editor toolbar, find) all read the shared --pane-bar-bg, which is
  // the bright tab-strip green when the pane is focused and neutral gray when not. Focus sets it on .panel.
  assert.ok(/\.panel\.active-pane,\s*\.panel\.typing-ready-pane\s*\{[^}]*--pane-bar-bg:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'focused panes set --pane-bar-bg to the bright tab-strip green');
  assert.equal(/\.panel\.active-pane > \.panel-head,\s*\.panel\.typing-ready-pane > \.panel-head/.test(preferencesCss), false, 'focused pane tab containers use the shared .panel-head background rule, not a separate state override');
  assert.equal(/\.panel\.changes-panel/.test(preferencesCss), false, 'standalone Changes pane chrome CSS is removed');
  assert.ok(/\.panel\.file-explorer-panel > \.file-explorer-head:hover,\s*\.panel\.file-explorer-panel > \.file-explorer-head:focus-within,\s*\.panel\.file-explorer-panel:has\(\.file-explorer-tree-panel:hover\) > \.file-explorer-head,\s*\.panel\.file-explorer-panel:has\(\.file-explorer-tree-panel:focus-within\) > \.file-explorer-head\s*\{[^}]*--pane-bar-bg:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'Finder hover/focus colors only the Finder header');
  assert.ok(/\.panel-head\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'the tab strip reads the shared --pane-bar-bg');
  assert.ok(/\.panel-detail-row\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'the info/detail bar reads the shared --pane-bar-bg (gray when unfocused, not green)');
  assert.ok(/\.file-explorer-head\s*\{[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Finder header reads the shared pane bar background when focused/hovered');
  assert.ok(/\.file-explorer-changes-panel:hover,\s*\.file-explorer-changes-panel:focus-within\s*\{[^}]*--pane-bar-bg:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'embedded Finder Modified-files section uses the green pane bar on hover/focus');
  assert.ok(/\.file-explorer-changes-panel\s*\{[^}]*--pane-bar-bg:\s*var\(--panel2\)/.test(preferencesCss), 'embedded Finder Modified-files header stays neutral unless its own section is hovered/focused');
  assert.ok(/\.file-explorer-changes-head\s*\{[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Finder Modified-files header reads the shared pane bar background when focused/hovered');
  assert.ok(/\.file-explorer-changes-panel\s*\{[^}]*isolation:\s*isolate/.test(preferencesCss), 'Finder Modified-files section isolates its sticky header/content layers');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*padding:\s*0 5px 5px/.test(preferencesCss), 'Finder Modified-files panel has no top padding before its header');
  assert.ok(/\.file-explorer-changes-head\s*\{[\s\S]*z-index:\s*6[\s\S]*box-shadow:\s*0 2px 0 var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Finder Modified-files sticky header covers content below without adding a top band');
  assert.ok(/\.diff-ref-suggestion-popover\s*\{[\s\S]*max-height:\s*min\(320px,\s*42vh\)/.test(preferencesCss), 'diff-ref suggestions use a compact custom popup, not the browser-native datalist');
  assert.ok(/\.diff-ref-suggestion-option\s*\{[\s\S]*height:\s*24px/.test(preferencesCss), 'diff-ref popup rows are compact one-line options');
  assert.ok(/const minWidth = Math\.min\(compact \? 880 : 960, viewportWidth - 16\)/.test(changedFilesSource), 'diff-ref popup reserves enough width for normal 80-character commit subjects');
  assert.ok(/const maxWidth = compact \? 1040 : 1120/.test(changedFilesSource), 'diff-ref popup width is capped for the viewport without truncating normal subjects');
  assert.ok(/\.server-update-banner-reload\s*\{[\s\S]*background:\s*var\(--danger-strong\)[\s\S]*color:\s*#ffffff/.test(preferencesCss), 'server update Reload button uses the danger token in dark mode');
  assert.ok(/body\.theme-light \.server-update-banner-reload\s*\{[\s\S]*background:\s*var\(--danger-strong\)[\s\S]*color:\s*#ffffff/.test(preferencesCss), 'server update Reload button uses the danger token in light mode');
  assert.equal(/\.panel\.active-pane \.panel-head\s*\{[\s\S]*background:\s*var\(--pane-tab-panel-head-bg\)/.test(preferencesCss), false, 'focused panes do not recolor the tab strip green');
  assert.ok(preferencesCss.includes('.panel:not(.active-pane):not(.file-explorer-panel) .pane-tab.active'), 'non-focused panes dim their active tab without touching Finder panes');
  assert.ok(preferencesCss.includes('--pane-split-gap: 0px'), 'pane split layout collapses gap through a shared token');
  assert.ok(preferencesCss.includes('--pane-resizer-size: 1px'), 'pane splitter reserves only the 1px separator line');
  assert.ok(preferencesCss.includes('--pane-resizer-bg: rgba(255, 225, 77, 0.72)'), 'dark pane splitter is a visible bright-yellow divider at rest');
  assert.ok(preferencesCss.includes('--pane-resizer-hover-bg: rgba(255, 225, 77, 0.96)'), 'dark pane splitter turns brighter on hover/resize');
  assert.ok(preferencesCss.includes('--pane-resizer-bg: rgba(37, 99, 235, 0.72)'), 'light pane splitter is blue at rest');
  assert.ok(preferencesCss.includes('--pane-resizer-hover-line-size: 5px'), 'pane splitter hover thickens to a clearly visible 5px line over the 1px resting line');
  assert.ok(preferencesCss.includes('--pane-tile-radius: 0'), 'adjacent panes meet flush with square corners (no rounded-corner seam wedges)');
  // #29 + the nav (←/→) and search are centered as a PAIR — the nav absorbs the left free
  // space (margin-inline-start:auto) and the search absorbs the right (margin-inline: 6px auto), so the
  // cluster sits centered between the menubar and the right-side actions, not right-aligned.
  assert.ok(/\.topbar-nav\s*\{[^}]*margin-inline-start:\s*auto/.test(preferencesCss), '#29: the topbar nav group absorbs the left free space so the nav+search pair centers');
  assert.ok(/\.topbar-search\s*\{[^}]*margin-inline:\s*6px auto/.test(preferencesCss), '#29: topbar universal search is centered (auto inline-end) between the menubar and the right-side actions, not right-aligned');
  assert.ok(/\.resizer-row::after\s*\{[^}]*inset-inline: -5px/.test(preferencesCss), '#34: the resizer has a wide invisible grab zone (~5px past the line) so it is easy to grab');
  assert.equal(/\.panel \{[^}]*border: 1px solid var\(--line\)/.test(preferencesCss), false, '#35: panes drop the per-pane border so the only divider is the 1px separator');
  // The active/focus outline is the pane's "natural border" (a --pane-split-gap-wide real border, never
  // clipped, flush to the resizer) colored green — the SAME mechanism for every pane type. Every pane
  // has the transparent border; the active one colors it. No box-shadow, no inset ::after for focus.
  // the state ring IS the --pane-split-gap gutter border, colored per state via the unified
  // --panel-ring-color and faded by --pane-ring-opacity (transparent on a plain inactive pane). Sitting at
  // the seam edge, an active (green) pane and a needs (red) pane touch across the 1px resizer.
  assert.ok(/\.panel\s*\{[^}]*border:\s*var\(--pane-split-gap\) solid color-mix\(in srgb, var\(--panel-ring-color\) var\(--panel-ring-opacity,\s*var\(--pane-ring-opacity/.test(preferencesCss), 'the pane ring is the --pane-split-gap gutter border, colored by --panel-ring-color at per-state opacity (touches the neighbor at the seam)');
  // every focused pane (active or typing-ready) drives the SAME green ring via --panel-ring-color.
  assert.ok(/\.panel\.active-pane\s*\{[^}]*--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(preferencesCss), 'the active pane sets the green ring color');
  assert.ok(/\.panel\.active-pane\s*\{[^}]*--panel-ring-opacity:\s*var\(--pane-active-ring-opacity\)/.test(preferencesCss), 'the active pane uses the user-controlled active ring opacity');
  assert.ok(/\.panel\.typing-ready-pane\s*\{[^}]*--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(preferencesCss), 'a typing-ready pane sets the SAME green ring color as active');
  assert.ok(/\.panel\.typing-ready-pane\s*\{[^}]*--panel-ring-opacity:\s*var\(--pane-active-ring-opacity\)/.test(preferencesCss), 'typing-ready panes use the same user-controlled active ring opacity as active panes');
  assert.equal(/\.panel\.typing-ready-pane\s*\{[^}]*border-color:\s*#465267/.test(preferencesCss), false, 'no gray focus border — focused panes are green, not the old typing-ready gray');
  assert.equal(/body\.theme-light \.panel\.typing-ready-pane\s*\{[^}]*border-color:\s*#9aa6b6/.test(preferencesCss), false, 'no LIGHT-mode gray focus border on terminals (the #465267 twin) — focused terminals stay green in light mode too');
  // no content-edge overlay ring — the ring is the gutter border (asserted above). The
  // needs-* PULSE animates the border-color directly on .panel (not a ::after).
  assert.equal(/\.panel::after\s*\{/.test(preferencesCss), false, 'no .panel::after overlay ring — the ring is the gutter border, so adjacent rings touch at the seam');
  assert.ok(/\.panel\.needs-input-pane,[\s\S]*?\{[^}]*--panel-ring-color:\s*var\(--pane-ring-attention\)/.test(preferencesCss), 'needs-* panes set the red ring color (via the --pane-ring-attention token)');
  assert.ok(/\.panel\.needs-input-pane,[\s\S]*?\.panel\.needs-blocked-pane\s*\{[^}]*animation:\s*attention-ring-fade/.test(preferencesCss), 'needs-* panes pulse the red ring (animation on .panel, not ::after)');
  // image 028: a focused needs-attention pane resolves RED (attention beats the yolo-ready green tint).
  assert.ok(/\.panel\.active-pane\.needs-input-pane[\s\S]*?\.panel\.typing-ready-pane\.needs-blocked-pane\s*\{[^}]*--panel-ring-color:\s*var\(--pane-ring-attention\)/.test(preferencesCss), 'a focused needs-attention pane resolves the red ring color, not the green/yolo tint');
  assert.ok(/--pane-ring-attention:\s*#ff3347/.test(preferencesCss), 'the attention-ring token keeps the #ff3347 dark value');
  assert.equal(/\.panel\.active-pane\s*\{[^}]*border-color:/.test(preferencesCss), false, 'the active pane sets --panel-ring-color (which colors the shared gutter border), not its own border-color');
  // images 003/004 pane-color polish:
  assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-inactive-tab-bg:\s*var\(--active-tab-muted-bg\)/.test(preferencesCss), 'light-mode inactive tabs derive their tint from the active accent (not a fixed green)');
  // image 008: inactive-tab text is DARK in light mode (readable on the light-green tabs/strip), not the
  // dark-tuned near-white #dfe6ef that made it white-on-white.
  assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-text:\s*#1f2937/.test(preferencesCss), 'light-mode tab text is dark (no white-on-white inactive tabs)');
  // The name/dir/detail spans hardcode near-white (for dark tabs); light mode overrides them dark too,
  // or the branch/path text stays white-on-white even with a dark base tab color.
  assert.ok(/body\.theme-light \.pane-tab:not\(\.active\) \.session-button-name,[\s\S]*?\.session-button-detail\s*\{[^}]*color:\s*var\(--pc-control-fg\)/.test(preferencesCss), 'light-mode inactive-tab name/dir/detail text is dark via the shared control foreground token');
  // An inactive pane's inactive tabs follow the gray bar (--pane-bar-bg, which is --panel2 when unfocused);
  // the bars themselves go gray via --pane-bar-bg (asserted above), so only the tabs need this rule.
  assert.ok(/\.panel:not\(\.active-pane\):not\(\.typing-ready-pane\) \.pane-tab:not\(\.active\)\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'an inactive pane\'s inactive tabs follow the gray bar (--pane-bar-bg)');
  assert.ok(/\.panel\.active-pane \.pane-tab:not\(\.active\),\s*\.panel\.typing-ready-pane \.pane-tab:not\(\.active\)\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'an active pane\'s inactive tabs match the bright tab-strip bar (--pane-bar-bg)');
  assert.ok(/--pane-tab-unfocused-active-bg:\s*var\(--pane-tab-active-bg\)/.test(preferencesCss), "an inactive pane's active tab is full green (unfocused-active aliases the focused token)");
  assert.equal(/--pane-tab-unfocused-active-bg:\s*#d2ecc2/.test(preferencesCss), false, 'no lightened light-mode unfocused-active green remains');
  assert.ok(/--inactive-pane-opacity-scale:\s*1/.test(preferencesCss), 'inactive-pane dim defaults to full strength');
  assert.ok(/--inactive-pane-overlay-alpha:\s*0\.18/.test(preferencesCss), 'inactive-pane dim base alpha stays 0.18 in dark mode');
  assert.ok(/body\.theme-light\s*\{[\s\S]*?--inactive-pane-overlay-alpha:\s*0\.09/.test(preferencesCss), 'inactive-pane dim base alpha stays 0.09 in light mode');
  assert.ok(/--inactive-pane-overlay:\s*rgb\(var\(--inactive-pane-overlay-rgb\) \/ calc\(var\(--inactive-pane-overlay-alpha\) \* var\(--inactive-pane-opacity-scale\)\)\)/.test(preferencesCss), 'inactive-pane flat dim is scaled by the opacity slider');
  assert.equal(/inactive-pane-gradient/.test(preferencesCss), false, 'inactive-pane gradient CSS is removed until the feature is revisited');
  {
    // #261: a 0-20px pane spacing setting drives the inter-pane gap; the active pane's green box width
    // == that gap (--pane-split-gap), so it's 0 at spacing 0 and fills the active side up to the line.
    const paneSpacingSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(paneSpacingSrc.includes("numberSetting('appearance.pane_spacing', 3)"), 'runtime reads appearance.pane_spacing with a 3px fallback (matches the backend default)');
    assert.ok(paneSpacingSrc.includes("setProperty('--pane-split-gap'"), '#261: pane spacing drives the --pane-split-gap inter-pane gap');
    assert.equal(paneSpacingSrc.includes('paneSpacing / 5'), false, '#261: the active green box width is NOT a separate scaled value — it uses --pane-split-gap directly');
    assert.ok(/path: 'appearance\.pane_spacing'[\s\S]{0,90}min: 0, max: 20/.test(paneSpacingSrc), '#261: Preferences exposes a 0-20px pane spacing field');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['pref.appearance.pane_spacing.label'], 'Pane spacing', '#261: the pane spacing field has a localized label');
    // The terminal "follow" theme option reads "Follow global color theme" (NOT "app theme"), matching
    // the "Global color theme" setting it follows; the help references the global color theme too.
    const enT = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(enT['pref.appearance.terminal_theme.follow-app'], 'Follow global color theme', 'terminal follow option reads "Follow global color theme"');
    assert.ok(enT['pref.appearance.terminal_theme.help'].includes('global color theme'), 'terminal theme help references the global color theme');
  }
  assert.ok(/\.grid\.drop-preview-root\.drop-preview-top::before,[^{]*\{[^}]*var\(--drop-preview-width/.test(preferencesCss), '#36: the root top/bottom drop preview spans only the non-Finder content width (never covers the docked Finder)');
  assert.ok(/\.layout-column\s*\{[\s\S]*gap:\s*var\(--pane-split-gap\)/.test(preferencesCss), 'pane split layout reads the compact gap token');
  // #261: the REAL inter-pane gap is the flex split container (the column grid gap is a no-op for a
  // single-panel column), so appearance.pane_spacing now actually changes the gap, not just the ring.
  assert.ok(/\.layout-split\s*\{[\s\S]*?gap:\s*0;/.test(preferencesCss), '#261: the flex split container has no gap — pane spacing is the pane border width instead');
  // image 046: the terminal has no horizontal padding, so its dark box meets the resizer flush (the old
  // 2px left/right padding showed a dark sliver between the terminal and the yellow seam). image 034:
  // the former `padding-top: 2px` gap was also removed so the terminal uses the full pane → `padding: 0`.
  assert.ok(/\.terminal\s*\{[^}]*padding:\s*0;/.test(preferencesCss), 'terminal box is flush to the pane edge (padding:0 — no horizontal sliver and no top gap)');
  // image 034: xterm.css defaults `.xterm-viewport` to background #000, which showed as a black line at
  // the top of the LIGHT terminal (the strip the canvas rows don't cover). Force it transparent so the
  // .terminal container's theme background shows through.
  assert.ok(/\.terminal \.xterm:not\(\.allow-transparency\) \.xterm-viewport\s*\{[^}]*background-color:\s*transparent/.test(preferencesCss), 'xterm viewport is transparent (no black #000 line at the top of the terminal)');
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
  assert.ok(/\.file-editor-popout-preview-panel,[\s\S]*?\.file-editor-save-panel\s*\{[^}]*height:\s*20px/.test(preferencesCss), 'pop-out preview button shares the compact editor toolbar button sizing rule');
  assert.ok(/\.file-editor-panel-actions\s*\{[\s\S]*background:\s*color-mix\(in srgb, var\(--panel2\)/.test(preferencesCss), 'editor actions render as one compact gray toolbar');
  assert.ok(/\.file-editor-gutter-panel,\s*\n\.file-editor-wrap-panel,\s*\n\.file-editor-find-panel,\s*\n\.file-editor-diff-panel/.test(preferencesCss), 'diff button shares the compact editor toolbar sizing');
  assert.ok(preferencesCss.includes('--code-diff-add: #56d364'), 'dark diff add base is a brighter vivid green');
  assert.ok(preferencesCss.includes('--code-diff-remove: #ff7b72'), 'dark diff remove base is a brighter vivid red');
  assert.equal(preferencesCss.includes('--code-diff-add: #98c379'), false, 'muted one-dark diff green must not return as the YOLOmux dark default');
  assert.equal(preferencesCss.includes('--code-diff-remove: #e06c75'), false, 'muted one-dark diff red must not return as the YOLOmux dark default');
  assert.ok(preferencesCss.includes('--diff-remove-line-bg: #540c06'), '#250: dark diff removed-line fill matches the sampled deep maroon guide');
  assert.ok(preferencesCss.includes('--diff-add-line-bg: #2b5d16'), '#250: dark diff added-line fill matches the sampled deep green guide');
  assert.ok(preferencesCss.includes('.file-editor-icon-popout-preview'), 'preview pop-out has a distinct icon');
  assert.equal(preferencesCss.includes('preview-linked'), false, 'old paired side-preview ring styling is removed');
  assert.ok(/\.yoagent-global\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent global summary fits narrow panes');
  assert.ok(/\.yoagent-list\s*\{[\s\S]*display:\s*flex[\s\S]*flex-direction:\s*column/.test(preferencesCss), 'YO!agent content column can push the chat section to the bottom');
  assert.ok(/\.yoagent-chat\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent chat fits narrow panes');
  assert.ok(/\.yoagent-chat\s*\{[\s\S]*margin-top:\s*auto/.test(preferencesCss), 'YO!agent chat stays at the bottom of the summary view when there is spare height');
  assert.ok(/\.yoagent-global\s*\{[\s\S]*border-inline-start:\s*3px solid var\(--active-accent-bright\)/.test(preferencesCss), 'YO!agent global summary accent follows the active theme color');
  assert.equal(/\.yoagent-(?:global|refresh|session|chat|message|backend)[\s\S]{0,260}var\(--brand-green\)/.test(preferencesCss), false, 'YO!agent summary/chat accents do not hardcode the green theme token');
  assert.ok(/\.yoagent-chat\.empty\s*\{[\s\S]*grid-template-rows:\s*auto auto/.test(preferencesCss), 'empty YO!agent chat does not stretch an empty history row');
  assert.ok(preferencesCss.includes('body.editor-cursor-block .file-editor-codemirror .cm-cursor'), 'block cursor styling is available for CodeMirror');
  assert.ok(/\.preferences-setting-control\s*\{[^}]*--preferences-control-left-indent:\s*14px/.test(preferencesCss), 'Preferences controls share the 14px left inset');
  assert.ok(/\.preferences-setting-control\s*\{[^}]*--preferences-number-control-width:\s*11ch/.test(preferencesCss), 'Preferences number controls reserve room for the native spinner');
  assert.ok(/\.preferences-radio-group\s*\{[^}]*justify-content:\s*start[^}]*padding-inline-start:\s*var\(--preferences-control-left-indent\)/.test(preferencesCss), 'theme radio choices start from the left inset');
  assert.ok(/\.preferences-setting-control\.setting-type-text input\[type="text"\],\s*\n\.preferences-setting-control\.setting-type-select select\s*\{[^}]*justify-self:\s*start[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(preferencesCss), 'select/text controls use the same left inset');
  assert.ok(/\.preferences-setting-control\.setting-type-number\s*\{[^}]*grid-template-columns:\s*calc\(var\(--preferences-control-left-indent\) \+ var\(--preferences-number-control-width\)\) auto minmax\(0, 1fr\) auto/.test(preferencesCss), 'number rows use the shared left inset with a flexible spacer before Reset');
  assert.ok(/\.preferences-setting-control\.setting-type-number input\[type="number"\]\s*\{[^}]*width:\s*var\(--preferences-number-control-width\)[^}]*justify-self:\s*start[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)[^}]*padding-inline-end:\s*18px/.test(preferencesCss), 'number inputs start at the same left inset and leave room for the spinner');
  assert.ok(/\.preferences-setting-control\.setting-type-list textarea,\s*\n\.preferences-setting-control\.setting-type-textarea textarea\s*\{[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(preferencesCss), 'list/textarea controls use the same left inset');
  assert.ok(preferencesCss.includes('.file-editor-dialog-backdrop'), 'editor conflict and close decisions use the shared editor dialog');
  assert.equal(preferencesCss.includes('.app-menu-search-input'), false, 'Tabs menu no longer renders a sticky search input');
  assert.ok(preferencesCss.includes('.command-palette-detail .fuzzy-match'), 'command palette highlights fuzzy matches in detail text');
  assert.ok(preferencesCss.includes('.file-editor-diff-codemirror .cm-deletedChunk .cm-chunkButtons'), 'diff merge controls are positioned in the chunk margin');
  assert.ok(preferencesCss.includes('inset-inline-end: 8px !important'), 'diff merge controls sit on the right edge');
  assert.ok(/\.file-editor-diff-codemirror \.cm-merge-revert\s*\{[^}]*display:\s*none/.test(preferencesCss), 'diff view hides the left-side merge/revert strip completely');
  assert.ok(/\.file-editor-diff-codemirror \.cm-diff-overview\s*\{[^}]*inset-block:\s*0[\s\S]*inset-inline-end:\s*14px[\s\S]*width:\s*4px[\s\S]*pointer-events:\s*none/.test(preferencesCss), 'diff overview ruler matches the scrollbar track height, sits left of the native scrollbar hit area, and does not intercept scrollbar input');
  assert.ok(/\.file-editor-diff-codemirror \.cm-diff-overview-viewport\s*\{[^}]*position:\s*absolute[\s\S]*border:\s*1px solid rgba\(226,\s*238,\s*248,\s*0\.18\)[\s\S]*background:\s*rgba\(226,\s*238,\s*248,\s*0\.045\)/.test(preferencesCss), 'diff overview shows a faint neutral viewport indicator over the red/green gradient');
  assert.equal(preferencesCss.includes('.cm-diff-overview-tick'), false, 'diff overview has no tick CSS because it paints one gradient on the rail');
  assert.equal(preferencesCss.includes('.cm-diff-overview-tick.split-lane'), false, 'diff overview uses one full-width color per rendered row band, not split lanes');
  assert.ok(/\.file-editor-diff-codemirror \.cm-changedLineGutter,\s*\n\.file-editor-diff-codemirror \.cm-deletedLineGutter\s*\{[^}]*color:\s*inherit[\s\S]*background:\s*transparent/.test(preferencesCss), 'diff left gutter stays neutral; only changed content rows and the right overview rail carry red/green diff color');
  assert.equal(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-(?:changed|deleted)LineGutter\s*\{[^}]*background:\s*#[0-9a-fA-F]+/.test(preferencesCss), false, 'light theme must not reintroduce red/green left-gutter diff indicators');
  assert.ok(/\.file-editor-codemirror \.cm-scroller,\s*\n\.file-editor-codemirror-panel \.cm-scroller\s*\{[^}]*scrollbar-gutter:\s*stable[\s\S]*--pane-scrollbar-size:\s*12px/.test(preferencesCss), 'CodeMirror keeps a stable, draggable native scrollbar gutter while using the shared pane scrollbar behavior');
  assert.ok(/\.panel:is\(\.focused-pane,\s*\.active-pane\):hover\s*\{[^}]*--pane-scrollbar-current-thumb:\s*var\(--pane-scrollbar-thumb-active\)[\s\S]*--pane-scrollbar-current-track:\s*var\(--pane-scrollbar-track-active\)/.test(preferencesCss), 'only focused/active pane hover flips the inherited shared scrollbar variables to the configured cursor-color hover token');
  assert.ok(preferencesCss.includes('Pane scrollbars must always inherit a shared pane scrollbar contract'), 'pane CSS documents that scrollbars must inherit the shared rule');
  for (const selector of [
    '.preferences-scroll',
    '.terminal .xterm-viewport',
    '.transcript-preview',
    '.summary-preview',
    '.event-list',
    '.info-list',
    '.yoagent-chat-history',
    '.yoagent-chat .markdown-body pre',
    '.file-explorer-tree-panel',
    '.file-explorer-changes-panel',
    '.file-editor-raw-panel',
    '.file-editor-preview-pane',
    '.file-editor-preview-pane-panel',
    '.file-editor-image-panel',
    '.file-editor-dialog-body',
    '.file-editor-diff-codemirror .cm-mergeView',
    '.file-editor-codemirror .cm-scroller',
    '.file-editor-codemirror-panel .cm-scroller',
  ]) {
    assert.ok(preferencesCss.includes(selector), `shared pane scrollbar selector includes ${selector}`);
  }
  assert.ok(preferencesCss.includes('scrollbar-color: var(--pane-scrollbar-current-thumb, var(--pane-scrollbar-thumb)) var(--pane-scrollbar-current-track, var(--pane-scrollbar-track));'), 'shared scrollbar rule defaults to neutral gray and inherits configured cursor hover colors only from focused/active pane hover variables');
  assert.ok(/:where\([\s\S]*\)::\-webkit-scrollbar-thumb\s*\{[^}]*background:\s*var\(--pane-scrollbar-current-thumb,\s*var\(--pane-scrollbar-thumb\)\)/.test(preferencesCss), 'shared WebKit thumb uses the inherited current pane thumb token');
  assert.equal(/\.file-editor-panel:hover \.file-editor-codemirror-panel \.cm-scroller[\s\S]*?\{[\s\S]*?scrollbar-color:/.test(preferencesCss), false, 'editor pane no longer has local hover scrollbar coloring');
  assert.equal(/\.file-explorer-tree-panel:hover,[\s\S]*?\.file-explorer-tree-panel:focus-within\s*\{[\s\S]*?scrollbar-color:/.test(preferencesCss), false, 'Finder tree no longer has local hover scrollbar coloring');
  assert.equal(/\.file-explorer-changes-panel:hover,[\s\S]*?\.file-explorer-changes-panel:focus-within\s*\{[\s\S]*scrollbar-color:/.test(preferencesCss), false, 'Modified-files no longer has local hover scrollbar coloring');
  assert.equal(/\.panel\.changes-panel|\.changes-scroll/.test(preferencesCss), false, 'standalone Differ scrollbar CSS is removed');
  assert.equal(/\.panel:hover \.terminal \.xterm-viewport[\s\S]*?\{[\s\S]*?scrollbar-color:/.test(preferencesCss), false, 'terminal no longer has a local hover scrollbar color rule');
  assert.ok(/--diff-add-line-bg:\s*#2b5d16/.test(preferencesCss), '#250: diff added lines use the sampled opaque green fill over the dark bg');
  assert.ok(/\.file-editor-diff-codemirror \.cm-content \.cm-activeLine\s*\{[^}]*background:\s*var\(--diff-full-line-bg,\s*transparent\)/.test(preferencesCss), 'diff active lines keep the same red/green fill as their neighboring changed lines');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror\s*\{[\s\S]*--diff-add-line-bg:\s*#bfeac8/.test(preferencesCss), 'light diff added lines use a more visible green fill');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror\s*\{[\s\S]*--diff-remove-line-bg:\s*#f3b7b7/.test(preferencesCss), 'light diff removed lines use a more visible red fill');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-merge-a \.cm-changedLine,[\s\S]*?\.cm-deletedLine\s*\{[\s\S]*color:\s*#3b0a0a/.test(preferencesCss), 'light diff removed lines force dark red text on the stronger red fill');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-merge-a \.cm-changedText,[\s\S]*?\.cm-deletedChunk \.cm-deletedText\s*\{[\s\S]*color:\s*var\(--danger-light-text\)[\s\S]*background:\s*#f4b7b7/.test(preferencesCss), 'light diff removed inline text uses the danger text token on a distinct red fill (image 055)');
  assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-merge-b \.cm-changedText\s*\{[\s\S]*color:\s*#064e3b[\s\S]*background:\s*#b9e7c2/.test(preferencesCss), 'light diff added inline text uses dark green on a distinct green fill');
  assert.ok(/--diff-remove-line-bg:\s*#540c06/.test(preferencesCss), '#250: diff removed lines use the sampled opaque red fill over the dark bg');
  // a .panel-overlay-root must NOT be the scroll container (else the inactive-pane dim
  // scrolls away). The overlay-root bodies are overflow:hidden; the scrolling lives on inner wrappers.
  assert.ok(/\.preferences-body\s*\{[^}]*overflow:\s*hidden/.test(preferencesCss), 'C3: .preferences-body (overlay-root) must not scroll (overflow:hidden)');
  assert.ok(!/\.preferences-body\s*\{[^}]*overflow:\s*auto/.test(preferencesCss), 'C3: .preferences-body must NOT be overflow:auto');
  assert.ok(/\.preferences-scroll\s*\{[^}]*overflow:\s*auto/.test(preferencesCss), 'C3: .preferences-scroll is the scroll container');
  assert.equal(/\.preferences-scroll\s*\{[^}]*scrollbar-color:/.test(preferencesCss), false, 'Preferences does not customize scrollbar colors locally');
  assert.equal(/\.preferences-scroll::\-webkit-scrollbar-thumb/.test(preferencesCss), false, 'Preferences does not customize WebKit scrollbar thumbs locally');
  assert.equal(/\.changes-body|\.changes-scroll/.test(preferencesCss), false, 'standalone Differ overlay/scroll containers are removed');
  assert.ok(/body\.theme-light \.app-menu-ui-icon\.active\s*\{[\s\S]*background:\s*var\(--active-control-bg\)/.test(preferencesCss), '#251: light mode gives the active app-menu icon button a light-tuned active-control fill (no dark square)');
  assert.ok(/body\.theme-light \.app-menu-tab-command[\s\S]*\{[\s\S]*color:\s*var\(--text\)/.test(preferencesCss), '#252: light mode forces dark text on the rich Tabs/Changes dropdown rows so they are not washed out');
  assert.ok(/body\.theme-light \.file-explorer-changes-panel \.changes-comparison-head\s*\{[\s\S]*background:\s*transparent/.test(preferencesCss), '#253: the Finder "Comparing…" caption has no box chrome in light mode (blends as text)');
  assert.equal(/\.cm-deletedLineGutter\s*\{[^}]*color:\s*transparent/.test(preferencesCss), false, 'deleted rows carry no number via unified-merge read-only widgets, not a transparent-text gutter hack');
  assert.ok(preferencesCss.includes('clip-path: inset(0 -100vw)'), 'diff line backgrounds extend to the full editor width');
  // #44: diffs render as full-line red/green only (highlightChanges:false in both merge views). The old
  // YOLOmux intra-line token overlay stays gone; CodeMirror still emits its built-in changed/deleted
  // text spans, which we only style for light-theme contrast.
  const diffBundle = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal((diffBundle.match(/highlightChanges: false/g) || []).length, 2, '#44: both merge views disable intra-line change highlighting');
  assert.equal(diffBundle.includes('highlightChanges: true'), false, '#44: no merge view re-enables intra-line highlighting');
  assert.equal(preferencesCss.includes('cm-insertedText'), false, '#44: the dead intra-line token rules are removed');
  assert.equal(preferencesCss.includes('--diff-add-text-bg'), false, '#44: the unused intra-line text-bg token is removed');
  assert.ok(preferencesCss.includes('.file-tree-row.repo-non-main'), 'Finder repo rows have non-main branch styling');
  api.setClientSettingsPatchForTest({performance: {server_event_poll_ms: 850, server_background_file_event_poll_ms: 5000, server_directory_event_poll_ms: 3000, remote_resize_delay_ms: 220}});
  const preferencesHtml = api.preferencesPanelHtmlForTest('', []);
  assert.ok(preferencesHtml.indexOf('preferences-search-row') < preferencesHtml.indexOf('preferences-path-rows'), 'preferences search is first');
  assert.ok(preferencesHtml.includes('data-preferences-search-action>YOsearch</button>'), 'preferences search has an explicit YOsearch action');
  const globalPathRowsHtml = preferencesHtml.slice(preferencesHtml.indexOf('<div class="preferences-path-rows"'), preferencesHtml.indexOf('<div class="preferences-sections"'));
  assert.ok(/preferences-path-label">settings<\/span>[\s\S]*settings\.yaml[\s\S]*loaded/.test(globalPathRowsHtml), 'Preferences settings path row shows the loaded age inline');
  assert.equal(globalPathRowsHtml.includes('YOLO rules'), false, 'global Preferences path rows no longer show the YOLO rules path');
  assert.equal(preferencesHtml.includes('preferences-status'), false, 'Preferences does not render a separate loaded/status line');
  const yoloSectionHtml = preferencesHtml.slice(preferencesHtml.indexOf('data-preference-section="YOLO"'), preferencesHtml.indexOf('data-preference-section="YO!agent"'));
  assert.ok(yoloSectionHtml.includes('preferences-path-row preferences-path-row--section'), 'YOLO section contains the YOLO rules path row');
  assert.ok(yoloSectionHtml.includes('YOLO rules'), 'YOLO rules label is inside the YOLO section');
  assert.ok(/YOLO rules[\s\S]*data-copy-path=/.test(yoloSectionHtml), 'YOLO rules copy button is inside the YOLO section');
  assert.ok(yoloSectionHtml.includes('data-yolo-rule-open'), 'YOLO rule file open action remains inside the YOLO section');
  assert.ok(preferencesHtml.includes('preferences-global-reset'), 'preferences always expose Global reset so stale persisted settings can be rewritten');
  assert.ok(preferencesHtml.includes('data-preferences-reset-all'), 'preferences expose a global reset action even when values look default');
  api.setClientSettingsPatchForTest({general: {auto_focus: true}});
  const modifiedPreferencesHtml = api.preferencesPanelHtmlForTest('', []);
  assert.ok(modifiedPreferencesHtml.indexOf('preferences-global-reset') > modifiedPreferencesHtml.indexOf('preferences-sections'), 'preferences global reset is below the setting sections');
  assert.ok(modifiedPreferencesHtml.includes('Global reset'), 'preferences reset is labeled as global in normal-case text');
  assert.ok(modifiedPreferencesHtml.includes('resets every Preferences value'), 'preferences reset carries a broad warning');
  assert.ok(modifiedPreferencesHtml.includes('data-preferences-reset-all'), 'preferences expose a global reset action after a setting changes');
  assert.ok(/\.preferences-global-reset \.preferences-reset-all\s*\{[\s\S]*?background:\s*#d92d20[\s\S]*?font:\s*600 var\(--ui-font-size-sm\)\/1\.1 var\(--ui-font\)/.test(preferencesCss), 'preferences global reset button is red and uses normal UI text');
  assert.ok(/body\.theme-light \.preferences-global-reset \.preferences-reset-all\s*\{[\s\S]*?background:\s*var\(--danger-strong\)/.test(preferencesCss), 'preferences global reset button uses the shared danger token in light mode');
  assert.equal(preferencesHtml.includes('data-preferences-reset-confirm'), false, 'preferences do not show the destructive confirmation until requested');
  const resetConfirmHtml = api.preferencesResetConfirmHtmlForTest();
  assert.ok(resetConfirmHtml.includes('data-preferences-reset-confirm'), 'reset-all requires a second continue action');
  assert.ok(resetConfirmHtml.includes('Continue reset'), 'reset-all confirmation names the continue action');
  assert.ok(resetConfirmHtml.includes('preferences-global-reset confirming'), 'reset-all confirmation makes the warning visibly change');
  assert.ok(preferencesHtml.includes('preferences-setting-control setting-type-number'), 'number controls are identifiable for compact sizing');
  assert.ok(preferencesHtml.includes('data-setting-path="file_explorer.image_preview_max_px"'), 'preferences expose Finder image preview sizing');
  assert.ok(preferencesHtml.includes('data-setting-path="performance.server_event_poll_ms"'), 'Preferences expose the server SSE editor file-change poll interval');
  assert.ok(/data-setting-path="performance\.server_event_poll_ms"[\s\S]*?value="0\.850"[\s\S]*?min="0\.25"[\s\S]*?step="0\.05"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'server-side SSE editor file-change poll displays seconds with a 0.250s minimum');
  assert.ok(/data-setting-path="performance\.server_background_file_event_poll_ms"[\s\S]*?value="5\.000"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'server-side SSE background editor file-change poll defaults to 5 seconds');
  assert.ok(/data-setting-path="performance\.server_directory_event_poll_ms"[\s\S]*?value="3\.000"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'server-side SSE directory-change poll displays seconds');
  assert.ok(/data-setting-path="performance\.latency_refresh_ms"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'latency refresh displays seconds instead of raw milliseconds');
  assert.ok(/data-setting-path="performance\.event_log_refresh_ms"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'event-log refresh displays seconds instead of raw milliseconds');
  assert.ok(/data-setting-path="performance\.popover_show_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'hover popover timing remains in milliseconds');
  assert.ok(/data-setting-path="performance\.menu_hover_open_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'menu hover timing remains in milliseconds');
  assert.ok(/data-setting-path="performance\.tab_popover_show_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'tab hover timing remains in milliseconds');
  assert.ok(/data-setting-path="performance\.tab_popover_follow_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'tab hover follow timing remains in milliseconds');
  assert.ok(/data-setting-path="performance\.remote_resize_delay_ms"[\s\S]*?value="220"[\s\S]*?min="50"[\s\S]*?max="2000"[\s\S]*?step="10"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'remote resize client/server debounce displays milliseconds');
  const performanceHtml = preferencesHtml.slice(preferencesHtml.indexOf('data-preference-section="Performance"'), preferencesHtml.indexOf('data-preference-section="GitHub"'));
  assert.ok(performanceHtml.includes('Server SSE: editor file-change poll'), 'Performance labels the server-side SSE editor file-change interval');
  assert.ok(performanceHtml.includes('Server SSE: background editor file-change poll'), 'Performance labels the server-side SSE background editor interval');
  assert.ok(performanceHtml.includes('Server SSE: directory-change poll'), 'Performance labels the server-side SSE directory-change interval');
  assert.equal(performanceHtml.includes('Client pull: file-change/Differ fallback'), false, 'Performance no longer exposes the removed client file-change fallback interval');
  for (const removedPath of [
    'file_explorer.refresh_seconds',
    'file_explorer.session_files_refresh_seconds',
    'performance.activity_summary_refresh_ms',
    'performance.settings_refresh_ms',
    'performance.metadata_refresh_ms',
    'performance.watched_pr_refresh_ms',
    'performance.pane_state_refresh_ms',
  ]) {
    assert.equal(preferencesHtml.includes(`data-setting-path="${removedPath}"`), false, `${removedPath} is no longer exposed in Preferences`);
  }
  assert.ok(/data-setting-path="performance\.server_event_poll_ms"[\s\S]*data-setting-path="performance\.server_background_file_event_poll_ms"[\s\S]*data-setting-path="performance\.server_directory_event_poll_ms"[\s\S]*data-setting-path="performance\.latency_refresh_ms"[\s\S]*data-setting-path="performance\.event_log_refresh_ms"/.test(performanceHtml), 'Performance order groups server SSE settings before remaining client timers');
  assert.equal(preferencesHtml.includes('data-setting-path="file_explorer.refresh_ms"'), false, 'Finder refresh interval no longer exposes the legacy millisecond setting');
  assert.equal(diffBundle.includes('fileExplorerRefreshMsFromSettings'), false, 'Finder client-pull refresh setting helper is removed');
  assert.equal(diffBundle.includes('sessionFilesRefreshMsFromSettings'), false, 'Changed-files client-pull refresh setting helper is removed');
  assert.equal(diffBundle.includes("initialSetting('file_explorer.refresh_seconds'"), false, 'JS no longer reads the removed Finder fallback setting');
  assert.equal(diffBundle.includes("initialSetting('file_explorer.session_files_refresh_seconds'"), false, 'Changed-files/Differ fallback does not read a separate setting');
  assert.ok(diffBundle.includes("path: 'performance.server_event_poll_ms'") && diffBundle.includes('displayDecimals: 3'), 'server file-change poll stores milliseconds but displays 0.850-style seconds');
  assert.ok(diffBundle.includes("path: 'performance.server_background_file_event_poll_ms'") && diffBundle.includes('displayDecimals: 3'), 'server background file-change poll stores milliseconds but displays 5.000-style seconds');
  assert.ok(diffBundle.includes("path: 'performance.server_directory_event_poll_ms'") && diffBundle.includes('displayDecimals: 3'), 'server directory-change poll stores milliseconds but displays 0.850-style seconds');
  assert.ok(preferencesHtml.includes('data-setting-path="uploads.max_bytes"'), 'preferences expose the upload size cap');
  api.setClientSettingsPatchForTest({uploads: {max_bytes: 64 * 1024 * 1024}});
  const largeUploadPreferencesHtml = api.preferencesPanelHtmlForTest('upload', []);
  assert.ok(largeUploadPreferencesHtml.includes('preferences-setting-advisory'), 'large upload cap shows an rsync recommendation');
  assert.ok(largeUploadPreferencesHtml.includes('rsync -avz'), 'large upload recommendation includes a copyable rsync command');
  assert.ok(/data-setting-path="uploads\.max_bytes"[\s\S]*?value="64"/.test(largeUploadPreferencesHtml), 'upload size cap displays in MB (64), not raw bytes');
  assert.ok(/data-setting-path="uploads\.max_bytes"[\s\S]*?max="512"/.test(largeUploadPreferencesHtml), 'upload size cap min/max are expressed in MB');
  assert.ok(/data-setting-path="uploads\.max_bytes"[\s\S]*?preferences-setting-suffix">MB</.test(largeUploadPreferencesHtml), 'upload size cap labels its unit as MB');
  assert.equal(/data-setting-path="uploads\.max_bytes"[\s\S]*?preferences-setting-suffix">bytes</.test(largeUploadPreferencesHtml), false, 'upload size cap no longer shows a raw bytes suffix');
  assert.ok(preferencesHtml.includes('Auto-focus active pane'), 'auto-focus setting names the whole active pane/view');
  assert.ok(preferencesHtml.includes('enable hover-open menus'), 'auto-focus help covers menu hover behavior');
  assert.ok(preferencesHtml.includes('Off by default'), 'auto-focus help explains the default');
  assert.equal(preferencesHtml.includes('Auto-focus terminals'), false, 'auto-focus setting is not terminal-only');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.theme"'), 'preferences expose the global app theme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.terminal_theme"'), 'preferences expose the terminal color theme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.date_time_hour_cycle"'), 'preferences expose the date/time clock setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_dark_color_scheme"'), 'preferences expose the dark editor scheme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_light_color_scheme"'), 'preferences expose the light editor scheme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_cursor_style"'), 'preferences expose the editor cursor style setting');
  // Cursor color is a preference shared by the active terminal cursor, editor cursor, and pane scrollbar thumb.
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_cursor_color"'), 'preferences expose the editor cursor color setting');
  {
    const cursorSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/const DEFAULT_CURSOR_COLOR\s*=\s*'yellow'/.test(cursorSrc), 'cursor color: yellow remains the named default');
    assert.ok(/const NEON_CURSOR_COLOR_CHOICES\s*=\s*\['laser-lime', 'neon-green', 'neon-cyan', 'neon-magenta', 'neon-orange'\]/.test(cursorSrc), 'cursor color: neon choices are cursor-only, not active-color choices');
    assert.ok(/'laser-lime':\s*\{cursorLabelKey:\s*'pref\.appearance\.editor_cursor_color\.laser-lime',\s*cursor:\s*\{dark:\s*'#ccff00',\s*light:\s*'#6b8f00'\}\}/.test(cursorSrc), 'cursor color: Laser lime is the first neon cursor preset with a readable light-mode variant');
    assert.ok(/function editorCursorColorForScheme[\s\S]*value === 'theme' \? scheme\.cursor : cursorColorForPreset\(value, scheme\?\.dark === false\)/.test(cursorSrc), 'cursor color: theme uses the scheme cursor, color choices use the shared UI color parent');
    assert.ok(/function activeTerminalCursorColorForTheme[\s\S]*value === 'theme' \? baseTheme\.cursor : cursorColorForPreset\(value, resolvedTerminalThemeMode\(\) === 'light'\)/.test(cursorSrc), 'cursor color: active terminal uses the same shared UI color parent');
    assert.ok(cursorSrc.includes("initialSetting('appearance.editor_cursor_color', DEFAULT_CURSOR_COLOR)"), 'editor cursor color defaults through the shared cursor default');
  }
  assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave"'), 'preferences expose editor autosave');
  assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave_delay_seconds"'), 'preferences expose editor autosave delay');
  assert.ok(preferencesHtml.includes('data-setting-path="yoagent.backend"'), 'preferences expose YO!agent backend');
  assert.ok(preferencesHtml.includes('data-setting-path="yoagent.auto_refresh"'), 'preferences expose YO!agent background transcript-summary refresh');
  assert.ok(/data-setting-path="yoagent\.refresh_interval_seconds"[\s\S]*min="30"[\s\S]*max="3600"/.test(preferencesHtml), 'YO!agent background summary interval is bounded in Preferences');
  assert.ok(preferencesHtml.includes('data-setting-path="yoagent.system_prompt"'), 'preferences expose YO!agent prompt');
  assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.system_prompt"[\s\S]*data-setting-autosize="true"/.test(preferencesHtml), 'YO!agent system prompt renders as an autosizing full-width row');
  assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.intro"[\s\S]*data-setting-autosize="true"/.test(preferencesHtml), 'YO!agent intro renders as an autosizing full-width row');
  assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.format"[\s\S]*data-setting-autosize="true"/.test(preferencesHtml), 'YO!agent format renders as an autosizing full-width row');
  assert.ok(/yoagent\.system_prompt[\s\S]*?alwaysEnableReset:\s*true/.test(diffBundle), 'YO!agent system prompt keeps its row Reset button enabled to rewrite stale saved prompts');
  assert.ok(/yoagent\.intro[\s\S]*?alwaysEnableReset:\s*true/.test(diffBundle), 'YO!agent intro keeps its row Reset button enabled to rewrite stale saved prompts');
  assert.ok(/yoagent\.format[\s\S]*?alwaysEnableReset:\s*true/.test(diffBundle), 'YO!agent answer format keeps its row Reset button enabled to rewrite stale saved prompts');
  assert.ok(/const resetDisabled = readOnlyMode \|\| \(!item\.alwaysEnableReset && JSON\.stringify\(value\) === JSON\.stringify\(defaultValue\)\)/.test(diffBundle), 'alwaysEnableReset bypasses only the same-as-default disable rule');
  assert.ok(/data-setting-reset="yoagent\.system_prompt"(?! disabled)/.test(preferencesHtml), 'YO!agent system prompt row Reset is visible and enabled at defaults');
  assert.ok(/data-setting-reset="yoagent\.intro"(?! disabled)/.test(preferencesHtml), 'YO!agent intro row Reset is visible and enabled at defaults');
  assert.ok(/data-setting-reset="yoagent\.format"(?! disabled)/.test(preferencesHtml), 'YO!agent answer format row Reset is visible and enabled at defaults');
  assert.ok(/data-setting-path="file_explorer\.quick_access_paths"[\s\S]*data-setting-type="list"[\s\S]*rows="3"/.test(preferencesHtml), 'list settings keep compact textarea rows');
  assert.ok(/\.preferences-setting-row--wide\s*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/.test(preferencesCss), 'wide preference rows stack to one column');
  assert.ok(/\.preferences-setting-row--wide \.preferences-setting-control textarea\s*\{[\s\S]*grid-column:\s*1 \/ -1[\s\S]*min-height:\s*5lh/.test(preferencesCss), 'wide textarea controls span the row with a compact autosize floor');
  assert.ok(/function autosizePreferenceTextarea\([\s\S]*scrollHeight/.test(diffBundle), 'Preferences autosize textareas resize from their content height');
  assert.ok(/textarea\[data-setting-autosize="true"\]\s*\{[^}]*overflow-y:\s*hidden/.test(preferencesCss), 'autosizing Preferences textareas do not show stale inner scrollbars');
  // #38: long-value text fields (upload filename template, YOLO rule path) render as full-width --wide
  // rows so the whole value shows instead of clipping at the old 24ch cap.
  assert.ok(/preferences-setting-row preferences-setting-row--wide"><label class="preferences-setting-label" for="preference-uploads-filename_template"/.test(preferencesHtml), '#38: the upload filename template is a full-width row so its long value is not clipped');
  assert.ok(/preferences-setting-row preferences-setting-row--wide"><label class="preferences-setting-label" for="preference-yolo-rule_file_path"/.test(preferencesHtml), '#38: the YOLO rule file path is a full-width row so the long path is not clipped');
  assert.ok(/\.preferences-setting-row--wide \.preferences-setting-control\.setting-type-text input\[type="text"\][\s\S]*?\.preferences-setting-row--wide \.preferences-setting-control\.setting-type-select select\s*\{[\s\S]*?width:\s*100%/.test(preferencesCss), '#38: text/select inputs fill the full width inside wide rows');
  const radioModePaths = new Map([
    ['general.default_layout', ['single', 'split', 'grid', 'wall']],
    ['appearance.theme', ['system', 'dark', 'light']],
    ['appearance.active_color', ['green', 'blue', 'orange', 'yellow', 'purple', 'white']],
    ['appearance.terminal_theme', ['follow-app', 'dark', 'light']],
    ['appearance.date_time_hour_cycle', ['24', '12']],
    ['appearance.editor_cursor_style', ['line', 'block']],
    ['appearance.editor_cursor_color', ['green', 'blue', 'orange', 'yellow', 'purple', 'white', 'laser-lime', 'neon-green', 'neon-cyan', 'neon-magenta', 'neon-orange', 'theme']],
    ['yolo.prompt_source', ['hybrid', 'pane']],
    ['file_explorer.root_mode', ['fixed', 'sync']],
    ['file_explorer.image_open_mode', ['same-tab', 'new-tab']],
    ['yoagent.backend', ['auto', 'codex', 'claude']],
    ['yoagent.invocation', ['cli', 'api-key']],
  ]);
  for (const [path, values] of radioModePaths) {
    const pathPattern = path.replace(/\./g, '\\.');
    assert.equal(new RegExp(`<select[^>]*data-setting-path="${pathPattern}"`).test(preferencesHtml), false, `${path} is a compact mode and renders as radios, not a select`);
    for (const value of values) {
      assert.ok(new RegExp(`type="radio"[^>]*value="${value}"[^>]*data-setting-path="${pathPattern}"`).test(preferencesHtml), `${path} radio renders ${value}`);
    }
  }
  assert.ok(preferencesHtml.includes('>Same Tab<'), 'string radio labels are humanized');
  assert.equal(/data-setting-path="appearance\.theme"[\s\S]{0,180}preferences-radio-swatches/.test(preferencesHtml), false, 'Global color theme radios do not show color swatches');
  assert.equal(/<select[^>]*data-setting-path="appearance\.active_color"/.test(preferencesHtml), false, 'Active color renders as radios, not a select');
  assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#86d600[\s\S]*--preferences-radio-swatch:#4f9e3a/.test(preferencesHtml), 'Active color Green radio shows joined actual dark/light accent swatches');
  assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#f97316[\s\S]*--preferences-radio-swatch:#b91c1c/.test(preferencesHtml), 'Active color Blood orange light swatch is redder than Solar gold');
  assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#eab308[\s\S]*--preferences-radio-swatch:#d6a400/.test(preferencesHtml), 'Active color Solar gold light swatch is a brighter gold');
  assert.ok(/\.preferences-radio-group\.has-swatches\s*\{[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(148px,\s*1fr\)\)/.test(preferencesCss), 'swatched Preferences radio groups use a shared grid so wrapped rows align');
  assert.ok(/\.preferences-radio\.has-swatches\s*\{[\s\S]*grid-template-columns:\s*18px 34px minmax\(0,\s*1fr\)/.test(preferencesCss), 'swatched Preferences radios align radio, color chips, and label in fixed columns');
  assert.ok(/\.preferences-radio-swatch\s*\{[\s\S]*border-radius:\s*2px/.test(preferencesCss), 'Preferences color swatches are boxed, not round dots');
  assert.ok(/\.preferences-radio-swatches\.joined\s*\{[\s\S]*gap:\s*0/.test(preferencesCss), 'Preferences active-color swatches are connected into one segmented box');
  assert.ok(/\.keyboard-shortcuts-section h3\s*\{[\s\S]*color:\s*var\(--active-accent-bright\)/.test(preferencesCss), 'Keyboard shortcuts section headers follow the active theme color');
  assert.ok(preferencesHtml.includes('>New Tab<'), 'hyphenated string radio labels are humanized');
  // "No agent" (deterministic) is no longer a selectable backend — Auto still falls back to it internally,
  // but it is never offered as a pick in Preferences or the composer pill.
  assert.equal(/data-setting-path="yoagent\.backend"[\s\S]*?value="deterministic"/.test(preferencesHtml), false, 'Preferences no longer offer No agent (deterministic) as a backend option');
  assert.equal(preferencesHtml.includes('Deterministic'), false, 'Preferences do not expose the internal deterministic backend label');
  // #41: the YO!agent backend defaults to auto (codex -> claude -> No agent) and Preferences offers it.
  assert.ok(/value="auto"[^>]*data-setting-path="yoagent\.backend"[\s\S]*?>\s*<span>Auto \(Codex → Claude\)<\/span>/.test(preferencesHtml), '#41: the YO!agent backend radios offer the Auto option');
  assert.equal(preferencesHtml.includes('data-setting-path="appearance.editor_color_scheme"'), false, 'preferences do not show the legacy single mixed editor scheme setting');
  assert.ok(preferencesHtml.includes('<optgroup label="Dark">'), 'dark editor schemes are grouped under Dark');
  assert.ok(preferencesHtml.includes('<optgroup label="Light">'), 'light editor schemes are grouped under Light');
  assert.ok(preferencesHtml.indexOf('YOLOmux Dark') < preferencesHtml.indexOf('Popular IDE Dark+'), 'YOLOmux dark scheme appears first');
  assert.ok(preferencesHtml.indexOf('Popular IDE Light+') < preferencesHtml.indexOf('YOLOmux Light'), 'Popular IDE Light+ is the first light scheme');
  assert.ok(preferencesHtml.indexOf('YOLOmux Light') < preferencesHtml.indexOf('GitHub Light'), 'YOLOmux light scheme remains ahead of GitHub Light');
  assert.equal(api.globalThemeModeForTest(), 'dark');
  assert.equal(api.globalThemeIsDark(), true, 'global theme defaults dark');
  assert.equal(api.globalThemeLabel(), 'Dark');
  assert.equal(api.nextGlobalThemeMode(), 'light');
  assert.equal(api.terminalThemeModeForTest(), 'follow-app', 'terminal theme defaults to follow-app (matches the global app theme)');
  assert.equal(api.terminalThemeForGlobalTheme('light').background, '#ffffff', 'follow-app default gives a light terminal in light app mode');
  assert.equal(api.dateTimeHourCycleForTest(), '24', 'date/time clock defaults to 24-hour');
  {
    const timestamp = Date.UTC(2026, 5, 4, 19, 17) / 1000;
    const hour24Text = api.sessionFileTimeText(timestamp);
    assert.equal(/[AP]\.?M\.?/i.test(hour24Text), false, '24-hour file dates do not show AM/PM');
    assert.ok(/\b\d{2}:\d{2}\b/.test(hour24Text), '24-hour file dates render a two-digit clock');
    api.setDateTimeHourCycleForTest('12');
    const hour12Text = api.sessionFileTimeText(timestamp);
    assert.ok(/[AP]\.?M\.?/i.test(hour12Text), '12-hour file dates show AM/PM');
    api.setDateTimeHourCycleForTest('bogus');
    assert.equal(api.dateTimeHourCycleForTest(), '24', 'invalid date/time clock values normalize to 24-hour');
    api.setFileExplorerTreeDateModeForTest('none');
    assert.equal(api.sessionFileDisplayTimeText(timestamp), '', 'file-tree date mode None hides Finder/Differ timestamps');
    assert.equal(api.fileExplorerTreeDateModeLabel('none'), 'None', 'file-tree date mode None label uses the source locale catalog');
    assert.equal(api.fileExplorerTreeDateModeButtonLabel('none'), 'Date', 'file-tree date mode None button shows crossed-out Date text');
    assert.equal(api.fileExplorerTreeDateModeLabel('date'), 'Date', 'file-tree date mode Date label uses the source locale catalog');
    assert.equal(api.fileExplorerTreeDateModeLabel('relative'), 'Ago', 'file-tree date mode Ago label uses the source locale catalog');
    assert.equal(api.fileExplorerTreeDateModeTitle('relative'), 'Date display: Ago. Click to cycle None, Date, Ago.', 'file-tree date mode tooltip uses localized catalog text');
    api.setFileExplorerTreeDateModeForTest('date');
    assert.equal(api.sessionFileDisplayTimeText(timestamp), api.sessionFileTimeText(timestamp), 'file-tree date mode Date uses the localized absolute timestamp');
    api.setFileExplorerTreeDateModeForTest('relative');
    assert.equal(api.sessionFileRelativeTimeText(1000, 999), 'now', 'file-tree relative dates localize the now case');
    assert.equal(api.sessionFileRelativeTimeText(1000, 1059), '<1 min ago', 'file-tree relative dates localize the sub-minute case');
    assert.equal(api.sessionFileRelativeTimeText(1000, 1060), '1 min ago', 'file-tree relative dates show minute age');
    assert.equal(api.sessionFileRelativeTimeText(1000, 19720), '5.2 hrs ago', 'file-tree relative dates show decimal hour age');
    assert.equal(api.sessionFileRelativeTimeText(1000, 217000), '2.5 days ago', 'file-tree relative dates show decimal day age');
  }
  api.setTerminalThemeModeForTest('light');
  assert.equal(api.terminalThemeForGlobalTheme('dark').background, '#ffffff', 'terminal light theme is explicit opt-in');
  assert.equal(api.terminalThemeForGlobalTheme('dark').blue, '#0451a5');
  assert.equal(api.terminalThemeForGlobalTheme('dark').selectionBackground, '#93c5fd', 'light terminal selection uses a visible blue fill');
  assert.equal(api.terminalThemeForGlobalTheme('dark').selectionForeground, '#071327', 'light terminal selection forces readable selected text');
  // a white terminal auto-darkens faint 24-bit agent text via minimumContrastRatio.
  assert.equal(api.terminalMinimumContrastRatio('dark'), 4.5, '#32: light terminal raises the minimum contrast ratio');
  api.setTerminalThemeModeForTest('dark');
  assert.equal(api.terminalMinimumContrastRatio('dark'), 3, 'dark terminal uses a moderate 3:1 floor so a light-on-white agent composer (Codex input) is forced readable');
  api.setTerminalThemeModeForTest('light');
  api.setTerminalThemeModeForTest('follow-app');
  assert.equal(api.terminalThemeForGlobalTheme('light').background, '#ffffff', 'follow-app maps to the resolved app theme');
  assert.equal(api.terminalThemeForGlobalTheme('dark').background, '#11151d');
  api.setTerminalThemeModeForTest('dark');
  assert.equal(api.terminalThemeForGlobalTheme('dark').selectionBackground, UI_PINS.textSelectionBg, 'dark terminal selection uses a prominent blue fill');
  assert.equal(api.terminalThemeForGlobalTheme('dark').selectionForeground, '#ffffff', 'dark terminal selection forces readable selected text');
  api.setGlobalThemeModeForTest('light');
  api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
  assert.ok(api.bodyClassListForTest().contains('theme-light'), 'global light theme marks the body');
  assert.ok(api.bodyClassListForTest().contains('theme-resolved-light'), 'global light theme tracks the resolved mode');
  assert.equal(api.activeEditorSchemeForTest().label, 'YOLOmux Light', 'inherited editor theme follows global light (brand YOLOmux Light default)');
  assert.equal(api.configuredEditorSchemeForMode(true), 'dark');
  assert.equal(api.configuredEditorSchemeForMode(false), 'yolomux-light');
  api.setGlobalThemeModeForTest('dark');
  api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
  assert.equal(api.fileEditorThemeModeForTest(), 'inherit');
  assert.equal(api.activeEditorSchemeForTest().label, 'YOLOmux Dark');
  assert.equal(api.activeEditorSchemeForTest().activeLine, 'rgba(255, 255, 255, 0.04)');
  assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(96, 165, 250, 0.38)');
  assert.equal(api.activeEditorSchemeForTest().diff.addFg, '#56d364');
  assert.equal(api.activeEditorSchemeForTest().diff.removeFg, '#ff7b72');
  assert.equal(api.activeEditorSchemeForTest().activeLine.includes('118, 185, 0'), false, 'YOLOmux dark active line is no longer green');
  assert.equal(api.activeEditorSchemeForTest().selection.includes('118, 185, 0'), false, 'YOLOmux dark selection is no longer green');
  const editorSelectionSource = fs.readFileSync('static/yolomux.js', 'utf8');
  const editorSelectionCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(editorSelectionSource.includes("&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground"), 'editor selection theme beats CodeMirror focused selection defaults');
  assert.ok(editorSelectionSource.includes("boxShadow: scheme.dark ? 'inset 0 0 0 1px rgba(191, 219, 254, 0.42)' : 'inset 0 0 0 1px rgba(29, 78, 216, 0.24)'"), 'editor selections get a visible edge without an opaque fill');
  assert.equal(editorSelectionSource.includes("'.cm-searchMatch-selected': {\n      backgroundColor: scheme.dark ? 'rgba(118, 185, 0, 0.84)'"), false, 'selected editor search matches are not green, so they remain visible on green active/highlighted rows');
  assert.ok(/'\.cm-searchMatch-selected': \{[\s\S]*backgroundColor: scheme\.dark \? '#ffd166' : '#ff9f1c'[\s\S]*fontWeight: '800'/.test(editorSelectionSource), 'selected editor search matches use high-contrast yellow/orange fill and bold text');
  assert.ok(/\.file-editor-diff-codemirror \.cm-searchMatch\s*\{[\s\S]*z-index:\s*1[\s\S]*background:\s*var\(--diff-search-match-bg\) !important/.test(editorSelectionCss), 'Differ search matches sit above green/red diff row fills');
  assert.ok(/\.file-editor-diff-codemirror \.cm-searchMatch-selected\s*\{[\s\S]*background:\s*var\(--diff-search-selected-bg\) !important[\s\S]*font-weight:\s*900/.test(editorSelectionCss), 'selected Differ search matches use a stronger non-green fill');
  assert.ok(editorSelectionSource.includes("}, {dark: scheme.dark});"), 'CodeMirror receives the active light/dark theme flag');
  assert.ok(editorSelectionSource.includes("backgroundColor: 'transparent !important'"), 'CodeMirror native editor selection background is suppressed so drawSelection owns the fill');
  assert.ok(/\.file-editor-codemirror \.cm-content ::selection,[\s\S]*?background:\s*transparent !important[\s\S]*?color:\s*inherit !important/.test(editorSelectionCss), 'static CSS keeps global browser selection colors out of CodeMirror');
  assert.equal(/body\.editor-theme-light \.file-editor-codemirror \.cm-content,[\s\S]*?background:\s*var\(--editor-bg\) !important/.test(editorSelectionCss), false, 'light CodeMirror content stays transparent so the selection layer remains visible');
  for (const scheme of ['one-dark', 'dracula', 'monokai', 'popular-ide-dark-plus', 'nord']) {
    api.setFileEditorThemeMode(scheme);
    assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(96, 165, 250, 0.38)', `${scheme} uses the audited dark editor selection fill`);
  }
  api.setFileEditorThemeMode('github-light');
  assert.equal(api.fileEditorThemeModeForTest(), 'github-light');
  assert.equal(api.activeEditorSchemeForTest().label, 'GitHub Light');
  assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(37, 99, 235, 0.34)', 'light editor schemes use a visible blue selection fill');
  for (const scheme of ['yolomux-light', 'popular-ide-light-plus', 'one-light', 'solarized-light']) {
    api.setFileEditorThemeMode(scheme);
    assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(37, 99, 235, 0.34)', `${scheme} uses the audited light editor selection fill`);
  }
  const legacySchemePrefix = ['vs', 'code'].join('');
  api.setFileEditorThemeMode(`${legacySchemePrefix}-dark-plus`);
  assert.equal(api.fileEditorThemeModeForTest(), 'popular-ide-dark-plus', 'legacy dark scheme id migrates to the Popular IDE dark scheme');
  api.setFileEditorThemeMode('github-light');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--editor-scheme-bg'), '#ffffff');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--code-keyword'), '#cf222e');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-markdown-heading'), 'var(--active-accent)');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--markdown-heading'), 'var(--active-accent)');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-markdown-heading-bg'), 'transparent');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--markdown-heading-bg'), 'transparent');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-code-inline'), '#a40e26');
  assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-code-inline-bg'), '#fff1d6');
  assert.notEqual(api.activeEditorSchemeForTest().syntax.heading, api.activeEditorSchemeForTest().syntax.link);
  assert.notEqual(api.activeEditorSchemeForTest().syntax.inlineCode, api.activeEditorSchemeForTest().syntax.heading);
  assert.equal(api.editorThemeLabel(), 'GitHub Light editor scheme');
  api.setFileEditorThemeMode('dark');
  assert.equal(api.editorPreviewThemeStateForTest(), 'dark');
  api.cycleEditorThemeMode();
  assert.equal(api.editorPreviewThemeStateForTest(), 'light', 'theme toggle moves dark preview to light preview');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
  api.cycleEditorThemeMode();
  assert.equal(api.editorPreviewThemeStateForTest(), 'vanilla', 'theme toggle moves light preview to vanilla preview');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'vanilla');
  assert.equal(api.editorThemeLabel(), 'Vanilla preview');
  api.cycleEditorThemeMode();
  assert.equal(api.editorPreviewThemeStateForTest(), 'dark', 'theme toggle moves vanilla preview back to dark preview');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
  assert.equal(api.fileEditorThemeModeForTest(), 'dark', 'vanilla leaves by restoring a dark editor scheme');
  api.setFileEditorThemeMode('dark');
  api.cycleEditorThemeMode({includeVanilla: false});
  assert.equal(api.editorPreviewThemeStateForTest(), 'light', 'edit/diff theme toggle moves dark to light');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
  api.cycleEditorThemeMode({includeVanilla: false});
  assert.equal(api.editorPreviewThemeStateForTest(), 'dark', 'edit/diff theme toggle moves light back to dark without vanilla');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
  api.setFileEditorThemeMode('light');
  assert.equal(api.fileEditorThemeModeForTest(), 'yolomux-light', 'legacy light storage value maps to the YOLOmux Light default');
  assert.equal(api.activeEditorSchemeForTest().bg, '#ffffff', 'YOLOmux Light uses a bright white editor background');
  assert.equal(api.activeEditorSchemeForTest().previewBg, '#ffffff', 'YOLOmux Light preview background is bright white');
  assert.equal(api.activeEditorSchemeForTest().syntax.comment, '#008000', 'YOLOmux Light uses Popular IDE-style green comments');
  assert.equal(api.activeEditorSchemeForTest().syntax.keyword, '#0000ff', 'YOLOmux Light uses Popular IDE-style blue language keywords');
  assert.equal(api.activeEditorSchemeForTest().syntax.control, '#af00db', 'YOLOmux Light uses Popular IDE-style magenta Rust control keywords');
  assert.equal(api.activeEditorSchemeForTest().syntax.function, '#267f2e', 'YOLOmux Light uses Popular IDE-style green function declarations');
  assert.equal(api.activeEditorSchemeForTest().syntax.type, '#008080', 'YOLOmux Light uses Popular IDE-style teal type declarations');
  assert.equal(api.activeEditorSchemeForTest().syntax.property, '#5f3b00', 'YOLOmux Light uses Popular IDE-style brown field and parameter names');
  api.setFileEditorPreviewDisplayMode('vanilla');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'vanilla', 'vanilla preview mode is stored separately from the editor scheme');
  api.setFileEditorThemeMode('github-light');
  assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme', 'choosing a concrete editor theme exits vanilla preview mode');
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
  api.setPendingPreferencesRenderForTest(false);
  api.setPreferencesScrollActiveUntilForTest(Date.now() + 10000);
  api.renderPreferencesPanelsForTest();
  assert.equal(api.pendingPreferencesRenderForTest(), true, 'passive Preferences render is deferred while the user is scrolling');
  api.setPendingPreferencesRenderForTest(false);
  api.renderPreferencesPanelsForTest({force: true});
  assert.equal(api.pendingPreferencesRenderForTest(), false, 'forced Preferences render still runs during active scroll');
  const scrollNow = Date.now();
  api.setPreferencesScrollActiveUntilForTest(0);
  api.notePreferencesScrollActivityForTest(scrollNow);
  assert.ok(api.preferencesScrollActiveUntilForTest() >= scrollNow + 200, 'Preferences scroll activity creates a render-defer window');
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
  // #45: a needs-input popover near the top-right whose live width measures 0 (pre-paint) must still
  // clamp fully on-screen for its real rendered width — using the popover inline-size fallback, NOT the
  // tiny tab width (which let a wide popover overflow/clip off the right edge).
  const zeroWidthPopover = {getBoundingClientRect() { return {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0}; }};
  api.positionPaneTabPopover({
    getBoundingClientRect() { return {left: 1080, right: 1160, top: 40, bottom: 68, width: 80, height: 28}; },
    querySelector() { return zeroWidthPopover; },
  });
  const zeroWidthLeft = Number.parseInt(popoverStyle.getPropertyValue('--pane-tab-popover-left'), 10);
  assert.ok(zeroWidthLeft + 520 <= 1200, '#45: an unmeasured (0-width) right-edge popover still clamps on-screen for its real width');
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
  assert.equal(appMenuPopover.style.getPropertyValue('--app-menu-fit-width'), `${appMenuPopover.rect.width}px`);
  assert.equal(appMenuPopover.style.getPropertyValue('--app-menu-fit-offset'), '-180px');
  // C14: the menu-width measurer must un-clip the command label + detail spans (they were omitted, so the
  // menu measured to the LABELS and the longer detail sub-lines ellipsized with "…").
  const appMenuMeasureSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  const measureBody = appMenuMeasureSrc.slice(appMenuMeasureSrc.indexOf('function measureAppMenuContentWidth('), appMenuMeasureSrc.indexOf('function fitAppMenuPopover('));
  assert.ok(measureBody.includes(".app-menu-label'"), 'C14: the measurer neutralizes .app-menu-label truncation');
  assert.ok(measureBody.includes(".app-menu-detail'"), 'C14: the measurer neutralizes .app-menu-detail truncation');
  assert.ok(measureBody.includes("clone.classList?.contains('app-submenu-popover')"), 'C14: a standalone submenu clone is forced visible for measurement');
  assert.ok(measureBody.includes("querySelectorAll('.app-submenu-popover')"), 'C14: nested submenu widths are included in parent menu measurement');
  assert.ok(appMenuMeasureSrc.includes('_appMenuFontsRefit'), 'C14: a one-time fonts.ready re-fit corrects a cold first measurement');
  const topbarMenuCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.app-submenu-popover\s*\{[\s\S]*?max-width:\s*calc\(100% - var\(--app-submenu-inline-offset\)\)/.test(topbarMenuCss), 'C14: nested submenus clamp to the parent menu instead of clipping under overflow');
  assert.ok(/\.app-menu-detail\s*\{[\s\S]*?max-width:\s*min\(58ch/.test(topbarMenuCss), 'C14: app menu detail rows get enough width before ellipsizing');
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
  // P0 menu-bar: View → Sort tab list orders the Tabs navigator. 'name' sorts by label (deterministic
  // without session state); 'default' is identity. The View menu exposes the submenu with all 3 modes.
  api.setTabsMenuSortMode('default');
  assert.deepEqual(api.sortTabItemsForMenu(['3', '1', '2']), ['3', '1', '2'], 'default sort keeps the incoming order');
  api.setTabsMenuSortMode('name');
  assert.deepEqual(api.sortTabItemsForMenu(['3', '1', '2']), ['1', '2', '3'], 'name sort orders the tab list by label');
  api.setTabsMenuSortMode('default');
  const sortViewMenu = api.appMenuTree().find(menu => menu.id === 'view');
  const sortSubmenu = sortViewMenu.items.find(item => item.type === 'submenu' && item.label === api.t('menu.view.sortTabs'));
  assert.ok(sortSubmenu, 'View menu has a Sort tab list submenu');
  assert.deepEqual(sortSubmenu.items.map(item => item.label), [api.t('menu.view.sortTabs.default'), api.t('menu.view.sortTabs.attention'), api.t('menu.view.sortTabs.name')], 'the sort submenu offers default / needs-me / name');
  const layoutSubmenu = sortViewMenu.items.find(item => item.type === 'submenu' && item.label === api.t('menu.view.layout'));
  assert.ok(layoutSubmenu, 'View menu has a Layout submenu');
  assert.deepEqual(layoutSubmenu.items.map(item => item.label), api.layoutModeValues.map(mode => api.t(`menu.view.layout.${mode}`)), 'View layout options match the shared layout modes');
  assert.equal(layoutSubmenu.items.some(item => item.disabled), false, 'Every View layout option is implemented');
  const menus = api.appMenuTree();
  assert.equal(menus.map(menu => menu.label).join(','), 'File,View,tmux,Tabs,Help');
  assert.equal(menus.some(menu => menu.id === 'yolo'), false);
  const aboutHelpMenu = menus.find(menu => menu.id === 'help');
  assert.equal(aboutHelpMenu.items.some(item => item.type === 'submenu' && item.label === 'About'), false, 'Help menu no longer nests About as a submenu');
  const aboutCommand = aboutHelpMenu.items[aboutHelpMenu.items.length - 1];
  assert.equal(aboutCommand.type, 'command', 'Help menu ends with an About command');
  assert.equal(aboutCommand.label, 'About', 'About stays at the bottom of Help');
  assert.equal(aboutCommand.action, api.showAboutModal, 'About command opens the modal');
  assert.ok(api.aboutBrandHtml().includes('about-brand-yo'), 'About brand includes the large YO segment');
  assert.ok(api.aboutBrandHtml().includes('about-brand-lo'), 'About brand includes the large LO segment');
  assert.ok(api.aboutBrandHtml().includes('about-brand-x'), 'About brand includes the large x segment');
  api.showAboutModal();
  assert.ok(api.modalClassForTest().includes('open'), 'About opens the shared modal');
  assert.ok(api.modalClassForTest().includes('about-open'), 'About modal gets its compact layout class');
  assert.equal(api.modalTitleForTest(), api.t('menu.help.about'), 'About modal title is localized');
  assert.ok(api.modalBodyHtmlForTest().includes('about-brand-row'), 'About modal renders the large YOLOmux mark');
  assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${api.t('menu.help.about.datetime')}</dt>`), 'About modal contains localized date metadata');
  assert.ok(api.modalBodyHtmlForTest().includes('<dt>SHA</dt>'), 'About modal contains SHA metadata');
  assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${api.t('menu.help.about.version')}</dt>`), 'About modal contains localized version metadata');
  assert.ok(api.modalBodyHtmlForTest().includes('Keiven Chang'), 'About modal contains Keiven Chang');
  assert.ok(api.modalBodyHtmlForTest().includes('https://www.linkedin.com/in/keiven/'), 'Keiven Chang entry links to LinkedIn');
  assert.ok(fs.readFileSync('static/yolomux.js', 'utf8').includes('https://www.linkedin.com/in/keiven/'), 'About LinkedIn URL is bundled');
  assert.ok(api.modalBodyHtmlForTest().includes('https://github.com/keivenchang/yolomux'), 'DOIT.60: About modal links to the project GitHub repo');
  assert.ok(api.modalBodyHtmlForTest().includes('about-github'), 'DOIT.60: the GitHub link carries the about-github class');
  assert.ok(api.modalBodyHtmlForTest().includes(`>${api.t('menu.help.about.github')}</a>`), 'DOIT.60: the GitHub link uses the localized label');
  assert.ok(preferencesCss.includes('.modal.about-open'), 'About modal has compact modal chrome');
  assert.ok(/\.modal\.about-open\s*\{[\s\S]*?z-index:\s*var\(--z-pane-modal\)/.test(preferencesCss), 'About modal sits above pane resizers and other pane-local overlays');
  assert.ok(/\.modal\.about-open::before\s*\{[\s\S]*?position:\s*fixed/.test(preferencesCss), 'About modal dims the live app behind it so background lines do not bleed through');
  assert.ok(preferencesCss.includes('.about-brand-row'), 'About modal has a large brand row style');
  assert.ok(/\.about-brand-yo\s*\{[\s\S]*animation:\s*yolo-marker-rotate/.test(preferencesCss), 'About YO glyph spins with the shared YOLO marker animation');
  assert.ok(/\.about-brand-yo\s*\{[\s\S]*background:\s*var\(--pane-tab-yolo-bg\)/.test(preferencesCss), 'About YO glyph follows the active theme color');
  const brandCss = fs.readFileSync('static/brand.css', 'utf8');
  assert.ok(/--brand-primary-green:\s*var\(--brand-green,\s*#76b900\)/.test(brandCss), 'topbar YOLOmux LO stays brand green regardless of active color');
  assert.equal(/--brand-primary-green:\s*var\(--active-control-bg/.test(brandCss), false, 'topbar YOLOmux LO is not routed through the active color preference');
  assert.equal(api.testElementForId('closeModal').textContent || 'X', 'X', 'About modal close button is an X');
  assert.ok(fs.readFileSync('yolomux_lib/web.py', 'utf8').includes('<button id="closeModal" title="Close" aria-label="Close">X</button>'), 'HTML shell renders the modal close button as X');
  // File/View/Tabs/Help menu labels localize; tmux (a tool name) stays as-is.
  const zhHantMenu = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
  api.i18nSetCatalogForTest('zh-Hant', zhHantMenu);
  api.setActiveLocaleForTest('zh-Hant');
  assert.equal(api.appMenuTree().map(menu => menu.label).join(','), '檔案,檢視,tmux,分頁,說明', 'menu bar localizes (tmux unchanged)');
  api.showAboutModal();
  assert.equal(api.modalTitleForTest(), zhHantMenu['menu.help.about'], 'About modal title localizes to Chinese');
  assert.ok(api.modalBodyHtmlForTest().includes('about-brand-yo') && api.modalBodyHtmlForTest().includes('>優<'), 'Traditional Chinese About brand uses 優');
  assert.ok(api.modalBodyHtmlForTest().includes('>樂<'), 'Traditional Chinese About brand uses 樂');
  assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${zhHantMenu['menu.help.about.datetime']}</dt>`), 'Traditional Chinese About date label is localized');
  assert.ok(api.modalBodyHtmlForTest().includes('<dt>SHA</dt>'), 'SHA remains literal in Chinese About');
  assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${zhHantMenu['menu.help.about.version']}</dt>`), 'Traditional Chinese About version label is localized');
  assert.equal(api.testElementForId('closeModal').getAttribute('aria-label'), zhHantMenu['common.close'], 'About close button aria-label localizes');
  api.setActiveLocaleForTest('en');
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
  // New-session items: just the agent name (no "+" prefix); the detail shows the params passed.
  assert.ok(tmuxMenuLabels.includes('Claude'));
  assert.ok(tmuxMenuLabels.includes('Codex'));
  assert.ok(tmuxMenuLabels.includes('Term'), 'Term is always offered (a plain shell), not greyed unavailable');
  assert.equal(tmuxMenuLabels.includes('+ Claude'), false, 'the "+" prefix is dropped from new-session items');
  {
    const newSessionSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(newSessionSrc.includes('function agentLaunchParams(agent)'), 'the launch-params helper exists');
    assert.ok(/menuCommand\(agentName\(agent\), \(\) => createNextSession\(agent\)/.test(newSessionSrc), 'new-session label is the agent name (no "+"), action launches it');
    assert.ok(/capped \? t\('menu\.tmux\.limitReached'\) : agentLaunchParams\(agent\)/.test(newSessionSrc), 'a launchable new-session item shows the params passed as its detail');
  }
  assert.ok(tmuxMenuLabels.includes("Transcript for session '1'"));
  assert.ok(tmuxMenuLabels.includes("YO!summary for session '1'"));
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
  assert.ok(source.includes('function startPinTabShortcutChord()'), 'Pin Tab shortcut uses an explicit chord starter');
  assert.ok(source.includes("pendingGlobalShortcutChord = 'pin-tab'"), 'Pin Tab shortcut records the pending chord');
  assert.ok(source.includes("if (pendingGlobalShortcutChord === 'pin-tab' && key === 'enter')"), 'Pin Tab shortcut completes on Enter');
  assert.ok(source.includes('toggleActiveTabPinned();'), 'Pin Tab shortcut toggles the active tab');
  assert.ok(source.includes("window.addEventListener('keydown', handleGlobalShortcutKeydown, true)"), 'global shortcuts run in capture phase before focused controls swallow them');
  assert.ok(source.includes("if (mod && key === 'w')"), 'Cmd/Ctrl+W is captured before browser/app defaults');
  assert.ok(source.includes('function itemCanCloseWithAppShortcut(item)'), 'global close shortcut is scoped through a shared item guard');
  assert.ok(source.includes('if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);'), 'Cmd/Ctrl+W cannot close Preferences, YO!agent, Finder, Changes, or tmux tabs');
  assert.ok(source.includes("(key === 'backspace' || key === 'delete') && globalShortcutTargetAllowsAppAction(event.target)"), 'Backspace/Delete close fallback stays out of text editing contexts');
  assert.equal(source.includes('Ctrl/Cmd'), false, 'served UI strings do not show Ctrl/Cmd combined shortcuts');
  assert.ok(source.includes('showFileSaveConflictDialog'), 'editor saves route conflicts through the shared conflict dialog');
  assert.ok(source.includes('autoSaveFileEditor'), 'editor autosave is wired into the built client');
  assert.ok(source.includes('promptExternalChangeBeforeEditing'), 'editing a changed-on-disk buffer prompts before continuing');
  // A non-dirty editor reloads disk changes silently (the prompt is only for the genuine unsaved-edits conflict).
  assert.ok(/function promptExternalChangeBeforeEditing[\s\S]*?if \(!state\.dirty\) \{[\s\S]*?reloadOpenFileFromDisk\(path, \{force: true\}\)/.test(source), 'a non-dirty editor reloads external disk changes silently (no dialog)');
  const splitButtonIndex = source.indexOf('data-editor-mode="split"');
  const popoutPreviewButtonIndex = source.indexOf('file-editor-popout-preview-panel');
  const modeSeparatorIndex = source.indexOf('data-editor-toolbar-separator="mode"');
  assert.ok(splitButtonIndex > 0 && popoutPreviewButtonIndex > splitButtonIndex && popoutPreviewButtonIndex < modeSeparatorIndex, 'Preview pop-out sits directly in the editor mode button group after Split view');
  assert.ok(source.includes('editor.autosave_delay_seconds'), 'editor autosave delay is a persisted preference');
  assert.ok(source.includes('(commandPaletteIndex + 1) % commandPaletteItemsCache.length'), 'command palette arrow navigation wraps down');
  assert.ok(source.includes('item.splitRun'), 'command palette supports split-open actions');
  assert.equal(source.includes('function updateLinkedFilePreviewRings()'), false, 'old side-preview ring updater is removed');
  assert.equal(source.includes("previewPanel.classList.add('preview-linked')"), false, 'focused editors no longer mark paired side-preview panes');
  assert.equal(source.includes("editorPanel.classList.add('preview-linked')"), false, 'old pure-preview pane ring marker is removed');
  const focusStart = source.indexOf('function setFocusedPanelItem(');
  const focusEnd = source.indexOf('function clearPendingFileEditorFocusExcept(', focusStart);
  assert.ok(source.slice(focusStart, focusEnd).includes('const explicitFinderSync = isTmuxSession(item) || isFileEditorItem(item);'), 'explicit Finder sync is driven by clicked tmux panes and clicked editors');
  assert.ok(source.slice(focusStart, focusEnd).includes('if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item, {explicit: explicitFinderSync});'));
  const finderSyncStart = source.indexOf('function scheduleFileExplorerActiveTabSync(');
  const finderSyncEnd = source.indexOf('function cancelPendingFileExplorerActiveSync(', finderSyncStart);
  const finderSyncBody = source.slice(finderSyncStart, finderSyncEnd);
  assert.ok(finderSyncBody.includes("if (fileExplorerRootMode !== 'sync') return;"), 'Finder ignores terminal/editor clicks when Sync is not pressed');
  assert.ok(finderSyncBody.includes('const fileSyncPath = explicit && isFileEditorItem(preferredItem) ? fileItemPath(preferredItem) : \'\';'), 'explicit editor clicks become Finder Sync file targets');
  assert.ok(/const syncItem = fileSyncPath \? preferredItem : \(isTmuxSession\(preferredItem\) \? preferredItem : explicitSession\);[\s\S]*if \(!syncItem \|\| \(!fileSyncPath && syncItem !== explicitSession\)\) return/.test(finderSyncBody), 'Finder Sync accepts clicked editor files without requiring a tmux target');
  assert.ok(finderSyncBody.includes('fileExplorerSyncPlanForFile(fileSyncPath)'), 'Finder Sync can plan from an explicit editor file');
  assert.ok(finderSyncBody.includes('syncFileExplorerRootToActiveFile(fileSyncPath, {force: explicit})'), 'Finder Sync can move the root to a clicked editor file and explicit sync forces a re-apply');
  assert.ok(finderSyncBody.includes('(explicit || !fileExplorerSyncPlanAlreadyApplied(syncPlan))'), '#automatic Finder Sync skips a repeated already-applied plan');
  assert.equal(finderSyncBody.includes('syncFileExplorerToActiveTab(preferredItem'), false, 'fixed Finder mode never follows explicit tmux/editor clicks');
  assert.ok(source.includes('function fileExplorerSyncPlanKey(plan)'), '#Finder Sync has one shared sync-plan key helper');
  assert.ok(source.includes('function fileExplorerSyncPlanAlreadyApplied(plan)'), '#Finder Sync has one shared already-applied helper');
  assert.ok(source.includes('function markFileExplorerSyncPlanApplied(plan)'), '#Finder Sync marks successful plan application');
  const finderCandidatesStart = source.indexOf('function finderCandidateItems(');
  const finderCandidatesEnd = source.indexOf('function firstFinderPath(', finderCandidatesStart);
  const finderCandidatesBody = source.slice(finderCandidatesStart, finderCandidatesEnd);
  assert.equal(finderCandidatesBody.includes('focusedPanelItem'), false, 'Finder candidates do not read passive focusedPanelItem');
  assert.equal(finderCandidatesBody.includes('focusedTerminal'), false, 'Finder candidates do not read passive focusedTerminal');
  assert.equal(finderCandidatesBody.includes('lastFocusedTmuxSession'), false, 'Finder candidates do not read passive lastFocusedTmuxSession');
  assert.ok(source.includes('function setFileExplorerManualRootMode()'), 'manual Finder scope buttons leave Sync mode explicitly');
  assert.ok(source.includes('openFileExplorerManualRoot(expandQuickAccessPath(path));'), 'Finder quick-access opens are manual and disable Sync');
  assert.ok(source.includes('const opened = await openFileExplorerManualRoot(target);'), 'Finder typed path opens are manual and disable Sync');
  assert.ok(source.includes("if (entry.kind === 'dir') openFileExplorerManualRoot(fullPath);"), 'Finder root navigation by double-click is manual and disables Sync');
  assert.ok(source.includes('fileExplorerSyncManualCollapsedPaths'), 'Finder Sync tracks manually collapsed auto-expanded paths');
  assert.ok(/function fileExplorerSyncExpansionPaths\(plan\)[\s\S]*filter\(path => !fileExplorerSyncPathSuppressed\(path\)\)/.test(source), 'Finder Sync filters manually collapsed paths out of future auto-expansion');
  assert.ok(source.includes('function fileExplorerSyncExpansionTargets(root, affectedDirs = [], repoRoots = [])'), 'Finder Sync expansion targets are centralized');
  assert.ok(source.includes('const candidates = normalizedRepoRoots.length'), 'Finder Sync prefers repo-root expansion when repo metadata exists');
  assert.ok(source.includes('affectedDirs.map(path => firstChildPathUnderRoot(normalizedRoot, path))'), 'Finder Sync falls back to first-level affected directories, not every touched directory');
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
  assert.ok(api.keyboardShortcutsHtml().includes('Pin / unpin active tab'), 'shortcut overlay lists the Pin Tab chord');
  assert.ok(api.keyboardShortcutsHtml().includes('Ctrl+K Enter'), 'shortcut overlay renders the platform Pin Tab chord on PC/Linux');
  assert.ok(api.keyboardShortcutsHtml().includes('outside text'), 'shortcut overlay scopes the Backspace close-tab fallback');
  assert.ok(/async function copyTextToClipboard\(text\)[\s\S]*?if \(clipboard\?\.writeText\) \{[\s\S]*?try \{[\s\S]*?await clipboard\.writeText\(value\);[\s\S]*?\} catch/.test(source), 'clipboard copy falls back when navigator.clipboard exists but rejects');
  assert.ok(source.includes('function copyTerminalSelectionToClipboardEvent(session, term, event, container = null)'), 'terminal copy has a DOM copy-event fallback');
  assert.ok(source.includes("container.addEventListener('copy', event => {"), 'terminal container handles browser copy events');
  assert.ok(source.includes("event.clipboardData.setData('text/plain', selected);"), 'terminal copy-event fallback writes the xterm selection to clipboardData');
  assert.ok(source.includes('function copyTextToClipboardViaCopyEvent(text)'), 'terminal shortcut copy has a synchronous copy-event clipboard path');
  // the sync-then-async clipboard chain lives in ONE shared parent (writeTerminalTextToClipboard)
  // used by both the shortcut copy and the OSC 52 bridge.
  assert.ok(/function writeTerminalTextToClipboard\(text, label = 'copied'\)[\s\S]*?copyTextToClipboardViaCopyEvent\(text\)[\s\S]*?copyTextToClipboard\(text\)/.test(source), 'terminal clipboard writes use the synchronous copy-event path before async clipboard fallback (shared parent)');
  assert.ok(/function copyTerminalSelectionFromShortcut\(session, term, options = \{\}, container = null\)[\s\S]*?writeTerminalTextToClipboard\(text/.test(source), 'terminal shortcut copy routes through the shared clipboard-write chain');
  assert.ok(source.includes('async function copyTmuxSelectionToClipboard(session)'), 'terminal tmux copy-mode selection can bridge to the browser clipboard');
  assert.ok(source.includes("apiFetchJson(`/api/tmux-copy-selection?session=${encodeURIComponent(session)}`, {method: 'POST'})"), 'tmux copy bridge calls the authenticated tmux-copy endpoint');
  assert.ok(source.includes("new ClipboardItem({'text/plain': textBlob})"), 'tmux copy bridge starts deferred clipboard writes during the shortcut activation');
  assert.ok(/function terminalSelectedText\(term, container = null\)[\s\S]*browserSelectionTextInside\(container\)/.test(source), 'terminal copy shortcuts prefer visible browser selection before tmux copy-mode fallback');
  assert.ok(source.includes("container?.addEventListener?.('keydown'"), 'terminal copy guard runs in DOM capture before xterm/TUI handlers');
  assert.ok(/function handleFocusedTerminalCopyShortcut\(event\)[\s\S]*handleTerminalCopyShortcutKeydown\(session, item\.term, item\.container, event\)[\s\S]*stopImmediatePropagation/.test(source), 'focused-terminal copy guard runs at window capture before terminal internals');
  assert.ok(/function handleGlobalShortcutKeydown\(event\) \{[\s\S]*?if \(handleFocusedTerminalCopyShortcut\(event\)\) return/.test(source), 'global shortcuts first give focused terminal copy handling a chance');
  assert.ok(source.includes('const isTmuxCopyShortcut = event.altKey'), 'tmux copy-mode bridge is on a separate terminal shortcut');
  assert.ok(source.includes("appendContextMenuButton(menu, t('terminal.copyTmuxSelection'), () => copyTmuxSelectionToClipboard(session), closeTerminalContextMenu)"), 'terminal context menu exposes explicit tmux copy');
  assert.ok(/if \(!selected\) \{[\s\S]*?if \(isCmdC\) \{[\s\S]*?event\.preventDefault\(\);[\s\S]*?statusEl\.textContent = isMacPlatform\(\)[\s\S]*?nothing selected — Option-drag selects while Claude\/tmux owns the mouse[\s\S]*?return true;[\s\S]*?return false; \/\/ no selection: let Ctrl-C through as SIGINT/.test(source), 'Cmd-C without browser selection is swallowed (with a how-to-select hint) while Ctrl-C still falls through as SIGINT');
  assert.equal(source.includes('else copyTmuxSelectionToClipboard(session);'), false, 'Cmd-C no longer falls back to tmux copy-mode');
  assert.ok(api.keyboardShortcutsHtml().includes('Copy tmux selection'), 'keyboard shortcuts list includes the explicit tmux copy shortcut');
  const terminalCopyApi = loadYolomux('?platform=mac', ['1'], 'https:', 'MacIntel');
  const fetchCalls = [];
  terminalCopyApi.setFetchForTest((url, options = {}) => {
    fetchCalls.push({url: String(url), method: options.method || 'GET'});
    return new Promise(() => {});
  });
  let terminalSelection = '';
  let copyShortcutHandler = null;
  let clearSelectionCount = 0;
  terminalCopyApi.installTerminalCopyShortcutForTest('1', {
    getSelection: () => terminalSelection,
    clearSelection: () => { clearSelectionCount += 1; },
    attachCustomKeyEventHandler(handler) { copyShortcutHandler = handler; },
  });
  assert.equal(typeof copyShortcutHandler, 'function', 'terminal copy shortcut installs an xterm custom key handler');
  let prevented = 0;
  const cmdCResult = copyShortcutHandler({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: true,
    ctrlKey: false,
    altKey: false,
    preventDefault() { prevented += 1; },
  });
  assert.equal(cmdCResult, false, 'Mac Cmd-C with no browser/xterm selection is swallowed instead of reaching the PTY');
  assert.equal(prevented, 1, 'Mac Cmd-C with no browser/xterm selection prevents the terminal default');
  assert.deepStrictEqual(fetchCalls, [], 'Mac Cmd-C with no browser/xterm selection does not ask tmux for copy-mode text');
  let domKeydownHandler = null;
  let domKeydownOptions = null;
  let domCopyShortcutHandler = null;
  terminalCopyApi.installTerminalCopyShortcutForTest('1', {
    getSelection: () => '',
    clearSelection: () => { clearSelectionCount += 1; },
    attachCustomKeyEventHandler(handler) { domCopyShortcutHandler = handler; },
  }, {
    addEventListener(type, handler, options) {
      if (type === 'keydown') {
        domKeydownHandler = handler;
        domKeydownOptions = options;
      }
    },
  });
  assert.equal(typeof domCopyShortcutHandler, 'function', 'terminal DOM guard install keeps xterm handler available');
  assert.equal(domKeydownOptions?.capture, true, 'terminal DOM copy guard is installed in capture phase');
  fetchCalls.length = 0;
  prevented = 0;
  let stopped = 0;
  let stoppedImmediate = 0;
  domKeydownHandler({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: true,
    ctrlKey: false,
    altKey: false,
    preventDefault() { prevented += 1; },
    stopPropagation() { stopped += 1; },
    stopImmediatePropagation() { stoppedImmediate += 1; },
  });
  assert.equal(prevented, 1, 'terminal DOM copy guard prevents Cmd-C with no browser/xterm selection');
  assert.equal(stopped, 1, 'terminal DOM copy guard stops propagation before Claude/xterm');
  assert.equal(stoppedImmediate, 1, 'terminal DOM copy guard stops sibling handlers before Claude/xterm');
  assert.deepStrictEqual(fetchCalls, [], 'terminal DOM copy guard does not ask tmux for normal Cmd-C');
  terminalCopyApi.registerTerminalForTest('1', {
    getSelection: () => '',
    clearSelection: () => { clearSelectionCount += 1; },
  });
  terminalCopyApi.setFocusedTerminal('1');
  fetchCalls.length = 0;
  prevented = 0;
  stopped = 0;
  stoppedImmediate = 0;
  const focusedGuardResult = terminalCopyApi.handleFocusedTerminalCopyShortcutForTest({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: true,
    ctrlKey: false,
    altKey: false,
    preventDefault() { prevented += 1; },
    stopPropagation() { stopped += 1; },
    stopImmediatePropagation() { stoppedImmediate += 1; },
  });
  assert.equal(focusedGuardResult, true, 'focused terminal copy guard claims Cmd-C before Claude/xterm');
  assert.equal(prevented, 1, 'focused terminal window guard prevents Cmd-C before Claude/xterm');
  assert.equal(stopped, 1, 'focused terminal window guard stops propagation before target handlers');
  assert.equal(stoppedImmediate, 1, 'focused terminal window guard stops sibling window handlers');
  assert.deepStrictEqual(fetchCalls, [], 'focused terminal window guard does not ask tmux for normal Cmd-C');
  prevented = 0;
  const tmuxCopyShortcutResult = copyShortcutHandler({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: true,
    ctrlKey: false,
    altKey: true,
    shiftKey: false,
    preventDefault() { prevented += 1; },
  });
  assert.equal(tmuxCopyShortcutResult, false, 'Mac Cmd-Option-C is handled as explicit tmux copy');
  assert.equal(prevented, 1, 'Mac Cmd-Option-C prevents the terminal default');
  assert.deepStrictEqual(fetchCalls, [{url: '/api/tmux-copy-selection?session=1', method: 'POST'}], 'Mac Cmd-Option-C asks tmux for copy-mode text');
  fetchCalls.length = 0;
  prevented = 0;
  clearSelectionCount = 0;
  terminalCopyApi.clearClipboardTextForTest();
  terminalSelection = 'selected xterm text';
  const selectedCmdCResult = copyShortcutHandler({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: true,
    ctrlKey: false,
    altKey: false,
    preventDefault() { prevented += 1; },
  });
  assert.equal(selectedCmdCResult, false, 'Mac Cmd-C with xterm selection is handled by browser copy');
  assert.equal(prevented, 1, 'Mac Cmd-C with xterm selection prevents the terminal default');
  assert.deepStrictEqual(fetchCalls, [], 'Mac Cmd-C with xterm selection does not ask tmux for copy-mode text');
  assert.equal(terminalCopyApi.clipboardTextForTest(), 'selected xterm text', 'Mac Cmd-C with xterm selection writes selected xterm text to clipboardData');
  assert.equal(clearSelectionCount, 1, 'Mac Cmd-C with xterm selection clears xterm selection after copying');
  const insideSelectionNode = {};
  const terminalContainer = {contains: node => node === insideSelectionNode};
  terminalSelection = '';
  terminalCopyApi.setBrowserSelectionForTest('selected browser text', insideSelectionNode);
  terminalCopyApi.installTerminalCopyShortcutForTest('1', {
    getSelection: () => terminalSelection,
    clearSelection: () => { clearSelectionCount += 1; },
    attachCustomKeyEventHandler(handler) { copyShortcutHandler = handler; },
  }, terminalContainer);
  fetchCalls.length = 0;
  prevented = 0;
  clearSelectionCount = 0;
  terminalCopyApi.clearClipboardTextForTest();
  const browserSelectedCmdCResult = copyShortcutHandler({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: true,
    ctrlKey: false,
    altKey: false,
    preventDefault() { prevented += 1; },
  });
  terminalCopyApi.clearBrowserSelectionForTest();
  assert.equal(browserSelectedCmdCResult, false, 'Mac Cmd-C with browser selection inside the terminal is handled by browser copy');
  assert.equal(prevented, 1, 'Mac Cmd-C with browser selection prevents the terminal default');
  assert.deepStrictEqual(fetchCalls, [], 'Mac Cmd-C with browser selection does not ask tmux for copy-mode text');
  assert.equal(terminalCopyApi.clipboardTextForTest(), 'selected browser text', 'Mac Cmd-C with browser selection writes browser-selected terminal text to clipboardData');
  assert.equal(clearSelectionCount, 1, 'Mac Cmd-C with browser selection clears xterm selection state after copying');
  fetchCalls.length = 0;
  prevented = 0;
  clearSelectionCount = 0;
  terminalSelection = '';
  const ctrlCResult = copyShortcutHandler({
    type: 'keydown',
    code: 'KeyC',
    key: 'c',
    metaKey: false,
    ctrlKey: true,
    altKey: false,
    preventDefault() { prevented += 1; },
  });
  assert.equal(ctrlCResult, true, 'Ctrl-C with no xterm selection still reaches the PTY as SIGINT');
  assert.equal(prevented, 0, 'Ctrl-C with no selection does not prevent the terminal default');
  assert.deepStrictEqual(fetchCalls, [], 'Ctrl-C with no selection does not hit the tmux copy bridge');
  assert.equal(clearSelectionCount, 0, 'Ctrl-C fallthrough does not mutate xterm selection state');
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
  assert.ok(api.searchRankWeights.domainPrior.files.file > api.searchRankWeights.domainPrior.files.pane, 'Cmd-P domain weights rank files before panes for comparable matches');
  assert.ok(api.searchRankWeights.domainPrior.command.pane > api.searchRankWeights.domainPrior.command.file, 'Shift-Cmd-P domain weights rank panes before files for comparable matches');
  assert.ok(
    api.commandPaletteItemScore({group: 'Tabs', label: 'YO!agent', detail: '', searchFields: ['YO!agent']}, 'yoagent') >
      api.commandPaletteItemScore({group: 'Tabs', label: '1', detail: 'y o agent buried in details', searchFields: ['1', 'y o agent buried in details']}, 'yoagent'),
    'command palette ranks YO!agent first for punctuation-insensitive prefix queries'
  );
  const fileCandidate = (label, options = {}) => {
    const path = options.path || `/repo/current/${label}`;
    return {category: 'file', group: 'Files', label, detail: path, path, key: `file:${path}`, mtime: options.mtime || 0, searchFields: [label, path]};
  };
  const paneCandidate = (label, options = {}) => ({category: 'pane', group: 'Tabs', label, detail: options.detail || '', key: `pane:${label}`, mtime: options.mtime || 0, searchFields: [label, options.detail || '']});
  const rankValues = (surface, query, candidates, options = {}) => api.commandPaletteRankItems(candidates, query, {
    surface,
    nowSeconds: options.nowSeconds || 300,
    focusedRepoRoots: options.focusedRepoRoots || ['/repo/current'],
  }).map(options.value || (item => item.label));
  const sixCandidates = [
    fileCandidate('6.md'),
    fileCandidate('6-plan.md'),
    fileCandidate('a6.txt'),
    paneCandidate('6 Fix same-strip'),
  ];
  const fileHeavySixCandidates = [
    fileCandidate('6-00.md'),
    fileCandidate('6-01.md'),
    fileCandidate('6-02.md'),
    fileCandidate('6-03.md'),
    fileCandidate('6-04.md'),
    fileCandidate('6-05.md'),
    paneCandidate('6 Fix same-strip'),
    fileCandidate('a6.txt'),
  ];
  const claudeCandidates = [paneCandidate('claude'), fileCandidate('claude_notes.md')];
  const rankingCases = [
    {
      name: 'Cmd-P ranks anchored files first, then anchored pane, then non-anchored file',
      surface: 'files',
      query: '6',
      candidates: sixCandidates,
      expected: ['6.md', '6-plan.md', '6 Fix same-strip', 'a6.txt'],
    },
    {
      name: 'Cmd-P keeps a matching pane mixed into a file-heavy first screen',
      surface: 'files',
      query: '6',
      candidates: fileHeavySixCandidates,
      expected: ['6-00.md', '6-01.md', '6 Fix same-strip', '6-02.md', '6-03.md'],
      limit: 5,
    },
    {
      name: 'Shift-Cmd-P ranks the anchored pane before anchored files',
      surface: 'command',
      query: '6',
      candidates: sixCandidates,
      expected: ['6 Fix same-strip', '6.md', '6-plan.md', 'a6.txt'],
    },
    {
      name: 'contiguous matches beat scattered subsequences',
      surface: 'files',
      query: '123',
      candidates: [fileCandidate('hello 123'), fileCandidate('1 a 2 b 3 c')],
      expected: ['hello 123', '1 a 2 b 3 c'],
    },
    {
      name: 'newer mtime wins among comparable file matches',
      surface: 'files',
      query: '123',
      candidates: [fileCandidate('x 123 y', {mtime: 200}), fileCandidate('z 123 y', {mtime: 100})],
      expected: ['x 123 y', 'z 123 y'],
    },
    {
      name: 'focused repo affinity wins only among comparable matches',
      surface: 'files',
      query: 'target',
      candidates: [
        fileCandidate('target.md', {path: '/other/repo/target.md', mtime: 100}),
        fileCandidate('target.md', {path: '/repo/current/target.md', mtime: 100}),
      ],
      expected: ['/repo/current/target.md', '/other/repo/target.md'],
      value: item => item.path,
    },
    {
      name: 'anchored old match beats non-anchored new match',
      surface: 'files',
      query: 'ab',
      candidates: [fileCandidate('ab.md', {mtime: 1}), fileCandidate('x-ab.md', {mtime: 299})],
      expected: ['ab.md', 'x-ab.md'],
    },
    {
      name: 'repo affinity never rescues a weaker scattered match',
      surface: 'files',
      query: '123',
      candidates: [
        fileCandidate('hello 123', {path: '/other/repo/hello-123.md'}),
        fileCandidate('1 a 2 b 3 c', {path: '/repo/current/scattered.md'}),
      ],
      expected: ['hello 123', '1 a 2 b 3 c'],
    },
    {
      name: 'word-start anchored match beats mid-word match',
      surface: 'files',
      query: 'do',
      candidates: [fileCandidate('docs/'), fileCandidate('weirdo')],
      expected: ['docs/', 'weirdo'],
    },
    {
      name: 'Shift-Cmd-P ranks matching panes before files',
      surface: 'command',
      query: 'claude',
      candidates: claudeCandidates,
      expected: ['claude', 'claude_notes.md'],
    },
    {
      name: 'Cmd-P ranks matching files before panes',
      surface: 'files',
      query: 'claude',
      candidates: claudeCandidates,
      expected: ['claude_notes.md', 'claude'],
    },
    {
      name: 'empty Cmd-P file results sort by recency',
      surface: 'files',
      query: '',
      candidates: [fileCandidate('old.md', {mtime: 100}), fileCandidate('new.md', {mtime: 200})],
      expected: ['new.md', 'old.md'],
    },
    {
      name: 'empty Shift-Cmd-P pane results sort by recency',
      surface: 'command',
      query: '',
      candidates: [paneCandidate('old pane', {mtime: 100}), paneCandidate('new pane', {mtime: 200})],
      expected: ['new pane', 'old pane'],
    },
  ];
  for (const row of rankingCases) {
    const actual = [...rankValues(row.surface, row.query, row.candidates, row)];
    assert.deepStrictEqual(row.limit ? actual.slice(0, row.limit) : actual, row.expected, row.name);
  }
  // #40 / YO!info, Finder, and Preferences are the standalone virtual tabs; YO!agent is now a
  // sub-tab of the merged YO!info pane, so it is NOT a Tabs entry.
  const paletteItems = api.commandPaletteCommandItems();
  const expectedVirtualLabels = [api.infoItemId, api.fileExplorerItemId, api.prefsItemId].map(api.itemLabel);
  const paletteVirtualLabels = paletteItems.filter(item => item.group === 'Tabs' && expectedVirtualLabels.includes(item.label));
  assert.equal(paletteVirtualLabels.length, expectedVirtualLabels.length, 'command palette lists each virtual tab once');
  assert.equal(expectedVirtualLabels.every(label => paletteVirtualLabels.some(item => item.label === label)), true, 'command palette includes all virtual tabs');
  assert.equal(paletteVirtualLabels.every(item => item.group === 'Tabs'), true, 'virtual tab palette entries come from the Tabs group, not duplicate menu commands');
  assert.equal(paletteItems.some(item => item.targetItem === '__changes__'), false, 'retired standalone Differ is absent from the command palette');
  // YO!agent survives as a File-menu command that opens the merged pane on its sub-tab.
  assert.ok(paletteItems.some(item => item.group === 'Menu' && /YO!agent$/.test(item.label)), 'command palette offers a YO!agent command (opens the merged pane on its sub-tab)');
  assert.equal(paletteItems.some(item => item.group === 'Tabs' && item.label === 'YO!agent'), false, 'YO!agent is not a standalone palette tab anymore');
  const finderPaletteItem = paletteItems.find(item => item.targetItem === api.fileExplorerItemId);
  assert.ok(finderPaletteItem, 'command palette has a Finder/File Explorer tab row');
  assert.ok(
    api.commandPaletteItemScore(finderPaletteItem, 'File') > api.commandPaletteItemScore(api.fileQuickOpenItem('/repo/app/File.md'), 'File'),
    'typing File in the command palette promotes Finder/File Explorer above ordinary file matches'
  );
  api.setFileQuickOpenCandidatesForTest('/repo/app', [
    {name: 'helloXandYyy.py', path: '/repo/app/src/helloXandYyy.py', relative_path: 'src/helloXandYyy.py'},
  ]);
  const quickItem = api.fileQuickOpenItems().find(item => item.label === 'helloXandYyy.py');
  assert.ok(quickItem, 'file quick-open uses the same command-palette item shell');
  assert.equal(api.commandPaletteMatches(quickItem, 'xy'), true, 'file quick-open uses fuzzy matching');
  const doitApi = loadYolomux('', ['1']);
  doitApi.setFileQuickOpenCandidatesForTest('/repo/yolomux', [
    {name: 'websocket.py', path: '/repo/yolomux/yolomux_lib/websocket.py', relative_path: 'yolomux_lib/websocket.py', kind: 'file'},
    {name: 'DOIT.53.md', path: '/repo/yolomux/DOIT.53.md', relative_path: 'DOIT.53.md', kind: 'file'},
    {name: 'DOIT.parser-performance-v2-audit.md', path: '/repo/yolomux/frontend-crates/DOIT.parser-performance-v2-audit.md', relative_path: 'frontend-crates/DOIT.parser-performance-v2-audit.md', kind: 'file'},
    {name: 'DOIT.51.md', path: '/repo/yolomux/DOIT.51.md', relative_path: 'DOIT.51.md', kind: 'file'},
    {name: 'events.py', path: '/repo/yolomux/yolomux_lib/events.py', relative_path: 'yolomux_lib/events.py', kind: 'file'},
  ]);
  doitApi.setCommandPaletteStateForTest('files', 'DOIT:');
  assert.equal(doitApi.fileQuickOpenSearchText('DOIT:'), 'DOIT', 'file quick-open ignores a trailing colon with no line number');
  assert.equal(doitApi.commandPaletteSearchQuery(), 'DOIT', 'command palette scores the normalized file query');
  const doitRows = doitApi.commandPaletteItems()
    .filter(item => item.category === 'file')
    .map((item, index) => ({...item, index, score: doitApi.commandPaletteItemScore(item, 'DOIT')}))
    .filter(item => Number.isFinite(item.score))
    .sort((left, right) => right.score - left.score || left.label.localeCompare(right.label) || left.index - right.index)
    .map(item => item.label);
  assert.deepStrictEqual(canonical(doitRows.slice(0, 2)), ['DOIT.51.md', 'DOIT.53.md'], 'DOIT-numbered files stay contiguous for a DOIT: query');
  doitApi.setFileQuickOpenCandidatesForTest('/repo/yolomux', [
    {name: 'DOIT.53.md', path: '/repo/yolomux/DOIT.53.md', relative_path: 'DOIT.53.md', kind: 'file'},
    {name: 'report.html', path: '/home/test/dynamo/commits/logs/BA01C8.51e42e397/report.html', relative_path: 'commits/logs/BA01C8.51e42e397/report.html', indexed_root: '/home/test/dynamo', kind: 'file'},
  ]);
  doitApi.setCommandPaletteStateForTest('files', 'DOIT.53');
  const exactDoitRows = doitApi.commandPaletteItems()
    .filter(item => item.category === 'file')
    .map((item, index) => ({...item, index, score: doitApi.commandPaletteItemScore(item, 'DOIT.53')}))
    .filter(item => Number.isFinite(item.score))
    .sort((left, right) => right.score - left.score || left.index - right.index);
  assert.equal(exactDoitRows[0]?.label, 'DOIT.53.md', 'S15: exact local DOIT.53.md stays first for a dotted filename query');
  assert.equal(exactDoitRows.some(item => item.label === 'report.html'), false, 'S15: external indexed full-path-only fuzzy noise is hidden for dotted filename queries');
  assert.deepStrictEqual(
    canonical(api.cursorStyleFileReference('/home/keivenc/yolomux.dev1/20260609-001.png', {imageIndex: 1})),
    {label: '[Image #1]', detail: "'/home/keivenc/yolomux.dev1/20260609-001.png'"},
    'file quick-open can render image hits in Popular IDE-style reference form'
  );
  api.setFileQuickOpenCandidatesForTest('/home/keivenc/yolomux.dev1', [
    {name: '20260609-001.png', path: '/home/keivenc/yolomux.dev1/20260609-001.png', relative_path: '20260609-001.png', kind: 'file'},
    {name: '20260609-002.png', path: '/home/keivenc/yolomux.dev1/20260609-002.png', relative_path: '20260609-002.png', kind: 'file'},
  ]);
  const imageItems = api.fileQuickOpenItems().filter(item => item.key.includes('20260609-00'));
  assert.deepStrictEqual(canonical(imageItems.map(item => item.label)), ['[Image #1]', '[Image #2]'], 'Search image results use Popular IDE-style image numbering');
  assert.equal(imageItems[0].detail, "'/home/keivenc/yolomux.dev1/20260609-001.png'", 'Search image result details show the quoted absolute path');
  // C15 follow-up: cmd-P path mode offers a pinned "Open folder in Finder" row (Enter opens the typed
  // directory), while a subfolder entry descends and a file entry opens.
  api.setFileQuickOpenCandidatesForTest('/repo/app', [
    {name: 'dynamo', path: '/repo/app/dynamo', relative_path: 'dynamo', kind: 'dir'},
    {name: 'build.sh', path: '/repo/app/build.sh', relative_path: 'build.sh', kind: 'file'},
  ]);
  api.setCommandPaletteStateForTest('files', '/repo/app/');
  const pathItems = api.fileQuickOpenItems();
  const openFolder = pathItems.find(item => item.pinTop);
  assert.ok(openFolder, 'C15: path mode offers a pinned Open-folder row');
  assert.equal(openFolder.detail, '/repo/app', 'C15: the Open-folder row targets the listed directory');
  assert.equal(typeof openFolder.run, 'function', 'C15: the Open-folder row opens the directory');
  const dirEntry = pathItems.find(item => item.label === 'dynamo/');
  assert.ok(dirEntry && !dirEntry.pinTop, 'C15: a subfolder entry is a normal (descend) row, not pinned');
  // Bug fix: path-mode list entries (no indexed_root) must group under "Files", NOT "Indexed /" — the
  // empty indexed_root used to normalize to '/' and mislabel every directory row as indexed.
  assert.equal(dirEntry.group, 'Files', 'path-mode directory rows are grouped under Files, not mislabeled Indexed');
  assert.equal(pathItems.find(item => item.label === 'build.sh')?.group, 'Files', 'path-mode file rows are grouped under Files');
  // Typing an exact subdir name targets that subfolder for opening.
  api.setCommandPaletteStateForTest('files', '/repo/app/dynamo');
  assert.equal(api.fileQuickOpenItems().find(item => item.pinTop).detail, '/repo/app/dynamo', 'C15: an exact subdir name targets that subfolder');
  api.setCommandPaletteStateForTest('files', '');
  assert.equal(api.fileQuickOpenRootForSearch(), '/home/test/yolomux.dev', 'file quick-open defaults to the active repo root when no session cwd is known');
  api.setTranscriptInfoForTest('1', {project: {git: {root: '/repo/workspace'}}, selected_pane: {current_path: '/repo/workspace/src'}});
  api.setFocusedPanelItem('1');
  assert.equal(api.fileQuickOpenRootForSearch(), '/repo/workspace', 'file quick-open searches the workspace root when tmux is inside a repo');
  const fileRootApi = loadYolomux('', ['1']);
  fileRootApi.setTranscriptInfoForTest('1', {project: {git: {root: '/repo/workspace'}}, selected_pane: {current_path: '/repo/workspace/src'}});
  fileRootApi.setFocusedPanelItem('1');
  const activeMdPath = '/home/test/yolomux.dev/DOIT.54.md';
  const activeMdItem = fileRootApi.fileEditorItemFor(activeMdPath);
  const activeFileSlots = fileRootApi.emptyLayoutSlots();
  activeFileSlots.left = fileRootApi.paneStateWithTabs([activeMdItem], activeMdItem);
  fileRootApi.setOpenFileStateForTest(activeMdPath, {kind: 'text', gitRoot: '/home/test/yolomux.dev'});
  fileRootApi.setLayoutSlotsForTest(activeFileSlots);
  fileRootApi.setFocusedPanelItem(activeMdItem);
  assert.equal(fileRootApi.fileQuickOpenRootForSearch(), '/home/test/yolomux.dev', 'file quick-open uses the focused file editor repo before the last tmux cwd');
  assert.equal(fileRootApi.fileQuickOpenRootForFile('/home/test/yolomux.dev/src/file.js'), '/home/test/yolomux.dev/src', 'unloaded files fall back to their containing directory');
  api.setFileExplorerIndexedDirsForTest(['/repo/tools', '/repo/tools/src', '/repo/other']);
  assert.equal(api.fileExplorerDirectoryIsIndexed('/repo/tools'), true, 'Finder indexed directories are tracked by exact path');
  assert.equal(api.fileExplorerDirectoryIsIndexed('/repo/tools/src'), false, 'Finder compacts redundant child index marks under an indexed ancestor');
  assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/repo/workspace')), ['/repo/workspace', '/repo/other', '/repo/tools'], 'file quick-open adds indexed Finder directories and compacts nested search roots');
  assert.equal(api.fileQuickOpenScopeLabel('/repo/workspace'), '/repo/workspace + 2 indexed', 'file quick-open placeholder summarizes indexed search scope');
  api.setFileQuickOpenCandidatesForTest('/repo/workspace', [
    {name: 'target.md', path: '/home/test/dynamo/notes/target.md', relative_path: 'target.md', indexed_root: '/home/test/dynamo/notes'},
    {name: 'target.md', path: '/repo/workspace/docs/target.md', relative_path: 'docs/target.md', indexed_root: '/repo/workspace'},
  ]);
  const priorityItems = api.fileQuickOpenItems().filter(item => item.label === 'target.md');
  const contextItem = priorityItems.find(item => item.key === 'file:/repo/workspace/docs/target.md');
  const indexedItem = priorityItems.find(item => item.key === 'file:/home/test/dynamo/notes/target.md');
  assert.ok(contextItem && indexedItem, 'file quick-open renders both context and external indexed matches');
  assert.ok(api.commandPaletteItemScore(contextItem, 'target') > api.commandPaletteItemScore(indexedItem, 'target'), 'file quick-open prioritizes the active context over external indexed roots');
  api.setFileExplorerIndexedDirsForTest(['/home/test/dynamo']);
  assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test')), ['/home/test/dynamo'], 'an indexed child under the default root replaces the broad live parent search');
  assert.equal(api.fileQuickOpenRootMatchesPathAlias('/home/test/yolomux.dev', 'yolo/TODO.md'), true, 'bare yolo/... queries match the YOLOmux repo basename as a narrow root alias');
  assert.deepStrictEqual(canonical(api.fileQuickOpenExtraRootsForSearchQuery('yolo/TODO.md')), ['/home/test/yolomux.dev'], 'bare yolo/... queries add the app repo root without adding all of home');
  assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test', 'yolo/TODO.md')), ['/home/test/dynamo', '/home/test/yolomux.dev'], 'yolo/TODO.md searches the YOLOmux checkout even when home is narrowed to indexed Dynamo');
  assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test', 'src/file.js')), ['/home/test/dynamo'], 'unrelated slash queries do not add the YOLOmux root');
  const quickTargetApi = loadYolomux('', ['1', '2']);
  const quickOpenTargetSlots = quickTargetApi.emptyLayoutSlots();
  quickOpenTargetSlots.left = quickTargetApi.paneStateWithTabs(['1'], '1');
  quickOpenTargetSlots.slot1 = quickTargetApi.paneStateWithTabs(['2'], '2');
  quickOpenTargetSlots[quickTargetApi.layoutTreeKey] = quickTargetApi.splitNode('row', quickTargetApi.leafNode('left'), quickTargetApi.leafNode('slot1'), 50);
  quickTargetApi.setLayoutSlotsForTest(quickOpenTargetSlots);
  quickTargetApi.setFocusedPanelItem('2');
  assert.equal(quickTargetApi.fileQuickOpenTargetSlot(), 'slot1', 'Cmd-P normal file opens target the currently active pane, not the first pane');
  assert.equal(quickTargetApi.slotForTabActivation(quickTargetApi.prefsItemId), 'slot1', 'DOIT.56 N2: Preferences opens in the focused pane, not the first/largest pane');
  assert.equal(quickTargetApi.slotForTabActivation(quickTargetApi.infoItemId), 'slot1', 'DOIT.56 N2: YO!info opens in the focused pane, not the first/largest pane');
  const editorTargetItem = fileRootApi.fileEditorItemFor('/home/test/yolomux.dev/DOIT.53.md');
  fileRootApi.setOpenFileStateForTest('/home/test/yolomux.dev/DOIT.53.md', {kind: 'text', gitRoot: '/home/test/yolomux.dev'});
  fileRootApi.registerFileEditorLayoutItemForTest('/home/test/yolomux.dev/DOIT.53.md');
  const editorTargetSlots = fileRootApi.emptyLayoutSlots();
  editorTargetSlots.left = fileRootApi.paneStateWithTabs(['1'], '1');
  editorTargetSlots.slot1 = fileRootApi.paneStateWithTabs([editorTargetItem], editorTargetItem);
  editorTargetSlots[fileRootApi.layoutTreeKey] = fileRootApi.splitNode('row', fileRootApi.leafNode('left'), fileRootApi.leafNode('slot1'), 50);
  fileRootApi.setLayoutSlotsForTest(editorTargetSlots);
  fileRootApi.setFocusedPanelItem(editorTargetItem);
  assert.equal(fileRootApi.fileQuickOpenTargetSlot(), 'slot1', 'Cmd-P targets the active file-editor pane as well as terminal panes');
  const finderOnlyTargetSlots = fileRootApi.emptyLayoutSlots();
  finderOnlyTargetSlots.left = fileRootApi.paneStateWithTabs([fileRootApi.fileExplorerItemId], fileRootApi.fileExplorerItemId);
  finderOnlyTargetSlots.slot1 = fileRootApi.paneStateWithTabs(['1'], '1');
  finderOnlyTargetSlots[fileRootApi.layoutTreeKey] = fileRootApi.splitNode('row', fileRootApi.leafNode('left'), fileRootApi.leafNode('slot1'), 22);
  fileRootApi.setLayoutSlotsForTest(finderOnlyTargetSlots);
  fileRootApi.setFocusedPanelItem(fileRootApi.fileExplorerItemId);
  assert.equal(fileRootApi.fileQuickOpenTargetSlot(), null, 'Cmd-P does not target the reserved Finder pane for normal file opens');
  assert.equal(fileRootApi.slotForTabActivation(fileRootApi.prefsItemId), 'slot1', 'DOIT.56 N2: opening a virtual tab while Finder is focused falls back outside the reserved Finder pane');
  // #31: the Finder indexed badge reflects the cached build status without writing a long label into
  // the one-letter status column. Date/Ago mode hides the badge entirely because the date slot owns
  // the right side of the row.
  api.setFileExplorerTreeDateModeForTest('none');
  api.setFileExplorerIndexStatusForTest('/home/test/dynamo', 'building');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), '…', '#31: a building index renders a compact building badge');
  api.setFileExplorerIndexStatusForTest('/home/test/dynamo', 'ready');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), 'I', '#31: a ready index renders a compact indexed badge');
  api.setFileExplorerTreeDateModeForTest('date');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), '', '#31: Date mode hides the indexed badge instead of stacking it next to the date');
  api.setFileExplorerTreeDateModeForTest('relative');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), '', '#31: Ago mode hides the indexed badge instead of stacking it next to the age');
  api.setFileExplorerTreeDateModeForTest('none');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/not-indexed'), '', '#31: a non-indexed directory renders no badge');
  const checkboxInput = {getAttribute: name => name === 'type' ? 'checkbox' : ''};
  const textInput = {getAttribute: name => name === 'type' ? 'text' : ''};
  assert.equal(api.markdownPreviewInputAllowed(checkboxInput), true, 'GFM task-list checkbox inputs survive markdown sanitization');
  assert.equal(api.markdownPreviewInputAllowed(textInput), false, 'non-checkbox inputs stay blocked in markdown preview');
  const markdownSource = fs.readFileSync('static/yolomux.js', 'utf8');
  const blockedTagsSource = markdownSource.slice(markdownSource.indexOf('const MARKDOWN_PREVIEW_BLOCKED_TAGS'), markdownSource.indexOf('const MARKDOWN_PREVIEW_URL_ATTRS'));
  assert.ok(!blockedTagsSource.includes("'input'"), 'markdown sanitizer no longer blocks safe task-list checkboxes at the tag level');
  assert.ok(markdownSource.includes("const MARKDOWN_PREVIEW_INPUT_ATTRS = new Set(['type', 'checked', 'disabled', 'aria-label', 'class'])"), 'markdown task checkbox sanitizer has an input attribute allow-list');
  const cssSource = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(cssSource.includes('.markdown-body ul.contains-task-list'), 'markdown task lists remove the regular bullet gutter');
  assert.ok(cssSource.includes('.markdown-body li.task-list-item > input[type="checkbox"]'), 'markdown task-list checkbox alignment is pinned');
  // Descendants of an indexed root stay visually quiet; the root's compact indexed badge is the only repeated signal.
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo/lib'), '', 'indexed-root descendants do not show a noisy repeated badge');
  assert.equal(api.fileExplorerIndexBadgeText('/home/test/elsewhere'), '', 'C11: an unrelated directory shows no badge');
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
  assert.ok(contextMenu.children[0].innerHTML.includes('Pin Tab'), 'tab context menu starts with Pin Tab');
  assert.ok(contextMenu.children[0].innerHTML.includes('app-menu-ui-icon-pin'), 'Pin Tab context menu row has the shared pin icon');
  assert.equal(contextMenu.children[0].getAttribute('aria-label'), 'Pin Tab', 'Pin Tab context menu row has an accessible label');
  assert.deepStrictEqual(canonical(Array.from(contextMenu.children).map(child => child.textContent).filter(Boolean)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Transcript for session '1'", "YO!summary for session '1'", "Event log for session '1'", "Kill tmux session '1'"]);
  assert.equal(contextMenu.children.some(child => child.className === 'terminal-context-menu-separator'), true);
  const contextButtons = Array.from(contextMenu.children).filter(child => child.textContent);
  assert.equal(contextButtons[contextButtons.length - 1].classList.contains('danger'), true, 'Kill is styled as the final destructive action');
  api.setPinnedTabsForTest(['1']);
  api.showSessionContextMenu('1', 20, 20);
  const pinnedContextMenu = api.bodyChildren()[bodyChildCount];
  assert.ok(pinnedContextMenu.children[0].innerHTML.includes('Unpin Tab'), 'pinned tab context menu flips to Unpin Tab');
  assert.equal(pinnedContextMenu.children[0].getAttribute('aria-checked'), 'true', 'pinned tab context menu row is checked');
  const fileItemForMenu = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/README.md');
  api.showTabContextMenu(fileItemForMenu, 30, 30);
  const fileContextMenu = api.bodyChildren()[bodyChildCount];
  assert.ok(fileContextMenu.children[0].innerHTML.includes('Pin Tab'), 'file editor tabs also get the Pin Tab context menu');
  assert.equal(fileContextMenu.children.length, 1, 'non-tmux tab context menu only shows tab-level actions today');
  api.setPinnedTabsForTest([]);
  const sessionViews = api.tmuxSessionViewCommands('1');
  assert.deepStrictEqual(canonical(sessionViews.map(item => item.label)), ["Transcript for session '1'", "YO!summary for session '1'", "Event log for session '1'", 'Pane details']);
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
  const hideDetailsLabel = api.t('pane.details.hide');
  const showDetailsLabel = api.t('pane.details.show');
  assert.equal(controlsHtml.includes('data-panel-tab-overflow'), false);
  assert.equal((controlsHtml.match(/pane-actions-dots/g) || []).length, 1, 'pane header has one merged ellipsis menu');
  assert.ok(controlsHtml.includes(`title="${hideDetailsLabel}" aria-label="${hideDetailsLabel}" aria-pressed="true"`), 'pane header detail toggle starts as the Hide details action');
  assert.equal(controlsHtml.includes('title="YO!info" aria-label="YO!info"'), false, 'pane header detail toggle is not mislabeled as the YO!info pane');
  const detailsPanel = new TestElement('panel-1');
  detailsPanel.dataset.slot = '1';
  const innerDetailToggle = new TestElement('', 'button');
  innerDetailToggle.dataset.detailToggle = '1';
  const headerDetailToggle = new TestElement('', 'button');
  headerDetailToggle.dataset.detailToggle = '1';
  detailsPanel.appendChild(innerDetailToggle);
  api.testElementForId('body').appendChild(headerDetailToggle);
  api.setPanelDetailsCollapsedForTest(detailsPanel, false);
  assert.equal(innerDetailToggle.title, hideDetailsLabel, 'inner detail close uses the expanded-state hide label');
  assert.equal(headerDetailToggle.title, hideDetailsLabel, 'header detail toggle uses the expanded-state hide label');
  assert.equal(headerDetailToggle.getAttribute('aria-pressed'), 'true', 'expanded details mark the header toggle pressed');
  assert.equal(headerDetailToggle.classList.contains('active'), true, 'expanded details keep the header toggle active');
  api.setPanelDetailsCollapsedForTest(detailsPanel, true);
  assert.equal(innerDetailToggle.title, showDetailsLabel, 'inner detail close uses the collapsed-state show label');
  assert.equal(headerDetailToggle.title, showDetailsLabel, 'header detail toggle uses the collapsed-state show label');
  assert.equal(headerDetailToggle.getAttribute('aria-label'), showDetailsLabel, 'header detail aria label follows collapsed state');
  assert.equal(headerDetailToggle.getAttribute('aria-pressed'), 'false', 'collapsed details mark the header toggle unpressed');
  assert.equal(headerDetailToggle.classList.contains('active'), false, 'collapsed details remove the header active state');
  const dockviewPanel = new TestElement('panel-7');
  dockviewPanel.dataset.slot = 'left';
  dockviewPanel.dataset.layoutItem = '7';
  const dockviewHeaderDetailToggle = new TestElement('', 'button');
  dockviewHeaderDetailToggle.dataset.detailToggle = '7';
  api.testElementForId('body').appendChild(dockviewHeaderDetailToggle);
  api.setPanelDetailsCollapsedForTest(dockviewPanel, true);
  assert.equal(dockviewHeaderDetailToggle.getAttribute('aria-pressed'), 'false', 'Dockview header detail toggle syncs by layout item, not the left/right slot id');
  assert.equal(dockviewHeaderDetailToggle.title, showDetailsLabel, 'Dockview header detail toggle flips to Show details when collapsed');
  const terminalPane = api.testElementForId('terminal-pane-1');
  terminalPane.classList.add('active');
  terminalPane.clientWidth = 720;
  terminalPane.clientHeight = 260;
  const fits = [];
  api.registerTerminalForTest('1', {
    cols: 80,
    rows: 24,
    resize(cols, rows) {
      this.cols = cols;
      this.rows = rows;
      fits.push({cols, rows});
    },
    refresh() {},
  });
  api.setPanelDetailsCollapsedForTest(detailsPanel, false);
  assert.ok(fits.length >= 1, 'hiding or showing the detail row schedules a visible tmux terminal fit');
  assert.ok(api.tmuxPaneTabHtml('1', null, {key: 'blocked', short: 'BLK', label: 'Blocked', reason: 'test'}).includes('tab-symbol'));
  assert.equal(api.tmuxSessionNameError('good_name-1.2'), '');
  assert.equal(api.tmuxSessionNameError('dynamo 2'), '');
  assert.equal(api.tmuxSessionNameError('bad/name').includes('letters'), true);
  assert.ok(api.panelControlsHtml('1').includes('data-pane-actions="1"'));
  assert.equal(api.panelControlsHtml('__files__').includes('data-pane-actions'), false);
  assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['finder.toolbar.rootLabel'], undefined, 'Finder toolbar no longer carries a Root button label');
  assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['finder.rootMode.fixed'], undefined, 'Finder toolbar no longer carries a Root mode title');
  const serverShell = fs.readFileSync('yolomux_lib/web.py', 'utf8');
  assert.ok(serverShell.includes('id="fileExplorerRootMode"'));
  assert.ok(serverShell.includes('>Sync</button>'), 'Server-rendered Finder root toggle starts as Sync');
  assert.equal(serverShell.includes('>Root</button>'), false, 'Server-rendered Finder root toggle must not flash Root before JS runs');
  const readonlyApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly');
  assert.equal(readonlyApi.tmuxSessionActionCommands('1').every(item => item.disabled), true);
  readonlyApi.setAutoApproveStateForTest('1', {enabled: true});
  assert.equal(readonlyApi.menuTabCommand('1', {toggleYolo: true}).html.includes('data-auto-session'), false);
  api.setTranscriptInfoForTest('1', {
    project: {git: {cwd: '/home/test/yolomux.dev', root: '/home/test/yolomux.dev'}},
    panes: [{current_path: '/home/test/yolomux.dev/mock', command: 'bash'}],
    selected_pane: {current_path: '/home/test/yolomux.dev'},
  });
  assert.equal(api.fileExplorerRootModeValue(), 'sync');
  assert.equal(api.activeTmuxDirectoryPath('1'), '/home/test/yolomux.dev');
  assert.equal(api.fileExplorerRootForOpen('1'), '/home/test/yolomux.dev');
  api.setSessionFilesPayloadForTest({session: '1', repos: [{repo: '/repo/app'}], files: []});
  api.setFileExplorerRootMode('sync', {sync: false});
  assert.equal(api.fileExplorerRootModeValue(), 'sync');
  assert.equal(api.fileExplorerRootForOpen('1'), '/home/test/yolomux.dev');
  api.setTranscriptInfoForTest('1', {
    project: {git: {cwd: '/home/test/yolomux.dev/static_src/js', root: '/home/test/yolomux.dev'}},
    selected_pane: {current_path: '/home/test/yolomux.dev/static_src/js'},
  });
  api.setSessionFilesPayloadForTest({session: '1', repos: [], files: []});
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test/yolomux.dev',
    affectedDirs: ['/home/test/yolomux.dev'],
    expandPaths: [],
  });
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
  assert.equal(api.commonAncestorPath(['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b/src']), '/home/test/dynamo');
  api.setFileExplorerRootMode('sync', {sync: false});
  api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo/repo-a/src'}});
  api.setSessionFilesPayloadForTest({
    session: '1',
    repos: [{repo: '/home/test/dynamo/repo-a'}, {repo: '/home/test/dynamo/repo-b'}],
    files: [
      {repo: '/home/test/dynamo/repo-a', path: 'src/a.js', abs_path: '/home/test/dynamo/repo-a/src/a.js'},
      {repo: '/home/test/dynamo/repo-b', path: 'lib/b.py', abs_path: '/home/test/dynamo/repo-b/lib/b.py'},
    ],
  });
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test/dynamo',
    affectedDirs: ['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b', '/home/test/dynamo/repo-a/src', '/home/test/dynamo/repo-b/lib'],
    expandPaths: ['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b'],
  });
  const highlightSets = api.fileExplorerSessionHighlightSetsForTest('1');
  assert.deepStrictEqual(canonical(highlightSets.repoRoots), []);
  assert.deepStrictEqual(canonical(highlightSets.touchedDirs), []);
  assert.deepStrictEqual(canonical(highlightSets.expandedDirs), ['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b']);
  assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a', 'dir', '1'), 'file-tree-row--sync-expanded');
  assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a/src', 'dir', '1'), '');
  assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a/src/a.js', 'file', '1'), '');
  api.setFileExplorerRootMode('fixed', {sync: false});
  assert.equal(api.fileExplorerSessionHighlightClassForPath('/home/test/dynamo/repo-a', 'dir'), '');
  api.setFileExplorerRootMode('sync', {sync: false});
  api.setSessionFilesPayloadForTest({
    session: '2',
    repos: [{repo: '/home/test/dynamo/repo-a'}],
    files: [{repo: '/home/test/dynamo/repo-a', path: 'src/a.js', abs_path: '/home/test/dynamo/repo-a/src/a.js'}],
  });
  assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a', 'dir', '1'), '');
  api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo1/src'}});
  api.setSessionFilesPayloadForTest({
    session: '1',
    repos: [{repo: '/home/test/dynamo1'}, {repo: '/tmp/x'}],
    files: [
      {repo: '/home/test/dynamo1', path: 'src/a.js', abs_path: '/home/test/dynamo1/src/a.js'},
      {repo: '/tmp/x', path: 'b.py', abs_path: '/tmp/x/b.py'},
    ],
  });
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test/dynamo1',
    affectedDirs: ['/home/test/dynamo1', '/tmp/x', '/home/test/dynamo1/src'],
    expandPaths: [],
  });
  api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/ai-config/claude/skills'}});
  api.setSessionFilesPayloadForTest({
    session: '1',
    repos: [{repo: '/home/test/ai-config'}],
    files: [
      {repo: '/home/test/ai-config', path: 'claude/skills/a/SKILL.md', abs_path: '/home/test/ai-config/claude/skills/a/SKILL.md'},
      {repo: '/home/test/ai-config', path: 'hooks/install.js', abs_path: '/home/test/ai-config/hooks/install.js'},
    ],
  });
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test/ai-config',
    affectedDirs: ['/home/test/ai-config', '/home/test/ai-config/claude/skills/a', '/home/test/ai-config/hooks'],
    expandPaths: [],
  });
  api.setSessionFilesPayloadForTest({
    session: '1',
    repos: [],
    files: [
      {path: '/home/test/ai-config/claude/skills/a/SKILL.md', abs_path: '/home/test/ai-config/claude/skills/a/SKILL.md'},
      {path: '/home/test/ai-config/hooks/install.js', abs_path: '/home/test/ai-config/hooks/install.js'},
    ],
  });
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test/ai-config',
    affectedDirs: ['/home/test/ai-config/claude/skills/a', '/home/test/ai-config/hooks'],
    expandPaths: ['/home/test/ai-config/claude', '/home/test/ai-config/hooks'],
  });
  api.setTranscriptInfoForTest('1', {selected_pane: {current_path: ''}});
  api.setSessionFilesPayloadForTest({session: '1', repos: [], files: []});
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test',
    affectedDirs: [],
    expandPaths: [],
  });
  api.setSessionFilesPayloadForTest({
    session: '3',
    repos: [{repo: '/home/test/stale'}],
    files: [{repo: '/home/test/stale', path: 'old.js', abs_path: '/home/test/stale/old.js'}],
  });
  assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
    session: '1',
    root: '/home/test',
    affectedDirs: [],
    expandPaths: [],
  });
  const syncCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.equal(syncCss.includes('--finder-session-repo-bg'), false, 'Finder Sync repo marker no longer uses a background token');
  assert.equal(syncCss.includes('--finder-session-touched-bg'), false, 'Finder Sync touched-dir marker no longer uses a background token');
  assert.equal(syncCss.includes('--finder-sync-expanded-bg'), false, 'Finder auto-expanded Sync marker no longer uses a background token');
  assert.ok(/\.file-tree-row\.file-tree-row--sync-expanded > \.file-tree-name,[\s\S]*?\.file-tree-row\.file-tree-row--session-repo > \.file-tree-name,[\s\S]*?\.file-tree-row\.file-tree-row--session-touched > \.file-tree-name,[\s\S]*?\.file-tree-row\.file-tree-row--changed-ancestor > \.file-tree-name\s*\{[\s\S]*?font-weight:\s*800/.test(syncCss), 'Finder Sync and changed-ancestor markers bold the row name instead of painting a background');
  assert.ok(/\.file-tree-git-status\.file-tree-git-status-unknown\s*\{[\s\S]*?background:\s*rgba\(226,\s*232,\s*240,\s*0\.10\) !important[\s\S]*?color:\s*rgba\(226,\s*232,\s*240,\s*0\.46\)/.test(syncCss), 'dark Finder ? status badge is faint against a dark background');
  assert.ok(/body\.theme-light \.file-tree-git-status\.file-tree-git-status-unknown\s*\{[\s\S]*?background:\s*rgb\(var\(--overlay-slate-rgb\) \/ 0\.06\) !important[\s\S]*?color:\s*rgb\(var\(--overlay-slate-rgb\) \/ 0\.36\)/.test(syncCss), 'light Finder ? status badge is faint against a light background through the shared slate overlay token');
  assert.equal(syncCss.includes('.changes-status-unknown'), false, 'Differ no longer has a separate unknown-status CSS path');
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
      {abs_path: '/repo/README.md', agent: 'codex', status: 'M', added: 5, removed: 3},
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
  assert.equal(gitTree.children[1].querySelector(':scope > .file-tree-name').textContent, 'README.md', 'changed file name has no inline numstat');
  assert.ok(gitTree.children[1].querySelector(':scope > .file-tree-diff').innerHTML.includes('changes-diff-add') && gitTree.children[1].querySelector(':scope > .file-tree-diff').innerHTML.includes('changes-diff-remove'), 'changed file diff stats in separate diff element with colored spans');
  assert.equal(gitTree.children[1].classList.contains('has-agent'), true, 'changed Finder rows with agent attribution use the inline-agent file-tree layout');
  assert.ok(gitTree.children[1].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'changed Finder rows render the same agent icon slot as Differ rows');
  assert.equal(
    gitTree.children[1].querySelector(':scope > .file-tree-name').nextElementSibling,
    gitTree.children[1].querySelector(':scope > .file-tree-agent'),
    'changed Finder rows place the AI marker immediately after the filename',
  );
  const modifiedStatus = gitTree.children[1].querySelector(':scope > .file-tree-git-status');
  assert.equal(modifiedStatus.textContent, 'M');
  assert.equal(modifiedStatus.getAttribute('title'), 'M: modified', 'Finder M status badge explains itself on hover');
  assert.equal(modifiedStatus.getAttribute('aria-label'), 'M: modified', 'Finder M status badge is labeled for assistive tech');
  api.setFileExplorerSessionFilesPayloadForTest({
    loaded: true,
    repos: [],
    files: [{abs_path: '/repo/screenshot.png', agent: 'codex', status: '?'}],
  });
  const unknownTree = new TestElement('unknown-tree');
  unknownTree.setAttribute('role', 'tree');
  api.renderTreeChildrenForTest(unknownTree, '/repo', [{name: 'screenshot.png', kind: 'file'}]);
  const unknownStatus = unknownTree.children[0].querySelector(':scope > .file-tree-git-status');
  assert.equal(unknownStatus.textContent, '?');
  assert.equal(unknownStatus.classList.contains('file-tree-git-status-unknown'), true, 'Finder ? status badge uses a faint-neutral class');
  assert.equal(unknownStatus.getAttribute('title'), '?: untracked', 'Finder ? status badge explains itself on hover');

  const hiddenFile = {abs_path: '/repo/.github/workflows/ci.yml', repo: '/repo', path: '.github/workflows/ci.yml', status: 'M', added: 41, removed: 0};
  api.setFileExplorerSessionFilesPayloadForTest({loaded: true, repos: [], files: [hiddenFile]});
  const hiddenFinderTree = new TestElement('hidden-finder-tree');
  hiddenFinderTree.setAttribute('role', 'tree');
  api.renderTreeChildrenForTest(hiddenFinderTree, '/repo', [{name: '.github', kind: 'dir'}]);
  assert.equal(hiddenFinderTree.children.length, 0, 'Finder still hides dot-directories when hidden-files is off');
  const hiddenDifferTree = new TestElement('hidden-differ-tree');
  hiddenDifferTree.setAttribute('role', 'tree');
  api.renderTreeChildrenForTest(hiddenDifferTree, '/repo', [{name: '.github', kind: 'dir'}], 0, [
    ['/repo/.github', [{name: 'workflows', kind: 'dir'}]],
    ['/repo/.github/workflows', [{name: 'ci.yml', kind: 'file'}]],
  ], {
    differMode: true,
    includeHidden: true,
  });
  const hiddenDifferRows = Object.fromEntries(hiddenDifferTree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
  assert.ok(hiddenDifferRows['/repo/.github'], 'Differ renders changed hidden ancestors even when Finder hidden-files is off');
  assert.ok(hiddenDifferRows['/repo/.github/workflows/ci.yml'], 'Differ renders changed files under hidden directories');
  const hiddenDifferHtml = api.fileExplorerChangesPanelHtml();
  assert.ok(hiddenDifferHtml.includes('data-path="/repo/.github"'), 'rendered Differ panel includes hidden changed ancestors');
  assert.ok(hiddenDifferHtml.includes('data-open-change-file="/repo/.github/workflows/ci.yml"'), 'rendered Differ panel opens changed files under hidden directories');
  assert.ok(/data-open-change-file="\/repo\/\.github\/workflows\/ci\.yml"[\s\S]*changes-diff-add">\+41</.test(hiddenDifferHtml), 'Differ shows the hidden file numstat');

  api.setFileExplorerSessionFilesPayloadForTest({
    loaded: true,
    repos: [],
    files: [
      {abs_path: '/repo/A/B/C/F', agents: ['codex'], status: 'M', mtime: 1},
      {abs_path: '/repo/A/B/C/G', agent: 'claude', status: 'M', mtime: 2},
      {abs_path: '/repo/A/B/D/H', agents: ['codex'], status: 'A', mtime: 3},
      {abs_path: '/repo/A/B/D/touched-only.py', agent: 'claude', status: 'T', mtime: 4, source: 'transcript'},
    ],
  });
  api.setFileExplorerExpandedForTest(['/repo/A', '/repo/A/B', '/repo/A/B/C', '/repo/A/B/D']);
  const ancestorTree = new TestElement('ancestor-tree');
  ancestorTree.setAttribute('role', 'tree');
  ancestorTree.classList.add('file-explorer-tree-panel');
  api.renderTreeChildrenForTest(ancestorTree, '/repo', [{name: 'A', kind: 'dir'}], 0, [
    ['/repo/A', [{name: 'B', kind: 'dir'}]],
    ['/repo/A/B', [{name: 'C', kind: 'dir'}, {name: 'D', kind: 'dir'}]],
    ['/repo/A/B/C', [{name: 'F', kind: 'file'}, {name: 'G', kind: 'file'}]],
    ['/repo/A/B/D', [{name: 'H', kind: 'file'}]],
  ]);
  const ancestorRows = Object.fromEntries(ancestorTree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
  assert.equal(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-dir-count').textContent, '3', 'Finder changed ancestor A shows total changed descendants');
  assert.equal(ancestorRows['/repo/A/B'].querySelector(':scope > .file-tree-dir-count').textContent, '3', 'Finder changed ancestor B shows total changed descendants');
  assert.equal(ancestorRows['/repo/A/B/C'].querySelector(':scope > .file-tree-dir-count').textContent, '2', 'Finder changed ancestor C counts only its subtree');
  assert.equal(ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-dir-count').textContent, '1', 'Finder changed ancestor badges ignore transcript-only touched files with no diff');
  assert.ok(ancestorRows['/repo/A'].classList.contains('file-tree-row--changed-ancestor'), 'Finder changed ancestors are bold-marked');
  assert.ok(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon claude'), 'Finder changed ancestor A inherits Claude marker from descendants');
  assert.ok(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'Finder changed ancestor A inherits Codex marker from descendants');
  assert.ok(ancestorRows['/repo/A/B/C'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon claude'), 'Finder changed ancestor C inherits Claude marker from descendants');
  assert.ok(ancestorRows['/repo/A/B/C'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'Finder changed ancestor C inherits Codex marker from descendants');
  assert.ok(!ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon claude'), 'Finder changed ancestor D only shows agents present in that subtree');
  assert.ok(ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'Finder changed ancestor D inherits Codex marker from descendants');
  assert.ok(/agent-icon claude"[^>]*title="modified by Claude [^"]* ago"/.test(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML), 'Finder changed ancestor Claude marker hover names who modified it and when');
  assert.ok(/agent-icon codex"[^>]*title="modified by Codex [^"]* ago"/.test(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML), 'Finder changed ancestor Codex marker hover names who modified it and when');
  assert.equal(
    ancestorRows['/repo/A'].querySelector(':scope > .file-tree-name').nextElementSibling,
    ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent'),
    'Finder changed ancestors place the inherited AI marker immediately after the filename',
  );
  assert.equal(ancestorRows['/repo/A/B/C/F'].classList.contains('file-tree-row--changed-ancestor'), false, 'changed leaf files do not get the ancestor marker');

  api.setFileExplorerSessionFilesPayloadForTest({loaded: true, repos: [], files: []});
  const symlinkTree = new TestElement('symlink-tree');
  symlinkTree.setAttribute('role', 'tree');
  symlinkTree.classList.add('file-explorer-tree-panel');
  api.renderTreeChildrenForTest(symlinkTree, '/repo', [
    {name: 'utils', kind: 'dir', is_repo: true, is_symlink: true, symlink_target: '/home/test/utils', repo: {root: '/repo/utils', name: 'utils', branch: 'main'}},
  ]);
  const symlinkRow = symlinkTree.children[0];
  const symlinkName = symlinkRow.querySelector(':scope > .file-tree-name');
  assert.equal(symlinkName.textContent, 'utils [main] → /home/test/utils', 'symlinked repo rows initially show branch and target');
  api.updateFileTreeGitStatusRowsForTest([symlinkRow]);
  assert.equal(symlinkName.textContent, 'utils [main] → /home/test/utils', 'lightweight Finder status refresh preserves symlink target on repo rows');

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
    tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {split: 'row', pct: 50, children: [{slot: 'slot1'}, {slot: 'right'}]}]},
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: ['1'], active: '1'},
      right: {tabs: ['2'], active: '2'},
    },
  });

  const layoutModeSource = api.emptyLayoutSlots();
  const extraWallItem = api.registerFileEditorLayoutItem('/home/test/b.md');
  layoutModeSource[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  layoutModeSource.left = api.paneStateWithTabs(['__files__'], '__files__');
  layoutModeSource.slot1 = api.paneStateWithTabs(['1', '2', extraPaneItem, extraWallItem], extraPaneItem);
  api.setLayoutSlotsForTest(layoutModeSource);
  api.setFocusedPanelItem(extraPaneItem);
  api.setLayoutToGridPanes();
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {
      split: 'row',
      pct: 22,
      children: [
        {slot: 'left'},
        {
          split: 'row',
          pct: 50,
          children: [
            {split: 'column', pct: 50, children: [{slot: 'leftTop'}, {slot: 'leftBottom'}]},
            {split: 'column', pct: 50, children: [{slot: 'rightTop'}, {slot: 'rightBottom'}]},
          ],
        },
      ],
    },
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      leftTop: {tabs: ['1'], active: '1'},
      rightTop: {tabs: ['2'], active: '2'},
      leftBottom: {tabs: [extraPaneItem], active: extraPaneItem},
      rightBottom: {tabs: [extraWallItem], active: extraWallItem},
    },
  });
  api.setLayoutSlotsForTest(layoutModeSource);
  api.setFocusedPanelItem(extraPaneItem);
  api.setLayoutToWallPanes();
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {
      split: 'row',
      pct: 22,
      children: [
        {slot: 'left'},
        {
          split: 'row',
          pct: 50,
          children: [
            {
              split: 'row',
              pct: 50,
              children: [
                {split: 'row', pct: 50, children: [{slot: 'right'}, {slot: 'slot1'}]},
                {slot: 'slot2'},
              ],
            },
            {slot: 'slot3'},
          ],
        },
      ],
    },
    panes: {
      left: {tabs: ['__files__'], active: '__files__'},
      right: {tabs: ['1'], active: '1'},
      slot1: {tabs: ['2'], active: '2'},
      slot2: {tabs: [extraPaneItem], active: extraPaneItem},
      slot3: {tabs: [extraWallItem], active: extraWallItem},
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
  const roomyFinderDropRect = {left: 0, top: 0, right: 720, bottom: 520, width: 720, height: 520};
  const narrowFinderDropRect = {left: 0, top: 0, right: 300, bottom: 520, width: 300, height: 520};
  const shortFinderDropRect = {left: 0, top: 0, right: 720, bottom: 420, width: 720, height: 420};
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'middle', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'left', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'right', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'top', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom', targetRect: roomyFinderDropRect}), true);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom', targetRect: narrowFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom', targetRect: shortFinderDropRect}), false);
  const editorItem = api.registerFileEditorLayoutItem('/home/test/AGENTS.md');
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'middle', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'left', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'right', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'top', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'bottom', targetRect: roomyFinderDropRect}), true);
  const legacyChangesOnly = api.emptyLayoutSlots();
  legacyChangesOnly[api.layoutTreeKey] = api.leafNode('left');
  legacyChangesOnly.left = api.paneStateWithTabs(['__changes__'], '__changes__');
  api.setLayoutSlotsForTest(legacyChangesOnly);
  assert.equal(api.itemInLayout('__changes__'), false, 'retired standalone Differ is pruned if it appears outside URL alias resolution');
  api.setLayoutSlotsForTest(finderOnly);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'top', targetRect: roomyFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: narrowFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: roomyFinderDropRect}), true);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: shortFinderDropRect}), false);
  assert.equal(api.dropIntentAllowsSession('__files__', {targetSlot: 'slot1', zone: 'left'}), false, 'Finder pane drags never advertise a pane split preview');
  assert.equal(api.dropIntentAllowsSession('__changes__', {targetSlot: 'slot1', zone: 'left'}), false, 'retired standalone Differ is not draggable as a layout item');
  assert.equal(api.dropIntentAllowsSession('__files__', {boundary: 'root', zone: 'right', targetSlot: 'slot1'}), false, 'Finder pane drags never advertise a root split preview');
  assert.equal(api.dropIntentAllowsSession('__changes__', {boundary: 'gutter', zone: 'right', targetSlot: 'slot1'}), false, 'retired standalone Differ cannot be dropped at a gutter');
  const normalSplitSlots = api.emptyLayoutSlots();
  normalSplitSlots[api.layoutTreeKey] = api.leafNode('slot1');
  normalSplitSlots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(normalSplitSlots);
  assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'right', targetRect: {width: 620, height: 520}}), false, 'pane edge previews require enough width for both panes');
  assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'right', targetRect: {width: 720, height: 520}}), true);
  assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'bottom', targetRect: {width: 720, height: 420}}), false, 'pane edge previews require enough height for both panes');
  assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'middle', targetRect: {width: 300, height: 520}}), false, 'middle drops do not preview when the target cannot display the incoming tab');
  assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'middle', targetRect: {width: 420, height: 520}}), true);
  api.setLayoutSlotsForTest(finderOnly);
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
  api.splitSessionAtGutter('__prefs__', '', 'right');
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
  assert.deepStrictEqual(canonical(gutterSplit.panes.slot5), {tabs: ['__prefs__'], active: '__prefs__'});

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
  api.splitSessionAtLayoutBoundary('__prefs__', 'top');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).tree), {
    split: 'row',
    pct: 22,
    children: [
      {slot: 'left'},
      {split: 'column', pct: 50, children: [{slot: 'slot2'}, {slot: 'slot1'}]},
    ],
  });

  api.setLayoutSlotsForTest(dockedBoundarySlots);
  const dockedBoundaryRects = {
    left: {left: 0, top: 0, right: 240, bottom: 800, width: 240, height: 800},
    slot1: {left: 240, top: 0, right: 1200, bottom: 800, width: 960, height: 800},
  };
  api.setLayoutColumnRectsForTest(dockedBoundaryRects);
  api.showDropPreview({boundary: 'root', zone: 'bottom', targetSlot: 'slot1', previewNode: api.gridForTest(), targetRect: {left: 0, top: 0, right: 1200, bottom: 800, width: 1200, height: 800}});
  const previewInset = 6;
  assert.equal(api.gridForTest().style.getPropertyValue('--drop-preview-left'), `${dockedBoundaryRects.slot1.left + previewInset}px`, 'bottom full-span preview starts after the docked Finder');
  assert.equal(api.gridForTest().style.getPropertyValue('--drop-preview-width'), `${dockedBoundaryRects.slot1.width - previewInset * 2}px`, 'bottom full-span preview spans only the non-Finder content');
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
  const gutterEvent = dragEvent(601, '__info__');
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

  const noPreviewSlot = new TestElement('slot-one');
  noPreviewSlot.classList.add('drop-slot');
  noPreviewSlot.dataset.slot = 'slot1';
  noPreviewSlot.rect = {left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400};
  api.setGridPreviewNodesForTest([noPreviewSlot]);
  const finderPaneDrag = dragEvent(16, '__files__');
  finderPaneDrag.target = noPreviewSlot;
  finderPaneDrag.clientY = 200;
  api.handleDropDragOver(finderPaneDrag);
  assert.ok(finderPaneDrag.defaultPrevented, 'Finder pane dragover is handled so the browser does not show its own drop UI');
  assert.ok(finderPaneDrag.propagationStopped, 'Finder pane dragover is owned by the pane drag handler');
  assert.equal(finderPaneDrag.dataTransfer.dropEffect, 'none');
  assert.equal(noPreviewSlot.classList.contains('drop-preview'), false, 'Finder pane drags do not show a dotted pane preview');
  api.setLayoutSlotsForTest(finderOnly);
  const finderStrip = tabStrip([tabElement('__files__', 100, 120)]);
  api.bindPaneTabStrip(finderStrip, 'left');
  const event = dragEvent(125, '1');
  finderStrip.ondragover(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
  assert.equal(event.dataTransfer.dropEffect, 'none');
  assert.equal(finderStrip.classList.contains('tab-drop-preview'), false);
});

test('t@6215', () => {
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
});

test('t@6228', () => {
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
  assert.equal(api.fileEntryChanged({mtime: 1780806618930051800, size: 1}, {mtime_ns: 1780806618930051885, size: 1}), false);
  assert.equal(api.fileEntryChanged({mtime: 1780806618930051800, size: 1}, {mtime_ns: 1780806618950051800, size: 1}), true);
  assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 2}), true);
});

test('t@6249', () => {
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
});

test('t@6274', () => {
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
  // readonly is terminal-only — the server 403s every /api/fs/* read, so Download (and image
  // open) are disabled in readonly to match, rather than offering a command that fails.
  assert.equal(readonlyState.downloadDisabled, true, 'readonly cannot download (server forbids /api/fs/raw)');
  const readonlyImage = readonlyApi.fileContextMenuState({kind: 'file', name: 'screen.png'}, ['/repo/app/screen.png'], ['screen.png']);
  assert.equal(readonlyImage.openInNewTabDisabled, true, 'readonly cannot open an image in a tab (server forbids the read)');
  assert.equal(readonlyState.renameDisabled, true);
  assert.equal(readonlyState.deleteDisabled, true);
});

test('t@6296', () => {
  const api = loadYolomux('', ['1']);
  const html = api.transcriptPathRowHtml('/tmp/yolomux/session.jsonl');
  assert.ok(html.includes('/tmp/yolomux/session.jsonl'));
  assert.ok(html.includes('data-copy-transcript-path'));
  assert.equal(api.transcriptPathRowHtml('').includes('no transcript path'), true);
});

test('t@6304', () => {
  const api = loadYolomux('', ['1']);
  assert.equal(api.editorWrapValue(false), 'off');
  assert.equal(api.editorWrapValue(true), 'soft');
  assert.equal(api.rawFileUrl('/repo/app/a b.txt', {v: 7}), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&v=7');
  assert.equal(api.rawFileDownloadUrl('/repo/app/a b.txt'), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&download=1');
});

test('t@6312', () => {
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
});

test('t@6325', () => {
  const api = loadYolomux('', ['1']);
  assert.equal(api.agentErrorIsBlocking('codex transcript not found by process fd or cwd'), false);
  assert.equal(api.agentErrorIsBlocking('missing /home/test/.claude/sessions/123.json'), false);
  assert.equal(api.agentErrorIsBlocking('worker crashed'), true);
  assert.notEqual(api.sessionState('1', {agents: [{kind: 'codex', error: 'codex transcript not found by process fd or cwd'}]}).key, 'blocked');
  assert.notEqual(api.sessionState('1', {agents: [{kind: 'claude', error: 'missing /home/test/.claude/sessions/123.json'}]}).key, 'blocked');
  assert.equal(api.sessionState('1', {agents: [{kind: 'codex', error: 'worker crashed'}]}).key, 'blocked');
});

test('t@6335', () => {
  const api = loadYolomux('', ['1']);
  api.setAutoApproveStateForTest('1', {
    enabled: false,
    prompt: {visible: false},
    screen: {key: 'approval', text: 'Do you want to proceed?'},
  });
  const state = api.sessionState('1', {agents: [{kind: 'codex'}], panes: []});
  assert.equal(state.key, 'needs-approval', 'roster screen approval state lights EXEC? even when prompt.visible is absent');
  assert.equal(state.reason, 'Do you want to proceed?');
});

test('t@6347', () => {
  const api = loadYolomux('', ['1']);
  const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
  api.i18nSetCatalogForTest('zh-Hant', zhHant);
  api.setActiveLocaleForTest('zh-Hant');
  api.registerTerminalForTest('1', {}, {readyState: 3});
  assert.equal(api.sessionState('1').reason, zhHant['state.reason.terminalConnectionClosed'], 'disconnected terminal reason is localized');
  api.registerTerminalForTest('1', {}, {readyState: 1});
  api.setAutoApproveStateForTest('1', {screen: {key: 'disconnected', text: 'failed to capture pane'}});
  assert.equal(api.sessionState('1', {}).reason, zhHant['state.reason.terminalScreenUnavailable'], 'backend capture failure maps to the localized disconnected fallback');
});

test('t@6359', () => {
  const api = loadYolomux('', ['1', '2', '3']);
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  slots.left = api.paneStateWithTabs(['1', '__info__'], '1');
  slots.slot1 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(slots);

  assert.equal(api.itemIsBackgroundPaneTab('__info__'), true);
  assert.equal(api.itemIsBackgroundPaneTab('1'), false);
  assert.deepStrictEqual(canonical(api.backgroundTabItems()), ['__info__']);
  assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['__files__', '__prefs__', '3']);
});

test('t@6373', () => {
  const api = loadYolomux('', ['1']);
  const firstEditor = api.registerFileEditorLayoutItem('/repo/app/one.md');
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
  slots.left = api.paneStateWithTabs([firstEditor], firstEditor);
  slots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(slots);
  assert.equal(api.slotForNewFileEditorTab(), 'left');
});

test('t@6384', () => {
  const api = loadYolomux('', ['1']);
  assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/repo/app'), 'file.txt');
  assert.equal(api.pathRelativeToDirectory('/repo/app/src/file.txt', '/repo/app'), 'src/file.txt');
  assert.equal(api.pathRelativeToDirectory('/repo/app', '/repo/app'), '.');
  assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/'), 'repo/app/file.txt');
  assert.equal(api.pathRelativeToDirectory('/other/file.txt', '/repo/app'), '/other/file.txt');
});

test('t@6393', () => {
  const api = loadYolomux('', ['1']);
  assert.equal(api.splitPercentForNewItem('1', 'left'), 50);
  assert.equal(api.splitPercentForNewItem('1', 'right'), 50);
  assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'left'), 50);
  assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right'), 50);
  assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right', 42), 42);
  assert.equal(api.splitPercentForNewItem('__files__', 'left'), 22);
  assert.equal(api.splitPercentForNewItem('__files__', 'right'), 78);
});

test('t@6404', () => {
  const api = loadYolomux('', ['1']);
  const windowPanes = [
    {window: '2', window_name: 'codex', window_active: false, active: true, command: 'node'},
    {window: '1', window_name: 'bash', window_active: false, active: true, command: 'bash'},
    {window: '3', window_name: 'node', process_label: 'codex', window_active: true, active: true, command: 'node'},
  ];
  assert.deepStrictEqual(canonical(api.tmuxWindowRecords(windowPanes).map(item => ({
    indexText: item.indexText,
    nameLabel: item.nameLabel,
    numberLabel: item.numberLabel,
    indexedNameLabel: item.indexedNameLabel,
    active: item.active,
  }))), [
    {indexText: '1', nameLabel: 'bash', numberLabel: '1', indexedNameLabel: '1:bash', active: false},
    {indexText: '2', nameLabel: 'codex(2)', numberLabel: '2', indexedNameLabel: '2:codex(2)', active: false},
    {indexText: '3', nameLabel: 'codex(3)', numberLabel: '3', indexedNameLabel: '3:codex(3)', active: true},
  ], 'P5: tmux window records sort by index and disambiguate duplicate names with the window index');
  const windowBarHtml = api.tmuxWindowBarHtml('1', {panes: windowPanes});
  assert.ok(windowBarHtml.includes('data-tmux-window-label-mode="names"'), 'P5: normal window bars prefer names');
  assert.ok(windowBarHtml.includes('data-window-index="1"'), 'P5: window bar button targets window 1');
  assert.ok(windowBarHtml.includes('data-window-index="2"'), 'P5: window bar button targets window 2');
  assert.ok(/class="tab tmux-window-button active"[^>]*data-window-index="3"[^>]*aria-pressed="true"/.test(windowBarHtml), 'P5: active tmux window button is highlighted and pressed');
  assert.ok(windowBarHtml.includes('<span class="tmux-window-name-label">1:bash</span>'), 'DOIT.53 P2: normal labels include index:name');
  assert.ok(windowBarHtml.includes('<span class="tmux-window-name-label">2:codex(2)</span>'), 'P5: duplicate names keep the disambiguating suffix after the index prefix');
  assert.equal(windowBarHtml.includes('3:node'), false, 'DOIT.53 P2: process-aware agent labels beat raw tmux window names like node');
  assert.ok(/data-window-agent="shell"[^>]*data-window-index="1"/.test(windowBarHtml), 'per-agent color: the bash window is tagged as the shell agent');
  assert.ok(/tmux-window-button active"[^>]*data-window-agent="codex"/.test(windowBarHtml), 'per-agent color: the active codex window keeps its agent tag so the swatch shows on the green toggle');
  assert.equal(api.tmuxWindowAgentKeyForTest('claude'), 'claude', 'per-agent color: claude windows map to the claude agent key');
  assert.equal(api.tmuxWindowAgentKeyForTest('vim README.md'), 'editor', 'per-agent color: editor commands collapse to the editor agent key');
  assert.equal(api.tmuxWindowAgentKeyForTest('htop'), 'other', 'per-agent color: unknown programs fall back to the other agent key');
  const manyWindows = Array.from({length: 9}, (_unused, index) => ({
    window: String(index + 1),
    window_name: `w${index + 1}`,
    window_active: index === 0,
    active: true,
    command: 'bash',
  }));
  assert.equal(api.tmuxWindowBarLabelMode(api.tmuxWindowRecords(manyWindows)), 'numbers', 'P5: many windows fall back to numeric labels');
  assert.ok(api.tmuxWindowBarHtml('1', {panes: manyWindows}).includes('data-tmux-window-label-mode="numbers"'), 'P5: numeric fallback is reflected in the rendered bar');
  api.setTranscriptInfoForTest('1', {panes: windowPanes});
  const controls = api.panelControlsHtml('1');
  assert.equal(controls.includes('data-tmux-window-bar="1"'), false, 'DOIT.53 P1: tmux pane header controls do not render the window bar');
  assert.equal(controls.includes('tmux-window-step'), false, 'P5: tmux pane controls no longer render the old prev/next stepper');
  assert.ok(controls.includes('terminal-tab'), 'DOIT.53 P1: tmux pane header keeps only the terminal tab label in the top row');
  assert.ok(controls.includes('>Term</button>'), 'DOIT.56 N3: terminal header button keeps the static Term label');
  assert.equal(controls.includes('>codex</button>') || controls.includes('>node</button>'), false, 'DOIT.56 N3: terminal header no longer duplicates active window/process names');
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const yoloCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/tmuxWindowBarHtml\(session, transcriptMeta\.sessions\?\.\[session\]\)[\s\S]{0,180}class="panel-detail-close"/.test(source), 'DOIT.53 P1: tmux window bar is rendered on the detail row before the close button');
  assert.ok(/delegate\(panel, 'click', '\[data-window-dir\], \[data-window-index\]'/.test(source), 'DOIT.53 P3: in-panel window buttons use the shared delegated click path');
  assert.ok(/\.panel\.details-collapsed \.panel-detail-row\s*\{[\s\S]*display:\s*none/.test(yoloCss), 'DOIT.53 P1: detail-row window bar collapses with the detail row');
  assert.equal(yoloCss.includes('.panel-agent-badge'), false, 'DOIT.57 T1: the duplicate Info Bar agent-badge CSS is removed');
  assert.equal(source.includes('panel-agent-slot'), false, 'DOIT.57 T1: no agent-badge slot is rendered in the detail row');
  assert.ok(/\.tmux-window-button\.active\s*\{[\s\S]*background:\s*var\(--active-control-bg\)/.test(yoloCss), 'DOIT.57 T2: the active window button is a pressed toggle via the shared active-control tokens');
  assert.equal(/\.tmux-window-button\.active\s*\{[^}]*#[0-9a-fA-F]{3,6}/.test(yoloCss), false, 'DOIT.57 T2: the active window button uses theme-aware tokens, not hardcoded hex');
  assert.ok(/\.panel-detail-row \.tmux-window-bar\s*\{[\s\S]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(yoloCss), '2026-06-11 Info Bar regression: tmux window bar right-aligns next to the detail close button');
  assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\.dockview-inner-head-collapsed\.details-collapsed\s*\{\s*grid-template-rows:\s*minmax\(0, 1fr\)/.test(yoloCss), '2026-06-11 Info Bar regression: Dockview terminals get one full-height grid row when both inner header and details are hidden');
  assert.ok(/function setPanelDetailsCollapsed\(panel, collapsed\)\s*\{[\s\S]*schedulePanelDetailsFit\(panel\)/.test(source), '2026-06-11 Info Bar regression: details toggle refits visible tmux terminals after row height changes');
  assert.equal(source.includes('function windowStepButtonHtml'), false, 'DOIT.56 N3: dead header tmux stepper renderer stays removed');
  assert.equal(/button\.textContent = terminalTabLabel/.test(source), false, 'DOIT.56 N3: metadata refresh no longer rewrites the static terminal tab label');
  const calls = [];
  api.setFetchForTest((url, options = {}) => {
    calls.push({url: String(url), method: options.method || 'GET'});
    return Promise.resolve(jsonResponse({ok: true}));
  });
  api.tmuxWindowForTest('1', {windowIndex: '3'}, 'tmux window 3:codex(3)');
  assert.deepStrictEqual(calls, [{url: '/api/tmux-window?session=1&window=3', method: 'POST'}], 'P5: clicking a window button posts direct select-window for that index');
});

test('t@6424', () => {
  loadYolomux();
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function showAttentionAlert(');
  const end = source.indexOf('function dismissAttentionAlertsForSession(', start);
  assert.ok(start > 0 && end > start, 'could not locate showAttentionAlert body');
  const body = source.slice(start, end);
  assert.ok(body.includes('container: displayToastContainer(session)'), 'attention notifications use the target pane-local toast stack');
  assert.equal(body.includes('container: attentionAlerts'), false, 'attention notifications do not use the global fixed stack');
  assert.ok(source.includes('function compactNotificationTitle('), 'notification/toast titles use one compact title helper');
  assert.ok(body.includes('sessionNotificationTitle(session, state)'), 'attention toasts use the compact session notification title');
  assert.equal(source.includes('YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}'), false, 'attention notifications drop verbose host-prefixed titles');
  assert.equal(source.includes('YOLOmux - ${serverHostname}: ${message}'), false, 'watched-PR browser notifications drop verbose host-prefixed titles');
  assert.ok(source.includes("compactNotificationTitle(sessionLabel(session), 'terminal')"), 'terminal connection toasts use the compact session title');
  assert.ok(source.includes("localizedHtml('terminal.connection.reconnectingStatus'"), 'terminal reconnect status is i18n-keyed');
  assert.ok(source.includes("t('terminal.connection.reconnectingToast'"), 'terminal reconnect toast is i18n-keyed');
  assert.ok(source.includes("terminalNotConnectedHtml(session)"), 'terminal-not-connected statuses share the localized helper');
  assert.ok(source.includes("t('terminal.connection.connShort'"), 'terminal socket status text is i18n-keyed');
  assert.ok(source.includes("t('terminal.connection.socketsTitle'"), 'terminal socket status title is i18n-keyed');
  assert.ok(source.includes("t('terminal.summary.streamDisconnected')"), 'summary stream disconnect text is i18n-keyed');
  assert.equal(source.includes('Disconnected. Reconnecting in ${'), false, 'terminal reconnect toast does not leak a hardcoded English literal');
  assert.equal(source.includes('terminal is not connected</span>'), false, 'terminal-not-connected status does not leak a hardcoded English literal');
  assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['terminal.connection.reconnectingToast'], 'Disconnected. Reconnecting in {seconds}s.', 'terminal reconnect toast has a source locale key');
  assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['notify.testTitle'], 'YOLOmux[{host}] notifications enabled', 'test notification title uses compact host bracket format');
  const attentionCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.panel-toast-stack\s*\{[\s\S]*top:\s*8px[\s\S]*z-index:\s*var\(--z-full-screen-overlay\)/.test(attentionCss), 'pane-local toast stacks render below each pane tab strip and above pane contents');
});

test('t@6452', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('async function apiFetchJson('), 'D1: shared JSON fetch helper is bundled');
  assert.ok(source.includes('error.status = response.status'), 'D1: JSON fetch errors preserve HTTP status for callers');
  assert.ok(source.includes('error.payload = payload || {}'), 'D1: JSON fetch errors preserve API payloads for callers');
  const jsonFetchFiles = [
    'static_src/js/yolomux/40_file_explorer_files.js',
    'static_src/js/yolomux/70_layout_actions.js',
    'static_src/js/yolomux/80_panes_preferences.js',
    'static_src/js/yolomux/99_terminal_boot.js',
  ];
  for (const file of jsonFetchFiles) {
    const src = fs.readFileSync(file, 'utf8');
    assert.equal(/const response = await apiFetch\(/.test(src), false, `D1: ${file} should not hand-roll apiFetch response variables`);
    assert.equal(/await response\.json\(\)/.test(src), false, `D1: ${file} should use apiFetchJson instead of manual response.json`);
    assert.equal(/if \(!response\.ok\)/.test(src), false, `D1: ${file} should use apiFetchJson instead of manual response.ok checks`);
  }
  assert.ok((fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8')).includes('apiFetch(`/api/fs/unindex'), 'D1: Finder unindex remains fire-and-forget');
  assert.ok(fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8').includes("apiFetch('/api/event'"), 'D1: event telemetry remains fire-and-forget');
});

test('t@6473', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('const fileState = new Map();'), 'F1: one fileState map owns per-path file/editor state');
  assert.ok(source.includes('const openFiles = fileState;'), 'F1: openFiles is the compatibility alias for fileState');
  for (const obsolete of [
    'const fileEditorTabPaths = new Set()',
    'const filePreviewTabPaths = new Set()',
    'const openFileOwnerSessions = new Map()',
    'const fileEditorViewMode = new Map()',
    'const fileEditorImageMode = new Map()',
    'const editorBlameByPath = new Map()',
    'const fileEditorConflictDialogs = new Set()',
  ]) {
    assert.equal(source.includes(obsolete), false, `F1: removed obsolete path-keyed container ${obsolete}`);
  }
  assert.ok(/function setFileState[\s\S]*editorTabItems[\s\S]*ownerSessions[\s\S]*viewMode[\s\S]*imageMode[\s\S]*blame[\s\S]*conflictDialogOpen/.test(source), 'F1: replacing file content preserves per-path side state on the fileState record');
  assert.ok(/function removeOpenFile[\s\S]*deleteFileState\(path\)/.test(source), 'F1: closing the last owner deletes one fileState record');
  assert.ok(/function renameOpenFilePath[\s\S]*deleteFileState\(oldPath\)[\s\S]*setFileState\(newPath, state\)/.test(source), 'F1: rename moves one fileState record');
});

test('t@6493', () => {
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.actions button,\s*\.info-refresh,\s*\.info-sort-button,\s*\.changes-repo-head,[\s\S]*\.file-editor-toolbar button,[\s\S]*display:\s*inline-flex;[\s\S]*align-items:\s*center;[\s\S]*border:\s*0;[\s\S]*background:\s*transparent;[\s\S]*cursor:\s*pointer;/.test(css), 'I1: common button reset/flex base is centralized');
  assert.equal(/\.actions button\s*\{[^}]*display:\s*inline-flex/.test(css), false, 'I1: topbar actions do not restate the shared inline-flex base');
  assert.equal(/\.info-refresh\s*\{[^}]*cursor:\s*pointer/.test(css), false, 'I1: info refresh does not restate shared cursor behavior');
  assert.equal(/\.file-editor-mode-control button\s*\{[^}]*background:\s*transparent/.test(css), false, 'I1: editor mode buttons do not restate shared transparent background');
});

test('t@6501', () => {
  const api = loadYolomux();
  api.setDocumentTitleNowForTest(0);
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]');
  api.setDocumentTitleNowForTest(119000);
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]', 'idle title stays compact before two minutes');
  api.setDocumentTitleNowForTest(121000);
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux (idle for 2 min)', 'idle title shows elapsed minutes after two minutes');
  api.setDocumentTitleNowForTest(181000);
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux (idle for 3 min)', 'idle title minute count advances while idle');
  api.setAutoApproveStateForTest('1', {screen: {key: 'working'}});
  api.setAutoApproveStateForTest('2', {screen: {key: 'working'}});
  api.setAutoApproveStateForTest('3', {screen: {key: 'idle'}});
  api.updateDocumentTitle();
  assert.equal(api.runningAgentCount(), 2);
  assert.equal(api.documentTitleForTest(), 'YOLOmux [2 running]');
  api.setAutoApproveStateForTest('1', {screen: {key: 'idle'}});
  api.setAutoApproveStateForTest('2', {screen: {key: 'idle'}});
  api.updateDocumentTitle();
  assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]', 'idle timer resets after a running period');
});

test('t@6527', () => {
  const api = loadYolomux();
  const info = {
    project: {
      git: {
        branch: 'main',
        root: '/home/test/project',
        upstream: 'origin/main',
        dirty_count: 10,
        behind: 18,
        head: '747c3fd0c6 ci: Update the dep for the whl publish to be automated (#9961)',
        github_repo: {url: 'https://github.com/ai-project/project'},
        other_branches: {
          branches: [
            {
              name: 'main',
              current: true,
              subject: 'ci: Update the dep for the whl publish to be automated (#9961)',
              updated: '13 hours ago',
            },
            {
              name: 'keivenc/DIS-2141__internlm-tool-parser-parity',
              subject: 'feat: add InternLM tool parser parity',
              pull_request: {
                number: 10075,
                status_label: 'PASSING',
                url: 'https://github.com/ai-project/project/pull/10075',
              },
              linear_ids: ['DIS-2141'],
              updated: '3 days ago',
            },
          ],
        },
      },
      pull_request: null,
    },
    selected_pane: {current_path: '/home/test/project'},
  };
  const html = api.tmuxPaneTabHtml('4', info, null, true);
  const tabBadgeSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(tabBadgeSource.includes('function pullRequestNumberIndicatorHtml'), 'tab renders the PR number chip helper');
  assert.ok(/<span class="ci-indicator tab-symbol pr-number-chip pr-status-merged"[^>]*>#9961<\/span>/.test(html), 'merged default-branch tab renders the #number as a purple chip');
  assert.ok(html.includes('>YO<'), 'tab includes YO marker');
  assert.equal(/session-yolo-marker[^"]*tab-symbol/.test(html), false, 'YO marker stays visible when metadata badges are hidden');
  assert.ok(html.includes('>4<'), 'tab includes session number');
  assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
  assertNoStandalonePrBadge(html, 'merged default-branch tab');
  // #42: a source-inferred PR with no explicit status_label still reports no status (we don't trust a
  // raw merged flag on an inferred PR)...
  assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, merged: true}), '');
  // ...but an explicit status_label is honored even when source_only, so the default-branch head merge
  // commit (which is, by definition, merged) reports MERGED.
  assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, status_label: 'merged'}), 'merged');
  assert.equal(html.includes('MERGED'), false, '#42: a default-branch HEAD merge commit (#9961) consolidates merged state into the purple #number chip');
  assert.ok(html.includes('pr-status-merged'), '#42: the inferred merged PR uses the merged status color on the #number chip');
  assert.equal(html.includes('(#9961)'), false, 'tab title strips duplicated PR suffix');
  api.setActivitySummaryPayloadForTest({
    sessions: {
      '4': {
        local: 'Claude session 4 is idle in project. It currently has 10 files changed. Status check: 10 dirty files; 18 commits behind.',
      },
    },
  });
  const popover = api.sessionPopoverHtml('4', info, 'claude', true);
  assert.ok(/popover-subtitle[\s\S]*branch-indicator[^>]*>MAIN<[\s\S]*pr-number-chip pr-status-merged[^>]*>#9961<[\s\S]*ci: Update the dep/.test(popover), 'merged PR popover header mirrors the tab convention: MAIN chip, purple #number chip, then title');
  assert.equal(popover.includes('#9961:'), false, 'merged PR popover header omits the old #number text prefix');
  assert.ok(/popover-label">branch<\/div><div class="popover-value"><span class="ci-indicator tab-symbol branch-indicator[^"]*">MAIN<\/span>/.test(popover), 'merged PR popover branch row uses the same MAIN chip as the tab');
  assert.ok(/popover-label">PR<\/div><div class="popover-value"><span class="ci-indicator tab-symbol pr-number-chip pr-status-merged[^"]*">#9961<\/span>/.test(popover), 'merged PR popover PR row uses the same purple #number chip as the tab');
  assert.equal(popover.includes('#9961 MERGED'), false, 'merged PR popover omits redundant MERGED text');
  assert.equal(popover.includes('PR #9961'), false, 'merged PR popover avoids repeating PR before the #number value');
  assert.equal(popover.includes('popover-label">desc'), false, 'merged PR popover omits the desc row because the header already carries the PR title');
  assert.equal(popover.includes('Status check:'), false, 'merged PR popover removes the YO!agent status sentence when the dedicated git row is present');
  assert.ok(popover.includes('10 dirty · 18 behind'), 'merged PR popover keeps git facts in the dedicated git row');
  const branchListIndex = popover.indexOf('<div class="branch-list"');
  const detailLabels = [...popover.slice(0, branchListIndex).matchAll(/popover-label">([^<]+)/g)].map(match => match[1]);
  assert.equal(detailLabels[detailLabels.length - 1], 'git', 'merged PR popover makes git the final detail row');
  const headRow = popover.match(/popover-label">HEAD<\/div><div class="popover-value">([^<]+)<\/div><\/div>/)?.[1] || '';
  assert.equal(headRow, '747c3fd0c6', 'merged PR popover HEAD row shows only the SHA, not the repeated subject and PR suffix');
  const branchList = popover.slice(branchListIndex);
  assert.equal(branchList.includes('info-branch-current'), false, 'popover branch list drops the redundant current label');
  assert.ok(/branch-name"><span class="ci-indicator tab-symbol branch-indicator[^"]*">MAIN<\/span>[\s\S]*branch-meta">[\s\S]*pr-number-chip pr-status-merged[^>]*>#9961<\/span>/.test(branchList), 'popover branch list mirrors MAIN and merged PR chips for the current branch');
  assert.equal(branchList.includes('<div class="branch-subject">ci: Update the dep'), false, 'popover branch list suppresses the current branch subject when it duplicates the header title');
  assert.ok(/popover-chip-link[\s\S]*pr-number-chip[^>]*>#10075<\/span>[\s\S]*meta-pr-status pr-status-passing[^>]*>PASSING/.test(branchList), 'popover branch list shows non-current PR numbers as chips while keeping meaningful status text');
  assert.ok(branchList.includes('<div class="branch-subject">feat: add InternLM tool parser parity</div>'), 'popover branch list keeps non-current branch subjects because they add detail');

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
  // the YO ball spins ONLY when .working, and at the slow rotation setting (not a fast
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
  assert.equal(metadataPulseHtml.includes('pr-number-chip metadata-pulse'), false, 'open PR number chip is not pulsed by PR metadata changes');

  const mergedInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 12, merged: true, checks: {state: 'success'}},
    },
  };
  api.applyServerMetadataPulsesForTest('8', {status: 20000});
  const mergedPulseHtml = api.tmuxPaneTabHtml('8', mergedInfo, {key: 'idle'}, true);
  assert.ok(mergedPulseHtml.includes('pr-number-chip pr-status-merged metadata-pulse'), 'merged #number chip pulses after status change');

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
});

test('t@6675', () => {
  const api = loadYolomux('?debug=1', ['1', '2']);
  assert.equal(api.debugModeEnabledForTest(), true, 'debug=1 enables the JS Debug pane');
  assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,files,preferences,debug,image-viewer,file-editor');
  assert.equal(api.resolveLayoutItem('debug'), api.debugPaneItemId, 'debug URL item resolves to the virtual pane when enabled');
  assert.equal(api.itemParam(api.debugPaneItemId), 'debug', 'Debug pane serializes to the readable debug item');
  const fileMenu = api.appMenuTree().find(menu => menu.id === 'file');
  assert.ok(fileMenu.items.some(item => item.targetItem === api.debugPaneItemId), 'File menu exposes JS Debug only when enabled');
  const paletteRows = api.commandPaletteCommandItems().filter(item => item.targetItem === api.debugPaneItemId);
  assert.equal(paletteRows.length, 1, 'command palette lists the Debug pane once through the Tabs group');
  api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/ping', status: 200, ok: true, durationMs: 12.3});
  api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/activity-summary?locale=en', status: 200, ok: true, durationMs: 4200.4});
  api.recordJsDebugEventForTest('sse', {
    eventType: 'fs_changed',
    trigger: 'watch',
    cache: 'ready',
    computeMs: 22.4,
    receiveLatencyMs: 3.2,
    frameBytes: 999,
    bytes: 900,
    changeSummary: {
      roots_changed: 1,
      entries_added: 2,
      entries_removed: 1,
      entries_modified: 3,
      files_added: 1,
      files_removed: 0,
      files_modified: 2,
      dirs_added: 1,
      dirs_removed: 1,
      dirs_modified: 0,
    },
    listingSummary: {
      roots_listed: 2,
      roots_error: 0,
      entries_listed: 44,
    },
  });
  api.recordJsDebugEventForTest('error', {message: 'boom', source: '/static/yolomux.js', line: 10});
  assert.equal(api.jsDebugEventsForTest().length, 4, 'debug event recorder stores bounded diagnostics while enabled');
  const html = api.debugPanelHtmlForTest();
  assert.ok(html.includes('data-js-debug-log'), 'debug panel renders one copyable text log');
  assert.ok(html.includes('GET /api/ping'), 'debug panel renders API timing rows');
  assert.ok(html.includes('Slow API by max latency') && html.includes('GET /api/activity-summary'), 'debug panel summarizes slow API endpoints by path');
  assert.ok(html.includes('Slow SSE server work') && html.includes('Slow SSE receive latency'), 'debug panel summarizes SSE server time and receive latency');
  assert.ok(html.includes('fs=changed=roots:1 +2 -1 ~3 files=+1 ~2 dirs=+1 -1 listed=44/2'), 'debug panel renders fs_changed change counts');
  assert.ok(html.includes('SSE') && html.includes('3.2ms') && html.includes('rx=999B'), 'debug panel renders SSE receive time in the duration column and frame size');
  assert.equal(html.includes('lat=3.2ms'), false, 'debug panel does not use a separate lat= token for SSE rows');
  assert.ok(html.includes('boom'), 'debug panel renders JS error rows');
  const debugPaneSource = fs.readFileSync('static/yolomux.js', 'utf8');
  const debugPaneCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(!/panel\.className = 'panel preferences-panel js-debug-panel'/.test(debugPaneSource), 'Debug panel does not use the Preferences class; Preferences rerenders must not overwrite it');
  assert.ok(/\.preferences-panel,\s*\.js-debug-panel\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(debugPaneCss), 'Debug panel gets the shared panel grid without being a Preferences panel');
  assert.equal(debugPaneSource.includes("initialSetting('performance.activity_summary_refresh_ms'"), false, 'silent activity-summary polling preference is removed');
  assert.equal(debugPaneSource.includes('activitySummaryBackgroundRefreshMs'), false, 'activity-summary no longer keeps a client background refresh timer');
  assert.ok(debugPaneSource.includes('function activitySummaryIsVisible()'), 'activity-summary visibility tracking remains available for server watch state');
  const debugText = api.jsDebugTextForClipboardForTest();
  assert.ok(debugText.includes('page=/?debug=1'), 'debug text includes the active URL path and query');
  assert.ok(debugText.includes('API') && debugText.includes('GET /api/ping'), 'debug text exports API rows');
  assert.ok(debugText.includes('Slow API by max latency') && debugText.includes('GET /api/activity-summary'), 'debug text exports grouped slow API rows');
  assert.ok(debugText.includes('sse_rx=999B'), 'debug text counts estimated SSE frame bytes');
  assert.ok(debugText.includes('Slow SSE receive latency') && debugText.includes('fs_changed'), 'debug text exports grouped SSE latency rows');
  assert.ok(debugText.includes('fs=changed=roots:1 +2 -1 ~3 files=+1 ~2 dirs=+1 -1 listed=44/2'), 'debug text exports fs_changed change counts');
  assert.ok(debugText.includes('Error') && debugText.includes('boom'), 'debug text exports JS error rows');
  assert.equal(debugText.includes('"events"'), false, 'debug copy payload is compact text, not JSON');
  const url = api.syncInitialLayoutUrlForTest();
  assert.equal(parseUrl(url).get('debug'), '1', 'layout URL updates preserve debug=1');
  const openedApi = loadYolomux('?debug=1&sessions=debug', ['1']);
  assert.deepStrictEqual(canonical(openedApi.serialize(openedApi.currentSlots()).panes), {
    left: {tabs: [openedApi.debugPaneItemId], active: openedApi.debugPaneItemId},
  }, 'debug=1 allows sessions=debug to open the Debug pane directly');
  const injectedApi = loadYolomux('?sessions=files,6,5&layout=row@22(slot2,row@50(left,slot1))&tabs=slot2:files;left:6;slot1:5,info&debug=1', ['5', '6']);
  assert.deepStrictEqual(canonical(injectedApi.serialize(injectedApi.currentSlots()).panes), {
    left: {tabs: ['6'], active: '6'},
    slot1: {tabs: ['5', injectedApi.infoItemId, injectedApi.debugPaneItemId], active: injectedApi.debugPaneItemId},
    slot2: {tabs: [injectedApi.fileExplorerItemId], active: injectedApi.fileExplorerItemId},
  }, 'debug=1 injects and activates Debug in an existing URL layout');
});

test('t@6754', () => {
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
  const prFailingLabel = api.t('pr.status.failing');
  assert.ok(detail.includes(`#9981 ${prFailingLabel}`), 'tab menu detail includes localized PR and status');
  assert.ok(detail.includes('GH-2132'), 'tab menu detail includes Linear identifier');
  const linearIndex = detail.indexOf('GH-2132', detail.indexOf('~/project/project3'));
  assert.ok(linearIndex < detail.indexOf(`#9981 ${prFailingLabel}`), 'tab menu detail lists Linear before PR');

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
});

// a session is findable by an OTHER-branch PR / branch name / Linear ID — the same
// project.git.other_branches data YO!info shows — not only its current-branch PR.
test('t@6800', () => {
  const api = loadYolomux();
  const info = {
    selected_pane: {current_path: '/home/test/dynamo4'},
    project: {
      git: {
        branch: 'main',
        root: '/home/test/dynamo4',
        other_branches: {
          branches: [
            {
              name: 'keivenc/DIS-2193__other-work',
              current: false,
              pull_request: {number: 10289, title: 'feat: other branch work'},
              linear_ids: ['DIS-2193'],
            },
          ],
        },
      },
    },
  };
  api.setTranscriptInfoForTest('4', info);
  const fields = api.tabSearchFields('4');
  assert.ok(fields.includes('#10289'), 'an other-branch PR is indexed as #N');
  assert.ok(fields.includes('PR#10289'), '...and as PR#N');
  assert.ok(fields.includes('10289'), '...and as a bare number');
  assert.ok(fields.includes('keivenc/DIS-2193__other-work'), 'the other branch name is indexed');
  assert.ok(fields.includes('DIS-2193'), 'the other-branch Linear ID is indexed');
  assert.ok(fields.includes('feat: other branch work'), 'the other-branch PR title is indexed');
  assert.ok(api.tabSearchScore('4', '#10289') >= 0, 'searching #10289 matches the session');
  assert.ok(api.tabSearchScore('4', 'DIS-2193') >= 0, 'searching the Linear ID matches the session');
});

test('t@6833', () => {
  const api = loadYolomux('', ['alpha', 'beta']);
  const baseActivitySummaryPayload = {
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
  };
  api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
  assert.ok(api.globalActivitySummaryHtml().includes('YO!agent'), 'global activity summary uses the YO agent label');
  assert.equal(api.globalActivitySummaryHtml().includes('Session alpha'), false, 'YO!agent default panel does not expose the per-session SESSION detail list');
  assert.equal(api.yoagentChatHtml().includes('data-yoagent-chat-form'), false, 'No-agent YO!agent hides the chat form');
  assert.ok(api.yoagentChatHtml().includes('Set a Claude or Codex backend in Preferences to chat.'), 'No-agent YO!agent points users to backend settings');
  api.setClientSettingsPatchForTest({yoagent: {backend: 'claude'}});
  const enabledChatHtml = api.yoagentChatHtml();
  assert.ok(enabledChatHtml.includes('data-yoagent-chat-form'), 'Claude-backed YO!agent panel includes a chat form');
  assert.ok(enabledChatHtml.includes('Your most recent work is about editor fixes'), 'Claude-backed YO!agent chat starts with the regular intro message');
  assert.ok(enabledChatHtml.includes('Ask anything'), 'Claude-backed YO!agent composer uses the localized ask-anything placeholder');
  api.setActivitySummaryPayloadForTest({yoagent_summaries: {auto_refresh: true, updated_ts: 1760000000, updated_at: '2025-10-09T08:53:20+00:00'}, global: {headline: 'Cached rolling context'}, sessions: {}, session_order: []});
  assert.ok(api.yoagentChatHtml().includes('Background transcript summaries on'), 'YO!agent chat shows when background transcript summaries are enabled');
  api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
  assert.equal(enabledChatHtml.includes('yoagent-chat empty'), false, 'YO!agent intro is a regular message, not a special empty layout');
  assert.equal(enabledChatHtml.includes('yoagent-chat-toolbar'), false, 'YO!agent chat does not put Clear in a detached toolbar');
  assert.ok(enabledChatHtml.includes('yoagent-chat-controls'), 'YO!agent composer has a control row');
  assert.ok(enabledChatHtml.includes('data-yoagent-backend'), 'YO!agent composer shows the backend (Auto) pill mapped to yoagent.backend');
  // The composer pill offers only Auto / Claude / Codex — never "No agent" (deterministic), which stays
  // an internal Auto fallback.
  assert.ok(/data-yoagent-backend[\s\S]*?<option value="auto"/.test(enabledChatHtml), 'YO!agent composer pill offers Auto');
  assert.ok(/data-yoagent-backend[\s\S]*?<option value="claude"/.test(enabledChatHtml), 'YO!agent composer pill offers Claude');
  assert.ok(/data-yoagent-backend[\s\S]*?<option value="codex"/.test(enabledChatHtml), 'YO!agent composer pill offers Codex');
  assert.equal(/data-yoagent-backend[\s\S]*?<option value="deterministic"/.test(enabledChatHtml), false, 'YO!agent composer pill does not offer No agent (deterministic)');
  assert.ok(enabledChatHtml.includes('yoagent-chat-send-icon'), 'YO!agent send button is a circular arrow icon');
  assert.ok(enabledChatHtml.indexOf('yoagent-chat-clear') < enabledChatHtml.indexOf('yoagent-chat-send'), 'YO!agent send arrow is the last (far-right) control, after Clear');
  api.setYoagentBusyForTest(true);
  assert.ok(api.yoagentChatHtml().includes('yoagent-chat-spinner'), 'YO!agent busy state includes an animated spinner');
  // The "thinking" label keeps its word but the trailing dots are CSS-animated, so the text updates
  // without rebuilding the busy-state DOM.
  assert.ok(api.yoagentChatHtml().includes('thinking'), 'YO!agent busy state keeps the concise thinking label');
  assert.ok(api.yoagentChatHtml().includes('yoagent-thinking-dots'), 'YO!agent thinking dots are CSS animated, not hardcoded static text');
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
  assert.ok(api.globalActivitySummaryHtml().includes('data-yoagent-global-markdown'), 'global activity summary preserves markdown as escaped fallback until the render pass');
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
  assert.equal(api.infoBranchColumnWidthForTest(), 320, 'YO!info Branch column defaults wider than the old 230px minimum');
  assert.equal(api.setInfoBranchColumnWidthForTest(520), 520, 'YO!info Branch column accepts a wider user size');
  assert.equal(api.storageValueForTest('yolomux.infoBranchColumnWidth.v1'), '520', 'YO!info Branch column width persists in browser storage');
  assert.equal(api.setInfoBranchColumnWidthForTest(100), 230, 'YO!info Branch column width clamps to its minimum');
  assert.equal(api.setInfoBranchColumnWidthForTest(5000), 900, 'YO!info Branch column width clamps to its maximum');
  assert.equal(api.resetInfoBranchColumnWidthForTest(), 320, 'YO!info Branch column reset restores the default');
  assert.equal(api.infoDescColumnWidthForTest(), 310, 'YO!info desc column defaults to the previous minimum width');
  assert.equal(api.setInfoDescColumnWidthForTest(760), 760, 'YO!info desc column accepts a wider user size');
  assert.equal(api.storageValueForTest('yolomux.infoDescColumnWidth.v1'), '760', 'YO!info desc column width persists in browser storage');
  assert.equal(api.setInfoDescColumnWidthForTest(100), 310, 'YO!info desc column width clamps to its minimum');
  assert.equal(api.setInfoDescColumnWidthForTest(5000), 1600, 'YO!info desc column width clamps to its maximum');
  assert.equal(api.resetInfoDescColumnWidthForTest(), 310, 'YO!info desc column reset restores the default');
  const runResizeDrag = (column, moveX) => {
    const handleListeners = new Map();
    let capturedPointer = null;
    let releasedPointer = null;
    const resizeHandle = {
      dataset: {infoColumnResize: column},
      addEventListener(type, listener) { handleListeners.set(type, listener); },
      setPointerCapture(pointerId) { capturedPointer = pointerId; },
      releasePointerCapture(pointerId) { releasedPointer = pointerId; },
    };
    const resizeNode = {
      querySelectorAll(selector) {
        return selector === '[data-info-column-resize]' ? [resizeHandle] : [];
      },
    };
    api.bindInfoColumnResizersForTest(resizeNode);
    assert.equal(resizeHandle.dataset.bound, 'true', `YO!info ${column} resize handle binds once`);
    handleListeners.get('pointerdown')({
      pointerId: 7,
      clientX: 100,
      preventDefault() {},
      stopPropagation() {},
    });
    assert.equal(capturedPointer, 7, `YO!info ${column} resize drag captures the pointer`);
    api.windowListenersForTest('pointermove')[0]({clientX: moveX});
    api.windowListenersForTest('pointerup')[0]({});
    assert.equal(releasedPointer, 7, `YO!info ${column} resize drag releases the pointer`);
    assert.equal(api.windowListenersForTest('pointermove').length, 0, `YO!info ${column} resize drag removes pointermove listener on finish`);
  };
  runResizeDrag('branch', 180);
  assert.equal(api.infoBranchColumnWidthForTest(), 400, 'YO!info Branch resize drag changes the column width');
  assert.equal(api.storageValueForTest('yolomux.infoBranchColumnWidth.v1'), '400', 'YO!info Branch resize drag persists the final width');
  runResizeDrag('desc', 250);
  assert.equal(api.infoDescColumnWidthForTest(), 460, 'YO!info desc resize drag changes the column width');
  assert.equal(api.storageValueForTest('yolomux.infoDescColumnWidth.v1'), '460', 'YO!info desc resize drag persists the final width');
});

test('t@6976', () => {
  const api = loadYolomux();
  assert.equal(api.dedentSelectionText('  hello\n  world'), 'hello\nworld');
  assert.equal(api.dedentSelectionText('  hello\n    world'), 'hello\n  world');
  assert.equal(api.dedentSelectionText('\n  hello\n  world\n'), '\nhello\nworld\n');
  assert.equal(api.dedentSelectionText('hello\n  world'), 'hello\nworld');
  assert.equal(api.dedentSelectionText('● 1\n  2\n  3'), '1\n2\n3');
  assert.equal(api.dedentSelectionText('• answer'), 'answer');
  assert.equal(api.dedentSelectionText('• answer:\n\n  \"  hello\\n  world\"'), 'answer:\n\n\"  hello\\n  world\"');
});

test('t@6987', () => {
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
});

// an agent HARD-wraps a long URL with a HANGING INDENT — the continuation is its own logical
// line (isWrapped === false), indented under the URL column. Stitch it onto the link so the whole URL
// is one clickable link, underlined across both rows at their real columns.
test('t@7020', () => {
  const api = loadYolomux();
  const lines = [
    terminalLine('https://github.com/ai-dynamo/frontend-crates/actions/runs/26'),
    terminalLine('    919558600/job', false),  // hanging indent (4 spaces), NOT a soft-wrap
    terminalLine('$ ', false),                 // a plain prompt row — must NOT be merged
  ];
  // the URL row fills the terminal to its right edge (cols == its length), proving it was
  // CLIPPED and hard-wrapped — that is what licenses stitching the indented continuation onto it.
  const term = {cols: lines[0].translateToString(true).length, buffer: {active: {getLine: index => lines[index] || null}}};

  const full = 'https://github.com/ai-dynamo/frontend-crates/actions/runs/26919558600/job';
  // Query from the FIRST row.
  const firstRow = api.terminalWrappedLineLinks(term, 1);
  assert.equal(firstRow.length, 1, 'the hard-wrapped URL is one link when hovering row 1');
  assert.equal(firstRow[0].text, full, 'the link text is the full stitched URL');
  assert.equal(firstRow[0].range.start.y, 1);
  assert.equal(firstRow[0].range.start.x, 1);
  assert.equal(firstRow[0].range.end.y, 2, 'the underline extends onto the continuation row');
  // continuation '919558600/job' is 13 chars after a 4-space indent → last char at column 4 + 13 = 17.
  assert.equal(firstRow[0].range.end.x, 17, 'the continuation underline lands at its REAL (indented) columns');

  // Query from the CONTINUATION row — same link (backward sweep finds the URL start).
  const contRow = api.terminalWrappedLineLinks(term, 2);
  assert.equal(contRow.length, 1, 'the link is also active when hovering the continuation row');
  assert.equal(contRow[0].text, full);
  assert.equal(contRow[0].range.start.y, 1);
  assert.equal(contRow[0].range.end.y, 2);

  // A plain prompt row below is NOT part of the link.
  const promptRow = api.terminalWrappedLineLinks(term, 3);
  assert.equal(promptRow.length, 0, 'the prompt row after the URL is not merged into the link');
});

// (no false JOIN): a COMPLETE url at end-of-line that ends well short of the terminal's
// right edge was NOT clipped, so the indented next row must stay independent — earlier this merged into
// one bogus link `https://example.comnext step`.
test('t@7057', () => {
  const api = loadYolomux();
  const lines = [
    terminalLine('See https://example.com'),  // complete URL, ends at col 23 of an 80-col terminal
    terminalLine('    next step', false),      // indented prose — NOT a clipped URL continuation
  ];
  const term = {cols: 80, buffer: {active: {getLine: index => lines[index] || null}}};
  const row1 = api.terminalWrappedLineLinks(term, 1);
  assert.equal(row1.length, 1, 'C1: a complete URL at EOL links only itself');
  assert.equal(row1[0].text, 'https://example.com', 'C1: link text is the complete URL, not joined with the next row');
  assert.equal(row1[0].range.end.y, 1, 'C1: the underline stays on the URL row (no false continuation onto row 2)');
  const row2 = api.terminalWrappedLineLinks(term, 2);
  assert.equal(row2.length, 0, 'C1: the indented prose continuation is not a link');
});

// (no false merge): an indented line under a row that ends in PROSE (not an unterminated URL)
// is left alone — only a url token that runs off the right edge gets a continuation stitched on.
test('t@7074', () => {
  const api = loadYolomux();
  const lines = [
    terminalLine('Here are the steps to run:'),  // ends in prose, no trailing URL
    terminalLine('    https://example.com/guide', false),
  ];
  const term = {buffer: {active: {getLine: index => lines[index] || null}}};
  const row1 = api.terminalWrappedLineLinks(term, 1);
  assert.equal(row1.length, 0, 'a prose line is not merged with the indented URL below it');
  const row2 = api.terminalWrappedLineLinks(term, 2);
  assert.equal(row2.length, 1, 'the indented URL on its own row still links');
  assert.equal(row2[0].text, 'https://example.com/guide');
  // It links at its own indented columns (4-space indent → starts at column 5).
  assert.equal(row2[0].range.start.x, 5, 'a standalone indented URL underlines at its real column');
  assert.equal(row2[0].range.start.y, 2);
});

// watched-PR ref normalization (client mirror of the backend parse_pull_request_ref).
test('t@7092', () => {
  const api = loadYolomux();
  assert.equal(api.normalizeWatchedPrRef('ai-dynamo/frontend-crates#18'), 'ai-dynamo/frontend-crates#18');
  assert.equal(api.normalizeWatchedPrRef('ai-dynamo/frontend-crates/18'), 'ai-dynamo/frontend-crates#18');
  assert.equal(api.normalizeWatchedPrRef('https://github.com/ai-dynamo/frontend-crates/pull/18'), 'ai-dynamo/frontend-crates#18');
  assert.equal(api.normalizeWatchedPrRef('https://github.com/owner/repo/pull/7/files'), 'owner/repo#7');
  assert.equal(api.normalizeWatchedPrRef('  owner/repo#7  '), 'owner/repo#7');
  assert.equal(api.normalizeWatchedPrRef('https://gitlab.com/owner/repo/pull/7'), '', 'non-github URL is rejected');
  assert.equal(api.normalizeWatchedPrRef('owner/repo'), '', 'a repo without a PR number is rejected');
  assert.equal(api.normalizeWatchedPrRef('owner/repo#0'), '', 'PR #0 is rejected');
  assert.equal(api.normalizeWatchedPrRef('not a ref'), '');
  assert.equal(api.normalizeWatchedPrRef('https://github.com/owner/repo/issues/3'), '', 'an issue URL is not a PR');
});

// watched-PR status snapshot + the pure transition detector (merge / CI→failing / review).
test('t@7107', () => {
  const api = loadYolomux();
  const open = {state: 'open', checks: {state: 'passing'}, review_decision: 'REVIEW_REQUIRED'};
  assert.deepStrictEqual(canonical(api.watchedPrStatusSnapshot(open)), {merged: false, ci: 'passing', review: 'REVIEW_REQUIRED'});
  assert.equal(api.watchedPrStatusSnapshot({merged: true}).merged, true, 'merged flag → merged snapshot');
  assert.equal(api.watchedPrStatusSnapshot({status_label: 'merged'}).merged, true, 'merged status_label → merged snapshot');
  // First sighting (no prev) records a baseline → no transition (avoids a load-time storm).
  assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys(null, api.watchedPrStatusSnapshot(open))), []);
  assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: ''}, {merged: true, ci: 'passing', review: ''})), ['pr-merged'], '→ merged fires pr-merged');
  assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: ''}, {merged: false, ci: 'failing', review: ''})), ['pr-ci-failing'], 'CI → failing fires pr-ci-failing');
  assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'failing', review: ''}, {merged: false, ci: 'passing', review: ''})), [], 'CI failing → passing is not a pr-ci-failing transition');
  assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: 'REVIEW_REQUIRED'}, {merged: false, ci: 'passing', review: 'APPROVED'})), ['pr-review'], 'a review-decision change fires pr-review');
  const same = api.watchedPrStatusSnapshot(open);
  assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys(same, same)), [], 'an unchanged snapshot fires nothing');
});

// notify_transitions gates the new PR keys — they are opt-in (NOT in the default allowlist).
test('t@7124', () => {
  const api = loadYolomux();
  assert.equal(api.shouldNotifyTransitionKey('needs-input'), true, 'a default session-state key still notifies');
  assert.equal(api.shouldNotifyTransitionKey('pr-merged'), false, 'pr-merged is opt-in, off by default');
});

// watched PRs have an initial fetch, SSE updates, container, and transition notifications.
test('t@7131', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(source.includes("resetRuntimeInterval('watched-prs', refreshWatchedPrs"), false, 'watched PRs no longer run a recurring browser poll');
  assert.ok(source.includes("apiFetchJson('/api/watched-prs')"), 'refreshWatchedPrs keeps the boot/manual watched-PR endpoint fetch');
  assert.ok(source.includes('id="info-watched"'), 'YO!info renders a watched-PRs container');
  assert.ok(source.includes('notifyWatchedPrTransitions(watchedPrsData.watched_prs)'), 'incoming snapshots diff statuses to fire transition notifications');
});

// Dev-velocity #1b: in --dev mode the page subscribes to /api/dev-reload and reloads on bundle change.
test('t@7140', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('const devMode = bootstrap.dev === true'), 'the client reads the dev flag from the bootstrap');
  assert.ok(source.includes("new EventSource('/api/dev-reload')"), 'dev mode subscribes to the dev-reload SSE channel');
  assert.ok(/addEventListener\('reload',[\s\S]{0,120}location\.reload\(\)/.test(source), 'a reload event reloads the page');
  assert.ok(source.includes('installDevAutoReload()'), 'the dev auto-reload is installed at boot');
});

// browser clients subscribe to server push events for the expensive live datasets.
test('t@7149', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes("new EventSource('/api/client-events')"), 'client subscribes to the general server event stream');
  assert.ok(source.includes("installRuntimeIntervals();") && source.includes("installClientEventStream();"), 'SSE is installed alongside the remaining local ping/log timers');
  assert.equal(source.includes('function clientPushSuppressesPolling()'), false, 'expensive client polling gate is removed');
  assert.equal(source.includes('refreshTranscriptsFromRuntime'), false, 'metadata fallback poll wrapper is removed');
  assert.equal(source.includes('refreshWatchedFilesystemFromRuntime'), false, 'filesystem fallback poll wrapper is removed');
  assert.equal(source.includes('refreshSettingsFromRuntime'), false, 'settings fallback poll wrapper is removed');
  assert.ok(source.includes('syncServerWatchRoots({renew: true})'), 'connected push mode renews watched roots without polling the filesystem');
  assert.ok(source.includes("apiFetch('/api/watch/roots'"), 'client registers watched roots for server-side SSE polling');
  assert.ok(source.includes('function clientServerWatchRoots()'), 'client derives watched directory roots from Finder/session-file state');
  assert.ok(/function visibleFileEditorWatchFiles\(\)[\s\S]*?activePaneItems\(\)/.test(source), 'client reports active visible editor files separately from directory roots');
  assert.ok(/function backgroundFileEditorWatchFiles\(\)[\s\S]*?paneItems\(\)[\s\S]*?!visible\.has\(path\)/.test(source), 'client reports background editor files separately from active visible editor files');
  assert.ok(source.includes('files: visibleFileEditorWatchFiles()'), 'watch state includes visible editor file paths for the fast files_changed stream');
  assert.ok(source.includes('background_files: backgroundFileEditorWatchFiles()'), 'watch state includes background editor file paths for the slower files_changed stream');
  assert.ok(source.includes("['settings_changed', 'auto_approve_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready']"), 'client listens for the expected push event types');
  assert.ok(/if \(type === 'settings_changed'\)[\s\S]{0,220}applySettingsPayload\(payload\.data, \{force: true\}\)/.test(source), 'settings_changed applies direct payloads without polling settings again');
  assert.ok(/if \(type === 'auto_approve_changed'\)[\s\S]{0,120}applyAutoApprovePayload\(payload\.data\)/.test(source), 'auto_approve_changed applies direct payloads');
  assert.ok(/if \(type === 'watched_prs_changed'\)[\s\S]{0,120}applyWatchedPrsPayload\(payload\.data\)/.test(source), 'watched_prs_changed applies direct payloads');
  assert.ok(/if \(type === 'transcripts_changed'\)[\s\S]{0,220}applyTranscriptsPayload\(payload\.data, \{refreshAuto: false, refreshContext: false, refreshActivity: false\}\)/.test(source), 'transcripts_changed applies direct metadata payloads');
  assert.ok(/if \(type === 'context_items_ready'\)[\s\S]{0,160}applyContextItemsPayloadFromPush\(payload\.data/.test(source), 'context_items_ready applies direct context payloads');
  assert.ok(/if \(type === 'activity_summary_ready'\)[\s\S]{0,120}applyActivitySummaryPayloadFromPush\(payload\.data\)/.test(source), 'activity_summary_ready applies direct summary payloads');
  assert.ok(/if \(type === 'session_files_ready'\)[\s\S]{0,180}applySessionFilesPayloadFromPush\(payload\.data, payload\.request/.test(source), 'session_files_ready applies direct session-files payloads');
  assert.equal(source.includes('session_files_changed'), false, 'stale session_files_changed refetch event path is removed');
  assert.ok(/if \(type === 'files_changed'\)[\s\S]{0,180}refreshOpenFilesFromPush\(payload\)/.test(source), 'files_changed refreshes visible editor files without waiting for directory payloads');
  const filePushHelper = source.slice(source.indexOf('async function refreshOpenFilesFromPush'), source.indexOf('async function refreshFileExplorerFromPush'));
  assert.equal(filePushHelper.includes('fetchDirectory'), false, 'files_changed uses the server file signature directly, not a parent-directory listing');
  assert.equal(filePushHelper.includes('refreshOpenFilesIfChanged'), false, 'files_changed does not route through the directory-backed polling helper');
  assert.equal(source.includes('function scheduleSessionFilesPushRefresh()'), false, 'session-files push no longer triggers a client refetch helper');
  const watchRootsHelper = source.slice(source.indexOf('function clientServerWatchRoots()'), source.indexOf('function clientServerWatchState()'));
  assert.equal(watchRootsHelper.includes('openFiles.keys()'), false, 'open editor file dirs are not folded into the slower directory watch roots');
  assert.ok(/function applyLayoutSlots[\s\S]*?syncServerWatchRoots\(\)/.test(source), 'layout/tab changes immediately resync the server watch state');
  const fsPushHelper = source.slice(source.indexOf('async function refreshFileExplorerFromPush'), source.indexOf('function expandUserPath'));
  assert.equal(fsPushHelper.includes('fetchSessionFiles'), false, 'fs_changed refreshes Finder/open-file state without also fetching session-files');
  assert.ok(/if \(type === 'fs_changed'\)[\s\S]{0,180}refreshFileExplorerFromPush\(payload\)/.test(source), 'fs_changed refreshes Finder/open-file state through the shared push helper');
  assert.ok(source.includes('function clientServerWatchState()'), 'client reports rich watched state, not only filesystem roots');
  assert.ok(source.includes('context_items: activeSessions'), 'watch state includes active transcript context previews');
  assert.ok(source.includes('state.session_files = clientSessionFilesWatchRequests()'), 'watch state includes the current session-files request');
  assert.ok(source.includes("recordJsDebugEvent('sse'"), 'SSE events are captured in JS Debug');
});

// Finder symlink badge — the row toggles is-symlink/symlink-broken, shows a name→target
// title, and the CSS overlays an arrow badge (red + struck-through for broken).
test('t@7192', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(source.includes('row.className = `file-tree-row kind-${entry.kind}`'), false, 'Finder row refresh does not drop and re-add symlink/indexed classes');
  assert.ok(source.includes('syncFileTreeRowKindClass(row, entry.kind)'), 'Finder row kind classes update through stable toggles');
  assert.ok(source.includes("row.classList.toggle('is-symlink', entry.is_symlink === true)"), 'rows flag symlinks');
  assert.ok(source.includes("row.classList.toggle('symlink-broken', entry.kind === 'symlink-broken')"), 'rows flag broken symlinks');
  assert.ok(/entry\.is_symlink === true && entry\.symlink_target[\s\S]{0,160}→ \$\{entry\.symlink_target\}/.test(source), 'a symlink row title shows name → target');
  // The target renders INLINE in the row name ("name → target"), rel or abs as stored.
  const api = loadYolomux();
  const linkFile = api.fileTreeDisplayParts('/repo/link', {kind: 'file', name: 'link', is_symlink: true, symlink_target: '../real/path.txt'});
  assert.equal(linkFile.text, 'link → ../real/path.txt', 'inline text shows name → target');
  assert.ok(linkFile.html.includes('file-tree-symlink-target') && linkFile.html.includes('→ ../real/path.txt'), 'inline target is its own dimmed span');
  const linkDir = api.fileTreeDisplayParts('/repo/ld', {kind: 'dir', name: 'ld', is_symlink: true, symlink_target: '/abs/target'});
  assert.ok(linkDir.text.includes('ld → /abs/target'), 'a symlinked dir shows its absolute target inline');
  const plain = api.fileTreeDisplayParts('/repo/f.txt', {kind: 'file', name: 'f.txt'});
  assert.ok(!plain.text.includes('→'), 'a non-symlink has no target suffix');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.file-tree-row\.is-symlink > \.file-tree-icon::after\s*\{[^}]*content:\s*"↪"/.test(css), 'the symlink icon gets an arrow-badge overlay');
  assert.ok(/\.file-tree-row\.symlink-broken[^{]*\.file-tree-icon::after\s*\{[^}]*color:\s*var\(--bad\)/.test(css), 'a broken symlink badge is red (token)');
  assert.ok(/\.file-tree-row\.symlink-broken[^{]*\.file-tree-name\s*\{[^}]*line-through/.test(css), 'a broken symlink name is struck through');
});

test('t@7214', () => {
  const api = loadYolomux();
  const strip = tabStrip([
    tabElement('1', 100, 100),
    tabElement('2', 203, 100),
    tabElement('3', 306, 100),
  ]);

  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 110, clientY: 8}, '9')), {index: 0, x: 2, y: 0, height: 27, noop: false});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '9')), {index: 1, x: 103, y: 0, height: 27, noop: false});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 390, clientY: 8}, '9')), {index: 3, x: 304, y: 0, height: 27, noop: false});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '2')), {index: 1, x: 206, y: 0, height: 27, noop: true});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(tabStrip([]), {clientX: 180, clientY: 8}, '9')), {index: 0, x: 80, y: 0, height: 28, noop: false});
  assert.equal(api.paneTabDropIndex(strip, {clientX: 225, clientY: 8}, '9'), 1);

  const multiLineStrip = tabStrip([
    tabElement('1', 100, 100, 0),
    tabElement('2', 203, 100, 0),
    tabElement('3', 100, 100, 30),
    tabElement('4', 203, 100, 30),
  ]);
  multiLineStrip.rect = {left: 100, right: 406, top: 0, bottom: 58, width: 306, height: 58};
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 110, clientY: 38}, '9')), {index: 2, x: 2, y: 30, height: 27, noop: false});
  assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 225, clientY: 38}, '9')), {index: 3, x: 103, y: 30, height: 27, noop: false});
});

test('t@7240', () => {
  // View -> Theme is a submenu of discrete System/Dark/Light one-click items.
  const api = loadYolomux('', ['1']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const themeSubmenu = api.appMenuTree()
    .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
    .find(item => item.type === 'submenu' && item.label === 'Theme');
  assert.ok(themeSubmenu, '#24: View has a Theme submenu');
  const themeLabels = themeSubmenu.items.filter(item => item.type === 'command').map(item => item.label);
  assert.deepStrictEqual([...themeLabels], ['System', 'Dark', 'Light'], '#24: Theme submenu offers System/Dark/Light as discrete one-click items');
  assert.ok(themeSubmenu.items.some(item => item.label === 'Dark' && item.checked !== undefined), '#24: Theme items carry a checked state for the current mode');
  // The active marker tracks the LIVE theme (regression: normalizeGlobalThemeMode() with no arg used to
  // always return 'dark', so Dark stayed marked even after switching to Light).
  const themeCheckedFor = mode => {
    api.setGlobalThemeModeForTest(mode);
    return api.appMenuTree()
      .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
      .find(item => item.type === 'submenu' && item.label === 'Theme')
      .items.filter(item => item.checked === true).map(item => item.label);
  };
  assert.deepStrictEqual([...themeCheckedFor('light')], ['Light'], 'theme marker: only Light is checked when light is the live mode');
  assert.deepStrictEqual([...themeCheckedFor('dark')], ['Dark'], 'theme marker: only Dark is checked when dark is the live mode');
  assert.deepStrictEqual([...themeCheckedFor('system')], ['System'], 'theme marker: only System is checked when system is the live mode');
  // setGlobalThemeMode rebuilds the menu bar so the marker updates immediately (not on the next poll).
  assert.ok(/function setGlobalThemeMode[\s\S]*?applyGlobalThemeMode\([^)]*\);\s*renderSessionButtons\(\)/.test(source), 'setGlobalThemeMode re-renders the menu bar so the active marker updates at once');
  // #258: picking a theme APPLIES it live (the menu used to only save the patch).
  // #258: the theme applies live (body.theme-* flips), now via the shared apply+save helper that both
  // the one-click Theme submenu and the View cycle delegate to.
  assert.ok(/function applyAndSaveGlobalTheme[\s\S]*?globalThemeMode = next;\s*applyGlobalThemeMode\(\{updateEditor: true, updateTerminals: true\}\)/.test(source), '#258: applyAndSaveGlobalTheme applies the theme live');
  assert.ok(/function setGlobalThemeMode\(mode\)\s*\{\s*return applyAndSaveGlobalTheme\(normalizeGlobalThemeMode\(mode\)\)/.test(source), '#258: setGlobalThemeMode delegates to the shared apply+save helper');
  assert.ok(/function cycleGlobalThemeSetting\(\)\s*\{\s*return applyAndSaveGlobalTheme\(nextGlobalThemeMode\(\)\)/.test(source), '#258: cycleGlobalThemeSetting delegates to the shared apply+save helper');
  // #261: the View menu no longer PINS the terminal palette — it just follows the app (follow-app stays).
  assert.equal(/function setGlobalThemeMode[\s\S]*?patch\.appearance\.terminal_theme/.test(source), false, '#261: setGlobalThemeMode no longer pins appearance.terminal_theme');
  assert.equal(/function cycleGlobalThemeSetting[\s\S]*?patch\.appearance\.terminal_theme/.test(source), false, '#261: cycleGlobalThemeSetting no longer pins appearance.terminal_theme');
  // Active-terminal cursor: the focused pane's terminal shows the configured cursor color.
  assert.ok(/const DEFAULT_CURSOR_COLOR\s*=\s*'yellow'/.test(source), 'active-terminal cursor: yellow remains the default cursor color');
  assert.ok(/const UI_COLOR_PRESETS\s*=\s*\{[\s\S]*yellow:\s*\{labelKey:\s*'pref\.appearance\.active_color\.yellow',\s*cursorLabelKey:\s*'pref\.appearance\.editor_cursor_color\.yellow',\s*cursor:\s*\{dark:\s*'#ffea00',\s*light:\s*'#9a6700'\}/.test(source), 'active-terminal cursor: bright yellow cursor color lives in the shared UI color parent with a light-mode variant');
  assert.ok(/function terminalThemeForSession[\s\S]*?session === focusedPanelItem \? \{\.\.\.theme, cursor: activeTerminalCursorColorForTheme\(theme\)\}/.test(source), 'active-terminal cursor: the focused session gets the configured cursor color, others keep theme default');
  assert.ok(/item\.term\.options\.theme = terminalThemeForSession\(session, theme\)/.test(source), 'active-terminal cursor: applyTerminalRuntimeSettings themes the active terminal with the configured cursor color');
  assert.ok(/theme: terminalThemeForSession\(session\)/.test(source), 'active-terminal cursor: a newly-created terminal uses terminalThemeForSession');
  assert.ok(/function updatePanelInactiveOverlays[\s\S]*?refreshActiveTerminalCursor\(\)/.test(source), 'active-terminal cursor: focus changes refresh the cursor color (refreshActiveTerminalCursor)');
});

test('t@7283', () => {
  // YO!agent composer redesign (mockup 044): a rounded input bar with the input on top and a control
  // row below — backend "Auto" pill (wired to yoagent.backend) + subtle Clear + a circular send arrow.
  const src = fs.readFileSync('static/yolomux.js', 'utf8');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/class="yoagent-chat-controls"/.test(src), 'YO!agent composer has a control row');
  assert.ok(/function yoagentBackendPillHtml/.test(src) && /data-yoagent-backend/.test(src), 'composer renders the backend (Auto) pill');
  assert.ok(/\[data-yoagent-backend\][\s\S]*?saveSettingsPatch\(settingPatch\('yoagent\.backend'/.test(src), 'changing the backend pill writes the real yoagent.backend setting');
  assert.ok(/class="yoagent-chat-send-icon"[\s\S]*?<path/.test(src), 'send button is a circular arrow icon (not a text "Ask" button)');
  assert.ok(/\.yoagent-chat-form\s*\{[^}]*border-radius:\s*14px/.test(css), 'composer is one rounded container');
  assert.ok(/\.yoagent-chat-send\s*\{[^}]*border-radius:\s*50%/.test(css), 'send button is circular');
  assert.ok(/\.yoagent-backend-pill\s*\{/.test(css), 'backend pill is styled as a pill');
  assert.ok(/\.yoagent-chat \.markdown-body pre[\s\S]*?border-radius:\s*8px/.test(css), 'YO!agent code blocks are soft rounded boxes');
  assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body pre/.test(css), 'YO!agent code blocks get a light box + dark text in light mode');
  assert.ok(/body\.theme-light \.yoagent-message-body\.markdown-body,[\s\S]*?\.yoagent-global \.markdown-body\s*\{[^}]*color:\s*#111827/.test(css), 'YO!agent light-mode markdown bodies use dark app text instead of editor markdown colors');
  assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body strong,[\s\S]*?\.yoagent-global \.markdown-body strong\s*\{[^}]*color:\s*#111827/.test(css), 'YO!agent light-mode bold text is readable, not white-on-light');
  assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body :not\(pre\) > code,[\s\S]*?\.yoagent-global \.markdown-body :not\(pre\) > code\s*\{[^}]*color:\s*#0f4c81/.test(css), 'YO!agent light-mode inline code uses a readable app-blue chip');
  // Rendered-markdown chat bodies drop pre-wrap so bullet lists are tightly spaced (the preserved
  // newlines between/inside the generated <ul><li> HTML were widening them).
  assert.ok(/\.yoagent-message-body\.markdown-body\s*\{[^}]*white-space:\s*normal/.test(css), 'rendered markdown chat bodies use white-space:normal so bullets are not widely spaced');
  // The "thinking" busy indicator uses real staggered dot spans, not static localized "..." text or
  // pseudo-element content animation that may not visibly update in all browsers.
  assert.ok(/const thinkingDots = '<span class="yoagent-thinking-dots"[^']*<span>\.<\/span><span>\.<\/span><span>\.<\/span><\/span>';/.test(src), 'thinking dots render as three real animated spans');
  assert.ok(/\.yoagent-thinking-dots span\s*\{[^}]*animation:\s*yoagent-thinking-dot/.test(css), 'thinking dot spans animate directly');
  assert.ok(/\.yoagent-thinking-dots span\s*\{[^}]*opacity:\s*0/.test(css), 'thinking dots start hidden so the ellipsis visibly cycles');
  assert.ok(/\.yoagent-thinking-dots span:nth-child\(2\)\s*\{[^}]*animation-delay:\s*0\.2s/.test(css), 'thinking dot 2 is staggered');
  assert.ok(/\.yoagent-thinking-dots span:nth-child\(3\)\s*\{[^}]*animation-delay:\s*0\.4s/.test(css), 'thinking dot 3 is staggered');
  assert.ok(/@keyframes yoagent-thinking-dot/.test(css), 'the thinking-dot keyframes exist');
  assert.equal(/prefers-reduced-motion[^{]*\{[^}]*yoagent-thinking-dots/.test(css), false, 'thinking dots keep blinking even when reduced-motion CSS is active');
  // #YO!info scroll: the body pane (a grid item of the .panel grid) must keep min-width:0 so wide
  // content scrolls inside .info-list (overflow:auto) instead of blowing the column out past the
  // overflow:hidden panel (which silently clipped the right side — the user could not scroll right).
  assert.ok(/\.info-pane\s*\{[^}]*min-width:\s*0/.test(css), 'YO!info body pane keeps min-width:0 so wide content scrolls instead of being clipped');
  assert.ok(/\.info-list\s*\{[^}]*overflow:\s*auto/.test(css), 'YO!info list owns the scroll (overflow:auto, both axes)');
  const en = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
  assert.equal(en['yoagent.chatPlaceholder'], 'Ask anything…', 'composer placeholder matches the mockup ("Ask anything…")');
});

test('t@7321', () => {
  // file-search dedupe folds mirror + symlink copies, keeps different-content same-name.
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
});

test('t@7339', () => {
  // the yoagent markdown normalizer tightens loose lists / collapses blank-line runs.
  const api = loadYolomux('', ['1']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(api.yoagentTightMarkdown('- a\n\n- b\n\n- c'), '- a\n- b\n- c', '#129: blank lines between adjacent list items are stripped (tight list)');
  assert.equal(api.yoagentTightMarkdown('1. a\n\n2. b'), '1. a\n2. b', '#129: ordered-list item gaps are stripped too');
  assert.equal(api.yoagentTightMarkdown('lead\n\n\n\nmore'), 'lead\n\nmore', '#129: runs of 2+ blank lines collapse to one');
  assert.equal(api.yoagentTightMarkdown('- a\n\nparagraph'), '- a\n\nparagraph', '#129: a blank line before a NON-list paragraph is preserved');
  // The chat assistant path also runs the tightener (not just the summary path).
  assert.ok(/renderMarkdownPreviewInto\(body, yoagentTightMarkdown\(/.test(source), '#129: the chat assistant body is tightened before rendering');
  // yoagentInlineMarkdown folds in the tightening (heading downgrade + tight lists).
  assert.equal(api.yoagentInlineMarkdown('## H\n\n- a\n\n- b'), '**H**\n\n- a\n- b', '#129: inline-markdown downgrades headings AND tightens the list');
  // a <p> inside an <li> carries no margin so loose lists render tight.
  assert.ok(/\.markdown-body li > p\s*\{[^}]*margin:\s*0/.test(fs.readFileSync('static/yolomux.css', 'utf8')), '#128: .markdown-body li > p has zero margin');
});

test('t@7355', () => {
  // the markdown-preview relative-link path normalizer + the in-pane link handler.
  const api = loadYolomux('', ['1']);
  assert.equal(api.joinAndNormalize('/a/b/c', './x.md'), '/a/b/c/x.md', '#133: ./ resolves against the base dir');
  assert.equal(api.joinAndNormalize('/a/b/c', '../y/z.md'), '/a/b/y/z.md', '#133: ../ pops a segment');
  assert.equal(api.joinAndNormalize('/a/b', 'bare.md'), '/a/b/bare.md', '#133: a bare name resolves against the base dir');
  assert.equal(api.joinAndNormalize('/a/b', '/abs/x.md'), '/abs/x.md', '#133: an absolute rel ignores the base');
  assert.equal(api.joinAndNormalize('/a/b/c', '../../top.md'), '/a/top.md', '#133: multiple ../ collapse');
  const src = fs.readFileSync('static/yolomux.js', 'utf8');
  // The handler reads the RAW href, opens external links in a new tab, and routes file:// + relative
  // file links through openFileInEditor with a preview/edit mode + a failure toast.
  assert.ok(/function handleMarkdownPreviewLinkClick/.test(src), '#133: the markdown-preview link handler exists');
  assert.ok(/a\.getAttribute\('href'\)/.test(src), '#133: the handler reads the raw href attribute');
  assert.ok(/function localPathFromFileHref/.test(src), '#133: file:// preview links are converted to server-side paths');
  assert.ok(src.indexOf('localPathFromFileHref(href)') > -1, '#133: file:// links use the local-path helper');
  assert.ok(src.indexOf('localPathFromFileHref(href)') < src.indexOf("window.open(a.href, '_blank', 'noopener,noreferrer')"), '#133: file:// links are handled before the external window.open branch');
  assert.ok(/window\.open\(a\.href, '_blank', 'noopener,noreferrer'\)/.test(src), '#133: external/other-scheme links open in a new tab');
  assert.ok(/openFileInEditor\(resolved, basenameOf\(resolved\), \{[\s\S]*?viewMode: editorPreviewModeAvailable\(resolved\) \? 'preview' : 'edit'/.test(src), '#133: preview-capable file links open in preview (md/html), else edit');
  assert.ok(/t\('preview\.openFailed'/.test(src), "#133: a failed open surfaces a toast");
  // The handler is wired ONLY to the file-editor preview (path provided), not to yoagent bodies.
  assert.ok(/renderMarkdownPreviewInto\(container, text, path\)/.test(src), '#133: the file-editor preview threads the owning path (basePath); yoagent bodies pass no path');
});

test('t@7378', () => {
  // #260: the Global color theme field renders plain RADIO buttons (replaced the macOS-style cards).
  const api = loadYolomux('', ['1']);
  api.setActiveLocaleForTest('en');
  api.setClientSettingsPatchForTest({appearance: {theme: 'light'}});
  const html = api.preferencesPanelHtmlForTest('');
  for (const v of ['system', 'dark', 'light']) {
    assert.ok(new RegExp(`<input type="radio"[^>]*value="${v}"[^>]*data-setting-path="appearance\\.theme"`).test(html), `#260: a ${v} theme radio renders`);
  }
  assert.ok(/role="radiogroup"/.test(html), '#260: the theme radios render as a radiogroup');
  assert.ok(/value="light"[^>]*data-setting-path="appearance\.theme"[^>]*checked/.test(html), '#260: the active theme (light) radio is checked');
  assert.equal((html.match(/type="radio"[^>]*data-setting-path="appearance\.theme"[^>]*checked/g) || []).length, 1, '#260: exactly one theme radio is checked');
  assert.equal(html.includes('data-theme-card'), false, '#260: no macOS-style theme-card markup remains');
  assert.equal(/<select[^>]*data-setting-path="appearance\.theme"/.test(html), false, '#260: the theme field is radios, not a <select>');
  const themeSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/if \(path === 'appearance\.theme'\) \{\s*globalThemeMode = normalizeGlobalThemeMode\(value\);\s*applyGlobalThemeMode/.test(themeSrc), '#260: changing the theme radio applies the theme live (via savePreferenceControl)');
  const themeCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.preferences-radio-group\s*\{/.test(themeCss), '#260: the radio group has styling');
  assert.equal(/\.theme-card-system/.test(themeCss), false, '#260: the old theme-card CSS is gone');
});

test('t@7399', () => {
  // Preview font size is independent from the editor font size and defaults one px larger.
  const api = loadYolomux('', ['1']);
  api.setActiveLocaleForTest('en');
  const html = api.preferencesPanelHtmlForTest('');
  assert.ok(/data-preference-section="Terminal \/ Editor"[\s\S]*data-setting-path="appearance\.preview_font_size"/.test(html), 'preview font size renders in Terminal / Editor preferences');
  assert.ok(html.includes('Preview font size'), 'preview font size preference has a label');
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes("let editorPreviewFontSize = initialSetting('appearance.preview_font_size', editorFontSize + 1);"), 'preview font size defaults one larger than editor font during bootstrap');
  assert.ok(source.includes("root.setProperty('--editor-preview-font-size'"), 'preview font size writes its own CSS variable');
  assert.ok(source.includes("numberSetting('appearance.preview_font_size', editorFontSize + 1)"), 'preview font size reload preserves the editor+1 fallback');
  assert.ok(source.includes('class="file-editor-preview-font-panel"'), 'preview toolbar includes a font-size control group');
  assert.ok(source.includes('data-editor-preview-font-step="-1"'), 'preview toolbar includes a decrease button');
  assert.ok(source.includes('data-editor-preview-font-step="1"'), 'preview toolbar includes an increase button');
  assert.ok(source.includes("saveSettingsPatch(settingPatch('appearance.preview_font_size', next))"), 'preview font toolbar persists the setting');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.file-editor-preview-pane\s*\{[^}]*font-size:\s*var\(--editor-preview-font-size\)/.test(css), 'rendered preview pane uses the preview font variable');
  assert.ok(/\.file-editor-preview-pane-panel\s*\{[^}]*font-size:\s*var\(--editor-preview-font-size\)/.test(css), 'split/preview pane uses the preview font variable');
  assert.ok(/\.file-editor-raw-panel\s*\{[^}]*font-size:\s*var\(--editor-font-size\)/.test(css), 'raw editor pane keeps the editor font variable');
  const settingsSource = fs.readFileSync('yolomux_lib/settings.py', 'utf8');
  assert.ok(settingsSource.includes('"preview_font_size": 14'), 'preview font size default is 14');
  assert.ok(settingsSource.includes('("appearance", "preview_font_size"): (6, 32)'), 'preview font size has server-side limits');
});

test('t@7423', () => {
  // Phase 1: the topbar language switcher + system-locale resolution.
  const api = loadYolomux('', ['1']);
  // Explicit prefs resolve to themselves; 'system' (no navigator.language in the harness) falls back to en.
  assert.equal(api.resolveLocalePref('zh-Hant'), 'zh-Hant', 'Phase 1: an explicit locale pref resolves to itself');
  assert.equal(api.resolveLocalePref('zh-Hans'), 'zh-Hans', 'Phase 1: Simplified Chinese resolves to itself');
  assert.equal(api.resolveLocalePref('en'), 'en', 'Phase 1: English resolves to itself');
  assert.equal(api.resolveLocalePref('system'), 'en', 'Phase 1: system falls back to en without a browser locale');
  // The switcher choices: system + en + Traditional-before-Simplified + pseudo, endonym-labeled.
  const choices = api.i18nLocaleChoices();
  assert.deepEqual(choices.map(c => c.value), ['system', 'en', 'zh-Hant', 'zh-Hans', 'es', 'ja', 'de', 'fr', 'pt-BR', 'ru', 'ko', 'hi', 'ar', 'he', 'en-XA'], 'Phase 1/2: the locale choices are ordered with all shipped locales then pseudo');
  assert.equal(choices.find(c => c.value === 'de').label, 'Deutsch', 'Phase 2: German is labeled with its endonym');
  assert.equal(choices.find(c => c.value === 'ru').label, 'Русский', 'Phase 2: Russian is labeled with its endonym');
  assert.equal(choices.find(c => c.value === 'ar').label, 'العربية', 'Phase 2: Arabic is labeled with its endonym');
  assert.equal(choices.find(c => c.value === 'he').label, 'עברית', 'Hebrew is labeled with its endonym');
  for (const loc of ['de', 'fr', 'pt-BR', 'ru', 'ko', 'hi', 'ar', 'he']) {
    assert.equal(api.resolveLocalePref(loc), loc, `Phase 2: ${loc} resolves to itself`);
  }
  // RTL: Arabic and Hebrew are detected as right-to-left; LTR locales are not.
  assert.equal(api.i18nIsRtl('ar'), true, 'Phase 2: ar is RTL');
  assert.equal(api.i18nIsRtl('he'), true, 'he is RTL');
  assert.equal(api.i18nIsRtl('de'), false, 'Phase 2: de is LTR');
  // applyLocale flips document.dir; the build CSS uses logical flow properties so RTL mirrors.
  const rtlSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/document\.documentElement\.setAttribute\('dir', i18nIsRtl\(next\) \? 'rtl' : 'ltr'\)/.test(rtlSrc), 'Phase 2: applyLocale sets the document direction for RTL locales');
  // A language switch must repaint the Finder's static toolbar chrome, not just panel bodies — so
  // rerenderForLocale rebuilds the Finder panel from source (fixes stale prev-locale toolbar labels).
  assert.ok(/function rerenderForLocale[\s\S]*?relocalizeFileExplorerPanels\(\)/.test(rtlSrc), 'rerenderForLocale rebuilds the Finder toolbar chrome on a language switch');
  assert.ok(/function relocalizeFileExplorerPanels\(\)[\s\S]*?removePanelForItem\(fileExplorerItemId\)[\s\S]*?renderPanels\(/.test(rtlSrc), 'relocalizeFileExplorerPanels evicts then rebuilds the Finder panel from its single source of truth');
  const rtlCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.equal(/(^|[^-])(margin|padding|border)-(left|right):/m.test(rtlCss.replace(/[a-z-]*-(left|right)-radius/g, '')), false, 'Phase 2: flow-spacing CSS uses logical (inline) properties, not physical left/right, so RTL mirrors');
  assert.ok(rtlCss.includes('margin-inline-start:') && rtlCss.includes('padding-inline-start:'), 'Phase 2: the CSS uses logical inline properties');
  assert.equal(choices.find(c => c.value === 'es').label, 'Español', 'Phase 1: Spanish is labeled with its endonym');
  assert.equal(choices.find(c => c.value === 'ja').label, '日本語', 'Phase 1: Japanese is labeled with its endonym');
  assert.equal(api.resolveLocalePref('es'), 'es', 'Phase 1: Spanish resolves to itself');
  assert.equal(api.resolveLocalePref('ja'), 'ja', 'Phase 1: Japanese resolves to itself');
  assert.equal(choices.find(c => c.value === 'zh-Hant').label, '繁體中文', 'Phase 1: Traditional Chinese is labeled with its endonym');
  assert.equal(choices.find(c => c.value === 'zh-Hans').label, '简体中文', 'Phase 1: Simplified Chinese is labeled with its endonym');
  const src = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/sessionButtons\.appendChild\(createTopbarLanguageSwitcher\(\)\)/.test(src), 'Phase 1: the topbar renders the language switcher');
  assert.ok(/function createTopbarLanguageSwitcher[\s\S]*?applyLocale\(resolveLocalePref\(value\)\)[\s\S]*?saveSettingsPatch\(settingPatch\('general\.language', value\)\)/.test(src), 'Phase 1: the switcher applies the locale optimistically AND saves general.language (same setting as Preferences)');
  // The zh fallback mapping (zh-TW/HK/Hant -> Hant, other zh -> Hans).
  assert.ok(/nav\.startsWith\('zh'\)\) return \/hant\|/.test(src), 'Phase 1: system maps Chinese browser locales to Hant/Hans');
  assert.ok(/\.topbar-language\s*\{/.test(fs.readFileSync('static/yolomux.css', 'utf8')), 'Phase 1: the language switcher has topbar styling');
  // #256: topbar theme switcher (auto/dark/light) mirrors the language switcher and sits right of it;
  // order ends Language, Theme, Activity (activity pinned far-right).
  // #257: the topbar theme switcher was REMOVED (redundant). Order is Language, then Activity (far right).
  assert.ok(/sessionButtons\.appendChild\(createTopbarLanguageSwitcher\(\)\);\s*sessionButtons\.appendChild\(createTopbarActivityStatus\(\)\)/.test(src), '#257: topbar order is Language then Activity (no theme switcher between them)');
  assert.equal(/createTopbarThemeSwitcher/.test(src), false, '#257: createTopbarThemeSwitcher is gone (no redundant topbar theme select)');
  {
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.equal(/\.topbar-theme\s*\{/.test(css), false, '#257: the .topbar-theme CSS is removed with the switcher');
    // #254/#259 follow-up: light-mode inactive-pane dim stays neutral gray, with a softer alpha.
    assert.ok(css.includes('--inactive-pane-overlay-rgb: 90 96 105'), '#259: light-mode inactive panes dim a softer neutral gray (no red cast)');
    assert.ok(css.includes('--inactive-pane-overlay-alpha: 0.09'), '#259: light-mode inactive panes keep the softer alpha base');
    // Light-mode pane header (image 043): greenish-light tab-strip container + light frame-control
    // buttons (the minimize/zoom squares used to render dark/"black" with no light values).
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-dim:\s*#e1edda/.test(css), 'light mode: the pane tab-strip container is greenish-light (active-accent-dim)');
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-control-bg:\s*#f7f9fc/.test(css), 'light mode: the pane minimize/frame button has a light fill (not a dark square)');
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-zoom-bg:\s*var\(--active-control-bg\)/.test(css), 'light mode: the pane zoom button uses the shared active-control fill, not a dark square');
    assert.equal(css.includes('--inactive-pane-overlay-rgb: 124 82 88'), false, '#259: the earlier warm/red tint is gone (superseded by gray)');
    assert.equal(css.includes('--inactive-pane-overlay-alpha: 0.16'), false, '#259 follow-up: the too-dark light overlay alpha is gone');
    assert.equal(css.includes('--inactive-pane-overlay-alpha: 0.13'), false, '#259 follow-up: the still-too-dark light overlay alpha is gone');
    // #258 follow-up: editor toolbar placement is inherited from stable parent zones, not per-button order.
    assert.ok(/\.file-editor-toolbar-left\s*\{[^}]*flex:\s*0 1 auto/.test(css), 'editor info bar: #/Differ/FROM-TO live in the shared left zone');
    assert.ok(/\.file-editor-toolbar-center\s*\{[^}]*position:\s*absolute[\s\S]*left:\s*50%/.test(css), 'editor info bar: font-size controls live in the shared center zone');
    assert.ok(/\.file-editor-toolbar-right\s*\{[^}]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(css), 'editor info bar: edit/preview/tools live in the shared right zone');
    assert.equal(/\.file-editor-(?:gutter|diff|diff-expand)-panel\s*\{[^}]*order:/.test(css), false, 'editor info bar: left buttons do not own placement with child order rules');
    assert.ok(/\.file-editor-diff-ref-panel\s*\{[^}]*min-width:\s*max-content[^}]*overflow:\s*visible/.test(css), 'editor info bar: FROM/TO/reset is intrinsic-width and not clipped');
    assert.equal(css.includes('max-width: min(32vw, 190px)'), false, 'editor info bar: the old too-narrow 190px clipping cap is gone');
    assert.ok(/\.file-tab-parent\s*\{[^}]*text-overflow:\s*ellipsis/.test(css), 'duplicate file-tab parent suffix is styled as compact muted metadata');
    assert.ok(/\.preferences-setting-control\.setting-type-select,\s*\.preferences-setting-control\.setting-type-text\s*\{[^}]*justify-content:\s*start/.test(css), 'Preferences selects/text inputs are left-aligned from the shared inset');
    assert.ok(/\.preferences-setting-control\.setting-type-number input\[type="number"\]\s*\{[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(css), 'Preferences number inputs are left-aligned from the shared inset');
    // #258 (toast): the toast stack clears the topbar (z-index above 180) and messages wrap, not clip.
    assert.ok(/\.panel-toast-stack\s*\{[^}]*z-index:\s*var\(--z-full-screen-overlay\)/.test(css), '#258: the toast stack renders above the topbar (var(--z-full-screen-overlay)) so it is not clipped under it');
    assert.ok(/\.toast-line\s*\{[^}]*white-space:\s*normal/.test(css), '#258: toast messages wrap (white-space:normal) instead of ellipsis-clipping');
    assert.equal(/\.toast-line\s*\{[^}]*white-space:\s*nowrap/.test(css), false, '#258: the old nowrap/ellipsis clipping of the toast message line is gone');
  }
  // #255: inactive-pane dimming is now ONE CSS rule keyed off the uniformly-toggled .focused-pane class
  // — no per-pane JS overlay, no isVirtualItem special-case, every pane type dims identically.
  assert.equal(/function installPanelInactiveOverlays/.test(src), false, '#255: the per-pane JS overlay installer is deleted (dimming is pure CSS)');
  assert.equal(/class="panel-inactive-overlay"/.test(src), false, '#255: no per-pane inactive-overlay div is injected anymore');
  assert.ok(/\.panel:not\(\.focused-pane\)[^{]*\.panel-overlay-root::after\s*\{[^}]*background:\s*var\(--inactive-pane-overlay\)/.test(fs.readFileSync('static/yolomux.css', 'utf8')), '#255: inactive panes dim via one CSS rule on .panel:not(.focused-pane) .panel-overlay-root::after');
  assert.equal(/\.panel:not\(\.focused-pane\):not\(\.typing-ready-pane\)[^{]*\.panel-overlay-root::after/.test(fs.readFileSync('static/yolomux.css', 'utf8')), false, 'inactive-pane dim must still paint on a stale typing-ready pane');
  assert.equal(/updateInactivePaneGradientDirs|has-inactive-pane-gradient|pane-gradient-dir/.test(src), false, 'inactive-pane gradient JS is removed until the feature is revisited');
  // #260: a drag-drop open establishes a clean baseline (clears external-change flags on a fresh,
  // non-dirty open) so it never pops a spurious reload prompt — matching double-click.
  assert.ok(/function openDraggedFilesInEditor[\s\S]*?if \(draggedState && !draggedState\.dirty\) \{[\s\S]*?delete draggedState\.externalChanged/.test(src), '#260: drag-drop open clears externalChanged on a non-dirty fresh open (no spurious reload prompt)');
  // boot() resolves the raw general.language pref (so a system pref localizes client-side).
  assert.ok(/await applyLocale\(resolveLocalePref\(initialSetting\('general\.language', 'system'\)\)\)/.test(src), 'Phase 1: boot resolves the raw language pref (system -> navigator)');
  // The Spanish locale ships with full key-parity and real (non-English) translations.
  const en = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
  const es = JSON.parse(fs.readFileSync('static/locales/es.json', 'utf8'));
  assert.deepEqual(Object.keys(es).sort(), Object.keys(en).sort(), 'Phase 1: es.json has exactly the same keys as en.json (parity)');
  // The YO!info / YO!agent tab labels are localized via brand.tab.*; en (and non-Chinese locales) keep
  // the English brand text, while the two Chinese catalogs render the requested glyphs (asserted below).
  assert.equal(en['brand.tab.info'], 'YO!info', 'en YO!info tab label');
  assert.equal(en['brand.tab.agent'], 'YO!agent', 'en YO!agent tab label');
  assert.equal(es['menu.file'], 'Archivo', 'Phase 1: es translates a representative menu label');
  assert.equal(es['pref.reset.cancel'], 'Cancelar', 'Phase 1: es translates the reset cancel button');
  assert.ok(es['pref.appearance.file_explorer_font_size.label'].includes('{name}'), 'Phase 1: es preserves interpolation placeholders');
  const ja = JSON.parse(fs.readFileSync('static/locales/ja.json', 'utf8'));
  assert.deepEqual(Object.keys(ja).sort(), Object.keys(en).sort(), 'Phase 1: ja.json has exactly the same keys as en.json (parity)');
  assert.equal(ja['menu.file'], 'ファイル', 'Phase 1: ja translates a representative menu label');
  assert.equal(ja['pref.reset.cancel'], 'キャンセル', 'Phase 1: ja translates the reset cancel button');
  assert.ok(ja['changes.fileCount.other'].includes('{count}'), 'Phase 1: ja preserves count placeholders');
  const de = JSON.parse(fs.readFileSync('static/locales/de.json', 'utf8'));
  assert.deepEqual(Object.keys(de).sort(), Object.keys(en).sort(), 'Phase 2: de.json has exactly the same keys as en.json (parity)');
  assert.equal(de['menu.file'], 'Datei', 'Phase 2: de translates a representative menu label');
  assert.equal(de['login.signIn'], 'Anmelden', 'Phase 2: de translates the login sign-in label');
  const fr = JSON.parse(fs.readFileSync('static/locales/fr.json', 'utf8'));
  assert.deepEqual(Object.keys(fr).sort(), Object.keys(en).sort(), 'Phase 2: fr.json has exactly the same keys as en.json (parity)');
  assert.equal(fr['menu.file'], 'Fichier', 'Phase 2: fr translates a representative menu label');
  assert.equal(fr['pref.reset.cancel'], 'Annuler', 'Phase 2: fr translates the reset cancel button');
  // The Phase 2 tail locales all ship with full key-parity and preserve placeholders.
  for (const loc of ['pt-BR', 'ru', 'ko', 'hi', 'ar', 'he']) {
    const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
    assert.deepEqual(Object.keys(cat).sort(), Object.keys(en).sort(), `Phase 2: ${loc}.json has exactly the same keys as en.json (parity)`);
    assert.equal(cat['brand.marker'], 'YO', `Phase 2: ${loc} keeps the YO brand marker`);
    assert.equal(cat['brand.tab.info'], 'YO!info', `Phase 2: ${loc} keeps the YO!info tab label`);
    assert.equal(cat['brand.tab.agent'], 'YO!agent', `Phase 2: ${loc} keeps the YO!agent tab label`);
    assert.ok(cat['pref.appearance.file_explorer_font_size.label'].includes('{name}'), `Phase 2: ${loc} preserves the {name} placeholder`);
    assert.ok(cat['yoagent.files'].includes('{count}') && cat['yoagent.files'].includes('{added}'), `Phase 2: ${loc} preserves count/added placeholders`);
    assert.notEqual(cat['menu.file'], 'File', `Phase 2: ${loc} actually translates (menu.file not English)`);
    // Phase 3: the new Intl-wrap + deterministic-framing keys ship in every locale.
    for (const k of ['yoagent.updated.wrap', 'det.noBackend', 'det.noActivity', 'det.openPending']) {
      assert.ok(typeof cat[k] === 'string' && cat[k].length, `Phase 3: ${loc} has ${k}`);
    }
    assert.ok(cat['yoagent.updated.wrap'].includes('{rel}'), `Phase 3: ${loc} preserves the {rel} placeholder`);
  }
});

test('t@7555', () => {
  // Phase 3: relative time renders via Intl.RelativeTimeFormat(activeLocale) (native phrasing).
  const api = loadYolomux('', ['1']);
  api.setActiveLocaleForTest('en');
  assert.equal(api.relativeTimeFormat(120), '2 minutes ago', 'Phase 3: en relative time is "2 minutes ago" via Intl');
  assert.equal(api.relativeTimeFormat(7200), '2 hours ago', 'Phase 3: hours via Intl');
  assert.equal(api.relativeTimeFormat(172800), '2 days ago', 'Phase 3: days via Intl');
  const src = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/new Intl\.RelativeTimeFormat\(i18nActiveLocale/.test(src), 'Phase 3: relativeTimeFormat uses Intl.RelativeTimeFormat with the active locale');
  assert.ok(/t\('yoagent\.updated\.wrap', \{rel: relativeTimeFormat\(seconds\)\}\)/.test(src), 'Phase 3: the activity "last updated" line wraps the Intl relative time');
});

test('t@7567', () => {
  // tab-move latency. The shape signature ignores tabs order / active item, so a reorder or
  // activate is a "same shape" change that takes the cheap in-place branch (no grid/topbar teardown,
  // no server re-poll).
  const api = loadYolomux('', ['1', '2']);
  const slots = api.defaultLayoutSlots();
  const sigA = api.layoutShapeSignature(slots);
  // Mutating a pane's active item / tabs order does NOT change the shape signature.
  const clone = JSON.parse(JSON.stringify(slots));
  for (const key of Object.keys(clone)) {
    if (key !== '__tree' && clone[key] && Array.isArray(clone[key].tabs)) {
      clone[key].tabs = clone[key].tabs.slice().reverse();
      clone[key].active = clone[key].tabs[0];
    }
  }
  assert.equal(api.layoutShapeSignature(clone), sigA, '#reorder/activate keeps the same shape signature');
  // A different tree TOPOLOGY (a split) yields a different signature -> full rebuild path.
  const split = {'__tree': {split: 'row', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]}, slot1: {tabs: ['1'], active: '1'}, slot2: {tabs: ['2'], active: '2'}};
  assert.notEqual(api.layoutShapeSignature(split), sigA, '#a split changes the shape signature');

  // S1: applyLayoutSlots no longer re-polls the server (refreshTranscripts removed from its body).
  const layoutSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  const applyBody = layoutSrc.slice(layoutSrc.indexOf('function applyLayoutSlots'), layoutSrc.indexOf('function updateActiveSessionParam'));
  assert.equal(/refreshTranscripts\(\);/.test(applyBody), false, '#applyLayoutSlots does not call refreshTranscripts() (no server re-poll on a local layout change)');
  // applyLayoutSlots delegates the shape decision to the shared scheduler.
  const schedulerBody = layoutSrc.slice(layoutSrc.indexOf('function performLayoutRender'), layoutSrc.indexOf('function updateActiveSessionParam'));
  assert.ok(/requestLayoutRender\(\{[\s\S]*?prevShape[\s\S]*?nextShape: layoutShapeSignature\(layoutSlots\)/.test(applyBody), '#applyLayoutSlots sends prev/next shape to the shared layout scheduler');
  assert.ok(/function requestLayoutRender[\s\S]*?pendingLayoutRender = mergePendingLayoutRender/.test(schedulerBody), '#scheduler stores structured deferred render state during drag');
  assert.ok(/layoutRenderCanUseCheap\(renderRequest\)[\s\S]*?syncActivePanelsInPlace\(\)/.test(schedulerBody), '#same-shape changes take the in-place branch');
  assert.ok(/renderSessionButtons\(\);\s*renderPanels\(previousActive/.test(schedulerBody), '#shape changes still fall through to the full rebuild');
  assert.ok(layoutSrc.includes('function syncActivePanelsInPlace'), '#the in-place panel swap exists');
  // fix 6: the markdown preview render is guarded by a path+content signature.
  assert.ok(/container\._previewPath !== path \|\| container\._previewText !== text/.test(layoutSrc), '#fix 6: renderEditorPreviewPane skips re-rendering unchanged markdown');
});

test('t@7602', () => {
  // Phase 0: i18n runtime — t()/tPlural() fallback + interpolation, active-over-en, pseudo.
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
});

test('t@7620', () => {
  // Phase 0: the Preferences General section + section titles render through t(); under the
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
  // Phase 0 (extraction complete): every preference section's fields are i18n-keyed, so the
  // pseudo-locale accents them and NO plain-English label/help from any section leaks through.
  for (const key of [
    'pref.appearance.theme.label', 'pref.appearance.terminal_theme.help',
    'pref.appearance.date_time_hour_cycle.label', 'pref.appearance.font_sizes.note',
    'pref.performance.latency_refresh_ms.label', 'pref.performance.event_log_refresh_ms.label',
    'pref.performance.server_event_poll_ms.label', 'pref.performance.server_background_file_event_poll_ms.label',
    'pref.performance.server_directory_event_poll_ms.label',
    'pref.notifications.throttle_seconds.label',
    'pref.terminal_editor.scrollback.label', 'pref.uploads.max_bytes.label',
    'pref.yoagent.backend.label', 'pref.yolo.dry_run.label',
  ]) {
    assert.ok(html.includes(enXA[key]), `pseudo-locale renders ${key}`);
  }
  for (const englishLeak of [
    'Global color theme', 'Editor/Terminal font sizes are in Terminal / Editor.', 'Client pull: latency ping', 'Notification throttle',
    'Terminal scrollback', 'Upload size cap', 'YO!agent backend', 'Dry run',
  ]) {
    assert.equal(html.includes(englishLeak), false, `no plain-English "${englishLeak}" leaks under the pseudo-locale`);
  }
});

test('t@7654', () => {
  // "then Chinese": zh-Hant + zh-Hans catalogs localize the WHOLE Preferences panel, and the
  // language select offers both endonym-labeled (Traditional before Simplified).
  const api = loadYolomux('', ['1']);
  // The select offers the two Chinese options in their own script, Traditional listed before Simplified.
  const selectHtml = api.preferencesPanelHtmlForTest('language');
  assert.ok(selectHtml.includes('<option value="zh-Hant"'), 'language select offers Traditional Chinese');
  assert.ok(selectHtml.includes('<option value="zh-Hans"'), 'language select offers Simplified Chinese');
  assert.ok(selectHtml.includes('>繁體中文</option>') && selectHtml.includes('>简体中文</option>'), 'Chinese options use endonym labels');
  assert.ok(selectHtml.indexOf('value="zh-Hant"') < selectHtml.indexOf('value="zh-Hans"'), 'Traditional Chinese is listed before Simplified');
  for (const locale of ['zh-Hant', 'zh-Hans']) {
    const catalog = JSON.parse(fs.readFileSync(`static/locales/${locale}.json`, 'utf8'));
    // Same key set as English (the build enforces this; assert it here too).
    assert.deepStrictEqual(new Set(Object.keys(catalog)), new Set(Object.keys(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')))), `${locale} has the same keys as en`);
    api.i18nSetCatalogForTest(locale, catalog);
    api.setActiveLocaleForTest(locale);
    const zhHtml = api.preferencesPanelHtmlForTest('');
    assert.ok(zhHtml.includes(catalog['pref.appearance.theme.label']), `${locale} renders the localized global-theme label`);
    assert.ok(zhHtml.includes(catalog['pref.appearance.date_time_hour_cycle.label']), `${locale} renders the localized date/time clock label`);
    assert.ok(zhHtml.includes(catalog['pref.section.yoagent']), `${locale} renders the localized YO!agent section title`);
    // Brand glyph: YO!agent localizes to 優!助手 / 优!助手 (no plain "YO!agent" section title leak).
    assert.ok(catalog['pref.section.yoagent'].includes(locale === 'zh-Hant' ? '優!助手' : '优!助手'), `${locale} applies the YO!agent brand glyph`);
    // The YO marker glyph localizes to 優 / 优 (the catalog value the marker renders via t('brand.marker')).
    assert.equal(catalog['brand.marker'], locale === 'zh-Hant' ? '優' : '优', `${locale} marker glyph`);
    // #52: the wordmark glyphs localize to 優樂 / 优乐.
    assert.equal(catalog['brand.wordmark.yo'], locale === 'zh-Hant' ? '優' : '优', `${locale} wordmark YO glyph`);
    assert.equal(catalog['brand.wordmark.lo'], locale === 'zh-Hant' ? '樂' : '乐', `${locale} wordmark LO glyph`);
    // The user's request: YO!info -> 優!資料 / 优!资料, YO!agent -> 優!助手 / 优!助手.
    assert.equal(catalog['brand.tab.info'], locale === 'zh-Hant' ? '優!資料' : '优!资料', `${locale} YO!info tab label`);
    assert.equal(catalog['brand.tab.agent'], locale === 'zh-Hant' ? '優!助手' : '优!助手', `${locale} YO!agent tab label`);
    const localizedDate = api.sessionFileTimeText(Date.UTC(2026, 5, 4, 19, 17) / 1000);
    assert.equal(localizedDate.includes('Jun'), false, `${locale} Finder date does not leak the English month name`);
    assert.ok(/[年月日]/.test(localizedDate), `${locale} Finder date uses Chinese date wording`);
    assert.equal(/上午|下午|[AP]\.?M\.?/i.test(localizedDate), false, `${locale} Finder date defaults to a 24-hour clock`);
    assert.ok(/\d{2}:\d{2}/.test(localizedDate), `${locale} Finder date includes a two-digit clock`);
    assert.equal(api.fileExplorerTreeDateModeLabel('none'), catalog['finder.dateMode.none'], `${locale} Finder/Differ None date-mode button is localized`);
    assert.equal(api.fileExplorerTreeDateModeButtonLabel('none'), catalog['finder.dateMode.date'], `${locale} Finder/Differ None date-mode button shows localized crossed-out Date`);
    assert.equal(api.fileExplorerTreeDateModeLabel('date'), catalog['finder.dateMode.date'], `${locale} Finder/Differ Date date-mode button is localized`);
    assert.equal(api.fileExplorerTreeDateModeLabel('relative'), catalog['finder.dateMode.relative'], `${locale} Finder/Differ Ago date-mode button is localized`);
    assert.equal(api.fileExplorerTreeDateModeTitle('relative').includes('None'), false, `${locale} Finder/Differ date-mode tooltip does not leak English None`);
    assert.equal(api.fileExplorerTreeDateModeTitle('relative').includes('Date display'), false, `${locale} Finder/Differ date-mode tooltip does not leak English title text`);
    assert.equal(api.sessionFileRelativeTimeText(1000, 19720), catalog['relative.compact.hour.other'].replace('{count}', '5.2'), `${locale} Finder/Differ compact Ago text is localized`);
    assert.equal(/\bago\b|hrs?|days?|min\b/i.test(api.sessionFileRelativeTimeText(1000, 217000)), false, `${locale} Finder/Differ compact Ago text does not leak English units`);
    // The YOLO-toggle menu labels + the YOLO submenu header use the localized brand glyph (優/优 and
    // 優樂/优乐), not a Latin "YO"/"YOLO" (images #57 / #59).
    const glyph = locale === 'zh-Hant' ? '優' : '优';
    for (const k of ['menu.tmux.yo.on', 'menu.tmux.yo.off', 'menu.tmux.yo.elsewhere', 'menu.tmux.yo.none', 'menu.tmux.yoloSubmenu']) {
      assert.equal(/[A-Za-z]/.test(catalog[k]), false, `${locale} ${k} has no Latin "YO" leak`);
      assert.ok(catalog[k].startsWith(glyph), `${locale} ${k} leads with the localized brand glyph`);
    }
    // #54: the System theme option is bilingual (localized + "/System") so the OS-following option is
    // unambiguous in any locale; Dark/Light stay fully localized.
    assert.ok(catalog['pref.appearance.theme.system'].endsWith('/System'), `${locale} System theme option is bilingual`);
    assert.equal(catalog['pref.appearance.theme.dark'].includes('/'), false, `${locale} Dark theme option stays fully localized`);
    for (const englishLeak of ['Global color theme', 'Upload size cap', 'Terminal scrollback']) {
      assert.equal(zhHtml.includes(englishLeak), false, `${locale}: no plain-English "${englishLeak}" leaks`);
    }
  }
});

test('t@7714', () => {
  // "Language" is the FIRST General preference and its label is "Language" (not "UI language").
  const api = loadYolomux('', ['1']);
  const enCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
  assert.equal(enCatalog['pref.general.language.label'], 'Language', '#51: the language label reads "Language"');
  api.setActiveLocaleForTest('en');
  const generalHtml = api.preferencesPanelHtmlForTest('');
  assert.ok(generalHtml.includes('data-setting-path="general.language"'), '#51: the language field is present');
  assert.ok(generalHtml.indexOf('data-setting-path="general.language"') < generalHtml.indexOf('data-setting-path="general.auto_focus"'), '#51: the language field is the first General row (before auto-focus)');
});

test('t@7725', () => {
  // startup helper tips are a persisted General preference, rotate serially through
  // localStorage, use the shared toast path, and do not render for readonly users.
  const api = loadYolomux('', ['1']);
  api.setActiveLocaleForTest('en');
  const tips = api.startupHelperCatalog();
  assert.equal(tips.length, 14, 'startup helper catalog includes the initial tip set');
  assert.ok(tips.some(tip => tip.title === 'Drag files into terminals'), 'startup helper catalog includes file drag/drop');
  assert.ok(tips.some(tip => tip.title === 'Ask YO!agent for direction'), 'startup helper catalog includes YO!agent guidance');
  assert.ok(tips.some(tip => tip.title === 'Review agent changes'), 'startup helper catalog includes Differ');
  assert.equal(api.readStartupHelperIndex(tips.length), 0, 'startup helper index defaults to first tip');
  api.writeStartupHelperIndex(15);
  assert.equal(api.readStartupHelperIndex(tips.length), 1, 'startup helper index wraps by catalog length');
  const generalHtml = api.preferencesPanelHtmlForTest('');
  assert.ok(generalHtml.includes('data-setting-path="general.startup_tips"'), 'Startup Tips setting renders in General');
  assert.ok(generalHtml.includes('Startup Tips'), 'Startup Tips setting uses Tips wording');
  assert.ok(generalHtml.indexOf('data-setting-path="general.auto_focus"') < generalHtml.indexOf('data-setting-path="general.startup_tips"'), 'Startup Tips setting follows Auto-focus');
  const src = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
  assert.ok(src.includes("if (readOnlyMode || !startupHelpersEnabled) return null;"), 'startup helper does not render in readonly or disabled mode');
  assert.ok(src.includes("writeStartupHelperIndex((index + 1) % tips.length)"), 'startup helper advances the localStorage index when shown');
  assert.ok(src.includes("showStartupHelperTip({manual: true})"), 'Next tip action shows the next helper');
  assert.ok(src.includes("saveSettingsPatch(settingPatch('general.startup_tips', false))"), 'Turn off forever persists the General setting');
  assert.ok(src.includes('startupHelperPromptTitle(index, tips.length, tip)'), 'startup helper title includes tip number, total, and action prompt');
  assert.ok(src.includes('container: displayToastContainer(focusedPanelItem)'), 'startup helper renders in the focused pane toast stack, below pane tabs');
  assert.equal(src.includes("startupHelper.action.hide"), false, 'startup helper relies on the toast X instead of a duplicate Hide action');
  assert.ok(src.includes('actions: [navAction, offAction]'), 'startup helper actions are nav plus Turn off Tips forever');
  assert.ok(src.includes("startupHelperAction('<', () => showRelativeTip(-1)"), 'startup helper has a previous-tip arrow control');
  assert.ok(src.includes("startupHelperAction('>', () => showRelativeTip(1)"), 'startup helper has a next-tip arrow control');
  assert.ok(src.includes('countdownMs: 45000'), 'Startup Tips stay visible for 45 seconds');
  const helperStart = src.indexOf('function showStartupHelperTip');
  const helperEnd = src.indexOf('function scheduleStartupHelperTip');
  assert.ok(helperStart >= 0 && helperEnd > helperStart, 'startup helper function block is present');
  assert.equal(src.slice(helperStart, helperEnd).includes('.focus('), false, 'startup helper code does not steal focus');
  const helperCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
  assert.ok(/\.panel-toast-stack \.startup-helper-toast\s*\{[\s\S]*?align-self:\s*flex-end/.test(helperCss), 'startup helper toast is pane-local and right-aligned below the pane tab strip');
  assert.ok(/\.startup-helper-nav\s*\{[\s\S]*?display:\s*inline-flex/.test(helperCss), 'startup helper navigation is a compact arrow group');
  const bootSrc = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
  assert.ok(/installRuntimeIntervals\(\);\s*scheduleStartupHelperTip\(\);/.test(bootSrc), 'startup helper is scheduled after initial boot intervals');
  assert.ok(src.includes("if (location.protocol === 'file:') return;"), 'startup helper is skipped in file:// browser fixtures');
  const settingsSrc = fs.readFileSync('yolomux_lib/settings.py', 'utf8');
  assert.ok(settingsSrc.includes('"startup_tips": True'), 'Startup Tips setting defaults on server-side');
  assert.ok(settingsSrc.includes('"startup_helpers" in incoming'), 'legacy startup_helpers configs migrate to startup_tips');
});

test('t@7769', () => {
  // User screenshot 20260608-004: pane tabs should sit tight to the pane border and to each other.
  const tokenCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
  const css = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
  const popoverCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
  assert.ok(/\.panel-head\s*\{[\s\S]*?padding:\s*2px 1px 0;/.test(css), 'pane tab strip has a 1px left/right edge gap');
  assert.ok(/\.pane-tab\s*\{[\s\S]*?margin:\s*0 1px 0 0;/.test(css), 'pane tabs have a 1px horizontal gap');
  assert.ok(/\.yolomux-dockview \.dv-tabs-container\s*\{[\s\S]*?flex-wrap:\s*wrap/.test(css), 'Dockview pane tabs keep the old wrapping YOLOmux tab-strip behavior');
  assert.ok(/\.yolomux-dockview \.dv-tabs-container\s*\{[\s\S]*?padding-inline-end:\s*var\(--dockview-header-actions-reserved-inline-size,\s*0px\)/.test(css), 'Dockview tab strips reserve measured space for the overlaid right-side action buttons');
  assert.ok(/\.yolomux-dockview \.dv-tab\s*\{[\s\S]*?flex:\s*0 0 min\(var\(--dockview-tab-inline-size,\s*var\(--pane-tab-width\)\),\s*100%\)/.test(css), 'Dockview pane tabs use the measured header width while keeping the normal pane-tab width as the default');
  assert.ok(/\.yolomux-dockview \.dv-tab > \.dockview-pane-tab\s*\{[\s\S]*?border-radius:\s*6px 6px 0 0/.test(css), 'Dockview active tabs keep the old rounded top corners');
  assert.ok(/\.yolomux-dockview \.dv-groupview\s*\{[\s\S]*?border:\s*0;/.test(css), 'Dockview groups do not add a fat pane-spacing border around the skinny sash separator');
  assert.ok(/\.yolomux-dockview \.dv-groupview\s*\{[\s\S]*?padding:\s*var\(--pane-split-gap\);/.test(css), 'Dockview groups reserve pane-spacing width inside the active ring so terminals do not render under it');
  assert.ok(/\.yolomux-dockview \.dv-groupview::after\s*\{[\s\S]*?border:\s*var\(--pane-split-gap\) solid color-mix\(in srgb, var\(--panel-ring-color\) var\(--panel-ring-opacity\), transparent\)/.test(css), 'Dockview groups draw the active surround as a pane-spacing-width pseudo-ring without thickening the sash');
  assert.ok(/\.yolomux-dockview \.dv-groupview:has\(\.panel\.active-pane\),[\s\S]*?\.dv-groupview:has\(\.panel\.typing-ready-pane\)\s*\{[\s\S]*?--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(css), 'Dockview active/typing panes feed the same active ring color into the group pseudo-ring');
  assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\s*\{[\s\S]*?border-width:\s*0;/.test(css), 'Dockview-mounted panes do not keep the legacy pane-spacing border');
  assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\.dockview-inner-head-collapsed\s*\{[\s\S]*?grid-template-rows:\s*auto minmax\(0,\s*1fr\)/.test(css), 'Dockview-mounted panes switch from header/detail/content rows to detail/content rows when the inner header is hidden');
  assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel > \.panel-head\.dockview-inner-head-hidden,[\s\S]*?\.panel-head\[hidden\]\s*\{[\s\S]*?display:\s*none;/.test(css), 'Dockview hidden inner pane headers really stop rendering instead of leaving a green band');
  assert.ok(/\.yolomux-dockview \.dv-tab\.dv-inactive-tab > \.dockview-pane-tab:not\(\.active\)\s*\{[\s\S]*?background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(css), 'Dockview inactive tabs match the pane tab-strip background');
  assert.ok(/\.yolomux-dockview \.dockview-pane-header-actions \.pane-drag-handle\s*\{[\s\S]*?cursor:\s*grab/.test(css), 'Dockview exposes a compact whole-pane drag handle in the header actions');
  assert.ok(/\.yolomux-dockview \.dockview-pane-header-actions \.tab\s*\{[\s\S]*?height:\s*min\(18px,\s*var\(--pane-tab-height\)\)/.test(css), 'Dockview header action buttons stay compact instead of growing taller than the tab row');
  assert.ok(/\.yolomux-dockview \.dv-split-view-container > \.dv-sash-container > \.dv-sash,[\s\S]*?\.dv-sash:not\(\.disabled\):hover,[\s\S]*?\.dv-sash:not\(\.disabled\):active\s*\{[\s\S]*?background-color:\s*transparent;/.test(css), 'Dockview sash hit targets stay transparent so only the skinny pseudo-line is visible');
  assert.ok(/\.yolomux-dockview \.dv-sash::before\s*\{[\s\S]*?background:\s*var\(--pane-resizer-bg\)/.test(css), 'Dockview sashes draw the shared skinny pane separator at rest');
  assert.ok(/\.yolomux-dockview \.dv-split-view-container\.dv-horizontal > \.dv-sash-container > \.dv-sash::before\s*\{[\s\S]*?left:\s*calc\(50% - \(var\(--pane-resizer-line-size\) \/ 2\)\)/.test(css), 'Dockview horizontal sashes center the 1px resting separator');
  assert.ok(/\.yolomux-dockview \.dv-split-view-container\.dv-horizontal > \.dv-sash-container > \.dv-sash:hover::before,[\s\S]*?var\(--pane-resizer-hover-line-size\)/.test(css), 'Dockview horizontal sashes thicken only to the shared hover separator size');
  assert.ok(css.includes('--dv-drag-over-border: 2px dashed var(--pane-resizer-hover-bg)'), 'Dockview drag overlays use the configurable pane separator color');
  assert.ok(tokenCss.includes('--tab-insert-preview-width: 24px'), 'tab insertion previews use a large enough between-tabs box to see while dragging');
  assert.ok(/\.grid\.drop-preview::before\s*\{[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\)/.test(css), 'root tab-drag previews use the configurable pane separator color');
  assert.ok(/\.yolomux-dockview \.dv-groupview\.drop-preview::before/.test(css), 'Dockview panes can draw the shared dashed file/tab drop preview');
  assert.ok(/\.pane-tabs\.tab-drop-preview::after\s*\{[\s\S]*?width:\s*var\(--tab-insert-preview-width\);[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\)/.test(css), 'legacy tab insertion previews render as a visible dashed between-tabs box');
  assert.ok(/\.yolomux-dockview \.dv-tab\.dv-drop-target \.dv-drop-target-selection\.dv-drop-target-left,[\s\S]*?\.dv-drop-target-selection\.dv-drop-target-right\s*\{[\s\S]*?width:\s*var\(--tab-insert-preview-width\) !important;[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\) !important/.test(css), 'Dockview tab insertion previews render as a visible dashed between-tabs box instead of a half-tab overlay');
  assert.ok(/\.pane-drag-image\.drag-image\s*\{[\s\S]*?border:\s*2px dotted var\(--pane-resizer-hover-bg\)/.test(popoverCss), 'whole-pane drag preview renders as a dotted box using the shared separator color');
  assert.ok(/\.yolomux-dockview \.dv-tab\.dv-drop-target \.dv-drop-target-selection\.dv-drop-target-right\s*\{[\s\S]*?left:\s*100% !important;[\s\S]*?translateX\(-50%\)/.test(css), 'Dockview right-side tab insertion marker is centered on the target tab edge');
  const dockviewSrc = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
  assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*rootBoundaryDropZoneForEvent\(nativeEvent, rect\)[\s\S]*splitSessionAtLayoutBoundary\(rootIntent\.item, rootIntent\.zone, rootIntent\.sourceSlot\)/.test(dockviewSrc), 'Dockview content-edge drops in the root band use the legacy full-span boundary split');
  assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*event\?\.kind !== 'content' && event\?\.kind !== 'edge'/.test(dockviewSrc), 'Dockview edge overlays in the app root band use the bounded YOLOmux root preview instead of the native full-width overlay');
  assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*event\.kind === 'content' && event\.group && !dockviewContentDropCanUseRootBoundary\(nativeEvent, zone\)[\s\S]*return null/.test(dockviewSrc), 'Dockview pane-content drops keep the native local group split unless the pointer is on a root-edge cross-gutter');
  assert.ok(/function dockviewContentDropCanUseRootBoundary\(event, zone\)[\s\S]*const crossSplit = zone === 'left' \|\| zone === 'right' \? 'column' : 'row'[\s\S]*Math\.abs\(pointer - boundary\) <= tolerance/.test(dockviewSrc), 'Dockview root-boundary content drops are limited to the cross-gutter between existing panes');
  assert.ok(/function dockviewContentDropCanUseRootBoundary\(event, zone\)[\s\S]*const tolerance = Math\.max\(48, layoutBoundaryDropBandPx/.test(dockviewSrc), 'Dockview outer-edge drops beside stacked panes use a usable cross-gutter tolerance without stealing normal pane-edge drops');
  assert.ok(/function dockviewPaneContentDropInfo\(event\)[\s\S]*targetSlot[\s\S]*targetRect: layoutSlotScreenRect\(targetSlot\)[\s\S]*function dockviewPaneContentDropIntent\(event\)[\s\S]*dropIntentAllowsSession\(info\.item, info\.intent\)/.test(dockviewSrc), 'Dockview pane-content edge drops are converted to YOLOmux local pane split intents with real target geometry');
  assert.ok(/function dockviewShouldSuppressPaneContentDrop\(event\)[\s\S]*!dropIntentAllowsSession\(info\.item, info\.intent\)[\s\S]*function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewShouldSuppressPaneContentDrop\(event\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview suppresses native previews for invalid pane drops before a dashed box is advertised');
  assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const rootIntent = dockviewRootBoundaryDropIntent\(event\)[\s\S]*const paneIntent = dockviewPaneContentDropIntent\(event\)[\s\S]*splitSessionAtSlot\(paneIntent\.item, paneIntent\.targetSlot, paneIntent\.zone, paneIntent\.sourceSlot\)/.test(dockviewSrc), 'Dockview pane edge drops use splitSessionAtSlot so same-axis splits preserve 1/2 + 1/4 + 1/4 sizing');
  assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*rootBoundaryDropOverDockedFileExplorer\(nativeEvent, zone\)[\s\S]*return null/.test(dockviewSrc), 'Dockview root top/bottom previews defer when the pointer is inside the docked Finder/Differ column');
  assert.ok(/function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewShowRootBoundaryPreview\(intent\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview root-band drags show the bounded YOLOmux preview and suppress the native full-width Dockview overlay');
  assert.ok(dockviewSrc.includes('createRightHeaderActionComponent: () => createDockviewHeaderActionsRenderer()'), 'Dockview renders YOLOmux pane controls in the Dockview header row');
  assert.ok(/function dockviewLayoutToHost[\s\S]*api\.layout\?\.\(width, height\)/.test(dockviewSrc), 'Dockview is explicitly laid out to the host size instead of staying at the default 100px shell');
  assert.ok(/function hideDockviewInnerPaneTabs\(panel\)[\s\S]*panel\.classList\.add\('dockview-inner-head-collapsed'\)/.test(dockviewSrc), 'Dockview marks panels whose inner header was hidden so their content row still fills the pane');
  assert.ok(/function preserveDockviewDockedFileExplorerSplit[\s\S]*dockviewLayoutState\.reloadAfterAdoption = true/.test(dockviewSrc), 'Dockview adoption preserves and reapplies the docked Finder root split width');
  assert.ok(/function dockviewInstallFileDropBridge[\s\S]*dockviewHandleFileDragOver[\s\S]*dockviewHandleFileDrop/.test(dockviewSrc), 'Dockview panes bridge Finder/Differ file drags into the shared pane drop behavior');
  assert.ok(/function dockviewHandleFileDrop[\s\S]*openDraggedFilesInEditor\(payload, \{targetSlot: intent\.targetSlot, targetZone: intent\.zone\}\)/.test(dockviewSrc), 'Dockview file drops open dragged files in the intended pane split');
  assert.ok(/function dockviewHandleFileDragOver\(event\)[\s\S]*paneDragPayload\(event\)[\s\S]*paneSwapIntentForEvent\(event, panePayload\.slot\)[\s\S]*showDropPreview\(intent\)/.test(dockviewSrc), 'Dockview host dragover handles whole-pane swap previews separately from tab drags');
  assert.ok(/function dockviewHandleFileDrop\(event\)[\s\S]*paneDragPayload\(event\)[\s\S]*swapPaneSlots\(intent\.sourceSlot, intent\.targetSlot\)/.test(dockviewSrc), 'Dockview host drops swap whole panes when the pane payload is accepted');
  assert.ok(/function paneDragHandleHtml\(item\)[\s\S]*data-pane-drag=/.test(dockviewSrc), 'Dockview header actions include a dedicated pane-drag payload handle');
  assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.dv-tabs-and-actions-container[\s\S]*pane-drag-source[\s\S]*dockviewBeginPanePointerDrag\(event, sourceSlot\)/.test(dockviewSrc), 'Dockview tab-container background starts whole-pane drags without marking the tab container draggable');
  assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.panel-detail-row[\s\S]*syncDragSource\(detail\)/.test(dockviewSrc), 'Dockview pane info/detail rows start the same whole-pane pointer drag as the tab-container background');
  assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.file-editor-toolbar[\s\S]*syncDragSource\(editorToolbar\)/.test(dockviewSrc), 'Dockview editor toolbars start the same whole-pane pointer drag as other pane info bars');
  assert.ok(/function dockviewSyncHeaderActionReservations\(\)[\s\S]*--dockview-header-actions-reserved-inline-size[\s\S]*reservedWidth[\s\S]*--dockview-tab-inline-size/.test(dockviewSrc), 'Dockview measures the right-side action buttons and tab width so crowded tabs do not collide or spill past two rows');
  assert.ok(/function dockviewTrackPanePointerDrag\(event\)[\s\S]*startPaneDragPreview\(event, state\.sourceSlot\)[\s\S]*moveCustomDragPreview\(event\)/.test(dockviewSrc), 'Dockview pane-background pointer drags show and move the same pane drag preview as native pane drags');
  assert.ok(/function dockviewFinishPanePointerDrag\(event\)[\s\S]*stopCustomDragPreview\(\)[\s\S]*clearDropPreview\(\)/.test(dockviewSrc), 'Dockview pane-background pointer drags remove the pane preview on drop/cancel');
  const dragPreviewSrc = fs.readFileSync('static_src/js/yolomux/60_popovers_tabs.js', 'utf8');
  assert.ok(/const customDragPreviewCleanupEvents = \['drop', 'dragend', 'pointerup', 'mouseup', 'blur', 'visibilitychange'\]/.test(dragPreviewSrc), 'native custom drag previews clean up on drag release and page-cancel paths');
  assert.ok(/function bindCustomDragPreviewListeners\(\)[\s\S]*for \(const target of customDragPreviewEventTargets\(\)\)[\s\S]*target\.addEventListener\?\.\('dragover', moveCustomDragPreview, true\)[\s\S]*target\.addEventListener\?\.\(eventName, stopCustomDragPreview, true\)/.test(dragPreviewSrc), 'native custom drag preview cleanup is bound on both document and window');
  assert.equal(/header\.draggable = draggable/.test(dockviewSrc), false, 'Dockview tab-container background must not become a native draggable ancestor that steals tab drags');
  assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const edgeReorder = dockviewTabEdgeReorderIntent\(event\)[\s\S]*moveSessionToSlot\(edgeReorder\.item, edgeReorder\.targetSlot, edgeReorder\.sourceSlot, edgeReorder\.insertIndex\)/.test(dockviewSrc), 'Dockview manually reorders edge tabs dragged onto their adjacent neighbor');
  assert.ok(/function dockviewInstallTabPointerReorderFallback\(\)[\s\S]*document\.addEventListener\('pointerup', finish, true\)[\s\S]*document\.addEventListener\('mouseup', finish, true\)/.test(dockviewSrc), 'Dockview edge-tab reorder fallback listens to both pointer and mouse release paths');
  assert.ok(/function dockviewFinishTabPointerDrag\(event\)[\s\S]*dockviewTabForPoint[\s\S]*dockviewAdjacentEdgeTabInsertIndex[\s\S]*moveSessionToSlot\(state\.item, targetSlot, targetSlot, currentInsertIndex\)/.test(dockviewSrc), 'Dockview edge-tab pointer fallback reorders against the tab under the release point');
  assert.ok(/\.pane-tab > \.session-popover,\s*\.pane-tab-detached-popover\s*\{[\s\S]*position:\s*fixed/.test(css), 'Dockview tab hover popovers use the shared fixed-position tab popover surface');
  assert.ok(/function bindPaneTabPopover\(tab, session\)[\s\S]*tab\.classList\?\.contains\('dockview-pane-tab'\)[\s\S]*detachPaneTabPopover\(tab, popover\)/.test(fs.readFileSync('static_src/js/yolomux/80_panes_preferences.js', 'utf8')), 'Dockview tab hover popovers detach from the clipped Dockview tab scroller');
  assert.ok(/function preserveDockviewDockedFileExplorerSplit\(next, previous = layoutSlots\)[\s\S]*dockviewLayoutContentSignature\(next\) === dockviewLayoutContentSignature\(previous\)[\s\S]*return/.test(dockviewSrc), 'Dockview lets sash-only Finder/Differ resize updates change the root split pct');
  assert.ok(/function preserveDockviewContentSplitPercentagesAfterDockResize\(nextRoot, previousRoot, nextDocked, previousDocked\)[\s\S]*copyLayoutSplitPercentagesByShape\(nextContent, previousContent\)[\s\S]*reloadAfterAdoption = true/.test(dockviewSrc), 'Dockview Finder/Differ sash resize preserves nested content split percentages while the root pct changes');
  assert.ok(/function copyLayoutSplitPercentagesByShape\(target, source\)[\s\S]*target\.pct = sourcePct[\s\S]*copyLayoutSplitPercentagesByShape\(targetChildren\[index\], sourceChildren\[index\]\)/.test(dockviewSrc), 'Dockview content pct preservation recurses through matching nested split shapes');
  assert.ok(/function dockviewLayoutContentSignature\(slots = layoutSlots\)[\s\S]*nodeSignature[\s\S]*paneSignature/.test(dockviewSrc), 'Dockview compares content/topology separately from split percentages before preserving Finder width');
  const layoutActionSrc = fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8');
  assert.ok(/function layoutNodeScreenRect\(layoutNode\)[\s\S]*\.map\(slot => layoutSlotScreenRect\(slot\)\)/.test(layoutActionSrc), 'Docked Finder preview geometry uses slot screen rects that work for Dockview groups');
  assert.ok(/function layoutSlotScreenRect\(slot\)[\s\S]*\.dockview-panel-content > \.panel\[data-slot=/.test(layoutActionSrc), 'Dockview layout slots can resolve their visible group rectangle');
  assert.ok(/function rootBoundaryDropIntentForEvent\(event\)[\s\S]*rootBoundaryDropOverDockedFileExplorer\(event, zone\)[\s\S]*return null/.test(layoutActionSrc), 'legacy root top/bottom previews also defer inside a docked Finder/Differ column');
  assert.ok(/function fileDropIntentAllowsPayload\(payload, intent\)[\s\S]*dropIntentAllowsSession\(item, intent, \{allowCandidate: true\}\)/.test(layoutActionSrc), 'file drag previews use the same pane/Finder/min-size validator as tab drags');
  assert.ok(/function itemCanSplitSinglePurposePane\(item, intent\)[\s\S]*zone !== 'bottom'[\s\S]*return false[\s\S]*dropIntentHasRoomForItem\(item, intent\)/.test(layoutActionSrc), 'Finder/Differ target panes accept only bottom splits and only when the resulting pane can fit');
  assert.ok(/function dropIntentHasRoomForItem\(item, intent\)[\s\S]*minWidthForLayoutItem\(targetItem\)[\s\S]*targetMinWidth \+ itemMinWidth[\s\S]*targetMinHeight \+ itemMinHeight/.test(layoutActionSrc), 'pane drop previews are suppressed when the target is too small for both resulting panes');
});

test('t@7847', () => {
  // Pop-out previews must derive readable light-editor text inside their own document; copied inline
  // aliases like --text/--editor-scheme-fg override the pop-out's editor-theme-light remap.
  const source = (fs.readFileSync('static_src/js/yolomux/90_changes_editor.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/95_codemirror_editor.js', 'utf8'));
  const start = source.indexOf('function previewPopoutVariableStyle()');
  const end = source.indexOf('function previewPopoutToolbarHtml()');
  assert.ok(start >= 0 && end > start, 'previewPopoutVariableStyle exists');
  const variableBlock = source.slice(start, end);
  assert.equal(variableBlock.includes("'--text'"), false, 'preview pop-out does not copy --text inline');
  assert.ok(variableBlock.includes("['--editor-scheme-fg', '--popout-editor-scheme-fg']"), 'preview pop-out aliases active editor text instead of copying it onto --text');
  assert.ok(variableBlock.includes("'--code-keyword'") && variableBlock.includes("'--code-control'") && variableBlock.includes("'--code-string'"), 'preview pop-out copies syntax token variables for highlighted fenced code');
  assert.ok(source.includes('.file-preview-popout-window.editor-theme-light .markdown-body pre'), 'preview pop-out has light-theme code block rules outside .file-editor-content');
  assert.ok(source.includes('.file-preview-popout-window .markdown-body'), 'preview pop-out sets readable body text in its standalone document');
  assert.ok(/\.file-preview-popout-title\s*\{[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto minmax\(0,\s*1fr\)/.test(source), 'preview pop-out top bar uses left/title, centered font, and right theme zones');
  assert.ok(/\.file-preview-popout-title\s*\{[\s\S]*position:\s*fixed[\s\S]*z-index:\s*1000/.test(source), 'preview pop-out top bar stays fixed above the preview body while scrolling');
  assert.ok(/\.file-preview-popout-shell\s*\{[\s\S]*width:\s*100%[\s\S]*padding:\s*64px 24px 36px/.test(source), 'preview pop-out shell uses the full window width and reserves space below the fixed top bar');
  assert.equal(/\.file-preview-popout-shell\s*\{[\s\S]*width:\s*min\(/.test(source), false, 'preview pop-out content is not capped at a fixed desktop width');
  assert.ok(/<span class="file-preview-popout-title-path">[\s\S]*\$\{previewPopoutToolbarHtml\(\)\}/.test(source), 'preview pop-out header renders the path before the shared pop-out toolbar controls');
  assert.ok(/function previewPopoutToolbarHtml\(\)[\s\S]*file-editor-preview-font-panel[\s\S]*class="file-editor-theme-panel" data-preview-popout-theme/.test(source), 'preview pop-out toolbar renders font selector before the theme selector');
  assert.ok(/updateEditorThemeButton\(themeButton, \{includeVanilla: true\}\)/.test(source), 'preview pop-out theme selector includes vanilla mode');
  assert.ok(/cycleEditorThemeMode\(\{includeVanilla: true\}\)/.test(source), 'preview pop-out theme click cycles dark/light/vanilla');
  assert.ok(source.includes('min-width: 66px;') && source.includes('width: auto;'), 'preview pop-out theme selector leaves room for the visible Dark/Bright/Vanilla label');
  assert.ok(fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8').includes('.file-editor-theme-panel.theme-with-label::after'), 'preview theme selector renders the current mode label instead of hiding vanilla in the tooltip');
  assert.ok(source.includes('applyMarkdownFenceFallbackHighlight(block);'), 'markdown fenced code falls back to editor syntax highlighting when hljs lacks the language');
  assert.ok(source.includes("window.open(`/preview-popout?path=${encodeURIComponent(path)}`"), 'preview pop-out opens a same-origin URL instead of about:blank');
  assert.ok(/file-editor-popout-preview-panel'\)\?\.addEventListener\('click'[\s\S]*if \(openFilePreviewPopout\(path, panel\)\) \{[\s\S]*setFileEditorViewMode\(path, 'edit', item\);[\s\S]*renderFileEditorPanel\(panel, item\);/.test(source), 'pressing Pop-out opens the preview window and returns the in-pane editor to Edit mode');
  assert.ok(/function openFilePreviewPopout\(path, panel = null\)[\s\S]*return true;[\s\S]*return false;/.test(source), 'preview pop-out open path reports whether a pop-out was actually opened or focused');
  assert.ok(/previewWindow\._yolomuxPreviewControlsCleanup[\s\S]*bind\(previewWindow, 'scroll', syncScroll\)[\s\S]*bind\(previewWindow, 'wheel', scheduleScrollSync\)[\s\S]*bind\(scroller, 'scroll', syncScroll\)[\s\S]*bind\(scroller, 'wheel', scheduleScrollSync\)/.test(source), 'preview pop-out window and scrolling element sync immediately on scroll and schedule next-frame sync on wheel without stale document listeners');
  assert.ok(/function scrollSyncTargetPosition\(from, to, axis = 'top'\)[\s\S]*const edgeSnap = Math\.max\(2, Math\.ceil\(sourceClient \* 0\.01\)\);[\s\S]*if \(maxTo <= 0 \|\| current <= edgeSnap\) return 0;[\s\S]*if \(maxFrom <= edgeSnap \|\| current >= maxFrom - edgeSnap\) return maxTo;[\s\S]*const sourceCenter = Math\.min\(maxFrom, current\) \+ \(sourceClient \/ 2\);[\s\S]*return Math\.min\(maxTo, Math\.max\(0, target\)\);/.test(source), 'pop-out scroll sync aligns viewport centers with fractional precision and explicit edge snaps');
  assert.ok(/function syncFilePreviewPopoutFromPanel[\s\S]*syncScrollPositionByRatio\(from, scroller\)/.test(source), 'editor-to-popout scroll sync uses the shared proportional mapper');
  assert.ok(/function syncFilePreviewPopoutScroll[\s\S]*syncScrollPositionByRatio\(scroller, editorScroller\)[\s\S]*syncScrollPositionByRatio\(scroller, previewPane\)/.test(source), 'popout-to-editor scroll sync uses the shared proportional mapper');
  assert.ok(/function fileEditorSourceElement\(panel, source\)\s*\{[\s\S]*fileEditorPanelMode\(panel\) === 'diff'[\s\S]*return null/.test(source), 'Differ views are not preview-scroll sources');
  assert.ok(/function syncFilePreviewPopoutScroll[\s\S]*mode !== 'diff' && editorScroller[\s\S]*syncScrollPositionByRatio\(scroller, editorScroller\)/.test(source), 'preview pop-out scrolling does not drive Differ editors');
  assert.ok(/function scheduleFilePreviewPopoutScrollSync\(path, previewWindow, options = \{\}\)[\s\S]*requestAnimationFrame\(run\)/.test(source), 'pop-out wheel/scroll sync is coalesced through requestAnimationFrame for smooth trackpad deltas');
  assert.ok(/function syncFileEditorInPaneSplitScroll\(host, source\)[\s\S]*return syncScrollPositionByRatio\(from, to\);/.test(source), 'split Preview scroll sync uses the same fractional center/edge mapper as pop-out preview');
  const splitSyncStart = source.indexOf('function syncFileEditorInPaneSplitScroll');
  const splitSyncBody = source.slice(splitSyncStart, source.indexOf('\nfunction ', splitSyncStart + 1));
  assert.equal(/previewSourceLineForScroll|scrollPreviewToSourceLine|scrollIntoView/.test(splitSyncBody), false, 'split Preview scroll sync does not jump by source-line anchors');
  assert.ok(/function fileEditorScrollSyncBlocked\(panel, source = ''\)[\s\S]*panel\?\._splitScrollSource !== source/.test(source), 'split Preview scroll guard suppresses only the opposite/programmatic side');
  assert.ok(/function setFileEditorScrollSyncGuardForSource\(source, \.\.\.panels\)[\s\S]*panel\._splitScrollSource = source \|\| ''/.test(source), 'split Preview scroll guard records the active driver pane');
  assert.ok(/function scheduleFileEditorSplitScrollSync\(host, source\)[\s\S]*host\._splitScrollPendingSource = source[\s\S]*requestAnimationFrame\(run\)/.test(source), 'split Preview scroll sync is coalesced through requestAnimationFrame for large-document trackpad deltas');
  assert.ok(source.includes("addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'editor'))"), 'editor scroll listener uses the scheduled split-preview sync path');
  assert.ok(source.includes("addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'preview'))"), 'preview scroll listener uses the scheduled split-preview sync path');
  assert.ok(/function syncFileEditorSplitScroll[\s\S]*syncFilePreviewPopoutsFromPanel\(host, source\)/.test(source), 'editor preview/editor scroll drives open preview pop-outs');
  assert.ok(/function closeFilePreviewPopout\(path\)[\s\S]*filePreviewPopouts\.delete\(path\)[\s\S]*previewWindow\.close\?\.\(\)/.test(source), 'preview pop-out close removes the registry entry and closes the window');
  assert.ok(/function setFileEditorViewMode\(path, mode, item = null\)[\s\S]*mode === 'preview' \|\| mode === 'split'[\s\S]*closeFilePreviewPopout\(path\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'switching to in-editor Preview or Split closes any open pop-out preview for that file');
  assert.ok(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8').includes("if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();"), 'settings refresh syncs open preview pop-outs');
  assert.ok(/function replaceOpenFileStateFromDisk[\s\S]*renderOpenFilePath\(path\);[\s\S]*updateFilePreviewPopout\(path, loaded\.state\.content \|\| ''\)/.test((fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8'))), 'external disk reload syncs open preview pop-outs');
  assert.ok(source.includes('position: static !important;'), 'preview pop-out resets the in-pane absolute preview positioning');
  assert.ok(source.includes('display: block !important;') && source.includes('grid-template-rows: none !important;'), 'preview pop-out resets the app body grid layout');
  assert.ok(source.includes('width: 100% !important;') && source.includes('left: auto !important;'), 'preview pop-out resets split-preview geometry that would clip content to the right half');
});

test('t@7900', () => {
  // Preferences order is grouped by how the settings are used: general startup defaults, visual appearance,
  // terminal/editor behavior, notifications, file handling, polling/performance, then agent controls.
  const api = loadYolomux('', ['1']);
  api.setActiveLocaleForTest('en');
  const html = api.preferencesPanelHtmlForTest('');
  const sectionOrder = [...html.matchAll(/data-preference-section="([^"]+)"/g)].map(match => match[1]);
  const expectedOrder = [
    api.t('pref.section.general'),
    api.t('pref.section.appearance'),
    api.t('pref.section.terminal_editor'),
    api.t('pref.section.notifications'),
    api.fileExplorerLabel(),
    api.t('pref.section.uploads'),
    api.t('pref.section.performance'),
    api.t('pref.section.github'),
    api.t('pref.section.yolo'),
    api.t('pref.section.yoagent'),
  ];
  assert.deepStrictEqual(sectionOrder, expectedOrder, 'Preferences sections render in the grouped order');
  const sectionHtml = title => {
    const start = html.indexOf(`data-preference-section="${title}"`);
    assert.ok(start >= 0, `${title} section renders`);
    const next = html.indexOf('data-preference-section="', start + 1);
    return next >= 0 ? html.slice(start, next) : html.slice(start);
  };
  assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="general.reload_on_update"'), 'Notify on server update is in Notifications');
  assert.ok(sectionHtml(api.t('pref.section.performance')).includes('data-setting-path="general.reload_on_update_auto"'), 'Auto-reload on server update is in Performance');
  const appearanceHtml = sectionHtml(api.t('pref.section.appearance'));
  assert.ok(appearanceHtml.includes('data-setting-path="general.default_layout"'), 'Default layout is in Appearance');
  assert.ok(/type="radio"[^>]*value="split"[^>]*data-setting-path="general\.default_layout"/.test(appearanceHtml), 'Default layout offers Split');
  assert.ok(appearanceHtml.includes('Single pane') && appearanceHtml.includes('Split') && appearanceHtml.includes('Grid') && appearanceHtml.includes('Wall'), 'Default layout labels match View layout labels');
  assert.ok(appearanceHtml.includes('Envy green'), 'Active color Green is labeled Envy green');
  assert.ok(appearanceHtml.includes('Deep ocean blue'), 'Active color Blue is labeled Deep ocean blue');
  assert.ok(appearanceHtml.includes('Blood orange'), 'Active color Orange is labeled Blood orange');
  assert.ok(appearanceHtml.includes('Solar gold'), 'Active color Yellow is labeled Solar gold');
  assert.ok(appearanceHtml.includes('Royal violet'), 'Active color Purple is labeled Royal violet');
  assert.ok(appearanceHtml.includes('Moon white'), 'Active color White is labeled Moon white');
  assert.ok(appearanceHtml.includes('Signal green'), 'Cursor color Green is labeled Signal green');
  assert.ok(appearanceHtml.includes('Laser lime'), 'Cursor color Laser lime is available');
  assert.ok(appearanceHtml.includes('Neon green'), 'Cursor color Neon green is available');
  assert.ok(appearanceHtml.includes('Neon cyan'), 'Cursor color Neon cyan is available');
  assert.ok(appearanceHtml.includes('Neon magenta'), 'Cursor color Neon magenta is available');
  assert.ok(appearanceHtml.includes('Neon orange'), 'Cursor color Neon orange is available');
  assert.ok(appearanceHtml.includes('Electric azure'), 'Cursor color Blue is labeled Electric azure');
  assert.ok(appearanceHtml.includes('Flare orange'), 'Cursor color Orange is labeled Flare orange');
  assert.ok(appearanceHtml.includes('Lightning yellow'), 'Cursor color Yellow is labeled Lightning yellow');
  assert.ok(appearanceHtml.includes('Plasma violet'), 'Cursor color Purple is labeled Plasma violet');
  assert.ok(appearanceHtml.includes('Starlight white'), 'Cursor color White is labeled Starlight white');
  assert.ok(/type="radio"[^>]*value="blue"[^>]*data-setting-path="appearance\.active_color"/.test(appearanceHtml), 'Active color Blue renders as a radio');
  assert.ok(/data-setting-path="appearance\.active_color"[\s\S]*data-setting-path="appearance\.editor_cursor_color"[\s\S]*data-setting-path="appearance\.yolo_rotate_ms"/.test(appearanceHtml), 'Cursor color sits immediately after Active color in Appearance');
  assert.ok(/type="radio"[^>]*value="blue"[^>]*data-setting-path="appearance\.editor_cursor_color"/.test(appearanceHtml), 'Cursor color Blue renders as a radio');
  const preferencesSource = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/function layoutModePreferenceChoices\(\)\s*\{[\s\S]*layoutModeValues\.map\(value => \(\{value, label: t\(`menu\.view\.layout\.\$\{value\}`\)\}\)\)/.test(preferencesSource), 'Default layout choices derive from the shared View layout modes');
  assert.ok(/function activeColorPreferenceChoices\(\)\s*\{[\s\S]*UI_COLOR_CHOICES\.map\(value => activeColorPreferenceChoice\(value, t\(UI_COLOR_PRESETS\[value\]\.labelKey\)\)\)/.test(preferencesSource), 'Active color choices derive labels from the shared UI color parent');
  assert.ok(/function cursorColorPreferenceChoices\(\)\s*\{[\s\S]*clientSettingsPayload\?\.choices\?\.\['appearance\.editor_cursor_color'\][\s\S]*CURSOR_COLOR_CHOICES[\s\S]*\.map\(cursorColorPreferenceChoice\)/.test(preferencesSource), 'Cursor color choices sync to the backend allowlist with a local fallback');
  assert.ok(/function cursorColorPreferenceChoice\(value\)\s*\{[\s\S]*preset\?\.cursorLabelKey \? t\(preset\.cursorLabelKey\) : preferenceChoiceLabel\(value\)/.test(preferencesSource), 'Cursor color labels use cursor-specific bright color names from the shared parent');
  assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#3b82f6[\s\S]*--preferences-radio-swatch:#2563eb/.test(appearanceHtml), 'Active color Blue radio shows connected actual dark/light accent swatches');
  assert.ok(appearanceHtml.includes('preferences-setting-note') && appearanceHtml.includes('Editor/Terminal font sizes are in Terminal / Editor.'), 'Appearance shows a note after Finder font size pointing editor/terminal font sizes to Terminal / Editor');
  assert.ok(/data-setting-path="appearance\.file_explorer_font_size"[\s\S]*preferences-setting-note[\s\S]*data-setting-path="appearance\.tab_width"/.test(appearanceHtml), 'Appearance font-size note sits directly after Finder font size');
  assert.ok(/data-setting-path="appearance\.pane_ring_opacity"[^>]*data-setting-type="range"[^>]*min="5"[^>]*max="100"/.test(appearanceHtml), 'Pane ring opacity renders as a 5-100 Appearance slider');
  assert.equal(appearanceHtml.includes('data-setting-path="appearance.inactive_pane_gradient"'), false, 'Inactive pane gradient is removed from Appearance');
  assert.ok(/data-setting-path="appearance\.inactive_pane_opacity"[^>]*data-setting-type="range"[^>]*min="0"[^>]*max="100"/.test(appearanceHtml), 'Inactive pane opacity renders as a 0-100 Appearance slider');
  const appearancePaths = [...appearanceHtml.matchAll(/data-setting-path="([^"]+)"/g)].map(match => match[1]);
  assert.equal(appearancePaths.at(-1), 'appearance.date_time_hour_cycle', '12-hour / 24-hour Date/time clock is the last Appearance item');
  const terminalEditorHtml = sectionHtml(api.t('pref.section.terminal_editor'));
  assert.ok(terminalEditorHtml.includes('data-setting-path="appearance.terminal_theme"'), 'Terminal / Editor follows Appearance and owns terminal/editor-specific controls');
  assert.equal(terminalEditorHtml.includes('data-setting-path="appearance.editor_cursor_color"'), false, 'Cursor color moved out of Terminal / Editor into Appearance');
  assert.ok(/data-setting-path="appearance\.terminal_font_size"[\s\S]*data-setting-path="appearance\.editor_font_size"[\s\S]*data-setting-path="appearance\.preview_font_size"[\s\S]*data-setting-path="terminal_editor\.scrollback"/.test(terminalEditorHtml), 'Terminal / Editor groups Terminal, Editor, and Preview font sizes together before scrollback');
  assert.equal(sectionHtml(api.t('pref.section.general')).includes('data-setting-path="general.default_layout"'), false, 'Default layout no longer lives in General');
  assert.equal(sectionHtml(api.t('pref.section.general')).includes('data-setting-path="general.reload_on_update"'), false, 'Notify on server update no longer lives in General');
  // the GitHub section carries the watched-PRs list field.
  assert.ok(html.includes('data-setting-path="github.watched_prs"'), 'the GitHub section has the watched_prs list field');
  assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['pref.appearance.pane_ring_opacity.help'], 'Percent, 5–100. This is the ring drawn over the ACTIVE content edge; lower values make the green/red pane ring fainter.', 'Pane ring opacity help describes the ACTIVE content-edge ring');
  const settingsRuntimeSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
  assert.ok(settingsRuntimeSource.includes("Math.max(5, Math.min(100, numberSetting('appearance.pane_ring_opacity', 75)))"), 'Pane ring opacity runtime clamp allows 5%');
  assert.ok(settingsRuntimeSource.includes("root.setProperty('--pane-active-ring-opacity', `${percent}%`)"), 'The active pane ring opacity follows the 5-100% preference');
  assert.equal(settingsRuntimeSource.includes('Math.max(75, paneRingOpacity)'), false, 'The active pane ring must not force a 75% floor');
  assert.equal(settingsRuntimeSource.includes('inactive_pane_gradient'), false, 'Inactive pane gradient is removed from runtime settings');
  assert.ok(settingsRuntimeSource.includes("applyInactivePaneOpacity(numberSetting('appearance.inactive_pane_opacity', 60))"), 'Inactive pane opacity defaults to 60% in runtime settings');
  assert.ok(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('("appearance", "pane_ring_opacity"): (5, 100)'), 'Pane ring opacity server settings clamp allows 5%');
  assert.equal(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('inactive_pane_gradient'), false, 'Inactive pane gradient is removed from server settings');
  assert.ok(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('("appearance", "inactive_pane_opacity"): (0, 100)'), 'Inactive pane opacity server settings clamp is 0-100');
});

test('t@7980', () => {
  // the block cursor fills the full monospace cell (width: 1ch), not a fat line.
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/body\.editor-cursor-block[^{]*\.cm-cursor[\s\S]*?\{[\s\S]*?width: 1ch !important;/.test(css), '#122: the block editor cursor is one full character cell wide (1ch)');
});

test('t@7986', () => {
  // the Preferences global-reset UI (title, warning, both buttons, per-row Reset) is localized.
  const api = loadYolomux('', ['1']);
  const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
  api.i18nSetCatalogForTest('zh-Hant', zhHant);
  api.setActiveLocaleForTest('zh-Hant');
  // A non-default value makes the global-reset block render (it is hidden when everything is default).
  api.setClientSettingsPatchForTest({appearance: {ui_font_size: 19}});
  const html = api.preferencesPanelHtmlForTest('');
  assert.ok(html.includes(zhHant['pref.reset.title']), '#115: the global-reset title is localized');
  assert.ok(html.includes(zhHant['pref.reset.all']), '#115: the "Reset all defaults" button is localized');
  assert.ok(html.includes(`aria-label="${zhHant['pref.reset.aria']}"`), '#115: the reset group aria-label is localized');
  assert.ok(html.includes(`>${zhHant['pref.reset.row']}</button>`), '#115: the per-row Reset button is localized');
  // No bare English reset literals leak through.
  assert.ok(!/>Global reset<|>Reset all defaults<|>Continue reset</.test(html), '#115: no English reset literals leak in a non-English locale');
  // Source guard: every reset literal routes through t('pref.reset.*').
  const src = fs.readFileSync('static/yolomux.js', 'utf8');
  for (const key of ['title', 'confirmTitle', 'warning', 'confirmWarning', 'continue', 'cancel', 'all', 'row', 'aria']) {
    assert.ok(src.includes(`t('pref.reset.${key}'`), `#115: reset UI uses t('pref.reset.${key}')`);
  }
});

test('t@8008', () => {
  // the menu bar, Modified-files panel, diff-ref, and comparison localize in a non-English
  // locale and leak no bare English; source guards confirm the builders route through t().
  const api = loadYolomux('', ['1']);
  const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
  api.i18nSetCatalogForTest('zh-Hant', zhHant);
  api.setActiveLocaleForTest('zh-Hant');
  api.setFileExplorerSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
    repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1, behind: 0, ahead: 1}],
    files: [{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1}],
  });
  const panel = api.fileExplorerChangesPanelHtml();
  // C7: the embedded title now names the session via changes.titleForSession; assert its localized stems
  // surround the session (independent of the session label value).
  const titleStems = zhHant['changes.titleForSession'].split('{session}');
  const escHtml = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  assert.ok(titleStems.every(stem => !stem || panel.includes(escHtml(stem))), '#121/C7: the Modified-files title is localized and names the session');
  assert.ok(panel.includes(`>${zhHant['changes.refresh']}</button>`), '#121: the Modified-files Refresh button is localized');
  // C6/C15 follow-up: the FROM/TO text pickers are inline in each repo's localized comparison sentence
  // (no separate FROM/TO labels); assert the sentence's localized text stems surround the inputs.
  for (const stem of zhHant['diff.comparing'].split(/\{from\}|\{to\}/).map(s => s.trim()).filter(Boolean)) {
    assert.ok(panel.includes(stem), `#121/C15: the inline comparison sentence is localized ("${stem}")`);
  }
  assert.ok(/changes-repo-refs compact[\s\S]*data-diff-ref-from[\s\S]*data-diff-ref-to/.test(panel), '#121/C15: the FROM/TO text pickers are present inline on the repo comparison line');
  assert.ok(panel.includes(`aria-label="${zhHant['diff.ref.from.aria']}"`), '#121: the FROM picker aria-label is localized');
  assert.ok(panel.includes(zhHant['changes.ahead.one'].replace('{count}', '1')), '#121: the Ahead-N-commit meta is localized (tPlural)');
  // No bare English leaks in the localized Modified-files panel.
  assert.ok(!/>Modified files<|>Refresh<|>FROM <|>TO <|Ahead 1 commit|Comparing /.test(panel), '#121: no English leaks in the localized Modified-files panel');
  // Source guards: the menu/changes builders carry no bare English literals (all via t()).
  const appSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  for (const literal of ["menuCommand('Open file'", "menuCommand('Preferences'", "menuCommand('Log out'", "menuCommand('Refresh'", "menuSubmenu('Theme'", "menuCommand('Pane details'", "menuCommand('No matching tabs'", "'Kill tmux session", "class=\"changes-title\">Modified files<", '>FROM <select', '`Comparing ${esc(from)} to ${esc(to)}`']) {
    assert.equal(appSrc.includes(literal), false, `#121: bare English literal removed: ${literal}`);
  }
  // The pseudo-locale transforms a representative menu key (the completeness signal).
  const enXA = JSON.parse(fs.readFileSync('static/locales/en-XA.json', 'utf8'));
  assert.ok(/[⟦⟧]/.test(enXA['menu.file.openFile']) && !/^Open file$/.test(enXA['menu.file.openFile']), '#121: menu keys are pseudo-localized in en-XA');
});

test('t@8050', () => {
  // the default (files-mode) search bar blends matching commands/tabs into the results.
  const api = loadYolomux('', ['1']);
  const prefsLabel = api.itemLabel(api.prefsItemId);
  api.setFileQuickOpenCandidatesForTest('/repo/app', [
    {name: 'notes.py', path: '/repo/app/notes.py', relative_path: 'notes.py'},
  ]);
  api.setCommandPaletteStateForTest('files', prefsLabel);
  assert.ok(api.commandPaletteItems().some(item => item.group === 'Tabs' && item.label === prefsLabel), '#7: a command/tab matching a plain files-mode query is blended in (no > needed)');
  api.setCommandPaletteStateForTest('command', 'notes');
  assert.ok(api.commandPaletteItems().some(item => item.category === 'file' && item.path === '/repo/app/notes.py'), 'DOIT.55: command-mode queries also blend matching file-index results');
  // `>` stays commands-only — no file candidates blended.
  api.setCommandPaletteStateForTest('files', `>${prefsLabel}`);
  assert.ok(!api.commandPaletteItems().some(item => item.path === '/repo/app/notes.py'), '#7: the > prefix stays commands-only');
  // An empty files-mode query must NOT dump the whole command corpus.
  api.setCommandPaletteStateForTest('files', '');
  assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: empty files-mode query shows files only (no command dump)');
  // `@` stays reserved for symbols (no command blend).
  api.setCommandPaletteStateForTest('files', '@thing');
  assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: @ stays reserved for symbols');
});

// macOS Finder list-view keyboard PARITY. The key->intent map is a PURE function, unit-tested here so the
// full set of bindings is verified as behavior (not just source shape). Works for Finder AND Differ.
test('t@8072', () => {
  const api = loadYolomux();
  const I = (key, mods = {}) => api.fileExplorerKeyIntent(key, {shift: !!mods.shift, mod: !!mods.mod, alt: !!mods.alt});
  // move + extend
  assert.equal(I('ArrowDown'), 'move-down', 'Down moves selection');
  assert.equal(I('ArrowUp'), 'move-up', 'Up moves selection');
  assert.equal(I('ArrowDown', {shift: true}), 'extend-down', 'Shift+Down extends');
  assert.equal(I('ArrowUp', {shift: true}), 'extend-up', 'Shift+Up extends');
  assert.equal(I('Home'), 'move-home');
  assert.equal(I('End'), 'move-end');
  assert.equal(I('Home', {shift: true}), 'extend-home');
  assert.equal(I('End', {shift: true}), 'extend-end');
  // expand / collapse / parent / child
  assert.equal(I('ArrowRight'), 'expand', 'Right expands / steps in');
  assert.equal(I('ArrowLeft'), 'collapse', 'Left collapses / steps out');
  // open + enclosing folder
  assert.equal(I('ArrowDown', {mod: true}), 'open', 'Cmd-Down = open');
  assert.equal(I('o', {mod: true}), 'open', 'Cmd-O = open');
  assert.equal(I('O', {mod: true}), 'open');
  assert.equal(I('ArrowUp', {mod: true}), 'enclosing', 'Cmd-Up = enclosing folder');
  // rename / select-all / preview / type-ahead
  assert.equal(I('Enter'), 'rename', 'Return = rename (Finder)');
  assert.equal(I('a', {mod: true}), 'select-all', 'Cmd-A = select all');
  assert.equal(I(' '), 'preview', 'Space = Quick Look preview');
  assert.equal(I('d'), 'typeahead', 'a letter is type-to-select');
  assert.equal(I('R'), 'typeahead');
  // NOT claimed (left for the OS / other shortcuts)
  assert.equal(I('ArrowRight', {mod: true}), null, 'Cmd-Right is not claimed');
  assert.equal(I('Enter', {mod: true}), null);
  assert.equal(I('Enter', {shift: true}), null);
  assert.equal(I('ArrowDown', {alt: true}), null, 'Alt combos not claimed');
  assert.equal(I(' ', {mod: true}), null);
  assert.equal(I('Tab'), null);
  assert.equal(I('Escape'), null);
  assert.equal(I('x', {mod: true}), null, 'Cmd-X is not a Finder nav key here');
});

// Source guards: the dispatcher wires each intent to the right live-tree action.
test('t@8110', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('function handleFileExplorerArrowNav('), 'arrow-nav handler exists');
  assert.ok(source.includes('function fileExplorerKeyIntent('), 'pure key->intent map exists');
  assert.ok(/if \(handleFileExplorerArrowNav\(event\)\) return;/.test(source), 'wired into the global keydown after the delete shortcut');
  assert.ok(source.includes('!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)'), 'gated on the Finder/Differ surface');
  // Differ + Finder share the EXACT same handler: the container selector and the expand/collapse parent both cover the changes panel.
  assert.ok(source.includes('.file-explorer-tree-panel, .file-explorer-changes-panel'), 'the SAME handler scopes to both the Finder tree and the Differ changes panel');
  assert.ok(source.includes('function fileTreeDirectoryExpanded(') && source.includes('function setFileTreeDirectoryExpanded('), 'one shared expand/collapse parent for both surfaces');
  assert.ok(/setFileTreeDirectoryExpanded[\s\S]{0,260}closest\('\.file-explorer-changes-panel'\)[\s\S]{0,220}changesFolderCollapsed[\s\S]{0,220}expandDirectoryRow/.test(source), 'the parent dispatches Differ (changesFolderCollapsed) vs Finder (expandDirectoryRow) — no per-surface key code');
  assert.ok(source.includes('setFileTreeDirectoryExpanded(leadRow, leadPath, true)') && source.includes('setFileTreeDirectoryExpanded(leadRow, leadPath, false)'), 'Right/Left route through the shared expand/collapse parent');
  assert.ok(/intent === 'open'/.test(source) && source.includes('openChangedFileInDiff(') && source.includes('openFileInEditor(leadPath, entry)') && source.includes('openFileExplorerManualRoot(leadPath)'), 'open: Differ file -> diff, file -> editor, Finder folder -> descend');
  assert.ok(/intent === 'enclosing'[\s\S]{0,300}openFileExplorerManualRoot\(parent\)/.test(source), 'Cmd-Up opens the enclosing folder');
  assert.ok(/intent === 'rename'[\s\S]{0,200}beginFileTreeRename\(leadRow, leadPath, entry\)/.test(source), 'Enter renames the lead row (Finder AND Differ)');
  assert.ok(!/openChangeFile !== undefined\) return false/.test(source), 'no Differ-rename exclusion — Differ rows rename too (git mv handles tracked files)');
  assert.ok(/intent === 'preview'[\s\S]{0,300}openFileImagePreview\(leadRow, leadPath, entry\)/.test(source), 'Space previews (Quick Look) the lead file');
  assert.ok(source.includes('expandDirectoryRow(row, fullPath, {manual: true})') && source.includes('collapseDirectoryRow(row, fullPath, {manual: true})'), 'Finder branch of the shared parent still uses expand/collapseDirectoryRow');
  assert.ok(/pathIsInsideDirectory\(child\.dataset\.path, leadPath\)/.test(source), 'Right steps into the first child when already expanded');
  assert.ok(/rows\.find\(item => item\.dataset\.path === dirnameOf\(leadPath\)\)/.test(source), 'Left steps to the parent row');
  assert.ok(source.includes('function fileExplorerTypeaheadSelect('), 'type-ahead selection exists');
  assert.ok(source.includes('fileExplorerSelectionLead = fullPath'), 'click/range selection seeds the same lead');
  assert.ok(source.includes('fileTreeRepoPopoverCursor.x + 14'), 'repo-row hover popover anchors to the RIGHT of the cursor');
});

// S2 BEHAVIORAL PROOF: a file that is an open tab appears ONCE in the on-type palette — as its
// Tabs row — with its Recent/Files duplicate suppressed. Without the dedup it would appear twice.
test('t@8136', () => {
  const path = '/repo/app/notes.py';
  const enc = encodeURIComponent('file:' + path);   // file%3A%2Frepo%2Fapp%2Fnotes.py
  // open the file as a real tab (slot3) via the layout URL — same shape as the passing test above
  const search = `?sessions=files,${enc},5&layout=row@20.7(slot2,row@42(slot3,slot1))&tabs=slot2:files;slot3:${enc};slot1:5`;
  const api = loadYolomux(search, ['5']);
  const panes = api.serialize(api.currentSlots()).panes;
  assert.ok(Object.values(panes).some(p => (p.tabs || []).includes('file:' + path)), 'S2 setup: the file is an open tab');
  // the SAME file is also recently-open (Recent) and a search candidate (Files) — both would dupe it
  api.setOpenFileStateForTest(path, {name: 'notes.py'});
  api.setFileQuickOpenCandidatesForTest('/repo/app', [{name: 'notes.py', path, relative_path: 'notes.py'}]);
  api.setCommandPaletteStateForTest('files', 'notes');
  const items = api.commandPaletteItems();
  const fileGroupRows = items.filter(it => it.category === 'file' && (it.searchFields?.[1] || it.detail) === path);
  const tabRows = items.filter(it => it.category === 'pane' && api.fileItemPath(it.targetItem || '') === path);
  assert.equal(fileGroupRows.length, 0, 'S2: the open file is NOT duplicated as a Recent/Files row');
  assert.ok(tabRows.length >= 1, 'S2: the open file still appears as its Tabs row');
});

test('t@8155', () => {
  // #8-#13: renames, toggles, theme propagation, README preview.
  const api = loadYolomux('', ['1']);
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  // #8: Branch Info -> YO!info. The tab/menu/palette labels are now localized (functions, not consts) so
  // a runtime language switch repaints them; en still resolves to "YO!info" / "YO!agent".
  assert.ok(/function infoTabLabel\(\)\s*\{\s*return t\('brand\.tab\.info'\)/.test(source), '#8: the YO!info tab label is localized via brand.tab.info');
  assert.ok(/function yoagentTabLabel\(\)\s*\{\s*return t\('brand\.tab\.agent'\)/.test(source), 'the YO!agent tab label is localized via brand.tab.agent');
  // #9: File -> Finder toggles via toggleFinderPane (hide when in layout, else open).
  assert.ok(source.includes('menuCommand(fileExplorerLabel(), () => toggleFinderPane()'), '#9: File -> Finder uses the toggle');
  assert.ok(/function toggleFinderPane\(\)\s*\{[^}]*itemInLayout\(fileExplorerItemId\)[^}]*removeSessionFromLayout\(fileExplorerItemId\)/.test(source), '#9: toggle hides the Finder when it is already in the layout');
  // terminalThemeSettingForGlobalMode still maps modes correctly (pure helper), but #261 stopped the
  // View -> Theme toggle from PINNING the terminal palette — the terminal follows the app on its own.
  assert.equal(api.terminalThemeSettingForGlobalMode('system'), 'follow-app', 'system maps to follow-app');
  assert.equal(api.terminalThemeSettingForGlobalMode('dark'), 'dark', 'dark maps to dark');
  assert.equal(api.terminalThemeSettingForGlobalMode('light'), 'light', 'light maps to light');
  assert.equal(source.includes('patch.appearance.terminal_theme = terminalThemeSettingForGlobalMode(next)'), false, '#261: the View theme toggle no longer pins the terminal palette (terminal follows the app)');
  // #10: a global-theme change re-themes live editors via the compartment swap.
  assert.ok(/previousEditorSchemeId !== activeEditorScheme\(\)\.id \|\| previousCursorColor !== fileEditorCursorColor\)\s*\{[^}]*refreshOpenEditorThemePanels\(\)/.test(source), '#10: theme or cursor-color change re-themes open editors');
  // #12: Preferences field renamed. (Phase 0: the label is now i18n-keyed; en.json holds the text.)
  assert.ok(source.includes("label: t('pref.appearance.theme.label')"), '#12: the global theme field is i18n-keyed');
  assert.ok(source.includes("initialSetting('appearance.date_time_hour_cycle', '24')"), 'date/time clock defaults to 24-hour in the client');
  assert.ok(source.includes("label: t('pref.appearance.date_time_hour_cycle.label')"), 'date/time clock Preferences field is i18n-keyed');
  const enThemeCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
  assert.equal(enThemeCatalog['pref.appearance.theme.label'], 'Global color theme', '#12: the Preferences field reads "Global color theme"');
  assert.equal(enThemeCatalog['pref.appearance.theme.label'] === 'Global app theme', false, '#12: no stale "Global app theme" label remains');
  // #13: Help -> README opens rendered markdown preview.
  assert.ok(source.includes("openFileInEditor(path, 'README.md', {viewMode: 'preview'})"), '#13: README opens in preview mode');
});

test('t@8186', () => {
  // every pane keeps its active tab clearly green (no dimming); focused pane = brighter
  // lime + ring. Source-guards on the shared tokens + the un-dimmed unfocused-active rule.
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/--pane-tab-unfocused-active-bg:\s*var\(--pane-tab-active-bg\)/.test(css), '#11: unfocused panes show a clearly-visible green active tab (aliased to the focused full-green token; images 003/004)');
  assert.ok(/\.panel:not\(\.active-pane\):not\(\.file-explorer-panel\) \.pane-tab\.active\s*\{\s*opacity:\s*1/.test(css), '#11: unfocused active tabs are no longer dimmed');
  assert.ok(/--active-accent-bright:\s*#86d600/.test(css), '#11: the focused pane keeps the brighter lime active tab as its extra cue (via active-accent)');
});

test('t@8195', () => {
  // re-renders + search-focus are deferred/suppressed mid-drag so the dragged DOM node
  // is not replaced (which aborts the native HTML5 drag); + 3-tab placement in a consistent index space.
  const api = loadYolomux('', ['1', '2', '3']);
  api.setDragSessionForTest('2');
  api.renderPaneTabStrips();
  assert.equal(api.pendingTabStripRenderForTest(), true, '#30: tab-strip re-render is DEFERRED during a drag (node not replaced)');
  assert.equal(api.focusPreferencesSearch(), false, '#30: search focus is suppressed during a drag');
  // / a full renderPanels() pools every panel + clears the grid, which detaches the
  // dragged node and aborts the native drag. It must defer to a pendingLayoutRender request mid-drag, NOT
  // touch the grid. (If the guard were missing this call would throw on the absent grid element.)
  api.setPendingPanelsRenderForTest(false);
  api.renderPanels();
  assert.equal(api.pendingPanelsRenderForTest(), true, '#114: full panel re-render is DEFERRED during a drag (grid not wiped)');
  assert.equal(api.pendingLayoutRenderForTest().forceFull, true, '#renderPanels stores an explicit forced-full render request');
  api.setDragSessionForTest(null);
  const strip3 = tabStrip([tabElement('A', 100, 100), tabElement('B', 203, 100), tabElement('C', 306, 100)]);
  assert.equal(api.paneTabDropPlacement(strip3, {clientX: 330, clientY: 8}, 'A').index, 2, '#30: 3-tab L->R drop on the far tab lands after it');
  assert.equal(api.paneTabDropPlacement(strip3, {clientX: 120, clientY: 8}, 'C').index, 0, '#30: 3-tab R->L drop on the first tab lands before it');
  // #32/#33 source guards.
  const dragSrc = fs.readFileSync('static/yolomux.js', 'utf8');
  const dragCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(dragSrc.includes('minimumContrastRatio: terminalMinimumContrastRatio()'), '#32: terminal creation sets minimumContrastRatio');
  assert.ok(dragSrc.includes('item.term.options.minimumContrastRatio = minContrast'), '#32: live terminals re-apply minimumContrastRatio');
  assert.ok(/item\.container\.style\.background = theme\.background/.test(dragSrc), '#32: all terminal containers share one theme background');
  assert.ok(/body\.theme-light \.topbar-search\s*\{[^}]*background/.test(dragCss), '#33: the topbar search blends in light mode (no dark pill)');
  // The topbar is neutral at rest and switches to the green tab-strip color only on hover/focus.
  assert.ok(/\.topbar\s*\{[^}]*background:\s*var\(--panel2\)/.test(dragCss), 'topbar bg is neutral at rest');
  assert.ok(/\.topbar:hover,\s*\.topbar:focus-within\s*\{[^}]*background:\s*var\(--pane-tab-strip-bg\)/.test(dragCss), 'topbar bg matches the green tab strip on hover/focus');
  assert.ok(/body\.theme-light \.topbar\s*\{[^}]*background:\s*var\(--panel2\)/.test(dragCss), 'light-mode topbar is neutral at rest');
  assert.ok(/body\.theme-light \.topbar:hover,\s*body\.theme-light \.topbar:focus-within\s*\{[^}]*background:\s*var\(--pane-tab-strip-bg\)/.test(dragCss), 'light-mode topbar uses the green tab-strip bg on hover/focus');
  // / the dragSession guard MUST precede movePanelsToPool()/grid.innerHTML in
  // renderPanels, and endSessionDrag MUST flush via the scheduler instead of direct renderPanels().
  assert.ok(/function renderPanels\([^)]*\)\s*\{[\s\S]{0,700}?if \(dragSession != null\) \{[\s\S]*?requestLayoutRender\(\{[\s\S]*?forceFull: true[\s\S]*?return;[\s\S]{0,80}movePanelsToPool\(\)/.test(dragSrc), '#114/#52: renderPanels defers a structured forced-full request before pooling panels / clearing the grid');
  const endDragStart = dragSrc.indexOf('function endSessionDrag');
  const endDragBody = dragSrc.slice(endDragStart, endDragStart + 1200);
  assert.ok(/dragSession = null;[\s\S]*?flushPendingLayoutRender\(\);/.test(endDragBody), '#endSessionDrag flushes through the shared layout scheduler after clearing dragSession');
  assert.equal(/pendingPanelsRender/.test(endDragBody), false, '#endSessionDrag no longer uses the old boolean pendingPanelsRender flag');
});

test('t@8235', () => {
  // same-strip drag-reorder works in BOTH directions. Dropping a tab anywhere onto a
  // neighbor moves it past that neighbor (no center-overshoot required for the left->right case).
  const api = loadYolomux('', ['6']);
  const strip = tabStrip([tabElement('6', 100, 100), tabElement('P', 203, 100)]);
  // (re-open of #12): a drop ANYWHERE on the neighbor reorders — BOTH halves, BOTH ways.
  // P spans 203-303 (center 253); L spans 100-200 (center 150).
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '6').index, 1, 'L dropped on R LEFT half reorders RIGHT (was the no-op pre-fix)');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 290, clientY: 8}, '6').index, 1, 'L dropped on R RIGHT half reorders RIGHT');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 120, clientY: 8}, 'P').index, 0, 'R dropped on L LEFT half reorders LEFT');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 190, clientY: 8}, 'P').index, 0, 'R dropped on L RIGHT half reorders LEFT');
  // Cross-pane drops keep the centered insert threshold (unchanged behavior).
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 230, clientY: 8}, '9').index, 1, 'cross-pane drop keeps the centered threshold');
});

test('t@8250', () => {
  const api = loadYolomux();
  const strip = tabStrip([
    tabElement('1', 100, 100),
    tabElement('2', 203, 100),
    tabElement('3', 306, 100),
  ]);
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '2').noop, true, 'dragging 2 into the 1|2 adjacent gap is a no-op');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 290, clientY: 8}, '2').noop, true, 'dragging 2 into the 2|3 adjacent gap is a no-op');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 120, clientY: 8}, '2').noop, false, 'dragging 2 before tab 1 still reorders');
  assert.equal(api.paneTabDropPlacement(strip, {clientX: 330, clientY: 8}, '2').noop, false, 'dragging 2 after tab 3 still reorders');
});

test('t@8263', () => {
  const api = loadYolomux();
  const strip = new TestElement('dock-tabs');
  const dockTab = item => {
    const tab = new TestElement(`dv-${item}`);
    tab.classList.add('dv-tab');
    const inner = new TestElement(`tab-${item}`);
    inner.classList.add('dockview-pane-tab');
    inner.dataset.paneTab = item;
    tab.appendChild(inner);
    strip.appendChild(tab);
    return {tab, inner};
  };
  const one = dockTab('1');
  const two = dockTab('2');
  const three = dockTab('3');
  const eventFor = (target, position, panelId = '2') => ({
    kind: 'tab',
    position,
    group: {id: 'left'},
    nativeEvent: {target},
    getData: () => ({panelId, groupId: 'left'}),
  });
  assert.equal(api.dockviewTabDropWouldNoop(eventFor(one.inner, 'right')), true, 'Dockview suppresses the 1|2 no-op insertion preview');
  assert.equal(api.dockviewTabDropWouldNoop(eventFor(three.inner, 'left')), true, 'Dockview suppresses the 2|3 no-op insertion preview');
  assert.equal(api.dockviewTabDropWouldNoop(eventFor(one.inner, 'left')), false, 'Dockview still allows moving 2 before tab 1');
  assert.equal(api.dockviewTabDropWouldNoop(eventFor(three.inner, 'right')), false, 'Dockview still allows moving 2 after tab 3');
  api.setPinnedTabsForTest(['1', '2']);
  const firstToSecond = eventFor(two.inner, 'left', '1');
  assert.equal(api.dockviewTabDropWouldNoop(firstToSecond), false, 'Dockview must not suppress dragging the first tab onto the second tab');
  assert.deepStrictEqual(canonical(api.dockviewTabEdgeReorderIntent(firstToSecond)), {
    insertIndex: 1,
    item: '1',
    sourceSlot: 'left',
    targetSlot: 'left',
  }, 'Dockview manually reorders an edge tab dragged onto its adjacent neighbor');
  api.setPinnedTabsForTest([]);
});

test('t@8302', () => {
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

  api.showPaneTabDropPreview(strip, {clientX: 225, clientY: 8}, '2');
  assert.equal(strip.classList.contains('drag-over'), false, 'same-strip adjacent no-op does not show a tab-strip target outline');
  assert.equal(strip.classList.contains('tab-drop-preview'), false, 'same-strip adjacent no-op does not show an insertion preview');
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '');
  assert.equal(strip.style.getPropertyValue('--tab-drop-y'), '');
  assert.equal(strip.style.getPropertyValue('--tab-drop-height'), '');
});

test('t@8334', () => {
  const api = loadYolomux();
  const container = new TestElement('dockview-actions');
  const button = new TestElement('window-next', 'button');
  button.dataset.windowDir = 'next';
  button.dataset.windowSession = 'codex';
  const glyph = new TestElement('glyph', 'span');
  button.appendChild(glyph);
  container.appendChild(button);
  const directButton = new TestElement('window-2', 'button');
  directButton.dataset.windowIndex = '2';
  directButton.dataset.windowSession = 'codex';
  const directLabel = new TestElement('label', 'span');
  directButton.appendChild(directLabel);
  container.appendChild(directButton);
  assert.equal(api.windowStepButtonFromEvent({currentTarget: button, target: glyph}), button, 'legacy direct window-step clicks resolve the button currentTarget');
  assert.equal(api.windowStepButtonFromEvent({currentTarget: container, target: glyph}), button, 'Dockview delegated window-step clicks resolve the closest data-window-dir button');
  assert.equal(api.windowStepButtonFromEvent({currentTarget: container, target: directLabel}), directButton, 'Dockview delegated direct-window clicks resolve the closest data-window-index button');
});

test('t@8347', () => {
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
});

test('t@8367', () => {
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
});

test('t@8392', () => {
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
});

test('t@8413', () => {
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
});

test('t@8451', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.equal(source.includes('installFilePathDropTarget(session, panel);'), false, 'panel-level path drop target must not swallow pane split previews');
  assert.ok(source.includes('installTerminalFileDrop(session, container);'), 'terminal surface still accepts path insertion drops');
});

test('t@8457', () => {
  const api = loadYolomux();
  const source = tabElement('4', 100, 140);
  source.rect = {left: 100, right: 240, top: 20, bottom: 47, width: 140, height: 27};
  source.classList.add('pane-tab');
  const event = dragEvent(125, '4');
  event.currentTarget = source;
  event.clientY = 31;
  // C12 F2: the grab offset now comes from event.offsetX/offsetY (no getBoundingClientRect reflow).
  event.offsetX = 25;
  event.offsetY = 11;

  api.startSessionDrag(event, '4', 'left');

  assert.equal(source.classList.contains('dragging'), false, 'source tab is not dimmed while dragging');
  assert.equal(event.dataTransfer.effectAllowed, 'move');
  assert.equal(event.dataTransfer['application/x-yolomux-session'], JSON.stringify({session: '4', sourceSlot: 'left'}));
  assert.equal(event.dataTransfer['text/plain'], '4');
  // #47: tab drags use the NATIVE drag image — a one-time snapshot of the tab itself, positioned under
  // the grab point — with NO transparent image, NO JS clone-follow preview, and NO document listeners.
  assert.ok(event.dataTransfer.dragImage, 'native drag image is installed');
  assert.equal(event.dataTransfer.dragImage.node, source, '#47: the drag image is the tab itself (compositor snapshot)');
  // C12 F2: grab offset is event.offsetX/offsetY (cursor position within the tab), no layout reflow.
  assert.equal(event.dataTransfer.dragImage.x, 25, '#47/C12: drag-image grab offset X comes from event.offsetX');
  assert.equal(event.dataTransfer.dragImage.y, 11, '#47/C12: drag-image grab offset Y comes from event.offsetY');
  assert.equal(api.customDragPreviewForTest(), null, '#47: tab drags install no JS clone-follow preview');
});

test('t@8485', () => {
  const api = loadYolomux('', ['1', '2', '3']);
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
  slots.left = api.paneStateWithTabs(['1', '2'], '2');
  slots.right = api.paneStateWithTabs(['3'], '3');
  api.setLayoutSlotsForTest(slots);
  api.setLayoutColumnRectsForTest({
    left: {left: 0, top: 0, right: 800, bottom: 520, width: 800, height: 520},
    right: {left: 800, top: 0, right: 1600, bottom: 520, width: 800, height: 520},
  });
  const handle = new TestElement('pane-handle');
  const event = dragEvent(820, '1');
  event.currentTarget = handle;
  event.target = handle;
  event.offsetX = 7;
  event.offsetY = 8;

  api.startPaneDrag(event, 'left');

  assert.equal(event.dataTransfer.effectAllowed, 'move');
  assert.equal(event.dataTransfer['application/x-yolomux-pane'], JSON.stringify({slot: 'left'}));
  assert.equal(api.paneDragPayload(event).slot, 'left', 'pane drags use a distinct pane payload');
  assert.equal(event.dataTransfer.dragImage.node.className, 'transparent-drag-image', 'pane drags hide the browser snapshot behind the custom pane preview');
  assert.equal(event.dataTransfer.dragImage.x, 0, 'pane drags use a transparent native image at origin');
  assert.equal(event.dataTransfer.dragImage.y, 0, 'pane drags use a transparent native image at origin');
  const panePreview = api.customDragPreviewForTest();
  assert.ok(panePreview, 'pane drags install a custom pane preview');
  assert.ok(panePreview.classList.contains('pane-drag-image'), 'pane drag preview uses the pane ghost class');
  assert.equal(panePreview.dataset.dragSlot, 'left', 'pane drag preview records the dragged pane slot');
  assert.ok(panePreview.innerHTML.includes('2 tabs'), 'pane drag preview summarizes the pane tab count');
  assert.ok(parseFloat(panePreview.style.getPropertyValue('width') || panePreview.style.width || '0') > 0, 'pane drag preview has a measured width');
  assert.ok(parseFloat(panePreview.style.getPropertyValue('height') || panePreview.style.height || '0') > 0, 'pane drag preview has a measured height');

  const target = new TestElement('right-slot');
  target.classList.add('drop-slot');
  target.dataset.slot = 'right';
  target.rect = {left: 800, top: 0, right: 1600, bottom: 520, width: 800, height: 520};
  api.setGridPreviewNodesForTest([target]);
  event.target = target;
  api.handleDropDragOver(event);
  assert.equal(event.dataTransfer.dropEffect, 'move');
  assert.ok(target.classList.contains('drop-preview-middle'), 'pane swap previews the whole target pane, not an edge subpane');
  assert.equal(target.dataset.dropLabel, 'swap');
  assert.equal(api.paneSwapAllowed('left', 'right'), true, 'similarly sized panes can swap whole pane tab stacks');

  assert.equal(api.swapPaneSlots('left', 'right'), true);
  assert.deepStrictEqual([...api.currentSlots().left.tabs], ['3'], 'target pane tabs moved as a whole into the source slot');
  assert.deepStrictEqual([...api.currentSlots().right.tabs], ['1', '2'], 'source pane tabs moved as a whole into the target slot');
  assert.equal(api.currentSlots()[api.layoutTreeKey].split, 'row', 'pane swaps keep the existing split tree');
  assert.ok(api.windowListenersForTest('drop').length > 0, 'pane drag preview cleanup is bound at the window level');
  api.windowListenersForTest('drop')[0](event);
  assert.equal(api.customDragPreviewForTest(), null, 'pane drag preview is removed when native drag release reaches window instead of document');
  assert.equal(api.windowListenersForTest('drop').length, 0, 'window preview cleanup listeners are removed after pane drag cleanup');

  const smallSlots = api.emptyLayoutSlots();
  smallSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
  smallSlots.left = api.paneStateWithTabs(['1', '2'], '2');
  smallSlots.right = api.paneStateWithTabs(['3'], '3');
  api.setLayoutSlotsForTest(smallSlots);
  api.clearDropPreview();
  api.setLayoutColumnRectsForTest({
    left: {left: 0, top: 0, right: 800, bottom: 520, width: 800, height: 520},
    right: {left: 800, top: 0, right: 980, bottom: 520, width: 180, height: 520},
  });
  api.startPaneDrag(event, 'left');
  api.handleDropDragOver(event);
  assert.equal(event.dataTransfer.dropEffect, 'none', 'too-small target panes reject whole-pane swaps');
  assert.equal(target.classList.contains('drop-preview-middle'), false, 'too-small target panes do not advertise a pane swap preview');
  assert.equal(api.paneSwapAllowed('left', 'right'), false);
  api.endSessionDrag(event);
  assert.equal(api.customDragPreviewForTest(), null, 'pane drag preview is removed on cleanup');
});

test('t@8555', () => {
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
});

// editor back/forward navigation history (Popular IDE-style file stack). Tests the record/dedupe/
// truncate logic of recordEditorNav (the async back/forward re-open goes through the live open path).
test('t@8592', () => {
  const api = loadYolomux();
  api.editorNav.stack = [];
  api.editorNav.index = -1;
  api.recordEditorNav('/repo/a.txt');
  api.recordEditorNav('/repo/b.txt');
  assert.deepEqual(api.editorNav.stack, ['/repo/a.txt', '/repo/b.txt'], 'opens push onto the nav stack');
  assert.equal(api.editorNav.index, 1, 'index points at the latest open');
  api.recordEditorNav('/repo/b.txt');
  assert.deepEqual(api.editorNav.stack, ['/repo/a.txt', '/repo/b.txt'], 'a consecutive same-file open does not duplicate');
  assert.equal(api.editorNav.index, 1);
  api.editorNav.index = 0; // simulate Back to A
  api.recordEditorNav('/repo/c.txt');
  assert.deepEqual(api.editorNav.stack, ['/repo/a.txt', '/repo/c.txt'], 'a new open after Back drops the forward tail');
  assert.equal(api.editorNav.index, 1);
  api.editorNav.navigating = true; // a back/forward re-open must NOT record a new entry
  api.recordEditorNav('/repo/d.txt');
  assert.deepEqual(api.editorNav.stack, ['/repo/a.txt', '/repo/c.txt'], 'recording is suppressed while navigating');
  api.editorNav.navigating = false;
  // The stack holds arbitrary tab ITEM ids now (not only file paths), so any tab kind is recorded.
  api.recordEditorNav('5');                 // a terminal session tab
  api.recordEditorNav('file-explorer');     // the Finder dock
  assert.deepEqual(api.editorNav.stack.slice(-2), ['5', 'file-explorer'], 'tab back/forward records any tab kind, not just files');
  api.editorNav.stack = [];
  api.editorNav.index = -1;
  api.recordEditorNav('A');
  api.recordEditorNav('B');
  api.recordEditorNav('A');
  api.recordEditorNav('B');
  assert.deepEqual(api.editorNav.stack, ['A', 'B'], 'A->B->A->B collapses to the useful two-pane history');
  assert.equal(api.editorNav.index, 1, 'the collapsed ping-pong stack still points at the current pane');
  // S3: the stack trims to NAV_STACK_LIMIT (50) oldest-first so long sessions stay bounded.
  api.editorNav.stack = [];
  api.editorNav.index = -1;
  for (let i = 0; i < 55; i++) api.recordEditorNav(`/repo/f${i}.txt`);
  assert.equal(api.editorNav.stack.length, 50, 'S3: nav stack is capped at NAV_STACK_LIMIT (50)');
  assert.equal(api.editorNav.stack[0], '/repo/f5.txt', 'S3: the oldest entries are dropped when the cap is exceeded');
  assert.equal(api.editorNav.index, 49, 'S3: index points at the latest after trimming');
});

// A user click/type focus transition records the pane being left and the pane being entered, so Back
// becomes active even when the previous pane was only focused, not already present in the nav stack.
test('t@8634', () => {
  const api = loadYolomux();
  const back = api.testElementForId('topbarNavBack');
  api.editorNav.stack = [];
  api.editorNav.index = -1;
  back.disabled = true;
  api.setFocusedPanelItem('1');
  api.focusTerminalFromUserAction('2');
  assert.deepEqual(api.editorNav.stack, ['1', '2'], 'user focus transition records previous pane then target pane');
  assert.equal(api.editorNav.index, 1, 'user focus transition points history at the target pane');
  assert.equal(back.disabled, false, 'Back is active after clicking/typing into another pane');
});

// Tab history is bounded — the oldest entries drop past the cap so it can't grow without limit.
test('t@8648', () => {
  const api = loadYolomux();
  api.editorNav.stack = [];
  api.editorNav.index = -1;
  for (let i = 0; i < 80; i += 1) api.recordEditorNav(`tab-${i}`);
  assert.equal(api.editorNav.stack.length, 50, 'the nav history is capped at 50 entries');
  assert.equal(api.editorNav.stack[0], 'tab-30', 'the oldest entries past the cap are dropped');
  assert.equal(api.editorNav.stack[api.editorNav.stack.length - 1], 'tab-79', 'the newest entry is retained');
  assert.equal(api.editorNav.index, 49, 'the index tracks the capped stack tail');
});

// the quick-open palette collapses a file's editor-tab + preview-tab into ONE row with
// edit/preview view chips (it used to emit two identical rows — same name + path).
test('t@8661', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/const fileGroups = new Map\(\);[\s\S]{0,400}?fileItemPath\(item\)/.test(source), 'the palette groups file tabs by path (fileItemPath)');
  assert.ok(source.includes('tabRow(editorItem, {key: `file:${path}`, viewModes})'), 'editor+preview of one file collapse to a single `file:` row carrying view chips');
  assert.ok(source.includes('command-palette-view-chip'), 'the deduped file row renders edit/preview view chips');
  // follow-up: the chips are clickable — each carries its view's layout item and jumps to it.
  assert.ok(/data-view-item="\$\{esc\(v\.item\)\}" data-view-mode="\$\{esc\(v\.mode\)\}"/.test(source), 'each view chip carries its layout item + mode');
  assert.ok(/closest\('\[data-view-item\]'\)[\s\S]{0,180}selectSession\(viewItem, \{userInitiated: true\}\)/.test(source), 'clicking a chip jumps to that view and closes the palette');
});

// the Modified-files repo header is a collapse toggle (button + caret), per-repo state.
test('t@8672', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('head.dataset.changesRepoToggle = repo'), 'the repo header is a collapse toggle keyed by repo path');
  assert.ok(source.includes('changesRepoCollapsed.has(repo)'), 'the repo head reads per-repo collapse state');
  assert.ok(source.includes('changes-repo-caret'), 'the repo head shows a collapse caret');
});

// (button completion): the back/forward buttons live in the GLOBAL topbar (left of the search
// box), are wired to editorNavBack/Forward, and tab-switches record as nav.
test('t@8681', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes("createTopbarNav())"), 'the topbar assembles the nav group');
  assert.ok(source.indexOf('createTopbarNav())') < source.indexOf('createTopbarSearch())'), 'the nav (back/forward) group is appended before the topbar search box');
  assert.ok(/const back = makeButton\(\{[\s\S]*id: 'topbarNavBack'/.test(source) && /const forward = makeButton\(\{[\s\S]*id: 'topbarNavForward'/.test(source), 'the topbar nav builds #topbarNavBack / #topbarNavForward through the shared button builder');
  assert.ok(/topbarNavBack[\s\S]{0,400}?editorNavBack\(\)/.test(source), 'the back button is wired to editorNavBack()');
  assert.ok(/topbarNavForward[\s\S]{0,400}?editorNavForward\(\)/.test(source), 'the forward button is wired to editorNavForward()');
  assert.ok(/getElementById\('topbarNavBack'\)[\s\S]{0,200}?editorNav\.index <= 0/.test(source), 'updateEditorNavButtons disables Back at the start of the stack');
  assert.ok(source.includes('setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});'), 'a user-initiated tab switch of ANY tab kind records through the shared focus path');
  // keyboard chords — Mod+Alt+[ / Mod+Alt+] drive editor back/forward (Mod+[ / ] stay with CM indent).
  assert.ok(/event\.altKey && \(event\.code === 'BracketLeft' \|\| event\.code === 'BracketRight'\)/.test(source), 'editor nav has a Mod+Alt+bracket keyboard chord');
  assert.ok(/BracketLeft'\) editorNavBack\(\)/.test(source) && source.includes('else editorNavForward()'), 'the bracket chord maps [ to back and ] to forward');
  // an auto-focus-driven focus change records nav history (debounced, gated on autoFocusEnabled).
  assert.ok(source.includes('function recordAutoFocusNav(item, previousItem = null)'), 'auto-focus nav recorder exists');
  assert.ok(source.includes('recordAutoFocusNav(item, previousItem);'), 'setFocusedPanelItem records auto-focus nav with the previous focus');
  assert.ok(/recordAutoFocusNav[\s\S]{0,240}?if \(!autoFocusEnabled[\s\S]{0,240}?focusedPanelItem === item\) recordFocusNavTransition\(previousItem, item\)/.test(source), 'it is gated on autoFocusEnabled and debounced to the landed focus');
  assert.ok(!source.includes('file-editor-nav-control'), 'the per-pane editor nav group is fully removed (relocated to the topbar)');
});

// inline git blame — toolbar toggle + a CodeMirror line decoration (::after annotation).
test('t@8701', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('file-editor-blame-panel'), 'the editor toolbar has a blame toggle button');
  assert.ok(source.includes('file-editor-icon-blame'), 'the blame toggle uses the shared editor icon box, not an unaligned text glyph');
  const editorCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.file-editor-icon-blame::before\s*\{[^}]*top:\s*50%[^}]*left:\s*50%[^}]*transform:\s*translate\(-50%, -50%\)/.test(editorCss), 'Blame outer circle is explicitly centered inside the editor icon box');
  assert.ok(/\.file-editor-icon-blame::after\s*\{[^}]*top:\s*50%[^}]*left:\s*50%[^}]*transform:\s*translate\(-50%, -50%\)/.test(editorCss), 'Blame center dot is explicitly centered inside the editor icon box');
  assert.ok(source.includes('function codeMirrorBlameExtension(api, path)'), 'a CodeMirror blame extension decorates the cursor line');
  assert.ok(source.includes("'data-blame': blameAnnotationText(info)"), 'the cursor line gets the dim blame annotation via data-blame (CSS ::after)');
  assert.ok(source.includes('codeMirrorBlameExtension(api, path)'), 'the blame extension is wired into the editable editor extensions');
  const css = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.cm-line\[data-blame\]::after\s*\{[^}]*var\(--code-comment\)/.test(css), 'the blame annotation uses the theme-aware --code-comment token');
  // Fix: blame state is in the editor config signature, so toggling blame OFF rebuilds the editor and
  // the annotations are removed (the plugin is added/removed only at build time).
  assert.ok(source.includes('blame: fileEditorBlameEnabled'), 'blame is part of the editor config signature so a toggle rebuilds (annotations clear on OFF)');
  // follow-up: an all-lines blame Preference — the extension branches on it and the signature
  // carries it (so toggling the pref rebuilds the decorations).
  assert.ok(source.includes('blameAllLines: fileEditorBlameAllLines'), 'blame-all-lines is in the editor config signature');
  assert.ok(/if \(fileEditorBlameAllLines\)[\s\S]{0,260}view\.visibleRanges/.test(source), 'all-lines blame decorates every visible line');
  assert.ok(source.includes('data-setting-path="editor.blame_all_lines"') || source.includes("path: 'editor.blame_all_lines'"), 'Preferences exposes the all-lines blame toggle');
  // Blame + Diff buttons are adjacent git-history controls, but Blame stays available after Diff learns
  // a file is clean so inline blame still works in normal edit mode.
  assert.ok(/file-editor-blame-panel[\s\S]{0,260}file-editor-diff-panel/.test(source), 'Blame and Diff buttons are adjacent toolbar controls');
  assert.ok(source.includes('state.gitRoot = payload.git_root ? normalizeDirectoryPath(payload.git_root) : \'\''), 'editor state carries the per-file git_root from /api/fs/read through the shared normalizer without turning empty root into /');
  assert.ok(/function fileStateHasRepo\(path, state\)[\s\S]*state\?\.gitRoot \? normalizeDirectoryPath\(state\.gitRoot\) : ''[\s\S]*pathIsInsideDirectory\(normalized, root\)/.test(source), 'editor git actions require the file itself to be inside its reported repo root');
  assert.ok(/function fileStateHasUsefulGitHistory[\s\S]*state\?\.gitTracked === true[\s\S]*state\?\.gitHasHistory === true[\s\S]*state\.gitHistory\.length > 1/.test(source), 'file-history metadata requires an actual multi-commit file history');
  assert.ok(/function fileEditorGitActionControlsVisible[\s\S]*fileStateHasRepo\(path, state\)[\s\S]*fileStateHasUsefulGitHistory\(state\)[\s\S]*confirmedNoDiff/.test(source), 'Diff visibility hides files outside repos, non-git, creation-only, stale-history, or confirmed-clean files');
  assert.ok(/function fileEditorBlameControlsVisible[\s\S]*fileStateHasRepo\(path, state\)[\s\S]*fileStateHasUsefulGitHistory\(state\)/.test(source), 'Blame visibility depends on repo membership and useful file history, not current diff availability');
  assert.ok(/function updateFileEditorBlameButton[\s\S]*fileEditorBlameControlsVisible\(path, state, item\)/.test(source), 'blame button uses the blame-specific history visibility helper');
  assert.ok(/function updateFileEditorBlameButton[\s\S]*editorViewModeFor\(path, item\) === 'edit'[\s\S]*button\.disabled = !visible \|\| !editable/.test(source), 'Blame is clickable only in normal edit mode');
  assert.ok(/file-editor-blame-panel'\)\?\.addEventListener\('click'[\s\S]*event\.currentTarget\?\.disabled\) return/.test(source), 'disabled Blame clicks do not toggle the global blame preference');
  assert.ok(/function updateFileEditorDiffButton[\s\S]*const visible = fileEditorGitActionControlsVisible\(path, state, item\)[\s\S]*button\.hidden = !visible[\s\S]*button\.disabled = !visible/.test(source), 'diff button uses the shared git-action visibility predicate and disabled state');
  assert.ok(/file-editor-diff-panel'\)\?\.addEventListener\('click'[\s\S]*event\.currentTarget\?\.disabled \|\| event\.currentTarget\?\.hidden\) return/.test(source), 'hidden or disabled Differ clicks cannot enter diff mode');
  assert.ok(source.includes('state.gitTracked = payload.git_tracked === true'), 'editor state carries the git_tracked flag from /api/fs/read through the shared normalizer');
});

// Search scroll fix: navigating matches re-centers the match horizontally, so a short-line match in a
// doc with a long line elsewhere is no longer left scrolled fully right (off-screen / blank).
test('t@8739', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('function codeMirrorSearchScrollFix(api)'), 'search-scroll fix extension exists');
  assert.ok(source.includes('codeMirrorSearchScrollFix(api)'), 'search-scroll fix is wired into the editable editor extensions');
  assert.ok(/isUserEvent\?\.\('select\.search'\)/.test(source), 'the fix triggers only on search-driven selection changes');
  assert.ok(/scrollIntoView\(head,\s*\{x:\s*'center'/.test(source), 'the fix re-centers the match horizontally');
});

// S2: a file open as a tab shows ONCE in the merged palette — its deduped Tabs row (carrying
// both edit + preview chips) wins; the Recent/Files duplicate is dropped. Files-only / empty-box keep Recent.
test('t@8749', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const candidateStart = source.indexOf('function commandPaletteCandidateItems(');
  const candidateEnd = source.indexOf('function commandPaletteItems(', candidateStart);
  const candidateBody = source.slice(candidateStart, candidateEnd);
  const rankStart = source.indexOf('function commandPaletteRankItems(');
  const rankEnd = source.indexOf('function commandPaletteMatches(', rankStart);
  const rankBody = source.slice(rankStart, rankEnd);
  const fileNameBonusStart = source.indexOf('function commandPaletteFileNameBonus(');
  const fileNameBonusEnd = source.indexOf('function commandPaletteFinderAliasBonus(', fileNameBonusStart);
  const fileNameBonusBody = source.slice(fileNameBonusStart, fileNameBonusEnd);
  assert.ok(source.includes('const openTabPaths = new Set(commandPaletteAllTabItems().map(fileItemPath).filter(Boolean))'), 'S2: merged palette collects open-tab file paths');
  assert.ok(/dedupedFileItems = fileQuickOpenItems\(\)\.filter\(item => !openTabPaths\.has\(commandPaletteFilePath\(item\)\)\)/.test(source), 'S2: open-tab files are dropped from the file list so a file appears once total');
  assert.ok(/return \[\.\.\.dedupedFileItems, \.\.\.commandPaletteCommandItems\(\)\]/.test(source), 'S2: merged palette returns deduped files then commands');
  assert.ok(candidateStart > 0 && candidateEnd > candidateStart, 'DOIT.55: unified command palette candidate provider exists');
  assert.ok(candidateBody.includes('return commandPaletteMergedItems()'), 'DOIT.55: typed Cmd-P and Shift-Cmd-P queries use one merged candidate universe');
  assert.ok(candidateBody.includes('mode === \'files\' ? fileQuickOpenItems() : commandPaletteCommandItems()'), 'DOIT.55: mode only chooses the empty-query home category');
  assert.ok(rankStart > 0 && rankEnd > rankStart, 'DOIT.55: shared command palette ranker exists');
  assert.ok(rankBody.includes('commandPaletteItemScore(item, query, options)'), 'DOIT.55: both surfaces rank through the shared scorer');
  assert.ok(rankBody.includes('commandPaletteMixFirstScreenResults(ranked, query, options)'), 'DOIT.55 follow-up: shared ranker keeps first-screen file/pane results mixed after scoring');
  assert.ok(source.includes('class="command-palette-status" aria-live="polite" hidden'), 'search loading indicator is part of the palette chrome, not just the empty state');
  assert.ok(/renderCommandPaletteResults[\s\S]*input\.setAttribute\('aria-busy', fileQuickOpenLoading \? 'true' : 'false'\)[\s\S]*status\.textContent = text/.test(source), 'search loading indicator updates while local results remain visible');
  assert.ok(/renderCommandPaletteResults[\s\S]*commandPaletteRankItems\(commandPaletteItems\(\), query\)/.test(source), 'DOIT.55: rendering uses the shared provider/ranker path');
  assert.equal(source.includes('function commandPaletteItemPriorityRank'), false, 'DOIT.55: old per-surface priority rank fork stays removed');
  assert.ok(/const searchRankWeights = Object\.freeze\(\{[\s\S]*domainPrior:[\s\S]*recencyHalfLifeSeconds:[\s\S]*repoAffinity:[\s\S]*mixWindow:/.test(source), 'DOIT.55: ranking weights live in one exported table');
  assert.equal(/100000|60000|20000/.test(fileNameBonusBody), false, 'DOIT.55: file-name ranking bonuses use searchRankWeights, not inline legacy constants');
});

// S14: opt-in tab-drag timing instrumentation (OFF by default) to diagnose the ~500ms first-drag
// delay by measuring the real bucket, instead of guessing setDragImage is the cause.
test('t@8758', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes("storageGet('yolomux.debugDragTiming') === '1'"), 'S14: drag timing is gated behind an opt-in storage flag (no permanent user-visible perf log)');
  assert.ok(source.includes("dragTimingMark('pointerdown')"), 'S14: pointerdown is marked (pointerdown->dragstart bucket)');
  assert.ok(source.includes("dragTimingMark('startSessionDrag:begin')") && source.includes("dragTimingMark('startSessionDrag:end')"), 'S14: startSessionDrag is bracketed by timing marks');
  assert.ok(source.includes("dragTimingMarkOnce('dragMeasureStrip:first')") && source.includes("dragTimingMarkOnce('paneTabDropPlacement:first')"), 'S14: first strip-measure and drop-placement are marked');
  assert.ok(source.includes('dragTimingReport()'), 'S14: the per-bucket report fires at drag end');
  assert.ok(source.includes('function showDragTimingOverlay(') && source.includes("el.className = 'drag-timing-overlay'"), 'S14: a copyable on-page timing overlay (no DevTools) is rendered at drag end');
});

// root cause + fix: while Claude/tmux owns the mouse, copied text arrives as an OSC 52 clipboard
// escape; xterm.js drops it unless a handler is registered. The bridge decodes it and writes the browser
// clipboard, and never answers '?' read queries (no clipboard exfiltration).
test('t@8771', () => {
  const api = loadYolomux('?platform=mac', ['1'], 'https:', 'MacIntel');
  const b64 = text => Buffer.from(text, 'utf8').toString('base64');
  // pure decoder
  assert.equal(api.osc52ClipboardText(`c;${b64('hello from claude')}`), 'hello from claude', 'OSC 52 base64 payload decodes');
  assert.equal(api.osc52ClipboardText(`c;${b64('héllo ✓ 中文')}`), 'héllo ✓ 中文', 'OSC 52 decodes multibyte UTF-8 correctly');
  assert.equal(api.osc52ClipboardText('c;?'), null, 'OSC 52 read query (?) is never treated as text');
  assert.equal(api.osc52ClipboardText('c;'), null, 'empty OSC 52 payload is ignored');
  assert.equal(api.osc52ClipboardText('no-semicolon'), null, 'malformed OSC 52 without selector is ignored');
  assert.equal(api.osc52ClipboardText('c;!!!not-base64!!!'), null, 'invalid base64 is ignored instead of copying garbage');
  // behavioral: registered on ident 52; payload lands on the clipboard; queries are consumed without reply
  let registered = null;
  const fakeTerm = {parser: {registerOscHandler(ident, handler) { registered = {ident, handler}; }}};
  assert.equal(api.installTerminalOsc52BridgeForTest('1', fakeTerm), true, 'OSC 52 bridge installs when the parser API exists');
  assert.equal(registered?.ident, 52, 'bridge registers on OSC ident 52');
  api.clearClipboardTextForTest();
  assert.equal(registered.handler(`c;${b64('osc52 payload text')}`), true, 'OSC 52 write is consumed');
  assert.equal(api.clipboardTextForTest(), 'osc52 payload text', 'OSC 52 payload is written to the browser clipboard');
  api.clearClipboardTextForTest();
  assert.equal(registered.handler('c;?'), true, 'OSC 52 read query is consumed (no reply, no leak)');
  assert.equal(api.clipboardTextForTest(), '', 'OSC 52 read query never touches the clipboard');
  assert.equal(api.installTerminalOsc52BridgeForTest('1', {}), false, 'bridge degrades gracefully without parser API');
  // wiring + instrumentation guards
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(source.includes('installTerminalOsc52Bridge(session, term);'), 'OSC 52 bridge is wired into terminal startup');
  assert.ok(/registerOscHandler\(52, data =>/.test(source), 'bridge registers the OSC 52 parser handler');
  assert.ok(source.includes('writeTerminalTextToClipboard(text, `copied ${text.length} chars`)'), 'bridge routes through the shared terminal clipboard-write chain');
  assert.ok(source.includes("storageGet('yolomux.debugCopy') === '1'"), 'copy-path debug logging is gated behind an opt-in storage flag');
  assert.ok(source.includes("copyDebug('shortcut'") && source.includes("copyDebug('osc52'") && source.includes("copyDebug('clipboard'"), 'N1: shortcut, OSC 52, and clipboard-write stages each log one compact debug event');
});

// right-click must not clear the terminal highlight; the menu copies the selection captured at
// right-click time.
test('t@8804', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/container\.addEventListener\('mousedown', event => \{[\s\S]*?event\.button !== 2[\s\S]*?rightClickSelection = terminalSelectedText\(term, container\);[\s\S]*?event\.stopPropagation\(\);[\s\S]*?\}, \{capture: true\}\)/.test(source), 'N7: a capture-phase right-mousedown captures the selection and stops xterm clearing it');
  assert.ok(/showTerminalContextMenu\(session, term, event\.clientX, event\.clientY, container, rightClickSelection\)/.test(source), 'N7: the context menu receives the selection captured at right-click time');
  assert.ok(/copyTerminalSelection\(session, term, \{dedent, selectionText: selected\}, container\)/.test(source), 'N7: menu Copy uses the captured selection text, not a stale live re-read');
  assert.ok(/const selected = options\.selectionText != null \? options\.selectionText : terminalSelectedText\(term, container\)/.test(source), 'N7: copyTerminalSelection honors an explicit captured selection');
});

// DOIT.58 B1-B7: the Tabber (Finder pane's third mode) — source guards that rows route through the
// shared row pipeline (no forked *RowHtml builder), plus a behavioral test of the tree assembly.
test('t@tabber', () => {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  assert.ok(/mode === 'diff' \|\| mode === 'tabber' \? mode : 'files'/.test(source), 'B1: normalizeFileExplorerMode accepts files|diff|tabber');
  assert.ok(/if \(options\.mode === 'tabber'\) return updateTabberRow\(/.test(source), 'B3: updateFileTreeRow dispatches tabber rows to updateTabberRow (shared row entry point)');
  assert.ok(/renderTreeChildren\(container, '\/', entries, 0, \{[\s\S]*?mode: 'tabber'/.test(source), 'B3: renderTabberTree drives the shared renderTreeChildren with mode:tabber');
  assert.ok(/updateFileTreeRowContents\(row, icon, data\.label/.test(source), 'B3: updateTabberRow fills columns via the shared updateFileTreeRowContents');
  assert.equal(/function tabberRowHtml|function renderTabberRowHtml|function tabberFileRowHtml/.test(source), false, 'B3: no bespoke tabber *RowHtml builder forked off the shared pipeline');

  const api = loadYolomux();
  assert.equal(api.normalizeFileExplorerMode('tabber'), 'tabber');
  assert.equal(api.normalizeFileExplorerMode('bogus'), 'files');
  const switcher = api.fileExplorerModeSwitcherHtml();
  assert.ok(/data-file-explorer-mode-set="tabber"/.test(switcher), 'B1: the mode switcher renders a Tabber segment');
  assert.ok(/data-file-explorer-mode-set="files"[\s\S]*data-file-explorer-mode-set="diff"[\s\S]*data-file-explorer-mode-set="tabber"/.test(switcher), 'B1: Finder / Differ / Tabber order');

  api.setTranscriptInfoForTest('1', {
    project: {git: {branch: 'devbranch'}},
    panes: [
      {window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude', current_path: '/home/u/proj'},
      {window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/home/u'},
    ],
  });
  api.setTranscriptInfoForTest('2', {
    panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/home/u/two'}],
  });
  const {entries, entriesByDir} = api.buildTabberTree();
  const sessionEntries = entries.filter(e => e.tabber?.type === 'session');
  const s1 = sessionEntries.find(e => e.tabber.session === '1');
  assert.ok(s1, 'B2: tmux session 1 appears at level 0');
  assert.ok(String(s1.tabber.statusText || '').length > 0, 'B2: the repo branch annotates the session row');
  const windows = entriesByDir.get(`/${s1.name}`);
  assert.ok(Array.isArray(windows) && windows.length === 2, 'B2: session 1 expands to its two tmux windows');
  assert.equal(windows[0].tabber.type, 'window', 'B2: level 1 rows are windows');
  assert.ok(/0:claude/.test(windows[0].tabber.label), 'B2: window label is index:process');
  const panes = entriesByDir.get(`/${s1.name}/${windows[0].name}`);
  assert.ok(Array.isArray(panes) && panes.length === 1, 'B2: window 0 holds one pane');
  assert.equal(panes[0].tabber.type, 'pane', 'B2: level 2 rows are panes');
  assert.ok(/claude/.test(panes[0].tabber.label), 'B2: pane row shows the foreground process');

  // Render guard: the DOM rows must show the human labels, never the synthetic node names (s_<id>/w_<i>/p_<i>).
  const renderedNames = api.tabberRenderedNamesForTest();
  assert.ok(renderedNames.length >= 4, `B3: the tabber renders session + window + pane rows (got ${renderedNames.length})`);
  assert.equal(renderedNames.some(n => /^[swp]_/.test(n)), false, `B3: rows show human labels, not synthetic node names (got ${JSON.stringify(renderedNames)})`);
  assert.ok(renderedNames.some(n => /0:claude/.test(n)), `B3: window rows show index:process (got ${JSON.stringify(renderedNames)})`);

  // B4: most-recent-first sort from the activity ledger. Make session 2 more recently active than session 1,
  // then the codex window (under session 2) must render before the claude window (under session 1).
  api.setTabberActivityForTest({activity: {'2': {last_user_input_ts: 9999}, '1': {last_user_input_ts: 100}}});
  const recencyNames = api.tabberRenderedNamesForTest();
  const codexAt = recencyNames.findIndex(n => /0:codex/.test(n));
  const claudeAt = recencyNames.findIndex(n => /0:claude/.test(n));
  assert.ok(codexAt >= 0 && claudeAt >= 0, `B4: both windows render (got ${JSON.stringify(recencyNames)})`);
  assert.ok(codexAt < claudeAt, `B4: the more-recently-active session sorts first (codex@${codexAt} before claude@${claudeAt})`);

  // L3 / B5: a session's touched paths render as repo groups + openable file rows under the session.
  api.setTabberSessionFilesForTest('1', [
    {path: 'src/app.py', abs_path: '/home/u/proj/src/app.py', repo: '/home/u/proj', status: 'M', mtime: 5000},
    {path: 'README.md', abs_path: '/home/u/proj/README.md', repo: '/home/u/proj', status: 'A', mtime: 4000},
  ]);
  const rows = api.tabberRenderedRowsForTest();
  const repoRow = rows.find(r => r.type === 'repo');
  assert.ok(repoRow && /proj/.test(repoRow.name), `L3: a repo group row renders for the touched paths (got ${JSON.stringify(rows.map(r => r.type + ':' + r.name))})`);
  const fileRow = rows.find(r => r.type === 'path' && /app\.py/.test(r.name));
  assert.ok(fileRow, `L3: a touched file renders as a path row (got ${JSON.stringify(rows.filter(r => r.type === 'path').map(r => r.name))})`);
  assert.equal(fileRow.openFile, '/home/u/proj/src/app.py', 'B5: the path row carries abs_path for open-in-editor');
  assert.ok(/data-tabber-type="path"[\s\S]*?showFileTreeContextMenu\(row, abs,/.test(source), 'B5: right-click on a path row reuses the shared file context menu (targeting abs_path)');
});

{
  // DOIT.57: drag-into-terminal suggestion registry (the transient Alt+1..9 overlay's data layer).
  const api = loadYolomux();
  assert.equal(api.fileDropCategory('/x/shot.png'), 'image');
  assert.equal(api.fileDropCategory('build.log'), 'log');
  assert.equal(api.fileDropCategory('app.py'), 'code');
  assert.equal(api.fileDropCategory('data.csv'), 'data');
  assert.equal(api.fileDropCategory('README.md'), 'doc');
  assert.equal(api.fileDropCategory('fix.diff'), 'diff');
  assert.equal(api.fileDropCategory('/some/dir', 'dir'), 'dir');
  assert.equal(api.fileDropCategory('mystery.xyz'), 'any');

  const imgClaude = api.dropSuggestionsFor('image', 'claude', 1);
  assert.ok(imgClaude.some(s => s.id === 'img-error'), 'image + agent offers diagnose-screenshot');
  assert.ok(!imgClaude.some(s => s.id === 'log-errors'), 'image category hides log-only suggestions');
  assert.ok(imgClaude.length <= 9, 'suggestions cap at 9 (the path is inserted first, so 1..9 are all actions)');
  assert.equal(api.dropSuggestionsFor('image', '', 1).length, 0, 'a plain shell pane shows no agent suggestions');

  const logErrors = api.dropSuggestionsFor('log', 'claude', 1).find(s => s.id === 'log-errors');
  assert.ok(logErrors, 'log + agent offers find-errors');
  const logClause = api.composeDropSuggestion(logErrors);
  assert.ok(/\blog\b/i.test(logClause), 'compose returns a deictic clause that refers to the file (this log)');
  assert.equal(logClause.includes('/var/log'), false, 'compose does NOT repeat the path — it is appended after the already-inserted path');
  assert.equal(api.composeDropSuggestion(imgClaude.find(s => s.id === 'img-ocr')), 'do OCR on this image and extract all of the text.', 'OCR clause reads as an appendable instruction about this image');
  assert.ok(api.dropSuggestionsFor('any', 'codex', 1).some(s => s.id === 'analyze'), 'any-category fallback offers a generic look');
}

(async () => {
  {
    const treeApi = loadYolomux('', ['1']);
    treeApi.setFileExplorerRootMode('fixed', {sync: false});
    treeApi.setFileExplorerRootForTest('/repo');
    treeApi.setFileExplorerDirListingForTest('/repo', [
      {name: 'README.md', kind: 'file'},
      {name: 'src', kind: 'dir'},
    ]);
    treeApi.setFileExplorerDirListingForTest('/repo/src', [
      {name: 'app.js', kind: 'file'},
      {name: 'lib', kind: 'dir'},
    ]);
    treeApi.setFileExplorerDirListingForTest('/repo/src/lib', [
      {name: 'util.js', kind: 'file'},
    ]);
    assert.deepStrictEqual(canonical(await treeApi.fileExplorerDirectoryPathsForRootForTest('/repo')), ['/repo/src', '/repo/src/lib'], 'Finder Expand all collects every directory under the current root through the directory listing cache');
    await treeApi.setAllFileTreeDirectoriesExpandedForTest(null, true);
    assert.deepStrictEqual(canonical(treeApi.fileExplorerExpandedForTest()), ['/repo/src', '/repo/src/lib'], 'Finder Expand all flips the full directory expansion state');
    await treeApi.setAllFileTreeDirectoriesExpandedForTest(null, false);
    assert.deepStrictEqual(canonical(treeApi.fileExplorerExpandedForTest()), [], 'Finder Collapse all clears the directory expansion state');
  }

  {
    const syncTreeApi = loadYolomux('', ['1']);
    syncTreeApi.setFileExplorerRootMode('sync', {sync: false});
    syncTreeApi.setFileExplorerRootForTest('/home/test');
    syncTreeApi.setTranscriptInfoForTest('1', {
      project: {git: {cwd: '/home/test/yolomux.dev2', root: '/home/test/yolomux.dev2'}},
      selected_pane: {current_path: '/home/test/yolomux.dev2'},
    });
    syncTreeApi.setSessionFilesPayloadForTest({
      session: '1',
      repos: [{repo: '/home/test/yolomux.dev2'}, {repo: '/home/test/ai-config'}],
      files: [
        {repo: '/home/test/yolomux.dev2', path: 'static_src/js/app.js', abs_path: '/home/test/yolomux.dev2/static_src/js/app.js'},
        {repo: '/home/test/ai-config', path: 'hooks/install.js', abs_path: '/home/test/ai-config/hooks/install.js'},
      ],
    });
    syncTreeApi.setFileExplorerDirListingForTest('/home/test', [
      {name: 'ai-config', kind: 'dir'},
      {name: 'unrelated', kind: 'dir'},
      {name: 'yolomux.dev2', kind: 'dir'},
    ]);
    syncTreeApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev2', [
      {name: 'static_src', kind: 'dir'},
    ]);
    syncTreeApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev2/static_src', [
      {name: 'js', kind: 'dir'},
    ]);
    syncTreeApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev2/static_src/js', [
      {name: 'app.js', kind: 'file'},
    ]);
    syncTreeApi.setFileExplorerDirListingForTest('/home/test/ai-config', [
      {name: 'hooks', kind: 'dir'},
    ]);
    syncTreeApi.setFileExplorerDirListingForTest('/home/test/ai-config/hooks', [
      {name: 'install.js', kind: 'file'},
    ]);
    await syncTreeApi.setAllFileTreeDirectoriesExpandedForTest(null, true);
    assert.deepStrictEqual(canonical(syncTreeApi.fileExplorerExpandedForTest()), [
      '/home/test/ai-config',
      '/home/test/ai-config/hooks',
      '/home/test/yolomux.dev2',
      '/home/test/yolomux.dev2/static_src',
      '/home/test/yolomux.dev2/static_src/js',
    ], 'Finder Sync Expand expands affected paths without crawling unrelated home directories');
  }

  {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.leafNode('left');
    slots.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);

    const sent = [];
    api.registerTerminalForTest('1', {focus() {}}, {
      readyState: 1,
      send(message) {
        sent.push(JSON.parse(message));
      },
    });

    const today = new Date();
    const generatedName = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}-001.png`;
    const calls = [];
    api.setFetchForTest((url, options = {}) => {
      calls.push({url: String(url), method: options.method || 'GET', body: options.body});
      if (String(url).startsWith('/api/upload')) {
        return Promise.resolve(jsonResponse({files: [{path: `/home/test/${generatedName}`}]}));
      }
      if (String(url).startsWith('/api/transcripts')) {
        return Promise.resolve(jsonResponse({session_order: ['1'], sessions: {'1': {agents: []}}}));
      }
      if (String(url).startsWith('/api/auto-approve')) {
        return Promise.resolve(jsonResponse({sessions: {}}));
      }
      return Promise.resolve(jsonResponse({items: [], session: '1'}));
    });

    // DOIT.57 regression: a pasted image must ALWAYS insert its path reference, even with the suggestion
    // overlay on (default). The overlay is additive (it appends a clause); it never replaces the insert.
    // This pane has no agent, so no overlay rows render — only the path insert is asserted here.
    api.setClientSettingsPatchForTest({uploads: {show_suggestions: true}});
    api.bindClipboardPasteForTest();
    api.bindClipboardPasteForTest();
    const pasteListeners = api.documentListenersForTest('paste');
    assert.equal(pasteListeners.length, 1, 'image paste installs one document paste listener');

    const pasteEvent = {
      clipboardData: {
        items: [{
          kind: 'file',
          type: 'image/png',
          getAsFile() {
            return {name: 'image.png', type: 'image/png', size: 7};
          },
        }],
      },
      target: null,
      defaultPrevented: false,
      propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
    };
    pasteListeners[0](pasteEvent);
    await flushAsyncWork();

    assert.equal(pasteEvent.defaultPrevented, true, 'image paste is captured before xterm receives raw clipboard data');
    assert.equal(pasteEvent.propagationStopped, true, 'image paste stops propagation after starting upload');
    assert.equal(calls[0].url, '/api/upload?session=1', 'image paste uploads to the active terminal session');
    assert.equal(calls[0].method, 'POST');
    assert.equal(calls[0].body.fields[0].name, 'files');
    assert.equal(calls[0].body.fields[0].filename, generatedName);
    assert.deepStrictEqual(sent[0], {
      type: 'input',
      data: `[Image #1] '/home/test/${generatedName}' `,
    }, 'pasted image upload inserts the image reference into xterm');
  }

  {
    const api = loadYolomux();
    const calls = [];
    api.setFetchForTest((url, options = {}) => {
      const body = JSON.parse(options.body || '{}');
      calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
      return Promise.resolve(jsonResponse({
        responses: (body.requests || []).map(request => ({
          id: request.id,
          ok: true,
          status: 200,
          payload: {path: request.path, entries: [{name: 'TODO.md', kind: 'file'}]},
        })),
      }));
    });
    const first = api.fetchDirectoryForTest('/home/test');
    const second = api.fetchDirectoryForTest('/home/test/');
    await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(canonical(calls), [{
      method: 'POST',
      requests: [{id: 1, path: '/home/test', type: 'list'}],
      url: '/api/fs/batch',
    }], 'concurrent identical directory listings share one batched backend request');
    const [firstEntries, secondEntries] = await Promise.all([first, second]);
    assert.strictEqual(firstEntries, secondEntries, 'shared directory listing callers receive the same entries object');
    assert.equal(firstEntries[0].name, 'TODO.md');
    const cachedEntries = await api.fetchDirectoryForTest('/home/test');
    assert.strictEqual(cachedEntries, firstEntries, 'completed directory listing is reused by the short TTL cache');
    assert.equal(calls.length, 1, 'short TTL cache avoids an immediate repeat directory listing');
  }

  {
    const api = loadYolomux();
    const calls = [];
    api.setFetchForTest((url, options = {}) => {
      const body = JSON.parse(options.body || '{}');
      calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
      return Promise.resolve(jsonResponse({
        responses: (body.requests || []).map((request, index) => ({
          id: request.id,
          ok: true,
          status: 200,
          payload: {path: request.path, entries: [{name: index === 0 ? 'a.txt' : 'b.txt', kind: 'file'}]},
        })),
      }));
    });
    const first = api.fetchDirectoryForTest('/home/test', {fresh: true});
    const second = api.fetchDirectoryForTest('/home/test', {fresh: true});
    await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(canonical(calls), [{
      method: 'POST',
      requests: [
        {id: 1, path: '/home/test', type: 'list'},
        {id: 2, path: '/home/test', type: 'list'},
      ],
      url: '/api/fs/batch',
    }], 'explicit fresh directory listings stay distinct inside one batch');
    const [firstEntries, secondEntries] = await Promise.all([first, second]);
    assert.equal(firstEntries[0].name, 'a.txt');
    assert.equal(secondEntries[0].name, 'b.txt');
  }

  {
    const api = loadYolomux();
    api.setFileExplorerLastListErrorForTest('/home/test/blocked', 'Cannot open blocked');
    api.setFileExplorerPushRefreshDepthForTest(1);
    assert.equal(await api.fetchDirectoryForTest('/home/test'), null, 'P4: push-refresh-depth returns a benign null');
    assert.equal(api.currentFileExplorerListErrorForTest('/home/test'), '', 'P4: stale error from another path never applies to this path');
    api.setFileExplorerLastListErrorForTest('/home/test', 'Cannot open /home/test');
    assert.equal(await api.fetchDirectoryForTest('/home/test'), null, 'P4: push-refresh-depth still returns null when the same path had a stale error');
    assert.equal(api.currentFileExplorerListErrorForTest('/home/test'), '', 'P4: benign null clears the stale error for the current path');
    api.setFileExplorerPushRefreshDepthForTest(0);
    api.setFetchForTest((url, options = {}) => {
      const body = JSON.parse(options.body || '{}');
      return Promise.resolve(jsonResponse({
        responses: (body.requests || []).map(request => ({
          id: request.id,
          ok: false,
          status: 403,
          error: `denied ${request.path}`,
        })),
      }));
    });
    assert.equal(await api.fetchDirectoryForTest('/home/test/secret', {fresh: true}), null, 'P4: real list failures still return null');
    assert.equal(api.currentFileExplorerListErrorForTest('/home/test/secret'), 'denied /home/test/secret', 'P4: real list failures record a path-keyed error');
    assert.equal(api.currentFileExplorerListErrorForTest('/home/test'), '', 'P4: real errors remain scoped to the failed path');
  }

  {
    const api = loadYolomux();
    const calls = [];
    api.setFetchForTest((url, options = {}) => {
      const body = JSON.parse(options.body || '{}');
      calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
      return Promise.resolve(jsonResponse({
        responses: (body.requests || []).map(request => ({
          id: request.id,
          ok: true,
          status: 200,
          payload: {path: request.path, kind: 'dir', repo: {root: request.path}},
        })),
      }));
    });
    const first = api.fetchFilePathInfoForTest('/home/test');
    const second = api.fetchFilePathInfoForTest('/home/test/');
    await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(canonical(calls), [{
      method: 'POST',
      requests: [{id: 1, path: '/home/test', type: 'info'}],
      url: '/api/fs/batch',
    }], 'concurrent identical path-info lookups share one batched backend request');
    const [firstInfo, secondInfo] = await Promise.all([first, second]);
    assert.strictEqual(firstInfo, secondInfo, 'shared path-info callers receive the same payload object');
    assert.equal(firstInfo.kind, 'dir');
    const cachedInfo = await api.fetchFilePathInfoForTest('/home/test');
    assert.strictEqual(cachedInfo, firstInfo, 'completed path-info lookup is reused by the short TTL cache');
    assert.equal(calls.length, 1, 'short TTL cache avoids an immediate repeat path-info lookup');
  }

  {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/Promise\.all\(directories\.map\(async directory =>/.test(source), 'periodic Finder refresh starts watched directory checks together so fs/list can batch');
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
}).finally(() => {
  console.log(`\nlayout suite: ${__testPass} passed, ${__testFail} failed`);
  if (__testFail) process.exitCode = 1;
});

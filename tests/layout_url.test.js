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
  createAppMenuCommand,
  backgroundTabItems,
  canPaneExpand,
  codeMirrorHtmlSemanticEmphasisExtension,
  codeMirrorSearchMatches,
  codeMirrorSearchMatchSummary,
  emptyPlaceholderPaneState,
  emptyLayoutSlots,
  fileEditorPaneTabHtml,
  fileQuickOpenItem,
  fileQuickOpenItems,
  fileQuickOpenRootForSearch,
  changesPaneTabHtml,
  changesPanelHtml,
  fileExplorerChangesPanelHtml,
  changeFileRowHtml,
  diffRefControlsHtml,
  parseUnifiedDiffLineClasses,
  globalActivitySummaryHtml,
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
  childPathParts,
  inactiveTabItems,
  itemInLayout,
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
  dropIntentAllowsSession,
  directoryEntriesSignature,
  editorWrapValue,
  editorViewModeFor,
  editorPreviewModeAvailable,
  setFileEditorViewMode,
  activeEditorSchemeForTest() { return activeEditorScheme(); },
  configuredEditorSchemeForMode,
  editorSchemeCssVariables,
  editorThemeLabel,
  setFileEditorThemeMode,
  cycleEditorThemeMode,
  fileEditorThemeModeForTest() { return fileEditorThemeMode; },
  editorVisualHighlightHtml,
  editorVisualLineFragments,
  expandPaneFromLayout,
  infoBranchRows,
  fileContextMenuState,
  fileEditorItemFor,
  fileEntryChanged,
  fileItemPath,
  filePanelItemsForPath,
  imageOpenUsesSharedViewer,
  imageViewerItemFor,
  openFileEditorItems,
  pullRequestStatusLabel,
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
  rawFileUrl,
  rawFileDownloadUrl,
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
  documentElementStyleForTest() {
    return document.documentElement.style;
  },
};`, context);
  return context.__layoutTestApi;
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
  assert.ok(terminalToolbarBeforeFinderFocus.includes('data-tab-name="summary"'));
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
  const anchoredMarkdown = api.markdownTextWithSourceAnchors('# TITLE\n\n```sh\n# code\n```\n## Next');
  assert.ok(anchoredMarkdown.includes('data-source-line="1"'));
  assert.ok(anchoredMarkdown.includes('data-source-line="6"'));
  assert.equal(anchoredMarkdown.includes('data-source-line="3"'), false);
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
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.editorPreviewModeAvailable('/home/test/README.md'), true);
  assert.equal(api.editorPreviewModeAvailable('/home/test/index.html'), true);
  assert.equal(api.editorPreviewModeAvailable('/home/test/app.py'), false);
  const htmlPreview = new TestElement('html-preview');
  api.renderEditorPreviewPane(htmlPreview, '/home/test/index.html', '<style>h1{color:red}</style><h1>Hello</h1><script>window.bad = true</script>');
  assert.equal(htmlPreview.classList.contains('html-preview-body'), true);
  assert.equal(htmlPreview.classList.contains('code-preview-body'), false);
  assert.equal(htmlPreview.children.length, 1);
  assert.equal(htmlPreview.children[0].className, 'file-editor-html-preview');
  assert.equal(htmlPreview.children[0].attributes.sandbox, '', 'HTML preview iframe is sandboxed with scripts disabled');
  assert.ok(htmlPreview.children[0].srcdoc.includes('<h1>Hello</h1>'), 'HTML preview renders markup through srcdoc');
  api.setFileEditorViewMode('/home/test/app.py', 'split');
  assert.equal(api.editorViewModeFor('/home/test/app.py'), 'edit');
  api.setFileEditorViewMode('/home/test/README.md', 'split');
  assert.equal(api.editorViewModeFor('/home/test/README.md'), 'split');
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
  assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,yosup,files,preferences,changes,image-viewer,file-editor,file-preview');
  assert.equal(api.tabTypeForItem('__files__').key, 'files');
  assert.equal(api.tabTypeForItem('__changes__').key, 'changes');
  assert.equal(api.tabTypeForItem('image:/home/test/screen.png').key, 'image-viewer');
  assert.equal(api.tabTypeForItem('file:/home/test/README.md').key, 'file-editor');
  assert.equal(api.fileItemPath('image:/home/test/screen.png'), '/home/test/screen.png');
  api.setSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    repos: [{repo: '/repo/app', count: 2, touched_count: 2}],
    files: [
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
      {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: 'src/new.py', abs_path: '/repo/app/src/new.py', mtime: 200, added: 8, removed: 0},
    ],
  });
  assert.ok(api.changesPaneTabHtml().includes('changes-count-badge'));
  const changesHtml = api.changesPanelHtml();
  assert.ok(changesHtml.includes('/repo/app'));
  assert.ok(changesHtml.includes('src/new.py'));
  assert.ok(changesHtml.includes('changes-diff-add">+8</span>'), 'changed-file rows include green added counts');
  assert.ok(changesHtml.includes('changes-file-agent'), 'changed-file rows show the agent icon slot');
  assert.ok(changesHtml.includes('changes-file-date'), 'changed-file rows wrap the date for skinny styling');
  assert.ok(changesHtml.includes('data-diff-ref-from'), 'Changes pane exposes FROM ref picker');
  assert.ok(changesHtml.includes('data-diff-ref-to'), 'Changes pane exposes TO ref picker');
  const compactChangeHtml = api.changeFileRowHtml(
    {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
    {compact: true},
  );
  assert.ok(/changes-status[^>]*>M<\/span>[\s\S]*changes-file-name[^>]*>README\.md<\/span>[\s\S]*changes-file-agent[\s\S]*changes-file-meta[\s\S]*changes-diff-add[^>]*>\+2<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*changes-file-date/.test(compactChangeHtml), 'compact changed-file row order is status, file, AI icon, counts, date');
  assert.equal(changesHtml.includes('>codex<'), false, 'changed-file rows do not spell out the agent kind');
  assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'));
  assert.ok(changesHtml.includes('data-open-change-status="A"'), 'changed-file clicks carry status for deleted-file diff opens');
  api.setFileExplorerSessionFilesPayloadForTest({
    session: '1',
    loaded: true,
    errors: [],
    repos: [{repo: '/repo/app', count: 2, touched_count: 2}],
    files: [
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
    ],
  });
  assert.ok(api.fileExplorerChangesPanelHtml().includes('Modified files'), 'Finder embeds a modified-files panel');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('diff-ref-controls compact'), 'Finder modified-files panel exposes compact diff refs');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('data-session-files-display-toggle'), 'Finder modified-files panel uses one density toggle');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-file-row detailed'), 'Finder modified-files panel defaults to detailed rows');
  assert.equal(api.fileExplorerChangesPanelHtml().includes('>Compact</button>'), false, 'Finder density toggle is an icon, not paired text buttons');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-add">+2</span>'), 'Finder modified-files panel shows green added counts');
  assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-remove">-1</span>'), 'Finder modified-files panel shows red removed counts');
  const changedFilesCss = fs.readFileSync('static/yolomux.css', 'utf8');
  assert.ok(/\.changes-status\s*\{[\s\S]*width:\s*13px/.test(changedFilesCss), 'modified-file status chips are skinny');
  assert.ok(/\.changes-file-name\s*\{[\s\S]*font-weight:\s*500/.test(changedFilesCss), 'modified-file names are not bold');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*overflow-y:\s*scroll/.test(changedFilesCss), 'Finder modified-files scrollbar stays visible');
  assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*scrollbar-gutter:\s*stable/.test(changedFilesCss), 'Finder modified-files reserves scrollbar gutter');
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
  assert.ok(appSource.includes('/api/fs/diff?path=${encodeURIComponent(path)}&${diffRefQueryString()}'), 'editor diff requests carry FROM/TO refs');
  assert.ok(appSource.includes('data-file-explorer-new-folder'), 'Finder header exposes new-folder action');
  assert.ok(/switchFileExplorerChangesSession\(item\)/.test(appSource), 'tmux focus switches the Finder modified-files session immediately');
  assert.ok(/fetchSessionFiles\(\{destination: 'finder', session, silent: true, force: true\}\)/.test(appSource), 'tmux focus forces a fresh Finder modified-files fetch even if an older request is in flight');
  assert.equal(appSource.includes("state.kind === 'text' && !fileEditorAutosaveEnabled"), false, 'clean external file changes auto-reload even when autosave is off');
  assert.equal(appSource.includes('data-file-editor-close'), false, 'pane frame close uses the pane-close path, not active file-tab close');
  assert.equal(filesTab.includes('agent-icon file'), false);
  assert.ok(api.menuTabCommand('__files__').html.includes('app-menu-ui-icon-finder'));
  assert.ok(api.menuTabCommand('__prefs__').html.includes('app-menu-ui-icon-gear'));
  assert.ok(api.menuTabCommand('__info__').html.includes('app-menu-ui-icon-branch-info'));
  assert.ok(api.menuTabCommand('__yosup__').html.includes('app-menu-ui-icon-yosup'));
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
  assert.ok(macPaneControls.includes('pane-minimize pc-window-control pc-minimize'));
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
  const finderLayoutBeforeToggle = api.layoutParamValue(api.currentSlots());
  api.toggleFileExplorerShortcut();
  assert.equal(api.itemInLayout('__files__'), false, 'app shortcut hides the Finder pane');
  api.toggleFileExplorerShortcut();
  assert.equal(api.layoutParamValue(api.currentSlots()), finderLayoutBeforeToggle, 'app shortcut restores the prior Finder position and split size');

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
  assert.ok(preferencesCss.includes('--drop-outline: var(--accent-gold)'), 'drop-target outline color is tokenized');
  assert.ok(/body\.editor-theme-light\s*\{[\s\S]*--drop-outline:\s*#2563eb/.test(preferencesCss), 'light editor panes switch drop-target outlines to blue');
  assert.ok(/\.file-editor-cross-split-panel,\s*\n\.file-editor-save-panel/.test(preferencesCss), 'side preview button uses the compact editor toolbar button sizing');
  assert.ok(preferencesCss.includes('.file-editor-icon-side-split'), 'cross-pane side preview has a distinct icon');
  assert.ok(preferencesCss.includes('.file-editor-dialog-backdrop'), 'editor conflict and close decisions use the shared editor dialog');
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
  assert.ok(preferencesHtml.includes('Auto-focus active pane'), 'auto-focus setting names the whole active pane/view');
  assert.ok(preferencesHtml.includes('enable hover-open menus'), 'auto-focus help covers menu hover behavior');
  assert.ok(preferencesHtml.includes('Off by default'), 'auto-focus help explains the default');
  assert.equal(preferencesHtml.includes('Auto-focus terminals'), false, 'auto-focus setting is not terminal-only');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_dark_color_scheme"'), 'preferences expose the dark editor scheme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_light_color_scheme"'), 'preferences expose the light editor scheme setting');
  assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave"'), 'preferences expose editor autosave');
  assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave_delay_seconds"'), 'preferences expose editor autosave delay');
  assert.equal(preferencesHtml.includes('data-setting-path="appearance.editor_color_scheme"'), false, 'preferences do not show the legacy single mixed editor scheme setting');
  assert.ok(preferencesHtml.includes('<optgroup label="Dark">'), 'dark editor schemes are grouped under Dark');
  assert.ok(preferencesHtml.includes('<optgroup label="Light">'), 'light editor schemes are grouped under Light');
  assert.ok(preferencesHtml.indexOf('YOLOmux Dark') < preferencesHtml.indexOf('VS Code Dark+'), 'YOLOmux dark scheme appears first');
  assert.ok(preferencesHtml.indexOf('YOLOmux Light') < preferencesHtml.indexOf('GitHub Light'), 'YOLOmux light scheme appears first');
  assert.equal(api.configuredEditorSchemeForMode(true), 'dark');
  assert.equal(api.configuredEditorSchemeForMode(false), 'yolomux-light');
  assert.equal(api.fileEditorThemeModeForTest(), 'dark');
  assert.equal(api.activeEditorSchemeForTest().label, 'YOLOmux Dark');
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
  assert.equal(api.fileEditorThemeModeForTest(), 'dark', 'theme toggle returns from light to the default dark scheme');
  api.cycleEditorThemeMode();
  assert.equal(api.fileEditorThemeModeForTest(), 'yolomux-light', 'theme toggle uses the selected YOLOmux light scheme');
  api.setFileEditorThemeMode('light');
  assert.equal(api.fileEditorThemeModeForTest(), 'yolomux-light', 'legacy light storage value maps to YOLOmux Light');
  assert.equal(api.activeEditorSchemeForTest().bg, '#fbf9f4', 'YOLOmux Light is near-white beige instead of tan');
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
  assert.ok(fileMenuLabels.includes('Branch Info'));
  assert.ok(fileMenuLabels.includes("YO'sup"));
  assert.ok(fileMenuLabels.includes('Preferences'));
  assert.ok(fileMenuLabels.indexOf('Preferences') < fileMenuLabels.indexOf('Log out'));
  assert.deepStrictEqual(canonical(fileMenu.items.slice(-3).map(item => item.type === 'separator' ? '---' : item.label)), ['Preferences', '---', 'Log out']);
  const tmuxMenu = menus.find(menu => menu.id === 'tmux');
  const tmuxMenuLabels = tmuxMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(tmuxMenu.items[0].label, 'YO off');
  assert.equal(tmuxMenu.items[0].keepOpen, true);
  assert.equal(tmuxMenuLabels.includes('New tmux session'), false);
  assert.ok(tmuxMenuLabels.includes('+ Claude'));
  assert.ok(tmuxMenuLabels.includes('+ Codex'));
  assert.ok(tmuxMenu.items.find(item => item.label === '+ Codex')?.detail !== 'Create tmux session');
  assert.ok(tmuxMenuLabels.includes('Transcript'));
  assert.ok(tmuxMenuLabels.includes('AI summary'));
  assert.ok(tmuxMenuLabels.includes('Event log'));
  assert.ok(tmuxMenuLabels.includes('Pane details'));
  assert.ok(tmuxMenuLabels.includes("Rename tmux session '1'"));
  assert.ok(tmuxMenuLabels.includes('Kill session'));
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
  assert.ok(source.includes("if (mod && key === 'p' && globalShortcutTargetAllowsAppAction(event.target))"), 'file quick-open is bound through the global shortcut guard');
  assert.ok(source.includes('if (event.shiftKey) openCommandPalette();'), 'Shift plus app modifier opens the command palette');
  assert.ok(source.includes('else openFileQuickOpen();'), 'Plain app modifier plus P opens file quick-open');
  assert.ok(source.includes("if (event.key === ',')"), 'Preferences keeps best-effort comma shortcut in browser tabs');
  assert.ok(source.includes('selectSession(prefsItemId);'), 'Preferences shortcut opens the pane, while menu and palette remain fallbacks');
  assert.equal(source.includes('Ctrl/Cmd'), false, 'served UI strings do not show Ctrl/Cmd combined shortcuts');
  assert.ok(source.includes('showFileSaveConflictDialog'), 'editor saves route conflicts through the shared conflict dialog');
  assert.ok(source.includes('autoSaveFileEditor'), 'editor autosave is wired into the built client');
  assert.ok(source.includes('promptExternalChangeBeforeEditing'), 'editing a changed-on-disk buffer prompts before continuing');
  assert.ok(source.includes('editor.autosave_delay_seconds'), 'editor autosave delay is a persisted preference');
  assert.ok(source.includes('(commandPaletteIndex + 1) % commandPaletteItemsCache.length'), 'command palette arrow navigation wraps down');
  assert.ok(source.includes('item.splitRun'), 'command palette supports split-open actions');
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
  assert.equal(shortcutsMenu.type, 'submenu');
  assert.deepStrictEqual(canonical(shortcutsMenu.items.map(item => item.label)), ['Command palette', 'Save active editor', 'Toggle File Explorer', 'Open Preferences', 'Close menu or dialog', 'Session actions', 'Move or split tab']);
  assert.ok(shortcutsMenu.items.find(item => item.label === 'Command palette').detail.includes(api.appShortcutText('P', {shift: true})));
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
  assert.deepStrictEqual(Array.from(api.fuzzySubsequenceMatch('xy', 'helloXandYyy').indexes), [5, 9], 'fuzzy matcher exposes matched indexes for result highlighting');
  assert.ok(api.fuzzyHighlightHtml('xy', 'helloXandYyy').includes('<mark class="fuzzy-match">X</mark>'), 'palette results highlight matched characters');
  assert.equal(api.commandPaletteMatches({group: 'Tabs', label: 'helloXandYyy', detail: ''}, 'xy'), true, 'command palette uses fuzzy matching');
  assert.equal(api.commandPaletteMatches({group: 'Tabs', label: 'helloXandYyy', detail: ''}, 'xz'), false, 'command palette rejects non-matches');
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
  assert.equal(api.tabMenuItems()[0].type, 'search', 'Tabs menu starts with a search input');
  api.setTabsMenuSearchTextForTest('xy');
  assert.equal(api.tabSearchScore('1', 'xy') < 0, true, 'tab search uses fuzzy score and rejects non-matches');
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
  assert.deepStrictEqual(canonical(sessionActions.map(item => item.label)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Kill session"]);
  assert.equal(sessionActions.some(item => item.disabled), false);
  assert.equal(sessionActions.find(item => item.label === "Rename tmux session '1'").detail, '');
  const bodyChildCount = api.bodyChildren().length;
  api.showSessionContextMenu('1', 10, 10);
  const contextMenu = api.bodyChildren()[bodyChildCount];
  assert.deepStrictEqual(canonical(contextMenu.children.map(child => child.textContent)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Kill session"]);
  assert.equal(contextMenu.children.some(child => child.className === 'terminal-context-menu-separator'), false);
  const sessionViews = api.tmuxSessionViewCommands('1');
  assert.deepStrictEqual(canonical(sessionViews.map(item => item.label)), ['Transcript', 'AI summary', 'Event log', 'Pane details']);
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
  assert.ok(controlsHtml.includes('title="Transcript" aria-label="Transcript"'));
  assert.ok(controlsHtml.includes('title="AI summary" aria-label="AI summary"'));
  assert.ok(controlsHtml.includes('title="Event log" aria-label="Event log"'));
  assert.ok(controlsHtml.includes('title="Branch Info" aria-label="Branch Info"'));
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
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'top', targetRect: {width: 300}}), false);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: {width: 300}}), false);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'top', targetRect: {width: 520}}), true);
  assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: {width: 520}}), true);
  api.splitSessionAtSlot(editorItem, 'left', 'bottom');
  const editorSplit = api.serialize(api.currentSlots());
  assert.deepStrictEqual(canonical(Object.values(editorSplit.panes).filter(pane => pane.tabs.includes('__files__'))), [{tabs: ['__files__'], active: '__files__'}]);
  assert.deepStrictEqual(canonical(Object.values(editorSplit.panes).filter(pane => pane.tabs.includes(editorItem))), [{tabs: [editorItem], active: editorItem}]);
  assert.ok(JSON.stringify(editorSplit.tree).includes('"split":"column"'));

  const finderStrip = tabStrip([tabElement('__files__', 100, 120)]);
  api.bindPaneTabStrip(finderStrip, 'left');
  const event = dragEvent(125, '1');
  finderStrip.ondragover(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
  assert.equal(event.dataTransfer.dropEffect, 'none');
  assert.equal(finderStrip.classList.contains('tab-drop-preview'), false);
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
  assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['__yosup__', '__files__', '__prefs__', '__changes__', '3']);
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
  assert.ok(html.includes('>YO<'), 'tab includes YO marker');
  assert.equal(/session-yolo-marker[^"]*tab-symbol/.test(html), false, 'YO marker stays visible when metadata badges are hidden');
  assert.ok(html.includes('>4<'), 'tab includes session number');
  assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
  assert.ok(html.includes('>#9961<'), 'tab shows PR number from main HEAD subject');
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

  api.setAutoApproveStateForTest('4', {enabled: false, enabled_elsewhere: true, locked: true, lock_owner: {pid: 1234}, screen: {key: 'working'}});
  const externalHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
  assert.ok(/session-yolo-marker[^"]*locked/.test(externalHtml), 'YO owned by another server renders as yellow locked marker');
  assert.equal(/session-yolo-marker[^"]*active/.test(externalHtml), false, 'external YO is not shown as local active YO');
  assert.ok(externalHtml.includes('YOLO on elsewhere'), 'external YO marker title explains ownership is elsewhere');

  api.applyServerMetadataPulsesForTest('4', {main: 20000, pr: 20000});
  const metadataPulseHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
  assert.ok(metadataPulseHtml.includes('branch-indicator metadata-pulse'), 'MAIN badge pulses after metadata change');
  assert.ok(metadataPulseHtml.includes('pr-indicator pr-status-unknown metadata-pulse'), 'PR number badge pulses after metadata change');

  const mergedInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 12, merged: true, checks: {state: 'success'}},
    },
  };
  api.applyServerMetadataPulsesForTest('8', {status: 20000});
  const mergedPulseHtml = api.tmuxPaneTabHtml('8', mergedInfo, {key: 'idle'}, true);
  assert.ok(mergedPulseHtml.includes('pr-status-merged metadata-pulse'), 'MERGED badge pulses after status change');

  const ciInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 13, status_label: 'CI failing', checks: {state: 'failure'}},
    },
  };
  api.applyServerMetadataPulsesForTest('9', {ci: 20000});
  const ciPulseHtml = api.tmuxPaneTabHtml('9', ciInfo, {key: 'idle'}, true);
  assert.ok(ciPulseHtml.includes('pr-status-failing metadata-pulse'), 'CI badge is marked after CI change');
  const passingInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 14, status_label: 'open', checks: {state: 'passing'}},
    },
  };
  const passingHtml = api.tmuxPaneTabHtml('10', passingInfo, {key: 'idle'}, true);
  assert.ok(passingHtml.includes('pr-indicator'), 'passing PR still shows PR number');
  assert.equal(passingHtml.includes('>CI</span>'), false, 'passing CI does not add redundant CI badge');
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
      headline: "Sup! You've got 2 AI agents on editor fixes in yolomux.dev. Changes so far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
      lines: [
        "Sup! You've got 2 AI agents on editor fixes in yolomux.dev. Changes so far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
        'Yo - session alpha: Codex is active in yolomux.dev; 2 files changed (+8/-1); editor fixes',
      ],
    },
    sessions: {
      alpha: {local: "Sup! Codex session alpha is active. It has been working on editor fixes. Changes so far: 2 files changed (+8/-1)."},
    },
  });
  assert.ok(api.globalActivitySummaryHtml().includes("YO&#39;sup"), 'global activity summary uses the YO summary label');
  assert.ok(api.globalActivitySummaryHtml().includes('3 files changed (+9/-2)'), 'global activity summary renders file totals');
  assert.ok(api.globalActivitySummaryHtml().includes("Sup! You&#39;ve got 2 AI agents on editor fixes"), 'global activity summary renders a casual human sentence');
  assert.equal(api.sessionActivitySummary('alpha').local, "Sup! Codex session alpha is active. It has been working on editor fixes. Changes so far: 2 files changed (+8/-1).");
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

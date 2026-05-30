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
    this.rect = {width: 1200, height: 800, left: 0, top: 0, right: 1200, bottom: 800};
    this.style = new TestStyle();
    this.classList = new TestClassList();
  }

  addEventListener() {}
  removeEventListener() {}
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
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
  querySelector() { return null; }
  querySelectorAll() { return []; }
  remove() { this.removed = true; }
  scrollIntoView() {}
  getAttribute(name) { return this.attributes[name]; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  replaceChildren(...nodes) { this.children = nodes; }
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
  appMenuTree,
  createAppMenuCommand,
  backgroundTabItems,
  canPaneExpand,
  emptyPlaceholderPaneState,
  emptyLayoutSlots,
  fileEditorPaneTabHtml,
  fitAppMenuPopover,
  finderDirectoryForItem,
  finderTargetPathForItem,
  activeFinderDirectoryPath,
  activeFinderTargetPath,
  activeTmuxDirectoryPath,
  fileExplorerRootForOpen,
  fileExplorerRootModeValue,
  setFileExplorerRootMode,
  fileExplorerLabel,
  fileExplorerPanelCloseClass,
  fileEditorPanelCloseClass,
  fileIconFor,
  fileExplorerNeedsLeftDock,
  fileExplorerPaneTabHtml,
  firstEmptyPane,
  filePopoverRows,
  childPathParts,
  inactiveTabItems,
  itemIsBackgroundPaneTab,
  layoutFromParam,
  layoutParamValue,
  layoutSlotKeys,
  layoutWithFileExplorerDockedLeft,
  layoutWithReplacedItem,
  layoutWithoutItem,
  layoutWithItems,
  layoutTabsParamValue,
  layoutTreeKey,
  leafNode,
  menuTabCommand,
  activatePaneTab,
  currentSessionActionTarget,
  setFocusedPanelItem,
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
  clearPaneTabDropPreview,
  showSessionContextMenu,
  bodyChildren() { return document.body.children; },
  defaultLayoutSlots,
  dedentSelectionText,
  dropIntentAllowsSession,
  directoryEntriesSignature,
  editorWrapValue,
  editorViewModeFor,
  editorScrollTopForSourceLine,
  editorSourceLineForScroll,
  editorVisualHighlightHtml,
  editorVisualLineFragments,
  expandPaneFromLayout,
  infoBranchRows,
  fileContextMenuState,
  fileEntryChanged,
  pullRequestStatusLabel,
  renderTransportWarning,
  renderFileEditorPanel,
  rawFileUrl,
  rawFileDownloadUrl,
  registerFileEditorLayoutItem,
  requestFileEditorPanelFocus,
  focusFileEditorPanelIfReady,
  minimizePaneFromLayout,
  removePaneFromLayout,
  removeSessionFromLayout,
  sessionPopoverHtml,
  sessionState,
  slotForNewFileEditorTab,
  slotForNewTmuxSession,
  slotForTabActivation,
  simpleCodeSyntaxHtml,
  syntaxHighlightHtmlCanHideTextarea,
  smallLayoutSlotCandidate,
  splitPercentForNewItem,
  setInfoBranchSort,
  showPaneTabDropPreview,
  shouldPreserveSourceSlotForSplit,
  startSessionDrag,
  syncInitialLayoutUrl,
  tabMenuDetailText,
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
  pathRelativeToDirectory,
  currentSlots() { return layoutSlots; },
  setAutoApproveStateForTest(session, payload) {
    autoApproveStates.set(session, payload);
  },
  setTranscriptInfoForTest(session, info) {
    transcriptMeta.sessions = {...(transcriptMeta.sessions || {}), [session]: info};
  },
  applyServerMetadataPulsesForTest(session, pulses) {
    updateMetadataBadgePulses({sessions: {[session]: {metadata_badge_pulse_remaining_ms: pulses}}});
  },
  setInfoBranchSortForTest(key, dir = 'asc') {
    infoBranchSort = {key, dir};
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
  defaultLayoutForTest() {
    return globalThis.__layoutTestApi.serialize(defaultLayoutSlots());
  },
  customDragPreviewForTest() {
    return customDragPreview;
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
    dataTransfer: {
      dropEffect: '',
      effectAllowed: '',
      dragImage: null,
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
    stopImmediatePropagation() {
      this.propagationStopped = true;
    },
  };
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
}

{
  const api = loadYolomux('', ['1', '2']);
  const item = 'file:/home/keivenc/review.json';
  const paneTab = api.fileEditorPaneTabHtml(item);
  assert.ok(paneTab.includes('review.json'));
  assert.equal(paneTab.includes('agent-icon file'), false);

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
  const textarea = {scrollTop: 32, value: 'one\ntwo\nthree', clientWidth: 0};
  assert.equal(api.editorSourceLineForScroll(textarea, textarea.value), 3);
  assert.equal(api.editorScrollTopForSourceLine(textarea, textarea.value, 3), 32);

  assert.equal(api.syntaxHighlightHtmlCanHideTextarea('', '# TITLE'), false);
  assert.equal(api.syntaxHighlightHtmlCanHideTextarea('<span class="md-heading"># TITLE</span>', '# TITLE'), true);
  assert.equal(api.syntaxHighlightHtmlCanHideTextarea('', ''), true);
}

{
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const start = source.indexOf('function renderFileEditorPanel(');
  const end = source.indexOf('function loadFileEditorState(', start);
  assert.ok(start > 0 && end > start, 'could not locate renderFileEditorPanel body');
  assert.equal(source.slice(start, end).includes('.focus('), false, 'renderFileEditorPanel must not steal focus during refresh renders');
}

{
  const api = loadYolomux('', ['1']);
  const item = api.registerFileEditorLayoutItem('/home/test/AGENTS.md');
  let focusCount = 0;
  const textarea = {
    hidden: false,
    focus() {
      focusCount += 1;
    },
  };
  const panel = {
    querySelector(selector) {
      return selector === '.file-editor-textarea-panel' ? textarea : null;
    },
  };
  api.setFocusedPanelItem('1');
  api.requestFileEditorPanelFocus(item);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);
  assert.equal(focusCount, 0);

  api.setFocusedPanelItem(item);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), true);
  assert.equal(focusCount, 1);
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);

  api.requestFileEditorPanelFocus(item);
  textarea.hidden = true;
  assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);
  assert.equal(focusCount, 1);
}

{
  const api = loadYolomux('', ['1', '2']);
  const filesTab = api.fileExplorerPaneTabHtml();
  assert.equal(api.fileExplorerLabel(), 'File Explorer');
  assert.ok(filesTab.includes('File Explorer'));
  assert.equal(filesTab.includes('agent-icon file'), false);
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
  const appMenuIconButton = api.createAppMenuCommand({label: '+ Codex', iconHtml: '<span title="Codex">C</span>'});
  assert.equal(appMenuIconButton.innerHTML.includes('title='), false);
  const noMinimizedSlots = api.emptyLayoutSlots();
  noMinimizedSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
  noMinimizedSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
  noMinimizedSlots.slot1 = api.paneStateWithTabs(['1'], '1');
  api.setLayoutSlotsForTest(noMinimizedSlots);
  const tabMenu = api.appMenuTree().find(menu => menu.id === 'windows');
  const tabMenuLabels = tabMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(tabMenuLabels.includes('Active'), false);
  assert.equal(tabMenuLabels.includes('Inactive'), false);
  assert.equal(tabMenuLabels.includes('Minimized'), false);
  assert.equal(tabMenuLabels.some(label => label.startsWith('No ')), false);
  assert.equal(tabMenu.items.filter(item => item.type === 'separator').length, 1);
  const menus = api.appMenuTree();
  assert.equal(menus.map(menu => menu.label).join(','), 'File,View,tmux,Windows,Help');
  assert.equal(menus.some(menu => menu.id === 'yolo'), false);
  assert.equal(menus.some(menu => menu.id === 'settings'), false);
  const fileMenu = menus.find(menu => menu.id === 'file');
  const fileMenuLabels = fileMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(fileMenuLabels.includes('New tmux session'), false);
  assert.equal(fileMenuLabels.includes('Rename session'), false);
  assert.equal(fileMenuLabels.includes('Kill session'), false);
  assert.equal(fileMenuLabels.includes('Resume session'), false);
  assert.ok(fileMenuLabels.includes('Preferences'));
  assert.ok(fileMenuLabels.indexOf('Preferences') < fileMenuLabels.indexOf('Log out'));
  assert.deepStrictEqual(canonical(fileMenu.items.slice(-3).map(item => item.type === 'separator' ? '---' : item.label)), ['Preferences', '---', 'Log out']);
  const tmuxMenu = menus.find(menu => menu.id === 'tmux');
  const tmuxMenuLabels = tmuxMenu.items.map(item => item.label).filter(Boolean);
  assert.equal(tmuxMenu.items[0].label, 'YO off');
  assert.equal(tmuxMenu.items[0].keepOpen, true);
  assert.ok(tmuxMenuLabels.includes('New tmux session'));
  assert.ok(tmuxMenuLabels.includes('Transcript'));
  assert.ok(tmuxMenuLabels.includes('AI summary'));
  assert.ok(tmuxMenuLabels.includes('Event log'));
  assert.ok(tmuxMenuLabels.includes('Branch Info'));
  assert.ok(tmuxMenuLabels.includes("Rename tmux session '1'"));
  assert.ok(tmuxMenuLabels.includes('Kill session'));
  assert.equal(tmuxMenuLabels.includes("Enable YOLO for Tmux Session '1'"), false);
  assert.ok(tmuxMenuLabels.includes('Resume session'));
  assert.equal(tmuxMenu.badgeText, undefined);
  assert.ok(tmuxMenuLabels.includes('YOLO'));
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
  const yoloWindowsMenu = api.appMenuTree().find(menu => menu.id === 'windows');
  assert.equal(yoloWindowsMenu.badgeText, '1');
  assert.equal(yoloWindowsMenu.badgeTitle, '1 tmux session with YOLO enabled');
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
  const viewMenu = menus.find(menu => menu.id === 'view');
  assert.equal(viewMenu.items.find(item => item.label === 'Hide tab metadata').iconHtml.includes('app-menu-ui-icon-tab-meta active'), true);
  assert.equal(viewMenu.items.find(item => item.label === 'Alert').iconHtml.includes('app-menu-ui-icon-notify'), true);
  assert.equal(viewMenu.items.find(item => item.label === 'Refresh').iconHtml.includes('app-menu-ui-icon-refresh'), true);
  assert.equal(viewMenu.items.find(item => item.label === 'Refresh').detail, undefined);
  const helpMenu = menus.find(menu => menu.id === 'help');
  const helpMenuLabels = helpMenu.items.map(item => item.label).filter(Boolean);
  assert.ok(helpMenuLabels.includes('Keyboard shortcuts'));
  assert.ok(helpMenuLabels.includes('Open README'));
  const shortcutsMenu = helpMenu.items.find(item => item.label === 'Keyboard shortcuts');
  assert.equal(shortcutsMenu.type, 'submenu');
  assert.deepStrictEqual(canonical(shortcutsMenu.items.map(item => item.label)), ['Command palette', 'Save active editor', 'Close menu or dialog', 'Session actions', 'Move or split tab']);
  assert.equal(helpMenu.items.find(item => item.label === 'Open README').disabled, false);
  assert.equal(api.tabMenuItems().map(item => item.label).filter(Boolean).includes('Current Tab'), false);
  const namedSessionApi = loadYolomux('', ['1', 'dynamo2']);
  const tmuxOnlySlots = namedSessionApi.emptyLayoutSlots();
  tmuxOnlySlots[namedSessionApi.layoutTreeKey] = namedSessionApi.leafNode('left');
  tmuxOnlySlots.left = namedSessionApi.paneStateWithTabs(['1', 'dynamo2'], '1');
  namedSessionApi.setLayoutSlotsForTest(tmuxOnlySlots);
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
  assert.deepStrictEqual(canonical(sessionViews.map(item => item.label)), ['Transcript', 'AI summary', 'Event log', 'Branch Info']);
  assert.equal(api.fileIconFor('screenshot.png'), '🖼');
  assert.equal(api.fileIconFor('run.sh'), '🐚');
  assert.equal(api.fileIconFor('main.rs'), '🧩');
  assert.equal(api.fileIconFor('config.yaml'), '⚙');
  assert.equal(api.fileIconFor('README'), '📝');
  assert.equal(api.fileIconFor('Dockerfile'), '⚙');
  assert.equal(api.fileIconFor('archive.tar'), '🗜');
  assert.equal(api.fileIconFor('unknown.bin'), '📄');
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
  normalSplit.slot1 = api.paneStateWithTabs(['2'], '2');
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
  minimizedNormal.slot1 = api.paneStateWithTabs(['2'], '2');
  api.setLayoutSlotsForTest(minimizedNormal);
  api.minimizePaneFromLayout('2');
  assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
    tree: {slot: 'left'},
    panes: {left: {tabs: ['1', '2'], active: '1'}},
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
  const state = api.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
  assert.equal(state.copyRelativeDisabled, false);
  assert.equal(state.downloadDisabled, false);
  assert.equal(state.renameDisabled, false);
  assert.equal(state.deleteDisabled, false);

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
  assert.equal(api.agentErrorIsBlocking('codex transcript not found by cwd'), false);
  assert.equal(api.agentErrorIsBlocking('missing /home/test/.claude/sessions/123.json'), false);
  assert.equal(api.agentErrorIsBlocking('worker crashed'), true);
  assert.notEqual(api.sessionState('1', {agents: [{kind: 'codex', error: 'codex transcript not found by cwd'}]}).key, 'blocked');
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
  assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['__files__', '__prefs__', '3']);
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

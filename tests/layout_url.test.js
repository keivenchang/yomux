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
  removeAttribute() {}
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
}

function loadYolomux(search = '', sessions = ['1', '2', '3', '4', '5', '6'], protocol = 'http:', navigatorPlatform = 'Linux x86_64') {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const bootStart = source.indexOf("if (refreshMeta) {");
  assert.ok(bootStart > 0, 'could not find browser boot section');

  const bootstrap = JSON.stringify({
    sessions,
    availableAgents: [],
    accessRole: 'admin',
    homePath: '/home/test',
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
      removeEventListener() {},
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(`${source.slice(0, bootStart)}
globalThis.__layoutTestApi = {
  activeItemForSide,
  agentErrorIsBlocking,
  backgroundTabItems,
  emptyPlaceholderPaneState,
  emptyLayoutSlots,
  fileEditorButtonHtml,
  fileEditorPaneTabHtml,
  fileExplorerLabel,
  fileExplorerPaneTabHtml,
  firstEmptyPane,
  filePopoverRows,
  inactiveTabItems,
  itemIsBackgroundPaneTab,
  layoutFromParam,
  layoutParamValue,
  layoutSlotKeys,
  layoutWithoutItem,
  layoutWithItems,
  layoutTabsParamValue,
  layoutTreeKey,
  leafNode,
  normalizeLayoutSlots,
  paneIsPlaceholder,
  bindPaneTabStrip,
  clearPaneTabDropPreview,
  createTabListMenu,
  defaultLayoutSlots,
  dedentSelectionText,
  dropIntentAllowsSession,
  editorWrapValue,
  infoBranchRows,
  pullRequestStatusLabel,
  renderTransportWarning,
  rawFileDownloadUrl,
  registerFileEditorLayoutItem,
  removeSessionFromLayout,
  sessionPopoverHtml,
  sessionState,
  slotForNewFileEditorTab,
  slotForNewTmuxSession,
  slotForTabActivation,
  sessionButtonHtml,
  simpleCodeSyntaxHtml,
  smallLayoutSlotCandidate,
  splitPercentForNewItem,
  setInfoBranchSort,
  showPaneTabDropPreview,
  shouldShowTabListMenu,
  shouldPreserveSourceSlotForSplit,
  startSessionDrag,
  syncInitialLayoutUrl,
  tabListDetailText,
  tabListEntryBodyHtml,
  terminalWrappedLineLinks,
  splitNode,
  splitSessionAtSlot,
  updateActiveSessionParam,
  paneTabDropIndex,
  paneTabDropPlacement,
  tmuxPaneTabHtml,
  paneTabs,
  paneStateWithTabs,
  windowStepVisibility,
  markdownSyntaxHtml,
  moveSessionToSlot,
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
  const api = loadYolomux('', ['1', '2']);
  const item = 'file:/home/keivenc/review.json';
  const paneTab = api.fileEditorPaneTabHtml(item);
  const topTab = api.fileEditorButtonHtml(item);
  assert.ok(paneTab.includes('review.json'));
  assert.ok(topTab.includes('review.json'));
  assert.equal(paneTab.includes('agent-icon file'), false);
  assert.equal(topTab.includes('agent-icon file'), false);

  assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-heading-1'));
  assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-bold'));
  assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-comment'));
  assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-variable'));
  assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-attr'));
  assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-constant'));
}

{
  const api = loadYolomux('', ['1']);
  const filesTab = api.fileExplorerPaneTabHtml();
  assert.equal(api.fileExplorerLabel(), 'File Explorer');
  assert.ok(filesTab.includes('File Explorer'));
  assert.equal(filesTab.includes('agent-icon file'), false);

  const macApi = loadYolomux('', ['1'], 'http:', 'MacIntel');
  assert.equal(macApi.fileExplorerLabel(), 'Finder');

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
  assert.deepStrictEqual(canonical(split.panes.slot1), {tabs: [], active: null, placeholder: true});
  assert.deepStrictEqual(canonical(Object.values(split.panes).filter(pane => pane.tabs.includes('__info__'))), [{tabs: ['__info__'], active: '__info__'}]);
  const infoSlot = Object.entries(split.panes).find(([, pane]) => pane.tabs.includes('__info__'))[0];
  assert.equal(api.shouldPreserveSourceSlotForSplit(infoSlot, 'slot1'), false);
  assert.equal(api.shouldPreserveSourceSlotForSplit('slot1', 'left'), true);
  api.moveSessionToSlot('__info__', 'slot1', infoSlot);
  const movedBack = api.serialize(api.currentSlots());
  assert.deepStrictEqual(canonical(movedBack.panes), {
    left: {tabs: ['__files__'], active: '__files__'},
    slot1: {tabs: ['__info__'], active: '__info__'},
  });

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
  assert.equal(api.editorWrapValue(false), 'off');
  assert.equal(api.editorWrapValue(true), 'soft');
  assert.equal(api.rawFileDownloadUrl('/repo/app/a b.txt'), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&download=1');
}

{
  const api = loadYolomux('', ['1']);
  assert.equal(api.agentErrorIsBlocking('codex transcript not found by cwd'), false);
  assert.equal(api.agentErrorIsBlocking('worker crashed'), true);
  assert.notEqual(api.sessionState('1', {agents: [{kind: 'codex', error: 'codex transcript not found by cwd'}]}).key, 'blocked');
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
  assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['3']);
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
  assert.ok(html.includes('>4<'), 'tab includes session number');
  assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
  assert.ok(html.includes('>#9961<'), 'tab shows PR number from main HEAD subject');
  assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, merged: true}), '');
  assert.equal(html.includes('MERGED'), false, 'main fallback does not show merged status');
  assert.equal(html.includes('(#9961)'), false, 'tab title strips duplicated PR suffix');

  const blockedHtml = api.tmuxPaneTabHtml('4', info, {key: 'blocked', short: 'BLK', label: 'Blocked', reason: 'blocked command'}, false);
  assert.ok(blockedHtml.includes('--attention-animation-delay:'), 'red attention badges carry a synchronized animation delay');

  const genericWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'working'}, true);
  assert.equal(genericWorkingHtml.includes('session-yolo-marker active working'), false, 'generic working state does not pulse YO marker');

  api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}});
  const workingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
  assert.ok(workingHtml.includes('session-yolo-marker active working'), 'visible screen working pulses active YO marker');
  const workingTopHtml = api.sessionButtonHtml('4', info, {key: 'idle'}, true);
  assert.ok(workingTopHtml.includes('session-yolo-marker active working'), 'visible screen working pulses top YO marker');

  api.setAutoApproveStateForTest('4', {enabled: false, enabled_elsewhere: true, locked: true, lock_owner: {pid: 1234}, screen: {key: 'working'}});
  const externalHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
  assert.ok(externalHtml.includes('session-yolo-marker locked'), 'YO owned by another server renders as yellow locked marker');
  assert.equal(externalHtml.includes('session-yolo-marker active'), false, 'external YO is not shown as local active YO');
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

  const detail = api.tabListDetailText('4', info);
  assert.ok(detail.includes('GH-2132__reasoning-dangling-end-marker'), 'tab list detail includes fuller branch name');
  assert.ok(detail.includes('~/project/project3'), 'tab list detail includes compact path');
  assert.ok(detail.includes('#9981 CI failing'), 'tab list detail includes PR and status');
  assert.ok(detail.includes('GH-2132'), 'tab list detail includes Linear identifier');
  const linearIndex = detail.indexOf('GH-2132', detail.indexOf('~/project/project3'));
  assert.ok(linearIndex < detail.indexOf('#9981 CI failing'), 'tab list detail lists Linear before PR');

  const html = api.tabListEntryBodyHtml('4');
  assert.ok(html.includes('session-yolo-marker inactive'), 'tab list entry shows inactive YO indicator');
  assert.ok(html.includes('data-auto-session="4"'), 'tab list YO indicator is clickable');
  assert.ok(html.includes('fix(parser): parse dangling reasoning end markers'), 'tab list entry includes long PR title');
  assert.ok(html.includes('dangling-end-marker'), 'tab list entry includes branch detail inline');
  assert.ok(html.includes('~/project/project3'), 'tab list entry includes compact path inline');
  assert.ok(html.includes('GH-2132'), 'tab list entry includes Linear detail inline');
  assert.equal(html.includes('tab-list-entry-detail'), false, 'tab list entry is a single visible line');

  const popover = api.sessionPopoverHtml('4', info, 'codex', true);
  assert.ok(popover.indexOf('popover-label">Linear') < popover.indexOf('popover-label">PR'), 'tab popover lists Linear before PR');
}

{
  const api = loadYolomux();
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.leafNode('left');
  slots.left = api.paneStateWithTabs(['4', '5'], '4');
  api.setLayoutSlotsForTest(slots);

  assert.equal(api.shouldShowTabListMenu([]), false);
  assert.equal(api.shouldShowTabListMenu(['4']), false);
  assert.equal(api.shouldShowTabListMenu(['4', '5']), true);

  const paneMenu = api.createTabListMenu(['4', '5'], {kind: 'pane', side: 'left'});
  assert.equal(paneMenu.children[0].textContent, '');
  assert.equal(paneMenu.children[1].innerHTML.includes('Pane tabs'), false);
  assert.equal(paneMenu.children[1].children.length, 2);
  assert.ok(paneMenu.children[1].children[0].className.includes('active'), 'active pane tab is marked in show-all menu');
  assert.equal(paneMenu.children[1].children[0].draggable, true);

  const trayMenu = api.createTabListMenu(['1', '2'], {kind: 'tray'});
  assert.equal(trayMenu.children[0].textContent, '');
  assert.equal(trayMenu.children[1].innerHTML.includes('Inactive tabs'), true);
  assert.equal(trayMenu.children[1].children.length, 2);
  assert.equal(trayMenu.children[1].children[0].draggable, true);
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

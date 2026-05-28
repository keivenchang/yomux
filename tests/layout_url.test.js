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

function loadYolomux(search = '', sessions = ['1', '2', '3', '4', '5', '6']) {
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
      querySelectorAll: () => [],
      removeEventListener() {},
    },
    fetch() { return Promise.reject(new Error('fetch disabled in layout URL tests')); },
    history: {
      replaceState(_state, _title, url) {
        context.__lastUrl = url;
      },
    },
    location: {search, pathname: '/', hash: ''},
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
  emptyLayoutSlots,
  layoutFromParam,
  layoutParamValue,
  layoutSlotKeys,
  layoutTabsParamValue,
  layoutTreeKey,
  leafNode,
  normalizeLayoutSlots,
  bindWindowTabStrip,
  clearWindowTabDropPreview,
  createTabListMenu,
  dedentSelectionText,
  pullRequestStatusLabel,
  sessionButtonHtml,
  showWindowTabDropPreview,
  startSessionDrag,
  tabListDetailText,
  tabListEntryBodyHtml,
  terminalWrappedLineLinks,
  splitNode,
  updateActiveSessionParam,
  windowTabDropIndex,
  windowTabDropPlacement,
  windowSessionTabHtml,
  windowStack,
  windowStateWithTabs,
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
  serialize(slots) {
    return {
      tree: slots[layoutTreeKey],
      windows: Object.fromEntries(layoutSlotKeys(slots).map(slot => [
        slot,
        {tabs: windowStack(slot, slots), active: activeItemForSide(slot, slots)},
      ])),
    };
  },
  setLayoutSlotsForTest(nextSlots) {
    layoutSlots = normalizeLayoutSlots(nextSlots);
    activeSessions = sessionsFromLayout();
    updateActiveSessionParam();
    return globalThis.__lastUrl;
  },
  setGridPreviewNodesForTest(nodes) {
    grid.querySelectorAll = () => nodes;
  },
  customDragPreviewForTest() {
    return customDragPreview;
  },
};`, context);
  return context.__layoutTestApi;
}

function tabElement(session, left, width) {
  const tab = new TestElement(session);
  tab.dataset.windowSessionTab = session;
  tab.rect = {left, right: left + width, top: 0, bottom: 27, width, height: 27};
  return tab;
}

function tabStrip(tabs) {
  const strip = new TestElement('strip');
  strip.children = tabs;
  strip.rect = {left: 100, right: 406, top: 0, bottom: 28, width: 306, height: 28};
  strip.querySelectorAll = selector => {
    assert.equal(selector, '.window-session-tab');
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
  slots.left = api.windowStateWithTabs(['5', '6'], '6');
  slots.slot1 = api.windowStateWithTabs(['1'], '1');
  slots.slot2 = api.windowStateWithTabs(['3'], '3');
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
    windows: {
      left: {tabs: ['3', '2'], active: '3'},
    },
  });
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
  const html = api.windowSessionTabHtml('4', info, null, true);
  assert.ok(html.includes('>YO<'), 'tab includes YO marker');
  assert.ok(html.includes('>4<'), 'tab includes session number');
  assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
  assert.ok(html.includes('>#9961<'), 'tab shows PR number from main HEAD subject');
  assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, merged: true}), '');
  assert.equal(html.includes('MERGED'), false, 'main fallback does not show merged status');
  assert.equal(html.includes('(#9961)'), false, 'tab title strips duplicated PR suffix');

  const blockedHtml = api.windowSessionTabHtml('4', info, {key: 'blocked', short: 'BLK', label: 'Blocked', reason: 'blocked command'}, false);
  assert.ok(blockedHtml.includes('--attention-animation-delay:'), 'red attention badges carry a synchronized animation delay');

  const genericWorkingHtml = api.windowSessionTabHtml('4', info, {key: 'working'}, true);
  assert.equal(genericWorkingHtml.includes('session-yolo-marker active working'), false, 'generic working state does not pulse YO marker');

  api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}});
  const workingHtml = api.windowSessionTabHtml('4', info, {key: 'idle'}, true);
  assert.ok(workingHtml.includes('session-yolo-marker active working'), 'visible screen working pulses active YO marker');
  const workingTopHtml = api.sessionButtonHtml('4', info, {key: 'idle'}, true);
  assert.ok(workingTopHtml.includes('session-yolo-marker active working'), 'visible screen working pulses top YO marker');

  api.setAutoApproveStateForTest('4', {enabled: false, enabled_elsewhere: true, locked: true, lock_owner: {pid: 1234}, screen: {key: 'working'}});
  const externalHtml = api.windowSessionTabHtml('4', info, {key: 'idle'}, false);
  assert.ok(externalHtml.includes('session-yolo-marker locked'), 'YO owned by another server renders as yellow locked marker');
  assert.equal(externalHtml.includes('session-yolo-marker active'), false, 'external YO is not shown as local active YO');
  assert.ok(externalHtml.includes('YOLO on elsewhere'), 'external YO marker title explains ownership is elsewhere');

  api.applyServerMetadataPulsesForTest('4', {main: 20000, pr: 20000});
  const metadataPulseHtml = api.windowSessionTabHtml('4', info, {key: 'idle'}, true);
  assert.ok(metadataPulseHtml.includes('branch-indicator metadata-pulse'), 'MAIN badge pulses after metadata change');
  assert.ok(metadataPulseHtml.includes('pr-indicator pr-status-unknown metadata-pulse'), 'PR number badge pulses after metadata change');

  const mergedInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 12, merged: true, checks: {state: 'success'}},
    },
  };
  api.applyServerMetadataPulsesForTest('8', {status: 20000});
  const mergedPulseHtml = api.windowSessionTabHtml('8', mergedInfo, {key: 'idle'}, true);
  assert.ok(mergedPulseHtml.includes('pr-status-merged metadata-pulse'), 'MERGED badge pulses after status change');

  const ciInfo = {
    project: {
      git: {branch: 'feature'},
      pull_request: {number: 13, status_label: 'CI failing', checks: {state: 'failure'}},
    },
  };
  api.applyServerMetadataPulsesForTest('9', {ci: 20000});
  const ciPulseHtml = api.windowSessionTabHtml('9', ciInfo, {key: 'idle'}, true);
  assert.ok(ciPulseHtml.includes('pr-status-failing metadata-pulse'), 'CI badge is marked after CI change');
}

{
  const api = loadYolomux();
  const info = {
    selected_pane: {current_path: '/home/test/project/project3'},
    project: {
      git: {branch: 'keivenc/DIS-2132__reasoning-dangling-end-marker', root: '/home/test/project/project3'},
      pull_request: {
        number: 9981,
        title: 'fix(parser): parse dangling reasoning end markers',
        status_label: 'CI failing',
        checks: {state: 'failure'},
      },
      linear: [{identifier: 'DIS-2132', title: 'DeepSeek V4 validation'}],
    },
  };
  api.setTranscriptInfoForTest('4', info);

  const detail = api.tabListDetailText('4', info);
  assert.ok(detail.includes('DIS-2132__reasoning-dangling-end-marker'), 'tab list detail includes fuller branch name');
  assert.ok(detail.includes('~/project/project3'), 'tab list detail includes compact path');
  assert.ok(detail.includes('#9981 CI failing'), 'tab list detail includes PR and status');
  assert.ok(detail.includes('DIS-2132'), 'tab list detail includes Linear identifier');

  const html = api.tabListEntryBodyHtml('4');
  assert.ok(html.includes('session-yolo-marker inactive'), 'tab list entry shows inactive YO indicator');
  assert.ok(html.includes('fix(parser): parse dangling reasoning end markers'), 'tab list entry includes long PR title');
}

{
  const api = loadYolomux();
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.leafNode('left');
  slots.left = api.windowStateWithTabs(['4', '5'], '4');
  api.setLayoutSlotsForTest(slots);

  const windowMenu = api.createTabListMenu(['4', '5'], {kind: 'window', side: 'left'});
  assert.equal(windowMenu.children[0].textContent, '');
  assert.equal(windowMenu.children[1].innerHTML.includes('Window tabs'), true);
  assert.equal(windowMenu.children[1].children.length, 2);
  assert.ok(windowMenu.children[1].children[0].className.includes('active'), 'active window tab is marked in show-all menu');
  assert.equal(windowMenu.children[1].children[0].draggable, true);

  const trayMenu = api.createTabListMenu(['1', '2'], {kind: 'tray'});
  assert.equal(trayMenu.children[0].textContent, '');
  assert.equal(trayMenu.children[1].innerHTML.includes('Inactive tabs'), true);
  assert.equal(trayMenu.children[1].children.length, 2);
  assert.equal(trayMenu.children[1].children[0].draggable, true);
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

  assert.deepStrictEqual(canonical(api.windowTabDropPlacement(strip, {clientX: 110}, '9')), {index: 0, x: 2});
  assert.deepStrictEqual(canonical(api.windowTabDropPlacement(strip, {clientX: 225}, '9')), {index: 1, x: 103});
  assert.deepStrictEqual(canonical(api.windowTabDropPlacement(strip, {clientX: 390}, '9')), {index: 3, x: 304});
  assert.deepStrictEqual(canonical(api.windowTabDropPlacement(strip, {clientX: 225}, '2')), {index: 1, x: 206});
  assert.deepStrictEqual(canonical(api.windowTabDropPlacement(tabStrip([]), {clientX: 180}, '9')), {index: 0, x: 80});
  assert.equal(api.windowTabDropIndex(strip, {clientX: 225}, '9'), 1);
}

{
  const api = loadYolomux();
  const strip = tabStrip([
    tabElement('1', 100, 100),
    tabElement('2', 203, 100),
    tabElement('3', 306, 100),
  ]);

  api.showWindowTabDropPreview(strip, {clientX: 225}, '9');

  assert.ok(strip.classList.contains('drag-over'), 'tab strip shows drag target outline');
  assert.ok(strip.classList.contains('tab-drop-preview'), 'tab strip shows insertion preview');
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '103px');

  api.clearWindowTabDropPreview(strip);

  assert.equal(strip.classList.contains('drag-over'), false);
  assert.equal(strip.classList.contains('tab-drop-preview'), false);
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '');
}

{
  const api = loadYolomux();
  const source = tabElement('4', 100, 140);
  source.rect = {left: 100, right: 240, top: 20, bottom: 47, width: 140, height: 27};
  source.classList.add('window-session-tab');
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
  api.setGridPreviewNodesForTest([stalePanePreview]);
  api.bindWindowTabStrip(strip, 'left');

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
  assert.ok(strip.classList.contains('tab-drop-preview'), 'tab strip owns the active preview');
  assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '103px');
}

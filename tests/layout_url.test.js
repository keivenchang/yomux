const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class TestElement {
  constructor(id = '') {
    this.id = id;
    this.children = [];
    this.dataset = {};
    this.innerHTML = '';
    this.textContent = '';
    this.style = {setProperty() {}};
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    };
  }

  addEventListener() {}
  removeEventListener() {}
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  contains() { return false; }
  getBoundingClientRect() { return {width: 1200, height: 800, left: 0, top: 0}; }
  insertAdjacentHTML() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
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
      body: element('body'),
      createElement: tag => new TestElement(tag),
      getElementById: element,
      querySelectorAll: () => [],
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
  pullRequestStatusLabel,
  sessionButtonHtml,
  splitNode,
  updateActiveSessionParam,
  windowSessionTabHtml,
  windowStack,
  windowStateWithTabs,
  currentSlots() { return layoutSlots; },
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
};`, context);
  return context.__layoutTestApi;
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
        github_repo: {url: 'https://github.com/ai-dynamo/dynamo'},
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
}

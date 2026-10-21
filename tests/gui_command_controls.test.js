const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element extends EventTarget {
  constructor(tag = 'div') {
    super();
    this.localName = tag;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.classList = {add() {}, toggle() {}};
    this.listeners = new Map();
  }
  get textContent() { return this._text || ''; }
  set textContent(value) {
    this._text = String(value);
    this._html = String(value);
    this.firstChild = value ? {textContent: String(value)} : null;
  }
  get innerHTML() { return this._html || ''; }
  set innerHTML(value) {
    this._html = String(value);
    this._text = String(value).replace(/<[^>]*>/g, '');
    this.firstChild = value ? {textContent: this._text} : null;
  }
  addEventListener(type, listener, options) {
    super.addEventListener(type, listener, options);
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener, options) {
    super.removeEventListener(type, listener, options);
    this.listeners.get(type)?.delete(listener);
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  appendChild(child) { this.children.push(child); child.parent = this; }
  remove() { this.parent?.children.splice(this.parent.children.indexOf(this), 1); }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  focus() { this.focused = true; }
}

function harness() {
  const body = new Element();
  const document = new Element();
  const footer = new Element();
  footer.id = 'status';
  document.body = body;
  document.createElement = tag => new Element(tag);
  document.getElementById = id => id === 'status' ? footer : body.children.find(child => child.id === id) || null;
  const window = new Element();
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    document, window, URL, URLSearchParams, Symbol, console,
    esc: value => String(value).replaceAll('"', '&quot;'),
    domDataAttributeName: key => 'data-' + key.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase()),
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    apiFetch(url, options, internalOptions) {
      return new Promise((resolve, reject) => requests.push({url, options, internalOptions, resolve, reject}));
    },
  });
  vm.runInContext(`async function apiFetchJson(url, options, internalOptions) {
    const response = await apiFetch(url, options, internalOptions);
    if (!response.ok) throw new Error('request rejected');
    return response.json();
  }`, context);
  const core = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  vm.runInContext(core.slice(core.indexOf('function overlayFocusableElements('), core.indexOf('function createLatestResource(')), context);
  vm.runInContext(fs.readFileSync('static_src/js/yomux/76_panel_dom_actions.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('static_src/js/yomux/15_command_registry.js', 'utf8'), context);
  return {context, requests, timers, body, document, window, footer,
    run: code => vm.runInContext(code, context)};
}

async function run() {
  let h = harness();
  const button = h.run(`createToolbarButton({className: 'example', pressed: false, checked: true,
    ariaLabel: 'Example', title: 'Example title', hidden: true, attributes: {'aria-expanded': 'false'},
    dataset: {owner: 'toolbar'}, action: 'example'})`);
  assert.equal(button.className, 'btn-base example');
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.equal(button.getAttribute('aria-checked'), 'true');
  assert.equal(button.getAttribute('aria-expanded'), 'false');
  assert.equal(button.dataset.action, 'example');
  assert.equal(button.hidden, true);
  const serialized = h.run(`toolbarButtonHtml({className: 'example', pressed: false, checked: true,
    ariaLabel: 'Example', disabled: true, dataset: {owner: 'toolbar'}, action: 'example'})`);
  assert.match(serialized, /class="btn-base example"/);
  assert.match(serialized, /aria-pressed="false"/);
  assert.match(serialized, /aria-checked="true"/);
  assert.match(serialized, /disabled/);
  assert.match(serialized, /data-action="example"/);
  const row = h.run(`createActionRow({actions: [{className: 'row'}]})`);
  assert.equal(row.children[0].className, 'btn-base row');
  const segmented = h.run(`createSegmentedControl({items: [{className: 'segment'}]})`);
  assert.equal(segmented.children[0].className, 'btn-base segment');

  const controller = h.run('createDismissableOverlayController({trapFocus: true})');
  const overlay = new Element();
  const trigger = new Element('button');
  for (let index = 0; index < 3; index += 1) {
    controller.open(overlay, {trigger});
    assert.equal(overlay.listeners.get('pointerdown').size, 1);
    assert.equal(h.document.listeners.get('pointerdown').size, 1);
    controller.close();
    assert.equal(overlay.listeners.get('pointerdown').size, 0);
    assert.equal(h.document.listeners.get('pointerdown').size, 0);
    assert.equal(h.window.listeners.get('blur').size, 0);
  }
  assert.equal(trigger.focused, true);
  controller.open(overlay);
  const replacement = new Element();
  controller.open(replacement);
  assert.equal(overlay.listeners.get('pointerdown').size, 0);
  assert.equal(replacement.listeners.get('pointerdown').size, 1);
  h.window.dispatchEvent(new Event('blur'));
  assert.equal(controller.isOpen(), false);

  let insideEvents = 0;
  controller.open(overlay, {trigger, closeOnBlur: false});
  const inside = new Event('pointerdown');
  inside.stopPropagation = () => { insideEvents += 1; };
  overlay.dispatchEvent(inside);
  assert.equal(insideEvents, 1, 'one open installs exactly one effective pointer handler');
  h.window.dispatchEvent(new Event('blur'));
  assert.equal(controller.isOpen(), true, 'closeOnBlur false remains open');
  const escape = new Event('keydown', {cancelable: true});
  escape.key = 'Escape';
  h.document.dispatchEvent(escape);
  assert.equal(controller.isOpen(), false);
  assert.equal(escape.defaultPrevented, true);

  h = harness();
  const first = h.run(`dispatchCommand(COMMAND_ROUTES['finder-delete'], {payload: {path: '/first'}})`);
  const duplicate = h.run(`dispatchCommand(COMMAND_ROUTES['finder-delete'], {payload: {path: '/first'}})`);
  const other = h.run(`dispatchCommand(COMMAND_ROUTES['finder-delete'], {payload: {path: '/other'}})`);
  assert.equal(first, duplicate, 'duplicate clicks share their exact operation');
  assert.notEqual(first, other, 'different file targets must execute');
  assert.equal(h.requests.length, 2);
  h.requests.forEach(request => request.resolve({ok: true, json: async () => ({saved: true})}));
  await Promise.all([first, duplicate, other]);

  h = harness();
  const settingsA = h.run(`apiFetchJson('/api/settings', {method: 'POST', body: '{"theme":"dark"}'}, {quietStatuses: [409]})`);
  const settingsB = h.run(`apiFetchJson('/api/settings', {method: 'POST', body: '{"font_size":14}'})`);
  assert.equal(h.requests.length, 2, 'distinct settings patches cannot be discarded');
  assert.equal(h.requests[0].internalOptions.quietStatuses[0], 409);
  assert.equal(h.body.children.length, 2, 'direct registered mutations use the shared pending owner');
  [...h.timers.values()].forEach(callback => callback());
  assert.equal(h.body.children[0].dataset.commandStatusPhase, 'overdue');
  h.requests.forEach(request => request.resolve({ok: true, json: async () => ({saved: true})}));
  await Promise.all([settingsA, settingsB]);
  assert.equal(h.body.children.length, 0);

  h = harness();
  const urlA = h.run(`apiFetchJson('/api/kill-session?session=one', {method: 'POST'})`);
  const urlB = h.run(`apiFetchJson('/api/kill-session?session=two', {method: 'POST'})`);
  assert.equal(h.requests.length, 2, 'query-string targets must execute independently');
  h.requests[0].resolve({ok: true, json: async () => ({killed: true})});
  h.requests[1].reject(new Error('delayed failure'));
  await urlA;
  await assert.rejects(urlB, /delayed failure/);
  assert.equal(h.body.children.length, 1);
  assert.equal(h.body.children[0].dataset.commandStatusPhase, 'error');
  assert.equal(h.body.children[0].textContent, 'delayed failure');

  h = harness();
  const source = new Element('button');
  h.context.source = source;
  const failed = h.run(`dispatchCommand({...COMMAND_ROUTES['finder-delete'],
    optimistic() { source.changed = true; return 'before'; },
    rollback(undo) { source.changed = undo; }
  }, {payload: {path: '/fail'}}, source)`);
  assert.equal(source.disabled, true);
  assert.equal(source.getAttribute('aria-busy'), 'true');
  h.requests[0].reject(new Error('rollback failure'));
  await assert.rejects(failed, /rollback failure/);
  assert.equal(source.changed, 'before');
  assert.equal(source.disabled, false);
  assert.equal(source.getAttribute('aria-busy'), null);
  h = harness();
  h.context.source = new Element('button');
  const operationA = h.run(`dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {theme: 'dark'}}, source)`);
  const operationB = h.run(`dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {font_size: 18}}, source)`);
  h.requests[0].resolve({ok: true, json: async () => ({saved: true})});
  await operationA;
  assert.equal(h.context.source.disabled, true, 'a shared source remains pending until every distinct command settles');
  h.requests[1].resolve({ok: true, json: async () => ({saved: true})});
  await operationB;
  assert.equal(h.context.source.disabled, false);
  assert.equal(h.context.source.getAttribute('aria-describedby'), null);

  h = harness();
  const opaqueA = h.run(`apiFetchJson('/api/upload', {method: 'POST', body: {file: 'first'}})`);
  const opaqueB = h.run(`apiFetchJson('/api/upload', {method: 'POST', body: {file: 'second'}})`);
  assert.equal(h.requests.length, 2, 'opaque bodies cannot safely share an operation identity');
  h.requests.forEach(request => request.resolve({ok: true, json: async () => ({uploaded: true})}));
  await Promise.all([opaqueA, opaqueB]);

  h = harness();
  const raw = h.run(`apiFetch('/api/stats-retry', {method: 'POST', commandRouteId: 'stats-manual-retry'})`);
  assert.equal(h.body.children.length, 1, 'manual retry chooses the pending contract at a shared route');
  assert.equal(h.requests[0].options.commandRouteId, undefined, 'command metadata never reaches network transport');
  const response = {ok: false, status: 503};
  h.requests[0].resolve(response);
  assert.equal(await raw, response, 'raw callers retain their response and error interpretation');
  assert.equal(h.body.children[0].dataset.commandStatusPhase, 'error');
  await assert.rejects(h.run(`apiFetch('/api/settings', {method: 'POST', commandRouteId: 'kill-session'})`), /unregistered_mutating_command/);

  h = harness();
  h.context.source = new Element('button');
  const applying = h.run(`dispatchCommand({...COMMAND_ROUTES['settings-save'],
    applyResult: async () => { throw new Error('apply failure'); },
    rollback: () => { source.rolledBack = true; }
  }, {payload: {theme: 'dark'}}, source)`);
  h.requests[0].resolve({ok: true, json: async () => ({saved: true})});
  await assert.rejects(applying, /apply failure/);
  assert.equal(h.context.source.rolledBack, true, 'async result errors run shared rollback before settlement');
  assert.equal(h.context.source.disabled, false);
  h = harness();
  const toggleOn = h.run(`dispatchCommand({...COMMAND_ROUTES['auto-approve-toggle'], operationKey: () => 'one'},
    {url: '/api/auto-approve?session=one&enabled=1'})`);
  const toggleAgain = h.run(`dispatchCommand({...COMMAND_ROUTES['auto-approve-toggle'], operationKey: () => 'one'},
    {url: '/api/auto-approve?session=one&enabled=0'})`);
  const otherSession = h.run(`dispatchCommand({...COMMAND_ROUTES['auto-approve-toggle'], operationKey: () => 'two'},
    {url: '/api/auto-approve?session=two&enabled=1'})`);
  assert.equal(toggleOn, toggleAgain, 'repeated optimistic toggles join the same target operation');
  assert.notEqual(toggleOn, otherSession, 'different toggle targets remain independent');
  assert.equal(h.requests.length, 2);
  h.requests.forEach(request => request.resolve({ok: true, json: async () => ({enabled: true})}));
  await Promise.all([toggleOn, toggleAgain, otherSession]);

  h = harness();
  h.footer.innerHTML = '<span class="ok">codex ready</span>';
  h.footer.className = 'original-footer';
  h.footer.setAttribute('title', 'Existing status');
  const yolo = h.run(`dispatchCommand(COMMAND_ROUTES['auto-approve-toggle'],
    {url: '/api/auto-approve?session=one&enabled=1'})`);
  h.requests[0].resolve({ok: true, json: async () => ({enabled: true})});
  await yolo;
  assert.equal(h.footer.innerHTML, '<span class="ok">codex ready</span>', 'successful YO restores the existing ready footer');
  assert.equal(h.footer.className, 'original-footer');
  assert.equal(h.footer.getAttribute('title'), 'Existing status');

  for (const finishLastStartedFirst of [true, false]) {
    h = harness();
    h.footer.textContent = 'ready';
    const settings = h.run(`dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {theme: 'dark'}})`);
    const rename = h.run(`dispatchCommand({...COMMAND_ROUTES['rename-session'], pendingLabel: 'Renaming...'},
      {url: '/api/rename-session?session=one&new_name=two'})`);
    const finish = index => h.requests[index].resolve({ok: true, json: async () => ({saved: true})});
    if (finishLastStartedFirst) {
      finish(1); await rename;
      assert.equal(h.footer.textContent, 'Working...', 'remaining settings command owns the footer');
      finish(0); await settings;
    } else {
      finish(0); await settings;
      assert.equal(h.footer.textContent, 'Renaming...', 'remaining rename command owns the footer');
      finish(1); await rename;
    }
    assert.equal(h.footer.textContent, 'ready', 'overlapping successes restore the original footer in either order');
  }

  h = harness();
  h.footer.textContent = 'ready';
  const superseded = h.run(`dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {theme: 'light'}})`);
  h.footer.innerHTML = '<span class="ok">newer external result</span>';
  [...h.timers.values()].forEach(callback => callback());
  assert.equal(h.footer.innerHTML, '<span class="ok">newer external result</span>', 'overdue timers cannot overwrite newer external results');
  h.requests[0].resolve({ok: true, json: async () => ({saved: true})});
  await superseded;
  assert.equal(h.footer.innerHTML, '<span class="ok">newer external result</span>');

  h = harness();
  h.footer.textContent = 'ready';
  const success = h.run(`dispatchCommand(COMMAND_ROUTES['settings-save'], {payload: {theme: 'dark'}})`);
  const failure = h.run(`dispatchCommand(COMMAND_ROUTES['rename-session'], {url: '/api/rename-session?session=one&new_name=two'})`);
  h.requests[1].reject(new Error('newer failure'));
  await assert.rejects(failure, /newer failure/);
  [...h.timers.values()].forEach(callback => callback());
  h.requests[0].resolve({ok: true, json: async () => ({saved: true})});
  await success;
  assert.equal(h.footer.textContent, 'newer failure', 'older successful commands cannot erase a newer command error');

  h = harness();
  h.footer.textContent = 'ready';
  h.context.apiJsonResponse = async response => { const payload = await response.json(); const error = new Error(payload.error); error.payload = payload; throw error; };
  const rejected = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  let originalRead = false;
  const rejection = {ok:false,status:400,clone() {return {json:async () => ({error:'list exceeds 128 entries'})};},json:async () => {originalRead = true;return {error:'list exceeds 128 entries'};}};
  h.requests[0].resolve(rejection);
  assert.equal(await rejected, rejection);
  assert.equal(originalRead, false, 'raw error decoding preserves the original body');
  assert.equal(h.footer.textContent, 'Finder watch registration: list exceeds 128 entries');
  const oldRetry = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  const newerFailure = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  h.requests[2].resolve(rejection);
  await newerFailure;
  h.requests[1].resolve({ok:true});
  await oldRetry;
  assert.match(h.footer.textContent, /128 entries/, 'older success cannot clear a newer failure');
  const recovery = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  h.requests[3].resolve({ok:true});
  await recovery;
  assert.equal(h.footer.textContent, 'ready', 'newer matching success clears the rejected registration');

  h = harness();
  h.footer.textContent = 'ready';
  const firstDelete = h.run(`dispatchCommand(COMMAND_ROUTES['finder-delete'], {payload:{path:'/first'}})`);
  h.requests[0].reject(new Error('cannot delete first'));
  await assert.rejects(firstDelete);
  const otherDelete = h.run(`dispatchCommand(COMMAND_ROUTES['finder-delete'], {payload:{path:'/other'}})`);
  h.requests[1].resolve({ok:true,json:async()=>({})});
  await otherDelete;
  assert.equal(h.footer.textContent, 'cannot delete first');
  h = harness();
  h.footer.textContent = 'ready';
  const staleFailure = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  const currentSuccess = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  h.requests[1].resolve({ok:true});
  await currentSuccess;
  h.requests[0].resolve({ok:false,status:400});
  await staleFailure;
  assert.equal(h.footer.textContent, 'ready', 'stale failure cannot replace newer successful recovery');

  h = harness();
  h.footer.textContent = 'ready';
  const earlier = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'same'})`);
  const later = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'same'})`);
  h.requests[1].resolve({ok:true});
  await later;
  h.requests[0].resolve({ok:true});
  await earlier;
  assert.equal(h.footer.textContent, 'ready', 'stale success still retires pending feedback');

  h = harness();
  h.footer.textContent = 'ready';
  for (const target of ['a','b']) {
    const failedDelete = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'${target}'})`);
    h.requests.at(-1).reject(new Error('error ' + target));
    await assert.rejects(failedDelete);
  }
  for (const target of ['b','a']) {
    const retryDelete = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'${target}'})`);
    h.requests.at(-1).resolve({ok:true});
    await retryDelete;
  }
  assert.equal(h.footer.textContent, 'ready', 'restored error keeps its retry ownership');

  for (const recoveries of [['a','b'], ['b','c','a']]) {
    h = harness();
    h.footer.textContent = 'ready';
    for (const target of recoveries.length === 2 ? ['a','b'] : ['a','b','c']) {
      const failedDelete = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'${target}'})`);
      h.requests.at(-1).reject(new Error('error ' + target));
      await assert.rejects(failedDelete);
    }
    for (const target of recoveries) {
      const retryDelete = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'${target}'})`);
      h.requests.at(-1).resolve({ok:true});
      await retryDelete;
    }
    assert.equal(h.footer.textContent, 'ready', 'hidden recovered errors cannot return');
  }

  h = harness();
  h.footer.textContent = 'ready';
  const watchFailure = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  h.requests[0].reject(new Error('watch rejected'));
  await assert.rejects(watchFailure);
  const foregroundDelete = h.run(`apiFetch('/api/fs/delete', {method:'POST',body:'a'})`);
  const watchRecovery = h.run(`apiFetch('/api/watch/roots', {method:'POST'})`);
  h.requests[2].resolve({ok:true});
  await watchRecovery;
  assert.equal(h.footer.textContent, 'Working...');
  h.requests[1].resolve({ok:true});
  await foregroundDelete;
  assert.equal(h.footer.textContent, 'ready');

  console.log('gui command controls suite: passed');
}

run().catch(error => { console.error(error); process.exitCode = 1; });

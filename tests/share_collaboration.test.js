// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
'use strict';
const assert = require('assert');
const fs = require('fs');
const {posix} = require('path');
const vm = require('vm');
const {webcrypto} = require('crypto');

function controllerFixture({active = true} = {}) {
  const calls = [];
  const context = {window: {}, console, URL, crypto: webcrypto, location: {origin: 'https://fixture.test'},
    setTimeout() { return 1; }, clearTimeout() {}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/codemirror.js', 'utf8'), context);
  const cm = context.window.YOmuxCodeMirror;
  let document = cm.EditorState.create({doc: 'hello\n'});
  const operations = [];
  context.fetch = async (url, options) => {
    const query = new URL(String(url)).searchParams;
    const clientID = 'host:' + query.get('client');
    if (query.get('initial') === '1') return {ok: true, json: async () => active
      ? {active: true, text: document.doc.toString(), version: operations.length, clientID, session: 'session-A'}
      : {active: false}};
    const body = JSON.parse(options.body);
    calls.push(body);
    if (body.version === operations.length) {
      for (const operation of body.updates) {
        document = document.update({changes: cm.ChangeSet.fromJSON(operation.changes)}).state;
        operations.push({...operation, clientID});
      }
    }
    return {ok: true, json: async () => ({active: true, version: operations.length, updates: operations.slice(body.version),
      clientID, session: 'session-A', presence: [], paused: '', saved: true})};
  };
  vm.runInContext(fs.readFileSync('cmd/yomux-go/pages/share_client.gohtml', 'utf8'), context);
  function view() {
    const editor = {state: cm.EditorState.create({doc: ''})};
    editor.dispatch = transaction => { editor.state = editor.state.update(transaction).state; };
    return editor;
  }
  return {context, cm, calls, view, api: context.window.YOmuxShare, text: () => document.doc.toString()};
}

function workspaceFixture(status, reason = 'stale resource') {
  const fileState = new Map(), timers = [], requests = [], storage = new Map();
  let layouts = 0;
  const nodes = new Map();
  const grant = {id: 'grant', guest: 'Guest', expires: '2026-11-22T08:30:00Z', appearance: {}, resources: []};
  const context = {console, URL, URLSearchParams, Map, Set, Promise, fileState, panelNodes: new Map(),
    location: {origin: 'https://fixture.test', pathname: '/share/grant', hash: '#secret', search: ''},
    history: {replaceState() {}}, navigator: {},
    storageGet: key => storage.get(key) ?? null,
    storageSet: (key, value) => storage.set(key, value),
    sessionStorageGet: () => null, sessionStorageRemove() {},
    safeJsonParse: (value, fallback) => value === null ? fallback : JSON.parse(value),
    document: {body: {classList: {add() {}}}, addEventListener() {}, getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {hidden: false, textContent: ''});
      return nodes.get(id);
    }, createElement() {
      return {textContent: '', setAttribute() {}};
    }, querySelector(selector) {
      if (selector === '.topbar') return {hidden: true};
      assert.equal(selector, '.topbar .actions');
      return {prepend(node) { nodes.set('countdown', node); }};
    }},
    tabberItemId: 'tabber', tabberNormalSelections: [], tabberActivityState: {},
    fileExtensionOf: path => path.slice(path.lastIndexOf('.')).toLowerCase(), fileEditorItemFor: path => 'file:' + path,
    fileItemPath: item => item.slice('file:'.length), editorViewModeFor: () => 'edit', fileEditorSourceElement: () => null,
    dirnameOf: posix.dirname, joinAndNormalize: posix.join,
    setFileState(path, state) { fileState.set(path, state); return state; },
    fileEditorPanelsForPath: () => [], syncFileLayoutItems() {}, renderOpenFilePath() {},
    fileErrorState: error => ({kind: 'error', error}),
    defaultNormalTabberLayoutSlots: () => ({}), applyLayoutSlots() { layouts += 1; }, refreshTabberPanels() {},
    storeTabberNormalSelections(items) { context.tabberNormalSelections = items; },
    setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {},
    setInterval() { return 1; }, clearInterval() {}, shareTimeLeft: () => '5m 0s',
    installNativeAppViewportOwner() {}, syncNativeAppViewport() {}, installPreviewScrollOwnershipCapture() {},
    installTouchContextMenuOwner() {}, installGlobalThemeMediaListener() {},
    applySettingsPayload() {}, setTabberMode() {}, applyLocale: async () => {},
    resolveLocalePref: value => value, syncAppViewportBreakpointClasses() {}, applyFileExplorerStaticLabels() {},
    paintInitialAppShell() {}, t: value => value, showLayoutStatus(message) { context.lastStatus = message; },
    window: {addEventListener() {}, YOmuxShare: {randomID: () => 'snapshot-client', post: async () => ({name: 'Guest', owner: 'Admin', share: grant}),
      request: async url => {
        const request = new URL(String(url), 'https://fixture.test'); requests.push(request);
        if (request.pathname.endsWith('/state')) return {name: 'Guest', share: grant};
        if (request.pathname.endsWith('/chat')) return {messages: []};
        if (request.pathname.endsWith('/removed')) throw Object.assign(new Error(reason), {status});
        return {text: request.pathname.split('/').pop() + ' saved text'};
      }}},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static_src/js/yomux/96_share_workspace.js', 'utf8'), context);
  const workspace = vm.runInContext('shareWorkspace', context);
  return {context, workspace, fileState, timers, requests, grant, nodes, layouts: () => layouts};
}

(async () => {
  const fixture = controllerFixture();
  const a = fixture.view(), b = fixture.view();
  const errors = [];
  const first = await fixture.api.connect(a, '/api/share/document?path=file', {onError: error => errors.push(error)});
  const second = await fixture.api.connect(b, '/api/share/document?path=file', {onError: error => errors.push(error)});
  await first.flush(); await second.flush();
  assert(fixture.calls.every(call => call.updates.length === 0), 'admitting initial text must not send it back as a local edit');
  assert.equal(fixture.text(), 'hello\n');
  a.dispatch({changes: {from: 0, insert: 'A'}});
  b.dispatch({changes: {from: 0, insert: 'B'}});
  await first.flush();
  await second.flush();
  await first.flush();
  assert.equal(a.state.doc.toString(), b.state.doc.toString(), 'stale source edits rebase over the intervening accepted update');
  assert.equal(fixture.text(), a.state.doc.toString());
  assert.equal(fixture.text().match(/A/g).length, 1);
  assert.equal(fixture.text().match(/B/g).length, 1);
  assert.equal(errors.length, 0, String(errors));
  first.stop(); second.stop();
  const ended = controllerFixture({active: false});
  const editor = ended.view();
  assert.equal(await ended.api.connect(editor, '/api/share/document?path=file'), null);
  assert.equal(editor.state.readOnly, false, 'an ended initial request releases the temporary admission lock');
  for (const rejectInitial of [false, true]) {
    for (const destroyed of [false, true]) {
      const stale = controllerFixture();
      const staleEditor = stale.view();
      const request = stale.context.fetch;
      let release, reject;
      const held = new Promise((resolve, fail) => { release = resolve; reject = fail; });
      stale.context.fetch = async (url, options) => {
        if (new URL(String(url)).searchParams.get('initial') === '1') await held;
        return request(url, options);
      };
      let current = true, alive = true, dispatches = 0;
      const dispatch = staleEditor.dispatch;
      staleEditor.dispatch = transaction => { dispatches += 1; dispatch(transaction); };
      const pending = stale.api.connect(staleEditor, '/share/grant/resource', {
        current: () => current, alive: () => alive,
      });
      assert.equal(staleEditor.state.readOnly, true, 'initial admission excludes local edits');
      current = false;
      alive = !destroyed;
      const beforeRelease = dispatches;
      if (rejectInitial) {
        reject(new Error('permission_changed'));
        await assert.rejects(pending, /permission_changed/);
      } else {
        release();
        assert.equal(await pending, null, 'an obsolete epoch never installs collaboration');
      }
      assert.equal(staleEditor.state.doc.toString(), '', 'a stale initial response never replaces the retained document');
      if (destroyed) {
        assert.equal(dispatches, beforeRelease, 'retired views receive no cleanup transaction');
      } else {
        assert.equal(staleEditor.state.readOnly, false, 'epoch retirement releases admission on the retained live view');
        stale.context.fetch = request;
        current = true;
        const reopened = await stale.api.connect(staleEditor, '/share/grant/resource', {current: () => current, alive: () => alive});
        await reopened.flush();
        assert.equal(staleEditor.state.readOnly, false, 'a later write grant has no orphaned admission facet');
        reopened.stop();
      }
    }
  }
  const unavailable = workspaceFixture(409, 'file_unavailable');
  unavailable.grant.resources = [
    {id: 'removed', label: 'gone.md', kind: 'file', writable: false, permission_revision: 1},
    {id: 'healthy', label: 'healthy.md', kind: 'file', writable: false, permission_revision: 1},
  ];
  await unavailable.context.bootShareWorkspace();
  assert.equal(unavailable.workspace.resources.size, 2);
  assert.equal(unavailable.fileState.get('/shared/removed/gone.md').kind, 'error');
  assert.equal(unavailable.fileState.get('/shared/healthy/healthy.md').content, 'healthy saved text');
  assert(unavailable.requests.some(request => request.pathname.endsWith('/chat')), 'a missing file does not block guest chat');
  unavailable.workspace.resources.get('healthy').assets = {'100%#sample.png': 'image'};
  unavailable.workspace.resources.set('image', {id: 'image', kind: 'file', label: '100%#sample.png'});
  assert.equal(unavailable.context.shareWorkspaceRawURL('/shared/healthy/100%#sample.png'), '/share/grant/image');
  assert.equal(unavailable.context.shareWorkspaceRawURL('/shared/healthy/ungranted.png'), 'about:blank');
  for (const status of [404, 409]) {
    const staged = workspaceFixture(status);
    const retained = {id: 'retained', label: 'retained.md', kind: 'file', writable: false, permission_revision: 3};
    const added = {id: 'added', label: 'added.md', kind: 'file', writable: false, permission_revision: 4};
    const removed = {id: 'removed', label: 'removed.md', kind: 'file', writable: false, permission_revision: 5};
    staged.workspace.base = '/share/grant/'; staged.workspace.active = true;
    staged.workspace.resources.set(retained.id, retained);
    const originalMap = staged.workspace.resources;
    const originalState = {kind: 'text', content: 'unchanged draft', dirty: true};
    const retainedPath = staged.context.shareWorkspacePath(retained);
    staged.fileState.set(retainedPath, originalState);
    await assert.rejects(staged.context.syncShareWorkspaceResources([retained, added, removed]), error => error.status === status);
    assert.strictEqual(staged.workspace.resources, originalMap, 'failed staging leaves the published resource map intact');
    assert.equal(staged.workspace.resources.size, 1);
    assert.equal(staged.fileState.size, 1, 'failed staging never seeds partial empty editors');
    assert.strictEqual(staged.fileState.get(retainedPath), originalState);
    assert.equal(originalState.content, 'unchanged draft', 'even an earlier successful snapshot remains uncommitted');
    assert.equal(staged.layouts(), 0);
    await staged.context.syncShareWorkspaceResources([retained, added]);
    assert.equal(staged.workspace.resources.size, 2);
    assert.strictEqual(staged.workspace.resources.get(retained.id), retained, 'retained input handlers keep their resource identity');
    assert.equal(staged.fileState.get(staged.context.shareWorkspacePath(added)).content, 'added saved text');
    assert.equal(staged.layouts(), 1, 'the next authoritative membership commits the initial layout');
    await staged.context.syncShareWorkspaceResources([retained, added]);
    assert.equal(staged.layouts(), 1, 'ordinary snapshots preserve the committed layout');
    assert(staged.requests.every(request => request.searchParams.get('permission') === ({retained: '3', added: '4', removed: '5'})[request.pathname.split('/').pop()]),
      'each resource snapshot carries its permission epoch');
    const writable = {...added, writable: true};
    await staged.context.shareWorkspaceSnapshot(writable);
    const request = staged.requests[staged.requests.length - 1];
    assert.equal(request.searchParams.get('permission'), '4');
    assert.equal(request.searchParams.get('initial'), '1');
    assert.equal(request.searchParams.get('client'), 'snapshot-client');

    const startup = workspaceFixture(status);
    startup.grant.resources = [added, removed];
    await startup.context.bootShareWorkspace();
    assert.equal(startup.workspace.active, true);
    assert.equal(startup.context.document.title, 'YOmux - Admin - Guest');
    assert.equal(startup.workspace.resources.size, 0, 'stale startup data does not partially publish');
    assert.equal(startup.fileState.size, 0);
    assert.equal(startup.timers.length, 1, 'failed initial hydration leaves the same polling owner scheduled');
    startup.grant.resources = [];
    await startup.timers.shift()();
    assert.equal(startup.layouts(), 1, 'an authoritative empty membership still commits the initial Tabber layout');
    assert.equal(startup.workspace.layoutCommitted, true);
    assert.equal(startup.nodes.get('grid'), undefined, 'stale membership never replaces the workspace with a fatal error');
  }
  const host=controllerFixture();
  const state={content:'hello\n',original:'hello\n',kind:'text',_shareActive:true};
  const panels=[{_cmPath:'file',_cmView:host.view(),querySelector(){return null;}},{_cmPath:'file',_cmView:host.view(),querySelector(){return null;}}];
  Object.assign(host.context,{fileState:new Map([['file',state]]),fileEditorPanelState:()=>state,fileEditorPanelsForPath:()=>panels,
    renderLinkedFilePreviewPanels(){},renderFileEditorPreviewSurface(){},updateFileEditorPanelChrome(){},setFileEditorPanelStatus(){}});
  vm.runInContext(fs.readFileSync('static_src/js/yomux/95_share.js','utf8'),host.context);
  Object.assign(host.context, {
    layoutItems: ['image', 'z', 'a', 'terminal'],
    fileItemPath: item => item === 'terminal' ? null : '/' + item,
    fileEditorStateForItem: path => ({kind: path === '/image' ? 'image' : 'text'}),
    fileEditorStateWritable: () => true,
    isTmuxSession: item => item === 'terminal',
    terminals: new Map([['terminal', {container: {dataset: {terminalPaneTarget: '%2'}}}]]),
    terminalContextMenuPaneTargetForContainer: container => container?.dataset.terminalPaneTarget || '',
    terminalContextMenuPaneTargetForSession: () => '%1',
    transcriptMetadataState: {payload: {sessions: {terminal: {selected_pane: {pane_id: '%1', target: 'terminal:0.0'}}}}},
  });
  const grant = {resources: [{id: 'kept', kind: 'file', label: '/closed', writable: true, can_write: true}]};
  const candidates = host.context.sharePickerCandidates(grant, {kept: '/closed'});
  assert.deepEqual(Array.from(candidates, candidate => candidate.label), ['/a', '/closed', '/z', '/image', 'terminal %2'],
    'one picker groups editable files, other files, and terminals alphabetically');
  assert.equal(candidates[1].resource.id, 'kept', 'a closed retained tab keeps its server identity');
  assert.equal(candidates[1].selected, true);
  assert(candidates.filter(candidate => candidate.resource.id !== 'kept').every(candidate => !candidate.selected),
    'ordinary open tabs do not reselect themselves when editing a share');
  assert.equal(candidates[0].resource.writable, false, 'new tabs default to read access');
  await host.context.shareAttachHostPanel(panels[0],'file');await host.context.shareAttachHostPanel(panels[1],'file');
  await panels[0]._shareConnection.flush();await panels[1]._shareConnection.flush();
  panels[1]._cmView.dispatch({changes:{from:0,insert:'pending '}});host.context.shareHostLocalChanged(panels[1],'file');
  await panels[0]._shareConnection.flush();
  assert.equal(state.dirty,true,'a clean panel heartbeat must retain another host panel pending edits');
  assert.equal(state.content,'pending hello\n');
  const editorSource=fs.readFileSync('static_src/js/yomux/92_codemirror_editor.js','utf8');
  const commitStart=editorSource.indexOf('function commitCodeMirrorSource('),commitEnd=editorSource.indexOf('\nfunction scheduleCodeMirrorSource',commitStart);
  vm.runInContext(editorSource.slice(commitStart,commitEnd),host.context);
  host.context.commitCodeMirrorSource(panels[0],'file');
  assert.equal(state.content,'pending hello\n','blurring a clean panel must retain another panel pending document');
  const saveStart=editorSource.indexOf('async function performFileEditorSave('),saveEnd=editorSource.indexOf('\n}\n',saveStart)+3;
  Object.assign(host.context,{readOnlyMode:false,fileEditorPanelItem:()=>null,fileEditorItemFor:()=>null,fileEditorStateForItem:()=>state});
  vm.runInContext(editorSource.slice(saveStart,saveEnd),host.context);
  assert.equal(await host.context.performFileEditorSave('file',panels[0]),true);
  assert.equal(panels[1]._shareConnection.pending(),0,'Save from a clean panel must finish every panel pending edit');
  await panels[0]._shareConnection.flush();
  assert.equal(state.dirty,false,'the file becomes clean after every panel pending update is accepted');
  panels[0]._shareConnection.stop();panels[1]._shareConnection.stop();
  console.log('layout suite: 13 passed, 0 failed');
})().catch(error => { console.error(error); process.exitCode = 1; });

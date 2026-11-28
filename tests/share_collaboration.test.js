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
  const timers = new Map();
  let nextTimer = 0;
  let time = 10000;
  const context = {window: {}, console, URL, crypto: webcrypto, location: {origin: 'https://fixture.test'},
    Date: class extends Date { static now() { return time; } },
    performance: {now: () => time},
    setTimeout(callback, delay) {
      timers.set(++nextTimer, {callback, delay});
      return nextTimer;
    },
    clearTimeout(id) { timers.delete(id); }};
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
    const editor = {state: cm.EditorState.create({doc: ''}), dom: new EventTarget()};
    editor.dispatch = transaction => { editor.state = editor.state.update(transaction).state; };
    return editor;
  }
  return {context, cm, calls, timers, view, advance: ms => { time += ms; },
    api: context.window.YOmuxShare, text: () => document.doc.toString()};
}

async function verifySyncWaitsForInput(inPreview = false) {
  const fixture = controllerFixture();
  const view = fixture.view();
  const input = inPreview ? new EventTarget() : view.dom;
  const connection = await fixture.api.connect(view, '/api/share/document?path=file', {
    previewInputSurface: () => inPreview ? input : null,
  });
  await connection.flush();
  const [id, timer] = [...fixture.timers][0];
  fixture.timers.delete(id);
  const baseline = fixture.calls.length;
  input.dispatchEvent(new Event('compositionstart'));
  const tick = timer.callback();
  fixture.advance(1500);
  await Promise.resolve();
  assert.equal(fixture.calls.length, baseline, 'an ongoing composition holds the automatic request');
  input.dispatchEvent(new Event('compositionend'));
  view.dispatch({changes: {from: 0, insert: 'first '}});
  assert.equal(fixture.calls.length, baseline, 'composition end still waits for input quiet');

  const fetch = fixture.context.fetch;
  let releaseBody, bodyOpened;
  const decoding = new Promise(resolve => { bodyOpened = resolve; });
  fixture.context.fetch = async (...args) => {
    const response = await fetch(...args);
    return {...response, json: async () => {
      await new Promise(resolve => { releaseBody = resolve; bodyOpened(); });
      return response.json();
    }};
  };
  fixture.advance(350);
  [...fixture.timers.values()][0].callback();
  await decoding;
  input.dispatchEvent(new Event('keydown'));
  view.dispatch({changes: {from: 0, insert: 'second '}});
  fixture.context.fetch = fetch;
  releaseBody();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(connection.pending(), 2, 'response body arriving during input leaves local history alone');
  assert.equal(view.state.doc.toString(), 'second first hello\n');
  assert.equal(fixture.calls.length, baseline + 1);
  assert(await connection.flush(), 'explicit Save releases the quiet wait and sends remaining edits');
  await tick;
  assert.equal(fixture.calls.length, baseline + 2);
  assert.equal(fixture.text(), view.state.doc.toString());
  connection.stop();
  assert.equal(fixture.timers.size, 0);
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
  await verifySyncWaitsForInput();
  await verifySyncWaitsForInput(true);
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
  const batched = controllerFixture();
  const batchedView = batched.view();
  const connection = await batched.api.connect(batchedView, '/api/share/document?path=file');
  await connection.flush();
  assert.equal(batched.timers.size, 1, 'explicit sync keeps one scheduled poll');
  const [[timerID, timer]] = batched.timers;
  assert(timer.delay >= 1000 && timer.delay <= 2000, 'background sync runs on a one- to two-second cadence');
  const requestCount = batched.calls.length;
  for (const char of 'batch ') batchedView.dispatch({changes: {from: 0, insert: char}});
  assert.equal(batched.calls.length, requestCount, 'local transactions do not send individual requests');
  batched.timers.delete(timerID);
  await timer.callback();
  assert.equal(batched.calls.length, requestCount + 1);
  assert.equal(batched.calls.at(-1).updates.length, 6, 'one request carries the pending burst');
  assert.equal(batched.text(), batchedView.state.doc.toString());
  connection.stop();
  assert.equal(batched.timers.size, 0, 'disconnect cancels the scheduled poll');
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
  const previewRenders = [];
  const panels=[{_cmPath:'file',_cmView:host.view(),querySelector(){return null;}},{_cmPath:'file',_cmView:host.view(),querySelector(){return null;}}];
  Object.assign(host.context,{Blob,formatFileSize:size=>String(size),t:key=>key,tPlural:key=>key,
    fileState:new Map([['file',state]]),fileEditorPanelState:()=>state,fileEditorPanelsForPath:()=>panels,
    fileEditorPanelPreviewPane: panel => panel.preview?.hidden === false ? panel.preview : null,
    previewKindForPath: () => 'text',
    scheduleSplitEditorCaret() {},
    traceHtmlViewEdit() {},
    scheduleCodeMirrorSplitPreview: () => false,
    fileEditorPanelMode: () => 'split',
    renderLinkedFilePreviewPanels(){},renderFileEditorPreviewSurface(_panel, pane) { previewRenders.push(pane); },
    updateFileEditorPanelChrome(){},setFileEditorPanelStatus(){},captureCodeMirrorPanelViewState(){}});
  const statusSource = fs.readFileSync('static_src/js/yomux/45_file_explorer_actions.js', 'utf8');
  vm.runInContext(statusSource.slice(statusSource.indexOf('function openFileStatus('),
    statusSource.indexOf('function fileEditorAutosaveDelayMs(')), host.context);
  const editorSource = fs.readFileSync('static_src/js/yomux/92_codemirror_editor.js', 'utf8');
  vm.runInContext(editorSource.slice(editorSource.indexOf('function codeMirrorTextReplacement('),
    editorSource.indexOf('function syncCodeMirrorDocument(')), host.context);
  vm.runInContext(fs.readFileSync('static_src/js/yomux/95_share.js','utf8'),host.context);
  Object.assign(host.context, {
    layoutItems: ['image', 'z', 'a', 'deleted', 'terminal'],
    fileItemPath: item => item === 'terminal' ? null : '/' + item,
    fileEditorStateForItem: path => ({kind: path === '/image' ? 'image' : 'text', externalMissing: path === '/deleted'}),
    fileEditorStateWritable: () => true,
    isTmuxSession: item => item === 'terminal',
    terminals: new Map([['terminal', {container: {dataset: {terminalPaneTarget: '%2'}}}]]),
    terminalContextMenuPaneTargetForContainer: container => container?.dataset.terminalPaneTarget || '',
    terminalContextMenuPaneTargetForSession: () => '%1',
    transcriptMetadataState: {payload: {sessions: {terminal: {selected_pane: {pane_id: '%1', target: 'terminal:0.0'}}}}},
  });
  const grant = {resources: [{id: 'kept', kind: 'file', label: 'closed', writable: true, can_write: true}]};
  const candidates = host.context.sharePickerCandidates(grant, {kept: '/closed'});
  assert.deepEqual(Array.from(candidates, candidate => candidate.label), ['/a', '/closed', '/z', '/image', 'terminal %2'],
    'one picker groups editable files, other files, and terminals alphabetically');
  assert.equal(candidates[1].resource.id, 'kept', 'a closed retained tab keeps its server identity');
  assert.equal(candidates[1].selected, true);
  assert(candidates.filter(candidate => candidate.resource.id !== 'kept').every(candidate => !candidate.selected),
    'ordinary open tabs do not reselect themselves when editing a share');
  assert.equal(candidates[0].resource.writable, false, 'new tabs default to read access');
  assert(!host.context.sharePickerCandidates().some(candidate => candidate.resource.path === '/deleted'),
    'retained editor text for a missing file does not make it eligible for a new share');
  {
    // A refused join (a draft that no longer matches the saved text) leaves the editor working and unshared.
    const refused = {_cmPath: 'file', _cmView: host.view(), querySelector() { return null; }};
    const share = host.context.window.YOmuxShare, connect = share.connect, setStatus = host.context.setFileEditorPanelStatus;
    const statuses = [];
    share.connect = async () => { throw new Error('Save or reload the host draft before collaborating'); };
    host.context.setFileEditorPanelStatus = (panel, message, tone) => statuses.push([panel === refused, message, tone]);
    try { await host.context.shareAttachHostPanel(refused, 'file'); }
    finally { share.connect = connect; host.context.setFileEditorPanelStatus = setStatus; }
    assert.deepEqual(statuses, [[true, 'Save or reload the host draft before collaborating', 'warn']], 'a refused join shows its reason instead of throwing');
    assert.equal(refused._shareConnecting, false);
    assert.equal(refused._shareConnection, undefined);
  }
  await host.context.shareAttachHostPanel(panels[0],'file');await host.context.shareAttachHostPanel(panels[1],'file');
  await panels[0]._shareConnection.flush();await panels[1]._shareConnection.flush();
  panels[1]._cmView.dispatch({changes:{from:0,insert:'pending '}});host.context.shareHostLocalChanged(panels[1],'file');
  await panels[0]._shareConnection.flush();
  assert.equal(state.dirty,true,'a clean panel heartbeat must retain another host panel pending edits');
  assert.equal(state.content,'pending hello\n');
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
  assert.equal(host.context.openFileStatus(state).message, 'editor.saved', 'a heartbeat preserves the confirmed save acknowledgment');
  panels[1].preview = {hidden: true};
  panels[1]._cmView.dispatch({changes:{from:0,insert:'hidden '}});
  const previousContent = state.content;
  host.context.markSharedEditorPending(panels[1],state);
  assert.equal(state.content, previousContent, 'typing marks the draft without serializing the editor');
  await panels[1]._shareConnection.flush();
  assert.equal(previewRenders.length, 0, 'local input and server acknowledgments skip hidden previews');
  panels[1].preview.hidden = false;
  panels[1]._cmView.dispatch({changes:{from:0,insert:'split '}});
  host.context.shareHostLocalChanged(panels[1],'file');
  assert.equal(previewRenders.length, 0, 'local input queues the visible preview');
  const localPresentation = panels[1]._sharePresentationTimer;
  await panels[1]._shareConnection.flush();
  assert.equal(panels[1]._sharePresentationTimer, localPresentation, 'sync acknowledgment keeps the original local preview deadline');
  const firstPresentation = panels[1]._sharePresentationTimer;
  panels[1]._cmView.dispatch({changes:{from:0,insert:'newer '}});
  host.context.scheduleSharedEditorPresentation(panels[1],'file',state,true);
  assert.equal(panels[1]._sharePresentationTimer, firstPresentation, 'another edit keeps the queued preview deadline');
  assert.equal(previewRenders.length, 0, 'a sync acknowledgment does not render during typing');
  const presentation = panels[1]._sharePresentationTimer;
  const timerForPreview = host.timers.get(presentation);
  assert.equal(timerForPreview.delay, 250, 'the first render waits 250 ms');
  host.timers.delete(presentation);
  timerForPreview.callback();
  assert.deepEqual(previewRenders, [panels[1].preview], 'the queued render refreshes the visible preview once');
  assert.equal(panels[1]._sharePresentationTimer, null, 'the completed render leaves no repeating work');
  host.advance(100);
  host.context.scheduleSharedEditorPresentation(panels[1],'file',state,true);
  assert.equal(host.timers.get(panels[1]._sharePresentationTimer).delay, 150, 'the next render waits out the remaining cooldown');
  host.context.disconnectShareEditor(panels[1]);
  panels[0]._shareConnection.stop();
  const publications = [];
  host.context.previewKindForPath = path => path.endsWith('.tex') ? 'tex' : 'markdown';
  host.context.fileState.set('/added.tex', {kind: 'text', content: 'private draft', original: 'saved source'});
  host.context.fetch = async (url, opts) => {
    publications.push([url, JSON.parse(opts.body)]);
    return {ok: true, headers: new Headers({'Content-Type': 'application/pdf'})};
  };
  await host.context.publishNewSharedTeX({status: {}}, [
    {id: 'retained', kind: 'file', path: '/retained.tex'},
    {kind: 'file', path: '/notes.md'},
    {kind: 'file', path: '/added.tex'},
    {kind: 'terminal'},
  ]);
  assert.deepEqual(publications, [['/api/tex/preview', {path: '/added.tex', content: 'saved source', publish: true}]]);
  console.log('layout suite: 14 passed, 0 failed');
})().catch(error => { console.error(error); process.exitCode = 1; });

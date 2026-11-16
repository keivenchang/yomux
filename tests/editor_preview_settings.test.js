const {assert, fs, vm, test, testAsync, loadYomux, runSuites} = require('./browser_helpers/layout_test_helper');
const {runEditorPreviewSuite} = require('./browser_helpers/editor_preview_suite');

test('full metadata at the boot revision arrives without reverting settings', () => {
  const schema = JSON.parse(fs.readFileSync('assets/runtime/settings-schema.json', 'utf8'));
  const api = loadYomux('', undefined, undefined, undefined, undefined, {
    settingsPayload: {mtime_ns: 1, defaults: schema.DEFAULT_SETTINGS, settings: {}},
  });
  const applied = api.applySettingsPayloadForTest({mtime_ns: 1, settings: {appearance: {editor_cursor_style: 'block'}},
    catalog: {'tabber.codex_model': {choice_metadata: schema.YOAGENT_CODEX_MODEL_CATALOG}},
    choices: schema.SETTING_PAYLOAD_CHOICE_ORDER});
  assert.equal(applied, false);
  assert.equal(api.fileEditorCursorStyleForTest(), 'line');
  const html = api.preferencesPanelHtmlForTest('', []);
  const choices = [...html.matchAll(/<input\b[^>]*data-setting-path="tabber\.codex_effort"[^>]*>/g)]
    .map(match => match[0].match(/\bvalue="([^"]+)"/)[1]);
  assert.deepEqual(choices, schema.YOAGENT_CODEX_MODEL_CATALOG['gpt-6-luna'].effort_options);
});

test('Codex effort controls and model changes share the catalog across contexts', () => {
  const schema = JSON.parse(fs.readFileSync('assets/runtime/settings-schema.json', 'utf8'));
  const api = loadYomux();
  let revision = 300;
  for (const section of ['yoagent', 'tabber']) for (const model of ['gpt-6-luna', 'gpt-6-astra']) {
    api.applySettingsPayloadForTest({mtime_ns: revision++, defaults: schema.DEFAULT_SETTINGS,
      settings: {[section]: {codex_model: model}},
      catalog: {[`${section}.codex_model`]: {choice_metadata: schema.YOAGENT_CODEX_MODEL_CATALOG}}}, {initial: true});
    const html = api.preferencesPanelHtmlForTest('', []);
    const choices = [...html.matchAll(/<input\b[^>]*>/g)]
      .filter(match => match[0].includes(`data-setting-path="${section}.codex_effort"`))
      .map(match => match[0].match(/\bvalue="([^"]+)"/)[1]);
    assert.deepEqual(choices, schema.YOAGENT_CODEX_MODEL_CATALOG[model].effort_options);
    const patch = api.settingPatchForPathForTest(`${section}.codex_model`, model);
    assert.equal(patch[section].codex_effort, schema.YOAGENT_CODEX_MODEL_CATALOG[model].default_effort);
  }
});

test('new editors use line cursors while explicit block preferences persist', () => {
  const schema = JSON.parse(fs.readFileSync('assets/runtime/settings-schema.json', 'utf8'));
  assert.equal(schema.DEFAULT_SETTINGS.appearance.editor_cursor_style, 'line');
  const api = loadYomux();
  api.applySettingsPayloadForTest({mtime_ns: 100, defaults: schema.DEFAULT_SETTINGS, settings: {appearance: {editor_cursor_style: 'block'}}}, {initial: true});
  assert.equal(api.fileEditorCursorStyleForTest(), 'block');
  api.applySettingsPayloadForTest({mtime_ns: 101, defaults: schema.DEFAULT_SETTINGS, settings: {appearance: {}}}, {force: true});
  assert.equal(api.fileEditorCursorStyleForTest(), 'line');
});

test('repeated blocked renders preserve the bounded status pulse', () => {
  const api = loadYomux();
  api.setWorkflowTransitionGlowSecondsForTest(5);
  const options = {session: 'blocked-fixture', window_index: 1, nowSeconds: 100, scheduleRefresh: false};
  assert.equal(api.agentWindowActivityIconForTest('codex', 'blocked', null, options).pulseActive, true);
  assert.equal(api.agentWindowActivityIconForTest('codex', 'blocked', null, {...options, nowSeconds: 100.1}).pulseActive, true);
  assert.equal(api.agentWindowActivityIconForTest('codex', 'blocked', null, {...options, nowSeconds: 106}).pulseActive, false);
});

test('session popovers render filesystem paths as text', () => {
  const api = loadYomux('', ['1']);
  const path = '/tmp/<img src=x onerror="window.__pathInjected=true">';
  const info = {session: '1', selected_pane: {current_path: path}, panes: [{current_path: path, window: 0, window_active: true, active: true}], agents: []};
  const html = api.sessionPopoverHtml('1', info, '', false, {key: 'idle', label: 'Idle', reason: ''});
  assert.equal(html.includes('<img src=x'), false);
  assert.ok(html.includes('&lt;img src=x'));
});

test('editor option changes preserve pending copied-buffer dirty state', () => {
  const settings = fs.readFileSync('static_src/js/yomux/50_editor_settings_runtime.js', 'utf8');
  const files = fs.readFileSync('static_src/js/yomux/45_file_explorer_actions.js', 'utf8');
  const state = {kind: 'text', original: 'old', content: 'old', dirty: false};
  const panels = ['old', 'new unsaved text'].map((text, index) => ({
    classList: {toggle() {}}, dataset: {filePath: '/file', layoutItem: `copy-${index}`},
    querySelector: () => ({}), text, _cmSerializeTimer: index,
  }));
  const context = {
    document: {querySelectorAll: () => panels}, fileState: new Map([['/file', state]]),
    fileEditorWrapEnabled: true, fileEditorLineNumbersEnabled: true,
    updateEditorWrapButton() {}, updateEditorGutterButton() {},
    fileEditorStateForItem: () => state, fileEditorPanelState: () => state,
    fileEditorStateWritable: () => true, fileEditorPanelMode: () => 'edit',
    fileEditorPanelsForPath: () => panels,
    codeMirrorCurrentText: panel => panel.text, codeMirrorPanelContent: panel => panel.text,
    flushCodeMirrorSource(panel) { panel._cmSerializeTimer = null; },
    renderFileEditorPreviewSurface() {}, reconfigureCodeMirrorPanelEditorOptions: () => true,
  };
  vm.createContext(context);
  vm.runInContext(files.slice(files.indexOf('function updateOpenFileDirtyFlag('), files.indexOf('function openFileAutosaveReady('))
    + settings.slice(settings.indexOf('function applyEditorWrapPreference('), settings.indexOf('function setEditorWrapEnabled(')), context);
  context.applyEditorWrapPreference();
  assert.equal(state.content, 'new unsaved text');
  assert.equal(state.dirty, true, 'copy synchronization cannot mark native edits clean');
});

test('pulse synchronization and restart discover animations before changing their timelines', () => {
  const source = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const code = source.slice(source.indexOf('function collectAgentWindowPulseNodes('), source.indexOf('function scheduleAgentWindowActivityAnimationSync('));
  let dirty = false;
  let flushes = 0;
  let writes = 0;
  const nodes = Array.from({length: 20}, () => ({
    classList: {contains: name => name === 'status-indicator'},
    querySelector: () => null,
    closest: () => null,
    style: {getPropertyValue: () => '', removeProperty: () => { dirty = true; }},
    getAnimations() {
      if (dirty) { flushes += 1; dirty = false; }
      return [{
        animationName: 'pulse',
        effect: {getTiming: () => ({duration: 1000, delay: -250})},
        set startTime(value) { assert.equal(value, -750); writes += 1; dirty = true; },
        cancel() { dirty = true; },
        play() { dirty = true; },
      }];
    },
  }));
  const context = {
    document: {querySelectorAll: () => nodes}, Date, performance: {timeOrigin: 1000},
    agentWindowActivityPulseSelector: 'pulse',
    statusPulseAnimationEnabled: () => true,
    attentionAnimationClockDelay: () => '',
    attentionAnimationDurationMs: () => 1000,
    agentWindowActivityAnimationUsesGlobalPhase: () => true,
  };
  vm.runInNewContext(code + ';syncAgentWindowActivityAnimationDelays();', context);
  assert.equal(writes, 20);
  assert.equal(flushes, 0, 'timeline writes cannot force layout before the next marker discovery');
  dirty = false;
  flushes = 0;
  vm.runInNewContext('restartAgentWindowActivityPulseAnimations();', context);
  assert.equal(flushes, 1, 'restart has one discovery pass after all cancellation/play writes');
  assert.equal(writes, 40);
});

test('pulse mutation scheduling retains its subtree and discovers all frame scopes before writes', () => {
  const source = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
  const code = source.slice(source.indexOf('function mutationTouchesAgentWindowActivity('), source.indexOf('function agentWindowActivityTransitionKey('));
  const frames = [];
  let observeMutation;
  let documentQueries = 0;
  let graphQueries = 0;
  let dirty = false;
  let discoveries = 0;
  let writes = 0;
  const pulse = {
    matches: () => true,
    contains: () => false,
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    classList: {contains: name => name === 'js-debug-live-pulse'},
    style: {getPropertyValue: () => ''},
    getAnimations() {
      assert.equal(dirty, false, 'all scopes must be discovered before timeline writes');
      discoveries += 1;
      return [{animationName: 'agent-status-opacity-pulse',
        effect: {getTiming: () => ({duration: 1000})},
        set startTime(value) { assert.equal(value, -1000); dirty = true; writes += 1; },
      }];
    },
  };
  const sibling = {...pulse};
  const graph = {
    contains: node => node === pulse,
    querySelectorAll: () => { graphQueries += 1; return [pulse]; },
  };
  const context = {
    document: {body: {}, querySelectorAll: () => { documentQueries += 1; return []; }},
    Date, Number, Set, performance: {timeOrigin: 1000},
    MutationObserver: function(callback) { observeMutation = callback; this.observe = () => {}; },
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    agentWindowActivityMutationObserver: null, agentWindowActivityAnimationSyncFrame: 0,
    agentWindowActivityAnimationSyncDocument: false, agentWindowActivityAnimationSyncRoots: new Set(),
    agentWindowActivityPulseSelector: 'pulse',
    agentWindowActivityPulseAnimationNames: new Set(['agent-status-opacity-pulse']),
    attentionAnimationDelay: () => '', attentionAnimationDurationMs: () => 1000,
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  context.scheduleAgentWindowActivityAnimationSync(graph);
  observeMutation([{target: graph, addedNodes: [pulse]}]);
  context.scheduleAgentWindowActivityAnimationSync(pulse);
  context.scheduleAgentWindowActivityAnimationSync(sibling);
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(documentQueries, 0, 'a graph mutation cannot supersede its scoped work');
  assert.equal(graphQueries, 1, 'overlapping scopes must be queried once');
  assert.equal(discoveries, 2, 'matching roots participate even without matching descendants');
  assert.equal(writes, 2);
});

runSuites([async () => {
  await testAsync('YOagent sends sharing a pending availability read queue behind the active request', async () => {
    const source = fs.readFileSync('static_src/js/yomux/81_yoagent_panel.js', 'utf8');
    let release;
    const availability = new Promise(resolve => { release = resolve; });
    let posts = 0, serial = 0;
    const queued = [];
    const context = {
      yoagentChatState: {busy: false, activeRequest: null}, YOAGENT_CHAT_BACKENDS: ['codex'],
      yoagentBackendKey: () => 'codex', refreshYoagentAgentAvailability: () => availability,
      yoagentChatEnabled: () => true, resetYoagentComposerHistory() {}, installYoagentFocusTracker() {},
      yoagentNewChatRequestId: () => String(++serial), AbortController,
      yoagentConversationState: {messages: [], pendingWaits: [], streamingMessages: new Map()},
      yoagentFocusSerial: 0, yoagentChatInputIsFocused: () => false, yoagentDocumentHasFocus: () => true,
      yoagentStartupState: {}, startYoagentLocalThinkingStream() {}, renderYoagentPanel() {},
      i18nActiveLocaleId: () => 'en', enqueueYoagentChatMessage: text => queued.push(text),
      apiFetchJson: () => { posts++; return new Promise(() => {}); },
    };
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('async function startYoagentChatRequest('), source.indexOf('function updateYoagentActionPreview(')), context);
    context.sendYoagentChatMessage('first');
    const second = context.sendYoagentChatMessage('second');
    release();
    await second;
    assert.equal(posts, 1);
    assert.deepStrictEqual(queued, ['second']);
    assert.equal(context.yoagentChatState.activeRequest.text, 'first');
  });
  await testAsync('deleted Differ rows retain unsaved working buffers', async () => {
    const api = loadYomux();
    const path = '/repo/deleted.md';
    api.registerFileEditorLayoutItemForTest(path);
    api.setOpenFileStateForTest(path, {kind: 'text', content: 'unsaved buffer', original: 'old disk', dirty: true});
    await api.openChangedFileInDiffForTest(path, '1', 'D', '/repo', {openMode: 'edit'});
    const state = api.currentFileStateForTest(path);
    assert.equal(state.content, 'unsaved buffer');
    assert.equal(state.original, 'old disk');
    assert.equal(state.dirty, true);
    assert.equal(state.externalMissing, true);
  });
  await testAsync('emoji catalog retries failed and incomplete loads without duplicating concurrent requests', async () => {
    const source = fs.readFileSync('static_src/js/yomux/82_chat_panel.js', 'utf8');
    const code = source.slice(source.indexOf('function loadChatEmojiCatalog()'), source.indexOf('function chatRecentEmoji()'));
    const scripts = [];
    const context = {
      chatState: {emojiCatalogPromise: null}, t: key => key,
      document: {createElement: () => ({}), head: {appendChild: script => scripts.push(script)}},
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    const first = context.loadChatEmojiCatalog();
    assert.equal(context.loadChatEmojiCatalog(), first, 'concurrent callers share one pending acquisition');
    const failed = assert.rejects(first, /common.requestFailed/);
    scripts[0].onerror();
    await failed;
    const incomplete = context.loadChatEmojiCatalog();
    assert.equal(scripts.length, 2, 'a transport failure permits a fresh request');
    const missing = assert.rejects(incomplete, /common.requestFailed/);
    scripts[1].onload();
    await missing;
    const recovered = context.loadChatEmojiCatalog();
    assert.equal(scripts.length, 3, 'a script missing its catalog also permits recovery');
    const catalog = [{emoji: '😀', names: {en: 'grinning face'}, keywords: {en: ['smile']}}];
    context.YOMUX_EMOJI_DATA = catalog;
    scripts[2].onload();
    assert.equal(await recovered, catalog, 'recovery publishes the actual installed catalog');
    assert.equal(await context.loadChatEmojiCatalog(), catalog);
    assert.equal(scripts.length, 3, 'a complete catalog is reused without another request');
  });
  await testAsync('chat search closure and empty queries retire pending reads', async () => {
    const source = fs.readFileSync('static_src/js/yomux/82_chat_panel.js', 'utf8');
    const code = source.slice(source.indexOf('async function searchChatHistory('), source.indexOf('function bindChatPanel('));
    const timeline = {scrollTop: 15};
    const panel = {querySelector: selector => selector === '[data-chat-timeline]' ? timeline : null};
    let release, reject;
    let notices = 0;
    const context = {
      URLSearchParams,
      chatItemId: 'chat', panelDomId: item => item,
      document: {getElementById: () => panel},
      chatState: {messages: new Map([[1, {id: 1}]]), contextGeneration: 0, searchGeneration: 0, searchResults: []},
      apiFetchJson: () => new Promise((resolve, fail) => { release = resolve; reject = fail; }),
      chatRequestOptions: () => ({}), renderChatPanel() {}, cssEscape: value => value,
      chatTimelineIsNearBottom: () => false, statusErr() { notices += 1; }, localizedHtml: value => value,
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    const pendingContext = context.openChatMessageContext(99);
    context.closeChatSearch();
    release({target: {id: 99}});
    await pendingContext;
    assert.deepEqual([...context.chatState.messages.keys()], [1], 'late context cannot replace the restored timeline');
    const pendingSearch = context.searchChatHistory('old query');
    await context.searchChatHistory('');
    release({hits: [{target: {id: 99}}]});
    await pendingSearch;
    assert.equal(context.chatState.searchResults.length, 0, 'late search cannot repopulate a cleared query');
    const failedSearch = context.searchChatHistory('retired query');
    context.closeChatSearch();
    reject(new Error('retired search failed'));
    await failedSearch;
    const failedContext = context.openChatMessageContext(99);
    context.closeChatSearch();
    reject(new Error('retired context failed'));
    await failedContext;
    assert.equal(notices, 0, 'retired reads cannot publish stale error notices');
  });
  await runEditorPreviewSuite({shardIndex: 2, shardCount: 3});
}]);

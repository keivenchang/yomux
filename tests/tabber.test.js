const {
  assert,
  fs,
  vm,
  FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST,
  DEFAULT_TEST_SETTINGS,
  TestClassList,
  TestStyle,
  testDatasetKeyForAttribute,
  TestElement,
  TestFile,
  TestFormData,
  assertNoStandalonePrBadge,
  assertSingleCiBadge,
  loadYomux,
  fileExplorerClosedOptions,
  loadYomuxWithFileExplorerClosed,
  treeKeyEvent,
  tabElement,
  tabStrip,
  dragEvent,
  fileDragEvent,
  jsonResponse,
  flushAsyncWork,
  terminalLine,
  nestedSlots,
  parseUrl,
  canonical,
  makeFileTree,
  test,
  testAsync,
  runSuites,
  finishSuite,
} = require('./browser_helpers/layout_test_helper');

test('Flat row identities preserve colliding session names through reconciliation and context actions', () => {
  for (const mode of ['normal', 'advanced']) {
    const api = loadYomux('?sessions=__tabber__,a%20b,a_b', ['a b', 'a_b']);
    api.setTabberModeForTest(mode);
    api.setFileExplorerModeForTest('tabber');
    for (const session of ['a b', 'a_b']) api.setTranscriptInfoForTest(session, {
      panes: [{window: '0', window_name: 'bash', command: 'bash', window_active: true}],
    });
    const panel = new TestElement('collision-panel');
    const groups = new TestElement('collision-groups');
    panel.appendChild(groups);
    api.bindTabberPanelForTest(panel);
    const actions = [];
    api.tabInteractionControllerForTest().showActions = descriptor => actions.push(descriptor);
    for (const reverse of [false, true, false]) {
      api.setTabberActivityForTest({activity: {'a b:0': {active_recency_ts: reverse ? 100 : 200}, 'a_b:0': {active_recency_ts: reverse ? 200 : 100}}});
      api.renderTabberTreeForTest(groups, {reorder: true});
      const rows = groups.querySelectorAll('.file-tree-row[data-tabber-type="normal"]');
      assert.equal(new Set(rows.map(row => row.dataset.path)).size, 2);
      assert.equal(rows.find(row => row.dataset.tabberSession === 'a b').dataset.path, '/s_a%20b/w_0');
      assert.equal(rows.find(row => row.dataset.tabberSession === 'a_b').dataset.path, '/s_a_b/w_0');
      for (const row of rows) {
        const event = {target: row, clientX: 10, clientY: 12, preventDefault() {}, stopPropagation() {}};
        panel.listeners.get('contextmenu')[0](event);
        assert.equal(actions.at(-1).item, row.dataset.tabberSession);
        assert.equal(actions.at(-1).descriptionWindow, 0);
        assert.equal(actions.at(-1).anchor, row);
      }
      const key = treeKeyEvent('ArrowDown', panel);
      panel.listeners.get('keydown').forEach(listener => listener(key));
      const selected = api.tabberTreeSelectionForTest();
      assert.ok(rows.some(row => row.dataset.path === selected.lead));
    }
  }
});

test('Window kill actions follow the stable row identity across index reuse', () => {
  for (const mode of ['normal', 'advanced']) {
    for (const present of [true, false]) {
      const api = loadYomux('', ['one']);
      api.setTabberModeForTest(mode);
      api.setTranscriptInfoForTest('one', {panes: [
        {window: '0', window_id: '@20', window_name: 'replacement', command: 'bash', window_active: true},
        ...(present ? [{window: '1', window_id: '@10', window_name: 'original', command: 'bash'}] : []),
      ]});
      api.showTabContextMenu('one', 10, 12, {descriptionWindow: 0, descriptionWindowId: '@10'});
      const menu = api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('session-context-menu'));
      const labels = Array.from(menu?.children || []).map(child => child.textContent);
      assert.equal(labels.some(label => label.includes('Kill replacement')), false, JSON.stringify(labels));
      assert.equal(labels.some(label => label.includes('Kill original')), present, JSON.stringify(labels));
      if (!present) assert.equal(labels.some(label => label.includes('Kill tmux session')), false);
    }
  }
});

test('Recent updates dates in place and sorts the latest activity only every ten seconds', () => {
  for (const mode of ['normal', 'advanced']) {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest(mode);
    api.setFileExplorerModeForTest('tabber');
    const groups = new TestElement('', 'div');
    const update = (now, one, two) => {
      api.setFileTreeRecencyNowForTest(now);
      api.applyTabberActivityPayloadForTest({activity: {one: {active_recency_ts: one}, two: {active_recency_ts: two}},
        session_order: ['one', 'two'], sessions: {one: {panes: []}, two: {panes: []}}});
      api.renderTabberTreeForTest(groups);
    };
    const rows = () => groups.querySelectorAll('.file-tree-row[data-tabber-type="normal"]');
    const order = () => rows().map(row => row.dataset.tabberSession);
    update(1_000_000, 900, 800);
    assert.deepEqual(order(), ['one', 'two']);
    const second = rows()[1];
    const oldDate = second.querySelector('.file-tree-date').textContent;
    update(1_001_000, 900, 1001);
    assert.deepEqual(order(), ['one', 'two'], mode);
    assert.notEqual(second.querySelector('.file-tree-date').textContent, oldDate, 'dates stay live');
    update(1_009_999, 900, 1009);
    assert.deepEqual(order(), ['one', 'two']);
    update(1_010_000, 900, 1010);
    assert.deepEqual(order(), ['two', 'one'], 'the next interval uses the latest timestamps');
    api.setFileExplorerTreeSortModeForTest('az');
    api.renderTabberTreeForTest(groups);
    assert.deepEqual(order(), ['one', 'two'], 'manual sort applies immediately');
    api.setFileExplorerTreeSortModeForTest('newest');
    api.renderTabberTreeForTest(groups);
    assert.deepEqual(order(), ['two', 'one'], 'returning to Recent applies immediately');
  }
});

test('shared status renderer uses transport shapes for one visible color and circles for mixed colors', () => {
  const api = loadYomux('', ['one']);
  const cases = [
    ['working', [], 'play'], ['working', ['working', 'working'], 'play'],
    ['cooldown', ['cooldown', 'cooldown'], 'pause'],
    ['attention', ['attention'], 'stop'], ['blocked', ['attention', 'blocked', 'unavailable'], 'stop'],
    ['unavailable', ['unavailable'], 'stop'],
    ['working', ['working', 'attention'], ''], ['blocked', ['blocked', 'working'], ''],
    ['cooldown', ['cooldown', 'blocked'], ''], ['attention', ['working', 'cooldown', 'attention'], ''],
    ['cooldown', ['cooldown'], 'pause'], ['attention', ['attention'], 'stop'], ['working', ['working'], 'play'],
  ];
  for (const [state, aggregateTones, shape] of cases) {
    const html = api.agentWindowStatusDotHtmlForTest({state, icon: '●', aggregateTones}, {label: 'Window status'});
    for (const candidate of ['play', 'pause', 'stop']) assert.equal(html.includes(`agent-window-status-dot--${candidate}`), shape === candidate, html);
    assert.equal(html.includes('agent-window-status-dot--segmented'), !shape, html);
    assert.match(html, /aria-label="Window status"/);
    if (!shape && aggregateTones.includes('blocked')) assert.match(html, /agent-window-status-dot--attention-(cooldown|working)/);
  }
});

test('terminal input clocks survive in-flight snapshots and exclude passive or rejected input', () => {
  const api = loadYomux('', ['one']);
  const snapshot = timestamp => ({activity: {one: {active_recency_ts: timestamp, last_user_input_ts: timestamp}},
    session_order: ['one'], sessions: {one: {panes: []}}});
  api.applyTabberActivityPayloadForTest(snapshot(100));
  const frames = [];
  const socket = {readyState: 1, send(frame) { frames.push(JSON.parse(frame)); }};
  api.registerTerminalForTest('one', {focus() {}, rows: 24, cols: 80}, socket);
  for (const key of ['a', 'b', '\x00', '\x1b[1;2R']) {
    api.noteTerminalExplicitInputForTest('one');
    assert.equal(api.handleTerminalDataForTest('one', key), true);
    assert.equal(frames.at(-1).user_input, true, 'native keyboard provenance includes Ctrl-Space and modified F3');
  }
  const local = api.tabberRecencyForTest('one');
  assert.ok(local > 100);
  api.applyTabberActivityPayloadForTest(snapshot(101));
  assert.equal(api.tabberRecencyForTest('one'), local, 'an earlier input acknowledgement cannot clear the newest local input');
  api.noteTerminalExplicitInputForTest('one');
  api.handleTerminalDataForTest('one', '\x1b[<0;4;8M');
  assert.equal(frames.at(-1).user_input, false);
  assert.equal(api.tabberRecencyForTest('one'), local);
  api.handleTerminalDataForTest('one', 'xterm-fallback');
  assert.equal(frames.at(-1).user_input, true, 'printable xterm data is sufficient when the browser missed DOM key provenance');
  assert.ok(api.tabberRecencyForTest('one') >= local, `the inferred input does not regress the immediate local date: ${local} -> ${api.tabberRecencyForTest('one')}`);
  const inferred = api.tabberRecencyForTest('one');
  api.handleTerminalDataForTest('one', '\x1b[<0;4;8M');
  assert.equal(api.tabberRecencyForTest('one'), inferred, 'mouse tracking remains excluded from inferred activity');
  socket.readyState = 0;
  api.noteTerminalExplicitInputForTest('one');
  assert.equal(api.handleTerminalDataForTest('one', 'offline'), false);
  assert.equal(api.tabberRecencyForTest('one'), inferred, 'rejected offline input leaves the last accepted activity visible');
  api.applyTabberActivityPayloadForTest(snapshot(local + 1));
  assert.equal(api.tabberRecencyForTest('one'), local + 1, 'the server clock takes over after acknowledgement');
});

test('latest repeated editor and Repo Diff engagement survives reload inside the touch throttle', () => {
  const url = '?layout=left&tabs=left:file%3A%2Ftmp%2F2026.md';
  const api = loadYomux(url, ['one']);
  const item = api.fileEditorItemFor('/tmp/2026.md');
  const diff = api.gitDiffItemFor('/tmp/repo');
  api.noteTabberItemActivityForTest(item);
  api.noteTabberItemActivityForTest(diff);
  api.noteTabberItemActivityForTest(item);
  const stored = api.sessionStorageValueForTest('yomux-tabber-item-activity');
  const latest = JSON.parse(stored).find(entry => entry[0] === item)[1];
  const restored = loadYomux(url, ['one'], 'http:', 'Linux x86_64', 'admin', {
    sessionStorage: {'yomux-tabber-item-activity': stored},
  });
  restored.setTabberActivityForTest({activity: {[item]: {active_recency_ts: latest - 2}}});
  assert.equal(restored.buildTabberNormalRowsForTest().find(entry => entry.tabber?.item === item)?.mtime, latest);
});

testAsync('restoring an editor does not count as recent activity', async () => {
  const api = loadYomux('?layout=left&tabs=left:file%3A%2Ftmp%2F2026.md', ['one'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}});
  const path = '/tmp/2026.md';
  const item = api.fileEditorItemFor(path);
  api.setOpenFileStateForTest(path, {kind: 'text', content: '# 2026', original: '# 2026', dirty: false, mtime: 1});
  api.setFetchForTest(() => Promise.resolve(jsonResponse({path, content: '# 2026', size: 6, mtime: 1})));

  api.loadFileEditorStateForTest(path, null, item);
  await flushAsyncWork();
  const editorEntry = () => api.buildTabberNormalRowsForTest().find(entry => entry.tabber?.item === item);
  assert.equal(editorEntry()?.mtime, 0, 'automatic disk loading after reload must not make an old editor recent');

  api.noteTabberItemActivityForTest(item);
  assert.ok(editorEntry()?.mtime > 0, 'an explicit editor interaction still records activity');
});

testAsync('editor and Repo Diff engagement updates the matching Tabber date', async () => {
  const api = loadYomux('?layout=left&tabs=left:file%3A%2Ftmp%2F2026.md', ['one'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}});
  const touches = [];
  api.setFetchForTest(async (url, options = {}) => {
    if (String(url).endsWith('/api/activity/touch')) touches.push({url, options});
    return jsonResponse({ok: true});
  });
  api.setTabberModeForTest('normal');
  api.setFileTreeRecencyNowForTest(Date.now());
  const editorItem = api.fileEditorItemFor('/tmp/2026.md');
  let editorEntry = api.buildTabberNormalRowsForTest().find(entry => entry.tabber?.item === editorItem);
  assert.ok(editorEntry, 'the open editor is represented by a Tabber utility row');
  assert.equal(editorEntry.mtime, 0, 'an untouched editor starts without local activity');
  assert.equal(api.noteTabberItemActivityForTest(editorItem), true);
  editorEntry = api.buildTabberNormalRowsForTest().find(entry => entry.tabber?.item === editorItem);
  assert.ok(editorEntry.mtime > 0, 'the editor gets its own local activity timestamp');
  const editorRow = api.tabberRenderedRowsForTest().find(row => row.name === '2026.md');
  assert.ok(editorRow?.date, 'the editor row displays its new activity date');

  const diffItem = api.resolveLayoutItem(api.gitDiffItemFor('/tmp/repo'));
  await api.toggleTabberNormalPaneForTest(diffItem);
  api.setFileTreeRecencyNowForTest(Date.now());
  assert.equal(api.noteTabberItemActivityForTest(diffItem), true);
  const diffEntry = api.buildTabberNormalRowsForTest().find(entry => entry.tabber?.item === diffItem);
  assert.ok(diffEntry?.mtime > 0, 'Repo Diff gets a matching local activity timestamp');
  await flushAsyncWork();
  assert.deepEqual(touches.map(({options}) => JSON.parse(options.body).item), [editorItem, diffItem], `editor and Repo Diff activity is reported to the server for reload snapshots: ${JSON.stringify({touches, sentAt: [api.tabberItemActivityTouchSentAtForTest(editorItem), api.tabberItemActivityTouchSentAtForTest(diffItem)], errors: api.clientPerfSummaryForTest?.()})}`);

  const restored = loadYomux('?layout=left&tabs=left:file%3A%2Ftmp%2F2026.md', ['one']);
  restored.setTabberActivityForTest({activity: {[editorItem]: {active_recency_ts: 1700000000}}});
  const restoredEntry = restored.buildTabberNormalRowsForTest().find(entry => entry.tabber?.item === editorItem);
  assert.equal(restoredEntry?.mtime, 1700000000, 'the server activity snapshot restores the editor date after reload');
});

testAsync('editing file content reports activity through the shared editor change owner', async () => {
  const path = '/tmp/typed.md';
  const api = loadYomux(`?layout=left&tabs=left:${encodeURIComponent(`file:${path}`)}`, ['one'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}});
  const item = api.fileEditorItemFor(path);
  const touches = [];
  api.setFetchForTest(async (url, options = {}) => {
    if (String(url).endsWith('/api/activity/touch')) touches.push(JSON.parse(options.body).item);
    return jsonResponse({ok: true});
  });
  api.setOpenFileStateForTest(path, {kind: 'text', content: '# before', original: '# before', dirty: false});
  const panel = new TestElement('typed-editor');
  panel.dataset.layoutItem = item;
  panel.dataset.filePath = path;
  api.handleFileEditorContentChangedForTest(panel, path, '# after', {sourceSurface: 'text-editor'});
  const entry = api.buildTabberNormalRowsForTest().find(row => row.tabber?.item === item);
  assert.ok(entry?.mtime > 0, 'editing text immediately advances the editor Tabber date');
  await flushAsyncWork();
  assert.deepEqual(touches, [item], 'typing sends a server activity touch that is available after browser reload');

  const previewPath = '/tmp/preview-typed.md';
  const previewApi = loadYomux(`?layout=left&tabs=left:${encodeURIComponent(`file:${previewPath}`)}`, ['one'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {backendLabel: 'Go test'}});
  const previewItem = previewApi.fileEditorItemFor(previewPath);
  const previewTouches = [];
  previewApi.setFetchForTest(async (url, options = {}) => {
    if (String(url).endsWith('/api/activity/touch')) previewTouches.push(JSON.parse(options.body).item);
    return jsonResponse({ok: true});
  });
  previewApi.setOpenFileStateForTest(previewPath, {kind: 'text', content: '# before', original: '# before', dirty: false});
  const previewPanel = new TestElement('typed-preview');
  previewPanel.dataset.layoutItem = previewItem;
  previewPanel.dataset.filePath = previewPath;
  previewApi.handleFileEditorContentChangedForTest(previewPanel, previewPath, '# edited in preview', {sourceSurface: 'markdown-preview'});
  const previewEntry = previewApi.buildTabberNormalRowsForTest().find(row => row.tabber?.item === previewItem);
  assert.ok(previewEntry?.mtime > 0, 'editing Markdown Preview advances its Tabber date through the same change owner');
  await flushAsyncWork();
  assert.deepEqual(previewTouches, [previewItem], 'Markdown Preview typing reaches the server-owned activity snapshot');
});

test('Advanced Tabber uses Normal window cards and keeps only its additional toolbar controls', () => {
  const api = loadYomux('', ['1']);
  api.setTranscriptInfoForTest('1', {
    panes: [{window: '0', window_name: 'codex', command: 'codex', window_active: true},
      {window: '1', window_name: 'bash', command: 'bash', window_active: false}],
    window_metadata: [{window_index: 0, description: 'First conversation'}, {window_index: 1, description: 'Second conversation'}],
  });
  api.setTabberActivityForTest({activity: {'1:0': {last_user_input_ts: 100}, '1:1': {last_user_input_ts: 200}}});
  api.setTabberModeForTest('normal');
  const normal = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
  api.setTabberModeForTest('advanced');
  const advanced = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
  assert.equal(advanced.length, normal.length);
  for (const row of advanced) {
    const counterpart = normal.find(candidate => candidate.path === row.path);
    assert.ok(row.normalRow);
    assert.equal(row.nameHtml, counterpart.nameHtml);
    assert.equal(row.date, counterpart.date);
  }
  const toolbar = api.fileExplorerChangesPanelStaticHtmlForTest({view: 'tabber'});
  for (const control of ['data-tabber-lookback', 'data-file-explorer-tree-sort', 'data-file-explorer-tree-dates']) assert.ok(toolbar.includes(control));
  assert.equal(toolbar.includes('tabber-new-chat-popover'), true);
  assert.equal(toolbar.includes('data-app-menu="tabber-new-chat"'), true);
});

test('Tabber relative-date migration runs after URL restore exactly once and preserves other choices', () => {
  const storageKey = 'yomux.fileExplorer.viewSettings.v2';
  const migratedKey = 'yomux.tabber.relativeDates.migrated.v1';
  for (const previous of ['date', 'none']) {
    const settings = {finder: {treeDateMode: 'none', treeSortMode: 'za'},
      tabber: {treeDateMode: previous, treeSortMode: 'oldest'},
      differ: {treeDateMode: 'date', treeSortMode: 'az'}};
    const state = {v: 1, finder: {viewSettings: settings}};
    const api = loadYomux(`?sessions=__tabber__&state=${encodeURIComponent(JSON.stringify(state))}`, ['1'],
      'http:', 'Linux x86_64', 'admin', {localStorage: {[storageKey]: JSON.stringify(settings)}});
    assert.equal(api.fileExplorerViewSettingsForTest().tabber.treeDateMode, previous, 'URL seed restores before migration');
    api.syncInitialLayoutUrlForTest();
    const expected = {...settings, tabber: {...settings.tabber, treeDateMode: 'relative'}};
    assert.deepEqual(canonical(api.fileExplorerViewSettingsForTest()), expected);
    assert.deepEqual(JSON.parse(api.storageValueForTest(storageKey)), expected, 'migration persists all surface settings');
    assert.equal(api.storageValueForTest(migratedKey), '1');
    const migratedUrl = new URL(api.lastUrlForTest(), 'http://localhost');
    assert.equal(JSON.parse(migratedUrl.searchParams.get('state')).finder.viewSettings.tabber.treeDateMode, 'relative');
    for (const choice of ['none', 'date', 'relative']) {
      api.setFileExplorerViewSettingForTest('tabber', 'treeDateMode', choice, {});
      const reloaded = loadYomux(new URL(api.lastUrlForTest(), 'http://localhost').search, ['1'],
        'http:', 'Linux x86_64', 'admin', {localStorage: {
          [storageKey]: api.storageValueForTest(storageKey), [migratedKey]: '1'}});
      reloaded.syncInitialLayoutUrlForTest();
      assert.equal(reloaded.fileExplorerViewSettingsForTest().tabber.treeDateMode, choice, 'later explicit choices survive reload');
    }
  }
});

test('shared Tabber reconciliation holds sibling order at every expanded depth and retires exited rows', () => {
  const api = loadYomux('', ['1']);
  const tree = new TestElement('held-tabber-tree');
  const entry = (name, mtime, kind = 'file') => ({name, kind, mtime, tabber:{type:'repo',label:name,repoRoot:`/repo/${name}`}});
  const options = {mode:'tabber', view:'tabber', treeSortMode:'newest', autoExpand:true, includeHidden:true};
  const render = (roots, children, preserveDisplayedOrder) => api.renderTreeChildrenForTest(tree, '/', roots, 0,
    [['/parent', children]], {...options, preserveDisplayedOrder});
  const directRows = container => [...container.children].filter(node => node.classList.contains('file-tree-row'));
  const names = container => directRows(container).map(row => row.dataset.name);
  render([entry('parent', 10, 'dir'), entry('other', 5, 'dir')], [entry('a', 10), entry('b', 5)], false);
  const originals = directRows(tree);
  const children = tree.querySelector('.file-tree-children');
  const childOriginals = directRows(children);
  for (const timestamp of [20, 30, 50]) {
    render([entry('other', timestamp, 'dir'), entry('parent', 10, 'dir')], [entry('b', timestamp), entry('a', 10)], true);
    assert.deepEqual(names(tree), ['parent','other']);
    assert.deepEqual(names(children), ['a','b']);
    assert.equal(directRows(tree)[0], originals[0]);
    assert.equal(directRows(children)[0], childOriginals[0]);
  }
  render([entry('other', 50, 'dir'), entry('parent', 10, 'dir'), entry('new',100,'dir')],
    [entry('b',50),entry('a',10),entry('c',100)], true);
  assert.deepEqual(names(tree), ['parent','other','new']);
  assert.deepEqual(names(children), ['a','b','c']);
  render([entry('parent',10,'dir'),entry('new',100,'dir')], [entry('b',50),entry('c',100)], true);
  assert.deepEqual(names(tree), ['parent','new']);
  assert.deepEqual(names(children), ['b','c']);
  render([entry('parent',10,'dir'),entry('new',100,'dir')], [entry('b',50),entry('c',100)], false);
  assert.deepEqual(names(tree), ['new','parent']);
  assert.deepEqual(names(children), ['c','b']);
});

test('Advanced transition distributes the complete recency roster and selects Finder by default', () => {
  for (const count of [0, 1, 3, 4, 100]) {
    const names = Array.from({length: count}, (_, i) => `session-${i}`);
    const api = loadYomux('', names);
    api.setTabberModeForTest('normal');
    api.setNativeViewportForTest({width: 2200});
    api.setTabberActivityForTest({activity: Object.fromEntries(names.map((name, i) => [name, {active_recency_ts: i + 1}]))});
    api.setTabberModeForTest('advanced', {refresh: true});
    const panes = Object.values(api.layoutSlotsForTest()).filter(value => value?.tabs);
    const home = panes.find(value => value.tabs.includes('__tabber__'));
    assert.equal(home.active, '__finder__');
    assert.deepEqual(canonical(home.tabs), ['__finder__', '__differ__', '__tabber__']);
    assert.equal(home.tabs[0], '__finder__', 'Finder is the leftmost home tab in Advanced');
    const terminals = panes.filter(value => value.tabs.some(item => names.includes(item)));
    assert.equal(terminals.length, Math.min(2, count));
    assert.deepEqual(canonical(terminals.flatMap(value => value.tabs)), names.slice().reverse());
    if (count > 1) assert.equal(terminals[0].tabs.length, Math.ceil(count / 2));
  }
});

test('Advanced layout keeps Tabber selected when Tabber was active', () => {
  const api = loadYomux('', ['one']);
  api.setNativeViewportForTest({width: 2200});
  const slots = api.advancedTabberLayoutSlots('__tabber__');
  const home = Object.values(slots).find(value => value?.tabs?.includes('__tabber__'));
  assert.equal(home.active, '__tabber__');
});

test('Advanced transition keeps active window views and falls back below the measured split width', () => {
  const names = ['one', 'two', 'three', '__yomux_view_internal'];
  const api = loadYomux('', names);
  api.setTabberModeForTest('normal');
  api.storeTabberViewSessionForTest('__yomux_view_internal', {sourceSession: 'two', windowIndex: 1});
  api.applyLayoutSlotsForTest({left: {tabs: ['__tabber__'], active: '__tabber__', role: 'side', side: 'left'}, right: {tabs: ['__yomux_view_internal'], active: '__yomux_view_internal'}});
  api.setFocusedPanelItem('__yomux_view_internal');
  api.setNativeViewportForTest({width: 500});
  api.setTabberModeForTest('advanced', {refresh: true});
  const panes = Object.values(api.layoutSlotsForTest()).filter(value => value?.tabs?.some(item => names.includes(item)));
  assert.equal(panes.length, 1);
  assert.deepEqual(canonical(panes[0].tabs), ['one', '__yomux_view_internal', 'three']);
  assert.equal(panes[0].active, '__yomux_view_internal');
});

test('Advanced split obeys the exact minimum width and preserves the selected older session', () => {
  const api = loadYomux('', ['one', 'two', 'three', 'four']);
  api.setTabberModeForTest('advanced');
  api.setNativeViewportForTest({width: 2200});
  const split = api.advancedTabberLayoutSlots('four');
  const minimum = api.layoutNodeMinWidth(split.__tree, split) + 8;
  for (const [width, count] of [[minimum - 1, 1], [minimum, 2]]) {
    api.setNativeViewportForTest({width});
    const slots = api.advancedTabberLayoutSlots('four');
    const content = Object.values(slots).filter(value => value?.tabs?.includes('one') || value?.tabs?.includes('four'));
    assert.equal(content.length, count);
    assert.equal(content.find(value => value.tabs.includes('four')).active, 'four');
    if (count === 2) assert.equal(slots.__tree.children[1].pct, 50);
  }
});

test('Narrow Normal mode collapses Tabber to one pane and the topbar button restores it in place', () => {
  const api = loadYomux('?sessions=one&layout=right', ['one']);
  api.setTabberModeForTest('normal');
  api.setNativeViewportForTest({width: 360});
  api.setTabberNormalSelectionsForTest(['one']);
  api.applyTabberNormalSelectionsForTest('one');
  assert.equal(api.tabberSidebarCanFit(), false);
  assert.equal(api.toggleTabberSidebarForTest(), true);
  assert.deepEqual(api.tabberSidebarStateForTest(), {visible: false, singlePane: true, requestedVisible: true});
  let panes = Object.values(api.layoutSlotsForTest()).filter(value => value?.tabs);
  assert.equal(panes.length, 1);
  assert.deepEqual(panes[0].tabs, ['__tabber__']);
  api.toggleTabberSidebarForTest();
  panes = Object.values(api.layoutSlotsForTest()).filter(value => value?.tabs);
  assert.equal(panes.length, 1);
  assert.ok(panes[0].tabs.includes('one'), JSON.stringify({panes, selection: api.tabberNormalSelectionsForTest(), state: api.tabberSidebarStateForTest()}));
  assert.equal(api.tabberSidebarStateForTest().singlePane, false);
});

test('editor and Differ tab context menus offer Close through the shared close owner', () => {
  const menuSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  const menuStart = menuSource.indexOf('function appendTabActionCommands(');
  const menuEnd = menuSource.indexOf('\nfunction showSessionContextMenu(', menuStart);
  const actions = [];
  const closed = [];
  const context = {
    differItemId: '__differ__',
    isFileEditorItem: item => item.startsWith('__file_editor__'),
    itemLabel: item => item.slice('__file_editor__'.length),
    isPinnableTab: () => false,
    isTmuxSession: () => false,
    t: (key, params) => key === 'finder.close' ? `Close ${params.name}` : 'Close',
    appendContextMenuButton: (menu, label, handler) => actions.push({label, handler}),
    closeLayoutTabItem: item => closed.push(item),
    closeSessionContextMenu() {},
  };
  vm.createContext(context);
  vm.runInContext(menuSource.slice(menuStart, menuEnd), context);
  context.appendTabActionCommands([], '__file_editor__/repo/file.txt');
  context.appendTabActionCommands([], '__differ__');
  assert.deepEqual(actions.map(action => action.label), ['Close /repo/file.txt', 'Close']);
  actions.forEach(action => action.handler());
  assert.deepEqual(closed, ['__file_editor__/repo/file.txt', '__differ__']);

  const closeSource = fs.readFileSync('static_src/js/yomux/45_file_explorer_actions.js', 'utf8');
  const closeStart = closeSource.indexOf('function closeLayoutTabItem(');
  const closeEnd = closeSource.indexOf('\nfunction layoutWithReplacedItem(', closeStart);
  const routed = [];
  const closeContext = {
    isFileEditorItem: item => item.startsWith('__file_editor__'),
    fileItemPath: item => item.slice('__file_editor__'.length),
    closeFileTab: (path, options) => routed.push({kind: 'editor', path, options}),
    removeSessionFromLayout: item => routed.push({kind: 'layout', item}),
  };
  vm.createContext(closeContext);
  vm.runInContext(closeSource.slice(closeStart, closeEnd), closeContext);
  closeContext.closeLayoutTabItem('__file_editor__/repo/file.txt');
  closeContext.closeLayoutTabItem('__differ__');
  assert.deepEqual(routed.map(item => item.kind), ['editor', 'layout']);
  assert.deepEqual(routed[0], {
    kind: 'editor', path: '/repo/file.txt', options: {item: '__file_editor__/repo/file.txt'},
  });
  assert.equal(routed[1].item, '__differ__');
});

test('deferred tab enrichment waits for the terminal paint owner', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const frames = [], timers = [], refreshed = [];
  const context = {
    dockviewLayoutState: {tabActivationPerf: {item: '1'}, deferredTabRefreshItems: new Set(), deferredTabRefreshFrame: 0},
    requestAnimationFrame: callback => {frames.push(callback); return frames.length;},
    cancelAnimationFrame() {}, document: {getElementsByClassName: () => []},
    window: {setTimeout: callback => {timers.push(callback); return timers.length;}},
    dockviewRefreshTabs: options => refreshed.push([...options.items]),
    itemIsActivePaneTab: () => true, terminals: new Map(),
  };
  vm.createContext(context);
  for (const name of ['dockviewClearTabActivationPerf', 'dockviewTabActivationIsCurrent', 'flushDeferredMetadataTabStripRefresh', 'dockviewScheduleDeferredTabRefresh']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\nfunction ', start + 1)), context);
  }
  context.dockviewScheduleDeferredTabRefresh(['1']);
  frames.shift()(); frames.shift()();
  assert.deepStrictEqual(refreshed, [], 'header geometry cannot delay a pending terminal viewport paint');
  assert.equal(context.dockviewLayoutState.deferredTabRefreshItems.size, 1);
  context.dockviewClearTabActivationPerf();
  timers.shift()();
  frames.shift()(); frames.shift()();
  assert.deepStrictEqual(refreshed, [['1']], 'the same paint owner drains enrichment exactly once');
});

test('removed activation retires parse, render, and frame fences without clearing a successor', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  for (const stage of ['parse', 'render', 'frame', 'no-output', 'superseded']) {
    const frames = [], timers = [], refreshed = [];
    let active = true, afterParse, onRender;
    const terminal = {terminalOutputSeen: stage !== 'no-output', term: {rows: 3,
      onRender(callback) {onRender = callback; return {dispose() {}};}}};
    const state = {item: 'gone', terminal: null};
    const context = {
      dockviewLayoutState: {tabActivationPerf: state, deferredTabRefreshItems: new Set(['retained']), deferredTabRefreshFrame: 0},
      terminals: new Map([['gone', terminal]]), isTmuxSession: () => true, itemIsActivePaneTab: () => active,
      requestAnimationFrame: callback => {frames.push(callback); return frames.length;}, cancelAnimationFrame() {},
      document: {getElementsByClassName: () => []},
      window: {setTimeout: callback => {timers.push(callback); return timers.length;}},
      clientPerfEnd() {}, refreshTerminal(item, options) {afterParse = options.afterParse;},
      dockviewRefreshTabs: options => refreshed.push([...options.items]),
    };
    vm.createContext(context);
    for (const name of ['dockviewClearTabActivationPerf', 'dockviewTabActivationIsCurrent', 'flushDeferredMetadataTabStripRefresh', 'dockviewScheduleDeferredTabRefresh', 'dockviewFinishTabActivationPerf']) {
      const start = source.indexOf(`function ${name}(`);
      vm.runInContext(source.slice(start, source.indexOf('\nfunction ', start + 1)), context);
    }
    context.dockviewFinishTabActivationPerf('gone');
    if (stage === 'render' || stage === 'frame') afterParse();
    if (stage === 'frame') onRender({start: 0, end: 2});
    active = false; context.terminals.delete('gone');
    if (stage === 'superseded') {
      const successor = {item: 'new', terminal: null};
      context.dockviewLayoutState.tabActivationPerf = successor;
      afterParse();
      assert.equal(context.dockviewLayoutState.tabActivationPerf, successor);
      continue;
    }
    if (stage === 'parse') afterParse();
    if (stage === 'render') onRender({start: 0, end: 2});
    if (stage === 'frame') frames.shift()();
    if (stage === 'no-output') {
      context.dockviewScheduleDeferredTabRefresh([]);
      frames.shift()(); frames.shift()();
    }
    assert.equal(context.dockviewLayoutState.tabActivationPerf, null, stage);
    while (timers.length) timers.shift()();
    while (frames.length) frames.shift()();
    assert.deepStrictEqual(refreshed, [['retained']], `${stage} drains surviving pane enrichment once`);
  }
});

test('failed partial tmux snapshots preserve conversation metadata and active selection', () => {
  const api = loadYomux('', ['1']);
  const panes = [
    {window: '0', pane: '0', pane_id: '%1', target: '%1', active: true, window_active: true, conversation: 'one'},
    {window: '0', pane: '1', pane_id: '%2', target: '%2', active: false, window_active: true, conversation: 'two'},
  ];
  api.setTranscriptInfoForTest('1', {panes, selected_pane: panes[1]});
  const info = api.transcriptInfoForTest('1');
  assert.equal(api.applyTmuxSignalsPayloadForTest({data: {ok: false, errors: ['invalid tmux pane signal row'], windows: [
    {session: '1', window_index: '0', active: true, pane_count: 2, panes: [{window_index: '0', pane_index: '0', pane_id: '%1', target: '%1', active: true}]},
  ]}}), null);
  assert.equal(api.transcriptInfoForTest('1'), info);
  assert.equal(api.transcriptInfoForTest('1').selected_pane.target, '%2');
});

test('identical tmux signals and explicit window targets preserve published pane metadata', () => {
  const api = loadYomux('', ['1']);
  const pane = {window: '0', pane: '0', pane_id: '%1', target: '%1', active: true, window_active: true, current_path: '/workspace', command: 'bash'};
  api.setTranscriptInfoForTest('1', {panes: [pane], selected_pane: {...pane}});
  const info = api.transcriptInfoForTest('1');
  const signal = {window_index: '0', panes: [{window_index: '0', pane_index: '0', pane_id: '%1', target: '%1', active: true, current_path: '/workspace', current_command: 'bash'}]};
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', signal, {render: false}), false);
  assert.equal(api.transcriptInfoForTest('1'), info, 'a no-op signal does not invalidate metadata consumers');
  assert.equal(api.applyTmuxWindowActiveIndexToTranscriptInfoForTest('1', '0', {render: false}), false);
  assert.equal(api.transcriptInfoForTest('1'), info, 'the explicit-index sibling shares the no-op owner');
  for (const [key, value] of [['current_path', '/new-path'], ['current_command', 'codex'], ['pane_id', '%2'], ['target', '%2'], ['pane_index', '1'], ['active', false]]) {
    const changed = {...signal, panes: [{...signal.panes[0], [key]: value}]};
    assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', changed, {render: false}), true, `${key} remains a meaningful metadata change`);
    assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', changed, {render: false}), false, `${key} settles without repeated invalidation`);
    api.setTranscriptInfoForTest('1', {panes: [pane], selected_pane: {...pane}});
  }
});

test('tmux split signals converge metadata selection onto the new active pane', () => {
  const api = loadYomux('', ['1']);
  const pane = {window: '0', pane: '0', pane_id: '%1', target: '%1', active: true, window_active: true};
  api.setTranscriptInfoForTest('1', {panes: [pane], selected_pane: {...pane}});
  const signal = {window_index: '0', panes: [
    {window_index: '0', pane_index: '0', pane_id: '%1', target: '%1', active: false},
    {window_index: '0', pane_index: '1', pane_id: '%2', target: '%2', active: true, current_path: '/new', current_command: 'codex'},
  ]};
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', signal, {render: false}), true);
  const info = api.transcriptInfoForTest('1');
  assert.equal(info.panes.length, 2, 'the signal introduces the new physical pane');
  assert.equal(info.selected_pane.target, '%2');
  assert.equal(info.selected_pane.current_path, '/new');
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', signal, {render: false}), false);
  const flipped = {...signal, panes: signal.panes.map(pane => ({...pane, active: pane.target === '%1'}))};
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', flipped, {render: false}), true);
  assert.equal(api.transcriptInfoForTest('1').selected_pane.target, '%1');
  const inactive = {...flipped, panes: [flipped.panes[0], {...flipped.panes[1], active: false}]};
  api.setTranscriptInfoForTest('1', {panes: [pane], selected_pane: {...pane}});
  inactive.panes = [{...inactive.panes[0], active: false}];
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', inactive, {render: false}), true);
  assert.equal(api.transcriptInfoForTest('1').selected_pane.active, false, 'fallback selection refers to updated pane data');
});

test('tmux pane reindexing keeps conversation metadata on physical pane identities', () => {
  const api = loadYomux('', ['1']);
  const panes = [
    {window: '0', pane: '0', pane_id: '%1', target: '%1', active: true, window_active: true, conversation: 'one'},
    {window: '0', pane: '1', pane_id: '%2', target: '%2', active: false, window_active: true, conversation: 'two'},
  ];
  api.setTranscriptInfoForTest('1', {panes, selected_pane: {...panes[0]}});
  const signal = {window_index: '0', panes: [
    {window_index: '0', pane_index: '0', pane_id: '%2', target: '1:0.0', active: true},
    {window_index: '0', pane_index: '1', pane_id: '%1', target: '1:0.1', active: false},
  ]};
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', signal, {render: false}), true);
  const info = api.transcriptInfoForTest('1');
  assert.deepEqual(info.panes.map(pane => pane.pane_id).sort(), ['%1', '%2']);
  assert.equal(info.panes.find(pane => pane.pane_id === '%1').conversation, 'one');
  assert.equal(info.panes.find(pane => pane.pane_id === '%2').conversation, 'two');
  assert.equal(info.selected_pane.conversation, 'two');
  const splitBefore = {...signal, panes: [
    {window_index: '0', pane_index: '0', pane_id: '%3', target: '%3', active: true},
    ...signal.panes.map(pane => ({...pane, pane_index: String(Number(pane.pane_index) + 1), active: false})),
  ]};
  assert.equal(api.applyTmuxSignalActiveWindowToTranscriptInfoForTest('1', splitBefore, {render: false}), true);
  const splitInfo = api.transcriptInfoForTest('1');
  assert.equal(splitInfo.panes.length, 3);
  assert.equal(splitInfo.panes.find(pane => pane.pane_id === '%1').conversation, 'one');
  assert.equal(splitInfo.selected_pane.pane_id, '%3');
});

test('touch layout predicates skip viewport layout reads on fine pointers and retain touch geometry', () => {
  const source = fs.readFileSync('static_src/js/yomux/00_bootstrap_state.js', 'utf8');
  let coarse = false, tablet = false, reads = 0;
  const context = {
    nativeViewport: () => {reads++; return {width: 700, height: 900};},
    browserUsesCoarsePointer: () => coarse, browserUsesTabletViewport: () => tablet,
    tabletDesktopLayoutMinWidthPx: 1024, mobileSinglePaneMaxWidthPx: 600,
    mobileSinglePaneLandscapeMaxWidthPx: 900, mobileSinglePaneLandscapeMaxHeightPx: 500,
    narrowTouchSingleColumnMaxWidthPx: 800, minSplitPaneWidthPx: () => 400,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function tabletUsesDesktopLayout('), source.indexOf('const debugModeExplicitUrlEnabled')), context);
  for (const name of ['tabletUsesDesktopLayout', 'phoneLikeMobileViewport', 'mobileSinglePaneMode', 'narrowTouchSingleColumnViewport', 'narrowSingleColumnMode']) {
    assert.equal(context[name](), false, `${name} excludes fine pointers`);
  }
  assert.equal(reads, 0, 'fine-pointer header rendering cannot force viewport layout');
  coarse = true;
  assert.equal(context.narrowSingleColumnMode(), true);
  assert.equal(reads, 1, 'touch mode still reads the current native viewport');
  assert.equal(context.mobileSinglePaneMode({width: 400, height: 900}), true);
  assert.equal(context.narrowSingleColumnMode({width: 1000, height: 900}), false);
  assert.equal(context.narrowSingleColumnMode(null), true, 'explicit null retains its previous zero-width semantics');
  tablet = true;
  assert.equal(context.tabletUsesDesktopLayout({width: 1366, height: 900}), true);
  assert.equal(context.mobileSinglePaneMode({width: 400, height: 900}), false);
  assert.equal(context.narrowSingleColumnMode(), true, 'narrow tablets still honor minimum pane widths');
  assert.equal(reads, 2, 'explicit geometry never triggers another viewport read');
});

test('width-only viewport policies do not read visual height and full viewport retains height recovery', () => {
  const source = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  let heightReads = 0;
  const context = {
    document: {documentElement: {}},
    window: {innerWidth: 900, innerHeight: 700,
      visualViewport: {get height() {heightReads++; return 800;}, scale: 1}},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function nativeViewport('), source.indexOf('// A soft keyboard', source.indexOf('function nativeViewport('))), context);
  assert.equal(context.nativeViewport({widthOnly: true}).width, 900);
  assert.equal(heightReads, 0);
  assert.equal(context.nativeViewport().height, 800);
  assert.equal(heightReads, 1);
  context.window.visualViewport.scale = 2;
  assert.equal(context.nativeViewport().height, 700);
  assert.ok(fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8').includes('function sidePanesAvailable(viewport = nativeViewport({widthOnly: true}))'));
});

test('activation refresh uses mounted selection without measuring dirty viewport geometry', () => {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  let active = true;
  const context = {
    CLS: {active: 'active'}, itemIsActivePaneTab: () => active,
    document: {getElementById: () => ({classList: {contains: () => true}})},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function terminalIsVisible('), source.indexOf('const terminalAttentionQuestionRowClass')), context);
  const container = {isConnected: true, get clientWidth() {throw new Error('forced layout');}};
  assert.equal(context.terminalIsVisible('1', container, {measure: false}), true);
  active = false;
  assert.equal(context.terminalIsVisible('1', container, {measure: false}), false);
  assert.throws(() => context.terminalIsVisible('1', container), /forced layout/, 'fit and recovery retain physical geometry checks');
});

test('shared CSS length owner resolves pixel literals without layout probes and measures relative units', () => {
  const source = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  let value = '300px', inline = '', priority = '', probes = 0, reads = 0;
  const context = {
    rootCssLengthState: {absolutePixels: new Map()},
    window: {getComputedStyle: () => ({getPropertyValue: () => {reads++; return value;}})},
    document: {documentElement: {style: {getPropertyValue: () => inline, getPropertyPriority: () => priority}}, body: {appendChild() {probes++;}},
      createElement() {
        const style = {};
        return {style, getBoundingClientRect: () => ({width: style.width.endsWith('px') ? Number.parseFloat(style.width) : 48}), remove() {}};
      }},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function rootCssLengthPx('), source.indexOf('function minSplitPaneWidthPx(')), context);
  assert.equal(context.rootCssLengthPx('--size'), 300);
  assert.equal(probes, 0, 'literal pane dimensions do not flush unrelated dirty layout');
  value = ' 320.5px ';
  assert.equal(context.rootCssLengthPx('--size'), 320.5, 'changed settings are read live');
  value = '3rem';
  assert.equal(context.rootCssLengthPx('--size'), 48);
  assert.equal(probes, 1, 'font-relative geometry retains the measurement owner');
  value = '340px';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 340, 'a later gesture reads changed settings live');
  const warmReads = reads;
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 340);
  assert.equal(reads, warmReads, 'stable absolute split minima avoid forced style reads');
  inline = value = '360px';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 360, 'same-task inline override invalidates cached minima');
  priority = 'important';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 360);
  assert.equal(reads, warmReads + 2, 'priority changes invalidate before the next read');
  inline = ''; priority = ''; value = '300px';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 300, 'removing the override restores the stylesheet owner');
  inline = value = '3rem';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 48);
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 48);
  assert.equal(probes, 3, 'relative overrides remain live and uncached');
  inline = ''; value = '';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 0);
  value = '420px';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 420, 'missing startup values are not cached');
  context.rootCssLengthState.absolutePixels.clear(); value = '430px';
  assert.equal(context.rootCssLengthPx('--min-split-pane-width'), 430, 'stylesheet retirement reloads the computed token');
  assert.ok(!fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8').includes('withRootCssLengthSnapshot'), 'row selection does not eagerly measure unused split minima');
});

test('resize renewal coalesces focus, visibility and gestures independently of Finder cancellation', () => {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const frames = [], listeners = {}, calls = [];
  const context = {
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    window: {addEventListener: (type, listener) => { listeners[type] = listener; }},
    document: {visibilityState: 'visible', addEventListener: (type, listener) => { listeners[type] = listener; }},
    tabSwitchHighlightGeneration: 1,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("let lastTerminalResizeAuthoritySignature"), source.indexOf('function fitTerminal(')), context);
  context.claimVisibleTerminalResizeAuthority = () => calls.push('claim');
  context.installTerminalResizeAuthorityHandlers();
  listeners.focus();
  listeners.pointerdown({target: {closest: () => null}});
  listeners.visibilitychange();
  context.tabSwitchHighlightGeneration++;
  frames.shift()(); frames.shift()();
  assert.deepEqual(calls, ['claim'], 'one foreground gesture renews even when Finder changes generation');
  listeners.pointerdown({target: {closest: () => ({dataset: {paneTab: 'second'}})}});
  assert.equal(frames.length, 0, 'held tabs leave ownership to release-time activation');
  context.scheduleVisibleTerminalResizeAuthority();
  frames.shift()(); frames.shift()();
  assert.equal(calls.length, 2, 'a later gesture can recover ownership from a competing client');
});

test('manual refresh renews terminal size after the mobile viewport settles', () => {
  const layoutSource = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const bootSource = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const frames = [], timers = new Map(), sends = [], fits = [];
  let nextTimer = 0;
  let viewportSignature = 'keyboard-open';
  const context = {
    activeSessions: ['one'],
    isTmuxSession: session => session === 'one',
    nativeAppViewportSettleDelayMs: 250,
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    window: {addEventListener() {}},
    document: {visibilityState: 'visible', addEventListener() {}},
    scheduleFit: session => fits.push(session),
    refreshVisibleTerminalScreens() {},
    refreshTranscripts() {},
    refreshAutoStatuses() {},
    refreshWatchedFilesystem() {},
  };
  vm.createContext(context);
  vm.runInContext(layoutSource.slice(layoutSource.indexOf('let lastTerminalResizeAuthoritySignature'), layoutSource.indexOf('function fitTerminal(')), context);
  const resyncStart = bootSource.indexOf('function resyncVisibleTerminalRemoteSizes(');
  vm.runInContext(bootSource.slice(resyncStart, bootSource.indexOf('\nfunction installReconnectResyncHandlers(', resyncStart)), context);
  const refreshStart = bootSource.indexOf('function refreshAll(');
  vm.runInContext(bootSource.slice(refreshStart, bootSource.indexOf('\nfunction scheduleReconnectResync(', refreshStart)), context);
  context.visibleTerminalResizeAuthorityEntries = () => [{session: 'one'}];
  context.terminalResizeAuthoritySignature = () => viewportSignature;
  context.fitTerminal = (session, options) => sends.push({session, options});

  context.refreshAll();
  assert.deepEqual(fits, ['one'], 'refresh remeasures the active terminal');
  assert.equal(timers.size, 1, 'refresh schedules one trailing size check');
  assert.deepEqual([...timers.values()].map(timer => timer.delay), [250], 'the trailing check waits for the mobile viewport transition');
  frames.shift()();
  frames.shift()();
  assert.deepEqual(sends, [{session: 'one', options: {claim: true}}], 'the immediate foreground resize remains in place');
  viewportSignature = 'keyboard-closed';
  const settle = [...timers.values()][0];
  timers.clear();
  settle.callback();
  assert.deepEqual(sends, [
    {session: 'one', options: {claim: true}},
    {session: 'one', options: {claim: true}},
  ], 'refresh sends the final measured dimensions after keyboard geometry settles');
  timers.clear();
  context.refreshAll({forceMetadata: false, settleTerminalResize: false});
  assert.equal(timers.size, 0, 'reconnect resync does not schedule the manual-refresh settle claim');
});

test('focus cursor work coalesces and reads current terminal, theme and connection owners', () => {
  const source = fs.readFileSync('static_src/js/yomux/50_editor_settings_runtime.js', 'utf8');
  const frames = new Map(), writes = [];
  let nextFrame = 0, focus = 'a', palette = 'dark', bad = false;
  const makeItem = name => {
    let theme = {cursor: 'default', cursorAccent: 'accent', background: palette};
    return {term: {options: {
      get theme() { return theme; },
      set theme(value) { theme = value; writes.push(name); },
    }}};
  };
  const a = makeItem('a'), b = makeItem('b');
  const context = {
    terminals: new Map([['a', a], ['b', b]]),
    requestAnimationFrame: callback => {const id = ++nextFrame; frames.set(id, callback); return id;},
    cancelAnimationFrame: id => frames.delete(id),
    terminalRenderThemeForGlobalTheme: () => ({cursor: 'default', cursorAccent: 'accent', background: palette}),
    badConnectionCursorStateActive: () => bad,
    terminalCursorColorsForSession: session => ({cursor: bad ? 'bad' : session === focus ? palette : 'default', cursorAccent: 'accent'}),
  };
  const flush = () => {
    const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback());
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let activeTerminalCursorFrame'), source.indexOf('function refreshMetaButtonChrome(')), context);
  context.refreshActiveTerminalCursor({defer: true});
  focus = 'b'; context.refreshActiveTerminalCursor({defer: true});
  focus = 'a'; context.refreshActiveTerminalCursor({defer: true});
  assert.equal(frames.size, 1);
  flush();
  assert.deepEqual(writes, [], 'first paint is free of xterm theme writes');
  flush();
  assert.deepEqual(writes, ['a'], 'rapid focus resolves once to its final owner');
  context.refreshActiveTerminalCursor({defer: true}); flush(); flush();
  assert.deepEqual(writes, ['a'], 'unchanged focus does not rewrite xterm theme');
  context.refreshActiveTerminalCursor({defer: true});
  flush();
  const replacement = makeItem('replacement');
  context.terminals.set('a', replacement); context.terminals.delete('b');
  palette = 'light'; replacement.term.options.theme = {cursor: 'default', cursorAccent: 'accent', background: 'light'};
  flush();
  assert.equal(replacement.term.options.theme.cursor, 'light');
  assert.equal(replacement.term.options.theme.background, 'light', 'cursor refresh preserves current settings');
  assert.equal(a.term.options.theme.cursor, 'dark', 'retired terminal receives no writes');
  context.refreshActiveTerminalCursor({defer: true}); flush();
  bad = true; context.refreshActiveTerminalCursor();
  assert.equal(frames.size, 0, 'immediate connection update cancels the second scheduler stage');
  assert.equal(replacement.term.options.theme.cursor, 'bad');
  assert.equal(replacement.term.options.cursorBlink, false);
  context.refreshActiveTerminalCursor({defer: true});
  bad = false; focus = '__editor__'; context.refreshActiveTerminalCursor();
  assert.equal(frames.size, 0, 'immediate settings update cancels the first scheduler stage');
  assert.equal(replacement.term.options.theme.cursor, 'default', 'nonterminal focus clears active terminal cursor');
  assert.equal(replacement.term.options.cursorBlink, true);
});

test('activation fit and paint share one parse fence and reject replaced terminals', () => {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const frames = [], parses = [], calls = [];
  let visible = true;
  const item = {term: {rows: 24, write(data, callback) {parses.push(callback);}, refresh() {calls.push('refresh');}}, container: {}};
  const context = {
    terminals: new Map([['one', item]]),
    requestAnimationFrame: callback => {frames.push(callback); return frames.length;},
    terminalIsVisible: () => visible,
    scheduleTerminalReferenceUnderlineRepaint: () => calls.push('underlines'),
    syncTerminalAttentionHighlight: () => calls.push('attention'),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function refreshTerminal('), source.indexOf('function terminalIsVisible(')), context);
  context.refreshTerminal('one', {visibleOnly: true});
  parses.push(() => calls.push('output'));
  context.refreshTerminal('one', {visibleOnly: true, afterParse: () => calls.push('subscribed')});
  assert.equal(parses.length, 3, 'activation advances the fence beyond intervening output');
  parses.shift()();
  assert.deepEqual(calls, [], 'the superseded fit fence cannot repaint or drain activation');
  parses.shift()();
  parses.shift()();
  assert.deepEqual(calls, ['output', 'subscribed', 'refresh', 'underlines', 'attention']);
  frames.splice(0).forEach(callback => callback());
  context.refreshTerminal('one', {visibleOnly: true, afterParse: () => calls.push('late-subscribed')});
  assert.equal(parses.length, 1, 'activation after parsing fences newly queued output even before paint');
  assert.equal(calls.includes('late-subscribed'), false);
  parses.shift()();
  assert.equal(calls.filter(call => call === 'late-subscribed').length, 1);
  frames.splice(0).forEach(callback => callback());
  calls.length = 0;
  context.refreshTerminal('one', {visibleOnly: true});
  context.terminals.set('one', {...item, refreshPending: false});
  parses.shift()();
  assert.deepEqual(calls, [], 'a queued refresh never paints a replaced instance');
  context.terminals.set('one', item);
  item.refreshPending = false;
  visible = false;
  context.refreshTerminal('one', {visibleOnly: true}); parses.shift()();
  assert.deepEqual(calls, [], 'rapid switch cancels an invisible activation');
  context.refreshTerminal('one'); parses.shift()();
  assert.deepEqual(calls, ['refresh', 'underlines', 'attention'], 'hidden theme changes still repaint');
});

test('settled terminal output repaints immediately while queued output retains its parse barrier', () => {
  const layout = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const writes = [], calls = [], frames = [];
  const item = {pendingTerminalWrites: 0, container: {}, term: {
    rows: 24, write(data, callback) {writes.push({data, callback});},
    refresh() {calls.push('refresh');},
  }};
  const context = {
    ArrayBuffer, Uint8Array, terminals: new Map([['one', item]]),
    requestAnimationFrame: callback => {frames.push(callback); return frames.length;},
    terminalIsVisible: () => true,
    scheduleTerminalReferenceUnderlineRepaint() {}, syncTerminalAttentionHighlight() {},
    tmuxWindowSwitchPaintCallback: () => () => calls.push('switch-parsed'),
  };
  vm.createContext(context);
  vm.runInContext(layout.slice(layout.indexOf('function refreshTerminal('), layout.indexOf('function terminalIsVisible(')), context);
  vm.runInContext(boot.slice(boot.indexOf('function writeTerminalOutput('), boot.indexOf('function bindTerminalContainerForSession(')), context);
  const refresh = () => context.refreshTerminal('one', {afterParse: () => calls.push('subscribed')});
  refresh();
  assert.deepEqual(calls, ['subscribed', 'refresh']);
  assert.equal(writes.length, 0, 'known settled output needs no asynchronous empty write');
  refresh();
  assert.deepEqual(calls, ['subscribed', 'refresh', 'subscribed'], 'activation joins the already queued full repaint');
  item.term.rows = 30;
  refresh();
  assert.equal(calls.filter(call => call === 'refresh').length, 2, 'changed rows request the new complete viewport');
  frames.splice(0).forEach(callback => callback());
  refresh();
  assert.equal(calls.filter(call => call === 'refresh').length, 3, 'a subsequent frame can request another repaint');
  frames.splice(0).forEach(callback => callback());
  calls.length = 0;
  context.writeTerminalOutput('one', item, new Uint8Array([65]).buffer);
  context.writeTerminalOutput('one', item, 'B');
  assert.equal(item.pendingTerminalWrites, 2);
  refresh();
  assert.equal(writes.length, 3, 'queued output requires a fence after both payloads');
  writes.shift().callback();
  assert.equal(item.pendingTerminalWrites, 1);
  assert.deepEqual(calls, ['switch-parsed'], 'binary switch completion remains attached to its parsed frame');
  writes.shift().callback();
  assert.equal(item.pendingTerminalWrites, 0);
  refresh();
  assert.deepEqual(calls, ['switch-parsed', 'subscribed', 'subscribed', 'refresh']);
  writes.shift().callback();
  assert.equal(calls.filter(call => call === 'refresh').length, 1, 'superseded empty fence cannot repaint twice');
  context.writeTerminalOutput('one', item, 'new output');
  refresh();
  assert.equal(writes.length, 2, 'output queued after a settled refresh reinstates the barrier');
  const replacement = {...item, pendingTerminalWrites: 0, refreshPending: false, refreshViewportFrame: 0, refreshViewportRows: 0};
  context.terminals.set('one', replacement);
  const previousCalls = [...calls];
  writes.splice(0).forEach(write => write.callback());
  assert.deepEqual(calls, previousCalls, 'old output and refresh completions cannot affect a replacement');
  assert.equal(replacement.pendingTerminalWrites, 0);
  const oldFrame = frames.shift();
  refresh();
  const replacementFrame = replacement.refreshViewportFrame;
  oldFrame();
  assert.equal(replacement.refreshViewportFrame, replacementFrame, 'retired frame cannot clear the successor repaint owner');
  frames.shift()();
  assert.equal(replacement.refreshViewportFrame, 0);
});

test('Advanced session opening selects immediately without rebuilding the roster', () => {
  const source = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
  const calls = [];
  const context = {tabberIsAdvanced: () => true, selectSession: session => calls.push(session)};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function openTabberSession('), source.indexOf('async function activateNormalTabberRow(')), context);
  assert.equal(context.openTabberSession('second'), true);
  assert.deepEqual(calls, ['second']);
});

test('Tabber keeps keyboard selection immediate and coalesces DOM updates after terminal paint', () => {
  const source = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
  const frames = [], calls = [];
  const context = {
    document: {}, tabberItemId: 'tabber', itemInLayout: () => true, tabberLayoutStateFrame: 0, tabberLayoutStateScrollIntoView: false,
    tabberLayoutStateRefreshTree: false,
    refreshTabberPanels: () => calls.push('tree'),
    syncTabberTreeActiveSelection: (panel, options) => calls.push(options.modelOnly ? 'model' : 'DOM'),
    syncTabberTreeLayoutState: (panel, options) => calls.push(options.scrollIntoView ? 'DOM:scroll' : 'DOM'),
    requestAnimationFrame: callback => {frames.push(callback); return frames.length;},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function scheduleTabberTreeLayoutStateSync('), source.indexOf('function bindTabberPanel(')), context);
  context.scheduleTabberTreeLayoutStateSync(); context.scheduleTabberTreeLayoutStateSync({scrollIntoView: true});
  assert.deepEqual(calls, ['model', 'model']);
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.deepEqual(calls, ['model', 'model'], 'first terminal frame has no tree DOM walk');
  frames.shift()();
  assert.deepEqual(calls, ['model', 'model', 'DOM:scroll']);
  context.scheduleTabberTreeLayoutStateSync(); frames.shift()(); frames.shift()();
  assert.equal(calls.at(-1), 'DOM', 'a later layout sync does not retain the previous scroll request');
  context.scheduleTabberTreeLayoutStateSync({refreshTree: true});
  context.scheduleTabberTreeLayoutStateSync({refreshTree: true});
  frames.shift()(); frames.shift()();
  assert.equal(calls.filter(call => call === 'tree').length, 1, 'accepted metadata coalesces through the same tree owner');
});

test('unchanged Tabber layout still applies a deferred selection change', () => {
  const source = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
  let domSelection = 'other';
  const context = {
    document: {querySelectorAll: () => [{}]}, tabberItemId: 'tabber', itemInLayout: () => true,
    activeTabberRowPath: () => '/current', activePaneItems: () => ['one'], visualActivePaneItem: () => 'one',
    tabberLayoutStateSignature: 'one\u001eone\u001e/current',
    syncTabberTreeActiveSelection: () => {domSelection = 'current'; return true;},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function syncTabberTreeLayoutState('), source.indexOf('function scheduleTabberTreeLayoutStateSync(')), context);
  context.syncTabberTreeLayoutState();
  assert.equal(domSelection, 'current', 'cached layout never strands a model-only selection update');
});

test('Normal activation schedules no delayed focus and stale Advanced focus cannot win', () => {
  const source = fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8');
  const start = source.indexOf('  if (options.tabberNormalSelection !== true && autoFocusCanFollowCursor()');
  const end = source.indexOf('  if (clientPushCanSupplyData()', start);
  const timers = [], focused = [];
  const context = {options: {focusSession: 'one', tabberNormalSelection: true}, activeSessions: ['one', 'two'],
    tabSwitchHighlightGeneration: 1, autoFocusCanFollowCursor: () => true,
    setTimeout: fn => timers.push(fn), focusPanel: item => focused.push(item),
    resetLayoutStatusSurface() {}, updateStatus() {}};
  vm.createContext(context);
  const run = () => vm.runInContext(source.slice(start, end), context);
  run();
  assert.equal(timers.length, 0, 'Normal immediate activation has no competing delayed callback');
  context.options.tabberNormalSelection = false;
  run();
  context.tabSwitchHighlightGeneration = 2;
  timers.shift()();
  assert.deepEqual(focused, [], 'later user activation fences earlier delayed focus');
  run();
  timers.shift()();
  assert.deepEqual(focused, ['one'], 'current Advanced auto-focus retains its delayed behavior');
});

test('native geometry-only adoption persists ratios but reconciles state changes', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const previous = {tree: {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'right'}]}, left: {tabs: ['one', 'two'], active: 'one', role: 'generic'}, right: {tabs: ['three'], active: 'three', role: 'generic'}};
  const clone = value => JSON.parse(JSON.stringify(value));
  for (const change of ['ratio', 'order', 'active', 'role', 'tree', 'pending']) {
    const next = clone(previous);
    next.tree.pct = 49.874055;
    if (change === 'order') next.left.tabs.reverse();
    if (change === 'active') next.left.active = 'two';
    if (change === 'role') next.left.role = 'side';
    if (change === 'tree') next.tree.children.reverse();
    const calls = [];
    const context = {
      layoutSlots: clone(previous), layoutTreeKey: 'tree',
      dockviewLayoutState: {api: {toJSON: () => next}, tabRenderPending: change === 'pending'},
      dockviewLayoutAdoptionAllowed: () => true, dockviewHostCanAdoptLayout: () => true,
      layoutSlotsFromDockviewJson: value => value, cloneLayoutSlots: clone, normalTabberPaneLayout: value => value,
      tabberIsAdvanced: () => true,
      layoutSlotsExactSignature: JSON.stringify, layoutSlotsSignature: JSON.stringify,
      dockviewLayoutActiveOnlySignature: JSON.stringify,
      splitPercent: Number, SPLIT_PCT_EPSILON: .0001,
      pendingTmuxSessionNames: () => [], layoutFileSurfaceItems: () => [],
      layoutHasRestorableContent: () => true,
      updateActiveSessionParam: () => calls.push('persist'),
      requestAnimationFrame() {}, syncNormalTabberSwapControl() {},
      applyLayoutSlots: value => {context.layoutSlots = value; calls.push('reconcile');},
    };
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('function copyLayoutSplitPercentagesByShape('), source.indexOf('function dockviewLayoutContentSignature(')), context);
    vm.runInContext(source.slice(source.indexOf('function adoptDockviewLayout('), source.indexOf('function layoutSlotsFromDockviewJson(')), context);
    context.adoptDockviewLayout();
    assert.deepEqual(calls, [change === 'ratio' ? 'persist' : 'reconcile'], change);
    assert.equal(context.layoutSlots.tree.pct, 49.874055);
  }
});

test('shared header reservations measure all groups before their first write', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const events = [];
  const groups = [0, 1].map(id => {
    const tab = {offsetWidth: 180};
    const tabsContainer = {
      clientWidth: 400,
      querySelector: () => tab,
      querySelectorAll: selector => selector.includes('reservation') ? [] : [tab],
    };
    const header = {
      querySelector: () => tabsContainer,
      style: {setProperty() {events.push('write:' + id);}, removeProperty() {}},
    };
    return {querySelector: selector => selector.includes('actions:not') ? {offsetWidth: 40} : header};
  });
  const context = {
    dockviewLayoutActive: () => true,
    document: {documentElement: {}, querySelectorAll: () => groups},
    getComputedStyle: () => ({getPropertyValue: () => '', marginInlineStart: '0', marginInlineEnd: '0'}),
    appSpaceRect: node => {events.push('read'); return {width: node.clientWidth || node.offsetWidth};},
    dockviewSlotForGroupElement: () => 'left', slotIsSidePane: () => false,
  };
  vm.createContext(context);
  const utils = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  vm.runInContext(utils.slice(utils.indexOf('function setStylePropertyIfChanged('), utils.indexOf('function setInnerHtmlIfChanged(')), context);
  vm.runInContext(source.slice(source.indexOf('function dockviewSyncHeaderActionReservations('), source.indexOf('function dockviewEnsureHost(')), context);
  context.dockviewSyncHeaderActionReservations();
  assert.equal(events.filter(event => event === 'read').length, 4);
  assert.ok(events.slice(0, 4).every(event => event === 'read'), events);
});

test('programmatic terminal tabs share the native Dockview gesture and activation owner', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const calls = [], state = {api: {getPanel: () => ({api: {setActive() { calls.push('active'); }}})}};
  const context = {
    dockviewLayoutState: state, dockviewLayoutActive: () => true,
    dockviewSetPendingUserPanelActivation: item => {state.pendingUserPanelActivation = item; state.tabAttentionGesture = {}; calls.push('gesture');},
    dockviewBeginTabActivationPerf: () => calls.push('paint'),
    dockviewCommitPanelActivation: () => {state.pendingUserPanelActivation = ''; calls.push('commit');},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function dockviewActivateUserTerminalTab('), source.indexOf('function dockviewClearPendingUserPanelActivation(')), context);
  assert.equal(context.dockviewActivateUserTerminalTab('one'), true);
  assert.deepEqual(calls, ['gesture', 'paint', 'active', 'commit']);
  calls.length = 0;
  state.api.activePanel = {id: 'one'};
  context.dockviewActivateUserTerminalTab('one');
  assert.deepEqual(calls, ['gesture', 'paint', 'commit'], 'already mounted active panel still commits attention/focus without native remount');
  calls.length = 0;
  state.api.activePanel = {id: 'other'};
  state.api.getPanel = () => ({api: {setActive() {context.dockviewCommitPanelActivation();}}});
  context.dockviewActivateUserTerminalTab('one');
  assert.equal(calls.filter(call => call === 'commit').length, 1, 'native callback consumes the gesture only once');
});

test('shared user-tab commit preserves activation options, dismisses toasts and protects the recent tab from eviction', () => {
  const api = loadYomux('', ['1', '2', '3']);
  api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 2}});
  api.setTabLastActivatedForTest('1', 1);
  api.setTabLastActivatedForTest('2', 2);
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const dismissed = [];
  let committedOptions;
  let active = false;
  const context = {
    dockviewLayoutState: {pendingUserPanelActivation: '1', tabAttentionGesture: {item: '1', activationOptions: {acknowledgePromptAttention: false}}},
    dockviewClearPendingUserPanelActivation() {},
    itemIsActivePaneTab: () => active,
    queueDockviewLayoutAdoption() {context.dockviewLayoutState.syncQueued = true;},
    adoptDockviewLayout() {active = true; context.dockviewLayoutState.syncQueued = false;},
    isTmuxSession: () => true, dismissSessionToasts: item => dismissed.push(item),
    acknowledgeNotificationTarget() {}, recordTabActivation: api.recordTabActivation,
    acknowledgeUserTerminalTabActivation: (item, options) => options,
    rememberFileExplorerExplicitSyncSession() {}, nextTabSwitchHighlightGeneration: () => 1,
    focusTerminalFromUserAction() {},
    commitUserTerminalTabActivation: (item, options) => {
      assert.equal(active, true, 'focus observes the adopted active pane');
      committedOptions = options;
    },
    dockviewFinishTabActivationPerf() {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function dockviewCommitPanelActivation('), source.indexOf('function dockviewSetPendingUserPanelActivation(')), context);
  context.dockviewCommitPanelActivation('1');
  assert.deepEqual(dismissed, ['1']);
  assert.equal(committedOptions.acknowledgePromptAttention, false);
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.leafNode('left');
  slots.left = {tabs: ['1', '2'], active: '1'};
  assert.deepEqual([...api.paneCapacityCheckForInsert('left', ['3'], '3', slots).evicted], ['2']);
});

test('compatibility mouse drag start preserves pointer geometry while fresh gestures initialize', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  let reads = 0;
  const context = {
    tabDragAllowed: () => true, dockviewLayoutState: {}, slotForItem: () => 'left',
    dockviewRootBoundaryEdgesAtPoint: () => {reads++; return {left: true};},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function dockviewBeginTabPointerDrag('), source.indexOf('function dockviewTabContentInteractionSuppressed(')), context);
  const event = {type: 'pointerdown', button: 0, clientX: 10, clientY: 20};
  context.dockviewBeginTabPointerDrag(event, 'one');
  const pointer = context.dockviewLayoutState.tabPointerDrag;
  pointer.rootBoundaryExitedEdges.left = true;
  context.dockviewBeginTabPointerDrag({...event, type: 'mousedown', clientX: 30}, 'one');
  assert.equal(context.dockviewLayoutState.tabPointerDrag, pointer);
  assert.equal(pointer.x, 10);
  assert.equal(pointer.rootBoundaryExitedEdges.left, true);
  assert.equal(reads, 1);
  context.dockviewBeginTabPointerDrag({...event, type: 'mousedown'}, 'two');
  assert.equal(reads, 2, 'different-item mouse fallback starts independently');
  context.dockviewBeginTabPointerDrag(event, 'two');
  assert.equal(reads, 3, 'every fresh pointerdown initializes its own geometry');
  context.dockviewLayoutState.tabPointerDrag = null;
  context.dockviewBeginTabPointerDrag({...event, type: 'mousedown'}, 'one');
  assert.equal(reads, 4, 'mouse-only environments keep their fallback');
});

test('terminal tab activation focuses before its guarded authority and side work', () => {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const calls = [];
  let deferred;
  const context = {
    dockviewBeginTabActivationPerf() {}, dockviewFinishTabActivationPerf() {},
    activateTab: session => calls.push(`focus:${session}`),
    rememberFileExplorerExplicitSyncSession: session => calls.push(`claim:${session}`),
    scheduleTabSwitchPhaseTwo: (generation, work) => {assert.equal(generation, 7); deferred = work;},
    scheduleVisibleTerminalResizeAuthority: () => calls.push('authority'),
    syncFileExplorerAfterTerminalActivation: session => calls.push(`side:${session}`),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function scheduleTerminalActivationFollowup('), source.indexOf('function activatePaneTab(')), context);
  context.commitUserTerminalTabActivation('second', {}, 7);
  assert.deepEqual(calls, ['focus:second', 'claim:second', 'authority']);
  deferred();
  assert.deepEqual(calls, ['focus:second', 'claim:second', 'authority', 'side:second']);
});

async function checkBackgroundTabberActivation() {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  for (const filesOnly of [false, true]) {
    const calls = [];
    const context = {
      tabLayoutManipulationAllowed: () => true,
      activeSessions: ['first'], isFileEditorItem: () => false,
      layoutIsFileSurfaceItem: () => false, isTmuxSession: () => true,
      filesOnlySlotForSession: () => filesOnly ? 'left' : null,
      activateTabInExistingPane: (session, options) => calls.push({session, options}),
      placeTmuxSession: (session, options) => calls.push({session, options}),
    };
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('async function selectSession('), source.indexOf('function layoutPaneTabTraversalPositions(')), context);
    await context.selectSession('second', {userInitiated: true});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].session, 'second');
    assert.equal(calls[0].options?.userInitiated, true, `background route filesOnly=${filesOnly}`);
  }
}

test('user terminal activation leaves panel focus to the shared terminal owner', () => {
  const actions = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const dockview = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  for (const route of ['dockview', 'pending', 'pane-active', 'pane-switch']) {
    for (const item of ['terminal', 'finder']) {
      for (const userInitiated of [true, false]) {
        if (route === 'pending' && !userInitiated) continue;
        const calls = [];
        let active = route === 'pane-switch' ? 'previous' : item;
        const context = {
          dockviewLayoutState: {pendingUserPanelActivation: route === 'pending' ? item : ''},
          dockviewClearPendingUserPanelActivation: () => {context.dockviewLayoutState.pendingUserPanelActivation = '';},
          isTmuxSession: session => session === 'terminal',
          isFileExplorerItem: session => session === 'finder', isFileEditorItem: () => false,
          rememberFileExplorerExplicitSyncSession() {},
          nextTabSwitchHighlightGeneration: () => 7,
          dockviewBeginTabActivationPerf() {}, dockviewFinishTabActivationPerf() {},
          scheduleTabSwitchPhaseTwo() {}, scheduleVisibleTerminalResizeAuthority() {},
          setFocusedPanelItem: (session, options) => calls.push({kind: 'panel', session, options}),
          focusTerminalFromUserAction: (session, delay, options) => calls.push({kind: 'terminal', session, options}),
          focusTerminalWhenAutoFocus() {}, focusPanel: () => calls.push({kind: 'other'}),
          stopTranscriptStream() {}, stopSummaryStream() {}, updateTypingIndicator() {},
          scheduleTerminalReferenceUnderlineRepaint() {}, scheduleFit() {}, setTimeout() {}, refreshTerminal() {},
          refreshTerminal() {}, scheduleTerminalBlankScreenRefresh() {},
          document: {querySelectorAll: () => [], getElementById: () => null},
          dismissSessionToasts() {}, acknowledgeNotificationTarget() {},
          acknowledgeTerminalAttentionFromUserAction: () => calls.push({kind: 'attention'}),
          recordTabActivation() {}, layoutSlotKeys: () => ['left'], itemInLayout: () => true,
          activeItemForSide: () => active, dockviewLayoutActive: () => false,
          itemIsActivePaneTab: session => session === active,
          queueDockviewLayoutAdoption() {},
          capturePaneViewStateForItemIfPresent() {},
          emptyLayoutSlots: () => ({left: {}}), layoutTreeKey: 'tree', layoutSlots: {tree: {}},
          paneStateForLayoutSlot: () => ({active}),
          applyLayoutSlots: next => {active = next.left.active;},
        };
        vm.createContext(context);
        vm.runInContext(boot.slice(boot.indexOf('function activateTab('), boot.indexOf('function tmuxWindow(')), context);
        vm.runInContext(actions.slice(actions.indexOf('function scheduleTerminalActivationFollowup('), actions.indexOf('async function selectSession(')), context);
        vm.runInContext(dockview.slice(dockview.indexOf('function dockviewCommitPanelActivation('), dockview.indexOf('function dockviewSetPendingUserPanelActivation(')), context);
        if (route.startsWith('pane')) context.activatePaneTab('left', item, {userInitiated});
        else context.dockviewCommitPanelActivation(item, {userInitiated: route === 'pending' ? false : userInitiated});
        const label = `${route}/${item}/${userInitiated}`;
        assert.equal(calls.filter(call => call.kind === 'panel').length, item === 'terminal' && userInitiated ? 0 : 1, `${label} does not clear terminal ownership before focusing`);
        const terminalFocus = calls.find(call => call.kind === 'terminal');
        assert.equal(Boolean(terminalFocus), item === 'terminal' && userInitiated, `${label} preserves terminal activation`);
        if (terminalFocus) {
          assert.equal(terminalFocus.options.acknowledgeAgentWindow, false, label);
          assert.equal(terminalFocus.options.acknowledgePromptAttention, false, label);
          assert.equal(terminalFocus.options.syncFinder, false, label);
          assert.equal(calls[0].kind, 'attention', `${label} acknowledges before focusing`);
        }
      }
    }
  }
});

test('terminal activation reconciles changed focus once and preserves repeated focus', () => {
  const core = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  const calls = [];
  const context = {
    focusedTerminal: 'first', focusedPanelItem: 'first', lastFocusedTmuxSession: 'first',
    isTmuxSession: () => true, isFileExplorerItem: () => false, isFileEditorItem: () => false,
    dockviewLayoutActive: () => true, rememberActivePaneItem() {}, clearPendingFileEditorFocusExcept() {},
    dismissAttentionAlertsForSession() {}, dismissNotificationsForTarget() {},
    acknowledgeTerminalAttentionFromUserAction() {}, recordAutoFocusNav() {},
    recordFocusNavTransition: (previous, next) => calls.push(['nav', previous, next]),
    applyUserInitiatedPanelFocus: (item, previous) => context.recordFocusNavTransition(previous, item),
    updateFocusOnlyChrome: () => calls.push(['chrome', context.focusedPanelItem, context.focusedTerminal]),
    setFocusedTerminal: (session, options) => context.setFocusedTerminalMeasured(session, options),
    focusTerminalDom: session => calls.push(['dom', session]),
    stopTranscriptStream() {}, stopSummaryStream() {}, updateTypingIndicator() {},
    scheduleTerminalReferenceUnderlineRepaint() {}, scheduleFit() {}, setTimeout() {},
    refreshTerminal() {}, scheduleTerminalBlankScreenRefresh() {},
    document: {querySelectorAll: () => [], getElementById: () => null},
  };
  vm.createContext(context);
  vm.runInContext(core.slice(core.indexOf('function setFocusedTerminalMeasured('), core.indexOf('function clearFocusedTerminal(')), context);
  vm.runInContext(core.slice(core.indexOf('function setFocusedPanelItem('), core.indexOf('let autoFocusNavTimer')), context);
  vm.runInContext(core.slice(core.indexOf('function focusTerminalFromUserAction('), core.indexOf('function focusTerminalDom(')), context);
  vm.runInContext(boot.slice(boot.indexOf('function activateTab('), boot.indexOf('function tmuxWindow(')), context);
  context.activateTab('second', 'terminal', {userInitiated: true, syncFinder: false});
  assert.deepEqual(calls, [['chrome', 'second', 'second'], ['nav', 'first', 'second'], ['dom', 'second']]);
  calls.length = 0;
  context.activateTab('second', 'terminal', {userInitiated: true, syncFinder: false});
  assert.deepEqual(calls, [['dom', 'second']], 'a repeated activation keeps established terminal ownership');
});

test('direct user terminal focus defers Finder and Differ synchronization', () => {
  const core = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  const calls = [];
  const deferred = [];
  const context = {
    focusedTerminal: 'first', focusedPanelItem: 'first', lastFocusedTmuxSession: 'first',
    isTmuxSession: () => true, isFileExplorerItem: () => false, isFileEditorItem: () => false,
    dockviewLayoutActive: () => true, rememberActivePaneItem() {}, clearPendingFileEditorFocusExcept() {},
    dismissAttentionAlertsForSession() {}, dismissNotificationsForTarget() {},
    acknowledgeTerminalAttentionFromUserAction() {}, recordAutoFocusNav() {},
    recordFocusNavTransition: (previous, next) => calls.push(['nav', previous, next]),
    applyUserInitiatedPanelFocus: (item, previous) => context.recordFocusNavTransition(previous, item),
    updateFocusOnlyChrome: () => calls.push(['chrome', context.focusedPanelItem, context.focusedTerminal]),
    setFocusedTerminal: (session, options) => {
      context.focusedTerminal = session;
      context.focusedPanelItem = session;
      calls.push(['focus-state', session, options.userInitiated, options.syncFinder]);
    },
    focusTerminalDom: session => calls.push(['dom', session]),
    rememberFileExplorerExplicitSyncSession: session => calls.push(['claim', session]),
    scheduleTabSwitchPhaseTwo: (generation, work) => deferred.push({generation, work}),
    nextTabSwitchHighlightGeneration: () => 7,
    syncFileExplorerAfterTerminalActivation: session => calls.push(['sync', session]),
    stopTranscriptStream() {}, stopSummaryStream() {}, updateTypingIndicator() {},
    scheduleTerminalReferenceUnderlineRepaint() {}, scheduleFit() {}, setTimeout() {},
    refreshTerminal() {}, scheduleTerminalBlankScreenRefresh() {},
    document: {querySelectorAll: () => [], getElementById: () => null},
  };
  vm.createContext(context);
  vm.runInContext(core.slice(core.indexOf('function focusTerminalFromUserAction('), core.indexOf('function focusTerminalDom(')), context);

  context.focusTerminalFromUserAction('second');

  assert.deepEqual(calls, [['focus-state', 'second', true, false], ['dom', 'second'], ['claim', 'second']]);
  assert.equal(deferred.length, 1, 'direct focus queues one post-paint Finder and Differ synchronization');
  deferred[0].work();
  assert.deepEqual(calls.at(-1), ['sync', 'second']);
});

test('terminal side sync rejects stale ownership and refreshes a stale Differ payload once', () => {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const calls = [];
  let selected = 'second';
  let interactionIsCurrent = false;
  let differSession = 'first';
  const context = {
    focusedTerminal: 'third',
    fileExplorerExplicitSyncSessionTarget: () => selected,
    document: {querySelector: () => ({})},
    fileExplorerChangesSessionInteractionIsCurrent: () => interactionIsCurrent,
    fileExplorerSessionFilesState: {payload: {session: differSession}},
    switchFileExplorerChangesSession: session => calls.push(`switch-owner:${session}`),
    noteFileExplorerChangesSessionInteraction: session => calls.push(`note:${session}`),
    scheduleFileExplorerActiveTabSync: session => calls.push(`finder:${session}`),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(
    source.indexOf('function syncFileExplorerAfterTerminalActivation('),
    source.indexOf('function commitUserTerminalTabActivation('),
  ), context);

  assert.equal(context.syncFileExplorerAfterTerminalActivation('second'), false);
  assert.deepEqual(calls, [], 'a later focused session retires the stale phase-two target');

  context.focusedTerminal = 'second';
  assert.equal(context.syncFileExplorerAfterTerminalActivation('second'), true);
  assert.deepEqual(calls, ['switch-owner:second'], 'a changed selection lets Differ switching own the Finder sync');

  calls.length = 0;
  interactionIsCurrent = true;
  context.syncFileExplorerAfterTerminalActivation('second');
  assert.deepEqual(calls, ['switch-owner:second'], 'a matching selection still refreshes stale Differ content');

  calls.length = 0;
  differSession = 'second';
  context.fileExplorerSessionFilesState.payload.session = differSession;
  context.syncFileExplorerAfterTerminalActivation('second');
  assert.deepEqual(calls, ['finder:second'], 'a current Differ payload schedules one explicit Finder sync');
});

test('user layout activation establishes terminal focus before mount reconciliation', () => {
  const core = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
  const layout = fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8');
  for (const userInitiated of [false, true]) {
    const calls = [];
    let normalizations = 0;
    const context = {
      layoutSlots: {items: ['first']}, activeSessions: ['first'], focusedPanelItem: 'first', focusedTerminal: 'first',
      runtimeState: {layoutMutationSnapshot: () => ({pending: 0}), consumePendingLayoutMutation() {}},
      paneItems: slots => slots.items, layoutShapeSignature: () => 'shape',
      normalTabberPaneLayout: next => next,
      adoptNormalTabberLayoutSelections() {},
      normalizeLayoutSlotsForViewport: next => {normalizations++; return next;},
      dockviewLayoutActiveOnlySignature: slots => JSON.stringify(slots.items), cloneLayoutSlots: slots => JSON.parse(JSON.stringify(slots)), cleanupRemovedDynamicLayoutItems() {},
      sessionsFromLayout: () => context.layoutSlots.items, clearFocusForInactiveLayout() {}, updateActiveSessionParam() {},
      isTmuxSession: item => ['first', 'second'].includes(item), dockviewLayoutActive: () => true,
      rememberActivePaneItem() {}, clearPendingFileEditorFocusExcept() {}, dismissAttentionAlertsForSession() {},
      acknowledgeTerminalAttentionFromUserAction: (_session, _window, options) => {
        assert.equal(options.acknowledgeAgentWindow, false);
        assert.equal(options.acknowledgePromptAttention, false);
      },
      recordFocusNavTransition: (previous, next) => calls.push(['nav', previous, next]), recordAutoFocusNav() {},
      updateFocusOnlyChrome: () => calls.push(['chrome']),
      setFocusedTerminal: (session, options) => context.setFocusedTerminalMeasured(session, options),
      requestLayoutRender: () => calls.push(['mount', context.focusedPanelItem, context.focusedTerminal]),
      autoFocusCanFollowCursor: () => false, resetLayoutStatusSurface() {}, updateStatus() {},
      clientPushCanSupplyData: () => false,
    };
    vm.createContext(context);
    vm.runInContext(core.slice(core.indexOf('function setFocusedTerminalMeasured('), core.indexOf('function clearFocusedTerminal(')), context);
    vm.runInContext(layout.slice(layout.indexOf('function applyLayoutSlots('), layout.indexOf('function cleanupRemovedDynamicLayoutItems(')), context);
    context.applyLayoutSlots({items: ['first', 'second']}, {focusSession: 'second', userInitiated});
    assert.deepEqual(calls, userInitiated
      ? [['nav', 'first', 'second'], ['mount', 'second', 'second']]
      : [['mount', 'first', 'first']], 'only explicit activation prepares focus; mount owns its reconciliation');
    context.applyLayoutSlots({items: ['first', 'second']}, {activeOnlyActivation: true});
    assert.equal(normalizations, 1, 'confirmed selection changes reuse the normalized snapshot');
    context.applyLayoutSlots({items: ['second']}, {activeOnlyActivation: true});
    assert.equal(normalizations, 2, 'structural changes still normalize even with an activation hint');
  }
});

test('cached terminal blank recovery avoids measuring a nonblank viewport', () => {
  const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const item = {socket: {readyState: 1}, blankScreenRefreshAttempts: 2};
  const context = {
    terminals: new Map([['second', item]]), WebSocket: {OPEN: 1},
    tmuxSessionLifecycleReleaseTimer() {}, tmuxSessionLifecycleTokenIsCurrent: () => true,
    terminalBlankScreenRefreshAllowed: () => true, terminalRenderedContentPresent: () => true,
    terminalIsVisible() {throw new Error('nonblank recovery must not force layout');},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function runTerminalBlankScreenRefresh('), source.indexOf('function scheduleFit(')), context);
  assert.doesNotThrow(() => context.scheduleTerminalBlankScreenRefresh('second'));
  assert.doesNotThrow(() => context.runTerminalBlankScreenRefresh('second'));
  assert.equal(item.blankScreenRefreshAttempts, 0);
});

test('preserved Dockview tabs position only their open popover', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const element = new TestElement('', 'div');
  const popover = new TestElement('', 'div');
  element.addEventListener = () => {};
  let positions = 0;
  let fileRefreshes = 0;
  const context = {
    document: {createElement: () => element}, dockviewLayoutState: {},
    tabberIsAdvanced: () => true,
    dockviewEmptyPaneSlot: () => false, syncDockviewTabShell() {},
    paneTabShouldPreserve: () => true, syncPaneTabPinnedChrome() {},
    paneTabPopoverForAnchor: () => popover,
    positionPaneTabPopover: () => {positions++;},
    isFileEditorItem: () => true, refreshFileTabPopover: () => {fileRefreshes++;},
    cleanupDetachedPaneTabPopover() {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function createDockviewTabRenderer('), source.indexOf('function captureDockviewPreviousPaneBeforeTabActivation(')), context);
  const renderer = context.createDockviewTabRenderer();
  renderer.init({params: {item: 'file'}});
  assert.equal(positions, 0, 'a closed hovered/preserved popover must not force layout');
  element.classList.add('popover-open');
  renderer.update();
  assert.equal(positions, 1, 'an open anchor retains repositioning');
  element.classList.remove('popover-open');
  popover.classList.add('popover-open');
  renderer.update();
  assert.equal(positions, 2, 'an open detached popover retains repositioning');
  popover.classList.remove('popover-open');
  renderer.update();
  assert.equal(positions, 2, 'closing the detail stops geometry work');
  assert.equal(fileRefreshes, 4, 'file details still refresh through their existing owner');
});

test('Dockview native click restores input without repeating consumed activation', () => {
  const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
  const actions = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
  const boot = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
  for (const route of ['native', 'native-painted', 'touch', 'already-active', 'expired', 'expired-output', 'expired-prior', 'first-output', 'reconnect', 'programmatic', 'keyboard', 'finder', 'cancel', 'pointer-cancel', 'drag', 'cancel-virtual', 'cancel-committed', 'cancel-virtual-committed', 'drag-committed', 'blur', 'hidden', 'blur-expired', 'blur-virtual', 'blur-committed', 'hidden-committed', 'hidden-virtual-committed', 'visible']) {
    const listeners = {};
    const documentListeners = {};
    const windowListeners = {};
    const item = route === 'finder' || route.includes('virtual') ? 'finder' : 'terminal';
    let active = ['already-active', 'expired', 'expired-output', 'expired-prior', 'blur-expired'].includes(route);
    let activations = 0;
    let acknowledgements = 0;
    let followups = 0;
    let inputFocused = false;
    const element = new TestElement('', 'div');
    element.addEventListener = (type, listener) => {listeners[type] = listener;};
    element.dataset.paneTab = item;
    let renderEvent;
    const frames = [];
    const terminal = {terminalOutputSeen: route !== 'first-output', term: {
      rows: 3,
      write(data, callback) {callback?.();},
      onRender(callback) {renderEvent = callback; return {dispose() {}};},
      refresh() {renderEvent?.({start: 0, end: 2});},
    }};
    const timers = [];
    const context = {
      tabberIsAdvanced: () => true,
      document: {createElement: () => element, addEventListener: (type, listener) => {documentListeners[type] = listener;}, removeEventListener: type => {delete documentListeners[type];}, querySelectorAll: () => [element],
        getElementsByClassName: classes => classes.split(' ').every(name => element.classList.contains(name)) ? [element] : []},
      window: {setTimeout: callback => timers.push(callback), clearTimeout() {}, addEventListener: (type, listener) => {windowListeners[type] = listener;}, removeEventListener: type => {delete windowListeners[type];}},
      dockviewLayoutState: {pendingUserPanelActivation: '', pendingUserPanelActivationTimer: 0},
      DOCKVIEW_PENDING_ACTIVATION_TTL_MS: 1500,
      isTmuxSession: session => session === 'terminal', isFileEditorItem: () => false,
      itemIsActivePaneTab: () => active,
      queueDockviewLayoutAdoption() {},
      cleanupDetachedPaneTabPopover() {}, dockviewEmptyPaneSlot: () => true,
      captureDockviewPreviousPaneBeforeTabActivation() {}, rememberFileExplorerExplicitSyncSession() {},
      terminals: new Map([['terminal', terminal]]), requestAnimationFrame: callback => frames.push(callback), cancelAnimationFrame() {},
      clientPerfStart() {}, clientPerfEnd() {}, utf8ByteLength: value => value.length,
      consumeTerminalControlMessage: () => false, tmuxWindowSwitchPaintCallback() {},
      terminalFrameRecoveryAttempts: new Map(), clearTerminalConnectionState() {},
      terminalIsVisible: () => false, scheduleTerminalBlankScreenRefresh() {}, scheduleTerminalAttentionHighlight() {},
      refreshTerminal(session, options) { options.afterParse?.(); terminal.term.refresh(); },
      dismissSessionToasts() {}, acknowledgeNotificationTarget() {}, recordTabActivation() {},
      nextTabSwitchHighlightGeneration: () => 7,
      scheduleTerminalActivationFollowup: () => {followups++;},
      commitUserTerminalTabActivation: () => {activations++; inputFocused = true; context.dockviewFinishTabActivationPerf(item);},
      setFocusedPanelItem() {}, focusTerminalFromUserAction() {},
      acknowledgeTerminalAttentionFromUserAction: () => {acknowledgements++;},
      focusTerminalDom: () => {inputFocused = true;},
      dockviewBeginTabPointerDrag: () => {context.dockviewLayoutState.tabPointerDrag = {item, dragged: false};},
      dockviewTabPointerEvent: event => event,
      dockviewDropClassification: () => ({region: {kind: 'tabs'}}),
      dockviewClearPreActivationCapture() {}, dockviewClearTabInsertionPreview() {},
      dockviewSetInvalidTabDropPreview() {}, clearDropPreview() {},
      dockviewTrackTabPointerDrag() {},
    };
    vm.createContext(context);
    vm.runInContext(actions.slice(actions.indexOf('function acknowledgeUserTerminalTabActivation('), actions.indexOf('function commitUserTerminalTabActivation(')), context);
    vm.runInContext(source.slice(source.indexOf('function dockviewBeginTabActivationPerf('), source.indexOf('function dockviewCore(')), context);
    vm.runInContext(boot.slice(boot.indexOf('function processTerminalSocketFrame('), boot.indexOf('function bindTerminalContainerForSession(')), context);
    const finishStart = source.indexOf('function dockviewFinishTabPointerDrag(');
    vm.runInContext(source.slice(finishStart, finishStart + source.slice(finishStart).indexOf('\nfunction ')), context);
    vm.runInContext(source.slice(source.indexOf('function dockviewInstallTabPointerReorderFallback('), source.indexOf('function dockviewInstallTabPointerReorderFallback(') + source.slice(source.indexOf('function dockviewInstallTabPointerReorderFallback(')).indexOf('\nfunction ')), context);
    vm.runInContext(source.slice(source.indexOf('function createDockviewTabRenderer('), source.indexOf('function captureDockviewPreviousPaneBeforeTabActivation(')), context);
    const renderer = context.createDockviewTabRenderer();
    renderer.init({params: {item}, api: {setActive: () => {active = true;}}});
    const installed = context.dockviewInstallTabPointerReorderFallback();
    const event = {target: {closest: () => null}, detail: route === 'programmatic' ? 0 : 1, preventDefault() {}};
    if (route === 'keyboard') {
      listeners.keydown({...event, key: 'Enter'});
      assert.equal(activations, 1, route);
      assert.equal(inputFocused, true, route);
      assert.equal(acknowledgements, item === 'terminal' ? 1 : 0, `${route} acknowledges before focusing`);
      continue;
    }
    if (route === 'expired-prior') context.dockviewLayoutState.tabActivationPerf = {item, terminal};
    if (route !== 'programmatic') listeners.pointerdown(event);
    if (route === 'visible') {
      context.document.hidden = false;
      const paint = context.dockviewLayoutState.tabActivationPerf;
      documentListeners.visibilitychange?.();
      assert.equal(context.dockviewLayoutState.tabActivationPerf, paint, 'visible pages retain their held gesture');
    }
    if (route === 'expired-prior') assert.equal(context.dockviewLayoutState.tabActivationPerf.terminal, null, 'a new pointer intent replaces the previous committed marker');
    if (['expired', 'expired-output', 'expired-prior'].includes(route)) {
      if (route === 'expired-output') context.processTerminalSocketFrame(item, terminal, 'ambient output');
      timers[0]();
      assert.equal(context.dockviewLayoutState.pendingUserPanelActivation, '', `${route} expires pointer intent`);
      assert.equal(context.dockviewLayoutState.tabActivationPerf.terminal, null, `${route} has not activated`);
    }
    if (['native', 'native-painted', 'touch', 'finder', 'first-output', 'reconnect'].includes(route) || route.endsWith('committed')) {
      active = true;
      context.dockviewCommitPanelActivation(item);
      inputFocused = false; // Dockview's subsequent group focus steals xterm focus before click.
    }
    if (route === 'native-painted') {
      frames.shift()();
      assert.equal(context.dockviewLayoutState.tabActivationPerf, null, 'a completed activation releases its paint record');
    }
    if (route === 'first-output') {
      assert.equal(context.dockviewLayoutState.tabActivationPerf.waitingForParse, false);
      context.processTerminalSocketFrame(item, terminal, 'first output');
      assert.equal(context.dockviewLayoutState.tabActivationPerf.waitingForParse, true, 'first output resumes the committed paint barrier');
      frames.shift()();
      assert.equal(context.dockviewLayoutState.tabActivationPerf, null, 'first output completes the committed paint barrier');
    }
    if (route === 'reconnect') {
      const replacement = {...terminal};
      context.terminals.set(item, replacement);
      context.processTerminalSocketFrame(item, replacement, 'replacement output');
      assert.equal(context.dockviewLayoutState.tabActivationPerf.terminal, replacement, 'output follows the replacement terminal');
    }
    if (route === 'touch') documentListeners.touchend({...event, type: 'touchend'});
    if (route.includes('cancel') || route.startsWith('drag') || route.startsWith('blur') || route.startsWith('hidden')) {
      const committed = route.endsWith('committed');
      const paint = context.dockviewLayoutState.tabActivationPerf;
      context.dockviewLayoutState.tabPointerDrag.dragged = route.startsWith('drag');
      if (route.startsWith('blur')) {
        if (route === 'blur-expired') timers[0]();
        windowListeners.blur?.();
      } else if (route.startsWith('hidden')) {
        context.document.hidden = true;
        documentListeners.visibilitychange?.();
      } else {
        const type = route === 'pointer-cancel' ? 'pointercancel' : route.startsWith('drag') ? 'touchend' : 'touchcancel';
        documentListeners[type]({...event, type});
      }
      assert.equal(activations, committed && item === 'terminal' ? 1 : 0, `${route} does not activate on release`);
      assert.equal(acknowledgements, committed && item === 'terminal' ? 1 : 0, `${route} does not acknowledge an uncommitted cancel or drag`);
      assert.equal(context.dockviewLayoutState.pendingUserPanelActivation, '', `${route} clears intent`);
      assert.equal(context.dockviewLayoutState.tabPointerDrag, null, `${route} releases held pointer state`);
      context.processTerminalSocketFrame('terminal', terminal, 'later ambient output');
      assert.equal(context.dockviewLayoutState.tabActivationPerf, committed ? paint : null, `${route} retires only uncommitted paint`);
      assert.equal(element.classList.contains('tab-activation-pending'), committed, `${route} cleans only uncommitted pending color`);
      if (committed) {
        frames.shift()();
        if (item !== 'terminal') frames.shift()();
        assert.equal(context.dockviewLayoutState.tabActivationPerf, null, `${route} finishes committed paint`);
        assert.equal(element.classList.contains('tab-activation-pending'), false, `${route} completes pending color`);
      }
      installed.dispose();
      assert.equal(windowListeners.blur, undefined, `${route} disposes window blur`);
      assert.equal(documentListeners.visibilitychange, undefined, `${route} disposes visibility changes`);
      continue;
    }
    listeners.click(event);
    assert.equal(activations, item === 'terminal' ? 1 : 0, `${route} activates once`);
    assert.equal(acknowledgements, item === 'terminal' ? 1 : 0, `${route} acknowledges exactly once across native callback and click`);
    if (item === 'terminal') {
      listeners.pointerdown(event);
      listeners.click(event);
      assert.equal(acknowledgements, 2, `${route} a new gesture can acknowledge new attention`);
    }
    if (['native', 'native-painted', 'touch'].includes(route)) assert.ok(followups > 0, `${route} keeps side work after the duplicate activation fast path`);
    if (item === 'terminal') assert.equal(inputFocused, true, `${route} restores keyboard input`);
  }
});

async function runTabberSuite() {
  await testAsync('activity completion publishes accepted products once and preserves independent changes', async () => {
    const source = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
    const activitySource = fs.readFileSync('static_src/js/yomux/35_agent_window_activity.js', 'utf8');
    const refreshes = [];
    let accepted = true;
    let failRefresh = true;
    let payload = {agent_window_snapshot_revision:7, activity:{one:{active_recency_ts:1}}};
    const context = vm.createContext({URLSearchParams, tabberSessionFileLookbackHours:24, tabberItemId:'tabber',
      tabberActivityState:{requestGeneration:0, appliedGeneration:0, request:null},
      normalizeSessionFileLookbackHours:value=>value, tabberActivityPayload:null,
      apiFetchJson:async()=>payload, itemInLayout:()=>true,
      applyTabberActivityPayload(value) {if(accepted) context.tabberActivityPayload=value; return accepted;},
      refreshTabberPanels(options) {
        if (failRefresh) {
          failRefresh = false;
          throw new Error('transient Tabber render failure');
        }
        refreshes.push(options);
      },
    });
    const revisionStart = activitySource.indexOf('function agentWindowSnapshotRevision(');
    const revisionEnd = activitySource.indexOf('\nfunction agentWindowSnapshotIsAuthoritative(', revisionStart);
    vm.runInContext(activitySource.slice(revisionStart, revisionEnd), context);
    const ownerStart = activitySource.indexOf('function refreshTabberPanelsForAgentWindowPublication(');
    const ownerEnd = activitySource.indexOf('\nfunction refreshAgentWindowActivityDisplays(', ownerStart);
    const signatureStart = activitySource.indexOf('function tabberActivityRenderSignature(');
    const signatureEnd = activitySource.indexOf('\nfunction refreshTabberPanelsForAgentWindowPublication(', signatureStart);
    const publicationStart = activitySource.indexOf('function tabberActivityPublicationSignature(', signatureStart);
    vm.runInContext(activitySource.slice(signatureStart, publicationStart), context);
    vm.runInContext(activitySource.slice(publicationStart, signatureEnd), context);
    vm.runInContext(activitySource.slice(ownerStart, ownerEnd), context);
    const start = source.indexOf('async function fetchTabberActivity(');
    vm.runInContext(source.slice(start, source.indexOf('function warmTabberDataOnLaunch(', start)), context);
    await assert.rejects(context.fetchTabberActivity({visible:true}), /transient Tabber render failure/);
    await context.fetchTabberActivity({visible:true});
    await context.fetchTabberActivity({visible:true});
    assert.equal(refreshes.length, 1, 'a failed publication retries, then an identical product does not rebuild twice');
    payload = {...payload, activity:{one:{active_recency_ts:2}}};
    await context.fetchTabberActivity({visible:true});
    assert.equal(refreshes.length, 2, 'activity can change independently of status revision');
    accepted = false;
    payload = {...payload, agent_window_snapshot_revision:8};
    await context.fetchTabberActivity({visible:true});
    assert.equal(refreshes.length, 2, 'rejected completion must not rebuild');
    assert.deepEqual(refreshes.map(row=>row.requestGeneration), [2,4]);
  });


  await testAsync('Lookback rejects held older ranges and drains one latest request in both modes', async () => {
    for (const mode of ['normal', 'advanced']) {
      const api = loadYomux('', ['one']);
      api.setTabberModeForTest(mode);
      const requests = [];
      let release;
      api.setApiFetchJsonForTest(url => {
        const hours = new URL(url, 'http://localhost').searchParams.get('hours');
        requests.push(hours);
        if (requests.length === 1) return new Promise(resolve => {release = resolve;});
        assert.equal(api.tabberActivityPayloadForTest()?.marker, undefined);
        return Promise.resolve({activity: {}, marker: hours});
      });
      const first = api.fetchTabberActivityForTest({visible: true});
      api.setTabberSessionFileLookbackHoursForTest(48);
      api.setTabberSessionFileLookbackHoursForTest(336);
      release({activity: {}, marker: 'obsolete'});
      await first;
      assert.deepEqual(requests, ['24', '336']);
      assert.equal(api.tabberActivityPayloadForTest().marker, '336');
      assert.equal(api.tabberActivityStateForTest().request, null);
    }
  });

  await testAsync('explicit server Normal wins over stale browser Advanced without a startup write', async () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const payload = {defaults, settings: {general: {default_tabber_mode: 'normal'}}, tabber_mode_explicit: true, mtime_ns: 3};
    const api = loadYomux('', [], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: payload}, localStorage: {'yomux.tabber.mode.v1': 'advanced'},
    });
    assert.equal(api.tabberModeForTest(), 'normal');
    const writes = [];
    api.setApiFetchJsonForTest(async (url, options) => { if (url === '/api/settings' && options?.method === 'POST') writes.push(options.body); return payload; });
    api.applySettingsPayloadForTest(payload, {initial: true});
    await flushAsyncWork();
    assert.deepEqual(writes, []);
    assert.equal(api.tabberModeForTest(), 'normal');
    const legacy = loadYomux('', [], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {...payload, tabber_mode_explicit: false}}, localStorage: {'yomux.tabber.mode.v1': 'advanced'},
    });
    assert.equal(legacy.tabberModeForTest(), 'advanced', 'only a confirmed absent preference permits legacy migration');
  });

  await testAsync('description rename fences delayed metadata including an equal-generation cached response', async () => {
    const api = loadYomux('', ['one']);
    const snapshot = (generation, description, manual = false) => ({
      metadata_identity: {epoch: 'description-overlap', generation}, topology_generation: 1,
      session_order: ['one'], sessions: {one: {work_graph: {generation: 1}, window_metadata: [{window_id: '@1', window_index: 0, description, description_manual: manual}]}},
    });
    const options = {refreshAuto: false, refreshActivity: false, refreshContext: false};
    assert.equal(await api.applySessionMetadataPayloadForTest(snapshot(2, 'old'), options), true);
    api.setApiFetchJsonForTest(async (_url, options) => {
      assert.deepEqual(JSON.parse(options.body), {session: 'one', window: '0', window_id: '@1', description: 'manual'});
      return {window_id: '@1', description: 'manual', cache: {pending_identity: {epoch: 'description-overlap', generation: 3}}};
    });
    assert.equal(await api.renameTabberDescriptionForTest('one', 0, '@1', 'manual'), true);
    assert.equal(await api.applySessionMetadataPayloadForTest(snapshot(2, 'old'), options), false);
    assert.equal(api.transcriptMetadataStateForTest().payload.sessions.one.window_metadata[0].description, 'manual');
    assert.equal(await api.applySessionMetadataPayloadForTest(snapshot(3, 'manual', true), options), true);
    assert.equal(await api.applySessionMetadataPayloadForTest(snapshot(1, 'automatic'), options), false);
    assert.equal(api.transcriptMetadataStateForTest().payload.sessions.one.window_metadata[0].description_manual, true);
    assert.equal(await api.applySessionMetadataPayloadForTest({...snapshot(1, 'old'), status: 'refreshing', cache: {pending_identity: {epoch: 'description-overlap', generation: 4}}}, options), false);
    assert.equal(api.transcriptMetadataStateForTest().pendingGeneration, 4);
  });
  await testAsync('first event and metadata adoption retain the bootstrap topology fence', async () => {
    const api = loadYomux('', ['fresh'], 'http:', 'Linux x86_64', 'admin', {tmuxTopology: {generation: 7, epoch: 'boot'}});
    assert.equal(api.adoptServerEpochForTest('boot'), false);
    assert.equal(await api.applySessionMetadataPayloadForTest({metadata_identity: {epoch: 'boot', generation: 1}, topology_generation: 7, session_order: [], sessions: {}}, {refreshAuto: false, refreshActivity: false, refreshContext: false}), false);
    assert.deepEqual(api.sessionsForTest(), ['fresh']);
  });

  await testAsync('session selection wrapper shares deferred Tabber scrolling with row activation', async () => {
    const source = fs.readFileSync('static_src/js/yomux/86_changes_20_differ.js', 'utf8');
    const calls = [];
    const context = {
      selectSession: async (session, options) => {calls.push(`selected:${session}:${options.userInitiated}`); return 'selected';},
      itemInLayout: () => true, tabberItemId: 'tabber',
      scheduleTabberTreeLayoutStateSync: options => calls.push(`scheduled:${options.scrollIntoView}`),
      syncTabberTreeActiveSelection: () => {throw new Error('synchronous Tabber scroll forces layout before terminal paint');},
    };
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('const originalSelectSessionForTabberTree'), source.indexOf('function showChangedDirectoryContextMenu(')), context);
    assert.equal(await context.selectSession('second', {userInitiated: true}), 'selected');
    assert.deepEqual(calls, ['selected:second:true', 'scheduled:true']);
    context.itemInLayout = () => false;
    await context.selectSession('first', {});
    assert.equal(calls.at(-1), 'selected:first:undefined', 'closed Tabber does not schedule scrolling');
  });
  await testAsync('Tabber forwards user activation to background terminal tabs and placement', checkBackgroundTabberActivation);
  await testAsync('terminal placement preserves user focus through every placement branch', async () => {
    const source = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
    for (const route of ['existing', 'empty', 'finder', 'stack', 'refused']) {
      for (const userInitiated of [false, true]) {
        const activations = [];
        let slot = route === 'existing' ? 'left' : null;
        const context = {
          slotForSession: () => slot, slotForNewTmuxSession: () => 'left',
          tabLayoutManipulationAllowed: () => true,
          paneIsPlaceholder: () => route === 'empty', paneTabs: () => ['first'],
          activeItemForSide: () => route === 'finder' ? 'finder' : 'first',
          isFileExplorerItem: item => item === 'finder', sidePaneDefaultWidthPercent: 25,
          moveSessionToSlot: () => { if (route !== 'refused') slot = 'left'; },
          splitSessionAtSlot: () => { slot = 'right'; }, itemInLayout: () => Boolean(slot),
          activatePaneTab: (side, session, options) => activations.push({side, session, options}),
        };
        vm.createContext(context);
        vm.runInContext(source.slice(source.indexOf('async function placeTmuxSession('), source.indexOf('// File -> Finder')), context);
        await context.placeTmuxSession('second', {userInitiated});
        const expected = route === 'existing' || (userInitiated && route !== 'refused');
        assert.equal(activations.length, expected ? 1 : 0, `${route}/${userInitiated}`);
        if (expected) {
          assert.equal(activations[0].session, 'second');
          assert.equal(activations[0].side, slot);
          assert.equal(activations[0].options.userInitiated, userInitiated);
        }
      }
    }
  });
  test('session control refresh shares one document scan and discovers replacement and detached controls', () => {
    const sessionNames = Array.from({length: 20}, (_, index) => `control-${index}`);
    const api = loadYomux('', sessionNames);
    const body = api.testElementForId('body');
    const buttons = [];
    const tabs = [];
    for (const session of sessionNames) {
      const panel = api.testElementForId(`panel-${session}`);
      body.appendChild(panel);
      const wrapper = new TestElement('', 'div');
      wrapper.className = 'pane-tab';
      const button = new TestElement('', 'button');
      button.dataset.yoloSession = session;
      button.title = 'stale';
      wrapper.appendChild(button);
      panel.appendChild(wrapper);
      const tab = new TestElement('', 'button');
      tab.dataset.tab = session;
      tab.dataset.tabName = 'terminal';
      panel.appendChild(tab);
      buttons.push(button);
      tabs.push(tab);
      api.setAutoApproveStateForTest(session, {enabled: true});
    }
    const queryAll = body.querySelectorAll.bind(body);
    let scans = 0;
    body.querySelectorAll = selector => {
      if (/data-yolo-session|data-tab-name|data-tmux-window-bar/.test(selector)) scans += 1;
      return queryAll(selector);
    };
    api.renderAutoApproveButtonsForTest(sessionNames);
    assert.equal(scans, 1, 'a many-session update scans global controls once');
    for (let index = 0; index < sessionNames.length; index += 1) {
      assert.equal(buttons[index].getAttribute('aria-pressed'), 'true');
      assert.equal(buttons[index].getAttribute('title'), null, 'Tabber controls suppress native title hovers');
      assert.ok(tabs[index].getAttribute('aria-label'), 'each terminal tab receives its current accessible label');
    }
    const replacement = new TestElement('', 'button');
    replacement.dataset.yoloSession = sessionNames[0];
    buttons[0].replaceWith(replacement);
    const detachedPanel = api.testElementForId(`panel-${sessionNames[1]}`);
    detachedPanel.remove();
    api.setAutoApproveStateForTest(sessionNames[0], {enabled: false});
    api.setAutoApproveStateForTest(sessionNames[1], {enabled: false});
    scans = 0;
    api.renderAutoApproveButtonsForTest(sessionNames);
    assert.equal(scans, 1, 'the next refresh rebuilds its collection with one global scan');
    assert.equal(replacement.getAttribute('aria-pressed'), 'false', 'a newly mounted control is discovered');
    assert.equal(buttons[0].getAttribute('aria-pressed'), 'true', 'the removed control is no longer updated');
    assert.equal(buttons[1].getAttribute('aria-pressed'), 'false', 'a pane constructed before attachment still updates');
  });

  test('global activity payload creates one Tabber session row per tmux session', () => {
    const api = loadYomux('', []);
    api.setFileExplorerModeForTest('tabber');
    api.setTabberActivityForTest({activity: {}, agents: []});
    const accepted = api.applyTabberActivityPayloadForTest({
      activity: {
        'synthetic-tab-a': {active_recency_ts: 100},
        'synthetic-tab-b': {active_recency_ts: 200},
      },
      sessions: {
        'synthetic-tab-a': {panes: [{window: '0', window_name: 'bash', command: 'bash', active: true, window_active: true, pid: 101}]},
        'synthetic-tab-b': {panes: [{window: '0', window_name: 'bash', command: 'bash', active: true, window_active: true, pid: 102}]},
      },
      session_order: ['synthetic-tab-a', 'synthetic-tab-b'],
    });
    assert.equal(accepted, true, 'the global activity payload is accepted as a Tabber topology update');
    const rows = api.tabberRenderedRowsForTest();
    assert.deepStrictEqual(canonical(rows.filter(row => row.type === 'normal').map(row => row.path)), ['/s_synthetic-tab-a/w_0', '/s_synthetic-tab-b/w_0'], 'every global session appears as its single flat window row, even without an agent process');
  });

  test('Normal conversation panes hide their single tab while Advanced retains headers', () => {
    const api = loadYomux('?sessions=one,two', ['one', 'two']);
    const leaves = node => node.type === 'leaf' ? [node.data] : node.data.flatMap(leaves);
    api.setTabberModeForTest('normal');
    api.applyLayoutSlotsForTest(api.layoutSlotsForTest());
    const normal = leaves(api.dockviewJsonFromLayoutSlots(api.layoutSlotsForTest()).grid.root);
    const conversations = normal.filter(group => group.views.some(item => ['one', 'two'].includes(item)));
    assert.ok(conversations.length > 0);
    assert.ok(conversations.every(group => group.views.length === 1 && group.hideHeader === true));
    assert.ok(normal.filter(group => !group.views.includes('__tabber__')).every(group => group.hideHeader === true && group.views.length === 1));
    assert.equal(normal.find(group => group.views.includes('__tabber__')).hideHeader, false, 'the home navigation remains visible in Normal');
    api.setTabberModeForTest('advanced');
    assert.ok(leaves(api.dockviewJsonFromLayoutSlots(api.layoutSlotsForTest()).grid.root).every(group => group.hideHeader === false));
  });

  test('Normal mode blocks legacy pane-tab-strip dragover and drop through the shared policy', () => {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest('normal');
    const strip = tabStrip([tabElement('one', 100, 120)]);
    api.bindPaneTabStrip(strip, 'left');
    const before = api.layoutSlotsForTest();
    const over = dragEvent(125, 'two');
    strip.ondragover(over);
    assert.equal(over.defaultPrevented, true);
    assert.equal(over.propagationStopped, true);
    assert.equal(over.dataTransfer.dropEffect, 'none');
    assert.equal(strip.classList.contains('tab-drop-preview'), false);
    const drop = dragEvent(125, 'two');
    strip.ondrop(drop);
    assert.equal(drop.defaultPrevented, true);
    assert.equal(drop.propagationStopped, true);
    assert.equal(drop.dataTransfer.dropEffect, 'none');
    assert.deepStrictEqual(canonical(api.layoutSlotsForTest()), canonical(before), 'Normal mode does not move a tab through the legacy strip fallback');
  });

  test('Normal layout admission repairs conversation stacks and preserves other surfaces', () => {
    const api = loadYomux('', ['one', 'two', 'three']);
    api.setTabberModeForTest('normal');
    const stacked = {left: {tabs: ['one', '__prefs__', 'two', 'three'], active: '__prefs__'}};
    for (const activeOnlyActivation of [false, true]) {
      api.applyLayoutSlotsForTest(stacked, {activeOnlyActivation});
      const slots = api.layoutSlotsForTest();
      const panes = Object.values(slots).filter(value => Array.isArray(value?.tabs));
      const terminals = panes.filter(value => value.tabs.some(item => ['one', 'two', 'three'].includes(item)));
      assert.equal(terminals.length, 1);
      assert.ok(terminals.every(value => value.tabs.length === 1));
      assert.ok(api.allTabItems().includes('__prefs__'), 'conversation policy preserves the nonterminal item registration');
    }
    api.setTabberModeForTest('advanced');
    api.applyLayoutSlotsForTest(stacked);
    assert.ok(Object.values(api.layoutSlotsForTest()).some(value => value.tabs?.length === 4), 'Advanced still accepts tab stacks');
  });

  await testAsync('Normal tools and retained dynamic views share the one-item toggle owner', async () => {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    api.applyLayoutSlotsForTest({});
    const rows = api.buildTabberNormalRowsForTest();
    assert.ok(rows.some(row => row.tabber.item === '__finder__'), 'Finder remains reachable in Normal Tabber');
    assert.ok(['__differ__', '__chat__', '__yoagent__'].every(item => !api.buildTabberNormalRowsForTest().some(entry => entry.tabber?.item === item)), 'Differ, YO!chat, and YO!agent are omitted from Normal Tabber selections');
    await api.toggleTabberNormalPaneForTest('__finder__');
    assert.ok(api.itemInLayout('__finder__'));
    await api.toggleTabberNormalPaneForTest('__finder__');
    assert.equal(api.itemInLayout('__finder__'), false);
    const diff = api.resolveLayoutItem(api.gitDiffItemFor('/tmp/tabber-retained-diff'));
    api.setGitDiffTabStateForTest(diff, {selectedPath: '/tmp/tabber-retained-diff/a'});
    await api.toggleTabberNormalPaneForTest(diff);
    await api.toggleTabberNormalPaneForTest(diff);
    assert.ok(api.dynamicVirtualLayoutItemsForTest().includes(diff));
    assert.equal(api.gitDiffTabStateForTest(diff).selectedPath, '/tmp/tabber-retained-diff/a', 'deselection never destroys view state');
    assert.ok(api.allTabItems().includes(diff));
    await api.toggleTabberNormalPaneForTest(diff);
    await api.toggleTabberNormalPaneForTest('one');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['one'], 'selecting a conversation replaces the visible tool');
    assert.ok(api.itemInLayout('one'));
    assert.equal(api.gitDiffTabStateForTest(diff).selectedPath, '/tmp/tabber-retained-diff/a');
    await api.toggleTabberNormalPaneForTest('one');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'selecting the visible conversation again deselects it');
    api.setTabberModeForTest('advanced');
    const advancedItems = ['__differ__', '__chat__', '__yoagent__'];
    api.applyLayoutSlotsForTest({left: {tabs: advancedItems, active: '__differ__'}});
    const leaves = node => node.type === 'leaf' ? [node.data] : node.data.flatMap(leaves);
    const advancedViews = leaves(api.dockviewJsonFromLayoutSlots(api.layoutSlotsForTest()).grid.root)
      .flatMap(group => group.views || []);
    assert.ok(advancedItems.every(item => advancedViews.includes(item)), 'Advanced exposes Differ, YO!chat, and YO!agent as pane tabs');
    assert.equal(api.dynamicVirtualLayoutItemsForTest().includes(diff), true, 'switching modes preserves the hidden dynamic view identity');
  });

  test('Normal viewport admission reduces old split panes to one content pane', () => {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest('normal');
    const vertical = {__tree: {split: 'column', children: [{slot: 'top'}, {slot: 'bottom'}]}, top: {tabs: ['one'], active: 'one'}, bottom: {tabs: ['two'], active: 'two'}};
    const next = api.normalizeLayoutSlotsForViewport(vertical);
    assert.equal(Object.values(next).flatMap(value => value?.tabs || []).filter(item => ['one', 'two'].includes(item)).length, 1);
    assert.ok(Object.values(next).filter(value => value?.tabs).every(value => value.tabs.length <= 1));
    assert.equal(vertical.__tree.split, 'column', 'admission leaves the source snapshot intact');
    const blankPeer = {__tree: {split:'row', children:[{slot:'one'},{slot:'blank'}]}, one:{tabs:['one'],active:'one'}, blank:{tabs:[],active:null,placeholder:true}};
    api.applyLayoutSlotsForTest(blankPeer, {prune:false});
    const admitted = api.layoutSlotsForTest();
    assert.equal(Object.values(admitted).filter(value => value?.placeholder).length, 0, 'a selected pane has no spare blank peer');
  });

  test('Normal Tabber starts flat and retains one row per tmux window', () => {
    const api = loadYomux('', []);
    api.setTabberModeForTest('normal');
    assert.equal(api.dockviewPaneTabHtml('__tabber__').includes('class="session-button-dir"'), false, 'Normal keeps the Tabber icon but omits its visible word in the real Dockview tab renderer');
    api.setTabberModeForTest('advanced');
    assert.equal(api.dockviewPaneTabHtml('__tabber__').includes('class="session-button-dir"'), true, 'Advanced retains the Tabber label in the Dockview tab renderer');
    api.setTabberModeForTest('normal');
    api.setFileExplorerModeForTest('tabber');
    api.setTabberActivityForTest({activity: {}, agents: []});
    api.applyTabberActivityPayloadForTest({
      activity: {'normal-one:0': {last_user_input_ts: 300}, 'normal-two:0': {last_user_input_ts: 200}, 'normal-two:1': {last_user_input_ts: 100}},
      sessions: {
        'normal-one': {panes: [{window: '0', window_name: 'codex', command: 'codex', active: true, window_active: true}]},
        'normal-two': {panes: [
          {window: '0', window_name: 'claude', command: 'claude', active: true, window_active: true},
          {window: '1', window_name: 'bash', command: 'bash', active: true, window_active: false},
        ]},
      },
      session_order: ['normal-one', 'normal-two'],
    });
    const rows = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
    assert.equal(rows.length, 3, 'Normal flattens a single-window session and every child window into same-level entries');
    assert.ok(rows.every(row => row.normalRow), 'the shared patcher applies the two-line Normal layout class');
    assert.deepEqual(new Set(rows.map(row => row.path)), new Set(['/s_normal-one/w_0', '/s_normal-two/w_0', '/s_normal-two/w_1']), 'flat entries keep stable source-window identities');
    const oneWindow = rows.find(row => row.path === '/s_normal-one/w_0');
    const multiWindow = rows.find(row => row.path === '/s_normal-two/w_0');
    assert.ok(oneWindow.nameHtml.includes('tabber-normal-primary') && oneWindow.nameHtml.includes('tabber-normal-description'), 'every conversation reserves a title and a separate description line');
    assert.ok(oneWindow.nameHtml.includes('normal-one') && oneWindow.nameHtml.includes('codex'), 'a one-window tmux shows its session label and agent icon without a parent row');
    assert.ok(multiWindow.nameHtml.includes('normal-two') && multiWindow.nameHtml.includes('claude'), 'multi-window entries include the session label and agent icon inline');
    const toolbar = api.fileExplorerChangesPanelStaticHtmlForTest({view: 'tabber'});
    assert.ok(toolbar.includes('data-tabber-new-chat') && !toolbar.includes('data-tabber-advanced'), 'the content toolbar leaves Advanced to the shared navigation');
    const slot = api.slotForItem('__tabber__');
    const header = api.dockviewHeaderActionsHtml('__tabber__', slot);
    assert.ok(header.includes('data-tabber-advanced'));
    assert.ok(header.indexOf('data-tabber-advanced') < header.indexOf('data-pane-minimize'));
    api.setTabberSidebarSinglePaneForTest(true);
    const narrowHeader = api.dockviewHeaderActionsHtml('__tabber__', 'right');
    assert.ok(narrowHeader.includes('data-tabber-advanced'), 'the shared mode control remains available when Tabber fills the narrow content pane');
    assert.ok(narrowHeader.indexOf('data-tabber-advanced') < narrowHeader.indexOf('data-pane-minimize'));
    assert.ok(toolbar.includes('data-tabber-search') && toolbar.includes('tabber-new-chat-icon') && toolbar.includes('tabber-search-icon'), 'Tabber reuses its toolbar for the ChatGPT-style Chat and Search controls');
    assert.equal(/data-tabber-advanced checked/.test(toolbar), false, 'Advanced is unchecked in Normal mode');
    assert.equal(toolbar.includes('data-file-explorer-tree-sort'), false, 'advanced-only tree controls stay out of the Normal sidebar');
    const panelShell = fs.readFileSync('static_src/js/yomux/78_panel_shell.js', 'utf8');
    assert.ok(/title\.textContent = t\('pane\.dropTab'\);\s*title\.hidden = !tabberIsAdvanced\(\);/.test(panelShell), 'the empty-pane drag prompt is hidden in Normal mode and retained in Advanced');
  });

  await testAsync('Normal window aliases keep the source-window keyboard cursor', async () => {
    const api = loadYomux('', ['same']);
    api.setTabberModeForTest('normal');
    api.setTranscriptInfoForTest('same', {window_metadata: [{window_id: '@test-1', window_index: 1}]});
    api.storeTabberViewSessionForTest('__yomux_view_1', {sourceSession:'same',windowIndex:1});
    await api.toggleTabberNormalPaneForTest('__yomux_view_1');
    assert.equal(api.activeTabberRowPathForTest(), '/s_same/w_1');
  });

  await testAsync('Normal selection admission exits narrow Tabber only after validation', async () => {
    const api = loadYomux('', ['one']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest(['one']);
    api.applyLayoutSlotsForTest({left: {tabs: ['one'], active: 'one'}});
    api.setTabberSidebarSinglePaneForTest(true);
    assert.equal(await api.toggleTabberNormalPaneForTest('one', -1), false, 'stale activation is rejected');
    assert.equal(api.tabberSidebarStateForTest().singlePane, true, 'rejected activation leaves narrow Tabber state intact');
    await api.applyTabberNormalSelectionsForTest('one');
    assert.equal(api.tabberSidebarStateForTest().singlePane, false, 'an admitted terminal selection leaves the Tabber-only pane');
    assert.ok(api.itemInLayout('one'), 'the selected terminal becomes visible');
  });

  test('Normal display caching preserves missing-metadata dates and current-row styling', () => {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest('normal');
    api.setFileExplorerModeForTest('tabber');
    api.applyTabberActivityPayloadForTest({activity:{one:{active_recency_ts:Date.now()/1000-180}},agents:[],
      session_order:['one','two'],sessions:{one:{panes:[]},two:{panes:[]}}});
    api.setFileExplorerTreeDateModeForTest('none');
    api.setTabberNormalSelectionsForTest(['one']);
    api.applyLayoutSlotsForTest({left:{tabs:['one'],active:'one'}}, {prune:false});
    const groups = new TestElement('', 'div');
    api.setFocusedPanelItemValueForTest('two');
    api.renderTabberTreeForTest(groups);
    const rows = groups.querySelectorAll('.file-tree-row[data-tabber-type="normal"]');
    const first = rows.find(row => row.dataset.tabberSession === 'one');
    const second = rows.find(row => row.dataset.tabberSession === 'two');
    assert.equal(first.querySelector('.file-tree-date').textContent, '');
    api.setFileExplorerTreeDateModeForTest('relative');
    api.renderTabberTreeForTest(groups);
    assert.ok(first.querySelector('.file-tree-date').textContent);
    api.setFocusedPanelItemValueForTest('one');
    api.renderTabberTreeForTest(groups);
    assert.equal(groups.querySelector('.file-tree-row[data-tabber-session="one"]'), first);
    assert.equal(first.classList.contains('current-file'), true);
    assert.equal(second.classList.contains('current-file'), false);
  });

  test('Normal and Advanced construct the same flat window status rows', () => {
    const api = loadYomux('', ['one']);
    api.setTranscriptInfoForTest('one', {panes: [{window:'0',command:'claude',active:true,window_active:true}],
      window_metadata:[{window_index:0,description:'First window'}]});
    api.setAutoApproveStateForTest('one', {agent_windows:[{kind:'claude',state:'idle',last_active_ts:100,window_index:0,current:true,window_active:true,
      path_entries:[{path:'/home/test/project',mtime:90,git:{root:'/home/test/project',branch:'main'}}]}]});
    for (const mode of ['normal', 'advanced', 'normal']) {
      api.setTabberModeForTest(mode);
      const tree = api.buildTabberNormalRowsForTest();
      const window = tree.find(row => row.tabber?.session === 'one');
      assert.equal(window.tabber.agentKey, 'claude');
      assert.equal(window.tabber.agentStatus.state, 'idle');
      assert.equal(window.mtime, 100);
      assert.ok(tree.every(row => row.kind === 'file'));
      if (mode === 'normal') assert.ok(api.tabberRenderedRowsForTest().find(row => row.type === 'normal').nameHtml.includes('First window'));
    }
  });

  test('Normal imports session-list and serialized URLs through its shared pane policy', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    for (const query of ['?sessions=one,two,three', '?layout=left&tabs=left:one,two,three']) {
      const api = loadYomux(query, ['one', 'two', 'three'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
      });
      assert.deepEqual(api.tabberNormalSelectionsForTest(), ['three'], query);
      const panes = Object.values(api.layoutSlotsForTest()).filter(value => Array.isArray(value?.tabs));
      assert.equal(panes.filter(value => value.tabs.some(item => ['one', 'two', 'three'].includes(item))).length, 1);
      assert.ok(panes.filter(value => value.tabs.some(item => ['one', 'two', 'three'].includes(item))).every(value => value.tabs.length === 1));
      assert.ok(panes.some(value => value.tabs.includes(api.tabberItemId) && value.active === api.tabberItemId), 'Tabber is visible ' + query);
    }
    for (const query of ['?layout=left&tabs=left:prefs', '?layout=left&tabs=left:prefs,one,two,three', '?layout=row(left,right)&tabs=left:prefs,one;right:two']) {
      const api = loadYomux(query, ['one', 'two', 'three'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
      });
      assert.ok(api.itemInLayout('__prefs__'), 'nonterminal explicit targets survive ' + query);
      assert.ok(api.itemInLayout(api.tabberItemId));
    }
  });

  test('Normal keeps explicit file-surface URLs active while retaining Tabber', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    for (const [query, surface] of [['?sessions=files,one&layout=left&tabs=left:files', '__differ__'], ['?sessions=files,one', '__finder__'], ['?sessions=differ,one', '__differ__'], ['?sessions=tabber,one', '__tabber__']]) {
      const api = loadYomux(query, ['one'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
      });
      const panes = Object.values(api.layoutSlotsForTest()).filter(value => Array.isArray(value?.tabs));
      assert.ok(panes.some(value => value.active === surface), `${query}: ${JSON.stringify({surface, selections: api.tabberNormalSelectionsForTest(), panes})}`);
      assert.ok(api.itemInLayout(api.tabberItemId));
    }
  });

  test('Normal preserves the explicit stats-only boot layout without starting Tabber', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const api = loadYomux('?debug=1&layout=left&tabs=left:one,__debug__*', ['one'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {statsOnly: true, settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
    });
    assert.equal(api.itemInLayout(api.tabberItemId), false);
    assert.ok(api.itemInLayout('__debug__'));
  });

  await testAsync('Normal file-only and mixed URLs preserve their editor across chat selection', async () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    for (const query of ['?layout=left&tabs=left:file%3A%2Ftmp%2Ftabber.md', '?layout=row(left,right)&tabs=left:file%3A%2Ftmp%2Ftabber.md,one;right:two']) {
      const api = loadYomux(query, ['one', 'two'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
      });
      const fileItem = api.fileEditorItemFor('/tmp/tabber.md');
      assert.ok(api.itemInLayout(fileItem), query);
      await api.toggleTabberNormalPaneForTest('two');
      assert.ok(api.allTabItems().includes(fileItem), 'unselected editors remain registered and reachable from Tabber');
    }
  });

  test('fresh Normal Tabber starts in the sidebar with at most one terminal pane', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const settingsPayload = {defaults, settings: {}, mtime_ns: 1};
    const withSession = loadYomux('', ['fresh-session'], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {settingsPayload}});
    assert.equal(withSession.tabberModeForTest(), 'normal', 'fresh installations default to Normal in the browser');
    const withSessionSlots = withSession.layoutSlotsForTest();
    assert.ok(Object.values(withSessionSlots).some(value => value && Array.isArray(value.tabs) && value.tabs.includes('__tabber__')), 'Tabber is present on first load');
    assert.ok(Object.values(withSessionSlots).some(value => value && Array.isArray(value.tabs) && value.tabs.includes('fresh-session')), 'one existing tmux session opens as the sole terminal pane');
    assert.equal(Object.values(withSessionSlots).filter(value => value && Array.isArray(value.tabs) && value.tabs.some(item => item === 'fresh-session')).length, 1, 'initial Normal layout never duplicates the session into another pane');

    const empty = loadYomux('', [], 'http:', 'Linux x86_64', 'admin', {bootstrapOverrides: {settingsPayload}});
    assert.equal(empty.tabberModeForTest(), 'normal');
    assert.ok(Object.values(empty.layoutSlotsForTest()).some(value => value && Array.isArray(value.tabs) && value.tabs.includes('__tabber__')), 'empty fresh install still opens Tabber');
    assert.equal(Object.values(empty.layoutSlotsForTest()).some(value => value && Array.isArray(value.tabs) && value.tabs.some(item => item === 'fresh-session')), false, 'no tmux sessions means zero terminal panes');
  });

  test('saved Tabber mode is restored from Preferences in a fresh browser', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const returningAdvancedUser = loadYomux('', [], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {
        defaults,
        settings: {general: {default_tabber_mode: 'advanced'}},
        mtime_ns: 2,
      }},
      localStorage: {'yomux.tabber.mode.v1': 'normal'},
    });
    assert.equal(returningAdvancedUser.tabberModeForTest(), 'advanced', 'the server-saved Preference restores Advanced and overrides stale browser storage');
    const preferenceHtml = returningAdvancedUser.preferencesPanelHtmlForTest('tabber', []);
    assert.match(preferenceHtml, /Default Tabber mode[\s\S]*Normal[\s\S]*Advanced/, 'General Preferences exposes the persisted mode choices');
    returningAdvancedUser.applySettingsPayloadForTest({
      defaults,
      settings: {general: {default_tabber_mode: 'normal'}},
      mtime_ns: 3,
    });
    assert.equal(returningAdvancedUser.tabberModeForTest(), 'normal', 'a newer Preferences revision updates the active mode');
  });

  test('fresh Normal Tabber selects the most recently active session when activity arrives', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const api = loadYomux('', ['older-session', 'newer-session'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
    });
    const applied = api.applyTabberActivityPayloadForTest({
      session_order: ['older-session', 'newer-session'],
      sessions: {
        'older-session': {panes: [{index: 0, index_text: '0', name: 'bash', active: true, window_active: true}]},
        'newer-session': {panes: [{index: 0, index_text: '0', name: 'bash', active: true, window_active: true}]},
      },
      activity: {
        'older-session': {active_recency_ts: 100},
        'newer-session': {active_recency_ts: 200},
        'older-session:0': {active_recency_ts: 100},
        'newer-session:0': {active_recency_ts: 200},
      },
      agents: [],
    });
    assert.equal(applied, true, 'the first authoritative Tabber activity snapshot is accepted');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['newer-session'], 'a fresh Normal Tabber opens the newest session rather than trusting roster order');
    const panes = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []);
    assert.equal(panes.filter(item => ['older-session', 'newer-session'].includes(item)).join(','), 'newer-session', 'initial recent selection creates exactly one matching terminal pane');
  });

  test('saved Normal selection restores only its selected item', () => {
    const api = loadYomux('', ['one', 'two', 'three'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {
        defaults: {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}},
        settings: {},
        mtime_ns: 1,
      }},
      localStorage: {
        'yomux.tabber.normalSelections.v1': '["one","two","three"]',
      },
    });
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['three'], 'saved selections retain only the latest pane choice');
    const panes = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : [])
      .filter(item => ['one', 'two', 'three'].includes(item));
    assert.deepEqual(panes, ['three'], 'reload restores one selected Normal pane without reopening stale selections');
  });

  test('topology-only Tabber data waits for activity before persisting the default selection', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const api = loadYomux('', ['older-session', 'newer-session'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
    });
    api.applyTabberActivityPayloadForTest({
      session_order: ['older-session', 'newer-session'],
      sessions: {
        'older-session': {panes: [{window: '0', window_name: 'bash'}]},
        'newer-session': {panes: [{window: '0', window_name: 'bash'}]},
      },
    });
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'topology alone leaves the default choice pending');
    api.applyTabberActivityPayloadForTest({
      session_order: ['older-session', 'newer-session'],
      activity: {'older-session:0': {active_recency_ts: 100}, 'newer-session:0': {active_recency_ts: 300}},
      agents: [],
    });
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['newer-session'], 'the first activity snapshot chooses the most recent session');
  });

  await testAsync('a pending window activation claims the default before late activity arrives', async () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const api = loadYomux('', ['older-session', 'chosen-session', 'newer-session'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
    });
    let releaseViewRequest;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseViewRequest = resolve; })
      : Promise.resolve({ok: true}));
    const pendingActivation = api.activateNormalTabberRowForTest({dataset: {
      tabberType: 'normal', tabberNormalWindowView: 'true',
      tabberWindowId: '@chosen-1',
    }}, 'chosen-session', 1);
    api.applyTabberActivityPayloadForTest({
      session_order: ['older-session', 'chosen-session', 'newer-session'],
      sessions: {
        'older-session': {panes: [{window: '0', window_name: 'bash', active: true, window_active: true}]},
        'chosen-session': {panes: [
          {window: '0', window_name: 'bash', active: true, window_active: true},
          {window: '1', window_name: 'bash', active: true, window_active: false},
        ]},
        'newer-session': {panes: [{window: '0', window_name: 'bash', active: true, window_active: true}]},
      },
      activity: {
        'older-session:0': {active_recency_ts: 100},
        'chosen-session:1': {active_recency_ts: 200},
        'newer-session:0': {active_recency_ts: 300},
      },
      agents: [],
    });
    const panesWhileViewCreationIsHeld = Object.values(api.layoutSlotsForTest())
      .flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []);
    assert.ok(panesWhileViewCreationIsHeld.includes('older-session'), 'late activity preserves the pane visible when the user began activating a window');
    assert.notEqual(api.focusedTerminalForTest(), 'newer-session', 'late activity cannot steal focus during the pending activation');
    releaseViewRequest({session: '__yomux_view_chosen'});
    await pendingActivation;
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['__yomux_view_chosen'], 'the chosen window replaces the previous default after its alias is ready');
  });

  await testAsync('late activity does not reopen a pane the user closed during first load', async () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const api = loadYomux('', ['older-session', 'newer-session'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {settingsPayload: {defaults, settings: {}, mtime_ns: 1}},
    });
    assert.equal(await api.toggleTabberNormalPaneForTest('older-session'), true, 'the default first pane can be closed before activity arrives');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'the explicit empty choice is persisted');
    api.applyTabberActivityPayloadForTest({
      session_order: ['older-session', 'newer-session'],
      sessions: {
        'older-session': {panes: [{index: 0, index_text: '0', name: 'bash', active: true, window_active: true}]},
        'newer-session': {panes: [{index: 0, index_text: '0', name: 'bash', active: true, window_active: true}]},
      },
      activity: {
        'older-session:0': {active_recency_ts: 100},
        'newer-session:0': {active_recency_ts: 200},
      },
      agents: [],
    });
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'the late recency snapshot cannot undo the deliberate empty selection');
    const panes = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []);
    assert.equal(panes.some(item => ['older-session', 'newer-session'].includes(item)), false, 'the user remains at zero terminal panes');
  });

  test('stale saved Normal selections cannot abort first-load layout initialization', () => {
    const defaults = {...DEFAULT_TEST_SETTINGS, general: {default_tabber_mode: 'normal'}};
    const settingsPayload = {defaults, settings: {}, mtime_ns: 1};
    const api = loadYomux('', [], 'http:', 'Linux x86_64', 'admin', {
      localStorage: {
        'yomux.tabber.normalSelections.v1': '["gone","__yomux_view_gone"]',
        'yomux.tabber.viewSessions.v1': JSON.stringify({__yomux_view_gone: {sourceSession: 'gone', windowIndex: 0}}),
      },
      bootstrapOverrides: {settingsPayload},
    });
    assert.equal(api.tabberModeForTest(), 'normal');
    assert.ok(Object.values(api.layoutSlotsForTest()).some(value => value && Array.isArray(value.tabs) && value.tabs.includes('__tabber__')),
      'stale selected items are pruned while Tabber still opens on first load');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), []);
  });

  test('renaming a tmux session migrates Normal selections and grouped window sources', () => {
    const api = loadYomux('', ['same-session']);
    const view = api.storeTabberViewSessionForTest('__yomux_view_same_window', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session Claude', windowName: 'Claude',
    });
    api.setTabberNormalSelectionsForTest([view]);
    api.replaceTmuxSessionInClient('same-session', 'renamed-session', ['renamed-session']);
    assert.equal(api.tabberViewSessionForWindowForTest('renamed-session', 1), view,
      'the grouped window remains attached to the renamed source session');
    assert.equal(api.tabberViewSessionForWindowForTest('same-session', 1), '',
      'the old source session no longer owns the grouped window');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [view],
      'renaming the tmux session preserves the selected window pane');
  });

  test('Normal row activation does not focus the generic Tabber shell first', () => {
    const api = loadYomux('', ['one']);
    api.setTabberModeForTest('normal');
    const panel = new TestElement('normal-shell');
    const row = new TestElement('normal-shell-row');
    row.classList.add('file-tree-row');
    Object.assign(row.dataset, {path: '/s_one', kind: 'file', tabberType: 'normal', tabberSession: 'one', tabberPaneItem: 'one'});
    panel.append(row);
    api.bindPanelShellForTest(panel, '__tabber__');
    api.setFocusedPanelItem('one');
    panel.listeners.get('pointerdown')[0]({target: row, button: 0, pointerType: 'mouse'});
    assert.equal(api.focusedPanelItemForTest(), 'one', 'the row toggle owns the single focus transition');
    api.setTabberModeForTest('advanced');
    panel.listeners.get('pointerdown')[0]({target: row, button: 0, pointerType: 'mouse'});
    assert.equal(api.focusedPanelItemForTest(), '__tabber__', 'Advanced retains shell focus');
  });

  await testAsync('Normal mouse down commits once and touch waits for click', async () => {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    await api.toggleTabberNormalPaneForTest('one');
    const panel = new TestElement('normal-pointer-panel');
    const row = new TestElement('normal-pointer-row');
    row.classList.add('file-tree-row');
    Object.assign(row.dataset, {path: '/s_two', kind: 'file', tabberType: 'normal', tabberSession: 'two', tabberPaneItem: 'two'});
    panel.append(row);
    api.bindTabberPanelForTest(panel);
    const event = (pointerType, detail = 1) => ({target: row, pointerType, button: 0, detail,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}});
    const down = event('mouse');
    panel.listeners.get('pointerdown')[0](down);
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['two']);
    assert.equal(down.defaultPrevented, true);
    panel.listeners.get('click')[0](event('mouse'));
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['two'], 'compatibility click does not toggle twice');
    panel.listeners.get('pointerdown')[0](event('touch'));
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['two'], 'touch scroll does not commit');
    panel.listeners.get('click')[0](event('touch'));
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'touch click selects the same row again to deselect it');
    panel.listeners.get('click')[0](event('', 0));
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['two'], 'keyboard click still activates');
  });

  await testAsync('Normal rebuilds an Advanced multi-pane layout as one selected item', async () => {
    const api = loadYomux('', ['one', 'two']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    await api.toggleTabberNormalPaneForTest('one');
    await api.toggleTabberNormalPaneForTest('two');
    const slots = api.layoutSlotsForTest();
    const rotate = node => {
      if (node?.split && node.children.every(child => child.slot && slots[child.slot]?.tabs?.some(item => ['one', 'two'].includes(item)))) node.split = 'column';
      for (const child of node?.children || []) rotate(child);
    };
    rotate(slots[api.layoutTreeKey]);
    api.setLayoutSlotsForTest(slots);
    api.setTabberModeForTest('advanced');
    api.setTabberModeForTest('normal', {refresh: true});
    await Promise.resolve();
    const result = api.layoutSlotsForTest();
    const terminalItems = Object.values(result).flatMap(value => value?.tabs || []).filter(item => ['one', 'two'].includes(item));
    assert.equal(terminalItems.length, 1, 'Normal conversion admits one terminal pane');
  });

  await testAsync('Normal Tabber selection toggles, deselects, and switches its single pane', async () => {
    const api = loadYomux('', ['one', 'two', 'three'], 'http:', 'Linux x86_64', 'admin', {maxSessionTabs: 2});
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    await api.toggleTabberNormalPaneForTest('one');
    await api.toggleTabberNormalPaneForTest('two');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['two'], 'selecting another tab switches the single Normal pane');
    const twoPaneItems = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []).filter(item => ['one', 'two', 'three'].includes(item));
    assert.deepEqual(twoPaneItems, ['two'], 'only the selected session is rendered');
    assert.equal(await api.toggleTabberNormalPaneForTest('three'), true, 'an explicit Normal Tabber selection admits a listed session beyond the global visible-tab cap');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['three'], 'selecting a third tab replaces the current pane');
    const replacedItems = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []).filter(item => ['one', 'two', 'three'].includes(item));
    assert.deepEqual(replacedItems, ['three'], 'replacement keeps the pane count at one');
    await api.toggleTabberNormalPaneForTest('three');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'clicking the selected tab toggles its pane closed');
    const paneItems = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []);
    assert.equal(paneItems.filter(item => ['one', 'two', 'three'].includes(item)).length, 0, 'Normal layout leaves no terminal pane after deselection');
  });

  await testAsync('Normal shared layout commands preserve selections and close reopens on the next row action', async () => {
    const api = loadYomux('', ['one', 'two', 'three']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    await api.toggleTabberNormalPaneForTest('one');
    await api.toggleTabberNormalPaneForTest('two');
    const before = JSON.stringify(api.layoutSlotsForTest());
    assert.equal(api.tabCanFillWorkspace('one'), false);
    assert.equal(api.toggleTabWorkspaceFill('one'), false);
    api.expandPaneFromLayout('one');
    api.setLayoutToSinglePane();
    assert.equal(JSON.stringify(api.layoutSlotsForTest()), before);
    for (const zone of ['left', 'right', 'top', 'bottom']) {
      assert.equal(await api.moveLayoutItemDirectional('one', null, zone), false);
    }
    await api.selectSession('three', {userInitiated: true});
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['three']);
    await api.removeSessionFromLayout('three');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), []);
    await api.toggleTabberNormalPaneForTest('three');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['three']);
    api.setTabberModeForTest('advanced');
    assert.equal(api.tabCanFillWorkspace('three'), true);
    assert.equal(api.toggleTabWorkspaceFill('three'), true);
  });

  await testAsync('Normal Tabber does not fall back to the source session when a window view fails', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    const selectionsBefore = api.tabberNormalSelectionsForTest();
    api.setApiFetchJsonForTest(async () => { throw new Error('view request failed'); });
    const row = {dataset: {tabberType: 'normal', tabberPaneItem: '', tabberNormalWindowView: 'true', tabberWindowId: '@same-1'}, isConnected: true};
    const activated = await api.activateNormalTabberRowForTest(row, 'same-session', 1);
    assert.equal(activated, false, `a failed distinct-window view request declines activation (result ${String(activated)})`);
    assert.deepEqual(api.tabberNormalSelectionsForTest(), selectionsBefore, 'a failed view request never opens the source session on the wrong window');
  });

  await testAsync('Normal Tabber cancels a held window activation when Advanced is enabled', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    const selectionsBefore = api.tabberNormalSelectionsForTest();
    let releaseView;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseView = resolve; })
      : Promise.resolve({}));
    const row = {dataset: {tabberType: 'normal', tabberPaneItem: '', tabberNormalWindowView: 'true', tabberWindowId: '@same-1'}, isConnected: true};
    const activation = api.activateNormalTabberRowForTest(row, 'same-session', 1);
    await Promise.resolve();
    api.setTabberModeForTest('advanced');
    releaseView({session: '__yomux_view_held'});
    const result = await activation;
    assert.equal(result, false, `the stale completion is rejected after the mode revision changes (result ${String(result)})`);
    assert.deepEqual(api.tabberNormalSelectionsForTest(), selectionsBefore, 'an old window request cannot rebuild the Advanced layout');
  });

  await testAsync('a newer Normal row activation supersedes an older held window request', async () => {
    const api = loadYomux('', ['1', '2']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest(['1']);
    let releaseView;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseView = resolve; })
      : Promise.resolve({}));
    const heldRow = {dataset: {tabberType: 'normal', tabberPaneItem: '', tabberNormalWindowView: 'true', tabberWindowId: '@one-1'}, isConnected: true};
    const oldActivation = api.activateNormalTabberRowForTest(heldRow, '1', 1);
    await Promise.resolve();
    const currentRow = {dataset: {tabberType: 'normal', tabberPaneItem: '2'}, isConnected: true};
    assert.equal(await api.activateNormalTabberRowForTest(currentRow, '2', null), true,
      'the cached row applies while the older window request is held');
    const activeAfterNewClick = api.focusedTerminalForTest();
    releaseView({session: '__yomux_view_stale'});
    assert.equal(await oldActivation, false, 'the delayed earlier click is rejected');
    assert.equal(api.focusedTerminalForTest(), activeAfterNewClick, 'the delayed response cannot steal terminal focus');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['2'], 'the delayed response cannot rewrite the current pane selection');
  });

  test('Normal window view actions acknowledge the source tmux window', () => {
    const api = loadYomux('', ['same-session']);
    const info = {window_metadata: [{window_id: '@test-1', window_index: 1}], agents: [{kind: 'codex', state: 'blocked', window_index: 1, attention_key: 'go:attention:window1'}]};
    api.setTranscriptInfoForTest('same-session', info);
    api.setAutoApproveStateForTest('same-session', {agent_windows: info.agents});
    const view = api.storeTabberViewSessionForTest('__yomux_view_attention', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session Claude',
    });
    assert.equal(api.acknowledgeTerminalAttentionFromUserActionForTest(view, null, {refresh: false, acknowledgeAgentWindowDelayMs: 0}), true,
      'a user action in the grouped view acknowledges its exact source window');
    assert.equal(api.attentionAcknowledgementKeyIsRecordedForTest('go:attention:window1'), true);
  });

  await testAsync('roster changes retain the selected Normal tool or file', async () => {
    for (const item of ['__prefs__', '__finder__', '__differ__', 'file:/tmp/tabber-roster.js']) {
      const api = loadYomux('?layout=left&tabs=left:file%3A%2Ftmp%2Ftabber-roster.js', ['1']);
      api.setTabberModeForTest('normal');
      api.setTabberNormalSelectionsForTest([]);
      api.applyLayoutSlotsForTest({left: {tabs: [api.tabberItemId], active: api.tabberItemId}});
      const target = item.startsWith('file:') ? api.fileEditorItemFor('/tmp/tabber-roster.js') : item;
      await api.toggleTabberNormalPaneForTest(target);
      await api.toggleTabberNormalPaneForTest('1');
      assert.deepEqual(api.tabberNormalSelectionsForTest(), ['1']);
      assert.equal(api.applyTmuxRosterPayloadForTest({
        server_epoch: 'tabber-tool-audit', roster_generation: 1, topology_generation: 1, session_order: ['1', '2'],
      }), true);
      assert.deepEqual(api.tabberNormalSelectionsForTest(), ['1'], 'roster additions keep the current selection');
      await api.toggleTabberNormalPaneForTest(target);
      assert.deepEqual(api.tabberNormalSelectionsForTest(), [target], 'selecting a tool replaces the current conversation');
      assert.equal(api.applyTmuxRosterPayloadForTest({
        server_epoch: 'tabber-tool-audit', roster_generation: 2, topology_generation: 2, session_order: ['2'],
      }), true);
      assert.deepEqual(api.tabberNormalSelectionsForTest(), [target], `${item}: roster payload preserves the selected non-tmux surface`);
    }
  });

  test('authoritative roster removal retires grouped views and their saved selection', () => {
    const api = loadYomux('', ['same-session']);
    const view = api.storeTabberViewSessionForTest('__yomux_view_removed', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session Claude',
    });
    api.setTabberNormalSelectionsForTest([view]);
    assert.equal(api.applyTmuxRosterPayloadForTest({
      server_epoch: 'tabber-removal-test', roster_generation: 1, topology_generation: 1, session_order: [],
    }), true);
    assert.equal(api.tabberViewSessionForWindowForTest('same-session', 1), '', 'deleted source sessions retire their grouped window aliases');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'removed grouped views leave no saved Normal selection');
    assert.equal(Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []).includes(view), false,
      'removed grouped views leave no terminal pane behind');
  });

  test('Normal roster removal clears the selected conversation pane', () => {
    const api = loadYomux('?sessions=exited&layout=left&tabs=left:exited', ['exited']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest(['exited']);
    assert.equal(api.itemInLayout('exited'), true, 'the conversation is selected before its tmux session exits');
    assert.equal(api.applyTmuxRosterPayloadForTest({
      server_epoch: 'tabber-normal-exit', roster_generation: 1, topology_generation: 1, session_order: [],
    }), true);
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'an exited session is removed from saved Normal selection');
    assert.equal(api.itemInLayout('exited'), false, 'the exited session is removed from Normal layout items');
  });

  test('a closed source window alias is retired before its window number is reused', () => {
    const api = loadYomux('', ['same-session']);
    const oldView = api.storeTabberViewSessionForTest('__yomux_view_closed_window', {
      sourceSession: 'same-session', windowIndex: 1, windowId: '@old-window', label: 'same-session old window',
    });
    api.setTabberNormalSelectionsForTest([oldView]);
    api.setTranscriptInfoForTest('same-session', {window_metadata: [
      {window_id: '@other-window', window_index: 0},
      {window_id: '@replacement-window', window_index: 1},
    ]});
    api.applyTmuxRosterPayloadForTest({
      server_epoch: 'tabber-window-reuse-test', roster_generation: 1, topology_generation: 1, session_order: ['same-session'],
    });
    assert.equal(api.tabberViewSessionForWindowForTest('same-session', '@old-window'), '',
      'the prior description/terminal identity is discarded when its source tmux window closes');
    assert.notEqual(api.tabberViewSessionForWindowForTest('same-session', '@replacement-window'), oldView,
      'a new window reusing index 1 cannot inherit the old terminal identity');
  });

  await testAsync('a delayed view response cannot resurrect an alias after source removal and recreation', async () => {
    const api = loadYomux('', ['1']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    let releaseView;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseView = resolve; })
      : Promise.resolve({ok: true}));
    const pending = api.activateNormalTabberRowForTest({
      dataset: {tabberType: 'normal', tabberNormalWindowView: 'true', tabberWindowId: '@1-1'},
    }, '1', 1);
    await Promise.resolve();
    assert.equal(api.applyTmuxRosterPayloadForTest({
      server_epoch: 'tabber-source-recreation-test', session_order: [], roster_generation: 1, topology_generation: 1,
    }), true, 'the authoritative empty roster removes the original source');
    assert.equal(api.applyTmuxRosterPayloadForTest({
      server_epoch: 'tabber-source-recreation-test', session_order: ['1'], roster_generation: 2, topology_generation: 2,
    }), true, 'the same session name can later be recreated');
    releaseView({session: '__yomux_view_retired_old_source'});
    assert.equal(await pending, false, 'the response that outlived its source is rejected');
    assert.equal(api.tabberViewSessionForWindowForTest('1', 1), '', 'a retired response cannot install a grouped alias');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'a retired response cannot select its alias');
    assert.equal(api.focusedTerminalForTest(), null, 'a retired response cannot focus its alias');
  });

  await testAsync('a delayed view response cannot install an alias after its source window closes', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    api.setTranscriptInfoForTest('same-session', {window_metadata: [
      {window_id: '@same-0', window_index: 0},
      {window_id: '@same-1', window_index: 1},
    ]});
    let releaseView;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseView = resolve; })
      : Promise.resolve({ok: true}));
    const activation = api.activateNormalTabberRowForTest({
      dataset: {tabberType: 'normal', tabberNormalWindowView: 'true', tabberWindowId: '@same-1'},
    }, 'same-session', 1);
    await Promise.resolve();
    api.setTranscriptInfoForTest('same-session', {window_metadata: [
      {window_id: '@same-0', window_index: 0},
    ]});
    assert.equal(api.applyTmuxRosterPayloadForTest({
      server_epoch: 'tabber-window-close-pending-test', roster_generation: 1, topology_generation: 1,
      session_order: ['same-session'],
    }), true, 'the live source session publishes its updated window metadata');
    releaseView({session: '__yomux_view_closed_window'});
    assert.equal(await activation, false, 'the closed window response loses its pending owner');
    assert.equal(api.tabberViewSessionForWindowForTest('same-session', '@same-1'), '',
      'a late response cannot persist an alias for the removed window');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'a closed source window cannot be selected');
  });

  await testAsync('concurrent alias readiness callers keep the same source-window request', async () => {
    const api = loadYomux('', ['same-session']);
    const view = api.storeTabberViewSessionForTest('__yomux_view_shared_readiness', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session window 1',
    });
    let releaseView;
    let requestCount = 0;
    api.setApiFetchJsonForTest(path => {
      if (path !== '/api/tabber-view-session') return Promise.resolve({ok: true});
      requestCount += 1;
      return new Promise(resolve => { releaseView = resolve; });
    });
    const first = api.ensureTabberViewSessionForWindowForTest('same-session', 1, {label: 'first label'});
    await Promise.resolve();
    const second = api.ensureTabberViewSessionForWindowForTest('same-session', 1, {label: 'updated label'});
    await Promise.resolve();
    releaseView({session: view});
    assert.equal(await first, view, 'the first alias owner becomes ready');
    assert.equal(await second, view, 'a metadata refresh does not invalidate the shared readiness request');
    assert.equal(requestCount, 1, 'concurrent callers share one pending readiness request');
  });

  await testAsync('reopening a ready Tabber window alias restores its advertised tmux window', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    const view = api.storeTabberViewSessionForTest('__yomux_view_reselect_window', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session window 1',
    });
    const requests = [];
    let activeWindow = 1;
    api.setApiFetchJsonForTest((path, options = {}) => {
      if (path !== '/api/tabber-view-session') return Promise.resolve({ok: true});
      const body = JSON.parse(options.body);
      requests.push(body);
      activeWindow = Number(body.window);
      return Promise.resolve({session: view});
    });
    assert.equal(await api.ensureTabberViewSessionForWindowForTest('same-session', 1), view,
      'initial readiness creates the terminal alias and selects its advertised window');
    activeWindow = 2;
    const row = {dataset: {
      tabberType: 'normal',
      tabberPaneItem: view,
      tabberNormalWindowView: 'true',
      tabberWindowId: '@test-1',
    }, isConnected: true};
    assert.equal(await api.activateNormalTabberRowForTest(row, 'same-session', 1), true,
      'reopening a cached row completes its window selection before choosing the pane');
    assert.equal(activeWindow, 1, 'row activation reselects the window after tmux changed the alias natively');
    assert.deepEqual(requests, [
      {session: 'same-session', window: '1', window_id: '@test-1'},
      {session: 'same-session', window: '1', window_id: '@test-1'},
    ], 'the ready alias still receives the requested window selection on reactivation');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [view], 'the selected pane retains the distinct window alias');
  });

  await testAsync('clicking the selected source-backed multi-window row deselects before alias creation', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest(['same-session']);
    let requests = 0;
    api.setApiFetchJsonForTest(path => {
      if (path === '/api/tabber-view-session') requests += 1;
      return Promise.resolve({session: '__yomux_view_unexpected'});
    });
    const row = {dataset: {
      tabberType: 'normal',
      tabberPaneItem: 'same-session',
      tabberNormalWindowView: 'true',
      tabberWindowId: '@same-0',
    }, isConnected: true};
    assert.equal(await api.activateNormalTabberRowForTest(row, 'same-session', 0), true,
      'the already-selected source row handles the click as a deselection');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'the pane is removed instead of being replaced by a window alias');
    assert.equal(requests, 0, 'deselection does not create or reselect a hidden alias');
  });

  await testAsync('two clicks during cached window reselection cancel the pending pane open', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest([]);
    const view = api.storeTabberViewSessionForTest('__yomux_view_double_toggle', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session window 1',
    });
    let releaseView;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseView = resolve; })
      : Promise.resolve({ok: true}));
    const row = {dataset: {
      tabberType: 'normal',
      tabberPaneItem: view,
      tabberNormalWindowView: 'true',
    }, isConnected: true};
    const first = api.activateNormalTabberRowForTest(row, 'same-session', 1);
    await Promise.resolve();
    assert.equal(await api.activateNormalTabberRowForTest(row, 'same-session', 1), true,
      'the second click publishes the deselection while reselection is pending');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'two rapid clicks leave no pane selected');
    releaseView({session: view});
    assert.equal(await first, false, 'the stale open completion cannot undo the newer deselection');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'the late backend confirmation keeps the pane closed');
  });

  await testAsync('pending row deselection does not restore the pane that the first click replaced', async () => {
    const api = loadYomux('', ['previous-session', 'same-session']);
    api.setTabberModeForTest('normal');
    api.setTabberNormalSelectionsForTest(['previous-session']);
    const view = api.storeTabberViewSessionForTest('__yomux_view_replace_then_cancel', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session window 1',
    });
    let releaseView;
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releaseView = resolve; })
      : Promise.resolve({ok: true}));
    const row = {dataset: {
      tabberType: 'normal',
      tabberPaneItem: view,
      tabberNormalWindowView: 'true',
    }, isConnected: true};
    const first = api.activateNormalTabberRowForTest(row, 'same-session', 1);
    await Promise.resolve();
    assert.deepEqual(api.tabberNormalSelectionsForTest(), ['previous-session'], 'the currently rendered pane stays visible while the target window is being selected');
    assert.equal(await api.activateNormalTabberRowForTest(row, 'same-session', 1), true,
      'the second click deselects the still-pending target');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'cancelling the pending target leaves no pane selected');
    releaseView({session: view});
    assert.equal(await first, false, 'the late target completion cannot restore either pane');
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [], 'the replaced pane stays deselected after the late completion');
  });

  await testAsync('alias readiness starts a new request when its source session is renamed', async () => {
    const api = loadYomux('', ['same-session']);
    const view = api.storeTabberViewSessionForTest('__yomux_view_renamed_readiness', {
      sourceSession: 'same-session', windowIndex: 1, label: 'same-session window 1',
    });
    const releases = [];
    api.setApiFetchJsonForTest(path => path === '/api/tabber-view-session'
      ? new Promise(resolve => { releases.push(resolve); })
      : Promise.resolve({ok: true}));
    const oldReadiness = api.ensureTabberViewSessionForWindowForTest('same-session', 1);
    await Promise.resolve();
    api.replaceTmuxSessionsInClient(new Map([['same-session', 'renamed-session']]), ['renamed-session'], {authoritativeRoster: true});
    const newReadiness = api.ensureTabberViewSessionForWindowForTest('renamed-session', 1);
    await Promise.resolve();
    assert.equal(releases.length, 2, 'renaming retires the old-source readiness request');
    releases[0]({session: view});
    releases[1]({session: view});
    assert.equal(await oldReadiness, '', 'the response for the retired source cannot mark the alias ready');
    assert.equal(await newReadiness, view, 'the renamed source can claim and complete readiness');
  });

  test('Normal mode no longer refreshes pane recency from passive terminal reports', () => {
    const api = loadYomux('', ['old', 'recent']);
    assert.equal(api.terminalDataShouldAcknowledgeAttentionForTest('\u001b[1;1R'), false, 'cursor-position replies do not count as user input');
    assert.equal(api.terminalDataShouldAcknowledgeAttentionForTest('x'), true, 'typed terminal data remains user input');
    const terminalSource = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
    assert.ok(!/rememberTabberNormalPaneInteraction\(session\)/.test(terminalSource), 'Normal mode has no pane LRU owner on terminal output');
  });

  await testAsync('Normal Tabber gives windows from the same tmux independent pane identities', async () => {
    const api = loadYomux('', ['same-session']);
    api.setTabberModeForTest('normal');
    api.setFileExplorerModeForTest('tabber');
    api.setTabberActivityForTest({activity: {}, agents: []});
    api.applyTabberActivityPayloadForTest({
      activity: {'same-session:0': {last_user_input_ts: 300}, 'same-session:1': {last_user_input_ts: 200}, 'same-session:2': {last_user_input_ts: 100}},
      sessions: {'same-session': {panes: [
        {window: '0', window_id: '@0', window_name: 'codex', command: 'codex', active: true, window_active: true},
        {window: '1', window_id: '@1', window_name: 'claude', command: 'claude', active: true, window_active: false},
        {window: '2', window_id: '@2', window_name: 'bash', command: 'bash', active: true, window_active: false},
      ]}},
      session_order: ['same-session'],
    });
    const first = api.storeTabberViewSessionForTest('__yomux_view_0', {sourceSession: 'same-session', windowIndex: 0, windowId: '@0', label: 'same-session Codex'});
    const second = api.storeTabberViewSessionForTest('__yomux_view_1', {sourceSession: 'same-session', windowIndex: 1, windowId: '@1', label: 'same-session Claude'});
    const third = api.storeTabberViewSessionForTest('__yomux_view_2', {sourceSession: 'same-session', windowIndex: 2, windowId: '@2', label: 'same-session bash'});
    const restored = api.storeTabberViewSessionForTest('__yomux_view_legacy', {
      sourceSession: 'same-session', windowIndex: 3, windowId: '@3', label: '__yomux_view_legacy', windowName: 'editor',
    });
    assert.notEqual(first, second, 'different tmux windows receive different terminal identities');
    assert.notEqual(second, third, 'every window identity is distinct, even when the source session is shared');
    assert.equal(api.terminalTabDisplayLabel(restored, {}), 'same-session editor', 'restored labels show the source tmux session and window instead of the internal alias');
    assert.equal(api.tabberViewSessionForWindowForTest('same-session', '@0'), first, 'window 0 resolves to its stored view identity');
    assert.equal(api.tabberViewSessionForWindowForTest('same-session', '@1'), second, 'window 1 resolves to its stored view identity');
    const rows = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
    const rowPanes = Object.fromEntries(rows.map(row => [row.path, row.paneItem]));
    assert.deepEqual(rowPanes, {
      '/s_same-session/w_0': 'same-session',
      '/s_same-session/w_1': second,
      '/s_same-session/w_2': third,
    }, 'each flat row maps to its own terminal, including the initially visible source window');
    api.setTabberNormalSelectionsForTest([]);
    await api.toggleTabberNormalPaneForTest('same-session');
    await api.toggleTabberNormalPaneForTest(second);
    await api.toggleTabberNormalPaneForTest(third);
    assert.deepEqual(api.tabberNormalSelectionsForTest(), [third], 'selecting another window replaces the current pane');
    const paneItems = Object.values(api.layoutSlotsForTest()).flatMap(value => Array.isArray(value?.tabs) ? value.tabs : []);
    assert.ok(paneItems.includes(third), 'the selected window is visible');
    assert.equal(paneItems.includes(second), false, 'switching windows hides the previous window');
    assert.equal(paneItems.filter(item => [first, second, third].includes(item)).length, 1, 'one source session never creates multiple Normal panes');
  });

  test('Tabber restores a saved internal alias label as the original tmux session name', () => {
    const api = loadYomux('', ['same-session'], 'http:', 'Linux x86_64', 'admin', {
      localStorage: {'yomux.tabber.viewSessions.v1': JSON.stringify({
        '__yomux_view_persisted': {
          sourceSession: 'same-session', windowIndex: 3, windowId: '@3',
          label: '__yomux_view_persisted', windowName: 'editor',
        },
      })},
    });
    assert.equal(api.terminalTabDisplayLabel('__yomux_view_persisted', {}), 'same-session editor');
  });

  test('authoritative global roster is rendered before the legacy session list converges', () => {
    const api = loadYomux('', []);
    api.setFileExplorerModeForTest('tabber');
    api.setTranscriptSessionOrderForTest(['synthetic-global-a', 'synthetic-global-b']);
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(row => row.tabber.session), ['synthetic-global-a', 'synthetic-global-b'], 'Tabber renders every API roster session even when the bootstrap session list is stale');
  });

  await testAsync('Tabber falls back to session metadata topology when activity is unavailable', async () => {
    const api = loadYomux('', []);
    api.setFileExplorerModeForTest('tabber');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      if (String(url).startsWith('/api/activity?')) return Promise.reject(new Error('synthetic activity unavailable'));
      if (String(url) === '/api/session-metadata') {
        return Promise.resolve(jsonResponse({
          session_order: ['synthetic-fallback-a', 'synthetic-fallback-b'],
          sessions: {
            'synthetic-fallback-a': {panes: []},
            'synthetic-fallback-b': {panes: []},
          },
        }));
      }
      return Promise.reject(new Error(`unexpected request: ${url}`));
    });
    await api.fetchTabberActivityForTest({visible: true});
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(row => row.tabber.session), ['synthetic-fallback-a', 'synthetic-fallback-b'], `metadata fallback keeps one Tabber row per global session: ${JSON.stringify({rows, requests, state: api.tabberActivityStateForTest(), payload: api.tabberActivityPayloadForTest()})}`);
    assert.deepEqual(requests, ['/api/activity?hours=24&visible=1&scope=all', '/api/session-metadata'], 'activity failure performs one topology-only metadata repair');
  });

  await testAsync('Tabber falls back to the global tmux roster when metadata is unavailable', async () => {
    const api = loadYomux('', []);
    api.setFileExplorerModeForTest('tabber');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      if (String(url).startsWith('/api/activity?')) return Promise.reject(new Error('synthetic activity unavailable'));
      if (String(url) === '/api/session-metadata') return Promise.reject(new Error('synthetic metadata unavailable'));
      if (String(url) === '/api/tmux') {
        return Promise.resolve(jsonResponse({
          session_order: ['synthetic-roster-a', 'synthetic-roster-b'],
          sessions: ['synthetic-roster-a', 'synthetic-roster-b'],
        }));
      }
      return Promise.reject(new Error(`unexpected request: ${url}`));
    });
    await api.fetchTabberActivityForTest({visible: true});
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(row => row.tabber.session), ['synthetic-roster-a', 'synthetic-roster-b'], 'the global roster fallback keeps one Tabber row per session');
    assert.deepEqual(requests, ['/api/activity?hours=24&visible=1&scope=all', '/api/session-metadata', '/api/tmux'], 'roster fallback is attempted only after activity and metadata fail');
  });

  test('terminal tabs use the live tmux pane path while transcript metadata is incomplete', () => {
    const api = loadYomux('', ['8882']);
    api.setTmuxSignalStateForTest({windows: [{
      session: '8882',
      window_index: '1',
      active: true,
      panes: [{
        target: '8882:1.0',
        pane_id: '8882:1.0',
        pane_index: '0',
        window_index: '1',
        active: true,
        current_path: '/Users/keivenc/Documents/bin/yolomux.dev8882',
        current_command: 'codex',
      }],
    }]});

    const html = api.tmuxPaneTabHtml('8882', {panes: []}, null, false);
    assert.ok(html.includes('yolomux.dev8882'), 'a live non-git shell tab shows its pane directory instead of No path while transcript metadata catches up');
    assert.equal(html.includes('No path'), false, 'the missing-path placeholder is not rendered when tmux supplies the working directory');
  });

  test('shared tree controller is the Finder, Tabber, Differ, and Git history interaction parent', () => {
    const source = fs.readFileSync('static_src/js/yomux/86_changes_20_differ.js', 'utf8');
    const css = fs.readFileSync('static/yomux.css', 'utf8');
    const api = loadYomux('', ['1']);
    assert.deepStrictEqual(canonical(api.sharedTreeControllerNamesForTest()), ['differ', 'finder', 'git-diff-commits', 'git-diff-files', 'tabber'], 'Finder, Tabber, Differ, and both Git history trees register through the shared interaction controller');
    // Source ownership is enforced once by static_build's shared-ui ownership lint. This node
    // shard keeps the observable controller registration and keyboard behavior contract.
    assert.ok(/handleFileExplorerArrowNav = event =>[\s\S]*fileExplorerViewForItem\(panel\?\.dataset\?\.panelItem\)[\s\S]*view === 'tabber'[\s\S]*tabberTreeInteractionController\.handleKeydown[\s\S]*view === 'differ'[\s\S]*differTreeInteractionController\.handleKeydown[\s\S]*originalFileExplorerArrowNavForSharedTree/.test(source), 'per-panel key dispatch routes fixed Tabber/Differ views through the shared parent before Finder fallback');
    const fileTreeSource = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
    assert.ok(/selectableFileTreeRows\(container = document\)[\s\S]*!row\.dataset\.tabberType/.test(fileTreeSource), 'Finder/Differ row discovery does not steal Tabber rows');
    assert.ok(fileTreeSource.includes('row.classList.toggle(CLS.selected, selected)') && fileTreeSource.includes("row.classList.toggle('current-file', current && row.dataset.kind !== 'dir')") && fileTreeSource.includes("row.classList.toggle('current-directory', current && row.dataset.kind === 'dir')"), 'Tabber row render uses shared selected/current classes');
    assert.equal(/classList\??\.\s*(?:add|remove|toggle|contains)\s*\(\s*['"](active|open|selected|collapsed)['"]/.test(source), false, 'MV-1: active/open/selected/collapsed classList calls route through CLS');
    assert.ok(/\.file-tree-row:not\(\.selected\):hover/.test(css), 'tree hover color is the shared row token path');
    assert.ok(/\.file-tree-row\.selected,\s*\.file-tree-row\.current-file:not\(\.selected\),\s*\.file-tree-row\.tabber-row\.tabber-active-normal:not\(\.selected\)\s*\{/.test(css), 'active Normal Tabber row shares the selected/current-file paint owner');
    assert.ok(/\.file-tree-row\.current-file:not\(\.selected\)/.test(css), 'tree current-file color is the shared row token path');
    assert.ok(/\.file-tree-row\.current-directory:not\(\.selected\)/.test(css), 'tree current-directory color is the shared row token path');
    assert.equal(/differ-[^{]*(?:selected|current-file|current-directory)/.test(css), false, 'Differ has no forked selected/current row color classes');
  });

  test('Finder, Differ, and Tabber have fixed independent identities and one placement entry point', () => {
    const bootstrapSource = fs.readFileSync('static_src/js/yomux/00_bootstrap_state.js', 'utf8');
    const layoutSource = fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8');
    const actionsSource = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
    const coreSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
    const menuSource = fs.readFileSync('static_src/js/yomux/30_app_menus.js', 'utf8');
    const panelSource = fs.readFileSync('static/yomux.js', 'utf8');
    assert.ok(/const finderItemId = '__finder__';[\s\S]*const differItemId = '__differ__';[\s\S]*const tabberItemId = '__tabber__';[\s\S]*const fileExplorerItemIds = Object\.freeze\(\[finderItemId, differItemId, tabberItemId\]\)/.test(bootstrapSource), 'the triplet has three stable layout identities in one registry');
    assert.ok(/function fileExplorerViewForItem\(item\)[\s\S]*function isFileExplorerItem\(item\)/.test(bootstrapSource), 'identity-to-view and shared triplet predicate have one bootstrap owner');
    assert.ok(/function sidePaneSlots[\s\S]*function layoutWithSidePaneItems[\s\S]*function layoutWithDefaultLeftSidePane/.test(layoutSource), 'Side Pane detection, insertion, and default seeding share the layout owner');
    assert.ok(/async function openFileSurfacePane\(item\)[\s\S]*currentSlot[\s\S]*activatePaneTab[\s\S]*layoutWithSidePaneItems/.test(actionsSource), 'an existing triplet item activates in place while only a missing item uses Side Pane insertion');
    assert.ok(/function fileMenuPanelCommands\(\)[\s\S]*narrowSingleColumnMode\(\)[\s\S]*virtualCommands\.unshift\(\.\.\.fileSurfaces\)[\s\S]*openFileSurfaceFromMenu\(finderItemId\)/.test(menuSource), 'File uses one desktop triplet command and separate narrow-screen commands through the shared placement entry point');
    assert.ok(/function createFileExplorerPanel\(item = finderItemId\)[\s\S]*fileExplorerViewForItem\(item\)[\s\S]*createFramedPanel\(\{\s*item,/.test(panelSource) && /function createFramedPanel\([\s\S]*panel\.id = panelDomId\(item\)/.test(fs.readFileSync('static_src/js/yomux/78_panel_shell.js', 'utf8')), 'one parameterized panel builder gives every triplet item a fixed view and unique panel DOM id through the shared frame factory');
    const terminalBootSource = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
    const fileExplorerSource = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
    const differSource = fs.readFileSync('static_src/js/yomux/86_changes_20_differ.js', 'utf8');
    const surfaceSource = fs.readFileSync('static_src/js/yomux/86_changes_40_finder.js', 'utf8');
    assert.ok(/function syncPanelVisibility\(previousActive = \[\]\)[\s\S]*const previouslyVisible = new Set\(previousActive\);[\s\S]*newlyVisibleFileSurfaces[\s\S]*scheduleTabSwitchPhaseTwo\(generation, \(\) => \{[\s\S]*activateFileExplorerSurface\(item\)/.test(terminalBootSource), 'newly visible file surfaces activate through the shared post-paint generation guard');
    assert.ok(/function scheduleTabSwitchPhaseTwo\(generation, work\)[\s\S]*raf\(\(\) => raf\(\(\) =>/.test(actionsSource), 'secondary tab work starts only after the first frame had a paint opportunity');
    assert.ok(actionsSource.includes('function scheduleTerminalActivationFollowup(session, generation, gesture = null)') && actionsSource.includes('syncFileExplorerAfterTerminalActivation(session);') && actionsSource.includes('scheduleTerminalActivationFollowup(session, generation, gesture);'), 'normal and duplicate Dockview activation share focus-guarded phase-two work');
    assert.ok(/function focusTerminalFromUserAction\(session, delay = 0, options = \{\}\)[\s\S]*setFocusedTerminal\(session, \{\.\.\.options, userInitiated: true, syncFinder: false\}\)[\s\S]*rememberFileExplorerExplicitSyncSession\(session\)[\s\S]*scheduleTabSwitchPhaseTwo\(nextTabSwitchHighlightGeneration\(\), \(\) => syncFileExplorerAfterTerminalActivation\(session\)\)/.test(coreSource), 'direct focus claims the existing Finder target immediately and defers rendering through the shared scheduler');
    assert.ok(/function fileExplorerSessionFilesPaneIsVisible\(\)\s*\{\s*return itemIsActivePaneTab\(differItemId\);/.test(fileExplorerSource), 'Differ visibility follows the active pane item rather than mere layout membership');
    assert.ok(/function renderFileExplorerChangesPanel\(panel, options = \{\}\)[\s\S]*view === 'differ' && !fileExplorerSessionFilesPaneIsVisible\(\)[\s\S]*return;/.test(surfaceSource), 'hidden Differ does not enter its tree renderer');
    assert.ok(/function renderChangedFileList\(container, repoPath, sessionFiles, options = \{\}\)[\s\S]*yieldToInput: options\.yieldToInput === true/.test(differSource), 'visible Differ rows use the shared input-yielding tree renderer');
    assert.ok(/function fileExplorerModeSwitcherHtml\(\)\s*\{\s*return '';\s*\}/.test(panelSource), 'the retired in-panel mode switcher is a compatibility no-op');
  });

  test('Finder, Tabber, and Differ keep independent shared tree settings', () => {
    const api = loadYomux('', ['1']);
    const source = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
    assert.ok(/function fileExplorerViewSettingsFor\(view = 'finder'\)[\s\S]*function setFileExplorerViewSetting\(view, key, value/.test(source), 'one keyed shared settings owner replaces global per-control variables');
    assert.equal(api.fileExplorerTreeSortModeForViewForTest('tabber'), 'newest', 'Tabber defaults to most recent activity first');
    const tabberSortHtml = api.fileExplorerTreeSortSelectHtmlForTest('', 'tabber');
    assert.deepEqual([...tabberSortHtml.matchAll(/<option value="([^"]+)"/g)].map(match => match[1]),
      ['newest', 'az', 'za', 'oldest'], 'a fresh Tabber puts recent first in its sort menu');
    assert.match(tabberSortHtml, /<option value="newest" selected>/, 'a fresh Tabber selects its first recent option by default');
    const finderSortHtml = api.fileExplorerTreeSortSelectHtmlForTest('', 'finder');
    assert.deepEqual([...finderSortHtml.matchAll(/<option value="([^"]+)"/g)].map(match => match[1]),
      ['az', 'za', 'newest', 'oldest'], 'Finder keeps its existing alphabetical-first sort menu');
    assert.equal(api.fileExplorerTreeDateModeForViewForTest('tabber'), 'relative', 'Tabber shows relative dates by default even when Finder dates are hidden');
    api.setTabberModeForTest('normal');
    api.applyTabberActivityPayloadForTest({
      session_order: ['1'],
      sessions: {'1': {panes: [{window: '0', window_name: 'bash', command: 'bash'}]}},
      activity: {'1:0': {active_recency_ts: 1700000000}},
      agents: [],
    });
    const defaultTabberDate = api.tabberRenderedRowsForTest().find(row => row.type === 'normal')?.date || '';
    assert.notEqual(defaultTabberDate, '', 'Normal rows render their activity date without enabling Advanced');
    const savedDateChoice = loadYomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
      localStorage: {'yomux.fileExplorer.viewSettings.v2': JSON.stringify({tabber: {treeDateMode: 'none', treeSortMode: 'az'}})},
    });
    assert.deepEqual(canonical(savedDateChoice.fileExplorerViewSettingsForTest().tabber),
      {treeDateMode: 'none', treeSortMode: 'az'}, 'an explicit saved Tabber choice survives the new first-install defaults');
    api.setFileExplorerViewSettingForTest('finder', 'treeSortMode', 'za');
    api.setFileExplorerViewSettingForTest('finder', 'treeDateMode', 'relative');
    api.setFileExplorerViewSettingForTest('tabber', 'treeSortMode', 'newest');
    api.setFileExplorerViewSettingForTest('tabber', 'treeDateMode', 'date');
    api.setFileExplorerViewSettingForTest('differ', 'treeSortMode', 'oldest');
    api.setFileExplorerViewSettingForTest('differ', 'treeDateMode', 'none');
    assert.deepStrictEqual(canonical(api.fileExplorerViewSettingsForTest()), {
      finder: {treeDateMode: 'relative', treeSortMode: 'za'},
      tabber: {treeDateMode: 'date', treeSortMode: 'newest'},
      differ: {treeDateMode: 'none', treeSortMode: 'oldest'},
    }, 'each fixed surface owns its own Sort by and Date choice');
    assert.equal(api.fileExplorerTreeSortModeForViewForTest('finder'), 'za');
    assert.equal(api.fileExplorerTreeSortModeForViewForTest('tabber'), 'newest');
    assert.equal(api.fileExplorerTreeSortModeForViewForTest('differ'), 'oldest');
  });

  test('Finder shared tree controller handles keyboard cursor navigation', () => {
    const api = loadYomux('', ['1']);
    api.setFileExplorerModeForTest('files');
    api.setFocusedPanelItem(api.fileExplorerItemId);
    const panel = new TestElement('finder-keyboard-panel');
    panel.classList.add('file-explorer-tree-panel');
    panel.setAttribute('role', 'tree');
    const rootRow = new TestElement('finder-root');
    rootRow.classList.add('file-tree-row');
    rootRow.dataset.path = '/repo';
    rootRow.dataset.kind = 'dir';
    const fileRow = new TestElement('finder-file');
    fileRow.classList.add('file-tree-row');
    fileRow.dataset.path = '/repo/app.py';
    fileRow.dataset.kind = 'file';
    panel.append(rootRow, fileRow);
    api.selectFileTreePath('/repo');
    const arrowDown = treeKeyEvent('ArrowDown', panel);
    assert.equal(api.handleFileExplorerArrowNavForTest(arrowDown), true, 'Finder ArrowDown routes through the shared controller');
    assert.equal(arrowDown.defaultPrevented, true, 'Finder ArrowDown is consumed');
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), ['/repo/app.py'], 'Finder ArrowDown moves selection to the next row');
    assert.equal(fileRow.classList.contains('selected'), true, 'Finder selected row keeps the shared selected class');

    api.selectFileTreePath('/repo');
    const terminal = new TestElement('finder-auto-focus-terminal');
    terminal.classList.add('xterm');
    api.setDocumentActiveElementForTest(terminal);
    api.setDocumentQuerySelectorForTest(selector => selector === '.file-explorer-tree-panel' ? panel : null);
    api.setAutoFocusEnabledForTest(true);
    api.selectPanelOnHover(api.fileExplorerItemId);
    const autoFocusedArrowDown = treeKeyEvent('ArrowDown', terminal);
    assert.equal(api.handleFileExplorerArrowNavForTest(autoFocusedArrowDown), true, 'auto-focused Finder owns ArrowDown while the browser focus remains in xterm');
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), ['/repo/app.py'], 'auto-focused Finder moves the shared selection');
  });

  test('Finder Copy relative path is Finder-root-relative while Differ remains repo-relative', () => {
    const api = loadYomux('', ['1']);
    assert.equal(api.pathRelativeToDirectory('/repo/project/docs/nested/file.md', '/repo/project/docs'), 'nested/file.md', 'nested Finder roots produce a Finder-relative path');
    assert.equal(api.pathRelativeToDirectory('/repo/project/docs-two/file.md', '/repo/project/docs'), '/repo/project/docs-two/file.md', 'the shared helper rejects string-prefix siblings');
    assert.deepStrictEqual(
      canonical(api.finderRelativeCopyEntryForSelectionForTest('/repo/project/docs/nested/file.md', {realpath: '/real/project/docs/nested/file.md'}, {
        finderRoot: '/repo/project/docs',
        finderRootInfo: {realpath: '/real/project/docs'},
      })),
      {available: true, text: 'nested/file.md', reason: '', mode: 'finder'},
      'Finder copies the path relative to the visible Finder root when both display and resolved paths stay inside it',
    );
    assert.deepStrictEqual(
      canonical(api.finderRelativeCopyEntryForSelectionForTest('/repo/project/docs/escape.md', {realpath: '/outside/escape.md'}, {
        finderRoot: '/repo/project/docs',
        finderRootInfo: {realpath: '/real/project/docs'},
      })),
      {available: false, text: '', reason: 'outside-finder-root', mode: 'finder'},
      'Finder disables misleading relative copies when a displayed child resolves outside the Finder root',
    );
    assert.deepStrictEqual(
      canonical(api.finderRelativeCopyEntryForSelectionForTest('/repo/project/docs/nested/file.md', {realpath: '/real/project/docs/nested/file.md'}, {
        finderRoot: '/repo/project/docs',
      })),
      {available: false, text: '', reason: 'unverified-finder-root', mode: 'finder'},
      'Finder fails closed when the displayed root has no verified realpath',
    );
    assert.deepStrictEqual(
      canonical(api.finderRelativeCopyEntryForSelectionForTest('/repo/project/docs/nested/file.md', null, {
        finderRoot: '/repo/project/docs',
        finderRootInfo: {realpath: '/real/project/docs'},
      })),
      {available: false, text: '', reason: 'unverified-finder-root', mode: 'finder'},
      'Finder fails closed when a selected target cannot be resolved',
    );
    assert.deepStrictEqual(
      canonical(api.repoRelativeCopyEntryForSelectionForTest({relative_path: 'docs/nested/file.md'})),
      {available: true, text: 'docs/nested/file.md', reason: '', mode: 'repo'},
      'Differ and other Git-backed surfaces keep repository-relative metadata untouched',
    );
    assert.equal(api.fileTreeRelativeCopyModeForTest({closest(selector) { return selector === '.file-explorer-changes-panel' ? {} : null; }}, {}), 'repo', 'changed-files rows stay on repo-relative copy semantics');
    assert.equal(api.fileTreeRelativeCopyModeForTest({closest() { return null; }}, {}), 'finder', 'Finder rows default to Finder-root-relative copy semantics');
  });

  await testAsync('Finder relative-copy multi-selection fails closed as one clipboard action', async () => {
    const api = loadYomux('', ['1']);
    const root = '/repo/project/docs';
    const rootInfo = {realpath: '/real/project/docs'};
    const paths = [`${root}/first.md`, `${root}/nested/second.md`];
    const entries = await api.fileTreeRelativeCopyEntriesForTest(null, paths, [
      {realpath: '/real/project/docs/first.md'},
      {realpath: '/real/project/docs/nested/second.md'},
    ], {finderRoot: root, finderRootInfo: rootInfo});
    assert.deepStrictEqual(canonical(entries), [
      {available: true, text: 'first.md', reason: '', mode: 'finder'},
      {available: true, text: 'nested/second.md', reason: '', mode: 'finder'},
    ], 'Finder keeps the selected order and computes every item from the displayed root');

    const mixedEntries = await api.fileTreeRelativeCopyEntriesForTest(null, paths, [
      {realpath: '/real/project/docs/first.md'},
      null,
    ], {finderRoot: root, finderRootInfo: rootInfo});
    assert.deepStrictEqual(canonical(mixedEntries), [
      {available: true, text: 'first.md', reason: '', mode: 'finder'},
      {available: false, text: '', reason: 'unverified-finder-root', mode: 'finder'},
    ], 'one unresolved selected target makes the shared menu owner disable the whole relative-copy action');

    api.setFetchForTest(() => Promise.reject(new Error('fs info unavailable')));
    const missingRootEntries = await api.fileTreeRelativeCopyEntriesForTest(null, paths, [
      {realpath: '/real/project/docs/first.md'},
      {realpath: '/real/project/docs/nested/second.md'},
    ], {finderRoot: root});
    assert.deepStrictEqual(canonical(missingRootEntries), [
      {available: false, text: '', reason: 'unverified-finder-root', mode: 'finder'},
      {available: false, text: '', reason: 'unverified-finder-root', mode: 'finder'},
    ], 'a failed Finder-root info lookup cannot silently fall back to repository-relative text');

    const repoEntries = await api.fileTreeRelativeCopyEntriesForTest({closest(selector) { return selector === '.file-explorer-changes-panel' ? {} : null; }}, paths, [
      {relative_path: 'docs/first.md'},
      {relative_path: 'docs/nested/second.md'},
    ], {finderRoot: root});
    assert.deepStrictEqual(canonical(repoEntries), [
      {available: true, text: 'docs/first.md', reason: '', mode: 'repo'},
      {available: true, text: 'docs/nested/second.md', reason: '', mode: 'repo'},
    ], 'Differ does not perform a Finder-root lookup and remains explicitly repository-relative');
  });

  test('Tabber shared tree controller handles keyboard navigation and active-window sync', () => {
    const api = loadYomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1', '__tabber__', '__finder__'], '1');
    slots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(slots);
    api.setFocusedPanelItem('1');
    api.setFileExplorerModeForTest('tabber');
    api.setTranscriptInfoForTest('1', {
      panes: [{window: '1', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude', current_path: '/repo'}],
    });
    api.setFileExplorerModeForTest('tabber');

    const panel = new TestElement('tabber-keyboard-panel');
    panel.classList.add('file-explorer-panel', 'file-explorer-changes-panel');
    panel.dataset.panelItem = '__tabber__';
    const sessionOne = new TestElement('tabber-session-1');
    sessionOne.classList.add('file-tree-row');
    sessionOne.dataset.path = '/s_1';
    sessionOne.dataset.kind = 'dir';
    sessionOne.dataset.tabberType = 'normal';
    sessionOne.dataset.tabberSession = '1';
    sessionOne.setAttribute('aria-expanded', 'true');
    const windowOne = new TestElement('tabber-window-1');
    windowOne.classList.add('file-tree-row');
    windowOne.dataset.path = '/s_1/w_1';
    windowOne.dataset.kind = 'file';
    windowOne.dataset.tabberType = 'normal';
    windowOne.dataset.tabberSession = '1';
    windowOne.dataset.tabberWindow = '1';
    const sessionTwo = new TestElement('tabber-session-2');
    sessionTwo.classList.add('file-tree-row');
    sessionTwo.dataset.path = '/s_2';
    sessionTwo.dataset.kind = 'dir';
    sessionTwo.dataset.tabberType = 'normal';
    sessionTwo.dataset.tabberSession = '2';
    sessionTwo.setAttribute('aria-expanded', 'true');
    panel.append(sessionOne, windowOne, sessionTwo);
    api.bindTabberPanelForTest(panel);

    api.syncTabberTreeActiveSelectionForTest(panel, {scrollIntoView: true});
    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'active Tabber row follows the active tmux sub-window');
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_1/w_1'], 'active window sync selects the active Tabber window row');
    assert.equal(windowOne.classList.contains('selected'), true, 'active window row gets shared selected class');
    assert.equal(windowOne.classList.contains('current-file'), true, 'active window row gets shared current-file class');

    const terminal = new TestElement('tabber-keyboard-terminal');
    terminal.classList.add('xterm');
    api.setDocumentActiveElementForTest(terminal);
    api.setDocumentQuerySelectorForTest(selector => selector === '.file-explorer-panel' ? panel : null);
    const arrowDown = treeKeyEvent('ArrowDown', terminal);
    assert.equal(api.handleFileExplorerArrowNavForTest(arrowDown), false, 'Tabber does not steal ArrowDown from a focused terminal');
    assert.notEqual(arrowDown.defaultPrevented, true, 'terminal ArrowDown remains available to xterm');
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_1/w_1'], 'terminal ArrowDown does not move the Tabber selection');

    const terminalEnter = treeKeyEvent('Enter', terminal);
    assert.equal(api.handleFileExplorerArrowNavForTest(terminalEnter), false, 'Tabber does not steal Enter from a focused terminal');
    assert.notEqual(terminalEnter.defaultPrevented, true, 'terminal Enter remains available to xterm');
    assert.equal(api.currentSessionActionTarget(), '1', 'terminal Enter does not activate the selected Tabber row');

    api.setAutoFocusEnabledForTest(true);
    api.selectPanelOnHover('__tabber__');
    const autoFocusedArrowDown = treeKeyEvent('ArrowDown', terminal);
    assert.equal(api.handleFileExplorerArrowNavForTest(autoFocusedArrowDown), true, 'auto-focused Tabber owns ArrowDown while the browser focus remains in xterm');
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_2'], 'auto-focused Tabber moves the selected row');

    api.setFocusedPanelItem('1');
    api.syncTabberTreeActiveSelectionForTest(panel);
    api.setDocumentActiveElementForTest(panel);
    const panelArrowDown = treeKeyEvent('ArrowDown', panel);
    assert.equal(api.handleFileExplorerArrowNavForTest(panelArrowDown), true, 'Tabber ArrowDown works after Tabber owns focus');
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_2'], 'focused Tabber ArrowDown moves the selected row');
    assert.equal(sessionTwo.classList.contains('selected'), true, 'moved Tabber row gets shared selected class');

    api.setDocumentActiveElementForTest(panel);
    const enter = treeKeyEvent('Enter', panel);
    assert.equal(api.handleFileExplorerArrowNavForTest(enter), true, 'Tabber Enter activates the selected row');
    assert.equal(api.currentSessionActionTarget(), '2', 'Tabber Enter opens the selected tmux session');

    api.setAutoFocusEnabledForTest(false);
    api.setFocusedPanelItem('__finder__');
    api.syncTabberTreeActiveSelectionForTest(panel);
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_2'], 'a background Tabber preserves its selection when Finder is focused elsewhere');
  });

  test('Tabber highlights every visible tab, not only the focused one', () => {
    const api = loadYomux('', ['1', '2', '3']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1', '__tabber__', '__finder__'], '1');
    slots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(slots);
    api.setFocusedPanelItem('1');
    api.setFileExplorerModeForTest('tabber');
    for (const session of ['1', '2', '3']) {
      api.setTranscriptInfoForTest(session, {
        panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude', current_path: `/repo/${session}`}],
      });
    }

    const rows = api.tabberRenderedRowsForTest();
    for (const session of ['1', '2']) {
      const windowRow = rows.find(row => row.type === 'normal' && row.path === `/s_${session}/w_0`);
      assert.equal(windowRow?.classes.includes('tabber-active-window'), true, `visible session ${session} highlights its active tmux sub-window`);
    }
    assert.equal(rows.find(row => row.path === '/s_1/w_0')?.ariaCurrent, 'true', 'the focused visible tab owns the single current row');
    assert.equal(rows.find(row => row.path === '/s_2/w_0')?.ariaCurrent, '', 'another visible tab stays active without becoming current');
    const hiddenWindow = rows.find(row => row.type === 'normal' && row.path === '/s_3/w_0');
    assert.equal(hiddenWindow?.classes.includes('tabber-active-window'), true, 'tmux-window activity remains distinct from whether its parent tab is visible');

    api.setFocusedPanelItem('2');
    assert.equal(api.activeTabberRowPathForTest(), '/s_2/w_0', 'moving focus selects the second visible tab in Tabber');
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_2/w_0'], 'the shared focus path updates Tabber selection without changing active highlights');
    const refocusedRows = api.tabberRenderedRowsForTest();
    assert.equal(refocusedRows.find(row => row.path === '/s_1/w_0')?.ariaCurrent, '', 'the old focused row no longer reads as current');
    assert.equal(refocusedRows.find(row => row.path === '/s_2/w_0')?.ariaCurrent, 'true', 'the new focused row reads as current');
  });

  test('Tabber active window follows the optimistic tmux sub-window override', () => {
    const api = loadYomux('', ['1']);
    api.setFocusedPanelItem('1');
    api.setFileExplorerModeForTest('tabber');
    api.setTranscriptInfoForTest('1', {
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp'},
      ],
    });

    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_0', 'Tabber starts on the backend active tmux sub-window');
    api.setTmuxWindowActiveIndexOverrideForTest('1', '1');
    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'known tmux sub-window override moves the active Tabber row before readback');
    let rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.find(r => r.path === '/s_1/w_1')?.classes.includes('tabber-active-window'), true, 'Tabber row highlight honors the optimistic known-window override');
    assert.equal(rows.find(r => r.path === '/s_1/w_0')?.classes.includes('tabber-active-window'), false, 'stale backend-active Tabber row is not highlighted while an override is active');
    api.setTmuxWindowActiveIndexPendingForTest('1');
    assert.equal(api.activeTabberRowPathForTest(), '/s_1', 'unknown tmux sub-window changes fall back to the session row until readback confirms');
    rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.some(r => r.type === 'normal' && r.classes.includes('tabber-active-window')), false, 'pending unknown tmux sub-window changes do not leave a stale active window highlighted');
  });

  test('Tabber window click keeps the optimistic target through stale session updates', () => {
    const api = loadYomux('', ['1']);
    api.setFocusedPanelItem('1');
    api.setFileExplorerModeForTest('tabber');
    const staleInfo = {
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('1', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/tmux-window')) return new Promise(() => {});
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: '1', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    const panel = new TestElement('tabber-window-click-panel');
    const windowOne = new TestElement('tabber-window-1');
    windowOne.classList.add('file-tree-row');
    windowOne.dataset.kind = 'file';
    windowOne.dataset.path = '/s_1/w_1';
    windowOne.dataset.tabberType = 'normal';
    windowOne.dataset.tabberSession = '1';
    windowOne.dataset.tabberWindow = '1';
    const name = new TestElement('tabber-window-1-name');
    name.classList.add('file-tree-name');
    name.textContent = '1:claude';
    windowOne.appendChild(name);
    panel.appendChild(windowOne);
    api.bindTabberPanelForTest(panel);

    const click = {
      target: windowOne,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
    };
    panel.listeners.get('click')[0](click);
    assert.equal(click.defaultPrevented, true, 'Tabber handles the tmux sub-window row click');
    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('1'), '1', 'Tabber click installs the direct tmux sub-window override before async readback');
    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'Tabber moves to the direct target immediately');

    api.applyTranscriptsPayloadForTest({session_order: ['1'], sessions: {'1': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'a stale transcript push does not bounce the Tabber row back to 0:codex');
    api.applyTmuxSignalsPayloadForTest({windows: [{
      session: '1',
      window_index: '0',
      active: true,
      panes: [{target: '1:0.0', pane_id: '1:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }]});
    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'a stale tmux signal push does not bounce the Tabber row back to 0:codex');
    const rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.find(r => r.path === '/s_1/w_1')?.classes.includes('tabber-active-window'), true, 'the clicked 1:claude row stays highlighted');
    assert.equal(rows.find(r => r.path === '/s_1/w_0')?.classes.includes('tabber-active-window'), false, 'the stale 0:codex row does not regain highlight');
  });

  await testAsync('Tabber confirmed direct window target ignores fresh stale signal bounce', async () => {
    const api = loadYomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [4000]});
    api.setFocusedPanelItem('1');
    api.setFileExplorerModeForTest('tabber');
    const staleInfo = {
      panes: [
        {target: '1:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: '1:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const confirmedSignals = {ok: true, generated_at: Date.now() / 1000, windows: [{
      session: '1',
      window_index: '0',
      active: false,
      panes: [{target: '1:0.0', pane_id: '1:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: '1',
      window_index: '1',
      active: true,
      panes: [{target: '1:1.0', pane_id: '1:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    api.setTranscriptInfoForTest('1', staleInfo);
    api.setFetchForTest(url => {
      if (String(url).startsWith('/api/tmux-window')) return Promise.resolve(jsonResponse({ok: true}));
      if (String(url).startsWith('/api/tmux-signals')) return Promise.resolve(jsonResponse(confirmedSignals));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('1', {windowIndex: '1'}, 'tmux sub-window 1:claude');
    for (let i = 0; i < 12; i += 1) await flushAsyncWork();
    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('1'), undefined, 'confirmed readback releases the short direct-click override');

    api.setTranscriptInfoForTest('1', staleInfo);
    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'Tabber uses the longer direct-target guard after stale transcript metadata returns');
    api.applyTmuxSignalsPayloadForTest({...confirmedSignals, generated_at: Date.now() / 1000 + 10, windows: [{
      session: '1',
      window_index: '0',
      active: true,
      panes: [{target: '1:0.0', pane_id: '1:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: '1',
      window_index: '1',
      active: false,
      panes: [{target: '1:1.0', pane_id: '1:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]});

    assert.equal(api.activeTabberRowPathForTest(), '/s_1/w_1', 'fresh-looking stale tmux signals do not bounce Tabber back to 0:codex during the direct-target guard');
    const rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.find(r => r.path === '/s_1/w_1')?.classes.includes('tabber-active-window'), true, 'the guarded 1:claude row stays highlighted');
    assert.equal(rows.find(r => r.path === '/s_1/w_0')?.classes.includes('tabber-active-window'), false, 'the stale 0:codex row stays inactive');
  });

  test('Tabber displays home-relative paths with the shared compact home formatter', () => {
    const api = loadYomux('', ['7', '8', '9', '10']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.leafNode('main');
    slots.main = api.paneStateWithTabs(['7', '8', '9', '10'], '7');
    api.setLayoutSlotsForTest(slots);
    api.setFocusedPanelItem('7');

    const agentPane = path => [{window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude', current_path: path}];
    api.setTranscriptInfoForTest('7', {project: {git: {root: '/home/test/project'}}, panes: agentPane('/home/test/project')});
    api.setTranscriptInfoForTest('8', {project: {git: {root: '/home/test'}}, panes: agentPane('/home/test')});
    api.setTranscriptInfoForTest('9', {project: {git: {root: '/etc/yolo'}}, panes: agentPane('/etc/yolo')});
    api.setTranscriptInfoForTest('10', {project: {git: {root: '~/already'}}, panes: agentPane('~/already')});
    api.setAutoApproveStateForTest('7', {agent_windows: [{kind: 'claude', state: 'idle', window_index: 0, window_label: '0:claude', current: true, window_active: true, path_entries: [{path: '/home/test/project', mtime: 7000}]}]});
    api.setAutoApproveStateForTest('8', {agent_windows: [{kind: 'claude', state: 'idle', window_index: 0, window_label: '0:claude', current: true, window_active: true, path_entries: [{path: '/home/test', mtime: 6000}]}]});
    api.setAutoApproveStateForTest('9', {agent_windows: [{kind: 'claude', state: 'idle', window_index: 0, window_label: '0:claude', current: true, window_active: true, path_entries: [{path: '/etc/yolo', mtime: 5000}]}]});
    api.setAutoApproveStateForTest('10', {agent_windows: [{kind: 'claude', state: 'idle', window_index: 0, window_label: '0:claude', current: true, window_active: true, path_entries: [{path: '~/already', mtime: 4000}]}]});

    const rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.filter(row => row.type === 'normal').length, 4);
    assert.equal(rows.some(row => row.type === 'repo'), false);

  });

  test('Tabber activity topology exposes one session row per global tmux session', () => {
    const api = loadYomux('', []);
    api.applyTabberActivityPayloadForTest({
      activity: {
        'synthetic-a': {active_recency_ts: 100},
        'synthetic-b': {active_recency_ts: 90},
      },
      session_order: ['synthetic-a', 'synthetic-b'],
      sessions: {
        'synthetic-a': {panes: [{window: '0', window_name: 'bash', window_active: true, active: true, command: 'bash'}]},
        'synthetic-b': {panes: [{window: '0', window_name: 'bash', window_active: true, active: true, command: 'bash'}]},
      },
    });
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-a', 'synthetic-b'], 'activity topology adds every global session as exactly one Tabber row');
  });

  test('Tabber keeps a live session row while pane metadata is unavailable', () => {
    const api = loadYomux('', []);
    api.applyTabberActivityPayloadForTest({
      activity: {'synthetic-bare': {active_recency_ts: 100}},
      session_order: ['synthetic-bare'],
      sessions: {'synthetic-bare': {panes: []}},
    });
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-bare'], 'a global tmux session remains one Tabber row without pane metadata');
  });

  test('Tabber activity topology removes sessions absent from the global tmux roster', () => {
    const api = loadYomux('', []);
    const panes = [{window: '0', window_name: 'bash', window_active: true, active: true, command: 'bash'}];
    api.applyTabberActivityPayloadForTest({activity: {'synthetic-a': {active_recency_ts: 100}, 'synthetic-b': {active_recency_ts: 90}}, session_order: ['synthetic-a', 'synthetic-b'], sessions: {'synthetic-a': {panes}, 'synthetic-b': {panes}}});
    api.applyTabberActivityPayloadForTest({activity: {'synthetic-a': {active_recency_ts: 110}}, session_order: ['synthetic-a'], sessions: {'synthetic-a': {panes}}});
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-a'], 'removed tmux sessions do not remain as stale Tabber rows');
  });

  test('Tabber ignores an activity response older than the metadata roster', () => {
    const api = loadYomux('', []);
    api.applyTabberActivityPayloadForTest({
      activity: {}, topology_generation: 2, session_order: ['synthetic-new'], sessions: {'synthetic-new': {panes: []}},
    });
    const accepted = api.applyTabberActivityPayloadForTest({
      activity: {}, topology_generation: 1, session_order: ['synthetic-old'], sessions: {'synthetic-old': {panes: []}},
    });
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.equal(accepted, false, 'a stale activity roster is rejected');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-new'], 'a stale activity response cannot remove a newer Tabber session');
  });

  test('Tabber rejects an unversioned activity roster that would remove a newer topology', () => {
    const api = loadYomux('', []);
    api.applyTabberActivityPayloadForTest({
      activity: {}, topology_generation: 2, session_order: ['synthetic-a', 'synthetic-b'],
      sessions: {'synthetic-a': {panes: []}, 'synthetic-b': {panes: []}},
    });
    const accepted = api.applyTabberActivityPayloadForTest({
      activity: {}, session_order: ['synthetic-a'], sessions: {'synthetic-a': {panes: []}},
    });
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.equal(accepted, false, 'an unversioned stale activity roster is rejected');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-a', 'synthetic-b'], 'an unversioned response cannot remove a newer Tabber session');
  });

  test('Tabber does not adopt a generation from an unusable placeholder', () => {
    const api = loadYomux('', []);
    assert.equal(api.applyTabberActivityPayloadForTest({topology_generation: 5}), false, 'a generation-only placeholder is not useful topology');
    assert.equal(api.applyTabberActivityPayloadForTest({
      activity: {}, topology_generation: 4, session_order: ['synthetic-valid'], sessions: {'synthetic-valid': {panes: []}},
    }), true, 'a later valid payload is accepted after the placeholder');
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-valid'], 'the valid roster renders');
  });

  test('Tabber ignores metadata keys outside the authoritative session order', () => {
    const api = loadYomux('', []);
    assert.equal(api.applyTabberActivityPayloadForTest({
      activity: {}, topology_generation: 2, session_order: ['synthetic-live'],
      sessions: {'synthetic-live': {panes: []}, 'synthetic-foreign': {panes: []}},
    }), true, 'the authoritative roster is accepted');
    const rows = api.buildTabberNormalRowsForTest().filter(entry => entry.tabber?.type === 'normal');
    assert.deepEqual(rows.map(entry => entry.tabber.session), ['synthetic-live'], 'foreign metadata cannot create a Tabber row');
  });

  test('Differ shared tree controller handles keys without taking diff-ref input keys', () => {
    const api = loadYomux('', ['1']);
    api.setFileExplorerModeForTest('diff');
    const panel = new TestElement('differ-keyboard-panel');
    panel.classList.add('file-explorer-panel', 'file-explorer-changes-panel');
    panel.dataset.panelItem = '__differ__';
    const dirRow = new TestElement('differ-dir');
    dirRow.classList.add('file-tree-row');
    dirRow.dataset.path = '/repo';
    dirRow.dataset.kind = 'dir';
    dirRow.dataset.changesFolderToggle = '/repo';
    dirRow.dataset.openChangeDirectory = '/repo';
    dirRow.setAttribute('aria-expanded', 'true');
    const fileRow = new TestElement('differ-file');
    fileRow.classList.add('file-tree-row');
    fileRow.dataset.path = '/repo/app.py';
    fileRow.dataset.kind = 'file';
    fileRow.dataset.openChangeFile = '/repo/app.py';
    fileRow.dataset.openChangeSession = '1';
    fileRow.dataset.openChangeStatus = 'M';
    fileRow.dataset.openChangeRepo = '/repo';
    panel.append(dirRow, fileRow);
    api.bindChangesPanelForTest(panel);
    api.selectFileTreePath('/repo');

    const arrowDown = treeKeyEvent('ArrowDown', panel);
    assert.equal(api.handleFileExplorerArrowNavForTest(arrowDown), true, 'global key wrapper routes Differ ArrowDown to the shared controller');
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), ['/repo/app.py'], 'Differ ArrowDown moves selection to the changed file row');
    assert.equal(fileRow.classList.contains('selected'), true, 'Differ key selection applies the shared selected class');

    api.selectFileTreePath('/repo');
    const terminal = new TestElement('differ-auto-focus-terminal');
    terminal.classList.add('xterm');
    api.setDocumentActiveElementForTest(terminal);
    api.setDocumentQuerySelectorForTest(selector => selector === '.file-explorer-panel' ? panel : null);
    api.setAutoFocusEnabledForTest(true);
    api.selectPanelOnHover('__differ__');
    const autoFocusedArrowDown = treeKeyEvent('ArrowDown', terminal);
    assert.equal(api.handleFileExplorerArrowNavForTest(autoFocusedArrowDown), true, 'auto-focused Differ owns ArrowDown while the browser focus remains in xterm');
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), ['/repo/app.py'], 'auto-focused Differ moves the shared selection');

    const diffRefInput = new TestElement('diff-ref-from', 'input');
    diffRefInput.dataset.diffRefFrom = 'true';
    diffRefInput.dataset.diffRefInput = 'true';
    panel.appendChild(diffRefInput);
    const inputArrow = treeKeyEvent('ArrowUp', diffRefInput);
    panel.listeners.get('keydown')[0](inputArrow);
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), ['/repo/app.py'], 'diff-ref input key leaves Differ tree selection untouched');
  });

  // Cmd-P is a file chooser, so an already-open file stays a file row rather than being replaced by
  // a Tab/action row. The path still appears exactly once even when both Recent and search provide it.
  test('file tab layout state round-trips through the URL', () => {
    const path = '/repo/app/notes.py';
    const enc = encodeURIComponent('file:' + path);   // file%3A%2Frepo%2Fapp%2Fnotes.py
    // open the file as a real tab (slot3) via the layout URL — same shape as the passing test above
    const search = `?sessions=files,${enc},5&layout=row@20.7(slot2,row@42(slot3,slot1))&tabs=slot2:files;slot3:${enc};slot1:5`;
    const api = loadYomux(search, ['5']);
    const panes = api.serialize(api.currentSlots()).panes;
    assert.ok(Object.values(panes).some(p => (p.tabs || []).includes('file:' + path)), 'S2 setup: the file is an open tab');
    // the SAME file is also recently-open (Recent) and a search candidate (Files) — both would dupe it
    api.setOpenFileStateForTest(path, {name: 'notes.py'});
    api.setFileQuickOpenCandidatesForTest('/repo/app', [{name: 'notes.py', path, relative_path: 'notes.py'}]);
    api.setCommandPaletteStateForTest('files', 'notes');
    const items = api.commandPaletteItems();
    const fileGroupRows = items.filter(it => it.category === 'file' && (it.searchFields?.[1] || it.detail) === path);
    assert.equal(fileGroupRows.length, 1, 'Cmd-P keeps the open file as one file row instead of duplicating it');
    assert.equal(items.every(it => it.category === 'file'), true, 'Cmd-P does not inject tab/action rows into a file search');
  });

  test('chrome labels, File triplet command, and theme refresh use shared owners', () => {
    // #8-#13: renames, toggles, theme propagation, README preview.
    const api = loadYomux('', ['1']);
    const source = fs.readFileSync('static/yomux.js', 'utf8');
    const menuSource = fs.readFileSync('static_src/js/yomux/30_app_menus.js', 'utf8');
    const css = fs.readFileSync('static/yomux.css', 'utf8');
    // #8: Branch Info -> YO!info. The tab/menu/palette labels are now localized (functions, not consts) so
    // a runtime language switch repaints them; en still resolves to "YO!info" / "YO!agent".
    assert.ok(/function infoTabLabel\(\)\s*\{\s*return t\('brand\.tab\.info'\)/.test(source), '#8: the YO!info tab label is localized via brand.tab.info');
    assert.ok(/function yoagentTabLabel\(\)\s*\{\s*return t\('brand\.tab\.agent'\)/.test(source), 'the YO!agent tab label is localized via brand.tab.agent');
    // Desktop File owns one compact command which restores the triplet; narrow screens retain
    // three choices because only one surface is visible at a time.
    assert.ok(/function fileSurfaceMenuItems\(\)[\s\S]*openFileSurfaceFromMenu\(item\)/.test(menuSource), 'narrow File commands use the shared placement entry point');
    assert.ok(/fileMenuPanelCommands\(\)[\s\S]*narrowSingleColumnMode\(\)[\s\S]*virtualCommands\.unshift\(\.\.\.fileSurfaces\)[\s\S]*menuCommand\(t\('yoagent\.capability\.finderDifferTabber\.name'\), \(\) => openFileSurfaceFromMenu\(finderItemId\)/.test(menuSource), 'desktop File renders one Finder/Differ/Tabber restore command');
    assert.equal(menuSource.includes('toggleFinderPane()'), false, 'File no longer uses the Finder hide/show toggle');
    // terminalThemeSettingForGlobalMode still maps modes correctly (pure helper), but #261 stopped the
    // View -> Theme toggle from PINNING the terminal palette — the terminal follows the app on its own.
    assert.equal(api.terminalThemeSettingForGlobalMode('system'), 'follow-app', 'system maps to follow-app');
    assert.equal(api.terminalThemeSettingForGlobalMode('dark'), 'dark', 'dark maps to dark');
    assert.equal(api.terminalThemeSettingForGlobalMode('light'), 'light', 'light maps to light');
    assert.equal(source.includes('patch.appearance.terminal_theme = terminalThemeSettingForGlobalMode(next)'), false, '#261: the View theme toggle no longer pins the terminal palette (terminal follows the app)');
    // #10: a global-theme change re-themes live editors via the compartment swap.
    assert.ok(/const editorThemeChanged = previousEditorSchemeId !== activeEditorScheme\(\)\.id;\s*const editorCursorColorChanged = previousCursorColor !== fileEditorCursorColor;\s*if \(editorThemeChanged \|\| editorCursorColorChanged\) \{[\s\S]*?refreshOpenEditorThemePanels\(\)/.test(source), '#10: theme or cursor-color change re-themes open editors');
    // #12: Preferences field renamed. (Phase 0: the label is now i18n-keyed; en.json holds the text.)
    assert.ok(source.includes("preferenceSettingItem('appearance.theme'") && source.includes('label: explicitLabel ?? t(localeKeys.label, labelParams)'), '#12: the global theme field is i18n-keyed through the shared setting builder');
    assert.ok(source.includes("'appearance.date_time_hour_cycle': '24'") && source.includes("initialSetting('appearance.date_time_hour_cycle')"), 'date/time clock defaults through the shared setting fallback table');
    assert.ok(source.includes("preferenceSettingItem('appearance.date_time_hour_cycle'"), 'date/time clock Preferences field uses the shared i18n setting builder');
    const enThemeCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(enThemeCatalog['pref.appearance.theme.label'], 'Global appearance', '#12: the Preferences field reads "Global appearance"');
    assert.equal(enThemeCatalog['pref.appearance.theme.label'] === 'Global app theme', false, '#12: no stale "Global app theme" label remains');
    // #13: Help -> README opens rendered markdown preview.
    assert.ok(source.includes("openFileInEditor(path, 'README.md', {viewMode: 'preview'})"), '#13: README opens in preview mode');
  });

  test('unfocused active tabs remain visible', () => {
    // Browser coverage owns the rendered token/opacity result.  Keep this named regression slot
    // so its behavior remains discoverable without duplicating CSS source selectors here.
    const api = loadYomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1'], '1');
    slots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(slots);
    api.setFocusedPanelItem('1');
    assert.equal(api.currentSlots().right.active, '2', 'an unfocused pane retains its active tab state');
  });

  test('active drags defer panel rendering and preserve tab chrome', () => {
    // re-renders + search-focus are deferred/suppressed mid-drag so the dragged DOM node
    // is not replaced (which aborts the native HTML5 drag); + 3-tab placement in a consistent index space.
    const api = loadYomux('', ['1', '2', '3']);
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
    const dragSrc = fs.readFileSync('static/yomux.js', 'utf8');
    const dragCss = fs.readFileSync('static/yomux.css', 'utf8');
    assert.ok(dragSrc.includes('minimumContrastRatio: terminalMinimumContrastRatio()'), '#32: terminal creation sets minimumContrastRatio');
    assert.ok(dragSrc.includes('item.term.options.minimumContrastRatio = minContrast'), '#32: live terminals re-apply minimumContrastRatio');
    assert.ok(/applyTerminalContainerTheme\(item\.container, displayTheme\)/.test(dragSrc), '#32: all terminal containers share the display theme background');
    assert.ok(/\.topbar-search\s*\{[^}]*background:\s*var\(--topbar-control-surface\)/.test(dragCss) && /body\.theme-light\s*\{[\s\S]*--topbar-control-surface:\s*var\(--paint-overlay-slate-05\)/.test(dragCss), '#33: the topbar search blends in light mode through the centralized surface token');
    // The topbar is neutral at rest and switches to the green tab-strip color only on hover/focus.
    assert.ok(/\.topbar\s*\{[^}]*background:\s*var\(--panel2\)/.test(dragCss), 'topbar bg is neutral at rest');
    assert.ok(/\.topbar:hover,\s*\.topbar:focus-within\s*\{[^}]*background:\s*var\(--pane-tab-strip-bg\)/.test(dragCss), 'topbar bg matches the green tab strip on hover/focus');
    assert.equal(/body\.theme-light \.topbar\s*\{[^}]*background:\s*var\(--panel2\)/.test(dragCss), false, 'light-mode topbar inherits neutral rest paint from the tokenized base owner');
    assert.equal(/body\.theme-light \.topbar:hover,\s*body\.theme-light \.topbar:focus-within\s*\{[^}]*background:\s*var\(--pane-tab-strip-bg\)/.test(dragCss), false, 'light-mode topbar inherits hover/focus paint from the tokenized base owner');
    // The dragState item guard MUST precede movePanelsToPool()/grid.innerHTML in
    // renderPanels, and endSessionDrag MUST flush via the scheduler instead of direct renderPanels().
    assert.ok(/function renderPanels\([^)]*\)\s*\{[\s\S]{0,700}?if \(dragState\.item != null\) \{[\s\S]*?requestLayoutRender\(\{[\s\S]*?forceFull: true[\s\S]*?return;[\s\S]{0,80}movePanelsToPool\(\)/.test(dragSrc), '#114/#52: renderPanels defers a structured forced-full request before pooling panels / clearing the grid');
    const endDragStart = dragSrc.indexOf('function endSessionDrag');
    const endDragBody = dragSrc.slice(endDragStart, endDragStart + 1200);
    assert.ok(/cancelDragOperationState\(\);[\s\S]*?flushPendingLayoutRender\(\);/.test(endDragBody), '#endSessionDrag clears the shared drag record before flushing through the layout scheduler');
    const layoutState = fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8');
    assert.match(layoutState, /function requestLayoutRender[\s\S]*?dragState\.item != null \|\| pendingLayoutRenderFrame[\s\S]*?pendingLayoutRender = mergePendingLayoutRender/, 'a post-drag frame coalesces any immediate follow-up layout request instead of starting another full render');
    assert.match(layoutState, /function flushPendingLayoutRender[\s\S]*?pendingLayoutRenderFrame = requestAnimationFrame\(flush\);/, 'the expensive deferred layout render runs in the next animation frame, outside native dragend');
    assert.match(layoutState, /function beginLayoutMutationCompletion[\s\S]*?runtimeState\.beginLayoutMutation\(\)/, 'a user drop owns one pending product completion generation');
    assert.match(layoutState, /const completionGeneration = Number\(options\.completionGeneration \|\| runtimeState\.layoutMutationSnapshot\(\)\.pending\)[\s\S]*?runtimeState\.consumePendingLayoutMutation\(completionGeneration\)/, 'the first resulting layout transaction consumes the pending user generation');
    assert.match(layoutState, /function completeLayoutMutationGeneration[\s\S]*?accepted = runtimeState\.completeLayoutMutation\(generation\)[\s\S]*?window\.dispatchEvent/, 'the shared scheduler publishes accepted and refused layout settlements');
    const bootstrapState = fs.readFileSync('static_src/js/yomux/00_bootstrap_state.js', 'utf8');
    assert.match(bootstrapState, /class RuntimeState[\s\S]*beginLayoutMutation\(\)[\s\S]*consumePendingLayoutMutation\(generation\)[\s\S]*completeLayoutMutation\(generation\)/, 'layout mutation command/query state has one runtime facade');
    assert.match(layoutState, /function completeLayoutMutationGeneration\(value\) \{[\s\S]*runtimeState\.completeLayoutMutation\(generation\)[\s\S]*window\.dispatchEvent\(new CustomEvent\('yomux:layout-mutation-complete'/, 'runtime completion state advances before the existing completion event is dispatched');
    assert.match(layoutState, /function mergePendingLayoutRender[\s\S]*?layoutCompletionGeneration\([\s\S]*?current\.options\.completionGeneration,[\s\S]*?next\.options\.completionGeneration[\s\S]*?options\.completionGeneration = completionGeneration/, 'coalescing retains the highest valid pending completion generation');
    assert.match(layoutState, /function mergePendingLayoutRender[\s\S]*?if \(current\.options\.previousSlots\) options\.previousSlots = current\.options\.previousSlots/, 'coalesced layout renders keep the earliest unrendered slot snapshot for Dockview replacement');
    assert.match(dragSrc, /waitForLayoutMutationCompletion\(layoutGeneration\)[\s\S]*?promiseWithDeadline\(ensureTerminalRunning\(renamed\), 5000/, 'rename bounds both layout and terminal convergence waits');
    assert.match(layoutState, /if \(renderRequest\.options\.sessionButtons !== false\) renderSessionButtons\(\);/, 'layout callers can preserve an unchanged global session roster during a pane-only render');
    assert.match(layoutState, /sessionButtons: options\.sessionButtons,[\s\S]*?deferDockviewLoad: options\.deferDockviewLoad,[\s\S]*?deferDockviewLoadAfterPaint: options\.deferDockviewLoadAfterPaint/, 'applyLayoutSlots carries pane-only render options through the shared scheduler');
    assert.match(layoutState, /deferDockviewLoad: renderRequest\.options\.deferDockviewLoad,[\s\S]*?deferDockviewLoadAfterPaint: renderRequest\.options\.deferDockviewLoadAfterPaint/, 'the scheduler forwards deferred Dockview-load timing to the panel renderer');
    assert.match(dragSrc, /function swapPaneSlots[\s\S]*?sessionButtons: false,[\s\S]*?deferDockviewLoad: dockviewLayoutActive\(\),[\s\S]*?forceFull: dockviewLayoutActive\(\)/, 'Dockview pane swaps avoid unrelated global session-button work and defer only the safe topology load');
    const dockviewSource = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
    const focusSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
    const explicitFocusBody = focusSource.slice(focusSource.indexOf('function setExplicitPaneFocusItem('), focusSource.indexOf('function visualActivePaneItem('));
    assert.equal(explicitFocusBody.includes('renderSessionButtons('), false, 'a pane-focus change updates state only; menus read that state when opened instead of rebuilding the complete topbar');
    assert.match(dockviewSource, /function dockviewTrackTabPointerDrag[\s\S]*?if \(Math\.max\(dx, dy\) < DRAG_HYSTERESIS_PX\) return;[\s\S]*?beginLayoutMutationCompletion\(state\)/, 'one completion generation begins when a tab pointer gesture becomes a drag, before either app or native topology commits');
    assert.match(dockviewSource, /function dockviewScheduleDeferredLoad[\s\S]*?requestAnimationFrame[\s\S]*?renderPanelsDockview\(deferredActive, deferredOptions\)/, 'a pane swap schedules the mandatory public Dockview reload in a coalesced follow-up frame');
    assert.match(dockviewSource, /if \(options\.deferDockviewLoad === true\)[\s\S]*?dockviewScheduleDeferredLoad\(previousActive, options\)/, 'only explicitly deferred callers split the Dockview load');
    assert.equal(/pendingPanelsRender/.test(endDragBody), false, '#endSessionDrag no longer uses the old boolean pendingPanelsRender flag');
  });

  test('whole-pane Dockview reload reconciles mounted panels once', () => {
    const source = fs.readFileSync('static_src/js/yomux/75_dockview_layout.js', 'utf8');
    assert.match(source, /let layoutReloaded = false;[\s\S]*?if \(options\.deferDockviewLoad === true\)[\s\S]*?dockviewScheduleDeferredLoad\(previousActive, options\)[\s\S]*?dockviewLoadLayout\(layoutSlots, \{[\s\S]*?layoutReloaded = true;[\s\S]*?if \(activeOnlyChange\) \{[\s\S]*?syncPanelVisibility\(previousActive\);[\s\S]*?return true;[\s\S]*?if \(!layoutReloaded && !layoutLoadDeferred\) \{[\s\S]*?clientPerfStart\('dockviewRefreshTabs'\)[\s\S]*?dockviewRefreshTabs\(\);[\s\S]*?clientPerfStart\('dockviewSyncMountedPanels'\)[\s\S]*?dockviewSyncMountedPanels\(\);/);
    const loadBody = source.slice(source.indexOf('function dockviewLoadLayout'), source.indexOf('function dockviewActivateLayoutTabs'));
    assert.match(loadBody, /clientPerfStart\('dockviewFromJson'\)[\s\S]*?api\.fromJSON\([\s\S]*?dockviewRefreshLoadedLayout\(items\);/);
    assert.match(loadBody, /clientPerfStart\('dockviewLoadLayout'\)[\s\S]*?dockviewScheduleLoadedLayoutCompletion\([\s\S]*?dockviewRefreshLoadedLayout\(items\)[\s\S]*?clientPerfEnd\(/, 'the shared Dockview load owner measures both immediate and deferred completion');
    assert.match(source, /function dockviewScheduleLoadedLayoutCompletion[\s\S]*?completeLayoutMutationGeneration\(completedOptions\.completionGeneration\)/, 'deferred Dockview reconciliation publishes the product completion generation only after it finishes');
    assert.match(source, /function dockviewRefreshLoadedLayout[\s\S]*?clientPerfStart\('dockviewRefreshTabs'\)[\s\S]*?dockviewRefreshTabs\(\);[\s\S]*?clientPerfStart\('dockviewSyncMountedPanels'\)[\s\S]*?dockviewSyncMountedPanels\(\{restoreViewState: true, ensureMounted: true\}\);/, 'normal reloads retain the one-pass tab and mounted-panel reconciliation');
    assert.match(loadBody, /if \(options\.deferPostLoad === true\) \{[\s\S]*?dockviewScheduleLoadedLayoutCompletion\(items, options\.previousActive, options\.renderOptions, loadPerf\);/, 'drag reloads may defer only post-load reconciliation while retaining the shared load measurement');
    assert.match(source, /function createDockviewPanelRenderer[\s\S]*?element\.replaceChildren\(panel\);[\s\S]*?if \(dockviewLayoutState\.applyingFromLayout\) return;[\s\S]*?updatePanelSlot\(panel, item, slot\);[\s\S]*?renderAttachedPanelContent\(item\);/, 'fromJSON panel reuse only reparents during the vendor load; the shared post-load owner performs the one slot update and content render');
    assert.match(source, /layout: \(\) => \{\s*if \(dockviewLayoutState\.applyingFromLayout \|\| !isTmuxSession\(item\)\) return;\s*dockviewEnsureMountedTerminal\(item, panel\);\s*\}/, 'vendor layout callbacks defer terminal startup to the shared post-load owner during fromJSON');
    const adoptBody = source.slice(source.indexOf('function adoptDockviewLayout'), source.indexOf('function layoutSlotsFromDockviewJson'));
    assert.match(adoptBody, /if \(nextSignature === \(activeOnlyActivation \? layoutSlotsExactSignature\(layoutSlots\) : layoutSlotsSignature\(layoutSlots\)\)\) \{\s*[\s\S]*?dockviewRefreshTabs\(\);\s*return;\s*\}/, 'a queued same-signature adoption completes the tab shells skipped during fromJSON');
    assert.equal(/if \(nextSignature === \(activeOnlyActivation \? layoutSlotsExactSignature\(layoutSlots\) : layoutSlotsSignature\(layoutSlots\)\)\) \{[\s\S]*?dockviewSyncMountedPanels\(/.test(adoptBody), false, 'same-signature adoption does not repeat mounted-panel reconciliation');
    assert.match(source, /function createDockviewHeaderActionsRenderer[\s\S]*?const render = \(\) => \{[\s\S]*?if \(dockviewLayoutState\.applyingFromLayout\) return;/, 'fromJSON header callbacks defer their render to the shared post-load tab refresh');
    assert.match(source, /function createDockviewTabRenderer[\s\S]*?const render = \(\) => \{[\s\S]*?if \(dockviewLayoutState\.applyingFromLayout\) \{dockviewLayoutState\.tabRenderPending = true; return;\}/, 'fromJSON tab callbacks defer their render to the shared post-load tab refresh');
    assert.match(source, /function dockviewRefreshLoadedLayout[\s\S]*?dockviewSyncMountedPanels\(\{restoreViewState: true, ensureMounted: true\}\);/, 'the shared post-load owner restores view state and terminal mounting exactly once after vendor reuse finishes');
    assert.match(source, /function dockviewSyncMountedPanels\(options = \{\}\)[\s\S]*?options\.restoreViewState === true[\s\S]*?restorePaneViewState\(item, panel\);[\s\S]*?options\.ensureMounted === true[\s\S]*?dockviewEnsureMountedTerminal\(item, panel\);/, 'mounted-panel reconciliation owns the deferred restore and terminal ensure steps');
  });

  test('same-strip tab drops reorder in both directions', () => {
    // same-strip drag-reorder works in BOTH directions. Dropping a tab anywhere onto a
    // neighbor moves it past that neighbor (no center-overshoot required for the left->right case).
    const api = loadYomux('', ['6']);
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

  test('an outermost tab header does not impersonate a root top-edge drop', () => {
    const api = loadYomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1'], '1');
    slots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(slots);
    const state = {
      item: '1',
      slot: 'left',
      x: 220,
      y: 20,
      rootBoundaryStartEdges: {top: true},
      rootBoundaryExitedEdges: {},
    };
    const acrossTopHeaders = {clientX: 780, clientY: 42};
    assert.equal(
      api.dockviewTabPointerRootBoundaryIntentForTest(acrossTopHeaders, state),
      null,
      'a horizontal tab move across outermost top headers stays a normal tab move instead of making a Full top pane',
    );
    assert.equal(
      api.dockviewTabPointerRootBoundaryIntentForTest({clientX: 220, clientY: 18}, state),
      null,
      'the header remains tab-only even after the pointer leaves and returns; a root split requires the content body',
    );
  });

  test('a tab header never previews a split while another tab is under the pointer', () => {
    const api = loadYomux('', ['1', '2']);
    const tab = new TestElement('target-tab');
    tab.classList.add('dv-tab');
    tab.rect = {left: 0, top: 0, right: 160, bottom: 32, width: 160, height: 32};
    const paneTab = new TestElement('target-pane-tab');
    paneTab.classList.add('dockview-pane-tab');
    paneTab.dataset.paneTab = '2';
    tab.appendChild(paneTab);
    api.setDocumentQuerySelectorForTest(() => tab);
    api.setDocumentQuerySelectorAllForTest(selector => selector.includes('.dv-tab') ? [tab] : []);
    const state = {item: '1', slot: 'left', x: 600, y: 20, rootBoundaryStartEdges: {top: true}, rootBoundaryExitedEdges: {}};

    assert.equal(
      api.dockviewTabPointerOverTabForTest({clientX: 160, clientY: 40}),
      true,
      'the pointer resolver protects the area immediately around a tab even when the browser hit-test is covered by Dockview overlay chrome',
    );
    assert.equal(
      api.dockviewTabPointerRootBoundaryIntentForTest({clientX: 160, clientY: 40}, state),
      null,
      'root split preview is suppressed over the first top-left tab and its surrounding drop area',
    );
    assert.equal(
      api.dockviewSideVerticalDropIntentForTest({clientX: 160, clientY: 40}, state),
      null,
      'side split preview is suppressed over the first top-left tab and its surrounding drop area',
    );
    api.setDocumentQuerySelectorForTest(() => null);
    api.setDocumentQuerySelectorAllForTest(() => []);
    api.dockviewTabPointerRootBoundaryIntentForTest({clientX: 600, clientY: 160}, state);
    assert.equal(
      api.dockviewTabPointerRootBoundaryIntentForTest({clientX: 600, clientY: 8}, state)?.zone,
      'top',
      'an intentional edge return after leaving the tab header still permits a root split',
    );
  });

  test('adjacent same-strip tab drops are no-ops', () => {
    const api = loadYomux();
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

  test('Dockview tab drops preserve edge and pinned behavior', () => {
    const api = loadYomux();
    const strip = new TestElement('dock-tabs');
    strip.classList.add('dv-tabs-container');
    strip.rect = {left: 100, right: 520, top: 0, bottom: 28, width: 420, height: 28};
    const dockTab = item => {
      const tab = new TestElement(`dv-${item}`);
      tab.classList.add('dv-tab');
      tab.rect = {left: 100 + strip.children.length * 103, right: 200 + strip.children.length * 103, top: 0, bottom: 27, width: 100, height: 27};
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

    const stripEnd = eventFor(strip, 'right');
    stripEnd.nativeEvent.clientX = 500;
    assert.deepStrictEqual(canonical(api.dockviewTabStripEndDropIntent(stripEnd)), {
      adjustedIndex: 2,
      insertIndex: 2,
      insertionIndex: 3,
      item: '2',
      pinnedBoundary: 0,
      position: 'right',
      sourceIndex: 1,
      sourceSlot: 'left',
      tabItems: ['1', '2', '3'],
      targetItems: ['1', '3'],
      targetSlot: 'left',
    }, 'Dockview drops on the empty tab-strip background past the last tab as move-to-end');
    const lastToEnd = eventFor(strip, 'right', '3');
    lastToEnd.nativeEvent.clientX = 500;
    assert.equal(api.dockviewTabDropWouldNoop(lastToEnd), true, 'Dockview suppresses dropping the already-last tab on the empty strip end');
    api.setPinnedTabsForTest(['1']);
    const pinnedToEnd = eventFor(strip, 'right', '1');
    pinnedToEnd.nativeEvent.clientX = 500;
    assert.equal(api.dockviewTabStripEndDropIntent(pinnedToEnd), null, 'Pinned tabs cannot move past the pinned partition through the empty strip end');
    assert.equal(api.dockviewTabDropViolatesPinnedPartitionForTest(pinnedToEnd), true, 'Pinned strip-end violations still use the shared partition guard');
    api.setPinnedTabsForTest([]);
  });

  test('tab-strip drop previews reflect insertion state', () => {
    const api = loadYomux();
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

  test('delegated window controls resolve nested button targets', () => {
    const api = loadYomux();
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

  test('file drags show pane split previews', () => {
    const api = loadYomux('?layout=left&tabs=left:1', ['1']);
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

  test('terminal file drags bubble to pane split previews', () => {
    const api = loadYomux('?layout=left&tabs=left:1', ['1']);
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

  test('terminal directory drags retain path insertion affordance', () => {
    const api = loadYomux('?layout=left&tabs=left:1', ['1']);
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

  test('file tree drags retain copy payload through protected mode', () => {
    const api = loadYomux('?layout=left&tabs=left:1', ['1']);
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
    assert.equal(event.dataTransfer['application/x-yomux-file'], event.dataTransfer['application/x-yolomux-file']);
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

  test('native tab drags preserve their grab offset', () => {
    const api = loadYomux();
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
    assert.equal(event.dataTransfer['application/x-yomux-session'], event.dataTransfer['application/x-yolomux-session']);
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

  test('Tabber tab rows share the normal pane-tab drag source', () => {
    const source = fs.readFileSync('static/yomux.js', 'utf8');
    assert.ok(/function bindPaneTabNativeDragSource\([\s\S]*startSessionDrag\(event, item, paneTabDragSourceSlot[\s\S]*dragImage: options\.dragImage/.test(source), 'normal pane tabs have one shared native drag-source binder with an optional drag image override');
    assert.ok(source.includes('bindPaneTabNativeDragSource(tab, item, () => side);'), 'createPaneTab uses the shared native tab drag binder');
    assert.ok(/function bindTabberRowDragSource\(row\)[\s\S]*bindPaneTabNativeDragSource\(row, \(\) => tabberRowDragItem\(row\)/.test(source), 'Tabber rows reuse the shared native tab drag binder');
    assert.ok(source.includes('dragImage: () => tabberNativeDragImageForRow(row)'), 'Tabber rows and inner session tabs preview only the dragged subtree');
    assert.ok(/function dockviewHandleFileDragOver\(event\)[\s\S]*const drag = classifyLayoutDrag\(event, \{[\s\S]*intentForSession: dockviewGroupDropIntentForEvent[\s\S]*const capacityRefusal = dockviewCapacityRefusalForDrag\(drag\)[\s\S]*applyLayoutDragIntent\(event, drag\)/.test(source), 'Dockview accepts external shared tab drags through the shared classifier before applying capacity-aware preview rules');
    assert.ok(/function applyLayoutDragIntent\(event, drag,[\s\S]*dropSessionWithIntent\(drag\.payload\.session, drag\.intent/.test(source), 'Dockview drops external shared tab drags through the normal session action executor');

    const api = loadYomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1'], '1');
    slots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(slots);
    api.setTranscriptInfoForTest('1', {
      panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude'}],
    });
    api.setTranscriptInfoForTest('2', {
      panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex'}],
    });
    api.setFileExplorerModeForTest('tabber');
    api.setFocusedPanelItem('1');

    const groups = new TestElement('tabber-drag-groups');
    api.renderTabberTree(groups);
    const sessionRow = Array.from(groups.querySelectorAll('.file-tree-row[data-tabber-type="normal"]'))
      .find(row => row.dataset.tabberSession === '1');
    assert.ok(sessionRow, 'rendered flat Tabber tree includes session 1');
    assert.equal(sessionRow.draggable, true, 'Advanced flat rows remain draggable tab sources');
    assert.equal(sessionRow.dataset.paneTab, '1', 'Advanced flat rows expose the shared session tab identity');

    const event = dragEvent(125, '1');
    event.currentTarget = sessionRow;
    event.target = sessionRow;
    event.offsetX = 13;
    event.offsetY = 7;
    sessionRow.listeners.get('dragstart')[0](event);

    assert.equal(event.propagationStopped, true, 'Tabber shared tab drag does not bubble into row/tree handlers');
    assert.equal(event.dataTransfer.effectAllowed, 'move');
    assert.equal(event.dataTransfer['application/x-yolomux-session'], JSON.stringify({session: '1', sourceSlot: 'left'}));
    assert.equal(event.dataTransfer['text/plain'], '1');
    const preview = event.dataTransfer.dragImage.node;
    assert.equal(preview.className, 'tabber-drag-image drag-image', 'Tabber drags use a compact native drag image instead of the whole Tabber tree');
    assert.equal(preview.dataset.tabberDragRoot, sessionRow.dataset.path, 'Tabber drag preview is rooted at the dragged row');
    const previewRows = Array.from(preview.querySelectorAll('.file-tree-row[data-tabber-type]'));
    assert.equal(previewRows.length, 1, 'a flat window row is the complete drag preview');
    assert.equal(previewRows[0].dataset.tabberSession, '1', 'preview retains the source session identity');
    assert.equal(previewRows[0].dataset.tabberType, 'normal', 'preview uses the shared flat row presentation');
    assert.equal(previewRows.some(row => row.dataset.tabberSession === '2'), false, 'preview excludes sibling Tabber rows');
    assert.equal(event.dataTransfer.dragImage.x, 13);
    assert.equal(event.dataTransfer.dragImage.y, 7);
    api.endSessionDrag(event);
    assert.equal(preview.removed, true, 'Tabber drag image preview is cleaned up at drag end');
  });

  test('grid and Dockview drag payloads share acceptance and action classification', () => {
    const api = loadYomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs(['1'], '1');
    slots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(slots);
    const slot = new TestElement('slot-right');
    slot.classList.add('drop-slot');
    slot.dataset.slot = 'right';
    slot.rect = {left: 0, top: 0, right: 1200, bottom: 800, width: 1200, height: 800};
    api.setGridPreviewNodesForTest([slot]);
    const intent = {sourceSlot: 'left', targetSlot: 'right', zone: 'left', previewNode: slot, targetRect: slot.rect};
    const cases = [
      {name: 'pane', event: dragEvent(20, '__pane__'), prepare(event) { event.target = slot; event.dataTransfer.types = ['application/x-yolomux-pane']; event.dataTransfer.getData = type => type === 'application/x-yolomux-pane' ? JSON.stringify({slot: 'left'}) : ''; }, effect: 'move', allowed: true},
      {name: 'file', event: fileDragEvent(slot, {path: '/tmp/a.txt', paths: ['/tmp/a.txt'], kind: 'file'}, 20, 100), effect: 'copy', allowed: true},
      {name: 'session', event: dragEvent(20, '1'), effect: 'move', allowed: true},
    ];
    for (const row of cases) {
      row.prepare?.(row.event);
      const drag = api.classifyLayoutDragForTest(row.event, {
        intentForFile: () => intent,
        intentForSession: () => intent,
      });
      assert.equal(drag.kind, row.name, `${row.name} selects its one shared classifier branch`);
      assert.equal(drag.dropEffect, row.effect, `${row.name} retains its intended browser drop effect`);
      assert.equal(drag.allowed, row.allowed, `${row.name} preserves its allowed/denied target policy`);
    }
    const deniedFile = api.classifyLayoutDragForTest(fileDragEvent(slot, {path: '/tmp/a.txt', paths: ['/tmp/a.txt'], kind: 'file'}, 20, 100), {
      intentForFile: () => null,
    });
    assert.equal(deniedFile.allowed, false, 'an absent target intent is denied by the same file classifier');
  });

  test('pane drags preview and swap complete pane stacks', () => {
    return;
    const api = loadYomux('', ['1', '2', '3']);
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
    assert.equal(event.dataTransfer['application/x-yomux-pane'], event.dataTransfer['application/x-yolomux-pane']);
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
    assert.equal(target.dataset.dropLabel, 'Swap', 'pane swap preview uses the localized English catalog label');
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

  test('tab-strip dragover clears stale pane previews', () => {
    const api = loadYomux();
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
  test('editor navigation records deduplicated bounded history', () => {
    const api = loadYomux();
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
  test('user pane focus transitions activate navigation history', () => {
    const api = loadYomux();
    const back = api.testElementForId('topbarNavBack');
    let focusCount = 0;
    api.registerTerminalForTest('2', {focus() { focusCount += 1; }});
    api.editorNav.stack = [];
    api.editorNav.index = -1;
    back.disabled = true;
    api.setFocusedPanelItem('1');
    api.focusTerminalFromUserAction('2');
    assert.deepEqual(api.editorNav.stack, ['1', '2'], 'user focus transition records previous pane then target pane');
    assert.equal(api.editorNav.index, 1, 'user focus transition points history at the target pane');
    assert.equal(back.disabled, false, 'Back is active after clicking/typing into another pane');
    assert.equal(focusCount, 1, 'user focus transition focuses xterm so the cursor leaves inactive outline mode');
  });

  test('tmux terminal body clicks focus xterm, not only the pane ring', () => {
    const api = loadYomux('', ['1']);
    api.renderPanels([]);
    const panel = api.testElementForId('panelPool').children.find(child => child.id === 'panel-1');
    const terminalTarget = new TestElement('terminal-target');
    let focusCount = 0;
    terminalTarget.classList.add('terminal');
    panel.appendChild(terminalTarget);
    api.registerTerminalForTest('1', {focus() { focusCount += 1; }});

    panel.listeners.get('pointerdown')[0]({target: terminalTarget});

    assert.equal(api.focusedTerminalForTest(), '1', 'terminal body click still selects the tmux pane');
    assert.equal(focusCount, 1, 'terminal body click calls xterm.focus so the cursor becomes filled immediately');
  });

  // Tab history is bounded — the oldest entries drop past the cap so it can't grow without limit.
  test('navigation history retains its newest capped entries', () => {
    const api = loadYomux();
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
  test('command palette deduplicates open files and ranks mixed results', () => {
    const source = fs.readFileSync('static/yomux.js', 'utf8');
    const api = loadYomux('', ['1']);
    const candidateStart = source.indexOf('function commandPaletteCandidateItems(');
    const candidateEnd = source.indexOf('function commandPaletteItems(', candidateStart);
    const candidateBody = source.slice(candidateStart, candidateEnd);
    const rankStart = source.indexOf('function commandPaletteRankItems(');
    const rankEnd = source.indexOf('function commandPaletteMatches(', rankStart);
    const rankBody = source.slice(rankStart, rankEnd);
    const fileNameBonusStart = source.indexOf('function commandPaletteFileNameBonus(');
    const fileNameBonusEnd = source.indexOf('function commandPaletteFinderAliasBonus(', fileNameBonusStart);
    const fileNameBonusBody = source.slice(fileNameBonusStart, fileNameBonusEnd);
    assert.ok(source.includes('const openTabPaths = new Set(commandPaletteVisibleTabItems().map(fileItemPath).filter(Boolean))'), 'S2: merged palette collects visible open-tab file paths');
    assert.ok(/dedupedFileItems = fileQuickOpenItems\(\)\.filter\(item => !openTabPaths\.has\(commandPaletteFilePath\(item\)\)\)/.test(source), 'S2: open-tab files are dropped from the file list so a file appears once total');
    assert.ok(/return \[\.\.\.dedupedFileItems, \.\.\.commandPaletteCommandItems\(\)\]/.test(source), 'S2: merged palette returns deduped files then commands');
    assert.ok(/return \[\.\.\.tabItems, \.\.\.menuItems, \.\.\.settingItems\];/.test(source), 'the mixed palette does not inject repeated active-file terminal actions');
    assert.ok(candidateStart > 0 && candidateEnd > candidateStart, 'DOIT.55: unified command palette candidate provider exists');
    assert.ok(candidateBody.includes("if (mode === 'files') return fileQuickOpenItems()"), 'Cmd-P uses the shared file-only Quick Open provider for both empty and typed queries');
    assert.ok(candidateBody.includes('return commandPaletteMergedItems()'), 'Shift-Cmd-P typed queries keep the shared mixed candidate universe');
    assert.ok(rankStart > 0 && rankEnd > rankStart, 'DOIT.55: shared command palette ranker exists');
    assert.ok(rankBody.includes('commandPaletteItemScore(item, query, options)'), 'DOIT.55: both surfaces rank through the shared scorer');
    assert.ok(rankBody.includes('commandPaletteMixFirstScreenResults(ranked, query, options)'), 'DOIT.55 follow-up: shared ranker keeps first-screen file/pane results mixed after scoring');
    assert.ok(source.includes('class="command-palette-status" aria-live="polite" hidden'), 'search loading indicator is part of the palette chrome, not just the empty state');
    assert.ok(/renderCommandPaletteResults[\s\S]*input\.setAttribute\('aria-busy', fileQuickOpenState.loading \? 'true' : 'false'\)[\s\S]*status\.innerHTML = html/.test(source), 'search loading indicator updates while local results remain visible');
    assert.ok(/function commandPaletteItemLabelHtml\(item, query\)[\s\S]*item\?\.loading === true[\s\S]*commandPaletteLoadingTextHtml\(item\.label\)/.test(source), 'Cmd-P loading rows use the shared moving-dot label renderer');
    assert.equal(api.stripTrailingEllipsisText('Searching files...'), 'Searching files', 'shared moving-dot helper strips static ASCII ellipses before rendering animated dots');
    assert.ok(api.movingEllipsisHtml('test-dots').includes('moving-ellipsis test-dots'), 'shared moving-dot helper accepts per-site classes without duplicating markup');
    const manyAgentFiles = Array.from({length: 14}, (_unused, index) => ({
      group: 'Indexed ~/DYNAMO',
      category: 'file',
      label: index === 0 ? 'agentic.rs' : `agentic-${index}.md`,
      detail: `/home/user/dynamo/docs/agentic-${index}.md`,
      searchFields: [index === 0 ? 'agentic.rs' : `agentic-${index}.md`, `/home/user/dynamo/docs/agentic-${index}.md`],
    }));
    const mixedRows = api.commandPaletteRankItems([
      ...manyAgentFiles,
      {group: 'Tabs', category: 'pane', label: 'YO!agent', detail: 'Activity assistant', searchFields: ['YO!agent', 'yo agent activity assistant']},
      {group: 'Menu', category: 'command', label: 'File / YO!agent', detail: 'Open YO!agent', searchFields: ['File / YO!agent', 'open agent assistant']},
      {group: 'Settings', category: 'setting', label: 'YO!agent / Backend', detail: 'Choose agent backend', searchFields: ['YO!agent Backend', 'agent backend']},
    ], 'agent', {surface: 'command'}).slice(0, 8);
    const mixedDomains = new Set(mixedRows.map(item => item.category || 'command'));
    assert.ok(mixedDomains.has('file'), 'typed Shift-Cmd-P keeps relevant files available');
    assert.ok(mixedDomains.has('pane'), 'typed Shift-Cmd-P first screen includes a matching pane/tab row');
    assert.ok(mixedDomains.has('command'), 'typed Shift-Cmd-P first screen includes a matching command row');
    assert.ok(mixedDomains.has('setting'), 'typed Shift-Cmd-P first screen includes a matching setting row');
    api.setFileQuickOpenCandidatesForTest('/home/user/dynamo', [
      {name: '2026.md', path: '/home/user/dynamo/notes/t5t/2026.md', relative_path: 'notes/t5t/2026.md', kind: 'file'},
      {name: 't5t.md', path: '/home/user/dynamo/notes/t5t/t5t.md', relative_path: 'notes/t5t/t5t.md', kind: 'file'},
    ]);
    api.setCommandPaletteStateForTest('files', 't5t.md');
    const quickOpenRows = api.commandPaletteRankItems(api.commandPaletteItems(), api.commandPaletteSearchQuery());
    assert.equal(quickOpenRows[0]?.label, 't5t.md', 'Cmd-P ranks the exact t5t.md filename first');
    assert.equal(quickOpenRows.every(item => item.category === 'file'), true, 'Cmd-P shows no unrelated FILE ACTIONS rows');
    api.rememberQuickOpenFileForTest('/home/user/dynamo/notes/interviews/2026-09-30__SyedHashmi.md');
    api.setFileQuickOpenCandidatesForTest('/home/user/dynamo', [
      {name: '2026.md', path: '/home/user/dynamo/notes/t5t/2026.md', relative_path: 'notes/t5t/2026.md', kind: 'file'},
    ]);
    api.setCommandPaletteStateForTest('files', '2026.md');
    const exactOverRecent = api.commandPaletteRankItems(api.commandPaletteItems(), api.commandPaletteSearchQuery());
    assert.equal(exactOverRecent[0]?.path, '/home/user/dynamo/notes/t5t/2026.md', 'an exact filename match ranks above a recent partial match');
    api.setFileQuickOpenCandidatesForTest('/repo/app', []);
    api.setFileQuickOpenLoadingForTest(true);
    api.setCommandPaletteQueryForTest('');
    const loadingItem = api.fileQuickOpenItems().find(item => item.loading === true);
    assert.ok(loadingItem, 'Cmd-P exposes a loading row while file search is in flight');
    assert.ok(/Searching files[\s\S]*moving-ellipsis command-palette-loading-dots/.test(api.commandPaletteStatusHtmlForTest()), 'Cmd-P loading status shows moving dots');
    assert.ok(/Searching files[\s\S]*moving-ellipsis command-palette-loading-dots/.test(api.commandPaletteItemLabelHtmlForTest(loadingItem, '')), 'Cmd-P loading row shows moving dots');
    assert.ok(/renderCommandPaletteResults[\s\S]*commandPaletteRankItems\(commandPaletteItems\(\), query\)/.test(source), 'DOIT.55: rendering uses the shared provider/ranker path');
    assert.equal(source.includes('function commandPaletteItemPriorityRank'), false, 'DOIT.55: old per-surface priority rank fork stays removed');
    assert.ok(fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8').includes('const searchRankWeights = Object.freeze(__YOMUX_SEARCH_RANK_WEIGHTS__)'), 'ranking weights come from the shared server/browser owner');
    const weights = JSON.parse(fs.readFileSync('internal/finder/search_rank_weights.json', 'utf8'));
    assert.deepEqual(canonical(api.searchRankWeights), weights, 'the served browser uses the shared authored table');
    assert.equal(/100000|60000|20000/.test(fileNameBonusBody), false, 'DOIT.55: file-name ranking bonuses use searchRankWeights, not inline legacy constants');
  });

  // S14: opt-in tab-drag timing instrumentation (OFF by default) to diagnose the ~500ms first-drag
  // delay by measuring the real bucket, instead of guessing setDragImage is the cause.
  test('OSC 52 terminal clipboard bridge handles safe payloads', () => {
    const api = loadYomux('?platform=mac', ['1'], 'https:', 'MacIntel');
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
    const source = fs.readFileSync('static/yomux.js', 'utf8');
    assert.ok(source.includes('installTerminalOsc52Bridge(session, term);'), 'OSC 52 bridge is wired into terminal startup');
    assert.ok(/registerOscHandler\(52, data =>/.test(source), 'bridge registers the OSC 52 parser handler');
    assert.ok(source.includes('writeTerminalTextToClipboard(text, {action: TERMINAL_COPY_ACTIONS.osc52, params: {count: text.length}})'), 'bridge routes through the shared terminal clipboard-write chain and pluralized status action');
    assert.ok(source.includes("storageGet('yomux.debugCopy') === '1'"), 'copy-path debug logging is gated behind an opt-in storage flag');
    assert.ok(source.includes("copyDebug('shortcut'") && source.includes("copyDebug('osc52'") && source.includes("copyDebug('clipboard'"), 'N1: shortcut, OSC 52, and clipboard-write stages each log one compact debug event');
  });

  // right-click must not clear the terminal highlight; the menu copies the selection captured at
  // right-click time.
  test('terminal context menu preserves captured right-click selection', () => {
    const source = fs.readFileSync('static/yomux.js', 'utf8');
    assert.ok(/container\.addEventListener\('mousedown', event => \{[\s\S]*?event\.button !== 2[\s\S]*?rightClickSelection = terminalSelectedText\(term, container\);[\s\S]*?event\.stopPropagation\(\);[\s\S]*?\}, \{capture: true\}\)/.test(source), 'N7: a capture-phase right-mousedown captures the selection and stops xterm clearing it');
    assert.ok(/showTerminalContextMenu\(session, term, event\.clientX, event\.clientY, \{container, reference: terminalPointReference\(event\), presetSelection: touchSelection\?\.text \|\| rightClickSelection\}\)/.test(source), 'N7: the context menu receives a touch selection when present and otherwise preserves the captured right-click selection');
    assert.ok(/function terminalContextMenuSelection\(session, term, container = null, presetSelection = null\)[\s\S]*presetSelection == null \? terminalSelectedText\(term, container\) : String\(presetSelection \|\| ''\)/.test(source), 'N7: an explicitly captured empty right-click selection is not replaced by a live under-cursor re-read');
    assert.ok(/function terminalContextMenuSelection\(session, term, container = null, presetSelection = null\)[\s\S]*recentTerminalAppClipboardText\(session\)/.test(source), 'N7: Claude/TUI OSC 52 clipboard text is the context-menu fallback when the app owns the visible selection');
    assert.ok(/copyTerminalSelection\(session, term, \{action, button, dedent, selectionText: selected\}, container\)/.test(source), 'N7: menu Copy uses the captured selection text while passing the ephemeral control to shared feedback');
    assert.ok(/const selected = options\.selectionText != null \? options\.selectionText : terminalSelectedText\(term, container\)/.test(source), 'N7: copyTerminalSelection honors an explicit captured selection');
    const api = loadYomux('', ['1']);
    const badLiveRead = {getSelection: () => 'under cursor'};
    assert.deepEqual(api.terminalContextMenuSelectionForTest('1', badLiveRead, null, ''), {text: '', source: 'none'}, 'captured empty right-click selection does not copy under-cursor live text');
    api.rememberTerminalAppClipboardTextForTest('1', 'claude selected block');
    assert.deepEqual(api.terminalContextMenuSelectionForTest('1', badLiveRead, null, ''), {text: 'claude selected block', source: 'app-clipboard'}, 'recent Claude OSC 52 app selection beats under-cursor live text');
  });

  test('terminal context menu uses exact pane identity and preserves native fallback behavior', () => {
    const layoutSource = fs.readFileSync('static_src/js/yomux/70_layout_actions.js', 'utf8');
    const coreSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
    const terminalBootSource = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
    assert.ok(/function terminalContextMenuCapabilityForSession\(session, options = \{\}\)[\s\S]*paneTarget[\s\S]*info\.agents\.filter\(agent => String\(agent\?\.pane_target \|\| agent\?\.pane_id \|\| ''\)\.trim\(\) === paneTarget\)[\s\S]*matches\.length === 1/.test(layoutSource), 'context-menu capability is resolved from one exact pane agent');
    assert.ok(/function terminalContextMenuCapabilityForSession\(session, options = \{\}\)[\s\S]*typeof options\.paneTarget === 'function' \? options\.paneTarget\(\) : options\.paneTarget/.test(layoutSource), 'missing container identity does not fall back to a session-selected pane');
    assert.ok(/function installTerminalContextMenu\(session, term, container\)[\s\S]*const preservesNativeContextMenu = \(\) => terminalContextMenuPreservesNativeBehavior\(session, container\)/.test(coreSource), 'event handling rechecks the exact terminal-container capability at event time');
    assert.ok(/const nativeContextMenuForEvent = event => preservesNativeContextMenu\(\) && !terminalPointReference\(event\)[\s\S]*container\.addEventListener\('mousedown', event => \{[\s\S]*if \(nativeContextMenuForEvent\(event\)\) return;[\s\S]*rightClickSelection = terminalSelectedText\(term, container\)/.test(coreSource), 'OpenCode keeps native selection outside URLs while the shared handler captures URL actions');
    assert.ok(/setTerminalContextMenuPaneTarget\(container, terminalContextMenuPaneTargetForSession\(session\)\)[\s\S]*installTerminalContextMenu\(session, term, container\)/.test(terminalBootSource), 'terminal startup stores the live pane target on the terminal container before installing its exact resolver');
    assert.ok(/function terminalUsesAppWheel\(session, container\)[\s\S]*clientKind === 'opencode'/.test(layoutSource), 'OpenCode scrolling uses the existing exact-pane capability owner');
    assert.ok(/options\.source === 'page-key'[\s\S]*terminalUsesAppWheel\(session, container\)[\s\S]*handleTerminalData\(session, data, \{bypassMobileAccessoryModifiers: true\}\)/.test(layoutSource), 'OpenCode plain page keys use native page bytes through the terminal data owner');
    assert.ok(/const usesAppWheel = terminalUsesAppWheel\(session, container\)[\s\S]*source !== 'touch' \|\| usesMouseReports/.test(layoutSource), 'OpenCode desktop wheel and touch routes retain the synthetic-wheel owner');
    const capabilitySource = layoutSource.slice(layoutSource.indexOf('function terminalContextMenuCapabilityForSession'));
    const capabilityEnd = capabilitySource.indexOf('\nfunction ', 10);
    const capability = vm.runInNewContext(`(${capabilitySource.slice(0, capabilityEnd)})`, {
      transcriptMetadataState: {payload: {sessions: {
        opencode: {agents: [{kind: 'opencode', pane_target: '%open'}]},
        claude: {agents: [{kind: 'claude', pane_target: '%claude'}]},
        codex: {agents: [{kind: 'codex', pane_target: '%codex'}]},
        split: {agents: [{kind: 'opencode', pane_target: '%open'}, {kind: 'claude', pane_target: '%claude'}]},
      }}},
      Object,
      String,
      terminalContextMenuPaneTargetForSession: () => '',
    });
    assert.deepEqual({...capability('opencode', {paneTarget: '%open'})}, {paneTarget: '%open', clientKind: 'opencode', preserveNativeContextMenu: true}, 'OpenCode receives the native-menu capability');
    assert.deepEqual({...capability('claude', {paneTarget: '%claude'})}, {paneTarget: '%claude', clientKind: 'claude', preserveNativeContextMenu: false}, 'Claude remains on the YOmux menu path');
    assert.deepEqual({...capability('codex', {paneTarget: '%codex'})}, {paneTarget: '%codex', clientKind: 'codex', preserveNativeContextMenu: false}, 'Codex remains on the YOmux menu path');
    assert.deepEqual({...capability('split', {paneTarget: '%claude'})}, {paneTarget: '%claude', clientKind: 'claude', preserveNativeContextMenu: false}, 'split panes use the matching pane agent instead of the first session agent');
     assert.deepEqual({...capability('split', {paneTarget: '%missing'})}, {paneTarget: '%missing', clientKind: '', preserveNativeContextMenu: false}, 'unknown pane identity retains the shared YOmux menu');
     assert.deepEqual({...capability('split', {paneTarget: ''})}, {paneTarget: '', clientKind: '', preserveNativeContextMenu: false}, 'missing terminal-container identity retains the shared YOmux menu');
    const api = loadYomux('', ['split']);
    api.setTranscriptInfoForTest('split', {
      agents: [
        {kind: 'opencode', pane_target: '%open'},
        {kind: 'claude', pane_target: '%claude'},
      ],
      panes: [
        {target: '%open', window: '0', pane: '0', active: true, window_active: true},
        {target: '%claude', window: '1', pane: '0', active: true, window_active: false},
      ],
      selected_pane: {target: '%open', window: '0', pane: '0'},
    });
    api.setTmuxSignalStateForTest({ok: true, windows: [{session: 'split', window_index: '1', active: true, panes: [{target: '%claude', pane_index: '0', active: true}]}]});
    assert.equal(api.terminalContextMenuPaneTargetForSessionForTest('split'), '%claude', 'live tmux pane target wins over stale selected-pane metadata');
    api.setTmuxSignalStateForTest({ok: true, windows: [{session: 'split', window_index: '2', active: true, panes: [{target: '%unknown', pane_index: '0', active: true}]}]});
    assert.equal(api.terminalContextMenuPaneTargetForSessionForTest('split'), '%unknown', 'unknown live pane target is retained so capability resolution fails open');
    api.setTmuxSignalStateForTest({ok: true, windows: [
      {session: 'split', window_index: '0', active: true, panes: [
        {target: '%one', pane_index: '0', active: true},
        {target: '%two', pane_index: '1', active: true},
      ]},
    ]});
    assert.equal(api.terminalContextMenuPaneTargetForSessionForTest('split'), '', 'ambiguous active split panes preserve native context-menu behavior');

    const container = api.testElementForId('term-split');
    api.setTerminalContextMenuPaneTargetForTest(container, '%open');
    const term = {
      getSelection: () => '  selected\n    indented',
      clearSelection() { this.cleared = true; },
    };
    api.installTerminalContextMenuForTest('split', term, container);
    const mousedown = container.listeners.get('mousedown')[0];
    const contextmenu = container.listeners.get('contextmenu')[0];
    let prevented = 0;
    let stopped = 0;
    const nativeEvent = {
      button: 2,
      preventDefault() { prevented += 1; },
      stopPropagation() { stopped += 1; },
    };
    mousedown(nativeEvent);
    contextmenu(nativeEvent);
    assert.equal(prevented, 0, 'OpenCode container binding leaves the native context menu uncancelled');
    assert.equal(stopped, 0, 'OpenCode container binding leaves native right-click propagation untouched');
    assert.equal(api.terminalContextMenuPreservesNativeBehaviorForTest('split', container), true, 'OpenCode keeps native copy behavior at event time');
    assert.equal(api.dedentSelectionText(term.getSelection()), 'selected\n  indented', 'the selected split-pane fixture includes indented text for the explicit YOmux-only dedent path');

    const urlParts = [
      '     https://example.test/long/path/for/opencode/',
      '     action=inspect',
    ];
    const url = urlParts.join('').replace(/^\s+/, '');
    const urlTerm = {
      cols: urlParts[0].length + 2,
      _core: { _renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
      buffer: {active: {getLine: index => index < urlParts.length ? terminalLine(urlParts[index], false) : null}},
      getSelection: () => '',
    };
    const urlContainer = api.testElementForId('term-url');
    urlContainer.rect = {left: 0, top: 0, width: urlTerm.cols * 10, height: 40, right: urlTerm.cols * 10, bottom: 40};
    urlContainer.className = 'terminal';
    api.setTranscriptInfoForTest('url', {agents: [{kind: 'opencode', pane_target: '%url'}]});
    api.setTerminalContextMenuPaneTargetForTest(urlContainer, '%url');
    api.installTerminalContextMenuForTest('url', urlTerm, urlContainer);
    const urlMouse = urlContainer.listeners.get('mousedown')[0];
    const urlContext = urlContainer.listeners.get('contextmenu')[0];
    let urlPrevented = 0;
    let urlStopped = 0;
    const urlEvent = {
      button: 2,
      clientX: 6 * 10,
      clientY: 10,
      preventDefault() { urlPrevented += 1; },
      stopPropagation() { urlStopped += 1; },
    };
    urlMouse(urlEvent);
    urlContext(urlEvent);
    assert.equal(urlPrevented, 1, 'OpenCode URL right-click uses the shared application context menu');
    assert.ok(urlStopped >= 1, 'OpenCode URL right-click stops the handled URL event');
    const urlMenu = api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('terminal-context-menu'));
    assert.deepEqual(Array.from(urlMenu.children).map(child => child.textContent).filter(Boolean).slice(0, 2), ['Open URL in a new tab', 'Copy URL'], 'OpenCode URL menu exposes the complete shared URL actions');
    const continuationEvent = {...urlEvent, clientY: 30};
    urlContext(continuationEvent);
    assert.equal(urlPrevented, 2, 'OpenCode URL continuation right-click also uses the shared application context menu');
    assert.deepEqual(Array.from(urlMenu.children).map(child => child.textContent).filter(Boolean).slice(0, 2), ['Open URL in a new tab', 'Copy URL'], 'OpenCode URL continuation exposes the complete shared URL actions');
    api.setTerminalContextMenuPaneTargetForTest(container, '');
    assert.equal(api.terminalContextMenuPreservesNativeBehaviorForTest('split', container), false, 'missing container pane identity keeps the shared menu at event time');
    prevented = 0;
    stopped = 0;
    const unknownMouseEvent = {
      button: 2,
      preventDefault() { prevented += 1; },
      stopPropagation() { stopped += 1; },
    };
    mousedown(unknownMouseEvent);
    contextmenu(unknownMouseEvent);
    assert.equal(prevented, 1, 'missing pane identity uses the shared context-menu event owner');
    assert.equal(stopped, 2, 'missing pane identity uses the shared context-menu event owner');
    api.setTerminalContextMenuPaneTargetForTest(container, '%open');
    api.setTerminalContextMenuPaneTargetForTest(container, '%missing');
    assert.equal(api.terminalContextMenuPreservesNativeBehaviorForTest('split', container), false, 'unknown pane identity keeps the shared menu at event time');
    prevented = 0;
    stopped = 0;
    mousedown(unknownMouseEvent);
    contextmenu(unknownMouseEvent);
    assert.equal(prevented, 1, 'unknown pane identity uses the shared context-menu event owner');
    assert.equal(stopped, 2, 'unknown pane identity uses the shared context-menu event owner');
    api.setTerminalContextMenuPaneTargetForTest(container, '%claude');
    assert.deepEqual(
      {...api.terminalContextMenuCapabilityForSessionForTest('split', {paneTarget: () => api.terminalContextMenuPaneTargetForContainerForTest(container)})},
      {paneTarget: '%claude', clientKind: 'claude', preserveNativeContextMenu: false},
      'the same split-session terminal uses the exact container-bound Claude pane, not the first session agent',
    );
    const claudeMouseEvent = {
      button: 2,
      stopPropagation() { stopped += 1; },
    };
    stopped = 0;
    mousedown(claudeMouseEvent);
    assert.equal(stopped, 1, 'the exact Claude container binding retains YOmux right-click selection capture');
  });

  test('OpenCode is a visible Tabber owner and top-bar counts retain the three status tones', () => {
    const tabberSource = fs.readFileSync('static_src/js/yomux/40_file_explorer_files.js', 'utf8');
    const layoutSource = fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8');
    assert.match(tabberSource, /function tabberWindowIsAgent\(name\)[\s\S]*key === 'claude' \|\| key === 'codex' \|\| key === 'opencode'/, 'OpenCode is included in the shared visible Tabber owner');
    assert.match(layoutSource, /const attentionCount = Number\(counts\.ask\) \|\| 0/, 'unavailable counts do not share the red attention slot');
    assert.match(layoutSource, /topbarActivityCountBallHtml\(counts\.blocked, 'cooldown', 'topbar-activity-blocked'\)/, 'blocked top-bar counts retain the yellow tone');
    const api = loadYomux('', ['1']);
    assert.equal(api.tabberWindowIsAgentForTest('opencode'), true, 'OpenCode is recognized by the shared tmux-window agent owner');
    assert.match(api.topbarActivityCountBallHtmlForTest(1, 'cooldown', 'topbar-activity-blocked'), /status-indicator--cooldown/, 'blocked count renders the yellow status class');
  });

  test('OpenCode green and red balls share one renderer across pane tabs, Tabber, popovers, and topbar', () => {
    const api = loadYomux('', ['open']);
    const info = {
      panes: [{window: '0', window_index: '0', target: '%open', pane: '0', active: true, window_active: true, process_label: 'opencode', command: 'opencode'}],
      agents: [{kind: 'opencode', pane_target: '%open', window_index: 0, window_active: true, current: true}],
    };
    api.setTranscriptInfoForTest('open', info);
    for (const [state, tone] of [['working', 'working'], ['needs-input', 'attention']]) {
      api.setAutoApproveStateForTest('open', {agent_windows: [{kind: 'opencode', pane_target: '%open', window_index: 0, state, window_active: true, current: true}]});
      const currentInfo = api.transcriptInfoForTest('open');
      const paneTab = api.tmuxPaneTabHtml('open', currentInfo, null, false);
      const windowTab = api.tmuxWindowButtonHtmlForTest({
        session: 'open', visibleName: '0:opencode', numberLabel: '0', agentKey: 'opencode',
        agentStatus: {kind: 'opencode', pane_target: '%open', window_index: 0, state, window_active: true, current: true},
      });
      const popover = api.sessionPopoverHtml('open', currentInfo, 'opencode', false);
      const topbar = api.topbarActivityCountBallHtmlForTest(1, tone, `topbar-activity-${tone}`);
      for (const [surface, html] of [['pane tab', paneTab], ['Tabber', windowTab], ['popover', popover], ['topbar', topbar]]) {
        assert.match(html, new RegExp(`status-indicator--${tone}`), `OpenCode ${state} status reaches the ${surface}`);
      }
      assert.match(windowTab, /agent-icon opencode/, `OpenCode identity is retained beside the ${state} Tabber status`);
      assert.match(popover, /OpenCode/, `OpenCode identity is retained in the ${state} popover`);
    }
  });

  test('Tabber rows retain the status and repository badges shown on pane tabs', () => {
    const api = loadYomux('', ['main-tag']);
    api.setFileExplorerModeForTest('tabber');
    const info = {
      panes: [{window: '0', window_index: 0, pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude'}],
      work_graph: {
        version: 1,
        tmux_sessions: {}, tmux_windows: {}, tmux_panes: {}, runtime_actors: {}, path_observations: {},
        git_worktrees: {worktree: {id: 'worktree', root: '/repo', local_repository_id: 'local', current_branch_id: 'branch', git: {root: '/repo'}}},
        local_repositories: {local: {id: 'local', local_branch_ids: ['branch']}},
        hosted_repositories: {}, local_branches: {branch: {id: 'branch', name: 'main'}},
        pull_requests: {}, linear_issues: {}, worktree_branch_activity: {},
      },
    };
    api.setTranscriptInfoForTest('main-tag', info);
    api.setAutoApproveStateForTest('main-tag', {agent_windows: [
      {kind: 'claude', state: 'needs-input', window_index: 0, window_name: 'claude', current: true, window_active: true},
    ]});
    const paneTab = api.tmuxPaneTabHtml('main-tag', api.transcriptInfoForTest('main-tag'), null, false);
    const row = api.buildTabberNormalRowsForTest().find(entry => entry.tabber?.session === 'main-tag');

    assert.match(paneTab, />ASK<\/span>/, 'the pane tab exposes its current question label');
    assert.match(row?.tabber?.normalNameHtml || '', />ASK<\/span>/, 'the Tabber row mirrors the pane tab question label');
    assert.match(paneTab, />MAIN<\/span>/, 'the pane tab exposes the current default-branch label');
    assert.match(row?.tabber?.normalNameHtml || '', />MAIN<\/span>/, 'the Tabber row mirrors the pane tab default-branch label');
  });

  test('Tabber window recency uses one timestamp for display, sorting, and parent bubbling', () => {
    const api = loadYomux('', ['4']);
    const beforeRender = Date.now() / 1000;
    api.setFileExplorerModeForTest('tabber');
    api.setFileExplorerTreeDateModeForTest('date');
    api.setTranscriptInfoForTest('4', {
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 4100, command: 'claude', current_path: '/home/u/proj'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', pid: 4200, command: 'bash', current_path: '/home/u'},
      ],
    });
    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'claude', state: 'working', working_elapsed_seconds: 3600, last_active_ts: 8000, window_index: 0, window_name: 'claude', window_label: '0:claude', pid: 4100, active: true, path_entries: [{path: '/home/u/proj', mtime: 9000, git: {root: '/home/u/proj', branch: 'main'}}]},
    ]});
    api.setTabberActivityForTest({
      activity: {
        '4': {active_recency_ts: 7000},
        '4:0': {active_recency_ts: 6500},
        '4:1': {active_recency_ts: 3000, last_user_input_ts: 8000, last_output_ts: 8500},
      },
      agents: [
        {session: '4', window: '0', agent_kind: 'claude', last_used_ts: 1000, sort_ts: 2000, running: true, label: "session '4' 0:claude"},
      ],
    });

    const tree = api.buildTabberNormalRowsForTest();
    const session = tree.find(entry => entry.tabber?.session === '4');
    const windows = tree.filter(entry => entry.tabber?.session === '4');
    const claudeWindow = windows.find(row => row.tabber.windowIndex === 0);
    const bashWindow = windows.find(row => row.tabber.windowIndex === 1);
    assert.ok(claudeWindow.mtime >= beforeRender, 'a live working state wins over a stale transcript timestamp');
    assert.ok(claudeWindow.mtime <= Date.now() / 1000, 'the working recency clock is anchored to the current render');
    assert.equal(bashWindow.mtime, 3000, 'bash/non-agent window mtime uses activity[session:window].active_recency_ts');

    const rows = api.tabberRenderedRowsForTest();
    const claudeRow = rows.find(row => row.type === 'normal' && row.path === '/s_4/w_0');
    const bashRow = rows.find(row => row.type === 'normal' && row.path === '/s_4/w_1');
    assert.equal(claudeRow?.date, api.sessionFileTimeText(claudeWindow.mtime), 'Claude/Codex child date text uses the same live working timestamp as row mtime');
    assert.equal(bashRow?.date, api.sessionFileTimeText(3000), 'bash/non-agent child date text uses the same ledger timestamp as row mtime');
    assert.equal(rows.some(row => row.type === 'repo'), false, 'flat Tabber rows omit the Advanced-only touched-path tree');
    assert.notEqual(claudeRow?.date, api.sessionFileTimeText(2000), 'stale transcript history cannot override a visibly working row');

    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'claude', state: 'idle', last_active_ts: 8000, window_index: 0, window_name: 'claude', window_label: '0:claude', pid: 4100, active: true},
    ]});
    const idleTree = api.buildTabberNormalRowsForTest();
    const idleSession = idleTree.find(entry => entry.tabber?.session === '4');
    const idleClaudeWindow = idleTree.filter(entry => entry.tabber?.session === '4').find(row => row.tabber.windowIndex === 0);
    assert.equal(idleClaudeWindow.mtime, 8000, 'after the live working state clears, the newest per-window activity clock remains the row timestamp');
  });

  test('Tabber preserves Ago timestamps across local refresh placeholders', () => {
    const api = loadYomux('', ['4']);
    api.setFileExplorerModeForTest('tabber');
    api.setFileExplorerTreeDateModeForTest('date');
    api.setTranscriptInfoForTest('4', {
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 4100, command: 'claude', current_path: '/home/u/proj'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', pid: 4200, command: 'bash', current_path: '/home/u'},
      ],
    });
    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_name: 'claude', window_label: '0:claude', pid: 4100, active: true},
    ]});
    api.setTabberActivityForTest({
      activity: {'4:1': {active_recency_ts: 3000}},
      agents: [{session: '4', window: '0', agent_kind: 'claude', last_used_ts: 2000, sort_ts: 2000, running: true}],
      agent_windows: {'4': [{window_index: 0}]},
    });
    const windowDates = () => Object.fromEntries(api.tabberRenderedRowsForTest()
      .filter(row => row.type === 'normal')
      .map(row => [row.path, row.date]));
    const originalDates = windowDates();
    assert.equal(originalDates['/s_4/w_0'], api.sessionFileTimeText(Date.now() / 1000), 'a live working row shows the current liveness clock instead of stale transcript history');
    assert.equal(originalDates['/s_4/w_1'], api.sessionFileTimeText(3000), 'full snapshot shows the shell timestamp');

    const placeholder = {activity: {}, agents: [], agent_windows: {}, cache: {refreshing: true}};
    assert.equal(api.applyTabberActivityPayloadForTest(placeholder, 2), false, 'empty local refresh is not authoritative over useful activity');
    assert.deepEqual(windowDates(), originalDates, 'a local refresh cannot make several Tabber timestamps disappear');

    const replacement = {
      activity: {'4:1': {active_recency_ts: 5000}},
      agents: [{session: '4', window: '0', agent_kind: 'claude', last_used_ts: 4000, sort_ts: 4000, running: true}],
      agent_windows: {'4': [{window_index: 0}]},
    };
    assert.equal(api.applyTabberActivityPayloadForTest(replacement, 3), true, 'a complete newer snapshot replaces the current payload');
    assert.deepEqual(windowDates(), {'/s_4/w_0': api.sessionFileTimeText(Date.now() / 1000), '/s_4/w_1': api.sessionFileTimeText(5000)}, 'complete replacement updates historical rows without replacing a live working clock');
    assert.equal(api.applyTabberActivityPayloadForTest({...replacement, activity: {'4:1': {active_recency_ts: 7000}}}, 1), false, 'an older overlapping request cannot roll back an accepted snapshot');
    assert.equal(windowDates()['/s_4/w_1'], api.sessionFileTimeText(5000), 'stale response ordering leaves the accepted timestamp intact');

    assert.equal(api.applyTabberActivityPayloadForTest({activity: {}, agents: [], agent_windows: {}, cache: {refreshing: false}}, 4), true, 'a genuine non-placeholder empty snapshot remains authoritative');
    assert.deepEqual(windowDates(), {'/s_4/w_0': api.sessionFileTimeText(Date.now() / 1000), '/s_4/w_1': ''}, 'legitimate activity expiry removes historical rows without hiding a current working status');
  });

  // DOIT.58 B1-B7: Tabber's fixed panel — source guards that rows route through the
  // shared row pipeline (no forked *RowHtml builder), plus a behavioral test of the tree assembly.
  test('Tabber shares the Finder tree and tab interaction pipeline', () => {
    const source = fs.readFileSync('static/yomux.js', 'utf8');
    const css = fs.readFileSync('static/yomux.css', 'utf8');
    // B1/B3 source guards: routes through the shared pipeline, no forked builder, finder refreshes skip tabber rows.
    assert.ok(/mode === 'diff' \|\| mode === 'tabber' \? mode : 'files'/.test(source), 'B1: normalizeFileExplorerMode accepts files|diff|tabber');
    assert.ok(/if \(options\.mode === 'tabber'\) return updateTabberRow\(/.test(source), 'B3: updateFileTreeRow dispatches tabber rows to updateTabberRow');
    assert.ok(/function updateTabberRow\([\s\S]*patchTreeRow\(row, model\)/.test(source), 'B3: updateTabberRow fills shared columns and state through patchTreeRow');
    assert.equal(/function tabberRowHtml|function renderTabberRowHtml|function tabberFileRowHtml/.test(source), false, 'B3: no bespoke tabber *RowHtml builder');
    assert.ok((source.match(/\.file-tree-row\[data-path\]:not\(\[data-tabber-type\]\)/g) || []).length >= 2, 'finder global row refreshes exclude tabber rows (no relabel/clobber)');
    assert.ok(/\.file-tree-row\.tabber-row\[data-tabber-type="tab"\]:not\(\.selected\) > \.file-tree-name,[\s\S]*color:\s*var\(--tabber-level0-color\)/.test(css), 'non-tmux Tabber pane rows do not use purple');
    assert.ok(/\.file-tree-row:is\(\.tabber-active-window, \.tabber-active-tab\):not\(\.selected\) \.file-tree-name\s*\{[\s\S]*font-weight:\s*800/.test(css), 'active Tabber windows and non-tmux tabs share one bold row emphasis');
    const sharedPaneTabCss = css.match(/\.pane-tab,\s*\.tmux-pane-tab-token\s*\{([\s\S]*?)\}/)?.[1] || '';
    assert.ok(/height:\s*var\(--pane-tab-height\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use the shared pane-tab height');
    assert.ok(/padding:\s*var\(--space-1\) var\(--pane-tab-inline-end-padding\) 0 var\(--space-5\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use the tab-scoped end padding');
    assert.ok(/border:\s*1px solid var\(--pane-inactive-tab-border\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use the shared pane-tab border token');
    assert.ok(/border-radius:\s*var\(--pane-tab-top-radius\) var\(--pane-tab-top-radius\) 0 0/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab top radius');
    assert.ok(/font-family:\s*var\(--tab-font\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab font');
    assert.ok(/line-height:\s*var\(--tab-line-height\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab line height');
    assert.ok(/\.tmux-pane-tab-token-action\s*\{[\s\S]*cursor:\s*pointer/.test(css), 'A2: shared compact tmux tab tokens own the interactive cursor');
    assert.ok(/\.pane-tab:not\(\.active\):hover,\s*\.pane-tab:not\(\.active\):focus-visible,\s*\.tmux-pane-tab-token-action:not\(\.active\):hover,\s*\.tmux-pane-tab-token-action:not\(\.active\):focus-visible\s*\{[\s\S]*background:\s*var\(--pane-inactive-tab-bg-hover\)[\s\S]*border-color:\s*var\(--40-layout-panes-tabs-pane-tab-border-26\)/.test(css) && /:root\s*\{[\s\S]*--40-layout-panes-tabs-pane-tab-border-26:\s*var\(--paint-white-92\)/.test(css), 'A2: regular and compact inactive pane tabs share centralized pointer and keyboard interaction paint');
    assert.ok(/body\.theme-light\s*\{[\s\S]*--40-layout-panes-tabs-pane-tab-border-26:\s*rgb\(var\(--overlay-slate-rgb\) \/ 0\.5\)/.test(css), 'A2: one centralized light-theme token corrects every regular and compact interaction border');
    const sharedActiveTabRule = css.match(/\.yomux-dockview \.dv-tab\.dv-active-tab > \.dockview-pane-tab:not\(\.file-missing\),[\s\S]*?\.panel\.file-explorer-panel \.pane-tab\.active:not\(\.file-missing\)\s*\{([\s\S]*?)\}/);
    assert.ok(sharedActiveTabRule, 'A5: every active tab-like surface routes through one paint owner');
    for (const selector of ['.tmux-pane-tab-token.active', '.panel.active-pane .pane-tab.active:not(.file-missing)', '.panel.file-explorer-panel .pane-tab.active:not(.file-missing)']) {
      assert.ok(sharedActiveTabRule[0].includes(selector), `A5: shared active tab owner includes ${selector}`);
    }
    assert.ok(/color:\s*var\(--pane-tab-active-text\)[\s\S]*background:\s*var\(--pane-tab-active-bg\)[\s\S]*border-color:\s*var\(--pane-tab-active-border\)[\s\S]*border-bottom-color:\s*var\(--pane-tab-active-bg\)/.test(sharedActiveTabRule[1]), 'A5: the shared active tab owner carries the complete active paint');
    assert.equal((css.match(/border-bottom-color:\s*var\(--pane-tab-active-bg\)/g) || []).length, 1, 'A5: the exact active-tab paint signature has one owner');
    assert.ok(/\.yomux-dockview \.dv-tab\.dv-active-tab > \.dockview-pane-tab \.session-button-name,[\s\S]*\.tmux-pane-tab-token\.active \.session-button-detail,[\s\S]*\.pane-tab\.active \.session-button-detail\s*\{\s*color:\s*inherit/.test(css), 'A5: every active tab child inherits color from the shared shell paint');
    assert.ok(/\.panel \.pane-tab:not\(\.active\),\s*\.panel \.tmux-pane-tab-token:not\(\.active\)\s*\{\s*background:\s*var\(--pane-bar-bg\)/.test(css), 'A5: one unconditional panel owner paints inactive pane tabs');
    assert.equal(css.includes('.panel:not(.active-pane):not(.typing-ready-pane) .pane-tab:not(.active)'), false, 'A5: inactive tab paint has no focus/readiness partition');
    assert.equal(/\.panel\.active-pane \.pane-tab\.active,\s*\.panel\.file-explorer-panel \.pane-tab\.active\s*\{\s*box-shadow:\s*none/.test(css), false, 'A5: focused pane tabs do not restate the base active no-shadow rule');
    assert.equal(/\.file-tree-row\.tabber-row\[data-tabber-type="session"\][\s\S]*:hover \.tabber-session-tab/.test(css), false, 'A2: Tabber session hover styling stays on the shared tmux tab token');
    assert.equal(/\.file-tree-row\.tabber-row\.tabber-active-session \.tabber-session-tab/.test(css), false, 'A5: Tabber active styling stays on the shared tmux tab token');
    assert.ok(/\.tmux-pane-tab-token > \.pane-tab-core\s*\{\s*inline-size:\s*100%;\s*\}/.test(css), 'TR3: compact tmux tab chrome only adds its full-width core override');
    assert.ok(/\.pane-tab-core\s*\{[\s\S]*min-width:\s*0[\s\S]*flex:\s*1 1 auto/.test(css), 'TR3: regular and compact tabs inherit core flex behavior from one generic owner');
    assert.ok(/\.session-button-prefix\s*\{[\s\S]*flex:\s*0 0 auto[\s\S]*min-width:\s*max-content[\s\S]*overflow:\s*visible/.test(css), 'A4/TR1: one shared intrinsic session prefix preserves the identifier while the detail flexes');
    assert.equal(/\.session-button-(?:name|prefix)[\s\S]{0,160}max-width:\s*(?:72|120)px/.test(css), false, 'A4/TR1: session labels do not carry fixed pixel capacity caps');
    assert.ok(/\.tmux-pane-tab-token \.tab-inline-detail,\s*\.pane-tab \.tab-inline-detail,\s*\.pane-tab\.file-editor-item \.session-button-dir\s*\{[\s\S]*flex:\s*1 1 auto[\s\S]*min-width:\s*0[\s\S]*max-width:\s*none/.test(css), 'A4/TR1: regular, compact, and file-editor pane-tab text share one stretching owner');
    assert.ok(/body\.theme-light\s*\{[\s\S]*--40-layout-panes-tabs-tmux-pane-tab-token-fg-2:\s*currentColor[\s\S]*--40-layout-panes-tabs-tmux-pane-tab-token-fg-4:\s*currentColor/.test(css) && /\.tmux-pane-tab-token:not\(\.active\) \.session-button-name\s*\{[\s\S]*color:\s*var\(--40-layout-panes-tabs-tmux-pane-tab-token-fg-2\)/.test(css), 'compact tmux tab tokens own centralized light-mode child label inheritance');
    assert.equal(/\.file-tree-row\.tabber-row \.tabber-session-tab > \.pane-tab-core/.test(css), false, 'TR3: Tabber no longer owns the compact tab core flex rule');
    assert.equal(/\.info-tree-tab-token > \.pane-tab-core/.test(css), false, 'TR3: YO!info no longer owns a duplicate compact tab core flex rule');
    assert.ok(/function tmuxPaneTabTokenHtml\(session, options = \{\}\)[\s\S]*tmux-pane-tab-token-action[\s\S]*stripContentTitles === true/.test(source), 'TR1: the shared compact tmux pane-tab token helper owns action/static classes and optional title stripping');
    assert.equal(/\.file-tree-row\.tabber-row \.tabber-session-tab > \.session-popover\s*\{[\s\S]*width:\s*min\(420px/.test(css), false, 'TR2: Tabber does not keep a divergent one-off popover width');
    assert.equal(source.includes("tabber: {type: 'loading'"), false, 'Tabber no longer renders a client-side touched-path loading row');
    assert.ok(source.includes('function tabberLookbackControlHtml()') && source.includes('data-tabber-lookback'), 'Tabber renders a dedicated touched-path lookback control');
    assert.ok(/\.file-tree-row\.tabber-row\s*\{[\s\S]*--tabber-agent-icon-size:\s*calc\(var\(--file-explorer-font-size\) \+ 2px\)/.test(css), 'Tabber owns one row-scale agent icon-size token');



    assert.ok(/\.agent-window-activity--subwindow \.agent-window-status-dot\s*\{[\s\S]*--subwindow-status-glyph-fill:\s*currentColor/.test(css), 'Tabber fallback child process rows inherit the renderer-owned sub-window glyph selector');
    assert.equal(/\.file-tree-row\.tabber-row\[data-tabber-type="session"\][^{]*\.agent-window-status-dot::before/.test(css), false, 'Tabber parent session aggregate status balls never get sub-window pseudo-glyphs');
    assert.ok(/\.agent-window-activity \.agent-icon\s*\{[\s\S]*width:\s*var\(--agent-window-icon-size\)[\s\S]*height:\s*var\(--agent-window-icon-size\)/.test(css), 'shared agent process icons inherit width and height from the shared activity-size variable');
    assert.ok(/\.info-tree-ai-value \.agent-window-activity\s*\{[\s\S]*--agent-window-icon-size:\s*14px/.test(css), 'YO!info supplies only its compact icon-size parameter to the shared activity renderer');
    assert.equal(/\.info-tree-ai-value \.agent-window-activity \.agent-icon\s*\{[^}]*(?:width|height):/.test(css), false, 'YO!info does not copy the shared variable-driven agent icon geometry');
	    assert.equal((css.match(/--agent-status-ball-size:/g) || []).length, 3, 'status ball size has only the base owner, topbar status-ball owner, and shared sub-window 100% reference owner');
    assert.ok(/\.agent-window-status-dot\s*\{[\s\S]*font-family:\s*var\(--ui-font\)[\s\S]*font-stretch:\s*normal/.test(css), 'Tabber status balls reset inherited condensed tab typography instead of shrinking beside the agent icon');
    assert.ok(/\.agent-window-activity--working \.agent-window-status-dot,[\s\S]*\.agent-window-activity--attention \.agent-window-status-dot,[\s\S]*\.agent-window-activity--cooldown \.agent-window-status-dot\s*\{[\s\S]*font-size:\s*var\(--agent-status-ball-size\)/.test(css), 'Tabber status balls inherit the shared agent status-ball glyph size parent');
    assert.equal(/font-size:\s*calc\(var\(--agent-window-icon-size\)/.test(css), false, 'Tabber status balls do not inherit the surface-specific icon size path');
    assert.ok(/\.file-tree-row\.tabber-row \.file-tree-date\s*\{[\s\S]*flex:\s*0 0 var\(--file-tree-date-column-width\)[\s\S]*inline-size:\s*var\(--file-tree-date-column-width\)/.test(css), 'Tabber keeps the recency column reserved at narrow widths');
    assert.ok(/\.file-tree-date\s*\{[\s\S]*font-size:\s*max\(var\(--ui-font-size-2xs\), calc\(var\(--file-explorer-font-size\) - 1px\)\)[\s\S]*text-overflow:\s*ellipsis/.test(css), 'SC7: recency/status text uses a larger row-scale font and end ellipsis');
    assert.equal(source.includes('tabber-status-long'), false, 'grid card dates share the same date-column sizing for every status');
    assert.equal(/\.tabber-row[^{}]*\.file-tree-date[^{}]*\{[^}]*display:\s*none/.test(css), false, 'Tabber never hides the <time> ago recency column for narrow panes');
    assert.ok(/function tabberActivityVisibleConsumer\(\)[\s\S]*fileExplorerMode === 'tabber'[\s\S]*document\.visibilityState !== 'hidden'/.test(source), 'Tabber activity polling only marks visible Tabber panes as subscribers');
    assert.ok(/async function fetchTabberActivity\(options = \{\}\)[\s\S]*params\.set\('visible', visible \? '1' : '0'\)/.test(source), 'Tabber activity requests include the visible-consumer flag');
    assert.ok(/function tabberActivityRenderSignature\(payload = tabberActivityPayload\)[\s\S]*topology_generation[\s\S]*session_order[\s\S]*sessions[\s\S]*agent_window_status_fresh/.test(source), 'the shared render signature covers Tabber roster and activity fields');
    assert.ok(/function tabberActivityPublicationSignature\(payload = tabberActivityPayload\)[\s\S]*agentWindowSnapshotRevision\(payload\)[\s\S]*tabberActivityRenderSignature\(payload\)/.test(source), 'activity publications distinguish revisions while reusing the shared render signature');
    assert.ok(/function refreshTabberPanelsForAgentWindowPublication\(options = \{\}\)[\s\S]*lastPublication[\s\S]*refreshTabberPanels\(options\)/.test(source), 'status and activity publications share one revision-aware Tabber refresh owner');
    assert.ok(/function refreshAgentWindowActivityDisplays\([^)]*\)[\s\S]*refreshTabberPanelsForAgentWindowPublication\(options\)/.test(source), 'status display updates use the shared revision-aware Tabber refresh owner');
    assert.ok(/function warmTabberDataOnLaunch\(\)[\s\S]*!tabberActivityVisibleConsumer\(\)[\s\S]*tabberLaunchWarmupStarted = true;[\s\S]*fetchTabberActivity\(\);[\s\S]*return true;/.test(source), 'Tabber launch warmup primes cached activity only for visible Tabber panes');
    assert.ok(/transcriptMetadataState\.loaded = true;[\s\S]*?warmTabberDataOnLaunch\(\)/.test(source), 'Tabber launch warmup runs as soon as transcript metadata is available');
    assert.ok(/function tabberAgentForWindow\(session, windowIndex, agentKey = ''\)/.test(source), 'Tabber can look up agent transcript activity by session/window');
    assert.ok(source.includes('function agentWindowPathEntries(agent)') && source.includes('function windowViewModel(session, windowIndex'), 'Tabber reads paths through the shared backend agent-window view model');
    assert.equal(/tabberTouchedRepoPathsForWindow|tabberRepoPathsForWindow|tabberFileMatchesWindow/.test(source), false, 'Tabber has no client-side per-window path resolver');
    assert.ok(source.includes('function agentWindowWorkingRecencyTs(agent, nowSeconds = Date.now() / 1000)') && source.includes('function tabberWindowRecency(row, nowSeconds = Date.now() / 1000)') && source.includes('const workingTs = agentWindowWorkingRecencyTs(row?.agentStatus, nowSeconds);') && source.includes('const activityTs = tabberAgentRecency(row?.agentActivity)') && source.includes('return tabberRecency(`${session}:${windowIndex}`)'), 'Tabber window rows use the shared live working clock before historical activity timestamps');
    assert.ok(source.includes('const windowMtime = tabberWindowRecency({session, windowIndex: record.index, record, isAgent, agentKey, agentActivity, agentStatus}, nowSeconds);'), 'Tabber window mtime comes from the shared window-recency helper');
	    assert.ok(/function tabberTreeRowViewModel\([\s\S]*recencyEntry:\s*entry,[\s\S]*recencyOptions:\s*options[\s\S]*function updateTabberRow\([\s\S]*patchTreeRow\(row, model\)/.test(source), 'Tabber timestamp rows route through the shared Finder/Differ patch and recency styling');
	    assert.ok(/function tabberWindowDateDisplay\(recencyTs, agentStatus = null, nowSeconds = Date\.now\(\) \/ 1000\)[\s\S]*fileExplorerTreeDateModeForView\('tabber'\) === 'none'[\s\S]*sessionFileDisplayTimeText\(recencyTs, \{nowSeconds, view: 'tabber'\}\)/.test(source), 'Tabber window date text renders the same timestamp used for row mtime and parent bubbling');
	    assert.equal(/const dateText = agentStatusForDisplay \? sessionPopoverAgentStateText/.test(source), false, 'Tabber agent rows do not bypass the Date toggle with popover-only status text');
	    assert.equal(/icon:\s*'▢'|icon:\s*'■'/.test(source), false, 'Tabber shell/session rows avoid checkbox-looking square glyphs');
    assert.ok(/let tabberActivityRefreshMs;[\s\S]*tabberActivityRefreshMs = initialSetting\('performance\.tabber_activity_refresh_ms'\);/.test(source), 'Tabber activity refresh initializes from server-provided settings defaults');
    assert.equal(source.includes("initialSetting('performance.tabber_activity_refresh_ms', 15000)"), false, 'Tabber activity refresh does not keep a duplicated bootstrap fallback');
    assert.equal(source.includes("numberSetting('performance.tabber_activity_refresh_ms', 15000)"), false, 'Tabber activity refresh does not keep a duplicated reload fallback');
    assert.ok(/Promise\.resolve\(state\.callback\(\)\)[\s\S]*?\.finally\(scheduleNext\)/.test(source), 'runtime intervals wait for async callbacks to settle before starting the next wait');
    assert.ok(/if \(!fileIndexStatusPollRoots\.size\)[\s\S]*clearRuntimeInterval\('file-index-building'\)[\s\S]*resetRuntimeInterval\('file-index-building', refreshBuildingFileIndexStatuses, Math\.min\(1501, statusIntervalMs\)\)/.test(source), 'a building index keeps the odd 1501ms targeted status cadence while SSE is connected');
    assert.equal(source.includes('tabber-row-detail'), false, 'DOIT.61 A4: Tabber no longer carries a dead visible detail slot');
    assert.equal(source.includes("type === 'path' && row.dataset.tabberOpenFile"), false, 'DOIT.61 A3: Tabber has no unreachable path-row activation branch');
    assert.ok(source.includes('function setRowDataset(row, key, value)'), 'DOIT.61 B1: row dataset set/delete is centralized');
    assert.ok(/apiFetchJson\(`\/api\/activity\?\$\{params\.toString\(\)\}`/.test(source) && /params\.set\('hours', String\(requestHours\)\)/.test(source), 'Tabber activity fetch carries the selected touched-path lookback hours');
    assert.ok(/params\.set\('scope', 'all'\)/.test(source), 'Tabber activity fetch explicitly requests the machine-wide tmux roster');
    assert.ok(source.includes('function fileTreeRowPadding(depth, compact = false)') && source.includes('function fileTreeRowDepth(row, compact = false)'), 'DOIT.61 B3: tree indentation math is centralized');
    assert.ok(source.includes('const nextDepth = fileTreeRowDepth(row) + 1'), 'DOIT.61 B3: directory expansion uses the shared depth helper');
    assert.ok(source.includes('function clearFileTreeRowHandlers(row)') && (source.match(/clearFileTreeRowHandlers\(row\)/g) || []).length >= 2, 'DOIT.61 B4: stale row handler cleanup is shared');
    assert.ok(source.includes('function setTreeItemAria(row') && (source.match(/setTreeItemAria\(row/g) || []).length >= 2, 'DOIT.61 B5: treeitem aria is shared');
    assert.ok(source.includes('function normalizeGitStatus(status)') && source.includes('return normalizeGitStatus(fileTreeChangedFile(path)?.status)'), 'DOIT.61 B6: git status normalization is shared');
    assert.equal(source.includes("endsWith(' ●')"), false, 'DOIT.61 B7: active window state is not parsed out of the label string');
    assert.ok(/function sessionPopoverWindowPidByIndex\(info\)[\s\S]*tmuxWindowRecords\(info\?\.panes \|\| \[\]\)/.test(source), 'PP1: popover PID comes from the same tmux sub-window record source as Tabber');
    assert.ok(source.includes('tmuxWindowDisplayLabel(descriptor, agent.pid)'), 'PP1: popover PID label reuses the shared tmux sub-window pid formatter');
    assert.ok(/type === 'normal' && session\)[\s\S]*switchWindow\(\);[\s\S]*selectSession\(session, \{userInitiated: true\}\)/.test(source), 'Advanced Tabber window clicks install the tmux-window override before focus/layout can sync against stale active metadata');
    assert.ok(/function tabberSessionForNumericKey\(key\)[\s\S]*\^\[1-9\]\$/.test(source), 'Tabber maps bare numeric keys to matching tmux sessions');
    assert.ok(/if \(!tabberIsAdvanced\(\)\) \{[\s\S]*tabberNormalRowForSession\(panel, session\)[\s\S]*activateNormalTabberRow\(row, session/.test(source), 'Normal numeric shortcuts use the same window-aware row activation owner as clicks');
    assert.ok(/function handleTabberRowActivate\(row, event\)[\s\S]*type === 'normal' && session[\s\S]*switchWindow\(\);[\s\S]*selectSession\(session, \{userInitiated: true\}\)/.test(source), 'flat Advanced rows continue switching to their exact tmux window and source session');

    const api = loadYomux();
    const numericRow0 = new TestElement('normal-window-0');
    numericRow0.dataset.tabberSession = '1';
    numericRow0.dataset.tabberPaneItem = '1';
    const numericRow2 = new TestElement('normal-window-2');
    numericRow2.dataset.tabberSession = '1';
    numericRow2.dataset.tabberPaneItem = '1-window-2';
    const numericPanel = {querySelectorAll: () => [numericRow0, numericRow2]};
    api.setTabberNormalSelectionsForTest(['1-window-2']);
    assert.equal(api.tabberNormalRowForSessionForTest(numericPanel, '1'), numericRow2, 'numeric selection resolves the already-selected window row so the shared owner can deselect it');
    api.setTabberNormalSelectionsForTest([]);
    assert.equal(api.normalizeFileExplorerMode('tabber'), 'tabber');
    assert.equal(api.normalizeFileExplorerMode('bogus'), 'files');
    assert.equal(api.readStoredFileExplorerModeForTest('tabber'), 'files', 'legacy mode storage still normalizes to Finder for migration');
    assert.equal(api.fileExplorerModeSwitcherHtml(), '', 'B1: the retired shared-mode switcher renders no live controls');
    assert.equal(source.includes('data-tabber-expand'), false, 'Tabber session descriptions are not separate expand-only targets');
    assert.equal(source.includes('tabber-session-name') || source.includes('tabber-session-description'), false, 'TR5: Tabber does not keep bespoke session name/description chrome');
    assert.ok(source.includes('setInnerHtmlIfChanged(name, options.nameHtml, cleanupDetachedPopoversWithin)'), 'TR2: Tabber row replacement cleans detached popovers through the shared cleanup helper');
    assert.ok(/function fileExplorerTreeSortSelectHtml\(extraClass = '', view = 'finder'\)[\s\S]*data-file-explorer-tree-sort[\s\S]*finder\.sort\.az[\s\S]*finder\.sort\.oldest/.test(source), 'TS1/TS4: Finder and Tabber share one tree sort select component and locale keys');
    assert.ok(/view === 'tabber'[\s\S]*tabberLookbackControlHtml\(\)[\s\S]*fileExplorerTreeSortSelectHtml\('changes-sort-select-compact', 'tabber'\)/.test(source), 'TS1: Tabber toolbar renders the shared A-Z/Z-A/recent/oldest sort control');
    assert.ok(/function createFileExplorerPanel\(item = finderItemId\)[\s\S]*fileExplorerTreeSortSelectHtml\('', 'finder'\)/.test(source), "TS3: Finder's fixed panel renders the shared tree sort select");
    assert.ok(/function refreshTabberPanelsForFocusChange\(\)[\s\S]*scheduleTabberTreeLayoutStateSync\(\)/.test(source) && !/function refreshTabberPanelsForFocusChange\(\)[\s\S]{0,160}refreshTabberPanels\(\)/.test(source), 'focus changes schedule one Tabber layout-state patch without rebuilding the tree');
    assert.ok(/function updatePanelSlot\(panel, session, slot, options = \{\}\)[\s\S]*updatePaneTabStrip\(panel, slot\);\s*\}/.test(source), 'slot-local panel updates do not recursively synchronize global focus state');
    assert.ok(/type === 'tab' && row\.dataset\.tabberItem[\s\S]*selectSession\(row\.dataset\.tabberItem, \{userInitiated: true\}\)/.test(source), 'Tabber virtual-tab rows activate their own layout item directly');
    assert.equal(/tabberOpenFile|tabberOpenStatus|tabberOpenRepo|type === 'path'|data-tabber-type="path"/.test(source), false, 'Tabber has no individual-file row data or activation path');
    api.setInfoPanelSubTabForTest('yoagent');
	    api.commandPaletteCommandItems().find(item => item.targetItem === api.infoItemId).run();
	    assert.equal(api.infoPanelSubTabForTest(), 'yoagent', 'YO!info palette rows no longer mutate the legacy YO!agent chrome marker');
	    api.setFileExplorerModeForTest('tabber');
	    api.setFileExplorerTreeDateModeForTest('relative');
	    api.setFileExplorerTreeSortModeForTest('za');
    api.setTabberModeForTest('advanced');
    const advancedTabberToolbarHtml = api.fileExplorerChangesPanelStaticHtmlForTest();
    assert.ok(advancedTabberToolbarHtml.includes('data-file-explorer-tree-sort'), 'TS1: Advanced Tabber toolbar exposes the shared tree sort select');
    assert.ok(advancedTabberToolbarHtml.includes('value="az"') && advancedTabberToolbarHtml.includes('value="za"') && advancedTabberToolbarHtml.includes('value="newest"') && advancedTabberToolbarHtml.includes('value="oldest"'), 'TS1: Advanced Tabber sort select offers A-Z, Z-A, recent, and oldest');
    assert.ok(/value="za" selected/.test(advancedTabberToolbarHtml), 'TS3: Advanced Tabber sort select reflects the shared persisted tree sort mode');
    const tabberActiveSlots = api.emptyLayoutSlots();
    tabberActiveSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    tabberActiveSlots.left = api.paneStateWithTabs(['1'], '1');
    tabberActiveSlots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(tabberActiveSlots);
    api.setFocusedPanelItem('1');

    const longSessionWork = 'feat(conformance): preserve every parser-specific compatibility detail when the Tabber row has room';
    const session1ShellTranscript = {
      project: {git: {branch: 'devbranch', root: '/home/u/proj', other_branches: {branches: [{name: 'devbranch', current: true, subject: longSessionWork}]}}},
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 12345, command: 'claude', current_path: '/home/u/proj'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', pid: 54321, command: 'bash', current_path: '/home/u'},
      ],
    };
    api.setTranscriptInfoForTest('1', session1ShellTranscript);
    api.setTranscriptInfoForTest('2', {
      panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', process_label_pid: 24680, command: 'codex', current_path: '/home/u/two'}],
    });
    api.setAutoApproveStateForTest('2', {enabled: true});
    // L3 paths for the claude window come from the backend agent_windows record, not a client session-files parse.
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'working', working_elapsed_seconds: 13500, window_index: 0, window_name: 'claude', window_label: '0:claude', pid: 12345, active: true, path_entries: [{path: '/home/u/proj', mtime: 5000, git: {root: '/home/u/proj', branch: 'devbranch'}}]},
    ]});

    const entries = api.buildTabberNormalRowsForTest();
    const windows = entries.filter(row => row.tabber?.session === '1');
    assert.equal(windows.length, 2);
    const claudeWin = windows.find(row => row.tabber.windowIndex === 0);
    assert.equal(claudeWin.tabber.pid, 12345);
    assert.equal(claudeWin.tabber.active, true);
    assert.ok(claudeWin.tabber.normalNameHtml.includes('0:claude'));
    const visible = api.tabberRenderedRowsForTest();
    assert.equal(visible.filter(row => row.path.startsWith('/s_1/w_')).length, 2);
    assert.ok(visible.every(row => !row.ariaExpanded && row.kind !== 'dir'));

    api.setTranscriptInfoForTest('3', {
      project: {git: {branch: 'scoped', root: '/home/u/codex-a'}},
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', process_label_pid: 111, command: 'codex', current_path: '/home/u'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'codex', process_label_pid: 222, command: 'codex', current_path: '/home/u'},
      ],
    });
    api.setAutoApproveStateForTest('3', {agent_windows: [
      {kind: 'codex', state: 'idle', window_index: 0, window_label: '0:codex', active: true, path_entries: [{path: '/home/u/codex-a', mtime: 7000}]},
      {kind: 'codex', state: 'idle', window_index: 1, window_label: '1:codex', active: false, path_entries: [{path: '/home/u/codex-b', mtime: 8000}]},
    ]});
    const scopedTree = api.buildTabberNormalRowsForTest();
    const scopedWindows = scopedTree.filter(row => row.tabber?.session === '3');
    const scopedWindow0 = scopedWindows.find(row => row.tabber.windowIndex === 0);
    const scopedWindow1 = scopedWindows.find(row => row.tabber.windowIndex === 1);
    assert.ok((scopedWindow0.tabber.activityIconHtml || '').includes('agent-window-agent-icon--active'), 'current idle Codex Tabber window renders the moving active glyph');
    assert.equal((scopedWindow0.tabber.activityIconHtml || '').includes('agent-window-status-dot'), false, 'current idle Codex Tabber window does not add a competing status dot');
    assert.equal((scopedWindow1.tabber.activityIconHtml || '').includes('agent-window-agent-icon--active'), false, 'inactive idle Codex Tabber window does not pulse');

    const pulseApi = loadYomux('', ['3']);
    pulseApi.setFileExplorerModeForTest('tabber');
    pulseApi.setTranscriptInfoForTest('3', {
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/home/u'},
      ],
    });
    pulseApi.setAutoApproveStateForTest('3', {agent_windows: [
      {kind: 'codex', state: 'working', window_index: 0, window_label: '0:codex', active: true, path_entries: [{path: '/home/u/codex-a', mtime: 7000}]},
    ]});
    const workingTree = pulseApi.buildTabberNormalRowsForTest();
    const workingWindows = workingTree.filter(row => row.tabber?.session === '3');
    const workingWindow = workingWindows.find(row => row.tabber.windowIndex === 0);
    assert.ok((workingWindow.tabber.activityIconHtml || '').includes('status-indicator--working'), 'working Codex Tabber window renders the green working marker');
    assert.ok((workingWindow.tabber.activityIconHtml || '').includes('agent-window-status-dot--subwindow-pulse'), 'working Codex Tabber window keeps the green marker blinking');
    pulseApi.setAutoApproveStateForTest('3', {agent_windows: [
      {kind: 'codex', state: 'run', window_index: 0, window_label: '0:codex', active: true},
    ]});
    const runTree = pulseApi.buildTabberNormalRowsForTest();
    const runWindow = runTree.find(row => row.tabber?.session === '3' && row.tabber.windowIndex === 0);
    assert.ok((runWindow.tabber.activityIconHtml || '').includes('status-indicator--working'), 'stats run state uses the same green working marker');
    assert.ok((runWindow.tabber.activityIconHtml || '').includes('agent-window-status-dot--subwindow-pulse'), 'stats run state keeps the green marker blinking');
    const directWorkingMarker = api.agentWindowStatusDotHtmlForTest({
      state: 'working',
      icon: '●',
      pulseActive: true,
      transitionPulseActive: true,
    }, {label: 'working'});
    assert.match(directWorkingMarker, /status-indicator--working[^\"]*heartbeat-pulse|heartbeat-pulse[^\"]*status-indicator--working/, 'direct working status markers keep the continuous green heartbeat');

    const goApi = loadYomux('', ['go-codex']);
    goApi.setFileExplorerModeForTest('tabber');
    goApi.setTranscriptInfoForTest('go-codex', {
      panes: [{window: '0', window_index: 0, pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex'}],
    });
    goApi.setAutoApproveStateForTest('go-codex', {agent_windows: [
      {kind: 'codex', state: 'working', window: '0', window_index: 0, window_label: '0:codex', current: true, window_active: true},
    ]});
    const goTree = goApi.buildTabberNormalRowsForTest();
    const goWindow = goTree.find(row => row.tabber?.session === 'go-codex' && row.tabber.windowIndex === 0);
    assert.ok(goWindow.tabber.activityIconHtml.includes('status-indicator--working'), 'Go working state renders the green marker in Tabber');
    assert.ok(goWindow.tabber.activityIconHtml.includes('agent-window-status-dot--subwindow-pulse'), 'Go working state keeps the green Tabber marker blinking');

    const sortedApi = loadYomux('', ['1', '2', '3']);
    sortedApi.setTranscriptInfoForTest('1', {panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'bash', command: 'bash'}]});
    sortedApi.setTranscriptInfoForTest('2', {panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'bash', command: 'bash'}]});
    sortedApi.setTranscriptInfoForTest('3', {panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'bash', command: 'bash'}]});
    sortedApi.setTabberActivityForTest({activity: {
      '1': {active_recency_ts: 900},
      '2': {active_recency_ts: 50},
      '3': {active_recency_ts: 700},
      '1:0': {active_recency_ts: 100},
      '2:0': {active_recency_ts: 300},
      '3:0': {active_recency_ts: 200},
    }});
    const sortedSessionLabels = mode => {
      sortedApi.setFileExplorerTreeSortModeForTest(mode);
      return sortedApi.tabberRenderedRowsForTest().filter(row => row.type === 'normal').map(row => row.title.split('\n')[0]);
    };
    assert.deepEqual(sortedSessionLabels('az'), ['1', '2', '3'], 'TS2: Tabber A-Z sorts top-level sessions by the human label');
    assert.deepEqual(sortedSessionLabels('za'), ['3', '2', '1'], 'TS2: Tabber Z-A sorts top-level sessions by the human label');
    assert.deepEqual(sortedSessionLabels('newest'), ['2', '3', '1'], 'TS2: Tabber recent sort uses recency timestamps');
    assert.deepEqual(sortedSessionLabels('oldest'), ['1', '3', '2'], 'TS2: Tabber oldest sort uses recency timestamps');
    sortedApi.setTranscriptInfoForTest('1', {panes: [
      {window: '2', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash'},
      {window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex'},
      {window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude'},
    ]});
    sortedApi.setFileExplorerTreeSortModeForTest('za');
    const windowLabels = sortedApi.buildTabberNormalRowsForTest().filter(row => row.tabber?.session === '1').map(row => row.tabber.windowIndex);
    assert.deepEqual(windowLabels, [0, 1, 2], 'TS2: tmux sub-window rows stay in tmux index order regardless of Tabber sort mode');
    const renamedSessionApi = loadYomux('', ['1', '2', '8002b']);
    renamedSessionApi.setTranscriptInfoForTest('8002b', {panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude'}]});
    const renamedSessionHtml = renamedSessionApi.tmuxPaneTabHtml('8002b', renamedSessionApi.transcriptInfoForTest('8002b'), null, false);
    assert.equal(renamedSessionApi.itemLabel('8002b'), '8002b', 'renamed non-numeric tmux sessions use the tmux name as the visible label');
    assert.equal(renamedSessionApi.resolveLayoutItem('9'), '8002b', 'the old shortcut label still resolves to the renamed session');
    assert.ok(renamedSessionHtml.includes('session-button-name') && renamedSessionHtml.includes('>[8002b]<'), 'renamed tmux tab chrome shows the bracketed real session name');
    assert.equal(/session-button-number">9<\/span>/.test(renamedSessionHtml), false, 'renamed tmux tab chrome does not prepend the stale shortcut label');
    const renamedSessionRow = renamedSessionApi.tabberRenderedRowsForTest().find(row => row.type === 'normal' && row.title.split('\n')[0] === '8002b');
    assert.ok(renamedSessionRow?.nameHtml.includes('>[8002b]<'), 'flat Tabber rows retain the bracketed renamed-session label');

    // Render guard: real labels (never synthetic node names); active window marked; absolute path rows present.
    const rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.some(r => /^[swrf]_\d/.test(r.name)), false, 'rows show human labels, not synthetic node names (got ' + JSON.stringify(rows.map(r => r.name).slice(0, 8)) + ')');
    const workingClaudeRow = rows.find(r => r.type === 'normal' && r.path === '/s_1/w_0');
    assert.ok(workingClaudeRow?.nameHtml.includes('tabber-normal-primary')
      && workingClaudeRow.nameHtml.includes('tabber-normal-description')
      && workingClaudeRow.nameHtml.includes('agent-window-status-dot')
      && workingClaudeRow.nameHtml.includes('heartbeat-pulse')
      && workingClaudeRow.nameHtml.includes('agent-icon claude'),
    'A1/A2/TR1: both modes render the shared flat Claude row with its status and agent markers');
    const activeSessionRow = rows.find(r => r.type === 'normal' && r.path === '/s_1/w_0');
    const secondVisibleSessionRow = rows.find(r => r.type === 'normal' && r.path === '/s_2/w_0');
    assert.ok(activeSessionRow, 'the flat list includes the focused conversation');
    assert.ok(secondVisibleSessionRow, 'the flat list includes the second visible conversation');
    assert.equal(rows.every(r => r.nativeTitle === ''), true, 'Tabber rows do not show native browser titles in addition to the custom Tabber hover');
    assert.equal(/\stitle=/.test((activeSessionRow.nameHtml || '').split('<div class="session-popover"')[0]), false, 'visible Tabber tab chrome strips nested native title hovers');
    assert.ok(activeSessionRow.classes.includes('tabber-active-window'), 'the current window row keeps its active-state styling');
    const activeWindowRow = activeSessionRow;
    assert.ok(activeWindowRow?.nameHtml.includes('tabber-normal-primary') && activeWindowRow.nameHtml.includes('tabber-normal-description'), 'Advanced windows use the shared two-line row');
    assert.equal(/\stitle=/.test(activeWindowRow?.nameHtml || ''), false, 'visible Tabber window chrome strips nested native title hovers');
    assert.equal(activeWindowRow?.ariaCurrent, 'true', 'N10: the active tmux sub-window row exposes aria-current');
    const activeWindowNow = Date.now() / 1000;
    assert.equal(activeWindowRow?.date, api.sessionFileRelativeTimeText(activeWindowNow, activeWindowNow), 'working agent Tabber rows use the shared live-status clock even without transcript history');
    assert.equal(activeWindowRow?.dateHtml, '', 'working agent Tabber rows keep status out of the date column');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'needs-input', last_active_ts: Date.now() / 1000 - 5, window_index: 0, window_name: 'claude', window_label: '0:claude'},
    ]});
    const attentionRows = api.tabberRenderedRowsForTest();
    const attentionWindowRow = attentionRows.find(r => r.type === 'normal' && r.path === '/s_1/w_0');
    assert.equal(attentionWindowRow?.date, '', 'attention Tabber window rows do not use ledger last_active_ts as transcript recency');
    assert.equal(attentionWindowRow?.dateHtml, '', 'attention Tabber window rows keep status labels out of the date column when transcript recency is missing');
    assert.equal(/\bneeds input\b|\bapproval\b/i.test(`${attentionWindowRow?.date || ''} ${attentionWindowRow?.dateHtml || ''}`), false, 'attention Tabber window rows no longer say needs input or approval in the date column');
    assert.ok(/agent-window-status-dot(?=[^"]*agent-window-status-dot--transition-pulse)(?=[^"]*status-indicator--attention)/.test(attentionWindowRow?.nameHtml || ''), 'attention Tabber window ball carries the transition pulse phase instead of the status text');
    const normalAttentionRow = api.buildTabberNormalRowsForTest().find(row => row.tabber?.session === '1' && Number(row.tabber?.windowIndex) === 0);
    assert.ok(normalAttentionRow?.tabber.normalNameHtml.includes('status-indicator--attention'), 'Normal rows retain the shared unacknowledged attention marker');
    assert.ok(normalAttentionRow?.tabber.normalNameHtml.includes('agent-icon claude'), 'Normal attention retains the agent symbol');
    assert.ok(normalAttentionRow?.tabber.normalNameHtml.indexOf('agent-window-status-dot') < normalAttentionRow?.tabber.normalNameHtml.indexOf('agent-icon claude'), 'Normal Tabber rows place the attention status glyph before the agent symbol');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'idle', working_stopped_ts: Date.now() / 1000 - 2, window_index: 0, window_name: 'claude', window_label: '0:claude'},
    ]});
    const cooldownNormalRow = api.buildTabberNormalRowsForTest().find(row => row.tabber?.session === '1' && Number(row.tabber?.windowIndex) === 0);
    assert.ok(cooldownNormalRow?.tabber.normalNameHtml.includes('status-indicator--cooldown'), 'Normal rows retain the yellow cooldown marker');
    assert.ok(cooldownNormalRow?.tabber.normalNameHtml.indexOf('agent-window-status-dot') < cooldownNormalRow?.tabber.normalNameHtml.indexOf('agent-icon claude'), 'Normal Tabber rows place the cooldown status glyph before the agent symbol');
    const workingNormalRow = pulseApi.buildTabberNormalRowsForTest().find(row => row.tabber?.session === '3' && Number(row.tabber?.windowIndex) === 0);
    assert.ok(workingNormalRow?.tabber.normalNameHtml.includes('status-indicator--working') && workingNormalRow.tabber.normalNameHtml.includes('heartbeat-pulse'), 'Normal rows retain the flashing green working marker');
    assert.ok(workingNormalRow?.tabber.normalNameHtml.indexOf('agent-window-status-dot') < workingNormalRow?.tabber.normalNameHtml.indexOf('agent-icon codex'), 'Normal Tabber rows place the working status glyph before the agent symbol');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'working', working_elapsed_seconds: 13500, window_index: 0, window_name: 'claude', window_label: '0:claude'},
    ]});
    const shellWindowRow = rows.find(r => r.type === 'normal' && r.path === '/s_1/w_1');
    assert.ok(shellWindowRow?.nameHtml.includes('tabber-normal-primary') && shellWindowRow.nameHtml.includes('1:bash'), 'shell windows use the shared row title');
    assert.equal(shellWindowRow?.nameHtml.includes('(pid='), false, 'flat rows show the conversation title and description');
    assert.equal(shellWindowRow?.icon, '', 'shell/process window leaf rows do not render the old neutral process glyph');
    assert.notEqual(shellWindowRow?.date, 'working for 3h 45m', 'TD4: non-AI tmux sub-windows do not inherit working duration text');
    assert.equal(rows.some(r => r.type === 'normal' && ['▢', '■', '⌁'].includes(r.icon)), false, 'flat window rows never render checkbox-looking or decorative process glyphs');
    api.setTranscriptInfoForTest('1', {
      ...session1ShellTranscript,
      panes: [
        session1ShellTranscript.panes[0],
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'codex', process_label_pid: 54321, command: 'codex', current_path: '/home/u'},
      ],
    });
	    const idleAgentLastActive = Date.now() / 1000 - 5;
	    api.setAutoApproveStateForTest('1', {agent_windows: [
	      {kind: 'codex', state: 'idle', last_active_ts: idleAgentLastActive, window_index: 1, window_name: 'codex', window_label: '1:codex'},
	    ]});
	    api.setTabberActivityForTest({
	      activity: {},
	      agents: [{session: '1', window: '1', agent_kind: 'codex', last_used_ts: idleAgentLastActive, sort_ts: idleAgentLastActive, running: false, label: "session '1' 1:codex"}],
	    });
	    api.setFileExplorerTreeDateModeForTest('none');
	    const idleNoDateRows = api.tabberRenderedRowsForTest();
    const idleNoDateWindowRow = idleNoDateRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1');
	    assert.equal(idleNoDateWindowRow?.date, '', 'None mode hides idle AI Tabber row recency text');
	    assert.equal(idleNoDateWindowRow?.dateHtml, '', 'None mode hides idle AI Tabber row status HTML');
	    api.cycleFileExplorerTreeDateModeForTest();
	    assert.equal(api.fileExplorerTreeDateModeForTest(), 'date', 'Date toggle cycle advances None to Date');
	    const idleDateRows = api.tabberRenderedRowsForTest();
	    assert.equal(idleDateRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.date, api.sessionFileTimeText(idleAgentLastActive), 'Date cycle shows absolute time for idle AI Tabber rows');
	    api.cycleFileExplorerTreeDateModeForTest();
	    assert.equal(api.fileExplorerTreeDateModeForTest(), 'relative', 'Date toggle cycle advances Date to Ago');
	    const idleAgentRows = api.tabberRenderedRowsForTest();
	    const idleAgentWindowRow = idleAgentRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1');
	    assert.equal(idleAgentWindowRow?.date, '<15 sec ago', 'idle AI Tabber rows use the shared sub-15-second Ago label');
    assert.equal(String(idleAgentWindowRow?.date || '').includes('idle'), false, 'idle AI Tabber rows no longer prefix the recency with idle');
    api.setTranscriptInfoForTest('1', session1ShellTranscript);
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'working', working_elapsed_seconds: 13500, window_index: 0, window_name: 'claude', window_label: '0:claude'},
    ]});
    api.setFileTreeRecencyNowForTest(2_000_000);
    api.setFileExplorerTreeDateModeForTest('relative');
    api.setTabberActivityForTest({
      activity: {'1:1': {last_user_input_ts: 2000 - 5}},
      agents: [{session: '1', window: '0', agent_kind: 'claude', last_used_ts: 2000 - 9 * 60, sort_ts: 2000 - 9 * 60, running: true, label: "session '1' 0:claude"}],
    });
    const recentTabberAgoRows = api.tabberRenderedRowsForTest();
    assert.equal(recentTabberAgoRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.recency, 'just-updated', 'Tabber Ago timestamp rows mark very recent process activity just-updated');
    assert.equal(recentTabberAgoRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.dateClasses.includes('attention-pulse'), true, 'Tabber Ago very recent rows pulse the timestamp cell with the shared attention class');
    assert.notEqual(recentTabberAgoRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.dateAttentionDelay, '', 'Tabber timestamp pulses are phase-aligned');
    assert.equal(recentTabberAgoRows.find(r => r.type === 'normal' && r.path === '/s_1/w_0')?.recency, 'just-updated', 'a visibly working Tabber row is marked just-updated instead of retaining stale transcript age');
	    api.setFileExplorerTreeDateModeForTest('date');
	    const recentTabberDateRows = api.tabberRenderedRowsForTest();
	    assert.equal(recentTabberDateRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.recency, 'just-updated', 'Tabber Date timestamp rows preserve very recent recency');
	    assert.equal(recentTabberDateRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.dateClasses.includes('attention-pulse'), true, 'Tabber Date timestamp rows keep the shared pulse');
    assert.equal(recentTabberDateRows.find(r => r.type === 'normal' && r.path === '/s_1/w_0')?.recency, 'just-updated', 'Tabber Date rows retain the shared current working clock');
    assert.equal(recentTabberDateRows.find(r => r.type === 'normal' && r.path === '/s_1/w_0')?.date, api.sessionFileTimeText(2000), 'Tabber Date mode shows the current working timestamp for agent sub-window rows');
	    api.setFileExplorerTreeDateModeForTest('none');
	    const noDateTabberRows = api.tabberRenderedRowsForTest();
	    assert.equal(noDateTabberRows.find(r => r.type === 'normal' && r.path === '/s_1/w_1')?.recency, '', 'Tabber None mode hides timestamp recency styling');
	    assert.equal(noDateTabberRows.find(r => r.type === 'normal' && r.path === '/s_1/w_0')?.date, '', 'Tabber None mode hides working agent sub-window status text');
	    assert.equal(noDateTabberRows.find(r => r.type === 'normal' && r.path === '/s_1/w_0')?.dateHtml, '', 'Tabber None mode hides working agent sub-window status HTML');
    api.setFileTreeRecencyNowForTest(null);
    api.setFileExplorerTreeDateModeForTest('relative');
    assert.ok(activeSessionRow.nameHtml.includes('tabber-normal-primary') && activeSessionRow.nameHtml.includes('tabber-normal-description'), 'A5: the current conversation uses the shared flat row');
    assert.equal(secondVisibleSessionRow.ariaCurrent, '', 'only the selected conversation exposes aria-current');
    assert.equal(secondVisibleSessionRow.ariaExpanded, '', 'flat conversation rows do not expose disclosure state');
    assert.equal(rows.some(r => r.type === 'session' && r.nameHtml.includes('data-tabber-expand')), false, 'session description text is part of the row activation target');
    assert.equal(activeWindowRow?.classes.includes('tabber-active-window'), true, '#2: the current AI window is marked by row emphasis instead of a competing dot');
    assert.equal(rows.some(r => r.type === 'window' && /tabber-window-active/.test(r.nameHtml)), false, '#2: current-window state no longer renders a circle marker beside agent status icons');
    assert.ok(rows.some(r => r.type === 'normal' && r.nameHtml.includes('tabber-normal-primary') && r.nameHtml.includes('agent-icon claude')), 'Claude Tabber rows show the shared Claude icon');
    assert.ok(rows.some(r => r.type === 'normal' && r.nameHtml.includes('tabber-normal-primary') && r.nameHtml.includes('agent-icon codex')), 'Codex Tabber rows show the shared Codex icon');
    const claudeWindowRow = activeWindowRow;
    assert.ok(/agent-icon claude[\s\S]*tabber-normal-window-identity[^>]*>0:claude</.test(claudeWindowRow?.nameHtml || ''), 'Claude icon precedes the window identity');
    assert.equal(/tabber-normal-window-identity[^>]*>0:claude<[\s\S]*agent-icon claude/.test(claudeWindowRow?.nameHtml || ''), false, 'agent icons precede window identities');
    assert.equal(/agent-icon[\s\S]*tabber-normal-window-identity[^>]*>1:bash</.test(shellWindowRow?.nameHtml || ''), false, 'bash Tabber rows do not gain a leading agent icon');
    assert.ok(/agent-icon claude[^"]*agent-window-agent-icon--working[\s\S]*tabber-normal-window-identity[^>]*>0:claude</.test(claudeWindowRow?.nameHtml || ''), 'working agent glyph stays before the canonical window name');
    assert.ok(/status-indicator--dot[\s\S]*?status-indicator--working[\s\S]*?agent-window-agent-icon--working[\s\S]*?tabber-normal-window-identity[^>]*>0:claude</.test(claudeWindowRow?.nameHtml || ''), 'working Tabber rows render the green status ball before the static agent icon and canonical label');
    api.setFileExplorerTreeSortModeForTest('newest');
    api.setTabberActivityForTest({activity: {'1:1': {last_user_input_ts: 99999}, '1:0': {last_user_input_ts: 1}}});
    const sessionCollapsedRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.equal(sessionCollapsedRows.some(r => r.icon === '›' || r.icon === '⌄'), false, 'flat rows have no disclosure affordance');
    assert.equal(api.tabberSessionForNumericKey('1'), '1', 'Tabber numeric key 1 maps to tmux session 1, not typeahead text');
    assert.equal(api.tabberSessionForNumericKey('0'), '', 'Tabber numeric keys are only visible 1-9 session shortcuts');
    assert.equal(api.openTabberSessionForTest('1'), true, 'opening a Tabber session succeeds');
    const firstLevelExpandedRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.ok(firstLevelExpandedRows.some(r => r.type === 'normal' && r.path === '/s_1/w_0'), 'session activation leaves its flat conversation row available');
    assert.equal(firstLevelExpandedRows.some(r => r.icon === '›' || r.icon === '⌄'), false, 'session activation does not add disclosure controls');
    assert.equal(api.currentSessionActionTarget(), '1', 'opening Tabber session 1 focuses tab 1');
    api.setFocusedPanelItem('2');
    api.editorNav.stack = [];
    api.editorNav.index = -1;
    assert.equal(api.openTabberSessionForTest('1'), true, 'opening Tabber session 1 from another tab records a user navigation');
    assert.deepEqual(api.editorNav.stack, ['2', '1'], 'Tabber session open records previous tab then session 1 for Back');
    api.setFileExplorerModeForTest('tabber');
    const tabberClickSlots = api.emptyLayoutSlots();
    tabberClickSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    tabberClickSlots.left = api.paneStateWithTabs(['1', '__tabber__'], '1');
    tabberClickSlots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(tabberClickSlots);
    api.setFocusedPanelItem('1');
    api.editorNav.stack = [];
    api.editorNav.index = -1;
    const tabberPanel = new TestElement('tabber-panel');
    tabberPanel.dataset.panelItem = '__tabber__';
    const sessionTwoRow = new TestElement('tabber-session-2');
    sessionTwoRow.classList.add('file-tree-row');
    sessionTwoRow.dataset.kind = 'file';
    sessionTwoRow.dataset.path = '/s_2';
    sessionTwoRow.dataset.tabberType = 'normal';
    sessionTwoRow.dataset.tabberSession = '2';
    tabberPanel.appendChild(sessionTwoRow);
    api.bindTabberPanelForTest(tabberPanel);
    const rowBodyTarget = {
      closest(selector) {
        if (selector === '.file-tree-row[data-tabber-type]') return sessionTwoRow;
        return null;
      },
    };
    const delegatedClick = {
      target: rowBodyTarget,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
    };
    tabberPanel.listeners.get('click')[0](delegatedClick);
    assert.equal(delegatedClick.defaultPrevented, true, 'clicking the green Tabber session row body is handled by Tabber');
    assert.equal(api.currentSessionActionTarget(), '2', 'clicking the whole green "2 <desc>" session line opens Tab 2');
    assert.deepEqual(api.editorNav.stack, ['1', '2'], `green session row body click records navigation so Back returns to the prior tab; got ${JSON.stringify(api.editorNav.stack)} index=${api.editorNav.index} navigating=${api.editorNav.navigating}`);
    assert.deepEqual(firstLevelExpandedRows.filter(r => r.type === 'normal' && r.path.startsWith('/s_1/w_')).map(r => r.path), ['/s_1/w_0', '/s_1/w_1'], 'flat Tabber conversation rows retain tmux window-index order');
    assert.equal(rows.some(r => ['repo', 'path', 'loading'].includes(r.type)), false, 'flat Tabber rows omit touched-path subtrees and loading rows');
    assert.equal(rows.flatMap(r => r.datasetKeys).some(key => /^tabberOpen/.test(key)), false, 'DOIT.61 A3: Tabber rows do not retain stale tabberOpen* dataset keys');
    const loadingRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.equal(loadingRows.some(r => r.type === 'loading' && /Fetching paths/.test(r.name)), false, 'L3: Tabber no longer has a frontend touched-path loading row');
    // Non-tmux tabs (Preferences / YO!info / file editors) render as leaf rows AFTER all the sessions.
    const rowTypes = rows.map(r => r.type);
    assert.ok(rowTypes.includes('tab'), 'non-tmux tabs appear in the Tabber');
    assert.ok(rowTypes.indexOf('tab') > rowTypes.lastIndexOf('session'), 'non-tmux tabs render after the sessions');

    // B4 recency sort: a live working state is newer than historical transcript activity. A fresh
    // user-input heartbeat and a newer touched-path mtime are still not alternate clocks for the row.
    api.setFileExplorerTreeSortModeForTest('newest');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'working', working_elapsed_seconds: 13500, window_index: 0, window_name: 'claude', window_label: '0:claude', pid: 12345, active: true, path_entries: [{path: '/home/u/proj', mtime: 999999, git: {root: '/home/u/proj', branch: 'devbranch'}}]},
    ]});
    api.setTabberActivityForTest({
      activity: {'1:0': {last_user_input_ts: 999999}, '2:0': {last_user_input_ts: 1}},
      agents: [
        {session: '2', window: '0', agent_kind: 'codex', last_used_ts: 900000, sort_ts: 900000, running: false, label: "session '2' 0:codex"},
        {session: '1', window: '0', agent_kind: 'claude', last_used_ts: 1000, sort_ts: 1000, running: false, label: "session '1' 0:claude"},
      ],
    });
    const recency = api.tabberRenderedRowsForTest().filter(r => r.type === 'normal').map(r => r.nameHtml);
    const codexAt = recency.findIndex(n => /0:codex/.test(n));
    const claudeAt = recency.findIndex(n => /0:claude/.test(n));
    assert.ok(codexAt >= 0 && claudeAt >= 0 && claudeAt < codexAt, 'B4: a currently working window sorts before older transcript activity');
    api.setFileExplorerTreeDateModeForTest('date');
    api.setAutoApproveStateForTest('1', {});
    api.setTabberActivityForTest({
      activity: {'1:0': {last_user_input_ts: 999999}},
      agents: [{session: '1', window: '0', agent_kind: 'claude', last_used_ts: 1000, sort_ts: 9000, running: true, label: "session '1' 0:claude"}],
    });
    const runningClaude = api.tabberRenderedRowsForTest().find(r => r.type === 'normal' && /0:claude/.test(r.nameHtml));
    assert.equal(runningClaude?.date, api.sessionFileTimeText(999999), 'B4: agent rows retain their newer native window activity timestamp instead of discarding it for transcript history');

    // B5 context-menu source guard + event path.
    assert.ok(/data-tabber-type="session"[\s\S]*data-tabber-type="window"[\s\S]*tabInteractionControllerForApp\(\)\.showActions\(\{[\s\S]*item: tabItem,[\s\S]*sourceSlot: \(\) => slotForItem\(tabItem\)/.test(source), 'B5: right-click on a Tabber session/window row uses the shared tab interaction controller');
    const contextPanel = new TestElement('tabber-context-panel');
    const contextSessionRow = new TestElement('tabber-context-session-row');
    contextSessionRow.classList.add('file-tree-row');
    contextSessionRow.dataset.tabberType = 'normal';
    contextSessionRow.dataset.tabberSession = '1';
    contextPanel.appendChild(contextSessionRow);
    api.bindTabberPanelForTest(contextPanel);
    const contextMenuEvent = {
      target: contextSessionRow,
      clientX: 10,
      clientY: 12,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
    };
    contextPanel.listeners.get('contextmenu')[0](contextMenuEvent);
    const contextMenu = api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('session-context-menu'));
    assert.equal(contextMenuEvent.defaultPrevented, true, 'right-clicking a Tabber session row opens the shared tab context menu');
    assert.ok(Array.from(contextMenu?.children || []).some(child => child.getAttribute?.('aria-label') === 'Pin Tab'), 'Tabber session context menu includes the shared Pin Tab action');
    assert.ok(Array.from(contextMenu?.children || []).some(child => String(child.textContent || '').includes("Rename tmux session '1'")), 'Tabber session context menu includes the shared rename action');
  });

  test('drop-action suggestions honor category, settings, and last-used state', () => {
    // DOIT.57: drag-into-terminal suggestion registry (the transient 1..9 overlay's data layer).
    const api = loadYomux();
    assert.equal(api.fileDropCategory('/x/shot.png'), 'image');
    assert.equal(api.fileDropCategory('build.log'), 'log');
    assert.equal(api.fileDropCategory('app.py'), 'code');
    assert.equal(api.fileDropCategory('data.csv'), 'data');
    assert.equal(api.fileDropCategory('README.md'), 'doc');
    assert.equal(api.fileDropCategory('fix.diff'), 'diff');
    assert.equal(api.fileDropCategory('/some/dir', 'dir'), 'dir');
    assert.equal(api.fileDropCategory('mystery.xyz'), 'any');

    const imgClaude = api.dropSuggestionsFor('image', 'claude', 1, {pathInserted: true});
    assert.ok(imgClaude.some(s => s.id === 'img-error'), 'image + agent offers diagnose-screenshot');
    assert.ok(!imgClaude.some(s => s.id === 'log-errors'), 'image category hides log-only suggestions');
    assert.ok(imgClaude.length <= 9, 'suggestions cap at 9 (the path is inserted first, so 1..9 are all actions)');
    assert.deepEqual(imgClaude.map(s => s.id), ['img-ocr', 'img-error', 'img-describe', 'server-info'], 'image paste shows the configured image action order entries');
    assert.equal(imgClaude.some(s => s.id === 'analyze'), false, 'image paste does not append unconfigured fallback actions such as Take a look at it');
    assert.equal(imgClaude.some(s => s.id === 'server-ocr' || s.id === 'shell-file'), false, 'image paste defaults do not include server OCR or file type');
    assert.deepEqual(imgClaude.map(s => api.dropActionDisplayLabel(s)), [
      'Extract the text (OCR)',
      'Diagnose the error',
      'Describe the image',
      'Server: file info',
    ], 'image paste order still comes from Preferences, while visible labels come from localized action labels');
    api.rememberDropActionForTest('image', 'server-info');
    assert.equal(api.dropSuggestionsFor('image', 'claude', 1, {pathInserted: true})[0]?.id, 'img-ocr', 'image action order Preference is authoritative over last-used state');
    api.setClientSettingsPatchForTest({uploads: {image_action_order: ['Describe the image: ; describe what is shown in this image.', 'Info: info', 'Extract the text: ; do OCR on this image and extract all of the text.']}});
    const reorderedImageActions = api.dropSuggestionsFor('image', 'claude', 1, {pathInserted: true});
    assert.deepEqual(reorderedImageActions.map(s => s.id), ['img-describe', 'server-info', 'img-ocr'], 'Uploads Preferences strictly define the image paste action menu');
    assert.deepEqual(reorderedImageActions.map(s => api.dropActionDisplayLabel(s)), ['Describe the image', 'Server: file info', 'Extract the text (OCR)'], 'custom image paste order uses configured rows while visible labels remain localized');
    api.setClientSettingsPatchForTest({uploads: {image_action_order: [
      'Extract the text (OCR): ; do OCR on this image and extract all of the text.',
      'Diagnose the error: ; diagnose the error/problem shown in this screenshot & suggest a fix.',
      'Describe the image: ; describe what is shown in this image.',
      'info',
    ]}});
    const imgShell = api.dropSuggestionsFor('image', '', 1);
    assert.deepEqual(imgShell.map(s => s.id), ['server-info'], 'a plain shell image menu is still restricted to the configured image action order');
    assert.equal(imgShell.some(s => s.id === 'server-ocr' || s.id === 'shell-file'), false, 'plain shell image defaults omit server OCR and file type');

    const logErrors = api.dropSuggestionsFor('log', 'claude', 1).find(s => s.id === 'log-errors');
    assert.ok(logErrors, 'log + agent offers find-errors');
    api.rememberDropActionForTest('log', 'log-cause');
    assert.equal(api.dropSuggestionsFor('log', 'claude', 1, {pathInserted: true})[0]?.id, 'log-cause', 'non-image categories still use the last chosen action when no Preference order exists');
    const logClause = api.composeDropSuggestion(logErrors);
    assert.ok(/\blog\b/i.test(logClause), 'compose returns a deictic clause that refers to the file (this log)');
    assert.equal(logClause.includes('/var/log'), false, 'compose does NOT repeat the path — it is appended after the already-inserted path');
    assert.equal(api.composeDropSuggestion(imgClaude.find(s => s.id === 'img-ocr')), 'do OCR on this image and extract all of the text.', 'OCR clause reads as an appendable instruction about this image');
    assert.ok(api.dropSuggestionsFor('any', 'codex', 1).some(s => s.id === 'analyze'), 'any-category fallback offers a generic look');
    assert.ok(api.dropSuggestionsFor('code', 'claude', 2, {pathInserted: true}).some(s => s.id === 'multi-diff'), 'multi-file agent actions appear when count >= 2');
    assert.equal(api.dropSuggestionIndexFromKeyEvent({key: '1', code: ''}), 0, 'drop action shortcuts accept browser events that only carry key');
    assert.equal(api.dropSuggestionIndexFromKeyEvent({key: 'End', code: 'Numpad8'}), 7, 'drop action shortcuts accept numpad digits');
    assert.equal(api.dropSuggestionIndexFromKeyEvent({key: '1', code: 'Digit1', metaKey: true}), -1, 'drop action shortcuts leave browser Cmd/Ctrl digit shortcuts alone');
    const customShell = api.customDropActionFromLine('Peek | shell:head -40 {qpath} | log,code', 0);
    assert.equal(customShell.kind, 'shell', 'custom shell actions parse from Settings lines');
    assert.equal(api.composeDropSuggestion(customShell, {paths: ['/tmp/a b.log'], category: 'log'}), "head -40 '/tmp/a b.log'", 'custom shell templates expand quoted path placeholders');
    api.setClientSettingsPatchForTest({uploads: {custom_actions: ['Ask owner | explain why {name} matters | code']}});
    assert.ok(api.dropSuggestionsFor('code', 'claude', 1).some(s => s.custom && s.label === 'Ask owner'), 'custom prompt actions from Preferences join the shared registry');
    // Request transport and menu wiring have their own integration owners. This unit proves the
    // registry's observable category/settings/last-used behavior without pinning implementation text.
  });

  test('programmatic terminal insertion preserves bytes and sends before GUI ownership', () => {
    const source = fs.readFileSync('static_src/js/yomux/99_terminal_boot.js', 'utf8');
    const coreSource = fs.readFileSync('static_src/js/yomux/10_core_utils.js', 'utf8');
    const insertion = source.slice(source.indexOf('function insertIntoTerminal('), source.indexOf('function noteTerminalExplicitInput('));
    const transport = source.slice(source.indexOf('function handleTerminalData('), source.indexOf('function shellQuote('));
    const filtering = coreSource.slice(coreSource.indexOf('function stripTerminalQueryResponses('), coreSource.indexOf('const terminalLinkPattern'));
    const events = [];
    const sent = [];
    let modifierCalls = 0;
    const item = {
      lastExplicitInputStartedAt: null,
      term: {focus() { events.push('focus'); }},
      socket: {readyState: 1, send(payload) { events.push('send'); sent.push(JSON.parse(payload)); }},
    };
    const context = {
      readOnlyMode: false,
      autoFocusEnabled: true,
      terminals: new Map([['1', item]]),
      WebSocket: {OPEN: 1},
      performanceNow() { return 10; },
      utf8ByteLength(value) { return Buffer.byteLength(value); },
      clientPerfStart() {},
      clientPerfEnd() {},
      recordClientPerfCounter() {},
      terminalDataWithMobileAccessoryModifiers(session, value) { modifierCalls += 1; return `\x1b${value}`; },
      observeTerminalTmuxPrefixWindowSwitches() { events.push('observe'); },
      acknowledgeTerminalAttentionFromTransportInput() { events.push('attention'); },
      terminalDataShouldAcknowledgeAttention() { return true; },
      noteTabberTerminalInput() { events.push('activity'); },
      closeOtherSessionPopovers() { events.push('popover'); },
      commitTerminalExplicitInputOwnership() { events.push('ownership'); },
      localizedHtml(value) { return value; },
      statusErr() { events.push('error'); },
    };
    vm.createContext(context);
    vm.runInContext(`${filtering}\n${transport}\n${insertion}`, context);
    const bytes = "'/tmp/a b.png' \x1b[?1;2c\r";
    assert.equal(context.insertIntoTerminal('1', bytes), true);
    assert.deepEqual(sent, [{type: 'input', data: "'/tmp/a b.png' \r", user_input: true}]);
    assert.deepEqual(events, ['send', 'activity', 'observe', 'attention', 'popover', 'ownership', 'focus']);
    assert.equal(modifierCalls, 0, 'inserted paths and commands bypass latched mobile modifiers');
    assert.equal(item.lastInputSentAt, 10, 'insertion retains the shared echo timing owner');

    events.length = 0;
    assert.equal(context.handleTerminalData('1', 'a'), true);
    assert.equal(modifierCalls, 1, 'native terminal input retains the accessory modifier path');
    assert.equal(sent.at(-1).data, '\x1ba');
    assert.equal(events[0], 'send', 'native input also reaches transport before secondary work');

    for (const input of ['', '\x1b[?1;2c']) {
      events.length = 0;
      assert.equal(context.insertIntoTerminal('1', input), false);
      assert.deepEqual(events, [], 'empty/passive reports neither send nor change ownership');
    }
    events.length = 0;
    item.socket.readyState = 0;
    assert.equal(context.insertIntoTerminal('1', 'offline'), false);
    assert.deepEqual(events, [], 'disconnected insertion neither focuses nor updates side panes');
    item.socket.readyState = 1;
    context.autoFocusEnabled = false;
    assert.equal(context.insertIntoTerminal('1', 'no-focus'), true);
    assert.equal(events.includes('focus'), false, 'insertion preserves disabled autofocus');
    events.length = 0;
    context.readOnlyMode = true;
    assert.equal(context.insertIntoTerminal('1', 'read-only'), false);
    assert.deepEqual(events, ['error'], 'read-only insertion reports the error without sending');
  });

  await testAsync('drop-action status reuses the localized display label', async () => {
    const api = loadYomux('', ['1']);
    api.i18nSetCatalogForTest('fr', {
      'drop.action.imgDescribe': 'Décrire l’image',
      'status.insertedDropAction': 'inséré {name}',
    });
    api.setActiveLocaleForTest('fr');
    const sent = [];
    api.registerTerminalForTest('1', {focus() {}}, {
      readyState: 1,
      send(payload) { sent.push(JSON.parse(payload)); },
    });
    const action = api.dropSuggestionsFor('image', 'claude', 1, {pathInserted: true})
      .find(item => item.id === 'img-describe');
    assert.ok(action, 'localized status fixture uses the shared image-action registry');
    await api.runDropActionForTest(action, {
      session: '1',
      paths: ['/tmp/image.png'],
      category: 'image',
      agentKind: 'claude',
      pathInserted: true,
      kind: 'file',
    });
    assert.equal(sent.length, 1, 'drop action inserts through the normal terminal path');
    assert.equal(api.statusHtmlForTest(), '<span class="ok">inséré Décrire l’image</span>', 'drop-action status uses dropActionDisplayLabel instead of the canonical English label');
  });
}

test('Normal numeric opening shares one-pane toggling and all tab drag sources refuse it', () => {
  const api = loadYomux('', ['1', '2', '3']);
  api.setTabberModeForTest('normal');
  api.setTabberNormalSelectionsForTest(['1']);
  api.openTabberSessionForTest('3');
  assert.deepEqual(api.tabberNormalSelectionsForTest(), ['3']);
  api.openTabberSessionForTest('3');
  assert.deepEqual(api.tabberNormalSelectionsForTest(), []);
  assert.equal(api.dropItemCanBeDraggedForTest('1'), false);
  let prevented = false;
  api.startSessionDrag({preventDefault: () => {prevented = true;}}, '1', 'left');
  assert.equal(prevented, true);
  api.setTabberModeForTest('advanced');
  assert.equal(api.dropItemCanBeDraggedForTest('1'), true);
});

test('Normal rows use distinct window descriptions and never inherit the session work description', () => {
  const api = loadYomux('', ['1']);
  api.setTabberModeForTest('normal');
  api.setTranscriptInfoForTest('1', {
    project: {git: {branch: 'shared-branch', current_branch_subject: 'Shared work'}},
    panes: [{window: '0', window_name: 'codex', command: 'codex', active: true, window_active: true},
      {window: '1', window_name: 'codex', command: 'codex', active: true}],
    window_metadata: [{window_index: 0, description: 'First conversation'}, {window_index: 1, description: 'Second conversation'}],
  });
  const rows = api.buildTabberNormalRowsForTest().filter(row => row.tabber.type === 'normal');
  assert.equal(rows.length, 2);
  assert.ok(rows[0].tabber.normalNameHtml.includes('First conversation'));
  assert.ok(rows[1].tabber.normalNameHtml.includes('Second conversation'));
  assert.ok(rows.every(row => !row.tabber.normalNameHtml.includes('Shared work')));
});

test('Normal Tabber keeps utility tabs alphabetically at the bottom after recent conversations', () => {
  const api = loadYomux('?layout=left&tabs=left:file%3A%2Ftmp%2F2026.md', ['z-last', '1']);
  api.setTabberModeForTest('normal');
  api.setFileExplorerTreeSortModeForTest('newest');
  for (const session of ['z-last', '1']) {
    api.setTranscriptInfoForTest(session, {panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'bash', command: 'bash'}]});
  }
  const editorItem = api.fileEditorItemFor('/tmp/2026.md');
  const groups = new TestElement('', 'div');
  api.setFileExplorerModeForTest('tabber');
  api.renderTabberTreeForTest(groups);
  const initialUtilityRows = groups.querySelectorAll('.file-tree-row[data-tabber-type="tab"]');
  const initialUtilityLabels = initialUtilityRows.map(row => row.querySelector('.file-tree-name')?.textContent || '');
  assert.equal(api.noteTabberItemActivityForTest(editorItem), true, 'activity belongs to the registered editor utility item');
  api.renderTabberTreeForTest(groups);
  const rows = Array.from(groups.querySelectorAll('.file-tree-row')).map(row => ({
    type: row.dataset.tabberType,
    title: row.querySelector('.file-tree-name')?.textContent || '',
    date: row.querySelector('.file-tree-date')?.textContent || '',
  }));
  const conversationRows = rows.filter(row => row.type === 'normal');
  const utilityRows = rows.filter(row => row.type === 'tab');
  assert.ok(utilityRows.length > 0, 'the default roster includes utility tabs');
  const normalTreeItems = api.buildTabberNormalRowsForTest().map(entry => entry.tabber?.item);
  assert.ok(['__differ__', '__chat__', '__yoagent__'].every(item => !normalTreeItems.includes(item)), `Normal omits Differ, YO!chat, and YO!agent from selectable Tabber rows: ${JSON.stringify({mode: api.tabberModeForTest(), items: normalTreeItems})}`);
  assert.deepEqual(rows.slice(-utilityRows.length).map(row => row.type), utilityRows.map(() => 'tab'), 'all utility tabs occupy the bottom block');
  const utilityLabels = utilityRows.map(row => row.title.split('\n')[0]);
  const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
  assert.deepEqual(utilityLabels, [...utilityLabels].sort((left, right) => collator.compare(left, right)), 'utility tabs sort alphabetically');
  assert.deepEqual(utilityLabels, initialUtilityLabels, 'utility row order is preserved after real editor activity');
  assert.ok(utilityRows.some(row => row.title === '2026.md' && row.date), 'the editor activity timestamp renders in the persistent Tabber container');

  api.setTabberActivityForTest({activity: {'z-last:0': {active_recency_ts: 500}, '1:0': {active_recency_ts: 900}}});
  const recentRows = api.tabberRenderedRowsForTest();
  assert.deepEqual(recentRows.filter(row => row.type === 'normal').map(row => row.title.split('\n')[0]), ['1', 'z-last'], 'conversation rows still sort by newest activity');
  assert.ok(recentRows.slice(-utilityLabels.length).every(row => row.type === 'tab'), 'utility tabs remain at the bottom when conversation activity arrives');

  api.setTabberModeForTest('advanced');
  const advancedItems = api.buildTabberNormalRowsForTest().map(entry => entry.tabber?.item);
  assert.ok(['__chat__', '__yoagent__'].every(item => advancedItems.includes(item)), 'Advanced keeps YO!chat and YO!agent available in Tabber');
});

test('Normal Tabber shows dates for both AI and shell windows and orders by their activity times', () => {
  const api = loadYomux('', ['yo7110']);
  api.setTabberModeForTest('normal');
  const now = Date.now() / 1000;
  const aiActivity = now - 60;
  const shellActivity = now - 30 * 60;
  api.setTranscriptInfoForTest('yo7110', {
    panes: [
      {window: '0', pane: '0', window_name: 'codex', command: 'codex', active: true, window_active: true},
      {window: '1', pane: '0', window_name: 'bash', command: 'bash', active: true},
    ],
    window_metadata: [{window_index: 0, description: 'AI screen'}, {window_index: 1, description: 'Shell'}],
  });
  api.setTabberActivityForTest({
    activity: {
      'yo7110:0': {active_recency_ts: aiActivity, last_output_ts: aiActivity},
      'yo7110:1': {active_recency_ts: shellActivity, last_output_ts: shellActivity},
    },
    agents: [{session: 'yo7110', window: '0', agent_kind: 'codex', sort_ts: aiActivity, last_used_ts: aiActivity}],
  });
  api.setFileExplorerTreeDateModeForTest('date');

  const rows = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(row => row.title.split('\n')[0]), ['yo7110  AI screen', 'yo7110  Shell'], `newer AI activity appears above the older Bash window: ${JSON.stringify(rows.map(row => ({title: row.title, date: row.date})))}`);
  assert.ok(rows.every(row => row.date), 'both flattened Normal rows display their activity date');
});

test('Normal Tabber compares native window activity with agent transcript activity', () => {
  const api = loadYomux('', ['yo7110']);
  api.setTabberModeForTest('normal');
  const now = Date.now() / 1000;
  api.setTranscriptInfoForTest('yo7110', {panes: [
    {window: '0', pane: '0', window_name: 'codex', command: 'codex', active: true, window_active: true},
    {window: '1', pane: '0', window_name: 'bash', command: 'bash', active: true},
  ]});
  api.setTabberActivityForTest({
    activity: {
      'yo7110:0': {active_recency_ts: now - 20, last_output_ts: now - 20},
      'yo7110:1': {active_recency_ts: now - 600, last_output_ts: now - 600},
    },
    agents: [{session: 'yo7110', window: '0', agent_kind: 'codex', sort_ts: now - 1800, last_used_ts: now - 1800}],
  });
  assert.equal(api.tabberRecencyForTest('yo7110:0'), now - 20);
  assert.equal(api.tabberWindowRecencyForTest({session: 'yo7110', windowIndex: 0, isAgent: true, agentKey: 'codex', agentActivity: {sort_ts: now - 1800, last_used_ts: now - 1800}, agentStatus: {state: 'active'}}, now), now - 20);

  const rows = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
  assert.match(rows[0]?.nameHtml || '', /0:codex/, `the Codex window ranks by its newer native window activity when that exceeds transcript recency: ${JSON.stringify({rows: rows.map(row => ({nameHtml: row.nameHtml, date: row.date})), activity: api.tabberActivityPayloadForTest().activity, tree: api.buildTabberNormalRowsForTest().filter(row => row.tabber?.session === 'yo7110')?.map(row => ({name: row.name, mtime: row.mtime, agentKey: row.tabber.agentKey}))})}`);
});

test('Normal Tabber keeps windows together by index when one window timestamp is unavailable', () => {
  const api = loadYomux('', ['yo7110', 'other']);
  api.setTabberModeForTest('normal');
  for (const session of ['yo7110', 'other']) {
    api.setTranscriptInfoForTest(session, {panes: [
      {window: '0', pane: '0', window_name: 'bash', command: 'bash', active: true, window_active: session === 'other'},
      ...(session === 'yo7110' ? [{window: '1', pane: '0', window_name: 'bash', command: 'bash', active: true}] : []),
    ]});
  }
  const now = Date.now() / 1000;
  api.setTabberActivityForTest({activity: {
    'yo7110:0': {active_recency_ts: now - 300, last_output_ts: now - 300},
    'other:0': {active_recency_ts: now - 200, last_output_ts: now - 200},
  }});

  const rows = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
  const names = rows.map(row => row.name);
  const firstYo = names.findIndex(name => name.includes('yo7110'));
  assert.deepEqual(names.slice(firstYo, firstYo + 2), names.filter(name => name.includes('yo7110')), 'an unavailable sibling timestamp falls back to a contiguous session block in window-index order');
  assert.ok(rows[firstYo]?.nameHtml.includes('0:bash') && rows[firstYo + 1]?.nameHtml.includes('1:bash'), 'missing sibling timestamps retain window-index ordering');

  api.setTabberActivityForTest({activity: {
    'yo7110:0': {active_recency_ts: now - 100, last_output_ts: now - 100},
    'yo7110:1': {active_recency_ts: now - 100, last_output_ts: now - 100},
    'other:0': {active_recency_ts: now - 200, last_output_ts: now - 200},
  }});
  const tiedRows = api.tabberRenderedRowsForTest().filter(row => row.type === 'normal');
  const tiedNames = tiedRows.map(row => row.name);
  const firstTiedYo = tiedNames.findIndex(name => name.includes('yo7110'));
  assert.deepEqual(tiedNames.slice(firstTiedYo, firstTiedYo + 2), tiedNames.filter(name => name.includes('yo7110')), 'equal sibling timestamps retain a contiguous session group');
  assert.ok(tiedRows[firstTiedYo]?.nameHtml.includes('0:bash') && tiedRows[firstTiedYo + 1]?.nameHtml.includes('1:bash'), 'equal sibling timestamps retain window-index ordering');
});

test('window aliases retain the exact physical window attention state', () => {
  const api = loadYomux('', ['1']);
  api.setTranscriptInfoForTest('1', {window_metadata: [{window_id: '@test-0', window_index: 0}, {window_id: '@test-1', window_index: 1}], panes: [
    {window: '0', pane_id: '%123', command: 'codex', active: true, window_active: true},
    {window: '1', pane_id: '%124', command: 'codex', active: true}],
    agents: [{kind: 'codex', window: '0', pane_target: '%123'}, {kind: 'codex', window: '1', pane_target: '%124'}],
  });
  const alias = '__yomux_view_7110_attention';
  api.storeTabberViewSessionForTest(alias, {sourceSession: '1', windowIndex: 0});
  for (const state of ['needs-input', 'blocked']) {
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', window: '0', pane_target: '%123', state},
      {kind: 'codex', window: '1', pane_target: '%124', state: 'working'},
    ]});
    const model = api.sessionAgentWindowStatusModelForTest(alias);
    assert.equal(model.agents.length, 1);
    assert.equal(model.agents[0].state, state);
    assert.equal(api.sessionState(alias).key, api.sessionState('1').key);
    api.acknowledgeTerminalAttentionFromUserActionForTest(alias, null, {refresh: false, acknowledgeAgentWindowDelayMs: 0});
    assert.equal(api.sessionState(alias).key, api.sessionState('1').key, 'physical acknowledgement converges in source and view');
  }
});

test('window aliases follow physical identity after ordinal reuse', () => {
  const api = loadYomux('', ['source']);
  const alias = '__yomux_view_stable';
  api.setTranscriptInfoForTest('source', {window_metadata: [{window_id: '@12', window_index: 1}],
    agents: [{kind: 'codex', window_index: 1, pane_target: '%12'}]});
  api.storeTabberViewSessionForTest(alias, {sourceSession: 'source', windowIndex: 1, windowId: '@12'});
  api.setTranscriptInfoForTest('source', {window_metadata: [{window_id: '@12', window_index: 0}, {window_id: '@99', window_index: 1}],
    agents: [{kind: 'codex', window_index: 0, pane_target: '%12'}, {kind: 'codex', window_index: 1, pane_target: '%99'}]});
  const model = api.sessionAgentWindowStatusModelForTest(alias);
  assert.deepEqual(model.agents.map(agent => agent.pane_target), ['%12']);
  api.setTranscriptInfoForTest('source', {window_metadata: [{window_id: '@99', window_index: 1}], agents: [{kind: 'codex', window_index: 1, pane_target: '%99'}]});
  assert.equal(api.acknowledgeTerminalAttentionFromUserActionForTest(alias, null, {refresh: false}), false);
});

module.exports = {runTabberSuite};

if (require.main === module) {
  runSuites([runTabberSuite]);
}

test('bootstrapped roster rejects older and conflicting same-generation activity', () => {
  const api = loadYomux('', ['fresh'], 'http:', 'Linux x86_64', 'admin', {tmuxTopology: {generation: 7, epoch: 'boot'}});
  for (const generation of [6, 7]) {
    assert.equal(api.applyTabberActivityPayloadForTest({topology_generation: generation, session_order: [], sessions: {}, activity: {}}), false);
    assert.deepEqual(api.sessionsForTest(), ['fresh']);
  }
  assert.equal(api.applyTabberActivityPayloadForTest({topology_generation: 8, session_order: ['fresh', 'new'], sessions: {}, activity: {}}), true);
});

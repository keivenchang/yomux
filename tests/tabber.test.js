const {
  assert,
  fs,
  UI_PINS,
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
  loadYolomux,
  fileExplorerClosedOptions,
  loadYolomuxWithFileExplorerClosed,
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
} = require('./layout_test_helper');

async function runTabberSuite() {
  test('shared tree controller is the Finder, Tabber, and Differ interaction parent', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    const api = loadYolomux('', ['1']);
    assert.deepStrictEqual(canonical(api.sharedTreeControllerNamesForTest()), ['differ', 'finder', 'tabber'], 'Finder, Tabber, and Differ register through the shared tree interaction controller');
    assert.ok(source.includes('function createSharedTreeInteractionController('), 'shared tree controller exists');
    assert.ok(source.includes('function sharedTreeSelectionApi('), 'shared tree selection behavior has a named owner');
    assert.ok(source.includes('function sharedTreeExpansionApi('), 'shared tree expansion behavior has a named owner');
    assert.ok(source.includes('function sharedTreeClickHandler('), 'shared tree click routing has a named owner');
    assert.ok(source.includes('function sharedTreeKeyboardHandler('), 'shared tree keyboard routing has a named owner');
    assert.ok(/const finderTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*name: 'finder'/.test(source), 'Finder uses the shared tree interaction controller');
    assert.ok(/const tabberTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*name: 'tabber'/.test(source), 'Tabber uses the shared tree interaction controller');
    assert.ok(/const differTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*name: 'differ'/.test(source), 'Differ uses the shared tree interaction controller');
    assert.ok(/handleFileExplorerArrowNav = event =>[\s\S]*fileExplorerMode === 'tabber'[\s\S]*tabberTreeInteractionController\.handleKeydown[\s\S]*fileExplorerMode === 'diff'[\s\S]*differTreeInteractionController\.handleKeydown[\s\S]*originalFileExplorerArrowNavForSharedTree/.test(source), 'global key dispatch routes Tabber/Differ through the shared parent before Finder fallback');
    assert.ok(/selectableFileTreeRows\(container = document\)[\s\S]*!row\.dataset\.tabberType/.test(source), 'Finder/Differ legacy row discovery does not steal Tabber rows');
    assert.ok(source.includes('row.classList.toggle(CLS.selected, selected)') && source.includes("row.classList.toggle('current-file', current && row.dataset.kind !== 'dir')") && source.includes("row.classList.toggle('current-directory', current && row.dataset.kind === 'dir')"), 'Tabber row render uses shared selected/current classes');
    assert.equal(/classList\??\.\s*(?:add|remove|toggle|contains)\s*\(\s*['"](active|open|selected|collapsed)['"]/.test(source), false, 'MV-1: active/open/selected/collapsed classList calls route through CLS');
    assert.ok(/\.file-tree-row:not\(\.selected\):hover/.test(css), 'tree hover color is the shared row token path');
    assert.ok(/\.file-tree-row\.selected,\s*\.file-tree-row\.current-file:not\(\.selected\)\s*\{/.test(css), 'tree selected/current-file paint has one grouped owner');
    assert.ok(/\.file-tree-row\.current-file:not\(\.selected\)/.test(css), 'tree current-file color is the shared row token path');
    assert.ok(/\.file-tree-row\.current-directory:not\(\.selected\)/.test(css), 'tree current-directory color is the shared row token path');
    assert.equal(/differ-[^{]*(?:selected|current-file|current-directory)/.test(css), false, 'Differ has no forked selected/current row color classes');
  });

  test('Finder shared tree controller handles keyboard cursor navigation', () => {
    const api = loadYolomux('', ['1']);
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

  test('Tabber shared tree controller handles keyboard navigation and active-window sync', () => {
    const api = loadYolomux('', ['1', '2']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1'], '1');
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
    const sessionOne = new TestElement('tabber-session-1');
    sessionOne.classList.add('file-tree-row');
    sessionOne.dataset.path = '/s_1';
    sessionOne.dataset.kind = 'dir';
    sessionOne.dataset.tabberType = 'session';
    sessionOne.dataset.tabberSession = '1';
    sessionOne.setAttribute('aria-expanded', 'true');
    const windowOne = new TestElement('tabber-window-1');
    windowOne.classList.add('file-tree-row');
    windowOne.dataset.path = '/s_1/w_1';
    windowOne.dataset.kind = 'file';
    windowOne.dataset.tabberType = 'window';
    windowOne.dataset.tabberSession = '1';
    windowOne.dataset.tabberWindow = '1';
    const sessionTwo = new TestElement('tabber-session-2');
    sessionTwo.classList.add('file-tree-row');
    sessionTwo.dataset.path = '/s_2';
    sessionTwo.dataset.kind = 'dir';
    sessionTwo.dataset.tabberType = 'session';
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
    api.selectPanelOnHover(api.fileExplorerItemId);
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
    api.setFocusedPanelItem(api.fileExplorerItemId);
    api.syncTabberTreeActiveSelectionForTest(panel);
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), [], 'Tabber clears a stale selection when the finder pane is focused');
  });

  test('Tabber highlights every visible tab, not only the focused one', () => {
    const api = loadYolomux('', ['1', '2', '3']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    slots.left = api.paneStateWithTabs(['1'], '1');
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
      const sessionRow = rows.find(row => row.type === 'session' && row.path === `/s_${session}`);
      const windowRow = rows.find(row => row.type === 'window' && row.path === `/s_${session}/w_0`);
      assert.equal(sessionRow?.classes.includes('tabber-active-session'), true, `visible session ${session} keeps the shared active-tab highlight`);
      assert.ok(/\btabber-session-tab\b[^>]*\bactive\b/.test(sessionRow?.nameHtml || ''), `visible session ${session} uses the shared active tab chrome`);
      assert.equal(windowRow?.classes.includes('tabber-active-window'), true, `visible session ${session} highlights its active tmux sub-window`);
    }
    assert.equal(rows.find(row => row.path === '/s_1/w_0')?.ariaCurrent, 'true', 'the focused visible tab owns the single current row');
    assert.equal(rows.find(row => row.path === '/s_2/w_0')?.ariaCurrent, '', 'another visible tab stays active without becoming current');
    const hiddenSession = rows.find(row => row.type === 'session' && row.path === '/s_3');
    const hiddenWindow = rows.find(row => row.type === 'window' && row.path === '/s_3/w_0');
    assert.equal(hiddenSession?.classes.includes('tabber-active-session'), false, 'a tab not shown in any pane is not highlighted');
    assert.equal(hiddenSession?.ariaCurrent, '', 'a tab not shown in any pane has no active aria state');
    assert.equal(hiddenWindow?.classes.includes('tabber-active-window'), true, 'tmux-window activity remains distinct from whether its parent tab is visible');

    api.setFocusedPanelItem('2');
    assert.equal(api.activeTabberRowPathForTest(), '/s_2/w_0', 'moving focus selects the second visible tab in Tabber');
    assert.deepStrictEqual(canonical(api.tabberTreeSelectionForTest().paths), ['/s_2/w_0'], 'the shared focus path updates Tabber selection without changing active highlights');
    const refocusedRows = api.tabberRenderedRowsForTest();
    assert.equal(refocusedRows.find(row => row.path === '/s_1/w_0')?.ariaCurrent, '', 'the old focused row no longer reads as current');
    assert.equal(refocusedRows.find(row => row.path === '/s_2/w_0')?.ariaCurrent, 'true', 'the new focused row reads as current');
  });

  test('Tabber active window follows the optimistic tmux sub-window override', () => {
    const api = loadYolomux('', ['1']);
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
    assert.equal(rows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.classes.includes('tabber-active-window'), true, 'Tabber row highlight honors the optimistic known-window override');
    assert.equal(rows.find(r => r.type === 'window' && /0:codex/.test(r.name))?.classes.includes('tabber-active-window'), false, 'stale backend-active Tabber row is not highlighted while an override is active');
    api.setTmuxWindowActiveIndexPendingForTest('1');
    assert.equal(api.activeTabberRowPathForTest(), '/s_1', 'unknown tmux sub-window changes fall back to the session row until readback confirms');
    rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.some(r => r.type === 'window' && r.classes.includes('tabber-active-window')), false, 'pending unknown tmux sub-window changes do not leave a stale active window highlighted');
  });

  test('Tabber window click keeps the optimistic target through stale session updates', () => {
    const api = loadYolomux('', ['1']);
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
    windowOne.dataset.tabberType = 'window';
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
    assert.equal(rows.find(r => r.type === 'window' && /1:claude/.test(r.name))?.classes.includes('tabber-active-window'), true, 'the clicked 1:claude row stays highlighted');
    assert.equal(rows.find(r => r.type === 'window' && /0:codex/.test(r.name))?.classes.includes('tabber-active-window'), false, 'the stale 0:codex row does not regain highlight');
  });

  await testAsync('Tabber confirmed direct window target ignores fresh stale signal bounce', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
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
    assert.equal(rows.find(r => r.type === 'window' && /1:claude/.test(r.name))?.classes.includes('tabber-active-window'), true, 'the guarded 1:claude row stays highlighted');
    assert.equal(rows.find(r => r.type === 'window' && /0:codex/.test(r.name))?.classes.includes('tabber-active-window'), false, 'the stale 0:codex row stays inactive');
  });

  test('Tabber displays home-relative paths with the shared compact home formatter', () => {
    const api = loadYolomux('', ['7', '8', '9', '10']);
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

    const tree = api.buildTabberTree();
    const sessionSeven = tree.entries.find(entry => entry.tabber?.session === '7');
    const sessionSevenWindow = tree.entriesByDir.get('/' + sessionSeven.name)[0];
    const sessionSevenRepo = tree.entriesByDir.get('/' + sessionSeven.name + '/' + sessionSevenWindow.name)[0];
    assert.equal(sessionSevenRepo.tabber.label, '/home/test/project', 'Tabber keeps backend repo labels absolute before render');

    const rows = api.tabberRenderedRowsForTest();
    const byRepo = new Map(rows.filter(row => row.type === 'repo').map(row => [row.repoRoot, row]));
    assert.equal(byRepo.get('/home/test/project')?.name, '~/project', 'Tabber renders a repo under home as ~/name');
    assert.equal(byRepo.get('/home/test/project')?.title.split('\n')[0], '~/project', 'Tabber title uses the same compact home path');
    assert.equal(byRepo.get('/home/test')?.name, '~', 'Tabber renders the bare home directory as ~');
    assert.equal(byRepo.get('/etc/yolo')?.name, '/etc/yolo', 'Tabber leaves non-home paths unchanged');
    assert.equal(byRepo.get('~/already')?.name, '~/already', 'Tabber does not double-abbreviate an already-tilde path');
    assert.ok(/^\/s_7\/w_0\/r_/.test(byRepo.get('/home/test/project')?.path || ''), 'Tabber synthetic tree path stays id-based, not display-path based');
    assert.equal((byRepo.get('/home/test/project')?.path || '').includes('~'), false, 'Tabber synthetic tree path never receives home abbreviation');
  });

  test('Differ shared tree controller handles keys without taking diff-ref input keys', () => {
    const api = loadYolomux('', ['1']);
    api.setFileExplorerModeForTest('diff');
    const panel = new TestElement('differ-keyboard-panel');
    panel.classList.add('file-explorer-panel', 'file-explorer-changes-panel');
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
    api.selectPanelOnHover(api.fileExplorerItemId);
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

  // S2 BEHAVIORAL PROOF: a file that is an open tab appears ONCE in the on-type palette — as its
  // Tabs row — with its Recent/Files duplicate suppressed. Without the dedup it would appear twice.
  test('t@8136', () => {
    const path = '/repo/app/notes.py';
    const enc = encodeURIComponent('file:' + path);   // file%3A%2Frepo%2Fapp%2Fnotes.py
    // open the file as a real tab (slot3) via the layout URL — same shape as the passing test above
    const search = `?sessions=files,${enc},5&layout=row@20.7(slot2,row@42(slot3,slot1))&tabs=slot2:files;slot3:${enc};slot1:5`;
    const api = loadYolomux(search, ['5']);
    const panes = api.serialize(api.currentSlots()).panes;
    assert.ok(Object.values(panes).some(p => (p.tabs || []).includes('file:' + path)), 'S2 setup: the file is an open tab');
    // the SAME file is also recently-open (Recent) and a search candidate (Files) — both would dupe it
    api.setOpenFileStateForTest(path, {name: 'notes.py'});
    api.setFileQuickOpenCandidatesForTest('/repo/app', [{name: 'notes.py', path, relative_path: 'notes.py'}]);
    api.setCommandPaletteStateForTest('files', 'notes');
    const items = api.commandPaletteItems();
    const fileGroupRows = items.filter(it => it.category === 'file' && (it.searchFields?.[1] || it.detail) === path);
    const tabRows = items.filter(it => it.category === 'pane' && api.fileItemPath(it.targetItem || '') === path);
    assert.equal(fileGroupRows.length, 0, 'S2: the open file is NOT duplicated as a Recent/Files row');
    assert.ok(tabRows.length >= 1, 'S2: the open file still appears as its Tabs row');
  });

  test('t@8155', () => {
    // #8-#13: renames, toggles, theme propagation, README preview.
    const api = loadYolomux('', ['1']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    // #8: Branch Info -> YO!info. The tab/menu/palette labels are now localized (functions, not consts) so
    // a runtime language switch repaints them; en still resolves to "YO!info" / "YO!agent".
    assert.ok(/function infoTabLabel\(\)\s*\{\s*return t\('brand\.tab\.info'\)/.test(source), '#8: the YO!info tab label is localized via brand.tab.info');
    assert.ok(/function yoagentTabLabel\(\)\s*\{\s*return t\('brand\.tab\.agent'\)/.test(source), 'the YO!agent tab label is localized via brand.tab.agent');
    // #9: File -> Finder toggles via toggleFinderPane (hide when in layout, else open).
    assert.ok(source.includes('menuCommand(fileExplorerLabel(), () => toggleFinderPane()'), '#9: File -> Finder uses the toggle');
    assert.ok(/function toggleFinderPane\(\)\s*\{[^}]*itemInLayout\(fileExplorerItemId\)[^}]*removeSessionFromLayout\(fileExplorerItemId\)/.test(source), '#9: toggle hides the Finder when it is already in the layout');
    // terminalThemeSettingForGlobalMode still maps modes correctly (pure helper), but #261 stopped the
    // View -> Theme toggle from PINNING the terminal palette — the terminal follows the app on its own.
    assert.equal(api.terminalThemeSettingForGlobalMode('system'), 'follow-app', 'system maps to follow-app');
    assert.equal(api.terminalThemeSettingForGlobalMode('dark'), 'dark', 'dark maps to dark');
    assert.equal(api.terminalThemeSettingForGlobalMode('light'), 'light', 'light maps to light');
    assert.equal(source.includes('patch.appearance.terminal_theme = terminalThemeSettingForGlobalMode(next)'), false, '#261: the View theme toggle no longer pins the terminal palette (terminal follows the app)');
    // #10: a global-theme change re-themes live editors via the compartment swap.
    assert.ok(/previousEditorSchemeId !== activeEditorScheme\(\)\.id \|\| previousCursorColor !== fileEditorCursorColor\)\s*\{[^}]*refreshOpenEditorThemePanels\(\)/.test(source), '#10: theme or cursor-color change re-themes open editors');
    // #12: Preferences field renamed. (Phase 0: the label is now i18n-keyed; en.json holds the text.)
    assert.ok(source.includes("preferenceSettingItem('appearance.theme'") && source.includes('label: t(localeKeys.label, labelParams)'), '#12: the global theme field is i18n-keyed through the shared setting builder');
    assert.ok(source.includes("'appearance.date_time_hour_cycle': '24'") && source.includes("initialSetting('appearance.date_time_hour_cycle')"), 'date/time clock defaults through the shared setting fallback table');
    assert.ok(source.includes("preferenceSettingItem('appearance.date_time_hour_cycle'"), 'date/time clock Preferences field uses the shared i18n setting builder');
    const enThemeCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(enThemeCatalog['pref.appearance.theme.label'], 'Global appearance', '#12: the Preferences field reads "Global appearance"');
    assert.equal(enThemeCatalog['pref.appearance.theme.label'] === 'Global app theme', false, '#12: no stale "Global app theme" label remains');
    // #13: Help -> README opens rendered markdown preview.
    assert.ok(source.includes("openFileInEditor(path, 'README.md', {viewMode: 'preview'})"), '#13: README opens in preview mode');
  });

  test('t@8186', () => {
    // every pane keeps its active tab clearly green (no dimming); focused pane = brighter
    // lime + ring. Source-guards on the shared tokens + the un-dimmed unfocused-active rule.
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/--pane-tab-unfocused-active-bg:\s*var\(--pane-tab-active-bg\)/.test(css), '#11: unfocused panes show a clearly-visible green active tab (aliased to the focused full-green token; images 003/004)');
    assert.ok(/\.panel:not\(\.active-pane\):not\(\.file-explorer-panel\) \.pane-tab\.active\s*\{\s*opacity:\s*1/.test(css), '#11: unfocused active tabs are no longer dimmed');
    assert.ok(/--active-accent-bright:\s*#86d600/.test(css), '#11: the focused pane keeps the brighter lime active tab as its extra cue (via active-accent)');
  });

  test('t@8195', () => {
    // re-renders + search-focus are deferred/suppressed mid-drag so the dragged DOM node
    // is not replaced (which aborts the native HTML5 drag); + 3-tab placement in a consistent index space.
    const api = loadYolomux('', ['1', '2', '3']);
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
    const dragSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    const dragCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(dragSrc.includes('minimumContrastRatio: terminalMinimumContrastRatio()'), '#32: terminal creation sets minimumContrastRatio');
    assert.ok(dragSrc.includes('item.term.options.minimumContrastRatio = minContrast'), '#32: live terminals re-apply minimumContrastRatio');
    assert.ok(/applyTerminalContainerTheme\(item\.container, theme\)/.test(dragSrc), '#32: all terminal containers share one theme background');
    assert.ok(/body\.theme-light \.topbar-search\s*\{[^}]*background/.test(dragCss), '#33: the topbar search blends in light mode (no dark pill)');
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
    assert.equal(/pendingPanelsRender/.test(endDragBody), false, '#endSessionDrag no longer uses the old boolean pendingPanelsRender flag');
  });

  test('t@8235', () => {
    // same-strip drag-reorder works in BOTH directions. Dropping a tab anywhere onto a
    // neighbor moves it past that neighbor (no center-overshoot required for the left->right case).
    const api = loadYolomux('', ['6']);
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

  test('t@8250', () => {
    const api = loadYolomux();
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

  test('t@8263', () => {
    const api = loadYolomux();
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

  test('t@8302', () => {
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

    api.showPaneTabDropPreview(strip, {clientX: 225, clientY: 8}, '2');
    assert.equal(strip.classList.contains('drag-over'), false, 'same-strip adjacent no-op does not show a tab-strip target outline');
    assert.equal(strip.classList.contains('tab-drop-preview'), false, 'same-strip adjacent no-op does not show an insertion preview');
    assert.equal(strip.style.getPropertyValue('--tab-drop-x'), '');
    assert.equal(strip.style.getPropertyValue('--tab-drop-y'), '');
    assert.equal(strip.style.getPropertyValue('--tab-drop-height'), '');
  });

  test('t@8334', () => {
    const api = loadYolomux();
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

  test('t@8347', () => {
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
  });

  test('t@8367', () => {
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
  });

  test('t@8392', () => {
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
  });

  test('t@8413', () => {
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
  });

  test('t@8451', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(source.includes('installFilePathDropTarget(session, panel);'), false, 'panel-level path drop target must not swallow pane split previews');
    assert.ok(source.includes('installTerminalFileDrop(session, container);'), 'terminal surface still accepts path insertion drops');
  });

  test('t@8457', () => {
    const api = loadYolomux();
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
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function bindPaneTabNativeDragSource\([\s\S]*startSessionDrag\(event, item, paneTabDragSourceSlot[\s\S]*dragImage: options\.dragImage/.test(source), 'normal pane tabs have one shared native drag-source binder with an optional drag image override');
    assert.ok(source.includes('bindPaneTabNativeDragSource(tab, item, () => side);'), 'createPaneTab uses the shared native tab drag binder');
    assert.ok(/function bindTabberRowDragSource\(row\)[\s\S]*bindPaneTabNativeDragSource\(row, \(\) => tabberRowDragItem\(row\)/.test(source), 'Tabber rows reuse the shared native tab drag binder');
    assert.ok(source.includes('dragImage: () => tabberNativeDragImageForRow(row)'), 'Tabber rows and inner session tabs preview only the dragged subtree');
    assert.ok(/function dockviewHandleFileDragOver\(event\)[\s\S]*const tabPayload = dragPayload\(event\)[\s\S]*dropIntentAllowsSession\(tabPayload\.session, intent\)[\s\S]*showDropPreview\(intent\)/.test(source), 'Dockview accepts external shared tab drags from Tabber rows');
    assert.ok(/function dockviewHandleFileDrop\(event\)[\s\S]*const tabPayload = dragPayload\(event\)[\s\S]*dropSessionWithIntent\(tabPayload\.session, intent/.test(source), 'Dockview drops external shared tab drags through the normal session intent path');

    const api = loadYolomux('', ['1', '2']);
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
    const sessionRow = Array.from(groups.querySelectorAll('.file-tree-row[data-tabber-type="session"]'))
      .find(row => row.dataset.tabberSession === '1');
    assert.ok(sessionRow, 'rendered Tabber tree includes session 1');
    assert.equal(sessionRow.draggable, true, 'Tabber session rows are draggable tab sources');
    assert.equal(sessionRow.dataset.paneTab, '1', 'Tabber session rows expose the shared tab identity');

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
    assert.equal(previewRows.some(row => row.dataset.tabberSession === '1' && row.dataset.tabberType === 'session'), true, 'preview includes the dragged session tab row');
    assert.equal(previewRows.some(row => row.dataset.tabberSession === '1' && row.dataset.tabberType === 'window'), true, 'preview includes visible child rows under the dragged tab');
    assert.equal(previewRows.some(row => row.dataset.tabberSession === '2'), false, 'preview excludes sibling Tabber rows');
    assert.equal(event.dataTransfer.dragImage.x, 13);
    assert.equal(event.dataTransfer.dragImage.y, 7);
    api.endSessionDrag(event);
    assert.equal(preview.removed, true, 'Tabber drag image preview is cleaned up at drag end');
  });

  test('t@8485', () => {
    const api = loadYolomux('', ['1', '2', '3']);
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

  test('t@8555', () => {
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
  });

  // editor back/forward navigation history (Popular IDE-style file stack). Tests the record/dedupe/
  // truncate logic of recordEditorNav (the async back/forward re-open goes through the live open path).
  test('t@8592', () => {
    const api = loadYolomux();
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
  test('t@8634', () => {
    const api = loadYolomux();
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
    const api = loadYolomux('', ['1']);
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
  test('t@8648', () => {
    const api = loadYolomux();
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
  test('t@8661', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/const fileGroups = new Map\(\);[\s\S]{0,400}?fileItemPath\(item\)/.test(source), 'the palette groups file tabs by path (fileItemPath)');
    assert.ok(source.includes('tabRow(editorItem, {key: `file:${path}`, viewModes})'), 'editor+preview of one file collapse to a single `file:` row carrying view chips');
    assert.ok(source.includes('command-palette-view-chip'), 'the deduped file row renders edit/preview view chips');
    // follow-up: the chips are clickable — each carries its view's layout item and jumps to it.
    assert.ok(/data-view-item="\$\{esc\(v\.item\)\}" data-view-mode="\$\{esc\(v\.mode\)\}"/.test(source), 'each view chip carries its layout item + mode');
    assert.ok(/closest\('\[data-view-item\]'\)[\s\S]{0,180}selectSession\(viewItem, \{userInitiated: true\}\)/.test(source), 'clicking a chip jumps to that view and closes the palette');
  });

  // the Modified-files repo header is a collapse toggle (button + caret), per-repo state.
  test('t@8672', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('head.dataset.changesRepoToggle = repo'), 'the repo header is a collapse toggle keyed by repo path');
    assert.ok(source.includes('changesRepoCollapsed.has(repo)'), 'the repo head reads per-repo collapse state');
    assert.ok(source.includes('changes-repo-caret'), 'the repo head shows a collapse caret');
  });

  // (button completion): the back/forward buttons live in the GLOBAL topbar (left of the search
  // box), are wired to editorNavBack/Forward, and tab-switches record as nav.
  test('t@8681', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("createTopbarNav())"), 'the topbar assembles the nav group');
    assert.ok(source.indexOf('createTopbarNav())') < source.indexOf('createTopbarSearch())'), 'the nav (back/forward) group is appended before the topbar search box');
    assert.ok(/const back = makeButton\(\{[\s\S]*id: 'topbarNavBack'/.test(source) && /const forward = makeButton\(\{[\s\S]*id: 'topbarNavForward'/.test(source), 'the topbar nav builds #topbarNavBack / #topbarNavForward through the shared button builder');
    assert.ok(/topbarNavBack[\s\S]{0,400}?editorNavBack\(\)/.test(source), 'the back button is wired to editorNavBack()');
    assert.ok(/topbarNavForward[\s\S]{0,400}?editorNavForward\(\)/.test(source), 'the forward button is wired to editorNavForward()');
    assert.ok(/getElementById\('topbarNavBack'\)[\s\S]{0,200}?editorNav\.index <= 0/.test(source), 'updateEditorNavButtons disables Back at the start of the stack');
    assert.ok(source.includes('setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});'), 'a user-initiated tab switch of ANY tab kind records through the shared focus path');
    // keyboard chords — Mod+Alt+[ / Mod+Alt+] drive editor back/forward (Mod+[ / ] stay with CM indent).
    assert.ok(/event\.altKey && \(event\.code === 'BracketLeft' \|\| event\.code === 'BracketRight'\)/.test(source), 'editor nav has a Mod+Alt+bracket keyboard chord');
    assert.ok(/BracketLeft'\) editorNavBack\(\)/.test(source) && source.includes('else editorNavForward()'), 'the bracket chord maps [ to back and ] to forward');
    // an auto-focus-driven focus change records nav history (debounced, gated on autoFocusEnabled).
    assert.ok(source.includes('function recordAutoFocusNav(item, previousItem = null)'), 'auto-focus nav recorder exists');
    assert.ok(source.includes('recordAutoFocusNav(item, previousItem);'), 'setFocusedPanelItem records auto-focus nav with the previous focus');
    assert.ok(/recordAutoFocusNav[\s\S]{0,240}?if \(!autoFocusEnabled[\s\S]{0,240}?focusedPanelItem === item\) recordFocusNavTransition\(previousItem, item\)/.test(source), 'it is gated on autoFocusEnabled and debounced to the landed focus');
    assert.ok(!source.includes('file-editor-nav-control'), 'the per-pane editor nav group is fully removed (relocated to the topbar)');
  });

  // inline git blame — toolbar toggle + a CodeMirror line decoration (::after annotation).
  test('t@8701', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('file-editor-blame-panel'), 'the editor toolbar has a blame toggle button');
    assert.ok(source.includes('file-editor-icon-blame'), 'the blame toggle uses the shared editor icon box, not an unaligned text glyph');
    const editorCss = fs.readFileSync('static/yolomux.css', 'utf8');
    const centeredBlameOwner = editorCss.match(/(?:^|\n)([^{}]*\.file-editor-icon-blame::before[^{}]*)\{([^}]*)\}/);
    assert.ok(centeredBlameOwner, 'Blame outer circle participates in the shared centered editor-icon owner');
    assert.ok(centeredBlameOwner[1].includes('.file-editor-icon-blame::after'), 'Blame center dot participates in the same centered editor-icon owner');
    assert.ok(/top:\s*50%/.test(centeredBlameOwner[2]) && /left:\s*50%/.test(centeredBlameOwner[2]) && /transform:\s*translate\(-50%, -50%\)/.test(centeredBlameOwner[2]), 'the shared editor-icon owner centers both blame shapes');
    assert.ok(source.includes('function codeMirrorBlameExtension(api, path)'), 'a CodeMirror blame extension decorates the cursor line');
    assert.ok(source.includes("'data-blame': blameAnnotationText(info)"), 'the cursor line gets the dim blame annotation via data-blame (CSS ::after)');
    assert.ok(source.includes('codeMirrorBlameExtension(api, path)'), 'the blame extension is wired into the editable editor extensions');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.cm-line\[data-blame\]::after\s*\{[^}]*var\(--code-comment\)/.test(css), 'the blame annotation uses the theme-aware --code-comment token');
    // Fix: blame state is in the editor config signature, so toggling blame OFF rebuilds the editor and
    // the annotations are removed (the plugin is added/removed only at build time).
    assert.ok(source.includes('blame: fileEditorBlameEnabled'), 'blame is part of the editor config signature so a toggle rebuilds (annotations clear on OFF)');
    // follow-up: an all-lines blame Preference — the extension branches on it and the signature
    // carries it (so toggling the pref rebuilds the decorations).
    assert.ok(source.includes('blameAllLines: fileEditorBlameAllLines'), 'blame-all-lines is in the editor config signature');
    assert.ok(/if \(fileEditorBlameAllLines\)[\s\S]{0,260}view\.visibleRanges/.test(source), 'all-lines blame decorates every visible line');
    assert.ok(source.includes("preferenceSettingItem('editor.blame_all_lines'"), 'Preferences exposes the all-lines blame toggle through the shared setting builder');
    // Blame + Diff buttons are adjacent git-history controls, but Blame stays available after Diff learns
    // a file is clean so inline blame still works in normal edit mode.
    assert.ok(/file-editor-blame-panel[\s\S]{0,260}file-editor-diff-panel/.test(source), 'Blame and Diff buttons are adjacent toolbar controls');
    assert.ok(source.includes('state.gitRoot = payload.git_root ? normalizeDirectoryPath(payload.git_root) : \'\''), 'editor state carries the per-file git_root from /api/fs/read through the shared normalizer without turning empty root into /');
    assert.ok(/function fileStateHasRepo\(path, state\)[\s\S]*state\?\.gitRoot \? normalizeDirectoryPath\(state\.gitRoot\) : ''[\s\S]*pathIsInsideDirectory\(normalized, root\)/.test(source), 'editor git actions require the file itself to be inside its reported repo root');
    assert.ok(/function fileStateHasUsefulGitHistory[\s\S]*state\?\.gitTracked === true[\s\S]*state\?\.gitHasHistory === true[\s\S]*state\.gitHistory\.length > 1/.test(source), 'file-history metadata requires an actual multi-commit file history');
    assert.ok(/function fileEditorGitActionControlsVisible[\s\S]*fileStateHasRepo\(path, state\)[\s\S]*fileStateHasUsefulGitHistory\(state\)[\s\S]*confirmedNoDiff/.test(source), 'Diff visibility hides files outside repos, non-git, creation-only, stale-history, or confirmed-clean files');
    assert.ok(/function fileEditorBlameControlsVisible[\s\S]*fileStateHasRepo\(path, state\)[\s\S]*fileStateHasUsefulGitHistory\(state\)/.test(source), 'Blame visibility depends on repo membership and useful file history, not current diff availability');
    assert.ok(/function updateFileEditorBlameButton[\s\S]*fileEditorBlameControlsVisible\(path, state, item\)/.test(source), 'blame button uses the blame-specific history visibility helper');
    assert.ok(/function updateFileEditorBlameButton[\s\S]*editorViewModeFor\(path, item\) === 'edit'[\s\S]*button\.disabled = !visible \|\| !editable/.test(source), 'Blame is clickable only in normal edit mode');
    assert.ok(/'editor-blame': \(_event, target\) => \{[\s\S]*target\?\.disabled\) return/.test(source), 'disabled Blame clicks do not toggle the global blame preference');
    assert.ok(/function updateFileEditorDiffButton[\s\S]*const visible = fileEditorGitActionControlsVisible\(path, state, item\)[\s\S]*button\.hidden = !visible[\s\S]*button\.disabled = !visible/.test(source), 'diff button uses the shared git-action visibility predicate and disabled state');
    assert.ok(/'editor-diff': \(_event, target\) => \{[\s\S]*target\?\.disabled \|\| target\?\.hidden\) return/.test(source), 'hidden or disabled Differ clicks cannot enter diff mode');
    assert.ok(source.includes('state.gitTracked = payload.git_tracked === true'), 'editor state carries the git_tracked flag from /api/fs/read through the shared normalizer');
  });

  // Search scroll fix: navigating matches re-centers the match horizontally, so a short-line match in a
  // doc with a long line elsewhere is no longer left scrolled fully right (off-screen / blank).
  test('t@8739', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('function codeMirrorSearchScrollFix(api)'), 'search-scroll fix extension exists');
    assert.ok(source.includes('codeMirrorSearchScrollFix(api)'), 'search-scroll fix is wired into the editable editor extensions');
    assert.ok(/isUserEvent\?\.\('select\.search'\)/.test(source), 'the fix triggers only on search-driven selection changes');
    assert.ok(/scrollIntoView\(head,\s*\{x:\s*'center'/.test(source), 'the fix re-centers the match horizontally');
  });

  // S2: a file open as a tab shows ONCE in the merged palette — its deduped Tabs row (carrying
  // both edit + preview chips) wins; the Recent/Files duplicate is dropped. Files-only / empty-box keep Recent.
  test('t@8749', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const api = loadYolomux('', ['1']);
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
    assert.ok(candidateStart > 0 && candidateEnd > candidateStart, 'DOIT.55: unified command palette candidate provider exists');
    assert.ok(candidateBody.includes('return commandPaletteMergedItems()'), 'DOIT.55: typed Cmd-P and Shift-Cmd-P queries use one merged candidate universe');
    assert.ok(candidateBody.includes('mode === \'files\' ? fileQuickOpenItems() : commandPaletteCommandItems()'), 'DOIT.55: mode only chooses the empty-query home category');
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
    ], 'agent', {surface: 'files'}).slice(0, 8);
    const mixedDomains = new Set(mixedRows.map(item => item.category || 'command'));
    assert.ok(mixedDomains.has('file'), 'typed Cmd-P still leads with relevant files');
    assert.ok(mixedDomains.has('pane'), 'typed Cmd-P first screen includes a matching pane/tab row');
    assert.ok(mixedDomains.has('command'), 'typed Cmd-P first screen includes a matching command row');
    assert.ok(mixedDomains.has('setting'), 'typed Cmd-P first screen includes a matching setting row');
    api.setFileQuickOpenCandidatesForTest('/repo/app', []);
    api.setFileQuickOpenLoadingForTest(true);
    api.setCommandPaletteQueryForTest('');
    const loadingItem = api.fileQuickOpenItems().find(item => item.loading === true);
    assert.ok(loadingItem, 'Cmd-P exposes a loading row while file search is in flight');
    assert.ok(/Searching files[\s\S]*moving-ellipsis command-palette-loading-dots/.test(api.commandPaletteStatusHtmlForTest()), 'Cmd-P loading status shows moving dots');
    assert.ok(/Searching files[\s\S]*moving-ellipsis command-palette-loading-dots/.test(api.commandPaletteItemLabelHtmlForTest(loadingItem, '')), 'Cmd-P loading row shows moving dots');
    assert.ok(/renderCommandPaletteResults[\s\S]*commandPaletteRankItems\(commandPaletteItems\(\), query\)/.test(source), 'DOIT.55: rendering uses the shared provider/ranker path');
    assert.equal(source.includes('function commandPaletteItemPriorityRank'), false, 'DOIT.55: old per-surface priority rank fork stays removed');
    assert.ok(/const searchRankWeights = Object\.freeze\(\{[\s\S]*domainPrior:[\s\S]*recencyHalfLifeSeconds:[\s\S]*repoAffinity:[\s\S]*mixWindow:/.test(source), 'DOIT.55: ranking weights live in one exported table');
    assert.equal(/100000|60000|20000/.test(fileNameBonusBody), false, 'DOIT.55: file-name ranking bonuses use searchRankWeights, not inline legacy constants');
  });

  // S14: opt-in tab-drag timing instrumentation (OFF by default) to diagnose the ~500ms first-drag
  // delay by measuring the real bucket, instead of guessing setDragImage is the cause.
  test('t@8758', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("storageGet('yolomux.debugDragTiming') === '1'"), 'S14: drag timing is gated behind an opt-in storage flag (no permanent user-visible perf log)');
    assert.ok(source.includes("dragTimingMark('pointerdown')"), 'S14: pointerdown is marked (pointerdown->dragstart bucket)');
    assert.ok(source.includes("dragTimingMark('startSessionDrag:begin')") && source.includes("dragTimingMark('startSessionDrag:end')"), 'S14: startSessionDrag is bracketed by timing marks');
    assert.ok(source.includes("dragTimingMarkOnce('dragMeasureStrip:first')") && source.includes("dragTimingMarkOnce('paneTabDropPlacement:first')"), 'S14: first strip-measure and drop-placement are marked');
    assert.ok(source.includes('dragTimingReport()'), 'S14: the per-bucket report fires at drag end');
    assert.ok(source.includes('function showDragTimingOverlay(') && source.includes("el.className = 'drag-timing-overlay'"), 'S14: a copyable on-page timing overlay (no DevTools) is rendered at drag end');
  });

  // root cause + fix: while Claude/tmux owns the mouse, copied text arrives as an OSC 52 clipboard
  // escape; xterm.js drops it unless a handler is registered. The bridge decodes it and writes the browser
  // clipboard, and never answers '?' read queries (no clipboard exfiltration).
  test('t@8771', () => {
    const api = loadYolomux('?platform=mac', ['1'], 'https:', 'MacIntel');
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
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('installTerminalOsc52Bridge(session, term);'), 'OSC 52 bridge is wired into terminal startup');
    assert.ok(/registerOscHandler\(52, data =>/.test(source), 'bridge registers the OSC 52 parser handler');
    assert.ok(source.includes('writeTerminalTextToClipboard(text, {action: TERMINAL_COPY_ACTIONS.osc52, params: {count: text.length}})'), 'bridge routes through the shared terminal clipboard-write chain and pluralized status action');
    assert.ok(source.includes("storageGet('yolomux.debugCopy') === '1'"), 'copy-path debug logging is gated behind an opt-in storage flag');
    assert.ok(source.includes("copyDebug('shortcut'") && source.includes("copyDebug('osc52'") && source.includes("copyDebug('clipboard'"), 'N1: shortcut, OSC 52, and clipboard-write stages each log one compact debug event');
  });

  // right-click must not clear the terminal highlight; the menu copies the selection captured at
  // right-click time.
  test('t@8804', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/container\.addEventListener\('mousedown', event => \{[\s\S]*?event\.button !== 2[\s\S]*?rightClickSelection = terminalSelectedText\(term, container\);[\s\S]*?event\.stopPropagation\(\);[\s\S]*?\}, \{capture: true\}\)/.test(source), 'N7: a capture-phase right-mousedown captures the selection and stops xterm clearing it');
    assert.ok(/showTerminalContextMenu\(session, term, event\.clientX, event\.clientY, container, rightClickSelection\)/.test(source), 'N7: the context menu receives the selection captured at right-click time');
    assert.ok(/function terminalContextMenuSelection\(session, term, container = null, presetSelection = null\)[\s\S]*presetSelection == null \? terminalSelectedText\(term, container\) : String\(presetSelection \|\| ''\)/.test(source), 'N7: an explicitly captured empty right-click selection is not replaced by a live under-cursor re-read');
    assert.ok(/function terminalContextMenuSelection\(session, term, container = null, presetSelection = null\)[\s\S]*recentTerminalAppClipboardText\(session\)/.test(source), 'N7: Claude/TUI OSC 52 clipboard text is the context-menu fallback when the app owns the visible selection');
    assert.ok(/copyTerminalSelection\(session, term, \{action, dedent, selectionText: selected\}, container\)/.test(source), 'N7: menu Copy uses the captured selection text, not a stale live re-read');
    assert.ok(/const selected = options\.selectionText != null \? options\.selectionText : terminalSelectedText\(term, container\)/.test(source), 'N7: copyTerminalSelection honors an explicit captured selection');
    const api = loadYolomux('', ['1']);
    const badLiveRead = {getSelection: () => 'under cursor'};
    assert.deepEqual(api.terminalContextMenuSelectionForTest('1', badLiveRead, null, ''), {text: '', source: 'none'}, 'captured empty right-click selection does not copy under-cursor live text');
    api.rememberTerminalAppClipboardTextForTest('1', 'claude selected block');
    assert.deepEqual(api.terminalContextMenuSelectionForTest('1', badLiveRead, null, ''), {text: 'claude selected block', source: 'app-clipboard'}, 'recent Claude OSC 52 app selection beats under-cursor live text');
  });

  test('Tabber window recency uses one timestamp for display, sorting, and parent bubbling', () => {
    const api = loadYolomux('', ['4']);
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

    const tree = api.buildTabberTree();
    const session = tree.entries.find(entry => entry.tabber?.session === '4');
    const windows = tree.entriesByDir.get('/' + session.name);
    const claudeWindow = windows.find(row => row.tabber.windowIndex === 0);
    const bashWindow = windows.find(row => row.tabber.windowIndex === 1);
    const repoRows = tree.entriesByDir.get('/' + session.name + '/' + claudeWindow.name);
    assert.equal(claudeWindow.mtime, 2000, 'Claude/Codex window mtime uses transcript-event recency, not status or ledger recency');
    assert.equal(bashWindow.mtime, 3000, 'bash/non-agent window mtime uses activity[session:window].active_recency_ts');
    assert.equal(session.mtime, 3000, 'parent session mtime is max child semantic mtime after child rows are assigned');
    assert.equal(repoRows[0].mtime, 9000, 'touched-path file mtime stays on the path row');

    const rows = api.tabberRenderedRowsForTest();
    const sessionRow = rows.find(row => row.type === 'session' && row.path === '/s_4');
    const claudeRow = rows.find(row => row.type === 'window' && row.path === '/s_4/w_0');
    const bashRow = rows.find(row => row.type === 'window' && row.path === '/s_4/w_1');
    const repoRow = rows.find(row => row.type === 'repo' && row.repoRoot === '/home/u/proj');
    assert.equal(claudeRow?.date, api.sessionFileTimeText(2000), 'Claude/Codex child date text uses the same transcript timestamp as row mtime');
    assert.equal(bashRow?.date, api.sessionFileTimeText(3000), 'bash/non-agent child date text uses the same ledger timestamp as row mtime');
    assert.equal(sessionRow?.date, '', 'parent session rows omit aggregate timestamps because child sub-windows own the recency');
    assert.equal(repoRow?.date, api.sessionFileTimeText(9000), 'touched-path rows still display touched-file recency');
    assert.notEqual(claudeRow?.date, 'working for 1h 00m', 'working status does not replace the Tabber date column timestamp');
  });

  test('Tabber preserves Ago timestamps across follower refresh placeholders', () => {
    const api = loadYolomux('', ['4']);
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
      .filter(row => row.type === 'window')
      .map(row => [row.path, row.date]));
    const originalDates = windowDates();
    assert.equal(originalDates['/s_4/w_0'], api.sessionFileTimeText(2000), 'full snapshot shows the agent timestamp');
    assert.equal(originalDates['/s_4/w_1'], api.sessionFileTimeText(3000), 'full snapshot shows the shell timestamp');

    const placeholder = {activity: {}, agents: [], agent_windows: {}, cache: {refreshing_elsewhere: true}};
    assert.equal(api.applyTabberActivityPayloadForTest(placeholder, 2), false, 'empty follower refresh is not authoritative over useful activity');
    assert.deepEqual(windowDates(), originalDates, 'follower refresh cannot make several Tabber timestamps disappear');

    const replacement = {
      activity: {'4:1': {active_recency_ts: 5000}},
      agents: [{session: '4', window: '0', agent_kind: 'claude', last_used_ts: 4000, sort_ts: 4000, running: true}],
      agent_windows: {'4': [{window_index: 0}]},
    };
    assert.equal(api.applyTabberActivityPayloadForTest(replacement, 3), true, 'a complete newer snapshot replaces the current payload');
    assert.deepEqual(windowDates(), {'/s_4/w_0': api.sessionFileTimeText(4000), '/s_4/w_1': api.sessionFileTimeText(5000)}, 'complete replacement updates both timestamps');
    assert.equal(api.applyTabberActivityPayloadForTest({...replacement, activity: {'4:1': {active_recency_ts: 7000}}}, 1), false, 'an older overlapping request cannot roll back an accepted snapshot');
    assert.equal(windowDates()['/s_4/w_1'], api.sessionFileTimeText(5000), 'stale response ordering leaves the accepted timestamp intact');

    assert.equal(api.applyTabberActivityPayloadForTest({activity: {}, agents: [], agent_windows: {}, cache: {refreshing_elsewhere: false}}, 4), true, 'a genuine non-placeholder empty snapshot remains authoritative');
    assert.deepEqual(windowDates(), {'/s_4/w_0': '', '/s_4/w_1': ''}, 'legitimate expiry can remove timestamps instead of retaining data forever');
  });

  // DOIT.58 B1-B7: the Tabber (Finder pane's third mode) — source guards that rows route through the
  // shared row pipeline (no forked *RowHtml builder), plus a behavioral test of the tree assembly.
  test('t@tabber', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    const tabberSessionChromeSource = source.match(/function tabberSessionChromeHtml\(data\) \{[\s\S]*?\n\}/)?.[0] || '';
    // B1/B3 source guards: routes through the shared pipeline, no forked builder, finder refreshes skip tabber rows.
    assert.ok(/mode === 'diff' \|\| mode === 'tabber' \? mode : 'files'/.test(source), 'B1: normalizeFileExplorerMode accepts files|diff|tabber');
    assert.ok(/if \(options\.mode === 'tabber'\) return updateTabberRow\(/.test(source), 'B3: updateFileTreeRow dispatches tabber rows to updateTabberRow');
    assert.ok(/renderTreeChildren\(container, '\/', entries, 0, \{[\s\S]*?mode: 'tabber'/.test(source), 'B3: renderTabberTree drives renderTreeChildren with mode:tabber');
    assert.ok(/updateFileTreeRowContents\(row, icon, label,/.test(source), 'B3: updateTabberRow fills columns via the shared updateFileTreeRowContents');
    assert.equal(/function tabberRowHtml|function renderTabberRowHtml|function tabberFileRowHtml/.test(source), false, 'B3: no bespoke tabber *RowHtml builder');
    assert.ok((source.match(/\.file-tree-row\[data-path\]:not\(\[data-tabber-type\]\)/g) || []).length >= 2, 'finder global row refreshes exclude tabber rows (no relabel/clobber)');
    assert.ok(/\.file-tree-row\.tabber-row\s*\{[\s\S]*--tabber-level0-color:\s*var\(--markdown-heading\)[\s\S]*--tabber-level1-color:\s*var\(--code-function\)[\s\S]*--tabber-path-color:\s*var\(--text\)/.test(css), 'Tabber uses restrained level colors and keeps path rows normal text');
    assert.ok(/body\.theme-light \.file-tree-row\.tabber-row\s*\{[\s\S]*--tabber-level0-color:\s*var\(--text\)[\s\S]*--tabber-level1-color:\s*var\(--text\)/.test(css), 'Tabber light mode keeps session and window row text dark');
    assert.equal(/body\.theme-light \.file-tree-row\.tabber-row\s*\{[^}]*(?:--tabber-path-color|--tabber-detail-color)/.test(css), false, 'Tabber path and detail colors inherit their theme-aware base tokens instead of restating them in light mode');
    assert.ok(/\.file-tree-row\.tabber-row\[data-tabber-type="tab"\]:not\(\.selected\) > \.file-tree-name,[\s\S]*color:\s*var\(--tabber-level0-color\)/.test(css), 'non-tmux Tabber pane rows do not use purple');
    assert.ok(/\.file-tree-row:is\(\.tabber-active-window, \.tabber-active-tab\):not\(\.selected\) \.file-tree-name\s*\{[\s\S]*font-weight:\s*800/.test(css), 'active Tabber windows and non-tmux tabs share one bold row emphasis');
    const sharedPaneTabCss = css.match(/\.pane-tab,\s*\.tmux-pane-tab-token\s*\{([\s\S]*?)\}/)?.[1] || '';
    assert.ok(/height:\s*var\(--pane-tab-height\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use the shared pane-tab height');
    assert.ok(/padding:\s*var\(--space-1\) var\(--space-5\) 0/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab padding');
    assert.ok(/border:\s*1px solid var\(--pane-inactive-tab-border\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use the shared pane-tab border token');
    assert.ok(/border-radius:\s*var\(--pane-tab-top-radius\) var\(--pane-tab-top-radius\) 0 0/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab top radius');
    assert.ok(/font-family:\s*var\(--tab-font\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab font');
    assert.ok(/line-height:\s*var\(--tab-line-height\)/.test(sharedPaneTabCss), 'A2: compact tmux tab tokens use pane-tab line height');
    assert.ok(/\.tmux-pane-tab-token-action\s*\{[\s\S]*cursor:\s*pointer/.test(css), 'A2: shared compact tmux tab tokens own the interactive cursor');
    assert.ok(/\.pane-tab:not\(\.active\):hover,\s*\.pane-tab:not\(\.active\):focus-visible,\s*\.tmux-pane-tab-token-action:not\(\.active\):hover,\s*\.tmux-pane-tab-token-action:not\(\.active\):focus-visible\s*\{[\s\S]*background:\s*var\(--pane-inactive-tab-bg-hover\)[\s\S]*border-color:\s*var\(--paint-white-92\)/.test(css), 'A2: regular and compact inactive pane tabs share token-owned pointer and keyboard interaction paint');
    assert.ok(/body\.theme-light \.pane-tab:not\(\.active\):hover,[\s\S]*body\.theme-light \.tmux-pane-tab-token-action:not\(\.active\):focus-visible\s*\{[\s\S]*border-color:\s*rgb\(var\(--overlay-slate-rgb\) \/ 0\.5\)/.test(css), 'A2: one light-theme owner corrects every regular and compact interaction border');
    const sharedActiveTabRule = css.match(/\.yolomux-dockview \.dv-tab\.dv-active-tab > \.dockview-pane-tab:not\(\.file-missing\),[\s\S]*?\.file-explorer-mode-toggle\[aria-pressed="true"\]\s*\{([\s\S]*?)\}/);
    assert.ok(sharedActiveTabRule, 'A5: every active tab-like surface routes through one paint owner');
    for (const selector of ['.tmux-pane-tab-token.active', '.panel.active-pane .pane-tab.active:not(.file-missing)', '.panel.file-explorer-panel .pane-tab.active:not(.file-missing)']) {
      assert.ok(sharedActiveTabRule[0].includes(selector), `A5: shared active tab owner includes ${selector}`);
    }
    assert.ok(/color:\s*var\(--pane-tab-active-text\)[\s\S]*background:\s*var\(--pane-tab-active-bg\)[\s\S]*border-color:\s*var\(--pane-tab-active-border\)[\s\S]*border-bottom-color:\s*var\(--pane-tab-active-bg\)/.test(sharedActiveTabRule[1]), 'A5: the shared active tab owner carries the complete active paint');
    assert.equal((css.match(/border-bottom-color:\s*var\(--pane-tab-active-bg\)/g) || []).length, 1, 'A5: the exact active-tab paint signature has one owner');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-active-tab > \.dockview-pane-tab \.session-button-name,[\s\S]*\.tmux-pane-tab-token\.active \.session-button-detail,[\s\S]*\.pane-tab\.active \.session-button-detail\s*\{\s*color:\s*inherit/.test(css), 'A5: every active tab child inherits color from the shared shell paint');
    assert.ok(/\.panel \.pane-tab:not\(\.active\)\s*\{\s*background:\s*var\(--pane-bar-bg\)/.test(css), 'A5: one unconditional panel owner paints inactive pane tabs');
    assert.equal(css.includes('.panel:not(.active-pane):not(.typing-ready-pane) .pane-tab:not(.active)'), false, 'A5: inactive tab paint has no focus/readiness partition');
    assert.equal(/\.panel\.active-pane \.pane-tab\.active,\s*\.panel\.file-explorer-panel \.pane-tab\.active\s*\{\s*box-shadow:\s*none/.test(css), false, 'A5: focused pane tabs do not restate the base active no-shadow rule');
    const tabberSessionTabCss = css.match(/\.file-tree-row\.tabber-row \.tabber-session-tab\s*\{([\s\S]*?)\}/)?.[1] || '';
    assert.ok(/inline-size:\s*100%/.test(tabberSessionTabCss) && /max-inline-size:\s*100%/.test(tabberSessionTabCss), 'A2/A4: Tabber session label stretches to the row instead of the pane tab width cap');
    assert.equal(/max-inline-size:\s*min\(var\(--pane-tab-width\), 100%\)/.test(tabberSessionTabCss), false, 'A2/A4: Tabber session label has no shared pane-tab-width max cap');
    assert.equal(/(?:^|\n)\s*(?:color|background|border|border-radius|cursor)\s*:/.test(tabberSessionTabCss), false, 'A2: Tabber session labels do not duplicate visual tab shell styling');
    assert.equal(/\.file-tree-row\.tabber-row\[data-tabber-type="session"\][\s\S]*:hover \.tabber-session-tab/.test(css), false, 'A2: Tabber session hover styling stays on the shared tmux tab token');
    assert.equal(/\.file-tree-row\.tabber-row\.tabber-active-session \.tabber-session-tab/.test(css), false, 'A5: Tabber active styling stays on the shared tmux tab token');
    assert.ok(/\.file-tree-row\.tabber-row\.selected \.tabber-session-tab\s*\{[\s\S]*box-shadow:\s*var\(--active-control-focus-shadow-compact\)/.test(css), 'A2/A3: selected tree rows use the shared compact focus shadow on the tab-shaped label');
    assert.ok(/\.tmux-pane-tab-token > \.pane-tab-core\s*\{\s*inline-size:\s*100%;\s*\}/.test(css), 'TR3: compact tmux tab chrome only adds its full-width core override');
    assert.ok(/\.pane-tab-core\s*\{[\s\S]*min-width:\s*0[\s\S]*flex:\s*1 1 auto/.test(css), 'TR3: regular and compact tabs inherit core flex behavior from one generic owner');
    assert.ok(/\.session-button-prefix\s*\{[\s\S]*flex:\s*0 0 auto[\s\S]*min-width:\s*max-content[\s\S]*overflow:\s*visible/.test(css), 'A4/TR1: one shared intrinsic session prefix preserves the identifier while the detail flexes');
    assert.equal(/\.session-button-(?:name|prefix)[\s\S]{0,160}max-width:\s*(?:72|120)px/.test(css), false, 'A4/TR1: session labels do not carry fixed pixel capacity caps');
    assert.ok(/\.tmux-pane-tab-token \.tab-inline-detail,\s*\.pane-tab \.tab-inline-detail,\s*\.pane-tab\.file-editor-item \.session-button-dir\s*\{[\s\S]*flex:\s*1 1 auto[\s\S]*min-width:\s*0[\s\S]*max-width:\s*none/.test(css), 'A4/TR1: regular, compact, and file-editor pane-tab text share one stretching owner');
    assert.ok(/body\.theme-light \.tmux-pane-tab-token:not\(\.active\) \.session-button-name,[\s\S]*body\.theme-light \.tmux-pane-tab-token:not\(\.active\) \.session-button-detail\s*\{[\s\S]*color:\s*currentColor/.test(css), 'compact tmux tab tokens own light-mode child label inheritance');
    assert.equal(/\.file-tree-row\.tabber-row \.tabber-session-tab > \.pane-tab-core/.test(css), false, 'TR3: Tabber no longer owns the compact tab core flex rule');
    assert.equal(/\.info-tree-tab-token > \.pane-tab-core/.test(css), false, 'TR3: YO!info no longer owns a duplicate compact tab core flex rule');
    assert.ok(/\.session-popover-host > \.session-popover,\s*\.pane-tab-detached-popover\s*\{[\s\S]*position:\s*fixed[\s\S]*z-index:\s*var\(--z-pane-modal\)/.test(css), 'TR2: Tabber session popovers use the same fixed-position popover surface as real tabs');
    assert.ok(/\.session-popover-host\.popover-open > \.session-popover,\s*\.pane-tab-detached-popover\.popover-open\s*\{[\s\S]*visibility:\s*visible[\s\S]*opacity:\s*1/.test(css), 'TR2: Tabber session popovers open through the same visibility selector as real tabs');
    assert.ok(source.includes("'tabber-session-tab', 'session-popover-host'"), 'TR2: Tabber session tabs opt into the shared popover host class');
    assert.ok(/function tmuxPaneTabTokenHtml\(session, options = \{\}\)[\s\S]*tmux-pane-tab-token-action[\s\S]*stripContentTitles === true/.test(source), 'TR1: the shared compact tmux pane-tab token helper owns action/static classes and optional title stripping');
    assert.ok(tabberSessionChromeSource.includes('tmuxPaneTabTokenHtml(session,') && /stripContentTitles:\s*true/.test(tabberSessionChromeSource), 'TR1: Tabber session rows use the shared compact tmux pane-tab token helper');
    assert.ok(/detail:\s*data\.description \|\| ''/.test(tabberSessionChromeSource) && /sessionWorkDescription\(session, info, 0\)/.test(source), 'Tabber passes the unbounded work description into shared tab chrome so only real layout overflow truncates it');
    assert.equal(tabberSessionChromeSource.includes('tmuxPaneTabHtml('), false, 'TR1: Tabber does not rebuild shared tmux pane-tab inner HTML');
    assert.equal(/\.file-tree-row\.tabber-row \.tabber-session-tab > \.session-popover\s*\{[\s\S]*width:\s*min\(420px/.test(css), false, 'TR2: Tabber does not keep a divergent one-off popover width');
    assert.equal(source.includes("tabber: {type: 'loading'"), false, 'Tabber no longer renders a client-side touched-path loading row');
    assert.ok(source.includes('function tabberLookbackControlHtml()') && source.includes('data-tabber-lookback'), 'Tabber renders a dedicated touched-path lookback control');
    assert.ok(/function setTabberSessionFileLookbackHours\(hours, options = \{\}\)[\s\S]*clearTabberSessionFilesStates\(\)[\s\S]*fetchTabberActivity\(\)/.test(source), 'changing Tabber lookback reloads the cached activity agent-window records');
    assert.ok(/\.file-tree-row\.tabber-row\s*\{[\s\S]*--tabber-agent-icon-size:\s*calc\(var\(--file-explorer-font-size\) \+ 2px\)/.test(css), 'Tabber owns one row-scale agent icon-size token');
    assert.ok(/\.file-tree-row\.tabber-row \.tabber-session-tab \.session-agent-activity-marker \.agent-window-activity\s*\{[\s\S]*--agent-window-icon-size:\s*var\(--tabber-agent-icon-size\)/.test(css), 'Tabber parent session tabs use the shared row-scale agent icon-size token');
    assert.ok(/\.info-tree-ai-value\.tmux-window-bar,\s*\.file-tree-row\.tabber-row \.tabber-window-token\s*\{[\s\S]*max-width:\s*100%[\s\S]*justify-content:\s*flex-start/.test(css), 'YO!info and Tabber child rows share one tmux-window token alignment owner');
    assert.ok(/\.file-tree-row\.tabber-row \.tabber-window-token\s*\{[\s\S]*display:\s*inline-flex[\s\S]*min-width:\s*0/.test(css), 'Tabber child process rows keep only their local inline-flex/min-width shape');
    assert.ok(/\.file-tree-row\.tabber-row \.tabber-window-label \.agent-window-activity\s*\{[\s\S]*--agent-window-icon-size:\s*var\(--tabber-agent-icon-size\)[\s\S]*--agent-window-activity-inline-size:\s*var\(--agent-window-icon-size\)/.test(css), 'Tabber child fallback rows keep row-scale agent icons while sub-window glyphs inherit the shared Tab-circle size reference');
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
    assert.ok(/const agentState = String\(data\.agentStatus\?\.state \|\| STATE_KEY\.idle\);[\s\S]*row\.classList\.toggle\('tabber-status-long', data\.type === 'window'[\s\S]*agentWindowIsWorkingState\(agentState\)[\s\S]*agentWindowIsAttentionState\(agentState\)/.test(source), 'SC1: Tabber classifies long working/attention rows from semantic state, independent of translated display text');
    assert.ok(/\.file-tree-row\.tabber-row\.tabber-status-long \.file-tree-date\s*\{[\s\S]*display:\s*block[\s\S]*flex:\s*0 1 auto[\s\S]*text-align:\s*start[\s\S]*text-overflow:\s*ellipsis/.test(css), 'SC1/SC2: long Tabber statuses fit content and truncate at the end, not from the leading edge');
    assert.equal(/@container[\s\S]*tabber-row \.file-tree-date[\s\S]*display:\s*none/.test(css), false, 'Tabber never hides the <time> ago recency column for narrow panes');
    assert.ok(/function tabberActivityVisibleConsumer\(\)[\s\S]*fileExplorerMode === 'tabber'[\s\S]*document\.visibilityState !== 'hidden'/.test(source), 'Tabber activity polling only marks visible Tabber panes as subscribers');
    assert.ok(/async function fetchTabberActivity\(options = \{\}\)[\s\S]*params\.set\('visible', visible \? '1' : '0'\)/.test(source), 'Tabber activity requests include the visible-consumer flag');
    assert.ok(/function warmTabberDataOnLaunch\(\)[\s\S]*!tabberActivityVisibleConsumer\(\)[\s\S]*tabberLaunchWarmupStarted = true;[\s\S]*fetchTabberActivity\(\);[\s\S]*return true;/.test(source), 'Tabber launch warmup primes cached activity only for visible Tabber panes');
    assert.ok(/function tabberSessionPopoverRefreshIsUnsafe\(\)[\s\S]*\.tabber-session-tab\[data-popover-hover-state="open"\][\s\S]*popoverLifecycleActive\(tab, popover\)/.test(source), 'Tabber refresh detects active session-popover hover lifecycle before rebuilding rows');
    assert.ok(/function refreshTabberPanels\(\)[\s\S]*tabberSessionPopoverRefreshIsUnsafe\(\)[\s\S]*scheduleDeferredTabberRefresh\(\)[\s\S]*return/.test(source), 'Tabber activity refresh defers instead of replacing hovered session-tab DOM');
    assert.ok(/function renderAutoApproveButton\(session, payload\)[\s\S]*button\.setAttribute\('aria-label', buttonLabel\)[\s\S]*button\.closest\('\.tabber-session-tab'\)[\s\S]*button\.removeAttribute\('title'\)/.test(source), 'Tabber YO controls keep aria-label but suppress native title hovers after live auto-state sync');
    assert.ok(/transcriptMetadataState\.loaded = true;[\s\S]*?warmTabberDataOnLaunch\(\)/.test(source), 'Tabber launch warmup runs as soon as transcript metadata is available');
    assert.ok(/function tabberAgentForWindow\(session, windowIndex, agentKey = ''\)/.test(source), 'Tabber can look up agent transcript activity by session/window');
    assert.ok(source.includes('function agentWindowPathEntries(agent)') && source.includes('function windowViewModel(session, windowIndex'), 'Tabber reads paths through the shared backend agent-window view model');
    assert.equal(/tabberTouchedRepoPathsForWindow|tabberRepoPathsForWindow|tabberFileMatchesWindow/.test(source), false, 'Tabber has no client-side per-window path resolver');
    assert.ok(source.includes('function tabberWindowRecency(row)') && source.includes('const activityTs = tabberAgentRecency(row?.agentActivity)') && source.includes('return tabberRecency(`${session}:${windowIndex}`)'), 'Tabber window rows have one helper that owns the semantic recency timestamp');
    assert.ok(source.includes('const windowMtime = tabberWindowRecency({session, windowIndex: record.index, record, isAgent, agentKey, agentActivity, agentStatus});'), 'Tabber window mtime comes from the shared window-recency helper');
	    assert.ok(/function updateTabberRow\([\s\S]*updateFileTreeRowContents\(row, icon, label,[\s\S]*applyFileTreeRowRecency\(row, entry, options\)/.test(source), 'Tabber timestamp rows route through the shared Finder/Differ recency styling');
	    assert.ok(/function tabberWindowDateDisplay\(recencyTs, agentStatus = null, nowSeconds = Date\.now\(\) \/ 1000\)[\s\S]*fileExplorerTreeDateMode === 'none'[\s\S]*sessionFileDisplayTimeText\(recencyTs, \{nowSeconds\}\)/.test(source), 'Tabber window date text renders the same timestamp used for row mtime and parent bubbling');
	    assert.equal(/const dateText = agentStatusForDisplay \? sessionPopoverAgentStateText/.test(source), false, 'Tabber agent rows do not bypass the Date toggle with popover-only status text');
	    assert.equal(/icon:\s*'▢'|icon:\s*'■'/.test(source), false, 'Tabber shell/session rows avoid checkbox-looking square glyphs');
    assert.ok(/let tabberActivityRefreshMs;[\s\S]*tabberActivityRefreshMs = initialSetting\('performance\.tabber_activity_refresh_ms'\);/.test(source), 'Tabber activity refresh initializes from server-provided settings defaults');
    assert.equal(source.includes("initialSetting('performance.tabber_activity_refresh_ms', 15000)"), false, 'Tabber activity refresh does not keep a duplicated bootstrap fallback');
    assert.equal(source.includes("numberSetting('performance.tabber_activity_refresh_ms', 15000)"), false, 'Tabber activity refresh does not keep a duplicated reload fallback');
    assert.ok(/Promise\.resolve\(state\.callback\(\)\)[\s\S]*?\.finally\(scheduleNext\)/.test(source), 'runtime intervals wait for async callbacks to settle before starting the next wait');
    assert.ok(/file-index-building', refreshBuildingFileIndexStatuses, Math\.min\(1501, proactiveMs\)/.test(source), 'DOIT.61 A5: file-index building poll keeps the odd 1501ms cadence cap');
    assert.equal(source.includes('tabber-row-detail'), false, 'DOIT.61 A4: Tabber no longer carries a dead visible detail slot');
    assert.equal(source.includes("type === 'path' && row.dataset.tabberOpenFile"), false, 'DOIT.61 A3: Tabber has no unreachable path-row activation branch');
    assert.ok(source.includes('function setRowDataset(row, key, value)'), 'DOIT.61 B1: row dataset set/delete is centralized');
    assert.ok(source.includes('const tabberSessionFilesStates = new Map()'), 'DOIT.61 B2: Tabber session files use one state map');
    assert.equal(/tabberSessionFilesCache|tabberSessionFilesInFlight/.test(source), false, 'DOIT.61 B2: Tabber no longer has parallel cache + inflight state');
    assert.ok(source.includes('/api/session-files-batch?') && source.includes('function fetchTabberSessionFilesBatch(sessions'), 'the modified-files session-file helpers still have one batch request');
    assert.ok(/function ensureTabberSessionFilesFetches\(\)[\s\S]*fetchTabberActivity\(\)/.test(source), 'Tabber open reuses cached activity instead of a session-files path fetch');
    assert.equal(source.includes('fetchTabberSessionFilesBatch(tabberAgentSessions())'), false, 'Tabber agent-window paths do not hydrate through the old client session-files batch');
    assert.ok(/params\.set\('hours', String\(hours\)\)/.test(source), 'Tabber batch touched-path requests carry the selected lookback hours');
    assert.ok(/\/api\/session-files\?session=\$\{encodeURIComponent\(session\)\}&hours=\$\{encodeURIComponent\(String\(hours\)\)\}/.test(source), 'Tabber single-session fallback requests carry the selected lookback hours');
    assert.ok(/apiFetchJson\(`\/api\/activity\?\$\{params\.toString\(\)\}`/.test(source) && /params\.set\('hours', String\(normalizeSessionFileLookbackHours\(tabberSessionFileLookbackHours\)\)\)/.test(source), 'Tabber activity fetch carries the selected touched-path lookback hours');
    assert.ok(source.includes('function fileTreeRowPadding(depth, compact = false)') && source.includes('function fileTreeRowDepth(row, compact = false)'), 'DOIT.61 B3: tree indentation math is centralized');
    assert.ok(source.includes('const nextDepth = fileTreeRowDepth(row) + 1'), 'DOIT.61 B3: directory expansion uses the shared depth helper');
    assert.ok(source.includes('function clearFileTreeRowHandlers(row)') && (source.match(/clearFileTreeRowHandlers\(row\)/g) || []).length >= 2, 'DOIT.61 B4: stale row handler cleanup is shared');
    assert.ok(source.includes('function setTreeItemAria(row') && (source.match(/setTreeItemAria\(row/g) || []).length >= 2, 'DOIT.61 B5: treeitem aria is shared');
    assert.ok(source.includes('function normalizeGitStatus(status)') && source.includes('return normalizeGitStatus(fileTreeChangedFile(path)?.status)'), 'DOIT.61 B6: git status normalization is shared');
    assert.equal(source.includes("endsWith(' ●')"), false, 'DOIT.61 B7: active window state is not parsed out of the label string');
    assert.ok(/function tabberWindowButtonHtml\(data, label\)[\s\S]*tmuxWindowButtonHtml\(\{[\s\S]*classes:\s*\['tabber-window-button'\][\s\S]*showNumberLabel:\s*false[\s\S]*const pidText = tmuxWindowPidText\(data\?\.pid\)[\s\S]*const pidHtml = pidText \? `<span class="tabber-window-pid"> \$\{esc\(pidText\)\}<\/span>` : ''[\s\S]*stripTitleAttrs\(buttonHtml\)\}\$\{pidHtml\}/.test(source) && source.includes('function agentWindowPayloadCurrent(agent)'), 'DOIT.61 B7/PD: Tabber tmux sub-window rows route through the shared compact button helper and PID formatter while stripping native titles');
    assert.ok(/function sessionPopoverWindowPidByIndex\(info\)[\s\S]*tmuxWindowRecords\(info\?\.panes \|\| \[\]\)/.test(source), 'PP1: popover PID comes from the same tmux sub-window record source as Tabber');
    assert.ok(source.includes('tmuxWindowDisplayLabel(descriptor, agent.pid)'), 'PP1: popover PID label reuses the shared tmux sub-window pid formatter');
    assert.ok(/type === 'window' && session\) \{[\s\S]*switchWindow\(\);[\s\S]*selectSession\(session, \{userInitiated: true\}\)/.test(source), 'Tabber window clicks install the tmux-window override before focus/layout can sync against stale active metadata');
    assert.ok(/type === 'repo' && row\.dataset\.tabberRepoRoot\) \{[\s\S]*switchWindow\(\);[\s\S]*setFileExplorerMode\('files'\)/.test(source), 'Tabber repo clicks also install the tmux-window override before leaving Tabber mode');
    assert.equal(source.includes("if (entry.tabber?.type !== 'session') fileExplorerTabberCollapsed.add(path)"), false, 'Tabber collapse-all and disclosure toggles include session rows');
    assert.ok(/function tabberSessionForNumericKey\(key\)[\s\S]*\^\[1-9\]\$/.test(source), 'Tabber maps bare numeric keys to matching tmux sessions');
    assert.ok(/function setTabberPathExpanded\(fullPath, expanded\)[\s\S]*tabberPathDefaultsCollapsed\(fullPath\)[\s\S]*fileExplorerTabberExpanded\.add\(fullPath\)[\s\S]*setExpanded\(row, expanded\) \{[\s\S]*setTabberPathExpanded\(fullPath, expanded\)/.test(source), 'Tabber disclosure clicks and keyboard expansion reuse the default-collapse-aware expansion owner');
    assert.ok(/row\.dataset\.kind === 'dir' && fullPath && onDisclosure[\s\S]*toggleTabberCollapsed\(fullPath\)[\s\S]*type === 'session' && session\)[\s\S]*openTabberSession\(session\)/.test(source), 'Tabber session row text opens the session while the shared disclosure branch toggles collapse');

    const api = loadYolomux();
    assert.equal(api.normalizeFileExplorerMode('tabber'), 'tabber');
    assert.equal(api.normalizeFileExplorerMode('bogus'), 'files');
    assert.equal(api.readStoredFileExplorerModeForTest('tabber'), 'files', 'Tabber is an explicit mode choice, not the default restored left Finder pane');
    assert.ok(/data-file-explorer-mode-set="files"[\s\S]*data-file-explorer-mode-set="diff"[\s\S]*data-file-explorer-mode-set="tabber"/.test(api.fileExplorerModeSwitcherHtml()), 'B1: Finder / Differ / Tabber order');
    assert.equal(source.includes('data-tabber-expand'), false, 'Tabber session descriptions are not separate expand-only targets');
    assert.ok(tabberSessionChromeSource.includes('tmuxPaneTabTokenHtml(session,') && tabberSessionChromeSource.includes('sessionPopoverHtml(session, info, agentKind, auto, state)'), 'A1/A2/TR1: session rows render the shared tmux pane tab chrome and popover');
    assert.equal(source.includes('tabber-session-name') || source.includes('tabber-session-description'), false, 'TR5: Tabber does not keep bespoke session name/description chrome');
    assert.ok(/function bindTabberSessionChrome\(row, session\)[\s\S]*applySessionStateClasses\(tab, state\)[\s\S]*bindPaneTabPopover\(tab, session\)[\s\S]*toggleAutoApprove/.test(source), 'TR2: Tabber session rows reuse the shared state classes, popover binding, and YO toggle action');
    assert.ok(/const detached = tab\.classList\?\.contains\('dockview-pane-tab'\) === true\s*\|\| tab\.classList\?\.contains\('tabber-session-tab'\) === true/.test(source), 'TR2: Dockview and Tabber popovers detach through the same app-overlay path');
    assert.ok(/if \(name\.innerHTML !== options\.nameHtml\) \{\s*cleanupDetachedPopoversWithin\(name\);\s*name\.innerHTML = options\.nameHtml;/.test(source), 'TR2: Tabber row replacement cleans detached popovers through the shared cleanup helper');
    assert.ok(/function fileExplorerTreeSortSelectHtml\(extraClass = ''\)[\s\S]*data-file-explorer-tree-sort[\s\S]*finder\.sort\.az[\s\S]*finder\.sort\.oldest/.test(source), 'TS1/TS4: Finder and Tabber share one tree sort select component and locale keys');
    assert.ok(/fileExplorerMode === 'tabber'[\s\S]*tabberLookbackControlHtml\(\)[\s\S]*fileExplorerTreeSortSelectHtml\('changes-sort-select-compact'\)/.test(source), 'TS1: Tabber toolbar renders the shared A-Z/Z-A/recent/oldest sort control');
    assert.ok(/file-explorer-actions-row[\s\S]*fileExplorerTreeSortSelectHtml\('file-explorer-mode-files-only'\)/.test(source), 'TS3: Finder toolbar also renders the shared tree sort select');
    assert.ok(/row\.classList\.toggle\('tabber-active-session', data\.type === 'session' && data\.active === true\)/.test(source), 'A5: active-session styling is data-driven only for session rows');
    assert.ok(/function refreshTabberPanelsForFocusChange\(\)[\s\S]*scheduleTabberTreeLayoutStateSync\(\)/.test(source) && !/function refreshTabberPanelsForFocusChange\(\)[\s\S]{0,160}refreshTabberPanels\(\)/.test(source), 'focus changes schedule one Tabber layout-state patch without rebuilding the tree');
    assert.ok(/function updatePanelSlot\(panel, session, slot\)[\s\S]*updatePaneTabStrip\(panel, slot\);\s*\}/.test(source), 'slot-local panel updates do not recursively synchronize global focus state');
    assert.ok(/current \|\| \(data\.type === 'session' && data\.active === true\)\) row\.setAttribute\('aria-current', 'true'\)/.test(source), 'A3/A5: the current tmux session/window exposes aria-current on the tree row');
    assert.ok(/type === 'tab' && row\.dataset\.tabberItem[\s\S]*selectSession\(row\.dataset\.tabberItem, \{userInitiated: true\}\)/.test(source), 'Tabber virtual-tab rows activate their own layout item directly');
    assert.equal(/tabberOpenFile|tabberOpenStatus|tabberOpenRepo|type === 'path'|data-tabber-type="path"/.test(source), false, 'Tabber has no individual-file row data or activation path');
    api.setInfoPanelSubTabForTest('yoagent');
	    api.commandPaletteCommandItems().find(item => item.targetItem === api.infoItemId).run();
	    assert.equal(api.infoPanelSubTabForTest(), 'yoagent', 'YO!info palette rows no longer mutate the legacy YO!agent chrome marker');
	    api.setFileExplorerModeForTest('tabber');
	    api.setFileExplorerTreeDateModeForTest('relative');
	    api.setFileExplorerTreeSortModeForTest('za');
    const tabberToolbarHtml = api.fileExplorerChangesPanelStaticHtmlForTest();
    assert.ok(tabberToolbarHtml.includes('data-file-explorer-tree-sort'), 'TS1: Tabber toolbar exposes the shared tree sort select');
    assert.ok(tabberToolbarHtml.includes('value="az"') && tabberToolbarHtml.includes('value="za"') && tabberToolbarHtml.includes('value="newest"') && tabberToolbarHtml.includes('value="oldest"'), 'TS1: Tabber sort select offers A-Z, Z-A, recent, and oldest');
    assert.ok(/value="za" selected/.test(tabberToolbarHtml), 'TS3: Tabber sort select reflects the shared persisted tree sort mode');
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

    const {entries, entriesByDir} = api.buildTabberTree();
    const s1 = entries.find(e => e.tabber && e.tabber.session === '1');
    assert.ok(s1 && s1.tabber.type === 'session', 'B2: tmux session 1 appears at level 0');
    assert.equal(s1.tabber.active, true, 'A5: the focused tmux session is marked active at the top level');
    assert.equal(entries.find(e => e.tabber && e.tabber.session === '2')?.tabber.active, true, 'A5: a second session shown in another pane is active too');
    assert.ok(String(s1.tabber.branchText || '').length > 0, 'B2: the repo branch is retained as Tabber metadata');
    assert.equal(s1.tabber.description, longSessionWork, 'Tabber keeps the full work description instead of baking a character-count ellipsis into the row');
    const windows = entriesByDir.get('/' + s1.name);
    assert.ok(Array.isArray(windows) && windows.length === 2, 'B2: session 1 has its two tmux sub-windows');
    const claudeWin = windows.find(w => /0:claude/.test(w.tabber.label));
    assert.ok(claudeWin, 'B2: window label is index:process (0:claude)');
    assert.equal(claudeWin.tabber.label, '0:claude', 'PL/WI: AI window labels use the canonical index:agent label without raw pid text');
    assert.equal(claudeWin.tabber.pid, 12345, 'PD3: Tabber window entries carry the pid as data');
    assert.equal(claudeWin.tabber.active, true, '#2: the active window is flagged');
    assert.equal(claudeWin.kind, 'dir', 'L3: the agent window expands to touched absolute paths');
    const repos = entriesByDir.get('/' + s1.name + '/' + claudeWin.name);
    assert.ok(Array.isArray(repos) && repos.length === 1 && repos[0].tabber.type === 'repo', 'L3: agent window holds a repo group');
    assert.equal(repos[0].kind, 'file', 'L3: repo/path rows are leaves, not expandable file lists');
    assert.equal(repos[0].tabber.label, '/home/u/proj', 'L3: repo/path row shows the full absolute path');
    assert.equal(repos.some(row => /\/home\/u\/proj\/src/.test(row.tabber.label)), false, 'L3: descendant paths fold into the known repo root');
    assert.equal(repos.some(row => /^\/tmp/.test(row.tabber.label)), false, 'L3: non-repo touched paths are omitted from Tabber');
    assert.equal(entriesByDir.has('/' + s1.name + '/' + claudeWin.name + '/' + repos[0].name), false, 'L3: Tabber does not list individual files under the path row');
    const defaultCollapsedRows = api.tabberRenderedRowsForTest({defaultCollapsed: true});
    const defaultSessionRow = defaultCollapsedRows.find(row => row.type === 'session' && row.path === `/${s1.name}`);
    const defaultWindowRow = defaultCollapsedRows.find(row => row.type === 'window' && row.path === `/${s1.name}/${claudeWin.name}`);
    assert.equal(defaultSessionRow?.ariaExpanded, 'true', 'Tabber defaults to expanding each session Tab');
    assert.ok(defaultSessionRow?.nameHtml.includes(`<span class="session-button-dir tab-inline-detail">${longSessionWork}</span>`), 'shared Tabber chrome renders the full description and leaves overflow decisions to CSS');
    assert.equal(defaultWindowRow?.ariaExpanded, 'false', 'Tabber defaults to collapsing the directories inside sub-window buttons');
    assert.equal(defaultCollapsedRows.some(row => row.type === 'repo' && row.path.startsWith(`/${s1.name}/${claudeWin.name}/`)), false, 'Tabber hides sub-window directories until the user expands that window');
    assert.equal(api.setTabberPathExpandedForTest(defaultWindowRow.path, true), true, 'an explicit sub-window expansion changes the shared Tabber expansion model');
    const expandedWindowRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.equal(expandedWindowRows.find(row => row.path === defaultWindowRow.path)?.ariaExpanded, 'true', 'an explicitly expanded sub-window remains expanded after Tabber rerenders');
    assert.ok(expandedWindowRows.some(row => row.type === 'repo' && row.path.startsWith(`${defaultWindowRow.path}/`)), 'an explicitly expanded sub-window shows its attributed directory list');

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
    const scopedTree = api.buildTabberTree();
    const scopedSession = scopedTree.entries.find(e => e.tabber?.session === '3');
    const scopedWindows = scopedTree.entriesByDir.get('/' + scopedSession.name);
    const scopedWindow0 = scopedWindows.find(row => row.tabber.windowIndex === 0);
    const scopedWindow1 = scopedWindows.find(row => row.tabber.windowIndex === 1);
    const scopedRepos0 = scopedTree.entriesByDir.get('/' + scopedSession.name + '/' + scopedWindow0.name).map(row => row.tabber.label);
    const scopedRepos1 = scopedTree.entriesByDir.get('/' + scopedSession.name + '/' + scopedWindow1.name).map(row => row.tabber.label);
    assert.deepEqual(scopedRepos0, ['/home/u/codex-a'], 'Tabber repo rows under window 0 use only files attributed to tmux sub-window 0');
    assert.deepEqual(scopedRepos1, ['/home/u/codex-b'], 'Tabber repo rows under window 1 use only files attributed to tmux sub-window 1');
    assert.ok((scopedWindow0.tabber.activityIconHtml || '').includes('agent-window-agent-icon--active'), 'current idle Codex Tabber window renders the moving active glyph');
    assert.equal((scopedWindow0.tabber.activityIconHtml || '').includes('agent-window-status-dot'), false, 'current idle Codex Tabber window does not add a competing status dot');
    assert.equal((scopedWindow1.tabber.activityIconHtml || '').includes('agent-window-agent-icon--active'), false, 'inactive idle Codex Tabber window does not pulse');

    const sortedApi = loadYolomux('', ['1', '2', '3']);
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
      return sortedApi.tabberRenderedRowsForTest().filter(row => row.type === 'session').map(row => row.title.split('\n')[0]);
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
    const windowLabels = sortedApi.buildTabberTree().entriesByDir.get('/s_1').map(row => row.tabber.label);
    assert.deepEqual(windowLabels, ['0:codex', '1:claude', '2:bash'], 'TS2: tmux sub-window rows stay in tmux index order regardless of Tabber sort mode');
    const renamedSessionApi = loadYolomux('', ['1', '2', '8002b']);
    renamedSessionApi.setTranscriptInfoForTest('8002b', {panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', command: 'claude'}]});
    const renamedSessionHtml = renamedSessionApi.tmuxPaneTabHtml('8002b', renamedSessionApi.transcriptInfoForTest('8002b'), null, false);
    assert.equal(renamedSessionApi.itemLabel('8002b'), '8002b', 'renamed non-numeric tmux sessions use the tmux name as the visible label');
    assert.equal(renamedSessionApi.resolveLayoutItem('9'), '8002b', 'the old shortcut label still resolves to the renamed session');
    assert.ok(renamedSessionHtml.includes('session-button-name') && renamedSessionHtml.includes('>[8002b]<'), 'renamed tmux tab chrome shows the bracketed real session name');
    assert.equal(/session-button-number">9<\/span>/.test(renamedSessionHtml), false, 'renamed tmux tab chrome does not prepend the stale shortcut label');
    const renamedSessionRow = renamedSessionApi.tabberRenderedRowsForTest().find(row => row.type === 'session' && row.title.split('\n')[0] === '8002b');
    assert.ok(renamedSessionRow?.nameHtml.includes('>[8002b]<'), 'Tabber session rows inherit the same bracketed renamed-session tab chrome');

    // Render guard: real labels (never synthetic node names); active window marked; absolute path rows present.
    const rows = api.tabberRenderedRowsForTest();
    assert.equal(rows.some(r => /^[swrf]_\d/.test(r.name)), false, 'rows show human labels, not synthetic node names (got ' + JSON.stringify(rows.map(r => r.name).slice(0, 8)) + ')');
    assert.ok(rows.some(r => {
      if (r.type !== 'session' || !r.nameHtml.includes('tabber-session-tab') || !r.nameHtml.includes('pane-tab-core')) return false;
      const marker = r.nameHtml.match(/<span class="session-agent-activity-marker[^"]*">[\s\S]*?<\/span><\/span>/)?.[0] || '';
      return marker.includes('agent-window-activity--status-only')
        && marker.includes('agent-window-status-dot')
        && !marker.includes('agent-icon claude')
        && r.nameHtml.includes('session-button-prefix')
        && r.nameHtml.includes('tab-inline-detail');
    }), 'A1/A2/TR1: working Claude session rows render the real tab chrome with the shared status-only ball');
    assert.ok(rows.some(r => r.type === 'session' && r.title.split('\n')[0] === '2' && r.nameHtml.includes('data-action="pane-tab-auto-approve"')), 'TR2: Tabber enabled session rows expose the same YO auto-approve action as real tabs');
    assert.equal(rows.some(r => r.type === 'session' && r.title.split('\n')[0] === '1' && r.nameHtml.includes('data-action="pane-tab-auto-approve"')), false, 'TR2: Tabber auto-off working session rows hide the inactive YO action when there is no prompt');
    assert.equal(rows.some(r => r.type !== 'session' && r.nameHtml.includes('tabber-session-tab')), false, 'A1: window/repo/loading/non-tmux Tabber rows do not get the session tab treatment');
    const activeSessionRow = rows.find(r => r.type === 'session' && r.title.split('\n')[0] === '1');
    const secondVisibleSessionRow = rows.find(r => r.type === 'session' && r.title.split('\n')[0] === '2');
    assert.ok(activeSessionRow, 'A5: rendered rows include the focused session');
    assert.ok(secondVisibleSessionRow, 'A5: rendered rows include the second visible session');
    assert.equal(rows.every(r => r.nativeTitle === ''), true, 'Tabber rows do not show native browser titles in addition to the custom Tabber hover');
    assert.equal(/\stitle=/.test((activeSessionRow.nameHtml || '').split('<div class="session-popover"')[0]), false, 'visible Tabber tab chrome strips nested native title hovers');
    assert.ok(activeSessionRow.classes.includes('tabber-active-session'), 'A5: the current tmux session row gets the active-session class');
    const activeWindowRow = rows.find(r => r.type === 'window' && /^0:claude/.test(r.name));
    assert.ok(activeWindowRow?.nameHtml.includes('tmux-window-button tabber-window-button') && activeWindowRow.nameHtml.includes('data-tabber-window-button="shared"'), 'PD4: agent Tabber rows render through the shared compact tmux sub-window button');
    assert.ok(activeWindowRow?.nameHtml.includes('(pid=12345)'), 'PD4: agent Tabber rows render pid beside the compact visible sub-window label');
    assert.equal(/\stitle=/.test(activeWindowRow?.nameHtml || ''), false, 'visible Tabber window chrome strips nested native title hovers');
    assert.equal(activeWindowRow?.ariaCurrent, 'true', 'N10: the active tmux sub-window row exposes aria-current');
    assert.equal(activeWindowRow?.date, '', 'working agent Tabber rows do not fabricate date text without transcript recency');
    assert.equal(activeWindowRow?.dateHtml, '', 'working agent Tabber rows keep status out of the date column');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'needs-input', last_active_ts: Date.now() / 1000 - 5, window_index: 0, window_name: 'claude', window_label: '0:claude'},
    ]});
    const attentionRows = api.tabberRenderedRowsForTest();
    const attentionWindowRow = attentionRows.find(r => r.type === 'window' && /^0:claude/.test(r.name));
    assert.equal(attentionWindowRow?.date, '', 'attention Tabber window rows do not use ledger last_active_ts as transcript recency');
    assert.equal(attentionWindowRow?.dateHtml, '', 'attention Tabber window rows keep status labels out of the date column when transcript recency is missing');
    assert.equal(/\bneeds input\b|\bapproval\b/i.test(`${attentionWindowRow?.date || ''} ${attentionWindowRow?.dateHtml || ''}`), false, 'attention Tabber window rows no longer say needs input or approval in the date column');
    assert.ok(/agent-window-status-dot(?=[^"]*agent-window-status-dot--transition-pulse)(?=[^"]*status-indicator--attention)/.test(attentionWindowRow?.nameHtml || ''), 'attention Tabber window ball carries the transition pulse phase instead of the status text');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'working', working_elapsed_seconds: 13500, window_index: 0, window_name: 'claude', window_label: '0:claude'},
    ]});
    const shellWindowRow = rows.find(r => r.type === 'window' && /1:bash/.test(r.name));
    assert.ok(shellWindowRow?.nameHtml.includes('tmux-window-button tabber-window-button') && shellWindowRow.nameHtml.includes('1:bash'), 'PD5: bash Tabber rows use the same compact tmux sub-window button shell');
    assert.ok(shellWindowRow?.nameHtml.includes('(pid=54321)'), 'PD5: bash Tabber rows render pid inside the compact visible sub-window label');
    assert.equal(shellWindowRow?.icon, '', 'shell/process window leaf rows do not render the old neutral process glyph');
    assert.notEqual(shellWindowRow?.date, 'working for 3h 45m', 'TD4: non-AI tmux sub-windows do not inherit working duration text');
    assert.equal(rows.some(r => r.type === 'window' && ['▢', '■', '⌁'].includes(r.icon)), false, 'tmux sub-window rows never render checkbox-looking or decorative process glyphs');
    assert.equal(activeSessionRow.icon, '›', 'expanded session rows still use the shared disclosure affordance');
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
	    const idleNoDateWindowRow = idleNoDateRows.find(r => r.type === 'window' && r.path === '/s_1/w_1' && /1:codex/.test(r.name));
	    assert.equal(idleNoDateWindowRow?.date, '', 'None mode hides idle AI Tabber row recency text');
	    assert.equal(idleNoDateWindowRow?.dateHtml, '', 'None mode hides idle AI Tabber row status HTML');
	    api.cycleFileExplorerTreeDateModeForTest();
	    assert.equal(api.fileExplorerTreeDateModeForTest(), 'date', 'Date toggle cycle advances None to Date');
	    const idleDateRows = api.tabberRenderedRowsForTest();
	    assert.equal(idleDateRows.find(r => r.type === 'window' && r.path === '/s_1/w_1' && /1:codex/.test(r.name))?.date, api.sessionFileTimeText(idleAgentLastActive), 'Date cycle shows absolute time for idle AI Tabber rows');
	    api.cycleFileExplorerTreeDateModeForTest();
	    assert.equal(api.fileExplorerTreeDateModeForTest(), 'relative', 'Date toggle cycle advances Date to Ago');
	    const idleAgentRows = api.tabberRenderedRowsForTest();
	    const idleAgentWindowRow = idleAgentRows.find(r => r.type === 'window' && r.path === '/s_1/w_1' && /1:codex/.test(r.name));
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
    assert.equal(recentTabberAgoRows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.recency, 'just-updated', 'Tabber Ago timestamp rows mark very recent process activity just-updated');
    assert.equal(recentTabberAgoRows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.dateClasses.includes('attention-pulse'), true, 'Tabber Ago very recent rows pulse the timestamp cell with the shared attention class');
    assert.notEqual(recentTabberAgoRows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.dateAttentionDelay, '', 'Tabber timestamp pulses are phase-aligned');
    assert.equal(recentTabberAgoRows.find(r => r.type === 'window' && /0:claude/.test(r.name))?.recency, 'recent', 'Tabber Ago timestamp rows keep sub-ten-minute graduated styling');
	    api.setFileExplorerTreeDateModeForTest('date');
	    const recentTabberDateRows = api.tabberRenderedRowsForTest();
	    assert.equal(recentTabberDateRows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.recency, 'just-updated', 'Tabber Date timestamp rows preserve very recent recency');
	    assert.equal(recentTabberDateRows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.dateClasses.includes('attention-pulse'), true, 'Tabber Date timestamp rows keep the shared pulse');
	    assert.equal(recentTabberDateRows.find(r => r.type === 'window' && /0:claude/.test(r.name))?.recency, 'recent', 'Tabber Date timestamp rows preserve graduated recency');
	    assert.equal(recentTabberDateRows.find(r => r.type === 'window' && /0:claude/.test(r.name))?.date, api.sessionFileTimeText(2000 - 9 * 60), 'Tabber Date mode shows an absolute timestamp for agent sub-window rows');
	    api.setFileExplorerTreeDateModeForTest('none');
	    const noDateTabberRows = api.tabberRenderedRowsForTest();
	    assert.equal(noDateTabberRows.find(r => r.type === 'window' && /1:bash/.test(r.name))?.recency, '', 'Tabber None mode hides timestamp recency styling');
	    assert.equal(noDateTabberRows.find(r => r.type === 'window' && /0:claude/.test(r.name))?.date, '', 'Tabber None mode hides working agent sub-window status text');
	    assert.equal(noDateTabberRows.find(r => r.type === 'window' && /0:claude/.test(r.name))?.dateHtml, '', 'Tabber None mode hides working agent sub-window status HTML');
    api.setFileTreeRecencyNowForTest(null);
    api.setFileExplorerTreeDateModeForTest('relative');
    assert.ok(/class="[^"]*\btabber-session-tab\b[^"]*\bactive\b/.test(activeSessionRow.nameHtml), 'A5: the current tmux session label reads as an active tab');
    assert.ok(activeSessionRow.nameHtml.includes('data-tabber-session-chrome="shared"'), 'TR5: the session row marks the shared chrome path');
    assert.equal(secondVisibleSessionRow.classes.includes('tabber-active-session'), true, 'A5: every tmux session shown in a pane gets the active-session class');
    assert.equal(secondVisibleSessionRow.ariaCurrent, 'true', 'A5: every tmux session shown in a pane exposes active aria state');
    assert.ok(/\btabber-session-tab\b[^>]*\bactive\b/.test(secondVisibleSessionRow.nameHtml), 'A5: every tmux session shown in a pane uses the active tab shape');
    assert.equal(activeSessionRow.ariaExpanded, 'true', 'A3: session rows remain expandable treeitems');
    assert.equal(activeSessionRow.ariaSelected, 'false', 'A3: session rows keep tree selection state on the row, not the inner label');
    assert.equal(rows.some(r => r.type === 'session' && r.nameHtml.includes('data-tabber-expand')), false, 'session description text is part of the row activation target');
    assert.equal(activeWindowRow?.classes.includes('tabber-active-window'), true, '#2: the current AI window is marked by row emphasis instead of a competing dot');
    assert.equal(rows.some(r => r.type === 'window' && /tabber-window-active/.test(r.nameHtml)), false, '#2: current-window state no longer renders a circle marker beside agent status icons');
    assert.ok(rows.some(r => r.type === 'window' && r.nameHtml.includes('tmux-window-button tabber-window-button') && r.nameHtml.includes('agent-icon claude')), 'Claude Tabber window rows show the shared Claude icon inside the shared button shell');
    assert.ok(rows.some(r => r.type === 'window' && r.nameHtml.includes('tmux-window-button tabber-window-button') && r.nameHtml.includes('agent-icon codex')), 'Codex Tabber window rows show the shared Codex icon inside the shared button shell');
    const claudeWindowRow = activeWindowRow;
    assert.ok(/tmux-window-button tabber-window-button[\s\S]*agent-icon claude[\s\S]*tmux-window-name-text[^>]*>0:claude</.test(claudeWindowRow?.nameHtml || '') && (claudeWindowRow?.nameHtml || '').includes('(pid=12345)'), 'Claude icon renders before the canonical window name with pid beside the shared button');
    assert.equal(/tmux-window-button tabber-window-button[\s\S]*tmux-window-name-text[^>]*>0:claude<[\s\S]*agent-icon claude/.test(claudeWindowRow?.nameHtml || ''), false, 'Claude icon no longer renders after the canonical window name');
    assert.equal(/agent-icon[\s\S]*tmux-window-name-text[^>]*>1:bash</.test(shellWindowRow?.nameHtml || ''), false, 'bash Tabber rows do not gain a leading agent icon');
    assert.ok(/agent-icon claude[^"]*agent-window-agent-icon--working[\s\S]*tmux-window-name-text[^>]*>0:claude</.test(claudeWindowRow?.nameHtml || ''), 'working agent glyph stays before the canonical window name');
    assert.ok(/status-indicator--dot[\s\S]*?status-indicator--working[\s\S]*?agent-window-agent-icon--working[\s\S]*?tmux-window-name-text[^>]*>0:claude</.test(claudeWindowRow?.nameHtml || ''), 'working Tabber rows render the green status ball before the static agent icon and canonical label');
    api.setFileExplorerTreeSortModeForTest('newest');
    api.setTabberActivityForTest({activity: {'1:1': {last_user_input_ts: 99999}, '1:0': {last_user_input_ts: 1}}});
    api.setTabberCollapsedForTest(['/s_1']);
    const sessionCollapsedRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.equal(sessionCollapsedRows.some(r => r.type === 'window' && /^0:claude/.test(r.name)), false, 'collapsed Tabber session rows hide their process rows');
    const collapsedSessionOne = sessionCollapsedRows.find(r => r.type === 'session' && r.title.split('\n')[0] === '1');
    assert.equal(collapsedSessionOne?.icon, '›', 'A3: collapsed session rows keep the shared disclosure affordance');
    assert.equal(collapsedSessionOne?.ariaExpanded, 'false', 'A3: collapsed session rows keep aria-expanded=false');
    assert.equal(api.tabberSessionForNumericKey('1'), '1', 'Tabber numeric key 1 maps to tmux session 1, not typeahead text');
    assert.equal(api.tabberSessionForNumericKey('0'), '', 'Tabber numeric keys are only visible 1-9 session shortcuts');
    assert.equal(api.openTabberSessionForTest('1'), true, 'opening a Tabber session succeeds');
    const firstLevelExpandedRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.ok(firstLevelExpandedRows.some(r => r.type === 'window' && /^0:claude/.test(r.name)), 'opening a collapsed Tabber session expands its process rows');
    assert.equal(firstLevelExpandedRows.find(r => r.type === 'session' && r.title.split('\n')[0] === '1')?.icon, '›', 'A3: expanded session rows keep the shared disclosure affordance');
    assert.equal(api.currentSessionActionTarget(), '1', 'opening Tabber session 1 focuses tab 1');
    api.setFocusedPanelItem('2');
    api.editorNav.stack = [];
    api.editorNav.index = -1;
    assert.equal(api.openTabberSessionForTest('1'), true, 'opening Tabber session 1 from another tab records a user navigation');
    assert.deepEqual(api.editorNav.stack, ['2', '1'], 'Tabber session open records previous tab then session 1 for Back');
    api.setFileExplorerModeForTest('tabber');
    const tabberClickSlots = api.emptyLayoutSlots();
    tabberClickSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
    tabberClickSlots.left = api.paneStateWithTabs(['1'], '1');
    tabberClickSlots.right = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(tabberClickSlots);
    api.setFocusedPanelItem('1');
    api.editorNav.stack = [];
    api.editorNav.index = -1;
    const tabberPanel = new TestElement('tabber-panel');
    const sessionTwoRow = new TestElement('tabber-session-2');
    sessionTwoRow.classList.add('file-tree-row');
    sessionTwoRow.dataset.kind = 'dir';
    sessionTwoRow.dataset.path = '/s_2';
    sessionTwoRow.dataset.tabberType = 'session';
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
    assert.ok(firstLevelExpandedRows.findIndex(r => /0:claude/.test(r.name)) < firstLevelExpandedRows.findIndex(r => /1:bash/.test(r.name)), 'Tabber tmux sub-window rows stay in tmux sub-window index order even when tree sort is newest');
    assert.ok(rows.some(r => r.type === 'repo' && r.repoRoot === '/home/u/proj' && r.name === '/home/u/proj'), 'L3: path rows render with absolute paths');
    assert.ok(rows.some(r => r.type === 'repo' && r.repoRoot === '/home/u/proj' && r.icon === '📁'), 'L3: path rows use a folder icon');
    assert.ok(rows.some(r => r.type === 'session' && r.branch === 'devbranch' && r.title.includes('branch: devbranch')), 'B2: the session branch remains available in row metadata/hover text');
    assert.ok(rows.some(r => r.type === 'repo' && r.repoRoot === '/home/u/proj' && r.branch === 'devbranch' && r.title.includes('branch: devbranch')), 'L3: repo path rows retain branch context without visible badge text');
    for (const row of rows.filter(r => r.type === 'session' || r.type === 'repo')) {
      assert.equal(row.status, '', `Tabber ${row.type} row must not render branch/status fragments before the date column`);
      assert.equal(row.statusHidden, true, `Tabber ${row.type} row hides the shared one-character git status badge`);
    }
    assert.equal(rows.some(r => r.type === 'path'), false, 'L3: individual file rows are not rendered');
    assert.equal(rows.flatMap(r => r.datasetKeys).some(key => /^tabberOpen/.test(key)), false, 'DOIT.61 A3: Tabber rows do not retain stale tabberOpen* dataset keys');
    api.setTabberSessionFilesLoadingForTest('1');
    const loadingRows = api.tabberRenderedRowsForTest({preserveCollapsed: true});
    assert.equal(loadingRows.some(r => r.type === 'loading' && /Fetching paths/.test(r.name)), false, 'L3: Tabber no longer has a frontend touched-path loading row');
    // Non-tmux tabs (Preferences / YO!info / file editors) render as leaf rows AFTER all the sessions.
    const rowTypes = rows.map(r => r.type);
    assert.ok(rowTypes.includes('tab'), 'non-tmux tabs appear in the Tabber');
    assert.ok(rowTypes.indexOf('tab') > rowTypes.lastIndexOf('session'), 'non-tmux tabs render after the sessions');

    // B4 recency sort: agent windows use the backend transcript timestamp. A fresh user-input heartbeat in
    // session 1's Claude window and a newer touched-path mtime under it must not make it outrank the newer
    // Codex transcript in session 2.
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
    const recency = api.tabberRenderedRowsForTest().map(r => r.name);
    const codexAt = recency.findIndex(n => /0:codex/.test(n));
    const claudeAt = recency.findIndex(n => /0:claude/.test(n));
    assert.ok(codexAt >= 0 && claudeAt >= 0 && codexAt < claudeAt, 'B4: the more-recently-used agent transcript sorts first (codex before claude)');
    api.setFileExplorerTreeDateModeForTest('date');
    api.setAutoApproveStateForTest('1', {});
    api.setTabberActivityForTest({
      activity: {'1:0': {last_user_input_ts: 999999}},
      agents: [{session: '1', window: '0', agent_kind: 'claude', last_used_ts: 1000, sort_ts: 9000, running: true, label: "session '1' 0:claude"}],
    });
    const runningClaude = api.tabberRenderedRowsForTest().find(r => r.type === 'window' && /0:claude/.test(r.name));
    assert.equal(runningClaude?.date, api.sessionFileTimeText(9000), 'B4: active agent windows display the same transcript timestamp used for row mtime, not running/status text');

    // B5 context-menu source guard + event path.
    assert.ok(/data-tabber-type="session"[\s\S]*data-tabber-type="window"[\s\S]*showTabContextMenu\(tabItem, event\.clientX, event\.clientY/.test(source), 'B5: right-click on a Tabber session/window row reuses the shared tab context menu');
    assert.ok(/data-tabber-type="repo"[\s\S]*?showFileTreeContextMenu\(row, abs,/.test(source), 'B5: right-click on an absolute path row reuses the shared file context menu');
    const contextPanel = new TestElement('tabber-context-panel');
    const contextSessionRow = new TestElement('tabber-context-session-row');
    contextSessionRow.classList.add('file-tree-row');
    contextSessionRow.dataset.tabberType = 'session';
    contextSessionRow.dataset.tabberSession = '1';
    const contextSessionTab = new TestElement('tabber-context-session-tab');
    contextSessionTab.classList.add('tabber-session-tab');
    contextSessionTab.classList.add('tmux-pane-tab-token');
    contextSessionTab.classList.add('tmux-pane-tab-token-action');
    contextSessionRow.appendChild(contextSessionTab);
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
    assert.ok(contextMenu?.children[0]?.innerHTML.includes('Pin Tab'), 'Tabber session context menu includes the shared Pin Tab action');
    assert.ok(Array.from(contextMenu?.children || []).some(child => String(child.textContent || '').includes("Rename tmux session '1'")), 'Tabber session context menu includes the shared rename action');
  });

  {
    // DOIT.57: drag-into-terminal suggestion registry (the transient 1..9 overlay's data layer).
    const api = loadYolomux();
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
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("boolSetting('uploads.suggestion_autorun', false)"), 'read-only shell autorun is Preference-gated');
    assert.ok(source.includes("storageSet(dropActionLastKey(category), actionId)"), 'chosen drop actions are remembered per category');
    assert.ok(source.includes("apiFetchJson('/api/drop-action/run'"), 'server-side actions use the /api/drop-action/run endpoint');
    assert.ok(source.includes("appendContextMenuButton(menu, t('contextmenu.copyImage')"), 'Finder/Differ image context menus expose localized Copy image');
    assert.ok(source.includes('function commandPaletteDropActionItems()'), 'command palette reuses the drop-action registry for active file actions');
    assert.ok(source.includes("preferenceSettingItem('uploads.custom_actions'") && source.includes("preferenceSettingItem('uploads.suggestion_autorun'") && source.includes("preferenceSettingItem('uploads.image_action_order'"), 'Uploads Preferences expose custom actions, image ordering, and autorun through the shared setting builder');
  }

  await testAsync('drop-action status reuses the localized display label', async () => {
    const api = loadYolomux('', ['1']);
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

module.exports = {runTabberSuite};

if (require.main === module) {
  runSuites([runTabberSuite]);
}

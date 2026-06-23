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
  runSuites,
  finishSuite,
} = require('./layout_test_helper');

async function runLayoutRestoreSuite() {
  test('t@1160', () => {
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
  });

  test('session-file lookback options are one shared source', () => {
    const api = loadYolomux('', ['1']);
    const expectedHours = [0.5, 1, 2, 4, 8, 12, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240, 264, 288, 312, 336];
    const localJson = value => JSON.parse(JSON.stringify(value));
    assert.equal(api.sessionFileLookbackDefaultHoursForTest, 24);
    assert.deepStrictEqual(localJson(api.sessionFileLookbackHourValuesForTest), expectedHours);
    assert.deepStrictEqual(localJson(api.sessionFileLookbackOptionsForTest().map(option => option.hours)), expectedHours);
    assert.deepStrictEqual(localJson(api.sessionFileLookbackOptionsForTest().map(option => option.label)), ['30 min', '1 hour', '2 hours', '4 hours', '8 hours', '12 hours', '1 day', '2 days', '3 days', '4 days', '5 days', '6 days', '7 days', '8 days', '9 days', '10 days', '11 days', '12 days', '13 days', '14 days']);
    assert.equal(api.sessionFileLookbackLabelForTest(0.5), '30 min');
    assert.equal(api.normalizeSessionFileLookbackHoursForTest('336'), 336);
    assert.equal(api.normalizeSessionFileLookbackHoursForTest('0.5'), 0.5);
    assert.equal(api.normalizeSessionFileLookbackHoursForTest('365'), 24);
    assert.equal(api.normalizeSessionFileLookbackHoursForTest('365', 336), 336);
  });

  test('YO!info lookback control uses shared options and refreshes activity summary', () => {
    const requests = [];
    const api = loadYolomux('', ['1']);
    const control = api.infoLookbackControlHtmlForTest();
    assert.ok(control.includes('data-info-lookback'), 'Info renders a lookback select');
    assert.ok(control.includes('>30 min</option>'), 'Info lookback includes the shared 30 minute label');
    assert.ok(control.includes('>14 days</option>'), 'Info lookback includes the shared 14 day label');
    assert.ok(/value="24" selected/.test(control), 'Info lookback defaults to 24 hours');
    api.setTranscriptSessionOrderForTest(['external', '1']);
    api.setTranscriptInfoForTest('external', {
      project: {
        git: {
          root: '/repo/external',
          branch: 'main',
          other_branches: {branches: [{name: 'main', current: true, updated_ts: 20, subject: 'external tmux session'}]},
        },
      },
    });
    assert.deepStrictEqual(canonical(api.infoBranchRows().map(row => row.session)), ['external'], 'YO!info branch rows can render sessions outside the initial tab list');
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({sessions: {}, global: {lines: []}, session_order: [], session_file_hours: 336}));
    });
    api.setInfoSessionFileLookbackHoursForTest(336);
    assert.equal(api.infoSessionFileLookbackHoursForTest(), 336);
    assert.ok(requests.some(url => {
      const parsed = new URL(url, 'http://localhost');
      return parsed.pathname === '/api/activity-summary'
        && parsed.searchParams.get('force') === '1'
        && parsed.searchParams.get('scope') === 'all'
        && parsed.searchParams.get('hours') === '336';
    }), 'changing Info lookback refreshes all-session activity summary with selected hours');
  });

  test('t@1174', () => {
    const api = loadYolomux('', ['1', '2', '3']);
    const layout = api.defaultLayoutForTest();
    assert.deepStrictEqual(canonical(layout), {
      tree: {split: 'row', pct: 22, children: [{slot: 'slot1'}, {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'right'}]}]},
      panes: {
        slot1: {tabs: ['__files__'], active: '__files__'},
        left: {tabs: ['1', '2'], active: '1'},
        right: {tabs: ['3'], active: '3'},
      },
    });
    const url = api.syncInitialLayoutUrlForTest();
    const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
    assert.equal(params.get('sessions'), 'files,1,3');
    assert.equal(params.get('layout'), 'row@22(slot1,row@50(left,right))');
    assert.equal(params.get('tabs'), 'slot1:files;left:1,2;right:3');
  });

  test('t@1191', () => {
    const api = loadYolomux('', []);
    const layout = api.defaultLayoutForTest();
    assert.deepStrictEqual(canonical(layout), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: [], active: null, placeholder: true},
      },
    });
    const url = api.syncInitialLayoutUrlForTest();
    const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
    assert.equal(params.get('layout'), 'row@22(left,slot1)');
    assert.equal(params.get('tabs'), 'left:files;slot1:__empty_pane__');
  });

  test('t@1201', () => {
    const api = loadYolomux('?sessions=3,2,1', ['1', '2', '3']);
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'slot1'}, {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'right'}]}]},
      panes: {
        slot1: {tabs: ['__files__'], active: '__files__'},
        left: {tabs: ['3', '2'], active: '3'},
        right: {tabs: ['1'], active: '1'},
      },
    });
  });

  test('t@1207', () => {
    const api = loadYolomux('', ['6', '7'], 'https:', 'Linux x86_64', 'readonly', {
      share: {
        view: true,
        id: 'share123',
        sessions: ['6', '7'],
        session: '6',
        mode: 'ro',
        layout: 'row@30(slot1,left)',
        tabs: 'slot1:files;left:6,7*',
        finder: {root: '/home/test/yolomux.dev1', rootMode: 'fixed', mode: 'tabber', session: '7'},
      },
    });
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 30, children: [{slot: 'slot1'}, {slot: 'left'}]},
      panes: {
        slot1: {tabs: ['__files__'], active: '__files__'},
        left: {tabs: ['6', '7'], active: '7'},
      },
    });
    assert.equal(api.fileExplorerRootForTest(), '/home/test/yolomux.dev1');
    assert.equal(api.fileExplorerRootModeValue(), 'fixed');
    assert.equal(api.fileExplorerModeForTest(), 'tabber');
    assert.equal(api.fileExplorerRootForOpen('6'), '/home/test/yolomux.dev1');
  });

  test('t@1212', () => {
    const api = loadYolomux('?keep=1');
    assert.equal(api.layoutFromParam('', ''), null, 'empty layout param falls back to default layout');
    assert.equal(api.layoutFromParam(null, ''), null, 'null layout param falls back to default layout');
    assert.equal(api.layoutFromParam('not-a-layout', ''), null, 'garbage layout param falls back to default layout');
    api.rememberFileExplorerOpenIntentForTest(false);
    const slots = nestedSlots(api);
    const url = api.setLayoutSlotsForTest(slots);
    const params = parseUrl(url);
    assert.equal(params.get('sessions'), '6,1,3');
    assert.equal(params.get('layout'), 'row@37.5(left,col@62.5(slot1,slot2))');
    assert.equal(params.get('tabs'), 'left:5,6*;slot1:1;slot2:3');
    assert.equal(params.get('keep'), '1');

    const decoded = api.layoutFromParam(params.get('layout'), params.get('tabs'));
    assert.deepStrictEqual(canonical(api.serialize(decoded)), canonical(api.serialize(slots)));

    const duplicateAcrossSlots = api.layoutFromParam('row@0(left,slot1)', 'left:1;slot1:1,2*');
    assert.deepStrictEqual(canonical(api.serialize(duplicateAcrossSlots)), {
      tree: {split: 'row', pct: 5, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['1'], active: '1'},
        slot1: {tabs: ['2'], active: '2'},
      },
    }, 'layout parser dedupes duplicate sessions across slots and clamps low split percentages');
    const highPctLayout = api.layoutFromParam('row@120(left,slot1)', 'left:1;slot1:2');
    assert.equal(api.serialize(highPctLayout).tree.pct, 95, 'layout parser clamps high split percentages');

    const reloaded = loadYolomuxWithFileExplorerClosed(`?${url.split('?')[1] || ''}`);
    assert.deepStrictEqual(canonical(reloaded.serialize(reloaded.currentSlots())), canonical(api.serialize(slots)));
  });

  test('t@1243', () => {
    const api = loadYolomuxWithFileExplorerClosed();
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
    const reloaded = loadYolomuxWithFileExplorerClosed(encodedOldSearch);
    assert.deepStrictEqual(canonical(reloaded.serialize(reloaded.currentSlots())), canonical(api.serialize(decoded)));

    const oldFourPanePayload = {
      tree: {
        split: 'row',
        children: [
          {split: 'column', children: [{slot: 'leftTop'}, {slot: 'leftBottom'}]},
          {split: 'column', children: [{slot: 'rightTop'}, {slot: 'rightBottom'}]},
        ],
      },
      slots: {
        leftTop: {tabs: ['4'], active: '4'},
        leftBottom: {tabs: ['1'], active: '1'},
        rightTop: {tabs: ['changes'], active: 'changes'},
        rightBottom: {tabs: ['2', '3'], active: '3'},
      },
    };
    const fourPane = api.layoutFromParam(`tree:${JSON.stringify(oldFourPanePayload)}`, '');
    assert.deepStrictEqual(canonical(api.serialize(fourPane)), {
      tree: {
        split: 'row',
        pct: 22,
        children: [
          {slot: 'rightTop'},
          {
            split: 'row',
            pct: 50,
            children: [
              {split: 'column', pct: 50, children: [{slot: 'leftTop'}, {slot: 'leftBottom'}]},
              {slot: 'rightBottom'},
            ],
          },
        ],
      },
      panes: {
        leftBottom: {tabs: ['1'], active: '1'},
        leftTop: {tabs: ['4'], active: '4'},
        rightBottom: {tabs: ['2', '3'], active: '3'},
        rightTop: {tabs: ['__files__'], active: '__files__'},
      },
    });
    assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes inside an old four-pane URL still selects Finder diff mode');
  });

  test('t@1270', () => {
    const api = loadYolomuxWithFileExplorerClosed('?sessions=3&layout=left&tabs=left:3,2');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {slot: 'left'},
      panes: {
        left: {tabs: ['3', '2'], active: '3'},
      },
    });
  });

  test('t@1280', () => {
  for (const legacyChangesToken of ['changes', '__changes__']) {
    const api = loadYolomux(`?layout=left&tabs=left:${legacyChangesToken}`, ['1']);
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: [], active: null, placeholder: true},
      },
    });
    assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes-only URLs restore Finder diff mode');
  }
  });

  test('t@1289', () => {
  for (const yoagentToken of ['yoagent', '__yoagent__', '__yosup__']) {
    const api = loadYolomuxWithFileExplorerClosed(`?sessions=${yoagentToken}&layout=left&tabs=left:${yoagentToken}`, ['1']);
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {slot: 'left'},
      panes: {left: {tabs: ['__info__'], active: '__info__'}},
    });
    assert.equal(api.infoPanelSubTabForTest(), 'yoagent', `${yoagentToken} deep-link pre-selects the YO!agent sub-tab`);
  }
  });

  test('t@1298', () => {
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
  });

  test('t@1313', () => {
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

    const terminalToolbarBeforeFinderFocus = api.panelControlsHtml('6');
    api.setFocusedPanelItem('__files__');
    api.activatePaneTab('slot2', '__files__');
    assert.equal(api.panelControlsHtml('6'), terminalToolbarBeforeFinderFocus);
    assert.ok(terminalToolbarBeforeFinderFocus.includes('data-tab-name="terminal"'));
    assert.equal((terminalToolbarBeforeFinderFocus.match(/pane-actions-dots/g) || []).length, 1);
    assert.equal(terminalToolbarBeforeFinderFocus.includes('data-tab-name="summary"'), false);
    const agentInfo = {agents: [{kind: 'codex', transcript: '/tmp/codex.jsonl'}], selected_pane: {process_label: 'codex'}};
    api.setTranscriptInfoForTest('6', agentInfo);
    assert.equal(api.terminalTabLabel('6', agentInfo), 'Term', 'terminal tab visible label is static');
    assert.equal(api.terminalTabTitle('6', agentInfo), 'terminal: codex', 'terminal tab title keeps the active process detail');
    const agentToolbar = api.panelControlsHtml('6');
    assert.ok(agentToolbar.includes('>Term</button>'), 'terminal top row shows the generic terminal label');
    assert.equal(agentToolbar.includes('>codex</button>') || agentToolbar.includes('>Codex</button>'), false, 'terminal top row does not render the agent marker as the terminal tab text');
    const sessionSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(sessionSource.includes('panel-agent-slot'), false, 'DOIT.57 T1: the agent badge is removed from the Info Bar (the window buttons carry the agent fact)');
    assert.equal(/function sessionAgentBadgeHtml/.test(sessionSource), false, 'DOIT.57 T1: the unused agent-badge helper is gone');
    assert.ok(/function terminalTabDisplayLabel\(session, info\)\s*\{\s*return 'Term';\s*\}/.test(sessionSource), 'DOIT.56 N3: terminal tab visible label is always static');
    assert.ok(/function terminalTabTitle\(session, info\)[\s\S]*terminalTabDetailLabel\(session, info\)/.test(sessionSource), 'DOIT.56 N3: terminal tab title still uses process/window detail');
  });

  test('t@1367', () => {
    // Dockview round-trip parity: slots -> Dockview JSON -> slots is idempotent on the compacted
    // form. The pane rewrite's bidirectional sync hinges on dockviewJsonFromLayoutSlots and
    // layoutSlotsFromDockviewJson being exact inverses (up to compaction); any drift silently
    // reshuffles panes/tabs on a Dockview-driven relayout, and is the single most fragile invariant
    // in the rewrite. See docs/specs/GUI.md (pane layout model).
    const api = loadYolomux('', ['1', '2', '3', '4']);
    const base = api.emptyLayoutSlots();
    base[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    base.left = api.paneStateWithTabs(['__files__'], '__files__');
    base.slot1 = api.paneStateWithTabs(['1', '2', '3'], '2');
    // Binary-tree shapes round-trip EXACTLY (tree + panes). For every layout shape, the pane->tabs
    // assignment is the invariant that must hold.
    const binaryTreeModes = new Set(['single', 'split', 'grid']);
    for (const mode of ['single', 'split', 'grid']) {
      api.setLayoutSlotsForTest(base);
      api.setFocusedPanelItem('2');
      api.applyLayoutMode(mode);
      const slots = api.currentSlots();
      const compacted = api.serialize(api.compactLayoutSlots(slots));
      const roundTripped = api.serialize(api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(slots)));
      assert.deepStrictEqual(
        canonical(roundTripped.panes),
        canonical(compacted.panes),
        `slots -> dockview JSON -> slots preserves every pane's tabs + active tab (${mode} layout)`
      );
      if (binaryTreeModes.has(mode)) {
        assert.deepStrictEqual(
          canonical(roundTripped.tree),
          canonical(compacted.tree),
          `binary-tree layout round-trips the split tree exactly (${mode} layout)`
        );
        assert.equal(
          api.layoutSlotsSignature(api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(slots))),
          api.layoutSlotsSignature(api.compactLayoutSlots(slots)),
          `binary-tree layout round-trips the full normalized signature (${mode} layout)`
        );
      }
    }
  });

  test('t@1410', () => {
    const api = loadYolomux('', ['1', '2']);
    const item = 'file:/home/keivenc/review.json';
    const paneTab = api.fileEditorPaneTabHtml(item);
    assert.ok(paneTab.includes('review.json'));
    assert.equal(paneTab.includes('agent-icon file'), false);
    api.setOpenFileOwner('/home/keivenc/review.json', item, {ownerSession: '1'});
    assert.ok(api.fileEditorPaneTabHtml(item).includes('file-tab-owner'), 'file tabs show owning session when known');
    assert.ok(api.fileEditorPaneTabHtml(item).includes('>1</span>'), 'single owning session is shown in file tab');
    api.setOpenFileOwner('/home/keivenc/review.json', item, {ownerSession: '2'});
    assert.ok(api.fileEditorPaneTabHtml(item).includes('>multi</span>'), 'multi-session file tabs distinguish duplicate names');
    assert.equal(api.TAB_TYPES.some(type => type.key === 'file-preview'), false, 'side-preview file tabs are removed; preview uses the pop-out window');
    const duplicateParents = api.fileTabParentDisambiguators([
      api.fileEditorItemFor('/repo/app/src/config.json'),
      api.fileEditorItemFor('/repo/lib/src/config.json'),
      api.fileEditorItemFor('/repo/app/README.md'),
    ]);
    assert.deepStrictEqual([...duplicateParents.entries()].map(entry => entry.join('=')).sort(), [
      '/repo/app/src/config.json=app/src',
      '/repo/lib/src/config.json=lib/src',
    ], 'duplicate file tabs use the shortest unique parent suffix');
    const displayContext = api.paneTabDisplayContext([
      api.fileEditorItemFor('/repo/app/src/config.json'),
      api.fileEditorItemFor('/repo/lib/src/config.json'),
    ]);
    assert.equal(displayContext.fileParentLabels.get('/repo/app/src/config.json'), 'app/src');
    assert.ok(api.fileEditorPaneTabHtml(api.fileEditorItemFor('/repo/app/src/config.json'), {parentLabel: 'app/src'}).includes('file-tab-parent'), 'duplicate file tabs render the parent suffix in a muted slot');
    const loadingState = api.ensureFileTabStateForItem(api.fileEditorItemFor('/repo/app/src/pending.txt'));
    assert.equal(loadingState.kind, 'file', 'path-backed tabs use a file placeholder type, not literal "loading"');
    assert.equal(loadingState.loading, true, 'path-backed tabs still expose loading status until the file is fetched');
    const paneTabSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/bindDelayedSessionPopover\(tab, popover[\s\S]*maybeLoadFileTabForPopover\(tab, session\)/.test(paneTabSource), 'file tab popovers load stale/missing file state through the shared hover popover onOpen hook');
    assert.ok(/function loadFileEditorState[\s\S]*if \(panel\) renderFileEditorPanel\(panel, item\)/.test(paneTabSource), 'inactive file-tab hover loads can fetch without a mounted editor panel');

    assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-heading-1'));
    assert.ok(api.markdownSyntaxHtml('# TITLE\n**bold**').includes('md-bold'));
    const anchoredMarkdown = api.markdownTextWithSourceAnchors('| A | B |\n|---|---|\n| 1 | 2 |\n\n---');
    assert.equal(anchoredMarkdown.includes('markdown-source-anchor'), false, 'Markdown source is not mutated before marked parses GFM tables and rules');
    assert.ok(anchoredMarkdown.includes('|---|---|'), 'GFM table delimiter rows stay intact before parsing');
    assert.ok(anchoredMarkdown.includes('\n---'), 'thematic breaks stay intact before parsing');
    assert.deepStrictEqual(canonical(api.markdownTaskLineEntries('- [ ] Open\n- [x] Done\ntext')), [
      {line: 1, checked: false},
      {line: 2, checked: true},
    ], 'Markdown task lines are detected in source order for Preview checkbox binding');
    assert.equal(api.markdownTextWithTaskLineToggled('- [ ] Open\n- [x] Done', 1, true), '- [x] Open\n- [x] Done', 'Preview can toggle an unchecked task line to checked source');
    assert.equal(api.markdownTextWithTaskLineToggled('- [ ] Open\n- [x] Done', 2, false), '- [ ] Open\n- [ ] Done', 'Preview can toggle a checked task line to unchecked source');
    assert.equal(api.markdownTextWithTaskLineToggled('plain text', 1, true), null, 'Preview task toggles reject non-task source lines');
    assert.equal(api.previewKindForPath('/repo/README.md'), 'markdown', 'Markdown paths route to Markdown Preview');
    assert.equal(api.previewKindForPath('/repo/page.html'), 'html', 'HTML paths route to sandboxed HTML Preview');
    assert.equal(api.previewKindForPath('/repo/diagram.svg'), 'image', 'SVG paths route to image Preview, not trusted inline DOM');
    assert.equal(api.previewKindForPath('/repo/animation.apng'), 'image', 'APNG paths route to browser image Preview');
    assert.equal(api.previewKindForPath('/repo/photo.avif'), 'image', 'AVIF paths are image Preview candidates');
    assert.equal(api.previewKindForPath('/repo/spec.pdf'), 'pdf', 'PDF paths route to raw PDF Preview');
    assert.equal(api.previewKindForPath('/repo/chart.mmd'), 'mermaid', 'Mermaid source files route to Mermaid Preview');
    assert.equal(api.previewKindForPath('/repo/config.json'), 'structured', 'JSON paths route to structured Preview');
    assert.equal(api.previewKindForPath('/repo/events.jsonl'), 'structured', 'JSONL paths route to structured Preview');
    assert.equal(api.previewKindForPath('/repo/map.geojson'), 'structured', 'GeoJSON paths route to structured Preview');
    assert.equal(api.previewKindForPath('/repo/notebook.ipynb'), 'structured', 'notebooks route to safe structured Preview');
    assert.equal(api.previewKindForPath('/repo/diagram.drawio'), 'structured', 'Draw.io XML routes to structured Preview');
    assert.equal(api.previewKindForPath('/repo/.env'), 'structured', 'env/config files route to bounded config Preview');
    assert.equal(api.previewKindForPath('/repo/config.yaml'), 'structured', 'YAML paths route to structured Preview');
    assert.equal(api.previewKindForPath('/repo/config.toml'), 'structured', 'TOML paths route to structured Preview');
    assert.equal(api.previewKindForPath('/repo/table.csv'), 'table', 'CSV paths route to table Preview');
    assert.equal(api.previewKindForPath('/repo/table.tsv'), 'table', 'TSV paths route to table Preview');
    assert.equal(api.previewKindForPath('/repo/sound.mp3'), 'audio', 'audio paths route to native audio Preview');
    assert.equal(api.previewKindForPath('/repo/movie.mp4'), 'video', 'video paths route to native video Preview');
    assert.equal(api.previewKindForPath('/repo/photo.tiff'), 'unsupported', 'TIFF is recognized as unsupported-in-browser fallback');
    assert.equal(api.previewKindForPath('/repo/photo.heic'), 'unsupported', 'HEIC is recognized as unsupported-in-browser fallback');
    assert.equal(api.previewKindForPath('/repo/book.xlsx'), 'unsupported', 'Office files are recognized fallback, not fake text');
    assert.equal(api.previewKindForPath('/repo/data.parquet'), 'unsupported', 'Parquet is recognized fallback, not fake text');
    assert.equal(api.previewKindForPath('/repo/archive.zip'), 'unsupported', 'archives are recognized fallback, not unpacked');
    assert.equal(api.previewKindForPath('/repo/app.py'), 'text', 'known code files get the code-preview fallback');
    assert.equal(api.previewRendererForPath('/repo/config.json').id, 'structured', 'preview dispatch comes from the shared renderer registry');
    assert.equal(api.previewPathIsPreviewable('/repo/app.py'), false, 'generic code/text renderer is not a distinct Preview affordance');
    assert.equal(api.previewPathIsPreviewable('/repo/notes.txt'), false, 'plain text renderer is not a distinct Preview affordance');
    assert.equal(api.previewPathIsPreviewable('/repo/config.json'), true, 'structured JSON preview stays available because it differs from the editor');
    assert.equal(api.previewRendererForPath('/repo/notes.txt').previewable, false, 'text preview availability is owned by the renderer registry flag');
    assert.equal(api.previewRendererForPath('/repo/photo.tiff').id, 'unsupported-image', 'recognized image fallbacks are registry-owned');
    assert.equal(api.previewRendererForPath('/repo/archive.zip').id, 'unsupported-archive', 'recognized archive fallbacks are registry-owned');
    const previewRendererSamples = {
      'docs/preview-samples/10-markdown.md': 'markdown',
      'docs/preview-samples/11-html.html': 'html',
      'docs/preview-samples/12-image.svg': 'image',
      'docs/preview-samples/14-mermaid.mmd': 'mermaid',
      'docs/preview-samples/15-structured.json': 'structured',
      'docs/preview-samples/16-structured.jsonl': 'structured',
      'docs/preview-samples/17-notebook.ipynb': 'structured',
      'docs/preview-samples/18-structured.yaml': 'structured',
      'docs/preview-samples/19-structured.toml': 'structured',
      'docs/preview-samples/20-structured.drawio': 'structured',
      'docs/preview-samples/21-config.properties': 'structured',
      'docs/preview-samples/22-table.csv': 'table',
      'docs/preview-samples/23-table.tsv': 'table',
    };
    for (const [samplePath, rendererId] of Object.entries(previewRendererSamples)) {
      assert.equal(fs.existsSync(samplePath), true, `${rendererId} renderer has a docs/preview-samples fixture`);
      assert.equal(api.previewRendererForPath(`/repo/${samplePath}`).id, rendererId, `${samplePath} routes to ${rendererId}`);
    }
    [
      'docs/preview-samples/13-pdf.pdf',
      'docs/preview-samples/24-audio.wav',
      'docs/preview-samples/25-video.mp4',
      'docs/preview-samples/26-text.log',
      'docs/preview-samples/27-diff.patch',
      'docs/preview-samples/28-diagram.dot',
      'docs/preview-samples/29-plantuml.puml',
      'docs/preview-samples/30-unsupported-image.tiff',
      'docs/preview-samples/31-unsupported-document.xlsx',
      'docs/preview-samples/32-unsupported-data.parquet',
      'docs/preview-samples/33-unsupported-archive.zip',
      'docs/preview-samples/34-unsupported.unknown',
    ].forEach(samplePath => {
      assert.equal(fs.existsSync(samplePath), false, `${samplePath} stays out of the curated preview sample set`);
    });
    assert.equal(api.previewMediaKindForPath('/repo/a.png'), 'image', 'image media kind is shared');
    assert.equal(api.previewMediaKindForPath('/repo/a.pdf'), 'pdf', 'PDF media kind is shared');
    assert.equal(api.previewMediaKindForPath('/repo/a.mp3'), 'audio', 'audio media kind is shared');
    assert.equal(api.previewMimeForPath('/repo/a.apng'), 'image/apng', 'APNG MIME is known to the preview dispatcher');
    assert.equal(api.previewMimeForPath('/repo/a.svg'), 'image/svg+xml', 'SVG MIME is known to the preview dispatcher');
    assert.equal(api.previewMimeForPath('/repo/a.pdf'), 'application/pdf', 'PDF MIME is known to the preview dispatcher');
    assert.equal(api.previewMimeForPath('/repo/a.mp4'), 'video/mp4', 'video MIME is known to the preview dispatcher');
    assert.equal(api.previewMimeForPath('/repo/a.heic'), 'image/heic', 'HEIC MIME is known even though preview falls back');
    assert.equal(api.previewMimeForPath('/repo/a.xlsx'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'Office MIME is known for fallback display');
    assert.ok(api.markdownPreviewHtml('# Offline\n\n![local](./a.png)\n\n```js\nconst x = 1;\n```').includes('<h1>Offline</h1>'), 'Markdown Preview has a local parser fallback when marked is unavailable');
    assert.equal(/IMAGE_EXTENSIONS\.has|PDF_EXTENSIONS\.has|MERMAID_EXTENSIONS\.has/.test(fs.readFileSync('static/yolomux.js', 'utf8')), false, 'preview routing call sites use shared registry helpers instead of direct extension-set checks');
    assert.ok(/function sniffedRawPreviewFileState[\s\S]*rawPreviewFileStateFromMime/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'binary text-read failures recover through sniffed MIME and the shared renderer registry');
    assert.equal(api.markdownPreviewImageTarget('./asset dir/a%20b.png?cache=1#frag', '/repo/docs/README.md').src, '/api/fs/raw?path=%2Frepo%2Fdocs%2Fasset%20dir%2Fa%20b.png', 'relative Markdown image URLs resolve against the Markdown file and strip query/fragment from the filesystem path');
    assert.ok(api.markdownPreviewHtml('![plot](./asset(dir)/a(1).png "Plot title")').includes('src="./asset(dir)/a(1).png"'), 'offline Markdown fallback preserves image URLs with parentheses');
    assert.ok(api.markdownPreviewHtml('[doc](./notes(2026).md "Doc title")').includes('href="./notes(2026).md"'), 'offline Markdown fallback preserves link URLs with parentheses');
    assert.equal(api.markdownPreviewImageTarget('../img.svg', '/repo/docs/README.md').path, '/repo/img.svg', 'parent-relative Markdown images normalize before raw-file routing');
    assert.equal(api.markdownPreviewImageTarget('/repo/logo.gif', '/repo/docs/README.md').src, '/api/fs/raw?path=%2Frepo%2Flogo.gif', 'absolute local Markdown images route through raw-file serving');
    assert.equal(api.markdownPreviewImageTarget('https://example.test/image.png', '/repo/docs/README.md').external, true, 'safe external Markdown image URLs are not rewritten');
    assert.equal(api.markdownPreviewImageTarget('//example.test/image.png', '/repo/docs/README.md'), null, 'protocol-relative Markdown images stay blocked');
    assert.equal(api.markdownPreviewImageTarget('data:image/svg+xml,<svg></svg>', '/repo/docs/README.md'), null, 'SVG data image URLs stay blocked');
    assert.equal(api.markdownPreviewImageTarget('data:image/png;base64,abc', '/repo/docs/README.md').external, true, 'safe raster data images remain external data URLs');
    assert.equal(api.markdownPreviewImageTarget('javascript:alert(1)', '/repo/docs/README.md'), null, 'unsafe Markdown image URLs stay blocked');
    assert.equal(api.isMermaidFenceLanguage('mermaid'), true, 'Mermaid fences are detected');
    assert.equal(api.isMermaidFenceLanguage('mmd'), true, 'mmd fences are detected');
    assert.equal(api.isMermaidFenceLanguage('javascript'), false, 'non-Mermaid fences stay code preview');
    const unsafeSvg = '<svg onclick="evil()"><script>alert(1)</script><foreignObject>x</foreignObject><image href="https://evil.test/a.png"/><a href="#local">ok</a><style>@import url(https://evil.test/x.css); .a { fill: red; }</style></svg>';
    const sanitizedSvg = api.sanitizeStandaloneSvg(unsafeSvg);
    assert.equal(/<script|foreignObject|onclick=|evil\.test|@import|url\(/i.test(sanitizedSvg), false, 'Mermaid SVG sanitizer removes scripts, event handlers, external references, and stylesheet imports');
    assert.equal(sanitizedSvg.includes('href="#local"'), true, 'Mermaid SVG sanitizer keeps local fragment references');
    assert.ok(/function mermaidPreviewConfig[\s\S]*htmlLabels:\s*true[\s\S]*flowchart:\s*\{[\s\S]*htmlLabels:\s*true/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Mermaid HTML labels are allowed only as sanitizer input');
    assert.ok(/function svgForeignObjectTextNode[\s\S]*createElementNS\('http:\/\/www\.w3\.org\/2000\/svg', 'text'\)[\s\S]*node\.textContent = text/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Mermaid foreignObject labels convert to safe SVG text in browser runtime');
    assert.ok(/tagName === 'foreignobject'[\s\S]*svgForeignObjectTextNode\(child\)[\s\S]*child\.replaceWith\(textNode\)/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Mermaid foreignObject labels are converted before foreignObject stripping');
    assert.equal(api.markdownPreviewBlockedTagsForTest().includes('input'), false, 'Markdown sanitizer preserves checkbox inputs for task-list Preview controls');
    assert.ok(/bindMarkdownTaskCheckboxes\(container, text, markdownPath\)/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Markdown Preview wires rendered task checkboxes after parsing');
    assert.ok(/tagName === 'input'[\s\S]*getAttribute\('type'\)[\s\S]*checkbox/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'Markdown sanitizer removes non-checkbox inputs while allowing task checkboxes');
    const editorCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(editorCss.includes('.markdown-body th { background: var(--panel2); }'), 'Markdown table headers get a readable preview background');
    assert.ok(editorCss.includes('.markdown-body hr { border: 0; border-top: 1px solid var(--line); margin: 12px 0; }'), 'Markdown thematic breaks render as preview rules');
    assert.ok(editorCss.includes('.markdown-body li.task-list-item > input[type="checkbox"]'), 'Markdown Preview task checkboxes have visible interactive styling');
    assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body[\s\S]*background:\s*#ffffff[\s\S]*color:\s*var\(--lt-text\)/.test(editorCss), 'vanilla preview uses a neutral white email-friendly surface');
    assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body h1[\s\S]*color:\s*var\(--lt-text\)[\s\S]*background:\s*transparent/.test(editorCss), 'vanilla preview headings do not use YOLOmux accent coloring');
    assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body a[\s\S]*color:\s*#0645ad/.test(editorCss), 'vanilla preview links use a conventional blue instead of scheme colors');
    assert.ok(/\.file-editor-preview-pane(?:-panel)?\.vanilla-preview-body pre code \*[\s\S]*color:\s*inherit !important/.test(editorCss), 'vanilla preview strips syntax token colors inside code blocks');
    assert.ok(/\.markdown-body pre code \.hljs-keyword,[\s\S]*color:\s*var\(--code-keyword\) !important/.test(editorCss), 'themed markdown preview owns Highlight.js keyword color instead of relying on the external stylesheet');
    assert.ok(/\.markdown-body pre code \.hljs-string,[\s\S]*color:\s*var\(--code-string\) !important/.test(editorCss), 'themed markdown preview owns Highlight.js string color');
    const fileExplorerSource = (fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8'));
    const highlightStart = fileExplorerSource.indexOf('function codeMirrorHighlightExtension');
    const highlightEnd = fileExplorerSource.indexOf('function codeMirrorThemeExtension');
    const highlightBlock = fileExplorerSource.slice(highlightStart, highlightEnd);
    assert.ok(highlightBlock.includes("keyword: 'var(--code-keyword)'"), 'CodeMirror keywords use the same parent syntax variable as Preview');
    assert.ok(highlightBlock.includes("control: 'var(--code-control)'"), 'CodeMirror control keywords use the same parent syntax variable as Preview');
    assert.ok(highlightBlock.includes("string: 'var(--code-string)'"), 'CodeMirror strings use the same parent syntax variable as Preview');
    assert.ok(highlightBlock.includes("function: 'var(--code-function)'"), 'CodeMirror functions use the same parent syntax variable as Preview');
    assert.equal(highlightBlock.includes('scheme.syntax'), false, 'CodeMirror highlighting does not duplicate literal syntax colors outside the shared CSS variables');
    assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-comment'));
    assert.ok(api.simpleCodeSyntaxHtml('bash', '# comment\necho $HOME').includes('code-variable'));
    assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-attr'));
    assert.ok(api.simpleCodeSyntaxHtml('json', '{"name": "yolomux", "ok": true}').includes('code-constant'));
    assert.ok(api.simpleCodeSyntaxHtml('python', 'class ToolParser:\n    def extract_tool_calls(self, model_output: str) -> DeltaMessage | None:').includes('<span class="code-type">ToolParser</span>'), 'Python class declarations use the shared type token');
    assert.ok(api.simpleCodeSyntaxHtml('python', 'class ToolParser:\n    def extract_tool_calls(self, model_output: str) -> DeltaMessage | None:').includes('<span class="code-type">str</span>'), 'Python annotations use the shared type token');
    assert.ok(api.simpleCodeSyntaxHtml('rust', 'pub struct Tool { pub name: String, }').includes('<span class="code-control">pub</span>'), 'Rust pub uses the shared control token');
    assert.ok(api.simpleCodeSyntaxHtml('rust', 'pub struct Tool { pub name: String, }').includes('<span class="code-type">String</span>'), 'Rust field types use the shared type token');

    assert.deepStrictEqual(Array.from(api.editorVisualLineFragments('abcdefghijkl', 5, true)), ['abcde', 'fghij', 'kl']);
    assert.deepStrictEqual(Array.from(api.editorVisualLineFragments('abcdefghijkl', 5, false)), ['abcdefghijkl']);
    const gutterHtml = api.editorVisualHighlightHtml('bash', 'echo 1\nabcdefghijkl', {wrap: true, lineNumbers: true, columnCount: 5});
    assert.ok(gutterHtml.includes('editor-line-number">1</span'));
    assert.ok(gutterHtml.includes('editor-line-number">2</span'));
    assert.ok(gutterHtml.includes('editor-soft-wrap-marker">↪</span'));
    assert.ok(gutterHtml.includes('code-number'));
    const semanticRanges = [];
    const fakeApi = {
      Decoration: {
        mark(options) {
          return {
            range(from, to) {
              return {from, to, style: options.attributes.style};
            },
          };
        },
        set(ranges) {
          semanticRanges.push(...ranges);
          return ranges;
        },
      },
      ViewPlugin: {
        fromClass(Plugin) {
          const text = '<p><strong>bold</strong> <em>em</em></p>';
          const view = {
            visibleRanges: [{from: 0, to: text.length}],
            state: {
              doc: {
                length: text.length,
                sliceString(from, to) {
                  return text.slice(from, to);
                },
              },
            },
          };
          return new Plugin(view).decorations;
        },
      },
    };
    api.codeMirrorHtmlSemanticEmphasisExtension(fakeApi, '/home/test/index.html');
    assert.deepStrictEqual(semanticRanges.map(range => [range.from, range.to]), [[11, 15], [29, 31]]);
    assert.ok(semanticRanges[0].style.includes('font-weight:700'));
    assert.ok(semanticRanges[1].style.includes('font-style:italic'));

    const brokenLanguageApi = {
      javascript() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
      markdown() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
      LanguageDescription: {
        of() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
      },
      StreamLanguage: {
        define() { throw new TypeError("Cannot read properties of undefined (reading 'parser')"); },
      },
    };
    assert.deepStrictEqual(canonical(api.codeMirrorLanguageExtension(brokenLanguageApi, '/home/test/app.js')), [], 'broken JS language support falls back to editable plain text');
    assert.deepStrictEqual(canonical(api.codeMirrorLanguageExtension(brokenLanguageApi, '/home/test/README.md')), [], 'broken Markdown language support falls back to editable plain text');
    assert.deepStrictEqual(canonical(api.codeMirrorMarkdownCodeLanguages(brokenLanguageApi)), [], 'broken fenced-code language descriptions are skipped');
    assert.deepStrictEqual(canonical(api.codeMirrorHighlightExtension({})), [], 'missing highlight support falls back without crashing');
    assert.equal(api.codeMirrorApiIsUsable({Compartment: class {}, EditorState: {create() {}, readOnly: {of() {}}}, EditorView: {theme() {}, editable: {of() {}}, contentAttributes: {of() {}}}, keymap: {of() {}}, drawSelection() {}, highlightActiveLine() {}, search() {}, openSearchPanel() {}}), true, 'CodeMirror API validation accepts critical editor/search exports');
    assert.equal(api.codeMirrorApiIsUsable({Compartment: class {}, EditorState: {create() {}, readOnly: {of() {}}}, EditorView: {theme() {}, editable: {of() {}}}, keymap: {of() {}}, drawSelection() {}, highlightActiveLine() {}, search() {}, openSearchPanel() {}}), false, 'CodeMirror API validation rejects bundles without line-wrapping support');
    assert.equal(api.codeMirrorApiIsUsable({EditorState: {create() {}}, EditorView: {theme() {}}}), false, 'CodeMirror API validation rejects partial bundles');
    const codeMirrorBundlePackage = JSON.parse(fs.readFileSync('prototypes/codemirror-bundle/package.json', 'utf8'));
    const codeMirrorBundleLock = JSON.parse(fs.readFileSync('prototypes/codemirror-bundle/package-lock.json', 'utf8'));
    const codeMirrorDirectVersions = {
      '@codemirror/commands': '6.10.3',
      '@codemirror/lang-css': '6.3.1',
      '@codemirror/lang-html': '6.4.11',
      '@codemirror/lang-javascript': '6.2.5',
      '@codemirror/lang-json': '6.0.2',
      '@codemirror/lang-markdown': '6.5.0',
      '@codemirror/lang-python': '6.2.1',
      '@codemirror/lang-rust': '6.0.2',
      '@codemirror/lang-xml': '6.1.0',
      '@codemirror/lang-yaml': '6.1.3',
      '@codemirror/language': '6.12.3',
      '@codemirror/legacy-modes': '6.5.3',
      '@codemirror/merge': '6.12.1',
      '@codemirror/search': '6.7.0',
      '@codemirror/state': '6.6.0',
      '@codemirror/view': '6.43.0',
      'codemirror': '6.0.2',
      'esbuild': '0.28.0',
    };
    assert.deepEqual(codeMirrorBundlePackage.dependencies, codeMirrorDirectVersions, 'vendored CodeMirror manifest records exact direct versions');
    assert.deepEqual(codeMirrorBundleLock.packages[''].dependencies, codeMirrorDirectVersions, 'vendored CodeMirror lockfile root matches the exact direct versions');
    assert.equal(codeMirrorBundleLock.packages['node_modules/@codemirror/autocomplete'].version, '6.20.2', 'CodeMirror transitive autocomplete version is recorded');
    assert.equal(codeMirrorBundleLock.packages['node_modules/@codemirror/lint'].version, '6.9.6', 'CodeMirror transitive lint version is recorded');
    assert.equal(codeMirrorBundleLock.packages['node_modules/@lezer/highlight'].version, '1.2.3', 'Lezer highlight version is recorded');
    assert.equal(codeMirrorBundleLock.packages['node_modules/style-mod'].version, '4.1.3', 'style-mod version is recorded');
    assert.equal(codeMirrorBundleLock.packages['node_modules/w3c-keyname'].version, '2.2.8', 'w3c-keyname version is recorded');
    assert.ok(fs.readFileSync('prototypes/codemirror-entry.js', 'utf8').includes('cd prototypes/codemirror-bundle'), 'CodeMirror rebuild instructions use the checked-in bundle manifest');
    const parserCrashApi = {
      EditorState: {
        create(config) {
          if (JSON.stringify(config.extensions).includes('bad-language')) {
            throw new TypeError("Cannot read properties of undefined (reading 'parser')");
          }
          return {
            doc: {toString: () => String(config.doc || ''), length: String(config.doc || '').length},
            selection: {main: {head: 0}, ranges: []},
            extensions: config.extensions,
          };
        },
        readOnly: {of(value) { return ['readOnly', value]; }},
      },
      EditorView: {
        theme() { return 'theme'; },
        lineWrapping: 'wrap',
        editable: {of(value) { return ['editable', value]; }},
        updateListener: {of(listener) { return ['listener', listener]; }},
      },
      Compartment: class {
        of(extension) { return ['compartment', extension]; }
        reconfigure(extension) { return ['reconfigure', extension]; }
      },
      keymap: {of(entries) { return ['keymap', entries]; }},
      history() { return 'history'; },
      drawSelection() { return 'drawSelection'; },
      dropCursor() { return 'dropCursor'; },
      rectangularSelection() { return 'rectangularSelection'; },
      crosshairCursor() { return 'crosshairCursor'; },
      indentOnInput() { return 'indentOnInput'; },
      bracketMatching() { return 'bracketMatching'; },
      foldGutter() { return 'foldGutter'; },
      highlightActiveLine() { return 'highlightActiveLine'; },
      lineNumbers() { return 'lineNumbers'; },
      highlightActiveLineGutter() { return 'highlightActiveLineGutter'; },
      search() { return 'search'; },
      highlightSelectionMatches() { return 'highlightSelectionMatches'; },
      indentWithTab: 'indentWithTab',
      defaultKeymap: [],
      historyKeymap: [],
      searchKeymap: [],
      openSearchPanel() { return true; },
      markdown() { return 'bad-language'; },
    };
    const parserCrashPanel = {};
    const fallbackState = api.createEditableCodeMirrorState(parserCrashApi, parserCrashPanel, '/home/test/DOIT.2.md', '```bash\\necho hi\\n```');
    assert.equal(fallbackState.plain, true, 'CodeMirror parser crashes retry as plain editable state');
    assert.equal(fallbackState.state.doc.toString(), '```bash\\necho hi\\n```');
    assert.equal(JSON.stringify(fallbackState.state.extensions).includes('bad-language'), false, 'plain retry removes the failing language extension');
    assert.ok(api.codeMirrorPlainEditableExtensions(parserCrashApi, {}, '/home/test/DOIT.2.md').length > 0, 'plain editable fallback keeps editor controls available');

    const compareRows = canonical(api.lineDiffRows('one\ntwo\nfour', 'one\nthree\nfour'));
    assert.deepStrictEqual(compareRows.map(row => [row.leftKind, row.rightKind]), [
      ['same', 'same'],
      ['added', 'blank'],
      ['blank', 'removed'],
      ['same', 'same'],
    ], 'conflict compare dialog uses a real line diff');
    const compareHtml = api.fileConflictCompareHtml('one\ntwo', 'one\nthree');
    assert.ok(compareHtml.includes('file-compare-line added'), 'editor-only lines are marked green');
    assert.ok(compareHtml.includes('file-compare-line removed'), 'disk-only lines are marked red');
    assert.ok(compareHtml.includes('data-file-compare-scroll'), 'compare columns expose synchronized scroll targets');
  });

  test('t@1645', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(
      source.includes('if (panel._cmGeneration !== generation) return null;'),
      'stale CodeMirror renders no-op instead of reporting a load failure',
    );
    assert.ok(
      source.includes('if (loaded === false) renderFileEditorRawPane(rawPane, path, state.content);'),
      'raw editor fallback is only rendered for real CodeMirror failures',
    );
  });

  test('t@1657', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const withoutStorageHelpers = source
      .replace(/function storageGet\([\s\S]*?\n}\n\nfunction storageSet/, 'function storageSet')
      .replace(/function storageSet\([\s\S]*?\n}\n\nfunction safeJsonParse/, 'function safeJsonParse');
    assert.equal(withoutStorageHelpers.includes('localStorage.'), false, 'browser storage access goes through storageGet/storageSet helpers');
  });

  test('t@1665', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.editorPreviewModeAvailable('/home/test/README.md'), true);
    assert.equal(api.editorPreviewModeAvailable('/home/test/index.html'), true);
    assert.equal(api.editorPreviewModeAvailable('/home/test/app.py'), false);
    assert.equal(api.editorPreviewModeAvailable('/home/test/notes.txt'), false);
    assert.equal(api.editorPreviewModeAvailable('/home/test/config.json'), true);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('function sanitizeMarkdownPreviewHtml'), 'Markdown previews pass through a sanitizer');
    assert.ok(source.includes('MARKDOWN_PREVIEW_BLOCKED_TAGS'), 'Markdown sanitizer blocks executable/embedded HTML tags');
    assert.equal(source.includes('container.innerHTML = window.marked.parse'), false, 'Markdown previews are not inserted with unsanitized marked HTML');
    // previews still insert ONLY sanitized nodes — sanitize into a fragment, linkify bare URLs
    // on that already-sanitized fragment (so linkify can't reintroduce unsafe markup), then replaceChildren.
    assert.ok(source.includes('const frag = sanitizeMarkdownPreviewHtml(html);'), 'Markdown previews sanitize into a fragment');
    assert.ok(source.includes('linkifyBareUrls(frag);'), 'Markdown previews linkify bare URLs on the sanitized fragment');
    assert.ok(source.includes('container.replaceChildren(frag);'), 'Markdown previews replace DOM with the sanitized (+linkified) nodes');
    assert.ok(/function linkifyBareUrls[\s\S]*markdownPreviewUrlAllowed\(url, 'a'\)/.test(source), 'linkifyBareUrls only links URLs that pass markdownPreviewUrlAllowed (safe schemes)');
    const htmlPreview = new TestElement('html-preview');
    htmlPreview.scrollTop = 37;
    htmlPreview.scrollLeft = 6;
    api.renderEditorPreviewPane(htmlPreview, '/home/test/index.html', '<style>h1{color:red}</style><h1>Hello</h1><script>window.bad = true</script>');
    assert.equal(htmlPreview.classList.contains('html-preview-body'), true);
    assert.equal(htmlPreview.classList.contains('code-preview-body'), false);
    assert.equal(htmlPreview.children.length, 2);
    assert.equal(htmlPreview.children[0].className, 'file-editor-html-js-notice');
    assert.equal(htmlPreview.children[0].children[1].href, '/api/fs/html-preview?path=%2Fhome%2Ftest%2Findex.html');
    assert.equal(htmlPreview.children[0].children[1].dataset.htmlPreviewAuth, '1');
    assert.equal((htmlPreview.children[0].children[1].listeners.get('click') || []).length, 1);
    assert.equal(htmlPreview.children[1].className, 'file-editor-html-preview');
    assert.equal(htmlPreview.children[1].attributes.sandbox, '', 'HTML preview iframe is sandboxed with scripts disabled');
    assert.ok(htmlPreview.children[1].srcdoc.includes('<h1>Hello</h1>'), 'HTML preview renders markup through srcdoc');
    assert.equal(htmlPreview.scrollTop, 37, 'HTML preview refresh preserves vertical scroll');
    assert.equal(htmlPreview.scrollLeft, 6, 'HTML preview refresh preserves horizontal scroll');
    api.setFileEditorViewMode('/home/test/app.py', 'split');
    assert.equal(api.editorViewModeFor('/home/test/app.py'), 'edit');
    api.setFileEditorViewMode('/home/test/README.md', 'split');
    assert.equal(api.editorViewModeFor('/home/test/README.md'), 'split');
    const changedPath = '/repo/app/README.md';
    const changedItem = api.fileEditorItemFor(changedPath);
    api.setOpenFileOwner(changedPath, changedItem, {ownerSession: '1'});
    api.setFileEditorViewMode(changedPath, 'edit', changedItem);
    api.setOpenFileOwner(changedPath, changedItem, {ownerSession: '1'});
    api.setFileEditorViewMode(changedPath, 'diff', changedItem);
    assert.equal(api.filePanelItemsForPath(changedPath).join(','), changedItem, 'Finder and Modified-files reuse one editor item for the same path');
    assert.equal(api.editorViewModeFor(changedPath, changedItem), 'diff', 'opening from Modified files flips the shared editor item to diff mode');
    assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, diff: ''}), false, 'unchanged files are not diffable');
    assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, diff: 'diff --git a/a b/a'}), true, 'changed repo files are diffable');
    assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, untracked: true, diff: 'diff --git a/a b/a'}), true, 'untracked/all-added files can render as Differ row diffs');
    assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, diffOriginal: '', diff: 'diff --git a/a b/a\n--- /dev/null\n+++ b/a\n@@\n+one'}), true, 'new-file diffs can render as Differ row diffs');
    const unavailableDiffState = {kind: 'text', diffLoaded: false, diffLoading: true, diff: 'stale'};
    api.markOpenFileDiffUnavailable(unavailableDiffState, 'not tracked');
    assert.equal(unavailableDiffState.diffLoaded, true, 'diff-unavailable is a completed attempt');
    assert.equal(unavailableDiffState.diffUnavailable, true);
    assert.equal(unavailableDiffState.diff, '');
    assert.equal(api.openFileDiffAvailable(unavailableDiffState), false);
    const diffButton = new TestElement('diff-button');
    const fileHistory = [
      {ref: 'abc123def456', short: 'abc123d', subject: 'update file'},
      {ref: '987654321000', short: '9876543', subject: 'create file'},
    ];
    const trackedHistoryState = extra => ({kind: 'text', gitRoot: '/repo/app', gitTracked: true, gitHasHistory: true, gitHistory: fileHistory, ...extra});
    api.setFileEditorViewMode(changedPath, 'edit', changedItem);
    api.updateFileEditorDiffButton(diffButton, changedPath, trackedHistoryState({diffLoaded: true, diff: ''}), changedItem);
    assert.equal(diffButton.hidden, true, 'unchanged files do not show a Diff button');
    api.updateFileEditorDiffButton(diffButton, changedPath, trackedHistoryState({diffLoaded: true, diff: 'diff --git a/a b/a'}), changedItem);
    assert.equal(diffButton.hidden, false, 'changed repo files show a Diff button');
    assert.equal(diffButton.disabled, false, 'changed repo Diff button is clickable');
    api.setFileEditorViewMode(changedPath, 'diff', changedItem);
    api.updateFileEditorDiffButton(diffButton, changedPath, trackedHistoryState({diffLoading: true}), changedItem);
    assert.equal(diffButton.hidden, false, 'active diff view keeps a loading Diff button while refs load');
    assert.equal(diffButton.disabled, false, 'active diff view keeps Exit diff clickable while refs load');
    const codePath = '/repo/app/app.py';
    const codeItem = api.fileEditorItemFor(codePath);
    api.setFileEditorViewMode(codePath, 'diff', codeItem);
    api.updateFileEditorDiffButton(diffButton, codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem);
    assert.equal(diffButton.hidden, false, 'code files stuck in diff still show an Exit diff button');
    assert.equal(diffButton.disabled, false, 'Exit diff stays clickable even when no code-file diff is available');
    // #25: a .py (non-md/html) in NORMAL mode with no diff loaded yet still offers a clickable Diff
    // toggle, which lazily loads the diff on click (it only hides once a load confirms there is none).
    api.setFileEditorViewMode(codePath, 'edit', codeItem);
    api.updateFileEditorDiffButton(diffButton, codePath, trackedHistoryState({}), codeItem);
    assert.equal(diffButton.hidden, false, '#25: a code file with no diff loaded yet still offers a Diff toggle');
    assert.equal(diffButton.disabled, false, '#25: the Diff toggle is clickable so it can lazily load the diff');
    // A file git does not track (untracked / outside any repo) has no committed version to diff against,
    // so the Diff button stays hidden regardless of view mode (gitTracked falsey).
    api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', gitTracked: false}, codeItem);
    assert.equal(diffButton.hidden, true, 'untracked files never show a Diff button');
    assert.equal(api.fileEditorGitActionControlsVisible(codePath, {kind: 'text', gitTracked: false}, codeItem), false, 'non-git files show neither Diff nor Blame');
    api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', gitTracked: true, gitHasHistory: true, gitHistory: fileHistory}, codeItem);
    assert.equal(diffButton.hidden, true, 'files with no repo root never show a Differ button even if stale history metadata exists');
    assert.equal(diffButton.disabled, true, 'hidden no-repo Differ button is not clickable');
    assert.equal(api.fileEditorGitActionControlsVisible(codePath, {kind: 'text', gitTracked: true, gitHasHistory: false}, codeItem), false, 'creation-only files show neither Diff nor Blame');
    assert.equal(api.fileEditorGitActionControlsVisible(codePath, {kind: 'text', gitTracked: true, gitHasHistory: true}, codeItem), false, 'stale history booleans without file-level history show neither Diff nor Blame');
    assert.equal(api.fileEditorGitActionControlsVisible(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), false, 'files with history but no diff hide Diff after the no-diff result is known');
    assert.equal(api.fileEditorBlameControlsVisible(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), true, 'clean files with useful history still offer Blame in normal edit mode');
    assert.equal(api.fileEditorGitActionControlsVisible(codePath, trackedHistoryState({}), codeItem), true, 'files with history and unknown diff state show the Diff control');
    const blameButton = new TestElement('blame-button');
    api.setFileEditorViewMode(codePath, 'edit', codeItem);
    api.updateFileEditorBlameButton(blameButton, codePath, trackedHistoryState({}), codeItem);
    assert.equal(blameButton.hidden, false, 'Blame is visible in normal edit mode for files with useful history');
    assert.equal(blameButton.disabled, false, 'Blame is clickable only in normal edit mode');
    api.updateFileEditorBlameButton(blameButton, codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem);
    assert.equal(blameButton.hidden, false, 'Blame remains visible for clean files with useful history');
    assert.equal(blameButton.disabled, false, 'Blame remains clickable in normal edit mode after Diff confirms a clean file');
    api.setFileEditorViewMode(codePath, 'diff', codeItem);
    api.updateFileEditorBlameButton(blameButton, codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem);
    assert.equal(blameButton.hidden, false, 'Blame stays visible for files with useful history');
    assert.equal(blameButton.disabled, true, 'Blame is not clickable in diff mode');
    const mdPath = '/repo/app/README.md';
    const mdItem = api.fileEditorItemFor(mdPath);
    api.setFileEditorViewMode(mdPath, 'split', mdItem);
    api.updateFileEditorBlameButton(blameButton, mdPath, trackedHistoryState({}), mdItem);
    assert.equal(blameButton.disabled, true, 'Blame is not clickable in split mode');
    api.setFileEditorViewMode(mdPath, 'preview', mdItem);
    api.updateFileEditorBlameButton(blameButton, mdPath, trackedHistoryState({}), mdItem);
    assert.equal(blameButton.disabled, true, 'Blame is not clickable in preview mode');
    api.setFileEditorViewMode(codePath, 'diff', codeItem);
    assert.equal(api.fileEditorGitActionControlsVisible(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), true, 'active diff mode keeps the paired git controls visible so Exit diff remains available');
    // (RECURRING): pressing DIFF on a git-tracked file with useful history but a CLEAN working tree
    // (empty HEAD-vs-working diff) must NOT force-exit diff mode — the FROM/TO sha ref picker has to stay
    // reachable so the user can compare ARBITRARY refs. A clean README.md (many commits, no working changes)
    // used to snap back to edit and hide the picker entirely. codePath is in diff mode here.
    assert.equal(api.fileStateCanRenderDiffView(codePath, trackedHistoryState({diffLoaded: true, diff: ''})), true, 'a clean file WITH useful git history can still render the Diff editor shell and ref picker');
    assert.equal(api.fileStateCanRenderDiffView(codePath, trackedHistoryState({diffLoaded: true, diffUnavailable: true, diff: ''})), false, 'a diff transport/error state does not render the Diff editor shell just because history exists');
    assert.equal(api.diffModeShouldFallBackToEdit(codePath, trackedHistoryState({diffLoaded: true, diff: ''}), codeItem), false, 'a clean file WITH useful git history stays in diff mode so the FROM/TO ref picker stays reachable');
    assert.equal(api.diffModeShouldFallBackToEdit(codePath, {kind: 'text', gitRoot: '/repo/app', gitTracked: true, gitHasHistory: false, gitHistory: [], diffLoaded: true, diff: ''}, codeItem), true, 'a file WITHOUT useful history still falls back to edit when its diff is empty');
    assert.equal(api.diffModeShouldFallBackToEdit(codePath, {kind: 'text', gitTracked: true, gitHasHistory: true, gitHistory: fileHistory, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem), true, 'files outside a repo cannot remain in Differ mode even with stale useful-history metadata');
    assert.equal(api.diffModeShouldFallBackToEdit(codePath, trackedHistoryState({diffLoaded: true, diff: 'diff --git a/a b/a'}), codeItem), false, 'a file with a real diff stays in diff mode (control)');
    assert.ok(/async function enterFileEditorDiffMode[\s\S]*!fileStateHasRepo\(path, state\)[\s\S]*fileStateHasRepo\(path, current\) && \(openFileDiffAvailable\(current\) \|\| fileStateHasUsefulGitHistory\(current\)\)[\s\S]*setFileEditorViewMode\(path, 'diff', item\)/.test(source), 'pressing Diff keeps clean files with useful history in diff mode after the default diff load finishes, but only for files under a repo');
    api.updateFileEditorDiffButton(diffButton, codePath, {kind: 'text', gitTracked: false, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem);
    assert.equal(diffButton.hidden, true, 'untracked files stay diff-button-free even in diff mode');
    const diffExpandButton = new TestElement('diff-expand-button');
    api.setFileEditorViewMode(codePath, 'edit', codeItem);
    api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem);
    assert.equal(diffExpandButton.hidden, true, 'Expand unchanged is hidden outside Diff mode');
    api.setFileEditorViewMode(codePath, 'diff', codeItem);
    api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoading: true}, codeItem);
    assert.equal(diffExpandButton.hidden, true, 'Expand unchanged is hidden while the diff is still loading');
    api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoaded: true, diff: ''}, codeItem);
    assert.equal(diffExpandButton.hidden, true, 'Expand unchanged is hidden when the loaded diff has no unchanged-context folds to control');
    api.updateFileEditorDiffExpandButton(diffExpandButton, codePath, {kind: 'text', gitTracked: true, diffLoaded: true, diff: 'diff --git a/a b/a'}, codeItem);
    assert.equal(diffExpandButton.hidden, false, 'Expand unchanged is shown only for an active loaded diff');
    assert.equal(diffExpandButton.disabled, false, 'Expand unchanged is clickable for an active loaded diff');
    assert.equal(diffExpandButton.attributes['aria-pressed'], 'false', 'Expand unchanged reflects the persisted toggle state');
    assert.ok(/updateFileEditorDiffExpandButton\(diffExpandButton, path, state, item\);\s*if \(popoutPreviewButton\)/.test(source), 'rendering leaves Expand unchanged visibility owned by its dedicated updater');
    const noHistoryPath = '/repo/app/DOIT.37.md';
    api.setOpenFileStateForTest(noHistoryPath, {kind: 'text', gitTracked: true, gitHasHistory: false, gitHistory: []});
    assert.deepStrictEqual([...api.fileDiffRefHistoryItems(noHistoryPath)], [], 'file editor ref history is empty when the file has no file-level history');
    assert.deepStrictEqual([...api.diffRefFromSuggestions('/repo/app', noHistoryPath)], [], 'file editor FROM picker does not show unrelated repo history for a no-history file');
    const historyPath = '/repo/app/src/history.md';
    api.setOpenFileStateForTest(historyPath, {
      kind: 'text',
      gitTracked: true,
      gitHasHistory: true,
      gitHistory: [
        {ref: 'abc123def456', short: 'abc123d', subject: 'touch history.md'},
        {ref: '987654321000', short: '9876543', subject: 'create history.md'},
      ],
    });
    assert.deepStrictEqual(Array.from(api.diffRefFromSuggestions('/repo/app', historyPath)).map(item => item.short), ['HEAD', 'abc123d', '9876543'], 'file editor FROM picker uses file-specific history when it exists');
    assert.ok(api.diffRefControlsHtml({compact: true, repo: '/repo/app', path: historyPath}).includes(`data-diff-ref-path="${historyPath}"`), 'editor FROM/TO controls carry the file path for file-scoped history');
    assert.notEqual(
      api.codeMirrorConfigSignature(codePath, {mode: 'diff', expand: false}),
      api.codeMirrorConfigSignature(codePath, {mode: 'diff', expand: true}),
      'Diff expand/collapse changes the CodeMirror signature so the merge view rebuilds',
    );
    const editConfigSignature = JSON.parse(api.codeMirrorConfigSignature(codePath, {mode: 'edit'}));
    const diffConfigSignature = JSON.parse(api.codeMirrorConfigSignature(codePath, {mode: 'diff'}));
    assert.equal(Object.prototype.hasOwnProperty.call(editConfigSignature, 'wrap'), false, 'word wrap is live-reconfigured, not part of the CodeMirror rebuild signature');
    assert.equal(Object.prototype.hasOwnProperty.call(editConfigSignature, 'lineNumbers'), false, 'line numbers are live-reconfigured, not part of the CodeMirror rebuild signature');
    assert.equal(Object.prototype.hasOwnProperty.call(diffConfigSignature, 'wrap'), false, 'word wrap cannot force a diff editor rebuild');
    api.setFileEditorViewMode(codePath, 'edit', codeItem);
  });

  test('t@1835', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.runtimeIntervalDelay(3000), 3000);
    assert.equal(api.runtimeIntervalDelay(1250), 1250);
    assert.equal(api.runtimeIntervalDelay(0), 1);
  });

  test('t@1842', () => {
    const api = loadYolomux('', ['1']);
    assert.deepStrictEqual(canonical(api.codeMirrorSearchMatches('foo bar foo', 'foo')), [
      {from: 0, to: 3},
      {from: 8, to: 11},
    ]);
    assert.equal(api.codeMirrorSearchMatchSummary('foo bar foo', 'foo', {from: 8, to: 11, head: 11}).text, '2/2');
    assert.equal(api.codeMirrorSearchMatchSummary('Foo foo', 'foo', {head: 0}, {caseSensitive: true}).text, '1/1');
    assert.equal(api.codeMirrorSearchMatchSummary('food foo', 'foo', {head: 0}, {wholeWord: true}).text, '1/1');
    assert.equal(api.codeMirrorSearchMatchSummary('abc', '[', {head: 0}, {regexp: true}).text, '0/0');
    assert.deepStrictEqual(canonical(api.codeMirrorSearchMatches('vllm_rust: v0.22.0 0b3ba (/home/keivenc/dynamo/vllm-0.22.0)', '/home/keivenc/dynamo/vllm-0.22.0')), [
      {from: 26, to: 58},
    ], 'absolute paths with dots and hyphens are searched as literal text');
  });

  test('t@1857', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const start = source.indexOf('function renderFileEditorPanel(');
    const end = source.indexOf('function loadFileEditorState(', start);
    assert.ok(start > 0 && end > start, 'could not locate renderFileEditorPanel body');
    assert.equal(source.slice(start, end).includes('.focus('), false, 'renderFileEditorPanel must not steal focus during refresh renders');
    assert.ok(source.includes('function capturePaneViewStateForItemIfPresent(item)'), 'pane viewport capture has one shared focus-transition helper');
    assert.ok(source.includes('capturePaneViewStateForItemIfPresent(previous)'), 'switching pane tabs captures the outgoing pane viewport through the shared helper');
    assert.ok(/function setFocusedPanelItem[\s\S]*const previousItem = focusedPanelItem;[\s\S]*if \(previousItem !== item\) capturePaneViewStateForItemIfPresent\(previousItem\);/.test(source), 'clicking away from a pane snapshots its live viewport');
    assert.ok(/function setFocusedTerminal[\s\S]*const previousItem = focusedPanelItem;[\s\S]*if \(previousItem !== session\) capturePaneViewStateForItemIfPresent\(previousItem\);/.test(source), 'clicking into a terminal snapshots the outgoing pane viewport');
    assert.ok(source.includes('const scrollTop = scrollDOM?.scrollTop || 0;'), 'external CodeMirror reload preserves scrollTop');
    assert.ok(source.includes('view.requestMeasure({write: restoreScroll});'), 'external CodeMirror reload restores scroll after the document update');
    assert.ok(source.includes('view.requestMeasure'), 'CodeMirror viewport restore waits for a measured layout frame');
    assert.ok(source.includes("scrollSnapshot: typeof view.scrollSnapshot === 'function' ? view.scrollSnapshot() : null"), 'CodeMirror editor viewport capture stores a document-anchored scroll snapshot for long files');
    assert.ok(/view\.dispatch\(\{\s*selection: \{anchor, head\},[\s\S]*state\.scrollSnapshot \? \{effects: state\.scrollSnapshot\}/.test(source), 'CodeMirror editor viewport restore dispatches the saved scroll snapshot through CodeMirror');
    assert.ok(/function fileEditorPanelViewStateCaptureHasLayout\(panel, scrollDOM\)[\s\S]*!panel\.isConnected[\s\S]*scrollDOM\.clientHeight <= 0/.test(source), 'CodeMirror editor viewport capture ignores detached zero-height panels');
    assert.ok(/const paneScrollContainerSelector = \[[\s\S]*'\.preferences-scroll'[\s\S]*'\.info-list'[\s\S]*'\.file-explorer-tree-panel'[\s\S]*'\.file-editor-codemirror-panel \.cm-scroller'/.test(source), 'generic pane viewport capture includes non-editor and editor scroll containers');
    assert.ok(/function movePanelsToPool\(\)[\s\S]*capturePaneViewState\(item, panel\)/.test(source), 'pooled pane capture uses the generic pane viewport path');
    assert.ok(/function bindPanelShell\(panel, session\)[\s\S]*panel\.addEventListener\('scroll'[\s\S]*schedulePaneViewStateCapture\(session, panel\)/.test(source), 'pane shell delegates scroll captures to the generic pane viewport scheduler');
    assert.ok(/function schedulePaneViewStateCapture\(item, panel\)[\s\S]*requestAnimationFrame\(\(\) => \{[\s\S]*capturePaneViewState\(item, currentPanel\)/.test(source), 'pane scroll capture is throttled through one shared scheduler');
    assert.ok(/function scheduleFileEditorPanelViewStateCapture\(item, panel\)[\s\S]*schedulePaneViewStateCapture\(item, panel\)/.test(source), 'CodeMirror editor scroll capture routes through the shared pane scheduler');
    assert.ok(/addEventListener\('scroll', \(\) => \{[\s\S]*scheduleFileEditorSplitScrollSync\(panel, 'editor'\);[\s\S]*scheduleFileEditorPanelViewStateCapture\(item, panel\);[\s\S]*\}\)/.test(source), 'CodeMirror editor scroll listener records viewport state as well as split-preview sync');
  });

  test('t@1869', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const menuStart = source.indexOf('function bindAppMenuHover(');
    const menuEnd = source.indexOf('function openAppMenu(', menuStart);
    assert.ok(menuStart > 0 && menuEnd > menuStart, 'could not locate bindAppMenuHover body');
    const menuBody = source.slice(menuStart, menuEnd);
    assert.ok(menuBody.includes('canOpen: () => autoFocusEnabled || appMenuIsOpen()'), 'menu hover-open is cold-disabled by auto-focus but still switches while a menu is manually open');
    assert.ok(menuBody.includes('openAppMenuId === menuId'), 'old menu hover-close timers must not close a newer open menu');
    const activePreferenceStart = source.indexOf('function activePreferenceControl(');
    const activePreferenceEnd = source.indexOf('function clampPreferenceNumber(', activePreferenceStart);
    assert.ok(activePreferenceStart > 0 && activePreferenceEnd > activePreferenceStart, 'could not locate activePreferenceControl body');
    const activePreferenceBody = source.slice(activePreferenceStart, activePreferenceEnd);
    assert.ok(activePreferenceBody.includes('[data-preference-section-toggle]'), 'preference section buttons are preserved through search focusout');
    assert.ok(activePreferenceBody.includes('[data-preferences-reset-all]'), 'global reset button is preserved through search focusout');
    assert.ok(activePreferenceBody.includes('[data-preferences-reset-confirm]'), 'global reset confirmation button is preserved through focusout');
    assert.equal(source.includes('let sessionFilesRequestId = 0;'), false, 'standalone Changes request id is removed');
    assert.ok(source.includes('const fileExplorerSessionFilesGuard = makeGenerationGuard();'), 'Finder diff fetches have their own stale-response generation guard');
    assert.ok(source.includes('const requestIsCurrent = fileExplorerSessionFilesGuard.begin();'), 'Finder diff fetches reject stale responses through the shared guard');
    assert.ok(source.includes('function activeChangesControl'), 'Finder diff renders can detect active controls');
    assert.ok(source.includes('!activeChangesControl(panel)'), 'background Changes renders preserve active selects and ref controls');
    assert.ok(source.includes('function sessionFilesRenderOptions'), 'modified-file fetch rendering distinguishes silent polls from explicit user refreshes');
    assert.ok(source.includes('const loadingPromise = (async () => {'), 'editor file loading keeps a promise handle for guarded cleanup');
    assert.ok(source.includes('if (current?.loadingPromise === loadingPromise) delete current.loadingPromise;'), 'editor file loading clears stale loading promises after failure or success');
    assert.ok(source.includes('const activitySummaryGuard = makeGenerationGuard();'), 'activity summary refreshes carry a stale-response generation guard');
    assert.ok(source.includes('if (activitySummaryRefreshing && options.force !== true) return;'), 'activity summary polling skips overlapping non-forced refreshes');
    assert.ok(source.includes('let transcriptMetaRefreshPromise = null;'), 'metadata refreshes keep one in-flight promise');
    assert.ok(source.includes('if (transcriptMetaRefreshPromise) return transcriptMetaRefreshPromise;'), 'metadata refreshes dedupe overlapping loads');
    assert.ok(source.includes('transcriptMetaLoading = true;'), 'metadata refreshes expose a loading state');
    assert.ok(source.includes('infoMetadataLoadingHtml()'), 'YO!info renders an explicit repo-metadata loading state');
    assert.ok(source.includes('const notificationLastSentLimit = 512;'), 'notification signature cache has a bounded size');
    assert.ok(source.includes('setLimitedMapEntry(notificationLastSent, key, now, notificationLastSentLimit);'), 'notification signatures use the shared bounded-map helper');
    assert.ok(source.includes('existing?.delay === normalizedDelay'), 'runtime intervals keep their timer phase when refresh delays are unchanged');
    assert.ok(/async function boot\(\)[\s\S]*?initialAutoStatusesPromise = loadAutoStatuses\(\)\.catch[\s\S]*?\}\s*bindClipboardPaste\(\);/.test(source), 'image paste binding is installed during boot and does not wait on background auto-status refresh');
    // C12 F3: terminal fit scheduling collapsed from rAF + 80ms + 250ms (three fits) to one rAF + a single
    // trailing fit; the redundant middle timer (fitFinalTimer) is gone.
    assert.equal(source.includes('item.fitFinalTimer'), false, 'C12 F3: the redundant third fit timer is removed');
    assert.ok(/function scheduleFit[\s\S]*?requestAnimationFrame\([\s\S]*?item\.fitTimer = setTimeout/.test(source), 'C12 F3: fit scheduling is one rAF plus a single trailing timeout');
    assert.ok(/function terminalProbeFontFamily\(container\)[\s\S]*--mono-font[\s\S]*terminalFontFamily/.test(source), 'terminal fallback cell measurement uses the shared bundled mono font token');
    assert.equal(source.includes("probe.style.font = '13px ui-monospace"), false, 'terminal fallback cell measurement must not use a hardcoded fallback font stack');
    assert.ok(/function terminalFitSignature\(size\)[\s\S]*contentWidth[\s\S]*cellWidth[\s\S]*terminalFontSize[\s\S]*terminalFontFamily/.test(source), 'terminal fit skips are keyed by pane size, cell metrics, and terminal font settings');
    assert.ok(/function fitTerminal\(session, options = \{\}\)[\s\S]*terminalFitIsUnchanged\(item, size\)[\s\S]*return[\s\S]*if \(changed\) item\.term\.resize/.test(source), 'terminal fit drops duplicate observer echoes before calling term.resize again');
    const dockviewSource = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
    assert.ok(/function dockviewScheduleLayoutToHost\(api = dockviewLayoutState\.api, host = dockviewLayoutState\.host\)[\s\S]*requestAnimationFrame\(\(\) => \{[\s\S]*dockviewLayoutToHost\(api, host\)/.test(dockviewSource), 'Dockview host ResizeObserver layout work is coalesced to one layout per frame');
    assert.equal(source.includes('esm.sh'), false, 'CodeMirror loading never falls back to a third-party CDN');
    assert.ok(source.includes('CodeMirror local bundle is unavailable or incomplete'), 'CodeMirror loading reports local bundle failures clearly');
    assert.ok(source.includes('maybeHandleServerVersionChange(transcriptMeta.server_version)'), 'the metadata poll checks the live server version');
    // #39: the new-session picker greys an installed-but-logged-out agent and names its login command;
    // the metadata poll refreshes agentAuth so it re-enables after the user logs in.
    assert.ok(/function agentLoggedIn\(agent\)[\s\S]*entry\.logged_in !== false/.test(source), '#39: agentLoggedIn treats only confirmed logged-out status as unavailable');
    assert.ok(source.includes('const loggedOut = available && !agentLoggedIn(agent);'), '#39: the new-session picker computes a logged-out state per agent');
    assert.ok(/disabled: readOnlyMode \|\| !available \|\| loggedOut \|\| capped/.test(source), '#39: a logged-out agent is disabled in the picker');
    assert.ok(/loggedOut[\s\S]*?t\('menu\.tmux\.runLogin', \{command: agentLoginCommand\(agent\)\}\)/.test(source), '#39: a logged-out agent shows its login command as the menu detail (via t())');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['menu.tmux.runLogin'], 'Run {command}', '#39/#121: the login-command detail renders "Run <command>" in English');
    assert.ok(/function agentUnavailableReason\(agent\)[\s\S]*unavailable_reason/.test(source), '#62: unavailable agents carry a server-provided reason');
    assert.ok(/agentUnavailableReason\(agent\) === 'not-on-path'[\s\S]*t\('menu\.tmux\.agentUnavailablePath'\)/.test(source), '#62: missing agent CLIs show the server-PATH detail');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['menu.tmux.agentUnavailablePath'], 'Not on server PATH', '#62: missing-agent detail is localized in English');
    assert.ok(/function applyAgentAvailabilityPayload[\s\S]*payload\.agentAuth[\s\S]*payload\.availableAgents/.test(source) && source.includes('applyAgentAvailabilityPayload(transcriptMeta)'), '#39: the metadata poll refreshes agent login and installed-agent status');
    // #41: the frontend mirrors the server's auto backend resolution (codex -> claude -> deterministic)
    // so the chat input enables to match what the backend will run, and defaults to auto.
    assert.ok(/const YOAGENT_CHAT_BACKENDS = \['codex', 'claude'\]/.test(source) && /function yoagentResolvedBackend\(\)[\s\S]*?for \(const agent of YOAGENT_CHAT_BACKENDS\)[\s\S]*?yoagentBackendUsable\(agent\)/.test(source), '#41: yoagentResolvedBackend prefers codex then claude among logged-in agents');
    assert.ok(source.includes("initialSetting('yoagent.backend', 'auto')"), '#41: the YO!agent backend default is auto');
    assert.ok(/function yoagentChatEnabled\(\)[\s\S]*YOAGENT_CHAT_BACKENDS\.includes\(yoagentResolvedBackend\(\)\)/.test(source), '#41/#72: chat-enabled tracks only usable model-backed chat');
    assert.ok(/maybeHandleServerVersionChange[\s\S]*serverVersion === bootstrap\.version[\s\S]*updateNotificationAllowsVersion\(bootstrap\.version, serverVersion\)/.test(source), 'server/client-version reload is gated on the boot version and the reload_on_update threshold');
    assert.ok(/function updateNotificationAllowsVersion\([^)]*\)[\s\S]*cleanLevel === 'none'[\s\S]*targetParts\[1\] !== currentParts\[1\][\s\S]*cleanLevel === 'patch'[\s\S]*targetParts\[2\] !== currentParts\[2\][\s\S]*cleanLevel === 'patch'/.test(source), 'update notification threshold follows SemVer major/minor/patch mismatches');
    assert.ok(/maybeHandleServerVersionChange[\s\S]*boolSetting\('general\.reload_on_update_auto'[\s\S]*reloadIsSafe\(\)/.test(source), 'auto-reload only fires when enabled and reloadIsSafe()');
    const updateApi = loadYolomux('', ['1']);
    assert.equal(updateApi.normalizeUpdateNotificationLevelForTest(true), 'patch', 'legacy true maps to patch notifications');
    assert.equal(updateApi.normalizeUpdateNotificationLevelForTest(false), 'none', 'legacy false maps to no update notifications');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.3.26', 'patch'), true, 'patch threshold notifies for patch updates');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.3.26', 'minor'), false, 'minor threshold suppresses patch-only updates');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.4.0', 'major'), false, 'major threshold suppresses minor-only updates');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.4.0', 'minor'), true, 'minor threshold notifies for minor updates');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '1.0.0', 'minor'), true, 'minor threshold includes major updates');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '1.0.0', 'major'), true, 'major threshold notifies for major updates');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.3.24', 'patch'), true, 'patch threshold prompts for server/client patch rollback mismatches');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.2.0', 'minor'), true, 'minor threshold prompts for server/client minor rollback mismatches');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('1.0.0', '0.9.0', 'major'), true, 'major threshold prompts for server/client major rollback mismatches');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '0.3.24', 'minor'), false, 'minor threshold still suppresses patch-only mismatches');
    assert.equal(updateApi.updateNotificationAllowsVersionForTest('0.3.25', '1.0.0', 'none'), false, 'none threshold suppresses update notifications');
    assert.ok(/function applyUpdateAvailable\(status\)[\s\S]*status\.notify === false[\s\S]*return/.test(source), 'origin/main update cue respects the server-side notify threshold');
    assert.ok(/function reloadIsSafe\(\)[\s\S]*file\?\.dirty[\s\S]*isContentEditable/.test(source), 'reloadIsSafe refuses when an editor buffer is dirty or the user is typing');
    // #40: YO!info and YO!agent are merged into ONE panel with a segmented sub-tab toggle; both sub-views
    // (the metadata table + the AI chat/summary) live in the single info panel and the active one is shown.
    const createInfoPanelSource = source.slice(source.indexOf('function createInfoPanel()'), source.indexOf('// The merged YO!info pane keeps its outer chrome'));
    assert.ok(/function createInfoPanel\(\)[\s\S]*?class="info-subtabs"[\s\S]*?data-info-subtab="info"[\s\S]*?data-info-subtab="yoagent"/.test(source), '#40: the merged info panel renders a YO!info/YO!agent sub-tab toggle');
    assert.ok(/function createInfoPanel\(\)[\s\S]*?data-info-subview="info"[\s\S]*?id="info-content"[\s\S]*?data-info-subview="yoagent"[\s\S]*?id="yoagent-content"/.test(source), '#40: the merged info panel hosts both the metadata and the YO!agent sub-views');
    assert.ok(/function createInfoPanel\(\)[\s\S]*?class="info-subtab-actions"[\s\S]*?data-info-subtab-action="info"[\s\S]*?data-info-subtab-action="yoagent"/.test(source), '#40: refresh actions live in the YO!info/YO!agent sub-tab bar');
    assert.equal(createInfoPanelSource.includes('class="panel-detail-row"'), false, '#40: the merged YO!info panel no longer renders a redundant title/info bar');
    assert.equal(createInfoPanelSource.includes('id="meta-'), false, '#40: the merged YO!info panel no longer renders a subtitle meta bar');
    assert.equal(/class="transcript-head info-head"/.test(source), false, '#40: the duplicate sub-view title bar is gone');
    assert.ok(/function createInfoPanel\(\)[\s\S]*?renderInfoPanel\(\);[\s\S]*?renderYoagentPanel\(\);/.test(source), '#40: the merged panel renders both sub-views on creation');
    assert.ok(/renderAttached:\s*\(\) => \{[\s\S]*?applyInfoSubTab\(\);[\s\S]*?renderInfoPanel\(\);[\s\S]*?renderYoagentPanel\(\{preserveDraft: true, scrollBottom: false\}\);[\s\S]*?\}/.test(source), '#40/#YO!info: info tab registry hook renders both sub-views on attach');
    assert.ok(/function renderAttachedPanelContent\(item\)[\s\S]*?tabTypeForItem\(item\)\?\.renderAttached[\s\S]*?renderAttached\(item\)/.test(source), '#40/#YO!info: pooled panel attach dispatches through TAB_TYPES');
    assert.ok(/function renderDropSlot\(slot, session\)[\s\S]*?node\.appendChild\(panel\);\s*renderAttachedPanelContent\(session\);/.test(source), '#40/#YO!info: initial drop-slot attach renders YO!info before metadata polling');
    assert.ok(/function syncActivePanelsInPlace\(\)[\s\S]*?dropSlot\.replaceChildren\(desired\);[\s\S]*?updatePanelSlot\(desired, item, slot\);[\s\S]*?renderAttachedPanelContent\(item\);/.test(source), '#40/#YO!info: in-place panel swaps also render YO!info after attachment');
    assert.equal(source.includes('function createYoagentPanel('), false, '#40: the standalone YO!agent panel builder is gone');
    assert.ok(source.includes('function setInfoSubTab(') && source.includes('function applyInfoSubTab(') && source.includes('function relocalizeInfoPanelChrome(') && source.includes('async function openInfoSubTab('), '#40: sub-tab switch + locale + open helpers exist');
    assert.ok(/function setInfoSubTab[\s\S]*?writeStoredInfoSubTab\(next\)/.test(source), '#40: switching the sub-tab persists it (remembered across reloads)');
    assert.ok(/function openInfoSubTab[\s\S]*?selectSession\(infoItemId\)/.test(source), '#40: opening YO!agent activates the merged info pane');
    assert.ok(/function openYoagentRightPane\(\)[\s\S]*rightmostExistingPaneSlot\(\)[\s\S]*moveSessionToSlot\(infoItemId, targetSlot[\s\S]*splitInfoItemToRightPane\(sourceSlot\)/.test(source), '#40: the YO!agent shortcut places the merged info tab in the right pane');
    assert.ok(/function rerenderForLocale\(options = \{\}\)[\s\S]*?relocalizeInfoPanelChrome\(\)[\s\S]*?renderInfoPanel\(\)[\s\S]*?renderYoagentPanel\(\{preserveDraft: true, allowBusyRebuild: options\.localeChange === true\}\)[\s\S]*?relocalizeInfoPanelChrome\(\)/.test(source), '#40/#50: a language switch relabels persistent YO!info chrome and forces busy YO!agent UI to rebuild in the new locale');
    assert.equal(/function virtualPanelControlsHtml\(session\)[\s\S]*terminal-tab/.test(source), false, '#40: Preferences and YO!info virtual pane controls do not render a redundant active-tab pill');
    assert.ok(/function relocalizeInfoPanelChrome[\s\S]*?querySelectorAll\('\[data-info-subtab\]'\)[\s\S]*?button\.dataset\.infoSubtab === 'yoagent'[\s\S]*?data-info-refresh[\s\S]*?data-yoagent-refresh/.test(source), '#40/#50: the persistent YO!info/YO!agent sub-tab chrome and actions are localized in place by data attribute');
    assert.equal(/function relocalizeInfoPanelChrome[\s\S]*?info\.subtitle/.test(source), false, '#40/#50: no removed YO!info subtitle bar remains to relocalize');
    assert.ok(/let i18nApplyLocaleRequestId = 0/.test(source) && /async function applyLocale[\s\S]*?\+\+i18nApplyLocaleRequestId[\s\S]*?if \(requestId !== i18nApplyLocaleRequestId\) return/.test(source), '#50: overlapping language transitions cannot let an older catalog load repaint after the newer language choice');
    // Phase 1: the YO marker glyph is i18n-keyed (renders 優/优 under Chinese), not a hardcoded "YO".
    assert.ok(source.includes("esc(t('brand.marker'))"), 'the YO marker glyph renders via t(brand.marker)');
    // #81: a failed autosave-on-close falls through to the explicit save/discard/cancel dialog instead of
    // silently aborting the close.
    assert.ok(/if \(await saveFileEditor\(path, panel, \{autosave: true, closing: true\}\)\) return true;[\s\S]*?showFileEditorDecisionDialog/.test(source), '#81: autosave-on-close failure falls back to the close dialog');
    // #85/#86/#87/#88: toast removal honors countdownMs; reconnect confirmation is single-in-flight; the
    // repo popover is viewport-clamped; an equal-mtime unknown-size entry is treated as changed (re-stat).
    assert.ok(/removeAttentionAlert\(id\), options\.countdownMs \|\| toastDurationMs/.test(source), '#85: toast removal uses options.countdownMs');
    assert.ok(/function confirmSessionGoneOrReconnect[\s\S]*?if \(item\.confirmingGone\) return;[\s\S]*?item\.confirmingGone = true/.test(source), '#86: reconnect confirmation has an in-flight guard');
    assert.ok(/function showFileTreeRepoPopover[\s\S]*?clampToViewport\(/.test(source), '#87: the repo popover is clamped to the viewport');
    assert.ok(/function scheduleRepoRowHoverPopover[\s\S]*?setTimeout\([\s\S]*?showRepoRowHoverPopover\(row, path\)/.test(source), '#87: repo directory hover popovers are delayed through the shared popover delay timer');
    assert.equal(source.includes('row.onmouseenter = () => showRepoRowHoverPopover(row, fullPath);'), false, '#87: repo directory hover must not open the popover immediately on mouseenter');
    assert.ok(/function fileEntryChanged[\s\S]*?state\.size == null \|\| entry\.size == null\) return true/.test(source), '#88: unknown-size equal-mtime entries are treated as changed');
    // #73: the item-keyed editor maps are cleaned up on close + migrated on rename (no unbounded growth),
    // and the per-pane LRU timestamp survives a session rename.
    assert.ok(/function removeFilePanelOwner[\s\S]*?fileEditorViewState\.delete\(item\)[\s\S]*?tabLastActivatedAt\.delete\(item\)/.test(source), '#73: editor view-state + LRU timestamp are dropped on tab close');
    assert.ok(/function renameOpenFilePath[\s\S]*?fileEditorViewState\.set\(newKey[\s\S]*?tabLastActivatedAt\.set\(newKey/.test(source), '#73: editor view-state + LRU timestamp are migrated on rename');
    assert.ok(/function replaceSessionMetadata[\s\S]*?tabLastActivatedAt,\s*\n\s*\]\)/.test(source), '#73: the LRU timestamp is rekeyed across a session rename');
    const mergedInfoCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.info-subview\s*\{[\s\S]*?display:\s*none/.test(mergedInfoCss), '#40: inactive sub-views are hidden');
    assert.ok(/\.info-subview\.active\s*\{[\s\S]*?display:\s*flex/.test(mergedInfoCss), '#40: the active sub-view is shown');
    assert.ok(/\.info-subtab\.active\s*\{/.test(mergedInfoCss), '#40: the active sub-tab button is styled');
    assert.ok(/\.info-subtabs\s*\{[\s\S]*?background:\s*var\(--pane-bar-bg/.test(mergedInfoCss), '#40: YO!info sub-tabs use the same active pane bar background token');
    assert.ok(/\.info-subtab-actions\s*\{[\s\S]*?margin-inline-start:\s*auto/.test(mergedInfoCss), '#40: the active refresh action sits at the right side of the merged sub-tab bar');
    assert.ok(/\.info-subtab\.active\s*\{[\s\S]*?background:\s*var\(--pane-tab-active-bg/.test(mergedInfoCss), '#40: active YO!info sub-tab uses the pane active-tab color token');
    assert.ok(/\.info-subtab \.session-button-dir\s*\{[\s\S]*?color:\s*inherit/.test(mergedInfoCss), '#40: YO!info/YO!agent sub-tab labels inherit button contrast instead of forcing white text');
    assert.ok(/\.info-subtab\s*\{[\s\S]*?display:\s*inline-flex[\s\S]*?align-items:\s*center[\s\S]*?line-height:\s*1/.test(mergedInfoCss), '#40: YO!info/YO!agent sub-tab labels are vertically centered, including CJK labels');
    assert.ok(/body\.theme-light \.info-list,[\s\S]*?body\.theme-light \.info-watched\s*\{[\s\S]*?background:\s*#ffffff/.test(mergedInfoCss), '#40: the light-mode YO!info table uses a white surface');
    assert.ok(mergedInfoCss.includes('--info-branch-column-width: 320px'), 'YO!info Branch column has a named default width token');
    assert.ok(mergedInfoCss.includes('--info-desc-column-width: 310px'), 'YO!info desc column has a named default width token');
    assert.ok(/grid-template-columns:[\s\S]*var\(--info-session-column-width\)[\s\S]*var\(--info-path-column-width\)[\s\S]*minmax\(var\(--info-branch-column-width\), var\(--info-branch-column-width\)\)[\s\S]*var\(--info-pr-column-width\)[\s\S]*var\(--info-linear-column-width\)[\s\S]*minmax\(var\(--info-desc-column-width\), 1fr\)[\s\S]*var\(--info-updated-column-width\)/.test(mergedInfoCss), 'YO!info table columns use named width tokens');
    assert.ok(/min-width:\s*calc\([\s\S]*var\(--info-branch-column-width\)[\s\S]*var\(--info-desc-column-width\)[\s\S]*var\(--info-table-column-gap\) \* 6[\s\S]*var\(--info-table-inline-padding\) \* 2/.test(mergedInfoCss), 'YO!info table minimum width is derived from named column tokens');
    assert.ok(mergedInfoCss.includes('--info-column-resizer-hit-width: 24px'), 'YO!info column resize target has a named hit-width token');
    assert.ok(/\.info-resizable-header-cell\s*\{[\s\S]*?overflow:\s*visible/.test(mergedInfoCss), 'YO!info resize handles are not clipped by header cells');
    assert.ok(/\.info-column-resizer\s*\{[\s\S]*?width:\s*var\(--info-column-resizer-hit-width\)[\s\S]*?cursor:\s*col-resize/.test(mergedInfoCss), 'YO!info headers expose full-width column-resize handles');
    assert.ok(source.includes('data-info-column-resize="${esc(column)}"'), 'YO!info headers render resize handles through a shared helper');
    assert.ok(source.includes("resizeHandle('branch', t('info.resizeBranchColumn'))"), 'YO!info Branch header renders the resize handle');
    assert.ok(source.includes("resizeHandle('desc', t('info.resizeDescColumn'))"), 'YO!info desc header renders the resize handle');
    assert.ok(/function bindInfoColumnResizers[\s\S]*dataset\.infoColumnResize[\s\S]*setPointerCapture[\s\S]*storageSet\(config\.storageKey/.test(source), 'YO!info column drag persists resized widths through shared config');
    // #48: the merged info panel gets its own 3-row grid so the YO!info|YO!agent sub-tab row is always
    // visible (a real track) without a redundant detail/header bar.
    assert.ok(/\.info-panel\s*\{[\s\S]*?grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(mergedInfoCss), '#48: the info panel reserves a row for the sub-tab toggle');
    assert.ok(/\.info-panel\.details-collapsed\s*\{[\s\S]*?grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(mergedInfoCss), '#48: the sub-tab row survives a collapsed detail header');
    // #50: a language switch force-re-renders every localized surface and fires applyLocale optimistically.
    assert.ok(/function rerenderForLocale\(options = \{\}\)[\s\S]*?renderPreferencesPanels\(\{force: true\}\)[\s\S]*?renderBrandWordmark\(\)/.test(source), '#50: rerenderForLocale force-re-renders Preferences + the wordmark');
    assert.ok(/if \(path === 'general\.language'\) applyLocale\(resolveLocalePref\(value\)\)/.test(source), '#50: the language select switches locale optimistically, not on the poll');
    // #52: the wordmark YO/LO glyphs localize client-side (優樂 / 优乐) via t(brand.wordmark.*).
    assert.ok(/function renderBrandWordmark\(\)[\s\S]*?t\('brand\.wordmark\.yo'\)[\s\S]*?t\('brand\.wordmark\.lo'\)/.test(source), '#52: renderBrandWordmark localizes the YO/LO wordmark glyphs');
    assert.ok(/function updateBrandTitles\(\)[\s\S]*brand\.title = topbarServerUptimeTitle\(\)[\s\S]*version\.title = topbarVersionTitle\(\)/.test(source), 'top-left brand hover shows server uptime and version hover shows the commit SHA');
    assert.ok(/function topbarVersionTitle\(\)[\s\S]*SHA: \$\{sha\}/.test(source), 'top-left version title includes the SHA');
    // #47: tab drags use the native drag image (no JS clone-follow), and the drop-placement path reuses
    // cached tab rects during a drag instead of forcing sync layout (getBoundingClientRect) per move.
    assert.ok(/function startSessionDrag[\s\S]*?setDragImage\(source/.test(source), '#47: tab drags install the native drag image (the tab itself)');
    // C12 F2: dragstart must NOT force a layout reflow with getBoundingClientRect (it stalled the cold first drag).
    const startDragBody = source.slice(source.indexOf('function startSessionDrag'), source.indexOf('function endSessionDrag'));
    assert.equal(/\.getBoundingClientRect\(/.test(startDragBody), false, 'C12 F2: dragstart computes the grab offset without a getBoundingClientRect reflow');
    assert.ok(startDragBody.includes('event.offsetX') && startDragBody.includes('event.offsetY'), 'C12 F2: dragstart uses event.offsetX/offsetY for the drag-image offset');
    // C12 F1: a move of an already-running pane skips the blocking ensure-session round-trip.
    assert.ok(source.includes('function sessionTerminalIsLive('), 'C12 F1: a terminal-liveness helper exists');
    assert.ok(/if \(isTmuxSession\(session\) && !sessionTerminalIsLive\(session\)\) \{\s*const ensured = await ensureSession/.test(source), 'C12 F1: moveSessionToSlot only awaits ensureSession when the pane is not already live');
    assert.equal(source.includes('function startCustomDragPreview'), false, '#47: the tab clone-follow preview is removed');
    assert.ok(/function paneTabDropPlacement[\s\S]*?dragMeasureStrip\(strip\)/.test(source), '#47: drop placement measures the strip via the per-drag cache');
    assert.ok(/function dragMeasureStrip\([\s\S]*?dragSession != null[\s\S]*?dragTabRectCache/.test(source), '#47: the rect cache is only active during a live drag');
    assert.ok(source.includes('id="summary-${session}" class="summary-preview markdown-body"'), 'the YO!summary panel is a markdown-body container, not a raw <pre>');
    assert.ok(/transcript-head">\$\{esc\(t\('menu\.tmux\.aiTranscript'/.test(source), 'the YO!summary panel head names the session via the localized aiTranscript key');
    assert.ok(/function startSummaryStream[\s\S]*renderMarkdownPreviewInto\(node, raw\)/.test(source), 'the YO!summary stream renders accumulated text through the markdown pipeline');
    assert.ok(/function createTopbarSearch[\s\S]*openFileQuickOpen\(\)/.test(source), 'the topbar universal search opens the unified quick-open/command palette (no forked logic)');
    assert.ok(/renderSessionButtons[\s\S]*appendChild\(createTopbarSearch\(\)\)/.test(source), 'the topbar search is mounted in the menubar middle area');
    assert.ok(/refreshFileIndexStatus[\s\S]{0,400}\/api\/fs\/index-status\?root=/.test(source), '#30/#31: the client warms the backend index and tracks build status via /api/fs/index-status');
    assert.ok(source.includes("=== 'building' ? '…' : 'I'"), '#31: the indexed badge is compact when the date column is off');
    assert.ok(/function fileExplorerIndexBadgeText\(path\) \{[\s\S]*?fileExplorerTreeDateMode !== 'none'[\s\S]*?return ''/.test(source), '#31: Date/Ago rows hide the indexed status badge so it cannot overlap the date');
    assert.ok(source.includes("=== 'building' ? 'indexing…' : 'indexed'"), '#31: the indexed badge title keeps the full status text');
    assert.ok(/payload && payload\.ready \? 'ready' : 'building'/.test(source), '#31: a ready index (incl. during a background TTL rebuild that keeps ready=true) stays "indexed", not "indexing"');
    assert.ok(/fileExplorerIndexStatus\.set\(normalized, 'building'\);\s*refreshFileIndexStatus\(normalized\)/.test(source), '#30: indexing a directory eagerly warms its backend index (no cold first-query live walk)');
    const loadAutoStatusesFn = source.slice(source.indexOf('async function loadAutoStatuses'), source.indexOf('async function loadAutoStatuses') + 1700);
    assert.ok(loadAutoStatusesFn.includes('updateDocumentTitle();') && loadAutoStatusesFn.includes('renderAutoApproveButtons();'), '#46: the auto-status poll re-syncs the YO markers (renderAutoApproveButtons) alongside the tab title, so a done pane stops spinning on the same poll');
    assert.ok(source.includes("{path: 'file_explorer.indexed_dirs'"), '#32: Preferences exposes an editable indexed-directories list');
    assert.ok(/function reconcileIndexedDirsFromSetting[\s\S]*setFileExplorerDirectoryIndexed\(dir, true\)[\s\S]*setFileExplorerDirectoryIndexed\(dir, false\)/.test(source), '#32: editing the indexed-dirs setting adds/removes indexed dirs (bi-directional sync)');
    assert.ok(source.includes('/api/fs/unindex?root='), '#32: removing an indexed dir wires to the backend unindex');
    // C11: the indexed-dirs setting save MERGES a single add/remove into the shared list instead of
    // overwriting it with this page's set, so two browser origins don't clobber each other's indexed dirs.
    assert.ok(/persistIndexedDirsSetting\(indexed \? \{add: normalized\} : \{remove: normalized\}\)/.test(source), 'C11: indexed-dir save passes the single add/remove op (merge, not overwrite)');
    assert.ok(/function persistIndexedDirsSetting\(op = \{\}\)[\s\S]*?if \(op\.add\) set\.add\(op\.add\)[\s\S]*?if \(op\.remove\) set\.delete\(op\.remove\)/.test(source), 'C11: persistIndexedDirsSetting merges the op into the current shared setting');
    // C11 #3: the localStorage->setting migration is one-time (marker), so a stale per-origin cache can't
    // re-seed the durable shared setting after migration — the setting is the sole desired-root source.
    assert.ok(source.includes("storageSet(fileExplorerIndexedDirsMigratedKey, '1')"), 'C11 #3: indexed-dirs migration from localStorage is recorded as one-time');
    assert.ok(/if \(options\.initial && !migrated\)/.test(source), 'C11 #3: migration runs once, guarded by the marker');
    const focusPreferencesStart = source.indexOf('function focusPreferencesSearch(');
    const focusPreferencesEnd = source.indexOf('function preferencesScrollIsActive(', focusPreferencesStart);
    assert.ok(focusPreferencesStart > 0 && focusPreferencesEnd > focusPreferencesStart, 'could not locate focusPreferencesSearch body');
    assert.ok(source.slice(focusPreferencesStart, focusPreferencesEnd).includes('panel && panel.isConnected !== false'), 'explicit Preferences search focus falls back to the rendered panel when called without a panel');
    assert.equal(source.includes('function focusPreferencesSearchSoon('), false, 'Preferences no longer has delayed search auto-focus');
    assert.equal(source.includes('function focusFreshPreferencesSearchSoon('), false, 'Preferences no longer has fresh-pane search auto-focus');
    const focusedPanelStart = source.indexOf('function setFocusedPanelItem(');
    const focusedPanelEnd = source.indexOf('function clearPendingFileEditorFocusExcept(', focusedPanelStart);
    assert.ok(focusedPanelStart > 0 && focusedPanelEnd > focusedPanelStart, 'could not locate setFocusedPanelItem body');
    const focusedPanelBody = source.slice(focusedPanelStart, focusedPanelEnd);
    assert.equal(focusedPanelBody.includes('focusPreferencesSearch'), false, 'shared pane focus does not steal focus into Preferences search');
    assert.ok(focusedPanelBody.includes('updateTypingIndicator(activeSession)'), 'shared pane focus refreshes every pane focus ring immediately');
    const panelShellStart = source.indexOf('function bindPanelShell(');
    const panelShellEnd = source.indexOf('const head = panel.querySelector', panelShellStart);
    assert.ok(panelShellStart > 0 && panelShellEnd > panelShellStart, 'could not locate bindPanelShell body');
    const panelShellBody = source.slice(panelShellStart, panelShellEnd);
    assert.equal(panelShellBody.includes('focusPreferencesSearch'), false, 'panel pointer/focus events do not steal focus into Preferences search');
    assert.equal(source.includes('function preferenceFocusTargetIsInteractive'), false, 'the old Preferences search auto-focus target helper is removed');
    const resetAllStart = source.indexOf('function resetAllPreferences(');
    const resetAllEnd = source.indexOf('function createFileExplorerPanel(', resetAllStart);
    assert.ok(resetAllStart > 0 && resetAllEnd > resetAllStart, 'could not locate resetAllPreferences body');
    assert.equal(source.slice(resetAllStart, resetAllEnd).includes('focusSearch: true'), false, 'reset all is a settings interaction and does not re-arm fresh search focus');
    const refreshStart = source.indexOf('async function refreshOpenFilesIfChanged(');
    const refreshEnd = source.indexOf('function watchedFileExplorerDirectories(', refreshStart);
    assert.ok(refreshStart > 0 && refreshEnd > refreshStart, 'could not locate refreshOpenFilesIfChanged body');
    const refreshBody = source.slice(refreshStart, refreshEnd);
    assert.ok(refreshBody.includes('const fetched = await fetchFileEntryStatus(path);'), 'open-file refresh uses a structured file lookup');
    assert.ok(refreshBody.includes('refreshOpenFileFromFetchedStatus(path, state, fetched)'), 'open-file polling routes structured status through the shared refresh helper');
    const statusRefreshStart = source.indexOf('async function refreshOpenFileFromFetchedStatus(');
    const statusRefreshEnd = source.indexOf('async function refreshOpenFilesIfChanged(', statusRefreshStart);
    assert.ok(statusRefreshStart > 0 && statusRefreshEnd > statusRefreshStart, 'could not locate refreshOpenFileFromFetchedStatus body');
    const statusRefreshBody = source.slice(statusRefreshStart, statusRefreshEnd);
    assert.ok(statusRefreshBody.includes('if (fetched.missing)'), 'open-file refresh only marks missing after an explicit missing result');
    assert.ok(statusRefreshBody.includes('markOpenFileExternalError'), 'open-file refresh keeps network/list errors separate from deletion');
    assert.ok(statusRefreshBody.includes('openFileBackgroundReloadShouldDefer(path, state)'), 'background open-file refresh defers reload while the edited file has focus or was just saved');
    assert.ok(source.includes('function fileEditorPathHasFocus(path)') && source.includes('fileEditorPanelsForPath(path).some(panel => panel?.contains?.(active))'), 'background reload deferral detects focus inside any same-file editor panel');
    assert.ok(source.includes('function markOpenFileReloadDeferred(path, state, entry)') && source.includes('state.externalReloadDeferred'), 'deferred background reload marks external state instead of replacing the active document');
  });

  test('t@2069', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const start = source.indexOf('function updatePaneTabStrip(');
    const end = source.indexOf('function reconcilePaneTabChildren(', start);
    assert.ok(start > 0 && end > start, 'could not locate updatePaneTabStrip body');
    const body = source.slice(start, end);
    assert.equal(body.includes('replaceChildren(...'), false, 'routine pane-tab refresh must reconcile instead of rebuilding hovered tabs');
    assert.ok(source.includes('function reconcilePaneTabChildren('), 'pane-tab reconcile helper exists');
    assert.ok(source.includes('function paneTabShouldPreserve('), 'open pane-tab popovers keep their existing node');
    // The per-pane left-dropdown caret was removed (it wasted a line above the tab strip) — keep it gone.
    assert.equal(source.includes('pane-tabs-menu-caret'), false, 'no per-pane dropdown caret (removed by request)');
  });

  test('t@2082', () => {
    const api = loadYolomux('', ['1']);
    const tab = new TestElement('tab');
    const popover = new TestElement('popover');
    tab.className = 'pane-tab popover-open';
    tab.classList.add('pane-tab', 'popover-open');
    tab.dataset.paneTab = '1';
    tab.dataset.popoverHoverState = 'closing';
    tab.querySelector = selector => selector.includes('session-popover') ? popover : null;
    assert.equal(api.paneTabShouldPreserve(tab), true);
    const detachedTab = new TestElement('detached-tab');
    const detachedPopover = new TestElement('detached-popover');
    detachedTab.classList.add('pane-tab', 'dockview-pane-tab', 'popover-open');
    detachedTab.dataset.paneTab = '1';
    detachedTab.dataset.popoverHoverState = 'open';
    detachedTab.__yolomuxDetachedPopover = detachedPopover;
    detachedTab.querySelector = () => null;
    assert.equal(api.paneTabShouldPreserve(detachedTab), true, 'Dockview detached popovers keep their tab renderer from rebuilding');

    const fresh = new TestElement('fresh');
    fresh.className = 'pane-tab active';
    fresh.role = 'button';
    fresh.tabIndex = 0;
    fresh.draggable = true;
    fresh.dataset.paneTab = '1';
    fresh.getAttribute = name => name === 'aria-label' ? 'Session 1' : undefined;
    api.syncPreservedPaneTab(tab, fresh);
    assert.ok(tab.className.includes('popover-open'));
    assert.equal(tab.dataset.popoverHoverState, 'closing');
    assert.equal(tab.getAttribute('aria-label'), 'Session 1');
  });

  test('t@2114', () => {
    // GLOBAL activity status line: cross-session YOLO-screen RUN / ASK? / blocked / idle rollup.
    const api = loadYolomux('', ['1', '2', '3', '4']);
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'idle'}});
    api.setAutoApproveStateForTest('2', {enabled: true});
    const idleEnabledCounts = api.globalActivityCounts();
    assert.equal(idleEnabledCounts.running, 0, 'YOLO enabled but idle does not count as running');
    assert.equal(idleEnabledCounts.ask, 0, 'YOLO enabled but idle does not count as ASK?');
    assert.equal(idleEnabledCounts.blocked, 0, 'YOLO enabled but idle does not count as blocked');
    assert.equal(api.browserFaviconBadgeCount(idleEnabledCounts), 0, 'favicon badge is 0 when enabled sessions are idle');
    const idleHtml = api.globalActivityStatusLineHtml();
    assert.ok(/0 RUN/.test(idleHtml) && /0 ASK\?/.test(idleHtml) && /0 blocked/.test(idleHtml), 'status line shows explicit zero RUN / ASK? / blocked counts');
    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'working'}});
    api.setAutoApproveStateForTest('2', {enabled: true, screen: {key: 'needs-input'}});
    api.setAutoApproveStateForTest('3', {enabled: true, screen: {key: 'blocked'}});
    api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'idle'}});
    const counts = api.globalActivityCounts();
    assert.equal(counts.running, 1, 'one working session counts as running');
    assert.equal(counts.ask, 1, 'one needs-input session counts as ASK?');
    assert.equal(counts.blocked, 1, 'one blocked session counts as blocked');
    assert.equal(counts.attention, 2, 'attention is ASK? plus blocked');
    assert.equal(counts.idle, 1, 'the remaining session is idle');
    assert.equal(counts.total, 4, 'all tmux sessions are counted');
    assert.equal(api.browserFaviconBadgeCount(counts), 1, 'favicon badge counts actively running sessions only');
    assert.equal(api.browserFaviconBadgeCount({running: 0, attention: 1}), 0, 'favicon badge does not count attention-only sessions as active');
    assert.equal(api.browserFaviconBadgeCount({running: 0, attention: 0}), 0, 'favicon badge renders 0 when all sessions are idle');
    assert.equal(api.browserFaviconBadgeLabel(0), '0', 'favicon badge explicitly shows 0 when idle');
    assert.equal(api.browserFaviconBadgeLabel(107), '99+', 'favicon badge clamps large counts to a short label');
    const signalApi = loadYolomux('', ['1', '2']);
    signalApi.setDocumentTitleNowForTest(200000);
    signalApi.setTmuxSignalStateForTest({
      windows: [
        {session: 'background-agent', window_index: '0', activity_ts: 199},
        {session: 'old-output', window_index: '0', activity_ts: 10},
      ],
    });
    signalApi.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'working'}});
    signalApi.setAutoApproveStateForTest('2', {enabled: true, screen: {key: 'blocked'}});
    const signalCounts = signalApi.globalActivityCounts();
    assert.equal(signalCounts.running, 2, 'server-wide tmux signals count a recent active window outside the current tab plus YO-active sessions');
    assert.equal(signalCounts.blocked, 1, 'attention counts still come from YO screen state');
    assert.equal(signalCounts.total, 4, 'signal windows and configured tmux sessions share one total without requiring every window in the tab URL');
    assert.equal(signalCounts.idle, 1, 'an old tmux window outside the activity window remains idle');
    assert.equal(signalApi.browserFaviconBadgeCount(signalCounts), 2, 'favicon badge counts server-wide active windows');
    signalApi.setTmuxSignalStateForTest({
      windows: [
        {key: 'background-agent:0', session: 'background-agent', window_index: '0', activity_ts: 10, bell_flag: true},
      ],
    });
    const bellCounts = signalApi.globalActivityCounts();
    assert.equal(bellCounts.ask, 1, 'tmux bell windows count as ASK? attention');
    assert.equal(bellCounts.attention, 2, 'tmux bell attention combines with YO blocked attention');
    signalApi.setTmuxSignalStateForTest({
      windows: [{
        key: 'codex-action:0',
        session: 'codex-action',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        panes: [{
          window_key: 'codex-action:0',
          session: 'codex-action',
          window_index: '0',
          current_command: 'node',
          title: '[ . ] Action Required | yolomux.dev8001',
          dead: false,
        }],
      }],
    });
    const actionRequiredCounts = signalApi.globalActivityCounts();
    assert.equal(actionRequiredCounts.ask, 1, 'Codex action-required pane titles count as ASK? attention');
    assert.equal(actionRequiredCounts.attention, 2, 'Codex action-required attention combines with YO blocked attention');
    const signalClearApi = loadYolomux('', ['1']);
    signalClearApi.setAutoApproveStateForTest('1', {
      enabled: true,
      prompt: {visible: true, yes_selected: true, text: 'Would you like to run sleep 10?', signature: 'codex-sleep-10'},
      screen: {key: 'approval'},
    });
    signalClearApi.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          current_command: 'node',
          title: '[ . ] Action Required | yolomux.dev8001',
          dead: false,
        }],
      }],
    });
    assert.equal(signalClearApi.globalActivityCounts().ask, 1, 'tmux title and prompt payload for the same session count once');
    assert.equal(signalClearApi.clearPromptAttentionForSessionForTest('1'), true, 'manual clear records the signal-backed prompt signature');
    assert.equal(signalClearApi.globalActivityCounts().ask, 0, 'manual clear suppresses signal-backed prompt attention');
    const approvalApi = loadYolomux('', ['1', '2', '3']);
    approvalApi.setAutoApproveStateForTest('1', {
      enabled: true,
      prompt: {visible: true, yes_selected: true, text: 'Would you like to run sleep 10?', signature: 'codex-sleep-10'},
      screen: {key: 'idle'},
    });
    approvalApi.setAutoApproveStateForTest('2', {
      enabled: true,
      prompt: {visible: true, yes_selected: false, text: 'Which backend should I use?', signature: 'question-backend'},
      screen: {key: 'idle'},
    });
    approvalApi.setAutoApproveStateForTest('3', {
      enabled: true,
      screen: {key: 'approval', text: 'Do you want to proceed?', signature: 'screen-approval'},
    });
    const approvalCounts = approvalApi.globalActivityCounts();
    assert.equal(approvalCounts.ask, 3, 'visible approval prompts and questions all count as ASK?');
    approvalApi.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          current_command: 'node',
          title: '[ . ] Action Required | yolomux.dev8001',
          dead: false,
        }],
      }],
    });
    const dedupedApprovalCounts = approvalApi.globalActivityCounts();
    assert.equal(dedupedApprovalCounts.ask, 3, 'tmux action-required title and prompt payload for the same session count once');
    approvalApi.setTmuxSignalStateForTest({windows: []});
    const approvalState = approvalApi.sessionState('1', {agents: [{kind: 'codex'}], panes: []});
    assert.equal(approvalState.key, 'needs-approval', 'auto-enabled visible approval still surfaces attention until it is handled');
    assert.equal(approvalState.short, 'ASK?');
    const approvalBadge = approvalApi.sessionStateHtml(approvalState);
    assert.ok(approvalBadge.includes('ASK?'), 'approval prompt badge uses the ASK? contract');
    assert.ok(approvalBadge.includes('data-prompt-attention-clear="1"'), 'approval prompt badge can be manually cleared');
    const attentionSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/needs-input-pane', state\.key === 'needs-input' && state\.attention === true/.test(attentionSource), 'pane input ring only appears for uncleared ASK? attention');
    assert.ok(/needs-exec-pane', state\.key === 'needs-approval' && state\.attention === true/.test(attentionSource), 'pane approval ring only appears for uncleared ASK? attention');
    assert.equal(approvalApi.clearPromptAttentionForSessionForTest('1'), true, 'manual clear records the current prompt signature');
    assert.equal(approvalApi.globalActivityCounts().ask, 2, 'manual clear removes only the acknowledged prompt from ASK? count');
    const clearedState = approvalApi.sessionState('1', {agents: [{kind: 'codex'}], panes: []});
    assert.equal(clearedState.attention, false, 'manual clear suppresses the current attention signal');
    assert.equal(approvalApi.sessionStateHtml(clearedState), '', 'manual clear hides the current ASK? badge');
    approvalApi.setAutoApproveStateForTest('1', {
      enabled: true,
      prompt: {visible: true, yes_selected: true, text: 'Would you like to run pwd?', signature: 'codex-pwd'},
      screen: {key: 'idle'},
    });
    assert.equal(approvalApi.globalActivityCounts().ask, 3, 'changed prompt signature re-arms ASK?');
    assert.ok(approvalApi.sessionStateHtml(approvalApi.sessionState('1', {agents: [{kind: 'codex'}], panes: []})).includes('ASK?'), 'changed prompt shows the ASK? badge again');
    assert.equal(approvalApi.clearPromptAttentionForSessionForTest('1'), true, 'manual ASK? clear handles the re-armed prompt');
    assert.equal(approvalApi.globalActivityCounts().ask, 2, 'manual ASK? clear removes the re-armed prompt attention');
    const promptClearHandlerStart = attentionSource.indexOf('function handlePromptAttentionClearEvent(event)');
    const promptClearHandlerEnd = attentionSource.indexOf("document.addEventListener('click', handlePromptAttentionClearEvent)", promptClearHandlerStart);
    assert.ok(promptClearHandlerStart > 0 && promptClearHandlerEnd > promptClearHandlerStart, 'manual ASK? clear handler is present in the boot bundle');
    const promptClearHandlerBody = attentionSource.slice(promptClearHandlerStart, promptClearHandlerEnd);
    assert.ok(promptClearHandlerBody.includes('clearPromptAttentionForSession'), 'manual ASK? clear uses the prompt-attention clear path');
    assert.equal(/socket\.send|handleTerminalData|insertIntoTerminal|paste/.test(promptClearHandlerBody), false, 'manual ASK? clear handler does not send keystrokes or paste frames to tmux');
    const falsePositiveApi = loadYolomux('', ['1', '2', '3', '4', '8001', '8002', '8003', '9']);
    for (const session of ['1', '2', '3', '4', '8001', '8002', '8003', '9']) {
      falsePositiveApi.setAutoApproveStateForTest(session, {screen: {key: 'idle'}});
      falsePositiveApi.setTranscriptInfoForTest(session, {agents: [], panes: [{active: true, window_active: true}]});
    }
    const idlePaneCounts = falsePositiveApi.globalActivityCounts();
    assert.equal(idlePaneCounts.running, 0, 'tmux selected panes are not active work');
    assert.equal(idlePaneCounts.idle, 8, 'all eight selected-pane sessions remain idle');
    assert.equal(falsePositiveApi.browserFaviconBadgeCount(idlePaneCounts), 0, 'favicon does not show 8 for idle tmux sessions');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('browserFaviconRoundedRect(ctx, 2, 2, 60, 60, 10)') && source.includes("ctx.fillStyle = '#99d441'"), 'favicon fills the icon with the lime YO tile instead of a dark border');
    assert.ok(source.includes("ctx.font = '900 86px Arial, sans-serif'") && source.includes('ctx.scale(1.22, 1)') && source.includes("ctx.fillText('Y', 0, 0)"), 'favicon fills the tile with a large Y');
    assert.ok(source.includes("ctx.font = label.length > 2 ? '900 24px Arial, sans-serif' : label.length > 1 ? '900 32px Arial, sans-serif' : '900 42px Arial, sans-serif'") && source.includes("ctx.strokeText(label, 62, 50)") && source.includes("ctx.fillText(label, 62, 50)"), 'favicon overlays a prominent active count at the bottom-right');
    const html = api.globalActivityStatusLineHtml();
    assert.ok(/1 RUN/.test(html) && /topbar-activity-run active/.test(html), 'status line shows running count');
    assert.ok(/1 ASK\?/.test(html) && /topbar-activity-ask topbar-activity-attn/.test(html), 'status line shows ASK? count');
    assert.ok(/1 blocked/.test(html) && /topbar-activity-blocked topbar-activity-attn/.test(html), 'status line shows blocked count');
    assert.ok(/status-indicator[^"]*topbar-activity-ask[^"]*attention-pulse/.test(html), 'topbar ASK? inherits the shared status indicator pulse parent');
    assert.ok(/status-indicator[^"]*topbar-activity-blocked[^"]*attention-pulse/.test(html), 'topbar blocked inherits the shared status indicator pulse parent');
    assert.ok(/1 idle/.test(html), 'status line shows the idle count');
    assert.ok(/\.topbar-activity\s*\{/.test(css), 'the top-bar activity line is styled');
    assert.ok(/\.topbar-activity-run\.active/.test(css), 'RUN only turns green when nonzero');
    assert.ok(/\.topbar-activity\.has-attention/.test(css), 'the activity line highlights when a session needs the user');
  });

  test('t@2134', () => {
    // Event-driven session-kill: a terminal WS close roster-confirms gone vs transient disconnect.
    const api = loadYolomux('', ['1', '2', '3']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(api.sessionConfirmedGone('2', ['1', '3']), true, 'a tmux session absent from the roster is confirmed gone');
    assert.equal(api.sessionConfirmedGone('2', ['1', '2', '3']), false, 'a session still in the roster is a transient disconnect, not gone');
    assert.equal(api.sessionConfirmedGone('2', null), false, 'a failed/empty roster fetch never declares a session gone (reconnect instead)');
    assert.equal(api.sessionConfirmedGone(api.fileEditorItemFor('/x/y.txt'), []), false, 'non-tmux items are never roster-pruned');
    assert.ok(source.includes('confirmSessionGoneOrReconnect(session, item);'), 'terminal WS close roster-confirms before reconnecting');
    assert.ok(/sessionConfirmedGone\(session, order\)\)\s*\{\s*pruneDeadSession\(session\);/.test(source), 'a confirmed-gone session is pruned immediately');
    assert.ok(/scheduleTerminalReconnect\(session, item\);\s*\}\s*$/m.test(source) || source.includes('scheduleTerminalReconnect(session, item);'), 'a transient disconnect still reconnects');
  });

  test('t@2147', () => {
    // T5: stopping a session clears every session-keyed UI map and closes live streams.
    const api = loadYolomux('', ['1']);
    api.registerTerminalForTest('1', {dispose() {}}, {readyState: 1, close() {}});
    const closed = api.seedSessionTeardownStateForTest('1');
    api.stopSessionUiForTest('1');
    assert.deepEqual(api.sessionTeardownStateForTest('1'), {
      terminal: false,
      transcript: false,
      summary: false,
      autoApprove: false,
      uploads: false,
      uploadTimer: false,
    }, 'stopSessionUi clears all session-keyed UI state');
    assert.deepEqual(closed, {transcript: 1, summary: 1}, 'stopSessionUi closes both EventSource streams');
  });

  test('t@2164', () => {
    // Git-aware Finder: repo-dir inline annotation (branch + ahead/behind/dirty) + hover popover.
    const api = loadYolomux('', ['1']);
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    api.setFileExplorerRepoInfoForTest('/repo/app', {root: '/repo/app', name: 'app', branch: 'feature/x', upstream: 'origin/feature/x', ahead: 2, behind: 1, dirty_count: 3});
    const sync = api.fileTreeRepoSyncMeta('/repo/app').map(part => part.text);
    assert.deepStrictEqual([...sync], ['↑2', '↓1', '●3'], 'repo sync meta shows ahead/behind/dirty markers');
    const parts = api.fileTreeDisplayParts('/repo/app', {kind: 'dir', is_repo: true, name: 'app'});
    assert.ok(parts.html.includes('file-tree-repo-branch') && parts.html.includes('feature/x'), 'repo dir annotation shows the branch');
    assert.ok(parts.html.includes('file-tree-repo-ahead') && parts.html.includes('↑2'), 'repo dir annotation shows ahead count inline');
    assert.ok(parts.html.includes('file-tree-repo-dirty') && parts.html.includes('●3'), 'repo dir annotation shows dirty count inline');
    // Rich hover popover (replaces the native title tooltip) carries branch / upstream / stat / path.
    const pop = api.repoInfoPopoverHtml({root: '/repo/app', name: 'app', branch: 'feature/x', upstream: 'origin/feature/x', ahead: 2, behind: 1, dirty_count: 3});
    assert.ok(pop.includes('feature/x') && pop.includes('2 ahead') && pop.includes('1 behind') && pop.includes('3 dirty'), 'repo popover shows branch + ahead/behind/dirty');
    assert.ok(pop.includes('/repo/app'), 'repo popover shows the repo root path');
    assert.equal(api.repoInfoPopoverHtml({}), '', 'no popover html without a repo root');
    assert.ok(/\.file-tree-repo-popover\s*\{[^}]*position:\s*fixed/.test(css), 'the repo hover popover is a styled floating element');
    assert.ok(/\.session-popover\s*\{[\s\S]*background:\s*var\(--panel2\)[\s\S]*border:\s*1px solid var\(--active-control-soft-border\)/.test(css), 'session/tab popovers use a neutral card with theme-accent border, not a hardcoded green card');
    assert.ok(/\.popover-label\s*\{[\s\S]*color:\s*var\(--active-accent-bright\)/.test(css), 'session/tab popover labels follow the active theme color');
    assert.equal(/#081f08|#bddcaf|#a2c98d|rgba\(118,\s*185,\s*0,\s*0\.72\)/.test(css), false, 'old hardcoded green popup palette is gone');
    assert.ok(/\.file-tree-repo-ahead/.test(css) && /\.file-tree-repo-dirty/.test(css), 'inline ahead/dirty markers are styled');
  });

  test('t@2187', () => {
    // Max tabs per pane + LRU eviction (Preference).
    const api = loadYolomux('', ['1', '2', '3', '4', '5']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 3}});
    assert.equal(api.maxTabsPerPane(), 3, 'tab cap reads appearance.max_tabs_per_pane');
    api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 999}});
    assert.equal(api.maxTabsPerPane(), 30, 'tab cap is clamped to 30');
    api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 1}});
    assert.equal(api.maxTabsPerPane(), 2, 'tab cap is clamped to a minimum of 2');
    api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 3}});
    // Five tmux tabs, '5' is the active/newest; evict the 2 least-recently-used others.
    const tabs = ['1', '2', '3', '4', '5'];
    api.setTabLastActivatedForTest('1', 10);
    api.setTabLastActivatedForTest('2', 50);
    api.setTabLastActivatedForTest('3', 20);
    api.setTabLastActivatedForTest('4', 90);
    api.setTabLastActivatedForTest('5', 100);
    assert.deepStrictEqual([...api.tabsToEvictForCap(tabs, '5')], ['1', '3'], 'LRU eviction drops the two oldest non-active tabs');
    assert.deepStrictEqual([...api.tabsToEvictForCap(['1', '2'], '2')], [], 'no eviction when the pane is within the cap');
    // A dirty editor tab is never evicted; the next LRU tab goes instead.
    const editorItem = api.fileEditorItemFor('/x/draft.txt');
    api.setOpenFileStateForTest('/x/draft.txt', {kind: 'text', dirty: true, content: 'x', original: ''});
    api.setTabLastActivatedForTest(editorItem, 1);  // oldest, but dirty -> protected
    const withDirty = ['1', '2', editorItem, '5'];
    api.setClientSettingsPatchForTest({appearance: {max_tabs_per_pane: 2}});
    const evicted = api.tabsToEvictForCap(withDirty, '5');
    assert.ok(!evicted.includes(editorItem), 'a dirty/unsaved editor tab is never auto-closed');
    assert.ok(evicted.includes('1'), 'the oldest non-dirty tab is evicted instead of the dirty editor');
    api.setPinnedTabsForTest(['1']);
    assert.deepStrictEqual([...api.paneStateWithTabs(['2', '1', '3'], '3').tabs], ['1', '2', '3'], 'pinned tabs normalize to the front of their pane');
    assert.deepStrictEqual([...api.orderPaneTabs(['2', '1', '3'])], ['1', '2', '3'], 'shared tab-order helper preserves pinned-first ordering');
    const rawPinnedSlots = api.emptyLayoutSlots();
    rawPinnedSlots[api.layoutTreeKey] = api.leafNode('left');
    rawPinnedSlots.left = {tabs: ['2', '1', '3'], active: '3'};
    assert.deepStrictEqual([...api.normalizeLayoutSlots(rawPinnedSlots).left.tabs], ['1', '2', '3'], 'raw layout normalization also enforces pinned-first ordering');
    assert.ok(api.pinnedTabIconHtml('1').includes('pane-tab-pin-icon'), 'pinned tabs render a pin icon helper');
    assert.deepStrictEqual([...api.tabsToEvictForCap(['1', '2', '3', '4'], '4')], ['3', '2'], 'LRU eviction skips pinned tabs');
    assert.equal(api.isPinnableTab(api.fileExplorerItemId), false, 'Finder/Differ is not pinnable');
    assert.equal(api.isPinnableTab('2'), true, 'normal tmux tabs are pinnable');
    api.setPinnedTabsForTest([]);
    assert.ok(source.includes('const evicted = tabsToEvictForCap(tabs, session);'), 'moveSessionToSlot enforces the tab cap when a tab joins a pane');
  });

  test('t@2231', () => {
    // the session popover shows review status AND who reviewed.
    const api = loadYolomux('', ['5']);
    const approved = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'APPROVED', review_reviewers: [{login: 'alice', state: 'APPROVED'}]});
    assert.ok(/Approved by alice/.test(approved) && /pr-status-passing/.test(approved), 'popover shows "Approved by <login>"');
    const changes = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'CHANGES_REQUESTED', review_reviewers: [{login: 'bob', state: 'CHANGES_REQUESTED'}]});
    assert.ok(/Changes requested by bob/.test(changes) && /pr-status-failing/.test(changes), 'popover shows "Changes requested by <login>"');
    const required = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'REVIEW_REQUIRED', review_reviewers: []});
    assert.ok(/Review required/.test(required), 'popover shows "Review required" when unreviewed');
    assert.equal(api.pullRequestReviewInlineHtml({number: 12}), '', 'no review part without a decision');
    // Approved with no reviewer login still renders the status without a dangling "by".
    const approvedNoWho = api.pullRequestReviewInlineHtml({number: 12, review_decision: 'APPROVED', review_reviewers: []});
    assert.ok(/Approved</.test(approvedNoWho) && !/ by /.test(approvedNoWho), 'approved with no reviewer omits the "by" clause');
  });

  test('t@2246', () => {
    // #1/#2/#3: tab badge legibility (light), drop the redundant PR pill, no duplicate tooltip.
    const api = loadYolomux('', ['5']);
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    // #1: light-theme overrides exist for the badge chips, incl. a readable (non-transparent) review-required.
    assert.ok(/\.ci-indicator\.pr-review-required\s*\{[^}]*color:\s*#172033[\s\S]*background:\s*#e7ebf1[\s\S]*opacity:\s*1/.test(css), '#6: review-required chip is filled with dark text, readable on bright active tabs');
    assert.ok(/body\.theme-light \.ci-indicator\.pr-review-required\s*\{[^}]*background:\s*#e7ebf1/.test(css), '#6: review-required chip is legible (light fill) in light theme');
    assert.ok(/body\.theme-light \.ci-indicator\.pr-number-chip/.test(css) && /body\.theme-light \.ci-indicator\.pr-review-approved/.test(css), '#6: light-theme overrides cover number + review chips');
    // #2: the ready-review "PR" state pill is dropped (PR chips convey it now); other states still render.
    assert.equal(api.sessionStateHtml({key: 'ready-review', short: 'PR', label: 'Ready for review', reason: 'checks pass'}), '', '#7: the redundant ready-review PR pill is suppressed');
    const needsInputBadge = api.sessionStateHtml({key: 'needs-input', short: '?', label: 'Needs input', reason: 'waiting'});
    assert.ok(needsInputBadge.includes('session-state-needs-input'), '#7: actionable states still render a badge');
    assert.ok(/status-indicator[^"]*status-indicator--text[^"]*session-state-needs-input[^"]*status-indicator--attention[^"]*attention-pulse/.test(needsInputBadge), '#7: ASK? badges inherit shared status indicator text and pulse behavior');
    // #3: tab badge chips carry no native title (the custom popover is the single source).
    assert.ok(!api.pullRequestNumberIndicatorHtml('5', {number: 123}).includes('title='), '#8: the PR number chip has no native title tooltip');
    assert.ok(!api.pullRequestApprovalIndicatorHtml('5', {number: 123, state: 'open', review_decision: 'APPROVED'}).includes('title='), '#8: the approval chip has no native title tooltip');
    assert.ok(!api.pullRequestCompactBadgesHtml('5', {number: 123, state: 'open', review_decision: 'APPROVED'}).includes('title='), '#8: the compact PR badge row has no native title tooltips');
    const mergedBadges = api.pullRequestCompactBadgesHtml('5', {number: 123, merged: true});
    assert.ok(/pr-number-chip pr-status-merged/.test(mergedBadges) && />#123</.test(mergedBadges), 'merged PR tab uses one purple #number chip');
    assert.equal(mergedBadges.includes('MERGED'), false, 'merged PR tab omits the redundant MERGED text badge');
    assert.equal(api.pullRequestLinkLabel({number: 123, merged: true}), '#123', 'merged PR inline labels omit redundant MERGED text');
    assert.equal(api.pullRequestLinkLabel({number: 123, state: 'open'}), '#123', 'open PR inline labels omit redundant OPEN text');
  });

  test('t@2268', () => {
    // #38: tab APPROVAL badge driven by GitHub reviewDecision.
    const api = loadYolomux('', ['5']);
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    const approved = api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open', review_decision: 'APPROVED'});
    assert.ok(/pr-review-approved/.test(approved) && />Approved</.test(approved), '#38: approved PR shows a green Approved badge');
    const changes = api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open', review_decision: 'CHANGES_REQUESTED'});
    assert.ok(/pr-review-changes/.test(changes) && />Changes</.test(changes), '#38: changes-requested PR shows a red Changes badge');
    const required = api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open', review_decision: 'REVIEW_REQUIRED'});
    assert.ok(/pr-review-required/.test(required) && />Review</.test(required), '#38: review-required PR shows the neutral/crossed-out badge');
    // No badge without a PR, without a decision, or once merged.
    assert.equal(api.pullRequestApprovalIndicatorHtml('5', {number: 12345, state: 'open'}), '', '#38: no review badge when reviewDecision is absent');
    assert.equal(api.pullRequestApprovalIndicatorHtml('5', null), '', '#38: no review badge without a PR');
    assert.equal(api.pullRequestApprovalIndicatorHtml('5', {number: 12345, merged: true, review_decision: 'APPROVED'}), '', '#38: no review badge once the PR is merged');
    // The compact badge row carries the approval badge alongside #/CI.
    assert.ok(/pr-review-approved/.test(api.pullRequestCompactBadgesHtml('5', {number: 12345, state: 'open', review_decision: 'APPROVED'})), '#38: the compact PR badge row includes the approval badge');
    assert.ok(/\.ci-indicator\.pr-review-approved/.test(css), '#38: approval badge has an approved (green) color class');
    assert.ok(/\.ci-indicator\.pr-review-required[\s\S]*?line-through/.test(css), '#38: the review-required badge is crossed out');
  });

  test('t@2288', () => {
    const api = loadYolomux('', ['1']);
    const strip = new TestElement('strip');
    const tab = new TestElement('tab');
    const popover = new TestElement('popover');
    strip.className = 'pane-tabs';
    tab.className = 'pane-tab popover-open';
    tab.dataset.paneTab = '1';
    tab.dataset.popoverHoverState = 'open';
    popover.className = 'session-popover';
    strip.appendChild(tab);
    tab.appendChild(popover);
    api.restorePaneTabPopover(strip, '1');
    assert.equal(api.paneTabPopoverItemToRestore(strip), '1');
    assert.equal(api.bodyChildren().length, 0);
    assert.equal(api.paneTabShouldPreserve(tab), true);
  });

  test('t@2306', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const start = source.indexOf('function renderTreeChildren(');
    const end = source.indexOf('function rawFileUrl(', start);
    assert.ok(start > 0 && end > start, 'could not locate renderTreeChildren body');
    const body = source.slice(start, end);
    assert.equal(body.includes('replaceChildren(...nextNodes)'), false, 'Finder refresh must reconcile existing rows instead of rebuilding them');
    assert.equal(source.includes('function differFileTreeHtmlStr'), false, 'Differ must not maintain a parallel file-tree HTML renderer');
    assert.equal(source.includes('function changesGroupsHtmlStr'), false, 'Differ groups must render through the shared DOM renderer, not a duplicate HTML renderer');
    assert.equal(/function renderChangesPanels[\s\S]*renderChangesRoot\(/.test(source), false, 'standalone Changes render loop is removed');
    assert.ok(/function renderFileExplorerChangesPanel[\s\S]*renderChangesRoot\(/.test(source), 'Finder diff panel / embedded Differ refreshes through the shared incremental render root');
    assert.ok(source.includes('function updateFileTreeRowContents('), 'Finder row text/icon updates are localized');
    assert.ok(source.includes('function fileTreeRowDerivedState(') && source.includes('function applyFileTreeRowDerivedState('), 'R3: Finder derived row state has one builder and one applier');
    assert.ok(/function updateFileTreeRow\([\s\S]*buildFileTreeRowState\(fullPath, entry, depth, options\)[\s\S]*applyFileTreeRowDerivedState\(row, rowState\.derivedState\)/.test(source), 'R3/RA4: full Finder/Differ render applies the shared derived row state through the row-state builder');
    assert.ok(/function updateFileTreeGitStatusRows\(\)[\s\S]*applyFileTreeRowDerivedState\(row, fileTreeRowDerivedState\(fullPath, entry,/.test(source), 'R3: lightweight Finder refresh applies the shared derived row state');
    assert.equal(/const gitStatus = row\.dataset\.kind === 'file' \? fileTreeGitStatus/.test(source), false, 'R3: lightweight Finder refresh no longer has its own git-status derivation fork');
    const updateStart = source.indexOf('function updateFileTreeRowContents(');
    const updateEnd = source.indexOf('function updateFileTreeRow(', updateStart);
    assert.ok(updateStart > 0 && updateEnd > updateStart, 'could not locate updateFileTreeRowContents body');
    const updateBody = source.slice(updateStart, updateEnd);
    assert.equal(updateBody.includes("name.textContent = nameText;\n    name.innerHTML = '';"), false, 'Finder row text must not be cleared after being written');
    assert.ok(updateBody.includes("name.innerHTML = '';\n    name.textContent = nameText;"), 'Finder row clears stale HTML before writing plain text');
    const sharedTreeCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.file-tree-row\.has-agent \.file-tree-name\s*\{[^}]*flex:\s*0 1 auto[\s\S]*min-width:\s*0/.test(sharedTreeCss), 'file-tree rows with agent metadata keep the AI marker directly beside the filename while allowing the filename to ellipsize');
    assert.equal(/\.file-tree-row\.has-agent \.file-tree-agent\s*\{[^}]*margin-inline-end:\s*auto/.test(sharedTreeCss), false, 'file-tree agent metadata must not use auto-margin that can push date columns out of view');
    assert.ok(/\.changes-file-list\s*\{[^}]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)[\s\S]*min-inline-size:\s*0/.test(sharedTreeCss), 'Differ file-list grid constrains shared file-tree rows to the pane width instead of max-content clipping the date column');
    assert.ok(/\.file-tree-row:has\(> \.file-tree-diff:not\(\[hidden\]\)\) > \.file-tree-diff,[\s\S]*?margin-inline-start:\s*auto/.test(sharedTreeCss), 'Finder/Differ rows push the diff column, not the AI marker, to the right edge when diff data exists');
    assert.ok(/--file-tree-row-control-size:\s*calc\(var\(--file-explorer-font-size\) \+ 2px\)/.test(sharedTreeCss), 'Finder/Differ row controls scale from the Finder font size');
    assert.ok(/--file-tree-icon-size:\s*calc\(var\(--file-explorer-font-size\) \+ 2px\)/.test(sharedTreeCss), 'Finder/Differ icon boxes scale from the Finder font size');
    assert.equal(/--file-tree-row-control-size:\s*max\(18px/.test(sharedTreeCss), false, 'Finder/Differ row controls must not keep the old fixed 18px minimum');
    assert.ok(/\.file-tree-icon\s*\{[^}]*display:\s*inline-flex[\s\S]*flex:\s*0 0 var\(--file-tree-icon-size\)[\s\S]*font-size:\s*var\(--file-tree-icon-font-size\)[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ file icons use a centered box that follows the Finder font size');
    assert.ok(/\.file-tree-agent \.agent-icon\s*\{[^}]*width:\s*calc\(var\(--file-explorer-font-size\) \+ 3px\)[\s\S]*height:\s*calc\(var\(--file-explorer-font-size\) \+ 1px\)/.test(sharedTreeCss), 'Finder/Differ AI markers scale with the Finder font size instead of pinning the row height');
    assert.ok(/\.file-tree-diff\s*\{[^}]*justify-content:\s*flex-end[\s\S]*flex:\s*0 0 6\.5ch[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ diff counts reserve one shared column before the git status badge');
    assert.ok(/\.file-tree-git-status\s*\{[^}]*display:\s*inline-flex[\s\S]*align-items:\s*center[\s\S]*justify-content:\s*center[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ git status badges use the same centered box');
    assert.ok(/\.file-tree-git-status\s*\{[^}]*overflow:\s*hidden[\s\S]*white-space:\s*nowrap/.test(sharedTreeCss), 'Finder/Differ status badges cannot spill into the date column');
    assert.ok(/\.file-tree-date\s*\{[^}]*display:\s*inline-flex[\s\S]*align-items:\s*center[\s\S]*line-height:\s*1/.test(sharedTreeCss), 'Finder/Differ date cells align to the icon/status centerline');
    assert.ok(/\.file-tree-date\s*\{[^}]*justify-content:\s*flex-end[\s\S]*flex:\s*0 0 var\(--file-tree-date-column-width\)[\s\S]*inline-size:\s*var\(--file-tree-date-column-width\)/.test(sharedTreeCss), 'Finder/Differ date cells reserve a fixed right column so status badges line up');
    assert.ok(/\.file-tree-dir-count\[hidden\],[\s\S]*?\.file-tree-date\[hidden\]\s*\{[\s\S]*display:\s*none/.test(sharedTreeCss), '#46: hidden Finder/Differ metadata cells do not reserve their right-side columns');
    assert.ok(source.includes("refreshActivitySummary({force: true})"), 'YO!agent Refresh summary forces cached summaries to rebuild');
    assert.ok(source.includes("params.set('force', '1')"), 'YO!agent summary API supports a force refresh query');
    assert.ok(source.includes("params.set('locale', i18nActiveLocaleId())"), 'YO!agent summary API carries the active locale query');
    assert.ok(source.includes("data-yolo-rule-open"), 'Preferences exposes an Open button for the YOLO rule file');
    assert.ok(source.includes("apiFetchJson('/api/yoagent/reset'"), 'YO!agent clear conversation resets the server-side CLI session');
    assert.ok(source.includes("apiFetchJson('/api/yoagent/conversation'"), 'YO!agent hydrates chat history from the server-side conversation transcript');
    assert.ok(source.includes("transcript_display_path"), 'YO!agent conversation payload carries the transcript display path');
    assert.ok(source.includes("pathCopyButtonHtml(path, {className: 'yoagent-transcript-copy'"), 'YO!agent transcript path renders with the shared copy button');
    assert.ok(source.includes("renderYoagentPanel({preserveDraft: false, scrollBottom: true})"), 'YO!agent send/clear clears the draft and scrolls chat to the bottom');
    assert.ok(source.includes("'yoagent_conversation_changed'") && source.includes('loadYoagentConversation({force: true'), 'YO!agent background result pushes refresh the persisted conversation');
    assert.equal(source.includes('yoagentSessionSummariesHtml'), false, 'YO!agent default panel does not render the per-session SESSION detail card list');
    assert.ok(source.includes("row.draggable = entry.kind === 'file' || entry.kind === 'dir';") && source.includes("setRowDataset(row, 'openChangeFile', changedFile?.abs_path || '')"), 'Modified-files rows use the shared tree renderer and remain draggable as file payloads');
    assert.ok(source.includes("event.dataTransfer.setData('application/x-yolomux-file'"), 'Modified-files drag carries the same file payload as Finder drag');
    assert.ok(source.includes("'Allow index'"), 'Finder directory context menu exposes Allow index');
    assert.ok(source.includes("'Disallow index'"), 'Finder directory context menu exposes Disallow index');
    assert.ok(source.includes("row.classList.toggle('indexed-directory', state.indexedDirectory)"), 'Finder row render marks indexed directories');
    assert.ok(source.includes("'file-icon-dir-indexed'"), 'Finder indexed directories use a distinct icon class');
  });

  test('t@2355', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.fileImagePreviewMinShowDelayMs, 800);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(source.includes('Math.max(fileImagePreviewMinShowDelayMs, tabPopoverShowDelayMs)'), 'image previews share the tab-style delayed hover threshold');
    assert.ok(css.includes('--file-image-preview-max-size: 320px'), 'Finder image preview default max size is tokenized');
    assert.ok(/\.file-image-preview-popover[\s\S]*pointer-events:\s*none/.test(css), 'Finder image previews cannot keep themselves hovered over terminals');
    assert.ok(source.includes('function installPreviewZoomSurface'), 'visual previews share one zoom installer');
    assert.ok(source.includes('const previewZoomPolicy = Object.freeze'), 'visual preview zoom limits are owned by one policy object');
    assert.ok(source.includes('const previewZoomActions = Object.freeze'), 'visual preview zoom toolbar actions are owned by one action table');
    assert.ok(/function previewZoomButton[\s\S]*button\.dataset\.previewZoomAction = action\.id/.test(source), 'visual preview zoom buttons come from one helper');
    assert.ok(source.includes('function previewZoomOptionsForKind'), 'visual preview fit caps come from one renderer options helper');
    assert.ok(/mermaidFull: Object\.freeze\(\{[\s\S]*wheelZoom: true[\s\S]*panDrag: true/.test(source), 'Mermaid preview zoom owns wheel zoom and drag pan through renderer defaults');
    assert.ok(/function bindPreviewZoomDragPan[\s\S]*pointerdown[\s\S]*pointermove[\s\S]*viewport\.scrollLeft/.test(source), 'Mermaid preview drag pan is routed through the shared zoom surface');
    assert.ok(source.includes('function previewZoomScopedKey'), 'visual preview zoom state is scoped by surface context');
    assert.ok(source.includes('function svgReadableEdgeColor'), 'Mermaid SVG edges are restyled through one readable color helper');
    assert.ok(source.includes('function svgCssStyleValue'), 'Mermaid SVG color repair reads Mermaid class-based style rules');
    assert.ok(source.includes('function svgApplyReadableLabelStyle'), 'Mermaid SVG labels are restyled through one readable label helper');
    assert.ok(/function installPreviewZoomSurface[\s\S]*viewport\.className = 'file-editor-preview-zoom-viewport'/.test(source), 'visual previews share one scroll viewport');
    assert.ok(/function renderFileEditorImagePane[\s\S]*previewZoomOptionsForKind\('imagePane', \{path\}\)[\s\S]*installPreviewZoomSurface\(imagePane, img, zoomOptions\)/.test(source), 'image-file tabs use the shared zoom surface');
    assert.ok(/async function renderMermaidSourceInto[\s\S]*previewZoomOptionsForKind\(fullPreview \? 'mermaidFull' : 'mermaidInline'/.test(source), 'Mermaid previews use shared renderer zoom policy');
    assert.ok(/function disconnectPreviewZoomSurface\(shell, options = \{\}\)[\s\S]*resetPreviewZoomSurfaceClasses/.test(source), 'visual preview zoom cleanup uses one reset helper');
    assert.ok(/function hydratePreviewZoomSurface[\s\S]*data-preview-zoom-action[\s\S]*setPreviewZoomSurfaceState/.test(source), 'visual preview zoom controls can hydrate existing markup');
    assert.ok(source.includes("renderEditorPreviewPane(previewPane, path, state.content, {context: 'split'})"), 'Split Preview uses its own preview zoom context');
    assert.ok(source.includes("hydratePreviewZoomSurfaces(doc.querySelector('[data-preview-root]') || doc)"), 'preview pop-out rehydrates zoom controls after snapshot writes');
    assert.ok(source.includes('.file-preview-popout-window .file-editor-preview-pane-panel.file-editor-preview-zoom-shell'), 'preview pop-out preserves zoom-shell layout');
    assert.equal(source.includes('fileEditorImageModeForPath'), false, 'visual previews do not keep the obsolete imageMode state path');
    assert.ok(/function setFileState[\s\S]*previewZoom[\s\S]*previous\.previewZoom/.test(source), 'visual preview zoom state survives file-state replacement');
    assert.ok(/\.file-editor-preview-zoom-viewport\s*\{[\s\S]*overflow:\s*auto/.test(css), 'visual preview zoom surface owns a scrollable viewport');
    assert.ok(source.includes('capturePaneViewState(item, panel)'), 'file-editor renders use the shared pane viewport capture before pane/tab renders');
    assert.ok(source.includes('restoreFileEditorPanelViewState(item, panel)'), 'CodeMirror editor viewport is restored after pane/tab renders');
    assert.ok(/function renderFileEditorPanelShouldCaptureViewState\(options = \{\}\)[\s\S]*return options\.captureViewState !== false/.test(source), 'file editor render has one shared view-state capture gate');
    assert.ok(/function renderFileEditorPanel\(panel, item, options = \{\}\)[\s\S]*if \(renderFileEditorPanelShouldCaptureViewState\(options\)\) capturePaneViewState\(item, panel\)/.test(source), 'file editor render capture is routed through the shared pane capture gate');
    assert.ok(source.includes('refreshOpenEditorThemePanels'), 'global/editor theme changes update already-open CodeMirror panels');
    assert.ok(source.includes('function reconfigureCodeMirrorPanelTheme'), 'open CodeMirror panels reconfigure their theme compartment');
    assert.ok(source.includes('&& api?.Compartment'), 'CodeMirror API validation requires Compartment for in-place theme updates');
    assert.equal(source.includes("panel?._cmPlainFallback ? [codeMirrorThemeExtension(api)]"), false, 'plain CodeMirror fallback theme toggles keep Markdown fallback syntax decorations');
    assert.equal(source.includes('scheme: activeEditorScheme().id'), false, 'CodeMirror config signatures do not force panel rebuilds on theme-only changes');
    assert.ok(source.includes('function yoagentChatNetworkError'), 'YO!agent chat distinguishes network failures from normal HTTP errors');
    assert.ok(source.includes('let yoagentFocusSerial = 0'), 'YO!agent chat tracks whether focus was abandoned while a request is pending');
    assert.ok(/focusInput:\s*shouldRestoreFocus && focusSerial === yoagentFocusSerial && yoagentDocumentHasFocus\(\)/.test(source), 'YO!agent responses only refocus when the user did not move away');
    assert.ok(/if \(options\.summaryOnly && refreshYoagentSummaryRegions\(node\)\) \{[\s\S]*?restoreYoagentChatInputFocus/.test(source), 'YO!agent summary refresh restores an already-focused composer input');
    assert.ok(source.includes('function refreshYoagentSummaryRegions'), 'YO!agent metadata refresh can update summaries without rebuilding the chat input');
    assert.equal(source.includes('function yoagentAutoRefreshStatusHtml'), false, 'YO!agent no longer renders a continuous background-summary status notice');
    assert.ok(source.includes('summaryOnly: true'), 'YO!agent metadata refresh requests summary-only panel updates');
    assert.ok(source.includes("params.set('locale', i18nActiveLocaleId())"), 'YO!agent activity-summary requests carry the active UI locale');
    assert.ok(source.includes("params.set('scope', 'all')"), 'YO!agent activity-summary requests all visible tmux sessions');
    assert.ok(source.includes("scope: 'all'"), 'server watch-state keeps pushed activity-summary refreshes on all visible tmux sessions');
    assert.ok(source.includes('function yoagentSessionFromHref'), 'YO!agent Markdown session links parse the target session from the query string');
    assert.ok(/function handleYoagentSessionLinkClick[\s\S]*?selectSession\(session, \{userInitiated: true\}\)/.test(source), 'YO!agent Markdown session links select the matching tab');
    assert.ok(source.includes('function installYoagentSessionLinks'), 'YO!agent Markdown session links install a scoped click handler');
    assert.ok(/function linkYoagentSessionCodeReferences[\s\S]*?sessions\.includes\(session\)[\s\S]*?\(tmux\\s\+\)\?session\\s\*\$[\s\S]*?link\.href = `\?yoagent-session=\$\{encodeURIComponent\(session\)\}`/.test(source), 'YO!agent inline `tmux session `code`` references are converted to clickable session links');
    assert.ok(/function renderYoagentMessageMarkdown[\s\S]*?renderMarkdownPreviewInto\(body, yoagentTightMarkdown[\s\S]*?installYoagentSessionLinks\(body\)/.test(source), 'YO!agent Markdown rendering makes session links clickable after sanitization');
    assert.ok(/function rerenderForLocale\(options = \{\}\)[\s\S]*?allowBusyRebuild: options\.localeChange === true[\s\S]*?refreshActivitySummary\(\{force: true, silent: true, localeChange: true\}\)/.test(source), 'language switches force the busy YO!agent UI and activity summary through the new locale');
    // #45: assistant replies are structured Markdown — flag the body and render it through marked.js.
    assert.ok(source.includes('function renderYoagentMessageMarkdown'), '#45: YO!agent assistant replies render their multi-section Markdown body');
    assert.ok(source.includes('data-yoagent-global-markdown'), 'YO!agent global summary lines are flagged for markdown rendering');
    assert.ok(/\.yoagent-global \[data-yoagent-global-markdown\][\s\S]*?renderMarkdownPreviewInto\(body, yoagentTightMarkdown/.test(source), 'YO!agent global summary markdown is rendered through the sanitizer');
    assert.ok(/\.yoagent-message\.assistant \[data-yoagent-markdown\]/.test(source), '#45: the markdown render pass targets flagged assistant message nodes, including split agent-result blocks');
    assert.ok(/renderMarkdownPreviewInto\(body, yoagentTightMarkdown\(body\.textContent/.test(source), '#45/#129: assistant message Markdown is rendered (tightened) from the escaped-text fallback');
    assert.ok(source.includes("roleClass === 'assistant' ? 'yoagent-message-body markdown-body'"), '#45: assistant message bodies get the markdown-body class for formatting');
    // #42: editor controls (# / wrap / find / FROM-TO / diff / theme / save) move OFF the tab strip
    // onto a dedicated toolbar line below the tabs; the tab strip keeps only tabs + frame controls.
    const editorToolbarIdx = source.indexOf('function fileEditorToolbarHtml(');
    const editorToolbarEnd = source.indexOf('function createFileEditorPanel(', editorToolbarIdx);
    assert.ok(editorToolbarIdx > -1 && editorToolbarEnd > editorToolbarIdx, 'editor toolbar HTML is built by the shared toolbar helper caller');
    const editorToolbarTemplate = source.slice(editorToolbarIdx, editorToolbarEnd);
    const editorLeftZoneIdx = editorToolbarTemplate.indexOf('file-editor-toolbar-left');
    const editorCenterZoneIdx = editorToolbarTemplate.indexOf('file-editor-toolbar-center');
    const editorRightZoneIdx = editorToolbarTemplate.indexOf('file-editor-toolbar-right');
    const editorFrameActionsIdx = source.indexOf('file-editor-frame-actions');
    const editorTabsIdx = source.indexOf('<div class="pane-tabs"', editorFrameActionsIdx);
    const editorGutterIdx = editorToolbarTemplate.indexOf("className: 'file-editor-gutter-panel'");
    assert.ok(editorToolbarIdx > -1, '#42: editor controls render on a dedicated .file-editor-toolbar line');
    assert.ok(editorGutterIdx > -1, '#42: the # / line-numbers control lives in the toolbar row, not the tab strip');
    assert.ok(editorLeftZoneIdx > -1 && editorLeftZoneIdx < editorCenterZoneIdx && editorCenterZoneIdx < editorRightZoneIdx, 'editor toolbar renders shared left/center/right parent zones');
    assert.ok(source.includes('${fileEditorToolbarHtml(item)}'), 'createFileEditorPanel mounts the helper-built toolbar before the panel body');
    assert.ok(source.includes('function createToolbarButton(') && source.includes('function createSegmentedControl(') && source.includes('function createActionRow('), 'R9: panel buttons, segmented controls, and rows share DOM builders');
    assert.ok(source.includes('function bindActionDispatcher(') && source.includes("bindActionDispatcher(panel, {"), 'R9: toolbar clicks use delegated data-action dispatch');
    const editorLeftTemplate = editorToolbarTemplate.slice(editorLeftZoneIdx, editorCenterZoneIdx);
    const editorRightTemplate = editorToolbarTemplate.slice(editorRightZoneIdx);
    assert.ok(
      editorLeftTemplate.indexOf("className: 'file-editor-gutter-panel'") < editorLeftTemplate.indexOf("className: 'file-editor-wrap-panel'")
        && editorLeftTemplate.indexOf("className: 'file-editor-wrap-panel'") < editorLeftTemplate.indexOf("className: 'file-editor-diff-panel'"),
      'editor toolbar left/front controls render as #, wrap icon, Differ'
    );
    assert.equal(editorRightTemplate.includes('file-editor-wrap-panel'), false, 'editor toolbar no longer renders the redundant right-side wrap icon');
    assert.ok(
      editorToolbarTemplate.indexOf("className: 'file-editor-theme-panel'") < editorToolbarTemplate.indexOf("dataset: {editorMode: 'edit'}"),
      'editor toolbar renders the Bright/Dark/Vanilla selector immediately before Edit'
    );
    assert.ok(
      editorToolbarTemplate.indexOf("className: 'file-editor-reload-panel'") > editorToolbarTemplate.indexOf("dataset: {editorMode: 'edit'}"),
      'editor toolbar keeps Reload with the trailing command buttons'
    );
    assert.equal(source.includes("cycleEditorThemeMode({includeVanilla: mode === 'preview' || mode === 'split'})"), false, 'editor theme button never falls back to two-state dark/light based on view mode');
    assert.ok(/'editor-theme': \(\) => cycleEditorThemeMode\(\{includeVanilla: true\}\)/.test(source), 'editor theme button always cycles Bright/Dark/Vanilla');
    assert.ok(/updateEditorThemeButton\(themeButton, \{includeVanilla: true\}\)/.test(source), 'editor theme button always renders the visible three-state label');
    assert.ok(!/file-editor-gutter-panel|file-editor-find-panel|file-editor-diff-ref-panel|file-editor-wrap-panel/.test(source.slice(editorFrameActionsIdx, editorTabsIdx)), '#42: the editor tab strip is uncluttered — only tabs + frame controls remain');
    assert.ok(/\.panel\.file-editor-panel\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(css), '#42: the editor panel grid reserves a row for the toolbar between tabs and body');
    assert.ok(/\.file-editor-toolbar\[hidden\]\s*\{\s*display:\s*none/.test(css), '#42: the editor toolbar row collapses when no controls are visible');
    // Editor toolbar alignment: left/center/right are owned by parent groups, not per-button spacer hacks.
    assert.ok(/\.file-editor-toolbar-zone\s*\{[^}]*display:\s*inline-flex[\s\S]*align-items:\s*center/.test(css), 'editor toolbar children inherit shared zone behavior');
    assert.ok(/\.file-editor-toolbar-left\s*\{[^}]*flex:\s*0 1 auto/.test(css), 'editor toolbar left zone stays pinned left');
    assert.ok(/\.file-editor-toolbar-center\s*\{[^}]*position:\s*absolute[\s\S]*left:\s*50%[\s\S]*transform:\s*translate\(-50%, -50%\)/.test(css), 'editor toolbar center zone stays centered');
    assert.ok(/\.file-editor-toolbar-right\s*\{[^}]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(css), 'editor toolbar right zone is the only spacer-backed zone');
    assert.ok(/\.file-editor-diff-panel\s*\{[^}]*min-width:\s*44px/.test(css), 'editor toolbar gives Differ text-button width');
    assert.ok(/\.file-editor-gutter-panel,\s*\n\.file-editor-wrap-panel,\s*\n\.file-editor-find-panel,\s*\n\.file-editor-diff-expand-panel/.test(css), 'editor toolbar gives Wrap around the same compact icon-button sizing as # and Search');
    assert.ok(/\.file-editor-toolbar\s*\{[^}]*justify-content:\s*flex-start/.test(css), 'editor toolbar left-aligns # and Differ by default, including after browser refresh');
    const toolbarCssStart = css.indexOf('.file-editor-toolbar {');
    const toolbarCssEnd = css.indexOf('.file-editor-preview-font-panel button', toolbarCssStart);
    assert.equal(css.slice(toolbarCssStart, toolbarCssEnd).includes('margin-inline-end: auto'), false, 'editor toolbar has no per-button end-spacer rules that can move Differ');
    assert.ok(/\.file-editor-toolbar\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(css), '#3: editor toolbar background matches the pane chrome bar (--pane-bar-bg: bright focused / gray unfocused)');
    assert.ok(/\.file-editor-diff-panel\.active,[\s\S]*?\.file-editor-diff-panel\[aria-pressed="true"\]\s*\{[\s\S]*?background:\s*var\(--pane-ctl-pressed-bg/.test(css), 'Diff active state uses the shared pressed control color');
    const editorPressedStart = css.indexOf('.file-editor-mode-control-panel button.active');
    const editorPressedBlock = css.slice(editorPressedStart, css.indexOf('{', editorPressedStart));
    assert.ok(editorPressedBlock.includes('.file-editor-gutter-panel.active') && editorPressedBlock.includes('.file-editor-find-panel[aria-pressed="true"]') && editorPressedBlock.includes('.file-editor-wrap-panel[aria-pressed="true"]'), '#, Search, and wrap active states share the pressed control treatment');
    assert.ok(/className: 'file-editor-diff-panel'[\s\S]*label: 'Differ'/.test(editorToolbarTemplate), 'editor Diff toolbar button renders as Differ text');
    assert.ok(/className: 'file-editor-wrap-panel'[\s\S]*file-editor-icon-wrap/.test(editorToolbarTemplate), 'editor Wrap toolbar button renders the original icon in the left zone');
    assert.ok(/function updateEditorWrapButton\(button\)[\s\S]*setFileEditorIcon\(button, 'file-editor-icon-wrap'\)/.test(source), 'wrap button renderer preserves the original icon');
    assert.ok(source.includes('toggleEditorFind(panel);'), 'Search toolbar button toggles the CodeMirror search panel');
    assert.ok(source.includes('const currentText = String(state.content || \'\');'), 'plain CodeMirror editor mode owns its current text value');
    assert.ok(source.includes('function setLimitedMapEntry'), 'long-lived frontend maps share a bounded LRU setter');
    assert.ok(source.includes('fileExplorerMemoryCacheLimit = 512'), 'file explorer memory caches are capped');
    assert.ok(source.includes('commandPaletteRecentKeyLimit = 100'), 'command palette recent-key cache is capped');
    assert.ok(source.includes('restoreElementScrollPosition(container, scrollTop, scrollLeft);'), 'editor preview renders preserve scroll position');
    assert.equal(source.includes("const signature = codeMirrorConfigSignature(path, {mode: 'diff', layout, original, from: state.diffFromRef, to: state.diffToRef});\n  installCodeMirrorDiffResizeObserver"), false, 'diff resize observer is not installed before the rebuild decision');
    assert.ok(/function openFileQuickOpenPath\(path, options = \{\}\)[\s\S]*const targetSlot = fileQuickOpenTargetSlot\(\);[\s\S]*openedItem = await openFileInEditor\(path, \{name: label\}, targetSlot[\s\S]*\? \{targetSlot, userInitiated: true\}[\s\S]*: \{userInitiated: true\}\)/.test(source), 'quick-open normal file opens pass the active pane target slot');
    assert.ok(/function fileQuickOpenTargetSlot\(\)\s*\{\s*return focusedActivationSlot\(\);/.test(source), 'DOIT.56 N2: quick-open uses the shared focused-pane activation target');
    assert.ok(/function fileEditorActivationSlot\(\)[\s\S]*focusedActivationSlot\(\)[\s\S]*lastActiveNonFileExplorerPaneItem[\s\S]*slotForSession\(lastActiveNonFileExplorerPaneItem\)/.test(source), 'DOIT.56 N2: file opens remember the last active non-Finder pane when Differ itself has focus');
    assert.ok(/function slotForTabActivation\(item\)\s*\{[\s\S]*return fileEditorActivationSlot\(\) \|\| largestNonFileExplorerPaneSlot\(\)/.test(source), 'DOIT.56 N2: new virtual/file tabs prefer the focused non-Finder pane before largest-pane fallback');
    assert.ok(/async function openFileEditorPane\(path, options = \{\}\)[\s\S]*const activationSlot = slotForTabActivation\(item\);[\s\S]*await moveSessionToSlot\(item, activationSlot/.test(source), 'DOIT.56 N2: generic file opens share the same focused-pane activation target');
    assert.ok(/if \(options\.split === true\)[\s\S]*targetZone: targetSlot \? 'middle' : 'right'/.test(source), 'quick-open split-open keeps its explicit split behavior');
    assert.ok(source.includes('focusQuickOpenedFile(openedItem);'), 'quick-open focuses the opened file after the async open resolves');
    assert.ok(source.includes('await Promise.resolve(action?.());'), 'command palette selection awaits async run handlers before focus settles');
    assert.ok(source.includes('function focusCommandPaletteTarget'), 'command palette has one shared post-run focus helper');
    assert.ok(source.includes('focusCommandPaletteTarget(item);'), 'command palette applies deterministic focus after async tab/session actions');
    assert.ok(source.includes('targetItem: item,'), 'command palette tab entries carry their layout focus target');
    assert.ok(source.includes("const defaultLightEditorScheme = 'yolomux-light';"), 'light editor defaults to the brand YOLOmux Light scheme');
    assert.ok(source.includes('function defaultFileEditorViewModeForPath(path, kind)')
      && source.includes("return previewRendererForPath(path)?.defaultMode || 'edit';")
      && source.includes('else setFileEditorViewMode(fullPath, defaultFileEditorViewModeForPath(fullPath, kind), item);'), 'plain file opens reset stale diff mode back to edit while media and Mermaid source open in Preview');
    assert.ok(source.includes('applyMarkdownSourceLines(container, text);'), 'Markdown preview source anchors are attached after parsing');
    assert.ok(source.includes('function codeMirrorMarkdownFallbackSyntaxExtension'), 'Markdown edit mode has a parser-independent CodeMirror coloring fallback');
    assert.ok(/function codeMirrorThemeExtensions[\s\S]*codeMirrorMarkdownFallbackSyntaxExtension\(api, path\)/.test(source), 'Markdown fallback coloring is wired into live CodeMirror edit views');
    assert.ok(css.includes('.cm-content .md-heading'), 'Markdown fallback color classes apply inside CodeMirror edit content');
    assert.ok(/gutterButton\.hidden = state\.kind !== 'text' \|\| mode === 'preview'/.test(source), 'preview mode hides the line-number button because no CodeMirror gutter is shown');
    assert.ok(/wrapButton\.hidden = state\.kind !== 'text' \|\| mode === 'preview'/.test(source), 'preview mode hides the wrap button because no CodeMirror editor is shown');
    assert.ok(/findButton && mode === 'preview'/.test(source), 'preview mode hides Search because the CodeMirror search panel is not available there');
    assert.equal(source.includes('file-editor-pure-preview'), false, 'old side-preview-only editor mode class is removed');
    assert.equal(source.includes('isFilePreviewItem'), false, 'old file-preview tab type is removed from runtime');
    assert.ok(/function updatePanelSlot[\s\S]*panel\.dataset\.layoutItem = session[\s\S]*isFileEditorItem\(session\)[\s\S]*renderFileEditorPanel\(panel, session, \{updateActiveFile: !dockviewLayoutActive\(\), captureViewState: false\}\)/.test(source), 'switching a pane to a file editor tab re-renders editor chrome without making Dockview background renders active or overwriting saved scroll');
  });

  test('file editor pane reattach does not overwrite saved CodeMirror scroll state', () => {
    const api = loadYolomux('', ['1']);
    const path = '/home/test/notes.md';
    const item = api.registerFileEditorLayoutItemForTest(path);
    const contentText = Array.from({length: 120}, (_, index) => `line ${index + 1}`).join('\n');
    api.setOpenFileStateForTest(path, {mtime: 1, size: contentText.length, kind: 'text', original: contentText, content: contentText, dirty: false});
    const panel = new TestElement('editor-panel');
    panel.dataset.layoutItem = item;
    panel.dataset.filePath = path;
    panel.classList.add('panel', 'file-editor-panel');
    const rawPane = new TestElement('', 'pre');
    rawPane.classList.add('file-editor-raw-panel');
    rawPane.appendChild(new TestElement('', 'code'));
    const previewPane = new TestElement('', 'div');
    previewPane.classList.add('file-editor-preview-pane-panel');
    const imagePane = new TestElement('', 'div');
    imagePane.classList.add('file-editor-image-panel');
    const content = new TestElement('', 'div');
    content.classList.add('file-editor-content');
    content.append(rawPane, previewPane, imagePane);
    const status = new TestElement('', 'div');
    status.classList.add('file-editor-status-panel');
    const message = new TestElement('', 'span');
    message.classList.add('file-editor-status-message');
    const cursor = new TestElement('', 'span');
    cursor.classList.add('file-editor-cursor-status');
    status.append(message, cursor);
    panel.append(content, status);
    const scrollDOM = {scrollTop: 420, scrollLeft: 7};
    const doc = {
      length: contentText.length,
      toString() { return contentText; },
      lineAt(pos) { return {number: 1, from: Math.max(0, Math.min(Number(pos) || 0, contentText.length))}; },
    };
    panel._cmView = {
      scrollDOM,
      state: {doc, selection: {main: {anchor: 11, head: 13}, ranges: [{from: 11, to: 13, anchor: 11, head: 13}]}},
      dispatch(update) { this.lastDispatch = update; },
    };
    api.captureFileEditorPanelViewStateForTest(item, panel);
    assert.equal(api.fileEditorViewStateForTest(item).scrollTop, 420, 'the focused editor saves its real scroll before tab switch');
    assert.equal(api.fileEditorViewStateForTest(item).scrollLeft, 7);
    panel.isConnected = false;
    scrollDOM.clientHeight = 0;
    scrollDOM.scrollTop = 0;
    scrollDOM.scrollLeft = 0;
    api.captureFileEditorPanelViewStateForTest(item, panel);
    assert.equal(api.fileEditorViewStateForTest(item).scrollTop, 420, 'detached zero-height editor captures must not clobber a saved scroll position');
    assert.equal(api.fileEditorViewStateForTest(item).scrollLeft, 7);
    panel.isConnected = true;
    scrollDOM.clientHeight = 200;
    scrollDOM.scrollTop = 0;
    scrollDOM.scrollLeft = 0;
    panel._cmView.state.selection = {main: {anchor: 0, head: 0}, ranges: [{from: 0, to: 0, anchor: 0, head: 0}]};
    assert.equal(api.renderFileEditorPanelShouldCaptureViewStateForTest({captureViewState: false}), false);
    api.renderFileEditorPanel(panel, item, {updateActiveFile: false, captureViewState: false});
    assert.equal(api.fileEditorViewStateForTest(item).scrollTop, 420, 'pane reattach render must not replace saved scroll with detached zero scroll');
    assert.equal(api.fileEditorViewStateForTest(item).scrollLeft, 7);
  });

  test('file editor save hygiene helper respects the two opt-in settings', () => {
    const api = loadYolomux('', ['1']);
    const source = 'alpha  \n beta\t\nlast';
    assert.equal(
      api.normalizeFileEditorSaveContentForTest(source, {trimTrailingWhitespace: false, ensureFinalNewline: false}),
      source,
      'save hygiene leaves content unchanged when both settings are off',
    );
    assert.equal(
      api.normalizeFileEditorSaveContentForTest(source, {trimTrailingWhitespace: true, ensureFinalNewline: false}),
      'alpha\n beta\nlast',
      'trim-on-save removes only spaces and tabs at line ends',
    );
    assert.equal(
      api.normalizeFileEditorSaveContentForTest(source, {trimTrailingWhitespace: true, ensureFinalNewline: true}),
      'alpha\n beta\nlast\n',
      'final-newline-on-save adds one newline after trimming',
    );
    assert.equal(
      api.normalizeFileEditorSaveContentForTest('', {trimTrailingWhitespace: true, ensureFinalNewline: true}),
      '',
      'empty files stay empty',
    );
  });

  test('file editor status counts update from the live editor document', () => {
    const api = loadYolomux('', ['1']);
    const path = '/home/test/counts.txt';
    const panel = new TestElement('editor-counts-panel');
    panel.dataset.filePath = path;
    const status = new TestElement('', 'div');
    status.classList.add('file-editor-status-panel');
    const message = new TestElement('', 'span');
    message.classList.add('file-editor-status-message');
    const cursor = new TestElement('', 'span');
    cursor.classList.add('file-editor-cursor-status');
    status.append(message, cursor);
    panel.appendChild(status);
    api.setOpenFileStateForTest(path, {mtime: 1, size: 13, kind: 'text', original: 'one two\nthree', content: 'one two\nthree', dirty: false});
    const doc = {
      length: 13,
      toString() { return 'one two\nthree'; },
      lineAt(pos) { return Number(pos) >= 8 ? {number: 2, from: 8} : {number: 1, from: 0}; },
    };
    panel._cmView = {
      state: {doc, selection: {main: {head: 10}, ranges: [{from: 10, to: 10, anchor: 10, head: 10}]}},
    };

    api.setFileEditorPanelStatusForTest(panel, 'loaded', '');
    assert.equal(status.querySelector('.file-editor-count-status').textContent, '2 lines · 3 words · 13 chars');
    assert.equal(status.querySelector('.file-editor-cursor-status').textContent, '2:3');

    const updatedDoc = {
      length: 19,
      toString() { return 'one two\nthree four\n'; },
      lineAt(pos) {
        if (Number(pos) >= 19) return {number: 3, from: 19};
        return Number(pos) >= 8 ? {number: 2, from: 8} : {number: 1, from: 0};
      },
    };
    panel._cmView.state = {
      doc: updatedDoc,
      selection: {main: {head: 19}, ranges: [{from: 0, to: 3, anchor: 0, head: 3}]},
    };
    api.updateCodeMirrorCursorStatusForTest(panel);
    assert.equal(status.querySelector('.file-editor-count-status').textContent, '3 lines · 4 words · 19 chars');
    assert.equal(status.querySelector('.file-editor-cursor-status').textContent, '3:1 · 1 sel · 3 chars');
  });

  test('file editor restore dispatches CodeMirror scroll snapshot for long documents', () => {
    const api = loadYolomux('', ['1']);
    const path = '/home/test/2026.md';
    const item = api.registerFileEditorLayoutItemForTest(path);
    const panel = new TestElement('editor-panel');
    panel.dataset.layoutItem = item;
    const scrollDOM = {scrollTop: 960, scrollLeft: 12};
    const scrollSnapshot = {kind: 'cm-scroll-snapshot'};
    const dispatches = [];
    panel._cmView = {
      scrollDOM,
      scrollSnapshot() { return scrollSnapshot; },
      state: {
        doc: {length: 120000},
        selection: {main: {anchor: 80000, head: 80000}, ranges: [{from: 80000, to: 80000, anchor: 80000, head: 80000}]},
      },
      dispatch(update) { dispatches.push(update); },
      requestMeasure(request) { request.write(null, this); },
    };
    api.captureFileEditorPanelViewStateForTest(item, panel);
    const saved = api.fileEditorViewStateForTest(item);
    assert.equal(saved.scrollTop, 960);
    assert.equal(saved.scrollLeft, 12);
    assert.strictEqual(saved.scrollSnapshot, scrollSnapshot, 'capture stores CodeMirror scrollSnapshot so long-file restore has a line anchor');
    scrollDOM.scrollTop = 0;
    scrollDOM.scrollLeft = 0;
    api.restoreFileEditorPanelViewStateForTest(item, panel);
    assert.equal(dispatches[0].selection.anchor, 80000);
    assert.strictEqual(dispatches[0].effects, scrollSnapshot, 'restore dispatches CodeMirror scrollSnapshot instead of relying only on raw scrollTop');
    assert.equal(scrollDOM.scrollTop, 960);
    assert.equal(scrollDOM.scrollLeft, 12);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function restoreFileEditorPanelViewState[\s\S]*scrollStillAtTarget[\s\S]*requestAnimationFrame\(\(\) => measuredRestore\(true\)\)[\s\S]*requestAnimationFrame\(\(\) => requestAnimationFrame\(\(\) => measuredRestore\(true\)\)\)/.test(source), 'delayed CodeMirror scroll restores are guarded so user scrolling during diff load is not snapped back');
  });

  test('t@2463', () => {
    const api = loadYolomux('', ['1']);
    const section = new TestElement('section');
    section.rect = {left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500};
    const row = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    assert.equal(api.splitPercentForPointer(section, row, {clientX: 10, clientY: 0}), 32);
    assert.equal(api.splitPercentForPointer(section, row, {clientX: 990, clientY: 0}), 68);
    const nested = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 50);
    assert.equal(api.layoutNodeMinWidth(nested), 960);
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(css.includes('--min-split-pane-width: 320px'));
    assert.ok(css.includes('--min-split-pane-height: 220px'));
    assert.equal(api.minSplitPaneWidthPx(), 320, 'minSplitPaneWidthPx follows the CSS token/fallback value');
    assert.equal(api.minSplitPaneHeightPx(), 220, 'minSplitPaneHeightPx follows the CSS token/fallback value');
    assert.equal(/rootCssLengthPx\('--min-split-pane-(width|height)'\)\s*\|\|\s*(320|220)/.test(source), false, 'min split-pane fallback literals stay behind shared helpers');
  });

  test('t@2474', () => {
    const api = loadYolomux('', ['1']);
    const item = api.registerFileEditorLayoutItem('/home/test/AGENTS.md');
    let focusCount = 0;
    const panel = {
      _cmView: {
        focus() {
          focusCount += 1;
        },
      },
    };
    const emptyPanel = {
      _cmView: null,
    };
    api.setAutoFocusEnabledForTest(true);
    api.setFocusedPanelItem('1');
    api.requestFileEditorPanelFocus(item);
    assert.equal(api.focusFileEditorPanelIfReady(emptyPanel, item), false);
    assert.equal(focusCount, 0);

    api.setFocusedPanelItem(item);
    assert.equal(api.focusFileEditorPanelIfReady(panel, item), true);
    assert.equal(focusCount, 1);
    assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);

    api.requestFileEditorPanelFocus(item);
    assert.equal(api.focusFileEditorPanelIfReady(emptyPanel, item), false);
    assert.equal(focusCount, 1);

    api.setAutoFocusEnabledForTest(false);
    api.setFocusedPanelItem(item);
    api.requestFileEditorPanelFocus(item);
    assert.equal(api.focusFileEditorPanelIfReady(panel, item), false);
    assert.equal(focusCount, 1);

    api.setAutoFocusEnabledForTest(true);
    api.requestFileEditorPanelFocus(item);
    assert.equal(api.focusFileEditorPanelIfReady(panel, item), true);
    assert.equal(focusCount, 2);
  });

  test('t@2515', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.autoFocusEnabledForTest(), false, 'auto-focus is off by default');
    api.testElementForId('terminal-pane-1').classList.add('active');
    api.setAutoFocusEnabledForTest(false);
    api.selectPanelOnHover('1');
    assert.equal(api.focusedPanelItemForTest(), null);
    api.selectPanelOnHover('__files__');
    assert.equal(api.focusedPanelItemForTest(), null);

    const enabledApi = loadYolomux('', ['1']);
    enabledApi.setAutoFocusEnabledForTest(true);
    enabledApi.testElementForId('terminal-pane-1').classList.add('active');
    enabledApi.selectPanelOnHover('1');
    assert.equal(enabledApi.focusedPanelItemForTest(), '1');
  });

  test('t@2532', () => {
    const api = loadYolomux('', ['1']);
    let focusCount = 0;
    api.registerTerminalForTest('1', {focus() { focusCount += 1; }});
    api.setAutoFocusEnabledForTest(false);
    api.focusPanel('1');
    assert.equal(api.focusedPanelItemForTest(), '1');
    assert.equal(focusCount, 0);
    api.focusPanel('1', {userInitiated: true});
    assert.equal(api.focusedPanelItemForTest(), '1');
    assert.equal(focusCount, 1);
    api.selectSession('1', {userInitiated: true});
    assert.equal(api.focusedPanelItemForTest(), '1');
    assert.equal(focusCount, 2, 'selecting an already visible tmux tab is an explicit user focus action');
  });

  test('t@2548', () => {
    const api = loadYolomux('', ['1']);
    api.setFocusedTerminal('1');
    assert.equal(api.focusedPanelItemForTest(), '1');
    assert.equal(api.lastActivePaneItemForTest(), '1');
    assert.equal(api.visualActivePaneItemForTest(), '1');
    api.clearFocusedTerminal('1');
    assert.equal(api.focusedPanelItemForTest(), null, 'terminal blur clears keyboard pane focus');
    assert.equal(api.lastActivePaneItemForTest(), '1', 'terminal blur keeps the visual active pane');
    assert.equal(api.visualActivePaneItemForTest(), '1', 'visual active pane survives terminal blur');
  });
}

module.exports = {runLayoutRestoreSuite};

if (require.main === module) {
  runSuites([runLayoutRestoreSuite]);
}

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

function tmuxWindowButtonElement(session, index, active = false) {
  const button = new TestElement(`tmux-window-${session}-${index}`, 'button');
  button.className = `tab tmux-window-button${active ? ' active' : ''}`;
  button.dataset.windowIndex = String(index);
  button.dataset.windowSession = session;
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  return button;
}

function tmuxWindowBarElement(session, buttons) {
  const bar = new TestElement(`tmux-window-bar-${session}`);
  bar.dataset.tmuxWindowBar = session;
  buttons.forEach(button => bar.appendChild(button));
  return bar;
}

function activeTmuxWindowIndexesFromHtml(html) {
  return [...String(html || '').matchAll(/<button\b([^>]*)>/g)]
    .filter(([, attrs]) => /\btmux-window-button\b/.test(attrs) && /\bactive\b/.test(attrs))
    .map(([, attrs]) => attrs.match(/\bdata-window-index="([^"]+)"/)?.[1] || '');
}

function activeTmuxWindowIndexesFromElement(root) {
  return Array.from(root.querySelectorAll('.tmux-window-button[data-window-index]'))
    .filter(button => button.classList.contains('active'))
    .map(button => button.dataset.windowIndex || '');
}

function tmuxWindowButtonFromElement(root, index) {
  return root.querySelector(`.tmux-window-button[data-window-index="${String(index)}"]`);
}

async function runEditorPreviewSuite() {
  test('search history pane renders search results and compact runs', () => {
    const api = loadYolomux('', ['1']);
    api.setSearchHistoryStateForTest(
      'beta',
      {
        query: 'beta',
        results: [{
          session: '1',
          timestamp: '2026-01-01T00:00:00Z',
          kind: 'state_changed',
          source: 'event',
          title: 'beta event',
          snippet: 'beta event detail',
          target: {type: 'events', session: '1', tab: 'events'},
        }],
      },
      {
        runs: [{
          session: '1',
          prompt: 'please ship beta rollout',
          cwd: '/home/test/project',
          final_state: 'done',
          latest_summary: 'beta rollout finished',
          agent: {kind: 'codex', model: 'gpt-test'},
          pr: {number: 42, state: 'open'},
        }],
      }
    );

    const html = api.searchHistoryPanelHtmlForTest();

    assert.ok(html.includes('data-search-history-form'), 'Search & Runs pane includes a search form');
    assert.ok(html.includes('data-search-result-index="0"'), 'search result rows are actionable');
    assert.ok(html.includes('beta event detail'), 'search result snippets render');
    assert.ok(html.includes('data-run-history-session="1"'), 'run history rows are actionable');
    assert.ok(html.includes('please ship beta rollout'), 'run history prompts render');
    assert.ok(html.includes('beta rollout finished'), 'run history summaries render');
  });

  test('Finder/Differ/Tabber recency brightness and pulse apply in Ago and Date modes', () => {
    const api = loadYolomux('', ['1']);
    const nowMs = 2_000_000;
    const nowSeconds = nowMs / 1000;
    const entries = [
      {name: 'just.md', kind: 'file', mtime: nowSeconds - 5},
      {name: 'hot.md', kind: 'file', mtime: nowSeconds - 30},
      {name: 'fresh.md', kind: 'file', mtime: nowSeconds - 4 * 60},
      {name: 'ten.md', kind: 'file', mtime: nowSeconds - 9 * 60},
      {name: 'hour.md', kind: 'file', mtime: nowSeconds - 50 * 60},
      {name: 'old.md', kind: 'file', mtime: nowSeconds - 3 * 24 * 60 * 60},
    ];
    const rowMap = tree => Object.fromEntries(tree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
    const dateCell = row => row.querySelector(':scope > .file-tree-date');
    const tokensCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
    const sessionsCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
    const treeCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    const layoutSource = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
    const fileTreeSource = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
    const popoverSource = fs.readFileSync('static_src/js/yolomux/60_popovers_tabs.js', 'utf8');
    assert.ok(/function statusIndicatorToneClasses\(tone\)[\s\S]*tone === 'working'[\s\S]*status-indicator--working', 'heartbeat-pulse'[\s\S]*tone === 'cooldown'[\s\S]*status-indicator--cooldown'[\s\S]*tone === 'attention'[\s\S]*status-indicator--attention', 'heartbeat-pulse', 'attention-pulse'[\s\S]*tone === 'active'[\s\S]*status-indicator--active'[\s\S]*tone === 'settled'[\s\S]*status-indicator--settled[\s\S]*tone === 'idle'[\s\S]*status-indicator--idle/.test(layoutSource), 'ASK?/QUES?/activity-dot status tones are centralized in one shared parent helper');
    assert.ok(/statusIndicatorInlineClasses\(questionTone,\s*'topbar-activity-ques'/.test(layoutSource), 'topbar ASK? badges inherit shared inline status behavior');
    assert.ok(/statusIndicatorTextClasses\(tone,\s*classes\)/.test(layoutSource), 'tab ASK? badges inherit shared text status behavior');
    assert.ok(/function statusIndicatorLabelClasses\(tone,\s*\.\.\.classes\)[\s\S]*statusIndicatorModifiedClasses\('status-indicator--label'/.test(layoutSource), 'ASK? status labels inherit shared status-indicator tone behavior without badge text-transform');
    assert.ok(/const tone = item\.state === 'working'[\s\S]*statusIndicatorDotClasses\(\s*tone,\s*'agent-window-activity-icon'/.test(fileTreeSource), 'tmux window activity circles inherit shared dot status behavior');
    assert.ok(/statusIndicatorDotClasses\(\s*dotTone,\s*'session-agent-dot'/.test(popoverSource), 'session popover activity circles inherit shared dot status behavior');
    assert.ok(/\.status-indicator\s*\{[^}]*display:\s*inline-flex/.test(sessionsCss), 'ASK?/QUES?/activity-dot markers share the status-indicator parent');
    assert.ok(/\.status-indicator--text\s*\{[^}]*border:\s*1px solid var\(--divider\)/.test(sessionsCss), 'text status badges inherit pill framing from the shared parent modifier');
    assert.ok(/\.status-indicator--dot\s*\{[^}]*color:\s*var\(--muted\)/.test(sessionsCss), 'circle status markers inherit dot color/shape from the shared parent modifier');
    assert.ok(/\.heartbeat-pulse\s*\{[^}]*animation-duration:\s*var\(--pulse-duration\)[^}]*animation-timing-function:\s*var\(--pulse-easing\)/.test(sessionsCss), 'heartbeat indicators share one pulse timing parent');
    assert.ok(/\.status-indicator--dot\s*\{[^}]*border-radius:\s*999px[\s\S]*opacity:\s*1/.test(sessionsCss), 'circle status markers stay fully opaque while their glow pulses');
    assert.ok(/\.status-indicator--dot\.heartbeat-pulse\s*\{[\s\S]*--attention-pulse-brightness-rest:\s*0\.82[\s\S]*--attention-pulse-brightness-peak:\s*1\.34/.test(sessionsCss), 'pulsing dots opt into a brightness channel so the pulse remains visible on active accent backgrounds');
    assert.ok(/\.status-indicator--dot\.status-indicator--working\.heartbeat-pulse\s*\{[^}]*animation-name:\s*attention-ring-fade[\s\S]*animation-duration:\s*var\(--pulse-duration\)[\s\S]*animation-delay:\s*var\(--attention-animation-delay/.test(sessionsCss), 'working circle markers inherit the shared phased glow pulse');
    assert.equal(layoutSource.includes("status-indicator--cooldown', 'heartbeat-pulse"), false, 'cooldown tone does not inherit the heartbeat class');
    assert.equal(/status-indicator--dot\.status-indicator--cooldown\.heartbeat-pulse|\.status-indicator--cooldown\s*\{[^}]*--attention-ring-rgb/.test(sessionsCss), false, 'cooldown circle markers stay static yellow without pulse/glow');
    assert.equal(/\.status-indicator--dot\.status-indicator--working\.heartbeat-pulse,[\s\S]*?animation-name:\s*command-palette-thinking/.test(sessionsCss), false, 'status dots do not use the old command-palette-thinking pulse');
    assert.ok(/\.status-indicator--working\s*\{[^}]*--attention-ring-rgb:\s*82 210 115/.test(sessionsCss), 'working dot glow is green');
    assert.ok(/\.status-indicator--cooldown\s*\{[^}]*color:\s*var\(--accent-gold\)/.test(sessionsCss), 'cooldown dot is static yellow');
    assert.ok(/\.status-indicator--active\s*\{[^}]*color:\s*var\(--file-tree-recency-max-contrast, var\(--text\)\)/.test(sessionsCss), 'active labels use the same max-contrast token as plain hot recency');
    assert.ok(/\.status-indicator--attention\s*\{[^}]*--attention-ring-rgb:\s*255 51 71/.test(sessionsCss), 'ASK?/QUES? dot glow is red');
    assert.ok(/\.status-indicator--dot\.status-indicator--attention\s*\{[^}]*color:\s*var\(--bad\)/.test(sessionsCss), 'ASK?/QUES? dot glyphs use saturated red instead of pale danger text');
    assert.equal(/status-indicator--idle[\s\S]{0,160}animation/.test(sessionsCss), false, 'idle circle markers stay static');
    assert.ok(/@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*\.heartbeat-pulse[\s\S]*animation:\s*none/.test(sessionsCss), 'working/attention pulses are disabled under reduced motion by the shared parent');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-name:\s*attention-ring-fade/.test(sessionsCss), 'recency and attention share the attention-ring-fade animation parent');
    assert.ok(/@keyframes attention-ring-fade\s*\{[\s\S]*0%, 100%\s*\{[\s\S]*box-shadow:\s*0 0 0 0 rgb\(var\(--attention-ring-rgb, 255 51 71\) \/ 0\), 0 0 5px rgb\(var\(--attention-ring-rgb, 255 51 71\) \/ 0\.24\)[\s\S]*45%, 55%\s*\{[\s\S]*box-shadow:\s*0 0 0 2px rgb\(var\(--attention-ring-rgb, 255 51 71\) \/ 0\.72\), 0 0 26px rgb\(var\(--attention-ring-rgb, 255 51 71\) \/ 0\.68\)/.test(sessionsCss), 'attention-ring-fade uses one parameterized weak/strong glow waveform');
    assert.ok(/@keyframes attention-ring-fade\s*\{[\s\S]*filter:\s*saturate\(var\(--attention-pulse-saturate-rest, 1\)\) brightness\(var\(--attention-pulse-brightness-rest, 1\)\)[\s\S]*filter:\s*saturate\(var\(--attention-pulse-saturate-peak, 1\)\) brightness\(var\(--attention-pulse-brightness-peak, 1\)\)/.test(sessionsCss), 'attention-ring-fade also carries the dot brightness pulse with neutral defaults for non-dot users');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-duration:\s*var\(--pulse-duration\)/.test(sessionsCss), 'shared attention pulse uses the shared pulse duration token');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-timing-function:\s*var\(--pulse-easing\)/.test(sessionsCss), 'shared attention pulse uses the shared pulse easing token');
    assert.ok(/\.ci-indicator\.metadata-pulse:not\(\.pr-status-failing\)\s*\{[^}]*animation:\s*metadata-badge-pulse var\(--pulse-duration\) var\(--pulse-easing\) 14/.test(sessionsCss), 'metadata pulse no longer has a hardcoded duration');
    assert.equal(/900ms ease-in-out infinite alternate|metadata-badge-pulse 1\.4s/.test(sessionsCss), false, 'old hardcoded pulse durations are gone from session/popover CSS');
    assert.ok(/\.file-tree-date\s*\{[\s\S]*border:\s*1px solid transparent[\s\S]*border-radius:\s*5px/.test(treeCss), 'recency date cells have a visible border target for the shared attention-ring animation');
    assert.equal(/file-tree-recency-pulse/.test(treeCss + fileTreeSource), false, 'the old standalone file-tree recency pulse is gone');
    assert.equal(/10s ease-out/.test(treeCss), false, 'recency no longer uses the old one-shot ten-second pulse');
    assert.ok((tokensCss.match(/--file-tree-recency-hot:\s*var\(--file-tree-recency-max-contrast\);/g) || []).length >= 2, 'plain hot recency uses the shared max-contrast token in dark and light themes');
    assert.equal(/--file-tree-recency-hot:\s*var\(--bad\)/.test(tokensCss), false, 'plain hot recency is not red; red is ASK?-only');
    assert.ok(/\.file-tree-row:not\(\.selected\):not\(\.current-file\)\.file-tree-recency-just-updated > \.file-tree-date,[\s\S]*?\.file-tree-recency-hot > \.file-tree-date,[\s\S]*?\.file-tree-recency-fresh > \.file-tree-date\s*\{[\s\S]*font-weight:\s*800/.test(treeCss), 'newest recency rows stay bold through the shared date-cell rule');

    api.setFileTreeRecencyNowForTest(nowMs);
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 5, nowMs).key, 'just-updated', 'very recent mtime maps to the pulse-eligible recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 30, nowMs).key, 'hot', 'sub-minute mtime maps to the brightest non-pulsing recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 4 * 60, nowMs).key, 'fresh', 'five-minute-window mtime maps to the fresh recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 9 * 60, nowMs).key, 'recent', 'sub-ten-minute mtime still gets a recent recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 50 * 60, nowMs).key, 'recent', 'hour-window mtime maps to a middle recency bucket');
    assert.equal(api.fileTreeRecencyStateForMtimeForTest(nowSeconds - 3 * 24 * 60 * 60, nowMs).key, 'old', 'old mtime maps to the gray bucket');

    api.setFileExplorerTreeDateModeForTest('relative');
    const tree = new TestElement('finder-recency-tree');
    tree.setAttribute('role', 'tree');
    tree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    const rows = rowMap(tree);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'Ago mode marks very recent Finder rows just-updated');
    assert.equal(rows['/repo/just.md'].classList.contains('file-tree-recency-just-updated'), true, 'Ago mode applies the just-updated row class');
    assert.equal(dateCell(rows['/repo/just.md']).textContent, '<15 sec ago', 'Ago mode labels the pulse window with the matching sub-15-second text');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'very recent Finder rows pulse their date cell with the shared attention class');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('heartbeat-pulse'), true, 'very recent Finder rows inherit the shared heartbeat timing parent');
    assert.notEqual(dateCell(rows['/repo/just.md']).style.getPropertyValue('--attention-animation-delay'), '', 'date-cell pulse is phase-aligned with attentionAnimationDelay');
    assert.equal(rows['/repo/just.md'].style.getPropertyValue('--file-tree-recency-date-color'), 'var(--file-tree-recency-hot)', 'just-updated rows expose the token-backed date color');
    assert.equal(rows['/repo/hot.md'].dataset.recency, 'hot', 'Ago mode marks sub-minute Finder rows hot after the pulse window');
    assert.equal(dateCell(rows['/repo/hot.md']).textContent, '<1 min ago', 'Ago mode labels 15-60s rows as under one minute without pulsing');
    assert.equal(rows['/repo/hot.md'].style.getPropertyValue('--file-tree-recency-date-color'), 'var(--file-tree-recency-hot)', 'hot rows expose the same max-contrast date color');
    assert.equal(dateCell(rows['/repo/hot.md']).classList.contains('attention-pulse'), false, 'hot-but-not-just-updated rows do not pulse');
    assert.equal(rows['/repo/fresh.md'].dataset.recency, 'fresh', 'Ago mode marks fresh Finder rows without pulsing');
    assert.equal(rows['/repo/fresh.md'].style.getPropertyValue('--file-tree-recency-date-color'), 'var(--file-tree-recency-fresh)', 'older fresh rows keep their existing graduated color token');
    assert.equal(dateCell(rows['/repo/fresh.md']).classList.contains('attention-pulse'), false, 'fresh recency rows do not pulse');
    assert.equal(rows['/repo/ten.md'].dataset.recency, 'recent', 'Ago mode marks sub-ten-minute Finder rows recent without pulsing');
    assert.equal(rows['/repo/hour.md'].dataset.recency, 'recent', 'Ago mode marks hour-window Finder rows without pulsing');
    assert.equal(dateCell(rows['/repo/hour.md']).classList.contains('attention-pulse'), false, 'hour-window recency rows do not pulse');
    assert.equal(rows['/repo/old.md'].dataset.recency, 'old', 'Ago mode keeps old Finder rows in the gray bucket');
    assert.equal(dateCell(rows['/repo/old.md']).classList.contains('attention-pulse'), false, 'old Finder rows never pulse');

    api.setFileExplorerSelectionForTest(['/repo/just.md']);
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'selected rows still track the recency tier');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), false, 'selected rows suppress the recency attention pulse so selection colors win');
    api.setFileExplorerSelectionForTest([]);
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'clearing selection restores the pulse while the mtime is still fresh');

    const firstPulseUntil = rows['/repo/just.md'].__fileTreeRecencyAttentionUntilMs;
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(rows['/repo/just.md'].__fileTreeRecencyAttentionUntilMs, firstPulseUntil, 'same-mtime Finder refresh keeps the same stop time');
    api.setFileTreeRecencyNowForTest(nowMs + 10001);
    api.renderTreeChildrenForTest(tree, '/repo', entries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'hot', 'rows settle into the hot tier after the fifteen-second pulse window');
    assert.equal(dateCell(rows['/repo/just.md']).textContent, '<1 min ago', 'the label switches with the same fifteen-second boundary as the pulse');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), false, 'pulse class stops after the fifteen-second mtime window');

    const updatedEntries = entries.map(entry => entry.name === 'just.md'
      ? {...entry, mtime: (nowMs + 10001) / 1000 - 5}
      : entry);
    api.renderTreeChildrenForTest(tree, '/repo', updatedEntries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'mtime changes put the row back in the just-updated tier');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'mtime changes restart the shared date-cell pulse');
    assert.ok(rows['/repo/just.md'].__fileTreeRecencyAttentionUntilMs > firstPulseUntil, 'mtime-change pulse gets a new stop time');

    api.setFileExplorerTreeDateModeForTest('date');
    api.renderTreeChildrenForTest(tree, '/repo', updatedEntries);
    assert.equal(rows['/repo/just.md'].dataset.recency, 'just-updated', 'Date mode preserves Finder recency data');
    assert.equal(rows['/repo/just.md'].classList.contains('file-tree-recency-just-updated'), true, 'Date mode preserves Finder recency classes');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), true, 'Date mode keeps the shared pulse on the date cell');
    assert.equal(rows['/repo/ten.md'].dataset.recency, 'recent', 'Date mode preserves sub-ten-minute Finder recency');

    api.setFileExplorerTreeDateModeForTest('none');
    api.renderTreeChildrenForTest(tree, '/repo', updatedEntries);
    assert.equal(rows['/repo/just.md'].dataset.recency, undefined, 'None mode also leaves Finder recency data unset');
    assert.equal(dateCell(rows['/repo/just.md']).classList.contains('attention-pulse'), false, 'None mode removes date-cell attention pulse');

    api.setFileExplorerTreeDateModeForTest('relative');
    const differTree = new TestElement('differ-recency-tree');
    differTree.setAttribute('role', 'tree');
    differTree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(differTree, '/repo', updatedEntries, 0, [], {differMode: true});
    const differRows = rowMap(differTree);
    assert.equal(differRows['/repo/just.md'].dataset.recency, 'just-updated', 'Differ Ago rows use the shared recency state');
    assert.equal(dateCell(differRows['/repo/just.md']).classList.contains('attention-pulse'), true, 'Differ very recent rows pulse from shared recency rules');
    assert.equal(differRows['/repo/ten.md'].dataset.recency, 'recent', 'Differ Ago rows keep graduated recent styling');
    api.setFileExplorerTreeDateModeForTest('date');
    api.renderTreeChildrenForTest(differTree, '/repo', updatedEntries, 0, [], {differMode: true});
    assert.equal(differRows['/repo/just.md'].dataset.recency, 'just-updated', 'Differ Date rows preserve the recency signal');
    assert.equal(dateCell(differRows['/repo/just.md']).classList.contains('attention-pulse'), true, 'Differ Date rows keep the shared pulse');
    api.setFileTreeRecencyNowForTest(null);
  });

  test('t@6215', () => {
    const api = loadYolomux('', ['1']);
    const path = '/repo/app/common.py';
    const normalRows = api.filePopoverRows(path, {kind: 'text', size: 42}).join('');
    assert.equal((normalRows.match(/popover-copy-value/g) || []).length, 1);
    assert.ok(normalRows.includes('data-copy-path="/repo/app/common.py"'), 'file popover path copy uses the shared delegated copy attr');
    assert.equal(normalRows.includes('data-copy-popover-path'), false, 'file popover path copy no longer emits the dead popover-only copy attr');
    assert.equal(normalRows.includes('popover-subtitle'), false);
    assert.ok(normalRows.includes('file editor'));
    assert.equal(normalRows.includes('status'), false);
    const dirtyRows = api.filePopoverRows(path, {kind: 'text', dirty: true}).join('');
    assert.ok(dirtyRows.includes('status'));
    assert.ok(dirtyRows.includes('modified'));
  });

  test('t@6228', () => {
    const api = loadYolomux('', ['1']);
    const signature = api.directoryEntriesSignature([
      {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
      {name: 'a.txt', kind: 'file', size: 1, mtime: 10},
    ]);
    assert.equal(signature, api.directoryEntriesSignature([
      {name: 'a.txt', kind: 'file', size: 1, mtime: 10},
      {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
    ]));
    assert.notEqual(signature, api.directoryEntriesSignature([
      {name: 'a.txt', kind: 'file', size: 1, mtime: 11},
      {name: 'b.txt', kind: 'file', size: 2, mtime: 20},
    ]));
    assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 1}), false);
    assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 11, size: 1}), true);
    assert.equal(api.fileEntryChanged({mtime: 1780806618930051800, size: 1}, {mtime_ns: 1780806618930051885, size: 1}), false);
    assert.equal(api.fileEntryChanged({mtime: 1780806618930051800, size: 1}, {mtime_ns: 1780806618950051800, size: 1}), true);
    assert.equal(api.fileEntryChanged({mtime: 10, size: 1}, {mtime: 10, size: 2}), true);
  });

  test('t@6249', () => {
    const api = loadYolomux('', ['1']);
    const imagePath = '/home/test/a.png';
    const viewerItem = api.registerImageViewerLayoutItem(imagePath);
    assert.equal(viewerItem, api.imageViewerItemFor(imagePath));
    assert.deepStrictEqual(canonical(api.openFileEditorItems()), [viewerItem]);
    assert.deepStrictEqual(canonical(api.filePanelItemsForPath(imagePath)), [viewerItem]);
    assert.equal(api.fileItemPath(viewerItem), imagePath);
    const fileItem = api.registerFileEditorLayoutItem(imagePath);
    assert.equal(fileItem, api.fileEditorItemFor(imagePath));
    assert.deepStrictEqual(canonical(api.openFileEditorItems()), [viewerItem, fileItem]);
    assert.deepStrictEqual(canonical(api.filePanelItemsForPath(imagePath)), [viewerItem, fileItem]);
    assert.equal(api.imageOpenUsesSharedViewer(), true);
    assert.equal(api.imageOpenUsesSharedViewer({forceNewTab: true}), false);
    assert.equal(api.imageOpenUsesSharedViewer({targetSlot: 'left'}), false);

    api.setOpenFileStateForTest(imagePath, {kind: 'error', dirty: false, externalMissing: true, error: 'file deleted or moved on disk'});
    assert.equal(api.openFileIsMissing(imagePath), true);
    const missingHtml = api.fileEditorPaneTabHtml(fileItem);
    assert.ok(missingHtml.includes('file-tab-missing-badge'), 'missing file tabs show a badge');
    assert.ok(missingHtml.includes('a.png'), 'missing file tabs still show the basename');
    assert.equal(api.openFileStatus({kind: 'text', externalError: 'network down'}).message.includes('file state unknown'), true);
    assert.equal(api.openFileStatus({kind: 'text', externalError: 'network down'}).message.includes('deleted'), false, 'network/list refresh errors are not reported as deletion');
    assert.equal(
      api.fileInspectionErrorMessageForTest({payload: {error: 'outside allowed root'}, status: 403}, '/home/test/yolomux.dev3/docs/preview-samples/03-mixed.md'),
      'outside allowed root (HTTP 403)',
      'file inspection preserves the backend reason and HTTP status before falling back to the generic path message',
    );
    assert.equal(api.openFileStatus({kind: 'text', externalError: 'outside allowed root (HTTP 403)'}).message.includes('outside allowed root (HTTP 403)'), true);
  });

  test('t@6274', () => {
    const api = loadYolomux('', ['1']);
    const state = api.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
    assert.equal(state.copyRelativeDisabled, false);
    assert.equal(state.openInNewTabDisabled, false, 'text files can open a second editor tab from the shared file context menu');
    assert.equal(state.downloadDisabled, false);
    assert.equal(state.renameDisabled, false);
    assert.equal(state.deleteDisabled, false);
    const imageState = api.fileContextMenuState({kind: 'file', name: 'screen.png'}, ['/repo/app/screen.png'], ['screen.png']);
    assert.equal(imageState.openInNewTabDisabled, false);

    const readonlyApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly');
    const readonlyState = readonlyApi.fileContextMenuState({kind: 'file'}, ['/repo/app/a.txt'], ['a.txt']);
    // readonly is terminal-only — the server 403s every /api/fs/* read, so Download and file tab opens
    // are disabled in readonly to match, rather than offering a command that fails.
    assert.equal(readonlyState.downloadDisabled, true, 'readonly cannot download (server forbids /api/fs/raw)');
    const readonlyImage = readonlyApi.fileContextMenuState({kind: 'file', name: 'screen.png'}, ['/repo/app/screen.png'], ['screen.png']);
    assert.equal(readonlyImage.openInNewTabDisabled, true, 'readonly cannot open a file in a tab (server forbids the read)');
    assert.equal(readonlyState.renameDisabled, true);
    assert.equal(readonlyState.deleteDisabled, true);
  });

  test('t@6296', () => {
    const api = loadYolomux('', ['1']);
    const html = api.transcriptPathRowHtml('/tmp/yolomux/session.jsonl');
    assert.ok(html.includes('/tmp/yolomux/session.jsonl'));
    assert.ok(html.includes('data-copy-path'));
    assert.equal(api.transcriptPathRowHtml('').includes('no transcript path'), true);
  });

  test('path copy buttons route through one delegated handler', () => {
    const jsFiles = fs.readdirSync('static_src/js/yolomux')
      .filter(file => file.endsWith('.js'))
      .sort()
      .map(file => `static_src/js/yolomux/${file}`);
    const handlerSites = [];
    let source = '';
    for (const file of jsFiles) {
      const text = fs.readFileSync(file, 'utf8');
      source += text;
      for (const match of text.matchAll(/delegate\([^;\n]*'\[data-copy-path\]'[^;\n]*\)|closest\('\[data-copy-path\]'\)/g)) {
        handlerSites.push(`${file}:${match[0]}`);
      }
    }
    assert.deepStrictEqual(handlerSites, [
      "static_src/js/yolomux/10_core_utils.js:delegate(document, 'pointerup', '[data-copy-path]', handleCopyPathPointerUp, {capture: true})",
      "static_src/js/yolomux/10_core_utils.js:delegate(document, 'click', '[data-copy-path]', handleCopyPathClick, {capture: true})",
    ], 'all data-copy-path clicks are handled by the shared delegated owner');
    assert.ok(source.includes('globalThis.isSecureContext !== false && clipboard?.writeText'), 'copy avoids the async clipboard API when the page is explicitly insecure');
    assert.ok(source.includes('if (copyTextToClipboardViaCopyEvent(value)) return;'), 'copy falls back through a synchronous copy event before the textarea fallback');
    assert.ok(source.includes("statusOk(localizedHtml('status.copied'))"), 'copy success reports a generic copied status for path, session ID, and transcript buttons');
    assert.ok(source.includes("statusErr(localizedHtml('status.copyFailed', {error}))"), 'copy failure reports the error through the shared status line');
    assert.equal(source.includes('data-copy-transcript-path'), false, 'terminal transcript path no longer uses a parallel copy attribute');
  });

  await testAsync('popover copy buttons copy on pointerup/click and leave the popover open', async () => {
    const api = loadYolomux('', ['1']);
    const popover = new TestElement('copy-popover');
    popover.className = 'session-popover popover-open';
    const button = new TestElement('copy-button', 'button');
    button.dataset.copyPath = '/repo/app/common.py';
    popover.appendChild(button);
    const dispatch = (type, target, detail = 1) => {
      const event = {
        type,
        detail,
        target,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
        stopImmediatePropagation() { this.immediatePropagationStopped = true; },
      };
      for (const listener of api.documentListenersForTest(type)) listener(event);
      return event;
    };

    api.clearClipboardTextForTest();
    const pointerEvent = dispatch('pointerup', button, 1);
    await flushAsyncWork();
    assert.equal(api.clipboardTextForTest(), '/repo/app/common.py', 'pointerup copies the full value before the popover stops bubble-phase clicks');
    assert.equal(pointerEvent.defaultPrevented, true, 'copy pointerup suppresses tab/popover activation');
    assert.equal(pointerEvent.propagationStopped, true, 'copy pointerup does not bubble into popover dismissal');
    assert.ok(api.statusHtmlForTest().includes('copied'), 'copy success gives visible feedback');
    assert.equal(popover.classList.contains('popover-open'), true, 'copy success leaves the popover open');

    button.dataset.copyPath = '/repo/app/duplicate.py';
    dispatch('click', button, 1);
    await flushAsyncWork();
    assert.equal(api.clipboardTextForTest(), '/repo/app/common.py', 'the pointer-generated click is ignored after pointerup copies once');

    button.dataset.copyPath = 'keyboard-session-id';
    dispatch('click', button, 0);
    await flushAsyncWork();
    assert.equal(api.clipboardTextForTest(), 'keyboard-session-id', 'keyboard click activation still copies');
    assert.equal(popover.classList.contains('popover-open'), true, 'keyboard copy also leaves the popover open');
  });

  test('t@6304', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.editorWrapValue(false), 'off');
    assert.equal(api.editorWrapValue(true), 'soft');
    assert.equal(api.rawFileUrl('/repo/app/a b.txt', {v: 7}), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&v=7');
    assert.equal(api.rawFileDownloadUrl('/repo/app/a b.txt'), '/api/fs/raw?path=%2Frepo%2Fapp%2Fa%20b.txt&download=1');
    assert.deepStrictEqual({...api.markdownPreviewImageTarget('.uploads/pasted image.png', '/repo/docs/note.md')}, {
      src: '/api/fs/raw?path=%2Frepo%2Fdocs%2F.uploads%2Fpasted%20image.png',
      path: '/repo/docs/.uploads/pasted image.png',
      external: false,
    }, 'Markdown preview resolves editor-pasted relative .uploads images beside the Markdown file');
  });

  test('t@6312', () => {
    const api = loadYolomux('', ['1']);
    const pixelWheel = api.terminalWheelSignedLines({deltaY: 105, deltaMode: 0}, 40);
    assert.ok(pixelWheel > 2.5 && pixelWheel < 3.5, 'mouse-like pixel wheel remains about three lines');
    const touchpadTick = api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0}, 40);
    assert.ok(touchpadTick > 0 && touchpadTick < 0.2, 'small touchpad pixel deltas accumulate as fractions');
    assert.equal(api.terminalWheelSignedLines({deltaY: -3, deltaMode: 1}, 40), -3);
    assert.equal(api.terminalWheelSignedLines({deltaY: 1, deltaMode: 2}, 40), 12);
    assert.equal(api.terminalWheelSignedLines({deltaY: 999, deltaMode: 0}, 40), 12);
    assert.equal(api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0, ctrlKey: true}, 40), 0);
    assert.equal(api.terminalWheelSignedLines({deltaY: 4, deltaMode: 0, shiftKey: true}, 40), 34);
  });

  test('t@6318', () => {
    // Regression: alt-screen panes (claude/codex/vim) must NOT route the wheel into tmux copy-mode.
    // Their tmux pane has no scrollback, so the wheel has to reach the app instead. The wheel handler
    // gates on sessionPaneIsAlternateScreen.
    const api = loadYolomux('', ['1']);
    const altScreenPane = {
      windows: [{
        key: '1:0', session: '1', window_index: '0', active: true,
        panes: [{
          window_key: '1:0', session: '1', window_index: '0', pane_index: '0',
          target: '%11', pane_id: '%11', current_command: 'claude',
          active: true, alternate_on: true, pid: 1234, dead: false,
        }],
      }],
    };
    api.setTmuxSignalStateForTest(altScreenPane);
    assert.equal(api.sessionPaneIsAlternateScreen('1'), true, 'claude alt-screen pane defers the wheel to the app');
    const shellPane = JSON.parse(JSON.stringify(altScreenPane));
    shellPane.windows[0].panes[0].current_command = 'bash';
    shellPane.windows[0].panes[0].alternate_on = false;
    api.setTmuxSignalStateForTest(shellPane);
    assert.equal(api.sessionPaneIsAlternateScreen('1'), false, 'a normal shell pane keeps tmux copy-mode scrollback');
    api.setTmuxSignalStateForTest(null);
    assert.equal(api.sessionPaneIsAlternateScreen('1'), false, 'no signal state means no alt-screen claim');
  });

  test('t@6325', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.agentErrorIsBlocking('codex transcript not found by process fd or cwd'), false);
    assert.equal(api.agentErrorIsBlocking('missing /home/test/.claude/sessions/123.json'), false);
    assert.equal(api.agentErrorIsBlocking('worker crashed'), true);
    assert.notEqual(api.sessionState('1', {agents: [{kind: 'codex', error: 'codex transcript not found by process fd or cwd'}]}).key, 'blocked');
    assert.notEqual(api.sessionState('1', {agents: [{kind: 'claude', error: 'missing /home/test/.claude/sessions/123.json'}]}).key, 'blocked');
    assert.equal(api.sessionState('1', {agents: [{kind: 'codex', error: 'worker crashed'}]}).key, 'blocked');
  });

  test('t@6335', () => {
    const api = loadYolomux('', ['1']);
    api.setAutoApproveStateForTest('1', {
      enabled: false,
      prompt: {visible: false},
      screen: {key: 'approval', text: 'Do you want to proceed?'},
    });
    const state = api.sessionState('1', {agents: [{kind: 'codex'}], panes: []});
    assert.equal(state.key, 'needs-approval', 'roster screen approval state lights ASK? even when prompt.visible is absent');
    assert.equal(state.reason, 'Do you want to proceed?');
  });

  test('t@6341', () => {
    const api = loadYolomux('', ['1']);
    api.setDocumentTitleNowForTest(200000);
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 199,
        bell_flag: false,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: true,
          pid: 1234,
          dead: false,
        }],
      }],
    });
    assert.equal(api.sessionState('1', {agents: [], panes: []}).key, 'working', 'tmux command + pid/alternate screen marks an agent running');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        silence_flag: true,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: true,
          pid: 1234,
          dead: false,
        }],
      }],
    });
    const silent = api.sessionState('1', {agents: [], panes: []});
    assert.equal(silent.key, 'done', 'tmux silence alert marks a quiet live agent done');
    assert.ok(api.sessionStateHtml(silent).includes('session-state-done'), 'tmux silence done state renders a tab badge');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: true,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: false,
          pid: 0,
          dead: true,
          dead_status: 2,
        }],
      }],
    });
    assert.equal(api.sessionState('1', {agents: [], panes: []}).key, 'needs-input', 'tmux bell alert has attention priority');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'node',
          title: '[ . ] Action Required | yolomux.dev8001',
          alternate_on: false,
          pid: 0,
          dead: false,
        }],
      }],
    });
    const actionRequired = api.sessionState('1', {agents: [], panes: []});
    assert.equal(actionRequired.key, 'needs-input', 'Codex action-required pane title marks the session as ASK?');
    assert.equal(actionRequired.reason, 'tmux agent action required');
    api.setTmuxSignalStateForTest({
      windows: [{
        key: '1:0',
        session: '1',
        window_index: '0',
        activity_ts: 10,
        bell_flag: false,
        silence_flag: false,
        panes: [{
          window_key: '1:0',
          session: '1',
          window_index: '0',
          pane_index: '0',
          target: '%11',
          pane_id: '%11',
          current_command: 'codex',
          alternate_on: false,
          pid: 0,
          dead: true,
          dead_status: 2,
        }],
      }],
    });
    const exited = api.sessionState('1', {agents: [], panes: []});
    assert.equal(exited.key, 'done', 'dead tmux agent pane marks the session done');
    assert.equal(exited.reason, 'agent exited (status 2)');
    assert.ok(api.sessionStateHtml(exited).includes('session-state-done'), 'dead-agent done state renders a tab badge');
  });

  test('t@6347', () => {
    const api = loadYolomux('', ['1']);
    const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHant);
    api.setActiveLocaleForTest('zh-Hant');
    api.registerTerminalForTest('1', {}, {readyState: 3});
    assert.equal(api.sessionState('1').reason, zhHant['state.reason.terminalConnectionClosed'], 'disconnected terminal reason is localized');
    api.registerTerminalForTest('1', {}, {readyState: 1});
    api.setAutoApproveStateForTest('1', {screen: {key: 'disconnected', text: 'failed to capture pane'}});
    assert.equal(api.sessionState('1', {}).reason, zhHant['state.reason.terminalScreenUnavailable'], 'backend capture failure maps to the localized disconnected fallback');
  });

  test('t@6359', () => {
    const api = loadYolomux('', ['1', '2', '3']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    slots.left = api.paneStateWithTabs(['1', '__info__'], '1');
    slots.slot1 = api.paneStateWithTabs(['2'], '2');
    api.rememberFileExplorerOpenIntentForTest(false);
    api.setLayoutSlotsForTest(slots);

    assert.equal(api.itemIsBackgroundPaneTab('__info__'), true);
    assert.equal(api.itemIsBackgroundPaneTab('1'), false);
    assert.deepStrictEqual(canonical(api.backgroundTabItems()), ['__info__']);
    assert.deepStrictEqual(canonical(api.inactiveTabItems()), ['__files__', '__search_history__', '__prefs__', '3']);
  });

  test('t@6373', () => {
    const api = loadYolomux('', ['1']);
    const firstEditor = api.registerFileEditorLayoutItem('/repo/app/one.md');
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    slots.left = api.paneStateWithTabs([firstEditor], firstEditor);
    slots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    assert.equal(api.slotForNewFileEditorTab(), 'left');
  });

  test('t@6384', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/repo/app'), 'file.txt');
    assert.equal(api.pathRelativeToDirectory('/repo/app/src/file.txt', '/repo/app'), 'src/file.txt');
    assert.equal(api.pathRelativeToDirectory('/repo/app', '/repo/app'), '.');
    assert.equal(api.pathRelativeToDirectory('/repo/app/file.txt', '/'), 'repo/app/file.txt');
    assert.equal(api.pathRelativeToDirectory('/other/file.txt', '/repo/app'), '/other/file.txt');
  });

  test('t@6393', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.splitPercentForNewItem('1', 'left'), 50);
    assert.equal(api.splitPercentForNewItem('1', 'right'), 50);
    assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'left'), 50);
    assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right'), 50);
    assert.equal(api.splitPercentForNewItem('file:/repo/app/TODO.md', 'right', 42), 42);
    assert.equal(api.splitPercentForNewItem('__files__', 'left'), 22);
    assert.equal(api.splitPercentForNewItem('__files__', 'right'), 78);
  });

  test('t@6404', () => {
    const api = loadYolomux('', ['1']);
    const windowPanes = [
      {window: '2', window_name: 'codex', window_active: false, active: true, command: 'node', pid: 222},
      {window: '1', window_name: 'bash', window_active: false, active: true, command: 'bash', pid: 111},
      {window: '3', window_name: 'node', process_label: 'codex', process_label_pid: 3333, pid: 333, window_active: true, active: true, command: 'node'},
    ];
    assert.deepStrictEqual(canonical(api.tmuxWindowRecords(windowPanes).map(item => ({
      indexText: item.indexText,
      buttonNameLabel: item.buttonNameLabel,
      nameLabel: item.nameLabel,
      numberLabel: item.numberLabel,
      indexedButtonLabel: item.indexedButtonLabel,
      indexedNameLabel: item.indexedNameLabel,
      processLabel: item.processLabel,
      pid: item.pid,
      active: item.active,
    }))), [
      {indexText: '1', buttonNameLabel: 'bash', nameLabel: 'bash (pid=111)', numberLabel: '1', indexedButtonLabel: '1:bash', indexedNameLabel: '1:bash (pid=111)', processLabel: 'bash (pid=111)', pid: 111, active: false},
      {indexText: '2', buttonNameLabel: 'codex(2)', nameLabel: 'codex(2) (pid=222)', numberLabel: '2', indexedButtonLabel: '2:codex', indexedNameLabel: '2:codex (pid=222)', processLabel: 'codex (pid=222)', pid: 222, active: false},
      {indexText: '3', buttonNameLabel: 'codex(3)', nameLabel: 'codex(3) (pid=3333)', numberLabel: '3', indexedButtonLabel: '3:codex', indexedNameLabel: '3:codex (pid=3333)', processLabel: 'codex (pid=3333)', pid: 3333, active: true},
    ], 'P5: tmux window records sort by index and disambiguate duplicate names with the window index');
    const duplicateBashRecords = api.tmuxWindowRecords([
      {window: '2', window_name: 'bash', pid: 202},
      {window: '3', window_name: 'bash', pid: 303},
      {window: '4', window_name: 'bash', pid: 404},
    ]);
    assert.deepStrictEqual(canonical(duplicateBashRecords.map(item => item.indexedButtonLabel)), ['2:bash', '3:bash', '4:bash'], 'indexed window labels do not repeat the index suffix');
    assert.deepStrictEqual(canonical(duplicateBashRecords.map(item => item.buttonNameLabel)), ['bash(2)', 'bash(3)', 'bash(4)'], 'name-only labels keep the duplicate-name disambiguation suffix');
    const windowBarHtml = api.tmuxWindowBarHtml('1', {panes: windowPanes});
    assert.ok(windowBarHtml.includes('data-tmux-window-label-mode="names"'), 'P5: normal window bars prefer names');
    assert.ok(windowBarHtml.includes('data-window-index="1"'), 'P5: window bar button targets window 1');
    assert.ok(windowBarHtml.includes('data-window-index="2"'), 'P5: window bar button targets window 2');
    assert.ok(/class="tab tmux-window-button active"[^>]*data-window-index="3"[^>]*aria-pressed="true"/.test(windowBarHtml), 'P5: active tmux window button is highlighted and pressed');
    assert.ok(windowBarHtml.includes('<span class="tmux-window-name-label"><span class="tmux-window-name-text">1:bash</span></span>'), 'tmux window buttons show index:name without pid');
    assert.ok(windowBarHtml.includes('<span class="tmux-window-name-label"><span class="tmux-window-name-text">2:codex</span></span>'), 'AI tmux window button labels use the canonical index:agent kind');
    assert.equal(windowBarHtml.includes('(pid='), false, 'tmux window button labels do not show process pids');
    assert.equal(windowBarHtml.includes('3:node'), false, 'DOIT.53 P2: process-aware agent labels beat raw tmux window names like node');
    assert.equal(windowBarHtml.includes('data-window-agent'), false, 'tmux window buttons no longer carry per-agent color tags');
    const nowSeconds = Date.now() / 1000;
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', state: 'working', window_index: 3, last_active_ts: nowSeconds, window_label: '3:codex'},
      {kind: 'codex', state: 'idle', window_index: 2, last_active_ts: nowSeconds - 120, idle_since: nowSeconds - 120, window_label: '2:codex'},
    ]});
    assert.equal(api.agentWindowActivityIconForTest('codex', 'working', 0).icon, '●', 'working AI windows use the shared working icon');
    assert.equal(api.agentWindowActivityIconForTest('claude', 'idle', 60).icon, '○', 'idle AI windows use the shared idle icon after one minute');
    assert.equal(api.agentWindowActivityIconForTest('claude', 'idle', 10), null, 'recent idle AI windows do not show an idle icon yet');
    assert.equal(api.agentWindowActivityIconForTest('shell', 'working', 300), null, 'non-AI windows do not show working or idle icons');
    const transitionKey = '1:3:codex';
    assert.equal(api.agentWindowActivityIconForTest('codex', 'working', 0, {transitionKey, nowSeconds: 1000, scheduleRefresh: false}).state, 'working', 'working transition state is recorded');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey, nowSeconds: 1005, scheduleRefresh: false}).state, 'cooldown', 'a window that just stopped working shows static yellow for the dedicated 60-second cooldown');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 20, {transitionKey, nowSeconds: 1020, scheduleRefresh: false}).state, 'cooldown', 'the stopped marker stays yellow during the dedicated cooldown instead of using file-recency timing');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 0, {transitionKey, nowSeconds: 1065, scheduleRefresh: false}).state, 'settled', 'after the dedicated cooldown the stopped marker becomes settled black');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'needs-input', 0, {transitionKey, nowSeconds: 1061, scheduleRefresh: false}).state, 'attention', 'needs-input outranks cooldown and stays on the persistent red attention state');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'approval', 0, {transitionKey, nowSeconds: 1062, scheduleRefresh: false}).state, 'attention', 'approval prompts use the same persistent red attention state');
    assert.equal(api.agentWindowActivityIconForTest('codex', 'idle', 120, {transitionKey: 'cold-idle', nowSeconds: 2000, scheduleRefresh: false}).state, 'idle', 'an AI window never observed working stays hollow idle instead of flashing red');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'claude', state: 'needs-input', window_index: 0, window_label: '0:claude'},
    ]});
    const activeAskWindowBarHtml = api.tmuxWindowBarHtml('1', {panes: [
      {window: '0', window_name: 'claude', window_active: true, active: true, command: 'claude', pid: 4444},
    ]});
    assert.ok(/class="tab tmux-window-button active"[\s\S]*data-window-index="0"[\s\S]*0:claude[\s\S]*agent-window-activity-icon--attention[\s\S]*status-indicator--attention/.test(activeAskWindowBarHtml), 'active 0:claude ASK? window button renders the red shared attention dot, not an idle/current white dot');
    api.setAutoApproveStateForTest('1', {agent_windows: [
      {kind: 'codex', state: 'working', window_index: 3, last_active_ts: nowSeconds, window_label: '3:codex'},
      {kind: 'codex', state: 'idle', window_index: 2, last_active_ts: nowSeconds - 120, idle_since: nowSeconds - 120, window_label: '2:codex'},
    ]});
    const windowBarWithStatusHtml = api.tmuxWindowBarHtml('1', {panes: windowPanes});
    assert.ok(windowBarWithStatusHtml.includes('status-indicator') && windowBarWithStatusHtml.includes('agent-window-activity-icon--working'), 'window bar renders the shared working icon after AI labels');
    assert.ok(windowBarWithStatusHtml.includes('status-indicator--dot') && windowBarWithStatusHtml.includes('agent-window-activity-icon--idle'), 'window bar renders the shared idle icon after one-minute idle AI labels');
    assert.ok(!/<span class="tmux-window-name-text">1:bash<\/span><span class="agent-window-activity-icon/.test(windowBarWithStatusHtml), 'bash window labels do not get AI activity icons');
    const manyWindows = Array.from({length: 9}, (_unused, index) => ({
      window: String(index + 1),
      window_name: `w${index + 1}`,
      window_active: index === 0,
      active: true,
      command: 'bash',
    }));
    assert.equal(api.tmuxWindowBarLabelMode(api.tmuxWindowRecords(manyWindows)), 'numbers', 'P5: many windows fall back to numeric labels');
    assert.ok(api.tmuxWindowBarHtml('1', {panes: manyWindows}).includes('data-tmux-window-label-mode="numbers"'), 'P5: numeric fallback is reflected in the rendered bar');
    api.setTranscriptInfoForTest('1', {panes: windowPanes});
    const controls = api.panelControlsHtml('1');
    assert.equal(controls.includes('data-tmux-window-bar="1"'), false, 'DOIT.53 P1: tmux pane header controls do not render the window bar');
    assert.equal(controls.includes('tmux-window-step'), false, 'P5: tmux pane controls no longer render the old prev/next stepper');
    assert.ok(controls.includes('terminal-tab'), 'DOIT.53 P1: tmux pane header keeps only the terminal tab label in the top row');
    assert.ok(controls.includes('>Term</button>'), 'DOIT.56 N3: terminal header button keeps the static Term label');
    assert.equal(controls.includes('>codex</button>') || controls.includes('>node</button>'), false, 'DOIT.56 N3: terminal header no longer duplicates active window/process names');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const yoloCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/tmuxWindowBarHtml\(session, transcriptMeta\.sessions\?\.\[session\]\)[\s\S]{0,180}class="panel-detail-close"/.test(source), 'DOIT.53 P1: tmux window bar is rendered on the detail row before the close button');
    assert.ok(/delegate\(panel, 'click', '\[data-window-dir\], \[data-window-index\]'/.test(source), 'DOIT.53 P3: in-panel window buttons use the shared delegated click path');
    assert.ok(/\.panel\.details-collapsed \.panel-detail-row\s*\{[\s\S]*display:\s*none/.test(yoloCss), 'DOIT.53 P1: detail-row window bar collapses with the detail row');
    assert.equal(yoloCss.includes('.panel-agent-badge'), false, 'DOIT.57 T1: the duplicate Info Bar agent-badge CSS is removed');
    assert.equal(source.includes('panel-agent-slot'), false, 'DOIT.57 T1: no agent-badge slot is rendered in the detail row');
    assert.ok(/\.tmux-window-button\.active\s*\{[\s\S]*background:\s*var\(--active-control-bg\)/.test(yoloCss), 'DOIT.57 T2: the active window button is a pressed toggle via the shared active-control tokens');
    assert.equal(/\.tmux-window-button\.active\s*\{[^}]*#[0-9a-fA-F]{3,6}/.test(yoloCss), false, 'DOIT.57 T2: the active window button uses theme-aware tokens, not hardcoded hex');
    assert.ok(/\.tmux-window-button\.active \.agent-window-activity-icon\s*\{[\s\S]*text-shadow:\s*0 0 0 var\(--active-control-text\), 0 0 4px var\(--active-control-text\)/.test(yoloCss), 'active tmux window activity dots reuse active-control text for contrast');
    assert.ok(/\.tmux-window-button\.active \.agent-window-activity-icon\.status-indicator--attention\s*\{[\s\S]*color:\s*var\(--bad\)[\s\S]*text-shadow:\s*0 0 0 var\(--bad\), 0 0 6px rgb\(var\(--attention-ring-rgb, 255 51 71\) \/ 0\.85\)/.test(yoloCss), 'active ASK? window dots keep the saturated red attention color instead of the active-control white halo');
    assert.ok(/\.status-indicator--dot\.heartbeat-pulse\s*\{[\s\S]*--attention-pulse-brightness-rest:\s*0\.82[\s\S]*--attention-pulse-brightness-peak:\s*1\.34/.test(yoloCss), 'active tmux window activity dots inherit the shared brightness pulse in the built CSS');
    assert.equal(yoloCss.includes('window-agent-color') || yoloCss.includes('data-window-agent'), false, 'tmux window buttons have no per-agent tint CSS');
    assert.ok(source.includes('const AGENT_WINDOW_COOLDOWN_SECONDS = 60'), 'agent window cooldown has its own 60-second owner, separate from file-recency timing');
    assert.ok(source.includes("item.state === 'cooldown' ? 'cooldown'"), 'agent window stopped state maps to the shared cooldown tone instead of red attention');
    assert.ok(yoloCss.includes('.status-indicator--cooldown') && yoloCss.includes('var(--accent-gold)'), 'cooldown dot uses the shared theme-aware yellow/gold token');
    assert.ok(/status-indicator--dot\.status-indicator--working\.heartbeat-pulse[\s\S]*animation-delay:\s*var\(--attention-animation-delay/.test(yoloCss), 'working dots use the shared ASK? animation phase');
    assert.equal(source.includes("status-indicator--cooldown', 'heartbeat-pulse"), false, 'cooldown tone does not inherit heartbeat in the built source');
    assert.equal(/status-indicator--dot\.status-indicator--cooldown\.heartbeat-pulse|\.status-indicator--cooldown\s*\{[^}]*--attention-ring-rgb/.test(yoloCss), false, 'cooldown dots are static yellow in the built CSS');
    assert.equal(/status-indicator--dot\.status-indicator--working\.heartbeat-pulse[\s\S]{0,240}animation-direction:\s*alternate/.test(yoloCss), false, 'working dots no longer double the pulse period with alternate direction');
    assert.ok(/\.panel-detail-row \.tmux-window-bar\s*\{[\s\S]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(yoloCss), '2026-06-11 Info Bar regression: tmux window bar right-aligns next to the detail close button');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\.dockview-inner-head-collapsed\.details-collapsed\s*\{\s*grid-template-rows:\s*minmax\(0, 1fr\)/.test(yoloCss), '2026-06-11 Info Bar regression: Dockview terminals get one full-height grid row when both inner header and details are hidden');
    assert.ok(/function setPanelDetailsCollapsed\(panel, collapsed\)\s*\{[\s\S]*schedulePanelDetailsFit\(panel\)/.test(source), '2026-06-11 Info Bar regression: details toggle refits visible tmux terminals after row height changes');
    assert.equal(source.includes('function windowStepButtonHtml'), false, 'DOIT.56 N3: dead header tmux stepper renderer stays removed');
    assert.equal(/button\.textContent = terminalTabLabel/.test(source), false, 'DOIT.56 N3: metadata refresh no longer rewrites the static terminal tab label');
    const calls = [];
    const button1 = tmuxWindowButtonElement('1', '1', false);
    const button3 = tmuxWindowButtonElement('1', '3', true);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('1', [button1, button3]));
    api.setFetchForTest((url, options = {}) => {
      calls.push({url: String(url), method: options.method || 'GET'});
      return new Promise(() => {});
    });
    api.tmuxWindowForTest('1', {windowIndex: '1'}, 'tmux window 1:bash');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'direct window clicks mark the clicked button active synchronously before POST resolution');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '1')?.getAttribute('aria-pressed'), 'true', 'direct window clicks sync aria-pressed before POST resolution');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '3')?.classList.contains('active'), false, 'direct window clicks clear the previous active button synchronously');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '3')?.getAttribute('aria-pressed'), 'false', 'direct window clicks clear the previous pressed state synchronously');
    assert.deepStrictEqual(calls, [{url: '/api/tmux-window?session=1&window=1', method: 'POST'}], 'P5: clicking a window button posts direct select-window for that index');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('1', {panes: windowPanes})), ['1'], 'stale interim renders keep the explicit target highlighted until read-back confirms');
    calls.length = 0;
    const directMetaInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/home/u'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/home/u'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
      project: {
        git: {root: '/repo/agent', cwd: '/repo/agent/src', branch: 'agent-work', dirty_count: 8}, pull_request: null, linear: [],
        repos: [{root: '/repo/agent', cwd: '/repo/agent/src', branch: 'agent-work', dirty_count: 8, primary: true}],
      },
    };
    api.setTranscriptInfoForTest('meta-preview', directMetaInfo);
    const metaNode = api.testElementForId('meta-meta-preview');
    metaNode.innerHTML = 'stale';
    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:bash');
    assert.notEqual(metaNode.innerHTML, 'stale', 'clicking a known tmux window updates path/repo metadata without waiting for the next transcript poll');
    assert.ok(metaNode.innerHTML.includes('/tmp/shell'), 'known target-window pane path is reflected immediately in the Info Bar');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: bash', 'terminal detail label follows the optimistic target window, not stale backend-active metadata');
    assert.deepStrictEqual(calls, [{url: '/api/tmux-window?session=meta-preview&window=1', method: 'POST'}], 'tmux window click still posts the authoritative select-window request');
    const relativeApi = loadYolomux('', ['meta-preview']);
    relativeApi.setTranscriptInfoForTest('meta-preview', {
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/agent/src'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/agent/src'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
    });
    const terminalFrames = [];
    relativeApi.registerTerminalForTest('meta-preview', {focus() {}}, {readyState: WebSocket.OPEN, send(message) { terminalFrames.push(JSON.parse(message)); }});
    relativeApi.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      return Promise.resolve(jsonResponse({entries: [], path: '/repo/agent/src'}));
    });
    const relativeMetaNode = relativeApi.testElementForId('meta-meta-preview');
    const numericButton0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const numericButton1 = tmuxWindowButtonElement('meta-preview', '1', false);
    relativeApi.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [numericButton0, numericButton1]));
    relativeMetaNode.innerHTML = 'stale';
    assert.equal(relativeApi.handleTerminalDataForTest('meta-preview', '\x02n'), true, 'Ctrl-b n terminal bytes are still accepted by the transport path');
    assert.equal(terminalFrames.at(-1).data, '\x02n', 'Ctrl-b n is still sent verbatim to tmux');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(relativeApi.testElementForId('body')), [], 'Ctrl-b n clears the current active button synchronously until tmux confirms the real window');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '0')?.getAttribute('aria-pressed'), 'false', 'Ctrl-b n clears aria-pressed synchronously');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '1')?.classList.contains('active'), false, 'Ctrl-b n does not guess the next active button locally');
    assert.equal(relativeMetaNode.innerHTML, 'stale', 'Ctrl-b n does not locally predict path/repo metadata');
    relativeMetaNode.innerHTML = 'stale again';
    assert.equal(relativeApi.handleTerminalDataForTest('meta-preview', '\x02'), true, 'a split Ctrl-b prefix is still sent verbatim');
    assert.equal(relativeApi.handleTerminalDataForTest('meta-preview', '1'), true, 'a split tmux numeric selection key is still sent verbatim');
    assert.deepStrictEqual(terminalFrames.slice(-2).map(frame => frame.data), ['\x02', '1'], 'split tmux prefix and digit are not swallowed or merged');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(relativeApi.testElementForId('body')), ['1'], 'Ctrl-b then a number marks that explicit target active synchronously');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '1')?.getAttribute('aria-pressed'), 'true', 'Ctrl-b numeric selection syncs aria-pressed synchronously');
    assert.equal(tmuxWindowButtonFromElement(relativeApi.testElementForId('body'), '0')?.classList.contains('active'), false, 'Ctrl-b numeric selection clears the previous active button synchronously');
    assert.notEqual(relativeMetaNode.innerHTML, 'stale again', 'Ctrl-b then a number updates known target-window path/repo metadata immediately');
    assert.ok(relativeMetaNode.innerHTML.includes('/tmp/shell'), 'Ctrl-b numeric target uses the known target-window pane path immediately');
    const tmuxPrefixObserver = source.slice(source.indexOf('function observeTerminalTmuxPrefixWindowSwitches(session, data)'), source.indexOf('function handleTerminalData(session, data)'));
    assert.ok(tmuxPrefixObserver.includes("char === '\\x02'") && tmuxPrefixObserver.includes('terminalTmuxPrefixWindowShortcut(char)'), 'terminal transport observes tmux prefix window shortcuts without owning the bytes');
    assert.equal(source.includes('function previewTmuxWindowInfo'), false, 'tmux window switching has no relative-index predictor');
    assert.equal(source.includes('function previewTmuxWindowLabel'), false, 'tmux window switching has no optimistic local label repaint');
    assert.ok(/function noteTerminalTmuxWindowSwitch\(session, shortcut\)[\s\S]*const sequence = directIndex !== null[\s\S]*setTmuxWindowActiveIndexOverride\(session, directIndex\)[\s\S]*setTmuxWindowActiveIndexPending\(session\)[\s\S]*expectedIndex: directIndex, sequence[\s\S]*previousIndex, sequence/.test(source), 'terminal prefix observer highlights explicit targets and carries a sequence through readback');
    assert.ok(/function handleTerminalData\(session, data\)[\s\S]*observeTerminalTmuxPrefixWindowSwitches\(session, filtered\);[\s\S]*socket\.send\(JSON\.stringify\(\{type: 'input', data: filtered\}\)\)/.test(source), 'tmux prefix observation happens before sending the unchanged terminal bytes');
    assert.ok(/const sequence = setTmuxWindowActiveIndexOverride\(session, directIndex\)[\s\S]*apiFetchJson\(`\/api\/tmux-window\?session=\$\{encodeURIComponent\(session\)\}&window=\$\{encodeURIComponent\(String\(directIndex\)\)\}`[\s\S]*tmuxWindowSwitchSequenceMatches\(session, sequence\)[\s\S]*scheduleTmuxWindowReadback\(session, \{delayMs: 0, clearActiveIndexOverride: true, expectedIndex: directIndex, sequence\}\)/.test(source), 'direct window buttons highlight before POST and keep the optimistic target until authoritative confirmation');
    assert.ok(/function setTmuxWindowActiveIndexOverride\(session, windowIndex, options = \{\}\)[\s\S]*applyTmuxWindowActiveIndexToTranscriptInfo\(String\(session\), indexKey, \{render: true\}\)/.test(source), 'known direct tmux targets overlay transcript metadata immediately so stale polls do not flash the old window');
    assert.ok(/async function applyTranscriptsPayload\(payload, options = \{\}\)[\s\S]*transcriptMeta = transcriptPayloadWithTmuxWindowOverrides\(payload\)/.test(source), 'incoming transcript payloads preserve pending direct-window overrides');
    assert.ok(/async function refreshTmuxWindowActiveFromSignals\(session, options = \{\}\)[\s\S]*apiFetchJson\(tmuxWindowSignalReadbackUrl\(session\)/.test(source), 'tmux window readback uses the session-scoped lightweight tmux-signals endpoint');
    assert.ok(/function setTmuxWindowActiveIndexOverride\(session, windowIndex, options = \{\}\)[\s\S]*refreshTabberPanelsForTmuxWindowChange\(\)/.test(source), 'Tabber repaints immediately when a known tmux window target is selected');
    assert.ok(/function setTmuxWindowActiveIndexPending\(session, options = \{\}\)[\s\S]*refreshTabberPanelsForTmuxWindowChange\(\)/.test(source), 'Tabber repaints immediately when an unknown tmux window target is pending');
    assert.ok(/function applyTmuxSignalActiveWindowsToTranscriptInfo\(payload = \{\}\)[\s\S]*updatePanelHeader\(session, transcriptMeta\.sessions\?\.\[session\]\)[\s\S]*renderInfoPanel\(\);[\s\S]*refreshTabberPanels\(\)/.test(source), 'probe-confirmed tmux window readback repaints the Tabber without waiting for the activity poll');
  });

  await testAsync('tmux window direct failure rolls back optimistic metadata', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const info = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', info);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/tmux-window')) return Promise.reject(new Error('tmux select failed'));
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      return Promise.resolve(jsonResponse({entries: [], path: '/repo/claude'}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:claude');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'direct click applies the known target-window metadata synchronously');

    await flushAsyncWork();
    await flushAsyncWork();

    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), undefined, 'failed direct select clears the optimistic target');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: codex', 'failed direct select restores the previous active-window metadata');
  });

  await testAsync('tmux window explicit readback ignores stale active window', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const button0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const button1 = tmuxWindowButtonElement('meta-preview', '1', false);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [button0, button1]));
    api.setTranscriptInfoForTest('meta-preview', {
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/agent/src'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
    });
    api.registerTerminalForTest('meta-preview', {focus() {}}, {readyState: WebSocket.OPEN, send() {}});
    assert.equal(api.handleTerminalDataForTest('meta-preview', '\x021'), true, 'Ctrl-b 1 sets an explicit optimistic target');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'explicit target is active immediately');
    api.setFetchForTest(() => Promise.resolve(jsonResponse({ok: true, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/agent/src', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: false,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/tmp/shell', current_command: 'bash'}],
    }]})));
    await api.scheduleTmuxWindowReadbackForTest('meta-preview', {delayMs: 0, clearActiveIndexOverride: true, expectedIndex: '1', attempt: 5});
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'stale readback does not replace the explicit target');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '0')?.classList.contains('active'), false, 'stale previous active window does not flash active');
    assert.equal(api.activeTmuxSignalWindowForSessionForTest('meta-preview')?.window_index, '1', 'cached tmux signals keep the explicit target active during stale direct-window readback');
  });

  await testAsync('tmux signals keep stale transcript metadata from repainting old active window', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });
    api.applyTmuxSignalsPayloadForTest({windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: false,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: true,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]});

    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});

    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'stale transcript payloads are normalized through tmux-signals before repainting the window bar');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'stale transcript payloads do not revert the terminal title to the old active window');
  });

  await testAsync('direct tmux window clicks do not bounce through stale transcript or partial signal pushes', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const button0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const button1 = tmuxWindowButtonElement('meta-preview', '1', false);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [button0, button1]));
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      if (String(url).startsWith('/api/tmux-window')) return new Promise(() => {});
      if (String(url).startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:claude');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'the direct target is active immediately after click');
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'a stale transcript push cannot repaint the old active tmux window while a direct target is pending');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'the terminal label stays on the direct target while stale transcript data is pending');

    api.applyTmuxSignalsPayloadForTest({windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }]});

    assert.equal(api.activeTmuxSignalWindowForSessionForTest('meta-preview'), null, 'a partial stale signal payload is treated as unconfirmed while the target window is missing');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'partial stale signal pushes keep the direct target button active');
    assert.equal(tmuxWindowButtonFromElement(api.testElementForId('body'), '0')?.classList.contains('active'), false, 'partial stale signal pushes do not flash the old button active');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'partial stale signal pushes do not repaint generated window bars back to the old active window');
  });

  await testAsync('direct tmux window target survives stale in-place button bar refresh', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const button0 = tmuxWindowButtonElement('meta-preview', '0', true);
    const button1 = tmuxWindowButtonElement('meta-preview', '1', false);
    api.testElementForId('body').appendChild(tmuxWindowBarElement('meta-preview', [button0, button1]));
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest(url => {
      if (String(url).startsWith('/api/tmux-window')) return new Promise(() => {});
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:claude');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'direct click marks 1:claude active before the POST settles');

    api.updatePanelWindowStepButtonsForTest('meta-preview', staleInfo);

    assert.deepStrictEqual(activeTmuxWindowIndexesFromElement(api.testElementForId('body')), ['1'], 'stale header refresh cannot replace the button bar with 0:codex active');
  });

  await testAsync('direct tmux window readback only confirms from raw tmux active state', async () => {
    const api = loadYolomux('', ['meta-preview'], 'http:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const staleSignals = {ok: true, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: false,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    const requests = [];
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      requests.push(String(url));
      if (String(url).startsWith('/api/tmux-window')) return Promise.resolve(jsonResponse({ok: true}));
      if (String(url).startsWith('/api/tmux-signals')) return Promise.resolve(jsonResponse(staleSignals));
      if (String(url).startsWith('/api/fs/batch')) {
        const batchRequests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: batchRequests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:claude');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'the direct click still applies the optimistic target immediately');
    for (let i = 0; i < 20; i += 1) await flushAsyncWork();

    assert.ok(requests.filter(url => url.startsWith('/api/tmux-signals')).length > 1, 'stale raw tmux signals keep readback retrying instead of falsely confirming');
    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), '1', 'stale raw tmux signals do not clear the optimistic direct-window target');
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'the held optimistic target keeps stale metadata from repainting the old window after delayed readback');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'the terminal tab label does not bounce back to the old process after stale delayed readback');
  });

  await testAsync('confirmed direct tmux window target ignores delayed stale signal snapshots', async () => {
    const api = loadYolomux('', ['meta-preview'], 'http:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const confirmedSignals = {ok: true, generated_at: Date.now() / 1000, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: false,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: true,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    const oldStaleSignals = {ok: true, generated_at: 1, windows: [{
      session: 'meta-preview',
      window_index: '0',
      active: true,
      panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
    }, {
      session: 'meta-preview',
      window_index: '1',
      active: false,
      panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
    }]};
    const untimestampedStaleSignals = {...oldStaleSignals};
    delete untimestampedStaleSignals.generated_at;
    const requests = [];
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      requests.push(String(url));
      if (String(url).startsWith('/api/tmux-window')) return Promise.resolve(jsonResponse({ok: true}));
      if (String(url).startsWith('/api/tmux-signals')) return Promise.resolve(jsonResponse(confirmedSignals));
      if (String(url).startsWith('/api/fs/batch')) {
        const batchRequests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: batchRequests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:claude');
    for (let i = 0; i < 12; i += 1) await flushAsyncWork();

    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), undefined, 'confirmed direct target can release the short pressed-button override');
    api.applyTmuxSignalsPayloadForTest(untimestampedStaleSignals);
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'an untimestamped delayed tmux signal cannot repaint the previous active window after the override clears');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'an untimestamped delayed tmux signal does not bounce the terminal label back to Codex');
    api.applyTmuxSignalsPayloadForTest(oldStaleSignals);
    await api.applyTranscriptsPayloadForTest({session_order: ['meta-preview'], sessions: {'meta-preview': staleInfo}}, {refreshAuto: false, refreshContext: false, refreshActivity: false});

    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'an older delayed tmux signal cannot repaint the previous active window after the override clears');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'an older delayed tmux signal does not bounce the terminal label back to Codex');
    assert.ok(requests.some(url => url.startsWith('/api/tmux-signals')), 'the test exercised the direct-window signal readback path');
  });

  await testAsync('newer direct tmux window clicks ignore older delayed readbacks', async () => {
    const api = loadYolomux('', ['meta-preview']);
    const staleInfo = {
      agents: [{kind: 'codex', pane_target: 'meta-preview:0.0'}, {kind: 'claude', pane_target: 'meta-preview:1.0'}],
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/codex'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/codex'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'claude', command: 'claude', current_path: '/repo/claude'},
      ],
    };
    const postResolves = [];
    const requests = [];
    api.setTranscriptInfoForTest('meta-preview', staleInfo);
    api.setFetchForTest((url, options = {}) => {
      requests.push(String(url));
      if (String(url).startsWith('/api/tmux-window')) {
        return new Promise(resolve => postResolves.push(() => resolve(jsonResponse({ok: true}))));
      }
      if (String(url).startsWith('/api/tmux-signals')) {
        return Promise.resolve(jsonResponse({ok: true, windows: [{
          session: 'meta-preview',
          window_index: '0',
          active: true,
          panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/codex', current_command: 'codex'}],
        }, {
          session: 'meta-preview',
          window_index: '1',
          active: false,
          panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/repo/claude', current_command: 'claude'}],
        }]}));
      }
      if (String(url).startsWith('/api/fs/batch')) {
        const batchRequests = JSON.parse(options.body || '{}').requests || [];
        return Promise.resolve(jsonResponse({responses: batchRequests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
      }
      if (String(url).startsWith('/api/activity')) return Promise.resolve(jsonResponse({activity: {}}));
      if (String(url).startsWith('/api/session-files')) return Promise.resolve(jsonResponse({session: 'meta-preview', files: [], repos: [], errors: [], loaded: true}));
      return Promise.resolve(jsonResponse({}));
    });

    api.tmuxWindowForTest('meta-preview', {windowIndex: '0'}, 'tmux window 0:codex');
    api.tmuxWindowForTest('meta-preview', {windowIndex: '1'}, 'tmux window 1:claude');
    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), '1', 'latest direct click owns the optimistic target');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'latest direct click shows Claude before readback');

    postResolves[0]();
    for (let i = 0; i < 8; i += 1) await flushAsyncWork();

    assert.equal(api.tmuxWindowActiveIndexOverrideForTest('meta-preview'), '1', 'older POST completion cannot confirm the previous window over the latest click');
    assert.equal(api.terminalTabTitle('meta-preview', api.transcriptInfoForTest('meta-preview')), 'terminal: claude', 'older delayed readback does not bounce the button label back to Codex');
    assert.equal(requests.filter(url => url.startsWith('/api/tmux-signals')).length, 0, 'stale POST completion is ignored before it can start a readback');
  });

  await testAsync('tmux window relative readback lands on backend active window', async () => {
    const api = loadYolomux('', ['meta-preview']);
    api.setTranscriptInfoForTest('meta-preview', {
      selected_pane: {target: 'meta-preview:0.0', window: '0', pane: '0', current_path: '/repo/agent/src'},
      panes: [
        {target: 'meta-preview:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/repo/agent/src'},
        {target: 'meta-preview:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
    });
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: options.method || 'GET'});
      return Promise.resolve(jsonResponse({ok: true, windows: [{
        session: 'meta-preview',
        window_index: '0',
        active: false,
        panes: [{target: 'meta-preview:0.0', pane_id: 'meta-preview:0.0', pane_index: '0', window_index: '0', active: true, current_path: '/repo/agent/src', current_command: 'codex'}],
      }, {
        session: 'meta-preview',
        window_index: '1',
        window_name: 'bash',
        active: true,
        panes: [{target: 'meta-preview:1.0', pane_id: 'meta-preview:1.0', pane_index: '0', window_index: '1', active: true, current_path: '/tmp/shell', current_command: 'bash'}],
      }]}));
    });

    await api.scheduleTmuxWindowReadbackForTest('meta-preview', {delayMs: 0});

    assert.deepStrictEqual(requests.filter(request => request.url.startsWith('/api/tmux-signals')), [{url: '/api/tmux-signals?force=1&session=meta-preview', method: 'GET'}], 'relative window navigation readback uses the session-scoped lightweight tmux signal endpoint once');
    assert.deepStrictEqual(requests.filter(request => request.url.startsWith('/api/transcripts')), [], 'relative window navigation readback does not wait on transcript metadata');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('meta-preview', api.transcriptInfoForTest('meta-preview'))), ['1'], 'relative window navigation lands on the backend window_active value');
    assert.equal(api.transcriptInfoForTest('meta-preview').selected_pane.current_path, '/tmp/shell', 'relative window navigation updates the selected pane path from tmux signals');
  });

  test('t@6424', () => {
    loadYolomux();
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const start = source.indexOf('function showAttentionAlert(');
    const end = source.indexOf('function dismissAttentionAlertsForSession(', start);
    assert.ok(start > 0 && end > start, 'could not locate showAttentionAlert body');
    const body = source.slice(start, end);
    assert.ok(body.includes('container: displayToastContainer(session)'), 'attention notifications use the target pane-local toast stack');
    assert.equal(body.includes('container: attentionAlerts'), false, 'attention notifications do not use the global fixed stack');
    assert.ok(source.includes('function compactNotificationTitle('), 'notification/toast titles use one compact title helper');
    assert.ok(body.includes('sessionNotificationTitle(session, state)'), 'attention toasts use the compact session notification title');
    assert.equal(source.includes('YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}'), false, 'attention notifications drop verbose host-prefixed titles');
    assert.equal(source.includes('YOLOmux - ${serverHostname}: ${message}'), false, 'watched-PR browser notifications drop verbose host-prefixed titles');
    assert.ok(source.includes("compactNotificationTitle(sessionLabel(session), 'terminal')"), 'terminal connection toasts use the compact session title');
    assert.ok(source.includes("localizedHtml('terminal.connection.reconnectingStatus'"), 'terminal reconnect status is i18n-keyed');
    assert.ok(source.includes("t('terminal.connection.reconnectingToast'"), 'terminal reconnect toast is i18n-keyed');
    assert.ok(source.includes("terminalNotConnectedHtml(session)"), 'terminal-not-connected statuses share the localized helper');
    assert.ok(source.includes("t('terminal.connection.connShort'"), 'terminal socket status text is i18n-keyed');
    assert.ok(source.includes("t('terminal.connection.socketsTitle'"), 'terminal socket status title is i18n-keyed');
    assert.ok(source.includes("t('terminal.summary.streamDisconnected')"), 'summary stream disconnect text is i18n-keyed');
    assert.equal(source.includes('Disconnected. Reconnecting in ${'), false, 'terminal reconnect toast does not leak a hardcoded English literal');
    assert.equal(source.includes('terminal is not connected</span>'), false, 'terminal-not-connected status does not leak a hardcoded English literal');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['terminal.connection.reconnectingToast'], 'Disconnected. Reconnecting in {seconds}s.', 'terminal reconnect toast has a source locale key');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['notify.testTitle'], 'YOLOmux[{host}] notifications enabled', 'test notification title uses compact host bracket format');
    const attentionCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.panel-toast-stack\s*\{[\s\S]*top:\s*8px[\s\S]*z-index:\s*var\(--z-full-screen-overlay\)/.test(attentionCss), 'pane-local toast stacks render below each pane tab strip and above pane contents');
  });

  test('t@6452', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('async function apiFetchJson('), 'D1: shared JSON fetch helper is bundled');
    assert.ok(source.includes('error.status = response.status'), 'D1: JSON fetch errors preserve HTTP status for callers');
    assert.ok(source.includes('error.payload = payload || {}'), 'D1: JSON fetch errors preserve API payloads for callers');
    const jsonFetchFiles = [
      'static_src/js/yolomux/40_file_explorer_files.js',
      'static_src/js/yolomux/70_layout_actions.js',
      'static_src/js/yolomux/78_panel_shell.js',
      'static_src/js/yolomux/80_info_panel.js',
      'static_src/js/yolomux/81_yoagent_panel.js',
      'static_src/js/yolomux/82_preferences_panel.js',
      'static_src/js/yolomux/83_debug_panel.js',
      'static_src/js/yolomux/99_terminal_boot.js',
    ];
    for (const file of jsonFetchFiles) {
      const src = fs.readFileSync(file, 'utf8');
      assert.equal(/const response = await apiFetch\(/.test(src), false, `D1: ${file} should not hand-roll apiFetch response variables`);
      assert.equal(/await response\.json\(\)/.test(src), false, `D1: ${file} should use apiFetchJson instead of manual response.json`);
      assert.equal(/if \(!response\.ok\)/.test(src), false, `D1: ${file} should use apiFetchJson instead of manual response.ok checks`);
    }
    assert.ok((fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8')).includes('apiFetch(`/api/fs/unindex'), 'D1: Finder unindex remains fire-and-forget');
    assert.ok(fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8').includes("apiFetch('/api/event'"), 'D1: event telemetry remains fire-and-forget');
  });

  test('t@6473', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const sourceBlock = (startNeedle, endNeedle = '') => {
      const start = source.indexOf(startNeedle);
      assert.ok(start >= 0, `F1: source block starts with ${startNeedle}`);
      if (!endNeedle) return source.slice(start);
      const end = source.indexOf(endNeedle, start + startNeedle.length);
      assert.ok(end > start, `F1: source block ends with ${endNeedle}`);
      return source.slice(start, end);
    };
    assert.ok(source.includes('const fileState = new Map();'), 'F1: one fileState map owns per-path file/editor state');
    assert.ok(source.includes('const openFiles = fileState;'), 'F1: openFiles is the compatibility alias for fileState');
    for (const obsolete of [
      'const fileEditorTabPaths = new Set()',
      'const filePreviewTabPaths = new Set()',
      'const openFileOwnerSessions = new Map()',
      'const fileEditorViewMode = new Map()',
      'const fileEditorImageMode = new Map()',
      'const editorBlameByPath = new Map()',
      'const fileEditorConflictDialogs = new Set()',
    ]) {
      assert.equal(source.includes(obsolete), false, `F1: removed obsolete path-keyed container ${obsolete}`);
    }
    const setFileStateBlock = sourceBlock('function setFileState(path, state)', 'function deleteFileState(path)');
    assert.ok(/editorTabItems[\s\S]*ownerSessions[\s\S]*viewMode[\s\S]*previewZoom[\s\S]*blame[\s\S]*conflictDialogOpen/.test(setFileStateBlock), 'F1: replacing file content preserves per-path side state on the fileState record');
    const removeOpenFileBlock = sourceBlock('async function removeOpenFile(path, options = {})', 'function closeFileTab(path, options = {})');
    assert.ok(removeOpenFileBlock.includes('deleteFileState(path)'), 'F1: closing the last owner deletes one fileState record');
    const renameOpenFilePathBlock = sourceBlock('function renameOpenFilePath(oldPath, newPath)');
    assert.ok(renameOpenFilePathBlock.includes('deleteFileState(oldPath)') && renameOpenFilePathBlock.includes('setFileState(newPath, state)'), 'F1: rename moves one fileState record');
  });

  test('t@6493', () => {
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.actions button,\s*\.info-refresh,\s*\.info-sort-button,\s*\.changes-repo-head,[\s\S]*\.file-editor-toolbar button,[\s\S]*display:\s*inline-flex;[\s\S]*align-items:\s*center;[\s\S]*border:\s*0;[\s\S]*background:\s*transparent;[\s\S]*cursor:\s*pointer;/.test(css), 'I1: common button reset/flex base is centralized');
    assert.equal(/\.actions button\s*\{[^}]*display:\s*inline-flex/.test(css), false, 'I1: topbar actions do not restate the shared inline-flex base');
    assert.equal(/\.info-refresh\s*\{[^}]*cursor:\s*pointer/.test(css), false, 'I1: info refresh does not restate shared cursor behavior');
    assert.equal(/\.file-editor-mode-control button\s*\{[^}]*background:\s*transparent/.test(css), false, 'I1: editor mode buttons do not restate shared transparent background');
  });

  test('t@6501', () => {
    const api = loadYolomux();
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    api.setDocumentTitleNowForTest(0);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]');
    api.setDocumentTitleNowForTest(119000);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]', 'idle title stays compact before two minutes');
    api.setDocumentTitleNowForTest(121000);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux (idle for 2 min)', 'idle title shows elapsed minutes after two minutes');
    api.setDocumentTitleNowForTest(181000);
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux (idle for 3 min)', 'idle title minute count advances while idle');
    api.setAutoApproveStateForTest('1', {screen: {key: 'working'}});
    api.setAutoApproveStateForTest('2', {screen: {key: 'idle'}, agent_windows: [{kind: 'claude', state: 'working', window_index: 2, window_label: '2:claude'}]});
    api.setAutoApproveStateForTest('3', {screen: {key: 'idle'}});
    api.updateDocumentTitle();
    assert.equal(api.runningAgentCount(), 2);
    assert.equal(api.documentTitleForTest(), 'YOLOmux [2 running]');
    assert.equal(api.sessionYoloIsWorking('2'), true, 'a hidden/background working window makes the session YO spin');
    api.setAutoApproveStateForTest('1', {screen: {key: 'idle'}});
    api.setAutoApproveStateForTest('2', {screen: {key: 'idle'}});
    api.updateDocumentTitle();
    assert.equal(api.documentTitleForTest(), 'YOLOmux [idle]', 'idle timer resets after a running period');
    assert.ok(/function updateDocumentTitle\(\)[\s\S]*updateBrowserFavicon\(\)/.test(source), 'document title refresh also refreshes the browser favicon badge');
  });

  test('t@6527', () => {
    const api = loadYolomux();
    const info = {
      project: {
        git: {
          branch: 'main',
          root: '/home/test/project',
          upstream: 'origin/main',
          dirty_count: 10,
          behind: 18,
          head: '747c3fd0c6 ci: Update the dep for the whl publish to be automated (#9961)',
          github_repo: {url: 'https://github.com/ai-project/project'},
          other_branches: {
            branches: [
              {
                name: 'main',
                current: true,
                subject: 'ci: Update the dep for the whl publish to be automated (#9961)',
                updated: '13 hours ago',
              },
              {
                name: 'keivenc/DIS-2141__internlm-tool-parser-parity',
                subject: 'feat: add InternLM tool parser parity',
                pull_request: {
                  number: 10075,
                  status_label: 'PASSING',
                  url: 'https://github.com/ai-project/project/pull/10075',
                },
                linear_ids: ['DIS-2141'],
                updated: '3 days ago',
              },
            ],
          },
        },
        pull_request: null,
      },
      selected_pane: {current_path: '/home/test/project'},
    };
    const html = api.tmuxPaneTabHtml('4', info, null, true);
    const tabBadgeSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(tabBadgeSource.includes('function pullRequestNumberIndicatorHtml'), 'tab renders the PR number chip helper');
    assert.ok(/<span class="ci-indicator tab-symbol pr-number-chip pr-status-merged"[^>]*>#9961<\/span>/.test(html), 'merged default-branch tab renders the #number as a purple chip');
    assert.ok(html.includes('>YO<'), 'tab includes YO marker');
    assert.equal(/session-yolo-marker[^"]*tab-symbol/.test(html), false, 'YO marker stays visible when metadata badges are hidden');
    assert.ok(html.includes('>4<'), 'tab includes session number');
    assert.ok(html.includes('>MAIN<'), 'tab marks default branch');
    assertNoStandalonePrBadge(html, 'merged default-branch tab');
    // #42: a source-inferred PR with no explicit status_label still reports no status (we don't trust a
    // raw merged flag on an inferred PR)...
    assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, merged: true}), '');
    // ...but an explicit status_label is honored even when source_only, so the default-branch head merge
    // commit (which is, by definition, merged) reports MERGED.
    assert.equal(api.pullRequestStatusLabel({number: 9961, source_only: true, status_label: 'merged'}), 'merged');
    assert.equal(html.includes('MERGED'), false, '#42: a default-branch HEAD merge commit (#9961) consolidates merged state into the purple #number chip');
    assert.ok(html.includes('pr-status-merged'), '#42: the inferred merged PR uses the merged status color on the #number chip');
    assert.equal(html.includes('(#9961)'), false, 'tab title strips duplicated PR suffix');
    api.setActivitySummaryPayloadForTest({
      sessions: {
        '4': {
          local: 'Claude session 4 is idle in project. It currently has 10 files changed. Status check: 10 dirty files; 18 commits behind.',
        },
      },
    });
    const popover = api.sessionPopoverHtml('4', info, 'claude', true);
    assert.ok(/popover-title">tmux session 4 ·/.test(popover), 'session popover title labels the header as a tmux session');
    assert.ok(/popover-subtitle[\s\S]*branch-indicator[^>]*>MAIN<[\s\S]*pr-number-chip pr-status-merged[^>]*>#9961<[\s\S]*ci: Update the dep/.test(popover), 'merged PR popover header mirrors the tab convention: MAIN chip, purple #number chip, then title');
    assert.equal(popover.includes('#9961:'), false, 'merged PR popover header omits the old #number text prefix');
    assert.ok(/popover-label">branch<\/div><div class="popover-value"><span class="ci-indicator tab-symbol branch-indicator[^"]*">MAIN<\/span>/.test(popover), 'merged PR popover branch row uses the same MAIN chip as the tab');
    assert.ok(/popover-label">PR<\/div><div class="popover-value"><span class="ci-indicator tab-symbol pr-number-chip pr-status-merged[^"]*">#9961<\/span>/.test(popover), 'merged PR popover PR row uses the same purple #number chip as the tab');
    assert.equal(popover.includes('#9961 MERGED'), false, 'merged PR popover omits redundant MERGED text');
    assert.equal(popover.includes('PR #9961'), false, 'merged PR popover avoids repeating PR before the #number value');
    assert.equal(popover.includes('popover-label">desc'), false, 'merged PR popover omits the desc row because the header already carries the PR title');
    assert.equal(popover.includes('Status check:'), false, 'merged PR popover removes the YO!agent status sentence when the dedicated git row is present');
    assert.ok(popover.includes('10 dirty · 18 behind'), 'merged PR popover keeps git facts in the dedicated git row');
    const branchListIndex = popover.indexOf('<div class="branch-list"');
    const detailLabels = [...popover.slice(0, branchListIndex).matchAll(/popover-label">([^<]+)/g)].map(match => match[1]);
    assert.equal(detailLabels[detailLabels.length - 1], 'git', 'merged PR popover makes git the final detail row');
    const headRow = popover.match(/popover-label">HEAD<\/div><div class="popover-value">([^<]+)<\/div><\/div>/)?.[1] || '';
    assert.equal(headRow, '747c3fd0c6', 'merged PR popover HEAD row shows only the SHA, not the repeated subject and PR suffix');
    const branchList = popover.slice(branchListIndex);
    assert.equal(branchList.includes('info-branch-current'), false, 'popover branch list drops the redundant current label');
    assert.ok(/branch-name"><span class="ci-indicator tab-symbol branch-indicator[^"]*">MAIN<\/span>[\s\S]*branch-meta">[\s\S]*pr-number-chip pr-status-merged[^>]*>#9961<\/span>/.test(branchList), 'popover branch list mirrors MAIN and merged PR chips for the current branch');
    assert.equal(branchList.includes('<div class="branch-subject">ci: Update the dep'), false, 'popover branch list suppresses the current branch subject when it duplicates the header title');
    assert.ok(/popover-chip-link[\s\S]*pr-number-chip[^>]*>#10075<\/span>[\s\S]*meta-pr-status pr-status-passing[^>]*>PASSING/.test(branchList), 'popover branch list shows non-current PR numbers as chips while keeping meaningful status text');
    assert.ok(branchList.includes('<div class="branch-subject">feat: add InternLM tool parser parity</div>'), 'popover branch list keeps non-current branch subjects because they add detail');

    const blockedHtml = api.tmuxPaneTabHtml('4', info, {key: 'blocked', short: 'BLK', label: 'Blocked', reason: 'blocked command'}, false);
    assert.ok(blockedHtml.includes('--attention-animation-delay:'), 'red attention badges carry a synchronized animation delay');

    const genericWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'working'}, true);
    assert.equal(/session-yolo-marker[^"]*active[^"]*working/.test(genericWorkingHtml), false, 'generic working state does not pulse YO marker');

    api.setAutoApproveStateForTest('4', {enabled: true, screen: {key: 'working'}});
    const workingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
    assert.ok(/session-yolo-marker[^"]*active[^"]*working/.test(workingHtml), 'visible screen working pulses active YO marker');

    api.setAutoApproveStateForTest('4', {enabled: false, screen: {key: 'working'}});
    const autoOffWorkingHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
    assert.ok(/session-yolo-marker[^"]*\bworking\b/.test(autoOffWorkingHtml), 'a working agent spins its YO ball even when auto-approve is off');
    assert.equal(/session-yolo-marker[^"]*\bactive\b/.test(autoOffWorkingHtml), false, 'an auto-off working marker is not rendered as active');
    const yoloMarkerCss = fs.readFileSync('static/yolomux.css', 'utf8');
    // the YO ball spins ONLY when .working, and at the slow rotation setting (not a fast
    // hardcoded value); there is NO ambient idle-rotation rule, so an idle marker is static.
    assert.ok(/\.session-yolo-marker\.working\s*\{[^}]*--yolo-rotation-duration/.test(yoloMarkerCss), '#23: working YO spin is driven by the slow yolo_rotate_ms setting');
    assert.equal(/\.session-yolo-marker\.working\s*\{[^}]*--yolo-working-duration/.test(yoloMarkerCss), false, '#23: the fast hardcoded working duration is gone');
    assert.equal(yoloMarkerCss.includes('--yolo-working-duration'), false, '#23: the dead --yolo-working-duration token is removed');
    assert.equal(/\.session-yolo-marker:not\(\.inactive\):not\(\.locked\):not\(\.working\)/.test(yoloMarkerCss), false, '#23: the ambient idle-rotation rule is deleted (idle markers are static)');

    api.setAutoApproveStateForTest('4', {enabled: false, enabled_elsewhere: true, locked: true, lock_owner: {pid: 1234}, screen: {key: 'working'}});
    const externalHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, false);
    assert.ok(/session-yolo-marker[^"]*locked/.test(externalHtml), 'YO owned by another server renders as yellow locked marker');
    assert.equal(/session-yolo-marker[^"]*active/.test(externalHtml), false, 'external YO is not shown as local active YO');
    assert.ok(externalHtml.includes('YOLO on elsewhere'), 'external YO marker title explains ownership is elsewhere');

    api.applyServerMetadataPulsesForTest('4', {main: 20000, pr: 20000});
    const metadataPulseHtml = api.tmuxPaneTabHtml('4', info, {key: 'idle'}, true);
    assert.ok(metadataPulseHtml.includes('branch-indicator metadata-pulse'), 'MAIN badge pulses after metadata change');
    assert.equal(metadataPulseHtml.includes('pr-number-chip metadata-pulse'), false, 'open PR number chip is not pulsed by PR metadata changes');

    const mergedInfo = {
      project: {
        git: {branch: 'feature'},
        pull_request: {number: 12, merged: true, checks: {state: 'success'}},
      },
    };
    api.applyServerMetadataPulsesForTest('8', {status: 20000});
    const mergedPulseHtml = api.tmuxPaneTabHtml('8', mergedInfo, {key: 'idle'}, true);
    assert.ok(mergedPulseHtml.includes('pr-number-chip pr-status-merged metadata-pulse'), 'merged #number chip pulses after status change');

    [
      {session: '9', number: 13, state: 'failure', statusLabel: 'CI failing', statusClass: 'pr-status-failing', pulse: true, label: 'failing open PR'},
      {session: '10', number: 14, state: 'passing', statusLabel: 'open', statusClass: 'pr-status-passing', pulse: true, label: 'passing open PR'},
      {session: '11', number: 15, state: 'pending', statusLabel: 'open', statusClass: 'pr-status-pending', pulse: false, label: 'pending open PR'},
      {session: '12', number: 16, state: 'unknown', statusLabel: 'open', statusClass: '', pulse: false, label: 'unknown open PR'},
    ].forEach(({session, number, state, statusLabel, statusClass, pulse, label}) => {
      if (pulse) api.applyServerMetadataPulsesForTest(session, {ci: 20000});
      const ciHtml = api.tmuxPaneTabHtml(session, {
        project: {
          git: {branch: 'feature'},
          pull_request: {number, status_label: statusLabel, checks: {state}},
        },
      }, {key: 'idle'}, true);
      assertNoStandalonePrBadge(ciHtml, label);
      if (statusClass) assert.ok(ciHtml.includes(statusClass), `${label} renders ${statusClass}`);
      if (pulse) assert.ok(ciHtml.includes('metadata-pulse'), `${label} CI badge is marked after CI change`);
      if (state !== 'unknown') assertSingleCiBadge(ciHtml, label);
    });
    api.setAutoApproveStateForTest('4', {agent_windows: [
      {kind: 'claude', state: 'working', window_index: 0, window_label: '0:claude'},
      {kind: 'codex', state: 'needs-input', window_index: 1, window_label: '1:codex'},
    ]});
    assert.equal(api.sessionState('4', {agents: [{kind: 'claude'}, {kind: 'codex'}], panes: []}).key, 'needs-input', 'a background agent window needing input propagates ASK? to the session tab');
    const agentPopover = api.sessionPopoverHtml('4', {panes: []}, 'claude', false);
    assert.ok(/class="[^"]*status-indicator[^"]*session-agent-dot[^"]*status-indicator--dot[^"]*status-indicator--working/.test(agentPopover), 'working popover dot inherits the shared dot/working status indicator classes');
    assert.ok(/class="[^"]*status-indicator[^"]*session-agent-dot[^"]*status-indicator--dot[^"]*status-indicator--attention[^"]*attention-pulse/.test(agentPopover), 'ASK? popover dot inherits the shared attention pulse classes');
    assert.ok(/class="[^"]*session-agent-status[^"]*status-indicator--label[^"]*agent-status-attention[^"]*status-indicator--attention[^"]*attention-pulse[^"]*" style="--attention-animation-delay:/.test(agentPopover), 'ASK? popover status text inherits the shared red attention pulse and phase');
    assert.ok(agentPopover.includes('ASK? &lt;15 sec ago'), 'ASK? popover status text shows recency instead of approval/needs-input subtype words');
    assert.equal(agentPopover.includes('ASK? needs input') || agentPopover.includes('ASK? approval'), false, 'ASK? popover status text drops subtype words');
    assert.ok(agentPopover.includes('tmux window 0:claude'), 'working agent row labels the tmux window explicitly');
    assert.ok(agentPopover.includes('tmux window 1:codex'), 'ASK? agent row labels the tmux window explicitly');
    assert.equal(agentPopover.includes('tmux window tmux window'), false, 'agent row does not double-label tmux window');
    const localeFiles = fs.readdirSync('static_src/locales').filter(name => name.endsWith('.json'));
    for (const file of localeFiles) {
      const catalog = JSON.parse(fs.readFileSync(`static_src/locales/${file}`, 'utf8'));
      assert.ok(catalog['popover.tmuxSession']?.includes('{label}'), `${file} localizes popover.tmuxSession and preserves {label}`);
      assert.ok(catalog['popover.tmuxWindow']?.includes('{label}'), `${file} localizes popover.tmuxWindow and preserves {label}`);
      assert.ok(catalog['popover.sessionId'], `${file} localizes popover.sessionId`);
    }
  });

  test('t@6675', () => {
    const api = loadYolomux('?debug=1', ['1', '2']);
    assert.equal(api.debugModeEnabledForTest(), true, 'debug=1 enables the JS Debug pane');
    assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,files,search-history,preferences,debug,image-viewer,file-editor');
    assert.equal(api.resolveLayoutItem('debug'), api.debugPaneItemId, 'debug URL item resolves to the virtual pane when enabled');
    assert.equal(api.itemParam(api.debugPaneItemId), 'debug', 'Debug pane serializes to the readable debug item');
    const fileMenu = api.appMenuTree().find(menu => menu.id === 'file');
    assert.ok(fileMenu.items.some(item => item.targetItem === api.debugPaneItemId), 'File menu exposes JS Debug only when enabled');
    const paletteRows = api.commandPaletteCommandItems().filter(item => item.targetItem === api.debugPaneItemId);
    assert.equal(paletteRows.length, 1, 'command palette lists the Debug pane once through the Tabs group');
    api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/ping', status: 200, ok: true, durationMs: 12.3});
    api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/activity-summary?locale=en', status: 200, ok: true, durationMs: 4200.4});
    api.recordJsDebugEventForTest('sse', {
      eventType: 'fs_changed',
      trigger: 'watch',
      cache: 'ready',
      computeMs: 22.4,
      receiveLatencyMs: 3.2,
      frameBytes: 999,
      bytes: 900,
      changeSummary: {
        roots_changed: 1,
        entries_added: 2,
        entries_removed: 1,
        entries_modified: 3,
        files_added: 1,
        files_removed: 0,
        files_modified: 2,
        dirs_added: 1,
        dirs_removed: 1,
        dirs_modified: 0,
      },
      listingSummary: {
        roots_listed: 2,
        roots_error: 0,
        entries_listed: 44,
      },
    });
    api.recordJsDebugEventForTest('error', {message: 'boom', source: '/static/yolomux.js', line: 10});
    assert.equal(api.jsDebugEventsForTest().length, 4, 'debug event recorder stores bounded diagnostics while enabled');
    const html = api.debugPanelHtmlForTest();
    assert.ok(html.includes('data-js-debug-log'), 'debug panel renders one copyable text log');
    assert.ok(html.includes('GET /api/ping'), 'debug panel renders API timing rows');
    assert.ok(html.includes('Slow API by max latency') && html.includes('GET /api/activity-summary'), 'debug panel summarizes slow API endpoints by path');
    assert.ok(html.includes('Slow SSE server work') && html.includes('Slow SSE receive latency'), 'debug panel summarizes SSE server time and receive latency');
    assert.ok(html.includes('fs=changed=roots:1 +2 -1 ~3 files=+1 ~2 dirs=+1 -1 listed=44/2'), 'debug panel renders fs_changed change counts');
    assert.ok(html.includes('SSE') && html.includes('3.2ms') && html.includes('rx=999B'), 'debug panel renders SSE receive time in the duration column and frame size');
    assert.equal(html.includes('lat=3.2ms'), false, 'debug panel does not use a separate lat= token for SSE rows');
    assert.ok(html.includes('boom'), 'debug panel renders JS error rows');
    const debugPaneSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const debugPaneCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(!/panel\.className = 'panel preferences-panel js-debug-panel'/.test(debugPaneSource), 'Debug panel does not use the Preferences class; Preferences rerenders must not overwrite it');
    assert.ok(/\.preferences-panel,\s*\.js-debug-panel\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\)/.test(debugPaneCss), 'Debug panel gets the shared panel grid without being a Preferences panel');
    assert.equal(debugPaneSource.includes("initialSetting('performance.activity_summary_refresh_ms'"), false, 'silent activity-summary polling preference is removed');
    assert.equal(debugPaneSource.includes('activitySummaryBackgroundRefreshMs'), false, 'activity-summary no longer keeps a client background refresh timer');
    assert.ok(debugPaneSource.includes('function activitySummaryIsVisible()'), 'activity-summary visibility tracking remains available for server watch state');
    const debugText = api.jsDebugTextForClipboardForTest();
    assert.ok(debugText.includes('page=/?debug=1'), 'debug text includes the active URL path and query');
    assert.ok(debugText.includes('API') && debugText.includes('GET /api/ping'), 'debug text exports API rows');
    assert.ok(debugText.includes('Slow API by max latency') && debugText.includes('GET /api/activity-summary'), 'debug text exports grouped slow API rows');
    assert.ok(debugText.includes('sse_rx=999B'), 'debug text counts estimated SSE frame bytes');
    assert.ok(debugText.includes('Slow SSE receive latency') && debugText.includes('fs_changed'), 'debug text exports grouped SSE latency rows');
    assert.ok(debugText.includes('fs=changed=roots:1 +2 -1 ~3 files=+1 ~2 dirs=+1 -1 listed=44/2'), 'debug text exports fs_changed change counts');
    assert.ok(debugText.includes('Error') && debugText.includes('boom'), 'debug text exports JS error rows');
    assert.equal(debugText.includes('"events"'), false, 'debug copy payload is compact text, not JSON');
    const url = api.syncInitialLayoutUrlForTest();
    assert.equal(parseUrl(url).get('debug'), '1', 'layout URL updates preserve debug=1');
    const openedApi = loadYolomux('?debug=1&sessions=debug', ['1']);
    assert.deepStrictEqual(canonical(openedApi.serialize(openedApi.currentSlots()).panes), {
      left: {tabs: [openedApi.debugPaneItemId], active: openedApi.debugPaneItemId},
      slot1: {tabs: [openedApi.fileExplorerItemId], active: openedApi.fileExplorerItemId},
    }, 'debug=1 allows sessions=debug to open the Debug pane directly');
    const injectedApi = loadYolomux('?sessions=files,6,5&layout=row@22(slot2,row@50(left,slot1))&tabs=slot2:files;left:6;slot1:5,info&debug=1', ['5', '6']);
    assert.deepStrictEqual(canonical(injectedApi.serialize(injectedApi.currentSlots()).panes), {
      left: {tabs: ['6'], active: '6'},
      slot1: {tabs: ['5', injectedApi.infoItemId, injectedApi.debugPaneItemId], active: injectedApi.debugPaneItemId},
      slot2: {tabs: [injectedApi.fileExplorerItemId], active: injectedApi.fileExplorerItemId},
    }, 'debug=1 injects and activates Debug in an existing URL layout');
  });

  test('session popover lists agent windows with working durations and idle recency', () => {
    const api = loadYolomux('', ['4', '5', '6']);
    const baseInfo = {selected_pane: {current_path: '/repo'}, project: {git: {root: '/repo'}}};
    const noAgentHtml = api.sessionPopoverHtml('4', {...baseInfo, agents: []}, '', false);
    assert.ok(noAgentHtml.includes('no AI agents in this tab'), '0-agent tabs render a clear empty line');

    const now = Date.now() / 1000;
    const multiInfo = {
      ...baseInfo,
      agents: [{kind: 'claude', pane_target: '%10'}, {kind: 'codex', pane_target: '%11'}],
    };
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'codex', state: 'idle', idle_since: now - 300, last_active_ts: now - 300, window_index: 1, window_name: 'codex', window_label: '1:codex'},
        {kind: 'claude', state: 'working', working_elapsed_seconds: 158, window_index: 0, window_name: 'claude', window_label: '0:claude'},
      ],
    });
    const multiText = api.sessionPopoverHtml('5', multiInfo, 'claude', false).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    const workingIndex = multiText.indexOf('0:claude — working for 2m 38s');
    const idleIndex = multiText.indexOf('1:codex — 5 min ago');
    assert.ok(workingIndex >= 0, 'working row uses the live status-counter elapsed');
    assert.ok(idleIndex > workingIndex, 'working agents render before idle agents and idle agents use recency text');

    api.setAutoApproveStateForTest('4', {
      agent_windows: [{kind: 'codex', state: 'idle', last_active_ts: now - 5, window_index: 0, window_name: 'codex', window_label: '0:codex'}],
    });
    const recentIdleText = api.sessionPopoverHtml('4', {...baseInfo, agents: [{kind: 'codex'}]}, 'codex', false).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(recentIdleText.includes('0:codex — &lt;15 sec ago') || recentIdleText.includes('0:codex — <15 sec ago'), 'sub-15-second idle agents use the shared Ago recency label');

    api.setAutoApproveStateForTest('4', {
      agent_windows: [{kind: 'codex', state: 'idle', idle_since: now - 900, last_active_ts: now - 900, window_index: 1, window_name: 'codex', window_label: '1:codex'}],
    });
    const currentIdleHtml = api.sessionPopoverHtml('4', {selected_pane: {current_path: '/repo', window_index: '1'}, project: {git: {root: '/repo'}}, agents: [{kind: 'codex'}]}, 'codex', false);
    const currentIdleText = currentIdleHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(currentIdleText.includes('1:codex — active'), 'focused/current agent window display state is active, not transcript-idle');
    assert.equal(currentIdleText.includes('idle 15m'), false, 'focused/current agent window is not falsely labeled idle from transcript recency');
    assert.ok(/session-agent-row[^"]*state-idle[^"]*current/.test(currentIdleHtml), 'focused/current agent window row carries the current class for header styling');
    assert.ok(/agent-status-active[^"]*status-indicator--active/.test(currentIdleHtml), 'focused/current agent window label uses the shared active/max-contrast status class');

    const parityInfo = {
      selected_pane: {target: '5:0.0', window: '0', pane: '0', current_path: '/repo/codex-root/src'},
      project: {
        git: {root: '/repo/session-root', branch: 'session-branch'},
        repos: [
          {root: '/repo/codex-root', cwd: '/repo/codex-root/src', branch: 'codex-branch', dirty_count: 4, ahead: 1, primary: true},
          {root: '/repo/claude-root', cwd: '/repo/claude-root/src/deep', branch: 'claude-branch', dirty_count: 0, ahead: 3},
        ],
      },
      agents: [{kind: 'codex', pane_target: '5:0.0'}, {kind: 'claude', pane_target: '5:1.0'}],
      panes: [
        {target: '5:0.0', window: '0', pane: '0', window_active: false, active: true, process_label: 'codex', process_label_pid: 111, command: 'codex', current_path: '/repo/codex-root/src'},
        {target: '5:1.0', window: '1', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 222, command: 'claude', current_path: '/repo/claude-root/src/deep'},
      ],
      window_metadata: [
        {window: '0', window_index: 0, path: '/repo/codex-root/src', git: {root: '/repo/codex-root', branch: 'codex-branch', dirty_count: 4, ahead: 1}},
        {window: '1', window_index: 1, path: '/repo/claude-root/src/deep', git: {root: '/repo/claude-root', branch: 'claude-branch', dirty_count: 0, ahead: 3}},
      ],
    };
    api.setTranscriptInfoForTest('5', parityInfo);
    api.setFocusedPanelItem('5');
    api.setFileExplorerModeForTest('tabber');
    api.setTabberSessionFilesForTest('5', [
      {path: 'codex.py', abs_path: '/repo/codex-root/src/codex.py', repo: '/repo/codex-root', status: 'M', mtime: 200, agents: ['codex'], agent_windows: [{kind: 'codex', window: '0', window_index: 0, pane: '0', pane_target: '5:0.0'}]},
      {path: 'claude.py', abs_path: '/repo/claude-root/src/deep/claude.py', repo: '/repo/claude-root', status: 'M', mtime: 300, agents: ['claude'], agent_windows: [{kind: 'claude', window: '1', window_index: 1, pane: '0', pane_target: '5:1.0'}]},
    ]);
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'codex', state: 'working', working_elapsed_seconds: 65, window_index: 0, window_label: '0:codex'},
        {kind: 'claude', state: 'idle', idle_since: now - 3600, last_active_ts: now - 3600, window_index: 1, window_label: '1:claude'},
      ],
    });
    const parityPopoverHtml = api.sessionPopoverHtml('5', parityInfo, 'claude', false);
    const parityPopoverText = parityPopoverHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(parityPopoverText.includes('1:claude (pid=222) — active'), 'popover marks tmux window_active=1 as active even when selected_pane still points at window 0');
    assert.ok(parityPopoverText.includes('0:codex (pid=111) — working for 1m 5s'), 'non-focused window keeps its own working state');
    assert.equal((parityPopoverHtml.match(/session-agent-row[^"]*current/g) || []).length, 1, 'popover marks exactly one agent window current');
    assert.deepStrictEqual(activeTmuxWindowIndexesFromHtml(api.tmuxWindowBarHtml('5', parityInfo)), ['1'], 'window bar marks the tmux window_active window');
    const parityRows = api.tabberRenderedRowsForTest();
    const parityClaudeRow = parityRows.find(row => row.type === 'window' && /^1:claude/.test(row.name));
    const parityCodexRow = parityRows.find(row => row.type === 'window' && /^0:codex/.test(row.name));
    assert.equal(parityClaudeRow?.classes.includes('tabber-active-window'), true, 'Tabber marks the same active tmux window as the popover and window bar');
    assert.equal(parityClaudeRow?.date, 'active', 'Tabber active window displays active instead of stale transcript recency');
    assert.ok((parityCodexRow?.nameHtml || '').includes('agent-window-activity-icon--working'), 'Tabber working dot uses the same working state as the popover');
    const parityTree = api.buildTabberTree();
    const paritySession = parityTree.entries.find(entry => entry.tabber?.session === '5');
    const parityWindows = parityTree.entriesByDir.get('/' + paritySession.name);
    const parityClaudeWindow = parityWindows.find(row => row.tabber.windowIndex === 1);
    const parityClaudeRepos = parityTree.entriesByDir.get('/' + paritySession.name + '/' + parityClaudeWindow.name).map(row => row.tabber.label);
    assert.deepEqual(parityClaudeRepos, ['/repo/claude-root'], 'Tabber and popover share the touched repo root for the active Claude window');
    const parityMetaHtml = api.projectMetaHtml('5', parityInfo);
    assert.ok(parityMetaHtml.includes('/repo/claude-root'), 'Info Line uses the active AI window touched repo root, not the raw pane cwd subdir');
    assert.ok(parityMetaHtml.includes('claude-branch') && parityMetaHtml.includes('0 dirty') && parityMetaHtml.includes('3 ahead'), 'Info Line git summary matches the active window metadata');
    assert.equal(parityMetaHtml.includes('/repo/claude-root/src/deep'), false, 'Info Line does not show the active AI window raw cwd subdir when touched repo metadata exists');
    assert.equal(parityMetaHtml.includes('codex-branch') || parityMetaHtml.includes('4 dirty'), false, 'Info Line does not leak selected-pane git state for the inactive Codex window');

    api.setAutoApproveStateForTest('6', {
      agent_windows: [{kind: 'codex', state: 'working', working_elapsed_seconds: 3720, window_index: 0, window_name: 'codex', window_label: '0:codex'}],
    });
    const singleText = api.sessionPopoverHtml('6', {...baseInfo, agents: [{kind: 'codex'}]}, 'codex', false).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(singleText.includes('0:codex — working for 1h 02m'), '1-agent tabs use the shared compact elapsed formatter with the window label');

    const perWindowInfo = {
      selected_pane: {current_path: '/repo/selected-session-path'},
      project: {git: {root: '/repo/session-root', branch: 'session-branch'}},
      agents: [{kind: 'claude'}, {kind: 'codex'}],
      panes: [
        {window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 12345, command: 'claude', current_path: '/home/u'},
        {window: '1', pane: '0', window_active: false, active: true, process_label: 'codex', process_label_pid: 24680, command: 'codex', current_path: '/home/u'},
      ],
      window_metadata: [
        {window: '0', window_index: 0, path: '/home/u', git: {root: '/repo/claude', branch: 'claude-branch', dirty_count: 2, head: 'abc1234 claude head'}},
        {window: '1', window_index: 1, path: '/home/u', git: {root: '/repo/codex-a', branch: 'codex-branch', dirty_count: 0, head: 'def5678 codex head'}},
      ],
    };
    api.setTranscriptInfoForTest('5', perWindowInfo);
    api.setTabberSessionFilesForTest('5', [
      {path: 'claude.py', abs_path: '/repo/claude/claude.py', repo: '/repo/claude', status: 'M', mtime: 100, agents: ['claude'], agent_windows: [{kind: 'claude', window: '0', window_index: 0, pane: '0', pane_target: '5:0.0'}]},
      {path: 'codex-a.py', abs_path: '/repo/codex-a/codex-a.py', repo: '/repo/codex-a', status: 'M', mtime: 300, agents: ['codex'], agent_windows: [{kind: 'codex', window: '1', window_index: 1, pane: '0', pane_target: '5:1.0'}]},
      {path: 'codex-b.py', abs_path: '/repo/codex-b/codex-b.py', repo: '/repo/codex-b', status: 'M', mtime: 200, agents: ['codex'], agent_windows: [{kind: 'codex', window: '1', window_index: 1, pane: '0', pane_target: '5:1.0'}]},
    ]);
    api.setAutoApproveStateForTest('5', {
      agent_windows: [
        {kind: 'claude', state: 'working', working_elapsed_seconds: 10, window_index: 0, window_label: '0:claude', transcript: '/logs/claude-session.jsonl', transcript_id: 'claude-session-id'},
        {kind: 'codex', state: 'idle', idle_since: now - 120, last_active_ts: now - 120, window_index: 1, window_label: '1:codex', transcript: '/logs/codex-thread.jsonl', transcript_id: 'codex-thread-id'},
      ],
    });
    const perWindowHtml = api.sessionPopoverHtml('5', perWindowInfo, 'claude', false);
    const perWindowText = perWindowHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.ok(perWindowText.includes('tmux window 0:claude') && perWindowText.includes('/repo/claude') && perWindowText.includes('claude-branch'), 'popover attributes path and branch to the Claude window');
    assert.ok(perWindowText.includes('tmux window 1:codex') && perWindowText.includes('/repo/codex-a') && perWindowText.includes('/repo/codex-b') && perWindowText.includes('codex-branch'), 'popover attributes touched repo paths and branch to the Codex window');
    assert.equal(perWindowText.includes('/home/u'), false, 'touched repo attribution replaces the bare pane cwd fallback in per-window agent popovers');
    const tabberTree = api.buildTabberTree();
    const sessionFive = tabberTree.entries.find(entry => entry.tabber?.session === '5');
    const tabberCodexWindow = tabberTree.entriesByDir.get('/' + sessionFive.name).find(row => row.tabber.windowIndex === 1);
    const tabberCodexPaths = tabberTree.entriesByDir.get('/' + sessionFive.name + '/' + tabberCodexWindow.name).map(row => row.tabber.label);
    assert.deepEqual(tabberCodexPaths, ['/repo/codex-a', '/repo/codex-b'], 'popover and Tabber share the same per-window touched repo resolver');
    assert.ok(perWindowText.includes('tmux window 0:claude (pid=12345)'), 'popover header shows the Claude PID from the same pane record source as Tabber');
    assert.ok(perWindowText.includes('tmux window 1:codex (pid=24680)'), 'popover header shows the Codex PID from the same pane record source as Tabber');
    assert.ok(perWindowHtml.includes('Session ID') && perWindowHtml.includes('data-copy-path="claude-session-id"') && perWindowHtml.includes('data-copy-path="/logs/claude-session.jsonl"'), 'HT1/HT3: agent popovers show session ID and transcript location with shared copy buttons');
    assert.equal(perWindowHtml.includes('Transcript ID'), false, 'Codex/Claude ID rows are no longer mislabeled as transcript IDs');
    assert.ok(/popover-label">Transcript<\/div><div class="popover-value">[\s\S]*data-copy-path="\/logs\/claude-session\.jsonl"/.test(perWindowHtml), 'the transcript path remains a separate Transcript row');
    assert.ok(perWindowHtml.includes('data-copy-path="codex-thread-id"') && perWindowHtml.includes('data-copy-path="/logs/codex-thread.jsonl"'), 'HT2: transcript rows are attributed per AI window');
    assert.equal(perWindowText.split('tmux window 0:claude').length - 1, 1, 'Claude window label appears once in the merged state/metadata row');
    assert.equal(perWindowText.split('tmux window 1:codex').length - 1, 1, 'Codex window label appears once in the merged state/metadata row');
    assert.equal(perWindowHtml.includes('session-window-metadata-title'), false, 'per-window metadata no longer repeats the tmux window label as a title');
    assert.equal(perWindowText.includes('/repo/selected-session-path'), false, 'multi-agent popover does not render the old selected-pane path as a flat session path');

    api.setTabberSessionFilesForTest('5', []);
    const sharedWindowInfo = {
      ...perWindowInfo,
      window_metadata: [
        {window: '0', window_index: 0, path: '/repo/shared', git: {root: '/repo/shared', branch: 'shared-branch'}},
        {window: '1', window_index: 1, path: '/repo/shared', git: {root: '/repo/shared', branch: 'shared-branch'}},
      ],
    };
    const sharedHtml = api.sessionPopoverHtml('5', sharedWindowInfo, 'claude', false);
    const sharedText = sharedHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    assert.equal(sharedText.split('tmux window 0:claude').length - 1, 1, 'shared-path Claude window label appears once');
    assert.equal(sharedText.split('tmux window 1:codex').length - 1, 1, 'shared-path Codex window label appears once');
    assert.equal(sharedText.split('/repo/shared').length - 1, 1, 'shared metadata collapses to one path block after the window rows');
    assert.equal(sharedHtml.includes('session-window-metadata-title'), false, 'shared metadata no longer renders a duplicate label title');

    const sessionsCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
    assert.ok(/\.session-agent-window-block > \.session-agent-row\s*\{[\s\S]*background:\s*var\(--pane-inactive-tab-bg\)/.test(sessionsCss), 'per-window popover headers use the pane-tab shaded background token');
    assert.ok(/\.session-agent-window-block > \.session-agent-row\.current\s*\{[\s\S]*background:\s*var\(--active-tab-muted-bg\)/.test(sessionsCss), 'current per-window popover header uses the active muted tab background token');
  });

  test('t@6754', () => {
    const api = loadYolomux();
    const info = {
      selected_pane: {current_path: '/home/test/project/project3'},
      project: {
        git: {branch: 'keivenc/GH-2132__reasoning-dangling-end-marker', root: '/home/test/project/project3'},
        pull_request: {
          number: 9981,
          title: 'fix(parser): parse dangling reasoning end markers',
          description: 'Parser PR description mentions fallback recovery',
          status_label: 'CI failing',
          checks: {state: 'failure'},
        },
        linear: [{identifier: 'GH-2132', title: 'DeepSeek V4 validation'}],
      },
    };
    api.setTranscriptInfoForTest('4', info);

    const detail = api.tabMenuDetailText('4', info);
    const searchFields = api.tabSearchFields('4');
    assert.ok(searchFields.includes('PR'), 'tab search fields include the literal PR token');
    assert.ok(searchFields.includes('PR#9981'), 'tab search fields include PR#number');
    assert.ok(searchFields.includes('#9981'), 'tab search fields include #number');
    assert.ok(searchFields.includes('9981'), 'tab search fields include bare PR number');
    assert.ok(searchFields.includes('Parser PR description mentions fallback recovery'), 'tab search fields include the PR description');
    assert.ok(searchFields.includes('GH-2132'), 'tab search fields include Linear identifiers from issue objects');
    assert.ok(searchFields.includes('DeepSeek V4 validation'), 'tab search fields include Linear titles from issue objects');
    assert.ok(detail.includes('GH-2132__reasoning-dangling-end-marker'), 'tab menu detail includes fuller branch name');
    assert.ok(detail.includes('~/project/project3'), 'tab menu detail includes compact path');
    const prFailingLabel = api.t('pr.status.failing');
    assert.ok(detail.includes(`#9981 ${prFailingLabel}`), 'tab menu detail includes localized PR and status');
    assert.ok(detail.includes('GH-2132'), 'tab menu detail includes Linear identifier');
    const linearIndex = detail.indexOf('GH-2132', detail.indexOf('~/project/project3'));
    assert.ok(linearIndex < detail.indexOf(`#9981 ${prFailingLabel}`), 'tab menu detail lists Linear before PR');

    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.leafNode('left');
    slots.left = api.paneStateWithTabs(['4'], '4');
    api.setLayoutSlotsForTest(slots);
    const command = api.menuTabCommand('4');
    assert.ok(command.ariaLabel.includes('GH-2132__reasoning-dangling-end-marker'), 'tab menu row aria label carries detail');
    assert.ok(command.html.includes('fix(parser): parse dangling reasoning end markers'), 'tab menu row includes long PR title');
    assert.ok(command.html.includes('pane-tab-core'), 'tab menu row uses pane tab markup');

    const popover = api.sessionPopoverHtml('4', info, 'codex', true);
    assert.ok(popover.indexOf('popover-label">Linear') < popover.indexOf('popover-label">PR'), 'tab popover lists Linear before PR');
  });

  // a session is findable by an OTHER-branch PR / branch name / Linear ID — the same
  // project.git.other_branches data YO!info shows — not only its current-branch PR.
  test('t@6800', () => {
    const api = loadYolomux();
    const info = {
      selected_pane: {current_path: '/home/test/dynamo4'},
      project: {
        git: {
          branch: 'main',
          root: '/home/test/dynamo4',
          other_branches: {
            branches: [
              {
                name: 'keivenc/DIS-2193__other-work',
                current: false,
                subject: 'feat: branch subject text',
                pull_request: {number: 10289, title: 'feat: other branch work', description: 'PR body explains cut over parser wiring', linear_ids: ['DIS-2200']},
                linear_ids: ['DIS-2193'],
              },
            ],
          },
        },
      },
    };
    api.setTranscriptInfoForTest('4', info);
    const fields = api.tabSearchFields('4');
    assert.ok(fields.includes('#10289'), 'an other-branch PR is indexed as #N');
    assert.ok(fields.includes('PR#10289'), '...and as PR#N');
    assert.ok(fields.includes('10289'), '...and as a bare number');
    assert.ok(fields.includes('keivenc/DIS-2193__other-work'), 'the other branch name is indexed');
    assert.ok(fields.includes('feat: branch subject text'), 'the other branch subject is indexed');
    assert.ok(fields.includes('DIS-2193'), 'the other-branch Linear ID is indexed');
    assert.ok(fields.includes('DIS-2200'), 'the other-branch PR Linear IDs are indexed');
    assert.ok(fields.includes('feat: other branch work'), 'the other-branch PR title is indexed');
    assert.ok(fields.includes('PR body explains cut over parser wiring'), 'the other-branch PR description is indexed');
    assert.ok(Number.isFinite(api.tabSearchScore('4', 'cut over')), 'searching other-branch PR description matches the session');
    assert.ok(api.tabSearchScore('4', '#10289') >= 0, 'searching #10289 matches the session');
    assert.ok(api.tabSearchScore('4', 'DIS-2193') >= 0, 'searching the Linear ID matches the session');
    api.setCommandPaletteStateForTest('files', 'cut over');
    const visibleRows = api.commandPaletteRankItems(api.commandPaletteItems(), 'cut over').slice(0, 60);
    assert.ok(visibleRows.some(item => item.targetItem === '4'), 'Cmd-P searching cut over shows the matching pane');
  });

  test('t@6801', () => {
    const api = loadYolomux('', ['2']);
    api.setTranscriptInfoForTest('2', {
      selected_pane: {current_path: '/home/test/dynamo/dynamo2'},
      project: {
        git: {
          branch: 'keivenchang/DIS-2223__nemotron-reasoning-end-token-stream-split',
          root: '/home/test/dynamo/dynamo2',
          other_branches: {
            branches: [
              {
                name: 'keivenchang/DIS-2223__nemotron-reasoning-end-token-stream-split',
                current: true,
                subject: 'Rework parser debug taps to always-on anomaly detection',
                pull_request: {number: 10569, title: 'Rework parser debug taps to always-on anomaly detection'},
                linear_ids: ['DIS-2223'],
              },
            ],
          },
        },
      },
    });
    api.setFileQuickOpenCandidatesForTest('/home/test/dynamo', Array.from({length: 80}, (_, index) => ({
      name: `10569-noise-${index}.txt`,
      path: `/home/test/dynamo/noise/10569-noise-${index}.txt`,
      relative_path: `noise/10569-noise-${index}.txt`,
    })));
    api.setCommandPaletteStateForTest('files', '10569');
    const fields = api.tabSearchFields('2');
    assert.ok(fields.includes('10569'), 'current branch PR from other_branches is indexed as a bare number');
    assert.ok(api.tabMenuDetailText('2').includes('#10569'), 'current branch PR from other_branches is visible in the tab detail');
    const rows = api.commandPaletteRankItems(api.commandPaletteItems(), '10569').slice(0, 8);
    const row = rows.find(item => item.targetItem === '2');
    assert.ok(row, 'Cmd-P searching the current PR number keeps the matching pane on the first screen');
    assert.ok(row.detail.startsWith('PR #10569 · '), 'Cmd-P row detail puts the matching PR number before long branch/path text');
    const popupText = api.commandPaletteResultsHtmlForTest().replace(/<[^>]+>/g, '');
    assert.ok(popupText.includes('PR #10569'), 'rendered Cmd-P popup visibly shows the matching PR number');
  });

  test('t@6802', () => {
    const api = loadYolomux('', ['wt']);
    api.setTranscriptInfoForTest('wt', {
      project: {
        git: {
          root: '/home/test/yolomux.dev3',
          cwd: '/home/test/yolomux.dev3',
          worktree: {
            path: '/home/test/yolomux.dev3',
            parent_root: '/home/test/yolomux',
            name: 'yolomux.dev3',
          },
          other_branches: {
            branches: [
              {name: 'yolomux.dev3', current: true, updated: 'today', updated_ts: 1, subject: 'worktree path row'},
            ],
          },
        },
      },
    });
    const [row] = api.infoBranchRows();
    assert.equal(row.pathLabel, '~/yolomux.dev3 (worktree of ~/yolomux)', 'YO!info path shows the compact full path and its worktree parent');
    assert.equal(row.pathTitle, '/home/test/yolomux.dev3 (worktree of /home/test/yolomux)', 'YO!info path tooltip keeps the absolute path and parent');
  });

  test('t@info-branch-repo-inventory', () => {
    const api = loadYolomux('', ['s1']);
    api.setTranscriptInfoForTest('s1', {
      project: {
        git: {
          root: '/repo/app',
          branch: 'main',
          other_branches: {
            branches: [
              {name: 'main', current: true, updated: '1 minute ago', updated_ts: 300, subject: 'app current'},
              {name: 'feature/app', current: false, updated: '2 minutes ago', updated_ts: 200, subject: 'app feature'},
            ],
          },
        },
        repos: [
          {
            root: '/repo/app',
            branch: 'main',
            other_branches: {
              branches: [
                {name: 'main', current: true, updated: '1 minute ago', updated_ts: 300, subject: 'duplicate app current'},
              ],
            },
          },
          {
            root: '/repo/lib',
            branch: 'lib-main',
            other_branches: {
              branches: [
                {name: 'lib-main', current: true, updated: 'today', updated_ts: 400, subject: 'lib current'},
                {name: 'feature/lib', current: false, updated: 'yesterday', updated_ts: 100, subject: 'lib feature'},
              ],
            },
          },
        ],
        pull_request: null,
        linear: [],
      },
    });
    const rowKey = row => `${row.path}\n${row.branch}`;
    const rows = new Map(api.infoBranchRows().map(row => [rowKey(row), row]));
    assert.equal(rows.get('/repo/app\nmain').session, 's1', 'YO!info keeps the session label for the primary checked-out branch');
    assert.equal(rows.get('/repo/app\nfeature/app').session, '', 'YO!info leaves non-current primary repo branches unassigned');
    assert.equal(rows.get('/repo/lib\nlib-main').session, 's1', 'YO!info assigns the session to a checked-out branch in a secondary touched repo');
    assert.equal(rows.get('/repo/lib\nfeature/lib').session, '', 'YO!info shows secondary repo branches without pretending the session owns them');
    assert.equal(rows.get('/repo/lib\nfeature/lib').updatedTs, 100, 'YO!info keeps the branch last-modified timestamp from the touched repo inventory');
    assert.equal(rows.get('/repo/lib\nfeature/lib').pathLabel, '/repo/lib', 'YO!info shows the secondary touched repo path');
  });

  test('t@info-session-drawer', () => {
    const api = loadYolomux('', ['s1']);
    api.setTranscriptInfoForTest('s1', {
      project: {
        git: {
          root: '/repo/app',
          cwd: '/repo/app/src',
          branch: 'feature/info',
          dirty_count: 3,
          ahead: 2,
          behind: 1,
          other_branches: {
            branches: [
              {name: 'feature/info', current: true, updated: 'today', updated_ts: 400, subject: 'info drawer'},
            ],
          },
        },
        pull_request: {number: 42, title: 'Add info drawer', url: 'https://example.test/pull/42', status_label: 'passing', checks: {status_label: 'passing'}},
        linear: [{identifier: 'GUI-7', title: 'Info drawer metadata', state: 'In Progress', url: 'https://linear.test/GUI-7'}],
      },
    });
    api.setActivitySummaryPayloadForTest({
      generated_at: '2026-06-19T18:00:00+00:00',
      session_file_hours: 336,
      session_info: {
        s1: {
          path: '/repo/app',
          git: {root: '/repo/app', branch: 'feature/info', dirty_count: 3, ahead: 2, behind: 1},
          pull_request: {number: 42, title: 'Add info drawer', url: 'https://example.test/pull/42', status_label: 'passing', checks: {status_label: 'passing'}},
          ci: {status_label: 'passing'},
          linear: [{identifier: 'GUI-7', title: 'Info drawer metadata', state: 'In Progress', url: 'https://linear.test/GUI-7'}],
          latest_summary: 'Latest YO!info summary line',
          recent_events: [{time: '2026-06-19T18:00:01Z', type: 'state_changed', message: 'ready'}],
        },
      },
    });
    const closed = api.infoSessionDrawerHtmlForTest('s1');
    assert.ok(closed.includes('/repo/app'), 'D3: drawer renders cached full path from the activity-summary payload');
    assert.ok(closed.includes('feature/info'), 'D3: drawer renders branch metadata');
    assert.ok(closed.includes('dirty 3 · ahead 2 · behind 1'), 'D3: drawer renders dirty/ahead/behind counts');
    assert.ok(closed.includes('#42'), 'D3: drawer renders PR metadata through the shared PR renderer');
    assert.ok(closed.includes('passing'), 'D3: drawer renders CI status');
    assert.ok(closed.includes('GUI-7'), 'D3: drawer renders issue metadata');
    assert.ok(closed.includes('Latest YO!info summary line'), 'D3: drawer renders latest summary');
    assert.ok(closed.includes('state_changed · ready'), 'D3: drawer renders recent events');
    assert.equal(api.toggleInfoSessionDrawerForTest('s1'), true, 'D3: opening the drawer records per-session open state');
    assert.equal(api.toggleInfoSessionDrawerForTest('s1'), false, 'D3: closing the drawer clears only that session open state');
    const drawerSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/const infoSessionDrawerHtmlCache = new Map\(\)/.test(drawerSource), 'D3: drawer HTML is cached per session/payload signature');
    assert.ok(/function applyActivitySummaryPayloadFromPush[\s\S]*clearInfoSessionDrawerCache\(\)/.test(drawerSource), 'D3: activity-summary payload refresh invalidates drawer cache');
    assert.ok(/async function applyTranscriptsPayload[\s\S]*clearInfoSessionDrawerCache\(\)/.test(drawerSource), 'D3: transcript metadata refresh invalidates drawer cache');
    assert.ok(/function toggleInfoSessionDrawer[\s\S]*refreshActivitySummary\(\{force: true\}\)/.test(drawerSource), 'D3: opening a drawer lazily fetches all-session activity data when absent');
  });

  test('t@6833', () => {
    const api = loadYolomux('', ['alpha', 'beta'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {
        availableAgents: ['codex', 'claude'],
        agentAuth: {
          codex: {installed: true, logged_in: true},
          claude: {installed: true, logged_in: true},
        },
      },
    });
    const baseActivitySummaryPayload = {
      generated_at: '2026-05-31T12:00:00+00:00',
      global: {
        headline: "Your most recent work is about editor fixes, and you are currently making changes to yolomux.dev in order to finish editor fixes. So far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
        lines: [
          "Your most recent work is about editor fixes, and you are currently making changes to yolomux.dev in order to finish editor fixes. So far: 3 files changed (+9/-2); 1 of 2 AI agents is active.",
          'Session alpha: Codex is active in yolomux.dev; 2 files changed (+8/-1); editor fixes',
        ],
      },
      sessions: {
        alpha: {local: "Codex session alpha is active in yolomux.dev. It has been working on editor fixes. It currently has 2 files changed (+8/-1)."},
      },
      agents: [
        {session: '5', window: '2', window_name: 'codex', window_label: '2:codex', agent_kind: 'codex', label: "session '5' 2:codex", running: true, sort_ts: Date.now() / 1000, cwd: '/home/test/yolomux.dev', recent_paths: [{path: '/home/test/yolomux.dev', mtime: Date.now() / 1000, count: 2}]},
        {session: '6', window: '1', window_name: 'claude', window_label: '1:claude', agent_kind: 'claude', label: "session '6' 1:claude", last_used_ts: Date.now() / 1000 - 180, sort_ts: Date.now() / 1000 - 180, cwd: '/home/test/other', recent_paths: [{path: '/home/test/other', mtime: Date.now() / 1000 - 180, count: 1}]},
      ],
    };
    const noAgentApi = loadYolomux('', ['alpha', 'beta']);
    noAgentApi.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    const noAgentHtml = noAgentApi.yoagentChatHtml();
    assert.ok(noAgentHtml.includes('data-yoagent-chat-form'), 'No-backend YO!agent still shows a disabled chat form');
    assert.ok(noAgentHtml.includes('Set a Claude or Codex backend in Preferences to chat.'), 'No-backend YO!agent shows the disabled backend message');
    assert.ok(/data-yoagent-backend[\s\S]*disabled[\s\S]*No agent/.test(noAgentHtml), 'No-backend composer shows a disabled none backend state');
    const claudeOnlyApi = loadYolomux('', ['alpha', 'beta'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {
        availableAgents: ['claude'],
        agentAuth: {claude: {installed: true, logged_in: true}},
      },
    });
    const claudeOnlyHtml = claudeOnlyApi.yoagentChatHtml();
    assert.ok(/data-yoagent-backend[\s\S]*<option value="claude" selected/.test(claudeOnlyHtml), 'Composer selects the only installed logged-in backend');
    assert.equal(/data-yoagent-backend[\s\S]*<option value="codex"/.test(claudeOnlyHtml), false, 'Composer hides unavailable backends');
    api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    assert.ok(api.globalActivitySummaryHtml().includes('YO!agent'), 'global activity summary uses the YO agent label');
    assert.equal(api.globalActivitySummaryHtml().includes('Session alpha'), false, 'YO!agent default panel does not expose the per-session SESSION detail list');
    api.setClientSettingsPatchForTest({yoagent: {backend: 'claude'}});
    assert.equal(api.yoagentChatHtml().includes('Your most recent work is about editor fixes'), false, 'Claude-backed YO!agent does not auto-inject Recent agents until the startup one-shot is enabled');
    assert.equal(api.showYoagentStartupInfoOnceForTest(), true, 'YO!agent startup info can be shown once when the tab first opens');
    assert.equal(api.showYoagentStartupInfoOnceForTest(), false, 'YO!agent startup info does not re-show on later renders');
    const enabledChatHtml = api.yoagentChatHtml();
    assert.ok(enabledChatHtml.includes('data-yoagent-chat-form'), 'Claude-backed YO!agent panel includes a chat form');
    assert.ok(enabledChatHtml.includes('Your most recent work is about editor fixes'), 'Claude-backed YO!agent chat shows the regular intro message only during startup');
    assert.ok(enabledChatHtml.includes('Ask anything'), 'Claude-backed YO!agent composer uses the localized ask-anything placeholder');
    assert.ok(enabledChatHtml.includes('yoagent-message assistant yoagent-recent-agents-message'), 'YO!agent chat shows recent agents as an assistant-style response during startup');
    assert.ok(enabledChatHtml.includes('<ul class="yoagent-recent-agents-list">'), 'YO!agent chat shows recent agents as a bullet list');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-session">session 5'), 'YO!agent recent agents show the session in a fixed field');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-window">2:codex'), 'YO!agent recent agents show the tmux window name in a fixed field');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-paths">~/yolomux.dev'), 'YO!agent recent agents show touched paths from the backend agent payload');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-activity">running'), 'YO!agent recent agents show running agents as running');
    assert.ok(enabledChatHtml.includes('yoagent-recent-agent-activity">3 min ago'), 'YO!agent recent agents show compact last-used time for idle agents');
    assert.ok(enabledChatHtml.indexOf('yoagent-recent-agent-session">session 5') < enabledChatHtml.indexOf('yoagent-recent-agent-session">session 6'), 'YO!agent recent agents preserve backend recency order');
    api.applyActivitySummaryPayloadFromPushForTest({
      generated_at: '2026-05-31T12:05:00+00:00',
      global: {headline: 'Pushed summary should stay out of the printed startup block'},
      sessions: {},
      agents: [{session: '7', window_label: '0:claude', agent_kind: 'claude', label: "session '7' 0:claude", running: true}],
    });
    const pushUpdatedChatHtml = api.yoagentChatHtml();
    assert.ok(pushUpdatedChatHtml.includes('Your most recent work is about editor fixes'), 'activity-summary pushes do not repaint the one-shot startup summary');
    assert.equal(pushUpdatedChatHtml.includes('Pushed summary should stay out of the printed startup block'), false, 'activity-summary pushes are cache-only for the printed startup block');
    assert.equal(pushUpdatedChatHtml.includes('yoagent-recent-agent-session">session 7'), false, 'activity-summary pushes do not repaint printed Recent Agents');
    api.applyActivitySummaryPayloadFromPushForTest({
      generated_at: '2026-05-31T12:10:00+00:00',
      global: {headline: 'Manual refresh replaces the printed startup block'},
      sessions: {},
      agents: [{session: '8', window_label: '0:codex', agent_kind: 'codex', label: "session '8' 0:codex", running: true}],
    }, {refreshStartupSnapshot: true});
    const manuallyRefreshedChatHtml = api.yoagentChatHtml();
    assert.ok(manuallyRefreshedChatHtml.includes('Manual refresh replaces the printed startup block'), 'explicit activity-summary refresh replaces the startup summary snapshot');
    assert.ok(manuallyRefreshedChatHtml.includes('yoagent-recent-agent-session">session 8'), 'explicit activity-summary refresh replaces the Recent Agents snapshot');
    assert.equal(manuallyRefreshedChatHtml.includes('Your most recent work is about editor fixes'), false, 'explicit refresh removes the stale startup summary snapshot');
    api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    api.showYoagentStartupInfoForLatestActivityForTest();
    api.setYoagentMessagesForTest([{role: 'user', content: 'what changed?'}, {role: 'assistant', content: 'Checking the activity context.'}]);
    const chatWithHistoryHtml = api.yoagentChatHtml();
    assert.ok(chatWithHistoryHtml.includes('Checking the activity context.'), 'YO!agent chat keeps persisted messages');
    assert.ok(chatWithHistoryHtml.includes('yoagent-message assistant yoagent-recent-agents-message'), 'YO!agent chat keeps Recent Agents visible after a question');
    assert.ok(chatWithHistoryHtml.includes('Your most recent work is about editor fixes'), 'YO!agent chat keeps the current-work summary visible after a question');
    api.setActivitySummaryPayloadForTest({yoagent_summaries: {mode: 'first_launch', running: true, updated_ts: 1760000000, updated_at: '2025-10-09T08:53:20+00:00'}, global: {headline: 'Cached rolling context'}, sessions: {}, session_order: []});
    assert.equal(api.yoagentChatHtml().includes('Background transcript summaries on'), false, 'YO!agent chat no longer renders a continuous background-summary status notice');
    api.setActivitySummaryPayloadForTest(baseActivitySummaryPayload);
    assert.equal(enabledChatHtml.includes('yoagent-chat empty'), false, 'YO!agent intro is a regular message, not a special empty layout');
    assert.equal(enabledChatHtml.includes('yoagent-chat-toolbar'), false, 'YO!agent chat does not put Clear in a detached toolbar');
    assert.ok(enabledChatHtml.includes('yoagent-chat-controls'), 'YO!agent composer has a control row');
    assert.ok(enabledChatHtml.includes('data-yoagent-backend'), 'YO!agent composer shows the backend selector mapped to yoagent.backend');
    assert.ok(enabledChatHtml.includes('data-yoagent-model'), 'YO!agent composer shows the model selector');
    assert.ok(enabledChatHtml.includes('data-yoagent-effort'), 'YO!agent composer shows the effort selector');
    assert.ok(enabledChatHtml.indexOf('data-yoagent-backend') < enabledChatHtml.indexOf('data-yoagent-model'), 'YO!agent composer renders backend before model');
    assert.ok(enabledChatHtml.indexOf('data-yoagent-model') < enabledChatHtml.indexOf('data-yoagent-effort'), 'YO!agent composer renders model before effort');
    assert.ok(/data-yoagent-backend[\s\S]*?<option value="claude" selected/.test(enabledChatHtml), 'YO!agent composer selects the saved backend');
    const modelCatalogSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/'claude-fable-5': 'pref\.yoagent\.claude_model\.fable'/.test(modelCatalogSource), 'YO!agent frontend fallback includes the current generally available Claude Fable model');
    assert.ok(/'gpt-5\.3-codex-spark': 'pref\.yoagent\.codex_model\.gpt53spark'/.test(modelCatalogSource), 'YO!agent frontend fallback includes Codex Spark when the backend catalog is not loaded');
    assert.ok(/data-yoagent-model[\s\S]*data-yoagent-setting-path="yoagent\.claude_model"[\s\S]*?<option value="claude-opus-4-8"/.test(enabledChatHtml), 'YO!agent composer model options follow the selected backend');
    assert.ok(/data-yoagent-effort[\s\S]*data-yoagent-setting-path="yoagent\.claude_effort"[\s\S]*?<option value="low"/.test(enabledChatHtml), 'YO!agent composer effort options follow the selected backend');
    assert.equal(/data-yoagent-backend[\s\S]*?<option value="auto"/.test(enabledChatHtml), false, 'YO!agent composer backend selector does not offer Auto');
    assert.equal(/data-yoagent-backend[\s\S]*?<option value="deterministic"/.test(enabledChatHtml), false, 'YO!agent composer backend selector does not offer No agent as a selectable backend');
    assert.ok(enabledChatHtml.includes('yoagent-chat-send-icon'), 'YO!agent send button is a circular arrow icon');
    assert.ok(enabledChatHtml.indexOf('yoagent-chat-clear') < enabledChatHtml.indexOf('yoagent-chat-send'), 'YO!agent send arrow is the last (far-right) control, after Clear');
    api.setYoagentMessagesForTest([
      {role: 'user', content: 'first question', createdAt: '2026-06-13T17:38:00Z'},
      {role: 'assistant', content: 'first answer', createdAt: '2026-06-13T17:38:01Z'},
      {role: 'user', content: 'second question', createdAt: '2026-06-13T17:39:00Z'},
    ]);
    assert.ok(/:\d{2}\s*[AP]M\s*PDT/.test(api.yoagentChatHtml()), 'YO!agent message timestamps include seconds');
    api.setYoagentDraftForTest('new draft');
    const historyInput = {value: 'new draft', disabled: false, setSelectionRange(start, end) { this.selection = [start, end]; }};
    assert.deepStrictEqual(api.yoagentUserMessageHistoryForTest(), ['first question', 'second question'], 'YO!agent composer history contains only prior user messages');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'up'), true, 'Up enters YO!agent composer history');
    assert.equal(historyInput.value, 'second question', 'first Up shows the most recent user message');
    assert.deepStrictEqual(historyInput.selection, ['second question'.length, 'second question'.length], 'history navigation places the cursor at the end for editing');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'up'), true, 'repeated Up walks older');
    assert.equal(historyInput.value, 'first question', 'second Up shows the older user message');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'up'), true, 'Up at the oldest message is handled');
    assert.equal(historyInput.value, 'first question', 'Up clamps at the oldest message');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'down'), true, 'Down walks newer');
    assert.equal(historyInput.value, 'second question', 'first Down returns to the newer history message');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'down'), true, 'Down from newest history returns to the latest draft slot');
    assert.equal(historyInput.value, 'new draft', 'latest slot restores the unsent draft so the placeholder is visible when blank');
    assert.equal(api.yoagentNavigateChatHistoryForTest(historyInput, 'down'), false, 'Down at the latest draft slot leaves the composer alone');
    api.applyYoagentConversationPayloadForTest({
      transcript_path: '/home/test/.local/state/yolomux/yoagent/conversation.jsonl',
      transcript_display_path: '~/.local/state/yolomux/yoagent/conversation.jsonl',
      messages: [{role: 'user', content: 'persisted question', createdAt: '2026-06-13T17:39:00Z'}],
    });
    const transcriptHtml = api.yoagentChatHtml();
    assert.ok(transcriptHtml.includes('yoagent-transcript-path'), 'YO!agent chat shows the persisted transcript location at the top');
    assert.ok(transcriptHtml.includes('~/.local/state/yolomux/yoagent/conversation.jsonl'), 'YO!agent transcript row uses the compact display path');
    assert.ok(transcriptHtml.includes('data-copy-path="/home/test/.local/state/yolomux/yoagent/conversation.jsonl"'), 'YO!agent transcript path can be copied');
    assert.equal(transcriptHtml.includes('yoagent-message assistant yoagent-recent-agents-message'), true, 'persisted YO!agent messages keep the one-shot Recent agents block visible');
    api.applyYoagentConversationPayloadForTest({
      messages: [{role: 'user', content: 'ask 6 and 7 for status', createdAt: '2026-06-13T17:39:00Z'}],
      pending_waits: [
        {id: 'wait-6', session: '6', started_ts: Date.now() / 1000 - 5, transcript: '/tmp/6.jsonl'},
        {id: 'wait-7', session: '7', started_ts: Date.now() / 1000 - 10, transcript: '/tmp/7.jsonl'},
      ],
    });
    const pendingWaitsHtml = api.yoagentChatHtml();
    assert.ok(pendingWaitsHtml.includes('yoagent-waiting-queue'), 'pending result waits render as a visible queue');
    assert.equal((pendingWaitsHtml.match(/class="yoagent-waiting-item"/g) || []).length, 2, 'multiple pending waits render as separate rows');
    assert.ok(pendingWaitsHtml.includes('data-yoagent-wait-clear="wait-6"'), 'pending waits expose a clear control');
    assert.ok(pendingWaitsHtml.includes('data-yoagent-wait-clear="wait-7"'), 'each pending wait can be cleared independently');
    assert.ok(/data-yoagent-chat-input[^>]*placeholder="Ask anything…"(?![^>]* disabled)/.test(pendingWaitsHtml), 'pending waits do not disable the YO!agent composer input');
    assert.ok(/class="yoagent-chat-send"(?![^>]* disabled)/.test(pendingWaitsHtml), 'pending waits do not disable the YO!agent send button');
    api.applyYoagentConversationPayloadForTest({
      messages: [
        {role: 'user', content: 'ask 6 for status', createdAt: '2026-06-13T17:39:00Z'},
        {role: 'assistant', kind: 'agent_result', session: '6', content: 'I sent the request to tmux session `6`, but I did not see a result before the wait timed out.', createdAt: '2026-06-13T17:40:00Z'},
      ],
      pending_waits: [],
    });
    const clearedWaitsHtml = api.yoagentChatHtml();
    assert.equal(clearedWaitsHtml.includes('yoagent-waiting-queue'), false, 'cleared server waits remove the pending queue');
    assert.ok(clearedWaitsHtml.includes('did not see a result before the wait timed out'), 'cleared waits leave the visible timeout/result message');
    assert.ok(/data-yoagent-chat-input[^>]*placeholder="Ask anything…"(?![^>]* disabled)/.test(clearedWaitsHtml), 'cleared waits keep the YO!agent composer input enabled');
    api.applyYoagentJobsPayloadForTest({
      jobs: [
        {id: 'job-confirm', type: 'wait_then_send', status: 'pending_confirmation', target: {session: '6'}, public_text: 'send date', last_observed_state: {blockers: ['target is busy']}},
        {id: 'job-queued', type: 'result_watch', status: 'queued', target: {roster: ['6', '7']}, action: {text_preview: 'wait for replies'}},
        {id: 'job-fired', type: 'wait_then_send', status: 'fired', session: '8', action: {text: 'echo done'}},
        {id: 'job-failed', type: 'wait_then_send', status: 'failed', target: {session: '9'}, error: 'timed out waiting'},
        {id: 'job-cancelled', type: 'wait_then_send', status: 'cancelled', target: {session: '10'}},
      ],
    });
    const jobsHtml = api.yoagentChatHtml();
    assert.ok(jobsHtml.includes('yoagent-jobs-list'), 'YO!agent jobs render as a visible queue in the chat history');
    assert.equal((jobsHtml.match(/class="yoagent-job-item/g) || []).length, 5, 'queued, pending, fired, failed, and cancelled jobs render as separate rows');
    assert.ok(jobsHtml.includes('data-yoagent-job-confirm="job-confirm"'), 'pending-confirmation jobs expose a confirm control');
    assert.ok(jobsHtml.includes('data-yoagent-job-cancel="job-confirm"'), 'pending-confirmation jobs expose a cancel control');
    assert.ok(jobsHtml.includes('data-yoagent-job-cancel="job-queued"'), 'queued jobs expose a cancel control');
    assert.equal(jobsHtml.includes('data-yoagent-job-confirm="job-fired"'), false, 'fired jobs do not expose stale confirm controls');
    assert.ok(jobsHtml.includes('target 6') && jobsHtml.includes('blocked by target is busy'), 'job rows show target sessions and blockers');
    assert.ok(jobsHtml.includes('send date') && jobsHtml.includes('wait for replies'), 'job rows show prompt/action previews');
    assert.ok(/data-yoagent-chat-input[^>]*placeholder="Ask anything…"(?![^>]* disabled)/.test(jobsHtml), 'visible jobs do not disable the YO!agent composer input');
    api.setYoagentMessagesForTest([
      {role: 'user', content: 'wait for session 6, then ask for date', createdAt: '2026-06-13T17:40:00Z'},
      {
        role: 'assistant',
        content: 'I resolved tmux session `6` and prepared a confirmed send action.',
        createdAt: '2026-06-13T17:40:01Z',
        details: '- backend: `claude`\n- response time: `1.234s` (`1234.0ms`)',
        responseMs: 5300,
        actions: [{
          id: 'ya_test',
          status: 'ready',
          session: '6',
          text: 'date',
          target: {session: '6', agent_kind: 'claude', transport: 'pane-paste', pane_target: '%6', cwd: '/repo/app'},
        }],
      },
      {
        role: 'assistant',
        kind: 'agent_result',
        session: '6',
        content: 'Result from tmux session `6`:\n\nThe date is June 13, 2026.',
        createdAt: '2026-06-13T17:41:00Z',
      },
    ]);
    const actionHtml = api.yoagentChatHtml();
    assert.ok(actionHtml.includes('yoagent-message user'), 'YO!agent user turns keep a role-specific bubble');
    assert.ok(actionHtml.includes('yoagent-message assistant'), 'YO!agent assistant turns keep a role-specific bubble');
    assert.ok(actionHtml.includes('5.3 seconds to respond'), 'YO!agent assistant headers show response latency from the persisted message field');
    assert.equal((actionHtml.match(/seconds to respond/g) || []).length, 1, 'YO!agent user turns do not show response latency');
    assert.ok(actionHtml.includes('yoagent-message assistant yoagent-agent-result'), 'YO!agent target-agent result turns get a distinct result bubble class');
    assert.ok(actionHtml.includes('yoagent-agent-result-heading') && actionHtml.includes('yoagent-agent-result-output'), 'YO!agent target-agent result splits the heading from the quoted output block');
    assert.ok(actionHtml.includes('class="yoagent-message-details"') && actionHtml.includes('response time:'), 'YO!agent assistant turns can expose expandable safe diagnostics');
    assert.ok(actionHtml.includes('data-yoagent-action-card="ya_test"'), 'YO!agent assistant turns render server-resolved action cards');
    assert.ok(actionHtml.includes('data-yoagent-action-send="ya_test"'), 'ready YO!agent action cards expose a confirmed send control');
    assert.ok(actionHtml.includes('Action preview') && actionHtml.includes('Send'), 'ready YO!agent action cards use localized action labels');
    assert.ok(actionHtml.includes('visible tmux pane'), 'ready YO!agent action cards label sends as visible-pane delivery');
    const openDetail = {dataset: {yoagentMessageDetailsKey: 'assistant|1'}, open: true};
    const closedDetail = {dataset: {yoagentMessageDetailsKey: 'assistant|2'}, open: false};
    const stateNode = {
      querySelectorAll(selector) {
        if (selector === '.yoagent-message-details[open][data-yoagent-message-details-key]') return [openDetail];
        if (selector === '.yoagent-message-details[data-yoagent-message-details-key]') return [closedDetail, openDetail];
        return [];
      },
    };
    const openKeys = api.yoagentOpenMessageDetailsStateForTest(stateNode);
    api.restoreYoagentOpenMessageDetailsStateForTest(stateNode, openKeys);
    assert.deepStrictEqual([...openKeys], ['assistant|1'], 'YO!agent captures the opened Details message key before repaint');
    assert.equal(openDetail.open, true, 'YO!agent restores the matching Details block after repaint');
    assert.equal(closedDetail.open, false, 'YO!agent does not expand unrelated Details blocks after repaint');
    api.setYoagentBusyForTest(true);
    assert.ok(api.yoagentChatHtml().includes('yoagent-chat-spinner'), 'YO!agent busy state includes an animated spinner');
    // The "thinking" label keeps its word but the trailing dots are CSS-animated, so the text updates
    // without rebuilding the busy-state DOM.
    assert.ok(api.yoagentChatHtml().includes('thinking'), 'YO!agent busy state keeps the concise thinking label');
    assert.ok(api.yoagentChatHtml().includes('yoagent-thinking-dots'), 'YO!agent thinking dots are CSS animated, not hardcoded static text');
    assert.ok(api.yoagentChatHtml().includes('session-yolo-marker active working'), 'YO!agent busy spinner reuses the YO tab working marker');
    api.setYoagentMessagesForTest([
      {
        role: 'assistant',
        content: 'Done',
        createdAt: '2026-06-13T17:42:00Z',
        details: 'usage: {"cache_creation":{"input_tokens":123}}',
        auxiliaryPreview: 'usage: {"cache_creation":{"input_tokens":123}}',
      },
    ]);
    const usageOnlyHtml = api.yoagentChatHtml();
    const usageOnlySummary = usageOnlyHtml.match(/<summary>[\s\S]*?<\/summary>/)?.[0] || '';
    assert.ok(/<summary><span>details…<\/span><\/summary>/.test(usageOnlyHtml), 'YO!agent diagnostic-only details collapse to just details…');
    assert.ok(/<pre class="yoagent-safe-details">usage:/.test(usageOnlyHtml), 'YO!agent usage diagnostics remain visible inside the expanded details body');
    assert.equal(usageOnlySummary.includes('usage:'), false, 'YO!agent usage diagnostics do not leak into the collapsed summary preview');
    api.setYoagentMessagesForTest([
      {
        role: 'assistant',
        content: 'Done',
        createdAt: '2026-06-13T17:42:01Z',
        details: '- response time: `1.000s` (`1000.0ms`)',
        auxiliaryLines: ['thinking: reading activity context'],
        auxiliaryPreview: 'thinking: reading activity context',
      },
    ]);
    const thinkingPreviewHtml = api.yoagentChatHtml();
    assert.ok(thinkingPreviewHtml.includes('<summary><span>thinking (4 words)…</span></summary>'), 'completed YO!agent thinking details collapse to a count-only summary');
    assert.equal(thinkingPreviewHtml.includes('yoagent-details-preview'), false, 'completed YO!agent thinking details do not keep preview words in the collapsed summary');
    assert.ok(thinkingPreviewHtml.includes('<pre class="yoagent-auxiliary-stream">thinking: reading activity context</pre>'), 'expanded completed YO!agent thinking details keep the real thinking text');
    api.setYoagentBusyForTest(false);
    api.setYoagentDraftForTest('half typed question');
    assert.ok(api.yoagentChatHtml().includes('value="half typed question"'), 'YO!agent chat draft survives summary refresh re-renders');
    api.setYoagentErrorForTest("Couldn't reach the YOLOmux server. Your question is still in the box; retry after the server is back.");
    assert.ok(api.yoagentChatHtml().includes('data-yoagent-retry'), 'YO!agent network failures show a retry action without losing the draft');
    api.setYoagentErrorForTest('');
    api.setYoagentNoticeForTest({backend: 'claude', reason: 'Claude CLI is not logged in. Run `claude login`.'});
    assert.ok(api.yoagentChatHtml().includes('yoagent-chat-notice'), 'YO!agent chat surfaces backend fallback notices');
    assert.ok(api.yoagentChatHtml().includes('claude'), 'YO!agent fallback notice includes the backend');
    assert.ok(api.yoagentChatHtml().includes('claude login'), 'YO!agent fallback notice includes the login action');
    assert.ok(api.globalActivitySummaryHtml().includes('3 files changed (+9/-2)'), 'global activity summary renders file totals');
    assert.ok(api.globalActivitySummaryHtml().includes('data-yoagent-global-markdown'), 'global activity summary preserves markdown as escaped fallback until the render pass');
    assert.ok(api.globalActivitySummaryHtml().includes('Your most recent work is about editor fixes'), 'global activity summary renders a human sentence');
    assert.equal(api.globalActivitySummaryHtml().includes('Session alpha'), false, 'global activity summary omits per-session detail lines');
    assert.equal(api.sessionActivitySummary('alpha').local, "Codex session alpha is active in yolomux.dev. It has been working on editor fixes. It currently has 2 files changed (+8/-1).");
    api.setTranscriptInfoForTest('alpha', {
      project: {
        git: {
          root: '/repo/alpha',
          branch: 'zeta',
          other_branches: {
            branches: [
              {name: 'zeta', current: true, updated: 'yesterday', updated_ts: 100, subject: 'second item', linear_ids: ['GH-2']},
            ],
          },
        },
      },
    });
    api.setTranscriptInfoForTest('beta', {
      project: {
        git: {
          root: '/repo/beta',
          branch: 'alpha',
          other_branches: {
            branches: [
              {name: 'alpha', current: true, updated: 'today', updated_ts: 200, subject: 'first item', linear_ids: ['GH-1']},
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
    const shareInfoSnapshot = api.shareUiStateSnapshotForTest().info;
    assert.deepStrictEqual(canonical(shareInfoSnapshot.branchSort), {dir: 'asc', key: 'branch'}, 'YO!share snapshots the YO!info branch sort state');
    assert.deepStrictEqual(canonical(shareInfoSnapshot.branchRows.map(row => row.session)), ['beta', 'alpha'], 'YO!share snapshots host-owned YO!info rows');
    api.setInfoBranchColumnWidthForTest(520);
    api.setInfoDescColumnWidthForTest(760);
    const shareInfoWidthSnapshot = api.shareUiStateSnapshotForTest().info;
    assert.deepStrictEqual(canonical(shareInfoWidthSnapshot.columnWidths), {branch: 520, desc: 760}, 'YO!share snapshots host YO!info column widths');
    api.resetInfoBranchColumnWidthForTest();
    api.resetInfoDescColumnWidthForTest();
    const shareApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
      share: {view: true, id: 'share-info', mode: 'ro', session: '1', sessions: ['1']},
    });
    const shareInfoScroller = shareApi.testElementForId('info-content');
    shareInfoScroller.scrollTop = 0;
    shareInfoScroller.scrollLeft = 0;
    shareApi.setTranscriptInfoForTest('1', {
      project: {
        git: {
          root: '/repo/client-only',
          branch: 'client-local',
          other_branches: {
            branches: [
              {name: 'client-local', current: true, updated: 'now', updated_ts: 999, subject: 'must not render'},
            ],
          },
        },
      },
    });
    assert.deepStrictEqual(canonical(shareApi.infoBranchRows().map(row => row.session)), ['1'], 'share client starts with local YO!info rows before a host snapshot arrives');
    shareApi.applyShareUiStateForTest({info: {branchSort: {key: 'session', dir: 'desc'}, columnWidths: {branch: 610, desc: 820}, branchRows: shareInfoSnapshot.branchRows}});
    assert.deepStrictEqual(canonical(shareApi.shareUiStateSnapshotForTest().info.branchSort), {dir: 'desc', key: 'session'}, 'share viewers apply YO!info sort from the host UI snapshot');
    assert.deepStrictEqual(canonical(shareApi.infoBranchRows().map(row => row.session)), ['beta', 'alpha'], 'share viewers render host-owned YO!info rows instead of local transcript metadata');
    assert.equal(shareApi.infoBranchColumnWidthForTest(), 610, 'share viewers apply the host YO!info Branch column width');
    assert.equal(shareApi.infoDescColumnWidthForTest(), 820, 'share viewers apply the host YO!info desc column width');
    shareApi.applyShareScrollStateForTest({target: 'info', kind: 'info', top: 88, left: 144});
    assert.equal(shareInfoScroller.scrollTop, 88, 'share viewers apply YO!info vertical host scroll');
    assert.equal(shareInfoScroller.scrollLeft, 144, 'share viewers apply YO!info horizontal host scroll');
    shareInfoScroller.scrollTop = 0;
    shareInfoScroller.scrollLeft = 0;
    shareApi.restoreShareReadonlyScrollTargetForTest(shareInfoScroller);
    assert.equal(shareInfoScroller.scrollTop, 88, 'readonly YO!info local vertical scroll restores to the host position');
    assert.equal(shareInfoScroller.scrollLeft, 144, 'readonly YO!info local horizontal scroll restores to the host position');
    assert.equal(api.infoBranchColumnWidthForTest(), 320, 'YO!info Branch column defaults wider than the old 230px minimum');
    assert.equal(api.setInfoBranchColumnWidthForTest(520), 520, 'YO!info Branch column accepts a wider user size');
    assert.equal(api.storageValueForTest('yolomux.infoBranchColumnWidth.v1'), '520', 'YO!info Branch column width persists in browser storage');
    assert.equal(api.setInfoBranchColumnWidthForTest(100), 230, 'YO!info Branch column width clamps to its minimum');
    assert.equal(api.setInfoBranchColumnWidthForTest(5000), 900, 'YO!info Branch column width clamps to its maximum');
    assert.equal(api.resetInfoBranchColumnWidthForTest(), 320, 'YO!info Branch column reset restores the default');
    assert.equal(api.infoDescColumnWidthForTest(), 310, 'YO!info desc column defaults to the previous minimum width');
    assert.equal(api.setInfoDescColumnWidthForTest(760), 760, 'YO!info desc column accepts a wider user size');
    assert.equal(api.storageValueForTest('yolomux.infoDescColumnWidth.v1'), '760', 'YO!info desc column width persists in browser storage');
    assert.equal(api.setInfoDescColumnWidthForTest(100), 310, 'YO!info desc column width clamps to its minimum');
    assert.equal(api.setInfoDescColumnWidthForTest(5000), 1600, 'YO!info desc column width clamps to its maximum');
    assert.equal(api.resetInfoDescColumnWidthForTest(), 310, 'YO!info desc column reset restores the default');
    const runResizeDrag = (column, moveX) => {
      const handleListeners = new Map();
      let capturedPointer = null;
      let releasedPointer = null;
      const resizeHandle = {
        dataset: {infoColumnResize: column},
        addEventListener(type, listener) { handleListeners.set(type, listener); },
        setPointerCapture(pointerId) { capturedPointer = pointerId; },
        releasePointerCapture(pointerId) { releasedPointer = pointerId; },
      };
      const resizeNode = {
        querySelectorAll(selector) {
          return selector === '[data-info-column-resize]' ? [resizeHandle] : [];
        },
      };
      api.bindInfoColumnResizersForTest(resizeNode);
      assert.equal(resizeHandle.dataset.bound, 'true', `YO!info ${column} resize handle binds once`);
      handleListeners.get('pointerdown')({
        pointerId: 7,
        clientX: 100,
        preventDefault() {},
        stopPropagation() {},
      });
      assert.equal(capturedPointer, 7, `YO!info ${column} resize drag captures the pointer`);
      api.windowListenersForTest('pointermove')[0]({clientX: moveX});
      api.windowListenersForTest('pointerup')[0]({});
      assert.equal(releasedPointer, 7, `YO!info ${column} resize drag releases the pointer`);
      assert.equal(api.windowListenersForTest('pointermove').length, 0, `YO!info ${column} resize drag removes pointermove listener on finish`);
    };
    runResizeDrag('branch', 180);
    assert.equal(api.infoBranchColumnWidthForTest(), 400, 'YO!info Branch resize drag changes the column width');
    assert.equal(api.storageValueForTest('yolomux.infoBranchColumnWidth.v1'), '400', 'YO!info Branch resize drag persists the final width');
    runResizeDrag('desc', 250);
    assert.equal(api.infoDescColumnWidthForTest(), 460, 'YO!info desc resize drag changes the column width');
    assert.equal(api.storageValueForTest('yolomux.infoDescColumnWidth.v1'), '460', 'YO!info desc resize drag persists the final width');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function setInfoColumnWidth\(column, value, options = \{\}\)[\s\S]*const previous = infoColumnWidth\(column\)[\s\S]*scheduleShareUiStatePublish\(\)/.test(source), 'YO!info column width changes schedule a host share UI-state snapshot');
    assert.ok(/function shareInfoStateSnapshot\(options = \{\}\)[\s\S]*columnWidths:[\s\S]*branch:[\s\S]*infoBranchColumnWidthPx[\s\S]*desc:[\s\S]*infoDescColumnWidthPx[\s\S]*options\.includeRows !== false[\s\S]*branchRows = infoBranchRows\(\)\.map\(shareInfoRowSnapshot\)/.test(source), 'YO!share info snapshots include host YO!info rows and column widths when full state is requested');
    assert.ok(/function applyShareInfoState\(info = \{\}\)[\s\S]*shareInfoBranchRowsOverride = cleanShareInfoRows\(info\.branchRows\)[\s\S]*setInfoColumnWidth\('branch', widths\.branch, \{persist: false, publish: false\}\)[\s\S]*setInfoColumnWidth\('desc', widths\.desc, \{persist: false, publish: false\}\)/.test(source), 'share clients apply host YO!info rows and column widths without persisting or echo-publishing');
  });

  await testAsync('YO!agent chat queue waits for pending target-agent waits before sending', async () => {
    const api = loadYolomux('', ['alpha'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {
        availableAgents: ['claude'],
        agentAuth: {claude: {installed: true, logged_in: true}},
      },
    });
    api.setClientSettingsPatchForTest({yoagent: {backend: 'claude'}});
    const chatPosts = [];
    api.setFetchForTest((url, options = {}) => {
      const path = String(url);
      if (path === '/api/yoagent/chat') {
        const body = JSON.parse(options.body || '{}');
        chatPosts.push(body.message);
        return Promise.resolve(jsonResponse({
          backend: 'claude',
          backend_used: 'claude',
          answer: `${body.message} answer`,
          conversation: {
            messages: [
              {role: 'user', content: body.message, createdAt: '2026-06-13T17:39:00Z'},
              {role: 'assistant', content: `${body.message} answer`, createdAt: '2026-06-13T17:39:01Z'},
            ],
            pending_waits: [],
          },
        }));
      }
      return Promise.resolve(jsonResponse({messages: [], pending_waits: []}));
    });

    api.applyYoagentConversationPayloadForTest({
      messages: [{role: 'user', content: 'ask alpha for status', createdAt: '2026-06-13T17:38:00Z'}],
      pending_waits: [{id: 'wait-alpha', session: 'alpha', started_ts: Date.now() / 1000, transcript: '/tmp/alpha.jsonl'}],
    });
    await api.sendYoagentChatMessageForTest('second ask');
    assert.deepStrictEqual(chatPosts, [], 'pending target-agent waits keep later asks in the local queue');
    assert.deepStrictEqual(canonical(api.yoagentChatQueueForTest().map(item => item.text)), ['second ask'], 'later ask is visible as queued text');

    api.applyYoagentConversationPayloadForTest({
      messages: [{role: 'assistant', kind: 'agent_result', session: 'alpha', content: 'alpha result', createdAt: '2026-06-13T17:39:00Z'}],
      pending_waits: [],
    });
    for (let i = 0; i < 4; i += 1) await flushAsyncWork();
    assert.deepStrictEqual(chatPosts, ['second ask'], 'queued ask is sent only after the pending wait clears');
    assert.deepStrictEqual(canonical(api.yoagentChatQueueForTest()), [], 'sent ask is removed from the queue');
  });

  test('t@6976', () => {
    const api = loadYolomux();
    assert.equal(api.dedentSelectionText('  hello\n  world'), 'hello\nworld');
    assert.equal(api.dedentSelectionText('  hello\n    world'), 'hello\n  world');
    assert.equal(api.dedentSelectionText('\n  hello\n  world\n'), '\nhello\nworld\n');
    assert.equal(api.dedentSelectionText('hello\n  world'), 'hello\nworld');
    assert.equal(api.dedentSelectionText('● 1\n  2\n  3'), '1\n2\n3');
    assert.equal(api.dedentSelectionText('• answer'), 'answer');
    assert.equal(api.dedentSelectionText('• answer:\n\n  \"  hello\\n  world\"'), 'answer:\n\n\"  hello\\n  world\"');
  });

  test('t@6987', () => {
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
    assert.equal(middleLinks[0].type, 'url');
    assert.deepStrictEqual(canonical(middleLinks[0].range), {
      start: {x: 1, y: 1},
      end: {x: 6, y: 3},
    });

    const lastLinks = api.terminalWrappedLineLinks(term, 3);
    assert.equal(lastLinks.length, 1);
    assert.equal(lastLinks[0].text, 'https://example.com/abcdef');
  });

  // an agent HARD-wraps a long URL with a HANGING INDENT — the continuation is its own logical
  // line (isWrapped === false), indented under the URL column. Stitch it onto the link so the whole URL
  // is one clickable link, underlined across both rows at their real columns.
  test('t@7020', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('https://github.com/ai-dynamo/frontend-crates/actions/runs/26'),
      terminalLine('    919558600/job', false),  // hanging indent (4 spaces), NOT a soft-wrap
      terminalLine('$ ', false),                 // a plain prompt row — must NOT be merged
    ];
    // the URL row fills the terminal to its right edge (cols == its length), proving it was
    // CLIPPED and hard-wrapped — that is what licenses stitching the indented continuation onto it.
    const term = {cols: lines[0].translateToString(true).length, buffer: {active: {getLine: index => lines[index] || null}}};

    const full = 'https://github.com/ai-dynamo/frontend-crates/actions/runs/26919558600/job';
    // Query from the FIRST row.
    const firstRow = api.terminalWrappedLineLinks(term, 1);
    assert.equal(firstRow.length, 1, 'the hard-wrapped URL is one link when hovering row 1');
    assert.equal(firstRow[0].text, full, 'the link text is the full stitched URL');
    assert.equal(firstRow[0].range.start.y, 1);
    assert.equal(firstRow[0].range.start.x, 1);
    assert.equal(firstRow[0].range.end.y, 2, 'the underline extends onto the continuation row');
    // continuation '919558600/job' is 13 chars after a 4-space indent → last char at column 4 + 13 = 17.
    assert.equal(firstRow[0].range.end.x, 17, 'the continuation underline lands at its REAL (indented) columns');

    // Query from the CONTINUATION row — same link (backward sweep finds the URL start).
    const contRow = api.terminalWrappedLineLinks(term, 2);
    assert.equal(contRow.length, 1, 'the link is also active when hovering the continuation row');
    assert.equal(contRow[0].text, full);
    assert.equal(contRow[0].range.start.y, 1);
    assert.equal(contRow[0].range.end.y, 2);

    // A plain prompt row below is NOT part of the link.
    const promptRow = api.terminalWrappedLineLinks(term, 3);
    assert.equal(promptRow.length, 0, 'the prompt row after the URL is not merged into the link');
  });

  // Some TUIs hard-wrap long URLs as separate, flush-left rows at width-1 to avoid xterm auto-wrap.
  // The shared reference detector still needs to stitch those rows into one URL for the context menu.
  test('t@7036', () => {
    const api = loadYolomux();
    const linkProviderSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
    const providerStart = linkProviderSource.indexOf('function installTerminalLinkProvider');
    const providerEnd = linkProviderSource.indexOf('function terminalCellDimensions', providerStart);
    const providerSource = linkProviderSource.slice(providerStart, providerEnd);
    assert.ok(/registerLinkProvider\(\{[\s\S]*provideLinks: \(y, callback\) => \{[\s\S]*void y;[\s\S]*callback\(\[\]\)/.test(linkProviderSource), 'xterm left-click links are disabled; terminal references are context-menu only');
    assert.equal(providerSource.includes('window.open'), false, 'xterm link provider must not open browser tabs from left-click activation');
    const first = 'https://claude.com/cai/oauth/authorize?client_id=abc123&scope=org%3Acreate_a';
    const continuations = [
      'pi_key+user%3Aprofile+user%3Ainference&redirect_uri=http%3A%2F%2FlocalhostABCDEF',
      '%3A54545%2Fcallback&code_challenge=GSappE0',
    ];
    const full = `${first}${continuations.join('')}`;
    for (const cols of [first.length, first.length + 1]) {
      const lines = [
        terminalLine(first, false),
        terminalLine(continuations[0], false),
        terminalLine(continuations[1], false),
        terminalLine('$ ', false),
      ];
      const term = {cols, buffer: {active: {getLine: index => lines[index] || null}}};
      for (const y of [1, 2, 3]) {
        const links = api.terminalWrappedLineLinks(term, y);
        assert.equal(links.length, 1, `zero-indent width ${cols} row ${y} resolves one stitched URL`);
        assert.equal(links[0].text, full, `zero-indent width ${cols} row ${y} returns the full URL; got ${links[0].text}`);
        assert.deepStrictEqual(canonical(links[0].range), {
          start: {x: 1, y: 1},
          end: {x: continuations[1].length, y: 3},
        }, `zero-indent width ${cols} row ${y} underlines the full 3-row URL`);
      }
      assert.equal(api.terminalWrappedLineLinks(term, 4).length, 0, `zero-indent width ${cols} stops before the prompt row`);
    }
  });

  test('t@7059', () => {
    const api = loadYolomux();
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/yolomux.dev3'}});
    const lines = [
      terminalLine('• Documented it in docs/specs/SHARE_TEST_INVENTORY.md:123'),
      terminalLine('Open https://example.com/guide here'),
    ];
    const term = {cols: 80, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};
    const refs = api.terminalWrappedLineReferences(term, 1);
    const fileRef = refs.find(ref => ref.type === 'file');
    assert.deepStrictEqual(canonical({
      text: fileRef?.text,
      path: fileRef?.path,
      line: fileRef?.line,
      range: fileRef?.range,
    }), {
      text: 'docs/specs/SHARE_TEST_INVENTORY.md:123',
      path: 'docs/specs/SHARE_TEST_INVENTORY.md',
      line: 123,
      range: {start: {x: 20, y: 1}, end: {x: 57, y: 1}},
    }, 'terminal output detects relative file:line references as context-menu references');
    assert.equal(api.terminalFileReferenceAbsolutePath('1', fileRef), '/home/test/yolomux.dev3/docs/specs/SHARE_TEST_INVENTORY.md', 'relative terminal file refs resolve against the active pane cwd');
    assert.equal(api.terminalWrappedLineLinks(term, 1).some(ref => ref.type === 'file'), false, 'file references are not xterm left-click links');
    assert.equal(api.terminalReferenceAtPosition(term, {x: 32, y: 1})?.text, 'docs/specs/SHARE_TEST_INVENTORY.md:123', 'right-click hit-testing finds the file ref under the cursor');
    const urlRef = api.terminalReferenceAtPosition(term, {x: 8, y: 2});
    assert.equal(urlRef.type, 'url', 'right-click hit-testing still finds URLs');
    assert.equal(urlRef.href, 'https://example.com/guide');
    assert.equal(typeof urlRef.activate, 'undefined', 'URL references have no left-click activation handler');

    const container = api.testElementForId('terminal-pane-1');
    container.rect = {left: 0, top: 0, width: 800, height: 200, right: 800, bottom: 200};
    term._core = {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}};
    assert.deepStrictEqual(canonical(api.terminalPositionFromClientPoint(term, container, 315, 10)), {x: 32, y: 1}, 'client point maps to the terminal cell used by context-menu hit-testing');
  });

  // (no false merge): a fresh URL on the next flush-left row is its own link, not a continuation.
  test('t@7047', () => {
    const api = loadYolomux();
    const first = 'https://example.com/first';
    const second = 'https://example.com/second';
    const lines = [
      terminalLine(first, false),
      terminalLine(second, false),
    ];
    const term = {cols: first.length + 1, buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row1.length, 1, 'fresh-next-url row 1 has one link');
    assert.equal(row1[0].text, first, 'fresh-next-url row 1 stays separate');
    assert.equal(row1[0].range.end.y, 1, 'fresh-next-url row 1 underline does not continue');
    assert.equal(row2.length, 1, 'fresh-next-url row 2 has one link');
    assert.equal(row2[0].text, second, 'fresh-next-url row 2 stays separate');
    assert.equal(row2[0].range.start.y, 2, 'fresh-next-url row 2 starts on row 2');
  });

  // (no false merge): even an unterminated URL-looking row cannot absorb a flush-left continuation when
  // it did not reach the terminal edge.
  test('t@7052', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('https://example.com/abc', false),
      terminalLine('def', false),
    ];
    const term = {cols: 80, buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row1.length, 1, 'short-edge row 1 has its own link');
    assert.equal(row1[0].text, 'https://example.com/abc', 'short-edge row 1 does not absorb row 2');
    assert.equal(row1[0].range.end.y, 1, 'short-edge row 1 underline stays on row 1');
    assert.equal(row2.length, 0, 'short-edge row 2 has no standalone URL');
  });

  // (no false JOIN): a COMPLETE url at end-of-line that ends well short of the terminal's
  // right edge was NOT clipped, so the indented next row must stay independent — earlier this merged into
  // one bogus link `https://example.comnext step`.
  test('t@7057', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('See https://example.com'),  // complete URL, ends at col 23 of an 80-col terminal
      terminalLine('    next step', false),      // indented prose — NOT a clipped URL continuation
    ];
    const term = {cols: 80, buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    assert.equal(row1.length, 1, 'C1: a complete URL at EOL links only itself');
    assert.equal(row1[0].text, 'https://example.com', 'C1: link text is the complete URL, not joined with the next row');
    assert.equal(row1[0].range.end.y, 1, 'C1: the underline stays on the URL row (no false continuation onto row 2)');
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row2.length, 0, 'C1: the indented prose continuation is not a link');
  });

  // (no false merge): an indented line under a row that ends in PROSE (not an unterminated URL)
  // is left alone — only a url token that runs off the right edge gets a continuation stitched on.
  test('t@7074', () => {
    const api = loadYolomux();
    const lines = [
      terminalLine('Here are the steps to run:'),  // ends in prose, no trailing URL
      terminalLine('    https://example.com/guide', false),
    ];
    const term = {buffer: {active: {getLine: index => lines[index] || null}}};
    const row1 = api.terminalWrappedLineLinks(term, 1);
    assert.equal(row1.length, 0, 'a prose line is not merged with the indented URL below it');
    const row2 = api.terminalWrappedLineLinks(term, 2);
    assert.equal(row2.length, 1, 'the indented URL on its own row still links');
    assert.equal(row2[0].text, 'https://example.com/guide');
    // It links at its own indented columns (4-space indent → starts at column 5).
    assert.equal(row2[0].range.start.x, 5, 'a standalone indented URL underlines at its real column');
    assert.equal(row2[0].range.start.y, 2);
  });

  // watched-PR ref normalization (client mirror of the backend parse_pull_request_ref).
  test('t@7092', () => {
    const api = loadYolomux();
    assert.equal(api.normalizeWatchedPrRef('ai-dynamo/frontend-crates#18'), 'ai-dynamo/frontend-crates#18');
    assert.equal(api.normalizeWatchedPrRef('ai-dynamo/frontend-crates/18'), 'ai-dynamo/frontend-crates#18');
    assert.equal(api.normalizeWatchedPrRef('https://github.com/ai-dynamo/frontend-crates/pull/18'), 'ai-dynamo/frontend-crates#18');
    assert.equal(api.normalizeWatchedPrRef('https://github.com/owner/repo/pull/7/files'), 'owner/repo#7');
    assert.equal(api.normalizeWatchedPrRef('  owner/repo#7  '), 'owner/repo#7');
    assert.equal(api.normalizeWatchedPrRef('https://gitlab.com/owner/repo/pull/7'), '', 'non-github URL is rejected');
    assert.equal(api.normalizeWatchedPrRef('owner/repo'), '', 'a repo without a PR number is rejected');
    assert.equal(api.normalizeWatchedPrRef('owner/repo#0'), '', 'PR #0 is rejected');
    assert.equal(api.normalizeWatchedPrRef('not a ref'), '');
    assert.equal(api.normalizeWatchedPrRef('https://github.com/owner/repo/issues/3'), '', 'an issue URL is not a PR');
  });

  // watched-PR status snapshot + the pure transition detector (merge / CI→failing / review).
  test('t@7107', () => {
    const api = loadYolomux();
    const open = {state: 'open', checks: {state: 'passing'}, review_decision: 'REVIEW_REQUIRED'};
    assert.deepStrictEqual(canonical(api.watchedPrStatusSnapshot(open)), {merged: false, ci: 'passing', review: 'REVIEW_REQUIRED'});
    assert.equal(api.watchedPrStatusSnapshot({merged: true}).merged, true, 'merged flag → merged snapshot');
    assert.equal(api.watchedPrStatusSnapshot({status_label: 'merged'}).merged, true, 'merged status_label → merged snapshot');
    // First sighting (no prev) records a baseline → no transition (avoids a load-time storm).
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys(null, api.watchedPrStatusSnapshot(open))), []);
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: ''}, {merged: true, ci: 'passing', review: ''})), ['pr-merged'], '→ merged fires pr-merged');
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: ''}, {merged: false, ci: 'failing', review: ''})), ['pr-ci-failing'], 'CI → failing fires pr-ci-failing');
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'failing', review: ''}, {merged: false, ci: 'passing', review: ''})), [], 'CI failing → passing is not a pr-ci-failing transition');
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys({merged: false, ci: 'passing', review: 'REVIEW_REQUIRED'}, {merged: false, ci: 'passing', review: 'APPROVED'})), ['pr-review'], 'a review-decision change fires pr-review');
    const same = api.watchedPrStatusSnapshot(open);
    assert.deepStrictEqual(canonical(api.watchedPrTransitionKeys(same, same)), [], 'an unchanged snapshot fires nothing');
  });

  // notify_transitions gates the new PR keys — they are opt-in (NOT in the default allowlist).
  test('t@7124', () => {
    const api = loadYolomux();
    assert.equal(api.shouldNotifyTransitionKey('needs-input'), true, 'a default session-state key still notifies');
    assert.equal(api.shouldNotifyTransitionKey('pr-merged'), false, 'pr-merged is opt-in, off by default');
  });

  // watched PRs have an initial fetch, SSE updates, container, and transition notifications.
  test('t@7131', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(source.includes("resetRuntimeInterval('watched-prs', refreshWatchedPrs"), false, 'watched PRs no longer run a recurring browser poll');
    assert.ok(source.includes("apiFetchJson('/api/watched-prs')"), 'refreshWatchedPrs keeps the boot/manual watched-PR endpoint fetch');
    assert.ok(source.includes('id="info-watched"'), 'YO!info renders a watched-PRs container');
    assert.ok(source.includes('notifyWatchedPrTransitions(watchedPrsData.watched_prs)'), 'incoming snapshots diff statuses to fire transition notifications');
  });

  // Dev-velocity #1b: in --dev mode the page subscribes to /api/dev-reload and reloads on bundle change.
  test('t@7140', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('const devMode = bootstrap.dev === true'), 'the client reads the dev flag from the bootstrap');
    assert.ok(source.includes("new EventSource('/api/dev-reload')"), 'dev mode subscribes to the dev-reload SSE channel');
    assert.ok(/addEventListener\('reload',[\s\S]{0,120}location\.reload\(\)/.test(source), 'a reload event reloads the page');
    assert.ok(source.includes('installDevAutoReload()'), 'the dev auto-reload is installed at boot');
  });

  // browser clients subscribe to server push events for the expensive live datasets.
  test('t@7149', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("new EventSource('/api/client-events')"), 'client subscribes to the general server event stream');
    assert.ok(source.includes("installRuntimeIntervals();") && source.includes("installClientEventStream();"), 'SSE is installed alongside the remaining local ping/log timers');
    assert.equal(source.includes('function clientPushSuppressesPolling()'), false, 'expensive client polling gate is removed');
    assert.equal(source.includes('refreshTranscriptsFromRuntime'), false, 'metadata fallback poll wrapper is removed');
    assert.equal(source.includes('refreshWatchedFilesystemFromRuntime'), false, 'filesystem fallback poll wrapper is removed');
    assert.equal(source.includes('refreshSettingsFromRuntime'), false, 'settings fallback poll wrapper is removed');
    assert.ok(source.includes('syncServerWatchRoots({renew: true})'), 'connected push mode renews watched roots without polling the filesystem');
    assert.ok(source.includes("apiFetch('/api/watch/roots'"), 'client registers watched roots for server-side SSE polling');
    assert.ok(source.includes('function clientServerWatchRoots()'), 'client derives watched directory roots from Finder/session-file state');
    assert.ok(/function visibleFileEditorWatchFiles\(\)[\s\S]*?activePaneItems\(\)/.test(source), 'client reports active visible editor files separately from directory roots');
    assert.ok(/function backgroundFileEditorWatchFiles\(\)[\s\S]*?paneItems\(\)[\s\S]*?!visible\.has\(path\)/.test(source), 'client reports background editor files separately from active visible editor files');
    assert.ok(source.includes('files: visibleFileEditorWatchFiles()'), 'watch state includes visible editor file paths for the fast files_changed stream');
    assert.ok(source.includes('background_files: backgroundFileEditorWatchFiles()'), 'watch state includes background editor file paths for the slower files_changed stream');
    assert.ok(source.includes("['settings_changed', 'auto_approve_changed', 'tmux_signals_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready', 'update_available', 'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta']"), 'client listens for the expected push event types');
    assert.ok(/addEventListener\('ready',[\s\S]{0,260}refreshAutoStatuses\(\)\.catch/.test(source), 'client-events ready re-fetches auto status so stale YO markers are backfilled after reconnect');
    assert.ok(/function installReconnectResyncHandlers\(\)[\s\S]*document\.addEventListener\('visibilitychange'[\s\S]*document\.visibilityState === 'visible'[\s\S]*scheduleReconnectResync\('visible'\)[\s\S]*window\.addEventListener\('online'[\s\S]*scheduleReconnectResync\('online'\)/.test(source), 'page wake and network restore schedule a shared refreshAll resync');
    assert.ok(/function scheduleReconnectResync\(reason = ''\)[\s\S]*setTimeout\(\(\) => \{[\s\S]*refreshAll\(\)/.test(source), 'wake/network reconnect resync is debounced before refreshAll');
    const runtimeSrc = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    assert.ok(runtimeSrc.includes("resetRuntimeInterval('auto-approve', () => {\n    if (clientEventsConnected === true) return null;\n    return refreshAutoStatuses();\n  }, autoApproveDisconnectedPollMs);"), 'auto-approve fallback poll only runs while client-events is disconnected');
    assert.ok(/if \(type === 'settings_changed'\)[\s\S]{0,220}applySettingsPayload\(payload\.data, \{force: true\}\)/.test(source), 'settings_changed applies direct payloads without polling settings again');
    assert.ok(/if \(type === 'auto_approve_changed'\)[\s\S]{0,120}applyAutoApprovePayload\(payload\.data\)/.test(source), 'auto_approve_changed applies direct payloads');
    assert.ok(/if \(type === 'tmux_signals_changed'\)[\s\S]{0,120}applyTmuxSignalsPayload\(payload\)/.test(source), 'tmux_signals_changed applies direct payloads');
    assert.ok(/if \(type === 'watched_prs_changed'\)[\s\S]{0,120}applyWatchedPrsPayload\(payload\.data\)/.test(source), 'watched_prs_changed applies direct payloads');
    assert.ok(/if \(type === 'transcripts_changed'\)[\s\S]{0,220}applyTranscriptsPayload\(payload\.data, \{refreshAuto: false, refreshContext: false, refreshActivity: false\}\)/.test(source), 'transcripts_changed applies direct metadata payloads');
    assert.ok(/if \(type === 'context_items_ready'\)[\s\S]{0,160}applyContextItemsPayloadFromPush\(payload\.data/.test(source), 'context_items_ready applies direct context payloads');
    assert.ok(/if \(type === 'activity_summary_ready'\)[\s\S]{0,120}applyActivitySummaryPayloadFromPush\(payload\.data\)/.test(source), 'activity_summary_ready applies direct summary payloads');
    assert.ok(/if \(type === 'yoagent_skills_changed'\)[\s\S]{0,160}refreshActivitySummary\(\{force: true/.test(source), 'yoagent_skills_changed refreshes YO!agent context');
    assert.ok(/if \(type === 'yoagent_jobs_changed'\)[\s\S]*loadYoagentJobs\(\{force: true, silent: true, render: infoPanelSubTab === 'yoagent'[\s\S]*maybeNotifyYoagentJob\(payload\.notification/.test(source), 'yoagent_jobs_changed refreshes jobs and can notify from server-fired jobs');
    assert.ok(/if \(type === 'session_files_ready'\)[\s\S]{0,180}applySessionFilesPayloadFromPush\(payload\.data, payload\.request/.test(source), 'session_files_ready applies direct session-files payloads');
    assert.equal(source.includes('session_files_changed'), false, 'stale session_files_changed refetch event path is removed');
    assert.ok(/if \(type === 'files_changed'\)[\s\S]{0,180}refreshOpenFilesFromPush\(payload\)/.test(source), 'files_changed refreshes visible editor files without waiting for directory payloads');
    const filePushHelper = source.slice(source.indexOf('async function refreshOpenFilesFromPush'), source.indexOf('async function refreshFileExplorerFromPush'));
    assert.equal(filePushHelper.includes('fetchDirectory'), false, 'files_changed uses the server file signature directly, not a parent-directory listing');
    assert.equal(filePushHelper.includes('refreshOpenFilesIfChanged'), false, 'files_changed does not route through the directory-backed polling helper');
    assert.equal(source.includes('function scheduleSessionFilesPushRefresh()'), false, 'session-files push no longer triggers a client refetch helper');
    const watchRootsHelper = source.slice(source.indexOf('function clientServerWatchRoots()'), source.indexOf('function clientServerWatchState()'));
    assert.equal(watchRootsHelper.includes('openFiles.keys()'), false, 'open editor file dirs are not folded into the slower directory watch roots');
    assert.ok(/function applyLayoutSlots[\s\S]*?syncServerWatchRoots\(\)/.test(source), 'layout/tab changes immediately resync the server watch state');
    const fsPushHelper = source.slice(source.indexOf('async function refreshFileExplorerFromPush'), source.indexOf('function expandUserPath'));
    assert.equal(fsPushHelper.includes('fetchSessionFiles'), false, 'fs_changed refreshes Finder/open-file state without also fetching session-files');
    assert.ok(/if \(type === 'fs_changed'\)[\s\S]{0,180}refreshFileExplorerFromPush\(payload\)/.test(source), 'fs_changed refreshes Finder/open-file state through the shared push helper');
    assert.ok(source.includes('function clientServerWatchState()'), 'client reports rich watched state, not only filesystem roots');
    assert.ok(source.includes('context_items: activeSessions'), 'watch state includes active transcript context previews');
    assert.ok(source.includes('state.session_files = clientSessionFilesWatchRequests()'), 'watch state includes the current session-files request');
    assert.ok(source.includes("recordJsDebugEvent('sse'"), 'SSE events are captured in JS Debug');
  });

  test('t@7156', () => {
    const timing = fs.readFileSync('static_src/js/yolomux/02_timing.js', 'utf8');
    const runtime = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    const terminal = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    const layout = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
    const actions = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    const shareReplay = fs.readFileSync('static_src/js/yolomux/97_share_replay.js', 'utf8');
    assert.ok(/const uiDelayMs = Object\.freeze\(\{[\s\S]*serverWatchRenew:\s*60000[\s\S]*tmuxWindowReadback:\s*120[\s\S]*tmuxWindowReadbackRetry:\s*80[\s\S]*terminalRefreshAfterTabSelect:\s*120[\s\S]*fileQuickOpenDebounce:\s*160[\s\S]*fileExplorerTypeaheadClear:\s*700[\s\S]*shareGeometryDigestPublish:\s*2000/.test(timing), 'RA7: remaining frontend timing literals are owned by uiDelayMs');
    assert.ok(runtime.includes("resetRuntimeInterval('server-watch-renew', renewServerWatchRootsFromRuntime, serverWatchRenewMs);"), 'RA7: server watch renewal uses the shared timing owner');
    assert.ok(terminal.includes('const tmuxWindowReadbackDelayMs = tmuxWindowReadbackMs;') && terminal.includes('const tmuxWindowReadbackRetryDelayMs = tmuxWindowReadbackRetryMs;'), 'RA7: tmux readback delays come from the shared timing owner');
    assert.ok(terminal.includes('setTimeout(() => refreshTerminal(session), terminalRefreshAfterTabSelectMs);'), 'RA7: terminal refresh delay uses the shared timing owner');
    assert.ok(layout.includes('fileQuickOpenDebounce = setTimeout(run, fileQuickOpenDebounceMs);'), 'RA7: quick-open debounce uses the shared timing owner');
    assert.ok(actions.includes("setTimeout(() => { fileExplorerTypeaheadBuffer = ''; }, fileExplorerTypeaheadClearMs);"), 'RA7: Finder typeahead clear delay uses the shared timing owner');
    assert.ok(shareReplay.includes('shareGeometryDigestTimer = setInterval(publishShareGeometryDigest, shareGeometryDigestPublishMs);'), 'RA7: share geometry digest loop uses the shared timing owner');
    assert.equal(/server-watch-renew'[\s\S]{0,120}60000/.test(runtime), false, 'RA7: server-watch-renew no longer has an inline minute literal');
    assert.equal(/setTimeout\(run,\s*160\)/.test(layout), false, 'RA7: quick-open debounce no longer has an inline delay');
    assert.equal(/setInterval\(publishShareGeometryDigest,\s*2000\)/.test(shareReplay), false, 'RA7: share geometry digest loop no longer has an inline delay');
  });

  test('t@7185-terminal-resize-recovery-and-dispose-guards', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function scheduleRemoteResize\(session[\s\S]*?!terminalCanPublishRemoteSize\(\)[\s\S]*item\.remoteResizePending = true/.test(source), 'hidden-tab resize skips are marked pending instead of silently disappearing');
    assert.ok(/function forceRemoteResize\(session\)[\s\S]*sendRemoteResize\(session\)/.test(source), 'forced remote resize bypasses unchanged-fit dedupe by sending current terminal dims');
    assert.ok(/function resyncVisibleTerminalRemoteSizes\(reason = ''\)[\s\S]*scheduleFit\(session\)[\s\S]*forceRemoteResize\(session\)/.test(source), 'page-visible and online recovery force-publish current terminal geometry');
    assert.ok(/function refreshAll\(\)[\s\S]*resyncVisibleTerminalRemoteSizes\('refresh'\)[\s\S]*refreshTranscripts\(\{force: true\}\)/.test(source), 'manual refresh resizes visible tmux panes before continuing existing refresh work');
    assert.ok(/document\.addEventListener\('visibilitychange'[\s\S]*resyncVisibleTerminalRemoteSizes\('visible'\)/.test(source), 'visibility return resends terminal geometry');
    assert.ok(/window\.addEventListener\('online'[\s\S]*resyncVisibleTerminalRemoteSizes\('online'\)/.test(source), 'network return resends terminal geometry');
    assert.ok(/function closeTerminalItem\(session, item\)[\s\S]*cancelAnimationFrame\(item\.fitFrame\)[\s\S]*clearTimeout\(item\.fitTimer\)[\s\S]*item\.fitFrame = 0[\s\S]*item\.fitTimer = 0/.test(source), 'terminal teardown cancels pending fit callbacks');
    assert.ok(/socket\.onmessage = event => \{[\s\S]*terminals\.get\(session\) !== item[\s\S]*try \{[\s\S]*item\.term\.write/.test(source), 'late websocket frames are ignored after terminal item replacement/dispose');
  });

  // Finder symlink badge — the row toggles is-symlink/symlink-broken, shows a name→target
  // title, and the CSS overlays an arrow badge (red + struck-through for broken).
  test('t@7192', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(source.includes('row.className = `file-tree-row kind-${entry.kind}`'), false, 'Finder row refresh does not drop and re-add symlink/indexed classes');
    assert.ok(source.includes('function buildFileTreeRowState('), 'RA4: row render state has a named builder');
    assert.ok(source.includes('function applyFileTreeRowDataset('), 'RA4: row dataset/class work has a named applier');
    assert.ok(source.includes('function bindFinderRowHandlers('), 'RA4: Finder handlers have a named binder');
    assert.ok(source.includes('function bindDifferRowData('), 'RA4: Differ row data has a named binder');
    assert.ok(/function updateFileTreeRow\([\s\S]*buildFileTreeRowState[\s\S]*applyFileTreeRowDataset[\s\S]*applyFileTreeRowDerivedState[\s\S]*bindDifferRowData[\s\S]*bindFinderRowHandlers/.test(source), 'RA4: updateFileTreeRow is the short dispatcher over the row helpers');
    assert.ok(source.includes('syncFileTreeRowKindClass(row, entry.kind)'), 'Finder row kind classes update through stable toggles');
    assert.ok(source.includes("row.classList.toggle('is-symlink', entry.is_symlink === true)"), 'rows flag symlinks');
    assert.ok(source.includes("row.classList.toggle('symlink-broken', entry.kind === 'symlink-broken')"), 'rows flag broken symlinks');
    assert.ok(/entry\.is_symlink === true && entry\.symlink_target[\s\S]{0,160}→ \$\{entry\.symlink_target\}/.test(source), 'a symlink row title shows name → target');
    // The target renders INLINE in the row name ("name → target"), rel or abs as stored.
    const api = loadYolomux();
    const linkFile = api.fileTreeDisplayParts('/repo/link', {kind: 'file', name: 'link', is_symlink: true, symlink_target: '../real/path.txt'});
    assert.equal(linkFile.text, 'link → ../real/path.txt', 'inline text shows name → target');
    assert.ok(linkFile.html.includes('file-tree-symlink-target') && linkFile.html.includes('→ ../real/path.txt'), 'inline target is its own dimmed span');
    const linkDir = api.fileTreeDisplayParts('/repo/ld', {kind: 'dir', name: 'ld', is_symlink: true, symlink_target: '/abs/target'});
    assert.ok(linkDir.text.includes('ld → /abs/target'), 'a symlinked dir shows its absolute target inline');
    const plain = api.fileTreeDisplayParts('/repo/f.txt', {kind: 'file', name: 'f.txt'});
    assert.ok(!plain.text.includes('→'), 'a non-symlink has no target suffix');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.file-tree-row\.is-symlink > \.file-tree-icon::after\s*\{[^}]*content:\s*"↪"/.test(css), 'the symlink icon gets an arrow-badge overlay');
    assert.ok(/\.file-tree-row\.symlink-broken[^{]*\.file-tree-icon::after\s*\{[^}]*color:\s*var\(--bad\)/.test(css), 'a broken symlink badge is red (token)');
    assert.ok(/\.file-tree-row\.symlink-broken[^{]*\.file-tree-name\s*\{[^}]*line-through/.test(css), 'a broken symlink name is struck through');
  });

  test('t@7214', () => {
    const api = loadYolomux();
    const strip = tabStrip([
      tabElement('1', 100, 100),
      tabElement('2', 203, 100),
      tabElement('3', 306, 100),
    ]);

    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 110, clientY: 8}, '9')), {index: 0, x: 2, y: 0, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '9')), {index: 1, x: 103, y: 0, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 390, clientY: 8}, '9')), {index: 3, x: 304, y: 0, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(strip, {clientX: 225, clientY: 8}, '2')), {index: 1, x: 206, y: 0, height: 27, noop: true});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(tabStrip([]), {clientX: 180, clientY: 8}, '9')), {index: 0, x: 80, y: 0, height: 28, noop: false});
    assert.equal(api.paneTabDropIndex(strip, {clientX: 225, clientY: 8}, '9'), 1);

    const multiLineStrip = tabStrip([
      tabElement('1', 100, 100, 0),
      tabElement('2', 203, 100, 0),
      tabElement('3', 100, 100, 30),
      tabElement('4', 203, 100, 30),
    ]);
    multiLineStrip.rect = {left: 100, right: 406, top: 0, bottom: 58, width: 306, height: 58};
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 110, clientY: 38}, '9')), {index: 2, x: 2, y: 30, height: 27, noop: false});
    assert.deepStrictEqual(canonical(api.paneTabDropPlacement(multiLineStrip, {clientX: 225, clientY: 38}, '9')), {index: 3, x: 103, y: 30, height: 27, noop: false});
  });

  test('t@7240', () => {
    // View -> Theme is a submenu of discrete System/Dark/Light one-click items.
    const api = loadYolomux('', ['1']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const themeSubmenu = api.appMenuTree()
      .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
      .find(item => item.type === 'submenu' && item.label === 'Theme');
    assert.ok(themeSubmenu, '#24: View has a Theme submenu');
    const themeLabels = themeSubmenu.items.filter(item => item.type === 'command').map(item => item.label);
    assert.deepStrictEqual([...themeLabels], ['System', 'Dark', 'Light'], '#24: Theme submenu offers System/Dark/Light as discrete one-click items');
    assert.ok(themeSubmenu.items.some(item => item.label === 'Dark' && item.checked !== undefined), '#24: Theme items carry a checked state for the current mode');
    // The active marker tracks the LIVE theme (regression: normalizeGlobalThemeMode() with no arg used to
    // always return 'dark', so Dark stayed marked even after switching to Light).
    const themeCheckedFor = mode => {
      api.setGlobalThemeModeForTest(mode);
      return api.appMenuTree()
        .flatMap(menu => Array.isArray(menu.items) ? menu.items : [])
        .find(item => item.type === 'submenu' && item.label === 'Theme')
        .items.filter(item => item.checked === true).map(item => item.label);
    };
    assert.deepStrictEqual([...themeCheckedFor('light')], ['Light'], 'theme marker: only Light is checked when light is the live mode');
    assert.deepStrictEqual([...themeCheckedFor('dark')], ['Dark'], 'theme marker: only Dark is checked when dark is the live mode');
    assert.deepStrictEqual([...themeCheckedFor('system')], ['System'], 'theme marker: only System is checked when system is the live mode');
    // setGlobalThemeMode rebuilds the menu bar so the marker updates immediately (not on the next poll).
    assert.ok(/function setGlobalThemeMode[\s\S]*?applyGlobalThemeMode\([^)]*\);\s*renderSessionButtons\(\)/.test(source), 'setGlobalThemeMode re-renders the menu bar so the active marker updates at once');
    // #258: picking a theme APPLIES it live (the menu used to only save the patch).
    // #258: the theme applies live (body.theme-* flips), now via the shared apply+save helper that both
    // the one-click Theme submenu and the View cycle delegate to.
    assert.ok(/function applyAndSaveGlobalTheme[\s\S]*?globalThemeMode = next;\s*applyGlobalThemeMode\(\{updateEditor: true, updateTerminals: true\}\)/.test(source), '#258: applyAndSaveGlobalTheme applies the theme live');
    assert.ok(/function setGlobalThemeMode\(mode\)\s*\{\s*return applyAndSaveGlobalTheme\(normalizeGlobalThemeMode\(mode\)\)/.test(source), '#258: setGlobalThemeMode delegates to the shared apply+save helper');
    assert.ok(/function cycleGlobalThemeSetting\(\)\s*\{\s*return applyAndSaveGlobalTheme\(nextGlobalThemeMode\(\)\)/.test(source), '#258: cycleGlobalThemeSetting delegates to the shared apply+save helper');
    // #261: the View menu no longer PINS the terminal palette — it just follows the app (follow-app stays).
    assert.equal(/function setGlobalThemeMode[\s\S]*?patch\.appearance\.terminal_theme/.test(source), false, '#261: setGlobalThemeMode no longer pins appearance.terminal_theme');
    assert.equal(/function cycleGlobalThemeSetting[\s\S]*?patch\.appearance\.terminal_theme/.test(source), false, '#261: cycleGlobalThemeSetting no longer pins appearance.terminal_theme');
    // Active-terminal cursor: the focused pane's terminal shows the configured cursor color.
    assert.ok(/const DEFAULT_CURSOR_COLOR\s*=\s*'yellow'/.test(source), 'active-terminal cursor: yellow remains the default cursor color');
    assert.ok(/const UI_COLOR_PRESETS\s*=\s*\{[\s\S]*yellow:\s*\{labelKey:\s*'pref\.appearance\.active_color\.yellow',\s*cursorLabelKey:\s*'pref\.appearance\.editor_cursor_color\.yellow',\s*cursor:\s*\{dark:\s*'#ffea00',\s*light:\s*'#9a6700'\}/.test(source), 'active-terminal cursor: bright yellow cursor color lives in the shared UI color parent with a light-mode variant');
    assert.ok(/function terminalThemeForSession[\s\S]*?session === focusedPanelItem \? \{\.\.\.theme, cursor: activeTerminalCursorColorForTheme\(theme\)\}/.test(source), 'active-terminal cursor: the focused session gets the configured cursor color, others keep theme default');
    assert.ok(/item\.term\.options\.theme = terminalThemeForSession\(session, theme\)/.test(source), 'active-terminal cursor: applyTerminalRuntimeSettings themes the active terminal with the configured cursor color');
    assert.ok(/theme: terminalThemeForSession\(session\)/.test(source), 'active-terminal cursor: a newly-created terminal uses terminalThemeForSession');
    assert.ok(/function updatePanelInactiveOverlays[\s\S]*?refreshActiveTerminalCursor\(\)/.test(source), 'active-terminal cursor: focus changes refresh the cursor color (refreshActiveTerminalCursor)');
  });

  test('t@7283', () => {
    // YO!agent composer redesign (mockup 044): a rounded input bar with the input on top and a control
    // row below — backend/model/effort selectors (wired to YO!agent settings) + subtle Clear + a circular send arrow.
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/class="yoagent-chat-controls"/.test(src), 'YO!agent composer has a control row');
    assert.ok(/function yoagentComposerControlsHtml/.test(src) && /kind: 'backend'/.test(src) && /kind: 'model'/.test(src) && /kind: 'effort'/.test(src), 'composer renders backend/model/effort selectors');
    assert.ok(/data-yoagent-setting-path/.test(src) && /saveSettingsPatch\(settingPatchForPath\(path, yoagentSetting\.value\)/.test(src), 'changing a composer selector writes the real YO!agent setting path through the shared patch helper');
    assert.ok(/class="yoagent-chat-send-icon"[\s\S]*?<path/.test(src), 'send button is a circular arrow icon (not a text "Ask" button)');
    assert.ok(/\.yoagent-chat-form\s*\{[^}]*border-radius:\s*14px/.test(css), 'composer is one rounded container');
    assert.ok(/\.yoagent-chat-form\s*\{[\s\S]*border:\s*1px solid var\(--link-soft\)/.test(css), 'YO!agent composer border reuses the YOU bubble color token');
    assert.ok(/\.yoagent-chat-send\s*\{[^}]*border-radius:\s*50%/.test(css), 'send button is circular');
    assert.ok(/\.yoagent-composer-pill,\s*\n\.yoagent-backend-pill\s*\{/.test(css), 'composer selectors are styled as compact pills');
    assert.ok(/\.yoagent-backend-pill-dot\s*\{[\s\S]*background:\s*var\(--agent-inactive-marker-bg\)[\s\S]*border:\s*2px solid var\(--yoagent-inactive-backend-dot-border\)[\s\S]*box-shadow:/.test(css), 'YO!agent inactive backend dot is a clear black circle with a token-owned visible border');
    assert.ok(/\.yoagent-composer-pill-backend:not\(:has\(select:disabled\)\) \.yoagent-backend-pill-dot\s*\{[\s\S]*background:\s*var\(--pr-status-passing\)[\s\S]*box-shadow:/.test(css), 'YO!agent usable backend dot switches to the green active status without reusing the current-window marker');
    assert.ok(/body\.theme-light \.yoagent-backend-pill-dot\s*\{[\s\S]*background:\s*var\(--agent-inactive-marker-bg\)[\s\S]*border-color:\s*var\(--agent-inactive-marker-border\)/.test(css), 'YO!agent inactive backend dot keeps its black fill with an explicit light-mode border pair');
    assert.ok(/\.yoagent-recent-agents-list\s*\{[^}]*display:\s*grid/.test(css), 'YO!agent recent agents render as a compact bullet list inside the chat history');
    assert.ok(/function yoagentRecentAgentPathText\(agent, signal = yoagentRecentAgentSignal\(agent\)\)[\s\S]*agent\?\.recent_paths[\s\S]*compactHomePath/.test(src), 'YO!agent recent agents display backend recent_paths with compact home paths');
    assert.ok(/function yoagentRecentAgentsHtml\(payload = yoagentStartupActivityPayload\(\)\)[\s\S]*payload\?\.agents[\s\S]*<ul class="yoagent-recent-agents-list">/.test(src), 'YO!agent recent agents render from the startup activity-summary snapshot as a list');
    assert.ok(/function yoagentRecentAgentsMessageHtml\(\)[\s\S]*yoagentRecentAgentsHtml\(payload\)[\s\S]*yoagent-message assistant yoagent-recent-agents-message/.test(src), 'YO!agent recent agents are wrapped as an assistant response for the startup one-shot');
    assert.ok(/let yoagentStreamingMessages = new Map\(\)/.test(src), 'YO!agent keeps transient streaming assistant messages in chat state');
    assert.ok(/function yoagentStreamingMessagesList\(\)[\s\S]*yoagentStreamingMessages\.values/.test(src), 'YO!agent exposes streamed assistant deltas as renderable messages');
    assert.ok(/function applyYoagentStreamPayload\(payload = \{\}\)[\s\S]*hidden_thinking_removed[\s\S]*raw model thinking was hidden/.test(src), 'YO!agent stream events expose safe thinking diagnostics without raw chain-of-thought');
    assert.ok(/function applyYoagentStreamPayload\(payload = \{\}\)[\s\S]*auxiliary_lines[\s\S]*auxiliaryPreview[\s\S]*auxiliaryText[\s\S]*auxiliaryTruncated/.test(src), 'YO!agent stream payloads keep auxiliary thinking/tool lines separate from assistant content');
    assert.ok(src.includes("'yoagent_stream_delta'"), 'YO!agent subscribes to streaming SSE events');
    assert.ok(/function yoagentChatMessagesHtml\(\)[\s\S]*const startupInfo = yoagentStartupInfoVisible \? yoagentStartupInfoHtml\(\) : '';[\s\S]*return `\$\{messageHtml\}\$\{startupInfo\}`;/.test(src), 'YO!agent startup info is state-gated instead of always appended after messages');
    assert.ok(/function showYoagentStartupInfoOnce\(\)[\s\S]*captureYoagentStartupActivitySummarySnapshot\(\)[\s\S]*yoagentStartupInfoVisible = true/.test(src), 'YO!agent startup info freezes the activity snapshot when it is printed');
    assert.ok(/function showYoagentStartupInfoForLatestActivity\(\)[\s\S]*resetYoagentStartupActivitySummarySnapshot\(\)[\s\S]*yoagentStartupInfoShown = false[\s\S]*showYoagentStartupInfoOnce\(\)/.test(src), 'YO!agent can intentionally re-show the latest activity snapshot after clearing conversation');
    assert.ok(/function applyActivitySummaryPayloadFromPush\(payload = \{\}, options = \{\}\)[\s\S]*options\.refreshStartupSnapshot === true[\s\S]*captureYoagentStartupActivitySummarySnapshot\(\{replace: true\}\)/.test(src), 'activity-summary pushes are cache-only unless an explicit refresh requests a new startup snapshot');
    assert.ok(/async function prewarmYoagent\(options = \{\}\)[\s\S]*visible: shouldRequestStartupAnswer[\s\S]*applyYoagentConversationPayload\(payload\.conversation/.test(src), 'YO!agent prewarm asks for one visible startup LLM answer and applies the saved conversation');
    assert.ok(/async function clearYoagentConversation\(\)[\s\S]*yoagentPrewarmStarted = false[\s\S]*showYoagentStartupInfoForLatestActivity\(\)[\s\S]*refreshActivitySummary\(\{force: true, silent: true\}\)[\s\S]*showYoagentStartupInfoForLatestActivity\(\)/.test(src), 'Clear conversation resets prewarm and re-renders the refreshed latest activity snapshot');
    assert.equal(/function applyYoagentConversationPayload\(payload = \{\}\)[\s\S]*if \(messages\.length\) hideYoagentStartupInfo\(\)/.test(src), false, 'YO!agent real conversation payloads keep the startup Recent agents block');
    assert.ok(/function applyYoagentConversationPayload\(payload = \{\}\)[\s\S]*hasOwnProperty\.call\(payload, 'messages'\)[\s\S]*return false/.test(src), 'YO!agent ignores partial/missing conversation payloads instead of clearing visible history');
    assert.ok(/let yoagentPendingWaits = \[\]/.test(src), 'YO!agent keeps server-reported pending waits in chat state');
    assert.ok(/function applyYoagentConversationPayload\(payload = \{\}\)[\s\S]*yoagentPendingWaits = Array\.isArray\(payload\.pending_waits\)/.test(src), 'YO!agent conversation payload carries pending background waits');
    assert.ok(/function yoagentPendingWaitsHtml\(\)[\s\S]*tPlural\('yoagent\.waiting\.count'[\s\S]*yoagent-waiting-queue/.test(src), 'YO!agent renders a waiting queue for one or more background result waits');
    assert.ok(/function yoagentPendingWaitsHtml\(\)[\s\S]*sourceRegarding[\s\S]*targetRegarding[\s\S]*yoagent\.waiting\.handoff[\s\S]*yoagent\.waiting\.session/.test(src), 'YO!agent waiting rows distinguish handoff waits from direct session waits and include both regarding summaries');
    assert.ok(/data-yoagent-wait-clear/.test(src) && /async function clearYoagentPendingWait/.test(src), 'YO!agent pending waits expose a clear affordance through the existing wait store');
    assert.ok(/function applyYoagentJobsPayload\(payload = \{\}\)[\s\S]*yoagentJobs = Array\.isArray\(payload\.jobs\)/.test(src), 'YO!agent keeps server-reported jobs in chat state');
    assert.ok(/function yoagentJobsHtml\(\)[\s\S]*yoagent-jobs-list/.test(src), 'YO!agent renders queued jobs as a visible list');
    assert.ok(/data-yoagent-job-confirm/.test(src) && /data-yoagent-job-cancel/.test(src), 'YO!agent job rows expose confirm/cancel controls');
    assert.ok(/async function loadYoagentJobs\(options = \{\}\)[\s\S]*apiFetchJson\('\/api\/yoagent\/jobs'/.test(src), 'YO!agent hydrates jobs from the existing jobs API');
    assert.ok(/type === 'yoagent_jobs_changed'[\s\S]*loadYoagentJobs\(\{force: true, silent: true, render: infoPanelSubTab === 'yoagent'/.test(src), 'YO!agent job SSE refreshes the visible job list');
    assert.ok(/\[data-yoagent-job-confirm\][\s\S]*confirmYoagentJob/.test(src) && /\[data-yoagent-job-cancel\][\s\S]*cancelYoagentJob/.test(src), 'YO!agent job controls are delegated from the merged info panel');
    assert.ok(/function yoagentShouldScrollBottom\(options, scrollState\)[\s\S]*options\.scrollBottom === true[\s\S]*options\.scrollBottom === false[\s\S]*yoagentScrollbackLocked[\s\S]*scrollState\?\.nearBottom/.test(src), 'YO!agent chat auto-scrolls only when forced or already near the bottom and not manually scrollback-locked');
    assert.ok(/function yoagentChatScrollOwner\(node = document\.getElementById\('yoagent-content'\)\)\s*\{[\s\S]*return node\?\.querySelector\?\.\('\.yoagent-chat-history'\) \|\| node \|\| null;[\s\S]*function scrollYoagentChatToBottom/.test(src), 'YO!agent has one normal scroll owner with only the outer node as a fallback');
    assert.ok(/function scrollYoagentChatToBottom\(node = document\.getElementById\('yoagent-content'\)\)[\s\S]*const owner = yoagentChatScrollOwner\(node\);[\s\S]*owner\.scrollTop = owner\.scrollHeight[\s\S]*yoagentScrollbackLocked = false/.test(src), 'YO!agent bottom-scroll drives only the chosen scroll owner');
    assert.ok(/function yoagentChatScrollState\(node = document\.getElementById\('yoagent-content'\)\)[\s\S]*const owner = yoagentChatScrollOwner\(node\);[\s\S]*ownerTop: owner \? owner\.scrollTop : 0/.test(src), 'YO!agent scroll state stores only the chosen owner top');
    assert.equal(/yoagentChatScrollState[\s\S]{0,420}(nodeTop|panelTop|panelBody)/.test(src), false, 'YO!agent scroll state does not capture outer list or panel body scroll positions');
    assert.ok(/function restoreYoagentChatScrollState\(node, state\)[\s\S]*const owner = yoagentChatScrollOwner\(node\);[\s\S]*owner\.scrollTop = state\.ownerTop \|\| 0[\s\S]*yoagentScrollbackLocked = state\.nearBottom === false/.test(src), 'YO!agent restores only the chosen scroll owner and preserves the scrollback lock');
    assert.ok(/function installYoagentChatScrollTracker\(node = document\.getElementById\('yoagent-content'\)\)[\s\S]*const history = yoagentChatScrollOwner\(node\);[\s\S]*addEventListener\('scroll'[\s\S]*yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom\(history\)/.test(src), 'YO!agent chat records manual scrollback on the single scroll owner');
    assert.ok(src.includes("loadYoagentConversation({force: true, render: infoPanelSubTab === 'yoagent', scrollBottom: 'auto'})"), 'YO!agent background result pushes preserve manual scrollback unless the chat is already near bottom');
    assert.ok(/\.yoagent-transcript-path\s*\{[^}]*display:\s*flex[\s\S]*min-width:\s*0/.test(css), 'YO!agent transcript path row is compact and ellipsizes inside the chat panel');
    assert.ok(/\.yoagent-transcript-value\s*\{[^}]*text-overflow:\s*ellipsis/.test(css), 'YO!agent transcript path cannot overflow the chat panel');
    assert.ok(/\.yoagent-message\.assistant\s*\{[\s\S]*align-self:\s*flex-start[\s\S]*margin-inline-end:\s*28px[\s\S]*border-color:\s*var\(--active-control-border\)[\s\S]*background:\s*color-mix\(in srgb, var\(--active-control-soft-bg\)/.test(css), 'YO!agent assistant bubbles are left-indented and use the active theme accent');
    assert.ok(/\.yoagent-message\.assistant\.yoagent-agent-result\s*\{[\s\S]*border-inline-start-color:\s*var\(--accent-gold\)[\s\S]*border-inline-start-width:\s*6px/.test(css), 'YO!agent target-agent result bubbles have a stronger colored left rule');
    assert.ok(/function yoagentAgentResultParts\(text\)[\s\S]*heading[\s\S]*output/.test(src), 'YO!agent target-agent result parser splits the heading from the output');
    assert.ok(/\.yoagent-agent-result-body\s*\{[\s\S]*display:\s*grid[\s\S]*gap:\s*0/.test(css), 'YO!agent target-agent result body stacks heading and output without extra vertical gap');
    assert.ok(/\.yoagent-message\.assistant\.yoagent-agent-result \.yoagent-agent-result-output\s*\{[\s\S]*padding-inline-start:\s*14px[\s\S]*border-inline-start:\s*3px solid var\(--accent-gold\)/.test(css), 'YO!agent target-agent result output is indented behind a full-height left bar');
    assert.ok(/\.yoagent-message\.user\s*\{[\s\S]*align-self:\s*flex-end[\s\S]*margin-inline-start:\s*28px[\s\S]*border-color:\s*var\(--link-soft\)/.test(css), 'YO!agent user bubbles are right-indented with the secondary/link border color');
    assert.ok(/\.yoagent-message\s*\{[\s\S]*overflow:\s*visible[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent message bubbles are not vertical scroll containers that swallow wheel input');
    assert.ok(/\.yoagent-message-body\s*\{[\s\S]*overflow-x:\s*visible[\s\S]*overflow-y:\s*visible[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent message bodies leave vertical wheel scrolling to the chat history owner');
    assert.ok(/function yoagentTimestampText[\s\S]*second:\s*'2-digit'/.test(src), 'YO!agent chat timestamps include seconds');
    assert.ok(/function yoagentMessageLatencyHtml[\s\S]*yoagent-message-latency[\s\S]*yoagent\.responseLatency/.test(src), 'YO!agent assistant timestamps include a localized response-latency suffix');
    assert.ok(/function yoagentMessageDetailsHtml[\s\S]*data-yoagent-message-details-key/.test(src), 'YO!agent assistant diagnostics render as an expandable details block with a stable message key');
    assert.ok(/function yoagentAuxiliaryLineIsDiagnostic[\s\S]*usage:[\s\S]*response time/.test(src), 'YO!agent collapsed details filter diagnostics out of auxiliary previews');
    assert.ok(/function yoagentMessageDetailsHtml[\s\S]*yoagentThinkingDetailsPreview[\s\S]*yoagentDetailsPreviewHtml[\s\S]*yoagent-auxiliary-stream[\s\S]*yoagent-details-note/.test(src), 'YO!agent assistant diagnostics render active thinking preview, expanded stream, and truncation note');
    assert.ok(/function yoagentToolLineHtml[\s\S]*yoagent-tc-command/.test(src), 'YO!agent tool-call lines wrap executed commands in a dedicated command span');
    assert.ok(/t\('yoagent\.toolCall\.label'\)/.test(src), 'YO!agent tool-call details use the localized "tool call" label instead of TC');
    assert.ok(/\.yoagent-tc-command\s*\{[\s\S]*color:\s*var\(--code-function\)/.test(css), 'YO!agent tool-call command span has a distinct themed color');
    assert.ok(/function refreshYoagentSummaryRegions[\s\S]*const openDetails = yoagentOpenMessageDetailsState\(node\)[\s\S]*restoreYoagentOpenMessageDetailsState\(node, openDetails\)/.test(src), 'YO!agent summary refresh preserves expanded Details blocks');
    assert.ok(/function renderYoagentPanel[\s\S]*const openDetails = yoagentOpenMessageDetailsState\(node\)[\s\S]*node\.innerHTML = yoagentChatHtml\(\);[\s\S]*restoreYoagentOpenMessageDetailsState\(node, openDetails\)/.test(src), 'YO!agent full chat rerenders preserve expanded Details blocks');
    assert.ok(/\.yoagent-message-details pre\s*\{[\s\S]*max-height:\s*180px/.test(css), 'YO!agent diagnostics details stay bounded inside the message');
    assert.ok(/\.yoagent-message-details pre\s*\{[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent diagnostics details allow vertical wheel chaining at scroll edges');
    assert.ok(/\.yoagent-message-details summary::before\s*\{[\s\S]*border-inline-start:\s*6px solid currentColor/.test(css), 'YO!agent diagnostics summary renders an explicit disclosure triangle');
    assert.ok(/\.yoagent-message-details\[open\] summary::before\s*\{[\s\S]*transform:\s*rotate\(90deg\)/.test(css), 'YO!agent disclosure triangle rotates when details are open');
    assert.ok(/\.yoagent-details-preview\s*\{[\s\S]*max-height:\s*calc\(2 \* max/.test(css), 'YO!agent default collapsed auxiliary preview reserves at most two lines');
    assert.ok(/\.yoagent-details-preview\.yoagent-thinking-live-preview\s*\{[\s\S]*max-height:\s*calc\(5 \* max/.test(css), 'YO!agent live thinking collapsed preview reserves five visual lines while running');
    assert.ok(/\.yoagent-details-preview\s*\{[\s\S]*overflow:\s*clip/.test(css), 'YO!agent collapsed auxiliary preview clips without becoming a hidden scroll container');
    assert.ok(/\.yoagent-message-details pre\.yoagent-auxiliary-stream\s*\{[\s\S]*color:\s*color-mix/.test(css), 'YO!agent auxiliary stream is visually quieter than normal chat text');
    assert.ok(/body\.theme-light \.yoagent-message,[\s\S]*body\.theme-light \.yoagent-message-body,[\s\S]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode message bodies use readable light-mode text');
    assert.ok(/body\.theme-light \.yoagent-chat-input\s*\{[\s\S]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode composer input uses readable light-mode text');
    assert.ok(/\.yoagent-chat-history\s*\{[\s\S]*overflow-x:\s*hidden[\s\S]*overflow-y:\s*auto[\s\S]*scrollbar-gutter:\s*stable/.test(css), 'YO!agent chat history is the single normal vertical scrollbar with a stable gutter');
    assert.ok(/\.yoagent-chat-history\s*\{[\s\S]*--pane-scrollbar-current-thumb:\s*var\(--pane-scrollbar-thumb\)[\s\S]*--pane-scrollbar-current-track:\s*var\(--pane-scrollbar-track\)/.test(css), 'YO!agent history keeps the normal rail neutral during active-pane hover');
    assert.ok(/\.yoagent-chat-history::\-webkit-scrollbar-thumb:hover,[\s\S]*\.yoagent-chat-history::\-webkit-scrollbar-thumb:active\s*\{[\s\S]*background:\s*var\(--pane-scrollbar-thumb-active\)/.test(css), 'YO!agent history uses the bright thumb only for direct scrollbar hover or drag');
    assert.ok(/\.yoagent-waiting-queue\s*\{[\s\S]*border:\s*1px solid var\(--active-control-soft-border\)/.test(css), 'YO!agent pending waits render as a visible compact queue');
    assert.ok(/\.yoagent-jobs-list\s*\{[\s\S]*border:\s*1px solid var\(--line\)/.test(css), 'YO!agent jobs render as a visible compact queue');
    assert.ok(/\.yoagent-job-controls\s*\{[\s\S]*display:\s*flex/.test(css), 'YO!agent job controls are visible inline controls');
    const actionCardStart = src.indexOf('function yoagentActionCardHtml(action)');
    const actionCardEnd = src.indexOf('function yoagentIntroMessageText', actionCardStart);
    const actionCardBody = src.slice(actionCardStart, actionCardEnd);
    assert.ok(actionCardStart >= 0 && actionCardBody.includes('data-yoagent-action-card') && actionCardBody.includes('data-yoagent-action-send'), 'YO!agent action previews render as confirmed-send cards');
    assert.ok(actionCardBody.includes("t('yoagent.action.preview')") && actionCardBody.includes("t('yoagent.action.send')"), 'YO!agent action card labels are localized');
    assert.ok(src.includes("t('yoagent.statusActionSent'") && src.includes("t('yoagent.statusBackend'"), 'YO!agent action/backend status strings are localized');
    assert.ok(/\.yoagent-chat \.markdown-body pre[\s\S]*?border-radius:\s*8px/.test(css), 'YO!agent code blocks are soft rounded boxes');
    assert.ok(/\.yoagent-chat \.markdown-body pre,[\s\S]*\.yoagent-global \.markdown-body pre\s*\{[\s\S]*overflow-x:\s*auto[\s\S]*overflow-y:\s*auto[\s\S]*overscroll-behavior:\s*auto/.test(css), 'YO!agent code blocks keep horizontal scrolling and normal vertical wheel chaining');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body pre/.test(css), 'YO!agent code blocks get a light box + dark text in light mode');
    assert.ok(/--lt-code-block-bg:\s*#f3f4f6;[\s\S]*--lt-code-block-border:\s*#e4e7ec;[\s\S]*--lt-code-block-text:\s*#1f2328;/.test(css), 'R4: neutral light code-block values live in the shared lt token owner');
    assert.ok(/body\.theme-light \.yoagent-chat,[\s\S]*body\.theme-light \.yoagent-message\s*\{[\s\S]*background:\s*var\(--panel\);[\s\S]*border-color:\s*var\(--line\);/.test(css), 'R4: YO!agent light bubbles use shared panel and line tokens');
    assert.ok(/body\.theme-light \.yoagent-message-details pre\s*\{[\s\S]*background:\s*var\(--lt-code-block-bg\);[\s\S]*border-color:\s*var\(--lt-code-block-border\);/.test(css), 'R4: YO!agent details code blocks use shared neutral code-block tokens');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body pre,[\s\S]*body\.theme-light \.yoagent-global \.markdown-body pre\s*\{[\s\S]*background:\s*var\(--lt-code-block-bg\);[\s\S]*border-color:\s*var\(--lt-code-block-border\);[\s\S]*color:\s*var\(--lt-code-block-text\);/.test(css), 'R4: YO!agent markdown code blocks use shared neutral code-block tokens');
    assert.ok(/\.file-editor-theme-panel\.theme-vanilla\s*\{[\s\S]*background:\s*var\(--lt-editor-bg\);[\s\S]*border-color:\s*var\(--lt-line\);/.test(css), 'R4: editor vanilla swatch uses light editor tokens');
    assert.ok(/body\.theme-light \.command-palette-dialog,[\s\S]*body\.theme-light \.keyboard-shortcuts-dialog\s*\{[\s\S]*background:\s*var\(--panel\);[\s\S]*border-color:\s*var\(--line\);/.test(css), 'R4: command palette and shortcuts dialogs use shared light panel tokens');
    assert.ok(/body\.theme-light \.yoagent-message-body\.markdown-body,[\s\S]*?\.yoagent-global \.markdown-body\s*\{[^}]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode markdown bodies use dark app text instead of editor markdown colors');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body strong,[\s\S]*?\.yoagent-global \.markdown-body strong\s*\{[^}]*color:\s*var\(--lt-text\)/.test(css), 'YO!agent light-mode bold text is readable, not white-on-light');
    assert.ok(/body\.theme-light \.yoagent-chat \.markdown-body :not\(pre\) > code,[\s\S]*?\.yoagent-global \.markdown-body :not\(pre\) > code\s*\{[^}]*color:\s*#0f4c81/.test(css), 'YO!agent light-mode inline code uses a readable app-blue chip');
    // Rendered-markdown chat bodies drop pre-wrap so bullet lists are tightly spaced (the preserved
    // newlines between/inside the generated <ul><li> HTML were widening them).
    assert.ok(/\.yoagent-message-body\.markdown-body\s*\{[^}]*white-space:\s*normal/.test(css), 'rendered markdown chat bodies use white-space:normal so bullets are not widely spaced');
    // The "thinking" busy indicator uses the shared real-span moving ellipsis. Do not fork a second
    // pseudo-element or per-feature keyframe animation.
    assert.ok(/function movingEllipsisHtml\(className = ''\)[\s\S]*<span>\.<\/span><span>\.<\/span><span>\.<\/span>/.test(src), 'moving dots render as three real animated spans from one helper');
    assert.ok(src.includes("textWithMovingEllipsisHtml(t('yoagent.thinking'), 'yoagent-thinking-dots')"), 'YO!agent thinking uses the shared moving ellipsis helper');
    assert.ok(/\.moving-ellipsis span\s*\{[^}]*animation:\s*moving-ellipsis-dot/.test(css), 'moving dot spans animate directly');
    assert.ok(/\.moving-ellipsis span\s*\{[^}]*opacity:\s*0/.test(css), 'moving dots start hidden so the ellipsis visibly cycles');
    assert.ok(/\.moving-ellipsis span:nth-child\(2\)\s*\{[^}]*animation-delay:\s*0\.2s/.test(css), 'moving dot 2 is staggered');
    assert.ok(/\.moving-ellipsis span:nth-child\(3\)\s*\{[^}]*animation-delay:\s*0\.4s/.test(css), 'moving dot 3 is staggered');
    assert.equal((css.match(/@keyframes moving-ellipsis-dot/g) || []).length, 1, 'the moving-dot keyframes have one shared owner');
    assert.equal(/@keyframes (yoagent-thinking-dot|tabber-loading-dots)/.test(css), false, 'old per-feature moving-dot keyframes stay removed');
    assert.equal(/prefers-reduced-motion[^{]*\{[^}]*yoagent-thinking-dots/.test(css), false, 'thinking dots keep blinking even when reduced-motion CSS is active');
    // #YO!info scroll: the body pane (a grid item of the .panel grid) must keep min-width:0 so wide
    // content scrolls inside .info-list (overflow:auto) instead of blowing the column out past the
    // overflow:hidden panel (which silently clipped the right side — the user could not scroll right).
    assert.ok(/\.info-pane\s*\{[^}]*min-width:\s*0/.test(css), 'YO!info body pane keeps min-width:0 so wide content scrolls instead of being clipped');
    assert.ok(/\.info-list\s*\{[^}]*overflow:\s*auto/.test(css), 'YO!info list owns the scroll (overflow:auto, both axes)');
    const en = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(en['yoagent.chatPlaceholder'], 'Ask anything…', 'composer placeholder matches the mockup ("Ask anything…")');
    assert.equal(en['yoagent.jobs.title'], 'YO!agent jobs', 'YO!agent job-list title is localized');
    assert.equal(en['yoagent.jobs.confirm'], 'Confirm', 'YO!agent job confirm button is localized');
    assert.equal(en['yoagent.waiting.count.other'], 'Waiting for {count} replies', 'YO!agent pending-wait count is localized');
    assert.equal(en['yoagent.waiting.handoff'], 'Waiting for tmux session `{source}` to respond (regarding {sourceRegarding}), before handing off the next request to tmux session `{target}` (regarding {targetRegarding})', 'YO!agent handoff wait text names both sessions and both regarding summaries');
  });

  test('t@7287', () => {
    const api = loadYolomux('', ['1', '2']);
    api.setDocumentTitleNowForTest(200000);
    api.setTmuxSignalStateForTest({
      windows: [
        {
          key: '1:0',
          session: '1',
          window_index: '0',
          activity_ts: 190,
          bell_flag: true,
          silence_flag: true,
          active_clients: 2,
          active_clients_list: 'client-a,client-b',
          active_client_details: [{name: 'client-a', user: 'keiven'}, {name: 'client-b', user: 'viewer'}],
          zoomed: true,
          layout: 'layout-host',
          visible_layout: 'visible-layout-host',
          panes: [{
            window_key: '1:0',
            session: '1',
            window_index: '0',
            pane_index: '0',
            target: '%11',
            pane_id: '%11',
            current_path: '/home/keivenc/live-project',
            current_command: 'codex',
            mode: 'copy-mode',
            in_mode: true,
            input_off: true,
            synchronized: true,
            dead: true,
            dead_status: 9,
          }],
        },
        {
          key: '2:0',
          session: '2',
          window_index: '0',
          activity_ts: 10,
          panes: [{
            window_key: '2:0',
            session: '2',
            window_index: '0',
            pane_index: '0',
            target: '%22',
            pane_id: '%22',
            current_path: '/home/keivenc/old-project',
            current_command: 'claude',
          }],
        },
      ],
    });
    api.setActivitySummaryPayloadForTest({
      agents: [{
        label: "session '2' 0:claude",
        session: '2',
        window: '0',
        window_label: '0:claude',
        pane: '0',
        pane_target: '%22',
        agent_kind: 'claude',
        cwd: '/home/keivenc/old-project',
        transcript: '/tmp/claude.jsonl',
        sort_ts: 999,
      }, {
        label: "session '1' 0:codex",
        session: '1',
        window: '0',
        window_label: '0:codex',
        pane: '0',
        pane_target: '%11',
        agent_kind: 'codex',
        cwd: '/home/keivenc/project',
        transcript: '/tmp/codex.jsonl',
      }],
    });
    const html = api.yoagentRecentAgentsHtmlForTest();
    assert.ok(html.includes('agent exited (status 9)'), 'recent agents surface dead tmux agent status');
    assert.ok(html.indexOf('session 1') < html.indexOf('session 2'), 'tmux window_activity sorts recent agents ahead of stale backend order');
    assert.ok(html.includes('yoagent-recent-agent tmux-idle'), 'old tmux window_activity dims idle recent-agent rows');
    assert.ok(html.includes('signal-bell') && html.includes('signal-silence'), 'recent agents surface tmux bell and silence signal chips');
    assert.ok(html.includes('signal-presence') && html.includes('2 viewers'), 'recent agents surface tmux active-client presence');
    assert.ok(html.includes('signal-zoom') && html.includes('zoom'), 'recent agents surface tmux zoom state');
    assert.ok(html.includes('/home/keivenc/live-project'), 'recent agents prefer live tmux pane_current_path');
    assert.ok(html.includes('client-a') && html.includes('layout-host') && html.includes('visible-layout-host'), 'recent agent title includes tmux viewer and layout details');
    assert.ok(html.includes('copy-mode') && html.includes('read-only') && html.includes('sync'), 'recent agents surface pane mode/read-only/sync chips');
    assert.ok(html.includes('data-yolomux-agent-restart="codex"'), 'dead agent row offers a restart action for the same agent kind');
    assert.ok(html.includes('tmux'), 'recent agents prefer tmux recency text when window_activity is available');
  });

  test('t@7290', () => {
    const api = loadYolomux('', ['1', '2']);
    api.rememberFileExplorerOpenIntentForTest(false);
    const single = api.emptyLayoutSlots();
    single[api.layoutTreeKey] = api.leafNode('left');
    single.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(single);
    assert.equal(api.rightmostExistingPaneSlot(), null, 'single-pane layout has no existing right pane');
    api.openYoagentRightPane();
    assert.equal(api.infoPanelSubTabForTest(), 'yoagent', 'Cmd+Alt+B selects the YO!agent sub-tab');
    let serialized = api.serialize(api.currentSlots());
    const paneList = value => Object.values(value.panes).filter(Boolean).map(canonical);
    const hasPane = (panes, expected) => panes.some(pane => JSON.stringify(pane) === JSON.stringify(expected));
    assert.equal(serialized.tree.split, 'row', 'Cmd+Alt+B creates a right pane from a single-pane layout');
    assert.ok(hasPane(paneList(serialized), {tabs: ['1'], active: '1'}), 'single-pane shortcut keeps the tmux tab alone');
    assert.ok(hasPane(paneList(serialized), {tabs: ['__info__'], active: '__info__'}), 'single-pane shortcut creates a separate YO!agent pane');

    api.rememberFileExplorerOpenIntentForTest(true);
    const finderSingle = api.emptyLayoutSlots();
    finderSingle[api.layoutTreeKey] = api.splitNode('row', api.leafNode('slot1'), api.leafNode('left'), 22);
    finderSingle.slot1 = api.paneStateWithTabs([api.fileExplorerItemId], api.fileExplorerItemId);
    finderSingle.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(finderSingle);
    assert.equal(api.rightmostExistingPaneSlot(), null, 'Finder plus one content pane does not count as an existing right pane');
    api.openYoagentRightPane();
    serialized = api.serialize(api.currentSlots());
    const finderSinglePanes = Object.values(serialized.panes).map(canonical);
    assert.ok(hasPane(finderSinglePanes, {tabs: ['1'], active: '1'}), 'Finder-docked single content pane keeps the tmux tab alone');
    assert.ok(hasPane(finderSinglePanes, {tabs: ['__files__'], active: '__files__'}), 'Finder-docked single content pane keeps Finder alone');
    assert.ok(hasPane(finderSinglePanes, {tabs: ['__info__'], active: '__info__'}), 'Finder-docked single content pane creates a separate YO!agent pane');

    api.rememberFileExplorerOpenIntentForTest(false);
    const split = api.emptyLayoutSlots();
    split[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    split.left = api.paneStateWithTabs(['2', '__info__'], '2');
    split.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(split);
    assert.equal(api.rightmostExistingPaneSlot(), 'slot1', 'right-pane detection uses the layout tree, not only literal right slot names');
    api.openYoagentRightPane();
    serialized = api.serialize(api.currentSlots());
    const splitPanes = paneList(serialized);
    assert.ok(hasPane(splitPanes, {tabs: ['2'], active: '2'}), `existing split removes YO!agent from the source pane: ${JSON.stringify(splitPanes)}`);
    assert.ok(hasPane(splitPanes, {tabs: ['1', '__info__'], active: '__info__'}), `existing split places YO!agent into the right pane: ${JSON.stringify(splitPanes)}`);
  });

  test('t@7321', () => {
    // file-search dedupe folds mirror + symlink copies, keeps different-content same-name.
    const api = loadYolomux('', ['1']);
    const deduped = api.dedupeFileSearchResults([
      {path: '/a/notes/DIS-1842.md', realpath: '/a/notes/DIS-1842.md', size: 100},
      {path: '/b/notes/DIS-1842.md', realpath: '/b/notes/DIS-1842.md', size: 100},   // content mirror -> folded
      {path: '/c/DIS-1842.md', realpath: '/c/DIS-1842.md', size: 250},                // different content -> kept
      {path: '/d/link.md', realpath: '/a/notes/DIS-1842.md', size: 100},              // symlink overlap -> folded
    ]).map(file => file.path);
    assert.deepStrictEqual([...deduped], ['/a/notes/DIS-1842.md', '/c/DIS-1842.md'], '#25: mirror + symlink copies fold; different-content same-name both survive');
    // Unknown-size hits dedupe only by path/realpath (never collapse two same-name unknown-size files).
    const unknown = api.dedupeFileSearchResults([
      {path: '/x/a.md', realpath: '/x/a.md'},
      {path: '/y/a.md', realpath: '/y/a.md'},
    ]).map(file => file.path);
    assert.deepStrictEqual([...unknown], ['/x/a.md', '/y/a.md'], '#25: unknown-size same-name files are not collapsed');
  });

  test('t@7339', () => {
    // the yoagent markdown normalizer tightens loose lists / collapses blank-line runs.
    const api = loadYolomux('', ['1']);
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(api.yoagentTightMarkdown('- a\n\n- b\n\n- c'), '- a\n- b\n- c', '#129: blank lines between adjacent list items are stripped (tight list)');
    assert.equal(api.yoagentTightMarkdown('1. a\n\n2. b'), '1. a\n2. b', '#129: ordered-list item gaps are stripped too');
    assert.equal(api.yoagentTightMarkdown('lead\n\n\n\nmore'), 'lead\n\nmore', '#129: runs of 2+ blank lines collapse to one');
    assert.equal(api.yoagentTightMarkdown('- a\n\nparagraph'), '- a\n\nparagraph', '#129: a blank line before a NON-list paragraph is preserved');
    // The chat assistant path also runs the tightener (not just the summary path).
    assert.ok(/renderMarkdownPreviewInto\(body, yoagentTightMarkdown\(/.test(source), '#129: the chat assistant body is tightened before rendering');
    // yoagentInlineMarkdown folds in the tightening (heading downgrade + tight lists).
    assert.equal(api.yoagentInlineMarkdown('## H\n\n- a\n\n- b'), '**H**\n\n- a\n- b', '#129: inline-markdown downgrades headings AND tightens the list');
    // a <p> inside an <li> carries no margin so loose lists render tight.
    assert.ok(/\.markdown-body li > p\s*\{[^}]*margin:\s*0/.test(fs.readFileSync('static/yolomux.css', 'utf8')), '#128: .markdown-body li > p has zero margin');
  });

  test('t@7355', () => {
    // the markdown-preview relative-link path normalizer + the in-pane link handler.
    const api = loadYolomux('', ['1']);
    assert.equal(api.joinAndNormalize('/a/b/c', './x.md'), '/a/b/c/x.md', '#133: ./ resolves against the base dir');
    assert.equal(api.joinAndNormalize('/a/b/c', '../y/z.md'), '/a/b/y/z.md', '#133: ../ pops a segment');
    assert.equal(api.joinAndNormalize('/a/b', 'bare.md'), '/a/b/bare.md', '#133: a bare name resolves against the base dir');
    assert.equal(api.joinAndNormalize('/a/b', '/abs/x.md'), '/abs/x.md', '#133: an absolute rel ignores the base');
    assert.equal(api.joinAndNormalize('/a/b/c', '../../top.md'), '/a/top.md', '#133: multiple ../ collapse');
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    // The handler reads the RAW href, opens external links in a new tab, and routes file:// + relative
    // file links through openFileInEditor with a preview/edit mode + a failure toast.
    assert.ok(/function handleMarkdownPreviewLinkClick/.test(src), '#133: the markdown-preview link handler exists');
    assert.ok(/a\.getAttribute\('href'\)/.test(src), '#133: the handler reads the raw href attribute');
    assert.ok(/function localPathFromFileHref/.test(src), '#133: file:// preview links are converted to server-side paths');
    assert.ok(src.indexOf('localPathFromFileHref(href)') > -1, '#133: file:// links use the local-path helper');
    assert.ok(src.indexOf('localPathFromFileHref(href)') < src.indexOf("window.open(a.href, '_blank', 'noopener,noreferrer')"), '#133: file:// links are handled before the external window.open branch');
    assert.ok(/window\.open\(a\.href, '_blank', 'noopener,noreferrer'\)/.test(src), '#133: external/other-scheme links open in a new tab');
    assert.ok(/openFileInEditor\(resolved, basenameOf\(resolved\), \{[\s\S]*?viewMode: editorPreviewModeAvailable\(resolved\) \? 'preview' : 'edit'/.test(src), '#133: preview-capable file links open in preview (md/html), else edit');
    assert.ok(/t\('preview\.openFailed'/.test(src), "#133: a failed open surfaces a toast");
    // The handler is wired ONLY to the file-editor preview (path provided), not to yoagent bodies.
    assert.ok(/renderMarkdownPreviewInto\(container, text, path, \{context: previewContext\}\)/.test(src), '#133: the file-editor preview threads the owning path and preview context; yoagent bodies pass no path');
  });

  test('t@7378', () => {
    // #260: the Global color theme field renders plain RADIO buttons (replaced the macOS-style cards).
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    api.setClientSettingsPatchForTest({appearance: {theme: 'light'}});
    const html = api.preferencesPanelHtmlForTest('');
    for (const v of ['system', 'dark', 'light']) {
      assert.ok(new RegExp(`<input type="radio"[^>]*value="${v}"[^>]*data-setting-path="appearance\\.theme"`).test(html), `#260: a ${v} theme radio renders`);
    }
    assert.ok(/role="radiogroup"/.test(html), '#260: the theme radios render as a radiogroup');
    assert.ok(/value="light"[^>]*data-setting-path="appearance\.theme"[^>]*checked/.test(html), '#260: the active theme (light) radio is checked');
    assert.equal((html.match(/type="radio"[^>]*data-setting-path="appearance\.theme"[^>]*checked/g) || []).length, 1, '#260: exactly one theme radio is checked');
    assert.equal(html.includes('data-theme-card'), false, '#260: no macOS-style theme-card markup remains');
    assert.equal(/<select[^>]*data-setting-path="appearance\.theme"/.test(html), false, '#260: the theme field is radios, not a <select>');
    const themeSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/if \(path === 'appearance\.theme'\) \{\s*globalThemeMode = normalizeGlobalThemeMode\(value\);\s*applyGlobalThemeMode/.test(themeSrc), '#260: changing the theme radio applies the theme live (via savePreferenceControl)');
    const themeCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.preferences-radio-group\s*\{/.test(themeCss), '#260: the radio group has styling');
    assert.equal(/\.theme-card-system/.test(themeCss), false, '#260: the old theme-card CSS is gone');
  });

  test('t@7399', () => {
    // Preview font size is independent from the editor font size and defaults one px larger.
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    const html = api.preferencesPanelHtmlForTest('');
    assert.ok(/data-preference-section="Terminal \/ Editor"[\s\S]*data-setting-path="appearance\.preview_font_size"/.test(html), 'preview font size renders in Terminal / Editor preferences');
    assert.ok(html.includes('Preview font size'), 'preview font size preference has a label');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes("let editorPreviewFontSize = initialSetting('appearance.preview_font_size', editorFontSize + 1);"), 'preview font size defaults one larger than editor font during bootstrap');
    assert.ok(source.includes("root.setProperty('--editor-preview-font-size'"), 'preview font size writes its own CSS variable');
    assert.ok(source.includes("numberSetting('appearance.preview_font_size', editorFontSize + 1)"), 'preview font size reload preserves the editor+1 fallback');
    assert.ok(source.includes('class="file-editor-preview-font-panel"'), 'preview toolbar includes a font-size control group');
    assert.ok(source.includes('data-editor-preview-font-step="-1"'), 'preview toolbar includes a decrease button');
    assert.ok(source.includes('data-editor-preview-font-step="1"'), 'preview toolbar includes an increase button');
    assert.ok(source.includes("saveSettingsPatch(settingPatch('appearance.preview_font_size', next))"), 'preview font toolbar persists the setting');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.file-editor-preview-pane\s*\{[^}]*font-size:\s*var\(--editor-preview-font-size\)/.test(css), 'rendered preview pane uses the preview font variable');
    assert.ok(/\.file-editor-preview-pane-panel\s*\{[^}]*font-size:\s*var\(--editor-preview-font-size\)/.test(css), 'split/preview pane uses the preview font variable');
    assert.ok(/\.file-editor-raw-panel\s*\{[^}]*font-size:\s*var\(--editor-font-size\)/.test(css), 'raw editor pane keeps the editor font variable');
    const settingsSource = fs.readFileSync('yolomux_lib/settings.py', 'utf8');
    assert.ok(settingsSource.includes('"preview_font_size": 14'), 'preview font size default is 14');
    assert.ok(settingsSource.includes('("appearance", "preview_font_size"): (6, 32)'), 'preview font size has server-side limits');
  });

  test('t@7423', () => {
    // Phase 1: the topbar language switcher + system-locale resolution.
    const api = loadYolomux('', ['1']);
    // Explicit prefs resolve to themselves; 'system' (no navigator.language in the harness) falls back to en.
    assert.equal(api.resolveLocalePref('zh-Hant'), 'zh-Hant', 'Phase 1: an explicit locale pref resolves to itself');
    assert.equal(api.resolveLocalePref('zh-Hans'), 'zh-Hans', 'Phase 1: Simplified Chinese resolves to itself');
    assert.equal(api.resolveLocalePref('en'), 'en', 'Phase 1: English resolves to itself');
    assert.equal(api.resolveLocalePref('system'), 'en', 'Phase 1: system falls back to en without a browser locale');
    // The switcher choices: system + shipped locales in product-priority order + pseudo, endonym-labeled.
    const choices = api.i18nLocaleChoices();
    assert.deepEqual(choices.map(c => c.value), ['system', 'en', 'zh-Hant', 'zh-Hans', 'ja', 'ko', 'es', 'de', 'fr', 'it', 'pt-BR', 'pl', 'nl', 'he', 'ar', 'ru', 'hi', 'vi', 'th', 'tr', 'en-XA'], 'Phase 1/2/4: the locale choices are ordered with all shipped locales then pseudo');
    assert.equal(choices.find(c => c.value === 'de').label, 'Deutsch', 'Phase 2: German is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'ru').label, 'Русский', 'Phase 2: Russian is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'ar').label, 'العربية', 'Phase 2: Arabic is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'he').label, 'עברית', 'Hebrew is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'vi').label, 'Tiếng Việt', 'Vietnamese is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'th').label, 'ไทย', 'Thai is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'tr').label, 'Türkçe', 'Turkish is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'nl').label, 'Nederlands', 'Dutch is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'pl').label, 'Polski', 'Polish is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'it').label, 'Italiano', 'Italian is labeled with its endonym');
    for (const loc of ['de', 'fr', 'pt-BR', 'ru', 'ko', 'hi', 'ar', 'he', 'vi', 'th', 'tr', 'nl', 'pl', 'it']) {
      assert.equal(api.resolveLocalePref(loc), loc, `Phase 2: ${loc} resolves to itself`);
    }
    // RTL: Arabic and Hebrew are detected as right-to-left; LTR locales are not.
    assert.equal(api.i18nIsRtl('ar'), true, 'Phase 2: ar is RTL');
    assert.equal(api.i18nIsRtl('he'), true, 'he is RTL');
    assert.equal(api.i18nIsRtl('de'), false, 'Phase 2: de is LTR');
    // applyLocale flips document.dir; the build CSS uses logical flow properties so RTL mirrors.
    const rtlSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/document\.documentElement\.setAttribute\('dir', i18nIsRtl\(next\) \? 'rtl' : 'ltr'\)/.test(rtlSrc), 'Phase 2: applyLocale sets the document direction for RTL locales');
    // A language switch must repaint the Finder's static toolbar chrome, not just panel bodies — so
    // rerenderForLocale rebuilds the Finder panel from source (fixes stale prev-locale toolbar labels).
    assert.ok(/function rerenderForLocale[\s\S]*?relocalizeFileExplorerPanels\(\)/.test(rtlSrc), 'rerenderForLocale rebuilds the Finder toolbar chrome on a language switch');
    assert.ok(/function relocalizeFileExplorerPanels\(\)[\s\S]*?removePanelForItem\(fileExplorerItemId\)[\s\S]*?renderPanels\(/.test(rtlSrc), 'relocalizeFileExplorerPanels evicts then rebuilds the Finder panel from its single source of truth');
    const rtlCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.equal(/(^|[^-])(margin|padding|border)-(left|right):/m.test(rtlCss.replace(/[a-z-]*-(left|right)-radius/g, '')), false, 'Phase 2: flow-spacing CSS uses logical (inline) properties, not physical left/right, so RTL mirrors');
    assert.ok(rtlCss.includes('margin-inline-start:') && rtlCss.includes('padding-inline-start:'), 'Phase 2: the CSS uses logical inline properties');
    assert.equal(choices.find(c => c.value === 'es').label, 'Español', 'Phase 1: Spanish is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'ja').label, '日本語', 'Phase 1: Japanese is labeled with its endonym');
    assert.ok(choices.findIndex(c => c.value === 'ko') === choices.findIndex(c => c.value === 'ja') + 1, 'Korean appears immediately after Japanese');
    assert.deepEqual(['de', 'fr', 'it', 'pt-BR', 'pl', 'nl'].map(loc => choices[choices.findIndex(c => c.value === 'de') + ['de', 'fr', 'it', 'pt-BR', 'pl', 'nl'].indexOf(loc)]?.value), ['de', 'fr', 'it', 'pt-BR', 'pl', 'nl'], 'German, French, Italian, Portuguese, Polish, and Dutch are grouped together');
    assert.ok(choices.findIndex(c => c.value === 'he') === choices.findIndex(c => c.value === 'nl') + 1, 'Hebrew appears immediately after Dutch');
    assert.equal(api.resolveLocalePref('es'), 'es', 'Phase 1: Spanish resolves to itself');
    assert.equal(api.resolveLocalePref('ja'), 'ja', 'Phase 1: Japanese resolves to itself');
    assert.equal(choices.find(c => c.value === 'zh-Hant').label, '繁體中文', 'Phase 1: Traditional Chinese is labeled with its endonym');
    assert.equal(choices.find(c => c.value === 'zh-Hans').label, '简体中文', 'Phase 1: Simplified Chinese is labeled with its endonym');
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/sessionButtons\.appendChild\(createTopbarLanguageSwitcher\(\)\)/.test(src), 'Phase 1: the topbar renders the language switcher');
    assert.ok(/function createTopbarLanguageSwitcher[\s\S]*?applyLocale\(resolveLocalePref\(value\)\)[\s\S]*?saveSettingsPatch\(settingPatch\('general\.language', value\)\)/.test(src), 'Phase 1: the switcher applies the locale optimistically AND saves general.language (same setting as Preferences)');
    assert.ok(/function rerenderForLocale[\s\S]*?renderSessionButtons\(\{force: true\}\)/.test(src), 'Phase 1: a real locale switch force-repaints the topbar labels after selection');
    assert.ok(src.includes("active.matches?.('select, input, .topbar-language, .app-menu-button')") && /function renderSessionButtons[\s\S]*?topbarControlIsActive\(\)/.test(src), 'the topbar does not rebuild while a topbar control is focused/open');
    // The zh fallback mapping (zh-TW/HK/Hant -> Hant, other zh -> Hans).
    assert.ok(/nav\.startsWith\('zh'\)\) return \/hant\|/.test(src), 'Phase 1: system maps Chinese browser locales to Hant/Hans');
    assert.ok(/share\.ttl_seconds[\s\S]*suffix:\s*t\('unit\.minute\.short'\)/.test(src), 'YO!share Preferences minute suffix is localized');
    assert.ok(/function tmuxSessionNameError\(name\)[\s\S]*rename\.error\.required[\s\S]*rename\.error\.tooLong[\s\S]*rename\.error\.invalidChars/.test(src), 'session rename validation errors use locale keys');
    assert.ok(/function dropActionDisplayLabel\(action\)[\s\S]*action\.labelKey[\s\S]*t\(action\.labelKey\)/.test(src), 'drop action display labels use locale keys while canonical labels remain stable');
    assert.ok(/function showTerminalDropSuggestions[\s\S]*t\('drop\.pathInserted'\)[\s\S]*tPlural\('drop\.files'[\s\S]*t\('drop\.suggestionHint'/.test(src), 'terminal drop suggestion header is localized');
    assert.ok(/async function showContext\(session\)[\s\S]*transcript\.tailTitle/.test(src), 'transcript tail modal title is localized');
    assert.ok(/\.topbar-language\s*\{/.test(fs.readFileSync('static/yolomux.css', 'utf8')), 'Phase 1: the language switcher has topbar styling');
    // #256: topbar theme switcher (auto/dark/light) mirrors the language switcher and sits right of it;
    // order ends Language, Theme, Activity (activity pinned far-right).
    // #257: the topbar theme switcher was REMOVED (redundant). Order is Language, then Activity (far right).
    assert.ok(/sessionButtons\.appendChild\(createTopbarLanguageSwitcher\(\)\);\s*sessionButtons\.appendChild\(createTopbarActivityStatus\(\)\)/.test(src), '#257: topbar order is Language then Activity (no theme switcher between them)');
    assert.ok(/function topbarControlIsActive\(\)[\s\S]*document\.activeElement[\s\S]*sessionButtons\?\.contains\(active\)[\s\S]*active\.matches\?\.\('select, input, \.topbar-language, \.app-menu-button'\)/.test(src), '#62: topbar detects focused controls before passive rebuilds');
    assert.ok(/if \(!options\.force && topbarControlIsActive\(\)\) \{[\s\S]*pendingSessionButtonsRender = true[\s\S]*return;\s*\}/.test(src), '#62: passive topbar renders defer while a topbar control is focused');
    assert.ok(/button\.addEventListener\('blur', flushPendingSessionButtonsRender\)/.test(src), '#62: language button blur flushes a deferred topbar render');
    assert.equal(/createTopbarThemeSwitcher/.test(src), false, '#257: createTopbarThemeSwitcher is gone (no redundant topbar theme select)');
    {
      const css = fs.readFileSync('static/yolomux.css', 'utf8');
      assert.equal(/\.topbar-theme\s*\{/.test(css), false, '#257: the .topbar-theme CSS is removed with the switcher');
      // #254/#259 follow-up: light-mode inactive-pane dim stays neutral gray, with a softer alpha.
      assert.ok(css.includes('--inactive-pane-overlay-rgb: 90 96 105'), '#259: light-mode inactive panes dim a softer neutral gray (no red cast)');
      assert.ok(css.includes('--inactive-pane-overlay-alpha: 0.09'), '#259: light-mode inactive panes keep the softer alpha base');
      // Light-mode pane header (image 043): greenish-light tab-strip container + light frame-control
      // buttons (the minimize/zoom squares used to render dark/"black" with no light values).
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-dim:\s*#e1edda/.test(css), 'light mode: the pane tab-strip container is greenish-light (active-accent-dim)');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-text:\s*#071000/.test(css), 'light mode: active accent text is dark on bright green/gold/white fills');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--icon-code:\s*#0369a1[\s\S]*?--link-soft:\s*#075985/.test(css), 'light mode: code/link text tokens use darker readable shades');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-control-bg:\s*#f7f9fc/.test(css), 'light mode: the pane minimize/frame button has a light fill (not a dark square)');
      assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-zoom-bg:\s*var\(--active-control-bg\)/.test(css), 'light mode: the pane zoom button uses the shared active-control fill, not a dark square');
      assert.ok(/function uiColorVisualPreset\(value, light = false\)[\s\S]*value === 'green'[\s\S]*light[\s\S]*text:\s*'#071000'/.test(src), 'Green active-color metadata reports dark on-accent text for the light preset');
      assert.equal(css.includes('--inactive-pane-overlay-rgb: 124 82 88'), false, '#259: the earlier warm/red tint is gone (superseded by gray)');
      assert.equal(css.includes('--inactive-pane-overlay-alpha: 0.16'), false, '#259 follow-up: the too-dark light overlay alpha is gone');
      assert.equal(css.includes('--inactive-pane-overlay-alpha: 0.13'), false, '#259 follow-up: the still-too-dark light overlay alpha is gone');
      // #258 follow-up: editor toolbar placement is inherited from stable parent zones, not per-button order.
      assert.ok(/\.file-editor-toolbar-left\s*\{[^}]*flex:\s*0 1 auto/.test(css), 'editor info bar: #/Differ/FROM-TO live in the shared left zone');
      assert.ok(/\.file-editor-toolbar-center\s*\{[^}]*position:\s*absolute[\s\S]*left:\s*50%/.test(css), 'editor info bar: font-size controls live in the shared center zone');
      assert.ok(/\.file-editor-toolbar-right\s*\{[^}]*margin-inline-start:\s*auto[\s\S]*justify-content:\s*flex-end/.test(css), 'editor info bar: edit/preview/tools live in the shared right zone');
      assert.equal(/\.file-editor-(?:gutter|diff|diff-expand)-panel\s*\{[^}]*order:/.test(css), false, 'editor info bar: left buttons do not own placement with child order rules');
      assert.ok(/\.file-editor-diff-ref-panel\s*\{[^}]*min-width:\s*max-content[^}]*overflow:\s*visible/.test(css), 'editor info bar: FROM/TO/reset is intrinsic-width and not clipped');
      assert.equal(css.includes('max-width: min(32vw, 190px)'), false, 'editor info bar: the old too-narrow 190px clipping cap is gone');
      assert.ok(/\.file-tab-parent\s*\{[^}]*text-overflow:\s*ellipsis/.test(css), 'duplicate file-tab parent suffix is styled as compact muted metadata');
      assert.ok(/\.preferences-setting-control\.setting-type-select,\s*\.preferences-setting-control\.setting-type-text\s*\{[^}]*justify-content:\s*start/.test(css), 'Preferences selects/text inputs are left-aligned from the shared inset');
      assert.ok(/\.preferences-setting-control\.setting-type-number input\[type="number"\]\s*\{[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(css), 'Preferences number inputs are left-aligned from the shared inset');
      // #258 (toast): the toast stack clears the topbar (z-index above 180) and messages wrap, not clip.
      assert.ok(/\.panel-toast-stack\s*\{[^}]*z-index:\s*var\(--z-full-screen-overlay\)/.test(css), '#258: the toast stack renders above the topbar (var(--z-full-screen-overlay)) so it is not clipped under it');
      assert.ok(/\.toast-line\s*\{[^}]*white-space:\s*normal/.test(css), '#258: toast messages wrap (white-space:normal) instead of ellipsis-clipping');
      assert.equal(/\.toast-line\s*\{[^}]*white-space:\s*nowrap/.test(css), false, '#258: the old nowrap/ellipsis clipping of the toast message line is gone');
    }
    // #255: inactive-pane dimming is now ONE CSS rule keyed off the uniformly-toggled .focused-pane class
    // — no per-pane JS overlay, no isVirtualItem special-case, every pane type dims identically.
    assert.equal(/function installPanelInactiveOverlays/.test(src), false, '#255: the per-pane JS overlay installer is deleted (dimming is pure CSS)');
    assert.equal(/class="panel-inactive-overlay"/.test(src), false, '#255: no per-pane inactive-overlay div is injected anymore');
    assert.ok(/\.panel:not\(\.focused-pane\)[^{]*\.panel-overlay-root::after\s*\{[^}]*background:\s*var\(--inactive-pane-overlay\)/.test(fs.readFileSync('static/yolomux.css', 'utf8')), '#255: inactive panes dim via one CSS rule on .panel:not(.focused-pane) .panel-overlay-root::after');
    assert.equal(/\.panel:not\(\.focused-pane\):not\(\.typing-ready-pane\)[^{]*\.panel-overlay-root::after/.test(fs.readFileSync('static/yolomux.css', 'utf8')), false, 'inactive-pane dim must still paint on a stale typing-ready pane');
    assert.equal(/updateInactivePaneGradientDirs|has-inactive-pane-gradient|pane-gradient-dir/.test(src), false, 'inactive-pane gradient JS is removed until the feature is revisited');
    // #260: a drag-drop open establishes a clean baseline (clears external-change flags on a fresh,
    // non-dirty open) so it never pops a spurious reload prompt — matching double-click.
    assert.ok(/function openDraggedFilesInEditor[\s\S]*?if \(draggedState && !draggedState\.dirty\) \{[\s\S]*?delete draggedState\.externalChanged/.test(src), '#260: drag-drop open clears externalChanged on a non-dirty fresh open (no spurious reload prompt)');
    // boot() resolves the raw general.language pref (so a system pref localizes client-side).
    assert.ok(/await applyLocale\(resolveLocalePref\(initialSetting\('general\.language', 'system'\)\)\)/.test(src), 'Phase 1: boot resolves the raw language pref (system -> navigator)');
    // The Spanish locale ships with full key-parity and real (non-English) translations.
    const en = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    const es = JSON.parse(fs.readFileSync('static/locales/es.json', 'utf8'));
    assert.deepEqual(Object.keys(es).sort(), Object.keys(en).sort(), 'Phase 1: es.json has exactly the same keys as en.json (parity)');
    const contextMenuOpenKeys = ['contextmenu.openInDiffer', 'contextmenu.openNewDiffEditor', 'contextmenu.openNewEditor'];
    const updatePreferenceKeys = [
      'pref.general.reload_on_update.label',
      'pref.general.reload_on_update.help',
      'pref.general.reload_on_update_auto.label',
      'pref.general.reload_on_update_auto.help',
      'pref.updates.notify_level.label',
      'pref.updates.notify_level.help',
      'pref.updates.notify_level.major',
      'pref.updates.notify_level.minor',
      'pref.updates.notify_level.patch',
      'pref.updates.notify_level.none',
    ];
    assert.equal(en['contextmenu.openInDiffer'], 'Open in a Differ', 'en reusable Differ context label');
    assert.equal(en['contextmenu.openNewDiffEditor'], 'Open in a new Differ', 'en new Differ context label');
    assert.equal(en['contextmenu.openNewEditor'], 'Open in a new Editor', 'en new Editor context label');
    assert.equal(en['pref.general.reload_on_update.label'], 'Show reload prompt after server update', 'en server-version reload label is specific');
    assert.equal(en['pref.updates.notify_level.label'], 'Notify when change in', 'en update notification threshold label is specific');
    for (const loc of ['es', 'ja', 'de', 'fr', 'pt-BR', 'ru', 'ko', 'hi', 'ar', 'he', 'vi', 'th', 'tr', 'nl', 'pl', 'it', 'zh-Hans', 'zh-Hant']) {
      const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
      for (const key of contextMenuOpenKeys) {
        assert.ok(typeof cat[key] === 'string' && cat[key].length, `${loc} has ${key}`);
        assert.notEqual(cat[key], en[key], `${loc} translates ${key} instead of falling back to English`);
      }
      for (const key of updatePreferenceKeys) {
        assert.ok(typeof cat[key] === 'string' && cat[key].length, `${loc} has ${key}`);
        assert.notEqual(cat[key], en[key], `${loc} localizes ${key} instead of falling back to English`);
      }
      for (const key of ['share.maxTime', 'share.maxViewers', 'share.newShare', 'share.readOnly', 'drop.pathInserted']) {
        assert.ok(typeof cat[key] === 'string' && cat[key].length, `${loc} has ${key}`);
        assert.notEqual(cat[key], en[key], `${loc} localizes ${key} instead of falling back to English`);
      }
      assert.ok(typeof cat['unit.minute.short'] === 'string' && cat['unit.minute.short'].length, `${loc} has unit.minute.short`);
    }
    // The YO!info / YO!agent tab labels are localized via brand.tab.*; en (and non-Chinese locales) keep
    // the English brand text, while the two Chinese catalogs render the requested glyphs (asserted below).
    assert.equal(en['brand.tab.info'], 'YO!info', 'en YO!info tab label');
    assert.equal(en['brand.tab.agent'], 'YO!agent', 'en YO!agent tab label');
    assert.equal(es['menu.file'], 'Archivo', 'Phase 1: es translates a representative menu label');
    assert.equal(es['pref.reset.cancel'], 'Cancelar', 'Phase 1: es translates the reset cancel button');
    assert.ok(es['pref.appearance.file_explorer_font_size.label'].includes('{name}'), 'Phase 1: es preserves interpolation placeholders');
    const ja = JSON.parse(fs.readFileSync('static/locales/ja.json', 'utf8'));
    assert.deepEqual(Object.keys(ja).sort(), Object.keys(en).sort(), 'Phase 1: ja.json has exactly the same keys as en.json (parity)');
    assert.equal(ja['menu.file'], 'ファイル', 'Phase 1: ja translates a representative menu label');
    assert.equal(ja['pref.reset.cancel'], 'キャンセル', 'Phase 1: ja translates the reset cancel button');
    assert.ok(ja['changes.fileCount.other'].includes('{count}'), 'Phase 1: ja preserves count placeholders');
    const de = JSON.parse(fs.readFileSync('static/locales/de.json', 'utf8'));
    assert.deepEqual(Object.keys(de).sort(), Object.keys(en).sort(), 'Phase 2: de.json has exactly the same keys as en.json (parity)');
    assert.equal(de['menu.file'], 'Datei', 'Phase 2: de translates a representative menu label');
    assert.equal(de['login.signIn'], 'Anmelden', 'Phase 2: de translates the login sign-in label');
    const fr = JSON.parse(fs.readFileSync('static/locales/fr.json', 'utf8'));
    assert.deepEqual(Object.keys(fr).sort(), Object.keys(en).sort(), 'Phase 2: fr.json has exactly the same keys as en.json (parity)');
    assert.equal(fr['menu.file'], 'Fichier', 'Phase 2: fr translates a representative menu label');
    assert.equal(fr['pref.reset.cancel'], 'Annuler', 'Phase 2: fr translates the reset cancel button');
    // The Phase 2 tail locales all ship with full key-parity and preserve placeholders.
    for (const loc of ['pt-BR', 'ru', 'ko', 'hi', 'ar', 'he']) {
      const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
      assert.deepEqual(Object.keys(cat).sort(), Object.keys(en).sort(), `Phase 2: ${loc}.json has exactly the same keys as en.json (parity)`);
      assert.equal(cat['brand.marker'], 'YO', `Phase 2: ${loc} keeps the YO brand marker`);
      assert.equal(cat['brand.tab.info'], 'YO!info', `Phase 2: ${loc} keeps the YO!info tab label`);
      assert.equal(cat['brand.tab.agent'], 'YO!agent', `Phase 2: ${loc} keeps the YO!agent tab label`);
      assert.ok(cat['pref.appearance.file_explorer_font_size.label'].includes('{name}'), `Phase 2: ${loc} preserves the {name} placeholder`);
      assert.ok(cat['yoagent.files'].includes('{count}') && cat['yoagent.files'].includes('{added}'), `Phase 2: ${loc} preserves count/added placeholders`);
      assert.notEqual(cat['menu.file'], 'File', `Phase 2: ${loc} actually translates (menu.file not English)`);
      // Phase 3: the new Intl-wrap + deterministic-framing keys ship in every locale.
      for (const k of ['yoagent.updated.wrap', 'det.noBackend', 'det.noActivity', 'det.openPending']) {
        assert.ok(typeof cat[k] === 'string' && cat[k].length, `Phase 3: ${loc} has ${k}`);
      }
      assert.ok(cat['yoagent.updated.wrap'].includes('{rel}'), `Phase 3: ${loc} preserves the {rel} placeholder`);
    }
    // Phase 4 locales ship in the developer-priority batch and preserve the same catalog contract.
    const phase4Expected = {
      vi: {menuFile: 'Tệp', loginSignIn: 'Đăng nhập', language: 'Ngôn ngữ'},
      th: {menuFile: 'ไฟล์', loginSignIn: 'เข้าสู่ระบบ', language: 'ภาษา'},
      tr: {menuFile: 'Dosya', loginSignIn: 'Oturum aç', language: 'Dil'},
      nl: {menuFile: 'Bestand', loginSignIn: 'Inloggen', language: 'Taal'},
      pl: {menuFile: 'Plik', loginSignIn: 'Zaloguj', language: 'Język'},
      it: {menuFile: 'File', loginSignIn: 'Accedi', language: 'Lingua'},
    };
    for (const [loc, expected] of Object.entries(phase4Expected)) {
      const cat = JSON.parse(fs.readFileSync(`static/locales/${loc}.json`, 'utf8'));
      assert.deepEqual(Object.keys(cat).sort(), Object.keys(en).sort(), `Phase 4: ${loc}.json has exactly the same keys as en.json (parity)`);
      assert.equal(cat['brand.marker'], 'YO', `Phase 4: ${loc} keeps the YO brand marker`);
      assert.equal(cat['menu.file'], expected.menuFile, `Phase 4: ${loc} translates the File menu label`);
      assert.equal(cat['login.signIn'], expected.loginSignIn, `Phase 4: ${loc} translates the login sign-in label`);
      assert.equal(cat['language.switcher'], expected.language, `Phase 4: ${loc} translates the language switcher label`);
      assert.ok(cat['pref.appearance.file_explorer_font_size.label'].includes('{name}'), `Phase 4: ${loc} preserves the {name} placeholder`);
      assert.ok(cat['yoagent.files'].includes('{count}') && cat['yoagent.files'].includes('{added}') && cat['yoagent.files'].includes('{removed}'), `Phase 4: ${loc} preserves count/added/removed placeholders`);
      assert.ok(cat['yoagent.updated.wrap'].includes('{rel}'), `Phase 4: ${loc} preserves the {rel} placeholder`);
      assert.notEqual(cat['yoagent.prompt.answerLanguage'], en['yoagent.prompt.answerLanguage'], `Phase 4: ${loc} sets a localized YO!agent answer-language directive`);
    }
  });

  test('topbar language button survives passive background topbar renders while focused', () => {
    const api = loadYolomux('', ['1']);
    api.renderSessionButtonsForTest({force: true});
    const root = api.sessionButtonsForTest();
    const button = root.querySelector('.topbar-language');
    assert.ok(button, 'language button is rendered');
    api.setDocumentActiveElementForTest(button);
    assert.equal(api.topbarControlIsActiveForTest(), true, 'focused language button is treated as active topbar control');

    api.renderSessionButtonsForTest();

    assert.equal(root.querySelector('.topbar-language'), button, 'passive render preserves the focused language button node');
    assert.equal(api.pendingSessionButtonsRenderForTest(), true, 'passive render records a pending topbar refresh');

    api.setDocumentActiveElementForTest(null);
    api.renderSessionButtonsForTest();

    assert.notEqual(root.querySelector('.topbar-language'), button, 'unfocused passive render can rebuild the topbar');
    assert.equal(api.pendingSessionButtonsRenderForTest(), false, 'unfocused passive render clears pending state');
  });

  test('topbar language blur flushes a deferred topbar render once focus leaves', () => {
    const api = loadYolomux('', ['1']);
    api.renderSessionButtonsForTest({force: true});
    const root = api.sessionButtonsForTest();
    const button = root.querySelector('.topbar-language');
    api.setDocumentActiveElementForTest(button);
    api.renderSessionButtonsForTest();
    assert.equal(root.querySelector('.topbar-language'), button, 'focused button is preserved before blur');
    assert.equal(api.pendingSessionButtonsRenderForTest(), true, 'pending render is queued before blur');

    api.setDocumentActiveElementForTest(null);
    const blurListeners = button.listeners.get('blur') || [];
    assert.ok(blurListeners.length > 0, 'language button has a blur listener');
    blurListeners.forEach(listener => listener({target: button}));

    assert.notEqual(root.querySelector('.topbar-language'), button, 'blur flush replaces the deferred topbar after focus leaves');
    assert.equal(api.pendingSessionButtonsRenderForTest(), false, 'blur flush clears pending state');
  });

  test('t@7555', () => {
    // Phase 3: relative time renders via Intl.RelativeTimeFormat(activeLocale) (native phrasing).
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    assert.equal(api.relativeTimeFormat(120), '2 minutes ago', 'Phase 3: en relative time is "2 minutes ago" via Intl');
    assert.equal(api.relativeTimeFormat(7200), '2 hours ago', 'Phase 3: hours via Intl');
    assert.equal(api.relativeTimeFormat(172800), '2 days ago', 'Phase 3: days via Intl');
    assert.equal(api.compactRelativeTimeFormat(180), '3 min ago', 'YO!agent recent-agent chips use compact relative time');
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/new Intl\.RelativeTimeFormat\(i18nActiveLocale/.test(src), 'Phase 3: relativeTimeFormat uses Intl.RelativeTimeFormat with the active locale');
    assert.ok(/t\('yoagent\.updated\.wrap', \{rel: relativeTimeFormat\(seconds\)\}\)/.test(src), 'Phase 3: the activity "last updated" line wraps the Intl relative time');
  });

  test('t@7567', () => {
    // tab-move latency. The shape signature ignores tabs order / active item, so a reorder or
    // activate is a "same shape" change that takes the cheap in-place branch (no grid/topbar teardown,
    // no server re-poll).
    const api = loadYolomux('', ['1', '2']);
    const slots = api.defaultLayoutSlots();
    const sigA = api.layoutShapeSignature(slots);
    // Mutating a pane's active item / tabs order does NOT change the shape signature.
    const clone = JSON.parse(JSON.stringify(slots));
    for (const key of Object.keys(clone)) {
      if (key !== '__tree' && clone[key] && Array.isArray(clone[key].tabs)) {
        clone[key].tabs = clone[key].tabs.slice().reverse();
        clone[key].active = clone[key].tabs[0];
      }
    }
    assert.equal(api.layoutShapeSignature(clone), sigA, '#reorder/activate keeps the same shape signature');
    // A different tree TOPOLOGY (a split) yields a different signature -> full rebuild path.
    const split = {'__tree': {split: 'row', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]}, slot1: {tabs: ['1'], active: '1'}, slot2: {tabs: ['2'], active: '2'}};
    assert.notEqual(api.layoutShapeSignature(split), sigA, '#a split changes the shape signature');

    // S1: applyLayoutSlots no longer re-polls the server (refreshTranscripts removed from its body).
    const layoutSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    const applyBody = layoutSrc.slice(layoutSrc.indexOf('function applyLayoutSlots'), layoutSrc.indexOf('function updateActiveSessionParam'));
    assert.equal(/refreshTranscripts\(\);/.test(applyBody), false, '#applyLayoutSlots does not call refreshTranscripts() (no server re-poll on a local layout change)');
    // applyLayoutSlots delegates the shape decision to the shared scheduler.
    const schedulerBody = layoutSrc.slice(layoutSrc.indexOf('function performLayoutRender'), layoutSrc.indexOf('function updateActiveSessionParam'));
    assert.ok(/requestLayoutRender\(\{[\s\S]*?prevShape[\s\S]*?nextShape: layoutShapeSignature\(layoutSlots\)/.test(applyBody), '#applyLayoutSlots sends prev/next shape to the shared layout scheduler');
    assert.ok(/function requestLayoutRender[\s\S]*?pendingLayoutRender = mergePendingLayoutRender/.test(schedulerBody), '#scheduler stores structured deferred render state during drag');
    assert.ok(/layoutRenderCanUseCheap\(renderRequest\)[\s\S]*?syncActivePanelsInPlace\(\)/.test(schedulerBody), '#same-shape changes take the in-place branch');
    assert.ok(/renderSessionButtons\(\);\s*renderPanels\(previousActive/.test(schedulerBody), '#shape changes still fall through to the full rebuild');
    assert.ok(layoutSrc.includes('function syncActivePanelsInPlace'), '#the in-place panel swap exists');
    // fix 6: the markdown preview render is guarded by a path+content signature.
    assert.ok(/container\._previewPath !== path \|\| container\._previewText !== text/.test(layoutSrc), '#fix 6: renderEditorPreviewPane skips re-rendering unchanged markdown');
  });

  test('t@7602', () => {
    // Phase 0: i18n runtime — t()/tPlural() fallback + interpolation, active-over-en, pseudo.
    const api = loadYolomux('', ['1']);
    api.i18nSetCatalogForTest('en', {greet: 'Hi {name}', plain: 'Plain'});
    api.setActiveLocaleForTest('en');
    assert.equal(api.t('greet', {name: 'Al'}), 'Hi Al', 't() interpolates {params}');
    assert.equal(api.t('plain'), 'Plain', 't() returns the catalog value');
    assert.equal(api.t('missing.key'), 'missing.key', 't() falls back to the key when absent (never blank)');
    api.i18nSetCatalogForTest('en', {'files.one': '{count} file', 'files.other': '{count} files'});
    assert.equal(api.tPlural('files', 1), '1 file', 'tPlural picks the one category');
    assert.equal(api.tPlural('files', 3), '3 files', 'tPlural picks the other category');
    api.i18nSetCatalogForTest('en', {x: 'English'});
    api.i18nSetCatalogForTest('zz', {x: 'Zzz'});
    api.setActiveLocaleForTest('zz');
    assert.equal(api.t('x'), 'Zzz', 'active locale wins over the en fallback');
    assert.equal(api.t('y'), 'y', 'missing-in-active falls through en to the key');
  });

  test('t@7620', () => {
    // Phase 0: the Preferences General section + section titles render through t(); under the
    // en-XA pseudo-locale every extracted label is accented/padded, with no plain-English leakage.
    const api = loadYolomux('', ['1']);
    const enXA = JSON.parse(fs.readFileSync('static/locales/en-XA.json', 'utf8'));
    api.i18nSetCatalogForTest('en-XA', enXA);
    api.setActiveLocaleForTest('en-XA');
    const html = api.preferencesPanelHtmlForTest('');
    assert.ok(html.includes(enXA['pref.section.general']), 'pseudo-locale section title renders');
    assert.ok(html.includes(enXA['pref.general.auto_focus.label']), 'pseudo-locale General field label renders');
    assert.ok(html.includes(enXA['pref.general.language.help']), 'pseudo-locale field help renders');
    assert.ok(html.includes(enXA['pref.searchButton']), 'pseudo-locale Preferences search button renders');
    assert.equal(html.includes('Auto-focus active pane'), false, 'no plain-English General field label leaks under the pseudo-locale');
    // Phase 0 (extraction complete): every preference section's fields are i18n-keyed, so the
    // pseudo-locale accents them and NO plain-English label/help from any section leaks through.
    for (const key of [
      'pref.appearance.theme.label', 'pref.appearance.terminal_theme.help',
      'pref.appearance.date_time_hour_cycle.label', 'pref.appearance.font_sizes.note',
      'pref.performance.latency_refresh_ms.label', 'pref.performance.event_log_refresh_ms.label',
      'pref.performance.server_event_poll_ms.label', 'pref.performance.server_background_file_event_poll_ms.label',
      'pref.performance.server_directory_event_poll_ms.label',
      'pref.performance.tabber_activity_refresh_ms.label',
      'pref.editorScheme.group.dark',
      'pref.notifications.throttle_seconds.label',
      'pref.terminal_editor.scrollback.label', 'pref.uploads.max_bytes.label',
      'pref.yoagent.backend.label', 'pref.yoagent.claude_model.label',
      'pref.yoagent.codex_model.label', 'pref.yolo.dry_run.label',
    ]) {
      assert.ok(html.includes(enXA[key]), `pseudo-locale renders ${key}`);
    }
    for (const englishLeak of [
      'Global appearance', 'Editor/Terminal font sizes are in Terminal / Editor.', 'Client pull: latency ping', 'Notification throttle',
      'Terminal scrollback', 'Upload size cap', 'YO!agent backend', 'Dry run',
    ]) {
      assert.equal(html.includes(englishLeak), false, `no plain-English "${englishLeak}" leaks under the pseudo-locale`);
    }
  });

  test('t@7654', () => {
    // zh-Hant + zh-Hans catalogs localize the WHOLE Preferences panel, and the language select offers
    // both endonym-labeled in product-priority order.
    const api = loadYolomux('', ['1']);
    // The select offers the two Chinese options in their own script, Traditional listed before Simplified.
    const selectHtml = api.preferencesPanelHtmlForTest('language');
    assert.ok(selectHtml.includes('<option value="zh-Hant"'), 'language select offers Traditional Chinese');
    assert.ok(selectHtml.includes('<option value="zh-Hans"'), 'language select offers Simplified Chinese');
    assert.ok(selectHtml.includes('>繁體中文</option>') && selectHtml.includes('>简体中文</option>'), 'Chinese options use endonym labels');
    assert.ok(selectHtml.indexOf('value="zh-Hant"') < selectHtml.indexOf('value="zh-Hans"'), 'Traditional Chinese is listed before Simplified');
    for (const locale of ['zh-Hant', 'zh-Hans']) {
      const catalog = JSON.parse(fs.readFileSync(`static/locales/${locale}.json`, 'utf8'));
      // Same key set as English (the build enforces this; assert it here too).
      assert.deepStrictEqual(new Set(Object.keys(catalog)), new Set(Object.keys(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')))), `${locale} has the same keys as en`);
      api.i18nSetCatalogForTest(locale, catalog);
      api.setActiveLocaleForTest(locale);
      const zhHtml = api.preferencesPanelHtmlForTest('');
      assert.ok(zhHtml.includes(catalog['pref.appearance.theme.label']), `${locale} renders the localized global-theme label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.date_time_hour_cycle.label']), `${locale} renders the localized date/time clock label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.active_color.label']), `${locale} renders the localized Active color label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.active_color.help']), `${locale} renders the localized Active color help`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.separator_color.label']), `${locale} renders the localized separator color label`);
      assert.ok(zhHtml.includes(catalog['pref.appearance.separator_color.help']), `${locale} renders the localized separator color help`);
      for (const key of ['blue', 'green', 'orange', 'purple', 'white', 'yellow']) {
        assert.ok(zhHtml.includes(catalog[`pref.appearance.active_color.${key}`]), `${locale} renders the localized Active color ${key} choice`);
      }
      assert.ok(zhHtml.includes(catalog['pref.general.startup_tips.label']), `${locale} renders the localized Startup Tips label`);
      assert.ok(zhHtml.includes(catalog['pref.general.startup_tips.help']), `${locale} renders the localized Startup Tips help`);
      assert.ok(zhHtml.includes(catalog['pref.section.yolo']), `${locale} renders the localized YOLO section title`);
      assert.ok(zhHtml.includes(catalog['pref.path.rules']), `${locale} renders the localized YOLO rules path label`);
      assert.ok(zhHtml.includes(catalog['pref.performance.auto_approve_interval_seconds.label']), `${locale} renders the localized YOLO worker poll label`);
      assert.ok(zhHtml.includes(catalog['pref.yolo.rule_file_path.help']), `${locale} renders the localized YOLO rule-file help`);
      assert.ok(zhHtml.includes(catalog['pref.section.yoagent']), `${locale} renders the localized YO!agent section title`);
      // Brand glyph: YO!agent localizes to 優!助手 / 优!助手 (no plain "YO!agent" section title leak).
      assert.ok(catalog['pref.section.yoagent'].includes(locale === 'zh-Hant' ? '優!助手' : '优!助手'), `${locale} applies the YO!agent brand glyph`);
      // The YO marker glyph localizes to 優 / 优 (the catalog value the marker renders via t('brand.marker')).
      assert.equal(catalog['brand.marker'], locale === 'zh-Hant' ? '優' : '优', `${locale} marker glyph`);
      // #52: the wordmark glyphs localize to 優樂 / 优乐.
      assert.equal(catalog['brand.wordmark.yo'], locale === 'zh-Hant' ? '優' : '优', `${locale} wordmark YO glyph`);
      assert.equal(catalog['brand.wordmark.lo'], locale === 'zh-Hant' ? '樂' : '乐', `${locale} wordmark LO glyph`);
      // The user's request: YO!info -> 優!資料 / 优!资料, YO!agent -> 優!助手 / 优!助手.
      assert.equal(catalog['brand.tab.info'], locale === 'zh-Hant' ? '優!資料' : '优!资料', `${locale} YO!info tab label`);
      assert.equal(catalog['brand.tab.agent'], locale === 'zh-Hant' ? '優!助手' : '优!助手', `${locale} YO!agent tab label`);
      const localizedDate = api.sessionFileTimeText(Date.UTC(2026, 5, 4, 19, 17) / 1000);
      assert.equal(localizedDate.includes('Jun'), false, `${locale} Finder date does not leak the English month name`);
      assert.ok(/[年月日]/.test(localizedDate), `${locale} Finder date uses Chinese date wording`);
      assert.equal(/上午|下午|[AP]\.?M\.?/i.test(localizedDate), false, `${locale} Finder date defaults to a 24-hour clock`);
      assert.ok(/\d{2}:\d{2}/.test(localizedDate), `${locale} Finder date includes a two-digit clock`);
      assert.equal(api.fileExplorerTreeDateModeLabel('none'), catalog['finder.dateMode.none'], `${locale} Finder/Differ None date-mode button is localized`);
      assert.equal(api.fileExplorerTreeDateModeButtonLabel('none'), catalog['finder.dateMode.date'], `${locale} Finder/Differ None date-mode button shows localized crossed-out Date`);
      assert.equal(api.fileExplorerTreeDateModeLabel('date'), catalog['finder.dateMode.date'], `${locale} Finder/Differ Date date-mode button is localized`);
      assert.equal(api.fileExplorerTreeDateModeLabel('relative'), catalog['finder.dateMode.relative'], `${locale} Finder/Differ Ago date-mode button is localized`);
      assert.equal(api.fileExplorerTreeDateModeTitle('relative').includes('None'), false, `${locale} Finder/Differ date-mode tooltip does not leak English None`);
      assert.equal(api.fileExplorerTreeDateModeTitle('relative').includes('Date display'), false, `${locale} Finder/Differ date-mode tooltip does not leak English title text`);
      assert.equal(api.sessionFileRelativeTimeText(1000, 1014), catalog['relative.compact.lessThan15Sec'], `${locale} Finder/Differ sub-15-second Ago text is localized`);
      assert.equal(api.sessionFileRelativeTimeText(1000, 19720), catalog['relative.compact.hour.other'].replace('{count}', '5.2'), `${locale} Finder/Differ compact Ago text is localized`);
      assert.equal(/\bago\b|hrs?|days?|min\b/i.test(api.sessionFileRelativeTimeText(1000, 217000)), false, `${locale} Finder/Differ compact Ago text does not leak English units`);
      assert.equal(api.editorModeLabel('edit'), catalog['editor.mode.edit'], `${locale} editor Edit mode label is localized`);
      assert.equal(api.editorModeLabel('preview'), catalog['editor.mode.preview'], `${locale} editor Preview mode label is localized`);
      assert.equal(api.editorModeLabel('split'), catalog['editor.mode.split'], `${locale} editor Split View mode label is localized`);
      assert.notEqual(api.editorModeLabel('edit'), 'Edit', `${locale} editor Edit mode label does not fall back to English`);
      assert.notEqual(api.editorModeLabel('preview'), 'Preview', `${locale} editor Preview mode label does not fall back to English`);
      assert.notEqual(api.editorModeLabel('split'), 'Split view', `${locale} editor Split View mode label does not fall back to English`);
      // The YOLO-toggle menu labels + the YOLO submenu header use the localized brand glyph (優/优 and
      // 優樂/优乐), not a Latin "YO"/"YOLO" (images #57 / #59).
      const glyph = locale === 'zh-Hant' ? '優' : '优';
      for (const k of ['menu.tmux.yo.on', 'menu.tmux.yo.off', 'menu.tmux.yo.elsewhere', 'menu.tmux.yo.none', 'menu.tmux.yoloSubmenu']) {
        assert.equal(/[A-Za-z]/.test(catalog[k]), false, `${locale} ${k} has no Latin "YO" leak`);
        assert.ok(catalog[k].startsWith(glyph), `${locale} ${k} leads with the localized brand glyph`);
      }
      const yoloSectionStart = zhHtml.indexOf(`data-preference-section="${catalog['pref.section.yolo']}"`);
      const yoloSectionEnd = zhHtml.indexOf('data-preference-section="', yoloSectionStart + 1);
      const yoloSectionHtml = zhHtml.slice(yoloSectionStart, yoloSectionEnd >= 0 ? yoloSectionEnd : undefined);
      assert.ok(yoloSectionStart >= 0, `${locale} can isolate the localized YOLO Preferences section`);
      assert.equal(yoloSectionHtml.includes('YOLO'), false, `${locale} YOLO Preferences section does not leak Latin YOLO`);
      api.setClientSettingsPayloadPatchForTest({mtime_ns: 1000000000});
      assert.equal(api.settingsLoadedAgeText(1123), catalog['pref.status.loadedSeconds'].replace('{count}', '0'), `${locale} Preferences loaded age is localized`);
      api.setClientSettingsPayloadPatchForTest({mtime_ns: 0});
      // #54: the System theme option is bilingual (localized + "/System") so the OS-following option is
      // unambiguous in any locale; Dark/Light stay fully localized.
      assert.ok(catalog['pref.appearance.theme.system'].endsWith('/System'), `${locale} System theme option is bilingual`);
      assert.equal(catalog['pref.appearance.theme.dark'].includes('/'), false, `${locale} Dark theme option stays fully localized`);
      for (const englishLeak of [
        'Global appearance',
        'Upload size cap',
        'Terminal scrollback',
        'Startup Tips',
        'Show one small Tip',
        'Theme color',
        'Deep ocean blue',
        'Envy green',
        'Blood orange',
        'Royal violet',
        'Moon white',
        'Solar gold',
        'YOLO rules',
        'YOLO worker poll interval',
        'Use the supplied',
        'Reply in Markdown',
        'Default shape:',
        'Use the live AI agent activity',
        'You are YO!agent',
        ['autonomous command', 'sending tools'].join('-'),
      ]) {
        assert.equal(zhHtml.includes(englishLeak), false, `${locale}: no plain-English "${englishLeak}" leaks`);
      }
      for (const catalogKey of ['events.title', 'meta.refreshTitle', 'status.selectPaneForImagePaste', 'status.yoloLoading', 'yolo.buttonOnForSession', 'yolo.buttonOffForSession', 'yolo.buttonOwnedBy']) {
        assert.equal(/YOLO/.test(catalog[catalogKey]), false, `${locale}: ${catalogKey} uses the localized YOLO brand`);
      }
    }
  });

  test('t@7714', () => {
    // "Language" is the FIRST General preference and its label is "Language" (not "UI language").
    const api = loadYolomux('', ['1']);
    const enCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    assert.equal(enCatalog['pref.general.language.label'], 'Language', '#51: the language label reads "Language"');
    api.setActiveLocaleForTest('en');
    const generalHtml = api.preferencesPanelHtmlForTest('');
    assert.ok(generalHtml.includes('data-setting-path="general.language"'), '#51: the language field is present');
    assert.ok(generalHtml.indexOf('data-setting-path="general.language"') < generalHtml.indexOf('data-setting-path="general.auto_focus"'), '#51: the language field is the first General row (before auto-focus)');
  });

  test('t@7725', () => {
    // startup helper tips are a persisted General preference, rotate serially through
    // localStorage, use the shared toast path, and do not render for readonly users.
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    const tips = api.startupHelperCatalog();
    assert.equal(tips.length, 14, 'startup helper catalog includes the initial tip set');
    assert.ok(tips.some(tip => tip.title === 'Drag files into terminals'), 'startup helper catalog includes file drag/drop');
    assert.ok(tips.some(tip => tip.title === 'Ask YO!agent for direction'), 'startup helper catalog includes YO!agent guidance');
    assert.ok(tips.some(tip => tip.title === 'Review agent changes'), 'startup helper catalog includes Differ');
    assert.equal(api.readStartupHelperIndex(tips.length), 0, 'startup helper index defaults to first tip');
    api.writeStartupHelperIndex(15);
    assert.equal(api.readStartupHelperIndex(tips.length), 1, 'startup helper index wraps by catalog length');
    const generalHtml = api.preferencesPanelHtmlForTest('');
    assert.ok(generalHtml.includes('data-setting-path="general.startup_tips"'), 'Startup Tips setting renders in General');
    assert.ok(generalHtml.includes('Startup Tips'), 'Startup Tips setting uses Tips wording');
    assert.ok(generalHtml.indexOf('data-setting-path="general.auto_focus"') < generalHtml.indexOf('data-setting-path="general.startup_tips"'), 'Startup Tips setting follows Auto-focus');
    const src = fs.readFileSync('static_src/js/yolomux/20_layout_state.js', 'utf8');
    assert.ok(src.includes("if (readOnlyMode || !startupHelpersEnabled) return null;"), 'startup helper does not render in readonly or disabled mode');
    assert.ok(src.includes("writeStartupHelperIndex((index + 1) % tips.length)"), 'startup helper advances the localStorage index when shown');
    assert.ok(src.includes("showStartupHelperTip({manual: true})"), 'Next tip action shows the next helper');
    assert.ok(src.includes("saveSettingsPatch(settingPatch('general.startup_tips', false))"), 'Turn off forever persists the General setting');
    assert.ok(src.includes('startupHelperPromptTitle(index, tips.length, tip)'), 'startup helper title includes tip number, total, and action prompt');
    assert.ok(src.includes('container: displayToastContainer(focusedPanelItem)'), 'startup helper renders in the focused pane toast stack, below pane tabs');
    assert.equal(src.includes("startupHelper.action.hide"), false, 'startup helper relies on the toast X instead of a duplicate Hide action');
    assert.ok(src.includes('actions: [navAction, offAction]'), 'startup helper actions are nav plus Turn off Tips forever');
    assert.ok(src.includes("startupHelperAction('<', () => showRelativeTip(-1)"), 'startup helper has a previous-tip arrow control');
    assert.ok(src.includes("startupHelperAction('>', () => showRelativeTip(1)"), 'startup helper has a next-tip arrow control');
    assert.ok(src.includes('countdownMs: 45000'), 'Startup Tips stay visible for 45 seconds');
    const helperStart = src.indexOf('function showStartupHelperTip');
    const helperEnd = src.indexOf('function scheduleStartupHelperTip');
    assert.ok(helperStart >= 0 && helperEnd > helperStart, 'startup helper function block is present');
    assert.equal(src.slice(helperStart, helperEnd).includes('.focus('), false, 'startup helper code does not steal focus');
    const helperCss = fs.readFileSync('static_src/css/yolomux/50_terminal_file_tree.css', 'utf8');
    assert.ok(/\.panel-toast-stack \.startup-helper-toast\s*\{[\s\S]*?align-self:\s*flex-end/.test(helperCss), 'startup helper toast is pane-local and right-aligned below the pane tab strip');
    assert.ok(/\.startup-helper-nav\s*\{[\s\S]*?display:\s*inline-flex/.test(helperCss), 'startup helper navigation is a compact arrow group');
    const bootSrc = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
    assert.ok(/installRuntimeIntervals\(\);\s*scheduleStartupHelperTip\(\);/.test(bootSrc), 'startup helper is scheduled after initial boot intervals');
    assert.ok(src.includes("if (location.protocol === 'file:') return;"), 'startup helper is skipped in file:// browser fixtures');
    const settingsSrc = fs.readFileSync('yolomux_lib/settings.py', 'utf8');
    assert.ok(settingsSrc.includes('"startup_tips": True'), 'Startup Tips setting defaults on server-side');
    assert.ok(settingsSrc.includes('"startup_helpers" in incoming'), 'legacy startup_helpers configs migrate to startup_tips');
  });

  test('t@7769', () => {
    // User screenshot 20260608-004: pane tabs should sit tight to the pane border and to each other.
    const tokenCss = fs.readFileSync('static_src/css/yolomux/00_tokens_base.css', 'utf8');
    const css = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
    const popoverCss = fs.readFileSync('static_src/css/yolomux/20_sessions_popovers.css', 'utf8');
    assert.ok(/\.panel-head\s*\{[\s\S]*?padding:\s*2px 1px 0;/.test(css), 'pane tab strip has a 1px left/right edge gap');
    assert.ok(/\.pane-tab\s*\{[\s\S]*?margin:\s*0 1px 0 0;/.test(css), 'pane tabs have a 1px horizontal gap');
    assert.ok(/\.yolomux-dockview \.dv-tabs-and-actions-container\s*\{[\s\S]*?height:\s*auto;[\s\S]*?overflow:\s*visible;/.test(css), 'Dockview pane headers grow vertically when tabs wrap');
    assert.ok(/\.yolomux-dockview \.dv-tabs-container\s*\{[\s\S]*?flex:\s*1 1 auto;[\s\S]*?flex-wrap:\s*wrap;[\s\S]*?inline-size:\s*100%;[\s\S]*?max-inline-size:\s*100%;[\s\S]*?height:\s*auto;[\s\S]*?max-height:\s*none;[\s\S]*?overflow:\s*visible;/.test(css), 'Dockview tab strips wrap at full width instead of narrowing every row for header buttons');
    assert.ok(/\.yolomux-dockview \.dockview-tab-row-break\s*\{[\s\S]*?flex:\s*0 0 100%;[\s\S]*?block-size:\s*0;[\s\S]*?pointer-events:\s*none;/.test(css), 'Dockview inserts a zero-height flex row break so only the first row reserves header-action space');
    assert.equal(/\.yolomux-dockview \.dv-tabs-container\s*\{[\s\S]*?flex-wrap:\s*nowrap/.test(css), false, 'Dockview pane tabs must not force a one-row nowrap strip');
    assert.ok(/\.yolomux-dockview \.dv-tab\s*\{[\s\S]*?flex:\s*0 0 var\(--dockview-tab-inline-size,\s*var\(--pane-tab-width\)\)/.test(css), 'Dockview pane tabs use the configured preference width by default');
    assert.ok(/\.yolomux-dockview \.dv-tab > \.dockview-pane-tab\s*\{[\s\S]*?border-radius:\s*6px 6px 0 0/.test(css), 'Dockview active tabs keep the old rounded top corners');
    assert.ok(/\.yolomux-dockview \.dv-groupview\s*\{[\s\S]*?border:\s*0;/.test(css), 'Dockview groups do not add a fat pane-spacing border around the skinny sash separator');
    assert.ok(/\.yolomux-dockview \.dv-groupview\s*\{[\s\S]*?padding:\s*var\(--pane-split-gap\);/.test(css), 'Dockview groups reserve pane-spacing width inside the active ring so terminals do not render under it');
    assert.ok(/\.yolomux-dockview \.dv-groupview::after\s*\{[\s\S]*?border:\s*var\(--pane-split-gap\) solid color-mix\(in srgb, var\(--panel-ring-color\) var\(--panel-ring-opacity\), transparent\)/.test(css), 'Dockview groups draw the active surround as a pane-spacing-width pseudo-ring without thickening the sash');
    assert.ok(/\.yolomux-dockview \.dv-groupview:has\(\.file-explorer-panel\)\s*\{[\s\S]*?min-width:\s*var\(--file-pane-min-inline-size\)/.test(css), 'Dockview gives the docked Finder/Differ group a real min-width floor');
    assert.ok(/\.yolomux-dockview \.dv-groupview:has\(\.panel\.active-pane\),[\s\S]*?\.dv-groupview:has\(\.panel\.typing-ready-pane\)\s*\{[\s\S]*?--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(css), 'Dockview active/typing panes feed the same active ring color into the group pseudo-ring');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\s*\{[\s\S]*?border-width:\s*0;/.test(css), 'Dockview-mounted panes do not keep the legacy pane-spacing border');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel\.dockview-inner-head-collapsed\s*\{[\s\S]*?grid-template-rows:\s*auto minmax\(0,\s*1fr\)/.test(css), 'Dockview-mounted panes switch from header/detail/content rows to detail/content rows when the inner header is hidden');
    assert.ok(/\.yolomux-dockview \.dockview-panel-content > \.panel > \.panel-head\.dockview-inner-head-hidden,[\s\S]*?\.panel-head\[hidden\]\s*\{[\s\S]*?display:\s*none;/.test(css), 'Dockview hidden inner pane headers really stop rendering instead of leaving a green band');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-inactive-tab > \.dockview-pane-tab:not\(\.active\)\s*\{[\s\S]*?background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(css), 'Dockview inactive tabs match the pane tab-strip background');
    assert.ok(/\.yolomux-dockview \.dockview-pane-header-actions \.pane-drag-handle\s*\{[\s\S]*?cursor:\s*grab/.test(css), 'Dockview exposes a compact whole-pane drag handle in the header actions');
    assert.ok(/\.yolomux-dockview \.dockview-pane-header-actions \.tab\s*\{[\s\S]*?height:\s*min\(18px,\s*var\(--pane-tab-height\)\)/.test(css), 'Dockview header action buttons stay compact instead of growing taller than the tab row');
    assert.ok(/\.yolomux-dockview \.dv-split-view-container > \.dv-sash-container > \.dv-sash,[\s\S]*?\.dv-sash:not\(\.disabled\):hover,[\s\S]*?\.dv-sash:not\(\.disabled\):active\s*\{[\s\S]*?background-color:\s*transparent;/.test(css), 'Dockview sash hit targets stay transparent so only the skinny pseudo-line is visible');
    assert.ok(/\.yolomux-dockview \.dv-sash::before\s*\{[\s\S]*?background:\s*var\(--pane-resizer-bg\)/.test(css), 'Dockview sashes draw the shared skinny pane separator at rest');
    assert.ok(/\.yolomux-dockview \.dv-split-view-container\.dv-horizontal > \.dv-sash-container > \.dv-sash::before\s*\{[\s\S]*?left:\s*calc\(50% - \(var\(--pane-resizer-line-size\) \/ 2\)\)/.test(css), 'Dockview horizontal sashes center the 1px resting separator');
    assert.ok(/\.yolomux-dockview \.dv-split-view-container\.dv-horizontal > \.dv-sash-container > \.dv-sash:hover::before,[\s\S]*?var\(--pane-resizer-hover-line-size\)/.test(css), 'Dockview horizontal sashes thicken only to the shared hover separator size');
    assert.ok(css.includes('--dv-drag-over-border: 2px dashed var(--pane-resizer-hover-bg)'), 'Dockview drag overlays use the configurable pane separator color');
    assert.ok(tokenCss.includes('--tab-insert-preview-width: 24px'), 'tab insertion previews use a large enough between-tabs box to see while dragging');
    assert.ok(/\.grid\.drop-preview::before\s*\{[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\)/.test(css), 'root tab-drag previews use the configurable pane separator color');
    assert.ok(/\.yolomux-dockview \.dv-groupview\.drop-preview::before/.test(css), 'Dockview panes can draw the shared dashed file/tab drop preview');
    assert.ok(/\.pane-tabs\.tab-drop-preview::after\s*\{[\s\S]*?width:\s*var\(--tab-insert-preview-width\);[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\)/.test(css), 'legacy tab insertion previews render as a visible dashed between-tabs box');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-drop-target \.dv-drop-target-selection\.dv-drop-target-left,[\s\S]*?\.dv-drop-target-selection\.dv-drop-target-right\s*\{[\s\S]*?width:\s*var\(--tab-insert-preview-width\) !important;[\s\S]*?border:\s*2px dashed var\(--pane-resizer-hover-bg\) !important/.test(css), 'Dockview tab insertion previews render as a visible dashed between-tabs box instead of a half-tab overlay');
    assert.ok(/\.pane-drag-image\.drag-image\s*\{[\s\S]*?border:\s*2px dotted var\(--pane-resizer-hover-bg\)/.test(popoverCss), 'whole-pane drag preview renders as a dotted box using the shared separator color');
    assert.ok(/\.yolomux-dockview \.dv-tab\.dv-drop-target \.dv-drop-target-selection\.dv-drop-target-right\s*\{[\s\S]*?left:\s*100% !important;[\s\S]*?translateX\(-50%\)/.test(css), 'Dockview right-side tab insertion marker is centered on the target tab edge');
    const dockviewSrc = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*rootBoundaryDropZoneForEvent\(nativeEvent, rect\)[\s\S]*splitSessionAtLayoutBoundary\(rootIntent\.item, rootIntent\.zone, rootIntent\.sourceSlot\)/.test(dockviewSrc), 'Dockview content-edge drops in the root band use the legacy full-span boundary split');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*event\?\.kind !== 'content' && event\?\.kind !== 'edge'/.test(dockviewSrc), 'Dockview edge overlays in the app root band use the bounded YOLOmux root preview instead of the native full-width overlay');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*event\.kind === 'content' && event\.group && !dockviewContentDropCanUseRootBoundary\(nativeEvent, zone\)[\s\S]*return null/.test(dockviewSrc), 'Dockview pane-content drops keep the native local group split unless the pointer is on a root-edge cross-gutter');
    assert.ok(/function dockviewContentDropCanUseRootBoundary\(event, zone\)[\s\S]*const crossSplit = zone === 'left' \|\| zone === 'right' \? 'column' : 'row'[\s\S]*Math\.abs\(pointer - boundary\) <= tolerance/.test(dockviewSrc), 'Dockview root-boundary content drops are limited to the cross-gutter between existing panes');
    assert.ok(/function dockviewContentDropCanUseRootBoundary\(event, zone\)[\s\S]*const tolerance = Math\.max\(48, layoutBoundaryDropBandPx/.test(dockviewSrc), 'Dockview outer-edge drops beside stacked panes use a usable cross-gutter tolerance without stealing normal pane-edge drops');
    assert.ok(/function dockviewPaneContentDropInfo\(event\)[\s\S]*targetSlot[\s\S]*targetRect: layoutSlotScreenRect\(targetSlot\)[\s\S]*function dockviewPaneContentDropIntent\(event\)[\s\S]*dropIntentAllowsSession\(info\.item, info\.intent\)/.test(dockviewSrc), 'Dockview pane-content edge drops are converted to YOLOmux local pane split intents with real target geometry');
    assert.ok(/function dockviewShouldSuppressPaneContentDrop\(event\)[\s\S]*!dropIntentAllowsSession\(info\.item, info\.intent\)[\s\S]*function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewShouldSuppressPaneContentDrop\(event\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview suppresses native previews for invalid pane drops before a dashed box is advertised');
    assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const rootIntent = dockviewRootBoundaryDropIntent\(event\)[\s\S]*const paneIntent = dockviewPaneContentDropIntent\(event\)[\s\S]*splitSessionAtSlot\(paneIntent\.item, paneIntent\.targetSlot, paneIntent\.zone, paneIntent\.sourceSlot\)/.test(dockviewSrc), 'Dockview pane edge drops use splitSessionAtSlot so same-axis splits preserve 1/2 + 1/4 + 1/4 sizing');
    assert.ok(/function dockviewRootBoundaryDropIntent\(event\)[\s\S]*rootBoundaryDropOverDockedFileExplorer\(nativeEvent, zone\)[\s\S]*return null/.test(dockviewSrc), 'Dockview root top/bottom previews defer when the pointer is inside the docked Finder/Differ column');
    assert.ok(/function dockviewPinnedTabCrossPaneViolation\(info\)[\s\S]*info\.createsPane === true[\s\S]*info\.targetSlot && info\.targetSlot !== info\.sourceSlot/.test(dockviewSrc), 'Dockview has one shared pinned-tab rule for cross-pane and new-pane violations');
    assert.ok(/function dockviewTabDropViolatesPinnedPartition\(event\)[\s\S]*dockviewPinnedTabCrossPaneViolation\(info\)[\s\S]*return true/.test(dockviewSrc), 'Dockview tab-strip drops reject pinned tabs that leave their current pane');
    assert.ok(/function dockviewPaneContentDropInfo\(event\)[\s\S]*createsPane: layoutSplitZone\(zone\)[\s\S]*function dockviewPaneContentDropIntent\(event\)[\s\S]*dockviewPinnedTabCrossPaneViolation\(info\.intent\)[\s\S]*return null/.test(dockviewSrc), 'Dockview pane-content drops reject pinned tabs that would split into a new pane');
    assert.ok(/function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewPinnedTabRootBoundaryViolation\(intent\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview root-boundary previews are suppressed for pinned tabs');
    assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const rootIntent = dockviewRootBoundaryDropIntent\(event\)[\s\S]*dockviewPinnedTabRootBoundaryViolation\(rootIntent\)[\s\S]*event\.preventDefault\(\)/.test(dockviewSrc), 'Dockview root-boundary drops do not split pinned tabs into new panes');
    assert.equal(dockviewSrc.includes('dockviewPinnedCrossPane'), false, 'old pinned cross-pane move exception helpers stay removed');
    assert.equal(dockviewSrc.includes('pinnedCrossPanePointerDrop'), false, 'old pinned cross-pane pointer fallback state stays removed');
    assert.ok(/function dockviewTrackRootBoundaryOverlay\(event\)[\s\S]*dockviewShowRootBoundaryPreview\(intent\)[\s\S]*event\.preventDefault\?\.\(\)/.test(dockviewSrc), 'Dockview root-band drags show the bounded YOLOmux preview and suppress the native full-width Dockview overlay');
    assert.ok(dockviewSrc.includes('createRightHeaderActionComponent: () => createDockviewHeaderActionsRenderer()'), 'Dockview renders YOLOmux pane controls in the Dockview header row');
    assert.ok(/function dockviewLayoutToHost[\s\S]*api\.layout\?\.\(width, height\)/.test(dockviewSrc), 'Dockview is explicitly laid out to the host size instead of staying at the default 100px shell');
    assert.ok(dockviewSrc.includes('const DOCKVIEW_MIN_LAYOUT_WIDTH = 640') && dockviewSrc.includes('const DOCKVIEW_MIN_LAYOUT_HEIGHT = 240'), 'Dockview serialized fallback dimensions use functional minimums');
    assert.ok(/function dockviewHostCanAdoptLayout\(host = dockviewLayoutState\.host\)[\s\S]*return width > 1 && height > 1/.test(dockviewSrc), 'Dockview adoption rejects hidden or zero-area hosts');
    assert.ok(/function adoptDockviewLayout\(\)[\s\S]*if \(!dockviewHostCanAdoptLayout\(\)\) return/.test(dockviewSrc), 'Dockview skips adopting snapshots while the host has no measurable area');
    assert.ok(/api\.onDidRemoveGroup\?\.\(group => dockviewHandleRemovedGroup\(group\)\)/.test(dockviewSrc), 'Dockview removed-group events queue Finder/Differ recovery');
    assert.ok(/function dockviewJsonFromLayoutSlots\(slots = layoutSlots\)[\s\S]*Math\.max\(DOCKVIEW_MIN_LAYOUT_HEIGHT[\s\S]*Math\.max\(DOCKVIEW_MIN_LAYOUT_WIDTH/.test(dockviewSrc), 'Dockview JSON snapshots clamp serialized dimensions to functional minimums');
    assert.ok(/function hideDockviewInnerPaneTabs\(panel\)[\s\S]*panel\.classList\.add\('dockview-inner-head-collapsed'\)/.test(dockviewSrc), 'Dockview marks panels whose inner header was hidden so their content row still fills the pane');
    assert.ok(/function preserveDockviewDockedFileExplorerSplit[\s\S]*dockviewLayoutState\.reloadAfterAdoption = true/.test(dockviewSrc), 'Dockview adoption preserves and reapplies the docked Finder root split width');
    assert.ok(/function dockviewInstallFileDropBridge[\s\S]*dockviewHandleFileDragOver[\s\S]*dockviewHandleFileDrop/.test(dockviewSrc), 'Dockview panes bridge Finder/Differ file drags into the shared pane drop behavior');
    assert.ok(/function dockviewHandleFileDrop[\s\S]*openDraggedFilesInEditor\(payload, \{targetSlot: intent\.targetSlot, targetZone: intent\.zone\}\)/.test(dockviewSrc), 'Dockview file drops open dragged files in the intended pane split');
    assert.ok(/function dockviewHandleFileDragOver\(event\)[\s\S]*paneDragPayload\(event\)[\s\S]*paneSwapIntentForEvent\(event, panePayload\.slot\)[\s\S]*showDropPreview\(intent\)/.test(dockviewSrc), 'Dockview host dragover handles whole-pane swap previews separately from tab drags');
    assert.ok(/function dockviewHandleFileDrop\(event\)[\s\S]*paneDragPayload\(event\)[\s\S]*swapPaneSlots\(intent\.sourceSlot, intent\.targetSlot\)/.test(dockviewSrc), 'Dockview host drops swap whole panes when the pane payload is accepted');
    assert.ok(/function paneDragHandleHtml\(item\)[\s\S]*data-pane-drag=/.test(dockviewSrc), 'Dockview header actions include a dedicated pane-drag payload handle');
    assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.dv-tabs-and-actions-container[\s\S]*pane-drag-source[\s\S]*dockviewBeginPanePointerDrag\(event, sourceSlot\)/.test(dockviewSrc), 'Dockview tab-container background starts whole-pane drags without marking the tab container draggable');
    assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.panel-detail-row[\s\S]*syncDragSource\(detail\)/.test(dockviewSrc), 'Dockview pane info/detail rows start the same whole-pane pointer drag as the tab-container background');
    assert.ok(/function dockviewSyncHeaderBackgroundDragSources\(\)[\s\S]*\.file-editor-toolbar[\s\S]*syncDragSource\(editorToolbar\)/.test(dockviewSrc), 'Dockview editor toolbars start the same whole-pane pointer drag as other pane info bars');
    assert.ok(/function dockviewClearTabRowBreaks\(tabsContainer\)[\s\S]*dockview-tab-row-break[\s\S]*node\.remove\(\)/.test(dockviewSrc), 'Dockview clears stale first-row break nodes before each header measurement pass');
    assert.ok(/function dockviewSyncHeaderActionReservations\(\)[\s\S]*preferredTabWidth[\s\S]*--pane-tab-width[\s\S]*availableWidth[\s\S]*--dockview-header-actions-reserved-inline-size[\s\S]*--dockview-tab-inline-size[\s\S]*firstRowCapacity[\s\S]*dockview-tab-row-break[\s\S]*insertBefore\(rowBreak, tabs\[firstRowCapacity\]\)/.test(dockviewSrc), 'Dockview measures right-side actions while keeping the configured tab width and breaking only the first row before the action cluster');
    assert.equal(dockviewSrc.includes('fitWidth'), false, 'Dockview tab fitting must not divide the pane width by tab count');
    assert.ok(/function dockviewTrackPanePointerDrag\(event\)[\s\S]*startPaneDragPreview\(event, state\.sourceSlot\)[\s\S]*moveCustomDragPreview\(event\)/.test(dockviewSrc), 'Dockview pane-background pointer drags show and move the same pane drag preview as native pane drags');
    assert.ok(/function dockviewFinishPanePointerDrag\(event\)[\s\S]*stopCustomDragPreview\(\)[\s\S]*clearDropPreview\(\)/.test(dockviewSrc), 'Dockview pane-background pointer drags remove the pane preview on drop/cancel');
    const dragPreviewSrc = fs.readFileSync('static_src/js/yolomux/60_popovers_tabs.js', 'utf8');
    assert.ok(/const customDragPreviewCleanupEvents = \['drop', 'dragend', 'pointerup', 'mouseup', 'blur', 'visibilitychange'\]/.test(dragPreviewSrc), 'native custom drag previews clean up on drag release and page-cancel paths');
    assert.ok(/function bindCustomDragPreviewListeners\(\)[\s\S]*for \(const target of customDragPreviewEventTargets\(\)\)[\s\S]*target\.addEventListener\?\.\('dragover', moveCustomDragPreview, true\)[\s\S]*target\.addEventListener\?\.\(eventName, stopCustomDragPreview, true\)/.test(dragPreviewSrc), 'native custom drag preview cleanup is bound on both document and window');
    assert.equal(/header\.draggable = draggable/.test(dockviewSrc), false, 'Dockview tab-container background must not become a native draggable ancestor that steals tab drags');
    assert.ok(/api\.onWillDrop\(event => \{[\s\S]*const edgeReorder = dockviewTabEdgeReorderIntent\(event\)[\s\S]*moveSessionToSlot\(edgeReorder\.item, edgeReorder\.targetSlot, edgeReorder\.sourceSlot, edgeReorder\.insertIndex\)/.test(dockviewSrc), 'Dockview manually reorders edge tabs dragged onto their adjacent neighbor');
    assert.ok(/function dockviewInstallTabPointerReorderFallback\(\)[\s\S]*document\.addEventListener\('pointerup', finish, true\)[\s\S]*document\.addEventListener\('mouseup', finish, true\)/.test(dockviewSrc), 'Dockview edge-tab reorder fallback listens to both pointer and mouse release paths');
    assert.ok(/function dockviewFinishTabPointerDrag\(event\)[\s\S]*dockviewTabForPoint[\s\S]*dockviewAdjacentEdgeTabInsertIndex[\s\S]*moveSessionToSlot\(state\.item, targetSlot, targetSlot, currentInsertIndex\)/.test(dockviewSrc), 'Dockview edge-tab pointer fallback reorders against the tab under the release point');
    assert.ok(/\.pane-tab > \.session-popover,\s*\.pane-tab-detached-popover\s*\{[\s\S]*position:\s*fixed/.test(css), 'Dockview tab hover popovers use the shared fixed-position tab popover surface');
    assert.ok(/body\.share-replay-shell \.share-mirror-stage \.app-overlay-root,[\s\S]*body\.share-replay-shell \.share-mirror-stage \.pane-tab-detached-popover\s*\{[\s\S]*position:\s*absolute/.test(css), 'YO!share replay positions detached tab popovers inside the transformed app root instead of the viewer viewport');
    assert.ok(/function bindPaneTabPopover\(tab, session\)[\s\S]*tab\.classList\?\.contains\('dockview-pane-tab'\)[\s\S]*detachPaneTabPopover\(tab, popover\)/.test(fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8')), 'Dockview tab hover popovers detach from the clipped Dockview tab scroller');
    assert.ok(/function preserveDockviewDockedFileExplorerSplit\(next, previous = layoutSlots\)[\s\S]*dockviewLayoutContentSignature\(next\) === dockviewLayoutContentSignature\(previous\)[\s\S]*return/.test(dockviewSrc), 'Dockview lets sash-only Finder/Differ resize updates change the root split pct');
    assert.ok(/function preserveDockviewContentSplitPercentagesAfterDockResize\(nextRoot, previousRoot, nextDocked, previousDocked\)[\s\S]*copyLayoutSplitPercentagesByShape\(nextContent, previousContent\)[\s\S]*reloadAfterAdoption = true/.test(dockviewSrc), 'Dockview Finder/Differ sash resize preserves nested content split percentages while the root pct changes');
    assert.ok(/function copyLayoutSplitPercentagesByShape\(target, source\)[\s\S]*target\.pct = sourcePct[\s\S]*copyLayoutSplitPercentagesByShape\(targetChildren\[index\], sourceChildren\[index\]\)/.test(dockviewSrc), 'Dockview content pct preservation recurses through matching nested split shapes');
    assert.ok(/function dockviewLayoutContentSignature\(slots = layoutSlots\)[\s\S]*nodeSignature[\s\S]*paneSignature/.test(dockviewSrc), 'Dockview compares content/topology separately from split percentages before preserving Finder width');
    const layoutActionSrc = fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8');
    assert.ok(/function layoutNodeScreenRect\(layoutNode\)[\s\S]*\.map\(slot => layoutSlotScreenRect\(slot\)\)/.test(layoutActionSrc), 'Docked Finder preview geometry uses slot screen rects that work for Dockview groups');
    assert.ok(/function layoutSlotScreenRect\(slot\)[\s\S]*\.dockview-panel-content > \.panel\[data-slot=/.test(layoutActionSrc), 'Dockview layout slots can resolve their visible group rectangle');
    assert.ok(/function rootBoundaryDropIntentForEvent\(event\)[\s\S]*rootBoundaryDropOverDockedFileExplorer\(event, zone\)[\s\S]*return null/.test(layoutActionSrc), 'legacy root top/bottom previews also defer inside a docked Finder/Differ column');
    assert.ok(/function fileDropIntentAllowsPayload\(payload, intent\)[\s\S]*dropIntentAllowsSession\(item, intent, \{allowCandidate: true\}\)/.test(layoutActionSrc), 'file drag previews use the same pane/Finder/min-size validator as tab drags');
    assert.ok(/function itemCanSplitSinglePurposePane\(item, intent\)[\s\S]*zone !== 'bottom'[\s\S]*return false[\s\S]*dropIntentHasRoomForItem\(item, intent\)/.test(layoutActionSrc), 'Finder/Differ target panes accept only bottom splits and only when the resulting pane can fit');
    assert.ok(/function dropIntentHasRoomForItem\(item, intent\)[\s\S]*minWidthForLayoutItem\(targetItem\)[\s\S]*targetMinWidth \+ itemMinWidth[\s\S]*targetMinHeight \+ itemMinHeight/.test(layoutActionSrc), 'pane drop previews are suppressed when the target is too small for both resulting panes');
  });

  test('t@7847', () => {
    // Pop-out previews must derive readable light-editor text inside their own document; copied inline
    // aliases like --text/--editor-scheme-fg override the pop-out's editor-theme-light remap.
    const source = [
      'static_src/js/yolomux/90_changes_editor.js',
      'static_src/js/yolomux/93_markdown_preview.js',
      'static_src/js/yolomux/94_preview_renderers.js',
      'static_src/js/yolomux/94_preview_popout.js',
      'static_src/js/yolomux/95_codemirror_editor.js',
    ].map(file => fs.readFileSync(file, 'utf8')).join('');
    const start = source.indexOf('function previewPopoutVariableStyle()');
    const end = source.indexOf('function previewPopoutToolbarHtml()');
    assert.ok(start >= 0 && end > start, 'previewPopoutVariableStyle exists');
    const variableBlock = source.slice(start, end);
    assert.equal(variableBlock.includes("'--text'"), false, 'preview pop-out does not copy --text inline');
    assert.ok(variableBlock.includes("['--editor-scheme-fg', '--popout-editor-scheme-fg']"), 'preview pop-out aliases active editor text instead of copying it onto --text');
    assert.ok(variableBlock.includes("'--code-keyword'") && variableBlock.includes("'--code-control'") && variableBlock.includes("'--code-string'"), 'preview pop-out copies syntax token variables for highlighted fenced code');
    assert.ok(source.includes('.file-preview-popout-window.editor-theme-light .markdown-body pre'), 'preview pop-out has light-theme code block rules outside .file-editor-content');
    assert.ok(source.includes('.file-preview-popout-window .markdown-body'), 'preview pop-out sets readable body text in its standalone document');
    assert.ok(/\.file-preview-popout-title\s*\{[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto minmax\(0,\s*1fr\)/.test(source), 'preview pop-out top bar uses left/title, centered font, and right theme zones');
    assert.ok(/\.file-preview-popout-title\s*\{[\s\S]*position:\s*fixed[\s\S]*z-index:\s*1000/.test(source), 'preview pop-out top bar stays fixed above the preview body while scrolling');
    assert.ok(/\.file-preview-popout-shell\s*\{[\s\S]*width:\s*100%[\s\S]*padding:\s*64px 24px 36px/.test(source), 'preview pop-out shell uses the full window width and reserves space below the fixed top bar');
    assert.equal(/\.file-preview-popout-shell\s*\{[\s\S]*width:\s*min\(/.test(source), false, 'preview pop-out content is not capped at a fixed desktop width');
    assert.ok(/<span class="file-preview-popout-title-path">[\s\S]*\$\{previewPopoutToolbarHtml\(\)\}/.test(source), 'preview pop-out header renders the path before the shared pop-out toolbar controls');
    assert.ok(/function previewPopoutToolbarHtml\(\)[\s\S]*file-editor-preview-font-panel[\s\S]*class="file-editor-theme-panel" data-preview-popout-theme/.test(source), 'preview pop-out toolbar renders font selector before the theme selector');
    assert.ok(/updateEditorThemeButton\(themeButton, \{includeVanilla: true\}\)/.test(source), 'preview pop-out theme selector includes vanilla mode');
    assert.ok(/cycleEditorThemeMode\(\{includeVanilla: true\}\)/.test(source), 'preview pop-out theme click cycles dark/light/vanilla');
    assert.ok(source.includes('min-width: 66px;') && source.includes('width: auto;'), 'preview pop-out theme selector leaves room for the visible Dark/Bright/Vanilla label');
    assert.ok(fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8').includes('.file-editor-theme-panel.theme-with-label::after'), 'preview theme selector renders the current mode label instead of hiding vanilla in the tooltip');
    assert.ok(source.includes('applyMarkdownFenceFallbackHighlight(block);'), 'markdown fenced code falls back to editor syntax highlighting when hljs lacks the language');
    assert.ok(source.includes("window.open(`/preview-popout?path=${encodeURIComponent(path)}`"), 'preview pop-out opens a same-origin URL instead of about:blank');
    assert.ok(/'editor-popout-preview': \(\) => \{[\s\S]*if \(openFilePreviewPopout\(path, panel\)\) \{[\s\S]*setFileEditorViewMode\(path, 'edit', item\);[\s\S]*renderFileEditorPanel\(panel, item\);/.test(source), 'pressing Pop-out opens the preview window and returns the in-pane editor to Edit mode');
    assert.ok(/function openFilePreviewPopout\(path, panel = null\)[\s\S]*return true;[\s\S]*return false;/.test(source), 'preview pop-out open path reports whether a pop-out was actually opened or focused');
    assert.ok(/function bumpFilePreviewPopoutGeneration\(path\)[\s\S]*record\.previewGeneration[\s\S]*function filePreviewPopoutGenerationMatches\(path, previewWindow, generation\)[\s\S]*record\.window === previewWindow && record\.previewGeneration === generation/.test(source), 'async preview pop-out snapshots are generation-guarded so stale Mermaid renders cannot overwrite newer content');
    assert.ok(/function writeFilePreviewPopoutWhenReady\(path, previewWindow, text\)[\s\S]*renderedPreviewSnapshot\(path, text\)[\s\S]*renderedPreviewSnapshotAsync\(path, text\)[\s\S]*filePreviewPopoutGenerationMatches\(path, previewWindow, generation\)/.test(source), 'preview pop-out writes an immediate snapshot and then a completed async snapshot through the same dispatch');
    assert.ok(/previewWindow\._yolomuxPreviewControlsCleanup[\s\S]*bind\(previewWindow, 'scroll', syncScroll\)[\s\S]*bind\(previewWindow, 'wheel', scheduleScrollSync\)[\s\S]*bind\(scroller, 'scroll', syncScroll\)[\s\S]*bind\(scroller, 'wheel', scheduleScrollSync\)/.test(source), 'preview pop-out window and scrolling element sync immediately on scroll and schedule next-frame sync on wheel without stale document listeners');
    assert.ok(/function scrollSyncTargetPosition\(from, to, axis = 'top'\)[\s\S]*const edgeSnap = Math\.max\(2, Math\.ceil\(sourceClient \* 0\.01\)\);[\s\S]*if \(maxTo <= 0 \|\| current <= edgeSnap\) return 0;[\s\S]*if \(maxFrom <= edgeSnap \|\| current >= maxFrom - edgeSnap\) return maxTo;[\s\S]*const sourceCenter = Math\.min\(maxFrom, current\) \+ \(sourceClient \/ 2\);[\s\S]*return Math\.min\(maxTo, Math\.max\(0, target\)\);/.test(source), 'pop-out scroll sync aligns viewport centers with fractional precision and explicit edge snaps');
    assert.ok(/function syncFilePreviewPopoutFromPanel[\s\S]*syncScrollPositionByRatio\(from, scroller\)/.test(source), 'editor-to-popout scroll sync uses the shared proportional mapper');
    assert.ok(/function syncFilePreviewPopoutScroll[\s\S]*syncScrollPositionByRatio\(scroller, editorScroller\)[\s\S]*syncScrollPositionByRatio\(scroller, previewPane\)/.test(source), 'popout-to-editor scroll sync uses the shared proportional mapper');
    assert.ok(/function fileEditorSourceElement\(panel, source\)\s*\{[\s\S]*fileEditorPanelMode\(panel\) === 'diff'[\s\S]*return null/.test(source), 'Differ views are not preview-scroll sources');
    assert.ok(/function syncFilePreviewPopoutScroll[\s\S]*mode !== 'diff' && editorScroller[\s\S]*syncScrollPositionByRatio\(scroller, editorScroller\)/.test(source), 'preview pop-out scrolling does not drive Differ editors');
    assert.ok(/function scheduleFilePreviewPopoutScrollSync\(path, previewWindow, options = \{\}\)[\s\S]*requestAnimationFrame\(run\)/.test(source), 'pop-out wheel/scroll sync is coalesced through requestAnimationFrame for smooth trackpad deltas');
    assert.ok(/function syncFileEditorInPaneSplitScroll\(host, source\)[\s\S]*return syncScrollPositionByRatio\(from, to\);/.test(source), 'split Preview scroll sync uses the same fractional center/edge mapper as pop-out preview');
    const splitSyncStart = source.indexOf('function syncFileEditorInPaneSplitScroll');
    const splitSyncBody = source.slice(splitSyncStart, source.indexOf('\nfunction ', splitSyncStart + 1));
    assert.equal(/previewSourceLineForScroll|scrollPreviewToSourceLine|scrollIntoView/.test(splitSyncBody), false, 'split Preview scroll sync does not jump by source-line anchors');
    assert.ok(/function fileEditorScrollSyncBlocked\(panel, source = ''\)[\s\S]*panel\?\._splitScrollSource !== source/.test(source), 'split Preview scroll guard suppresses only the opposite/programmatic side');
    assert.ok(/function setFileEditorScrollSyncGuardForSource\(source, \.\.\.panels\)[\s\S]*panel\._splitScrollSource = source \|\| ''/.test(source), 'split Preview scroll guard records the active driver pane');
    assert.ok(/function scheduleFileEditorSplitScrollSync\(host, source\)[\s\S]*host\._splitScrollPendingSource = source[\s\S]*requestAnimationFrame\(run\)/.test(source), 'split Preview scroll sync is coalesced through requestAnimationFrame for large-document trackpad deltas');
    assert.ok(/addEventListener\('scroll', \(\) => \{[\s\S]*scheduleFileEditorSplitScrollSync\(panel, 'editor'\);[\s\S]*scheduleFileEditorPanelViewStateCapture\(item, panel\);[\s\S]*\}\)/.test(source), 'editor scroll listener uses the scheduled split-preview sync path and records viewport state');
    assert.ok(source.includes("addEventListener('scroll', () => scheduleFileEditorSplitScrollSync(panel, 'preview'))"), 'preview scroll listener uses the scheduled split-preview sync path');
    assert.ok(/function syncFileEditorSplitScroll[\s\S]*syncFilePreviewPopoutsFromPanel\(host, source\)/.test(source), 'editor preview/editor scroll drives open preview pop-outs');
    assert.ok(/function closeFilePreviewPopout\(path\)[\s\S]*filePreviewPopouts\.delete\(path\)[\s\S]*previewWindow\.close\?\.\(\)/.test(source), 'preview pop-out close removes the registry entry and closes the window');
    assert.ok(/function setFileEditorViewMode\(path, mode, item = null\)[\s\S]*mode === 'preview' \|\| mode === 'split'[\s\S]*closeFilePreviewPopout\(path\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'switching to in-editor Preview or Split closes any open pop-out preview for that file');
    assert.ok(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8').includes("if (typeof refreshFilePreviewPopouts === 'function') refreshFilePreviewPopouts();"), 'settings refresh syncs open preview pop-outs');
    const fileReloadSource = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    assert.ok(/function replaceOpenFileStateFromDisk[\s\S]*renderOpenFilePath\(path\);[\s\S]*updateFilePreviewPopout\(path, loaded\.state\.content \|\| ''\)/.test(fileReloadSource), 'external disk reload syncs open preview pop-outs');
    assert.ok(/function replaceOpenFileStateFromDisk[\s\S]*fileEditorTabItemsForPath\(path\)\.map[\s\S]*captureFileEditorPanelViewState\(item, panel\)[\s\S]*renderOpenFilePath\(path\);[\s\S]*restoreFileEditorPanelViewState\(item, panel\)[\s\S]*requestAnimationFrame/.test(fileReloadSource), 'external disk reload preserves per-editor cursor and scroll for every open tab of the path');
    assert.ok(fileReloadSource.includes('function openFileBackgroundReloadShouldDefer(path, state)') && fileReloadSource.includes('openFileBackgroundReloadDeferMs'), 'push/watch reloads are deferred during active editing and immediately after save');
    assert.ok(source.includes('position: static !important;'), 'preview pop-out resets the in-pane absolute preview positioning');
    assert.ok(source.includes('display: block !important;') && source.includes('grid-template-rows: none !important;'), 'preview pop-out resets the app body grid layout');
    assert.ok(source.includes('width: 100% !important;') && source.includes('left: auto !important;'), 'preview pop-out resets split-preview geometry that would clip content to the right half');
  });

  test('t@7900', () => {
    // Preferences order is grouped by how the settings are used: general startup defaults, visual appearance,
    // terminal/editor behavior, notifications, file handling, polling/performance, then agent controls.
    const api = loadYolomux('', ['1']);
    api.setActiveLocaleForTest('en');
    const html = api.preferencesPanelHtmlForTest('');
    const sectionOrder = [...html.matchAll(/data-preference-section="([^"]+)"/g)].map(match => match[1]);
    const expectedOrder = [
      api.t('pref.section.general'),
      api.t('pref.section.appearance'),
      api.t('pref.section.terminal_editor'),
      api.t('pref.section.notifications'),
      api.fileExplorerLabel(),
      api.t('pref.section.uploads'),
      api.t('pref.section.performance'),
      api.t('pref.section.github'),
      api.t('pref.section.yoagent'),
      api.t('pref.section.share'),
      api.t('pref.section.yolo'),
    ];
    assert.deepStrictEqual(sectionOrder, expectedOrder, 'Preferences sections render in the grouped order');
    const yoagentIndex = sectionOrder.indexOf(api.t('pref.section.yoagent'));
    const shareIndex = sectionOrder.indexOf(api.t('pref.section.share'));
    const yoloIndex = sectionOrder.indexOf(api.t('pref.section.yolo'));
    assert.deepStrictEqual([yoagentIndex, shareIndex, yoloIndex], [sectionOrder.length - 3, sectionOrder.length - 2, sectionOrder.length - 1], 'YO!agent, YO!share, and YOLO sections stay adjacent at the end; YO!info has no standalone Preferences settings');
    const sectionHtml = title => {
      const start = html.indexOf(`data-preference-section="${title}"`);
      assert.ok(start >= 0, `${title} section renders`);
      const next = html.indexOf('data-preference-section="', start + 1);
      return next >= 0 ? html.slice(start, next) : html.slice(start);
    };
    assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="general.reload_on_update"'), 'server-version reload prompt is in Notifications');
    assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="general.reload_on_update_auto"'), 'server-version auto-reload is in Notifications');
    assert.equal(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="updates.check_enabled"'), false, 'origin/main update check toggle is removed from Notifications');
    assert.ok(sectionHtml(api.t('pref.section.notifications')).includes('data-setting-path="updates.notify_level"'), 'origin/main update notification threshold is in Notifications');
    const shareHtml = sectionHtml(api.t('pref.section.share'));
    assert.ok(shareHtml.includes('data-setting-path="share.ttl_seconds"'), 'YO!share Preferences exposes the default share lifetime');
    assert.ok(shareHtml.includes('data-setting-path="share.max_viewers"'), 'YO!share Preferences exposes the default viewer cap');
    assert.ok(shareHtml.includes('data-setting-path="share.read_only"'), 'YO!share Preferences exposes the read-only default');
    assert.ok(/type="radio"[^>]*value="http"[^>]*data-setting-path="share\.scheme"[\s\S]*type="radio"[^>]*value="https"[^>]*data-setting-path="share\.scheme"/.test(shareHtml), 'YO!share Preferences exposes http/https protocol defaults');
    assert.equal(sectionHtml(api.t('pref.section.performance')).includes('data-setting-path="general.reload_on_update_auto"'), false, 'server-version auto-reload no longer lives in Performance');
    assert.equal(sectionHtml(api.t('pref.section.performance')).includes('data-setting-path="updates.check_enabled"'), false, 'origin/main update check no longer lives in Performance');
    assert.equal(sectionHtml(api.t('pref.section.yoagent')).includes('data-setting-path="yoagent.refresh_interval_seconds"'), false, 'YO!agent Preferences no longer exposes the background transcript-summary interval');
    const appearanceHtml = sectionHtml(api.t('pref.section.appearance'));
    assert.ok(appearanceHtml.includes('Global appearance'), 'Appearance shows the renamed Global appearance field');
    assert.ok(appearanceHtml.includes('Theme color'), 'Appearance shows the renamed Theme color field');
    assert.ok(appearanceHtml.includes('data-setting-path="general.default_layout"'), 'Default layout is in Appearance');
    assert.ok(/type="radio"[^>]*value="split"[^>]*data-setting-path="general\.default_layout"/.test(appearanceHtml), 'Default layout offers Split');
    assert.ok(appearanceHtml.includes('Single pane') && appearanceHtml.includes('Split') && appearanceHtml.includes('Grid'), 'Default layout labels match View layout labels');
    assert.equal(appearanceHtml.includes('Wall'), false, 'Wall is no longer offered as a default layout choice');
    assert.ok(appearanceHtml.includes('Envy green'), 'Active color Green is labeled Envy green');
    assert.ok(appearanceHtml.includes('Deep ocean blue'), 'Active color Blue is labeled Deep ocean blue');
    assert.ok(appearanceHtml.includes('Blood orange'), 'Active color Orange is labeled Blood orange');
    assert.ok(appearanceHtml.includes('Solar gold'), 'Active color Yellow is labeled Solar gold');
    assert.ok(appearanceHtml.includes('Royal violet'), 'Active color Purple is labeled Royal violet');
    assert.ok(appearanceHtml.includes('Moon white'), 'Active color White is labeled Moon white');
    assert.ok(appearanceHtml.includes('Signal green'), 'Cursor color Green is labeled Signal green');
    assert.ok(appearanceHtml.includes('Laser lime'), 'Cursor color Laser lime is available');
    assert.ok(appearanceHtml.includes('Neon green'), 'Cursor color Neon green is available');
    assert.ok(appearanceHtml.includes('Neon cyan'), 'Cursor color Neon cyan is available');
    assert.ok(appearanceHtml.includes('Neon magenta'), 'Cursor color Neon magenta is available');
    assert.ok(appearanceHtml.includes('Neon orange'), 'Cursor color Neon orange is available');
    assert.ok(appearanceHtml.includes('Electric azure'), 'Cursor color Blue is labeled Electric azure');
    assert.ok(appearanceHtml.includes('Flare orange'), 'Cursor color Orange is labeled Flare orange');
    assert.ok(appearanceHtml.includes('Lightning yellow'), 'Cursor color Yellow is labeled Lightning yellow');
    assert.ok(appearanceHtml.includes('Plasma violet'), 'Cursor color Purple is labeled Plasma violet');
    assert.ok(appearanceHtml.includes('Starlight white'), 'Cursor color White is labeled Starlight white');
    assert.ok(/type="radio"[^>]*value="blue"[^>]*data-setting-path="appearance\.active_color"/.test(appearanceHtml), 'Active color Blue renders as a radio');
    assert.ok(/data-setting-path="appearance\.active_color"[\s\S]*data-setting-path="appearance\.separator_color"[\s\S]*data-setting-path="appearance\.editor_cursor_color"[\s\S]*data-setting-path="appearance\.yolo_rotate_ms"/.test(appearanceHtml), 'Separator and Cursor color sit immediately after Active color in Appearance');
    assert.ok(/type="radio"[^>]*value="blue"[^>]*data-setting-path="appearance\.editor_cursor_color"/.test(appearanceHtml), 'Cursor color Blue renders as a radio');
    const preferencesSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function layoutModePreferenceChoices\(\)\s*\{[\s\S]*layoutModeValues\.map\(value => \(\{value, label: t\(`menu\.view\.layout\.\$\{value\}`\)\}\)\)/.test(preferencesSource), 'Default layout choices derive from the shared View layout modes');
    assert.ok(/function activeColorPreferenceChoices\(\)\s*\{[\s\S]*UI_COLOR_CHOICES\.map\(value => activeColorPreferenceChoice\(value, t\(UI_COLOR_PRESETS\[value\]\.labelKey\)\)\)/.test(preferencesSource), 'Active color choices derive labels from the shared UI color parent');
    assert.ok(/function separatorColorPreferenceChoices\(\)[\s\S]*clientSettingsPayload\?\.choices\?\.\['appearance\.separator_color'\][\s\S]*SEPARATOR_COLOR_CHOICES[\s\S]*\.map\(separatorColorPreferenceChoice\)/.test(preferencesSource), 'Separator color choices sync to the backend allowlist with a local fallback');
    assert.ok(/function cursorColorPreferenceChoices\(\)\s*\{[\s\S]*clientSettingsPayload\?\.choices\?\.\['appearance\.editor_cursor_color'\][\s\S]*CURSOR_COLOR_CHOICES[\s\S]*\.map\(cursorColorPreferenceChoice\)/.test(preferencesSource), 'Cursor color choices sync to the backend allowlist with a local fallback');
    assert.ok(/function cursorColorPreferenceChoice\(value\)\s*\{[\s\S]*preset\?\.cursorLabelKey \? t\(preset\.cursorLabelKey\) : preferenceChoiceLabel\(value\)/.test(preferencesSource), 'Cursor color labels use cursor-specific bright color names from the shared parent');
    assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#3b82f6[\s\S]*--preferences-radio-swatch:#2563eb/.test(appearanceHtml), 'Active color Blue radio shows connected actual dark/light accent swatches');
    assert.ok(appearanceHtml.includes('preferences-setting-note') && appearanceHtml.includes('Editor/Terminal font sizes are in Terminal / Editor.'), 'Appearance shows a note after Finder font size pointing editor/terminal font sizes to Terminal / Editor');
    assert.ok(/data-setting-path="appearance\.file_explorer_font_size"[\s\S]*preferences-setting-note[\s\S]*data-setting-path="appearance\.tab_width"/.test(appearanceHtml), 'Appearance font-size note sits directly after Finder font size');
    assert.ok(/data-setting-path="appearance\.pane_ring_opacity"[^>]*data-setting-type="range"[^>]*min="5"[^>]*max="100"/.test(appearanceHtml), 'Pane ring opacity renders as a 5-100 Appearance slider');
    assert.equal(appearanceHtml.includes('data-setting-path="appearance.inactive_pane_gradient"'), false, 'Inactive pane gradient is removed from Appearance');
    assert.ok(/data-setting-path="appearance\.inactive_pane_opacity"[^>]*data-setting-type="range"[^>]*min="0"[^>]*max="100"/.test(appearanceHtml), 'Inactive pane opacity renders as a 0-100 Appearance slider');
    const appearancePaths = [...appearanceHtml.matchAll(/data-setting-path="([^"]+)"/g)].map(match => match[1]);
    assert.equal(appearancePaths.at(-1), 'appearance.date_time_hour_cycle', '12-hour / 24-hour Date/time clock is the last Appearance item');
    const terminalEditorHtml = sectionHtml(api.t('pref.section.terminal_editor'));
    assert.ok(terminalEditorHtml.includes('data-setting-path="appearance.terminal_theme"'), 'Terminal / Editor follows Appearance and owns terminal/editor-specific controls');
    assert.equal(terminalEditorHtml.includes('data-setting-path="appearance.editor_cursor_color"'), false, 'Cursor color moved out of Terminal / Editor into Appearance');
    assert.ok(/data-setting-path="appearance\.terminal_font_size"[\s\S]*data-setting-path="appearance\.editor_font_size"[\s\S]*data-setting-path="appearance\.preview_font_size"[\s\S]*data-setting-path="terminal_editor\.scrollback"/.test(terminalEditorHtml), 'Terminal / Editor groups Terminal, Editor, and Preview font sizes together before scrollback');
    assert.equal(sectionHtml(api.t('pref.section.general')).includes('data-setting-path="general.default_layout"'), false, 'Default layout no longer lives in General');
    assert.equal(sectionHtml(api.t('pref.section.general')).includes('data-setting-path="general.reload_on_update"'), false, 'Notify on server update no longer lives in General');
    // the GitHub section carries the watched-PRs list field.
    assert.ok(html.includes('data-setting-path="github.watched_prs"'), 'the GitHub section has the watched_prs list field');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['pref.appearance.pane_ring_opacity.help'], 'Percent, 5–100. This is the ring drawn over the ACTIVE content edge; lower values make the green/red pane ring fainter.', 'Pane ring opacity help describes the ACTIVE content-edge ring');
    const settingsRuntimeSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    assert.ok(settingsRuntimeSource.includes("Math.max(5, Math.min(100, numberSetting('appearance.pane_ring_opacity', 75)))"), 'Pane ring opacity runtime clamp allows 5%');
    assert.ok(settingsRuntimeSource.includes("root.setProperty('--pane-active-ring-opacity', `${percent}%`)"), 'The active pane ring opacity follows the 5-100% preference');
    assert.equal(settingsRuntimeSource.includes('Math.max(75, paneRingOpacity)'), false, 'The active pane ring must not force a 75% floor');
    assert.equal(settingsRuntimeSource.includes('inactive_pane_gradient'), false, 'Inactive pane gradient is removed from runtime settings');
    assert.ok(settingsRuntimeSource.includes("applyInactivePaneOpacity(numberSetting('appearance.inactive_pane_opacity', 60))"), 'Inactive pane opacity defaults to 60% in runtime settings');
    assert.ok(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('("appearance", "pane_ring_opacity"): (5, 100)'), 'Pane ring opacity server settings clamp allows 5%');
    assert.equal(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('inactive_pane_gradient'), false, 'Inactive pane gradient is removed from server settings');
    assert.ok(fs.readFileSync('yolomux_lib/settings.py', 'utf8').includes('("appearance", "inactive_pane_opacity"): (0, 100)'), 'Inactive pane opacity server settings clamp is 0-100');
  });

  test('t@7980', () => {
    // the block cursor fills the full monospace cell (width: 1ch), not a fat line.
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/body\.editor-cursor-block[^{]*\.cm-cursor[\s\S]*?\{[\s\S]*?width: 1ch !important;/.test(css), '#122: the block editor cursor is one full character cell wide (1ch)');
  });

  test('t@7986', () => {
    // the Preferences global-reset UI (title, warning, both buttons, per-row Reset) is localized.
    const api = loadYolomux('', ['1']);
    const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHant);
    api.setActiveLocaleForTest('zh-Hant');
    // A non-default value makes the global-reset block render (it is hidden when everything is default).
    api.setClientSettingsPatchForTest({appearance: {ui_font_size: 19}});
    const html = api.preferencesPanelHtmlForTest('');
    assert.ok(html.includes(zhHant['pref.reset.title']), '#115: the global-reset title is localized');
    assert.ok(html.includes(zhHant['pref.reset.all']), '#115: the "Reset all defaults" button is localized');
    assert.ok(html.includes(`aria-label="${zhHant['pref.reset.aria']}"`), '#115: the reset group aria-label is localized');
    assert.ok(html.includes(`>${zhHant['pref.reset.row']}</button>`), '#115: the per-row Reset button is localized');
    // No bare English reset literals leak through.
    assert.ok(!/>Global reset<|>Reset all defaults<|>Continue reset</.test(html), '#115: no English reset literals leak in a non-English locale');
    // Source guard: every reset literal routes through t('pref.reset.*').
    const src = fs.readFileSync('static/yolomux.js', 'utf8');
    for (const key of ['title', 'confirmTitle', 'warning', 'confirmWarning', 'continue', 'cancel', 'all', 'row', 'aria']) {
      assert.ok(src.includes(`t('pref.reset.${key}'`), `#115: reset UI uses t('pref.reset.${key}')`);
    }
  });

  test('t@8008', () => {
    // the menu bar, Modified-files panel, diff-ref, and comparison localize in a non-English
    // locale and leak no bare English; source guards confirm the builders route through t().
    const api = loadYolomux('', ['1']);
    const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHant);
    api.setActiveLocaleForTest('zh-Hant');
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
      repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1, behind: 0, ahead: 1}],
      files: [{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1}],
    });
    const panel = api.fileExplorerChangesPanelHtml();
    // C7: the embedded title now names the session via changes.titleForSession; assert its localized stems
    // surround the session (independent of the session label value).
    const titleStems = zhHant['changes.titleForSession'].split('{session}');
    const escHtml = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    assert.ok(titleStems.every(stem => !stem || panel.includes(escHtml(stem))), '#121/C7: the Modified-files title is localized and names the session');
    assert.ok(panel.includes(`>${zhHant['changes.refresh']}</button>`), '#121: the Modified-files Refresh button is localized');
    // C6/C15 follow-up: the FROM/TO text pickers are inline in each repo's localized comparison sentence
    // (no separate FROM/TO labels); assert the sentence's localized text stems surround the inputs.
    for (const stem of zhHant['diff.comparing'].split(/\{from\}|\{to\}/).map(s => s.trim()).filter(Boolean)) {
      assert.ok(panel.includes(stem), `#121/C15: the inline comparison sentence is localized ("${stem}")`);
    }
    assert.ok(/changes-repo-refs compact[\s\S]*data-diff-ref-from[\s\S]*data-diff-ref-to/.test(panel), '#121/C15: the FROM/TO text pickers are present inline on the repo comparison line');
    assert.ok(panel.includes(`aria-label="${zhHant['diff.ref.from.aria']}"`), '#121: the FROM picker aria-label is localized');
    assert.ok(panel.includes(zhHant['changes.ahead.one'].replace('{count}', '1')), '#121: the Ahead-N-commit meta is localized (tPlural)');
    // No bare English leaks in the localized Modified-files panel.
    assert.ok(!/>Modified files<|>Refresh<|>FROM <|>TO <|Ahead 1 commit|Comparing /.test(panel), '#121: no English leaks in the localized Modified-files panel');
    // Source guards: the menu/changes builders carry no bare English literals (all via t()).
    const appSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    for (const literal of ["menuCommand('Open file'", "menuCommand('Preferences'", "menuCommand('Log out'", "menuCommand('Refresh'", "menuSubmenu('Theme'", "menuCommand('Info Bar'", "menuCommand('No matching tabs'", "'Kill tmux session", "class=\"changes-title\">Modified files<", '>FROM <select', '`Comparing ${esc(from)} to ${esc(to)}`']) {
      assert.equal(appSrc.includes(literal), false, `#121: bare English literal removed: ${literal}`);
    }
    // The pseudo-locale transforms a representative menu key (the completeness signal).
    const enXA = JSON.parse(fs.readFileSync('static/locales/en-XA.json', 'utf8'));
    assert.ok(/[⟦⟧]/.test(enXA['menu.file.openFile']) && !/^Open file$/.test(enXA['menu.file.openFile']), '#121: menu keys are pseudo-localized in en-XA');
  });

  test('t@8050', () => {
    // the default (files-mode) search bar blends matching commands/tabs into the results.
    const api = loadYolomux('', ['1']);
    const prefsLabel = api.itemLabel(api.prefsItemId);
    api.setFileQuickOpenCandidatesForTest('/repo/app', [
      {name: 'notes.py', path: '/repo/app/notes.py', relative_path: 'notes.py'},
    ]);
    api.setCommandPaletteStateForTest('files', prefsLabel);
    assert.ok(api.commandPaletteItems().some(item => item.group === 'Tabs' && item.label === prefsLabel), '#7: a command/tab matching a plain files-mode query is blended in (no > needed)');
    api.setCommandPaletteStateForTest('command', 'notes');
    assert.ok(api.commandPaletteItems().some(item => item.category === 'file' && item.path === '/repo/app/notes.py'), 'DOIT.55: command-mode queries also blend matching file-index results');
    // `>` stays commands-only — no file candidates blended.
    api.setCommandPaletteStateForTest('files', `>${prefsLabel}`);
    assert.ok(!api.commandPaletteItems().some(item => item.path === '/repo/app/notes.py'), '#7: the > prefix stays commands-only');
    // An empty files-mode query must NOT dump the whole command corpus.
    api.setCommandPaletteStateForTest('files', '');
    assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: empty files-mode query shows files only (no command dump)');
    // `@` stays reserved for symbols (no command blend).
    api.setCommandPaletteStateForTest('files', '@thing');
    assert.ok(!api.commandPaletteItems().some(item => item.group === 'Tabs'), '#7: @ stays reserved for symbols');
  });

  // macOS Finder list-view keyboard PARITY. The key->intent map is a PURE function, unit-tested here so the
  // full set of bindings is verified as behavior (not just source shape). Works for Finder AND Differ.
  test('t@8072', () => {
    const api = loadYolomux();
    const I = (key, mods = {}) => api.fileExplorerKeyIntent(key, {shift: !!mods.shift, mod: !!mods.mod, alt: !!mods.alt});
    // move + extend
    assert.equal(I('ArrowDown'), 'move-down', 'Down moves selection');
    assert.equal(I('ArrowUp'), 'move-up', 'Up moves selection');
    assert.equal(I('ArrowDown', {shift: true}), 'extend-down', 'Shift+Down extends');
    assert.equal(I('ArrowUp', {shift: true}), 'extend-up', 'Shift+Up extends');
    assert.equal(I('Home'), 'move-home');
    assert.equal(I('End'), 'move-end');
    assert.equal(I('Home', {shift: true}), 'extend-home');
    assert.equal(I('End', {shift: true}), 'extend-end');
    // expand / collapse / parent / child
    assert.equal(I('ArrowRight'), 'expand', 'Right expands / steps in');
    assert.equal(I('ArrowLeft'), 'collapse', 'Left collapses / steps out');
    // open + enclosing folder
    assert.equal(I('ArrowDown', {mod: true}), 'open', 'Cmd-Down = open');
    assert.equal(I('o', {mod: true}), 'open', 'Cmd-O = open');
    assert.equal(I('O', {mod: true}), 'open');
    assert.equal(I('ArrowUp', {mod: true}), 'enclosing', 'Cmd-Up = enclosing folder');
    // rename / select-all / preview / type-ahead
    assert.equal(I('Enter'), 'rename', 'Return = rename (Finder)');
    assert.equal(I('a', {mod: true}), 'select-all', 'Cmd-A = select all');
    assert.equal(I(' '), 'preview', 'Space = Quick Look preview');
    assert.equal(I('d'), 'typeahead', 'a letter is type-to-select');
    assert.equal(I('R'), 'typeahead');
    // NOT claimed (left for the OS / other shortcuts)
    assert.equal(I('ArrowRight', {mod: true}), null, 'Cmd-Right is not claimed');
    assert.equal(I('Enter', {mod: true}), null);
    assert.equal(I('Enter', {shift: true}), null);
    assert.equal(I('ArrowDown', {alt: true}), null, 'Alt combos not claimed');
    assert.equal(I(' ', {mod: true}), null);
    assert.equal(I('Tab'), null);
    assert.equal(I('Escape'), null);
    assert.equal(I('x', {mod: true}), null, 'Cmd-X is not a Finder nav key here');
  });

  // Source guards: the dispatcher wires each intent to the right live-tree action.
  test('t@8110', () => {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(source.includes('function handleFileExplorerArrowNav('), 'arrow-nav handler exists');
    assert.ok(source.includes('function fileExplorerKeyIntent('), 'pure key->intent map exists');
    assert.ok(/if \(handleFileExplorerArrowNav\(event\)\) return;/.test(source), 'wired into the global keydown after the delete shortcut');
    assert.ok(source.includes('!eventTargetIsFileExplorerSurface(event.target) && !isFileExplorerItem(focusedPanelItem)'), 'gated on the Finder/Differ surface');
    assert.ok(/const finderTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*name: 'finder'/.test(source), 'Finder uses the shared tree interaction controller');
    assert.ok(source.includes('function fileTreeDirectoryExpanded(') && source.includes('function setFileTreeDirectoryExpanded('), 'one shared expand/collapse parent for both surfaces');
    assert.ok(/setFileTreeDirectoryExpanded[\s\S]{0,260}closest\('\.file-explorer-changes-panel'\)[\s\S]{0,220}changesFolderCollapsed[\s\S]{0,220}expandDirectoryRow/.test(source), 'the parent dispatches Differ (changesFolderCollapsed) vs Finder (expandDirectoryRow) — no per-surface key code');
    assert.ok(/finderTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*setExpanded\(row, expanded\)[\s\S]*setFileTreeDirectoryExpanded\(row, path, expanded === true\)/.test(source), 'Right/Left route through the shared controller expand/collapse parent');
    assert.ok(/intent === 'open'/.test(source) && source.includes('openChangedFileInDiff(') && source.includes('openFileInEditor(leadPath, entry)') && source.includes('openFileExplorerManualRoot(leadPath)'), 'open: Differ file -> reusable collapsed diff, file -> editor, Finder folder -> descend');
    assert.ok(/intent === 'enclosing'[\s\S]{0,300}openFileExplorerManualRoot\(parent\)/.test(source), 'Cmd-Up opens the enclosing folder');
    assert.ok(/intent === 'rename'[\s\S]{0,200}beginFileTreeRename\(leadRow, leadPath, entry\)/.test(source), 'Enter renames the lead row (Finder AND Differ)');
    assert.ok(!/openChangeFile !== undefined\) return false/.test(source), 'no Differ-rename exclusion — Differ rows rename too (git mv handles tracked files)');
    assert.ok(/intent === 'preview'[\s\S]{0,300}openFileImagePreview\(leadRow, leadPath, entry\)/.test(source), 'Space previews (Quick Look) the lead file');
    assert.ok(source.includes('expandDirectoryRow(row, fullPath, {manual: true})') && source.includes('collapseDirectoryRow(row, fullPath, {manual: true})'), 'Finder branch of the shared parent still uses expand/collapseDirectoryRow');
    assert.ok(/function sharedTreeChildRow\(rows, row\)[\s\S]*pathIsInsideDirectory\(childId, id\)/.test(source), 'Right steps into the first child when already expanded through the shared parent');
    assert.ok(/function sharedTreeParentRow\(rows, row\)[\s\S]*rows\.find\(item => sharedTreeRowId\(item\) === parent\)/.test(source), 'Left steps to the parent row through the shared parent');
    assert.ok(source.includes('function fileExplorerTypeaheadSelect('), 'type-ahead selection exists');
    assert.ok(source.includes('fileExplorerSelectionLead = fullPath'), 'click/range selection seeds the same lead');
    assert.ok(source.includes('fileTreeRepoPopoverCursor.x + 14'), 'repo-row hover popover anchors to the RIGHT of the cursor');
  });
}

module.exports = {runEditorPreviewSuite};

if (require.main === module) {
  runSuites([runEditorPreviewSuite]);
}

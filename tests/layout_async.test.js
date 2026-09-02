const {
  assert,
  fs,
  UI_PINS,
  vm,
  FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST,
  TestClassList,
  TestStyle,
  testDatasetKeyForAttribute,
  TestElement,
  TestFile,
  TestFormData,
  assertNoStandalonePrBadge,
  assertSingleCiBadge,
  deferredFetch, apiTransportRetirementScenario,
  settingsOverride,
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
} = require('./browser_helpers/layout_test_helper');
const {spawn} = require('node:child_process');
const {EventEmitter} = require('node:events');
const {PassThrough} = require('node:stream');
const {runSuite} = require('./layout_url.test.js');

// The product stamps generated upload names in Pacific on purpose (pacificDateStamp() in
// static_src/js/yolomux/99_terminal_boot.js), because the operator contract is Pacific. Deriving the
// expected stamp from ambient local time instead made every such assertion wrong on any runner whose
// clock is not Pacific — the UTC test container was a different calendar day for the last 7-8 hours of
// each Pacific day. This is the one owner of the expected stamp; keep it timezone-explicit.
function expectedPacificDateStamp() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}${values.month}${values.day}`;
}

// Ambient-clock stamp: what the product would emit if it ever regressed from the explicit Pacific
// formatter back to a bare new Date(). Only the negative control below uses it, to prove the control
// clock really is on a different calendar day than Pacific.
function ambientDateStamp() {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
}

function sseFrame(event, payload) {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function controllableSseResponse() {
  const reads = [];
  const queued = [];
  let cancelled = false;
  let closed = false;
  const settleRead = () => {
    if (!reads.length) return;
    if (queued.length) reads.shift()({done: false, value: queued.shift()});
    else if (closed) reads.shift()({done: true});
  };
  const response = {
    ok: true,
    status: 200,
    statusText: 'OK',
    body: {
      getReader() {
        return {
          read() {
            if (queued.length) return Promise.resolve({done: false, value: queued.shift()});
            if (closed) return Promise.resolve({done: true});
            return new Promise(resolve => reads.push(resolve));
          },
          cancel() {
            cancelled = true;
            closed = true;
            queued.length = 0;
            while (reads.length) reads.shift()({done: true});
            return Promise.resolve();
          },
        };
      },
    },
  };
  return {
    response,
    push(text) {
      queued.push(new TextEncoder().encode(text));
      settleRead();
    },
    close() {
      closed = true;
      while (reads.length) reads.shift()({done: true});
    },
    bindSignal(signal) {
      signal?.addEventListener?.('abort', () => {
        cancelled = true;
      }, {once: true});
    },
    cancelled() { return cancelled; },
  };
}

function summarizedHangingShard(closeOnSignal) {
  const signals = [];
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = signal => {
    signals.push(signal);
    if (signal === closeOnSignal) setImmediate(() => child.emit('close', null, signal));
    return true;
  };
  setImmediate(() => child.stdout.write('layout suite: 1 passed, 0 failed\n'));
  return {child, signals};
}

async function runLayoutAsyncSuite() {
  test('Quick Open SSE parser accepts split event frames and joins data lines', () => {
    const api = loadYolomux('', ['1']);
    const events = [];
    const parser = api.fileQuickOpenSseParserForTest((event, payload) => events.push({event, payload}));
    parser.feed('event: ch');
    parser.feed('unk\ndata: {"files":[{"path":"/repo/');
    parser.feed('a.md"}]');
    parser.feed('}\n\n');
    parser.feed('event: done\ndata: {"files":1}\n');
    parser.finish();
    assert.deepStrictEqual(canonical(events), [
      {event: 'chunk', payload: {files: [{path: '/repo/a.md'}]}},
      {event: 'done', payload: {files: 1}},
    ]);
  });

  await testAsync('Quick Open consumes indexed search chunks and repaints each arrival', async () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/home/test/yolomux.dev']);
    api.setFileQuickOpenCandidatesForTest('/home/test/yolomux.dev', []);
    api.installCommandPaletteFixtureForTest();
    api.setCommandPaletteStateForTest('files', 't5t');
    api.setCommandPaletteQueryForTest('t5t');
    const stream = controllableSseResponse();
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), signal: options.signal});
      stream.bindSignal(options.signal);
      return Promise.resolve(stream.response);
    });
    const renderResults = api.commandPaletteStateForTest().node.querySelector('.command-palette-results');
    renderResults.scrollTop = 240;
    const search = api.refreshFileQuickOpenCandidatesForTest('t5t');
    await flushAsyncWork();
    assert.equal(requests[0].url, '/api/fs/search-stream?root=%2Fhome%2Ftest%2Fyolomux.dev&query=t5t&limit=500');
    stream.push(sseFrame('start', {root: '/home/test/yolomux.dev', query: 't5t'}));
    await flushAsyncWork();
    await flushAsyncWork();
    stream.push(sseFrame('chunk', {files: [{path: '/home/test/yolomux.dev/low/t5t-notes.md', name: 't5t-notes.md', relative_path: 'low/t5t-notes.md'}]}));
    await flushAsyncWork();
    assert.equal(api.fileQuickOpenStateForTest().candidates.length, 1, 'the first chunk is applied before the stream finishes');
    assert.ok(api.commandPaletteStateForTest().node.querySelector('.command-palette-results').innerHTML.includes('notes.md'), 'the first chunk is rendered before the stream finishes');
    stream.push(sseFrame('chunk', {files: [{path: '/home/test/yolomux.dev/t5t.md', name: 't5t.md', relative_path: 't5t.md'}]}));
    await flushAsyncWork();
    assert.equal(renderResults.scrollTop, 240, 'stream repaint preserves the user scroll position');
    assert.equal(api.commandPaletteStateForTest().items[0].label, 't5t.md', 'a later exact match is re-ranked above earlier rows');
    stream.close();
    await flushAsyncWork();
    await search;
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), ['/home/test/yolomux.dev/low/t5t-notes.md', '/home/test/yolomux.dev/t5t.md']);
  });

  await testAsync('Quick Open emits DIS basename matches in the first indexed stream chunk', async () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/home/test/yolomux.dev']);
    api.installCommandPaletteFixtureForTest();
    api.setCommandPaletteStateForTest('files', 'dis');
    api.setCommandPaletteQueryForTest('dis');
    const stream = controllableSseResponse();
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/fs/search-stream?root=%2Fhome%2Ftest%2Fyolomux.dev&query=dis&limit=500');
      return Promise.resolve(stream.response);
    });
    const search = api.refreshFileQuickOpenCandidatesForTest('dis');
    await flushAsyncWork();
    stream.push(sseFrame('chunk', {files: [
      {path: '/home/test/yolomux.dev/notes/DIS-123.md', name: 'DIS-123.md', relative_path: 'notes/DIS-123.md'},
      {path: '/home/test/yolomux.dev/archive/d/i/s/notes.md', name: 'notes.md', relative_path: 'archive/d/i/s/notes.md'},
    ]}));
    await flushAsyncWork();
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), ['/home/test/yolomux.dev/notes/DIS-123.md'], 'a basename match is admitted while a weaker path-fragment row is not');
    assert.equal(api.commandPaletteStateForTest().items[0].label, 'DIS-123.md', 'the basename match is visible immediately');
    stream.close();
    await search;
  });

  test('Quick Open priority is open tab, remembered file, active AI path, then index', () => {
    const api = loadYolomux('', ['1']);
    const openPath = '/repo/open/DIS-open.md';
    const rememberedPath = '/repo/history/DIS-history.md';
    const workingPath = '/repo/current/DIS-current.md';
    const indexedPath = '/repo/other/DIS-indexed.md';
    api.setOpenFileStateForTest(openPath, {kind: 'text', content: 'open', original: 'open', editorTabItems: new Set(['file:' + openPath])});
    api.rememberQuickOpenFileForTest(rememberedPath);
    api.setTranscriptInfoForTest('1', {panes: [{active: true, window_active: true, current_path: '/repo/current', process_label: 'codex', command: 'codex'}], selected_pane: {active: true, current_path: '/repo/current'}});
    api.setFileQuickOpenCandidatesForTest('/repo', [
      {path: openPath, name: 'DIS-open.md', relative_path: 'open/DIS-open.md'},
      {path: rememberedPath, name: 'DIS-history.md', relative_path: 'history/DIS-history.md'},
      {path: workingPath, name: 'DIS-current.md', relative_path: 'current/DIS-current.md'},
      {path: indexedPath, name: 'DIS-indexed.md', relative_path: 'other/DIS-indexed.md'},
    ]);
    api.setCommandPaletteStateForTest('files', 'DIS-');
    api.setCommandPaletteQueryForTest('DIS-');
    const rows = api.commandPaletteRankItems(api.commandPaletteItems(), 'DIS-', {surface: 'files'})
      .filter(item => item.path)
      .map(item => item.path);
    assert.deepStrictEqual(canonical(rows.slice(0, 4)), [openPath, rememberedPath, workingPath, indexedPath], 'Quick Open uses the requested four priority tiers');
    assert.equal(api.quickOpenFileHistoryForTest().length, 1, 'the remembered-file list is separate and bounded');
  });

  test('Quick Open remembers only the newest 100 opened files', () => {
    const api = loadYolomux('', ['1']);
    for (let index = 0; index < 105; index += 1) api.rememberQuickOpenFileForTest(`/repo/history-${index}.md`);
    const history = api.quickOpenFileHistoryForTest();
    assert.equal(history.length, 100, 'file history is capped at 100 entries');
    assert.equal(history[0], '/repo/history-104.md', 'the newest opened file is first');
    assert.equal(history.at(-1), '/repo/history-5.md', 'the oldest entries beyond the cap are discarded');
  });

  test('opening empty Quick Open shows recent files without starting a search', () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.installCommandPaletteFixtureForTest();
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({files: []}));
    });
    api.rememberQuickOpenFileForTest('/repo/older.md');
    api.rememberQuickOpenFileForTest('/repo/newer.md');

    api.openCommandPaletteForTest({mode: 'files'});

    const state = api.commandPaletteStateForTest();
    const recentPaths = state.items.filter(item => item.category === 'file').map(item => item.path);
    assert.equal(recentPaths[0], '/repo/newer.md', 'empty Quick Open lists the newest opened file first');
    assert.equal(recentPaths[1], '/repo/older.md', 'empty Quick Open lists older opened files afterward');
    assert.ok(state.items[0].detail.includes(' · '), 'recent Quick Open rows show their last-opened date and time');
    assert.equal(api.fileQuickOpenStateForTest().loading, false, 'empty Quick Open is not loading');
    assert.equal(state.node.querySelector('.command-palette-status').hidden, true, 'empty Quick Open has no searching status');
    assert.deepStrictEqual(requests, [], 'opening empty Quick Open does not search');
  });

  await testAsync('Quick Open cancels a stale stream and fences its late chunk', async () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/home/test/yolomux.dev']);
    api.installCommandPaletteFixtureForTest();
    api.setFileQuickOpenCandidatesForTest('/home/test/yolomux.dev', []);
    api.setCommandPaletteStateForTest('files', 'old');
    api.setCommandPaletteQueryForTest('old');
    const oldStream = controllableSseResponse();
    const newStream = controllableSseResponse();
    let requestCount = 0;
    api.setFetchForTest((_url, options = {}) => {
      requestCount += 1;
      const stream = requestCount === 1 ? oldStream : newStream;
      assert.ok(options.signal, 'each stream receives the Quick Open AbortController signal');
      stream.bindSignal(options.signal);
      return Promise.resolve(stream.response);
    });
    const oldSearch = api.refreshFileQuickOpenCandidatesForTest('old');
    await flushAsyncWork();
    await flushAsyncWork();
    api.setCommandPaletteQueryForTest('new');
    const newSearch = api.refreshFileQuickOpenCandidatesForTest('new');
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(oldStream.cancelled(), true, `replacing the query cancels the old readable stream: ${JSON.stringify({requestCount, state: api.fileQuickOpenStateForTest()})}`);
    oldStream.push(sseFrame('chunk', {files: [{path: '/home/test/yolomux.dev/stale.md', name: 'stale.md'}]}));
    await flushAsyncWork();
    newStream.push(sseFrame('chunk', {files: [{path: '/home/test/yolomux.dev/current.md', name: 'current.md'}]}));
    newStream.close();
    await flushAsyncWork();
    await Promise.all([oldSearch, newSearch]);
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), ['/home/test/yolomux.dev/current.md']);
  });

  test('Git history commit rows keep the approved field order and responsive retention contract', () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    api.setGitDiffTabStateForTest(item, {hostedRemote: {provider: 'github', base_url: 'https://github.com/owner/project'}});
    const row = api.gitDiffCommitRowForTest(item, {
      sha: 'a'.repeat(40), short: 'a'.repeat(9), authored_at: 1786931640,
      files: 3, added: 42, removed: 11, binary_files: 0,
      author: 'Keiven Chang', subject: 'Record release evidence #123', parents: ['b'.repeat(40)],
    });
    assert.deepStrictEqual([...row.children].map(child => child.className), [
      'git-diff-commit-caret ui-disclosure-triangle',
      'git-diff-commit-sha',
      'git-diff-commit-date',
      'git-diff-commit-changes',
      'git-diff-commit-author',
      'git-diff-commit-description',
    ]);
    assert.equal(row.localName, 'div', 'commit links are never nested inside a button');
    assert.equal(row.getAttribute('aria-expanded'), 'false');
    assert.equal(row.children[1].textContent, 'aaaaaaaaa', 'SHA is always rendered');
    assert.equal(row.children[1].href, `https://github.com/owner/project/commit/${'a'.repeat(40)}`);
    assert.equal(row.querySelector('.git-diff-change-link').href, 'https://github.com/owner/project/pull/123');
    assert.deepStrictEqual([
      row.querySelector('.git-diff-commit-added').textContent,
      row.querySelector('.git-diff-commit-removed').textContent,
    ], ['+42', '-11']);
    assert.ok(row.getAttribute('aria-label').includes('Keiven Chang') && row.getAttribute('aria-label').includes('+42') && row.getAttribute('aria-label').includes('Record release evidence'), 'the accessible name retains every visible summary field');
    const gitlabItem = api.gitDiffItemFor('/gitlab');
    api.setGitDiffTabStateForTest(gitlabItem, {hostedRemote: {provider: 'gitlab', base_url: 'https://gitlab.example.com/group/project'}});
    const gitlabRow = api.gitDiffCommitRowForTest(gitlabItem, {sha: 'b'.repeat(40), short: 'b'.repeat(9), subject: 'Merge #9'});
    assert.equal(gitlabRow.querySelector('.git-diff-commit-sha').href, `https://gitlab.example.com/group/project/-/commit/${'b'.repeat(40)}`);
    assert.equal(gitlabRow.querySelector('.git-diff-change-link').href, 'https://gitlab.example.com/group/project/-/merge_requests/9');
    const plainRow = api.gitDiffCommitRowForTest(api.gitDiffItemFor('/plain'), {sha: 'c'.repeat(40), short: 'c'.repeat(9), subject: 'Plain #7'});
    assert.equal(plainRow.querySelector('.git-diff-commit-sha').localName, 'span');
    assert.equal(plainRow.querySelector('.git-diff-change-link').localName, 'span');
    const css = fs.readFileSync('static_src/css/yolomux/35_git_diff_viewer.css', 'utf8');
    const authorDrop = css.indexOf('.git-diff-commit-author');
    const dateDrop = css.indexOf('.git-diff-commit-date', authorDrop + 1);
    const changesDrop = css.indexOf('.git-diff-commit-changes', dateDrop + 1);
    assert.ok(authorDrop >= 0 && dateDrop > authorDrop && changesDrop > dateDrop, 'responsive rules remove author, then date, then changes');
    assert.match(css, /\.git-diff-commits\s*\{[\s\S]*grid-template-columns:\s*14px max-content max-content max-content/,
      'one commit-list grid owns the shared semantic column tracks');
    assert.match(css, /\.git-diff-commit\s*\{[\s\S]*grid-template-columns:\s*subgrid/,
      'each commit group inherits the list tracks');
    assert.match(css, /\.git-diff-commit-row\s*\{[\s\S]*grid-template-columns:\s*subgrid/,
      'each summary row inherits the same list tracks');
    assert.match(css, /\.git-diff-commits\s*\{[\s\S]*gap:\s*0 var\(--space-8\)/,
      'collapsed commit rows have no extra inter-row whitespace');
    assert.match(css, /\.git-diff-commit-row\s*\{[\s\S]*min-height:\s*24px;[\s\S]*padding:\s*var\(--space-2\) var\(--space-6\)/,
      'commit summaries keep the approved compact row height and padding');
    assert.match(css, /\.git-diff-commit-row\s*\{[\s\S]*white-space:\s*nowrap/);
    assert.match(css, /\.git-diff-commit-added\s*\{[\s\S]*color:\s*var\(--git-staged\)/);
    assert.match(css, /\.git-diff-commit-removed\s*\{[\s\S]*color:\s*var\(--git-deleted\)/);
    assert.match(css, /body\.theme-light \.git-diff-panel\s*\{[\s\S]*--git-diff-row-separator:\s*#[0-9a-f]+;[\s\S]*--git-diff-secondary-text:\s*#[0-9a-f]+/i);
    assert.match(css, /\.git-diff-commit\s*\{[\s\S]*box-shadow:\s*inset 0 -1px var\(--git-diff-row-separator\)/);
    assert.match(css, /\.git-diff-commit-detail\s*\{[\s\S]*margin-inline-start:/, 'commit detail indentation follows inline direction in RTL locales');
  });

  test('Git history commit rows use shared roving focus and valid tree ownership', () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const first = 'a'.repeat(40), second = 'b'.repeat(40);
    api.setGitDiffTabStateForTest(item, {path: '/repo', head: 'f'.repeat(40), commits: [{sha: first, short: 'aaaaaaaaa', subject: 'first'}, {sha: second, short: 'bbbbbbbbb', subject: 'second'}], visibleCommitCount: 2, loaded: true, loadAttempted: true});
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    api.renderGitDiffPanelForTest(item, {panel});
    const body = panel.querySelector('.git-diff-panel-body');
    const tree = panel.querySelector('.git-diff-commits');
    const rows = tree.querySelectorAll('.git-diff-commit-row');
    assert.equal(body.getAttribute('role'), undefined, 'status and pagination controls are not direct children of a tree');
    assert.equal(tree.getAttribute('role'), 'tree', 'only the commit list owns the commit tree role');
    assert.deepStrictEqual(rows.map(row => row.tabIndex), [0, -1], 'exactly one commit row participates in sequential focus');
    const event = treeKeyEvent('ArrowDown', rows[0]);
    tree.listeners.get('keydown')[0](event);
    assert.equal(event.defaultPrevented, true, 'the shared tree keyboard owner consumes ArrowDown');
    assert.deepStrictEqual(rows.map(row => row.tabIndex), [-1, 0], 'ArrowDown moves the roving tab stop');
    assert.equal(rows[1].focused, true, 'ArrowDown moves DOM focus to the next commit');
  });

  await testAsync('Git history refresh prunes old SHA caches and records a reload cursor', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const oldSha = 'a'.repeat(40), freshSha = 'b'.repeat(40), head = 'f'.repeat(40);
    const state = api.setGitDiffTabStateForTest(item, {path: '/repo', head: oldSha, commits: [{sha: oldSha}], loaded: true, loadAttempted: true});
    state.expanded.add(oldSha);
    state.details.set(oldSha, {sha: oldSha});
    state.detailCollapsedDirectories.set(oldSha, new Set(['/repo/old']));
    api.setFetchForTest(() => Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head, snapshot_cursor: 'snapshot-zero', commits: [{sha: freshSha, short: 'bbbbbbbbb'}], next_cursor: '', truncated: false})));
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    const refreshed = api.gitDiffTabStateForTest(item);
    assert.equal(refreshed.snapshotCursor, 'snapshot-zero', 'the first page retains an opaque offset-zero cursor for exact reload');
    assert.deepStrictEqual([...refreshed.expanded], [], 'Refresh drops disclosures outside the new bounded snapshot');
    assert.equal(refreshed.details.has(oldSha), false, 'Refresh retires stale detail payloads');
    assert.equal(refreshed.detailCollapsedDirectories.has(oldSha), false, 'Refresh retires stale per-SHA folder state');
  });

  await testAsync('Git history fills the viewport and exposes two viewport pages without another Git request', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const commits = Array.from({length: 40}, (_, index) => ({
      sha: String(index).padStart(40, '0'), short: String(index).padStart(9, '0'), subject: `commit ${index}`,
    }));
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero', commits, next_cursor: 'next-page', truncated: false}));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    Object.defineProperty(panel.querySelector('.git-diff-panel-body'), 'clientHeight', {configurable: true, value: 48});
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), true);
    assert.equal(panel.querySelectorAll('.git-diff-commit-row').length, 2, 'the first paint includes the available rows in the test viewport');
    assert.deepStrictEqual(requests, ['/api/fs/git-history?path=%2Frepo&limit=10'], 'one request carries five viewport pages');
    assert.equal(api.loadOlderGitDiffHistoryForTest(item), true);
    assert.equal(panel.querySelectorAll('.git-diff-commit-row').length, 3, 'Load older paints the retained reserve immediately');
    assert.equal(requests.length, 2, 'reaching the second visible page starts the next five-page fetch');
  });

  await testAsync('Git history auto-loads when the initial rows do not create a scrollbar', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const commits = Array.from({length: 8}, (_, index) => ({
      sha: String(index).padStart(40, '0'), short: String(index).padStart(9, '0'), subject: `commit ${index}`,
    }));
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40), snapshot_cursor: 'snapshot-zero', commits, next_cursor: requests.length === 1 ? 'next-page' : '', truncated: false}));
    });
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    const body = panel.querySelector('.git-diff-panel-body');
    Object.defineProperties(body, {
      clientHeight: {configurable: true, value: 480},
      scrollHeight: {configurable: true, get: () => Math.max(1, body.querySelectorAll('.git-diff-commit-row').length * 24)},
    });
    await api.refreshGitDiffHistoryForTest(item, {refresh: true});
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(requests.length, 2, 'a short initial list requests its next page without waiting for an impossible scroll event');
    assert.equal(panel.querySelectorAll('.git-diff-commit-row').length, 8, 'auto-loaded history renders the complete available result');
  });

  await testAsync('Git history freezes pagination and fences stale refresh generations without dropping valid rows', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo/src');
    const stale = deferredFetch();
    const fresh = deferredFetch();
    const older = deferredFetch();
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), signal: options.signal});
      if (requests.length === 1) return stale.promise;
      if (requests.length === 2) return fresh.promise;
      return older.promise;
    });
    const staleLoad = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    const freshLoad = api.refreshGitDiffHistoryForTest(item, {refresh: true});
    fresh.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'f'.repeat(40), commits: [{sha: 'f'.repeat(40), short: 'fffffffff', subject: 'fresh'}], next_cursor: 'frozen-cursor', truncated: false}));
    await freshLoad;
    stale.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'e'.repeat(40), commits: [{sha: 'e'.repeat(40), short: 'eeeeeeeee', subject: 'stale'}], next_cursor: '', truncated: false}));
    await staleLoad;
    let state = api.gitDiffTabStateForTest(item);
    assert.equal(state.head, 'f'.repeat(40), 'late refresh data cannot replace the newer generation');
    assert.deepStrictEqual([...state.commits.map(commit => commit.subject)], ['fresh']);
    assert.match(requests[0].url, /^\/api\/fs\/git-history\?path=%2Frepo%2Fsrc&limit=5$/);

    const append = api.loadOlderGitDiffHistoryForTest(item);
    assert.ok(requests[2].url.includes('cursor=frozen-cursor'), 'pagination uses the frozen snapshot cursor');
    older.resolve(jsonResponse({path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'f'.repeat(40), commits: [{sha: 'd'.repeat(40), short: 'ddddddddd', subject: 'older'}], next_cursor: '', truncated: true, truncation_reason: 'cursor_limit'}));
    await append;
    state = api.gitDiffTabStateForTest(item);
    assert.deepStrictEqual([...state.commits.map(commit => commit.subject)], ['fresh', 'older']);
    assert.equal(state.truncated, true, 'bounded pagination remains visibly partial');

    api.setFetchForTest(() => Promise.reject(new Error('offline')));
    assert.equal(await api.refreshGitDiffHistoryForTest(item, {refresh: true}), false);
    state = api.gitDiffTabStateForTest(item);
    assert.deepStrictEqual([...state.commits.map(commit => commit.subject)], ['fresh', 'older'], 'refresh failure retains the prior valid snapshot');
    assert.ok(state.error, 'refresh failure is presented instead of silently clearing the list');
  });

  await testAsync('Git history retries once without a stale saved cursor', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      if (requests.length === 1) {
        const error = new Error('invalid Git history cursor');
        error.status = 400;
        error.payload = {user_message: {key: 'fs.error.gitHistoryCursor', params: {}, fallback: 'invalid Git history cursor'}};
        return Promise.reject(error);
      }
      return Promise.resolve(jsonResponse({
        path: '/repo', repo: '/repo', relative_path: '', head: 'f'.repeat(40),
        snapshot_cursor: 'fresh-cursor', commits: [], next_cursor: '', truncated: false,
      }));
    });
    api.setGitDiffTabStateForTest(item, {snapshotCursor: 'old-cursor'});
    assert.equal(await api.refreshGitDiffHistoryForTest(item), true);
    assert.deepStrictEqual(requests, [
      '/api/fs/git-history?path=%2Frepo&limit=5&cursor=old-cursor',
      '/api/fs/git-history?path=%2Frepo&limit=5',
    ]);
    assert.equal(api.gitDiffTabStateForTest(item).snapshotCursor, 'fresh-cursor');
  });

  await testAsync('Git commit disclosures load independently and changed files retain status, rename, binary, and exact refs', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo');
    const shaA = 'a'.repeat(40);
    const shaB = 'b'.repeat(40);
    const head = 'f'.repeat(40);
    api.setGitDiffTabStateForTest(item, {path: '/repo', repo: '/repo', relativePath: '', head, commits: [{sha: shaA, short: 'aaaaaaaaa'}, {sha: shaB, short: 'bbbbbbbbb'}], loaded: true});
    const detailA = deferredFetch();
    const detailB = deferredFetch();
    api.setFetchForTest(url => String(url).includes(`commit=${shaA}`) ? detailA.promise : detailB.promise);
    const loadA = api.setGitDiffCommitExpandedForTest(item, shaA, true);
    const loadB = api.setGitDiffCommitExpandedForTest(item, shaB, true);
    detailB.resolve(jsonResponse({repo: '/repo', sha: shaB, parents: [shaA], from_ref: shaA, to_ref: shaB, message: 'second\n\nbody', message_truncated: false, files: [{status: 'M', path: 'b.js', old_path: '', added: 1, removed: 0, binary: false, counts_available: true}], files_truncated: false, truncated: false}));
    detailA.resolve(jsonResponse({
      repo: '/repo', sha: shaA, parents: [], from_ref: '0'.repeat(40), to_ref: shaA,
      message: '<script>first</script>\n\nbody', message_truncated: true,
      files: [
        {status: 'R', path: 'src/new.js', old_path: 'old.js', added: 5, removed: 2, binary: false, counts_available: true},
        {status: 'M', path: 'assets/data.bin', old_path: '', added: null, removed: null, binary: true, counts_available: true},
        {status: 'D', path: 'gone.txt', old_path: '', added: 0, removed: 4, binary: false, counts_available: true},
      ], files_truncated: true, truncated: true,
    }));
    await Promise.all([loadA, loadB]);
    const state = api.gitDiffTabStateForTest(item);
    assert.deepStrictEqual([...state.expanded].sort(), [shaA, shaB], 'multiple commits remain expanded');
    assert.equal(state.details.size, 2, 'one detail request does not invalidate another SHA');
    const model = api.gitDiffCommitFileTreeForTest(state.details.get(shaA));
    assert.equal(model.sessionFilesMap.get('/repo/src/new.js').status, 'R');
    assert.equal(model.sessionFilesMap.get('/repo/src/new.js').old_path, 'old.js');
    assert.equal(model.sessionFilesMap.get('/repo/assets/data.bin').binary, true);
    assert.equal(model.sessionFilesMap.get('/repo/gone.txt').status, 'D');
    const historicalItem = api.gitDiffHistoricalFileItemForTest(state.details.get(shaA), model.sessionFilesMap.get('/repo/src/new.js'));
    assert.deepStrictEqual(canonical(api.historicalFileEditorIdentity(historicalItem)), {path: '/repo/src/new.js', fromRef: '0'.repeat(40), toRef: shaA});
    assert.equal(api.gitDiffCommitMessageForTest(state.details.get(shaA)).textContent, '<script>first</script>\n\nbody', 'commit messages are rendered as text');
  });

  await testAsync('historical file selection paints its exact loading tab before comparison completion', async () => {
    const api = loadYolomux('', ['1']);
    const path = '/repo/src/app.js';
    const fromRef = 'b'.repeat(40);
    const toRef = 'a'.repeat(40);
    const comparison = deferredFetch();
    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'http://localhost');
      assert.equal(parsed.pathname, '/api/fs/diff');
      assert.equal(parsed.searchParams.get('path'), path);
      assert.equal(parsed.searchParams.get('from'), fromRef);
      assert.equal(parsed.searchParams.get('to'), toRef);
      return comparison.promise;
    });
    const opened = api.openGitDiffHistoricalFileForTest(
      {repo: '/repo', from_ref: fromRef, to_ref: toRef, parents: [fromRef]},
      {path: 'src/app.js', abs_path: path},
    );
    const item = api.historicalFileEditorItemFor(path, fromRef, toRef);
    assert.equal(opened, item, 'file selection returns the exact historical tab without waiting for Git');
    assert.equal(api.tabTypeForItem(item)?.key, 'file-editor', 'the exact historical Editor item is installed synchronously');
    assert.equal(api.editorViewModeFor(path, item), 'diff', 'the historical tab selects Diff before comparison bytes arrive');
    assert.equal(api.fileEditorStateForItemForTest(path, item)?.loading, true, 'the selected historical tab exposes a loading state');
    comparison.resolve(jsonResponse({repo: '/repo', relative_path: 'src/app.js', from_ref: fromRef, to_ref: toRef, diff: '@@ -1 +1 @@\n-old\n+new\n', original: 'old\n', working: 'new\n'}));
    await flushAsyncWork();
    assert.equal(api.fileEditorStateForItemForTest(path, item)?.diffLoaded, true, 'the background comparison still completes for the selected tab');
  });

  await testAsync('attached Diff repo panels load once, render disclosure DOM, and open the exact historical Editor tuple', async () => {
    const api = loadYolomux('', ['1']);
    const item = api.gitDiffItemFor('/repo/src');
    const sha = 'a'.repeat(40);
    const firstParent = 'b'.repeat(40);
    const secondParent = 'c'.repeat(40);
    const requests = [];
    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'http://localhost');
      requests.push(parsed.pathname);
      if (parsed.pathname === '/api/fs/git-history') {
        return Promise.resolve(jsonResponse({
          path: '/repo/src', repo: '/repo', relative_path: 'src', head: 'f'.repeat(40), next_cursor: '', truncated: false,
          commits: [{sha, short: 'aaaaaaaaa', parents: [firstParent, secondParent], subject: 'Merge exact history', author: 'Keiven Chang', authored_at: 1786931640, files: 1, added: 3, removed: 1, binary_files: 0}],
        }));
      }
      if (parsed.pathname === '/api/fs/git-commit') {
        return Promise.resolve(jsonResponse({
          repo: '/repo', scope_path: 'src', sha, parents: [firstParent, secondParent], from_ref: firstParent, to_ref: sha,
          subject: 'Merge exact history', message: 'Merge exact history\n\nBody text', authored_at: 1786931640,
          files: [{status: 'M', path: 'src/app.js', old_path: '', added: 3, removed: 1, binary: false, counts_available: true}],
          message_truncated: false, files_truncated: false, truncated: false,
        }));
      }
      if (parsed.pathname === '/api/fs/diff') {
        assert.equal(parsed.searchParams.get('from'), firstParent);
        assert.equal(parsed.searchParams.get('to'), sha);
        return Promise.resolve(jsonResponse({
          repo: '/repo', relative_path: 'src/app.js', from_ref: firstParent, to_ref: sha,
          diff: '@@ -1 +1 @@\n-old\n+new\n', original: 'old\n', working: 'new\n',
        }));
      }
      throw new Error(`unexpected request ${parsed.pathname}`);
    });

    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    api.renderGitDiffPanelForTest(item, {panel});
    api.renderGitDiffPanelForTest(item, {panel});
    assert.deepStrictEqual(requests, ['/api/fs/git-history'], 'an attached initial panel starts one history request');
    await flushAsyncWork();
    let row = panel.querySelector('.git-diff-commit-row');
    assert.ok(row, 'the loaded history renders a commit disclosure row');
    assert.equal(row.getAttribute('role'), 'treeitem');
    assert.equal(row.getAttribute('aria-expanded'), 'false');

    const commitTree = panel.querySelector('.git-diff-commits');
    const expandEvent = treeKeyEvent('ArrowRight', row);
    commitTree.listeners.get('keydown')[0](expandEvent);
    assert.equal(expandEvent.defaultPrevented, true, 'Right Arrow owns disclosure expansion');
    await flushAsyncWork();
    row = panel.querySelector('.git-diff-commit-row');
    assert.equal(row.getAttribute('aria-expanded'), 'true');
    const detail = panel.querySelector('.git-diff-commit-detail');
    assert.equal(detail.getAttribute('role'), 'group');
    assert.equal(detail.querySelector('.git-diff-commit-message').textContent, 'Merge exact history\n\nBody text');
    assert.equal(requests.filter(path => path === '/api/fs/git-commit').length, 1, 'one disclosure starts one detail request');

    const fileRow = detail.querySelectorAll('.file-tree-row').find(candidate => candidate.dataset.gitDiffCommitPath === '/repo/src/app.js');
    assert.ok(fileRow, 'the retained detail DOM contains the changed file row');
    const fileTree = detail.querySelector('.git-diff-file-tree');
    fileTree.listeners.get('click')[0]({target: fileRow, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {}});
    await flushAsyncWork();
    const historicalItem = api.historicalFileEditorItemFor('/repo/src/app.js', firstParent, sha);
    const historicalState = api.fileEditorStateForItemForTest('/repo/src/app.js', historicalItem);
    assert.equal(api.tabTypeForItem(historicalItem)?.key, 'file-editor', 'changed files reuse the current Editor tab type');
    assert.equal(api.editorViewModeFor('/repo/src/app.js', historicalItem), 'diff');
    assert.equal(historicalState.historicalComparisonKind, 'merge-first-parent');
    assert.equal(historicalState.content, 'new\n', 'historical Preview receives the immutable TO content');

    const collapseEvent = treeKeyEvent('ArrowLeft', row);
    row = panel.querySelector('.git-diff-commit-row');
    commitTree.listeners.get('keydown')[0](collapseEvent);
    assert.equal(collapseEvent.defaultPrevented, true, 'Left Arrow owns disclosure collapse');
    assert.equal(panel.querySelector('.git-diff-commit-row').getAttribute('aria-expanded'), 'false');
    const enterRow = panel.querySelector('.git-diff-commit-row');
    const enterEvent = treeKeyEvent('Enter', enterRow);
    commitTree.listeners.get('keydown')[0](enterEvent);
    await flushAsyncWork();
    assert.equal(panel.querySelector('.git-diff-commit-row').getAttribute('aria-expanded'), 'true', 'Enter reopens the cached disclosure');
    assert.equal(requests.filter(path => path === '/api/fs/git-commit').length, 1, 'cached disclosure does not refetch detail');
    assert.equal(api.gitDiffHistoricalComparisonKindForTest({parents: []}), 'root-empty-tree');
    assert.equal(api.gitDiffHistoricalComparisonKindForTest({parents: [firstParent]}), 'parent');
    assert.equal(api.gitDiffHistoricalComparisonKindForTest({parents: [firstParent, secondParent]}), 'merge-first-parent');
  });

  test('Diff repo relocalization rerenders labels and dates without replacing retained tab state', () => {
    const enCatalog = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
    const frCatalog = JSON.parse(fs.readFileSync('static/locales/fr.json', 'utf8'));
    const enApi = loadYolomux('', ['1']);
    const commit = {sha: 'd'.repeat(40), short: 'ddddddddd', parents: ['c'.repeat(40)], subject: 'Locale state', author: 'Keiven Chang', authored_at: 1786931640, files: 1, added: 2, removed: 1};
    const enDate = enApi.gitDiffCommitRowForTest(enApi.gitDiffItemFor('/repo'), commit).querySelector('.git-diff-commit-date').textContent;
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {locale: 'fr', strings: {en: enCatalog, fr: frCatalog}});
    const item = api.gitDiffItemFor('/repo');
    const state = api.setGitDiffTabStateForTest(item, {path: '/repo', repo: '/repo', relativePath: '', head: 'f'.repeat(40), commits: [commit], visibleCommitCount: 1, loaded: true, loadAttempted: true});
    state.expanded.add(commit.sha);
    state.details.set(commit.sha, {repo: '/repo', sha: commit.sha, parents: commit.parents, from_ref: commit.parents[0], to_ref: commit.sha, message: 'Locale state', files: []});
    const panel = api.createGitDiffPanelForTest(item);
    api.setPanelNodeForTest(item, panel);
    api.renderGitDiffPanelForTest(item, {panel});
    const body = panel.querySelector('.git-diff-panel-body');
    body.scrollTop = 73;
    const detailsIdentity = state.details;
    const expandedIdentity = state.expanded;
    panel.querySelector('.git-diff-heading').textContent = 'stale heading';
    panel.querySelector('.git-diff-commit-date').textContent = 'stale date';
    api.relocalizeGitDiffPanelForTest(item, panel);
    assert.equal(panel.querySelector('.git-diff-heading').textContent, frCatalog['contextmenu.showDiff']);
    assert.notEqual(panel.querySelector('.git-diff-commit-date').textContent, enDate, 'visible date/time uses the active locale');
    assert.equal(panel.querySelector('.git-diff-commit-date').title.length > 0, true, 'localized date retains an absolute-time tooltip');
    assert.equal(state.details, detailsIdentity);
    assert.equal(state.expanded, expandedIdentity);
    assert.equal(state.expanded.has(commit.sha), true);
    assert.equal(body.scrollTop, 73, 'relocalization preserves the tab scroll position');
  });

  await testAsync('historical Editor controls and preview updates cannot mutate or borrow working-tree state', async () => {
    const api = loadYolomux('', ['1']);
    const path = '/repo/README.md';
    const fromRef = '1'.repeat(40);
    const toRef = '2'.repeat(40);
    const item = api.historicalFileEditorItemFor(path, fromRef, toRef);
    api.setOpenFileStateForTest(path, {kind: 'text', original: '- [ ] WORKING', content: '- [ ] WORKING NEXT', dirty: true});
    api.registerFileEditorLayoutItemForTest(path, {item});
    api.setHistoricalFileStateForTest(item, {
      historical: true, kind: 'text', original: '- [ ] HISTORICAL', content: '- [ ] HISTORICAL', dirty: false,
      diffPinnedFromRef: fromRef, diffPinnedToRef: toRef, diffFromRef: fromRef, diffToRef: toRef,
    });
    const panel = new TestElement('historical-panel');
    panel.className = 'file-editor-panel';
    panel.dataset.layoutItem = item;
    panel.dataset.filePath = path;
    const preview = new TestElement('historical-preview');
    preview.className = 'file-editor-preview-pane-panel';
    panel.appendChild(preview);
    api.setPanelNodeForTest(item, panel);
    api.setFileEditorViewMode(path, 'preview', item);
    api.renderLinkedFilePreviewPanelsForTest(null, path, '# WORKING UPDATED');
    assert.equal(preview._previewText, '- [ ] HISTORICAL', 'working-tree updates render a historical panel from its own immutable TO content');
    api.refreshEditorPreviewsForTest();
    assert.equal(preview._previewText, '- [ ] HISTORICAL', 'global preview refresh uses item-scoped historical state');

    const markdownPreview = new TestElement('historical-markdown-preview');
    markdownPreview.dataset.mdPath = path;
    markdownPreview._markdownReadOnly = true;
    const taskList = new TestElement('historical-task-list', 'ul');
    const taskItem = new TestElement('historical-task-item', 'li');
    const task = new TestElement('historical-task', 'input');
    task.setAttribute('type', 'checkbox');
    task.classList.add('markdown-rendered-task-checkbox');
    taskItem.appendChild(task);
    taskList.appendChild(taskItem);
    markdownPreview.appendChild(taskList);
    panel.appendChild(markdownPreview);
    api.bindMarkdownTaskCheckboxesForTest(markdownPreview, '- [ ] HISTORICAL', path);
    assert.equal(task.disabled, true, 'historical Preview task controls are visibly disabled');
    task.checked = true;
    assert.equal(api.updateMarkdownTaskFromPreviewForTest(markdownPreview, task), false, 'historical Preview task controls fail closed');

    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: String(options.method || 'GET')});
      return Promise.resolve(jsonResponse({ok: true, mtime: 1, size: 1}));
    });
    assert.equal(await api.saveFileEditorForTest(path, panel), false, 'a programmatic save from a historical panel fails closed');
    assert.deepStrictEqual(requests, [], 'historical save cannot reach /api/fs/write');
    assert.equal(api.openFileStateForTest(path).content, '- [ ] WORKING NEXT', 'historical controls cannot alter dirty working-tree content');
    assert.equal(api.fileEditorStateForItemForTest(path, item).content, '- [ ] HISTORICAL');
    assert.equal(api.tabTypeForItem(item).canPopout(item), false, 'the path-keyed working preview popout is unavailable for historical tuples');
    assert.equal(api.openFilePreviewPopoutForTest(path, panel), false, 'programmatic historical popout fails closed');
    const workingPopoutWindow = {closed: false, close() { this.closed = true; }};
    api.setFilePreviewPopoutForTest(path, workingPopoutWindow);
    api.setFileEditorViewMode(path, 'diff', item);
    api.setFileEditorViewMode(path, 'preview', item);
    assert.equal(workingPopoutWindow.closed, false, 'historical mode changes do not close the working tab popout');
    assert.equal(api.closePopoutsForLayoutItemForTest(item), false, 'closing a historical tuple does not close the working tab popout');
    assert.equal(workingPopoutWindow.closed, false);
    assert.ok(api.filePreviewPopoutForTest(path));
    const refs = api.historicalDiffRefControlsHtmlForTest(api.fileEditorStateForItemForTest(path, item));
    assert.ok(refs.includes(fromRef) && refs.includes(toRef), 'historical FROM/TO refs are rendered from the immutable tuple');
    assert.equal(refs.includes('data-diff-ref-input'), false, 'historical refs are labels, not mutable pickers');
    assert.equal(refs.includes('data-diff-ref-reset'), false, 'historical refs cannot be reset to working-tree defaults');

    api.setFetchForTest(() => Promise.resolve(jsonResponse({
      path,
      repo: '/repo',
      relative_path: 'README.md',
      diff: 'silently substituted working diff',
      original: 'working original',
      working: 'working current',
      from_ref: 'HEAD',
      to_ref: 'current',
    })));
    const historicalState = api.fileEditorStateForItemForTest(path, item);
    assert.equal(await api.refreshOpenFileDiffForTest(path, {item, state: historicalState, fromRef, toRef, silent: true, renderOnComplete: false}), false, 'a backend ref fallback is rejected for an immutable historical tuple');
    assert.equal(historicalState.diffUnavailable, true, 'the historical Editor exposes a typed unavailable state');
    assert.match(historicalState.diffError, /stale/i);
    assert.equal(historicalState.content, '- [ ] HISTORICAL', 'a mismatched response cannot replace immutable Preview content');
    assert.equal(api.openFileStateForTest(path).content, '- [ ] WORKING NEXT', 'a mismatched historical response cannot touch the working Editor');
  });

  await testAsync('API transport retirement is request-scoped and keeps live failures blocking', async () => assert.deepStrictEqual(canonical(await apiTransportRetirementScenario()), {retired: {error: 'Failed to fetch', outcome: 'retired', reason: 'page_beforeunload', failures: 0, backendFailures: 0, consoleErrors: 0}, lateRetired: {error: 'Failed to fetch', outcome: 'retired', reason: 'page_beforeunload', failures: 0, backendFailures: 0, consoleErrors: 0}, resumedLive: {error: 'Failed to fetch', outcome: 'failed', failures: 1, backendFailures: 1, consoleErrors: 0}, raced: {error: 'Failed to fetch', outcome: 'retired', failures: 0}, live: {error: 'Failed to fetch', type: 'api', endpoint: '/api/auto-approve', outcome: 'failed', backendFailures: 1}}));
  await testAsync('a host-scoped terminal close still posts /api/event and checks /api/tmux-session-exists', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: String(options.method || 'GET')});
      return Promise.resolve(jsonResponse({exists: true}));
    });
    api.setShowToastForTest(() => {});
    assert.equal(api.clientCanUseUnscopedHostRequestsForTest(), true, 'a host viewer is eligible for host-only requests');
    const term = {write() {}, dispose() {}};
    const item = api.registerTerminalForTest('1', term, {readyState: WebSocket.CLOSED, close() {}});
    api.connectTerminalSocketForTest('1', item);
    const socket = item.socket;
    // Positive control: the same abnormal close under host scope DOES post terminal_disconnected and DOES
    // consult the tmux roster -- proving the fix gates on scope, not disables the feature.
    socket.onclose?.({target: socket, code: 1006, wasClean: false});
    await flushAsyncWork();
    assert.equal(requests.some(request => request.url.includes('/api/event') && request.method === 'POST'), true, 'a host viewer posts terminal_disconnected to /api/event');
    assert.equal(requests.some(request => request.url.includes('/api/tmux-session-exists')), true, 'a host viewer consults the tmux roster to decide reconnect vs prune');
  });

  await testAsync('background 401s retain the document while interactive commands still redirect to login', async () => {
    const api = loadYolomux();
    api.setFetchForTest(() => Promise.resolve({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      body: null,
      clone() { return {json: async () => ({login_url: '/login?next=%2F'})}; },
    }));

    await assert.rejects(
      api.apiFetchJsonQuietForTest('/api/stats-observations', {method: 'POST', body: '{}'}),
      error => error?.status === 401,
      'the registered background upload exposes its real 401 to the retry owner',
    );
    assert.deepStrictEqual(canonical(api.locationAssignmentsForTest()), [], 'a background 401 must not navigate away from retained work');

    await assert.rejects(
      api.apiFetchJsonQuietForTest('/api/settings', {method: 'POST', body: '{}'}),
      /authentication required/,
      'the registered interactive command retains the established auth failure',
    );
    assert.deepStrictEqual(canonical(api.locationAssignmentsForTest()), ['/login?next=%2F'], 'an interactive 401 still starts the login redirect');
  });

  await testAsync('one terminal authentication owner retires long-lived transports after the first 401', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [1500]});
    let requests = 0;
    api.setFetchForTest(() => {
      requests += 1;
      return Promise.resolve({
        ok: false,
        status: 401,
        statusText: 'Unauthorized',
        body: null,
        clone() { return {json: async () => ({code: 'authentication_required', login_url: '/login?next=%2F'})}; },
      });
    });
    api.resetRuntimeIntervalForTest('latency', () => null, 3000);
    api.resetRuntimeIntervalForTest('debug-stats', () => null, 500);
    api.installClientEventStreamForTest();
    api.startTranscriptStreamForTest('1');
    const transcriptSource = api.transcriptStreamForTest('1');
    assert.ok(api.clientEventTransportStateForTest().source, 'the client-events stream is live before authentication expires');
    assert.ok(transcriptSource, 'the transcript stream is live before authentication expires');

    let firstError;
    let secondError;
    try { await api.apiFetchJsonQuietForTest('/api/ping'); } catch (error) { firstError = error; }
    try { await api.apiFetchJsonQuietForTest('/api/ping'); } catch (error) { secondError = error; }

    assert.equal(requests, 1, 'the terminal latch blocks every request after the first 401');
    assert.equal(firstError?.status, 401);
    assert.equal(firstError?.terminalAuthentication, true);
    assert.equal(secondError?.status, 401, 'blocked siblings receive the same typed terminal outcome');
    assert.equal(api.runtimeIntervalActiveForTest('latency'), false, 'the /api/ping interval is retired');
    assert.equal(api.runtimeIntervalActiveForTest('debug-stats'), false, 'the snapshot interval is retired');
    assert.equal(api.clientEventTransportStateForTest().source, null, 'the shared client-events EventSource is closed');
    assert.equal(api.clientEventTransportStateForTest().enabled, false, 'client-events cannot immediately recreate its retired stream');
    assert.equal(api.transcriptStreamForTest('1'), null, 'the shared terminal owner closes direct transcript EventSources');
    transcriptSource.onerror?.();
    await flushAsyncWork();
    assert.equal(api.transcriptStreamForTest('1'), null, 'a retired transcript callback cannot schedule a replacement EventSource');
    assert.equal(api.testElementForId('body').dataset.authenticationState, 'signed-out');
    assert.equal(api.testElementForId('status').textContent, 'Authentication required.');
  });

  await testAsync('client-events resolves its hidden HTTP failure through one bounded auth probe', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [15000]});
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve({
        ok: false,
        status: 401,
        statusText: 'Unauthorized',
        body: null,
        clone() {
          return {
            json: async () => ({code: 'authentication_required'}),
            arrayBuffer: async () => new ArrayBuffer(0),
          };
        },
      });
    });
    api.installClientEventStreamForTest();
    const source = api.clientEventTransportStateForTest().source;
    source.onerror();
    await flushAsyncWork();
    await flushAsyncWork();

    assert.deepStrictEqual(requests, ['/api/ping?client_event_auth_probe=1'], 'one disconnect episode owns one status probe');
    assert.equal(api.clientEventTransportStateForTest().source, null, `the terminal auth owner clears the rejected EventSource (requests=${requests.length})`);
    assert.equal(source.closeCount, 1, `the terminal auth owner closes the rejected EventSource (closeCount=${String(source.closeCount)})`);
    assert.equal(api.clientEventTransportStateForTest().enabled, false);
  });

  await testAsync('a rejected client-events candidate reaches the same bounded auth probe', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve({
        ok: false,
        status: 401,
        statusText: 'Unauthorized',
        body: null,
        clone() {
          return {
            json: async () => ({code: 'authentication_required'}),
            arrayBuffer: async () => new ArrayBuffer(0),
          };
        },
      });
    });
    api.installClientEventStreamForTest();
    const active = api.clientEventTransportStateForTest().source;
    api.setEventLogTabActiveForTest('1', true);
    api.syncClientEventDemandForTest({immediate: true});
    const candidate = api.clientEventTransportStateForTest().replacementSource;
    assert.ok(candidate && candidate !== active);

    candidate.onerror();
    await flushAsyncWork();
    await flushAsyncWork();

    assert.deepStrictEqual(requests, ['/api/ping?client_event_auth_probe=1']);
    assert.equal(active.closeCount, 1, 'terminal authentication closes the formerly active source');
    assert.equal(candidate.closeCount, 1, 'terminal authentication closes the rejected candidate');
    assert.equal(api.clientEventTransportStateForTest().source, null);
    assert.equal(api.clientEventTransportStateForTest().replacementSource, null);
    assert.equal(api.clientEventTransportStateForTest().enabled, false);
  });

  await testAsync('the shared startup and refresh request owner admits at most eight API fetches', async () => {
    const api = loadYolomux();
    const pending = [];
    api.setFetchForTest(url => {
      const request = deferredFetch();
      pending.push({url: String(url), ...request});
      return request.promise;
    });

    const requests = Array.from({length: 12}, (_, index) => api.apiFetchJsonForTest(`/api/startup-refresh/${index}`));
    await flushAsyncWork();
    assert.equal(pending.length, 8, 'only eight startup or refresh API requests reach fetch before capacity is released');

    for (const request of pending.slice(0, 8)) request.resolve(jsonResponse({ok: true}));
    await flushAsyncWork();
    assert.equal(pending.length, 12, 'queued requests start as soon as the shared owner has capacity');
    for (const request of pending.slice(8)) request.resolve(jsonResponse({ok: true}));
    await Promise.all(requests);
  });

  await testAsync('a started startup request releases capacity when its fetch ignores abort', async () => {
    const api = loadYolomux();
    const controller = new AbortController();
    const ignoredFetch = deferredFetch();
    api.setFetchForTest(() => ignoredFetch.promise);

    let outcome = 'pending';
    api.apiFetchJsonForTest('/api/startup-refresh/ignores-abort', {signal: controller.signal})
      .then(() => { outcome = 'resolved'; }, error => { outcome = error?.name || String(error); });
    await flushAsyncWork();
    assert.equal(api.fixtureLifecycleOperationStateForTest().startupActive, 1, 'the started request owns one coordinator slot');

    controller.abort(new DOMException('consumer retired', 'AbortError'));
    await flushAsyncWork();
    assert.equal(api.fixtureLifecycleOperationStateForTest().startupActive, 0, 'abort releases the slot without waiting for a broken fetch');
    assert.equal(outcome, 'AbortError', 'the consumer request rejects on abort');
    ignoredFetch.resolve(jsonResponse({ok: true}));
    await flushAsyncWork();
    assert.equal(api.fixtureLifecycleOperationStateForTest().startupActive, 0, 'a late ignored-fetch result cannot release the slot twice');
    assert.equal(outcome, 'AbortError', 'a late ignored-fetch result cannot overwrite the abort result');
  });

  await testAsync('node shard launcher rejects a SIGKILL without a summary', async () => {
    const result = await runSuite('signal-kill-stub', () => spawn(process.execPath, ['-e', "process.kill(process.pid, 'SIGKILL')"]));
    assert.equal(result.status, 1, 'a signal-killed shard is never reported as successful');
    assert.ok(result.output.includes('without a suite summary'), 'a killed shard reports the missing terminal summary');
  });

  await testAsync('node shard launcher rejects a zero-exit summary that reports failures', async () => {
    const result = await runSuite('failed-summary-stub', () => spawn(process.execPath, ['-e', "console.log('layout suite: 0 passed, 1 failed')"]));

    assert.equal(result.status, 1, 'a reported test failure cannot pass through a zero child exit');
    assert.ok(result.output.includes('suite summary reported 1 failed'), 'the launcher classifies the failed terminal summary');
  });

  await testAsync('node shard launcher rejects malformed and duplicate summaries', async () => {
    const malformed = await runSuite('malformed-summary-stub', () => spawn(process.execPath, ['-e', "console.log('layout suite: 1 passed')"]));
    assert.equal(malformed.status, 1, 'a partial numeric summary cannot satisfy the shard contract');
    assert.ok(malformed.output.includes('without a suite summary'), 'a malformed summary reports the missing terminal contract');

    const duplicate = await runSuite('duplicate-summary-stub', () => spawn(process.execPath, ['-e', "console.log('layout suite: 1 passed, 0 failed'); console.log('layout suite: 1 passed, 0 failed')"]));
    assert.equal(duplicate.status, 1, 'multiple terminal summaries fail closed instead of selecting a convenient one');
    assert.ok(duplicate.output.includes('printed 2 suite summaries'), 'the launcher classifies duplicate terminal summaries');
  });

  await testAsync('node shard launcher terminates a shard that prints a summary but never exits', async () => {
    const {child, signals} = summarizedHangingShard('SIGTERM');
    const result = await runSuite('summary-hang-stub', () => child, {timeoutMs: 20, terminateGraceMs: 20});

    assert.equal(result.status, 1, 'a summarized shard that does not exit is never reported as successful');
    assert.ok(result.output.includes('exceeded 20 ms watchdog after printing a suite summary'), 'the launcher classifies the post-summary timeout');
    assert.deepStrictEqual(signals, ['SIGTERM'], 'the launcher first requests a narrow graceful termination');
  });

  await testAsync('node shard launcher escalates a summarized shard that ignores graceful termination', async () => {
    const {child, signals} = summarizedHangingShard('SIGKILL');
    const result = await runSuite('summary-stubborn-stub', () => child, {timeoutMs: 20, terminateGraceMs: 20});

    assert.equal(result.status, 1, 'a summarized shard that ignores SIGTERM remains failed');
    assert.deepStrictEqual(signals, ['SIGTERM', 'SIGKILL'], 'the bounded launcher escalates only after its graceful-termination window');
  });

  await testAsync('session metadata distinguishes fetch failure from a client apply failure', async () => {
    const api = loadYolomux('', ['1']);
    const fetchFailure = await api.fetchAndApplySessionMetadataForTest(
      () => Promise.reject(new Error('network unavailable')),
      () => { throw new Error('must not apply after a fetch failure'); },
    );
    assert.equal(fetchFailure.stage, 'fetch', 'a rejected metadata request is classified as fetch failure');

    const applyFailure = await api.fetchAndApplySessionMetadataForTest(
      () => Promise.resolve({sessions: {}}),
      () => { throw new Error('render failed'); },
    );
    assert.equal(applyFailure.stage, 'apply', 'a successful metadata response with a client exception is classified as apply failure');
    api.setTranscriptMetadataLoadErrorForTest(api.transcriptMetadataLoadErrorSnapshotForTest(applyFailure.error, applyFailure.stage));
    assert.equal(api.transcriptMetadataLoadErrorTextForTest(), 'render failed', 'the client apply error remains visible instead of becoming a fake transcript lookup failure');
    assert.equal(api.transcriptMetadataLoadErrorLabelForTest(), 'render failed', 'the pane header uses the actual apply error rather than the transcript lookup label');
  });

  await testAsync('Finder keeps deferred INFO work pending until its batch operation terminal SSE result', async () => {
    const api = loadYolomux();
    api.setFetchForTest((url, options = {}) => {
      assert.equal(String(url), '/api/fs/batch');
      const requests = JSON.parse(options.body || '{}').requests || [];
      assert.deepStrictEqual(canonical(requests.map(request => request.id)), [1, 2]);
      return Promise.resolve(jsonResponse({
        state: 'queued',
        request: {id: 'r-fs-batch'},
        operation: {
          id: 'op-fs-batch',
          kind: 'fs_batch',
          status_url: '/api/operations/op-fs-batch',
          events_url: '/api/client-events?operation_id=op-fs-batch',
          cursor: {epoch: 'epoch', seq: 0},
          context: {product_key: 'fs-batch:product'},
        },
      }, 202));
    });
    const firstInfo = api.fetchFilePathInfoForTest('/home/test/one', {fresh: true});
    const secondInfo = api.fetchFilePathInfoForTest('/home/test/two', {fresh: true});
    const flush = api.flushFileExplorerFsBatchForTest();
    await flushAsyncWork();
    let settled = false;
    Promise.all([firstInfo, secondInfo]).then(() => { settled = true; });
    await flushAsyncWork();
    assert.equal(settled, false, 'the 202 receipt remains pending without direct-request fallback');

    api.handleClientPushEventNowForTest('operation_terminal', {
      operation: {id: 'op-fs-batch', cursor: {epoch: 'epoch', seq: 1}},
      result: {
        state: 'ready',
        request: {id: 'r-fs-batch'},
        data: {
          responses: [
            {id: 1, ok: true, status: 200, payload: {path: '/home/test/one', kind: 'dir'}},
            {id: 2, ok: true, status: 200, payload: {path: '/home/test/two', kind: 'file'}},
          ],
        },
        quality: {complete: true, stale: false},
        warnings: [],
      },
    });

    assert.equal((await firstInfo).kind, 'dir');
    assert.equal((await secondInfo).kind, 'file');
    await flush;
  });

  await testAsync('Finder paints names and dates before batched Git enrichment patches repo rows in place', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    api.setFileExplorerTreeDateModeForTest('date');
    const entries = Array.from({length: 50}, (_value, index) => ({
      name: `repo-${String(index).padStart(2, '0')}`,
      kind: 'dir',
      size: 0,
      mtime: 1786640000 + index,
      repo_info_deferred: true,
    }));
    const calls = [];
    const infoBatch = deferredFetch();
    const infoResponse = requests => jsonResponse({responses: requests.map(request => ({
      id: request.id,
      ok: true,
      status: 200,
      payload: {
        path: request.path,
        kind: 'dir',
        mtime: 1786640000,
        repo: request.path === '/repos/repo-49' ? null : {
          root: request.path,
          name: request.path.split('/').at(-1),
          branch: `feature-${request.path.split('/').at(-1)}`,
          dirty_count: 2,
          upstream: 'origin/main',
          ahead: 1,
          behind: 0,
        },
      },
    }))});
    api.setFetchForTest((url, options = {}) => {
      if (String(url) === '/api/fs/fast/list?path=%2Frepos') {
        calls.push([{id: 0, type: 'list', path: '/repos'}]);
        return Promise.resolve(jsonResponse({path: '/repos', entries}));
      }
      assert.equal(String(url), '/api/fs/batch');
      const requests = JSON.parse(options.body || '{}').requests || [];
      calls.push(requests);
      assert.ok(requests.every(request => request.type === 'info'), 'the second phase contains only detailed info reads');
      if (calls.length === 1) {
        return Promise.resolve(jsonResponse({responses: requests.map(request => ({
          id: request.id,
          ok: true,
          status: 200,
          payload: {path: request.path, kind: 'dir', repo: {root: request.path, branch: 'stale-cache'}},
        }))}));
      }
      return calls.length === 3 ? infoBatch.promise : Promise.resolve(infoResponse(requests));
    });

    const firstPath = '/repos/repo-00';
    const seededInfo = api.fetchFilePathInfoForTest(firstPath);
    await api.flushFileExplorerFsBatchForTest();
    await seededInfo;
    assert.equal(calls.length, 1, 'the regression starts with a reusable stale INFO cache entry');

    const open = api.openFileExplorerAtForTest('/repos', {manualSelection: true, refreshPanels: false});
    assert.equal(await open, true);
    assert.equal(calls.length, 2, 'the first open batch contains no root or child Git-info request');
    assert.deepStrictEqual(canonical(calls[1].map(request => request.type)), ['list']);

    const tree = api.fileExplorerTreeForTest();
    const staleRemovedPath = '/repos/repo-49';
    api.setFileExplorerSelectionForTest([firstPath], firstPath);
    api.setFileExplorerExpandedForTest([firstPath]);
    api.renderTreeChildrenForTest(tree, '/repos', entries);
    api.setFileExplorerRepoInfoForTest(staleRemovedPath, {root: staleRemovedPath, name: 'repo-49', branch: 'stale-removed'});
    const staleRemovedRow = tree.querySelector(`.file-tree-row[data-path="${staleRemovedPath}"]`);
    api.updateFileTreeGitStatusRowsForTest([staleRemovedRow]);
    assert.ok(staleRemovedRow.querySelector(':scope > .file-tree-name').textContent.includes('stale-removed'));
    tree.scrollTop = 37;
    const firstRow = tree.querySelector(`.file-tree-row[data-path="${firstPath}"]`);
    assert.ok(firstRow, 'the first-phase repo row is visible');
    const firstDate = firstRow.querySelector(':scope > .file-tree-date').textContent;
    assert.equal(firstRow.querySelector(':scope > .file-tree-name').textContent, 'repo-00', 'the first paint does not wait for a branch badge');
    assert.ok(firstDate, 'the base mtime paints the configured Date column before Git enrichment');
    assert.equal(firstRow.getAttribute('aria-expanded'), 'true');
    assert.equal(firstRow.classList.contains('selected'), true);
    assert.ok(frames.length > 0, 'Git enrichment is deferred until a frame after the base listing');

    for (const frame of frames.splice(0)) frame();
    const enrichmentFlush = api.flushFileExplorerFsBatchForTest();
    assert.equal(calls.length, 3, 'fresh enrichment bypasses the reusable stale INFO cache');
    assert.equal(calls[2].length, 8, 'the first detailed wave is bounded independently from the server refusal ceiling');
    assert.ok(calls[2].every(request => request.trigger_counts['repo-enrichment'] === 1));
    const frameCountDuringEnrichment = frames.length;
    await api.fetchDirectoryForTest('/repos');
    assert.equal(calls.length, 3, 'a cached listing does not duplicate INFO requests already in flight');
    assert.equal(frames.length, frameCountDuringEnrichment, 'an in-flight enrichment stays the single owner of each repo path');
    infoBatch.resolve(infoResponse(calls[2]));
    await enrichmentFlush;
    await flushAsyncWork();
    await flushAsyncWork();
    while (frames.length) {
      frames.shift()();
      await api.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      await flushAsyncWork();
    }
    assert.deepStrictEqual(canonical(calls.slice(2).map(requests => requests.length)), [8, 8, 8, 8, 8, 8, 3], 'fifty rows plus the root backfill progressively in bounded detail waves');
    assert.ok(calls.slice(2).flat().every(request => request.trigger_counts['repo-enrichment'] === 1));

    const enrichedRow = tree.querySelector(`.file-tree-row[data-path="${firstPath}"]`);
    assert.strictEqual(enrichedRow, firstRow, 'Git enrichment patches the mounted row instead of rebuilding the tree');
    assert.ok(enrichedRow.querySelector(':scope > .file-tree-name').textContent.includes('feature-repo-00'));
    assert.equal(enrichedRow.classList.contains('repo-non-main'), true);
    assert.equal(enrichedRow.querySelector(':scope > .file-tree-date').textContent, firstDate, 'the late patch preserves the base date');
    assert.equal(enrichedRow.getAttribute('aria-expanded'), 'true');
    assert.equal(enrichedRow.classList.contains('selected'), true);
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), [firstPath]);
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [firstPath]);
    assert.equal(tree.scrollTop, 37);
    assert.ok(api.testElementForId('fileExplorerPath').title.includes('feature-repos'), 'the deferred root info updates the current root summary');
    assert.equal(staleRemovedRow.dataset.isRepo, 'false', 'an authoritative non-repo result clears the stale repo row state');
    assert.equal(staleRemovedRow.querySelector(':scope > .file-tree-name').textContent.includes('stale-removed'), false);

    const frameCountBeforeReuse = frames.length;
    await api.fetchDirectoryForTest('/repos');
    assert.equal(calls.length, 9, 'a repeated cached listing does not rerun resolved positive or negative Git enrichment');
    assert.equal(frames.length, frameCountBeforeReuse, 'resolved enrichment does not schedule another deferred wave');
    api.renderTreeChildrenForTest(tree, '/repos', entries);
    const cachedRepoRow = tree.querySelector(`.file-tree-row[data-path="${firstPath}"]`);
    assert.ok(cachedRepoRow.querySelector(':scope > .file-tree-name').textContent.includes('feature-repo-00'), 'a later base-row render reapplies cached Git info without another request');
  });

  await testAsync('Finder discards invalidated in-flight Git enrichment before patching the mounted row', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    const root = '/repos';
    const repoPath = '/repos/project';
    const entries = [{name: 'project', kind: 'dir', size: 0, mtime: 1786640000, repo_info_deferred: true}];
    const staleBatch = deferredFetch();
    const freshBatch = deferredFetch();
    let listCalls = 0;
    let infoCalls = 0;
    api.setFetchForTest((url, options = {}) => {
      if (String(url) === '/api/fs/fast/list?path=%2Frepos') {
        listCalls += 1;
        return Promise.resolve(jsonResponse({path: root, entries}));
      }
      assert.equal(String(url), '/api/fs/batch');
      const requests = JSON.parse(options.body || '{}').requests || [];
      assert.deepStrictEqual(canonical(requests.map(request => request.path)), [repoPath]);
      infoCalls += 1;
      return infoCalls === 1 ? staleBatch.promise : freshBatch.promise;
    });

    assert.deepStrictEqual(canonical((await api.fetchDirectoryForTest(root, {fresh: true})).map(entry => entry.name)), ['project']);
    const tree = api.fileExplorerTreeForTest();
    api.renderTreeChildrenForTest(tree, root, entries);
    const row = tree.querySelector(`.file-tree-row[data-path="${repoPath}"]`);
    assert.equal(row.querySelector(':scope > .file-tree-name').textContent, 'project');
    for (const frame of frames.splice(0)) frame();
    const staleFlush = api.flushFileExplorerFsBatchForTest();
    await flushAsyncWork();
    assert.equal(infoCalls, 1, 'the first INFO wave is in flight before invalidation');

    api.invalidateFileExplorerRootsForTest([root]);
    await api.fetchDirectoryForTest(root, {fresh: true});
    assert.equal(listCalls, 2, 'the invalidation starts a new fast listing transaction');
    staleBatch.resolve(jsonResponse({responses: [{
      id: 1,
      ok: true,
      status: 200,
      payload: {path: repoPath, kind: 'dir', repo: {root: repoPath, branch: 'stale-branch'}},
    }]}));
    await staleFlush;
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(row.querySelector(':scope > .file-tree-name').textContent.includes('stale-branch'), false, 'the invalidated INFO result never repaints the mounted row');

    assert.ok(frames.length > 0, 'the replacement enrichment is scheduled after stale ownership retires');
    for (const frame of frames.splice(0)) frame();
    const freshFlush = api.flushFileExplorerFsBatchForTest();
    await flushAsyncWork();
    assert.equal(infoCalls, 2, 'the invalidation starts one replacement INFO wave');
    freshBatch.resolve(jsonResponse({responses: [{
      id: 2,
      ok: true,
      status: 200,
      payload: {path: repoPath, kind: 'dir', repo: {root: repoPath, branch: 'fresh-branch'}},
    }]}));
    await freshFlush;
    await flushAsyncWork();
    await flushAsyncWork();
    assert.ok(row.querySelector(':scope > .file-tree-name').textContent.includes('fresh-branch'), 'the replacement INFO result patches the original mounted row');
  });

  await testAsync('Finder LIST uses the direct fast route while Git enrichment stays batched', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      const route = String(url);
      requests.push({route, method: options.method || 'GET', body: options.body || ''});
      if (route === '/api/fs/fast/list?path=%2Frepos') {
        return Promise.resolve(jsonResponse({
          path: '/repos',
          entries: [{name: 'repo-a', kind: 'dir', mtime: 1786640000, repo_info_deferred: true}],
        }));
      }
      assert.equal(route, '/api/fs/batch');
      const items = JSON.parse(options.body).requests;
      assert.ok(items.every(item => item.type === 'info'), 'only deferred INFO enrichment enters the batch route');
      return Promise.resolve(jsonResponse({responses: items.map(item => ({
        id: item.id,
        ok: true,
        status: 200,
        payload: {path: item.path, kind: 'dir', repo: null},
      }))}));
    });

    const opened = await api.openFileExplorerAtForTest('/repos', {manualSelection: true, refreshPanels: false});
    assert.equal(opened, true, `open failed after requests ${JSON.stringify(requests)}`);
    assert.deepStrictEqual(canonical(requests.map(request => [request.route, request.method])), [
      ['/api/fs/fast/list?path=%2Frepos', 'GET'],
    ], 'the first paint is one direct GET and does not wait for a batch receipt');
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repos/repo-a"]'));

    for (const frame of frames.splice(0)) frame();
    await api.flushFileExplorerFsBatchForTest();
    assert.equal(requests[1].route, '/api/fs/batch');
  });

  await testAsync('Finder Sync paints the fast root before progressive descendant listings finish', async () => {
    const api = loadYolomux('', ['1']);
    api.setFileExplorerRootMode('sync', {sync: false});
    const pending = new Map();
    const calls = [];
    api.setFetchForTest(url => {
      const route = String(url);
      calls.push(route);
      if (route === '/api/fs/fast/list?path=%2Frepo') {
        return Promise.resolve(jsonResponse({path: '/repo', entries: [{name: 'project', kind: 'dir'}]}));
      }
      const deferred = deferredFetch();
      pending.set(route, deferred);
      return deferred.promise;
    });
    const plan = {
      session: '1',
      root: '/repo',
      expandPaths: ['/repo/project', '/repo/project/one'],
      affectedDirs: ['/repo/project/one'],
    };
    let settled = false;
    const sync = api.syncFileExplorerRootToPlanForTest(plan, '1').then(value => {
      settled = true;
      return value;
    });

    await flushAsyncWork();
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/project"]'), `the root row paints while descendants remain pending; calls=${JSON.stringify(calls)}`);
    assert.equal(settled, false);
    assert.equal(calls[0], '/api/fs/fast/list?path=%2Frepo');

    pending.get('/api/fs/fast/list?path=%2Frepo%2Fproject').resolve(jsonResponse({
      path: '/repo/project',
      entries: [{name: 'one', kind: 'dir'}],
    }));
    await flushAsyncWork();
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/project/one"]'), 'the next BFS level backfills without waiting for deeper work');
    assert.equal(settled, false);

    pending.get('/api/fs/fast/list?path=%2Frepo%2Fproject%2Fone').resolve(jsonResponse({
      path: '/repo/project/one',
      entries: [{name: 'README.md', kind: 'file'}],
    }));
    assert.equal(await sync, true);
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/project/one/README.md"]'));
  });

  await testAsync('Finder Sync rejects a stale progressive descendant after a newer transaction takes ownership', async () => {
    const api = loadYolomux('', ['1', '2']);
    api.setFileExplorerRootMode('sync', {sync: false});
    const staleDescendant = deferredFetch();
    api.setFetchForTest(url => {
      const route = String(url);
      if (route === '/api/fs/fast/list?path=%2Fold') {
        return Promise.resolve(jsonResponse({path: '/old', entries: [{name: 'project', kind: 'dir'}]}));
      }
      if (route === '/api/fs/fast/list?path=%2Fold%2Fproject') return staleDescendant.promise;
      if (route === '/api/fs/fast/list?path=%2Fnew') {
        return Promise.resolve(jsonResponse({path: '/new', entries: [{name: 'current.txt', kind: 'file'}]}));
      }
      throw new Error(`unexpected request ${route}`);
    });

    const staleSync = api.syncFileExplorerRootToPlanForTest({
      session: '1',
      root: '/old',
      expandPaths: ['/old/project'],
      affectedDirs: ['/old/project'],
    }, '1');
    await flushAsyncWork();
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/old/project"]'), 'the first root paints before its descendant settles');

    const currentSync = api.syncFileExplorerRootToPlanForTest({
      session: '2',
      root: '/new',
      expandPaths: [],
      affectedDirs: ['/new'],
    }, '2');
    assert.equal(await currentSync, true);
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/new/current.txt"]'), 'the newer transaction owns the rendered tree');

    staleDescendant.resolve(jsonResponse({
      path: '/old/project',
      entries: [{name: 'stale.txt', kind: 'file'}],
    }));
    await staleSync;
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/new/current.txt"]'), 'late work cannot repaint the newer root');
    assert.equal(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/old/project/stale.txt"]'), null);
  });

  await testAsync('operation receipts reuse the shared client-event stream', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
    const initialSource = api.clientEventTransportStateForTest().source;
    initialSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    const record = api.registerApiOperationReceiptForTest({
      request: {id: 'r-shared-operation'},
      operation: {
        id: 'op-shared-operation',
        kind: 'fs_watch_diff',
        status_url: '/api/operations/op-shared-operation',
        events_url: '/api/client-events?operation_id=op-shared-operation',
        cursor: {epoch: 'operation-epoch', seq: 0},
      },
    });

    const sharedSource = api.clientEventTransportStateForTest().source;
    assert.equal(record.source, null, 'a normal receipt does not create a feature-local EventSource');
    const replacementSource = api.clientEventTransportStateForTest().replacementSource;
    assert.equal(sharedSource, initialSource, 'adding a pending operation preserves the global serving stream');
    assert.equal(replacementSource, null, 'operation replay IDs do not create connection demand');
    assert.equal(api.apiOperationStateForTest().pending, 1);

    sharedSource.listeners.get('operation_terminal')[0]({
      data: JSON.stringify({
        type: 'operation_terminal',
        payload: {
          operation: {id: 'op-shared-operation', cursor: {epoch: 'operation-epoch', seq: 1}},
          result: {state: 'ready', request: {id: 'r-shared-operation'}, data: {}},
        },
      }),
      type: 'operation_terminal',
      lastEventId: '',
    });
    await flushAsyncWork();

    assert.equal(api.apiOperationStateForTest().pending, 0, 'the shared operation event settles the registered receipt');
    assert.equal(api.clientEventTransportStateForTest().source, initialSource, 'settling a receipt keeps the same global stream');
  });

  await testAsync('an operation accepted before the first global stream is ready is present in that stream replay URL', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
    const preReadySource = api.clientEventTransportStateForTest().source;
    const operationId = 'op-pre-ready-replay';
    api.registerApiOperationReceiptForTest({
      request: {id: `r-${operationId}`},
      operation: {
        id: operationId,
        kind: 'session_files',
        context: {session: '1'},
        status_url: `/api/operations/${operationId}`,
        events_url: `/api/client-events?operation_id=${operationId}`,
        cursor: {epoch: 'operation-epoch', seq: 0},
      },
    });

    const replaySource = api.clientEventTransportStateForTest().source;
    assert.notEqual(replaySource, preReadySource, 'the pre-ready URL cannot claim an operation it never carried');
    assert.equal(preReadySource.readyState, 2, 'the stale pre-ready stream is retired before opening its replacement');
    assert.deepStrictEqual(new URL(replaySource.url, 'https://fixture.invalid').searchParams.get('operations'), operationId);
    assert.equal(api.clientEventTransportStateForTest().replacementSource, null, 'there is still only one pre-ready stream owner');

    replaySource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    replaySource.listeners.get('operation_terminal')[0]({
      data: JSON.stringify({
        type: 'operation_terminal',
        payload: {
          operation: {id: operationId, cursor: {epoch: 'operation-epoch', seq: 1}},
          result: {state: 'ready', request: {id: `r-${operationId}`}, data: {}},
        },
      }),
      type: 'operation_terminal',
      lastEventId: '',
    });
    assert.equal(api.apiOperationStateForTest().pending, 0, 'the replay-qualified stream terminalizes the accepted operation');
  });

  test('client-event pagehide disposes active and candidate streams exactly once and bfcache resumes demand', () => {
    const timers = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout(callback, delay) {
        const timer = {callback, delay, cleared: false};
        timers.push(timer);
        return timer;
      },
      clearTimeout(timer) { if (timer) timer.cleared = true; },
    });
    api.clearJsDebugEventsForTest();
    api.installClientEventStreamForTest();
    const active = api.clientEventTransportStateForTest().source;
    api.setEventLogTabActiveForTest('1', true);
    api.syncClientEventDemandForTest({immediate: true});
    const candidate = api.clientEventTransportStateForTest().replacementSource;
    active.onerror();
    const disconnectTimer = api.clientEventTransportStateForTest().disconnectTimer;
    assert.ok(disconnectTimer, 'an active-stream error owns one disconnect watchdog before page retirement');
    for (const listener of api.windowListenersForTest('pagehide')) listener({type: 'pagehide', persisted: true});
    assert.equal(active.closeCount, 1, 'pagehide closes the active stream once');
    assert.equal(candidate.closeCount, 1, 'pagehide closes the candidate stream once');
    assert.equal(api.clientEventTransportStateForTest().source, null);
    assert.equal(api.clientEventTransportStateForTest().disconnectTimer, null, 'pagehide restores the disconnect timer null sentinel');
    disconnectTimer.callback();
    assert.equal(api.jsDebugFailureEventsForTest().length, 0, 'a disposed disconnect watchdog cannot emit the production failure diagnostic');
    for (const listener of api.windowListenersForTest('pageshow')) listener({type: 'pageshow', persisted: true});
    const resumed = api.clientEventTransportStateForTest().source;
    assert.ok(resumed && resumed !== active && resumed !== candidate, 'bfcache pageshow opens one fresh demanded stream');
  });

  await testAsync('operation terminals acknowledge browser consumption only after completion and in one batch', async () => {
    const timers = [];
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
      setTimeout(callback, delay) {
        const handle = {callback, delay};
        timers.push(handle);
        return handle;
      },
      clearTimeout() {},
    });
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), options});
      return Promise.resolve(jsonResponse({ok: true, acknowledged: JSON.parse(options.body).acks.map(item => item.id)}));
    });
    const receipt = id => ({
      request: {id: `r-${id}`},
      operation: {id, kind: 'fs_watch_diff', cursor: {epoch: 'ack-epoch', seq: 0}},
    });
    const first = api.registerApiOperationReceiptForTest(receipt('op-ack-a'));
    const second = api.registerApiOperationReceiptForTest(receipt('op-ack-b'));
    const handled = [];
    api.addWindowEventListenerForTest('yolomux:operation-terminal', event => handled.push(event.detail.operation.id));

    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: first.id, cursor: {epoch: 'wrong-epoch', seq: 1}},
      result: {state: 'ready', data: {wrong: true}},
      status: 200,
    }), false);
    assert.equal(api.operationTerminalAckStateForTest().queued, 0, 'a rejected terminal cannot acknowledge consumption');
    for (const record of [first, second]) {
      assert.equal(api.applyApiOperationTerminalForTest({
        operation: {id: record.id, cursor: {epoch: 'ack-epoch', seq: 1}},
        result: {state: 'ready', data: {id: record.id}},
        status: 200,
      }), true);
      assert.ok(handled.includes(record.id), 'the feature terminal event is dispatched before its acknowledgment is queued');
    }
    assert.equal(requests.length, 0, 'completion only schedules the bounded batch');
    assert.equal(api.operationTerminalAckStateForTest().queued, 2);
    const flushTimer = timers.find(timer => timer.delay === api.operationTerminalAckDelayMsForTest());
    assert.ok(flushTimer, 'one bounded acknowledgment flush is scheduled');
    flushTimer.callback();
    await flushAsyncWork();

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/operations/ack');
    assert.equal(requests[0].options.method, 'POST');
    assert.deepStrictEqual(
      canonical(JSON.parse(requests[0].options.body).acks),
      [
        {id: 'op-ack-a', cursor: {epoch: 'ack-epoch', seq: 1}},
        {id: 'op-ack-b', cursor: {epoch: 'ack-epoch', seq: 1}},
      ],
    );
    assert.deepStrictEqual(handled, ['op-ack-a', 'op-ack-b']);
    assert.equal(api.operationTerminalAckStateForTest().queued, 0);
  });

  await testAsync('a lost operation acknowledgment response retains and retries the exact batch', async () => {
    const timers = [];
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
      setTimeout(callback, delay) {
        const handle = {callback, delay};
        timers.push(handle);
        return handle;
      },
      clearTimeout() {},
    });
    const bodies = [];
    api.setFetchForTest((_url, options = {}) => {
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1) return Promise.reject(new Error('response lost'));
      return Promise.resolve(jsonResponse({ok: true, acknowledged: ['op-ack-retry']}));
    });
    api.registerApiOperationReceiptForTest({
      request: {id: 'r-op-ack-retry'},
      operation: {id: 'op-ack-retry', kind: 'fs_watch_diff', cursor: {epoch: 'ack-retry-epoch', seq: 0}},
    });
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-ack-retry', cursor: {epoch: 'ack-retry-epoch', seq: 1}},
      result: {state: 'ready', data: {}},
      status: 200,
    }), true);
    timers.find(timer => timer.delay === api.operationTerminalAckDelayMsForTest()).callback();
    await flushAsyncWork();
    assert.equal(api.operationTerminalAckStateForTest().queued, 1, 'a lost response cannot retire the pending acknowledgment');
    const retry = timers.find(timer => timer.delay === 250);
    assert.ok(retry, 'the single owner schedules one bounded retry');
    retry.callback();
    await flushAsyncWork();

    assert.deepStrictEqual(canonical(bodies), [
      {acks: [{id: 'op-ack-retry', cursor: {epoch: 'ack-retry-epoch', seq: 1}}]},
      {acks: [{id: 'op-ack-retry', cursor: {epoch: 'ack-retry-epoch', seq: 1}}]},
    ]);
    assert.equal(api.operationTerminalAckStateForTest().queued, 0, 'an idempotent retry retires only the exact queued cursor');
  });

  await testAsync('channel replacement ignores operation churn while its subscriber opens', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
    const servingSource = api.clientEventTransportStateForTest().source;
    servingSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    const receipt = operationId => ({
      request: {id: `r-${operationId}`},
      operation: {
        id: operationId,
        kind: 'fs_watch_diff',
        status_url: `/api/operations/${operationId}`,
        events_url: `/api/client-events?operation_id=${operationId}`,
        cursor: {epoch: 'operation-epoch', seq: 0},
      },
    });
    api.registerApiOperationReceiptForTest(receipt('op-first'));
    api.setEventLogTabActiveForTest('1', true);
    api.syncClientEventDemandForTest({immediate: true});
    const channelReplacement = api.clientEventTransportStateForTest().replacementSource;
    assert.ok(channelReplacement, 'the changed channel set opens one replacement');
    api.registerApiOperationReceiptForTest(receipt('op-second'));
    assert.equal(api.clientEventTransportStateForTest().replacementSource, channelReplacement, 'operation churn keeps the one channel candidate');

    servingSource.listeners.get('operation_terminal')[0]({
      data: JSON.stringify({
        type: 'operation_terminal',
        payload: {
          operation: {id: 'op-first', cursor: {epoch: 'operation-epoch', seq: 1}},
          result: {state: 'ready', request: {id: 'r-op-first'}, data: {}},
        },
      }),
      type: 'operation_terminal',
      lastEventId: '',
    });
    assert.equal(api.apiOperationStateForTest().pending, 1);
    channelReplacement.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});

    assert.equal(servingSource.readyState, 2, 'the old serving stream closes after the channel candidate is ready');
    assert.equal(api.clientEventTransportStateForTest().source, channelReplacement, 'the channel candidate promotes despite operation churn');
    assert.equal(api.clientEventTransportStateForTest().replacementSource, null, 'operation churn does not open a corrected candidate');
  });

  await testAsync('sequential operation receipts retain one global client-event stream', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
    const source = api.clientEventTransportStateForTest().source;
    source.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});

    for (const operationId of ['op-first', 'op-second']) {
      api.registerApiOperationReceiptForTest({
        request: {id: `r-${operationId}`},
        operation: {
          id: operationId,
          kind: 'fs_watch_diff',
          status_url: `/api/operations/${operationId}`,
          events_url: `/api/client-events?operation_id=${operationId}`,
          cursor: {epoch: 'operation-epoch', seq: 0},
        },
      });
      assert.equal(api.clientEventTransportStateForTest().source, source, 'accepted work keeps the serving global stream');
      assert.equal(api.clientEventTransportStateForTest().replacementSource, null, 'an operation-ID change does not create a candidate stream');
      source.listeners.get('operation_terminal')[0]({
        data: JSON.stringify({
          type: 'operation_terminal',
          payload: {
            operation: {id: operationId, cursor: {epoch: 'operation-epoch', seq: 1}},
            result: {state: 'ready', request: {id: `r-${operationId}`}, data: {}},
          },
        }),
        type: 'operation_terminal',
        lastEventId: '',
      });
      assert.equal(api.apiOperationStateForTest().pending, 0, 'the same stream terminalizes the exact operation');
    }
  });

  await testAsync('filesystem read and diff receipts settle once through retained operation terminals', async () => {
    for (const timing of ['before-waiter', 'after-waiter', 'reconnect', 'native-reconnect']) {
      for (const operation of ['read', 'diff']) {
        const api = loadYolomux('', ['1']);
        let terminalEvents = 0;
        api.addWindowEventListenerForTest('yolomux:operation-terminal', () => { terminalEvents += 1; });
        api.clearJsDebugEventsForTest();
        if (timing === 'before-waiter' || timing === 'reconnect' || timing === 'native-reconnect') api.installClientEventStreamForTest();
        const operationId = `op-${timing}-${operation}`;
        let fetches = 0;
        api.setFetchForTest(url => {
          if (String(url) !== `/api/fs/${operation}`) return Promise.resolve(jsonResponse({}));
          fetches += 1;
          return Promise.resolve(jsonResponse({
            state: 'queued',
            request: {id: `r-${operationId}`},
            operation: {
              id: operationId,
              kind: 'filesystem_operation',
              context: {operation, path: `/tmp/${operation}.txt`},
              cursor: {epoch: 'filesystem-epoch', seq: 0},
            },
          }, 202));
        });
        const terminal = {
          operation: {id: operationId, cursor: {epoch: 'filesystem-epoch', seq: 1}},
          result: {state: 'ready', request: {id: `r-${operationId}`}, data: {operation, timing}},
          status: 200,
        };
        if (timing === 'before-waiter') {
          assert.equal(api.applyApiOperationTerminalForTest(terminal), true);
          assert.equal(terminalEvents, 0, 'an early terminal is cached without dispatch');
          assert.equal(api.jsDebugEventsForTest().filter(event => event.type === 'operation_wait').length, 0, 'an early terminal emits no telemetry before its receipt');
        }
        const resultPromise = api.fetchFilesystemOperationPayloadForTest(`/api/fs/${operation}`, operation);
        let settled = false;
        resultPromise.then(() => { settled = true; }, () => { settled = true; });
        await flushAsyncWork();
        if (timing === 'before-waiter') {
          assert.equal(api.clientEventTransportStateForTest().replacementSource, null, 'a matching retained terminal settles without opening replacement transport');
        } else if (timing === 'after-waiter') {
          assert.equal(api.applyApiOperationTerminalForTest({
            operation: {id: operationId, cursor: {epoch: 'filesystem-epoch', seq: 0}},
            result: {state: 'ready', data: {wrong: 'lower'}},
            status: 200,
          }), false, 'the receipt cursor cannot settle its own operation');
          assert.equal(api.applyApiOperationTerminalForTest({
            operation: {id: 'op-unrelated', cursor: {epoch: 'filesystem-epoch', seq: 1}},
            result: {state: 'ready', data: {wrong: 'unrelated'}},
            status: 200,
          }), true, 'an unrelated early terminal is retained without side effects');
          await flushAsyncWork();
          assert.equal(settled, false, 'lower and unrelated cursors do not settle the caller');
          assert.equal(api.applyApiOperationTerminalForTest(terminal), true);
        } else if (timing === 'reconnect' || timing === 'native-reconnect') {
          const sharedSource = api.clientEventTransportStateForTest().source;
          assert.ok(sharedSource, 'the pending operation retains the global stream');
          assert.equal(api.clientEventTransportStateForTest().replacementSource, null, 'the operation does not open a replacement stream');
          if (timing === 'native-reconnect') {
            sharedSource.onerror();
            assert.equal(api.clientEventTransportStateForTest().connected, false, 'native reconnect starts on the serving source');
            sharedSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
            assert.equal(api.clientEventTransportStateForTest().source, sharedSource, 'native ready reuses the same EventSource');
          }
          sharedSource.listeners.get('operation_terminal')[0]({
            data: JSON.stringify({type: 'operation_terminal', payload: terminal}),
            type: 'operation_terminal',
            lastEventId: '',
          });
          await flushAsyncWork();
        }
        assert.deepStrictEqual(canonical(await resultPromise), {operation, timing});
        assert.equal(fetches, 1, `${timing}/${operation} must not issue a continuation fetch`);
        assert.equal(terminalEvents, 1, `${timing}/${operation} dispatches one feature event`);
        assert.equal(api.jsDebugEventsForTest().filter(event => event.type === 'operation_wait').length, 1, `${timing}/${operation} emits one operation telemetry row`);
        assert.equal(api.apiOperationStateForTest().pending, 0);
        assert.equal(api.apiOperationStateForTest().waiters, 0);
        assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, `${timing}/${operation} invokes the terminal feature handler once`);
        assert.equal(api.applyApiOperationTerminalForTest(terminal), false, 'a duplicate terminal cursor settles nothing twice');
        assert.equal(api.applyApiOperationTerminalForTest({
          ...terminal,
          operation: {...terminal.operation, cursor: {epoch: 'filesystem-epoch', seq: 0}},
          result: {state: 'ready', data: {wrong: 'lower-after-terminal'}},
        }), false, 'a lower cursor cannot revisit an already settled operation');
        assert.equal(api.applyApiOperationTerminalForTest({
          ...terminal,
          operation: {...terminal.operation, cursor: {epoch: 'other-filesystem-epoch', seq: 2}},
          result: {state: 'ready', data: {wrong: 'different-epoch-after-terminal'}},
        }), false, 'a different epoch cannot revisit an already settled operation');
        assert.equal(api.applyApiOperationTerminalForTest({
          ...terminal,
          operation: {...terminal.operation, cursor: {epoch: 'filesystem-epoch', seq: 2}},
          result: {state: 'ready', data: {wrong: 'higher-after-terminal'}},
        }), false, 'a higher cursor cannot overwrite an already settled operation');
        assert.equal(terminalEvents, 1);
        assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, `${timing}/${operation} rejects every later terminal without another handler call`);
      }
    }

    const api = loadYolomux('', ['1']);
    api.setFetchForTest(() => Promise.resolve(jsonResponse({
      state: 'queued',
      operation: {
        id: 'op-failed-read',
        kind: 'filesystem_operation',
        context: {operation: 'read', path: '/tmp/missing.txt'},
        cursor: {epoch: 'filesystem-epoch', seq: 0},
      },
    }, 202)));
    const failed = api.fetchFilesystemOperationPayloadForTest('/api/fs/read', 'read');
    const observedFailure = failed.then(() => null, error => error);
    await flushAsyncWork();
    api.applyApiOperationTerminalForTest({
      operation: {id: 'op-unrelated', cursor: {epoch: 'filesystem-epoch', seq: 1}},
      result: {state: 'ready', data: {wrong: true}},
      status: 200,
    });
    api.applyApiOperationTerminalForTest({
      operation: {id: 'op-failed-read', cursor: {epoch: 'filesystem-epoch', seq: 1}},
      result: {
        state: 'failed',
        error: 'path not found: /tmp/missing.txt',
        user_message: {key: 'common.pathNotFound', params: {path: '/tmp/missing.txt'}, fallback: 'File not found'},
        status: 404,
      },
      status: 404,
    });
    const failure = await observedFailure;
    assert.equal(failure?.name, 'ApiOperationTerminalError');
    assert.equal(failure?.status, 404);
    assert.equal(failure?.code, 'common.pathNotFound');
    assert.equal(api.jsDebugFailureEventsForTest('rejection').length, 0, 'the consumer owns its rejection before the terminal arrives');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
  });

  await testAsync('a pre-ready candidate failure does not strand demand and recovers on a fresh candidate', async () => {
    const timers = [];
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
      setTimeout(callback, delay) {
        const handle = {callback, delay};
        timers.push(handle);
        return handle;
      },
      clearTimeout() {},
    });
    api.installClientEventStreamForTest();
    const activeSource = api.clientEventTransportStateForTest().source;
    assert.ok(activeSource, 'the active serving stream is open');

    // A changed channel set opens a CANDIDATE that has not yet fired ready.
    api.setEventLogTabActiveForTest('1', true);
    api.syncClientEventDemandForTest({immediate: true});
    const candidate = api.clientEventTransportStateForTest().replacementSource;
    assert.ok(candidate && candidate !== activeSource, 'a candidate stream opens for the changed demand');
    assert.ok(new URL(candidate.url, 'https://yolomux.test').searchParams.get('channels').split(',').includes('events'));
    assert.equal(api.clientEventTransportStateForTest().candidateEpisode.source, candidate, 'the candidate owns one bounded retry episode');

    // Transient pre-ready errors are tolerated within the bounded episode: the candidate is kept and
    // the active stream is never promoted-away or torn down.
    candidate.onerror();
    candidate.onerror();
    assert.equal(api.clientEventTransportStateForTest().replacementSource, candidate, 'the candidate survives errors within the bounded episode');
    assert.equal(api.clientEventTransportStateForTest().source, activeSource, 'the active stream is untouched while the candidate retries');
    assert.equal(candidate.readyState, 1, 'the candidate is not closed within the bounded episode');

    // Exhausting the episode must abandon the candidate, demote the active stream (it does not serve
    // the new demand), and re-drive demand into ONE corrected candidate rather than stranding it.
    candidate.onerror();
    assert.equal(candidate.readyState, 2, 'the exhausted candidate is closed');
    assert.equal(api.clientEventTransportStateForTest().connected, false, 'the active stream is demoted, not claimed to serve the new demand');
    const freshCandidate = api.clientEventTransportStateForTest().replacementSource;
    assert.ok(freshCandidate && freshCandidate !== candidate, 'demand is re-driven into a fresh candidate');
    assert.ok(new URL(freshCandidate.url, 'https://yolomux.test').searchParams.get('channels').split(',').includes('events'), 'the fresh candidate still carries the demanded channel');
    assert.equal(api.clientEventTransportStateForTest().candidateEpisode.source, freshCandidate, 'a new bounded episode governs the fresh candidate');
    assert.ok(timers.some(timer => timer.delay === api.reconnectResyncDebounceMsForTest?.() || timer.delay === 751), 'an HTTP resync is scheduled so no channel is stranded');

    // The fresh candidate becoming ready promotes it to active and restores serving.
    freshCandidate.listeners.get('ready')[0]({data: JSON.stringify({epoch: 'candidate-epoch', resource_revisions: {}}), type: 'ready', lastEventId: ''});
    assert.equal(api.clientEventTransportStateForTest().source, freshCandidate, 'the recovered candidate becomes the active stream');
    assert.equal(api.clientEventTransportStateForTest().replacementSource, null, 'no candidate remains after recovery');
    assert.equal(api.clientEventTransportStateForTest().candidateEpisode, null, 'the retry episode is cleared once the candidate is serving');
    assert.equal(api.clientEventTransportStateForTest().connected, true, 'serving is restored after recovery');
    assert.equal(activeSource.readyState, 2, 'the old active stream closes only after the corrected demand is ready');
  });

  await testAsync('filesystem operation waiters preserve the caller deadline with one request', async () => {
    let now = 0;
    let nextTimer = 1;
    const timers = new Map();
    const setTimeout = (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, {callback, due: now + Number(delay)});
      return id;
    };
    const clearTimeout = id => timers.delete(id);
    const advance = milliseconds => {
      now += milliseconds;
      for (const [id, timer] of [...timers.entries()].sort((left, right) => left[1].due - right[1].due)) {
        if (timer.due > now) continue;
        timers.delete(id);
        timer.callback();
      }
    };
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout,
      clearTimeout,
      performance: {now: () => now},
    });
    let fetches = 0;
    api.setFetchForTest(() => {
      fetches += 1;
      return Promise.resolve(jsonResponse({
        state: 'queued',
        operation: {
          id: 'op-deadline-read',
          kind: 'filesystem_operation',
          context: {operation: 'read', path: '/tmp/deadline.txt'},
          cursor: {epoch: 'deadline-epoch', seq: 0},
        },
      }, 202));
    });

    const result = api.fetchFilesystemOperationPayloadForTest('/api/fs/read', 'read').then(
      () => null,
      error => error,
    );
    await flushAsyncWork();
    assert.equal(api.apiOperationStateForTest().pending, 1);
    assert.equal(api.apiOperationStateForTest().waiters, 1);
    advance(119999);
    await flushAsyncWork();
    assert.equal(api.apiOperationStateForTest().pending, 1, '119,999 ms remains inside the caller deadline');
    advance(1);
    const error = await result;

    assert.equal(error?.name, 'ApiFetchDeadlineError');
    assert.equal(error?.code, 'deadline_expired');
    assert.equal(error?.status, 504);
    assert.equal(fetches, 1, 'expiry never starts a continuation request');
    assert.equal(api.apiOperationStateForTest().pending, 1, 'the accepted operation remains demanded after its UI waiter expires');
    assert.equal(api.apiOperationStateForTest().waiters, 0);
    assert.deepStrictEqual(canonical(api.fixtureLifecycleOperationStateForTest().pending), ['op-deadline-read'], 'L3 quiescence still observes the accepted operation');
    const failures = api.jsDebugFailureEventsForTest('error');
    assert.equal(failures.length, 1);
    assert.equal(failures[0].endpoint, '/api/fs/read');
    assert.equal(failures[0].error, 'deadline_expired: request exceeded its 120s deadline');
    assert.equal(api.jsDebugFailureEventsForTest('rejection').length, 0);
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-deadline-read', cursor: {epoch: 'deadline-epoch', seq: 1}},
      result: {state: 'ready', data: {path: '/tmp/deadline.txt', content: 'late but authoritative'}},
      status: 200,
    }), true, 'a matching backend terminal still settles the accepted operation');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().terminal, 1, 'the late terminal remains replayable');
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, 'the late terminal dispatches through the shared owner once');
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-deadline-read', cursor: {epoch: 'deadline-epoch', seq: 2}},
      result: {state: 'failed', error: 'conflicting duplicate'},
      status: 500,
    }), false, 'a conflicting duplicate cannot replace the retained terminal');
  });

  await testAsync('a filesystem operation waiter without an override uses the accepted-operation budget', async () => {
    let now = 0;
    let nextTimer = 1;
    const timers = new Map();
    const setTimeout = (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, {callback, due: now + Number(delay)});
      return id;
    };
    const advance = milliseconds => {
      now += milliseconds;
      for (const [id, timer] of [...timers.entries()].sort((left, right) => left[1].due - right[1].due)) {
        if (timer.due > now) continue;
        timers.delete(id);
        timer.callback();
      }
    };
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout,
      clearTimeout: id => timers.delete(id),
      performance: {now: () => now},
    });
    const receipt = {
      state: 'queued',
      operation: {
        id: 'op-default-accepted-deadline',
        kind: 'auto_approve_operation',
        context: {},
        cursor: {epoch: 'default-accepted-deadline', seq: 0},
      },
    };
    api.registerApiOperationReceiptForTest(receipt);
    const result = api.waitForApiOperationResultForTest(receipt, {
      kind: 'auto_approve_operation',
    }).then(() => null, error => error);

    advance(119999);
    await flushAsyncWork();
    assert.equal(api.apiOperationStateForTest().waiters, 1, 'the default waiter remains live through 119,999 ms');
    advance(1);
    const error = await result;
    assert.equal(error?.code, 'deadline_expired');
    assert.equal(error?.payload?.timeout_ms, 120000, 'the no-override waiter owns the accepted-operation budget');
    assert.equal(api.apiOperationStateForTest().waiters, 0);
  });

  await testAsync('one filesystem waiter can detach while another retains the accepted operation', async () => {
    let now = 0;
    let nextTimer = 1;
    const timers = new Map();
    const setTimeout = (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, {callback, due: now + Number(delay)});
      return id;
    };
    const advance = milliseconds => {
      now += milliseconds;
      for (const [id, timer] of [...timers.entries()].sort((left, right) => left[1].due - right[1].due)) {
        if (timer.due > now) continue;
        timers.delete(id);
        timer.callback();
      }
    };
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout,
      clearTimeout: id => timers.delete(id),
      performance: {now: () => now},
    });
    const receipt = {
      state: 'queued',
      request: {id: 'r-multiple-waiters'},
      operation: {
        id: 'op-multiple-waiters',
        kind: 'filesystem_operation',
        context: {operation: 'search', path: '/tmp'},
        cursor: {epoch: 'multiple-waiters-epoch', seq: 0},
      },
    };
    api.registerApiOperationReceiptForTest(receipt);
    const short = api.waitForApiOperationResultForTest(receipt, {
      kind: 'filesystem_operation',
      operation: 'search',
      deadlineMs: 1000,
    }).then(() => null, error => error);
    const live = api.waitForApiOperationResultForTest(receipt, {
      kind: 'filesystem_operation',
      operation: 'search',
      deadlineMs: 3000,
    });
    advance(1000);
    const detached = await short;
    assert.equal(detached?.code, 'deadline_expired');
    assert.equal(api.apiOperationStateForTest().pending, 1);
    assert.equal(api.apiOperationStateForTest().waiters, 1, 'only the expired consumer detaches');
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-multiple-waiters', cursor: {epoch: 'multiple-waiters-epoch', seq: 1}},
      result: {state: 'ready', data: {matches: 7}},
      status: 200,
    }), true);
    assert.deepStrictEqual(canonical(await live), {matches: 7});
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1);
  });

  await testAsync('operation replay retention is bounded while preserving delayed terminal-before-receipt delivery', async () => {
    const api = loadYolomux('', ['1']);
    const retainedLimit = 128;
    const largePayload = 'x'.repeat(256 * 1024);
    for (let index = 0; index < retainedLimit + 2; index += 1) {
      assert.equal(api.applyApiOperationTerminalForTest({
        operation: {id: `op-early-${index}`, cursor: {epoch: 'bounded-early', seq: index + 1}},
        result: {state: 'ready', data: {index, largePayload}},
        status: 200,
      }), true);
    }
    assert.equal(api.apiOperationStateForTest().terminal, retainedLimit, 'early terminal payload retention has one explicit count bound');
    assert.equal(api.apiOperationTerminalForTest('op-early-0'), null, 'the oldest unconsumed replay payload is evicted first');
    const delayedReceipt = {
      state: 'queued',
      operation: {
        id: `op-early-${retainedLimit + 1}`,
        kind: 'filesystem_operation',
        context: {operation: 'read', path: '/tmp/retained.txt'},
        cursor: {epoch: 'bounded-early', seq: 0},
      },
    };
    assert.deepStrictEqual(
      canonical(await api.waitForApiOperationResultForTest(delayedReceipt, {kind: 'filesystem_operation', operation: 'read'})),
      {index: retainedLimit + 1, largePayload},
      'a delayed valid receipt consumes an in-bound terminal-before-receipt payload exactly once',
    );

    for (let index = 0; index < retainedLimit + 2; index += 1) {
      const id = `op-complete-${index}`;
      api.registerApiOperationReceiptForTest({
        state: 'queued',
        operation: {
          id,
          kind: 'filesystem_operation',
          context: {operation: 'diff', path: `/tmp/${index}.txt`},
          cursor: {epoch: 'bounded-complete', seq: 0},
        },
      });
      assert.equal(api.applyApiOperationTerminalForTest({
        operation: {id, cursor: {epoch: 'bounded-complete', seq: index + 1}},
        result: {state: 'ready', data: {index, largePayload}},
        status: 200,
      }), true);
    }
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.ok(api.apiOperationStateForTest().records <= retainedLimit, 'completed receipt records share the terminal replay bound');
    assert.ok(api.apiOperationStateForTest().terminal <= retainedLimit, 'completed payloads cannot grow the replay cache past its bound');
    assert.equal(api.apiOperationTerminalForTest('op-complete-0'), null, 'completed payload eviction uses the same oldest-first owner');
  });

  await testAsync('session-scoped operation terminal settles globally but skips stale same-name generation paint', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly', {
      locationHash: '#t=session-generation-operation',
    });
    const receipt = {
      state: 'queued',
      operation: {
        id: 'op-old-generation',
        kind: 'filesystem_operation',
        context: {operation: 'diff', path: '/tmp/old.txt', session: '1'},
        events_url: '/api/client-events?operation_id=op-old-generation',
        cursor: {epoch: 'generation-operation', seq: 0},
      },
    };
    const record = api.registerApiOperationReceiptForTest(receipt);
    const waiter = api.waitForApiOperationResultForTest(receipt, {kind: 'filesystem_operation', operation: 'diff'});
    const killed = api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(killed);
    const recreated = api.beginTmuxSessionLifecycleMutationForTest('create', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(recreated, {session: '1'});
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-old-generation', cursor: {epoch: 'generation-operation', seq: 1}},
      result: {state: 'ready', data: {diff: 'old-generation-result'}},
      status: 200,
    }), true);
    assert.deepStrictEqual(canonical(await waiter), {diff: 'old-generation-result'}, 'the accepted backend operation settles its detached generation waiter');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
    assert.equal(record.handlerInvocations, 0, 'the stale generation invokes no feature renderer');

    const nextReceipt = {
      state: 'queued',
      operation: {
        id: 'op-new-generation',
        kind: 'filesystem_operation',
        context: {operation: 'diff', path: '/tmp/new.txt', session: '1'},
        cursor: {epoch: 'generation-operation', seq: 1},
      },
    };
    const nextRecord = api.registerApiOperationReceiptForTest(nextReceipt);
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-new-generation', cursor: {epoch: 'generation-operation', seq: 2}},
      result: {state: 'ready', data: {diff: 'new-generation-result'}},
      status: 200,
    }), true);
    assert.equal(nextRecord.handlerInvocations, 1, 'the replacement generation still reaches its feature renderer');
  });

  await testAsync('same-name recreation rejects stale terminal socket close and reconnect work', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({exists: false}));
    });
    const term = {write() {}, dispose() {}};
    const item = api.registerTerminalForTest('1', term, {readyState: WebSocket.CLOSED, close() {}});
    api.connectTerminalSocketForTest('1', item);
    const oldSocket = item.socket;
    const killed = api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(killed);
    const recreated = api.beginTmuxSessionLifecycleMutationForTest('create', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(recreated, {session: '1'});
    api.connectTerminalSocketForTest('1', item);
    const newSocket = item.socket;
    assert.notEqual(newSocket, oldSocket);
    oldSocket.onclose?.({target: oldSocket, code: 1006, wasClean: false});
    oldSocket.onerror?.({target: oldSocket});
    await flushAsyncWork();
    assert.equal(requests.some(url => url.includes('/api/tmux-session-exists')), false, 'a stale same-name socket close cannot issue an existence check');
    assert.equal(item.socket, newSocket, 'the recreated generation retains its own socket');
    assert.equal(newSocket.readyState, WebSocket.OPEN, 'the stale close cannot close the replacement socket');
    assert.equal(api.tmuxSessionLifecycleTokenIsCurrentForTest(item.sessionLifecycleToken), true);
  });

  await testAsync('retired summary and transcript streams cannot paint or reconnect into a same-name generation', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [1500]});
    api.startSummaryStreamForTest('1');
    api.startTranscriptStreamForTest('1');
    const oldSummary = api.summaryStreamForTest('1');
    const oldTranscript = api.transcriptStreamForTest('1');
    oldTranscript.onerror?.();
    const killed = api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(killed);
    const recreated = api.beginTmuxSessionLifecycleMutationForTest('create', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(recreated, {session: '1'});
    api.startSummaryStreamForTest('1');
    api.startTranscriptStreamForTest('1');
    const newSummary = api.summaryStreamForTest('1');
    const newTranscript = api.transcriptStreamForTest('1');
    assert.equal(oldSummary.closeCount, 1, 'replacing a retired summary closes its source exactly once');
    assert.equal(oldTranscript.closeCount, 1, 'the transcript error releases its source exactly once');
    oldSummary.listeners.get('delta')[0]({data: JSON.stringify({text: 'stale summary bytes'})});
    oldSummary.onerror?.();
    oldTranscript.listeners.get('items')[0]({data: JSON.stringify({items: [{role: 'user', content: 'stale transcript bytes'}]})});
    oldTranscript.onerror?.();
    await flushAsyncWork();
    assert.equal(api.summaryStreamForTest('1'), newSummary, 'stale summary callbacks cannot close the replacement source');
    assert.equal(api.transcriptStreamForTest('1'), newTranscript, 'stale transcript callbacks and timers cannot reconnect over the replacement source');
    assert.equal(api.testElementForId('summary-1').innerHTML.includes('stale summary bytes'), false);
    assert.equal(api.testElementForId('transcript-1').innerHTML.includes('stale transcript bytes'), false);
    assert.equal(api.tmuxSessionLifecycleRecordForTest('1').sources, 2, 'the replacement generation owns exactly its summary and transcript streams');
    api.stopSummaryStreamForTest('1', oldSummary);
    api.stopTranscriptStreamForTest('1', oldTranscript);
    assert.equal(api.summaryStreamForTest('1'), newSummary, 'expected-source stop cannot dispose a replacement summary scope');
    assert.equal(api.transcriptStreamForTest('1'), newTranscript, 'expected-source stop cannot dispose a replacement transcript scope');
    api.stopSummaryStreamForTest('1', newSummary);
    api.stopSummaryStreamForTest('1', newSummary);
    api.stopTranscriptStreamForTest('1', newTranscript);
    api.stopTranscriptStreamForTest('1', newTranscript);
    assert.equal(newSummary.closeCount, 1, 'summary scope disposal is idempotent');
    assert.equal(newTranscript.closeCount, 1, 'transcript scope disposal is idempotent');
  });

  await testAsync('chat lifecycle disposal aborts requests and suppresses owned timers', async () => {
    let nextTimer = 1;
    const timers = new Map();
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
      setTimeout(callback) {
        const timer = nextTimer;
        nextTimer += 1;
        timers.set(timer, callback);
        return timer;
      },
      clearTimeout(timer) { timers.delete(timer); },
    });
    api.setFetchForTest(() => Promise.resolve(jsonResponse({ok: true})));
    api.chatRequestOptionsForTest();
    api.replaceChatTypingForTest([{username: 'alice', expires_at_utc: (Date.now() / 1000) + 60}]);
    const before = api.chatLifecycleStateForTest();
    assert.equal(before.active, true);
    assert.equal(before.requestController.signal.aborted, false);
    assert.notEqual(before.typingExpiryTimer, null);
    const lateTypingExpiry = timers.get(before.typingExpiryTimer);
    api.clearChatLifecycleForTest({destroy: true});
    assert.equal(before.requestController.signal.aborted, true, 'chat destroy aborts the shared request controller');
    assert.equal(api.chatLifecycleStateForTest().active, false, 'chat destroy retires the whole resource scope');
    assert.equal(timers.has(before.typingExpiryTimer), false, 'chat destroy clears the owned typing expiry');
    lateTypingExpiry();
    assert.equal(api.chatLifecycleStateForTest().active, false, 'a late retired timer cannot recreate chat lifecycle work');
  });

  test('lifecycle scope disposes replacement and late resources exactly once', () => {
    const api = loadYolomux('', ['1']);
    const disposed = [];
    const scope = api.createLifecycleScopeForTest();
    const first = {name: 'first'};
    const replacement = {name: 'replacement'};
    scope.replace('resource', first, value => disposed.push(value.name));
    scope.replace('resource', replacement, value => disposed.push(value.name));
    assert.deepStrictEqual(disposed, ['first'], 'replacement disposes the previous owner immediately');
    assert.equal(scope.dispose('test'), true);
    assert.equal(scope.dispose('again'), false, 'scope disposal is idempotent');
    assert.deepStrictEqual(disposed, ['first', 'replacement']);
    scope.replace('resource', {name: 'late'}, value => disposed.push(value.name));
    assert.deepStrictEqual(disposed, ['first', 'replacement', 'late'], 'registration after disposal cannot leak a resource');
  });

  await testAsync('latest resource owns dedupe, latest-wins, last-good failure, and state order', async () => {
    const api = loadYolomux('', ['1']);
    const pending = [];
    const phases = [];
    const resource = api.createLatestResourceForTest({
      initial: {marker: 'initial'},
      load(target) {
        const request = deferredFetch();
        pending.push({...request, target});
        return request.promise;
      },
      apply(payload) { return payload; },
      onState(_state, event) { phases.push(event.phase); },
    });
    const old = resource.read('old');
    assert.strictEqual(resource.read('old'), old, 'the same target shares one in-flight request');
    const newer = resource.read('new');
    pending[1].resolve({marker: 'new'});
    await newer;
    pending[0].resolve({marker: 'old'});
    await old;
    assert.equal(resource.snapshot().value.marker, 'new', 'a delayed old response cannot replace the newer target');
    const failed = resource.read('failed');
    pending[2].reject(new Error('offline'));
    await failed;
    assert.equal(resource.snapshot().value.marker, 'new', 'a current failure preserves the last good value');
    assert.equal(resource.snapshot().error.message, 'offline', 'the current failure remains typed for the consumer');
    assert.deepStrictEqual(phases, ['loading', 'loading', 'applied', 'settled', 'loading', 'failed', 'settled'], 'render-facing state order is deterministic and stale work is silent');
  });

  await testAsync('retired terminal generation owns delayed resize scroll and blank refresh side effects', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [30, 220]});
    const sent = [];
    const term = {cols: 80, rows: 24, buffer: {active: {length: 0}}, refresh() {}};
    const item = api.registerTerminalForTest('1', term, {readyState: 1, send(value) { sent.push(value); }});
    api.scheduleRemoteResizeForTest('1', 220);
    api.queueTmuxScrollForTest('1', item, 5);
    api.scheduleTerminalBlankScreenRefreshForTest('1', {delayMs: 220});
    const killed = api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(killed);
    const recreated = api.beginTmuxSessionLifecycleMutationForTest('create', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(recreated, {session: '1'});
    api.registerTerminalForTest('1', term, {readyState: 1, send(value) { sent.push(`new:${value}`); }});
    await flushAsyncWork();
    assert.deepStrictEqual(sent, [], 'retired timers cannot send resize, tmux-scroll, or blank-refresh frames into same-name recreation');
  });

  await testAsync('detached consumers retain authoritative filesystem error and timeout terminals', async () => {
    for (const terminal of [
      {status: 404, code: 'common.pathNotFound', error: 'path not found'},
      {status: 504, code: 'deadline_expired', error: 'backend operation deadline expired'},
    ]) {
      const api = loadYolomux('', ['1']);
      const operationId = `op-detached-${terminal.status}`;
      const epoch = `detached-${terminal.status}-epoch`;
      const receipt = {
        state: 'queued',
        request: {id: `r-${operationId}`},
        operation: {
          id: operationId,
          kind: 'filesystem_operation',
          context: {operation: 'read', path: '/tmp/missing.txt'},
          cursor: {epoch, seq: 0},
        },
      };
      const controller = new AbortController();
      const detached = api.waitForApiOperationResultForTest(receipt, {
        kind: 'filesystem_operation',
        operation: 'read',
        signal: controller.signal,
      }).then(() => null, error => error);
      controller.abort(new DOMException('consumer retired', 'AbortError'));
      assert.equal((await detached)?.name, 'AbortError');
      const payload = {
        operation: {id: operationId, cursor: {epoch, seq: 1}},
        result: {
          state: 'failed',
          error: terminal.error,
          user_message: {key: terminal.code, fallback: terminal.error},
          status: terminal.status,
        },
        status: terminal.status,
      };
      assert.equal(api.applyApiOperationTerminalForTest(payload), true);
      const replay = api.waitForApiOperationResultForTest(receipt, {
        kind: 'filesystem_operation',
        operation: 'read',
      }).then(() => null, error => error);
      const replayError = await replay;
      assert.equal(replayError?.name, 'ApiOperationTerminalError');
      assert.equal(replayError?.status, terminal.status);
      assert.equal(replayError?.code, terminal.code);
      assert.equal(api.apiOperationStateForTest().pending, 0);
      assert.equal(api.apiOperationStateForTest().waiters, 0);
      assert.equal(api.apiOperationStateForTest().handlerInvocations, 1);
      assert.equal(api.applyApiOperationTerminalForTest({...payload, operation: {...payload.operation, cursor: {epoch, seq: 2}}}), false);
      assert.equal(api.apiOperationTerminalForTest(operationId).status, terminal.status, 'the first terminal remains stable for replay');
    }
  });

  await testAsync('filesystem operation custom deadlines retain only the budget remaining after receipt admission', async () => {
    let now = 100;
    let nextTimer = 1;
    const timers = new Map();
    const setTimeout = (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, {callback, delay: Number(delay), due: now + Number(delay)});
      return id;
    };
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout,
      clearTimeout: id => timers.delete(id),
      performance: {now: () => now},
    });
    const receipt = {
      state: 'queued',
      operation: {
        id: 'op-custom-deadline',
        kind: 'filesystem_operation',
        context: {operation: 'read', path: '/tmp/custom.txt'},
        cursor: {epoch: 'custom-deadline-epoch', seq: 0},
      },
    };
    api.registerApiOperationReceiptForTest(receipt);
    now += 275;
    const timerIdsBeforeWait = new Set(timers.keys());
    const result = api.waitForApiOperationResultForTest(receipt, {
      kind: 'filesystem_operation',
      operation: 'read',
      deadlineMs: 1000,
    });
    const waiterTimers = [...timers.entries()].filter(([id]) => !timerIdsBeforeWait.has(id));
    assert.deepStrictEqual(waiterTimers.map(([, timer]) => timer.delay), [725], 'the waiter owns only the remaining custom budget');
    api.applyApiOperationTerminalForTest({
      operation: {id: 'op-custom-deadline', cursor: {epoch: 'custom-deadline-epoch', seq: 1}},
      result: {state: 'ready', data: {path: '/tmp/custom.txt'}},
      status: 200,
    });
    assert.deepStrictEqual(canonical(await result), {path: '/tmp/custom.txt'});
    assert.equal(timers.has(waiterTimers[0][0]), false, 'settlement clears the waiter-owned deadline timer');
  });

  await testAsync('an admitted filesystem receipt rejects a later wrong-epoch terminal', async () => {
    const api = loadYolomux('', ['1']);
    const operationId = 'op-wrong-epoch-after-receipt';
    const receipt = {
      state: 'queued',
      request: {id: `r-${operationId}`},
      operation: {
        id: operationId,
        kind: 'filesystem_operation',
        context: {operation: 'read', path: '/tmp/after-receipt.txt'},
        cursor: {epoch: 'expected-after-receipt', seq: 0},
      },
    };
    const wrong = {
      operation: {id: operationId, cursor: {epoch: 'wrong-after-receipt', seq: 1}},
      result: {state: 'ready', data: {wrong: true}},
      status: 200,
    };
    const valid = {
      operation: {id: operationId, cursor: {epoch: 'expected-after-receipt', seq: 1}},
      result: {state: 'ready', data: {order: 'after-receipt'}},
      status: 200,
    };
    let terminalEvents = 0;
    api.addWindowEventListenerForTest('yolomux:operation-terminal', () => { terminalEvents += 1; });
    api.clearJsDebugEventsForTest();
    api.registerApiOperationReceiptForTest(receipt);
    const resultPromise = api.waitForApiOperationResultForTest(receipt, {
      kind: 'filesystem_operation',
      operation: 'read',
    });
    assert.equal(api.applyApiOperationTerminalForTest(wrong), false, 'an admitted receipt rejects a wrong epoch');
    assert.equal(api.applyApiOperationTerminalForTest(valid), true, 'the exact terminal still settles after the wrong epoch');
    assert.deepStrictEqual(canonical(await resultPromise), {order: 'after-receipt'});
    assert.equal(canonical(api.apiOperationTerminalForTest(operationId).operation.cursor).epoch, 'expected-after-receipt');
    assert.equal(terminalEvents, 1, 'the exact terminal dispatches once');
    assert.equal(api.jsDebugEventsForTest().filter(event => event.type === 'operation_wait').length, 1, 'the exact terminal records one telemetry row');
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, 'the exact terminal invokes the feature handler once');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
  });

  await testAsync('a pre-receipt wrong epoch still demands the exact operation on the shared stream', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
    const initialSource = api.clientEventTransportStateForTest().source;
    initialSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    const operationId = 'op-wrong-epoch-shared-stream';
    const receipt = {
      state: 'queued',
      request: {id: `r-${operationId}`},
      operation: {
        id: operationId,
        kind: 'filesystem_operation',
        context: {operation: 'read', path: '/tmp/shared-stream.txt'},
        events_url: `/api/client-events?operation_id=${operationId}`,
        cursor: {epoch: 'expected-shared-stream', seq: 0},
      },
    };
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: operationId, cursor: {epoch: 'wrong-shared-stream', seq: 1}},
      result: {state: 'ready', data: {wrong: true}},
      status: 200,
    }), true, 'an unknown wrong-epoch terminal is retained before receipt admission');
    let terminalEvents = 0;
    api.addWindowEventListenerForTest('yolomux:operation-terminal', () => { terminalEvents += 1; });
    api.clearJsDebugEventsForTest();
    api.registerApiOperationReceiptForTest(receipt);
    assert.equal(api.apiOperationTerminalForTest(operationId), null, 'receipt admission removes the mismatched retained terminal');
    const resultPromise = api.waitForApiOperationResultForTest(receipt, {kind: 'filesystem_operation', operation: 'read'});
    const replacementSource = api.clientEventTransportStateForTest().replacementSource;
    assert.equal(replacementSource, null, 'discarding a mismatched terminal keeps the global shared stream');
    initialSource.listeners.get('operation_terminal')[0]({
      data: JSON.stringify({
        type: 'operation_terminal',
        payload: {
          operation: {id: operationId, cursor: {epoch: 'expected-shared-stream', seq: 1}},
          result: {state: 'ready', data: {transport: 'shared'}},
          status: 200,
        },
      }),
      type: 'operation_terminal',
      lastEventId: '',
    });
    assert.deepStrictEqual(canonical(await resultPromise), {transport: 'shared'});
    assert.equal(canonical(api.apiOperationTerminalForTest(operationId).operation.cursor).epoch, 'expected-shared-stream');
    assert.equal(terminalEvents, 1, 'the shared terminal dispatches once');
    assert.equal(api.jsDebugEventsForTest().filter(event => event.type === 'operation_wait').length, 1, 'the shared terminal records one telemetry row');
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, 'the shared terminal invokes the feature handler once');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
  });

  await testAsync('every ordinary filesystem route shares one cold receipt await owner', async () => {
    const operations = [
      ['list', '/api/fs/list?path=%2Ftmp'],
      ['search', '/api/fs/search?root=%2Ftmp&query=x'],
      ['index_status', '/api/fs/index-status?root=%2Ftmp'],
      ['read', '/api/fs/read?path=%2Ftmp%2Fa'],
      ['info', '/api/fs/info?path=%2Ftmp%2Fa'],
      ['diff', '/api/fs/diff?path=%2Ftmp%2Fa'],
      ['blame', '/api/batch/blame?path=%2Ftmp%2Fa'],
      ['count', '/api/batch/count?path=%2Ftmp'],
      ['write', '/api/fs/write'],
      ['delete', '/api/fs/delete'],
      ['unindex', '/api/fs/unindex?root=%2Ftmp'],
      ['rename', '/api/fs/rename'],
      ['mkdir', '/api/fs/mkdir'],
    ];
    for (const [operation, url] of operations) {
      const api = loadYolomux('', ['1']);
      let fetches = 0;
      api.setFetchForTest(() => {
        fetches += 1;
        return Promise.resolve(jsonResponse({
          state: 'queued',
          request: {id: `r-${operation}`},
          operation: {
            id: `op-${operation}`,
            kind: 'filesystem_operation',
            context: {operation, path: '/tmp/a'},
            cursor: {epoch: `epoch-${operation}`, seq: 0},
          },
        }, 202));
      });
      const result = api.apiFetchJsonForTest(url, operation === 'write' ? {method: 'POST', body: '{}'} : {});
      await flushAsyncWork();
      api.applyApiOperationTerminalForTest({
        operation: {id: `op-${operation}`, cursor: {epoch: `epoch-${operation}`, seq: 1}},
        result: {state: 'ready', request: {id: `r-${operation}`}, data: {operation}},
        status: 200,
      });
      assert.deepStrictEqual(canonical(await result), {operation});
      assert.equal(fetches, 1, `${operation} uses one request`);
      assert.equal(api.apiOperationStateForTest().pending, 0);
      assert.equal(api.apiOperationStateForTest().waiters, 0);
    }
  });

  await testAsync('editor open diff and save consume cold filesystem terminals before mutating state', async () => {
    const api = loadYolomux('', ['1']);
    const path = '/repo/cold-editor.txt';
    let sequence = 0;
    const receipts = [];
    api.clearJsDebugEventsForTest();
    api.setFetchForTest((url, options = {}) => {
      const route = String(url);
      const operation = route.startsWith('/api/fs/read') ? 'read'
        : route.startsWith('/api/fs/info') ? 'info'
        : route.startsWith('/api/fs/diff') ? 'diff'
          : route === '/api/fs/write' && options.method === 'POST' ? 'write' : '';
      assert.ok(operation, `unexpected editor fetch ${route}`);
      sequence += 1;
      const receipt = {operation, id: `op-editor-${operation}-${sequence}`, epoch: `editor-${operation}-${sequence}`};
      receipts.push(receipt);
      return Promise.resolve(jsonResponse({
        state: 'queued',
        request: {id: `r-editor-${operation}-${sequence}`},
        operation: {
          id: receipt.id,
          kind: 'filesystem_operation',
          context: {operation, path},
          cursor: {epoch: receipt.epoch, seq: 0},
        },
      }, 202));
    });
    const settle = (receipt, data) => api.applyApiOperationTerminalForTest({
      operation: {id: receipt.id, cursor: {epoch: receipt.epoch, seq: 1}},
      result: {state: 'ready', request: {id: `r-${receipt.id}`}, data},
      status: 200,
    });

    const opened = api.openFileInEditorForTest(path, {name: 'cold-editor.txt'}, {viewMode: 'edit'});
    await flushAsyncWork();
    assert.equal(api.currentFileStateForTest(path).loading, true, 'the selected tab exists in Loading state before its cold read terminal arrives');
    const repeatedOpen = api.openFileInEditorForTest(path, {name: 'cold-editor.txt'}, {viewMode: 'edit'});
    assert.equal(api.currentFileStateForTest(path).loading, true, 'reopening the same pending file focuses its existing Loading tab without waiting');
    assert.equal(receipts.filter(receipt => receipt.operation === 'read').length, 1, 'reopening a pending file does not submit a second read');
    settle(receipts.at(-1), {
      path,
      content: 'original\n',
      size: 9,
      mtime: 1,
      mtime_ns: 1,
      realpath: path,
      file_id: 'dev:1:ino:2',
      git_root: '/repo',
      git_tracked: true,
      git_history: [{ref: 'HEAD'}],
      git_has_history: true,
    });
    assert.ok(await opened);
    assert.ok(await repeatedOpen);
    assert.equal(api.currentFileStateForTest(path).content, 'original\n');
    await flushAsyncWork();
    assert.equal(receipts.filter(receipt => receipt.operation === 'read').length, 1, 'the base read paints before metadata decoration starts');
    assert.equal(receipts.filter(receipt => receipt.operation === 'info').length, 1, 'the deferred Git operation reads metadata without rereading content');
    settle(receipts.at(-1), {
      path,
      git_root: '/repo',
      git_tracked: true,
      git_history: [{ref: 'HEAD'}],
      git_has_history: true,
    });
    await flushAsyncWork();

    const diff = api.refreshOpenFileDiffForTest(path, {silent: true, renderOnComplete: false});
    await flushAsyncWork();
    settle(receipts.at(-1), {
      path,
      diff: '-original\n+changed\n',
      original: 'original\n',
      working: 'changed\n',
      repo: '/repo',
      relative_path: 'cold-editor.txt',
      from_ref: 'HEAD',
      to_ref: 'current',
    });
    assert.equal(await diff, true);
    assert.equal(api.currentFileStateForTest(path).diffLoaded, true);

    api.setOpenFileStateForTest(path, {...api.currentFileStateForTest(path), content: 'saved\n', dirty: true});
    const save = api.saveFileEditorForTest(path, null);
    await flushAsyncWork();
    assert.equal(api.currentFileStateForTest(path).dirty, true, 'a write receipt cannot mark the buffer clean');
    settle(receipts.at(-1), {path, size: 6, mtime: 2, mtime_ns: 2, realpath: path, file_id: 'dev:1:ino:2'});
    assert.equal(await save, true);
    assert.equal(api.currentFileStateForTest(path).dirty, false, 'the exact write terminal marks the buffer clean');
    assert.equal(receipts.length, 4, 'base open paints first, then deferred Git, diff, and save issue their own requests');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
    assert.equal(api.jsDebugFailureEventsForTest('rejection').length, 0);
  });

  test('push-owned operation kinds create no promise waiters or rejection diagnostics', () => {
    for (const kind of ['fs_batch', 'session_files']) {
      const api = loadYolomux('', ['1']);
      api.clearJsDebugEventsForTest();
      api.registerApiOperationReceiptForTest({
        request: {id: `r-${kind}`},
        operation: {id: `op-${kind}`, kind, context: {}, cursor: {epoch: `epoch-${kind}`, seq: 0}},
      });
      api.applyApiOperationTerminalForTest({
        operation: {id: `op-${kind}`, cursor: {epoch: `epoch-${kind}`, seq: 1}},
        result: {state: 'ready', request: {id: `r-${kind}`}, data: {}},
        status: 200,
      });
      assert.equal(api.apiOperationStateForTest().pending, 0);
      assert.equal(api.apiOperationStateForTest().waiters, 0);
      assert.equal(api.jsDebugFailureEventsForTest('rejection').length, 0);
    }
  });

  await testAsync('background-owner request record rejects stale HTTP completions and lets pushes win', async () => {
    const pending = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/background/status');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const first = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    const second = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    assert.equal(pending.length, 1, 'a forced refresh joins the in-flight snapshot instead of starting a replacement generation');
    pending[0].resolve(jsonResponse({marker: 'new-http'}));
    await first;
    assert.equal(api.backgroundOwnerStatusStateForTest().payload.marker, 'new-http', 'older HTTP completion cannot replace the newer generation');
    assert.equal(api.backgroundOwnerStatusStateForTest().request, null, 'only the current request clears the record handle');

    const third = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    api.applyBackgroundOwnerStatusPayloadForTest({marker: 'push'}, {render: false});
    pending[1].resolve(jsonResponse({marker: 'stale-after-push'}));
    await third;
    assert.equal(api.backgroundOwnerStatusStateForTest().payload.marker, 'push', 'an SSE payload invalidates the older HTTP generation');

    const fourth = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    pending[2].reject(new Error('owner offline'));
    await fourth;
    assert.ok(api.backgroundOwnerStatusStateForTest().error, 'the current request records its failure');
    assert.equal(api.backgroundOwnerStatusStateForTest().request, null, 'current failure releases the request handle');
  });

  await testAsync('background-owner refresh keeps one Promise return contract on fresh fast paths', async () => {
    const api = loadYolomux();
    api.setBackgroundOwnerStatusPayloadForTest({marker: 'fresh'});
    const refresh = api.refreshBackgroundOwnerStatusForTest({preferFresh: true, render: false});
    assert.equal(typeof refresh?.then, 'function', 'ready-channel repair may always attach catch to a background-owner refresh');
    assert.equal(await refresh, true, 'the fresh fast path preserves its fulfilled result');
  });

  test('panel-body reconciliation restores named anchors before one afterReplace hook', () => {
    const api = loadYolomux();
    const order = [];
    const oldScroller = {scrollTop: 31, scrollLeft: 7};
    const newScroller = {scrollTop: 0, scrollLeft: 0};
    const body = {
      innerHTML: '',
      querySelector(selector) {
        if (selector !== '.scroll') return null;
        return this.innerHTML ? newScroller : oldScroller;
      },
    };
    const anchor = api.elementScrollAnchor('.scroll');
    const restored = anchor.restore;
    anchor.restore = (root, value) => {
      order.push('restore');
      restored(root, value);
    };
    assert.equal(api.reconcilePanelBody({
      body,
      html: '<div class="scroll"></div>',
      anchors: [anchor],
      afterReplace() { order.push('after'); },
    }), true);
    assert.deepEqual(order, ['restore', 'after']);
    assert.equal(body.innerHTML, '<div class="scroll"></div>');
    assert.equal(newScroller.scrollTop, 31);
    assert.equal(newScroller.scrollLeft, 7);
  });

  test('search-index lifecycle generations reject a delayed older status response', () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    assert.equal(api.applyFileIndexStatusPayloadForTest('/repo', {state: 'building', generation: 4}), true);
    assert.equal(api.applyFileIndexStatusPayloadForTest('/repo', {state: 'ready', generation: 3}), false, 'an older HTTP status cannot overwrite the newer lifecycle transition');
    assert.equal(api.applyFileIndexStatusPayloadForTest('/repo', {state: 'error', generation: 4, error: 'walk failed'}), true, 'the matching lifecycle generation exposes its terminal error');
    assert.equal(api.fileIndexStatusFromPayloadForTest({state: 'error', error: 'walk failed'}), 'error');
  });

  // M11: the live 7771 incident. A follower served a persisted snapshot whose `indexd` producer was
  // dead; `ready_elsewhere` correctly went false, and the badge silently degraded to "building" -
  // honest about readiness, silent about staleness. One derivation now answers both questions.
  test('a snapshot whose producer is dead reads as stale, never as building or ready', () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    const orphaned = {
      state: 'follower',
      ready: false,
      ready_elsewhere: false,
      freshness: 'orphaned',
      freshness_reason: 'producer_not_running',
      producer_state: 'not_running',
      snapshot_age_seconds: 10800,
      stale: true,
      refresh_requested: false,
      refreshing_elsewhere: false,
    };
    assert.equal(api.fileIndexStatusFromPayloadForTest(orphaned), 'stale', 'a dead producer is stale, not building');
    const derived = api.fileIndexFreshnessFromPayloadForTest(orphaned);
    assert.equal(derived.stale, true, 'the one derivation reads the backend stale verdict');
    assert.equal(derived.ageSeconds, 10800, 'the snapshot age survives the derivation for the age sentence');
    assert.equal(
      api.fileIndexFreshnessMessageForTest(derived),
      'These results are from 3 hours ago and may be out of date. The file indexer is not running, so newer files are missing.',
      'the message states the age in words and names the dead producer',
    );

    assert.equal(api.applyFileIndexStatusPayloadForTest('/repo', {...orphaned, generation: 11}), true);
    assert.equal(api.fileExplorerIndexStatusForTest('/repo'), 'stale', 'the one status map records stale for the Finder badge');
    assert.equal(api.fileExplorerIndexBadgeText('/repo'), 'stale', 'the existing badge renderer says stale in words, not by colour');
    assert.equal(api.fileExplorerIndexBadgeTitleForTest('/repo'), 'Index snapshot is out of date — newer files may be missing', 'the badge title explains the consequence');

    const vouched = {
      state: 'follower',
      ready: false,
      ready_elsewhere: true,
      freshness: 'fresh',
      freshness_reason: '',
      producer_state: 'running',
      snapshot_age_seconds: 12,
      stale: false,
      refresh_requested: false,
      refreshing_elsewhere: false,
    };
    assert.equal(api.fileIndexStatusFromPayloadForTest(vouched), 'ready', 'a vouched snapshot is still plainly ready');
    assert.equal(api.fileIndexFreshnessMessageForTest(api.fileIndexFreshnessFromPayloadForTest(vouched)), '', 'a vouched snapshot says nothing at all');
    assert.equal(api.fileIndexStatusFromPayloadForTest({state: 'building'}), 'building', 'a genuinely warming index keeps its building state');
    assert.equal(
      api.fileIndexStatusFromPayloadForTest({...orphaned, too_large: true}),
      'too_large',
      'partial coverage still wins, so the file-limit warning is not lost behind staleness',
    );
  });

  // The user-visible half of the same incident: Quick Open kept answering from that snapshot and said
  // nothing. Rows stay - stale beats nothing - but the palette now says how old they are and why.
  test('quick open labels a stale snapshot inline, with its age, and keeps the rows usable', () => {
    const staleApi = loadYolomux('', ['1']);
    staleApi.setFileExplorerIndexedDirsForTest(['/home/test/dynamo']);
    staleApi.installCommandPaletteFixtureForTest();
    staleApi.setCommandPaletteQueryForTest('2026.md');
    const stale = staleApi.fileQuickOpenSearchPayloadResultForTest({
      root: '/home/test/dynamo',
      files: [{
        name: '2026.md',
        path: '/home/test/dynamo/notes/t5t/2026.md',
        relative_path: 'notes/t5t/2026.md',
        kind: 'file',
      }],
      index_state: 'follower-stale',
      index_coverage: 'unverified',
      freshness: 'orphaned',
      freshness_reason: 'producer_not_running',
      producer_state: 'not_running',
      snapshot_age_seconds: 10800,
      stale: true,
      refresh_requested: false,
      refreshing_elsewhere: false,
    }, '/home/test/dynamo');
    assert.equal(stale.indexWarming, false, 'a stale snapshot is a completed answer, not a warming one');
    assert.equal(stale.freshness.stale, true, 'quick open reads the same derivation as the Finder index badge');
    staleApi.setFileQuickOpenCandidatesForTest(stale.root, stale.files);
    staleApi.setFileQuickOpenFreshnessForTest(staleApi.fileQuickOpenWorstFreshnessForTest([stale.freshness]));
    const staleText = staleApi.commandPaletteFreshnessTextForTest();
    assert.equal(
      staleText,
      'These results are from 3 hours ago and may be out of date. The file indexer is not running, so newer files are missing.',
      'the palette states the age in plain words and names the reason',
    );
    assert.ok(/3 hours ago/.test(staleText), 'the age is words a non-engineer reads, never a raw snapshot_age_seconds');
    const staleHtml = staleApi.commandPaletteStatusHtmlForTest();
    assert.ok(staleHtml.includes('command-palette-freshness'), 'the sentence renders inline in the palette status row');
    assert.ok(staleHtml.includes('3 hours ago'), 'the rendered row carries the age, not a bare colour change');
    assert.ok(staleApi.commandPaletteStatusTextForTest().includes('3 hours ago'), 'the aria-label carries the same sentence for a screen reader');
    assert.ok(
      staleApi.fileQuickOpenItems().some(item => item.path === '/home/test/dynamo/notes/t5t/2026.md'),
      'stale rows stay visible and openable - stale beats nothing',
    );

    // Reachable by a screen reader, and never by colour alone: the aria-live status row is visible,
    // labelled with the same sentence, and marked by a class rather than a bare colour swap.
    staleApi.renderCommandPaletteResultsForTest();
    const staleStatus = staleApi.commandPaletteStateForTest().node.querySelector('.command-palette-status');
    assert.equal(staleStatus.hidden, false, 'the stale sentence is actually shown, not computed and dropped');
    assert.equal(
      staleStatus.getAttribute('aria-label'),
      'These results are from 3 hours ago and may be out of date. The file indexer is not running, so newer files are missing.',
      'the live region announces the whole sentence',
    );
    assert.ok(staleStatus.classList.names.has('stale'), 'the stale marker is a class, so the warning is not colour-only');
    assert.ok(staleApi.commandPaletteStateForTest().node.querySelector('.command-palette-results').innerHTML.includes('2026.md'), 'the stale rows are still rendered beneath the warning');

    const freshApi = loadYolomux('', ['1']);
    freshApi.setFileExplorerIndexedDirsForTest(['/home/test/dynamo']);
    freshApi.installCommandPaletteFixtureForTest();
    freshApi.setCommandPaletteQueryForTest('2026.md');
    const fresh = freshApi.fileQuickOpenSearchPayloadResultForTest({
      root: '/home/test/dynamo',
      files: [{name: '2026.md', path: '/home/test/dynamo/notes/t5t/2026.md', relative_path: 'notes/t5t/2026.md', kind: 'file'}],
      index_state: 'follower-ready',
      index_coverage: 'full',
      freshness: 'fresh',
      freshness_reason: '',
      producer_state: 'running',
      snapshot_age_seconds: 9,
      stale: false,
      refresh_requested: false,
      refreshing_elsewhere: false,
    }, '/home/test/dynamo');
    freshApi.setFileQuickOpenCandidatesForTest(fresh.root, fresh.files);
    freshApi.setFileQuickOpenFreshnessForTest(freshApi.fileQuickOpenWorstFreshnessForTest([fresh.freshness]));
    assert.equal(freshApi.commandPaletteFreshnessTextForTest(), '', 'a vouched snapshot adds no warning at all');
    assert.equal(freshApi.commandPaletteStatusHtmlForTest(), '', 'a vouched snapshot leaves the palette status row hidden');

    // Empty AND stale is a false negative, not a completed "No matches".
    const emptyApi = loadYolomux('', ['1']);
    emptyApi.installCommandPaletteFixtureForTest();
    emptyApi.setCommandPaletteQueryForTest('2026.md');
    emptyApi.setFileQuickOpenCandidatesForTest('/home/test/dynamo', []);
    emptyApi.setFileQuickOpenFreshnessForTest(stale.freshness);
    assert.equal(
      emptyApi.commandPaletteEmptyTextForTest(),
      'These results are from 3 hours ago and may be out of date. The file indexer is not running, so newer files are missing.',
      'an empty stale search explains itself instead of claiming No matches',
    );

    // Several roots answer one blended list, so the worst freshness owns the sentence.
    const unrecorded = {state: 'orphaned', reason: 'producer_epoch_unrecorded', producerState: 'unrecorded', ageSeconds: 90000, stale: true, refreshingElsewhere: false};
    const behind = {state: 'stale', reason: 'producer_vouch_expired', producerState: 'running', ageSeconds: 600, stale: true, refreshingElsewhere: true};
    assert.equal(staleApi.fileQuickOpenWorstFreshnessForTest([fresh.freshness, behind, unrecorded]), unrecorded, 'an orphaned root outranks a merely lagging one');
    assert.equal(staleApi.fileQuickOpenWorstFreshnessForTest([fresh.freshness]), null, 'all-vouched roots report no freshness problem');
    assert.equal(
      staleApi.fileIndexFreshnessMessageForTest(unrecorded),
      'These results are from 1 day ago and may be out of date. No file indexer has claimed this folder, so newer files are missing.',
      'an unrecorded producer is named as its own reason',
    );
    assert.equal(
      staleApi.fileIndexFreshnessMessageForTest(behind),
      'These results are from 10 minutes ago and may be out of date. The file indexer has not checked in recently, so newer files may be missing. A refresh is running now.',
      'a lagging producer with an accepted refresh says both',
    );
    assert.equal(
      staleApi.fileIndexFreshnessMessageForTest({...unrecorded, ageSeconds: null}),
      'These results come from an older snapshot and may be out of date. No file indexer has claimed this folder, so newer files are missing.',
      'an unknown age degrades to words, never to a blank or a raw number',
    );
  });

  test('search-index completion pushes its lifecycle snapshot without rereading the root', () => {
    const api = loadYolomux();
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({state: 'ready'}));
    });
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    api.setDocumentVisibilityForTest('hidden');
    assert.equal(api.handleClientPushEventForTest('background_refresh_done', {role: 'search-index', root: '/repo', state: 'ready', generation: 7}, {epoch: 'index-owner-b', resource: 'background:search-index:repo', resource_revision: 7}), true);
    assert.deepStrictEqual(requests, [], 'a complete index lifecycle payload is readable state, not a reason to re-poll its root');
    assert.equal(api.applyFileIndexStatusPayloadForTest('/repo', {state: 'building', generation: 6}), false, 'the event-owned generation fences a stale later HTTP response');
  });

  // Live 7771: /home/keivenc/dev held 22k+ rows while a progressive BFS kept crawling. The index-status
  // payload still carried a SUPERSEDED generation's `too_large`/`coverage:partial` verdict, so the badge
  // read "file limit reached" and the Finder banner claimed "Indexed 0 files" — both false. The live
  // truth is `progressive_coverage`: a crawl that is not truncated and not yet full is still BUILDING.
  test('a progressive BFS crawl reads as building, not the terminal file-limit state, and clears a superseded warning', () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    api.clearFileIndexPartialWarningsForTest();

    const building = {
      ready: true,
      too_large: true,            // a superseded persisted snapshot was capped ...
      state: 'too_large',
      coverage: 'partial',
      count: 100000,
      max_files: 100000,
      generation: 6,
      progressive_coverage: {truncated: false, full_coverage: false, published_generation: 6, entry_count: 54945},
    };
    assert.equal(api.fileIndexStatusFromPayloadForTest(building), 'building',
      'the live crawl is not truncated, so the root is building rather than the terminal file-limit state');
    assert.equal(
      api.fileIndexStatusFromPayloadForTest({...building, progressive_coverage: {truncated: true, full_coverage: false, published_generation: 7, entry_count: 100000}}),
      'too_large',
      'a genuinely truncated crawl still maps to the file-limit state');
    assert.equal(
      api.fileIndexStatusFromPayloadForTest({ready: true, state: 'ready', progressive_coverage: {truncated: false, full_coverage: true, published_generation: 8, entry_count: 100000}}),
      'ready',
      'a completed full-coverage crawl reads ready');

    // A superseded "file limit reached" warning must clear the moment the live crawl is building again,
    // so its stale count cannot linger on screen.
    api.seedFileIndexPartialWarningRootForTest('/repo');
    assert.equal(api.fileIndexPartialWarningRootWarnedForTest('/repo'), true, 'the root starts with a prior capped-index warning');
    assert.equal(api.applyFileIndexStatusPayloadForTest('/repo', {...building, generation: 11}), true);
    assert.equal(api.fileExplorerIndexStatusForTest('/repo'), 'building', 'the live crawl re-derives as building');
    assert.equal(api.fileIndexPartialWarningRootWarnedForTest('/repo'), false,
      'a superseded capped-index warning is cleared once the crawl is building again');
  });

  // Live 7771: a fully-qualified name typed while /home/keivenc/dev was still warming settled on the
  // empty snapshot and never refreshed, even though the file was later indexed. The palette must
  // re-issue the CURRENT typed query when the index advances, with no retype.
  await testAsync('a search_progress signal streams the crawl\'s newly-published matches by cursor, not a repeated full query', async () => {
    const api = loadYolomux();
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    const searches = [];
    api.setFetchForTest(url => {
      const target = String(url);
      if (target.includes('/api/fs/search') || target.includes('/api/batch/search')) {
        searches.push(target);
        if (target.includes('cursor=')) {
          // The delta read: one committed match published since the baseline cursor.
          return Promise.resolve(jsonResponse({
            root: '/repo', root_realpath: '/repo', query: 't5t.md', limit: 500, more: false,
            cursor: 'CUR1',
            changes: [{operation: 'upsert', path: '/repo/notes/t5t/t5t.md', name: 't5t.md', relative_path: 'notes/t5t/t5t.md', realpath: '/repo/notes/t5t/t5t.md'}],
            coverage: {published_depth: 2, frontier_depth: 3, frontier_size: 4, entry_count: 12000, full_coverage: false, truncated: false},
          }));
        }
        // The warming snapshot returns nothing yet, but hands back a baseline cursor to stream from.
        return Promise.resolve(jsonResponse({
          root: '/repo', root_realpath: '/repo', query: 't5t.md', index_state: 'warming', index_coverage: 'pending',
          files: [], initial_cursor: 'CUR0',
        }));
      }
      return Promise.resolve(jsonResponse({state: 'building'}));
    });

    // Palette open in files mode; the warming snapshot seeds one baseline cursor for /repo.
    api.installCommandPaletteFixtureForTest();
    api.setCommandPaletteStateForTest('files', 't5t.md');
    api.setCommandPaletteQueryForTest('t5t.md');
    await api.refreshFileQuickOpenCandidatesForTest('t5t.md');
    const seeded = api.fileQuickOpenDeltaRootsForTest();
    assert.equal(seeded.length, 1, 'the warming snapshot seeds exactly one delta cursor');
    assert.equal(seeded[0].cursor, 'CUR0', 'the baseline cursor from initial_cursor is stored per root');
    const snapshotSearches = searches.filter(target => !target.includes('cursor=')).length;

    // The crawl publishes a directory: a path-free search_progress signal names this root's scope.
    api.handleClientPushEventNowForTest('search_progress', {
      scope_id: api.fileSearchScopeIdForTest('/repo'),
      generation: 5, revision: 7,
      coverage: {published_depth: 2, frontier_depth: 3, frontier_size: 4, entry_count: 12000, full_coverage: false, truncated: false},
    });
    await flushAsyncWork();

    const deltaSearches = searches.filter(target => target.includes('cursor=CUR0'));
    assert.equal(deltaSearches.length, 1, 'the signal issues exactly one bounded delta read against the baseline cursor');
    assert.equal(searches.filter(target => !target.includes('cursor=')).length, snapshotSearches,
      'the signal issues a delta, NOT a repeated full snapshot search');
    assert.ok(api.fileQuickOpenStateForTest().candidates.some(candidate => candidate.path === '/repo/notes/t5t/t5t.md'),
      'the freshly-published file streams into the palette candidates without a retype');
    assert.equal(api.fileQuickOpenDeltaRootsForTest()[0].cursor, 'CUR1', 'the root advances to the returned cursor');
  });

  // Seed one delta cursor for /repo through a real snapshot fetch, returning the recorded requests and
  // a fetch-response controller so each streaming test drives its own delta/rebase/pagination replies.
  async function seedQuickOpenDeltaCursor(api, {snapshotFiles = [], initialCursor = 'CUR0', deltaReply} = {}) {
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    const searches = [];
    api.setFetchForTest(url => {
      const target = String(url);
      if (target.includes('/api/fs/search') || target.includes('/api/batch/search')) {
        searches.push(target);
        if (target.includes('cursor=')) return Promise.resolve(jsonResponse(deltaReply(target)));
        return Promise.resolve(jsonResponse({
          root: '/repo', root_realpath: '/repo', query: 't5t', index_state: 'warming',
          files: snapshotFiles, initial_cursor: initialCursor,
        }));
      }
      return Promise.resolve(jsonResponse({state: 'building'}));
    });
    api.installCommandPaletteFixtureForTest();
    api.setFileQuickOpenCandidatesForTest('/repo', []);
    api.setCommandPaletteStateForTest('files', 't5t');
    api.setCommandPaletteQueryForTest('t5t');
    await api.refreshFileQuickOpenCandidatesForTest('t5t');
    return {searches, scopeId: api.fileSearchScopeIdForTest('/repo')};
  }

  await testAsync('an out-of-order delta for a superseded request cannot mutate a newer palette', async () => {
    const api = loadYolomux();
    const {searches} = await seedQuickOpenDeltaCursor(api, {deltaReply: () => ({changes: [], more: false, cursor: 'CUR1'})});
    const staleRequestId = api.fileQuickOpenDeltaRootsForTest()[0].requestId;
    // A newer search supersedes the cursor set (a query change bumps requestId + reseeds).
    await api.refreshFileQuickOpenCandidatesForTest('t5t');
    searches.length = 0;
    const status = api.ingestFileQuickOpenDeltaForTest('/repo', staleRequestId, {
      changes: [{operation: 'upsert', path: '/repo/stale.md', name: 'stale.md', relative_path: 'stale.md', realpath: '/repo/stale.md'}],
      more: false, cursor: 'STALE',
    });
    assert.equal(status.stale, true, 'a delta for the retired requestId is rejected as stale');
    assert.equal(api.fileQuickOpenStateForTest().candidates.some(file => file.path === '/repo/stale.md'), false,
      'the stale response never mutates the current candidate set');
  });

  await testAsync('a superseded query keeps its rows visible while the longer query loads', async () => {
    // The reported shape: typing `t5t.md` showed `DOIT.p1.e5.backend-lifetime-supervision.md` at row 1
    // and no `t5t.md` at all. Measured against the real index, the backend answer for `t5t.md` puts
    // `notes/t5t/t5t.md` at position 1 and that DOIT file at position 77 -- so the row on screen was
    // never an answer to `t5t.md`. It was the answer to the shorter `t5t`, still being presented while
    // the newer, more specific search sat accepted-but-unanswered behind the operation queue.
    const api = loadYolomux('', ['1', '2', '3', '4', '5', '6'], 'http:', 'Linux x86_64', 'admin', {repoRoot: '/repo'});
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    const decoy = '/repo/queues/DOIT.p1.e5.backend-lifetime-supervision.md';
    let answerLongQuery = null;
    api.setFetchForTest(url => {
      const target = String(url);
      if (!target.includes('/api/fs/search')) return Promise.resolve(jsonResponse({state: 'building'}));
      // The longer query is accepted but never answered during this test: the queued 202 case.
      if (target.includes(encodeURIComponent('t5t.md'))) return new Promise(resolve => { answerLongQuery = resolve; });
      return Promise.resolve(jsonResponse({
        root: '/repo', root_realpath: '/repo', query: 't5t', limit: 500,
        files: [{path: decoy, name: 'DOIT.p1.e5.backend-lifetime-supervision.md',
                 relative_path: 'queues/DOIT.p1.e5.backend-lifetime-supervision.md', realpath: decoy}],
      }));
    });

    api.installCommandPaletteFixtureForTest();
    api.setFileQuickOpenCandidatesForTest('/repo', []);
    api.setCommandPaletteStateForTest('files', 't5t');
    api.setCommandPaletteQueryForTest('t5t');
    await api.refreshFileQuickOpenCandidatesForTest('t5t');
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), [decoy],
      'the shorter query legitimately matches the decoy');

    api.setCommandPaletteStateForTest('files', 't5t.md');
    api.setCommandPaletteQueryForTest('t5t.md');
    const inFlight = api.refreshFileQuickOpenCandidatesForTest('t5t.md');
    await flushAsyncWork();

    assert.ok(answerLongQuery, 'the longer query reached the backend and is still unanswered');
    assert.equal(api.fileQuickOpenStateForTest().loading, true, 'the palette reports that it is still searching');
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), [decoy],
      'rows belonging to the prior query remain visible while the newer query loads');
    const retainedHtml = api.commandPaletteResultsHtmlForTest();
    assert.ok(retainedHtml.includes('backend-life'), `the retained prior row remains rendered while the backend answers: ${retainedHtml}`);
    assert.equal(api.commandPaletteItems().find(item => item.path === decoy)?.disabled, true,
      'a retained prior row cannot be selected for the newer query');

    answerLongQuery(jsonResponse({
      root: '/repo', root_realpath: '/repo', query: 't5t.md', limit: 500,
      files: [{path: '/repo/notes/t5t/t5t.md', name: 't5t.md', relative_path: 'notes/t5t/t5t.md', realpath: '/repo/notes/t5t/t5t.md'}],
    }));
    await inFlight;
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), ['/repo/notes/t5t/t5t.md'],
      'the answer for the current query replaces the list once it lands');
  });

  await testAsync('a same-query refresh keeps its current rows visible until the new answer lands', async () => {
    const api = loadYolomux('', ['1', '2', '3', '4', '5', '6'], 'http:', 'Linux x86_64', 'admin', {repoRoot: '/repo'});
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    const retained = '/repo/notes/t5t/t5t.md';
    let searchCount = 0;
    let answerRefresh = null;
    api.setFetchForTest(url => {
      const target = String(url);
      if (!target.includes('/api/fs/search') && !target.includes('/api/batch/search')) return Promise.resolve(jsonResponse({state: 'building'}));
      searchCount += 1;
      if (searchCount > 1) return new Promise(resolve => { answerRefresh = resolve; });
      return Promise.resolve(jsonResponse({
        root: '/repo', root_realpath: '/repo', query: 't5t', limit: 500,
        files: [{path: retained, name: 't5t.md', relative_path: 'notes/t5t/t5t.md', realpath: retained}],
      }));
    });

    api.installCommandPaletteFixtureForTest();
    api.setFileQuickOpenCandidatesForTest('/repo', []);
    api.setCommandPaletteStateForTest('files', 't5t');
    api.setCommandPaletteQueryForTest('t5t');
    await api.refreshFileQuickOpenCandidatesForTest('t5t');

    const inFlight = api.refreshFileQuickOpenCandidatesForTest('  t5t  ');
    await flushAsyncWork();
    assert.ok(answerRefresh, 'the normalized same-query refresh reached the backend and remains pending');
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), [retained],
      'the current answer remains visible during a refresh of the same normalized query');

    answerRefresh(jsonResponse({root: '/repo', root_realpath: '/repo', query: 't5t', limit: 500, files: []}));
    await inFlight;
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates), [],
      'the refreshed same-query answer replaces the retained rows once it lands');
  });

  await testAsync('a path query clears prior rows while its directory changes', async () => {
    const api = loadYolomux();
    let listCount = 0;
    let answerSecondDirectory = null;
    api.setFetchForTest(url => {
      if (!String(url).includes('/api/fs/fast/list')) return Promise.resolve(jsonResponse({state: 'building'}));
      listCount += 1;
      if (listCount > 1) return new Promise(resolve => { answerSecondDirectory = resolve; });
      return Promise.resolve(jsonResponse({
        path: '/repo-a',
        entries: [{kind: 'file', name: 'foo.md'}],
      }));
    });

    api.installCommandPaletteFixtureForTest();
    api.setFileQuickOpenCandidatesForTest('/repo-a', []);
    api.setCommandPaletteStateForTest('files', '/repo-a/foo');
    api.setCommandPaletteQueryForTest('/repo-a/foo');
    await api.refreshFileQuickOpenCandidatesForTest('/repo-a/foo');
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), ['/repo-a/foo.md']);

    api.setCommandPaletteStateForTest('files', '/repo-b/foo');
    api.setCommandPaletteQueryForTest('/repo-b/foo');
    const inFlight = api.refreshFileQuickOpenCandidatesForTest('/repo-b/foo');
    await flushAsyncWork();
    assert.ok(answerSecondDirectory, 'the same-filter query for the second directory remains pending');
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path)), ['/repo-a/foo.md'],
      'rows from the prior directory remain visible while the new directory is loading');

    answerSecondDirectory(jsonResponse({path: '/repo-b', entries: []}));
    await inFlight;
  });

  await testAsync('a rebase_required verdict performs one full-snapshot repair, not a mixed merge', async () => {
    const api = loadYolomux();
    const {searches, scopeId} = await seedQuickOpenDeltaCursor(api, {
      deltaReply: () => ({root: '/repo', root_realpath: '/repo', rebase_required: true, reason: 'generation_superseded'}),
    });
    const snapshotsBefore = searches.filter(target => !target.includes('cursor=')).length;
    api.handleClientPushEventNowForTest('search_progress', {scope_id: scopeId, generation: 6, revision: 9, coverage: {}});
    await flushAsyncWork();
    assert.equal(searches.filter(target => target.includes('cursor=')).length, 1, 'the stale cursor is read exactly once before rebasing');
    assert.ok(searches.filter(target => !target.includes('cursor=')).length > snapshotsBefore,
      'rebase_required repairs by re-issuing one full snapshot search');
  });

  await testAsync('a signal drains a paginated backlog bounded, advancing the cursor once settled', async () => {
    const api = loadYolomux();
    const pages = [
      {changes: [{operation: 'upsert', path: '/repo/one.md', name: 'one.md', relative_path: 'one.md', realpath: '/repo/one.md'}], more: true, cursor: 'CUR1'},
      {changes: [{operation: 'upsert', path: '/repo/two.md', name: 'two.md', relative_path: 'two.md', realpath: '/repo/two.md'}], more: false, cursor: 'CUR2'},
    ];
    let page = 0;
    const {searches, scopeId} = await seedQuickOpenDeltaCursor(api, {deltaReply: () => pages[Math.min(page++, pages.length - 1)]});
    api.handleClientPushEventNowForTest('search_progress', {scope_id: scopeId, generation: 5, revision: 7, coverage: {}});
    await flushAsyncWork();
    assert.equal(searches.filter(target => target.includes('cursor=')).length, 2, 'more=true continues paging until the server says done');
    assert.deepStrictEqual(
      canonical(api.fileQuickOpenStateForTest().candidates.map(file => file.path).sort()),
      ['/repo/one.md', '/repo/two.md'], 'both paged deltas merge into the candidate set');
    assert.equal(api.fileQuickOpenDeltaRootsForTest()[0].cursor, 'CUR2', 'the cursor advances to the final page');
    assert.equal(api.fileQuickOpenDeltaRootsForTest()[0].fetching, false, 'the in-flight guard is released once the backlog drains');
  });

  await testAsync('a search_progress signal after close or for another root pumps nothing', async () => {
    const api = loadYolomux();
    const {searches, scopeId} = await seedQuickOpenDeltaCursor(api, {deltaReply: () => ({changes: [], more: false, cursor: 'CUR1'})});
    searches.length = 0;
    // A non-matching scope digest correlates to no open root.
    api.handleClientPushEventNowForTest('search_progress', {scope_id: api.fileSearchScopeIdForTest('/somewhere/else'), generation: 5, revision: 7, coverage: {}});
    await flushAsyncWork();
    assert.equal(searches.length, 0, 'a signal for another root issues no delta read');
    // After close, the matching scope must not stream against a dead palette.
    api.closeCommandPaletteForTest();
    api.handleClientPushEventNowForTest('search_progress', {scope_id: scopeId, generation: 5, revision: 7, coverage: {}});
    await flushAsyncWork();
    assert.equal(searches.length, 0, 'a signal after the palette closes pumps nothing');
  });

  await testAsync('a root with no committed baseline repairs from a full snapshot on its first signal', async () => {
    const api = loadYolomux();
    // initial_cursor null: nothing committed yet, so a signal cannot delta-read and must repair.
    const {searches, scopeId} = await seedQuickOpenDeltaCursor(api, {initialCursor: null, deltaReply: () => ({changes: [], more: false, cursor: 'CUR1'})});
    assert.equal(api.fileQuickOpenDeltaRootsForTest()[0].cursor, null, 'a warming root with nothing committed stores a null baseline');
    const snapshotsBefore = searches.filter(target => !target.includes('cursor=')).length;
    api.handleClientPushEventNowForTest('search_progress', {scope_id: scopeId, generation: 5, revision: 7, coverage: {}});
    await flushAsyncWork();
    assert.equal(searches.filter(target => target.includes('cursor=')).length, 0, 'a null-cursor root never issues a delta read');
    assert.ok(searches.filter(target => !target.includes('cursor=')).length > snapshotsBefore, 'it repairs from one full snapshot to pick up pre-cursor rows');
  });

  await testAsync('background-owner takeover revalidates visible indexed roots once', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({state: 'ready', generation: 8}));
    });
    api.setFileExplorerIndexedDirsForTest(['/repo']);
    api.installCommandPaletteFixtureForTest();
    api.setCommandPaletteStateForTest('files', '');
    assert.equal(api.handleClientPushEventForTest('background_owner_changed', {marker: 'takeover'}, {epoch: 'owner-b', resource: 'background-owner', resource_revision: 2}), true);
    await flushAsyncWork();
    assert.deepStrictEqual(requests, ['/api/fs/index-status?root=%2Frepo'], 'a takeover revalidates each visible indexed root exactly once');
    assert.equal(api.fileExplorerIndexStatusForTest('/repo'), 'ready', 'legacy index lifecycle state remains data instead of being mistaken for a canonical API envelope');

    requests.length = 0;
    api.setDocumentVisibilityForTest('hidden');
    assert.equal(api.handleClientPushEventForTest('background_owner_changed', {marker: 'hidden-takeover'}, {epoch: 'owner-b', resource: 'background-owner', resource_revision: 3}), true);
    await flushAsyncWork();
    assert.deepStrictEqual(requests, [], 'a hidden Finder does not pay for a takeover revalidation');
  });

  await testAsync('auto-approve startup snapshots share one in-flight request', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/auto-approve');
      return new Promise(resolve => pending.push(resolve));
    });
    const boot = api.loadAutoStatusesForTest({render: false});
    const ready = api.loadAutoStatusesForTest({render: false});
    assert.equal(boot, ready, 'boot and SSE ready share one auto-approve snapshot owner');
    assert.equal(pending.length, 1, 'duplicate startup consumers issue exactly one auto-approve request');
    pending[0](jsonResponse({sessions: {}}));
    await boot;

    await api.loadAutoStatusesForTest({preferFresh: true, render: false});
    assert.equal(pending.length, 1, 'a ready event immediately after boot reuses the fresh auto-approve snapshot');
  });

  await testAsync('auto-approve accepted snapshot waits for its terminal without per-session recovery fetches', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({
        state: 'queued',
        operation: {
          id: 'op-auto-snapshot',
          kind: 'auto_approve_snapshot',
          context: {},
          cursor: {epoch: 'auto-snapshot', seq: 0},
        },
      }, 202));
    });
    const loading = api.loadAutoStatusesForTest({force: true, render: false});
    await flushAsyncWork();
    assert.deepStrictEqual(requests, ['/api/auto-approve']);
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: 'op-auto-snapshot', cursor: {epoch: 'auto-snapshot', seq: 1}},
      result: {state: 'ready', data: {session_order: ['1'], sessions: {'1': {target: '1', enabled: false, marker: 'terminal'}}}},
      status: 200,
    }), true);
    await loading;
    assert.equal(api.autoApproveStateForTest('1')?.marker, 'terminal');
    assert.deepStrictEqual(requests, ['/api/auto-approve'], 'accepted first delivery never falls back to a second per-session request');
  });

  await testAsync('Tabber snapshot owner coalesces repeated rendering consumers', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFileExplorerModeForTest('tabber');
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/activity?'));
      return new Promise(resolve => pending.push(resolve));
    });
    const first = api.fetchTabberActivityForTest({visible: true});
    const second = api.fetchTabberActivityForTest({visible: true});
    const third = api.fetchTabberActivityForTest({visible: true});
    assert.equal(pending.length, 1, 'repeated Tabber renders start exactly one /api/activity request');
    pending[0](jsonResponse({activity: {}, agents: []}));
    await Promise.all([first, second, third]);
  });

  await testAsync('activity-summary request and push paths stay terminal-disabled', async () => {
    const requests = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({marker: 'unexpected', sessions: {}, global: {lines: []}}));
    });

    assert.equal(api.activitySummaryEnabledForTest(), false);
    assert.equal(await api.refreshActivitySummaryForTest({force: true, localeChange: true}), false);
    assert.equal(await api.refreshActivitySummaryForTest({force: true, silent: true}), false);
    assert.equal(api.applyActivitySummaryPayloadFromPushForTest({marker: 'push', sessions: {}, global: {lines: []}}), false);
    assert.deepStrictEqual(requests, [], 'disabled request and push paths perform no HTTP work');
    assert.deepStrictEqual(canonical(api.activitySummaryStateForTest()), {
      payload: {sessions: {}, global: {lines: []}, session_order: [], status: 'feature_disabled', reason: 'async_replacement_required'},
      refreshing: false,
    });
    assert.equal(api.jsDebugEventsForTest().some(event => event.category === 'graph_activity'), false, 'the disabled path emits no synthetic request failure');
  });

  await testAsync('Finder filesystem resource records reject stale fresh and invalidated completions', async () => {
    for (const resource of [
      {
        type: 'list',
        path: '/home/test/list',
        fetch: (api, path, options) => api.fetchDirectoryForTest(path, options),
        payload: marker => ({entries: [{name: `${marker}.txt`, kind: 'file'}]}),
        marker: value => value[0].name.replace('.txt', ''),
      },
      {
        type: 'info',
        path: '/home/test/info',
        fetch: (api, path, options) => api.fetchFilePathInfoForTest(path, options),
        payload: marker => ({marker, path: '/home/test/info', kind: 'dir'}),
        marker: value => value.marker,
      },
    ]) {
      const api = loadYolomux();
      const batches = [];
      api.setFetchForTest((url, options = {}) => {
        const directList = String(url).startsWith('/api/fs/fast/list?');
        if (!directList) assert.equal(String(url), '/api/fs/batch');
        const requests = directList ? [{id: 0}] : (JSON.parse(options.body || '{}').requests || []);
        const batch = {...deferredFetch(), requests};
        batch.directList = directList;
        batches.push(batch);
        return batch.promise;
      });
      const resolveBatch = (index, marker, ok = true) => {
        const batch = batches[index];
        if (batch.directList) {
          batch.resolve(ok ? jsonResponse(resource.payload(marker)) : jsonResponse({error: marker}, 500));
          return;
        }
        batch.resolve(jsonResponse({
          responses: batch.requests.map(request => ok
            ? {id: request.id, ok: true, status: 200, payload: resource.payload(marker)}
            : {id: request.id, ok: false, status: 500, error: marker}),
        }));
      };

      const older = resource.fetch(api, resource.path, {fresh: true});
      const olderFlush = api.flushFileExplorerFsBatchForTest();
      assert.equal(batches.length, 1);
      const newer = resource.fetch(api, resource.path, {fresh: true});
      const newerFlush = api.flushFileExplorerFsBatchForTest();
      const coalescedList = resource.type === 'list';
      assert.equal(batches.length, coalescedList ? 1 : 2, `${resource.type}: direct LIST fan-out is coalesced while independent batched INFO reads retain stale-generation coverage`);
      if (coalescedList) {
        resolveBatch(0, 'new');
        await Promise.all([olderFlush, newerFlush]);
        assert.equal(resource.marker(await older), 'new');
        assert.equal(resource.marker(await newer), 'new');
      } else {
        resolveBatch(1, 'new');
        await newerFlush;
        assert.equal(resource.marker(await newer), 'new');
        resolveBatch(0, 'old');
        await olderFlush;
        assert.equal(resource.marker(await older), 'old', `${resource.type}: the original caller still receives its own response`);
      }
      assert.equal(resource.marker(await resource.fetch(api, resource.path, {})), 'new', `${resource.type}: the slower older response cannot overwrite the newer cache generation`);

      const invalidated = resource.fetch(api, resource.path, {fresh: true});
      const invalidatedFlush = api.flushFileExplorerFsBatchForTest();
      const invalidatedIndex = coalescedList ? 1 : 2;
      assert.equal(batches.length, invalidatedIndex + 1);
      api.invalidateFileExplorerFsCachesForTest();
      resolveBatch(invalidatedIndex, 'retired');
      await invalidatedFlush;
      assert.equal(resource.marker(await invalidated), 'retired');
      assert.equal(api.fileExplorerFsResourceRecordsForTest().length, 0, `${resource.type}: invalidated completion cannot recreate its retired resource record`);

      const current = resource.fetch(api, resource.path, {fresh: true});
      const currentFlush = api.flushFileExplorerFsBatchForTest();
      resolveBatch(invalidatedIndex + 1, 'current');
      await currentFlush;
      assert.equal(resource.marker(await current), 'current');
      const records = api.fileExplorerFsResourceRecordsForTest();
      assert.equal(records.length, 1);
      assert.equal(records[0].hasValue, true);
      assert.equal(records[0].requestActive, false);
      assert.equal(resource.marker(records[0].value), 'current', `${resource.type}: only the current generation publishes a reusable value`);
    }
  });

  await testAsync('Finder filesystem current failure releases the shared resource request', async () => {
    const api = loadYolomux();
    const batches = [];
    api.setFetchForTest((url, options = {}) => {
      const requests = JSON.parse(options.body || '{}').requests || [];
      const batch = {...deferredFetch(), requests};
      batches.push(batch);
      return batch.promise;
    });
    const failed = api.fetchFilePathInfoForTest('/home/test/failure', {fresh: true});
    const failedFlush = api.flushFileExplorerFsBatchForTest();
    batches[0].resolve(jsonResponse({responses: batches[0].requests.map(request => ({id: request.id, ok: false, status: 500, error: 'failed'}))}));
    await failedFlush;
    await assert.rejects(failed, /failed/);
    let records = api.fileExplorerFsResourceRecordsForTest();
    assert.equal(records[0].requestActive, false, 'current failure clears its request handle');
    assert.equal(records[0].hasValue, false, 'current failure is not cached as a value');

    const retry = api.fetchFilePathInfoForTest('/home/test/failure');
    const retryFlush = api.flushFileExplorerFsBatchForTest();
    assert.equal(batches.length, 2, 'failure cleanup allows an immediate replacement request');
    batches[1].resolve(jsonResponse({responses: batches[1].requests.map(request => ({id: request.id, ok: true, status: 200, payload: {marker: 'retry'}}))}));
    await retryFlush;
    assert.equal((await retry).marker, 'retry');
    records = api.fileExplorerFsResourceRecordsForTest();
    assert.equal(records[0].requestActive, false);
    assert.equal(records[0].value.marker, 'retry');
  });

  {
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal((source.match(/const fileExplorerFsResourceRecords = new Map\(\);/g) || []).length, 1, 'Finder list/info resource state has one record-map owner');
    for (const retired of ['fileExplorerDirListingCache', 'fileExplorerDirListingInflight', 'fileExplorerPathInfoCache', 'fileExplorerPathInfoInflight']) {
      assert.ok(!source.includes(retired), `${retired} retired after list/info state moved into one resource record`);
    }
  }

  await testAsync('bounded read pending is a typed retry instead of an invalid response contract', async () => {
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(() => Promise.resolve(jsonResponse({
      state: 'queued',
      request: {id: 'r-bounded-read-pending'},
      status: 'pending',
      retry_after_seconds: 1,
      reason: 'upstream service is refreshing',
      ok: true,
      terminal: false,
    }, 202)));

    await assert.rejects(
      api.apiFetchJsonForTest('/api/fixture'),
      error => api.isApiPendingResponseForTest(error)
        && error.operationId === ''
        && error.retryAfterSeconds === 1,
    );
  });

  await testAsync('ready response envelopes expose only canonical data to current clients', async () => {
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(() => Promise.resolve(jsonResponse({
      state: 'ready',
      request: {id: 'r-canonical-metadata'},
      data: {sessions: {'1': {panes: []}}, session_order: ['1']},
    })));

    assert.deepStrictEqual(
      canonical(await api.apiFetchJsonForTest('/api/session-metadata')),
      {sessions: {'1': {panes: []}}, session_order: ['1']},
      'the shared decoder does not require flattened legacy aliases beside data',
    );
  });

  await testAsync('client-event transport record owns connection, frame queue, and reconnect timer', async () => {
    const frames = [];
    const cancelledFrames = [];
    const timers = [];
    const clearedTimers = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        const id = 40 + frames.length + 1;
        frames.push({id, callback});
        return id;
      },
      cancelAnimationFrame(id) { cancelledFrames.push(id); },
      setTimeout(callback, delay) {
        const id = 80 + timers.length + 1;
        timers.push({id, callback, delay});
        return id;
      },
      clearTimeout(id) { clearedTimers.push(id); },
    });
    api.setFetchForTest(() => Promise.resolve(jsonResponse({sessions: {}, session_order: []})));

    api.queueClientPushEventForTest('noop', {session: '1', marker: 1});
    api.queueClientPushEventForTest('noop', {session: '1', marker: 2});
    assert.equal(api.clientEventTransportStateForTest().queued, 1, 'same-session events coalesce in the record queue');
    assert.equal(api.clientEventTransportStateForTest().frame, 41, 'the record owns the scheduled foreground frame');
    frames[0].callback();
    assert.equal(api.clientEventTransportStateForTest().queued, 0, 'the foreground frame consumes the record queue');
    assert.equal(api.clientEventTransportStateForTest().frame, 0, 'the consumed frame clears its record field');

    api.queueClientPushEventForTest('noop-a', {session: '1'});
    api.setDocumentVisibilityForTest('hidden');
    api.queueClientPushEventForTest('noop-b', {session: '1'});
    assert.deepStrictEqual(cancelledFrames, [42], 'hidden delivery cancels the pending foreground frame');
    assert.equal(api.clientEventTransportStateForTest().queued, 0, 'hidden delivery flushes immediately');
    assert.equal(api.clientEventTransportStateForTest().frame, 0, 'hidden delivery clears the frame field');

    api.setDocumentVisibilityForTest('visible');
    api.queueClientPushEventForTest('noop-c', {session: '1'});
    const replacementFrame = api.clientEventTransportStateForTest().frame;
    frames[1].callback();
    assert.equal(api.clientEventTransportStateForTest().frame, replacementFrame, 'a cancelled frame callback cannot clear its replacement frame');
    assert.equal(api.clientEventTransportStateForTest().queued, 1, 'a cancelled frame callback cannot consume replacement-generation events');
    frames[2].callback();
    assert.equal(api.clientEventTransportStateForTest().queued, 0);
    assert.equal(api.clientEventTransportStateForTest().frame, 0);

    api.installClientEventStreamForTest();
    const source = api.clientEventTransportStateForTest().source;
    assert.equal(api.installClientEventStreamForTest(), false, 'starting an already-serving transport does not create a second stream');
    source.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    assert.equal(api.clientEventTransportStateForTest().connected, true, 'ready marks the record connected');
    source.onerror();
    assert.equal(api.clientEventTransportStateForTest().connected, false, 'error marks the same record disconnected');
    source.listeners.get('ping')[0]({data: '{}', type: 'ping', lastEventId: ''});
    assert.equal(api.clientEventTransportStateForTest().connected, true, 'later traffic marks the record connected again');

    api.syncClientEventDemandForTest();
    const firstDemandTimerId = api.clientEventTransportStateForTest().demandTimer;
    api.syncClientEventDemandForTest();
    const secondDemandTimerId = api.clientEventTransportStateForTest().demandTimer;
    timers.find(timer => timer.id === firstDemandTimerId).callback();
    assert.equal(api.clientEventTransportStateForTest().demandTimer, secondDemandTimerId, 'a replaced demand debounce cannot clear its replacement');
    timers.find(timer => timer.id === secondDemandTimerId).callback();
    assert.equal(api.clientEventTransportStateForTest().demandTimer, null, 'the current demand debounce consumes the null sentinel exactly once');

    const reconnectTimerStart = timers.length;
    const clearedBeforeReconnect = clearedTimers.length;
    api.scheduleReconnectResyncForTest('visible');
    api.scheduleReconnectResyncForTest('online');
    const firstReconnectTimer = timers[reconnectTimerStart];
    const secondReconnectTimer = timers[reconnectTimerStart + 1];
    assert.deepStrictEqual(clearedTimers.slice(clearedBeforeReconnect), [firstReconnectTimer.id], 'replacement reconnect debounce clears the prior record timer');
    assert.equal(api.clientEventTransportStateForTest().resyncTimer, secondReconnectTimer.id, 'the record owns the replacement reconnect timer');
    firstReconnectTimer.callback();
    assert.equal(api.clientEventTransportStateForTest().resyncTimer, secondReconnectTimer.id, 'a replaced reconnect callback cannot clear or run over the current timer');
    secondReconnectTimer.callback();
    assert.equal(api.clientEventTransportStateForTest().resyncTimer, null, 'firing consumes the reconnect timer');
    await flushAsyncWork();
  });

  test('client-event demanded transport owns one exact grace episode through constructor recovery and removal', () => {
    let now = 0;
    let nextTimer = 1;
    const timers = new Map();
    const cleared = [];
    const setTimeout = (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, {callback, due: now + Number(delay), delay: Number(delay)});
      return id;
    };
    const clearTimeout = id => {
      cleared.push(id);
      timers.delete(id);
    };
    const advance = milliseconds => {
      now += milliseconds;
      for (const [id, timer] of [...timers.entries()].sort((left, right) => left[1].due - right[1].due)) {
        if (timer.due > now) continue;
        timers.delete(id);
        timer.callback();
      }
    };
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout,
      clearTimeout,
      performance: {now: () => now},
    });
    api.clearJsDebugEventsForTest();
    class ThrowingEventSource {
      constructor() { throw new Error('constructor unavailable'); }
    }
    api.setEventSourceConstructorForTest(ThrowingEventSource);
    api.installClientEventStreamForTest();
    assert.equal([...timers.values()].filter(timer => timer.delay === 15000).length, 1, 'positive demand arms one exact grace timer when construction throws');
    assert.equal(api.clientEventTransportStateForTest().disconnectEpisode.id, 1);
    advance(14999);
    assert.equal(api.jsDebugFailureEventsForTest().length, 0, '14,999ms remains inside the grace');
    advance(1);
    let failures = api.jsDebugFailureEventsForTest();
    assert.equal(failures.length, 1, '15,000ms records exactly one production diagnostic');
    assert.equal(failures[0].route, '/api/client-events');
    assert.equal(failures[0].deliveryOutcome, 'stalled');
    const receipt = api.jsDebugCurrentObservationReceiptBarrierForTest();
    assert.equal(receipt.pending + receipt.retrying + receipt.rejected + receipt.dropped, 1, 'the exact failure owns one durable receipt');

    class RecoveringEventSource {
      constructor(url) { this.url = url; this.listeners = new Map(); }
      addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(listener);
      }
      close() {}
    }
    api.setEventSourceConstructorForTest(RecoveringEventSource);
    api.applyClientEventDemandForTest();
    const recoveredSource = api.clientEventTransportStateForTest().source;
    recoveredSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    const recovery = api.jsDebugEventsForTest().filter(event => event.eventType === 'client_events_recovered');
    assert.equal(recovery.length, 1, 'ready records one correlated nonblocking recovery');
    assert.equal(recovery[0].disconnectEpisode, failures[0].disconnectEpisode);
    assert.equal(api.jsDebugFailureEventsForTest().length, 1, 'recovery is not a second failure');

    const removed = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {setTimeout, clearTimeout, performance: {now: () => now}});
    removed.setEventSourceConstructorForTest(undefined);
    removed.installClientEventStreamForTest();
    const removalTimer = removed.clientEventTransportStateForTest().disconnectEpisode;
    assert.ok(removalTimer, 'missing EventSource uses the same demanded-transport episode');
    removed.setDocumentVisibilityForTest('hidden');
    removed.applyClientEventDemandForTest();
    assert.equal(removed.clientEventTransportStateForTest().disconnectEpisode, null, 'zero demand cancels the outstanding episode');
  });

  test('client-event resource revisions reject stale events without rejecting a newer server epoch', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.handleClientPushEventForTest('fs_changed', {marker: 'new'}, {epoch: 'server-a', resource: 'fs_changed', resource_revision: 2}), true);
    assert.equal(api.handleClientPushEventForTest('fs_changed', {marker: 'old'}, {epoch: 'server-a', resource: 'fs_changed', resource_revision: 1}), false, 'a delayed resource update cannot replace the latest state');
    assert.equal(api.handleClientPushEventForTest('fs_changed', {marker: 'restart'}, {epoch: 'server-b', resource: 'fs_changed', resource_revision: 1}), true, 'a re-exec server epoch starts an independent sequence');
    assert.deepStrictEqual(canonical(api.clientEventTransportStateForTest().resourceRevisions), {fs_changed: 1});
  });

  test('client-event ready is a reconnect fence and does not discard an unread resource revision', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.applyClientEventReadyEnvelopeForTest({epoch: 'server-a', resource_revisions: {fs_changed: 9}}), true);
    assert.deepStrictEqual(canonical(api.clientEventTransportStateForTest().resourceRevisions), {}, 'ready does not pretend a resource fetch happened');
    assert.equal(api.handleClientPushEventForTest('fs_changed', {marker: 'queued'}, {epoch: 'server-a', resource: 'fs_changed', resource_revision: 9}), true, 'a frame at the ready fence remains deliverable');
  });

  test('client-event ready repairs only same-epoch resource gaps', () => {
    const api = loadYolomux('', ['1']);
    api.handleClientPushEventForTest('fs_changed', {}, {epoch: 'server-a', resource: 'fs_changed', resource_revision: 4});
    assert.deepStrictEqual(canonical(api.clientEventReadyGapResourcesForTest({resource_revisions: {fs_changed: 4, 'event_log_changed:1': 3}})), ['event_log_changed:1']);
  });

  await testAsync('status deltas repair a missed revision and a late joiner to the latest snapshots', async () => {
    const latestAuto = {
      agent_window_snapshot_revision: 3,
      session_order: ['1'],
      sessions: {'1': {target: '1', enabled: true, agent_windows: [{window_index: 0, state: 'needs-input'}]}},
      rules: {mode: 'safe'},
    };
    const latestTmux = {
      ok: true,
      window_count: 1,
      windows: [{session: '1', window_index: 0, key: '1:0', active: true}],
    };
    const patchAuto = {
      patch: true,
      collection: 'sessions',
      changes: {'1': latestAuto.sessions['1']},
      removed_keys: [],
      fields: {agent_window_snapshot_revision: 3},
      removed_fields: [],
    };
    const patchTmux = {
      patch: true,
      collection: 'windows',
      changes: {'1:0': latestTmux.windows[0]},
      removed_keys: [],
      fields: {ok: true, window_count: 1},
      removed_fields: [],
    };
    const makeClient = () => {
      const requests = [];
      const api = loadYolomux('', ['1']);
      api.setFetchForTest(url => {
        requests.push(String(url));
        if (String(url) === '/api/auto-approve') return Promise.resolve(jsonResponse(latestAuto));
        if (String(url) === '/api/tmux-signals?force=1') return Promise.resolve(jsonResponse(latestTmux));
        return Promise.reject(new Error(`unexpected status repair: ${url}`));
      });
      api.setDocumentVisibilityForTest('hidden');
      return {api, requests};
    };
    const initialAuto = {
      agent_window_snapshot_revision: 1,
      session_order: ['1'],
      sessions: {'1': {target: '1', enabled: false, agent_windows: [{window_index: 0, state: 'idle'}]}},
      rules: {mode: 'safe'},
    };
    const initialTmux = {ok: true, window_count: 1, windows: [{session: '1', window_index: 0, key: '1:0', active: false}]};

    const consecutive = makeClient();
    assert.equal(consecutive.api.handleClientPushEventForTest('auto_approve_changed', {data: initialAuto}, {epoch: 'server-a', resource: 'auto_approve_changed', resource_revision: 1}), true);
    assert.equal(consecutive.api.handleClientPushEventForTest('tmux_signals_changed', {data: initialTmux}, {epoch: 'server-a', resource: 'tmux_signals_changed', resource_revision: 1}), true);
    assert.equal(consecutive.api.handleClientPushEventForTest('auto_approve_changed', patchAuto, {epoch: 'server-a', resource: 'auto_approve_changed', base_resource_revision: 1, resource_revision: 2}), true, 'a consecutive auto-approve patch applies without repair');
    assert.equal(consecutive.api.handleClientPushEventForTest('tmux_signals_changed', patchTmux, {epoch: 'server-a', resource: 'tmux_signals_changed', base_resource_revision: 1, resource_revision: 2}), true, 'a consecutive tmux patch applies without repair');

    const missed = makeClient();
    assert.equal(missed.api.handleClientPushEventForTest('auto_approve_changed', {data: initialAuto}, {epoch: 'server-a', resource: 'auto_approve_changed', resource_revision: 1}), true);
    assert.equal(missed.api.handleClientPushEventForTest('tmux_signals_changed', {data: initialTmux}, {epoch: 'server-a', resource: 'tmux_signals_changed', resource_revision: 1}), true);
    assert.equal(missed.api.handleClientPushEventForTest('auto_approve_changed', patchAuto, {epoch: 'server-a', resource: 'auto_approve_changed', base_resource_revision: 2, resource_revision: 3}), false, 'a client that missed revision 2 rejects revision 3 until HTTP repair');
    assert.equal(missed.api.handleClientPushEventForTest('tmux_signals_changed', patchTmux, {epoch: 'server-a', resource: 'tmux_signals_changed', base_resource_revision: 2, resource_revision: 3}), false, 'tmux patches use the same gap fence');
    await flushAsyncWork();

    const late = makeClient();
    assert.equal(late.api.handleClientPushEventForTest('auto_approve_changed', patchAuto, {epoch: 'server-a', resource: 'auto_approve_changed', base_resource_revision: 2, resource_revision: 3}), false, 'a late client cannot apply a delta without a snapshot');
    assert.equal(late.api.handleClientPushEventForTest('tmux_signals_changed', patchTmux, {epoch: 'server-a', resource: 'tmux_signals_changed', base_resource_revision: 2, resource_revision: 3}), false, 'a late tmux consumer also requests a snapshot');
    await flushAsyncWork();

    const reconnect = makeClient();
    assert.equal(reconnect.api.handleClientPushEventForTest('auto_approve_changed', {data: initialAuto}, {epoch: 'server-a', resource: 'auto_approve_changed', resource_revision: 1}), true);
    assert.equal(reconnect.api.handleClientPushEventForTest('tmux_signals_changed', {data: initialTmux}, {epoch: 'server-a', resource: 'tmux_signals_changed', resource_revision: 1}), true);
    assert.equal(reconnect.api.applyClientEventReadyEnvelopeForTest({epoch: 'server-b', resource_revisions: {auto_approve_changed: 3, tmux_signals_changed: 3}}), true);
    reconnect.api.repairClientEventResourcesForTest(['auto_approve_changed', 'tmux_signals_changed'], {epoch: 'server-b', resource_revisions: {auto_approve_changed: 3, tmux_signals_changed: 3}});
    await flushAsyncWork();

    assert.deepStrictEqual(canonical(consecutive.api.autoApproveStateForTest('1')), {...latestAuto.sessions['1'], agent_window_snapshot_revision: 3});
    assert.deepStrictEqual(canonical(consecutive.api.tmuxSignalStateForTest()), latestTmux);
    assert.deepStrictEqual(canonical(consecutive.api.clientEventTransportStateForTest().resourceRevisions), {auto_approve_changed: 2, tmux_signals_changed: 2});
    assert.deepStrictEqual(consecutive.requests, []);

    for (const client of [missed, late, reconnect]) {
      assert.deepStrictEqual(canonical(client.api.autoApproveStateForTest('1')), {...latestAuto.sessions['1'], agent_window_snapshot_revision: 3});
      assert.deepStrictEqual(canonical(client.api.tmuxSignalStateForTest()), latestTmux);
      assert.deepStrictEqual(canonical(client.api.clientEventTransportStateForTest().resourceRevisions), {auto_approve_changed: 3, tmux_signals_changed: 3});
      assert.deepStrictEqual(client.requests.sort(), ['/api/auto-approve', '/api/tmux-signals?force=1']);
    }
  });

  await testAsync('keyed patches at revisions 401/402/403 apply in one frame strictly in order', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => { requests.push(String(url)); return Promise.resolve(jsonResponse({})); });
    api.setDocumentVisibilityForTest('hidden');
    const base = {
      agent_window_snapshot_revision: 400,
      session_order: ['1'],
      sessions: {'1': {target: '1', enabled: false, agent_windows: [{window_index: 0, state: 'idle'}]}},
      rules: {mode: 'safe'},
    };
    // Seed the single applied revision at 400 with a full snapshot.
    assert.equal(api.handleClientPushEventForTest('auto_approve_changed', {data: base}, {epoch: 'server-a', resource: 'auto_approve_changed', resource_revision: 400}), true);
    const patch = revision => ({
      patch: true,
      collection: 'sessions',
      changes: {'1': {target: '1', enabled: revision % 2 === 1, agent_windows: [{window_index: 0, state: `rev-${revision}`}]}},
      removed_keys: [],
      fields: {agent_window_snapshot_revision: revision},
      removed_fields: [],
    });
    // 401, 402, 403 each carry the immediately preceding base and apply in order.
    for (const revision of [401, 402, 403]) {
      assert.equal(
        api.handleClientPushEventForTest('auto_approve_changed', patch(revision), {epoch: 'server-a', resource: 'auto_approve_changed', base_resource_revision: revision - 1, resource_revision: revision}),
        true,
        `revision ${revision} applies on its immediate predecessor`,
      );
      assert.equal(api.clientEventTransportStateForTest().resourceRevisions.auto_approve_changed, revision, 'the single applied revision advances only at validated apply');
    }
    assert.equal(api.autoApproveStateForTest('1').agent_windows[0].state, 'rev-403', 'the last in-order patch is the applied state');
    // A re-delivered 402 (<= the applied 403) is superseded and cannot walk the applied revision back.
    assert.equal(api.handleClientPushEventForTest('auto_approve_changed', patch(402), {epoch: 'server-a', resource: 'auto_approve_changed', base_resource_revision: 401, resource_revision: 402}), false, 'a superseded revision cannot walk the applied revision backward');
    assert.equal(api.clientEventTransportStateForTest().resourceRevisions.auto_approve_changed, 403, 'a rejected out-of-order frame never advances the applied revision');
    await flushAsyncWork();
    assert.deepStrictEqual(requests.filter(url => url === '/api/auto-approve'), [], 'in-order patches repair nothing over ordinary HTTP');
  });

  await testAsync('a revision-only agent-window patch advances the revision without HTTP or a row rebuild', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => { requests.push(String(url)); return Promise.resolve(jsonResponse({})); });
    api.setDocumentVisibilityForTest('hidden');
    const base = {
      agent_window_snapshot_revision: 7,
      session_order: ['1'],
      sessions: {'1': {target: '1', enabled: true, agent_windows: [{window_index: 0, state: 'working'}]}},
      rules: {mode: 'safe'},
    };
    assert.equal(api.handleClientPushEventForTest('auto_approve_changed', {data: base}, {epoch: 'server-a', resource: 'auto_approve_changed', resource_revision: 1}), true);
    assert.equal(api.autoApproveStateForTest('1').agent_window_snapshot_revision, 7);
    // The server re-measured the same rows under a new snapshot revision: a MINIMAL patch (empty
    // changes, one field) advances the revision on every row without changing row content.
    const revisionOnly = {
      patch: true,
      collection: 'sessions',
      changes: {},
      removed_keys: [],
      fields: {agent_window_snapshot_revision: 8},
      removed_fields: [],
    };
    const before = api.autoApproveStateForTest('1');
    const httpBefore = requests.filter(url => url === '/api/auto-approve').length;
    assert.equal(api.handleClientPushEventForTest('auto_approve_changed', revisionOnly, {epoch: 'server-a', resource: 'auto_approve_changed', base_resource_revision: 1, resource_revision: 2}), true);
    const after = api.autoApproveStateForTest('1');
    assert.equal(after.agent_window_snapshot_revision, 8, 'the revision-only patch advances the snapshot revision');
    assert.deepStrictEqual(after.agent_windows, before.agent_windows, 'the row content is unchanged by a revision-only patch');
    assert.equal(after.state, before.state, 'no row field beyond the revision changes');
    await flushAsyncWork();
    assert.equal(requests.filter(url => url === '/api/auto-approve').length - httpBefore, 0, 'a revision-only patch performs no HTTP refetch');
  });

  test('client-event queue overflow maps every dropped resource to a scoped repair owner', () => {
    const source = fs.readFileSync('static_src/js/yolomux/99_client_event_transport.js', 'utf8');
    assert.ok(/function clientEventRepairChannels\(resources = \[\]\)[\s\S]*fs_changed[\s\S]*channels\.add\('files'\)[\s\S]*event_log_changed[\s\S]*channels\.add\('events'\)[\s\S]*return channels/.test(source), 'overflow maps files/event logs to their own repair channels');
    assert.ok(/function handleClientPushEvent\(type, payload = \{\}, envelope = \{\}\)[\s\S]*repairClientEventResources\(repairResources, envelope\)[\s\S]*clientEventEnvelopeIsCurrent\(envelope, payload\)/.test(source), 'the delivered overflow metadata is consumed before stale-frame filtering');
  });

  await testAsync('open event logs demand and refresh only through their SSE invalidation', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({events: []}));
    });
    const slots = api.layoutSlotsForTest();
    const sessionSlot = Object.keys(slots).find(key => slots[key]?.tabs?.includes('1'));
    slots[sessionSlot].active = '1';
    api.setLayoutSlotsForTest(slots);
    api.setEventLogTabActiveForTest('1');
    api.setEventLogScrollTopForTest('1', 57);
    const demand = api.clientEventDemandDescriptorForTest();
    assert.ok(demand.channels.includes('events'), `an active event-log pane alone creates the event-log SSE demand: ${JSON.stringify(demand)}`);
    api.refreshEventLogsFromPushForTest({session: '1'});
    await flushAsyncWork();
    assert.deepStrictEqual(requests, ['/api/events?session=1&limit=120'], 'one session invalidation fetches only its open event log');
    assert.equal(api.eventLogScrollTopForTest('1'), 57, 'a log invalidation preserves the reader position in the bounded event page');
    const runtimeSource = fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8');
    assert.ok(!runtimeSource.includes("resetRuntimeInterval('events', refreshOpenEventLogs"), 'the normal event-log browser interval is retired');
    assert.ok(/resetRuntimeInterval\('events-fallback',[\s\S]*clientEventTransportState\.connected === true[\s\S]*refreshOpenEventLogs/.test(runtimeSource), 'event logs retain a disconnected-only repair path');
  });

  await testAsync('event-log SSE reconnect repairs the active bounded reader once', async () => {
    const api = loadYolomux('', ['1']);
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({events: []}));
    });
    const slots = api.layoutSlotsForTest();
    const sessionSlot = Object.keys(slots).find(key => slots[key]?.tabs?.includes('1'));
    slots[sessionSlot].active = '1';
    api.setLayoutSlotsForTest(slots);
    api.setEventLogTabActiveForTest('1');
    assert.equal(api.handleClientPushEventForTest('event_log_changed', {session: '1'}, {epoch: 'event-log-server', resource: 'event_log_changed:1', resource_revision: 3}), true);
    await flushAsyncWork();
    requests.length = 0;
    api.installClientEventStreamForTest();
    const source = api.clientEventTransportStateForTest().source;
    source.listeners.get('ready')[0]({data: JSON.stringify({epoch: 'event-log-server', resource_revisions: {'event_log_changed:1': 4}}), type: 'ready', lastEventId: ''});
    await flushAsyncWork();
    assert.deepStrictEqual(requests, ['/api/events?session=1&limit=120'], 'a same-epoch event-log revision gap repairs only the demanded log without reviving a polling loop');
    source.listeners.get('ready')[0]({data: JSON.stringify({epoch: 'event-log-server', resource_revisions: {'event_log_changed:1': 4}}), type: 'ready', lastEventId: ''});
    await flushAsyncWork();
    assert.equal(requests.length, 1, 'a same-epoch ready frame with no revision gap does not reread the log');
  });

  await testAsync('event-log SSE bursts coalesce to one in-flight repair plus one current follow-up', async () => {
    const api = loadYolomux('', ['1']);
    const pending = [];
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/events?session=1&limit=120');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });
    const slots = api.layoutSlotsForTest();
    const sessionSlot = Object.keys(slots).find(key => slots[key]?.tabs?.includes('1'));
    slots[sessionSlot].active = '1';
    api.setLayoutSlotsForTest(slots);
    api.setEventLogTabActiveForTest('1');
    api.refreshEventLogsFromPushForTest({session: '1'});
    api.refreshEventLogsFromPushForTest({session: '1'});
    api.refreshEventLogsFromPushForTest({session: '1'});
    assert.equal(pending.length, 1, 'a burst joins the one current event-log repair');
    pending[0].resolve(jsonResponse({events: []}));
    await flushAsyncWork();
    assert.equal(pending.length, 2, 'the burst produces one bounded follow-up for data written during the first fetch');
    pending[1].resolve(jsonResponse({events: []}));
    await flushAsyncWork();
  });

  test('frontend request and transport records keep retired parallel globals absent', () => {
    const source = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    for (const name of [
      'backgroundOwnerStatusPayload', 'backgroundOwnerStatusLoading', 'backgroundOwnerStatusLoaded', 'backgroundOwnerStatusError', 'backgroundOwnerStatusRefreshPromise',
      'activitySummaryPayload', 'activitySummaryRefreshing', 'activitySummaryLastRefreshTs', 'activitySummaryGuard',
      'infoPanelRenderPending', 'infoPanelLastRenderSignature', 'infoPanelLastRenderHtml',
      'clientEventsSource', 'clientEventsConnected', 'clientPushEventQueue', 'clientPushEventFrame', 'reconnectResyncTimer',
    ]) assert.equal(source.includes(name), false, `${name} remains retired`);
    for (const owner of ['backgroundOwnerStatusState', 'activitySummaryState', 'infoPanelRenderCache', 'clientEventTransportState']) {
      assert.ok(source.includes(`const ${owner} = {`), `${owner} is the one owner`);
    }
  });

  await testAsync('transcript metadata record lets direct push payloads invalidate older HTTP work', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/session-metadata'));
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const first = api.refreshSessionMetadataForTest({refreshAuto: false, refreshActivity: false});
    api.refreshSessionMetadataForTest({refreshAuto: false, refreshActivity: false});
    assert.equal(pending.length, 1, 'overlapping metadata callers share one request');
    await api.applySessionMetadataPayloadForTest({marker: 'push', session_order: [], sessions: {}}, {
      refreshAuto: false,
      refreshActivity: false,
      refreshContext: false,
    });
    pending[0].resolve(jsonResponse({marker: 'stale-http', session_order: [], sessions: {}}));
    await first;
    assert.equal(api.transcriptMetadataStateForTest().payload.marker, 'push', 'direct payload invalidates the older HTTP generation');
    assert.equal(api.transcriptMetadataStateForTest().request, null, 'settled stale HTTP work releases its own handle');
    assert.equal(api.transcriptMetadataStateForTest().loading, false, 'direct payload and final cleanup leave loading settled');

    const failed = api.refreshSessionMetadataForTest({refreshAuto: false, refreshActivity: false});
    pending[1].reject(new Error('metadata offline'));
    await failed;
    assert.equal(api.transcriptMetadataStateForTest().error.stage, 'fetch', 'the current fetch failure remains classified');
    assert.ok(api.vmConsoleErrorsForTest().some(message => message.includes('session metadata fetch failed')), 'the expected transport diagnostic is captured by this test instead of printed in a green run');
  });

  await testAsync('a transcripts revision during an older metadata request owns one follow-up cache read', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/session-metadata');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const initial = api.refreshSessionMetadataForTest({refreshAuto: false, refreshActivity: false});
    assert.equal(api.handleClientPushEventForTest(
      'transcripts_changed',
      {refresh: true},
      {epoch: 'server-a', resource: 'transcripts_changed', resource_revision: 1},
    ), true);
    await api.flushQueuedClientPushEventsForTest();
    assert.equal(pending.length, 1, 'the revision waits for the exact older request instead of duplicating it');

    pending[0].resolve(jsonResponse({metadata_identity: {epoch: 'server-a', generation: 1}, marker: 'old', session_order: ['1'], sessions: {'1': {panes: []}}}));
    await initial;
    await flushAsyncWork();
    assert.equal(pending.length, 2, 'the revision starts one cache read after the older request settles');
    pending[1].resolve(jsonResponse({metadata_identity: {epoch: 'server-a', generation: 2}, marker: 'published', session_order: ['1'], sessions: {'1': {panes: []}}}));
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(api.transcriptMetadataStateForTest().payload.marker, 'published', 'the follow-up consumes the revision that arrived during the older request');
    assert.equal(api.transcriptMetadataStateForTest().request, null);
  });

  await testAsync('forced post-mutation metadata supersedes pre-mutation work and rejects stale ABA apply', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/session-metadata'));
      const request = deferredFetch();
      pending.push({url: String(url), ...request});
      return request.promise;
    });

    const stale = api.refreshSessionMetadataForTest({refreshAuto: false, refreshActivity: false});
    const fresh = api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
    assert.equal(pending.length, 2, 'a forced topology refresh never coalesces with pre-mutation metadata');
    assert.equal(pending[1].url, '/api/session-metadata?force=1');
    pending[1].resolve(jsonResponse({marker: 'generation-n-plus-one', session_order: ['1'], sessions: {'1': {generation: 2}}}));
    await fresh;
    pending[0].resolve(jsonResponse({marker: 'generation-n', session_order: ['1'], sessions: {'1': {generation: 1}}}));
    await stale;
    assert.equal(api.transcriptMetadataStateForTest().payload.marker, 'generation-n-plus-one', 'the older same-name generation cannot overwrite the forced result');
    assert.equal(api.transcriptMetadataStateForTest().request, null);
  });

  await testAsync('forced auto-status requests apply only the newest same-topology generation', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/auto-approve');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const first = api.loadAutoStatusesForTest({force: true});
    const second = api.loadAutoStatusesForTest({force: true});
    assert.equal(pending.length, 2, 'forced requests remain independent within one topology epoch');
    pending[1].resolve(jsonResponse({marker: 'newer', session_order: ['1'], sessions: {'1': {target: '1', marker: 'newer'}}}));
    await second;
    pending[0].resolve(jsonResponse({marker: 'older', session_order: ['1'], sessions: {'1': {target: '1', marker: 'older'}}}));
    const stale = await first;

    assert.equal(api.autoApproveStateForTest('1')?.marker, 'newer', 'the older forced response cannot overwrite the newer state');
    assert.equal(stale.staleRequest, true, 'the older response is classified as stale work');
    assert.equal(api.autoStatusRequestActiveForTest(), false, 'the newest request releases the shared active handle');
    assert.deepStrictEqual(api.vmConsoleErrorsForTest(), [], 'stale status work emits no diagnostic');
  });

  await testAsync('metadata and auto-status snapshots reject topology epochs crossed by same-name recreation', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      const request = deferredFetch();
      pending.push({url: String(url), ...request});
      return request.promise;
    });
    const staleMetadata = api.refreshSessionMetadataForTest({refreshAuto: false, refreshActivity: false});
    const staleStatus = api.loadAutoStatusesForTest({force: true, render: false});
    const killed = api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(killed);
    const recreated = api.beginTmuxSessionLifecycleMutationForTest('create', {session: '1'});
    api.commitTmuxSessionLifecycleMutationForTest(recreated, {session: '1'});
    pending.find(item => item.url.startsWith('/api/session-metadata')).resolve(jsonResponse({marker: 'stale-topology', session_order: ['1'], sessions: {'1': {}}}));
    pending.find(item => item.url === '/api/auto-approve').resolve(jsonResponse({marker: 'stale-topology', session_order: ['1'], sessions: {'1': {enabled: true}}}));
    await Promise.all([staleMetadata, staleStatus]);
    assert.notEqual(api.transcriptMetadataStateForTest().payload.marker, 'stale-topology');
    assert.notEqual(api.autoApproveStateForTest('1')?.marker, 'stale-topology');

    const freshMetadata = api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
    const freshStatus = api.loadAutoStatusesForTest({force: true, render: false});
    const freshMetadataRequest = pending.filter(item => item.url.startsWith('/api/session-metadata')).at(-1);
    const freshStatusRequest = pending.filter(item => item.url === '/api/auto-approve').at(-1);
    freshMetadataRequest.resolve(jsonResponse({marker: 'fresh-topology', session_order: ['1'], sessions: {'1': {}}}));
    freshStatusRequest.resolve(jsonResponse({session_order: ['1'], sessions: {'1': {target: '1', enabled: false, marker: 'fresh-topology'}}}));
    await Promise.all([freshMetadata, freshStatus]);
    assert.equal(api.transcriptMetadataStateForTest().payload.marker, 'fresh-topology');
    assert.equal(api.autoApproveStateForTest('1')?.marker, 'fresh-topology');
  });

  test('tmux lifecycle generations make kill-create reuse ABA-safe', () => {
    const api = loadYolomux('', ['1']);
    const originalToken = api.tmuxSessionLifecycleTokenForTest('1');
    const original = api.tmuxSessionLifecycleRecordForTest('1');
    const killed = api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '1'});
    assert.equal(api.tmuxSessionLifecycleRecordForTest('1').phase, 'killing');
    assert.equal(api.tmuxSessionLifecycleTokenIsCurrentForTest(originalToken), false, 'kill blocks every new old-generation lease before its POST');
    api.commitTmuxSessionLifecycleMutationForTest(killed);
    assert.equal(api.tmuxSessionLifecycleRecordForTest('1').phase, 'retired');

    const recreated = api.beginTmuxSessionLifecycleMutationForTest('create', {session: '1'});
    const next = api.tmuxSessionLifecycleRecordForTest('1');
    assert.ok(next.generation > original.generation, 'same-name recreate always receives a later generation');
    assert.equal(next.phase, 'creating');
    api.commitTmuxSessionLifecycleMutationForTest(recreated, {session: '1'});
    assert.equal(api.tmuxSessionLifecycleTokenIsCurrentForTest(api.tmuxSessionLifecycleTokenForTest('1')), true);
    assert.equal(api.rollbackTmuxSessionLifecycleMutationForTest(killed), false, 'a delayed old completion cannot roll back the newer generation');
  });

  test('superseding kill or rename retires a delayed rename target without stale commit', () => {
    for (const nextKind of ['kill', 'rename']) {
      const api = loadYolomux('', ['1']);
      const delayed = api.beginTmuxSessionLifecycleMutationForTest('rename', {session: '1', newName: 'abandoned'});
      assert.equal(api.tmuxSessionLifecycleRecordForTest('abandoned').phase, 'renaming-in');
      const replacement = api.beginTmuxSessionLifecycleMutationForTest(nextKind, {
        session: '1',
        ...(nextKind === 'rename' ? {newName: 'replacement'} : {}),
      });
      assert.equal(delayed.state, 'superseded');
      assert.equal(api.tmuxSessionLifecycleRecordForTest('abandoned').phase, 'retired', `${nextKind} retires the abandoned inbound generation`);
      assert.equal(api.tmuxSessionLifecycleRecordForTest('1').phase, nextKind === 'rename' ? 'renaming-out' : 'killing', `${nextKind} owns the reconciled outbound generation`);
      assert.equal(api.commitTmuxSessionLifecycleMutationForTest(delayed), null, 'the delayed response cannot commit after supersession');
      api.commitTmuxSessionLifecycleMutationForTest(replacement, nextKind === 'rename' ? {newName: 'replacement'} : {});
      if (nextKind === 'rename') assert.equal(api.tmuxSessionLifecycleRecordForTest('replacement').phase, 'renaming-in');
      else assert.equal(api.tmuxSessionLifecycleRecordForTest('1').phase, 'retired');
    }

    const api = loadYolomux('', ['1', '2']);
    const delayed = api.beginTmuxSessionLifecycleMutationForTest('rename', {session: '1', newName: 'abandoned'});
    api.beginTmuxSessionLifecycleMutationForTest('kill', {session: '2'});
    assert.equal(delayed.state, 'superseded');
    assert.equal(api.tmuxSessionLifecycleRecordForTest('1').phase, 'stable', 'a different-session mutation releases the superseded outbound record');
    assert.equal(api.tmuxSessionLifecycleRecordForTest('abandoned').phase, 'retired', 'a different-session mutation still retires the superseded inbound record');
  });

  await testAsync('tmux mutation blocks new old-generation requests and drains issued leases', async () => {
    const api = loadYolomux('', ['1']);
    const lease = api.tmuxSessionLifecycleAcquireRequestForTest('1');
    const mutation = api.beginTmuxSessionLifecycleMutationForTest('rename', {session: '1', newName: 'renamed'});
    let drained = false;
    const wait = api.waitForTmuxSessionLifecycleMutationLeasesForTest(mutation).then(result => {
      drained = result;
      return result;
    });
    await flushAsyncWork();
    assert.equal(drained, false, 'the mutation waits for the already-issued ordinary request');
    assert.equal(api.tmuxSessionLifecycleAcquireRequestForTest('1'), null, 'renaming-out blocks a new old-name request');
    assert.deepStrictEqual(canonical(api.pendingTmuxSessionNamesForTest()), ['renamed']);
    lease.release();
    assert.equal(await wait, true);
    api.commitTmuxSessionLifecycleMutationForTest(mutation, {newName: 'renamed'});
    assert.equal(api.tmuxSessionLifecycleRecordForTest('1').phase, 'retired');
    assert.equal(api.tmuxSessionLifecycleRecordForTest('renamed').phase, 'renaming-in');
  });

  await testAsync('every terminal metadata outcome accounts for a deferred sealed status payload', async () => {
    // Regression: `applyAutoApprovePayload` can HOLD a sealed status payload until metadata knows
    // its sessions, but the only release lived on the success tail of `applySessionMetadataPayload`,
    // after its terminal early returns. A malformed, superseded, older-generation, or
    // committed-render-superseded outcome left the payload held with nothing recorded and nothing
    // scheduled to revisit it, so the published consumer revision could never advance again.
    const api = loadYolomux('', ['1']);
    const quiet = {refreshAuto: false, refreshActivity: false, refreshContext: false};
    const metadataFor = (generation, sessionOrder, extra = {}) => ({
      metadata_identity: {epoch: 'epoch-a', generation},
      session_order: sessionOrder,
      sessions: Object.fromEntries(sessionOrder.map(session => [session, {panes: [], ...extra}])),
    });
    const sealed = revision => ({
      session_order: ['1', '2'],
      sessions: {
        '1': {target: '1', enabled: false, last_action: 'off'},
        '2': {target: '2', enabled: false, last_action: 'off'},
      },
      agent_window_snapshot_revision: revision,
    });
    const lastApply = () => api.transcriptMetadataStateForTest().lastApply;
    const hold = label => assert.equal(
      api.applyAutoApprovePayloadForTest(sealed(9)).deferred, true,
      `${label}: a seal naming a session metadata does not know must be held`,
    );

    assert.equal(await api.applySessionMetadataPayloadForTest(metadataFor(1, ['1']), quiet), true);
    assert.equal(api.transcriptMetadataStateForTest().loaded, true, 'metadata must be loaded before a seal can defer');
    // A newer work graph so the older-generation refusal has something to be older than.
    assert.equal(await api.applySessionMetadataPayloadForTest(
      metadataFor(2, ['1'], {work_graph: {version: 1, generation: 9}}), quiet), true);

    // FINDING 1: all FOUR terminal outcomes, each with a freshly held payload.
    let committedRenderCalls = 0;
    const terminalCases = [
      ['malformed_payload', null, quiet],
      ['superseded_request', metadataFor(3, ['1']), {...quiet, requestIsCurrent: () => false}],
      ['older_work_graph_generation', metadataFor(4, ['1'], {work_graph: {version: 1, generation: 4}}), quiet],
      // The committed-render outcome is current at the entry gate and superseded at the render
      // gate, which is the only way to reach the fourth terminal exit.
      ['committed_render_superseded', metadataFor(5, ['1']), {
        ...quiet,
        requestIsCurrent: () => {
          committedRenderCalls += 1;
          return committedRenderCalls === 1;
        },
      }],
    ];
    for (const [expectedReason, metadataPayload, options] of terminalCases) {
      hold(expectedReason);
      await api.applySessionMetadataPayloadForTest(metadataPayload, options);
      const last = lastApply();
      assert.equal(last.reason, expectedReason, `${expectedReason}: the terminal outcome names itself`);
      assert.ok(last.deferredSealed, `${expectedReason}: the terminal outcome accounts for the held payload`);
      assert.equal(
        last.deferredSealed.state, 'retained_awaiting_metadata',
        `${expectedReason}: metadata still does not cover the held sessions, so retention is recorded rather than silent`,
      );
      assert.equal(last.deferredSealed.revision, 9, `${expectedReason}: the held revision is recorded`);
    }

    // FINDING 4: the retained -> applied transition, through the same owner.
    assert.equal(await api.applySessionMetadataPayloadForTest(metadataFor(6, ['1', '2']), quiet), true);
    assert.equal(lastApply().deferredSealed.state, 'applied', 'a covered payload is released, not held');
    assert.equal(api.autoApproveStateForTest('2').agent_window_snapshot_revision, 9, 'the released payload advanced the consumer revision');
    assert.equal(lastApply().deferredSealed.revision, 9);

    // FINDING 3: a PARTIAL per-session overtake must RETAIN, not discard. Session '1' is pushed
    // past the held revision while session '2' is left behind; a global maximum would wrongly
    // discard status that session '2' never received.
    const api2 = loadYolomux('', ['1']);
    assert.equal(await api2.applySessionMetadataPayloadForTest(metadataFor(1, ['1']), quiet), true);
    assert.equal(api2.applyAutoApprovePayloadForTest(sealed(9)).deferred, true, 'partial: the seal is held');
    api2.setAutoApproveStateForTest('1', {target: '1', enabled: false, last_action: 'off', agent_window_snapshot_revision: 40});
    api2.setAutoApproveStateForTest('2', {target: '2', enabled: false, last_action: 'off', agent_window_snapshot_revision: 3});
    await api2.applySessionMetadataPayloadForTest(null, quiet);
    assert.equal(api2.transcriptMetadataStateForTest().lastApply.reason, 'malformed_payload');
    assert.equal(
      api2.transcriptMetadataStateForTest().lastApply.deferredSealed.state, 'retained_awaiting_metadata',
      'a partial overtake must retain: one advanced session cannot discard status another never received',
    );

    // FINDING 2: a REAL complete overtake discards. Both held sessions are at or past the held
    // revision, so the seal carries no truth any consumer still needs.
    api2.setAutoApproveStateForTest('2', {target: '2', enabled: false, last_action: 'off', agent_window_snapshot_revision: 9});
    await api2.applySessionMetadataPayloadForTest(null, quiet);
    const overtaken = api2.transcriptMetadataStateForTest().lastApply;
    assert.equal(overtaken.reason, 'malformed_payload');
    assert.equal(overtaken.deferredSealed.state, 'discarded_superseded_revision', 'a fully overtaken seal is discarded');
    assert.equal(overtaken.deferredSealed.revision, 9);
    // FINDING 4: retained -> discarded is terminal; nothing is held afterwards.
    await api2.applySessionMetadataPayloadForTest(null, quiet);
    assert.equal(
      api2.transcriptMetadataStateForTest().lastApply.deferredSealed.state, 'none',
      'the discard is terminal: no payload remains held',
    );
  });

  await testAsync('metadata apply records the generation it rendered and a machine-readable reason for every drop', async () => {
    // Regression: a dropped metadata payload returned a bare `false` that no caller read, and the
    // rendered model carried no identity, so "the refresh landed and had nothing new" and "the
    // refresh was silently discarded" were indistinguishable. A create-session gate could only tell
    // them apart by waiting 15s for a watchdog.
    const api = loadYolomux('', ['1']);
    assert.equal(api.transcriptMetadataStateForTest().generation, 0, 'no metadata generation is claimed before one is applied');

    const applied = await api.applySessionMetadataPayloadForTest({
      metadata_identity: {epoch: 'epoch-a', generation: 7},
      cache: {pending_identity: {epoch: 'epoch-a', generation: 9}},
      session_order: ['1'],
      sessions: {'1': {panes: [], work_graph: {version: 1, generation: 4}}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false});
    const afterApply = api.transcriptMetadataStateForTest();
    assert.equal(applied, true);
    assert.equal(afterApply.epoch, 'epoch-a', 'the server process that produced the render is retained with its generation');
    assert.equal(afterApply.generation, 7, 'the applied build generation is retained as the rendered identity');
    assert.equal(afterApply.pendingGeneration, 9, 'the generation the server told the client to expect is retained');
    assert.deepStrictEqual(
      {applied: afterApply.lastApply.applied, reason: afterApply.lastApply.reason, payloadGeneration: afterApply.lastApply.payloadGeneration},
      {applied: true, reason: 'applied', payloadGeneration: 7},
    );

    const dropped = await api.applySessionMetadataPayloadForTest({
      metadata_identity: {epoch: 'epoch-a', generation: 8},
      session_order: ['1'],
      sessions: {'1': {panes: [], work_graph: {version: 1, generation: 3}}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false});
    const afterDrop = api.transcriptMetadataStateForTest();
    assert.equal(dropped, false, 'an older session work graph is still refused');
    assert.deepStrictEqual(
      {applied: afterDrop.lastApply.applied, reason: afterDrop.lastApply.reason, session: afterDrop.lastApply.session},
      {applied: false, reason: 'older_work_graph_generation', session: '1'},
    );
    assert.equal(afterDrop.generation, 7, 'a refused payload cannot advance the rendered generation');

    const superseded = await api.applySessionMetadataPayloadForTest({
      metadata_identity: {epoch: 'epoch-a', generation: 9},
      session_order: ['1'],
      sessions: {'1': {panes: []}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false, requestIsCurrent: () => false});
    assert.equal(superseded, false);
    assert.equal(api.transcriptMetadataStateForTest().lastApply.reason, 'superseded_request', 'a superseded request names itself instead of vanishing');

    // An identity-less payload (an older server) still RENDERS -- refusing it would leave the pane
    // on bytes from a process that may be gone -- but it can never advance the applied generation,
    // because a bare number is not evidence about any particular server's build.
    const unidentified = await api.applySessionMetadataPayloadForTest({
      metadata_generation: 4242,
      session_order: ['1'],
      sessions: {'1': {panes: [], marker: 'legacy-server'}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false});
    const afterUnidentified = api.transcriptMetadataStateForTest();
    assert.equal(unidentified, true, 'an identity-less payload is still rendered');
    assert.equal(afterUnidentified.payload.sessions['1'].marker, 'legacy-server');
    assert.equal(afterUnidentified.epoch, 'epoch-a', 'an identity-less payload cannot change which server the client is tracking');
    assert.equal(afterUnidentified.generation, 7, 'a bare generation scalar cannot advance the applied identity');
  });

  await testAsync('a superseded response still records the build the server promised', async () => {
    // Regression: the pending identity is a fact about the SERVER's build queue, not about whether
    // this client's request is still current, but it was read AFTER the supersede check and so was
    // discarded with the payload. A forced read whose apply lost the race against any concurrent
    // refresh or `transcripts_changed` push therefore left `pendingGeneration` at zero -- a target
    // every payload already satisfies -- while the forced settle, which reads the target from its
    // own response, still converged. The reload gate saw exactly that: the server named build 7 and
    // the browser reported awaiting build 0.
    const api = loadYolomux('', ['1']);
    await api.applySessionMetadataPayloadForTest({
      metadata_identity: {epoch: 'epoch-a', generation: 7},
      cache: {pending_identity: {epoch: 'epoch-a', generation: 9}},
      session_order: ['1'],
      sessions: {'1': {panes: []}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false});
    assert.equal(api.transcriptMetadataStateForTest().pendingGeneration, 9);

    const superseded = await api.applySessionMetadataPayloadForTest({
      metadata_identity: {epoch: 'epoch-a', generation: 10},
      cache: {pending_identity: {epoch: 'epoch-a', generation: 11}},
      session_order: ['1'],
      sessions: {'1': {panes: []}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false, requestIsCurrent: () => false});
    const state = api.transcriptMetadataStateForTest();
    assert.equal(superseded, false, 'a superseded payload is still not rendered');
    assert.equal(state.lastApply.reason, 'superseded_request');
    assert.equal(state.generation, 7, 'a superseded payload cannot advance the RENDERED generation');
    assert.equal(state.pendingGeneration, 11, 'the build the server promised survives the supersede');

    // The epoch fence still holds: a promise from another server process is not comparable here.
    await api.applySessionMetadataPayloadForTest({
      metadata_identity: {epoch: 'epoch-b', generation: 20},
      cache: {pending_identity: {epoch: 'epoch-b', generation: 21}},
      session_order: ['1'],
      sessions: {'1': {panes: []}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false, requestIsCurrent: () => false});
    const afterForeign = api.transcriptMetadataStateForTest();
    assert.equal(afterForeign.epoch, 'epoch-a', 'a superseded reply cannot adopt another epoch');
    assert.equal(afterForeign.pendingGeneration, 11, 'a promise from another server process is not adopted here');
  });

  // ---------------------------------------------------------------------------------------------
  // Session-metadata identity is (server epoch, build generation).
  //
  // The generation counts builds inside ONE server process and restarts at zero in its replacement.
  // A browser that retained a bare generation across a server swap treated the replacement's
  // pre-request cache -- generation 0 -- as an already-observed build, so a forced post-mutation
  // refresh resolved as success without ever reading the generation it had been promised.
  // ---------------------------------------------------------------------------------------------
  const EPOCH_A = 'epoch-aaaaaaaaaaaa';
  const EPOCH_B = 'epoch-bbbbbbbbbbbb';

  function metadataPayload(epoch, generation, extra = {}) {
    const {pending, sessions, ...rest} = extra;
    return {
      ...(epoch ? {metadata_identity: {epoch, generation}} : {}),
      metadata_generation: generation,
      cache: {hit: true, generation, ...(pending ? {pending_identity: pending} : {})},
      session_order: ['1'],
      sessions: sessions || {'1': {panes: []}},
      ...rest,
    };
  }

  async function applyOldServerMetadata(api, epoch = EPOCH_A) {
    await api.applySessionMetadataPayloadForTest(
      metadataPayload(epoch, 50, {
        pending: {epoch, generation: 50},
        sessions: {'1': {panes: [], marker: 'old-server', work_graph: {version: 1, generation: 900}}},
        indexed_repos: [{root: '/old-server-repo'}],
      }),
      {refreshAuto: false, refreshActivity: false, refreshContext: false},
    );
    const state = api.transcriptMetadataStateForTest();
    assert.deepStrictEqual({epoch: state.epoch, generation: state.generation, pendingGeneration: state.pendingGeneration}, {epoch, generation: 50, pendingGeneration: 50});
  }

  await testAsync('a forced refresh answered by a replacement server waits for that server\'s own build', async () => {
    // The exact reproduction: retained applied/pending generation 50, then a new process answers
    // `force=1` from its pre-request cache at generation 0 and promises generation 1.
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [151]});
    await applyOldServerMetadata(api);

    const requests = [];
    api.setFetchForTest(async input => {
      requests.push(String(input));
      if (requests.length === 1) {
        return jsonResponse(metadataPayload(EPOCH_B, 0, {
          pending: {epoch: EPOCH_B, generation: 1},
          sessions: {'1': {panes: [], marker: 'new-server-pre-request-cache'}},
        }));
      }
      if (requests.length < 4) {
        return jsonResponse(metadataPayload(EPOCH_B, 0, {sessions: {'1': {panes: [], marker: 'new-server-pre-request-cache'}}}));
      }
      return jsonResponse(metadataPayload(EPOCH_B, 1, {sessions: {'1': {panes: [], marker: 'new-server-post-request-build'}}}));
    });

    const result = await api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
    const state = api.transcriptMetadataStateForTest();

    assert.deepStrictEqual(requests, [
      '/api/session-metadata?force=1',
      '/api/session-metadata',
      '/api/session-metadata',
      '/api/session-metadata',
    ], 'the force issues convergence reads until the promised build of the NEW epoch arrives');
    assert.equal(state.epoch, EPOCH_B, 'the replacement server owns the identity');
    assert.equal(state.previousEpoch, EPOCH_A, 'the swap names both sides');
    assert.equal(state.generation, 1, 'the applied generation is the new epoch\'s build, not the retained 50');
    assert.equal(state.payload.sessions['1'].marker, 'new-server-post-request-build');
    assert.deepStrictEqual(
      canonical({ok: result.ok, reason: result.reason, requested: result.requested, applied: result.applied}),
      {ok: true, reason: 'converged', requested: {epoch: EPOCH_B, generation: 1}, applied: {epoch: EPOCH_B, generation: 1}},
    );
  });

  await testAsync('negative control: without an epoch the same swap settles instantly against pre-request bytes', async () => {
    // Same byte sequence as the test above with `metadata_identity` removed, i.e. the pre-fix wire.
    // Every assertion above goes red here -- the retained identity is never reset, no convergence
    // read is issued, and the force resolves against the payload that predates it. That is what
    // makes the assertions above evidence about the epoch rather than about anything else.
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [151]});
    await api.applySessionMetadataPayloadForTest(
      metadataPayload(EPOCH_A, 50, {pending: {epoch: EPOCH_A, generation: 50}}),
      {refreshAuto: false, refreshActivity: false, refreshContext: false},
    );

    const requests = [];
    api.setFetchForTest(async input => {
      requests.push(String(input));
      return jsonResponse({
        metadata_generation: 0,
        cache: {hit: true, generation: 0, pending_generation: 1},
        session_order: ['1'],
        sessions: {'1': {panes: [], marker: 'new-server-pre-request-cache'}},
      });
    });

    const result = await api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
    const state = api.transcriptMetadataStateForTest();
    assert.deepStrictEqual(requests, ['/api/session-metadata?force=1'], 'an epoch-less server produces no convergence read');
    assert.equal(state.epoch, EPOCH_A, 'an epoch-less payload cannot re-partition the retained identity');
    assert.equal(state.generation, 50, 'the retained generation is untouched');
    // It is reported as unsatisfiable rather than as success: no build identity was ever named.
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'forced_no_pending_identity');
  });

  await testAsync('a force whose response names no build identity is unsatisfiable, never converged', async () => {
    for (const pending of [undefined, {epoch: EPOCH_A, generation: 0}]) {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [151]});
      await applyOldServerMetadata(api);
      api.setFetchForTest(async () => jsonResponse(metadataPayload(EPOCH_A, 50, pending ? {pending} : {})));
      const result = await api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
      assert.deepStrictEqual({ok: result.ok, reason: result.reason}, {ok: false, reason: 'forced_no_pending_identity'}, `pending=${JSON.stringify(pending)}`);
    }

    // Negative control: the SAME code path returns a converged verdict as soon as a real build
    // identity is named and observed, so the failure above is caused by the missing identity.
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [151]});
    await applyOldServerMetadata(api);
    api.setFetchForTest(async () => jsonResponse(metadataPayload(EPOCH_A, 51, {pending: {epoch: EPOCH_A, generation: 51}})));
    const converged = await api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
    assert.deepStrictEqual({ok: converged.ok, reason: converged.reason}, {ok: true, reason: 'converged'});
  });

  await testAsync('a delayed response from the superseded server cannot take the identity back', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin');
    await applyOldServerMetadata(api);
    await api.applySessionMetadataPayloadForTest(
      metadataPayload(EPOCH_B, 3, {sessions: {'1': {panes: [], marker: 'new-server'}}}),
      {refreshAuto: false, refreshActivity: false, refreshContext: false},
    );
    assert.equal(api.transcriptMetadataStateForTest().epoch, EPOCH_B);

    // The in-flight read issued against the old server finally answers. It is superseded, so it is
    // rejected BEFORE it can touch shared identity -- validation precedes adoption.
    const applied = await api.applySessionMetadataPayloadForTest(
      metadataPayload(EPOCH_A, 51, {sessions: {'1': {panes: [], marker: 'stale-old-server'}}}),
      {refreshAuto: false, refreshActivity: false, refreshContext: false, requestIsCurrent: () => false},
    );
    const state = api.transcriptMetadataStateForTest();
    assert.equal(applied, false);
    assert.equal(state.lastApply.reason, 'superseded_request');
    assert.equal(state.epoch, EPOCH_B, 'a superseded old-epoch reply cannot flip the epoch back');
    assert.equal(state.generation, 3, 'nor overwrite the new epoch\'s applied generation');
    assert.equal(state.payload.sessions['1'].marker, 'new-server', 'nor replace the new server\'s bytes');

    // A/B/A: a CURRENT reply from a third process is adopted normally, and never compared against
    // the generations of either previous one.
    await api.applySessionMetadataPayloadForTest(
      metadataPayload(EPOCH_A, 2, {sessions: {'1': {panes: [], marker: 'restarted-a'}}}),
      {refreshAuto: false, refreshActivity: false, refreshContext: false},
    );
    const alternated = api.transcriptMetadataStateForTest();
    assert.deepStrictEqual({epoch: alternated.epoch, generation: alternated.generation}, {epoch: EPOCH_A, generation: 2});
    assert.equal(alternated.payload.sessions['1'].marker, 'restarted-a', 'a lower generation in a NEW epoch is not stale');
  });

  await testAsync('a force pinned to one server terminates when another server answers', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [151]});
    await applyOldServerMetadata(api);

    const requests = [];
    api.setFetchForTest(async input => {
      requests.push(String(input));
      // The force is answered by A and promises A's generation 51; the convergence read is then
      // answered by a different process. That generation can never satisfy the pinned target.
      if (requests.length === 1) return jsonResponse(metadataPayload(EPOCH_A, 50, {pending: {epoch: EPOCH_A, generation: 51}}));
      return jsonResponse(metadataPayload(EPOCH_B, 99, {sessions: {'1': {panes: [], marker: 'replacement'}}}));
    });

    const result = await api.refreshSessionMetadataForTest({force: true, refreshAuto: false, refreshActivity: false});
    const state = api.transcriptMetadataStateForTest();
    assert.deepStrictEqual({ok: result.ok, reason: result.reason}, {ok: false, reason: 'forced_settle_epoch_changed'});
    assert.equal(state.epoch, EPOCH_B);
    assert.equal(state.generation, 99);
    assert.equal(state.payload.sessions['1'].marker, 'replacement', 'the replacement payload is still rendered; only the verdict fails');
    assert.equal(requests.length, 2, 'the pinned force stops instead of chasing an unrelated counter');
    assert.equal(state.lastApply.awaitedGeneration, 51);
  });

  await testAsync('an epoch change drops the previous process\'s generation-dependent baseline', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin');
    await applyOldServerMetadata(api);
    assert.equal(api.transcriptMetadataStateForTest().payload.sessions['1'].work_graph.generation, 900);

    // A lightweight first payload from the replacement server. Both the preserved work graph and
    // the preserved indexed-repo list are process-local, so inheriting them would relabel a dead
    // server's data as this one's -- and the retained graph generation 900 would refuse every
    // build the new process produces.
    const applied = await api.applySessionMetadataPayloadForTest(
      metadataPayload(EPOCH_B, 1, {metadata_loading: true, indexed_repos: [], sessions: {'1': {panes: [], marker: 'new-server-lightweight', work_graph: {version: 1, generation: 5}}}}),
      {refreshAuto: false, refreshActivity: false, refreshContext: false},
    );
    const state = api.transcriptMetadataStateForTest();
    assert.equal(applied, true, 'a lower work-graph generation from a NEW epoch is not older work');
    assert.equal(state.payload.sessions['1'].work_graph.generation, 5, 'the previous epoch\'s work graph is not carried forward');
    assert.deepStrictEqual(canonical(state.payload.indexed_repos), [], 'nor its indexed-repo baseline');

    // Negative control: inside ONE epoch the same refusal still applies, so the acceptance above is
    // caused by the epoch change and not by the work-graph comparison having been removed.
    const refused = await api.applySessionMetadataPayloadForTest(
      metadataPayload(EPOCH_B, 2, {sessions: {'1': {panes: [], marker: 'same-epoch-older', work_graph: {version: 1, generation: 4}}}}),
      {refreshAuto: false, refreshActivity: false, refreshContext: false},
    );
    assert.equal(refused, false, 'an older work graph within the same epoch is still refused');
    assert.equal(api.transcriptMetadataStateForTest().lastApply.reason, 'older_work_graph_generation');
    assert.equal(api.transcriptMetadataStateForTest().payload.sessions['1'].marker, 'new-server-lightweight');
  });

  await testAsync('a pushed metadata payload must be stamped by the process that delivered it', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin');
    await applyOldServerMetadata(api, EPOCH_B);
    const refreshes = [];
    api.setFetchForTest(async input => {
      refreshes.push(String(input));
      return jsonResponse(metadataPayload(EPOCH_B, 60, {sessions: {'1': {panes: [], marker: 'http-repair'}}}));
    });

    const push = (data, epoch, revision) => api.handleClientPushEventForTest(
      'transcripts_changed',
      {data},
      {epoch, resource: 'transcripts_changed', resource_revision: revision},
    );

    // Matching identities: the inline bytes are applied without an HTTP read.
    push(metadataPayload(EPOCH_B, 61, {sessions: {'1': {panes: [], marker: 'pushed'}}}), EPOCH_B, 1);
    await api.flushQueuedClientPushEventsForTest();
    assert.equal(api.transcriptMetadataStateForTest().payload.sessions['1'].marker, 'pushed');
    assert.deepStrictEqual(refreshes, []);

    // Mismatched, then missing: the untrustworthy inline payload is dropped and the handler falls
    // back to an HTTP read that carries an identity of its own. It never applies as-is.
    let revision = 1;
    for (const [data, label] of [
      [metadataPayload(EPOCH_A, 62, {sessions: {'1': {panes: [], marker: 'foreign-inner-epoch'}}}), 'mismatched'],
      [{metadata_generation: 63, session_order: ['1'], sessions: {'1': {panes: [], marker: 'identity-less'}}}, 'missing'],
    ]) {
      refreshes.length = 0;
      revision += 1;
      push(data, EPOCH_B, revision);
      await api.flushQueuedClientPushEventsForTest();
      await flushAsyncWork();
      assert.notEqual(api.transcriptMetadataStateForTest().payload.sessions['1'].marker, data.sessions['1'].marker, `${label} inner identity must not apply`);
      assert.deepStrictEqual(refreshes, ['/api/session-metadata'], `${label} inner identity falls back to an identified HTTP read`);
    }
  });

  function createSessionFixture(sessionMetadataResponse) {
    const api = loadYolomuxWithFileExplorerClosed('?sessions=1&layout=left&tabs=left:1', ['1'], 'http:', 'Linux x86_64', 'admin');
    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'http://localhost');
      if (parsed.pathname === '/api/create-session-plan') return Promise.resolve(jsonResponse({session: '2', generation: 7, ok: true}));
      if (parsed.pathname === '/api/create-session') return Promise.resolve(jsonResponse({session: '2', sessions: ['1', '2'], agent: 'codex', created: true, ok: true}));
      if (parsed.pathname === '/api/ensure-session') return Promise.resolve(jsonResponse({session: '2', created: false, ok: true}));
      if (parsed.pathname === '/api/session-metadata') return sessionMetadataResponse(parsed);
      return Promise.resolve(jsonResponse({ok: true}));
    });
    return api;
  }

  await testAsync('a committed session mutation survives a post-commit refresh that never converges', async () => {
    // The mutation boundary's verdict used to be a discarded boolean: a forced refresh that never
    // observed the promised build reported exactly the same success as one that did. It must now
    // report a typed reason -- and it must still never undo a session the user really created,
    // because the server mutation and the local commit have both already happened.
    const api = createSessionFixture(() => Promise.resolve(jsonResponse(
      // The post-mutation force is answered by a DIFFERENT server than the one that will build it.
      metadataPayload(EPOCH_B, 4, {pending: {epoch: EPOCH_A, generation: 99}, sessions: {'1': {panes: []}, '2': {panes: []}}, session_order: ['1', '2']}),
    )));

    await api.createNextSessionForTest('codex');
    await flushAsyncWork();

    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
      left: {tabs: ['1', '2'], active: '2'},
    }, 'a non-converged reconciliation never rolls back the committed session');
    assert.equal(api.tmuxSessionLifecycleRecordForTest('2').phase !== 'retired', true, 'the committed lifecycle transaction is preserved');
    assert.ok(/created 2/.test(api.statusHtmlForTest()), api.statusHtmlForTest());
    assert.equal(api.metadataConvergenceStatusForTest(), 'forced_settle_epoch_changed', 'the mutation boundary names why its view is not current');

    // The release-blocking signal is a structured, owned diagnostic -- not an unowned console
    // warning. It carries the machine-readable reason and the identity it was waiting on, and it
    // is release-blocking through the same jsDebugFailureEvents() path every other client failure
    // uses.
    const failures = api.jsDebugFailureEventsForTest('error')
      .filter(event => event.failure === 'session_metadata_convergence');
    assert.equal(failures.length, 1, 'exactly one owned diagnostic is recorded for the non-convergence');
    assert.deepStrictEqual(
      {type: failures[0].type, reason: failures[0].reason, awaitedGeneration: failures[0].awaitedGeneration},
      {type: 'client_failure', reason: 'forced_settle_epoch_changed', awaitedGeneration: 99},
    );
    assert.equal(api.vmConsoleErrorsForTest().some(entry => /did not converge/.test(entry)), false, 'the verdict no longer goes to console');
  });

  await testAsync('negative control: a converging post-commit refresh reports no outstanding repair', async () => {
    // Same mutation, same code path, with the force answered by the server that built it. If this
    // could not come back clean, the assertion above would be measuring the fixture, not the fix.
    const api = createSessionFixture(() => Promise.resolve(jsonResponse(
      metadataPayload(EPOCH_B, 4, {pending: {epoch: EPOCH_B, generation: 4}, sessions: {'1': {panes: []}, '2': {panes: []}}, session_order: ['1', '2']}),
    )));

    await api.createNextSessionForTest('codex');
    await flushAsyncWork();

    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {left: {tabs: ['1', '2'], active: '2'}});
    assert.equal(api.metadataConvergenceStatusForTest(), '', 'a converged reconciliation leaves no repair marker behind');
    // Negative control for the diagnostic above: a healthy mutation emits NO client_failure at all,
    // so the assertion there is measuring the non-convergence and not the mutation.
    assert.equal(
      api.jsDebugFailureEventsForTest('error').filter(event => event.failure === 'session_metadata_convergence').length,
      0,
      'a converged mutation emits no convergence diagnostic',
    );
  });

  await testAsync('post-mutation reconciliation reports a typed reason for a metadata read that fails', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin');
    api.setFetchForTest(url => (String(url).startsWith('/api/session-metadata')
      ? Promise.reject(new Error('metadata unreachable'))
      : Promise.resolve(jsonResponse({ok: true}))));

    const result = await api.refreshTmuxSessionMutationStateForTest();
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'fetch_failed');
    assert.equal(result.stage, 'fetch');
    assert.equal(api.metadataConvergenceStatusForTest(), 'fetch_failed');
  });

  await testAsync('sealed agent-window status waits for its metadata roster before becoming visible', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin');
    await api.applySessionMetadataPayloadForTest({
      session_order: ['1'],
      sessions: {'1': {panes: []}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false});
    api.applyAutoApprovePayloadForTest({
      agent_window_snapshot_revision: 17,
      session_order: ['1', '2'],
      sessions: {
        '1': {agent_windows: [{window_index: 0, kind: 'codex'}]},
        '2': {agent_windows: [{window_index: 0, kind: 'claude'}]},
      },
    }, {render: false});

    assert.equal(api.sessionAgentWindowStatusModelForTest('2').stateRevision, 0, 'a sealed status snapshot must not expose a session absent from its metadata roster');
    await api.applySessionMetadataPayloadForTest({
      session_order: ['1', '2'],
      sessions: {'1': {panes: []}, '2': {panes: []}},
    }, {refreshAuto: false, refreshActivity: false, refreshContext: false});
    assert.equal(api.sessionAgentWindowStatusModelForTest('2').stateRevision, 17, 'metadata convergence releases the held sealed status snapshot');
  });

  await testAsync('Tabs menu shows cached labels immediately then refreshes its open rows from live session metadata', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/session-metadata?force=1', 'opening Tabs requests one live metadata refresh after rendering cached rows');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });
    api.renderSessionButtonsForTest({force: true});
    const tabs = Array.from(api.sessionButtonsForTest().querySelectorAll('.app-menu'))
      .find(menu => menu.dataset.appMenu === 'tabs');
    assert.ok(tabs, 'Tabs menu is rendered from the existing cached session snapshot');
    const cachedCommand = api.appMenuTree().find(menu => menu.id === 'tabs')?.items.find(item => item.targetItem === '1');
    assert.ok(cachedCommand, 'cached session row is available without waiting for list-sessions');
    assert.equal(cachedCommand.html.includes('fresh-session-name'), false, 'the initial row is the pre-refresh cached label');

    api.openAppMenuForTest(tabs);
    assert.equal(tabs.classList.contains('open'), true, 'Tabs opens immediately while live metadata is pending');
    assert.equal(pending.length, 1, 'reopening work is coalesced through the shared metadata request record');
    pending[0].resolve(jsonResponse({
      session_order: ['1'],
      sessions: {
        '1': {
          panes: [],
          agents: [],
          work_graph: {
            version: 1,
            generation: 1,
            tmux_sessions: {'tmux-session:1': {id: 'tmux-session:1', name: '1', tmux_window_ids: ['tmux-window:1:0'], tmux_pane_ids: ['tmux-pane:1:0.0'], runtime_actor_ids: ['actor:1:0'], path_observation_ids: ['observation:1:0']}},
            tmux_windows: {'tmux-window:1:0': {id: 'tmux-window:1:0', tmux_session_id: 'tmux-session:1', index: '0', name: '', tmux_pane_ids: ['tmux-pane:1:0.0']}},
            tmux_panes: {'tmux-pane:1:0.0': {id: 'tmux-pane:1:0.0', tmux_window_id: 'tmux-window:1:0', target: '%1-0', index: '0', current_path: '/tmp/fresh-session-name', active: true, window_active: true, runtime_actor_ids: ['actor:1:0'], path_observation_ids: ['observation:1:0']}},
            runtime_actors: {'actor:1:0': {id: 'actor:1:0', tmux_pane_id: 'tmux-pane:1:0.0', kind: 'shell', cwd: '/tmp/fresh-session-name', status: '', path_observation_ids: ['observation:1:0']}},
            path_observations: {'observation:1:0': {id: 'observation:1:0', tmux_pane_id: 'tmux-pane:1:0.0', runtime_actor_id: 'actor:1:0', git_worktree_id: 'worktree:/tmp/fresh-session-name', path: '/tmp/fresh-session-name', source: 'fixture', priority: 0, last_observed_at: 1}},
            git_worktrees: {'worktree:/tmp/fresh-session-name': {id: 'worktree:/tmp/fresh-session-name', root: '/tmp/fresh-session-name', git_dir: '/tmp/fresh-session-name/.git', kind: 'primary', parent_root: '', local_repository_id: 'local:/tmp/fresh-session-name', hosted_repository_id: null, current_branch_id: 'branch:local:/tmp/fresh-session-name:fresh-session-name', branch_activity_ids: [], path_observation_ids: ['observation:1:0'], git: {root: '/tmp/fresh-session-name', branch: 'fresh-session-name'}}},
            local_repositories: {'local:/tmp/fresh-session-name': {id: 'local:/tmp/fresh-session-name', common_git_dir: '/tmp/fresh-session-name/.git', git_worktree_ids: ['worktree:/tmp/fresh-session-name'], local_branch_ids: ['branch:local:/tmp/fresh-session-name:fresh-session-name'], hosted_repository_id: null}},
            hosted_repositories: {},
            local_branches: {'branch:local:/tmp/fresh-session-name:fresh-session-name': {id: 'branch:local:/tmp/fresh-session-name:fresh-session-name', local_repository_id: 'local:/tmp/fresh-session-name', name: 'fresh-session-name', current: true, pull_request_ids: [], linear_issue_ids: []}},
            pull_requests: {},
            linear_issues: {},
            worktree_branch_activity: {},
          },
        },
      },
    }));
    await flushAsyncWork();
    assert.equal(tabs.classList.contains('open'), true, 'the live update patches the open Tabs menu instead of closing it');
    const refreshedCommand = api.appMenuTree().find(menu => menu.id === 'tabs')?.items.find(item => item.targetItem === '1');
    assert.ok(refreshedCommand, 'the live update retains the cached session row identity');
    assert.equal(refreshedCommand.html.includes('fresh-session-name'), true, 'the open Tabs row receives the live list-sessions name/description');
  });

  await testAsync('Search and Runs records reject stale query and refresh completions', async () => {
    const pendingSearch = [];
    const pendingRuns = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      const value = String(url);
      if (value.startsWith('/api/search?')) {
        const request = {...deferredFetch(), url: value};
        pendingSearch.push(request);
        return request.promise;
      }
      if (value === '/api/run-history') {
        const request = deferredFetch();
        pendingRuns.push(request);
        return request.promise;
      }
      throw new Error(`unexpected URL ${value}`);
    });

    const oldSearch = api.runSearchHistoryQueryForTest('old');
    const newSearch = api.runSearchHistoryQueryForTest('new');
    pendingSearch[1].resolve(jsonResponse({query: 'new', marker: 'new', results: []}));
    await newSearch;
    pendingSearch[0].resolve(jsonResponse({query: 'old', marker: 'old', results: []}));
    await oldSearch;
    assert.equal(api.searchHistoryStateForTest().query, 'new', 'visible query remains the newest input');
    assert.equal(api.searchHistoryStateForTest().payload.marker, 'new', 'older query results cannot replace the newest payload');
    assert.equal(api.searchHistoryStateForTest().loading, false, 'only the current search clears loading');

    const staleAfterEmpty = api.runSearchHistoryQueryForTest('will-clear');
    await api.runSearchHistoryQueryForTest('');
    pendingSearch[2].resolve(jsonResponse({query: 'will-clear', results: [{title: 'stale'}]}));
    await staleAfterEmpty;
    assert.equal(api.searchHistoryStateForTest().query, '', 'empty query reset remains current');
    assert.deepStrictEqual(canonical(api.searchHistoryStateForTest().payload.results), [], 'empty query invalidates older results');

    const oldRuns = api.refreshRunHistoryDataForTest();
    const newRuns = api.refreshRunHistoryDataForTest();
    pendingRuns[1].resolve(jsonResponse({marker: 'new-runs', runs: []}));
    await newRuns;
    pendingRuns[0].reject(new Error('stale runs failed'));
    await oldRuns;
    assert.equal(api.runHistoryStateForTest().payload.marker, 'new-runs', 'stale run-history failure cannot replace current rows');
    assert.equal(api.runHistoryStateForTest().error, null, 'stale run-history failure stays silent');
  });

  await testAsync('YO!agent jobs record lets forced loads and direct pushes replace older hydration', async () => {
    const pending = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/yoagent/jobs');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const oldLoad = api.loadYoagentJobsForTest({silent: true});
    const newLoad = api.loadYoagentJobsForTest({force: true, silent: true});
    pending[1].resolve(jsonResponse({jobs: [{id: 'new'}]}));
    await newLoad;
    pending[0].resolve(jsonResponse({jobs: [{id: 'old'}]}));
    await oldLoad;
    assert.equal(api.yoagentJobsStateForTest().items[0].id, 'new', 'forced replacement rejects the older job list');

    const staleAfterPush = api.loadYoagentJobsForTest({force: true, silent: true});
    api.applyYoagentJobsPayloadForTest({jobs: [{id: 'push'}]});
    pending[2].resolve(jsonResponse({jobs: [{id: 'stale'}]}));
    await staleAfterPush;
    assert.equal(api.yoagentJobsStateForTest().items[0].id, 'push', 'direct job payload invalidates older hydration');
    assert.equal(api.yoagentJobsStateForTest().loading, false, 'job record loading settles after stale request cleanup');
  });

  await testAsync('YO!agent conversation record does not drop forced or pushed updates during hydration', async () => {
    const pending = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/yoagent/conversation');
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const oldLoad = api.loadYoagentConversationForTest({silent: true, render: false});
    const newLoad = api.loadYoagentConversationForTest({force: true, silent: true, render: false});
    assert.equal(pending.length, 2, 'forced conversation refresh starts a replacement generation instead of being dropped');
    pending[1].resolve(jsonResponse({messages: [{content: 'new'}], pending_waits: [], transcript_path: '/new.jsonl'}));
    await newLoad;
    pending[0].resolve(jsonResponse({messages: [{content: 'old'}], pending_waits: [{id: 'old-wait'}], transcript_path: '/old.jsonl'}));
    await oldLoad;
    assert.equal(api.yoagentConversationStateForTest().messages[0].content, 'new', 'older hydration cannot replace the forced conversation');
    assert.equal(api.yoagentConversationStateForTest().path, '/new.jsonl', 'transcript path belongs to the same current record generation');

    const staleAfterPush = api.loadYoagentConversationForTest({force: true, silent: true, render: false});
    api.applyYoagentConversationPayloadForTest({messages: [{content: 'push'}], pending_waits: [], transcript_path: '/push.jsonl'});
    pending[2].resolve(jsonResponse({messages: [{content: 'stale'}], pending_waits: [{id: 'stale-wait'}]}));
    await staleAfterPush;
    assert.equal(api.yoagentConversationStateForTest().messages[0].content, 'push', 'direct conversation payload invalidates older hydration');
    assert.deepStrictEqual(canonical(api.yoagentConversationStateForTest().pendingWaits), [], 'stale hydration cannot resurrect cleared waits');
    assert.equal(api.yoagentConversationStateForTest().loading, false, 'conversation loading settles after stale cleanup');
  });

  await testAsync('YO!agent read resources dedupe same targets and preserve consumer error policy', async () => {
    const pendingJobs = [];
    const pendingConversation = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      const request = deferredFetch();
      if (String(url) === '/api/yoagent/jobs') pendingJobs.push(request);
      else if (String(url) === '/api/yoagent/conversation') pendingConversation.push(request);
      else throw new Error(`unexpected URL ${url}`);
      return request.promise;
    });

    const jobs = api.loadYoagentJobsForTest({silent: true});
    assert.strictEqual(api.loadYoagentJobsForTest({silent: true}), jobs, 'same-target jobs reads share one request');
    pendingJobs[0].resolve(jsonResponse({jobs: [{id: 'last-good-job'}]}));
    assert.equal(await jobs, true, 'jobs retain their boolean success result');
    const failedJobs = api.loadYoagentJobsForTest({force: true, silent: true});
    pendingJobs[1].reject(new Error('jobs offline'));
    assert.equal(await failedJobs, false, 'silent jobs errors remain swallowed as false');
    assert.equal(api.yoagentJobsStateForTest().items[0].id, 'last-good-job', 'jobs failure preserves last good data');

    const conversation = api.loadYoagentConversationForTest({silent: true, render: false});
    assert.strictEqual(api.loadYoagentConversationForTest({silent: true, render: false}), conversation, 'same-target conversation reads share one request');
    pendingConversation[0].resolve(jsonResponse({messages: [{content: 'last-good-conversation'}], pending_waits: []}));
    assert.equal(await conversation, true, 'conversation retains its boolean apply result');
    const failedConversation = api.loadYoagentConversationForTest({force: true, silent: true, render: false});
    pendingConversation[1].reject(new Error('conversation offline'));
    assert.equal(await failedConversation, false, 'silent conversation errors remain swallowed as false');
    assert.equal(api.yoagentConversationStateForTest().messages[0].content, 'last-good-conversation', 'conversation failure preserves last good data');
  });

  test('data request records keep retired parallel globals absent', () => {
    const source = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    assert.equal(/\btranscriptMeta\b/.test(source), false, 'transcriptMeta remains retired');
    assert.equal(/\byoagentJobs\b/.test(source), false, 'yoagentJobs remains retired');
    for (const name of ['yoagentMessages', 'yoagentPendingWaits', 'yoagentConversationLoaded', 'yoagentConversationLoading', 'yoagentConversationPath', 'yoagentConversationDisplayPath', 'yoagentStreamingMessages']) {
      assert.equal(source.includes(name), false, `${name} remains retired`);
    }
    for (const name of [
      'transcriptMetaLoading', 'transcriptMetaLoaded', 'transcriptMetaLoadError', 'transcriptMetaRefreshPromise',
      'searchHistoryQuery', 'searchHistoryPayload', 'searchHistoryLoading', 'searchHistoryError',
      'runHistoryPayload', 'runHistoryLoading', 'runHistoryError', 'yoagentJobsLoading',
    ]) assert.equal(source.includes(name), false, `${name} remains retired`);
    for (const owner of ['transcriptMetadataState', 'searchHistoryState', 'runHistoryState', 'yoagentConversationState', 'yoagentJobsState']) {
      assert.ok(source.includes(`const ${owner} = {`), `${owner} is the one owner`);
    }
    assert.equal(source.includes('yoagentConversationState.guard'), false, 'conversation has no parallel generation guard');
    assert.equal(source.includes('yoagentJobsState.guard'), false, 'jobs has no parallel generation guard');
  });

  await testAsync('Finder Sync record cancels stale root work and resets manual ownership atomically', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerSyncStateForTest({inFlightSignature: 'old-plan', appliedPlanKey: 'old-plan', generation: 4});
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/fast/list?'));
      const request = {
        ...deferredFetch(),
        path: new URL(String(url), 'https://yolomux.test').searchParams.get('path'),
      };
      pending.push(request);
      return request.promise;
    });
    const reply = pendingRequest => jsonResponse({path: pendingRequest.path, entries: [{name: 'README.md', kind: 'file'}]});

    const staleOpen = api.openFileExplorerAtForTest('/home/test/old-root', {syncSelection: true, refreshPanels: false});
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(pending.length, 1, 'the old sync root has one pending directory transaction');
    const manualOpen = api.openFileExplorerAtForTest('/home/test/new-root', {manualSelection: true, refreshPanels: false});
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(pending.length, 2, 'manual root selection starts a newer directory transaction');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncStateForTest()), {
      inFlightSignature: '',
      appliedPlanKey: '',
      generation: 5,
    }, 'manual selection invalidates the entire prior Finder Sync transaction record');

    pending[1].resolve(reply(pending[1]));
    assert.equal(await manualOpen, true, 'the manual root applies');
    pending[0].resolve(reply(pending[0]));
    assert.equal(await staleOpen, false, 'the older root completion is rejected');
    assert.equal(api.fileExplorerRootForTest(), '/home/test/new-root', 'stale root work cannot replace the manual root');
    const settled = api.fileExplorerSyncStateForTest();
    assert.equal(settled.inFlightSignature, '', 'manual root completion leaves no stale in-flight signature');
    assert.equal(settled.appliedPlanKey, '', 'manual root completion leaves no stale applied plan');
  });

  test('Finder Sync target record keeps expanded and manual-collapse state atomic through switches and eviction', () => {
    const api = loadYolomux('', ['1']);
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerVisibleSyncTargetForTest('1', '/repo/a');
    api.resetFileExplorerSyncManualCollapsesForTest({session: '1', root: '/repo/a'});
    api.setFileExplorerExpandedForTest(['/repo/a/src']);
    api.rememberFileExplorerSyncExpandedStateForTest('1', '/repo/a');
    api.rememberFileExplorerSyncManualCollapseForTest('/repo/a/src');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncTargetRecordForTest('1\x1f/repo/a')), {
      expandedPaths: ['/repo/a/src'],
      manualCollapsedPaths: ['/repo/a/src'],
      cursorPath: '',
      selectedPaths: [],
      anchorPath: '',
    }, 'one target record owns both disclosure fields');

    api.setFileExplorerVisibleSyncTargetForTest('1', '/repo/b');
    api.resetFileExplorerSyncManualCollapsesForTest({session: '1', root: '/repo/b'});
    api.setFileExplorerExpandedForTest(['/repo/b/lib']);
    api.rememberFileExplorerSyncExpandedStateForTest('1', '/repo/b');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncTargetRecordForTest('1\x1f/repo/b')), {
      expandedPaths: ['/repo/b/lib'],
      manualCollapsedPaths: [],
      cursorPath: '',
      selectedPaths: [],
      anchorPath: '',
    }, 'a second target receives an independent complete record');

    api.resetFileExplorerSyncManualCollapsesForTest({session: '1', root: '/repo/a'});
    assert.deepStrictEqual(canonical(api.fileExplorerSyncManualCollapsedPathsForTest()), ['/repo/a/src'], 'switching back restores the same manual-collapse set owned by target A');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncTargetRecordForTest('1\x1f/repo/a').expandedPaths), ['/repo/a/src'], 'switching manual state cannot evict the paired expansion field');

    for (let index = 0; index <= api.fileExplorerMemoryCacheLimitForTest; index += 1) {
      api.touchFileExplorerSyncTargetRecordForTest(`synthetic-${index}`);
    }
    assert.equal(api.fileExplorerSyncTargetRecordForTest('1\x1f/repo/a'), null, 'bounded eviction removes the complete old target record');
    assert.equal(api.fileExplorerSyncTargetRecordKeysForTest().length, api.fileExplorerMemoryCacheLimitForTest, 'combined target cache keeps the shared bound');

    const source = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    assert.equal(source.includes('fileExplorerExpandedBySyncTarget'), false, 'retired expansion map stays absent');
    assert.equal(source.includes('fileExplorerSyncManualCollapsedByTarget'), false, 'retired manual-collapse map stays absent');
    assert.ok(source.includes('const fileExplorerSyncTargetRecords = new Map()'), 'one target-record map remains');
  });

  await testAsync('Finder Sync merges touched paths without overriding manual disclosure state', async () => {
    const api = loadYolomux('', ['1', '2']);
    const root = '/home/test';
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerDirListingForTest(root, [{name: 'dev', kind: 'dir'}, {name: 'other', kind: 'dir'}]);
    api.setFileExplorerDirListingForTest('/home/test/dev', [{name: 'ant', kind: 'dir'}]);
    api.setFileExplorerDirListingForTest('/home/test/dev/ant', [{name: 'README.md', kind: 'file'}]);
    api.setFileExplorerDirListingForTest('/home/test/other', [{name: 'changed.txt', kind: 'file'}]);

    await api.syncFileExplorerRootToPlanForTest({session: '1', root, expandPaths: [], affectedDirs: []}, '1');
    api.setFileExplorerSyncUserExpansionForTest('/home/test/dev', true);
    api.setFileExplorerSyncUserExpansionForTest('/home/test/dev/ant', true);
    await api.syncFileExplorerRootToPlanForTest({session: '2', root, expandPaths: ['/home/test/other'], affectedDirs: ['/home/test/other']}, '2');
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), ['/home/test/dev', '/home/test/dev/ant', '/home/test/other'], 'a later sync tick adds its touched path without collapsing manually expanded ancestors');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncUserExpansionStateForTest()), [['/home/test/dev', true], ['/home/test/dev/ant', true]], 'one user-intent map records manual expansion provenance separately from automatic sync paths');

    api.setFileExplorerSyncUserExpansionForTest('/home/test/dev', false);
    await api.syncFileExplorerRootToPlanForTest({session: '1', root, expandPaths: ['/home/test/dev', '/home/test/dev/ant'], affectedDirs: ['/home/test/dev/ant']}, '1');
    assert.equal(api.fileExplorerExpandedForTest().includes('/home/test/dev'), false, 'a later touched file cannot re-expand a path the user collapsed');
  });

  await testAsync('Finder Sync remembers independent same-root keyboard cursors without taking focus', async () => {
    const api = loadYolomux('', ['1', '2']);
    api.setFileExplorerRootMode('sync', {sync: false});
    const root = '/home/test';
    api.setFileExplorerDirListingForTest(root, [
      {name: 'one.txt', kind: 'file'},
      {name: 'two.txt', kind: 'file'},
    ]);
    const terminal = new TestElement('terminal');
    terminal.classList.add('xterm');
    api.setDocumentActiveElementForTest(terminal);
    const firstPlan = {session: '1', root, expandPaths: [], affectedDirs: [root]};
    const secondPlan = {session: '2', root, expandPaths: [], affectedDirs: [root]};

    await api.syncFileExplorerRootToPlanForTest(firstPlan, '1');
    const firstRow = api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/home/test/one.txt"]');
    let firstScrolls = 0;
    firstRow.scrollIntoView = () => { firstScrolls += 1; };
    api.selectFileTreePath('/home/test/one.txt');
    await api.syncFileExplorerRootToPlanForTest(secondPlan, '2');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncTargetRecordForTest(`1\x1f${root}`)), {
      expandedPaths: [],
      manualCollapsedPaths: [],
      cursorPath: '/home/test/one.txt',
      selectedPaths: ['/home/test/one.txt'],
      anchorPath: '/home/test/one.txt',
    }, 'leaving a target stores its cursor and selection in the existing session+root record');

    api.selectFileTreePath('/home/test/two.txt');
    await api.syncFileExplorerRootToPlanForTest(firstPlan, '1');
    const restoredOne = api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/home/test/one.txt"]');
    assert.equal(api.fileExplorerSelectionLeadForTest(), '/home/test/one.txt', 'returning to session 1 restores its own lead row');
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest().paths), ['/home/test/one.txt'], 'the remembered lead is selected after restore');
    assert.equal(api.fileExplorerTreeForTest().getAttribute('aria-activedescendant'), restoredOne.id, 'the tree active-descendant names the restored cursor row');
    assert.equal(firstScrolls, 0, 'an already visible restored cursor does not force the native scroll fallback');
    assert.equal(api.documentActiveElementForTest(), terminal, 'cursor restore never steals terminal/browser focus');

    await api.syncFileExplorerRootToPlanForTest(secondPlan, '2');
    assert.equal(api.fileExplorerSelectionLeadForTest(), '/home/test/two.txt', 'the same-root session 2 record remains independent');
  });

  await testAsync('Finder Sync cursor restore falls back to a visible ancestor without a fetch', async () => {
    const frames = [];
    const api = loadYolomux('', ['1', '2'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    api.setFileExplorerRootMode('sync', {sync: false});
    const root = '/home/test';
    api.setFileExplorerDirListingForTest(root, [{name: 'project', kind: 'dir'}]);
    api.setFileExplorerDirListingForTest(`${root}/project`, [{name: 'inside.txt', kind: 'file'}]);
    let fetchCalls = 0;
    api.setFetchForTest(() => {
      fetchCalls += 1;
      return Promise.reject(new Error('cursor restoration must stay cache-first'));
    });
    const expandedPlan = {session: '1', root, expandPaths: [`${root}/project`], affectedDirs: [`${root}/project`]};
    const collapsedPlan = {session: '2', root, expandPaths: [], affectedDirs: [root]};
    await api.syncFileExplorerRootToPlanForTest(expandedPlan, '1');
    api.selectFileTreePath(`${root}/project/inside.txt`);
    api.setFileExplorerExpandedForTest([]); // emulate a manually collapsed parent before leaving session 1
    await api.syncFileExplorerRootToPlanForTest(collapsedPlan, '2');
    await api.syncFileExplorerRootToPlanForTest({session: '1', root, expandPaths: [], affectedDirs: [root]}, '1');
    assert.equal(api.fileExplorerSelectionLeadForTest(), `${root}/project`, 'a hidden remembered child degrades to its visible ancestor');
    assert.equal(api.fileExplorerTreeForTest().getAttribute('aria-activedescendant'), api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/home/test/project"]').id, 'fallback cursor remains exposed to assistive tree navigation');
    assert.equal(fetchCalls, 0, 'restoring a collapsed cursor adds no blocking fetch before deferred revalidation');
  });

  await testAsync('Finder Sync warm session switches render synchronously from the bounded listing cache', async () => {
    const frames = [];
    const api = loadYolomux('', ['1', '2'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    api.setFileExplorerRootMode('sync', {sync: false});
    const listings = new Map([
      ['/home/test', [{name: 'project', kind: 'dir'}]],
      ['/home/test/project', [{name: 'one', kind: 'dir'}, {name: 'two', kind: 'dir'}]],
      ['/home/test/project/one', [{name: 'a.js', kind: 'file'}]],
      ['/home/test/project/two', [{name: 'b.js', kind: 'file'}]],
    ]);
    for (const [path, entries] of listings) api.setFileExplorerDirListingForTest(path, entries);
    let fetchCalls = 0;
    api.setFetchForTest(() => {
      fetchCalls += 1;
      return Promise.reject(new Error('warm switch must not block on HTTP'));
    });
    const firstPlan = {
      session: '1',
      root: '/home/test',
      expandPaths: ['/home/test/project', '/home/test/project/one'],
      affectedDirs: ['/home/test/project/one'],
    };
    const first = api.syncFileExplorerRootToPlanForTest(firstPlan, '1');
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), ['/home/test/project', '/home/test/project/one'], 'the cached expansion is visible before the returned promise is awaited');
    assert.equal(fetchCalls, 0, 'the warm switch hot path issues no blocking request');
    await first;
    const projectRow = Array.from(api.fileExplorerTreeForTest().children).find(row => row.dataset?.path === '/home/test/project');
    assert.ok(projectRow, 'the cached root is materialized immediately');

    const secondPlan = {
      session: '2',
      root: '/home/test',
      expandPaths: ['/home/test/project', '/home/test/project/two'],
      affectedDirs: ['/home/test/project/two'],
    };
    const second = api.syncFileExplorerRootToPlanForTest(secondPlan, '2');
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), ['/home/test/project', '/home/test/project/two'], 'the next session expansion replaces the first session expansion synchronously');
    assert.equal(fetchCalls, 0, 'switching back and forth remains cache-only until background revalidation');
    assert.equal(Array.from(api.fileExplorerTreeForTest().children).find(row => row.dataset?.path === '/home/test/project'), projectRow, 'same-root session switches reconcile and retain the existing directory row node');
    await second;
    assert.ok(api.fileExplorerFsResourceKeysForTest().length <= api.fileExplorerMemoryCacheLimitForTest, 'the reused listing cache remains under the shared LRU bound');
  });

  await testAsync('Finder Sync revalidates a warm tree after its cache-first frame without collapsing it', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerDirListingForTest('/repo', [{name: 'old.txt', kind: 'file'}]);
    const requests = [];
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/fast/list?'));
      const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
      requests.push(path);
      return Promise.resolve(jsonResponse({path, entries: [{name: 'new.txt', kind: 'file'}]}));
    });
    const plan = {session: '1', root: '/repo', expandPaths: [], affectedDirs: ['/repo']};
    const sync = api.syncFileExplorerRootToPlanForTest(plan, '1');
    assert.equal(requests.length, 0, 'the cache-first render performs no request before its frame');
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/old.txt"]'), 'the cached row is visible synchronously');
    await sync;
    assert.equal(frames.length, 1, 'one deferred revalidation frame is scheduled');
    frames.shift()();
    await flushAsyncWork();
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(requests.length, 1, 'background listing settles in one direct request after the frame');
    assert.deepStrictEqual([...new Set(requests)], ['/repo'], 'revalidation is scoped to the visible cached directory');
    assert.equal(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/old.txt"]'), null, 'a changed cached row is removed in place after revalidation');
    assert.ok(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/new.txt"]'), 'the changed directory appears after revalidation');
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'background freshness does not collapse or invent disclosure state');
  });

  await testAsync('Finder Sync unchanged revalidation preserves the mounted row identity', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerDirListingForTest('/repo', [{name: 'same.txt', kind: 'file'}]);
    api.setFetchForTest(url => {
      const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
      return Promise.resolve(jsonResponse({path, entries: [{name: 'same.txt', kind: 'file'}]}));
    });
    const plan = {session: '1', root: '/repo', expandPaths: [], affectedDirs: ['/repo']};
    await api.syncFileExplorerRootToPlanForTest(plan, '1');
    const row = api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/same.txt"]');
    assert.ok(row, 'the cache-first row is mounted before revalidation');
    frames.shift()();
    await flushAsyncWork();
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(api.fileExplorerTreeForTest().querySelector('.file-tree-row[data-path="/repo/same.txt"]'), row, 'an unchanged signature skips reconciliation and preserves DOM identity');
  });

  await testAsync('Finder Sync warm revalidation cannot resurrect a directory retired by a newer parent classification', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) {
        frames.push(callback);
        return frames.length;
      },
    });
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerDirListingForTest('/repo', [{name: 'gone', kind: 'dir'}]);
    api.setFileExplorerDirListingForTest('/repo/gone', [{name: 'cached.txt', kind: 'file'}]);
    const heldChild = deferredFetch();
    let rootRequests = 0;
    const requests = [];
    api.setFetchForTest(url => {
      const parsed = new URL(String(url), 'https://yolomux.test');
      const path = parsed.searchParams.get('path');
      requests.push(`${parsed.pathname}:${path || ''}`);
      if (parsed.pathname === '/api/fs/batch') return Promise.resolve(jsonResponse({responses: []}));
      if (parsed.pathname === '/api/fs/info') return Promise.resolve(jsonResponse({path, kind: 'dir'}));
      if (path === '/repo') {
        if (parsed.pathname === '/api/fs/fast/list') rootRequests += 1;
        const kind = rootRequests === 1 ? 'dir' : 'file';
        return Promise.resolve(jsonResponse({path, entries: [{name: 'gone', kind}]}));
      }
      if (path === '/repo/gone') return heldChild.promise;
      return Promise.reject(new Error(`unexpected listing ${path}`));
    });
    const plan = {session: '1', root: '/repo', expandPaths: ['/repo/gone'], affectedDirs: ['/repo/gone']};
    await api.syncFileExplorerRootToPlanForTest(plan, '1');
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), ['/repo/gone'], 'the warm cache paints the directory before revalidation');
    assert.equal(frames.length, 1, 'the warm cache schedules one deferred revalidation frame');
    const revalidationGeneration = api.fileExplorerSyncStateForTest().generation;
    frames.shift()();
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(rootRequests, 1, `the revalidation confirms the parent before requesting the held child (observed ${rootRequests}; ${requests.join(', ')})`);

    await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'the newer file classification retires warm disclosure state');
    const cancelledState = api.fileExplorerSyncStateForTest();
    assert.ok(cancelledState.generation > revalidationGeneration, 'retirement generation-fences the deferred warm revalidation');
    assert.equal(cancelledState.inFlightSignature, '', 'retirement leaves no warm sync transaction owner');

    heldChild.resolve(jsonResponse({path: '/repo/gone', entries: [{name: 'stale.txt', kind: 'file'}]}));
    await flushAsyncWork();
    await flushAsyncWork();
    await flushAsyncWork();
    assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'the held child response cannot resurrect the retired warm directory');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncStateForTest()), canonical(cancelledState), 'settling stale warm work cannot reclaim the cancelled generation or signature');
  });

  test('tree-row patch preserves normalized Finder file and Differ directory contracts in place', () => {
    const api = loadYolomux('', ['1']);
    const finder = api.testElementForId('tree-row-finder-contract');
    const file = {name: 'file.md', kind: 'file', size: 12, mtime: 100};
    api.renderTreeChildrenForTest(finder, '/repo', [file]);
    const finderRow = finder.querySelector('.file-tree-row[data-path="/repo/file.md"]');
    const finderContract = api.treeRowContractForTest(finderRow);
    api.renderTreeChildrenForTest(finder, '/repo', [{...file}]);
    assert.strictEqual(finder.querySelector('.file-tree-row[data-path="/repo/file.md"]'), finderRow, 'Finder patches the existing file row');
    assert.deepStrictEqual(canonical(api.treeRowContractForTest(finderRow)), canonical(finderContract), 'Finder file DOM/dataset/classes/ARIA/column order are stable');

    const differ = api.testElementForId('tree-row-differ-contract');
    const directory = {name: 'src', kind: 'dir', mtime: 200};
    api.renderTreeChildrenForTest(differ, '/repo', [directory], 0, [['/repo/src', []]], {differMode: true, repoForDiffer: '/repo'});
    const differRow = differ.querySelector('.file-tree-row[data-path="/repo/src"]');
    const differContract = api.treeRowContractForTest(differRow);
    api.renderTreeChildrenForTest(differ, '/repo', [{...directory}], 0, [['/repo/src', []]], {differMode: true, repoForDiffer: '/repo'});
    assert.strictEqual(differ.querySelector('.file-tree-row[data-path="/repo/src"]'), differRow, 'Differ patches the existing directory row');
    assert.deepStrictEqual(canonical(api.treeRowContractForTest(differRow)), canonical(differContract), 'Differ directory DOM/dataset/classes/ARIA/column order are stable');
  });

  await testAsync('Finder Sync cold descendant listings use eight bounded direct workers and share the LRU bound', async () => {
    const api = loadYolomux('', ['1']);
    const directories = Array.from({length: 12}, (_value, index) => `/cold/${index}`);
    const pending = [];
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/fs/fast/list?'));
      const request = {...deferredFetch(), path: new URL(String(url), 'https://yolomux.test').searchParams.get('path')};
      pending.push(request);
      return request.promise;
    });
    const listingPromise = api.fetchFileExplorerSyncListingsForTest(directories, {force: true});
    await flushAsyncWork();
    assert.equal(pending.length, 8, 'the bounded owner starts eight one-level GETs before awaiting a response');
    for (const request of pending.slice(0, 8)) request.resolve(jsonResponse({path: request.path, entries: []}));
    await flushAsyncWork();
    assert.equal(pending.length, 12, 'the remaining four start only after capacity becomes available');
    for (const request of pending.slice(8)) request.resolve(jsonResponse({path: request.path, entries: []}));
    const listings = await listingPromise;
    assert.equal(listings.size, directories.length, 'every cold directory settles');
    assert.deepStrictEqual(pending.map(request => request.path), directories, 'the direct one-level requests preserve breadth-first queue order');

    const limit = api.fileExplorerMemoryCacheLimitForTest;
    for (let index = 0; index < limit + 5; index += 1) {
      api.setFileExplorerDirListingForTest(`/lru/${index}`, []);
    }
    const keys = api.fileExplorerFsResourceKeysForTest();
    assert.equal(keys.length, limit, 'the shared filesystem-resource LRU remains strictly bounded');
    assert.equal(keys.some(key => key.endsWith('/lru/0')), false, 'the oldest listing is evicted');
    assert.equal(keys.some(key => key.endsWith(`/lru/${limit + 4}`)), true, 'the newest listing remains cached');
  });

  test('Finder Sync cold and stale paths share bounded parallel fetch and deferred revalidation owners', () => {
    const source = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
    const syncOwner = source.slice(source.indexOf('async function syncFileExplorerRootToPlan('), source.indexOf('async function syncFileExplorerToActiveTab('));
    assert.ok(/function fetchFileExplorerSyncListings[\s\S]*workerCount = Math\.min\(8, queue\.length\)[\s\S]*Promise\.all\(Array\.from/.test(source), 'cold directory listings use one bounded parallel worker owner');
    assert.ok(/function scheduleFileExplorerSyncRevalidation[\s\S]*requestAnimationFrame[\s\S]*fresh: true, force: true/.test(source), 'stale-while-revalidate starts only after the cache-first frame and bypasses background suppression');
    assert.equal(syncOwner.includes('for (const path of expandPaths)'), false, 'the retired sequential per-folder expansion loop cannot return');
    assert.equal(syncOwner.includes('preserveExpanded: false'), false, 'session switches no longer enter the destructive subtree teardown path');
  });

  test('Finder filesystem batches carry bounded caller attribution without path metadata', () => {
    const source = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
    const actionsSource = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
    assert.ok(source.includes('function fileExplorerFsBatchTrigger(options = {})'), 'the shared batch owner, not individual callers, normalizes its trigger enum');
    assert.ok(source.includes('function fileExplorerFsBatchClientMetadata()'), 'batch requests carry an opaque client revision and scope');
    assert.ok(source.includes('watch_token: watchToken.slice(0, 128)'), 'batch product identity follows the existing filesystem watch token so ready bytes retire after invalidation');
    assert.ok(source.includes('trigger_counts: item.triggerCounts') && source.includes('...fileExplorerFsBatchClientMetadata()'), 'each batch item carries bounded trigger counts while the request carries browser scope');
    // Both /api/fs/batch bounds are the server's, and it states them in the boot payload. A literal
    // here would be a copy free to drift from filesystem.MAX_BATCH_REQUESTS, which is what let the
    // flush post a body the server refuses.
    assert.ok(source.includes("const fileExplorerFsBatchLimits = (typeof bootstrap === 'object' && bootstrap?.filesystemBatchLimits) || {};"), 'the batch bounds are read from the boot payload the server writes');
    assert.ok(source.includes('const fileExplorerFsBatchRequestLimit = fileExplorerServerStatedLimit(fileExplorerFsBatchLimits.maxRequests, 1);'), 'the flush splits at the request bound the server states');
    assert.ok(source.includes('const fileExplorerFsBatchTriggerCountLimit = fileExplorerServerStatedLimit(fileExplorerFsBatchLimits.triggerCountLimit, 1);'), 'coalesced trigger counts are capped at the ceiling the server states');
    assert.equal(/(?:fileExplorerFsBatchRequestLimit|fileExplorerFsBatchTriggerCountLimit)\s*=\s*\d/.test(source), false, 'neither batch bound may be a literal in the bundle');
    assert.ok(/catch \(error\)[\s\S]{0,260}trigger: 'watch-diff-fallback'/.test(source), 'watch-diff failure repairs remain attributable');
    assert.ok(source.includes("trigger: 'deferred-interaction'"), 'the deferred interaction repair is distinguishable from the watch fallback');
    assert.ok(actionsSource.includes('async function refreshFileExplorerIfChanged(options = {})') && actionsSource.includes('trigger: options.trigger'), 'the fallback owner forwards its trigger to the shared batch request');
  });

  await testAsync('a mass deferred INFO enrichment is split at the bound the server states instead of posted whole and refused', async () => {
    // Deferred repo enrichment can inspect every visible directory at once. The flush once drained
    // the whole queue into ONE body, and the server refuses a
    // body above filesystem.MAX_BATCH_REQUESTS with a 400 invalid_request, so the entire operation
    // failed rather than being split. The stub refuses exactly the way the server does.
    const api = loadYolomux();
    const bodies = [];
    api.setFetchForTest((url, options = {}) => {
      assert.equal(String(url), '/api/fs/batch');
      const requests = JSON.parse(options.body || '{}').requests || [];
      bodies.push(requests);
      if (requests.length > 64) {
        return Promise.resolve(jsonResponse({
          state: 'failed',
          request: {id: 'r-too-many'},
          error: {code: 'invalid_request', message: {key: 'request.error.tooManyItems', fallback: 'too many items', params: {field: 'requests', max: 64}}},
        }, 400));
      }
      return Promise.resolve(jsonResponse({
        responses: requests.map(request => ({
          id: request.id,
          ok: true,
          status: 200,
          payload: {path: request.path, kind: 'dir', marker: String(request.id)},
        })),
      }));
    });

    const paths = Array.from({length: 130}, (_, index) => `/home/test/mass/${index}`);
    const infos = paths.map(path => api.fetchFilePathInfoForTest(path, {fresh: true}));
    const flush = await api.flushFileExplorerFsBatchForTest();
    assert.equal(flush.ok, true, 'a 130-path re-list succeeds instead of being refused');
    assert.deepStrictEqual(bodies.map(requests => requests.length), [64, 64, 2], 'the queue is split into consecutive slices no larger than the stated bound');
    assert.equal(bodies.some(requests => requests.length > 64), false, 'no body reaches the server above the bound it refuses');
    assert.deepStrictEqual(bodies.flat().map(request => request.path), paths, 'chunking preserves queue order across chunk boundaries');
    const entries = await Promise.all(infos);
    assert.deepStrictEqual(
      entries.map(entry => entry.marker),
      bodies.flat().map(request => String(request.id)),
      'every queued path still gets its own per-item result',
    );
  });

  await testAsync('a failing filesystem batch chunk settles only its own items and never discards its siblings', async () => {
    const api = loadYolomux('', ['1', '2', '3', '4', '5', '6'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {filesystemBatchLimits: {maxRequests: 2, triggerCountLimit: 64}},
    });
    const posts = [];
    const singles = [];
    api.setFetchForTest((url, options = {}) => {
      const text = String(url);
      if (text.startsWith('/api/fs/batch')) {
        const requests = JSON.parse(options.body || '{}').requests || [];
        posts.push(requests.map(request => request.path));
        // Only the FIRST chunk fails, at the transport, which is the case that used to be able to
        // take a whole flush with it.
        if (posts.length === 1) return Promise.reject(new Error('chunk transport failed'));
        return Promise.resolve(jsonResponse({
          responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, kind: 'dir', marker: 'ok'}})),
        }));
      }
      // A failed batch chunk must not fan out into one request per item. Any non-batch request
      // reaches this branch so the assertion below catches the retired fallback directly.
      singles.push(text);
      return Promise.reject(new Error('unexpected single-item fallback'));
    });

    const paths = ['/home/test/chunk/a', '/home/test/chunk/b', '/home/test/chunk/c', '/home/test/chunk/d', '/home/test/chunk/e'];
    const infos = paths.map(path => api.fetchFilePathInfoForTest(path, {fresh: true}));
    const entriesPromise = Promise.allSettled(infos);
    const flush = await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(posts, [
      ['/home/test/chunk/a', '/home/test/chunk/b'],
      ['/home/test/chunk/c', '/home/test/chunk/d'],
      ['/home/test/chunk/e'],
    ], 'the chunks after the failed one are still posted, in queue order');
    assert.equal(flush.chunks, 3, 'the stated bound of 2 splits five queued paths into three chunks');
    assert.equal(flush.ok, false, 'the flush reports the failed chunk rather than hiding it');
    assert.equal(singles.length, 0, 'the failed chunk never falls back to per-item requests');
    const entries = await entriesPromise;
    assert.deepStrictEqual(
      entries.map(entry => entry.status === 'fulfilled' ? (entry.value?.marker || null) : null),
      [null, null, 'ok', 'ok', 'ok'],
      'the failed chunk surfaces its own error while every sibling item still gets its result',
    );
  });

  await testAsync('a boot payload that states no filesystem batch bound posts one item per request', async () => {
    // Fail closed: a server that did not state a bound is one this bundle cannot promise a bounded
    // body to, so it sends the only size no server can refuse for being too large.
    const api = loadYolomux('', ['1', '2', '3', '4', '5', '6'], 'http:', 'Linux x86_64', 'admin', {
      bootstrapOverrides: {filesystemBatchLimits: null},
    });
    const bodies = [];
    api.setFetchForTest((url, options = {}) => {
      assert.equal(String(url), '/api/fs/batch');
      const requests = JSON.parse(options.body || '{}').requests || [];
      bodies.push(requests.map(request => request.path));
      return Promise.resolve(jsonResponse({
        responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, kind: 'dir', marker: 'ok'}})),
      }));
    });

    const paths = ['/home/test/unstated/a', '/home/test/unstated/b', '/home/test/unstated/c'];
    const infos = paths.map(path => api.fetchFilePathInfoForTest(path, {fresh: true}));
    await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(bodies, [[paths[0]], [paths[1]], [paths[2]]], 'an unstated bound sends one item per request rather than a remembered 64');
    const entries = await Promise.all(infos);
    assert.deepStrictEqual(entries.map(entry => entry.marker), ['ok', 'ok', 'ok'], 'every item still settles');
  });

  test('compact watchd push revisions cannot overwrite the watch-diff cursor', () => {
    const bootstrap = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    const source = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
    const owner = source.slice(source.indexOf('async function refreshFileExplorerFromPush('), source.indexOf('function fileExplorerWatchPayloadEntries('));
    assert.ok(bootstrap.includes("let fileExplorerFilesystemWatchToken = '';\nlet fileExplorerFilesystemPushToken = '';"), 'push revisions and watch-diff cursors have separate state owners');
    assert.ok(owner.includes('if (fileExplorerFilesystemPushToken === nextToken) return;'), 'a repeated watchd push revision cannot start another watch-diff operation');
    const cursorAssignments = owner.match(/fileExplorerFilesystemWatchToken = nextToken/g) || [];
    assert.equal(cursorAssignments.length, 3, 'only the two watch-diff response paths and one authoritative full-SSE path may advance the watch-diff cursor');
    assert.equal(owner.match(/options\.fromWatchDiff === true && nextToken/g)?.length, 2, 'both watch-diff cursor assignments are guarded by response provenance');
    assert.ok(owner.includes("payload?.mode === 'full' && nextToken"), 'the remaining cursor assignment accepts only an authoritative full SSE keyframe');
    assert.ok(
      owner.indexOf("payload?.mode === 'full' && nextToken") < owner.indexOf('fileExplorerFilesystemPushToken === nextToken'),
      'an authoritative full keyframe advances the watch cursor before push-render dedupe',
    );
  });

  await testAsync('Tabber activity cache treats direct snapshots as newer than in-flight HTTP work', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFileExplorerModeForTest('tabber');
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/activity?'));
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
    });

    const request = api.fetchTabberActivityForTest({visible: true});
    assert.equal(api.tabberActivityStateForTest().requestGeneration, 1, 'the HTTP request owns the first generation');
    assert.equal(api.applyTabberActivityPayloadForTest({marker: 'push', activity: {}, agents: []}), true, 'a direct snapshot applies');
    assert.equal(api.tabberActivityStateForTest().appliedGeneration, 2, 'the direct snapshot advances the shared generation');
    pending[0].resolve(jsonResponse({marker: 'stale-http', activity: {}, agents: []}));
    await request;
    assert.equal(api.tabberActivityPayloadForTest().marker, 'push', 'the older HTTP completion cannot replace the direct snapshot');
    assert.equal(api.tabberActivityStateForTest().request, null, 'identity-safe cleanup releases the completed request handle');

    const failed = api.fetchTabberActivityForTest({visible: true});
    pending[1].reject(new Error('activity offline'));
    await failed;
    const failedState = api.tabberActivityStateForTest();
    assert.equal(failedState.request, null, 'a failed current request releases its handle');
    assert.equal(failedState.loaded, true, 'a failed attempt remains settled so rendering does not create a retry loop');
    assert.equal(api.tabberActivityPayloadForTest().marker, 'push', 'a failed refresh preserves the last good snapshot');
  });

  await testAsync('Tabber SSE completion refreshes only a visible Tabber', async () => {
    const visible = loadYolomux('', ['1']);
    visible.setLayoutSlotsForTest({left: visible.paneStateWithTabs([visible.tabberItemId], visible.tabberItemId)});
    visible.setFileExplorerModeForTest('tabber');
    const visibleCalls = [];
    visible.setFetchForTest(url => {
      visibleCalls.push(String(url));
      return Promise.resolve(jsonResponse({activity: {}, agents: []}));
    });
    visible.handleClientPushEventNowForTest('background_refresh_done', {role: 'tabber-activity'});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(visibleCalls.filter(url => url.startsWith('/api/activity?')).length, 1, 'a completed shared cache generation refreshes the visible Tabber once');

    const hidden = loadYolomux('', ['1']);
    hidden.setLayoutSlotsForTest({left: hidden.paneStateWithTabs(['1'], '1')});
    const hiddenCalls = [];
    hidden.setFetchForTest(url => {
      hiddenCalls.push(String(url));
      return Promise.resolve(jsonResponse({activity: {}, agents: []}));
    });
    hidden.handleClientPushEventNowForTest('background_refresh_done', {role: 'tabber-activity'});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(hiddenCalls.length, 0, 'a hidden Tabber does not fetch for another client\'s completion');
  });

  await testAsync('Quick Open reuses one cached parent listing across path filters', async () => {
    const api = loadYolomux();
    const calls = [];
    api.installCommandPaletteFixtureForTest();
    api.setFetchForTest((url, options = {}) => {
      calls.push({url: String(url), signal: options.signal});
      return Promise.resolve(jsonResponse({path: '/tmp', entries: [
        {name: 'helloworld', kind: 'dir'},
        {name: 'hello-world.txt', kind: 'file'},
        {name: 'unrelated.txt', kind: 'file'},
      ]}));
    });

    api.setCommandPaletteStateForTest('files', '/tmp/hw');
    api.setCommandPaletteQueryForTest('/tmp/hw');
    await api.refreshFileQuickOpenCandidatesForTest('/tmp/hw');
    assert.deepStrictEqual(canonical(api.fileQuickOpenStateForTest().candidates.map(item => item.path)), [
      '/tmp/helloworld', '/tmp/hello-world.txt', '/tmp/unrelated.txt',
    ]);

    api.setCommandPaletteQueryForTest('/tmp/helloworld');
    await api.refreshFileQuickOpenCandidatesForTest('/tmp/helloworld');
    assert.equal(calls.length, 1, 'changing only the path filter reuses the cached parent listing');
    assert.equal(calls[0].url, '/api/fs/fast/list?path=%2Ftmp', 'path mode loads one direct parent listing');
    assert.deepStrictEqual(canonical(api.commandPaletteStateForTest().items.filter(item => item.category === 'file').map(item => item.label)), [
      'Open folder in File Explorer', 'helloworld/', 'hello-world.txt',
    ]);

    api.invalidateFileExplorerRootsForTest(['/tmp']);
    api.setCommandPaletteQueryForTest('/tmp/hw');
    await api.refreshFileQuickOpenCandidatesForTest('/tmp/hw');
    assert.equal(calls.length, 2, 'a filesystem invalidation forces the next path query to reload its parent');
  });

  await testAsync('Quick Open path listing rejects stale completions after a parent change', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.installCommandPaletteFixtureForTest();
    api.setFetchForTest((url, options = {}) => {
      const request = {
        ...deferredFetch(),
        url: String(url),
        signal: options.signal,
      };
      pending.push(request);
      return request.promise;
    });

    const stale = api.refreshFileQuickOpenCandidatesForTest('/tmp-old/old');
    assert.equal(pending[0].url, '/api/fs/fast/list?path=%2Ftmp-old', 'absolute path mode loads its containing directory through the fast listing route');
    api.abortFileQuickOpenSearchForTest();

    const current = api.refreshFileQuickOpenCandidatesForTest('/tmp-new/new');
    assert.equal(pending[1].url, '/api/fs/fast/list?path=%2Ftmp-new', 'a parent change loads the new directory once');
    pending[1].resolve(jsonResponse({path: '/tmp-new', entries: [{name: 'new.txt', kind: 'file'}]}));
    await current;
    pending[0].resolve(jsonResponse({path: '/tmp-old', entries: [{name: 'old.txt', kind: 'file'}]}));
    await stale;
    const state = api.fileQuickOpenStateForTest();
    assert.deepStrictEqual(canonical(state.candidates.map(item => item.path)), ['/tmp-new/new.txt'], 'stale completion after cancel/restart cannot replace current candidates');
    assert.equal(state.loading, false, 'the current request settles loading');
    assert.equal(state.error, '', 'stale completion cannot add an error to the current result');
  });

  test('Quick Open debounce replacement has one timer owner and consumes its handle', () => {
    const timers = [];
    const cleared = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout(callback, delay) {
        const id = timers.length + 1;
        timers.push({id, callback, delay});
        return id;
      },
      clearTimeout(id) { cleared.push(id); },
    });
    api.setCommandPaletteQueryForTest('>command');
    const timerCountBefore = timers.length;
    api.scheduleFileQuickOpenSearchForTest();
    const priorTimer = api.fileQuickOpenStateForTest().debounce;
    api.scheduleFileQuickOpenSearchForTest();
    const replacementTimer = api.fileQuickOpenStateForTest().debounce;
    assert.deepStrictEqual(cleared, [priorTimer], 'replacement debounce clears the prior record timer');
    assert.equal(timers.length, timerCountBefore + 2, 'the record schedules only its original and replacement timers');
    assert.notEqual(replacementTimer, priorTimer, 'the record owns only the replacement timer');
    timers.find(item => item.id === replacementTimer).callback();
    assert.equal(api.fileQuickOpenStateForTest().debounce, null, 'running the debounce consumes its record handle');
  });

  await testAsync('YO!agent chat record drains queued asks after action work finishes', async () => {
    const pendingAction = [];
    const calls = [];
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
    api.applyYoagentConversationPayloadForTest({messages: [], pending_waits: []});
    api.setFetchForTest((url, options = {}) => {
      const path = String(url);
      calls.push(path);
      if (path === '/api/yoagent/actions/execute-send') {
        return new Promise(resolve => pendingAction.push(resolve));
      }
      if (path === '/api/yoagent/agents') return Promise.resolve(jsonResponse({agents: [{key: 'codex', available: true}]}));
      if (path === '/api/yoagent/chat') {
        const body = JSON.parse(options.body || '{}');
        return Promise.resolve(jsonResponse({
          answer: 'queued done',
          backend: 'codex',
          backend_used: 'codex',
          conversation: {messages: [{role: 'user', content: body.message}, {role: 'assistant', content: 'queued done'}], pending_waits: []},
        }));
      }
      throw new Error(`unexpected fetch ${path}`);
    });

    const action = api.executeYoagentActionSendForTest('preview-1');
    assert.equal(api.yoagentChatStateForTest().busy, true, 'action work marks the shared chat record busy');
    await api.sendYoagentChatMessageForTest('after action');
    assert.deepStrictEqual(canonical(api.yoagentChatQueueForTest().map(item => item.text)), ['after action'], 'a prompt submitted during action work enters the shared queue');
    pendingAction[0](jsonResponse({session: '1', transport: 'tmux', conversation: {messages: [], pending_waits: []}}));
    await action;
    await flushAsyncWork();
    await flushAsyncWork();
    assert.equal(calls.filter(path => path === '/api/yoagent/chat').length, 1, 'finishing action work drains the queued ask through the normal chat path');
    assert.equal(api.yoagentChatQueueForTest().length, 0, 'the drained queue is empty');
    assert.equal(api.yoagentChatStateForTest().busy, false, 'the shared busy state settles after the queued ask');
    assert.equal(api.yoagentChatStateForTest().activeRequest, null, 'the active request is cleared with the busy state');
  });

  await testAsync('YO!agent startup record freezes its disabled snapshot and keeps prewarm one-shot', async () => {
    const pending = [];
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
    api.setActivitySummaryPayloadForTest({global: {headline: 'old activity'}, sessions: {}, session_order: []});
    assert.equal(api.showYoagentStartupInfoOnceForTest(), true, 'startup info shows once');
    assert.equal(api.applyActivitySummaryPayloadFromPushForTest({global: {headline: 'new activity'}, sessions: {}, session_order: []}), false, 'disabled activity pushes are rejected');
    assert.equal(api.yoagentStartupStateForTest().activityPayload.global.headline, 'old activity', 'later activity pushes do not mutate the frozen startup snapshot');
    api.hideYoagentStartupInfoForTest();
    assert.equal(api.showYoagentStartupInfoOnceForTest(), false, 'hiding does not reset the one-shot');
    assert.equal(api.yoagentStartupStateForTest().infoVisible, false, 'the hidden startup block stays hidden');
    assert.equal(api.showYoagentStartupInfoForLatestActivityForTest(), true, 'explicit latest-activity refresh resets the one-shot');
    assert.equal(api.yoagentStartupStateForTest().activityPayload.global.headline, 'old activity', 'explicit refresh cannot capture a rejected activity push');

    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/yoagent/prewarm');
      return new Promise(resolve => pending.push(resolve));
    });
    const first = api.prewarmYoagentForTest();
    const duplicate = api.prewarmYoagentForTest();
    await duplicate;
    assert.equal(pending.length, 1, 'duplicate prewarm calls share the startup one-shot');
    let startup = api.yoagentStartupStateForTest();
    assert.equal(startup.prewarmStarted, true, 'the startup record remembers that prewarm began');
    assert.equal(startup.prewarming, true, 'the startup record owns the visible prewarm state');
    assert.equal(startup.llmRequested, true, 'the first empty startup requests one visible answer');
    api.applyYoagentStreamPayloadForTest({stream_id: 'startup', done: true});
    assert.equal(api.yoagentStartupStateForTest().prewarming, false, 'stream completion settles the same prewarm state');
    pending[0](jsonResponse({conversation: {messages: [], pending_waits: []}}));
    await first;
    startup = api.yoagentStartupStateForTest();
    assert.equal(startup.prewarming, false, 'prewarm completion remains settled');
    assert.equal(startup.prewarmStarted, true, 'completion preserves one-shot ownership until Clear explicitly resets it');
  });

  test('YO!agent timeline orders the activity snapshot with persisted answers by timestamp', () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
    api.setActivitySummaryPayloadForTest({
      generated_at: '2026-07-10T02:02:38Z',
      global: {headline: 'activity snapshot'},
      sessions: {},
      session_order: [],
    });
    api.showYoagentStartupInfoOnceForTest();
    api.setYoagentMessagesForTest([
      {role: 'assistant', content: 'older answer', createdAt: '2026-07-10T01:50:00Z'},
      {role: 'assistant', content: 'latest answer', createdAt: '2026-07-10T02:11:52Z'},
    ]);

    const html = api.yoagentChatHtml();
    assert.ok(html.indexOf('older answer') < html.indexOf('activity snapshot'), 'older persisted answers remain before the activity snapshot');
    assert.ok(html.indexOf('activity snapshot') < html.indexOf('latest answer'), 'a newer persisted answer renders after the older activity snapshot');
  });

  test('UI transaction records keep retired parallel globals absent', () => {
    const source = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    for (const name of [
      'fileExplorerSyncPathInFlight', 'fileExplorerLastAppliedSyncPlanKey', 'fileExplorerSyncGeneration',
      'tabberActivityRequestGeneration', 'tabberActivityAppliedRequestGeneration', 'tabberActivityLoaded', 'tabberActivityFetchPromise',
      'fileQuickOpenRoot', 'fileQuickOpenCandidates', 'fileQuickOpenLoading', 'fileQuickOpenError', 'fileQuickOpenRequestId', 'fileQuickOpenDebounce', 'fileQuickOpenAbortController',
      'yoagentBusy', 'yoagentActiveChatRequest', 'yoagentChatQueue', 'yoagentChatQueueSerial', 'yoagentError', 'yoagentDraft', 'yoagentHistoryCursor', 'yoagentHistoryDraft', 'yoagentNotice',
      'yoagentStartupActivitySummaryPayload', 'yoagentPrewarming', 'yoagentPrewarmStarted', 'yoagentStartupLlmRequested', 'yoagentStartupInfoShown', 'yoagentStartupInfoVisible',
    ]) assert.equal(source.includes(name), false, `${name} remains retired`);
    for (const owner of ['fileExplorerSyncState', 'tabberActivityState', 'fileQuickOpenState', 'yoagentChatState', 'yoagentStartupState']) {
      assert.ok(source.includes(`const ${owner} = {`), `${owner} is the one owner`);
    }
  });

  test('layout URL record consumes pending state once and owns one refresh timer', () => {
    const timers = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      setTimeout(callback, delay) {
        const id = timers.length + 1;
        timers.push({id, callback, delay});
        return id;
      },
    });
    assert.equal(api.applyLayoutUrlStateSeedForTest({preferences: {searchText: 'first'}}), true, 'a deep-link seed enters the record');
    assert.equal(api.layoutUrlStateForTest().applied, false, 'a replacement seed is pending');
    assert.equal(api.applyPendingLayoutUrlStateForTest(), true, 'the pending state applies once');
    assert.equal(api.applyPendingLayoutUrlStateForTest(), false, 'the same state cannot replay');
    assert.equal(api.layoutUrlStateForTest().applied, true, 'the consumed marker advances with the pending state');

    const timerCountBefore = timers.length;
    api.scheduleLayoutUrlStateRefreshForTest();
    const refreshTimer = api.layoutUrlStateForTest().refreshTimer;
    api.scheduleLayoutUrlStateRefreshForTest();
    assert.equal(timers.length, timerCountBefore + 1, 'duplicate URL refresh schedules share one record timer');
    assert.equal(api.layoutUrlStateForTest().refreshTimer, refreshTimer, 'the record owns the scheduled timer');
    timers.find(item => item.id === refreshTimer).callback();
    assert.equal(api.layoutUrlStateForTest().refreshTimer, null, 'firing consumes only the matching record timer');
  });

  test('editor field application normalizes URL restore fields and delegates per-file modes', () => {
    const editor = {
      globalThemeMode: 'light',
      terminalThemeMode: 'light',
      themeMode: 'github-light',
      previewDisplayMode: 'vanilla',
      wrapEnabled: true,
      lineNumbersEnabled: false,
      blameEnabled: true,
      diffExpandUnchanged: true,
      previewFontSize: 27,
      modes: [{path: '/tmp/ignored.txt', mode: 'edit'}],
    };
    const api = loadYolomux('', ['1']);
    const urlModes = [];
    api.applyEditorStateFieldsForTest(editor, {applyModeEntry: entry => urlModes.push(entry)});
    const {modes, ...expectedFields} = editor;
    assert.deepEqual(canonical(api.editorStateFieldsSnapshotForTest()), canonical(expectedFields), 'URL restore applies every common editor field through one normalizer');
    assert.deepEqual(canonical(urlModes), canonical(modes), 'the editor field normalizer delegates each per-file mode to its transport-specific owner');
  });

  await testAsync('session-files peer responses preserve each destination request owner in both completion orders', async () => {
    for (const firstDestination of ['finder', 'differ']) {
      const secondDestination = firstDestination === 'finder' ? 'differ' : 'finder';
      const pending = [];
      const api = loadYolomux('', ['1']);
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
      slots.left = api.paneStateWithTabs([api.finderItemId, api.differItemId], api.differItemId);
      slots.right = api.paneStateWithTabs(['1'], '1');
      api.setLayoutSlotsForTest(slots);
      api.setFileExplorerModeForTest('diff');
      api.setFileExplorerChangesSelectedSessionForTest('1');
      api.setFileExplorerFinderSelectedSessionForTest('1');
      api.setFetchForTest((url, options = {}) => {
        assert.ok(String(url).startsWith('/api/session-files?'));
        const request = deferredFetch();
        pending.push({...request, signal: options.signal || null});
        return request.promise;
      });

      const requests = {
        finder: api.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true}),
        differ: api.fetchSessionFilesForTest({destination: 'differ', session: '1', silent: true, force: true}),
      };
      assert.equal(pending.length, 2, 'Finder and Differ own distinct transports for the matching request');
      const requestForDestination = {finder: pending[0], differ: pending[1]};
      requestForDestination[firstDestination].resolve(jsonResponse({
        session: '1',
        loaded: true,
        repos: [{repo: `/${firstDestination}-first`}],
        files: [],
        errors: [],
        from_ref: 'HEAD',
        to_ref: 'current',
      }));
      await requests[firstDestination];
      const secondState = secondDestination === 'finder'
        ? api.fileExplorerFinderSessionFilesStateForTest()
        : api.fileExplorerSessionFilesStateForTest();
      assert.equal(requestForDestination[secondDestination].signal?.aborted, false, `${firstDestination} completion cannot abort ${secondDestination}`);
      assert.equal(secondState.loading, true, `${secondDestination} keeps its active generation while the peer result is available`);

      requestForDestination[secondDestination].resolve(jsonResponse({
        session: '1',
        loaded: true,
        repos: [{repo: `/${secondDestination}-newer`}],
        files: [],
        errors: [],
        from_ref: 'HEAD',
        to_ref: 'current',
      }));
      await requests[secondDestination];
      const finderState = api.fileExplorerFinderSessionFilesStateForTest();
      const differState = api.fileExplorerSessionFilesStateForTest();
      assert.equal(finderState.payload.repos[0].repo, `/${secondDestination}-newer`, 'the later owner result refreshes Finder after both requests settle');
      assert.equal(differState.payload.repos[0].repo, `/${secondDestination}-newer`, 'the later owner result refreshes Differ after both requests settle');
      assert.equal(finderState.loading, false);
      assert.equal(differState.loading, false);
      assert.equal(api.jsDebugFailureEventsForTest().length, 0, 'peer completion order emits no diagnostic failure');
    }
  });

  await testAsync('session-files background completion applies its ready payload without another accepted request', async () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId, api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({session: '1', loaded: true, repos: [], files: [], errors: []}));
    });
    const request = {session: '1', hours: 24, from_ref: '', to_ref: '', repo_refs: {}};
    const staleData = {session: '1', loaded: true, repos: [{repo: '/stale'}], files: [], errors: [], from_ref: '', to_ref: ''};
    const freshData = {session: '1', loaded: true, repos: [{repo: '/ready'}], files: [], errors: [], from_ref: '', to_ref: ''};

    api.applySessionFilesPayloadFromPushForTest(staleData, request);
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/stale', 'the stale HTTP generation is initially visible in Finder');
    assert.equal(api.fileExplorerSessionFilesStateForTest().payload.repos[0].repo, '/stale', 'the stale HTTP generation is initially visible in Differ');
    api.handleClientPushEventNowForTest('session_files_ready', {request, data: freshData});
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/ready', 'the ready push applies to Finder');
    assert.equal(api.fileExplorerSessionFilesStateForTest().payload.repos[0].repo, '/ready', 'the ready push applies to Differ');
    const cacheView = 'a'.repeat(64);
    const requestDescriptor = '2fc357e15260b25bb94dbee3151934bb8e25b2a7beadd9192701f9070013c88e';
    api.handleClientPushEventNowForTest('background_refresh_done', {role: 'session-files', session: '1', cache_key_hash: 'ready-generation', cache_view_id: cacheView, request_descriptor: requestDescriptor});
    await flushAsyncWork();
    assert.deepStrictEqual(requests, [`/api/session-files?from=HEAD&to=current&session=1&hours=24&cache_only=1&cache_view=${cacheView}`], 'the redacted completion may revalidate the canonical cache once, but cannot start another accepted session-files request');
    api.handleClientPushEventNowForTest('background_refresh_done', {role: 'session-files', session: '1', cache_key_hash: 'ready-generation', cache_view_id: cacheView, request_descriptor: requestDescriptor});
    api.handleClientPushEventNowForTest('background_refresh_done', {role: 'session-files', session: 'other-session', cache_key_hash: 'other-generation', cache_view_id: 'b'.repeat(64), request_descriptor: requestDescriptor});
    api.handleClientPushEventNowForTest('background_refresh_done', {role: 'session-files', session: '1', cache_key_hash: 'wrong-descriptor', cache_view_id: 'c'.repeat(64), request_descriptor: 'd'.repeat(64)});
    await flushAsyncWork();
    assert.deepStrictEqual(requests, [`/api/session-files?from=HEAD&to=current&session=1&hours=24&cache_only=1&cache_view=${cacheView}`], 'an exact replay and a wrong-session completion perform zero cache-view reads');
  });

  await testAsync('a cache-only pending response consumes its session-files completion once', async () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    api.applySessionFilesPayloadFromPushForTest({session: '1', loaded: true, repos: [{repo: '/preserved'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'}, {session: '1', from_ref: 'HEAD', to_ref: 'current'});
    const requests = [];
    api.setFetchForTest(url => {
      requests.push(String(url));
      return Promise.resolve(jsonResponse({session: '1', state: 'queued', status: 'pending', retry_after_seconds: 1}, 202));
    });
    const cacheView = 'c'.repeat(64);
    const completion = {role: 'session-files', session: '1', cache_key_hash: 'pending-generation', cache_view_id: cacheView, request_descriptor: '2fc357e15260b25bb94dbee3151934bb8e25b2a7beadd9192701f9070013c88e'};
    api.handleClientPushEventNowForTest('background_refresh_done', completion);
    await new Promise(resolve => setImmediate(resolve));
    api.handleClientPushEventNowForTest('background_refresh_done', completion);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.length, 1, 'a bounded cache-only 202 consumes its completion so an EventSource replay cannot create a disk-read loop');
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/preserved', 'a cache miss preserves the current browser state');
  });

  await testAsync('a newer session-files completion queues behind its in-flight older cache view', async () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    const requestDescriptor = 'e'.repeat(64); // Server-issued descriptor for a canonicalized repo-ref spelling.
    api.applySessionFilesPayloadFromPushForTest({session: '1', loaded: true, repos: [{repo: '/base'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current', cache: {request_descriptor: requestDescriptor}}, {session: '1', from_ref: 'HEAD', to_ref: 'current'});
    const pending = [];
    api.setFetchForTest(url => new Promise(resolve => pending.push({url: String(url), resolve})));
    api.handleClientPushEventNowForTest('background_refresh_done', {role: 'session-files', session: '1', cache_key_hash: 'older', cache_view_id: 'a'.repeat(64), request_descriptor: requestDescriptor});
    await flushAsyncWork();
    assert.equal(pending.length, 1, 'the older completion opens its one cache-only view');
    api.handleClientPushEventNowForTest('background_refresh_done', {role: 'session-files', session: '1', cache_key_hash: 'newer', cache_view_id: 'b'.repeat(64), request_descriptor: requestDescriptor});
    await flushAsyncWork();
    assert.equal(pending.length, 1, 'the newer completion is sequenced instead of aborting the older cache-only read');
    pending[0].resolve(jsonResponse({session: '1', loaded: true, repos: [{repo: '/older'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'}));
    await flushAsyncWork();
    assert.equal(pending.length, 2, 'the queued newer cache view starts only after the older request settles');
    pending[1].resolve(jsonResponse({session: '1', loaded: true, repos: [{repo: '/newer'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'}));
    await flushAsyncWork();
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/newer', 'the newer generation owns the final rendered payload');
  });

  await testAsync('a completion waits for an ordinary session-files read before applying its cache view', async () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    const pending = [];
    api.setFetchForTest(url => new Promise(resolve => pending.push({url: String(url), resolve})));
    const ordinary = api.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true});
    await flushAsyncWork();
    assert.equal(pending.length, 1, 'the ordinary request starts first');
    const descriptor = '2fc357e15260b25bb94dbee3151934bb8e25b2a7beadd9192701f9070013c88e';
    api.handleClientPushEventNowForTest('background_refresh_done', {
      role: 'session-files', session: '1', cache_key_hash: 'newer', cache_view_id: 'd'.repeat(64), request_descriptor: descriptor,
    });
    await flushAsyncWork();
    assert.equal(pending.length, 1, 'the completion remains pending instead of being acknowledged behind the ordinary read');
    pending[0].resolve(jsonResponse({session: '1', loaded: true, repos: [{repo: '/older'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'}));
    await ordinary;
    await flushAsyncWork();
    assert.equal(pending.length, 2, `the completion opens exactly one cache view after the ordinary request settles: ${pending.map(entry => entry.url).join(', ')}`);
    pending[1].resolve(jsonResponse({session: '1', loaded: true, repos: [{repo: '/newer'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'}));
    await flushAsyncWork();
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/newer', 'the completion view replaces the older ordinary response');
  });

  await testAsync('a hidden session-files surface ignores a redacted completion without opening a cache view', async () => {
    const api = loadYolomux('', ['1']);
    let fetches = 0;
    api.setFetchForTest(() => { fetches += 1; return Promise.resolve(jsonResponse({})); });
    api.handleClientPushEventNowForTest('background_refresh_done', {
      role: 'session-files', session: '1', cache_key_hash: 'hidden',
      cache_view_id: 'f'.repeat(64), request_descriptor: 'e'.repeat(64),
    });
    await flushAsyncWork();
    assert.equal(fetches, 0, 'a hidden completion does not open a cache-only request');
  });

  await testAsync('an EventSource reconnect replays one session-files completion without a second cache read', async () => {
    const frames = [];
    const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
      requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
      cancelAnimationFrame() {},
    });
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    const descriptor = '2fc357e15260b25bb94dbee3151934bb8e25b2a7beadd9192701f9070013c88e';
    const calls = [];
    api.setFetchForTest(url => {
      calls.push(String(url));
      return Promise.resolve(jsonResponse({session: '1', loaded: true, repos: [{repo: '/replayed'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'}));
    });
    api.installClientEventStreamForTest();
    const source = api.clientEventTransportStateForTest().source;
    source.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    source.onerror();
    source.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    const completion = {role: 'session-files', session: '1', cache_key_hash: 'reconnect-generation', cache_view_id: '9'.repeat(64), request_descriptor: descriptor};
    const replay = {data: JSON.stringify({type: 'background_refresh_done', payload: completion}), type: 'background_refresh_done', lastEventId: '71'};
    frames.length = 0;
    source.listeners.get('background_refresh_done')[0](replay);
    assert.equal(frames.length, 1, 'the replay reaches the transport-owned frame queue');
    frames.shift()();
    await flushAsyncWork();
    source.listeners.get('background_refresh_done')[0](replay);
    if (frames.length) frames.shift()();
    await flushAsyncWork();
    const cacheViewCalls = calls.filter(url => url.includes('/api/session-files?'));
    assert.equal(cacheViewCalls.length, 1, `the replayed completion consumes one opaque cache view after native reconnect: ${JSON.stringify(calls)}`);
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/replayed');
  });

  await test('watcher session-files receipt waits for its terminal payload instead of painting an empty ready state', () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId, api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    const context = {session: '1', hours: 24, from_ref: 'HEAD', to_ref: 'current', repo_refs: {}};
    const staleData = {session: '1', loaded: true, repos: [{repo: '/stale'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'};
    const readyData = {session: '1', loaded: true, repos: [{repo: '/terminal'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'};
    const receipt = {
      state: 'queued',
      request: {id: 'r-watcher-session-files'},
      operation: {
        id: 'op-watcher-session-files',
        kind: 'session_files',
        cursor: {epoch: 'watcher', seq: 0},
        context,
      },
    };

    api.applySessionFilesPayloadFromPushForTest(staleData, context);
    api.handleClientPushEventNowForTest('session_files_ready', {request: context, status: 202, data: receipt});
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/stale', 'a watcher receipt cannot normalize into an empty Finder payload');
    assert.equal(api.fileExplorerSessionFilesStateForTest().payload.repos[0].repo, '/stale', 'a watcher receipt cannot normalize into an empty Differ payload');

    api.handleClientPushEventNowForTest('operation_terminal', {
      operation: {id: receipt.operation.id, cursor: {epoch: 'watcher', seq: 1}},
      result: {state: 'ready', request: receipt.request, data: readyData, quality: {complete: true, stale: false}, warnings: []},
    });
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/terminal', 'the watcher terminal applies to Finder after its receipt');
    assert.equal(api.fileExplorerSessionFilesStateForTest().payload.repos[0].repo, '/terminal', 'the watcher terminal applies to Differ after its receipt');
  });

  await test('replayed watcher terminal settles when its session-files receipt arrives later', () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.finderItemId, api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFileExplorerFinderSelectedSessionForTest('1');
    const context = {session: '1', hours: 24, from_ref: 'HEAD', to_ref: 'current', repo_refs: {}};
    const receipt = {
      state: 'queued',
      request: {id: 'r-replayed-watcher-session-files'},
      operation: {
        id: 'op-replayed-watcher-session-files',
        kind: 'session_files',
        cursor: {epoch: 'watcher-replay', seq: 0},
        context,
      },
    };
    const readyData = {session: '1', loaded: true, repos: [{repo: '/replayed-terminal'}], files: [], errors: [], from_ref: 'HEAD', to_ref: 'current'};

    api.handleClientPushEventNowForTest('operation_terminal', {
      operation: {id: receipt.operation.id, cursor: {epoch: 'watcher-replay', seq: 1}},
      result: {state: 'ready', request: receipt.request, data: readyData, quality: {complete: true, stale: false}, warnings: []},
    });
    api.handleClientPushEventNowForTest('session_files_ready', {request: context, status: 202, data: receipt});
    assert.equal(api.fileExplorerFinderSessionFilesStateForTest().payload.repos[0].repo, '/replayed-terminal', 'a replayed terminal settles Finder when the watcher receipt registers');
    assert.equal(api.fileExplorerSessionFilesStateForTest().payload.repos[0].repo, '/replayed-terminal', 'a replayed terminal settles Differ when the watcher receipt registers');
  });

  await testAsync('authoritative session-files push fences stale HTTP without aborting its finite transport', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFetchForTest((url, options = {}) => {
      assert.ok(String(url).startsWith('/api/session-files?'));
      const request = deferredFetch();
      pending.push({...request, signal: options.signal || null});
      return request.promise;
    });

    const request = api.fetchSessionFilesForTest({destination: 'differ', session: '1', silent: true, force: true});
    assert.equal(api.fileExplorerSessionFilesStateForTest().loading, true, 'the HTTP generation owns loading');
    assert.equal(api.applySessionFilesPayloadFromPushForTest({
      session: '1',
      loaded: true,
      marker: 'push',
      repos: [{repo: '/push'}],
      files: [],
      errors: [],
      from_ref: 'HEAD',
      to_ref: 'current',
    }, {session: '1', from_ref: 'HEAD', to_ref: 'current'}), true, 'the matching authoritative push applies');
    assert.equal(api.fileExplorerSessionFilesStateForTest().loading, false, 'the push settles visible loading');
    await flushAsyncWork();
    assert.equal(pending[0].signal?.aborted, false, 'the push leaves the already-dispatched finite transport alive');
    assert.equal(api.fixtureLifecycleOperationStateForTest().startupActive, 1, 'the finite transport remains owned until its response settles');
    pending[0].resolve(jsonResponse({
      session: '1',
      loaded: true,
      marker: 'stale-http',
      repos: [{repo: '/stale'}],
      files: [],
      errors: [],
      from_ref: 'HEAD',
      to_ref: 'current',
    }));
    await request;
    const state = api.fileExplorerSessionFilesStateForTest();
    assert.equal(state.payload.repos[0].repo, '/push', 'the stale HTTP completion cannot replace the pushed payload');
    assert.equal(state.loading, false, 'stale finally cannot change the settled push state');
    assert.equal(state.signature, api.sessionFilesPayloadSignatureForPayloadForTest(state.payload), 'payload and signature remain one record snapshot');
    assert.equal(api.fixtureLifecycleOperationStateForTest().startupActive, 0, 'the settled finite transport releases startup capacity');
    const event = api.jsDebugEventsForTest().find(item => item.type === 'api' && item.endpoint === '/api/session-files');
    assert.equal(event?.status, 200, 'the finite HTTP transport settles as a successful response');
    assert.equal(api.jsDebugFailureEventsForTest().length, 0, 'stale HTTP completion emits no diagnostic failure');
  });

  await testAsync('session-files operation failure fences stale HTTP without aborting its finite transport', async () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    const receipt = {
      state: 'queued',
      request: {id: 'r-session-files-failure'},
      operation: {
        id: 'op-session-files-failure',
        kind: 'session_files',
        context: {session: '1', from_ref: 'HEAD', to_ref: 'current'},
        events_url: '/api/client-events?operation_id=op-session-files-failure',
        cursor: {epoch: 'session-files-failure', seq: 0},
      },
    };
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/session-files?'));
      return Promise.resolve(jsonResponse(receipt, 202));
    });
    await api.fetchSessionFilesForTest({destination: 'differ', session: '1', silent: true, force: true});

    const pending = deferredFetch();
    let pendingSignal = null;
    api.setFetchForTest((url, options = {}) => {
      assert.ok(String(url).startsWith('/api/session-files?'));
      pendingSignal = options.signal || null;
      return pending.promise;
    });
    const staleRequest = api.fetchSessionFilesForTest({destination: 'differ', session: '1', silent: true, force: true});
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: receipt.operation.id, cursor: {epoch: 'session-files-failure', seq: 1}},
      result: {state: 'failed', error: {message: {key: 'error.requestFailed', fallback: 'session files failed'}}},
      status: 500,
    }), true, 'the operation failure owns the visible terminal payload');
    assert.equal(pendingSignal?.aborted, false, 'the operation failure leaves the already-dispatched finite transport alive');
    pending.resolve(jsonResponse({
      session: '1',
      loaded: true,
      repos: [{repo: '/stale'}],
      files: [],
      errors: [],
      from_ref: 'HEAD',
      to_ref: 'current',
    }));
    await staleRequest;
    const state = api.fileExplorerSessionFilesStateForTest();
    assert.equal(state.payload.operation_error.message.fallback, 'session files failed', 'the stale HTTP completion cannot replace the terminal failure');
    assert.equal(state.loading, false);
    assert.equal(api.jsDebugFailureEventsForTest().length, 0, 'terminal ownership emits no client diagnostic failure');
  });

  await testAsync('duplicate session-files receipt reuses its retained terminal result', async () => {
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    const receipt = {
      state: 'queued',
      request: {id: 'r-session-files-retained'},
      operation: {
        id: 'op-session-files-retained',
        kind: 'session_files',
        context: {session: '1', from_ref: 'HEAD', to_ref: 'current'},
        events_url: '/api/client-events?operation_id=op-session-files-retained',
        cursor: {epoch: 'session-files-retained', seq: 0},
      },
    };
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/session-files?'));
      return Promise.resolve(jsonResponse(receipt, 202));
    });

    await api.fetchSessionFilesForTest({destination: 'differ', session: '1', silent: true, force: true});
    assert.equal(api.sessionFilesPayloadForTest().refreshing_elsewhere, true, 'the first accepted receipt paints queued state');
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: receipt.operation.id, cursor: {epoch: 'session-files-retained', seq: 1}},
      result: {
        state: 'ready',
        data: {
          session: '1',
          loaded: true,
          repos: [{repo: '/ready'}],
          files: [{repo: '/ready', path: 'DONE.md', abs_path: '/ready/DONE.md'}],
          errors: [],
          from_ref: 'HEAD',
          to_ref: 'current',
        },
      },
      status: 200,
    }), true, 'the accepted operation reaches one terminal result');
    assert.equal(api.sessionFilesPayloadForTest().files[0].path, 'DONE.md', 'the terminal result paints Differ');

    await api.fetchSessionFilesForTest({destination: 'differ', session: '1', silent: true, force: true});
    const state = api.sessionFilesPayloadForTest();
    assert.equal(state.loaded, true, 'a duplicate receipt cannot regress ready state');
    assert.equal(state.refreshing_elsewhere, false, 'a duplicate receipt cannot repaint queued state');
    assert.equal(state.files[0].path, 'DONE.md', 'the retained terminal product is reused');
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, 'terminal feature handling remains exactly once');
  });

  test('command-palette record resets query, cursor, and rendered items together on open', () => {
    const api = loadYolomux('', ['1']);
    api.installCommandPaletteFixtureForTest();
    api.setCommandPaletteStateForTest('command', 'stale query');
    api.invokeCommandPaletteItemForTest({run() {}});
    api.openCommandPaletteForTest({mode: 'command'});
    const state = api.commandPaletteStateForTest();
    assert.equal(state.query, '', 'open clears the prior query');
    assert.equal(state.index, 0, 'open resets the cursor');
    assert.ok(Array.isArray(state.items), 'rendered items stay owned by the same record');
    assert.equal(state.node.hidden, false, 'the record node is the opened palette');
    api.closeCommandPaletteForTest();
    assert.equal(api.commandPaletteStateForTest().node.hidden, true, 'close hides the same record node');
  });

  test('drag record prevents file payload leakage into a later tab drag and clears atomically', () => {
    const api = loadYolomux('', ['1']);
    api.beginFileDragForTest({path: '/tmp/a.txt', paths: ['/tmp/a.txt'], kind: 'file'});
    assert.equal(api.dragStateForTest().filePayload.path, '/tmp/a.txt', 'file drag payload belongs to the shared record');
    const source = tabElement('1', 0, 100);
    const event = dragEvent(10, '1');
    event.currentTarget = source;
    api.startSessionDrag(event, '1', 'left');
    assert.equal(api.dragStateForTest().filePayload, null, 'starting a tab drag clears the prior file payload');
    assert.equal(api.dragStateForTest().item, '1', 'the tab identity is current');
    api.endSessionDrag(event);
    const state = api.dragStateForTest();
    for (const field of ['item', 'sourceSlot', 'paneSlot', 'filePayload', 'customPreview', 'nativePreview', 'tabRectCache']) {
      assert.equal(state[field], null, `${field} clears with the drag operation`);
    }
  });

  test('UI shell and drag records keep retired parallel globals absent', () => {
    const source = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
    for (const name of [
      'layoutUrlStateFromQuery', 'layoutUrlStateApplied', 'layoutUrlStateRefreshTimer',
      'fileExplorerSessionFilesPayload', 'fileExplorerSessionFilesPayloadSignature', 'fileExplorerSessionFilesLoading', 'fileExplorerSessionFilesGuard',
      'commandPaletteNode', 'commandPaletteQuery', 'commandPaletteIndex', 'commandPaletteItemsCache',
      'dragSession', 'dragSourceSlot', 'dragPaneSlot', 'dragFilePayloadState', 'customDragPreview', 'customDragPreviewOffset', 'nativeDragImagePreview', 'transparentDragImage', 'dragTabRectCache',
    ]) assert.equal(source.includes(name), false, `${name} remains retired`);
    for (const owner of ['layoutUrlState', 'fileExplorerSessionFilesState', 'commandPaletteState', 'dragState']) {
      assert.ok(source.includes(`const ${owner} = {`), `${owner} is the one owner`);
    }
  });

    {
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin');
      const transcriptPath = '/home/test/.local/state/yolomux/yoagent/conversation.jsonl';
      api.applyYoagentConversationPayloadForTest({
        transcript_path: transcriptPath,
        transcript_display_path: '~/.local/state/yolomux/yoagent/conversation.jsonl',
        messages: [{role: 'user', content: 'persisted question', createdAt: '2026-06-13T17:39:00Z'}],
      });
      const transcriptHtml = api.yoagentChatHtml();
      assert.ok(transcriptHtml.includes('yoagent-transcript-copy'), 'YO!agent transcript row renders a copy button');
      assert.ok(transcriptHtml.includes(`data-copy-path="${transcriptPath}"`), 'YO!agent transcript copy button carries the transcript path');

      const button = new TestElement('yoagent-transcript-copy', 'button');
      button.className = 'path-copy-button yoagent-transcript-copy';
      button.dataset.copyPath = transcriptPath;
      const clickEvent = {
        target: button,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
      };
      for (const listener of api.documentListenersForTest('click')) listener(clickEvent);
      await flushAsyncWork();
      await flushAsyncWork();

      assert.equal(clickEvent.defaultPrevented, true, 'shared path-copy handler claims the YO!agent copy click');
      assert.equal(api.clipboardTextForTest(), transcriptPath, 'YO!agent transcript copy writes the transcript path');
      assert.ok(api.statusHtmlForTest().includes('copied'), 'YO!agent transcript copy reports success');
    }

    {
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {fireTimeoutDelays: [300]});
      const calls = [];
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body || ''});
        return Promise.resolve(jsonResponse({ok: true}));
      });

      api.syncServerWatchRootsForTest();
      api.syncServerWatchRootsForTest();
      await flushAsyncWork();
      await flushAsyncWork();

      const watchCalls = calls.filter(call => call.url === '/api/watch/roots');
      assert.equal(watchCalls.length, 1, 'adjacent watch-root syncs coalesce into one POST');
      assert.equal(watchCalls[0].method, 'POST', 'watch-root sync still sends the server registration');
    }

    await testAsync('unchanged watch-root descriptors do not retain debounce work', async () => {
      const timers = [];
      const cleared = [];
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout(id) { cleared.push(id); },
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('existing-token');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        return Promise.resolve(jsonResponse({ok: true}));
      });

      await api.syncServerWatchRootsNowForTest({force: true});
      await flushAsyncWork();
      await flushAsyncWork();
      await api.syncServerWatchRootsNowForTest({force: true});
      assert.equal(calls.length, 2, 'the fixture establishes the settled registered descriptor');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'the descriptor registration settles');

      for (let index = 0; index < 20; index += 1) api.syncServerWatchRootsForTest();

      const finalState = api.serverWatchRootsStateForTest();
      assert.equal(finalState.timer, null, `unchanged refreshes retain no debounce timer: ${JSON.stringify({finalState, current: api.clientServerWatchStateForTest()})}`);
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {}, 'unchanged refreshes retain no pending options');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, false, 'fixture quiescence sees no synthetic watch-root work');
      assert.equal(calls.length, 2, 'unchanged refreshes issue no duplicate registration');
      assert.deepStrictEqual(cleared, [], 'no redundant timer needs cancellation when no descriptor change was queued');
    });

    await testAsync('pending watch-root descriptor coalesces without restarting its debounce', async () => {
      let now = 0;
      let nextTimer = 1;
      const timers = new Map();
      const cleared = [];
      const calls = [];
      const setTimeout = (callback, delay) => {
        const id = nextTimer++;
        timers.set(id, {callback, due: now + delay});
        return id;
      };
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout,
        clearTimeout(id) { cleared.push(id); timers.delete(id); },
        performance: {now: () => now},
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        return Promise.resolve(jsonResponse({ok: true}));
      });

      api.syncServerWatchRootsForTest();
      const initialState = api.serverWatchRootsStateForTest();
      const initialDescriptor = api.clientServerWatchStateForTest();
      const timer = initialState.timer;
      now = 100;
      api.syncServerWatchRootsForTest();
      const repeatedState = api.serverWatchRootsStateForTest();
      const repeatedDescriptor = api.clientServerWatchStateForTest();
      assert.equal(repeatedState.timer, timer, `the repeated queued descriptor keeps its original timer: ${JSON.stringify({initialState, initialDescriptor, repeatedState, repeatedDescriptor})}`);
      assert.deepStrictEqual(cleared, [], 'the repeated queued descriptor does not cancel the debounce');

      now = 300;
      timers.get(timer).callback();
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(calls.filter(call => call.url === '/api/watch/roots').length, 1, 'the original debounce registers exactly once');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, false, 'registration completion retires fixture-visible debounce work');
    });

    await testAsync('identical forced watch-root generation joins one in-flight registration', async () => {
      const timers = [];
      const firstRegistration = deferredFetch();
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout() {},
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFilesystemWatchTokenForTest('existing-token');
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        if (calls.length === 1) return firstRegistration.promise;
        return Promise.resolve(jsonResponse({ok: true}));
      });
      const force = {
        force: true,
        forceSourceOwner: 'client-events-ready',
        forceSourceGeneration: 'epoch-1:ready-1',
      };

      const first = api.syncServerWatchRootsNowForTest(force);
      const joined = api.syncServerWatchRootsNowForTest(force);
      firstRegistration.resolve(jsonResponse({ok: true}));
      await first;
      await joined;
      await flushAsyncWork();
      const trailingTimer = api.serverWatchRootsStateForTest().timer;
      if (trailingTimer !== null) timers.find(item => item.id === trailingTimer).callback();
      await flushAsyncWork();
      await flushAsyncWork();

      const watchCalls = calls.filter(call => call.url === '/api/watch/roots');
      assert.equal(watchCalls.length, 1, 'one descriptor and force generation issue one POST with no trailing duplicate');
      assert.equal(api.serverWatchRootsStateForTest().timer, null, 'the joined generation leaves no trailing timer');
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {}, 'the joined generation leaves no trailing options');
      await api.syncServerWatchRootsNowForTest(force);
      assert.equal(calls.filter(call => call.url === '/api/watch/roots').length, 1, 'the settled force generation remains a no-op');
    });

    await testAsync('ready watch-root repair coalesces identical envelopes but trails a newer resource generation', async () => {
      const runReadySequence = async secondEnvelope => {
        const timers = [];
        const firstRegistration = deferredFetch();
        const calls = [];
        const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
          setTimeout(callback, delay) {
            const id = timers.length + 1;
            timers.push({id, callback, delay});
            return id;
          },
          clearTimeout() {},
        });
        api.setFilesystemWatchTokenForTest('existing-token');
        api.setFileExplorerRootForTest('/repo');
        const slots = api.emptyLayoutSlots();
        slots[api.layoutTreeKey] = api.leafNode('left');
        slots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
        api.setLayoutSlotsForTest(slots);
        api.setFetchForTest((url, options = {}) => {
          calls.push({url: String(url), options});
          if (calls.length === 1) return firstRegistration.promise;
          return Promise.resolve(jsonResponse({ok: true}));
        });
        api.installClientEventStreamForTest();
        const source = api.clientEventTransportStateForTest().source;
        const ready = envelope => source.listeners.get('ready')[0]({
          data: JSON.stringify(envelope),
          type: 'ready',
          lastEventId: '',
        });
        const fireWatchTimer = () => {
          const timerId = api.serverWatchRootsStateForTest().timer;
          assert.notEqual(timerId, null, 'ready owns an immediate watch-root registration timer');
          timers.find(item => item.id === timerId).callback();
        };
        const firstEnvelope = {epoch: 'server-a', resource_revisions: {fs_changed: 7}};

        ready(firstEnvelope);
        fireWatchTimer();
        assert.equal(calls.filter(call => call.url === '/api/watch/roots').length, 1, 'the first ready generation starts one registration');
        ready(secondEnvelope);
        fireWatchTimer();
        firstRegistration.resolve(jsonResponse({ok: true}));
        await flushAsyncWork();
        await flushAsyncWork();
        const trailingTimer = api.serverWatchRootsStateForTest().timer;
        if (trailingTimer !== null) timers.find(item => item.id === trailingTimer).callback();
        await flushAsyncWork();
        await flushAsyncWork();
        return calls.filter(call => call.url === '/api/watch/roots');
      };

      const identical = await runReadySequence({epoch: 'server-a', resource_revisions: {fs_changed: 7}});
      assert.equal(identical.length, 1, 'a repeated byte-identical ready envelope does not create a trailing POST');

      const advanced = await runReadySequence({epoch: 'server-a', resource_revisions: {fs_changed: 8}});
      assert.equal(advanced.length, 2, 'a newer ready resource generation creates exactly one trailing POST');
      assert.equal(advanced[0].options.body, advanced[1].options.body, 'the resource generation, not descriptor drift, qualifies the trailing POST');

      const recoveryTimers = [];
      const recoveryCalls = [];
      const recoveryApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = recoveryTimers.length + 1;
          recoveryTimers.push({id, callback, delay});
          return id;
        },
        clearTimeout() {},
      });
      recoveryApi.setFilesystemWatchTokenForTest('existing-token');
      recoveryApi.setFileExplorerRootForTest('/repo');
      const recoverySlots = recoveryApi.emptyLayoutSlots();
      recoverySlots[recoveryApi.layoutTreeKey] = recoveryApi.leafNode('left');
      recoverySlots.left = recoveryApi.paneStateWithTabs([recoveryApi.finderItemId], recoveryApi.finderItemId);
      recoveryApi.setLayoutSlotsForTest(recoverySlots);
      recoveryApi.setFetchForTest((url, options = {}) => {
        recoveryCalls.push({url: String(url), options});
        return Promise.resolve(jsonResponse({ok: true}));
      });
      recoveryApi.installClientEventStreamForTest();
      const recoverySource = recoveryApi.clientEventTransportStateForTest().source;
      const recoveryEnvelope = {epoch: 'server-recovery', resource_revisions: {fs_changed: 3}};
      const readyAfterRecovery = () => recoverySource.listeners.get('ready')[0]({
        data: JSON.stringify(recoveryEnvelope),
        type: 'ready',
        lastEventId: '',
      });
      readyAfterRecovery();
      recoveryTimers.find(item => item.id === recoveryApi.serverWatchRootsStateForTest().timer).callback();
      await flushAsyncWork();
      await flushAsyncWork();
      recoverySource.onerror();
      const disconnectEpisode = recoveryApi.clientEventTransportStateForTest().disconnectEpisode;
      assert.ok(disconnectEpisode?.id, 'an active-stream disconnect owns a recovery episode');
      readyAfterRecovery();
      recoveryTimers.find(item => item.id === recoveryApi.serverWatchRootsStateForTest().timer).callback();
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(recoveryCalls.filter(call => call.url === '/api/watch/roots').length, 2, 'a genuine disconnect episode re-registers the unchanged descriptor once');
    });

    await testAsync('newer forced watch-root generation retains exactly one trailing registration', async () => {
      const timers = [];
      const firstRegistration = deferredFetch();
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout() {},
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFilesystemWatchTokenForTest('existing-token');
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        if (calls.length === 1) return firstRegistration.promise;
        return Promise.resolve(jsonResponse({ok: true}));
      });

      const first = api.syncServerWatchRootsNowForTest({
        force: true,
        forceSourceOwner: 'client-events-ready',
        forceSourceGeneration: 'epoch-1:ready-1',
      });
      const newer = {
        force: true,
        forceSourceOwner: 'client-events-ready',
        forceSourceGeneration: 'epoch-1:ready-2',
      };
      api.syncServerWatchRootsNowForTest(newer);
      api.syncServerWatchRootsNowForTest(newer);
      firstRegistration.resolve(jsonResponse({ok: true}));
      await first;
      await flushAsyncWork();
      const trailingTimer = api.serverWatchRootsStateForTest().timer;
      assert.notEqual(trailingTimer, null, 'the newer generation owns one trailing timer');
      timers.find(item => item.id === trailingTimer).callback();
      await flushAsyncWork();
      await flushAsyncWork();

      const watchCalls = calls.filter(call => call.url === '/api/watch/roots');
      assert.equal(watchCalls.length, 2, 'the newer generation issues exactly one trailing POST');
      assert.equal(watchCalls[0].options.body, watchCalls[1].options.body, 'the source generation, not body drift, qualifies the trailing POST');
      assert.equal(api.serverWatchRootsStateForTest().timer, null, 'the trailing generation retires its timer');
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {}, 'the trailing generation retires its options');
    });

    await testAsync('failed forced watch-root generation remains retryable', async () => {
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin');
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFilesystemWatchTokenForTest('existing-token');
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        return calls.length === 1
          ? Promise.reject(new Error('offline'))
          : Promise.resolve(jsonResponse({ok: true}));
      });
      const force = {
        force: true,
        forceSourceOwner: 'client-events-ready',
        forceSourceGeneration: 'epoch-1:ready-retry',
      };

      await api.syncServerWatchRootsNowForTest(force);
      await api.syncServerWatchRootsNowForTest(force);

      const watchCalls = calls.filter(call => call.url === '/api/watch/roots');
      assert.equal(watchCalls.length, 2, 'failure does not mark the force generation complete');
      assert.equal(watchCalls[0].options.body, watchCalls[1].options.body, 'the retry preserves the descriptor body');
      assert.equal(api.serverWatchRootsStateForTest().registered, true, 'the successful retry restores registration state');
    });

    await testAsync('watch-root descriptor changes replay after an in-flight registration', async () => {
      const timers = [];
      const firstRegistration = deferredFetch();
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout() {},
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFilesystemWatchTokenForTest('existing-token');
      api.setFileExplorerRootForTest('/repo-a');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        if (calls.length === 1) return firstRegistration.promise;
        return Promise.resolve(jsonResponse({ok: true}));
      });

      const first = api.syncServerWatchRootsNowForTest({force: true});
      assert.equal(api.serverWatchRootsStateForTest().inFlight, true, 'the first descriptor owns the registration');
      api.setFileExplorerRootForTest('/repo-b');
      api.syncServerWatchRootsForTest({immediate: true});
      const changedTimer = api.serverWatchRootsStateForTest().timer;
      timers.find(item => item.id === changedTimer).callback();
      assert.equal(calls.length, 1, 'the changed descriptor waits behind the active registration');

      firstRegistration.resolve(jsonResponse({ok: true}));
      await first;
      await flushAsyncWork();
      const replayTimer = api.serverWatchRootsStateForTest().timer;
      if (replayTimer !== null) timers.find(item => item.id === replayTimer).callback();
      await flushAsyncWork();
      await flushAsyncWork();

      assert.equal(calls.length, 2, 'the changed descriptor replays exactly once after retirement');
      assert.deepStrictEqual(JSON.parse(calls[1].options.body).roots, ['/repo-b'], 'the replay carries the latest root descriptor');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'the replayed registration retires');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, false, 'fixture quiescence observes the replay completion');
    });

    await testAsync('forced immediate watch-root registration is not postponed by ordinary refreshes', async () => {
      const timers = [];
      const cleared = [];
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout(id) { cleared.push(id); },
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFilesystemWatchTokenForTest('existing-token');
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        return Promise.resolve(jsonResponse({ok: true}));
      });
      await api.syncServerWatchRootsNowForTest({force: true});

      api.syncServerWatchRootsForTest({immediate: true, force: true});
      for (let index = 0; index < 20; index += 1) api.syncServerWatchRootsForTest();
      const timer = api.serverWatchRootsStateForTest().timer;
      const timerRecord = timers.find(item => item.id === timer);

      assert.equal(timerRecord.delay, 0, 'ordinary refreshes cannot demote a forced immediate deadline');
      assert.equal(api.serverWatchRootsStateForTest().pendingOptions.force, true, 'the merged record retains forced ownership');
      assert.equal(api.serverWatchRootsStateForTest().pendingOptions.immediate, true, 'the merged record retains immediate priority');
      timerRecord.callback();
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(calls.length, 2, 'the forced registration runs exactly once');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, false, 'the forced owner retires after its POST');
    });

    await testAsync('slow successful watch-diff baseline remains lifecycle-visible', async () => {
      const baseline = deferredFetch();
      const api = loadYolomux('', ['1']);
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFileExplorerRootForTest('/repo');
      const visibleSlots = api.emptyLayoutSlots();
      visibleSlots[api.layoutTreeKey] = api.leafNode('left');
      visibleSlots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
      api.setLayoutSlotsForTest(visibleSlots);
      api.setFetchForTest((url) => {
        const parsed = new URL(String(url), 'https://yolomux.test');
        if (parsed.pathname === '/api/watch/roots') return Promise.resolve(jsonResponse({ok: true}));
        if (parsed.pathname === '/api/fs/watch-diff') return baseline.promise;
        return Promise.reject(new Error(`unexpected baseline request ${url}`));
      });

      const owner = api.syncServerWatchRootsNowForTest({force: true});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(api.serverWatchRootsStateForTest().registrationPending, false, 'registration retires before the held baseline body');
      assert.equal(api.serverWatchRootsStateForTest().baselinePending, true, 'the slow 200 baseline remains owned');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, true, 'fixture quiescence retains the slow baseline owner');

      baseline.resolve(jsonResponse({mode: 'full', token: 'slow-baseline-token', directories: []}));
      await owner;
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, false, 'fixture quiescence retires after the baseline applies');
    });

    await testAsync('watch-root synchronization is SSE-identity scoped and reconnect-forced, not browser-renewed', async () => {
      const timers = [];
      const cleared = [];
      let resolveFetch = null;
      const calls = [];
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout(id) { cleared.push(id); },
      });
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFileExplorerRootForTest('/repo');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        if (new URL(String(url), 'https://yolomux.test').pathname === '/api/fs/watch-diff') {
          return Promise.resolve(jsonResponse({mode: 'full', token: 'baseline-token', directories: []}));
        }
        return new Promise(resolve => { resolveFetch = resolve; });
      });

      api.syncServerWatchRootsForTest();
      const priorTimer = api.serverWatchRootsStateForTest().timer;
      api.syncServerWatchRootsForTest({immediate: true});
      const replacementTimer = api.serverWatchRootsStateForTest().timer;
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {immediate: true, force: false}, 'adjacent watch state changes coalesce into one record');
      assert.deepStrictEqual(cleared, [priorTimer], 'replacement debounce clears the prior record timer');
      assert.notEqual(replacementTimer, priorTimer, 'the watch-root record owns only the replacement timer');
      timers.find(item => item.id === replacementTimer).callback();
      assert.equal(api.serverWatchRootsStateForTest().timer, null, 'firing consumes the record timer');
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {}, 'firing consumes merged options once');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, true, 'the same record owns active fetch state');
      api.syncServerWatchRootsNowForTest();
      assert.equal(calls.length, 1, 'an in-flight registration suppresses duplicate fetches');
      resolveFetch(jsonResponse({ok: true}));
      await flushAsyncWork();
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'completion clears in-flight state');
      const firstBody = JSON.parse(calls[0].options.body);
      assert.ok(firstBody.client_id, 'the private watch registration carries the existing SSE client identity in its POST body');

      api.syncServerWatchRootsNowForTest();
      assert.equal(calls.length, 2, 'the successful registration owns one full Finder baseline');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        return Promise.resolve(jsonResponse({ok: true}));
      });
      api.syncServerWatchRootsNowForTest({force: true});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(calls.length, 3, 'an SSE ready/reconnect can explicitly restore the same descriptor');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'forced reconnect registration settles before the next descriptor');

      api.setFileExplorerRootForTest('/repo-failed');
      api.setFetchForTest(() => Promise.reject(new Error('offline')));
      api.syncServerWatchRootsNowForTest();
      await flushAsyncWork();
      await flushAsyncWork();
      await flushAsyncWork();
      const failedState = api.serverWatchRootsStateForTest();
      assert.equal(failedState.inFlight, false, 'failure releases the synchronization record');
      assert.equal(failedState.signature, '', `failure invalidates the signature so the next registration retries: ${JSON.stringify(failedState)}`);
    });

    await testAsync('visible Finder startup owns one full watch baseline after root registration', async () => {
      const api = loadYolomux('', ['1']);
      const registration = deferredFetch();
      const calls = [];
      api.setClientEventsSourceForTest({readyState: 1});
      api.setFileExplorerRootForTest('/repo');
      const visibleSlots = api.emptyLayoutSlots();
      visibleSlots[api.layoutTreeKey] = api.leafNode('left');
      visibleSlots.left = api.paneStateWithTabs([api.finderItemId], api.finderItemId);
      api.setLayoutSlotsForTest(visibleSlots);
      assert.equal(api.fileExplorerTreePaneIsVisibleForTest(), true, 'the startup fixture has a visible Finder tree');
      assert.equal(api.filesystemWatchTokenForTest(), '', 'the startup fixture begins without a watch baseline');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        const parsed = new URL(String(url), 'https://yolomux.test');
        if (parsed.pathname === '/api/watch/roots') return registration.promise;
        if (parsed.pathname === '/api/fs/watch-diff') {
          return Promise.resolve(jsonResponse({
            state: 'queued',
            request: {id: 'r-startup-watch-diff'},
            operation: {
              id: 'op-startup-watch-diff',
              kind: 'fs_watch_diff',
              status_url: '/api/operations/op-startup-watch-diff',
              events_url: '/api/client-events?operation_id=op-startup-watch-diff',
              cursor: {epoch: 'epoch', seq: 0},
              context: {mode: 'full'},
            },
          }, 202));
        }
        return Promise.reject(new Error(`unexpected startup watch request ${url}`));
      });

      api.syncServerWatchRootsNowForTest({force: true});
      assert.equal(calls.length, 1, 'the baseline waits for successful watch-root registration');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, true, 'fixture lifecycle retains the held watch-root registration');
      registration.resolve(jsonResponse({ok: true}));
      await flushAsyncWork();
      await flushAsyncWork();

      assert.equal(api.serverWatchRootsStateForTest().registered, true, 'successful root registration precedes the baseline');
      const watchDiffCalls = calls.filter(call => new URL(call.url, 'https://yolomux.test').pathname === '/api/fs/watch-diff');
      assert.equal(watchDiffCalls.length, 1, 'visible Finder startup begins exactly one full watch baseline');
      assert.equal(new URL(watchDiffCalls[0].url, 'https://yolomux.test').searchParams.get('full'), '1');
      assert.equal(api.apiOperationStateForTest().pending, 1, 'the queued baseline remains owned until its terminal result');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, true, 'root synchronization owns the baseline through terminal delivery');
      assert.equal(api.serverWatchRootsStateForTest().registrationPending, false, 'successful registration retires before the accepted baseline');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, true, 'accepted baseline remains lifecycle-visible until its operation ledger receipt arrives');
      const joinedRefresh = api.refreshWatchedFilesystemForTest({full: true});
      api.syncServerWatchRootsNowForTest({force: true});
      await flushAsyncWork();
      assert.equal(calls.filter(call => new URL(call.url, 'https://yolomux.test').pathname === '/api/fs/watch-diff').length, 1, 'manual refresh and reconnect join the owned startup baseline');
      assert.equal(api.serverWatchRootsStateForTest().baselinePending, true, 'the shared baseline owner remains visible while its receipt is pending');

      api.handleClientPushEventNowForTest('operation_terminal', {
        operation: {id: 'op-startup-watch-diff', cursor: {epoch: 'epoch', seq: 1}},
        result: {
          state: 'ready',
          data: {mode: 'full', token: 'startup-token', directories: []},
          quality: {complete: true, stale: false},
          warnings: [],
        },
      });
      await flushAsyncWork();
      await flushAsyncWork();
      await joinedRefresh;

      assert.equal(api.filesystemWatchTokenForTest(), 'startup-token', 'the owned terminal establishes the startup baseline');
      assert.equal(api.apiOperationStateForTest().pending, 0, 'terminal delivery retires the baseline receipt');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'the shared owner retires after the baseline token exists');
      assert.equal(api.fixtureLifecycleOperationStateForTest().watchRootsPending, false, 'fixture lifecycle retires the completed watch-root owner');
    });

    test('watch-root synchronization: retired parallel globals stay absent', () => {
      const src = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
      const transport = fs.readFileSync('static_src/js/yolomux/99_client_event_transport.js', 'utf8');
      for (const name of ['serverWatchRootsSignature', 'serverWatchRootsInFlight', 'serverWatchRootsSyncedAt', 'serverWatchRootsTimer', 'serverWatchRootsPendingOptions']) {
        assert.equal(src.includes(name), false, `${name} remains retired`);
      }
      assert.ok(src.includes('const serverWatchRootsState = {'), 'one watch-root synchronization owner remains');
      for (const field of ['request: null', "activeKey: ''", "scheduledKey: ''", 'completedForceKeys: new Map()']) {
        assert.ok(src.includes(field), `${field} stays on the shared watch-root owner`);
      }
      for (const owner of ["'client-events-ready'", "'roots-changed'"]) {
        assert.ok(transport.includes(owner), `${owner} supplies an explicit watch-root force owner`);
      }
      assert.ok(transport.includes('handleClientPushEventNowByType(type, payload, envelope)'), 'queued roots-changed delivery preserves its source-generation envelope');
      assert.equal(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8').includes("'server-watch-renew'"), false, 'the browser renewal interval is retired in favor of SSE lifecycle cleanup');
    });

    await testAsync('hidden Finder/Differ refresh work is skipped', async () => {
      const hiddenApi = loadYolomuxWithFileExplorerClosed('', ['1']);
      const hiddenSlots = hiddenApi.emptyLayoutSlots();
      hiddenSlots.left = hiddenApi.paneStateWithTabs(['1'], '1');
      hiddenApi.setLayoutSlotsForTest(hiddenSlots);
      hiddenApi.setFileExplorerRootForTest('/repo');
      hiddenApi.setFileExplorerExpandedForTest(['/repo/src']);
      hiddenApi.setFileExplorerModeForTest('diff');
      hiddenApi.setFileExplorerSessionFilesPayloadForTest({
        session: '1',
        loaded: true,
        repos: [{repo: '/repo'}],
        files: [{repo: '/repo', abs_path: '/repo/src/changed.js'}],
        refs_by_repo: {},
        errors: [],
        from_ref: 'HEAD',
        to_ref: 'current',
      });
      const hiddenState = hiddenApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(hiddenState.roots), [], 'hidden Finder/Differ does not register Finder tree or session-files roots');
      assert.equal(hiddenState.root_surfaces_version, 1, 'even an empty descriptor names the root-surface protocol it follows');
      assert.deepStrictEqual(canonical(hiddenState.root_surfaces), [], 'the hidden descriptor has exact empty root-surface coverage');
      assert.equal(Object.prototype.hasOwnProperty.call(hiddenState, 'session_files'), false, 'hidden Finder/Differ does not register session-files refresh work');
      const hiddenCalls = [];
      hiddenApi.setFetchForTest(url => {
        hiddenCalls.push(String(url));
        return Promise.reject(new Error(`hidden Finder/Differ should not fetch ${url}`));
      });
      await hiddenApi.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true});
      await hiddenApi.refreshWatchedFilesystemForTest({full: true});
      assert.deepStrictEqual(hiddenCalls, [], 'hidden Finder/Differ skips session-files and tree refresh fetches');

      const finderApi = loadYolomux('', ['1']);
      finderApi.setFileExplorerRootForTest('/finder');
      const finderState = finderApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(finderState.root_surfaces), [
        {path: '/finder', surfaces: ['finder']},
      ], 'Finder roots retain the surface that declared them');

      const visibleApi = loadYolomux('', ['1']);
      const visibleSlots = visibleApi.emptyLayoutSlots();
      visibleSlots[visibleApi.layoutTreeKey] = visibleApi.splitNode('row', visibleApi.leafNode('left'), visibleApi.leafNode('right'));
      visibleSlots.left = visibleApi.paneStateWithTabs([visibleApi.differItemId], visibleApi.differItemId);
      visibleSlots.right = visibleApi.paneStateWithTabs(['1'], '1');
      visibleApi.setLayoutSlotsForTest(visibleSlots);
      visibleApi.setFileExplorerRootForTest('/repo');
      visibleApi.setFileExplorerExpandedForTest(['/repo/src']);
      visibleApi.setFileExplorerModeForTest('diff');
      visibleApi.setFileExplorerSessionFilesPayloadForTest({
        session: '1',
        loaded: true,
        repos: [{repo: '/other'}, {repo: '/repo'}],
        files: [
          ...Array.from({length: 99}, (_unused, index) => ({
            repo: '/repo',
            abs_path: `/repo/skills/skill-${index}/changed.js`,
          })),
          {repo: '', abs_path: '/scratch/loose.txt'},
        ],
        refs_by_repo: {},
        errors: [],
        from_ref: 'HEAD',
        to_ref: 'current',
      });
      const visibleState = visibleApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(visibleState.roots), ['/other', '/repo', '/scratch'], 'visible Differ keeps an uncovered file parent without one watch per repository-covered displayed file');
      assert.deepStrictEqual(canonical(visibleState.root_surfaces), [
        {path: '/other', surfaces: ['modified-files-repository']},
        {path: '/repo', surfaces: ['modified-files-repository']},
        {path: '/scratch', surfaces: ['modified-files-parent']},
      ], 'every Differ root retains its repository or uncovered-file-parent source');
      assert.deepStrictEqual(canonical(visibleState.session_files), [{session: '1', hours: 24, from_ref: 'HEAD', to_ref: 'current', repo_refs: null}], 'visible Differ registers the current session-files request');
      visibleSlots.left = visibleApi.paneStateWithTabs([visibleApi.tabberItemId], visibleApi.tabberItemId);
      visibleApi.setLayoutSlotsForTest(visibleSlots);
      const tabberState = visibleApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(tabberState.roots), [], 'visible Tabber does not inherit hidden Finder/Differ roots');
      assert.deepStrictEqual(canonical(tabberState.root_surfaces), [], 'visible Tabber retains exact empty root-surface coverage');
      assert.equal(Object.prototype.hasOwnProperty.call(tabberState, 'session_files'), false, 'visible Tabber does not register session-files work');
    });

    {
      const api = loadYolomux('', ['1']);
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');

      await api.refreshFileExplorerFromPushForTest({
        mode: 'full',
        token: 'full-sse-token',
        directories: [{
          path: '/repo',
          ok: true,
          status: 200,
          data: {path: '/repo', entries: [{name: 'pushed.txt', kind: 'file', mtime: 10, size: 5}]},
        }],
      });

      assert.equal(api.filesystemWatchTokenForTest(), 'full-sse-token', 'an authoritative full SSE frame advances the watch-diff baseline');
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFetchForTest(url => {
        calls.push(String(url));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      const directories = [{
        path: '/repo',
        ok: true,
        status: 200,
        data: {path: '/repo', entries: [{name: 'same.txt', kind: 'file', mtime: 10, size: 5}]},
      }];

      await api.refreshFileExplorerFromPushForTest({mode: 'diff', token: 'shared-token', directories});
      const firstRecord = api.fileExplorerFsResourceRecordsForTest().find(record => record.key === 'list\u001f/repo');
      await api.refreshFileExplorerFromPushForTest({mode: 'full', token: 'shared-token', directories});
      const secondRecord = api.fileExplorerFsResourceRecordsForTest().find(record => record.key === 'list\u001f/repo');

      assert.equal(api.filesystemWatchTokenForTest(), 'shared-token', 'a full frame sharing the prior compact token still advances the watch cursor');
      assert.equal(calls.length, 0, 'compact then full same-token delivery does not request a watch diff');
      assert.equal(secondRecord.generation, firstRecord.generation, 'push dedupe prevents duplicate rendering after cursor advancement');
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFilesystemLastFullAtForTest(Date.now());
      api.setFetchForTest((url, options = {}) => {
        calls.push(String(url));
        const parsed = new URL(String(url), 'https://yolomux.test');
        if (parsed.pathname === '/api/fs/watch-diff') {
          assert.equal(parsed.searchParams.get('since'), 'old-token', 'compact fs_changed asks for the diff from the client-held token');
          assert.equal(parsed.searchParams.has('full'), false, 'recent keyframe state keeps compact fs_changed on the diff path');
          return Promise.resolve(jsonResponse({
            mode: 'diff',
            token: 'new-token',
            since: 'old-token',
            directories: [{
              path: '/repo',
              ok: true,
              status: 200,
              data: {path: '/repo', entries: [{name: 'changed.txt', kind: 'file', mtime: 10, size: 5}]},
            }],
          }));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });

      const refresh = api.refreshFileExplorerFromPushForTest({refresh: true, mode: 'diff', token: 'server-token', roots: ['/repo'], change_summary: {roots_changed: 1}});
      await refresh;
      await flushAsyncWork();

      const watchDiffCalls = calls.filter(url => new URL(url, 'https://yolomux.test').pathname === '/api/fs/watch-diff');
      assert.equal(watchDiffCalls.length, 1, 'compact fs_changed performs one stateless watch-diff request');
      assert.equal(api.filesystemWatchTokenForTest(), 'new-token', 'watch-diff response advances the client baseline token');
    }

    await testAsync('an out-of-order watch-diff response cannot regress a newer full keyframe cursor', async () => {
      const api = loadYolomux('', ['1']);
      const pendingDiff = deferredFetch();
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFilesystemLastFullAtForTest(Date.now());
      api.setFetchForTest(url => {
        const parsed = new URL(String(url), 'https://yolomux.test');
        assert.equal(parsed.pathname, '/api/fs/watch-diff');
        assert.equal(parsed.searchParams.get('since'), 'old-token');
        return pendingDiff.promise;
      });

      const compactRefresh = api.refreshFileExplorerFromPushForTest({
        refresh: true,
        mode: 'diff',
        token: 'compact-push-token',
        roots: ['/repo'],
        change_summary: {roots_changed: 1},
      });
      await flushAsyncWork();
      assert.equal(api.filesystemPushTokenForTest(), 'compact-push-token', 'the live push token records the compact invalidation while its diff is pending');
      assert.equal(api.filesystemWatchTokenForTest(), 'old-token', 'a compact invalidation cannot advance the watch-diff cursor before its response');

      await api.refreshFileExplorerFromPushForTest({mode: 'full', token: 'full-keyframe-token', directories: []});
      assert.equal(api.filesystemPushTokenForTest(), 'full-keyframe-token', 'the newer full keyframe becomes the live push token');
      assert.equal(api.filesystemWatchTokenForTest(), 'full-keyframe-token', 'the newer full keyframe advances the watch-diff cursor');

      pendingDiff.resolve(jsonResponse({mode: 'diff', token: 'stale-diff-token', since: 'old-token', directories: []}));
      await compactRefresh;
      await flushAsyncWork();

      assert.equal(api.filesystemPushTokenForTest(), 'full-keyframe-token', 'the stale watch-diff completion cannot replace the live full-keyframe push token');
      assert.equal(api.filesystemWatchTokenForTest(), 'full-keyframe-token', 'the stale watch-diff completion cannot regress the full-keyframe cursor');
    });

    await testAsync('watch-diff 202 waits for its operation terminal result without a Finder batch fallback', async () => {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFilesystemLastFullAtForTest(Date.now());
      api.setFetchForTest(url => {
        calls.push(String(url));
        const parsed = new URL(String(url), 'https://yolomux.test');
        if (parsed.pathname === '/api/fs/watch-diff') {
          return Promise.resolve(jsonResponse({
            state: 'queued',
            request: {id: 'r-fs-watch-diff'},
            operation: {
              id: 'op-fs-watch-diff',
              kind: 'fs_watch_diff',
              status_url: '/api/operations/op-fs-watch-diff',
              events_url: '/api/client-events?operation_id=op-fs-watch-diff',
              cursor: {epoch: 'epoch', seq: 0},
              context: {mode: 'diff', token: 'new-token', since: 'old-token'},
            },
          }, 202));
        }
        return Promise.reject(new Error(`watch-diff receipt must not fall back to ${url}`));
      });

      const refresh = api.refreshFileExplorerFromPushForTest({refresh: true, mode: 'diff', token: 'server-token', roots: ['/repo'], change_summary: {roots_changed: 1}});
      await flushAsyncWork();
      assert.equal(api.filesystemWatchTokenForTest(), 'old-token', 'the queued receipt does not advance the client baseline before completion');
      assert.equal(calls.filter(url => new URL(url, 'https://yolomux.test').pathname === '/api/fs/batch').length, 0, 'the queued watch-diff operation does not start a direct Finder batch fallback');

      api.handleClientPushEventNowForTest('operation_terminal', {
        operation: {id: 'op-fs-watch-diff', cursor: {epoch: 'epoch', seq: 1}},
        result: {
          state: 'ready',
          request: {id: 'r-fs-watch-diff'},
          data: {
            mode: 'diff',
            token: 'new-token',
            since: 'old-token',
            directories: [{
              path: '/repo',
              ok: true,
              status: 200,
              data: {path: '/repo', entries: [{name: 'changed-after-receipt.txt', kind: 'file', mtime: 10, size: 5}]},
            }],
          },
          quality: {complete: true, stale: false},
          warnings: [],
        },
      });
      await refresh;
      await flushAsyncWork();

      assert.equal(api.filesystemWatchTokenForTest(), 'new-token', 'the terminal watch-diff result advances the client baseline token');
      assert.ok(api.fileExplorerDirectoryRecordForTest('/repo').knownEntryNames.includes('changed-after-receipt.txt'), 'the terminal watch-diff result applies directory entries');
    });

    await testAsync('concurrent filesystem invalidations share one watch-diff receipt and retain one trailing refresh', async () => {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFilesystemLastFullAtForTest(Date.now());
      api.setFetchForTest(url => {
        calls.push(String(url));
        const parsed = new URL(String(url), 'https://yolomux.test');
        assert.equal(parsed.pathname, '/api/fs/watch-diff');
        if (calls.length === 1) {
          assert.equal(parsed.searchParams.get('since'), 'old-token');
          return Promise.resolve(jsonResponse({
            state: 'queued',
            request: {id: 'r-coalesced-watch-diff'},
            operation: {
              id: 'op-coalesced-watch-diff',
              kind: 'fs_watch_diff',
              status_url: '/api/operations/op-coalesced-watch-diff',
              events_url: '/api/client-events?operation_id=op-coalesced-watch-diff',
              cursor: {epoch: 'epoch', seq: 0},
              context: {mode: 'diff', token: 'terminal-token', since: 'old-token'},
            },
          }, 202));
        }
        assert.equal(calls.length, 2, 'only one trailing refresh is retained');
        assert.equal(parsed.searchParams.get('since'), 'old-token', 'a newer source generation fences the older terminal cursor');
        return Promise.resolve(jsonResponse({
          mode: 'diff',
          token: 'latest-token',
          since: 'terminal-token',
          directories: [],
        }));
      });

      const first = api.refreshFileExplorerFromPushForTest({
        refresh: true,
        mode: 'diff',
        token: 'server-token-a',
        roots: ['/repo'],
      });
      await flushAsyncWork();
      const second = api.refreshFileExplorerFromPushForTest({
        refresh: true,
        mode: 'diff',
        token: 'server-token-b',
        roots: ['/repo'],
      });
      await flushAsyncWork();

      assert.equal(calls.length, 1, 'a held operation terminal owns every same-cursor refresh');
      assert.equal(api.apiOperationStateForTest().pending, 1, 'one durable receipt remains pending');
      api.handleClientPushEventNowForTest('operation_terminal', {
        operation: {id: 'op-coalesced-watch-diff', cursor: {epoch: 'epoch', seq: 1}},
        result: {
          state: 'ready',
          data: {mode: 'diff', token: 'terminal-token', since: 'old-token', directories: []},
          quality: {complete: true, stale: false},
          warnings: [],
        },
      });
      await Promise.all([first, second]);
      await flushAsyncWork();

      assert.equal(calls.length, 2, 'the latest invalidation starts exactly one trailing refresh after the terminal');
      assert.equal(api.filesystemWatchTokenForTest(), 'latest-token');
      assert.equal(api.apiOperationStateForTest().pending, 0);
    });

    await testAsync('empty watch-diff terminal result advances once without recursively fetching', async () => {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFilesystemLastFullAtForTest(Date.now());
      api.setFetchForTest(url => {
        calls.push(String(url));
        return Promise.resolve(jsonResponse({
          state: 'queued',
          request: {id: 'r-empty-watch-diff'},
          operation: {
            id: 'op-empty-watch-diff',
            kind: 'fs_watch_diff',
            status_url: '/api/operations/op-empty-watch-diff',
            events_url: '/api/client-events?operation_id=op-empty-watch-diff',
            cursor: {epoch: 'epoch', seq: 0},
            context: {mode: 'diff', token: 'new-token', since: 'old-token'},
          },
        }, 202));
      });

      const refresh = api.refreshFileExplorerFromPushForTest({refresh: true, mode: 'diff', token: 'server-token', roots: ['/repo']});
      await flushAsyncWork();
      api.handleClientPushEventNowForTest('operation_terminal', {
        operation: {id: 'op-empty-watch-diff', cursor: {epoch: 'epoch', seq: 1}},
        result: {
          state: 'ready',
          data: {mode: 'diff', refresh: true, token: 'new-token', since: 'old-token', directories: []},
          quality: {complete: true, stale: false},
          warnings: [],
        },
      });
      await refresh;
      await flushAsyncWork();

      assert.equal(calls.length, 1, 'a resolved empty watch-diff result does not start another watch-diff request');
      assert.equal(api.filesystemWatchTokenForTest(), 'new-token', 'the empty terminal result advances the client baseline token');
    });

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFilesystemWatchTokenForTest('old-token');
      api.setFilesystemLastFullAtForTest(Date.now());
      api.setFetchForTest(url => {
        calls.push(String(url));
        const parsed = new URL(String(url), 'https://yolomux.test');
        if (parsed.pathname === '/api/fs/watch-diff') {
          assert.equal(parsed.searchParams.get('full'), '1', 'forced refresh asks for a full filesystem frame');
          assert.equal(parsed.searchParams.get('since'), 'old-token', 'forced refresh still sends the client baseline token');
          return Promise.resolve(jsonResponse({mode: 'full', token: 'full-token', directories: []}));
        }
        return Promise.resolve(jsonResponse({}));
      });

      await api.refreshWatchedFilesystemForTest({full: true});

      const watchDiffCalls = calls.filter(url => new URL(url, 'https://yolomux.test').pathname === '/api/fs/watch-diff');
      assert.equal(watchDiffCalls.length, 1, 'manual filesystem refresh uses one forced watch-diff request');
      assert.equal(api.filesystemWatchTokenForTest(), 'full-token', 'full refresh response advances the client baseline token');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyTmuxSignalsPayloadForTest({data: {ok: true, windows: [
        {session: '1', window_index: 0, active: true},
        {session: '1', window_index: 1, active: false},
      ]}});
      api.applyTmuxSignalsPayloadForTest({
        patch: true,
        collection: 'windows',
        changes: {
          '1:0': {session: '1', window_index: 0, active: false},
          '1:1': {session: '1', window_index: 1, active: true},
        },
        removed_keys: [],
        fields: {},
        removed_fields: [],
      });

      assert.equal(String(api.activeTmuxSignalWindowForSessionForTest('1').window_index), '1', 'tmux signal patches merge into the existing window snapshot');
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFetchForTest(url => {
        calls.push(String(url));
        const parsed = new URL(String(url), 'http://localhost');
        if (parsed.pathname === '/api/session-files-batch') {
          return Promise.resolve(jsonResponse({sessions: {1: {files: [{repo: '/repo/one', abs_path: '/repo/one/a.py', mtime: 1}]}}}));
        }
        if (parsed.pathname === '/api/session-files') {
          return Promise.resolve(jsonResponse({files: [{repo: '/repo/one', abs_path: '/repo/one/b.py', mtime: 2}]}));
        }
        if (parsed.pathname === '/api/activity') {
          return Promise.resolve(jsonResponse({activity: {}, agent_windows: {}}));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });

      assert.equal(api.tabberSessionFileLookbackHoursForTest(), 24, 'Tabber touched-path lookback defaults to 24 hours');
      assert.ok(api.tabberLookbackControlHtmlForTest().includes('data-tabber-lookback'), 'Tabber exposes its own lookback select');
      assert.ok(/value="24" selected/.test(api.tabberLookbackControlHtmlForTest()), 'Tabber lookback select marks the 24 hour default');
      await api.fetchTabberSessionFilesBatchForTest(['1'], {force: true});
      assert.ok(calls.some(url => {
        const parsed = new URL(url, 'http://localhost');
        return parsed.pathname === '/api/session-files-batch' && parsed.searchParams.get('hours') === '24';
      }), 'Tabber batch touched-path hydration requests the default 24 hour lookback');

      calls.length = 0;
      api.setTabberSessionFileLookbackHoursForTest(336, {refresh: false});
      assert.equal(api.tabberSessionFileLookbackHoursForTest(), 336, 'Tabber stores the selected 14 day lookback');
      assert.ok(/value="336" selected/.test(api.tabberLookbackControlHtmlForTest()), 'Tabber lookback select marks the selected 14 day value');
      await api.fetchTabberSessionFilesBatchForTest(['1']);
      await api.fetchTabberSessionFilesForTest('1', {force: true});
      assert.ok(calls.some(url => {
        const parsed = new URL(url, 'http://localhost');
        return parsed.pathname === '/api/session-files-batch' && parsed.searchParams.get('hours') === '336';
      }), 'changing Tabber lookback invalidates the loaded cache and reloads batch touched paths with selected hours');
      assert.ok(calls.some(url => {
        const parsed = new URL(url, 'http://localhost');
        return parsed.pathname === '/api/session-files' && parsed.searchParams.get('session') === '1' && parsed.searchParams.get('hours') === '336';
      }), 'Tabber single-session touched-path fallback uses selected hours');

      calls.length = 0;
      api.setTranscriptInfoForTest('1', {
        panes: [{window: '0', pane: '0', window_active: true, active: true, process_label: 'claude', process_label_pid: 10, command: 'claude', current_path: '/repo/one'}],
      });
      api.setFileExplorerModeForTest('tabber');
      const panel = new TestElement('tabber-lookback-panel');
      const select = new TestElement('tabber-lookback-select', 'select');
      select.dataset.tabberLookback = 'true';
      select.value = '48';
      panel.appendChild(select);
      api.bindTabberPanelForTest(panel);
      panel.listeners.get('change')[0]({
        target: {
          closest(selector) {
            return selector === '[data-tabber-lookback]' ? select : null;
          },
        },
      });
      assert.equal(api.tabberSessionFileLookbackHoursForTest(), 48, 'Tabber lookback change handler stores the selected value');
      assert.ok(calls.some(url => {
        const parsed = new URL(url, 'http://localhost');
        return parsed.pathname === '/api/activity' && parsed.searchParams.get('hours') === '48';
      }), 'Tabber lookback change handler reloads cached activity paths immediately');
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFetchForTest(url => {
        calls.push(String(url));
        if (String(url) === '/api/run-history') {
          return Promise.resolve(jsonResponse({runs: [{session: '1', prompt: 'history prompt', latest_summary: 'history summary'}]}));
        }
        if (String(url) === '/api/search?q=beta%20status') {
          return Promise.resolve(jsonResponse({
            query: 'beta status',
            results: [{session: '1', kind: 'summary', title: 'summary', snippet: 'beta status summary', target: {type: 'summary', session: '1', tab: 'summary'}}],
          }));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });

      await api.refreshRunHistoryDataForTest();
      await api.runSearchHistoryQueryForTest('beta status');

      assert.deepStrictEqual(calls, ['/api/run-history', '/api/search?q=beta%20status'], 'Search & Runs fetches compact history and search query endpoints');
      const html = api.searchHistoryPanelHtmlForTest();
      assert.ok(html.includes('beta status summary'), 'Search & Runs renders API search results after submit');
      assert.ok(html.includes('history prompt'), 'Search & Runs renders API run history rows after refresh');
    }

    {
      const api = loadYolomux('', ['1']);
      const writes = [];
      api.setFetchForTest((url, options = {}) => {
        assert.equal(String(url), '/api/fs/write');
        const body = JSON.parse(options.body || '{}');
        writes.push(body);
        return Promise.resolve(jsonResponse({mtime: 100 + writes.length, size: body.content.length}));
      });

      const defaultPath = '/home/test/default-save.txt';
      api.setOpenFileStateForTest(defaultPath, {mtime: 1, size: 0, kind: 'text', original: 'base\n', content: 'base  \nnext', dirty: true});
      assert.equal(await api.saveFileEditorForTest(defaultPath, null), true, 'default save succeeds');
      assert.equal(writes[0].content, 'base  \nnext', 'save hygiene is off by default');
      assert.equal(api.openFileStateForTest(defaultPath).original, 'base  \nnext', 'default save records the exact saved content as clean');

      api.setClientSettingsPatchForTest({editor: {trim_trailing_whitespace_on_save: true, ensure_final_newline_on_save: true}});
      const hygienePath = '/home/test/hygiene-save.txt';
      api.setOpenFileStateForTest(hygienePath, {mtime: 2, size: 0, kind: 'text', original: 'base\n', content: 'base  \nnext', dirty: true});
      assert.equal(await api.saveFileEditorForTest(hygienePath, null), true, 'opt-in hygiene save succeeds');
      assert.equal(writes[1].content, 'base\nnext\n', 'opt-in save trims trailing whitespace and adds a final newline');
      assert.equal(api.openFileStateForTest(hygienePath).dirty, false, 'hygiene save leaves the normalized buffer clean');
      assert.equal(api.openFileStateForTest(hygienePath).original, 'base\nnext\n');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/home/test/reload.txt';
      let fetchCount = 0;
      const confirmations = [];
      api.setOpenFileStateForTest(path, {mtime: 1, size: 11, kind: 'text', original: 'old disk\n', content: 'local edit\n', dirty: true});
      api.setFetchForTest((url, options = {}) => {
        const text = String(url);
        if (text.startsWith('/api/fs/batch')) {
          const requests = JSON.parse(options.body || '{}').requests || [];
          return Promise.resolve(jsonResponse({responses: requests.map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, entries: [{name: 'reload.txt', kind: 'file', mtime: 9, size: 11}]},
          }))}));
        }
        if (text.startsWith('/api/fs/list?')) {
          return Promise.resolve(jsonResponse({entries: [{name: 'reload.txt', kind: 'file', mtime: 9, size: 11}]}));
        }
        if (!text.startsWith('/api/fs/read?')) return Promise.resolve(jsonResponse({entries: []}));
        fetchCount += 1;
        assert.equal(text, `/api/fs/read?path=${encodeURIComponent(path)}`);
        return Promise.resolve(jsonResponse({content: 'fresh disk\n', mtime: 9, size: 11}));
      });
      api.setWindowConfirmForTest(message => {
        confirmations.push(message);
        return false;
      });
      assert.equal(await api.reloadOpenFileFromDiskForTest(path), false, 'dirty reload cancels when the user rejects the warning');
      assert.equal(fetchCount, 0, 'cancelled dirty reload does not read from disk');
      assert.equal(api.openFileStateForTest(path).content, 'local edit\n', 'cancelled dirty reload preserves the unsaved buffer');
      assert.equal(confirmations.length, 1, 'dirty reload shows the existing warning');

      api.setWindowConfirmForTest(message => {
        confirmations.push(message);
        return true;
      });
      assert.equal(await api.reloadOpenFileFromDiskForTest(path), true, 'dirty reload proceeds after confirmation');
      assert.equal(fetchCount, 1, 'confirmed reload reads the disk copy');
      assert.equal(api.openFileStateForTest(path).content, 'fresh disk\n', 'confirmed reload replaces the buffer with disk content');
      assert.equal(api.openFileStateForTest(path).dirty, false, 'confirmed reload leaves the disk copy clean');
    }

    {
      const api = loadYolomux('', ['1', '2']);
      api.setFileExplorerModeForTest('tabber');
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 50);
      slots.left = api.paneStateWithTabs(['1', api.tabberItemId], '1');
      slots.right = api.paneStateWithTabs(['2'], '2');
      api.setLayoutSlotsForTest(slots);
      api.setFocusedPanelItem('1');
      api.editorNav.stack = [];
      api.editorNav.index = -1;
      const tabberPanel = new TestElement('tabber-back-panel');
      const sessionTwoRow = new TestElement('tabber-back-session-2');
      sessionTwoRow.classList.add('file-tree-row');
      sessionTwoRow.dataset.kind = 'dir';
      sessionTwoRow.dataset.path = '/s_2';
      sessionTwoRow.dataset.tabberType = 'session';
      sessionTwoRow.dataset.tabberSession = '2';
      tabberPanel.appendChild(sessionTwoRow);
      api.bindTabberPanelForTest(tabberPanel);
      tabberPanel.listeners.get('click')[0]({
        target: {
          closest(selector) {
            if (selector === '.file-tree-row[data-tabber-type]') return sessionTwoRow;
            return null;
          },
        },
        preventDefault() {},
        stopPropagation() {},
      });
      assert.equal(api.currentSessionActionTarget(), '2', 'clicking the green Tabber session row opens Tab 2 before Back');
      await api.editorNavBackForTest();
      assert.equal(api.currentSessionActionTarget(), '1', 'Back returns to the previously active tab after a green Tabber session row click');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/main.py';
      const item = api.fileEditorDiffPreviewItemFor(path);
      api.setOpenFileOwner(path, item);
      api.setOpenFileStateForTest(path, {
        kind: 'text',
        original: 'print("hello")\n',
        content: 'print("hello")\n',
        dirty: false,
        realpath: path,
        file_id: 'dev:10:ino:20',
        fileIdentity: 'id:dev:10:ino:20',
      });
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
      slots.left = api.paneStateWithTabs(['1'], '1');
      slots.slot1 = api.paneStateWithTabs([item], item);
      api.rememberFileExplorerOpenIntentForTest(false);
      api.setLayoutSlotsForTest(slots);
      api.setFocusedPanelItem('1');
      api.setFetchForTest(url => {
        const text = String(url);
        if (text.startsWith('/api/fs/read')) {
          return Promise.resolve(jsonResponse({
            path,
            content: 'print("hello")\n',
            size: 15,
            mtime: 1,
            mtime_ns: 1,
            realpath: path,
            file_id: 'dev:10:ino:20',
            git_root: '/repo/app',
            git_tracked: true,
            git_history: [{ref: 'a'}, {ref: 'b'}],
            git_has_history: true,
          }));
        }
        if (text.startsWith('/api/fs/diff')) {
          return Promise.resolve(jsonResponse({
            repo: '/repo/app',
            relative_path: 'src/main.py',
            diff: '@@ -1 +1 @@\n-print("old")\n+print("hello")\n',
            original: 'print("old")\n',
            working: 'print("hello")\n',
          }));
        }
        return Promise.resolve(jsonResponse({ok: true}));
      });

      await api.openChangedFileInDiffForTest(path, '1', 'M', '/repo/app', {userInitiated: true});

      assert.equal(api.slotForSession(item), 'slot1', 'Differ reopen keeps the moved filediff tab in its current pane');
      assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
        left: {tabs: ['1'], active: '1'},
        slot1: {tabs: [item], active: item},
      });
      assert.equal(api.editorViewModeFor(path, item), 'diff', 'Differ reopen leaves the moved filediff tab in Diff mode');
    }

    {
      const api = loadYolomux('', ['1']);
      const firstPath = '/repo/app/src/first.py';
      const secondPath = '/repo/app/src/second.py';
      const localBasename = path => String(path || '').split('/').pop() || '';
      api.setFetchForTest(url => {
        const text = String(url);
        const path = decodeURIComponent((text.match(/path=([^&]+)/) || [])[1] || '');
        if (text.startsWith('/api/fs/read')) {
          return Promise.resolve(jsonResponse({
            path,
            content: `print("${localBasename(path)}")\n`,
            size: 16,
            mtime: 1,
            mtime_ns: 1,
            realpath: path,
            file_id: path.endsWith('first.py') ? 'dev:10:ino:20' : 'dev:10:ino:21',
            git_root: '/repo/app',
            git_tracked: true,
            git_history: [{ref: 'a'}, {ref: 'b'}],
            git_has_history: true,
          }));
        }
        if (text.startsWith('/api/fs/diff')) {
          return Promise.resolve(jsonResponse({
            repo: '/repo/app',
            relative_path: path.replace('/repo/app/', ''),
            diff: `@@ -1 +1 @@\n-print("old")\n+print("${localBasename(path)}")\n`,
            original: 'print("old")\n',
            working: `print("${localBasename(path)}")\n`,
          }));
        }
        return Promise.resolve(jsonResponse({ok: true}));
      });

      await api.openChangedFileInDiffForTest(firstPath, '1', 'M', '/repo/app', {userInitiated: true});
      const firstItem = api.fileEditorDiffPreviewItemFor(firstPath);
      assert.deepStrictEqual(canonical(api.filePanelItemsForPath(firstPath)), [firstItem], 'first Differ row uses the reusable Differ preview tab');
      await api.openChangedFileInDiffForTest(secondPath, '1', 'M', '/repo/app', {userInitiated: true});
      const secondItem = api.fileEditorDiffPreviewItemFor(secondPath);

      assert.deepStrictEqual(canonical(api.filePanelItemsForPath(firstPath)), [], 'second Differ row removes the old preview owner');
      assert.deepStrictEqual(canonical(api.filePanelItemsForPath(secondPath)), [secondItem], 'second Differ row owns the preview tab under the new path');
      assert.equal(api.editorViewModeFor(secondPath, secondItem), 'diff', 'second Differ row opens the next file in Diff mode, not Edit mode');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/main.py';
      const existingItem = api.fileEditorItemFor(path);
      api.setOpenFileOwner(path, existingItem);
      api.setOpenFileStateForTest(path, {
        kind: 'text',
        original: 'print("hello")\n',
        content: 'print("hello")\n',
        dirty: false,
        realpath: path,
        file_id: 'dev:10:ino:20',
        fileIdentity: 'id:dev:10:ino:20',
        gitRoot: '/repo/app',
        gitTracked: true,
        gitHistory: [{ref: 'a'}, {ref: 'b'}],
        gitHasHistory: true,
        diffLoaded: true,
        diffUnavailable: true,
        diffError: 'old unavailable diff',
      });
      api.setFileEditorViewMode(path, 'edit', existingItem);
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
      slots.left = api.paneStateWithTabs(['1'], '1');
      slots.slot1 = api.paneStateWithTabs([existingItem], existingItem);
      api.setLayoutSlotsForTest(slots);
      api.setFocusedPanelItem('1');
      api.setFetchForTest(url => {
        const text = String(url);
        if (text.startsWith('/api/fs/read')) {
          return Promise.resolve(jsonResponse({
            path,
            content: 'print("hello")\n',
            size: 15,
            mtime: 1,
            mtime_ns: 1,
            realpath: path,
            file_id: 'dev:10:ino:20',
            git_root: '/repo/app',
            git_tracked: true,
            git_history: [{ref: 'a'}, {ref: 'b'}],
            git_has_history: true,
          }));
        }
        if (text.startsWith('/api/fs/diff')) {
          api.setFileEditorViewMode(path, 'edit', existingItem);
          return Promise.resolve(jsonResponse({
            repo: '/repo/app',
            relative_path: 'src/main.py',
            diff: '@@ -1 +1 @@\n-print("old")\n+print("hello")\n',
            original: 'print("old")\n',
            working: 'print("hello")\n',
          }));
        }
        return Promise.resolve(jsonResponse({ok: true}));
      });

      await api.openChangedFileInDiffForTest(path, '1', 'M', '/repo/app', {userInitiated: true});

      assert.equal(api.slotForSession(existingItem), 'slot1', 'Differ row reopen keeps the existing editor tab in its pane');
      assert.equal(api.editorViewModeFor(path, existingItem), 'diff', 'a repeated Differ row click forces the actual existing tab back to Diff mode');
    }

    {
      const api = loadYolomux('', ['1']);
      const realPath = '/repo/app/src/main.py';
      const linkPath = '/repo/app/link-main.py';
      const calls = [];
      api.setFetchForTest(url => {
        const text = String(url);
        calls.push(text);
        const path = decodeURIComponent((text.match(/path=([^&]+)/) || [])[1] || '');
        if (text.startsWith('/api/fs/read')) {
          return Promise.resolve(jsonResponse({
            path,
            content: 'print("hello")\n',
            size: 15,
            mtime: 1,
            mtime_ns: 1,
            realpath: realPath,
            file_id: 'dev:10:ino:20',
            git_root: '/repo/app',
            git_tracked: true,
            git_history: [{ref: 'a'}, {ref: 'b'}],
            git_has_history: true,
          }));
        }
        return Promise.resolve(jsonResponse({ok: true}));
      });

      const firstItem = await api.openFileInEditorForTest(realPath, {name: 'main.py', realpath: realPath, file_id: 'dev:10:ino:20'}, {viewMode: 'edit'});
      const dirtyState = api.currentFileStateForTest(realPath);
      api.setOpenFileStateForTest(realPath, {
        ...dirtyState,
        content: 'dirty edit\n',
        dirty: true,
      });
      const secondItem = await api.openFileInAdditionalEditorTabForTest(linkPath, {name: 'link-main.py', realpath: realPath, file_id: 'dev:10:ino:20'}, {viewMode: 'diff'});

      assert.equal(secondItem, firstItem, 'opening a symlink alias focuses the existing physical-file editor item');
      assert.deepStrictEqual(canonical(api.openFileEditorItems()), [firstItem], 'same physical file has one editable editor item');
      assert.deepStrictEqual(canonical(api.filePanelItemsForPath(realPath)), [firstItem], 'primary path owns the single editor tab');
      assert.deepStrictEqual(canonical(api.filePanelItemsForPath(linkPath)), [], 'symlink alias does not create a second editable editor tab');
      assert.equal(api.editorViewModeFor(realPath, firstItem), 'diff', 'alias open applies the requested mode to the existing editor');
      assert.equal(api.currentFileStateForTest(realPath).content, 'dirty edit\n', 'alias open preserves the dirty buffer');
      const readCalls = calls.filter(url => url.startsWith('/api/fs/read'));
      const metadataCalls = calls.filter(url => url.startsWith('/api/fs/info') && url.includes('include_git=1'));
      assert.equal(readCalls.length, 1, 'entry identity avoids a second base read before focusing the existing editor');
      assert.equal(metadataCalls.length, 1, 'the one deferred Git operation follows the already-painted base content without rereading bytes');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/keep-preview.md';
      const aliasPath = '/repo/app/keep-preview-alias.md';
      const calls = [];
      api.setFetchForTest(url => {
        const request = String(url);
        calls.push(request);
        const requestedPath = decodeURIComponent((request.match(/path=([^&]+)/) || [])[1] || '');
        return Promise.resolve(jsonResponse({
          path: requestedPath,
          content: '# preview\n',
          size: 10,
          mtime: 1,
          mtime_ns: 1,
          realpath: path,
          file_id: 'dev:10:ino:22',
        }));
      });

      const item = await api.openFileInEditorForTest(path, {name: 'keep-preview.md', realpath: path, file_id: 'dev:10:ino:22'}, {viewMode: 'preview'});
      assert.equal(api.editorViewModeFor(path, item), 'preview', 'the initial explicit Preview action selects Preview');
      assert.equal(await api.openFileInEditorForTest(path, {name: 'keep-preview.md', realpath: path, file_id: 'dev:10:ino:22'}), item, 'an implicit reopen focuses the existing tab');
      assert.equal(api.currentFileStateForTest(path).kind, 'text', 'an implicit reopen keeps the loaded file state');
      assert.equal(api.editorViewModeFor(path, item), 'preview', 'an implicit reopen preserves the selected Preview mode');
      assert.equal(await api.openFileInEditorForTest(aliasPath, {name: 'keep-preview-alias.md', realpath: path, file_id: 'dev:10:ino:22'}), item, 'an implicit physical-file alias focuses the same tab');
      assert.equal(api.editorViewModeFor(path, item), 'preview', 'an implicit physical-file alias preserves the selected Preview mode');
      await api.openTerminalFileReferenceForTest({path, info: {name: 'keep-preview.md', realpath: path, file_id: 'dev:10:ino:22'}});
      assert.equal(api.editorViewModeFor(path, item), 'preview', 'terminal Open also preserves the selected Preview mode');
      assert.equal(await api.openFileInEditorForTest(path, {name: 'keep-preview.md', realpath: path, file_id: 'dev:10:ino:22'}, {viewMode: 'edit'}), item, 'an explicit reopen still focuses the existing tab');
      assert.equal(api.editorViewModeFor(path, item), 'edit', 'an explicit view action still overrides the preserved mode');
      assert.equal(calls.filter(url => url.startsWith('/api/fs/read?') && !url.includes('include_git=1')).length, 1, 'reopens and aliases do not start another base read');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/stale-git.md';
      const deferredGit = deferredFetch();
      api.setFetchForTest(url => {
        const route = String(url);
        if (route.includes('include_git=1')) return deferredGit.promise;
        return Promise.resolve(jsonResponse({
          path,
          content: '# base\n',
          size: 7,
          mtime: 1,
          mtime_ns: 1,
          realpath: path,
          file_id: 'dev:10:ino:21',
        }));
      });

      await api.openFileInEditorForTest(path, {name: 'stale-git.md'}, {viewMode: 'edit'});
      assert.equal(api.currentFileStateForTest(path).content, '# base\n', 'base content paints before deferred Git metadata resolves');
      api.setOpenFileStateForTest(path, {
        ...api.currentFileStateForTest(path),
        content: '# newer state\n',
        original: '# newer state\n',
        gitRoot: '/newer/repo',
      });
      deferredGit.resolve(jsonResponse({
        path,
        content: '# base\n',
        size: 7,
        mtime: 1,
        mtime_ns: 1,
        realpath: path,
        file_id: 'dev:10:ino:21',
        git_root: '/stale/repo',
        git_tracked: true,
        git_history: [{ref: 'HEAD'}],
        git_has_history: true,
      }));
      await flushAsyncWork();
      assert.equal(api.currentFileStateForTest(path).content, '# newer state\n', 'a stale deferred Git result never replaces newer editor content');
      assert.equal(api.currentFileStateForTest(path).gitRoot, '/newer/repo', 'a stale deferred Git result never clobbers newer Git state');
    }

    for (const response of [
      () => jsonResponse({path: '/repo/app/src/closed.md', content: '# closed\n', size: 9, mtime: 1, mtime_ns: 1}),
      () => jsonResponse({error: 'path not found: /repo/app/src/closed.md', status: 404}, 404),
    ]) {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/closed.md';
      const delayedRead = deferredFetch();
      api.setFetchForTest(() => delayedRead.promise);
      const opening = api.openFileInEditorForTest(path, {name: 'closed.md'}, {viewMode: 'edit'});
      await flushAsyncWork();
      assert.equal(api.currentFileStateForTest(path).loading, true, 'the immediate tab is Loading before a delayed base read completes');
      assert.equal(await api.closeFileTabForTest(path), true, 'the user can close an immediate Loading tab');
      delayedRead.resolve(response());
      assert.equal(await opening, null, 'a completed read returns no item after its Loading tab was closed');
      assert.equal(api.currentFileStateForTest(path), null, 'neither a delayed success nor a delayed error resurrects a closed tab');
      assert.deepEqual(canonical(api.filePanelItemsForPath(path)), [], 'a delayed base read never recreates closed tab ownership');
    }

    {
      const api = loadYolomux('', ['1']);
      const oldPath = '/repo/app/something.md';
      const newPath = '/repo/app/blah/something.md';
      const fileId = 'dev:10:ino:20';
      const calls = [];
      api.setFetchForTest(url => {
        const text = String(url);
        calls.push(text);
        const path = decodeURIComponent((text.match(/path=([^&]+)/) || [])[1] || '');
        if (text.startsWith('/api/fs/read')) {
          return Promise.resolve(jsonResponse({
            path,
            content: path === newPath ? '# moved\n' : '# original\n',
            size: path === newPath ? 8 : 11,
            mtime: path === newPath ? 2 : 1,
            mtime_ns: path === newPath ? 2 : 1,
            realpath: path,
            file_id: fileId,
            git_root: '/repo/app',
            git_tracked: true,
            git_history: [{ref: 'a'}, {ref: 'b'}],
            git_has_history: true,
          }));
        }
        return Promise.resolve(jsonResponse({ok: true}));
      });

      const oldItem = await api.openFileInEditorForTest(oldPath, {name: 'something.md', realpath: oldPath, file_id: fileId}, {viewMode: 'edit'});
      const oldState = api.currentFileStateForTest(oldPath);
      api.setOpenFileStateForTest(oldPath, {
        ...oldState,
        kind: 'error',
        original: '',
        content: '',
        dirty: false,
        error: 'path not found: /repo/app/something.md',
        externalMissing: true,
      });
      calls.length = 0;

      const newItem = await api.openFileInEditorForTest(newPath, {name: 'something.md', realpath: newPath, file_id: fileId}, {viewMode: 'edit'});

      assert.notEqual(newItem, oldItem, 'opening the moved full path does not focus the stale missing editor tab');
      assert.equal(api.currentFileStateForTest(oldPath).externalMissing, true, 'old path remains marked missing');
      assert.equal(api.currentFileStateForTest(newPath).content, '# moved\n', 'new path loads fresh file content');
      const newPathReadCalls = calls.filter(url => url.startsWith('/api/fs/read'));
      const newPathMetadataCalls = calls.filter(url => url.startsWith('/api/fs/info') && url.includes('include_git=1'));
      assert.deepStrictEqual(newPathReadCalls.map(url => decodeURIComponent((url.match(/path=([^&]+)/) || [])[1] || '')), [newPath], 'new full path forces one base content read');
      assert.equal(newPathMetadataCalls.length, 1, 'the deferred new-path Git operation does not reread content');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/main.py';
      const readResolvers = [];
      const calls = [];
      api.setFetchForTest(url => {
        const text = String(url);
        calls.push(text);
        if (text.startsWith('/api/fs/read')) {
          return new Promise(resolve => {
            readResolvers.push(() => resolve(jsonResponse({
              path,
              content: 'print("hello")\n',
              size: 15,
              mtime: 1,
              mtime_ns: 1,
              realpath: path,
              file_id: 'dev:10:ino:20',
              git_root: '/repo/app',
              git_tracked: true,
              git_history: [{ref: 'a'}, {ref: 'b'}],
              git_has_history: true,
            })));
          });
        }
        return Promise.resolve(jsonResponse({ok: true}));
      });

      const firstOpen = api.openFileInAdditionalEditorTabForTest(path, {name: 'main.py'}, {viewMode: 'edit'});
      const secondOpen = api.openFileInAdditionalEditorTabForTest(path, {name: 'main.py'}, {viewMode: 'diff'});
      assert.equal(readResolvers.length, 1, 'concurrent same-path editor opens share one in-flight read');
      readResolvers[0]();
      const [firstItem, secondItem] = await Promise.all([firstOpen, secondOpen]);

      assert.equal(secondItem, firstItem, 'concurrent same-path new-editor opens converge on the first editor item');
      assert.deepStrictEqual(canonical(api.openFileEditorItems()), [firstItem], 'concurrent same-path opens leave one editable editor item');
      assert.equal(api.editorViewModeFor(path, firstItem), 'diff', 'the later requested mode applies to the focused existing editor');
      const samePathReadCalls = calls.filter(url => url.startsWith('/api/fs/read'));
      const samePathMetadataCalls = calls.filter(url => url.startsWith('/api/fs/info') && url.includes('include_git=1'));
      assert.equal(samePathReadCalls.length, 1, 'same-path opens share one base read');
      assert.equal(samePathMetadataCalls.length, 1, 'the shared open emits one metadata-only Git request');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/canonical.md';
      api.setFetchForTest(url => {
        const text = String(url);
        if (text.startsWith('/api/fs/diff')) {
          return Promise.resolve(jsonResponse({
            repo: '/repo/app',
            relative_path: 'src/canonical.md',
            from_ref: 'a'.repeat(40),
            to_ref: 'current',
            diff: '@@ -1 +1 @@\n-old\n+dirty\n',
            original: 'old\n',
            working: 'dirty\n',
          }));
        }
        return Promise.resolve(jsonResponse({
          path,
          content: '# original\n',
          size: 11,
          mtime: 1,
          mtime_ns: 1,
          realpath: path,
          file_id: 'dev:10:ino:30',
          git_root: '/repo/app',
          git_tracked: true,
          git_history: [{ref: 'a'}, {ref: 'b'}],
          git_has_history: true,
        }));
      });

      const first = await api.openFileInAdditionalEditorTabForTest(path, {name: 'canonical.md'}, {canonical: true, viewMode: 'edit'});
      const opened = api.openFileStateForTest(path);
      api.setOpenFileStateForTest(path, {...opened, content: '# dirty\n', dirty: true});
      const preview = await api.openFileInAdditionalEditorTabForTest(path, {name: 'canonical.md'}, {canonical: true, viewMode: 'preview'});
      const diff = await api.openFileInAdditionalEditorTabForTest(path, {name: 'canonical.md'}, {canonical: true, viewMode: 'diff'});

      assert.equal(first, api.fileEditorItemFor(path));
      assert.equal(preview, first);
      assert.equal(diff, first);
      assert.deepStrictEqual(canonical(api.openFileEditorItems()), [first], 'Finder mode actions retain one canonical working-tree item');
      assert.equal(api.openFileStateForTest(path).content, '# dirty\n', 'Finder mode changes preserve the dirty working-tree buffer');
      assert.equal(api.editorViewModeFor(path, first), 'diff', 'the last Finder action changes only the selected mode');
    }

    {
      const api = loadYolomux('', ['1']);
      const path = '/repo/app/src/history.js';
      const firstFrom = '1'.repeat(40);
      const firstTo = '2'.repeat(40);
      const secondFrom = '3'.repeat(40);
      const secondTo = '4'.repeat(40);
      api.setOpenFileStateForTest(path, {kind: 'text', content: 'working\n', original: 'working\n', dirty: true});
      api.setFetchForTest(url => {
        const parsed = new URL(String(url), 'http://localhost');
        const fromRef = parsed.searchParams.get('from');
        const toRef = parsed.searchParams.get('to');
        return Promise.resolve(jsonResponse({
          repo: '/repo/app',
          relative_path: 'src/history.js',
          from_ref: fromRef,
          to_ref: toRef,
          diff: `@@ -1 +1 @@\n-${fromRef}\n+${toRef}\n`,
          original: `${fromRef}\n`,
          working: `${toRef}\n`,
        }));
      });

      const first = await api.openHistoricalFileInEditorForTest(path, firstFrom, firstTo, {repo: '/repo/app'});
      const repeat = await api.openHistoricalFileInEditorForTest(path, firstFrom, firstTo, {repo: '/repo/app'});
      const second = await api.openHistoricalFileInEditorForTest(path, secondFrom, secondTo, {repo: '/repo/app'});
      assert.equal(repeat, first, 'the same historical tuple activates the exact Editor instance');
      assert.notEqual(second, first, 'another historical tuple creates another current-Editor instance');
      assert.equal(api.tabTypeForItem(first)?.key, 'file-editor');
      assert.equal(api.editorViewModeFor(path, first), 'diff');
      assert.equal(api.fileEditorStateForItemForTest(path, first).content, `${firstTo}\n`, 'historical Preview owns immutable TO content');
      assert.equal(api.fileEditorStateForItemForTest(path, second).content, `${secondTo}\n`, 'two tuples for one path retain isolated content');
      api.registerFileEditorLayoutItemForTest(path, {item: first});
      assert.equal(api.fileEditorStateForItemForTest(path, first).content, `${firstTo}\n`, 'layout registration cannot reapply loading defaults over immutable TO content');
      assert.equal(api.fileEditorStateForItemForTest(path, first).readOnly, true);
      assert.equal(api.openFileStateForTest(path).content, 'working\n', 'historical opens never replace dirty working-tree state');
    }

    {
      const sessions = Array.from({length: 9}, (_, index) => String(index + 1));
      const api = loadYolomux('', sessions);
      const repo = '/repo/app';
      const path = `${repo}/src/return.js`;
      const fromRef = '5'.repeat(40);
      const toRef = '6'.repeat(40);
      const repoItem = api.resolveLayoutItem(api.gitDiffItemFor(repo));
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.leafNode('left');
      slots.left = api.paneStateWithTabs(['1', repoItem], repoItem);
      api.setLayoutSlotsForTest(slots);
      api.setFocusedPanelItem(repoItem);
      api.setFetchForTest(url => {
        const parsed = new URL(String(url), 'http://localhost');
        return Promise.resolve(jsonResponse({
          repo,
          relative_path: 'src/return.js',
          from_ref: parsed.searchParams.get('from'),
          to_ref: parsed.searchParams.get('to'),
          diff: '@@ -1 +1 @@\n-old\n+new\n',
          original: 'old\n',
          working: 'new\n',
        }));
      });

      const opened = await api.openGitDiffHistoricalFileForTest({
        repo,
        from_ref: fromRef,
        to_ref: toRef,
        parents: [fromRef],
      }, {path: 'src/return.js', abs_path: path}, {returnToItem: repoItem});
      assert.equal(api.layoutSlotsForTest().left.active, opened, 'clicking a commit file activates its historical diff tab');

      const minimizeSlots = api.emptyLayoutSlots();
      minimizeSlots[api.layoutTreeKey] = api.splitNode(
        'row',
        api.leafNode('left'),
        api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50),
        60,
      );
      minimizeSlots.left = api.paneStateWithTabs(['1'], '1');
      minimizeSlots.slot1 = api.paneStateWithTabs([repoItem], repoItem);
      minimizeSlots.slot2 = api.paneStateWithTabs([opened], opened);
      api.setLayoutSlotsForTest(minimizeSlots);
      api.minimizePaneFromLayout(opened);
      assert.equal(api.layoutSlotsForTest().left.active, '1', 'minimizing retains the unrelated destination pane selection');
      assert.equal(api.layoutSlotsForTest().slot1.active, repoItem, 'minimizing a historical diff pane returns to its originating Diff repo tab in another pane');

      const capacitySlots = api.emptyLayoutSlots();
      capacitySlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 70);
      capacitySlots.left = api.paneStateWithTabs([...sessions, repoItem], '1');
      capacitySlots.slot1 = api.paneStateWithTabs([opened], opened);
      api.setLayoutSlotsForTest(capacitySlots);
      api.minimizePaneFromLayout(opened);
      assert.equal(api.layoutSlotsForTest().left.tabs.includes(repoItem), true, 'a full destination cannot evict the originating Diff repo tab');
      assert.equal(api.layoutSlotsForTest().left.active, repoItem, 'a full destination still returns to the originating Diff repo tab');

      api.activatePaneTab('left', opened);
      await api.closeFileTabForTest(path, {item: opened});
      assert.equal(api.layoutSlotsForTest().left.active, repoItem, 'closing the historical diff returns to its originating Diff repo tab');
    }

    {
      const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
      const differApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {
        strings: {en: JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')), 'zh-Hant': zhHant},
      });
      differApi.i18nSetCatalogForTest('zh-Hant', zhHant);
      differApi.setFileExplorerModeForTest('diff');
      differApi.setFileExplorerChangesSelectedSessionForTest('1');
      differApi.setSessionFilesPayloadForTest({
        session: '1',
        loaded: true,
        errors: [],
        refs_by_repo: {},
        repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1}],
        files: [{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1}],
      });
      const beforeLocaleChange = differApi.fileExplorerChangesPanelHtml();
      assert.ok(beforeLocaleChange.includes('data-open-change-file="/repo/app/README.md"'), 'Differ renders rows before a language change');
      differApi.setActiveLocaleForTest('zh-Hant');
      assert.equal(differApi.i18nActiveLocaleId(), 'zh-Hant', 'the active language changes to Traditional Chinese');
      const afterLocaleChange = differApi.fileExplorerChangesPanelHtml();
      assert.ok(afterLocaleChange.includes('data-open-change-file="/repo/app/README.md"'), 'Differ rows stay visible after a language change');
      assert.ok(afterLocaleChange.includes(zhHant['common.reload']), 'Differ chrome localizes after a language change');
      assert.equal(afterLocaleChange.includes('No Differ results for this session.'), false, 'Differ does not blank to the empty state during locale apply');
    }

    {
      const staleDoitPath = '/home/test/yolomux.dev1/DOIT.57.md';
      const dirtyMissingPath = '/home/test/yolomux.dev1/unsaved.md';
      const realDoitPath = '/home/test/yolomux.dev2/DOIT.57.md';
      const validatingDoitApi = loadYolomux('', ['1']);
      const validatingDoitItem = validatingDoitApi.registerFileEditorLayoutItemForTest(staleDoitPath);
      const dirtyMissingItem = validatingDoitApi.registerFileEditorLayoutItemForTest(dirtyMissingPath);
      const validatingDoitSlots = validatingDoitApi.emptyLayoutSlots();
      validatingDoitSlots.left = validatingDoitApi.paneStateWithTabs([validatingDoitItem, dirtyMissingItem], validatingDoitItem);
      validatingDoitApi.setLayoutSlotsForTest(validatingDoitSlots);
      validatingDoitApi.setOpenFileStateForTest(dirtyMissingPath, {
        kind: 'text',
        original: '# original\n',
        content: '# unsaved\n',
        dirty: true,
        externalMissing: true,
        externalMissingCheckedAt: Date.now(),
      });
      validatingDoitApi.setFileQuickOpenCandidatesForTest('/home/test/yolomux.dev3', [
        {name: 'DOIT.57.md', path: realDoitPath, relative_path: 'DOIT.57.md', indexed_root: '/home/test/yolomux.dev2', kind: 'file'},
      ]);
      validatingDoitApi.setCommandPaletteStateForTest('files', 'doit57');
      const validationCalls = [];
      let staleDoitExists = false;
      validatingDoitApi.setFetchForTest((url, options = {}) => {
        if (String(url).startsWith('/api/fs/read')) {
          return Promise.resolve(jsonResponse({content: '# restored\n', size: 11, mtime_ns: 12}));
        }
        const body = JSON.parse(options.body || '{}');
        validationCalls.push({url: String(url), requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map(request => request.path === staleDoitPath && !staleDoitExists
            ? {id: request.id, ok: false, status: 404, error: 'path not found'}
            : {id: request.id, ok: true, status: 200, payload: {path: request.path, kind: 'file'}}),
        }));
      });
      assert.ok(validatingDoitApi.commandPaletteItems().some(item => item.category === 'file' && item.path === realDoitPath), 'Cmd-P keeps the matching filesystem candidate visible before path-info validation resolves');
      validatingDoitApi.setCommandPaletteStateForTest('command', 'doit57');
      assert.ok(validatingDoitApi.commandPaletteItems().some(item => item.targetItem === dirtyMissingItem), 'dirty missing tabs remain reachable through Shift-Cmd-P');
      await validatingDoitApi.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      const validatedDoitItems = validatingDoitApi.commandPaletteItems();
      assert.ok(validationCalls.some(call => call.requests.some(request => request.type === 'info' && request.path === staleDoitPath)), 'quick search validates open file tab paths through fs info');
      const validatedStaleDoitRows = validatedDoitItems.filter(item => item.targetItem === validatingDoitItem
        || item.path === staleDoitPath
        || item.key?.includes(staleDoitPath)
        || (item.searchFields || []).includes(staleDoitPath));
      assert.deepStrictEqual(canonical(validatedStaleDoitRows), [], '404-validated stale file paths are removed from quick search results');
      assert.ok(validatedDoitItems.some(item => item.category === 'file' && item.path === realDoitPath), 'the real DOIT.57 file result remains after stale tab validation');
      assert.equal(validatingDoitApi.currentFileStateForTest(staleDoitPath).externalMissing, true, 'the shared file record owns the validated missing state');

      staleDoitExists = true;
      validatingDoitApi.setOpenFileStateForTest(staleDoitPath, {
        ...validatingDoitApi.currentFileStateForTest(staleDoitPath),
        externalMissingCheckedAt: 0,
      });
      validatingDoitApi.commandPaletteItems();
      await validatingDoitApi.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      const recoveredDoitItems = validatingDoitApi.commandPaletteItems();
      assert.ok(recoveredDoitItems.some(item => item.targetItem === validatingDoitItem), 'a recreated clean file tab returns to quick search after shared-state revalidation');
      assert.equal(validatingDoitApi.currentFileStateForTest(staleDoitPath).externalMissing, undefined, 'recreated clean files clear shared missing state');
      assert.equal(validatingDoitApi.currentFileStateForTest(staleDoitPath).content, '# restored\n', 'recreated clean files reload through the normal file-state owner');
    }

    {
      const treeApi = loadYolomux('', ['1']);
      treeApi.setFileExplorerRootMode('fixed', {sync: false});
      treeApi.setFileExplorerRootForTest('/repo');
      treeApi.setFileExplorerDirListingForTest('/repo', [
        {name: 'README.md', kind: 'file'},
        {name: 'src', kind: 'dir'},
      ]);
      treeApi.setFileExplorerDirListingForTest('/repo/src', [
        {name: 'app.js', kind: 'file'},
        {name: 'lib', kind: 'dir'},
      ]);
      treeApi.setFileExplorerDirListingForTest('/repo/src/lib', [
        {name: 'util.js', kind: 'file'},
      ]);
      assert.deepStrictEqual(canonical(await treeApi.fileExplorerDirectoryPathsForRootForTest('/repo')), ['/repo/src', '/repo/src/lib'], 'Finder Expand all collects every directory under the current root through the directory listing cache');
      await treeApi.setAllFileTreeDirectoriesExpandedForTest(null, true);
      assert.deepStrictEqual(canonical(treeApi.fileExplorerExpandedForTest()), ['/repo/src', '/repo/src/lib'], 'Finder Expand all flips the full directory expansion state');
      await treeApi.setAllFileTreeDirectoriesExpandedForTest(null, false);
      assert.deepStrictEqual(canonical(treeApi.fileExplorerExpandedForTest()), [], 'Finder Collapse all clears the directory expansion state');
    }

    {
      const restoredApi = loadYolomux('', ['1']);
      const container = new TestElement('restored-finder-tree');
      restoredApi.setFileExplorerExpandedForTest(['/repo/dynamo']);
      restoredApi.renderTreeChildrenForTest(container, '/repo', [{name: 'dynamo', kind: 'dir'}], 0);
      const row = container.children.find(node => node?.dataset?.path === '/repo/dynamo');
      assert.equal(row.getAttribute('aria-expanded'), 'true', 'a restored expanded directory points down immediately');
      assert.equal(row.classList.contains('loading-children'), true, 'a restored expanded directory with no children rendered yet shows the moving loading indicator');

      restoredApi.renderTreeChildrenForTest(container, '/repo', [{name: 'dynamo', kind: 'dir'}], 0, [
        ['/repo/dynamo', [{name: 'child.txt', kind: 'file'}]],
      ]);
      assert.equal(row.classList.contains('loading-children'), false, 'the restored directory clears the loading indicator once its child listing is available');

      const cachedRestoredApi = loadYolomux('', ['1']);
      const cachedContainer = new TestElement('cached-restored-finder-tree');
      cachedRestoredApi.setFileExplorerExpandedForTest(['/repo/dynamo']);
      cachedRestoredApi.setFileExplorerDirListingForTest('/repo/dynamo', [{name: 'cached-child.txt', kind: 'file'}]);
      cachedRestoredApi.renderTreeChildrenForTest(cachedContainer, '/repo', [{name: 'dynamo', kind: 'dir'}], 0, [], {view: 'finder'});
      const cachedRow = cachedContainer.children.find(node => node?.dataset?.path === '/repo/dynamo');
      const cachedChildren = cachedContainer.children.find(node => node?.dataset?.parent === '/repo/dynamo');
      assert.equal(cachedRow.classList.contains('loading-children'), false, 'a restored Finder directory consumes its retained child listing without a loading turn');
      assert.ok(cachedChildren?.children.some(node => node?.dataset?.path === '/repo/dynamo/cached-child.txt'), 'a restored Finder directory paints its retained nested rows immediately');
    }

    {
      const pendingApi = loadYolomux('', ['1']);
      const container = new TestElement('finder-tree');
      const longPath = '/repo/this/is/a/very/long/path/that/takes/time';
      const parent = longPath.slice(0, longPath.lastIndexOf('/'));
      const name = longPath.slice(longPath.lastIndexOf('/') + 1);
      pendingApi.renderTreeChildrenForTest(container, parent, [{name, kind: 'dir'}], 0);
      const row = container.children.find(node => node?.dataset?.path === longPath);
      let resolveList = null;
      pendingApi.setFetchForTest(url => {
        assert.ok(String(url).startsWith('/api/fs/fast/list?'));
        return new Promise(resolve => {
          resolveList = () => resolve(jsonResponse({path: longPath, entries: [{name: 'child.txt', kind: 'file'}]}));
        });
      });
      const expandPromise = pendingApi.expandDirectoryRowForTest(row, longPath, {manual: true});
      await flushAsyncWork();
      assert.equal(row.getAttribute('aria-expanded'), 'true', 'Finder directory shows expanded immediately while the backend listing is pending');
      assert.equal(row.classList.contains('loading-children'), true, 'Finder directory shows a pending expansion spinner while listing is in flight');
      assert.ok(resolveList, 'directory expansion issued the direct one-level listing request');
      resolveList();
      await expandPromise;
      assert.equal(row.getAttribute('aria-expanded'), 'true', 'Finder directory remains expanded after the backend listing resolves');
      assert.equal(row.classList.contains('loading-children'), false, 'Finder directory clears the pending spinner after the backend listing resolves');
      assert.ok(container.children.some(node => node?.classList?.contains('file-tree-children') && node.dataset.parent === longPath), 'resolved directory listing renders the child container');
    }

    {
      const syncTreeApi = loadYolomux('', ['1']);
      syncTreeApi.setFileExplorerRootMode('sync', {sync: false});
      syncTreeApi.setFileExplorerRootForTest('/home/test');
      syncTreeApi.setTranscriptInfoForTest('1', {
        project: {git: {cwd: '/home/test/yolomux.dev2', root: '/home/test/yolomux.dev2'}},
        selected_pane: {current_path: '/home/test/yolomux.dev2'},
      });
      syncTreeApi.setFinderSessionFilesPayloadForTest({
        session: '1',
        repos: [{repo: '/home/test/yolomux.dev2'}, {repo: '/home/test/ai-config'}],
        files: [
          {repo: '/home/test/yolomux.dev2', path: 'static_src/js/app.js', abs_path: '/home/test/yolomux.dev2/static_src/js/app.js'},
          {repo: '/home/test/ai-config', path: 'hooks/install.js', abs_path: '/home/test/ai-config/hooks/install.js'},
        ],
      });
      syncTreeApi.setFileExplorerDirListingForTest('/home/test', [
        {name: 'ai-config', kind: 'dir'},
        {name: 'unrelated', kind: 'dir'},
        {name: 'yolomux.dev2', kind: 'dir'},
      ]);
      syncTreeApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev2', [
        {name: 'static_src', kind: 'dir'},
      ]);
      syncTreeApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev2/static_src', [
        {name: 'js', kind: 'dir'},
      ]);
      syncTreeApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev2/static_src/js', [
        {name: 'app.js', kind: 'file'},
      ]);
      syncTreeApi.setFileExplorerDirListingForTest('/home/test/ai-config', [
        {name: 'hooks', kind: 'dir'},
      ]);
      syncTreeApi.setFileExplorerDirListingForTest('/home/test/ai-config/hooks', [
        {name: 'install.js', kind: 'file'},
      ]);
      await syncTreeApi.setAllFileTreeDirectoriesExpandedForTest(null, true);
      assert.deepStrictEqual(canonical(syncTreeApi.fileExplorerExpandedForTest()), [
        '/home/test/ai-config',
        '/home/test/ai-config/hooks',
        '/home/test/yolomux.dev2',
        '/home/test/yolomux.dev2/static_src',
        '/home/test/yolomux.dev2/static_src/js',
      ], 'Finder Sync Expand expands affected paths without crawling unrelated home directories');
    }

    {
      const api = loadYolomux('', ['1', '2']);
      api.setFileExplorerRootMode('sync', {sync: false});
      api.setFileExplorerRootForTest('/home/test');
      api.setTranscriptInfoForTest('1', {
        project: {git: {cwd: '/home/test/project/1/src', root: '/home/test/project/1'}},
        selected_pane: {current_path: '/home/test/project/1/src'},
      });
      api.setFinderSessionFilesPayloadForTest({
        session: '1',
        repos: [{repo: '/home/test/project/1'}, {repo: '/home/test/project/2'}],
        files: [
          {repo: '/home/test/project/1', path: 'src/app.js', abs_path: '/home/test/project/1/src/app.js'},
          {repo: '/home/test/project/2', path: 'README.md', abs_path: '/home/test/project/2/README.md'},
        ],
      });
      assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
        session: '1',
        root: '/home/test',
        expandPaths: [
          '/home/test/project',
          '/home/test/project/1',
          '/home/test/project/2',
          '/home/test/project/1/src',
        ],
        affectedDirs: [
          '/home/test/project/1',
          '/home/test/project/2',
          '/home/test/project/1/src',
        ],
      }, 'Finder sync opens home for home-contained tabs and expands the full active-tab working chain under it');

      api.setTranscriptInfoForTest('2', {
        project: {git: {cwd: '/tmp/outside/src', root: '/tmp/outside'}},
        selected_pane: {current_path: '/tmp/outside/src'},
      });
      api.setFinderSessionFilesPayloadForTest({
        session: '2',
        repos: [{repo: '/tmp/outside'}, {repo: '/home/test/project/3'}],
        files: [
          {repo: '/tmp/outside', path: 'src/app.js', abs_path: '/tmp/outside/src/app.js'},
          {repo: '/home/test/project/3', path: 'README.md', abs_path: '/home/test/project/3/README.md'},
        ],
      });
      assert.equal(api.fileExplorerSyncPlanForTest('2').root, '/tmp/outside', 'mixed home and outside-home working paths outside home use the focused repo root');
    }

    {
      const api = loadYolomux('', ['1', '2']);
      api.setFileExplorerRootMode('sync', {sync: false});
      api.setFileExplorerDirListingForTest('/home/test', [{name: 'project', kind: 'dir'}]);
      api.setFileExplorerDirListingForTest('/home/test/project', [{name: '1', kind: 'dir'}, {name: '2', kind: 'dir'}, {name: '3', kind: 'dir'}]);
      api.setFileExplorerDirListingForTest('/home/test/project/1', [{name: 'src', kind: 'dir'}]);
      api.setFileExplorerDirListingForTest('/home/test/project/1/src', [{name: 'app.js', kind: 'file'}]);
      api.setFileExplorerDirListingForTest('/home/test/project/2', [{name: 'README.md', kind: 'file'}]);
      api.setFileExplorerDirListingForTest('/home/test/project/3', [{name: 'README.md', kind: 'file'}]);
      assert.equal(await api.openFileExplorerAtForTest('/home/test', {syncSelection: true}), true);

      api.setTranscriptInfoForTest('1', {
        project: {git: {cwd: '/home/test/project/1/src', root: '/home/test/project/1'}},
        selected_pane: {current_path: '/home/test/project/1/src'},
      });
      api.setFinderSessionFilesPayloadForTest({
        session: '1',
        repos: [{repo: '/home/test/project/1'}, {repo: '/home/test/project/2'}],
        files: [
          {repo: '/home/test/project/1', path: 'src/app.js', abs_path: '/home/test/project/1/src/app.js'},
          {repo: '/home/test/project/2', path: 'README.md', abs_path: '/home/test/project/2/README.md'},
        ],
      });
      api.scheduleFileExplorerActiveTabSyncForTest('1', {explicit: true});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(api.fileExplorerRootForTest(), '/home/test', 'syncing a home tab opens the home root');
      assert.equal(api.fileExplorerPathDisplayForTest(), '~', 'the Finder path display shows the home-compacted sync root');
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [
        '/home/test/project',
        '/home/test/project/1',
        '/home/test/project/1/src',
        '/home/test/project/2',
      ], 'sync expands the full working chain for the focused home tab');

      const collapseParent = new TestElement('collapse-parent');
      const collapseRow = new TestElement('collapse-row');
      collapseParent.appendChild(collapseRow);
      api.collapseDirectoryRowForTest(collapseRow, '/home/test/project/1', {manual: true});
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [
        '/home/test/project',
        '/home/test/project/1/src',
        '/home/test/project/2',
      ], 'manual collapse removes the collapsed folder from the active tab expanded set');

      api.setTranscriptInfoForTest('2', {
        project: {git: {cwd: '/home/test/project/3', root: '/home/test/project/3'}},
        selected_pane: {current_path: '/home/test/project/3'},
      });
      api.setFinderSessionFilesPayloadForTest({
        session: '2',
        repos: [{repo: '/home/test/project/3'}],
        files: [{repo: '/home/test/project/3', path: 'README.md', abs_path: '/home/test/project/3/README.md'}],
      });
      api.scheduleFileExplorerActiveTabSyncForTest('2', {explicit: true});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [
        '/home/test/project',
        '/home/test/project/3',
      ], 'focusing another home tab swaps to that tab chain without carrying previous expanded folders');

      api.setFinderSessionFilesPayloadForTest({
        session: '1',
        repos: [{repo: '/home/test/project/1'}, {repo: '/home/test/project/2'}],
        files: [
          {repo: '/home/test/project/1', path: 'src/app.js', abs_path: '/home/test/project/1/src/app.js'},
          {repo: '/home/test/project/2', path: 'README.md', abs_path: '/home/test/project/2/README.md'},
        ],
      });
      api.scheduleFileExplorerActiveTabSyncForTest('1', {explicit: true});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [
        '/home/test/project',
        '/home/test/project/2',
      ], 'returning to the tab restores its in-memory state without auto-reopening the manually collapsed working folder');
    }

    {
      const api = loadYolomux('', ['1']);
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.leafNode('left');
      slots.left = api.paneStateWithTabs(['1'], '1');
      api.setLayoutSlotsForTest(slots);

      const sent = [];
      api.registerTerminalForTest('1', {focus() {}}, {
        readyState: 1,
        send(message) {
          sent.push(JSON.parse(message));
        },
      });

      const generatedName = `${expectedPacificDateStamp()}-001.png`;
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body});
        if (String(url).startsWith('/api/upload')) {
          return Promise.resolve(jsonResponse({files: [{path: `/home/test/${generatedName}`}]}));
        }
        if (String(url).startsWith('/api/session-metadata')) {
          return Promise.resolve(jsonResponse({session_order: ['1'], sessions: {'1': {agents: []}}}));
        }
        if (String(url).startsWith('/api/auto-approve')) {
          return Promise.resolve(jsonResponse({sessions: {}}));
        }
        return Promise.resolve(jsonResponse({items: [], session: '1'}));
      });

      // DOIT.57 regression: a pasted image must ALWAYS insert its path reference, even with the suggestion
      // overlay on (default). The overlay is additive (it appends a clause); it never replaces the insert.
      // This pane has no agent, so no overlay rows render — only the path insert is asserted here.
      api.setClientSettingsPatchForTest({uploads: {show_suggestions: true}});
      api.bindClipboardPasteForTest();
      api.bindClipboardPasteForTest();
      const pasteListeners = api.documentListenersForTest('paste');
      assert.equal(pasteListeners.length, 1, 'image paste installs one document paste listener');

      const pasteEvent = {
        clipboardData: {
          items: [{
            kind: 'file',
            type: 'image/png',
            getAsFile() {
              return {name: 'image.png', type: 'image/png', size: 7};
            },
          }],
        },
        target: null,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
      };
      pasteListeners[0](pasteEvent);
      await flushAsyncWork();

      assert.equal(pasteEvent.defaultPrevented, true, 'image paste is captured before xterm receives raw clipboard data');
      assert.equal(pasteEvent.propagationStopped, true, 'image paste stops propagation after starting upload');
      assert.equal(calls[0].url, '/api/upload?session=1', 'image paste uploads to the active terminal session');
      assert.equal(calls[0].method, 'POST');
      assert.equal(calls[0].body.fields[0].name, 'files');
      assert.equal(calls[0].body.fields[0].filename, generatedName);
      assert.deepStrictEqual(sent[0], {
        type: 'input',
        data: `[Image #1] '/home/test/${generatedName}'`,
      }, 'pasted image upload inserts the image reference into xterm without trailing whitespace');
      assert.equal(
        calls.some(call => String(call.url).startsWith('/api/session-metadata')),
        false,
        'pasted image upload does not force a transcript/session-metadata rescan on the latency-sensitive path',
      );
    }

    // Negative control for the Pacific upload stamp. Pinning docker/Dockerfile.test to
    // America/Los_Angeles must not be able to hide a real date bug, so prove the filename assertion
    // above is still date-sensitive. Pacific/Kiritimati (UTC+14) and Etc/GMT+12 (UTC-12) are 26 hours
    // apart, so at every instant at least one of them is on a different calendar day than Pacific.
    // Forcing that zone as the process TZ moves every ambient new Date() — including inside the
    // vm-loaded bundle, which shares this isolate's timezone cache — while leaving the product's
    // explicit Intl timeZone formatter alone. If the product ever regressed from pacificDateStamp() to
    // a bare new Date(), the uploaded filename would follow the control clock and this test goes red.
    await testAsync('generated upload filenames stay on the Pacific calendar day when the runner clock is not', async () => {
      const pacificStamp = expectedPacificDateStamp();
      const originalTimeZone = process.env.TZ;
      try {
        const controlZone = ['Pacific/Kiritimati', 'Etc/GMT+12'].find(zone => {
          process.env.TZ = zone;
          return ambientDateStamp() !== pacificStamp;
        });
        assert.ok(controlZone, 'two zones 26 hours apart always straddle the Pacific calendar day');
        assert.notEqual(ambientDateStamp(), pacificStamp, 'the control clock is genuinely a different calendar day, so this assertion can still fail');

        const api = loadYolomux('', ['1']);
        const slots = api.emptyLayoutSlots();
        slots[api.layoutTreeKey] = api.leafNode('left');
        slots.left = api.paneStateWithTabs(['1'], '1');
        api.setLayoutSlotsForTest(slots);
        api.registerTerminalForTest('1', {focus() {}}, {readyState: 1, send() {}});

        const calls = [];
        api.setFetchForTest((url, options = {}) => {
          calls.push({url: String(url), method: options.method || 'GET', body: options.body});
          if (String(url).startsWith('/api/upload')) {
            return Promise.resolve(jsonResponse({files: [{path: `/home/test/${pacificStamp}-001.png`}]}));
          }
          return Promise.resolve(jsonResponse({items: [], session: '1'}));
        });

        api.bindClipboardPasteForTest();
        const pasteListeners = api.documentListenersForTest('paste');
        pasteListeners[0]({
          clipboardData: {
            items: [{
              kind: 'file',
              type: 'image/png',
              getAsFile() {
                return {name: 'image.png', type: 'image/png', size: 7};
              },
            }],
          },
          target: null,
          defaultPrevented: false,
          propagationStopped: false,
          preventDefault() { this.defaultPrevented = true; },
          stopPropagation() { this.propagationStopped = true; },
        });
        await flushAsyncWork();

        const uploadCall = calls.find(call => call.url.startsWith('/api/upload'));
        assert.ok(uploadCall, 'the control-clock paste still reaches the upload endpoint');
        assert.equal(
          uploadCall.body.fields[0].filename,
          `${pacificStamp}-001.png`,
          'the generated upload name follows the Pacific calendar day, not the ambient runner clock',
        );
      } finally {
        if (originalTimeZone === undefined) delete process.env.TZ;
        else process.env.TZ = originalTimeZone;
      }
    });

    // DOIT.78 payload-matrix contract (78.5): the ONE shared image-payload detector/extractor used by BOTH
    // paste and drop must recognize EVERY browser exposure (File item, plain File list, image MIME type,
    // rich text/html <img>) and extract every image — so no exposure slips past the claim and leaks as an
    // attachment. Headless clipboard/drag image injection is unreliable, so the shared logic (not a flaky
    // Selenium clipboard test) is the regression surface; live event wiring is covered by the paste
    // contracts below + the source-grep invariant.
    {
      const api = loadYolomux();
      const dt = over => ({
        items: over.items || [],
        files: over.files || [],
        types: over.types || [],
        getData(type) { return (over.data || {})[type] || ''; },
      });
      const fileItem = (type = 'image/png') => ({kind: 'file', type, getAsFile() { return {name: 'x', type, size: 4}; }});
      const has = api.dataTransferHasImagePayloadForTest;
      const files = api.dataTransferImageFilesForTest;
      assert.equal(has(dt({items: [fileItem()]})), true, '78.5: image File item is image-bearing');
      assert.equal(has(dt({files: [{type: 'image/png'}]})), true, '78.5: plain image File list is image-bearing');
      assert.equal(has(dt({types: ['image/png']})), true, '78.5: image MIME type is image-bearing');
      assert.equal(has(dt({types: ['text/html'], data: {'text/html': '<img src="https://x/y.png">'}})), true, '78.5: rich text/html <img> is image-bearing');
      assert.equal(has(dt({items: [fileItem('image/png'), fileItem('image/jpeg')]})), true, '78.5: multiple image items are image-bearing');
      assert.equal(has(dt({types: ['text/plain'], data: {'text/plain': 'hello'}})), false, '78.5: plain text is not image-bearing');
      assert.equal(has(dt({items: [{kind: 'file', type: 'application/pdf', getAsFile() { return {name: 'a.pdf', type: 'application/pdf'}; }}]})), false, '78.5: a non-image file item is not image-bearing');
      assert.equal(has(null), false, '78.5: missing payload is not image-bearing');
      assert.equal(files(dt({items: [fileItem(), fileItem('image/jpeg')]})).length, 2, '78.5: extracts every image File item (multi-image)');
      assert.equal(files(dt({files: [{type: 'image/png', name: 'p.png'}, {type: 'text/plain', name: 'n.txt'}]})).length, 1, '78.5: extracts only image entries from a plain File list');
      assert.equal(files(dt({types: ['text/html'], data: {'text/html': '<img src="data:image/png;base64,AAAA">'}})).length, 1, '78.5: extracts image data URLs from rich text/html');
    }

    // DOIT.78 (78.1): an image pasted as RICH DATA (text/html <img>, NO File) must still be CLAIMED
    // (preventDefault + stopPropagation) so the raw image cannot leak to the agent as an attachment.
    {
      const api = loadYolomux('', ['1']);
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.leafNode('left');
      slots.left = api.paneStateWithTabs(['1'], '1');
      api.setLayoutSlotsForTest(slots);
      api.registerTerminalForTest('1', {focus() {}}, {readyState: 1, send() {}});
      api.setFetchForTest(() => Promise.resolve(jsonResponse({items: [], session: '1'})));
      api.bindClipboardPasteForTest();
      const pasteListeners = api.documentListenersForTest('paste');
      const richPaste = {
        clipboardData: {
          items: [],
          types: ['text/html'],
          getData(type) { return type === 'text/html' ? '<img src="https://example.com/x.png" alt="">' : ''; },
        },
        target: null,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
      };
      pasteListeners[0](richPaste);
      await flushAsyncWork();
      assert.equal(richPaste.defaultPrevented, true, '78.1: rich-data image paste is claimed so it cannot leak to the agent as an attachment');
      assert.equal(richPaste.propagationStopped, true, '78.1: rich-data image paste stops propagation once claimed');
    }

    // DOIT.78 (78.4): pasting MULTIPLE image Files in one event uploads ALL of them and inserts a text
    // reference for each — never one ref + one attachment.
    {
      const api = loadYolomux('', ['1']);
      const slots = api.emptyLayoutSlots();
      slots[api.layoutTreeKey] = api.leafNode('left');
      slots.left = api.paneStateWithTabs(['1'], '1');
      api.setLayoutSlotsForTest(slots);
      const sent = [];
      api.registerTerminalForTest('1', {focus() {}}, {readyState: 1, send(message) { sent.push(JSON.parse(message)); }});
      api.setFetchForTest(url => {
        if (String(url).startsWith('/api/upload')) return Promise.resolve(jsonResponse({files: [{path: '/home/test/multi-a.png'}, {path: '/home/test/multi-b.png'}]}));
        return Promise.resolve(jsonResponse({items: [], session: '1'}));
      });
      api.setClientSettingsPatchForTest({uploads: {show_suggestions: false}});
      api.bindClipboardPasteForTest();
      const pasteListeners = api.documentListenersForTest('paste');
      const imageItem = name => ({kind: 'file', type: 'image/png', getAsFile() { return {name, type: 'image/png', size: 7}; }});
      const multiPaste = {
        clipboardData: {items: [imageItem('a.png'), imageItem('b.png')], types: ['Files'], getData() { return ''; }},
        target: null,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
      };
      pasteListeners[0](multiPaste);
      await flushAsyncWork();
      assert.equal(multiPaste.defaultPrevented, true, '78.4: multi-image paste is claimed');
      const allSent = sent.map(message => message.data).join('');
      assert.ok(allSent.includes('multi-a.png') && allSent.includes('multi-b.png'), '78.4: both pasted images become text references in the terminal (no attachment)');
    }

    // Markdown editor image paste inserts the server-owned absolute central-upload paths into CodeMirror.
    {
      const api = loadYolomux('', ['1']);
      const sent = [];
      api.registerTerminalForTest('1', {focus() {}}, {readyState: 1, send(message) { sent.push(JSON.parse(message)); }});
      api.setFocusedTerminal('1');
      const path = '/repo/docs/note.md';
      const item = api.fileEditorItemFor(path);
      api.registerFileEditorLayoutItemForTest(path, {item});
      api.setOpenFileStateForTest(path, {kind: 'text', original: 'hello\n', content: 'hello\n', dirty: false});
      let content = 'hello\n';
      let focused = false;
      const view = {
        state: {doc: {length: content.length}, selection: {main: {from: content.length, to: content.length}}},
        dispatch(transaction) {
          const change = transaction.changes;
          content = `${content.slice(0, change.from)}${change.insert}${content.slice(change.to)}`;
          this.state.doc.length = content.length;
          this.state.selection.main = {from: transaction.selection.anchor, to: transaction.selection.anchor};
        },
        focus() { focused = true; },
      };
      const panel = new TestElement('panel-editor-note');
      panel.className = 'panel file-editor-panel';
      panel.dataset.filePath = path;
      panel.dataset.layoutItem = item;
      panel._cmView = view;
      panel._cmMode = 'edit';
      const cmTarget = new TestElement('cm-target');
      panel.appendChild(cmTarget);
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body});
        if (String(url).startsWith('/api/upload')) {
          return Promise.resolve(jsonResponse({files: [
            {path: '/tmp/yolomux.alice/uploads/editor/one.png', relative_path: '/tmp/yolomux.alice/uploads/editor/one.png'},
            {path: '/tmp/yolomux.alice/uploads/editor/two file.png', relative_path: '/tmp/yolomux.alice/uploads/editor/two file.png'},
          ]}));
        }
        return Promise.resolve(jsonResponse({items: [], session: '1'}));
      });
      api.bindClipboardPasteForTest();
      const pasteListeners = api.documentListenersForTest('paste');
      const imageItem = name => ({kind: 'file', type: 'image/png', getAsFile() { return {name, type: 'image/png', size: 7}; }});
      const pasteEvent = {
        clipboardData: {items: [imageItem('one.png'), imageItem('two.png')], types: ['Files'], getData() { return ''; }},
        target: cmTarget,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
      };
      pasteListeners[0](pasteEvent);
      await flushAsyncWork();
      assert.equal(pasteEvent.defaultPrevented, true, 'Markdown editor image paste is claimed before terminal handling');
      assert.equal(pasteEvent.propagationStopped, true, 'Markdown editor image paste stops propagation');
      assert.equal(calls[0].url, `/api/upload?editor_path=${encodeURIComponent(path)}`, 'Markdown editor paste uploads with the editor path');
      assert.equal(calls[0].method, 'POST');
      assert.equal(calls[0].body.fields.length, 2, 'Markdown editor paste uploads every image');
      assert.equal(content, 'hello\n![image](/tmp/yolomux.alice/uploads/editor/one.png)\n![image](/tmp/yolomux.alice/uploads/editor/two%20file.png)', 'Markdown editor paste inserts absolute central-upload links at the cursor');
      assert.equal(focused, true, 'Markdown editor paste restores CodeMirror focus');
      assert.equal(sent.length, 0, 'Markdown editor paste never sends raw image data to xterm');
    }

    // Rich remote images are still claimed for Markdown editors even when no uploadable File can be extracted.
    {
      const api = loadYolomux('', ['1']);
      const sent = [];
      api.registerTerminalForTest('1', {focus() {}}, {readyState: 1, send(message) { sent.push(JSON.parse(message)); }});
      api.setFocusedTerminal('1');
      const path = '/repo/docs/note.md';
      const item = api.fileEditorItemFor(path);
      api.registerFileEditorLayoutItemForTest(path, {item});
      const panel = new TestElement('panel-editor-remote');
      panel.className = 'panel file-editor-panel';
      panel.dataset.filePath = path;
      panel.dataset.layoutItem = item;
      panel._cmView = {state: {doc: {length: 0}, selection: {main: {from: 0, to: 0}}}, dispatch() {}};
      panel._cmMode = 'edit';
      const cmTarget = new TestElement('cm-remote-target');
      panel.appendChild(cmTarget);
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET'});
        return Promise.resolve(jsonResponse({items: [], session: '1'}));
      });
      api.bindClipboardPasteForTest();
      const pasteEvent = {
        clipboardData: {
          items: [],
          types: ['text/html'],
          getData(type) { return type === 'text/html' ? '<img src="https://example.com/remote.png">' : ''; },
        },
        target: cmTarget,
        defaultPrevented: false,
        propagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
      };
      api.documentListenersForTest('paste')[0](pasteEvent);
      await flushAsyncWork();
      assert.equal(pasteEvent.defaultPrevented, true, 'remote Markdown image paste is claimed');
      assert.equal(pasteEvent.propagationStopped, true, 'remote Markdown image paste stops propagation');
      assert.equal(calls.length, 0, 'remote Markdown image paste does not upload without an extractable File');
      assert.equal(sent.length, 0, 'remote Markdown image paste never leaks to xterm');
    }

    // Non-Markdown editors do not steal the terminal image paste path.
    {
      const api = loadYolomux('', ['1']);
      const sent = [];
      api.registerTerminalForTest('1', {focus() {}}, {readyState: 1, send(message) { sent.push(JSON.parse(message)); }});
      api.setFocusedTerminal('1');
      const path = '/repo/src/app.py';
      const panel = new TestElement('panel-editor-python');
      panel.className = 'panel file-editor-panel';
      panel.dataset.filePath = path;
      panel.dataset.layoutItem = api.fileEditorItemFor(path);
      panel._cmView = {state: {doc: {length: 0}, selection: {main: {from: 0, to: 0}}}, dispatch() {}};
      panel._cmMode = 'edit';
      const cmTarget = new TestElement('cm-python-target');
      panel.appendChild(cmTarget);
      const calls = [];
      api.setFetchForTest(url => {
        calls.push(String(url));
        if (String(url).startsWith('/api/upload')) return Promise.resolve(jsonResponse({files: [{path: '/repo/.uploads/python.png'}]}));
        return Promise.resolve(jsonResponse({items: [], session: '1'}));
      });
      api.setClientSettingsPatchForTest({uploads: {show_suggestions: false}});
      api.bindClipboardPasteForTest();
      const imageItem = {kind: 'file', type: 'image/png', getAsFile() { return {name: 'python.png', type: 'image/png', size: 7}; }};
      const pasteEvent = {
        clipboardData: {items: [imageItem], types: ['Files'], getData() { return ''; }},
        target: cmTarget,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
      };
      api.documentListenersForTest('paste')[0](pasteEvent);
      await flushAsyncWork();
      assert.equal(calls[0], '/api/upload?session=1', 'non-Markdown editor paste falls back to terminal upload');
      assert.ok(sent.some(message => String(message.data || '').includes('/repo/.uploads/python.png')), 'non-Markdown editor paste keeps terminal reference insertion');
    }

    // DOIT.78 (78.6): invariant guard — paste and drop must route through the ONE shared image-payload
    // detector so a new entry point can't reintroduce a divergent leak path.
    {
      const imgSource = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(/scope\.ownEvent\('paste', document, 'paste', event => \{\s*if \(!dataTransferHasImagePayload\(event\.clipboardData\)\) return;[\s\S]*markdownEditorPasteTarget\(event\)/.test(imgSource), '78.6: the document paste handler claims via the shared dataTransferHasImagePayload detector before editor or terminal routing');
      assert.ok(imgSource.includes('function hasUploadableDrag(event)') && /addEventListener\('drop', event => \{\s*if \(!hasUploadableDrag\(event\)\) return;/.test(imgSource), '78.6: the file-drop handler claims via hasUploadableDrag (file OR image rich-data)');
      assert.ok(imgSource.includes('function dataTransferImageFiles(dt)') && imgSource.includes('function dataTransferHasImagePayload(dt)'), '78.6: the shared image-payload parent exists');
      assert.ok(/const files = dataTransferImageFiles\(event\.clipboardData\);[\s\S]*uploadEditorFiles\(editorTarget, files\)/.test(imgSource), '78.6: Markdown editor paste uploads through the shared image-payload extractor');
    }

    {
      const api = loadYolomux();
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET'});
        return Promise.resolve(jsonResponse({path: '/home/test', entries: [{name: 'TODO.md', kind: 'file'}]}));
      });
      const first = api.fetchDirectoryForTest('/home/test', {trigger: 'tree-render'});
      const second = api.fetchDirectoryForTest('/home/test/', {trigger: 'watch-diff-fallback'});
      await api.flushFileExplorerFsBatchForTest();
      assert.deepStrictEqual(canonical(calls), [{
        method: 'GET',
        url: '/api/fs/fast/list?path=%2Fhome%2Ftest',
      }], 'concurrent identical directory listings share one direct fast request');
      const [firstEntries, secondEntries] = await Promise.all([first, second]);
      assert.strictEqual(firstEntries, secondEntries, 'shared directory listing callers receive the same entries object');
      assert.equal(firstEntries[0].name, 'TODO.md');
      const cachedEntries = await api.fetchDirectoryForTest('/home/test');
      assert.strictEqual(cachedEntries, firstEntries, 'completed directory listing is reused by the short TTL cache');
      assert.equal(calls.length, 1, 'short TTL cache avoids an immediate repeat directory listing');
    }

    {
      const api = loadYolomux();
      const batches = [];
      api.setFetchForTest(url => {
        assert.equal(String(url), '/api/fs/fast/list?path=%2Fhome%2Ftest%2Fbootstrap');
        const batch = deferredFetch();
        batches.push(batch);
        return batch.promise;
      });
      const path = '/home/test/bootstrap';
      const container = new TestElement('finder-tree');
      api.renderTreeChildrenForTest(container, '/home/test', [{name: 'bootstrap', kind: 'dir'}], 0);
      const row = container.children.find(node => node?.dataset?.path === path);
      const background = api.fetchDirectoryForTest(path, {trigger: 'tree-render'});
      await flushAsyncWork();
      assert.equal(batches.length, 1, 'the bootstrap list is already in flight');
      const user = api.onFileTreeRowClick(row, path, {name: 'bootstrap', kind: 'dir'}, {});
      await flushAsyncWork();
      assert.equal(batches.length, 2, 'a user list gets one successor direct GET instead of inheriting the bootstrap wait');
      batches[1].resolve(jsonResponse({path, entries: [{name: 'clicked.txt', kind: 'file'}]}));
      await user;
      assert.equal(row.classList.contains('loading-children'), false, 'the click settles before the bootstrap response');
      batches[0].resolve(jsonResponse({path, entries: [{name: 'bootstrap.txt', kind: 'file'}]}));
      assert.equal((await background)[0].name, 'bootstrap.txt', 'the background request remains independently observable');
    }

    {
      const api = loadYolomux();
      const calls = [];
      api.setDocumentVisibilityForTest('hidden');
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET'});
        return Promise.resolve(jsonResponse({path: '/home/hidden', entries: [{name: 'visible.txt', kind: 'file'}]}));
      });
      assert.equal(await api.fetchDirectoryForTest('/home/hidden'), null, 'hidden pages skip background Finder directory fetches');
      assert.deepStrictEqual(canonical(calls), [], 'hidden background Finder fetches do not enqueue /api/fs/batch');

      const userFetch = api.fetchDirectoryForTest('/home/hidden', {user: true});
      await api.flushFileExplorerFsBatchForTest();
      assert.equal((await userFetch)[0].name, 'visible.txt');
      assert.deepStrictEqual(canonical(calls), [{
        method: 'GET',
        url: '/api/fs/fast/list?path=%2Fhome%2Fhidden',
      }], 'explicit user Finder fetches bypass hidden-background suppression');
    }

    {
      const api = loadYolomux();
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET'});
        return Promise.resolve(jsonResponse({path: '/home/test', entries: [{name: 'a.txt', kind: 'file'}]}));
      });
      const first = api.fetchDirectoryForTest('/home/test', {fresh: true});
      const second = api.fetchDirectoryForTest('/home/test', {fresh: true});
      await api.flushFileExplorerFsBatchForTest();
      assert.deepStrictEqual(canonical(calls), [{
        method: 'GET',
        url: '/api/fs/fast/list?path=%2Fhome%2Ftest',
      }], 'concurrent fresh directory listings bypass stale values but share one in-flight backend request');
      const [firstEntries, secondEntries] = await Promise.all([first, second]);
      assert.equal(firstEntries[0].name, 'a.txt');
      assert.strictEqual(secondEntries, firstEntries, 'fresh coalesced callers share the same response object');
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFileExplorerExpandedForTest(['/repo/src', '/repo/src/js', '/repo/tests']);
      api.setFetchForTest((url, options = {}) => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        calls.push({url: String(url), method: options.method || 'GET'});
        const entries = path === '/repo'
          ? [{name: 'src', kind: 'dir'}, {name: 'tests', kind: 'dir'}]
          : (path === '/repo/src' ? [{name: 'js', kind: 'dir'}] : [{name: 'child', kind: 'file'}]);
        return Promise.resolve(jsonResponse({path, entries}));
      });
      const entriesPromise = api.fileExplorerEntriesByWatchedDirectoryForTest('/repo');
      await api.flushFileExplorerFsBatchForTest();
      const entriesByDir = await entriesPromise;
      assert.deepStrictEqual(canonical(calls.map(call => call.url)), [
        '/api/fs/fast/list?path=%2Frepo',
        '/api/fs/fast/list?path=%2Frepo%2Fsrc',
        '/api/fs/fast/list?path=%2Frepo%2Ftests',
        '/api/fs/fast/list?path=%2Frepo%2Fsrc%2Fjs',
      ], 'watched Finder/Differ directories use explicit one-level fast GETs in breadth-first order');
      assert.deepStrictEqual(canonical(Array.from(entriesByDir.keys()).sort()), ['/repo', '/repo/src', '/repo/src/js', '/repo/tests']);
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFileExplorerExpandedForTest(['/repo/STATUS-REPORT.md']);
      api.setFetchForTest(url => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        calls.push(path);
        if (path === '/repo') {
          return Promise.resolve(jsonResponse({path, entries: [{name: 'STATUS-REPORT.md', kind: 'file'}]}));
        }
        return Promise.resolve(jsonResponse({
          error: 'not a directory',
          user_message: {key: 'fs.error.notDirectory', params: {path}, fallback: 'not a directory'},
        }, 400));
      });
      await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
      assert.deepStrictEqual(canonical(calls), ['/repo'], 'an authoritative parent file entry never reaches the directory-listing route');
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'a restored file path is retired from directory disclosure state');
    }

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFileExplorerExpandedForTest(['/repo/gone', '/repo/gone/nested']);
      api.setFileExplorerSyncUserExpansionForTest('/repo/gone', true);
      api.rememberFileExplorerSyncExpandedStateForTest('1', '/repo');
      api.setFetchForTest(url => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        calls.push(path);
        if (path === '/repo') return Promise.resolve(jsonResponse({path, entries: [{name: 'gone', kind: 'dir'}]}));
        return Promise.resolve(jsonResponse({
          error: 'path not found',
          user_message: {key: 'common.pathNotFound', params: {path}, fallback: 'path not found'},
        }, 404));
      });
      await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
      await api.fetchDirectoryForTest('/repo/gone', {fresh: true});
      await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
      assert.equal(calls.filter(path => path === '/repo/gone').length, 1, 'a terminal missing-directory result suppresses repeated background demand');
      assert.equal(calls.includes('/repo/gone/nested'), false, 'a missing ancestor prevents descendant listing demand');
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'a missing directory and its descendants are retired from disclosure state');
      assert.deepStrictEqual(canonical(api.fileExplorerSyncUserExpansionStateForTest()), [], 'terminal retirement clears the persistent user-expansion mirror');
      assert.deepStrictEqual(canonical(api.fileExplorerSyncTargetRecordForTest('1\x1f/repo').expandedPaths), [], 'terminal retirement clears remembered sync-target disclosure state');
      const negativeRecord = api.fileExplorerFsResourceRecordsForTest().find(record => record.key === 'list\x1f/repo/gone');
      assert.equal(negativeRecord.failureStatus, 404, 'the shared filesystem resource record retains the terminal negative result');
      assert.ok(negativeRecord.retryAt > Date.now(), 'the terminal negative result carries a bounded retry deadline');
      await api.fetchDirectoryForTest('/repo/gone', {fresh: true, user: true});
      assert.equal(calls.filter(path => path === '/repo/gone').length, 2, 'explicit user demand may bypass the background negative backoff once');
    }

    {
      const api = loadYolomux('', ['1']);
      const staleChild = deferredFetch();
      api.setFileExplorerRootForTest('/repo');
      api.setFileExplorerExpandedForTest(['/repo/file']);
      api.setFetchForTest(url => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        if (path === '/repo/file') return staleChild.promise;
        if (path === '/repo') return Promise.resolve(jsonResponse({path, entries: [{name: 'file', kind: 'file'}]}));
        return Promise.reject(new Error(`unexpected listing ${path}`));
      });
      const oldRequest = api.fetchDirectoryForTest('/repo/file', {fresh: true});
      await flushAsyncWork();
      await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
      staleChild.resolve(jsonResponse({path: '/repo/file', entries: [{name: 'stale.txt', kind: 'file'}]}));
      await oldRequest;
      const record = api.fileExplorerFsResourceRecordsForTest().find(candidate => candidate.key === 'list\x1f/repo/file');
      assert.equal(record.failureStatus, 400, 'a newer parent classification keeps ownership after an old child response settles');
      assert.equal(record.hasValue, false, 'the stale child response cannot republish a directory listing after file classification');
    }

    {
      const api = loadYolomux('', ['1']);
      const heldDescendant = deferredFetch();
      let descendantRequests = 0;
      api.setFileExplorerRootForTest('/repo');
      api.setFileExplorerExpandedForTest(['/repo/dir', '/repo/dir/sub']);
      api.setFetchForTest(url => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        if (path === '/repo/dir/sub') {
          descendantRequests += 1;
          return descendantRequests === 1
            ? heldDescendant.promise
            : Promise.resolve(jsonResponse({path, entries: [{name: 'fresh.txt', kind: 'file'}]}));
        }
        if (path === '/repo') return Promise.resolve(jsonResponse({path, entries: [{name: 'dir', kind: 'file'}]}));
        return Promise.reject(new Error(`unexpected listing ${path}`));
      });
      const staleRequest = api.fetchDirectoryForTest('/repo/dir/sub', {fresh: true});
      await flushAsyncWork();
      await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
      heldDescendant.resolve(jsonResponse({path: '/repo/dir/sub', entries: [{name: 'stale.txt', kind: 'file'}]}));
      await staleRequest;
      assert.equal(api.fileExplorerFsResourceRecordsForTest().some(record => record.key === 'list\x1f/repo/dir/sub'), false, 'ancestor retirement fences and removes descendant listing records');
      const recreated = await api.fetchDirectoryForTest('/repo/dir/sub', {user: true});
      assert.equal(descendantRequests, 2, 'a recreated descendant performs a new request instead of reusing stale retired data');
      assert.equal(recreated[0].name, 'fresh.txt', 'the recreated descendant publishes only its new generation');
    }

    await testAsync('Finder Sync cannot resurrect a directory retired by a newer parent classification', async () => {
      const api = loadYolomux('', ['1']);
      api.setFileExplorerRootMode('sync', {sync: false});
      const heldChild = deferredFetch();
      let rootRequests = 0;
      api.setFetchForTest(url => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        if (path === '/repo') {
          rootRequests += 1;
          const kind = rootRequests === 1 ? 'dir' : 'file';
          return Promise.resolve(jsonResponse({path, entries: [{name: 'gone', kind}]}));
        }
        if (path === '/repo/gone') return heldChild.promise;
        return Promise.reject(new Error(`unexpected listing ${path}`));
      });
      const plan = {session: '1', root: '/repo', expandPaths: ['/repo/gone'], affectedDirs: ['/repo/gone']};
      const sync = api.syncFileExplorerRootToPlanForTest(plan, '1');
      await flushAsyncWork();
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), ['/repo/gone'], 'the first parent generation admits the directory while its child listing is pending');
      await api.fileExplorerEntriesByWatchedDirectoryForTest('/repo', {fresh: true});
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'the newer file classification retires the in-flight sync disclosure');
      heldChild.resolve(jsonResponse({path: '/repo/gone', entries: [{name: 'stale.txt', kind: 'file'}]}));
      await sync;
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'the stale sync callback cannot resurrect the retired directory');
      assert.equal(api.fileExplorerSyncStateForTest().inFlightSignature, '', 'retirement cancels the stale sync transaction owner');
    });

    {
      const api = loadYolomux('', ['1']);
      const calls = [];
      let rootKind = 'dir';
      api.setFetchForTest((url, options = {}) => {
        const parsed = new URL(String(url), 'https://yolomux.test');
        calls.push({path: parsed.pathname, queryPath: parsed.searchParams.get('path') || '', method: options.method || 'GET'});
        if (parsed.pathname === '/api/fs/batch') {
          const body = JSON.parse(options.body || '{}');
          return Promise.resolve(jsonResponse({responses: body.requests.map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, name: 'note.txt', kind: rootKind},
          }))}));
        }
        if (parsed.pathname === '/api/fs/fast/list' && parsed.searchParams.get('path') === '/repo') {
          return Promise.resolve(jsonResponse({path: '/repo', entries: [{name: 'note.txt', kind: 'file'}]}));
        }
        if (parsed.pathname === '/api/fs/fast/list' && parsed.searchParams.get('path') === '/repo/note.txt') {
          return Promise.resolve(jsonResponse({path: '/repo/note.txt', entries: [{name: 'inside.txt', kind: 'file'}]}));
        }
        return Promise.reject(new Error(`unexpected request ${parsed}`));
      });
      const primed = api.fetchFilePathInfoForTest('/repo/note.txt');
      await api.flushFileExplorerFsBatchForTest();
      await primed;
      rootKind = 'file';
      api.applyLayoutUrlStateSeedForTest({finder: {root: '/repo/note.txt'}});
      const opened = api.openFileExplorerAtForTest('/repo/note.txt', {refreshPanels: false});
      await api.flushFileExplorerFsBatchForTest();
      assert.equal(await opened, true, 'a file-valued Finder root opens its containing directory');
      assert.equal(calls.some(call => call.path === '/api/fs/fast/list' && call.queryPath === '/repo/note.txt'), false, 'a file-valued root never reaches the directory-listing route');
      assert.ok(calls.some(call => call.path === '/api/fs/fast/list' && call.queryPath === '/repo'), 'the resolved containing directory owns the one fast listing');
      rootKind = 'dir';
      api.applyLayoutUrlStateSeedForTest({finder: {root: '/repo/note.txt'}});
      const reopened = api.openFileExplorerAtForTest('/repo/note.txt', {refreshPanels: false});
      await api.flushFileExplorerFsBatchForTest();
      assert.equal(await reopened, true, 'a cached file root that becomes a directory is reclassified and opened directly');
      assert.equal(calls.filter(call => call.path === '/api/fs/batch').length, 3, 'each validated open bypasses the prior INFO kind cache');
      assert.equal(calls.filter(call => call.path === '/api/fs/fast/list' && call.queryPath === '/repo/note.txt').length, 1, 'file-to-directory recreation clears the old list negative and fetches the new directory once');
    }

    {
      const api = loadYolomux('', ['1']);
      const oldInfo = deferredFetch();
      let infoRequests = 0;
      const fastListPaths = [];
      api.setFetchForTest((url, options = {}) => {
        const parsed = new URL(String(url), 'https://yolomux.test');
        if (parsed.pathname === '/api/fs/batch') {
          infoRequests += 1;
          if (infoRequests === 1) return oldInfo.promise;
          const body = JSON.parse(options.body || '{}');
          return Promise.resolve(jsonResponse({responses: body.requests.map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, name: request.path.split('/').pop(), kind: 'dir'},
          }))}));
        }
        if (parsed.pathname === '/api/fs/fast/list') {
          const path = parsed.searchParams.get('path');
          fastListPaths.push(path);
          return Promise.resolve(jsonResponse({path, entries: []}));
        }
        return Promise.reject(new Error(`unexpected request ${parsed}`));
      });
      const oldOpen = api.openFileExplorerAtForTest('/repo/old', {validateKind: true, refreshPanels: false});
      const oldFlush = api.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      const newOpen = api.openFileExplorerAtForTest('/repo/new', {validateKind: true, refreshPanels: false});
      await api.flushFileExplorerFsBatchForTest();
      assert.equal(await newOpen, true, 'the newer validated root applies while the old INFO request is held');
      oldInfo.resolve(jsonResponse({responses: [{id: 1, ok: true, status: 200, payload: {path: '/repo/old', name: 'old', kind: 'dir'}}]}));
      await oldFlush;
      assert.equal(await oldOpen, false, 'the older validated root is fenced after its delayed INFO settles');
      assert.equal(api.fileExplorerRootForTest(), '/repo/new', 'delayed root validation cannot overwrite the newer open generation');
      assert.deepStrictEqual(canonical(fastListPaths), ['/repo/new'], 'the stale validated root never issues a directory listing');
    }

    {
      const api = loadYolomux();
      api.setFileExplorerLastListErrorForTest('/home/test/blocked', 'Cannot open blocked');
      api.setFileExplorerPushRefreshDepthForTest(1);
      assert.equal(await api.fetchDirectoryForTest('/home/test'), null, 'P4: push-refresh-depth returns a benign null');
      assert.equal(api.currentFileExplorerListErrorForTest('/home/test'), '', 'P4: stale error from another path never applies to this path');
      api.setFileExplorerLastListErrorForTest('/home/test', 'Cannot open /home/test');
      assert.equal(await api.fetchDirectoryForTest('/home/test'), null, 'P4: push-refresh-depth still returns null when the same path had a stale error');
      assert.equal(api.currentFileExplorerListErrorForTest('/home/test'), '', 'P4: benign null clears the stale error for the current path');
      api.setFileExplorerPushRefreshDepthForTest(0);
      api.setFetchForTest(url => {
        const path = new URL(String(url), 'https://yolomux.test').searchParams.get('path');
        return Promise.resolve(jsonResponse({error: `denied ${path}`}, 403));
      });
      assert.equal(await api.fetchDirectoryForTest('/home/test/secret', {fresh: true}), null, 'P4: real list failures still return null');
      assert.equal(api.currentFileExplorerListErrorForTest('/home/test/secret'), 'denied /home/test/secret', 'P4: real list failures record a path-keyed error');
      assert.equal(api.currentFileExplorerListErrorForTest('/home/test'), '', 'P4: real errors remain scoped to the failed path');
    }

    {
      const api = loadYolomux();
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, kind: 'dir', repo: {root: request.path}},
          })),
        }));
      });
      const first = api.fetchFilePathInfoForTest('/home/test');
      const second = api.fetchFilePathInfoForTest('/home/test/');
      await api.flushFileExplorerFsBatchForTest();
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [{id: 1, path: '/home/test', trigger_counts: {'tree-render': 2}, type: 'info'}],
        url: '/api/fs/batch',
      }], 'concurrent identical path-info lookups share one batched backend request');
      const [firstInfo, secondInfo] = await Promise.all([first, second]);
      assert.strictEqual(firstInfo, secondInfo, 'shared path-info callers receive the same payload object');
      assert.equal(firstInfo.kind, 'dir');
      const cachedInfo = await api.fetchFilePathInfoForTest('/home/test');
      assert.strictEqual(cachedInfo, firstInfo, 'completed path-info lookup is reused by the short TTL cache');
      assert.equal(calls.length, 1, 'short TTL cache avoids an immediate repeat path-info lookup');
    }

    {
      const deleteCalls = [];
      const runDelete = async ({path, kind = 'dir', count = 0, confirmResponses = [true], fetchRejectsCount = false, role = 'admin'}) => {
        const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', role);
        const confirms = [];
        api.setConfirmForTest(message => {
          confirms.push(String(message));
          return confirmResponses.length ? confirmResponses.shift() : true;
        });
        api.setFetchForTest((url, options = {}) => {
          const text = String(url || '');
          deleteCalls.push({url: text, method: options.method || 'GET', body: options.body || ''});
          if (text.startsWith('/api/batch/count')) {
            if (fetchRejectsCount) return Promise.reject(new Error('count failed'));
            return Promise.resolve(jsonResponse({path, kind: 'dir', files: count, recursive: true}));
          }
          if (text.startsWith('/api/fs/delete')) return Promise.resolve(jsonResponse({deleted: true, path}));
          if (text.startsWith('/api/session-files')) return Promise.resolve(jsonResponse({loaded: true, files: [], repos: [], errors: []}));
          if (text.startsWith('/api/fs/batch')) {
            const body = JSON.parse(options.body || '{}');
            return Promise.resolve(jsonResponse({responses: (body.requests || []).map(request => ({id: request.id, ok: true, payload: {path: request.path, entries: [], kind: 'dir'}}))}));
          }
          return Promise.resolve(jsonResponse({ok: true}));
        });
        await api.deleteFileTreePathForTest(path, {kind, name: path.split('/').pop()}, [path]);
        return {confirms, calls: deleteCalls.splice(0)};
      };

      let result = await runDelete({path: '/home/test/small', count: 5, confirmResponses: [true]});
      assert.equal(result.confirms.length, 1, 'directory with five files uses only the normal delete confirm');
      assert.equal(result.calls.some(call => call.url.startsWith('/api/fs/delete')), true, 'small directory delete proceeds after the normal confirm');

      result = await runDelete({path: '/home/test/big', count: 6, confirmResponses: [true, false]});
      assert.equal(result.confirms.length, 2, 'large directory gets a second count-aware confirm');
      assert.ok(result.confirms[1].includes('You have 6 files in this directory, CONFIRM?') && result.confirms[1].includes('/home/test/big'), 'second confirm shows the file count and directory path');
      assert.equal(result.calls.some(call => call.url.startsWith('/api/fs/delete')), false, 'cancelling the second confirm aborts the whole delete');

      result = await runDelete({path: '/home/test/file.txt', kind: 'file', count: 99, confirmResponses: [true]});
      assert.equal(result.confirms.length, 1, 'single file delete still has only one confirm');
      assert.equal(result.calls.some(call => call.url.startsWith('/api/batch/count')), false, 'single file delete does not fetch a directory count');
      assert.equal(result.calls.some(call => call.url.startsWith('/api/fs/delete')), true, 'single file delete proceeds unchanged');

      result = await runDelete({path: '/home/test/unknown', count: 0, confirmResponses: [true, false], fetchRejectsCount: true});
      assert.equal(result.confirms.length, 2, 'directory count failure falls back to a generic second confirm');
      assert.ok(result.confirms[1].includes('Could not count files in this directory, CONFIRM delete?'), 'count failure still requires an explicit safety confirm');
      assert.equal(result.calls.some(call => call.url.startsWith('/api/fs/delete')), false, 'declining the fallback confirm aborts delete');

      result = await runDelete({path: '/home/test/readonly', count: 10, confirmResponses: [true, true], role: 'readonly'});
      assert.equal(result.confirms.length, 0, 'readonly mode blocks delete before any confirm');
      assert.equal(result.calls.length, 0, 'readonly mode blocks delete before count or delete requests');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {target: '%test', current_path: '/home/test/yolomux.dev3'}, panes: [{target: '%test', active: true, window_active: true, current_path: '/home/test/yolomux.dev3'}]});
      const lines = [terminalLine('• Documented it in tests/SHARE_TEST_INVENTORY.md:123')];
      const term = {cols: 80, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};
      const fileRef = api.terminalWrappedLineReferences(term, 1).find(ref => ref.type === 'file');
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', paths: body.paths || []});
        return Promise.resolve(jsonResponse({
          path: body.paths[0],
          info: {kind: 'file', name: 'SHARE_TEST_INVENTORY.md', path: body.paths[0]},
        }));
      });
      const targetPromise = api.terminalFileReferenceTarget('1', {...fileRef, path: '/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md'});
      await api.flushFileExplorerFsBatchForTest();
      const target = await targetPromise;
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        paths: ['/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md'],
        url: '/api/fs/resolve-file-candidates',
      }], 'terminal file refs use one bounded point resolver request');
      assert.deepStrictEqual(canonical(target), {
        info: {kind: 'file', name: 'SHARE_TEST_INVENTORY.md', path: '/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md'},
        line: 123,
        path: '/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md',
        text: 'tests/SHARE_TEST_INVENTORY.md:123',
      }, 'confirmed terminal file refs carry the absolute path and line for the Open file menu action');
    }

    await testAsync('terminal file menus expose Open file without resolver admission and the selected tab owns its read', async () => {
      const api = loadYolomux('', ['1']);
      const readRequest = deferredFetch();
      const requests = [];
      api.setFetchForTest(url => {
        requests.push(String(url));
        assert.ok(String(url).startsWith('/api/fs/read?'), `only the selected tab may read, got ${url}`);
        return readRequest.promise;
      });
      const menu = () => api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('terminal-context-menu'));
      const labels = node => Array.from(node.children).map(child => child.textContent).filter(Boolean);
      const first = {type: 'file', path: '/tmp/first.md', text: '/tmp/first.md'};

      void api.showTerminalContextMenuForTest('1', {getSelection: () => ''}, 10, 10, {reference: first});
      const firstMenu = menu();
      assert.ok(firstMenu, 'a file right-click paints its menu before the resolver settles');
      assert.equal(api.testElementForId('appOverlayRoot').children.filter(child => child.classList?.contains('terminal-context-menu')).length, 1, 'the immediate menu is the sole terminal context-menu overlay');
      assert.deepStrictEqual(labels(firstMenu), ['Open file', 'Copy path', 'Copy', 'Copy without indent', 'Copy tmux selection'], 'a syntactic file candidate exposes the enabled action on the first menu paint');
      assert.equal(firstMenu.children[0].disabled, false, 'Open file is never gated on a resolver verdict');
      assert.deepStrictEqual(requests, [], 'right-click does not submit an optional resolver request');
      firstMenu.children[0].listeners.get('click')[0]({preventDefault() {}, stopPropagation() {}});
      await flushAsyncWork();
      assert.deepStrictEqual(requests.filter(url => url.startsWith('/api/fs/read?')), [`/api/fs/read?path=${encodeURIComponent(first.path)}`], 'the immediate tab owns one direct read');
      assert.equal(api.currentFileStateForTest(first.path).loading, true, 'the new selected tab renders Loading while its backend read is held');
      readRequest.resolve(jsonResponse({path: first.path, content: '# first\n', size: 8, mtime: 1, mtime_ns: 1, realpath: first.path, file_id: 'dev:1:ino:1'}));
      await flushAsyncWork();
      assert.equal(api.currentFileStateForTest(first.path).loading, undefined, 'the same tab leaves Loading after its read completes');
      assert.equal(api.currentFileStateForTest(first.path).content, '# first\n', 'the same tab receives the read content');
    });

    await testAsync('terminal file menus reuse a warm positive target without a second backend admission', async () => {
      const api = loadYolomux('', ['1']);
      const target = {type: 'file', path: '/tmp/warm-target.md', text: '/tmp/warm-target.md'};
      let fetches = 0;
      api.setFetchForTest(() => {
        fetches += 1;
        return Promise.resolve(jsonResponse({path: target.path, info: {kind: 'file', name: 'warm-target.md', path: target.path}}));
      });
      assert.ok(await api.terminalFileReferenceTarget('1', target, {fresh: false}), 'the passive target lookup warms a positive cache entry');
      const beforeMenuFetches = fetches;
      void api.showTerminalContextMenuForTest('1', {getSelection: () => ''}, 10, 10, {reference: target});
      const menu = api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('terminal-context-menu'));
      assert.ok(menu, 'the explicit right-click paints a menu synchronously');
      assert.deepStrictEqual(Array.from(menu.children).map(child => child.textContent).filter(Boolean), ['Open file', 'Copy path', 'Copy', 'Copy without indent', 'Copy tmux selection'], 'a warm positive target makes file actions available on the first menu paint');
      assert.equal(fetches, beforeMenuFetches, 'the explicit gesture does not submit the already-known target a second time');
    });

    {
      const api = loadYolomux('', ['1']);
      const lines = [terminalLine('127.0.0.1 - - "GET /api/auto-approve HTTP/1.1" 404 -')];
      const term = {cols: 100, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};

      assert.deepEqual(
        api.terminalWrappedLineReferences(term, 1).filter(ref => ref.type === 'file'),
        [],
        'HTTP request targets in terminal server logs are not probed as filesystem paths',
      );
    }

    {
      const api = loadYolomux('', ['1']);
      const prefix = '/tmp/instruction-';
      const lines = [terminalLine(prefix), terminalLine('')];
      const term = {cols: prefix.length + 1, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};

      assert.deepEqual(
        api.terminalWrappedLineReferences(term, 1).filter(ref => ref.type === 'file'),
        [],
        'a path token clipped at the terminal right edge is not probed before its continuation arrives',
      );

      lines[1] = terminalLine('fleet-check.md', true);
      const completed = api.terminalWrappedLineReferences(term, 1).find(ref => ref.type === 'file');
      assert.equal(completed?.path, '/tmp/instruction-fleet-check.md', 'the same token resolves after its real soft-wrap continuation arrives');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo4/lib/llm/src'}});
      const lines = [terminalLine('protocols/openai/chat_completions/qwen3_coder_v2.rs')];
      const term = {cols: 100, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', paths: body.paths || []});
        return Promise.resolve(jsonResponse({
          path: body.paths[0],
          info: {kind: 'file', name: 'qwen3_coder_v2.rs', path: body.paths[0]},
        }));
      });
      const providerPromise = api.terminalReferenceProviderLinks('1', term, 1);
      await api.flushFileExplorerFsBatchForTest();
      const links = await providerPromise;
      assert.deepStrictEqual(canonical(calls), [], 'terminal file underline paint does not submit background filesystem work');
      assert.equal(links.length, 1, 'syntactic terminal file refs are exposed to xterm as visual decorations without an existence probe');
      assert.deepStrictEqual(canonical({
        text: links[0].text,
        range: links[0].range,
        decorations: links[0].decorations,
      }), {
        text: 'protocols/openai/chat_completions/qwen3_coder_v2.rs',
        range: {start: {x: 1, y: 1}, end: {x: 51, y: 1}},
        decorations: {pointerCursor: false, underline: true},
      }, 'xterm marks terminal file refs with underline but no left-click pointer affordance');
      assert.equal(links[0].activate(), undefined, 'left-click activation is intentionally a no-op');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/cache-misses'}});
      const fileRef = {type: 'file', path: 'missing.js', text: 'missing.js'};
      let requestCount = 0;
      api.setFetchForTest((_url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        requestCount += 1;
        return Promise.resolve(jsonResponse({path: '', info: null, misses: body.paths.map(path => ({path, status: 404}))}));
      });
      const firstTarget = api.terminalFileReferenceTarget('1', fileRef, {fresh: false});
      const concurrentTarget = api.terminalFileReferenceTarget('1', fileRef, {fresh: false});
      assert.equal(await firstTarget, null, 'missing passive terminal file refs resolve to null');
      assert.equal(await concurrentTarget, null, 'concurrent missing terminal file refs share the in-flight resolution');
      assert.deepEqual(
        api.jsDebugEventsForTest().filter(event => event.type === 'client_failure'),
        [],
        'passive terminal file guesses do not turn an expected missing candidate into a client error',
      );
      const requestsAfterFirstResolution = requestCount;
      assert.ok(requestsAfterFirstResolution > 0, 'the first missing terminal file ref checks its context-derived paths');
      assert.equal(await api.terminalFileReferenceTarget('1', fileRef, {fresh: false}), null, 'negative terminal file target results are cached');
      assert.equal(requestCount, requestsAfterFirstResolution, 'repeated passive missing-file scans perform no backend lookups');
      assert.equal(api.terminalFileReferenceTargetCacheSizeForTest(), 1, 'one missing terminal file token occupies one bounded target-cache entry');
      assert.equal(await api.terminalFileReferenceTarget('1', fileRef), null, 'fresh user resolution still reports the missing target');
      assert.ok(requestCount > requestsAfterFirstResolution, 'fresh context-menu resolution bypasses a passive cached miss');
      api.invalidateTerminalFileReferenceTargetsForTest(['/home/test/cache-misses']);
      assert.equal(api.terminalFileReferenceTargetCacheHasForTest('1', fileRef), false, 'filesystem changes invalidate matching cached terminal misses before their TTL');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/repo'}});
      let requestCount = 0;
      let resolveRequest;
      api.setFetchForTest((_url, options = {}) => {
        requestCount += 1;
        const body = JSON.parse(options.body);
        assert.deepEqual(body, {paths: ['/repo/a.py', '/a.py', '/home/test/a.py']}, 'different terminal line targets share one candidate request');
        return new Promise(resolve => { resolveRequest = resolve; });
      });
      const first = api.terminalFileReferenceTarget('1', {type: 'file', path: 'a.py', line: 10, text: 'a.py:10'}, {fresh: false});
      const second = api.terminalFileReferenceTarget('1', {type: 'file', path: 'a.py', line: 20, text: 'a.py:20'}, {fresh: false});
      await flushAsyncWork();
      assert.equal(requestCount, 1, 'one visible path has one in-flight candidate request even when terminal references carry different line numbers');
      resolveRequest(jsonResponse({path: '/repo/a.py', info: {kind: 'file', name: 'a.py', path: '/repo/a.py'}}));
      assert.deepEqual(canonical(await first), {path: '/repo/a.py', info: {kind: 'file', name: 'a.py', path: '/repo/a.py'}, line: 10, text: 'a.py:10'});
      assert.deepEqual(canonical(await second), {path: '/repo/a.py', info: {kind: 'file', name: 'a.py', path: '/repo/a.py'}, line: 20, text: 'a.py:20'});
    }

    {
      const api = loadYolomux('', ['1']);
      let requests = 0;
      api.setFetchForTest(() => {
        requests += 1;
        return Promise.resolve(jsonResponse({}));
      });
      assert.equal(await api.terminalFileReferenceTarget('1', {type: 'file', path: 'https://example.invalid/file.md'}, {fresh: false}), null, 'unsupported file-like references are rejected locally');
      assert.equal(requests, 0, 'unsupported file-like references do not submit an empty resolver request');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/cache-ttl'}});
      let now = 1_000_000;
      let requestCount = 0;
      api.setFetchForTest((_url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        requestCount += 1;
        const path = body.paths.find(candidate => candidate.endsWith('positive.js')) || '';
        return Promise.resolve(jsonResponse({path, info: path ? {kind: 'file', name: 'positive.js', path} : null}));
      });
      const clock = () => now;
      const missing = {type: 'file', path: 'negative.js', text: 'negative.js'};
      const existing = {type: 'file', path: 'positive.js', text: 'positive.js'};

      assert.equal(await api.terminalFileReferenceTarget('1', missing, {fresh: false, now: clock}), null, 'the fake-clock negative lookup initially misses');
      const afterNegative = requestCount;
      now += 4_999;
      assert.equal(await api.terminalFileReferenceTarget('1', missing, {fresh: false, now: clock}), null, 'a negative result remains cached before its bounded TTL expires');
      assert.equal(requestCount, afterNegative, 'the fake-clock pre-expiry negative lookup sends no new fs batch request');
      now += 1;
      assert.equal(await api.terminalFileReferenceTarget('1', missing, {fresh: false, now: clock}), null, 'an expired negative result is resolved again');
      assert.ok(requestCount > afterNegative, 'the fake clock proves missing candidates retry only after their TTL');

      assert.ok(await api.terminalFileReferenceTarget('1', existing, {fresh: false, now: clock}), 'the fake-clock positive lookup resolves');
      const afterPositive = requestCount;
      now += 29_999;
      assert.ok(await api.terminalFileReferenceTarget('1', existing, {fresh: false, now: clock}), 'a positive result remains cached before its longer TTL expires');
      assert.equal(requestCount, afterPositive, 'the fake-clock pre-expiry positive lookup sends no new fs batch request');
      now += 1;
      api.invalidateFileExplorerFsCachesForTest();
      assert.ok(await api.terminalFileReferenceTarget('1', existing, {fresh: false, now: clock}), 'an expired positive result is resolved again');
      assert.ok(requestCount > afterPositive, 'the fake clock proves existing candidates retry only after their TTL');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/cache-lru'}});
      let requestCount = 0;
      api.setFetchForTest((_url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        requestCount += 1;
        const path = body.paths[0];
        return Promise.resolve(jsonResponse({path, info: {kind: 'file', name: path.split('/').pop(), path}}));
      });
      const ref = index => ({type: 'file', path: `cache-${index}.js`, text: `cache-${index}.js`});
      await Promise.all(Array.from({length: 512}, (_unused, index) => (
        api.terminalFileReferenceTarget('1', ref(index), {fresh: false})
      )));
      assert.equal(requestCount, 512, 'the first 512 distinct terminal file refs each resolve once');
      assert.equal(api.terminalFileReferenceTargetCacheSizeForTest(), 512, 'terminal file target LRU reaches the shared cache limit');
      await api.terminalFileReferenceTarget('1', ref(0), {fresh: false});
      assert.equal(requestCount, 512, 'reusing the oldest target is a cache hit that refreshes its LRU recency');
      await api.terminalFileReferenceTarget('1', ref(512), {fresh: false});
      assert.equal(api.terminalFileReferenceTargetCacheSizeForTest(), 512, 'adding another target evicts instead of growing beyond the shared limit');
      assert.equal(api.terminalFileReferenceTargetCacheHasForTest('1', ref(0)), true, 'the recently reused oldest target survives the LRU eviction');
      assert.equal(api.terminalFileReferenceTargetCacheHasForTest('1', ref(1)), false, 'the least-recently-used target is the entry that gets evicted');
      await api.terminalFileReferenceTarget('1', ref(0), {fresh: false});
      assert.equal(requestCount, 513, 'the recently reused oldest target survives the LRU eviction');
      await api.terminalFileReferenceTarget('1', ref(1), {fresh: false});
      assert.equal(api.terminalFileReferenceTargetCacheHasForTest('1', ref(1)), true, 're-resolving the evicted target returns it to the bounded target cache');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentJobsPayloadForTest({jobs: [{id: 'job-1', status: 'pending_confirmation', target: {session: '1'}, public_text: 'date'}]});
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body || ''});
        if (String(url).endsWith('/confirm')) return Promise.resolve(jsonResponse({job: {id: 'job-1', status: 'fired', target: {session: '1'}, public_text: 'date'}}));
        if (String(url).endsWith('/cancel')) return Promise.resolve(jsonResponse({job: {id: 'job-1', status: 'cancelled', target: {session: '1'}, public_text: 'date'}}));
        if (String(url) === '/api/yoagent/jobs') return Promise.resolve(jsonResponse({jobs: [{id: 'job-1', status: 'fired', target: {session: '1'}, public_text: 'date'}]}));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      await api.confirmYoagentJobForTest('job-1');
      await api.cancelYoagentJobForTest('job-1');
      assert.deepStrictEqual(canonical(calls.map(call => ({method: call.method, url: call.url}))), [
        {method: 'POST', url: '/api/yoagent/jobs/job-1/confirm'},
        {method: 'GET', url: '/api/yoagent/jobs'},
        {method: 'POST', url: '/api/yoagent/jobs/job-1/cancel'},
        {method: 'GET', url: '/api/yoagent/jobs'},
      ], 'YO!agent job confirm/cancel controls call the existing job routes and refresh the list');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
      const calls = [];
      let firstChatResolve = null;
      api.setFetchForTest((url, options = {}) => {
        const call = {url: String(url), method: options.method || 'GET', body: options.body || '', hasSignal: Boolean(options.signal)};
        calls.push(call);
        if (String(url) === '/api/yoagent/chat') {
          const body = JSON.parse(String(options.body || '{}'));
          if (body.message === 'first') {
            return new Promise(resolve => {
              firstChatResolve = () => resolve(jsonResponse({
                answer: 'first done',
                backend: 'codex',
                backend_used: 'codex',
                conversation: {messages: [{role: 'user', content: 'first'}, {role: 'assistant', content: 'first done'}]},
              }));
            });
          }
          if (body.message === 'second') {
            return Promise.resolve(jsonResponse({
              answer: 'second done',
              backend: 'codex',
              backend_used: 'codex',
              conversation: {messages: [{role: 'user', content: 'second'}, {role: 'assistant', content: 'second done'}]},
            }));
          }
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      const firstTurn = api.sendYoagentChatMessageForTest('first');
      await Promise.resolve();
      await api.sendYoagentChatMessageForTest('second');
      assert.equal(api.yoagentChatQueueForTest().length, 1, 'submitting while YO!agent is busy enqueues the next ask instead of dropping it');
      assert.ok(api.yoagentChatHtml().includes('yoagent-chat-queue'), 'queued chat turns render in their own queue, separate from pending result waits');
      firstChatResolve();
      await firstTurn;
      await new Promise(resolve => setTimeout(resolve, 0));
      assert.equal(api.yoagentChatQueueForTest().length, 0, 'finishing the active ask drains the next queued ask');
      const chatBodies = calls.filter(call => call.url === '/api/yoagent/chat').map(call => JSON.parse(call.body));
      assert.deepStrictEqual(chatBodies.map(body => body.message), ['first', 'second'], 'queued asks run FIFO after the active turn completes');
      assert.ok(chatBodies.every(body => body.request_id && body.stream_id && body.request_id === body.stream_id), 'chat sends carry one request/stream id so local thinking and backend deltas update the same row');
      const source = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(/new AbortController\(\)|typeof AbortController === 'function'/.test(source) && /signal:\s*controller\?\.signal/.test(source), 'active chat fetch uses AbortController when the browser provides it');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
      const hiddenToolOutput = 'hidden tool output '.repeat(20000);
      let chatBody = null;
      api.applyYoagentConversationPayloadForTest({
        messages: [{
          role: 'assistant',
          content: 'short visible answer',
          auxiliaryText: hiddenToolOutput,
          auxiliaryPreview: hiddenToolOutput.slice(0, 2000),
          streamItems: [{kind: 'tool', text: hiddenToolOutput}],
          createdAt: '2026-06-24T20:00:00Z',
        }],
      });
      api.setFetchForTest((url, options = {}) => {
        if (String(url) === '/api/yoagent/chat') {
          chatBody = JSON.parse(String(options.body || '{}'));
          return Promise.resolve(jsonResponse({
            answer: 'ok',
            backend: 'codex',
            backend_used: 'codex',
            conversation: {messages: [{role: 'user', content: 'hello?'}, {role: 'assistant', content: 'ok'}]},
          }));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      await api.sendYoagentChatMessageForTest('hello?');
      const encodedBody = JSON.stringify(chatBody || {});
      assert.equal(chatBody.message, 'hello?', 'YO!agent chat still sends the current prompt');
      assert.equal(Object.prototype.hasOwnProperty.call(chatBody, 'history'), false, 'YO!agent chat relies on server-side transcript history instead of reposting browser messages');
      assert.ok(encodedBody.length < 2048, 'YO!agent chat request stays small even when prior visible messages carry hidden stream/tool data');
      assert.equal(encodedBody.includes('hidden tool output'), false, 'hidden stream/tool details are not serialized into the chat request');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
      api.setFetchForTest((url) => {
        if (String(url) === '/api/yoagent/chat') {
          return Promise.resolve(jsonResponse({error: 'Request Entity Too Large'}, 413));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      await api.sendYoagentChatMessageForTest('hello?');
      const html = api.yoagentChatHtml();
      assert.ok(html.includes('conversation too large to resume'), 'YO!agent 413 errors explain that the resumable conversation is too large');
      assert.equal(html.includes('chat failed: Request Entity Too Large'), false, 'YO!agent 413 errors do not expose only the raw HTTP reason');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          availableAgents: [],
          agentAuth: {},
          settingsPayload: settingsOverride({yoagent: {backend: 'claude'}}),
        },
      });
      assert.equal(api.yoagentResolvedBackendForTest(), 'deterministic', 'without installed-agent metadata, explicit Claude cannot be attempted yet');
      await api.applyTranscriptsPayloadForTest({
        session_order: ['1'],
        sessions: {},
        availableAgents: ['claude'],
        agentAuth: {claude: {installed: true, logged_in: true}},
      }, {refreshAuto: false, refreshActivity: false});
      assert.equal(api.yoagentResolvedBackendForTest(), 'claude', 'metadata refresh updates availableAgents as well as agentAuth');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          availableAgents: ['claude'],
          agentAuth: {claude: {installed: true, logged_in: false}},
          settingsPayload: settingsOverride({yoagent: {backend: 'claude'}}),
        },
      });
      assert.equal(api.yoagentResolvedBackendForTest(), 'claude', 'explicit Claude selection stays explicit when the CLI exists');
      assert.deepStrictEqual(canonical(api.yoagentAvailableBackendOptionsForTest()), ['claude'], 'explicit Claude remains visible even if stale auth says logged out');
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body || ''});
        if (String(url) === '/api/agent-auth?force=1') {
          return Promise.resolve(jsonResponse({
            availableAgents: ['claude'],
            agentAuth: {claude: {installed: true, logged_in: true}},
          }));
        }
        if (String(url) === '/api/yoagent/chat') {
          return Promise.resolve(jsonResponse({
            answer: 'claude answered',
            backend: 'claude',
            backend_used: 'claude',
            conversation: {messages: [{role: 'user', content: 'hello'}, {role: 'assistant', content: 'claude answered'}]},
          }));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      await api.sendYoagentChatMessageForTest('hello');
      assert.deepStrictEqual(canonical(calls.map(call => ({method: call.method, url: call.url}))), [
        {method: 'GET', url: '/api/agent-auth?force=1'},
        {method: 'POST', url: '/api/yoagent/chat'},
      ], 'explicit backend sends force-refresh agent auth before posting chat');
      assert.equal(JSON.parse(calls[1].body).message, 'hello', 'chat request still posts the user request after refresh');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
      const calls = [];
      api.applyYoagentConversationPayloadForTest({
        messages: [{role: 'assistant', content: 'sent to target'}],
        pending_waits: [{id: 'wait-target', session: '1', started_ts: Math.round(Date.now() / 1000)}],
      });
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body || ''});
        if (String(url) === '/api/yoagent/chat') {
          return Promise.resolve(jsonResponse({
            answer: 'queued after wait',
            backend: 'codex',
            backend_used: 'codex',
            conversation: {messages: [{role: 'user', content: 'after'}, {role: 'assistant', content: 'queued after wait'}]},
          }));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      await api.sendYoagentChatMessageForTest('after');
      assert.equal(api.yoagentChatQueueForTest().length, 1, 'pending target-agent waits make new asks join the queue');
      assert.deepStrictEqual(calls, [], 'queued asks are not sent while a target-agent reply is still pending');
      api.applyYoagentConversationPayloadForTest({messages: [{role: 'assistant', content: 'target finished'}], pending_waits: []});
      await new Promise(resolve => setTimeout(resolve, 0));
      assert.equal(api.yoagentChatQueueForTest().length, 0, 'clearing the pending wait drains the next queued ask');
      assert.deepStrictEqual(calls.filter(call => call.url === '/api/yoagent/chat').map(call => JSON.parse(call.body).message), ['after'], 'pending-wait queue drains FIFO after the target AI finishes');
    }

    {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
      const calls = [];
      api.setYoagentBusyForTest(true);
      await api.sendYoagentChatMessageForTest('queued only');
      const queued = api.yoagentChatQueueForTest()[0];
      assert.ok(queued?.id, 'busy submit creates a cancelable queued item');
      api.cancelQueuedYoagentChatMessageForTest(queued.id);
      assert.equal(api.yoagentChatQueueForTest().length, 0, 'canceling a queued item removes only that pending ask');
      api.setYoagentBusyForTest(false);
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body || ''});
        if (String(url) === '/api/yoagent/chat') {
          return new Promise((_resolve, reject) => {
            options.signal?.addEventListener('abort', () => {
              const error = new Error('aborted');
              error.name = 'AbortError';
              reject(error);
            });
          });
        }
        if (/^\/api\/yoagent\/chat\/.+\/cancel$/.test(String(url))) {
          return Promise.resolve(jsonResponse({ok: true, cancelled: true}));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      api.sendYoagentChatMessageForTest('stop me');
      await Promise.resolve();
      const active = api.yoagentActiveChatRequestForTest();
      assert.ok(active?.id, 'active YO!agent request records the request id');
      assert.ok(api.cancelActiveYoagentChatRequestForTest(), 'active cancel aborts the running request');
      await flushAsyncWork();
      assert.equal(api.yoagentActiveChatRequestForTest(), null, 'active cancel frees the composer immediately');
      assert.ok(api.yoagentChatHtml().includes('Stopped.'), 'active cancel leaves a stopped message state');
      assert.equal(api.fixtureLifecycleOperationStateForTest().startupActive, 0, 'active cancel releases its shared startup request slot');
      assert.equal(api.jsDebugFailureEventsForTest().length, 0, 'active cancel retires its request without a diagnostic failure');
      assert.deepStrictEqual(canonical(calls.map(call => ({method: call.method, url: call.url}))), [
        {method: 'POST', url: '/api/yoagent/chat'},
        {method: 'POST', url: `/api/yoagent/chat/${active.id}/cancel`},
      ], 'active cancel posts to the request-scoped cancel route');
    }

    {
      const api = loadYolomux('', ['1']);
      const detailsPreviews = html => [...String(html || '').matchAll(/<span class="[^"]*\byoagent-details-preview\b[^"]*">([\s\S]*?)<\/span>/g)].map(match => match[1]);
      const thinkingLine = 'thinking: scanning files reading activity context final synthesis';
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-thinking',
        phase: 'delta',
        content: 'partial answer',
        stream_items: [
          {kind: 'thinking', text: thinkingLine, labelKey: 'yoagent.stream.thinking', fallback: 'thinking'},
          {kind: 'tool', text: 'tool output: command: collected files', labelKey: 'yoagent.stream.toolOutput', fallback: 'tool output'},
        ],
        auxiliary_lines: [thinkingLine, 'tool output: command: collected files'],
        auxiliary_preview: `${thinkingLine}\ntool output: command: collected files`,
        hidden_work_active: true,
        tool_active: true,
      });
      const runningHtml = api.yoagentChatHtml();
      const runningPreviews = detailsPreviews(runningHtml);
      assert.equal(runningPreviews[0], thinkingLine, 'running thinking preview shows one continuously growing thinking line');
      assert.equal(runningPreviews[1], 'tool output: command: collected files', 'tool calls use their own one-line TC preview');
      assert.ok(runningHtml.includes('yoagent-thinking-live-preview'), 'running thinking preview uses the five-line live preview clamp');
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-thinking',
        phase: 'hidden_work_done',
        done: true,
        auxiliary_done: true,
        auxiliary_lines: [thinkingLine, 'tool output: command: collected files'],
      });
      const donePreviews = detailsPreviews(api.yoagentChatHtml());
      assert.equal(donePreviews.length, 1, 'completed thinking summary collapses to count-only with no preview words');
      assert.equal(donePreviews[0], 'tool output: command: collected files', 'completed tool-call preview remains separate from thinking');

      const longThinking = ['thinking:', ...Array.from({length: 72}, (_value, index) => `word${index}`)].join(' ');
      api.applyYoagentConversationPayloadForTest({
        messages: [{
          role: 'assistant',
          content: 'answer',
          createdAt: '2026-06-20T00:00:00Z',
          auxiliaryLines: [longThinking],
          auxiliaryText: longThinking,
        }],
      });
      const longHtml = api.yoagentChatHtml();
      const longPreviews = detailsPreviews(longHtml);
      assert.deepStrictEqual(longPreviews, [], 'completed thinking details do not show preview words in the collapsed summary');
      assert.ok(longHtml.includes('thinking (73 words)…'), 'completed thinking details label counts the full thinking text');
      assert.ok(longHtml.includes(longThinking), 'expanded thinking details keep the complete thinking text');
      assert.equal(longHtml.includes('did not expose readable thinking text'), false, 'word-bearing thinking does not show the token-only note');

      api.applyYoagentConversationPayloadForTest({
        messages: [{
          role: 'assistant',
          content: 'answer',
          createdAt: '2026-06-20T00:00:00Z',
          streamItems: [{
            kind: 'thinking',
            text: '',
            labelKey: 'yoagent.stream.thinking',
            fallback: 'thinking',
            tokenCount: 200,
          }],
        }],
      });
      const tokenProgressHtml = api.yoagentChatHtml();
      assert.ok(tokenProgressHtml.includes('thinking (~200 tokens)…'), 'Claude token-only thinking progress is labeled as tokens, not fake words');
      assert.equal(tokenProgressHtml.includes('thinking (2 words)…'), false, 'Claude token-only thinking progress does not use the text word counter');
      assert.equal(tokenProgressHtml.includes('thinking: thinking'), false, 'Claude token-only thinking progress does not duplicate the thinking prefix');
      assert.ok(tokenProgressHtml.includes('did not expose readable thinking text'), 'Claude token-only thinking progress explains why no words are shown');
      assert.equal(tokenProgressHtml.includes('<pre class="yoagent-auxiliary-stream">'), false, 'Claude token-only progress is metadata, not fake thinking body text');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-multiline-tool',
        phase: 'tool',
        content: 'partial answer',
        stream_items: [
          {kind: 'tool', text: 'tool output: command: line 1\nline 2', labelKey: 'yoagent.stream.toolOutput', fallback: 'tool output'},
        ],
        auxiliary_lines: ['tool output: command: line 1\nline 2'],
        auxiliary_preview: 'tool output: command: line 1\nline 2',
        tool_active: true,
      });
      const html = api.yoagentChatHtml();
      assert.equal(html.includes('Details…'), false, 'multiline tool output continuation lines do not leak into the thinking details preview');
      assert.ok(html.includes('yoagent-toolcall-details'), 'multiline tool output still renders in the structured tool-call block');
      assert.ok(html.includes('tool output: command: line 1\nline 2'), 'tool-call pre preserves real multiline output');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-interleaved',
        phase: 'delta',
        content: 'first answer second answer',
        stream_items: [
          {kind: 'thinking', text: 'thinking: reading context'},
          {kind: 'assistant', text: 'first answer'},
          {kind: 'tool', text: 'tool output: command: line 1\nline 2'},
          {kind: 'assistant', text: 'second answer'},
        ],
        auxiliary_lines: ['thinking: reading context', 'tool output: command: line 1\nline 2'],
      });
      const html = api.yoagentChatHtml();
      const ordered = [
        html.indexOf('thinking: reading context'),
        html.indexOf('first answer'),
        html.indexOf('tool output: command: line 1\nline 2'),
        html.indexOf('second answer'),
      ];
      assert.ok(ordered.every(index => index >= 0), 'interleaved YO!agent stream rows all render');
      assert.deepStrictEqual(ordered, [...ordered].sort((left, right) => left - right), 'thinking/tool rows and assistant text render in stream order');
      assert.ok(html.includes('yoagent-message-stream'), 'interleaved stream uses the ordered message stream renderer');
      assert.equal((html.match(/<details class="[^"]*yoagent-stream-detail/g) || []).length, 2, 'thinking and tool-call stream rows remain independently collapsible');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-real-claude-thinking',
        phase: 'thinking',
        content: '',
        stream_items: [
          {kind: 'thinking', text: 'thinking: Reading context\n  and checking files'},
        ],
        auxiliary_lines: ['thinking: Reading context and checking files'],
        hidden_work_active: true,
      });
      const html = api.yoagentChatHtml();
      assert.ok(html.includes('thinking: Reading context\n  and checking files'), 'real Claude thinking text stream renders in the expanded GUI body');
      assert.ok(html.includes('thinking (6 words)…'), 'real Claude thinking text uses the normal thinking label');
      assert.equal(html.includes('did not expose readable thinking text'), false, 'real Claude thinking text never shows the token-only note');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-coalesced',
        phase: 'delta',
        content: 'middle answer',
        stream_items: [
          {kind: 'tool', text: 'tool start: command: rg files'},
          {kind: 'tool', text: 'tool output: command: found one'},
          {kind: 'tool', text: 'tool done: command: exit 0'},
          {kind: 'assistant', text: 'middle answer'},
          {kind: 'tool', text: 'tool start: command: git status'},
        ],
      });
      const html = api.yoagentChatHtml();
      assert.equal((html.match(/yoagent-toolcall-details/g) || []).length, 2, 'adjacent tool calls coalesce but assistant text splits tool runs');
      assert.ok(html.includes('tool start: command: rg files') && html.includes('tool output: command: found one') && html.includes('tool done: command: exit 0'), 'coalesced tool block keeps every tool line in order');
      assert.ok(html.includes('|stream|0') && html.includes('|stream|4'), 'coalesced tool runs keep stable source-index detail keys');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentStreamPayloadForTest({
        stream_id: 'stream-thinking-count',
        phase: 'delta',
        content: 'answer',
        stream_items: [
          {kind: 'thinking', text: 'alpha beta gamma'},
          {kind: 'thinking', text: 'delta epsilon'},
          {kind: 'assistant', text: 'answer'},
        ],
      });
      const html = api.yoagentChatHtml();
      assert.ok(html.includes('thinking (5 words)…'), 'coalesced thinking stream label counts the full merged thinking run');
      assert.equal((html.match(/<details class="[^"]*yoagent-stream-detail/g) || []).length, 1, 'adjacent thinking stream rows coalesce into one collapsible');
    }

    {
      const api = loadYolomux('', ['1']);
      api.applyYoagentConversationPayloadForTest({
        messages: [],
        pending_waits: [{id: 'wait-1', session: '1', label: 'Waiting for tmux session `1` to reply', started_ts: Math.round(Date.now() / 1000) - 65}],
      });
      assert.ok(api.yoagentChatHtml().includes('data-yoagent-wait-clear="wait-1"'), 'YO!agent pending wait rows expose a Clear control');
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body || ''});
        if (String(url) === '/api/yoagent/waits/wait-1/clear') {
          return Promise.resolve(jsonResponse({
            conversation: {
              messages: [{role: 'assistant', kind: 'agent_result', session: '1', content: 'Result from tmux session `1`: done'}],
              pending_waits: [],
            },
          }));
        }
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });
      await api.clearYoagentPendingWaitForTest('wait-1');
      assert.deepStrictEqual(canonical(calls.map(call => ({method: call.method, url: call.url, body: JSON.parse(call.body || '{}')}))), [
        {method: 'POST', url: '/api/yoagent/waits/wait-1/clear', body: {id: 'wait-1'}},
      ], 'YO!agent wait Clear posts to the existing wait clear endpoint');
      const html = api.yoagentChatHtml();
      assert.equal(html.includes('yoagent-waiting-queue'), false, 'clearing a stale wait removes the pending row');
      assert.ok(html.includes('Result from tmux session') && html.includes('done'), 'clearing a stale wait preserves recorded result messages');
    }

    {
      const frames = [];
      const api = loadYolomux('', ['1'], 'http:', 'iPhone', 'admin', {coarsePointer: true});
      api.registerTerminalForTest('1', {focus() {}}, {
        readyState: WebSocket.OPEN,
        send(frame) { frames.push(JSON.parse(frame)); },
      });
      assert.equal(api.terminalMobileAccessoryDataForTest('1', 'escape'), '\x1b', 'mobile accessory maps Esc to the terminal escape byte');
      assert.equal(api.terminalMobileAccessoryDataForTest('1', 'backspace'), '\x7f', 'mobile accessory maps its visible Backspace key to the terminal DEL byte');
      assert.equal(api.terminalMobileAccessoryDataForTest('1', 'arrow-up'), '\x1b[A', 'mobile accessory uses the normal cursor sequence outside application-cursor mode');
      assert.equal(api.terminalMobileAccessoryRepeatsForTest('arrow-up'), true, 'holding an arrow is repeatable like a hardware cursor key');
      assert.equal(api.terminalMobileAccessoryRepeatsForTest('tmux-scroll-down'), true, 'holding PgDown repeats tmux scrolling without closing the palette');
      assert.equal(api.terminalMobileAccessoryRepeatsForTest('tab'), false, 'Tab remains a one-shot key');
      assert.equal(api.sendTerminalMobileAccessoryInputForTest('1', 'open'), true, 'mobile keyboard launcher opens the palette without sending terminal bytes');
      assert.equal(api.terminalMobileAccessoryStateForTest('1').open, true, 'palette visibility belongs to the same per-session accessory record');
      assert.equal(api.sendTerminalMobileAccessoryInputForTest('1', 'open'), true, 'a repeated launcher-open action leaves the existing palette open');
      assert.equal(api.terminalMobileAccessoryStateForTest('1').open, true, 'only the dedicated close action closes the palette');
      assert.equal(api.sendTerminalMobileAccessoryInputForTest('1', 'interrupt'), true, 'mobile Ctrl-C sends through the shared terminal transport');
      assert.equal(api.sendTerminalMobileAccessoryInputForTest('1', 'backspace'), true, 'visible mobile Backspace sends through the shared terminal transport');
      assert.deepStrictEqual(canonical(frames), [{type: 'input', data: '\x03'}, {type: 'input', data: '\x7f'}], 'mobile Ctrl-C and Backspace preserve their terminal control bytes');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'ctrl'), true, 'mobile Ctrl latch turns on for the next OS-keyboard character');
      assert.equal(api.handleTerminalDataForTest('1', 'c'), true, 'a character following the Ctrl latch uses the normal xterm input path');
      assert.deepStrictEqual(canonical(frames), [{type: 'input', data: '\x03'}, {type: 'input', data: '\x7f'}, {type: 'input', data: '\x03'}], 'Ctrl plus the phone keyboard C becomes the same interrupt byte');
      assert.deepStrictEqual(canonical(api.terminalMobileAccessoryStateForTest('1')), {ctrl: false, alt: false, shift: false, cmd: false, ctrlLocked: false, altLocked: false, shiftLocked: false, cmdLocked: false, more: false, open: true, palettePlacement: null, x: null, y: null, palettePress: null, launcherPress: null, suppressLauncherClick: false}, 'one-shot modifier state clears after the next key while the palette stays open');
      assert.equal(api.sendTerminalMobileAccessoryInputForTest('1', 'close'), true, 'the dedicated X action closes the palette');
      assert.equal(api.terminalMobileAccessoryStateForTest('1').open, false, 'closing the palette leaves the terminal untouched');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'alt'), true, 'mobile Alt latch turns on independently');
      assert.equal(api.handleTerminalDataForTest('1', 'x'), true, 'Alt-modified phone input follows the existing terminal data path');
      assert.equal(frames.at(-1).data, '\x1bx', 'Alt prefixes the next key with Escape');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'shift'), true, 'mobile Shift latch turns on independently');
      assert.equal(api.handleTerminalDataForTest('1', 'a'), true, 'Shift-modified phone input follows the existing terminal data path');
      assert.equal(frames.at(-1).data, 'A', 'Shift uppercases the next lowercase character');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'shift'), true, 'mobile Shift latch can shift punctuation');
      assert.equal(api.handleTerminalDataForTest('1', '1'), true, 'Shift-modified punctuation follows the existing terminal data path');
      assert.equal(frames.at(-1).data, '!', 'Shift maps number-row punctuation to the shifted glyph');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'cmd'), true, 'mobile Cmd/Meta latch turns on independently');
      assert.equal(api.handleTerminalDataForTest('1', 'k'), true, 'Cmd/Meta-modified phone input follows the existing terminal data path');
      assert.equal(frames.at(-1).data, '\x1bk', 'Cmd/Meta prefixes the next key with Escape like terminal Meta');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'ctrl'), true, 'first Ctrl tap arms a one-shot modifier');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'ctrl'), true, 'second Ctrl tap inside the double-tap window locks the modifier');
      assert.equal(api.handleTerminalDataForTest('1', 'a'), true, 'locked Ctrl applies to the first typed key');
      assert.equal(api.handleTerminalDataForTest('1', 'b'), true, 'locked Ctrl persists for another typed key');
      assert.equal(frames.at(-2).data, '\x01', 'locked Ctrl maps A to SOH');
      assert.equal(frames.at(-1).data, '\x02', 'locked Ctrl maps B to STX');
      assert.equal(api.terminalMobileAccessoryStateForTest('1').ctrlLocked, true, 'locked Ctrl stays visibly locked after terminal input');
      assert.equal(api.toggleTerminalMobileAccessoryStateForTest('1', 'ctrl'), false, 'tapping a locked modifier turns it off');
      assert.equal(api.terminalMobileAccessoryStateForTest('1').ctrlLocked, false, 'turning off a locked modifier clears the lock bit');
      const keyboardHtml = api.terminalMobileAccessoryHtmlForTest('1');
      const keyboardActions = ['escape', 'ctrl', 'interrupt', 'tab', 'tmux-prefix', 'upload', 'backspace', 'copy', 'arrow-up', 'tmux-scroll-up', 'arrow-left', 'enter', 'arrow-right', 'command-v', 'arrow-down', 'tmux-scroll-down', 'shift', 'alt', 'cmd', 'command-p', 'home', 'end', 'delete', 'shift-tab', 'ctrl-d', 'ctrl-z', 'ctrl-l', 'ctrl-r'];
      assert.equal(keyboardActions.every(action => keyboardHtml.includes(`data-terminal-mobile-key="${action}"`)), true, 'the touch palette exposes every primary and extra key in one surface');
      const handleIndex = keyboardHtml.indexOf('mobile-terminal-key-grabber');
      const closeIndex = keyboardHtml.indexOf('data-terminal-mobile-close="1"');
      const contentIndex = keyboardHtml.indexOf('mobile-terminal-key-content');
      assert.ok(/mobile-terminal-key-launcher"[\s\S]*aria-expanded="false"[\s\S]*>⌨<\/button>/.test(keyboardHtml) && handleIndex < closeIndex && closeIndex < contentIndex, `the closed launcher stays a keyboard button while the open keybar owns a first-child handle and top-right X (${handleIndex}/${closeIndex}/${contentIndex})`);
      assert.ok(keyboardHtml.includes('data-terminal-mobile-page="primary"') && keyboardHtml.includes('data-terminal-mobile-page="more" hidden'), 'mobile keyboard renders one primary page and one initially hidden More page');
      assert.equal((keyboardHtml.match(/data-terminal-mobile-key="interrupt"/g) || []).length, 2, 'both pages reuse the shared Ctrl-C definition');
      assert.equal((keyboardHtml.match(/data-terminal-mobile-key="more"/g) || []).length, 2, 'both pages retain the More page toggle');
      assert.ok(keyboardHtml.includes('⌘P') && keyboardHtml.includes('⌘V'), 'the touch palette exposes Command-P quick-open and Command-V paste without a physical keyboard');
    }

    {
      const frames = [];
      const api = loadYolomux('', ['1']);
      api.registerTerminalForTest('1', {focus() {}}, {
        readyState: WebSocket.OPEN,
        send(frame) { frames.push(JSON.parse(frame)); },
      });
      api.setFocusedTerminal('1');
      api.clearClientPerfCountersForTest();
      api.noteTerminalExplicitInputForTest('1');
      assert.equal(
        api.clientPerfSummaryForTest().some(counter => counter.name === 'focusSet'),
        false,
        'an already-owned terminal key does not re-enter synchronous focus and attention ownership',
      );
      assert.equal(api.handleTerminalDataForTest('1', 'a'), true, 'the key still reaches the shared terminal transport');
      assert.deepStrictEqual(canonical(frames), [{type: 'input', data: 'a'}]);
      const perf = Object.fromEntries(api.clientPerfSummaryForTest().map(counter => [counter.name, counter]));
      assert.equal(perf['term.onData'].count, 1, 'the root input owner still records one terminal data handler');
      assert.equal(perf.wsSend.count, 1, 'the root input owner still sends one WebSocket frame');
    }

    await testAsync('server/client version mismatch asks whether to reload the browser', async () => {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          settingsPayload: settingsOverride({}, {general: {reload_on_update: true, reload_on_update_auto: false}}),
        },
      });
      api.maybeHandleServerVersionChangeForTest('0.4.19');
      const banner = api.bodyChildren().find(node => node.id === 'serverUpdateBanner');
      assert.ok(banner, 'server/client patch rollback mismatch shows the existing reload banner');
      assert.equal(banner.dataset.version, '0.4.19', 'reload banner stores the mismatched server version');
      assert.ok(banner.children[0].textContent.includes('Do you want to reload the browser?'), 'reload banner asks the user whether to reload');
      const actions = banner.children[1];
      assert.equal(actions.className, 'toast-control-row server-update-banner-actions', 'reload banner groups actions through the shared toast control row');
      assert.equal(actions.children[0].textContent, 'Reload', 'reload banner keeps the existing Reload action');
      assert.equal(actions.children[1].textContent, 'Keep', 'reload banner keeps the existing dismiss action as Keep');
      api.maybeHandleServerVersionChangeForTest('0.4.19');
      assert.equal(api.bodyChildren().filter(node => node.id === 'serverUpdateBanner').length, 1, 'same mismatched version does not spawn repeated banners');
      actions.children[1].listeners.get('click')[0]();
      assert.equal(api.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'Keep dismisses the mismatch banner');
      api.maybeHandleServerVersionChangeForTest('0.4.19');
      assert.equal(api.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'dismissed same mismatch does not immediately reopen');

      const reloadApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          settingsPayload: settingsOverride({}, {general: {reload_on_update: true, reload_on_update_auto: false}}),
        },
      });
      reloadApi.maybeHandleServerVersionChangeForTest('0.4.21');
      const reloadBanner = reloadApi.bodyChildren().find(node => node.id === 'serverUpdateBanner');
      reloadBanner.children[1].children[0].listeners.get('click')[0]();
      assert.equal(reloadApi.reloadCountForTest(), 1, 'Reload action reloads the browser');

      const autoApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          settingsPayload: settingsOverride({}, {general: {reload_on_update: true, reload_on_update_auto: true}}),
        },
      });
      autoApi.setOpenFileStateForTest('/repo/app.py', {kind: 'text', content: 'dirty', original: 'clean', dirty: true});
      autoApi.maybeHandleServerVersionChangeForTest('0.4.21');
      assert.equal(autoApi.reloadCountForTest(), 0, 'dirty editors block automatic reload on server/client mismatch');
      assert.ok(autoApi.bodyChildren().some(node => node.id === 'serverUpdateBanner'), 'dirty auto-reload fallback still shows the existing reload banner');
    });

    await testAsync('server/client bundle revision mismatch asks whether to reload the browser', async () => {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          clientRevision: 'old-client-rev',
          settingsPayload: settingsOverride({}, {general: {reload_on_update: true, reload_on_update_auto: false}}),
        },
      });
      api.maybeHandleServerVersionChangeForTest('0.4.20', 'new-client-rev');
      const banner = api.bodyChildren().find(node => node.id === 'serverUpdateBanner');
      assert.ok(banner, 'same-version bundle revision mismatch shows the existing reload banner');
      assert.equal(banner.dataset.version, 'client:new-client-rev', 'reload banner stores the mismatched bundle revision');
      assert.ok(banner.children[0].textContent.includes('Do you want to reload the browser?'), 'bundle revision banner asks the user whether to reload');
      api.maybeHandleServerVersionChangeForTest('0.4.20', 'new-client-rev');
      assert.equal(api.bodyChildren().filter(node => node.id === 'serverUpdateBanner').length, 1, 'same bundle revision mismatch does not spawn repeated banners');

      const autoApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          clientRevision: 'old-client-rev',
          settingsPayload: settingsOverride({}, {general: {reload_on_update: true, reload_on_update_auto: true}}),
        },
      });
      autoApi.maybeHandleServerVersionChangeForTest('0.4.20', 'new-client-rev');
      assert.equal(autoApi.reloadCountForTest(), 1, 'safe automatic reload fires on same-version bundle revision mismatch');

      const disabledApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          clientRevision: 'old-client-rev',
          settingsPayload: settingsOverride({}, {general: {reload_on_update: false, reload_on_update_auto: true}}),
        },
      });
      disabledApi.maybeHandleServerVersionChangeForTest('0.4.20', 'new-client-rev');
      assert.equal(disabledApi.reloadCountForTest(), 0, 'disabled reload-on-update suppresses bundle revision auto reload');
      assert.equal(disabledApi.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'disabled reload-on-update suppresses bundle revision banner');
    });

    await testAsync('self-update: Update Now removes toast and reloads after restart ping', async () => {
      const api = loadYolomux('', ['1']);
      api.setConfirmForTest(() => true);
      const toasts = [];
      const owner = api.testElementForId('update-toast');
      owner.className = 'attention-alert toast toast-update';
      let actionButton = null;
      api.setShowToastForTest((title, lines, options = {}) => {
        toasts.push({title, lines: Array.isArray(lines) ? lines : [lines]});
        if (title === 'YOLOmux update available') {
          actionButton = options.actions[0];
          owner.appendChild(actionButton);
          return owner;
        }
        return null;
      });
      const fetchCalls = [];
      api.setFetchForTest((url, options = {}) => {
        fetchCalls.push({url: String(url), method: options.method || 'GET'});
        if (String(url).startsWith('/api/self-update')) {
          return Promise.resolve(jsonResponse({
            ok: true,
            restarting: true,
            error: 'updated; restarting now',
            user_message: {key: 'update.result.restarting', params: {}, fallback: 'updated; restarting now'},
            target: '0.4.18',
          }));
        }
        if (String(url).startsWith('/api/ping')) return Promise.resolve(jsonResponse({ok: true}));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      });

      api.applyUpdateAvailableForTest({available: true, notify: true, target: '0.4.18'});
      assert.equal(owner.dataset.updateTarget, '0.4.18', 'update toast carries the target version');
      assert.ok(actionButton, 'Update Now action was rendered');
      actionButton.listeners.get('click')[0]({target: actionButton, stopPropagation() {}});
      assert.equal(owner.removed, true, 'Update Now dismisses the update-available toast before the API returns');

      await flushAsyncWork();
      await flushAsyncWork();
      assert.deepStrictEqual(canonical(fetchCalls[0]), {method: 'POST', url: '/api/self-update'}, 'self-update posts immediately after confirmation');
      assert.ok(toasts.some(item => item.title === 'Installing update...'), 'successful restarting update shows installing status');
      assert.ok(toasts.some(item => item.lines[0] === 'YOLOmux was updated and is restarting now.'), 'self-update resolves the server descriptor through the active locale instead of showing its raw fallback');
      assert.deepStrictEqual(canonical(api.selfUpdateReloadStateForTest()), {
        attempts: 0,
        deferredToastShown: false,
        pending: true,
        serverVersionReloadHandled: '0.4.18',
        target: '0.4.18',
      }, 'successful self-update owns the target version and starts reload polling');

      api.maybeHandleServerVersionChangeForTest('0.4.18');
      assert.equal(api.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'self-update target suppresses the generic reload banner');
      await api.pollSelfUpdateReloadForTest();
      assert.equal(api.reloadCountForTest(), 1, 'reachable restarted server triggers automatic reload');
    });

    await testAsync('self-update: dirty edits and active typing defer automatic reload safely', async () => {
      const dirtyApi = loadYolomux('', ['1']);
      const dirtyToasts = [];
      dirtyApi.setShowToastForTest((title, lines) => {
        dirtyToasts.push({title, lines: Array.isArray(lines) ? lines : [lines]});
        return null;
      });
      dirtyApi.setFetchForTest(() => Promise.resolve(jsonResponse({ok: true})));
      dirtyApi.setOpenFileStateForTest('/repo/app.py', {kind: 'text', content: 'dirty', original: 'clean', dirty: true});
      dirtyApi.startSelfUpdateReloadPollingForTest('0.4.19');
      await dirtyApi.pollSelfUpdateReloadForTest();
      assert.equal(dirtyApi.reloadCountForTest(), 0, 'dirty editors block self-update auto reload');
      assert.equal(dirtyApi.selfUpdateReloadStateForTest().deferredToastShown, true, 'dirty reload deferral is tracked');
      assert.ok(dirtyToasts.some(item => item.title === 'Software Update' && String(item.lines[0]).includes('unsaved edits')), 'dirty deferral shows a self-update-specific toast');
      dirtyApi.maybeHandleServerVersionChangeForTest('0.4.19');
      assert.equal(dirtyApi.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'dirty self-update deferral still suppresses the generic reload banner');

      const typingApi = loadYolomux('', ['1']);
      const typingToasts = [];
      typingApi.setShowToastForTest((title, lines) => {
        typingToasts.push({title, lines: Array.isArray(lines) ? lines : [lines]});
        return null;
      });
      typingApi.setFetchForTest(() => Promise.resolve(jsonResponse({ok: true})));
      const input = typingApi.testElementForId('typing-input');
      input.localName = 'input';
      input.tagName = 'INPUT';
      typingApi.setDocumentActiveElementForTest(input);
      typingApi.startSelfUpdateReloadPollingForTest('0.4.20');
      await typingApi.pollSelfUpdateReloadForTest();
      assert.equal(typingApi.reloadCountForTest(), 0, 'active typing blocks self-update auto reload');
      assert.ok(typingToasts.some(item => item.title === 'Software Update' && String(item.lines[0]).includes('active typing')), 'typing deferral shows a self-update-specific toast');
    });

    await testAsync('self-update: restart polling record replaces timers and terminates one attempt', async () => {
      const timers = [];
      const cleared = [];
      const toasts = [];
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        setTimeout(callback, delay) {
          const id = timers.length + 1;
          timers.push({id, callback, delay});
          return id;
        },
        clearTimeout(id) { cleared.push(id); },
      });
      api.setShowToastForTest((title, lines) => {
        toasts.push({title, lines: Array.isArray(lines) ? lines : [lines]});
        return null;
      });
      api.setFetchForTest(() => Promise.reject(new Error('server restarting')));

      api.startSelfUpdateReloadPollingForTest('0.4.20');
      const priorTimer = timers.at(-1);
      api.startSelfUpdateReloadPollingForTest('0.4.21');
      const replacementTimer = timers.at(-1);
      assert.deepStrictEqual(canonical(api.selfUpdateReloadStateForTest()), {
        attempts: 0,
        deferredToastShown: false,
        pending: true,
        serverVersionReloadHandled: '0.4.21',
        target: '0.4.21',
      }, 'a new attempt resets the complete record and owns the latest target');
      assert.deepStrictEqual(cleared, [priorTimer.id], 'restarting polling clears the prior attempt timer');
      assert.notEqual(replacementTimer.id, priorTimer.id, 'the replacement attempt owns a new timer handle');
      assert.equal(replacementTimer.delay, 0, 'replacement polling is scheduled immediately');

      for (let attempt = 0; attempt < 120; attempt += 1) await api.pollSelfUpdateReloadForTest();
      assert.equal(api.selfUpdateReloadStateForTest().pending, false, 'the complete attempt stops at the retry bound');
      assert.equal(api.selfUpdateReloadStateForTest().attempts, 120, 'the retry count belongs to the stopped attempt');
      assert.ok(toasts.some(item => item.lines[0] === 'Update installed, but YOLOmux did not answer after restart. Reload when it is reachable.'), 'terminal failure reports the bounded timeout');
    });

    test('self-update: retired parallel reload scalars stay absent', () => {
      const src = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
      for (const name of ['selfUpdateReloadPending', 'selfUpdateReloadTarget', 'selfUpdateReloadAttempts', 'selfUpdateReloadTimer', 'selfUpdateReloadDeferredToastShown']) {
        assert.equal(src.includes(name), false, `${name} remains retired`);
      }
      assert.ok(src.includes('const selfUpdateReloadState = {'), 'one reload-state owner remains');
    });

    test('self-update: topbar update badge + dryrun wiring present', () => {
      const src = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(/function applyUpdateAvailable\(/.test(src), 'applyUpdateAvailable present');
      assert.ok(/function checkForUpdateOnce\(/.test(src), 'checkForUpdateOnce present');
      assert.ok(/function triggerSelfUpdate\(/.test(src), 'triggerSelfUpdate present');
      assert.ok(src.includes('/api/self-update'), 'posts to /api/self-update');
      assert.ok(src.includes('/api/update-status'), 'checks /api/update-status');
      assert.ok(src.includes('updateDryRun'), 'dryrun url flag wired');
      assert.ok(src.includes('data-update-badge'), 'topbar update badge selector wired');
      assert.ok(src.includes("'update_available'"), 'subscribes to the update_available client event');
    });

    test('YO!stats exact ranges offer only the server-supported resolution cells', () => {
      const api = loadYolomux('', ['1']);
      const expected = new Map([
        [300, [1, 10]],
        [900, [10, 60]],
        [1800, [10, 60]],
        [3600, [60, 300]],
        [7200, [60, 300]],
        [14400, [60, 300]],
        [28800, [60, 300]],
        [57600, [300]],
        [86400, [300]],
      ]);
      for (const [range, resolutions] of expected) {
        assert.deepStrictEqual(
          [...api.debugGraphExactResolutionChoicesForTest(range)],
          resolutions,
          `${range}s exposes its exact server cells`,
        );
      }
    });

    test('YO!stats source gaps do not overpaint exact family data', () => {
      const api = loadYolomux('', ['1']);
      const seriesValue = value => ({value, source_count: 1, first_timestamp: 0, last_timestamp: 0});
      const snapshot = {
        window_start: 0,
        window_end: 40,
        resolution_seconds: 10,
        buckets: [
          {start: 0, duration: 10, series: {'cpu_percent:retired': seriesValue(1)}},
          {start: 10, duration: 10, series: {}},
          {start: 20, duration: 10, series: {'cpu_percent:current': seriesValue(2)}},
          {start: 30, duration: 10, series: {}},
        ],
        no_data: [
          {family: 'cpu', start: 0, end: 10},
          {family: 'cpu', start: 10, end: 20},
          {family: 'cpu', start: 20, end: 30},
        ],
      };
      assert.deepStrictEqual(
        canonical(api.jsDebugCurrentCoverageIntervalsForTest(snapshot, 'cpu')),
        [
          {startSeconds: 0, endSeconds: 10, resolutionSeconds: 10, sourceResolutionSeconds: 10},
          {startSeconds: 20, endSeconds: 40, resolutionSeconds: 10, sourceResolutionSeconds: 10},
        ],
        'only a source gap with no exact family value becomes a family-wide red band',
      );
    });

    test('YO!stats exact server buckets are never re-aggregated by legacy tiers', () => {
      const api = loadYolomux('', ['1']);
      const now = Date.now();
      api.clearJsDebugGraphDataForTest();
      api.debugGraphApplyServerRecordForTest({start: (now - 16 * 60 * 60 * 1000) / 1000, duration: 300});
      api.compactJsDebugGraphBucketsForTest(now);
      assert.deepStrictEqual(
        [...api.jsDebugGraphBucketDurationsForTest()],
        [300],
        'a 300s exact server cell stays 300s even in the old 600s age tier',
      );
    });

    test('copy affordances route through the shared feedback contract', () => {
      const core = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
      const files = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
      const terminalBoot = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
      const clipboardSourceFiles = fs.readdirSync('static_src/js/yolomux').filter(name => name.endsWith('.js')).sort();
      const rawTextWriterFiles = clipboardSourceFiles.filter(name => fs.readFileSync(`static_src/js/yolomux/${name}`, 'utf8').includes('copyTextToClipboard('));
      const clipboardItemFiles = clipboardSourceFiles.filter(name => fs.readFileSync(`static_src/js/yolomux/${name}`, 'utf8').includes('ClipboardItem'));
      assert.match(core, /function copyTextWithFeedback\(text, options = \{\}\)[\s\S]*copyTextToClipboard\(text\)\.then\([\s\S]*showCopyFeedback\(options\)/, 'one parent owns text-copy success and failure feedback');
      assert.deepEqual(rawTextWriterFiles, ['10_core_utils.js'], 'the raw text writer stays private to the shared feedback parent');
      assert.deepEqual(clipboardItemFiles, ['10_core_utils.js', '45_file_explorer_actions.js'], 'the ClipboardItem inventory is explicit and small enough to enforce');
      assert.equal([...core.matchAll(/copyTextToClipboard\(/g)].length, 2, 'only the shared feedback parent may invoke the raw text clipboard writer');
      assert.match(files, /navigator\.clipboard\.write\(\[new ClipboardItem[\s\S]*showCopyFeedback\(/, 'image clipboard writes report through the shared feedback parent');
      assert.match(core, /function copyTerminalSelectionToClipboardEvent[\s\S]*showCopyFeedback\(/, 'the synchronous terminal copy-event path keeps activation while reporting feedback');
      assert.match(terminalBoot, /scope\.ownEvent\('copy', container, 'copy', event => \{\s*copyTerminalSelectionToClipboardEvent/, 'the real terminal copy listener stays on the synchronous shared path');
    });
}

module.exports = {runLayoutAsyncSuite};

if (require.main === module) {
  runSuites([runLayoutAsyncSuite]);
}

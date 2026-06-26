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

async function runShareThemeSuite() {
  test('t@2560', () => {
    const api = loadYolomux('', ['1', '2']);
    api.setFileExplorerTreeDateModeForTest('date');
    assert.equal(api.TAB_TYPES.map(type => type.key).join(','), 'info,yoagent,files,search-history,preferences,image-viewer,file-editor');
    assert.equal(api.debugModeEnabledForTest(), false, 'JS Debug pane is off without the debug=1 URL flag');
    assert.equal(api.resolveLayoutItem('debug'), 'debug', 'debug layout item is ignored while the URL flag is off');
    assert.equal(api.fileIndexStatusFromPayloadForTest({ready: true, state: 'ready'}), 'ready', 'ready file indexes stop polling');
    assert.equal(api.fileIndexStatusFromPayloadForTest({ready: false, state: 'follower', ready_elsewhere: true}), 'ready', 'follower-owned ready file indexes stop polling');
    assert.equal(api.fileIndexStatusFromPayloadForTest({ready: false, state: 'building'}), 'building', 'building file indexes keep polling');
    // YO!info and YO!agent are independent virtual tabs; legacy yoagent/yosup aliases open YO!agent.
    assert.equal(api.resolveLayoutItem('yoagent'), api.yoagentItemId, 'yoagent alias resolves to the standalone YO!agent item');
    assert.equal(api.resolveLayoutItem('yosup'), api.yoagentItemId, 'legacy yosup URL param resolves to YO!agent');
    assert.equal(api.resolveLayoutItem('__yosup__'), api.yoagentItemId, 'legacy yosup item id resolves to YO!agent');
    assert.equal(api.resolveLayoutItem('__yoagent__'), api.yoagentItemId, 'legacy yoagent item id resolves to YO!agent');
    api.setFileExplorerModeForTest('files');
    assert.equal(api.resolveLayoutItem('changes'), api.fileExplorerItemId, 'legacy changes URL param resolves to the Finder pane');
    assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes URL param preselects Finder diff mode');
    api.setFileExplorerModeForTest('files');
    assert.equal(api.resolveLayoutItem('__changes__'), api.fileExplorerItemId, 'legacy changes item id resolves to the Finder pane');
    assert.equal(api.fileExplorerModeForTest(), 'diff', 'legacy changes item id preselects Finder diff mode');
    assert.equal(api.resolveLayoutItem('files'), api.fileExplorerItemId, 'files alias still resolves to Finder');
    assert.equal(api.resolveLayoutItem('search'), api.searchHistoryItemId, 'search alias resolves to the Search & Runs pane');
    assert.equal(api.resolveLayoutItem('history'), api.searchHistoryItemId, 'history alias resolves to the Search & Runs pane');
    assert.equal(api.resolveLayoutItem('run-history'), api.searchHistoryItemId, 'run-history alias resolves to the Search & Runs pane');
    assert.equal(api.itemParam(api.infoItemId), 'info', 'the YO!info pane uses the info param');
    assert.equal(api.itemParam(api.yoagentItemId), 'yoagent', 'the YO!agent pane uses the yoagent param');
    assert.equal(api.itemParam(api.searchHistoryItemId), 'search-history', 'the Search & Runs pane uses a stable URL param');
    assert.equal(api.tabTypeForItem('__files__').key, 'files');
    assert.equal(api.tabTypeForItem(api.searchHistoryItemId).key, 'search-history');
    assert.equal(api.tabTypeForItem('__changes__'), null, 'standalone Changes tab type is removed');
    assert.equal(api.tabTypeForItem('image:/home/test/screen.png').key, 'image-viewer');
    assert.equal(api.tabTypeForItem('file:/home/test/README.md').key, 'file-editor');
    assert.equal(api.fileItemPath('image:/home/test/screen.png'), '/home/test/screen.png');
    const duplicateEditorItem = api.fileEditorCopyItemFor('/home/test/README.md');
    assert.notEqual(duplicateEditorItem, api.fileEditorItemFor('/home/test/README.md'), 'secondary editor tabs get their own layout item id');
    assert.equal(api.tabTypeForItem(duplicateEditorItem).key, 'file-editor');
    assert.equal(api.fileItemPath(duplicateEditorItem), '/home/test/README.md', 'secondary editor tabs still map to the same backing file path');
    assert.equal(api.resolveLayoutItem(duplicateEditorItem), duplicateEditorItem, 'secondary editor tab ids restore from the layout URL');
    const differPreviewItem = api.fileEditorDiffPreviewItemFor('/home/test/README.md');
    assert.equal(api.tabTypeForItem(differPreviewItem).key, 'file-editor');
    assert.equal(api.fileItemPath(differPreviewItem), '/home/test/README.md', 'Differ preview tabs map to the same backing file path');
    assert.equal(api.resolveLayoutItem(differPreviewItem), differPreviewItem, 'Differ preview tab ids restore from the layout URL');
    api.setSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
      repos: [{repo: '/repo/app', count: 3, touched_count: 4, added: 10, removed: 1, behind: 0, ahead: 2}],
      files: [
        {session: '1', agent: 'codex', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
        {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: 'src/new.py', abs_path: '/repo/app/src/new.py', mtime: 200, added: 8, removed: 0, diff_tracked: true},
        {session: '1', agent: 'codex', status: '?', repo: '/repo/app', path: 'src/raw.txt', abs_path: '/repo/app/src/raw.txt', mtime: 220, added: 4, removed: 0, diff_tracked: false},
        {session: '1', agent: 'codex', status: 'T', repo: '/repo/app', path: 'src/touched-only.py', abs_path: '/repo/app/src/touched-only.py', mtime: 300, added: 0, removed: 0, source: 'transcript'},
      ],
    });
    api.setFileExplorerModeForTest('diff');
    const changesHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(changesHtml.includes('/repo/app'));
    const repoHeadStart = changesHtml.indexOf('class="changes-repo-head"');
    const repoHead = changesHtml.slice(repoHeadStart, changesHtml.indexOf('</button>', repoHeadStart));
    assert.ok(/changes-repo-caret[\s\S]*changes-repo-title[^>]*>\/repo\/app<[\s\S]*changes-repo-totals[\s\S]*changes-diff-add[^>]*>\+10<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*changes-repo-count[^>]*>3<\/span>/.test(repoHead), 'repo disclosure header shows repo name, tracked +added, -removed, and file count');
    assert.equal(/Behind|Ahead/.test(repoHead), false, 'ahead/behind stays out of the repo disclosure header');
    assert.ok(changesHtml.includes('1 repo, 3 files changed in &#39;1&#39;'), 'Finder diff summary names repo count, file count, and session explicitly');
    const comparisonSummaryStart = changesHtml.indexOf('class="changes-comparison-summary"');
    const comparisonSummary = changesHtml.slice(comparisonSummaryStart, changesHtml.indexOf('</div>', comparisonSummaryStart));
    assert.equal(/changes-summary-totals|changes-diff-add|changes-diff-remove|changes-repo-count/.test(comparisonSummary), false, 'Finder diff summary does not repeat global +line/-line/file totals');
    // D4: a per-agent transcript-missing message arrives in payload.warnings (NOT payload.errors) and must
    // render as a non-blocking changes-warning notice while valid changed files/repos still render.
    api.setSessionFilesPayloadForTest({
      session: '3', loaded: true, errors: [],
      warnings: ['codex transcript not found by process fd or cwd'],
      refs_by_repo: {}, repos: [{repo: '/repo/app', count: 1, added: 1, removed: 0}],
      files: [{session: '3', agent: 'codex', repo: '/repo/app', path: 'a.py', abs_path: '/repo/app/a.py', mtime: 100, added: 1, removed: 0}],
    });
    api.setFileExplorerModeForTest('diff');
    const warnHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(warnHtml.includes('<div class="changes-warning">codex transcript not found by process fd or cwd</div>'), 'a per-agent transcript-missing message renders as a non-blocking changes-warning notice');
    assert.equal(warnHtml.includes('class="changes-error"'), false, 'a non-blocking warning is NOT rendered as a red changes-error');
    assert.ok(warnHtml.includes('/repo/app'), 'the valid changed repo still renders alongside the warning (not blocked)');
    api.setSessionFilesPayloadForTest({session: '2', loaded: false, errors: [], refs_by_repo: {}, repos: [], files: []});
    api.setSessionFilesLoadingForTest(true);
    const loadingDifferHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(/changes-loading[\s\S]*session-yolo-marker active working changes-loading-yolo[\s\S]*loading 2[\s\S]*moving-ellipsis changes-loading-dots/.test(loadingDifferHtml), 'Differ loading state uses the spinning YO marker, session label, and shared moving dots');
    assert.equal(loadingDifferHtml.includes('not loaded'), false, 'Differ loading state does not flash "not loaded" while a session switch fetch is in flight');
    const sessionSwitchSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const sessionSwitchBody = sessionSwitchSource.slice(sessionSwitchSource.indexOf('function switchFileExplorerChangesSession('), sessionSwitchSource.indexOf('function noteFileExplorerChangesSessionInteraction('));
    assert.ok(/setSessionFilesLoadingForDestination\('finder', !cachedPayloadIsLoaded\);\s*scheduleFileExplorerActiveTabSync\(session, \{explicit: true\}\);\s*renderFileExplorerChangesPanels\(\);\s*fetchSessionFiles\(\{destination: 'finder', session, silent: true, force: true, background: cachedPayloadIsLoaded\}\);/.test(sessionSwitchBody), 'auto-switching Differ sessions shows loading only when no loaded cached payload is available while Finder Sync updates immediately');
    assert.ok(/const backgroundRefresh = options\.background === true;[\s\S]*if \(!backgroundRefresh\) setSessionFilesLoadingForDestination\(destination, true\);[\s\S]*if \(current && !backgroundRefresh\) setSessionFilesLoadingForDestination\(destination, false\);/.test(sessionSwitchSource), 'background Differ refreshes do not replace cached content with the foreground loading state');
    api.setSessionFilesLoadingForTest(false);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setSessionFilesCachePayloadForTest('2', {
      session: '2',
      loaded: true,
      errors: [],
      refs_by_repo: {},
      repos: [{repo: '/repo/cached', count: 1, touched_count: 1, added: 1, removed: 0}],
      files: [{session: '2', agent: 'codex', status: 'M', repo: '/repo/cached', path: 'cached-visible.py', abs_path: '/repo/cached/cached-visible.py', mtime: 500, added: 1, removed: 0}],
    });
    let cachedRefreshUrl = '';
    api.setFetchForTest(url => {
      cachedRefreshUrl = String(url);
      return new Promise(() => {});
    });
    const mountedChangesPanel = api.testElementForId('mounted-changes-panel');
    mountedChangesPanel.className = 'file-explorer-changes-panel';
    api.setDocumentQuerySelectorForTest(selector => selector === '.file-explorer-changes-panel' ? mountedChangesPanel : null);
    api.setDocumentQuerySelectorAllForTest(() => []);
    assert.equal(api.noteFileExplorerChangesSessionInteractionForTest('2'), true, 'cached Differ switch still changes the selected session');
    api.setDocumentQuerySelectorForTest(() => null);
    api.setDocumentQuerySelectorAllForTest(() => []);
    const cachedSwitchHtml = api.fileExplorerChangesPanelHtml();
    assert.equal(cachedSwitchHtml.includes('changes-loading'), false, 'cached Differ switch keeps rendered rows instead of showing the loading state');
    assert.ok(cachedSwitchHtml.includes('cached-visible.py'), 'cached Differ switch renders the cached changed-file rows immediately');
    assert.ok(cachedRefreshUrl.includes('/api/session-files?') && cachedRefreshUrl.includes('session=2') && cachedRefreshUrl.includes('force=1'), 'cached Differ switch still starts a forced background refresh');
    api.setFileExplorerModeForTest('files');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setSessionFilesPayloadForTest({session: '1', loaded: true, errors: [], refs_by_repo: {}, repos: [{repo: '/repo/current'}], files: []});
    api.setSessionFilesCachePayloadForTest('2', {
      session: '2',
      loaded: true,
      errors: [],
      refs_by_repo: {},
      repos: [{repo: '/repo/cached-finder', count: 1, touched_count: 1, added: 1, removed: 0}],
      files: [{session: '2', agent: 'codex', status: 'M', repo: '/repo/cached-finder', path: 'cached-finder-visible.py', abs_path: '/repo/cached-finder/cached-finder-visible.py', mtime: 600, added: 1, removed: 0}],
    });
    let cachedFinderRefreshUrl = '';
    api.setFetchForTest(url => {
      cachedFinderRefreshUrl = String(url);
      return new Promise(() => {});
    });
    api.setDocumentQuerySelectorForTest(selector => selector === '.file-explorer-changes-panel' ? mountedChangesPanel : null);
    api.setDocumentQuerySelectorAllForTest(() => []);
    assert.equal(api.noteFileExplorerChangesSessionInteractionForTest('2'), true, 'cached Finder switch still changes the selected session');
    api.setDocumentQuerySelectorForTest(() => null);
    api.setDocumentQuerySelectorAllForTest(() => []);
    const cachedFinderPayload = api.sessionFilesPayloadForTest();
    assert.equal(cachedFinderPayload.session, '2', 'cached Finder switch applies the target session payload immediately');
    assert.equal(cachedFinderPayload.repos[0].repo, '/repo/cached-finder', 'cached Finder switch uses the cached target repo before the fresh fetch resolves');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('cached-finder-visible.py'), 'cached Finder switch renders the embedded Differ rows immediately');
    assert.ok(cachedFinderRefreshUrl.includes('/api/session-files?') && cachedFinderRefreshUrl.includes('session=2') && cachedFinderRefreshUrl.includes('force=1'), 'cached Finder switch still starts a forced background refresh');
    api.setSessionFilesPayloadForTest({session: '2', loaded: false, errors: [], refs_by_repo: {}, repos: [], files: []});
    api.setSessionFilesLoadingForTest(true);
    api.setFileExplorerModeForTest('files');
    const loadingEmbeddedDifferHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(/changes-comparison-head compact[\s\S]*changes-loading[\s\S]*session-yolo-marker active working changes-loading-yolo/.test(loadingEmbeddedDifferHtml), 'embedded Finder Differ loading header uses the same moving YO indicator');
    assert.equal(loadingEmbeddedDifferHtml.includes('not loaded'), false, 'embedded Finder Differ loading header does not flash "not loaded"');
    api.setFileExplorerModeForTest('diff');
    api.setSessionFilesLoadingForTest(false);
    api.setSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
      repos: [{repo: '/repo/app', count: 3, touched_count: 4, added: 10, removed: 1, behind: 0, ahead: 2}],
      files: [
        {session: '1', agent: 'codex', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
        {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: 'src/new.py', abs_path: '/repo/app/src/new.py', mtime: 200, added: 8, removed: 0, diff_tracked: true},
        {session: '1', agent: 'codex', status: '?', repo: '/repo/app', path: 'src/raw.txt', abs_path: '/repo/app/src/raw.txt', mtime: 220, added: 4, removed: 0, diff_tracked: false},
        {session: '1', agent: 'codex', status: 'T', repo: '/repo/app', path: 'src/touched-only.py', abs_path: '/repo/app/src/touched-only.py', mtime: 300, added: 0, removed: 0, source: 'transcript'},
      ],
    });
    assert.ok(/changes-repo-refs[\s\S]*changes-repo-compare-title[\s\S]*Comparing[\s\S]*data-diff-ref-from[\s\S]*to[\s\S]*data-diff-ref-to/.test(changesHtml), 'Finder diff shows a per-repo comparison row with inline FROM/TO controls');
    assert.equal((changesHtml.match(/repo, 3 files changed in &#39;1&#39;/g) || []).length, 1, '#24: the repo/file-count summary appears exactly once (in the comparison card), not duplicated in the toolbar');
    assert.equal(changesHtml.includes('class="changes-summary"'), false, '#24: the standalone toolbar summary duplicate is removed');
    assert.ok(changesHtml.includes('class="changes-comparison-summary"'), '#24: the summary lives in the comparison card');
    assert.equal(changesHtml.includes('touched-only.py'), false, 'Differ hides transcript-only T rows that have no real diff');
    const fullToolbarSlice = changesHtml.slice(changesHtml.indexOf('changes-toolbar'), changesHtml.indexOf('</div>', changesHtml.indexOf('changes-toolbar')));
    assert.equal(fullToolbarSlice.includes('data-session-files-session'), false, 'full Differ body toolbar no longer repeats the Session dropdown');
    assert.ok(/data-session-files-sort[\s\S]*data-file-explorer-tree-dates[\s\S]*data-session-files-refresh/.test(fullToolbarSlice), 'full Differ body toolbar keeps Sort, date mode, and Reload');
    assert.ok(/data-file-explorer-tree-dates[\s\S]*data-file-tree-expand-collapse-all="expand"[\s\S]*data-file-tree-expand-collapse-all="collapse"[\s\S]*data-session-files-refresh/.test(fullToolbarSlice), 'full Differ body toolbar orders Date, Expand all, Collapse all, Reload');
    assert.ok(changesHtml.includes('Behind 0 commits'), 'Finder diff shows behind count');
    assert.ok(changesHtml.includes('Ahead 2 commits'), 'Finder diff shows ahead count');
    assert.ok(changesHtml.includes('file-tree-name">src'), 'Finder diff groups nested paths under folders');
    assert.ok(changesHtml.includes('data-changes-folder-toggle="/repo/app/src"'), 'Changes tree folders are collapsible by a stable key');
    assert.ok(changesHtml.includes('data-open-change-directory="/repo/app/src"'), 'Changes tree folders carry the absolute directory path for the context menu');
    assert.ok(changesHtml.includes('data-change-rel="src"'), 'Changes tree folders carry the relative directory path for copy');
    assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'), 'file leaves keep the open-file action');
    assert.ok(changesHtml.includes('changes-diff-add">+8</span>'), 'changed-file rows include green added counts');
    assert.ok(changesHtml.includes('changes-diff-add-neutral">+4</span>'), 'non-git-diff raw added counts stay visible but neutral');
    assert.ok(changesHtml.includes('changes-file-agent'), 'changed-file rows show the agent icon slot');
    assert.ok(changesHtml.includes('file-tree-row kind-file git-modified has-agent'), 'changed-file rows use the shared file-tree row renderer and inline-agent layout');
    assert.ok(/file-tree-git-status"[^>]*title="M: modified"[^>]*aria-label="M: modified"[^>]*>M<\/span>/.test(changesHtml), 'changed-file rows show and label the M status badge in the shared file-tree status slot');
    assert.ok(/file-tree-git-status"[^>]*title="A: added"[^>]*aria-label="A: added"[^>]*>A<\/span>/.test(changesHtml), 'added changed-file rows label the A status badge');
    assert.ok(changesHtml.includes('file-tree-dir-count">2</span>'), 'changed-file folders show a bare recursive changed-file count from the shared row renderer');
    assert.equal(changesHtml.includes('files changed</span>'), false, 'changed-file folders do not repeat the file-count label in the tree row metadata');
    assert.ok(changesHtml.includes('file-tree-icon'), 'changed-file rows show a file-type icon slot');
    assert.ok(changesHtml.includes('file-tree-date'), 'changed-file rows wrap the date for skinny styling');
    assert.ok(/class="file-tree-row kind-dir[^"]*"[^>]*data-path="\/repo\/app\/src"[\s\S]*<span class="file-tree-date"[^>]*>[^<]+<\/span>/.test(changesHtml), 'Differ directory rows show the same non-empty date slot as Finder');
    assert.ok(/class="[^"]*file-explorer-date-toggle[^"]*changes-date-toggle[^"]*active[^"]*"[^>]*data-file-explorer-tree-dates[^>]*>Date<\/button>/.test(changesHtml), 'Finder diff toolbar exposes the active-colored shared Finder date-mode button');
    const collapseToggleHtml = api.fileExplorerChangesCollapseToggleHtml();
    assert.ok(collapseToggleHtml.includes('data-session-files-collapse-toggle'), 'Differ collapse helper still exposes collapse/expand all behavior for legacy callers');
    assert.ok(collapseToggleHtml.includes('Collapse all'), 'Differ collapse helper starts in collapse-all state');
    assert.equal(collapseToggleHtml.includes('▤'), false, 'Differ collapse toggle does not use the old square-looking Finder icon');
    assert.equal(api.fileExplorerChangesAllReposCollapsedForTest(), false, 'Differ repos start expanded');
    api.toggleAllFileExplorerChangesForTest();
    assert.equal(api.fileExplorerChangesAllReposCollapsedForTest(), true, 'Differ collapse toggle collapses every repo');
    assert.equal(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), false, 'collapsed Differ repo hides file leaves');
    assert.ok(api.fileExplorerChangesCollapseToggleHtml().includes('Expand all'), 'Differ collapse toggle switches to expand-all state');
    api.toggleAllFileExplorerChangesForTest();
    assert.equal(api.fileExplorerChangesAllReposCollapsedForTest(), false, 'Differ collapse toggle expands every repo again');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), 'expanded Differ repo shows file leaves again');
    api.setChangesFolderCollapsedForTest(['/repo/app/src']);
    const collapsedChangesHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(collapsedChangesHtml.includes('file-tree-row kind-dir collapsed'), 'collapsed changed-file folders keep their state');
    assert.equal(collapsedChangesHtml.includes('data-open-change-file="/repo/app/src/new.py"'), false, 'collapsed changed-file folders hide file leaves');
    const differExpandCollapseSource = {closest: selector => selector === '.file-explorer-changes-panel' ? {} : null};
    api.setAllFileTreeDirectoriesExpandedForTest(differExpandCollapseSource, false);
    assert.deepStrictEqual(canonical(api.changesRepoCollapsedForTest()), ['/repo/app'], 'Differ Collapse all collapses every repo root section');
    assert.deepStrictEqual(canonical(api.changesFolderCollapsedForTest()), ['/repo/app/src'], 'Differ Collapse all records every changed directory row as collapsed');
    assert.equal(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), false, 'Differ Collapse all hides changed file leaves');
    api.setAllFileTreeDirectoriesExpandedForTest(differExpandCollapseSource, true);
    assert.deepStrictEqual(canonical(api.changesRepoCollapsedForTest()), [], 'Differ Expand all expands repo root sections');
    assert.deepStrictEqual(canonical(api.changesFolderCollapsedForTest()), [], 'Differ Expand all expands changed directory rows');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('data-open-change-file="/repo/app/src/new.py"'), 'Differ Expand all shows changed file leaves');
    api.setChangesFolderCollapsedForTest([]);
    assert.ok(changesHtml.includes('data-diff-ref-from'), 'Finder diff exposes FROM ref picker');
    assert.ok(changesHtml.includes('data-diff-ref-to'), 'Finder diff exposes TO ref picker');
    assert.ok(changesHtml.includes('data-diff-ref-input'), 'Finder diff exposes text ref pickers');
    assert.ok(changesHtml.includes('data-diff-ref-reset'), 'Finder diff exposes the shared FROM/TO reset button');
    assert.ok(changesHtml.includes(`aria-label="${api.t('diff.ref.reset')}"`), 'Diff reset buttons carry an aria-label');
    assert.ok(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(changesHtml), 'Finder diff FROM ref picker is a text input with the compact suggestion popup');
    assert.ok(/<input(?=[^>]*data-diff-ref-to)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(changesHtml), 'Finder diff TO ref picker is a text input with the compact suggestion popup');
    assert.equal(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*list=)[^>]*>/.test(changesHtml), false, 'FROM ref picker does not use the browser-native datalist popup');
    assert.equal(/<datalist/.test(changesHtml), false, 'diff ref pickers render no native datalist menu');
    // C6: the FROM/TO controls are now scoped to each repo header (data-diff-ref-repo), not one global pair.
    assert.ok(changesHtml.includes('data-diff-ref-repo="/repo/app"'), 'C6: each repo header carries its own scoped FROM/TO controls');
    const toolbarSlice = changesHtml.slice(changesHtml.indexOf('changes-toolbar'), changesHtml.indexOf('</div>', changesHtml.indexOf('changes-toolbar')));
    assert.equal(toolbarSlice.includes('data-diff-ref'), false, 'C6: the global FROM/TO pair is gone from the Changes toolbar (now per-repo)');
    assert.ok(changesHtml.includes('changes-repo-compare-title'), 'C6: each repo header shows its own comparison title');
    // C6: per-repo suggestions — an unknown repo offers only HEAD as a FROM base (no cross-repo SHAs).
    assert.equal(api.diffRefFromSuggestions('/no/such/repo').length, 1, 'C6: an unknown repo offers only HEAD as a FROM base');
    assert.equal(/<select[^>]*data-diff-ref-from/.test(changesHtml), false, 'FROM control is a text input, not a select');
    assert.equal(/<select[^>]*data-diff-ref-to/.test(changesHtml), false, 'TO control is a text input, not a select');
    assert.ok(api.diffRefFromSuggestions('/repo/app').some(item => item.subject === 'older base commit'), 'Finder diff FROM picker has recent commit subjects available to the popup');
    assert.equal(api.diffRefFromSuggestions().some(item => item.ref === 'current'), false, 'FROM picker does not suggest current as the older base');
    assert.equal(api.diffRefToSuggestions('HEAD').map(item => item.ref).join(','), 'current', 'TO picker only offers refs newer than the selected FROM base');
    api.setDiffRefsByRepoForTest('/repo/app', {from: '611d3bb', to: '56c8fc4'});
    assert.equal(api.fileRepoForPath('/repo/app/README.md'), '/repo/app', 'C6: editor diff refs use the exact changed-file repo when the file is in the Modified-files payload');
    api.setSessionFilesPayloadForTest({session: '1', loaded: true, errors: [], refs_by_repo: {}, repos: [{repo: '/repo/app'}], files: []});
    assert.equal(api.fileRepoForPath('/repo/app/src/unchanged.py'), '/repo/app', 'C6: editor diff refs can infer the repo from the Modified-files repo header even when the file row is absent');
    api.setSessionFilesPayloadForTest({session: '1', loaded: true, errors: [], refs_by_repo: {}, repos: [], files: []});
    api.setOpenFileStateForTest('/repo/app/src/unchanged.py', {kind: 'text', diffRepo: '/repo/app'});
    const loadedEditorRepo = api.fileRepoForPath('/repo/app/src/unchanged.py');
    assert.equal(loadedEditorRepo, '/repo/app', 'C6: editor diff refs fall back to the repo returned by a previous /api/fs/diff payload');
    assert.equal(api.diffRefParams(loadedEditorRepo).from, '611d3bb', 'C6: an editor-only diff repo still picks the repo-scoped FROM ref');
    assert.equal(api.diffRefParams(loadedEditorRepo).to, '56c8fc4', 'C6: an editor-only diff repo still picks the repo-scoped TO ref');
    const duplicateShaOptions = api.diffRefSelectOptionsHtml('abc123def4567890', {suggestions: [{ref: 'abc123d', short: 'abc123d', subject: 'same commit'}]});
    assert.equal((duplicateShaOptions.match(/<option/g) || []).length, 1, 'diff ref picker dedupes full SHA and short SHA for the same commit');
    assert.equal(duplicateShaOptions.includes('selected ref'), false, 'diff ref picker does not add a synthetic duplicate for a selected SHA already in suggestions');
    api.setDiffRefsByRepoForTest('/repo/app', null);
    api.setSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [
        {ref: 'HEAD', short: 'abc123d/HEAD origin/main main', subject: 'current head commit', commit: 'abc123def4567890', aliases: ['HEAD', 'origin/main', 'main']},
        {ref: 'abc123def4567890', short: 'abc123d/origin/main main', subject: 'current head commit', aliases: ['origin/main', 'main']},
        {ref: '9876543210000000', short: '9876543', subject: 'older commit'},
      ]},
      repos: [{repo: '/repo/app', count: 0, touched_count: 0, added: 0, removed: 0, from_ref: 'HEAD', to_ref: 'current'}],
      files: [],
    });
    const collapsedHeadRefs = api.diffRefFromSuggestions('/repo/app');
    assert.deepEqual(collapsedHeadRefs.map(item => item.short), ['abc123d/HEAD origin/main main', '9876543'], 'Differ FROM refs collapse duplicate HEAD and head-SHA entries into one labeled ref');
    assert.deepEqual(collapsedHeadRefs[0].aliases, ['HEAD', 'origin/main', 'main'], 'Differ FROM refs preserve all same-commit aliases on the collapsed HEAD row');
    assert.deepEqual(api.diffRefPopoverItems('origin/main', {compact: true, suggestions: collapsedHeadRefs}).map(item => item.short), ['abc123d/HEAD origin/main main'], 'Differ ref popup searches same-commit branch aliases');
    assert.equal(api.diffRefPopoverItems('', {compact: true, suggestions: collapsedHeadRefs, showAll: true}).some(item => item.short === 'abc123d'), false, 'Differ ref popup does not keep the duplicate short-SHA row for HEAD');
    const collapsedDifferHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*value="abc123d\/HEAD origin\/main main")[^>]*>/.test(collapsedDifferHtml), 'Differ comparison row shows the collapsed short-SHA/HEAD label and same-commit branch aliases');
    const historyHeadPath = '/repo/app/src/history-head.md';
    api.setOpenFileStateForTest(historyHeadPath, {
      kind: 'text',
      gitTracked: true,
      gitHasHistory: true,
      gitHistory: [
        {ref: 'HEAD', short: 'abc123d/HEAD origin/main main', subject: 'current head commit', commit: 'abc123def4567890', aliases: ['HEAD', 'origin/main', 'main']},
        {ref: 'abc123def4567890', short: 'abc123d/origin/main main', subject: 'current head commit', aliases: ['origin/main', 'main']},
        {ref: '9876543210000000', short: '9876543', subject: 'older commit'},
      ],
    });
    const collapsedEditorRefs = api.diffRefControlsHtml({compact: true, repo: '/repo/app', path: historyHeadPath});
    assert.ok(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*value="abc123d\/HEAD origin\/main main")[^>]*>/.test(collapsedEditorRefs), 'Diff Editor ref toolbar shows the same collapsed short-SHA/HEAD label and same-commit branch aliases');
    assert.equal((collapsedEditorRefs.match(/abc123d(?!\/HEAD)/g) || []).length, 0, 'Diff Editor ref toolbar does not show a separate duplicate short-SHA label for HEAD');
    const manyDiffRefs = Array.from({length: 120}, (_, index) => ({ref: `${String(index).padStart(7, 'a')}abcdef`, short: `r${index}`, subject: `commit ${index}`}));
    const changedFilesSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const fileExplorerSource = (fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8') + fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8'));
    assert.equal(api.diffRefPopoverItems('', {compact: true, suggestions: manyDiffRefs, showAll: true}).length, 12, 'compact diff-ref popups are capped to avoid huge menus');
    assert.equal(api.diffRefPopoverItems('', {compact: false, suggestions: manyDiffRefs, showAll: true}).length, 18, 'full diff-ref popups are capped to a compact menu size');
    assert.deepEqual(api.diffRefPopoverItems('commit 117', {compact: true, suggestions: manyDiffRefs}).map(item => item.subject), ['commit 117'], 'typing filters the diff-ref popup to matching refs/subjects');
    assert.ok(/function diffRefComparisonLineHtml[\s\S]*diffRefResetButtonHtml\(refs\)/.test(changedFilesSource), 'Editor and Differ FROM/TO rows share the same reset button helper');
    assert.ok(/function diffRefControlsHtml[\s\S]*diffRefResetButtonHtml\(refs\)/.test(changedFilesSource), 'compact Editor FROM/TO controls use the shared reset button helper');
    assert.ok(/function diffRefResetButtonHtml[\s\S]*t\('pref\.reset\.row'\)[\s\S]*<\/button>/.test(changedFilesSource), 'Diff reset button visibly says Reset');
    assert.equal(/function diffRefResetButtonHtml[\s\S]*>⇤<\/button>/.test(changedFilesSource), false, 'Diff reset button does not use a glyph-only label');
    assert.equal(/function diffRefResetButtonHtml[\s\S]*>↺<\/button>/.test(changedFilesSource), false, 'Diff reset button does not use the reload-looking circular arrow');
    assert.ok(/function commitDiffRefControls[\s\S]*dataset\?\.diffRefPath[\s\S]*setRepoDiffRefs\(repo, fromInput\?\.value, toInput\?\.value, \{path\}\)/.test(changedFilesSource), 'editor diff ref commits carry the file path so no-history files do not fall back to repo history');
    assert.ok(/function syncDiffRefControlValues[\s\S]*dataset\?\.diffRefPath[\s\S]*diffRefFromSuggestions\(repo, path\)[\s\S]*diffRefToSuggestions\(refs\.from, repo, path\)/.test(changedFilesSource), 'editor diff ref sync keeps using file-scoped history after rerenders');
    assert.ok(/status\.textContent !== statusText[\s\S]*date\.textContent !== dateText/.test(changedFilesSource), 'Differ/Finder metadata slots avoid rewriting unchanged status/date text');
    assert.ok(/setClassNameIfChanged\(icon,[\s\S]*file-icon-dir-indexed/.test(changedFilesSource), 'indexed directory icon class is not rewritten when unchanged');
    // the picker-open is now the shared openDiffRefPickerForInput helper (used by both the
    // changes panel and the file-editor diff-ref toolbar) instead of two inline copies.
    assert.ok(/function openDiffRefPickerForInput\([^)]*\)\s*\{[\s\S]*?showDiffRefPicker\(input, \{showAll: true\}\)/.test(changedFilesSource), 'clicking/focusing a filled ref input still shows available options (via shared openDiffRefPickerForInput)');
    assert.ok(changedFilesSource.includes('openDiffRefPickerForInput(diffRefInput, diffRefInput.closest('), 'changes panel routes ref-input focus/pointer through the shared picker-open helper');
    assert.ok(/const minWidth = Math\.min\(compact \? 880 : 960, viewportWidth - 16\)/.test(changedFilesSource), 'diff-ref popup is wide enough for normal 80-char commit subjects');
    assert.ok(/const maxWidth = compact \? 1040 : 1120/.test(changedFilesSource), 'diff-ref popup can expand beyond the old narrow 620px cap');
    assert.ok(/document\.addEventListener\('scroll', event =>[\s\S]*positionDiffRefPopover\(diffRefPopoverInput, context\.compact\)/.test(changedFilesSource), 'scrolling around an open diff-ref popup repositions it instead of closing it');
    assert.equal(changedFilesSource.includes('.showPicker('), false, 'diff refs do not use the browser-native popup API');
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'HEAD', short: 'HEAD', subject: 'base commit'}, {ref: 'current', short: 'current', subject: 'working tree'}]},
      repos: [{repo: '/repo/app', count: 0, touched_count: 1, added: 0, removed: 0, from_ref: 'HEAD', to_ref: 'current', behind: 0, ahead: 0}],
      files: [],
    });
    const emptyExplicitRepoHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(emptyExplicitRepoHtml.includes('/repo/app'), 'Differ keeps an explicit repo section visible even when the selected refs have zero file diffs');
    assert.ok(/changes-repo-refs[\s\S]*data-diff-ref-from[\s\S]*data-diff-ref-to/.test(emptyExplicitRepoHtml), 'empty explicit repo section still exposes FROM/TO controls');
    assert.ok(emptyExplicitRepoHtml.includes('No Differ results for this session.'), 'empty explicit repo section explains that the selected refs have no visible file rows');
    assert.equal(emptyExplicitRepoHtml.includes('data-open-change-file='), false, 'empty explicit repo section does not invent transcript-only file rows');
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '3',
      loaded: true,
      errors: [],
      refs_by_repo: {'/home/test/frontend-crates3': [{ref: 'HEAD', short: 'HEAD', subject: 'base commit'}, {ref: 'current', short: 'current', subject: 'working tree'}]},
      repos: [{repo: '/home/test/frontend-crates3', count: 0, touched_count: 0, added: 0, removed: 0, from_ref: 'default', to_ref: 'base', behind: 0, ahead: 0}],
      files: [],
    });
    const emptySessionRepoHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(emptySessionRepoHtml.includes('/home/test/frontend-crates3'), 'Differ keeps the selected tab repo section visible even when the working-tree diff is empty');
    assert.ok(emptySessionRepoHtml.includes('1 repo, 0 files'), 'empty selected-tab repo still counts as a repo in the Differ summary');
    assert.ok(emptySessionRepoHtml.includes('No Differ results for this session.'), 'empty selected-tab repo explains that it has no visible file rows');
    assert.equal(changedFilesSource.includes("panel.addEventListener('dblclick', async event => {"), false, 'modified-file rows no longer require double-click to open');
    assert.ok(/panel\.addEventListener\('click', async event => \{[\s\S]*?data-open-change-file[\s\S]*?differTreeInteractionController\.handleClick\(event, panel, \{row\}\)/.test(changedFilesSource), 'single-clicking a Differ file row routes through the shared controller');
    assert.ok(/const differTreeInteractionController = createSharedTreeInteractionController\(\{[\s\S]*selectFromClick\(row, id, event\)[\s\S]*updateFileTreeSelectionFromClick\(row, id \|\| differTreeRowPath\(row\), event\)[\s\S]*activateRow\(row, event\)[\s\S]*openChangedFileInDiff/.test(changedFilesSource), 'the shared Differ controller selects first and opens the reusable diff tab unless it is a modifier-selection click');
    const compactChangeHtml = api.changesGroupsSnapshotHtmlForTest([
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
    ], {compact: true});
    const gitStatusClassCases = {
      A: 'git-untracked',
      U: 'git-untracked',
      '?': 'git-untracked',
      D: 'git-deleted',
      S: 'git-staged',
      M: 'git-modified',
      T: 'git-transcript',
    };
    for (const [status, expectedClass] of Object.entries(gitStatusClassCases)) {
      assert.equal(api.gitStatusRowClass(status), expectedClass, `shared git status row class for ${status}`);
    }
    assert.ok(/file-tree-name[^>]*>README\.md<\/span>[\s\S]*file-tree-agent[\s\S]*changes-file-agent[\s\S]*file-tree-diff[\s\S]*changes-diff-add[^>]*>\+2<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*file-tree-dir-count[\s\S]*file-tree-git-status[^>]*>M<\/span>[\s\S]*file-tree-date/.test(compactChangeHtml), 'compact changed-file row order is file, AI icon, diff counts, file count, status, date');
    assert.ok(/file-tree-git-status[^>]*title="M: modified"[^>]*aria-label="M: modified"[^>]*>M<\/span>/.test(compactChangeHtml), 'compact changed-file M badge explains itself on hover');
    const missingChangeHtml = api.changesGroupsSnapshotHtmlForTest([
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'docs/GUI_SPECS.md', abs_path: '/repo/app/docs/GUI_SPECS.md', mtime: 0, added: 1, removed: 0, missing: true},
    ], {compact: true});
    assert.ok(/file-tree-git-status[^>]*title="D: deleted"[^>]*aria-label="D: deleted"[^>]*>D<\/span>/.test(missingChangeHtml), 'stat-less changed-file rows display as missing/deleted instead of ordinary modified');
    assert.ok(/file-tree-date[^>]*>—<\/span>/.test(missingChangeHtml), 'stat-less changed-file rows keep the date column visible with a placeholder');
    assert.equal(changesHtml.includes('>codex<'), false, 'changed-file rows do not spell out the agent kind');
    assert.ok(changesHtml.includes('data-open-change-file="/repo/app/src/new.py"'));
    assert.ok(changesHtml.includes('data-open-change-status="A"'), 'changed-file clicks carry status for deleted-file diff opens');
    assert.equal(changedFilesSource.includes("const isTouchedOnly = normalizedStatus === 'T';"), false, 'touched-only transcript rows no longer hard-bypass the Diff editor');
    assert.ok(changedFilesSource.includes("const openDiffMode = options.openMode !== 'edit';"), 'ordinary Differ row opens build the CodeMirror diff view by default');
    assert.ok(changedFilesSource.includes("if (openDiffMode) setFileEditorDiffExpandUnchangedForItem(path, item, false);"), 'ordinary Differ row opens start collapsed without changing the global diff-expand preference');
    assert.ok(changedFilesSource.includes("const initialMode = openDiffMode ? 'diff' : 'edit';"), 'Differ rows start in Diff mode and fall back only after the diff payload proves unavailable');
    assert.ok(changedFilesSource.includes("viewMode: initialMode"), 'changed-file rows set the editor mode explicitly');
    assert.ok(/async function openChangedFileInDiff\([^]*?reusableFileEditorDiffPreviewItem\(path\)[^]*?const payloadRepoRefs = \(\(\) => \{[^]*?\}\)\(\);[\s\S]*?noteFileExplorerChangesSessionInteraction\(ownerSession\)[\s\S]*?fileEditorActivationSlot\(\)[\s\S]*?openFileInEditor/.test(changedFilesSource), 'opening a changed-file row commits its owner session, preserves row FROM/TO refs, targets the active editor pane, and reuses the Differ preview tab');
    assert.ok(/if \(!openDiffMode\) \{[\s\S]*?renderOpenFilePath\(path\);[\s\S]*?void refreshOpenFileDiff\(path, \{silent: true, renderOnComplete: false, \.\.\.payloadRepoRefs\}\);[\s\S]*?return;[\s\S]*?\}/.test(changedFilesSource), 'non-diffable rows prefetch diff data without the async repaint that can fight live scrolling');
    assert.ok(/diffReady && fileStateCanRenderDiffView\(path, current\)[\s\S]*setFileEditorViewMode\(path, 'diff', item\)[\s\S]*setFileEditorViewMode\(path, 'edit', item\)[\s\S]*t\('editor\.diffUnavailable'/.test(changedFilesSource), 'failed or non-renderable diff opens visibly fall back to edit mode');
    const touchedOnlyHtml = api.changesGroupsSnapshotHtmlForTest([
      {session: '1', agent: 'codex', status: 'T', repo: '/repo/app', path: 'src/merged.py', abs_path: '/repo/app/src/merged.py', mtime: 100, source: 'transcript'},
    ], {compact: true});
    assert.ok(touchedOnlyHtml.includes('git-transcript') && touchedOnlyHtml.includes('>T</span>'), 'touched-only transcript rows carry a neutral T status badge');
    assert.ok(/file-tree-git-status[^>]*title="T: touched by AI transcript"[^>]*aria-label="T: touched by AI transcript"[^>]*>T<\/span>/.test(touchedOnlyHtml), 'touched-only T badge explains itself on hover');
    // C5: agent attribution renders 0-to-N icons from item.agents (Claude before Codex), with a screen-
    // reader label when more than one appears.
    const zeroAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: [], status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
    assert.equal(zeroAgentRow.includes('changes-file-agent'), false, 'C5: a file with no transcript attribution renders zero agent icons');
    const oneAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: ['codex'], status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
    assert.ok(oneAgentRow.includes('agent-icon codex') && !oneAgentRow.includes('agent-icon claude'), 'C5: one agent renders exactly one icon');
    assert.ok(/agent-icon codex"[^>]*aria-label="modified by Codex [^"]* ago"[^>]*title="modified by Codex [^"]* ago"/.test(oneAgentRow), 'C5: Codex icon hover names who modified the file and when');
    const twoAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: ['codex', 'claude'], status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
    assert.ok(twoAgentRow.includes('agent-icon claude') && twoAgentRow.includes('agent-icon codex'), 'C5: a file touched by both agents renders both icons');
    assert.ok(twoAgentRow.indexOf('agent-icon claude') < twoAgentRow.indexOf('agent-icon codex'), 'C5: agent icons order Claude before Codex');
    assert.ok(/changes-file-agent(?![^>]*title=)[^>]*aria-label="modified by Claude [^"]* ago, modified by Codex [^"]* ago"/.test(twoAgentRow), 'C5: the multi-agent slot is labeled for screen readers without a generic native tooltip');
    assert.ok(/agent-icon claude"[^>]*title="modified by Claude [^"]* ago"/.test(twoAgentRow), 'C5: Claude icon hover names who modified the file and when');
    assert.ok(/agent-icon codex"[^>]*title="modified by Codex [^"]* ago"/.test(twoAgentRow), 'C5: Codex icon hover stays contextual in multi-agent rows');
    const legacyAgentRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 1}], {});
    assert.ok(legacyAgentRow.includes('agent-icon codex'), 'C5: legacy scalar agent payloads still render their icon');
    // C5: image rows carry size + relative path and DROP the native title (the rich hover preview replaces it);
    // non-image rows keep the full-path title.
    const imageRow = api.changesGroupsSnapshotHtmlForTest([{session: '1', agents: [], status: 'A', repo: '/repo/app', path: 'pic.png', abs_path: '/repo/app/pic.png', mtime: 1, size: 4096}], {});
    assert.ok(imageRow.includes('file-tree-row kind-file git-untracked'), 'C5: added uploaded rows use the same git-untracked class as Finder rows');
    assert.ok(/file-tree-git-status[^>]*title="A: added"[^>]*aria-label="A: added"[^>]*>A<\/span>/.test(imageRow), 'C5: added status badge explains itself on hover');
    assert.ok(imageRow.includes('data-change-size="4096"'), 'C5: image rows carry the file size for preview gating');
    assert.ok(imageRow.includes('data-change-rel="pic.png"'), 'C5: rows carry the relative path for Copy path');
    assert.equal(/title="[^"]*pic\.png"/.test(imageRow), false, 'C5: image rows drop the native title so it does not duplicate the hover preview');
    // C5 / per-render row binder + shared Finder selection/context menu for Modified-files rows.
    assert.ok(changedFilesSource.includes('function bindChangedFileRowBehaviors('), 'C5: a per-render binder hooks Modified-files rows');
    assert.ok(/function bindChangedFileRowBehaviors\([\s\S]*?bindFileImagePreview\(row, path/.test(changedFilesSource), 'C5: image rows get the Finder hover preview');
    assert.equal(changedFilesSource.includes('function selectChangedFileRow('), false, 'Differ no longer has bespoke single-row selected state');
    assert.equal(changedFilesSource.includes('function showChangedFileContextMenu('), false, 'Differ file rows no longer fork a safe-only context menu');
    assert.ok(/Single-clicks route through the shared tree controller[\s\S]{0,620}panel\.addEventListener\('click', event => \{[\s\S]*?const row = event\.target\.closest\('\.file-tree-row\[data-path\]'\)[\s\S]*?differTreeInteractionController\.handleClick\(event, panel, \{row\}\)/.test(changedFilesSource), 'Differ click selection routes through the shared tree controller');
    assert.ok(/selectFromClick\(row, id, event\)[\s\S]{0,180}updateFileTreeSelectionFromClick\(row, id \|\| differTreeRowPath\(row\), event\)/.test(changedFilesSource), 'Differ shared controller selection routes through the Finder selection parent');
    assert.ok(/data-open-change-file[\s\S]{0,2200}showFileTreeContextMenu\(fileRow,\s*path,\s*changedFileRowEntry\(fileRow\)[\s\S]*t\('contextmenu\.openInDiffer'\)[\s\S]*openChangedFileInDiff[\s\S]*\{userInitiated: true, openMode: 'diff'\}[\s\S]*t\('contextmenu\.openNewDiffEditor'\)[\s\S]*forceNewTab: true[\s\S]*openMode: 'diff'[\s\S]*t\('contextmenu\.openNewEditor'\)[\s\S]*openFileInAdditionalEditorTab/.test(changedFilesSource), 'Differ file right-click routes through the shared Finder context menu and orders Open in a Differ, Open in a new Differ, then Open in a new Editor');
    assert.equal(/Open file in editor|Open file in diff/.test(changedFilesSource), false, 'Differ file context menu no longer hardcodes the old labels');
    assert.ok(/async function showFileTreeContextMenu\([\s\S]*?const actionContext = \{fullPath, entry, selectedPaths, infos, primaryInfo: infos\[0\] \|\| null, menuState\};[\s\S]*?for \(const action of openInNewTabActions\)[\s\S]*?typeof action\.label === 'function'[\s\S]*?appendContextMenuButton\(menu, label \|\| 'Open in new tab'[\s\S]*?appendContextMenuButton\(menu, multiple \? 'Copy relative paths' : 'Copy relative path'/.test(fileExplorerSource), 'Finder/Differ file context menu lists Open actions first and resolves dynamic Open labels before Copy actions');
    assert.equal(changedFilesSource.includes('contextmenu.openDifferent'), false, 'Differ file context menu no longer uses dynamic different-editor labels');
    assert.ok(/async function deleteFileTreePath[\s\S]*fetchSessionFiles\(\{destination: 'finder', session: fileExplorerSessionFilesTargetSession\(\), silent: true, force: true\}\)/.test(changedFilesSource), 'shared delete refreshes session-files so Differ rows disappear immediately');
    assert.ok(changedFilesSource.includes('function showChangedDirectoryContextMenu('), 'C5: Modified-files folder rows have a right-click menu');
    const dirCtxStart = changedFilesSource.indexOf('function showChangedDirectoryContextMenu(');
    const dirCtxBody = changedFilesSource.slice(dirCtxStart, changedFilesSource.indexOf('\nfunction ', dirCtxStart + 1));
    for (const action of ['Copy relative path', 'Copy full path', 'Expand ']) {
      assert.ok(dirCtxBody.includes(action), `C5: the changed-directory menu offers "${action}"`);
    }
    assert.ok(dirCtxBody.includes('copyChangedPath(rel || path'), 'C5: directory Copy relative path uses the folder-relative path when present');
    assert.ok(/function openChangedDirectoryInFinder\([\s\S]*?openFileExplorerPane\(\)[\s\S]*?setFileExplorerMode\('files'\)[\s\S]*?expandFileExplorerTreesToPath\(path\)[\s\S]*?selectFileTreePath\(path\)/.test(changedFilesSource), 'C5: Modified-files folder menu switches to Finder mode and expands the directory in-place');
    assert.equal(/'Open in new tab'|'Download'|'Rename'|'Delete'|"Open in new tab"|"Download"|"Rename"|"Delete"/.test(dirCtxBody), false, 'C5: the Modified-files folder menu stays directory-only and non-destructive');
    assert.ok(/contextmenu'[\s\S]*?data-open-change-file[\s\S]*?showFileTreeContextMenu[\s\S]*?data-open-change-directory[\s\S]*?showChangedDirectoryContextMenu/.test(changedFilesSource), 'right-click dispatches file rows through the shared Finder menu and folder rows through the directory menu');
    assert.ok(changedFilesSource.includes("multiple ? 'Copy full paths' : 'Copy full path'"), 'Finder context menu uses Copy full path label');
    assert.equal(/Copy raw paths?/.test(fileExplorerSource), false, 'Finder context menu no longer exposes a duplicate raw path action');
    api.setSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1}],
      files: [
        {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
        {session: '1', agent: 'codex', status: 'A', repo: '/repo/app', path: '.uploads/20260531-028.png', abs_path: '/repo/app/.uploads/20260531-028.png', mtime: 200, added: 0, removed: 0, uploaded: true},
      ],
    });
    api.setFileExplorerModeForTest('diff');
    const uploadedCollapsedHtml = api.fileExplorerChangesPanelHtml();
    assert.equal(uploadedCollapsedHtml.includes('Uploaded files'), false, 'uploaded files do not render in a synthetic top-level group');
    assert.ok(uploadedCollapsedHtml.includes('/repo/app'), 'uploaded files stay under their repo section');
    assert.ok(uploadedCollapsedHtml.includes('data-changes-folder-toggle="/repo/app/.uploads"'), 'uploaded files render under the repo-local .uploads directory row');
    assert.ok(uploadedCollapsedHtml.includes('file-tree-row kind-dir collapsed'), 'repo-local .uploads directories are collapsed by default');
    assert.equal(uploadedCollapsedHtml.includes('20260531-028.png</span>'), false, 'default-collapsed .uploads hides uploaded leaves');
    api.setChangesFolderCollapsedForTest([]);
    const uploadedExpandedHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(uploadedExpandedHtml.includes('20260531-028.png</span>'), 'expanded .uploads folder shows uploaded rows');
    assert.ok(uploadedExpandedHtml.includes('file-tree-icon file-icon-image'), 'uploaded image rows use the shared image icon class');
    assert.equal(uploadedExpandedHtml.includes('changes-file-row'), false, 'uploaded rows no longer use the legacy row renderer with separators');
    assert.equal(/changes-file-path[^>]*>20260531-028\.png</.test(uploadedExpandedHtml), false, 'uploaded Differ rows do not repeat the basename as the secondary path line');
    api.setFileExplorerModeForTest('files');
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
      repos: [{repo: '/repo/app', count: 2, touched_count: 2, added: 2, removed: 1, behind: 0, ahead: 1}],
      files: [
        {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
      ],
    });
    assert.ok(api.fileExplorerChangesPanelHtml().includes('Differ:'), 'Finder embeds a Differ panel');
    assert.ok(/class="changes-title">Differ: &#39;1&#39;<\/span>/.test(api.fileExplorerChangesPanelHtml()), 'C7: the embedded Differ title uses the compact session title');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFileExplorerModeForTest('diff');
    api.setDiffRefsByRepoForTest('/repo/app', {from: 'abc1111', to: 'current'});
    const firstDiffCacheKey = api.sessionFilesCacheKeyForTest('1');
    api.setDiffRefsByRepoForTest('/repo/app', {from: 'def2222', to: 'current'});
    const secondDiffCacheKey = api.sessionFilesCacheKeyForTest('1');
    assert.notEqual(firstDiffCacheKey, secondDiffCacheKey, 'Differ cache key changes when repo FROM/TO refs change for the same session');
    api.setDiffRefsByRepoForTest('/repo/app', null);
    api.setFileExplorerModeForTest('files');
    {
      const stickyApi = loadYolomux('', ['1', '2']);
      stickyApi.setFileExplorerChangesSelectedSessionForTest('1');
      assert.equal(stickyApi.fileExplorerSessionFilesTargetSessionForTest(), '1', 'Finder Modified-files target starts from the committed session');
      stickyApi.noteFileExplorerChangesSessionInteractionForTest('2');
      assert.equal(stickyApi.fileExplorerSessionFilesTargetSessionForTest(), '2', 'explicit session interaction updates the Finder Modified-files target');
    }
    {
      const shareApi = loadYolomux('?shareReplay=0', ['5', '6'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-finder', mode: 'ro', session: '5', sessions: ['5', '6'], finder: {session: '5', mode: 'diff'}},
      });
      assert.equal(shareApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'read-only share Finder target starts from the host-pinned Finder session');
      assert.equal(shareApi.noteFileExplorerChangesSessionInteractionForTest('6'), false, 'read-only share local session interactions cannot retarget Finder');
      assert.equal(shareApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'read-only share local interaction leaves Finder on the host-pinned session');
      shareApi.setSessionFilesPayloadForTest({session: '6', loaded: true, files: [], repos: [], errors: []});
      assert.equal(shareApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'read-only share background session-files payloads do not retarget Finder');
      shareApi.applyShareUiStateForTest({finder: {session: '6', mode: 'diff'}});
      assert.equal(shareApi.fileExplorerSessionFilesTargetSessionForTest(), '6', 'read-only share follows the host-authored Finder session frame');

      const unpinnedShareApi = loadYolomux('?shareReplay=0', ['5', '6'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-unpinned-finder', mode: 'ro', session: '5', sessions: ['5', '6']},
      });
      unpinnedShareApi.setSessionFilesPayloadForTest({session: '6', loaded: true, files: [], repos: [], errors: []});
      assert.equal(unpinnedShareApi.fileExplorerSessionFilesTargetSessionForTest(), '', 'read-only share without a host Finder pin does not fall back to payload session or sessions[0]');
    }
    {
      const signalPathApi = loadYolomux('', ['5']);
      signalPathApi.setTranscriptInfoForTest('5', {
        selected_pane: {current_path: '/home/test/stale-transcript-path'},
      });
      signalPathApi.setTmuxSignalStateForTest({
        windows: [{
          key: '5:0',
          session: '5',
          active: true,
          panes: [{session: '5', active: true, current_path: '/home/test/live-tmux-path'}],
        }],
      });
      assert.equal(signalPathApi.activeTmuxDirectoryPath('5'), '/home/test/live-tmux-path', 'Finder sync prefers direct tmux pane_current_path over stale transcript metadata');
    }
    {
      const hoverApi = loadYolomux('', ['5', '6']);
      hoverApi.setTranscriptInfoForTest('5', {
        project: {git: {cwd: '/home/test/yolomux.dev/src', root: '/home/test/yolomux.dev'}},
        selected_pane: {current_path: '/home/test/yolomux.dev/src'},
      });
      hoverApi.setTranscriptInfoForTest('6', {
        project: {git: {cwd: '/home/test/other.dev/src', root: '/home/test/other.dev'}},
        selected_pane: {current_path: '/home/test/other.dev/src'},
      });
      hoverApi.setSessionFilesPayloadForTest({session: '5', repos: [{repo: '/home/test/yolomux.dev'}], files: []});
      hoverApi.noteFileExplorerChangesSessionInteractionForTest('5');
      hoverApi.setFileExplorerRootMode('sync', {sync: false});
      hoverApi.setAutoFocusEnabledForTest(true);
      hoverApi.selectPanelOnHover('6');
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'hover/autofocus does not commit the Finder Modified-files target');
      assert.equal(hoverApi.activeTmuxDirectoryPath(), '/home/test/yolomux.dev/src', 'hover/autofocus does not become the Finder tmux-directory source');
      assert.deepStrictEqual(canonical(hoverApi.fileExplorerSyncPlanForTest()), {
        session: '5',
        root: '/home/test/yolomux.dev',
        affectedDirs: ['/home/test/yolomux.dev'],
        expandPaths: [],
      }, 'hover/autofocus keeps Finder Sync planned from the explicit session');
      hoverApi.setFocusedTerminal('6');
      assert.equal(hoverApi.activeTmuxDirectoryPath(), '/home/test/yolomux.dev/src', 'passive xterm focus does not become the Finder tmux-directory source');
      hoverApi.selectSession('6');
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'passive selectSession does not commit the Finder Modified-files target');
      hoverApi.selectSession('6', {userInitiated: true});
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '6', 'explicit selectSession can commit the Finder Modified-files target');
      hoverApi.setFileExplorerChangesSelectedSessionForTest('5');
      hoverApi.noteFileExplorerChangesSessionInteractionForTest('6');
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '6', 'explicit session interaction can still commit the Finder Modified-files target');
      // DOIT.58 D1/D2: the same guard must hold in full Differ mode — hover + passive xterm focus keep the
      // committed Differ target (which drives the title, session-select value, cache key, and fetch request),
      // and only explicit input/selection commits it.
      hoverApi.setFileExplorerChangesSelectedSessionForTest('5');
      hoverApi.noteFileExplorerChangesSessionInteractionForTest('5');
      hoverApi.setFileExplorerModeForTest('diff');
      hoverApi.selectPanelOnHover('6');
      hoverApi.setFocusedTerminal('6');
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'D1: Differ mode hover + passive focus keep the committed Differ target on A');
      hoverApi.selectSession('6');
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '5', 'D1: passive selectSession does not retarget the Differ in diff mode');
      hoverApi.selectSession('6', {userInitiated: true});
      assert.equal(hoverApi.fileExplorerSessionFilesTargetSessionForTest(), '6', 'D1: explicit selectSession commits the Differ target in diff mode');
      hoverApi.setFileExplorerModeForTest('files');
    }
    {
      const mouseReportApi = loadYolomux('', ['1', '8002']);
      mouseReportApi.setFileExplorerModeForTest('diff');
      mouseReportApi.noteFileExplorerChangesSessionInteractionForTest('1');
      mouseReportApi.noteFileExplorerChangesSessionInteractionForTest('8002');
      const socketMessages = [];
      mouseReportApi.registerTerminalForTest('1', {focus() {}}, {
        readyState: 1,
        send(message) { socketMessages.push(JSON.parse(message)); },
      });
      assert.equal(mouseReportApi.handleTerminalDataForTest('1', '\x1b[<35;12;7M'), true, 'xterm mouse-report bytes are still forwarded to the terminal backend');
      assert.equal(socketMessages[0]?.data, '\x1b[<35;12;7M', 'xterm mouse-report bytes stay a transport concern');
      assert.equal(mouseReportApi.fileExplorerSessionFilesTargetSessionForTest(), '8002', 'xterm hover/mouse-report bytes from pane 1 do not auto-select Differ session 1');
    }
    // C15/C6: the redundant global "N files changed in '1'" summary and global comparison line are gone;
    // each repo owns its own compact comparison line instead.
    const compactFinderPanel = api.fileExplorerChangesPanelHtml();
    const compactHeadStart = compactFinderPanel.indexOf('class="changes-repo-head"');
    const compactRepoHead = compactFinderPanel.slice(compactHeadStart, compactFinderPanel.indexOf('</button>', compactHeadStart));
    assert.ok(/changes-repo-totals[\s\S]*changes-diff-add[^>]*>\+2<\/span>[\s\S]*changes-diff-remove[^>]*>-1<\/span>[\s\S]*changes-repo-count[^>]*>2<\/span>/.test(compactRepoHead), 'Finder Modified-files repo header shows the repo aggregate totals');
    assert.equal(compactFinderPanel.includes('files changed in'), false, 'C15: the compact header drops the redundant file-count/session summary');
    assert.equal(/changes-comparison-head compact[\s\S]*?Comparing/.test(compactFinderPanel), false, 'C6: Finder Modified-files has no global comparison line');
    assert.ok(/changes-repo-refs compact[\s\S]*?diff-ref-inline[\s\S]*?data-diff-ref-from[\s\S]*?data-diff-ref-to/.test(compactFinderPanel), 'C6: Finder Modified-files exposes per-repo inline FROM/TO controls');
    assert.ok(/changes-repo-refs compact[\s\S]*?data-diff-ref-repo="\/repo\/app"/.test(compactFinderPanel), 'C6: the inline comparison inputs are scoped to the repo');
    assert.ok(/changes-repo-refs compact[\s\S]*?Ahead 1 commit/.test(compactFinderPanel), 'C6: ahead/behind lives on the repo comparison row');
    assert.equal(compactFinderPanel.includes('changes-repo-popover'), false, 'C6: repo comparison details are visible, not hidden in a hover popover');
    // C15: ahead/behind is hidden when 0 (the popover shows only the non-zero ahead, not "Behind 0 commits").
    assert.equal(compactFinderPanel.includes('Behind 0 commit'), false, 'C15: 0-commit ahead/behind is not printed');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('class="changes-title"'), 'Finder modified-files header has a responsive title cell');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('diff-ref-controls compact'), 'Finder modified-files panel exposes compact diff refs');
    // C8/C13 follow-up: Finder embedded Differ now uses the same compact select control pattern as Finder's
    // own A-Z/Z-A/recent/oldest sort control, defaulting to recent.
    const finderSortPanel = api.fileExplorerChangesPanelHtml();
    assert.ok(/<select class="[^"]*file-explorer-sort-select[^"]*changes-sort-select[^"]*changes-sort-select-compact[^"]*"[^>]*data-session-files-sort/.test(finderSortPanel), 'Finder embedded Differ sort uses the shared compact select styling');
    assert.ok(/data-file-explorer-tree-dates[\s\S]*data-file-tree-expand-collapse-all="expand"[\s\S]*data-file-tree-expand-collapse-all="collapse"[\s\S]*data-session-files-refresh/.test(finderSortPanel), 'Finder embedded Differ header orders Date, Expand all, Collapse all, Reload');
    const sortLabels = {az: 'finder.sort.az', za: 'finder.sort.za', newest: 'finder.sort.newest', oldest: 'finder.sort.oldest'};
    for (const [value, key] of Object.entries(sortLabels)) {
      const label = api.t(key).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      assert.ok(new RegExp(`<option value="${value}"[^>]*>${label}</option>`).test(finderSortPanel), `Finder embedded Differ sort offers ${value}`);
    }
    assert.ok(new RegExp(`<option value="newest"[^>]* selected[^>]*>${api.t('finder.sort.newest')}</option>`).test(finderSortPanel), 'Finder embedded Differ sort defaults to recent');
    assert.equal(/changes-sort-toggle|changes-sort-seg|changes-sort-divider/.test(finderSortPanel), false, 'Finder embedded Differ no longer uses a separate segmented sort control');
    const sortItems = [
      {path: 'b.txt', repo: '/repo/app', mtime: 100},
      {path: 'a.txt', repo: '/repo/app', mtime: 200},
      {path: 'c.txt', repo: '/repo/app', mtime: 50},
    ];
    api.setSessionFilesSortModeForTest('az');
    assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'Differ A-Z sorts by path ascending');
    api.setSessionFilesSortModeForTest('za');
    assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['c.txt', 'b.txt', 'a.txt'], 'Differ Z-A sorts by path descending');
    api.setSessionFilesSortModeForTest('newest');
    assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'Differ recent sorts by newest mtime first');
    api.setSessionFilesSortModeForTest('oldest');
    assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['c.txt', 'b.txt', 'a.txt'], 'Differ oldest sorts by oldest mtime first');
    api.setSessionFilesSortModeForTest('mtime');
    assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'legacy Differ mtime value maps to New');
    api.setSessionFilesSortModeForTest('name');
    assert.deepEqual(api.sortedSessionFiles(sortItems).map(item => item.path), ['a.txt', 'b.txt', 'c.txt'], 'legacy Differ name value maps to A-Z');
    const renderedSortItems = [
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'b.txt', abs_path: '/repo/app/b.txt', mtime: 100},
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'a.txt', abs_path: '/repo/app/a.txt', mtime: 200},
      {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'c.txt', abs_path: '/repo/app/c.txt', mtime: 50},
    ];
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {},
      repos: [{repo: '/repo/app', count: 3, touched_count: 3, added: 0, removed: 0}],
      files: renderedSortItems,
    });
    const renderedDifferOrder = () => Array.from(api.fileExplorerChangesPanelHtml().matchAll(/data-open-change-file="\/repo\/app\/([^"]+)"/g)).map(match => match[1]);
    api.setSessionFilesSortModeForTest('az');
    assert.deepEqual(renderedDifferOrder(), ['a.txt', 'b.txt', 'c.txt'], 'rendered Differ tree A-Z follows the Differ sort select');
    api.setSessionFilesSortModeForTest('za');
    assert.deepEqual(renderedDifferOrder(), ['c.txt', 'b.txt', 'a.txt'], 'rendered Differ tree Z-A follows the Differ sort select');
    api.setSessionFilesSortModeForTest('newest');
    assert.deepEqual(renderedDifferOrder(), ['a.txt', 'b.txt', 'c.txt'], 'rendered Differ tree New follows the Differ sort select');
    api.setSessionFilesSortModeForTest('oldest');
    assert.deepEqual(renderedDifferOrder(), ['c.txt', 'b.txt', 'a.txt'], 'rendered Differ tree Old follows the Differ sort select');
    api.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      errors: [],
      refs_by_repo: {'/repo/app': [{ref: 'abc123def456', short: 'abc123d', subject: 'older base commit'}]},
      repos: [{repo: '/repo/app', count: 2, touched_count: 2, added: 2, removed: 1, behind: 0, ahead: 1}],
      files: [
        {session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1},
      ],
    });
    api.setSessionFilesSortModeForTest('newest');
    assert.ok(/class="[^"]*file-explorer-date-toggle[^"]*changes-date-toggle[^"]*active[^"]*"[^>]*data-file-explorer-tree-dates[^>]*>Date<\/button>/.test(finderSortPanel), 'Finder embedded Differ header exposes the active-colored shared date-mode button');
    assert.ok(/class="changes-refresh"[^>]*>Reload<\/button>/.test(finderSortPanel), 'C13: the Reload button reads "Reload" (via i18n, not hardcoded)');
    assert.ok(/<input(?=[^>]*data-diff-ref-from)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(api.fileExplorerChangesPanelHtml()), 'Finder compact modified-files header exposes the FROM text picker with the compact popup');
    assert.ok(/<input(?=[^>]*data-diff-ref-to)(?=[^>]*aria-haspopup="listbox")[^>]*>/.test(api.fileExplorerChangesPanelHtml()), 'Finder compact modified-files header exposes the TO text picker with the compact popup');
    assert.equal(/<datalist/.test(api.fileExplorerChangesPanelHtml()), false, 'Finder compact diff refs do not render native datalists');
    assert.equal(api.fileExplorerChangesPanelHtml().includes('data-session-files-display-toggle'), false, '#41: the modified-files density toggle is removed');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('file-tree-row kind-file compact'), '#41: the Finder modified-files panel is always compact');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('data-file-explorer-changes-close'), '#44: the Modified-files header has a close (X) button to hide the section');
    assert.equal(api.fileExplorerChangesPanelHtml().includes('>Compact</button>'), false, 'Finder density toggle is an icon, not paired text buttons');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-add">+2</span>'), 'Finder modified-files panel shows green added counts');
    assert.ok(api.fileExplorerChangesPanelHtml().includes('changes-diff-remove">-1</span>'), 'Finder modified-files panel shows red removed counts');
    // C9: the per-session detail bar shows a repo carousel when the session touches more than one repo,
    // starts on the backend-provided first repo, and no control appears for a single-repo session.
    const multiRepoInfo = {
      agents: [], selected_pane: {current_path: '/repo/app'},
      project: {
        git: {root: '/repo/app', branch: 'main', dirty_count: 0}, pull_request: null, linear: [],
        repos: [
          {root: '/repo/lib', cwd: '/repo/lib', branch: 'feature', dirty_count: 2, ahead: 1, primary: true, activity_ts: 200},
          {root: '/repo/app', cwd: '/repo/app', branch: 'main', dirty_count: 0, primary: false, activity_ts: 100},
        ],
      },
    };
    const multiMetaHtml = api.projectMetaHtml('repo-cycle', multiRepoInfo);
    assert.ok(/data-repo-cycle="repo-cycle"/.test(multiMetaHtml), 'C9: a multi-repo session shows repo cycle arrows');
    assert.ok(/data-repo-chip="repo-cycle"/.test(multiMetaHtml), 'C9: the repo count still opens the repo menu');
    assert.ok(multiMetaHtml.includes('/repo/lib'), 'C9: the first displayed repo is the first backend-ordered repo');
    assert.ok(multiMetaHtml.includes('>1/2</button>'), 'C9: the repo control shows only the current repo position');
    assert.equal(multiMetaHtml.includes('1/2 repos'), false, 'C9: the repo control omits the repo label');
    assert.ok(multiMetaHtml.indexOf('data-repo-cycle="repo-cycle"') < multiMetaHtml.indexOf('/repo/lib'), 'C9: the repo carousel is the leftmost metadata control before path/description text');
    api.cycleSessionRepoDisplayForTest('repo-cycle', multiRepoInfo, 1);
    const cycledMetaHtml = api.projectMetaHtml('repo-cycle', multiRepoInfo);
    assert.ok(cycledMetaHtml.includes('/repo/app'), 'C9: the next arrow cycles the informational row to the next repo');
    assert.ok(cycledMetaHtml.includes('>2/2</button>'), 'C9: the repo control updates the current repo position');
    const secondaryPrInfo = {
      selected_pane: {current_path: '/home/test/dynamo/dynamo4'},
      project: {
        git: {root: '/home/test/ai-config', cwd: '/home/test/ai-config', branch: 'master', dirty_count: 1},
        pull_request: null,
        linear: [{identifier: 'CFG-1', title: 'Config repo issue', state: 'Open'}],
        repos: [
          {
            root: '/home/test/dynamo/dynamo4',
            cwd: '/home/test/dynamo/dynamo4',
            branch: 'keivenchang/DIS-2228__qwen3-coder-tool-calls-v2',
            dirty_count: 0,
            primary: true,
            other_branches: {
              branches: [
                {
                  name: 'keivenchang/DIS-2228__qwen3-coder-tool-calls-v2',
                  current: true,
                  pull_request: {number: 10853, title: 'feat: gate Qwen3-Coder tool calls'},
                },
              ],
            },
          },
          {root: '/home/test/ai-config', cwd: '/home/test/ai-config', branch: 'master', dirty_count: 1, primary: false},
        ],
      },
    };
    const secondaryPrHtml = api.paneInfoBarMetaHtml('secondary-pr', secondaryPrInfo);
    assert.ok(secondaryPrHtml.includes('#10853'), 'Info Bar shows a current-branch PR from the selected secondary repo');
    assert.ok(secondaryPrHtml.includes('feat: gate Qwen3-Coder tool calls'), 'Info Bar uses the selected secondary repo PR title');
    assert.equal(secondaryPrHtml.includes('CFG-1'), false, 'secondary repo Info Bar does not inherit primary repo Linear metadata');
    const singleRepoInfo = {...multiRepoInfo, project: {...multiRepoInfo.project, repos: [multiRepoInfo.project.repos[0]]}};
    assert.equal(api.projectMetaHtml('single-repo-cycle', singleRepoInfo).includes('meta-repo-switch'), false, 'C9: a single-repo session shows no carousel');
    const windowScopedInfo = {
      agents: [{kind: 'codex', pane_target: '5:0.0'}],
      selected_pane: {target: '5:0.0', window: '0', pane: '0', current_path: '/home/u'},
      panes: [
        {target: '5:0.0', window: '0', pane: '0', window_active: true, active: true, process_label: 'codex', command: 'codex', current_path: '/home/u'},
        {target: '5:1.0', window: '1', pane: '0', window_active: false, active: true, process_label: 'bash', command: 'bash', current_path: '/tmp/shell'},
      ],
      project: {
        git: {root: '/repo/agent', cwd: '/repo/agent/src', branch: 'agent-work', dirty_count: 8}, pull_request: null, linear: [],
        repos: [{root: '/repo/agent', cwd: '/repo/agent/src', branch: 'agent-work', dirty_count: 8, primary: true}],
      },
    };
    const codexMetaHtml = api.projectMetaHtml('window-scope', windowScopedInfo);
    const bashWindowInfo = {
      ...windowScopedInfo,
      selected_pane: windowScopedInfo.panes[1],
      panes: windowScopedInfo.panes.map(pane => ({...pane, window_active: pane.window === '1'})),
    };
    const bashMetaHtml = api.projectMetaHtml('window-scope', bashWindowInfo);
    assert.ok(codexMetaHtml.includes('/repo/agent/src') && codexMetaHtml.includes('8 dirty'), 'active agent window keeps transcript-derived repo metadata');
    assert.equal(bashMetaHtml.includes('/repo/agent'), false, 'non-agent active window outside the repo does not inherit the agent touched repo');
    assert.ok(bashMetaHtml.includes('/tmp/shell'), 'non-agent active window shows its own cwd in the Info Bar');
    const c9Src = fs.readFileSync('static/yolomux.js', 'utf8');
    const c9Css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(c9Src.includes('function showRepoChipMenu('), 'C9: the repo count opens a popover');
    assert.ok(/showRepoChipMenu\([\s\S]*?openFileExplorerAt\(root\)/.test(c9Src), 'C9: clicking a repo row scopes the Finder to that repo');
    assert.ok(multiMetaHtml.includes('class="btn-base meta-repo-cycle"'), 'CSS-2: repo arrow buttons use the shared button reset');
    assert.ok(multiMetaHtml.includes('class="btn-base meta-repo-chip"'), 'CSS-2: repo count button uses the shared button reset');
    assert.ok(/\.btn-base,[\s\S]*?\{[\s\S]*display:\s*inline-flex;[\s\S]*align-items:\s*center;[\s\S]*border:\s*0;[\s\S]*background:\s*transparent;[\s\S]*cursor:\s*pointer;[\s\S]*font:\s*inherit;/.test(c9Css), 'CSS-2: shared btn-base owns the button reset cluster');
    assert.ok(/\.control-active-hover:hover,\s*\.control-active-hover:focus-visible\s*\{[\s\S]*outline:\s*0;[\s\S]*color:\s*var\(--active-control-text\);[\s\S]*background:\s*var\(--active-control-bg\);/.test(c9Css), 'CSS-3: shared control-active-hover owns the active hover/focus recolor');
    assert.ok(/\.meta-repo-chip\s*\{[\s\S]*padding:\s*0 1px/.test(c9Css), 'C9: the repo position button has at most 2px horizontal padding');
    assert.ok(/\.meta-repo-cycle\s*\{[\s\S]*width:\s*auto/.test(c9Css), 'C9: the repo arrow buttons are content-sized, not fixed-width');
    assert.ok(/\.meta-repo-cycle\s*\{[\s\S]*padding:\s*0 1px/.test(c9Css), 'C9: the repo arrow buttons have at most 2px horizontal padding');
    // C10: Finder delete shortcut — Command-Delete on Mac, plain Delete on PC, gated to the Finder surface
    // and taking precedence over the global Mod+Delete tab-close.
    assert.ok(c9Src.includes('function handleFileExplorerDeleteShortcut('), 'C10: a Finder delete-shortcut handler exists');
    const globalShortcutBody = c9Src.slice(c9Src.indexOf('function handleGlobalShortcutKeydown(event)'), c9Src.indexOf("window.addEventListener('keydown', handleGlobalShortcutKeydown, true)"));
    assert.ok(globalShortcutBody.indexOf('handleFocusedTerminalCopyShortcut(event)') >= 0, 'terminal copy guard is part of the global shortcut path');
    assert.ok(globalShortcutBody.indexOf('handleFocusedTerminalCopyShortcut(event)') < globalShortcutBody.indexOf('handleFileExplorerDeleteShortcut(event)'), 'terminal copy guard runs before other global shortcuts');
    assert.ok(globalShortcutBody.indexOf('handleFileExplorerDeleteShortcut(event)') < globalShortcutBody.indexOf("if (mod && key === 'w')"), 'C10: Finder delete runs before the global tab-close shortcut');
    const c10Body = c9Src.slice(c9Src.indexOf('function handleFileExplorerDeleteShortcut('), c9Src.indexOf('function beginFileTreeRename('));
    assert.ok(c10Body.includes('isMacPlatform()') && c10Body.includes('event.metaKey === true'), 'C10: Mac requires Command (metaKey)');
    assert.ok(/key !== 'delete' \|\| event\.metaKey/.test(c10Body), 'C10: PC requires a plain Delete (no modifiers)');
    assert.ok(c10Body.includes('eventTargetIsFileExplorerSurface(event.target)'), 'C10: the shortcut is scoped to the Finder surface');
    assert.ok(c10Body.includes('globalShortcutTargetAllowsAppAction(event.target)'), 'C10: the shortcut is suppressed in text/rename inputs');
    assert.ok(c10Body.includes('fileExplorerSelectedPaths') && c10Body.includes('deleteFileTreePath('), 'C10: it deletes the selected paths via the existing delete flow');
    assert.ok(c10Body.includes('readOnlyMode'), 'C10: readonly is blocked through the existing delete guard');
    const changedFilesCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.file-tree-git-status\s*\{[\s\S]*width:\s*13px/.test(changedFilesCss), 'modified-file status chips use the shared skinny Finder badge');
    // #46: Modified-files rows match the Finder file tree — the row uses the file-explorer font size and
    // the filename carries no semibold/bold weight (regular, not big bold white).
    assert.equal(changedFilesCss.includes('.changes-file-row'), false, '#46: modified-file rows use shared Finder file-tree rows, not standalone changes-file-row CSS');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*--changes-indent-line:\s*rgba\(148,\s*163,\s*184,\s*0\.50\)/.test(changedFilesCss), 'Differ/Finder changes trees use a brighter dark-mode guide line');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*--changes-repo-head-bg:\s*color-mix\(in srgb,\s*var\(--panel2\) 88%,\s*var\(--text\) 12%\)/.test(changedFilesCss), 'Differ/Finder changes repo headers use a brighter dark-mode scoped background token');
    assert.ok(/body\.theme-light \.file-explorer-changes-panel,[\s\S]*--changes-indent-line:\s*var\(--tree-indent-line\);[\s\S]*--changes-repo-head-bg:\s*var\(--panel2\)/.test(changedFilesCss), 'light-mode Differ/Finder changes tree guide and repo header tokens stay unchanged');
    assert.equal(/(?:^|\n)\.changes-file-name\s*\{[^}]*font-weight/.test(changedFilesCss), false, '#46: modified-file names carry no bold/semibold weight override');
    assert.equal(changedFilesCss.includes('.changes-tree-folder'), false, 'Differ folders use the shared Finder tree renderer, not a stale changes-tree-folder CSS path');
    assert.equal(changedFilesSource.includes('function changeFileRowHtml('), false, 'Differ rows are not rendered through the legacy standalone row builder');
    assert.ok(/\.changes-repo-title\s*\{[\s\S]*color:\s*var\(--accent-gold\)[\s\S]*font-weight:\s*800/.test(changedFilesCss), 'Modified-files repo names are gold and bold');
    assert.ok(/\.changes-repo-totals\s*\{[\s\S]*margin-inline-start:\s*auto/.test(changedFilesCss), 'Modified-files repo totals are pinned to the right side of the disclosure header');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*--changes-folder-text:\s*var\(--accent-gold\)/.test(changedFilesCss), 'Finder diff inherits the same gold subdirectory token');
    assert.ok(/body\.theme-light\b[\s\S]*?--tree-indent-line:\s*rgba\(100,\s*116,\s*139,\s*0\.12\)/.test(changedFilesCss), 'light-mode tree connector lines are subdued (shared --tree-indent-line token)');
    assert.ok(/\.file-tree-git-status\.file-tree-git-status-unknown\s*\{[\s\S]*?background:\s*rgba\(226,\s*232,\s*240,\s*0\.10\)/.test(changedFilesCss), 'shared Finder/Differ unknown status chips are faint-neutral in dark mode');
    // Purple is RESERVED for MERGED PR status: the untracked/unknown change badge must NOT be purple
    // (it was #a78bfa, reading as a merged indicator next to the PR badges); the merged PR status keeps it.
    assert.ok(/\.file-tree-row\.git-untracked:not\(\.selected\) \.file-tree-git-status:not\(\[hidden\]\)\s*\{[\s\S]*background:\s*var\(--git-untracked-badge\)/.test(changedFilesCss), 'untracked badge uses the shared git status token, not the merged PR token');
    assert.ok(/--input-bg\s*:/.test(changedFilesCss), 'input background token is defined instead of relying on a dark fallback');
    assert.equal(/var\(--input-bg,\s*#[0-9a-fA-F]{3,8}\)/.test(changedFilesCss), false, 'input controls do not hide dark literals inside token fallbacks');
    assert.equal(/z-index:\s*(?:260|220)\b/.test(changedFilesCss), false, 'file editor z-index collision literals are routed through named tokens');
    assert.equal(changedFilesCss.includes('#a78bfa'), false, 'the old untracked purple is gone — no non-merged status uses purple');
    assert.ok(changedFilesCss.includes('body.theme-light .changes-comparison-head'), 'light theme explicitly restyles the Changes comparison header');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*overflow-y:\s*scroll/.test(changedFilesCss), 'Finder modified-files scrollbar stays visible');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*scrollbar-gutter:\s*stable/.test(changedFilesCss), 'Finder modified-files reserves scrollbar gutter');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*container-type:\s*inline-size/.test(changedFilesCss), 'Finder modified-files header uses pane-width container queries');
    assert.ok(changedFilesCss.includes('@container (max-width: 520px)'), 'Finder modified-files header wraps before narrow pane widths overlap');
    assert.ok(changedFilesCss.includes('grid-template-areas:'), 'Finder modified-files narrow header uses explicit row areas');
    assert.ok(changedFilesCss.includes('body.theme-light .file-explorer'), 'light theme explicitly restyles the Finder tree');
    assert.ok(changedFilesCss.includes('body.theme-light .file-explorer-changes-panel'), 'light theme explicitly restyles Finder modified-files');
    assert.ok(changedFilesCss.includes('.file-tree-row.kind-file .file-tree-name'), 'Finder filenames resolve to row text colors instead of inherited stale colors');
    assert.ok(/\.file-explorer-changes-panel \.changes-refresh::before[\s\S]*?\{\s*content:\s*"↻"/.test(changedFilesCss), 'Finder embedded Differ refresh paints a visible refresh icon');
    assert.ok(/\.file-explorer-date-reload-cluster \.changes-refresh::before\s*\{[\s\S]*content:\s*"↻"/.test(changedFilesCss), 'Finder date/reload cluster refresh paints the same visible refresh icon');
    assert.ok(/body\.theme-light \.file-explorer-changes-panel \.changes-refresh[\s\S]*?\{\s*background:\s*transparent/.test(changedFilesCss), 'light-mode embedded Differ refresh is not a blank white square');
    assert.ok(changedFilesCss.includes('--file-hover-bg: #fff2a8'), 'light-mode Finder/Differ row hover uses a yellow highlighter fill');
    assert.ok(/\.file-tree-row:not\(\.selected\):hover\s*\{[\s\S]*background:\s*var\(--file-hover-bg\)[\s\S]*box-shadow:\s*inset 4px 0 0 var\(--file-hover-border\)/.test(changedFilesCss), 'Finder/Differ hover rows use the shared yellow highlighter tokens without overriding selected rows');
    assert.ok(/\.file-tree-row\.current-file:not\(\.selected\)\s*\{[\s\S]*color:\s*var\(--file-selection-text\)[\s\S]*background:\s*var\(--file-selection-bg\)[\s\S]*box-shadow:\s*inset 4px 0 0 var\(--file-selection-border\)/.test(changedFilesCss), 'Finder Sync current file reuses the selected-row color tokens');
    assert.ok(c9Src.includes('function scheduleFileExplorerActiveFileReveal('), 'Finder active-file reveal uses a shared helper');
    assert.ok(/function showFileEditorPaneForPath\([\s\S]*?scheduleFileExplorerActiveFileReveal\(path\)/.test(c9Src), 'opening an editor file reveals it in the current Finder root');
    assert.ok(/const previousActiveFile = activeFile;[\s\S]*?if \(previousActiveFile !== path\) scheduleFileExplorerActiveFileReveal\(path\)/.test(c9Src), 'switching editor tabs reveals the newly active file without re-expanding on every render');
    assert.ok(changedFilesCss.includes('flex-wrap: wrap;'), 'Finder toolbar wraps instead of clipping quick-access controls');
    assert.ok(/\.file-explorer-path-row \.file-explorer-path-inline\s*\{[\s\S]*flex:\s*1 1 0[\s\S]*min-width:\s*0[\s\S]*min-inline-size:\s*0/.test(changedFilesCss), 'Finder path row lets the absolute path shrink without wrapping Sync or Copy');
    const fakeChangesScroll = {scrollTop: 45, scrollLeft: 3, innerHTML: ''};
    api.replaceHtmlPreservingScroll(fakeChangesScroll, '<div>updated</div>');
    assert.equal(fakeChangesScroll.innerHTML, '<div>updated</div>');
    assert.equal(fakeChangesScroll.scrollTop, 45, 'modified-files refresh preserves vertical scroll');
    assert.equal(fakeChangesScroll.scrollLeft, 3, 'modified-files refresh preserves horizontal scroll');
    // #149/#150: the edit view no longer auto-loads the diff or paints inline diff decorations.
    // Changes are shown ONLY in the explicit diff VIEW (the MergeView). The inline-decoration helpers and
    // the edit-mode auto-load are removed; parseUnifiedDiffLineClasses/codeMirrorDiffLineExtension are gone.
    const editSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal(/function parseUnifiedDiffLineClasses/.test(editSrc), false, '#150: parseUnifiedDiffLineClasses is deleted (no inline diff decorations)');
    assert.equal(/function codeMirrorDiffLineExtension/.test(editSrc), false, '#150: codeMirrorDiffLineExtension is deleted');
    assert.equal(/state\.diffLineClasses/.test(editSrc), false, '#150: the dead state.diffLineClasses is removed');
    const renderPanelBody = editSrc.slice(editSrc.indexOf('function renderFileEditorPanel'), editSrc.indexOf('function renderFileEditorPanel') + 4000);
    assert.equal(/!state\.diffLoaded && !state\.diffLoading && !state\.diffUnavailable/.test(renderPanelBody), false, '#149: renderFileEditorPanel no longer auto-loads the diff on open/render');
    const filesTab = api.fileExplorerPaneTabHtml();
    assert.equal(api.fileExplorerLabel(), 'File Explorer');
    assert.ok(filesTab.includes('File Explorer'));
    const appSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(appSource.includes("const editorViewModes = new Set(['edit', 'preview', 'split', 'diff'])"), 'file editor registers diff as a real view mode');
    assert.ok(appSource.includes('new api.MergeView'), 'wide diff mode uses CodeMirror MergeView');
    assert.ok(appSource.includes('api.unifiedMergeView'), 'narrow diff mode uses CodeMirror unified merge view');
    assert.ok(appSource.includes('function reconfigureCodeMirrorPanelEditorOptions'), 'word wrap and line-number toggles live-reconfigure CodeMirror instead of rebuilding');
    assert.ok(appSource.includes('function codeMirrorLineWrappingExtension'), 'word wrap has an explicit CodeMirror line-wrapping helper');
    assert.ok(appSource.includes("api.EditorView?.contentAttributes?.of?.({class: 'cm-lineWrapping'})"), 'word wrap falls back to CodeMirror contentAttributes when EditorView.lineWrapping is not exported');
    assert.ok(appSource.includes("&& (api?.EditorView?.lineWrapping || api?.EditorView?.contentAttributes?.of)"), 'CodeMirror API validation requires a usable wrapping extension path');
    assert.ok(/function codeMirrorWrapMarkerExtension\(api\)[\s\S]{0,140}const scheme = activeEditorScheme\(\)/.test(appSource), 'wrap marker plugin defines its active editor scheme before using scheme.dark');
    assert.equal(api.codeMirrorWrapMarkerRowsForBlock({type: 0, height: 60}, 20), 3, 'wrap marker counts visual rows for real text blocks');
    assert.equal(api.codeMirrorWrapMarkerRowsForBlock({type: 1, height: 60}, 20), 1, 'wrap marker skips widget-before blocks such as deleted diff chunks');
    assert.equal(api.codeMirrorWrapMarkerRowsForBlock({type: 3, height: 60}, 20), 1, 'wrap marker skips widget-range blocks such as replacement/deleted diff chunks');
    assert.equal(api.codeMirrorWrapMarkerRowsForBlock({height: 60, widget: {}}, 20), 1, 'wrap marker skips widget-backed blocks even when type metadata is missing');
    const wrapApplyStart = appSource.indexOf('function applyEditorWrapPreference');
    const wrapApplyBody = appSource.slice(wrapApplyStart, appSource.indexOf('function setEditorWrapEnabled', wrapApplyStart));
    assert.ok(wrapApplyBody.includes('codeMirrorCurrentText(panel)'), 'wrap toggles capture the live CodeMirror document before any fallback render');
    assert.ok(wrapApplyBody.indexOf('reconfigureCodeMirrorPanelEditorOptions(panel)') > 0, 'wrap toggles try the live CodeMirror reconfigure path');
    assert.ok(wrapApplyBody.indexOf('reconfigureCodeMirrorPanelEditorOptions(panel)') < wrapApplyBody.indexOf('renderFileEditorPanel(panel'), 'wrap toggles reconfigure before falling back to a full render');
    assert.equal(appSource.includes('allowInlineDiffs: true'), false, 'unified diff uses native deleted chunks so removed lines are not editable doc lines');
    // #17: deleted rows carry NO line number and stay read-only — done structurally, not via a CSS
    // transparent-text hack. The unified diff edits the MODIFIED document and overlays the original
    // through unifiedMergeView, so deleted lines are merge-decoration widgets (read-only, unnumbered),
    // never real numbered document lines. (No real CodeMirror in this Node harness, so this guards the
    // construction that produces the rendered behaviour, which was verified visually.)
    assert.ok(appSource.includes('const unifiedMergeOptions = {') && appSource.includes('original,'), 'unified diff options carry the original document for deleted-line widgets');
    assert.ok(appSource.includes('doc: currentText,'), 'unified diff edits the modified/current document');
    assert.ok(appSource.includes('api.unifiedMergeView(unifiedMergeOptions)'), 'unified diff overlays the original via unifiedMergeView, so deleted rows are unnumbered read-only widgets');
    const diffGutterCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.equal(/\.cm-deletedLineGutter\s*\{[^}]*color:\s*transparent/.test(diffGutterCss), false, '#17: deleted-line numbers are suppressed natively by unifiedMergeView, not by a transparent-text gutter hack');
    // C6: editor diffs carry the FILE'S REPO refs (per-repo), not a single global pair.
    // When explicit fromRef/toRef are provided (e.g. from the Modified-files panel click), they take precedence;
    // otherwise falls back to diffRefQueryString(fileRepoForPath(path)) so per-repo selections still apply.
    assert.ok(appSource.includes('diffRefQueryString(fileRepoForPath(path))'), 'editor diff requests fall back to per-repo FROM/TO refs');
    assert.ok(appSource.includes("const explicitFromRef = options.fromRef || ''") && appSource.includes("const explicitToRef = options.toRef || ''"), 'editor diff accepts explicit FROM/TO override from Modified-files panel click');
    assert.ok(appSource.includes('state.diffPinnedFromRef = explicitFromRef ||') && appSource.includes('state.diffPinnedToRef = explicitToRef ||'), 'explicit Modified-files refs are pinned on the open file state');
    assert.ok(appSource.includes('state.diffPinnedFromRef || state.diffPinnedToRef'), 'later diff refreshes reuse explicit file-level refs instead of falling back to HEAD/current');
    assert.ok(appSource.includes("state.diffPinnedFromRef = '';") && appSource.includes("state.diffPinnedToRef = '';"), 'repo-level FROM/TO changes clear any file-level explicit ref pins');
    assert.ok(appSource.includes("const diffTargetIsCurrent = !state.diffToRef || state.diffToRef === 'current';"), 'diff editor editability follows TO=current after the FROM/TO flip');
    assert.ok(appSource.includes('const diffEditsAllowed = diffTargetIsCurrent;'), 'diff editor allows edits on the new/current side');
    assert.ok(/function destroyCodeMirrorPanel[\s\S]*\.cm-diff-overview'\)\?\.remove\(\)/.test(appSource), '#26: tearing down the CodeMirror panel removes the diff scrollbar overview so its red/green rail does not linger in edit/normal mode');
    // the right overview is one linear-gradient derived from CodeMirror's rendered diff-row sequence.
    assert.ok(/function buildDiffOverviewGradientFromBands[\s\S]*linear-gradient\(to bottom/.test(appSource), 'diff overview builds one linear-gradient from non-overlapping row bands');
    assert.ok(/function diffOverviewCodeMirrorChunks[\s\S]*view\?\.state\?\.values[\s\S]*fromA[\s\S]*fromB/.test(appSource), 'diff overview reads CodeMirror merge chunks from the live EditorView state');
    assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*const chunks = diffOverviewCodeMirrorChunks\(view, panel\)[\s\S]*diffOverviewRowsFromCodeMirrorRenderedWeights\(view, chunks, currentText, original, container\)[\s\S]*\|\| diffOverviewRowsFromCodeMirrorChunks\(chunks, currentText, original\)/.test(appSource), 'diff overview derives the right rail from CodeMirror chunk rows, with text visual-row weights for wrap');
    assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*if \(!chunkRows && view\) \{[\s\S]*scheduleDiffOverviewReadinessRebuild\(panel\);[\s\S]*return;/.test(appSource), '#50: live CodeMirror diff views wait for chunks instead of drawing the raw-diff fallback rail');
    assert.ok(/function diffOverviewScrollLooksCurrentOnly[\s\S]*scrollTarget\.scrollHeight[\s\S]*diffOverviewLineHeight[\s\S]*return scrollRows < threshold/.test(appSource), '#55/#56/#57: diff overview detects CodeMirror current-side-only scroll geometry before deleted widgets are mounted');
    assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*diffOverviewScrollLooksCurrentOnly\(chunkRows, scrollTarget, view, container\)[\s\S]*scheduleDiffOverviewSettledRebuild\(panel\)[\s\S]*return;/.test(appSource), '#55/#56/#57: pre-snap current-side-only editors defer the rail until CodeMirror mounts deleted rows');
    assert.equal(appSource.includes('diffOverviewRowsFromCurrentSideOnly'), false, '#55/#56/#57: current-side-only pre-snap state does not draw a temporary green rail');
    assert.equal(appSource.includes('diffOverviewRowsFromCodeMirrorGeometry'), false, 'diff overview does not infer red bands from lineBlockAt pixel gaps');
    assert.ok(/function updateCodeMirrorDiffOverviewGeometry[\s\S]*scrollTarget\.clientHeight[\s\S]*overview\.style\.height/.test(appSource), 'diff overview rail is dynamically sized to the native vertical scrollbar track, excluding the horizontal scrollbar area');
    assert.ok(/function updateCodeMirrorDiffOverview[\s\S]*overview\.style\.background = gradient/.test(appSource), 'diff overview writes the gradient onto the rail itself');
    assert.equal(appSource.includes("cm-diff-overview-tick"), false, 'diff overview no longer creates per-chunk tick DOM');
    assert.equal(appSource.includes('diffOverviewRenderedRows'), false, 'diff overview no longer samples rendered DOM rows for color ticks');
    // ...and the neutral viewport indicator still REBUILDS when a fold expands/collapses (geometry/height change).
    assert.ok(/function codeMirrorDiffOverviewListener[\s\S]*?update\.geometryChanged \|\| update\.heightChanged[\s\S]*?scheduleDiffOverviewRebuild/.test(appSource), 'B3: a CM updateListener rebuilds the overview on geometry/fold change');
    assert.ok(/panel\._diffOverviewCtx = \{container, state, currentText, original\}/.test(appSource), 'B3: the overview build stores its context so the fold-rebuild can recompute from live geometry');
    const parseOverviewStops = gradient => {
      const stops = [];
      const pattern = /(#[0-9a-f]{6}|transparent)\s+([0-9.]+)%\s+([0-9.]+)%/gi;
      let match = pattern.exec(String(gradient || ''));
      while (match) {
        stops.push({
          color: match[1].toLowerCase(),
          startText: match[2],
          endText: match[3],
          start: Number.parseFloat(match[2]),
          end: Number.parseFloat(match[3]),
        });
        match = pattern.exec(String(gradient || ''));
      }
      return stops;
    };
    const changedOverviewStops = gradient => parseOverviewStops(gradient).filter(stop => stop.color !== 'transparent');
    const assertNoOverviewStopOverlap = stops => {
      for (let index = 1; index < stops.length; index += 1) {
        assert.equal(stops[index - 1].end <= stops[index].start, true, 'adjacent diff overview stops never overlap');
      }
    };
    const positionedDiff = [
      'diff --git a/a.txt b/a.txt',
      '--- a/a.txt',
      '+++ b/a.txt',
      '@@ -11 +11 @@',
      '-old display row ten',
      '+new display row eleven',
    ].join('\n');
    const positionedGradient = api.buildDiffOverviewGradientForTest(positionedDiff, 100);
    assert.ok(positionedGradient.startsWith('linear-gradient(to bottom,'), 'diff overview gradient has the expected CSS function shape');
    const positionedStops = changedOverviewStops(positionedGradient);
    assert.deepEqual(positionedStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
      {color: UI_PINS.diffOverviewDelete, start: '10.000', end: '11.000'},
      {color: UI_PINS.diffOverviewAdd, start: '11.000', end: '12.000'},
    ], 'diff overview maps consecutive removed/added rows to adjacent one-line gradient slices');
    assert.equal(positionedStops[0].endText, positionedStops[1].startText, 'consecutive red/green rows share one exact boundary');
    assertNoOverviewStopOverlap(positionedStops);
    assert.deepEqual([...new Set(positionedStops.map(stop => stop.color))].sort(), [UI_PINS.diffOverviewAdd, UI_PINS.diffOverviewDelete], 'diff overview uses only the red and green row colors for changed stops');
    assert.equal(/rgba|var\(|\bred\b|\bgreen\b/.test(positionedGradient), false, 'diff overview gradient uses literal row colors only');
    assert.equal(api.buildDiffOverviewGradientForTest('', 100), null, 'empty diffs do not draw an overview gradient');
    const makeLines = (count, label) => Array.from({length: count}, (_, index) => `${label} ${index + 1}`).join('\n');
    const largePrefix = `${makeLines(21, 'same')}\n`;
    const largeRemoved = makeLines(540, 'removed');
    const largeAdded = makeLines(199, 'added');
    const largeTail = makeLines(50, 'tail');
    const largeOriginal = `${largePrefix}${largeRemoved}\n${largeTail}`;
    const largeCurrent = `${largePrefix}${largeAdded}\n${largeTail}`;
    const largeChunk = {
      fromA: largePrefix.length,
      toA: largePrefix.length + largeRemoved.length,
      fromB: largePrefix.length,
      toB: largePrefix.length + largeAdded.length,
    };
    const largeRows = api.diffOverviewRowsFromCodeMirrorChunksForTest([largeChunk], largeCurrent, largeOriginal);
    assert.deepEqual(largeRows.bands, [
      {kind: 'remove', start: 21, end: 561},
      {kind: 'add', start: 561, end: 760},
    ], 'large CodeMirror replacement chunks render as normal prefix, red deleted widget rows, then green changed current rows');
    assert.equal(largeRows.currentLineCount, 270, 'large replacement current side includes all current rows');
    assert.equal(largeRows.deletedRows, 540, 'large replacement deleted CodeMirror widget rows are counted in the denominator');
    assert.equal(largeRows.totalRows, 810, 'large replacement overview denominator is current rows plus CodeMirror deleted widget rows');
    const currentOnlyView = {
      defaultLineHeight: 20,
      state: {doc: {lines: 270}},
      contentDOM: {classList: {contains: () => false}},
    };
    assert.equal(api.diffOverviewScrollLooksCurrentOnlyForTest(largeRows, {scrollHeight: 270 * 20}, currentOnlyView, null), true, '#55/#56/#57 live scroller height near current rows selects the pre-snap model');
    assert.equal(api.diffOverviewScrollLooksCurrentOnlyForTest(largeRows, {scrollHeight: 810 * 20}, currentOnlyView, null), false, '#55/#56/#57 full diff scroller height keeps the red+green model after CodeMirror mounts deleted widgets');
    const currentOnlyContainer = new TestElement('current-only-diff-container');
    const currentOnlyScroller = new TestElement('current-only-diff-scroller');
    currentOnlyScroller.className = 'cm-scroller';
    currentOnlyScroller.clientHeight = 400;
    currentOnlyScroller.scrollHeight = 270 * 20;
    currentOnlyContainer.appendChild(currentOnlyScroller);
    api.setDiffExpandUnchangedForTest(true);
    api.updateCodeMirrorDiffOverviewForTest(
      {
        _cmMode: 'diff',
        _diffOverviewWaitingForDeletedRows: true,
        _cmMergeView: {chunks: [largeChunk]},
        _cmView: {
          scrollDOM: currentOnlyScroller,
          defaultLineHeight: 20,
          state: {doc: {lines: 270}, values: [[largeChunk]]},
          contentDOM: {classList: {contains: () => false}},
        },
      },
      currentOnlyContainer,
      {diff: ''},
      largeCurrent,
      largeOriginal,
    );
    assert.equal(currentOnlyContainer.querySelector('.cm-diff-overview'), null, '#55/#56/#57 current-side-only CodeMirror geometry draws no temporary rail before the deleted widgets settle');
    const largeStops = changedOverviewStops(api.buildDiffOverviewGradientFromBandsForTest(largeRows.bands, largeRows.totalRows));
    assert.deepEqual(largeStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
      {color: UI_PINS.diffOverviewDelete, start: '2.593', end: '69.259'},
      {color: UI_PINS.diffOverviewAdd, start: '69.259', end: '93.827'},
    ], 'large replacement gradient allocates the green band CodeMirror paints, not only literal raw additions');
    assert.equal(largeStops[0].endText, largeStops[1].startText, 'large replacement red and green bands share one boundary and never overlap');
    assertNoOverviewStopOverlap(largeStops);
    const makeOverviewFixtureLines = (count, label) => Array.from({length: count}, (_, index) => `${label} ${String(index + 1).padStart(3, '0')}`);
    // Screenshot #48 repro shape from:
    //   git diff 521bbfd 05f22a8 -- static_src/js/yolomux/99_terminal_boot.js
    // The hunk starts at line 164, removes 30 rows, then adds 80 current-side rows.
    const screenshotPrefix = makeOverviewFixtureLines(163, 'same');
    const screenshotRemoved = makeOverviewFixtureLines(30, 'removed');
    const screenshotAdded = makeOverviewFixtureLines(80, 'added');
    const screenshotTail = makeOverviewFixtureLines(40, 'tail');
    const screenshotOriginal = [...screenshotPrefix, ...screenshotRemoved, ...screenshotTail].join('\n');
    const screenshotCurrent = [...screenshotPrefix, ...screenshotAdded, ...screenshotTail].join('\n');
    const lineStartsForTest = text => {
      const starts = [0];
      for (let index = 0; index < text.length; index += 1) {
        if (text.charCodeAt(index) === 10) starts.push(index + 1);
      }
      return starts;
    };
    const offsetForLineForTest = (starts, lineNumber) => starts[Math.max(0, lineNumber - 1)] ?? starts[starts.length - 1] ?? 0;
    const screenshotOriginalStarts = lineStartsForTest(screenshotOriginal);
    const screenshotCurrentStarts = lineStartsForTest(screenshotCurrent);
    const screenshotChunk = {
      fromA: offsetForLineForTest(screenshotOriginalStarts, 164),
      toA: offsetForLineForTest(screenshotOriginalStarts, 194),
      fromB: offsetForLineForTest(screenshotCurrentStarts, 164),
      toB: offsetForLineForTest(screenshotCurrentStarts, 244),
    };
    const screenshotRows = api.diffOverviewRowsFromCodeMirrorChunksForTest([screenshotChunk], screenshotCurrent, screenshotOriginal);
    const expectedScreenshotStops = changedOverviewStops(api.buildDiffOverviewGradientFromBandsForTest(screenshotRows.bands, screenshotRows.totalRows));
    assert.deepEqual(screenshotRows.bands, [
      {kind: 'remove', start: 163, end: 193},
      {kind: 'add', start: 193, end: 273},
    ], '#48 exact 99_terminal_boot.js 521bbfd..05f22a8 renders red deleted rows followed immediately by green current rows');
    assert.equal(expectedScreenshotStops[0].endText, expectedScreenshotStops[1].startText, '#48 red/green bands share one exact boundary');
    const screenshotContainer = new TestElement('screenshot-diff-container');
    const screenshotScroller = new TestElement('screenshot-diff-scroller');
    screenshotScroller.className = 'cm-scroller';
    screenshotScroller.clientHeight = 400;
    screenshotScroller.scrollHeight = 20000;
    screenshotContainer.appendChild(screenshotScroller);
    const screenshotDoc = {
      length: screenshotCurrent.length,
      lines: screenshotCurrentStarts.length,
      line(number) {
        return {from: offsetForLineForTest(screenshotCurrentStarts, number)};
      },
    };
    const screenshotPanel = {
      _cmMode: 'diff',
      _cmMergeView: {chunks: [screenshotChunk]},
      _cmView: {
        scrollDOM: screenshotScroller,
        state: {doc: screenshotDoc, values: [[screenshotChunk]]},
        contentHeight: 20000,
        lineBlockAt(pos) {
          const line = screenshotCurrentStarts.findIndex((start, index) => start <= pos && (screenshotCurrentStarts[index + 1] ?? Infinity) > pos) + 1;
          const lineNumber = Math.max(1, line);
          if (lineNumber < 164) return {top: (lineNumber - 1) * 20, bottom: lineNumber * 20};
          if (lineNumber <= 243) {
            const inflatedTop = 10000 + (lineNumber - 164) * 20;
            return {top: inflatedTop, bottom: inflatedTop + 20};
          }
          const tailTop = 11600 + (lineNumber - 244) * 20;
          return {top: tailTop, bottom: tailTop + 20};
        },
      },
    };
    api.setDiffExpandUnchangedForTest(true);
    api.updateCodeMirrorDiffOverviewForTest(screenshotPanel, screenshotContainer, {diff: ''}, screenshotCurrent, screenshotOriginal);
    const screenshotOverview = screenshotContainer.querySelector('.cm-diff-overview');
    const screenshotStops = changedOverviewStops(screenshotOverview?.style?.background || '');
    assert.deepEqual(
      screenshotStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})),
      expectedScreenshotStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})),
      '#48 production overview ignores misleading lineBlockAt gaps and matches the CodeMirror row stream exactly',
    );
    assertNoOverviewStopOverlap(screenshotStops);
    const wrappedCurrent = 'same\nwrapped current line\nnext';
    const wrappedOriginal = 'same\nold line\nnext';
    const wrappedCurrentStarts = lineStartsForTest(wrappedCurrent);
    const wrappedOriginalStarts = lineStartsForTest(wrappedOriginal);
    const wrappedChunk = {
      fromA: offsetForLineForTest(wrappedOriginalStarts, 2),
      toA: offsetForLineForTest(wrappedOriginalStarts, 3),
      fromB: offsetForLineForTest(wrappedCurrentStarts, 2),
      toB: offsetForLineForTest(wrappedCurrentStarts, 3),
    };
    const wrappedDoc = {
      length: wrappedCurrent.length,
      lines: wrappedCurrentStarts.length,
      line(number) {
        return {from: offsetForLineForTest(wrappedCurrentStarts, number)};
      },
    };
    const wrappedView = {
      defaultCharacterWidth: 8,
      defaultLineHeight: 20,
      contentDOM: {
        clientWidth: 64,
        classList: {contains: name => name === 'cm-lineWrapping'},
        getBoundingClientRect: () => ({width: 64}),
      },
      state: {doc: wrappedDoc},
      lineBlockAt(pos) {
        if (pos >= offsetForLineForTest(wrappedCurrentStarts, 3)) return {top: 80, bottom: 100};
        if (pos >= offsetForLineForTest(wrappedCurrentStarts, 2)) return {top: 20, bottom: 80};
        return {top: 0, bottom: 20};
      },
    };
    const wrappedRows = api.diffOverviewRowsFromCodeMirrorRenderedWeightsForTest(
      wrappedView,
      [wrappedChunk],
      wrappedCurrent,
      wrappedOriginal,
      null,
    );
    assert.deepEqual(wrappedRows.bands, [
      {kind: 'remove', start: 1, end: 2},
      {kind: 'add', start: 2, end: 5},
    ], '#49 wrap weights the green current line by its text visual-row estimate while keeping deleted rows explicit');
    assert.equal(wrappedRows.totalRows, 6, '#49 wrap denominator uses visual row weights: normal + red + wrapped green + normal');
    const deletedWrapCurrent = 'ok\nnew\nend';
    const deletedWrapOriginal = 'ok\nold deleted line\nend';
    const deletedWrapCurrentStarts = lineStartsForTest(deletedWrapCurrent);
    const deletedWrapOriginalStarts = lineStartsForTest(deletedWrapOriginal);
    const deletedWrapChunk = {
      fromA: offsetForLineForTest(deletedWrapOriginalStarts, 2),
      toA: offsetForLineForTest(deletedWrapOriginalStarts, 3),
      fromB: offsetForLineForTest(deletedWrapCurrentStarts, 2),
      toB: offsetForLineForTest(deletedWrapCurrentStarts, 3),
    };
    const deletedWrapDoc = {
      length: deletedWrapCurrent.length,
      lines: deletedWrapCurrentStarts.length,
      line(number) {
        return {from: offsetForLineForTest(deletedWrapCurrentStarts, number)};
      },
    };
    const deletedWrapView = {
      defaultCharacterWidth: 8,
      defaultLineHeight: 20,
      contentDOM: {
        clientWidth: 24,
        classList: {contains: name => name === 'cm-lineWrapping'},
        getBoundingClientRect: () => ({width: 24}),
      },
      state: {doc: deletedWrapDoc},
      lineBlockAt(pos) {
        if (pos >= offsetForLineForTest(deletedWrapCurrentStarts, 3)) return {top: 40, bottom: 60};
        if (pos >= offsetForLineForTest(deletedWrapCurrentStarts, 2)) return {top: 20, bottom: 40};
        return {top: 0, bottom: 20};
      },
    };
    const deletedWrapRows = api.diffOverviewRowsFromCodeMirrorRenderedWeightsForTest(
      deletedWrapView,
      [deletedWrapChunk],
      deletedWrapCurrent,
      deletedWrapOriginal,
      null,
    );
    assert.deepEqual(deletedWrapRows.bands, [
      {kind: 'remove', start: 1, end: 7},
      {kind: 'add', start: 7, end: 8},
    ], '#49 wrap estimates unmounted deleted rows from original text width instead of squeezing them to one row');
    assert.equal(deletedWrapRows.totalRows, 9, '#49 wrapped deleted rows contribute to the full rail denominator');
    assert.equal(appSource.includes("scrollTarget.addEventListener?.('scroll', rebuild"), false, '#51/#52: scrolling updates only the viewport box, not the red/green gradient');
    const notReadyContainer = new TestElement('not-ready-diff-container');
    const notReadyScroller = new TestElement('not-ready-diff-scroller');
    notReadyScroller.className = 'cm-scroller';
    notReadyContainer.appendChild(notReadyScroller);
    const notReadyPanel = {
      _cmMode: 'diff',
      _cmView: {scrollDOM: notReadyScroller, state: {doc: wrappedDoc, values: []}},
    };
    const notReadyDiff = [
      'diff --git a/a.txt b/a.txt',
      '--- a/a.txt',
      '+++ b/a.txt',
      '@@ -1,3 +1,3 @@',
      '-old one',
      '-old two',
      '+new one',
      '+new two',
      ' context',
    ].join('\n');
    api.setDiffExpandUnchangedForTest(true);
    api.updateCodeMirrorDiffOverviewForTest(
      notReadyPanel,
      notReadyContainer,
      {diff: notReadyDiff},
      'new one\nnew two\ncontext',
      'old one\nold two\ncontext',
    );
    assert.equal(notReadyContainer.querySelector('.cm-diff-overview'), null, '#50: live CodeMirror diff with missing chunks does not draw the raw fallback rail before the settled rebuild');
    const multiHunkDiff = [
      'diff --git a/a.txt b/a.txt',
      '--- a/a.txt',
      '+++ b/a.txt',
      '@@ -6 +6 @@',
      '-old near top',
      '+new near top',
      '@@ -50 +50 @@',
      '-old far down',
      '+new far down',
    ].join('\n');
    const multiHunkStops = changedOverviewStops(api.buildDiffOverviewGradientForTest(multiHunkDiff, 100));
    assert.deepEqual(multiHunkStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
      {color: UI_PINS.diffOverviewDelete, start: '5.000', end: '6.000'},
      {color: UI_PINS.diffOverviewAdd, start: '6.000', end: '7.000'},
      {color: UI_PINS.diffOverviewDelete, start: '50.000', end: '51.000'},
      {color: UI_PINS.diffOverviewAdd, start: '51.000', end: '52.000'},
    ], 'diff overview leaves real uncolored gaps between separate hunks');
    assert.equal(multiHunkStops.some(stop => stop.start > 7 && stop.start < 50), false, 'diff overview does not color the unchanged gap between hunks');
    assertNoOverviewStopOverlap(multiHunkStops);
    const replacementDiff = [
      'diff --git a/a.txt b/a.txt',
      '--- a/a.txt',
      '+++ b/a.txt',
      '@@ -1,3 +1,3 @@',
      '-old one',
      '-old two',
      '+new one',
      '+new two',
      ' context',
    ].join('\n');
    assert.equal(api.diffOverviewRemovedLineCountForTest(replacementDiff), 2, 'diff overview denominator includes deleted rows because they occupy editor rows');
    api.setDiffExpandUnchangedForTest(false);
    const overviewContainer = new TestElement('overview-container');
    api.updateCodeMirrorDiffOverviewForTest(null, overviewContainer, {diff: replacementDiff}, 'new one\nnew two\ncontext', 'old one\nold two\ncontext');
    assert.equal(overviewContainer.querySelector('.cm-diff-overview'), null, 'collapsed unchanged diff view omits the right overview colors entirely');
    api.setDiffExpandUnchangedForTest(true);
    api.updateCodeMirrorDiffOverviewForTest(null, overviewContainer, {diff: replacementDiff}, 'new one\nnew two\ncontext', 'old one\nold two\ncontext');
    const overview = overviewContainer.querySelector('.cm-diff-overview');
    assert.ok(overview, 'diff overview renders a rail for replacement hunks');
    assert.ok(String(overview.style.background || '').includes('linear-gradient'), 'diff overview paints the red/green rail as one gradient');
    assert.equal(overview.querySelectorAll('.cm-diff-overview-tick').length, 0, 'diff overview creates no per-chunk tick children');
    const overviewStops = changedOverviewStops(overview.style.background);
    assert.deepEqual(overviewStops.map(stop => ({color: stop.color, start: stop.startText, end: stop.endText})), [
      {color: UI_PINS.diffOverviewDelete, start: '0.000', end: '40.000'},
      {color: UI_PINS.diffOverviewAdd, start: '40.000', end: '80.000'},
    ], 'diff overview replacement gradient follows rendered red rows, then rendered green rows, linearly');
    assertNoOverviewStopOverlap(overviewStops);
    const viewportContainer = new TestElement('viewport-container');
    const viewportScroller = new TestElement('viewport-scroller');
    viewportScroller.className = 'cm-scroller';
    viewportScroller.scrollTop = 50;
    viewportScroller.clientHeight = 100;
    viewportScroller.scrollHeight = 500;
    viewportContainer.appendChild(viewportScroller);
    api.updateCodeMirrorDiffOverviewForTest(null, viewportContainer, {diff: replacementDiff}, 'new one\nnew two\ncontext', 'old one\nold two\ncontext');
    const viewportOverview = viewportContainer.querySelector('.cm-diff-overview');
    const viewportIndicator = viewportOverview.querySelector('.cm-diff-overview-viewport');
    assert.ok(viewportIndicator, 'diff overview includes a viewport indicator when a scroll container is available');
    assert.equal(viewportIndicator.style.top, '10%', 'diff overview viewport indicator follows scrollTop / scrollHeight');
    assert.equal(viewportIndicator.style.height, '20%', 'diff overview viewport indicator follows clientHeight / scrollHeight');
    assert.equal(appSource.includes('splitLaneIndexes'), false, 'diff overview does not use an overlapping-lane model');
    assert.equal(appSource.includes('Math.max(0.8'), false, 'diff overview does not inflate one-line ticks in percent space, which made adjacent red/green bands overlap');
    // a diff-only toolbar toggle shows ALL context (omits collapseUnchanged) vs collapsing runs.
    assert.ok(appSource.includes('file-editor-diff-expand-panel'), 'B4: the diff toolbar has an expand/collapse-all-unchanged toggle');
    assert.ok(/function toggleFileEditorDiffExpandUnchangedForItem[\s\S]*?setFileEditorDiffExpandUnchangedForItem/.test(appSource), 'B4: the panel toggle flips diff context expansion for the current editor item');
    assert.ok(/expandUnchanged \? \{\} : \{collapseUnchanged: \{margin: 3, minSize: 8\}\}/.test(appSource), 'B4: expanded omits collapseUnchanged so every unchanged line shows (both diff layouts)');
    assert.ok(/mode: 'diff'[^;]*expand: expandUnchanged/.test(appSource), 'B4: the diff config signature includes the current item expansion state so toggling rebuilds the diff view');
    const diffLayoutFn = appSource.slice(appSource.indexOf('function codeMirrorDiffLayout('), appSource.indexOf('function codeMirrorDiffLayout(') + 800);
    assert.ok(diffLayoutFn.includes("return 'inline';"), '#33: the diff always uses the unified (inline) layout');
    assert.equal(diffLayoutFn.includes("'side'"), false, '#33: the wide-pane side-by-side layout (which numbered deleted rows) is no longer selected, so deleted rows are unnumbered widgets at every width');
    assert.equal(api.codeMirrorDiffLayout({getBoundingClientRect: () => ({width: 2000})}), 'inline', '#33: even a very wide pane uses the unified (inline) diff, so deleted rows are never numbered');
    assert.equal(api.codeMirrorDiffLayout({getBoundingClientRect: () => ({width: 300})}), 'inline', '#33: a narrow pane also uses the unified diff');
    assert.equal(appSource.includes('{wrap: false}'), false, '#47: expanded diff honors the live Word Wrap setting instead of forcing wrap off');
    assert.equal(appSource.includes('view.lineBlockAt(doc.line(line).from)'), false, '#48: diff overview must not infer deleted-row color from CodeMirror pixel gaps');
    assert.ok(/if \(state\.diffLoading && state\._diffLoadingPromise\) return state\._diffLoadingPromise/.test(appSource), '#43: concurrent diff loads are deduped (callers await one in-flight load), so the panel never renders against an un-loaded original');
    assert.ok(/if \(!state\.diffLoaded && !state\.diffUnavailable\) \{[\s\S]{0,320}await refreshOpenFileDiff\(path, \{silent: true, renderOnComplete: false\}\);[\s\S]{0,160}if \(panel\._cmGeneration !== generation\) return null/.test(appSource), '#43/Q4: unresolved diffs await the deduped payload and continue in the same generation instead of flashing an edit view');
    const unresolvedDiffBranch = appSource.slice(appSource.indexOf('async function ensureCodeMirrorDiffPanel('), appSource.indexOf('if (!fileStateCanRenderDiffView(path, state))', appSource.indexOf('async function ensureCodeMirrorDiffPanel(')));
    assert.equal(unresolvedDiffBranch.includes("forceMode: 'edit'"), false, 'unresolved diff payloads must not bail to a temporary edit-mode CodeMirror view');
    assert.ok(/CodeMirror diff language parser failed; retrying plain diff editor/.test(appSource), 'diff CodeMirror build has the same parser-failure plain retry safety net as edit mode');
    const diffPanelBody = appSource.slice(appSource.indexOf('async function ensureCodeMirrorDiffPanel('), appSource.indexOf('async function ensureCodeMirrorPanel(', appSource.indexOf('async function ensureCodeMirrorDiffPanel(')));
    assert.ok(/catch \(error\) \{[\s\S]{0,360}CodeMirror diff editor unavailable; showing read-only raw text[\s\S]{0,260}container\.hidden = true;[\s\S]{0,260}return false;/.test(diffPanelBody), 'diff CodeMirror build failures fall back to raw text instead of leaving an emptied blank pane');
    assert.ok(/ensureCodeMirrorPanel\(panel, item, path, state\)\.then\(loaded => \{[\s\S]{0,160}renderFileEditorRawPane\(rawPane, path, state\.content\);[\s\S]{0,160}\}\)\.catch\(error => \{[\s\S]{0,360}renderFileEditorRawPane\(rawPane, path, state\.content\);/.test(appSource), 'CodeMirror render promise rejections are caught and fall back to raw text');
    assert.equal(appSource.includes("fileEditorEmptyState('No diff'"), false, 'clean selected refs do not render a No diff empty state');
    assert.ok(/if \(!fileStateCanRenderDiffView\(path, state\)\)[\s\S]{0,360}return ensureCodeMirrorPanel\(panel, item, path, state, \{forceMode: 'edit'\}\)/.test(appSource), 'only truly unavailable diff views fall back to the normal editable CodeMirror view');
    assert.ok(/function diffModeShouldFallBackToEdit[\s\S]*!state\.diffLoading[\s\S]*!fileStateCanRenderDiffView\(path, state\)/.test(appSource), 'once a diff load confirms no renderable diff view, diff mode exits to edit; a file WITH history stays in diff mode so the FROM/TO ref picker is reachable');
    const refreshDiffStart = appSource.indexOf('async function refreshOpenFileDiff(');
    const openFileEditorStart = appSource.indexOf('async function openFileInEditor(', refreshDiffStart);
    assert.ok(refreshDiffStart > 0 && openFileEditorStart > refreshDiffStart, '#43: refreshOpenFileDiff body is locatable');
    const refreshDiffBody = appSource.slice(refreshDiffStart, openFileEditorStart);
    const diffLoadingClearIndex = refreshDiffBody.indexOf('state.diffLoading = false;');
    const diffPanelRenderIndex = refreshDiffBody.indexOf('renderFileEditorPanel(panel, item);');
    assert.ok(diffLoadingClearIndex >= 0 && diffPanelRenderIndex > diffLoadingClearIndex, '#43: diff-load completion clears diffLoading before repainting the panel, so the expanded-context toolbar button is not left disabled');
    assert.ok(refreshDiffBody.includes("options.renderOnComplete !== false && editorViewModeFor(path, item) === 'diff'"), 'awaited diff builders can suppress the completion re-render that otherwise supersedes their generation');
    assert.equal(api.openFileDiffAvailable({kind: 'text', diffLoaded: true, untracked: true, diff: 'diff --git a/a b/a\n--- /dev/null\n+++ b/a\n@@\n+x'}), true, '#43: an untracked/all-added Differ row reports a displayable diff');
    assert.ok(/function openDraggedFilesInEditor[\s\S]*await refreshOpenFileDiff\(path[\s\S]*openFileDiffAvailable\(draggedState\)[\s\S]*setFileEditorViewMode\(path, 'diff'/.test(appSource), '#39: a dragged CHANGED file opens in the same unified diff view as double-click (routes through the shared refreshOpenFileDiff/diff path)');
    assert.ok(appSource.includes('data-file-explorer-new-folder'), 'Finder header exposes new-folder action');
    const focusPanelBody = appSource.slice(appSource.indexOf('function setFocusedPanelItem('), appSource.indexOf('let autoFocusNavTimer'));
    assert.equal(/switchFileExplorerChangesSession/.test(focusPanelBody), false, 'passive focus/hover no longer switches the Finder Modified-files session');
    assert.equal(appSource.includes('sessionFilesTargetSession({followActive: true})'), false, 'Finder Modified-files session selection never follows passive hover/autofocus');
    assert.ok(/function noteFileExplorerChangesSessionInteraction\(session\)/.test(appSource), 'explicit session interactions can commit the Finder Modified-files target');
    assert.ok(/function noteFileExplorerChangesSessionInteraction\(session\)[\s\S]*shareViewMode && !shareWriteMode && !applyingShareRemoteUiState[\s\S]*return false/.test(appSource), 'read-only share clients cannot locally commit a Finder/Differ session');
    assert.ok(/function fileExplorerSessionFilesTargetSession\(\)[\s\S]*shareViewMode && !shareWriteMode[\s\S]*fileExplorerChangesSelectedSession[\s\S]*fileExplorerExplicitSyncSession[\s\S]*return ''/.test(appSource), 'read-only share Finder/Differ target resolves only from the host-pinned session');
    const dockviewActivePanelStart = appSource.indexOf('api.onDidActivePanelChange(panel => {');
    assert.ok(dockviewActivePanelStart > 0, 'Dockview active-panel listener is locatable');
    const dockviewActivePanelBody = appSource.slice(dockviewActivePanelStart, appSource.indexOf('api.onWillShowOverlay', dockviewActivePanelStart));
    assert.ok(dockviewActivePanelBody.includes('if (dockviewLayoutState.applyingFromLayout) return;'), 'Dockview active-panel listener ignores programmatic layout application');
    assert.equal(dockviewActivePanelBody.includes('noteFileExplorerChangesSessionInteraction'), false, 'Dockview active-panel listener stays passive so hover focus does not retarget Differ');
    assert.equal(dockviewActivePanelBody.includes('userInitiated: true'), false, 'Dockview active-panel listener does not launder hover focus into a user interaction');
    assert.ok(dockviewActivePanelBody.includes('setFocusedPanelItem(item);'), 'Dockview active-panel listener still updates passive focus state');
    const dockviewTabRendererStart = appSource.indexOf('function createDockviewTabRenderer()');
    assert.ok(dockviewTabRendererStart > 0, 'Dockview tab renderer is locatable');
    const dockviewTabRendererBody = appSource.slice(dockviewTabRendererStart, appSource.indexOf('function createDockviewPanelRenderer()', dockviewTabRendererStart));
    assert.ok(/function captureDockviewPreviousPaneBeforeTabActivation\(tabElement, targetItem\)[\s\S]*activeItemForSide\(slot\)[\s\S]*capturePaneViewStateForItemIfPresent\(previous\)/.test(appSource), 'Dockview tab activation captures the previously visible pane before Dockview hides it');
    assert.ok(/element\.addEventListener\('pointerdown', event => \{[\s\S]*captureDockviewPreviousPaneBeforeTabActivation\(element, item\);[\s\S]*dockviewBeginTabPointerDrag\(event, item\)/.test(dockviewTabRendererBody), 'Dockview pointer tab activation captures pane viewport before native Dockview switching');
    assert.ok(/const commitExplicitTabInteraction = \(\) => \{[\s\S]*?if \(isTmuxSession\(item\)\) noteFileExplorerChangesSessionInteraction\(item\);[\s\S]*?setFocusedPanelItem\(item, \{userInitiated: true\}\);[\s\S]*?\};/.test(dockviewTabRendererBody), 'Dockview tab gestures commit tmux Differ context at the explicit call site');
    assert.ok(/element\.addEventListener\('click', async event => \{[\s\S]*?commitExplicitTabInteraction\(\);[\s\S]*?\}\);/.test(dockviewTabRendererBody), 'Dockview tab click commits an explicit interaction without relying on active-panel changes');
    assert.ok(/element\.addEventListener\('keydown', event => \{[\s\S]*?\['Enter', ' '\]\.includes\(event\.key\)[\s\S]*?commitExplicitTabInteraction\(\);[\s\S]*?api\?\.setActive\?\.\(\);[\s\S]*?\}\);/.test(dockviewTabRendererBody), 'Dockview tab Enter/Space activation commits an explicit interaction before setting the active panel');
    assert.ok(/function activatePaneTab\([^]*?noteFileExplorerChangesSessionInteraction\(session\)/.test(appSource), 'clicking a tmux pane tab commits the Finder Modified-files target');
    assert.ok(/function activatePaneTab\([^]*?isFileEditorItem\(session\)[^]*?changedFileOwnerSessionForPath\(path, \{owners\}\)[^]*?owners\.length === 1[^]*?noteFileExplorerChangesSessionInteraction\(owner\)/.test(appSource), 'clicking a file tab commits an exact-path Differ owner, falling back to a single owner only');
    const exactOwnerApi = loadYolomux('', ['1', '2', '5']);
    const exactPath = '/home/test/frontend-crates/tests/parity/toolcalling/table.py';
    const similarPath = '/home/test/frontend-crates/conformance/utils/tests/parity/toolcalling/table.py';
    const exactItem = exactOwnerApi.fileEditorItemFor(exactPath);
    exactOwnerApi.setFileExplorerSessionFilesPayloadForTest({session: '5', loaded: true, files: [], repos: [], errors: []});
    exactOwnerApi.setSessionFilesCachePayloadForTest('1', {loaded: true, files: [{abs_path: similarPath, status: 'M'}], repos: [], errors: []});
    exactOwnerApi.setSessionFilesCachePayloadForTest('2', {loaded: true, files: [{abs_path: exactPath, status: 'M'}], repos: [], errors: []});
    exactOwnerApi.setOpenFileOwner(exactPath, exactItem, {ownerSession: '1'});
    exactOwnerApi.setOpenFileOwner(exactPath, exactItem, {ownerSession: '2'});
    const exactOwnerSlots = exactOwnerApi.emptyLayoutSlots();
    exactOwnerSlots[exactOwnerApi.layoutTreeKey] = exactOwnerApi.leafNode('left');
    exactOwnerSlots.left = exactOwnerApi.paneStateWithTabs([exactItem, '5'], '5');
    exactOwnerApi.setLayoutSlotsForTest(exactOwnerSlots);
    exactOwnerApi.activatePaneTab('left', exactItem, {userInitiated: true});
    assert.equal(exactOwnerApi.fileExplorerSessionFilesTargetSessionForTest(), '2', 'file-tab click chooses the one session with the exact changed absolute path');
    exactOwnerApi.setSessionFilesCachePayloadForTest('1', {loaded: true, files: [{abs_path: exactPath, status: 'M'}], repos: [], errors: []});
    assert.equal(exactOwnerApi.changedFileOwnerSessionForPathForTest(exactPath, {owners: ['1', '2']}), '', 'same absolute file changed by multiple sessions remains ambiguous');
    const terminalInputStart = appSource.indexOf('function startTerminal(');
    const terminalInputEnd = appSource.indexOf('function updateTypingIndicator(', terminalInputStart);
    const terminalInputBody = appSource.slice(terminalInputStart, terminalInputEnd);
    const terminalContainerBindingStart = appSource.indexOf('function bindTerminalContainerForSession(');
    const terminalContainerBindingEnd = appSource.indexOf('function startTerminal(', terminalContainerBindingStart);
    const terminalContainerBindingBody = appSource.slice(terminalContainerBindingStart, terminalContainerBindingEnd);
    const terminalDataHandlerStart = appSource.indexOf('function handleTerminalData(');
    const terminalDataHandlerBody = appSource.slice(terminalDataHandlerStart, appSource.indexOf('function shellQuote(', terminalDataHandlerStart));
    assert.ok(terminalContainerBindingBody.includes("container.addEventListener('keydown', () => noteTerminalExplicitInput(session), {capture: true});"), 'terminal keydown commits the Finder Modified-files target');
    assert.ok(terminalContainerBindingBody.includes("container.addEventListener('paste', () => noteTerminalExplicitInput(session), {capture: true});"), 'terminal paste commits the Finder Modified-files target');
    assert.ok(terminalInputBody.includes('bindTerminalContainerForSession(session, term, container);'), 'startTerminal uses the shared terminal container binding path');
    assert.ok(terminalInputBody.includes('term.onData(data => handleTerminalData(session, data));'), 'startTerminal routes terminal bytes through the shared transport-only handler');
    assert.ok(/allowProposedApi:\s*true/.test(terminalInputBody), 'xterm opts into the unicode service needed by the Unicode11 addon');
    assert.ok(/function applyTerminalUnicode11Addon\(term\)[\s\S]*new Unicode11AddonCtor\(\)[\s\S]*term\.loadAddon\(addon\)[\s\S]*term\.unicode\.activeVersion = '11'/.test(appSource), 'xterm terminals register Unicode 11 widths for emoji cell accounting');
    assert.ok(/const term = new TerminalCtor\([\s\S]*?\}\);\s*applyTerminalUnicode11Addon\(term\);\s*term\.open\(container\);/.test(terminalInputBody), 'xterm Unicode widths are selected before the first terminal paint');
    assert.equal(/term\.onData\(data => \{[^]*?noteFileExplorerChangesSessionInteraction\(session\)/.test(terminalInputBody), false, 'xterm data transport does not commit Finder because hover focus can emit focus/mouse reports');
    assert.equal(/term\.onData\(data => \{[^]*?noteTerminalExplicitInput\(session\)/.test(terminalInputBody), false, 'xterm data transport does not commit Finder indirectly through explicit-input helpers because hover mouse reports can emit terminal bytes');
    assert.equal(terminalDataHandlerBody.includes('noteTerminalExplicitInput'), false, 'terminal byte handling stays transport-only; DOM keydown/paste/beforeinput own explicit-input commits');
    assert.ok(/fetchSessionFiles\(\{destination: 'finder', session, silent: true, force: true, background: cachedPayloadIsLoaded\}\)/.test(appSource), 'explicit session changes force a fresh Finder modified-files fetch even if an older request is in flight');
    assert.ok(/function sessionFilesCacheKey\(session\)[\s\S]*sessionFilesRequestQueryString\(\)/.test(appSource), 'Differ cached payloads are keyed by session plus effective FROM/TO/refs query');
    assert.ok(/const cached = fileExplorerSessionFilesCache\.get\(sessionFilesCacheKey\(session\)\)/.test(appSource), 'Differ session switches do not reuse payloads from a different ref pair');
    assert.ok(/function switchFileExplorerChangesSession\(session\)[\s\S]*if \(cachedPayloadIsLoaded\) \{[\s\S]*setSessionFilesPayloadForDestination\('finder', cached\.payload\)/.test(appSource), 'Finder session switches apply cached target payloads immediately before the forced refresh');
    assert.ok(/function switchFileExplorerChangesSession\(session\)[\s\S]*setSessionFilesLoadingForDestination\('finder', !cachedPayloadIsLoaded\);\s*scheduleFileExplorerActiveTabSync\(session, \{explicit: true\}\);/.test(appSource), 'Finder session switches sync the root from tmux metadata immediately while session-files refreshes');
    assert.ok(/fileExplorerSessionFilesCache\.set\(sessionFilesCacheKey\(session\), \{payload: nextPayload, signature\}\)/.test(appSource), 'Differ stores cached payloads under the same ref-aware key it reads');
    assert.ok(/function applySessionFilesPayloadFromPush\([\s\S]*sessionFilesPushRequestMatchesCurrent\(request, session\)/.test(appSource), 'SSE session-files payloads cannot overwrite the active Differ refs with a stale request');
    const stalePushApi = loadYolomux('', ['1']);
    stalePushApi.setFileExplorerModeForTest('diff');
    stalePushApi.setFileExplorerChangesSelectedSessionForTest('1');
    stalePushApi.setDiffRefsByRepoForTest('/home/test/vllm-0.22.0', {from: '0b3ba88', to: 'current'});
    stalePushApi.setFileExplorerSessionFilesPayloadForTest({
      session: '1',
      loaded: true,
      files: [],
      repos: [{repo: '/home/test/vllm-0.22.0', count: 8, added: 270, removed: 8}],
      errors: [],
    });
    assert.equal(stalePushApi.sessionFilesPushRequestMatchesCurrentForTest({
      session: '1',
      hours: 24,
      from_ref: 'HEAD',
      to_ref: 'current',
    }, '1'), false, 'a stale HEAD/current session-files push does not match active per-repo refs');
    assert.equal(stalePushApi.applySessionFilesPayloadFromPushForTest(
      {session: '1', loaded: true, files: [], repos: [{repo: '/home/test/vllm-0.22.0', count: 0, added: 0, removed: 0}]},
      {session: '1', hours: 24, from_ref: 'HEAD', to_ref: 'current'},
    ), false, 'stale session-files push is ignored before it can replace the active Differ payload');
    assert.equal(stalePushApi.sessionFilesPayloadForTest().repos[0].added, 270, 'ignored stale push leaves the visible Differ payload intact');
    const rootlessDifferApi = loadYolomux('', ['8002']);
    const yolomuxRepo = '/home/keivenc/yolomux.dev8002';
    rootlessDifferApi.setFileExplorerModeForTest('diff');
    rootlessDifferApi.setFileExplorerChangesSelectedSessionForTest('8002');
    rootlessDifferApi.setFileExplorerSessionFilesPayloadForTest({
      session: '8002',
      loaded: true,
      files: [{session: '8002', repo: yolomuxRepo, path: 'docs/DONE.md', abs_path: `${yolomuxRepo}/docs/DONE.md`, status: 'M'}],
      repos: [{repo: yolomuxRepo, count: 1, touched_count: 1, added: 4, removed: 0}],
      errors: [],
    });
    assert.equal(rootlessDifferApi.applySessionFilesPayloadFromPushForTest(
      {session: '8002', loaded: true, files: [], repos: [], errors: []},
      {session: '8002', hours: 24, from_ref: 'HEAD', to_ref: 'current'},
    ), false, 'rootless empty session-files push cannot blank a loaded same-session Differ repo');
    assert.equal(rootlessDifferApi.sessionFilesPayloadForTest().repos[0].repo, yolomuxRepo, 'ignored rootless push leaves the yolomux.dev8002 Differ repo visible');
    assert.equal(rootlessDifferApi.applySessionFilesPayloadFromPushForTest(
      {session: '8002', loaded: true, files: [], repos: [{repo: yolomuxRepo, count: 0, touched_count: 0, added: 0, removed: 0}], errors: []},
      {session: '8002', hours: 24, from_ref: 'HEAD', to_ref: 'current'},
    ), true, 'clean session-files push with the live repo root still clears Differ rows');
    assert.equal(rootlessDifferApi.sessionFilesPayloadForTest().repos[0].count, 0, 'clean rooted payload replaces the previous dirty count');
    const staleRefApi = loadYolomux('', ['8002']);
    staleRefApi.setFileExplorerModeForTest('diff');
    staleRefApi.setFileExplorerChangesSelectedSessionForTest('8002');
    staleRefApi.setFileExplorerSessionFilesPayloadForTest({
      session: '8002',
      loaded: true,
      files: [{session: '8002', repo: yolomuxRepo, path: 'docs/DONE.md', abs_path: `${yolomuxRepo}/docs/DONE.md`, status: 'M'}],
      repos: [{repo: yolomuxRepo, count: 1, touched_count: 1, added: 4, removed: 0}],
      errors: [],
    });
    staleRefApi.setDiffRefsByRepoForTest('/home/keivenc/yolomux.dev2', {from: 'HEAD', to: 'current'});
    staleRefApi.setDiffRefsByRepoForTest('/home/keivenc/dynamo/vllm-0.22.0', {from: '0b3ba88f165976e77ca5e6a7a3f5bba4562b80af', to: 'current'});
    staleRefApi.setDiffRefsByRepoForTest('/home/keivenc/dynamo/dynamo-utils.dev', {from: 'bc81c855a74be44b19941546d624bfb647f48055', to: 'current'});
    staleRefApi.setDiffRefsByRepoForTest(yolomuxRepo, {from: 'HEAD', to: 'current'});
    assert.equal(staleRefApi.sessionFilesCacheKeyForTest('8002').includes('&refs='), false, 'session-files requests omit stale repo refs and global-equivalent HEAD/current overrides');
    staleRefApi.setDiffRefsByRepoForTest(yolomuxRepo, {from: 'abc1234', to: 'current'});
    const sessionFilesQuery = staleRefApi.sessionFilesCacheKeyForTest('8002').split('\x1f')[1];
    const sessionFilesRefs = JSON.parse(new URLSearchParams(sessionFilesQuery).get('refs'));
    assert.deepStrictEqual(Object.keys(sessionFilesRefs), [yolomuxRepo], 'session-files requests keep only the current Differ repo override');
    assert.deepStrictEqual(sessionFilesRefs[yolomuxRepo], {from: 'abc1234', to: 'current'}, 'the current repo override remains intact');
    assert.ok(/function sessionFilesPayloadIsFinderWorktree\([\s\S]*from_ref \|\| 'HEAD'[\s\S]*to_ref \|\| 'current'/.test(appSource), 'Finder file mode can preserve an already-loaded HEAD/current payload for sync planning');
    assert.ok(/function sessionFilesPayloadShouldPreserveCurrent\([\s\S]*sessionFilesPayloadIsRootlessEmpty\(nextPayload\)[\s\S]*sessionFilesRepoRoots\(current\)\.length > 0/.test(appSource), 'Differ ignores rootless empty session-files pushes after a rooted payload is already visible');
    assert.ok(/if \(backgroundRefresh && sessionFilesPayloadShouldPreserveCurrent\(nextPayload\)\) return;/.test(appSource), 'background refreshes cannot blank a rooted Differ payload with a rootless empty result');
    assert.ok(/function sessionFilesRelevantDiffRefRepos\([\s\S]*sessionFilesRepoRoots\(payload\)[\s\S]*function sessionFilesRefsQuery\([\s\S]*relevantRepos\.has\(normalizedRepo\)[\s\S]*nextRefs\.from === globalRefs\.from/.test(appSource), 'session-files requests prune stale per-repo refs before calling the API');
    assert.ok(/fileExplorerMode !== 'diff' && sessionFilesPayloadIsFinderWorktree\(fileExplorerSessionFilesPayload, session\)/.test(appSource), 'Finder file mode does not blank the current worktree payload when committing a session');
    assert.ok(/function sessionFilesRequestQueryString\(\)[\s\S]*fileExplorerMode !== 'diff'[\s\S]*from=HEAD&to=current[\s\S]*diffRefQueryString\(\)\}\$\{sessionFilesRefsQuery\(\)\}/.test(appSource), 'Finder file mode requests only current worktree status while Differ follows selected refs');
    assert.ok(/function setFileExplorerMode\([\s\S]*fetchSessionFiles\(\{destination: 'finder', session: fileExplorerSessionFilesTargetSession\(\), silent: true, force: true\}\)/.test(appSource), 'switching back from Differ to Finder forces a fresh worktree-status fetch');
    assert.ok(/refreshFileExplorerTrees\(\);\s*fetchSessionFiles\(\{destination: 'finder', session: fileExplorerSessionFilesTargetSession\(\), silent: true, force: true\}\)/.test(appSource), 'Finder Reload refreshes the modified-file overlay as well as the directory tree');
    assert.equal(appSource.includes("state.kind === 'text' && !fileEditorAutosaveEnabled"), false, 'clean external file changes auto-reload even when autosave is off');
    assert.equal(appSource.includes('data-file-editor-close'), false, 'pane frame close uses the pane-close path, not active file-tab close');
    assert.equal(filesTab.includes('agent-icon file'), false);
    assert.ok(api.menuTabCommand('__files__').html.includes('app-menu-ui-icon-finder'));
    assert.ok(api.menuTabCommand('__prefs__').html.includes('app-menu-ui-icon-gear'));
    assert.ok(api.menuTabCommand('__info__').html.includes('app-menu-ui-icon-branch-info'));
    assert.ok(api.menuTabCommand('__yoagent__').html.includes('app-menu-ui-icon-yoagent'), 'YO!agent has its own tab/menu icon');
    assert.equal(api.menuTabCommand('__changes__').html.includes('app-menu-ui-icon-changes'), false, 'retired standalone Differ no longer has a menu/tab icon');
    assert.ok(api.menuTabCommand('file:/home/test/README.md').html.includes('app-menu-ui-icon-document'));
    assert.equal(api.platformWindowControlClass('minimize'), 'pc-window-control pc-minimize');
    assert.equal(api.platformWindowControlClass('close'), 'pc-window-control pc-close');
    assert.equal(api.platformWindowControlClass('zoom'), 'pc-window-control pc-zoom');
    assert.equal(api.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
    assert.equal(api.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');
    const pcSingleSlots = api.emptyLayoutSlots();
    pcSingleSlots[api.layoutTreeKey] = api.leafNode('left');
    pcSingleSlots.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(pcSingleSlots);
    const pcPaneControls = api.panelControlsHtml('1');
    assert.ok(pcPaneControls.includes('pane-minimize pc-window-control pc-minimize'));
    assert.ok(pcPaneControls.includes('pane-expand pc-window-control pc-zoom'));
    assert.ok(pcPaneControls.includes('panel-detail-toggle pane-detail-toggle pc-window-control pc-minimize'));
    assert.equal(pcPaneControls.includes('>Info</button>'), false);
    assert.ok(pcPaneControls.indexOf('pane-detail-toggle') < pcPaneControls.indexOf('pane-minimize'));
    assert.ok(pcPaneControls.includes('hidden type="button" data-pane-expand="1"'));
    const expandablePcSlots = api.emptyLayoutSlots();
    expandablePcSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    expandablePcSlots.left = api.paneStateWithTabs(['1'], '1');
    expandablePcSlots.slot1 = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(expandablePcSlots);
    assert.equal(api.canPaneExpand('1'), true);
    assert.ok(api.panelControlsHtml('1').includes('pane-expand pc-window-control pc-zoom'));
    assert.equal(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'), false);
    const placeholderPcSlots = api.emptyLayoutSlots();
    placeholderPcSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    placeholderPcSlots.left = api.paneStateWithTabs(['1'], '1');
    placeholderPcSlots.slot1 = api.emptyPlaceholderPaneState();
    api.setLayoutSlotsForTest(placeholderPcSlots);
    assert.equal(api.canPaneExpand('1'), false);
    assert.ok(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));

    const macApi = loadYolomux('', ['1'], 'http:', 'MacIntel');
    assert.equal(macApi.fileExplorerLabel(), 'Finder');
    assert.equal(macApi.platformWindowControlClass('minimize'), 'pc-window-control pc-minimize');
    assert.equal(macApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
    assert.equal(macApi.platformWindowControlClass('zoom'), 'pc-window-control pc-zoom');
    assert.equal(macApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
    assert.equal(macApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');
    const macPaneControls = macApi.panelControlsHtml('1');
    assert.ok(macPaneControls.includes('data-pane-minimize="1"'));
    assert.ok(macPaneControls.includes('data-detail-toggle="1"'));
    assert.ok(macPaneControls.includes('pane-minimize pc-window-control pc-minimize'));
    assert.ok(macPaneControls.indexOf('pane-detail-toggle') < macPaneControls.indexOf('pane-minimize'));
    assert.ok(macPaneControls.includes('data-pane-expand="1"'));
    assert.ok(macPaneControls.includes('pane-expand pc-window-control pc-zoom'));
    assert.ok(macPaneControls.includes('hidden type="button" data-pane-expand="1"'));
    const macFinderControls = macApi.panelControlsHtml('__files__');
    assert.ok(macFinderControls.includes('data-pane-close="__files__"'));
    assert.ok(macFinderControls.includes('pane-close pc-window-control pc-close'));
    assert.equal(macFinderControls.includes('data-pane-expand'), false);
    const macFinderFields = macApi.tabSearchFields(macApi.fileExplorerItemId);
    assert.ok(macFinderFields.includes('Finder'), 'Finder tab indexes its visible macOS name');
    assert.ok(macFinderFields.includes('File Explorer'), 'Finder tab also indexes the File Explorer alias');
    assert.equal(macApi.commandPaletteMatches({group: 'Tabs', label: 'Finder', detail: '', searchFields: macFinderFields}, 'File Explorer'), true, 'typing File Explorer finds the Finder command palette row');

    const forcedPcApi = loadYolomux('?platform=pc', ['1'], 'http:', 'MacIntel');
    assert.equal(forcedPcApi.fileExplorerLabel(), 'File Explorer');
    assert.ok(forcedPcApi.tabSearchFields(forcedPcApi.fileExplorerItemId).includes('Finder'), 'File Explorer tab also indexes the Finder alias');
    assert.equal(forcedPcApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
    assert.equal(forcedPcApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
    assert.equal(forcedPcApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');

    const forcedMacApi = loadYolomux('?platform=mac', ['1'], 'http:', 'Linux x86_64');
    assert.equal(forcedMacApi.fileExplorerLabel(), 'Finder');
    assert.equal(forcedMacApi.platformWindowControlClass('close'), 'pc-window-control pc-close');
    assert.equal(forcedMacApi.fileExplorerPanelCloseClass(), 'file-explorer-panel-close pc-window-control pc-close');
    assert.equal(forcedMacApi.fileEditorPanelCloseClass(), 'file-editor-panel-close pc-window-control pc-close');

    const watchApi = loadYolomux('', ['1']);
    const visiblePath = '/repo/README.md';
    const backgroundPath = '/repo/NOTES.md';
    const visibleItem = watchApi.registerFileEditorLayoutItem(visiblePath);
    const backgroundItem = watchApi.registerFileEditorLayoutItem(backgroundPath);
    const watchSlots = watchApi.emptyLayoutSlots();
    watchSlots[watchApi.layoutTreeKey] = watchApi.splitNode('row', watchApi.leafNode('left'), watchApi.leafNode('right'));
    watchSlots.left = watchApi.paneStateWithTabs([backgroundItem, visibleItem], visibleItem);
    watchSlots.right = watchApi.paneStateWithTabs(['1'], '1');
    watchApi.setLayoutSlotsForTest(watchSlots);
    assert.deepStrictEqual([...watchApi.visibleFileEditorWatchFilesForTest()], [visiblePath], 'active visible editor files use the fast watch list');
    assert.deepStrictEqual([...watchApi.backgroundFileEditorWatchFilesForTest()], [backgroundPath], 'background editor tabs use the slower watch list');
    assert.deepStrictEqual([...watchApi.clientServerWatchStateForTest().files], [visiblePath], 'watch payload sends active editor files under files');
    assert.deepStrictEqual([...watchApi.clientServerWatchStateForTest().background_files], [backgroundPath], 'watch payload sends background editor files separately');
    watchApi.activatePaneTab('left', backgroundItem, {userInitiated: true});
    assert.deepStrictEqual([...watchApi.visibleFileEditorWatchFilesForTest()], [backgroundPath], 'activating a background editor promotes it to the fast watch list');
    assert.deepStrictEqual([...watchApi.backgroundFileEditorWatchFilesForTest()], [visiblePath], 'the previously visible editor moves to the slower watch list');

    const selfHealingFinderUrlApi = loadYolomux('?sessions=1&layout=left&tabs=left:1,2,3,4,5,6,ant', ['1', '2', '3', '4', '5', '6', 'ant']);
    assert.equal(selfHealingFinderUrlApi.itemInLayout('__files__'), true, 'Finder-less URLs self-heal unless this tab explicitly closed Finder');
    assert.deepStrictEqual(Array.from(selfHealingFinderUrlApi.layoutSlotKeys(selfHealingFinderUrlApi.currentSlots())), ['slot1', 'left']);
    const singlePaneUrlApi = loadYolomux(
      '?sessions=1&layout=left&tabs=left:1,2,3,4,5,6,ant',
      ['1', '2', '3', '4', '5', '6', 'ant'],
      'http:',
      'Linux x86_64',
      'admin',
      fileExplorerClosedOptions(),
    );
    assert.deepStrictEqual(Array.from(singlePaneUrlApi.layoutSlotKeys(singlePaneUrlApi.currentSlots())), ['left']);
    assert.deepStrictEqual(Array.from(singlePaneUrlApi.paneTabs('left')), ['1', '2', '3', '4', '5', '6', 'ant']);
    assert.equal(singlePaneUrlApi.canPaneExpand('1'), false);
    assert.ok(singlePaneUrlApi.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));

    const staleFinderWidthUrlApi = loadYolomux(
      '?sessions=1,2&layout=row@22(left,slot1)&tabs=left:1;slot1:2',
      ['1', '2'],
      'http:',
      'Linux x86_64',
      'admin',
      fileExplorerClosedOptions(),
    );
    assert.deepStrictEqual(canonical(staleFinderWidthUrlApi.serialize(staleFinderWidthUrlApi.currentSlots())), {
      tree: {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['1'], active: '1'},
        slot1: {tabs: ['2'], active: '2'},
      },
    }, 'stale Finder-width URLs do not restore a non-Finder terminal pane at 22%');
    assert.equal(staleFinderWidthUrlApi.layoutParamValue(staleFinderWidthUrlApi.currentSlots()), 'row@50(left,slot1)');

    const finderBesideSinglePaneUrlApi = loadYolomux(
      '?sessions=files,3&layout=row@22(slot1,left)&tabs=slot1:files;left:1,6,5,2,ant,4,3*',
      ['1', '2', '3', '4', '5', '6', 'ant'],
    );
    assert.deepStrictEqual(Array.from(finderBesideSinglePaneUrlApi.layoutSlotKeys(finderBesideSinglePaneUrlApi.currentSlots())), ['slot1', 'left']);
    assert.equal(finderBesideSinglePaneUrlApi.activeItemForSide('slot1'), '__files__');
    assert.deepStrictEqual(Array.from(finderBesideSinglePaneUrlApi.paneTabs('left')), ['1', '6', '5', '2', 'ant', '4', '3']);
    assert.equal(finderBesideSinglePaneUrlApi.activeItemForSide('left'), '3');
    assert.equal(finderBesideSinglePaneUrlApi.canPaneExpand('3'), false);
    assert.ok(finderBesideSinglePaneUrlApi.panelControlsHtml('3').includes('hidden type="button" data-pane-expand="3"'));

    const defaultFinderApi = loadYolomux('', ['1', '2']);
    assert.equal(defaultFinderApi.itemInLayout('__files__'), true, 'param-less boot includes the Finder pane');
    assert.equal(defaultFinderApi.itemInLayout('__files__', defaultFinderApi.defaultLayoutSlots()), true, 'defaultLayoutSlots includes the Finder pane');
    const sessionsOnlyFinderApi = loadYolomux('?sessions=1', ['1', '2']);
    assert.equal(sessionsOnlyFinderApi.itemInLayout('__files__'), true, 'sessions-only boot includes the Finder pane');

    const finderToggleSlots = api.emptyLayoutSlots();
    finderToggleSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 31);
    finderToggleSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    finderToggleSlots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(finderToggleSlots);
    api.setFocusedPanelItem('1');
    const finderLayoutBeforeToggle = api.layoutParamValue(api.currentSlots());
    api.toggleFileExplorerShortcut();
    assert.equal(api.itemInLayout('__files__'), false, 'app shortcut hides the Finder pane');
    assert.equal(api.sessionStorageValueForTest(FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST), '0', 'keyboard-hiding Finder writes the explicit closed intent');
    assert.equal(api.fileExplorerClosedByUserForTest(), true, 'keyboard-hiding Finder suppresses self-heal until the shortcut restores it');
    assert.equal(api.statusTextForTest(), `${api.fileExplorerLabel()} hidden - ${api.appShortcutText('B')} restores`, 'hiding Finder announces how to restore it');
    assert.equal(api.focusedPanelItemForTest(), '1', 'hiding Finder keeps focus on the active terminal');
    api.toggleFileExplorerShortcut();
    assert.equal(api.sessionStorageValueForTest(FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST), '1', 'restoring Finder records explicit per-tab open intent');
    assert.equal(api.layoutParamValue(api.currentSlots()), finderLayoutBeforeToggle, 'app shortcut restores the prior Finder position and split size');
    assert.equal(api.focusedPanelItemForTest(), '1', 'restoring Finder keeps focus on the active terminal');
    api.toggleFileExplorerShortcut();
    api.clearFileExplorerShortcutRestoreSlotsForTest();
    api.toggleFileExplorerShortcut();
    assert.equal(api.itemInLayout('__files__'), true, 'app shortcut reopens Finder even when the in-memory restore snapshot is gone');

    const focusedEditor = api.testElementForId('shortcut-editor');
    focusedEditor.className = 'cm-editor';
    api.setDocumentActiveElementForTest(focusedEditor);
    const shortcutListeners = api.windowListenersForTest('keydown');
    const shortcutEvent = {
      key: 'b',
      code: 'KeyB',
      metaKey: false,
      ctrlKey: true,
      altKey: false,
      shiftKey: false,
      target: focusedEditor,
      prevented: false,
      preventDefault() { this.prevented = true; },
      stopPropagation() {},
    };
    shortcutListeners.forEach(listener => listener(shortcutEvent));
    assert.equal(shortcutEvent.prevented, false, 'Cmd/Ctrl-B is not stolen while editor focus owns text formatting');
    assert.equal(api.itemInLayout('__files__'), true, 'blocked Cmd/Ctrl-B does not hide Finder from editor focus');
    api.setDocumentActiveElementForTest(null);
    const macFinderShortcutApi = loadYolomux('?platform=mac', ['1'], 'http:', 'MacIntel');
    const macTerminalTarget = macFinderShortcutApi.testElementForId('mac-shortcut-xterm');
    macTerminalTarget.className = 'xterm';
    macFinderShortcutApi.setDocumentActiveElementForTest(macTerminalTarget);
    assert.equal(macFinderShortcutApi.globalShortcutTargetAllowsAppAction(macTerminalTarget), false, 'terminal focus is still blocked for generic app shortcuts');
    assert.equal(macFinderShortcutApi.globalShortcutTargetAllowsFinderShortcut(macTerminalTarget), true, 'Mac Cmd-B is allowed from terminal focus');
    assert.equal(macFinderShortcutApi.globalShortcutShouldToggleFinderForTest({
      key: 'b',
      code: 'KeyB',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      target: macTerminalTarget,
    }), true, 'Mac Cmd-B toggles Finder even when terminal focus owns the hidden xterm textarea');
    const pcTerminalTarget = api.testElementForId('pc-shortcut-xterm');
    pcTerminalTarget.className = 'xterm';
    api.setDocumentActiveElementForTest(pcTerminalTarget);
    assert.equal(api.globalShortcutShouldToggleFinderForTest({
      key: 'b',
      code: 'KeyB',
      metaKey: false,
      ctrlKey: true,
      altKey: false,
      shiftKey: false,
      target: pcTerminalTarget,
    }), false, 'PC/Linux Ctrl-B is not stolen from a focused terminal because tmux owns it');
    api.setDocumentActiveElementForTest(null);

    const fileEditorItem = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/README.md');
    const fileEditorSlots = api.emptyLayoutSlots();
    fileEditorSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 20);
    fileEditorSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    fileEditorSlots.slot1 = api.paneStateWithTabs([fileEditorItem], fileEditorItem);
    fileEditorSlots.slot2 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(fileEditorSlots);
    api.setLayoutColumnRectsForTest({
      left: {left: 0, right: 220, top: 0, bottom: 800, width: 220, height: 800},
      slot1: {left: 230, right: 580, top: 0, bottom: 800, width: 350, height: 800},
      slot2: {left: 590, right: 1190, top: 0, bottom: 800, width: 600, height: 800},
    });
    assert.equal(api.largestPaneSlotForFileEditor(['slot1']), 'slot2', 'file editor helpers choose the next biggest existing non-Finder pane');

    const delayHtml = api.preferencesPanelHtmlForTest('delay', ['Performance']);
    assert.ok(delayHtml.includes('data-preference-section="Performance"'), 'delay search shows Performance');
    assert.equal(/data-preference-section="Performance"[\s\S]*preferences-settings" hidden/.test(delayHtml), false, 'search expands matching collapsed sections');
    assert.ok(delayHtml.includes('Server SSE: editor file-change poll'), 'delay search surfaces server SSE timing settings');
    const preferencesCss = fs.readFileSync('static/yolomux.css', 'utf8');
    const preferencesJs = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(preferencesCss.startsWith('/* GENERATED by tools/static_build.py from static_src/'), 'generated CSS has a do-not-edit header');
    assert.ok(/\.preferences-section-toggle\s*\{[\s\S]*color:\s*var\(--pane-tab-text\)[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Preferences section headers use the same background token as the pane tab container');
    assert.ok(/\.preferences-search-button\s*\{[\s\S]*font:\s*700 var\(--ui-font-size-sm\)\/1\.1 var\(--ui-font\)/.test(preferencesCss), 'Preferences search button uses the normal UI font, not condensed tab text');
    assert.ok(preferencesCss.includes('--file-explorer-changes-min-block-size: 96px'), 'modified-files resizer shares a stable min-size token');
    assert.ok(preferencesCss.includes('--drop-outline: #ffffff'), '#40: dark-mode drag preview/outline is white (light mode stays blue, asserted below)');
    assert.ok(preferencesCss.includes('--text-selection-bg: #2563eb'), 'dark mode browser text selection uses a prominent blue fill');
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--text-selection-bg:\s*#93c5fd/.test(preferencesCss), 'light mode browser text selection uses a visible blue fill');
    assert.ok(/::selection\s*\{(?=[^}]*background:\s*var\(--text-selection-bg\))(?![^}]*color:)[^}]*\}/.test(preferencesCss), 'browser text selection paints background only, preserving text color');
    assert.ok(/::-moz-selection\s*\{(?=[^}]*background:\s*var\(--text-selection-bg\))(?![^}]*color:)[^}]*\}/.test(preferencesCss), 'Firefox text selection paints background only, preserving text color');
    assert.equal(preferencesCss.includes('--text-selection-text'), false, 'global selection no longer defines selected-text color');
    assert.ok(/\.file-tree-repo-meta\s*\{[^}]*font-size: var\(--ui-font-size-2xs\)/.test(preferencesCss), '#37: the Finder repo/branch label is condensed to a smaller font so more files fit');
    assert.ok(/\.file-explorer-hidden-toggle,\s*\n\.file-explorer-root-mode-toggle,\s*\n\.file-explorer-header-action\s*\{[\s\S]*white-space:\s*nowrap/.test(preferencesCss), 'Finder toolbar text buttons never wrap localized labels vertically');
    assert.ok(/\.file-explorer-root-mode-toggle\s*\{[\s\S]*?width:\s*auto[\s\S]*?min-width:\s*38px[\s\S]*?flex:\s*0 0 auto/.test(preferencesCss), 'Finder root-mode button sizes to localized label content');
    assert.ok(/\.file-explorer-root-mode-toggle-panel\s*\{[\s\S]*?width:\s*auto[\s\S]*?min-width:\s*38px[\s\S]*?flex:\s*0 0 auto/.test(preferencesCss), 'Finder panel root-mode button sizes to localized label content');
    assert.ok(/\.file-explorer-toolbar\s*\{[\s\S]*flex-direction:\s*column[\s\S]*justify-content:\s*flex-start/.test(preferencesCss), 'Finder toolbar is a deliberate stacked-row column instead of one wrapping row');
    assert.ok(/\.file-explorer-toolbar-row\s*\{[\s\S]*inline-size:\s*100%/.test(preferencesCss), 'Finder toolbar rows span the pane width');
    assert.ok(/\.file-explorer-toolbar-spacer\s*\{[\s\S]*flex:\s*1 1 auto/.test(preferencesCss), 'Finder toolbar uses explicit spacers to pin right-side controls');
    assert.ok(/\.file-explorer-actions-row \.file-explorer-date-reload-cluster\s*\{[\s\S]*display:\s*inline-flex/.test(preferencesCss), 'Finder row 3 keeps date, tree expand/collapse, and reload grouped');
    assert.ok(/\.file-explorer-date-toggle\[data-date-mode="none"\]\s*\{[\s\S]*text-decoration-line:\s*line-through/.test(preferencesCss), 'Finder/Differ None date mode renders as crossed-out Date');
    assert.ok(/\.file-explorer-path-row \.file-explorer-path-inline\s*\{[\s\S]*flex:\s*1 1 0[\s\S]*min-width:\s*0[\s\S]*min-inline-size:\s*0/.test(preferencesCss), 'Finder path fills the dedicated path row and can shrink without wrapping Sync or Copy');
    assert.equal(/body:not\(\.file-explorer-mode-diff\) \.file-explorer-primary-row \.file-explorer-toolbar-spacer/.test(preferencesCss), false, 'Finder files mode keeps the primary-row spacer so Session stays pinned right');
    assert.ok(/\.file-explorer-mode-switcher\s*\{[\s\S]*display:\s*inline-flex[\s\S]*height:\s*var\(--pane-tab-height\)/.test(preferencesCss), 'Finder/Differ/Tabber mode switcher uses pane-tab height');
    assert.ok(/\.file-explorer-mode-toggle\s*\{(?=[\s\S]*height:\s*var\(--pane-tab-height\))(?=[\s\S]*font-family:\s*var\(--tab-font\))(?=[\s\S]*border-radius:\s*6px 6px 0 0)/.test(preferencesCss), 'Finder/Differ/Tabber mode buttons look like pane tabs');
    assert.equal(/\.file-explorer-mode-label\s*\{[\s\S]*writing-mode:\s*vertical-rl/.test(preferencesCss), false, 'Finder/Differ mode labels are regular left-to-right text');
    assert.ok(/\.file-explorer-mode-toggle\s*\{[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'inactive Finder/Differ/Tabber mode tabs use the pane tab strip background');
    assert.ok(/\.file-explorer-mode-toggle\[aria-pressed="true"\]\s*\{[\s\S]*background:\s*var\(--pane-tab-active-bg\)/.test(preferencesCss), 'active Finder/Differ/Tabber mode tab is filled from the pane-tab active token');
    assert.ok(/\.file-explorer-folder-icon\s*\{[\s\S]*border:\s*1\.5px solid currentColor[\s\S]*\.file-explorer-folder-icon::before/.test(preferencesCss), 'Finder new-folder button renders a folder icon instead of a square glyph');
    assert.ok(/\.file-explorer-path,[\s\S]*?\.file-explorer-path-inline\s*\{[\s\S]*color:\s*var\(--text\)[\s\S]*border:\s*1px solid var\(--line\)/.test(preferencesCss), 'Finder path uses normal text contrast and visible input chrome');
    const finderPanelBundle = fs.readFileSync('static/yolomux.js', 'utf8');
    const finderPanelStart = finderPanelBundle.indexOf('function createFileExplorerPanel');
    const finderPanelSource = finderPanelBundle.slice(
      finderPanelStart,
      finderPanelBundle.indexOf('function bindFileExplorerPanel', finderPanelStart),
    );
    assert.ok(/file-explorer-toolbar-row file-explorer-primary-row[\s\S]*file-explorer-toolbar-row file-explorer-path-row file-explorer-mode-files-only[\s\S]*file-explorer-toolbar-row file-explorer-actions-row file-explorer-mode-files-only/.test(finderPanelSource), 'Finder panel toolbar renders primary, path, and files-only action rows in order');
    assert.equal(finderPanelSource.includes('file-explorer-diff-row'), false, 'Differ title is folded into the shared primary row');
    assert.equal(finderPanelSource.includes('file-explorer-panel-title'), false, 'Finder panel no longer prints redundant Finder/Differ title text');
    assert.ok(/file-explorer-toolbar-row file-explorer-primary-row[\s\S]*fileExplorerModeSwitcherHtml\(\)[\s\S]*fileExplorerDiffSessionControlHtml\(fileExplorerSessionFilesTargetSession\(\)\)[\s\S]*file-explorer-toolbar-spacer[\s\S]*file-explorer-frame-controls/.test(finderPanelSource), 'Finder panel primary row renders mode tabs, immediate Session, spacer, and close control');
    assert.equal(finderPanelSource.includes('fileExplorerChangesCollapseToggleHtml()'), false, 'Finder panel primary row no longer renders a redundant Differ collapse/expand button next to close');
    assert.ok(/file-explorer-toolbar-row file-explorer-path-row file-explorer-mode-files-only[\s\S]*file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel[\s\S]*<input class="file-explorer-path-inline file-explorer-mode-files-only"[\s\S]*file-explorer-path-copy-panel/.test(finderPanelSource), 'Finder path row renders Sync, path, then Copy');
    assert.ok(/function fileExplorerDiffSessionControlHtml[\s\S]*file-explorer-diff-session-control file-explorer-mode-files-diff-only changes-control[\s\S]*changes\.session[\s\S]*sessionFilesSessionSelectHtml\(session/.test(finderPanelBundle), 'Finder and Differ keep the Session dropdown in the shared top row');
    assert.equal(finderPanelSource.includes('file-explorer-scope-row'), false, 'Finder no longer renders a separate scope row');
    assert.equal(finderPanelSource.includes('file-explorer-quick-access-panel'), false, 'Finder pane chrome no longer renders visible quick-root buttons');
    assert.ok(finderPanelBundle.includes("t('finder.toolbar.syncTitle')"), 'Finder Sync button has a dedicated tooltip/aria label string');
    assert.ok(finderPanelSource.includes('title="${esc(t(\'finder.toolbar.syncTitle\'))}"') && finderPanelSource.includes('${esc(t(\'finder.toolbar.syncLabel\'))}</button>'), 'Finder Sync panel button uses the full tooltip while keeping the compact visible label');
    assert.equal(api.displayQuickAccessPath('/'), '/*', 'Finder root quick-access button labels root as /*');
    assert.equal(api.displayQuickAccessPath('/*'), '/*', 'Finder accepts /* as the root quick-access label');
    assert.equal(api.expandQuickAccessPath('/'), '/', 'Finder / quick-access opens the root directory');
    assert.equal(api.expandQuickAccessPath('/*'), '/', 'Finder /* quick-access opens the root directory, not a literal glob path');
    assert.equal(api.displayQuickAccessPath('/tmp'), '/tmp', 'Finder quick-access labels absolute paths such as /tmp with their leading slash');
    assert.equal(finderPanelBundle.includes('renderQuickAccessInto(fileExplorerQuickAccess)'), false, 'legacy Finder quick-root container is not populated in visible UI');
    assert.ok(/const modes = \[[\s\S]*mode: 'files'[\s\S]*mode: 'diff'[\s\S]*mode: 'tabber'[\s\S]*data-file-explorer-mode-set="\$\{esc\(item\.mode\)\}"/.test(finderPanelBundle), 'Finder/Differ/Tabber switcher renders all mode tabs from one source');
    assert.ok(finderPanelSource.includes("fileExplorerTreeDateButtonHtml('changes-date-toggle')"), 'Finder panel toolbar uses the shared date-mode button helper with the Differ sizing class');
    assert.ok(/fileExplorerTreeSortSelectHtml\('file-explorer-mode-files-only'\)[\s\S]*file-explorer-date-reload-cluster[\s\S]*fileExplorerTreeDateButtonHtml\('changes-date-toggle'\)[\s\S]*fileTreeExpandCollapseAllButtonsHtml\('changes-date-toggle'\)[\s\S]*data-file-explorer-refresh[\s\S]*changes\.refresh/.test(finderPanelSource), 'Finder date-mode button, Expand all, Collapse all, and Reload form a trailing cluster in the files-only action row');
    assert.equal(finderPanelSource.includes('file-explorer-repo-summary'), false, 'Finder files-only action row no longer prints repo/path text between sort and date display');
    const finderActionsRowStart = finderPanelSource.indexOf('file-explorer-toolbar-row file-explorer-actions-row');
    const finderActionsRowSource = finderPanelSource.slice(finderActionsRowStart, finderPanelSource.indexOf('</div>', finderActionsRowStart));
    assert.ok(/data-file-explorer-new-file[\s\S]*data-file-explorer-new-folder[\s\S]*file-explorer-folder-icon/.test(finderActionsRowSource), 'Finder files-only action row renders new file, then a folder-icon new-folder button');
    assert.ok(/file-explorer-hidden-toggle file-explorer-hidden-toggle-panel[\s\S]*fileExplorerTreeSortSelectHtml\('file-explorer-mode-files-only'\)/.test(finderActionsRowSource), 'Finder files-only action row puts .* before the sort selector');
    assert.equal(finderActionsRowSource.includes('data-file-explorer-collapse'), false, 'Finder files-only action row no longer renders the old standalone left-side collapse button');
    const treeExpandCollapseButtons = api.fileTreeExpandCollapseAllButtonsHtml('changes-date-toggle');
    assert.ok(/data-file-tree-expand-collapse-all="expand"[\s\S]*data-file-tree-expand-collapse-all="collapse"/.test(treeExpandCollapseButtons), 'shared tree expand/collapse helper renders Expand all before Collapse all');
    assert.ok(/data-file-tree-expand-collapse-all="expand"[\s\S]*file-tree-expand-collapse-icon[\s\S]*data-file-tree-expand-collapse-all="collapse"[\s\S]*file-tree-expand-collapse-icon/.test(treeExpandCollapseButtons), 'shared tree expand/collapse helper renders toolbar SVG icons');
    assert.equal(treeExpandCollapseButtons.includes('▦') || treeExpandCollapseButtons.includes('▤'), false, 'shared tree expand/collapse helper does not use square text glyphs');
    assert.ok(/\.file-tree-expand-collapse-all\s*\{[\s\S]*box-sizing:\s*border-box[\s\S]*width:\s*16px[\s\S]*max-width:\s*16px[\s\S]*flex:\s*0 0 16px[\s\S]*padding:\s*0/.test(preferencesCss), 'shared tree expand/collapse toolbar icon buttons stay narrow');
    assert.ok(/\.changes-toolbar \.file-tree-expand-collapse-all\.changes-date-toggle,[\s\S]*\.file-explorer-date-reload-cluster \.file-tree-expand-collapse-all\.changes-date-toggle\s*\{[\s\S]*max-inline-size:\s*16px[\s\S]*height:\s*20px/.test(preferencesCss), 'Differ/Finder toolbar context cannot widen tree expand/collapse icon buttons');
    assert.equal(finderActionsRowSource.includes('data-file-explorer-mode-set'), false, 'Finder files-only action row no longer repeats the mode switcher');
    assert.equal(/data-file-explorer-refresh[\s\S]*file-explorer-collapse/.test(finderPanelSource), false, 'Finder no longer has a standalone left-side refresh button before collapse');
    assert.equal(preferencesCss.includes('--file-explorer-changes-size: 40%'), false, 'Finder diff mode no longer keeps the old 40% stacked Modified-files section cap');
    assert.ok(/body\.file-explorer-mode-files \.file-explorer-changes-panel[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="files"\] \.file-explorer-changes-panel/.test(preferencesCss), 'files mode hides the Finder changes panel and resizer on the host and in DOM replay');
    assert.ok(/body\.file-explorer-mode-diff \.file-explorer-tree-panel[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="diff"\] \.file-explorer-tree-panel/.test(preferencesCss), 'diff mode hides the Finder tree panel on the host and in DOM replay');
    assert.ok(/body\.file-explorer-mode-diff \.file-explorer-changes-panel[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="diff"\] \.file-explorer-changes-panel[\s\S]*?\{[\s\S]*flex:\s*1 1 auto[\s\S]*max-block-size:\s*none/.test(preferencesCss), 'diff mode lets the Finder changes panel fill the pane on the host and in DOM replay');
    assert.ok(/body\.file-explorer-mode-diff \.file-explorer-mode-files-only,[\s\S]*body\.file-explorer-mode-tabber \.file-explorer-mode-files-only,[\s\S]*body:not\(\.file-explorer-mode-diff\) \.file-explorer-mode-diff-only,[\s\S]*body\.file-explorer-mode-tabber \.file-explorer-mode-files-diff-only,[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="diff"\] \.file-explorer-mode-files-only,[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="tabber"\] \.file-explorer-mode-files-only,[\s\S]*\.file-explorer-panel:not\(\[data-file-explorer-mode="diff"\]\) \.file-explorer-mode-diff-only,[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="tabber"\] \.file-explorer-mode-files-diff-only\s*\{[\s\S]*?display:\s*none/.test(preferencesCss), 'Tabber hides Finder-only toolbar controls and Finder/Differ Session controls on the host and in DOM replay');
    assert.ok(/body\.file-explorer-mode-tabber \.file-explorer-changes-panel[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="tabber"\] \.file-explorer-changes-panel/.test(preferencesCss), 'DOIT.58 B1: tabber mode fills the pane like diff (tree hidden, changes panel full)');
    assert.ok(/body\.file-explorer-mode-tabber \.file-explorer-tree-panel[\s\S]*\.file-explorer-panel\[data-file-explorer-mode="tabber"\] \.file-explorer-tree-panel/.test(preferencesCss), 'DOIT.58 B1: tabber mode hides the Finder tree panel');
    assert.ok(/\.file-explorer-changes-panel \.changes-comparison-head\s*\{[^}]*flex-wrap: nowrap/.test(preferencesCss), '#44(d): the Finder comparison header is compacted to one tight line (header chrome takes less height)');
    assert.ok(/\.grid\.drop-preview::before/.test(preferencesCss), 'root layout drops have a full-layout preview overlay');
    assert.ok(/\.grid\.drop-preview-gutter::before\s*\{[\s\S]*--drop-preview-left/.test(preferencesCss), 'split-bar drops use explicit full-span preview geometry');
    // C15: the Finder↔Modified-files resizer reuses the shared --pane-resizer-* tokens (thin yellow line at
    // rest, hover brightens) instead of its old special-cased hardcoded-green strip.
    const resizerStart = preferencesCss.indexOf('.file-explorer-changes-resizer {');
    const resizerBeforeStart = preferencesCss.indexOf('.file-explorer-changes-resizer::before', resizerStart);
    const resizerEnd = preferencesCss.indexOf('.file-explorer-changes-panel {', resizerBeforeStart);
    const resizerBlock = preferencesCss.slice(resizerStart, resizerEnd);
    assert.ok(resizerBlock.includes('var(--pane-resizer-bg)'), 'C15: the Finder resizer draws the shared thin yellow line at rest');
    assert.ok(resizerBlock.includes('var(--pane-resizer-hover-bg)'), 'C15: the Finder resizer brightens via the shared hover token');
    assert.ok(resizerBlock.includes('var(--drop-outline-shadow)'), 'C15: the Finder resizer hover uses the shared outline-shadow token');
    assert.equal(/118,\s*185,\s*0|255,\s*255,\s*255,\s*0\.04/.test(resizerBlock), false, 'C15: the hardcoded green gradient + white border are gone from the Finder resizer');
    // Light theme drives the BASE pane-tab/ring styling via tokens, not rule overrides; but #28
    // adds targeted hover/link contrast overrides (expected), so guard only the base tab + ring.
    assert.equal(/body\.theme-light\s+\.pane-tab\s*\{/.test(preferencesCss), false, 'light theme does not restyle the base pane tab (tokens drive it)');
    assert.equal(/body\.theme-light\s+\.panel\.active-pane\s*\{/.test(preferencesCss), false, 'light theme does not restyle the active-pane ring directly');
    assert.ok(/body\.theme-light \.meta a/.test(preferencesCss), '#28: light theme adds a contrast override for Info Bar links');
    assert.ok(/body\.theme-light \.pane-tab:hover/.test(preferencesCss), '#28: light theme fixes the near-white pane-tab hover border');
    assert.ok(/body\.theme-light \.tabs \.pane-actions:hover/.test(preferencesCss), '#28: light theme fixes the white tab-overflow hover glyph');
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--active-accent-bright:\s*#4f9e3a/.test(preferencesCss), '#31: the active pane tab has a light-mode green (via the active-accent token) so a theme switch repaints it');
    assert.ok(/body\.theme-light \.panel\.active-pane \.panel-detail-row \.session-button-name/.test(preferencesCss), '#35: the active-pane Info Bar header label is forced dark in light mode (was light-on-light)');
    assert.ok(fs.readFileSync('static/yolomux.js', 'utf8').includes('session-button-dir pane-tab-info-label'), '#27: the YO!info tab label uses the themed .session-button-dir color treatment');
    assert.ok(preferencesCss.includes('--active-accent-bright: #86d600'), 'focused active pane tab uses a brighter NV green fill (via the active-accent token)');
    assert.ok(preferencesCss.includes('--pane-tab-active-accent: var(--active-accent-bright)'), 'focused active pane tab accent token derives from the shared active-accent (same green as the fill)');
    assert.equal(preferencesCss.includes('box-shadow: inset 0 2px 0 var(--pane-tab-active-accent)'), false, 'focused active pane tabs do not paint a contrasting top line');
    assert.ok(preferencesCss.includes('--pane-tab-width: 180px'), 'pane tabs default to the compact 180px width');
    assert.ok(changedFilesSource.includes("numberSetting('appearance.tab_width', 180)"), 'runtime settings fallback keeps the 180px tab width default');
    assert.ok(preferencesCss.includes('--active-accent-dim: color-mix(in srgb, var(--active-accent) 26%, var(--panel))'), 'dark/root theme uses the brighter shared pane tab-strip background (active-accent-dim)');
    assert.equal(/body\.theme-dark\s*\{[^}]*--pane-tab-strip-bg\s*:/.test(preferencesCss), false, 'dark theme inherits the shared pane tab-strip token instead of restating a separate color');
    assert.equal(preferencesCss.includes('--pane-tab-strip-hover-bg:'), false, 'dark theme no longer defines a separate pane tab-container hover token');
    const themeLightTokenBlock = preferencesCss.match(/body\.theme-light\s*\{[^}]*\}/)?.[0] || '';
    assert.equal(themeLightTokenBlock.includes('--pane-tab-strip-hover-bg'), false, 'light theme does not define the dark-only pane tab-container hover token');
    assert.ok(/body\.theme-light\s*\{[\s\S]*--active-accent-dim:\s*#e1edda/.test(preferencesCss), 'light theme uses a greenish-light pane tab-strip background (active-accent-dim)');
    assert.ok(/--pane-tab-unfocused-active-bg:\s*var\(--pane-tab-active-bg\)/.test(preferencesCss), 'unfocused active tabs use the SAME full green as the focused active tab (+ images 003/004: undimmed, un-lightened per-pane highlight)');
    assert.equal(preferencesCss.includes('--pane-tab-unfocused-active-bg: #aeb7c4'), false, 'gray unfocused-active pane tabs must not return');
    assert.ok(preferencesCss.includes('--pane-tab-panel-ring-width: 4px'), 'the pane ring uses the 4px width token');
    assert.ok(preferencesCss.includes('--pane-ring-opacity: 75%'), 'the pane ring default opacity is prominent enough in both themes');
    assert.ok(preferencesCss.includes('--pane-active-ring-opacity: 75%'), 'the active pane ring shares the default ring opacity token');
    // Light mode uses a BLUE pane separator (dark mode keeps amber/yellow).
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-resizer-bg:\s*rgba\(37, 99, 235/.test(preferencesCss), 'light mode uses a blue pane separator');
    // Pane chrome bars (strip, Info Bar, editor toolbar, find) all read the shared --pane-bar-bg, which is
    // the bright tab-strip green when the pane is focused and neutral gray when not. Focus sets it on .panel.
    assert.ok(/\.panel\.active-pane,\s*\.panel\.typing-ready-pane\s*\{[^}]*--pane-bar-bg:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'focused panes set --pane-bar-bg to the bright tab-strip green');
    assert.equal(/\.panel\.active-pane > \.panel-head,\s*\.panel\.typing-ready-pane > \.panel-head/.test(preferencesCss), false, 'focused pane tab containers use the shared .panel-head background rule, not a separate state override');
    assert.equal(/\.panel\.changes-panel/.test(preferencesCss), false, 'standalone Changes pane chrome CSS is removed');
    assert.ok(/\.panel\.file-explorer-panel > \.file-explorer-head:hover,\s*\.panel\.file-explorer-panel > \.file-explorer-head:focus-within,\s*\.panel\.file-explorer-panel:has\(\.file-explorer-tree-panel:hover\) > \.file-explorer-head,\s*\.panel\.file-explorer-panel:has\(\.file-explorer-tree-panel:focus-within\) > \.file-explorer-head\s*\{[^}]*--pane-bar-bg:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'Finder hover/focus colors only the Finder header');
    assert.ok(/\.panel-head\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'the tab strip reads the shared --pane-bar-bg');
    assert.ok(/\.panel-detail-row\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'the Info Bar reads the shared --pane-bar-bg (gray when unfocused, not green)');
    assert.ok(/\.file-explorer-head\s*\{[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Finder header reads the shared pane bar background when focused/hovered');
    assert.ok(/\.file-explorer-changes-panel:hover,\s*\.file-explorer-changes-panel:focus-within\s*\{[^}]*--pane-bar-bg:\s*var\(--pane-tab-strip-bg\)/.test(preferencesCss), 'embedded Finder Modified-files section uses the green pane bar on hover/focus');
    assert.ok(/\.file-explorer-changes-panel\s*\{[^}]*--pane-bar-bg:\s*var\(--panel2\)/.test(preferencesCss), 'embedded Finder Modified-files header stays neutral unless its own section is hovered/focused');
    assert.ok(/\.file-explorer-changes-head\s*\{[\s\S]*background:\s*var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Finder Modified-files header reads the shared pane bar background when focused/hovered');
    assert.ok(/\.file-explorer-changes-panel\s*\{[^}]*isolation:\s*isolate/.test(preferencesCss), 'Finder Modified-files section isolates its sticky header/content layers');
    assert.ok(/\.file-explorer-changes-panel\s*\{[\s\S]*padding:\s*0 5px 5px/.test(preferencesCss), 'Finder Modified-files panel has no top padding before its header');
    assert.ok(/\.file-explorer-changes-head\s*\{[\s\S]*z-index:\s*var\(--z-sticky-pane-head\)[\s\S]*box-shadow:\s*0 2px 0 var\(--pane-bar-bg,\s*var\(--panel2\)\)/.test(preferencesCss), 'Finder Modified-files sticky header covers content below without adding a top band');
    assert.ok(/\.diff-ref-suggestion-popover\s*\{[\s\S]*max-height:\s*min\(320px,\s*42vh\)/.test(preferencesCss), 'diff-ref suggestions use a compact custom popup, not the browser-native datalist');
    assert.ok(/\.diff-ref-suggestion-option\s*\{[\s\S]*height:\s*24px/.test(preferencesCss), 'diff-ref popup rows are compact one-line options');
    assert.ok(/\.diff-ref-suggestion-option\s*\{[\s\S]*grid-template-columns:\s*minmax\(18ch,\s*32ch\)\s*minmax\(0,\s*1fr\)\s*16ch\s*minmax\(8ch,\s*18ch\)/.test(preferencesCss), 'diff-ref popup aligns ref, subject, date, and author as separate columns');
    assert.ok(changedFilesSource.includes('diff-ref-suggestion-date') && changedFilesSource.includes('diff-ref-suggestion-author'), 'diff-ref popup renders date and author in separate cells so both columns line up');
    assert.ok(/const minWidth = Math\.min\(compact \? 880 : 960, viewportWidth - 16\)/.test(changedFilesSource), 'diff-ref popup reserves enough width for normal 80-character commit subjects');
    assert.ok(/const maxWidth = compact \? 1040 : 1120/.test(changedFilesSource), 'diff-ref popup width is capped for the viewport without truncating normal subjects');
    assert.ok(/\.server-update-banner-reload\s*\{[\s\S]*background:\s*var\(--danger-strong\)[\s\S]*color:\s*#ffffff/.test(preferencesCss), 'server update Reload button uses the danger token in dark mode');
    assert.ok(/body\.theme-light \.server-update-banner-reload\s*\{[\s\S]*background:\s*var\(--danger-strong\)[\s\S]*color:\s*#ffffff/.test(preferencesCss), 'server update Reload button uses the danger token in light mode');
    assert.equal(/\.panel\.active-pane \.panel-head\s*\{[\s\S]*background:\s*var\(--pane-tab-panel-head-bg\)/.test(preferencesCss), false, 'focused panes do not recolor the tab strip green');
    assert.ok(preferencesCss.includes('.panel:not(.active-pane):not(.file-explorer-panel) .pane-tab.active'), 'non-focused panes dim their active tab without touching Finder panes');
    assert.ok(preferencesCss.includes('--pane-split-gap: 0px'), 'pane split layout collapses gap through a shared token');
    assert.ok(preferencesCss.includes('--pane-resizer-size: 1px'), 'pane splitter reserves only the 1px separator line');
    assert.ok(preferencesCss.includes('--pane-resizer-bg: rgba(255, 225, 77, 0.72)'), 'dark pane splitter is a visible bright-yellow divider at rest');
    assert.ok(preferencesCss.includes('--pane-resizer-hover-bg: rgba(255, 225, 77, 0.96)'), 'dark pane splitter turns brighter on hover/resize');
    assert.ok(preferencesCss.includes('--pane-resizer-bg: rgba(37, 99, 235, 0.72)'), 'light pane splitter is blue at rest');
    assert.ok(preferencesCss.includes('--pane-resizer-hover-line-size: 5px'), 'pane splitter hover thickens to a clearly visible 5px line over the 1px resting line');
    assert.ok(preferencesCss.includes('--pane-tile-radius: 0'), 'adjacent panes meet flush with square corners (no rounded-corner seam wedges)');
    // #29 + the nav (←/→) and search are centered as a PAIR — the nav absorbs the left free
    // space (margin-inline-start:auto) and the search absorbs the right (margin-inline: 6px auto), so the
    // cluster sits centered between the menubar and the right-side actions, not right-aligned.
    assert.ok(/\.topbar-nav\s*\{[^}]*margin-inline-start:\s*auto/.test(preferencesCss), '#29: the topbar nav group absorbs the left free space so the nav+search pair centers');
    assert.ok(/\.topbar-search\s*\{[^}]*margin-inline:\s*6px auto/.test(preferencesCss), '#29: topbar universal search is centered (auto inline-end) between the menubar and the right-side actions, not right-aligned');
    assert.ok(/\.resizer-row::after\s*\{[^}]*inset-inline: -5px/.test(preferencesCss), '#34: the resizer has a wide invisible grab zone (~5px past the line) so it is easy to grab');
    assert.equal(/\.panel \{[^}]*border: 1px solid var\(--line\)/.test(preferencesCss), false, '#35: panes drop the per-pane border so the only divider is the 1px separator');
    // The active/focus outline is the pane's "natural border" (a --pane-split-gap-wide real border, never
    // clipped, flush to the resizer) colored green — the SAME mechanism for every pane type. Every pane
    // has the transparent border; the active one colors it. No box-shadow, no inset ::after for focus.
    // the state ring IS the --pane-split-gap gutter border, colored per state via the unified
    // --panel-ring-color and faded by --pane-ring-opacity (transparent on a plain inactive pane). Sitting at
    // the seam edge, an active (green) pane and a needs (red) pane touch across the 1px resizer.
    assert.ok(/\.panel\s*\{[^}]*border:\s*var\(--pane-split-gap\) solid color-mix\(in srgb, var\(--panel-ring-color\) var\(--panel-ring-opacity,\s*var\(--pane-ring-opacity/.test(preferencesCss), 'the pane ring is the --pane-split-gap gutter border, colored by --panel-ring-color at per-state opacity (touches the neighbor at the seam)');
    // every focused pane (active or typing-ready) drives the SAME green ring via --panel-ring-color.
    assert.ok(/\.panel\.active-pane\s*\{[^}]*--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(preferencesCss), 'the active pane sets the green ring color');
    assert.ok(/\.panel\.active-pane\s*\{[^}]*--panel-ring-opacity:\s*var\(--pane-active-ring-opacity\)/.test(preferencesCss), 'the active pane uses the user-controlled active ring opacity');
    assert.ok(/\.panel\.typing-ready-pane\s*\{[^}]*--panel-ring-color:\s*var\(--pane-tab-panel-ring\)/.test(preferencesCss), 'a typing-ready pane sets the SAME green ring color as active');
    assert.ok(/\.panel\.typing-ready-pane\s*\{[^}]*--panel-ring-opacity:\s*var\(--pane-active-ring-opacity\)/.test(preferencesCss), 'typing-ready panes use the same user-controlled active ring opacity as active panes');
    assert.equal(/\.panel\.typing-ready-pane\s*\{[^}]*border-color:\s*#465267/.test(preferencesCss), false, 'no gray focus border — focused panes are green, not the old typing-ready gray');
    assert.equal(/body\.theme-light \.panel\.typing-ready-pane\s*\{[^}]*border-color:\s*#9aa6b6/.test(preferencesCss), false, 'no LIGHT-mode gray focus border on terminals (the #465267 twin) — focused terminals stay green in light mode too');
    // no content-edge overlay ring — the ring is the gutter border (asserted above). The
    // needs-* PULSE uses the shared .attention-pulse parent on the panel (not a ::after).
    assert.equal(/\.panel::after\s*\{/.test(preferencesCss), false, 'no .panel::after overlay ring — the ring is the gutter border, so adjacent rings touch at the seam');
    assert.ok(/\.panel\.needs-input-pane,[\s\S]*?\{[^}]*--panel-ring-color:\s*var\(--pane-ring-attention\)/.test(preferencesCss), 'needs-* panes set the red ring color (via the --pane-ring-attention token)');
    assert.ok(/\.attention-pulse\s*\{[^}]*animation-name:\s*attention-ring-fade/.test(preferencesCss), 'the shared attention pulse parent drives the red ring animation');
    assert.ok(/syncAttentionAnimation\(node, active\)[\s\S]*?classList\?\.toggle\?\.\('attention-pulse'/.test(preferencesJs), 'needs-* panes/tabs receive the shared attention-pulse class from syncAttentionAnimation');
    // image 028: a focused needs-attention pane resolves RED (attention beats the yolo-ready green tint).
    assert.ok(/\.panel\.active-pane\.needs-input-pane[\s\S]*?\.panel\.typing-ready-pane\.needs-blocked-pane\s*\{[^}]*--panel-ring-color:\s*var\(--pane-ring-attention\)/.test(preferencesCss), 'a focused needs-attention pane resolves the red ring color, not the green/yolo tint');
    assert.ok(/--pane-ring-attention:\s*#ff3347/.test(preferencesCss), 'the attention-ring token keeps the #ff3347 dark value');
    assert.equal(/\.panel\.active-pane\s*\{[^}]*border-color:/.test(preferencesCss), false, 'the active pane sets --panel-ring-color (which colors the shared gutter border), not its own border-color');
    // images 003/004 pane-color polish:
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-inactive-tab-bg:\s*var\(--active-tab-muted-bg\)/.test(preferencesCss), 'light-mode inactive tabs derive their tint from the active accent (not a fixed green)');
    // image 008: inactive-tab text is DARK in light mode (readable on the light-green tabs/strip), not the
    // dark-tuned near-white #dfe6ef that made it white-on-white.
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--pane-tab-text:\s*#1f2937/.test(preferencesCss), 'light-mode tab text is dark (no white-on-white inactive tabs)');
    // The name/dir/detail spans hardcode near-white (for dark tabs); light mode overrides them dark too,
    // or the branch/path text stays white-on-white even with a dark base tab color.
    assert.ok(/body\.theme-light \.pane-tab:not\(\.active\) \.session-button-name,[\s\S]*?\.session-button-detail\s*\{[^}]*color:\s*var\(--pc-control-fg\)/.test(preferencesCss), 'light-mode inactive-tab name/dir/detail text is dark via the shared control foreground token');
    // An inactive pane's inactive tabs follow the gray bar (--pane-bar-bg, which is --panel2 when unfocused);
    // the bars themselves go gray via --pane-bar-bg (asserted above), so only the tabs need this rule.
    assert.ok(/\.panel:not\(\.active-pane\):not\(\.typing-ready-pane\) \.pane-tab:not\(\.active\)\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'an inactive pane\'s inactive tabs follow the gray bar (--pane-bar-bg)');
    assert.ok(/\.panel\.active-pane \.pane-tab:not\(\.active\),\s*\.panel\.typing-ready-pane \.pane-tab:not\(\.active\)\s*\{[^}]*background:\s*var\(--pane-bar-bg\)/.test(preferencesCss), 'an active pane\'s inactive tabs match the bright tab-strip bar (--pane-bar-bg)');
    assert.ok(/--pane-tab-unfocused-active-bg:\s*var\(--pane-tab-active-bg\)/.test(preferencesCss), "an inactive pane's active tab is full green (unfocused-active aliases the focused token)");
    assert.equal(/--pane-tab-unfocused-active-bg:\s*#d2ecc2/.test(preferencesCss), false, 'no lightened light-mode unfocused-active green remains');
    assert.ok(/--inactive-pane-opacity-scale:\s*1/.test(preferencesCss), 'inactive-pane dim defaults to full strength');
    assert.ok(/--inactive-pane-overlay-alpha:\s*0\.18/.test(preferencesCss), 'inactive-pane dim base alpha stays 0.18 in dark mode');
    assert.ok(/body\.theme-light\s*\{[\s\S]*?--inactive-pane-overlay-alpha:\s*0\.09/.test(preferencesCss), 'inactive-pane dim base alpha stays 0.09 in light mode');
    assert.ok(/--inactive-pane-overlay:\s*rgb\(var\(--inactive-pane-overlay-rgb\) \/ calc\(var\(--inactive-pane-overlay-alpha\) \* var\(--inactive-pane-opacity-scale\)\)\)/.test(preferencesCss), 'inactive-pane flat dim is scaled by the opacity slider');
    assert.equal(/inactive-pane-gradient/.test(preferencesCss), false, 'inactive-pane gradient CSS is removed until the feature is revisited');
    {
      // #261: a 0-20px pane spacing setting drives the inter-pane gap; the active pane's green box width
      // == that gap (--pane-split-gap), so it's 0 at spacing 0 and fills the active side up to the line.
      const paneSpacingSrc = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(paneSpacingSrc.includes("numberSetting('appearance.pane_spacing', 3)"), 'runtime reads appearance.pane_spacing with a 3px fallback (matches the backend default)');
      assert.ok(paneSpacingSrc.includes("setProperty('--pane-split-gap'"), '#261: pane spacing drives the --pane-split-gap inter-pane gap');
      assert.equal(paneSpacingSrc.includes('paneSpacing / 5'), false, '#261: the active green box width is NOT a separate scaled value — it uses --pane-split-gap directly');
      assert.ok(/path: 'appearance\.pane_spacing'[\s\S]{0,90}min: 0, max: 20/.test(paneSpacingSrc), '#261: Preferences exposes a 0-20px pane spacing field');
      assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['pref.appearance.pane_spacing.label'], 'Pane spacing', '#261: the pane spacing field has a localized label');
      // The terminal "follow" theme option reads "Follow global color theme" (NOT "app theme"), matching
      // the "Global color theme" setting it follows; the help references the global color theme too.
      const enT = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
      assert.equal(enT['pref.appearance.terminal_theme.follow-app'], 'Follow global color theme', 'terminal follow option reads "Follow global color theme"');
      assert.ok(enT['pref.appearance.terminal_theme.help'].includes('global color theme'), 'terminal theme help references the global color theme');
    }
    assert.ok(/\.grid\.drop-preview-root\.drop-preview-top::before,[^{]*\{[^}]*var\(--drop-preview-width/.test(preferencesCss), '#36: the root top/bottom drop preview spans only the non-Finder content width (never covers the docked Finder)');
    assert.ok(/\.layout-column\s*\{[\s\S]*gap:\s*var\(--pane-split-gap\)/.test(preferencesCss), 'pane split layout reads the compact gap token');
    // #261: the REAL inter-pane gap is the flex split container (the column grid gap is a no-op for a
    // single-panel column), so appearance.pane_spacing now actually changes the gap, not just the ring.
    assert.ok(/\.layout-split\s*\{[\s\S]*?gap:\s*0;/.test(preferencesCss), '#261: the flex split container has no gap — pane spacing is the pane border width instead');
    // image 046: the terminal has no horizontal padding, so its dark box meets the resizer flush (the old
    // 2px left/right padding showed a dark sliver between the terminal and the yellow seam). image 034:
    // the former `padding-top: 2px` gap was also removed so the terminal uses the full pane → `padding: 0`.
    assert.ok(/\.terminal\s*\{[^}]*padding:\s*0;/.test(preferencesCss), 'terminal box is flush to the pane edge (padding:0 — no horizontal sliver and no top gap)');
    // image 034: xterm.css defaults `.xterm-viewport` to background #000, which showed as a black line at
    // the top of the LIGHT terminal (the strip the canvas rows don't cover). Force it transparent so the
    // .terminal container's theme background shows through.
    assert.ok(/\.terminal \.xterm:not\(\.allow-transparency\) \.xterm-viewport\s*\{[^}]*background-color:\s*transparent/.test(preferencesCss), 'xterm viewport is transparent (no black #000 line at the top of the terminal)');
    assert.ok(/\.layout-resizer\s*\{[\s\S]*flex:\s*0 0 var\(--pane-resizer-size\)/.test(preferencesCss), 'pane splitters read the compact size token');
    assert.ok(/\.resizer-row::before\s*\{[\s\S]*left:\s*calc\(50% - \(var\(--pane-resizer-line-size\) \/ 2\)\)/.test(preferencesCss), 'row splitters draw a tokenized centered visible line');
    assert.ok(/\.resizer-row:hover::before,[\s\S]*var\(--pane-resizer-hover-line-size\)/.test(preferencesCss), 'row splitters widen on hover without increasing the resting seam');
    assert.ok(/\.layout-resizer:hover::before,[\s\S]*background:\s*transparent/.test(preferencesCss), 'resizer hover does not return to a solid fill');
    assert.ok(/\.resizer-row:hover::before,[^{]*\{[^}]*background: var\(--pane-resizer-hover-bg\)/.test(preferencesCss), '#27: row splitter hover is a solid straight yellow bar (no dashed gradient)');
    assert.ok(/\.resizer-column:hover::before,[^{]*\{[^}]*background: var\(--pane-resizer-hover-bg\)/.test(preferencesCss), '#27: column splitter hover is a solid straight yellow bar (no dashed gradient)');
    assert.ok(preferencesCss.includes('--pc-control-fg: #1f2937'), 'light mode uses a dark foreground for pane action controls');
    assert.ok(preferencesCss.includes('--pane-tab-panel-head-text: #1f2937'), 'light mode uses dark status text');
    assert.ok(/\.tabs \.pane-actions,\s*\n\.tabs \.panel-tab-overflow\s*\{[\s\S]*color:\s*var\(--pc-control-fg\)/.test(preferencesCss), 'pane actions use the shared platform-control foreground');
    assert.ok(/\.meta-path\s*\{[\s\S]*color:\s*var\(--pane-meta-path\)/.test(preferencesCss), 'status path color is theme-tokenized');
    assert.ok(/body\.editor-theme-light\s*\{[\s\S]*--drop-outline:\s*var\(--drop-outline-light\)/.test(preferencesCss), 'light editor panes switch drop-target outlines to readable blue');
    assert.ok(/\.file-editor-popout-preview-panel,[\s\S]*?\.file-editor-save-panel\s*\{[^}]*height:\s*20px/.test(preferencesCss), 'pop-out preview button shares the compact editor toolbar button sizing rule');
    assert.ok(/\.file-editor-panel-actions\s*\{[\s\S]*background:\s*color-mix\(in srgb, var\(--panel2\)/.test(preferencesCss), 'editor actions render as one compact gray toolbar');
    assert.ok(/\.file-editor-gutter-panel,\s*\n\.file-editor-wrap-panel,\s*\n\.file-editor-find-panel,\s*\n\.file-editor-diff-panel/.test(preferencesCss), 'diff button shares the compact editor toolbar sizing');
    assert.ok(preferencesCss.includes('--code-diff-add: #56d364'), 'dark diff add base is a brighter vivid green');
    assert.ok(preferencesCss.includes('--code-diff-remove: #ff7b72'), 'dark diff remove base is a brighter vivid red');
    assert.equal(preferencesCss.includes('--code-diff-add: #98c379'), false, 'muted one-dark diff green must not return as the YOLOmux dark default');
    assert.equal(preferencesCss.includes('--code-diff-remove: #e06c75'), false, 'muted one-dark diff red must not return as the YOLOmux dark default');
    assert.ok(preferencesCss.includes('--diff-remove-line-bg: #540c06'), '#250: dark diff removed-line fill matches the sampled deep maroon guide');
    assert.ok(preferencesCss.includes('--diff-add-line-bg: #2b5d16'), '#250: dark diff added-line fill matches the sampled deep green guide');
    assert.ok(preferencesCss.includes('.file-editor-icon-popout-preview'), 'preview pop-out has a distinct icon');
    assert.equal(preferencesCss.includes('preview-linked'), false, 'old paired side-preview ring styling is removed');
    assert.ok(/\.yoagent-global\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent global summary fits narrow panes');
    assert.ok(/\.yoagent-list\s*\{[\s\S]*display:\s*flex[\s\S]*flex-direction:\s*column/.test(preferencesCss), 'YO!agent content column can push the chat section to the bottom');
    assert.ok(/\.yoagent-list\s*\{[\s\S]*overflow-x:\s*auto[\s\S]*overflow-y:\s*hidden/.test(preferencesCss), 'YO!agent outer list keeps horizontal overflow but does not expose a competing vertical scrollbar');
    assert.ok(/\.yoagent-chat\s*\{[\s\S]*min-width:\s*0/.test(preferencesCss), 'YO!agent chat fits narrow panes');
    assert.ok(/\.yoagent-chat\s*\{[\s\S]*margin-top:\s*auto/.test(preferencesCss), 'YO!agent chat stays at the bottom of the summary view when there is spare height');
    assert.ok(preferencesCss.includes('--terminal-font-size: 13px'), 'terminal font size is exposed as a shared CSS variable');
    assert.ok(/\.yoagent-list\s*\{[\s\S]*font-size:\s*var\(--terminal-font-size\)/.test(preferencesCss), 'YO!agent uses the terminal font-size setting');
    assert.ok(/\.yoagent-chat\s*\{[\s\S]*grid-template-rows:\s*auto minmax\(0, 1fr\) auto/.test(preferencesCss), 'YO!agent transcript row stays compact while history owns spare height, including the Recent agents response');
    assert.ok(/\.yoagent-chat\.has-history\s*\{[\s\S]*flex:\s*1 1 auto/.test(preferencesCss), 'populated YO!agent chat fills the pane so the composer stays pinned below the single history scroller');
    assert.ok(/\.yoagent-global\s*\{[\s\S]*border-inline-start:\s*3px solid var\(--active-accent-bright\)/.test(preferencesCss), 'YO!agent global summary accent follows the active theme color');
    assert.equal(/\.yoagent-(?:global|refresh|session|chat|message|backend)[\s\S]{0,260}var\(--nv-green\)/.test(preferencesCss), false, 'YO!agent summary/chat accents do not hardcode the green theme token');
    assert.ok(/\.yoagent-chat\.empty\s*\{[\s\S]*grid-template-rows:\s*auto auto auto/.test(preferencesCss), 'empty YO!agent chat does not stretch an empty history row');
    assert.ok(preferencesCss.includes('body.editor-cursor-block .file-editor-codemirror .cm-cursor'), 'block cursor styling is available for CodeMirror');
    assert.ok(/\.preferences-setting-control\s*\{[^}]*--preferences-control-left-indent:\s*14px/.test(preferencesCss), 'Preferences controls share the 14px left inset');
    assert.ok(/\.preferences-setting-control\s*\{[^}]*--preferences-number-control-width:\s*11ch/.test(preferencesCss), 'Preferences number controls reserve room for the native spinner');
    assert.ok(/\.preferences-radio-group\s*\{[^}]*justify-content:\s*start[^}]*padding-inline-start:\s*var\(--preferences-control-left-indent\)/.test(preferencesCss), 'theme radio choices start from the left inset');
    assert.ok(/\.preferences-setting-control\.setting-type-text input\[type="text"\],\s*\n\.preferences-setting-control\.setting-type-select select\s*\{[^}]*justify-self:\s*start[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(preferencesCss), 'select/text controls use the same left inset');
    assert.ok(/\.preferences-setting-control\.setting-type-number\s*\{[^}]*grid-template-columns:\s*calc\(var\(--preferences-control-left-indent\) \+ var\(--preferences-number-control-width\)\) auto minmax\(0, 1fr\) auto/.test(preferencesCss), 'number rows use the shared left inset with a flexible spacer before Reset');
    assert.ok(/\.preferences-setting-control\.setting-type-number input\[type="number"\]\s*\{[^}]*width:\s*var\(--preferences-number-control-width\)[^}]*justify-self:\s*start[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)[^}]*padding-inline-end:\s*18px/.test(preferencesCss), 'number inputs start at the same left inset and leave room for the spinner');
    assert.ok(/\.preferences-setting-control\.setting-type-list textarea,\s*\n\.preferences-setting-control\.setting-type-textarea textarea\s*\{[^}]*margin-inline-start:\s*var\(--preferences-control-left-indent\)/.test(preferencesCss), 'list/textarea controls use the same left inset');
    assert.ok(preferencesCss.includes('.file-editor-dialog-backdrop'), 'editor conflict and close decisions use the shared editor dialog');
    assert.equal(preferencesCss.includes('.app-menu-search-input'), false, 'Tabs menu no longer renders a sticky search input');
    assert.ok(preferencesCss.includes('.command-palette-detail .fuzzy-match'), 'command palette highlights fuzzy matches in detail text');
    assert.ok(preferencesCss.includes('.file-editor-diff-codemirror .cm-deletedChunk .cm-chunkButtons'), 'diff merge controls are positioned in the chunk margin');
    assert.ok(preferencesCss.includes('inset-inline-end: 8px !important'), 'diff merge controls sit on the right edge');
    assert.ok(/\.file-editor-diff-codemirror \.cm-merge-revert\s*\{[^}]*display:\s*none/.test(preferencesCss), 'diff view hides the left-side merge/revert strip completely');
    assert.ok(/\.file-editor-diff-codemirror \.cm-diff-overview\s*\{[^}]*inset-block:\s*0[\s\S]*inset-inline-end:\s*14px[\s\S]*width:\s*4px[\s\S]*pointer-events:\s*none/.test(preferencesCss), 'diff overview ruler matches the scrollbar track height, sits left of the native scrollbar hit area, and does not intercept scrollbar input');
    assert.ok(/\.file-editor-diff-codemirror \.cm-diff-overview-viewport\s*\{[^}]*position:\s*absolute[\s\S]*border:\s*1px solid rgba\(226,\s*238,\s*248,\s*0\.18\)[\s\S]*background:\s*rgba\(226,\s*238,\s*248,\s*0\.045\)/.test(preferencesCss), 'diff overview shows a faint neutral viewport indicator over the red/green gradient');
    assert.equal(preferencesCss.includes('.cm-diff-overview-tick'), false, 'diff overview has no tick CSS because it paints one gradient on the rail');
    assert.equal(preferencesCss.includes('.cm-diff-overview-tick.split-lane'), false, 'diff overview uses one full-width color per rendered row band, not split lanes');
    assert.ok(/\.file-editor-diff-codemirror \.cm-changedLineGutter,\s*\n\.file-editor-diff-codemirror \.cm-deletedLineGutter\s*\{[^}]*color:\s*inherit[\s\S]*background:\s*transparent/.test(preferencesCss), 'diff left gutter stays neutral; only changed content rows and the right overview rail carry red/green diff color');
    assert.equal(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-(?:changed|deleted)LineGutter\s*\{[^}]*background:\s*#[0-9a-fA-F]+/.test(preferencesCss), false, 'light theme must not reintroduce red/green left-gutter diff indicators');
    assert.ok(/\.file-editor-codemirror \.cm-scroller,\s*\n\.file-editor-codemirror-panel \.cm-scroller\s*\{[^}]*scrollbar-gutter:\s*stable[\s\S]*--pane-scrollbar-size:\s*12px/.test(preferencesCss), 'CodeMirror keeps a stable, draggable native scrollbar gutter while using the shared pane scrollbar behavior');
    assert.ok(/\.panel:is\(\.focused-pane,\s*\.active-pane\):hover\s*\{[^}]*--pane-scrollbar-current-thumb:\s*var\(--pane-scrollbar-thumb-active\)[\s\S]*--pane-scrollbar-current-track:\s*var\(--pane-scrollbar-track-active\)/.test(preferencesCss), 'only focused/active pane hover flips the inherited shared scrollbar variables to the configured cursor-color hover token');
    assert.ok(preferencesCss.includes('Pane scrollbars must always inherit a shared pane scrollbar contract'), 'pane CSS documents that scrollbars must inherit the shared rule');
    assert.ok(preferencesCss.includes("YO!agent's normal vertical owner is .yoagent-chat-history"), 'pane CSS documents the YO!agent single vertical scroll owner');
    for (const selector of [
      '.preferences-scroll',
      '.terminal .xterm-viewport',
      '.transcript-preview',
      '.summary-preview',
      '.event-list',
      '.info-list',
      '.yoagent-chat-history',
      '.yoagent-chat .markdown-body pre',
      '.file-explorer-tree-panel',
      '.file-explorer-changes-panel',
      '.file-editor-raw-panel',
      '.file-editor-preview-pane',
      '.file-editor-preview-pane-panel',
      '.file-editor-image-panel',
      '.file-editor-dialog-body',
      '.file-editor-diff-codemirror .cm-mergeView',
      '.file-editor-codemirror .cm-scroller',
      '.file-editor-codemirror-panel .cm-scroller',
    ]) {
      assert.ok(preferencesCss.includes(selector), `shared pane scrollbar selector includes ${selector}`);
    }
    assert.ok(preferencesCss.includes('scrollbar-color: var(--pane-scrollbar-current-thumb, var(--pane-scrollbar-thumb)) var(--pane-scrollbar-current-track, var(--pane-scrollbar-track));'), 'shared scrollbar rule defaults to neutral gray and inherits configured cursor hover colors only from focused/active pane hover variables');
    assert.ok(/:where\([\s\S]*\)::\-webkit-scrollbar-thumb\s*\{[^}]*background:\s*var\(--pane-scrollbar-current-thumb,\s*var\(--pane-scrollbar-thumb\)\)/.test(preferencesCss), 'shared WebKit thumb uses the inherited current pane thumb token');
    assert.equal(/\.file-editor-panel:hover \.file-editor-codemirror-panel \.cm-scroller[\s\S]*?\{[\s\S]*?scrollbar-color:/.test(preferencesCss), false, 'editor pane no longer has local hover scrollbar coloring');
    assert.equal(/\.file-explorer-tree-panel:hover,[\s\S]*?\.file-explorer-tree-panel:focus-within\s*\{[\s\S]*?scrollbar-color:/.test(preferencesCss), false, 'Finder tree no longer has local hover scrollbar coloring');
    assert.equal(/\.file-explorer-changes-panel:hover,[\s\S]*?\.file-explorer-changes-panel:focus-within\s*\{[\s\S]*scrollbar-color:/.test(preferencesCss), false, 'Modified-files no longer has local hover scrollbar coloring');
    assert.equal(/\.panel\.changes-panel|\.changes-scroll/.test(preferencesCss), false, 'standalone Differ scrollbar CSS is removed');
    assert.equal(/\.panel:hover \.terminal \.xterm-viewport[\s\S]*?\{[\s\S]*?scrollbar-color:/.test(preferencesCss), false, 'terminal no longer has a local hover scrollbar color rule');
    assert.ok(/--diff-add-line-bg:\s*#2b5d16/.test(preferencesCss), '#250: diff added lines use the sampled opaque green fill over the dark bg');
    assert.ok(/\.file-editor-diff-codemirror \.cm-content \.cm-activeLine\s*\{[^}]*background:\s*var\(--diff-full-line-bg,\s*transparent\)/.test(preferencesCss), 'diff active lines keep the same red/green fill as their neighboring changed lines');
    assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror\s*\{[\s\S]*--diff-add-line-bg:\s*#bfeac8/.test(preferencesCss), 'light diff added lines use a more visible green fill');
    assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror\s*\{[\s\S]*--diff-remove-line-bg:\s*#f3b7b7/.test(preferencesCss), 'light diff removed lines use a more visible red fill');
    assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-merge-a \.cm-changedLine,[\s\S]*?\.cm-deletedLine\s*\{[\s\S]*color:\s*#3b0a0a/.test(preferencesCss), 'light diff removed lines force dark red text on the stronger red fill');
    assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-merge-a \.cm-changedText,[\s\S]*?\.cm-deletedChunk \.cm-deletedText\s*\{[\s\S]*color:\s*var\(--danger-light-text\)[\s\S]*background:\s*#f4b7b7/.test(preferencesCss), 'light diff removed inline text uses the danger text token on a distinct red fill (image 055)');
    assert.ok(/body\.editor-theme-light \.file-editor-diff-codemirror \.cm-merge-b \.cm-changedText\s*\{[\s\S]*color:\s*var\(--success-text-strong\)[\s\S]*background:\s*#b9e7c2/.test(preferencesCss), 'light diff added inline text uses dark green on a distinct green fill');
    assert.ok(/--diff-remove-line-bg:\s*#540c06/.test(preferencesCss), '#250: diff removed lines use the sampled opaque red fill over the dark bg');
    // a .panel-overlay-root must NOT be the scroll container (else the inactive-pane dim
    // scrolls away). The overlay-root bodies are overflow:hidden; the scrolling lives on inner wrappers.
    assert.ok(/\.preferences-body\s*\{[^}]*overflow:\s*hidden/.test(preferencesCss), 'C3: .preferences-body (overlay-root) must not scroll (overflow:hidden)');
    assert.ok(!/\.preferences-body\s*\{[^}]*overflow:\s*auto/.test(preferencesCss), 'C3: .preferences-body must NOT be overflow:auto');
    assert.ok(/\.preferences-scroll\s*\{[^}]*overflow:\s*auto/.test(preferencesCss), 'C3: .preferences-scroll is the scroll container');
    assert.equal(/\.preferences-scroll\s*\{[^}]*scrollbar-color:/.test(preferencesCss), false, 'Preferences does not customize scrollbar colors locally');
    assert.equal(/\.preferences-scroll::\-webkit-scrollbar-thumb/.test(preferencesCss), false, 'Preferences does not customize WebKit scrollbar thumbs locally');
    assert.equal(/\.changes-body|\.changes-scroll/.test(preferencesCss), false, 'standalone Differ overlay/scroll containers are removed');
    assert.ok(/body\.theme-light \.app-menu-ui-icon\.active\s*\{[\s\S]*background:\s*var\(--active-control-bg\)/.test(preferencesCss), '#251: light mode gives the active app-menu icon button a light-tuned active-control fill (no dark square)');
    assert.ok(/body\.theme-light \.app-menu-tab-command[\s\S]*\{[\s\S]*color:\s*var\(--text\)/.test(preferencesCss), '#252: light mode forces dark text on the rich Tabs/Changes dropdown rows so they are not washed out');
    assert.ok(/body\.theme-light \.file-explorer-changes-panel \.changes-comparison-head\s*\{[\s\S]*background:\s*transparent/.test(preferencesCss), '#253: the Finder "Comparing…" caption has no box chrome in light mode (blends as text)');
    assert.equal(/\.cm-deletedLineGutter\s*\{[^}]*color:\s*transparent/.test(preferencesCss), false, 'deleted rows carry no number via unified-merge read-only widgets, not a transparent-text gutter hack');
    assert.ok(preferencesCss.includes('clip-path: inset(0 -100vw)'), 'diff line backgrounds extend to the full editor width');
    // Wrapped-line diff bug: the full-bleed box-shadow/clip-path trick is ONLY for BLOCK line elements.
    // In @codemirror/merge the inserted/deleted text are inline marks (<ins>/<del> with class
    // cm-insertedLine/cm-deletedLine). Applying clip-path to a soft-wrapped inline element let the parent
    // .cm-changedLine block band paint over the wrapped continuation rows, blanking their text. They must
    // reset box-shadow + clip-path so a long added/removed line that wraps shows text on every visual row.
    assert.ok(
      /\.file-editor-diff-codemirror \.cm-insertedLine,\s*\n\.file-editor-diff-codemirror \.cm-deletedLine\s*\{[^}]*box-shadow:\s*none[\s\S]*?clip-path:\s*none/.test(preferencesCss),
      'inline inserted/deleted diff marks reset box-shadow + clip-path so soft-wrapped continuation rows keep visible text',
    );
    const preferencesCssNoComments = preferencesCss.replace(/\/\*[\s\S]*?\*\//g, '');
    const fullBleedRule = /([^{};]*)\{\s*box-shadow:\s*-100vw 0 0 var\(--diff-full-line-bg\)[^}]*clip-path:\s*inset\(0 -100vw\)/.exec(preferencesCssNoComments);
    assert.ok(fullBleedRule, 'the full-bleed box-shadow/clip-path rule for block diff lines exists');
    assert.equal(/\.cm-insertedLine\b/.test(fullBleedRule[1]), false, 'the full-bleed box-shadow rule must not list the inline cm-insertedLine mark (it buries wrapped rows)');
    assert.equal(/\.cm-deletedLine\b/.test(fullBleedRule[1]), false, 'the full-bleed box-shadow rule must not list the inline cm-deletedLine mark (it buries wrapped rows)');
    // #44: diffs render as full-line red/green only (highlightChanges:false in both merge views). The old
    // YOLOmux intra-line token overlay stays gone; CodeMirror still emits its built-in changed/deleted
    // text spans, which we only style for light-theme contrast.
    const diffBundle = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.equal((diffBundle.match(/highlightChanges: false/g) || []).length, 2, '#44: both merge views disable intra-line change highlighting');
    assert.equal(diffBundle.includes('highlightChanges: true'), false, '#44: no merge view re-enables intra-line highlighting');
    assert.equal(preferencesCss.includes('cm-insertedText'), false, '#44: the dead intra-line token rules are removed');
    assert.equal(preferencesCss.includes('--diff-add-text-bg'), false, '#44: the unused intra-line text-bg token is removed');
    assert.ok(preferencesCss.includes('.file-tree-row.repo-non-main'), 'Finder repo rows have non-main branch styling');
    api.setClientSettingsPatchForTest({performance: {server_event_poll_ms: 850, server_background_file_event_poll_ms: 5000, server_directory_event_poll_ms: 3000, tabber_activity_refresh_ms: 15000, remote_resize_delay_ms: 220}, updates: {notify_level: 'patch'}});
    const preferencesHtml = api.preferencesPanelHtmlForTest('', []);
    assert.ok(preferencesHtml.indexOf('preferences-search-row') < preferencesHtml.indexOf('preferences-path-rows'), 'preferences search is first');
    assert.ok(preferencesHtml.includes('data-preferences-search-action>Search</button>'), 'preferences search has an explicit localized action');
    const globalPathRowsHtml = preferencesHtml.slice(preferencesHtml.indexOf('<div class="preferences-path-rows"'), preferencesHtml.indexOf('<div class="preferences-sections"'));
    assert.ok(/preferences-path-label">settings<\/span>[\s\S]*settings\.yaml[\s\S]*loaded/.test(globalPathRowsHtml), 'Preferences settings path row shows the loaded age inline');
    assert.equal(globalPathRowsHtml.includes('YOLO rules'), false, 'global Preferences path rows no longer show the YOLO rules path');
    assert.equal(preferencesHtml.includes('preferences-status'), false, 'Preferences does not render a separate loaded/status line');
    const yoloSectionStart = preferencesHtml.indexOf('data-preference-section="YOLO"');
    const yoloSectionEnd = preferencesHtml.indexOf('data-preference-section="', yoloSectionStart + 1);
    const yoloSectionHtml = preferencesHtml.slice(yoloSectionStart, yoloSectionEnd >= 0 ? yoloSectionEnd : undefined);
    assert.ok(yoloSectionHtml.includes('preferences-path-row preferences-path-row--section'), 'YOLO section contains the YOLO rules path row');
    assert.ok(yoloSectionHtml.includes('YOLO rules'), 'YOLO rules label is inside the YOLO section');
    assert.ok(/YOLO rules[\s\S]*data-copy-path=/.test(yoloSectionHtml), 'YOLO rules copy button is inside the YOLO section');
    assert.ok(yoloSectionHtml.includes('data-yolo-rule-open'), 'YOLO rule file open action remains inside the YOLO section');
    assert.ok(preferencesHtml.includes('preferences-global-reset'), 'preferences always expose Global reset so stale persisted settings can be rewritten');
    assert.ok(preferencesHtml.includes('data-preferences-reset-all'), 'preferences expose a global reset action even when values look default');
    api.setClientSettingsPatchForTest({general: {auto_focus: true}});
    const modifiedPreferencesHtml = api.preferencesPanelHtmlForTest('', []);
    assert.ok(modifiedPreferencesHtml.indexOf('preferences-global-reset') > modifiedPreferencesHtml.indexOf('preferences-sections'), 'preferences global reset is below the setting sections');
    assert.ok(modifiedPreferencesHtml.includes('Global reset'), 'preferences reset is labeled as global in normal-case text');
    assert.ok(modifiedPreferencesHtml.includes('resets every Preferences value'), 'preferences reset carries a broad warning');
    assert.ok(modifiedPreferencesHtml.includes('data-preferences-reset-all'), 'preferences expose a global reset action after a setting changes');
    assert.ok(/\.preferences-global-reset \.preferences-reset-all\s*\{[\s\S]*?color:\s*var\(--danger-action-text\)[\s\S]*?background:\s*var\(--danger-action-bg\)[\s\S]*?border-color:\s*var\(--danger-action-border\)[\s\S]*?font:\s*600 var\(--ui-font-size-sm\)\/1\.1 var\(--ui-font\)/.test(preferencesCss), 'preferences global reset button uses danger action tokens and normal UI text');
    assert.ok(/body\.theme-light \.preferences-global-reset \.preferences-reset-all\s*\{[\s\S]*?color:\s*var\(--danger-action-text\)[\s\S]*?background:\s*var\(--danger-strong\)[\s\S]*?border-color:\s*var\(--danger-action-light-border\)/.test(preferencesCss), 'preferences global reset button uses shared danger tokens in light mode');
    assert.equal(preferencesHtml.includes('data-preferences-reset-confirm'), false, 'preferences do not show the destructive confirmation until requested');
    const resetConfirmHtml = api.preferencesResetConfirmHtmlForTest();
    assert.ok(resetConfirmHtml.includes('data-preferences-reset-confirm'), 'reset-all requires a second continue action');
    assert.ok(resetConfirmHtml.includes('Continue reset'), 'reset-all confirmation names the continue action');
    assert.ok(resetConfirmHtml.includes('preferences-global-reset confirming'), 'reset-all confirmation makes the warning visibly change');
    assert.ok(preferencesHtml.includes('preferences-setting-control setting-type-number'), 'number controls are identifiable for compact sizing');
    assert.ok(preferencesHtml.includes('data-setting-path="file_explorer.image_preview_max_px"'), 'preferences expose Finder image preview sizing');
    assert.ok(preferencesHtml.includes('data-setting-path="performance.server_event_poll_ms"'), 'Preferences expose the server SSE editor file-change poll interval');
    assert.ok(preferencesHtml.includes('data-setting-path="updates.notify_level"'), 'Preferences expose the origin/main update notification threshold');
    assert.equal(preferencesHtml.includes('data-setting-path="updates.check_enabled"'), false, 'Preferences do not expose a redundant origin/main update-check toggle');
    assert.ok(/value="major"[^>]*data-setting-path="updates\.notify_level"[\s\S]*value="minor"[^>]*data-setting-path="updates\.notify_level"[\s\S]*value="patch"[^>]*data-setting-path="updates\.notify_level"[^>]*checked[\s\S]*value="none"[^>]*data-setting-path="updates\.notify_level"/.test(preferencesHtml), 'Update notification threshold is a major/minor/patch/none radio group defaulting to patch');
    assert.ok(/data-setting-path="performance\.server_event_poll_ms"[\s\S]*?value="0\.850"[\s\S]*?min="0\.25"[\s\S]*?step="0\.05"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'server-side SSE editor file-change poll displays seconds with a 0.250s minimum');
    assert.ok(/data-setting-path="performance\.server_background_file_event_poll_ms"[\s\S]*?value="5\.000"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'server-side SSE background editor file-change poll defaults to 5 seconds');
    assert.ok(/data-setting-path="performance\.server_directory_event_poll_ms"[\s\S]*?value="3\.000"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'server-side SSE directory-change poll displays seconds');
    assert.ok(/data-setting-path="performance\.tabber_activity_refresh_ms"[\s\S]*?value="15"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'Tabber server poll interval defaults to 15 seconds in Preferences');
    assert.ok(/data-setting-path="performance\.agent_window_cooldown_seconds"[\s\S]*?value="60"[\s\S]*?min="0"[\s\S]*?max="300"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'finished yellow ball duration defaults to 60 seconds in Preferences');
    assert.ok(/data-setting-path="performance\.latency_refresh_ms"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'latency refresh displays seconds instead of raw milliseconds');
    assert.ok(/data-setting-path="performance\.event_log_refresh_ms"[\s\S]*?preferences-setting-suffix">s</.test(preferencesHtml), 'event-log refresh displays seconds instead of raw milliseconds');
    assert.ok(/data-setting-path="performance\.popover_show_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'hover popover timing remains in milliseconds');
    assert.ok(/data-setting-path="performance\.menu_hover_open_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'menu hover timing remains in milliseconds');
    assert.ok(/data-setting-path="performance\.tab_popover_show_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'tab hover timing remains in milliseconds');
    assert.ok(/data-setting-path="performance\.tab_popover_follow_delay_ms"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'tab hover follow timing remains in milliseconds');
    assert.ok(/data-setting-path="performance\.remote_resize_delay_ms"[\s\S]*?value="220"[\s\S]*?min="50"[\s\S]*?max="2000"[\s\S]*?step="10"[\s\S]*?preferences-setting-suffix">ms</.test(preferencesHtml), 'remote resize client/server debounce displays milliseconds');
    const performanceHtml = preferencesHtml.slice(preferencesHtml.indexOf('data-preference-section="Performance"'), preferencesHtml.indexOf('data-preference-section="GitHub"'));
    const notificationsHtml = preferencesHtml.slice(preferencesHtml.indexOf('data-preference-section="Notifications"'), preferencesHtml.indexOf('data-preference-section="Finder"'));
    assert.ok(performanceHtml.includes('Server SSE: editor file-change poll'), 'Performance labels the server-side SSE editor file-change interval');
    assert.ok(performanceHtml.includes('Server SSE: background editor file-change poll'), 'Performance labels the server-side SSE background editor interval');
    assert.ok(performanceHtml.includes('Server SSE: directory-change poll'), 'Performance labels the server-side SSE directory-change interval');
    assert.ok(performanceHtml.includes('Tabber server poll interval'), 'Performance labels the Tabber activity refresh as a server poll interval');
    assert.equal(performanceHtml.includes('Finished yellow ball duration'), false, 'Performance does not own the finished yellow ball duration');
    assert.ok(notificationsHtml.includes('Finished yellow ball duration'), 'Notifications labels the finished yellow ball duration');
    assert.ok(notificationsHtml.includes('Yellow means the agent is done; look at its output.'), 'Notifications explains what the yellow finished ball means');
    assert.equal(performanceHtml.includes('Client pull: file-change/Differ fallback'), false, 'Performance no longer exposes the removed client file-change fallback interval');
    for (const removedPath of [
      'file_explorer.refresh_seconds',
      'file_explorer.session_files_refresh_seconds',
      'performance.activity_summary_refresh_ms',
      'performance.settings_refresh_ms',
      'performance.metadata_refresh_ms',
      'performance.watched_pr_refresh_ms',
      'performance.pane_state_refresh_ms',
    ]) {
      assert.equal(preferencesHtml.includes(`data-setting-path="${removedPath}"`), false, `${removedPath} is no longer exposed in Preferences`);
    }
    assert.ok(/data-setting-path="appearance\.red_reminder_ms"[\s\S]*data-setting-path="performance\.agent_window_cooldown_seconds"[\s\S]*data-setting-path="appearance\.metadata_badge_pulse_seconds"/.test(notificationsHtml), 'Notifications order keeps the finished yellow ball duration after the red/yellow/green pulse setting');
    assert.ok(/data-setting-path="performance\.server_event_poll_ms"[\s\S]*data-setting-path="performance\.server_background_file_event_poll_ms"[\s\S]*data-setting-path="performance\.server_directory_event_poll_ms"[\s\S]*data-setting-path="performance\.latency_refresh_ms"[\s\S]*data-setting-path="performance\.event_log_refresh_ms"[\s\S]*data-setting-path="performance\.tabber_activity_refresh_ms"/.test(performanceHtml), 'Performance order groups server SSE settings before remaining client timers');
    assert.equal(preferencesHtml.includes('data-setting-path="file_explorer.refresh_ms"'), false, 'Finder refresh interval no longer exposes the legacy millisecond setting');
    assert.equal(diffBundle.includes('fileExplorerRefreshMsFromSettings'), false, 'Finder client-pull refresh setting helper is removed');
    assert.equal(diffBundle.includes('sessionFilesRefreshMsFromSettings'), false, 'Changed-files client-pull refresh setting helper is removed');
    assert.equal(diffBundle.includes("initialSetting('file_explorer.refresh_seconds'"), false, 'JS no longer reads the removed Finder fallback setting');
    assert.equal(diffBundle.includes("initialSetting('file_explorer.session_files_refresh_seconds'"), false, 'Changed-files/Differ fallback does not read a separate setting');
    assert.ok(diffBundle.includes("path: 'performance.server_event_poll_ms'") && diffBundle.includes('displayDecimals: 3'), 'server file-change poll stores milliseconds but displays 0.850-style seconds');
    assert.ok(diffBundle.includes("path: 'performance.server_background_file_event_poll_ms'") && diffBundle.includes('displayDecimals: 3'), 'server background file-change poll stores milliseconds but displays 5.000-style seconds');
    assert.ok(diffBundle.includes("path: 'performance.server_directory_event_poll_ms'") && diffBundle.includes('displayDecimals: 3'), 'server directory-change poll stores milliseconds but displays 0.850-style seconds');
    assert.ok(diffBundle.includes("path: 'performance.tabber_activity_refresh_ms'") && diffBundle.includes("initialSetting('performance.tabber_activity_refresh_ms')"), 'Tabber activity refresh is backed by the Performance preference through settings defaults');
    assert.equal(diffBundle.includes("initialSetting('performance.tabber_activity_refresh_ms', 15000)"), false, 'Tabber activity refresh does not duplicate the server default in bootstrap JS');
    assert.equal(diffBundle.includes("numberSetting('performance.tabber_activity_refresh_ms', 15000)"), false, 'Tabber activity refresh does not duplicate the server default on settings reload');
    assert.ok(diffBundle.includes("path: 'performance.agent_window_cooldown_seconds'") && diffBundle.includes("initialSetting('performance.agent_window_cooldown_seconds')"), 'agent cooldown duration is backed by the Performance preference through settings defaults');
    assert.ok(preferencesHtml.includes('data-setting-path="uploads.max_bytes"'), 'preferences expose the upload size cap');
    api.setClientSettingsPatchForTest({uploads: {max_bytes: 64 * 1024 * 1024}});
    const largeUploadPreferencesHtml = api.preferencesPanelHtmlForTest('upload', []);
    assert.ok(largeUploadPreferencesHtml.includes('preferences-setting-advisory'), 'large upload cap shows an rsync recommendation');
    assert.ok(largeUploadPreferencesHtml.includes('rsync -avz'), 'large upload recommendation includes a copyable rsync command');
    assert.ok(/data-setting-path="uploads\.max_bytes"[\s\S]*?value="64"/.test(largeUploadPreferencesHtml), 'upload size cap displays in MB (64), not raw bytes');
    assert.ok(/data-setting-path="uploads\.max_bytes"[\s\S]*?max="512"/.test(largeUploadPreferencesHtml), 'upload size cap min/max are expressed in MB');
    assert.ok(/data-setting-path="uploads\.max_bytes"[\s\S]*?preferences-setting-suffix">MB</.test(largeUploadPreferencesHtml), 'upload size cap labels its unit as MB');
    assert.equal(/data-setting-path="uploads\.max_bytes"[\s\S]*?preferences-setting-suffix">bytes</.test(largeUploadPreferencesHtml), false, 'upload size cap no longer shows a raw bytes suffix');
    assert.ok(preferencesHtml.includes('Auto-focus active pane'), 'auto-focus setting names the whole active pane/view');
    assert.ok(preferencesHtml.includes('enable hover-open menus'), 'auto-focus help covers menu hover behavior');
    assert.ok(preferencesHtml.includes('Off by default'), 'auto-focus help explains the default');
    assert.equal(preferencesHtml.includes('Auto-focus terminals'), false, 'auto-focus setting is not terminal-only');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.theme"'), 'preferences expose the global app theme setting');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.terminal_theme"'), 'preferences expose the terminal color theme setting');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.date_time_hour_cycle"'), 'preferences expose the date/time clock setting');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_dark_color_scheme"'), 'preferences expose the dark editor scheme setting');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_light_color_scheme"'), 'preferences expose the light editor scheme setting');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_cursor_style"'), 'preferences expose the editor cursor style setting');
    // Cursor color is a preference shared by the active terminal cursor, editor cursor, and pane scrollbar thumb.
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.editor_cursor_color"'), 'preferences expose the editor cursor color setting');
    {
      const cursorSrc = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(/const DEFAULT_CURSOR_COLOR\s*=\s*'yellow'/.test(cursorSrc), 'cursor color: yellow remains the named default');
      assert.ok(/const NEON_CURSOR_COLOR_CHOICES\s*=\s*\['laser-lime', 'neon-green', 'neon-cyan', 'neon-magenta', 'neon-orange'\]/.test(cursorSrc), 'cursor color: neon choices are cursor-only, not active-color choices');
      assert.ok(/'laser-lime':\s*\{cursorLabelKey:\s*'pref\.appearance\.editor_cursor_color\.laser-lime',\s*cursor:\s*\{dark:\s*'#ccff00',\s*light:\s*'#6b8f00'\}\}/.test(cursorSrc), 'cursor color: Laser lime is the first neon cursor preset with a readable light-mode variant');
      assert.ok(/function editorCursorColorForScheme[\s\S]*value === 'theme' \? scheme\.cursor : cursorColorForPreset\(value, scheme\?\.dark === false\)/.test(cursorSrc), 'cursor color: theme uses the scheme cursor, color choices use the shared UI color parent');
      assert.ok(/function activeTerminalCursorColorForTheme[\s\S]*value === 'theme' \? baseTheme\.cursor : cursorColorForPreset\(value, resolvedTerminalThemeMode\(\) === 'light'\)/.test(cursorSrc), 'cursor color: active terminal uses the same shared UI color parent');
      assert.ok(cursorSrc.includes("initialSetting('appearance.editor_cursor_color', DEFAULT_CURSOR_COLOR)"), 'editor cursor color defaults through the shared cursor default');
    }
    assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave"'), 'preferences expose editor autosave');
    assert.ok(preferencesHtml.includes('data-setting-path="editor.autosave_delay_seconds"'), 'preferences expose editor autosave delay');
    assert.ok(preferencesHtml.includes('data-setting-path="yoagent.backend"'), 'preferences expose YO!agent backend');
    assert.ok(preferencesHtml.includes('data-setting-path="yoagent.claude_model"'), 'preferences expose YO!agent Claude model');
    assert.ok(preferencesHtml.includes('data-setting-path="yoagent.codex_model"'), 'preferences expose YO!agent Codex model');
    assert.ok(preferencesHtml.includes('Claude model'), 'YO!agent Claude model label is localized instead of showing a raw key');
    assert.ok(preferencesHtml.includes('Codex model'), 'YO!agent Codex model label is localized instead of showing a raw key');
    assert.equal(preferencesHtml.includes('pref.yoagent.claude_model.label'), false, 'YO!agent Claude model label does not fall back to the raw i18n key');
    assert.equal(preferencesHtml.includes('pref.yoagent.codex_model.label'), false, 'YO!agent Codex model label does not fall back to the raw i18n key');
    assert.equal(/yoagent\.claude_model[\s\S]{0,220}wide:\s*true/.test(diffBundle), false, 'YO!agent Claude model does not stretch across a full-width row');
    assert.equal(/yoagent\.codex_model[\s\S]{0,260}wide:\s*true/.test(diffBundle), false, 'YO!agent Codex model does not stretch across a full-width row');
    assert.ok(/select\[data-setting-path="yoagent\.claude_model"\][\s\S]*select\[data-setting-path="yoagent\.codex_model"\]\s*\{[\s\S]*width:\s*min\(calc\(100% - var\(--preferences-control-left-indent\)\),\s*42ch\)/.test(preferencesCss), 'YO!agent model selects are just wide enough for model labels, not full row width');
    assert.equal(preferencesHtml.includes('data-setting-path="yoagent.auto_refresh"'), false, 'YO!agent background transcript summaries no longer expose an auto-refresh checkbox');
    assert.equal(preferencesHtml.includes('data-setting-path="yoagent.refresh_interval_seconds"'), false, 'YO!agent background transcript summaries no longer expose an interval row');
    assert.ok(preferencesHtml.includes('data-setting-path="yoagent.system_prompt"'), 'preferences expose YO!agent prompt');
    assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.system_prompt"[\s\S]*data-setting-autosize="true"/.test(preferencesHtml), 'YO!agent system prompt renders as an autosizing full-width row');
    assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.intro"[\s\S]*data-setting-autosize="true"/.test(preferencesHtml), 'YO!agent intro renders as an autosizing full-width row');
    assert.ok(/preferences-setting-row preferences-setting-row--wide[\s\S]*data-setting-path="yoagent\.format"[\s\S]*data-setting-autosize="true"/.test(preferencesHtml), 'YO!agent format renders as an autosizing full-width row');
    assert.ok(/yoagent\.system_prompt[\s\S]*?alwaysEnableReset:\s*true/.test(diffBundle), 'YO!agent system prompt keeps its row Reset button enabled to rewrite stale saved prompts');
    assert.ok(/yoagent\.intro[\s\S]*?alwaysEnableReset:\s*true/.test(diffBundle), 'YO!agent intro keeps its row Reset button enabled to rewrite stale saved prompts');
    assert.ok(/yoagent\.format[\s\S]*?alwaysEnableReset:\s*true/.test(diffBundle), 'YO!agent answer format keeps its row Reset button enabled to rewrite stale saved prompts');
    assert.ok(/const resetDisabled = preferencesReadOnlyVisual \|\| \(!item\.alwaysEnableReset && JSON\.stringify\(value\) === JSON\.stringify\(defaultValue\)\)/.test(diffBundle), 'alwaysEnableReset bypasses only the same-as-default disable rule');
    assert.ok(/data-setting-reset="yoagent\.system_prompt"(?! disabled)/.test(preferencesHtml), 'YO!agent system prompt row Reset is visible and enabled at defaults');
    assert.ok(/data-setting-reset="yoagent\.intro"(?! disabled)/.test(preferencesHtml), 'YO!agent intro row Reset is visible and enabled at defaults');
    assert.ok(/data-setting-reset="yoagent\.format"(?! disabled)/.test(preferencesHtml), 'YO!agent answer format row Reset is visible and enabled at defaults');
    assert.ok(/data-setting-path="file_explorer\.quick_access_paths"[\s\S]*data-setting-type="list"[\s\S]*rows="3"/.test(preferencesHtml), 'list settings keep compact textarea rows');
    assert.ok(/data-setting-path="uploads\.image_action_order"[\s\S]*data-setting-autosize="true"[\s\S]*data-setting-max-items="9"[\s\S]*rows="7"/.test(preferencesHtml), 'image paste action order autosizes from its readable list and caps shortcut-backed items at 9');
    api.setClientSettingsPatchForTest({uploads: {image_action_order: ['Extract the text (OCR): ; do OCR on this image and extract all of the text.']}});
    assert.ok(api.preferencesPanelHtmlForTest('image paste', []).includes('Extract the text (OCR): ; do OCR on this image and extract all of the text.'), 'image paste action order shows popup label plus the exact inserted prompt text');
    assert.ok(/\.preferences-setting-row--wide\s*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/.test(preferencesCss), 'wide preference rows stack to one column');
    assert.ok(/\.preferences-setting-row--wide \.preferences-setting-control textarea\s*\{[\s\S]*grid-column:\s*1 \/ -1[\s\S]*min-height:\s*5lh/.test(preferencesCss), 'wide textarea controls span the row with a compact autosize floor');
    assert.ok(/function autosizePreferenceTextarea\([\s\S]*scrollHeight/.test(diffBundle), 'Preferences autosize textareas resize from their content height');
    assert.ok(/textarea\[data-setting-autosize="true"\]\s*\{[^}]*overflow-y:\s*hidden/.test(preferencesCss), 'autosizing Preferences textareas do not show stale inner scrollbars');
    // #38: long-value text fields (upload filename template, YOLO rule path) render as full-width --wide
    // rows so the whole value shows instead of clipping at the old 24ch cap.
    assert.ok(/preferences-setting-row preferences-setting-row--wide"><label class="preferences-setting-label" for="preference-uploads-filename_template"/.test(preferencesHtml), '#38: the upload filename template is a full-width row so its long value is not clipped');
    assert.ok(/preferences-setting-row preferences-setting-row--wide"><label class="preferences-setting-label" for="preference-yolo-rule_file_path"/.test(preferencesHtml), '#38: the YOLO rule file path is a full-width row so the long path is not clipped');
    assert.ok(/\.preferences-setting-row--wide \.preferences-setting-control\.setting-type-text input\[type="text"\][\s\S]*?\.preferences-setting-row--wide \.preferences-setting-control\.setting-type-select select\s*\{[\s\S]*?width:\s*100%/.test(preferencesCss), '#38: text/select inputs fill the full width inside wide rows');
    const radioModePaths = new Map([
      ['general.default_layout', ['single', 'split', 'grid']],
      ['appearance.theme', ['system', 'dark', 'light']],
      ['appearance.active_color', ['green', 'blue', 'orange', 'yellow', 'purple', 'white']],
      ['appearance.separator_color', ['theme', 'green', 'blue', 'orange', 'yellow', 'purple', 'white']],
      ['appearance.terminal_theme', ['follow-app', 'dark', 'light']],
      ['appearance.date_time_hour_cycle', ['24', '12']],
      ['appearance.editor_cursor_style', ['line', 'block']],
      ['appearance.editor_cursor_color', ['green', 'blue', 'orange', 'yellow', 'purple', 'white', 'laser-lime', 'neon-green', 'neon-cyan', 'neon-magenta', 'neon-orange', 'theme']],
      ['yolo.prompt_source', ['hybrid', 'pane']],
      ['file_explorer.root_mode', ['fixed', 'sync']],
      ['file_explorer.image_open_mode', ['same-tab', 'new-tab']],
      ['yoagent.backend', ['auto', 'codex', 'claude']],
      ['yoagent.invocation', ['cli']],
    ]);
    for (const [path, values] of radioModePaths) {
      const pathPattern = path.replace(/\./g, '\\.');
      assert.equal(new RegExp(`<select[^>]*data-setting-path="${pathPattern}"`).test(preferencesHtml), false, `${path} is a compact mode and renders as radios, not a select`);
      for (const value of values) {
        assert.ok(new RegExp(`type="radio"[^>]*value="${value}"[^>]*data-setting-path="${pathPattern}"`).test(preferencesHtml), `${path} radio renders ${value}`);
      }
    }
    assert.ok(preferencesHtml.includes('>Same Tab<'), 'string radio labels are humanized');
    assert.equal(/data-setting-path="appearance\.theme"[\s\S]{0,180}preferences-radio-swatches/.test(preferencesHtml), false, 'Global color theme radios do not show color swatches');
    assert.equal(/<select[^>]*data-setting-path="appearance\.active_color"/.test(preferencesHtml), false, 'Active color renders as radios, not a select');
    assert.equal(/<select[^>]*data-setting-path="appearance\.separator_color"/.test(preferencesHtml), false, 'Separator color renders as radios, not a select');
    assert.ok(preferencesHtml.includes('data-setting-path="appearance.separator_color"'), 'Preferences expose separator color for pane separators and drop previews');
    assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#86d600[\s\S]*--preferences-radio-swatch:#4f9e3a/.test(preferencesHtml), 'Active color Green radio shows joined actual dark/light accent swatches');
    assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#f97316[\s\S]*--preferences-radio-swatch:#b91c1c/.test(preferencesHtml), 'Active color Blood orange light swatch is redder than Solar gold');
    assert.ok(/preferences-radio-swatches joined[\s\S]*--preferences-radio-swatch:#eab308[\s\S]*--preferences-radio-swatch:#d6a400/.test(preferencesHtml), 'Active color Solar gold light swatch is a brighter gold');
    assert.ok(/\.preferences-radio-group\.has-swatches\s*\{[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(148px,\s*1fr\)\)/.test(preferencesCss), 'swatched Preferences radio groups use a shared grid so wrapped rows align');
    assert.ok(/\.preferences-radio\.has-swatches\s*\{[\s\S]*grid-template-columns:\s*18px 34px minmax\(0,\s*1fr\)/.test(preferencesCss), 'swatched Preferences radios align radio, color chips, and label in fixed columns');
    assert.ok(/\.preferences-radio-swatch\s*\{[\s\S]*border-radius:\s*2px/.test(preferencesCss), 'Preferences color swatches are boxed, not round dots');
    assert.ok(/\.preferences-radio-swatches\.joined\s*\{[\s\S]*gap:\s*0/.test(preferencesCss), 'Preferences active-color swatches are connected into one segmented box');
    assert.ok(/\.keyboard-shortcuts-section h3\s*\{[\s\S]*color:\s*var\(--active-accent-bright\)/.test(preferencesCss), 'Keyboard shortcuts section headers follow the active theme color');
    assert.ok(preferencesHtml.includes('>New Tab<'), 'hyphenated string radio labels are humanized');
    // "No agent" (deterministic) is no longer a selectable backend — Auto still falls back to it internally,
    // but it is never offered as a pick in Preferences or the composer pill.
    assert.equal(/data-setting-path="yoagent\.backend"[\s\S]*?value="deterministic"/.test(preferencesHtml), false, 'Preferences no longer offer No agent (deterministic) as a backend option');
    assert.equal(preferencesHtml.includes('Deterministic'), false, 'Preferences do not expose the internal deterministic backend label');
    assert.equal(/data-setting-path="yoagent\.invocation"[\s\S]*?value="api-key"/.test(preferencesHtml), false, 'Preferences do not expose the reserved API-key invocation mode');
    assert.ok(/data-setting-path="yoagent\.invocation"[\s\S]*?>\s*<span>Local process<\/span>/.test(preferencesHtml), 'Preferences labels YO!agent invocation as a local process transport, not a generic CLI toggle');
    assert.ok(preferencesHtml.includes('persistent local app-server') && preferencesHtml.includes('stream-json CLI subprocess'), 'Preferences help explains Codex app-server and Claude stream-json transport');
    // #41: the YO!agent backend defaults to auto (codex -> claude -> No agent) and Preferences offers it.
    assert.ok(/value="auto"[^>]*data-setting-path="yoagent\.backend"[\s\S]*?>\s*<span>Auto \(Codex → Claude\)<\/span>/.test(preferencesHtml), '#41: the YO!agent backend radios offer the Auto option');
    assert.equal(preferencesHtml.includes('data-setting-path="appearance.editor_color_scheme"'), false, 'preferences do not show the legacy single mixed editor scheme setting');
    assert.ok(preferencesHtml.includes('<optgroup label="Dark">'), 'dark editor schemes are grouped under Dark');
    assert.ok(preferencesHtml.includes('<optgroup label="Light">'), 'light editor schemes are grouped under Light');
    assert.ok(preferencesHtml.indexOf('YOLOmux Dark') < preferencesHtml.indexOf('Popular IDE Dark+'), 'YOLOmux dark scheme appears first');
    assert.ok(preferencesHtml.indexOf('Popular IDE Light+') < preferencesHtml.indexOf('YOLOmux Light'), 'Popular IDE Light+ is the first light scheme');
    assert.ok(preferencesHtml.indexOf('YOLOmux Light') < preferencesHtml.indexOf('GitHub Light'), 'YOLOmux light scheme remains ahead of GitHub Light');
    assert.equal(api.globalThemeModeForTest(), 'dark');
    assert.equal(api.globalThemeIsDark(), true, 'global theme defaults dark');
    assert.equal(api.globalThemeLabel(), 'Dark');
    assert.equal(api.nextGlobalThemeMode(), 'light');
    assert.equal(api.terminalThemeModeForTest(), 'follow-app', 'terminal theme defaults to follow-app (matches the global app theme)');
    assert.equal(api.terminalThemeForGlobalTheme('light').background, '#ffffff', 'follow-app default gives a light terminal in light app mode');
    assert.equal(api.dateTimeHourCycleForTest(), '24', 'date/time clock defaults to 24-hour');
    {
      const timestamp = Date.UTC(2026, 5, 4, 19, 17) / 1000;
      const hour24Text = api.sessionFileTimeText(timestamp);
      assert.equal(/[AP]\.?M\.?/i.test(hour24Text), false, '24-hour file dates do not show AM/PM');
      assert.ok(/\b\d{2}:\d{2}\b/.test(hour24Text), '24-hour file dates render a two-digit clock');
      api.setDateTimeHourCycleForTest('12');
      const hour12Text = api.sessionFileTimeText(timestamp);
      assert.ok(/[AP]\.?M\.?/i.test(hour12Text), '12-hour file dates show AM/PM');
      api.setDateTimeHourCycleForTest('bogus');
      assert.equal(api.dateTimeHourCycleForTest(), '24', 'invalid date/time clock values normalize to 24-hour');
      api.setFileExplorerTreeDateModeForTest('none');
      assert.equal(api.sessionFileDisplayTimeText(timestamp), '', 'file-tree date mode None hides Finder/Differ timestamps');
      assert.equal(api.fileExplorerTreeDateModeLabel('none'), 'None', 'file-tree date mode None label uses the source locale catalog');
      assert.equal(api.fileExplorerTreeDateModeButtonLabel('none'), 'Date', 'file-tree date mode None button shows crossed-out Date text');
      assert.equal(api.fileExplorerTreeDateModeLabel('date'), 'Date', 'file-tree date mode Date label uses the source locale catalog');
      assert.equal(api.fileExplorerTreeDateModeLabel('relative'), 'Ago', 'file-tree date mode Ago label uses the source locale catalog');
      assert.equal(api.fileExplorerTreeDateModeTitle('relative'), 'Date display: Ago. Click to cycle None, Date, Ago.', 'file-tree date mode tooltip uses localized catalog text');
      api.setFileExplorerTreeDateModeForTest('date');
      assert.equal(api.sessionFileDisplayTimeText(timestamp), api.sessionFileTimeText(timestamp), 'file-tree date mode Date uses the localized absolute timestamp');
      api.setFileExplorerTreeDateModeForTest('relative');
      assert.equal(api.sessionFileRelativeTimeText(1000, 999), 'now', 'file-tree relative dates localize the now case');
      assert.equal(api.sessionFileRelativeTimeText(1000, 1014), '<15 sec ago', 'file-tree relative dates localize the pulse-window case');
      assert.equal(api.sessionFileRelativeTimeText(1000, 1059), '<1 min ago', 'file-tree relative dates localize the sub-minute case after the pulse window');
      assert.equal(api.sessionFileRelativeTimeText(1000, 1060), '1 min ago', 'file-tree relative dates show minute age');
      assert.equal(api.sessionFileRelativeTimeText(1000, 19720), '5.2 hrs ago', 'file-tree relative dates show decimal hour age');
      assert.equal(api.sessionFileRelativeTimeText(1000, 217000), '2.5 days ago', 'file-tree relative dates show decimal day age');
    }
    api.setTerminalThemeModeForTest('light');
    assert.equal(api.terminalThemeForGlobalTheme('dark').background, '#ffffff', 'terminal light theme is explicit opt-in');
    assert.equal(api.terminalThemeForGlobalTheme('dark').blue, '#0451a5');
    assert.equal(api.terminalThemeForGlobalTheme('dark').selectionBackground, '#93c5fd', 'light terminal selection uses a visible blue fill');
    assert.equal(api.terminalThemeForGlobalTheme('dark').selectionForeground, '#071327', 'light terminal selection forces readable selected text');
    // a white terminal auto-darkens faint 24-bit agent text via minimumContrastRatio.
    assert.equal(api.terminalMinimumContrastRatio('dark'), 4.5, '#32: light terminal raises the minimum contrast ratio');
    api.setTerminalThemeModeForTest('dark');
    assert.equal(api.terminalMinimumContrastRatio('dark'), 3, 'dark terminal uses a moderate 3:1 floor so a light-on-white agent composer (Codex input) is forced readable');
    api.setTerminalThemeModeForTest('light');
    api.setTerminalThemeModeForTest('follow-app');
    assert.equal(api.terminalThemeForGlobalTheme('light').background, '#ffffff', 'follow-app maps to the resolved app theme');
    assert.equal(api.terminalThemeForGlobalTheme('dark').background, '#11151d');
    {
      const refreshCalls = [];
      let textureClears = 0;
      const term = {
        rows: 24,
        cols: 80,
        options: {},
        refresh(start, end) { refreshCalls.push([start, end]); },
        clearTextureAtlas() { textureClears += 1; },
      };
      api.registerTerminalForTest('1', term);
      api.setGlobalThemeModeForTest('dark');
      api.applyGlobalThemeMode({updateEditor: false, updateTerminals: true});
      assert.equal(term.options.theme.background, '#11151d', 'terminal runtime theme follows the dark app theme');
      assert.deepStrictEqual(refreshCalls, [[0, 23]], 'terminal theme changes force an xterm repaint of already-painted cells');
      assert.equal(textureClears, 1, 'terminal theme changes clear cached glyph colors before repaint');
    }
    {
      const refreshCalls = [];
      let textureClears = 0;
      const term = {
        rows: 24,
        cols: 80,
        options: {},
        refresh(start, end) { refreshCalls.push([start, end]); },
        clearTextureAtlas() { textureClears += 1; },
      };
      api.registerTerminalForTest('system-repaint', term);
      api.setTerminalThemeModeForTest('follow-app');
      api.setSystemPrefersDarkForTest(false);
      api.setGlobalThemeModeForTest('dark');
      api.applyGlobalThemeMode({updateEditor: false, updateTerminals: true});
      refreshCalls.length = 0;
      textureClears = 0;
      api.setGlobalThemeModeForTest('system');
      api.applyGlobalThemeMode({updateEditor: false, updateTerminals: true});
      assert.equal(term.options.theme.background, '#ffffff', 'System repaints follow-app terminals to the OS-resolved light theme from Dark');
      assert.deepStrictEqual(refreshCalls, [[0, 23]], 'System from Dark forces a terminal repaint');
      assert.equal(textureClears, 1, 'System from Dark clears cached glyph colors');
      refreshCalls.length = 0;
      textureClears = 0;
      api.setSystemPrefersDarkForTest(true);
      api.setGlobalThemeModeForTest('light');
      api.applyGlobalThemeMode({updateEditor: false, updateTerminals: true});
      refreshCalls.length = 0;
      textureClears = 0;
      api.setGlobalThemeModeForTest('system');
      api.applyGlobalThemeMode({updateEditor: false, updateTerminals: true});
      assert.equal(term.options.theme.background, '#11151d', 'System repaints follow-app terminals to the OS-resolved dark theme from Light');
      assert.deepStrictEqual(refreshCalls, [[0, 23]], 'System from Light forces a terminal repaint');
      assert.equal(textureClears, 1, 'System from Light clears cached glyph colors');
    }
    api.setSystemPrefersDarkForTest(false);
    api.setGlobalThemeModeForTest('system');
    api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
    assert.ok(api.bodyClassListForTest().contains('theme-system'), 'system theme keeps the preference marker on the body');
    assert.ok(api.bodyClassListForTest().contains('theme-light'), 'system-resolved-light applies the same body class as explicit light');
    assert.ok(api.bodyClassListForTest().contains('theme-resolved-light'), 'system-resolved-light keeps the resolved marker for share/editor logic');
    api.setSystemPrefersDarkForTest(true);
    api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
    assert.ok(api.bodyClassListForTest().contains('theme-system'), 'system theme keeps the preference marker after OS dark changes');
    assert.ok(api.bodyClassListForTest().contains('theme-dark'), 'system-resolved-dark applies the same body class as explicit dark');
    assert.ok(api.bodyClassListForTest().contains('theme-resolved-dark'), 'system-resolved-dark keeps the resolved marker for share/editor logic');
    api.setTerminalThemeModeForTest('dark');
    assert.equal(api.terminalThemeForGlobalTheme('dark').selectionBackground, UI_PINS.textSelectionBg, 'dark terminal selection uses a prominent blue fill');
    assert.equal(api.terminalThemeForGlobalTheme('dark').selectionForeground, '#ffffff', 'dark terminal selection forces readable selected text');
    api.setGlobalThemeModeForTest('light');
    api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
    assert.ok(api.bodyClassListForTest().contains('theme-light'), 'global light theme marks the body');
    assert.ok(api.bodyClassListForTest().contains('theme-resolved-light'), 'global light theme tracks the resolved mode');
    assert.equal(api.activeEditorSchemeForTest().label, 'YOLOmux Light', 'inherited editor theme follows global light (brand YOLOmux Light default)');
    assert.equal(api.configuredEditorSchemeForMode(true), 'dark');
    assert.equal(api.configuredEditorSchemeForMode(false), 'yolomux-light');
    api.setGlobalThemeModeForTest('dark');
    api.applyGlobalThemeMode({updateEditor: true, updateTerminals: false});
    assert.equal(api.fileEditorThemeModeForTest(), 'inherit');
    assert.equal(api.activeEditorSchemeForTest().label, 'YOLOmux Dark');
    assert.equal(api.activeEditorSchemeForTest().activeLine, 'rgba(255, 255, 255, 0.04)');
    assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(96, 165, 250, 0.38)');
    assert.equal(api.activeEditorSchemeForTest().diff.addFg, '#56d364');
    assert.equal(api.activeEditorSchemeForTest().diff.removeFg, '#ff7b72');
    assert.equal(api.activeEditorSchemeForTest().activeLine.includes('118, 185, 0'), false, 'YOLOmux dark active line is no longer green');
    assert.equal(api.activeEditorSchemeForTest().selection.includes('118, 185, 0'), false, 'YOLOmux dark selection is no longer green');
    const editorSelectionSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const editorSelectionCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(editorSelectionSource.includes("&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground"), 'editor selection theme beats CodeMirror focused selection defaults');
    assert.ok(editorSelectionSource.includes("boxShadow: scheme.dark ? 'inset 0 0 0 1px rgba(191, 219, 254, 0.42)' : 'inset 0 0 0 1px rgba(29, 78, 216, 0.24)'"), 'editor selections get a visible edge without an opaque fill');
    assert.equal(editorSelectionSource.includes("'.cm-searchMatch-selected': {\n      backgroundColor: scheme.dark ? 'rgba(118, 185, 0, 0.84)'"), false, 'selected editor search matches are not green, so they remain visible on green active/highlighted rows');
    assert.ok(/'\.cm-searchMatch-selected': \{[\s\S]*backgroundColor: scheme\.dark \? '#ffd166' : '#ff9f1c'[\s\S]*fontWeight: '800'/.test(editorSelectionSource), 'selected editor search matches use high-contrast yellow/orange fill and bold text');
    assert.ok(/\.file-editor-diff-codemirror \.cm-searchMatch\s*\{[\s\S]*z-index:\s*var\(--z-inline-control\)[\s\S]*background:\s*var\(--diff-search-match-bg\) !important/.test(editorSelectionCss), 'Differ search matches sit above green/red diff row fills');
    assert.ok(/\.file-editor-diff-codemirror \.cm-searchMatch-selected\s*\{[\s\S]*background:\s*var\(--diff-search-selected-bg\) !important[\s\S]*font-weight:\s*900/.test(editorSelectionCss), 'selected Differ search matches use a stronger non-green fill');
    assert.ok(editorSelectionSource.includes("}, {dark: scheme.dark});"), 'CodeMirror receives the active light/dark theme flag');
    assert.ok(editorSelectionSource.includes("backgroundColor: 'transparent !important'"), 'CodeMirror native editor selection background is suppressed so drawSelection owns the fill');
    assert.ok(/\.file-editor-codemirror \.cm-content ::selection,[\s\S]*?background:\s*transparent !important[\s\S]*?color:\s*inherit !important/.test(editorSelectionCss), 'static CSS keeps global browser selection colors out of CodeMirror');
    assert.equal(/body\.editor-theme-light \.file-editor-codemirror \.cm-content,[\s\S]*?background:\s*var\(--editor-bg\) !important/.test(editorSelectionCss), false, 'light CodeMirror content stays transparent so the selection layer remains visible');
    for (const scheme of ['one-dark', 'dracula', 'monokai', 'popular-ide-dark-plus', 'nord']) {
      api.setFileEditorThemeMode(scheme);
      assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(96, 165, 250, 0.38)', `${scheme} uses the audited dark editor selection fill`);
    }
    api.setFileEditorThemeMode('github-light');
    assert.equal(api.fileEditorThemeModeForTest(), 'github-light');
    assert.equal(api.activeEditorSchemeForTest().label, 'GitHub Light');
    assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(37, 99, 235, 0.34)', 'light editor schemes use a visible blue selection fill');
    for (const scheme of ['yolomux-light', 'popular-ide-light-plus', 'one-light', 'solarized-light']) {
      api.setFileEditorThemeMode(scheme);
      assert.equal(api.activeEditorSchemeForTest().selection, 'rgba(37, 99, 235, 0.34)', `${scheme} uses the audited light editor selection fill`);
    }
    const legacySchemePrefix = ['vs', 'code'].join('');
    api.setFileEditorThemeMode(`${legacySchemePrefix}-dark-plus`);
    assert.equal(api.fileEditorThemeModeForTest(), 'popular-ide-dark-plus', 'legacy dark scheme id migrates to the Popular IDE dark scheme');
    api.setFileEditorThemeMode('github-light');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--editor-scheme-bg'), '#ffffff');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--code-keyword'), '#cf222e');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-markdown-heading'), 'var(--active-accent)');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--markdown-heading'), 'var(--active-accent)');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-markdown-heading-bg'), 'transparent');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--markdown-heading-bg'), 'transparent');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-code-inline'), '#a40e26');
    assert.equal(api.documentElementStyleForTest().getPropertyValue('--lt-code-inline-bg'), '#fff1d6');
    assert.notEqual(api.activeEditorSchemeForTest().syntax.heading, api.activeEditorSchemeForTest().syntax.link);
    assert.notEqual(api.activeEditorSchemeForTest().syntax.inlineCode, api.activeEditorSchemeForTest().syntax.heading);
    assert.equal(api.editorThemeLabel(), 'GitHub Light editor scheme');
    api.setFileEditorThemeMode('dark');
    assert.equal(api.editorPreviewThemeStateForTest(), 'dark');
    api.cycleEditorThemeMode();
    assert.equal(api.editorPreviewThemeStateForTest(), 'light', 'theme toggle moves dark preview to light preview');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
    api.cycleEditorThemeMode();
    assert.equal(api.editorPreviewThemeStateForTest(), 'vanilla', 'theme toggle moves light preview to vanilla preview');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'vanilla');
    assert.equal(api.editorThemeLabel(), 'Vanilla preview');
    api.cycleEditorThemeMode();
    assert.equal(api.editorPreviewThemeStateForTest(), 'dark', 'theme toggle moves vanilla preview back to dark preview');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
    assert.equal(api.fileEditorThemeModeForTest(), 'dark', 'vanilla leaves by restoring a dark editor scheme');
    api.setFileEditorThemeMode('dark');
    api.cycleEditorThemeMode({includeVanilla: false});
    assert.equal(api.editorPreviewThemeStateForTest(), 'light', 'edit/diff theme toggle moves dark to light');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
    api.cycleEditorThemeMode({includeVanilla: false});
    assert.equal(api.editorPreviewThemeStateForTest(), 'dark', 'edit/diff theme toggle moves light back to dark without vanilla');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme');
    api.setFileEditorThemeMode('light');
    assert.equal(api.fileEditorThemeModeForTest(), 'yolomux-light', 'legacy light storage value maps to the YOLOmux Light default');
    assert.equal(api.activeEditorSchemeForTest().bg, '#ffffff', 'YOLOmux Light uses a bright white editor background');
    assert.equal(api.activeEditorSchemeForTest().previewBg, '#ffffff', 'YOLOmux Light preview background is bright white');
    assert.equal(api.activeEditorSchemeForTest().syntax.comment, '#008000', 'YOLOmux Light uses Popular IDE-style green comments');
    assert.equal(api.activeEditorSchemeForTest().syntax.keyword, '#0000ff', 'YOLOmux Light uses Popular IDE-style blue language keywords');
    assert.equal(api.activeEditorSchemeForTest().syntax.control, '#af00db', 'YOLOmux Light uses Popular IDE-style magenta Rust control keywords');
    assert.equal(api.activeEditorSchemeForTest().syntax.function, '#267f2e', 'YOLOmux Light uses Popular IDE-style green function declarations');
    assert.equal(api.activeEditorSchemeForTest().syntax.type, '#008080', 'YOLOmux Light uses Popular IDE-style teal type declarations');
    assert.equal(api.activeEditorSchemeForTest().syntax.property, '#5f3b00', 'YOLOmux Light uses Popular IDE-style brown field and parameter names');
    api.setFileEditorPreviewDisplayMode('vanilla');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'vanilla', 'vanilla preview mode is stored separately from the editor scheme');
    api.setFileEditorThemeMode('github-light');
    assert.equal(api.fileEditorPreviewDisplayModeForTest(), 'theme', 'choosing a concrete editor theme exits vanilla preview mode');
    api.setFileEditorCursorStyleForTest('block');
    api.applyEditorCursorStyle();
    assert.ok(api.bodyClassListForTest().contains('editor-cursor-block'), 'block cursor style marks the body');
    api.setFileEditorCursorStyleForTest('line');
    api.applyEditorCursorStyle();
    assert.ok(api.bodyClassListForTest().contains('editor-cursor-line'), 'line cursor style marks the body');
    let focusedSearch = false;
    let selection = null;
    const search = {
      value: 'abc',
      focus(options) {
        focusedSearch = options?.preventScroll === true;
      },
      setSelectionRange(start, end) {
        selection = [start, end];
      },
    };
    assert.equal(api.focusPreferencesSearch({isConnected: true, querySelector: selector => selector === '[data-preferences-search]' ? search : null}), true);
    assert.equal(focusedSearch, true, 'Preferences search focus uses preventScroll');
    assert.deepStrictEqual(selection, [3, 3], 'Preferences search focus moves caret to the end');
    api.setPendingPreferencesRenderForTest(false);
    api.setPreferencesScrollActiveUntilForTest(Date.now() + 10000);
    api.renderPreferencesPanelsForTest();
    assert.equal(api.pendingPreferencesRenderForTest(), true, 'passive Preferences render is deferred while the user is scrolling');
    api.setPendingPreferencesRenderForTest(false);
    api.renderPreferencesPanelsForTest({force: true});
    assert.equal(api.pendingPreferencesRenderForTest(), false, 'forced Preferences render still runs during active scroll');
    const scrollNow = Date.now();
    api.setPreferencesScrollActiveUntilForTest(0);
    api.notePreferencesScrollActivityForTest(scrollNow);
    assert.ok(api.preferencesScrollActiveUntilForTest() >= scrollNow + 200, 'Preferences scroll activity creates a render-defer window');
    const hugeItem = {path: 'appearance.editor_font_size', label: 'Editor font size', help: 'Font size used by editor text.', suffix: 'px'};
    assert.equal(api.preferenceItemMatches(hugeItem, 'huge'), true, 'size aliases match font settings');
    const popupItem = {path: 'performance.tab_popover_show_delay_ms', label: 'Tab detail hover delay', help: 'Initial delay before details open.', suffix: 'ms'};
    assert.equal(api.preferenceItemMatches(popupItem, 'tooltip'), true, 'tooltip aliases match popover settings');
    const explorerItem = {path: 'file_explorer.quick_access_paths', label: 'Quick paths', help: 'Pinned roots.', suffix: ''};
    assert.equal(api.preferenceItemMatches(explorerItem, 'bookmarks'), true, 'bookmark aliases match quick paths');
    assert.equal(api.preferenceSectionMatches({title: 'Notifications', items: []}, 'alerts'), true, 'section search uses aliases');

    const panelForPopover = {
      getBoundingClientRect() {
        return {left: 10, right: 500, top: 0, bottom: 500, width: 490, height: 500};
      },
    };
    const popoverForPosition = new TestElement('positioned-tab-popover');
    popoverForPosition.rect = {left: 0, right: 520, top: 0, bottom: 300, width: 520, height: 300};
    api.positionPaneTabPopover({
      getBoundingClientRect() {
        return {left: 34, right: 274, top: 40, bottom: 68, width: 240, height: 28};
      },
      querySelector(selector) {
        assert.equal(selector, ':scope > .session-popover');
        return popoverForPosition;
      },
      closest(selector) {
        assert.equal(selector, '.panel');
        return panelForPopover;
      },
    });
    const popoverStyle = api.documentElementStyleForTest();
    const popoverLeft = Number.parseInt(popoverStyle.getPropertyValue('--pane-tab-popover-left'), 10);
    assert.equal(popoverLeft, 34);
    assert.equal(popoverForPosition.style.left, '34px', 'tab popovers carry replayable inline left instead of relying only on document CSS variables');
    assert.equal(popoverForPosition.style.top, '71px', 'tab popovers carry replayable inline top instead of relying only on document CSS variables');
    assert.equal(popoverForPosition.style.width, '520px', 'tab popovers carry replayable inline width so share viewers do not recompute vw-sized popovers against the client viewport');
    assert.equal(popoverForPosition.style.height, '300px', 'tab popovers carry replayable inline height so share viewers do not recompute wrapped popover height');
    assert.equal(popoverStyle.getPropertyValue('--pane-tab-popover-width'), '');
    assert.ok(popoverLeft + popoverForPosition.getBoundingClientRect().width <= 1200);
    assert.ok(popoverForPosition.getBoundingClientRect().width > panelForPopover.getBoundingClientRect().width);
    api.positionPaneTabPopover({
      getBoundingClientRect() {
        return {left: 1080, right: 1160, top: 40, bottom: 68, width: 80, height: 28};
      },
      querySelector() {
        return popoverForPosition;
      },
    });
    const clampedPopoverLeft = Number.parseInt(popoverStyle.getPropertyValue('--pane-tab-popover-left'), 10);
    assert.ok(clampedPopoverLeft + popoverForPosition.getBoundingClientRect().width <= 1200);
    assert.equal(popoverForPosition.style.left, `${clampedPopoverLeft}px`, 'clamped tab popover inline left matches the document CSS variable used by the live host');
    // #45: a needs-input popover near the top-right whose live width measures 0 (pre-paint) must still
    // clamp fully on-screen for its real rendered width — using the popover inline-size fallback, NOT the
    // tiny tab width (which let a wide popover overflow/clip off the right edge).
    const zeroWidthPopover = {getBoundingClientRect() { return {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0}; }};
    api.positionPaneTabPopover({
      getBoundingClientRect() { return {left: 1080, right: 1160, top: 40, bottom: 68, width: 80, height: 28}; },
      querySelector() { return zeroWidthPopover; },
    });
    const zeroWidthLeft = Number.parseInt(popoverStyle.getPropertyValue('--pane-tab-popover-left'), 10);
    assert.ok(zeroWidthLeft + 520 <= 1200, '#45: an unmeasured (0-width) right-edge popover still clamps on-screen for its real width');
    const viewportClamp = api.clampToViewport(1190, 790, 100, 100, {edgeGap: 8});
    assert.equal(viewportClamp.left, 1092);
    assert.equal(viewportClamp.top, 692);
    assert.deepEqual(canonical(api.appViewport()), {width: 1200, height: 800, w: 1200, h: 800}, 'M0: appViewport is the native browser viewport in normal mode');
    assert.deepEqual(canonical(api.setAppViewportOverrideForTest({w: 1440, h: 900})), {width: 1440, height: 900, w: 1440, h: 900}, 'M0: appViewport can be pinned to a host viewport shape');
    assert.equal(api.effectiveViewportWidthForTest({width: 0}), 1200, 'MV-7: missing viewport widths use one shared desktop fallback');
    assert.equal(api.effectiveViewportWidthForTest({width: 240}), 320, 'MV-7: viewport widths share the same minimum floor');
    const uiSource = fs.readFileSync('static/yolomux.js', 'utf8');
    assert.ok(/function effectiveViewportWidth\(/.test(uiSource), 'MV-7: effective viewport width is owned by one helper');
    assert.ok(/function paneDragPreviewMetrics[\s\S]*const viewportWidth = effectiveViewportWidth\(viewport\)/.test(uiSource), 'MV-7: pane drag preview routes viewport width through the shared helper');
    assert.ok(/function positionDiffRefPopover[\s\S]*const viewportWidth = effectiveViewportWidth\(viewport\)/.test(uiSource), 'MV-7: diff-ref popover routes viewport width through the shared helper');
    assert.equal(/Math\.max\(320,\s*(?:Number\()?viewport\.width/.test(uiSource), false, 'MV-7: raw viewport width floors stay out of feature code');
    api.setAppMirrorTransformForTest({scale: 2, tx: 10, ty: 20});
    assert.deepEqual(canonical(api.appSpaceRect({left: 30, top: 50, right: 130, bottom: 150, width: 100, height: 100})), {left: 10, top: 15, width: 50, height: 50, right: 60, bottom: 65}, 'M2: appSpaceRect maps visual rects back into app space under a root transform');
    assert.deepEqual(canonical(api.appSpacePoint(30, 50)), {x: 10, y: 15}, 'M7: appSpacePoint maps a visual point back into mirror app space');
    assert.deepEqual(canonical(api.visualPointFromAppSpace(10, 15)), {x: 30, y: 50}, 'M7: visualPointFromAppSpace maps app-space back into visual mirror coordinates');
    api.setAppMirrorTransformForTest({scale: 1, tx: 0, ty: 0});
    api.setAppViewportOverrideForTest(null);
    assert.equal(api.normalizeShareViewFit('contain'), 'contain');
    assert.equal(api.normalizeShareViewFit('bad'), 'cover');
    assert.deepEqual(canonical(api.shareMirrorFitTransform({width: 1440, height: 900}, {width: 720, height: 900}, 'cover')), {fit: 'cover', scale: 1, tx: -360, ty: 0, hostWidth: 1440, hostHeight: 900, clientWidth: 720, clientHeight: 900}, 'M3: cover crops the long host axis and centers it');
    assert.deepEqual(canonical(api.shareMirrorFitTransform({width: 1440, height: 900}, {width: 720, height: 900}, 'contain')), {fit: 'contain', scale: 0.5, tx: 0, ty: 225, hostWidth: 1440, hostHeight: 900, clientWidth: 720, clientHeight: 900}, 'M3: contain letterboxes the short axis and centers it');
    const hoverAnchor = new TestElement('hover-anchor');
    const hoverPopover = new TestElement('hover-popover');
    hoverAnchor.appendChild(hoverPopover);
    let hoverOpens = 0;
    let hoverCloses = 0;
    const hoverController = api.createHoverPopoverForTest({
      anchor: hoverAnchor,
      popover: hoverPopover,
      showDelay: 0,
      hideDelay: 0,
      onOpen: () => { hoverOpens += 1; },
      onClose: () => { hoverCloses += 1; },
    });
    hoverController.openNow();
    assert.equal(hoverOpens, 1, 'hover popover openNow calls its onOpen handler once');
    assert.ok(hoverAnchor.classList.contains('popover-open'));
    hoverController.closeNow();
    assert.equal(hoverCloses, 1, 'hover popover closeNow calls its onClose handler once');
    assert.equal(hoverAnchor.classList.contains('popover-open'), false, 'hover popover closeNow removes the state class');
    const staleAnchor = new TestElement('stale-anchor');
    const stalePopover = new TestElement('stale-popover');
    staleAnchor.appendChild(stalePopover);
    let staleOpens = 0;
    const staleController = api.createHoverPopoverForTest({
      anchor: staleAnchor,
      popover: stalePopover,
      showDelay: 0,
      hideDelay: 0,
      onOpen: () => { staleOpens += 1; },
    });
    staleAnchor.hovered = false;
    staleController.openNow({type: 'pointerenter'});
    assert.equal(staleOpens, 0, 'stale delayed hover opens are suppressed after the pointer leaves');
    staleAnchor.hovered = true;
    staleController.openNow({type: 'pointerenter'});
    assert.equal(staleOpens, 1, 'active hover opens still work');
    const appMenuAnchor = new TestElement('app-menu-anchor');
    appMenuAnchor.rect = {left: 900, right: 980, top: 0, bottom: 28, width: 80, height: 28};
    const appMenuWrapper = new TestElement('app-menu-wrapper');
    appMenuWrapper.querySelector = selector => {
      assert.equal(selector, ':scope > .app-menu-button, :scope > .app-menu-command');
      return appMenuAnchor;
    };
    const appMenuPopover = new TestElement('app-menu-popover');
    appMenuPopover.parentElement = appMenuWrapper;
    appMenuPopover.rect = {left: 900, right: 1380, top: 28, bottom: 400, width: 480, height: 372};
    api.fitAppMenuPopover(appMenuPopover);
    assert.equal(appMenuPopover.style.getPropertyValue('--app-menu-fit-width'), `${appMenuPopover.rect.width}px`, 'app menu fit width uses the measured popover width');
    assert.equal(appMenuPopover.style.getPropertyValue('--app-menu-fit-offset'), '-180px', 'app menu fit offset keeps the popover inside the viewport');
    // C14: the menu-width measurer must un-clip the command label + detail spans (they were omitted, so the
    // menu measured to the LABELS and the longer detail sub-lines ellipsized with "…").
    const appMenuMeasureSrc = fs.readFileSync('static/yolomux.js', 'utf8');
    const measureBody = appMenuMeasureSrc.slice(appMenuMeasureSrc.indexOf('function measureAppMenuContentWidth('), appMenuMeasureSrc.indexOf('function fitAppMenuPopover('));
    assert.ok(measureBody.includes(".app-menu-label'"), 'C14: the measurer neutralizes .app-menu-label truncation');
    assert.ok(measureBody.includes(".app-menu-detail'"), 'C14: the measurer neutralizes .app-menu-detail truncation');
    assert.ok(measureBody.includes("clone.classList?.contains('app-submenu-popover')"), 'C14: a standalone submenu clone is forced visible for measurement');
    assert.ok(measureBody.includes("querySelectorAll('.app-submenu-popover')"), 'C14: nested submenu widths are included in parent menu measurement');
    assert.ok(appMenuMeasureSrc.includes('_appMenuFontsRefit'), 'C14: a one-time fonts.ready re-fit corrects a cold first measurement');
    const topbarMenuCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(/\.app-submenu-popover\s*\{[\s\S]*?max-width:\s*calc\(100% - var\(--app-submenu-inline-offset\)\)/.test(topbarMenuCss), 'C14: nested submenus clamp to the parent menu instead of clipping under overflow');
    assert.ok(/\.app-menu-detail\s*\{[\s\S]*?max-width:\s*min\(58ch/.test(topbarMenuCss), 'C14: app menu detail rows get enough width before ellipsizing');
    const tabMenuCommand = api.menuTabCommand('1', {detail: 'Minimized'});
    assert.equal(tabMenuCommand.title, undefined);
    assert.equal(tabMenuCommand.detail, '');
    assert.equal(tabMenuCommand.ariaLabel, '1 - Minimized');
    assert.equal(tabMenuCommand.html.includes(' title='), false);
    const appMenuButton = api.createAppMenuCommand({label: 'Rename session', detail: 'Focus a tmux session first'});
    assert.equal(appMenuButton.title, undefined);
    assert.equal(appMenuButton.hasAttribute('title'), false);
    assert.equal(appMenuButton.getAttribute('aria-label'), 'Rename session - Focus a tmux session first');
    const mirroredHoverButton = api.createAppMenuCommand({label: 'Mirror hover'});
    mirroredHoverButton.listeners.get('pointerenter')[0]({type: 'pointerenter'});
    assert.ok(mirroredHoverButton.classList.contains('share-mirror-active'), 'DOIT.68: host menu option hover writes a real class the share popup layer can mirror');
    const checkedAppMenuButton = api.createAppMenuCommand({label: 'Hide tab metadata', checked: true});
    assert.equal(checkedAppMenuButton.className.includes('has-check'), false);
    assert.equal(checkedAppMenuButton.innerHTML.includes('>*<'), false);
    assert.equal(checkedAppMenuButton.dataset.checked, 'true');
    const appMenuIconButton = api.createAppMenuCommand({label: '+ Codex', iconHtml: '<span title="Codex">C</span>'});
    assert.equal(appMenuIconButton.innerHTML.includes('title='), false);
    const noMinimizedSlots = api.emptyLayoutSlots();
    noMinimizedSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    noMinimizedSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    noMinimizedSlots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(noMinimizedSlots);
    const tabMenu = api.appMenuTree().find(menu => menu.id === 'tabs');
    const tabMenuLabels = tabMenu.items.map(item => item.label).filter(Boolean);
    assert.ok(tabMenuLabels.includes('new Claude'), 'Tabs menu owns new Claude');
    assert.ok(tabMenuLabels.includes('new Codex'), 'Tabs menu owns new Codex');
    assert.ok(tabMenuLabels.includes('new Xterm'), 'Tabs menu owns new Xterm');
    assert.ok(tabMenu.items.find(item => item.label === 'new Xterm').iconHtml.includes('app-menu-ui-icon-shell'), 'new Xterm uses the shared shell icon');
    assert.equal(tabMenuLabels.includes('Active'), false);
    assert.equal(tabMenuLabels.includes('Inactive'), false);
    assert.equal(tabMenuLabels.includes('Minimized'), false);
    assert.equal(tabMenuLabels.some(label => label.startsWith('No ')), false);
    assert.equal(tabMenu.items.filter(item => item.type === 'separator').length, 2, 'Tabs menu separators only split populated command groups');
    // P0 menu-bar: View → Sort tab list orders the Tabs navigator. 'name' sorts by label (deterministic
    // without session state); 'default' is identity. The View menu exposes the submenu with all 3 modes.
    api.setTabsMenuSortMode('default');
    assert.deepEqual(api.sortTabItemsForMenu(['3', '1', '2']), ['3', '1', '2'], 'default sort keeps the incoming order');
    api.setTabsMenuSortMode('name');
    assert.deepEqual(api.sortTabItemsForMenu(['3', '1', '2']), ['1', '2', '3'], 'name sort orders the tab list by label');
    api.setTabsMenuSortMode('default');
    const sortViewMenu = api.appMenuTree().find(menu => menu.id === 'view');
    const sortSubmenu = sortViewMenu.items.find(item => item.type === 'submenu' && item.label === api.t('menu.view.sortTabs'));
    assert.ok(sortSubmenu, 'View menu has a Sort tab list submenu');
    assert.deepEqual(sortSubmenu.items.map(item => item.label), [api.t('menu.view.sortTabs.default'), api.t('menu.view.sortTabs.attention'), api.t('menu.view.sortTabs.name')], 'the sort submenu offers default / needs-me / name');
    const layoutSubmenu = sortViewMenu.items.find(item => item.type === 'submenu' && item.label === api.t('menu.view.layout'));
    assert.ok(layoutSubmenu, 'View menu has a Layout submenu');
    assert.deepEqual(layoutSubmenu.items.map(item => item.label), api.layoutModeValues.map(mode => api.t(`menu.view.layout.${mode}`)), 'View layout options match the shared layout modes');
    assert.equal(layoutSubmenu.items.some(item => item.disabled), false, 'Every View layout option is implemented');
    const menus = api.appMenuTree();
    assert.equal(menus.map(menu => menu.label).join(','), 'File,View,tmux,Tabs,Help');
    assert.equal(menus.some(menu => menu.id === 'yolo'), false);
    const aboutHelpMenu = menus.find(menu => menu.id === 'help');
    assert.equal(aboutHelpMenu.items.some(item => item.type === 'submenu' && item.label === 'About'), false, 'Help menu no longer nests About as a submenu');
    const aboutCommand = aboutHelpMenu.items[aboutHelpMenu.items.length - 1];
    assert.equal(aboutCommand.type, 'command', 'Help menu ends with an About command');
    assert.equal(aboutCommand.label, 'About', 'About stays at the bottom of Help');
    assert.equal(aboutCommand.action, api.showAboutModal, 'About command opens the modal');
    assert.ok(api.aboutBrandHtml().includes('about-brand-yo'), 'About brand includes the large YO segment');
    assert.ok(api.aboutBrandHtml().includes('about-brand-lo'), 'About brand includes the large LO segment');
    assert.ok(api.aboutBrandHtml().includes('about-brand-x'), 'About brand includes the large x segment');
    api.showAboutModal();
    assert.ok(api.modalClassForTest().includes('open'), 'About opens the shared modal');
    assert.ok(api.modalClassForTest().includes('about-open'), 'About modal gets its compact layout class');
    assert.equal(api.modalTitleForTest(), api.t('menu.help.about'), 'About modal title is localized');
    assert.ok(api.modalBodyHtmlForTest().includes('about-brand-row'), 'About modal renders the large YOLOmux mark');
    assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${api.t('menu.help.about.datetime')}</dt>`), 'About modal contains localized date metadata');
    assert.ok(api.modalBodyHtmlForTest().includes('<dt>SHA</dt>'), 'About modal contains SHA metadata');
    assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${api.t('menu.help.about.version')}</dt>`), 'About modal contains localized version metadata');
    assert.ok(api.modalBodyHtmlForTest().includes('Keiven Chang'), 'About modal contains Keiven Chang');
    assert.ok(api.modalBodyHtmlForTest().includes('https://www.linkedin.com/in/keiven/'), 'Keiven Chang entry links to LinkedIn');
    assert.ok(fs.readFileSync('static/yolomux.js', 'utf8').includes('https://www.linkedin.com/in/keiven/'), 'About LinkedIn URL is bundled');
    assert.ok(api.modalBodyHtmlForTest().includes('https://github.com/keivenchang/yolomux'), 'DOIT.60: About modal links to the project GitHub repo');
    assert.ok(api.modalBodyHtmlForTest().includes('about-github'), 'DOIT.60: the GitHub link carries the about-github class');
    assert.ok(api.modalBodyHtmlForTest().includes(`>${api.t('menu.help.about.github')}</a>`), 'DOIT.60: the GitHub link uses the localized label');
    assert.ok(api.modalBodyHtmlForTest().includes('<span> - </span><a class="about-author about-github"'), 'About author and GitHub links render on one line');
    assert.equal(api.modalBodyHtmlForTest().includes('(to YOLOmux)'), false, 'About GitHub link no longer carries the old inline target suffix');
    assert.ok(api.modalBodyHtmlForTest().includes('https://polyformproject.org/licenses/noncommercial/1.0.0'), 'About modal links to the PolyForm Noncommercial license');
    assert.ok(api.modalBodyHtmlForTest().includes(`>${api.t('menu.help.about.license')}</a>`), 'About modal renders the localized license label');
    assert.ok(preferencesCss.includes('.modal.about-open .modal-dialog'), 'About modal has compact modal chrome through the shared dialog shell');
    assert.ok(/\.modal\s*\{[\s\S]*?z-index:\s*var\(--z-file-conflict-dialog\)/.test(preferencesCss), 'shared app modal sits above pane resizers, menus, and other pane-local overlays');
    assert.ok(/\.app-modal-overlay\s*\{[\s\S]*?position:\s*fixed[\s\S]*?inset:\s*0[\s\S]*?background:\s*var\(--app-modal-backdrop-bg\)/.test(preferencesCss), 'modal overlays share one dimmed full-viewport parent behavior');
    assert.ok(/\.modal\s*\{[\s\S]*?align-items:\s*center[\s\S]*?justify-items:\s*center[\s\S]*?padding:\s*var\(--popover-edge-gap\)/.test(preferencesCss), 'shared app modal centers dialogs in the viewport instead of top-anchoring them');
    assert.ok(/--app-modal-border-width:\s*max\(2px,\s*var\(--pane-split-gap\)\)/.test(preferencesCss), 'modal dialog borders use the shared pane-spacing-width token with a visible floor');
    assert.ok(/--app-modal-border-color:\s*color-mix\(in srgb,\s*var\(--pane-tab-panel-ring\) var\(--pane-active-ring-opacity\),\s*transparent\)/.test(preferencesCss), 'modal dialog borders use the same active pane ring color and opacity');
    assert.ok(/\.modal-dialog\s*\{[\s\S]*?border:\s*var\(--app-modal-border-width\) solid var\(--app-modal-border-color\)/.test(preferencesCss), 'shared app modal dialog uses the active-pane border token');
    assert.ok(/\.modal\.share-open \.modal-dialog\s*\{[\s\S]*?border:\s*var\(--app-modal-border-width\) solid var\(--app-modal-border-color\)/.test(preferencesCss), 'YO!share modal dialog keeps the active-pane border token');
    assert.ok(/\.command-palette-dialog\s*\{[\s\S]*?border:\s*var\(--app-modal-border-width\) solid var\(--app-modal-border-color\)/.test(preferencesCss), 'command palette dialog uses the active-pane border token');
    assert.ok(/\.keyboard-shortcuts-dialog\s*\{[\s\S]*?border:\s*var\(--app-modal-border-width\) solid var\(--app-modal-border-color\)/.test(preferencesCss), 'keyboard shortcuts dialog uses the active-pane border token');
    assert.ok(/\.file-editor-dialog\s*\{[\s\S]*?border:\s*var\(--app-modal-border-width\) solid var\(--app-modal-border-color\)/.test(preferencesCss), 'file editor dialog uses the active-pane border token');
    assert.ok(/\.session-rename-dialog\s*\{[\s\S]*?border:\s*var\(--app-modal-border-width\) solid var\(--app-modal-border-color\)/.test(preferencesCss), 'rename dialog uses the active-pane border token');
    assert.equal(/\.modal\.(?:about|share)-open::before/.test(preferencesCss), false, 'About and YO!share do not carry duplicate modal backdrop pseudo-elements');
    assert.ok(/\.modal\.share-open \.modal-dialog\s*\{[\s\S]*?max-height:\s*min\(80vh/.test(preferencesCss), 'YO!share modal is bounded to the viewport so long active-share lists cannot push the create form offscreen');
    assert.ok(/\.modal\.share-open #modalBody\s*\{[\s\S]*?overflow:\s*auto/.test(preferencesCss), 'YO!share modal body scrolls inside the shared modal panel');
    assert.ok(preferencesCss.includes('.about-brand-row'), 'About modal has a large brand row style');
    assert.equal(/\.about-brand-yo\s*\{[^}]*animation:\s*yolo-marker-rotate/.test(preferencesCss), false, 'About YO glyph is static — it no longer rotates (the working green ball is the only YO motion)');
    assert.ok(/\.about-brand-yo\s*\{[\s\S]*background:\s*var\(--pane-tab-yolo-bg\)/.test(preferencesCss), 'About YO glyph follows the active theme color');
    assert.ok(/\.about-brand-lo\s*\{[\s\S]*color:\s*var\(--brand-green\)/.test(preferencesCss), 'About LO stays brand green regardless of active color');
    const brandCss = fs.readFileSync('static/brand.css', 'utf8');
    assert.ok(/--brand-yolo-bg:\s*var\(--active-control-bg,\s*var\(--brand-primary-green\)\)/.test(brandCss), 'topbar YO follows the active color with green fallback');
    assert.ok(/\.brand-title \.brand-yolo\s*\{[\s\S]*background:\s*var\(--brand-yolo-bg\)/.test(brandCss), 'topbar YO background uses the active-color brand token');
    assert.ok(/--brand-primary-green:\s*var\(--brand-green,\s*#76b900\)/.test(brandCss), 'topbar YOLOmux LO stays brand green regardless of active color');
    assert.equal(/--brand-primary-green:\s*var\(--active-control-bg/.test(brandCss), false, 'topbar YOLOmux LO is not routed through the active color preference');
    assert.equal(api.testElementForId('closeModal').textContent || 'X', 'X', 'About modal close button is an X');
    const shellHtml = fs.readFileSync('yolomux_lib/web.py', 'utf8');
    assert.ok(shellHtml.includes('<section id="modal" class="modal app-modal-overlay">'), 'HTML shell routes About/share/transcript through the shared modal overlay parent');
    assert.ok(shellHtml.includes('<div class="modal-dialog">'), 'HTML shell gives the shared app modal a bounded dialog child');
    assert.ok(shellHtml.includes('<button id="closeModal" title="Close" aria-label="Close">X</button>'), 'HTML shell renders the modal close button as X');
    // File/View/Tabs/Help menu labels localize; tmux (a tool name) stays as-is.
    const zhHantMenu = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
    api.i18nSetCatalogForTest('zh-Hant', zhHantMenu);
    api.setActiveLocaleForTest('zh-Hant');
    assert.equal(api.appMenuTree().map(menu => menu.label).join(','), '檔案,檢視,tmux,分頁,說明', 'menu bar localizes (tmux unchanged)');
    api.showAboutModal();
    assert.equal(api.modalTitleForTest(), zhHantMenu['menu.help.about'], 'About modal title localizes to Chinese');
    assert.ok(api.modalBodyHtmlForTest().includes('about-brand-yo') && api.modalBodyHtmlForTest().includes('>優<'), 'Traditional Chinese About brand uses 優');
    assert.ok(api.modalBodyHtmlForTest().includes('>樂<'), 'Traditional Chinese About brand uses 樂');
    assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${zhHantMenu['menu.help.about.datetime']}</dt>`), 'Traditional Chinese About date label is localized');
    assert.ok(api.modalBodyHtmlForTest().includes('<dt>SHA</dt>'), 'SHA remains literal in Chinese About');
    assert.ok(api.modalBodyHtmlForTest().includes(`<dt>${zhHantMenu['menu.help.about.version']}</dt>`), 'Traditional Chinese About version label is localized');
    assert.equal(api.testElementForId('closeModal').getAttribute('aria-label'), zhHantMenu['common.close'], 'About close button aria-label localizes');
    api.setActiveLocaleForTest('en');
    assert.equal(menus.some(menu => menu.id === 'settings'), false);
    const fileMenu = menus.find(menu => menu.id === 'file');
    const fileMenuLabels = fileMenu.items.map(item => item.label).filter(Boolean);
    assert.equal(fileMenuLabels.includes('New tmux session'), false);
    assert.equal(fileMenuLabels.includes('Rename session'), false);
    assert.equal(fileMenuLabels.includes('Kill session'), false);
    assert.equal(fileMenuLabels.includes('Resume session'), false);
    assert.ok(fileMenuLabels.includes('YO!info'));
    assert.ok(fileMenuLabels.includes('Search & Runs'));
    assert.ok(fileMenuLabels.includes('YO!agent'));
    assert.ok(fileMenuLabels.includes('YO!share...'));
    assert.ok(fileMenuLabels.includes('Preferences'));
    assert.ok(fileMenuLabels.includes('YO!stats'));
    assert.ok(fileMenuLabels.indexOf('Preferences') < fileMenuLabels.indexOf('Log out'));
    assert.equal(fileMenuLabels[0], 'Open file', 'File -> Open file is the first File menu command');
    assert.deepStrictEqual(canonical(fileMenuLabels.slice(0, 7)), ['Open file', api.fileExplorerLabel(), 'Search & Runs', 'YO!info', 'YO!agent', 'YO!stats', 'YO!share...'], 'File menu starts with Open file, then Finder/Search/data/YO commands');
    const firstYoIndex = fileMenuLabels.indexOf('YO!info');
    assert.deepStrictEqual(canonical(fileMenuLabels.slice(firstYoIndex, firstYoIndex + 4)), ['YO!info', 'YO!agent', 'YO!stats', 'YO!share...'], 'File menu keeps the YO!* selections adjacent with YO!stats after YO!agent');
    assert.ok(fileMenuLabels.indexOf('YO!share...') < fileMenuLabels.indexOf('Preferences'), 'YO!share stays before Preferences');
    assert.deepStrictEqual(canonical(fileMenu.items.slice(-4).map(item => item.type === 'separator' ? '---' : item.label)), ['YO!share...', 'Preferences', '---', 'Log out']);
    for (const label of [api.fileExplorerLabel(), 'YO!info', 'Search & Runs', 'YO!agent', 'Open file', 'YO!share...', 'Preferences', 'YO!stats', 'Log out']) {
      const item = fileMenu.items.find(candidate => candidate.label === label);
      assert.ok(item?.iconHtml, `File menu ${label} uses the shared icon row`);
      assert.equal(item.className || '', '', `File menu ${label} does not use the raised tab-row scaffold`);
    }
    const finderMenuItem = fileMenu.items.find(candidate => candidate.label === api.fileExplorerLabel());
    assert.equal(finderMenuItem.detail, api.appShortcutText('B'), 'File -> Finder shows the same Cmd/Ctrl-B shortcut that toggles the Finder');
    const macFileMenuApi = loadYolomux('?platform=mac', ['1'], 'http:', 'MacIntel');
    const macFinderMenuItem = macFileMenuApi.appMenuTree().find(menu => menu.id === 'file')?.items.find(candidate => candidate.label === 'Finder');
    assert.equal(macFinderMenuItem?.detail, '⌘+B', 'File -> Finder shows Cmd-B on Mac');
    const shareMenuItem = fileMenu.items.find(candidate => candidate.label === 'YO!share...');
    assert.equal(shareMenuItem.detail, 'sharing', 'YO!share menu row avoids target/session counts');
    const shareCommandItem = api.commandPaletteCommandItems().find(candidate => candidate.label === 'File / YO!share...');
    assert.equal(shareCommandItem?.keybinding, 'Ctrl+K', 'YO!share is discoverable from the command surface with the direct share shortcut');
    const shareCreateHtml = api.shareCreateFormHtmlForTest();
    assert.ok(/name="ttl_minutes"[^>]*type="number"[^>]*min="1"[^>]*max="480"[^>]*step="1"/.test(shareCreateHtml), 'YO!share max time is a typable minutes number field');
    assert.equal(/<select[^>]*name="ttl_seconds"/.test(shareCreateHtml), false, 'YO!share max time is not a dropdown');
    assert.ok(/name="debug_profile"[^>]*type="checkbox"/.test(shareCreateHtml), 'YO!share create form exposes the opt-in debug/profiling upload checkbox');
    const sharePayload = api.shareCreatePayloadFromFormForTest({elements: {
      ttl_minutes: {value: '15'},
      max_viewers: {value: '7'},
      read_only: {checked: true},
      debug_profile: {checked: true},
      scheme: {value: 'http'},
    }});
    assert.equal(sharePayload.ttl_seconds, 900, 'typed YO!share max-time minutes are converted to seconds for the API');
    assert.equal(sharePayload.max_viewers, 7, 'YO!share create payload still reads the viewer cap');
    assert.equal(sharePayload.debug_profile, true, 'YO!share create payload carries the debug/profiling upload opt-in');
    assert.ok(sharePayload.ui_state?.finder, 'YO!share create payload builds UI state without stale Finder fields');
    assert.equal('textWraps' in sharePayload.ui_state, false, 'YO!share create payload omits full wrapped-text metrics');
    assert.equal('scroll' in sharePayload.ui_state, false, 'YO!share create payload omits full scroll replay frames');
    assert.equal('branchRows' in sharePayload.ui_state.info, false, 'YO!share create payload omits bulky YO!info rows');
    assert.equal('modes' in sharePayload.ui_state.editor, false, 'YO!share create payload omits bulky editor mode rows until the websocket full-state publish');
    {
      const heavyRoot = api.appRootForTest();
      for (let index = 0; index < 80; index += 1) {
        const heavyTextarea = new TestElement(`share-heavy-wrap-${index}`, 'textarea');
        heavyTextarea.dataset.settingPath = `share.heavy.${index}`;
        heavyTextarea.value = `wrapped ${index} ${'x'.repeat(1000)}`;
        heavyTextarea.textContent = heavyTextarea.value;
        heavyTextarea.clientWidth = 640;
        heavyTextarea.clientHeight = 96;
        heavyTextarea.scrollWidth = 644;
        heavyTextarea.scrollHeight = 320 + index;
        heavyTextarea.rect = {left: 20, top: 20 + index, right: 660, bottom: 116 + index, width: 640, height: 96};
        heavyRoot.appendChild(heavyTextarea);
      }
      const fullUiStateBytes = JSON.stringify(api.shareUiStateSnapshotForTest()).length;
      const createPayloadBytes = JSON.stringify(api.shareCreatePayloadFromFormForTest({elements: {
        ttl_minutes: {value: '15'},
        max_viewers: {value: '7'},
        read_only: {checked: true},
        debug_profile: {checked: false},
        scheme: {value: 'http'},
      }})).length;
      assert.ok(fullUiStateBytes > 16 * 1024, 'test fixture produces a full YO!share UI snapshot larger than the /api/share create limit');
      assert.ok(createPayloadBytes < 16 * 1024, 'YO!share create payload stays below the server body limit even when the live UI snapshot is large');
    }
    {
      const shareSource = fs.readFileSync('static/yolomux.js', 'utf8');
      const timingSource = fs.readFileSync('static_src/js/yolomux/02_timing.js', 'utf8');
      const bootstrapSource = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
      const terminalSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
      assert.ok(/const uiDelayMs = Object\.freeze\(\{[\s\S]*shareViewerStatusBackupRefresh:\s*30001[\s\S]*shareHostStatusBackupRefresh:\s*3001[\s\S]*shareRemoteResizeAfterSocketOpen:\s*50/.test(timingSource), 'R10/MV-3: backend-facing backup polls use odd cadences through the shared timing partial');
      assert.ok(/serverWatchRenew:\s*60001[\s\S]*serverWatchDebounce:\s*300[\s\S]*shareGeometryDigestPublish:\s*2001/.test(timingSource), 'MV-3: backend-facing renew and geometry publish cadences stay odd while UI delays stay round');
      assert.ok(/fileExplorerFilesystemKeyframeMs = 60001/.test(bootstrapSource), 'MV-3: filesystem watch keyframes use an odd cadence');
      assert.ok(/shareDebugProfileUploadMinIntervalMs:\s*5000/.test(timingSource) && /autoApproveDisconnectedPollMs:\s*5003/.test(timingSource), 'R10: hardcoded debug upload and odd fallback poll timings are owned by the shared timing partial');
      assert.equal(/const shareDebugProfileUploadMinIntervalMs = 5000|const autoApproveDisconnectedPollMs = 5003/.test(bootstrapSource), false, 'R10: bootstrap no longer owns hardcoded timing literals');
      assert.equal(/const shareViewerStatusBackupRefreshMs = \d+|const shareHostStatusBackupRefreshMs = \d+|const shareReplayKeyframeRequestInitialBackoffMs = 5000|const shareGeometryResyncMinIntervalMs = 10000|scheduleRemoteResize\(session, 50\)/.test(terminalSource), false, 'R10: terminal boot no longer owns share/replay timing literals');
      assert.ok(shareSource.includes("node.className = 'app-modal-overlay keyboard-shortcuts-overlay'"), 'keyboard shortcuts use the shared modal overlay parent');
      assert.ok(shareSource.includes("node.className = 'app-modal-overlay command-palette'"), 'command palette uses the shared modal overlay parent');
      assert.ok(/backdrop\.className = `app-modal-overlay file-editor-dialog-backdrop/.test(shareSource), 'file editor dialogs use the shared modal overlay parent');
      assert.ok(shareSource.includes("overlay.className = 'app-modal-overlay session-rename-backdrop'"), 'rename dialog uses the shared modal overlay parent');
      assert.equal(/uploadedFilesCollapsed/.test(shareSource), false, 'YO!share source has no stale uploadedFilesCollapsed state reference');
      assert.ok(/const shareMenuActive = shareViewMode \|\| shareHasActiveShare\(\)/.test(shareSource), 'YO!share menu active state includes share-view and host active-share state');
      assert.ok(/detail:\s*shareMenuActive \|\| shareCanOpen \? t\('share\.menu\.sharing'\) : t\('share\.noSession'\)/.test(shareSource), 'enabled YO!share menu row says only "sharing", with no count');
      assert.ok(/function shareSessionsFromLayout\(slots = layoutSlots\)[\s\S]*paneItems\(slots\)[\s\S]*isTmuxSession/.test(shareSource), 'YO!share gathers every tmux session from the current pane/tab layout');
      assert.ok(/function shareCreatePayloadFromForm[\s\S]*const sharedSessions = shareSessionsFromLayout\(\)[\s\S]*sessions:\s*sharedSessions\.length \? sharedSessions : \[targetSession\]\.filter\(Boolean\)[\s\S]*layout:\s*seed\.layout[\s\S]*tabs:\s*seed\.tabs[\s\S]*finder:\s*shareFinderSeed\(\)[\s\S]*ui_state:\s*shareCreateUiStateSnapshot\(\)/.test(shareSource), 'YO!share create payload carries all shared sessions plus layout/tabs/Finder and compact UI-state seed');
      assert.ok(/function shareCreatePayloadFromForm[\s\S]*debug_profile:\s*form\?\.elements\?\.debug_profile\?\.checked === true/.test(shareSource), 'YO!share create payload includes only explicit debug/profiling upload opt-in');
      assert.ok(/function shareDebugProfileUploadPayload[\s\S]*shareRedactDiagnosticValue[\s\S]*async function shareUploadDebugProfile[\s\S]*shareDebugProfileUploadEnabled\(\)[\s\S]*apiFetchJson\('\/api\/share\/debug-profile'/.test(shareSource), 'YO!share debug/profiling uploads are opt-in, sent to the share debug endpoint, and client-redacted');
      assert.ok(/async function boot\(\)[\s\S]*shareBootstrap\?\.uiState[\s\S]*await applyShareUiState/.test(shareSource), 'share-view boot applies the server UI-state bootstrap after the initial panes render');
      assert.ok(/function shareBootstrapLayoutParams\(\)[\s\S]*shareBootstrap\.layout[\s\S]*shareBootstrap\.tabs[\s\S]*shareBootstrap\.sessions[\s\S]*function initialLayoutSlots\(\)[\s\S]*const shareParams = shareBootstrapLayoutParams\(\)[\s\S]*const params = shareParams \|\| new URLSearchParams[\s\S]*preserveMissingFileExplorer: shareParams !== null/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'share-view boot uses the server share bootstrap layout before the browser query string and preserves a host-minimized Finder');
      assert.ok(/function paintInitialAppShell\(\)[\s\S]*renderSessionButtons\(\)[\s\S]*renderPanels\(\[\], \{prune: false\}\)[\s\S]*initialAppShellPainted = true[\s\S]*async function boot\(\)[\s\S]*bindClipboardPaste\(\);\s*paintInitialAppShell\(\);\s*if \(!shareViewMode\) \{[\s\S]*await refreshTranscripts\(\{refreshAuto: false\}\)[\s\S]*\} else \{[\s\S]*transcriptMeta = \{session_order: sessions\.slice\(\)[\s\S]*await refreshTranscripts\(\{refreshAuto: false, refreshActivity: false\}\)/.test(shareSource), 'boot paints the saved shell before transcript metadata loads, and share-view uses a stub only until scoped metadata loads');
      assert.ok(/function updateActiveSessionParam\(\)[\s\S]*if \(shareViewMode\) return/.test(fs.readFileSync('static/yolomux.js', 'utf8')), 'share-view boot does not rewrite the share URL from local layout state');
      assert.ok(/function syncShareProtocolControls[\s\S]*!readOnly\.checked[\s\S]*https\.checked = true[\s\S]*http\.disabled = true/.test(shareSource), 'YO!share modal locks write mode to https');
      assert.ok(/let activeShares = \[\]/.test(shareSource), 'YO!share tracks active shares as a list for concurrent shares');
      assert.ok(/function refreshActiveShare\(options = \{\}\)[\s\S]*setActiveShares\(normalizeShareListPayload[\s\S]*ensureShareHostSockets\(\)/.test(shareSource), 'YO!share status refresh consumes the active share list and opens per-token host sockets');
      assert.ok(/function renderShareManageView\(errorText = ''\)[\s\S]*share-create-panel[\s\S]*shareCreateFormHtml[\s\S]*share-active-panel[\s\S]*share-entry-list/.test(shareSource), 'YO!share manage view sections New share before Active share URLs');
      assert.ok(/function shareEntryHtml\(share\)[\s\S]*share-url-primary[\s\S]*share-url-primary-head[\s\S]*<span class="share-url-control">[\s\S]*<input type="text" readonly value="\$\{esc\(share\.url\)\}" data-share-secret>[\s\S]*share-url-copy-button[\s\S]*data-share-copy[\s\S]*data-share-secret/.test(shareSource), 'YO!share manage rows make the active URL prominent while keeping the copy icon immediately beside a redacted URL input');
      const replayRegistrationFindings = source => {
        const checks = [
          ['private/redact flags are dropped', /function shareReplayElementRedactionAction\(element\)[\s\S]*data-share-private[\s\S]*data-share-redact/],
          ['secret/password fields become placeholders', /function shareReplayElementRedactionAction\(element\)[\s\S]*data-share-secret[\s\S]*'placeholder'/],
          ['mutation ignores private volatile terminal and Finder surfaces', /function shareReplayMutationNodeIsIgnored\(node\)[\s\S]*'\.terminal'[\s\S]*'\.xterm'[\s\S]*'\[data-share-private\]'[\s\S]*'\[data-share-redact\]'[\s\S]*'\[data-share-volatile\]'[\s\S]*'\.file-explorer-panel'/],
          ['token-bearing attributes are rejected', /function shareReplayAttributeIsTokenBearing\(name = ''\)[\s\S]*token[\s\S]*sharetoken[\s\S]*secret[\s\S]*password/],
          ['share URLs and dangerous schemes are rejected', /function shareReplayUrlAttributeIsUnsafe\(name = '', value = ''\)[\s\S]*share[\s\S]*#t=[\s\S]*javascript/],
          ['terminal containers serialize as placeholders', /function shareReplayTerminalPlaceholderForElement\(element, nodeId\)[\s\S]*class:\s*'share-terminal-placeholder'[\s\S]*shareMirrorProtocol\.terminalPlaceholder\.dataset/],
          ['share URL controls are marked secret', /function shareEntryHtml\(share\)[\s\S]*value="\$\{esc\(share\.url\)\}" data-share-secret/],
          ['app-visible popup root is inside appRoot', /function appOverlayRootElement\(\)[\s\S]*overlay\.id = 'appOverlayRoot'[\s\S]*root\.appendChild\(overlay\)/],
        ];
        const findings = checks.filter(check => !check[1].test(source)).map(check => check[0]);
        if (/document\.body\.appendChild\((?:menu|popover|overlay|backdrop|modal|palette)\)/.test(source)) findings.push('popup hosts use app overlay');
        return findings;
      };
      const registeredReplayFixture = `
        function shareReplayElementRedactionAction(element) { if (shareReplayElementHasFlag(element, 'data-share-private') || shareReplayElementHasFlag(element, 'data-share-redact')) return 'drop'; if ((tag === 'input' && type) || shareReplayElementHasFlag(element, 'data-share-secret')) return 'placeholder'; }
        function shareReplayMutationNodeIsIgnored(node) { const selectors = ['.terminal', '.xterm', '[data-share-private]', '[data-share-redact]', '[data-share-volatile]', '.file-explorer-panel']; }
        function shareReplayAttributeIsTokenBearing(name = '') { return /token|sharetoken|secret|password/.test(name); }
        function shareReplayUrlAttributeIsUnsafe(name = '', value = '') { return /\\/share\\/[A-Za-z0-9_-]+/.test(value) || /#t=/.test(value) || /javascript/.test(value); }
        function shareReplayTerminalPlaceholderForElement(element, nodeId) { return {node: {attrs: {class: 'share-terminal-placeholder', [shareMirrorProtocol.terminalPlaceholder.dataset]: '1'}}}; }
        function shareEntryHtml(share) { return '<input type="text" readonly value="\${esc(share.url)}" data-share-secret>'; }
        function appOverlayRootElement() { overlay.id = 'appOverlayRoot'; root.appendChild(overlay); }
      `;
      const unregisteredReplayFixture = `
        function shareReplayElementRedactionAction(element) { return 'keep'; }
        function shareReplayMutationNodeIsIgnored(node) { return false; }
        function shareReplayAttributeIsTokenBearing(name = '') { return false; }
        function shareReplayUrlAttributeIsUnsafe(name = '', value = '') { return false; }
        function shareEntryHtml(share) { return '<input value="\${esc(share.url)}">'; }
        document.body.appendChild(popover);
      `;
      assert.deepStrictEqual(replayRegistrationFindings(registeredReplayFixture), [], 'DOIT.72 P6.1: replay registration source guard accepts a fully registered fixture');
      assert.ok(replayRegistrationFindings(unregisteredReplayFixture).length >= 7, 'DOIT.72 P6.1: replay registration source guard rejects missing redaction, terminal, token, and popup registration');
      assert.deepStrictEqual(replayRegistrationFindings(shareSource), [], 'DOIT.72 P6.1: mirrored private surfaces, terminal containers, token-bearing fields, and app-visible popup hosts are registered for replay/redaction');
      assert.ok(/function shareEntryHtml\(share\)[\s\S]*shareViewerListHtml\(share\)[\s\S]*function shareViewerListHtml\(share\)[\s\S]*share\.users\.duration[\s\S]*share\.users\.ip[\s\S]*share\.users\.browser/.test(shareSource), 'YO!share manage rows render a user list with connected time, IP, and browser columns');
      assert.ok(/function normalizeSharePayload\(payload\)[\s\S]*viewerDetails:\s*normalizeShareViewerDetails\(payload\)/.test(shareSource), 'YO!share payload normalization preserves active viewer details for the manage modal');
      assert.ok(/<button type="button" class="danger share-stop-inline" data-share-stop>/.test(shareSource), 'YO!share manage rows place Stop inline with mode/protocol metadata');
      assert.ok(/async function stopActiveShare\(tokenOrShortId = ''\)[\s\S]*JSON\.stringify\(\{token: target\}\)/.test(shareSource), 'YO!share stop is scoped to a selected token when a row is stopped');
      assert.ok(/function apiFetch\(url, options = \{\}\)[\s\S]*X-Share-Token/.test(shareSource), 'share-view API calls attach the fragment token as X-Share-Token');
      assert.ok(/const shareWriteMode = shareViewMode && shareBootstrap\?\.mode === 'rw'/.test(shareSource), 'share-view write mode is explicit and token-driven');
      assert.ok(/disableStdin:\s*readOnlyMode && !shareWriteMode/.test(shareSource), 'rw share-view terminals are created with stdin enabled');
      assert.ok(/function insertIntoTerminal\(session, text\)[\s\S]*readOnlyMode && !shareWriteMode/.test(shareSource), 'rw share-view can send terminal insertions while ro share-view stays blocked');
      assert.ok(/function handleTerminalData\(session, data\)[\s\S]*readOnlyMode && !shareWriteMode/.test(shareSource) && shareSource.includes('term.onData(data => handleTerminalData(session, data));'), 'rw share-view can type into the focused terminal only through terminal input frames');
      assert.ok(shareSource.includes('function renderShareStatusPill') && shareSource.includes('share.pill'), 'host topbar renders the YO!share status pill');
      assert.ok(shareSource.includes('share.pillMultiple'), 'host topbar aggregates multiple active YO!share URLs into one status pill');
      assert.ok(/const shareViewerId = \(\(\) => \{[\s\S]*sessionStorage\.getItem\(key\)[\s\S]*randomShareViewerId/.test(shareSource), 'share-view pages keep one viewer id across all terminal websockets in the page');
      assert.ok(/const shareClientId = \(\(\) => \{[\s\S]*shareViewMode[\s\S]*sessionStorage\.getItem\(key\)[\s\S]*randomShareViewerId/.test(shareSource), 'every host/viewer browser gets a stable share client id for echo suppression and cursor color');
      assert.ok(/function wsUrl\(session\)[\s\S]*shareViewMode[\s\S]*URLSearchParams\(\{session, token: shareToken, viewer: shareViewerId\}\)[\s\S]*\/ws\/share-view\?/.test(shareSource), 'share-view terminals use /ws/share-view with the fragment token and shared viewer id');
      assert.ok(/function terminalCanPublishRemoteSize\(\)[\s\S]*!shareViewMode/.test(shareSource), 'share-view terminals cannot send remote resize frames');
      assert.ok(/function shareHostWsUrl\(token\)[\s\S]*URLSearchParams\(\{share: token, client: shareClientId\}\)[\s\S]*\/ws\/share-host\?\$\{params\.toString\(\)\}/.test(shareSource), 'share hosts publish UI state through /ws/share-host with a sender client id');
      assert.ok(/function shareViewerUiWsUrl\(token\)[\s\S]*\/ws\/share-ui\?/.test(shareSource), 'share-view clients receive UI state through the share-scoped /ws/share-ui socket');
      assert.ok(/function startShareStatusRefresh\(\)[\s\S]*if \(shareViewMode\)[\s\S]*ensureShareHostSockets\(\)/.test(shareSource), 'read-only share viewers also open the UI socket so editor/Finder-only layouts receive mirror frames');
      assert.ok(/shareViewerStatusBackupRefreshMs:\s*uiDelayMs\.shareViewerStatusBackupRefresh/.test(timingSource), 'read-only share viewers use push for live state and keep /api/share status polling as a low-frequency backup through the shared timing owner');
      assert.ok(/function shareCanPublishUi\(\)[\s\S]*applyingShareRemoteUiState[\s\S]*shareReplayViewerModeEnabled\(\)[\s\S]*shareViewMode[\s\S]*shareWriteMode[\s\S]*shareToken[\s\S]*shareHasActiveShare/.test(shareSource), 'DOIT.72 P5.3: share UI publication is allowed for hosts with shares and legacy rw viewers, but replay viewers cannot publish semantic UI frames');
      assert.ok(/function beginShareRemoteUiApply\(\)[\s\S]*applyingShareRemoteUiState[\s\S]*\+ 1[\s\S]*return \(\) => \{[\s\S]*applyingShareRemoteUiState[\s\S]*- 1/.test(shareSource), 'remote UI apply uses a depth counter so overlapping async/sync mirror applies cannot leave rw viewers non-publishable');
      assert.ok(/function sharePublish\(type, payload = \{\}, options = \{\}\)[\s\S]*shareBuildUiMessage\(type, payload, \{\.\.\.options, commitSequence: false\}\)[\s\S]*const serialized = JSON\.stringify\(message\)[\s\S]*shareCommitBuiltUiMessage\(message\)[\s\S]*socket\.send\(serialized\)/.test(shareSource), 'sharePublish fans host and rw-viewer UI-state publication with one serialized message and sender ids');
      assert.ok(/function shareBuildUiMessage\(type, payload = \{\}, options = \{\}\)[\s\S]*shareNextMirrorFrameMetadata\(type, options\)/.test(shareSource), 'DOIT.72 P0.1: sharePublish stamps mirror metadata through one message builder');
      assert.ok(/function applyShareUiMessage\(message\)[\s\S]*shareDropStaleMirrorFrame\(message\)[\s\S]*return/.test(shareSource), 'DOIT.72 P0.1: share viewers drop stale sequenced mirror frames before applying payloads');
      assert.ok(/function shareBaseUiStateSnapshot\(options = \{\}\)[\s\S]*viewport:\s*shareViewportSnapshot\(\)[\s\S]*appearance:\s*shareAppearanceSnapshot\(\)[\s\S]*terminalDims:\s*shareTerminalDimensionsSnapshot\(\)[\s\S]*chrome:[\s\S]*tabMetaVisible:[\s\S]*infoSubTab:[\s\S]*autoApprove:\s*shareAutoApproveStateSnapshot\(\)[\s\S]*info:\s*shareInfoStateSnapshot\(\{includeRows: !compact\}\)[\s\S]*finder:\s*shareFinderStateSnapshot\(\{compact\}\)[\s\S]*editor:\s*shareEditorStateSnapshot\(\{compact\}\)[\s\S]*preferences:\s*sharePreferencesStateSnapshot\(\{compact\}\)/.test(shareSource), 'YO!share snapshots chrome, YO!info, Finder/Differ/Tabber, editor, and Preferences state through shared compact/full helpers');
      assert.ok(/function shareCreateUiStateSnapshot\(\)[\s\S]*shareBaseUiStateSnapshot\(\{compact: true\}\)/.test(shareSource), 'YO!share create uses a compact UI-state seed');
      assert.ok(/function shareUiStateSnapshot\(\)[\s\S]*shareBaseUiStateSnapshot\(\{compact: false\}\)[\s\S]*textWraps:\s*shareWrappedTextDigestSnapshot\(\)[\s\S]*scroll:\s*shareScrollStateSnapshot\(\)/.test(shareSource), 'YO!share full UI snapshots include host YO state, wrapped-control metrics, and current scroll offsets for late viewers');
      assert.ok(/function shareEditorModesSnapshot\(\)[\s\S]*entry\.viewState[\s\S]*entry\.diffFromRef[\s\S]*entry\.diffToRef[\s\S]*entry\.diffExpandUnchanged/.test(shareSource), 'DOIT.68: YO!share editor snapshots carry diff refs, expand state, and editor scroll state');
      assert.ok(/function shareTerminalDimensionsSnapshot\(\)[\s\S]*term\?\.rows[\s\S]*term\?\.cols/.test(shareSource), 'M4: YO!share snapshots live host terminal dimensions for mirror viewers');
      assert.ok(/function shareBaseUiStateSnapshot\(options = \{\}\)[\s\S]*viewport:\s*shareViewportSnapshot\(\)[\s\S]*appearance:\s*shareAppearanceSnapshot\(\)/.test(shareSource), 'M1: YO!share snapshots host viewport and appearance geometry inputs');
      assert.ok(/function shareAppearanceSnapshot\(\)[\s\S]*locale:[\s\S]*i18nActiveLocaleId[\s\S]*languagePref:[\s\S]*initialSetting\('general\.language'/.test(shareSource), 'DOIT.67: YO!share appearance snapshots carry locale inputs for live and late-join viewers');
      assert.ok(/function shareBaseUiStateSnapshot\(options = \{\}\)[\s\S]*terminalDims:\s*shareTerminalDimensionsSnapshot\(\)/.test(shareSource), 'M4: YO!share ui_state carries terminal dimensions for late-joining viewers');
      assert.ok(/let shareViewFit = normalizeShareViewFit\(storageGet\(shareViewFitStorageKey\) \|\| initialSetting\('share\.view_fit', 'cover'\)\)/.test(shareSource), 'M3: share mirror fit defaults through share.view_fit with cover fallback');
      assert.ok(/function shareMirrorFitTransform\(hostViewport, clientViewport, fit = shareViewFit\)[\s\S]*mode === 'contain' \? Math\.min\(scaleX, scaleY\) : Math\.max\(scaleX, scaleY\)[\s\S]*tx: \(client\.width - width\) \/ 2/.test(shareSource), 'M3: cover/contain transform math is centralized');
      assert.ok(/function applyShareMirrorTransform\(\)[\s\S]*nativeViewport\(\)[\s\S]*--share-mirror-scale[\s\S]*--share-mirror-tx[\s\S]*--share-mirror-ty/.test(shareSource), 'M3: share view applies one root transform from host to client viewport');
      assert.ok(/function scheduleShareUiStatePublish\(options = \{\}\)[\s\S]*shareUiStatePublishTimer[\s\S]*sharePublishUiState\(options\)/.test(shareSource), 'YO!share debounces host UI-state publication');
      assert.ok(/function applyShareChromeState\(chrome = \{\}\)[\s\S]*tabMetaVisible = chrome\.tabMetaVisible !== false[\s\S]*infoPanelSubTab = normalizedInfoSubTab\(chrome\.infoSubTab\)[\s\S]*renderYoagentPanel/.test(shareSource), 'YO!share applies host-owned tab metadata and legacy YO!agent chrome state');
      assert.ok(/function setFileEditorDiffExpandUnchangedForItem[\s\S]*fileEditorDiffExpandOverrides\.set[\s\S]*scheduleShareUiStatePublish\(\)/.test(shareSource), 'per-editor diff expansion changes publish the mirrored editor state');
      assert.ok(/function setRepoDiffRefs\(repo, fromRef, toRef, options = \{\}\)[\s\S]*renderFileExplorerChangesPanels\(\{force: true\}\)[\s\S]*scheduleShareTopologySnapshot\('differ-refs'\)[\s\S]*return true/.test(shareSource), 'Differ FROM/TO changes schedule a full share UI-state snapshot through the topology scheduler');
      assert.ok(/function scheduleShareViewportPublish\(\)[\s\S]*shareViewMode[\s\S]*sharePublish\('viewport', shareViewportSnapshot\(\)\)[\s\S]*}, 150\)/.test(shareSource), 'M1: host resize publishes a debounced viewport frame');
      assert.ok(/function scheduleShareAppearancePublish\(options = \{\}\)[\s\S]*shareViewMode[\s\S]*sharePublish\('appearance', shareAppearanceSnapshot\(\)\)[\s\S]*scheduleShareTopologySnapshot\(options\.reason \|\| 'appearance'\)/.test(shareSource), 'M1: host settings apply publishes appearance geometry and a full topology snapshot');
      assert.ok(/async function applyLocale\(locale\)[\s\S]*rerenderForLocale\(\{localeChange: true\}\)[\s\S]*scheduleShareAppearancePublish\(\)[\s\S]*scheduleSharePopupLayerPublish\(\{immediate: true\}\)/.test(shareSource), 'DOIT.67: host locale switches publish appearance and refreshed popup-layer frames');
      assert.ok(/window\.addEventListener\('resize', \(\) => \{[\s\S]*scheduleShareViewportPublish\(\)/.test(shareSource), 'M1: the host resize handler schedules viewport publication');
      assert.ok(/async function applyShareUiState\(payload = \{\}\)[\s\S]*applyShareInfoState\(payload\.info \|\| \{\}\)[\s\S]*applyShareEditorState\(payload\.editor \|\| \{\}\)[\s\S]*applySharePreferencesState\(payload\.preferences \|\| \{\}\)[\s\S]*await applyShareFinderState\(payload\.finder \|\| \{\}\)/.test(shareSource), 'share viewers apply mirrored YO!info, editor, Preferences, and Finder/Differ/Tabber state');
      assert.ok(/function shareReadOnlyFinderStateIsHostOwned\(\)[\s\S]*shareViewMode && !shareWriteMode && !applyingShareRemoteUiState/.test(shareSource), 'read-only share clients treat Finder root and expansion as host-owned between host frames');
      assert.ok(/function scheduleFileExplorerActiveTabSync\(preferredItem = null, options = \{\}\)[\s\S]*if \(shareReadOnlyFinderStateIsHostOwned\(\)\) return/.test(shareSource), 'read-only share clients ignore local active-tab sync that would jump Finder to the client context');
      assert.ok(/async function applyShareFinderState\(finder = \{\}\)[\s\S]*previousRoot !== normalizedRoot[\s\S]*openFileExplorerAt\(normalizedRoot[\s\S]*else \{[\s\S]*refreshFileExplorerTreesInPlace/.test(shareSource), 'read-only share clients refresh same-root Finder frames in place instead of reopening and flashing collapsed');
      assert.ok(/async function applyShareUiState\(payload = \{\}\)[\s\S]*applyShareViewportState\(payload\.viewport \|\| \{\}\)[\s\S]*applyShareAppearanceState\(payload\.appearance \|\| \{\}\)/.test(shareSource), 'M1: share viewers apply geometry inputs before semantic UI state');
      assert.ok(/async function applyShareUiState\(payload = \{\}\)[\s\S]*applyShareTerminalDimensionsState\(payload\.terminalDims \|\| \[\]\)/.test(shareSource), 'M4: share viewers apply host terminal dimensions from the UI state');
      assert.ok(/function applyShareUiMessage\(message\)[\s\S]*message\.type === 'ui-state'[\s\S]*applyShareUiState\(payload\)/.test(shareSource), 'share viewers consume live ui-state frames');
      const uiStateIndex = shareSource.indexOf("message.type === 'ui-state'");
      const remoteApplyIndex = shareSource.indexOf('beginShareRemoteUiApply()', shareSource.indexOf('function applyShareUiMessage'));
      assert.ok(uiStateIndex >= 0 && remoteApplyIndex >= 0 && uiStateIndex < remoteApplyIndex, 'live ui-state frames are dispatched before the outer remote-apply guard so rw viewers do not get stuck non-publishable');
      assert.ok(/function applyShareUiMessage\(message\)[\s\S]*message\.type === 'viewport'[\s\S]*applyShareViewportState\(payload\)[\s\S]*message\.type === 'appearance'[\s\S]*applyShareAppearanceState\(payload\)/.test(shareSource), 'M1: share viewers consume live viewport and appearance frames');
      assert.ok(/async function applyShareUiState\(payload = \{\}\)[\s\S]*applyShareAutoApproveState\(payload\.autoApprove \|\| \{\}\)[\s\S]*applyShareTextWrapMetrics\(payload\.textWraps \|\| \[\]\)[\s\S]*applyShareScrollSnapshot\(payload\.scroll \|\| \[\]\)/.test(shareSource), 'YO!share clients apply host YO state, wrapped-control metrics, and full-snapshot scroll after semantic panes render');
      assert.ok(/shareReplayKeyframeRequestInitialBackoffMs:\s*5000/.test(timingSource), 'YO!share replay keyframe requests start with a five-second retry floor through the shared timing owner');
      assert.ok(/shareReplayKeyframeRequestMinIntervalMs:\s*5000/.test(timingSource), 'YO!share replay keyframe requests are rate-limited to at most once every five seconds through the shared timing owner');
      assert.ok(/const shareReplayHostKeyframeMinIntervalMs = shareReplayKeyframeRequestMinIntervalMs/.test(shareSource), 'YO!share hosts coalesce repair keyframes on the same five-second floor as viewer requests');
      assert.ok(/function sharePublishDomKeyframe\(reason = 'manual-debug'\)[\s\S]*cleanReason === 'manual-debug' \|\| cleanReason === 'topology' \|\| cleanReason === 'join'[\s\S]*shareReplayHostKeyframeMinIntervalMs/.test(shareSource), 'YO!share topology and join keyframes bypass the repair floor while replay-error/backpressure repair stays throttled');
      assert.equal(/targetViewer && reason === 'gap' \? 'join' : reason/.test(shareSource), false, 'YO!share viewer gap repair requests must not be rewritten to join and bypass the host repair floor');
      assert.ok(/if \(message\.type === shareMirrorProtocol\.frames\.domKeyframeRequest\) \{[\s\S]*const reason = shareReplayKeyframeReason[\s\S]*sharePublishDomKeyframe\(reason\)/.test(shareSource), 'YO!share host keyframe-request handling preserves the repair reason so gap/backpressure repairs are throttled');
      assert.ok(/shareGeometryResyncMinIntervalMs:\s*10000/.test(timingSource), 'YO!share semantic geometry resync is rate-limited to at most once every ten seconds through the shared timing owner');
      assert.ok(/function handleGlobalShortcutKeydown\(event\)[\s\S]*if \(key === 'k'\)[\s\S]*if \(event\.shiftKey\) startPinTabShortcutChord\(\);[\s\S]*else showShareModal\(\);/.test(shareSource), 'Cmd/Ctrl-K opens YO!share directly while Shift+Cmd/Ctrl-K keeps the pin-tab chord');
      assert.ok(/function startPinTabShortcutChord\(\)[\s\S]*appShortcutText\('K', \{shift: true\}\)/.test(shareSource), 'pin-tab prompt moved off the YO!share shortcut');
      assert.ok(/async function uploadFiles\(session, fileList, options = \{\}\)[\s\S]*refreshTerminalAfterUpload\(session\)/.test(shareSource), 'upload completion forces a terminal repaint after path insertion/toast rendering');
      assert.ok(/function refreshTerminalAfterUpload\(session\)[\s\S]*scheduleFit\(session\)[\s\S]*refreshTerminal\(session\)[\s\S]*requestAnimationFrame/.test(shareSource), 'upload repaint uses the shared fit and xterm refresh helpers');
      assert.ok(/async function resyncShareViewerUiState\(\)[\s\S]*now - lastStartedAt < shareGeometryResyncMinIntervalMs[\s\S]*shareGeometryResyncLastStartedAt = now[\s\S]*terminalDims:\s*payload\?\.terminalDims \|\| uiState\.terminalDims \|\| \[\]/.test(shareSource), 'DOIT.69: geometry drift resync also re-pins host terminal dimensions and is rate-limited');
      assert.ok(/function shareReplayRequestKeyframe\(reason = 'replay-error', detail = \{\}\)[\s\S]*requestFloorMs = Math\.max\(shareReplayKeyframeRequestMinIntervalMs[\s\S]*now - lastRequestAt < requestFloorMs[\s\S]*shareReplayKeyframeRequestSuppressedCount/.test(shareSource), 'YO!share replay keyframe repair requests share the five-second floor');
      assert.ok(/function shareReplayDeltaSequenceStatus\(message = \{\}\)[\s\S]*epoch < currentEpoch \|\| sequence <= lastSequence[\s\S]*reason: 'stale'/.test(shareSource), 'DOM deltas from older epochs or already-applied sequences are stale, not viewer-behind gaps');
      assert.ok(/function shareReplayDeltaCanApplyBestEffort\(sequenceStatus = \{\}\)[\s\S]*sequenceStatus\.reason !== 'gap'[\s\S]*epoch === currentEpoch[\s\S]*sequence > lastSequence/.test(shareSource), 'YO!share replay gaps keep applying same-epoch incremental updates while complete DOM replay is throttled');
      assert.ok(/function shareNextMirrorFrameMetadata\(type, options = \{\}\)[\s\S]*shareMirrorFrameTypeIsDomReplayContent\(type\)[\s\S]*shareNextDomReplayFrameMetadata/.test(shareSource), 'YO!share DOM replay frames use a sequence stream independent from semantic mirror frames');
      assert.ok(/function sharePublishDomKeyframeNow\(reason = 'manual-debug'\)[\s\S]*cancelsScheduledTopology[\s\S]*followsRecentTopology[\s\S]*shareReplayResetMutationPublisherForKeyframe\(cancelsScheduledTopology \|\| followsRecentTopology \? 'topology' : cleanReason\)/.test(shareSource), 'a manual/debug keyframe that supersedes or follows topology keeps the topology mutation quiet window');
      assert.ok(/function applyShareViewBodyClasses\(\)[\s\S]*share-view-mode[\s\S]*share-view-readonly[\s\S]*share-view-write/.test(shareSource), 'DOIT.69: share viewer body classes distinguish read-only from write share view');
      assert.ok(/const preferencesReadOnlyVisual = readOnlyMode && !shareViewMode/.test(shareSource), 'DOIT.69: Preferences stay visually host-identical in read-only share view');
      assert.ok(/const readonly = readOnlyMode && !shareViewMode \? `<span class="preferences-readonly">/.test(shareSource), 'DOIT.69: read-only Preferences chrome is suppressed inside mirrored share view');
      assert.ok(/function renderSessionButtons\(options = \{\}\)[\s\S]*if \(openAppMenuId\) requestAnimationFrame\(\(\) => scheduleSharePopupLayerPublish\(\{immediate: true\}\)\)/.test(shareSource), 'DOIT.69: restored open topbar menus republish the popup layer after rerender');
      assert.equal(/detail:\s*choice\.value/.test(shareSource), false, 'DOIT.67: topbar language menu does not print raw locale codes as visible detail text');
      assert.ok(/function applyShareUiMessage\(message\)[\s\S]*layoutFromParam\(payload\.layout[\s\S]*applyLayoutSlots\(next/.test(shareSource), 'share-view UI frames apply layout through layoutFromParam');
      assert.ok(shareSource.includes("'.pane-tab-detached-popover.popover-open'") && shareSource.includes("'.pane-tab.popover-open > .session-popover'") && shareSource.includes("'.dockview-pane-tab.popover-open > .session-popover'") && shareSource.includes("'.tabber-session-tab.popover-open > .session-popover'"), 'DOIT.68: popup-layer capture includes detached, inline, and Tabber tab hover popovers');
      assert.ok(shareSource.includes("'.diff-ref-suggestion-popover:not([hidden])'"), 'DOIT.68: popup-layer capture includes custom diff-ref dropdowns');
      assert.ok(/function bindAppMenuCommandMirrorActive[\s\S]*share-mirror-active/.test(shareSource), 'DOIT.68: menu option hover/focus gets a serializable mirror-active class');
      assert.ok(/\.app-menu-command\.share-mirror-active:not\(:disabled\)/.test(fs.readFileSync('static/yolomux.css', 'utf8')), 'DOIT.68: mirrored menu option active class uses the same styling as local hover/focus');
      assert.ok(/function sharePointerPayloadForPoint\(clientX, clientY, options = \{\}\)[\s\S]*appSpacePoint\(clientX, clientY\)[\s\S]*scope: 'viewport'[\s\S]*x: Math\.round\(point\.x \* 10\) \/ 10[\s\S]*y: Math\.round\(point\.y \* 10\) \/ 10/.test(shareSource), 'M7: share pointer publication sends app-space viewport coordinates through the single mirror transform');
      assert.ok(/function sharePointFromPointerPayload\(payload = \{\}\)[\s\S]*if \(payload\.scope && payload\.scope !== 'viewport'\) return null[\s\S]*visualPointFromAppSpace\(x, y\)/.test(shareSource), 'M7: share pointer rendering maps app-space viewport coordinates through the local mirror transform');
      assert.equal(shareSource.includes('shareSemanticPointerPayload'), false, 'M7: semantic pointer fallbacks are removed after mirror-frame geometry');
      assert.equal(shareSource.includes('shareTerminalCellPointerPayload'), false, 'M7: terminal cell pointer fallback is removed after mirror-frame geometry');
      assert.equal(shareSource.includes("scope: 'pane'"), false, 'M7: pane-relative pointer fallback is removed after mirror-frame geometry');
      assert.ok(/function shareScrollPayloadForElement\(element\)[\s\S]*target: descriptor\.target[\s\S]*anchor[\s\S]*head/.test(shareSource), 'M5: share scroll payloads carry editor scroll and selection state through one helper');
      assert.ok(/function shareScrollTargetForElement\(element\)[\s\S]*element\.closest\('#info-content'\)[\s\S]*target: 'info'/.test(shareSource), 'YO!info scroll is a mirrored share scroll target');
      assert.ok(/function applyShareScrollState\(payload = \{\}\)[\s\S]*applyingShareRemoteScroll = true[\s\S]*scrollTop = top[\s\S]*view\.dispatch\(\{selection/.test(shareSource), 'M5: share scroll apply sets scroll and editor selection under an echo guard');
      assert.ok(/function applyShareScrollState\(payload = \{\}\)[\s\S]*shareLastAppliedScrollByTarget\.set\(target, \{top, left, payload: \{[\s\S]*shareRememberEditorViewState\(payload, top, left\)[\s\S]*shareScrollElementForPayload/.test(shareSource), 'DOIT.68: host scroll frames become authoritative before the client DOM scroller exists');
      assert.ok(/shareLastAppliedScrollByTarget\.set\(target, \{top, left, payload: \{/.test(shareSource), 'DOIT.67: share viewers remember the last host scroll frame and payload for local-scroll restoration');
      assert.ok(/function restoreShareReadonlyScrollTarget\(target\)[\s\S]*shareScrollTargetForElement\(target\)[\s\S]*shareLastAppliedScrollByTarget\.get\(descriptor\.target\)[\s\S]*descriptor\.element\.scrollTop = top/.test(shareSource), 'DOIT.67: read-only local scroll restores the event target directly to the last host-authored value');
      assert.equal(shareSource.includes('shareLastAppliedScrollPayloadByTarget'), false, 'share scroll restore state uses one target-keyed map');
      assert.ok(/function restoreShareScrollTargetByKey\(target\)[\s\S]*const state = shareLastAppliedScrollByTarget\.get\(cleanTarget\)[\s\S]*\.\.\.\(state\.payload \|\| \{\}\)[\s\S]*shareScrollElementForPayload\(payload\)[\s\S]*scrollTop = payload\.top/.test(shareSource), 'DOIT.69: pending host scroll frames replay from the full remembered payload after pane DOM rebuilds');
      assert.ok(/function scheduleShareScrollRestoreByKey\(target, options = \{\}\)[\s\S]*shareScrollRestoreFrameTimers\.set\(cleanTarget, state\)[\s\S]*requestAnimationFrame\(run\)/.test(shareSource), 'readonly share scroll restore retries across render frames');
      assert.ok(/function applyShareScrollState\(payload = \{\}\)[\s\S]*scheduleShareScrollRestoreByKey\(target\)/.test(shareSource), 'host scroll frames schedule replay even when the current DOM scroller is not ready');
      assert.ok(/function shareScrollStateSnapshot\(\)[\s\S]*shareScrollPayloadForElement\(element\)/.test(shareSource), 'full UI-state snapshots reuse the shared scroll payload helper instead of inventing a second scroll format');
      assert.ok(/function applyShareScrollSnapshot\(scroll = \[\]\)[\s\S]*applyShareScrollState\(payload\)/.test(shareSource), 'full UI-state scroll replays through the same host-authored scroll apply path');
      assert.ok(/function restoreShareScrollTargetsByPrefix\(prefix\)[\s\S]*shareLastAppliedScrollByTarget\.keys\(\)[\s\S]*scheduleShareScrollRestoreByKey\(target\)/.test(shareSource), 'DOIT.69: editor scroll targets can replay as a group after editor rerender');
      assert.ok(/function scheduleShareFileEditorScrollRestore\(item, path\)[\s\S]*editor:\$\{key\}:editor[\s\S]*editor:\$\{key\}:preview/.test(shareSource), 'file editor renders schedule host scroll replay for editor and preview scrollers');
      assert.ok(/function shareCanPublishScroll\(\)[\s\S]*shareViewMode && !shareWriteMode[\s\S]*return false[\s\S]*return shareCanPublishUi\(\)/.test(shareSource), 'DOIT.69: read-only share viewers cannot publish scroll frames');
      assert.ok(/function scheduleShareScrollPublishForElement\(element\)[\s\S]*!shareCanPublishScroll\(\)[\s\S]*restoreShareReadonlyScrollTarget\(element\)[\s\S]*if \(shareCanPublishScroll\(\)\) sharePublish\('scroll'/.test(shareSource), 'DOIT.69: scroll publish scheduling restores readonly local scroll and rechecks publish permission before sending');
      assert.ok(/function installShareScrollPublisher\(\)[\s\S]*if \(shareViewMode && !shareWriteMode\) \{[\s\S]*restoreShareReadonlyScrollTarget\(event\.target\)[\s\S]*return;[\s\S]*scheduleShareScrollPublishForElement/.test(shareSource), 'M5: one document-level scroll publisher covers mirrored surfaces but read-only viewers never publish back');
      assert.ok(/function shareGeometryDigestSnapshot\(\)[\s\S]*viewport[\s\S]*slots[\s\S]*tabStrips[\s\S]*terminalCells[\s\S]*editors[\s\S]*fonts[\s\S]*textWraps/.test(shareSource), 'M9: geometry digest measures mirror inputs, rendered outputs, and wrapped text/control layout');
      assert.ok(/function shareEditorDigest\(panel\)[\s\S]*rect[\s\S]*contentHash[\s\S]*errorHash/.test(shareSource), 'M9: editor digest uses stable editor identity/content/error state rather than client scrollHeight jitter');
      assert.equal(/scrollHeight:\s*Math\.round\(Number\(panel\._cmView/.test(shareSource), false, 'M9: editor digest does not compare CodeMirror scrollHeight across share clients');
      assert.ok(/const shareWrappedTextDigestSelectors = \[[\s\S]*'textarea\[data-setting-path\]'[\s\S]*'\.app-menu-command-label'[\s\S]*'\.info-row'/.test(shareSource), 'M9: wrapped text digest covers native controls, menus, and YO!info-style rows');
      assert.ok(/function shareGeometryFirstDifference\(host = \{\}, local = \{\}\)[\s\S]*'textWraps'/.test(shareSource), 'M9: digest comparison names wrapped text/control drift separately');
      assert.ok(/async function boot\(\)[\s\S]*waitForYolomuxFontsReady\(\{timeoutMs: 0\}\)\.catch\(\(\) => \{\}\)[\s\S]*paintInitialAppShell\(\)[\s\S]*installYolomuxFontMetricRefresh\(\)/.test(shareSource), 'M9: first app render starts bundled font loading and corrects wrapped widgets after metrics settle');
      assert.ok(/const shareAppliedTextWrapMetricsByKey = new Map\(\)/.test(shareSource), 'M9: share viewers retain host wrapped-text metrics for digest repair');
      assert.ok(/function shareTextWrapDigestEntryWithHostMetrics\(entry, metric = null\)[\s\S]*clientWidth[\s\S]*scrollHeight/.test(shareSource), 'M9: wrapped text digest uses host-owned dimensions after metrics are applied');
      assert.ok(/let shareGeometryRepairInFlight = false/.test(shareSource), 'M9: share geometry repair has an in-flight guard so repeated digest frames do not report stale buckets');
      const shareProtocolSource = shareSource.slice(
        shareSource.indexOf('// Share mirror protocol owner.'),
        shareSource.indexOf('// End share mirror protocol owner.'),
      );
      assert.ok(/const shareMirrorProtocol = Object\.freeze\(\{[\s\S]*version:\s*1[\s\S]*frames:[\s\S]*replayFrameTypes:[\s\S]*sequencedFrameTypes:[\s\S]*keyframeReasons:[\s\S]*sequenceFields:[\s\S]*redaction:[\s\S]*terminalPlaceholder:[\s\S]*debugNames:/.test(shareProtocolSource), 'DOIT.72 P1.1: share mirror protocol owns frame names, version, sequencing, redaction, placeholder, and debug metadata');
      for (const replayType of ['dom-keyframe', 'dom-delta', 'dom-keyframe-request', 'dom-keyframe-ack', 'dom-replay-error', 'terminal-host-resize']) {
        assert.ok(shareProtocolSource.includes(`'${replayType}'`), `DOIT.72 P1.1: protocol owns ${replayType}`);
        assert.equal(shareSource.replace(shareProtocolSource, '').includes(`'${replayType}'`), false, `DOIT.72 P1.1: ${replayType} is not scattered outside the protocol owner`);
      }
      assert.ok(/function shareGeometryRepairActionForDiff\(diff = ''\)[\s\S]*terminalCells[\s\S]*shareMirrorProtocol\.frames\.terminalHostResize[\s\S]*textWraps[\s\S]*shareMirrorProtocol\.frames\.textWrapMetrics[\s\S]*domDigest[\s\S]*shareMirrorProtocol\.frames\.domKeyframe[\s\S]*slots[\s\S]*tabStrips[\s\S]*editors[\s\S]*shareMirrorProtocol\.frames\.uiState/.test(shareSource), 'DOIT.72 P0.2/P1.1: geometry drift repair maps buckets through protocol-owned frame names');
      assert.ok(/async function repairShareGeometryDigest\(payload = \{\}, initialDiff = ''\)[\s\S]*repairShareGeometryBucket\(payload, diff\)[\s\S]*shareGeometryDigestCompare/.test(shareSource), 'M9: viewers repair and recheck geometry drift through the bucket-specific repair helper');
      assert.equal(/async function repairShareGeometryDigest\(payload = \{\}, initialDiff = ''\)[\s\S]*else await resyncShareViewerUiState\(\)/.test(shareSource), false, 'DOIT.72 P0.2: geometry drift repair no longer blindly runs the generic semantic resync for every mismatch');
      assert.ok(/function applyShareGeometryDigest\(payload = \{\}\)[\s\S]*shareGeometryDigestCompare\(payload\)[\s\S]*!shareGeometryRepairInFlight[\s\S]*repairShareGeometryDigest\(payload, diff\)/.test(shareSource), 'M9: viewers compare geometry digests and route mismatch through the guarded repair helper');
      assert.ok(/shareGeometryDigestPublishMs:\s*uiDelayMs\.shareGeometryDigestPublish/.test(timingSource), 'M9/MV-3: the odd geometry digest cadence is owned by the shared timing partial');
      assert.ok(/function installShareGeometryDigestLoop\(\)[\s\S]*setInterval\(publishShareGeometryDigest, shareGeometryDigestPublishMs\)/.test(shareSource), 'M9: host publishes geometry digest through the shared timing owner');
      assert.ok(/function renderSharePointerGhost\(payload = \{\}\)[\s\S]*payload\.sender === shareClientId[\s\S]*ensureSharePointerGhost\(sender\)[\s\S]*renderShareClickRipple/.test(shareSource), 'share participants render remote ghost cursors and ignore their own echoed cursor');
      assert.ok(/function shareHostTerminalSize\(session\)[\s\S]*shareHostDimensions\.get[\s\S]*rawRows <= 0 \|\| rawCols <= 0[\s\S]*return null/.test(shareSource), 'share viewers size xterm only from positive host terminal dimensions');
      assert.ok(/function fitTerminal\(session, options = \{\}\)[\s\S]*if \(shareViewMode\) \{[\s\S]*if \(!hostSize\) return[\s\S]*item\.term\.resize\(hostSize\.cols, hostSize\.rows\)[\s\S]*item\.term\.reset\(\)[\s\S]*return;[\s\S]*estimateTerminalSize/.test(shareSource), 'DOIT.69: share-view fitting uses host dims only, resets on host dim changes, and never reflows from the client pane box');
      const dockviewSource = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
      assert.ok(/function dockviewSyncHeaderActionReservations\(\)[\s\S]*appSpaceRect\(actions\)[\s\S]*appSpaceRect\(header\)/.test(dockviewSource), 'M2/M3: Dockview tab fitting uses app-space widths under the mirror transform');
      const shareCss = fs.readFileSync('static/yolomux.css', 'utf8');
      assert.ok(/body\.app-vw-lte-1500 \.app-menu-button/.test(shareCss), 'M0/M2: responsive topbar chrome is keyed by appViewport classes, not native media width');
      assert.equal(/@media \(max-width: (1500|1280|1100|1080|980|760|720)px\)/.test(shareCss), false, 'M0/M2: mirror-sensitive breakpoints do not use native viewport media queries');
      assert.ok(/body\.share-view-mode\.share-view-readonly \.share-mirror-stage \.preferences-scroll[\s\S]*\.share-mirror-stage \.info-list[\s\S]*overflow:\s*hidden !important[\s\S]*scrollbar-width:\s*none/.test(shareCss), 'read-only share mirrors disable local Preferences and YO!info scrollbar dragging while host scroll remains programmatic');
      assert.ok(/body\.share-view-mode\.share-view-readonly \.share-mirror-stage \.xterm-viewport::-webkit-scrollbar[\s\S]*width:\s*0/.test(shareCss), 'DOIT.69: read-only share mirrors hide WebKit terminal scrollbar thumbs');
      assert.equal(shareSource.includes('applyShareTerminalScale'), false, 'M4: share terminals no longer have a per-pane scale path');
      assert.equal(shareCss.includes('--share-terminal-scale'), false, 'M4: share terminal scale CSS is removed; root transform owns mirror scaling');
      assert.ok(/@font-face\s*\{[\s\S]*font-family:\s*"YOLOmux UI"[\s\S]*yolomux-ui\.woff2/.test(shareCss), 'M8: bundled UI font is declared');
      assert.ok(/@font-face\s*\{[\s\S]*font-family:\s*"YOLOmux Mono"[\s\S]*yolomux-mono\.woff2/.test(shareCss), 'M8: bundled mono font is declared');
      assert.ok(/@font-face\s*\{[\s\S]*font-family:\s*"YOLOmux UI"[\s\S]*font-display:\s*block/.test(shareCss), 'M9: bundled UI font blocks fallback-font layout during first paint');
      assert.ok(/@font-face\s*\{[\s\S]*font-family:\s*"YOLOmux Mono"[\s\S]*font-display:\s*block/.test(shareCss), 'M9: bundled mono font blocks fallback-font layout during first paint');
      assert.ok(/--ui-font:\s*"YOLOmux UI",/.test(shareCss) && /--mono-font:\s*"YOLOmux Mono",/.test(shareCss), 'M8: bundled fonts are first in the shared font tokens');
      assert.ok(/\.share-viewer-mirror-status\.match\s*\{[\s\S]*var\(--good\)/.test(shareCss), 'M9: mirror match status is visible in the share banner');
      assert.ok(/message\.type === 'host-resize' \|\| message\.type === shareMirrorProtocol\.frames\.terminalHostResize[\s\S]*updateShareHostTerminalSize/.test(shareSource), 'share viewers apply host-resize and terminal-host-resize UI events');
      assert.ok(/const sharePointerPublishIntervalMs = 50/.test(shareSource), 'share hosts throttle pointer publication to about 20Hz before latest-wins server coalescing');
      assert.ok(/function installSharePointerPublisher\(\)[\s\S]*pointermove[\s\S]*queueSharePointerMove[\s\S]*pointerdown[\s\S]*sharePublishPointerEvent/.test(shareSource), 'share hosts publish throttled pointer movement and click events');
      assert.ok(/function sharePublishPointerEvent\(event, options = \{\}\)[\s\S]*shareReplayFeatureEnabled\(\)[\s\S]*sharePublish\('pointer', \{\.\.\.payload, visible: true\}\)[\s\S]*return[\s\S]*sharePublish\('pointer', payload\)/.test(shareSource), 'default DOM replay shares publish pointer through one unsequenced pointer frame instead of double-sending sequenced replay deltas');
      assert.ok(/function queueSharePointerMove\(event\)[\s\S]*if \(!shareCanPublishUi\(\)\) return/.test(shareSource), 'share pointer publishing is shared by hosts and rw viewers');
      assert.ok(/function shareReadOnlyReplayModeEnabled\(\)[\s\S]*shareViewMode && !shareWriteMode && shareReplayFeatureEnabled/.test(shareSource), 'DOIT.72 P4.2: read-only replay mode has one shared predicate');
      assert.ok(/function shareReplayViewerModeEnabled\(\)[\s\S]*shareViewMode && shareReplayFeatureEnabled/.test(shareSource), 'DOIT.72 P5.2: write and read-only replay viewers share one replay shell predicate');
      assert.ok(/function shareSemanticReadOnlyMirrorEnabled\(\)[\s\S]*!shareReplayViewerModeEnabled\(\)/.test(shareSource), 'DOIT.72 P5.2: semantic read-only mirror mode is the explicit replay opt-out path');
      assert.ok(/async function applyShareUiState\(payload = \{\}\)[\s\S]*!shareSemanticMirrorApplyAllowed\(\)/.test(shareSource), 'DOIT.72 P4.2: semantic UI-state apply is guarded away from read-only replay mode');
      assert.ok(/function applySharePopupLayer\(payload = \{\}, sender = ''\)[\s\S]*shareReadOnlyReplayModeEnabled\(\)/.test(shareSource), 'DOIT.72 P4.2: legacy popup-layer apply is guarded away from read-only replay mode');
      assert.ok(/function appOverlayRootElement\(\)[\s\S]*overlay\.id = 'appOverlayRoot'[\s\S]*root\.appendChild\(overlay\)/.test(shareSource), 'DOIT.72 P4.3: host-visible overlay DOM lives under #appRoot so read-only replay serializes it normally');
      assert.ok(/\.app-overlay-root\s*\{[\s\S]*position:\s*fixed[\s\S]*z-index:\s*var\(--z-share-presence\)[\s\S]*pointer-events:\s*none/.test(shareCss), 'DOIT.72 P4.3: app overlay root is fixed app-space chrome and does not resize the layout grid');
      assert.ok(/function createContextMenuController\(\)[\s\S]*appOverlayRootElement\(\)\.appendChild\(menu\)/.test(shareSource), 'DOIT.72 P4.3: context menus mount under the replayed app overlay root');
      assert.ok(/function detachPaneTabPopover\(tab, popover\)[\s\S]*const host = appOverlayRootElement\(\)[\s\S]*host\.appendChild\(popover\)/.test(shareSource), 'DOIT.72 P4.3: detached tab popovers mount under the replayed app overlay root');
      assert.ok(/function ensureDiffRefPopover\(\)[\s\S]*appOverlayRootElement\(\)\?\.appendChild\(diffRefPopover\)/.test(shareSource), 'DOIT.72 P4.3: diff ref popovers mount under the replayed app overlay root');
      assert.ok(/function fileTreeRepoPopoverNode\(\)[\s\S]*appOverlayRootElement\(\)\.appendChild\(node\)/.test(shareSource), 'DOIT.72 P4.3: repo hover popovers mount under the replayed app overlay root');
      assert.ok(/function openFileImagePreview\(anchor, path, entry, point = null\)[\s\S]*appOverlayRootElement\(\)\.appendChild\(popover\)/.test(shareSource), 'DOIT.72 P4.3: image preview popovers mount under the replayed app overlay root');
      assert.ok(/function showSessionRenameDialog\(session\)[\s\S]*appOverlayRootElement\(\)\.appendChild\(overlay\)/.test(shareSource), 'DOIT.72 P4.3: rename dialogs mount under the replayed app overlay root');
      assert.ok(/function showFileEditorDecisionDialog\(options = \{\}\)[\s\S]*appOverlayRootElement\(\)\.appendChild\(backdrop\)/.test(shareSource), 'DOIT.72 P4.3: editor decision dialogs mount under the replayed app overlay root');
      assert.ok(/function ensureCommandPalette\(\)[\s\S]*appOverlayRootElement\(\)\.appendChild\(node\)/.test(shareSource), 'DOIT.72 P4.3: command palette modal DOM mounts under the replayed app overlay root');
      assert.ok(/function shareReplayTerminalPlaceholderDiagnostics\(\)[\s\S]*healthy: connected === entries\.length/.test(shareSource), 'DOIT.72 P4.4: replay health names terminal placeholder health directly');
      assert.ok(/function shareReplayHealthDiagnostics\(\)[\s\S]*match: shareReplayShellState\.status === 'mirrored' && terminalPlaceholders\.healthy[\s\S]*domDigest: shareReplayCurrentDomDigest\(\)/.test(shareSource), 'DOIT.72 P4.4: read-only replay parity is reported through replay health and DOM digest');
      assert.ok(/function applyShareReplayShellMessage\(message = \{\}\)[\s\S]*message\.type === shareMirrorProtocol\.frames\.geometryDigest[\s\S]*exposeShareDebugApi\(\)[\s\S]*return true/.test(shareSource), 'DOIT.72 P4.4: replay viewers consume legacy geometry digest frames without semantic geometry repair');
      assert.ok(/function installShareReadonlyInteractionBlocker\(\)[\s\S]*shareSemanticReadOnlyMirrorEnabled\(\)[\s\S]*window\.addEventListener\(name, blockShareReadonlyInteraction/.test(shareSource), 'semantic read-only share viewers install a capture-phase UI interaction blocker');
      assert.ok(/'touchstart'[\s\S]*'touchmove'[\s\S]*'wheel'/.test(shareSource.slice(shareSource.indexOf('function installShareReadonlyInteractionBlocker'))), 'DOIT.67: read-only share blocker captures touch/wheel before mirrored panes can scroll locally');
      assert.ok(/function shareReadonlyPointerEventHitsScrollContainer\(event\)[\s\S]*shareScrollTargetForElement\(event\.target\)[\s\S]*descriptor\.element === event\.target/.test(shareSource), 'read-only share pointerdown on native scroll containers is blocked so scrollbar drags cannot mutate local scroll');
      assert.ok(/function blockShareReadonlyInteraction\(event\)[\s\S]*shareSemanticReadOnlyMirrorEnabled\(\)[\s\S]*\[data-share-viewer-control\][\s\S]*preventDefault/.test(shareSource), 'semantic read-only share blocking exempts only viewer chrome controls');
      assert.ok(/installShareReadonlyInteractionBlocker\(\);[\s\S]*window\.addEventListener\('keydown', handleGlobalShortcutKeydown, true\)/.test(shareSource), 'read-only share interaction blocking is installed before global shortcuts can mutate local UI');
      assert.ok(/function openAppMenu\(wrapper, options = \{\}\)[\s\S]*scheduleSharePopupLayerPublish\(\{immediate: true\}\)/.test(shareSource), 'DOIT.67: top-level app menu opens immediately publish the host-owned popup layer');
      assert.ok(/function closeAppMenus\(keepOpen = null\)[\s\S]*scheduleSharePopupLayerPublish\(\{immediate: true\}\)/.test(shareSource), 'DOIT.67: top-level app menu closes immediately clear the host-owned popup layer');
      assert.ok(/function sharePopupLayerElements\(\)[\s\S]*'\.app-menu\.open \.app-menu-popover'/.test(shareSource), 'topbar File/tmux/Tabs/language dropdowns are captured through the shared popup-layer selector');
      assert.ok(/function applyShareUiMessage\(message\)[\s\S]*message\.type === 'menu'[\s\S]*return;/.test(shareSource), 'share viewers ignore legacy menu-id frames instead of re-rendering local menus');
      assert.ok(/function applyShareUiMessage\(message\)[\s\S]*message\.type === 'popup-layer'[\s\S]*applySharePopupLayer/.test(shareSource), 'share viewers apply inert host-owned popup-layer frames');
      assert.ok(/function sharePopupLayerPayload\(\)[\s\S]*seq: sharePopupLayerSequence[\s\S]*owner: shareClientId/.test(shareSource), 'DOIT.67: popup-layer frames carry sequence and owner metadata');
      assert.ok(/function applySharePopupLayer\(payload = \{\}, sender = ''\)[\s\S]*seq <= previousSeq[\s\S]*return/.test(shareSource), 'DOIT.67: stale popup-layer frames cannot resurrect closed popup HTML');
      assert.ok(/function shareSanitizePopupHtml\(html = ''\)[\s\S]*data-share-secret[\s\S]*input\[value\*="\/share\/"\]/.test(shareSource), 'popup-layer serialization redacts marked share secrets and share URL inputs');
      assert.ok(/\.share-popup-mirror-layer\s*\{[\s\S]*pointer-events:\s*none/.test(shareCss), 'mirrored popup layer is inert on share viewers');
      assert.ok(/\.share-popup-mirror-layer\s*\{[\s\S]*position:\s*absolute/.test(shareCss), 'DOIT.67: mirrored popup layer lives in scaled app space, not native viewport space');
      const popupChildRule = shareCss.match(/\.share-popup-mirror-item > \*\s*\{(?<body>[^}]*)\}/)?.groups?.body || '';
      assert.equal(/transform:\s*scale/.test(popupChildRule), false, 'DOIT.67: mirrored popup content is not double-scaled inside the root mirror transform');
      assert.ok(/top:\s*0\s*!important/.test(popupChildRule) && /left:\s*0\s*!important/.test(popupChildRule), 'DOIT.67: mirrored popup children are anchored to the host-measured shell');
      assert.ok(/visibility:\s*visible\s*!important/.test(popupChildRule) && /opacity:\s*1\s*!important/.test(popupChildRule), 'DOIT.67: mirrored popup children stay visible without host-only open ancestors');
      const popupModalRule = shareCss.match(/\.share-popup-mirror-item > \.modal\s*\{(?<body>[^}]*)\}/)?.groups?.body || '';
      assert.ok(/width:\s*100%\s*!important/.test(popupModalRule) && /height:\s*100%\s*!important/.test(popupModalRule) && /max-height:\s*none\s*!important/.test(popupModalRule), 'mirrored modal overlays fill the host-measured shell without using client viewport dimensions');
      assert.ok(/\.share-popup-mirror-item > \.modal\.share-open \.modal-dialog\s*\{[\s\S]*width:\s*min\(960px,\s*calc\(100% - 28px\)\)\s*!important/.test(shareCss), 'mirrored YO!share dialog width is relative to the host-measured modal shell');
      assert.ok(/function shareTerminalBytesFromMessage\(session, message\)[\s\S]*message\.ch !== 'term'[\s\S]*atob\(message\.data\)/.test(shareSource), 'share-view terminal output is decoded from tagged terminal frames');
  	    assert.ok(/function scheduleShareTopologySnapshot\(reason = 'topology'\)[\s\S]*scheduleShareUiStatePublish\(\{reason: `topology:\$\{cleanReason\}`\}\)[\s\S]*scheduleShareTopologyDomKeyframe\(\)/.test(shareSource), 'DOIT.72 P0.3: topology changes route through one full UI-state snapshot scheduler and a replay keyframe scheduler');
  	    assert.ok(/shareTopologyKeyframePointerQuietMs:\s*500/.test(timingSource) && /const shareTopologyKeyframeMaxDeferralMs = shareReplayHostKeyframeMinIntervalMs/.test(shareSource) && /function shareTopologyDomKeyframeDelayMs\(\)[\s\S]*shareReplayTopologyKeyframeQueuedAt[\s\S]*shareReplayHostLastKeyframeAt[\s\S]*sharePointerQuietDelayMs\(\)/.test(shareSource), 'topology replay keyframes wait for a pointer-quiet window and respect the host keyframe floor');
  	    assert.ok(/function sharePublishDomKeyframeNow\(reason = 'manual-debug'\)[\s\S]*const cleanReason = shareReplayKeyframeReason\(reason\)[\s\S]*if \(cleanReason !== 'topology'\) clearScheduledShareTopologyDomKeyframe\(\)/.test(shareSource), 'manual/debug/join keyframes cancel redundant pending topology keyframes after they capture the current DOM');
  	    assert.ok(/function scheduleShareTopologyDomKeyframe\(\)[\s\S]*!shareHasActiveShare\(\)[\s\S]*shareReplayPauseMutationPublisherForTopology\(\)[\s\S]*shareReplayTopologyKeyframeQueuedAt[\s\S]*shareReplayTopologyKeyframeTimer = setTimeout\(runScheduledShareTopologyDomKeyframe, 0\)/.test(shareSource) && /function runScheduledShareTopologyDomKeyframe\(\)[\s\S]*shareTopologyDomKeyframeDelayMs\(\)[\s\S]*requestAnimationFrame[\s\S]*sharePublishDomKeyframe\('topology'\)/.test(shareSource), 'default DOM replay shares pause topology mutations and coalesce delayed topology keyframes after layout render so Finder minimize/restore cannot leave stale panes');
  	    assert.ok(/shareReplayPostTopologyKeyframeQuietExtraMs:\s*1000/.test(timingSource) && /const shareReplayPostTopologyKeyframeQuietMs = shareReplayHostKeyframeMinIntervalMs \+ yolomuxTiming\.shareReplayPostTopologyKeyframeQuietExtraMs/.test(shareSource) && /function shareReplayDrainMutationPublisher\(\)[\s\S]*takeRecords[\s\S]*shareReplayPendingMutations\.splice\(0, shareReplayPendingMutations\.length\)[\s\S]*shareReplayDeltaFramePending = false/.test(shareSource) && /function shareReplayPauseMutationPublisherForTopology\(\)[\s\S]*shareReplayMutationPublisherPaused = true[\s\S]*shareReplayDrainMutationPublisher\(\)[\s\S]*shareReplayTopologyMutationPauseTimer = setTimeout/.test(shareSource) && /function shareReplayResetMutationPublisherForKeyframe\(reason = 'manual-debug'\)[\s\S]*shareReplayMutationPublisherPaused = true[\s\S]*shareReplayDrainMutationPublisher\(\)[\s\S]*cleanReason === 'join' \|\| cleanReason === 'topology'[\s\S]*shareReplayResumeMutationPublisherAfterFrames\(quietMs\)/.test(shareSource) && /function sharePublishDomKeyframeNow\(reason = 'manual-debug'\)[\s\S]*followsRecentTopology[\s\S]*shareReplayResetMutationPublisherForKeyframe\(cancelsScheduledTopology \|\| followsRecentTopology \? 'topology' : cleanReason\)[\s\S]*sharePublish\(shareMirrorProtocol\.frames\.domKeyframe/.test(shareSource), 'DOM keyframes discard pending mutation deltas and join/topology resets keep a keyframe-floor quiet window so boot/layout churn cannot immediately stale the viewer');
  	    assert.ok(/let shareReplayHostMirroredNodes = new WeakSet\(\)/.test(shareSource) && /function shareReplayHostMutationTargetIsMirrored\(node\)[\s\S]*const disconnected = element\.isConnected === false[\s\S]*const outsideRoot = typeof root\?\.contains === 'function' && !root\.contains\(element\)[\s\S]*shareReplayHostMirroredNodes\.has/.test(shareSource) && /function shareCreateDomKeyframePayload\(reason = 'manual-debug'\)[\s\S]*mirroredNodes: \[\][\s\S]*shareReplayHostMirroredNodes = shareReplayHostMirroredNodeSet\(context\.mirroredNodes\)/.test(shareSource) && /function shareReplayMutationEntries\(records = \[\]\)[\s\S]*!shareReplayHostMutationTargetIsMirrored\(target\)[\s\S]*continue/.test(shareSource), 'DOM replay host only publishes mutation deltas for nodes serialized into the current mirror keyframe');
  	    assert.ok(/function shareReplayMutationEntries\(records = \[\]\)[\s\S]*let needsKeyframe = false[\s\S]*shareReplayMutationNodeIsIgnored\(node\)[\s\S]*needsKeyframe = true[\s\S]*entries\.splice\(0, entries\.length\)[\s\S]*scheduleShareTopologyDomKeyframe\(\)/.test(shareSource), 'child-list mutations touching ignored Finder/private nodes do not emit partial deltas that can duplicate Finder and Differ panes');
  	    assert.ok(/const shareReplayHostDeltaMaxBytes = 48 \* 1024/.test(shareSource) && /function shareReplayFlushMutationDeltas\(\)[\s\S]*shareReplayPublishDeltaPayload\(payload, 'mutation', \{maxBytes: shareReplayHostDeltaMaxBytes\}\)[\s\S]*published\?\.skipped[\s\S]*sharePublishDomKeyframe\('backpressure'\)[\s\S]*return null[\s\S]*return published/.test(shareSource), 'oversized DOM delta batches request a throttled keyframe through the single-serialize publish path instead of creating replay sequence gaps');
  	    assert.ok(/function applyLayoutSlots\(nextSlots, options = \{\}\)[\s\S]*sharePublishLayout\(\)[\s\S]*scheduleShareTopologySnapshot\(options\.shareReason \|\| 'layout'\)/.test(shareSource), 'layout commits publish share layout updates and schedule a full UI-state snapshot through the topology scheduler');
      assert.ok(/function activatePaneTab\(side, session, options = \{\}\)[\s\S]*sharePublish\('active-tab', \{slot: side, item: session\}\)[\s\S]*scheduleShareTopologySnapshot\('tab-activation'\)/.test(fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8')), 'tab activation schedules a full topology snapshot behind the narrow active-tab frame');
      assert.ok(/async function openFileExplorerAt\(path, options = \{\}\)[\s\S]*scheduleShareTopologySnapshot\('finder-root'\)/.test(fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8')), 'Finder root changes schedule a full topology snapshot');
      assert.ok(/function setFileExplorerMode\(mode, options = \{\}\)[\s\S]*scheduleShareTopologySnapshot\('finder-mode'\)/.test(fs.readFileSync('static_src/js/yolomux/90_changes_editor.js', 'utf8')), 'Finder/Differ/Tabber mode changes schedule a full topology snapshot');
      assert.ok(/function switchFileExplorerChangesSession\(session\)[\s\S]*scheduleShareTopologySnapshot\('finder-session'\)/.test(fs.readFileSync('static_src/js/yolomux/90_changes_editor.js', 'utf8')), 'Finder/Differ/Tabber session changes schedule a full topology snapshot');
      assert.ok(/function setFileEditorViewMode\(path, mode, item = null\)[\s\S]*scheduleShareTopologySnapshot\('editor-mode'\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'editor mode changes schedule a full topology snapshot');
      assert.ok(/function setFileEditorThemeMode\(mode\)[\s\S]*scheduleShareTopologySnapshot\('editor-theme'\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'editor theme changes schedule a full topology snapshot');
      assert.ok(/function applyGlobalThemeMode\(options = \{\}\)[\s\S]*scheduleShareTopologySnapshot\('theme'\)[\s\S]*scheduleShareAppearancePublish\(\{reason: options\.reason \|\| 'theme', topology: false\}\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'all host theme applications publish appearance frames through the shared apply parent');
      assert.ok(/function installGlobalThemeMediaListener\(\)[\s\S]*applyGlobalThemeMode\(\{updateEditor: true, updateTerminals: true, reason: 'theme-system'\}\)/.test(fs.readFileSync('static_src/js/yolomux/50_editor_settings_runtime.js', 'utf8')), 'system OS theme changes identify their appearance publish reason through the shared parent');
      assert.ok(/function openAppMenu\(wrapper, options = \{\}\)[\s\S]*scheduleSharePopupLayerPublish\(\{immediate: true\}\)[\s\S]*scheduleShareTopologySnapshot\('popup-open'\)/.test(fs.readFileSync('static_src/js/yolomux/30_app_menus.js', 'utf8')), 'popup opens schedule a full topology snapshot');
      assert.ok(/function closeAppMenus\(keepOpen = null\)[\s\S]*scheduleSharePopupLayerPublish\(\{immediate: true\}\)[\s\S]*scheduleShareTopologySnapshot\('popup-close'\)/.test(fs.readFileSync('static_src/js/yolomux/30_app_menus.js', 'utf8')), 'popup closes schedule a full topology snapshot');
      assert.ok(/function createShareFromForm\(form\)[\s\S]*sharePublishLayout\(\)[\s\S]*sharePublishUiState\(\)/.test(shareSource), 'newly-created shares immediately receive the full host UI state');
      assert.ok(/function setFocusedPanelItem\(item, options = \{\}\)[\s\S]*sharePublish\('focus', \{item\}\)/.test(shareSource), 'focused-pane changes publish share focus updates');
      assert.ok(/function activatePaneTab\(side, session, options = \{\}\)[\s\S]*sharePublish\('active-tab', \{slot: side, item: session\}\)/.test(shareSource), 'pane tab activation publishes share active-tab updates');
      assert.ok(shareCss.includes('--z-share-presence: 320'), 'share presence has a dedicated z-index above menus');
      assert.ok(/\.share-ghost-cursor\s*\{[\s\S]*z-index:\s*var\(--z-share-presence\)[\s\S]*pointer-events:\s*none/.test(shareCss), 'share ghost cursor overlays the UI without taking pointer input');
      assert.ok(/\.share-ghost-cursor\s*\{[\s\S]*border-radius:\s*50%[\s\S]*box-shadow:/.test(shareCss), 'remote share cursor renders as a target, not a native pointer arrow');
      assert.ok(/\.share-ghost-cursor::before\s*\{[\s\S]*height:\s*36px[\s\S]*background:\s*var\(--share-cursor-color/.test(shareCss), 'remote share cursor has a vertical target crosshair');
      assert.ok(/\.share-ghost-cursor::after\s*\{[\s\S]*width:\s*36px[\s\S]*background:\s*var\(--share-cursor-color/.test(shareCss), 'remote share cursor has a horizontal target crosshair');
      assert.ok(/\.share-click-ripple\s*\{[\s\S]*animation:\s*share-click-ripple/.test(shareCss), 'share click ripples are transient CSS animations');
      assert.ok(/function renderShareClickRipple\(x, y, sender = ''\)[\s\S]*addEventListener\('animationend', \(\) => ripple\.remove\(\), \{once: true\}\)[\s\S]*addEventListener\('animationcancel', \(\) => ripple\.remove\(\), \{once: true\}\)/.test(shareSource), 'CSS-JS-1: share click ripple cleanup is owned by CSS animation events');
      assert.equal(/renderShareClickRipple[\s\S]*setTimeout\([\s\S]*ripple\.remove\(\)[\s\S]*560/.test(shareSource), false, 'CSS-JS-1: share click ripple cleanup has no JS duration literal');
      assert.ok(/\.share-status-pill\.share-mode-read\s*\{[\s\S]*var\(--good\)/.test(shareCss), 'read share status uses the green/good mode color');
      assert.ok(/\.share-status-pill\.share-mode-write\s*\{[\s\S]*var\(--bad\)/.test(shareCss), 'write share status uses the red/bad mode color');
      assert.ok(/\.share-ghost-cursor::before\s*\{[\s\S]*--share-cursor-color/.test(shareCss), 'share cursor color is participant-specific');
      assert.ok(/--share-stage-bg:\s*#000/.test(shareCss), 'M3: share view keeps one black stage token for letterbox/crop bands');
      assert.ok(/body\.theme-light\s*\{[\s\S]*--share-stage-bg:\s*#000/.test(shareCss), 'M3: light mode explicitly keeps the share stage black');
      assert.ok(/\.share-mirror-stage\s*\{[\s\S]*position:\s*fixed[\s\S]*overflow:\s*hidden[\s\S]*background:\s*var\(--share-stage-bg\)/.test(shareCss), 'M3: share view has a fixed black clipping stage for letterbox/crop bands');
      assert.ok(/body\.share-view-mode \.share-mirror-stage \.app-root\s*\{[\s\S]*position:\s*absolute[\s\S]*transform:\s*translate3d\(var\(--share-mirror-tx/.test(shareCss), 'M3: share view transforms the app root inside the mirror stage');
      assert.ok(/\.share-viewer-banner\s*\{[\s\S]*position:\s*fixed[\s\S]*bottom:\s*8px/.test(shareCss), 'M3: share viewer banner is fixed outside the mirror root');
    }
    {
      const metaApi = loadYolomux('', ['1'], 'https:');
      const protocol = metaApi.shareMirrorProtocolForTest;
      assert.equal(protocol.version, 1, 'DOIT.72 P1.1: replay frames carry protocol version 1');
      assert.deepStrictEqual([...protocol.replayFrameTypes].sort(), [
        'dom-delta',
        'dom-keyframe',
        'dom-keyframe-ack',
        'dom-keyframe-request',
        'dom-replay-error',
        'terminal-host-resize',
      ].sort(), 'DOIT.72 P1.1: replay frame vocabulary is centralized');
      assert.deepStrictEqual([...protocol.keyframeReasons], ['join', 'gap', 'digest', 'replay-error', 'backpressure', 'topology', 'manual-debug'], 'DOIT.72 P1.1: keyframe request reasons are centralized');
      assert.deepStrictEqual([...protocol.sequenceFields], ['epoch', 'sequence', 'baseSequence'], 'DOIT.72 P1.1: replay sequence fields are centralized');
      assert.deepStrictEqual([...protocol.terminalPlaceholder.fields], ['placeholderId', 'session', 'rows', 'cols', 'terminalEpoch'], 'DOIT.72 P1.1: terminal placeholder metadata fields are centralized');
      assert.equal(protocol.frames.inputIntent, 'input-intent', 'DOIT.72 P5.1: write-mode input intents use a protocol-owned frame name');
      assert.deepStrictEqual(canonical(protocol.inputIntentTypes), {
        hostCommand: 'host-command',
        menuCommand: 'menu-command',
        tabActivate: 'tab-activate',
        terminalInput: 'terminal-input',
        terminalPaste: 'terminal-paste',
        terminalScroll: 'terminal-scroll',
      }, 'DOIT.72 P5.1: write-mode input intent type names are centralized');
      assert.equal(protocol.redaction.policyVersion, 1, 'DOIT.72 P1.1: redaction policy version is centralized');
      assert.equal(protocol.debugNames.domKeyframe, 'DOM keyframe', 'DOIT.72 P1.1: debug names are centralized');
      assert.equal(metaApi.shareGeometryRepairActionForDiffForTest('domDigest'), protocol.frames.domKeyframe, 'DOIT.72 P1.1: dom digest repair asks for the protocol keyframe frame');
      assert.equal(metaApi.shareGeometryRepairActionForDiffForTest('terminalCells'), protocol.frames.terminalHostResize, 'DOIT.72 P1.1: terminal drift repair uses the protocol resize frame');
      const sequencedTypes = ['layout', 'viewport', 'appearance', 'popup-layer', 'geometry-digest', 'host-resize'];
      const frames = sequencedTypes.map(type => metaApi.shareBuildUiMessageForTest(type, {kind: type}, {reason: `test-${type}`}));
      frames.forEach((frame, index) => {
        assert.equal(frame.epoch, 1, `${frame.type} carries the current mirror epoch`);
        assert.equal(frame.sequence, index + 1, `${frame.type} carries a monotonic mirror sequence`);
        assert.equal(frame.reason, `test-${frame.type}`, `${frame.type} carries a mirror reason`);
        assert.equal(frame.sender.length > 0, true, `${frame.type} carries a sender id`);
      });
      const uiStateFrame = metaApi.shareBuildUiMessageForTest('ui-state', {layout: 'left'}, {reason: 'full-reset'});
      assert.ok(uiStateFrame.epoch > frames[frames.length - 1].epoch, 'full ui-state frames advance the mirror epoch');
      assert.equal(uiStateFrame.sequence, frames.length + 1, 'full ui-state frames continue the sender sequence');
      assert.equal(uiStateFrame.reason, 'full-reset', 'ui-state frames carry the reset reason');
      const keyframeFrame = metaApi.shareBuildUiMessageForTest(protocol.frames.domKeyframe, {root: {}}, {reason: 'join'});
      assert.equal(keyframeFrame.version, protocol.version, 'DOIT.72 P1.1: replay frames carry the protocol version');
      assert.equal(keyframeFrame.reason, 'join', 'DOIT.72 P1.1: replay frames carry protocol-owned reason metadata');
      assert.equal(keyframeFrame.epoch, 2, 'YO!share replay keyframes use a replay-only epoch that semantic frames cannot advance');
      assert.equal(keyframeFrame.sequence, 1, 'YO!share replay keyframes use a replay-only sequence that semantic frames cannot advance');
      const interleavedUiStateFrame = metaApi.shareBuildUiMessageForTest('ui-state', {layout: 'left'}, {reason: 'finder-semantic'});
      const interleavedGeometryFrame = metaApi.shareBuildUiMessageForTest('geometry-digest', {digest: 'safari-jitter'}, {reason: 'safari-cadence'});
      const deltaFrame = metaApi.shareBuildUiMessageForTest(protocol.frames.domDelta, {mutations: []}, {reason: 'mutation'});
      assert.ok(interleavedUiStateFrame.sequence > frames.length + 1, 'semantic frames continue their own mirror sequence between replay frames');
      assert.equal(interleavedGeometryFrame.type, 'geometry-digest', 'Safari cadence geometry frames are semantic frames');
      assert.ok(interleavedGeometryFrame.sequence > interleavedUiStateFrame.sequence, 'geometry digest frames continue the semantic sequence between replay frames');
      assert.equal(deltaFrame.epoch, keyframeFrame.epoch, 'YO!share replay deltas stay in the keyframe replay epoch despite interleaved semantic frames');
      assert.equal(deltaFrame.baseSequence, keyframeFrame.sequence, 'YO!share replay delta base points at the previous replay frame, not the previous semantic frame');
      assert.equal(deltaFrame.sequence, keyframeFrame.sequence + 1, 'YO!share replay delta sequence is contiguous with the replay keyframe');

      const replayOffApi = loadYolomux('', ['1']);
      assert.equal(replayOffApi.shareReplayFeatureEnabledForTest(), false, 'DOIT.72 P1.2: DOM replay keyframes are feature-flagged off by default');
      assert.equal(replayOffApi.shareCreateDomKeyframePayloadForTest('join'), null, 'DOIT.72 P1.2: disabled replay flag prevents host keyframe serialization');

      const replayShellApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-replay-shell', mode: 'ro', session: '1', sessions: ['1']},
      });
      assert.equal(replayShellApi.shareReplayFeatureEnabledForTest(), true, 'DOIT.72 P4.1: read-only share viewers enable replay by default');
      assert.equal(replayShellApi.shareReplayShellEnabledForTest(), true, 'DOIT.72 P4.1: read-only share viewers boot the replay shell without shareReplay=1');
      const replayWriteApi = loadYolomux('?shareReplay=1', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-replay-write', mode: 'rw', session: '1', sessions: ['1']},
      });
      assert.equal(replayWriteApi.shareReplayShellEnabledForTest(), true, 'DOIT.72 P5.2: write shares boot the replay shell once input forwarding exists');
      assert.equal(replayWriteApi.shareCanPublishUiForTest(), false, 'DOIT.72 P5.3: write replay viewers do not publish semantic UI-state/layout/popup frames');
      const replayDisabledShellApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-replay-off', mode: 'ro', session: '1', sessions: ['1']},
      });
      assert.equal(replayDisabledShellApi.shareReplaySemanticEscapeEnabledForTest(), true, 'DOIT.72 P4.1: shareReplay=0 is the temporary semantic escape hatch');
      assert.equal(replayDisabledShellApi.shareReplayShellEnabledForTest(), false, 'DOIT.72 P4.1: the semantic escape hatch disables the read-only replay shell');
      const replayStorageEscapeApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        localStorage: {'yolomux.shareReplaySemantic': '1'},
        share: {view: true, id: 'share-replay-storage-off', mode: 'ro', session: '1', sessions: ['1']},
      });
      assert.equal(replayStorageEscapeApi.shareReplayShellEnabledForTest(), false, 'DOIT.72 P4.1: the stored semantic escape hatch disables default read-only replay');

      const replayApi = loadYolomux('?shareReplay=1', ['1'], 'https:');
      assert.equal(replayApi.shareReplayFeatureEnabledForTest(), true, 'DOIT.72 P1.2: shareReplay=1 enables host keyframe serialization');
      const replayRoot = replayApi.appRootForTest();
      replayRoot.replaceChildren();
      const finder = new TestElement('finder-fixture', 'section');
      finder.className = 'file-explorer-panel';
      finder.dataset.shareSurface = 'finder';
      finder.textContent = 'Finder files';
      const editor = new TestElement('editor-fixture', 'article');
      editor.className = 'file-editor-panel';
      editor.dataset.item = 'file:/repo/README.md';
      editor.textContent = 'Editor body';
      const prefs = new TestElement('prefs-fixture', 'section');
      prefs.className = 'preferences-panel';
      prefs.textContent = 'Preferences body';
  	    const popup = new TestElement('popup-fixture', 'div');
  	    popup.className = 'app-menu-popover open';
  	    popup.textContent = 'Popup body';
  	    const customTag = new TestElement('custom-think', 'think');
  	    customTag.textContent = 'custom think marker';
  	    const privateNode = new TestElement('private-fixture', 'div');
  	    privateNode.dataset.sharePrivate = '1';
  	    privateNode.textContent = 'private token text';
      const terminal = new TestElement('term-1', 'div');
      terminal.className = 'terminal';
      const terminalInternal = new TestElement('xterm-internal', 'div');
      terminalInternal.className = 'xterm-screen';
      terminalInternal.textContent = 'xterm internal text';
      terminal.appendChild(terminalInternal);
      const terminalTwo = new TestElement('term-2', 'div');
      terminalTwo.className = 'terminal';
      const terminalTwoInternal = new TestElement('xterm-internal-2', 'div');
      terminalTwoInternal.className = 'xterm-screen';
      terminalTwoInternal.textContent = 'xterm second internal text';
      terminalTwo.appendChild(terminalTwoInternal);
      const hiddenTerminal = new TestElement('term-hidden', 'div');
      hiddenTerminal.className = 'terminal';
      hiddenTerminal.setAttribute('hidden', '');
      hiddenTerminal.rect = {width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0};
      const hiddenTerminalInternal = new TestElement('xterm-hidden-internal', 'div');
      hiddenTerminalInternal.className = 'xterm-screen';
      hiddenTerminalInternal.textContent = 'hidden xterm internal text';
      hiddenTerminal.appendChild(hiddenTerminalInternal);
      const panelPoolNode = replayApi.testElementForId('panelPool');
      panelPoolNode.replaceChildren();
      const pooledTerminal = new TestElement('term-pooled', 'div');
      pooledTerminal.className = 'terminal';
      const pooledTerminalInternal = new TestElement('xterm-pooled-internal', 'div');
      pooledTerminalInternal.className = 'xterm-screen';
      pooledTerminalInternal.textContent = 'pooled xterm internal text';
      pooledTerminal.appendChild(pooledTerminalInternal);
      panelPoolNode.appendChild(pooledTerminal);
  	    replayRoot.append(finder, editor, prefs, popup, customTag, privateNode, terminal, terminalTwo, hiddenTerminal, panelPoolNode);
      replayApi.registerTerminalForTest('1', {rows: 28, cols: 106, focus() {}});
      replayApi.registerTerminalForTest('2', {rows: 31, cols: 120, focus() {}});
      replayApi.registerTerminalForTest('hidden', {rows: 40, cols: 140, focus() {}});
      replayApi.registerTerminalForTest('pooled', {rows: 50, cols: 150, focus() {}});
      const replayMessage = replayApi.shareCreateDomKeyframeMessageForTest('join');
      assert.equal(replayMessage.type, replayApi.shareMirrorProtocolForTest.frames.domKeyframe, 'DOIT.72 P1.2: serializer returns a dom-keyframe message');
      assert.equal(replayMessage.version, replayApi.shareMirrorProtocolForTest.version, 'DOIT.72 P1.2: keyframe message carries replay protocol version');
      assert.equal(replayMessage.reason, 'join', 'DOIT.72 P1.2: keyframe message carries the requested keyframe reason');
      const replayPayload = replayMessage.payload;
      assert.equal(replayPayload.root.tag, 'div', 'DOIT.72 P1.2: keyframe root serializes #appRoot');
      assert.equal(replayPayload.root.attrs.id, 'appRoot', 'DOIT.72 P1.2: root attributes are captured');
      assert.deepStrictEqual(Object.keys(replayPayload.assets).sort(), ['css', 'fonts', 'js'], 'DOIT.72 P1.2: keyframe captures asset and font fingerprints');
      assert.deepStrictEqual(Object.keys(replayPayload.viewport).sort(), ['height', 'width'], 'DOIT.72 P1.2: keyframe captures host viewport');
      const walkReplayNodes = node => [node, ...(node.children || []).flatMap(walkReplayNodes)];
      const replayNodes = walkReplayNodes(replayPayload.root);
      assert.deepStrictEqual(replayNodes.map(node => node.nodeId), replayNodes.map((_node, index) => index + 1), 'DOIT.72 P1.2: node ids are stable and sequential within each keyframe');
  	    const replayJson = JSON.stringify(replayPayload.root);
  	    assert.ok(replayJson.includes('Finder files') && replayJson.includes('Editor body') && replayJson.includes('Preferences body') && replayJson.includes('Popup body'), 'DOIT.72 P1.2: representative app surfaces are serialized');
  	    assert.ok(replayJson.includes('custom think marker'), 'YO!share keyframes preserve text from browser-created custom tags');
  	    const customReplayNode = replayNodes.find(node => node.attrs?.id === 'custom-think');
  	    assert.equal(customReplayNode?.tag, 'span', 'YO!share keyframes coerce unsupported custom tags to inert spans');
  	    assert.equal(replayJson.includes('"tag":"think"'), false, 'YO!share keyframes never emit unsupported custom tag names');
  	    assert.equal(replayJson.includes('private token text'), false, 'DOIT.72 P1.2: private nodes are excluded from the keyframe');
      assert.equal(replayJson.includes('xterm internal text'), false, 'DOIT.72 P1.2: terminal internals are excluded from the keyframe');
      assert.equal(replayJson.includes('xterm second internal text'), false, 'DOIT.72 P3.1: second visible terminal internals are excluded from the keyframe');
      assert.equal(replayJson.includes('hidden xterm internal text'), false, 'DOIT.72 P3.1: hidden terminal internals are excluded from the keyframe');
      assert.equal(replayJson.includes('pooled xterm internal text'), false, 'DOIT.72 P3.1: pooled terminal internals are excluded from the keyframe');
      const terminalPlaceholderNodes = replayNodes.filter(node => node.attrs?.['data-share-terminal-placeholder']);
      assert.deepStrictEqual(canonical(terminalPlaceholderNodes.map(node => ({
        session: node.attrs['data-share-terminal-placeholder'],
        rows: node.attrs['data-rows'],
        cols: node.attrs['data-cols'],
      }))), [
        {session: '1', rows: '28', cols: '106'},
        {session: '2', rows: '31', cols: '120'},
      ], 'DOIT.72 P3.1: visible terminal DOM is replaced with one placeholder node per live terminal');
      assert.deepStrictEqual(Array.from(replayPayload.terminals, entry => ({...entry})), [
        {placeholderId: 'term-ph-1', session: '1', rows: 28, cols: 106, terminalEpoch: 1},
        {placeholderId: 'term-ph-2', session: '2', rows: 31, cols: 120, terminalEpoch: 1},
      ], 'DOIT.72 P3.1: terminal placeholder metadata records host terminal dimensions for visible terminals only');
      assert.equal(replayPayload.terminals.some(entry => entry.session === 'hidden' || entry.session === 'pooled'), false, 'DOIT.72 P3.1: hidden and pooled terminals do not create stale placeholder metadata');
      assert.deepStrictEqual({...replayPayload.redaction}, {policyVersion: 1, removedCount: 3}, 'DOIT.72 P3.1: keyframe records redaction metadata for private, hidden terminal, and pooled terminal exclusions');
      const replayPayloadAgain = replayApi.shareCreateDomKeyframePayloadForTest('join');
      assert.equal(replayApi.stableDigestJson(replayPayloadAgain.root), replayApi.stableDigestJson(replayPayload.root), 'DOIT.72 P1.2: unchanged DOM serializes with stable per-keyframe ids');

      const scopedReplayApi = loadYolomux('?shareReplay=1', ['allowed-session', 'blocked-session'], 'https:');
      scopedReplayApi.setActiveSharesForTest([{token: 'share-token', session: 'allowed-session', sessions: ['allowed-session']}]);
      const scopedRoot = scopedReplayApi.appRootForTest();
      scopedRoot.replaceChildren();
      const allowedTerminal = new TestElement('term-allowed-session', 'div');
      allowedTerminal.className = 'terminal';
      allowedTerminal.appendChild(new TestElement('allowed-internal', 'div'));
      allowedTerminal.children[0].textContent = 'allowed terminal internals';
      const blockedTerminal = new TestElement('term-blocked-session', 'div');
      blockedTerminal.className = 'terminal';
      blockedTerminal.appendChild(new TestElement('blocked-internal', 'div'));
      blockedTerminal.children[0].textContent = 'blocked terminal internals';
      scopedRoot.append(allowedTerminal, blockedTerminal);
      scopedReplayApi.registerTerminalForTest('allowed-session', {rows: 24, cols: 80, focus() {}});
      scopedReplayApi.registerTerminalForTest('blocked-session', {rows: 24, cols: 80, focus() {}});
      const scopedPayload = scopedReplayApi.shareCreateDomKeyframePayloadForTest('join');
      const scopedNodes = walkReplayNodes(scopedPayload.root);
      assert.deepStrictEqual([...scopedPayload.terminals.map(entry => entry.session)], ['allowed-session'], 'DOIT.0: replay terminal metadata is filtered to the active share session scope');
      assert.deepStrictEqual([...scopedNodes.filter(node => node.attrs?.['data-share-terminal-placeholder']).map(node => node.attrs['data-share-terminal-placeholder'])], ['allowed-session'], 'DOIT.0: unauthorized terminal DOM is not serialized as a healthy placeholder');
      assert.equal(JSON.stringify(scopedPayload.root).includes('blocked terminal internals'), false, 'DOIT.0: unauthorized terminal internals are dropped with the placeholder');

      replayRoot.replaceChildren();
      const scrollSurface = new TestElement('replay-scroll-surface', 'div');
      scrollSurface.className = 'preferences-scroll';
      scrollSurface.scrollTop = 321;
      scrollSurface.scrollLeft = 17;
      scrollSurface.scrollHeight = 900;
      scrollSurface.clientHeight = 200;
      scrollSurface.scrollWidth = 640;
      scrollSurface.clientWidth = 300;
      const terminalViewport = new TestElement('terminal-viewport', 'div');
      terminalViewport.className = 'xterm-viewport';
      terminalViewport.scrollTop = 222;
      terminalViewport.scrollHeight = 900;
      terminalViewport.clientHeight = 200;
      const terminalShell = new TestElement('term-2', 'div');
      terminalShell.className = 'terminal';
      terminalShell.appendChild(terminalViewport);
      replayRoot.append(scrollSurface, terminalShell);
      const scrollPayload = replayApi.shareCreateDomKeyframePayloadForTest('scroll');
      const scrollPayloadNodes = walkReplayNodes(scrollPayload.root);
      const scrollPayloadNode = scrollPayloadNodes.find(node => node.attrs?.id === 'replay-scroll-surface');
      assert.ok(scrollPayloadNode?.nodeId, 'DOIT.72 P2.4: scroll surface is present in the serialized replay root');
      assert.deepStrictEqual(canonical(scrollPayload.scroll.map(entry => ({nodeId: entry.nodeId, target: entry.target, kind: entry.kind, top: entry.top, left: entry.left}))), [
        {nodeId: scrollPayloadNode.nodeId, target: 'preferences', kind: 'preferences', top: 321, left: 17},
      ], 'DOIT.72 P2.4: keyframes capture scrollable mirrored nodes by replay node id');
      assert.equal(replayApi.shareReplayScrollEntryForElementForTest(terminalViewport), null, 'DOIT.72 P2.4: terminal internals are excluded from replay scroll capture');
      const hostPointer = replayApi.sharePointerPayloadForPointForTest(123, 456, {click: true});
      assert.deepStrictEqual(canonical(hostPointer), {scope: 'viewport', x: 123, y: 456, click: true}, 'DOIT.72 P2.4: host pointer payload stays in app-space coordinates for replay deltas');
      assert.deepStrictEqual(canonical(replayApi.shareReplayPointerPayloadForTest({...hostPointer, visible: true}, 'host-browser')), {scope: 'viewport', x: 123, y: 456, visible: true, click: true, sender: 'host-browser'}, 'DOIT.72 P2.4: replay pointer payload preserves sender, click, and app-space coordinates');

      replayRoot.replaceChildren();
      const unsafeLink = new TestElement('unsafe-link', 'a');
      unsafeLink.setAttribute('href', 'javascript:alert(1)');
      unsafeLink.setAttribute('onclick', 'window.bad=1');
      unsafeLink.textContent = 'Open https://host.example/share/abc123#t=secret-token token=secret-token';
      const tokenAttrs = new TestElement('token-attrs', 'div');
      tokenAttrs.setAttribute('data-share-token', 'secret-token');
      tokenAttrs.setAttribute('shareToken', 'secret-token');
      tokenAttrs.setAttribute('title', '/share/abc123#t=secret-token');
      tokenAttrs.textContent = 'visible title';
      const passwordInput = new TestElement('password-fixture', 'input');
      passwordInput.setAttribute('type', 'password');
      passwordInput.setAttribute('value', 'secret-token');
      const scriptNode = new TestElement('script-fixture', 'script');
      scriptNode.textContent = 'window.bad = true';
      const redactedNode = new TestElement('redacted-fixture', 'div');
      redactedNode.dataset.shareRedact = '1';
      redactedNode.textContent = 'hidden redacted node';
      replayRoot.append(unsafeLink, tokenAttrs, passwordInput, scriptNode, redactedNode);
      const redactedPayload = replayApi.shareCreateDomKeyframePayloadForTest('manual-debug');
      const redactedNodes = walkReplayNodes(redactedPayload.root);
      const redactedJson = JSON.stringify(redactedPayload.root);
      assert.equal(redactedJson.includes('secret-token'), false, 'DOIT.72 P1.3: keyframe sanitizer removes token values');
      assert.equal(redactedJson.includes('/share/abc123'), false, 'DOIT.72 P1.3: keyframe sanitizer removes share URLs');
      assert.equal(redactedJson.includes('onclick'), false, 'DOIT.72 P1.3: keyframe sanitizer removes inline event handlers');
      assert.equal(redactedJson.includes('javascript:'), false, 'DOIT.72 P1.3: keyframe sanitizer removes dangerous URL schemes');
      assert.equal(redactedJson.includes('data-share-token'), false, 'DOIT.72 P1.3: keyframe sanitizer removes token-bearing attributes');
      assert.equal(redactedJson.includes('shareToken'), false, 'DOIT.72 P1.3: keyframe sanitizer removes camelCase token attributes');
      assert.equal(redactedJson.includes('window.bad'), false, 'DOIT.72 P1.3: keyframe sanitizer removes script execution content');
      assert.equal(redactedJson.includes('hidden redacted node'), false, 'DOIT.72 P1.3: keyframe sanitizer excludes data-share-redact nodes');
      assert.ok(redactedNodes.some(node => node.attrs?.['data-share-redacted'] === 'secret'), 'DOIT.72 P1.3: password/secret fields serialize as placeholders');
      assert.equal(replayApi.shareReplayRedactTextForTest('/share/abc123#t=secret-token').includes('/share/abc123'), false, 'DOIT.72 P1.3: direct redactor removes share paths');
      assert.equal(replayApi.shareReplaySanitizeAttributeForTest('href', 'javascript:alert(1)'), null, 'DOIT.72 P1.3: attribute sanitizer rejects dangerous href');
      assert.equal(replayApi.shareReplaySanitizeAttributeForTest('shareToken', 'secret-token'), null, 'DOIT.72 P1.3: attribute sanitizer rejects token attributes');
      const debugCopy = replayApi.shareDebugTextForClipboardForTest({
        url: 'https://host.example/share/abc123#t=secret-token',
        token: 'secret-token',
        nested: {shareToken: 'secret-token', text: 'token=secret-token'},
      });
      assert.equal(debugCopy.includes('secret-token'), false, 'DOIT.72 P1.3: debug copy redacts token values through the shared sanitizer');
      assert.equal(debugCopy.includes('/share/abc123'), false, 'DOIT.72 P1.3: debug copy redacts share URLs through the shared sanitizer');
      assert.ok(debugCopy.includes('[redacted-share-token]') || debugCopy.includes('[redacted-share-url]'), 'DOIT.72 P1.3: debug copy includes explicit redaction markers');
      const debugUpload = JSON.stringify(replayApi.shareDebugProfileUploadPayloadForTest('share-replay-health', {
        url: 'https://host.example/share/abc123#t=secret-token',
        token: 'secret-token',
        nested: {shareToken: 'secret-token', text: 'token=secret-token'},
      }));
      assert.equal(debugUpload.includes('secret-token'), false, 'YO!share debug/profiling upload payload redacts token values before POST');
      assert.equal(debugUpload.includes('/share/abc123'), false, 'YO!share debug/profiling upload payload redacts share URLs before POST');

      const deltaApi = loadYolomux('?shareReplay=1', ['1'], 'https:');
      const deltaRoot = deltaApi.appRootForTest();
      deltaRoot.replaceChildren();
      const deltaTarget = new TestElement('delta-target', 'div');
      deltaTarget.textContent = '';
      const textNode = {
        nodeType: 3,
        textContent: 'old target',
        parentElement: null,
        contains(node) { return node === this; },
        matches() { return false; },
        closest() { return null; },
      };
      deltaTarget.appendChild(textNode);
      deltaRoot.append(deltaTarget);
      deltaTarget.setAttribute('title', 'Open /share/abc123#t=secret-token token=secret-token');
      const titleRecord = {type: 'attributes', target: deltaTarget, attributeName: 'title'};
      deltaTarget.setAttribute('href', 'javascript:alert(1)');
      const hrefRecord = {type: 'attributes', target: deltaTarget, attributeName: 'href'};
      deltaTarget.setAttribute('onclick', 'window.bad = true');
      const onclickRecord = {type: 'attributes', target: deltaTarget, attributeName: 'onclick'};
      deltaApi.shareCreateDomKeyframePayloadForTest('join');
      textNode.textContent = 'Changed token=secret-token /share/abc123#t=secret-token';
      const addedNode = new TestElement('delta-added', 'span');
      addedNode.setAttribute('onclick', 'window.bad = true');
      addedNode.textContent = 'Added /share/abc123#t=secret-token';
      const volatileNode = new TestElement('delta-volatile', 'span');
      volatileNode.className = 'share-replay-volatile';
      volatileNode.textContent = 'volatile timer text';
      const terminalNode = new TestElement('term-1', 'div');
      terminalNode.className = 'terminal';
      terminalNode.textContent = 'terminal internal text';
      const terminalWrapper = new TestElement('terminal-wrapper', 'section');
      const movedTerminalNode = new TestElement('term-1', 'div');
      movedTerminalNode.className = 'terminal';
      terminalWrapper.append(movedTerminalNode);
  	    const records = [
  	      {type: 'characterData', target: textNode},
  	      titleRecord,
  	      hrefRecord,
  	      onclickRecord,
  	      {type: 'childList', target: deltaRoot, addedNodes: [addedNode, terminalWrapper], removedNodes: []},
  	    ];
  	    const deltaEntries = deltaApi.shareReplayMutationEntriesForTest(records);
  	    const deltaJson = JSON.stringify(deltaEntries);
  	    assert.equal(deltaJson.includes('secret-token'), false, 'DOIT.72 P2.1: mutation delta redacts token values');
  	    assert.equal(deltaJson.includes('/share/abc123'), false, 'DOIT.72 P2.1: mutation delta redacts share URLs');
  	    assert.equal(deltaJson.includes('javascript:'), false, 'DOIT.72 P2.1: mutation delta removes dangerous URL values');
  	    assert.equal(deltaJson.includes('onclick'), false, 'DOIT.72 P2.1: mutation delta drops inline handler attributes');
  	    assert.ok(deltaEntries.some(entry => entry.kind === 'characterData' && entry.text.includes('[redacted-share-token]')), 'DOIT.72 P2.1: characterData mutations are captured and redacted');
  	    assert.ok(deltaEntries.some(entry => entry.kind === 'attributes' && entry.name === 'title' && entry.value.includes('[redacted-share-url]')), 'DOIT.72 P2.1: attribute mutations are captured and redacted');
  	    assert.ok(deltaEntries.some(entry => entry.kind === 'attributes' && entry.name === 'href' && entry.value === null && entry.removed === true), 'DOIT.72 P2.1: unsafe URL attribute mutations become removals');
  	    const childListEntry = deltaEntries.find(entry => entry.kind === 'childList');
  	    assert.equal(childListEntry.added.length, 2, 'DOIT.72 P2.1: childList mutations include safe added nodes and terminal placeholder wrappers');
  	    assert.equal(childListEntry.added[0].attrs.onclick, undefined, 'DOIT.72 P2.1: added node serialization sanitizes attributes');
  	    const unsupportedAddedNode = new TestElement('delta-think', 'think');
  	    unsupportedAddedNode.textContent = 'delta custom marker';
  	    const unsupportedDeltaEntries = deltaApi.shareReplayMutationEntriesForTest([
  	      {type: 'childList', target: deltaRoot, addedNodes: [unsupportedAddedNode], removedNodes: []},
  	    ]);
  	    assert.equal(unsupportedDeltaEntries[0].added[0].tag, 'span', 'YO!share deltas coerce unsupported custom tags to inert spans');
  	    assert.equal(unsupportedDeltaEntries[0].added[0].text, 'delta custom marker', 'YO!share deltas preserve unsupported custom tag text');
  	    assert.equal(JSON.stringify(unsupportedDeltaEntries).includes('"tag":"think"'), false, 'YO!share deltas never emit unsupported custom tag names');
  	    assert.deepStrictEqual(JSON.parse(JSON.stringify(deltaEntries.terminals)), [{placeholderId: 'term-ph-1', session: '1', rows: 0, cols: 0, terminalEpoch: 1}], 'YO!share DOM deltas carry terminal placeholder metadata for moved terminal subtrees');
  	    const ignoredChildListEntries = deltaApi.shareReplayMutationEntriesForTest([
  	      {type: 'childList', target: deltaRoot, addedNodes: [volatileNode, terminalNode], removedNodes: []},
  	    ]);
  	    const ignoredJson = JSON.stringify(ignoredChildListEntries);
  	    assert.equal(ignoredChildListEntries.length, 0, 'DOIT.72 P2.1: childList mutations touching ignored nodes wait for a keyframe instead of emitting partial deltas');
  	    assert.equal(ignoredJson.includes('volatile timer text'), false, 'DOIT.72 P2.1: mutation delta suppresses volatile nodes');
  	    assert.equal(ignoredJson.includes('terminal internal text'), false, 'DOIT.72 P2.1: mutation delta suppresses terminal internals');
  	    deltaApi.shareReplayEnqueueMutationRecordsForTest(records);
      const deltaBatch = deltaApi.shareReplayLastDeltaBatchForTest();
      assert.equal(deltaBatch.count, deltaEntries.length, 'DOIT.72 P2.1: mutation records coalesce into one dom-delta batch');
      assert.deepStrictEqual(deltaBatch.mutations, deltaEntries, 'DOIT.72 P2.1: coalesced batch preserves sanitized mutation entries');

      const rwApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-rw-meta', mode: 'rw', session: '1', sessions: ['1']},
      });
      const writeViewerFrame = rwApi.shareBuildUiMessageForTest('viewport', {width: 900, height: 600}, {reason: 'writer-viewport'});
      assert.equal(Number.isFinite(writeViewerFrame.epoch), true, 'rw viewers use the same sequenced frame builder as hosts');
      assert.equal(writeViewerFrame.reason, 'writer-viewport', 'rw viewer frames carry the shared mirror reason');

      const staleApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-stale-meta', mode: 'ro', session: '1', sessions: ['1']},
      });
      assert.equal(staleApi.shareDropStaleMirrorFrameForTest({type: 'ui-state', sender: 'host-a', epoch: 4, sequence: 9}), false, 'first host frame applies');
      assert.deepStrictEqual({...staleApi.shareMirrorLastFrameForTest('host-a')}, {epoch: 4, sequence: 9}, 'viewer records last mirror frame per sender');
      assert.equal(staleApi.shareDropStaleMirrorFrameForTest({type: 'layout', sender: 'host-a', epoch: 3, sequence: 99}), true, 'lower-epoch layout frame is stale even with a higher sequence');
      assert.equal(staleApi.shareDropStaleMirrorFrameForTest({type: 'viewport', sender: 'host-a', epoch: 4, sequence: 8}), true, 'same-epoch lower sequence frame is stale');
      assert.equal(staleApi.shareDropStaleMirrorFrameForTest({type: 'dom-keyframe', sender: 'host-a', epoch: 2, sequence: 1}), false, 'DOM replay keyframes are not dropped by newer semantic frame metadata from the same sender');
      assert.deepStrictEqual({...staleApi.shareMirrorLastFrameForTest('host-a', 'dom-replay')}, {epoch: 2, sequence: 1}, 'viewer records DOM replay stale state separately from semantic state');
      assert.equal(staleApi.shareDropStaleMirrorFrameForTest({type: 'appearance', sender: 'host-b', epoch: 1, sequence: 1}), false, 'different sender has an independent sequence');
      assert.equal(staleApi.shareDropStaleMirrorFrameForTest({type: 'layout', sender: 'host-a'}), false, 'legacy unsequenced frames still apply during migration');
    }
    {
      const roApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share123', mode: 'ro', session: '1', sessions: ['1']},
      });
      assert.equal(roApi.shareReadOnlyReplayModeEnabledForTest(), false, 'DOIT.72 P4.2: shareReplay=0 opts read-only viewers into the semantic mirror path');
      assert.equal(roApi.shareSemanticReadOnlyMirrorEnabledForTest(), true, 'DOIT.72 P4.2: the semantic read-only escape hatch keeps legacy semantic guards active');
      const stage = roApi.ensureShareMirrorStageForTest();
      assert.equal(stage.id, 'shareMirrorStage', 'read-only share view creates the mirror stage');
      assert.equal(roApi.testElementForId('appRoot').parentElement, stage, 'read-only share view moves appRoot under the mirror stage');
      const target = activates => ({
        closest(selector) {
          if (selector === '[data-share-viewer-control]') return null;
          return activates ? {nodeType: 1} : null;
        },
      });
      const viewerControlTarget = {
        closest(selector) {
          return selector === '[data-share-viewer-control]' ? {nodeType: 1} : null;
        },
      };
      const eventFor = (type, options = {}) => ({
        type,
        key: options.key || '',
        ctrlKey: options.ctrlKey === true,
        metaKey: options.metaKey === true,
        shiftKey: options.shiftKey === true,
        target: options.viewerControl ? viewerControlTarget : target(options.activates === true),
        defaultPrevented: false,
        stopped: false,
        immediateStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; },
        stopImmediatePropagation() { this.immediateStopped = true; },
      });
      const replayReadOnlyApi = loadYolomux('', ['1', '2'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-replay-semantic-guard', mode: 'ro', session: '1', sessions: ['1', '2']},
      });
      assert.equal(replayReadOnlyApi.shareReadOnlyReplayModeEnabledForTest(), true, 'DOIT.72 P4.2: default read-only viewers are replay viewers');
      assert.equal(replayReadOnlyApi.shareSemanticReadOnlyMirrorEnabledForTest(), false, 'DOIT.72 P4.2: default replay viewers do not enable the semantic read-only path');
      const replayClick = eventFor('click', {activates: true});
      replayReadOnlyApi.blockShareReadonlyInteraction(replayClick);
      assert.equal(replayClick.defaultPrevented, false, 'DOIT.72 P4.2: replay viewers do not run the semantic readonly blocker');
      assert.equal(replayClick.immediateStopped, false, 'DOIT.72 P4.2: replay viewers do not stop mirrored DOM events through the semantic blocker');
      const replayTabsBefore = replayReadOnlyApi.layoutTabsParamValue(replayReadOnlyApi.currentSlots());
      replayReadOnlyApi.applyShareUiStateForTest({layout: 'row@50(left,slot1)', tabs: 'left:1;slot1:2', viewport: {width: 900, height: 600}});
      assert.equal(replayReadOnlyApi.layoutTabsParamValue(replayReadOnlyApi.currentSlots()), replayTabsBefore, 'DOIT.72 P4.2: replay viewers ignore direct semantic ui-state apply calls');
      replayReadOnlyApi.applyShareUiMessageForTest({ch: 'ui', type: 'layout', sender: 'host', payload: {layout: 'row@50(left,slot1)', tabs: 'left:1;slot1:2'}});
      assert.equal(replayReadOnlyApi.layoutTabsParamValue(replayReadOnlyApi.currentSlots()), replayTabsBefore, 'DOIT.72 P4.2: replay viewers ignore semantic layout UI messages');
      replayReadOnlyApi.applySharePopupLayerForTest({seq: 7, owner: 'host', items: [{rect: {left: 1, top: 1, width: 2, height: 2}, html: '<div>legacy popup</div>'}]}, 'host');
      assert.equal(replayReadOnlyApi.sharePopupLayerNodeForTest(), null, 'DOIT.72 P4.2: replay viewers ignore legacy popup-layer frames');
      replayReadOnlyApi.setShareReplaySequenceStateForTest(7, 121);
      const staleDeltaStatus = replayReadOnlyApi.shareReplayDeltaSequenceStatusForTest({epoch: 7, sequence: 121, baseSequence: 120});
      assert.equal(staleDeltaStatus.reason, 'stale', 'YO!share replay drops late same-epoch deltas after a newer keyframe');
      replayReadOnlyApi.applyShareReplayDeltaForTest({mutations: [], digest: ''}, {type: 'dom-delta', sender: 'host', epoch: 7, sequence: 121, baseSequence: 120});
      assert.equal(replayReadOnlyApi.shareReplaySequenceStateForTest().stale, 1, 'YO!share replay records stale deltas separately');
      assert.equal(replayReadOnlyApi.shareReplaySequenceStateForTest().dropped, 0, 'stale replay deltas do not mark the viewer behind');
      assert.equal(replayReadOnlyApi.shareReplaySequenceStateForTest().requests, 0, 'stale replay deltas do not request another keyframe');
      const futureDeltaStatus = replayReadOnlyApi.shareReplayDeltaSequenceStatusForTest({epoch: 7, sequence: 124, baseSequence: 123});
    assert.equal(futureDeltaStatus.reason, 'gap', 'YO!share replay still treats truly missing deltas as a repair gap');
    assert.equal(futureDeltaStatus.lastSequence, 121, 'YO!share replay gap diagnostics include the local sequence cursor');
    assert.equal(replayReadOnlyApi.shareReplayDeltaCanApplyBestEffortForTest(futureDeltaStatus), true, 'YO!share replay can apply same-epoch gap deltas while it waits for throttled keyframe repair');
      const replayRoot = replayReadOnlyApi.testElementForId('appRoot');
      const replayTab = new TestElement('replay-tab');
      replayTab.className = 'pane-tab';
      replayTab.dataset.paneTab = '1';
      const replayPopover = new TestElement('replay-popover');
      replayPopover.className = 'session-popover';
      replayTab.appendChild(replayPopover);
      replayRoot.appendChild(replayTab);
      assert.equal(replayReadOnlyApi.bindShareReplayPaneTabPopoversForTest(replayRoot), 0, 'read-only YO!share DOM replay does not create client-local hover popovers');
      assert.equal(replayTab.dataset.shareReplayPopoverBound, undefined, 'read-only YO!share leaves host-owned popover state unbound on the viewer');
      assert.equal((replayTab.listeners.get('pointerenter') || []).length, 0, 'read-only YO!share tab popovers do not install viewer-local hover listeners');
      assert.equal(replayReadOnlyApi.bindShareReplayPaneTabPopoversForTest(replayRoot), 0, 'YO!share replay tab popover binding is idempotent');
      const selectionDown = eventFor('mousedown');
      roApi.blockShareReadonlyInteraction(selectionDown);
      assert.equal(selectionDown.defaultPrevented, false, 'read-only share mousedown keeps native text selection default');
      assert.equal(selectionDown.immediateStopped, true, 'read-only share mousedown still stops app handlers');
      const contextMenu = eventFor('contextmenu');
      roApi.blockShareReadonlyInteraction(contextMenu);
      assert.equal(contextMenu.defaultPrevented, false, 'read-only share contextmenu keeps native browser copy menu');
      assert.equal(contextMenu.immediateStopped, true, 'read-only share contextmenu suppresses YOLOmux context menus');
      const copyKey = eventFor('keydown', {ctrlKey: true, key: 'c'});
      roApi.blockShareReadonlyInteraction(copyKey);
      assert.equal(copyKey.defaultPrevented, false, 'read-only share Ctrl/Cmd-C keeps browser copy default');
      const arrowKey = eventFor('keydown', {key: 'ArrowDown'});
      roApi.blockShareReadonlyInteraction(arrowKey);
      assert.equal(arrowKey.defaultPrevented, true, 'read-only share navigation keys cannot scroll mirrored panes locally');
      const typingKey = eventFor('keydown', {key: 'x'});
      roApi.blockShareReadonlyInteraction(typingKey);
      assert.equal(typingKey.defaultPrevented, true, 'read-only share typing is blocked');
      const paste = eventFor('paste');
      roApi.blockShareReadonlyInteraction(paste);
      assert.equal(paste.defaultPrevented, true, 'read-only share paste mutation is blocked');
      const buttonClick = eventFor('click', {activates: true});
      roApi.blockShareReadonlyInteraction(buttonClick);
      assert.equal(buttonClick.defaultPrevented, true, 'read-only share button/link activation is blocked');
      const textClick = eventFor('click');
      roApi.blockShareReadonlyInteraction(textClick);
      assert.equal(textClick.defaultPrevented, false, 'read-only share plain text click keeps harmless browser default');
      const viewerFitClick = eventFor('click', {viewerControl: true});
      roApi.blockShareReadonlyInteraction(viewerFitClick);
      assert.equal(viewerFitClick.immediateStopped, false, 'share viewer chrome controls remain usable');
      const appRoot = roApi.testElementForId('appRoot');
      appRoot.classList.add('app-root');
      const editorPanel = new TestElement('', 'article');
      editorPanel.classList.add('file-editor-panel');
      editorPanel.dataset.filePath = '/tmp/a.md';
      editorPanel.dataset.layoutItem = 'file:/tmp/a.md';
      const scroller = new TestElement('', 'div');
      scroller.classList.add('cm-scroller');
      editorPanel.appendChild(scroller);
      appRoot.appendChild(editorPanel);
      assert.equal(roApi.shareCanPublishScrollForTest(), false, 'read-only share viewers cannot publish scroll frames');
      roApi.applyShareViewBodyClassesForTest();
      assert.equal(roApi.testElementForId('body').classList.contains('share-view-readonly'), true, 'read-only share view marks the body for hidden local scrollbars');
      roApi.setClientSettingsPatchForTest({general: {auto_focus: true}});
      const prefsHtml = roApi.preferencesPanelHtmlForTest('');
      assert.equal(prefsHtml.includes('preferences-readonly'), false, 'read-only share Preferences do not add client-only readonly chrome');
      assert.equal(/data-setting-path="general\.auto_focus"[^>]* disabled/.test(prefsHtml), false, 'read-only share Preferences controls stay visually host-identical');
      assert.equal(/data-setting-reset="general\.auto_focus"[^>]* disabled/.test(prefsHtml), false, 'read-only share Preferences reset buttons stay visually host-identical when the host would enable them');
      assert.equal(roApi.shareReadonlyTargetIsMirroredSurfaceForTest(scroller), true, 'read-only scroll target detection finds mirrored editor surfaces');
      const wheel = eventFor('wheel');
      wheel.target = scroller;
      roApi.blockShareReadonlyInteraction(wheel);
      assert.equal(wheel.defaultPrevented, true, 'read-only share wheel input is blocked on mirrored editor surfaces');
      const scrollbarPointer = eventFor('pointerdown');
      scrollbarPointer.target = scroller;
      roApi.blockShareReadonlyInteraction(scrollbarPointer);
      assert.equal(scrollbarPointer.defaultPrevented, true, 'read-only share pointerdown on the scroller itself cannot start a scrollbar drag');
      const editorText = new TestElement('', 'span');
      scroller.appendChild(editorText);
      const textPointer = eventFor('pointerdown');
      textPointer.target = editorText;
      roApi.blockShareReadonlyInteraction(textPointer);
      assert.equal(textPointer.defaultPrevented, false, 'read-only share pointerdown inside scroll content still allows text selection defaults');
      scroller.scrollTop = 999;
      scroller.scrollLeft = 17;
      roApi.setShareLastAppliedScrollForTest('editor:file:/tmp/a.md:editor', {top: 123, left: 4}, {
        kind: 'editor',
        path: '/tmp/a.md',
        item: 'file:/tmp/a.md',
        source: 'editor',
      });
      const scroll = eventFor('scroll');
      scroll.target = scroller;
      roApi.blockShareReadonlyInteraction(scroll);
      assert.equal(scroller.scrollTop, 123, 'read-only local scroll is restored to the last host scrollTop');
      assert.equal(scroller.scrollLeft, 4, 'read-only local scroll is restored to the last host scrollLeft');
      assert.deepStrictEqual({...roApi.shareLastAppliedScrollPayloadForTest('editor:file:/tmp/a.md:editor')}, {
        kind: 'editor',
        path: '/tmp/a.md',
        item: 'file:/tmp/a.md',
        source: 'editor',
        target: 'editor:file:/tmp/a.md:editor',
        top: 123,
        left: 4,
      }, 'read-only local scroll restore keeps a full target-key payload for later DOM replay');
      const diffScroller = new TestElement('', 'div');
      diffScroller.classList.add('file-explorer-changes-panel');
      diffScroller.scrollTop = 0;
      diffScroller.scrollLeft = 0;
      roApi.setDocumentQuerySelectorAllForTest(selector => selector === '.file-explorer-changes-panel' ? [diffScroller] : []);
      roApi.applyShareScrollStateForTest({target: 'finder:diff', kind: 'finder', mode: 'diff', top: 345, left: 6});
      diffScroller.scrollTop = 0;
      diffScroller.scrollLeft = 0;
      assert.equal(roApi.scheduleShareScrollRestoreByKeyForTest('finder:diff', {frames: 2}), true, 'pending Differ scroll schedules replay by target key after the pane appears');
      assert.equal(diffScroller.scrollTop, 345, 'pending Differ scroll replay restores the host scrollTop');
      assert.equal(diffScroller.scrollLeft, 6, 'pending Differ scroll replay restores the host horizontal scroll');
      const termCalls = [];
      roApi.registerTerminalForTest('1', {
        cols: 80,
        rows: 24,
        resize(cols, rows) { termCalls.push(['resize', cols, rows]); this.cols = cols; this.rows = rows; },
        reset() { termCalls.push(['reset']); },
        refresh(start, end) { termCalls.push(['refresh', start, end]); },
      });
      assert.equal(roApi.shareHostTerminalSizeForTest('missing'), null, 'share terminal sizing has no client-estimate fallback when host dims are absent');
      roApi.updateShareHostTerminalSizeForTest('1', 33, 111);
      assert.deepStrictEqual(termCalls.slice(0, 2), [['resize', 111, 33], ['reset']], 'host-resize applies host cols/rows then resets the viewer xterm buffer');
      termCalls.length = 0;
      const repairFrames = roApi.shareMirrorProtocolForTest.frames;
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('slots'), repairFrames.uiState, 'slot drift requests the semantic ui-state reset bucket while replay is not default');
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('tabStrips'), repairFrames.uiState, 'tab-strip drift requests the semantic ui-state reset bucket while replay is not default');
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('editors'), repairFrames.uiState, 'editor drift requests the semantic ui-state reset bucket while replay is not default');
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('textWraps'), repairFrames.textWrapMetrics, 'wrapped text drift uses host metrics repair');
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('terminalCells'), repairFrames.terminalHostResize, 'terminal-cell drift uses the host-resize/repaint repair path');
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('popup-layer'), repairFrames.popupLayer, 'popup drift has its own repair action');
      assert.equal(roApi.shareGeometryRepairActionForDiffForTest('domDigest'), repairFrames.domKeyframe, 'future DOM digest drift requests a replay keyframe');
      assert.equal(roApi.applyShareTerminalCellsRepairForTest([{session: '1', rows: 36, cols: 120}]), true, 'terminal-cell repair consumes host digest dimensions');
      assert.deepStrictEqual(termCalls.slice(0, 3), [['resize', 120, 36], ['reset'], ['refresh', 0, 35]], 'terminal-cell repair uses the same resize, reset, repaint ordering as host-resize frames');
      roApi.applySharePopupLayerForTest({seq: 2, owner: 'host', items: []}, 'host');
      assert.equal(roApi.sharePopupLayerLastSeqForTest('host'), 2, 'popup-layer applies the newest host frame sequence');
      assert.equal(roApi.sharePopupLayerNodeForTest().parentElement, appRoot, 'popup-layer mirror is mounted inside the scaled app root');
      roApi.applySharePopupLayerForTest({seq: 1, owner: 'host', items: [{rect: {left: 1, top: 1, width: 2, height: 2}, html: '<div>stale</div>'}]}, 'host');
      assert.equal(roApi.sharePopupLayerLastSeqForTest('host'), 2, 'stale popup-layer frames are ignored after a newer close frame');
    }
    api.setActiveLocaleForTest('ja');
    const appearance = api.shareAppearanceSnapshotForTest();
    assert.equal(appearance.locale, 'ja', 'share appearance snapshot includes the active locale');
    assert.equal(appearance.languagePref, 'system', 'share appearance snapshot includes the persisted language preference');
    api.setActiveLocaleForTest('en');

    const shareThemeApi = loadYolomux('', ['1'], 'https:', 'MacIntel', 'readonly', {
      shareReplay: true,
      share: {view: true, id: 'share-theme', mode: 'ro', session: '1', sessions: ['1']},
    });
    const shareTermCalls = [];
    let shareTextureClears = 0;
    shareThemeApi.registerTerminalForTest('1', {
      rows: 24,
      cols: 80,
      options: {},
      refresh(start, end) { shareTermCalls.push(['refresh', start, end]); },
      clearTextureAtlas() { shareTextureClears += 1; },
    });
    shareThemeApi.applyShareAppearanceStateForTest({theme: 'system', resolvedTheme: 'light', terminalTheme: 'follow-app'});
    assert.equal(shareThemeApi.globalThemeModeForTest(), 'system', 'share viewers preserve the host theme preference value');
    assert.equal(shareThemeApi.shareResolvedGlobalThemeModeForTest(), 'light', 'share viewers store the host resolved system theme');
    assert.equal(shareThemeApi.resolvedGlobalThemeModeForTest(), 'light', 'share viewers resolve system theme from the host frame, not local matchMedia');
    assert.ok(shareThemeApi.testElementForId('body').classList.contains('theme-resolved-light'), 'share viewer body uses the host-resolved light class');
    assert.ok(shareThemeApi.testElementForId('body').classList.contains('theme-light'), 'share viewer body applies normal light CSS for host-resolved System light');
    assert.ok(shareThemeApi.testElementForId('body').classList.contains('theme-system'), 'share viewer body preserves the host System preference marker');
    assert.equal(shareThemeApi.terminalThemeModeForTest(), 'follow-app', 'share viewers keep the real terminal theme setting value');
    assert.equal(shareTermCalls.at(-1)?.join(':'), 'refresh:0:23', 'share appearance applies a terminal repaint');
    assert.equal(shareTextureClears, 1, 'share appearance clears cached terminal glyph colors');
    shareThemeApi.applyShareAppearanceStateForTest({theme: 'system', resolvedTheme: 'dark', terminalTheme: 'follow-app'});
    assert.equal(shareThemeApi.resolvedGlobalThemeModeForTest(), 'dark', 'a later host OS flip updates the share viewer resolved theme');
    assert.ok(shareThemeApi.testElementForId('body').classList.contains('theme-resolved-dark'), 'share viewer body follows the later host-resolved dark class');
    assert.ok(shareThemeApi.testElementForId('body').classList.contains('theme-dark'), 'share viewer body applies normal dark CSS for host-resolved System dark');
    assert.equal(shareTermCalls.at(-1)?.join(':'), 'refresh:0:23', 'later share appearance frames repaint terminal cells too');
    assert.equal(shareTextureClears, 2, 'later share appearance frames clear cached glyph colors too');

    const replayThemeApi = loadYolomux('', ['1'], 'https:', 'MacIntel', 'readonly', {
      shareReplay: true,
      share: {view: true, id: 'share-replay-theme', mode: 'ro', session: '1', sessions: ['1']},
    });
    replayThemeApi.setShareReplaySequenceStateForTest(1, 0);
    replayThemeApi.applyShareUiMessageForTest({
      ch: 'ui',
      type: replayThemeApi.shareMirrorProtocolForTest.frames.appearance,
      sender: 'host',
      epoch: 1,
      sequence: 1,
      payload: {theme: 'system', resolvedTheme: 'light', terminalTheme: 'follow-app'},
    });
    assert.equal(replayThemeApi.resolvedGlobalThemeModeForTest(), 'light', 'replay-shell share viewers apply live appearance frames instead of swallowing them');
    assert.ok(replayThemeApi.testElementForId('body').classList.contains('theme-resolved-light'), 'replay-shell appearance frames repaint the viewer body');
    assert.ok(replayThemeApi.testElementForId('body').classList.contains('theme-light'), 'replay-shell appearance frames activate normal light CSS for System light');

    const topologyDelayApi = loadYolomux('', ['1']);
    topologyDelayApi.setShareReplayTopologyKeyframeQueuedAtForTest(Date.now());
    topologyDelayApi.setShareReplayHostLastKeyframeAtForTest(0);
    topologyDelayApi.setSharePointerLastPublishedAtForTest(-100);
    const pointerQuietDelay = topologyDelayApi.shareTopologyDomKeyframeDelayMsForTest();
    assert.ok(pointerQuietDelay >= 350 && pointerQuietDelay <= 500, `topology replay waits for pointer quiet instead of serializing during active cursor movement (${pointerQuietDelay})`);
    topologyDelayApi.setSharePointerLastPublishedAtForTest(-1000);
    topologyDelayApi.setShareReplayHostLastKeyframeAtForTest(Date.now() - 1000);
    const keyframeFloorDelay = topologyDelayApi.shareTopologyDomKeyframeDelayMsForTest();
    assert.ok(keyframeFloorDelay >= 3500 && keyframeFloorDelay <= 5000, `topology replay respects the host keyframe floor after a recent keyframe (${keyframeFloorDelay})`);
    topologyDelayApi.setShareReplayTopologyKeyframeQueuedAtForTest(Date.now() - 6000);
    topologyDelayApi.setShareReplayHostLastKeyframeAtForTest(Date.now());
    topologyDelayApi.setSharePointerLastPublishedAtForTest(0);
    assert.equal(topologyDelayApi.shareTopologyDomKeyframeDelayMsForTest(), 0, 'topology replay max deferral eventually permits a keyframe even under continuous pointer movement');

    const sharePointerSlots = api.emptyLayoutSlots();
    sharePointerSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    sharePointerSlots.left = api.paneStateWithTabs(['1'], '1');
    sharePointerSlots.slot1 = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(sharePointerSlots);
    api.setAppMirrorTransformForTest({scale: 1, tx: 0, ty: 0});
    const hostPointer = api.sharePointerPayloadForPoint(600, 40);
    assert.deepStrictEqual(canonical(hostPointer), {scope: 'viewport', x: 600, y: 40}, 'M7: host pointer publishes raw app-space viewport coordinates');
    api.setAppMirrorTransformForTest({scale: 0.5, tx: 20, ty: 30});
    const viewerPointer = api.sharePointerPayloadForPoint(320, 230, {click: true});
    assert.equal(viewerPointer.scope, 'viewport');
    assert.equal(viewerPointer.x, 600, 'M7: share-view pointer visual x maps back through the mirror transform before publish');
    assert.equal(viewerPointer.y, 400, 'M7: share-view pointer visual y maps back through the mirror transform before publish');
    assert.equal(viewerPointer.click, true);
    const viewerPoint = api.sharePointFromPointerPayload({scope: 'viewport', x: 600, y: 400});
    assert.equal(viewerPoint.x, 320, 'M7: received app-space pointer maps into the local visual mirror x');
    assert.equal(viewerPoint.y, 230, 'M7: received app-space pointer maps into the local visual mirror y');
    assert.equal(api.sharePointFromPointerPayload({scope: 'pane', x: 600, y: 400}), null, 'M7: old pane-relative pointer payloads are rejected');

    const pointerPublishApi = loadYolomux('', ['1']);
    pointerPublishApi.setActiveSharesForTest([{token: 'share-token'}]);
    const pointerFrames = [];
    pointerPublishApi.setShareHostSocketForTest('share-token', {
      readyState: 1,
      send(message) { pointerFrames.push(JSON.parse(message)); },
    });
    pointerPublishApi.sharePublishPointerEventForTest({clientX: 44, clientY: 55, isPrimary: true});
    assert.equal(pointerFrames.length, 1, 'DOM replay pointer publication sends one frame per host pointer tick');
    assert.equal(pointerFrames[0].type, 'pointer');
    assert.deepStrictEqual(canonical(pointerFrames[0].payload), {scope: 'viewport', visible: true, x: 44, y: 55});
    api.setAppMirrorTransformForTest({scale: 1, tx: 0, ty: 0});
    const digestA = {snapshot: {viewport: {width: 1, height: 2}, fonts: {ui: 12}, slots: [], tabStrips: [], terminalCells: [], editors: []}};
    const digestB = {snapshot: {editors: [], terminalCells: [], tabStrips: [], slots: [], fonts: {ui: 12}, viewport: {height: 2, width: 1}}};
    assert.equal(api.stableDigestJson(digestA.snapshot), api.stableDigestJson(digestB.snapshot), 'M9: digest JSON is stable across object key order');
    assert.equal(api.shareGeometryDigestValue(digestA.snapshot), api.shareGeometryDigestValue(digestB.snapshot), 'M9: digest hash is stable across object key order');
    assert.equal(api.shareGeometryFirstDifference(digestA, {snapshot: {...digestA.snapshot, fonts: {ui: 13}}}), 'fonts', 'M9: digest comparison names the first differing top-level component');
    const wrapApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64');
    const wrapRoot = wrapApi.appRootForTest();
    const wrapTextarea = new TestElement('yoagent-format', 'textarea');
    wrapTextarea.dataset.settingPath = 'yoagent.format';
    wrapTextarea.value = 'Reply in Markdown. Default shape: a short direct answer, then optional bullets for the top relevant topics.';
    wrapTextarea.textContent = wrapTextarea.value;
    wrapTextarea.clientWidth = 640;
    wrapTextarea.clientHeight = 96;
    wrapTextarea.scrollWidth = 642;
    wrapTextarea.scrollHeight = 132;
    wrapTextarea.rect = {left: 40, top: 100, right: 680, bottom: 196, width: 640, height: 96};
    wrapRoot.appendChild(wrapTextarea);
    const wrapDigest = wrapApi.shareWrappedTextDigestSnapshotForTest();
    assert.equal(wrapDigest.length, 1, 'M9: wrapped text digest includes Preferences textareas');
    assert.equal(wrapDigest[0].key, 'yoagent.format', 'M9: wrapped text digest keys controls by setting path');
    assert.equal(wrapDigest[0].scrollHeight, 132, 'M9: wrapped text digest records native control scrollHeight so line-wrap drift is detected');
    assert.equal(wrapApi.shareGeometryFirstDifference(
      {snapshot: {...digestA.snapshot, textWraps: [{key: 'yoagent.format', scrollHeight: 132}]}},
      {snapshot: {...digestA.snapshot, textWraps: [{key: 'yoagent.format', scrollHeight: 96}]}},
    ), 'textWraps', 'M9: wrapped text/control drift is named separately from generic geometry drift');
    const tmuxMenu = menus.find(menu => menu.id === 'tmux');
    const tmuxMenuLabels = tmuxMenu.items.map(item => item.label).filter(Boolean);
    const tabsMenu = menus.find(menu => menu.id === 'tabs');
    const tabsMenuLabels = tabsMenu.items.map(item => item.label).filter(Boolean);
    assert.equal(tmuxMenu.items[0].label, 'YO off');
    assert.equal(tmuxMenu.items[0].keepOpen, true);
    assert.equal(tmuxMenuLabels.includes('New tmux session'), false);
    // New-session items use an explicit "new" command label in Tabs; the detail shows the params passed.
    assert.equal(tmuxMenuLabels.includes('new Claude'), false);
    assert.equal(tmuxMenuLabels.includes('new Codex'), false);
    assert.equal(tmuxMenuLabels.includes('new Xterm'), false);
    assert.ok(tabsMenuLabels.includes('new Claude'));
    assert.ok(tabsMenuLabels.includes('new Codex'));
    assert.ok(tabsMenuLabels.includes('new Xterm'), 'Xterm is always offered (a plain shell), not greyed unavailable');
    assert.ok(tabsMenu.items.find(item => item.label === 'new Xterm').iconHtml.includes('app-menu-ui-icon-shell'), 'Tabs -> new Xterm uses the shell symbol');
    assert.equal(tmuxMenuLabels.includes('+ Claude'), false, 'the "+" prefix is dropped from new-session items');
    api.setAgentAuthForTest({claude: {installed: false, logged_in: false, unavailable_reason: 'not-on-path'}});
    const unavailableTabsMenu = api.appMenuTree().find(menu => menu.id === 'tabs');
    const missingPathClaude = unavailableTabsMenu.items.find(item => item.label === 'new Claude');
    assert.equal(missingPathClaude.disabled, true);
    assert.equal(missingPathClaude.detail, 'Not on server PATH');
    {
      const newSessionSrc = fs.readFileSync('static/yolomux.js', 'utf8');
      const menuCss = fs.readFileSync('static/yolomux.css', 'utf8');
      assert.ok(newSessionSrc.includes('function agentLaunchParams(agent)'), 'the launch-params helper exists');
      assert.ok(/menuCommand\(newTmuxSessionLabel\(agent\), \(\) => createNextSession\(agent\)/.test(newSessionSrc), 'new-session label uses the "new {name}" locale string, action launches it');
      assert.ok(/function newTmuxSessionIcon\(agent\)[\s\S]*agent === 'term' \? appMenuUiIcon\('shell'\) : agentIcon\(agent\)/.test(newSessionSrc), 'new Xterm uses the shared shell icon path while Claude/Codex keep agent icons');
      assert.ok(/function tabMenuItems\(openItems[\s\S]*menuGroups\(\s*newTmuxSessionItems\(\),[\s\S]*filteredOpenItems/.test(newSessionSrc), 'Tabs menu owns the new-session commands');
      assert.ok(menuCss.includes('.app-menu-ui-icon-shell') && menuCss.includes('--icon-shell'), 'shell menu icon CSS is generated');
      assert.ok(/capped \? t\('menu\.tmux\.limitReached'\) : agentLaunchParams\(agent\)/.test(newSessionSrc), 'a launchable new-session item shows the params passed as its detail');
    }
    assert.ok(tmuxMenuLabels.includes("Transcript for session '1'"));
    assert.ok(tmuxMenuLabels.includes("YO!summary for session '1'"));
    assert.ok(tmuxMenuLabels.includes("Event log for session '1'"));
    assert.ok(tmuxMenuLabels.includes('Info Bar'));
    assert.ok(tmuxMenuLabels.includes("Rename tmux session '1'"));
    assert.ok(tmuxMenuLabels.includes("Kill tmux session '1'"));
    assert.equal(tmuxMenuLabels.includes("Enable YOLO for Tmux Session '1'"), false);
    assert.ok(tmuxMenuLabels.includes('Resume session'));
    assert.equal(tmuxMenu.badgeText, undefined);
    assert.ok(tmuxMenuLabels.includes('YOLO'));
    assert.ok(tmuxMenuLabels.indexOf('YOLO') > tmuxMenuLabels.indexOf('Resume session'), 'YOLO submenu stays at the bottom after session actions');
    const yoloMenu = tmuxMenu.items.find(item => item.label === 'YOLO');
    assert.equal(yoloMenu.type, 'submenu');
    const yoloPulseItem = yoloMenu.items.find(item => item.path === 'appearance.red_reminder_ms');
    assert.equal(yoloPulseItem.type, 'number-setting');
    assert.equal(yoloPulseItem.label, 'Red/yellow/green status pulse period');
    assert.equal(yoloPulseItem.suffix, 'ms');
    assert.equal(yoloMenu.items.some(item => item.path === 'appearance.yolo_rotate_ms'), false);
    assert.ok(yoloMenu.items.some(item => item.label === 'Open rule file'));
    assert.ok(yoloMenu.items.some(item => item.label === 'Reload rules'));
    assert.equal(yoloMenu.items.some(item => item.label === 'Sessions'), false);
    assert.equal(tmuxMenu.items.find(item => item.label === "Rename tmux session '1'").disabled, false);
    assert.equal(tmuxMenu.items.find(item => item.label === "Rename tmux session '1'").detail, '');
    api.setAutoApproveStateForTest('1', {enabled: true});
    const idleYoloTabsMenu = api.appMenuTree().find(menu => menu.id === 'tabs');
    assert.equal(idleYoloTabsMenu.badgeText, '0');
    assert.equal(idleYoloTabsMenu.badgeTitle, '0 running YOLO jobs');
    api.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'working'}});
    const yoloTmuxMenu = api.appMenuTree().find(menu => menu.id === 'tmux');
    assert.equal(yoloTmuxMenu.badgeText, undefined);
    const yoloTabsMenu = api.appMenuTree().find(menu => menu.id === 'tabs');
    assert.equal(yoloTabsMenu.badgeText, '1');
    assert.equal(yoloTabsMenu.badgeTitle, '1 running YOLO job');
    assert.equal(yoloTmuxMenu.items[0].label, 'YO on');
    assert.equal(yoloTmuxMenu.items[0].keepOpen, true);
    assert.equal(yoloTmuxMenu.items[0].iconHtml.includes('session-yolo-marker'), true);
    assert.equal(yoloTmuxMenu.items.find(item => item.label === 'YOLO').items.some(item => item.label.startsWith('Sessions')), false);
    api.setAutoApproveStateForTest('1', {enabled: false});
    assert.equal(api.currentSessionActionTarget(), '1');
    const filesOnlySlots = api.emptyLayoutSlots();
    filesOnlySlots[api.layoutTreeKey] = api.leafNode('left');
    filesOnlySlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    api.setLayoutSlotsForTest(filesOnlySlots);
    const filesOnlyTmuxMenu = api.appMenuTree().find(menu => menu.id === 'tmux');
    assert.equal(filesOnlyTmuxMenu.items.find(item => item.label === 'Rename tmux session').disabled, true);
    assert.equal(filesOnlyTmuxMenu.items.find(item => item.label === 'Rename tmux session').detail, 'No tmux tab focused');
    const multiTmuxSlots = api.emptyLayoutSlots();
    multiTmuxSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50), 22);
    multiTmuxSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    multiTmuxSlots.slot1 = api.paneStateWithTabs(['1'], '1');
    multiTmuxSlots.slot2 = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(multiTmuxSlots);
    api.setFocusedPanelItem('2');
    api.setFocusedPanelItem('__files__');
    assert.equal(api.currentSessionActionTarget(), '2');
    const source = fs.readFileSync('static/yolomux.js', 'utf8');
    const css = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(source.includes("menuNumberSetting('appearance.red_reminder_ms'"), 'YOLO submenu exposes the renamed status pulse setting');
    assert.ok(/function createAppMenuNumberSetting[\s\S]*saveSettingsPatch\(settingPatch\(item\.path, next\)\)/.test(source), 'menu number-setting rows save through the shared settings API');
    assert.ok(/function applyAppMenuNumberSettingPreview[\s\S]*path === 'appearance\.red_reminder_ms'[\s\S]*applyCssSettings\(\)/.test(source), 'YOLO menu status pulse control live-applies the shared pulse timing');
    assert.ok(css.includes('.app-menu-setting-control input[type="number"]'), 'menu number-setting rows have dedicated input styling');
    assert.ok(source.startsWith('/* GENERATED by tools/static_build.py from static_src/'), 'generated JS has a do-not-edit header');
    assert.ok(source.includes('const mod = appModifier(event);'), 'global app shortcuts use one platform modifier');
    assert.equal(source.includes('const mod = event.ctrlKey || event.metaKey;'), false, 'global app shortcuts do not claim both Ctrl and Cmd');
    assert.ok(source.includes('function globalShortcutTargetAllowsPlatformAction(target)'), 'platform app shortcuts use a shared focus guard');
    assert.ok(source.includes("return isMacPlatform() || globalShortcutTargetAllowsAppAction(target);"), 'Mac app shortcuts bypass terminal focus so Cmd+P cannot fall through to browser Print');
    assert.ok(source.includes("if (mod && key === 'p' && platformActionAllowed)"), 'file quick-open is bound through the platform shortcut guard');
    assert.ok(source.includes('if (event.shiftKey) openCommandPalette();'), 'Shift plus app modifier opens the command palette');
    assert.ok(source.includes('else openFileQuickOpen();'), 'Plain app modifier plus P opens file quick-open');
    assert.ok(/platformMod && event\.altKey && event\.code === 'KeyB'[\s\S]*openYoagentRightPane\(\)/.test(source), 'Cmd/Ctrl+Alt+B opens YO!agent in the right pane');
    assert.ok(source.includes("if (event.key === ',')"), 'Preferences keeps best-effort comma shortcut in browser tabs');
    assert.ok(source.includes('selectSession(prefsItemId);'), 'Preferences shortcut opens the pane, while menu and palette remain fallbacks');
    assert.ok(source.includes('function startPinTabShortcutChord()'), 'Pin Tab shortcut uses an explicit chord starter');
    assert.ok(source.includes("pendingGlobalShortcutChord = 'pin-tab'"), 'Pin Tab shortcut records the pending chord');
    assert.ok(source.includes("if (pendingGlobalShortcutChord === 'pin-tab' && key === 'enter')"), 'Pin Tab shortcut completes on Enter');
    assert.ok(source.includes('toggleActiveTabPinned();'), 'Pin Tab shortcut toggles the active tab');
    assert.ok(source.includes("window.addEventListener('keydown', handleGlobalShortcutKeydown, true)"), 'global shortcuts run in capture phase before focused controls swallow them');
    assert.ok(source.includes("if (mod && key === 'w')"), 'Cmd/Ctrl+W is captured before browser/app defaults');
    assert.ok(source.includes('function itemCanCloseWithAppShortcut(item)'), 'global close shortcut is scoped through a shared item guard');
    assert.ok(source.includes('if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);'), 'Cmd/Ctrl+W cannot close Preferences, YO!agent, Finder, Changes, or tmux tabs');
    assert.ok(source.includes("(key === 'backspace' || key === 'delete') && globalShortcutTargetAllowsAppAction(event.target)"), 'Backspace/Delete close fallback stays out of text editing contexts');
    assert.equal(source.includes('Ctrl/Cmd'), false, 'served UI strings do not show Ctrl/Cmd combined shortcuts');
    assert.ok(source.includes('showFileSaveConflictDialog'), 'editor saves route conflicts through the shared conflict dialog');
    assert.ok(source.includes('autoSaveFileEditor'), 'editor autosave is wired into the built client');
    assert.ok(source.includes('promptExternalChangeBeforeEditing'), 'editing a changed-on-disk buffer prompts before continuing');
    // A non-dirty editor reloads disk changes silently (the prompt is only for the genuine unsaved-edits conflict).
    assert.ok(/function promptExternalChangeBeforeEditing[\s\S]*?if \(!state\.dirty\) \{[\s\S]*?reloadOpenFileFromDisk\(path, \{force: true\}\)/.test(source), 'a non-dirty editor reloads external disk changes silently (no dialog)');
    const splitButtonIndex = source.indexOf("dataset: {editorMode: 'split'}");
    const popoutPreviewButtonIndex = source.indexOf("className: 'file-editor-popout-preview-panel'");
    const modeSeparatorIndex = source.indexOf("dataset: {editorToolbarSeparator: 'mode'}");
    assert.ok(splitButtonIndex > 0 && popoutPreviewButtonIndex > splitButtonIndex && popoutPreviewButtonIndex < modeSeparatorIndex, 'Preview pop-out sits directly in the editor mode button group after Split view');
    assert.ok(source.includes('editor.autosave_delay_seconds'), 'editor autosave delay is a persisted preference');
    assert.ok(source.includes('(commandPaletteIndex + 1) % commandPaletteItemsCache.length'), 'command palette arrow navigation wraps down');
    assert.ok(source.includes('item.splitRun'), 'command palette supports split-open actions');
    assert.equal(source.includes('function updateLinkedFilePreviewRings()'), false, 'old side-preview ring updater is removed');
    assert.equal(source.includes("previewPanel.classList.add('preview-linked')"), false, 'focused editors no longer mark paired side-preview panes');
    assert.equal(source.includes("editorPanel.classList.add('preview-linked')"), false, 'old pure-preview pane ring marker is removed');
    const focusStart = source.indexOf('function setFocusedPanelItem(');
    const focusEnd = source.indexOf('function clearPendingFileEditorFocusExcept(', focusStart);
    assert.ok(source.slice(focusStart, focusEnd).includes('const explicitFinderSync = isTmuxSession(item) || isFileEditorItem(item);'), 'explicit Finder sync is driven by clicked tmux panes and clicked editors');
    assert.ok(source.slice(focusStart, focusEnd).includes('if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item, {explicit: explicitFinderSync});'));
    const finderSyncStart = source.indexOf('function scheduleFileExplorerActiveTabSync(');
    const finderSyncEnd = source.indexOf('function cancelPendingFileExplorerActiveSync(', finderSyncStart);
    const finderSyncBody = source.slice(finderSyncStart, finderSyncEnd);
    assert.ok(finderSyncBody.includes("if (fileExplorerRootMode !== 'sync') return;"), 'Finder ignores terminal/editor clicks when Sync is not pressed');
    assert.ok(finderSyncBody.includes('const fileSyncPath = explicit && isFileEditorItem(preferredItem) ? fileItemPath(preferredItem) : \'\';'), 'explicit editor clicks become Finder Sync file targets');
    assert.ok(/const syncItem = fileSyncPath \? preferredItem : \(isTmuxSession\(preferredItem\) \? preferredItem : explicitSession\);[\s\S]*if \(!syncItem \|\| \(!fileSyncPath && syncItem !== explicitSession\)\) return/.test(finderSyncBody), 'Finder Sync accepts clicked editor files without requiring a tmux target');
    assert.ok(finderSyncBody.includes('fileExplorerSyncPlanForFile(fileSyncPath)'), 'Finder Sync can plan from an explicit editor file');
    assert.ok(finderSyncBody.includes('syncFileExplorerRootToActiveFile(fileSyncPath, {force: explicit})'), 'Finder Sync can move the root to a clicked editor file and explicit sync forces a re-apply');
    assert.ok(/openFileExplorerAt\(plan\.root, \{[\s\S]*preserveExpanded: false,[\s\S]*preserveScroll: false,[\s\S]*syncSelection: true,[\s\S]*user: options\.force === true,[\s\S]*showPending: options\.force === true,/.test(source), 'sync-driven Finder root opens do not cancel newer pending explicit pane syncs and explicit opens are user-owned');
    assert.ok(finderSyncBody.includes('(explicit || !fileExplorerSyncPlanAlreadyApplied(syncPlan))'), '#automatic Finder Sync skips a repeated already-applied plan');
    const openFileExplorerAtStart = source.indexOf('async function openFileExplorerAt(');
    const openFileExplorerAtEnd = source.indexOf('function resetFileExplorerAppliedSyncPlan(', openFileExplorerAtStart);
    assert.ok(source.includes('let fileExplorerOpenGeneration = 0;'), 'Finder root opens have a dedicated generation for async race cancellation');
    assert.ok(/async function openFileExplorerAt\([\s\S]*const openGeneration = \+\+fileExplorerOpenGeneration;[\s\S]*const openStillCurrent = \(\) => openGeneration === fileExplorerOpenGeneration;[\s\S]*const entries = await fetchDirectory\(root,[\s\S]*if \(!openStillCurrent\(\)\) return false;/.test(source), 'stale Finder root fetches are dropped before they can render');
    assert.ok(source.slice(openFileExplorerAtStart, openFileExplorerAtEnd).includes("if (options.syncSelection !== true) cancelPendingFileExplorerActiveSync({invalidateOpen: false});"), 'completed manual Finder opens cancel pending sync without invalidating their own open generation');
    assert.ok(/const showPendingRoot = options\.manualSelection === true \|\| options\.showPending === true;[\s\S]*if \(showPendingRoot\) \{[\s\S]*fileExplorerManualSelectionActive = options\.manualSelection === true;[\s\S]*renderFileExplorerTreeSearching\(root\);/.test(source), 'manual and explicit Sync opens claim the Finder UI with a searching row before listing resolves');
    assert.ok(/openFileExplorerAt\(plan\.root, \{[\s\S]*syncSelection: true,[\s\S]*user: options\.force === true,[\s\S]*showPending: options\.force === true,/.test(source), 'explicit Finder Sync opens bypass live push background suppression and show pending state');
    assert.ok(/async function openFileExplorerAt\([\s\S]*if \(options\.manualSelection === true\) \{[\s\S]*cancelPendingFileExplorerActiveSync\(\);[\s\S]*const showPendingRoot = options\.manualSelection === true \|\| options\.showPending === true;[\s\S]*fileExplorerManualSelectionActive = options\.manualSelection === true;[\s\S]*setFileExplorerPathDisplay\(root\);[\s\S]*renderFileExplorerRootModeControls\(\);[\s\S]*renderFileExplorerTreeSearching\(root\);[\s\S]*const entries = await fetchDirectory\(root,/.test(source), 'typed/manual Finder opens disable Sync, clears stale rows, and shows searching before slow directory listing resolves');
    assert.ok(source.includes("textWithMovingEllipsisHtml('searching...', 'file-tree-searching-dots')"), 'Finder searching row reuses the shared moving ellipsis helper');
    assert.ok(/\.file-tree-status-row\s*\{[\s\S]*cursor:\s*default/.test(css), 'Finder searching row is styled as passive status, not an interactive path');
    assert.equal(finderSyncBody.includes('syncFileExplorerToActiveTab(preferredItem'), false, 'fixed Finder mode never follows explicit tmux/editor clicks');
    assert.ok(source.includes('function fileExplorerSyncPlanKey(plan)'), '#Finder Sync has one shared sync-plan key helper');
    assert.ok(source.includes('function fileExplorerSyncPlanAlreadyApplied(plan)'), '#Finder Sync has one shared already-applied helper');
    assert.ok(source.includes('function markFileExplorerSyncPlanApplied(plan)'), '#Finder Sync marks successful plan application');
    const finderCandidatesStart = source.indexOf('function finderCandidateItems(');
    const finderCandidatesEnd = source.indexOf('function firstFinderPath(', finderCandidatesStart);
    const finderCandidatesBody = source.slice(finderCandidatesStart, finderCandidatesEnd);
    assert.equal(finderCandidatesBody.includes('focusedPanelItem'), false, 'Finder candidates do not read passive focusedPanelItem');
    assert.equal(finderCandidatesBody.includes('focusedTerminal'), false, 'Finder candidates do not read passive focusedTerminal');
    assert.equal(finderCandidatesBody.includes('lastFocusedTmuxSession'), false, 'Finder candidates do not read passive lastFocusedTmuxSession');
    assert.ok(/function setFileExplorerManualRootMode\(\) \{[\s\S]*cancelPendingFileExplorerActiveSync\(\);[\s\S]*setFileExplorerRootMode\('fixed', \{sync: false, persist: true\}\)/.test(source), 'manual Finder scope buttons cancel pending Sync and leave Sync mode explicitly');
    assert.ok(/function cancelPendingFileExplorerActiveSync\(options = \{\}\) \{[\s\S]*fileExplorerInteractionGeneration \+= 1;[\s\S]*if \(options\.invalidateOpen !== false\) fileExplorerOpenGeneration \+= 1;[\s\S]*fileExplorerSyncGeneration \+= 1;/.test(source), 'manual Finder actions invalidate stale root opens and stale tree expansion work');
    assert.ok(source.includes('openFileExplorerManualRoot(expandQuickAccessPath(path));'), 'Finder quick-access opens are manual and disable Sync');
    assert.ok(source.includes('const opened = await openFileExplorerManualRoot(target);'), 'Finder typed path opens are manual and disable Sync');
    assert.ok(source.includes("if (entry.kind === 'dir') openFileExplorerManualRoot(fullPath);"), 'Finder root navigation by double-click is manual and disables Sync');
    assert.ok(source.includes('fileExplorerSyncManualCollapsedPaths'), 'Finder Sync tracks manually collapsed auto-expanded paths');
    assert.ok(/function fileExplorerSyncExpansionPaths\(plan\)[\s\S]*filter\(path => !fileExplorerSyncPathSuppressed\(path\)\)/.test(source), 'Finder Sync filters manually collapsed paths out of future auto-expansion');
    assert.ok(source.includes('function fileExplorerSyncExpansionTargets(root, affectedDirs = [], repoRoots = [])'), 'Finder Sync expansion targets are centralized');
    assert.ok(source.includes('const candidates = normalizedRepoRoots.length'), 'Finder Sync prefers repo-root expansion when repo metadata exists');
    assert.ok(source.includes('affectedDirs.map(path => firstChildPathUnderRoot(normalizedRoot, path))'), 'Finder Sync falls back to first-level affected directories, not every touched directory');
    const viewMenu = menus.find(menu => menu.id === 'view');
    assert.equal(viewMenu.items.find(item => item.label === 'Hide tab metadata').iconHtml.includes('app-menu-ui-icon-tab-meta active'), true);
    assert.equal(viewMenu.items.find(item => item.label === 'Hide tab metadata').keepOpen, true);
    assert.equal(viewMenu.items.find(item => item.label === 'Alert').iconHtml.includes('app-menu-ui-icon-notify'), true);
    assert.equal(viewMenu.items.find(item => item.label === 'Alert').keepOpen, true);
    assert.equal(viewMenu.items.find(item => item.label === 'Refresh').iconHtml.includes('app-menu-ui-icon-refresh'), true);
    assert.equal(viewMenu.items.find(item => item.label === 'Refresh').keepOpen, undefined);
    assert.equal(viewMenu.items.find(item => item.label === 'Refresh').detail, undefined);
    const helpMenu = menus.find(menu => menu.id === 'help');
    const helpMenuLabels = helpMenu.items.map(item => item.label).filter(Boolean);
    assert.ok(helpMenuLabels.includes('Keyboard shortcuts'));
    assert.ok(helpMenuLabels.includes('Open README'));
    const shortcutsMenu = helpMenu.items.find(item => item.label === 'Keyboard shortcuts');
    assert.equal(shortcutsMenu.type, 'command');
    assert.equal(shortcutsMenu.detail, '?');
    assert.ok(source.includes('function keyboardShortcutCatalog()'), 'shortcut help is driven from one catalog');
    assert.ok(api.keyboardShortcutsHtml().includes('Pin / unpin active tab'), 'shortcut overlay lists the Pin Tab chord');
    assert.ok(api.keyboardShortcutsHtml().includes('Ctrl+K Enter'), 'shortcut overlay renders the platform Pin Tab chord on PC/Linux');
    assert.ok(api.keyboardShortcutsHtml().includes('Previous / next tab') && api.keyboardShortcutsHtml().includes('Meta+← / Meta+→'), 'shortcut overlay documents Meta+Arrow pane-tab navigation');
    assert.ok(api.keyboardShortcutsHtml().includes('Drag a tab'), 'shortcut overlay is honest that tab/pane layout changes are pointer-driven');
    assert.equal(/Move or split tab[\s\S]{0,120}(Ctrl|Cmd|Alt|Shift)\+/.test(api.keyboardShortcutsHtml()), false, 'shortcut overlay does not advertise keyboard tab/pane movement');
    assert.ok(api.keyboardShortcutsHtml().includes('outside text'), 'shortcut overlay scopes the Backspace close-tab fallback');
    assert.ok(/async function copyTextToClipboard\(text\)[\s\S]*?if \(globalThis\.isSecureContext !== false && clipboard\?\.writeText\) \{[\s\S]*?try \{[\s\S]*?await clipboard\.writeText\(value\);[\s\S]*?\} catch/.test(source), 'clipboard copy falls back when navigator.clipboard exists but rejects');
    assert.ok(source.includes('function copyTerminalSelectionToClipboardEvent(session, term, event, container = null)'), 'terminal copy has a DOM copy-event fallback');
    assert.ok(source.includes('function handleTerminalTmuxWindowShortcutKeydown(session, event)'), 'terminal Meta+Arrow navigation shares the terminal shortcut guard');
    assert.ok(/function terminalTmuxWindowShortcutItem\(session\)[\s\S]*?const activeItem = visualActivePaneItem\(\);[\s\S]*?return activeItem \|\| session/.test(source), 'terminal Meta+Arrow starts from the active pane tab even when a stale terminal handler still owns the key event');
    assert.ok(source.includes("grid?.querySelectorAll?.('.dv-groupview')"), 'Meta+Arrow traversal can read rendered Dockview group order');
    assert.ok(source.includes("record.group.querySelectorAll?.('.dockview-pane-tab')"), 'Meta+Arrow traversal can read rendered Dockview tab strip order');
    assert.ok(/function paneTabTraversalPositions\(slots = layoutSlots\)[\s\S]*const rendered = renderedPaneTabTraversalPositions\(slots\);[\s\S]*return rendered\.length \? rendered : layoutPaneTabTraversalPositions\(slots\);/.test(source), 'Meta+Arrow traversal follows rendered Dockview order and falls back to serialized layout order');
    assert.ok(/function selectAdjacentPaneTab\(direction, options = \{\}\)[\s\S]*adjacentPaneTabPosition\(direction, options\)[\s\S]*activatePaneTab\(target\.slot, target\.item, activationOptions\)/.test(source), 'terminal/global Meta+Arrow navigation shares the pane-tab activation parent');
    assert.ok(/const paneTabShortcutDirection = terminalTmuxWindowShortcutDirection\(event\);[\s\S]*if \(paneTabShortcutDirection && globalShortcutTargetAllowsAppAction\(event\.target\)\) \{[\s\S]*selectAdjacentPaneTab\(paneTabShortcutDirection, \{userInitiated: true\}\)/.test(source), 'global Meta+Arrow handler routes Finder/Differ/Tabber focus through the same pane-tab selector');
    assert.equal(source.includes('function selectAdjacentTmuxSession('), false, 'old tmux-session-only Meta+Arrow shortcut path is removed');
    assert.ok(source.includes("container.addEventListener('copy', event => {"), 'terminal container handles browser copy events');
    assert.ok(source.includes("event.clipboardData.setData('text/plain', selected);"), 'terminal copy-event fallback writes the xterm selection to clipboardData');
    assert.ok(source.includes('function copyTextToClipboardViaCopyEvent(text)'), 'terminal shortcut copy has a synchronous copy-event clipboard path');
    // the sync-then-async clipboard chain lives in ONE shared parent (writeTerminalTextToClipboard)
    // used by both the shortcut copy and the OSC 52 bridge.
    assert.ok(/const TERMINAL_COPY_ACTIONS = Object\.freeze\(\{[\s\S]*selected:[\s\S]*selectedDedent:[\s\S]*tmux:[\s\S]*osc52:/.test(source), 'terminal copy menu/status/cleanup choices are described by one action table');
    assert.ok(/function writeTerminalTextToClipboard\(text, options = \{\}\)[\s\S]*?terminalCopyStatusText\(action[\s\S]*?copyTextToClipboardViaCopyEvent\(text\)[\s\S]*?copyTextToClipboard\(text\)/.test(source), 'terminal clipboard writes use the synchronous copy-event path before async clipboard fallback (shared parent)');
    assert.ok(/function copyTerminalSelectionFromShortcut\(session, term, options = \{\}, container = null\)[\s\S]*?writeTerminalTextToClipboard\(text, \{[\s\S]*?afterCopy: \(\) => clearTerminalVisibleSelection\(session, term, container, action\.reason\)/.test(source), 'terminal shortcut copy routes through the shared clipboard-write chain and visible-selection cleanup');
    assert.ok(source.includes('async function copyTmuxSelectionToClipboard(session, term = null, container = null)'), 'terminal tmux copy-mode selection can bridge to the browser clipboard and clear visible terminal selection');
    assert.ok(source.includes("apiFetchJson(`/api/tmux-copy-selection?session=${encodeURIComponent(session)}`, {method: 'POST'})"), 'tmux copy bridge calls the authenticated tmux-copy endpoint');
    assert.ok(source.includes("new ClipboardItem({'text/plain': textBlob})"), 'tmux copy bridge starts deferred clipboard writes during the shortcut activation');
    assert.ok(/function terminalSelectedText\(term, container = null\)[\s\S]*browserSelectionTextInside\(container\)/.test(source), 'terminal copy shortcuts prefer visible browser selection before tmux copy-mode fallback');
    assert.ok(source.includes("container?.addEventListener?.('keydown'"), 'terminal copy guard runs in DOM capture before xterm/TUI handlers');
    assert.ok(/function handleFocusedTerminalCopyShortcut\(event\)[\s\S]*handleTerminalCopyShortcutKeydown\(session, item\.term, item\.container, event\)[\s\S]*stopImmediatePropagation/.test(source), 'focused-terminal copy guard runs at window capture before terminal internals');
    assert.ok(/function handleGlobalShortcutKeydown\(event\) \{[\s\S]*?if \(handleFocusedTerminalCopyShortcut\(event\)\) return/.test(source), 'global shortcuts first give focused terminal copy handling a chance');
    assert.ok(source.includes('const isTmuxCopyShortcut = event.altKey'), 'tmux copy-mode bridge is on a separate terminal shortcut');
    assert.ok(source.includes('appendContextMenuButton(menu, terminalCopyActionLabel(TERMINAL_COPY_ACTIONS.tmux), () => copyTmuxSelectionToClipboard(session, term, container), closeTerminalContextMenu)'), 'terminal context menu exposes explicit tmux copy through the shared action descriptor');
    assert.ok(/function withTerminalVisibleSelectionCleanup\(session, term, container, reason, handler\)[\s\S]*clearTerminalVisibleSelection\(session, term, container, reason\)/.test(source), 'terminal menu actions share one cleanup wrapper after consuming selected text');
    assert.ok(/function copyTerminalSelectionToClipboardEvent\(session, term, event, container = null\)[\s\S]*event\.clipboardData\.setData\('text\/plain', selected\)[\s\S]*clearTerminalVisibleSelection\(session, term, container, TERMINAL_COPY_ACTIONS\.selected\.reason\)/.test(source), 'terminal DOM copy-event path also clears visible terminal selection after capturing clipboard text');
    assert.ok(/function appendUrlContextMenuItems\(menu, href, closeMenu, options = \{\}\)[\s\S]*consumeTerminalSelection\(options\.session, options\.term, options\.container, reason, handler\)[\s\S]*appendContextMenuButton\(menu, t\('contextmenu\.openUrl'\)[\s\S]*appendContextMenuButton\(menu, t\('contextmenu\.copyUrl'\)/.test(source), 'terminal URL menu open/copy actions route through visible-selection cleanup');
    assert.ok(/if \(!selected\) \{[\s\S]*?if \(isCmdC\) \{[\s\S]*?event\.preventDefault\(\);[\s\S]*?statusEl\.textContent = isMacPlatform\(\)[\s\S]*?t\('terminal\.copyHintMac'\)[\s\S]*?t\('terminal\.copyHintPc'\)[\s\S]*?return true;[\s\S]*?return false; \/\/ no selection: let Ctrl-C through as SIGINT/.test(source), 'Cmd-C without browser selection is swallowed with localized select/copy hints while Ctrl-C still falls through as SIGINT');
    assert.equal(source.includes("'Copy without indent'"), false, 'terminal copy menu labels are locale keys, not raw strings in the bundle');
    assert.equal(source.includes('copied ${text.length} chars'), false, 'terminal copied-count status text is locale/plural-key driven');
    assert.equal(source.includes('else copyTmuxSelectionToClipboard(session);'), false, 'Cmd-C no longer falls back to tmux copy-mode');
    assert.ok(/function appendContextMenuButton\(menu, label, handler, closeMenu, options = \{\}\)[\s\S]*\['control-active-hover', options\.className \|\| ''\]/.test(source), 'CSS-3: terminal context menu rows use the shared active hover/focus class');
    assert.ok(api.keyboardShortcutsHtml().includes('Copy tmux selection'), 'keyboard shortcuts list includes the explicit tmux copy shortcut');
    const cleanupContainer = new TestElement('terminal-cleanup-container');
    const cleanupAnchor = new TestElement('terminal-cleanup-anchor');
    cleanupContainer.appendChild(cleanupAnchor);
    let contextMenuClearCount = 0;
    api.setBrowserSelectionForTest('browser selected text', cleanupAnchor);
    api.rememberTerminalAppClipboardTextForTest('1', 'osc52 selected text');
    const cleanupResult = api.clearTerminalVisibleSelectionForTest('1', {
      getSelection: () => 'xterm selected text',
      clearSelection() { contextMenuClearCount += 1; },
    }, cleanupContainer, 'unit-test');
    assert.equal(cleanupResult.before.xtermChars, 'xterm selected text'.length, 'visible-selection classifier records xterm selection length');
    assert.equal(cleanupResult.before.browserChars, 'browser selected text'.length, 'visible-selection classifier records browser selection length inside the terminal container');
    assert.equal(cleanupResult.before.recentOsc52Chars, 'osc52 selected text'.length, 'visible-selection classifier records recent OSC 52 fallback length');
    assert.equal(cleanupResult.browserCleared, true, 'terminal selection cleanup clears browser selection only when it touches the terminal container');
    assert.equal(contextMenuClearCount, 1, 'terminal selection cleanup clears xterm selection');
    assert.equal(api.browserSelectionClearCountForTest(), 1, 'browser removeAllRanges is called exactly once for terminal-owned selection');
    api.clearBrowserSelectionForTest();
    const terminalContextMenuNode = () => api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('terminal-context-menu'));
    const urlReference = {type: 'url', href: 'https://github.com/ai-dynamo/dynamo/pull/172'};
    api.showTerminalContextMenuForTest('1', {getSelection: () => ''}, 10, 10, null, urlReference.href, urlReference);
    let terminalUrlMenu = terminalContextMenuNode();
    let terminalUrlLabels = Array.from(terminalUrlMenu.children).map(child => child.textContent).filter(Boolean);
    assert.deepStrictEqual(canonical(terminalUrlLabels), ['Open URL in a new tab', 'Copy URL', 'Copy tmux selection', 'Copy without indent'], 'terminal URL menu puts the link action first and drops the redundant generic Copy row when the selection already equals the href');
    assert.equal(terminalUrlLabels.includes('Copy'), false, 'terminal URL menu does not show the ambiguous generic Copy row when it would duplicate Copy URL');
    api.showTerminalContextMenuForTest('1', {getSelection: () => ''}, 10, 10, null, 'YOLOmux PR 172', urlReference);
    terminalUrlMenu = terminalContextMenuNode();
    terminalUrlLabels = Array.from(terminalUrlMenu.children).map(child => child.textContent).filter(Boolean);
    assert.deepStrictEqual(canonical(terminalUrlLabels), ['Open URL in a new tab', 'Copy URL', 'Copy selected text', 'Copy tmux selection', 'Copy without indent'], 'terminal URL menu labels the selected-text copy path explicitly when the visible text differs from the href');
    assert.ok(/function appendUrlContextMenuItems\(menu, href, closeMenu, options = \{\}\)[\s\S]*appendContextMenuButton\(menu, t\('contextmenu.openUrl'\)[\s\S]*appendContextMenuButton\(menu, t\('contextmenu.copyUrl'\)[\s\S]*options\.includeSelectedText && selectedText && selectedText !== url[\s\S]*appendContextMenuButton\(menu, t\('contextmenu.copySelectedText'\)/.test(source), 'link menus share one helper that orders Open URL before copy actions and only shows Copy selected text when the selected text differs from the href');
    const terminalCopyApi = loadYolomux('?platform=mac', ['1'], 'https:', 'MacIntel');
    const fetchCalls = [];
    terminalCopyApi.setFetchForTest((url, options = {}) => {
      fetchCalls.push({url: String(url), method: options.method || 'GET'});
      return new Promise(() => {});
    });
    let terminalSelection = '';
    let copyShortcutHandler = null;
    let clearSelectionCount = 0;
    terminalCopyApi.installTerminalCopyShortcutForTest('1', {
      getSelection: () => terminalSelection,
      clearSelection: () => { clearSelectionCount += 1; },
      attachCustomKeyEventHandler(handler) { copyShortcutHandler = handler; },
    });
    assert.equal(typeof copyShortcutHandler, 'function', 'terminal copy shortcut installs an xterm custom key handler');
    let prevented = 0;
    const cmdCResult = copyShortcutHandler({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(cmdCResult, false, 'Mac Cmd-C with no browser/xterm selection is swallowed instead of reaching the PTY');
    assert.equal(prevented, 1, 'Mac Cmd-C with no browser/xterm selection prevents the terminal default');
    assert.deepStrictEqual(fetchCalls, [], 'Mac Cmd-C with no browser/xterm selection does not ask tmux for copy-mode text');
    let domKeydownHandler = null;
    let domKeydownOptions = null;
    let domCopyShortcutHandler = null;
    terminalCopyApi.installTerminalCopyShortcutForTest('1', {
      getSelection: () => '',
      clearSelection: () => { clearSelectionCount += 1; },
      attachCustomKeyEventHandler(handler) { domCopyShortcutHandler = handler; },
    }, {
      addEventListener(type, handler, options) {
        if (type === 'keydown') {
          domKeydownHandler = handler;
          domKeydownOptions = options;
        }
      },
    });
    assert.equal(typeof domCopyShortcutHandler, 'function', 'terminal DOM guard install keeps xterm handler available');
    assert.equal(domKeydownOptions?.capture, true, 'terminal DOM copy guard is installed in capture phase');
    fetchCalls.length = 0;
    prevented = 0;
    let stopped = 0;
    let stoppedImmediate = 0;
    domKeydownHandler({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      preventDefault() { prevented += 1; },
      stopPropagation() { stopped += 1; },
      stopImmediatePropagation() { stoppedImmediate += 1; },
    });
    assert.equal(prevented, 1, 'terminal DOM copy guard prevents Cmd-C with no browser/xterm selection');
    assert.equal(stopped, 1, 'terminal DOM copy guard stops propagation before Claude/xterm');
    assert.equal(stoppedImmediate, 1, 'terminal DOM copy guard stops sibling handlers before Claude/xterm');
    assert.deepStrictEqual(fetchCalls, [], 'terminal DOM copy guard does not ask tmux for normal Cmd-C');
    terminalCopyApi.registerTerminalForTest('1', {
      getSelection: () => '',
      clearSelection: () => { clearSelectionCount += 1; },
    });
    terminalCopyApi.setFocusedTerminal('1');
    fetchCalls.length = 0;
    prevented = 0;
    stopped = 0;
    stoppedImmediate = 0;
    const focusedGuardResult = terminalCopyApi.handleFocusedTerminalCopyShortcutForTest({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      preventDefault() { prevented += 1; },
      stopPropagation() { stopped += 1; },
      stopImmediatePropagation() { stoppedImmediate += 1; },
    });
    assert.equal(focusedGuardResult, true, 'focused terminal copy guard claims Cmd-C before Claude/xterm');
    assert.equal(prevented, 1, 'focused terminal window guard prevents Cmd-C before Claude/xterm');
    assert.equal(stopped, 1, 'focused terminal window guard stops propagation before target handlers');
    assert.equal(stoppedImmediate, 1, 'focused terminal window guard stops sibling window handlers');
    assert.deepStrictEqual(fetchCalls, [], 'focused terminal window guard does not ask tmux for normal Cmd-C');
    prevented = 0;
    const tmuxCopyShortcutResult = copyShortcutHandler({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: true,
      ctrlKey: false,
      altKey: true,
      shiftKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(tmuxCopyShortcutResult, false, 'Mac Cmd-Option-C is handled as explicit tmux copy');
    assert.equal(prevented, 1, 'Mac Cmd-Option-C prevents the terminal default');
    assert.deepStrictEqual(fetchCalls, [{url: '/api/tmux-copy-selection?session=1', method: 'POST'}], 'Mac Cmd-Option-C asks tmux for copy-mode text');
    fetchCalls.length = 0;
    prevented = 0;
    clearSelectionCount = 0;
    terminalCopyApi.clearClipboardTextForTest();
    terminalSelection = 'selected xterm text';
    const selectedCmdCResult = copyShortcutHandler({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(selectedCmdCResult, false, 'Mac Cmd-C with xterm selection is handled by browser copy');
    assert.equal(prevented, 1, 'Mac Cmd-C with xterm selection prevents the terminal default');
    assert.deepStrictEqual(fetchCalls, [], 'Mac Cmd-C with xterm selection does not ask tmux for copy-mode text');
    assert.equal(terminalCopyApi.clipboardTextForTest(), 'selected xterm text', 'Mac Cmd-C with xterm selection writes selected xterm text to clipboardData');
    assert.equal(clearSelectionCount, 1, 'Mac Cmd-C with xterm selection clears xterm selection after copying');
    const insideSelectionNode = {};
    const terminalContainer = {contains: node => node === insideSelectionNode};
    terminalSelection = '';
    terminalCopyApi.setBrowserSelectionForTest('selected browser text', insideSelectionNode);
    terminalCopyApi.installTerminalCopyShortcutForTest('1', {
      getSelection: () => terminalSelection,
      clearSelection: () => { clearSelectionCount += 1; },
      attachCustomKeyEventHandler(handler) { copyShortcutHandler = handler; },
    }, terminalContainer);
    fetchCalls.length = 0;
    prevented = 0;
    clearSelectionCount = 0;
    terminalCopyApi.clearClipboardTextForTest();
    const browserSelectedCmdCResult = copyShortcutHandler({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      preventDefault() { prevented += 1; },
    });
    const browserSelectionClearCount = terminalCopyApi.browserSelectionClearCountForTest();
    terminalCopyApi.clearBrowserSelectionForTest();
    assert.equal(browserSelectedCmdCResult, false, 'Mac Cmd-C with browser selection inside the terminal is handled by browser copy');
    assert.equal(prevented, 1, 'Mac Cmd-C with browser selection prevents the terminal default');
    assert.deepStrictEqual(fetchCalls, [], 'Mac Cmd-C with browser selection does not ask tmux for copy-mode text');
    assert.equal(terminalCopyApi.clipboardTextForTest(), 'selected browser text', 'Mac Cmd-C with browser selection writes browser-selected terminal text to clipboardData');
    assert.equal(clearSelectionCount, 1, 'Mac Cmd-C with browser selection clears xterm selection state after copying');
    assert.equal(browserSelectionClearCount, 1, 'Mac Cmd-C with browser selection clears browser selection inside the terminal after copying');
    const cmdArrowEvent = direction => ({
      type: 'keydown',
      code: direction < 0 ? 'ArrowLeft' : 'ArrowRight',
      key: direction < 0 ? 'ArrowLeft' : 'ArrowRight',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault() {},
      stopPropagation() {},
      stopImmediatePropagation() {},
    });
    const assertPaneTabTraversalRoundTrips = (navApi, label) => {
      const positions = navApi.paneTabTraversalPositionsForTest();
      assert.ok(positions.length >= 2, `${label}: traversal has multiple tabs`);
      assert.equal(navApi.adjacentPaneTabPosition(-1, {item: positions[0].item}), null, `${label}: Cmd-Left stops at the first tab`);
      assert.equal(navApi.adjacentPaneTabPosition(1, {item: positions.at(-1).item}), null, `${label}: Cmd-Right stops at the last tab`);
      for (let index = 0; index < positions.length - 1; index += 1) {
        const current = positions[index];
        const next = positions[index + 1];
        assert.deepStrictEqual(canonical(navApi.adjacentPaneTabPosition(1, {item: current.item})), canonical(next), `${label}: Cmd-Right ${current.item} -> ${next.item}`);
        assert.deepStrictEqual(canonical(navApi.adjacentPaneTabPosition(-1, {item: next.item})), canonical(current), `${label}: Cmd-Left ${next.item} -> ${current.item}`);
        navApi.activatePaneTab(current.slot, current.item, {userInitiated: true});
        assert.equal(navApi.selectAdjacentPaneTab(1, {userInitiated: true}), true, `${label}: selector moves right from ${current.item}`);
        assert.equal(navApi.visualActivePaneItemForTest(), next.item, `${label}: selector lands on ${next.item}`);
        assert.equal(navApi.selectAdjacentPaneTab(-1, {userInitiated: true}), true, `${label}: selector moves left from ${next.item}`);
        assert.equal(navApi.visualActivePaneItemForTest(), current.item, `${label}: selector returns to ${current.item}`);
      }
    };
    const assertStaleTerminalHandlerRoundTrips = (navApi, staleSession, label) => {
      let staleHandler = null;
      navApi.installTerminalCopyShortcutForTest(staleSession, {
        getSelection: () => '',
        attachCustomKeyEventHandler(handler) { staleHandler = handler; },
      });
      assert.equal(typeof staleHandler, 'function', `${label}: stale terminal handler was installed`);
      const positions = navApi.paneTabTraversalPositionsForTest();
      for (let index = 0; index < positions.length - 1; index += 1) {
        const current = positions[index];
        const next = positions[index + 1];
        navApi.activatePaneTab(current.slot, current.item, {userInitiated: true});
        assert.equal(staleHandler(cmdArrowEvent(1)), false, `${label}: stale handler owns Cmd-Right from ${current.item}`);
        assert.equal(navApi.visualActivePaneItemForTest(), next.item, `${label}: stale handler lands on ${next.item}`);
        assert.equal(staleHandler(cmdArrowEvent(-1)), false, `${label}: stale handler owns Cmd-Left from ${next.item}`);
        assert.equal(navApi.visualActivePaneItemForTest(), current.item, `${label}: stale handler returns to ${current.item}`);
      }
    };
    const terminalNavApi = loadYolomux('?platform=mac', ['1', '2', '3'], 'https:', 'MacIntel');
    const terminalNavEditor = terminalNavApi.fileEditorItemFor('/repo/app/README.md');
    const terminalNavSlots = terminalNavApi.emptyLayoutSlots();
    terminalNavSlots[terminalNavApi.layoutTreeKey] = terminalNavApi.splitNode(
      'row',
      terminalNavApi.leafNode('left'),
      terminalNavApi.splitNode('column', terminalNavApi.leafNode('slot1'), terminalNavApi.leafNode('slot2'), 50),
      50,
    );
    terminalNavSlots.left = terminalNavApi.paneStateWithTabs([terminalNavApi.fileExplorerItemId, '1'], '1');
    terminalNavSlots.slot1 = terminalNavApi.paneStateWithTabs(['2', terminalNavApi.prefsItemId], '2');
    terminalNavSlots.slot2 = terminalNavApi.paneStateWithTabs([terminalNavEditor, '3'], terminalNavEditor);
    terminalNavApi.setLayoutSlotsForTest(terminalNavSlots);
    assert.equal(terminalNavApi.adjacentPaneTabPosition(1, {item: '1'}).item, '2', 'Meta+Right spills from the last tab in a pane to the next pane');
    assert.equal(terminalNavApi.adjacentPaneTabPosition(-1, {item: '1'}).item, terminalNavApi.fileExplorerItemId, 'Meta+Left moves within a mixed pane to the previous non-tmux tab');
    assert.equal(terminalNavApi.adjacentPaneTabPosition(1, {item: '3'}), null, 'Meta+Right stops at the last pane tab instead of wrapping back to the first visible tab');
    const roundTripApi = loadYolomux('?platform=mac', ['1', '2', '3'], 'https:', 'MacIntel');
    const roundTripEditorA = roundTripApi.registerFileEditorLayoutItemForTest('/repo/app/README.md');
    const roundTripEditorB = roundTripApi.registerFileEditorLayoutItemForTest('/repo/app/settings.json');
    const roundTripSlots = roundTripApi.emptyLayoutSlots();
    roundTripSlots[roundTripApi.layoutTreeKey] = roundTripApi.splitNode(
      'row',
      roundTripApi.leafNode('left'),
      roundTripApi.splitNode('column', roundTripApi.leafNode('slot1'), roundTripApi.leafNode('slot2'), 50),
      50,
    );
    roundTripSlots.left = roundTripApi.paneStateWithTabs([roundTripApi.fileExplorerItemId, '1', roundTripEditorA], '1');
    roundTripSlots.slot1 = roundTripApi.paneStateWithTabs(['2', roundTripApi.prefsItemId, roundTripEditorB], '2');
    roundTripSlots.slot2 = roundTripApi.paneStateWithTabs(['3'], '3');
    roundTripApi.setLayoutSlotsForTest(roundTripSlots);
    assert.deepStrictEqual(
      canonical(roundTripApi.paneTabTraversalPositionsForTest().map(position => position.item)),
      [roundTripApi.fileExplorerItemId, '1', roundTripEditorA, '2', roundTripApi.prefsItemId, roundTripEditorB, '3'],
      'Meta+Arrow traversal is the pane list expanded as Pane1 tabs, then Pane2 tabs, then Pane3 tabs',
    );
    assertPaneTabTraversalRoundTrips(roundTripApi, 'serialized pane/tab structure');
    const staleRoundTripApi = loadYolomux('?platform=mac', ['1', '2', '3'], 'https:', 'MacIntel');
    const staleRoundTripEditorA = staleRoundTripApi.registerFileEditorLayoutItemForTest('/repo/app/README.md');
    const staleRoundTripEditorB = staleRoundTripApi.registerFileEditorLayoutItemForTest('/repo/app/settings.json');
    const staleRoundTripSlots = staleRoundTripApi.emptyLayoutSlots();
    staleRoundTripSlots[staleRoundTripApi.layoutTreeKey] = staleRoundTripApi.splitNode(
      'row',
      staleRoundTripApi.leafNode('left'),
      staleRoundTripApi.splitNode('column', staleRoundTripApi.leafNode('slot1'), staleRoundTripApi.leafNode('slot2'), 50),
      50,
    );
    staleRoundTripSlots.left = staleRoundTripApi.paneStateWithTabs([staleRoundTripApi.fileExplorerItemId, '1', staleRoundTripEditorA], '1');
    staleRoundTripSlots.slot1 = staleRoundTripApi.paneStateWithTabs(['2', staleRoundTripApi.prefsItemId, staleRoundTripEditorB], '2');
    staleRoundTripSlots.slot2 = staleRoundTripApi.paneStateWithTabs(['3'], '3');
    staleRoundTripApi.setLayoutSlotsForTest(staleRoundTripSlots);
    assertStaleTerminalHandlerRoundTrips(staleRoundTripApi, '1', 'stale terminal handler across panes');
    let navShortcutHandler = null;
    terminalNavApi.installTerminalCopyShortcutForTest('1', {
      getSelection: () => '',
      attachCustomKeyEventHandler(handler) { navShortcutHandler = handler; },
    });
    prevented = 0;
    const navRightResult = navShortcutHandler({
      type: 'keydown',
      code: 'ArrowRight',
      key: 'ArrowRight',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(navRightResult, false, 'Mac Cmd-Right is handled by the terminal shortcut guard');
    assert.equal(prevented, 1, 'Mac Cmd-Right prevents the terminal/xterm default');
    assert.equal(terminalNavApi.currentSessionActionTarget(), '2', 'Mac Cmd-Right moves from session 1 to the next visible pane tab');
    terminalNavApi.registerTerminalForTest('2', {getSelection: () => ''});
    terminalNavApi.setFocusedTerminal('2');
    prevented = 0;
    stopped = 0;
    stoppedImmediate = 0;
    const focusedNavResult = terminalNavApi.handleFocusedTerminalCopyShortcutForTest({
      type: 'keydown',
      code: 'ArrowLeft',
      key: 'ArrowLeft',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault() { prevented += 1; },
      stopPropagation() { stopped += 1; },
      stopImmediatePropagation() { stoppedImmediate += 1; },
    });
    assert.equal(focusedNavResult, true, 'focused terminal capture guard handles Mac Cmd-Left');
    assert.equal(prevented, 1, 'focused terminal Cmd-Left prevents browser/xterm defaults');
    assert.equal(stopped, 1, 'focused terminal Cmd-Left stops propagation before target handlers');
    assert.equal(stoppedImmediate, 1, 'focused terminal Cmd-Left stops sibling handlers');
    assert.equal(terminalNavApi.currentSessionActionTarget(), '1', 'Mac Cmd-Left moves back to the previous visible pane tab');
    const screenshotNavApi = loadYolomux('?platform=mac', ['8001', '8002', '8003'], 'https:', 'MacIntel');
    const screenshotEditor = screenshotNavApi.fileEditorItemFor('/home/keivenc/yolomux.dev2/docs/specs/SHARE_TEST_INVENTORY.md');
    const screenshotNavSlots = screenshotNavApi.emptyLayoutSlots();
    screenshotNavSlots[screenshotNavApi.layoutTreeKey] = screenshotNavApi.splitNode(
      'row',
      screenshotNavApi.leafNode('left'),
      screenshotNavApi.splitNode(
        'row',
        screenshotNavApi.splitNode('column', screenshotNavApi.leafNode('slot1'), screenshotNavApi.leafNode('slot2'), 66),
        screenshotNavApi.leafNode('slot3'),
        66,
      ),
      20,
    );
    screenshotNavSlots.left = screenshotNavApi.paneStateWithTabs([screenshotNavApi.fileExplorerItemId], screenshotNavApi.fileExplorerItemId);
    screenshotNavSlots.slot1 = screenshotNavApi.paneStateWithTabs(['8001', screenshotEditor], screenshotEditor);
    screenshotNavSlots.slot2 = screenshotNavApi.paneStateWithTabs(['8002'], '8002');
    screenshotNavSlots.slot3 = screenshotNavApi.paneStateWithTabs(['8003', screenshotNavApi.infoItemId], screenshotNavApi.infoItemId);
    screenshotNavApi.setLayoutSlotsForTest(screenshotNavSlots);
    let screenshotNavShortcutHandler = null;
    screenshotNavApi.installTerminalCopyShortcutForTest('8001', {
      getSelection: () => '',
      attachCustomKeyEventHandler(handler) { screenshotNavShortcutHandler = handler; },
    });
    prevented = 0;
    const fileBackResult = screenshotNavShortcutHandler({
      type: 'keydown',
      code: 'ArrowLeft',
      key: 'ArrowLeft',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(fileBackResult, false, 'stale terminal capture handles Cmd-Left from an active file tab');
    assert.equal(prevented, 1, 'stale terminal capture prevents the terminal default from an active file tab');
    assert.equal(screenshotNavApi.activeItemForSide('slot1'), '8001', 'Cmd-Left from SHARE_TEST_INVENTORY goes to 8001, not the previous pane boundary');
    screenshotNavApi.setFocusedPanelItem(screenshotNavApi.infoItemId);
    screenshotNavShortcutHandler = null;
    screenshotNavApi.installTerminalCopyShortcutForTest('8003', {
      getSelection: () => '',
      attachCustomKeyEventHandler(handler) { screenshotNavShortcutHandler = handler; },
    });
    prevented = 0;
    const infoBackResult = screenshotNavShortcutHandler({
      type: 'keydown',
      code: 'ArrowLeft',
      key: 'ArrowLeft',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(infoBackResult, false, 'stale terminal capture handles Cmd-Left from YO!info');
    assert.equal(prevented, 1, 'stale terminal capture prevents the terminal default from YO!info');
    assert.equal(screenshotNavApi.activeItemForSide('slot3'), '8003', 'Cmd-Left from YO!info goes to 8003 instead of reselecting YO!info');
    const noPingPongApi = loadYolomux('?platform=mac', ['8002'], 'https:', 'MacIntel');
    const noPingPongSlots = noPingPongApi.emptyLayoutSlots();
    noPingPongSlots[noPingPongApi.layoutTreeKey] = noPingPongApi.leafNode('slot1');
    noPingPongSlots.slot1 = noPingPongApi.paneStateWithTabs(['8002', noPingPongApi.prefsItemId], '8002');
    noPingPongApi.setLayoutSlotsForTest(noPingPongSlots);
    let noPingPongShortcutHandler = null;
    noPingPongApi.installTerminalCopyShortcutForTest('8002', {
      getSelection: () => '',
      attachCustomKeyEventHandler(handler) { noPingPongShortcutHandler = handler; },
    });
    const cmdRightFrom8002 = {
      type: 'keydown',
      code: 'ArrowRight',
      key: 'ArrowRight',
      metaKey: true,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault() {},
    };
    assert.equal(noPingPongShortcutHandler(cmdRightFrom8002), false, 'stale terminal capture handles first Cmd-Right from 8002');
    assert.equal(noPingPongApi.activeItemForSide('slot1'), noPingPongApi.prefsItemId, 'first Cmd-Right moves 8002 to Preferences');
    assert.equal(noPingPongShortcutHandler(cmdRightFrom8002), false, 'stale terminal capture still owns the repeated Cmd-Right');
    assert.equal(noPingPongApi.activeItemForSide('slot1'), noPingPongApi.prefsItemId, 'repeated Cmd-Right stops on Preferences instead of toggling back to 8002');
    const renderedOrderApi = loadYolomux('?platform=mac', ['8001', '8002', '8003'], 'https:', 'MacIntel');
    const renderedOrderSlots = renderedOrderApi.emptyLayoutSlots();
    renderedOrderSlots[renderedOrderApi.layoutTreeKey] = renderedOrderApi.splitNode(
      'row',
      renderedOrderApi.leafNode('left'),
      renderedOrderApi.splitNode('row', renderedOrderApi.leafNode('slot1'), renderedOrderApi.leafNode('slot3'), 66),
      20,
    );
    renderedOrderSlots.left = renderedOrderApi.paneStateWithTabs([renderedOrderApi.fileExplorerItemId], renderedOrderApi.fileExplorerItemId);
    renderedOrderSlots.slot1 = renderedOrderApi.paneStateWithTabs(['8001', '8002'], '8001');
    renderedOrderSlots.slot3 = renderedOrderApi.paneStateWithTabs(['8003'], '8003');
    renderedOrderApi.setLayoutSlotsForTest(renderedOrderSlots);
    const renderedTab = item => ({dataset: {paneTab: item}});
    const renderedGroup = (slot, left, items) => ({
      getBoundingClientRect: () => ({left, top: 0, width: 300, height: 600}),
      querySelector: selector => selector === '.dockview-panel-content > .panel' ? {dataset: {slot}} : null,
      querySelectorAll: selector => selector === '.dockview-pane-tab' ? items.map(renderedTab) : [],
    });
    renderedOrderApi.gridForTest().querySelectorAll = selector => selector === '.dv-groupview' ? [
      renderedGroup('left', 0, [renderedOrderApi.fileExplorerItemId]),
      renderedGroup('slot1', 400, ['8002', '8001']),
      renderedGroup('slot3', 900, ['8003']),
    ] : [];
    assert.deepStrictEqual(
      canonical(renderedOrderApi.paneTabTraversalPositionsForTest().map(position => position.item)),
      [renderedOrderApi.fileExplorerItemId, '8002', '8001', '8003'],
      'Meta+Arrow traversal follows the rendered Dockview tab order when serialized pane tabs are stale',
    );
    assertPaneTabTraversalRoundTrips(renderedOrderApi, 'rendered Dockview tab order');
    assertStaleTerminalHandlerRoundTrips(renderedOrderApi, '8001', 'stale terminal handler with rendered Dockview tab order');
    assert.equal(renderedOrderApi.adjacentPaneTabPosition(1, {item: renderedOrderApi.fileExplorerItemId}).item, '8002', 'Cmd-Right moves Finder to the first rendered middle tab');
    assert.equal(renderedOrderApi.adjacentPaneTabPosition(-1, {item: '8002'}).item, renderedOrderApi.fileExplorerItemId, 'Cmd-Left moves 8002 back to Finder');
    assert.equal(renderedOrderApi.adjacentPaneTabPosition(1, {item: '8002'}).item, '8001', 'Cmd-Right follows rendered middle tab order');
    assert.equal(renderedOrderApi.adjacentPaneTabPosition(-1, {item: '8001'}).item, '8002', 'Cmd-Left reverses rendered middle tab order');
    assert.equal(renderedOrderApi.adjacentPaneTabPosition(1, {item: '8003'}), null, 'Cmd-Right stops at the last rendered tab instead of wrapping to Finder');
    assert.equal(renderedOrderApi.adjacentPaneTabPosition(-1, {item: renderedOrderApi.fileExplorerItemId}), null, 'Cmd-Left stops at Finder instead of wrapping to the last rendered tab');
    const finderNavApi = loadYolomux('?platform=mac', ['1', '2'], 'https:', 'MacIntel');
    const finderNavEditor = finderNavApi.fileEditorItemFor('/repo/app/notes.md');
    const finderNavSlots = finderNavApi.emptyLayoutSlots();
    finderNavSlots[finderNavApi.layoutTreeKey] = finderNavApi.splitNode('row', finderNavApi.leafNode('left'), finderNavApi.leafNode('slot1'), 50);
    finderNavSlots.left = finderNavApi.paneStateWithTabs([finderNavApi.fileExplorerItemId, '1'], finderNavApi.fileExplorerItemId);
    finderNavSlots.slot1 = finderNavApi.paneStateWithTabs([finderNavEditor, '2'], finderNavEditor);
    finderNavApi.setLayoutSlotsForTest(finderNavSlots);
    finderNavApi.setFocusedPanelItem(finderNavApi.fileExplorerItemId);
    assert.equal(finderNavApi.globalShortcutTargetAllowsAppAction(null), true, 'Finder focus is eligible for app-level Meta+Arrow');
    assert.equal(finderNavApi.selectAdjacentPaneTab(1, {userInitiated: true}), true, 'Finder Meta+Right uses the shared pane-tab selector');
    assert.equal(finderNavApi.activeItemForSide('left'), '1', 'Finder Cmd-Right moves to the next tab in the same pane');
    const cmActive = {closest: selector => selector === '.cm-editor' ? cmActive : null};
    finderNavApi.setDocumentActiveElementForTest(cmActive);
    finderNavApi.setFocusedPanelItem('1');
    assert.equal(finderNavApi.globalShortcutTargetAllowsAppAction(null), false, 'Cmd-Right remains native line navigation inside the code editor');
    assert.equal(finderNavApi.activeItemForSide('left'), '1', 'editor Cmd-Right does not move app pane tabs');
    fetchCalls.length = 0;
    prevented = 0;
    clearSelectionCount = 0;
    terminalSelection = '';
    const ctrlCResult = copyShortcutHandler({
      type: 'keydown',
      code: 'KeyC',
      key: 'c',
      metaKey: false,
      ctrlKey: true,
      altKey: false,
      preventDefault() { prevented += 1; },
    });
    assert.equal(ctrlCResult, true, 'Ctrl-C with no xterm selection still reaches the PTY as SIGINT');
    assert.equal(prevented, 0, 'Ctrl-C with no selection does not prevent the terminal default');
    assert.deepStrictEqual(fetchCalls, [], 'Ctrl-C with no selection does not hit the tmux copy bridge');
    assert.equal(clearSelectionCount, 0, 'Ctrl-C fallthrough does not mutate xterm selection state');
    assert.equal(source.includes("key === 'k' && platformActionAllowed"), false, 'Cmd+K no longer opens the command palette');
    assert.ok(source.includes("!mod && globalShortcutTargetAllowsAppAction(event.target) && (event.key === '?'"), 'question-mark shortcut does not fire while typing in editors or terminals');
    assert.equal(api.appModifier({ctrlKey: true, metaKey: false, altKey: false}), true, 'PC app modifier is Ctrl');
    assert.equal(api.appModifier({ctrlKey: false, metaKey: true, altKey: false}), false, 'PC app modifier ignores Cmd/meta');
    const macShortcutApi = loadYolomux('?platform=mac', ['1'], 'http:', 'MacIntel');
    assert.equal(macShortcutApi.appModifier({ctrlKey: false, metaKey: true, altKey: false}), true, 'Mac app modifier is Cmd');
    assert.equal(macShortcutApi.appModifier({ctrlKey: true, metaKey: false, altKey: false}), false, 'Mac app modifier reserves Ctrl for tmux');
    assert.equal(macShortcutApi.appShortcutText('B'), '⌘+B');
    const pcOverrideShortcutApi = loadYolomux('?platform=pc', ['1'], 'http:', 'MacIntel');
    assert.equal(pcOverrideShortcutApi.appShortcutText('B'), 'Ctrl+B', 'platform override can force PC shortcut labels');
    assert.ok(Number.isFinite(api.fuzzySubsequenceScore('xy', 'hello X and blah Y')), 'fuzzy matcher allows ordered gaps');
    assert.equal(Number.isFinite(api.fuzzySubsequenceScore('xz', 'hello X and blah Y')), false, 'fuzzy matcher rejects missing characters');
    assert.ok(api.fuzzySearchScore('hel', ['helloXandYyy']) > api.fuzzySearchScore('hel', ['h e l']), 'fuzzy matcher ranks contiguous matches higher');
    assert.ok(api.fuzzySearchScore('2223', ['DIS-2223']) > api.fuzzySearchScore('2223', ['hello22 23']), 'fuzzy matcher ranks raw contiguous numeric matches above separated numeric runs');
    assert.ok(api.fuzzySearchScore('2223', ['DIS-2223']) > api.fuzzySearchScore('2223', ['2-2-2-3']), 'fuzzy matcher ranks raw contiguous numeric matches above punctuation-separated numeric runs');
    assert.ok(api.fuzzySearchScore('READ', ['README.md']) > api.fuzzySearchScore('READ', ['src/README.md']), 'fuzzy matcher prefers primary field prefixes');
    assert.ok(api.fuzzySearchScore('yoagent', ['YO!agent']) > api.fuzzySearchScore('yoagent', ['1', 'y o agent in details']), 'punctuation-insensitive label prefixes beat scattered detail matches');
    assert.deepStrictEqual(Array.from(api.fuzzySubsequenceMatch('xy', 'helloXandYyy').indexes), [5, 9], 'fuzzy matcher exposes matched indexes for result highlighting');
    assert.ok(api.fuzzyHighlightHtml('xy', 'helloXandYyy').includes('<mark class="fuzzy-match">X</mark>'), 'palette results highlight matched characters');
    assert.ok(api.fuzzyHighlightHtml('10144', '#10144').includes('<mark class="fuzzy-match">10144</mark>'), 'contiguous fuzzy matches render as one box');
    assert.equal(api.commandPaletteMatches({group: 'Tabs', label: 'helloXandYyy', detail: ''}, 'xy'), true, 'command palette uses fuzzy matching');
    assert.equal(api.commandPaletteMatches({group: 'Tabs', label: 'helloXandYyy', detail: ''}, 'xz'), false, 'command palette rejects non-matches');
    assert.ok(api.searchRankWeights.domainPrior.files.file > api.searchRankWeights.domainPrior.files.pane, 'Cmd-P domain weights rank files before panes for comparable matches');
    assert.ok(api.searchRankWeights.domainPrior.command.pane > api.searchRankWeights.domainPrior.command.file, 'Shift-Cmd-P domain weights rank panes before files for comparable matches');
    assert.ok(
      api.commandPaletteItemScore({group: 'Tabs', label: 'YO!agent', detail: '', searchFields: ['YO!agent']}, 'yoagent') >
        api.commandPaletteItemScore({group: 'Tabs', label: '1', detail: 'y o agent buried in details', searchFields: ['1', 'y o agent buried in details']}, 'yoagent'),
      'command palette ranks YO!agent first for punctuation-insensitive prefix queries'
    );
    const fileCandidate = (label, options = {}) => {
      const path = options.path || `/repo/current/${label}`;
      return {category: 'file', group: 'Files', label, detail: path, path, key: `file:${path}`, mtime: options.mtime || 0, searchFields: [label, path]};
    };
    const paneCandidate = (label, options = {}) => ({category: 'pane', group: 'Tabs', label, detail: options.detail || '', key: `pane:${label}`, mtime: options.mtime || 0, searchFields: [label, options.detail || '']});
    assert.ok(
      api.commandPaletteItemScore(paneCandidate('2', {detail: 'keivenchang/DIS-2223__nemotron-reasoning-end-token-stream-split'}), '2223', {surface: 'files'}) >
        api.commandPaletteItemScore(fileCandidate('hello22 23.md'), '2223', {surface: 'files', focusedRepoRoots: ['/repo/current']}),
      'Cmd-P ranks contiguous branch-number matches above scattered numeric file-name matches'
    );
    assert.ok(
      api.commandPaletteItemScore(paneCandidate('2', {detail: 'keivenchang/DIS-2223__nemotron-reasoning-end-token-stream-split'}), '2223', {surface: 'files'}) >
        api.commandPaletteItemScore(fileCandidate('2-2-2-3.md'), '2223', {surface: 'files', focusedRepoRoots: ['/repo/current']}),
      'Cmd-P ranks contiguous branch-number matches above punctuation-separated numeric file-name matches'
    );
    const rankValues = (surface, query, candidates, options = {}) => api.commandPaletteRankItems(candidates, query, {
      surface,
      nowSeconds: options.nowSeconds || 300,
      focusedRepoRoots: options.focusedRepoRoots || ['/repo/current'],
    }).map(options.value || (item => item.label));
    const sixCandidates = [
      fileCandidate('6.md'),
      fileCandidate('6-plan.md'),
      fileCandidate('a6.txt'),
      paneCandidate('6 Fix same-strip'),
    ];
    const fileHeavySixCandidates = [
      fileCandidate('6-00.md'),
      fileCandidate('6-01.md'),
      fileCandidate('6-02.md'),
      fileCandidate('6-03.md'),
      fileCandidate('6-04.md'),
      fileCandidate('6-05.md'),
      paneCandidate('6 Fix same-strip'),
      fileCandidate('a6.txt'),
    ];
    const claudeCandidates = [paneCandidate('claude'), fileCandidate('claude_notes.md')];
    const rankingCases = [
      {
        name: 'Cmd-P ranks anchored files first, then anchored pane, then non-anchored file',
        surface: 'files',
        query: '6',
        candidates: sixCandidates,
        expected: ['6.md', '6-plan.md', '6 Fix same-strip', 'a6.txt'],
      },
      {
        name: 'Cmd-P keeps a matching pane mixed into a file-heavy first screen',
        surface: 'files',
        query: '6',
        candidates: fileHeavySixCandidates,
        expected: ['6-00.md', '6-01.md', '6 Fix same-strip', '6-02.md', '6-03.md'],
        limit: 5,
      },
      {
        name: 'Shift-Cmd-P ranks the anchored pane before anchored files',
        surface: 'command',
        query: '6',
        candidates: sixCandidates,
        expected: ['6 Fix same-strip', '6.md', '6-plan.md', 'a6.txt'],
      },
      {
        name: 'contiguous matches beat scattered subsequences',
        surface: 'files',
        query: '123',
        candidates: [fileCandidate('hello 123'), fileCandidate('1 a 2 b 3 c')],
        expected: ['hello 123', '1 a 2 b 3 c'],
      },
      {
        name: 'newer mtime wins among comparable file matches',
        surface: 'files',
        query: '123',
        candidates: [fileCandidate('x 123 y', {mtime: 200}), fileCandidate('z 123 y', {mtime: 100})],
        expected: ['x 123 y', 'z 123 y'],
      },
      {
        name: 'focused repo affinity wins only among comparable matches',
        surface: 'files',
        query: 'target',
        candidates: [
          fileCandidate('target.md', {path: '/other/repo/target.md', mtime: 100}),
          fileCandidate('target.md', {path: '/repo/current/target.md', mtime: 100}),
        ],
        expected: ['/repo/current/target.md', '/other/repo/target.md'],
        value: item => item.path,
      },
      {
        name: 'anchored old match beats non-anchored new match',
        surface: 'files',
        query: 'ab',
        candidates: [fileCandidate('ab.md', {mtime: 1}), fileCandidate('x-ab.md', {mtime: 299})],
        expected: ['ab.md', 'x-ab.md'],
      },
      {
        name: 'repo affinity never rescues a weaker scattered match',
        surface: 'files',
        query: '123',
        candidates: [
          fileCandidate('hello 123', {path: '/other/repo/hello-123.md'}),
          fileCandidate('1 a 2 b 3 c', {path: '/repo/current/scattered.md'}),
        ],
        expected: ['hello 123', '1 a 2 b 3 c'],
      },
      {
        name: 'word-start anchored match beats mid-word match',
        surface: 'files',
        query: 'do',
        candidates: [fileCandidate('docs/'), fileCandidate('weirdo')],
        expected: ['docs/', 'weirdo'],
      },
      {
        name: 'Shift-Cmd-P ranks matching panes before files',
        surface: 'command',
        query: 'claude',
        candidates: claudeCandidates,
        expected: ['claude', 'claude_notes.md'],
      },
      {
        name: 'Cmd-P ranks matching files before panes',
        surface: 'files',
        query: 'claude',
        candidates: claudeCandidates,
        expected: ['claude_notes.md', 'claude'],
      },
      {
        name: 'empty Cmd-P file results sort by recency',
        surface: 'files',
        query: '',
        candidates: [fileCandidate('old.md', {mtime: 100}), fileCandidate('new.md', {mtime: 200})],
        expected: ['new.md', 'old.md'],
      },
      {
        name: 'empty Shift-Cmd-P pane results sort by recency',
        surface: 'command',
        query: '',
        candidates: [paneCandidate('old pane', {mtime: 100}), paneCandidate('new pane', {mtime: 200})],
        expected: ['new pane', 'old pane'],
      },
    ];
    for (const row of rankingCases) {
      const actual = [...rankValues(row.surface, row.query, row.candidates, row)];
      assert.deepStrictEqual(row.limit ? actual.slice(0, row.limit) : actual, row.expected, row.name);
    }
    // YO!info, YO!agent, Finder, Search & Runs, and Preferences are standalone virtual tabs.
    const paletteItems = api.commandPaletteCommandItems();
    const expectedVirtualLabels = [api.infoItemId, api.yoagentItemId, api.fileExplorerItemId, api.searchHistoryItemId, api.prefsItemId].map(api.itemLabel);
    const paletteVirtualLabels = paletteItems.filter(item => item.group === 'Tabs' && expectedVirtualLabels.includes(item.label));
    assert.equal(paletteVirtualLabels.length, expectedVirtualLabels.length, 'command palette lists each virtual tab once');
    assert.equal(expectedVirtualLabels.every(label => paletteVirtualLabels.some(item => item.label === label)), true, 'command palette includes all virtual tabs');
    assert.equal(paletteVirtualLabels.every(item => item.group === 'Tabs'), true, 'virtual tab palette entries come from the Tabs group, not duplicate menu commands');
    assert.equal(paletteItems.some(item => item.targetItem === '__changes__'), false, 'retired standalone Differ is absent from the command palette');
    assert.ok(paletteItems.some(item => item.group === 'Tabs' && item.targetItem === api.yoagentItemId), 'YO!agent is a standalone palette tab');
    const finderPaletteItem = paletteItems.find(item => item.targetItem === api.fileExplorerItemId);
    assert.ok(finderPaletteItem, 'command palette has a Finder/File Explorer tab row');
    assert.ok(
      api.commandPaletteItemScore(finderPaletteItem, 'File') > api.commandPaletteItemScore(api.fileQuickOpenItem('/repo/app/File.md'), 'File'),
      'typing File in the command palette promotes Finder/File Explorer above ordinary file matches'
    );
    api.setFileQuickOpenCandidatesForTest('/repo/app', [
      {name: 'helloXandYyy.py', path: '/repo/app/src/helloXandYyy.py', relative_path: 'src/helloXandYyy.py'},
    ]);
    const quickItem = api.fileQuickOpenItems().find(item => item.label === 'helloXandYyy.py');
    assert.ok(quickItem, 'file quick-open uses the same command-palette item shell');
    assert.equal(api.commandPaletteMatches(quickItem, 'xy'), true, 'file quick-open uses fuzzy matching');
    const doitApi = loadYolomux('', ['1']);
    doitApi.setFileQuickOpenCandidatesForTest('/repo/yolomux', [
      {name: 'websocket.py', path: '/repo/yolomux/yolomux_lib/websocket.py', relative_path: 'yolomux_lib/websocket.py', kind: 'file'},
      {name: 'DOIT.53.md', path: '/repo/yolomux/DOIT.53.md', relative_path: 'DOIT.53.md', kind: 'file'},
      {name: 'DOIT.parser-performance-v2-audit.md', path: '/repo/yolomux/frontend-crates/DOIT.parser-performance-v2-audit.md', relative_path: 'frontend-crates/DOIT.parser-performance-v2-audit.md', kind: 'file'},
      {name: 'DOIT.51.md', path: '/repo/yolomux/DOIT.51.md', relative_path: 'DOIT.51.md', kind: 'file'},
      {name: 'events.py', path: '/repo/yolomux/yolomux_lib/events.py', relative_path: 'yolomux_lib/events.py', kind: 'file'},
    ]);
    doitApi.setCommandPaletteStateForTest('files', 'DOIT:');
    assert.equal(doitApi.fileQuickOpenSearchText('DOIT:'), 'DOIT', 'file quick-open ignores a trailing colon with no line number');
    assert.equal(doitApi.commandPaletteSearchQuery(), 'DOIT', 'command palette scores the normalized file query');
    const doitRows = doitApi.commandPaletteItems()
      .filter(item => item.category === 'file')
      .map((item, index) => ({...item, index, score: doitApi.commandPaletteItemScore(item, 'DOIT')}))
      .filter(item => Number.isFinite(item.score))
      .sort((left, right) => right.score - left.score || left.label.localeCompare(right.label) || left.index - right.index)
      .map(item => item.label);
    assert.deepStrictEqual(canonical(doitRows.slice(0, 2)), ['DOIT.51.md', 'DOIT.53.md'], 'DOIT-numbered files stay contiguous for a DOIT: query');
    doitApi.setFileQuickOpenCandidatesForTest('/repo/yolomux', [
      {name: 'DOIT.53.md', path: '/repo/yolomux/DOIT.53.md', relative_path: 'DOIT.53.md', kind: 'file'},
      {name: 'report.html', path: '/home/test/dynamo/commits/logs/BA01C8.51e42e397/report.html', relative_path: 'commits/logs/BA01C8.51e42e397/report.html', indexed_root: '/home/test/dynamo', kind: 'file'},
    ]);
    doitApi.setCommandPaletteStateForTest('files', 'DOIT.53');
    const exactDoitRows = doitApi.commandPaletteItems()
      .filter(item => item.category === 'file')
      .map((item, index) => ({...item, index, score: doitApi.commandPaletteItemScore(item, 'DOIT.53')}))
      .filter(item => Number.isFinite(item.score))
      .sort((left, right) => right.score - left.score || left.index - right.index);
    assert.equal(exactDoitRows[0]?.label, 'DOIT.53.md', 'S15: exact local DOIT.53.md stays first for a dotted filename query');
    assert.equal(exactDoitRows.some(item => item.label === 'report.html'), false, 'S15: external indexed full-path-only fuzzy noise is hidden for dotted filename queries');
    const doitFamilyApi = loadYolomux('', ['1']);
    assert.deepStrictEqual(canonical(doitFamilyApi.fileQuickOpenExtraRootsForSearchQuery('DOIT')), ['/home/test'], 'DOIT queries search the current YOLOmux workdir family parent');
    doitFamilyApi.setFileQuickOpenCandidatesForTest('/home/test/yolomux.dev3', [
      {name: 'DOIT.64.md', path: '/home/test/yolomux.dev1/DOIT.64.md', relative_path: 'yolomux.dev1/DOIT.64.md', indexed_root: '/home/test', kind: 'file'},
      {name: 'DOIT.57.md', path: '/home/test/yolomux.dev2/DOIT.57.md', relative_path: 'yolomux.dev2/DOIT.57.md', indexed_root: '/home/test', kind: 'file'},
      {name: 'DOIT.parser-performance-v2-audit.md', path: '/home/test/dynamo/frontend-crates/DOIT.parser-performance-v2-audit.md', relative_path: 'frontend-crates/DOIT.parser-performance-v2-audit.md', indexed_root: '/home/test/dynamo', kind: 'file'},
      {name: '75_dockview_layout.js', path: '/home/test/yolomux.dev3/static_src/js/yolomux/75_dockview_layout.js', relative_path: 'static_src/js/yolomux/75_dockview_layout.js', indexed_root: '/home/test/yolomux.dev3', kind: 'file'},
    ]);
    doitFamilyApi.setCommandPaletteStateForTest('files', 'DOIT');
    const doitFamilyPaths = doitFamilyApi.fileQuickOpenItems()
      .filter(item => item.category === 'file')
      .map(item => item.path);
    assert.deepStrictEqual(canonical(doitFamilyPaths), ['/home/test/yolomux.dev1/DOIT.64.md', '/home/test/yolomux.dev2/DOIT.57.md'], 'DOIT quick-open keeps YOLOmux sibling docs and drops indexed Dynamo/fuzzy path noise');
    assert.deepStrictEqual(
      canonical(api.cursorStyleFileReference('/home/keivenc/yolomux.dev1/20260609-001.png', {imageIndex: 1})),
      {label: '[Image #1]', detail: "'/home/keivenc/yolomux.dev1/20260609-001.png'"},
      'file quick-open can render image hits in Popular IDE-style reference form'
    );
    assert.deepStrictEqual(
      canonical(api.cursorStyleFileReference("/home/test/with spaces/[draft] it's $fine.png", {imageIndex: 7})),
      {label: '[Image #7]', detail: "'/home/test/with spaces/[draft] it'\\''s $fine.png'"},
      'Search image references shell-quote paths with spaces and shell-special characters'
    );
    api.setFileQuickOpenCandidatesForTest('/home/keivenc/yolomux.dev1', [
      {name: '20260609-001.png', path: '/home/keivenc/yolomux.dev1/20260609-001.png', relative_path: '20260609-001.png', kind: 'file'},
      {name: '20260609-002.png', path: '/home/keivenc/yolomux.dev1/20260609-002.png', relative_path: '20260609-002.png', kind: 'file'},
      {name: "[draft] it's $fine.png", path: "/home/keivenc/yolomux.dev1/screens with spaces/[draft] it's $fine.png", relative_path: "screens with spaces/[draft] it's $fine.png", kind: 'file'},
    ]);
    const imageItems = api.fileQuickOpenItems().filter(item => item.key.includes('20260609-00'));
    assert.deepStrictEqual(canonical(imageItems.map(item => item.label)), ['[Image #1]', '[Image #2]'], 'Search image results use Popular IDE-style image numbering');
    assert.equal(imageItems[0].detail, "'/home/keivenc/yolomux.dev1/20260609-001.png'", 'Search image result details show the quoted absolute path');
    const specialImageItem = api.fileQuickOpenItems().find(item => item.path.includes('[draft]'));
    assert.equal(specialImageItem.detail, "'/home/keivenc/yolomux.dev1/screens with spaces/[draft] it'\\''s $fine.png'", 'Search image result details safely quote special-character paths');
    // C15 follow-up: cmd-P path mode offers a pinned "Open folder in Finder" row (Enter opens the typed
    // directory), while a subfolder entry descends and a file entry opens.
    api.setFileQuickOpenCandidatesForTest('/repo/app', [
      {name: 'dynamo', path: '/repo/app/dynamo', relative_path: 'dynamo', kind: 'dir'},
      {name: 'build.sh', path: '/repo/app/build.sh', relative_path: 'build.sh', kind: 'file'},
    ]);
    api.setCommandPaletteStateForTest('files', '/repo/app/');
    const pathItems = api.fileQuickOpenItems();
    const openFolder = pathItems.find(item => item.pinTop);
    assert.ok(openFolder, 'C15: path mode offers a pinned Open-folder row');
    assert.equal(openFolder.detail, '/repo/app', 'C15: the Open-folder row targets the listed directory');
    assert.equal(typeof openFolder.run, 'function', 'C15: the Open-folder row opens the directory');
    api.setCommandPaletteStateForTest('files', '/tmp/yolomux-paste-options-popup.png');
    const exactPathItems = api.fileQuickOpenItems();
    assert.equal(exactPathItems[0].path, '/tmp/yolomux-paste-options-popup.png', 'absolute file path input pins the exact file as the Enter target');
    assert.equal(exactPathItems[0].label, 'Open image yolomux-paste-options-popup.png', 'absolute image path input labels the pinned row as an image open');
    assert.ok(exactPathItems.some(item => item.key === 'open-folder:/tmp'), 'absolute file path input still offers the parent folder row');
    const dirEntry = pathItems.find(item => item.label === 'dynamo/');
    assert.ok(dirEntry && !dirEntry.pinTop, 'C15: a subfolder entry is a normal (descend) row, not pinned');
    // Bug fix: path-mode list entries (no indexed_root) must group under "Files", NOT "Indexed /" — the
    // empty indexed_root used to normalize to '/' and mislabel every directory row as indexed.
    assert.equal(dirEntry.group, 'Files', 'path-mode directory rows are grouped under Files, not mislabeled Indexed');
    assert.equal(pathItems.find(item => item.label === 'build.sh')?.group, 'Files', 'path-mode file rows are grouped under Files');
    // Typing an exact subdir name targets that subfolder for opening.
    api.setCommandPaletteStateForTest('files', '/repo/app/dynamo');
    assert.equal(api.fileQuickOpenItems().find(item => item.pinTop).detail, '/repo/app/dynamo', 'C15: an exact subdir name targets that subfolder');
    api.setCommandPaletteStateForTest('files', '');
    assert.equal(api.fileQuickOpenRootForSearch(), '/home/test/yolomux.dev', 'file quick-open defaults to the active repo root when no session cwd is known');
    api.setTranscriptInfoForTest('1', {project: {git: {root: '/repo/workspace'}}, selected_pane: {current_path: '/repo/workspace/src'}});
    api.setFocusedPanelItem('1');
    assert.equal(api.fileQuickOpenRootForSearch(), '/repo/workspace', 'file quick-open searches the workspace root when tmux is inside a repo');
    const fileRootApi = loadYolomux('', ['1']);
    fileRootApi.setTranscriptInfoForTest('1', {project: {git: {root: '/repo/workspace'}}, selected_pane: {current_path: '/repo/workspace/src'}});
    fileRootApi.setFocusedPanelItem('1');
    const activeMdPath = '/home/test/yolomux.dev/DOIT.54.md';
    const activeMdItem = fileRootApi.fileEditorItemFor(activeMdPath);
    const activeFileSlots = fileRootApi.emptyLayoutSlots();
    activeFileSlots.left = fileRootApi.paneStateWithTabs([activeMdItem], activeMdItem);
    fileRootApi.setOpenFileStateForTest(activeMdPath, {kind: 'text', gitRoot: '/home/test/yolomux.dev'});
    fileRootApi.setLayoutSlotsForTest(activeFileSlots);
    fileRootApi.setFocusedPanelItem(activeMdItem);
    assert.equal(fileRootApi.fileQuickOpenRootForSearch(), '/home/test/yolomux.dev', 'file quick-open uses the focused file editor repo before the last tmux cwd');
    assert.equal(fileRootApi.fileQuickOpenRootForFile('/home/test/yolomux.dev/src/file.js'), '/home/test/yolomux.dev/src', 'unloaded files fall back to their containing directory');
    api.setFileExplorerIndexedDirsForTest(['/repo/tools', '/repo/tools/src', '/repo/other']);
    assert.equal(api.fileExplorerDirectoryIsIndexed('/repo/tools'), true, 'Finder indexed directories are tracked by exact path');
    assert.equal(api.fileExplorerDirectoryIsIndexed('/repo/tools/src'), false, 'Finder compacts redundant child index marks under an indexed ancestor');
    assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/repo/workspace')), ['/repo/workspace', '/repo/other', '/repo/tools'], 'file quick-open adds indexed Finder directories and compacts nested search roots');
    assert.equal(api.fileQuickOpenScopeLabel('/repo/workspace'), '/repo/workspace + 2 indexed', 'file quick-open placeholder summarizes indexed search scope');
    api.setFileQuickOpenCandidatesForTest('/repo/workspace', [
      {name: 'target.md', path: '/home/test/dynamo/notes/target.md', relative_path: 'target.md', indexed_root: '/home/test/dynamo/notes'},
      {name: 'target.md', path: '/repo/workspace/docs/target.md', relative_path: 'docs/target.md', indexed_root: '/repo/workspace'},
    ]);
    const priorityItems = api.fileQuickOpenItems().filter(item => item.label === 'target.md');
    const contextItem = priorityItems.find(item => item.key === 'file:/repo/workspace/docs/target.md');
    const indexedItem = priorityItems.find(item => item.key === 'file:/home/test/dynamo/notes/target.md');
    assert.ok(contextItem && indexedItem, 'file quick-open renders both context and external indexed matches');
    assert.ok(api.commandPaletteItemScore(contextItem, 'target') > api.commandPaletteItemScore(indexedItem, 'target'), 'file quick-open prioritizes the active context over external indexed roots');
    api.setFileExplorerIndexedDirsForTest(['/home/test/dynamo']);
    assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test')), ['/home/test/dynamo'], 'an indexed child under the default root replaces the broad live parent search');
    assert.equal(api.fileQuickOpenRootMatchesPathAlias('/home/test/yolomux.dev', 'yolo/TODO.md'), true, 'bare yolo/... queries match the YOLOmux repo basename as a narrow root alias');
    assert.deepStrictEqual(canonical(api.fileQuickOpenExtraRootsForSearchQuery('yolo/TODO.md')), ['/home/test/yolomux.dev'], 'bare yolo/... queries add the app repo root without adding all of home');
    assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test', 'yolo/TODO.md')), ['/home/test/dynamo', '/home/test/yolomux.dev'], 'yolo/TODO.md searches the YOLOmux checkout even when home is narrowed to indexed Dynamo');
    assert.deepStrictEqual(canonical(api.fileQuickOpenRootsForSearch('/home/test', 'src/file.js')), ['/home/test/dynamo'], 'unrelated slash queries do not add the YOLOmux root');
    const staleDoitApi = loadYolomux('', ['1']);
    const staleDoitPath = '/home/test/yolomux.dev1/DOIT.57.md';
    const realDoitPath = '/home/test/yolomux.dev2/DOIT.57.md';
    const staleDoitItem = staleDoitApi.registerFileEditorLayoutItemForTest(staleDoitPath);
    const staleDoitSlots = staleDoitApi.emptyLayoutSlots();
    staleDoitSlots.left = staleDoitApi.paneStateWithTabs([staleDoitItem], staleDoitItem);
    staleDoitApi.setLayoutSlotsForTest(staleDoitSlots);
    staleDoitApi.setOpenFileStateForTest(staleDoitPath, {kind: 'error', externalMissing: true, error: 'file deleted or moved on disk'});
    staleDoitApi.setFileQuickOpenCandidatesForTest('/home/test/yolomux.dev3', [
      {name: 'DOIT.57.md', path: realDoitPath, relative_path: 'DOIT.57.md', indexed_root: '/home/test/yolomux.dev2', kind: 'file'},
    ]);
    staleDoitApi.setCommandPaletteStateForTest('files', 'doit57');
    const staleDoitItems = staleDoitApi.commandPaletteItems();
    const staleDoitRows = staleDoitItems.filter(item => item.targetItem === staleDoitItem
      || item.path === staleDoitPath
      || item.key?.includes(staleDoitPath)
      || (item.searchFields || []).includes(staleDoitPath));
    assert.deepStrictEqual(canonical(staleDoitRows), [], 'missing open file paths are hidden from quick search results');
    assert.ok(staleDoitItems.some(item => item.category === 'file' && item.path === realDoitPath), 'real indexed DOIT.57 result remains available when a stale tab path is hidden');
    const quickTargetApi = loadYolomux('', ['1', '2']);
    const quickOpenTargetSlots = quickTargetApi.emptyLayoutSlots();
    quickOpenTargetSlots.left = quickTargetApi.paneStateWithTabs(['1'], '1');
    quickOpenTargetSlots.slot1 = quickTargetApi.paneStateWithTabs(['2'], '2');
    quickOpenTargetSlots[quickTargetApi.layoutTreeKey] = quickTargetApi.splitNode('row', quickTargetApi.leafNode('left'), quickTargetApi.leafNode('slot1'), 50);
    quickTargetApi.setLayoutSlotsForTest(quickOpenTargetSlots);
    quickTargetApi.setFocusedPanelItem('2');
    assert.equal(quickTargetApi.fileQuickOpenTargetSlot(), 'slot1', 'Cmd-P normal file opens target the currently active pane, not the first pane');
    assert.equal(quickTargetApi.slotForTabActivation(quickTargetApi.prefsItemId), 'slot1', 'DOIT.56 N2: Preferences opens in the focused pane, not the first/largest pane');
    assert.equal(quickTargetApi.slotForTabActivation(quickTargetApi.infoItemId), 'slot1', 'DOIT.56 N2: YO!info opens in the focused pane, not the first/largest pane');
    const editorTargetItem = fileRootApi.fileEditorItemFor('/home/test/yolomux.dev/DOIT.53.md');
    fileRootApi.setOpenFileStateForTest('/home/test/yolomux.dev/DOIT.53.md', {kind: 'text', gitRoot: '/home/test/yolomux.dev'});
    fileRootApi.registerFileEditorLayoutItemForTest('/home/test/yolomux.dev/DOIT.53.md');
    const editorTargetSlots = fileRootApi.emptyLayoutSlots();
    editorTargetSlots.left = fileRootApi.paneStateWithTabs(['1'], '1');
    editorTargetSlots.slot1 = fileRootApi.paneStateWithTabs([editorTargetItem], editorTargetItem);
    editorTargetSlots[fileRootApi.layoutTreeKey] = fileRootApi.splitNode('row', fileRootApi.leafNode('left'), fileRootApi.leafNode('slot1'), 50);
    fileRootApi.setLayoutSlotsForTest(editorTargetSlots);
    fileRootApi.setFocusedPanelItem(editorTargetItem);
    assert.equal(fileRootApi.fileQuickOpenTargetSlot(), 'slot1', 'Cmd-P targets the active file-editor pane as well as terminal panes');
    const finderOnlyTargetSlots = fileRootApi.emptyLayoutSlots();
    finderOnlyTargetSlots.left = fileRootApi.paneStateWithTabs([fileRootApi.fileExplorerItemId], fileRootApi.fileExplorerItemId);
    finderOnlyTargetSlots.slot1 = fileRootApi.paneStateWithTabs(['1'], '1');
    finderOnlyTargetSlots[fileRootApi.layoutTreeKey] = fileRootApi.splitNode('row', fileRootApi.leafNode('left'), fileRootApi.leafNode('slot1'), 22);
    fileRootApi.setLayoutSlotsForTest(finderOnlyTargetSlots);
    fileRootApi.setFocusedPanelItem(fileRootApi.fileExplorerItemId);
    assert.equal(fileRootApi.fileQuickOpenTargetSlot(), null, 'Cmd-P does not target the reserved Finder pane for normal file opens');
    assert.equal(fileRootApi.slotForTabActivation(fileRootApi.prefsItemId), 'slot1', 'DOIT.56 N2: opening a virtual tab while Finder is focused falls back outside the reserved Finder pane');
    // #31: the Finder indexed badge reflects the cached build status without writing a long label into
    // the one-letter status column. Date/Ago mode hides the badge entirely because the date slot owns
    // the right side of the row.
    api.setFileExplorerTreeDateModeForTest('none');
    api.setFileExplorerIndexStatusForTest('/home/test/dynamo', 'building');
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), '…', '#31: a building index renders a compact building badge');
    api.setFileExplorerIndexStatusForTest('/home/test/dynamo', 'ready');
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), 'I', '#31: a ready index renders a compact indexed badge');
    api.setFileExplorerTreeDateModeForTest('date');
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), '', '#31: Date mode hides the indexed badge instead of stacking it next to the date');
    api.setFileExplorerTreeDateModeForTest('relative');
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo'), '', '#31: Ago mode hides the indexed badge instead of stacking it next to the age');
    api.setFileExplorerTreeDateModeForTest('none');
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/not-indexed'), '', '#31: a non-indexed directory renders no badge');
    const checkboxInput = {getAttribute: name => name === 'type' ? 'checkbox' : ''};
    const textInput = {getAttribute: name => name === 'type' ? 'text' : ''};
    assert.equal(api.markdownPreviewInputAllowed(checkboxInput), true, 'GFM task-list checkbox inputs survive markdown sanitization');
    assert.equal(api.markdownPreviewInputAllowed(textInput), false, 'non-checkbox inputs stay blocked in markdown preview');
    const markdownSource = fs.readFileSync('static/yolomux.js', 'utf8');
    const blockedTagsSource = markdownSource.slice(markdownSource.indexOf('const MARKDOWN_PREVIEW_BLOCKED_TAGS'), markdownSource.indexOf('const MARKDOWN_PREVIEW_URL_ATTRS'));
    assert.ok(!blockedTagsSource.includes("'input'"), 'markdown sanitizer no longer blocks safe task-list checkboxes at the tag level');
    assert.ok(markdownSource.includes("const MARKDOWN_PREVIEW_INPUT_ATTRS = new Set(['type', 'checked', 'disabled', 'aria-label', 'class'])"), 'markdown task checkbox sanitizer has an input attribute allow-list');
    const cssSource = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.ok(cssSource.includes('.markdown-body ul.contains-task-list'), 'markdown task lists remove the regular bullet gutter');
    assert.ok(cssSource.includes('.markdown-body li.task-list-item > input[type="checkbox"]'), 'markdown task-list checkbox alignment is pinned');
    // Descendants of an indexed root stay visually quiet; the root's compact indexed badge is the only repeated signal.
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/dynamo/lib'), '', 'indexed-root descendants do not show a noisy repeated badge');
    assert.equal(api.fileExplorerIndexBadgeText('/home/test/elsewhere'), '', 'C11: an unrelated directory shows no badge');
    // #23: the topbar universal search renders a launcher button (opens the unified palette on click).
    const topbarSearch = api.createTopbarSearch();
    assert.ok(String(topbarSearch.className || '').includes('topbar-search'), '#23: topbar search renders a .topbar-search launcher');
    assert.equal(topbarSearch.type, 'button', '#23: the topbar search launcher is a button');
    assert.equal(topbarSearch.children.length, 3, '#23: the launcher renders an icon, a label, and a shortcut hint');
    assert.equal(api.tabMenuItems().some(item => item.type === 'search'), false, 'Tabs menu does not include its own search input');
    api.setTabsMenuSearchTextForTest('xy');
    assert.equal(api.tabSearchScore('1', 'xy') < 0, true, 'tab search uses fuzzy score and rejects non-matches');
    api.setTabsMenuSearchTextForTest('');
    assert.equal(helpMenu.items.find(item => item.label === 'Open README').disabled, false);
    assert.equal(api.tabMenuItems().map(item => item.label).filter(Boolean).includes('Current Tab'), false);
    const namedSessionApi = loadYolomux('', ['1', 'dynamo2']);
    const tmuxOnlySlots = namedSessionApi.emptyLayoutSlots();
    tmuxOnlySlots[namedSessionApi.layoutTreeKey] = namedSessionApi.leafNode('left');
    tmuxOnlySlots.left = namedSessionApi.paneStateWithTabs(['1', 'dynamo2'], '1');
    namedSessionApi.setLayoutSlotsForTest(tmuxOnlySlots);
    namedSessionApi.setTabsMenuSearchTextForTest('do2');
    const filteredNamedCommands = namedSessionApi.tabMenuItems().filter(item => item.type === 'command');
    const filteredNamedTabs = filteredNamedCommands.filter(item => item.targetItem);
    assert.equal(filteredNamedTabs.length, 1, 'Tabs search filters navigator entries without hiding launch commands');
    assert.deepEqual(filteredNamedTabs.map(item => item.targetItem), ['dynamo2'], 'Tabs search keeps the matching session command');
    assert.ok(filteredNamedCommands.some(item => item.label === 'new Xterm'), 'Tabs search keeps new-session commands available');
    assert.ok(Number.isFinite(namedSessionApi.tabSearchScore('dynamo2', 'do2')), 'Tabs search matches the raw session name');
    namedSessionApi.setTabsMenuSearchTextForTest('');
    const tmuxTabActionLabels = namedSessionApi.tabMenuItems().map(item => item.label).filter(Boolean);
    assert.equal(tmuxTabActionLabels.includes('Current Tab'), false);
    assert.equal(tmuxTabActionLabels.includes('YOLO policy: enable'), false);
    namedSessionApi.activatePaneTab('left', 'dynamo2');
    assert.equal(namedSessionApi.currentSessionActionTarget(), 'dynamo2');
    assert.equal(namedSessionApi.appMenuTree().find(menu => menu.id === 'tmux').items.find(item => item.label === "Rename tmux session 'dynamo2'").disabled, false);
    assert.equal(namedSessionApi.appMenuTree().find(menu => menu.id === 'tmux').items[0].label, 'YO off');
    assert.equal(namedSessionApi.appMenuTree().find(menu => menu.id === 'tmux').items.some(item => item.label === "Enable YOLO for Tmux Session 'dynamo2'"), false);
    const sessionActions = api.tmuxSessionActionCommands('1');
    assert.deepStrictEqual(canonical(sessionActions.map(item => item.label)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Kill tmux session '1'"]);
    assert.equal(sessionActions.some(item => item.disabled), false);
    assert.equal(sessionActions.find(item => item.label === "Rename tmux session '1'").detail, '');
    const contextMenuNode = () => api.testElementForId('appOverlayRoot').children.find(child => child.classList?.contains('terminal-context-menu'));
    api.showSessionContextMenu('1', 10, 10);
    const contextMenu = contextMenuNode();
    assert.ok(contextMenu.children[0].innerHTML.includes('Pin Tab'), 'tab context menu starts with Pin Tab');
    assert.ok(contextMenu.children[0].innerHTML.includes('app-menu-ui-icon-pin'), 'Pin Tab context menu row has the shared pin icon');
    assert.equal(contextMenu.children[0].getAttribute('aria-label'), 'Pin Tab', 'Pin Tab context menu row has an accessible label');
    assert.deepStrictEqual(canonical(Array.from(contextMenu.children).map(child => child.textContent).filter(Boolean)), ["Enable YOLO for Tmux Session '1'", "Rename tmux session '1'", "Transcript for session '1'", "YO!summary for session '1'", "Event log for session '1'", "Kill tmux session '1'"]);
    assert.equal(contextMenu.children.some(child => child.className === 'terminal-context-menu-separator'), true);
    const contextButtons = Array.from(contextMenu.children).filter(child => child.textContent);
    assert.equal(contextButtons[contextButtons.length - 1].classList.contains('danger'), true, 'Kill is styled as the final destructive action');
    api.setPinnedTabsForTest(['1']);
    api.showSessionContextMenu('1', 20, 20);
    const pinnedContextMenu = contextMenuNode();
    assert.ok(pinnedContextMenu.children[0].innerHTML.includes('Unpin Tab'), 'pinned tab context menu flips to Unpin Tab');
    assert.equal(pinnedContextMenu.children[0].getAttribute('aria-checked'), 'true', 'pinned tab context menu row is checked');
    const fileItemForMenu = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/README.md');
    api.showTabContextMenu(fileItemForMenu, 30, 30);
    const fileContextMenu = contextMenuNode();
    assert.ok(fileContextMenu.children[0].innerHTML.includes('Pin Tab'), 'file editor tabs also get the Pin Tab context menu');
    assert.equal(fileContextMenu.children.length, 1, 'non-tmux tab context menu only shows tab-level actions today');
    api.setPinnedTabsForTest([]);
    const sessionViews = api.tmuxSessionViewCommands('1');
    assert.deepStrictEqual(canonical(sessionViews.map(item => item.label)), ["Transcript for session '1'", "YO!summary for session '1'", "Event log for session '1'", 'Info Bar']);
    assert.equal(api.fileIconFor('screenshot.png'), '🖼');
    assert.equal(api.fileIconFor('run.sh'), '🐚');
    assert.equal(api.fileIconFor('main.rs'), '🧩');
    assert.equal(api.fileIconFor('config.yaml'), '⚙');
    assert.equal(api.fileIconFor('README'), '📝');
    assert.equal(api.fileIconFor('Dockerfile'), '⚙');
    assert.equal(api.fileIconFor('archive.tar'), '🗜');
    assert.equal(api.fileIconFor('unknown.bin'), '📄');
    assert.equal(api.fileIconClassFor('README.md'), 'file-icon-doc');
    assert.equal(api.fileIconClassFor('main.rs'), 'file-icon-code');
    assert.equal(api.fileIconClassFor('screenshot.png'), 'file-icon-image');
    const controlsHtml = api.panelControlsHtml('1');
    const hideDetailsLabel = api.t('pane.details.hide');
    const showDetailsLabel = api.t('pane.details.show');
    assert.equal(controlsHtml.includes('data-panel-tab-overflow'), false);
    assert.equal((controlsHtml.match(/pane-actions-dots/g) || []).length, 1, 'pane header has one merged ellipsis menu');
    assert.equal(hideDetailsLabel, 'hide Info Bar');
    assert.equal(showDetailsLabel, 'show Info Bar');
    const paneInfoLocaleFiles = fs.readdirSync('static_src/locales').filter(name => name.endsWith('.json')).sort();
    const localizedPaneInfoLabels = {
      'zh-Hans.json': {
        'menu.tmux.paneDetails': '信息栏',
        'pane.details.hide': '隐藏信息栏',
        'pane.details.show': '显示信息栏',
      },
      'zh-Hant.json': {
        'menu.tmux.paneDetails': '資訊列',
        'pane.details.hide': '隱藏資訊列',
        'pane.details.show': '顯示資訊列',
      },
    };
    for (const localeFile of paneInfoLocaleFiles) {
      const catalog = JSON.parse(fs.readFileSync(`static_src/locales/${localeFile}`, 'utf8'));
      for (const key of ['menu.tmux.paneDetails', 'pane.details.hide', 'pane.details.show']) {
        const value = String(catalog[key] || '');
        if (localizedPaneInfoLabels[localeFile]) {
          assert.equal(value, localizedPaneInfoLabels[localeFile][key], `W5: ${localeFile} ${key} localizes the pane metadata bar label`);
          assert.equal(value.includes('Info Bar'), false, `W5: ${localeFile} ${key} does not leak English Info Bar`);
        } else {
          assert.ok(value.includes('Info Bar'), `W5: ${localeFile} ${key} names the pane metadata bar Info Bar`);
        }
        assert.equal(/details/i.test(value), false, `W5: ${localeFile} ${key} no longer uses Details for the pane metadata bar`);
      }
    }
    const sourceEnCatalog = JSON.parse(fs.readFileSync('static_src/locales/en.json', 'utf8'));
    assert.equal(sourceEnCatalog['popover.details'], 'details', 'W5: generic popover/YO!agent Details copy stays separate from pane Info Bar labels');
    assert.ok(controlsHtml.includes(`title="${hideDetailsLabel}" aria-label="${hideDetailsLabel}" aria-pressed="true"`), 'pane header Info Bar toggle starts as the hide Info Bar action');
    assert.equal(controlsHtml.includes('title="YO!info" aria-label="YO!info"'), false, 'pane header detail toggle is not mislabeled as the YO!info pane');
    const detailsPanel = new TestElement('panel-1');
    detailsPanel.dataset.slot = '1';
    const innerDetailToggle = new TestElement('', 'button');
    innerDetailToggle.dataset.detailToggle = '1';
    const headerDetailToggle = new TestElement('', 'button');
    headerDetailToggle.dataset.detailToggle = '1';
    detailsPanel.appendChild(innerDetailToggle);
    api.testElementForId('body').appendChild(headerDetailToggle);
    api.setPanelDetailsCollapsedForTest(detailsPanel, false);
    assert.equal(innerDetailToggle.title, hideDetailsLabel, 'inner detail close uses the expanded-state hide label');
    assert.equal(headerDetailToggle.title, hideDetailsLabel, 'header detail toggle uses the expanded-state hide label');
    assert.equal(headerDetailToggle.getAttribute('aria-pressed'), 'true', 'expanded details mark the header toggle pressed');
    assert.equal(headerDetailToggle.classList.contains('active'), true, 'expanded details keep the header toggle active');
    api.setPanelDetailsCollapsedForTest(detailsPanel, true);
    assert.equal(innerDetailToggle.title, showDetailsLabel, 'inner detail close uses the collapsed-state show label');
    assert.equal(headerDetailToggle.title, showDetailsLabel, 'header detail toggle uses the collapsed-state show label');
    assert.equal(headerDetailToggle.getAttribute('aria-label'), showDetailsLabel, 'header detail aria label follows collapsed state');
    assert.equal(headerDetailToggle.getAttribute('aria-pressed'), 'false', 'collapsed details mark the header toggle unpressed');
    assert.equal(headerDetailToggle.classList.contains('active'), false, 'collapsed details remove the header active state');
    const dockviewPanel = new TestElement('panel-7');
    dockviewPanel.dataset.slot = 'left';
    dockviewPanel.dataset.layoutItem = '7';
    const dockviewHeaderDetailToggle = new TestElement('', 'button');
    dockviewHeaderDetailToggle.dataset.detailToggle = '7';
    api.testElementForId('body').appendChild(dockviewHeaderDetailToggle);
    api.setPanelDetailsCollapsedForTest(dockviewPanel, true);
    assert.equal(dockviewHeaderDetailToggle.getAttribute('aria-pressed'), 'false', 'Dockview header detail toggle syncs by layout item, not the left/right slot id');
    assert.equal(dockviewHeaderDetailToggle.title, showDetailsLabel, 'Dockview header detail toggle flips to show Info Bar when collapsed');
    const stableTerminalPane = api.testElementForId('terminal-pane-stable');
    stableTerminalPane.classList.add('active');
    stableTerminalPane.clientWidth = 720;
    stableTerminalPane.clientHeight = 260;
    const stableFits = [];
    api.registerTerminalForTest('stable', {
      cols: 80,
      rows: 24,
      _core: {_renderService: {_renderer: {dimensions: {css: {cell: {width: 9, height: 18}}}}}},
      resize(cols, rows) {
        this.cols = cols;
        this.rows = rows;
        stableFits.push({cols, rows});
      },
      refresh() {},
    });
    api.fitTerminalForTest('stable');
    assert.deepEqual(stableFits, [{cols: 79, rows: 14}], 'terminal fit uses the full 720px pane width, not a half-width transient box');
    api.fitTerminalForTest('stable');
    assert.deepEqual(stableFits, [{cols: 79, rows: 14}], 'a second fit with unchanged pane and cell metrics does not re-resize xterm');
    stableTerminalPane.clientWidth = 360;
    api.fitTerminalForTest('stable');
    assert.equal(stableFits.length, 2, 'a real pane width change still resizes the terminal');
    assert.ok(/function terminalCanPublishRemoteSize\(\)\s*\{[\s\S]*document\.visibilityState !== 'hidden'/.test(source), 'hidden/background browser tabs cannot publish terminal resize authority');
    assert.ok(/function wsUrl\(session\)[\s\S]*new URLSearchParams\(\{session, client: shareClientId\}\)/.test(source), 'terminal sockets carry a stable browser client id for resize authority');
    assert.ok(/function sendRemoteResize\(session, options = \{\}\)[\s\S]*terminalCanPublishRemoteSize\(\)[\s\S]*foreground:\s*true[\s\S]*message\.activate = true[\s\S]*message\.client = shareClientId/.test(source), 'terminal resize frames carry explicit foreground authority and browser identity');
    assert.ok(/function claimVisibleTerminalResizeAuthority\(reason = '', options = \{\}\)[\s\S]*visibleTerminalResizeAuthorityEntries\(\)[\s\S]*fitTerminal\(session, \{claim: true\}\)/.test(source), 'browser activation claims every visible xterm pane through the shared fit path');
    assert.ok(/function installTerminalResizeAuthorityHandlers\(\)[\s\S]*window\.addEventListener\('focus'[\s\S]*document\.addEventListener\('visibilitychange'/.test(source), 'browser focus/visibility activates visible xterm resize authority');
    assert.ok(/function scheduleRemoteResize\(session,[\s\S]*terminalCanPublishRemoteSize\(\)[\s\S]*item\.resizeTimer = null/.test(source), 'hidden/background tabs cancel pending terminal resize timers instead of shrinking tmux later');
    const authorityApi = loadYolomux('', ['1', '2', '3']);
    const authoritySlots = authorityApi.emptyLayoutSlots();
    authoritySlots[authorityApi.layoutTreeKey] = authorityApi.splitNode('row', authorityApi.leafNode('left'), authorityApi.leafNode('right'), 50);
    authoritySlots.left = authorityApi.paneStateWithTabs(['1'], '1');
    authoritySlots.right = authorityApi.paneStateWithTabs(['2'], '2');
    authorityApi.setLayoutSlotsForTest(authoritySlots);
    const authoritySends = [];
    for (const session of ['1', '2', '3']) {
      const pane = authorityApi.testElementForId(`terminal-pane-${session}`);
      pane.classList.add('active');
      pane.clientWidth = session === '2' ? 900 : 720;
      pane.clientHeight = 260;
      authorityApi.registerTerminalForTest(session, {
        cols: 80,
        rows: 24,
        _core: {_renderService: {_renderer: {dimensions: {css: {cell: {width: 9, height: 18}}}}}},
        resize(cols, rows) {
          this.cols = cols;
          this.rows = rows;
        },
        refresh() {},
      }, {readyState: WebSocket.OPEN, send(message) { authoritySends.push({session, message: JSON.parse(message)}); }});
    }
    authorityApi.claimVisibleTerminalResizeAuthorityForTest('test', {force: true});
    assert.deepEqual(authoritySends.map(item => item.session).sort(), ['1', '2'], 'browser activation claims all visible xterm panes and skips hidden/background tabs');
    assert.equal(authoritySends.every(item => item.message.type === 'resize' && item.message.foreground === true && item.message.activate === true && item.message.client), true, 'activation resize frames carry foreground authority, activation, and browser id');
    authorityApi.claimVisibleTerminalResizeAuthorityForTest('test');
    assert.equal(authoritySends.length, 2, 'same browser surface does not keep re-sending unchanged visible terminal authority');
    const terminalPane = api.testElementForId('terminal-pane-1');
    terminalPane.classList.add('active');
    terminalPane.clientWidth = 720;
    terminalPane.clientHeight = 260;
    const fits = [];
    api.registerTerminalForTest('1', {
      cols: 80,
      rows: 24,
      resize(cols, rows) {
        this.cols = cols;
        this.rows = rows;
        fits.push({cols, rows});
      },
      refresh() {},
    });
    api.setPanelDetailsCollapsedForTest(detailsPanel, false);
    assert.ok(fits.length >= 1, 'hiding or showing the Info Bar schedules a visible tmux terminal fit');
    assert.ok(api.tmuxPaneTabHtml('1', null, {key: 'blocked', short: 'Blocked', label: 'Blocked', reason: 'test'}).includes('tab-symbol'));
    assert.equal(api.tmuxSessionNameError('good_name-1.2'), '');
    assert.equal(api.tmuxSessionNameError('dynamo 2'), '');
    assert.equal(api.tmuxSessionNameError('bad/name').includes('letters'), true);
    assert.ok(api.panelControlsHtml('1').includes('data-pane-actions="1"'));
    assert.equal(api.panelControlsHtml('__files__').includes('data-pane-actions'), false);
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['finder.toolbar.rootLabel'], undefined, 'Finder toolbar no longer carries a Root button label');
    assert.equal(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))['finder.rootMode.fixed'], undefined, 'Finder toolbar no longer carries a Root mode title');
    const serverShell = fs.readFileSync('yolomux_lib/web.py', 'utf8');
    assert.ok(serverShell.includes('id="fileExplorerRootMode"'));
    assert.ok(serverShell.includes('>Sync</button>'), 'Server-rendered Finder root toggle starts as Sync');
    assert.equal(serverShell.includes('>Root</button>'), false, 'Server-rendered Finder root toggle must not flash Root before JS runs');
    const readonlyApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly');
    assert.equal(readonlyApi.tmuxSessionActionCommands('1').every(item => item.disabled), true);
    readonlyApi.setAutoApproveStateForTest('1', {enabled: true});
    assert.equal(readonlyApi.menuTabCommand('1', {toggleYolo: true}).html.includes('data-auto-session'), false);
    api.setTranscriptInfoForTest('1', {
      project: {git: {cwd: '/home/test/yolomux.dev', root: '/home/test/yolomux.dev'}},
      panes: [{current_path: '/home/test/yolomux.dev/mock', command: 'bash'}],
      selected_pane: {current_path: '/home/test/yolomux.dev'},
    });
    assert.equal(api.fileExplorerRootModeValue(), 'sync');
    assert.equal(api.activeTmuxDirectoryPath('1'), '/home/test/yolomux.dev');
    assert.equal(api.fileExplorerRootForOpen('1'), '/home/test/yolomux.dev');
    api.setSessionFilesPayloadForTest({session: '1', repos: [{repo: '/repo/app'}], files: []});
    api.setFileExplorerRootMode('sync', {sync: false});
    assert.equal(api.fileExplorerRootModeValue(), 'sync');
    assert.equal(api.fileExplorerRootForOpen('1'), '/home/test/yolomux.dev');
    api.setTranscriptInfoForTest('1', {
      project: {git: {cwd: '/home/test/yolomux.dev/static_src/js', root: '/home/test/yolomux.dev'}},
      selected_pane: {current_path: '/home/test/yolomux.dev/static_src/js'},
    });
    api.setSessionFilesPayloadForTest({session: '1', repos: [], files: []});
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test/yolomux.dev',
      affectedDirs: ['/home/test/yolomux.dev'],
      expandPaths: [],
    });
    assert.equal(api.finderDirectoryForItem('1'), '');
    assert.equal(api.activeFinderDirectoryPath('1'), '');
    assert.equal(api.finderTargetPathForItem('1'), '');
    assert.equal(api.activeFinderTargetPath('1'), '');
    const fileItem = api.registerFileEditorLayoutItem('/home/test/yolomux.dev/TODO.md');
    assert.equal(api.fileExplorerRootForOpen(fileItem), '/home/test');
    api.setFileExplorerRootMode('fixed', {sync: false});
    assert.equal(api.finderDirectoryForItem(fileItem), '/home/test/yolomux.dev');
    assert.equal(api.finderTargetPathForItem(fileItem), '/home/test/yolomux.dev/TODO.md');
    assert.equal(api.activeFinderTargetPath(fileItem), '/home/test/yolomux.dev/TODO.md');
    assert.equal(api.pathIsInsideDirectory('/home/test/yolomux.dev/mock', '/home/test'), true);
    assert.equal(api.pathIsInsideDirectory('/home/test2/yolomux.dev/mock', '/home/test'), false);
    assert.deepStrictEqual(canonical(api.childPathParts('/home/test', '/home/test/yolomux.dev/mock')), ['yolomux.dev', 'mock']);
    assert.deepStrictEqual(canonical(api.childPathParts('/home/test/yolomux.dev', '/home/test/yolomux.dev/TODO.md')), ['TODO.md']);
    assert.equal(api.commonAncestorPath(['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b/src']), '/home/test/dynamo');
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo/repo-a/src'}});
    api.setSessionFilesPayloadForTest({
      session: '1',
      repos: [{repo: '/home/test/dynamo/repo-a'}, {repo: '/home/test/dynamo/repo-b'}],
      files: [
        {repo: '/home/test/dynamo/repo-a', path: 'src/a.js', abs_path: '/home/test/dynamo/repo-a/src/a.js'},
        {repo: '/home/test/dynamo/repo-b', path: 'lib/b.py', abs_path: '/home/test/dynamo/repo-b/lib/b.py'},
      ],
    });
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test/dynamo',
      affectedDirs: ['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b', '/home/test/dynamo/repo-a/src', '/home/test/dynamo/repo-b/lib'],
      expandPaths: ['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b'],
    });
    const highlightSets = api.fileExplorerSessionHighlightSetsForTest('1');
    assert.deepStrictEqual(canonical(highlightSets.repoRoots), []);
    assert.deepStrictEqual(canonical(highlightSets.touchedDirs), []);
    assert.deepStrictEqual(canonical(highlightSets.expandedDirs), ['/home/test/dynamo/repo-a', '/home/test/dynamo/repo-b']);
    assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a', 'dir', '1'), 'file-tree-row--sync-expanded');
    assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a/src', 'dir', '1'), '');
    assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a/src/a.js', 'file', '1'), '');
    api.setFileExplorerRootMode('fixed', {sync: false});
    assert.equal(api.fileExplorerSessionHighlightClassForPath('/home/test/dynamo/repo-a', 'dir'), '');
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setSessionFilesPayloadForTest({
      session: '2',
      repos: [{repo: '/home/test/dynamo/repo-a'}],
      files: [{repo: '/home/test/dynamo/repo-a', path: 'src/a.js', abs_path: '/home/test/dynamo/repo-a/src/a.js'}],
    });
    assert.equal(api.fileExplorerSessionHighlightClassForTest('/home/test/dynamo/repo-a', 'dir', '1'), '');
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo1/src'}});
    api.setSessionFilesPayloadForTest({
      session: '1',
      repos: [{repo: '/home/test/dynamo1'}, {repo: '/tmp/x'}],
      files: [
        {repo: '/home/test/dynamo1', path: 'src/a.js', abs_path: '/home/test/dynamo1/src/a.js'},
        {repo: '/tmp/x', path: 'b.py', abs_path: '/tmp/x/b.py'},
      ],
    });
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test/dynamo1',
      affectedDirs: ['/home/test/dynamo1', '/tmp/x', '/home/test/dynamo1/src'],
      expandPaths: [],
    });
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/ai-config/claude/skills'}});
    api.setSessionFilesPayloadForTest({
      session: '1',
      repos: [{repo: '/home/test/ai-config'}],
      files: [
        {repo: '/home/test/ai-config', path: 'claude/skills/a/SKILL.md', abs_path: '/home/test/ai-config/claude/skills/a/SKILL.md'},
        {repo: '/home/test/ai-config', path: 'hooks/install.js', abs_path: '/home/test/ai-config/hooks/install.js'},
      ],
    });
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test/ai-config',
      affectedDirs: ['/home/test/ai-config', '/home/test/ai-config/claude/skills/a', '/home/test/ai-config/hooks'],
      expandPaths: [],
    });
    api.setSessionFilesPayloadForTest({
      session: '1',
      repos: [],
      files: [
        {path: '/home/test/ai-config/claude/skills/a/SKILL.md', abs_path: '/home/test/ai-config/claude/skills/a/SKILL.md'},
        {path: '/home/test/ai-config/hooks/install.js', abs_path: '/home/test/ai-config/hooks/install.js'},
      ],
    });
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test/ai-config',
      affectedDirs: ['/home/test/ai-config/claude/skills/a', '/home/test/ai-config/hooks'],
      expandPaths: ['/home/test/ai-config/claude', '/home/test/ai-config/hooks'],
    });
    api.setTranscriptInfoForTest('1', {selected_pane: {current_path: ''}});
    api.setSessionFilesPayloadForTest({session: '1', repos: [], files: []});
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test',
      affectedDirs: [],
      expandPaths: [],
    });
    api.setSessionFilesPayloadForTest({
      session: '3',
      repos: [{repo: '/home/test/stale'}],
      files: [{repo: '/home/test/stale', path: 'old.js', abs_path: '/home/test/stale/old.js'}],
    });
    assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
      session: '1',
      root: '/home/test',
      affectedDirs: [],
      expandPaths: [],
    });
    const syncCss = fs.readFileSync('static/yolomux.css', 'utf8');
    assert.equal(syncCss.includes('--finder-session-repo-bg'), false, 'Finder Sync repo marker no longer uses a background token');
    assert.equal(syncCss.includes('--finder-session-touched-bg'), false, 'Finder Sync touched-dir marker no longer uses a background token');
    assert.equal(syncCss.includes('--finder-sync-expanded-bg'), false, 'Finder auto-expanded Sync marker no longer uses a background token');
    assert.ok(/\.file-tree-row\.file-tree-row--sync-expanded > \.file-tree-name,[\s\S]*?\.file-tree-row\.file-tree-row--session-repo > \.file-tree-name,[\s\S]*?\.file-tree-row\.file-tree-row--session-touched > \.file-tree-name,[\s\S]*?\.file-tree-row\.file-tree-row--changed-ancestor > \.file-tree-name\s*\{[\s\S]*?font-weight:\s*800/.test(syncCss), 'Finder Sync and changed-ancestor markers bold the row name instead of painting a background');
    assert.ok(/\.file-tree-git-status\.file-tree-git-status-unknown\s*\{[\s\S]*?background:\s*rgba\(226,\s*232,\s*240,\s*0\.10\) !important[\s\S]*?color:\s*rgba\(226,\s*232,\s*240,\s*0\.46\)/.test(syncCss), 'dark Finder ? status badge is faint against a dark background');
    assert.ok(/body\.theme-light \.file-tree-git-status\.file-tree-git-status-unknown\s*\{[\s\S]*?background:\s*rgb\(var\(--overlay-slate-rgb\) \/ 0\.06\) !important[\s\S]*?color:\s*rgb\(var\(--overlay-slate-rgb\) \/ 0\.36\)/.test(syncCss), 'light Finder ? status badge is faint against a light background through the shared slate overlay token');
    assert.equal(syncCss.includes('.changes-status-unknown'), false, 'Differ no longer has a separate unknown-status CSS path');
    const scrollContainer = {
      clientHeight: 100,
      isConnected: true,
      scrollTop: 0,
      getBoundingClientRect() {
        return {top: 0, bottom: 100, height: 100};
      },
    };
    const targetRow = {
      isConnected: true,
      getBoundingClientRect() {
        return {top: 420, bottom: 440, height: 20};
      },
    };
    assert.equal(api.scrollFileTreeRowIntoView(scrollContainer, targetRow), true);
    assert.equal(scrollContainer.scrollTop, 380);
    const visibleRow = {
      isConnected: true,
      getBoundingClientRect() {
        return {top: 40, bottom: 60, height: 20};
      },
    };
    assert.equal(api.scrollFileTreeRowIntoView(scrollContainer, visibleRow), true);
    assert.equal(scrollContainer.scrollTop, 380);

    const {tree, rows} = makeFileTree(['/repo/a.md', '/repo/b.md', '/repo/c.md', '/repo/d.md']);
    tree.scrollTop = 120;
    api.selectFileTreePath('/repo/a.md');
    api.updateFileTreeSelectionFromClick(rows[2], '/repo/c.md', {shiftKey: true, metaKey: false, ctrlKey: false});
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()), {
      paths: ['/repo/a.md', '/repo/b.md', '/repo/c.md'],
      anchor: '/repo/a.md',
      manual: true,
    });
    assert.equal(tree.scrollTop, 120);

    const fallbackTree = makeFileTree(['/repo/a.md', '/repo/b.md', '/repo/c.md']);
    api.setFileExplorerSelectionForTest(['/repo/b.md'], '/outside/old.md');
    api.updateFileTreeSelectionFromClick(fallbackTree.rows[2], '/repo/c.md', {shiftKey: true, metaKey: false, ctrlKey: false});
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()), {
      paths: ['/repo/b.md', '/repo/c.md'],
      anchor: '/repo/b.md',
      manual: true,
    });

    api.setFileExplorerSelectionForTest(['/old/a.md', '/repo/a.md'], '/old/a.md');
    api.pruneFileExplorerSelectionForRoot('/repo');
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()), {
      paths: ['/repo/a.md'],
      anchor: null,
      manual: false,
    });

    const clickOnlyTree = makeFileTree(['/repo/open-me.md']);
    api.onFileTreeRowClick(clickOnlyTree.rows[0], '/repo/open-me.md', {kind: 'file', name: 'open-me.md'}, {shiftKey: false, metaKey: false, ctrlKey: false});
    assert.deepStrictEqual(canonical(api.fileExplorerSelectionForTest()).paths, ['/repo/open-me.md']);
    assert.equal(api.activeFileForTest(), null);

    const refreshTree = new TestElement('refresh-tree');
    refreshTree.setAttribute('role', 'tree');
    refreshTree.classList.add('file-explorer-tree-panel');
    api.setFileExplorerExpandedForTest(['/repo/src']);
    const rootEntries = [{name: 'src', kind: 'dir'}, {name: 'README.md', kind: 'file'}];
    api.renderTreeChildrenForTest(refreshTree, '/repo', rootEntries, 0, [
      ['/repo/src', [{name: 'a.py', kind: 'file'}, {name: 'b.py', kind: 'file'}]],
    ]);
    const srcRow = refreshTree.children[0];
    const srcChildren = refreshTree.children[1];
    const firstFileRow = srcChildren.children[0];
    refreshTree.scrollTop = 44;
    srcChildren.scrollTop = 12;
    api.renderTreeChildrenForTest(refreshTree, '/repo', rootEntries, 0, [
      ['/repo/src', [{name: 'a.py', kind: 'file'}, {name: 'c.py', kind: 'file'}]],
    ]);
    assert.equal(refreshTree.children[0], srcRow);
    assert.equal(refreshTree.children[1], srcChildren);
    assert.equal(srcChildren.children[0], firstFileRow);
    assert.equal(srcChildren.children.length, 2);
    assert.equal(srcChildren.children[1].dataset.path, '/repo/src/c.py');
    assert.equal(refreshTree.scrollTop, 44);
    assert.equal(srcChildren.scrollTop, 12);

    api.setFileExplorerSessionFilesPayloadForTest({
      loaded: true,
      repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 5, removed: 3}],
      files: [
        {abs_path: '/repo/README.md', agent: 'codex', status: 'M', added: 5, removed: 3},
        {abs_path: '/repo/app/a.py', repo: '/repo/app', status: 'M', added: 5, removed: 3},
      ],
    });
    const gitTree = new TestElement('git-tree');
    gitTree.setAttribute('role', 'tree');
    gitTree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(gitTree, '/repo', [
      {name: 'app', kind: 'dir', is_repo: true, repo: {root: '/repo/app', name: 'app', branch: 'feature/x'}},
      {name: 'README.md', kind: 'file'},
    ]);
    const repoName = gitTree.children[0].querySelector(':scope > .file-tree-name');
    const repoDiff = gitTree.children[0].querySelector(':scope > .file-tree-diff');
    assert.equal(repoName.textContent, 'app [feature/x]', 'repo rows keep cached branch metadata inline');
    assert.equal(repoName.textContent.includes('+5/-3'), false, 'repo rows do not append combined numstat inline');
    assert.ok(repoName.innerHTML.includes('file-tree-repo-branch'), 'repo row branch is wrapped for monospace styling');
    assert.equal(repoName.innerHTML.includes('file-tree-repo-delta'), false, 'repo rows no longer use the retired combined numstat span');
    assert.ok(/changes-diff-add[^>]*>\+5<\/span>[\s\S]*changes-diff-remove[^>]*>-3<\/span>/.test(repoDiff.innerHTML), 'repo rows render aggregate numstat through the shared diff element with colored spans');
    assert.equal(gitTree.children[0].classList.contains('repo-non-main'), true, 'non-main repo rows stand out');
    assert.equal(gitTree.children[1].querySelector(':scope > .file-tree-name').textContent, 'README.md', 'changed file name has no inline numstat');
    assert.ok(gitTree.children[1].querySelector(':scope > .file-tree-diff').innerHTML.includes('changes-diff-add') && gitTree.children[1].querySelector(':scope > .file-tree-diff').innerHTML.includes('changes-diff-remove'), 'changed file diff stats in separate diff element with colored spans');
    assert.equal(gitTree.children[1].classList.contains('has-agent'), true, 'changed Finder rows with agent attribution use the inline-agent file-tree layout');
    assert.ok(gitTree.children[1].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'changed Finder rows render the same agent icon slot as Differ rows');
    assert.equal(
      gitTree.children[1].querySelector(':scope > .file-tree-name').nextElementSibling,
      gitTree.children[1].querySelector(':scope > .file-tree-agent'),
      'changed Finder rows place the AI marker immediately after the filename',
    );
    const modifiedStatus = gitTree.children[1].querySelector(':scope > .file-tree-git-status');
    assert.equal(modifiedStatus.textContent, 'M');
    assert.equal(modifiedStatus.getAttribute('title'), 'M: modified', 'Finder M status badge explains itself on hover');
    assert.equal(modifiedStatus.getAttribute('aria-label'), 'M: modified', 'Finder M status badge is labeled for assistive tech');
    api.setFileExplorerSessionFilesPayloadForTest({
      loaded: true,
      repos: [],
      files: [{abs_path: '/repo/screenshot.png', agent: 'codex', status: '?'}],
    });
    const unknownTree = new TestElement('unknown-tree');
    unknownTree.setAttribute('role', 'tree');
    api.renderTreeChildrenForTest(unknownTree, '/repo', [{name: 'screenshot.png', kind: 'file'}]);
    const unknownStatus = unknownTree.children[0].querySelector(':scope > .file-tree-git-status');
    assert.equal(unknownStatus.textContent, '?');
    assert.equal(unknownStatus.classList.contains('file-tree-git-status-unknown'), true, 'Finder ? status badge uses a faint-neutral class');
    assert.equal(unknownStatus.getAttribute('title'), '?: untracked', 'Finder ? status badge explains itself on hover');

    const hiddenFile = {abs_path: '/repo/.github/workflows/ci.yml', repo: '/repo', path: '.github/workflows/ci.yml', status: 'M', added: 41, removed: 0};
    api.setFileExplorerSessionFilesPayloadForTest({loaded: true, repos: [], files: [hiddenFile]});
    const hiddenFinderTree = new TestElement('hidden-finder-tree');
    hiddenFinderTree.setAttribute('role', 'tree');
    api.renderTreeChildrenForTest(hiddenFinderTree, '/repo', [{name: '.github', kind: 'dir'}]);
    assert.equal(hiddenFinderTree.children.length, 0, 'Finder still hides dot-directories when hidden-files is off');
    const hiddenDifferTree = new TestElement('hidden-differ-tree');
    hiddenDifferTree.setAttribute('role', 'tree');
    api.renderTreeChildrenForTest(hiddenDifferTree, '/repo', [{name: '.github', kind: 'dir'}], 0, [
      ['/repo/.github', [{name: 'workflows', kind: 'dir'}]],
      ['/repo/.github/workflows', [{name: 'ci.yml', kind: 'file'}]],
    ], {
      differMode: true,
      includeHidden: true,
    });
    const hiddenDifferRows = Object.fromEntries(hiddenDifferTree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
    assert.ok(hiddenDifferRows['/repo/.github'], 'Differ renders changed hidden ancestors even when Finder hidden-files is off');
    assert.ok(hiddenDifferRows['/repo/.github/workflows/ci.yml'], 'Differ renders changed files under hidden directories');
    const hiddenDifferHtml = api.fileExplorerChangesPanelHtml();
    assert.ok(hiddenDifferHtml.includes('data-path="/repo/.github"'), 'rendered Differ panel includes hidden changed ancestors');
    assert.ok(hiddenDifferHtml.includes('data-open-change-file="/repo/.github/workflows/ci.yml"'), 'rendered Differ panel opens changed files under hidden directories');
    assert.ok(/data-open-change-file="\/repo\/\.github\/workflows\/ci\.yml"[\s\S]*changes-diff-add">\+41</.test(hiddenDifferHtml), 'Differ shows the hidden file numstat');

    api.setFileExplorerSessionFilesPayloadForTest({
      loaded: true,
      repos: [],
      files: [
        {abs_path: '/repo/A/B/C/F', agents: ['codex'], status: 'M', mtime: 1, added: 2, removed: 1},
        {abs_path: '/repo/A/B/C/G', agent: 'claude', status: 'M', mtime: 2, added: 3, removed: 4},
        {abs_path: '/repo/A/B/D/H', agents: ['codex'], status: 'A', mtime: 3, added: 8, removed: 0},
        {abs_path: '/repo/A/B/D/touched-only.py', agent: 'claude', status: 'T', mtime: 4, source: 'transcript'},
      ],
    });
    api.setFileExplorerExpandedForTest(['/repo/A', '/repo/A/B', '/repo/A/B/C', '/repo/A/B/D']);
    const ancestorTree = new TestElement('ancestor-tree');
    ancestorTree.setAttribute('role', 'tree');
    ancestorTree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(ancestorTree, '/repo', [{name: 'A', kind: 'dir'}], 0, [
      ['/repo/A', [{name: 'B', kind: 'dir'}]],
      ['/repo/A/B', [{name: 'C', kind: 'dir'}, {name: 'D', kind: 'dir'}]],
      ['/repo/A/B/C', [{name: 'F', kind: 'file'}, {name: 'G', kind: 'file'}]],
      ['/repo/A/B/D', [{name: 'H', kind: 'file'}]],
    ]);
    const ancestorRows = Object.fromEntries(ancestorTree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
    assert.equal(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-dir-count').textContent, '3', 'Finder changed ancestor A shows total changed descendants as a bare count');
    assert.equal(ancestorRows['/repo/A/B'].querySelector(':scope > .file-tree-dir-count').textContent, '3', 'Finder changed ancestor B shows total changed descendants as a bare count');
    assert.equal(ancestorRows['/repo/A/B/C'].querySelector(':scope > .file-tree-dir-count').textContent, '2', 'Finder changed ancestor C counts only its subtree as a bare count');
    assert.equal(ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-dir-count').textContent, '1', 'Finder changed ancestor badges ignore transcript-only touched files with no diff and render a bare count');
    assert.ok(ancestorRows['/repo/A'].classList.contains('file-tree-row--changed-ancestor'), 'Finder changed ancestors are bold-marked');
    assert.ok(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon claude'), 'Finder changed ancestor A inherits Claude marker from descendants');
    assert.ok(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'Finder changed ancestor A inherits Codex marker from descendants');
    assert.ok(ancestorRows['/repo/A/B/C'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon claude'), 'Finder changed ancestor C inherits Claude marker from descendants');
    assert.ok(ancestorRows['/repo/A/B/C'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'Finder changed ancestor C inherits Codex marker from descendants');
    assert.ok(!ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon claude'), 'Finder changed ancestor D only shows agents present in that subtree');
    assert.ok(ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-agent').innerHTML.includes('agent-icon codex'), 'Finder changed ancestor D inherits Codex marker from descendants');
    assert.equal(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-name').textContent.includes('+13/-5'), false, 'Finder changed ancestors do not append combined numstat inline');
    assert.ok(/changes-diff-add[^>]*>\+13<\/span>[\s\S]*changes-diff-remove[^>]*>-5<\/span>/.test(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-diff').innerHTML), 'Finder changed ancestors render aggregate numstat through the shared diff element with colored spans');
    assert.ok(/changes-diff-add[^>]*>\+8<\/span>/.test(ancestorRows['/repo/A/B/D'].querySelector(':scope > .file-tree-diff').innerHTML), 'Finder changed ancestors omit zero remove counts through the shared file diff helper');
    assert.ok(/agent-icon claude"[^>]*title="modified by Claude [^"]* ago"/.test(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML), 'Finder changed ancestor Claude marker hover names who modified it and when');
    assert.ok(/agent-icon codex"[^>]*title="modified by Codex [^"]* ago"/.test(ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').innerHTML), 'Finder changed ancestor Codex marker hover names who modified it and when');
    assert.equal(
      ancestorRows['/repo/A'].querySelector(':scope > .file-tree-name').nextElementSibling,
      ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent'),
      'Finder changed ancestors place the inherited AI marker immediately after the filename',
    );
    assert.equal(
      ancestorRows['/repo/A'].querySelector(':scope > .file-tree-agent').nextElementSibling,
      ancestorRows['/repo/A'].querySelector(':scope > .file-tree-diff'),
      'Finder changed ancestors place diff counts before the changed-file count',
    );
    assert.equal(
      ancestorRows['/repo/A'].querySelector(':scope > .file-tree-diff').nextElementSibling,
      ancestorRows['/repo/A'].querySelector(':scope > .file-tree-dir-count'),
      'Finder changed ancestors place the changed-file count after diff counts',
    );
    assert.equal(ancestorRows['/repo/A/B/C/F'].classList.contains('file-tree-row--changed-ancestor'), false, 'changed leaf files do not get the ancestor marker');

    const derivedSnapshot = row => {
      const status = row.querySelector(':scope > .file-tree-git-status');
      const agent = row.querySelector(':scope > .file-tree-agent');
      const count = row.querySelector(':scope > .file-tree-dir-count');
      const name = row.querySelector(':scope > .file-tree-name');
      const ownedClasses = ['file-tree-row--changed-ancestor', 'repo-non-main', 'has-agent', 'git-modified', 'git-untracked', 'git-deleted', 'git-staged', 'git-transcript']
        .filter(className => row.classList.contains(className));
      return {
        classes: ownedClasses,
        statusText: status?.textContent || '',
        statusTitle: status?.getAttribute('title') || '',
        statusAria: status?.getAttribute('aria-label') || '',
        statusHidden: status?.hidden === true,
        agentHtml: agent?.innerHTML || '',
        agentHidden: agent?.hidden === true,
        countText: count?.textContent || '',
        countHidden: count?.hidden === true,
        nameText: name?.textContent || '',
        nameHtml: name?.innerHTML || '',
      };
    };
    api.setFileExplorerTreeDateModeForTest('none');
    api.setFileExplorerIndexedDirsForTest(['/repo/indexed']);
    api.setFileExplorerIndexStatusForTest('/repo/indexed', 'building');
    api.setFileExplorerSessionFilesPayloadForTest({
      loaded: true,
      repos: [],
      files: [{abs_path: '/repo/indexed/a.py', agents: ['codex'], status: 'M', mtime: 1, added: 2, removed: 0}],
    });
    api.setFileExplorerExpandedForTest(['/repo/indexed']);
    const incrementalTree = new TestElement('incremental-tree');
    incrementalTree.setAttribute('role', 'tree');
    incrementalTree.classList.add('file-explorer-tree-panel');
    const indexedEntriesByDir = [['/repo/indexed', [{name: 'a.py', kind: 'file'}]]];
    api.renderTreeChildrenForTest(incrementalTree, '/repo', [{name: 'indexed', kind: 'dir'}], 0, indexedEntriesByDir);
    const incrementalRows = Object.fromEntries(incrementalTree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
    assert.equal(incrementalRows['/repo/indexed'].querySelector(':scope > .file-tree-git-status').textContent, '…', 'indexed directory starts with the building badge');
    api.setFileExplorerIndexStatusForTest('/repo/indexed', 'ready');
    api.setFileExplorerSessionFilesPayloadForTest({
      loaded: true,
      repos: [],
      files: [
        {abs_path: '/repo/indexed/a.py', agents: ['claude'], status: '?', mtime: 2, added: 4, removed: 1},
        {abs_path: '/repo/indexed/b.py', agents: ['codex'], status: 'A', mtime: 3, added: 5, removed: 0},
      ],
    });
    const expectedTree = new TestElement('expected-incremental-tree');
    expectedTree.setAttribute('role', 'tree');
    expectedTree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(expectedTree, '/repo', [{name: 'indexed', kind: 'dir'}], 0, indexedEntriesByDir);
    const expectedRows = Object.fromEntries(expectedTree.querySelectorAll('.file-tree-row[data-path]').map(row => [row.dataset.path, row]));
    api.updateFileTreeGitStatusRowsForTest([incrementalRows['/repo/indexed'], incrementalRows['/repo/indexed/a.py']]);
    assert.deepStrictEqual(derivedSnapshot(incrementalRows['/repo/indexed']), derivedSnapshot(expectedRows['/repo/indexed']), 'lightweight Finder refresh matches full render for directory status/title/agent/count/name state');
    assert.deepStrictEqual(derivedSnapshot(incrementalRows['/repo/indexed/a.py']), derivedSnapshot(expectedRows['/repo/indexed/a.py']), 'lightweight Finder refresh matches full render for changed-file status/title/agent/name state');

    api.setFileExplorerSessionFilesPayloadForTest({loaded: true, repos: [], files: []});
    const symlinkTree = new TestElement('symlink-tree');
    symlinkTree.setAttribute('role', 'tree');
    symlinkTree.classList.add('file-explorer-tree-panel');
    api.renderTreeChildrenForTest(symlinkTree, '/repo', [
      {name: 'utils', kind: 'dir', is_repo: true, is_symlink: true, symlink_target: '/home/test/utils', repo: {root: '/repo/utils', name: 'utils', branch: 'main'}},
    ]);
    const symlinkRow = symlinkTree.children[0];
    const symlinkName = symlinkRow.querySelector(':scope > .file-tree-name');
    assert.equal(symlinkName.textContent, 'utils [main] → /home/test/utils', 'symlinked repo rows initially show branch and target');
    api.updateFileTreeGitStatusRowsForTest([symlinkRow]);
    assert.equal(symlinkName.textContent, 'utils [main] → /home/test/utils', 'lightweight Finder status refresh preserves symlink target on repo rows');

    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.leafNode('slot2');
    slots.slot2 = api.paneStateWithTabs(['__files__'], '__files__');
    const next = api.layoutWithItems(slots, ['1']);
    assert.deepStrictEqual(canonical(api.serialize(next).panes), {
      slot2: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: ['1'], active: '1'},
    });
    assert.equal(next[api.layoutTreeKey].split, 'row');
    assert.equal(next[api.layoutTreeKey].pct, 22);

    const placeholderSlots = api.emptyLayoutSlots();
    placeholderSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    placeholderSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    placeholderSlots.slot1 = api.emptyPlaceholderPaneState();
    api.setLayoutSlotsForTest(placeholderSlots);
    assert.equal(api.firstEmptyPane(), 'slot1');
    assert.equal(api.slotForTabActivation('1'), 'slot1');
    const filled = api.layoutWithItems(placeholderSlots, ['1']);
    assert.deepStrictEqual(canonical(api.serialize(filled).panes), {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: ['1'], active: '1'},
    });
    const placeholderUrl = api.setLayoutSlotsForTest(placeholderSlots);
    const placeholderParams = parseUrl(placeholderUrl);
    assert.equal(placeholderParams.get('layout'), 'row@22(left,slot1)');
    assert.equal(placeholderParams.get('tabs'), 'left:files;slot1:__empty_pane__');
    const reloadedPlaceholder = loadYolomux(`?${placeholderUrl.split('?')[1] || ''}`, ['1']);
    assert.deepStrictEqual(canonical(reloadedPlaceholder.serialize(reloadedPlaceholder.currentSlots())), canonical(api.serialize(placeholderSlots)));

    const finderAndTmux = api.emptyLayoutSlots();
    finderAndTmux[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    finderAndTmux.left = api.paneStateWithTabs(['__files__'], '__files__');
    finderAndTmux.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(finderAndTmux);
    assert.equal(api.slotForNewTmuxSession('2'), 'slot1');
    assert.equal(api.slotForTabActivation('2'), 'slot1');
    api.removePaneFromLayout('1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: [], active: null, placeholder: true},
    });
    const normalSplit = api.emptyLayoutSlots();
    normalSplit[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    normalSplit.left = api.paneStateWithTabs(['1'], '1');
    const extraPaneItem = api.registerFileEditorLayoutItem('/home/test/a.md');
    normalSplit.slot1 = api.paneStateWithTabs(['2', extraPaneItem], '2');
    api.rememberFileExplorerOpenIntentForTest(false);
    api.setLayoutSlotsForTest(normalSplit);
    api.removePaneFromLayout('2');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
      left: {tabs: ['1'], active: '1'},
    });
    api.rememberFileExplorerOpenIntentForTest(true);

    const tmuxAndFinder = api.emptyLayoutSlots();
    tmuxAndFinder[api.layoutTreeKey] = api.splitNode('row', api.leafNode('slot1'), api.leafNode('left'), 78);
    tmuxAndFinder.slot1 = api.paneStateWithTabs(['1'], '1');
    tmuxAndFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
    api.setLayoutSlotsForTest(tmuxAndFinder);
    api.removeSessionFromLayout('1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).panes), {
      slot1: {tabs: [], active: null, placeholder: true},
      left: {tabs: ['__files__'], active: '__files__'},
    });

    const tmuxAboveFinder = api.emptyLayoutSlots();
    tmuxAboveFinder[api.layoutTreeKey] = api.splitNode('column', api.leafNode('slot1'), api.leafNode('left'), 48);
    tmuxAboveFinder.slot1 = api.paneStateWithTabs(['1'], '1');
    tmuxAboveFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
    api.setLayoutSlotsForTest(tmuxAboveFinder);
    api.removeSessionFromLayout('1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: [], active: null, placeholder: true},
      },
    });

    const finderAboveTmux = api.emptyLayoutSlots();
    finderAboveTmux[api.layoutTreeKey] = api.splitNode('column', api.leafNode('left'), api.leafNode('slot1'), 52);
    finderAboveTmux.left = api.paneStateWithTabs(['__files__'], '__files__');
    finderAboveTmux.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(finderAboveTmux);
    api.removePaneFromLayout('1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: [], active: null, placeholder: true},
      },
    });

    const activationSlots = api.emptyLayoutSlots();
    activationSlots[api.layoutTreeKey] = api.splitNode(
      'row',
      api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
      api.leafNode('slot1'),
      28,
    );
    activationSlots.slot2 = api.emptyPlaceholderPaneState();
    activationSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    activationSlots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(activationSlots);
    assert.equal(api.slotForTabActivation('2'), 'slot1');

    const middleFinderSlots = api.emptyLayoutSlots();
    middleFinderSlots[api.layoutTreeKey] = api.splitNode(
      'row',
      api.leafNode('left'),
      api.splitNode('row', api.leafNode('slot2'), api.leafNode('slot1'), 22),
      35,
    );
    middleFinderSlots.left = api.paneStateWithTabs(['1'], '1');
    middleFinderSlots.slot2 = api.paneStateWithTabs(['__files__'], '__files__');
    middleFinderSlots.slot1 = api.paneStateWithTabs(['2'], '2');
    assert.equal(api.fileExplorerNeedsLeftDock(middleFinderSlots), true);
    const dockedFinder = api.normalizeLayoutSlots(middleFinderSlots);
    assert.deepStrictEqual(canonical(api.serialize(dockedFinder)), {
      tree: {
        split: 'row',
        pct: 22,
        children: [
          {slot: 'slot2'},
          {split: 'row', pct: 35, children: [{slot: 'left'}, {slot: 'slot1'}]},
        ],
      },
      panes: {
        slot2: {tabs: ['__files__'], active: '__files__'},
        left: {tabs: ['1'], active: '1'},
        slot1: {tabs: ['2'], active: '2'},
      },
    });
    api.setLayoutSlotsForTest(dockedFinder);
    assert.equal(api.fileExplorerNeedsLeftDock(), false);

    const verticalFinderBranch = api.emptyLayoutSlots();
    verticalFinderBranch[api.layoutTreeKey] = api.splitNode(
      'row',
      api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
      api.leafNode('slot1'),
      22,
    );
    verticalFinderBranch.slot2 = api.paneStateWithTabs(['__info__'], '__info__');
    verticalFinderBranch.left = api.paneStateWithTabs(['__files__'], '__files__');
    verticalFinderBranch.slot1 = api.paneStateWithTabs(['1'], '1');
    assert.equal(api.fileExplorerNeedsLeftDock(verticalFinderBranch), false);
    api.setLayoutSlotsForTest(verticalFinderBranch);
    assert.equal(api.fileExplorerNeedsLeftDock(), false);

    const noFinderSplit = api.emptyLayoutSlots();
    noFinderSplit[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    noFinderSplit.left = api.paneStateWithTabs(['1'], '1');
    noFinderSplit.slot1 = api.paneStateWithTabs(['2'], '2');
    api.rememberFileExplorerOpenIntentForTest(true);
    assert.equal(api.itemInLayout('__files__', api.normalizeLayoutSlots(noFinderSplit)), true, 'normalization re-docks a missing Finder when it was not explicitly closed');
    api.rememberFileExplorerOpenIntentForTest(false);
    assert.equal(api.itemInLayout('__files__', api.normalizeLayoutSlots(noFinderSplit)), false, 'normalization keeps Finder hidden after an explicit per-tab close');
    api.rememberFileExplorerOpenIntentForTest(true);
    api.setLayoutSlotsForTest(noFinderSplit);
    assert.deepStrictEqual(canonical(api.serialize(api.layoutWithFileExplorerDockedLeft())), {
      tree: {
        split: 'row',
        pct: 22,
        children: [
          {slot: 'slot2'},
          {split: 'row', pct: 50, children: [{slot: 'left'}, {slot: 'slot1'}]},
        ],
      },
      panes: {
        slot2: {tabs: ['__files__'], active: '__files__'},
        left: {tabs: ['1'], active: '1'},
        slot1: {tabs: ['2'], active: '2'},
      },
    });

    const dockviewPrevious = api.emptyLayoutSlots();
    dockviewPrevious[api.layoutTreeKey] = api.splitNode('row', api.leafNode('slot2'), api.leafNode('left'), 22);
    dockviewPrevious.slot2 = api.paneStateWithTabs(['__files__'], '__files__');
    dockviewPrevious.left = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(dockviewPrevious);
    const dockviewNextWithoutFinder = api.emptyLayoutSlots();
    dockviewNextWithoutFinder[api.layoutTreeKey] = api.leafNode('left');
    dockviewNextWithoutFinder.left = api.paneStateWithTabs(['1'], '1');
    api.adoptDockviewLayoutForTest(api.dockviewJsonFromLayoutSlots(dockviewNextWithoutFinder), {width: 0, height: 0});
    assert.deepStrictEqual(
      canonical(api.serialize(api.currentSlots())),
      canonical(api.serialize(dockviewPrevious)),
      'Dockview adoption ignores 0-area host measurements so sleep/hidden-tab layouts cannot drop Finder',
    );
    api.adoptDockviewLayoutForTest(api.dockviewJsonFromLayoutSlots(dockviewNextWithoutFinder));
    assert.equal(api.itemInLayout('__files__'), true, 'Dockview adoption re-docks Finder when a non-user commit drops it');
    assert.equal(api.slotForSession('__files__'), 'slot2', 'Dockview adoption preserves the previous Finder slot when possible');

    const transientDockviewFinder = api.emptyLayoutSlots();
    transientDockviewFinder[api.layoutTreeKey] = api.splitNode(
      'row',
      api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
      api.leafNode('slot1'),
      35,
    );
    transientDockviewFinder.slot2 = api.paneStateWithTabs(['2'], '2');
    transientDockviewFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
    transientDockviewFinder.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(transientDockviewFinder);
    const transientDockviewJson = api.dockviewJsonFromLayoutSlots(api.currentSlots());
    const clearDockviewLeafViews = (node, id) => {
      if (!node) return false;
      if (node.type === 'leaf' && node.data?.id === id) {
        node.data.views = [];
        node.data.activeView = null;
        return true;
      }
      return (Array.isArray(node.data) ? node.data : []).some(child => clearDockviewLeafViews(child, id));
    };
    assert.equal(clearDockviewLeafViews(transientDockviewJson.grid.root, 'left'), true, 'test fixture clears the previous Finder Dockview group');
    api.adoptDockviewLayoutForTest(transientDockviewJson);
    assert.deepStrictEqual(
      canonical(api.serialize(api.currentSlots())),
      canonical(api.serialize(api.normalizeLayoutSlots(transientDockviewFinder))),
      'Dockview adoption restores a transiently empty Finder/Differ group in its previous tree position',
    );

    const expandedNormal = api.emptyLayoutSlots();
    expandedNormal[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    expandedNormal.left = api.paneStateWithTabs(['1'], '1');
    expandedNormal.slot1 = api.paneStateWithTabs(['2'], '2');
    api.rememberFileExplorerOpenIntentForTest(false);
    api.setLayoutSlotsForTest(expandedNormal);
    api.expandPaneFromLayout('2');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {slot: 'slot1'},
      panes: {slot1: {tabs: ['2', '1'], active: '2'}},
    });
    api.rememberFileExplorerOpenIntentForTest(true);

    const expandedBesideFinder = api.emptyLayoutSlots();
    expandedBesideFinder[api.layoutTreeKey] = api.splitNode(
      'row',
      api.leafNode('left'),
      api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 50),
      22,
    );
    expandedBesideFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
    expandedBesideFinder.slot1 = api.paneStateWithTabs(['1'], '1');
    expandedBesideFinder.slot2 = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(expandedBesideFinder);
    api.expandPaneFromLayout('2');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: ['2', '1'], active: '2'},
      },
    });

    const layoutCommands = api.emptyLayoutSlots();
    layoutCommands[api.layoutTreeKey] = api.splitNode(
      'row',
      api.leafNode('left'),
      api.splitNode('row', api.leafNode('slot1'), api.leafNode('slot2'), 50),
      22,
    );
    layoutCommands.left = api.paneStateWithTabs(['__files__'], '__files__');
    layoutCommands.slot1 = api.paneStateWithTabs(['1'], '1');
    layoutCommands.slot2 = api.paneStateWithTabs(['2'], '2');
    api.setLayoutSlotsForTest(layoutCommands);
    api.setFocusedPanelItem('2');
    api.setLayoutToSinglePane();
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: ['1', '2'], active: '2'},
      },
    });
    api.setLayoutToSplitPanes();
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {split: 'row', pct: 50, children: [{slot: 'slot1'}, {slot: 'right'}]}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: ['1'], active: '1'},
        right: {tabs: ['2'], active: '2'},
      },
    });

    const layoutModeSource = api.emptyLayoutSlots();
    const extraWallItem = api.registerFileEditorLayoutItem('/home/test/b.md');
    layoutModeSource[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    layoutModeSource.left = api.paneStateWithTabs(['__files__'], '__files__');
    layoutModeSource.slot1 = api.paneStateWithTabs(['1', '2', extraPaneItem, extraWallItem], extraPaneItem);
    api.setLayoutSlotsForTest(layoutModeSource);
    api.setFocusedPanelItem(extraPaneItem);
    api.setLayoutToGridPanes();
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {
        split: 'row',
        pct: 22,
        children: [
          {slot: 'left'},
          {
            split: 'row',
            pct: 50,
            children: [
              {split: 'column', pct: 50, children: [{slot: 'leftTop'}, {slot: 'leftBottom'}]},
              {split: 'column', pct: 50, children: [{slot: 'rightTop'}, {slot: 'rightBottom'}]},
            ],
          },
        ],
      },
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        leftTop: {tabs: ['1'], active: '1'},
        rightTop: {tabs: ['2'], active: '2'},
        leftBottom: {tabs: [extraPaneItem], active: extraPaneItem},
        rightBottom: {tabs: [extraWallItem], active: extraWallItem},
      },
    });
    api.setLayoutSlotsForTest(layoutModeSource);
    api.setFocusedPanelItem(extraPaneItem);
    api.setLayoutToWallPanes();
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {
        split: 'row',
        pct: 22,
        children: [
          {slot: 'left'},
          {
            split: 'row',
            pct: 50,
            children: [
              {
                split: 'row',
                pct: 50,
                children: [
                  {split: 'row', pct: 50, children: [{slot: 'right'}, {slot: 'slot1'}]},
                  {slot: 'slot2'},
                ],
              },
              {slot: 'slot3'},
            ],
          },
        ],
      },
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        right: {tabs: ['1'], active: '1'},
        slot1: {tabs: ['2'], active: '2'},
        slot2: {tabs: [extraPaneItem], active: extraPaneItem},
        slot3: {tabs: [extraWallItem], active: extraWallItem},
      },
    });

    const singlePaneBesideFinder = api.emptyLayoutSlots();
    singlePaneBesideFinder[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    singlePaneBesideFinder.left = api.paneStateWithTabs(['__files__'], '__files__');
    singlePaneBesideFinder.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(singlePaneBesideFinder);
    assert.equal(api.canPaneExpand('1'), false);
    assert.ok(api.panelControlsHtml('1').includes('data-pane-expand'));
    assert.ok(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));
    api.expandPaneFromLayout('1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: ['1'], active: '1'},
      },
    });

    const placeholderBesideSinglePane = api.emptyLayoutSlots();
    placeholderBesideSinglePane[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 40);
    placeholderBesideSinglePane.left = api.paneStateWithTabs(['1'], '1');
    placeholderBesideSinglePane.slot1 = api.emptyPlaceholderPaneState();
    api.rememberFileExplorerOpenIntentForTest(false);
    api.setLayoutSlotsForTest(placeholderBesideSinglePane);
    assert.equal(api.canPaneExpand('1'), false);
    assert.ok(api.panelControlsHtml('1').includes('hidden type="button" data-pane-expand="1"'));
    api.expandPaneFromLayout('1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {slot: 'left'},
      panes: {
        left: {tabs: ['1'], active: '1'},
      },
    });

    const minimizedNormal = api.emptyLayoutSlots();
    minimizedNormal[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 50);
    minimizedNormal.left = api.paneStateWithTabs(['1'], '1');
    minimizedNormal.slot1 = api.paneStateWithTabs(['2', extraPaneItem], '2');
    api.setLayoutSlotsForTest(minimizedNormal);
    api.minimizePaneFromLayout('2');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {slot: 'left'},
      panes: {left: {tabs: ['1', '2', extraPaneItem], active: '1'}},
    });
    api.rememberFileExplorerOpenIntentForTest(true);

    const finderOnly = api.emptyLayoutSlots();
    finderOnly[api.layoutTreeKey] = api.leafNode('left');
    finderOnly.left = api.paneStateWithTabs(['__files__'], '__files__');
    api.setLayoutSlotsForTest(finderOnly);
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot1'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: [], active: null, placeholder: true},
      },
    });
    assert.equal(api.slotForNewTmuxSession('2'), 'slot1');

    const dragSlots = api.emptyLayoutSlots();
    dragSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    dragSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    dragSlots.slot1 = api.paneStateWithTabs(['__info__'], '__info__');
    api.setLayoutSlotsForTest(dragSlots);
    const removed = api.layoutWithoutItem('__info__', {preserveEmptySlot: 'slot1'});
    assert.deepStrictEqual(canonical(api.serialize(removed).panes.slot1), {tabs: [], active: null, placeholder: true});
    const closed = api.normalizeLayoutSlots(api.layoutWithoutItem('__info__', {preserveRemovedSlot: true}));
    assert.deepStrictEqual(canonical(api.serialize(closed).panes), {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: [], active: null, placeholder: true},
    });
    const moved = api.normalizeLayoutSlots(api.layoutWithoutItem('__info__'));
    assert.deepStrictEqual(canonical(api.serialize(moved).panes), {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: [], active: null, placeholder: true},
    });
    api.splitSessionAtSlot('__info__', 'left', 'top', 'slot1');
    const split = api.serialize(api.currentSlots());
    assert.deepStrictEqual(canonical(Object.values(split.panes).filter(pane => pane.tabs.includes('__files__'))), [{tabs: ['__files__'], active: '__files__'}]);
    assert.equal(split.panes.slot1, undefined);
    assert.deepStrictEqual(canonical(Object.values(split.panes).filter(pane => pane.tabs.includes('__info__'))), [{tabs: ['__info__'], active: '__info__'}]);
    const infoSlot = Object.entries(split.panes).find(([, pane]) => pane.tabs.includes('__info__'))[0];
    assert.notEqual(infoSlot, 'slot1');
    assert.equal(api.shouldPreserveSourceSlotForSplit(infoSlot, 'slot1'), false);

    const finderCloseSlots = api.emptyLayoutSlots();
    finderCloseSlots[api.layoutTreeKey] = api.splitNode(
      'row',
      api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50),
      api.leafNode('slot1'),
      22,
    );
    finderCloseSlots.slot2 = api.paneStateWithTabs(['__info__'], '__info__');
    finderCloseSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    finderCloseSlots.slot1 = api.emptyPlaceholderPaneState();
    api.setLayoutSlotsForTest(finderCloseSlots);
    api.removeSessionFromLayout('__files__');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {slot: 'slot2'},
      panes: {slot2: {tabs: ['__info__'], active: '__info__'}},
    });

    const autoPruneSlots = api.emptyLayoutSlots();
    autoPruneSlots[api.layoutTreeKey] = api.splitNode('column', api.leafNode('slot2'), api.leafNode('left'), 50);
    autoPruneSlots.slot2 = api.paneStateWithTabs(['1'], '1');
    autoPruneSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    const topColumn = new TestElement('top-column');
    topColumn.dataset.slot = 'slot2';
    topColumn.rect = {left: 0, top: 0, right: 500, bottom: 120, width: 500, height: 120};
    const finderColumn = new TestElement('finder-column');
    finderColumn.dataset.slot = 'left';
    finderColumn.rect = {left: 0, top: 126, right: 500, bottom: 250, width: 500, height: 124};
    api.setLayoutSlotsForTest(autoPruneSlots);
    api.setGridPreviewNodesForTest([topColumn, finderColumn]);
    assert.equal(api.smallLayoutSlotCandidate().slot, 'slot2');

    autoPruneSlots.slot2 = api.paneStateWithTabs(['__files__', '1'], '1');
    api.setLayoutSlotsForTest(autoPruneSlots);
    api.setGridPreviewNodesForTest([topColumn, finderColumn]);
    assert.equal(api.slotCanAutoPrune('slot2'), false, 'legacy auto-prune protects a slot when Finder is a background tab');
    assert.equal(api.smallLayoutSlotCandidate(), null, 'legacy auto-prune does not remove a small slot containing a background Finder tab');

    autoPruneSlots.slot2 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(autoPruneSlots);
    topColumn.rect = {left: 0, top: 0, right: 500, bottom: 260, width: 500, height: 260};
    api.setGridPreviewNodesForTest([topColumn, finderColumn]);
    assert.equal(api.smallLayoutSlotCandidate(), null);

    const allEmpty = api.emptyLayoutSlots();
    allEmpty[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 40);
    allEmpty.left = api.emptyPlaceholderPaneState();
    allEmpty.slot1 = api.emptyPlaceholderPaneState();
    assert.deepStrictEqual(canonical(api.serialize(api.normalizeLayoutSlots(allEmpty))), {
      tree: {slot: 'left'},
      panes: {left: {tabs: [], active: null, placeholder: true}},
    });

    const killedApi = loadYolomux('', ['2']);
    const killedSlots = killedApi.emptyLayoutSlots();
    killedSlots[killedApi.layoutTreeKey] = killedApi.splitNode('row', killedApi.leafNode('left'), killedApi.leafNode('slot1'), 22);
    killedSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
    killedSlots.slot1 = {tabs: ['1'], active: '1'};
    const killedNormalized = killedApi.normalizeLayoutSlots(killedSlots, {
      preserveRemovedItems: ['1'],
      preserveRemovedSlots: true,
    });
    assert.deepStrictEqual(canonical(killedApi.serialize(killedNormalized).panes), {
      left: {tabs: ['__files__'], active: '__files__'},
      slot1: {tabs: [], active: null, placeholder: true},
    });
    const killedNestedFinderSlots = killedApi.emptyLayoutSlots();
    killedNestedFinderSlots[killedApi.layoutTreeKey] = killedApi.splitNode(
      'row',
      killedApi.leafNode('slot1'),
      killedApi.splitNode('row', killedApi.leafNode('left'), killedApi.leafNode('slot2'), 22),
      58,
    );
    killedNestedFinderSlots.slot1 = {tabs: ['1'], active: '1'};
    killedNestedFinderSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
    killedNestedFinderSlots.slot2 = killedApi.paneStateWithTabs(['2'], '2');
    const killedNestedFinderNormalized = killedApi.normalizeLayoutSlots(killedNestedFinderSlots, {
      preserveRemovedItems: ['1'],
      preserveRemovedSlots: true,
    });
    assert.deepStrictEqual(canonical(killedApi.serialize(killedNestedFinderNormalized)), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: ['2'], active: '2'},
      },
    });
    const staleFileOpenPath = '/home/test/AGENTS.md';
    const staleFileOpenItem = killedApi.registerFileEditorLayoutItem(staleFileOpenPath);
    const staleFileOpenSlots = killedApi.emptyLayoutSlots();
    staleFileOpenSlots[killedApi.layoutTreeKey] = killedApi.splitNode(
      'row',
      killedApi.leafNode('slot1'),
      killedApi.splitNode('row', killedApi.leafNode('left'), killedApi.leafNode('slot2'), 22),
      58,
    );
    staleFileOpenSlots.slot1 = killedApi.emptyPlaceholderPaneState();
    staleFileOpenSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
    staleFileOpenSlots.slot2 = killedApi.paneStateWithTabs([staleFileOpenItem], staleFileOpenItem);
    killedApi.setLayoutSlotsForTest(staleFileOpenSlots);
    killedApi.openFileEditorPane(staleFileOpenPath);
    assert.deepStrictEqual(canonical(killedApi.serialize(killedApi.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: [staleFileOpenItem], active: staleFileOpenItem},
      },
    });

    const killedVerticalSlots = killedApi.emptyLayoutSlots();
    killedVerticalSlots[killedApi.layoutTreeKey] = killedApi.splitNode('column', killedApi.leafNode('slot1'), killedApi.leafNode('left'), 50);
    killedVerticalSlots.slot1 = {tabs: ['1'], active: '1'};
    killedVerticalSlots.left = killedApi.paneStateWithTabs(['__files__'], '__files__');
    const killedVerticalNormalized = killedApi.normalizeLayoutSlots(killedVerticalSlots, {
      preserveRemovedItems: ['1'],
      preserveRemovedSlots: true,
    });
    assert.deepStrictEqual(canonical(killedApi.serialize(killedVerticalNormalized)), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: [], active: null, placeholder: true},
      },
    });

    api.setLayoutSlotsForTest(finderOnly);
    const roomyFinderDropRect = {left: 0, top: 0, right: 720, bottom: 520, width: 720, height: 520};
    const narrowFinderDropRect = {left: 0, top: 0, right: 300, bottom: 520, width: 300, height: 520};
    const shortFinderDropRect = {left: 0, top: 0, right: 720, bottom: 420, width: 720, height: 420};
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'middle', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'left', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'right', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'top', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom', targetRect: roomyFinderDropRect}), true);
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom', targetRect: narrowFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('1', {targetSlot: 'left', zone: 'bottom', targetRect: shortFinderDropRect}), false);
    const editorItem = api.registerFileEditorLayoutItem('/home/test/AGENTS.md');
    assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'middle', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'left', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'right', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'top', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession(editorItem, {targetSlot: 'left', zone: 'bottom', targetRect: roomyFinderDropRect}), true);
    const legacyChangesOnly = api.emptyLayoutSlots();
    legacyChangesOnly[api.layoutTreeKey] = api.leafNode('left');
    legacyChangesOnly.left = api.paneStateWithTabs(['__changes__'], '__changes__');
    api.setLayoutSlotsForTest(legacyChangesOnly);
    assert.equal(api.itemInLayout('__changes__'), false, 'retired standalone Differ is pruned if it appears outside URL alias resolution');
    api.setLayoutSlotsForTest(finderOnly);
    assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'top', targetRect: roomyFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: narrowFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: roomyFinderDropRect}), true);
    assert.equal(api.dropIntentAllowsSession('__prefs__', {targetSlot: 'left', zone: 'bottom', targetRect: shortFinderDropRect}), false);
    assert.equal(api.dropIntentAllowsSession('__files__', {targetSlot: 'slot1', zone: 'left'}), false, 'Finder pane drags never advertise a pane split preview');
    assert.equal(api.dropIntentAllowsSession('__changes__', {targetSlot: 'slot1', zone: 'left'}), false, 'retired standalone Differ is not draggable as a layout item');
    assert.equal(api.dropIntentAllowsSession('__files__', {boundary: 'root', zone: 'right', targetSlot: 'slot1'}), false, 'Finder pane drags never advertise a root split preview');
    assert.equal(api.dropIntentAllowsSession('__changes__', {boundary: 'gutter', zone: 'right', targetSlot: 'slot1'}), false, 'retired standalone Differ cannot be dropped at a gutter');
    const normalSplitSlots = api.emptyLayoutSlots();
    normalSplitSlots[api.layoutTreeKey] = api.leafNode('slot1');
    normalSplitSlots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(normalSplitSlots);
    assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'right', targetRect: {width: 620, height: 520}}), false, 'pane edge previews require enough width for both panes');
    assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'right', targetRect: {width: 720, height: 520}}), true);
    assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'bottom', targetRect: {width: 720, height: 420}}), false, 'pane edge previews require enough height for both panes');
    assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'middle', targetRect: {width: 300, height: 520}}), false, 'middle drops do not preview when the target cannot display the incoming tab');
    assert.equal(api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'middle', targetRect: {width: 420, height: 520}}), true);

    const dragMatrixSlots = api.emptyLayoutSlots();
    dragMatrixSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 25);
    dragMatrixSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    dragMatrixSlots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(dragMatrixSlots);
    const matrixNormalRect = {left: 260, top: 0, right: 980, bottom: 520, width: 720, height: 520};
    const matrixFinderRect = {left: 0, top: 0, right: 720, bottom: 520, width: 720, height: 520};
    const filePayload = {path: '/repo/app/README.md', paths: ['/repo/app/README.md'], kind: 'file'};
    const multiFilePayload = {path: '/repo/app/a.md', paths: ['/repo/app/a.md', '/repo/app/b.md'], kind: 'file'};
    const directoryPayload = {path: '/repo/app/src', paths: ['/repo/app/src'], kind: 'dir'};
    const matrixCases = [
      {source: 'tab', target: 'normal-pane', zone: 'middle', previewOwner: 'tab-strip', dropEffect: 'move', finalAction: 'move-tab-to-pane', allowed: api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'middle', targetRect: matrixNormalRect})},
      {source: 'tab', target: 'normal-pane', zone: 'right', previewOwner: 'pane', dropEffect: 'move', finalAction: 'split-pane', allowed: api.dropIntentAllowsSession('2', {targetSlot: 'slot1', zone: 'right', targetRect: matrixNormalRect})},
      {source: 'tab', target: 'Finder/Differ', zone: 'middle', previewOwner: 'none', dropEffect: 'none', finalAction: 'none', allowed: api.dropIntentAllowsSession('2', {targetSlot: 'left', zone: 'middle', targetRect: matrixFinderRect})},
      {source: 'tab', target: 'Finder/Differ', zone: 'bottom', previewOwner: 'pane', dropEffect: 'move', finalAction: 'split-reserved-pane-bottom', allowed: api.dropIntentAllowsSession('2', {targetSlot: 'left', zone: 'bottom', targetRect: matrixFinderRect})},
      {source: 'Finder/Differ-tab', target: 'normal-pane', zone: 'right', previewOwner: 'none', dropEffect: 'none', finalAction: 'none', allowed: api.dropIntentAllowsSession('__files__', {targetSlot: 'slot1', zone: 'right', targetRect: matrixNormalRect})},
      {source: 'file-row', target: 'normal-pane', zone: 'left', previewOwner: 'pane', dropEffect: 'copy', finalAction: 'open-file-editor-split', allowed: api.fileDropIntentAllowsPayload(filePayload, {targetSlot: 'slot1', zone: 'left', targetRect: matrixNormalRect})},
      {source: 'multi-file-row', target: 'normal-pane', zone: 'middle', previewOwner: 'pane', dropEffect: 'copy', finalAction: 'open-files-in-pane', allowed: api.fileDropIntentAllowsPayload(multiFilePayload, {targetSlot: 'slot1', zone: 'middle', targetRect: matrixNormalRect})},
      {source: 'directory-row', target: 'terminal-path-target', zone: 'left', previewOwner: 'pane', dropEffect: 'copy', finalAction: 'insert-directory-path-or-split', allowed: api.pathDropIntentAllowsPayload(directoryPayload, {targetSlot: 'slot1', zone: 'left', targetRect: matrixNormalRect})},
      {source: 'directory-row', target: 'file-editor-drop', zone: 'left', previewOwner: 'none', dropEffect: 'none', finalAction: 'none', allowed: api.fileDropIntentAllowsPayload(directoryPayload, {targetSlot: 'slot1', zone: 'left', targetRect: matrixNormalRect})},
      {source: 'tab', target: 'root-edge', zone: 'right', previewOwner: 'root', dropEffect: 'move', finalAction: 'split-root', allowed: api.dropIntentAllowsSession('2', {boundary: 'root', zone: 'right', targetSlot: 'slot1', targetRect: matrixNormalRect})},
      {source: 'tab', target: 'cross-gutter', zone: 'right', previewOwner: 'gutter', dropEffect: 'move', finalAction: 'split-gutter', allowed: api.dropIntentAllowsSession('2', {boundary: 'gutter', zone: 'right', targetSlot: 'slot1', targetRect: matrixNormalRect})},
    ];
    assert.deepStrictEqual(canonical(matrixCases), [
      {source: 'tab', target: 'normal-pane', zone: 'middle', previewOwner: 'tab-strip', dropEffect: 'move', finalAction: 'move-tab-to-pane', allowed: true},
      {source: 'tab', target: 'normal-pane', zone: 'right', previewOwner: 'pane', dropEffect: 'move', finalAction: 'split-pane', allowed: true},
      {source: 'tab', target: 'Finder/Differ', zone: 'middle', previewOwner: 'none', dropEffect: 'none', finalAction: 'none', allowed: false},
      {source: 'tab', target: 'Finder/Differ', zone: 'bottom', previewOwner: 'pane', dropEffect: 'move', finalAction: 'split-reserved-pane-bottom', allowed: true},
      {source: 'Finder/Differ-tab', target: 'normal-pane', zone: 'right', previewOwner: 'none', dropEffect: 'none', finalAction: 'none', allowed: false},
      {source: 'file-row', target: 'normal-pane', zone: 'left', previewOwner: 'pane', dropEffect: 'copy', finalAction: 'open-file-editor-split', allowed: true},
      {source: 'multi-file-row', target: 'normal-pane', zone: 'middle', previewOwner: 'pane', dropEffect: 'copy', finalAction: 'open-files-in-pane', allowed: true},
      {source: 'directory-row', target: 'terminal-path-target', zone: 'left', previewOwner: 'pane', dropEffect: 'copy', finalAction: 'insert-directory-path-or-split', allowed: true},
      {source: 'directory-row', target: 'file-editor-drop', zone: 'left', previewOwner: 'none', dropEffect: 'none', finalAction: 'none', allowed: false},
      {source: 'tab', target: 'root-edge', zone: 'right', previewOwner: 'root', dropEffect: 'move', finalAction: 'split-root', allowed: true},
      {source: 'tab', target: 'cross-gutter', zone: 'right', previewOwner: 'gutter', dropEffect: 'move', finalAction: 'split-gutter', allowed: true},
    ], 'drag/drop matrix covers source x target x zone through the shared validators');
    api.setLayoutSlotsForTest(finderOnly);
    api.splitSessionAtSlot(editorItem, 'left', 'bottom');
    const editorSplit = api.serialize(api.currentSlots());
    assert.deepStrictEqual(canonical(Object.values(editorSplit.panes).filter(pane => pane.tabs.includes('__files__'))), [{tabs: ['__files__'], active: '__files__'}]);
    assert.deepStrictEqual(canonical(Object.values(editorSplit.panes).filter(pane => pane.tabs.includes(editorItem))), [{tabs: [editorItem], active: editorItem}]);
    assert.ok(JSON.stringify(editorSplit.tree).includes('"split":"column"'));

    const fullSpanA = api.registerFileEditorLayoutItem('/home/test/full-span-a.md');
    const fullSpanB = api.registerFileEditorLayoutItem('/home/test/full-span-b.md');
    const fullSpanC = api.registerFileEditorLayoutItem('/home/test/full-span-c.md');
    const fullSpanD = api.registerFileEditorLayoutItem('/home/test/full-span-d.md');
    const fullSpanSlots = api.emptyLayoutSlots();
    fullSpanSlots[api.layoutTreeKey] = api.splitNode(
      'row',
      api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 50),
      api.splitNode('column', api.leafNode('slot3'), api.leafNode('slot4'), 50),
      50,
    );
    fullSpanSlots.slot1 = api.paneStateWithTabs([fullSpanA], fullSpanA);
    fullSpanSlots.slot2 = api.paneStateWithTabs([fullSpanB], fullSpanB);
    fullSpanSlots.slot3 = api.paneStateWithTabs([fullSpanC], fullSpanC);
    fullSpanSlots.slot4 = api.paneStateWithTabs([fullSpanD], fullSpanD);
    api.setLayoutSlotsForTest(fullSpanSlots);
    api.splitSessionAtLayoutBoundary('__info__', 'right');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {
        split: 'row',
        pct: 50,
        children: [
          {
            split: 'row',
            pct: 50,
            children: [
              {split: 'column', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]},
              {split: 'column', pct: 50, children: [{slot: 'slot3'}, {slot: 'slot4'}]},
            ],
          },
          {slot: 'slot5'},
        ],
      },
      panes: {
        slot1: {tabs: [fullSpanA], active: fullSpanA},
        slot2: {tabs: [fullSpanB], active: fullSpanB},
        slot3: {tabs: [fullSpanC], active: fullSpanC},
        slot4: {tabs: [fullSpanD], active: fullSpanD},
        slot5: {tabs: ['__info__'], active: '__info__'},
      },
    });

    api.setLayoutSlotsForTest(fullSpanSlots);
    api.splitSessionAtGutter('__prefs__', '', 'right');
    const gutterSplit = api.serialize(api.currentSlots());
    assert.equal(gutterSplit.tree.split, 'row');
    assert.deepStrictEqual(canonical(gutterSplit.tree.children[0]), {
      split: 'column',
      pct: 50,
      children: [{slot: 'slot1'}, {slot: 'slot2'}],
    });
    assert.deepStrictEqual(canonical(gutterSplit.tree.children[1]), {
      split: 'row',
      pct: 50,
      children: [
        {slot: 'slot5'},
        {split: 'column', pct: 50, children: [{slot: 'slot3'}, {slot: 'slot4'}]},
      ],
    });
    assert.deepStrictEqual(canonical(gutterSplit.panes.slot5), {tabs: ['__prefs__'], active: '__prefs__'});

    const dockedBoundarySlots = api.emptyLayoutSlots();
    dockedBoundarySlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    dockedBoundarySlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    dockedBoundarySlots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(dockedBoundarySlots);
    api.splitSessionAtLayoutBoundary('__info__', 'bottom');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {
        split: 'row',
        pct: 22,
        children: [
          {slot: 'left'},
          {split: 'column', pct: 50, children: [{slot: 'slot1'}, {slot: 'slot2'}]},
        ],
      },
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot1: {tabs: ['1'], active: '1'},
        slot2: {tabs: ['__info__'], active: '__info__'},
      },
    });

    const dockedBoundaryTopSlots = api.emptyLayoutSlots();
    dockedBoundaryTopSlots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    dockedBoundaryTopSlots.left = api.paneStateWithTabs(['__files__'], '__files__');
    dockedBoundaryTopSlots.slot1 = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(dockedBoundaryTopSlots);
    api.splitSessionAtLayoutBoundary('__prefs__', 'top');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots()).tree), {
      split: 'row',
      pct: 22,
      children: [
        {slot: 'left'},
        {split: 'column', pct: 50, children: [{slot: 'slot2'}, {slot: 'slot1'}]},
      ],
    });

    api.setLayoutSlotsForTest(dockedBoundarySlots);
    const dockedBoundaryRects = {
      left: {left: 0, top: 0, right: 240, bottom: 800, width: 240, height: 800},
      slot1: {left: 240, top: 0, right: 1200, bottom: 800, width: 960, height: 800},
    };
    api.setLayoutColumnRectsForTest(dockedBoundaryRects);
    api.showDropPreview({boundary: 'root', zone: 'bottom', targetSlot: 'slot1', previewNode: api.gridForTest(), targetRect: {left: 0, top: 0, right: 1200, bottom: 800, width: 1200, height: 800}});
    const previewInset = 6;
    assert.equal(api.gridForTest().style.getPropertyValue('--drop-preview-left'), `${dockedBoundaryRects.slot1.left + previewInset}px`, 'bottom full-span preview starts after the docked Finder');
    assert.equal(api.gridForTest().style.getPropertyValue('--drop-preview-width'), `${dockedBoundaryRects.slot1.width - previewInset * 2}px`, 'bottom full-span preview spans only the non-Finder content');
    api.clearDropPreview();

    const dockedBoundaryMoveOnlyContent = api.emptyLayoutSlots();
    dockedBoundaryMoveOnlyContent[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('slot1'), 22);
    dockedBoundaryMoveOnlyContent.left = api.paneStateWithTabs(['__files__'], '__files__');
    dockedBoundaryMoveOnlyContent.slot1 = api.paneStateWithTabs(['__info__'], '__info__');
    api.setLayoutSlotsForTest(dockedBoundaryMoveOnlyContent);
    api.splitSessionAtLayoutBoundary('__info__', 'bottom', 'slot1');
    assert.deepStrictEqual(canonical(api.serialize(api.currentSlots())), {
      tree: {split: 'row', pct: 22, children: [{slot: 'left'}, {slot: 'slot2'}]},
      panes: {
        left: {tabs: ['__files__'], active: '__files__'},
        slot2: {tabs: ['__info__'], active: '__info__'},
      },
    });

    api.setLayoutSlotsForTest(fullSpanSlots);
    const boundarySlot = new TestElement('boundary-slot');
    boundarySlot.classList.add('drop-slot');
    boundarySlot.dataset.slot = 'slot4';
    boundarySlot.rect = {left: 620, top: 0, right: 1200, bottom: 800, width: 580, height: 800};
    const boundaryEvent = dragEvent(1192, '__info__');
    boundaryEvent.target = boundarySlot;
    boundaryEvent.clientY = 400;
    const boundaryIntent = api.dropIntentForEvent(boundaryEvent, {allowBoundary: true});
    assert.equal(boundaryIntent.boundary, 'root');
    assert.equal(boundaryIntent.zone, 'right');
    api.showDropPreview(boundaryIntent);
    assert.ok(api.gridForTest().classList.contains('drop-preview-root'), 'outer-edge session drags show a root full-span preview');
    assert.equal(api.gridForTest().dataset.dropLabel, 'full right');
    api.clearDropPreview();
    assert.equal(api.gridForTest().classList.contains('drop-preview-root'), false);
    assert.equal('dropLabel' in api.gridForTest().dataset, false);

    const resizer = new TestElement('root-resizer');
    resizer.classList.add('layout-resizer');
    resizer.dataset.splitPath = '';
    resizer.rect = {left: 598, top: 0, right: 602, bottom: 800, width: 4, height: 800};
    const gutterEvent = dragEvent(601, '__info__');
    gutterEvent.target = resizer;
    gutterEvent.clientY = 400;
    const gutterIntent = api.dropIntentForEvent(gutterEvent, {allowBoundary: true});
    assert.equal(gutterIntent.boundary, 'gutter');
    assert.equal(gutterIntent.zone, 'right');
    assert.equal(gutterIntent.splitPath, '');
    api.showDropPreview(gutterIntent);
    assert.ok(api.gridForTest().classList.contains('drop-preview-gutter'), 'split-bar session drags show a full-span gutter preview');
    assert.equal(api.gridForTest().dataset.dropLabel, 'full span');
    assert.ok(api.gridForTest().style.getPropertyValue('--drop-preview-width'), 'gutter preview geometry is explicit');
    api.clearDropPreview();

    const noPreviewSlot = new TestElement('slot-one');
    noPreviewSlot.classList.add('drop-slot');
    noPreviewSlot.dataset.slot = 'slot1';
    noPreviewSlot.rect = {left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400};
    api.setGridPreviewNodesForTest([noPreviewSlot]);
    const finderPaneDrag = dragEvent(16, '__files__');
    finderPaneDrag.target = noPreviewSlot;
    finderPaneDrag.clientY = 200;
    api.handleDropDragOver(finderPaneDrag);
    assert.ok(finderPaneDrag.defaultPrevented, 'Finder pane dragover is handled so the browser does not show its own drop UI');
    assert.ok(finderPaneDrag.propagationStopped, 'Finder pane dragover is owned by the pane drag handler');
    assert.equal(finderPaneDrag.dataTransfer.dropEffect, 'none');
    assert.equal(noPreviewSlot.classList.contains('drop-preview'), false, 'Finder pane drags do not show a dotted pane preview');
    api.setLayoutSlotsForTest(finderOnly);
    const finderStrip = tabStrip([tabElement('__files__', 100, 120)]);
    api.bindPaneTabStrip(finderStrip, 'left');
    const event = dragEvent(125, '1');
    finderStrip.ondragover(event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(event.propagationStopped, true);
    assert.equal(event.dataTransfer.dropEffect, 'none');
    assert.equal(finderStrip.classList.contains('tab-drop-preview'), false);
  });
}

module.exports = {runShareThemeSuite};

if (require.main === module) {
  runSuites([runShareThemeSuite]);
}

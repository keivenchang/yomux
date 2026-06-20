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

async function runLayoutAsyncSuite() {
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
        return parsed.pathname === '/api/session-files-batch' && parsed.searchParams.get('hours') === '48';
      }), 'Tabber lookback change handler reloads touched paths immediately');
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
      slots.left = api.paneStateWithTabs(['1'], '1');
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
      const scrollHostApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64');
      const prefsScroller = new TestElement('prefs-scroll');
      prefsScroller.className = 'preferences-scroll';
      prefsScroller.scrollTop = 444;
      prefsScroller.scrollLeft = 12;
      scrollHostApi.setDocumentQuerySelectorAllForTest(selector => selector === '.preferences-scroll' ? [prefsScroller] : []);
      const hostScrollSnapshot = scrollHostApi.shareUiStateSnapshotForTest().scroll.find(entry => entry.target === 'preferences');
      assert.deepStrictEqual(canonical(hostScrollSnapshot), {kind: 'preferences', left: 12, target: 'preferences', top: 444}, 'YO!share full UI snapshots include host Preferences scroll for late viewers');

      const sharePrefsApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-prefs-scroll', mode: 'ro', session: '1', sessions: ['1']},
      });
      const sharePrefsScroller = new TestElement('share-prefs-scroll');
      sharePrefsScroller.className = 'preferences-scroll';
      sharePrefsScroller.scrollTop = 0;
      sharePrefsScroller.scrollLeft = 0;
      sharePrefsApi.setDocumentQuerySelectorAllForTest(selector => selector === '.preferences-scroll' ? [sharePrefsScroller] : []);
      await sharePrefsApi.applyShareUiStateForTest({scroll: [hostScrollSnapshot]});
      assert.equal(sharePrefsScroller.scrollTop, 444, 'YO!share clients apply Preferences scroll from full UI snapshots');
      assert.equal(sharePrefsScroller.scrollLeft, 12, 'YO!share clients apply Preferences horizontal scroll from full UI snapshots');

      const shareTextarea = new TestElement('share-yoagent-format', 'textarea');
      shareTextarea.dataset.settingPath = 'yoagent.format';
      shareTextarea.value = 'Reply in Markdown. Default shape: a short direct answer, then optional bullets for the top relevant topics.';
      shareTextarea.clientWidth = 200;
      shareTextarea.clientHeight = 60;
      shareTextarea.scrollHeight = 160;
      sharePrefsApi.appRootForTest().appendChild(shareTextarea);
      await sharePrefsApi.applyShareUiStateForTest({textWraps: [{
        key: 'yoagent.format',
        tag: 'textarea',
        rect: {left: 40, top: 80, width: 640, height: 132},
        scrollHeight: 160,
      }]});
      assert.equal(shareTextarea.style.width, '640px', 'YO!share clients pin native settings control width from host wrapped-text metrics');
      assert.equal(shareTextarea.style.height, '132px', 'YO!share clients pin native settings control height from host wrapped-text metrics');
      assert.equal(shareTextarea.style.overflowY, 'auto', 'YO!share clients preserve host textarea clipping/scrolling when content exceeds host height');
    }

    {
      const hostTopbarApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64');
      hostTopbarApi.setAutoApproveStateForTest('1', {enabled: true, screen: {key: 'working'}});
      const autoSnapshot = hostTopbarApi.shareUiStateSnapshotForTest().autoApprove;
      const shareTopbarApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-yolo-badge', mode: 'ro', session: '1', sessions: ['1']},
      });
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tabs').badgeText, '0', 'share viewers start with 0 running YO jobs before the host snapshot');
      await shareTopbarApi.applyShareUiStateForTest({autoApprove: autoSnapshot});
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tabs').badgeText, '1', 'share viewers mirror the host running YO badge from UI state');
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tmux').items[0].label, 'YO on', 'share viewers mirror host tmux YO state from UI state');
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
      assert.equal(calls.filter(url => url.startsWith('/api/fs/read')).length, 1, 'entry identity avoids a second read before focusing the existing editor');
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
      assert.equal(calls.filter(url => url.startsWith('/api/fs/read')).length, 1, 'same-path open dedupe does not race a second read');
    }

    {
      const zhHant = JSON.parse(fs.readFileSync('static/locales/zh-Hant.json', 'utf8'));
      const shareDifferApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        strings: {en: JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')), 'zh-Hant': zhHant},
        share: {view: true, id: 'share123', mode: 'ro', session: '1', sessions: ['1']},
      });
      shareDifferApi.i18nSetCatalogForTest('zh-Hant', zhHant);
      shareDifferApi.setFileExplorerModeForTest('diff');
      shareDifferApi.setFileExplorerChangesSelectedSessionForTest('1');
      shareDifferApi.setSessionFilesPayloadForTest({
        session: '1',
        loaded: true,
        errors: [],
        refs_by_repo: {},
        repos: [{repo: '/repo/app', count: 1, touched_count: 1, added: 2, removed: 1}],
        files: [{session: '1', agent: 'codex', status: 'M', repo: '/repo/app', path: 'README.md', abs_path: '/repo/app/README.md', mtime: 100, added: 2, removed: 1}],
      });
      const beforeLocaleFrame = shareDifferApi.fileExplorerChangesPanelHtml();
      assert.ok(beforeLocaleFrame.includes('data-open-change-file="/repo/app/README.md"'), 'DOIT.67: Differ renders rows before a mirrored language frame');
      shareDifferApi.applyShareAppearanceStateForTest({locale: 'zh-Hant', languagePref: 'zh-Hant'});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(shareDifferApi.i18nActiveLocaleId(), 'zh-Hant', 'DOIT.67: mirrored appearance frames apply the host language to read-only viewers');
      const afterLocaleFrame = shareDifferApi.fileExplorerChangesPanelHtml();
      assert.ok(afterLocaleFrame.includes('data-open-change-file="/repo/app/README.md"'), 'DOIT.67: Differ rows stay visible after a mirrored language frame');
      assert.ok(afterLocaleFrame.includes(zhHant['changes.refresh']), 'DOIT.67: Differ chrome is localized after a mirrored language frame');
      assert.equal(afterLocaleFrame.includes('No Differ results for this session.'), false, 'DOIT.67: Differ does not blank to the empty-state during mirrored locale apply');
    }

    {
      const shareEditorApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-diff', mode: 'ro', session: '1', sessions: ['1']},
      });
      const path = '/repo/app/test_app.py';
      const item = shareEditorApi.fileEditorItemFor(path);
      shareEditorApi.registerFileEditorLayoutItemForTest(path, {item});
      shareEditorApi.setOpenFileStateForTest(path, {
        mtime: 1,
        size: 180,
        kind: 'text',
        original: 'line 1\nline 2\n',
        content: 'line 1\nline two\n',
        dirty: false,
        gitRoot: '/repo/app',
        gitTracked: true,
        gitHasHistory: true,
        gitHistory: [{ref: 'HEAD', short: 'HEAD'}, {ref: 'abc1234', short: 'abc1234'}],
        diffLoaded: false,
      });
      await shareEditorApi.applyShareUiStateForTest({editor: {modes: [{
        path,
        item,
        mode: 'diff',
        diffFromRef: 'abc1234',
        diffToRef: 'current',
        diffExpandUnchanged: true,
        viewState: {top: 444, left: 9, anchor: 21, head: 25},
      }]}});
      assert.equal(shareEditorApi.editorViewModeFor(path, item), 'diff', 'DOIT.68: read-only share UI-state restores host editor diff mode');
      assert.equal(shareEditorApi.openFileStateForTest(path).diffPinnedFromRef, 'abc1234', 'DOIT.68: read-only share UI-state restores host diff FROM ref');
      assert.equal(shareEditorApi.openFileStateForTest(path).diffPinnedToRef, 'current', 'DOIT.68: read-only share UI-state restores host diff TO ref');
      assert.equal(shareEditorApi.fileEditorViewStateForTest(item).scrollTop, 444, 'DOIT.68: read-only share UI-state seeds host editor scrollTop');
      assert.equal(shareEditorApi.fileEditorViewStateForTest(item).scrollLeft, 9, 'DOIT.68: read-only share UI-state seeds host editor horizontal scroll');
      const target = `editor:${item}:editor`;
      shareEditorApi.applyShareScrollStateForTest({target, kind: 'editor', path, item, source: 'editor', top: 712, left: 13, anchor: 80, head: 81});
      assert.deepStrictEqual({...shareEditorApi.shareLastAppliedScrollForTest(target)}, {top: 712, left: 13}, 'DOIT.68: host editor scroll frames are remembered before a DOM scroller exists');
      assert.equal(shareEditorApi.fileEditorViewStateForTest(item).scrollTop, 712, 'DOIT.68: host editor scroll frames update the editor view-state cache');
      assert.equal(shareEditorApi.fileEditorViewStateForTest(item).anchor, 80, 'DOIT.68: host editor scroll frames update the editor selection anchor');
    }

    {
      const hostFinderDiffApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64');
      hostFinderDiffApi.setDiffRefsByRepoForTest('/repo/app', {from: 'abc1234', to: 'def5678'});
      const finderSnapshot = hostFinderDiffApi.shareUiStateSnapshotForTest().finder;
      assert.deepStrictEqual(canonical(finderSnapshot.diffRefsByRepo['/repo/app']), {from: 'abc1234', to: 'def5678'}, 'YO!share snapshots repo-scoped Differ FROM and TO refs');
      const shareFinderDiffApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-finder-diff', mode: 'ro', session: '1', sessions: ['1']},
      });
      // applyShareUiState -> applyShareFinderState -> openFileExplorerAt enqueues a BATCHED /api/fs/batch
      // directory listing; the 8ms flush now fires via the harness setTimeout shim, so the apply settles
      // instead of hanging. Stub /api/fs/batch so the auto-flushed listing resolves cleanly.
      shareFinderDiffApi.setFetchForTest((url, options = {}) => {
        if (String(url).startsWith('/api/fs/batch')) {
          const requests = JSON.parse(options.body || '{}').requests || [];
          return Promise.resolve(jsonResponse({responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: []}}))}));
        }
        return Promise.resolve(jsonResponse({items: [], session: '1'}));
      });
      await shareFinderDiffApi.applyShareUiStateForTest({finder: finderSnapshot});
      assert.deepStrictEqual(canonical(shareFinderDiffApi.diffRefParams('/repo/app')), {from: 'abc1234', to: 'def5678'}, 'YO!share clients apply repo-scoped Differ TO refs instead of sticking on current');
    }

    {
      const shareFinderJumpApi = loadYolomux('?shareReplay=0', ['5', '6'], 'https:', 'Linux x86_64', 'readonly', {
        share: {
          view: true,
          id: 'share-finder-jump',
          mode: 'ro',
          session: '5',
          sessions: ['5', '6'],
          finder: {root: '/home/test/yolomux.dev1', rootMode: 'sync', mode: 'files', session: '5'},
        },
      });
      shareFinderJumpApi.setTranscriptInfoForTest('5', {
        project: {git: {cwd: '/home/test/yolomux.dev1/src', root: '/home/test/yolomux.dev1'}},
        selected_pane: {current_path: '/home/test/yolomux.dev1/src'},
      });
      shareFinderJumpApi.setTranscriptInfoForTest('6', {
        project: {git: {cwd: '/home/test/other.dev/src', root: '/home/test/other.dev'}},
        selected_pane: {current_path: '/home/test/other.dev/src'},
      });
      shareFinderJumpApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev1', [{name: 'src', kind: 'dir'}]);
      shareFinderJumpApi.setFileExplorerDirListingForTest('/home/test/yolomux.dev1/src', [{name: 'main.js', kind: 'file'}]);
      shareFinderJumpApi.setFileExplorerDirListingForTest('/home/test/other.dev', [{name: 'src', kind: 'dir'}]);
      assert.equal(shareFinderJumpApi.shareReadOnlyFinderStateIsHostOwnedForTest(), true, 'read-only share clients treat Finder root and expansion as host-owned between host frames');

      await shareFinderJumpApi.applyShareUiStateForTest({finder: {
        root: '/home/test/yolomux.dev1',
        rootMode: 'sync',
        mode: 'files',
        session: '5',
        expanded: ['/home/test/yolomux.dev1/src'],
      }});
      assert.equal(shareFinderJumpApi.fileExplorerRootForTest(), '/home/test/yolomux.dev1', 'read-only share applies the host Finder root');
      assert.deepStrictEqual(canonical(shareFinderJumpApi.fileExplorerExpandedForTest()), ['/home/test/yolomux.dev1/src'], 'read-only share applies the host Finder expansion');

      shareFinderJumpApi.setSessionFilesPayloadForDestinationForTest({
        session: '6',
        loaded: true,
        repos: [{repo: '/home/test/other.dev'}],
        files: [{session: '6', agent: 'codex', status: 'M', repo: '/home/test/other.dev', path: 'src/main.js', abs_path: '/home/test/other.dev/src/main.js'}],
        errors: [],
      });
      shareFinderJumpApi.scheduleFileExplorerActiveTabSyncForTest('6', {explicit: true});
      assert.equal(await shareFinderJumpApi.openFileExplorerAtForTest('/home/test/other.dev'), false, 'read-only share local Finder opens are blocked outside host UI-state frames');
      await Promise.resolve();
      await Promise.resolve();
      assert.equal(shareFinderJumpApi.fileExplorerRootForTest(), '/home/test/yolomux.dev1', 'read-only share local payloads cannot jump Finder to the client context');
      assert.deepStrictEqual(canonical(shareFinderJumpApi.fileExplorerExpandedForTest()), ['/home/test/yolomux.dev1/src'], 'read-only share local payloads cannot collapse or replace the host expansion');

      await shareFinderJumpApi.applyShareUiStateForTest({finder: {
        root: '/home/test/yolomux.dev1',
        rootMode: 'sync',
        mode: 'files',
        session: '5',
        expanded: ['/home/test/yolomux.dev1/src'],
      }});
      assert.equal(shareFinderJumpApi.fileExplorerRootForTest(), '/home/test/yolomux.dev1', 'repeated same-root host frames keep the Finder on the host root');
      assert.deepStrictEqual(canonical(shareFinderJumpApi.fileExplorerExpandedForTest()), ['/home/test/yolomux.dev1/src'], 'repeated same-root host frames keep the host expansion stable');
    }

    {
      const hostChromeApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64');
      hostChromeApi.setInfoPanelSubTabForTest('yoagent');
      hostChromeApi.setTabMetaVisibleForTest(false);
      const chromeSnapshot = hostChromeApi.shareUiStateSnapshotForTest().chrome;
      assert.equal(chromeSnapshot.tabMetaVisible, false, 'YO!share snapshots host tab metadata state that is otherwise local-storage-backed');
      assert.equal(chromeSnapshot.infoSubTab, 'yoagent', 'YO!share snapshots the host YO!info/YO!agent sub-tab');

      const shareChromeApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-chrome', mode: 'ro', session: '1', sessions: ['1']},
      });
      shareChromeApi.setInfoPanelSubTabForTest('info');
      shareChromeApi.setTabMetaVisibleForTest(true);
      await shareChromeApi.applyShareUiStateForTest({chrome: chromeSnapshot});
      assert.equal(shareChromeApi.infoPanelSubTabForTest(), 'yoagent', 'YO!share clients mirror the host YO!agent sub-tab');
      assert.equal(shareChromeApi.tabMetaVisibleForTest(), false, 'YO!share clients mirror the host tab metadata toggle');
    }

    {
      const hostDiffApi = loadYolomux('', ['1'], 'https:', 'Linux x86_64');
      const path = '/repo/app/expand_me.py';
      const item = hostDiffApi.registerFileEditorLayoutItemForTest(path);
      hostDiffApi.setOpenFileStateForTest(path, {
        mtime: 1,
        size: 180,
        kind: 'text',
        original: 'line 1\nline 2\n',
        content: 'line 1\nline two\n',
        dirty: false,
        gitRoot: '/repo/app',
        gitTracked: true,
        gitHasHistory: true,
        gitHistory: [{ref: 'HEAD', short: 'HEAD'}, {ref: 'abc1234', short: 'abc1234'}],
        diffLoaded: true,
        diff: 'diff --git a/expand_me.py b/expand_me.py\n',
      });
      hostDiffApi.setFileEditorViewMode(path, 'diff', item);
      hostDiffApi.setFileEditorDiffExpandUnchangedForItemForTest(path, item, true);
      const modeEntry = hostDiffApi.shareUiStateSnapshotForTest().editor.modes.find(entry => entry.item === item);
      assert.equal(modeEntry?.diffExpandUnchanged, true, 'YO!share snapshots per-editor diff expansion overrides');

      const shareDiffApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-diff-expand', mode: 'ro', session: '1', sessions: ['1']},
      });
      shareDiffApi.registerFileEditorLayoutItemForTest(path, {item});
      shareDiffApi.setOpenFileStateForTest(path, {
        mtime: 1,
        size: 180,
        kind: 'text',
        original: 'line 1\nline 2\n',
        content: 'line 1\nline two\n',
        dirty: false,
        gitRoot: '/repo/app',
        gitTracked: true,
        gitHasHistory: true,
        gitHistory: [{ref: 'HEAD', short: 'HEAD'}, {ref: 'abc1234', short: 'abc1234'}],
        diffLoaded: true,
        diff: 'diff --git a/expand_me.py b/expand_me.py\n',
      });
      await shareDiffApi.applyShareUiStateForTest({editor: {modes: [modeEntry]}});
      assert.equal(shareDiffApi.fileEditorDiffExpandUnchangedForItemForTest(item), true, 'YO!share clients apply per-editor diff expansion overrides');
    }

    {
      const staleDoitPath = '/home/test/yolomux.dev1/DOIT.57.md';
      const realDoitPath = '/home/test/yolomux.dev2/DOIT.57.md';
      const validatingDoitApi = loadYolomux('', ['1']);
      const validatingDoitItem = validatingDoitApi.registerFileEditorLayoutItemForTest(staleDoitPath);
      const validatingDoitSlots = validatingDoitApi.emptyLayoutSlots();
      validatingDoitSlots.left = validatingDoitApi.paneStateWithTabs([validatingDoitItem], validatingDoitItem);
      validatingDoitApi.setLayoutSlotsForTest(validatingDoitSlots);
      validatingDoitApi.setFileQuickOpenCandidatesForTest('/home/test/yolomux.dev3', [
        {name: 'DOIT.57.md', path: realDoitPath, relative_path: 'DOIT.57.md', indexed_root: '/home/test/yolomux.dev2', kind: 'file'},
      ]);
      validatingDoitApi.setCommandPaletteStateForTest('files', 'doit57');
      const validationCalls = [];
      validatingDoitApi.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        validationCalls.push({url: String(url), requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map(request => request.path === staleDoitPath
            ? {id: request.id, ok: false, status: 404, error: 'path not found'}
            : {id: request.id, ok: true, status: 200, payload: {path: request.path, kind: 'file'}}),
        }));
      });
      assert.ok(validatingDoitApi.commandPaletteItems().some(item => item.targetItem === validatingDoitItem), 'unknown file tabs remain visible before path-info validation resolves');
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
      const syncTreeApi = loadYolomux('', ['1']);
      syncTreeApi.setFileExplorerRootMode('sync', {sync: false});
      syncTreeApi.setFileExplorerRootForTest('/home/test');
      syncTreeApi.setTranscriptInfoForTest('1', {
        project: {git: {cwd: '/home/test/yolomux.dev2', root: '/home/test/yolomux.dev2'}},
        selected_pane: {current_path: '/home/test/yolomux.dev2'},
      });
      syncTreeApi.setSessionFilesPayloadForTest({
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

      const today = new Date();
      const generatedName = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}-001.png`;
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), method: options.method || 'GET', body: options.body});
        if (String(url).startsWith('/api/upload')) {
          return Promise.resolve(jsonResponse({files: [{path: `/home/test/${generatedName}`}]}));
        }
        if (String(url).startsWith('/api/transcripts')) {
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
        data: `[Image #1] '/home/test/${generatedName}' `,
      }, 'pasted image upload inserts the image reference into xterm');
    }

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

    // DOIT.78 (78.6): invariant guard — paste and drop must route through the ONE shared image-payload
    // detector so a new entry point can't reintroduce a divergent leak path.
    {
      const imgSource = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(/document\.addEventListener\('paste', event => \{\s*if \(!dataTransferHasImagePayload\(event\.clipboardData\)\) return;/.test(imgSource), '78.6: the document paste handler claims via the shared dataTransferHasImagePayload detector');
      assert.ok(imgSource.includes('function hasUploadableDrag(event)') && /addEventListener\('drop', event => \{\s*if \(!hasUploadableDrag\(event\)\) return;/.test(imgSource), '78.6: the file-drop handler claims via hasUploadableDrag (file OR image rich-data)');
      assert.ok(imgSource.includes('function dataTransferImageFiles(dt)') && imgSource.includes('function dataTransferHasImagePayload(dt)'), '78.6: the shared image-payload parent exists');
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
            payload: {path: request.path, entries: [{name: 'TODO.md', kind: 'file'}]},
          })),
        }));
      });
      const first = api.fetchDirectoryForTest('/home/test');
      const second = api.fetchDirectoryForTest('/home/test/');
      await api.flushFileExplorerFsBatchForTest();
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [{id: 1, path: '/home/test', type: 'list'}],
        url: '/api/fs/batch',
      }], 'concurrent identical directory listings share one batched backend request');
      const [firstEntries, secondEntries] = await Promise.all([first, second]);
      assert.strictEqual(firstEntries, secondEntries, 'shared directory listing callers receive the same entries object');
      assert.equal(firstEntries[0].name, 'TODO.md');
      const cachedEntries = await api.fetchDirectoryForTest('/home/test');
      assert.strictEqual(cachedEntries, firstEntries, 'completed directory listing is reused by the short TTL cache');
      assert.equal(calls.length, 1, 'short TTL cache avoids an immediate repeat directory listing');
    }

    {
      const api = loadYolomux();
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map((request, index) => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, entries: [{name: index === 0 ? 'a.txt' : 'b.txt', kind: 'file'}]},
          })),
        }));
      });
      const first = api.fetchDirectoryForTest('/home/test', {fresh: true});
      const second = api.fetchDirectoryForTest('/home/test', {fresh: true});
      await api.flushFileExplorerFsBatchForTest();
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [
          {id: 1, path: '/home/test', type: 'list'},
          {id: 2, path: '/home/test', type: 'list'},
        ],
        url: '/api/fs/batch',
      }], 'explicit fresh directory listings stay distinct inside one batch');
      const [firstEntries, secondEntries] = await Promise.all([first, second]);
      assert.equal(firstEntries[0].name, 'a.txt');
      assert.equal(secondEntries[0].name, 'b.txt');
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
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map(request => ({
            id: request.id,
            ok: false,
            status: 403,
            error: `denied ${request.path}`,
          })),
        }));
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
        requests: [{id: 1, path: '/home/test', type: 'info'}],
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
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/yolomux.dev3'}});
      const lines = [terminalLine('• Documented it in docs/specs/SHARE_TEST_INVENTORY.md:123')];
      const term = {cols: 80, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};
      const fileRef = api.terminalWrappedLineReferences(term, 1).find(ref => ref.type === 'file');
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: body.requests.map(request => ({
            id: request.id,
            ok: true,
            payload: {kind: 'file', name: 'SHARE_TEST_INVENTORY.md', path: request.path},
          })),
        }));
      });
      const targetPromise = api.terminalFileReferenceTarget('1', fileRef);
      await api.flushFileExplorerFsBatchForTest();
      const target = await targetPromise;
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [{id: 1, path: '/home/test/yolomux.dev3/docs/specs/SHARE_TEST_INVENTORY.md', type: 'info'}],
        url: '/api/fs/batch',
      }], 'terminal file refs confirm existence through the shared fs info batch path');
      assert.deepStrictEqual(canonical(target), {
        info: {kind: 'file', name: 'SHARE_TEST_INVENTORY.md', path: '/home/test/yolomux.dev3/docs/specs/SHARE_TEST_INVENTORY.md'},
        line: 123,
        path: '/home/test/yolomux.dev3/docs/specs/SHARE_TEST_INVENTORY.md',
        text: 'docs/specs/SHARE_TEST_INVENTORY.md:123',
      }, 'confirmed terminal file refs carry the absolute path and line for the Open file menu action');
    }

    {
      const source = fs.readFileSync('static/yolomux.js', 'utf8');
      assert.ok(/Promise\.all\(directories\.map\(async directory =>/.test(source), 'periodic Finder refresh starts watched directory checks together so fs/list can batch');
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

    test('self-update: Update Now removes toast and reloads after restart ping', async () => {
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
          return Promise.resolve(jsonResponse({ok: true, restarting: true, message: 'updated; restarting now', target: '0.4.18'}));
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

    test('self-update: dirty edits and active typing defer automatic reload safely', async () => {
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
}

module.exports = {runLayoutAsyncSuite};

if (require.main === module) {
  runSuites([runLayoutAsyncSuite]);
}

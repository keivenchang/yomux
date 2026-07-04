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

async function runLayoutAsyncSuite() {
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

  await testAsync('background-owner request record rejects stale HTTP completions and lets pushes win', async () => {
    const pending = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      assert.equal(String(url), '/api/background/status');
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
    });

    const first = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    const second = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    assert.equal(pending.length, 2, 'a forced refresh starts a replacement generation');
    pending[1].resolve(jsonResponse({marker: 'new-http'}));
    await second;
    pending[0].resolve(jsonResponse({marker: 'old-http'}));
    await first;
    assert.equal(api.backgroundOwnerStatusStateForTest().payload.marker, 'new-http', 'older HTTP completion cannot replace the newer generation');
    assert.equal(api.backgroundOwnerStatusStateForTest().request, null, 'only the current request clears the record handle');

    const third = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    api.applyBackgroundOwnerStatusPayloadForTest({marker: 'push'}, {render: false});
    pending[2].resolve(jsonResponse({marker: 'stale-after-push'}));
    await third;
    assert.equal(api.backgroundOwnerStatusStateForTest().payload.marker, 'push', 'an SSE payload invalidates the older HTTP generation');

    const fourth = api.refreshBackgroundOwnerStatusForTest({force: true, render: false});
    pending[3].reject(new Error('owner offline'));
    await fourth;
    assert.ok(api.backgroundOwnerStatusStateForTest().error, 'the current request records its failure');
    assert.equal(api.backgroundOwnerStatusStateForTest().request, null, 'current failure releases the request handle');
  });

  await testAsync('activity-summary request record rejects stale responses after a newer push', async () => {
    const pending = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/activity-summary?'));
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
    });

    const first = api.refreshActivitySummaryForTest({force: true, localeChange: true});
    const second = api.refreshActivitySummaryForTest({force: true, localeChange: true});
    pending[1].resolve(jsonResponse({marker: 'new-http', sessions: {}, global: {lines: []}}));
    await second;
    pending[0].reject(new Error('stale request failed'));
    await first;
    assert.equal(api.activitySummaryStateForTest().payload.marker, 'new-http', 'a stale failure cannot replace the newer forced response');

    const third = api.refreshActivitySummaryForTest({force: true, localeChange: true});
    api.applyActivitySummaryPayloadFromPushForTest({marker: 'push', sessions: {}, global: {lines: []}});
    pending[2].resolve(jsonResponse({marker: 'stale-after-push', sessions: {}, global: {lines: []}}));
    await third;
    assert.equal(api.activitySummaryStateForTest().payload.marker, 'push', 'an SSE push invalidates the older HTTP generation');
    assert.equal(api.activitySummaryStateForTest().refreshing, false, 'the push completes the shared refresh state');
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
        assert.equal(String(url), '/api/fs/batch');
        const requests = JSON.parse(options.body || '{}').requests || [];
        return new Promise(resolve => batches.push({requests, resolve}));
      });
      const resolveBatch = (index, marker, ok = true) => {
        const batch = batches[index];
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
      assert.equal(batches.length, 2, `${resource.type}: independent fresh reads stay independently batched`);
      resolveBatch(1, 'new');
      await newerFlush;
      assert.equal(resource.marker(await newer), 'new');
      resolveBatch(0, 'old');
      await olderFlush;
      assert.equal(resource.marker(await older), 'old', `${resource.type}: the original caller still receives its own response`);
      assert.equal(resource.marker(await resource.fetch(api, resource.path, {})), 'new', `${resource.type}: the slower older response cannot overwrite the newer cache generation`);

      const invalidated = resource.fetch(api, resource.path, {fresh: true});
      const invalidatedFlush = api.flushFileExplorerFsBatchForTest();
      assert.equal(batches.length, 3);
      api.invalidateFileExplorerFsCachesForTest();
      resolveBatch(2, 'retired');
      await invalidatedFlush;
      assert.equal(resource.marker(await invalidated), 'retired');
      assert.equal(api.fileExplorerFsResourceRecordsForTest().length, 0, `${resource.type}: invalidated completion cannot recreate its retired resource record`);

      const current = resource.fetch(api, resource.path, {fresh: true});
      const currentFlush = api.flushFileExplorerFsBatchForTest();
      resolveBatch(3, 'current');
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
      return new Promise(resolve => batches.push({requests, resolve}));
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
    api.installClientEventStreamForTest();
    const source = api.clientEventTransportStateForTest().source;
    source.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    assert.equal(api.clientEventTransportStateForTest().connected, true, 'ready marks the record connected');
    source.onerror();
    assert.equal(api.clientEventTransportStateForTest().connected, false, 'error marks the same record disconnected');
    source.listeners.get('ping')[0]({data: '{}', type: 'ping', lastEventId: ''});
    assert.equal(api.clientEventTransportStateForTest().connected, true, 'later traffic marks the record connected again');

    const reconnectTimerStart = timers.length;
    api.scheduleReconnectResyncForTest('visible');
    api.scheduleReconnectResyncForTest('online');
    const firstReconnectTimer = timers[reconnectTimerStart];
    const secondReconnectTimer = timers[reconnectTimerStart + 1];
    assert.deepStrictEqual(clearedTimers, [firstReconnectTimer.id], 'replacement reconnect debounce clears the prior record timer');
    assert.equal(api.clientEventTransportStateForTest().resyncTimer, secondReconnectTimer.id, 'the record owns the replacement reconnect timer');
    secondReconnectTimer.callback();
    assert.equal(api.clientEventTransportStateForTest().resyncTimer, null, 'firing consumes the reconnect timer');
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
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
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
  });

  await testAsync('Search and Runs records reject stale query and refresh completions', async () => {
    const pendingSearch = [];
    const pendingRuns = [];
    const api = loadYolomux();
    api.setFetchForTest(url => {
      const value = String(url);
      if (value.startsWith('/api/search?')) return new Promise((resolve, reject) => pendingSearch.push({url: value, resolve, reject}));
      if (value === '/api/run-history') return new Promise((resolve, reject) => pendingRuns.push({resolve, reject}));
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
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
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
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
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
  });

  await testAsync('Finder Sync record cancels stale root work and resets manual ownership atomically', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFileExplorerRootMode('sync', {sync: false});
    api.setFileExplorerSyncStateForTest({inFlightSignature: 'old-plan', appliedPlanKey: 'old-plan', generation: 4});
    api.setFetchForTest((url, options = {}) => {
      assert.equal(String(url), '/api/fs/batch');
      return new Promise(resolve => pending.push({resolve, requests: JSON.parse(options.body || '{}').requests || []}));
    });
    const reply = pendingRequest => jsonResponse({
      responses: pendingRequest.requests.map(request => ({
        id: request.id,
        ok: true,
        status: 200,
        payload: {path: request.path, entries: [{name: 'README.md', kind: 'file'}]},
      })),
    });

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
    }, 'one target record owns both disclosure fields');

    api.setFileExplorerVisibleSyncTargetForTest('1', '/repo/b');
    api.resetFileExplorerSyncManualCollapsesForTest({session: '1', root: '/repo/b'});
    api.setFileExplorerExpandedForTest(['/repo/b/lib']);
    api.rememberFileExplorerSyncExpandedStateForTest('1', '/repo/b');
    assert.deepStrictEqual(canonical(api.fileExplorerSyncTargetRecordForTest('1\x1f/repo/b')), {
      expandedPaths: ['/repo/b/lib'],
      manualCollapsedPaths: [],
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

  await testAsync('Tabber activity cache treats direct snapshots as newer than in-flight HTTP work', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.setFileExplorerModeForTest('tabber');
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/activity?'));
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
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

  await testAsync('Quick Open record aborts path listings and rejects close-reopen stale completions', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    api.installCommandPaletteFixtureForTest();
    api.setFetchForTest((url, options = {}) => new Promise((resolve, reject) => pending.push({
      url: String(url),
      signal: options.signal,
      resolve,
      reject,
    })));

    const stale = api.refreshFileQuickOpenCandidatesForTest('/tmp/old');
    assert.ok(pending[0].url.startsWith('/api/fs/list?path='), 'absolute path mode uses a directory listing');
    assert.ok(pending[0].signal, 'path-mode listing receives the record abort signal');
    api.abortFileQuickOpenSearchForTest();
    assert.equal(pending[0].signal.aborted, true, 'closing/cancelling aborts the path-mode request');

    const current = api.refreshFileQuickOpenCandidatesForTest('/tmp/new');
    pending[1].resolve(jsonResponse({path: '/tmp', entries: [{name: 'new.txt', kind: 'file'}]}));
    await current;
    pending[0].resolve(jsonResponse({path: '/tmp', entries: [{name: 'old.txt', kind: 'file'}]}));
    await stale;
    const state = api.fileQuickOpenStateForTest();
    assert.deepStrictEqual(canonical(state.candidates.map(item => item.path)), ['/tmp/new.txt'], 'stale completion after cancel/restart cannot replace current candidates');
    assert.equal(state.loading, false, 'the current request settles loading');
    assert.equal(state.abortController, null, 'the current request releases its abort controller');
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
    api.scheduleFileQuickOpenSearchForTest();
    api.scheduleFileQuickOpenSearchForTest();
    assert.deepStrictEqual(cleared, [1], 'replacement debounce clears the prior record timer');
    assert.equal(api.fileQuickOpenStateForTest().debounce, 2, 'the record owns only the replacement timer');
    timers[1].callback();
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

  await testAsync('YO!agent startup record freezes snapshots and keeps prewarm one-shot', async () => {
    const pending = [];
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {availableAgents: ['codex'], agentAuth: {codex: {installed: true, logged_in: true}}});
    api.setActivitySummaryPayloadForTest({global: {headline: 'old activity'}, sessions: {}, session_order: []});
    assert.equal(api.showYoagentStartupInfoOnceForTest(), true, 'startup info shows once');
    api.applyActivitySummaryPayloadFromPushForTest({global: {headline: 'new activity'}, sessions: {}, session_order: []});
    assert.equal(api.yoagentStartupStateForTest().activityPayload.global.headline, 'old activity', 'later activity pushes do not mutate the frozen startup snapshot');
    api.hideYoagentStartupInfoForTest();
    assert.equal(api.showYoagentStartupInfoOnceForTest(), false, 'hiding does not reset the one-shot');
    assert.equal(api.yoagentStartupStateForTest().infoVisible, false, 'the hidden startup block stays hidden');
    assert.equal(api.showYoagentStartupInfoForLatestActivityForTest(), true, 'explicit latest-activity refresh resets the one-shot');
    assert.equal(api.yoagentStartupStateForTest().activityPayload.global.headline, 'new activity', 'explicit refresh captures the latest snapshot');

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

    api.scheduleLayoutUrlStateRefreshForTest();
    api.scheduleLayoutUrlStateRefreshForTest();
    assert.equal(timers.length, 1, 'duplicate URL refresh schedules share one record timer');
    assert.equal(api.layoutUrlStateForTest().refreshTimer, 1, 'the record owns the scheduled timer');
    timers[0].callback();
    assert.equal(api.layoutUrlStateForTest().refreshTimer, null, 'firing consumes only the matching record timer');
  });

  await testAsync('session-files record lets an accepted push invalidate older HTTP work', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.fileExplorerItemId], api.fileExplorerItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/session-files?'));
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
    });

    const request = api.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true});
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
    }, {session: '1', from_ref: 'HEAD', to_ref: 'current'}), true, 'the matching push applies');
    assert.equal(api.fileExplorerSessionFilesStateForTest().loading, false, 'the push settles visible loading');
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
    assert.equal(state.payload.repos[0].repo, '/push', 'the older HTTP completion cannot replace the pushed payload');
    assert.equal(state.loading, false, 'stale finally cannot change the settled push state');
    assert.equal(state.signature, api.sessionFilesPayloadSignatureForPayloadForTest(state.payload), 'payload and signature remain one record snapshot');
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
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
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
      const api = loadYolomux('', ['1'], 'https:', 'Linux x86_64', 'admin', {fireAllTimeouts: true});
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

    await testAsync('watch-root synchronization record owns debounce, renewal, and fetch state', async () => {
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
        return new Promise(resolve => { resolveFetch = resolve; });
      });

      api.syncServerWatchRootsForTest({renew: true});
      api.syncServerWatchRootsForTest({immediate: true});
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {immediate: true, renew: true}, 'adjacent options merge into one record');
      assert.deepStrictEqual(cleared, [1], 'replacement debounce clears the prior record timer');
      timers[1].callback();
      assert.equal(api.serverWatchRootsStateForTest().timer, null, 'firing consumes the record timer');
      assert.deepStrictEqual(canonical(api.serverWatchRootsStateForTest().pendingOptions), {}, 'firing consumes merged options once');
      assert.equal(api.serverWatchRootsStateForTest().inFlight, true, 'the same record owns active fetch state');
      api.syncServerWatchRootsNowForTest();
      assert.equal(calls.length, 1, 'an in-flight registration suppresses duplicate fetches');
      resolveFetch(jsonResponse({ok: true}));
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'completion clears in-flight state');
      assert.ok(api.serverWatchRootsStateForTest().syncedAt > 0, 'success records the renewal timestamp');

      api.syncServerWatchRootsNowForTest({renew: true});
      assert.equal(calls.length, 1, 'a fresh identical registration does not renew early');
      api.setServerWatchRootsSyncedAtForTest(Date.now() - 240001);
      api.setFetchForTest((url, options = {}) => {
        calls.push({url: String(url), options});
        return Promise.resolve(jsonResponse({ok: true}));
      });
      api.syncServerWatchRootsNowForTest({renew: true});
      await flushAsyncWork();
      assert.equal(calls.length, 2, 'an expired identical registration renews through the same record');

      api.setFileExplorerRootForTest('/repo-failed');
      api.setFetchForTest(() => Promise.reject(new Error('offline')));
      api.syncServerWatchRootsNowForTest();
      await flushAsyncWork();
      await flushAsyncWork();
      assert.equal(api.serverWatchRootsStateForTest().inFlight, false, 'failure releases the synchronization record');
      assert.equal(api.serverWatchRootsStateForTest().signature, '', 'failure invalidates the signature so the next registration retries');
    });

    test('watch-root synchronization: retired parallel globals stay absent', () => {
      const src = fs.readFileSync('static_src/js/yolomux/00_bootstrap_state.js', 'utf8');
      for (const name of ['serverWatchRootsSignature', 'serverWatchRootsInFlight', 'serverWatchRootsSyncedAt', 'serverWatchRootsTimer', 'serverWatchRootsPendingOptions']) {
        assert.equal(src.includes(name), false, `${name} remains retired`);
      }
      assert.ok(src.includes('const serverWatchRootsState = {'), 'one watch-root synchronization owner remains');
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
      assert.equal(Object.prototype.hasOwnProperty.call(hiddenState, 'session_files'), false, 'hidden Finder/Differ does not register session-files refresh work');
      const hiddenCalls = [];
      hiddenApi.setFetchForTest(url => {
        hiddenCalls.push(String(url));
        return Promise.reject(new Error(`hidden Finder/Differ should not fetch ${url}`));
      });
      await hiddenApi.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true});
      await hiddenApi.refreshWatchedFilesystemForTest({full: true});
      assert.deepStrictEqual(hiddenCalls, [], 'hidden Finder/Differ skips session-files and tree refresh fetches');

      const visibleApi = loadYolomux('', ['1']);
      const visibleSlots = visibleApi.emptyLayoutSlots();
      visibleSlots[visibleApi.layoutTreeKey] = visibleApi.splitNode('row', visibleApi.leafNode('left'), visibleApi.leafNode('right'));
      visibleSlots.left = visibleApi.paneStateWithTabs([visibleApi.fileExplorerItemId], visibleApi.fileExplorerItemId);
      visibleSlots.right = visibleApi.paneStateWithTabs(['1'], '1');
      visibleApi.setLayoutSlotsForTest(visibleSlots);
      visibleApi.setFileExplorerRootForTest('/repo');
      visibleApi.setFileExplorerExpandedForTest(['/repo/src']);
      visibleApi.setFileExplorerModeForTest('diff');
      visibleApi.setFileExplorerSessionFilesPayloadForTest({
        session: '1',
        loaded: true,
        repos: [{repo: '/repo'}],
        files: [{repo: '/repo', abs_path: '/repo/src/changed.js'}],
        refs_by_repo: {},
        errors: [],
        from_ref: 'HEAD',
        to_ref: 'current',
      });
      const visibleState = visibleApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(visibleState.roots), ['/repo', '/repo/src'], 'visible Differ registers only session-files roots while the Finder tree is hidden');
      assert.deepStrictEqual(canonical(visibleState.session_files), [{session: '1', hours: 24, from_ref: 'HEAD', to_ref: 'current', repo_refs: null}], 'visible Differ registers the current session-files request');
      visibleApi.setFileExplorerModeForTest('tabber');
      const tabberState = visibleApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(tabberState.roots), [], 'visible Tabber does not inherit hidden Finder/Differ roots');
      assert.equal(Object.prototype.hasOwnProperty.call(tabberState, 'session_files'), false, 'visible Tabber does not register session-files work');
    });

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
      api.applyTmuxSignalsPayloadForTest({patch: true, windows: [
        {session: '1', window_index: 0, active: false},
        {session: '1', window_index: 1, active: true},
      ], removed_window_keys: []});

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
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tmux').items[0].label, 'YO (auto approve; YOLO)', 'share viewers mirror host tmux YO state from UI state');
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
      assert.deepStrictEqual(calls.filter(url => url.startsWith('/api/fs/read')).map(url => decodeURIComponent((url.match(/path=([^&]+)/) || [])[1] || '')), [newPath], 'new full path forces a fresh read');
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
      assert.ok(afterLocaleFrame.includes(zhHant['common.reload']), 'DOIT.67: Differ chrome is localized after a mirrored language frame');
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
      assert.deepStrictEqual({...shareEditorApi.shareScrollTargetPositionForTest(target)}, {top: 712, left: 13}, 'DOIT.68: host editor scroll frames are remembered before a DOM scroller exists');
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
      await hostChromeApi.selectSession(hostChromeApi.yoagentItemId);
      hostChromeApi.setTabMetaVisibleForTest(false);
      const chromeSnapshot = hostChromeApi.shareUiStateSnapshotForTest().chrome;
      assert.equal(chromeSnapshot.tabMetaVisible, false, 'YO!share snapshots host tab metadata state that is otherwise local-storage-backed');
      assert.equal(chromeSnapshot.infoSubTab, 'yoagent', 'YO!share snapshots the host YO!agent tab as legacy chrome state');

      const shareChromeApi = loadYolomux('?shareReplay=0', ['1'], 'https:', 'Linux x86_64', 'readonly', {
        share: {view: true, id: 'share-chrome', mode: 'ro', session: '1', sessions: ['1']},
      });
      shareChromeApi.setInfoPanelSubTabForTest('info');
      shareChromeApi.setTabMetaVisibleForTest(true);
      await shareChromeApi.applyShareUiStateForTest({chrome: chromeSnapshot});
      assert.equal(shareChromeApi.infoPanelSubTabForTest(), 'yoagent', 'YO!share clients preserve the legacy host YO!agent chrome marker');
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
      assert.ok(validatingDoitApi.commandPaletteItems().some(item => item.targetItem === validatingDoitItem), 'unknown file tabs remain visible before path-info validation resolves');
      assert.ok(validatingDoitApi.commandPaletteItems().some(item => item.targetItem === dirtyMissingItem), 'dirty missing tabs remain reachable through quick search');
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
      const pendingApi = loadYolomux('', ['1']);
      const container = new TestElement('finder-tree');
      const longPath = '/repo/this/is/a/very/long/path/that/takes/time';
      const parent = longPath.slice(0, longPath.lastIndexOf('/'));
      const name = longPath.slice(longPath.lastIndexOf('/') + 1);
      pendingApi.renderTreeChildrenForTest(container, parent, [{name, kind: 'dir'}], 0);
      const row = container.children.find(node => node?.dataset?.path === longPath);
      let resolveBatch = null;
      pendingApi.setFetchForTest((url, options = {}) => {
        if (!String(url).startsWith('/api/fs/batch')) return Promise.resolve(jsonResponse({}));
        const requests = JSON.parse(options.body || '{}').requests || [];
        return new Promise(resolve => {
          resolveBatch = () => resolve(jsonResponse({responses: requests.map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, entries: [{name: 'child.txt', kind: 'file'}]},
          }))}));
        });
      });
      const expandPromise = pendingApi.expandDirectoryRowForTest(row, longPath, {manual: true});
      const flushPromise = pendingApi.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      assert.equal(row.getAttribute('aria-expanded'), 'true', 'Finder directory shows expanded immediately while the backend listing is pending');
      assert.equal(row.classList.contains('loading-children'), true, 'Finder directory shows a pending expansion spinner while listing is in flight');
      assert.ok(resolveBatch, 'directory expansion issued the backend listing request');
      resolveBatch();
      await flushPromise;
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
      const api = loadYolomux('', ['1', '2']);
      api.setFileExplorerRootMode('sync', {sync: false});
      api.setFileExplorerRootForTest('/home/test');
      api.setTranscriptInfoForTest('1', {
        project: {git: {cwd: '/home/test/project/1/src', root: '/home/test/project/1'}},
        selected_pane: {current_path: '/home/test/project/1/src'},
      });
      api.setSessionFilesPayloadForTest({
        session: '1',
        repos: [{repo: '/home/test/project/1'}, {repo: '/home/test/project/2'}],
        files: [
          {repo: '/home/test/project/1', path: 'src/app.js', abs_path: '/home/test/project/1/src/app.js'},
          {repo: '/home/test/project/2', path: 'README.md', abs_path: '/home/test/project/2/README.md'},
        ],
      });
      assert.deepStrictEqual(canonical(api.fileExplorerSyncPlanForTest('1')), {
        session: '1',
        root: '/home/test/project',
        expandPaths: [
          '/home/test/project/1',
          '/home/test/project/2',
        ],
        affectedDirs: [
          '/home/test/project/1',
          '/home/test/project/2',
          '/home/test/project/1/src',
        ],
      }, 'Finder sync opens the nearest common project root and expands the active tab working repos under it');

      api.setTranscriptInfoForTest('2', {
        project: {git: {cwd: '/tmp/outside/src', root: '/tmp/outside'}},
        selected_pane: {current_path: '/tmp/outside/src'},
      });
      api.setSessionFilesPayloadForTest({
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
      api.setSessionFilesPayloadForTest({
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
      assert.equal(api.fileExplorerRootForTest(), '/home/test/project', 'syncing a home tab opens the nearest common project root');
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [
        '/home/test/project/1',
        '/home/test/project/2',
      ], 'sync expands the working repo roots for the focused home tab');

      const collapseParent = new TestElement('collapse-parent');
      const collapseRow = new TestElement('collapse-row');
      collapseParent.appendChild(collapseRow);
      api.collapseDirectoryRowForTest(collapseRow, '/home/test/project/1', {manual: true});
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [
        '/home/test/project/2',
      ], 'manual collapse removes the collapsed folder from the active tab expanded set');

      api.setTranscriptInfoForTest('2', {
        project: {git: {cwd: '/home/test/project/3', root: '/home/test/project/3'}},
        selected_pane: {current_path: '/home/test/project/3'},
      });
      api.setSessionFilesPayloadForTest({
        session: '2',
        repos: [{repo: '/home/test/project/3'}],
        files: [{repo: '/home/test/project/3', path: 'README.md', abs_path: '/home/test/project/3/README.md'}],
      });
      api.scheduleFileExplorerActiveTabSyncForTest('2', {explicit: true});
      await flushAsyncWork();
      await flushAsyncWork();
      assert.deepStrictEqual(canonical(api.fileExplorerExpandedForTest()), [], 'focusing another tab swaps to the single-repo root without carrying previous expanded folders');

      api.setSessionFilesPayloadForTest({
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

      const today = new Date();
      const generatedName = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}-001.png`;
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

    // Markdown editor image paste uploads beside the Markdown file and inserts relative image links into CodeMirror.
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
            {path: '/repo/docs/.uploads/one.png', relative_path: '.uploads/one.png'},
            {path: '/repo/docs/.uploads/two file.png', relative_path: '.uploads/two file.png'},
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
      assert.equal(content, 'hello\n![image](.uploads/one.png)\n![image](.uploads/two%20file.png)', 'Markdown editor paste inserts relative image links at the cursor');
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
      assert.ok(/document\.addEventListener\('paste', event => \{\s*if \(!dataTransferHasImagePayload\(event\.clipboardData\)\) return;[\s\S]*markdownEditorPasteTarget\(event\)/.test(imgSource), '78.6: the document paste handler claims via the shared dataTransferHasImagePayload detector before editor or terminal routing');
      assert.ok(imgSource.includes('function hasUploadableDrag(event)') && /addEventListener\('drop', event => \{\s*if \(!hasUploadableDrag\(event\)\) return;/.test(imgSource), '78.6: the file-drop handler claims via hasUploadableDrag (file OR image rich-data)');
      assert.ok(imgSource.includes('function dataTransferImageFiles(dt)') && imgSource.includes('function dataTransferHasImagePayload(dt)'), '78.6: the shared image-payload parent exists');
      assert.ok(/const files = dataTransferImageFiles\(event\.clipboardData\);[\s\S]*uploadEditorFiles\(editorTarget, files\)/.test(imgSource), '78.6: Markdown editor paste uploads through the shared image-payload extractor');
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
      api.setDocumentVisibilityForTest('hidden');
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, entries: [{name: 'visible.txt', kind: 'file'}]},
          })),
        }));
      });
      assert.equal(await api.fetchDirectoryForTest('/home/hidden'), null, 'hidden pages skip background Finder directory fetches');
      assert.deepStrictEqual(canonical(calls), [], 'hidden background Finder fetches do not enqueue /api/fs/batch');

      const userFetch = api.fetchDirectoryForTest('/home/hidden', {user: true});
      await api.flushFileExplorerFsBatchForTest();
      assert.equal((await userFetch)[0].name, 'visible.txt');
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [{id: 1, path: '/home/hidden', type: 'list'}],
        url: '/api/fs/batch',
      }], 'explicit user Finder fetches bypass hidden-background suppression');
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
      const api = loadYolomux('', ['1']);
      const calls = [];
      api.setFileExplorerRootForTest('/repo');
      api.setFileExplorerExpandedForTest(['/repo/src', '/repo/src/js', '/repo/tests']);
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: (body.requests || []).map(request => ({
            id: request.id,
            ok: true,
            status: 200,
            payload: {path: request.path, entries: [{name: 'child', kind: 'file'}]},
          })),
        }));
      });
      const entriesPromise = api.fileExplorerEntriesByWatchedDirectoryForTest('/repo');
      await api.flushFileExplorerFsBatchForTest();
      const entriesByDir = await entriesPromise;
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [
          {id: 1, path: '/repo', type: 'list'},
          {id: 2, path: '/repo/src', type: 'list'},
          {id: 3, path: '/repo/src/js', type: 'list'},
          {id: 4, path: '/repo/tests', type: 'list'},
        ],
        url: '/api/fs/batch',
      }], 'watched Finder/Differ directories prefetch in one fs batch instead of one POST per expanded directory');
      assert.deepStrictEqual(canonical(Array.from(entriesByDir.keys()).sort()), ['/repo', '/repo/src', '/repo/src/js', '/repo/tests']);
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
      const deleteCalls = [];
      const runDelete = async ({path, kind = 'dir', count = 0, confirmResponses = [true], fetchRejectsCount = false, role = 'admin'}) => {
        const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', role, {fireAllTimeouts: true});
        const confirms = [];
        api.setConfirmForTest(message => {
          confirms.push(String(message));
          return confirmResponses.length ? confirmResponses.shift() : true;
        });
        api.setFetchForTest((url, options = {}) => {
          const text = String(url || '');
          deleteCalls.push({url: text, method: options.method || 'GET', body: options.body || ''});
          if (text.startsWith('/api/fs/count')) {
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
      assert.equal(result.calls.some(call => call.url.startsWith('/api/fs/count')), false, 'single file delete does not fetch a directory count');
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
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/dynamo4/lib/llm/src'}});
      const lines = [terminalLine('protocols/openai/chat_completions/qwen3_coder_v2.rs')];
      const term = {cols: 100, rows: 10, buffer: {active: {viewportY: 0, getLine: index => lines[index] || null}}};
      const calls = [];
      api.setFetchForTest((url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        calls.push({url: String(url), method: options.method || 'GET', requests: body.requests || []});
        return Promise.resolve(jsonResponse({
          responses: body.requests.map(request => ({
            id: request.id,
            ok: true,
            payload: {kind: 'file', name: 'qwen3_coder_v2.rs', path: request.path},
          })),
        }));
      });
      const providerPromise = api.terminalReferenceProviderLinks('1', term, 1);
      await api.flushFileExplorerFsBatchForTest();
      const links = await providerPromise;
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [{id: 1, path: '/home/test/dynamo4/lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs', type: 'info'}],
        url: '/api/fs/batch',
      }], 'terminal qwen-style file refs confirm existence against the active pane cwd');
      assert.equal(links.length, 1, 'confirmed terminal file refs are exposed to xterm as visual decorations');
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
        requestCount += body.requests.length;
        return Promise.resolve(jsonResponse({
          responses: body.requests.map(request => ({id: request.id, ok: false, status: 404, error: 'not found'})),
        }));
      });
      const firstTarget = api.terminalFileReferenceTarget('1', fileRef, {fresh: false});
      const concurrentTarget = api.terminalFileReferenceTarget('1', fileRef, {fresh: false});
      assert.equal(await firstTarget, null, 'missing passive terminal file refs resolve to null');
      assert.equal(await concurrentTarget, null, 'concurrent missing terminal file refs share the in-flight resolution');
      const requestsAfterFirstResolution = requestCount;
      assert.ok(requestsAfterFirstResolution > 0, 'the first missing terminal file ref checks its context-derived paths');
      assert.equal(await api.terminalFileReferenceTarget('1', fileRef, {fresh: false}), null, 'negative terminal file target results are cached');
      assert.equal(requestCount, requestsAfterFirstResolution, 'repeated passive missing-file scans perform no backend lookups');
      assert.equal(api.terminalFileReferenceTargetCacheSizeForTest(), 1, 'one missing terminal file token occupies one bounded target-cache entry');
      assert.equal(await api.terminalFileReferenceTarget('1', fileRef), null, 'fresh user resolution still reports the missing target');
      assert.ok(requestCount > requestsAfterFirstResolution, 'fresh context-menu resolution bypasses a passive cached miss');
    }

    {
      const api = loadYolomux('', ['1']);
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/cache-lru'}});
      let requestCount = 0;
      api.setFetchForTest((_url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        requestCount += body.requests.length;
        return Promise.resolve(jsonResponse({
          responses: body.requests.map(request => ({
            id: request.id,
            ok: true,
            payload: {kind: 'file', name: request.path.split('/').pop(), path: request.path},
          })),
        }));
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
          settingsPayload: {defaults: DEFAULT_TEST_SETTINGS, settings: {yoagent: {backend: 'claude'}}, mtime_ns: 1},
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
          settingsPayload: {defaults: DEFAULT_TEST_SETTINGS, settings: {yoagent: {backend: 'claude'}}, mtime_ns: 1},
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
      await Promise.resolve();
      assert.equal(api.yoagentActiveChatRequestForTest(), null, 'active cancel frees the composer immediately');
      assert.ok(api.yoagentChatHtml().includes('Stopped.'), 'active cancel leaves a stopped message state');
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
      assert.equal((html.match(/<details class="[^"]*yoagent-message-details/g) || []).length, 2, 'thinking and tool-call stream rows remain independently collapsible');
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
      assert.equal((html.match(/<details class="[^"]*yoagent-message-details/g) || []).length, 1, 'adjacent thinking stream rows coalesce into one collapsible');
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

    test('server/client version mismatch asks whether to reload the browser', async () => {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          settingsPayload: {
            defaults: {general: {reload_on_update: true, reload_on_update_auto: false}},
            settings: {},
            mtime_ns: 1,
          },
        },
      });
      api.maybeHandleServerVersionChangeForTest('0.4.19');
      const banner = api.bodyChildren().find(node => node.id === 'serverUpdateBanner');
      assert.ok(banner, 'server/client patch rollback mismatch shows the existing reload banner');
      assert.equal(banner.dataset.version, '0.4.19', 'reload banner stores the mismatched server version');
      assert.ok(banner.children[0].textContent.includes('Do you want to reload the browser?'), 'reload banner asks the user whether to reload');
      assert.equal(banner.children[1].textContent, 'Reload', 'reload banner keeps the existing Reload action');
      assert.equal(banner.children[2].textContent, 'Keep', 'reload banner keeps the existing dismiss action as Keep');
      api.maybeHandleServerVersionChangeForTest('0.4.19');
      assert.equal(api.bodyChildren().filter(node => node.id === 'serverUpdateBanner').length, 1, 'same mismatched version does not spawn repeated banners');
      banner.children[2].listeners.get('click')[0]();
      assert.equal(api.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'Keep dismisses the mismatch banner');
      api.maybeHandleServerVersionChangeForTest('0.4.19');
      assert.equal(api.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'dismissed same mismatch does not immediately reopen');

      const reloadApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          settingsPayload: {
            defaults: {general: {reload_on_update: true, reload_on_update_auto: false}},
            settings: {},
            mtime_ns: 1,
          },
        },
      });
      reloadApi.maybeHandleServerVersionChangeForTest('0.4.21');
      const reloadBanner = reloadApi.bodyChildren().find(node => node.id === 'serverUpdateBanner');
      reloadBanner.children[1].listeners.get('click')[0]();
      assert.equal(reloadApi.reloadCountForTest(), 1, 'Reload action reloads the browser');

      const autoApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          settingsPayload: {
            defaults: {general: {reload_on_update: true, reload_on_update_auto: true}},
            settings: {},
            mtime_ns: 1,
          },
        },
      });
      autoApi.setOpenFileStateForTest('/repo/app.py', {kind: 'text', content: 'dirty', original: 'clean', dirty: true});
      autoApi.maybeHandleServerVersionChangeForTest('0.4.21');
      assert.equal(autoApi.reloadCountForTest(), 0, 'dirty editors block automatic reload on server/client mismatch');
      assert.ok(autoApi.bodyChildren().some(node => node.id === 'serverUpdateBanner'), 'dirty auto-reload fallback still shows the existing reload banner');
    });

    test('server/client bundle revision mismatch asks whether to reload the browser', async () => {
      const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          clientRevision: 'old-client-rev',
          settingsPayload: {
            defaults: {general: {reload_on_update: true, reload_on_update_auto: false}},
            settings: {},
            mtime_ns: 1,
          },
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
          settingsPayload: {
            defaults: {general: {reload_on_update: true, reload_on_update_auto: true}},
            settings: {},
            mtime_ns: 1,
          },
        },
      });
      autoApi.maybeHandleServerVersionChangeForTest('0.4.20', 'new-client-rev');
      assert.equal(autoApi.reloadCountForTest(), 1, 'safe automatic reload fires on same-version bundle revision mismatch');

      const disabledApi = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'admin', {
        bootstrapOverrides: {
          version: '0.4.20',
          clientRevision: 'old-client-rev',
          settingsPayload: {
            defaults: {general: {reload_on_update: false, reload_on_update_auto: true}},
            settings: {},
            mtime_ns: 1,
          },
        },
      });
      disabledApi.maybeHandleServerVersionChangeForTest('0.4.20', 'new-client-rev');
      assert.equal(disabledApi.reloadCountForTest(), 0, 'disabled reload-on-update suppresses bundle revision auto reload');
      assert.equal(disabledApi.bodyChildren().some(node => node.id === 'serverUpdateBanner'), false, 'disabled reload-on-update suppresses bundle revision banner');
    });

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

    test('self-update: restart polling record replaces timers and terminates one attempt', async () => {
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
      api.startSelfUpdateReloadPollingForTest('0.4.21');
      assert.deepStrictEqual(canonical(api.selfUpdateReloadStateForTest()), {
        attempts: 0,
        deferredToastShown: false,
        pending: true,
        serverVersionReloadHandled: '0.4.21',
        target: '0.4.21',
      }, 'a new attempt resets the complete record and owns the latest target');
      assert.deepStrictEqual(cleared, [1], 'restarting polling clears the prior attempt timer');
      assert.equal(timers.at(-1).delay, 0, 'replacement polling is scheduled immediately');

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
}

module.exports = {runLayoutAsyncSuite};

if (require.main === module) {
  runSuites([runLayoutAsyncSuite]);
}

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
  deferredFetch,
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
  await testAsync('share viewers retain local diagnostics without scheduling unscoped host requests', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly', {
      share: {view: true, id: 'share-diagnostic-capability', mode: 'ro', session: '1', sessions: ['1']},
      fireTimeoutDelays: [10000],
    });
    const requests = [];
    api.setFetchForTest(async (input, options = {}) => {
      requests.push({url: String(input), method: String(options.method || 'GET')});
      return {ok: true, status: 200, clone() { return this; }, json: async () => ({status: 'none'})};
    });

    assert.equal(api.clientCanUseUnscopedHostRequestsForTest(), false);
    await api.refreshTmuxStatusModeForTest('1');
    api.recordJsDebugEventForTest('error', {
      level: 'error',
      message: 'share-local diagnostic',
      source: '/ws/share-view',
      eventType: 'share-view',
      deliveryOutcome: 'failed',
    });
    await new Promise(resolve => setImmediate(resolve));

    assert.deepStrictEqual(requests, [], 'share viewers never request host-only tmux status or diagnostic upload routes');
    assert.ok(api.jsDebugEventsForTest().some(event => event.message === 'share-local diagnostic'), 'the diagnostic remains locally observable');
    assert.equal(api.debugEventCountsForTest().errors, 1, 'the local diagnostic remains visible to the stats summary');
    assert.deepStrictEqual(
      canonical(api.jsDebugCurrentObservationStateForTest()),
      {queue: 0, receipts: 0, timerPending: false, livenessTimerPending: false},
      'an ineligible share viewer creates no upload queue, pending receipt, or cadence timer',
    );
  });

  await testAsync('a share-scoped terminal close issues no host-only /api/event or /api/tmux-session-exists', async () => {
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly', {
      share: {view: true, id: 'share-host-poller-guard', mode: 'ro', session: '1', sessions: ['1']},
    });
    const requests = [];
    api.setFetchForTest((url, options = {}) => {
      requests.push({url: String(url), method: String(options.method || 'GET')});
      return Promise.resolve(jsonResponse({exists: false}));
    });
    api.setShowToastForTest(() => {});
    assert.equal(api.clientCanUseUnscopedHostRequestsForTest(), false, 'a read-only share viewer is not eligible for host-only requests');
    const term = {write() {}, dispose() {}};
    const item = api.registerTerminalForTest('1', term, {readyState: WebSocket.CLOSED, close() {}});
    api.connectTerminalSocketForTest('1', item);
    const socket = item.socket;
    // A transient abnormal close (1006, not clean) is not "final": it drives the reconnect/confirm path,
    // which for a host-scoped viewer would POST /api/event (terminal_disconnected) and GET
    // /api/tmux-session-exists. A share viewer must issue NEITHER -- both are host-only (share_access=none)
    // and the server returns 403 for a share token; a real 403 during teardown fails the strict browser
    // server-log-ring gate. Both producers are gated on the shared clientCanUseUnscopedHostRequests() owner.
    socket.onclose?.({target: socket, code: 1006, wasClean: false});
    await flushAsyncWork();
    assert.equal(requests.some(request => request.url.includes('/api/event')), false, 'a share viewer never posts terminal_disconnected to the host-only /api/event route');
    assert.equal(requests.some(request => request.url.includes('/api/tmux-session-exists')), false, 'a share viewer never checks the host-only tmux roster');
  });

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

  await testAsync('Finder keeps one batch pending until its operation terminal SSE result', async () => {
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
    const list = api.fetchDirectoryForTest('/home/test/one', {fresh: true});
    const info = api.fetchFilePathInfoForTest('/home/test/two', {fresh: true});
    const flush = api.flushFileExplorerFsBatchForTest();
    await flushAsyncWork();
    let settled = false;
    Promise.all([list, info]).then(() => { settled = true; });
    await flushAsyncWork();
    assert.equal(settled, false, 'the 202 receipt remains pending without direct-request fallback');

    api.handleClientPushEventNowForTest('operation_terminal', {
      operation: {id: 'op-fs-batch', cursor: {epoch: 'epoch', seq: 1}},
      result: {
        state: 'ready',
        request: {id: 'r-fs-batch'},
        data: {
          responses: [
            {id: 1, ok: true, status: 200, payload: {path: '/home/test/one', entries: [{name: 'one.txt', kind: 'file'}]}},
            {id: 2, ok: true, status: 200, payload: {path: '/home/test/two', kind: 'file'}},
          ],
        },
        quality: {complete: true, stale: false},
        warnings: [],
      },
    });

    assert.equal((await list)[0].name, 'one.txt');
    assert.equal((await info).kind, 'file');
    await flush;
  });

  await testAsync('operation receipts reuse the shared client-event stream', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
    const initialSource = api.clientEventTransportStateForTest().source;
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
    assert.equal(sharedSource, initialSource, 'adding a pending operation preserves the serving stream until its replacement is ready');
    assert.notEqual(replacementSource, initialSource, 'adding a pending operation opens a replacement shared stream');
    assert.equal(new URL(replacementSource.url, 'https://yolomux.test').searchParams.get('operations'), 'op-shared-operation');
    replacementSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    assert.equal(initialSource.readyState, 2, 'the old stream closes only after the replacement subscriber is serving');
    assert.equal(api.clientEventTransportStateForTest().source, replacementSource);
    assert.equal(api.apiOperationStateForTest().pending, 1);

    replacementSource.listeners.get('operation_terminal')[0]({
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
    assert.equal(api.clientEventTransportStateForTest().source, replacementSource, 'settling a receipt does not reconnect the shared stream solely to remove a replay fence');
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

  await testAsync('replacement demand drops operations completed while its subscriber opens', async () => {
    const api = loadYolomux('', ['1']);
    api.installClientEventStreamForTest();
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
    const firstOperationSource = api.clientEventTransportStateForTest().replacementSource;
    firstOperationSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    api.registerApiOperationReceiptForTest(receipt('op-second'));
    const staleReplacement = api.clientEventTransportStateForTest().replacementSource;
    assert.equal(new URL(staleReplacement.url, 'https://yolomux.test').searchParams.get('operations'), 'op-first,op-second');

    firstOperationSource.listeners.get('operation_terminal')[0]({
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
    staleReplacement.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});

    const currentReplacement = api.clientEventTransportStateForTest().replacementSource;
    assert.equal(staleReplacement.readyState, 2, 'the stale replacement closes without taking ownership');
    assert.equal(api.clientEventTransportStateForTest().source, firstOperationSource, 'the serving stream remains active during repair');
    assert.notEqual(currentReplacement, staleReplacement, 'current pending demand opens one corrected replacement');
    assert.equal(new URL(currentReplacement.url, 'https://yolomux.test').searchParams.get('operations'), 'op-second');
    currentReplacement.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    assert.equal(firstOperationSource.readyState, 2, 'the old serving stream closes only after corrected demand is ready');
    assert.equal(api.clientEventTransportStateForTest().source, currentReplacement);
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
          const replacementSource = api.clientEventTransportStateForTest().replacementSource;
          assert.ok(replacementSource, 'the pending operation creates a replacement shared stream');
          assert.equal(new URL(replacementSource.url, 'https://yolomux.test').searchParams.get('operations'), operationId);
          replacementSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
          if (timing === 'native-reconnect') {
            assert.equal(api.clientEventTransportStateForTest().source, replacementSource, 'the demanded replacement becomes serving');
            replacementSource.onerror();
            assert.equal(api.clientEventTransportStateForTest().connected, false, 'native reconnect starts on the serving source');
            replacementSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
            assert.equal(api.clientEventTransportStateForTest().source, replacementSource, 'native ready reuses the same EventSource');
          }
          replacementSource.listeners.get('operation_terminal')[0]({
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

    // A changed demand opens a CANDIDATE that has not yet fired ready.
    api.registerApiOperationReceiptForTest({
      request: {id: 'r-candidate'},
      operation: {
        id: 'op-candidate',
        kind: 'fs_watch_diff',
        status_url: '/api/operations/op-candidate',
        events_url: '/api/client-events?operation_id=op-candidate',
        cursor: {epoch: 'candidate-epoch', seq: 0},
      },
    });
    const candidate = api.clientEventTransportStateForTest().replacementSource;
    assert.ok(candidate && candidate !== activeSource, 'a candidate stream opens for the changed demand');
    assert.equal(new URL(candidate.url, 'https://yolomux.test').searchParams.get('operations'), 'op-candidate');
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
    assert.equal(new URL(freshCandidate.url, 'https://yolomux.test').searchParams.get('operations'), 'op-candidate', 'the fresh candidate still carries the demanded operation');
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
    advance(14999);
    await flushAsyncWork();
    assert.equal(api.apiOperationStateForTest().pending, 1, '14,999 ms remains inside the caller deadline');
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
    assert.equal(failures[0].error, 'deadline_expired: request exceeded its 15s deadline');
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

  await testAsync('session retirement detaches the last share waiter without closing operation ownership', async () => {
    const shareToken = 'retired session share token';
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly', {
      share: {view: true, id: 'share-retired-session', mode: 'ro', session: '1', sessions: ['1']},
      locationHash: `#t=${encodeURIComponent(shareToken)}`,
    });
    const controller = new AbortController();
    const receipt = {
      state: 'queued',
      request: {id: 'r-retired-session'},
      operation: {
        id: 'op-retired-session',
        kind: 'filesystem_operation',
        context: {operation: 'diff', path: '/repo/retired.txt', session: '1'},
        events_url: '/api/client-events?operation_id=op-retired-session',
        cursor: {epoch: 'retired-session-epoch', seq: 0},
      },
    };
    const record = api.registerApiOperationReceiptForTest(receipt);
    const source = record.source;
    const waiter = api.waitForApiOperationResultForTest(receipt, {
      kind: 'filesystem_operation',
      operation: 'diff',
      signal: controller.signal,
    }).then(() => null, error => error);
    controller.abort(new DOMException('session retired', 'AbortError'));
    assert.equal((await waiter)?.name, 'AbortError');
    assert.equal(api.apiOperationStateForTest().pending, 1, 'session retirement cannot retire the backend operation');
    assert.equal(api.apiOperationStateForTest().waiters, 0);
    assert.equal(record.source, source, 'the exact-ID transport remains owned without a UI waiter');
    assert.notEqual(source.readyState, 2, 'the exact-ID transport remains open');
    source.listeners.get('operation_terminal')[0]({
      data: JSON.stringify({
        type: 'operation_terminal',
        payload: {
          operation: {id: 'op-retired-session', cursor: {epoch: 'retired-session-epoch', seq: 1}},
          result: {state: 'ready', data: {diff: 'retained'}},
          status: 200,
        },
      }),
      type: 'operation_terminal',
      lastEventId: '',
    });
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().terminal, 1);
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1);
    assert.equal(source.readyState, 2, 'the exact-ID transport closes only after terminal settlement');
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
      share: {view: true, id: 'share-generation-operation', mode: 'ro', session: '1', sessions: ['1']},
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
    const source = record.source;
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
    assert.equal(source.readyState, 2, 'the exact-ID EventSource closes at terminal settlement');

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
    assert.ok(replacementSource, 'discarding a mismatched terminal still opens a replacement shared stream');
    assert.equal(new URL(replacementSource.url, 'https://yolomux.test').searchParams.get('operations'), operationId, 'the replacement stream demands the exact operation ID');
    replacementSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    assert.equal(initialSource.readyState, 2, 'the original shared stream closes only after its replacement is ready');
    replacementSource.listeners.get('operation_terminal')[0]({
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

  await testAsync('a pre-receipt wrong epoch still opens the exact share-token operation stream', async () => {
    const shareToken = 'share operation token';
    const api = loadYolomux('', ['1'], 'http:', 'Linux x86_64', 'readonly', {
      share: {view: true, id: 'share-operation', mode: 'ro', session: '1', sessions: ['1']},
      locationHash: `#t=${encodeURIComponent(shareToken)}`,
      fireTimeoutDelays: [25],
    });
    const acknowledgmentRequests = [];
    api.setFetchForTest((url, options = {}) => {
      acknowledgmentRequests.push({url: String(url), options});
      const body = JSON.parse(options.body || '{}');
      return Promise.resolve(jsonResponse({ok: true, acknowledged: body.acks.map(item => item.id)}));
    });
    const operationId = 'op-wrong-epoch-share-token';
    const eventsUrl = `/api/client-events?operation_id=${operationId}`;
    const receipt = {
      state: 'queued',
      request: {id: `r-${operationId}`},
      operation: {
        id: operationId,
        kind: 'filesystem_operation',
        context: {operation: 'read', path: '/tmp/share-token.txt'},
        events_url: eventsUrl,
        cursor: {epoch: 'expected-share-token', seq: 0},
      },
    };
    assert.equal(api.applyApiOperationTerminalForTest({
      operation: {id: operationId, cursor: {epoch: 'wrong-share-token', seq: 1}},
      result: {state: 'ready', data: {wrong: true}},
      status: 200,
    }), true, 'an unknown wrong-epoch terminal is retained before share receipt admission');
    let terminalEvents = 0;
    api.addWindowEventListenerForTest('yolomux:operation-terminal', () => { terminalEvents += 1; });
    api.clearJsDebugEventsForTest();
    const record = api.registerApiOperationReceiptForTest(receipt);
    assert.equal(api.apiOperationTerminalForTest(operationId), null, 'share receipt admission removes the mismatched retained terminal');
    const resultPromise = api.waitForApiOperationResultForTest(receipt, {kind: 'filesystem_operation', operation: 'read'});
    assert.ok(record.source, 'discarding a mismatched terminal still opens the feature-local share stream');
    assert.equal(record.source.url, `${eventsUrl}&token=${encodeURIComponent(shareToken)}`, 'the feature-local source uses the exact receipt URL and share token');
    record.source.listeners.get('operation_terminal')[0]({
      data: JSON.stringify({
        type: 'operation_terminal',
        payload: {
          operation: {id: operationId, cursor: {epoch: 'expected-share-token', seq: 1}},
          result: {state: 'ready', data: {transport: 'share'}},
          status: 200,
        },
      }),
      type: 'operation_terminal',
      lastEventId: '',
    });
    assert.deepStrictEqual(canonical(await resultPromise), {transport: 'share'});
    assert.equal(canonical(api.apiOperationTerminalForTest(operationId).operation.cursor).epoch, 'expected-share-token');
    assert.equal(terminalEvents, 1, 'the share terminal dispatches once');
    assert.equal(api.jsDebugEventsForTest().filter(event => event.type === 'operation_wait').length, 1, 'the share terminal records one telemetry row');
    assert.equal(api.apiOperationStateForTest().handlerInvocations, 1, 'the share terminal invokes the feature handler once');
    assert.equal(api.apiOperationStateForTest().pending, 0);
    assert.equal(api.apiOperationStateForTest().waiters, 0);
    await flushAsyncWork();
    assert.equal(acknowledgmentRequests.length, 1, 'share completion sends one application-level acknowledgment');
    assert.equal(acknowledgmentRequests[0].url, '/api/operations/ack');
    const acknowledgmentHeaders = acknowledgmentRequests[0].options.headers;
    const shareHeader = typeof acknowledgmentHeaders?.get === 'function'
      ? acknowledgmentHeaders.get('X-Share-Token')
      : acknowledgmentHeaders?.['X-Share-Token'];
    assert.equal(shareHeader, shareToken, 'the acknowledgment preserves the share-token transport boundary');
  });

  await testAsync('every ordinary filesystem route shares one cold receipt await owner', async () => {
    const operations = [
      ['list', '/api/fs/list?path=%2Ftmp'],
      ['search', '/api/fs/search?root=%2Ftmp&query=x'],
      ['index_status', '/api/fs/index-status?root=%2Ftmp'],
      ['read', '/api/fs/read?path=%2Ftmp%2Fa'],
      ['info', '/api/fs/info?path=%2Ftmp%2Fa'],
      ['diff', '/api/fs/diff?path=%2Ftmp%2Fa'],
      ['blame', '/api/blame?path=%2Ftmp%2Fa'],
      ['count', '/api/fs/count?path=%2Ftmp'],
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
    assert.equal(api.currentFileStateForTest(path).content, 'original\n');

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
    assert.equal(receipts.length, 3, 'open, diff, and save each issue one request');
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
      if (target.includes('/api/fs/search')) {
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
      if (target.includes('/api/fs/search')) {
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
        assert.equal(String(url), '/api/fs/batch');
        const requests = JSON.parse(options.body || '{}').requests || [];
        const batch = {...deferredFetch(), requests};
        batches.push(batch);
        return batch.promise;
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
      const coalescedList = resource.type === 'list';
      assert.equal(batches.length, coalescedList ? 1 : 2, `${resource.type}: list fan-out is coalesced while independent info reads retain stale-generation coverage`);
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
    const source = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
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
      const request = {
        ...deferredFetch(),
        requests: JSON.parse(options.body || '{}').requests || [],
      };
      pending.push(request);
      return request.promise;
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
    const api = loadYolomux('', ['1', '2']);
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
    assert.equal(fetchCalls, 0, 'restoring a collapsed cursor adds no blocking fetch');
  });

  await testAsync('Finder Sync warm session switches render synchronously from the bounded listing cache', async () => {
    const api = loadYolomux('', ['1', '2']);
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
    api.setFetchForTest((url, options = {}) => {
      assert.equal(String(url), '/api/fs/batch');
      const items = JSON.parse(options.body).requests;
      requests.push(items.map(item => item.path));
      return Promise.resolve(jsonResponse({
        responses: items.map(item => ({
          id: item.id,
          ok: true,
          status: 200,
          payload: {entries: [{name: 'new.txt', kind: 'file'}]},
        })),
      }));
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
    assert.equal(requests.length, 1, 'background listings settle in one batched request after the frame');
    assert.deepStrictEqual([...new Set(requests[0])], ['/repo'], 'revalidation is scoped to the visible cached directory');
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
    api.setFetchForTest((_url, options = {}) => {
      const items = JSON.parse(options.body).requests;
      return Promise.resolve(jsonResponse({responses: items.map(item => ({
        id: item.id, ok: true, status: 200, payload: {entries: [{name: 'same.txt', kind: 'file'}]},
      }))}));
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

  await testAsync('Finder Sync cold listings start in bounded parallel batches and share the LRU bound', async () => {
    const api = loadYolomux('', ['1']);
    const directories = Array.from({length: 12}, (_value, index) => `/cold/${index}`);
    const batches = [];
    api.setFetchForTest((url, options = {}) => {
      assert.equal(String(url), '/api/fs/batch');
      const items = JSON.parse(options.body).requests;
      batches.push(items.map(item => item.path));
      return Promise.resolve(jsonResponse({
        responses: items.map(item => ({id: item.id, ok: true, status: 200, payload: {entries: []}})),
      }));
    });
    const listings = await api.fetchFileExplorerSyncListingsForTest(directories, {force: true});
    assert.equal(listings.size, directories.length, 'every cold directory settles');
    assert.equal(batches[0].length, 8, 'the first response is awaited only after all eight bounded workers have started');
    assert.deepStrictEqual(batches.map(batch => batch.length), [8, 4], 'twelve cold listings take two bounded batches rather than twelve sequential round trips');

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

  await testAsync('a mass re-list is split at the bound the server states instead of posted whole and refused', async () => {
    // Keiven's Differ pointed at a worktree deleted the day before, which re-lists every open
    // directory at once. The flush drained the whole queue into ONE body, and the server refuses a
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
          payload: {path: request.path, entries: [{name: `${request.id}.txt`, kind: 'file'}]},
        })),
      }));
    });

    const paths = Array.from({length: 130}, (_, index) => `/home/test/mass/${index}`);
    const listings = paths.map(path => api.fetchDirectoryForTest(path, {fresh: true}));
    const flush = await api.flushFileExplorerFsBatchForTest();
    assert.equal(flush.ok, true, 'a 130-path re-list succeeds instead of being refused');
    assert.deepStrictEqual(bodies.map(requests => requests.length), [64, 64, 2], 'the queue is split into consecutive slices no larger than the stated bound');
    assert.equal(bodies.some(requests => requests.length > 64), false, 'no body reaches the server above the bound it refuses');
    assert.deepStrictEqual(bodies.flat().map(request => request.path), paths, 'chunking preserves queue order across chunk boundaries');
    const entries = await Promise.all(listings);
    assert.deepStrictEqual(
      entries.map(entry => (Array.isArray(entry) ? entry.map(row => row.name) : entry)),
      bodies.flat().map(request => [`${request.id}.txt`]),
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
          responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: [{name: 'ok.txt', kind: 'file'}]}})),
        }));
      }
      // The failed chunk falls back to one request per item; fail those too, so the two items it
      // owns end in their error state and the assertion below is about the siblings only.
      singles.push(text);
      return Promise.reject(new Error('single-item fallback failed'));
    });

    const paths = ['/home/test/chunk/a', '/home/test/chunk/b', '/home/test/chunk/c', '/home/test/chunk/d', '/home/test/chunk/e'];
    const listings = paths.map(path => api.fetchDirectoryForTest(path, {fresh: true}));
    const flush = await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(posts, [
      ['/home/test/chunk/a', '/home/test/chunk/b'],
      ['/home/test/chunk/c', '/home/test/chunk/d'],
      ['/home/test/chunk/e'],
    ], 'the chunks after the failed one are still posted, in queue order');
    assert.equal(flush.chunks, 3, 'the stated bound of 2 splits five queued paths into three chunks');
    assert.equal(flush.ok, false, 'the flush reports the failed chunk rather than hiding it');
    assert.equal(singles.length, 2, 'only the failed chunk falls back to per-item requests');
    const entries = await Promise.all(listings);
    assert.deepStrictEqual(
      entries.map(entry => (Array.isArray(entry) ? entry.map(row => row.name) : entry)),
      [null, null, ['ok.txt'], ['ok.txt'], ['ok.txt']],
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
        responses: requests.map(request => ({id: request.id, ok: true, status: 200, payload: {path: request.path, entries: [{name: 'ok.txt', kind: 'file'}]}})),
      }));
    });

    const paths = ['/home/test/unstated/a', '/home/test/unstated/b', '/home/test/unstated/c'];
    const listings = paths.map(path => api.fetchDirectoryForTest(path, {fresh: true}));
    await api.flushFileExplorerFsBatchForTest();
    assert.deepStrictEqual(bodies, [[paths[0]], [paths[1]], [paths[2]]], 'an unstated bound sends one item per request rather than a remembered 64');
    const entries = await Promise.all(listings);
    assert.deepStrictEqual(entries.map(entry => entry.map(row => row.name)), [['ok.txt'], ['ok.txt'], ['ok.txt']], 'every item still settles');
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

  await testAsync('Quick Open record aborts path listings and rejects close-reopen stale completions', async () => {
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

  test('editor field application has one normalizer for URL restore and share replay', () => {
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
    const fromUrl = loadYolomux('', ['1']);
    const urlModes = [];
    fromUrl.applyEditorStateFieldsForTest(editor, {applyModeEntry: entry => urlModes.push(entry)});
    const fromShare = loadYolomux('', ['1']);
    fromShare.applyShareEditorStateForTest(editor);
    assert.deepEqual(canonical(fromUrl.editorStateFieldsSnapshotForTest()), canonical(fromShare.editorStateFieldsSnapshotForTest()), 'URL and share apply every common editor field through the same normalizer');
    assert.deepEqual(canonical(urlModes), canonical(editor.modes), 'the shared normalizer delegates each per-file mode to its transport-specific owner');
  });

  await testAsync('session-files record lets an accepted push invalidate older HTTP work', async () => {
    const pending = [];
    const api = loadYolomux('', ['1']);
    const slots = api.emptyLayoutSlots();
    slots[api.layoutTreeKey] = api.splitNode('row', api.leafNode('left'), api.leafNode('right'));
    slots.left = api.paneStateWithTabs([api.differItemId], api.differItemId);
    slots.right = api.paneStateWithTabs(['1'], '1');
    api.setLayoutSlotsForTest(slots);
    api.setFileExplorerModeForTest('diff');
    api.setFileExplorerChangesSelectedSessionForTest('1');
    api.setFetchForTest(url => {
      assert.ok(String(url).startsWith('/api/session-files?'));
      const request = deferredFetch();
      pending.push(request);
      return request.promise;
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

    await api.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true});
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

    await api.fetchSessionFilesForTest({destination: 'finder', session: '1', silent: true, force: true});
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
      for (const name of ['serverWatchRootsSignature', 'serverWatchRootsInFlight', 'serverWatchRootsSyncedAt', 'serverWatchRootsTimer', 'serverWatchRootsPendingOptions']) {
        assert.equal(src.includes(name), false, `${name} remains retired`);
      }
      assert.ok(src.includes('const serverWatchRootsState = {'), 'one watch-root synchronization owner remains');
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
      assert.deepStrictEqual(canonical(visibleState.session_files), [{session: '1', hours: 24, from_ref: 'HEAD', to_ref: 'current', repo_refs: null}], 'visible Differ registers the current session-files request');
      visibleSlots.left = visibleApi.paneStateWithTabs([visibleApi.tabberItemId], visibleApi.tabberItemId);
      visibleApi.setLayoutSlotsForTest(visibleSlots);
      const tabberState = visibleApi.clientServerWatchStateForTest();
      assert.deepStrictEqual(canonical(tabberState.roots), [], 'visible Tabber does not inherit hidden Finder/Differ roots');
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
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tabs').badgeText, undefined, 'Tabs never shows a running-YOLO circle');
      await shareTopbarApi.applyShareUiStateForTest({autoApprove: autoSnapshot});
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tabs').badgeText, undefined, 'host UI state cannot restore the removed Tabs circle in a share viewer');
      assert.equal(shareTopbarApi.appMenuTree().find(menu => menu.id === 'tmux').items[0].label, 'YO (YOLO auto approve) tmux', 'share viewers mirror host tmux YO state from UI state');
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
      api.setSessionFilesPayloadForTest({
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
      const first = api.fetchDirectoryForTest('/home/test', {trigger: 'tree-render'});
      const second = api.fetchDirectoryForTest('/home/test/', {trigger: 'watch-diff-fallback'});
      await api.flushFileExplorerFsBatchForTest();
      assert.deepStrictEqual(canonical(calls), [{
        method: 'POST',
        requests: [{id: 1, path: '/home/test', trigger_counts: {'tree-render': 1, 'watch-diff-fallback': 1}, type: 'list'}],
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
      const batches = [];
      api.setFetchForTest((url, options = {}) => {
        assert.equal(String(url), '/api/fs/batch');
        const batch = {...deferredFetch(), requests: JSON.parse(options.body || '{}').requests || []};
        batches.push(batch);
        return batch.promise;
      });
      const path = '/home/test/bootstrap';
      const container = new TestElement('finder-tree');
      api.renderTreeChildrenForTest(container, '/home/test', [{name: 'bootstrap', kind: 'dir'}], 0);
      const row = container.children.find(node => node?.dataset?.path === path);
      const background = api.fetchDirectoryForTest(path, {trigger: 'tree-render'});
      const backgroundFlush = api.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      assert.equal(batches.length, 1, 'the bootstrap list is already in flight');
      const user = api.onFileTreeRowClick(row, path, {name: 'bootstrap', kind: 'dir'}, {});
      const userFlush = api.flushFileExplorerFsBatchForTest();
      await flushAsyncWork();
      assert.equal(batches.length, 2, 'a user list gets one successor batch instead of inheriting the bootstrap wait');
      assert.deepStrictEqual(canonical(batches.map(batch => batch.requests)), [
        [{id: 1, path: '/home/test/bootstrap', trigger_counts: {'tree-render': 1}, type: 'list'}],
        [{id: 2, path: '/home/test/bootstrap', trigger_counts: {'explicit-user': 1}, type: 'list'}],
      ]);
      batches[1].resolve(jsonResponse({responses: [{id: 2, ok: true, status: 200, payload: {entries: [{name: 'clicked.txt', kind: 'file'}]}}]}));
      await userFlush;
      await user;
      assert.equal(row.classList.contains('loading-children'), false, 'the click settles before the bootstrap response');
      batches[0].resolve(jsonResponse({responses: [{id: 1, ok: true, status: 200, payload: {entries: [{name: 'bootstrap.txt', kind: 'file'}]}}]}));
      await backgroundFlush;
      assert.equal((await background)[0].name, 'bootstrap.txt', 'the background request remains independently observable');
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
        requests: [{id: 1, path: '/home/hidden', trigger_counts: {'explicit-user': 1}, type: 'list'}],
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
        requests: [{id: 1, path: '/home/test', trigger_counts: {'fresh-repair': 2}, type: 'list'}],
        url: '/api/fs/batch',
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
          {id: 1, path: '/repo', trigger_counts: {'tree-render': 1}, type: 'list'},
          {id: 2, path: '/repo/src', trigger_counts: {'tree-render': 1}, type: 'list'},
          {id: 3, path: '/repo/src/js', trigger_counts: {'tree-render': 1}, type: 'list'},
          {id: 4, path: '/repo/tests', trigger_counts: {'tree-render': 1}, type: 'list'},
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
      const lines = [terminalLine('• Documented it in tests/SHARE_TEST_INVENTORY.md:123')];
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
        requests: [{id: 1, path: '/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md', trigger_counts: {'explicit-user': 1}, type: 'info'}],
        url: '/api/fs/batch',
      }], 'terminal file refs confirm existence through the shared fs info batch path');
      assert.deepStrictEqual(canonical(target), {
        info: {kind: 'file', name: 'SHARE_TEST_INVENTORY.md', path: '/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md'},
        line: 123,
        path: '/home/test/yolomux.dev3/tests/SHARE_TEST_INVENTORY.md',
        text: 'tests/SHARE_TEST_INVENTORY.md:123',
      }, 'confirmed terminal file refs carry the absolute path and line for the Open file menu action');
    }

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
        requests: [{id: 1, path: '/home/test/dynamo4/lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs', trigger_counts: {'explicit-user': 1}, type: 'info'}],
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
      api.setTranscriptInfoForTest('1', {selected_pane: {current_path: '/home/test/cache-ttl'}});
      let now = 1_000_000;
      let requestCount = 0;
      api.setFetchForTest((_url, options = {}) => {
        const body = JSON.parse(options.body || '{}');
        requestCount += body.requests.length;
        return Promise.resolve(jsonResponse({
          responses: body.requests.map(request => ({
            id: request.id,
            ok: request.path.endsWith('positive.js'),
            status: request.path.endsWith('positive.js') ? 200 : 404,
            payload: request.path.endsWith('positive.js') ? {kind: 'file', name: 'positive.js', path: request.path} : undefined,
            error: request.path.endsWith('positive.js') ? undefined : 'not found',
          })),
        }));
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
      const keyboardActions = ['escape', 'ctrl', 'interrupt', 'tab', 'tmux-prefix', 'backspace', 'copy', 'arrow-up', 'tmux-scroll-up', 'arrow-left', 'enter', 'arrow-right', 'command-v', 'arrow-down', 'tmux-scroll-down', 'shift', 'alt', 'cmd', 'command-p', 'home', 'end', 'delete', 'shift-tab', 'ctrl-d', 'ctrl-z', 'ctrl-l', 'ctrl-r'];
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
      assert.match(terminalBoot, /addEventListener\('copy', event => \{\s*copyTerminalSelectionToClipboardEvent/, 'the real terminal copy listener stays on the synchronous shared path');
    });
}

module.exports = {runLayoutAsyncSuite};

if (require.main === module) {
  runSuites([runLayoutAsyncSuite]);
}

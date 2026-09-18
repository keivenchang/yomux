const {
  assert,
  loadYolomux,
  flushAsyncWork,
  testAsync,
  runSuites,
} = require('./browser_helpers/layout_test_helper');

async function runOperationReceiptRepairSuite() {
  await testAsync('a dropped operation terminal repairs from its durable receipt stream', async () => {
    const api = loadYolomux('', ['1']);
    const sources = [];
    api.setEventSourceConstructorForTest(class {
      constructor(url) {
        this.url = String(url || '');
        this.readyState = 1;
        this.listeners = new Map();
        this.closeCount = 0;
        sources.push(this);
      }

      addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(listener);
      }

      close() {
        this.closeCount += 1;
        this.readyState = 2;
      }
    });
    api.installClientEventStreamForTest();
    const sharedSource = api.clientEventTransportStateForTest().source;
    sharedSource.listeners.get('ready')[0]({data: '{}', type: 'ready', lastEventId: ''});
    const operationId = 'op-overflow-replay';
    const eventsUrl = `/api/client-events?operation_id=${operationId}`;
    api.repairClientEventResourcesForTest(
      [`operation_terminal:${operationId}`],
      {epoch: 'server-a', resource_revisions: {[`operation_terminal:${operationId}`]: 1}},
    );
    const record = api.registerApiOperationReceiptForTest({
      request: {id: `r-${operationId}`},
      operation: {
        id: operationId,
        kind: 'fs_batch',
        status_url: `/api/operations/${operationId}`,
        events_url: eventsUrl,
        cursor: {epoch: 'operation-epoch', seq: 0},
      },
    });

    const replaySource = record.source || sources.at(-1);
    assert.ok(replaySource, 'the missed durable terminal owns one operation-scoped replay stream');
    assert.equal(replaySource.url, eventsUrl);
    assert.equal(api.clientEventTransportStateForTest().source, sharedSource, 'repair does not replace the serving global stream');
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
    await flushAsyncWork();

    assert.equal(api.apiOperationStateForTest().pending, 0, 'durable replay terminalizes the stranded receipt');
    assert.equal(record.source, null, 'terminalization retires the operation-scoped stream');
    assert.equal(replaySource.readyState, 2, 'the repair stream closes after the exact terminal');
  });

  await testAsync('pagehide closes an operation repair stream and preserves a retry marker', async () => {
    const api = loadYolomux('', ['1']);
    const sources = [];
    api.setEventSourceConstructorForTest(class {
      constructor(url) {
        this.url = String(url || '');
        this.readyState = 1;
        this.listeners = new Map();
        this.closeCount = 0;
        sources.push(this);
      }

      addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(listener);
      }

      close() {
        this.closeCount += 1;
        this.readyState = 2;
      }
    });
    api.installClientEventStreamForTest();
    const operationId = 'op-pagehide-replay';
    api.repairClientEventResourcesForTest([`operation_terminal:${operationId}`]);
    const record = api.registerApiOperationReceiptForTest({
      request: {id: `r-${operationId}`},
      operation: {id: operationId, kind: 'fs_batch', events_url: `/api/client-events?operation_id=${operationId}`, cursor: {epoch: 'e', seq: 0}},
    });
    const replaySource = record.source || sources.at(-1);
    for (const listener of api.windowListenersForTest('pagehide')) listener({type: 'pagehide'});
    assert.equal(replaySource.closeCount, 1, 'pagehide closes the operation repair stream');
    assert.equal(record.source, null, 'pagehide clears the operation stream owner');
    assert.equal(api.apiOperationStateForTest().pending, 1, 'pagehide leaves the receipt pending for retry');
  });

  await testAsync('a repair stream error retries after the shared stream reconnects', async () => {
    const api = loadYolomux('', ['1']);
    const sources = [];
    api.setEventSourceConstructorForTest(class {
      constructor(url) {
        this.url = String(url || '');
        this.readyState = 1;
        this.listeners = new Map();
        sources.push(this);
      }

      addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(listener);
      }

      close() {
        this.readyState = 2;
      }
    });
    api.installClientEventStreamForTest();
    const operationId = 'op-retry-replay';
    api.repairClientEventResourcesForTest([`operation_terminal:${operationId}`]);
    const record = api.registerApiOperationReceiptForTest({
      request: {id: `r-${operationId}`},
      operation: {id: operationId, kind: 'fs_batch', events_url: `/api/client-events?operation_id=${operationId}`, cursor: {epoch: 'e', seq: 0}},
    });
    const firstReplay = record.source || sources.at(-1);
    firstReplay.onerror?.();
    assert.equal(record.source, null, 'a failed repair stream releases its source');
    api.syncClientEventDemandForTest({immediate: true});
    const retry = record.source || sources.at(-1);
    assert.notEqual(retry, firstReplay, 'the next ready frame retries the durable replay');
  });
}

module.exports = {runOperationReceiptRepairSuite};

if (require.main === module) runSuites([runOperationReceiptRepairSuite]);

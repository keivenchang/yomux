// Regenerates tests/fixtures/stats_request_shapes.json from the REAL client owner.
// Two steps, both required, both from the repo root (the readerRequest dialect
// comes from the REAL python web-layer translator, never a hand-mapped JS mirror):
//   1. node tests/_gen_shapes.js
//   2. python3 tests/_gen_shapes_reader.py  (fills each case's readerRequest via
//      TmuxWebtermApp.stats_sample_history_query through the contract-tested mirror)
const {loadYolomux} = require('./layout_test_helper');
const fs = require('fs');
const api = loadYolomux('?debug=1&sessions=debug', ['1']);
const NOW = 1800000000; // fixed epoch for deterministic goldens
// Canonical per-range request shapes. There are NO token_* params anymore: token
// rates and cost ride every history record of the one history stream. One
// fresh-range history request per range plus the prefetch and backoff shapes.
const cases = [];
for (const rangeSeconds of [300, 900, 1800, 3600, 2 * 3600, 4 * 3600, 8 * 3600, 16 * 3600, 24 * 3600]) {
  const params = {
    since: 0, clientId: 'golden-client', tokenConsumer: '1',
    historyStart: NOW - rangeSeconds, historyEnd: NOW, historyResolution: 1,
    history: true,
  };
  cases.push({
    name: `fresh-range-${rangeSeconds}s`, rangeSeconds, params,
    query: api.jsDebugStatsSampleQueryForTest(params),
    readerRequest: null, // filled by tests/_gen_shapes_reader.py from the REAL translator
  });
}
const prefetchParams = {clientId: 'golden-client', historyStart: NOW - 24 * 3600, historyEnd: 0, historyResolution: 1};
cases.push({
  name: 'full-retention-prefetch', rangeSeconds: 24 * 3600, params: prefetchParams,
  query: api.jsDebugStatsSampleQueryForTest(prefetchParams),
  readerRequest: null,
});
const suppressedParams = {since: 42, clientId: 'golden-client', tokenConsumer: '0', historyStart: 0, historyEnd: 0, historyResolution: 1, history: false};
cases.push({name: 'history-suppressed-backoff', rangeSeconds: 0, params: suppressedParams, query: api.jsDebugStatsSampleQueryForTest(suppressedParams), readerRequest: null});
fs.writeFileSync(require('path').join(__dirname, 'fixtures', 'stats_request_shapes.json'), JSON.stringify({nowSeconds: NOW, cases}, null, 2) + '\n');
console.log('goldens written:', cases.length, 'cases (now run python3 tests/_gen_shapes_reader.py)');
console.log('sample:', cases[5].query);

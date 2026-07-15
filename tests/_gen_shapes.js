const {loadYolomux} = require('./layout_test_helper');
const fs = require('fs');
const api = loadYolomux('?debug=1&sessions=debug', ['1']);
const NOW = 1800000000; // fixed epoch for deterministic goldens
// Canonical per-range request shapes: token_resolution 0 below 4h, 120 at 4h..<16h, 300 at 16h+
// (debugGraphAgentTokenResolution). One fresh-range history request plus the prefetch shape.
const tokenResolutionFor = r => (r < 4 * 3600 ? 0 : (r >= 16 * 3600 ? 300 : 120));
const cases = [];
for (const rangeSeconds of [300, 900, 1800, 3600, 2 * 3600, 4 * 3600, 8 * 3600, 16 * 3600, 24 * 3600]) {
  const tokenResolution = tokenResolutionFor(rangeSeconds);
  const params = {
    since: 0, clientId: 'golden-client', tokenConsumer: '1',
    historyStart: NOW - rangeSeconds, historyEnd: NOW, historyResolution: 1,
    history: true, tokenResolution, tokenSince: 0,
    tokenHistoryStart: NOW - rangeSeconds, tokenHistoryEnd: 0,
  };
  cases.push({
    name: `fresh-range-${rangeSeconds}s`, rangeSeconds, params,
    query: api.jsDebugStatsSampleQueryForTest(params),
    readerRequest: {
      history_start: NOW - rangeSeconds, history_end: NOW, history_resolution: 1,
      history_max_points: 6000, include_history: true, client_id: 'golden-client',
      ...(tokenResolution ? {token_resolution: tokenResolution} : {}),
    },
  });
}
const prefetchParams = {clientId: 'golden-client', historyStart: NOW - 24 * 3600, historyEnd: 0, historyResolution: 1};
cases.push({
  name: 'full-retention-prefetch', rangeSeconds: 24 * 3600, params: prefetchParams,
  query: api.jsDebugStatsSampleQueryForTest(prefetchParams),
  readerRequest: {history_start: NOW - 24 * 3600, history_end: 0, history_resolution: 1, history_max_points: 6000, include_history: true, client_id: 'golden-client'},
});
const suppressedParams = {since: 42, clientId: 'golden-client', tokenConsumer: '0', historyStart: 0, historyEnd: 0, historyResolution: 1, history: false};
cases.push({name: 'history-suppressed-backoff', rangeSeconds: 0, params: suppressedParams, query: api.jsDebugStatsSampleQueryForTest(suppressedParams), readerRequest: null});
fs.writeFileSync('fixtures/stats_request_shapes.json', JSON.stringify({nowSeconds: NOW, cases}, null, 2) + '\n');
console.log('goldens written:', cases.length, 'cases');
console.log('sample:', cases[5].query);

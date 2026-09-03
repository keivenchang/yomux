// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const assert = require('assert');
const {execFileSync} = require('child_process');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('static_src/js/yolomux/84_stats_current.js', 'utf8');
const transportLifecycleSource = fs.readFileSync('static_src/js/yolomux/09_transport_lifecycle.js', 'utf8');
const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const lifecycleScopeSource = coreSource.slice(
  coreSource.indexOf('function createLifecycleScope('),
  coreSource.indexOf('\nfunction delegate(', coreSource.indexOf('function createLifecycleScope(')),
).replace('function createLifecycleScope(', 'const createLifecycleScope = function (') + ';';

function loadNamespace() {
  const context = vm.createContext({console, AbortController});
  vm.runInContext(lifecycleScopeSource, context, {filename: '10_core_utils.js:createLifecycleScope'});
  vm.runInContext(transportLifecycleSource, context, {filename: '09_transport_lifecycle.js'});
  vm.runInContext(source, context, {filename: '84_stats_current.js'});
  const namespace = context.YOLOmuxStatsCurrent;
  return {
    ...namespace,
    createBrowserClient(options = {}) {
      if (options.EventSource && Object.hasOwn(options, 'fetch')) options.EventSource.fetchImpl = options.fetch;
      return namespace.createBrowserClient(options);
    },
    mount(element, options = {}) {
      if (options.EventSource && Object.hasOwn(options, 'fetch')) options.EventSource.fetchImpl = options.fetch;
      return namespace.mount(element, options);
    },
  };
}

function loadController(options) { return loadNamespace().createController(options); }

function capabilities() {
  const matrix = [
    [300, 1, [1, 10]],
    [900, 10, [10, 60]],
    [1800, 60, [10, 60]],
    [3600, 300, [60, 300]],
    [7200, 300, [60, 300]],
    [14400, 300, [60, 300]],
    [28800, 300, [60, 300]],
    [57600, 300, [300]],
    [86400, 300, [300]],
  ];
  return {
    resolution_choices: [1, 10, 60, 300],
    max_buckets: 600,
    min_buckets: 12,
    max_live_cadence_seconds: 60,
    ranges: matrix.map(([rangeSeconds, auto, explicit]) => ({
      range_seconds: rangeSeconds,
      auto_resolution_seconds: auto,
      explicit_resolution_seconds: explicit,
      buckets: Object.fromEntries(explicit.map(resolution => [resolution, rangeSeconds / resolution])),
    })),
  };
}

function seriesValue(value, at, sourceCount = 1) {
  return {value, source_count: sourceCount, first_timestamp: at, last_timestamp: at};
}

function costDimensions(changes = {}) {
  return Object.fromEntries(['input', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'output', 'other'].map(name => [
    name, changes[name] || {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
  ]));
}

function costReport(changes = {}) {
  return {
    schema_version: 4,
    total_micro_usd: 0,
    total_api_list_micro_usd: 0,
    total_tokens: 0,
    dimensions: costDimensions(),
    priced: {atoms: 0, tokens: 0},
    unpriced: {atoms: 0, tokens: 0},
    models: [],
    agents: [],
    evidence: [],
    catalog_revision: 0,
    omissions: {models: 0, agents: 0, evidence: 0},
    reasoning_available: false,
    ...changes,
  };
}

function usageAtomBackfill() {
  return {
    state: 'complete',
    sources: 2,
    missing: 0,
    scan: {
      files_read: 2,
      records_parsed: 3,
      atoms_emitted: 4,
      atoms_accepted: 4,
      atoms_rejected: 0,
      rejection_reasons: {},
    },
  };
}

function buckets(start, count, duration, sparse = false) {
  return Array.from({length: count}, (_unused, index) => {
    const bucketStart = start + index * duration;
    const empty = sparse && index > 0;
    return {
      start: bucketStart,
      duration,
      series: empty ? {} : {cpu: seriesValue(index, bucketStart)},
      source: empty
        ? {first_timestamp: null, last_timestamp: null, count: 0}
        : {first_timestamp: bucketStart, last_timestamp: bucketStart, count: 1},
      open: index === count - 1,
    };
  });
}

function snapshot({range = 300, requested = 'AUTO', resolution = 1, cache = 1, sourceGeneration = 1, sparse = false} = {}) {
  return {
    protocol_version: 2,
    range_seconds: range,
    requested_resolution: requested,
    resolution_seconds: resolution,
    window_start: 0,
    window_end: range,
    generated_at: range,
    source_generation: sourceGeneration,
    cache_generation: cache,
    rightmost_open: true,
    buckets: buckets(0, range / resolution, resolution, sparse),
    no_data: [],
    cost_report: costReport(),
    usage_atom_backfill: usageAtomBackfill(),
  };
}

function snapshotChunk(full, chunkIndex, chunkCount) {
  const firstBucket = Math.floor(chunkIndex * full.buckets.length / chunkCount);
  const finalBucket = Math.floor((chunkIndex + 1) * full.buckets.length / chunkCount);
  const selectedBuckets = full.buckets.slice(firstBucket, finalBucket);
  const chunkStart = selectedBuckets[0].start;
  const chunkEnd = selectedBuckets.at(-1).start + full.resolution_seconds;
  return {
    ...full,
    chunk_index: chunkIndex,
    chunk_count: chunkCount,
    chunk_start: chunkStart,
    chunk_end: chunkEnd,
    buckets: selectedBuckets,
    no_data: full.no_data.flatMap(span => (
      span.end > chunkStart && span.start < chunkEnd
        ? [{...span, start: Math.max(span.start, chunkStart), end: Math.min(span.end, chunkEnd)}]
        : []
    )),
  };
}

function delta({
  range = 300,
  resolution = 1,
  sourceGeneration = 2,
  base = 1,
  cache = 2,
  revision = 1,
  bucketReplacements = [],
  noData = [],
  tombstones = [],
  report = costReport(),
} = {}) {
  return {
    protocol_version: 2,
    range_seconds: range,
    resolution_seconds: resolution,
    source_generation: sourceGeneration,
    base_cache_generation: base,
    cache_generation: cache,
    revision,
    buckets: bucketReplacements,
    no_data: noData,
    tombstones,
    cost_report: report,
  };
}

class FakeClock {
  constructor(initialTime = 0) {
    this.time = initialTime;
    this.nextId = 1;
    this.timers = new Map();
  }

  now = () => this.time;
  setTimeout = (callback, delay) => {
    const id = this.nextId++;
    this.timers.set(id, {at: this.time + delay, callback});
    return id;
  };
  clearTimeout = id => this.timers.delete(id);

  async advance(milliseconds) {
    const target = this.time + milliseconds;
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([_id, timer]) => timer.at <= target)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
      if (!due) break;
      this.time = due[1].at;
      this.timers.delete(due[0]);
      due[1].callback();
      await flushPromises();
    }
    this.time = target;
    await flushPromises();
  }

  nextDelay() {
    return Math.min(...[...this.timers.values()].map(timer => timer.at - this.time));
  }
}

class FakeEventSource {
  static instances = [];
  static fetchImpl = null;
  static autoRespond = true;

  constructor(url, options) {
    this.url = url;
    this.options = options;
    this.listeners = new Map();
    this.closeCount = 0;
    this.constructor.instances.push(this);
    if (this.constructor.autoRespond !== false && typeof this.constructor.fetchImpl === 'function') {
      queueMicrotask(() => this.respondFromLegacySnapshotMock());
    }
  }

  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(callback);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter(listener => listener !== callback));
  }

  close() {
    this.closeCount += 1;
  }

  // A real EventSource exposes the frame's SSE `id:` line as `event.lastEventId`, and
  // persists the last non-empty one across frames that carry none.
  emit(name, data = '', lastEventId = '') {
    if (lastEventId) this.lastEventId = String(lastEventId);
    for (const callback of this.listeners.get(name) || []) {
      callback({data, lastEventId: this.lastEventId || ''});
    }
  }

  async respondFromLegacySnapshotMock() {
    if (this.closeCount || !this.url.startsWith('/api/stats-stream')) return;
    const parsed = new URL(this.url, 'http://stats.test');
    parsed.pathname = '/api/stats-snapshot';
    let result;
    try {
      result = await this.constructor.fetchImpl(`${parsed.pathname}${parsed.search}`, {background: true});
    } catch (_error) {
      this.emit('error');
      return;
    }
    if (this.closeCount) return;
    const payload = await result.json();
    if (result.status === 200) {
      const value = payload?.state === 'ready' && payload?.data ? payload.data : payload;
      this.emit('ack', JSON.stringify({
        cache_generation: value.cache_generation,
        chunk_count: value.chunk_count || 1,
        not_modified: false,
        range_seconds: value.range_seconds,
        requested_resolution: value.requested_resolution,
        resolution_seconds: value.resolution_seconds,
      }));
      this.emit('snapshot', JSON.stringify(value));
      this.emit('ready', JSON.stringify({cache_generation: value.cache_generation, revision: 0}));
      return;
    }
    if (result.status === 304) {
      const cacheGeneration = Number(parsed.searchParams.get('since_generation')) || 0;
      const resolution = parsed.searchParams.get('resolution');
      this.emit('ack', JSON.stringify({
        cache_generation: cacheGeneration,
        chunk_count: 1,
        not_modified: true,
        range_seconds: Number(parsed.searchParams.get('range_seconds')),
        requested_resolution: resolution === 'AUTO' ? 'AUTO' : Number(resolution),
        resolution_seconds: 1,
      }));
      this.emit('ready', JSON.stringify({cache_generation: cacheGeneration, revision: 0}));
      return;
    }
    if (result.status === 202) this.emit('pending', JSON.stringify(payload));
    else if (result.status === 426) this.emit('upgrade_required', JSON.stringify(payload));
    else this.emit('unavailable', JSON.stringify(payload || {reason: `HTTP ${result.status}`}));
  }
}

class ManualEventSource extends FakeEventSource {
  static instances = [];
  static autoRespond = false;
}

function emitSnapshotSequence(stream, fullOrChunks) {
  const chunks = Array.isArray(fullOrChunks) ? fullOrChunks : [fullOrChunks];
  const first = chunks[0];
  stream.emit('ack', JSON.stringify({
    cache_generation: first.cache_generation,
    chunk_count: chunks.length,
    not_modified: false,
    range_seconds: first.range_seconds,
    requested_resolution: first.requested_resolution,
    resolution_seconds: first.resolution_seconds,
  }));
  for (const chunk of chunks) stream.emit('snapshot', JSON.stringify(chunk));
  stream.emit('ready', JSON.stringify({cache_generation: first.cache_generation, revision: 0}));
}

class FakePageLifecycle {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(callback);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter(listener => listener !== callback));
  }

  registered() {
    return [...this.listeners.entries()].filter(([_name, listeners]) => listeners.length).map(([name]) => name);
  }

  emit(name) {
    for (const callback of this.listeners.get(name) || []) callback({type: name});
  }
}

function response(status, payload = null) {
  return {
    status,
    json: async () => payload,
  };
}

class FakeDomElement {
  constructor(role = 'root') {
    this.role = role;
    this.listeners = new Map();
    this.scrollTop = 0;
    this._innerHTML = '';
    this.controls = null;
    this.recovery = null;
    this.status = null;
    this.content = null;
    this.modal = null;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    if (this.role === 'root' && value.includes('data-stats-current-controls')) {
      this.controls = new FakeDomElement('controls');
      this.recovery = new FakeDomElement('recovery');
      this.status = new FakeDomElement('status');
      this.content = new FakeDomElement('content');
      this.modal = new FakeDomElement('modal');
    } else if (this.role === 'root' && value === '') {
      this.controls = null;
      this.recovery = null;
      this.status = null;
      this.content = null;
      this.modal = null;
    }
  }

  get innerHTML() {
    return this._innerHTML;
  }

  querySelector(selector) {
    return {
      '[data-stats-current-controls]': this.controls,
      '[data-stats-current-recovery]': this.recovery,
      '[data-stats-current-status]': this.status,
      '[data-stats-current-content]': this.content,
      '[data-stats-current-modal-root]': this.modal,
    }[selector] || null;
  }

  addEventListener(name, callback) {
    this.listeners.set(name, callback);
  }

  removeEventListener(name, callback) {
    if (this.listeners.get(name) === callback) this.listeners.delete(name);
  }

  change(dataset, value) {
    this.listeners.get('change')?.({target: {dataset, value}});
  }
}

function rendererSnapshot({range = 300, requested = 1, resolution = 1, cache = 1} = {}) {
  const result = snapshot({range, requested, resolution, cache, sourceGeneration: cache, sparse: true});
  for (const bucket of result.buckets) {
    bucket.series = {};
    bucket.source = {first_timestamp: null, last_timestamp: null, count: 0};
  }
  const put = (start, name, value) => {
    const bucket = result.buckets.find(item => item.start === start);
    bucket.series[name] = seriesValue(value, start);
    bucket.source = {first_timestamp: start, last_timestamp: start, count: 1};
  };
  const secondStart = resolution === 1 ? 10 : resolution;
  put(0, 'cpu_percent:host', 10);
  put(resolution, 'cpu_percent:host', 20);
  put(0, 'agent_tokens_per_minute:122_frontend-crates|0|%17|codex', 100);
  put(secondStart, 'agent_tokens_per_minute:122_frontend-crates|0|%17|codex', 900);
  put(0, 'agent_tokens_per_minute:122_frontend-crates|1|%18|claude', 50);
  put(secondStart, 'agent_tokens_per_minute:122_frontend-crates|1|%18|claude', 100);
  put(0, 'model_tokens_per_minute:output:gpt', 80);
  put(secondStart, 'model_tokens_per_minute:output:gpt', 800);
  put(0, 'model_tokens_per_minute:input:gpt', 5000);
  put(0, 'cost_micro_usd', 250000);
  put(secondStart, 'cost_micro_usd', 50000);
  put(0, 'api_list_cost_micro_usd', 500000);
  put(secondStart, 'api_list_cost_micro_usd', 100000);
  put(0, 'usage_tokens', 1000);
  put(secondStart, 'usage_tokens', 200);
  const modelKey = '0123456789abcdef01234567';
  const agentKey = '89abcdef0123456789abcdef';
  const evidenceKey = 'fedcba9876543210fedcba98';
  const dimensions = costDimensions({
    input: {tokens: 600, micro_usd: 120000, api_list_micro_usd: 240000},
    cache_read: {tokens: 300, micro_usd: 30000, api_list_micro_usd: 60000},
    cache_write_5m: {tokens: 100, micro_usd: 50000, api_list_micro_usd: 100000},
    output: {tokens: 200, micro_usd: 100000, api_list_micro_usd: 200000},
  });
  const attribution = {
    total_tokens: 1200,
    total_micro_usd: 300000,
    total_api_list_micro_usd: 600000,
    dimensions,
    priced: {atoms: 4, tokens: 1150},
    unpriced: {atoms: 1, tokens: 50},
  };
  const agentAttribution = {
    total_tokens: 600,
    total_micro_usd: 150000,
    total_api_list_micro_usd: 300000,
    dimensions: costDimensions({
      input: {tokens: 300, micro_usd: 60000, api_list_micro_usd: 120000},
      cache_read: {tokens: 150, micro_usd: 15000, api_list_micro_usd: 30000},
      cache_write_5m: {tokens: 50, micro_usd: 25000, api_list_micro_usd: 50000},
      output: {tokens: 100, micro_usd: 50000, api_list_micro_usd: 100000},
    }),
    priced: {atoms: 2, tokens: 575},
    unpriced: {atoms: 1, tokens: 25},
  };
  agentAttribution.sources = [{source: 'codex', ...agentAttribution}];
  result.cost_report = costReport({
    total_micro_usd: 300000,
    total_api_list_micro_usd: 600000,
    total_tokens: 1200,
    dimensions,
    priced: {atoms: 4, tokens: 1150},
    unpriced: {atoms: 1, tokens: 50},
    models: [{key: modelKey, provider: 'openai', model: 'gpt-5.6-sol', ...attribution}],
    agents: [
      {key: agentKey, source: 'codex', label: '122_frontend-crates|0|%17|codex', ...agentAttribution},
      {
        key: '99abcdef0123456789abcdef', source: 'codex', label: '122_frontend-crates|1|%18|codex',
        ...agentAttribution, unpriced: {atoms: 0, tokens: 25},
      },
    ],
    evidence: [{
      key: evidenceKey, provider: 'openai', model: 'gpt-5.6-sol', dimension: 'output',
      direction: 'output', modality: 'text', cache_role: 'none', unit: 'tokens',
      pricing_profile: 'default', service_tier: 'default', catalog_model: 'gpt-5.6-sol',
      rate_usd: '10.00', rate_scale: 1000000, effective_from: '2026-07-09',
      source_kind: 'seed', source_url: 'https://example.com/pricing', catalog_revision: 3,
      tokens: 200, micro_usd: 100000, api_list_micro_usd: 200000, priced_atoms: 1,
    }],
    catalog_revision: 3,
  });
  result.no_data = [{
    family: 'agent_tokens', source_id: 'usage-scan', start: 5, end: 8,
    epoch: 'usage-e1', reason: 'coverage_gap', source_cadence_seconds: 10,
  }];
  return result;
}

function sparseCadenceSnapshot({cache = 1} = {}) {
  const result = snapshot({requested: 1, cache, sourceGeneration: cache, sparse: true});
  for (const bucket of result.buckets) {
    bucket.series = {};
    bucket.source = {first_timestamp: null, last_timestamp: null, count: 0};
  }
  const put = (start, name, value) => {
    const bucket = result.buckets.find(item => item.start === start);
    bucket.series[name] = seriesValue(value, start);
    bucket.source = {first_timestamp: start, last_timestamp: start, count: 1};
  };
  put(290, 'run_agents', 2);
  put(290, 'gpu_util_percent:gpu:0', 40);
  put(240, 'system_memory_used_bytes', 8_000_000_000);
  return result;
}

function chartMarkup(html, chartId) {
  return html.match(new RegExp(`<article[^>]*data-stats-chart="${chartId}"[\\s\\S]*?</article>`))?.[0] || '';
}

async function flushPromises() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

const tests = [];
function test(name, body) {
  tests.push({name, body});
}

test('session token identity groups tmux panes without merging background agents', () => {
  const api = loadNamespace();
  assert.equal(api.canonicalSessionKey('yo7771-b|0|codex'), 'yo7771-b');
  assert.equal(api.canonicalSessionKey('yo7771-b|1|claude'), 'yo7771-b');
  assert.equal(api.canonicalSessionKey('yo7771-b|2|opencode'), 'yo7771-b');
  const background = 'claude-bg:project:123456789abc:deadbeef';
  assert.equal(api.canonicalSessionKey(background), background);
  assert.equal(api.canonicalAgentLabel('yo7771-b|1|claude'), 'yo7771-b');
});

test('session token chart consolidates OpenCode panes by the canonical tmux session key', () => {
  const api = loadNamespace();
  assert.equal(api.canonicalSessionKey('workspace|0|%17|opencode'), 'workspace');
  assert.equal(api.canonicalSessionKey('workspace|1|%18|opencode'), 'workspace');
  assert.equal(api.canonicalAgentLabel('workspace|0|%17|opencode'), 'workspace');
});

test('normalizes saved choices and builds one exact current request', () => {
  const controller = loadController({
    capabilities: capabilities(), savedRange: 900, savedResolution: 120, clientId: 'browser-a',
  });
  assert.deepStrictEqual({...controller.selection()}, {
    range_seconds: 900, resolution: 'AUTO', resolution_seconds: 10,
  });
  assert.deepStrictEqual({...controller.buildRequest()}, {
    range_seconds: 900,
    resolution: 'AUTO',
    client_id: 'browser-a',
    since_generation: 0,
  });
  controller.acceptSnapshot(snapshot({range: 900, resolution: 10}));
  assert.equal(controller.buildRequest().since_generation, 1);
  assert.deepStrictEqual({...controller.buildDeltaRequest()}, {
    range_seconds: 900,
    resolution_seconds: 10,
    client_id: 'browser-a',
    after_cache_generation: 1,
    after_revision: 0,
  });
  const invalidRange = loadController({
    capabilities: capabilities(), savedRange: 123, savedResolution: 10, clientId: 'browser-b',
  });
  assert.deepStrictEqual({...invalidRange.selection()}, {
    range_seconds: 300, resolution: 'AUTO', resolution_seconds: 1,
  });
});

test('consumes every server capability cell without deriving or substituting a matrix', () => {
  const advertised = capabilities();
  let requests = 0;
  for (const row of advertised.ranges) {
    for (const requested of ['AUTO', ...row.explicit_resolution_seconds]) {
      const controller = loadController({
        capabilities: advertised,
        savedRange: row.range_seconds,
        savedResolution: requested,
        clientId: `matrix-${requests}`,
      });
      const concrete = requested === 'AUTO' ? row.auto_resolution_seconds : requested;
      assert.deepStrictEqual({...controller.selection()}, {
        range_seconds: row.range_seconds,
        resolution: requested,
        resolution_seconds: concrete,
      });
      assert.deepStrictEqual({...controller.buildRequest()}, {
        range_seconds: row.range_seconds,
        resolution: requested,
        client_id: `matrix-${requests}`,
        since_generation: 0,
      });
      requests += 1;
    }
  }
  assert.equal(requests, 25, 'the test traverses every AUTO and explicit server cell');

  for (const mutate of [
    value => { value.resolution_choices[3] = 600; },
    value => { value.ranges[0].explicit_resolution_seconds.push(60); },
    value => { value.ranges[0].buckets[1] = 299; },
    value => { value.extra = true; },
  ]) {
    const invalid = JSON.parse(JSON.stringify(capabilities()));
    mutate(invalid);
    assert.throws(() => loadController({
      capabilities: invalid, savedRange: 300, savedResolution: 'AUTO', clientId: 'invalid',
    }), /capabilit|resolution|bucket|fields/);
  }
});

test('accepts the producer-shaped snapshot before atomically replacing the generation', () => {
  const controller = loadController({capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a'});
  const initial = snapshot({requested: 1});
  assert.equal(controller.acceptSnapshot(initial), true);
  const active = controller.generation();
  const invalid = snapshot({requested: 1, cache: 2});
  invalid.buckets[4].duration = 10;
  assert.throws(() => controller.acceptSnapshot(invalid), /bucket duration/);
  assert.strictEqual(controller.generation(), active);
  assert.throws(
    () => controller.acceptSnapshot({...snapshot({requested: 1, cache: 2}), legacy: true}),
    /fields are not exact/,
  );
  assert.throws(
    () => controller.acceptSnapshot({...snapshot({requested: 1, cache: 2}), buckets: Array(601).fill(initial.buckets[0])}),
    /too many buckets/,
  );
  assert.throws(
    () => controller.acceptSnapshot({...snapshot({requested: 1, cache: 2}), buckets: initial.buckets.slice(1)}),
    /density|exact window/,
  );
  const badSource = snapshot({requested: 1, cache: 2});
  badSource.buckets[0].source = {count: 1};
  assert.throws(() => controller.acceptSnapshot(badSource), /fields are not exact/);
  const scalarSeries = snapshot({requested: 1, cache: 2});
  scalarSeries.buckets[0].series.cpu = 1;
  assert.throws(() => controller.acceptSnapshot(scalarSeries), /series cpu must be an object/);
  const inventedSeriesField = snapshot({requested: 1, cache: 2});
  inventedSeriesField.buckets[0].series.cpu.total = 1;
  assert.throws(() => controller.acceptSnapshot(inventedSeriesField), /fields are not exact/);
  const badGap = snapshot({requested: 1, cache: 2});
  badGap.no_data = [{family: 'gpu', start: 1, end: 2}];
  assert.throws(() => controller.acceptSnapshot(badGap), /fields are not exact/);
  const badOpen = snapshot({requested: 1, cache: 2});
  badOpen.buckets[0].open = true;
  assert.throws(() => controller.acceptSnapshot(badOpen), /open state/);
  const badCostReport = snapshot({requested: 1, cache: 2});
  badCostReport.cost_report.total_tokens = 1;
  assert.throws(() => controller.acceptSnapshot(badCostReport), /cost_report totals/);
  const inventedCostField = snapshot({requested: 1, cache: 2});
  inventedCostField.cost_report.legacy_total = 0;
  assert.throws(() => controller.acceptSnapshot(inventedCostField), /fields are not exact/);
  const missingAgentLabel = rendererSnapshot();
  delete missingAgentLabel.cost_report.agents[0].label;
  assert.throws(() => controller.acceptSnapshot(missingAgentLabel), /fields are not exact/);
  const undercountedSources = rendererSnapshot();
  undercountedSources.cost_report.agents[0].sources[0] = {
    ...undercountedSources.cost_report.agents[0].sources[0],
    total_tokens: 0,
    total_micro_usd: 0,
    total_api_list_micro_usd: 0,
    dimensions: costDimensions(),
    priced: {atoms: 0, tokens: 0},
    unpriced: {atoms: 0, tokens: 0},
  };
  assert.throws(
    () => controller.acceptSnapshot(undercountedSources),
    /sources totals disagree with parent attribution/,
  );
  const withGap = snapshot({requested: 1, cache: 2});
  withGap.no_data = [{
    family: 'gpu', source_id: 'gpu:0', start: 10, end: 20, epoch: 'gpu-e1',
    reason: 'coverage_gap', source_cadence_seconds: 10,
  }];
  assert.equal(controller.acceptSnapshot(withGap), true);
  const empty = {...snapshot({requested: 1, cache: 3}), buckets: [], rightmost_open: false};
  assert.equal(controller.acceptSnapshot(empty), true, 'a validated empty generation is ready data');
});

test('accepts the service-produced backfill field and no unlisted snapshot field', () => {
  const bareSnapshot = snapshot({requested: 1});
  delete bareSnapshot.usage_atom_backfill;
  const payload = JSON.parse(execFileSync('python3', ['-c', `
import json
import sys
import threading
from yolomux_lib.stats_current.service import StatsCurrentService

service = object.__new__(StatsCurrentService)
service._snapshot_body_decoration_lock = threading.Lock()
service._snapshot_body_decoration_cache = None
service._snapshot_body_decoration_builds = 0
service._snapshot_body_decoration_hits = 0
service._usage_atom_backfill = {
    "state": "complete", "sources": 2, "missing": 0,
    "scan": {
        "files_read": 2, "records_parsed": 3, "atoms_emitted": 4,
        "atoms_accepted": 4, "atoms_rejected": 0, "rejection_reasons": {},
    },
}
service.encoder = lambda value: json.dumps(value, separators=(",", ":")).encode()
sys.stdout.buffer.write(service._snapshot_body_with_backfill_status(sys.stdin.buffer.read()))
`], {input: JSON.stringify(bareSnapshot), encoding: 'utf8'}));
  const controller = loadController({capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'producer-contract'});
  assert.equal(controller.acceptSnapshot(payload), true);
  assert.throws(
    () => controller.acceptSnapshot({...payload, unexpected_snapshot_field: true}),
    /fields are not exact/,
  );
});

test('accepts the service-produced first delta after a cold public-owner snapshot', () => {
  const payload = JSON.parse(execFileSync('python3', ['-c', `
import json
import tempfile
from pathlib import Path

from yolomux_lib.stats_current import protocol, service, storage

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    database = root / storage.DATABASE_FILENAME
    wall_now = [1_800_000_000.0]
    initial_monotonic = [0.0]
    initial = service.StatsCurrentService(
        root / "initial.sock",
        database,
        monotonic=lambda: initial_monotonic[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        initial_monotonic[0] = service.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        restarted = service.StatsCurrentService(
            root / "restarted.sock",
            database,
            monotonic=lambda: 0.0,
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        _metadata, snapshot_binary = restarted.handle_with_binary({
            "protocol_version": storage.MIN_WRITER_PROTOCOL,
            "schema_generation": storage.SCHEMA_VERSION,
            "action": "snapshot",
            "range_seconds": 900,
            "resolution": "AUTO",
            "client_id": "producer-browser",
        })
        snapshot = protocol.validate_snapshot(json.loads(snapshot_binary))
        wall_now[0] += 10.0
        restarted._build_once(store, True, frozenset())
        _metadata, delta_binary = restarted.handle_with_binary({
            "protocol_version": storage.MIN_WRITER_PROTOCOL,
            "schema_generation": storage.SCHEMA_VERSION,
            "action": "delta",
            "range_seconds": 900,
            "resolution_seconds": 10,
            "client_id": "producer-browser",
            "after_cache_generation": snapshot["cache_generation"],
            "after_revision": 0,
        })
        delta = protocol.validate_delta(json.loads(delta_binary))
        print(json.dumps({"snapshot": snapshot, "delta": delta}, separators=(",", ":")))
`], {encoding: 'utf8'}));
  const controller = loadController({
    capabilities: capabilities(), savedRange: 900, savedResolution: 'AUTO', clientId: 'producer-browser',
  });

  assert.equal(controller.acceptSnapshot(payload.snapshot), true);
  assert.equal(controller.acceptDelta(payload.delta), true);
  assert.equal(controller.generation().cache_generation, payload.delta.cache_generation);
  assert.equal(controller.presentation().delta_revision, payload.delta.revision);
});

test('cache generations are immutable and source generations cannot regress', () => {
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'generation',
  });
  const initial = snapshot({requested: 1, cache: 3, sourceGeneration: 3});
  assert.equal(controller.acceptSnapshot(initial), true);
  assert.equal(controller.acceptSnapshot(initial), false);
  const inconsistent = snapshot({requested: 1, cache: 3, sourceGeneration: 3});
  inconsistent.buckets[0].series.cpu.value = 91;
  assert.throws(() => controller.acceptSnapshot(inconsistent), /not immutable/);
  assert.throws(
    () => controller.acceptSnapshot(snapshot({requested: 1, cache: 4, sourceGeneration: 2})),
    /source generation regressed/,
  );
  assert.equal(controller.generation().cache_generation, 3);
});

test('source generations cannot regress within one range selection', () => {
  const clock = new FakeClock();
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'selection-generation', clock,
  });
  controller.acceptSnapshot(snapshot({requested: 1, cache: 5, sourceGeneration: 5}));
  controller.select(900, 10);
  assert.equal(controller.generation(), null);
  controller.acceptSnapshot(snapshot({range: 900, requested: 10, resolution: 10, cache: 6, sourceGeneration: 0}));
  controller.acceptSnapshot(snapshot({range: 900, requested: 10, resolution: 10, cache: 7, sourceGeneration: 6}));
  controller.select(300, 1);
  assert.equal(controller.generation().cache_generation, 5, 'an already accepted exact cache remains last-known-good');
  assert.throws(
    () => controller.acceptSnapshot(snapshot({requested: 1, cache: 8, sourceGeneration: 4})),
    /source generation regressed/,
  );
});

test('applies exact newer deltas and rejects stale or wrong-key deltas', async () => {
  const clock = new FakeClock();
  let repairs = 0;
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a', clock,
    repairBaseMs: 100, repairMaxMs: 400,
    fetchSnapshot: async () => {
      repairs += 1;
      return snapshot({requested: 1, cache: 3, sourceGeneration: 3});
    },
  });
  controller.acceptSnapshot(snapshot({requested: 1}));
  const original = controller.generation();
  assert.equal(controller.acceptDelta(delta({sourceGeneration: 1, base: 0, cache: 1})), false);
  assert.strictEqual(controller.generation(), original, 'a stale delta changes nothing');
  const replacement = {...original.buckets[2], series: {cpu: seriesValue(99, original.buckets[2].start)}};
  const replacementReport = costReport({
    total_tokens: 1,
    dimensions: costDimensions({output: {tokens: 1, micro_usd: 0, api_list_micro_usd: 0}}),
    priced: {atoms: 1, tokens: 1},
  });
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2, base: 1, cache: 2, revision: 41, bucketReplacements: [replacement],
    report: replacementReport,
  })), true);
  assert.equal(controller.generation().buckets[2].series.cpu.value, 99);
  assert.strictEqual(controller.generation().cost_report, replacementReport, 'the precomputed delta report replaces wholesale');
  assert.deepStrictEqual({...controller.buildDeltaRequest()}, {
    range_seconds: 300,
    resolution_seconds: 1,
    client_id: 'a',
    after_cache_generation: 2,
    after_revision: 41,
  });
  const wrongKey = delta({range: 900, resolution: 10, sourceGeneration: 3, base: 2, cache: 3, revision: 42});
  assert.equal(controller.acceptDelta(wrongKey), false);
  controller.acceptDelta(wrongKey);
  assert.equal(clock.timers.size, 1, 'wrong deltas coalesce behind one repair');
  await clock.advance(250);
  assert.equal(repairs, 1);
  assert.equal(controller.generation().cache_generation, 3);
});

test('an exact tail delta rolls one dense window without a full download', () => {
  let fullFetches = 0;
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'tail',
    fetchSnapshot: async () => { fullFetches += 1; },
  });
  const initial = snapshot({requested: 1});
  controller.acceptSnapshot(initial);
  const closedTail = {...initial.buckets.at(-1), open: false};
  const newTail = {
    start: 300,
    duration: 1,
    series: {cpu: seriesValue(77, 300)},
    source: {first_timestamp: 300, last_timestamp: 300, count: 1},
    open: true,
  };
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 4,
    revision: 8,
    bucketReplacements: [closedTail, newTail],
    tombstones: [{kind: 'bucket', start: 0, duration: 1}],
  })), true);
  const active = controller.generation();
  assert.equal(active.window_start, 1);
  assert.equal(active.window_end, 301);
  assert.equal(active.buckets.length, 300);
  assert.equal(active.buckets[0].start, 1);
  assert.equal(active.buckets.at(-1).start, 300);
  assert.equal(active.buckets.at(-1).series.cpu.value, 77);
  assert.deepStrictEqual({...controller.buildDeltaRequest()}, {
    range_seconds: 300,
    resolution_seconds: 1,
    client_id: 'tail',
    after_cache_generation: 4,
    after_revision: 8,
  });
  assert.equal(fullFetches, 0, 'the 1s exact tail stays on SSE');
});

test('a new source generation adopts its earlier authoritative open-bucket window', () => {
  const clock = new FakeClock();
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'restart-window', clock,
  });
  const initial = snapshot({requested: 1});
  initial.window_start = 1;
  initial.window_end = 301;
  initial.generated_at = 301;
  initial.buckets = buckets(1, 300, 1);
  controller.acceptSnapshot(initial);

  const newHead = {
    start: 0,
    duration: 1,
    series: {cpu: seriesValue(77, 0)},
    source: {first_timestamp: 0, last_timestamp: 0, count: 1},
    open: false,
  };
  const newOpen = {...initial.buckets.at(-2), open: true};
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 2,
    revision: 1,
    bucketReplacements: [newHead, newOpen],
    tombstones: [{kind: 'bucket', start: 300, duration: 1}],
  })), true);
  assert.equal(controller.generation().window_start, 0);
  assert.equal(controller.generation().window_end, 300);
  assert.equal(controller.generation().buckets.at(-1).start, 299);
  assert.equal(controller.generation().buckets.at(-1).open, true);
});

test('full no-data replacements and typed tombstones update only their exact identities', () => {
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'gaps',
  });
  const gap = {
    family: 'gpu',
    source_id: 'gpu:0',
    start: 10,
    end: 20,
    epoch: 'gpu:3',
    reason: 'late',
    source_cadence_seconds: 10,
  };
  const initial = snapshot({requested: 1});
  initial.no_data = [gap];
  controller.acceptSnapshot(initial);
  const replacement = {...gap, reason: 'known_outage', source_cadence_seconds: 60};
  assert.equal(controller.acceptDelta(delta({
    base: 1,
    cache: 5,
    revision: 20,
    noData: [replacement],
  })), true);
  assert.equal(controller.generation().no_data.length, 1);
  assert.equal(controller.generation().no_data[0].reason, 'known_outage');
  assert.equal(controller.generation().no_data[0].source_cadence_seconds, 60);

  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 3,
    base: 5,
    cache: 9,
    revision: 21,
    tombstones: [{
      kind: 'no_data', family: 'gpu', source_id: 'gpu:0', epoch: 'gpu:3', start: 10, end: 20,
    }],
  })), true);
  assert.equal(controller.generation().no_data.length, 0);
  assert.equal(source.includes('replaceIdentities'), false, 'no inferred no-data replacement owner remains');
});

test('malformed, unordered, or ambiguous tombstones cannot partially mutate the generation', async () => {
  const clock = new FakeClock();
  let repairs = 0;
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'bad-tombstone', clock,
    repairBaseMs: 100,
    fetchSnapshot: async () => {
      repairs += 1;
      return snapshot({requested: 1, cache: 2, sourceGeneration: 2});
    },
  });
  const initial = snapshot({requested: 1});
  controller.acceptSnapshot(initial);
  const replacement = {...initial.buckets[2], series: {cpu: seriesValue(88, initial.buckets[2].start)}};
  const invalid = [
    delta({
      bucketReplacements: [replacement],
      tombstones: [{kind: 'bucket', start: 2, duration: 1}],
    }),
    delta({tombstones: [{kind: 'series', start: 2, duration: 1}]}),
    delta({tombstones: [
      {kind: 'no_data', family: 'gpu', source_id: 'gpu:0', epoch: 'gpu:1', start: 10, end: 20},
      {kind: 'bucket', start: 2, duration: 1},
    ]}),
  ];
  for (const update of invalid) assert.equal(controller.acceptDelta(update), false);
  assert.strictEqual(controller.generation(), initial);
  assert.equal(clock.timers.size, 1, 'all invalid tombstones coalesce behind one repair');
  await clock.advance(100);
  assert.equal(repairs, 1);
});

test('invalid delta base and revision coalesce behind one bounded exact repair', async () => {
  const clock = new FakeClock();
  let repairs = 0;
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'chain', clock,
    repairBaseMs: 100,
    repairMaxMs: 400,
    fetchSnapshot: async () => {
      repairs += 1;
      return snapshot({requested: 1, cache: 20, sourceGeneration: 20});
    },
  });
  const initial = snapshot({requested: 1, cache: 7, sourceGeneration: 7});
  controller.acceptSnapshot(initial);
  const replacement = {...initial.buckets[2], series: {cpu: seriesValue(42, initial.buckets[2].start)}};
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 8,
    base: 7,
    cache: 11,
    revision: 41,
    bucketReplacements: [replacement],
  })), true);
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 9,
    base: 10,
    cache: 20,
    revision: 42,
    bucketReplacements: [replacement],
  })), false, 'wrong predecessor requires a snapshot');
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 9,
    base: 11,
    cache: 20,
    revision: 43,
    bucketReplacements: [replacement],
  })), false, 'non-consecutive revision requires a snapshot');
  assert.equal(clock.timers.size, 1, 'both failures share one repair owner');
  await clock.advance(100);
  assert.equal(repairs, 1);
  assert.equal(controller.generation().cache_generation, 20);
  assert.equal(controller.buildDeltaRequest().after_revision, 0, 'snapshot repair resets the delta cursor');
});

test('caught delta rejection records one bounded safe reason before full repair and clears only on push proof', () => {
  const failures = [];
  const clock = new FakeClock();
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1,
    clientId: 'must-not-leak-client', clock, onFailure: failure => failures.push(failure),
  });
  const initial = snapshot({requested: 1, cache: 1, sourceGeneration: 1});
  controller.acceptSnapshot(initial);
  const replacement = {...initial.buckets[2], series: {cpu: seriesValue(42, initial.buckets[2].start)}};

  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2, base: 9, cache: 2, revision: 1, bucketReplacements: [replacement],
  })), false);
  controller.handleTransportFailure();

  assert.equal(failures.length, 1, 'the caught rejection owns the latch before a generic stream error');
  assert.equal(failures[0].source, '/api/stats-stream');
  assert.match(failures[0].message, /d;p=k/);
  assert.match(failures[0].message, /delta base does not match active cache/);
  assert.match(failures[0].message, /a=1\/1\/0/);
  assert.match(failures[0].message, /i=2\/9\/2\/1/);
  assert.match(failures[0].message, /o=1\/0\/0/);
  assert.match(failures[0].message, /;x=Error;/);
  assert.ok(failures[0].message.length <= 160);
  assert.equal(failures[0].message.includes('must-not-leak-client'), false);
  assert.equal(controller.buildRequest().since_generation, 0, 'a caught rejection forces an exact full snapshot repair');

  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2, base: 1, cache: 2, revision: 1, bucketReplacements: [replacement],
  })), true);
  controller.handleTransportFailure();
  assert.equal(failures.length, 2, 'only an accepted push clears the diagnostic latch');
  assert.equal(failures[1].message, 'YO!stats stream unavailable');
});

test('delta rejection keeps every max-width field and full error class across phases', () => {
  const max = Number.MAX_SAFE_INTEGER;
  const rawErrorClass = 'ABCDE FG;IJKLMNOPQRSTUVWX';
  const errorClass = rawErrorClass.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 24);
  const errorReason = 'private reason text that may only be truncated from the right';
  const rejectionError = () => Object.assign(new Error(errorReason), {name: rawErrorClass});
  const decode = value => Number([...value].reduce(
    (result, digit) => result * 36n + BigInt(Number.parseInt(digit, 36)),
    0n,
  ));
  const stagedArray = (lowLengthReads = 0, traps = {}) => {
    let lengthReads = 0;
    return new Proxy([], {
      get(target, property, receiver) {
        if (property === 'length') {
          lengthReads += 1;
          if (traps.throwLength && lengthReads === 1) throw rejectionError();
          return lengthReads <= lowLengthReads ? 0 : max;
        }
        if (property === Symbol.iterator && traps.throwIterator) {
          return () => { throw rejectionError(); };
        }
        if (property === 'map' && traps.throwMap) {
          return () => { throw rejectionError(); };
        }
        return Reflect.get(target, property, receiver);
      },
    });
  };
  const maxArray = () => stagedArray();
  const prepare = failures => {
    const controller = loadController({
      capabilities: capabilities(), savedRange: 300, savedResolution: 1,
      clientId: 'max-safe-diagnostic', clock: new FakeClock(),
      onFailure: failure => failures.push(failure),
    });
    controller.acceptSnapshot(snapshot({requested: 1, cache: max - 4, sourceGeneration: max - 4}));
    const replacement = {...controller.generation().buckets[2]};
    assert.equal(controller.acceptDelta(delta({
      sourceGeneration: max - 3,
      base: max - 4,
      cache: max - 2,
      revision: max - 1,
      bucketReplacements: [replacement],
    })), true);
    return controller;
  };
  const update = values => delta({
    sourceGeneration: max - 1,
    base: max - 2,
    cache: max,
    revision: max,
    ...values,
  });
  const phaseCases = {
    e: common => new Proxy(update(common), {ownKeys: () => { throw rejectionError(); }}),
    k: common => new Proxy(update(common), {
      get: (target, property, receiver) => {
        if (property === 'range_seconds') throw rejectionError();
        return Reflect.get(target, property, receiver);
      },
    }),
    b: common => update({...common, bucketReplacements: stagedArray(0, {throwLength: true})}),
    n: common => update({
      ...common,
      bucketReplacements: stagedArray(2),
      noData: stagedArray(0, {throwIterator: true}),
    }),
    c: common => update({
      ...common,
      bucketReplacements: stagedArray(2),
      noData: stagedArray(1),
      report: new Proxy(costReport(), {ownKeys: () => { throw rejectionError(); }}),
    }),
    t: common => update({
      ...common,
      bucketReplacements: stagedArray(2),
      noData: stagedArray(1),
      tombstones: stagedArray(0, {throwLength: true}),
    }),
    a: common => update({
      ...common,
      bucketReplacements: stagedArray(3, {throwMap: true}),
      noData: stagedArray(2),
      tombstones: stagedArray(2),
    }),
  };

  for (const [phase, makeUpdate] of Object.entries(phaseCases)) {
    const failures = [];
    const controller = prepare(failures);
    const common = {
      bucketReplacements: maxArray(),
      noData: maxArray(),
      tombstones: maxArray(),
    };
    assert.equal(controller.acceptDelta(makeUpdate(common)), false, phase);
    assert.equal(failures.length, 1, phase);
    const message = failures[0].message;
    const fields = message.match(/^d;p=([^;]+);a=([^;]+);i=([^;]+);o=([^;]+);x=([^;]+)(?:;r=(.*))?$/);
    assert.ok(fields, message);
    assert.equal(fields[1], phase);
    assert.deepStrictEqual(fields[2].split('/').map(decode), [max - 3, max - 2, max - 1]);
    assert.deepStrictEqual(fields[3].split('/').map(decode), [max - 1, max - 2, max, max]);
    assert.deepStrictEqual(fields[4].split('/').map(decode), [max, max, max]);
    assert.equal(fields[5], errorClass);
    assert.ok(fields[6] === undefined || errorReason.startsWith(fields[6]));
    assert.ok(message.length <= 160);
  }
});

test('malformed, composed, and no-data delta failures expose only their bounded rejection dimension', () => {
  const initial = snapshot({requested: 1, cache: 1, sourceGeneration: 1});
  const replacement = {...initial.buckets[2], series: {cpu: seriesValue(42, initial.buckets[2].start)}};
  const cases = [
    ['envelope', {cache_generation: 2}],
    ['apply', delta({
      sourceGeneration: 2, base: 1, cache: 2, revision: 1,
      bucketReplacements: [replacement],
      tombstones: [{kind: 'bucket', start: replacement.start, duration: replacement.duration}],
    })],
    ['no_data', delta({
      sourceGeneration: 2, base: 1, cache: 2, revision: 1,
      noData: [{
        family: 'gpu', source_id: 'must-not-leak-source', start: 20, end: 10,
        epoch: 'must-not-leak-epoch', reason: 'bad', source_cadence_seconds: 10,
      }],
    })],
  ];

  for (const [dimension, update] of cases) {
    const failures = [];
    const clock = new FakeClock();
    const controller = loadController({
      capabilities: capabilities(), savedRange: 300, savedResolution: 1,
      clientId: `diagnostic-${dimension}`, clock, onFailure: failure => failures.push(failure),
    });
    controller.acceptSnapshot(initial);
    assert.equal(controller.acceptDelta(update), false);
    assert.equal(failures.length, 1);
    const phase = {envelope: 'e', apply: 'a', no_data: 'n'}[dimension];
    assert.match(failures[0].message, new RegExp(`d;p=${phase}`));
    assert.ok(failures[0].message.length <= 160);
    assert.equal(failures[0].message.includes('must-not-leak'), false);
  }
});

test('a renderer callback throw after delta commit is not mislabeled as wire rejection', () => {
  const failures = [];
  const initial = snapshot({requested: 1, cache: 1, sourceGeneration: 1});
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1,
    clientId: 'callback-throw', clock: new FakeClock(),
    onFailure: failure => failures.push(failure),
    onGeneration: generation => {
      if (generation.cache_generation === 2) throw new Error('renderer failed after commit');
    },
  });
  controller.acceptSnapshot(initial);
  const replacement = {...initial.buckets[2], series: {cpu: seriesValue(42, initial.buckets[2].start)}};

  assert.throws(() => controller.acceptDelta(delta({
    sourceGeneration: 2, base: 1, cache: 2, revision: 1, bucketReplacements: [replacement],
  })), /renderer failed after commit/);
  assert.equal(controller.generation().cache_generation, 2);
  assert.equal(controller.buildDeltaRequest().after_revision, 1);
  assert.equal(failures.length, 0);
});

test('repair failures use one bounded exponential backoff owner', async () => {
  const clock = new FakeClock();
  let calls = 0;
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a', clock,
    repairBaseMs: 100, repairMaxMs: 250,
    fetchSnapshot: async () => {
      calls += 1;
      throw new Error('offline');
    },
  });
  controller.acceptDelta({cache_generation: 1});
  assert.equal(clock.nextDelay(), 100);
  await clock.advance(100);
  assert.equal(calls, 1);
  assert.equal(clock.nextDelay(), 200);
  await clock.advance(200);
  assert.equal(calls, 2);
  assert.equal(clock.nextDelay(), 250, 'backoff is capped');
});

test('hidden and static states suspend repairs then resume one coalesced repair', async () => {
  const clock = new FakeClock();
  let repairs = 0;
  let controller;
  const wrongDelta = cache => delta({
    range: 900,
    resolution: 10,
    sourceGeneration: cache,
    base: cache - 1,
    cache,
    revision: cache,
  });
  controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a', clock,
    repairBaseMs: 100, repairMaxMs: 400,
    fetchSnapshot: async () => {
      repairs += 1;
      const next = controller.generation().cache_generation + 1;
      return snapshot({requested: 1, cache: next, sourceGeneration: next});
    },
  });
  controller.acceptSnapshot(snapshot({requested: 1}));
  controller.setVisible(false);
  controller.acceptDelta(wrongDelta(2));
  controller.acceptDelta(wrongDelta(2));
  assert.equal(clock.timers.size, 0, 'hidden state has no repair polling');
  controller.setVisible(true);
  assert.equal(clock.timers.size, 1, 'visibility resumes one coalesced repair');
  assert.equal(clock.nextDelay(), 0, 'visibility restoration repairs immediately');
  await clock.advance(100);
  assert.equal(repairs, 1);
  controller.setZoomedStatic(true);
  controller.acceptDelta(wrongDelta(3));
  assert.equal(clock.timers.size, 0, 'static zoom has no repair polling');
  controller.setZoomedStatic(false);
  assert.equal(clock.timers.size, 1, 'leaving static zoom resumes one repair');
  await clock.advance(100);
  assert.equal(repairs, 2);
});

test('a rapid selection change cannot publish the old in-flight response', async () => {
  const clock = new FakeClock();
  const pending = [];
  const requests = [];
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'rapid', clock,
    fetchSnapshot: request => new Promise(resolve => {
      requests.push({...request});
      pending.push(resolve);
    }),
  });
  controller.start();
  await clock.advance(100);
  assert.equal(requests.length, 1);
  controller.select(900, 10);
  pending.shift()(snapshot({requested: 1, cache: 5, sourceGeneration: 5}));
  await flushPromises();
  assert.equal(controller.generation(), null, 'old selection completion is ignored');
  await clock.advance(0);
  assert.equal(requests.length, 2);
  assert.deepStrictEqual(requests[1], {
    range_seconds: 900,
    resolution: 10,
    client_id: 'rapid',
    since_generation: 0,
  });
  pending.shift()(snapshot({range: 900, requested: 10, resolution: 10, cache: 6, sourceGeneration: 6}));
  await flushPromises();
  assert.equal(controller.generation().range_seconds, 900);
});

test('selecting the current exact view preserves its cached generation', () => {
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'same-selection',
  });
  const accepted = snapshot({requested: 1, cache: 7, sourceGeneration: 7});
  controller.acceptSnapshot(accepted);
  const before = controller.generation();
  const selection = controller.select(300, 1);
  assert.equal(controller.generation(), before);
  assert.equal(selection.range_seconds, 300);
  assert.equal(selection.resolution, 1);
  assert.equal(selection.resolution_seconds, 1);
});

test('scheduler cadence is resolution-driven, non-overlapping, hidden-aware, and zoom-aware', async () => {
  const clock = new FakeClock();
  const pending = [];
  let ticks = 0;
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a', clock,
    onTick: () => {
      ticks += 1;
      return new Promise(resolve => pending.push(resolve));
    },
  });
  controller.start();
  await clock.advance(999);
  assert.equal(ticks, 0);
  await clock.advance(1);
  assert.equal(ticks, 1);
  await clock.advance(5000);
  assert.equal(ticks, 1, 'a slow tick cannot overlap itself');
  pending.shift()(null);
  await flushPromises();
  await clock.advance(1000);
  assert.equal(ticks, 2);
  controller.setVisible(false);
  pending.shift()(null);
  await flushPromises();
  await clock.advance(5000);
  assert.equal(ticks, 2);
  controller.setVisible(true);
  controller.setZoomedStatic(true);
  await clock.advance(5000);
  assert.equal(ticks, 2);
  controller.setZoomedStatic(false);
  await clock.advance(1000);
  assert.equal(ticks, 3);
  controller.stop();

  for (const [range, resolution, cadence] of [
    [900, 10, 10_000],
    [3600, 60, 60_000],
    [900, 60, 60_000],
    [28800, 60, 60_000],
    [86400, 300, 60_000],
  ]) {
    const otherClock = new FakeClock();
    let count = 0;
    const other = loadController({
      capabilities: capabilities(), savedRange: range, savedResolution: resolution,
      clientId: 'b', clock: otherClock, onTick: () => { count += 1; },
    });
    other.acceptSnapshot(snapshot({range, requested: resolution, resolution}));
    other.start();
    await otherClock.advance(cadence - 1);
    assert.equal(count, 0);
    await otherClock.advance(1);
    assert.equal(count, 1);
  }
});

test('resolved live cadence latches one stream-stall failure until generation advancement', async () => {
  const clock = new FakeClock();
  const failures = [];
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'stall-watchdog', clock,
    onFailure: failure => failures.push(failure),
  });
  controller.acceptSnapshot(snapshot({requested: 1, cache: 1, sourceGeneration: 1}));
  controller.start();

  await clock.advance(4000);
  assert.equal(failures.length, 1, 'more than three resolved 1s cadences emits one failure');
  assert.equal(failures[0].source, '/api/stats-stream');
  assert.match(failures[0].message, /^YO!stats stream .*stalled|^YO!stats stream .*missing/);
  assert.ok(failures[0].message.length <= 160, 'the failure message is bounded');
  await clock.advance(5000);
  assert.equal(failures.length, 1, 'the same stalled generation cannot spam');

  controller.acceptSnapshot(snapshot({requested: 1, cache: 2, sourceGeneration: 2}));
  await clock.advance(4000);
  assert.equal(failures.length, 1, 'snapshot repair cannot prove transport recovery or clear the latch');
  const repaired = controller.generation();
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 3,
    base: 2,
    cache: 3,
    revision: 1,
    bucketReplacements: [{
      ...repaired.buckets[2], series: {cpu: seriesValue(99, repaired.buckets[2].start)},
    }],
  })), true);
  await clock.advance(4000);
  assert.equal(failures.length, 2, 'an accepted push clears the latch for a later stall');
  controller.stop();
});

test('a narrowed selection restarts the delivery budget instead of blaming the new stream', async () => {
  const clock = new FakeClock();
  const failures = [];
  const controller = loadController({
    capabilities: capabilities(), savedRange: 900, savedResolution: 10, clientId: 'select-budget', clock,
    onFailure: failure => failures.push(failure),
  });
  controller.acceptSnapshot(snapshot({range: 900, requested: 10, resolution: 10}));
  controller.start();
  await clock.advance(20_000);
  assert.deepStrictEqual(failures, [], '20s of a 10s-cadence 30s budget is not a stall');

  controller.select(300, 1);
  await clock.advance(1000);
  assert.deepStrictEqual(failures, [], 'the first 1s tick after narrowing cannot inherit the 10s-cadence age');
  await clock.advance(2000);
  assert.deepStrictEqual(failures, [], 'the replacement snapshot is still inside the new 3s budget');

  await clock.advance(2000);
  assert.equal(failures.length, 1, 'a selection that never delivers past its own budget is still a stall');
  assert.equal(failures[0].source, '/api/stats-stream');
  assert.match(failures[0].message, /^YO!stats stream generation stalled for more than 3s$/);
  controller.stop();
});

test('a range the server does not serve is refused instead of silently clamped', async () => {
  const clock = new FakeClock();
  const failures = [];
  const controller = loadController({
    capabilities: capabilities(), savedRange: 3600, savedResolution: 60, clientId: 'unserved-range', clock,
    onFailure: failure => failures.push(failure),
  });
  const before = controller.selection();
  assert.equal(before.range_seconds, 3600);
  const after = controller.select(1234, 'AUTO');
  assert.equal(after.range_seconds, 3600, 'an unserved range keeps the selection the caller already had');
  assert.equal(controller.selection().range_seconds, 3600);
  assert.equal(failures.length, 1, 'an unserved range is reported, never substituted in silence');
  assert.match(failures[0].message, /^YO!stats range 1234 is not served/);
  assert.ok(failures[0].message.length <= 160, 'the failure message is bounded');
  assert.equal(failures[0].source, '/api/stats-stream');
});

test('a refused range keeps the live stream open instead of manufacturing a stall', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  const fetchImpl = async url => {
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    return response(200, snapshot({range: 300, requested: 1, resolution: 1}));
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: FakeEventSource,
    clientId: 'refused-range',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, onFailure: failure => failures.push(failure)},
  });
  await client.start();
  await clock.advance(0);
  assert.equal(FakeEventSource.instances.length, 1);

  const result = client.select(1234, 'AUTO');
  assert.equal(result.range_seconds, 300, 'the refused range leaves the served selection alone');
  assert.equal(failures.length, 1, 'the refusal itself is the only record');
  await clock.advance(500);
  assert.equal(FakeEventSource.instances.length, 2, 'the stream closed for the refused switch is reopened');
  assert.equal(FakeEventSource.instances[1].url, FakeEventSource.instances[0].url.replace('since_generation=0', 'since_generation=1'));
  const reopened = FakeEventSource.instances[1];
  const deliveredBeforeHeartbeats = client.streamEvidence().deliverySequence;
  for (let beat = 0; beat < 5; beat += 1) {
    await clock.advance(1000);
    reopened.emit('ready', JSON.stringify({cache_generation: 1, revision: 0}));
  }
  // The failure owner latches after the refusal, so delivery evidence — not the
  // failure count — is what proves the old view kept receiving server frames.
  assert.equal(client.streamEvidence().deliverySequence, deliveredBeforeHeartbeats + 5, 'the reopened stream keeps delivering');
  assert.equal(client.streamEvidence().streamOpen, true);
  client.stop();
});

test('quiet-stream ready heartbeats are liveness, and only real silence is a stall', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  const fetchImpl = async url => {
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    return response(200, snapshot({range: 300, requested: 1, resolution: 1}));
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: FakeEventSource,
    clientId: 'ready-liveness',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, onFailure: failure => failures.push(failure)},
  });
  await client.start();
  await clock.advance(0);
  assert.equal(FakeEventSource.instances.length, 1);
  const stream = FakeEventSource.instances[0];
  const deliveredBeforeHeartbeats = client.streamEvidence().deliverySequence;

  // The server emits one frame per resolved cadence: a delta when the view moved,
  // otherwise a `ready` heartbeat. Ten unmodified cadences are a provably alive
  // server serving a provably current view, not a stalled stream.
  for (let beat = 0; beat < 10; beat += 1) {
    await clock.advance(1000);
    stream.emit('ready', JSON.stringify({cache_generation: 1, revision: 0}));
  }
  assert.equal(client.streamEvidence().deliverySequence, deliveredBeforeHeartbeats + 10, 'every heartbeat was delivered and validated');
  assert.deepStrictEqual(failures, [], 'a heartbeat-quiet healthy stream never reports a stall');

  // Silence, not quiet: the server stops delivering entirely.
  await clock.advance(4000);
  assert.equal(failures.length, 1, 'a stream that stops delivering is still reported once');
  assert.match(failures[0].message, /^YO!stats stream generation stalled for more than 3s$/);
  client.stop();
});

// The stall predicate can only say that nothing arrived for three cadences; it cannot say
// WHICH side went quiet. `streamEvidence()` already holds the observables that separate a
// silent server from a stream this browser tore down and reopened, so the failure that
// reaches the retained artifact must carry that snapshot, sampled at report time.
test('the stall failure carries the stream evidence that classifies which side went quiet', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  const fetchImpl = async url => {
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    return response(200, snapshot({range: 300, requested: 1, resolution: 1}));
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: FakeEventSource,
    clientId: 'stall-evidence',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, onFailure: failure => failures.push(failure)},
  });
  await client.start();
  await clock.advance(0);
  const before = client.streamEvidence();

  await clock.advance(4000);
  assert.equal(failures.length, 1, 'silence past the budget is reported once');
  const evidence = failures[0].evidence;
  assert.ok(evidence && typeof evidence === 'object', 'the stall failure carries a stream evidence snapshot');
  assert.deepStrictEqual(
    Object.keys(evidence).sort(),
    Object.keys(before).sort(),
    'the attached snapshot is the whole streamEvidence() record, not a hand-picked subset',
  );
  // Real silence: no frame was delivered and the stream was never torn down, so the quiet
  // is upstream of this browser. A client-side rejection would move one of these two.
  assert.equal(evidence.deliverySequence, before.deliverySequence, 'no frame was delivered during the stall window');
  assert.equal(evidence.streamEpoch, before.streamEpoch, 'the stream was never closed and reopened');
  assert.equal(evidence.streamOpen, true, 'the transport was still open when the watchdog fired');
  assert.equal(evidence.running, true);
  assert.equal(evidence.lastDeliveryKind, before.lastDeliveryKind);
  assert.equal(evidence.lastDeliveryAtMs, before.lastDeliveryAtMs, 'the snapshot is sampled at report time and shows the last delivery is stale');
  client.stop();
});

// `YO!stats stream generation stalled for more than 3s` is the classification predicate for
// the whole stall investigation and several retained artifacts key off it byte-for-byte.
// Attaching evidence must not touch it, and must not disturb the existing payload shape.
test('attaching stall evidence leaves the stall message byte-identical', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  const fetchImpl = async url => {
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    return response(200, snapshot({range: 300, requested: 1, resolution: 1}));
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: FakeEventSource,
    clientId: 'stall-message',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, onFailure: failure => failures.push(failure)},
  });
  await client.start();
  await clock.advance(0);
  await clock.advance(4000);
  assert.equal(failures.length, 1);
  assert.equal(failures[0].message, 'YO!stats stream generation stalled for more than 3s');
  assert.equal(failures[0].source, '/api/stats-stream');
  assert.deepStrictEqual(Object.keys(failures[0]).sort(), ['evidence', 'message', 'source']);
  client.stop();
});

// Boundary 4 of the stall investigation. The browser knows when a frame ARRIVED; without the
// server's emit time it cannot tell "the server never sent it" from "the server sent it and it
// arrived late". The emit timestamp rides the SSE `id:` line, outside the JSON body, so no frame
// body key set changes and the strict `exactFields` validators never see it.
test('the browser retains the server emit timestamp so emit can be compared against arrival', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  const fetchImpl = async url => {
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    return response(200, snapshot({range: 300, requested: 1, resolution: 1}));
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: FakeEventSource,
    clientId: 'emit-clock',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, onFailure: failure => failures.push(failure)},
  });
  await client.start();
  await clock.advance(0);
  const stream = FakeEventSource.instances[0];

  await clock.advance(1000);
  stream.emit('ready', JSON.stringify({cache_generation: 1, revision: 0}), '4000');
  const first = client.streamEvidence();
  assert.equal(first.lastDeliveryEmitMs, 4000, 'the server emit timestamp is retained');
  const firstArrival = first.lastDeliveryAtMs;

  // The server emits one cadence later, but the frame takes 1.5 seconds longer to arrive. Emit
  // spacing stays at one cadence while arrival spacing stretches: that difference is transport,
  // and it is invisible without the emit timestamp.
  await clock.advance(2500);
  stream.emit('ready', JSON.stringify({cache_generation: 1, revision: 0}), '5000');
  const second = client.streamEvidence();
  assert.equal(second.lastDeliveryEmitMs - first.lastDeliveryEmitMs, 1000, 'the server emitted on cadence');
  assert.equal(second.lastDeliveryAtMs - firstArrival, 2500, 'the browser saw it 1.5s late');
  assert.deepStrictEqual(failures, [], 'retaining the emit clock reports nothing on its own');

  // A frame with no `id:` keeps the last known emit time, exactly as EventSource does.
  await clock.advance(1000);
  stream.emit('ready', JSON.stringify({cache_generation: 1, revision: 0}));
  assert.equal(client.streamEvidence().lastDeliveryEmitMs, 5000, 'an id-less frame does not erase the emit clock');
  client.stop();
});

test('a server that sends no emit timestamps leaves the evidence numeric rather than absent', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const client = loadNamespace().createBrowserClient({
    fetch: async url => (url === '/api/stats-capabilities'
      ? response(200, capabilities())
      : response(200, snapshot({range: 300, requested: 1, resolution: 1}))),
    EventSource: FakeEventSource,
    clientId: 'emit-clock-absent',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock},
  });
  await client.start();
  await clock.advance(0);
  FakeEventSource.instances[0].emit('ready', JSON.stringify({cache_generation: 1, revision: 0}));
  const evidence = client.streamEvidence();
  assert.equal(evidence.lastDeliveryEmitMs, 0, 'an absent emit clock reads as 0, never NaN or undefined');
  assert.equal(Number.isFinite(evidence.lastDeliveryEmitMs), true);
  client.stop();
});

test('presentation work and exact snapshot repair never overlap each other', async () => {
  const repairClock = new FakeClock();
  let finishRepair;
  let ticks = 0;
  const repairing = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'repair-first',
    clock: repairClock,
    fetchSnapshot: () => new Promise(resolve => { finishRepair = resolve; }),
    onTick: () => { ticks += 1; },
  });
  repairing.acceptSnapshot(snapshot({requested: 1}));
  repairing.handleReconnect();
  await repairClock.advance(0);
  repairing.start();
  await repairClock.advance(1000);
  assert.equal(ticks, 0, 'presentation waits for an in-flight snapshot repair');
  finishRepair(snapshot({requested: 1, cache: 2, sourceGeneration: 2}));
  await flushPromises();
  await repairClock.advance(999);
  assert.equal(ticks, 0);
  await repairClock.advance(1);
  assert.equal(ticks, 1);

  const tickClock = new FakeClock();
  let finishTick;
  let repairs = 0;
  const ticking = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'tick-first',
    clock: tickClock,
    fetchSnapshot: async () => {
      repairs += 1;
      return snapshot({requested: 1, cache: 2, sourceGeneration: 2});
    },
    onTick: () => new Promise(resolve => { finishTick = resolve; }),
  });
  ticking.acceptSnapshot(snapshot({requested: 1}));
  ticking.start();
  await tickClock.advance(1000);
  ticking.handleReconnect();
  await tickClock.advance(0);
  assert.equal(repairs, 0, 'repair waits for in-flight presentation work');
  finishTick(null);
  await flushPromises();
  await tickClock.advance(499);
  assert.equal(repairs, 0);
  await tickClock.advance(1);
  assert.equal(repairs, 1);
});

test('scheduler aligns to wall-clock boundaries and labels snapshot-capable coarse ticks', async () => {
  const fineClock = new FakeClock(250);
  const fineKinds = [];
  const fineViewportEnds = [];
  let fullFetches = 0;
  const fine = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a', clock: fineClock,
    fetchSnapshot: async () => { fullFetches += 1; },
    onViewport: frame => fineViewportEnds.push(frame.window_end),
    onTick: (_request, tick) => fineKinds.push(tick.snapshotDue),
  });
  fine.acceptSnapshot(snapshot({requested: 1}));
  fine.start();
  await fineClock.advance(749);
  assert.deepStrictEqual(fineKinds, []);
  assert.deepStrictEqual(fineViewportEnds, []);
  await fineClock.advance(1);
  assert.deepStrictEqual(fineKinds, [false], '1s presentation/SSE tick lands at t=1000');
  assert.deepStrictEqual(fineViewportEnds, [301], 'an off-boundary snapshot advances on the first wall-clock second');
  assert.equal(fullFetches, 0, 'a fine tick does not inherently fetch a full snapshot');

  for (const range of [900]) {
    const tenClock = new FakeClock(11_000);
    const tenKinds = [];
    const tenViewportEnds = [];
    const tenSecond = loadController({
      capabilities: capabilities(), savedRange: range, savedResolution: 10,
      clientId: `ten-${range}`, clock: tenClock,
      fetchSnapshot: async () => { fullFetches += 1; },
      onViewport: frame => tenViewportEnds.push(frame.window_end),
      onTick: (_request, tick) => tenKinds.push(tick.snapshotDue),
    });
    tenSecond.acceptSnapshot(snapshot({range, requested: 10, resolution: 10}));
    tenSecond.start();
    await tenClock.advance(8_999);
    assert.deepStrictEqual(tenKinds, []);
    assert.deepStrictEqual(tenViewportEnds, [], '10s presentation stays fixed before the shared boundary');
    await tenClock.advance(1);
    assert.deepStrictEqual(tenKinds, [false], '10s presentation/SSE tick lands at t=20000');
    assert.deepStrictEqual(tenViewportEnds, [range + 10], '10s cadence is independent of range length');
    assert.equal(fullFetches, 0, 'a 10s tick does not inherently fetch a full snapshot');
  }

  for (const [range, resolution] of [[900, 60], [86400, 300]]) {
    const clock = new FakeClock(12_345);
    const kinds = [];
    const viewportEnds = [];
    const coarse = loadController({
      capabilities: capabilities(), savedRange: range, savedResolution: resolution,
      clientId: 'b', clock,
      onViewport: frame => viewportEnds.push(frame.window_end),
      onTick: (_request, tick) => {
        kinds.push(tick.snapshotDue);
        return snapshot({range, requested: resolution, resolution, cache: 1});
      },
    });
    coarse.acceptSnapshot(snapshot({range, requested: resolution, resolution, cache: 1}));
    coarse.start();
    await clock.advance(60_000 - 12_345 - 1);
    assert.deepStrictEqual(kinds, []);
    assert.deepStrictEqual(viewportEnds, []);
    await clock.advance(1);
    assert.deepStrictEqual(kinds, [true], '60s/300s tick may return one exact snapshot per minute');
    assert.deepStrictEqual(viewportEnds, [range + 60], 'coarse presentation advances once at the minute boundary');
    assert.equal(coarse.generation().cache_generation, 1);
    await clock.advance(59_999);
    assert.deepStrictEqual(viewportEnds, [range + 60], 'coarse presentation does no sub-minute work');
    await clock.advance(1);
    assert.deepStrictEqual(viewportEnds, [range + 60, range + 120]);
  }
});

test('an off-boundary exact delta cannot reset and suppress the next 1s motion tick', async () => {
  const clock = new FakeClock(250);
  const viewportEnds = [];
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1,
    clientId: 'delta-anchor', clock,
    onViewport: frame => viewportEnds.push(frame.window_end),
  });
  controller.acceptSnapshot(snapshot({requested: 1}));
  controller.start();
  await clock.advance(750);
  assert.deepStrictEqual(viewportEnds, [301]);
  await clock.advance(250);
  const active = controller.generation();
  const replacement = {
    ...active.buckets[2],
    series: {cpu: seriesValue(99, active.buckets[2].start)},
  };
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 2,
    revision: 1,
    bucketReplacements: [replacement],
  })), true);
  await clock.advance(749);
  assert.deepStrictEqual(viewportEnds, [301]);
  await clock.advance(1);
  assert.deepStrictEqual(viewportEnds, [301, 302], 'the next wall-clock second advances after an off-boundary delta');
});

test('sparse data stays byte-for-byte unchanged while viewport time advances', async () => {
  const clock = new FakeClock();
  const viewportEnds = [];
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'a', clock,
    onViewport: frame => viewportEnds.push(frame.window_end), onTick: () => null,
  });
  controller.acceptSnapshot(snapshot({requested: 1, sparse: true}));
  const active = controller.generation();
  const serialized = JSON.stringify(active);
  const before = controller.projectSeries('cpu', 300).map(point => ({value: point.value, x: point.x}));
  controller.start();
  await clock.advance(3000);
  assert.deepStrictEqual(viewportEnds, [301, 302, 303]);
  assert.strictEqual(controller.generation(), active);
  assert.equal(JSON.stringify(controller.generation()), serialized);
  const after = controller.projectSeries('cpu', 300).map(point => ({value: point.value, x: point.x}));
  assert.deepStrictEqual(before.map(point => point.value), after.map(point => point.value));
  assert.equal(after.length, before.length, 'presentation motion does not fabricate sparse points');
  assert.equal(after[0].x, before[0].x - 3, 'the existing point moves left with the live domain');
  assert.deepStrictEqual({...controller.axis()}, {smallest_unit: 'second', show_seconds: true});
});

test('1s presentation moves sparse native-cadence families without inventing samples', async () => {
  const clock = new FakeClock();
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1,
    clientId: 'sparse-native-cadence', clock, onTick: () => null,
  });
  controller.acceptSnapshot(sparseCadenceSnapshot());
  const active = controller.generation();
  const serialized = JSON.stringify(active);
  const series = [
    ['run_agents', 2],
    ['gpu_util_percent:gpu:0', 40],
    ['system_memory_used_bytes', 8_000_000_000],
  ];
  const before = Object.fromEntries(series.map(([name]) => [name, controller.projectSeries(name, 300)]));

  controller.start();
  await clock.advance(3000);

  assert.strictEqual(controller.generation(), active, 'presentation does not publish a dataset generation');
  assert.equal(controller.generation().cache_generation, 1);
  assert.equal(JSON.stringify(controller.generation()), serialized, 'presentation does not mutate the exact dataset');
  for (const [name, value] of series) {
    const after = controller.projectSeries(name, 300);
    assert.equal(after.length, 1, `${name} remains one native sample`);
    assert.equal(after[0].value, value, `${name} retains its native value`);
    assert.equal(after[0].x, before[name][0].x - 3, `${name} moves with the 1s presentation domain`);
  }

  const replacement = {
    ...active.buckets.at(-1),
    series: Object.fromEntries(series.map(([name, value]) => [name, seriesValue(value + 1, 299)])),
    source: {first_timestamp: 299, last_timestamp: 299, count: 3},
  };
  assert.equal(controller.acceptDelta(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 2,
    revision: 1,
    bucketReplacements: [replacement],
  })), true);
  assert.notStrictEqual(controller.generation(), active, 'a native delta publishes a new dataset generation');
  assert.equal(controller.generation().cache_generation, 2);
  for (const [name, value] of series) {
    const afterDelta = controller.projectSeries(name, 300);
    assert.deepStrictEqual([...afterDelta].map(point => point.value), [value, value + 1]);
  }
});

test('zoom freezes presentation while exact data advances, then reset rejoins the live edge', async () => {
  const clock = new FakeClock();
  const controller = loadController({
    capabilities: capabilities(), savedRange: 300, savedResolution: 1, clientId: 'zoom', clock,
  });
  const initial = snapshot({requested: 1});
  controller.acceptSnapshot(initial);
  controller.start();
  await clock.advance(1000);
  assert.equal(controller.presentation().window_end, 301);
  controller.setZoomedStatic(true);

  const applyTail = (cache, start, previous) => controller.acceptDelta(delta({
    sourceGeneration: cache,
    base: cache - 1,
    cache,
    revision: cache - 1,
    bucketReplacements: [
      {...previous, open: false},
      {
        start,
        duration: 1,
        series: {cpu: seriesValue(start, start)},
        source: {first_timestamp: start, last_timestamp: start, count: 1},
        open: true,
      },
    ],
    tombstones: [{kind: 'bucket', start: start - 300, duration: 1}],
  }));
  assert.equal(applyTail(2, 300, initial.buckets.at(-1)), true);
  assert.equal(applyTail(3, 301, controller.generation().buckets.at(-1)), true);
  await clock.advance(5000);
  assert.equal(controller.generation().window_end, 302, 'zoom retains exact incoming data');
  assert.equal(controller.presentation().window_end, 301, 'zoomed presentation remains stationary');
  controller.setZoomedStatic(false);
  assert.equal(controller.presentation().window_end, 302, 'Reset Zoom rejoins the accepted live edge');
});

test('only an echoed 1s resolution requests seconds on the X axis', () => {
  for (const [range, resolution, showSeconds] of [[300, 1, true], [900, 10, false], [900, 60, false], [86400, 300, false]]) {
    const controller = loadController({
      capabilities: capabilities(), savedRange: range, savedResolution: resolution, clientId: `axis-${resolution}`,
    });
    assert.deepStrictEqual({...controller.axis()}, {
      smallest_unit: showSeconds ? 'second' : 'minute',
      show_seconds: showSeconds,
    });
  }
});

test('browser transport uses one authenticated current stream and exact snapshot repair', async () => {
  ManualEventSource.instances = [];
  const clock = new FakeClock();
  const fetches = [];
  const fetchImpl = async (url, options, internalOptions) => {
    fetches.push({url, options, internalOptions});
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    throw new Error(`unexpected browser fetch: ${url}`);
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: ManualEventSource,
    clientId: 'browser-current',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  const firstStart = client.start();
  const duplicateStart = client.start();
  assert.strictEqual(firstStart, duplicateStart, 'concurrent starts share capability initialization');
  await firstStart;
  await clock.advance(0);
  assert.equal(fetches.filter(item => item.url === '/api/stats-capabilities').length, 1);
  assert.equal(fetches.length, 1, 'history does not use fetch or Promise.all fan-out');
  assert.equal(fetches[0].internalOptions, undefined, 'capability reads have no expected non-2xx status');
  assert.equal(fetches[0].options.method, 'GET');
  assert.equal(ManualEventSource.instances.length, 1);
  const initialStream = ManualEventSource.instances[0];
  assert.equal(initialStream.options.withCredentials, true);
  assert.equal(initialStream.url, [
    '/api/stats-stream?range_seconds=300&resolution=1',
    'client_id=browser-current&since_generation=0',
  ].join('&'));
  emitSnapshotSequence(initialStream, snapshot({requested: 1, cache: 1, sourceGeneration: 1}));
  await flushPromises();
  assert.deepStrictEqual({...client.streamEvidence()}, {
    running: true, visible: true, healthy: true, streamOpen: true, streamEpoch: 3,
    deliverySequence: 3, acceptedDeltaSequence: 0, lastDeliveryKind: 'ready', lastDeliveryAtMs: 0,
    lastDeliveryEmitMs: 0, lastDeliveryEpoch: 3, rangeSeconds: 300, resolutionSeconds: 1, sourceGeneration: 1,
    cacheGeneration: 1, deltaRevision: 0,
  }, 'ack, snapshot, and ready arrive on the same stream that stays live');
  initialStream.emit('ready', JSON.stringify({cache_generation: 1, revision: 0}));
  assert.equal(client.streamEvidence().deliverySequence, 4, 'a validated ready frame proves quiet-stream delivery');
  assert.equal(client.streamEvidence().lastDeliveryKind, 'ready');
  const cachedInitialGeneration = client.controller().generation();
  client.select(300, 1);
  assert.equal(client.controller().generation(), cachedInitialGeneration, 'same exact browser selection keeps cached data');
  assert.equal(initialStream.closeCount, 0, 'same exact browser selection keeps its live stream');

  const original = client.controller().generation();
  initialStream.emit('delta', JSON.stringify(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 2,
    revision: 1,
    bucketReplacements: [{
      ...original.buckets[2], series: {cpu: seriesValue(99, original.buckets[2].start)},
    }],
  })));
  assert.equal(client.controller().generation().cache_generation, 2);
  assert.equal(client.controller().presentation().delta_revision, 1);
  assert.equal(client.streamEvidence().deliverySequence, 5);
  assert.equal(client.streamEvidence().acceptedDeltaSequence, 1);
  assert.equal(client.streamEvidence().lastDeliveryKind, 'delta');
  assert.equal(client.streamEvidence().cacheGeneration, 2);
  assert.equal(ManualEventSource.instances.length, 1, 'accepted deltas do not reopen the live stream');

  initialStream.emit('error');
  initialStream.emit('error');
  assert.equal(initialStream.closeCount, 1, 'the failed native reconnect owner is closed once');
  await clock.advance(100);
  assert.equal(fetches.length, 1, 'a reconnect still has no parallel history fetch');
  assert.equal(ManualEventSource.instances.length, 2, 'one incremental repair reopens one stream');
  assert.ok(ManualEventSource.instances[1].url.endsWith('since_generation=2'));
  ManualEventSource.instances[1].emit('ack', JSON.stringify({
    cache_generation: 2, chunk_count: 1, not_modified: true,
    range_seconds: 300, requested_resolution: 1, resolution_seconds: 1,
  }));
  ManualEventSource.instances[1].emit('ready', JSON.stringify({cache_generation: 2, revision: 0}));
  await flushPromises();

  client.select(900, 10);
  assert.equal(ManualEventSource.instances[1].closeCount, 1);
  await clock.advance(0);
  assert.equal(ManualEventSource.instances.length, 3);
  assert.ok(ManualEventSource.instances[2].url.includes('range_seconds=900&resolution=10'));
  emitSnapshotSequence(ManualEventSource.instances[2], snapshot({
    range: 900, requested: 10, resolution: 10, cache: 3, sourceGeneration: 3,
  }));
  await flushPromises();

  client.setVisible(false);
  assert.equal(ManualEventSource.instances[2].closeCount, 1);
  ManualEventSource.instances[2].emit('repair');
  await clock.advance(0);
  assert.equal(ManualEventSource.instances.length, 3, 'closed-stream events cannot create duplicates');
  client.setVisible(true);
  await clock.advance(0);
  assert.equal(ManualEventSource.instances.length, 4, 'visibility repair creates one current stream');
  ManualEventSource.instances[3].emit('ack', JSON.stringify({
    cache_generation: 3, chunk_count: 1, not_modified: true,
    range_seconds: 900, requested_resolution: 10, resolution_seconds: 10,
  }));
  ManualEventSource.instances[3].emit('ready', JSON.stringify({cache_generation: 3, revision: 0}));
  await flushPromises();
  await client.start();
  assert.equal(ManualEventSource.instances.length, 4, 'duplicate start keeps the active stream');
  assert.equal(fetches.filter(item => item.url === '/api/stats-capabilities').length, 1);

  client.stop();
  assert.equal(ManualEventSource.instances[3].closeCount, 1);
  await client.start();
  await clock.advance(100);
  assert.equal(ManualEventSource.instances.length, 5, 'restart reuses capabilities and the accepted cursor');
  assert.equal(fetches.filter(item => item.url === '/api/stats-capabilities').length, 1);
  client.stop();
  assert.equal(fetches.length, 1);
});

test('eleven 1s deltas stay on one SSE and retain unchanged buckets in memory', async () => {
  ManualEventSource.instances = [];
  const clock = new FakeClock();
  const fullGenerations = [];
  const incrementalDeliveries = [];
  const fetchImpl = async url => {
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    throw new Error(`unexpected browser fetch: ${url}`);
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: ManualEventSource,
    clientId: 'eleven-second-tail',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {
      clock,
      onGeneration: generation => fullGenerations.push(generation),
      onDelta: (generation, update) => incrementalDeliveries.push({generation, update}),
    },
  });

  await client.start();
  await clock.advance(0);
  const stream = ManualEventSource.instances[0];
  emitSnapshotSequence(stream, snapshot({requested: 1, cache: 1, sourceGeneration: 1}));
  await flushPromises();
  const retainedBucket = client.controller().generation().buckets.find(bucket => bucket.start === 20);

  for (let second = 1; second <= 11; second += 1) {
    const active = client.controller().generation();
    const previousTail = active.buckets.at(-1);
    const closedTail = {...previousTail, open: false};
    const newStart = active.window_end;
    const newTail = {
      start: newStart,
      duration: 1,
      series: {cpu: seriesValue(100 + second, newStart)},
      source: {first_timestamp: newStart, last_timestamp: newStart, count: 1},
      open: true,
    };
    stream.emit('delta', JSON.stringify(delta({
      sourceGeneration: second + 1,
      base: second,
      cache: second + 1,
      revision: second,
      bucketReplacements: [closedTail, newTail],
      tombstones: [{kind: 'bucket', start: active.window_start, duration: 1}],
    })));
    stream.emit('ready', JSON.stringify({cache_generation: second + 1, revision: second}));
  }

  const active = client.controller().generation();
  assert.equal(ManualEventSource.instances.length, 1, 'the initial request remains the only EventSource');
  assert.equal(stream.closeCount, 0, 'the live stream remains open after the ten-second boundary');
  assert.equal(fullGenerations.length, 1, 'ack/snapshot readiness publishes one full generation');
  assert.equal(incrementalDeliveries.length, 11, 'later frames use only the incremental callback');
  assert.equal(client.streamEvidence().acceptedDeltaSequence, 11);
  assert.equal(client.streamEvidence().deliverySequence, 25, 'one ack, one snapshot, initial ready, then eleven delta/ready pairs');
  assert.strictEqual(active.buckets.find(bucket => bucket.start === 20), retainedBucket, 'unchanged bucket objects remain resident');
  assert.equal(active.buckets.length, 300);
  assert.equal(active.window_start, 11);
  assert.equal(active.window_end, 311);
  assert.deepEqual(
    incrementalDeliveries.map(item => [item.update.buckets.length, item.update.tombstones.length]),
    Array.from({length: 11}, () => [2, 1]),
    'each callback carries only changed and tombstoned identities',
  );
  client.stop();
});

test('one 8h SSE publishes one complete generation after four private snapshot chunks', async () => {
  ManualEventSource.instances = [];
  const clock = new FakeClock();
  const generations = [];
  const states = [];
  const full = snapshot({range: 28800, requested: 300, resolution: 300, cache: 41, sourceGeneration: 37});
  full.no_data = [{
    family: 'cpu', source_id: 'host', start: 7100, end: 7300,
    epoch: 'host-1', reason: 'collector_gap', source_cadence_seconds: 10,
  }];
  const client = loadNamespace().createBrowserClient({
    fetch: async (url, options) => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      throw new Error(`unexpected history fetch: ${url} ${String(options)}`);
    },
    EventSource: ManualEventSource,
    clientId: 'chunked-eight-hours',
    savedRange: 28800,
    savedResolution: 300,
    onState: state => states.push(state),
    controllerOptions: {
      clock,
      repairBaseMs: 100,
      repairMaxMs: 400,
      onGeneration: generation => generations.push(generation),
    },
  });

  await client.start();
  await clock.advance(0);
  assert.equal(ManualEventSource.instances.length, 1, 'history and live deltas share one EventSource');
  const stream = ManualEventSource.instances[0];
  assert.ok(stream.url.includes('range_seconds=28800&resolution=300'));
  const chunks = [0, 1, 2, 3].map(index => snapshotChunk(full, index, 4));
  stream.emit('ack', JSON.stringify({
    cache_generation: 41, chunk_count: 4, not_modified: false,
    range_seconds: 28800, requested_resolution: 300, resolution_seconds: 300,
  }));
  for (const chunk of chunks) {
    stream.emit('snapshot', JSON.stringify(chunk));
    assert.equal(client.controller().generation(), null, 'partial data stays private until ready');
    assert.equal(generations.length, 0, 'no renderer generation is published between chunks');
  }
  stream.emit('ready', JSON.stringify({cache_generation: 41, revision: 0}));
  await flushPromises();

  assert.equal(generations.length, 1, 'ready atomically publishes one complete generation');
  assert.equal(generations[0].buckets.length, full.buckets.length);
  assert.deepEqual(client.controller().generation().buckets, full.buckets, 'chunked and whole-window buckets are equivalent');
  assert.deepEqual(client.controller().generation().no_data, full.no_data, 'chunked and whole-window gaps are equivalent');
  assert.equal(states.at(-1), 'ready');
  assert.equal(ManualEventSource.instances.length, 1);
  stream.emit('ready', JSON.stringify({cache_generation: 41, revision: 0}));
  assert.equal(client.streamEvidence().streamOpen, true, 'the snapshot stream remains the live stream');
  client.stop();
});

test('switching selection closes one in-flight SSE and stale chunks cannot publish', async () => {
  ManualEventSource.instances = [];
  const clock = new FakeClock();
  const full = snapshot({range: 28800, requested: 300, resolution: 300, cache: 41, sourceGeneration: 37});
  const generations = [];
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      throw new Error(`unexpected history fetch: ${url}`);
    },
    EventSource: ManualEventSource,
    clientId: 'cancel-chunk-selection',
    savedRange: 28800,
    savedResolution: 300,
    controllerOptions: {
      clock,
      repairBaseMs: 100,
      repairMaxMs: 400,
      onGeneration: generation => generations.push(generation),
    },
  });

  await client.start();
  await clock.advance(0);
  const oldStream = ManualEventSource.instances[0];
  oldStream.emit('ack', JSON.stringify({
    cache_generation: 41, chunk_count: 4, not_modified: false,
    range_seconds: 28800, requested_resolution: 300, resolution_seconds: 300,
  }));
  oldStream.emit('snapshot', JSON.stringify(snapshotChunk(full, 0, 4)));
  assert.equal(generations.length, 0);
  client.select(300, 1);
  await flushPromises();
  await clock.advance(0);
  assert.equal(oldStream.closeCount, 1, 'one selection transition closes the one history stream');
  oldStream.emit('snapshot', JSON.stringify(snapshotChunk(full, 1, 4)));
  oldStream.emit('ready', JSON.stringify({cache_generation: 41, revision: 0}));
  assert.equal(generations.length, 0, 'no stale chunk publishes after the selection changes');
  const newStream = ManualEventSource.instances[1];
  emitSnapshotSequence(newStream, snapshot({requested: 1, resolution: 1, cache: 50, sourceGeneration: 50}));
  await flushPromises();
  assert.equal(client.controller().generation().range_seconds, 300);
  assert.equal(client.controller().generation().cache_generation, 50);
  assert.equal(ManualEventSource.instances.length, 2);
  assert.ok(newStream.url.includes('range_seconds=300&resolution=1'));
  client.stop();
  await flushPromises();
  assert.equal(clock.timers.size, 0, 'stopping after cancellation cannot leave a repair timer');
});

test('snapshot authentication failure retires the client instead of polling at repair cadence', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const terminalFailures = [];
  let snapshotFetches = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotFetches += 1;
      return response(401, {reason: 'authentication_required'});
    },
    EventSource: FakeEventSource,
    clientId: 'browser-auth-retirement',
    savedRange: 300,
    savedResolution: 1,
    onState: state => states.push(state),
    onTerminalAuthentication: error => terminalFailures.push(error),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  await client.start();
  await clock.advance(0);
  assert.equal(snapshotFetches, 1, 'the first 401 is the only snapshot request');
  assert.equal(FakeEventSource.instances.length, 1, 'authentication is rejected on the one stats SSE request');
  assert.equal(FakeEventSource.instances[0].closeCount, 1, 'the rejected stream is retired immediately');
  await clock.advance(60_000);
  assert.equal(snapshotFetches, 1, 'terminal authentication cannot re-enter the repair timer');
  assert.deepEqual(states, ['loading', 'signed-out']);
  assert.equal(terminalFailures.length, 1, 'one owner observes the terminal authentication transition');
  assert.equal(terminalFailures[0].status, 401);
});

test('capabilities authentication failure is terminal before snapshot or stream startup', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const terminalFailures = [];
  let capabilityFetches = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      assert.equal(url, '/api/stats-capabilities');
      capabilityFetches += 1;
      return response(401, {code: 'authentication_required'});
    },
    EventSource: FakeEventSource,
    clientId: 'browser-capability-auth-retirement',
    onState: state => states.push(state),
    onTerminalAuthentication: error => terminalFailures.push(error),
    controllerOptions: {clock},
  });

  await assert.rejects(client.start(), error => error?.terminalAuthentication === true);
  await clock.advance(60_000);
  assert.equal(capabilityFetches, 1, 'readiness retry cannot refetch capabilities after a 401');
  assert.equal(FakeEventSource.instances.length, 0);
  assert.deepEqual(states, ['loading', 'signed-out']);
  assert.equal(terminalFailures.length, 1);
});

test('a stream failure whose repair receives 401 closes the stream and stops repair', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const terminalFailures = [];
  let snapshotFetches = 0;
  let authenticationExpired = false;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotFetches += 1;
      return authenticationExpired
        ? response(401, {code: 'authentication_required'})
        : response(200, snapshot({requested: 1}));
    },
    EventSource: FakeEventSource,
    clientId: 'browser-stream-auth-retirement',
    savedRange: 300,
    savedResolution: 1,
    onState: state => states.push(state),
    onTerminalAuthentication: error => terminalFailures.push(error),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  await client.start();
  await clock.advance(0);
  assert.equal(snapshotFetches, 1);
  assert.equal(FakeEventSource.instances.length, 1);
  authenticationExpired = true;
  FakeEventSource.instances[0].emit('error');
  await clock.advance(100);
  assert.equal(snapshotFetches, 2, 'the ambiguous native error gets one authenticated repair read');
  assert.equal(FakeEventSource.instances[0].closeCount, 1);
  await clock.advance(60_000);
  assert.equal(snapshotFetches, 2, 'the repair 401 retires rather than reopening the stream');
  assert.equal(FakeEventSource.instances.length, 2, 'the fenced stream is replaced once after recovery');
  assert.equal(states.at(-1), 'signed-out');
  assert.equal(terminalFailures.length, 1);
});

test('a snapshot from a newer server that grew a top-level field is reported, not silently blanked', async () => {
  // The v0.6.10 -> v0.7.1 upgrade of port 7770: the running server grew the
  // `usage_atom_backfill` snapshot field while an already-open tab kept running the
  // pre-upgrade bundle. Every repair snapshot then failed the exact-field contract, the
  // rejection was discarded by the repair `catch`, and Session tokens, Model tokens, and the
  // Cost Summary stayed blank for two hours with nothing said anywhere.
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  let serverGrewAField = false;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      const payload = snapshot({requested: 1, cache: 2, sourceGeneration: 2});
      return response(200, serverGrewAField
        ? {...payload, field_this_client_predates: {state: 'ready'}}
        : payload);
    },
    EventSource: FakeEventSource,
    clientId: 'browser-forward-incompatible-snapshot',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {
      clock, repairBaseMs: 100, repairMaxMs: 400,
      onFailure: failure => failures.push(failure),
    },
  });
  await client.start();
  await clock.advance(0);
  const painted = client.controller().generation();
  assert.equal(painted.cache_generation, 2, 'the pre-upgrade client paints the old server\'s snapshot');

  serverGrewAField = true;
  FakeEventSource.instances[0].emit('repair');
  await clock.advance(100);
  await clock.advance(0);

  assert.equal(client.controller().generation(), painted, 'an unreadable snapshot never replaces the painted generation');
  assert.equal(failures.length, 1, 'the unreadable snapshot is reported once instead of leaving a blank panel');
  assert.match(failures[0].message, /snapshot fields are not exact/, 'the reported reason names the contract that rejected the payload');
  client.stop();
});

test('server-requested stream repair reconnects without reporting a native transport failure', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  let snapshotFetches = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotFetches += 1;
      return response(200, snapshot({requested: 1, cache: snapshotFetches, sourceGeneration: snapshotFetches}));
    },
    EventSource: FakeEventSource,
    clientId: 'browser-repair-signal',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {
      clock, repairBaseMs: 100, repairMaxMs: 400,
      onFailure: failure => failures.push(failure),
    },
  });
  await client.start();
  await clock.advance(0);
  FakeEventSource.instances[0].emit('repair');
  assert.equal(failures.length, 0, 'the server repair control frame is not an error observation');
  await clock.advance(100);
  assert.equal(FakeEventSource.instances.length, 2);
  FakeEventSource.instances[1].emit('error');
  assert.equal(failures.length, 1, 'a native EventSource failure uses the latched failure owner');
  client.stop();
});

async function startRetirementClient(pageLifecycle, {failures, retirements}) {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  let snapshotFetches = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotFetches += 1;
      return response(200, snapshot({requested: 1, cache: snapshotFetches, sourceGeneration: snapshotFetches}));
    },
    EventSource: FakeEventSource,
    pageLifecycle,
    clientId: 'browser-retirement',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {
      clock, repairBaseMs: 100, repairMaxMs: 400,
      onFailure: failure => failures.push(failure),
      onRetirement: retirement => retirements.push(retirement),
    },
  });
  await client.start();
  await clock.advance(0);
  return {client, clock};
}

test('an unload-initiated stream close reports one machine-readable retirement instead of a transport failure', async () => {
  const lifecycle = new FakePageLifecycle();
  const failures = [];
  const retirements = [];
  const {client} = await startRetirementClient(lifecycle, {failures, retirements});

  // Measured ordering on live 7771: beforeunload, then the native EventSource error.
  lifecycle.emit('beforeunload');
  FakeEventSource.instances[0].emit('error');
  assert.deepEqual(failures.map(entry => entry.message), [], 'the browser tearing down its own stream is not a transport failure');
  assert.deepEqual(lifecycle.registered(), ['beforeunload', 'pagehide', 'pageshow']);
  assert.deepEqual(retirements.map(entry => ({...entry})), [
    {reason: 'page_beforeunload', source: '/api/stats-stream'},
  ], 'the expected close still carries a machine-readable reason and route');
  assert.equal(FakeEventSource.instances[0].closeCount, 1, 'the retired stream is closed exactly once');

  const pagehideOnly = new FakePageLifecycle();
  const laterFailures = [];
  const laterRetirements = [];
  const later = await startRetirementClient(pagehideOnly, {failures: laterFailures, retirements: laterRetirements});
  pagehideOnly.emit('pagehide');
  FakeEventSource.instances[0].emit('error');
  assert.equal(laterFailures.length, 0, 'pagehide alone also discriminates a teardown close');
  assert.equal(laterRetirements.length, 1);
  assert.equal(laterRetirements[0].reason, 'page_pagehide');
  client.stop();
  later.client.stop();
  assert.deepEqual(lifecycle.registered(), [], 'client stop removes every page lifecycle listener');
  await client.start();
  assert.deepEqual(lifecycle.registered(), ['beforeunload', 'pagehide', 'pageshow'], 'client restart installs one fresh lifecycle listener set');
  client.stop();
});

test('a genuine mid-session stream failure stays a warning-level transport failure through every retirement path', async () => {
  const lifecycle = new FakePageLifecycle();
  const failures = [];
  const retirements = [];
  const {client, clock} = await startRetirementClient(lifecycle, {failures, retirements});

  // 1. No unload signal at all: an ambiguous native error is still a failure.
  FakeEventSource.instances[0].emit('error');
  assert.equal(retirements.length, 0, 'a failure with no unload signal is never reclassified');
  assert.deepEqual(failures.map(entry => entry.message), ['YO!stats stream unavailable']);
  await clock.advance(100);
  assert.equal(FakeEventSource.instances.length, 2, 'the failed transport still repairs');

  // 2. A server-sent `unavailable` frame during an unload is the server failing, not the page leaving.
  lifecycle.emit('beforeunload');
  FakeEventSource.instances[1].emit('unavailable');
  assert.equal(retirements.length, 0, 'a server unavailable frame is never a page retirement');
  assert.equal(failures.length, 1, 'the failure owner stays latched until an accepted push');

  // 3. The document survived the unload it started: one stream delivery clears the unload
  //    latch, so the next genuine error is a failure again and the gate is not blinded.
  const survived = new FakePageLifecycle();
  const survivedFailures = [];
  const survivedRetirements = [];
  const surviving = await startRetirementClient(survived, {failures: survivedFailures, retirements: survivedRetirements});
  survived.emit('beforeunload');
  const deliveredBeforeSurvival = surviving.client.streamEvidence().deliverySequence;
  FakeEventSource.instances[0].emit('ready', JSON.stringify({cache_generation: 1, revision: 0}));
  assert.equal(surviving.client.streamEvidence().deliverySequence, deliveredBeforeSurvival + 1, 'the surviving document is demonstrably still transporting');
  FakeEventSource.instances[0].emit('error');
  assert.equal(survivedRetirements.length, 0, 'a delivery clears the unload latch so the next failure is not blinded');
  assert.deepEqual(survivedFailures.map(entry => entry.message), ['YO!stats stream unavailable']);

  // 4. A bfcache restore clears the latch by lifecycle alone, with no delivery required.
  const restored = new FakePageLifecycle();
  const restoredFailures = [];
  const restoredRetirements = [];
  const later = await startRetirementClient(restored, {failures: restoredFailures, retirements: restoredRetirements});
  restored.emit('pagehide');
  restored.emit('pageshow');
  FakeEventSource.instances[0].emit('error');
  assert.equal(restoredRetirements.length, 0, 'a restored page reports a genuine failure as a failure');
  assert.deepEqual(restoredFailures.map(entry => entry.message), ['YO!stats stream unavailable']);
  client.stop();
  surviving.client.stop();
  later.client.stop();
});

test('browser readiness times out, retries while visible, and stays latched until accepted push proof', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  let capabilitiesCalls = 0;
  let snapshotCalls = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') {
        capabilitiesCalls += 1;
        if (capabilitiesCalls === 1) return new Promise(() => {});
        if (capabilitiesCalls === 2) throw new Error('capabilities rejected');
        return response(200, capabilities());
      }
      snapshotCalls += 1;
      return response(200, snapshot({requested: 1, cache: 1, sourceGeneration: 1}));
    },
    EventSource: FakeEventSource,
    clientId: 'browser-readiness',
    savedRange: 300,
    savedResolution: 1,
    readinessTimeoutMs: 100,
    readinessRetryMs: 50,
    controllerOptions: {
      clock, repairBaseMs: 10, repairMaxMs: 40,
      onFailure: failure => failures.push(failure),
    },
  });
  const initialStart = client.start();
  const initialRejected = assert.rejects(initialStart, /timed out/);
  await clock.advance(100);
  assert.equal(failures.length, 1);
  await initialRejected;
  assert.equal(capabilitiesCalls, 1);
  await clock.advance(50);
  assert.equal(capabilitiesCalls, 2, 'the first bounded retry runs while document-visible');
  assert.equal(failures.length, 1, 'a rejected retry stays in the same latched episode');
  await clock.advance(50);
  assert.equal(capabilitiesCalls, 3, 'a later retry can recover capabilities');
  await clock.advance(0);
  assert.equal(snapshotCalls, 1);
  assert.equal(client.controller().generation().cache_generation, 1);
  assert.equal(FakeEventSource.instances.length, 1);
  FakeEventSource.instances[0].emit('error');
  assert.equal(failures.length, 1, 'snapshot readiness does not clear the initialization failure latch');
  await clock.advance(10);
  const repairedStream = FakeEventSource.instances[1];
  repairedStream.emit('delta', JSON.stringify(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 2,
    revision: 1,
    bucketReplacements: [{
      ...client.controller().generation().buckets[2],
      series: {cpu: seriesValue(88, 2)},
    }],
  })));
  repairedStream.emit('error');
  assert.equal(failures.length, 2, 'accepted push proof clears the latch for a later native failure');
  client.stop();
});

test('browser readiness bounds a hung initial snapshot and retries the whole client', async () => {
  ManualEventSource.instances = [];
  const clock = new FakeClock();
  const failures = [];
  let capabilitiesCalls = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') {
        capabilitiesCalls += 1;
        return response(200, capabilities());
      }
      throw new Error(`unexpected history fetch: ${url}`);
    },
    EventSource: ManualEventSource,
    clientId: 'browser-snapshot-readiness',
    savedRange: 300,
    savedResolution: 1,
    readinessTimeoutMs: 100,
    readinessRetryMs: 50,
    controllerOptions: {
      clock, repairBaseMs: 10, repairMaxMs: 40,
      onFailure: failure => failures.push(failure),
    },
  });
  await client.start();
  await clock.advance(0);
  assert.equal(ManualEventSource.instances.length, 1);
  await clock.advance(100);
  assert.equal(failures.length, 1, 'a hung initial snapshot is one bounded initialization failure');
  assert.equal(ManualEventSource.instances[0].closeCount, 1);
  await clock.advance(50);
  assert.equal(capabilitiesCalls, 2, 'readiness retry reconstructs the whole client contract');
  await clock.advance(0);
  assert.equal(ManualEventSource.instances.length, 2);
  emitSnapshotSequence(ManualEventSource.instances[1], snapshot({requested: 1, cache: 2, sourceGeneration: 2}));
  await flushPromises();
  assert.equal(client.controller().generation().cache_generation, 2);
  client.stop();
});

test('browser transport unwraps response envelopes before exact validation', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const envelope = data => ({
    state: 'ready',
    request: {id: 'r-stats-envelope'},
    data,
    ok: true,
    terminal: true,
  });
  const client = loadNamespace().createBrowserClient({
    fetch: async url => response(200, envelope(
      url === '/api/stats-capabilities'
        ? capabilities()
        : snapshot({requested: 1, cache: 7, sourceGeneration: 7}),
    )),
    EventSource: FakeEventSource,
    clientId: 'browser-envelope',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  await client.start();
  await clock.advance(0);
  assert.equal(client.controller().generation().cache_generation, 7);
  assert.equal(FakeEventSource.instances.length, 1);
  client.stop();

  const invalid = loadNamespace().createBrowserClient({
    fetch: async () => response(200, envelope({...capabilities(), unknown_inner_field: true})),
    EventSource: FakeEventSource,
    clientId: 'browser-envelope-negative',
    savedRange: 300,
    savedResolution: 1,
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });
  await assert.rejects(
    invalid.start(),
    error => error.statsContractViolation === true && /stats capabilities fields are not exact/.test(error.message),
  );
});

test('five typed stream repairs for a 24h selection each settle through one snapshot owner', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  let generation = 0;
  const snapshotRequests = [];
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotRequests.push(url);
      generation += 1;
      return response(200, snapshot({
        range: 86400,
        requested: 300,
        resolution: 300,
        cache: generation,
        sourceGeneration: generation,
      }));
    },
    EventSource: FakeEventSource,
    clientId: 'repeat-24h-repair',
    savedRange: 86400,
    savedResolution: 300,
    onState: state => states.push(state),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  await client.start();
  await clock.advance(0);
  for (let index = 0; index < 5; index += 1) {
    const stream = FakeEventSource.instances.at(-1);
    stream.emit('repair', JSON.stringify({
      status: 'repair_required',
      reason: 'delta cursor is outside the retained exact chain',
    }));
    await clock.advance(100);
    await flushPromises();
    await flushPromises();
    assert.equal(states.at(-1), 'ready', `repair ${index + 1} reaches a terminal state`);
    assert.equal(FakeEventSource.instances.length, index + 2, `repair ${index + 1} opens one replacement stream`);
    assert.equal(stream.closeCount, 1, `repair ${index + 1} closes its failed stream once`);
  }

  assert.equal(snapshotRequests.length, 6, 'initial load plus five repairs issue no duplicate snapshots');
  assert.ok(snapshotRequests.every(url => url.includes('range_seconds=86400&resolution=300')));
  client.stop();
});

test('valid pending snapshots honor the server retry hint until exact 2h/300s data is ready', async () => {
  ManualEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      throw new Error(`unexpected history fetch: ${url}`);
    },
    EventSource: ManualEventSource,
    clientId: 'pending-exact-300',
    savedRange: 7200,
    savedResolution: 300,
    onState: state => states.push(state),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  await client.start();
  await clock.advance(0);
  ManualEventSource.instances[0].emit('pending', JSON.stringify({status: 'pending', retry_after_seconds: 1}));
  await flushPromises();
  assert.equal(ManualEventSource.instances.length, 1);
  assert.equal([...clock.timers.values()].filter(timer => timer.at - clock.time === 1000).length, 1, 'one controller timer owns the pending retry');
  assert.equal(clock.nextDelay(), 1000, 'the valid server hint replaces offline backoff');
  assert.equal(client.controller().generation(), null);
  await clock.advance(999);
  assert.equal(ManualEventSource.instances.length, 1, 'pending does not reconnect before the server asks');
  await clock.advance(1);
  assert.equal(ManualEventSource.instances.length, 2);
  ManualEventSource.instances[1].emit('pending', JSON.stringify({status: 'pending', retry_after_seconds: 1}));
  await flushPromises();
  assert.equal([...clock.timers.values()].filter(timer => timer.at - clock.time === 1000).length, 1, 'a repeated pending response still owns one timer');
  assert.equal(clock.nextDelay(), 1000, 'repeated pending does not grow exponential backoff');
  await clock.advance(1000);
  assert.equal(ManualEventSource.instances.length, 3);
  emitSnapshotSequence(ManualEventSource.instances[2], snapshot({
    range: 7200, requested: 300, resolution: 300, cache: 9, sourceGeneration: 9,
  }));
  await flushPromises();

  assert.equal(client.controller().generation().range_seconds, 7200);
  assert.equal(client.controller().generation().resolution_seconds, 300);
  assert.ok(states.includes('pending'));
  assert.equal(states.at(-1), 'ready');
  assert.ok(ManualEventSource.instances.every(stream => stream.url.includes('range_seconds=7200&resolution=300')));
  client.stop();
});

test('an arbitrary 503 remains an offline error with bounded exponential retry', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  let snapshotCalls = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotCalls += 1;
      return response(503, {status: 'unavailable', retry_after_seconds: 1});
    },
    EventSource: FakeEventSource,
    clientId: 'offline-503',
    savedRange: 7200,
    savedResolution: 300,
    onState: state => states.push(state),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 250},
  });

  await client.start();
  await clock.advance(0);
  await flushPromises();
  await flushPromises();
  assert.equal(snapshotCalls, 1);
  assert.equal(states.at(-1), 'error');
  assert.equal(clock.nextDelay(), 200);
  await clock.advance(200);
  await flushPromises();
  await flushPromises();
  assert.equal(snapshotCalls, 2);
  assert.equal(clock.nextDelay(), 250, 'offline retry remains capped');
  client.stop();
});

test('terminal 503 reason reaches state and explicit retry restarts snapshot and stream', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const fetches = [];
  let healthy = false;
  const client = loadNamespace().createBrowserClient({
    fetch: async (url, options) => {
      fetches.push({url, options});
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      if (url === '/api/stats-retry') {
        healthy = true;
        return response(200, {ok: true, status: 'ready'});
      }
      if (!healthy) {
        return response(503, {
          status: 'unavailable',
          reason: 'statsd exited (2): MigrationError: unsupported retired database',
          terminal: true,
        });
      }
      return response(200, snapshot({range: 7200, requested: 300, resolution: 300}));
    },
    EventSource: FakeEventSource,
    clientId: 'terminal-retry',
    savedRange: 7200,
    savedResolution: 300,
    onState: (state, error) => states.push({state, error}),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 250},
  });

  await client.start();
  await clock.advance(0);
  await flushPromises();
  assert.equal(states.at(-1).state, 'error');
  assert.equal(states.at(-1).error.message, 'statsd exited (2): MigrationError: unsupported retired database');
  assert.equal(states.at(-1).error.terminal, true);

  await client.retry();
  await flushPromises();
  await clock.advance(250);
  await flushPromises();
  assert.equal(fetches.find(item => item.url === '/api/stats-retry').options.method, 'POST');
  assert.equal(states.at(-1).state, 'ready');
  assert.equal(FakeEventSource.instances.length, 2, 'the failed stream plus the explicit-retry stream are both observable');
  client.stop();
});

test('recoverable stale-daemon read 426 posts one automatic recovery and repairs without a click', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const fetches = [];
  let recovered = false;
  const client = loadNamespace().createBrowserClient({
    fetch: async (url, options) => {
      fetches.push({url, options});
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      if (url === '/api/stats-retry') {
        recovered = true;
        return response(200, {ok: true, status: 'ready'});
      }
      if (!recovered) return response(426, {
        status: 'upgrade_required', reason: 'stale statsd daemon handshake requires a lifecycle retry',
        required_protocol_version: 24, required_schema_generation: 6, required_build: '3',
      });
      return response(200, snapshot({range: 7200, requested: 300, resolution: 300}));
    },
    EventSource: FakeEventSource,
    clientId: 'read-fence-auto-retry',
    savedRange: 7200,
    savedResolution: 300,
    onState: (state, error) => states.push({state, error}),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 250},
  });

  await client.start();
  await clock.advance(0);
  await flushPromises();
  await flushPromises();
  await flushPromises();
  await flushPromises();
  assert.equal(states.at(-1).state, 'error');
  assert.equal(states.at(-1).error.versionFence, true);
  assert.equal(states.at(-1).error.recoverableReadFence, true);
  assert.equal(states.at(-1).error.terminal, false);
  assert.equal(states.at(-1).error.requiredProtocolVersion, 24);
  assert.equal(fetches.filter(item => item.url === '/api/stats-retry').length, 1);
  assert.equal(clock.nextDelay(), 200);
  await clock.advance(200);
  await flushPromises();
  await flushPromises();
  assert.equal(states.at(-1).state, 'ready');
  assert.equal(FakeEventSource.instances.length, 2, 'the fenced stream is replaced once after recovery');
  client.stop();
});

test('terminal writer-style 426 is not eligible for the read retry endpoint', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const fetches = [];
  const client = loadNamespace().createBrowserClient({
    fetch: async (url, options) => {
      fetches.push({url, options});
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      return response(426, {
        status: 'upgrade_required', terminal: true, reason: 'browser observation writer protocol is retired',
        required_protocol_version: 24, required_schema_generation: 6, required_build: '3',
      });
    },
    EventSource: FakeEventSource,
    clientId: 'terminal-writer-fence',
    savedRange: 7200,
    savedResolution: 300,
    onState: (state, error) => states.push({state, error}),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 250},
  });

  await client.start();
  await clock.advance(0);
  await flushPromises();
  await flushPromises();
  assert.equal(states.at(-1).state, 'error');
  assert.equal(states.at(-1).error.versionFence, true);
  assert.equal(states.at(-1).error.recoverableReadFence, false);
  assert.equal(states.at(-1).error.terminal, true);
  assert.equal(fetches.filter(item => item.url === '/api/stats-retry').length, 0);
  client.stop();
});

test('invalid streamed JSON and protocol deltas share the controller bounded repair path', async () => {
  for (const [streamedValue, immediate] of [
    ['{', true],
    [JSON.stringify(delta({range: 900, resolution: 10})), false],
  ]) {
    FakeEventSource.instances = [];
    const clock = new FakeClock();
    let snapshotFetches = 0;
    const client = loadNamespace().createBrowserClient({
      fetch: async url => {
        if (url === '/api/stats-capabilities') return response(200, capabilities());
        snapshotFetches += 1;
        return response(200, snapshot({requested: 1, cache: snapshotFetches}));
      },
      EventSource: FakeEventSource,
      clientId: 'browser-repair',
      savedRange: 300,
      savedResolution: 1,
      controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
    });
    await client.start();
    await clock.advance(0);
    const stream = FakeEventSource.instances[0];
    stream.emit('delta', streamedValue);
    stream.emit('delta', streamedValue);
    assert.equal(stream.closeCount, 1);
    await clock.advance(99);
    assert.equal(snapshotFetches, 1, 'repair timing is owned by the controller');
    await clock.advance(1);
    assert.equal(snapshotFetches, 2, 'duplicate bad events coalesce behind one repair');
    assert.equal(FakeEventSource.instances.length, 2);
    client.stop();
  }
});

test('mount owns exact capability controls and renders sparse current series without invention', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const root = new FakeDomElement();
  const fetchedSnapshots = [];
  const mounted = loadNamespace().mount(root, {
    view: 'stats',
    clientId: 'mounted-stats',
    savedRange: 300,
    savedResolution: 1,
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      const parsed = new URL(url, 'http://stats.test');
      fetchedSnapshots.push(url);
      const range = Number(parsed.searchParams.get('range_seconds'));
      const requestedText = parsed.searchParams.get('resolution');
      const requested = requestedText === 'AUTO' ? 'AUTO' : Number(requestedText);
      const resolution = requested === 'AUTO' ? (range === 300 ? 1 : 10) : requested;
      return response(200, rendererSnapshot({range, requested, resolution, cache: fetchedSnapshots.length}));
    },
    EventSource: FakeEventSource,
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  assert.deepStrictEqual([...Object.keys(mounted)].sort(), ['destroy', 'setVisible', 'start', 'stop']);
  assert.ok(root.status.innerHTML.includes('data-stats-current-state="loading"'));
  await mounted.start();
  const initialResolutionControl = root.controls.innerHTML.match(
    /<select data-stats-current-resolution[\s\S]*?<\/select>/,
  )[0];
  assert.ok(initialResolutionControl.includes('AUTO (1s)'));
  assert.ok(initialResolutionControl.includes('value="1"'));
  assert.ok(initialResolutionControl.includes('value="10"'));
  assert.equal(initialResolutionControl.includes('value="60"'), false);
  assert.equal(initialResolutionControl.includes('value="300"'), false);

  root.scrollTop = 73;
  await clock.advance(0);
  assert.equal(root.status.innerHTML, '');
  assert.equal(root.scrollTop, 73, 'chart paint preserves the mounted scroll container');
  const html = root.content.innerHTML;
  const cpu = chartMarkup(html, 'cpu');
  const agent = chartMarkup(html, 'agent-tokens');
  const modelOutput = chartMarkup(html, 'model-output-tokens');
  const modelUsage = chartMarkup(html, 'model-usage');
  assert.ok(cpu && agent && modelOutput && modelUsage);
  assert.ok(agent.includes('<h3>Session tokens/min</h3>'));
  assert.ok(cpu.includes('data-point-count="2"'));
  assert.match(cpu, /<path d="M[^"]+ L[^"]+"/);
  assert.ok(agent.includes('data-point-count="2"'));
  assert.match(agent, /<path d="M[^"]+ M[^"]+"/);
  assert.equal(/<path d="[^"]* L/.test(agent), false, 'missing token buckets are not bridged');
  assert.ok(agent.includes('data-y-min="0"') || agent.includes('data-stats-chart="agent-tokens" data-y-min="0"'));
  assert.ok(agent.includes('data-y-max="1000"'));
  assert.ok(modelOutput.includes('data-y-max="1000"'), 'agent and model-output charts share one peak');
  assert.ok(modelUsage.includes('data-y-max="5000"'), 'input/cache dimensions do not distort output parity');
  assert.ok(agent.includes('data-no-data-family="agent_tokens"'));
  assert.ok(agent.includes('data-no-data-source="usage-scan"'));
  assert.ok(agent.includes('>122_frontend-crates</li>'), 'Session tokens/min uses the canonical tmux-session label');
  assert.equal((agent.match(/>122_frontend-crates<\/li>/g) || []).length, 1, 'pane-level usage with one visible session name has one legend entry');
  assert.ok(agent.includes('data-point-value="150"'));
  assert.ok(agent.includes('data-point-value="1000"'), 'same-session pane usage is summed per bucket');
  assert.ok(agent.includes('data-axis-seconds="true"'));
  assert.match(agent, />\d{2}:\d{2}:\d{2}<\/text>/);
  const visibility = root.controls.innerHTML;
  assert.ok(visibility.indexOf('>Session tokens</button>') < visibility.indexOf('>Model tokens</button>'));
  assert.ok(visibility.indexOf('>Model tokens</button>') < visibility.indexOf('>Cost</button>'));
  assert.match(visibility, /data-stats-current-visibility="cost" aria-pressed="false">Cost/);
  assert.equal(root.content.innerHTML.includes('data-stats-chart="cost"'), false, 'the Stats cost chart starts off');
  const snapshotsBeforeToggle = fetchedSnapshots.length;
  const costToggle = {
    dataset: {statsCurrentVisibility: 'cost'},
    closest(selector) { return selector === '[data-stats-current-visibility]' ? this : null; },
  };
  root.listeners.get('click')({target: costToggle});
  assert.ok(root.content.innerHTML.includes('data-stats-chart="cost"'));
  assert.match(root.controls.innerHTML, /data-stats-current-visibility="cost" aria-pressed="true">Cost/);
  assert.equal(fetchedSnapshots.length, snapshotsBeforeToggle, 'chart visibility is presentation-only');
  root.listeners.get('click')({target: costToggle});
  assert.equal(root.content.innerHTML.includes('data-stats-chart="cost"'), false);

  const liveStream = FakeEventSource.instances[0];
  const hiddenMarkup = root.content.innerHTML;
  const hiddenSnapshotRequests = fetchedSnapshots.length;
  mounted.setVisible(false);
  liveStream.emit('delta', JSON.stringify(delta({
    sourceGeneration: 2,
    base: 1,
    cache: 2,
    revision: 1,
    bucketReplacements: [{
      ...rendererSnapshot().buckets[0],
      series: {'cpu_percent:host': seriesValue(77, 0)},
    }],
  })));
  assert.equal(liveStream.closeCount, 0, 'panel hiding does not stop the exact transport');
  assert.equal(root.content.innerHTML, hiddenMarkup, 'hidden generations skip renderer work');
  mounted.setVisible(true);
  assert.equal(fetchedSnapshots.length, hiddenSnapshotRequests, 'panel opening does not fetch a snapshot');
  assert.notEqual(root.content.innerHTML, hiddenMarkup, 'opening paints the already-accepted generation');

  await clock.advance(1000);
  assert.equal(root.scrollTop, 73);
  assert.ok(root.content.innerHTML.includes('data-point-count="2"'), 'presentation motion does not add points');

  const acceptedBeforeRangeChange = root.content.innerHTML;
  root.change({statsCurrentRange: ''}, '900');
  assert.ok(root.status.innerHTML.includes('data-stats-current-state="loading"'));
  assert.equal(root.content.innerHTML, acceptedBeforeRangeChange, 'range loading keeps the accepted chart visible until ready');
  const changedResolutionControl = root.controls.innerHTML.match(
    /<select data-stats-current-resolution[\s\S]*?<\/select>/,
  )[0];
  assert.ok(changedResolutionControl.includes('AUTO (10s)'));
  assert.ok(changedResolutionControl.includes('value="AUTO" selected'));
  assert.ok(changedResolutionControl.includes('value="10"'));
  assert.ok(changedResolutionControl.includes('value="60"'));
  assert.equal(changedResolutionControl.includes('value="1"'), false);
  assert.equal(changedResolutionControl.includes('value="300"'), false);

  await flushPromises();
  await clock.advance(0);
  await flushPromises();
  assert.ok(root.content.innerHTML.includes('data-axis-seconds="false"'));
  assert.match(root.content.innerHTML, />\d{2}:\d{2}<\/text>/);
  for (const row of capabilities().ranges) {
    root.change({statsCurrentRange: ''}, String(row.range_seconds));
    const control = root.controls.innerHTML.match(
      /<select data-stats-current-resolution[\s\S]*?<\/select>/,
    )[0];
    const values = [...control.matchAll(/<option value="([^"]+)"/g)].map(match => (
      match[1] === 'AUTO' ? 'AUTO' : Number(match[1])
    ));
    assert.deepStrictEqual(values, ['AUTO', ...row.explicit_resolution_seconds]);
    assert.ok(control.includes(`AUTO (${row.auto_resolution_seconds}s)`));
    assert.match(control, /<option value="AUTO" selected>/);
  }
  mounted.destroy();
  assert.equal(root.innerHTML, '');
  assert.equal(root.listeners.has('change'), false);
});

test('pointer zoom and hover/touch tooltips share one chart interaction path', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const documentListeners = new Map();
  const root = new FakeDomElement();
  root.ownerDocument = {
    addEventListener(name, callback) { documentListeners.set(name, callback); },
    removeEventListener(name, callback) {
      if (documentListeners.get(name) === callback) documentListeners.delete(name);
    },
  };
  root.contains = () => false;
  const mounted = loadNamespace().mount(root, {
    view: 'stats',
    clientId: 'interaction',
    savedRange: 300,
    savedResolution: 1,
    fetch: async url => url === '/api/stats-capabilities'
      ? response(200, capabilities())
      : response(200, rendererSnapshot()),
    EventSource: FakeEventSource,
    controllerOptions: {clock},
  });
  await mounted.start();
  await clock.advance(0);

  const tooltip = {hidden: true, style: {}, textContent: ''};
  const selectionRect = {
    hidden: true,
    values: {},
    setAttribute(name, value) { this.values[name] = value; },
  };
  const point = {
    dataset: {
      seriesPoint: 'system_cpu_percent', pointStart: '120', pointDuration: '1', pointValue: '12.5',
      pointSourceCount: '2', pointFirstTimestamp: '120', pointLastTimestamp: '120.5',
    },
    getAttribute(name) { return name === 'cx' ? '260' : null; },
  };
  const chart = {
    dataset: {statsChart: 'cpu'},
    querySelectorAll(selector) { return selector === '[data-series-point]' ? [point] : []; },
    querySelector(selector) { return selector === '[data-stats-current-tooltip]' ? tooltip : null; },
  };
  const svg = {
    captured: null,
    closest(selector) { return selector === '[data-stats-chart]' ? chart : null; },
    getBoundingClientRect() { return {left: 0, width: 600}; },
    querySelector(selector) { return selector === '[data-stats-current-selection]' ? selectionRect : null; },
    setPointerCapture(id) { this.captured = id; },
    releasePointerCapture(id) { if (this.captured === id) this.captured = null; },
  };
  const target = {
    closest(selector) {
      if (selector === '[data-stats-current-svg]') return svg;
      if (selector === '[data-stats-chart]') return chart;
      return null;
    },
  };
  const pointer = (clientX, pointerType = 'touch') => ({
    button: 0, clientX, pointerId: 7, pointerType, target,
    prevented: false,
    preventDefault() { this.prevented = true; },
  });

  const down = pointer(150);
  root.listeners.get('pointerdown')(down);
  const move = pointer(370);
  root.listeners.get('pointermove')(move);
  root.listeners.get('pointerup')(pointer(370));
  assert.equal(down.prevented && move.prevented, true, 'touch scrolling is suppressed only by the SVG interaction handlers');
  assert.equal(selectionRect.values.x, '150');
  assert.equal(selectionRect.values.width, '220');
  assert.match(root.controls.innerHTML, /data-stats-current-zoom-reset>Reset zoom/);
  assert.equal(root.content.innerHTML.includes('data-point-count="2"'), true, 'zoom changes only the viewport, not the exact dataset');

  root.listeners.get('click')({target: {closest: selector => (
    selector === '[data-stats-current-zoom-reset]' ? {} : null
  )}});
  assert.match(root.controls.innerHTML, /data-stats-current-zoom-reset hidden/);

  root.listeners.get('pointermove')(pointer(260, 'mouse'));
  assert.equal(tooltip.hidden, false, 'desktop pointer movement shows the indexed nearest value');
  assert.match(tooltip.textContent, /system cpu percent · 12\.5/);
  assert.match(tooltip.textContent, /2 samples · source \d{2}:\d{2}:\d{2}–\d{2}:\d{2}:\d{2}/);
  root.listeners.get('pointerleave')(pointer(260, 'mouse'));
  assert.equal(tooltip.hidden, true, 'desktop hover clears on leave');

  root.listeners.get('pointerdown')(pointer(260));
  root.listeners.get('pointerup')(pointer(260));
  assert.equal(tooltip.hidden, false, 'a touch tap pins the same value tooltip');
  documentListeners.get('pointerdown')({target: {}});
  assert.equal(tooltip.hidden, true, 'an outside tap dismisses the pinned tooltip');
  mounted.destroy();
  assert.equal(documentListeners.has('pointerdown'), false);
});

test('cost mount renders the precomputed summary and explicit scrollable details without browser totals', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const root = new FakeDomElement();
  const mounted = loadNamespace().mount(root, {
    view: 'cost',
    clientId: 'mounted-cost',
    savedRange: 300,
    savedResolution: 1,
    fetch: async url => url === '/api/stats-capabilities'
      ? response(200, capabilities())
      : response(200, rendererSnapshot()),
    EventSource: FakeEventSource,
    controllerOptions: {clock},
  });
  await mounted.start();
  await clock.advance(0);
  const html = root.content.innerHTML;
  assert.ok(html.includes('yo-stats-current-grid--compact'));
  assert.ok(html.includes('data-stats-chart="cost"'));
  assert.ok(html.includes('data-stats-chart="usage"'));
  assert.ok(html.includes('data-stats-chart="cost" data-y-min="0" data-y-max="500000"'));
  assert.ok(html.includes('data-stats-chart="usage" data-y-min="0" data-y-max="1000"'));
  assert.ok(html.includes('data-series="cost_micro_usd" data-point-count="2"'));
  assert.ok(html.includes('data-series="api_list_cost_micro_usd" data-point-count="2"'));
  assert.ok(html.includes('data-series="usage_tokens" data-point-count="2"'));
  assert.equal(html.includes('data-stats-chart="agent-tokens"'), false);
  assert.ok(html.includes('Cost Summary'));
  assert.ok(html.includes('<strong>$0.30</strong> marginal'));
  assert.ok(html.includes('<strong>$0.60</strong> list'));
  assert.ok(html.includes('Total tokens: <strong>1.2K tokens</strong>'));
  assert.ok(html.includes('Input=600, Cache read=300, 5m cache write=100, 1h cache write=0, Output=200, Other=0'));
  assert.ok(html.includes('data-stats-current-cost-more>More Info</button>'));
  assert.equal(html.includes('Cost by Model'), false, 'full report details stay out of the compact summary');

  root.listeners.get('click')({target: {
    closest: selector => selector === '[data-stats-current-cost-more]' ? {} : null,
    matches: () => false,
  }});
  const modal = root.modal.innerHTML;
  assert.ok(modal.includes('role="dialog"'));
  assert.ok(modal.includes('data-stats-current-cost-modal-scroll'));
  assert.ok(modal.includes('data-stats-current-cost-modal-close'));
  assert.ok(modal.includes('Cost by Model'));
  assert.ok(modal.includes('gpt-5.6-sol'));
  assert.ok(modal.includes('122_frontend-crates'), 'Cost by Agent uses the same canonical tmux-session label as Session tokens/min');
  assert.ok(modal.includes('>122_frontend-crates</span>'), 'Cost by Agent does not waste visible space on pane-key suffixes');
  assert.equal((modal.match(/>122_frontend-crates<\/span>/g) || []).length, 2, 'Cost by Agent preserves distinct server agent rows');
  assert.match(modal, /yo-cost-current-agent-row[\s\S]*?yo-cost-current-agent[^>]*>122_frontend-crates<\/span><\/summary>[\s\S]*?<\/th><td>600<small>/, 'the server-owned agent rows retain their individual attributions');
  assert.ok(modal.includes('Cost by Agent'));
  assert.ok(modal.includes('Source attribution'));
  assert.equal((modal.match(/Source attribution/g) || []).length, 2);
  assert.equal((modal.match(/data-stats-current-cost-table="source"/g) || []).length, 0);
  assert.ok(modal.includes('Pricing attribution'));
  assert.ok(modal.includes('$0.10 marginal · $0.20 list'));
  assert.ok(modal.includes('https://example.com/pricing'));
  assert.ok(modal.includes('Reasoning breakdown unavailable'));
  root.listeners.get('click')({target: {
    closest: () => null,
    matches: selector => selector === '[data-stats-current-cost-modal-backdrop]',
  }});
  assert.equal(root.modal.innerHTML, '', 'clicking the backdrop dismisses the explicit modal');
  mounted.destroy();
});

test('mount has one stable loading, pending, and error state owner', async () => {
  FakeEventSource.instances = [];
  const pendingClock = new FakeClock();
  const pendingRoot = new FakeDomElement();
  const pending = loadNamespace().mount(pendingRoot, {
    view: 'stats', clientId: 'pending', fetch: async url => (
      url === '/api/stats-capabilities'
        ? response(200, capabilities())
        : response(202, {status: 'pending', retry_after_seconds: 1})
    ),
    EventSource: FakeEventSource,
    controllerOptions: {clock: pendingClock, repairBaseMs: 100, repairMaxMs: 400},
  });
  await pending.start();
  assert.ok(pendingRoot.status.innerHTML.includes('loading'));
  await pendingClock.advance(0);
  const pendingMarkup = pendingRoot.status.innerHTML;
  assert.ok(pendingMarkup.includes('data-stats-current-state="pending"'));
  await pendingClock.advance(199);
  assert.equal(pendingRoot.status.innerHTML, pendingMarkup, 'retries do not churn the pending message');
  pending.destroy();

  const errorRoot = new FakeDomElement();
  const failed = loadNamespace().mount(errorRoot, {
    view: 'stats', clientId: 'failed', fetch: async () => response(500), EventSource: FakeEventSource,
  });
  await assert.rejects(failed.start(), /HTTP 500/);
  assert.ok(errorRoot.status.innerHTML.includes('data-stats-current-state="error"'));
  failed.destroy();
});

test('static build registers the current controller once without a second namespace owner', () => {
  const summary = JSON.parse(execFileSync('python3', ['-c', [
    'import json',
    'from tools.static_build import ASSETS, build_asset, lint_duplicate_functions',
    'parts = ASSETS["yolomux.js"]',
    'built = build_asset("yolomux.js")',
    'current = "static_src/js/yolomux/84_stats_current.js"',
    'legacy = "static_src/js/yolomux/85_debug_panel.js"',
    'print(json.dumps({',
    '  "current_count": parts.count(current),',
    '  "current_index": parts.index(current),',
    '  "legacy_count": parts.count(legacy),',
    '  "legacy_index": parts.index(legacy) if legacy in parts else -1,',
    '  "namespace_owner_count": built.count("globalThis.YOLOmuxStatsCurrent ="),',
    '  "namespace_position": built.index("globalThis.YOLOmuxStatsCurrent ="),',
    '  "legacy_position": built.find("const jsDebugGraphDefaultRangeSeconds"),',
    '  "duplicate_declarations": lint_duplicate_functions(),',
    '}))',
  ].join('\n')], {encoding: 'utf8'}));
  assert.equal(summary.current_count, 1);
  assert.ok(summary.legacy_count === 0 || summary.current_index < summary.legacy_index);
  assert.equal(summary.namespace_owner_count, 1);
  assert.ok(summary.legacy_position < 0 || summary.namespace_position < summary.legacy_position);
  assert.deepStrictEqual(summary.duplicate_declarations, []);
  if (fs.existsSync('static_src/js/yolomux/85_debug_panel.js')) {
    const legacySource = fs.readFileSync('static_src/js/yolomux/85_debug_panel.js', 'utf8');
    assert.equal(legacySource.includes('globalThis.YOLOmuxStatsCurrent ='), false, 'the established renderer adapter cannot own the current namespace');
  }
});

test('new controller has one namespace and no old temporal processing dependency', () => {
  const context = vm.createContext({console});
  vm.runInContext(source, context, {filename: '84_stats_current.js'});
  assert.deepStrictEqual(Object.keys(context).sort(), ['YOLOmuxStatsCurrent', 'console']);
  for (const forbidden of [
    'debugGraph', 'aggregateBucket', 'sampleHold', 'interpolate', 'prefetch', 'mergeRanges',
  ]) assert.equal(source.includes(forbidden), false, `current controller must not contain ${forbidden}`);
});

test('isolated current-stats escaping and UTF-8 validation retain their fail-closed contract', () => {
  assert.match(source, /This module is intentionally self-contained:[\s\S]*escaping and UTF-8 validation helpers cannot depend on the app bundle's lexical core helpers\./);
  assert.match(source, /function currentStatsEscape\(value\) \{\s+return String\(value\)\.replace/);
  assert.match(source, /function utf8ByteLength\(value\) \{\s+try \{\s+return encodeURIComponent\(value\)[\s\S]*?\} catch \(_error\) \{\s+return Infinity;/);
});

(async () => {
  let passed = 0;
  for (const {name, body} of tests) {
    try {
      await body();
      passed += 1;
    } catch (error) {
      console.error(`FAIL: ${name}`);
      console.error(error);
      process.exitCode = 1;
    }
  }
  console.log(`stats current UI suite: ${passed} passed, ${tests.length - passed} failed`);
})();

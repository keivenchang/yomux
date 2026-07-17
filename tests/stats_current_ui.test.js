// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const assert = require('assert');
const {execFileSync} = require('child_process');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('static_src/js/yolomux/82_stats_current.js', 'utf8');

function loadNamespace() {
  const context = vm.createContext({console});
  vm.runInContext(source, context, {filename: '82_stats_current.js'});
  return context.YOLOmuxStatsCurrent;
}

function loadController(options) {
  return loadNamespace().createController(options);
}

function capabilities() {
  const matrix = [
    [300, 1, [1, 10]],
    [900, 10, [10, 60]],
    [1800, 10, [10, 60]],
    [3600, 10, [10, 60, 300]],
    [7200, 60, [60, 300]],
    [14400, 60, [60, 300]],
    [28800, 60, [60, 300]],
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
  return Object.fromEntries(['input', 'cache_read', 'cache_write', 'output', 'other'].map(name => [
    name, changes[name] || {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
  ]));
}

function costReport(changes = {}) {
  return {
    schema_version: 2,
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

  constructor(url, options) {
    this.url = url;
    this.options = options;
    this.listeners = new Map();
    this.closeCount = 0;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(callback);
    this.listeners.set(name, listeners);
  }

  close() {
    this.closeCount += 1;
  }

  emit(name, data = '') {
    for (const callback of this.listeners.get(name) || []) callback({data});
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
    this.status = null;
    this.content = null;
    this.modal = null;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    if (this.role === 'root' && value.includes('data-stats-current-controls')) {
      this.controls = new FakeDomElement('controls');
      this.status = new FakeDomElement('status');
      this.content = new FakeDomElement('content');
      this.modal = new FakeDomElement('modal');
    } else if (this.role === 'root' && value === '') {
      this.controls = null;
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
  put(0, 'cpu_percent:host', 10);
  put(resolution, 'cpu_percent:host', 20);
  put(0, 'agent_tokens_per_minute:sol', 100);
  put(10, 'agent_tokens_per_minute:sol', 900);
  put(0, 'model_tokens_per_minute:output:gpt', 80);
  put(10, 'model_tokens_per_minute:output:gpt', 800);
  put(0, 'model_tokens_per_minute:input:gpt', 5000);
  put(0, 'cost_micro_usd', 250000);
  put(10, 'cost_micro_usd', 50000);
  put(0, 'api_list_cost_micro_usd', 500000);
  put(10, 'api_list_cost_micro_usd', 100000);
  put(0, 'usage_tokens', 1000);
  put(10, 'usage_tokens', 200);
  const modelKey = '0123456789abcdef01234567';
  const agentKey = '89abcdef0123456789abcdef';
  const evidenceKey = 'fedcba9876543210fedcba98';
  const dimensions = costDimensions({
    input: {tokens: 600, micro_usd: 120000, api_list_micro_usd: 240000},
    cache_read: {tokens: 300, micro_usd: 30000, api_list_micro_usd: 60000},
    cache_write: {tokens: 100, micro_usd: 50000, api_list_micro_usd: 100000},
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
  result.cost_report = costReport({
    total_micro_usd: 300000,
    total_api_list_micro_usd: 600000,
    total_tokens: 1200,
    dimensions,
    priced: {atoms: 4, tokens: 1150},
    unpriced: {atoms: 1, tokens: 50},
    models: [{key: modelKey, provider: 'openai', model: 'gpt-5.6-sol', ...attribution}],
    agents: [{key: agentKey, source: 'codex', label: 'yo8881|0|codex', ...attribution}],
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
  await Promise.resolve();
  await Promise.resolve();
}

const tests = [];
function test(name, body) {
  tests.push({name, body});
}

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
  assert.equal(requests, 26, 'the test traverses every AUTO and explicit server cell');

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

test('validates a whole snapshot before atomically replacing the generation', () => {
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
  const withGap = snapshot({requested: 1, cache: 2});
  withGap.no_data = [{
    family: 'gpu', source_id: 'gpu:0', start: 10, end: 20, epoch: 'gpu-e1',
    reason: 'coverage_gap', source_cadence_seconds: 10,
  }];
  assert.equal(controller.acceptSnapshot(withGap), true);
  const empty = {...snapshot({requested: 1, cache: 3}), buckets: [], rightmost_open: false};
  assert.equal(controller.acceptSnapshot(empty), true, 'a validated empty generation is ready data');
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
  await clock.advance(100);
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
  await clock.advance(0);
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
    [3600, 10, 10_000],
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

  for (const range of [900, 3600]) {
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
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const fetches = [];
  let current300Generation = 1;
  const fetchImpl = async (url, options) => {
    fetches.push({url, options});
    if (url === '/api/stats-capabilities') return response(200, capabilities());
    const parsed = new URL(url, 'http://stats.test');
    assert.equal(parsed.pathname, '/api/stats-snapshot');
    const range = Number(parsed.searchParams.get('range_seconds'));
    const requestedText = parsed.searchParams.get('resolution');
    const requested = requestedText === 'AUTO' ? 'AUTO' : Number(requestedText);
    const resolution = requested === 'AUTO' ? (range === 300 ? 1 : 10) : requested;
    const since = Number(parsed.searchParams.get('since_generation'));
    if (range === 900 && since === 3) {
      return response(304);
    }
    return response(200, snapshot({
      range,
      requested,
      resolution,
      cache: range === 300 ? current300Generation : 3,
      sourceGeneration: range === 300 ? current300Generation : 3,
    }));
  };
  const client = loadNamespace().createBrowserClient({
    fetch: fetchImpl,
    EventSource: FakeEventSource,
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
  assert.equal(fetches[1].url, [
    '/api/stats-snapshot?range_seconds=300&resolution=1',
    'client_id=browser-current&since_generation=0',
  ].join('&'));
  for (const call of fetches) {
    assert.equal(call.options.method, 'GET');
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.cache, 'no-store');
    assert.equal(call.options.headers.Accept, 'application/json');
  }
  assert.equal(FakeEventSource.instances.length, 1);
  const initialStream = FakeEventSource.instances[0];
  assert.equal(initialStream.options.withCredentials, true);
  assert.equal(initialStream.url, [
    '/api/stats-stream?range_seconds=300&resolution_seconds=1',
    'client_id=browser-current&after_cache_generation=1&after_revision=0',
  ].join('&'));
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
  current300Generation = 2;
  assert.equal(client.controller().generation().cache_generation, 2);
  assert.equal(client.controller().presentation().delta_revision, 1);
  assert.equal(FakeEventSource.instances.length, 1, 'accepted deltas do not reopen the live stream');

  initialStream.emit('error');
  initialStream.emit('error');
  assert.equal(initialStream.closeCount, 1, 'the failed native reconnect owner is closed once');
  await clock.advance(0);
  assert.equal(fetches.filter(item => item.url.includes('range_seconds=300') && item.url.includes('since_generation=0')).length, 2, 'stream repair requests one authoritative snapshot instead of a 304 loop');
  assert.equal(FakeEventSource.instances.length, 2, 'one full repair reopens one stream');
  assert.ok(FakeEventSource.instances[1].url.endsWith('after_cache_generation=2&after_revision=0'));

  client.select(900, 10);
  assert.equal(FakeEventSource.instances[1].closeCount, 1);
  client.setVisible(true);
  await client.start();
  await clock.advance(0);
  assert.ok(fetches.at(-1).url.includes('range_seconds=900&resolution=10'));
  assert.equal(FakeEventSource.instances.length, 3);
  assert.ok(FakeEventSource.instances[2].url.includes('range_seconds=900&resolution_seconds=10'));

  client.setVisible(false);
  assert.equal(FakeEventSource.instances[2].closeCount, 1);
  FakeEventSource.instances[2].emit('repair');
  await clock.advance(0);
  assert.equal(FakeEventSource.instances.length, 3, 'closed-stream events cannot create duplicates');
  client.setVisible(true);
  await clock.advance(0);
  assert.equal(FakeEventSource.instances.length, 4, 'visibility repair creates one current stream');
  await client.start();
  assert.equal(FakeEventSource.instances.length, 4, 'duplicate start keeps the active stream');
  assert.equal(fetches.filter(item => item.url === '/api/stats-capabilities').length, 1);

  client.stop();
  assert.equal(FakeEventSource.instances[3].closeCount, 1);
  await client.start();
  assert.equal(FakeEventSource.instances.length, 5, 'restart reuses capabilities and the accepted cursor');
  assert.equal(fetches.filter(item => item.url === '/api/stats-capabilities').length, 1);
  client.stop();
  assert.ok(fetches.every(item => !item.url.includes('/api/stats-delta')));
});

test('valid pending snapshots honor the server retry hint until exact 2h/300s data is ready', async () => {
  FakeEventSource.instances = [];
  const clock = new FakeClock();
  const states = [];
  const snapshotUrls = [];
  let snapshotCalls = 0;
  const client = loadNamespace().createBrowserClient({
    fetch: async url => {
      if (url === '/api/stats-capabilities') return response(200, capabilities());
      snapshotUrls.push(url);
      snapshotCalls += 1;
      if (snapshotCalls <= 2) {
        return response(503, {status: 'pending', retry_after_seconds: 1});
      }
      return response(200, snapshot({
        range: 7200,
        requested: 300,
        resolution: 300,
        cache: 9,
        sourceGeneration: 9,
      }));
    },
    EventSource: FakeEventSource,
    clientId: 'pending-exact-300',
    savedRange: 7200,
    savedResolution: 300,
    onState: state => states.push(state),
    controllerOptions: {clock, repairBaseMs: 100, repairMaxMs: 400},
  });

  await client.start();
  await clock.advance(0);
  await flushPromises();
  await flushPromises();
  assert.equal(snapshotCalls, 1);
  assert.equal([...clock.timers.values()].filter(timer => timer.at - clock.time === 1000).length, 1, 'one controller timer owns the pending retry');
  assert.equal(clock.nextDelay(), 1000, 'the valid server hint replaces offline backoff');
  assert.equal(client.controller().generation(), null);
  await clock.advance(999);
  assert.equal(snapshotCalls, 1, 'pending does not poll before the server asks');
  await clock.advance(1);
  await flushPromises();
  await flushPromises();
  assert.equal(snapshotCalls, 2);
  assert.equal([...clock.timers.values()].filter(timer => timer.at - clock.time === 1000).length, 1, 'a repeated pending response still owns one timer');
  assert.equal(clock.nextDelay(), 1000, 'repeated pending does not grow exponential backoff');
  await clock.advance(1000);
  await flushPromises();
  await flushPromises();

  assert.equal(snapshotCalls, 3);
  assert.equal(client.controller().generation().range_seconds, 7200);
  assert.equal(client.controller().generation().resolution_seconds, 300);
  assert.ok(states.includes('pending'));
  assert.equal(states.at(-1), 'ready');
  assert.equal(FakeEventSource.instances.length, 1, 'success opens one stream without a second request owner');
  assert.ok(snapshotUrls.every(url => url.includes('range_seconds=7200&resolution=300')));
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
  await clock.advance(0);
  await flushPromises();
  assert.equal(fetches.find(item => item.url === '/api/stats-retry').options.method, 'POST');
  assert.equal(states.at(-1).state, 'ready');
  assert.equal(FakeEventSource.instances.length, 1);
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
    await clock.advance(immediate ? 0 : 99);
    assert.equal(snapshotFetches, immediate ? 2 : 1, 'repair timing is owned by the controller');
    if (!immediate) await clock.advance(1);
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
  assert.ok(cpu.includes('data-point-count="2"'));
  assert.match(cpu, /<path d="M[^"]+ L[^"]+"/);
  assert.ok(agent.includes('data-point-count="2"'));
  assert.match(agent, /<path d="M[^"]+ M[^"]+"/);
  assert.equal(/<path d="[^"]* L/.test(agent), false, 'missing token buckets are not bridged');
  assert.ok(agent.includes('data-y-min="0"') || agent.includes('data-stats-chart="agent-tokens" data-y-min="0"'));
  assert.ok(agent.includes('data-y-max="900"'));
  assert.ok(modelOutput.includes('data-y-max="900"'), 'agent and model-output charts share one peak');
  assert.ok(modelUsage.includes('data-y-max="5000"'), 'input/cache dimensions do not distort output parity');
  assert.ok(agent.includes('data-no-data-family="agent_tokens"'));
  assert.ok(agent.includes('data-no-data-source="usage-scan"'));
  assert.ok(agent.includes('data-axis-seconds="true"'));
  assert.match(agent, />\d{2}:\d{2}:\d{2}<\/text>/);
  const visibility = root.controls.innerHTML;
  assert.ok(visibility.indexOf('>Agent tokens</button>') < visibility.indexOf('>Model tokens</button>'));
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

  await clock.advance(1000);
  assert.equal(root.scrollTop, 73);
  assert.ok(root.content.innerHTML.includes('data-point-count="2"'), 'presentation motion does not add points');

  root.change({statsCurrentRange: ''}, '900');
  assert.ok(root.status.innerHTML.includes('data-stats-current-state="loading"'));
  assert.equal(root.content.innerHTML, '');
  const changedResolutionControl = root.controls.innerHTML.match(
    /<select data-stats-current-resolution[\s\S]*?<\/select>/,
  )[0];
  assert.ok(changedResolutionControl.includes('AUTO (10s)'));
  assert.ok(changedResolutionControl.includes('value="10"'));
  assert.ok(changedResolutionControl.includes('value="60"'));
  assert.equal(changedResolutionControl.includes('value="1"'), false);
  assert.equal(changedResolutionControl.includes('value="300"'), false);

  await clock.advance(0);
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
  assert.ok(html.includes('Marginal <strong>$0.30</strong>'));
  assert.ok(html.includes('At API list prices <strong>$0.60</strong>'));
  assert.ok(html.includes('Total tokens: <strong>1.2K tokens</strong>'));
  assert.ok(html.includes('Input=600, Cached=300, Cache write=100, Output=200, Other=0'));
  assert.ok(html.includes('data-stats-current-cost-more>More Info</button>'));
  assert.equal(html.includes('Model Usages'), false, 'full report details stay out of the compact summary');

  root.listeners.get('click')({target: {
    closest: selector => selector === '[data-stats-current-cost-more]' ? {} : null,
    matches: () => false,
  }});
  const modal = root.modal.innerHTML;
  assert.ok(modal.includes('role="dialog"'));
  assert.ok(modal.includes('data-stats-current-cost-modal-scroll'));
  assert.ok(modal.includes('data-stats-current-cost-modal-close'));
  assert.ok(modal.includes('Model Usages'));
  assert.ok(modal.includes('gpt-5.6-sol'));
  assert.ok(modal.includes('By Agent'));
  assert.ok(modal.includes('Pricing attribution'));
  assert.ok(modal.includes('Marginal $0.10 · At API list prices $0.20'));
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
        : response(503, {status: 'pending', retry_after_seconds: 1})
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
    'current = "static_src/js/yolomux/82_stats_current.js"',
    'legacy = "static_src/js/yolomux/83_debug_panel.js"',
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
  if (fs.existsSync('static_src/js/yolomux/83_debug_panel.js')) {
    const legacySource = fs.readFileSync('static_src/js/yolomux/83_debug_panel.js', 'utf8');
    assert.equal(legacySource.includes('globalThis.YOLOmuxStatsCurrent ='), false, 'the established renderer adapter cannot own the current namespace');
  }
});

test('new controller has one namespace and no old temporal processing dependency', () => {
  const context = vm.createContext({console});
  vm.runInContext(source, context, {filename: '82_stats_current.js'});
  assert.deepStrictEqual(Object.keys(context).sort(), ['YOLOmuxStatsCurrent', 'console']);
  for (const forbidden of [
    'debugGraph', 'aggregateBucket', 'sampleHold', 'interpolate', 'prefetch', 'mergeRanges',
  ]) assert.equal(source.includes(forbidden), false, `current controller must not contain ${forbidden}`);
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

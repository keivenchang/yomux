// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
// The Daemons roster front end: the System panel renders `local_services` schema 5 -- the
// snapshot-level `health` provenance block and the per-row `health` block -- through the ONE
// metric-envelope cell renderer, as ONE service roster with one disclosure per row.
//
// Every test here drives the real functions sliced out of static_src/js/yolomux/85_debug_panel.js.
// There is no second renderer and no second state map in this file: `debugSystemMetricText` is the
// same function the three process metrics already went through, the health columns call it, and
// the web row and the tmux child row call it for their structural absences too.
//
// The permanent negative controls this shard owns, because each one is a defect that a green suite
// has hidden before:
//   * a partial-coverage count rendered as if it were complete,
//   * a stale snapshot rendered as current,
//   * an unobserved value rendered as 0,
//   * a column nobody observes (the web process's restarts/requests/errors/latency) rendered as 0,
//   * a row's diagnostics built into the default DOM and merely hidden with CSS.

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = [
  'static_src/js/yolomux/84_debug_observation.js',
  'static_src/js/yolomux/85_debug_panel.js',
].map(path => fs.readFileSync(path, 'utf8')).join('\n');
const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const i18nSource = fs.readFileSync('static_src/js/yolomux/05_i18n.js', 'utf8');
const css = fs.readFileSync('static_src/css/yolomux/30_preferences_changes.css', 'utf8');
const localeEn = JSON.parse(fs.readFileSync('static_src/locales/en.json', 'utf8'));

let passed = 0;
let failed = 0;

function test(name, body) {
  try {
    body();
    passed += 1;
  } catch (error) {
    failed += 1;
    console.error(`FAIL: ${name}`);
    console.error(error.stack || error);
  }
}

function slice(text, startNeedle, endNeedle) {
  const start = text.indexOf(startNeedle);
  assert.notEqual(start, -1, `${startNeedle} exists`);
  const end = text.indexOf(endNeedle, start);
  assert.notEqual(end, -1, `${endNeedle} follows ${startNeedle}`);
  return text.slice(start, end);
}

function sourceFunction(name, nextName) {
  return slice(source, `function ${name}(`, `\nfunction ${nextName}(`);
}

// The whole render block, verbatim, in contiguous slices: the tmux-watcher vocabulary, the
// constants, the shared metric-envelope cell renderer, the provenance/coverage/transition helpers,
// the roster adapter and renderer, the schema guard, and the rehomed per-row diagnostics.
const RENDER_SOURCE = [
  slice(source, 'const DEBUG_SYSTEM_TMUX_WATCHER_DEFAULT_REASONS', '\n// NO staleness threshold lives here any more'),
  // One contiguous slice, where there used to be two with the retired per-cell renderer wedged
  // between them: the constants, the cell renderers, the roster, and the schema guard the roster
  // now reads on its own.
  slice(source, 'const DEBUG_SYSTEM_HEALTH_COUNT_KEYS', '\nfunction debugSystemRolesHtml('),
  slice(source, 'function debugSystemSamplerFamilyEntries(', '\nfunction debugSystemCpuBudgetCardHtml('),
  sourceFunction('debugSystemCpuBudgetCardHtml', 'debugSystemSummaryStripHtml'),
  sourceFunction('debugSystemAlertsHtml', 'debugSystemAdvancedHtml'),
  // The summary strip and its generated-age helper: the strip is one of the four surfaces that
  // carried a live region, so the live-region contract has to render the real one.
  sourceFunction('debugSystemSummaryStripHtml', 'debugSystemAlertsHtml'),
  sourceFunction('debugSystemAnnounceHtml', 'debugSystemRegionHtml'),
  sourceFunction('debugSystemGeneratedAge', 'debugSystemSamplerFamilySeconds'),
].join('\n');

// One rendered roster row, so a per-row assertion cannot accidentally match the next row's paint.
function rosterRow(html, id) {
  return slice(html, `data-subsystem-id="${id}"`, '</tr>');
}

const SHARED_SOURCE = [
  slice(coreSource, 'function esc(', '\nconst disclosureChevronGlyph'),
  slice(i18nSource, 'function relativeTimeFormat(', '\nfunction compactRelativeTimeFormat('),
  sourceFunction('debugGraphTerseTimeText', 'debugGraphTerseBytesText'),
  sourceFunction('debugGraphTerseBytesText', 'debugGraphAxisValueText'),
  sourceFunction('debugGraphUptimeText', 'debugGraphBytesText'),
  // The absolute wall-clock label the "History retained since" row renders from
  // `observer_epoch_started_at`. `debugGraphTimeLabel` typeof-guards the optional
  // `localizedDateTimeFormat`, so it renders here without the full i18n date module.
  sourceFunction('debugGraphLocalDateKey', 'debugGraphTimeLabel'),
  sourceFunction('debugGraphTimeLabel', 'debugGraphExactTimeLabel'),
  sourceFunction('debugSystemNumber', 'debugSystemRowsHtml'),
  sourceFunction('debugSystemRowsHtml', 'debugSystemCardHtml'),
  // The card shell, needed by the CPU-budget card below: it is the surface that coerced the
  // backend's `stale` state to `ok`, so it has to be rendered here, not just read.
  slice(source, 'function debugSystemCardHtml(', '\nconst DEBUG_SYSTEM_TMUX_WATCHER_DEFAULT_REASONS'),
].join('\n');

function translate(key, params = {}) {
  const template = String(localeEn[key] ?? key);
  return template.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match));
}

function renderContext(extra = {}) {
  return vm.createContext({
    Array,
    Boolean,
    Date,
    Intl,
    JSON,
    Map,
    Math,
    Number,
    Object,
    Set,
    String,
    console,
    result: null,
    i18nActiveLocale: 'en',
    t: translate,
    // The panel's own request state. The web row reports a failed status fetch as its reason; the
    // roster never invents one.
    jsDebugSystemState: {payload: null, error: '', inFlight: false, updatedAt: 0},
    ...extra,
  });
}

// One shared fixture builder. Tests override exactly the field under test so a failure names it.
function healthSnapshot(overrides = {}) {
  return {
    available: true,
    reason_code: '',
    schema_version: 1,
    // A deliberately non-live port: tests/test_test_isolation.py forbids a `:<live port>`
    // literal in an automated test, and the roster renders this one verbatim beside the web row.
    port: 7999,
    observer_epoch: 'ab12cd34',
    observer_epoch_started_at: 1000,
    revision: 812,
    written_at: 1900,
    // `age_seconds` is how long since a service last CHANGED state. The liveness fields below
    // are how long since the observer last LOOKED. They are different facts with different
    // producers, and conflating them is what made a quiet system report its monitor as dead.
    age_seconds: 2.0,
    observer_alive: true,
    observer_cycle_age_seconds: 2.0,
    observer_cycles: 42,
    observer_liveness_reason_code: '',
    history_coverage: 'full',
    history_reset_reason: '',
    persistence_state: 'ok',
    persistence_reason_code: '',
    resources: 6,
    ...overrides,
  };
}

function measured(value) {
  return {state: 'measured', value, reason_code: '', reason: ''};
}

function absent(reasonCode, reason) {
  return {state: 'unavailable', value: null, reason_code: reasonCode, reason};
}

function serviceHealth(overrides = {}) {
  return {
    observed: true,
    unavailable_reason_code: '',
    state: 'ready',
    reason_code: 'none',
    recovery_outcome: 'none',
    process_epoch: 'pid:4242:start:98',
    pid: 4242,
    observed_at: 1900,
    since_revision: 700,
    since_wall_time: 1000,
    state_age_seconds: 900,
    transitions: [
      {revision: 3, wall_time: 1000, previous_state: 'starting', new_state: 'ready', reason_code: 'none', process_epoch: 'pid:4242:start:98', recovery_outcome: 'none'},
    ],
    transitions_total: 1,
    transitions_truncated: false,
    errors_by_reason: {},
    coverage: {
      retained_counters: 'full',
      retained_counter_reasons: [],
      lifecycle: 'full',
      lifecycle_reasons: [],
      counters: 'full',
      counter_reasons: [],
      counter_scope: 'web_process',
    },
    metrics: {
      restart_count: measured(3),
      process_start_count: measured(4),
      demand_start_count: measured(3),
      unexpected_restart_count: measured(0),
      observations: measured(450),
      request_count: measured(1204),
      error_count: measured(7),
      completed_count: measured(1197),
      latency_average_ms: measured(12.5),
      latency_max_ms: measured(340),
    },
    ...overrides,
  };
}

function serviceRow(id, overrides = {}, healthOverrides = {}) {
  return {
    id,
    service: id,
    label: id,
    state: 'running',
    reason_code: '',
    reason: '',
    pid: 4242,
    metrics: {
      cpu_now_percent: measured(2.0),
      rss_bytes: measured(48 * 1024 * 1024),
      uptime_seconds: measured(3600),
    },
    health: serviceHealth(healthOverrides),
    ...overrides,
  };
}

function localServices(overrides = {}, serviceOverrides = {}) {
  return {
    schema_version: 5,
    inventory: ['statsd'],
    services: [serviceRow('statsd', overrides.serviceExtra || {}, serviceOverrides)],
    health: healthSnapshot(overrides.health || {}),
  };
}

// The web process publishes the SAME metric envelopes as every service row -- see
// `yolomux_lib/app.py:system_status_server_block`. It used to publish plain floats, which is how an
// unsampled value reached this panel as a finite `0` and was stamped `measured`.
const WEB_SERVER = {
  version: '0.7.1',
  pid: 5150,
  started_at: 1000,
  uptime_seconds: measured(8040),
  cpu_percent: measured(3.0),
  system_cpu_percent: measured(11.0),
  rss_bytes: measured(88 * 1024 * 1024),
};

// What the backend publishes before statsd's first push: three typed absences, one reason.
const UNPUSHED_SAMPLE_REASON = 'statsd has not pushed a CPU sample to this web process yet, so its CPU and memory have not been measured';
const WEB_SERVER_NEVER_SAMPLED = {
  version: '0.7.1',
  pid: 5150,
  started_at: 1000,
  uptime_seconds: measured(8040),
  cpu_percent: absent('cpu_sample_not_pushed', UNPUSHED_SAMPLE_REASON),
  system_cpu_percent: absent('cpu_sample_not_pushed', UNPUSHED_SAMPLE_REASON),
  rss_bytes: absent('cpu_sample_not_pushed', UNPUSHED_SAMPLE_REASON),
};

// The CORE body only. `refresh`, `top_endpoints`, `top_background_work`, `top_event_types`,
// `login_throttle`, `largest_active_transcripts`, `transcripts_cache` and `owner.debug`/
// `owner.control` moved to `/api/system-status/advanced` when the snapshot split landed, so a
// fixture that still carried them here would be describing a body the server no longer sends.
function payloadFor(payloadLocalServices, extra = {}) {
  return {
    ok: true,
    generated_at: 1902,
    state_dir: '/fixture/state',
    server: WEB_SERVER,
    owner: {},
    search_index: {},
    caches: {},
    client_events: {},
    chat: {},
    cpu_budget: {},
    tmux_signal_watcher: {state: 'attached', demanded: true, sessions: ['debug'], process_pid: 9001},
    local_services: payloadLocalServices,
    ...extra,
  };
}

function renderRoster(payloadLocalServices, {nowSeconds = 1902, expanded = [], extra = {}} = {}) {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  context.fixture = payloadFor(payloadLocalServices, extra);
  context.nowSeconds = nowSeconds;
  context.expanded = expanded;
  vm.runInContext('result = debugSystemRosterHtml(fixture, {nowSeconds, expanded: new Set(expanded)});', context);
  return String(context.result);
}

// A row expanded, because everything the disclosure owns is only BUILT when it is open.
function renderStatsdOpen(payloadLocalServices, nowSeconds = 1902) {
  return renderRoster(payloadLocalServices, {nowSeconds, expanded: ['statsd']});
}

function renderSnapshot(health) {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  context.fixture = health;
  vm.runInContext('result = debugSystemHealthSnapshotHtml(fixture);', context);
  return String(context.result);
}

// The other surface an explanation can reach: the compact alert slot above the roster. Paired with
// `renderSnapshot` so a test can assert a sentence appears on exactly ONE of the two.
function renderAlerts(healthOverrides = {}) {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  context.fixture = payloadFor(localServices({health: healthOverrides}));
  vm.runInContext('result = debugSystemAlertsHtml(fixture);', context);
  return String(context.result);
}

// -- the guard ---------------------------------------------------------------------------------

test('the panel guard renders schema 5, the version yolomux_lib/local_service_projection.py publishes', () => {
  const guarded = [...source.matchAll(/Number\(payload\.local_services\?\.schema_version\) === (\d+)/g)].map(match => match[1]);
  assert.deepEqual(guarded, ['5'], 'the panel must guard exactly once, on the published schema version');
  const projection = fs.readFileSync('yolomux_lib/local_service_projection.py', 'utf8');
  assert.match(projection, /^LOCAL_SERVICES_SCHEMA_VERSION = 5$/m, 'producer and consumer pin the same number');
});

test('an unsupported schema renders every health column as its reason, never as a number', () => {
  const supported = renderRoster(localServices());
  assert.match(supported, /data-subsystem-health-metric="process_start_count" data-metric-state="measured">4</);

  // The immediately-previous schema (2) is now refused: removing the dead `alert` key was a shape
  // change, so a schema-2 payload must fall through to the typed unsupported state, not render.
  const stale = localServices();
  stale.schema_version = 3;
  const unsupported = renderRoster(stale);
  // `coverage="unavailable"`, not `"full"`. Coverage is read out of the payload's own health block,
  // and this panel has just said it cannot read that payload -- so claiming full coverage was one
  // more fact borrowed from the schema it declared unrenderable.
  assert.match(unsupported, /data-subsystem-health-metric="process_start_count" data-metric-state="unavailable" title="the backend published a local-services schema this panel does not render" data-metric-reason="schema_unsupported">—</);
  assert.doesNotMatch(unsupported, /data-subsystem-health-metric="process_start_count"[^>]*>4</, 'an unrendered schema must not publish its numbers as measured');
  // Every metric column, not just the health ones. The process metrics are typed envelopes from
  // the same payload, and forwarding them raw printed a measured memory figure on a row that had
  // just declared the payload unreadable.
  for (const column of ['cpu_now_percent', 'rss_bytes', 'uptime_seconds', 'request_count', 'error_count']) {
    assert.match(unsupported, new RegExp(`data-subsystem-(?:health-)?metric="${column}" data-metric-state="unavailable"[^>]*data-metric-reason="schema_unsupported">—`), column);
  }
});

test('an unsupported schema is ONE typed roster state, through the same tone and label owners', () => {
  // What this replaces: a whole second renderer. An unsupported schema used to fall through to a
  // retained per-cell table -- its own classifier, its own lifecycle state map, its own freshness
  // rules -- built as a card inside Advanced. The roster already covers the case, so it says it in
  // one typed state that goes through `debugSystemStateTone` and `debugSystemRosterStateLabel`
  // exactly like every other state.
  const stale = localServices();
  stale.schema_version = 2;
  const html = renderRoster(stale);
  // ONE row, and it is not a per-service row. A schema mismatch means the panel does not know
  // which services exist, so it lists none of them -- `statsd` is in this payload's inventory and
  // must NOT appear, because believing that inventory is the same trust the version guard denies.
  const ids = [...html.matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(ids, ['web', 'tmux-signal-watcher', 'local-services']);
  const row = rosterRow(html, 'local-services');
  assert.match(row, /data-subsystem-state="schema_unsupported"/);
  assert.match(row, /data-subsystem-tone="warn"/, 'the panel cannot render the payload; that is not the daemon being down');
  assert.match(row, /<span data-subsystem-state-label>Unsupported<\/span>/);
  assert.match(row, /<span class="js-debug-roster-reason" data-subsystem-reason>the backend published a local-services schema this panel does not render<\/span>/);

  // The published state is deliberately NOT forwarded: this panel cannot know what an unrendered
  // schema's tokens mean, and repeating one would be the same unearned confidence as printing its
  // numbers. The supported payload publishes `running`, and the unsupported one must not echo it.
  assert.match(rosterRow(renderRoster(localServices()), 'statsd'), /data-subsystem-state="running"/);
  assert.doesNotMatch(row, /data-subsystem-state="running"/, 'the unsupported row must not echo a state it cannot interpret');

  // Both maps are the ONE owner each: the state resolves through them, so it cannot paint or read
  // differently from any other state.
  const tones = slice(source, 'const DEBUG_SYSTEM_STATE_TONES', '\nfunction debugSystemStateTone(');
  const labels = slice(source, 'const DEBUG_SYSTEM_ROSTER_STATE_LABEL_KEYS', '\nfunction debugSystemRosterStateLabel(');
  assert.match(tones, /schema_unsupported: 'warn',/);
  assert.match(labels, /schema_unsupported: 'debug\.system\.roster\.state\.unsupported',/);
  assert.equal(localeEn['debug.system.roster.state.unsupported'], 'Unsupported', 'the label is localized, not a raw token');
});

test('a genuinely FUTURE-shaped payload still says it cannot be rendered', () => {
  // THE REPRO. The earlier test set a previous schema on an otherwise current-shaped fixture, so
  // every familiar field was still present and the per-service rows got built anyway -- the typed
  // state appeared, but only because the payload happened to have the shape this panel knows. A
  // payload from a genuinely newer schema need not carry `inventory` or `services` at all, and then
  // the adapter produced NO service rows and the roster rendered as though the web process were the
  // only thing running. Nothing anywhere said the panel could not read it.
  for (const shape of [{schema_version: 6}, {schema_version: 6, inventory: [], services: []}]) {
    const html = renderRoster(shape);
    const ids = [...html.matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
    assert.deepEqual(ids, ['web', 'tmux-signal-watcher', 'local-services'], JSON.stringify(shape));
    const row = rosterRow(html, 'local-services');
    assert.match(row, /data-subsystem-state="schema_unsupported"/, JSON.stringify(shape));
    assert.match(row, /data-subsystem-tone="warn"/, JSON.stringify(shape));
    assert.match(row, /<span class="js-debug-roster-reason" data-subsystem-reason>the backend published a local-services schema this panel does not render<\/span>/);
    // The row names the block it stands for, and does not invent a daemon id beside it.
    assert.match(row, /<span class="js-debug-system-service-name">Local services<\/span>/);
    assert.doesNotMatch(row, /js-debug-roster-qualifier/, 'there is no daemon id to qualify it with');
  }

  // NEGATIVE CONTROL: a SUPPORTED payload with an empty inventory has genuinely nothing to list
  // and must not grow a warning row.
  const supported = renderRoster({schema_version: 5, inventory: [], services: []});
  const supportedIds = [...supported.matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(supportedIds, ['web', 'tmux-signal-watcher']);
  assert.doesNotMatch(supported, /schema_unsupported/);
});

test('an unsupported schema row interprets NO field from the schema it cannot render', () => {
  // The second half of the same finding. The adapter kept the payload's `health` and process
  // objects, so opening the disclosure read observations, transitions, a pid and process metrics
  // straight out of the very schema the row had just declared unreadable -- rendered as measured.
  // The rule is now absolute rather than field-by-field: an unreadable payload is never iterated
  // and never read, so there is no per-service row for any of it to reach.
  const future = {
    schema_version: 6,
    inventory: ['statsd'],
    services: [serviceRow('statsd', {pid: 4242, state: 'running'})],
  };
  const html = renderRoster(future, {expanded: ['statsd', 'local-services']});
  assert.doesNotMatch(html, /data-subsystem-id="statsd"/, 'an unreadable inventory is not an inventory');
  assert.doesNotMatch(html, />4242</, 'a pid read from an unrenderable schema is a fabricated identity');
  assert.doesNotMatch(html, />48\.0MB</, 'and so is a memory reading');
  assert.doesNotMatch(html, /data-subsystem-transitions/, 'no transition history from an unreadable schema');
  assert.doesNotMatch(html, /observer samples: 450/, 'no observation counts from an unreadable schema');
  assert.doesNotMatch(html, /pid:4242:start:98/, 'no process epoch from an unreadable schema');
  // Opening the one row it DOES build reports the absence, not a blank.
  const detail = slice(html, 'id="js-debug-roster-detail-local-services"', '</tr>');
  assert.match(detail, /the backend published a local-services schema this panel does not render/);

  // NEGATIVE CONTROL: the SAME fixture at schema 5 renders all of it, so the assertions above are
  // measuring the guard and not a fixture that never had the data.
  const supported = {...future, schema_version: 5};
  const supportedRow = rosterRow(renderStatsdOpen(supported), 'statsd');
  assert.match(supportedRow, /data-subsystem-metric="rss_bytes" data-metric-state="measured"/);
  assert.match(slice(renderStatsdOpen(supported), 'id="js-debug-roster-detail-statsd"', '</tr>'), /observer samples: 450/);
});

test('NO value from an unreadable payload reaches the HTML, by whole-output sweep', () => {
  // THE REPRO for a false green. The previous version of this contract asserted per-FIELD absence
  // -- no pid here, no memory there -- and passed while `health.port` was read one branch ABOVE
  // the schema guard and handed to the web row as its qualifier. An unsupported payload still
  // changed the rendered output, and 49 tests stayed green.
  //
  // So this asserts over the whole rendered string, once per sentinel, rather than over the fields
  // someone thought to check. Each sentinel is a value that only this payload could have supplied.
  const sentinels = {
    port: '42424',
    inventory: 'sentinel-inventory-service',
    label: 'SentinelLabel',
    pid: '987654',
    epoch: 'sentinel-epoch-token',
    reason: 'sentinel-reason-text',
    totals: '31337',
    revision: '55555',
  };
  const hostile = {
    // Familiar-LOOKING, so nothing about its shape warns a reader off it. Only the version differs.
    schema_version: 6,
    health: {
      available: true,
      port: Number(sentinels.port),
      revision: Number(sentinels.revision),
      observer_epoch: sentinels.epoch,
      observer_alive: true,
      observer_cycle_age_seconds: 2.0,
      observer_cycles: 42,
      history_reset_reason: sentinels.reason,
      persistence_state: sentinels.reason,
      persistence_reason_code: sentinels.reason,
      resources: 6,
    },
    totals: {processes: Number(sentinels.totals)},
    inventory: [sentinels.inventory],
    services: [serviceRow(sentinels.inventory, {
      label: sentinels.label,
      pid: Number(sentinels.pid),
      state: 'running',
      reason: sentinels.reason,
    }, {process_epoch: sentinels.epoch})],
    recovery_events: [{subsystem: sentinels.inventory, event: sentinels.reason, quarantined_path: sentinels.reason}],
  };

  // Every surface, with the disclosures and Advanced OPEN -- the regions that read the block most
  // deeply are exactly the ones a collapsed-by-default render would never have exercised.
  const rendered = [
    renderRoster(hostile, {expanded: [sentinels.inventory, 'local-services', 'statsd']}),
    renderAlerts(hostile),
    renderSnapshot(healthSnapshot(hostile.health)),
  ].join('\n');
  for (const [name, value] of Object.entries(sentinels)) {
    // `renderSnapshot` is called with the block directly, which is the ONE path that legitimately
    // renders it -- it is included above only to prove the sentinels are renderable at all.
    const roster = renderRoster(hostile, {expanded: [sentinels.inventory, 'local-services', 'statsd']});
    assert.equal(roster.includes(value), false, `${name}=${value} from an unreadable payload reached the roster HTML`);
    assert.equal(renderAlerts(hostile).includes(value), false, `${name}=${value} reached the alert slot`);
  }

  // The roster is exactly the three rows, and the third one says why.
  const ids = [...renderRoster(hostile).matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(ids, ['web', 'tmux-signal-watcher', 'local-services']);
  assert.match(rosterRow(renderRoster(hostile), 'local-services'), /data-subsystem-state="schema_unsupported"/);
  // The web row carries no port qualifier, which is the exact field that escaped the old guard.
  assert.doesNotMatch(rosterRow(renderRoster(hostile), 'web'), /js-debug-roster-qualifier/);
  assert.equal(renderAlerts(hostile), '', 'an unreadable payload cannot raise a health or recovery alert');

  // NEGATIVE CONTROL: at schema 5 the SAME payload renders those sentinels, so the sweep above is
  // measuring the guard and not a fixture that never carried the values.
  const readable = {...hostile, schema_version: 5};
  const supported = renderRoster(readable, {expanded: [sentinels.inventory]});
  for (const key of ['port', 'inventory', 'label', 'pid']) {
    assert.equal(supported.includes(sentinels[key]), true, `${key} must render when the schema IS supported`);
  }

  // And the read has ONE owner, so a future reader cannot reintroduce the escape. Only the version
  // guard and the owner itself may touch `payload.local_services` directly.
  const direct = source.split('\n')
    .filter(line => line.includes('payload.local_services') && !line.trim().startsWith('//'));
  assert.deepEqual(direct.map(line => line.trim()), [
    'return Number(payload.local_services?.schema_version) === 5;',
    "return payload.local_services && typeof payload.local_services === 'object' ? payload.local_services : {};",
  ], 'exactly two direct reads survive: the version guard, and the one owner it gates');
  assert.match(sourceFunction('debugSystemRenderableLocalServices', 'debugSystemRolesHtml'),
    /if \(!debugSystemLocalServicesSchemaSupported\(payload\)\) return \{\};/);
});

// -- NEGATIVE CONTROL: the legacy per-cell renderer is gone, not merely unreferenced ------------

test('there is no second Daemons renderer and no second service-state classifier', () => {
  // The retired surface, by name. Each of these was either a second renderer for a case the roster
  // already covers, a second classifier in JavaScript of a rule `yolomux_lib/app.py` owns, or the
  // lifecycle state map that only they read. A deletion that leaves a live reference behind is
  // worse than no deletion, so this asserts on the SOURCE, not on whether anything calls them.
  const retired = [
    'debugSystemServiceState',
    'DEBUG_SYSTEM_SERVICE_FRESH_MS',
    'debugSystemLocalServicesState',
    'debugSystemLocalServiceFields',
    'debugSystemServiceName',
    'debugSystemPrevText',
    'debugSystemStripPrevText',
    'debugSystemDictSummaryText',
    'debugSystemQueueText',
    'debugSystemCacheText',
    'debugSystemProductCountersText',
    'debugSystemProductRuntimeText',
    'debugSystemLocalServiceRecord',
    'debugSystemLocalServiceUpdateLifecycle',
    'debugSystemLocalServiceFieldValue',
    'debugSystemLocalServicesCardHtml',
    'debugSystemLocalServiceCellLayoutAttrs',
    'debugSystemLocalServicesTableHtml',
    'debugSystemLocalServiceCellMap',
    'ensureDebugSystemLocalServicesTable',
    'updateDebugSystemLocalServiceCell',
    'updateDebugSystemLocalServicesCard',
  ];
  for (const name of retired) {
    assert.doesNotMatch(source, new RegExp(name), `${name} is retired and must not survive anywhere in the panel source`);
  }
  // Its markup hooks and its CSS go with it, or the styles outlive the renderer that used them.
  for (const hook of ['data-js-debug-local-services', 'data-js-debug-service-cell', 'data-js-debug-service-row', 'data-js-debug-service-head', 'data-js-debug-service-state']) {
    assert.doesNotMatch(source, new RegExp(hook), `${hook} belonged to the retired table`);
  }
  for (const rule of ['js-debug-system-local-services', 'js-debug-system-service-cell--']) {
    assert.doesNotMatch(css, new RegExp(rule), `${rule} styled the retired table`);
  }
  // And Advanced no longer has a fallback view to fall back TO.
  const advanced = sourceFunction('debugSystemAdvancedHtml', 'debugSystemRegionHtml');
  // The retired BUILDERS by name. `debugSystemRenderableLocalServices` legitimately appears here --
  // it is the guarded reader every surface goes through -- so a bare /LocalServices/ match would
  // now flag the very thing that closed the escape it was written to catch.
  for (const builder of ['debugSystemLocalServicesCardHtml', 'debugSystemLocalServicesTableHtml', 'updateDebugSystemLocalServicesCard']) {
    assert.doesNotMatch(advanced, new RegExp(builder), 'Advanced must not build a second local-services view');
  }
  // One definition and exactly three readers: the guarded reader that empties the block, the
  // roster adapter that turns a false answer into the typed row, and the publisher that sends only
  // that safe row to the triangle reducer. Anything else reading the version directly would be a
  // second place the rule could drift.
  assert.equal(source.match(/debugSystemLocalServicesSchemaSupported\(/g).length, 4,
    'the schema guard has one definition, the guarded reader, roster adapter, and triangle publisher');
});

test('a running daemon whose transport failed is an issue with its typed reason, not "down"', () => {
  // THE BEHAVIOUR THE RETIRED CLASSIFIER PINNED, re-pinned on the surviving path. It used to be a
  // JavaScript re-derivation from `pid`/`healthy`/`transport_reason` -- a second copy of the rule
  // `yolomux_lib/app.py:system_status_service` owns. The backend classifies, and the roster's job
  // is to keep the classification intact: `issue` is a running process that is not serving, and it
  // must not read as a process that is down or idle.
  const fixture = localServices();
  fixture.services = [serviceRow('statsd', {
    state: 'issue',
    reason_code: 'transport_failed',
    reason: 'status transport refused',
    pid: 4242,
  })];
  const row = rosterRow(renderRoster(fixture), 'statsd');
  assert.match(row, /data-subsystem-state="issue"/);
  assert.match(row, /data-subsystem-tone="bad"/, 'a service that is not serving is actionable');
  // THE WORD, not just the attributes. Both assertions above are machine-readable state that a
  // reader never sees; the rule this row exists to obey is "status is never carried by colour
  // alone", and until this line nothing checked that the degraded state renders a word at all.
  assert.match(row, /<span data-subsystem-state-label>Issue<\/span>/, 'the degraded state renders its word beside the dot');
  // ...and the word is the catalog's, not a literal composed in the renderer.
  assert.equal(localeEn['debug.system.localServices.state.issue'], 'Issue');
  assert.match(row, /<span class="js-debug-roster-reason" data-subsystem-reason>status transport refused<\/span>/);
  // The two readings the old label existed to prevent, still prevented.
  assert.doesNotMatch(row, /data-subsystem-state="unavailable"/, 'a running process with a failed transport is not down');
  assert.doesNotMatch(row, /data-subsystem-tone="muted"/, 'and it is not idle');
});

// -- one roster, one adapter, one classifier ---------------------------------------------------

test('there is exactly one roster renderer, one row adapter and one status-tone owner', () => {
  assert.equal(source.match(/function debugSystemRosterHtml\(/g).length, 1);
  assert.equal(source.match(/function debugSystemRosterRows\(/g).length, 1);
  assert.equal(source.match(/function debugSystemStateTone\(/g).length, 1);
  assert.equal(source.match(/function debugSystemMetricText\(/g).length, 1);
  // The retired default card wall: the grid may only be built inside the collapsed Advanced
  // section, never by `debugSystemInnerHtml` itself.
  const inner = sourceFunction('debugSystemInnerHtml', 'debugSystemFocusKey');
  assert.doesNotMatch(inner, /js-debug-system-grid/, 'the default Daemons view must not build the card grid');
  assert.doesNotMatch(inner, /debugSystemCardHtml/, 'the default Daemons view must not build summary cards');
  const advanced = sourceFunction('debugSystemAdvancedHtml', 'debugSystemRegionHtml');
  assert.match(advanced, /js-debug-system-grid/, 'the card grid survives only inside Advanced diagnostics');
  // No second inventory, no second label map, no ordering array in the browser.
  const roster = slice(source, 'const DEBUG_SYSTEM_ROSTER_WEB_ID', '\n// The schema this panel renders.');
  assert.doesNotMatch(roster, /indexd/, 'the roster must not name a service id; the inventory owns them');
  assert.doesNotMatch(roster, /Quick Open|YO!stats'|Filesystem jobs/, 'the roster must not carry a label map');
});

test('the roster row order is the payload inventory, with the web process first and its child nested', () => {
  const fixture = localServices();
  fixture.inventory = ['indexd', 'statsd', 'jobd', 'statusd', 'watchd', 'approvald'];
  fixture.services = fixture.inventory.map(id => serviceRow(id));
  const html = renderRoster(fixture);
  const ids = [...html.matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(ids, ['web', 'tmux-signal-watcher', 'indexd', 'statsd', 'jobd', 'statusd', 'watchd', 'approvald']);
  assert.match(html, /data-subsystem-id="tmux-signal-watcher" data-subsystem-kind="child" data-subsystem-state="attached" data-subsystem-parent="web"/);
  // Row position never depends on health: a down service keeps its inventory slot.
  fixture.services[4] = serviceRow('watchd', {state: 'unavailable', reason: 'Status transport failed', pid: 0});
  const withOutage = renderRoster(fixture);
  const outageIds = [...withOutage.matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(outageIds, ids, 'rows must not re-sort by health');
});

test('a service in the inventory with no published row is a visible missing row, not a gap', () => {
  const fixture = localServices();
  fixture.inventory = ['statsd', 'jobd'];
  const html = renderRoster(fixture);
  const ids = [...html.matchAll(/data-subsystem-row data-subsystem-id="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(ids, ['web', 'tmux-signal-watcher', 'statsd', 'jobd']);
  assert.match(html, /data-subsystem-id="jobd" data-subsystem-kind="service" data-subsystem-state="unavailable"/);
  assert.match(html, /<span class="js-debug-roster-reason" data-subsystem-reason>Service status is missing<\/span>/);
});

// -- what Keiven asked to see ------------------------------------------------------------------

test('one row shows what is up, how long, how many starts, errors, requests and avg/max responses', () => {
  const html = renderRoster(localServices());
  assert.match(html, /data-subsystem-row data-subsystem-id="statsd" data-subsystem-kind="service" data-subsystem-state="running"/);
  assert.match(html, /data-subsystem-metric="uptime_seconds" data-metric-state="measured">1h 0m 0s</);
  assert.match(html, /data-subsystem-health-metric="process_start_count" data-metric-state="measured">4</);
  assert.match(html, /data-subsystem-health-metric="request_count" data-metric-state="measured" data-metric-coverage="full">1,204</);
  assert.match(html, /data-subsystem-health-metric="error_count" data-metric-state="measured" data-metric-coverage="full">7</);
  assert.match(html, /data-subsystem-health-metric="latency_average_ms" data-metric-state="measured" data-metric-coverage="full">12.5ms</);
  assert.match(html, /data-subsystem-health-metric="latency_max_ms" data-metric-state="measured" data-metric-coverage="full">340ms</);
  // Average and maximum share ONE column, separated in one cell -- still two typed envelopes.
  const statsdRow = rosterRow(html, 'statsd');
  assert.match(
    statsdRow,
    /data-subsystem-column="latency"><span class="js-debug-roster-celllabel">Latency avg \/ max<\/span><span data-subsystem-health-metric="latency_average_ms" data-metric-state="measured" data-metric-coverage="full">12\.5ms<\/span><span class="js-debug-roster-sep" aria-hidden="true"> \/ <\/span><span data-subsystem-health-metric="latency_max_ms" data-metric-state="measured" data-metric-coverage="full">340ms<\/span><\/td>/,
  );
  // The three process-metric cells keep their own attribute and their own three keys.
  const processMetrics = [...statsdRow.matchAll(/data-subsystem-metric="([a-z_]+)"/g)].map(match => match[1]);
  assert.deepEqual(processMetrics, ['uptime_seconds', 'rss_bytes', 'cpu_now_percent']);
  assert.doesNotMatch(statsdRow, /Observed state: ready/, 'the retained observation belongs in the disclosure, not the row');
});

test('the status cell carries a dot, a word and a machine state -- never colour alone', () => {
  const fixture = localServices();
  fixture.inventory = ['statsd', 'watchd', 'jobd'];
  fixture.services = [
    serviceRow('statsd'),
    serviceRow('watchd', {state: 'idle', reason: 'Starts on demand', pid: 0}),
    serviceRow('jobd', {state: 'unavailable', reason: 'Status transport failed', pid: 0}),
  ];
  const html = renderRoster(fixture);
  assert.match(rosterRow(html, 'statsd'), /data-subsystem-tone="good">[\s\S]*?<span data-subsystem-state-label>Ready<\/span>/);
  assert.match(rosterRow(html, 'watchd'), /data-subsystem-tone="muted"><span class="js-debug-roster-dot" aria-hidden="true">○<\/span><span data-subsystem-state-label>Idle<\/span>/);
  assert.match(rosterRow(html, 'jobd'), /data-subsystem-tone="bad">[\s\S]*?<span data-subsystem-state-label>Unavailable<\/span>/);
  // An idle service is gray and non-alerting; it is not painted like the down one.
  assert.doesNotMatch(rosterRow(html, 'watchd'), /data-subsystem-tone="bad"/);
  assert.match(rosterRow(html, 'watchd'), /<span class="js-debug-roster-reason" data-subsystem-reason>Starts on demand<\/span>/);
});

// -- NEGATIVE CONTROL: the web process's unobserved columns must never render as 0 ----------------

test('the web process renders its unobserved columns as their reason, never as 0', () => {
  const html = renderRoster(localServices());
  const webRow = rosterRow(html, 'web');
  for (const key of ['process_start_count', 'request_count', 'error_count', 'latency_average_ms', 'latency_max_ms']) {
    assert.match(
      webRow,
      new RegExp(`data-subsystem-health-metric="${key}" data-metric-state="unavailable"[^>]*title="the backend-health observer watches the local services from this web process, so it has never observed this web process itself" data-metric-reason="web_process_not_observed">—<`),
      key,
    );
    assert.doesNotMatch(webRow, new RegExp(`data-subsystem-health-metric="${key}"[^>]*>0<`), `${key} rendered as 0 is a fabricated measurement`);
  }
  // What it DOES measure is measured.
  assert.match(webRow, /data-subsystem-metric="uptime_seconds" data-metric-state="measured">2h 14m 0s</);
  assert.match(webRow, /data-subsystem-metric="rss_bytes" data-metric-state="measured">88\.0MB</);
  assert.match(webRow, /data-subsystem-metric="cpu_now_percent" data-metric-state="measured">3%</);
  assert.match(webRow, /YOLOmux web<\/span><span class="js-debug-roster-qualifier">:7999<\/span>/);
});

test('the tmux signal watcher is a child of the web row with no invented process metrics', () => {
  const html = renderRoster(localServices());
  const childRow = rosterRow(html, 'tmux-signal-watcher');
  for (const key of ['cpu_now_percent', 'rss_bytes', 'uptime_seconds']) {
    assert.match(childRow, new RegExp(`data-subsystem-metric="${key}" data-metric-state="unavailable" title="this is an in-process subsystem, not a separate process, so it has no independent process or traffic measurement" data-metric-reason="subsystem_not_observed">—<`), key);
  }
  assert.doesNotMatch(childRow, />0</, 'an unmeasured subsystem must not publish a zero');

  // Undemanded and never started is gray idle-by-design, not an outage.
  const undemanded = rosterRow(renderRoster(localServices(), {extra: {tmux_signal_watcher: {state: 'never-started', demanded: false, sessions: [], process_pid: 0}}}), 'tmux-signal-watcher');
  assert.match(undemanded, /data-subsystem-state="never-started"/);
  assert.match(undemanded, /data-subsystem-tone="muted"/);
  // Demanded and never started is the outage, in the same published state.
  const demanded = rosterRow(renderRoster(localServices(), {extra: {tmux_signal_watcher: {state: 'never-started', demanded: true, sessions: [], process_pid: 0}}}), 'tmux-signal-watcher');
  assert.match(demanded, /data-subsystem-state="never-started"/);
  assert.match(demanded, /data-subsystem-tone="bad"/);
});

// -- NEGATIVE CONTROL: details are BUILT lazily, not hidden with CSS -----------------------------

test('a collapsed row builds no transition list, no coverage note and no sampler table', () => {
  const collapsed = renderRoster(localServices());
  assert.match(collapsed, /aria-expanded="false" aria-controls="js-debug-roster-detail-statsd"/);
  assert.doesNotMatch(collapsed, /js-debug-system-health-transitions/, 'a closed row must not build its transition list');
  assert.doesNotMatch(collapsed, /data-subsystem-coverage-note/, 'a closed row must not build its coverage notes');
  assert.doesNotMatch(collapsed, /data-subsystem-health-row/, 'a closed row must not build its retained-health block');
  assert.doesNotMatch(collapsed, /data-js-debug-sampler-families/, 'a closed row must not build the sampler families table');
  // The container exists so `aria-controls` resolves, but it is empty and hidden.
  assert.match(collapsed, /data-subsystem-detail-built="false" hidden>\s*<td role="cell" colspan="9" id="js-debug-roster-detail-statsd"><\/td>/);
  assert.doesNotMatch(collapsed, /data-subsystem-detail-built="true"/, 'no row builds its detail by default');

  const open = renderStatsdOpen(localServices());
  assert.match(open, /aria-expanded="true" aria-controls="js-debug-roster-detail-statsd"/);
  assert.match(open, /<td role="cell" colspan="9" id="js-debug-roster-detail-statsd"><div class="js-debug-roster-detail">/);
  assert.match(open, /data-subsystem-health-row/);
  assert.match(open, /js-debug-system-health-transitions/);
  // One row open does not open another.
  assert.equal(open.match(/data-subsystem-detail-built="true"/g).length, 1);
  // Every disclosure target exists whether or not its content is built.
  const targets = [...open.matchAll(/id="(js-debug-roster-detail-[^"]+)"/g)].map(match => match[1]);
  const controls = [...open.matchAll(/aria-controls="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(targets, controls, 'every aria-controls resolves to a rendered element');
});

test('the disclosure names the row it controls and every row targets its own detail', () => {
  const fixture = localServices();
  fixture.inventory = ['statsd', 'jobd'];
  fixture.services = [serviceRow('statsd'), serviceRow('jobd')];
  const html = renderRoster(fixture, {expanded: ['jobd']});
  assert.match(html, /aria-controls="js-debug-roster-detail-web"/);
  assert.match(html, /aria-label="Show details for statsd · statsd"/);
  assert.match(html, /aria-label="Hide details for jobd · jobd"/);
  const controls = [...html.matchAll(/aria-controls="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(controls).size, controls.length, 'every disclosure target is unique');
});

// -- the rehomed diagnostics --------------------------------------------------------------------

test('the sampler lives under statsd, search and caches under indexd, events and chat under web', () => {
  const fixture = localServices();
  fixture.inventory = ['indexd', 'statsd'];
  fixture.services = [serviceRow('indexd'), serviceRow('statsd', {sampler_alive: true, sampler_families: {agent_tokens: {alive: true}}})];
  const extra = {
    search_index: {root_count: 4, build_count: 9, scanned_entries: 1200, ignored_entries: 3, cache_bytes: 4096},
    caches: {session_files: {files: 12, bytes: 2048}, activity: {files: 3, bytes: 512}},
    client_events: {channel_counts: {a: 2, b: 3}, published_events: 91, delivered_events: 88},
    chat: {subscribers: 2, store: {message_rows: 40, typing_leases: 1}},
  };
  const statsdOpen = renderRoster(fixture, {expanded: ['statsd'], extra});
  assert.match(statsdOpen, /YO!stats sampler/);
  assert.match(statsdOpen, /data-js-debug-sampler-families/);
  assert.doesNotMatch(statsdOpen, /Indexed roots/, 'the search index does not belong to statsd');

  const indexdOpen = renderRoster(fixture, {expanded: ['indexd'], extra});
  assert.match(indexdOpen, /Search &amp; caches/);
  assert.match(indexdOpen, /<dt>Indexed roots<\/dt><dd>4<\/dd>/);
  assert.doesNotMatch(indexdOpen, /YO!stats sampler/, 'the sampler does not belong to indexd');

  const webOpen = renderRoster(fixture, {expanded: ['web'], extra});
  assert.match(webOpen, /<dt>SSE subscribers<\/dt><dd>5<\/dd>/);
  assert.match(webOpen, /<dt>Chat messages<\/dt><dd>40<\/dd>/);
  assert.match(webOpen, /<dt>Version<\/dt><dd>0\.7\.1<\/dd>/);
  assert.match(webOpen, /<dt>PID<\/dt><dd>5150<\/dd>/);
  assert.match(webOpen, /never observed this web process itself/, 'the web row explains its four empty columns where the reader opens them');
});

test('the disclosure repeats the columns that can DROP, and only those', () => {
  const open = renderStatsdOpen(localServices());
  const dropped = slice(open, 'data-subsystem-dropped-metrics', '</dl>');
  // Exactly the `priority: secondary` columns -- the five the container query hides.
  for (const label of ['Memory', 'CPU', 'Starts', 'Requests', 'Errors']) {
    assert.match(dropped, new RegExp(`<dt>${label}</dt>`), label);
  }
  assert.match(dropped, /<dt>Starts<\/dt><dd>4<\/dd>/);
  // NEGATIVE CONTROL: Latency and Uptime are `primary` and survive every width, so a copy of them
  // underneath the row they are already on is pure duplication -- the top third of the ~40-line
  // disclosure Keivenc measured. If a column is ever demoted to secondary this must be updated,
  // which is the point: the two lists come from one `priority` field.
  for (const label of ['Latency avg / max', 'Uptime']) {
    assert.doesNotMatch(dropped, new RegExp(`<dt>${label.replace(/\//g, '\\/')}</dt>`),
      `${label} never drops, so restating it under the row is duplication`);
  }
  // The copies must be REACHABLE, not merely present: they are only displayed at the width where
  // the column itself is hidden, so the two rules quote the same threshold.
  const query = slice(css, '@container js-debug-system (max-width: 48rem)', '\n}\n');
  assert.match(query, /\.js-debug-roster-cell--secondary\s*\{\s*display: none;/);
  assert.match(query, /\.js-debug-roster-dropped\s*\{\s*display: block;/);
  assert.match(css, /\.js-debug-roster-dropped\s*\{\s*display: none;\s*\}/, 'hidden by default, revealed by the query');
});

test('the published fields the columns have no room for are rendered, not dropped', () => {
  const html = renderStatsdOpen(localServices({}, {errors_by_reason: {transport_refused: 4, timeout: 3, never_seen: 0}}));
  assert.match(html, /<p data-subsystem-health-detail>observer samples: 450 · process starts: 4 · demand starts: 3 · unexpected restarts: 0 · completed requests: 1,197 · peer pid 4242 \(epoch pid:4242:start:98\)\.<\/p>/);
  assert.match(html, /<p data-subsystem-errors-by-reason>Errors by reason: transport_refused 4, timeout 3\.<\/p>/);
  assert.doesNotMatch(html, /never_seen/, 'a reason with no error is not an error row');

  const unobserved = renderStatsdOpen(localServices({}, {
    observed: false,
    unavailable_reason_code: 'observer_unattached',
    pid: 0,
    metrics: {
      ...serviceHealth().metrics,
      observations: absent('observer_unattached', 'The health observer has not recorded this service yet'),
      completed_count: absent('counters_unreadable', 'The local-service RPC ledger returned no usable counter'),
    },
  }));
  assert.match(unobserved, /<p data-subsystem-health-detail>observer samples: The health observer has not recorded this service yet · process starts: 4 · demand starts: 3 · unexpected restarts: 0 · completed requests: The local-service RPC ledger returned no usable counter\.<\/p>/);
  assert.doesNotMatch(unobserved, /observer samples: 0/, 'an unobserved sample count rendered as 0 is a fabricated measurement');
  assert.doesNotMatch(unobserved, /peer pid/, 'an unverified pid is not published as a fact');
});

test('the health columns go through the SAME metric-envelope cell renderer as the process metrics', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  vm.runInContext(`
    result = {
      uptime: debugSystemMetricText({state: 'measured', value: 3600}, 'uptime_seconds'),
      restarts: debugSystemMetricText({state: 'measured', value: 3}, 'restart_count'),
      requests: debugSystemMetricText({state: 'measured', value: 1204}, 'request_count'),
      latency: debugSystemMetricText({state: 'measured', value: 12.5}, 'latency_average_ms'),
      absentRestarts: debugSystemMetricText({state: 'unavailable', value: null, reason: 'The health observer has not recorded this service yet'}, 'restart_count'),
      missingEnvelope: debugSystemMetricText(undefined, 'request_count'),
      structural: debugSystemMetricText(debugSystemAbsentMetric('web_process_not_observed'), 'restart_count'),
    };
  `, context);
  assert.deepEqual({...context.result}, {
    uptime: '1h 0m 0s',
    restarts: '3',
    requests: '1,204',
    latency: '12.5ms',
    absentRestarts: 'The health observer has not recorded this service yet',
    missingEnvelope: translate('common.notAvailable'),
    structural: 'the backend-health observer watches the local services from this web process, so it has never observed this web process itself',
  });
  assert.equal(source.match(/function debugSystemMetricText\(/g).length, 1, 'exactly one metric-envelope cell renderer');
});

// -- NEGATIVE CONTROL: an unobserved value must never render as 0 --------------------------------

test('an unobserved or untimed value renders its reason, never 0', () => {
  const fixture = localServices({}, {
    observed: false,
    unavailable_reason_code: 'resource_unobserved',
    state: '',
    transitions: [],
    transitions_total: 0,
    coverage: {retained_counters: 'unavailable', retained_counter_reasons: [], lifecycle: 'unavailable', lifecycle_reasons: [], counters: 'full', counter_reasons: [], counter_scope: 'web_process'},
    metrics: {
      restart_count: absent('resource_unobserved', 'The health observer has not recorded this service yet'),
      process_start_count: absent('resource_unobserved', 'The health observer has not recorded this service yet'),
      demand_start_count: absent('resource_unobserved', 'The health observer has not recorded this service yet'),
      unexpected_restart_count: absent('resource_unobserved', 'The health observer has not recorded this service yet'),
      observations: absent('resource_unobserved', 'The health observer has not recorded this service yet'),
      request_count: measured(0),
      error_count: measured(0),
      completed_count: measured(0),
      latency_average_ms: absent('no_completed_request', 'No completed request has been timed in this web process'),
      latency_max_ms: absent('no_completed_request', 'No completed request has been timed in this web process'),
    },
  });
  const html = renderStatsdOpen(fixture);
  assert.match(html, /data-subsystem-health-metric="process_start_count" data-metric-state="unavailable" title="The health observer has not recorded this service yet" data-metric-reason="resource_unobserved">—</);
  assert.match(html, /data-subsystem-health-metric="latency_average_ms" data-metric-state="unavailable" data-metric-coverage="full" title="No completed request has been timed in this web process" data-metric-reason="no_completed_request">—</);
  assert.match(html, /data-subsystem-health-metric="latency_max_ms" data-metric-state="unavailable" data-metric-coverage="full" title="No completed request has been timed in this web process" data-metric-reason="no_completed_request">—</);
  // The em dash is the CELL; the sentence behind it is still reachable. For a column that can drop
  // it is reachable twice -- in the cell's title and in the disclosure's copy.
  assert.match(html, /<dt>Starts<\/dt><dd>The health observer has not recorded this service yet<\/dd>/);
  // Latency never drops, so its reason is carried by the cell it sits in (asserted above) and is
  // NOT restated underneath. Losing the copy must not lose the sentence.
  assert.match(html, /title="No completed request has been timed in this web process"/);
  assert.doesNotMatch(html, /<dt>Latency avg \/ max<\/dt>/, 'a column that never drops is not restated in its own disclosure');
  assert.doesNotMatch(html, /data-subsystem-health-metric="process_start_count"[^>]*>0</, 'an unobserved start count rendered as 0 is a fabricated measurement');
  assert.doesNotMatch(html, /data-subsystem-health-metric="latency_average_ms"[^>]*>0/, 'an untimed average rendered as 0ms is a fabricated measurement');
  // A request count of 0 IS measured -- this process really issued no request -- so it stays 0.
  assert.match(html, /data-subsystem-health-metric="request_count" data-metric-state="measured" data-metric-coverage="full">0</);
  assert.match(html, /Not observed — the observer has never recorded this service\./);
  assert.match(html, /Retained totals are unavailable: the observer has never recorded this service\./);
});

// -- NEGATIVE CONTROL: a partial count must never look complete ----------------------------------

test('a partial retained counter aggregate keeps its reason in the row', () => {
  const fixture = localServices({}, {
    coverage: {
      retained_counters: 'partial',
      retained_counter_reasons: ['counters_not_observed', 'missed_final_sample'],
      lifecycle: 'full',
      lifecycle_reasons: [],
      counters: 'full',
      counter_reasons: [],
      counter_scope: 'web_process',
    },
  });
  const html = renderStatsdOpen(fixture);
  assert.match(html, /Retained counter totals \(observations\) are PARTIAL: counters_not_observed \(the observer never read a counter sample, so every retained total would be a structural zero\); missed_final_sample \(a restart happened before the final counter sample could be read\)\./);
  assert.match(html, /data-subsystem-health-metric="process_start_count" data-metric-state="measured">4<\/span>/);
  // The ledger counters are independently complete here, so they are NOT flagged.
  assert.match(html, /data-subsystem-health-metric="request_count" data-metric-state="measured" data-metric-coverage="full">1,204<\/span>/);
  assert.doesNotMatch(html, /data-subsystem-health-metric="request_count"[^>]*>1,204<sup class="js-debug-system-coverage-flag"/);
});

test('partial ledger counters flag the request, error and response columns, not the retained ones', () => {
  const html = renderStatsdOpen(localServices({}, {
    coverage: {
      retained_counters: 'full',
      retained_counter_reasons: [],
      lifecycle: 'full',
      lifecycle_reasons: [],
      counters: 'partial',
      counter_reasons: ['web_process_scope'],
      counter_scope: 'web_process',
    },
  }));
  for (const key of ['request_count', 'error_count', 'latency_average_ms', 'latency_max_ms']) {
    assert.match(html, new RegExp(`data-subsystem-health-metric="${key}" data-metric-state="measured" data-metric-coverage="partial">[^<]+<sup class="js-debug-system-coverage-flag"`), key);
  }
  assert.match(html, /data-subsystem-health-metric="process_start_count" data-metric-state="measured">4<\/span>/);
  assert.match(html, /Requests, errors and response times are PARTIAL: web_process_scope \(the retained history starts before this web process, so these counts cover less time than the process history beside them\)\./);
});

test('legacy lifecycle history reports a partial classification without inventing restarts', () => {
  const html = renderStatsdOpen(localServices({}, {
    coverage: {
      retained_counters: 'full',
      retained_counter_reasons: [],
      lifecycle: 'partial',
      lifecycle_reasons: ['legacy_lifecycle_unclassified'],
      counters: 'full',
      counter_reasons: [],
      counter_scope: 'web_process',
    },
  }));
  assert.match(html, /unexpected restarts: 0/);
  assert.match(html, /Restart classification is PARTIAL: legacy_lifecycle_unclassified \(older retained process replacements did not record whether the preceding absence was expected\)\./);
});

// -- the partial mark is a FOOTNOTE MARKER, and it keeps the word for a screen reader -------------
//
// The mark used to be the literal word beside the number (`3 partial`), which reads as a second
// value in a dense table. It is a footnote to the number, so it renders as one: `3*`. Dropping the
// word from the page is only safe while the word survives in the accessibility tree -- a bare `*`
// announces as nothing, or as "asterisk", which is not the fact.

function partialCounterCoverageHtml() {
  return renderStatsdOpen(localServices({}, {
    coverage: {
      retained_counters: 'full',
      retained_counter_reasons: [],
      lifecycle: 'full',
      lifecycle_reasons: [],
      counters: 'partial',
      counter_reasons: ['web_process_scope'],
      counter_scope: 'web_process',
    },
  }));
}

test('a partial count is marked with a real superscript footnote, not the word partial', () => {
  const html = partialCounterCoverageHtml();
  assert.match(
    html,
    /data-metric-coverage="partial">1,204<sup class="js-debug-system-coverage-flag" data-coverage-flag><span aria-hidden="true">\*<\/span>/,
    'the mark must be a real <sup> attached to the number it qualifies, so the cell reads 1,204*',
  );
  assert.doesNotMatch(
    html,
    /<span class="js-debug-system-coverage-flag" data-coverage-flag>partial<\/span>/,
    'the literal word beside the number is what the footnote marker replaces',
  );
  // The full sentence is a footnote, so it must still be reachable: the row keeps its coverage
  // explanation verbatim. A marker with nothing behind it is worse than the word it replaced.
  assert.match(html, /Requests, errors and response times are PARTIAL: web_process_scope/);
});

test('the partial footnote marker keeps an accessible name that says partial in words', () => {
  const html = partialCounterCoverageHtml();
  assert.match(
    html,
    /<sup class="js-debug-system-coverage-flag" data-coverage-flag><span aria-hidden="true">\*<\/span><span class="a11y-only">partial<\/span><\/sup>/,
    'the glyph is hidden from the accessibility tree and the word is carried in the panel a11y-only span',
  );
  // ...and that word is a locale value, not English composed in the renderer.
  assert.equal(localeEn['debug.system.roster.coverage.partial'], 'partial');
  assert.doesNotMatch(source, /data-coverage-flag[^>]*>partial/, 'the accessible word must not be inlined in the JS');
});

test('the summary strip total carries the same footnote marker and the same accessible word', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.services = [serviceRow('statsd')];
  context.fixture = payloadFor(fixture, {server: WEB_SERVER_NEVER_SAMPLED});
  vm.runInContext('result = debugSystemSummaryStripHtml(fixture);', context);
  const memoryFact = slice(String(context.result), 'data-js-debug-roster-summary-fact="memory"', '</span></sup>');
  assert.match(
    memoryFact,
    /Memory 48\.0MB<sup class="js-debug-system-coverage-flag" data-coverage-flag><span aria-hidden="true">\*<\/span><span class="a11y-only">partial/,
    'the strip and the roster cells share ONE marker owner, so the strip reads 48.0MB*',
  );
  // The sentence behind the marker is unchanged: it is the only place the reader learns WHY.
  assert.match(memoryFact, /title="this total covers the 1 of 2 rows that own a process and published a measurement; the rest are unmeasured, not zero"/);
});

test('counter_scope web_process is spelled out ONCE for the whole table', () => {
  const fixture = localServices();
  fixture.inventory = ['statsd', 'jobd', 'indexd'];
  fixture.services = [serviceRow('statsd'), serviceRow('jobd'), serviceRow('indexd')];
  const html = renderRoster(fixture, {expanded: ['statsd', 'jobd']});
  const sentence = /Requests, errors and response times count only what this web process issued \(scope: web_process\) — not everything this service has ever served\./g;
  // The denominator is a property of the aggregate, not of a row or a column. Every row publishes
  // the same scope, so it is stated once.
  assert.equal((html.match(sentence) || []).length, 1, 'the scope sentence appears exactly once in the table');
  assert.match(html, /<p class="js-debug-system-column-scope" data-subsystem-scope-caption>Requests, errors/);
  // NEGATIVE CONTROL 1: it must not come back as a per-column caption. Three headers each carrying
  // "this web process" is where the density went: being `white-space: nowrap`, it pinned Latency,
  // Requests and Errors to 121-127px apiece against numbers that need ~68px.
  assert.doesNotMatch(html, /<th[^>]*>[^<]*<span class="js-debug-system-column-scope">/, 'the scope is not repeated in the column headers');
  assert.match(html, /data-subsystem-column="request_count" data-subsystem-health-column="request_count">Requests<\/th>/);
  assert.match(html, /data-subsystem-column="process_start_count">Starts<\/th>/, 'the retained process-start count is not scoped to this web process');
  // NEGATIVE CONTROL 2: nor once per expanded row. Two rows are open above; one sentence total.
  assert.equal((html.match(/data-subsystem-coverage-note/g) || []).length, 0, 'a full-coverage row has no per-row note left to print');
  // The row-specific coverage notes are NOT what moved: a partial count is still explained in the
  // row that is partial, because that fact differs per row.
  const partial = renderStatsdOpen(localServices({}, {
    coverage: {retained_counters: 'full', retained_counter_reasons: [], lifecycle: 'full', lifecycle_reasons: [], counters: 'partial', counter_reasons: ['web_process_scope'], counter_scope: 'web_process'},
  }));
  assert.match(partial, /Requests, errors and response times are PARTIAL: web_process_scope/);
  assert.equal((partial.match(sentence) || []).length, 1, 'the scope sentence is still exactly once');
});

// -- NEGATIVE CONTROL: a QUIET monitor must never be reported as a DEAD one -----------------------
//
// What this pair used to be, and why it was worse than useless: one test handed the panel
// `{age_seconds: 3700}` and asserted it rendered "STOPPED UPDATING". That is a formatter test --
// it confirms the panel formats the number it was given, so it cannot fail when the MODEL is
// wrong, and it certified the false contract as correct. The observer publishes only on a
// service-state CHANGE, so an hour-old publication is the SIGNATURE of a healthy quiet system.
//
// The full cross-language proof (real observer cycles -> real store -> real projection) is
// `tests/test_backend_health_liveness.py`. These pin the panel's half of the model: given the two
// facts separately, which state does it choose?

test('a quiet healthy observer is not reported as a dead one', () => {
  // THE DEFECT, at the panel boundary: nothing has changed for an hour, and the observer probed
  // two seconds ago. That is a healthy silent system, and it must read as one.
  const quiet = renderSnapshot(healthSnapshot({age_seconds: 3700, observer_alive: true, observer_cycle_age_seconds: 2.0}));
  assert.match(quiet, /data-health-staleness="current"/);
  assert.match(quiet, /data-health-alerting="false"/);
  assert.doesNotMatch(quiet, /STOPPED UPDATING/, 'a quiet monitor reported as dead is the false banner this model exists to remove');
  // The headline names the fact and carries NO elapsed time; the numbers are in the rows below.
  assert.match(quiet, /a long-unchanged state below means a quiet system, not a stalled one\./);
  assert.match(quiet, /<dt>Observer last checked<\/dt>/);
  assert.match(quiet, /<dt>Last state change<\/dt>/);

  // The panel must hold NO threshold of its own: it cannot see the observer's cadence, and a
  // second copy of the deadline is exactly how the two drift apart again.
  assert.doesNotMatch(source, /DEBUG_SYSTEM_HEALTH_STALE_SECONDS\s*=/, 'the liveness deadline has one owner, in the observer');
});

test('the snapshot names when the retained history begins, from the observer epoch start', () => {
  // W13 surfaced the already-published `observer_epoch_started_at` as a reader-facing row: how far
  // back the retained history actually reaches. A present wall-clock renders an absolute label; an
  // absent one reads as not-available, never the epoch-zero date a bare `new Date(0)` would print.
  const present = renderSnapshot(healthSnapshot({observer_epoch_started_at: 1000}));
  const presentMatch = present.match(/<dt>History retained since<\/dt><dd[^>]*>([^<]*)<\/dd>/);
  assert.ok(presentMatch, 'the History retained since row is present');
  assert.notEqual(presentMatch[1], 'not available', 'a present epoch start renders a real timestamp, not the absent marker');
  assert.notEqual(presentMatch[1].trim(), '', 'the timestamp is non-empty');

  const absent = renderSnapshot(healthSnapshot({observer_epoch_started_at: 0}));
  assert.match(absent, /<dt>History retained since<\/dt><dd[^>]*>not available<\/dd>/);
});

test('an observer that stopped looking is still reported as stopped', () => {
  // Liveness must be able to say NO, or it is decoration. Raising a threshold or suppressing the
  // banner would have made the quiet case green AND this one green, which is why neither is a fix.
  const stopped = renderSnapshot(healthSnapshot({observer_alive: false, observer_cycle_age_seconds: 600}));
  assert.match(stopped, /data-health-staleness="stopped"/);
  assert.match(stopped, /data-health-alerting="true"/);
  assert.doesNotMatch(stopped, /data-health-staleness="current"/);
  // The SENTENCE is not printed here: a stopped observer is an alerting state, so the compact alert
  // slot above the roster is the one surface that says it. See the one-owner test below.
  assert.doesNotMatch(stopped, /Backend health STOPPED UPDATING/, 'the alert slot owns an alerting explanation');
  assert.equal(renderAlerts({observer_alive: false, observer_cycle_age_seconds: 600}).match(/Backend health STOPPED UPDATING — the observer stopped attempting probe cycles\./g).length, 1);

  // Attached but not yet probing is a third, different fact -- and an absent cycle age renders as
  // its reason, never as a fresh zero.
  const never = renderSnapshot(healthSnapshot({observer_alive: false, observer_cycle_age_seconds: null}));
  assert.match(never, /data-health-staleness="never-observed"/);
  assert.match(never, /has not completed a probe cycle yet/);
  assert.match(never, /<dt>Observer last checked<\/dt><dd title="the observer is attached but has not completed a probe cycle yet"[^>]*>—<\/dd>/);
  assert.doesNotMatch(never, /<dt>Observer last checked<\/dt><dd[^>]*>0/, 'a never-run observer rendered as 0s ago is a fabricated measurement');
});

test('only a stopped or unavailable observer raises the ONE compact alert above the roster', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const alerts = health => {
    context.fixture = payloadFor(localServices({health}));
    vm.runInContext('result = debugSystemAlertsHtml(fixture);', context);
    return String(context.result);
  };
  assert.equal(alerts({}), '', 'a healthy snapshot raises no alert at all');
  // THE DEFECT: an hour of silence from a live observer raised a red banner on every poll.
  assert.equal(alerts({age_seconds: 3700, observer_alive: true, observer_cycle_age_seconds: 2.0}), '',
    'a quiet healthy system must raise no alert at all');
  const stopped = alerts({observer_alive: false, observer_cycle_age_seconds: 600});
  assert.match(stopped, /role="alert"/);
  assert.match(stopped, /data-system-alert="backend-health">Backend health STOPPED UPDATING/);
  // ONE alert element, not one per row.
  assert.equal(stopped.match(/class="js-debug-system-alert"/g).length, 1);
  const reset = alerts({history_reset_reason: 'history_corrupt', persistence_state: 'degraded'});
  assert.match(reset, /data-system-alert="history-reset">history_corrupt — the retained history file was unreadable and was reset/);
  assert.match(reset, /data-system-alert="persistence">Retained history persistence is degraded/);
});

test('a recovered corrupt database is named in the same one compact alert', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.recovery_events = [{
    subsystem: 'statsd',
    event: 'unreadable_current_database',
    quarantined_artifact: 'stats-v7.sqlite3.corrupt-1785600000',
    quarantined_path: '/fixture/state/stats-v7.sqlite3.corrupt-1785600000',
    destination_path: '/fixture/state/stats-v7.sqlite3',
    reason: 'database disk image is malformed',
  }];
  context.fixture = payloadFor(fixture);
  vm.runInContext('result = debugSystemAlertsHtml(fixture);', context);
  const html = String(context.result);
  assert.match(html, /data-system-recovery-banner/);
  assert.match(html, /statsd recovered from unreadable_current_database/);
  assert.match(html, /stats-v7\.sqlite3\.corrupt-1785600000/);
  assert.match(html, /\/fixture\/state\/stats-v7\.sqlite3\./);
  assert.equal(html.match(/class="js-debug-system-alert"/g).length, 1, 'one alert slot, not one card per event');
});

test('unattached, not-yet-probing and probing-but-unchanged are three different, named facts', () => {
  // `observer_cycles`/`observer_alive` are NULL when nothing is attached, not `0`/`false` --
  // `yolomux_lib/local_service_projection.py` refuses to publish a count nobody took, and
  // `tests/test_app.py::test_an_app_with_no_observer_attached_says_so_instead_of_publishing_zeros`
  // pins that end. This is the other end: `Number(null)` is 0, so rendering the count through the
  // plain number formatter would have turned the honest absence straight back into a measured
  // zero on screen.
  const unattachedHealth = {available: false, reason_code: 'observer_unattached', revision: 0, age_seconds: null, written_at: 0, resources: 0, persistence_state: '', history_coverage: '', observer_alive: null, observer_cycles: null, observer_cycle_age_seconds: null, observer_liveness_reason_code: 'observer_unattached'};
  const unattached = renderSnapshot(healthSnapshot(unattachedHealth));
  assert.match(unattached, /<dt>Observer cycles<\/dt><dd title="the backend-health observer is not attached to this web process" data-value-reason="the backend-health observer is not attached to this web process">—<\/dd>/);
  assert.doesNotMatch(unattached, /<dt>Observer cycles<\/dt><dd>0<\/dd>/, 'an unattached observer rendered as 0 completed cycles is a fabricated measurement');
  assert.match(unattached, /data-health-available="false"/);
  assert.match(unattached, /data-health-staleness="unavailable"/);
  // Unavailable is an alerting state, so the sentence is printed by the alert slot and by nothing
  // else. The provenance block still names the state in its own attribute, above.
  assert.doesNotMatch(unattached, /Backend health is unavailable/, 'the alert slot owns an alerting explanation');
  assert.match(renderAlerts(unattachedHealth), /Backend health is unavailable — the backend-health observer is not attached to this web process\. Restarts, requests, errors and response times below are not measured; they are not zero\./);

  // Attached, but no probe cycle has finished.
  const neverObserved = renderSnapshot(healthSnapshot({age_seconds: null, written_at: 0, revision: 0, observer_alive: false, observer_cycle_age_seconds: null, observer_cycles: 0}));
  assert.match(neverObserved, /data-health-available="true"/);
  assert.match(neverObserved, /data-health-staleness="never-observed"/);
  assert.match(neverObserved, /has not completed a probe cycle yet/);

  // Probing normally, but no service has changed state yet. This is HEALTHY and is neither of the
  // two above: the observer is looking, and there is simply nothing to report.
  const quietSinceStart = renderSnapshot(healthSnapshot({age_seconds: null, written_at: 0, revision: 0}));
  assert.match(quietSinceStart, /data-health-staleness="current"/);
  assert.match(quietSinceStart, /data-health-alerting="false"/);
  assert.match(quietSinceStart, /<dt>Last state change<\/dt><dd>never<\/dd>/);
});

test('snapshot provenance shows the revision, a reset history and a degraded persistence state', () => {
  const html = renderSnapshot(healthSnapshot({
    revision: 812,
    history_coverage: 'reset',
    history_reset_reason: 'history_corrupt',
    persistence_state: 'degraded',
    persistence_reason_code: 'write_failed',
  }));
  assert.match(html, /<dt>Snapshot revision<\/dt><dd>#812<\/dd>/);
  assert.match(html, /<dt>Last state change<\/dt><dd>2 seconds ago \(2s old\)<\/dd>/);
  assert.match(html, /<dt>Observer last checked<\/dt><dd>2 seconds ago \(2s ago\)<\/dd>/);
  assert.match(html, /<dt>Observer cycles<\/dt><dd>42<\/dd>/);
  assert.match(html, /<dt>Observer epoch<\/dt><dd>ab12cd34<\/dd>/);
  assert.match(html, /<dt>Services retained<\/dt><dd>6<\/dd>/);
  // The reason CODE, which is a published field. The SENTENCE that explains it belongs to the
  // alert slot, and printing it here as well is the two-copies defect the one-owner test pins.
  assert.match(html, /<dt>History reset<\/dt><dd>history_corrupt<\/dd>/);
  assert.match(html, /<dt>Persistence<\/dt><dd>degraded — write_failed<\/dd>/);
  assert.doesNotMatch(html, /the retained history file was unreadable and was reset/, 'the alert slot owns the explanation');
  assert.doesNotMatch(html, /may not survive a restart/, 'the alert slot owns the explanation');
  assert.match(html, /data-health-alerting="true"/, 'a reset history and a degraded persistence state are not a quiet status line');

  const healthy = renderSnapshot(healthSnapshot());
  assert.match(healthy, /<dt>History coverage<\/dt><dd>full<\/dd>/);
  assert.doesNotMatch(healthy, /<dt>Persistence<\/dt>/, 'a healthy persistence state is not worth a row');
});

// -- NEGATIVE CONTROL: one explanation, one producer, one render site ----------------------------

test('every stale/error explanation has ONE producer and is rendered on exactly ONE surface', () => {
  // The three conditions at once, so all three explanations are live in the same render. Before
  // this owner existed the compact slot wrote its own history-reset and persistence sentences while
  // the provenance block wrote near-identical ones, and the stopped headline was printed by BOTH:
  // a reader with Advanced open read every one of these facts twice, in two wordings.
  const health = {
    observer_alive: false,
    observer_cycle_age_seconds: 600,
    history_reset_reason: 'history_corrupt',
    persistence_state: 'degraded',
    persistence_reason_code: 'write_failed',
  };
  const rendered = `${renderAlerts(health)}\n${renderSnapshot(healthSnapshot(health))}`;
  const explanations = [
    'Backend health STOPPED UPDATING — the observer stopped attempting probe cycles. Every retained number below describes its last cycle, not now.',
    'history_corrupt — the retained history file was unreadable and was reset; counts from before the reset are gone.',
    'Retained history persistence is degraded — write failed; the retained history may not survive a restart.',
  ];
  for (const sentence of explanations) {
    const occurrences = rendered.split(sentence).length - 1;
    assert.equal(occurrences, 1, `"${sentence.slice(0, 48)}..." is rendered ${occurrences} times, and one explanation gets one surface`);
  }

  // A NON-alerting state is the mirror image: the alert slot is silent, so the provenance block is
  // the one surface that carries the sentence. Neither state prints it twice, and neither drops it.
  const quiet = `${renderAlerts({})}\n${renderSnapshot(healthSnapshot())}`;
  const currentSentence = 'Backend health is current: the observer is still completing probe cycles, and a long-unchanged state below means a quiet system, not a stalled one.';
  assert.equal(quiet.split(currentSentence).length - 1, 1, 'a quiet system still gets its sentence, exactly once');

  // The producer side of the same rule: one owner function, one call site for the headline it
  // wraps, and no surface composing an explanation of its own.
  assert.equal(source.match(/function debugSystemHealthExplanations\(/g).length, 1, 'one explanation owner');
  assert.equal(source.match(/debugSystemHealthSnapshotHeadline\(/g).length, 2,
    'the headline has one definition and exactly one call site, inside the explanation owner');
  const slot = sourceFunction('debugSystemAlertsHtml', 'debugSystemAdvancedHtml');
  const provenance = sourceFunction('debugSystemHealthSnapshotHtml', 'debugSystemHealthCoverage');
  for (const [name, body] of [['the alert slot', slot], ['the provenance block', provenance]]) {
    assert.doesNotMatch(body, /Retained history persistence is/, `${name} must not compose the persistence explanation`);
    assert.doesNotMatch(body, /may not survive a restart/, `${name} must not compose the persistence explanation`);
    assert.doesNotMatch(body, /counts from before the reset are gone/, `${name} must not compose the reset explanation`);
    assert.doesNotMatch(body, /debugSystemHealthReasonText\(/, `${name} must not turn a reason code into prose itself`);
  }
});

// -- truncated transition history ----------------------------------------------------------------

test('transitions_truncated says older rows exist rather than implying the list is complete', () => {
  const transitions = Array.from({length: 16}, (_unused, index) => ({
    revision: 100 + index,
    wall_time: 1000 + index,
    previous_state: 'ready',
    new_state: index % 2 ? 'ready' : 'down',
    reason_code: index % 2 ? 'none' : 'service_absent',
    process_epoch: 'pid:4242:start:98',
    recovery_outcome: 'none',
  }));
  const truncated = renderStatsdOpen(localServices({}, {transitions, transitions_total: 42, transitions_truncated: true}));
  assert.match(truncated, /data-transitions-truncated="true"/);
  assert.match(truncated, /42 state changes recorded — showing the latest 16; older rows exist and are not shown here\./);
  assert.doesNotMatch(truncated, /42 state changes recorded — all of them are shown\./);
  // Newest first, so the row a reader acts on is the first one.
  assert.match(truncated, /<ol class="js-debug-system-health-transitions"><li>rev #115 · 15 minutes ago · ready → ready<\/li>/);
  assert.match(truncated, /<li>rev #114 · 15 minutes ago · ready → down \(service_absent\)<\/li>/);

  const complete = renderStatsdOpen(localServices({}, {transitions, transitions_total: 16, transitions_truncated: false}));
  assert.match(complete, /data-transitions-truncated="false"/);
  assert.match(complete, /16 state changes recorded — all of them are shown\./);
  assert.doesNotMatch(complete, /older rows exist/);
});

// `recovery_outcome` has exactly two renderers -- the observed-state sentence and the transition
// list -- and until this test EVERY fixture in this file pinned `'none'`, which is the one value
// both renderers drop. Two live branches, zero coverage: a `recovered` service could have rendered
// nothing, or the wrong word, and the whole suite would still have been green.
test('a recovered service prints its recovery outcome in the observed state AND on the transition it happened on', () => {
  const html = renderStatsdOpen(localServices({}, {
    state: 'ready',
    reason_code: 'none',
    recovery_outcome: 'recovered',
    transitions: [
      {revision: 3, wall_time: 1000, previous_state: 'ready', new_state: 'down', reason_code: 'service_absent', process_epoch: 'pid:4242:start:98', recovery_outcome: 'none'},
      {revision: 4, wall_time: 1500, previous_state: 'down', new_state: 'ready', reason_code: 'none', process_epoch: 'pid:4242:start:98', recovery_outcome: 'recovered'},
    ],
    transitions_total: 2,
  }));
  // Branch 1 -- `debugSystemHealthObservedText`. `reason_code` is `none`, so the outcome is the
  // ONLY detail the sentence carries, which is exactly the case a `'none'` fixture cannot reach.
  assert.match(html, /<p data-subsystem-health-state>Observed state: ready for [^<]*since revision #700 — recovery recovered\.<\/p>/);
  // Branch 2 -- `debugSystemHealthTransitionsHtml`, on the row where the recovery happened.
  assert.match(html, /<li>rev #4 · [^<]*· down → ready \(recovery recovered\)<\/li>/);
  // NEGATIVE CONTROL, same render: the earlier transition published `'none'`, and `none` is not a
  // recovery outcome to print. Without this the two assertions above pass for a renderer that
  // prints the field unconditionally, which is the defect in the other direction.
  assert.match(html, /<li>rev #3 · [^<]*· ready → down \(service_absent\)<\/li>/);
  assert.doesNotMatch(html, /recovery none/);
});

test('an observed service with no recorded change says so instead of rendering an empty list', () => {
  const html = renderStatsdOpen(localServices({}, {transitions: [], transitions_total: 0, transitions_truncated: false}));
  assert.match(html, /No state change has been recorded for this service yet\./);
  assert.doesNotMatch(html, /js-debug-system-health-transitions/);
});

// -- the rendered surface has styles ---------------------------------------------------------------

test('the provenance banner, the scope label and the partial flag are styled', () => {
  assert.match(css, /\.js-debug-system-health-snapshot\s*\{[\s\S]*?border: 1px solid var\(--line\);/);
  assert.match(css, /\.js-debug-system-health-snapshot--alert\s*\{[\s\S]*?border-color: var\(--warning-border-strong\);/);
  assert.match(css, /\.js-debug-system-column-scope\s*\{[\s\S]*?display: block;/);
  assert.match(css, /\.js-debug-system-coverage-flag\s*\{[\s\S]*?color: var\(--warning-border-strong\);/);
});

// One bounded CSS rule body: `[\s\S]*?` runs past the closing brace and happily matches a
// declaration from a LATER rule, which is how this shard claimed the roster header was sticky
// while production had deliberately made it static and the browser gate asserted exactly that.
// Two tests asserting opposite things about one element, one of them by accident.
function cssRule(selector) {
  const start = css.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `${selector} exists`);
  const end = css.indexOf('}', start);
  assert.notEqual(end, -1, `${selector} has a rule body`);
  return css.slice(start, end);
}

test('the roster is a responsive full-width table whose header is deliberately NOT sticky', () => {
  assert.match(cssRule('.js-debug-system-table.js-debug-roster-table'), /font-variant-numeric: tabular-nums;/);
  // The summary strip is the ONE pinned layer; see the browser gate
  // `test_the_summary_strip_is_the_one_pinned_layer_and_it_really_pins`.
  assert.doesNotMatch(cssRule('.js-debug-roster-table thead th'), /position: sticky/,
    'a second pinned layer cannot be aligned with the summary and must stay unpinned');
  assert.match(cssRule('.js-debug-system-region[data-js-debug-system-region="summary"]'), /position: sticky;/);
  assert.doesNotMatch(css, /--js-debug-roster-header-offset:/, 'the remembered offset is gone with the coupling');
  assert.match(css, /\.js-debug-roster-toggle:focus-visible\s*\{[\s\S]*?outline: 2px solid var\(--accent\);/);
  // The roster responds to the PANEL, not the window: a dockview pane is far narrower than the
  // viewport, so a media query would keep nine columns in a container that cannot hold them.
  assert.match(css, /\.js-debug-system-view\s*\{[\s\S]*?container-type: inline-size;[\s\S]*?container-name: js-debug-system;/);
  // The drop threshold is MEASURED against what nine columns need, not chosen. In a real browser
  // the nine-column table is 702px (43.9rem) of min-content; this query used to test 72rem, so the
  // roster dropped five numeric columns while ~18rem of panel sat unused -- which is why a 10%
  // window increase bought five columns at once.
  assert.match(css, /@container js-debug-system \(max-width: 48rem\)\s*\{\s*\.js-debug-roster-table \.js-debug-roster-cell--secondary\s*\{\s*display: none;/);
  assert.doesNotMatch(css, /@container js-debug-system \(max-width: 72rem\)/, 'the old threshold hid five columns a panel had room for');
  assert.match(css, /@container js-debug-system \(max-width: 36rem\)/);
  assert.doesNotMatch(css, /@media[^{]*\{\s*\.js-debug-roster/, 'the roster must not respond to the viewport');
  assert.match(css, /\.js-debug-system-state--warn\s*\{[\s\S]*?color: var\(--warning-border-strong\);/);
  // The alert slot is one compact bordered strip, not a restored card.
  assert.match(css, /\.js-debug-system-alert\s*\{[\s\S]*?border: 1px solid var\(--warning-border-strong\);/);
});

// -- the two-line service row, at the width the roster promised one --------------------------------

test('below 36rem the row stacks into two lines with a named metric on each, not a crushed table', () => {
  const phone = slice(css, '@container js-debug-system (max-width: 36rem)', '\n}\n');

  // MEASURED against the CSS this replaces. At a 390px container the four surviving columns were
  // 140px / 83px / 83px / 83px under `table-layout: fixed`, and the rendered result was NOT the
  // two-line row the section claimed: a quiet row was one line box, and `tmux-signal-watcher` was
  // four, because "Control client is attached" was folded into the 83px Status column as
  // `Control` / `client is` / `attached`. The table's own min-content in that regime measures
  // 355px, so 390px was never short of room -- it was spending it on four unreadable columns.
  assert.doesNotMatch(phone, /table-layout: fixed;/,
    'a four-column fixed table at 390px is the layout this breakpoint exists to replace');
  assert.match(phone, /\.js-debug-roster-row\s*\{[^}]*display: grid;/, 'the row lays out as lines, not columns');
  assert.match(phone, /\.js-debug-roster-table > thead\s*\{\s*display: none;/, 'a header cannot label a stacked row');

  // The header's labels move INTO the cells at exactly the width the header is hidden -- one label
  // string in one position, the same mutually exclusive contract `.js-debug-roster-dropped` has.
  assert.match(cssRule('.js-debug-roster-celllabel'), /display: none;/, 'hidden wherever the header is shown');
  assert.match(phone, /\.js-debug-roster-celllabel\s*\{\s*display: inline;/, 'shown wherever the header is not');
  const cell = sourceFunction('debugSystemRosterMetricCellHtml', 'debugSystemRosterHeaderHtml');
  const header = sourceFunction('debugSystemRosterHeaderHtml', 'debugSystemRosterRowHtml');
  for (const [name, body] of [['the cell', cell], ['the header', header]]) {
    assert.match(body, /esc\(t\(column\.labelKey\)\)/, `${name} must take its label from the one column definition`);
  }
  assert.equal((css.match(/\.js-debug-roster-celllabel\s*\{\s*display:/g) || []).length, 2,
    'the label has exactly two display states: shown at phone width, hidden everywhere else');

  // The three lines, each placed once.
  assert.match(phone, /th\.js-debug-roster-service\s*\{\s*grid-area: 1 \/ 1;/, 'line 1: who it is');
  assert.match(phone, /\.js-debug-roster-status\s*\{\s*grid-area: 1 \/ 2;/, 'line 1: what it is doing');
  assert.match(phone, /\.js-debug-roster-cell\s*\{\s*grid-row: 2;/, 'line 2: the metrics');
  assert.match(phone, /\.js-debug-roster-reason\s*\{\s*grid-row: 3;\s*grid-column: 1 \/ -1;/,
    'line 3: the explanation, at the full width of the row');
  // The status cell holds two things that belong on two different lines, so the cell box gets out
  // of the way. Without this the sentence wraps inside the status column all over again.
  assert.match(phone, /\.js-debug-roster-statuscell\s*\{\s*display: contents;/);
  // `display: block` on a stacked cell would beat the 48rem rule that drops the five secondary
  // columns -- measured, it put all seven metrics back on the row and produced a 307px row at 390px.
  assert.doesNotMatch(phone, /\.js-debug-roster-row > td\s*\{[^}]*display: block;/,
    'a blockified grid item must not re-show the columns the 48rem query hides');

  // An author `display` beats the UA `[hidden] { display: none }` at any specificity, and both of
  // these elements are grid items at this width, so a hidden one would hold a line open.
  assert.match(cssRule('.js-debug-roster-reason[hidden]'), /display: none;/);
  assert.match(phone, /\.js-debug-roster-detailrow\[hidden\]\s*\{\s*display: none;/);

  // Table semantics do not survive `display: block` in any engine, so the roster states them.
  const table = sourceFunction('debugSystemRosterHtml', 'debugSystemRosterMetricListHtml');
  const row = sourceFunction('debugSystemRosterRowHtml', 'debugSystemRosterHtml');
  assert.match(table, /<table role="table"/);
  assert.match(table, /<tbody role="rowgroup">/);
  assert.match(header, /<thead role="rowgroup"><tr role="row"><th role="columnheader"/);
  assert.match(row, /<tr role="row" class="\$\{rowClass\}"/);
  assert.match(row, /<th role="rowheader" scope="row" class="js-debug-roster-service">/);
  assert.match(row, /<td role="cell" class="js-debug-roster-statuscell"/);
  assert.match(cell, /<td role="cell" class="js-debug-roster-cell/);
});

// -- NEGATIVE CONTROL: one spelling for "nobody measured this" ------------------------------------

test('an unpublished sampler field is an em dash with a reason, never 0 and never "not available"', () => {
  const fixture = localServices();
  fixture.inventory = ['statsd'];
  // A sampler that has never run: every field below is ABSENT, not zero.
  fixture.services = [serviceRow('statsd', {sampler_alive: false})];
  const html = renderStatsdOpen(fixture);
  const sampler = slice(html, 'YO!stats sampler', '</dl>');

  // The defect Keivenc read off the screen: two spellings of one fact in one view.
  assert.doesNotMatch(sampler, /not available/, 'the panel spells an unmeasured value one way, and it is not "not available"');
  assert.match(sampler, /<dt>Late \/ missed deadlines<\/dt><dd title="the stats sampler has not published a cycle count yet" data-value-reason="[^"]+">— \/ —<\/dd>/);

  // A `|| 0` fallback rendered a sampler that has never run as a measured `0.0ms`. That is the
  // fabricated-zero defect this panel exists to prevent, in the panel's own diagnostics.
  assert.match(sampler, /<dt>Last cycle<\/dt><dd title="the stats sampler has not completed a timed cycle yet"[^>]*>—<\/dd>/);
  assert.match(sampler, /<dt>Last history latency<\/dt><dd title="no history query has been assembled since this process started"[^>]*>—<\/dd>/);
  assert.doesNotMatch(sampler, /0\.0ms/, 'an unrun sampler rendered as 0.0ms is a fabricated measurement');

  // Every em dash in the block carries its reason -- a bare dash beside an explained one is the
  // same two-spellings defect one step smaller.
  for (const [, dd] of sampler.matchAll(/<dd([^>]*)>—/g)) {
    assert.match(dd, /data-value-reason="[^"]+"/, `an em dash with no reason: <dd${dd}>`);
  }

  // A sampler that HAS run still reports its real numbers, including a genuine zero.
  const live = renderStatsdOpen((() => {
    const f = localServices();
    f.services = [serviceRow('statsd', {
      sampler_alive: true, sampler_last_cycle_seconds: 0.25,
      sampler_late_cycles: 0, sampler_missed_cycles: 2,
    })];
    return f;
  })());
  assert.match(live, /<dt>Late \/ missed deadlines<\/dt><dd>0 \/ 2<\/dd>/, 'a measured zero is still a zero');
  assert.match(live, /<dt>Last cycle<\/dt><dd>250ms<\/dd>/);
});

test('the transition list has room to paint its own numbers', () => {
  // `--space-9` is 9px. A list marker paints OUTSIDE the content box, so 9px left "1." hanging past
  // the list where `.js-debug-roster-wrap { overflow-x: hidden }` clipped it: the list rendered
  // ". rev #6" instead of "1. rev #6". Measured in Chrome, the fix moves the padding 9px -> 19.8px.
  const rule = slice(css, '.js-debug-system-health-notes,', '}');
  assert.match(rule, /padding-inline-start: 3ch;/, 'the marker needs room for a two-digit ordinal');
  assert.doesNotMatch(rule, /padding-inline-start: var\(--space-9\)/, '9px clips the ordinal against the roster wrap');
  // The clipping ancestor is still there, so the padding is what has to be right.
  assert.match(css, /\.js-debug-roster-wrap\s*\{\s*overflow-x: hidden;/);
});

test('the disclosure lays out inside the row rather than across the whole table', () => {
  // At 2400px the detail spanned ~1700px with labels hard left and values hard right, so the eye
  // could not connect a label to its value and the block read as a separate document.
  const detail = slice(css, '.js-debug-roster-detail .js-debug-system-kv {', '\n.js-debug-roster-detail .js-debug-system-kv dd');
  assert.match(detail, /grid-template-columns: repeat\(auto-fill, minmax\(20rem, 28rem\)\);/, 'pairs pack into measured columns');
  assert.match(css, /\.js-debug-roster-detail \.js-debug-system-kv dd\s*\{\s*text-align: start;/, 'the value sits beside its label, not at the far edge');
  // The base list still ends-aligns; only the disclosure overrides it, so this is a scoped override
  // and not a second kv renderer.
  assert.match(css, /\.js-debug-system-kv dd\s*\{[\s\S]*?text-align: end;/);
  assert.equal(source.match(/<dl class="js-debug-system-kv">/g).length, 1, 'exactly one label/value list renderer');
});


// -- THE LIVE REGION: a screen reader must not be told the time every five seconds ----------------
//
// `role="status"` and `role="alert"` are live regions: assistive technology re-announces their
// contents whenever the text changes. The panel polls every 5 seconds, and four surfaces carried
// a live role over text containing an elapsed time -- "Updated 3 seconds ago", "Backend health
// checked 2 seconds ago", "Last accepted 4s". So a Daemons panel left open re-announced itself
// every five seconds, forever, and drowned the announcements that actually matter.
//
// The contract this pins: across a poll where NOTHING meaningful changed, every live region's text
// is byte-identical. Only two live regions remain -- the compact alert slot, whose text is now
// free of elapsed time, and one visually-hidden announce region carrying the state counts.

// Every live region in the four Daemons surfaces that have one, with its text.
function liveRegions(html) {
  const regions = [];
  const pattern = /<([a-z]+)([^>]*(?:role="(?:status|alert)"|aria-live="[^"]*")[^>]*)>/g;
  let match;
  while ((match = pattern.exec(html)) !== null) {
    // The element's text content, tags stripped -- what would actually be announced.
    const rest = html.slice(match.index + match[0].length);
    const close = rest.indexOf(`</${match[1]}>`);
    regions.push({attrs: match[2].trim(), text: rest.slice(0, close < 0 ? 400 : close).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()});
  }
  return regions;
}

function daemonsSurfaces(payloadLocalServices, {nowSeconds, extra = {}} = {}) {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  context.fixture = payloadFor(payloadLocalServices, extra);
  context.nowSeconds = nowSeconds;
  vm.runInContext(`
    result = [
      debugSystemAnnounceHtml(fixture),
      debugSystemSummaryStripHtml(fixture),
      debugSystemAlertsHtml(fixture),
      debugSystemHealthSnapshotHtml(fixture.local_services.health || {}),
      debugSystemRosterHtml(fixture, {nowSeconds, expanded: new Set()}),
    ].join('\\n');
  `, context);
  return String(context.result);
}

// One quiet poll: five seconds pass, every age advances, nothing changes state.
function quietPoll(offset) {
  const fixture = localServices({health: {age_seconds: 3700 + offset, observer_cycle_age_seconds: 2.0 + offset}});
  return daemonsSurfaces(fixture, {nowSeconds: 1902 + offset});
}

test('a poll that changed nothing re-announces nothing', () => {
  const before = liveRegions(quietPoll(0));
  const after = liveRegions(quietPoll(5));
  assert.ok(before.length > 0, 'the panel has at least one live region');
  assert.deepEqual(after, before, 'a live region whose text moved on a quiet poll is re-announced');

  // NEGATIVE CONTROL on the specific words that used to chatter: no live region may carry an
  // elapsed time at all, because any such text changes on every single poll by construction.
  for (const region of before) {
    assert.doesNotMatch(region.text, /\b(seconds?|minutes?|hours?) ago\b/,
      `a live region carrying an elapsed time re-announces every poll: ${JSON.stringify(region)}`);
    assert.doesNotMatch(region.text, /\bUpdated\b/, `volatile timestamp text in a live region: ${JSON.stringify(region)}`);
  }
});

test('an ongoing outage does not re-announce itself every poll', () => {
  // The alert slot IS a live region, and it must stay one -- a stopped observer is worth
  // interrupting a reader for. But it is `role="alert"` (assertive), so if its text carried the
  // elapsed time it would interrupt every five seconds for as long as the outage lasted, which is
  // the worst version of this defect rather than a lesser one.
  const stopped = offset => daemonsSurfaces(
    localServices({health: {observer_alive: false, observer_cycle_age_seconds: 600 + offset}}),
    {nowSeconds: 1902 + offset},
  );
  const before = liveRegions(stopped(0));
  const after = liveRegions(stopped(5));
  const alerts = regions => regions.filter(region => /role="alert"/.test(region.attrs));
  assert.ok(alerts(before).length > 0, 'a stopped observer still raises the alert');
  assert.match(alerts(before)[0].text, /STOPPED UPDATING/);
  assert.deepEqual(after, before, 'an outage that has not changed must not re-announce on every poll');
  // A 5-second offset is below `relativeTimeFormat`'s granularity, so equality alone would still
  // pass with "10 minutes ago" in the banner -- it would merely re-announce every minute instead
  // of every five seconds. Scan the text itself, which is granularity-independent.
  for (const region of before) {
    assert.doesNotMatch(region.text, /\b(seconds?|minutes?|hours?) ago\b/,
      `an alert carrying an elapsed time re-interrupts the reader: ${JSON.stringify(region)}`);
  }
});

test('a live region still announces a change that matters', () => {
  const steady = localServices();
  steady.inventory = ['statsd', 'jobd'];
  steady.services = [serviceRow('statsd'), serviceRow('jobd')];
  const healthy = liveRegions(daemonsSurfaces(steady, {nowSeconds: 1902}));

  // One service goes down: that IS worth interrupting a reader for.
  const degraded = localServices();
  degraded.inventory = ['statsd', 'jobd'];
  degraded.services = [serviceRow('statsd'), serviceRow('jobd', {state: 'unavailable', reason: 'Status transport failed', pid: 0})];
  const announced = liveRegions(daemonsSurfaces(degraded, {nowSeconds: 1902}));

  const text = region => region.map(entry => entry.text).join(' | ');
  assert.notEqual(text(announced), text(healthy), 'a service changing state must reach the live region');
  // The counts are the meaningful signal, and they come from the ONE roster-summary owner.
  assert.match(text(announced), /1 ready · 0 idle · 2 issues|issues/);
});

test('the announce region is visually hidden and carries no timestamp', () => {
  const html = daemonsSurfaces(localServices(), {nowSeconds: 1902});
  assert.match(html, /<p class="a11y-only" role="status"[^>]*data-js-debug-system-announce>/);
  const announce = slice(html, 'data-js-debug-system-announce', '</p>');
  assert.doesNotMatch(announce, /ago|Updated/, 'the announce region states what changed, never when');
  // The visible summary strip keeps the timestamp and loses its live role.
  assert.match(html, /class="js-debug-roster-summary-facts"(?![^>]*role=)/);
});


// -- a floor is not an exact total, and a restart is not an empty history ------------------------

test('a lifetime count that is only a floor is rendered as a floor', () => {
  const exact = renderStatsdOpen(localServices({}, {transitions_total: 42, transitions_truncated: true, transitions_total_exact: true}));
  assert.match(exact, /42 state changes recorded — showing the latest 1;/);
  assert.doesNotMatch(exact, /at least/);

  // A history retained before the lifetime counter existed can only yield a lower bound.
  const floor = renderStatsdOpen(localServices({}, {transitions_total: 128, transitions_truncated: true, transitions_total_exact: false}));
  assert.match(floor, /at least 128 state changes recorded — showing the latest 1;/);
});

test('a restarted observer with retained history does not claim nothing was measured', () => {
  // THE ADDENDUM: retained history is real, this process has simply not looked yet.
  const restarted = renderSnapshot(healthSnapshot({
    observer_alive: false, observer_cycle_age_seconds: null, observer_cycles: 0,
    observer_liveness_reason_code: 'no_observer_cycle_recorded', resources: 6, revision: 812,
  }));
  assert.match(restarted, /data-health-staleness="never-observed"/);
  assert.match(restarted, /this process has not completed its first probe cycle yet/);
  assert.match(restarted, /The numbers below are the retained history from before the restart\./);
  assert.doesNotMatch(restarted, /nothing below has been measured/,
    'a restart with retained history must not read as an empty history');

  // A genuinely empty history still says so.
  const empty = renderSnapshot(healthSnapshot({
    observer_alive: false, observer_cycle_age_seconds: null, observer_cycles: 0,
    observer_liveness_reason_code: 'no_observer_cycle_recorded', resources: 0, revision: 0,
  }));
  assert.match(empty, /nothing has been retained, so nothing below has been measured\./);
});

test('a monitor that has failed every cycle since start is an alert, not the quiet not-yet line', () => {
  // THE REPRO, at the panel boundary. The backend gets this right: an observer that throws on its
  // very first cycle and every one after it reports `observer_cycles_failing` with a NULL cycle
  // age, because no cycle has ever completed. The panel keyed the generic `never-observed` branch
  // off that null age alone and never looked at the typed reason, so a monitor that was failing
  // continuously with an empty history rendered as "attached, hasn't looked yet" -- a quiet,
  // non-alerting line -- and the compact alert slot stayed silent. Nothing on the screen said the
  // backend-health observer was broken.
  const health = {
    observer_alive: false,
    observer_cycle_age_seconds: null,
    observer_cycles: 0,
    observer_liveness_reason_code: 'observer_cycles_failing',
    revision: 0,
    resources: 0,
    age_seconds: null,
    written_at: 0,
  };
  const snapshot = renderSnapshot(healthSnapshot(health));
  assert.match(snapshot, /data-health-staleness="stopped"/, 'a typed failure is not "has not looked yet"');
  assert.match(snapshot, /data-health-alerting="true"/);
  const alerts = renderAlerts(health);
  assert.match(alerts, /data-system-alert="backend-health">Backend health STOPPED UPDATING — the observer is still attempting cycles but they are failing/);
  // With no completed cycle there is no "last cycle" to describe, so the sentence must not claim
  // the numbers below came from one.
  assert.match(alerts, /Nothing below has been measured by this process\./);
  assert.doesNotMatch(alerts, /describes its last cycle/, 'a monitor that never completed a cycle has no last cycle');

  // The sibling shape, from the same defect class: a monitor that died on its FIRST cycle and
  // then stopped being scheduled publishes `observer_cycles_stalled` with the same null cycle age
  // and zero retained resources. It must reach the alert slot too -- this is the rendered end of
  // `test_a_first_cycle_failure_then_silence_is_stalled_not_a_benign_never_looked`.
  const stalledFromStart = {...health, observer_liveness_reason_code: 'observer_cycles_stalled'};
  assert.match(renderSnapshot(healthSnapshot(stalledFromStart)), /data-health-staleness="stopped"/);
  assert.match(renderAlerts(stalledFromStart), /data-system-alert="backend-health">Backend health STOPPED UPDATING — the observer stopped attempting probe cycles\. Nothing below has been measured by this process\./);

  // NEGATIVE CONTROL: attached-but-not-yet-probing still has a null cycle age, is still NOT an
  // alert, and must stay on the quiet line. The two differ only by the typed reason.
  const notYet = {observer_alive: false, observer_cycle_age_seconds: null, observer_cycles: 0, observer_liveness_reason_code: 'no_observer_cycle_recorded'};
  assert.match(renderSnapshot(healthSnapshot(notYet)), /data-health-staleness="never-observed"/);
  assert.equal(renderAlerts(notYet), '', 'an observer that has simply not started probing raises no alert');
});

test('a failing observer and a stopped one are different sentences', () => {
  // Both are alerting, so the sentence is read off the ONE surface that prints an alerting
  // explanation; the provenance block still carries the typed state in its own attribute.
  const failingHealth = {observer_alive: false, observer_cycle_age_seconds: 40, observer_liveness_reason_code: 'observer_cycles_failing'};
  const stalledHealth = {observer_alive: false, observer_cycle_age_seconds: 40, observer_liveness_reason_code: 'observer_cycles_stalled'};
  assert.match(renderSnapshot(healthSnapshot(failingHealth)), /data-health-staleness="stopped"/);
  assert.match(renderSnapshot(healthSnapshot(stalledHealth)), /data-health-staleness="stopped"/);
  const failing = renderAlerts(failingHealth);
  const stalled = renderAlerts(stalledHealth);
  // Both are an outage, but the typed reason distinguishes "throwing" from "not running at all",
  // which is the difference between a bug to fix and a thread to restart.
  assert.notEqual(
    /the observer is still attempting cycles but they are failing/.test(failing),
    /the observer is still attempting cycles but they are failing/.test(stalled),
  );
});


// -- ONE population for every number in the summary strip -----------------------------------------

test('the summary counts and its CPU/memory describe the SAME rows', () => {
  // The counts came from the roster rows (the web process included); CPU and memory came from
  // `local_services.totals`, which the backend computes over the six local services ONLY --
  // `LocalServicesCollector.collect` raises if anything outside LOCAL_SERVICE_INVENTORY appears,
  // so the web process cannot be in it. Two numbers, side by side, over different populations.
  // They now come from one function over one array, so they cannot drift apart.
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.inventory = ['statsd', 'jobd'];
  fixture.services = [serviceRow('statsd'), serviceRow('jobd')];
  // A deliberately WRONG backend total: if the strip still reads it, this test says so.
  fixture.totals = {processes: 2, cpu_percent: 999.0, rss_bytes: 1};
  context.fixture = payloadFor(fixture);
  vm.runInContext('result = {summary: debugSystemRosterSummary(debugSystemRosterRows(fixture)), html: debugSystemSummaryStripHtml(fixture)};', context);
  const {summary, html} = context.result;

  // Four rows are counted, because four rows render: the web process, its nested tmux child, and
  // two services.
  assert.equal(summary.ready + summary.idle + summary.issues, 4, summary);
  assert.equal(summary.population, 4, summary);
  // Three of the four own a process whose CPU and memory can be summed. The nested child runs
  // INSIDE the web process, so its resources are already inside the web row's figure.
  assert.equal(summary.resourcePopulation, 3, summary);
  // ...and the CPU/memory sums cover those same three rows: 2% + 2% from the services and 3% from
  // the web process, 48MB + 48MB + 88MB.
  assert.equal(Math.round(summary.cpuPercent * 10) / 10, 7.0, summary);
  assert.equal(summary.rssBytes, (48 + 48 + 88) * 1024 * 1024, summary);
  assert.equal(summary.cpuMeasured, 3, summary);

  assert.doesNotMatch(html, /999/, 'the strip must not read a total computed over a different population');
  // `debugSystemNumber(7, 1)` drops the trailing zero, as it does everywhere else in the panel.
  assert.match(html, /CPU 7%/);
});

test('the summary counts EVERY row the roster renders, nested child included', () => {
  // THE "of 7 while 8 rows render" DEFECT. `debugSystemRosterSummary` filtered `kind === 'child'`
  // out of its population while `debugSystemRosterHtml` drew it, so two owners answered "how many
  // rows are there" with two different numbers -- and a red child row was missing from the
  // `issues` count that is the whole reason a reader opens this view. One array now feeds both.
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.inventory = ['statsd', 'jobd'];
  fixture.services = [serviceRow('statsd'), serviceRow('jobd')];
  context.fixture = payloadFor(fixture);
  vm.runInContext(
    'result = {summary: debugSystemRosterSummary(debugSystemRosterRows(fixture)),'
    + ' html: debugSystemRosterHtml(fixture, {nowSeconds: 1902, expanded: new Set()})};',
    context,
  );
  const {summary, html} = context.result;
  const rendered = (String(html).match(/data-subsystem-row /g) || []).length;
  assert.equal(rendered, 4, 'web + tmux child + two services');
  assert.equal(summary.population, rendered, 'the counted population IS the rendered rows');
  assert.equal(summary.ready + summary.idle + summary.issues, rendered, summary);

  // The fact the old filter hid: a demanded watcher that never started is an outage, it renders
  // red, and it must be IN the issues count. Under the filter this read "0 issues" beside a red row.
  context.fixture = payloadFor(fixture, {
    tmux_signal_watcher: {state: 'never-started', demanded: true, sessions: ['debug'], process_pid: 0},
  });
  vm.runInContext(
    'result = {summary: debugSystemRosterSummary(debugSystemRosterRows(fixture)),'
    + ' html: debugSystemRosterHtml(fixture, {nowSeconds: 1902, expanded: new Set()})};',
    context,
  );
  const degraded = context.result;
  assert.match(rosterRow(String(degraded.html), 'tmux-signal-watcher'), /data-subsystem-tone="bad"/);
  assert.equal(degraded.summary.issues, 1, degraded.summary);
  // NEGATIVE CONTROL: an undemanded watcher in the same state is idle by design, so it is counted
  // as idle -- counting every child as an issue would be the same defect with the sign flipped.
  context.fixture = payloadFor(fixture, {
    tmux_signal_watcher: {state: 'never-started', demanded: false, sessions: [], process_pid: 0},
  });
  vm.runInContext('result = debugSystemRosterSummary(debugSystemRosterRows(fixture));', context);
  assert.equal(context.result.issues, 0, context.result);
  assert.equal(context.result.idle, 1, context.result);
});

test('the summary CPU number is labelled a population sum and carries no single-process budget', () => {
  // THE `CPU 172.5% / 30%` DEFECT. The numerator is a sum over every roster row; the denominator
  // was `SERVER_CPU_BUDGET_PERCENT`, the budget for the WEB PROCESS ALONE. Eight processes over
  // one process's budget renders a permanent breach nothing is actually breaching.
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.inventory = ['statsd', 'jobd'];
  fixture.services = [serviceRow('statsd'), serviceRow('jobd')];
  context.fixture = payloadFor(fixture, {
    cpu_budget: {status: 'ok', current_percent: 3.0, budget_percent: 30.0, sustained_budget_seconds: 300, sustained_seconds: 0, top_consumers: [], stale: false},
  });
  vm.runInContext('result = debugSystemSummaryStripHtml(fixture);', context);
  const html = String(context.result);
  const cpuFact = slice(html, 'data-js-debug-roster-summary-fact="cpu"', '</span>');
  assert.match(cpuFact, /CPU 7% across all rows/, 'the number says what it is a sum over');
  assert.doesNotMatch(cpuFact, /30/, 'a single-process budget is not the denominator of a population sum');
  assert.doesNotMatch(html, /7% \/ /, 'and the strip prints no ratio at all');
  // The budget is not deleted, it is left with the ONE figure it applies to: the CPU budget card,
  // which renders the web process's own current reading against it.
  vm.runInContext('result = debugSystemCpuBudgetCardHtml({status: "ok", current_percent: 3.0, budget_percent: 30.0, sustained_seconds: 0, sustained_budget_seconds: 300, top_consumers: []});', context);
  assert.match(String(context.result), />3% \/ 30%</, 'the budget still renders against the one process it budgets');
});

test('a summary number nobody measured is an em dash with a reason, never a zero', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.inventory = ['statsd'];
  fixture.services = [serviceRow('statsd', {metrics: {
    cpu_now_percent: absent('resource_unobserved', 'not observed'),
    rss_bytes: absent('resource_unobserved', 'not observed'),
    uptime_seconds: absent('resource_unobserved', 'not observed'),
  }})];
  // No web process figures either, so nothing in the population has been measured.
  context.fixture = payloadFor(fixture, {server: {version: '0.7.1', pid: 5150}});
  vm.runInContext('result = debugSystemSummaryStripHtml(fixture);', context);
  const html = String(context.result);
  assert.match(html, /CPU —/, 'a sum over zero measured values is an absence, not 0%');
  assert.match(html, /Memory —/);
  assert.doesNotMatch(html, /CPU 0\.0%/, 'a fabricated zero is the defect this panel exists to prevent');
});

// -- the web process's own three metrics ---------------------------------------------------------
//
// Found in a real browser against a live dev server: the row printed
// `Memory 0.0B`, `CPU 0%` and `System CPU 0%`, each carrying data-metric-state="measured", while
// /proc said VmRSS 166028 kB and ps said %CPU 10.5. The test above did not catch it because its
// fixture OMITTED the fields, so `Number(undefined)` was NaN and the absent path was taken. The
// live payload SENT them, as finite zeros -- which is the one input that reaches the defect.

test('an unpushed web sample is unavailable with its reason, never a measured zero', () => {
  const html = renderRoster(localServices(), {extra: {server: WEB_SERVER_NEVER_SAMPLED}});
  const webRow = rosterRow(html, 'web');

  for (const key of ['cpu_now_percent', 'rss_bytes']) {
    assert.match(
      webRow,
      new RegExp(`data-subsystem-metric="${key}" data-metric-state="unavailable"`),
      `${key} was never sampled, so it cannot be reported as measured`,
    );
  }
  assert.doesNotMatch(webRow, /data-metric-state="measured">0\.0B</, 'a 0-byte resident set is not a measurement');
  assert.doesNotMatch(webRow, /data-metric-state="measured">0%</, 'a 0% CPU reading nobody took is not a measurement');
  assert.match(webRow, /data-metric-reason="cpu_sample_not_pushed"/, 'the row must carry the machine-readable reason code');
  // Uptime is read locally, not sampled, so it stays measured beside the two that are not.
  assert.match(webRow, /data-subsystem-metric="uptime_seconds" data-metric-state="measured">2h 14m 0s</);
});

test('an unpushed web sample is excluded from the summary totals, not summed as zero', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.services = [serviceRow('statsd')];
  context.fixture = payloadFor(fixture, {server: WEB_SERVER_NEVER_SAMPLED});
  vm.runInContext('result = {summary: debugSystemRosterSummary(debugSystemRosterRows(fixture)), html: debugSystemSummaryStripHtml(fixture)};', context);
  const {summary, html} = context.result;

  // One service measured, the web process not. The strip must report the ONE it measured and must
  // not count the web row -- counting it as a measured 0 is what silently dropped ~163MB of real
  // RSS out of a total the strip presented as complete.
  assert.equal(summary.rssMeasured, 1, 'the unmeasured web row must not be counted as measured');
  assert.equal(summary.cpuMeasured, 1, summary);
  assert.equal(summary.rssBytes, 48 * 1024 * 1024, 'the total covers only the rows it actually measured');
  assert.match(html, /Memory 48\.0MB/);

  // ...and a total over SOME of the population says so, in the panel's ONE existing spelling for
  // partial coverage. Excluding the row without marking the exclusion is the same silent
  // undercount the fabricated zero caused, with a better reason behind it.
  const memoryFact = slice(html, 'data-js-debug-roster-summary-fact="memory"', '</span>');
  assert.match(memoryFact, /data-metric-coverage="partial"/, 'a partial total must carry the machine-readable coverage state');
  assert.match(memoryFact, /data-metric-measured-rows="1" data-metric-population-rows="2"/);
  assert.match(memoryFact, /<sup class="js-debug-system-coverage-flag" data-coverage-flag>/, 'the partiality must be RENDERED, not only in an attribute');
  assert.match(memoryFact, /data-value-reason="[^"]*1 of 2 rows[^"]*"/);
  const cpuFact = slice(html, 'data-js-debug-roster-summary-fact="cpu"', '</span>');
  assert.match(cpuFact, /data-metric-coverage="partial"/, 'the CPU sum is partial for the same reason and says so the same way');
});

test('a complete summary total carries no partial flag', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  const fixture = localServices();
  fixture.services = [serviceRow('statsd')];
  // NEGATIVE CONTROL: every row measured, so the `partial` mark must not appear. Without this a
  // permanently-flagged strip would pass the assertions above while telling the reader nothing.
  context.fixture = payloadFor(fixture, {server: WEB_SERVER});
  vm.runInContext('result = debugSystemSummaryStripHtml(fixture);', context);
  const html = String(context.result);
  assert.doesNotMatch(html, /data-metric-coverage="partial"/);
  assert.doesNotMatch(html, /data-coverage-flag/);
  assert.match(html, /Memory 136MB/, '48MB + 88MB, both measured');
});

test('the web disclosure prints System CPU as an absence with its reason, not 0.0%', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  context.fixture = payloadFor(localServices(), {server: WEB_SERVER_NEVER_SAMPLED});
  vm.runInContext('result = debugSystemWebProcessDetailHtml(fixture);', context);
  const html = String(context.result);
  assert.doesNotMatch(html, /System CPU<\/dt><dd[^>]*>0\.0%/, 'the fourth metric off the same unpushed sample is absent too');
  assert.match(html, /<dt>System CPU<\/dt><dd[^>]*data-value-reason="[^"]+"[^>]*>—</);
});

// -- the CPU budget block ------------------------------------------------------------------------

test('a stale CPU budget renders stale, and its unmeasured current reading is an em dash', () => {
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  // Exactly what the live dev server published: status stale, no sample age, and no current percentage behind it.
  context.fixture = {
    status: 'stale', current_percent: null, budget_percent: 30.0, sustained_budget_seconds: 300.0,
    sustained_seconds: 0.0, top_consumers: [], source: 'statsd_push', sample_age_seconds: null, stale: true,
  };
  vm.runInContext('result = debugSystemCpuBudgetCardHtml(fixture);', context);
  const html = String(context.result);
  assert.match(html, /data-js-debug-cpu-budget="stale"/, 'the published state must survive the render, not be coerced to ok');
  assert.doesNotMatch(html, /data-js-debug-cpu-budget="ok"/);
  // `debugSystemNumber(30, 1)` drops the trailing zero, as it does everywhere else in the panel.
  assert.doesNotMatch(html, />0% \/ 30%</, 'a current reading nobody sampled is not 0%');
  assert.match(html, />— \/ 30%</);
});

test('the summary strip reads nothing at all out of the CPU budget block', () => {
  // This test used to assert the strip MARKED its 30% denominator as stale. That mark existed only
  // because the strip printed a denominator it had no business printing; with the denominator gone
  // there is nothing stale to mark, and the block has exactly one renderer again. The assertion
  // that survives is the stronger one: a stale budget block cannot reach the strip in any form.
  const context = renderContext();
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}`, context);
  context.fixture = payloadFor(localServices(), {
    server: WEB_SERVER_NEVER_SAMPLED,
    cpu_budget: {status: 'stale', current_percent: null, budget_percent: 30.0, stale: true, sample_age_seconds: null},
  });
  vm.runInContext('result = debugSystemSummaryStripHtml(fixture);', context);
  const html = String(context.result);
  const cpuFact = slice(html, 'data-js-debug-roster-summary-fact="cpu"', '</span>');
  assert.doesNotMatch(cpuFact, /30/, 'the budget percentage must not reach the strip');
  assert.doesNotMatch(html, /data-value-stale/, 'and there is no budget freshness for the strip to report');
  // Source-level negative search: one reader of the block, and it is not this one.
  const strip = sourceFunction('debugSystemSummaryStripHtml', 'debugSystemAlertsHtml');
  assert.doesNotMatch(strip, /payload\.cpu_budget/, 'the summary strip must not read the CPU budget block');
  assert.doesNotMatch(strip, /budget_percent/);
  // ...and the strip's own CPU sum is still rendered, so this is not a test that passes by deleting
  // the fact it is about.
  assert.match(cpuFact, /CPU 2% across all rows/, 'statsd measured 2%; the web process published none');
});

// ---------------------------------------------------------------------------------------------
// THE SNAPSHOT SPLIT
//
// `/api/system-status` is served from a retained background snapshot, and the diagnostics a reader
// opens deliberately moved to `/api/system-status/advanced` on their own cadence. Two contracts the
// panel has to hold, and each one is a defect a green suite would otherwise hide:
//   * the Advanced body is fetched ONLY while its disclosure is open -- fetching it on every poll
//     puts the transcript scans and top-N folds back on the 5s path the split removed, and nothing
//     about a rendered card would show it;
//   * both routes can answer `ok:false` with a typed `snapshot` state and NO body. The aged body is
//     withheld, not relabelled, so there is nothing to fall back on: the panel says which state it
//     is in and re-asks in half a second instead of leaving the reader a blank poll interval.
// ---------------------------------------------------------------------------------------------

// The panel's own poll cadences and request state, sliced rather than re-declared: a test that
// wrote its own 5000/500/10000 would keep passing after the product's numbers changed.
const SNAPSHOT_CADENCE_SOURCE = slice(source, 'const jsDebugSystemPollMs =', '\nconst jsDebugLogsPollMs');
const SNAPSHOT_STATE_SOURCE = slice(source, 'const jsDebugSystemState = {', '\nconst jsDebugLogLevels');
const SNAPSHOT_REFUSAL_SOURCE = slice(source, 'const DEBUG_SYSTEM_SNAPSHOT_STATE_TEXT', '\nfunction debugSystemInnerHtml(');
const SNAPSHOT_POLL_SOURCE = slice(source, 'async function pollDebugSystemAdvanced(', '\nfunction refreshDebugLogsViews(');
// The whole render path from the region dispatcher down, so a refusal test that expects NO regions
// is running against a build that could actually have rendered them.
const ADVANCED_RENDER_SOURCE = [
  SNAPSHOT_CADENCE_SOURCE,
  slice(source, 'function debugSystemRolesHtml(', '\nfunction debugSystemSamplerFamilyEntries('),
  slice(source, 'function debugSystemAdvancedHtml(', '\n// The four regions of the Daemons view'),
  slice(source, 'function debugSystemRegionHtml(', '\n// The last HTML written into each region'),
].join('\n');

// One harness for the poll owner. Every request the panel makes lands in `requests`, and every
// re-arm of the ONE `debug-system` timer lands in `intervals`, so a test names the exact URL that
// should not have been asked for and the exact delay the next poll was scheduled at.
function pollHarness({advancedOpen = false, responses = {}} = {}) {
  const sandbox = {
    Array, Boolean, Date, Intl, JSON, Map, Math, Number, Object, Set, String, console,
    t: translate,
    i18nActiveLocale: 'en',
    result: null,
    requests: [],
    intervals: [],
    cleared: [],
    renders: 0,
    debugRuntimeState: {subTab: 'system'},
    jsDebugStatsPanelVisible: () => true,
    userMessageText: error => String(error?.message || error),
    refreshDebugSystemViews: () => { sandbox.renders += 1; },
    resetRuntimeInterval: (name, callback, delay) => { sandbox.intervals.push({name, delay}); },
    clearRuntimeInterval: name => { sandbox.cleared.push(name); },
    apiFetchJsonQuiet: async url => {
      sandbox.requests.push(url);
      const answer = responses[url];
      if (answer === undefined) throw new Error(`no fixture response for ${url}`);
      if (answer instanceof Error) throw answer;
      return answer;
    },
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(`${SNAPSHOT_CADENCE_SOURCE}\n${SNAPSHOT_STATE_SOURCE}\n${SNAPSHOT_REFUSAL_SOURCE}\n${SNAPSHOT_POLL_SOURCE}`, context);
  vm.runInContext(`jsDebugSystemRosterState.advancedOpen = ${advancedOpen === true};`, context);
  return context;
}

function pollOnce(context) {
  return vm.runInContext('pollDebugSystemStatus({force: true});', context);
}

const CORE_BODY = payloadFor(localServices());
const ADVANCED_BODY = {
  ok: true,
  generated_at: 1902,
  owner: {debug: {generation_count: 41}, control: {}},
  refresh: {
    local_refreshing: {},
    coalescing: {recent_pending_count: 0},
    counters: {coalesced_refresh_requests: 7},
    recurring_work: [],
    roles: {},
  },
  top_endpoints: [{surface: '/api/from-the-advanced-route', count: 12, compute_ms_max: 4, payload_bytes_total: 2048}],
  top_background_work: [{role: 'indexd', surface: 'index-scan', count: 3, compute_ms_max: 900, payload_bytes_total: 4096}],
  top_event_types: [],
  login_throttle: {},
  largest_active_transcripts: [],
  transcripts_cache: {},
};
// The exact shape `SnapshotRead.refusal_payload` publishes at HTTP 200 (see
// yolomux_lib/system_status_snapshot.py). `snapshot` describes the state; there is no body.
function snapshotRefusalBody(overrides = {}) {
  return {
    ok: false,
    schema: 'system-status-snapshot',
    snapshot: {
      state: 'stale',
      reason_code: 'system_status_snapshot_stale',
      reason: 'The newest system-status snapshot is 14.0s old, past the 12.0s freshness deadline.',
      age_seconds: 14.0,
      last_generated_at: 1888,
      last_sequence: 3,
      cadence_seconds: 5.0,
      freshness_deadline_seconds: 12.0,
      ...overrides,
    },
  };
}

function renderAdvanced({payload = CORE_BODY, advanced = {}} = {}) {
  const context = renderContext({
    jsDebugSystemRosterState: {expanded: new Set(), advancedOpen: true},
    jsDebugSystemAdvancedState: {payload: null, error: '', inFlight: false, updatedAt: 0},
  });
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}\n${ADVANCED_RENDER_SOURCE}`, context);
  context.fixture = payload;
  context.advanced = advanced;
  vm.runInContext('result = debugSystemAdvancedHtml(fixture, advanced);', context);
  return String(context.result);
}

async function testAsync(name, body) {
  try {
    await body();
    passed += 1;
  } catch (error) {
    failed += 1;
    console.error(`FAIL: ${name}`);
    console.error(error.stack || error);
  }
}

async function runSnapshotSplitSuite() {
  await testAsync('a poll with the Advanced disclosure CLOSED asks for the core body and nothing else', async () => {
    // The load-bearing negative. Nothing rendered would reveal a background fetch of the Advanced
    // body, so the request list is the only place this can be pinned: an unconditional advanced
    // read here would move the transcript scans and top-N folds back onto every 5s poll.
    const context = pollHarness({advancedOpen: false, responses: {'/api/system-status': CORE_BODY}});
    await pollOnce(context);
    assert.deepEqual([...context.requests], ['/api/system-status'], 'a closed Advanced disclosure must not fetch /api/system-status/advanced');
  });

  await testAsync('opening the Advanced disclosure is what fetches its route, and the producer cadence gates the re-read', async () => {
    const context = pollHarness({
      advancedOpen: true,
      responses: {'/api/system-status': CORE_BODY, '/api/system-status/advanced': ADVANCED_BODY},
    });
    await pollOnce(context);
    assert.deepEqual([...context.requests], ['/api/system-status', '/api/system-status/advanced']);
    // A second poll inside the producer's own cadence would re-read the same bytes.
    await pollOnce(context);
    assert.deepEqual([...context.requests], ['/api/system-status', '/api/system-status/advanced', '/api/system-status'],
      'the advanced body is re-read on the producer cadence, not on every core poll');
    // Past that cadence the panel asks again, so this is not a test that passes by never refetching.
    vm.runInContext('jsDebugSystemAdvancedState.updatedAt -= jsDebugSystemAdvancedPollMs + 1;', context);
    await pollOnce(context);
    assert.deepEqual([...context.requests].slice(-2), ['/api/system-status', '/api/system-status/advanced']);
    // One owner: the core poll re-arms the ONE `debug-system` timer, and no other interval exists.
    assert.deepEqual([...new Set(context.intervals.map(entry => entry.name))], ['debug-system']);
  });

  await testAsync('a typed refusal re-polls in half a second instead of leaving the panel blank for a whole interval', async () => {
    const context = pollHarness({advancedOpen: false, responses: {'/api/system-status': snapshotRefusalBody()}});
    await pollOnce(context);
    assert.deepEqual(context.intervals.at(-1), {name: 'debug-system', delay: 500},
      'a withheld snapshot is re-asked at the refusal cadence, not at the 5s poll cadence');
    // ...and a body that arrived goes straight back to the normal cadence.
    context.responses = null;
    vm.runInContext('jsDebugSystemState.payload = null;', context);
    const current = pollHarness({advancedOpen: false, responses: {'/api/system-status': CORE_BODY}});
    await pollOnce(current);
    assert.deepEqual(current.intervals.at(-1), {name: 'debug-system', delay: 5000});
  });

  await testAsync('the Advanced fetch survives a core refusal and reports its own refusal without caching it', async () => {
    const context = pollHarness({
      advancedOpen: true,
      responses: {'/api/system-status': CORE_BODY, '/api/system-status/advanced': snapshotRefusalBody({state: 'unavailable', reason_code: 'system_status_snapshot_unavailable', age_seconds: null})},
    });
    await pollOnce(context);
    await pollOnce(context);
    assert.deepEqual([...context.requests], [
      '/api/system-status', '/api/system-status/advanced', '/api/system-status', '/api/system-status/advanced',
    ], 'a refusal carries no body, so it must not start a cadence window that suppresses the next read');
    assert.equal(vm.runInContext('debugSystemPollDelayMs();', context), 500);
  });

  console.log(`system health panel suite: ${passed} passed, ${failed} failed`);
  if (failed) process.exitCode = 1;
}

test('the Advanced cards read the advanced body, never the core keys that moved off it', () => {
  // The core body no longer carries `top_endpoints`/`top_background_work`/`refresh`/`owner.debug` at
  // all. This fixture puts a decoy row in the core body: reading it would render the wrong surface,
  // and reading nothing would render an empty table where six cards used to be.
  const html = renderAdvanced({
    payload: payloadFor(localServices(), {
      owner: {status: 'owner', owner: true, current_owner: {port: 7999, pid: 5150}, search_index: {mode: 'live'}, debug: {generation_count: 999}},
      top_endpoints: [{surface: '/api/decoy-from-the-core-body', count: 1, compute_ms_max: 1, payload_bytes_total: 1}],
      refresh: {counters: {coalesced_refresh_requests: 999}},
    }),
    advanced: {payload: ADVANCED_BODY, refusal: null, error: '', inFlight: false},
  });
  assert.match(html, /\/api\/from-the-advanced-route/, 'Top API endpoints must be rendered from the advanced body');
  assert.doesNotMatch(html, /decoy-from-the-core-body/, 'the retired core keys are not a fallback source');
  assert.match(html, /index-scan/, 'Top background work comes from the advanced body too');
  assert.match(html, /Refresh coordination/);
  assert.match(html, /<dt>Generations<\/dt><dd[^>]*>41</, 'owner.debug now arrives on the advanced route');
  assert.doesNotMatch(html, />999</, 'no advanced fact may be read out of the core payload');
});

test('an Advanced body that has not arrived is reported, not drawn as empty cards', () => {
  const loading = renderAdvanced({advanced: {payload: null, refusal: null, error: '', inFlight: true}});
  assert.match(loading, /data-js-debug-system-advanced-state="loading"/);
  assert.doesNotMatch(loading, /Top API endpoints/, 'an empty card is an absence dressed up as a measurement');
  assert.match(loading, /Backend-health snapshot/, 'the cards the CORE body owns still render');
  const refused = renderAdvanced({
    advanced: {payload: null, error: '', inFlight: false, refusal: {state: 'stale', reasonCode: 'system_status_snapshot_stale', reason: 'The newest system-status snapshot is 14.0s old, past the 12.0s freshness deadline.', ageSeconds: 14.0}},
  });
  assert.match(refused, /data-js-debug-system-advanced-state="stale"/);
  assert.match(refused, /data-js-debug-system-advanced-reason-code="system_status_snapshot_stale"/);
  assert.match(refused, /past the 12.0s freshness deadline/);
});

test('a typed core refusal replaces the roster instead of rendering a roster nobody measured', () => {
  const context = renderContext({
    jsDebugSystemState: {payload: snapshotRefusalBody(), error: '', inFlight: false, updatedAt: 0},
    jsDebugSystemAdvancedState: {payload: null, error: '', inFlight: false, updatedAt: 0},
    jsDebugSystemRosterState: {expanded: new Set(), advancedOpen: false},
  });
  vm.runInContext(`${SHARED_SOURCE}\n${RENDER_SOURCE}\n${ADVANCED_RENDER_SOURCE}`, context);
  vm.runInContext('result = debugSystemInnerHtml();', context);
  const html = String(context.result);
  assert.match(html, /data-js-debug-system-snapshot-state="stale"/, 'the panel renders the state it was told');
  assert.match(html, /data-js-debug-system-snapshot-reason-code="system_status_snapshot_stale"/);
  assert.match(html, /past the 12.0s freshness deadline/, 'the backend reason is the reason the reader sees');
  assert.doesNotMatch(html, /data-js-debug-system-region/, 'there is no payload behind a refusal, so there is no roster to draw');
  // The owner-unattached refusal is the same shape with its own reason code -- one contract.
  context.jsDebugSystemState.payload = snapshotRefusalBody({state: 'unavailable', reason_code: 'system_status_snapshot_owner_unattached', reason: 'This process has no system-status snapshot owner.', age_seconds: null});
  vm.runInContext('result = debugSystemInnerHtml();', context);
  assert.match(String(context.result), /data-js-debug-system-snapshot-reason-code="system_status_snapshot_owner_unattached"/);
  assert.doesNotMatch(String(context.result), /Newest snapshot age/, 'an age nobody published is not printed');
});

// The suite summary and the exit code are printed by the async runner, AFTER the awaited tests
// settle. Printing them here would count an async failure as a pass -- the shard launcher treats a
// summary line as the whole result.
void runSnapshotSplitSuite();

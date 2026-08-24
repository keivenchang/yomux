// M10 of DOIT.p0.daemon-monitor: a down backend service must be visible in the ONE top-right health
// indicator, pushed, with no diagnostics panel open and no new polling.
//
// Every test here drives the same owner the product uses: `applyBackendHealthPayload()` for the
// `backend_health_changed` payload and `syncBackendHealthIndicator(host)` for the single DOM node.
// The node DOM stub does not build a real `.topbar` (the product resolves its host from it), so the
// tests hand the renderer their own `.topbar-right-tools` host. There is still exactly one renderer,
// one state object and one insertion point.
//
// The control is now PERMANENTLY mounted and a fixed-size icon shell (DOIT.topbar-health-layout-shift):
// a health transition repaints the same node instead of inserting or removing a variable-size pill, so
// healthy is a STATE of the node (`data-backend-health === ''`, disabled, inert), not its absence. The
// full localized sentence lives in the `.backend-health-indicator-text` role=status live region and on
// the control's title; the whole control is the System details route (no separate Details button).
const {
  assert,
  fs,
  TestElement,
  loadYolomux,
  flushAsyncWork,
  test,
  testAsync,
  runSuites,
} = require('./browser_helpers/layout_test_helper');

const EN = JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'));
const CORE_SOURCE = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const CSS_SOURCE = fs.readFileSync('static_src/css/yolomux/10_topbar_menus.css', 'utf8');
const BOOT_SOURCE = [
  'static_src/js/yolomux/99_terminal_boot.js',
  'static_src/js/yolomux/99_client_event_transport.js',
].map(path => fs.readFileSync(path, 'utf8')).join('\n');

function topbarHost() {
  const host = new TestElement('', 'div');
  host.className = 'topbar-right-tools';
  return host;
}

function healthPayload(overrides = {}) {
  return {epoch: 'observer-1', revision: 1, overall_state: 'ready', degraded_resources: [], ...overrides};
}

function resource(overrides = {}) {
  return {id: 'watchd', label: 'File watching', state: 'down', reason_code: 'service_absent', ...overrides};
}

// The accessible text of the live region: what a screen reader announces for role="status".
function accessibleText(indicator) {
  if (!indicator) return '';
  return indicator.children.map(child => String(child.textContent || '')).join(' ').trim();
}

function messageText(indicator) {
  return String(indicator?.querySelector('.backend-health-indicator-text')?.textContent || '');
}

// role=status now lives on the live-region owner (the text span), not the control button.
function liveRegionRole(indicator) {
  return String(indicator?.querySelector('.backend-health-indicator-text')?.getAttribute('role') || '');
}

function severityOf(indicator) {
  return String(indicator?.dataset?.backendHealth ?? '');
}

function renderHealth(api, payload) {
  const host = topbarHost();
  api.handleClientPushEventNowForTest('backend_health_changed', payload);
  return {host, indicator: api.syncBackendHealthIndicatorForTest(host)};
}

async function runBackendHealthIndicatorSuite() {
  test('backend_health_changed is on the browser EventSource contract so the indicator is actually subscribed', () => {
    const api = loadYolomux();
    assert.ok(
      api.clientServerPushEventTypesForTest().includes('backend_health_changed'),
      'an event name absent from clientServerPushEventTypes is never subscribed to, so the indicator would never fire in production',
    );
  });

  test('a down service is named in the top-right indicator from the pushed event alone', () => {
    const api = loadYolomux();
    const {host, indicator} = renderHealth(api, healthPayload({
      overall_state: 'down',
      degraded_resources: [resource()],
    }));

    assert.ok(indicator, 'a down service must render the indicator');
    assert.equal(host.children.length, 1, 'the indicator is the single node added to .topbar-right-tools');
    assert.equal(host.children[0], indicator, 'it is inserted into .topbar-right-tools, not somewhere else');
    assert.equal(liveRegionRole(indicator), 'status');
    assert.equal(indicator.dataset.backendHealth, 'down');
    assert.equal(indicator.disabled, false, 'a warning is an actionable control');
    assert.equal(EN['backendHealth.down.single'], '{label} is not running', 'pins the exact user-facing wording');
    assert.equal(messageText(indicator), 'File watching is not running');
  });

  test('web disconnected outranks a down service', () => {
    const api = loadYolomux();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      overall_state: 'down',
      degraded_resources: [resource()],
    }));
    for (let attempt = 0; attempt < 3; attempt += 1) api.noteBackendHealthFailureForTest();
    const indicator = api.syncBackendHealthIndicatorForTest(topbarHost());

    assert.equal(indicator.dataset.backendHealth, 'unresponsive', 'a browser that reaches nothing is not "one service is down"');
    assert.equal(messageText(indicator), `${EN['common.requestFailed']} · ${EN['tmuxWall.status.disconnectedRetrying']}`);
    assert.equal(api.backendHealthStateForTest().serviceSeverity, 'down', 'the masked service failure is retained, not discarded');
  });

  test('a service warning never masks a disconnected browser, and recovering the transport reveals it again', () => {
    const api = loadYolomux();
    for (let attempt = 0; attempt < 3; attempt += 1) api.noteBackendHealthFailureForTest();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      overall_state: 'degraded',
      degraded_resources: [resource({id: 'indexd', label: 'Quick Open index', state: 'degraded', reason_code: 'overload'})],
    }));
    const host = topbarHost();
    assert.equal(api.syncBackendHealthIndicatorForTest(host).dataset.backendHealth, 'unresponsive',
      'a degraded-service payload arriving while the browser is disconnected must not downgrade the warning');

    api.noteBackendHealthSuccessForTest();
    const indicator = api.syncBackendHealthIndicatorForTest(host);
    assert.equal(indicator.dataset.backendHealth, 'degraded', 'the service warning surfaces once the transport is back');
    assert.equal(host.children.length, 1, 'the same node is reused across severity changes');
  });

  test('a down service outranks a degraded one and is the resource that gets named', () => {
    const api = loadYolomux();
    const {indicator} = renderHealth(api, healthPayload({
      overall_state: 'degraded',
      degraded_resources: [
        resource({id: 'indexd', label: 'Quick Open index', state: 'degraded', reason_code: 'overload'}),
        resource({id: 'watchd', label: 'File watching', state: 'down', reason_code: 'service_absent'}),
      ],
    }));

    assert.equal(indicator.dataset.backendHealth, 'down');
    assert.equal(EN['backendHealth.down.multiple'], '{label} and {count} more are not running', 'pins the exact user-facing wording');
    assert.equal(messageText(indicator), 'File watching and 1 more are not running');
  });

  test('the named resources are bounded: one label plus a count, never a wall of service names', () => {
    const api = loadYolomux();
    const labels = ['File watching', 'Quick Open index', 'Background jobs', 'Session status', 'Usage stats', 'Auto-approval'];
    const ids = ['watchd', 'indexd', 'jobd', 'statusd', 'statsd', 'approvald'];
    const {indicator} = renderHealth(api, healthPayload({
      overall_state: 'down',
      degraded_resources: labels.map((label, index) => resource({id: ids[index], label})),
    }));

    assert.equal(messageText(indicator), 'File watching and 5 more are not running');
    for (const label of labels.slice(1)) {
      assert.ok(!messageText(indicator).includes(label), `only one service is named; ${label} must be folded into the count`);
    }
  });

  test('the rendered text uses the server label and never the raw service id', () => {
    const api = loadYolomux();
    const {indicator} = renderHealth(api, healthPayload({
      overall_state: 'down',
      degraded_resources: [resource({id: 'watchd', label: 'File watching', reason_code: 'service_absent'})],
    }));

    const text = accessibleText(indicator);
    assert.ok(text.includes('File watching'), 'the human label the server already provides is what the user reads');
    assert.ok(!text.includes('watchd'), 'a raw process id must never reach the rendered text');
    assert.ok(!text.includes('service_absent'), 'the machine reason code is not user-facing prose');
    assert.equal(indicator.dataset.backendHealthReason, 'service_absent', 'the reason code is retained for diagnostics');
  });

  test('an unlabelled resource falls back to a translated noun instead of leaking its id', () => {
    const api = loadYolomux();
    const {indicator} = renderHealth(api, healthPayload({
      overall_state: 'down',
      degraded_resources: [resource({id: 'watchd', label: ''})],
    }));

    assert.equal(EN['backendHealth.service'], 'A backend service', 'pins the exact user-facing wording');
    assert.equal(messageText(indicator), 'A backend service is not running');
    assert.ok(!accessibleText(indicator).includes('watchd'), 'the id is not a label substitute');
  });

  test('one accepted healthy revision immediately paints the same indicator green', () => {
    const api = loadYolomux();
    const host = topbarHost();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      revision: 1,
      overall_state: 'down',
      degraded_resources: [resource()],
    }));
    assert.equal(api.syncBackendHealthIndicatorForTest(host).dataset.backendHealth, 'down');

    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({revision: 2, overall_state: 'ready'}));
    const recovered = api.syncBackendHealthIndicatorForTest(host);
    assert.equal(severityOf(recovered), 'ready');
    assert.equal(messageText(recovered), 'All backend services are ready');
    assert.equal(recovered.disabled, false, 'green remains a details action');
    assert.equal(host.children.length, 1, 'the node persists as an inert fixed slot, it is not removed');
  });

  test('a replayed or older revision cannot walk the state backwards', () => {
    const api = loadYolomux();
    const host = topbarHost();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      revision: 7,
      overall_state: 'down',
      degraded_resources: [resource()],
    }));
    assert.equal(api.applyBackendHealthPayloadForTest(healthPayload({revision: 7, overall_state: 'ready'})), false,
      'the same revision is a replay');
    assert.equal(api.applyBackendHealthPayloadForTest(healthPayload({revision: 6, overall_state: 'ready'})), false,
      'an older revision is stale');
    assert.equal(api.syncBackendHealthIndicatorForTest(host).dataset.backendHealth, 'down');
  });

  test('a new observer epoch can immediately publish its accepted healthy state', () => {
    const api = loadYolomux();
    const host = topbarHost();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      revision: 4,
      overall_state: 'down',
      degraded_resources: [resource()],
    }));
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({revision: 5, overall_state: 'ready'}));
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({epoch: 'observer-2', revision: 1, overall_state: 'ready'}));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'ready');
  });

  test('starting and ready both render the non-alerting green state', () => {
    const api = loadYolomux();
    for (const state of ['starting', 'ready']) {
      const {indicator, host} = renderHealth(api, healthPayload({
        revision: state === 'starting' ? 1 : 2,
        overall_state: state,
        degraded_resources: [resource({state})],
      }));
      assert.equal(severityOf(indicator), 'ready', `${state} must agree with green roster rows`);
      assert.equal(indicator.disabled, false, `${state} keeps the System details action available`);
      assert.equal(host.children.length, 1, 'the fixed slot is permanently mounted, warning or not');
    }
  });

  test('the warning is carried by words, not by colour', () => {
    const api = loadYolomux();
    const down = renderHealth(api, healthPayload({
      revision: 1,
      overall_state: 'down',
      degraded_resources: [resource()],
    })).indicator;
    const downText = accessibleText(down);
    const api2 = loadYolomux();
    const degraded = renderHealth(api2, healthPayload({
      revision: 1,
      overall_state: 'degraded',
      degraded_resources: [resource({state: 'degraded', reason_code: 'overload'})],
    })).indicator;
    const degradedText = accessibleText(degraded);

    assert.equal(EN['backendHealth.degraded.single'], '{label} is degraded', 'pins the exact user-facing wording');
    assert.ok(downText.includes('is not running'), 'the down state is stated in text');
    assert.ok(degradedText.includes('is degraded'), 'the degraded state is stated in text');
    assert.notEqual(downText, degradedText, 'severity must be distinguishable with every colour token removed');
    assert.equal(liveRegionRole(down), 'status', 'both states stay in the same live region');
    assert.equal(liveRegionRole(degraded), 'status');
  });

  test('the triangle severity is reduced from the same green yellow and red roster rows', () => {
    const api = loadYolomux();
    const serviceIds = ['indexd', 'statsd', 'jobd', 'statusd', 'watchd', 'approvald'];
    const measured = value => ({state: 'measured', value, reason_code: '', reason: ''});
    const payload = (states, revision) => ({
      ok: true,
      generated_at: 1902,
      server: {
        version: '0.7.12', pid: 5150, started_at: 1000,
        uptime_seconds: measured(8040), cpu_percent: measured(3),
        system_cpu_percent: measured(11), rss_bytes: measured(88),
      },
      owner: {}, search_index: {}, caches: {}, client_events: {}, chat: {}, cpu_budget: {},
      tmux_signal_watcher: {state: 'attached', demanded: true, sessions: ['debug'], process_pid: 9001},
      local_services: {
        schema_version: 5,
        inventory: serviceIds,
        services: serviceIds.map(id => ({
          id, service: id, label: id, pid: 4242,
          state: states[id] || 'running', reason_code: '', reason: '',
          metrics: {
            cpu_now_percent: measured(2), rss_bytes: measured(48), uptime_seconds: measured(3600),
          },
          health: {},
        })),
        health: {available: true, observer_epoch: 'observer-1', revision, port: 7220},
      },
    });
    const host = topbarHost();

    api.publishDebugSystemRosterHealthForTest(payload({}, 10));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'ready');
    api.publishDebugSystemRosterHealthForTest(payload({statsd: 'unknown'}, 11));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'degraded');
    api.publishDebugSystemRosterHealthForTest(payload({statsd: 'unknown', watchd: 'unavailable'}, 12));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'down');
    api.publishDebugSystemRosterHealthForTest(payload({}, 13));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'ready');
  });

  test('a delayed roster response from a retired observer epoch cannot clear a newer failure', () => {
    const api = loadYolomux();
    const host = topbarHost();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      epoch: 'old', revision: 20, overall_state: 'ready',
    }));
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      epoch: 'new', revision: 1, overall_state: 'down', degraded_resources: [resource()],
    }));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'down');

    assert.equal(api.applyBackendHealthRosterStateForTest({
      epoch: 'old', revision: 99, rows: [], observedIds: ['watchd'],
    }), false);
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'down');
  });

  test('the typed unsupported-schema yellow row also makes the triangle yellow', () => {
    const api = loadYolomux();
    const host = topbarHost();
    api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
      epoch: 'observer-1', revision: 1, overall_state: 'ready',
    }));
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'ready');

    assert.equal(api.publishDebugSystemRosterHealthForTest({
      ok: true,
      local_services: {schema_version: 999, inventory: [{hostile: true}], services: [{state: 'running'}]},
    }), true);
    assert.equal(severityOf(api.syncBackendHealthIndicatorForTest(host)), 'degraded');
  });

  test('the whole control is a details action that reuses the existing System view route', () => {
    const api = loadYolomux();
    const {indicator} = renderHealth(api, healthPayload({
      overall_state: 'down',
      degraded_resources: [resource()],
    }));

    assert.equal(indicator.localName, 'button', 'native button semantics give Enter/Space and focus for free');
    assert.equal(indicator.type, 'button');
    // The accessible NAME describes the action; the STATE sentence is announced by the live region
    // and shown on the tooltip, so the fixed slot never has to widen to a variable-length pill.
    assert.equal(indicator.getAttribute('aria-label'), EN['backendHealth.detailsAria']);
    assert.equal(EN['backendHealth.detailsAria'], 'Show backend service details', 'pins the exact user-facing wording');
    assert.equal(indicator.getAttribute('title'), 'File watching is not running', 'the tooltip carries the full sentence');
    // Reuse proof: the same two calls the System sub-tab button itself makes, not a second route in.
    assert.match(
      CORE_SOURCE,
      /async function openBackendHealthDetails\(\) \{\s*await selectSession\(debugPaneItemId, \{userInitiated: true\}\);\s*setDebugSubTab\('system'\);/,
      'the details action reuses selectSession(debugPaneItemId) + setDebugSubTab(system)',
    );
  });

  await testAsync('the details action selects the System view', async () => {
    const api = loadYolomux();
    api.setFetchForTest(async () => ({ok: true, status: 200, clone() { return this; }, json: async () => ({})}));
    await api.openBackendHealthDetailsForTest();
    assert.equal(api.debugSubTabForTest(), 'system');
  });

  await testAsync('no polling was added: health arrives on the pushed event and issues no request', async () => {
    const api = loadYolomux();
    const requests = [];
    api.setFetchForTest(async (input, options = {}) => {
      requests.push({url: String(input), method: String(options.method || 'GET')});
      return {ok: true, status: 200, clone() { return this; }, json: async () => ({})};
    });

    for (let revision = 1; revision <= 5; revision += 1) {
      api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
        revision,
        overall_state: revision % 2 ? 'down' : 'ready',
        degraded_resources: revision % 2 ? [resource()] : [],
      }));
      api.syncBackendHealthIndicatorForTest(topbarHost());
    }
    await flushAsyncWork();

    assert.deepStrictEqual(requests, [], 'the indicator must not fetch anything: health is pushed');
  });

  test('the indicator owns no request or timer of its own', () => {
    const region = CORE_SOURCE.slice(
      CORE_SOURCE.indexOf('function backendHealthStateSeverity'),
      CORE_SOURCE.indexOf('function noteBackendHealthFailure'),
    );
    assert.ok(region.includes('function syncBackendHealthIndicator'), 'the backend-health region is the one being scanned');
    for (const forbidden of ['apiFetch(', 'fetch(', 'setInterval(', 'setTimeout(']) {
      assert.ok(!region.includes(forbidden), `the indicator must not ${forbidden.slice(0, -1)}: health is pushed, never polled`);
    }
    const branch = BOOT_SOURCE.slice(BOOT_SOURCE.indexOf("if (type === 'backend_health_changed')"));
    const body = branch.slice(0, branch.indexOf('\n  }\n'));
    assert.ok(body.includes('applyBackendHealthPayload(payload)'), 'the branch applies the pushed payload');
    assert.ok(!/refresh|apiFetch|fetch\(/.test(body), 'the branch must not trigger a refetch of /api/system-status');
    const systemStatusCallSites = [...BOOT_SOURCE.matchAll(/apiFetch[A-Za-z]*\('\/api\/system-status'/g)].length
      + [...CORE_SOURCE.matchAll(/apiFetch[A-Za-z]*\('\/api\/system-status'/g)].length;
    assert.equal(systemStatusCallSites, 0, '/api/system-status stays owned by the demand-driven System panel poll');
  });

  test('there is one indicator, one state object and one insertion point', () => {
    const api = loadYolomux();
    const host = topbarHost();
    for (let revision = 1; revision <= 4; revision += 1) {
      api.handleClientPushEventNowForTest('backend_health_changed', healthPayload({
        revision,
        overall_state: 'down',
        degraded_resources: [resource()],
      }));
      api.syncBackendHealthIndicatorForTest(host);
    }
    api.noteBackendHealthFailureForTest();
    api.noteBackendHealthFailureForTest();
    api.noteBackendHealthFailureForTest();
    api.syncBackendHealthIndicatorForTest(host);

    assert.equal(host.querySelectorAll('[data-backend-health]').length, 1, 'repeated renders must never build a second widget');
    // One builder for the single control, and one insertion point (the fallback mount). The healthy
    // path repaints the same node instead of removing it, so there is no second DOM owner.
    assert.equal([...CORE_SOURCE.matchAll(/className: 'backend-health-indicator',/g)].length, 1, 'one control builder');
    assert.equal([...CORE_SOURCE.matchAll(/function createBackendHealthIndicator\(\)/g)].length, 1, 'one control factory');
    assert.equal([...CORE_SOURCE.matchAll(/const backendHealthState = \{/g)].length, 1, 'one state object');
    assert.equal([...CORE_SOURCE.matchAll(/host\.prepend\(indicator\)/g)].length, 1, 'one insertion point');
  });

  test('the healthy triangle is visible green but deliberately subtle', () => {
    assert.match(CSS_SOURCE, /\[data-backend-health="ready"\][\s\S]*?color:\s*color-mix\(in srgb, var\(--good\) 45%, transparent\)/);
    assert.match(CSS_SOURCE, /\[data-backend-health="ready"\][\s\S]*?border-color:\s*color-mix\(in srgb, var\(--good\) 28%, transparent\)/);
    const readyRule = CSS_SOURCE.match(/\.backend-health-indicator\[data-backend-health="ready"\]\s*\{([\s\S]*?)\}/)?.[1] || '';
    assert.doesNotMatch(readyRule, /box-shadow|text-shadow/);
  });
}

module.exports = {runBackendHealthIndicatorSuite};

if (require.main === module) {
  runSuites([runBackendHealthIndicatorSuite]);
}

"""Gate H: rendered subsystem panels and their HTTP status data."""

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import fast_pointer_actions
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import run_when_browser_ready
from yolomux_lib import app as app_module


SYSTEM_STATUS_SERVICE_IDS = ("indexd", "statsd", "jobd", "statusd", "watchd", "approvald")
SYSTEM_STATUS_METRIC_KEYS = ("cpu_now_percent", "rss_bytes", "uptime_seconds")
# The Daemons roster puts the web process first and nests its one owned in-process subsystem under
# it; the six local services follow in the order `LOCAL_SERVICE_INVENTORY` declares. The web row is
# NOT a seventh local service -- it is this process, and `payload.server` is where it comes from.
ROSTER_ROW_IDS = ("web", "tmux-signal-watcher") + SYSTEM_STATUS_SERVICE_IDS


# Geometry and focus are only real on a VISIBLE subview, so every roster test opens Daemons first.
# Opening it also starts the 5s poll, and the first poll is already in flight when the click
# returns -- its `finally` would replace the fixture with the live payload between one
# `execute_script` and the next. This waits for that first poll to land, then freezes the poller so
# the fixture is what is under test. Tests that need a poll drive `refreshDebugSystemViews()`.
_FREEZE_SYSTEM_POLL_SCRIPT = """
    const done = arguments[arguments.length - 1];
    document.querySelector('[data-js-debug-subtab="system"]')?.click();
    const settle = () => {
      if (jsDebugSystemState.inFlight) {
        setTimeout(settle, 25);
        return;
      }
      clearRuntimeInterval('debug-system');
      // Stashed, not discarded: a test that drives the REAL Refresh control has to put the real
      // poller back, or it would only ever exercise this no-op stub.
      window.__realPollDebugSystemStatus = pollDebugSystemStatus;
      pollDebugSystemStatus = async () => false;
      done(true);
    };
    settle();
"""


def _open_daemons_with_frozen_poll(browser):
    assert browser.execute_async_script(_FREEZE_SYSTEM_POLL_SCRIPT) is True


def _schema_two_payload_script(states=None):
    """One browser fixture builder for a schema-2 `/api/system-status` payload."""
    return """
        const inventory = arguments[0];
        const states = arguments[1];
        const measured = value => ({state: 'measured', value, reason_code: '', reason: ''});
        const absent = (state, reasonCode, reason) => ({state, value: null, reason_code: reasonCode, reason});
        const health = index => ({
          observed: index < 3,
          unavailable_reason_code: index < 3 ? '' : 'resource_unobserved',
          state: index < 3 ? 'ready' : '',
          reason_code: 'none', recovery_outcome: 'none', process_epoch: `pid:${4200 + index}:start:98`,
          pid: index < 3 ? 4200 + index : 0, observed_at: 1900, since_revision: 700,
          since_wall_time: 1000, state_age_seconds: 900,
          transitions: [], transitions_total: 0, transitions_truncated: false, errors_by_reason: {},
          coverage: {retained_counters: 'full', retained_counter_reasons: [], counters: 'full', counter_reasons: [], counter_scope: 'web_process'},
          metrics: {
            restart_count: index < 3 ? measured(index) : absent('unavailable', 'resource_unobserved', 'The health observer has not recorded this service yet'),
            observations: measured(10),
            request_count: index < 3 ? measured(100 + index) : absent('unavailable', 'resource_unobserved', 'The health observer has not recorded this service yet'),
            error_count: index < 3 ? measured(0) : absent('unavailable', 'resource_unobserved', 'The health observer has not recorded this service yet'),
            completed_count: measured(100),
            latency_average_ms: index < 3 ? measured(12.5) : absent('unavailable', 'no_completed_request', 'No completed request has been timed in this web process'),
            latency_max_ms: index < 3 ? measured(340) : absent('unavailable', 'no_completed_request', 'No completed request has been timed in this web process'),
          },
        });
        jsDebugSystemState.payload = {
          ok: true,
          generated_at: Date.now() / 1000,
          state_dir: '/fixture/state',
          server: {version: '0.7.1', pid: 5150, started_at: Date.now() / 1000 - 8040, uptime_seconds: 8040, cpu_percent: 3, system_cpu_percent: 11, rss_bytes: 92274688},
          // CORE body keys only: `refresh` and the top-N folds moved to /api/system-status/advanced
          // when the snapshot split landed, so a fixture carrying them would describe a body the
          // server no longer sends.
          owner: {}, search_index: {}, caches: {}, client_events: {}, chat: {}, cpu_budget: {budget_percent: 30},
          tmux_signal_watcher: {state: 'attached', demanded: true, sessions: ['debug'], process_pid: 9001},
          local_services: {
            schema_version: 2,
            inventory,
            totals: {processes: 6, cpu_percent: 12, rss_bytes: 799014912},
            health: {available: true, reason_code: '', port: 7999, observer_epoch: 'ab12cd34', revision: 812, written_at: 1900, age_seconds: 2, history_coverage: 'full', history_reset_reason: '', persistence_state: 'ok', persistence_reason_code: '', resources: 6},
            services: inventory.map((id, index) => ({
              id,
              label: id,
              state: states[index],
              // `issue` is a RUNNING process that is not serving, so it keeps its pid and its
              // measured metrics and carries a transport reason -- it is not `unavailable`.
              reason_code: states[index] === 'idle' ? 'not_started' : (states[index] === 'running' ? '' : 'transport_failed'),
              reason: states[index] === 'unavailable' ? 'Status transport failed' : (states[index] === 'issue' ? 'Status transport refused' : (states[index] === 'idle' ? 'Starts on demand' : '')),
              pid: index < 3 ? 4200 + index : 0,
              started_at: index < 3 ? Date.now() / 1000 - 120 : 0,
              metrics: {
                cpu_now_percent: index < 3 ? measured(index + 1) : absent(states[index] === 'unavailable' ? 'unavailable' : 'not_running', states[index] === 'unavailable' ? 'transport_failed' : 'not_started', states[index] === 'unavailable' ? 'Status transport failed' : 'Service is not running'),
                rss_bytes: index < 3 ? measured((index + 1) * 1048576) : absent(states[index] === 'unavailable' ? 'unavailable' : 'not_running', states[index] === 'unavailable' ? 'transport_failed' : 'not_started', states[index] === 'unavailable' ? 'Status transport failed' : 'Service is not running'),
                uptime_seconds: index < 3 ? measured(120 + index) : absent(states[index] === 'unavailable' ? 'unavailable' : 'not_running', states[index] === 'unavailable' ? 'transport_failed' : 'not_started', states[index] === 'unavailable' ? 'Status transport failed' : 'Service is not running'),
              },
              health: health(index),
              details: {},
            })),
          },
        };
        refreshDebugSystemViews();
    """


DEFAULT_ROSTER_STATES = ["running", "running", "running", "unavailable", "idle", "running"]
# `issue` -- a running daemon whose status transport failed -- is the one published service state
# no browser fixture exercised. It sits at index 2 so the row keeps a pid and measured metrics: the
# whole point of the state is that the PROCESS is alive while the SERVICE is not answering.
ISSUE_ROSTER_STATES = ["running", "running", "issue", "unavailable", "idle", "running"]


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 SubsystemSpec subsystem rows")
def test_h1_every_subsystem_cell_has_an_honest_rendered_value(browser, tmp_path):
    """Every subsystem row has a visible value, and a healthy running row has no stale reason."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    rows = browser.execute_script(
        """
        return Array.from(document.querySelectorAll('[data-subsystem-row]')).map(row => ({
          state: row.dataset.subsystemState || '',
          value: row.querySelector('[data-subsystem-value]')?.textContent?.trim() || '',
          reason: row.querySelector('[data-subsystem-reason]')?.textContent?.trim() || '',
          reasonVisible: Boolean(row.querySelector('[data-subsystem-reason]')
            && getComputedStyle(row.querySelector('[data-subsystem-reason]')).display !== 'none'),
        }));
        """,
    )
    assert len(rows) == 11, rows
    for row in rows:
        assert row["value"] and row["value"] != "[object Object]", row
        if row["state"] == "running":
            assert row["reason"] == "" and not row["reasonVisible"], row
        elif row["state"] == "unknown":
            assert row["reason"] and row["reasonVisible"], row
        else:
            assert row["state"] == "paused", row
            assert row["reason"] and row["reasonVisible"], row


@pytest.mark.no_browser
def test_h2_system_status_metrics_are_not_null(monkeypatch, gate_runtime_paths):
    """The System API reports five real services and qualifies every unavailable metric."""
    now = 1_785_600_000.0
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.stats_current_client.database_path = gate_runtime_paths.state_dir / "services" / "stats-v7.sqlite3"
        monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {
            "service": "indexd", "pid": 4101, "started_at": now - 101, "healthy": True,
            "resources": {"cpu_percent": None, "rss_bytes": 64 * 1024 * 1024},
        })
        monkeypatch.setattr(webapp.stats_current_runtime, "status", lambda: {
            "service": {"ok": True, "pid": 4102, "started_at": now - 102, "migration": {
                "state": "ready",
                "result": "recovered",
                "issue_records": ({
                    "kind": "unreadable_current_database",
                    "source": "stats-v7.sqlite3.corrupt-1785600000",
                    "detail": "database disk image is malformed",
                },),
            }},
            "families": {},
        })
        monkeypatch.setattr(webapp.stats_current_client, "runtime_status", lambda status: {
            **status,
            "resources": {"cpu_percent": 2.0, "rss_bytes": 48 * 1024 * 1024},
        }, raising=False)
        monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {
            "service": "jobd", "pid": 4103, "started_at": now - 103, "healthy": True,
            "resources": {"cpu_percent": 3.0, "rss_bytes": 96 * 1024 * 1024},
        })
        monkeypatch.setattr(webapp.status_client, "runtime_status", lambda: {
            "service": "statusd", "pid": 4104, "started_at": now - 104, "healthy": True,
            "resources": {"cpu_percent": 4.0, "rss_bytes": 32 * 1024 * 1024},
        })
        monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {
            "service": "approvald", "pid": 0, "started_at": 0.0, "healthy": True,
            "resources": {"cpu_percent": None, "rss_bytes": None},
        })
        payload = webapp.system_status_payload()
    finally:
        webapp.control_server.stop()

    local_services = payload["local_services"]
    assert local_services["schema_version"] == 2, local_services
    assert tuple(local_services["inventory"]) == SYSTEM_STATUS_SERVICE_IDS, local_services
    services = local_services["services"]
    assert tuple(service["id"] for service in services) == SYSTEM_STATUS_SERVICE_IDS, services
    for service in services:
        for key in SYSTEM_STATUS_METRIC_KEYS:
            metric = service["metrics"][key]
            assert metric["state"] in {"measured", "warming", "not_running", "unavailable"}, (service, key)
            if metric["state"] == "measured":
                assert isinstance(metric["value"], (int, float)), (service, key)
            else:
                assert metric["value"] is None and metric["reason_code"] and metric["reason"], (service, key)
    assert services[0]["metrics"]["cpu_now_percent"]["state"] == "warming", services[0]
    assert services[1]["metrics"]["cpu_now_percent"] == {
        "state": "measured", "value": 2.0, "reason_code": "", "reason": "",
    }, services[1]
    assert services[1]["metrics"]["rss_bytes"]["value"] == 48 * 1024 * 1024, services[1]
    assert all(metric["state"] == "not_running" for metric in services[-1]["metrics"].values()), services[-1]
    assert local_services["recovery_events"] == [{
        "subsystem": "statsd",
        "event": "unreadable_current_database",
        "quarantined_artifact": "stats-v7.sqlite3.corrupt-1785600000",
        "quarantined_path": str(gate_runtime_paths.state_dir / "services" / "stats-v7.sqlite3.corrupt-1785600000"),
        "destination_path": str(gate_runtime_paths.state_dir / "services" / "stats-v7.sqlite3"),
        "reason": "database disk image is malformed",
    }]


@pytest.mark.browser
def test_h3_every_roster_row_remains_rendered_in_inventory_order(browser, tmp_path):
    """The roster pins the web process, its child subsystem and all six services, in a stable order.

    This replaced the old six-row assertion when the card wall became one roster: the web process
    and the tmux signal watcher used to be standalone boxes above the table and are now the first
    two rows of it, so the pinned set grew by exactly those two and by nothing else.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    rows = run_when_browser_ready(
        browser,
        _schema_two_payload_script()
        + """
        return Array.from(document.querySelectorAll('[data-subsystem-row]')).map(row => ({
          id: row.dataset.subsystemId || '',
          kind: row.dataset.subsystemKind || '',
          parent: row.dataset.subsystemParent || '',
          state: row.dataset.subsystemState || '',
          tone: row.querySelector('[data-subsystem-tone]')?.dataset.subsystemTone || '',
          stateLabel: row.querySelector('[data-subsystem-state-label]')?.textContent?.trim() || '',
          reason: row.querySelector('[data-subsystem-reason]')?.textContent?.trim() || '',
          metrics: Object.fromEntries(Array.from(row.querySelectorAll('[data-subsystem-metric]')).map(cell => [cell.dataset.subsystemMetric, cell.textContent.trim()])),
          reasons: Object.fromEntries(Array.from(row.querySelectorAll('[data-subsystem-metric]')).map(cell => [cell.dataset.subsystemMetric, cell.getAttribute('title') || ''])),
        }));
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert tuple(row["id"] for row in rows) == ROSTER_ROW_IDS, rows
    assert all(set(row["metrics"]) == set(SYSTEM_STATUS_METRIC_KEYS) for row in rows), rows
    by_id = {row["id"]: row for row in rows}
    assert by_id["tmux-signal-watcher"]["kind"] == "child" and by_id["tmux-signal-watcher"]["parent"] == "web", rows
    # A down service is red WITH its reason; an idle one is gray and carries no alert paint.
    assert by_id["statusd"]["state"] == "unavailable" and by_id["statusd"]["tone"] == "bad", rows
    assert by_id["statusd"]["reason"] == "Status transport failed", rows
    assert by_id["watchd"]["state"] == "idle" and by_id["watchd"]["tone"] == "muted", rows
    assert by_id["watchd"]["reason"] == "Starts on demand", rows
    # Status never depends on colour alone: every row renders a state word beside its dot.
    assert all(row["stateLabel"] for row in rows), rows
    # Unobserved process metrics say why; they never read as a bare zero.
    # An unmeasured cell is an em dash carrying its reason, never a bare zero and never blank.
    assert by_id["watchd"]["metrics"]["rss_bytes"] == "\u2014", rows
    assert by_id["watchd"]["reasons"]["rss_bytes"] == "Service is not running", rows
    assert "0" not in by_id["watchd"]["metrics"].values(), rows


@pytest.mark.browser
def test_a_degraded_service_renders_the_word_issue_in_the_red_tone(browser, tmp_path):
    """A running daemon that is not serving reads as the WORD `Issue`, painted the roster's red.

    No browser fixture used the `issue` state before this one, so the degraded row had never been
    rendered by a real engine: its state word, its paint, and its reason were pinned only as
    strings in the Node shard. Colour is checked against the roster's OWN other rows rather than
    against a hex literal -- an `issue` must paint exactly like the `unavailable` row beside it and
    must not paint like the ready or idle rows, which is the rule `debugSystemStateTone` owns.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    painted = run_when_browser_ready(
        browser,
        _schema_two_payload_script()
        + """
        const statusOf = id => {
          const row = document.querySelector(`[data-subsystem-row][data-subsystem-id="${id}"]`);
          const status = row.querySelector('[data-subsystem-tone]');
          return {
            state: row.dataset.subsystemState || '',
            tone: status.dataset.subsystemTone || '',
            word: status.querySelector('[data-subsystem-state-label]')?.textContent?.trim() || '',
            color: getComputedStyle(status).color,
            reason: row.querySelector('[data-subsystem-reason]')?.textContent?.trim() || '',
            uptime: row.querySelector('[data-subsystem-metric="uptime_seconds"]')?.textContent?.trim() || '',
          };
        };
        return {issue: statusOf('jobd'), down: statusOf('statusd'), ready: statusOf('statsd'), idle: statusOf('watchd')};
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        ISSUE_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    issue = painted["issue"]
    assert issue["state"] == "issue" and issue["tone"] == "bad", painted
    # The WORD, in the browser. Status is never carried by colour alone.
    assert issue["word"] == "Issue", painted
    assert issue["reason"] == "Status transport refused", painted
    # Red, and the same red the roster already uses for an actionable row -- not a second token.
    red = tuple(int(part) for part in issue["color"].removeprefix("rgb(").removesuffix(")").split(",")[:3])
    assert red[0] > red[1] and red[0] > red[2], painted
    assert issue["color"] == painted["down"]["color"], painted
    # ...and not the ready green or the idle gray, so the assertion above cannot pass on a roster
    # that paints every row the same colour.
    assert issue["color"] not in (painted["ready"]["color"], painted["idle"]["color"]), painted
    # The state's whole meaning: the PROCESS is up while the SERVICE is not answering. A row that
    # rendered an em dash here would be `unavailable` wearing a different word.
    assert issue["uptime"] == "2m 2s", painted
    assert painted["down"]["uptime"] == "—", painted


@pytest.mark.browser
def test_the_default_daemons_view_is_a_roster_not_a_card_wall(browser, tmp_path):
    """The retired Server/CPU budget/Worker totals/Search & caches boxes are gone from the default."""
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    layout = run_when_browser_ready(
        browser,
        _schema_two_payload_script()
        + """
        const view = document.querySelector('[data-js-debug-system]');
        const regions = Array.from(view.querySelectorAll('[data-js-debug-system-region]')).map(node => node.dataset.jsDebugSystemRegion);
        const summary = view.querySelector('[data-js-debug-roster-summary]');
        const roster = view.querySelector('[data-js-debug-roster]');
        const advanced = view.querySelector('[data-js-debug-system-advanced]');
        return {
          regions,
          cards: view.querySelectorAll('.js-debug-system-card').length,
          grids: view.querySelectorAll('.js-debug-system-grid').length,
          headings: Array.from(view.querySelectorAll('h3')).map(node => node.textContent.trim()),
          summaryText: summary?.textContent?.replace(/\\s+/g, ' ').trim() || '',
          advancedOpen: advanced ? advanced.hasAttribute('open') : null,
          rosterTop: roster?.getBoundingClientRect().top ?? 0,
          advancedTop: advanced?.getBoundingClientRect().top ?? 0,
          summaryTop: summary?.getBoundingClientRect().top ?? 0,
          detailRows: view.querySelectorAll('[data-subsystem-detail-row][data-subsystem-detail-built="true"]').length,
          transitionLists: view.querySelectorAll('.js-debug-system-health-transitions').length,
          samplerTables: view.querySelectorAll('[data-js-debug-sampler-families]').length,
        };
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    # `announce` is a visually hidden `role="status"` region carrying only the state counts. It is
    # its own region deliberately: the region cache rewrites a region only when its generated HTML
    # changes, so keeping the volatile "Updated N seconds ago" out of this one is what stops a
    # screen reader being re-announced on every 5-second poll.
    assert layout["regions"] == ["announce", "summary", "alerts", "roster", "advanced"], layout
    assert layout["cards"] == 0 and layout["grids"] == 0, layout
    for retired in ("Server", "CPU budget", "Distributed owner", "Worker totals", "Search & caches", "Events & chat", "Tmux signal watcher"):
        assert retired not in layout["headings"], layout
    assert layout["advancedOpen"] is False, layout
    assert layout["summaryTop"] <= layout["rosterTop"] < layout["advancedTop"], layout
    assert "ready" in layout["summaryText"] and "issues" in layout["summaryText"], layout
    # Lazy, not hidden: no collapsed row builds its transition list or its sampler table.
    assert layout["detailRows"] == 0, layout
    assert layout["transitionLists"] == 0 and layout["samplerTables"] == 0, layout


# Records every URL the panel asks for, and answers the two system-status routes from fixtures. The
# core body is the roster fixture the other tests already build, plus a DECOY `top_endpoints`: the
# advanced diagnostics moved off the core body when `/api/system-status` became a background
# snapshot, so a card rendered from that decoy is a card reading a key the server no longer sends.
_ADVANCED_ROUTE_RECORDER_SCRIPT = """
    const advanced = arguments[0];
    const core = jsDebugSystemState.payload;
    core.top_endpoints = [{surface: '/api/decoy-from-the-core-body', count: 1, compute_ms_max: 1, payload_bytes_total: 1}];
    window.__systemStatusRequests = [];
    window.__realApiFetchJsonQuiet = apiFetchJsonQuiet;
    apiFetchJsonQuiet = async (url, ...rest) => {
      window.__systemStatusRequests.push(url);
      if (url.startsWith('/api/system-status/advanced')) return advanced;
      if (url.startsWith('/api/system-status')) return core;
      return window.__realApiFetchJsonQuiet(url, ...rest);
    };
    // The freeze helper stubbed the poller out; this test is about what the REAL poller requests.
    pollDebugSystemStatus = window.__realPollDebugSystemStatus;
    return true;
"""

# One real poll, settled: both the core read and the advanced read it may drive are awaited before
# the request list is reported.
_POLL_AND_SETTLE_SCRIPT = """
    const done = arguments[arguments.length - 1];
    pollDebugSystemStatus({force: true}).then(() => {
      const settle = () => {
        if (jsDebugSystemState.inFlight || jsDebugSystemAdvancedState.inFlight) {
          setTimeout(settle, 25);
          return;
        }
        done(window.__systemStatusRequests.slice());
      };
      settle();
    }, error => done(['poll failed', String(error)]));
"""

_ADVANCED_FIXTURE = {
    "ok": True,
    "generated_at": 1902,
    "owner": {"debug": {"generation_count": 41}, "control": {}},
    "refresh": {"local_refreshing": {}, "coalescing": {"recent_pending_count": 0}, "counters": {"coalesced_refresh_requests": 7}, "recurring_work": [], "roles": {}},
    "top_endpoints": [{"surface": "/api/from-the-advanced-route", "count": 12, "compute_ms_max": 4, "payload_bytes_total": 2048}],
    "top_background_work": [],
    "top_event_types": [],
    "login_throttle": {},
    "largest_active_transcripts": [],
    "transcripts_cache": {},
}


@pytest.mark.browser
def test_advanced_diagnostics_are_fetched_only_while_their_disclosure_is_open(browser, tmp_path):
    """The Advanced body has its own route, and the panel asks for it only when it is open.

    `/api/system-status` is now published from a retained background snapshot, and the diagnostics a
    reader opens deliberately -- refresh coordination, the top-N folds, transcripts, `owner.debug` --
    were split onto `/api/system-status/advanced` at their own cadence precisely so that transcript
    scans and top-N folds stop running on the five-second poll of a panel nobody has opened.

    Requesting that route on every poll would put all of it back and nothing rendered would show it,
    so the assertion is on the REQUEST LIST, driven through the real click path in a real browser.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    run_when_browser_ready(
        browser,
        _schema_two_payload_script() + "return true;",
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert browser.execute_script(_ADVANCED_ROUTE_RECORDER_SCRIPT, _ADVANCED_FIXTURE) is True

    closed_requests = browser.execute_async_script(_POLL_AND_SETTLE_SCRIPT)
    assert closed_requests == ["/api/system-status"], closed_requests

    # Opening it is the demand signal, through a real mouse click on the real summary.
    fast_pointer_actions(browser).click(
        browser.find_element(By.CSS_SELECTOR, "[data-js-debug-system-advanced-summary]")
    ).perform()
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const settle = () => {
          if (jsDebugSystemAdvancedState.inFlight || !jsDebugSystemAdvancedState.payload) {
            setTimeout(settle, 25);
            return;
          }
          const advanced = document.querySelector('[data-js-debug-system-advanced]');
          done({
            requests: window.__systemStatusRequests.slice(),
            open: advanced.hasAttribute('open'),
            text: advanced.textContent.replace(/\\s+/g, ' ').trim(),
            state: advanced.querySelector('[data-js-debug-system-advanced-state]')?.dataset.jsDebugSystemAdvancedState || '',
          });
        };
        settle();
        """
    )
    assert opened["open"] is True, opened
    assert opened["requests"] == ["/api/system-status", "/api/system-status/advanced"], opened
    # Rendered from the advanced body, and NOT from the retired core key sitting right beside it.
    assert "/api/from-the-advanced-route" in opened["text"], opened
    assert "decoy-from-the-core-body" not in opened["text"], opened
    # The label and value are adjacent cells of the one key/value list, so textContent joins them.
    assert "Generations41" in opened["text"], opened
    assert opened["state"] == "", opened

    # Closing it stops the demand: the next poll is a core-only read again.
    fast_pointer_actions(browser).click(
        browser.find_element(By.CSS_SELECTOR, "[data-js-debug-system-advanced-summary]")
    ).perform()
    closed_again = browser.execute_async_script(_POLL_AND_SETTLE_SCRIPT)
    assert closed_again == [
        "/api/system-status",
        "/api/system-status/advanced",
        "/api/system-status",
    ], closed_again


@pytest.mark.browser
def test_a_withheld_system_status_snapshot_is_rendered_as_the_state_it_is(browser, tmp_path):
    """Before the first publish, or past the freshness deadline, the body is a typed refusal.

    The aged report is WITHHELD, not relabelled, so there is nothing to fall back on: the panel has
    to say which state it is in rather than draw a roster of fabricated `unavailable` rows, and it
    has to re-ask in half a second rather than leave the reader a blank five-second poll interval.
    The core slot is demand-gated, so this is the normal first read after any quiet period.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    rendered = browser.execute_async_script(
        """
        const refusal = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__intervalDelays = [];
        const realReset = resetRuntimeInterval;
        resetRuntimeInterval = (name, callback, delay) => {
          if (name === 'debug-system') window.__intervalDelays.push(delay);
          // The recorded timer is NOT armed: an interval left running at the refusal cadence would
          // keep firing into the next test through the shared browser.
          return null;
        };
        apiFetchJsonQuiet = async () => refusal;
        pollDebugSystemStatus = window.__realPollDebugSystemStatus;
        pollDebugSystemStatus({force: true}).then(() => {
          const view = document.querySelector('[data-js-debug-system]');
          const status = view.querySelector('[data-js-debug-system-snapshot-state]');
          resetRuntimeInterval = realReset;
          done({
            state: status?.dataset.jsDebugSystemSnapshotState || '',
            reasonCode: status?.dataset.jsDebugSystemSnapshotReasonCode || '',
            text: view.textContent.replace(/\\s+/g, ' ').trim(),
            regions: view.querySelectorAll('[data-js-debug-system-region]').length,
            rosterRows: view.querySelectorAll('[data-subsystem-id]').length,
            delays: window.__intervalDelays.slice(),
          });
        }, error => done({error: String(error)}));
        """,
        {
            "ok": False,
            "schema": "system-status-snapshot",
            "snapshot": {
                "state": "stale",
                "reason_code": "system_status_snapshot_stale",
                "reason": "The newest system-status snapshot is 14.0s old, past the 12.0s freshness deadline.",
                "age_seconds": 14.0,
                "last_generated_at": 1888,
                "last_sequence": 3,
                "cadence_seconds": 5.0,
                "freshness_deadline_seconds": 12.0,
            },
        },
    )
    assert rendered.get("error") is None, rendered
    assert rendered["state"] == "stale", rendered
    assert rendered["reasonCode"] == "system_status_snapshot_stale", rendered
    assert "past the 12.0s freshness deadline" in rendered["text"], rendered
    # Nothing measured, so nothing drawn: no regions, and no roster rows invented from an empty body.
    assert rendered["regions"] == 0 and rendered["rosterRows"] == 0, rendered
    # And the next read is half a second away, not five.
    assert rendered["delays"] and rendered["delays"][-1] == 500, rendered


@pytest.mark.browser
def test_roster_disclosure_opens_by_mouse_enter_and_space_without_opening_another(browser, tmp_path):
    """One disclosure per row, driven through the real browser event path."""
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    run_when_browser_ready(
        browser,
        _schema_two_payload_script() + "return true;",
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    def toggle_state():
        return browser.execute_script(
            """
            return Array.from(document.querySelectorAll('[data-js-debug-roster-toggle]')).map(button => ({
              id: button.dataset.jsDebugRosterToggle,
              expanded: button.getAttribute('aria-expanded'),
              controls: button.getAttribute('aria-controls'),
              targetPresent: Boolean(document.getElementById(button.getAttribute('aria-controls'))),
              detailPresent: (document.getElementById(button.getAttribute('aria-controls'))?.childElementCount || 0) > 0,
              label: button.getAttribute('aria-label') || '',
            }));
            """
        )

    before = toggle_state()
    assert [row["expanded"] for row in before] == ["false"] * len(ROSTER_ROW_IDS), before
    assert not any(row["detailPresent"] for row in before), before
    # Every disclosure target exists even while its content is unbuilt, so `aria-controls` and the
    # `aria-expanded` state it qualifies both resolve for a screen reader.
    assert all(row["targetPresent"] for row in before), before

    browser.execute_script("document.querySelector('[data-js-debug-roster-toggle=\"statsd\"]').click();")
    after_click = {row["id"]: row for row in toggle_state()}
    assert after_click["statsd"]["expanded"] == "true", after_click
    assert after_click["statsd"]["detailPresent"] is True, after_click
    assert [row for row in after_click.values() if row["expanded"] == "true"] == [after_click["statsd"]], after_click
    assert after_click["statsd"]["label"].startswith("Hide details"), after_click

    # Enter and Space reach the same one handler because the control is a real button.
    browser.execute_script("document.querySelector('[data-js-debug-roster-toggle=\"jobd\"]').focus();")
    browser.switch_to.active_element.send_keys(Keys.ENTER)
    after_enter = {row["id"]: row for row in toggle_state()}
    assert after_enter["jobd"]["expanded"] == "true", after_enter
    assert after_enter["statsd"]["expanded"] == "true", after_enter

    browser.switch_to.active_element.send_keys(Keys.SPACE)
    after_space = {row["id"]: row for row in toggle_state()}
    assert after_space["jobd"]["expanded"] == "false", after_space
    assert after_space["jobd"]["detailPresent"] is False, after_space
    assert after_space["statsd"]["expanded"] == "true", after_space

    # The open disclosure's own padding, MEASURED. `.js-debug-roster-detailrow > td` tied the plain
    # `.js-debug-system-table th, td` rule on specificity and sat above it in the file, so source
    # order won and the block had always rendered with the dense table-cell padding -- the roomier
    # padding written for it had never once applied at any width. A source assertion could not have
    # caught that: both rules are present and both look correct in isolation.
    padding = browser.execute_script(
        """
        const cell = document.getElementById('js-debug-roster-detail-statsd');
        const metric = document.querySelector('[data-subsystem-row] [data-subsystem-column]');
        const styles = getComputedStyle(document.documentElement);
        const token = name => styles.getPropertyValue(name).trim();
        return {
          detail: getComputedStyle(cell).padding,
          metric: getComputedStyle(metric).padding,
          expected: `${token('--space-8')} ${token('--space-10')}`,
        };
        """
    )
    assert padding["detail"] == padding["expected"], padding
    assert padding["detail"] != padding["metric"], padding


def _arm_refresh_busy_recorder(browser):
    """Record what the Refresh control looked like at every render of one refresh round.

    The in-flight window is short and the summary region is REPLACED during it, so sampling from
    Python races the render. A MutationObserver on the view records every state the control was
    actually rendered in, which is what makes "it was never `disabled`" a measurement.
    """
    browser.execute_script(
        """
        const view = document.querySelector('[data-js-debug-system]');
        window.__refreshBusyStates = [];
        window.__refreshBusyObserver?.disconnect();
        window.__refreshBusyObserver = new MutationObserver(() => {
          const button = document.querySelector('[data-js-debug-system-refresh]');
          if (!button) return;
          window.__refreshBusyStates.push({
            disabled: button.disabled === true,
            ariaDisabled: button.getAttribute('aria-disabled') || '',
          });
        });
        window.__refreshBusyObserver.observe(view, {
          childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'aria-disabled'],
        });
        """
    )


def _settled_refresh_state(browser):
    return browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const settle = () => {
          if (jsDebugSystemState.inFlight) {
            setTimeout(settle, 25);
            return;
          }
          window.__refreshBusyObserver?.disconnect();
          const view = document.querySelector('[data-js-debug-system]');
          const button = document.querySelector('[data-js-debug-system-refresh]');
          const active = document.activeElement;
          done({
            activeTag: active ? active.tagName : '',
            activeFocusKey: (active && active.dataset && active.dataset.jsDebugSystemFocusKey) || '',
            activeInsideView: Boolean(view && active && view.contains(active)),
            busyStates: window.__refreshBusyStates || [],
            disabledNow: button ? button.disabled === true : null,
            ariaDisabledNow: button ? (button.getAttribute('aria-disabled') || '') : null,
          });
        };
        settle();
        """
    )


@pytest.mark.browser
def test_roster_refresh_preserves_expansion_focus_and_scroll(browser, tmp_path):
    """A five-second poll must not close an open row or steal the focus inside it.

    The panel used to rebuild the whole Daemons view with `innerHTML` twice per poll. Nothing was
    focusable then, so nobody noticed; seven disclosure buttons make it a defect a reader sees.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    result = run_when_browser_ready(
        browser,
        _schema_two_payload_script()
        + """
        const view = document.querySelector('[data-js-debug-system]');
        document.querySelector('[data-js-debug-roster-toggle="statsd"]').click();
        const toggle = document.querySelector('[data-js-debug-roster-toggle="statsd"]');
        toggle.focus();
        view.scrollTop = 40;
        const before = {
          expanded: toggle.getAttribute('aria-expanded'),
          focused: document.activeElement === toggle,
          scrollTop: view.scrollTop,
          detailNode: document.getElementById('js-debug-roster-detail-statsd'),
        };
        // Exactly what a poll does: same payload identity, new generated_at, two renders.
        jsDebugSystemState.inFlight = true;
        refreshDebugSystemViews();
        jsDebugSystemState.inFlight = false;
        jsDebugSystemState.payload = {...jsDebugSystemState.payload, generated_at: jsDebugSystemState.payload.generated_at + 5};
        refreshDebugSystemViews();
        const afterToggle = document.querySelector('[data-js-debug-roster-toggle="statsd"]');
        return {
          beforeExpanded: before.expanded,
          beforeFocused: before.focused,
          beforeScrollTop: before.scrollTop,
          afterExpanded: afterToggle.getAttribute('aria-expanded'),
          afterFocused: document.activeElement === afterToggle,
          afterScrollTop: view.scrollTop,
          detailSurvived: document.getElementById('js-debug-roster-detail-statsd') === before.detailNode,
          detailPresent: (document.getElementById('js-debug-roster-detail-statsd')?.childElementCount || 0) > 0,
          openRows: document.querySelectorAll('[data-subsystem-detail-row][data-subsystem-detail-built="true"]').length,
        };
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert result["beforeExpanded"] == "true" and result["beforeFocused"] is True, result
    assert result["afterExpanded"] == "true", result
    assert result["afterFocused"] is True, result
    assert result["detailPresent"] is True and result["openRows"] == 1, result
    assert result["afterScrollTop"] == result["beforeScrollTop"], result
    # The in-flight render must not touch the roster at all: the open detail node is the SAME node.
    assert result["detailSurvived"] is True, result

    # Everything above drives `refreshDebugSystemViews()` directly with focus already parked on a
    # roster toggle, so it never activates the Refresh control and could not fail on the reported
    # defect: a real click or a real Enter on Refresh dumped the reader on `document.body` and never
    # brought them back. Drive the real control, by mouse and by keyboard, with the REAL poller.
    browser.execute_script("pollDebugSystemStatus = window.__realPollDebugSystemStatus;")
    for activation in ("mouse", "keyboard"):
        _arm_refresh_busy_recorder(browser)
        if activation == "mouse":
            fast_pointer_actions(browser).click(
                browser.find_element(By.CSS_SELECTOR, "[data-js-debug-system-refresh]")
            ).perform()
        else:
            browser.execute_script("document.querySelector('[data-js-debug-system-refresh]').focus();")
            browser.switch_to.active_element.send_keys(Keys.ENTER)
        settled = _settled_refresh_state(browser)
        assert settled["activeTag"] != "BODY", (activation, settled)
        assert settled["activeInsideView"] is True, (activation, settled)
        assert settled["activeFocusKey"] == "roster-refresh", (activation, settled)
        # The cause, asserted directly: `disabled` is what blurs the control, and it also hides it
        # from a screen reader mid-refresh. The busy state is carried by `aria-disabled`, and
        # re-entry stays blocked by the `inFlight` early return in `pollDebugSystemStatus`.
        assert [state for state in settled["busyStates"] if state["disabled"]] == [], (activation, settled)
        assert any(state["ariaDisabled"] == "true" for state in settled["busyStates"]), (activation, settled)
        assert settled["disabledNow"] is False and settled["ariaDisabledNow"] == "", (activation, settled)


@pytest.mark.browser
def test_the_first_poll_after_creation_and_after_a_rerender_replaces_no_region(browser, tmp_path):
    """The region cache must be seeded by EVERY site that builds the five regions.

    `createDebugPanel` and `renderDebugPanels({force: true})` both write the whole Daemons view
    through `debugPanelHtml`, and neither recorded what it wrote. So the next poll found each
    region's cache entry `undefined`, decided all five had changed, and assigned `innerHTML` to
    every one of them -- a wholesale DOM replacement on an UNCHANGED payload, taking the focused
    control and the open disclosure row with it.

    The test above cannot catch this: it starts from a cache an earlier refresh already populated,
    so it never exercises the creation lifecycle. This one measures the FIRST poll after each full
    build, by node identity and MutationObserver rather than by rendered text -- identical text
    written twice is still a replacement, and that is the defect.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    result = run_when_browser_ready(
        browser,
        _schema_two_payload_script()
        + """
        const WATCHED = ['roster', 'advanced'];
        const regionsOf = view => Object.fromEntries(
          Array.from(view.querySelectorAll('[data-js-debug-system-region]'))
            .map(node => [node.dataset.jsDebugSystemRegion, node]),
        );
        // ONE unchanged in-flight poll, measured. `takeRecords()` is read synchronously because a
        // MutationObserver callback would not have run yet when this script returns.
        function measureUnchangedPoll(view) {
          const before = regionsOf(view);
          const observers = WATCHED.map(name => {
            const observer = new MutationObserver(() => {});
            observer.observe(before[name], {childList: true, subtree: false});
            return observer;
          });
          jsDebugSystemState.inFlight = true;
          refreshDebugSystemViews();
          jsDebugSystemState.inFlight = false;
          const replaced = WATCHED.filter((name, index) => observers[index].takeRecords().length > 0);
          observers.forEach(observer => observer.disconnect());
          const after = regionsOf(view);
          return {
            replaced,
            sameNodes: WATCHED.every(name => after[name] === before[name]),
          };
        }
        function openFocusAndMeasure() {
          const view = document.querySelector('[data-js-debug-system]');
          const toggle = document.querySelector('[data-js-debug-roster-toggle="statsd"]');
          toggle.focus();
          const detail = document.getElementById('js-debug-roster-detail-statsd');
          const measured = measureUnchangedPoll(view);
          const afterToggle = document.querySelector('[data-js-debug-roster-toggle="statsd"]');
          return {
            ...measured,
            focusKept: document.activeElement === afterToggle,
            stillOpen: afterToggle.getAttribute('aria-expanded') === 'true',
            detailKept: document.getElementById('js-debug-roster-detail-statsd') === detail,
          };
        }

        // Open a row first: expansion state lives outside the DOM, so it survives a full rebuild
        // and gives every measurement below the same open row to preserve.
        document.querySelector('[data-js-debug-roster-toggle="statsd"]').click();

        // (1) The first poll after a FULL RERENDER of the panel body.
        renderDebugPanels({force: true});
        const afterRerender = openFocusAndMeasure();

        // (2) The first poll after PANEL CREATION, through the real creation path.
        document.querySelector('.js-debug-panel').replaceWith(createDebugPanel());
        const afterCreate = openFocusAndMeasure();

        return {afterRerender, afterCreate};
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={
            "refreshDebugSystemViews": "function",
            "renderDebugPanels": "function",
            "createDebugPanel": "function",
        },
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    for label in ("afterRerender", "afterCreate"):
        case = result[label]
        assert case["replaced"] == [], (label, case)
        assert case["sameNodes"] is True, (label, case)
        assert case["stillOpen"] is True, (label, case)
        assert case["detailKept"] is True, (label, case)
        assert case["focusKept"] is True, (label, case)


@pytest.mark.browser
def test_system_panel_reports_each_typed_tmux_signal_watcher_state(browser, tmp_path):
    """The roster must not collapse absent, attaching, idle, and exited watchers into one Boolean.

    The watcher moved from a standalone card to the web process's child row, so its five typed
    states are now read from the row itself (visible without expanding) and its demand, sessions and
    detail from the row's own disclosure. Nothing in the vocabulary changed -- only where it lives.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    states = run_when_browser_ready(
        browser,
        """
        const fixtures = [
          {state: 'never-started', demanded: false, healthy: false, reason_code: 'not_started', reason: 'Tmux signal watcher has not been started', sessions: [], process_pid: 0},
          {state: 'never-started', demanded: true, healthy: false, reason_code: 'not_started', reason: 'Tmux signal watcher has not been started', sessions: [], process_pid: 0},
          {state: 'attaching', demanded: true, healthy: null, reason_code: 'attaching', reason: 'Tmux control client is attaching', sessions: ['debug'], process_pid: 0},
          {state: 'no-sessions', demanded: true, healthy: true, reason_code: 'no_sessions', reason: 'No tmux sessions are configured to watch', sessions: [], process_pid: 0},
          {state: 'exited', demanded: true, healthy: false, reason_code: 'control_client_exited', reason: 'Tmux control client exited', sessions: ['debug'], process_pid: 0},
        ];
        return fixtures.map(watcher => {
          jsDebugSystemState.payload = {
            ok: true, generated_at: Date.now() / 1000,
            server: {}, owner: {}, refresh: {}, search_index: {}, caches: {}, client_events: {}, chat: {}, cpu_budget: {},
            top_endpoints: [], top_background_work: [],
            local_services: {schema_version: 2, inventory: [], totals: {}, services: []},
            tmux_signal_watcher: watcher,
          };
          refreshDebugSystemViews();
          document.querySelector('[data-js-debug-roster-toggle="tmux-signal-watcher"]').click();
          const row = document.querySelector('[data-subsystem-row][data-subsystem-id="tmux-signal-watcher"]');
          const detail = document.querySelector('[data-js-debug-tmux-signal-watcher]');
          const alert = document.querySelector('[data-system-alert="tmux-signal-watcher"]');
          const result = {
            state: detail?.dataset.tmuxSignalWatcherState || '',
            demanded: detail?.dataset.tmuxSignalWatcherDemanded || '',
            role: detail?.getAttribute('role') || '',
            text: detail?.textContent?.replace(/\\s+/g, ' ').trim() || '',
            rowState: row?.dataset.subsystemState || '',
            rowTone: row?.querySelector('[data-subsystem-tone]')?.dataset.subsystemTone || '',
            rowLabel: row?.querySelector('[data-subsystem-state-label]')?.textContent?.trim() || '',
            alerted: Boolean(alert),
          };
          document.querySelector('[data-js-debug-roster-toggle="tmux-signal-watcher"]').click();
          return result;
        });
        """,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert [item["state"] for item in states] == ["never-started", "never-started", "attaching", "no-sessions", "exited"], states
    assert [item["demanded"] for item in states] == ["false", "true", "true", "true", "true"], states
    assert [item["role"] for item in states] == ["status", "alert", "status", "status", "alert"], states
    assert "Idle" in states[0]["text"] and "DemandNo" in states[0]["text"] and "not been started" in states[0]["text"], states[0]
    assert "Never started" in states[1]["text"] and "DemandYes" in states[1]["text"] and "not been started" in states[1]["text"], states[1]
    assert "Attaching" in states[2]["text"], states[2]
    assert "No sessions" in states[3]["text"], states[3]
    assert "Exited" in states[4]["text"] and "exited" in states[4]["text"], states[4]
    # The published state stays on the row; only the PAINT distinguishes idle-by-design from an outage.
    assert [item["rowState"] for item in states] == ["never-started", "never-started", "attaching", "no-sessions", "exited"], states
    assert [item["rowTone"] for item in states] == ["muted", "bad", "warn", "muted", "bad"], states
    assert [item["rowLabel"] for item in states] == ["Never started", "Never started", "Attaching", "No sessions", "Exited"], states
    # An actionable watcher failure is visible in the ONE compact alert without expanding anything.
    assert [item["alerted"] for item in states] == [False, True, False, False, True], states


@pytest.mark.browser
def test_system_status_corruption_recovery_names_quarantine_and_destination_in_one_alert(browser, tmp_path):
    """A recovered corrupt database is visible without inspecting logs or chart history.

    The large per-event banner card became the ONE compact alert slot above the roster, so the size
    assertions moved from "at least 80px tall" to "one alert element, above the roster, carrying
    every named fact". The facts asserted are unchanged.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    banner = run_when_browser_ready(
        browser,
        """
        document.querySelector('[data-js-debug-subtab="system"]')?.click();
        jsDebugSystemState.payload = {
          ok: true,
          generated_at: Date.now() / 1000,
          server: {}, owner: {}, refresh: {}, search_index: {}, caches: {}, client_events: {}, chat: {}, cpu_budget: {},
          top_endpoints: [], top_background_work: [],
          local_services: {
            schema_version: 2,
            inventory: [],
            services: [],
            recovery_events: [{
              subsystem: 'statsd',
              event: 'unreadable_current_database',
              quarantined_artifact: 'stats-v7.sqlite3.corrupt-1785600000',
              quarantined_path: '/fixture/state/stats-v7.sqlite3.corrupt-1785600000',
              destination_path: '/fixture/state/stats-v7.sqlite3',
              reason: 'database disk image is malformed',
            }],
          },
        };
        refreshDebugSystemViews();
        const element = document.querySelector('[data-system-recovery-banner]');
        const slot = document.querySelector('[data-js-debug-system-alert]');
        const roster = document.querySelector('[data-js-debug-roster]');
        const style = element ? getComputedStyle(element) : null;
        return {
          text: element?.textContent?.replace(/\\s+/g, ' ').trim() || '',
          role: slot?.getAttribute('role') || '',
          slots: document.querySelectorAll('[data-js-debug-system-alert]').length,
          aboveRoster: Boolean(slot && roster) && slot.getBoundingClientRect().top < roster.getBoundingClientRect().top,
          visible: style ? style.display !== 'none' && element.getBoundingClientRect().height > 0 : false,
          fontSize: style ? Number.parseFloat(style.fontSize) : 0,
        };
        """,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert banner["role"] == "alert", banner
    assert "statsd" in banner["text"], banner
    assert "unreadable_current_database" in banner["text"], banner
    assert "stats-v7.sqlite3.corrupt-1785600000" in banner["text"], banner
    assert "/fixture/state/stats-v7.sqlite3" in banner["text"], banner
    assert banner["slots"] == 1, banner
    assert banner["aboveRoster"] is True, banner
    assert banner["visible"] is True and banner["fontSize"] >= 11, banner


@pytest.mark.browser
def test_h4_long_rendered_values_wrap_without_overflow_or_truncation(browser, tmp_path):
    """A long backend string wraps inside the roster row and its disclosure, and widens neither.

    This gate used to drive the retired per-cell Local services table's Runtime cell. That view is
    gone -- it was a second renderer for a case the roster already covers -- so the same contract is
    now measured on the surfaces long backend strings actually reach: the row's own status reason,
    and the row's disclosure. The deliberately long token models a service reason or task key that
    carries an identifying path or operation label. The full text must remain available: wrapped,
    never clipped, and never by widening the roster past its scroller.
    """
    long_token = "transcript-reconciliation-for-long-lived-forked-subagent-session"
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    metrics = run_when_browser_ready(
        browser,
        _schema_two_payload_script() + """
        const longToken = arguments[2];
        const payload = jsDebugSystemState.payload;
        const target = payload.local_services.services.find(service => service.id === 'statsd');
        target.reason = `Status transport refused for ${longToken}`;
        target.state = 'issue';
        jsDebugSystemRosterState.expanded = new Set(['statsd']);
        refreshDebugSystemViews();
        const view = document.querySelector('[data-js-debug-system]');
        const table = view.querySelector('[data-js-debug-roster]');
        const wrap = table.closest('.js-debug-system-table-wrap');
        const row = table.querySelector('[data-subsystem-row][data-subsystem-id="statsd"]');
        const reason = row.querySelector('[data-subsystem-reason]');
        const detail = document.getElementById('js-debug-roster-detail-statsd');
        if (!reason || !detail || !detail.childElementCount) return {error: 'missing roster reason or disclosure'};
        const reasonStyle = getComputedStyle(reason);
        return {
          longToken,
          reasonText: reason.textContent,
          detailPresent: detail.childElementCount > 0,
          overflowWrap: reasonStyle.overflowWrap,
          reasonScrollWidth: reason.scrollWidth,
          reasonClientWidth: reason.clientWidth,
          reasonHeight: reason.getBoundingClientRect().height,
          reasonLineHeight: Number.parseFloat(reasonStyle.lineHeight),
          detailScrollWidth: detail.scrollWidth,
          detailClientWidth: detail.clientWidth,
          wrapScrollWidth: wrap.scrollWidth,
          wrapClientWidth: wrap.clientWidth,
          tableRight: table.getBoundingClientRect().right,
          viewRight: view.getBoundingClientRect().right,
        };
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        long_token,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert not metrics.get("error"), metrics
    # Present in full, and WRAPPED rather than clipped: a single line could only hold it by
    # overflowing, so more than one line box is the evidence that it wrapped.
    assert metrics["longToken"] in metrics["reasonText"], metrics
    assert metrics["overflowWrap"] in {"anywhere", "break-word"}, metrics
    assert metrics["reasonHeight"] > metrics["reasonLineHeight"] * 1.5, metrics
    assert metrics["reasonScrollWidth"] <= metrics["reasonClientWidth"] + 1, metrics
    assert metrics["detailPresent"] is True, metrics
    assert metrics["detailScrollWidth"] <= metrics["detailClientWidth"] + 1, metrics
    # And nothing widened to make room for it.
    assert metrics["wrapScrollWidth"] <= metrics["wrapClientWidth"] + 1, metrics
    assert metrics["tableRight"] <= metrics["viewRight"] + 1, metrics


@pytest.mark.browser
def test_roster_fits_desktop_laptop_and_phone_widths_without_hiding_a_value(browser, tmp_path):
    """Service, Status, Latency and Uptime survive every width; secondary metrics move into the row.

    1920x1080 is the wide-desktop class, 1280x800 the desktop class, 1024x768 the laptop/tablet
    class and 390x844 the phone class. A screenshot's exact pixel size is not a device class, so
    none is pinned here.

    The roster responds to the PANEL, not the window -- and it has to, because a dockview pane at a
    1280px window is far narrower than the window. This boot fixture pins its debug pane at 704px at
    every window size, which is measured below and asserted: window sizes alone would therefore
    prove nothing about the column layout, so each device class also sets the container's own inline
    size and the pass/fail line is read from the measured container, exactly as the query is.
    """
    secondary_columns = {"rss_bytes", "cpu_now_percent", "restart_count", "request_count", "error_count"}
    # The container-query breakpoint, in px. Below it the five secondary metrics leave the row.
    # Measured, not chosen: the nine-column table needs 702px of min-content in Chrome, so the old
    # 72rem threshold dropped five columns while ~18rem of panel sat unused. See the comment on the
    # `@container` rule in static_src/css/yolomux/30_preferences_changes.css.
    secondary_breakpoint_px = 48 * 16
    # (window width, window height, container width) -- one row per device class.
    device_classes = ((1920, 1080, 1400), (1280, 800, 1100), (1024, 768, 760), (390, 844, 360))
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    run_when_browser_ready(
        browser,
        _schema_two_payload_script() + "return true;",
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    original = browser.get_window_size()
    widths = set()
    try:
        for width, height, container in device_classes:
            browser.set_window_size(width, height)
            metrics = browser.execute_script(
                """
                const container = arguments[0];
                const view = document.querySelector('[data-js-debug-system]');
                view.style.width = '';
                const paneWidth = view.clientWidth;
                view.style.width = `${container}px`;
                const table = view.querySelector('[data-js-debug-roster]');
                const wrap = table.closest('.js-debug-system-table-wrap');
                const visible = node => {
                  const style = getComputedStyle(node);
                  return style.display !== 'none' && style.visibility !== 'hidden' && node.getBoundingClientRect().width > 0;
                };
                const rows = Array.from(table.querySelectorAll('[data-subsystem-row]'));
                const tableRect = table.getBoundingClientRect();
                const viewRect = view.getBoundingClientRect();
                // Read the columns off a real ROW, not off `thead`. Below 36rem the row stacks into
                // two readable lines and the header is hidden -- its labels move into the cells --
                // so a header-only census would report every column as gone at phone width when in
                // fact every one of them is still on the row. The status cell is `display: contents`
                // at that width, so what is censused is the status BADGE the reader actually sees.
                const census = [
                  ...(visible(rows[0].querySelector('.js-debug-roster-service')) ? ['js-debug-roster-service'] : []),
                  ...(visible(rows[0].querySelector('.js-debug-roster-status')) ? ['js-debug-roster-status'] : []),
                  ...Array.from(rows[0].querySelectorAll('[data-subsystem-column]')).filter(visible)
                    .map(node => node.dataset.subsystemColumn),
                ];
                return {
                  visibleColumns: census,
                  rows: rows.length,
                  // The Daemons scroller is the surface this queue owns. The fixture page around it
                  // is a synthetic multi-pane harness whose own width is not a device class.
                  viewOverflow: view.scrollWidth - view.clientWidth,
                  viewWidth: view.clientWidth,
                  paneWidth,
                  wrapOverflow: wrap.scrollWidth - wrap.clientWidth,
                  tableRight: tableRect.right,
                  viewRight: viewRect.right,
                  clipped: rows.some(row => {
                    const cell = row.querySelector('.js-debug-roster-service');
                    return cell.scrollWidth > cell.clientWidth + 1 || cell.scrollHeight > cell.clientHeight + 1;
                  }),
                  // Cells only overlap if they share a line. Below 36rem the metric cells sit on
                  // the row's SECOND line, directly beneath the service name, which is the layout
                  // -- comparing every cell against its predecessor regardless of line would call
                  // that an overlap.
                  overlaps: rows.some(row => {
                    const cells = Array.from(row.children).filter(visible).map(cell => cell.getBoundingClientRect());
                    return cells.some((rect, index) => cells.some((other, otherIndex) => otherIndex < index
                      && rect.top < other.bottom - 1 && other.top < rect.bottom - 1
                      && rect.left < other.right - 1 && other.left < rect.right - 1));
                  }),
                };
                """,
                container,
            )
            # Why this test sets the container width: the fixture's pane does not follow the window.
            assert metrics["paneWidth"] == 704, (width, metrics)
            assert metrics["rows"] == len(ROSTER_ROW_IDS), (width, metrics)
            for required in ("js-debug-roster-service", "js-debug-roster-status", "latency", "uptime_seconds"):
                assert required in metrics["visibleColumns"], (width, metrics)
            if metrics["viewWidth"] <= secondary_breakpoint_px:
                assert not secondary_columns & set(metrics["visibleColumns"]), (width, metrics)
            else:
                assert secondary_columns <= set(metrics["visibleColumns"]), (width, metrics)
                widths.add("wide")
            assert metrics["viewOverflow"] <= 1, (width, metrics)
            assert metrics["wrapOverflow"] <= 1, (width, metrics)
            assert metrics["tableRight"] <= metrics["viewRight"] + 1, (width, metrics)
            assert metrics["clipped"] is False, (width, metrics)
            assert metrics["overlaps"] is False, (width, metrics)

        # At phone width NO value becomes unreachable -- but the two routes are different, and the
        # difference is the point. Latency and Uptime are `primary` and keep their own columns, so
        # they are read from the row. The five secondary metrics lose their columns here, so their
        # copies inside the disclosure must be present AND actually displayed.
        browser.set_window_size(390, 844)
        detail = browser.execute_script(
            """
            document.querySelector('[data-js-debug-roster-toggle="statsd"]').click();
            const view = document.querySelector('[data-js-debug-system]');
            const node = document.getElementById('js-debug-roster-detail-statsd');
            const dropped = node ? node.querySelector('[data-subsystem-dropped-metrics]') : null;
            const table = view.querySelector('[data-js-debug-roster]');
            const visible = el => {
              const style = getComputedStyle(el);
              return style.display !== 'none' && style.visibility !== 'hidden' && el.getBoundingClientRect().width > 0;
            };
            return {
              text: node ? node.textContent.replace(/\\s+/g, ' ') : '',
              droppedShown: dropped ? visible(dropped) : false,
              droppedLabels: dropped ? Array.from(dropped.querySelectorAll('dt')).map(n => n.textContent.trim()) : [],
              visibleColumns: Array.from(table.querySelectorAll('[data-subsystem-row] [data-subsystem-column]')).filter(visible)
                .map(n => n.dataset.subsystemColumn),
              viewOverflow: view.scrollWidth - view.clientWidth,
            };
            """
        )
        assert detail["droppedShown"] is True, detail
        assert detail["droppedLabels"] == ["Memory", "CPU", "Restarts", "Requests", "Errors"], detail
        # The two that never drop are read from the row itself, not from a copy beneath it.
        for column in ("latency", "uptime_seconds"):
            assert column in detail["visibleColumns"], (column, detail)
        assert "Latency avg / max" not in detail["text"], detail
        assert "Uptime" not in detail["text"], detail
        assert detail["viewOverflow"] <= 1, detail
        # At least one measured width must actually be wide enough to show every column, or this
        # test only ever proved the compact layout.
        assert "wide" in widths, widths
    finally:
        browser.set_window_size(original["width"], original["height"])


@pytest.mark.browser
def test_the_roster_row_is_two_readable_lines_at_phone_width(browser, tmp_path):
    """At a 390px container a service row is TWO line boxes, and its explanation is a whole sentence.

    What this measures, and why each number is here. Before the stacked layout the narrow roster was
    a four-column fixed table whose columns measured 140px / 83px / 83px / 83px at a 390px
    container. Measured in Chrome against that CSS:
      * a row with no status explanation was ONE line box, not the promised two;
      * `tmux-signal-watcher` was FOUR, because "Control client is attached" was folded into the
        83px Status column as `Control` / `client is` / `attached`;
      * no number on the second line carried a name, because the only labels were in a header row
        three services further up.
    So the assertions below are line boxes, not heights: the service name and the status badge share
    line one, every surviving metric sits on line two BESIDE ITS OWN NAME, and an explanation is one
    unbroken sentence on a line of its own. `document.createRange()` per character is what makes a
    line box measurable at all -- an element height cannot tell two wrapped lines from one tall one.
    """
    line_boxes_script = """
        const width = arguments[0];
        const view = document.querySelector('[data-js-debug-system]');
        view.style.width = `${width}px`;
        const table = view.querySelector('[data-js-debug-roster]');
        const shown = node => {
          if (!node) return false;
          const style = getComputedStyle(node);
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          return style.display === 'contents' || node.getBoundingClientRect().width > 0;
        };
        // The rendered LINE BOXES of a subtree: one entry per painted line, in visual order, with
        // the text that actually landed on it. Characters whose boxes share a top (within 3px, for
        // baseline-aligned inline-flex badges) are one line.
        const lineBoxes = root => {
          const lines = [];
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walker.nextNode())) {
            if (!node.nodeValue.trim()) continue;
            const range = document.createRange();
            for (let index = 0; index < node.nodeValue.length; index += 1) {
              range.setStart(node, index);
              range.setEnd(node, index + 1);
              const rect = range.getClientRects()[0];
              if (!rect || rect.width === 0) continue;
              const last = lines[lines.length - 1];
              if (last && Math.abs(last.top - rect.top) <= 3) last.text += node.nodeValue[index];
              else lines.push({top: rect.top, text: node.nodeValue[index]});
            }
          }
          return lines.sort((first, second) => first.top - second.top).map(line => line.text.trim());
        };
        const rows = Array.from(table.querySelectorAll('[data-subsystem-row]')).map(row => {
          const reasonNode = row.querySelector('[data-subsystem-reason]');
          return {
            id: row.dataset.subsystemId,
            reason: shown(reasonNode) ? reasonNode.textContent.trim() : '',
            lines: lineBoxes(row),
            columns: Array.from(row.querySelectorAll('[data-subsystem-column]')).filter(shown)
              .map(cell => cell.dataset.subsystemColumn),
            labelsShown: Array.from(row.querySelectorAll('.js-debug-roster-celllabel')).filter(shown)
              .map(label => label.textContent.trim()),
          };
        });
        return {
          rows,
          headerShown: shown(table.querySelector('thead')),
          viewOverflow: view.scrollWidth - view.clientWidth,
          wrapOverflow: (() => {
            const wrap = table.closest('.js-debug-system-table-wrap');
            return wrap.scrollWidth - wrap.clientWidth;
          })(),
          // Table semantics do not survive `display: block`, so the roster carries explicit roles.
          roles: {
            table: table.getAttribute('role'),
            row: table.querySelector('[data-subsystem-row]').getAttribute('role'),
            rowheader: table.querySelector('[data-subsystem-row] .js-debug-roster-service').getAttribute('role'),
            cell: table.querySelector('[data-subsystem-row] [data-subsystem-column]').getAttribute('role'),
            columnheader: table.querySelector('thead .js-debug-roster-service').getAttribute('role'),
          },
        };
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    run_when_browser_ready(
        browser,
        _schema_two_payload_script() + "return true;",
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    original = browser.get_window_size()
    try:
        browser.set_window_size(390, 844)
        phone = browser.execute_script(line_boxes_script, 390)
        assert phone["viewOverflow"] <= 1, phone
        assert phone["wrapOverflow"] <= 1, phone
        assert phone["headerShown"] is False, "a stacked row cannot be labelled by a header row above it"
        assert phone["roles"] == {
            "table": "table", "row": "row", "rowheader": "rowheader",
            "cell": "cell", "columnheader": "columnheader",
        }, phone["roles"]
        assert [row["id"] for row in phone["rows"]] == list(ROSTER_ROW_IDS), phone["rows"]
        explained = 0
        for row in phone["rows"]:
            # Line 1 is identity plus state; line 2 is every surviving metric. A row with an
            # explanation gets a third line, and it holds the WHOLE sentence -- that third line is
            # what the 83px Status column used to shred into three.
            expected_lines = 3 if row["reason"] else 2
            assert len(row["lines"]) == expected_lines, (row["id"], row["lines"])
            assert row["columns"] == ["latency", "uptime_seconds"], (row["id"], row["columns"])
            # Every metric on line two carries its own name, because the header is gone.
            assert row["labelsShown"] == ["Latency avg / max", "Uptime"], (row["id"], row["labelsShown"])
            for label in row["labelsShown"]:
                assert label in row["lines"][1], (row["id"], label, row["lines"])
            if row["reason"]:
                explained += 1
                assert row["lines"][2] == row["reason"], (row["id"], row["lines"])
        # The fixture must actually contain explained rows, or the sentence assertion proved nothing.
        assert explained >= 2, phone["rows"]

        # The mirror image at desktop width: the header labels the columns, so the in-cell copies of
        # those same labels are not rendered. One label, one position, at every width.
        browser.set_window_size(1600, 900)
        desktop = browser.execute_script(line_boxes_script, 1400)
        assert desktop["headerShown"] is True, desktop
        for row in desktop["rows"]:
            assert row["labelsShown"] == [], (row["id"], row["labelsShown"])
            assert len(row["columns"]) == 7, (row["id"], row["columns"])
    finally:
        browser.set_window_size(original["width"], original["height"])


@pytest.mark.browser
def test_the_summary_strip_is_the_one_pinned_layer_and_it_really_pins(browser, tmp_path):
    """ONE sticky layer, and it actually sticks. No second layer, so no offset to keep in sync.

    Three measured defects sat behind the old `--js-debug-roster-header-offset: 2.1rem`:
      * 2.1rem (33.6px) was a remembered copy of the summary strip's height, which measures 32px
        on one line and 94px once it wraps -- and a longer locale wraps it where English does not;
      * the strip was never actually sticky. It lives in a region wrapper whose box is exactly its
        own height, and a sticky element only holds while its containing block is in view, so at a
        320px pane its bottom edge sat at -10px while the header held a 33.6px gap open for it;
      * the thead's nearest scrollport is `.js-debug-roster-wrap` (its `overflow-x: hidden` makes
        one), not the panel, so the offset could never measure the distance it claimed to.

    So the coupling is deleted rather than measured. This asserts what replaced it: the summary
    pins to the top of the scroller at every width, and nothing is pinned on top of it.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    _open_daemons_with_frozen_poll(browser)
    run_when_browser_ready(
        browser,
        _schema_two_payload_script() + "return true;",
        list(SYSTEM_STATUS_SERVICE_IDS),
        DEFAULT_ROSTER_STATES,
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=('[data-js-debug-subtab="system"]',),
    )
    measurements = []
    for container in (1400, 1000, 700, 520, 360):
        measurements.append(
            browser.execute_script(
                """
                const container = arguments[0];
                const view = document.querySelector('[data-js-debug-system]');
                view.style.width = `${container}px`;
                // A SHORT pane, which is what a dockview pane usually is: sticky only means
                // anything in a scroller that actually scrolls.
                view.style.height = '320px';
                view.querySelector('[data-js-debug-roster-toggle="statsd"]')?.click();
                const summary = view.querySelector('[data-js-debug-roster-summary]');
                const region = view.querySelector('[data-js-debug-system-region="summary"]');
                const table = view.querySelector('[data-js-debug-roster]');
                const head = table.querySelector('thead th');
                view.scrollTop = 0;
                void view.scrollTop;
                view.scrollTop = view.scrollHeight;
                void view.scrollTop;
                const v = view.getBoundingClientRect();
                const s = summary.getBoundingClientRect();
                return {
                  container,
                  scrollTop: view.scrollTop,
                  summaryHeight: Math.round(s.height * 10) / 10,
                  // How far the pinned strip sits from the top of its scroller. A strip that
                  // scrolled away goes sharply negative; a pinned one stays at ~0.
                  summaryOffsetFromScrollerTop: Math.round((s.top - v.top) * 10) / 10,
                  regionPosition: getComputedStyle(region).position,
                  headerPosition: getComputedStyle(head).position,
                  declaredOffset: getComputedStyle(view).getPropertyValue('--js-debug-roster-header-offset').trim(),
                };
                """,
                container,
            )
        )

    for metrics in measurements:
        # The one pinned layer is the region, and it is genuinely pinned.
        assert metrics["regionPosition"] == "sticky", metrics
        # NEGATIVE CONTROL on the real defect: before this, the strip scrolled off to -10px and
        # beyond while claiming to be sticky. Pinned means it stays at the scroller's top edge.
        if metrics["scrollTop"] > 0:
            assert -1 <= metrics["summaryOffsetFromScrollerTop"] <= 1, (
                "the summary strip declares position:sticky but scrolled away",
                metrics,
                measurements,
            )
        # No second pinned layer, and no remembered offset left to drift.
        assert metrics["headerPosition"] != "sticky", metrics
        assert metrics["declaredOffset"] == "", metrics

    # The strip really does change height across these widths, so this exercises the wrap that
    # made a single hardcoded offset wrong rather than measuring one geometry five times.
    assert len({m["summaryHeight"] for m in measurements}) > 1, measurements


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 SubsystemSpec Preferences controls")
def test_h5_preferences_toggle_changes_its_subsystem_row_without_reload(browser, tmp_path):
    """Toggling a Preferences subsystem updates that row in place without a navigation reload."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const row = document.querySelector('[data-subsystem-row][data-subsystem-state="running"]');
        const toggle = row?.querySelector('[data-subsystem-toggle]');
        const navigationCount = performance.getEntriesByType('navigation').length;
        if (!row || !toggle) return done({error: 'missing running subsystem toggle'});
        const observer = new MutationObserver(() => {
          if (row.dataset.subsystemState === 'paused') {
            observer.disconnect();
            done({state: row.dataset.subsystemState, navigationCount, afterNavigationCount: performance.getEntriesByType('navigation').length});
          }
        });
        observer.observe(row, {attributes: true, attributeFilter: ['data-subsystem-state']});
        toggle.click();
        setTimeout(() => { observer.disconnect(); done({state: row.dataset.subsystemState, navigationCount, afterNavigationCount: performance.getEntriesByType('navigation').length, timedOut: true}); }, 750);
        """,
    )
    assert result["state"] == "paused" and not result.get("timedOut"), result
    assert result["afterNavigationCount"] == result["navigationCount"], result


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 SubsystemSpec subsystem recovery")
def test_h7_paused_subsystem_recovers_from_its_rendered_ui_control(browser, tmp_path):
    """A paused subsystem resumes from its row control and changes state without YAML edits."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const row = document.querySelector('[data-subsystem-row][data-subsystem-state="paused"]');
        const resume = row?.querySelector('[data-subsystem-resume]');
        if (!row || !resume) return done({error: 'missing paused subsystem recovery control'});
        const observer = new MutationObserver(() => {
          if (row.dataset.subsystemState === 'running') {
            observer.disconnect();
            done({state: row.dataset.subsystemState});
          }
        });
        observer.observe(row, {attributes: true, attributeFilter: ['data-subsystem-state']});
        resume.click();
        setTimeout(() => { observer.disconnect(); done({state: row.dataset.subsystemState, timedOut: true}); }, 750);
        """,
    )
    assert result["state"] == "running" and not result.get("timedOut"), result


@pytest.mark.browser
@pytest.mark.xfail(strict=True, reason="NOT-APPLICABLE on v0.6.10; waits for F9 SubsystemSpec feature ordering")
def test_h8_preferences_and_tabs_dropdown_share_feature_order(browser, tmp_path):
    """Preferences and the Tabs dropdown render the identical SubsystemSpec feature order."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    order = browser.execute_script(
        """
        const values = selector => Array.from(document.querySelectorAll(selector))
          .map(node => node.dataset.subsystemFeature || '')
          .filter(Boolean);
        return {
          preferences: values('[data-preferences-subsystem-feature]'),
          tabs: values('[data-tabs-subsystem-feature]'),
        };
        """,
    )
    assert order["preferences"] and order["tabs"] and order["preferences"] == order["tabs"], order


@pytest.mark.browser
def test_h6_clearing_preferences_search_restores_every_section(browser, tmp_path):
    """Filtering Preferences then clearing its rendered search input restores the original complete section list."""
    load_live_runtime_boot_fixture(browser, tmp_path)
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        selectSession('__prefs__').then(
          () => requestAnimationFrame(() => done({ok: true})),
          error => done({ok: false, error: String(error)})
        );
        """
    )
    assert opened["ok"], opened
    metrics = run_when_browser_ready(
        browser,
        """
        const search = document.querySelector('[data-preferences-search]');
        const sectionIds = () => Array.from(document.querySelectorAll('[data-preference-section]')).map(node => node.dataset.preferenceSection);
        const before = sectionIds();
        search.value = 'appearance';
        search.dispatchEvent(new Event('input', {bubbles: true}));
        const filtered = sectionIds();
        const clearedSearch = document.querySelector('[data-preferences-search]');
        clearedSearch.value = '';
        clearedSearch.dispatchEvent(new Event('input', {bubbles: true}));
        return {before, filtered, after: sectionIds()};
        """,
        globals_required={"selectSession": "function", "renderPreferencesPanels": "function"},
        dom_anchors=("[data-preferences-search]", ".preferences-sections"),
    )
    assert metrics["filtered"] != metrics["before"], metrics
    assert metrics["after"] == metrics["before"], metrics

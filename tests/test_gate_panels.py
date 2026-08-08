"""Gate H: rendered subsystem panels and their HTTP status data."""

import pytest

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import run_when_browser_ready
from yolomux_lib import app as app_module


SYSTEM_STATUS_SERVICE_IDS = ("indexd", "statsd", "jobd", "statusd", "watchd", "approvald")
SYSTEM_STATUS_METRIC_KEYS = ("cpu_now_percent", "rss_bytes", "uptime_seconds")


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
    assert local_services["schema_version"] == 1, local_services
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
def test_h3_all_eleven_subsystem_rows_remain_rendered(browser, tmp_path):
    """The historical node now pins five honest process rows, including idle and unavailable services."""
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    rows = run_when_browser_ready(
        browser,
        """
        const inventory = arguments[0];
        const measured = value => ({state: 'measured', value, reason_code: '', reason: ''});
        const absent = (state, reasonCode, reason) => ({state, value: null, reason_code: reasonCode, reason});
        jsDebugSystemState.payload = {
          ok: true,
          generated_at: Date.now() / 1000,
          server: {}, owner: {}, refresh: {}, search_index: {}, caches: {}, client_events: {}, chat: {}, cpu_budget: {},
          top_endpoints: [], top_background_work: [],
          local_services: {
            schema_version: 1,
            inventory,
            totals: {},
            services: inventory.map((id, index) => ({
              id,
              label: id,
              state: index === 3 ? 'unavailable' : (index === 4 ? 'idle' : 'running'),
              reason_code: index === 3 ? 'transport_failed' : (index === 4 ? 'not_started' : ''),
              reason: index === 3 ? 'Status transport failed' : (index === 4 ? 'Starts on demand' : ''),
              pid: index < 3 ? 4200 + index : 0,
              started_at: index < 3 ? Date.now() / 1000 - 120 : 0,
              metrics: {
                cpu_now_percent: index < 3 ? measured(index + 1) : absent(index === 3 ? 'unavailable' : 'not_running', index === 3 ? 'transport_failed' : 'not_started', index === 3 ? 'Status transport failed' : 'Service is not running'),
                rss_bytes: index < 3 ? measured((index + 1) * 1048576) : absent(index === 3 ? 'unavailable' : 'not_running', index === 3 ? 'transport_failed' : 'not_started', index === 3 ? 'Status transport failed' : 'Service is not running'),
                uptime_seconds: index < 3 ? measured(120 + index) : absent(index === 3 ? 'unavailable' : 'not_running', index === 3 ? 'transport_failed' : 'not_started', index === 3 ? 'Status transport failed' : 'Service is not running'),
              },
              details: {},
            })),
          },
        };
        refreshDebugSystemViews();
        return Array.from(document.querySelectorAll('[data-subsystem-row]')).map(row => ({
          id: row.dataset.subsystemId || '',
          state: row.dataset.subsystemState || '',
          reason: row.querySelector('[data-subsystem-reason]')?.textContent?.trim() || '',
          metrics: Object.fromEntries(Array.from(row.querySelectorAll('[data-subsystem-metric]')).map(cell => [cell.dataset.subsystemMetric, cell.textContent.trim()])),
        }));
        """,
        list(SYSTEM_STATUS_SERVICE_IDS),
        globals_required={"refreshDebugSystemViews": "function"},
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert tuple(row["id"] for row in rows) == SYSTEM_STATUS_SERVICE_IDS, rows
    assert all(set(row["metrics"]) == set(SYSTEM_STATUS_METRIC_KEYS) for row in rows), rows
    assert rows[3]["state"] == "unavailable" and rows[3]["reason"], rows
    assert rows[4]["state"] == "idle" and rows[4]["reason"], rows


@pytest.mark.browser
def test_system_panel_reports_each_typed_tmux_signal_watcher_state(browser, tmp_path):
    """The System view must not collapse absent, attaching, idle, and exited watchers into one Boolean."""
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
            top_endpoints: [], top_background_work: [], local_services: {totals: {}, services: []}, tmux_signal_watcher: watcher,
          };
          refreshDebugSystemViews();
          const card = document.querySelector('[data-js-debug-tmux-signal-watcher]');
          return {
            state: card?.dataset.tmuxSignalWatcherState || '',
            demanded: card?.dataset.tmuxSignalWatcherDemanded || '',
            role: card?.getAttribute('role') || '',
            text: card?.textContent?.replace(/\\s+/g, ' ').trim() || '',
          };
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


@pytest.mark.browser
def test_system_status_corruption_recovery_banner_names_quarantine_and_destination(browser, tmp_path):
    """A recovered corrupt database is visible without inspecting logs or chart history."""
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
            schema_version: 1,
            inventory: ['indexd', 'statsd', 'jobd', 'statusd', 'watchd', 'approvald'],
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
        const rect = element?.getBoundingClientRect();
        const style = element ? getComputedStyle(element) : null;
        return {
          text: element?.textContent?.replace(/\\s+/g, ' ').trim() || '',
          role: element?.getAttribute('role') || '',
          height: rect?.height || 0,
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
    assert banner["height"] >= 80 and banner["fontSize"] >= 16, banner


@pytest.mark.browser
def test_h4_long_rendered_values_wrap_without_overflow_or_truncation(browser, tmp_path):
    """Runtime wraps product work inside a fixed Local Services cell without widening its card.

    This uses the System panel's normal payload-to-DOM path.  The deliberately long
    task key models a service name that carries an identifying path or operation
    label; the full text must remain available after the narrow fixed-table layout
    wraps it rather than forcing its containing debug card wider.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    metrics = run_when_browser_ready(
        browser,
        """
        const expectedToken = 'transcript-reconciliation-for-long-lived-forked-subagent-session';
        document.querySelector('[data-js-debug-subtab="system"]')?.click();
        jsDebugSystemState.payload = {
          ok: true,
          generated_at: Date.now() / 1000,
          server: {}, owner: {}, refresh: {}, search_index: {}, caches: {},
          client_events: {}, chat: {}, cpu_budget: {}, top_endpoints: [], top_background_work: [],
          local_services: {totals: {}, services: [{
            service: 'statsd', pid: 4242, started_at: Date.now() / 1000 - 120,
            uptime_seconds: 120, resources: {cpu_percent: 12.5, rss_bytes: 104857600},
            product_runtime_ms: {
              [expectedToken]: {avg_ms: 12, max_ms: 34, count: 56},
            },
            product_work_totals: {
              [expectedToken]: {completed: 56, rejected: 0},
            },
          }]},
        };
        refreshDebugSystemViews();
        const card = document.querySelector('[data-js-debug-local-services-card]');
        const parent = card?.parentElement;
        const wrap = card?.querySelector('.js-debug-system-local-services-wrap');
        const cell = card?.querySelector('[data-js-debug-service-cell][data-field="runtime"]');
        if (!card || !parent || !wrap || !cell) return {error: 'missing Local Services Runtime cell'};
        card.style.width = '602px';
        const cardRect = card.getBoundingClientRect();
        const parentRect = parent.getBoundingClientRect();
        const wrapRect = wrap.getBoundingClientRect();
        const cellRect = cell.getBoundingClientRect();
        const style = getComputedStyle(cell);
        return {
          text: cell.textContent,
          expectedToken,
          whiteSpace: style.whiteSpace,
          overflowWrap: style.overflowWrap,
          cellScrollWidth: cell.scrollWidth,
          cellClientWidth: cell.clientWidth,
          cellHeight: cellRect.height,
          wrapWidth: wrapRect.width,
          wrapScrollWidth: wrap.scrollWidth,
          cardRight: cardRect.right,
          parentRight: parentRect.right,
        };
        """,
        globals_required={
            "refreshDebugSystemViews": "function",
        },
        dom_anchors=("[data-js-debug-subtab=\"system\"]",),
    )
    assert not metrics.get("error"), metrics
    assert metrics["expectedToken"] in metrics["text"], metrics
    assert metrics["whiteSpace"] == "pre-wrap" and metrics["overflowWrap"] == "anywhere", metrics
    assert metrics["cellScrollWidth"] <= metrics["cellClientWidth"] + 1, metrics
    assert metrics["wrapScrollWidth"] <= metrics["wrapWidth"] + 1, metrics
    assert metrics["cardRight"] <= metrics["parentRight"] + 1, metrics


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

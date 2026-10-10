# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Section G regression gates for rendered stats, cost, and materializer health."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import load_static_html_fixture
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import assert_browser_journey_error_free
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import load_gate_browser
from tests.gate_harness import open_gate_stats_surface
from tests.gate_harness import run_fixture_cleanup_phases
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.helpers.browser_stats_coverage import _current_stats_fixture_html, _start_current_stats, _write_current_stats_fixture_assets
from tests.helpers.gate_stats import NOW, _seed_realistic_stats
from tests.helpers.gate_stats import stop_stats_current_service
from tools.mockers.transcript import append_record as _append_record
from tools.mockers.transcript import codex_meta as _codex_meta
from tools.mockers.transcript import codex_usage as _codex_usage
from tools.mockers.transcript import write_records as _write_records
from tests.helpers.gate_stats import commit_scan as _commit
from yomux_lib import server as server_module
from yomux_lib import app as app_module
from yomux_lib import web as web_module
from yomux_lib.client_events import ClientEventBroker
from yomux_lib.stats_current import client as stats_client
from yomux_lib.stats_current import http as stats_http
from yomux_lib.stats_current import pricing
from yomux_lib.stats_current import service as stats_service
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yomux_lib.stats_current.usage import usage_atom_from_source
from yomux_lib.workspace import settings as workspace_settings


REALISTIC_ROW_COUNT = 8_929
REPAIR_BACKLOG_RECORDS = 240


def _stats_snapshot_ready(response) -> bool:
    if response.status == 200:
        return True
    assert response.status == 202, response.status
    payload = response.json()
    assert isinstance(payload, dict), payload
    assert payload.get("status") == "pending", payload
    retry_after_seconds = payload.get("retry_after_seconds")
    assert type(retry_after_seconds) is int, payload
    assert 1 <= retry_after_seconds <= 60, payload
    return False


@pytest.mark.browser
def test_g0_stats_snapshot_readiness_accepts_only_ready_or_bounded_pending():
    assert _stats_snapshot_ready(SimpleNamespace(status=200, json=lambda: None)) is True
    pending = SimpleNamespace(
        status=202,
        json=lambda: {"status": "pending", "retry_after_seconds": 1},
    )
    assert _stats_snapshot_ready(pending) is False
    invalid = (
        SimpleNamespace(status=409, json=lambda: {}),
        SimpleNamespace(status=202, json=lambda: {"status": "queued", "retry_after_seconds": 1}),
        SimpleNamespace(status=202, json=lambda: {"status": "pending", "retry_after_seconds": 0}),
        SimpleNamespace(status=202, json=lambda: {"status": "pending", "retry_after_seconds": 61}),
    )
    for response in invalid:
        with pytest.raises(AssertionError):
            _stats_snapshot_ready(response)


@pytest.fixture
def copy_feedback_stats_runtime(monkeypatch, gate_runtime_paths):
    """Keep copy journeys out of the unrelated status-generation service path."""

    settings_path = gate_runtime_paths.config_dir / "settings.yaml"
    assert workspace_settings.SETTINGS_PATH == settings_path
    workspace_settings.save_settings({"general": {"startup_tips": False}}, settings_path)

    def explicit_settings_payload():
        return workspace_settings.settings_payload(settings_path)

    monkeypatch.setattr(app_module, "settings_payload", explicit_settings_payload)
    monkeypatch.setattr(web_module, "settings_payload", explicit_settings_payload)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "start_status_generation_watcher", lambda self, record: False)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_g0_logs_copy_confirms_first_press_and_repeats_after_revert(browser, copy_feedback_stats_runtime, gate_live_server):
    """One real Logs-button activation copies its text and shows transient confirmation."""

    def current_stats_ready(_driver):
        response = gate_http_request(
            gate_live_server,
            "/api/stats-snapshot?range_seconds=900&resolution=AUTO&client_id=stats-copy-gate",
        )
        return _stats_snapshot_ready(response)

    assert WebDriverWait(browser, 12, poll_frequency=0.1).until(current_stats_ready) is True
    load_gate_browser(browser, gate_live_server)
    startup_state = browser.execute_script(
        """
        return {
          enabled: startupHelpersEnabled,
          configured: clientSettings?.general?.startup_tips,
          defaultValue: clientSettingsDefaults?.general?.startup_tips,
          storage: Object.fromEntries(Object.entries(localStorage)),
        };
        """
    )
    assert startup_state["enabled"] is False, startup_state
    assert browser.execute_script("return showStartupHelperTip({manual: true}) === null;") is True
    opened = open_gate_stats_surface(browser)
    assert opened["visible"] is True, opened
    browser.find_element(By.CSS_SELECTOR, '[data-js-debug-subtab="logs"]').click()
    settled = WebDriverWait(browser, 8).until(
        lambda current: current.execute_script(
            """
            return jsDebugLogsState.inFlight === false
              && document.querySelector('[data-js-debug-logs-copy]')?.isConnected === true;
            """
        )
    )
    assert settled is True
    browser.execute_cdp_cmd(
        "Browser.grantPermissions",
        {
            "origin": gate_live_server.base_url,
            "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
        },
    )
    measurement = browser.execute_script(
        """
        jsDebugLogsState.levels.add('info');
        recordJsDebugEvent('api', {method: 'GET', url: '/fixture/stats-log-copy-first-marker', status: 200});
        refreshDebugLogsViews();
        const button = document.querySelector('[data-js-debug-logs-copy]');
            const events = [];
            for (const type of ['pointerdown', 'click']) {
              button.addEventListener(type, () => {
                if (type === 'click') window.__statsLogsCopyMeasurement.textAtClick = debugLogsTextForClipboard();
                events.push({
                  type,
                  label: button.textContent.trim(),
                  focused: document.activeElement === button,
                  connected: button.isConnected,
                });
              }, {capture: true});
        }
        window.__statsLogsCopyMeasurement = {button, events};
        return {
          label: button.textContent.trim(),
          text: debugLogsTextForClipboard(),
          status: document.getElementById('status')?.textContent?.trim() || '',
        };
        """
    )
    assert "stats-log-copy-first-marker" in measurement["text"], measurement
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        navigator.clipboard.writeText('clipboard-before-first-press').then(() => done({ok: true}), error => done({error: String(error)}));
        """
    )

    button = browser.find_element(By.CSS_SELECTOR, '[data-js-debug-logs-copy]')
    button.click()
    copied_text = browser.execute_script("return window.__statsLogsCopyMeasurement.textAtClick;")
    assert "stats-log-copy-first-marker" in copied_text, copied_text
    first = browser.execute_async_script(
        """
        const expected = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
              const clipboard = await window.__yomuxTestWaitFor(async () => {
                const text = await navigator.clipboard.readText();
                return text === expected ? text : '';
              }, {timeoutMs: 3000, intervalMs: 10, description: 'first Logs clipboard write'});
              refreshDebugLogsViews();
              await window.__yomuxTestHelpers.settle(2);
          const measured = window.__statsLogsCopyMeasurement;
          done({
            clipboard,
            events: measured.events,
            label: measured.button.textContent.trim(),
            focused: document.activeElement === measured.button,
            connected: measured.button.isConnected,
            status: document.getElementById('status')?.textContent?.trim() || '',
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """,
        copied_text,
    )
    assert first.get("error") is None, first
    assert first["clipboard"] == copied_text, first
    assert [event["type"] for event in first["events"]] == ["pointerdown", "click"], first
    assert first["focused"] is True and first["connected"] is True, first
    assert first["label"] == "Copied", first

    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        navigator.clipboard.writeText('clipboard-before-second-press').then(() => done({ok: true}), error => done({error: String(error)}));
        """
    )

    repeated = WebDriverWait(browser, 4).until(
        lambda current: current.execute_script(
            """
            const button = document.querySelector('[data-js-debug-logs-copy]');
            if (button?.textContent?.trim() !== 'Copy') return false;
            const text = debugLogsTextForClipboard();
            button.click();
            return {clicked: true, text};
            """
        )
    )
    assert repeated["clicked"] is True
    assert "stats-log-copy-first-marker" in repeated["text"], repeated
    second = browser.execute_async_script(
        """
        const expected = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const clipboard = await window.__yomuxTestWaitFor(async () => {
            const text = await navigator.clipboard.readText();
            return text === expected ? text : '';
          }, {timeoutMs: 3000, intervalMs: 10, description: 'second Logs clipboard write'});
          const button = document.querySelector('[data-js-debug-logs-copy]');
          done({clipboard, label: button?.textContent?.trim() || ''});
        })().catch(error => done({error: String(error?.stack || error)}));
        """,
        repeated["text"],
    )
    assert second == {"clipboard": repeated["text"], "label": "Copied"}, second
    js_errors = assert_browser_journey_error_free(browser, claimed_clean_surfaces=("stats",))
    assert js_errors["jsDebugErrors"] == [], js_errors
    assert js_errors["browserLocalFailures"] == [], js_errors
    assert js_errors["serverLogErrors"] == [], js_errors
    assert js_errors["severeBrowserLogEntries"] == [], js_errors


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_g0_api_sse_copy_confirms_across_rerender_and_repeats(browser, copy_feedback_stats_runtime, gate_live_server):
    """The API/SSE control keeps its first-press confirmation through a panel render."""

    def current_stats_ready(_driver):
        response = gate_http_request(
            gate_live_server,
            "/api/stats-snapshot?range_seconds=900&resolution=AUTO&client_id=stats-api-copy-gate",
        )
        return _stats_snapshot_ready(response)

    assert WebDriverWait(browser, 12, poll_frequency=0.1).until(current_stats_ready) is True
    load_gate_browser(browser, gate_live_server)
    browser.execute_script(
        """
        window.__gateStatsMenuRerender = setInterval(() => {
          if (document.querySelector('.app-menu.open')) {
            clearInterval(window.__gateStatsMenuRerender);
            window.__gateStatsMenuRerender = null;
            return;
          }
          renderSessionButtons({force: true});
        }, 0);
        """
    )
    try:
        opened = open_gate_stats_surface(browser)
    finally:
        browser.execute_script(
            "clearInterval(window.__gateStatsMenuRerender); window.__gateStatsMenuRerender = null;"
        )
    assert opened["visible"] is True, opened
    browser.find_element(By.CSS_SELECTOR, '[data-js-debug-subtab="events"]').click()
    assert WebDriverWait(browser, 8).until(
        lambda current: current.execute_script(
            "return document.querySelector('[data-js-debug-copy]')?.isConnected === true"
        )
    ) is True
    browser.execute_cdp_cmd(
        "Browser.grantPermissions",
        {
            "origin": gate_live_server.base_url,
            "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
        },
    )
    measurement = browser.execute_script(
        """
        recordJsDebugEvent('api', {method: 'GET', url: '/fixture/stats-api-copy-rerender-marker', status: 200});
        const button = document.querySelector('[data-js-debug-copy]');
        return {text: jsDebugTextForClipboard(), label: button?.textContent.trim() || '', aria: button?.getAttribute('aria-label') || ''};
        """
    )
    assert "stats-api-copy-rerender-marker" in measurement["text"], measurement
    assert measurement["label"] == "Copy", measurement

    primed = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        navigator.clipboard.writeText('clipboard-before-api-first-press').then(() => done({ok: true}), error => done({error: String(error)}));
        """
    )
    assert primed == {"ok": True}, primed
    browser.find_element(By.CSS_SELECTOR, '[data-js-debug-copy]').click()
    first = browser.execute_async_script(
        """
        const expected = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const clipboard = await window.__yomuxTestWaitFor(async () => {
            const text = await navigator.clipboard.readText();
            return text.includes(expected) ? text : '';
          }, {timeoutMs: 3000, intervalMs: 10, description: 'first API/SSE clipboard write'});
          document.querySelector('[data-js-debug-subtab="logs"]').click();
          document.querySelector('[data-js-debug-subtab="events"]').click();
          const button = await window.__yomuxTestWaitFor(() => {
            const current = document.querySelector('[data-js-debug-copy]');
            return current?.textContent.trim() === 'Copied' && current.getAttribute('aria-label') === 'Copied' ? current : null;
          }, {timeoutMs: 3000, intervalMs: 10, description: 'API/SSE copied feedback after rerender'});
          done({clipboard, label: button.textContent.trim(), aria: button.getAttribute('aria-label')});
        })().catch(error => done({error: String(error?.stack || error)}));
        """,
        "stats-api-copy-rerender-marker",
    )
    assert "stats-api-copy-rerender-marker" in first["clipboard"], first
    assert first["label"] == "Copied" and first["aria"] == "Copied", first

    repeated = WebDriverWait(browser, 4).until(
        lambda current: current.execute_script(
            """
            const button = document.querySelector('[data-js-debug-copy]');
            if (button?.textContent?.trim() !== 'Copy' || button.getAttribute('aria-label') !== 'Copy') return false;
            const text = jsDebugTextForClipboard();
            return {text};
            """
        )
    )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        navigator.clipboard.writeText('clipboard-before-api-second-press').then(() => done({ok: true}), error => done({error: String(error)}));
        """
    )
    browser.execute_script("document.querySelector('[data-js-debug-copy]').click();")
    second = browser.execute_async_script(
        """
        const expected = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const clipboard = await window.__yomuxTestWaitFor(async () => {
            const text = await navigator.clipboard.readText();
            return text.includes(expected) ? text : '';
          }, {timeoutMs: 3000, intervalMs: 10, description: 'second API/SSE clipboard write'});
          const button = document.querySelector('[data-js-debug-copy]');
          done({clipboard, label: button?.textContent.trim() || '', aria: button?.getAttribute('aria-label') || ''});
        })().catch(error => done({error: String(error?.stack || error)}));
        """,
        "stats-api-copy-rerender-marker",
    )
    assert "stats-api-copy-rerender-marker" in second["clipboard"], second
    assert second["label"] == "Copied" and second["aria"] == "Copied", second
    js_errors = assert_browser_journey_error_free(browser, claimed_clean_surfaces=("stats",))
    assert js_errors["jsDebugErrors"] == [], js_errors
    assert js_errors["browserLocalFailures"] == [], js_errors
    assert js_errors["serverLogErrors"] == [], js_errors
    assert js_errors["severeBrowserLogEntries"] == [], js_errors


def _live_usage_while_repair_backlog_remains(root: Path) -> tuple[tuple[storage.UsageAtom, ...], int]:
    """Return current-tail atoms while a production-shaped orphan repair remains queued."""

    sessions = root / ".codex" / "sessions" / "2026" / "07" / "31"
    active = sessions / "rollout-active.jsonl"
    orphan = sessions / "rollout-orphan.jsonl"
    _write_records(active, [_codex_meta("active-thread"), _codex_usage(NOW - 3, 100, 20, 10)])
    rows = [{"key": "gate-stats|0|codex", "kind": "codex", "transcript": str(active)}]
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=20)
    initial = scanner.scan(rows)
    _commit(scanner, initial)

    _write_records(orphan, [
        _codex_meta(
            "orphan-thread",
            "active-thread",
            forked_from_id="active-thread",
            thread_source="subagent",
        ),
        *(
            {"type": "response_item", "timestamp": NOW - 600 + index, "payload": {"text": "historical repair"}}
            for index in range(REPAIR_BACKLOG_RECORDS)
        ),
    ])
    scanner._max_records_per_scan = 1
    repair = scanner.scan(rows)
    _commit(scanner, repair)
    assert repair.backlog_files == 1

    _append_record(active, _codex_usage(NOW - 1, 140, 25, 17))
    live = scanner.scan(rows)
    assert live.backlog_files == 1
    atoms = tuple(
        usage_atom_from_source({**vars(item.atom), "tmux_key": item.tmux_key})
        for scan in (initial, live)
        for item in scan.items
    )
    assert atoms and any(atom.direction == "output" and atom.payload["quantity"] == 7 for atom in atoms)
    return atoms, live.backlog_files


@contextmanager
def _seeded_stats_page(browser, monkeypatch, tmp_path: Path, state_dir: Path, *, live_atoms=()):
    """Run the real stats service and HTTP forwarder on fixture-owned resources."""

    state = state_dir / "gate-stats-service"
    state.mkdir(parents=True)
    socket_path = state / "services" / "statsd.sock"
    database = state / storage.DATABASE_FILENAME
    clock_seconds = [NOW]
    assert _seed_realistic_stats(database) == REALISTIC_ROW_COUNT
    if live_atoms:
        with storage.Store.open(database) as store:
            appended = store.append_batch(usage_atoms=tuple(live_atoms))
            assert appended.usage_atoms_accepted == len(live_atoms)

    evidence = pricing.PricingEvidence(
        "gate-priced-model", "1.00", 1_000_000, "2026-07-31", "seed",
        "https://example.com/pricing", 1,
    )
    service = stats_service.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=60,
        clock=lambda: clock_seconds[0],
        price_resolver=lambda atom: pricing.UsagePriceProjection(
            int(atom.payload["quantity"]), int(atom.payload["quantity"]), evidence,
        ),
    )
    service_thread = threading.Thread(target=service.run, name="gate-stats-service", daemon=True)
    service_thread.start()
    http_server = http_thread = None
    try:
        assert service.cache_ready_event.wait(20), service._status()
        client = stats_client.StatsCurrentClient(socket_path, database)
        asset_name = "gate-stats.html"
        asset_dir = tmp_path / "gate-stats-static"
        _write_current_stats_fixture_assets(asset_dir, asset_name)
        monkeypatch.setitem(web_module.STATIC_CONTENT_TYPES, asset_name, "text/html; charset=utf-8")
        monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)
        monkeypatch.setattr(server_module, "start_agent_auth_status_refresh", lambda *args, **kwargs: None)
        app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                client,
                client_binding_secret=b"gate-stats-client-binding-secret",
            ),
            client_events=ClientEventBroker(),
        )
        http_server, http_thread = start_browser_server(monkeypatch, tmp_path, app, auth_bypass=True)
        browser.get(f"http://127.0.0.1:{http_server.server_address[1]}/static/{asset_name}")
        yield SimpleNamespace(service=service, client=client, app=app, clock_seconds=clock_seconds)
    finally:
        run_fixture_cleanup_phases("gate stats browser", (
            ("HTTP server", lambda: stop_browser_server(http_server, http_thread, browser=browser) if http_server is not None and http_thread is not None else None),
            ("stats service", lambda: stop_stats_current_service(service, service_thread)),
        ))


def _start_current_stats_over_network(browser):
    """Mount the production stream transport over the fixture's real HTTP server.

    `_current_stats_fixture_html(network_fetch=True)` forwards its fetches to the
    server, but its generic helper intentionally substitutes a synchronous fake
    EventSource for renderer tests. G1 exercises the live append-to-paint
    transition, so it must use the browser's native EventSource too.
    """

    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const root = document.getElementById('stats-root');
        const mounted = YOmuxStatsCurrent.mount(root, {
          view: 'stats',
          clientId: 'gate-live-stats',
          savedRange: 300,
          savedResolution: 1,
          fetch: window.__statsFixture.fetch,
          EventSource: window.EventSource,
        });
        window.__statsFixture.mounted = mounted;
        mounted.start().then(async () => {
          await window.__yomuxTestWaitFor(
            () => root.querySelector('[data-stats-chart="cpu"]'),
            {description: 'live stats first exact generation'}
          );
          done({ok: true});
        }).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_g4_revisiting_unchanged_selection_reuses_cache_and_repair_reconnects_without_refetch(browser, tmp_path):
    """G4: exact cached selections and a server-requested reconnect avoid redundant snapshots."""

    load_static_html_fixture(
        browser,
        tmp_path,
        "g4-selection-cache.html",
        _current_stats_fixture_html(),
    )
    _start_current_stats(browser)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const fixture = window.__statsFixture;
          const root = document.getElementById('stats-root');
          const settle = async (advanceMs = 0) => {
            await fixture.clock.advance(advanceMs);
            await Promise.resolve();
            await Promise.resolve();
          };
          const choose = async (rangeSeconds, resolution) => {
            const range = root.querySelector('[data-stats-current-range]');
            if (Number(range.value) !== rangeSeconds) {
              range.value = String(rangeSeconds);
              range.dispatchEvent(new Event('change', {bubbles: true}));
              await settle();
            }
            const picker = root.querySelector('[data-stats-current-resolution]');
            picker.value = String(resolution);
            picker.dispatchEvent(new Event('change', {bubbles: true}));
            await settle();
          };
          await choose(3600, 60);
          const before = fixture.snapshotRequests.length;
          for (const resolution of [300, 60, 300, 60]) await choose(3600, resolution);
          const unchangedFetches = fixture.snapshotRequests.length - before;
          await settle(500);
          const source = [...fixture.eventSources].reverse().find(item => !item.closed);
          const sourcesBeforeRepair = fixture.eventSources.length;
          source.emit('repair', {});
          await settle(500);
          done({
            before,
            after: fixture.snapshotRequests.length,
            unchangedFetches,
            sourcesBeforeRepair,
            sourcesAfterRepair: fixture.eventSources.length,
            priorSourceClosed: source.closed === true,
            replacementSourceOpen: fixture.eventSources.at(-1)?.closed !== true,
            active: fixture.lastSnapshot,
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["unchangedFetches"] == 0, result
    assert result["after"] - result["before"] == 0, result
    assert result["sourcesAfterRepair"] == result["sourcesBeforeRepair"] + 1, result
    assert result["priorSourceClosed"] is True and result["replacementSourceOpen"] is True, result


@pytest.mark.browser
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_g1_real_token_and_cost_series_paint_while_repair_backlog_remains(
    browser, monkeypatch, tmp_path, gate_runtime_paths
):
    """Paint real token/cost paths from 8,929 rows while a 240-record repair backlog remains."""

    atoms, backlog_files = _live_usage_while_repair_backlog_remains(gate_runtime_paths.state_dir / "repair")
    with _seeded_stats_page(
        browser,
        monkeypatch,
        tmp_path,
        gate_runtime_paths.state_dir,
    ) as runtime:
        _start_current_stats_over_network(browser)
        before_generation = runtime.client.status()["cache_generation"]
        runtime.clock_seconds[0] += 1
        appended = runtime.client.append(usage_atoms=atoms)
        assert appended["counts"]["usage_atoms_accepted"] == len(atoms), appended
        metrics = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const root = document.getElementById('stats-root');
            const painted = selector => {
              const series = root.querySelector(selector);
              const path = series?.querySelector('path');
              const rect = series?.getBoundingClientRect();
              return {
                present: Boolean(series && path),
                points: Number(series?.dataset.pointCount || 0),
                path: path?.getAttribute('d') || '',
                displayed: Boolean(rect && rect.width > 0 && rect.height > 0),
              };
            };
            (async () => {
              const token = await window.__yomuxTestWaitFor(() => {
                const value = painted('[data-stats-chart="agent-tokens"] [data-series^="agent_tokens_per_minute:"]');
                return value.points > 1 ? value : null;
              }, {timeoutMs: 10000, description: 'token paint after backlogged producer publication'});
              root.querySelector('[data-stats-current-visibility="cost"]').click();
              const cost = await window.__yomuxTestWaitFor(() => {
                const value = painted('[data-stats-chart="cost"] [data-series="cost_micro_usd"]');
                return value.points > 1 ? value : null;
              }, {timeoutMs: 10000, description: 'cost paint after backlogged producer publication'});
              done({token, cost});
            })().catch(error => done({error: String(error?.stack || error)}));
            """
        )
        after_generation = runtime.client.status()["cache_generation"]
    assert backlog_files == 1
    assert not metrics.get("error"), metrics
    assert after_generation > before_generation
    assert metrics["token"]["present"] and metrics["token"]["points"] > 1 and metrics["token"]["path"], metrics
    assert metrics["cost"]["present"] and metrics["cost"]["points"] > 1 and metrics["cost"]["path"], metrics
    assert metrics["token"]["displayed"] and metrics["cost"]["displayed"], metrics


@pytest.mark.browser
def test_g2_unpriced_live_attribution_never_renders_as_zero_cost(browser, tmp_path, gate_runtime_paths):
    """Unknown-model live attribution stays visibly unpriced instead of becoming $0.00."""

    load_static_html_fixture(browser, tmp_path, "gate-stats-unpriced.html", _current_stats_fixture_html())
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          let pricedReport = null;
          window.__statsFixture.snapshotTransform = input => {
            const snapshot = structuredClone(input);
            const report = snapshot.cost_report;
            pricedReport = structuredClone(report);
            const zeroCosts = value => {
              if (!value || typeof value !== 'object') return;
              for (const [key, item] of Object.entries(value)) {
                if (key === 'micro_usd' || key === 'api_list_micro_usd' || key === 'total_micro_usd' || key === 'total_api_list_micro_usd') value[key] = 0;
                else zeroCosts(item);
              }
            };
            zeroCosts(report);
            report.priced = {atoms: 0, tokens: 0};
            report.unpriced = {atoms: 3, tokens: report.total_tokens};
            for (const row of [...report.models, ...report.agents]) {
              row.priced = {atoms: 0, tokens: 0};
              row.unpriced = {atoms: 3, tokens: row.total_tokens};
            }
            for (const row of report.evidence) row.priced_atoms = 0;
            return snapshot;
          };
          await window.__statsFixture.start('cost');
          const root = document.getElementById('stats-root');
          root.querySelector('[data-stats-current-cost-more]').click();
          const unpriced = {
            summary: root.querySelector('[data-stats-current-cost-summary]').textContent,
            details: document.querySelector('[data-stats-current-cost-modal-scroll]').textContent,
          };
          window.__statsFixture.lastSnapshot = {
            ...structuredClone(window.__statsFixture.lastSnapshot),
            cost_report: pricedReport,
          };
          window.__statsFixture.emitCpuDelta(12);
          await window.__statsFixture.clock.advance(0);
          done({
            unpriced,
            liveSummary: root.querySelector('[data-stats-current-cost-summary]').textContent,
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "Unpriced: 1.1K tokens / 3 atoms" in metrics["unpriced"]["details"], metrics
    assert "Unpriced" in metrics["unpriced"]["summary"], metrics
    assert "$0.00" not in metrics["unpriced"]["summary"], metrics
    assert "$0.25" in metrics["liveSummary"], metrics


@pytest.mark.browser
def test_g6_unlisted_series_uses_declared_micro_usd_unit(browser, tmp_path, gate_runtime_paths):
    """An unlisted 150,000 micro-USD series paints as $0.15 from its declared unit."""

    load_static_html_fixture(browser, tmp_path, "gate-stats-unlisted-unit.html", _current_stats_fixture_html())
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__statsFixture.snapshotTransform = input => {
          const snapshot = structuredClone(input);
          const bucket = snapshot.buckets.find(item => Object.keys(item.series).length > 0);
          delete bucket.series.cost_micro_usd;
          bucket.series.unlisted_billing_probe = {
            value: 150000,
            unit: 'micro_usd',
            source_count: 1,
            first_timestamp: bucket.start,
            last_timestamp: bucket.start,
          };
          return snapshot;
        };
        (async () => {
          await window.__statsFixture.start('cost');
          const root = document.getElementById('stats-root');
          const series = root.querySelector('[data-series="unlisted_billing_probe"]');
          const chart = series?.closest('[data-stats-chart]');
          const svg = chart?.querySelector('[data-stats-current-svg]');
          const point = series?.querySelector('[data-series-point]');
          const bounds = svg?.getBoundingClientRect();
          svg?.dispatchEvent(new PointerEvent('pointermove', {
            bubbles: true,
            pointerType: 'mouse',
            clientX: bounds.left + Number(point?.getAttribute('cx')) / 600 * bounds.width,
          }));
          done({
            chart: chart?.dataset.statsChart || '',
            labels: [...chart?.querySelectorAll('text') || []].map(item => item.textContent),
            legend: chart?.querySelector('[data-series-legend="unlisted_billing_probe"]')?.textContent || '',
            tooltip: chart?.querySelector('[data-stats-current-tooltip]')?.textContent || '',
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["legend"] == "unlisted billing probe", result
    assert "$0.15" in result["labels"], result
    assert "$0.15" in result["tooltip"], result


@pytest.mark.browser
def test_g7_monotonic_value_never_renders_as_wall_clock_age(browser, tmp_path, gate_runtime_paths):
    """`generated_at` is wall-clock data. A monotonic counter in that field must read as absent.

    Subtracting a small monotonic value from `Date.now()` yields a confident, false multi-year age
    instead of admitting no wall-clock time exists, so the guard lives in `debugSystemGeneratedAge`
    and the summary strip's `updated` fact is where its answer is rendered.

    The selector is a DATA HOOK, deliberately, and this test is why. It used to be
    `[role="status"]`, which was written when the summary strip carried that role. A later change
    moved the role off the strip and added a screen-reader announce region ahead of it in document
    order; `querySelector` takes the first match, so this test silently rebound to the announce
    region and started asserting against a counts string. The contract went unverified while the
    suite stayed green. A bare role is a rebindable selector -- any future sibling can take it --
    so this names the element that owns the rendering instead.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1&layout=left&tabs=left:1")
    rendered = browser.execute_script(
        """
        jsDebugSystemState.payload = {
          ok: true,
          generated_at: 12345,
          server: {}, owner: {}, refresh: {roles: {}}, search_index: {}, caches: {},
          client_events: {}, chat: {}, cpu_budget: {},
          local_services: {totals: {}, services: []},
          top_endpoints: [], top_background_work: [],
        };
        const host = document.createElement('div');
        host.innerHTML = debugSystemInnerHtml();
        const updated = host.querySelector('[data-js-debug-roster-summary-fact="updated"]');
        return {
          updated: updated ? updated.textContent : null,
          // Every element in the rendered panel that carries the age, so a future second copy
          // rendered somewhere else cannot pass by being invisible to one selector.
          matches: Array.from(host.querySelectorAll('*'))
            .filter(node => !node.children.length && /\b12345\b|1970/.test(node.textContent))
            .map(node => node.textContent.trim()),
        };
        """
    )
    assert rendered["updated"] is not None, rendered
    text = rendered["updated"]
    assert "year" not in text.lower(), text
    assert "not available" in text.lower() or "n/a" in text.lower(), text
    # NEGATIVE CONTROL: nothing anywhere in the panel renders the raw monotonic value or a 1970
    # date derived from it. The single-element assertion above cannot see a second copy.
    assert rendered["matches"] == [], rendered

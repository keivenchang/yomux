# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""HTTP-served browser coverage for the current render-only YO!stats client."""

import inspect
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.request import Request
from urllib.request import urlopen

from selenium.webdriver.common.by import By
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _live_runtime_boot_fixture_html
from tests.helpers.browser_stats_coverage import _current_stats_fixture_html
from tests.helpers.browser_stats_coverage import _start_current_stats
from tests.helpers.browser_stats_coverage import _write_current_stats_fixture_assets
from tests.gate_harness import run_fixture_cleanup_phases
from yomux_lib.stats_current import resolution as stats_resolution
from yomux_lib.stats_current import storage


def test_current_stats_fixture_uses_canonical_resolution_policy():
    source = inspect.getsource(_current_stats_fixture_html)
    assert "const matrix = [" not in source
    assert "window.__statsFixtureCapabilities" in source


# Shared tooling/fixture safety contract; this does not validate Python backend product behavior.
@pytest.mark.shared_tooling
def test_live_runtime_boot_fixture_exposes_exact_stats_capabilities_before_logs_render(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    observed = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        Promise.all([
          fetch('/api/stats-capabilities', {cache: 'no-store'}).then(response => response.json()),
          (async () => {
            document.querySelector('[data-js-debug-subtab="logs"]')?.click();
            return window.__yomuxTestWaitFor(
              () => {
                const view = document.querySelector('[data-js-debug-subview="logs"]');
                return view && !view.hidden && view.querySelector('.js-debug-logs-toolbar')
                  && view.querySelector('.js-debug-log-list') ? true : false;
              },
              {timeoutMs: 2000, intervalMs: 10, description: 'fixture Logs rendered assertion path'},
            );
          })(),
        ]).then(([capabilities, logsRendered]) => done({capabilities, logsRendered}))
          .catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert observed.get("error") is None, observed
    assert observed["capabilities"] == json.loads(json.dumps(stats_resolution.wire_capabilities()))
    assert observed["logsRendered"] is True, observed


def test_stats_only_boot_keeps_stats_ready_without_unrelated_route_traffic(browser, tmp_path):
    """Stats-only boot must not probe routes outside stats plus the live agent-activity surface."""

    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?debug=1&sessions=debug&layout=left&tabs=left:__debug__",
        sessions=("debug",),
        terminal_only=True,
        stats_only=True,
    )
    result = WebDriverWait(browser, 15, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            if (typeof jsDebugHistoryReadiness === 'undefined'
                || jsDebugHistoryReadiness.phase !== 'ready') return false;
            const graph = document.querySelector('[data-js-debug-graph]');
            const rect = graph?.getBoundingClientRect();
            return graph && rect.width > 0 && rect.height > 0
              ? {
                  statsOnly: window.__fixtureStatsOnly === true,
                  graphWidth: rect.width,
                  graphHeight: rect.height,
                  statsFetches: window.__bootFetches.filter(item => item.path.startsWith('/api/stats')).length,
                  agentStatusFetches: window.__bootFetches.filter(item => item.path === '/api/stats-agent-status').length,
                }
              : false;
            """
        )
    )
    assert result["statsOnly"] is True, result
    assert result["statsFetches"] > 0, result
    assert result["agentStatusFetches"] > 0, result
    detail = browser.execute_script(
        """
        const node = document.querySelector('[data-js-debug-agent-window-detail="activity"]');
        return {text: node?.textContent?.trim() || '', state: node?.dataset?.jsDebugAgentWindowDetailState || null, body: document.body.innerText.slice(0, 1200)};
        """
    )
    assert detail["state"] == "current" and "2 agent windows across 1 sessions" in detail["text"], {"result": result, "detail": detail}
    non_stats_boot_fetches = browser.execute_script(
        """
        return window.__bootFetches.filter(item => !item.path.startsWith('/api/stats-'));
        """
    )
    assert non_stats_boot_fetches == [{
        "body": None,
        "method": "GET",
        "path": "/api/activity",
        "search": "?hours=24&visible=1&scope=all",
    }], non_stats_boot_fetches
    boot_failures = browser.execute_script(
        """
        return (typeof jsDebugFailureEvents === 'function' ? jsDebugFailureEvents() : []).filter(event => event.type === 'api');
        """
    )
    assert not boot_failures, boot_failures
    unrelated = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const paths = [
          '/api/settings', '/api/session-metadata', '/api/background/status',
          '/api/auto-approve', '/api/fs/index-status', '/api/watched-prs',
          '/api/update-status', '/api/chat/bootstrap', '/api/transcripts',
          '/api/unrelated-boot-route',
        ];
        Promise.all(paths.map(path => fetch(path, {cache: 'no-store'}).then(response =>
          response.json().then(body => ({path, status: response.status, body})))))
          .then(results => done(results))
          .catch(error => done({error: String(error)}));
        """
    )
    assert isinstance(unrelated, list), unrelated
    assert all(item["status"] == 404 and item["body"]["error"] == "stats-only fixture rejects unrelated route" for item in unrelated), unrelated
    unknown = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        apiFetchJson('/api/unrelated-boot-route', {cache: 'no-store'})
          .then(() => done({error: 'unexpected success'}))
          .catch(error => done({message: String(error?.message || error), status: error?.status || null}));
        """
    )
    assert unknown["status"] == 404, unknown
    unknown_failures = browser.execute_script(
        """
        return (typeof jsDebugFailureEvents === 'function' ? jsDebugFailureEvents() : [])
          .filter(event => event.endpoint === '/api/unrelated-boot-route');
        """
    )
    assert unknown_failures and unknown_failures[-1]["status"] == 404, unknown_failures
    readiness_after_unrelated_404 = browser.execute_script(
        """
        const graph = document.querySelector('[data-js-debug-graph]');
        const rect = graph?.getBoundingClientRect();
        return {
          phase: typeof jsDebugHistoryReadiness !== 'undefined' ? jsDebugHistoryReadiness.phase : null,
          graphWidth: rect?.width || 0,
          graphHeight: rect?.height || 0,
        };
        """
    )
    assert readiness_after_unrelated_404["phase"] == "ready", readiness_after_unrelated_404
    assert readiness_after_unrelated_404["graphWidth"] > 0 and readiness_after_unrelated_404["graphHeight"] > 0, readiness_after_unrelated_404
    browser.execute_script("clearJsDebugEvents();")


def test_stats_only_graph_renders_live_green_edge_pulse(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?debug=1&sessions=debug&layout=left&tabs=left:__debug__",
        sessions=("debug",),
        terminal_only=True,
        stats_only=True,
    )
    result = WebDriverWait(browser, 15, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const graph = document.querySelector('[data-js-debug-graph]');
            if (!graph || typeof jsDebugHistoryReadiness === 'undefined' || jsDebugHistoryReadiness.phase !== 'ready') return false;
            const pulse = graph.querySelectorAll('[data-js-debug-live-pulse]');
            const node = pulse[0];
            const style = node ? getComputedStyle(node) : null;
            return {
              pulseCount: pulse.length,
              animationName: style?.animationName || '',
              fill: style?.fill || '',
              pulseWidth: node?.getBoundingClientRect().width || 0,
              graphHidden: graph.offsetParent === null,
            };
            """
        )
    )
    assert result["pulseCount"] > 0, result
    assert result["animationName"] == "agent-status-opacity-pulse", result
    assert result["fill"] != "none", result
    assert result["pulseWidth"] >= 2, result
    assert result["graphHidden"] is False, result


def test_stats_only_activity_adopts_working_codex_marker_for_visible_terminal(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?debug=1&sessions=1,debug&layout=left&tabs=left:__debug__,1",
        sessions=("1", "debug"),
        terminal_only=True,
        stats_only=True,
        auto_approve_payload={
            "agent_window_snapshot_revision": 1,
            "session_order": ["1"],
            "sessions": {
                "1": {
                    "target": "1",
                    "enabled": False,
                    "agent_windows": [{
                        "kind": "codex",
                        "state": "working",
                        "window_index": 0,
                        "window_label": "0:codex",
                        "pane_target": "%73",
                        "current": True,
                    }],
                },
            },
        },
    )
    result = WebDriverWait(browser, 15, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const tab = document.querySelector('[data-pane-tab="1"]');
            const marker = tab?.querySelector('.session-agent-activity-marker');
            const dot = marker?.querySelector('.agent-window-status-dot');
            if (!tab || !marker || !dot) return false;
            return {
              state: dot.className,
              animationName: getComputedStyle(dot).animationName,
              color: getComputedStyle(dot).color,
            };
            """
        )
    )
    assert "status-indicator--working" in result["state"], result
    assert "heartbeat-pulse" in result["state"], result
    assert result["animationName"] == "agent-status-opacity-pulse", result


def _load_current_stats(browser, tmp_path, view="stats"):
    load_static_html_fixture(
        browser,
        tmp_path,
        f"current-stats-{view}.html",
        _current_stats_fixture_html(),
    )
    _start_current_stats(browser, view)








def test_current_stats_chromium_merges_historical_view_alias_tokens_under_source_tmux_names(browser, tmp_path):
    load_static_html_fixture(browser, tmp_path, "current-stats-aliases.html", _current_stats_fixture_html())
    browser.execute_script(
        """
        const fixture = window.__statsFixture;
        window.YOmuxStatsCurrent.setSessionAliases({
          '__yomux_view_7110_3a5ed04792596e3a0e3f43d1': 'yo7111',
          '__yomux_view_7110_6617c41d89676ea351c22c43': 'yo7110',
        });
        fixture.snapshotTransform = snapshot => {
          for (const bucket of snapshot.buckets) {
            const value = Number(Object.entries(bucket.series).find(([name]) => name.startsWith('agent_tokens_per_minute:'))?.[1].value || 0);
            const series = Object.fromEntries(Object.entries(bucket.series).filter(([name]) => !name.startsWith('agent_tokens_per_minute:')));
            Object.assign(series, {
              'agent_tokens_per_minute:yo7111': {value: 20, source_count: 1, first_timestamp: bucket.start, last_timestamp: bucket.start},
              'agent_tokens_per_minute:__yomux_view_7110_3a5ed04792596e3a0e3f43d1|1|%65|codex': {value: 30, source_count: 1, first_timestamp: bucket.start, last_timestamp: bucket.start},
              'agent_tokens_per_minute:__yomux_view_7110_6617c41d89676ea351c22c43|0|%55|codex': {value, source_count: 1, first_timestamp: bucket.start, last_timestamp: bucket.start},
            });
            bucket.series = series;
          }
          return snapshot;
        };
        """
    )
    started = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const fixture = window.__statsFixture;
        const root = document.getElementById('stats-root');
        fixture.start('stats').then(() => done({ok: true})).catch(error => done({
          error: String(error?.stack || error),
          generations: fixture.generationEvents.map(item => JSON.parse(item.dataset).requested_resolution),
          status: root.querySelector('[data-stats-current-state]')?.textContent || '',
          charts: [...root.querySelectorAll('[data-stats-chart]')].map(chart => chart.dataset.statsChart),
          rootHtml: root.innerHTML.slice(0, 1500),
          aliasSetter: typeof window.YOmuxStatsCurrent.setSessionAliases,
          failures: typeof jsDebugFailureEvents === 'function' ? jsDebugFailureEvents() : [],
        }));
        """
    )
    assert started.get("error") is None, started
    result = browser.execute_script(
        """
        const root = document.getElementById('stats-root');
        return {
          series: [...root.querySelectorAll('[data-stats-chart="agent-tokens"] [data-series]')].map(item => ({
            key: item.dataset.series,
            label: item.querySelector('title')?.textContent || '',
            values: [...item.querySelectorAll('[data-series-point]')].map(point => Number(point.dataset.pointValue)),
          })),
          legend: [...root.querySelectorAll('[data-stats-chart="agent-tokens"] [data-series-legend]')].map(item => item.textContent),
        };
        """
    )
    keys = {row["key"] for row in result["series"]}
    assert "agent_tokens_per_minute:yo7111" in keys, result
    assert "agent_tokens_per_minute:yo7110" in keys, result
    assert not any("__yomux_view_" in key for key in keys), result
    assert {"yo7110", "yo7111"}.issubset(set(result["legend"])), result




def _retired_current_stats_touch_pointer_path_pins_dismisses_and_zooms(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    result = browser.execute_script(
        """
        const root = document.getElementById('stats-root');
        const cpuChart = root.querySelector('[data-stats-chart="cpu"]');
        const initialSvg = cpuChart.querySelector('[data-stats-current-svg]');
        const controls = root.querySelector('[data-stats-current-controls]');
        const initialLabels = [...initialSvg.querySelectorAll('text')].slice(0, 2).map(item => item.textContent);
        const bounds = initialSvg.getBoundingClientRect();
        const firstPoint = initialSvg.querySelector('[data-series-point]');
        const pointClientX = bounds.left + Number(firstPoint.getAttribute('cx')) / 600 * bounds.width;
        const clientY = bounds.top + bounds.height / 2;
        const touchAction = {
          svg: getComputedStyle(initialSvg).touchAction,
          chart: getComputedStyle(cpuChart).touchAction,
          controls: getComputedStyle(controls).touchAction,
        };
        initialSvg.setPointerCapture = () => {};
        initialSvg.releasePointerCapture = () => {};

        const dispatchTouch = (target, type, clientX, pointerId) => {
          const event = new PointerEvent(type, {
            bubbles: true,
            cancelable: true,
            pointerType: 'touch',
            pointerId,
            button: 0,
            clientX,
            clientY,
          });
          target.dispatchEvent(event);
          return event.defaultPrevented;
        };

        const tapDownPrevented = dispatchTouch(initialSvg, 'pointerdown', pointClientX, 31);
        const tapUpPrevented = dispatchTouch(initialSvg, 'pointerup', pointClientX, 31);
        const tooltip = cpuChart.querySelector('[data-stats-current-tooltip]');
        const pinned = {
          hidden: tooltip.hidden,
          text: tooltip.textContent,
          downPrevented: tapDownPrevented,
          upPrevented: tapUpPrevented,
        };

        dispatchTouch(document.body, 'pointerdown', 1, 32);
        const dismissed = tooltip.hidden;
        const controlPointerPrevented = dispatchTouch(
          controls,
          'pointerdown',
          controls.getBoundingClientRect().left + 1,
          33,
        );

        const dragStart = bounds.left + bounds.width * 0.25;
        const dragEnd = bounds.left + bounds.width * 0.75;
        const dragDownPrevented = dispatchTouch(initialSvg, 'pointerdown', dragStart, 34);
        const dragMovePrevented = dispatchTouch(initialSvg, 'pointermove', dragEnd, 34);
        const selectionVisibleDuringDrag = !initialSvg.querySelector('[data-stats-current-selection]').hidden;
        const dragUpPrevented = dispatchTouch(initialSvg, 'pointerup', dragEnd, 34);

        const zoomedSvg = root.querySelector('[data-stats-chart="cpu"] [data-stats-current-svg]');
        const zoomedLabels = [...zoomedSvg.querySelectorAll('text')].slice(0, 2).map(item => item.textContent);
        const reset = root.querySelector('[data-stats-current-zoom-reset]');
        const resetVisible = Boolean(reset && !reset.hidden);
        reset.click();
        const resetSvg = root.querySelector('[data-stats-chart="cpu"] [data-stats-current-svg]');
        const resetLabels = [...resetSvg.querySelectorAll('text')].slice(0, 2).map(item => item.textContent);

        return {
          pinned,
          dismissed,
          touchAction,
          controlPointerPrevented,
          drag: {
            downPrevented: dragDownPrevented,
            movePrevented: dragMovePrevented,
            upPrevented: dragUpPrevented,
            selectionVisibleDuringDrag,
            resetVisible,
            initialLabels,
            zoomedLabels,
            resetLabels,
          },
        };
        """
    )
    assert result["pinned"]["hidden"] is False, result
    assert "cpu percent" in result["pinned"]["text"].lower(), result
    assert result["pinned"]["downPrevented"] is True, result
    assert result["pinned"]["upPrevented"] is True, result
    assert result["dismissed"] is True, result
    assert result["touchAction"]["svg"] == "none", result
    assert result["touchAction"]["chart"] != "none", result
    assert result["touchAction"]["controls"] != "none", result
    assert result["controlPointerPrevented"] is False, result
    assert result["drag"]["downPrevented"] is True, result
    assert result["drag"]["movePrevented"] is True, result
    assert result["drag"]["upPrevented"] is True, result
    assert result["drag"]["selectionVisibleDuringDrag"] is True, result
    assert result["drag"]["resetVisible"] is True, result
    assert result["drag"]["zoomedLabels"] != result["drag"]["initialLabels"], result
    assert result["drag"]["resetLabels"] == result["drag"]["initialLabels"], result


def _retired_current_stats_controls_and_charts_fit_desktop_and_ipad_widths(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    for width, height in ((1280, 800), (768, 1024), (430, 800)):
        browser.set_window_size(width, height)
        metrics = browser.execute_script(
            """
            const root = document.getElementById('stats-root');
            const rootRect = root.getBoundingClientRect();
            const charts = [...root.querySelectorAll('[data-stats-chart]')].map(chart => {
              const rect = chart.getBoundingClientRect();
              return {left: rect.left, right: rect.right, width: rect.width};
            });
            const controls = root.querySelector('.yo-stats-current-controls').getBoundingClientRect();
            return {
              root: {left: rootRect.left, right: rootRect.right, width: rootRect.width},
              charts,
              rendering: {
                charts: charts.length,
                paths: root.querySelectorAll('[data-series] > path').length,
                points: [...root.querySelectorAll('[data-point-count]')]
                  .reduce((total, series) => total + Number(series.dataset.pointCount), 0),
              },
              controls: {left: controls.left, right: controls.right, width: controls.width},
              svgTouchActions: [...root.querySelectorAll('[data-stats-current-svg]')].map(svg => getComputedStyle(svg).touchAction),
              bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            };
            """
        )
        assert metrics["bodyOverflow"] <= 1, (width, metrics)
        assert metrics["controls"]["left"] >= metrics["root"]["left"] - 1, (width, metrics)
        assert metrics["controls"]["right"] <= metrics["root"]["right"] + 1, (width, metrics)
        assert all(chart["left"] >= metrics["root"]["left"] - 1 for chart in metrics["charts"]), (width, metrics)
        assert all(chart["right"] <= metrics["root"]["right"] + 1 for chart in metrics["charts"]), (width, metrics)
        assert metrics["rendering"] == {"charts": 7, "paths": 7, "points": 306}, (width, metrics)
        assert set(metrics["svgTouchActions"]) == {"none"}, (width, metrics)


def _retired_current_cost_summary_opens_an_internal_scroll_modal_and_dismisses_both_ways(browser, tmp_path):
    _load_current_stats(browser, tmp_path, view="cost")
    browser.set_window_size(430, 600)
    summary = browser.execute_script(
        """
        const root = document.getElementById('stats-root');
        return {
          text: root.querySelector('[data-stats-current-cost-summary]').textContent,
          detailsBefore: root.querySelector('[role="dialog"]') !== null,
          buttonText: root.querySelector('[data-stats-current-cost-more]').textContent,
        };
        """
    )
    assert summary["detailsBefore"] is False, summary
    assert summary["buttonText"] == "More Info", summary
    assert "Total: $0.25" in summary["text"], summary
    assert "Total tokens: 1.1K tokens" in summary["text"], summary

    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-more]").click()
    metrics = browser.execute_script(
        """
        const modal = document.querySelector('[role="dialog"]');
        const scroll = modal.querySelector('[data-stats-current-cost-modal-scroll]');
        const rect = modal.getBoundingClientRect();
        scroll.scrollTop = Math.min(120, scroll.scrollHeight - scroll.clientHeight);
        const opened = [];
        const priorOpen = window.open;
        window.open = (...args) => { opened.push(args); return {}; };
        const pricingLink = modal.querySelector('a[href^="http"]');
        const pricingEvent = new MouseEvent('click', {bubbles: true, cancelable: true});
        pricingLink?.dispatchEvent(pricingEvent);
        window.open = priorOpen;
        return {
          title: modal.querySelector('h2').textContent,
          text: modal.textContent,
          link: modal.querySelector('a')?.href || '',
          opened,
          pricingDefaultPrevented: pricingEvent.defaultPrevented,
          top: rect.top,
          bottom: rect.bottom,
          right: rect.right,
          viewportWidth: innerWidth,
          viewportHeight: innerHeight,
          scrollTop: scroll.scrollTop,
          scrollHeight: scroll.scrollHeight,
          clientHeight: scroll.clientHeight,
          bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
        """
    )
    assert metrics["title"].startswith("Cost summary details ·"), metrics
    assert "Model Usages" in metrics["text"] and "By Agent" in metrics["text"], metrics
    assert "What these columns mean" in metrics["text"], metrics
    assert "Reasoning breakdown unavailable" in metrics["text"], metrics
    assert metrics["link"] == "https://example.com/pricing", metrics
    assert 0 <= metrics["top"] < metrics["bottom"] <= metrics["viewportHeight"], metrics
    assert metrics["right"] <= metrics["viewportWidth"] + 1, metrics
    assert metrics["scrollHeight"] > metrics["clientHeight"], metrics
    assert metrics["scrollTop"] > 0, metrics
    assert metrics["bodyOverflow"] <= 1, metrics

    preserved = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const scroll = document.querySelector('[data-stats-current-cost-modal-scroll]');
          const before = scroll.scrollTop;
          await window.__statsFixture.clock.advance(1000);
          done({before, after: document.querySelector('[data-stats-current-cost-modal-scroll]').scrollTop});
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert preserved.get("error") is None and preserved["after"] == preserved["before"], preserved

    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-modal-close]").click()
    assert browser.execute_script("return document.querySelector('[role=dialog]') === null;") is True
    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-more]").click()
    browser.execute_script("document.querySelector('[data-stats-current-cost-modal-backdrop]').click();")
    assert browser.execute_script("return document.querySelector('[role=dialog]') === null;") is True




def test_graph_refresh_preserves_scroll_before_native_wheel(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(lambda driver: driver.execute_script("return typeof refreshDebugGraphElement === 'function' && document.querySelector('.js-debug-panel [data-js-debug-graph]') !== null"))
    before = browser.execute_script("""
      const graph = document.querySelector('.js-debug-panel [data-js-debug-graph]');
      const owner = graph.closest('.js-debug-graph-view');
      owner.style.height = '240px'; owner.style.maxHeight = '240px'; owner.style.width = '240px';
      graph.style.minHeight = '1400px'; graph.style.minWidth = '1000px';
      owner.scrollTop = 180; owner.scrollLeft = 65;
      const before = {top:owner.scrollTop, left:owner.scrollLeft};
      const original = restoreElementScrollPosition;
      let deferredCalls = 0;
      restoreElementScrollPosition = function(...args) {deferredCalls++; return original(...args);};
      try {refreshDebugGraphElement(graph, {force:true});} finally {restoreElementScrollPosition = original;}
      window.__graphScrollAudit = {owner, before};
      return {before, after:{top:owner.scrollTop,left:owner.scrollLeft}, deferredCalls};
    """)
    assert before['before']['top'] == 180 and before['before']['left'] == 65, before
    assert before['after'] == before['before'] and before['deferredCalls'] == 0, before
    owner = browser.find_element(By.CSS_SELECTOR, '.js-debug-graph-view')
    ActionChains(browser).scroll_from_origin(ScrollOrigin.from_element(owner), 0, 140).perform()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return window.__graphScrollAudit.owner.scrollTop > window.__graphScrollAudit.before.top"))
    after = browser.execute_async_script("""
      const done = arguments[arguments.length-1], owner = window.__graphScrollAudit.owner;
      const top = owner.scrollTop, left = owner.scrollLeft;
      requestAnimationFrame(() => requestAnimationFrame(() => done({top,left,settledTop:owner.scrollTop,settledLeft:owner.scrollLeft})));
    """)
    assert after['top'] > 180 and after['settledTop'] == after['top'] and after['settledLeft'] == after['left'], after

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

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _live_runtime_boot_fixture_html
from tests.helpers.browser_stats_coverage import _current_stats_fixture_html
from tests.helpers.browser_stats_coverage import _start_current_stats
from tests.helpers.browser_stats_coverage import _write_current_stats_fixture_assets
from yolomux_lib import server as server_module
from yolomux_lib import web as web_module
from yolomux_lib.stats_current import client as stats_client
from yolomux_lib.stats_current import http as stats_http
from yolomux_lib.stats_current import resolution as stats_resolution
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage


def test_current_stats_fixture_uses_canonical_resolution_policy():
    source = inspect.getsource(_current_stats_fixture_html)
    assert "const matrix = [" not in source
    assert "window.__statsFixtureCapabilities" in source


def test_live_runtime_boot_fixture_exposes_exact_stats_capabilities_before_logs_render(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    observed = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        Promise.all([
          fetch('/api/stats-capabilities', {cache: 'no-store'}).then(response => response.json()),
          (async () => {
            document.querySelector('[data-js-debug-subtab="logs"]')?.click();
            return window.__yolomuxTestWaitFor(
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


def _load_current_stats(browser, tmp_path, view="stats"):
    load_static_html_fixture(
        browser,
        tmp_path,
        f"current-stats-{view}.html",
        _current_stats_fixture_html(),
    )
    _start_current_stats(browser, view)


def test_current_stats_browser_traverses_every_exact_matrix_cell(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    cells = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const results = [];
          for (const row of window.__statsFixture.capabilities.ranges) {
            for (const requested of ['AUTO', ...row.explicit_resolution_seconds]) {
              await window.__statsFixture.select(row.range_seconds, requested);
              const root = document.getElementById('stats-root');
              const generation = window.__statsFixture.lastGeneration;
              const points = [...root.querySelectorAll('[data-point-count]')].map(item => Number(item.dataset.pointCount));
              const paths = [...root.querySelectorAll('[data-stats-chart]')].map(chart => ({
                id: chart.dataset.statsChart,
                series: chart.querySelectorAll('[data-series]').length,
                paths: chart.querySelectorAll('[data-series] > path').length,
              }));
              results.push({
                range: row.range_seconds,
                requested,
                resolution: generation.resolution_seconds,
                bucketCount: generation.buckets.length,
                maxPoints: Math.max(...points),
                exactLabel: root.querySelector('.yo-stats-current-exact').textContent,
                axisSeconds: root.querySelector('[data-stats-chart="cpu"] svg').dataset.axisSeconds,
                paths,
              });
            }
          }
          done(results);
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert isinstance(cells, list), cells
    expected_cells = sum(
        1 + len(stats_resolution.explicit_resolutions(range_seconds))
        for range_seconds in stats_resolution.RANGE_SECONDS
    )
    assert len(cells) == expected_cells
    assert {cell["resolution"] for cell in cells} == set(stats_resolution.RESOLUTION_CHOICES)
    for cell in cells:
        assert cell["bucketCount"] == cell["range"] // cell["resolution"], cell
        assert cell["maxPoints"] == cell["bucketCount"] <= 600, cell
        assert cell["exactLabel"] == f"Exact {cell['resolution']}s", cell
        assert cell["axisSeconds"] == ("true" if cell["resolution"] == 1 else "false"), cell
        assert all(path["paths"] == path["series"] for path in cell["paths"]), cell


def test_http_client_rpc_cache_and_browser_render_every_exact_matrix_cell(
    browser,
    monkeypatch,
    tmp_path,
):
    now = 1_700_000_000.0
    service_now = [now - 60]
    state = tmp_path / "current-stats-e2e"
    state.mkdir()
    socket_path = state / "services" / "statsd.sock"
    database = state / storage.DATABASE_FILENAME
    service = stats_service.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=60,
        clock=lambda: service_now[0],
    )
    service_thread = threading.Thread(target=service.run, daemon=True)
    service_thread.start()
    http_server = http_thread = second_server = second_thread = None
    try:
        assert service.cache_ready_event.wait(5), service._status()
        client = stats_client.StatsCurrentClient(socket_path, database)
        service.cache_ready_event.clear()
        service_now[0] = now
        appended = client.append(
            observations=(storage.Observation(
                "cpu-real", "cpu", "web", now - 0.25, "cpu-epoch", 1,
                {"process_percent": 7, "system_percent": 23},
            ),),
            coverage_epochs=(storage.CoverageEpoch(
                "cpu", "web", "cpu-epoch", now - 10, None, 1, 1,
            ),),
            usage_atoms=(storage.UsageAtom(
                "usage-real", "output", "text", "none", "tokens", now - 0.25,
                {
                    "quantity": 25,
                    "provider": "openai",
                    "model": "gpt-real",
                    "agent_id": "sol",
                    "telemetry_complete": True,
                },
            ),),
        )
        assert appended["accepted"] == 3
        assert service.cache_ready_event.wait(5), service._status()
        assert service._status()["generations"]["cache_matches_source"] is True

        asset_name = "stats-current-e2e.html"
        asset_dir = tmp_path / "current-stats-static"
        _write_current_stats_fixture_assets(asset_dir, asset_name)
        monkeypatch.setitem(
            web_module.STATIC_CONTENT_TYPES,
            asset_name,
            "text/html; charset=utf-8",
        )
        monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)
        monkeypatch.setattr(
            server_module,
            "start_agent_auth_status_refresh",
            lambda *args, **kwargs: None,
        )
        app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                client,
                client_binding_secret=b"stats-e2e-client-binding-secret",
            ),
        )
        http_server, http_thread = start_browser_server(
            monkeypatch,
            tmp_path,
            app,
            auth_bypass=True,
        )
        browser.get(
            f"http://127.0.0.1:{http_server.server_address[1]}/static/{asset_name}"
        )
        _start_current_stats(browser)

        cells = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              const results = [];
              for (const row of window.__statsFixture.capabilities.ranges) {
                for (const requested of ['AUTO', ...row.explicit_resolution_seconds]) {
                  await window.__statsFixture.select(row.range_seconds, requested);
                  const root = document.getElementById('stats-root');
                  const accepted = window.__statsFixture.lastGeneration;
                  results.push({
                    range: row.range_seconds,
                    requested,
                    concrete: accepted.resolution_seconds,
                    buckets: accepted.buckets.length,
                    cpuPoints: root.querySelector('[data-series="cpu_percent:web"]')?.dataset.pointCount || '0',
                    axisSeconds: root.querySelector('[data-stats-chart="cpu"] svg').dataset.axisSeconds,
                  });
                }
              }
              done(results);
            })().catch(error => done({error: String(error?.stack || error)}));
            """
        )
        assert isinstance(cells, list), cells
        expected_cells = sum(
            1 + len(stats_resolution.explicit_resolutions(range_seconds))
            for range_seconds in stats_resolution.RANGE_SECONDS
        )
        assert len(cells) == expected_cells
        assert {cell["concrete"] for cell in cells} == set(stats_resolution.RESOLUTION_CHOICES)
        for cell in cells:
            assert cell["buckets"] == cell["range"] // cell["concrete"], cell
            assert cell["cpuPoints"] == "1", cell
            assert cell["axisSeconds"] == ("true" if cell["concrete"] == 1 else "false"), cell
        status = client.status()
        assert status["warm"] == {"ready": expected_cells, "total": expected_cells, "percent": 100.0}
        # Data identity is (range_seconds, resolved resolution_seconds): AUTO and the explicit
        # value it resolves to share one already-fetched generation, so distinct network requests
        # equal the distinct concrete resolutions offered per range, not every UI selection made.
        expected_requests = sum(
            len(stats_resolution.explicit_resolutions(range_seconds))
            for range_seconds in stats_resolution.RANGE_SECONDS
        )
        assert status["requests"]["snapshot"] >= expected_requests

        second_client = stats_client.StatsCurrentClient(socket_path, database)
        exact_request = {
            "range_seconds": 300,
            "resolution": 1,
            "client_id": "browser-current-fixture",
        }
        first_metadata, first_body = client.snapshot(exact_request)
        second_metadata, second_body = second_client.snapshot(exact_request)
        assert first_metadata["cache_generation"] == second_metadata["cache_generation"]
        assert first_metadata["source_generation"] == second_metadata["source_generation"]
        assert first_body == second_body

        second_app = SimpleNamespace(
            sessions=[],
            dangerously_yolo=False,
            stats_current_http=stats_http.StatsHttpForwarder(
                second_client,
                client_binding_secret=b"stats-e2e-client-binding-secret",
            ),
        )
        second_server, second_thread = start_browser_server(
            monkeypatch,
            tmp_path,
            second_app,
            auth_bypass=True,
        )
        query = (
            "/api/stats-snapshot?range_seconds=300&resolution=1&"
            "client_id=browser-current-fixture"
        )
        request_headers = {"X-YOLOmux-Request-ID": "r-stats-reader-parity"}
        with urlopen(
            Request(
                f"http://127.0.0.1:{http_server.server_address[1]}{query}",
                headers=request_headers,
            ),
            timeout=3,
        ) as response:
            first_http_body = response.read()
        with urlopen(
            Request(
            f"http://127.0.0.1:{second_server.server_address[1]}{query}",
                headers=request_headers,
            ),
            timeout=3,
        ) as response:
            second_http_body = response.read()
        assert first_http_body == second_http_body, {
            "first": json.loads(first_http_body),
            "second": json.loads(second_http_body),
        }
        assert json.loads(first_http_body)["cache_generation"] == first_metadata["cache_generation"]
    finally:
        if second_server is not None and second_thread is not None:
            stop_browser_server(second_server, second_thread)
        if http_server is not None and http_thread is not None:
            stop_browser_server(http_server, http_thread, browser=browser)
        service.stop_event.set()
        service.work_event.set()
        service_thread.join(timeout=3)
        assert not service_thread.is_alive()


def test_current_stats_one_second_motion_and_sse_delta_do_not_refetch_or_fabricate(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const fixture = window.__statsFixture;
          const root = document.getElementById('stats-root');
          const tokenPoint = () => root.querySelector('[data-series="agent_tokens_per_minute:sol"] [data-series-point]');
          const before = {
            x: Number(tokenPoint().getAttribute('cx')),
            value: Number(tokenPoint().dataset.pointValue),
            count: root.querySelector('[data-series="agent_tokens_per_minute:sol"]').dataset.pointCount,
            snapshots: fixture.snapshotRequests.length,
          };
          await fixture.clock.advance(1000);
          const afterTick = {
            x: Number(tokenPoint().getAttribute('cx')),
            value: Number(tokenPoint().dataset.pointValue),
            count: root.querySelector('[data-series="agent_tokens_per_minute:sol"]').dataset.pointCount,
            snapshots: fixture.snapshotRequests.length,
          };
          fixture.emitCpuDelta(99);
          await Promise.resolve();
          await Promise.resolve();
          const cpuValues = [...root.querySelectorAll('[data-series="cpu_percent:host"] [data-series-point]')].map(point => Number(point.dataset.pointValue));
          done({
            before,
            afterTick,
            afterDelta: {
              cpuLast: cpuValues.at(-1),
              snapshots: fixture.snapshotRequests.length,
              axis: root.querySelector('[data-stats-chart="cpu"] svg').dataset.axisSeconds,
              labels: [...root.querySelectorAll('[data-stats-chart="cpu"] text')].map(item => item.textContent),
            },
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["afterTick"]["x"] < result["before"]["x"], result
    assert result["afterTick"]["value"] == result["before"]["value"] == 120, result
    assert result["afterTick"]["count"] == result["before"]["count"] == "1", result
    assert result["afterTick"]["snapshots"] == result["before"]["snapshots"], result
    assert result["afterDelta"]["cpuLast"] == 99, result
    assert result["afterDelta"]["snapshots"] == result["before"]["snapshots"], result
    assert result["afterDelta"]["axis"] == "true", result
    assert any(label.count(":") == 2 for label in result["afterDelta"]["labels"]), result


def test_current_stats_one_second_motion_preserves_sparse_native_cadence_series(browser, tmp_path):
    _load_current_stats(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const fixture = window.__statsFixture;
          const root = document.getElementById('stats-root');
          const names = [
            'run_agents',
            'gpu_util_percent:gpu:0',
            'system_memory_used_bytes',
          ];
          const read = name => {
            const series = root.querySelector(`[data-series="${name}"]`);
            const points = [...series.querySelectorAll('[data-series-point]')];
            return {
              count: Number(series.dataset.pointCount),
              x: points.map(point => Number(point.getAttribute('cx'))),
              values: points.map(point => Number(point.dataset.pointValue)),
              sourceCounts: points.map(point => Number(point.dataset.pointSourceCount)),
            };
          };
          const before = Object.fromEntries(names.map(name => [name, read(name)]));
          const generationBefore = fixture.generationEvents.at(-1);
          const generationCountBefore = fixture.generationEvents.length;
          const snapshotsBefore = fixture.snapshotRequests.length;

          await fixture.clock.advance(3000);
          const afterTicks = Object.fromEntries(names.map(name => [name, read(name)]));
          const presentation = {
            generationCount: fixture.generationEvents.length,
            cacheGeneration: fixture.generationEvents.at(-1).cacheGeneration,
            datasetUnchanged: fixture.generationEvents.at(-1).dataset === generationBefore.dataset,
            snapshots: fixture.snapshotRequests.length,
          };

          fixture.emitSparseCadenceDelta();
          await Promise.resolve();
          await Promise.resolve();
          done({
            names,
            before,
            afterTicks,
            presentation,
            generationCountBefore,
            snapshotsBefore,
            afterDelta: {
              series: Object.fromEntries(names.map(name => [name, read(name)])),
              generationCount: fixture.generationEvents.length,
              cacheGeneration: fixture.generationEvents.at(-1).cacheGeneration,
            },
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["generationCountBefore"] == 1, result
    assert result["presentation"]["generationCount"] == 1, result
    assert result["presentation"]["cacheGeneration"] == 1, result
    assert result["presentation"]["datasetUnchanged"] is True, result
    assert result["presentation"]["snapshots"] == result["snapshotsBefore"], result
    shifts = []
    for name in result["names"]:
        before = result["before"][name]
        after_ticks = result["afterTicks"][name]
        assert after_ticks["count"] == before["count"] == 1, result
        assert after_ticks["values"] == before["values"], result
        assert after_ticks["sourceCounts"] == before["sourceCounts"] == [1], result
        assert after_ticks["x"][0] < before["x"][0], result
        shifts.append(after_ticks["x"][0] - before["x"][0])
    assert max(shifts) - min(shifts) < 0.001, result
    assert result["afterDelta"]["generationCount"] == 2, result
    assert result["afterDelta"]["cacheGeneration"] == 2, result
    for name in result["names"]:
        assert result["afterDelta"]["series"][name]["count"] == 2, result
    assert result["afterDelta"]["series"]["run_agents"]["values"] == [2, 3], result
    assert result["afterDelta"]["series"]["gpu_util_percent:gpu:0"]["values"] == [40, 41], result
    assert result["afterDelta"]["series"]["system_memory_used_bytes"]["values"] == [8000000000, 8100000000], result


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


def test_current_cost_pricing_link_dispatches_external_open(browser, tmp_path):
    _load_current_stats(browser, tmp_path, view="cost")
    browser.find_element(By.CSS_SELECTOR, "[data-stats-current-cost-more]").click()
    result = browser.execute_script(
        """
        const modal = document.querySelector('[role="dialog"]');
        const opened = [];
        const priorOpen = window.open;
        window.open = (...args) => { opened.push(args); return {}; };
        const link = modal.querySelector('a[href^="http"]');
        const event = new MouseEvent('click', {bubbles: true, cancelable: true});
        link.dispatchEvent(event);
        window.open = priorOpen;
        return {opened, defaultPrevented: event.defaultPrevented, location: location.href};
        """
    )
    assert result["opened"] == [["https://example.com/pricing", "_blank", "noopener,noreferrer"]], result
    assert result["defaultPrevented"] is True, result
    assert result["location"].endswith("current-stats-cost.html"), result

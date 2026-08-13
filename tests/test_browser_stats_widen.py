# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retained YO!stats range-transition regressions."""

import json
import time
from http import HTTPStatus
from typing import Any

import pytest

from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.gate_harness import wait_for_fixture_api_quiescence
from yolomux_lib.stats_current import http as stats_current_http
from yolomux_lib.stats_current import storage


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]

CPU_GRAPH_SAMPLE_COUNT = 8
CPU_GRAPH_PERCENT = 37.0

_STATS_ROUTE_INSTRUMENTATION = r"""
(() => {
  if (globalThis.__yolomuxStatsRouteCounts) return;
  const tracked = new Set([
    '/api/stats-capabilities', '/api/stats-snapshot', '/api/stats-retry', '/api/stats-stream',
  ]);
  const counts = Object.fromEntries([...tracked].map(path => [path, 0]));
  globalThis.__yolomuxStatsRouteCounts = counts;
  const count = input => {
    try {
      const url = new URL(typeof input === 'string' ? input : input?.url || '', location.href);
      if (tracked.has(url.pathname)) counts[url.pathname] += 1;
    } catch (_) {}
  };
  const nativeFetch = globalThis.fetch;
  if (typeof nativeFetch === 'function') {
    globalThis.fetch = function(input, ...rest) {
      count(input);
      return nativeFetch.call(this, input, ...rest);
    };
  }
  const NativeEventSource = globalThis.EventSource;
  if (typeof NativeEventSource === 'function') {
    globalThis.EventSource = new Proxy(NativeEventSource, {
      construct(target, args) {
        count(args[0]);
        return Reflect.construct(target, args, target);
      },
    });
  }
})();
"""


def _register_stats_route_instrumentation(harness: Any) -> None:
    register_browser_new_document_script(harness.driver, _STATS_ROUTE_INSTRUMENTATION)


def _stats_route_counts(harness: Any) -> dict[str, int]:
    return harness.driver.execute_script("return {...globalThis.__yolomuxStatsRouteCounts};")


def _browser_failure_fingerprint(client: Any, signature: str, minimum_count: int) -> dict[str, Any] | bool:
    response = client.browser_diagnostics()
    status = response.get("observation_status", {})
    for fingerprint in status.get("fingerprints", ()):
        if fingerprint.get("signature") == signature and int(fingerprint.get("count") or 0) >= minimum_count:
            return {"fingerprint": fingerprint, "status": status}
    return False


def test_retained_stats_widen_fetches_and_paints_the_full_exact_window(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script(
            "return typeof setDebugGraphRange === 'function' "
            "&& typeof globalThis.YOLOmuxStatsCurrent?.createBrowserClient === 'function' "
            "&& document.querySelector('.js-debug-panel [data-js-debug-graph]') !== null"
        )
    )
    result = browser.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        (async () => {
          const matrix = [
            [300, 1, [1, 10]],
            [900, 10, [10, 60]],
            [3600, 60, [60, 300]],
            [14400, 60, [60, 300]],
            [86400, 300, [300]],
          ];
          const capabilities = {
            resolution_choices: [1, 10, 60, 300],
            max_buckets: 600,
            min_buckets: 12,
            max_live_cadence_seconds: 60,
            ranges: matrix.map(([rangeSeconds, autoResolution, explicitResolutions]) => ({
              range_seconds: rangeSeconds,
              auto_resolution_seconds: autoResolution,
              explicit_resolution_seconds: explicitResolutions,
              buckets: Object.fromEntries(explicitResolutions.map(resolution => [resolution, rangeSeconds / resolution])),
            })),
          };
          const emptyDimensions = () => Object.fromEntries(
            ['input', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'output', 'other'].map(key => [
              key,
              {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
            ]),
          );
          const costReport = () => ({
            schema_version: 3,
            total_micro_usd: 0,
            total_api_list_micro_usd: 0,
            total_tokens: 0,
            dimensions: emptyDimensions(),
            priced: {atoms: 0, tokens: 0},
            unpriced: {atoms: 0, tokens: 0},
            models: [],
            agents: [],
            evidence: [],
            catalog_revision: 0,
            omissions: {models: 0, agents: 0, evidence: 0},
            reasoning_available: false,
          });
          let generation = 0;
          const snapshot = (rangeSeconds, requestedResolution, resolutionSeconds) => {
            const windowEnd = Math.floor(Date.now() / 1000 / resolutionSeconds) * resolutionSeconds;
            const windowStart = windowEnd - rangeSeconds;
            const bucketCount = rangeSeconds / resolutionSeconds;
            generation += 1;
            return {
              protocol_version: 2,
              range_seconds: rangeSeconds,
              requested_resolution: requestedResolution,
              resolution_seconds: resolutionSeconds,
              window_start: windowStart,
              window_end: windowEnd,
              generated_at: windowEnd,
              source_generation: generation,
              cache_generation: generation,
              rightmost_open: true,
              buckets: Array.from({length: bucketCount}, (_unused, index) => {
                const start = windowStart + index * resolutionSeconds;
                return {
                  start,
                  duration: resolutionSeconds,
                  series: {'cpu_percent:widen-fixture': {
                    value: 20 + index % 5,
                    source_count: 1,
                    first_timestamp: start,
                    last_timestamp: start,
                  }},
                  source: {first_timestamp: start, last_timestamp: start, count: 1},
                  open: index === bucketCount - 1,
                };
              }),
              no_data: [],
              cost_report: costReport(),
            };
          };
          const requests = [];
          const fixtureFetch = async input => {
            const url = new URL(String(input), location.href);
            if (url.pathname === '/api/stats-capabilities') {
              return {status: 200, json: async () => structuredClone(capabilities)};
            }
            if (url.pathname !== '/api/stats-snapshot') {
              return {status: 404, json: async () => ({})};
            }
            const rangeSeconds = Number(url.searchParams.get('range_seconds'));
            const requestedText = url.searchParams.get('resolution');
            const requestedResolution = requestedText === 'AUTO' ? 'AUTO' : Number(requestedText);
            const capability = capabilities.ranges.find(row => row.range_seconds === rangeSeconds);
            const resolutionSeconds = requestedResolution === 'AUTO'
              ? capability.auto_resolution_seconds
              : requestedResolution;
            const accepted = snapshot(rangeSeconds, requestedResolution, resolutionSeconds);
            requests.push({
              rangeSeconds,
              requestedResolution,
              resolutionSeconds,
              sinceGeneration: Number(url.searchParams.get('since_generation')),
              bucketCount: accepted.buckets.length,
              windowStart: accepted.window_start,
              windowEnd: accepted.window_end,
            });
            return {status: 200, json: async () => structuredClone(accepted)};
          };
          class FixtureEventSource {
            addEventListener() {}
            close() {}
          }
          const waitForGeneration = async (rangeSeconds, resolutionSeconds) => {
            await window.__yolomuxTestWaitFor(
              () => {
                const accepted = jsDebugCurrentStatsClientState.client?.controller?.()?.generation?.();
                return accepted?.range_seconds === rangeSeconds
                  && accepted?.resolution_seconds === resolutionSeconds
                  && jsDebugHistoryReadiness.phase === 'ready';
              },
              {timeoutMs: 3000, intervalMs: 10, description: `retained ${rangeSeconds}/${resolutionSeconds} generation`},
            );
          };
          const warm = async (rangeSeconds, resolutionSeconds) => {
            setDebugGraphRange(rangeSeconds);
            if (Number(debugRuntimeState.graphResolutionOverrideSeconds) !== resolutionSeconds) {
              setDebugGraphResolutionOverride(resolutionSeconds);
            }
            await waitForGeneration(rangeSeconds, resolutionSeconds);
          };
          const widen = async (fromRange, fromResolution, toRange, toResolution) => {
            await warm(fromRange, fromResolution);
            const before = requests.length;
            setDebugGraphRange(toRange);
            const loadingState = jsDebugHistoryReadinessStateName();
            await waitForGeneration(toRange, toResolution);
            refreshDebugGraphSurfaces({force: true, deferFocusedControl: false});
            const targetRequests = requests.slice(before).filter(request => request.rangeSeconds === toRange);
            const cpuBuckets = [...jsDebugGraphBuckets.values()]
              .filter(bucket => Number(bucket.cpuCount) > 0)
              .sort((left, right) => Number(left.startMs) - Number(right.startMs));
            const graph = document.querySelector('.js-debug-panel [data-js-debug-graph]');
            const overlay = graph?.querySelector('[data-js-debug-history-overlay]');
            return {
              fromRange,
              toRange,
              toResolution,
              loadingState,
              requests: targetRequests,
              paintedBucketCount: cpuBuckets.length,
              paintedStart: Number(cpuBuckets[0]?.startMs) / 1000,
              paintedEnd: (Number(cpuBuckets.at(-1)?.startMs) + Number(cpuBuckets.at(-1)?.durationMs)) / 1000,
              readiness: jsDebugHistoryReadinessStateName(),
              busy: graph?.getAttribute('aria-busy') || '',
              overlayVisible: Boolean(overlay && !overlay.hidden),
            };
          };

          jsDebugCurrentStatsClientState.client?.stop();
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          debugRuntimeState.graphRangeSeconds = 900;
          debugRuntimeState.graphResolutionOverrideSeconds = 10;
          const client = YOLOmuxStatsCurrent.createBrowserClient({
            fetch: fixtureFetch,
            EventSource: FixtureEventSource,
            clientId: 'retained-widen-fixture',
            savedRange: 900,
            savedResolution: 10,
            controllerOptions: {
              onGeneration: accepted => applyJsDebugCurrentSnapshot(accepted, {forceGraphRefresh: true}),
            },
          });
          jsDebugCurrentStatsClientState.client = client;
          jsDebugCurrentStatsClientState.selectionKey = '900:10';
          jsDebugCurrentStatsClientState.startPromise = null;
          await client.start();
          await waitForGeneration(900, 10);

          const transitions = [];
          transitions.push(await widen(900, 10, 14400, 60));
          transitions.push(await widen(300, 1, 3600, 60));
          transitions.push(await widen(3600, 60, 86400, 300));
          await warm(900, 10);
          await warm(300, 1);
          refreshDebugGraphSurfaces({force: true, deferFocusedControl: false});
          const intervalOptionsAfterRoundTrip = [...document.querySelectorAll('[data-js-debug-resolution-override] option')]
            .map(option => option.textContent.trim());
          const idleRequest = client.controller().buildRequest();
          const legacyFetch = apiFetchJsonQuiet;
          const legacySnapshotRequests = [];
          apiFetchJsonQuiet = async (url, ...args) => {
            if (String(url).startsWith('/api/stats-snapshot?')) legacySnapshotRequests.push(String(url));
            return legacyFetch(url, ...args);
          };
          await Promise.all(Array.from({length: 8}, () => pollJsDebugStatsSample()));
          apiFetchJsonQuiet = legacyFetch;
          client.stop();
          done({transitions, intervalOptionsAfterRoundTrip, idleRequest, legacySnapshotRequests});
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert "1s" in result["intervalOptionsAfterRoundTrip"], result
    assert result["idleRequest"]["since_generation"] > 0, result
    assert result["legacySnapshotRequests"] == [], result
    assert len(result["transitions"]) == 3, result
    for transition in result["transitions"]:
        expected_buckets = transition["toRange"] // transition["toResolution"]
        assert transition["loadingState"] == "loading-older", transition
        assert len(transition["requests"]) == 1, transition
        request = transition["requests"][0]
        assert request["sinceGeneration"] == 0, transition
        assert request["resolutionSeconds"] == transition["toResolution"], transition
        assert request["bucketCount"] == expected_buckets, transition
        assert transition["paintedBucketCount"] == expected_buckets, transition
        assert transition["paintedStart"] == request["windowStart"], transition
        assert transition["paintedEnd"] == request["windowEnd"], transition
        assert transition["readiness"] == "ready", transition
        assert transition["busy"] == "false", transition
        assert transition["overlayVisible"] is False, transition


def _open_real_stats_graph(harness: Any, *, include_finder: bool = True) -> None:
    session = str(harness.runtime.tmux.sessions[0])
    if include_finder:
        harness.load(tabs=("files", "__debug__", session))
    else:
        harness.driver.get(
            f"{harness.base_url}/?sessions={session}&layout=left&tabs=left:__debug__,{session}"
        )
        WebDriverWait(harness.driver, 12, poll_frequency=0.05).until(
            lambda driver: driver.execute_script(
                "return document.readyState === 'complete' "
                "&& typeof syncJsDebugCurrentStatsClient === 'function' "
                "&& document.querySelector('#grid') !== null;"
            )
        )
    harness.switch_session("__debug__")
    result = harness.driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        const graphTab = document.querySelector('[data-js-debug-subtab="graph"]');
        graphTab?.click();
        window.__yolomuxTestWaitFor(() => {
          const client = jsDebugCurrentStatsClientState?.client;
          const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
          return client?.controller?.()?.generation?.() && select ? true : null;
        }, {timeoutMs: 12000, description: 'real YO!stats generation and interval picker'})
          .then(() => done({ok: true}), error => done({error: String(error?.stack || error)}));
        """
    )
    assert result == {"ok": True}, result


def _wait_for_real_stats_fixture_jobs(harness: Any) -> dict[str, Any]:
    def settled(_driver):
        status = harness.runtime.app.job_client.runtime_status()
        queues = status.get("queues") if isinstance(status.get("queues"), dict) else {}
        if status.get("active_records") or any(int(count or 0) for count in queues.values()):
            return False
        return status

    return WebDriverWait(harness.driver, 20, poll_frequency=0.1).until(settled)


def _flush_and_stop_real_stats_graph(harness: Any, *, require_client: bool = False) -> dict[str, Any]:
    try:
        receipt = harness.driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const client = jsDebugCurrentStatsClientState.client;
            const startPromise = jsDebugCurrentStatsClientState.startPromise;
            const lifecycle = {
              hadClient: Boolean(client),
              startPending: Boolean(startPromise),
              hadGeneration: Boolean(client?.controller?.()?.generation?.()),
            };
            const errors = [];
            (async () => {
              try {
                if (startPromise) await startPromise;
              } catch (error) {
                errors.push({phase: 'start', error: String(error?.stack || error)});
              } finally {
                try {
                  client?.stop?.();
                } catch (error) {
                  errors.push({phase: 'stop', error: String(error?.stack || error)});
                }
              }
              try {
                await flushJsDebugCurrentObservations();
              } catch (error) {
                errors.push({phase: 'flush', error: String(error?.stack || error)});
              }
              const barrier = jsDebugCurrentObservationReceiptBarrier();
              done({lifecycle, errors, barrier});
            })().catch(error => done({fatalError: String(error?.stack || error), lifecycle, errors}));
            """
        )
    finally:
        try:
            wait_for_fixture_api_quiescence(harness.driver)
        finally:
            _wait_for_real_stats_fixture_jobs(harness)
    if receipt.get("fatalError") is not None or receipt["errors"]:
        raise AssertionError(f"real stats cleanup failed: {json.dumps(receipt, sort_keys=True)}")
    assert receipt["barrier"]["quiescent"] is True, receipt
    if require_client:
        assert receipt["lifecycle"]["hadClient"] is True, receipt
    return receipt


def _exercise_real_stats_interval_round_trip(harness: Any) -> dict[str, Any]:
    return harness.driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        (async () => {
          const capabilitiesResponse = await fetch('/api/stats-capabilities', {cache: 'no-store'});
          const capabilities = await capabilitiesResponse.json();
          const capability = capabilities.ranges.find(row => row.range_seconds === 300);
          if (capabilitiesResponse.status !== 200 || !capability) {
            throw new Error(`5-minute stats capability missing: ${capabilitiesResponse.status}`);
          }
          const waitForSelection = async rangeSeconds => {
            await window.__yolomuxTestWaitFor(() => {
              const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
              const selection = controller?.selection?.();
              const generation = controller?.generation?.();
              const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
              return selection?.range_seconds === rangeSeconds
                && generation?.range_seconds === rangeSeconds
                && jsDebugHistoryReadiness?.phase === 'ready'
                && select ? true : null;
            }, {timeoutMs: 12000, description: `real YO!stats ${rangeSeconds}s selection`});
          };
          const setRange = async rangeSeconds => {
            const options = [...document.querySelectorAll('.js-debug-panel #js-debug-range-options option')];
            const index = options.findIndex(option => Number(option.dataset.jsDebugRange) === rangeSeconds);
            if (index < 0) throw new Error(`range ${rangeSeconds}s is not offered`);
            const slider = document.querySelector('.js-debug-panel [data-js-debug-range-slider]');
            slider.focus({preventScroll: true});
            slider.value = String(index);
            slider.dispatchEvent(new Event('input', {bubbles: true}));
            slider.dispatchEvent(new Event('change', {bubbles: true}));
            await waitForSelection(rangeSeconds);
          };
          const setResolution = async resolutionSeconds => {
            const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
            if (![...select.options].some(option => Number(option.value) === resolutionSeconds)) {
              throw new Error(`resolution ${resolutionSeconds}s is not offered`);
            }
            select.focus({preventScroll: true});
            select.value = String(resolutionSeconds);
            select.dispatchEvent(new Event('change', {bubbles: true}));
            await window.__yolomuxTestWaitFor(() => {
              const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
              const generation = controller?.generation?.();
              return controller?.selection?.()?.resolution === resolutionSeconds
                && generation?.requested_resolution === resolutionSeconds
                && jsDebugHistoryReadiness?.phase === 'ready' ? true : null;
            }, {timeoutMs: 12000, description: `real YO!stats ${resolutionSeconds}s resolution`});
          };
          const pickerState = label => {
            const controller = jsDebugCurrentStatsClientState.client.controller();
            const generation = controller.generation();
            const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
            return {
              label,
              options: [...select.options].map(option => option.value),
              selected: select.value,
              selection: controller.selection(),
              generation: {
                range_seconds: generation.range_seconds,
                requested_resolution: generation.requested_resolution,
                resolution_seconds: generation.resolution_seconds,
                buckets: generation.buckets.length,
                source_generation: generation.source_generation,
              },
            };
          };

          await setRange(300);
          await setResolution(1);
          const before = pickerState('before');
          await setRange(3600);
          const widened = pickerState('widened');
          await setRange(300);
          const returned = pickerState('returned');
          done({
            expected: ['0', ...capability.explicit_resolution_seconds.map(String)],
            before,
            widened,
            returned,
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )


def _real_stats_picker_state(harness: Any) -> dict[str, Any]:
    return harness.driver.execute_script(
        """
        const controller = jsDebugCurrentStatsClientState.client.controller();
        const generation = controller.generation();
        const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
        return {
          options: [...select.options].map(option => option.value),
          selected: select.value,
          range_seconds: generation.range_seconds,
          resolution_seconds: generation.resolution_seconds,
          buckets: generation.buckets.length,
          source_generation: generation.source_generation,
        };
        """
    )


def _wait_for_ring_publication(harness: Any, source_generation: int) -> dict[str, Any]:
    client = harness.runtime.app.stats_current_client

    def published(_driver):
        status = client.status()
        ring = status.get("ring_writer") if isinstance(status, dict) else None
        if not isinstance(ring, dict):
            return False
        if int(ring.get("publications") or 0) < 1:
            return False
        if int(ring.get("last_source_generation") or 0) < source_generation:
            return False
        return status

    return WebDriverWait(harness.driver, 20, poll_frequency=0.1).until(published)


def _cpu_graph_state(harness: Any) -> dict[str, Any]:
    return harness.driver.execute_script(
        r"""
        const graph = document.querySelector('.js-debug-panel [data-js-debug-graph]');
        const cpuChart = document.querySelector('.js-debug-panel [data-js-debug-chart="cpu"]');
        const cpuAxisMax = Number(cpuChart?.dataset?.jsDebugChartAxisMax || 0);
        const lines = [...document.querySelectorAll('.js-debug-panel polyline[data-js-debug-series]')];
        const cpuLines = lines.filter(line => line.dataset.jsDebugSeries === 'systemCpu');
        const points = cpuLines.flatMap(line => String(line.getAttribute('points') || '')
          .trim().split(/\s+/).filter(Boolean).map(point => {
            const [x, y] = point.split(',').map(Number);
            return {x, y, percent: (116 - y) / 108 * cpuAxisMax};
          }));
        const overlay = graph?.querySelector('[data-js-debug-history-overlay]');
        const warnings = (typeof jsDebugEvents !== 'undefined' ? jsDebugEvents : [])
          .filter(event => event?.type === 'stats_history' && event?.level === 'warning')
          .map(event => String(event.message || ''));
        return {
          lineCount: lines.length,
          seriesKeys: [...new Set(lines.map(line => line.dataset.jsDebugSeries))],
          cpuPointCount: points.length,
          cpuAxisMax,
          points,
          historyState: graph?.dataset?.jsDebugHistoryState || '',
          busy: graph?.getAttribute('aria-busy') || '',
          overlayHidden: overlay?.hidden === true,
          warnings,
        };
        """
    )


def _assert_seeded_cpu_graph(state: dict[str, Any]) -> None:
    assert state["lineCount"] > 0, state
    assert "systemCpu" in state["seriesKeys"], state
    assert state["cpuPointCount"] >= CPU_GRAPH_SAMPLE_COUNT - 1, state
    assert state["cpuAxisMax"] == 50, state
    assert all(8 <= point["y"] <= 116 for point in state["points"]), state
    assert all(abs(point["percent"] - CPU_GRAPH_PERCENT) < 0.2 for point in state["points"]), state
    assert state["historyState"] == "ready", state
    assert state["busy"] == "false", state
    assert state["overlayHidden"] is True, state
    assert state["warnings"] == [], state


def _wait_for_cpu_points(harness: Any) -> dict[str, Any] | bool:
    state = _cpu_graph_state(harness)
    return state if state["cpuPointCount"] >= CPU_GRAPH_SAMPLE_COUNT - 1 else False


def _wait_for_stats_warning(harness: Any) -> dict[str, Any] | bool:
    state = _cpu_graph_state(harness)
    return state if state["warnings"] else False


def test_real_stats_cleanup_settles_start_and_stops_before_final_flush(authenticated_e2e_browser: Any) -> None:
    wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver, timeout=20)
    _open_real_stats_graph(authenticated_e2e_browser, include_finder=False)
    wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver, timeout=20)
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    authenticated_e2e_browser.driver.execute_script(
        """
        globalThis.__statsCleanupFixture = {
          client: jsDebugCurrentStatsClientState.client,
          startPromise: jsDebugCurrentStatsClientState.startPromise,
          flush: flushJsDebugCurrentObservations,
          markers: [],
        };
        jsDebugCurrentStatsClientState.client = {
          stop() { globalThis.__statsCleanupFixture.markers.push('stop'); },
          controller() { return null; },
        };
        jsDebugCurrentStatsClientState.startPromise = {
          then(_resolve, reject) {
            globalThis.__statsCleanupFixture.markers.push('start-rejected');
            reject(new Error('pending start rejection'));
          },
        };
        flushJsDebugCurrentObservations = () => {
          globalThis.__statsCleanupFixture.markers.push('flush-rejected');
          return Promise.reject(new Error('final flush rejection'));
        };
        """
    )
    try:
        with pytest.raises(AssertionError) as cleanup_error:
            _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
        assert "pending start rejection" in str(cleanup_error.value), cleanup_error.value
        assert "final flush rejection" in str(cleanup_error.value), cleanup_error.value
        markers = authenticated_e2e_browser.driver.execute_script(
            "return [...globalThis.__statsCleanupFixture.markers];"
        )
        assert markers == ["start-rejected", "stop", "flush-rejected"], markers
    finally:
        authenticated_e2e_browser.driver.execute_script(
            """
            jsDebugCurrentStatsClientState.client = globalThis.__statsCleanupFixture.client;
            jsDebugCurrentStatsClientState.startPromise = globalThis.__statsCleanupFixture.startPromise;
            flushJsDebugCurrentObservations = globalThis.__statsCleanupFixture.flush;
            delete globalThis.__statsCleanupFixture;
            """
        )
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)


def test_hidden_stats_panel_receives_push_then_opens_from_cached_generation(
    authenticated_e2e_browser: Any,
    request: pytest.FixtureRequest,
) -> None:
    runtime = authenticated_e2e_browser.runtime
    client = runtime.app.stats_current_client
    assert client.ensure_started() is True
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    _wait_for_ring_publication(authenticated_e2e_browser, 0)
    request.addfinalizer(
        lambda: _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    )
    _register_stats_route_instrumentation(authenticated_e2e_browser)
    authenticated_e2e_browser.driver.execute_script(
        "localStorage.setItem('yolomux.stats.ui_preferences.v1', "
        "JSON.stringify({rangeSeconds: 300, resolutionOverrideSeconds: 1}));"
    )
    session = str(runtime.tmux.sessions[0])
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )
    authenticated_e2e_browser.switch_session(session)
    hidden = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
            const generation = controller?.generation?.();
            if (!generation || jsDebugStatsPanelVisible()) return false;
            return {
              cacheGeneration: generation.cache_generation,
              sourceGeneration: generation.source_generation,
              rangeSeconds: generation.range_seconds,
              resolutionSeconds: generation.resolution_seconds,
              paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
              requestCounts: {...globalThis.__yolomuxStatsRouteCounts},
            };
            """
        )
    )
    assert hidden["rangeSeconds"] == 300 and hidden["resolutionSeconds"] == 1, hidden
    assert hidden["requestCounts"]["/api/stats-capabilities"] == 1, hidden
    assert hidden["requestCounts"]["/api/stats-snapshot"] >= 1, hidden
    assert hidden["requestCounts"]["/api/stats-retry"] == 0, hidden
    assert hidden["requestCounts"]["/api/stats-stream"] == 1, hidden
    observed_at = int(time.time())
    cadence_started = time.monotonic()
    appended = client.append(observations=(storage.Observation(
        f"hidden-panel-push-{observed_at}",
        "cpu",
        "hidden-panel-push",
        observed_at,
        "hidden-panel-push",
        1,
        {"process_percent": 19.0, "system_percent": 29.0},
    ),))
    assert appended.get("ok") is True, appended
    pushed = WebDriverWait(authenticated_e2e_browser.driver, 6, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            if (!generation || generation.source_generation < arguments[0]) return false;
            return {
              cacheGeneration: generation.cache_generation,
              sourceGeneration: generation.source_generation,
              paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
            };
            """,
            int(appended["source_generation"]),
        )
    )
    assert pushed["paintedGenerationKey"] == hidden["paintedGenerationKey"], pushed
    assert _stats_route_counts(authenticated_e2e_browser) == hidden["requestCounts"], pushed

    hidden_updates = [pushed]
    for offset in range(1, 4):
        append_deadline = cadence_started + (offset * 1.05)
        WebDriverWait(authenticated_e2e_browser.driver, 5, poll_frequency=0.05).until(
            lambda _driver, deadline=append_deadline: time.monotonic() >= deadline
        )
        appended = client.append(observations=(storage.Observation(
            f"hidden-panel-push-{observed_at + offset}",
            "cpu",
            "hidden-panel-push",
            observed_at + offset,
            "hidden-panel-push",
            1,
            {
                "process_percent": 19.0 + offset,
                "system_percent": 29.0 + offset,
            },
        ),))
        assert appended.get("ok") is True, appended
        previous = hidden_updates[-1]
        advanced = WebDriverWait(
            authenticated_e2e_browser.driver,
            6,
            poll_frequency=0.05,
        ).until(
            lambda driver: driver.execute_script(
                """
                const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
                if (!generation
                    || generation.source_generation < arguments[0]
                    || generation.cache_generation <= arguments[1]) return false;
                return {
                  cacheGeneration: generation.cache_generation,
                  sourceGeneration: generation.source_generation,
                  paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
                  panelVisible: jsDebugStatsPanelVisible(),
                };
                """,
                int(appended["source_generation"]),
                previous["cacheGeneration"],
            )
        )
        assert advanced["paintedGenerationKey"] == hidden["paintedGenerationKey"], advanced
        assert advanced["panelVisible"] is False, advanced
        assert _stats_route_counts(authenticated_e2e_browser) == hidden["requestCounts"], advanced
        hidden_updates.append(advanced)

    continued = authenticated_e2e_browser.driver.execute_script(
        """
        const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
        const stalls = jsDebugEvents.filter(event =>
          String(event?.route || '') === '/api/stats-stream'
          && /YO!stats (stream|data).*(stalled|missing|unavailable)/i.test(String(event?.message || '')));
        return {
          cacheGeneration: generation?.cache_generation || 0,
          paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
          panelVisible: jsDebugStatsPanelVisible(),
          stalls: stalls.map(event => String(event.message || '')),
          requestCounts: {...globalThis.__yolomuxStatsRouteCounts},
        };
        """
    )
    assert [update["cacheGeneration"] for update in hidden_updates] == sorted({
        update["cacheGeneration"] for update in hidden_updates
    }), hidden_updates
    assert continued["cacheGeneration"] == hidden_updates[-1]["cacheGeneration"], continued
    assert continued["paintedGenerationKey"] == hidden["paintedGenerationKey"], continued
    assert continued["panelVisible"] is False, continued
    assert continued["stalls"] == [], continued
    assert continued["requestCounts"] == hidden["requestCounts"], continued
    before_open_counts = _stats_route_counts(authenticated_e2e_browser)

    authenticated_e2e_browser.switch_session("__debug__")
    opened = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
            const generation = controller?.generation?.();
            const expected = jsDebugCurrentStatsGenerationKey(generation);
            if (!jsDebugStatsPanelVisible() || !expected || jsDebugCurrentStatsClientState.paintedGenerationKey !== expected) return false;
            return {
              cacheGeneration: generation.cache_generation,
              sourceGeneration: generation.source_generation,
              paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
              requestCounts: {...globalThis.__yolomuxStatsRouteCounts},
            };
            """
        )
    )
    assert opened["cacheGeneration"] == continued["cacheGeneration"], opened
    assert opened["sourceGeneration"] >= pushed["sourceGeneration"], opened
    assert opened["requestCounts"] == before_open_counts, opened
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)


def test_stats_stream_failure_is_durable_latched_and_recovers_only_after_a_real_push(
    authenticated_e2e_browser: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = authenticated_e2e_browser.runtime
    client = runtime.app.stats_current_client
    assert client.ensure_started() is True
    original_capabilities = runtime.app.stats_current_http.capabilities
    original_delta_stream = runtime.app.stats_current_http.delta_stream

    def invalid_capabilities() -> dict[str, object]:
        payload = dict(original_capabilities())
        payload["invalid_review_field"] = True
        return payload

    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", invalid_capabilities)
    _register_stats_route_instrumentation(authenticated_e2e_browser)
    session = str(runtime.tmux.sessions[0])
    authenticated_e2e_browser.driver.execute_script(
        "localStorage.setItem('yolomux.stats.ui_preferences.v1', "
        "JSON.stringify({rangeSeconds: 300, resolutionOverrideSeconds: 1}));"
    )
    install_live_runtime_boot_error_tracker(authenticated_e2e_browser.driver)
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:files,__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )

    failed = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const failures = jsDebugEvents.filter(event => event?.type === 'stats_history'
              && event?.level === 'warning' && event?.route === '/api/stats-stream'
              && String(event?.message || '').includes('stats capabilities fields are not exact'));
            const capabilities = globalThis.__yolomuxStatsRouteCounts?.['/api/stats-capabilities'] || 0;
            if (failures.length !== 1 || capabilities < 2) return false;
            const event = failures[0];
            return {
              count: failures.length,
              signature: event.signature,
              source: event.route,
              message: event.message,
              marked: Object.prototype.hasOwnProperty.call(event, 'provenance'),
              capabilities,
            };
            """
        )
    )
    assert failed["source"] == "/api/stats-stream", failed
    assert failed["marked"] is False, failed

    receipt = WebDriverWait(authenticated_e2e_browser.driver, 18, poll_frequency=0.2).until(
        lambda driver: driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            Promise.resolve(typeof flushJsDebugCurrentObservations === 'function'
              ? flushJsDebugCurrentObservations() : undefined).then(() => {
                const barrier = jsDebugCurrentObservationReceiptBarrier();
                done(barrier.quiescent && barrier.accepted > 0 ? barrier : false);
              }, () => done(false));
            """
        )
    )
    assert receipt["quiescent"] is True and receipt["accepted"] > 0, receipt

    retained = WebDriverWait(authenticated_e2e_browser.driver, 18, poll_frequency=0.2).until(
        lambda _driver: _browser_failure_fingerprint(client, failed["signature"], 1)
    )
    assert retained["fingerprint"]["signature"] == failed["signature"], retained
    assert retained["fingerprint"]["kind"] == "warning", retained
    assert retained["fingerprint"]["provenance"] == "unknown", retained

    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", original_capabilities)
    healthy = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            return generation ? {
              sourceGeneration: generation.source_generation,
              rangeSeconds: generation.range_seconds,
              resolutionSeconds: generation.resolution_seconds,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
            } : false;
            """
        )
    )
    assert healthy["rangeSeconds"] == 300 and healthy["resolutionSeconds"] == 1, healthy
    assert healthy["failureLatched"] is True, healthy
    healthy_counts = _stats_route_counts(authenticated_e2e_browser)
    assert set(healthy_counts) == {
        "/api/stats-capabilities", "/api/stats-snapshot", "/api/stats-retry", "/api/stats-stream",
    }, healthy_counts
    assert healthy_counts["/api/stats-capabilities"] >= 2, healthy_counts
    assert healthy_counts["/api/stats-snapshot"] >= 1, healthy_counts
    assert healthy_counts["/api/stats-retry"] == 0, healthy_counts
    assert healthy_counts["/api/stats-stream"] >= 1, healthy_counts
    stream_base = authenticated_e2e_browser.driver.execute_script(
        """
        const client = jsDebugCurrentStatsClientState?.client;
        client?.stop?.();
        const controller = client?.controller?.();
        controller?.setVisible?.(true);
        const generation = controller?.generation?.();
        const presentation = controller?.presentation?.();
        return generation && presentation ? {
          generation: structuredClone(generation),
          revision: presentation.delta_revision,
        } : null;
        """
    )
    assert stream_base and stream_base["generation"]["buckets"], stream_base
    generation = stream_base["generation"]
    streamed_cache_generation = int(generation["cache_generation"]) + 1
    streamed_source_generation = int(generation["source_generation"]) + 1
    streamed_revision = int(stream_base["revision"]) + 1
    streamed_delta = json.dumps({
        "protocol_version": 2,
        "range_seconds": generation["range_seconds"],
        "resolution_seconds": generation["resolution_seconds"],
        "source_generation": streamed_source_generation,
        "base_cache_generation": generation["cache_generation"],
        "cache_generation": streamed_cache_generation,
        "revision": streamed_revision,
        "buckets": [generation["buckets"][-1]],
        "no_data": [],
        "tombstones": [],
        "cost_report": generation["cost_report"],
    }, separators=(",", ":")).encode("utf-8")
    streamed = False

    def accepted_delta_stream(
        _raw_query: str,
        *,
        authenticated_username: str,
    ) -> stats_current_http.DeltaStreamResult:
        nonlocal streamed
        assert authenticated_username
        if not streamed:
            streamed = True
            return stats_current_http.DeltaStreamResult(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "content_type": "application/json",
                    "base_cache_generation": generation["cache_generation"],
                    "cache_generation": streamed_cache_generation,
                    "revision": streamed_revision,
                },
                streamed_delta,
            )
        return stats_current_http.DeltaStreamResult(
            HTTPStatus.NOT_MODIFIED,
            {"ok": True, "not_modified": True, "cache_generation": streamed_cache_generation},
        )

    monkeypatch.setattr(runtime.app.stats_current_http, "delta_stream", accepted_delta_stream)
    accepted_push = authenticated_e2e_browser.driver.execute_async_script(
        """
            const done = arguments[arguments.length - 1];
            const client = jsDebugCurrentStatsClientState?.client;
            const controller = client?.controller?.();
            const query = new URLSearchParams({
              range_seconds: String(arguments[0]),
              resolution_seconds: String(arguments[1]),
              client_id: 'browser-durability-push',
              after_cache_generation: String(arguments[2]),
              after_revision: String(arguments[3]),
            });
            const source = new EventSource(`/api/stats-stream?${query}`, {withCredentials: true});
            let settled = false;
            const finish = value => {
              if (settled) return;
              settled = true;
              clearTimeout(watchdog);
              source.close();
              done(value);
            };
            const watchdog = setTimeout(() => finish({accepted: false, reason: 'streamed delta timeout'}), 5000);
            source.addEventListener('delta', event => {
              let accepted = false;
              let reason = '';
              try {
                accepted = controller.acceptDelta(JSON.parse(event.data));
              } catch (error) {
                reason = String(error?.message || error);
              }
              finish({
                accepted,
                reason,
                sourceGeneration: controller.generation()?.source_generation,
                failureLatched: jsDebugCurrentStatsClientState.failureLatched,
              });
            });
            source.addEventListener('error', () => finish({accepted: false, reason: 'streamed delta error'}));
        """,
        generation["range_seconds"],
        generation["resolution_seconds"],
        generation["cache_generation"],
        stream_base["revision"],
    )
    assert accepted_push["accepted"] is True, accepted_push
    assert accepted_push["sourceGeneration"] == streamed_source_generation, accepted_push
    assert accepted_push["failureLatched"] is False, accepted_push
    accepted_failures = authenticated_e2e_browser.driver.execute_script(
        """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            const failures = jsDebugEvents.filter(event => event?.type === 'stats_history'
              && event?.level === 'warning' && event?.route === '/api/stats-stream');
            return {
              sourceGeneration: generation.source_generation,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
              failures: failures.map(event => ({
                signature: event.signature,
                message: event.message,
                source: event.route,
                marked: Object.prototype.hasOwnProperty.call(event, 'provenance'),
              })),
            };
        """
    )
    assert accepted_failures["failureLatched"] is False, accepted_failures

    def unavailable_delta_stream(
        _raw_query: str,
        *,
        authenticated_username: str,
    ) -> stats_current_http.DeltaStreamResult:
        assert authenticated_username
        return stats_current_http.DeltaStreamResult(
            HTTPStatus.FAILED_DEPENDENCY,
            {"ok": False, "status": "unavailable", "reason": "forced graph stream failure"},
        )

    monkeypatch.setattr(runtime.app.stats_current_http, "delta_stream", unavailable_delta_stream)
    authenticated_e2e_browser.driver.execute_script(
        "void jsDebugCurrentStatsClientState.client.start();"
    )
    recovered = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            const failures = jsDebugEvents.filter(event => event?.type === 'stats_history'
              && event?.level === 'warning' && event?.route === '/api/stats-stream');
            const later = [...failures].reverse().find(event => event.message !== arguments[0]);
            if (!generation || !later) return false;
            return {
              sourceGeneration: generation.source_generation,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
              later: {
                signature: later.signature,
                message: later.message,
                source: later.route,
                marked: Object.prototype.hasOwnProperty.call(later, 'provenance'),
              },
            };
            """,
            failed["message"],
        )
    )
    monkeypatch.setattr(runtime.app.stats_current_http, "delta_stream", original_delta_stream)
    assert recovered["later"]["source"] == "/api/stats-stream", recovered
    assert recovered["later"]["marked"] is False, recovered
    assert recovered["failureLatched"] is True, recovered
    recovered_receipt = WebDriverWait(authenticated_e2e_browser.driver, 18, poll_frequency=0.2).until(
        lambda driver: driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            Promise.resolve(flushJsDebugCurrentObservations()).then(() => {
              const barrier = jsDebugCurrentObservationReceiptBarrier();
              done(barrier.quiescent ? barrier : false);
            }, () => done(false));
            """
        )
    )
    assert recovered_receipt["quiescent"] is True, recovered_receipt
    expected_failures = authenticated_e2e_browser.driver.execute_script(
        """
        return jsDebugEvents.filter(event => event?.type === 'stats_history'
          && event?.level === 'warning' && event?.route === '/api/stats-stream');
        """,
    )
    assert [
        {
            "signature": event["signature"],
            "source": event["route"],
            "message": event["message"],
            "marked": "provenance" in event,
        }
        for event in expected_failures
    ] == [
        {"signature": failed["signature"], "source": failed["source"], "message": failed["message"], "marked": False},
        {"signature": recovered["later"]["signature"], "source": recovered["later"]["source"], "message": recovered["later"]["message"], "marked": False},
    ]
    retired = acknowledge_and_consume_only_expected_js_debug_failures(
        authenticated_e2e_browser.driver,
        expected_failures,
    )
    assert len(retired) == 2
    chrome_entries = read_browser_console_log(authenticated_e2e_browser.driver)
    chrome_failures = [
        entry for entry in chrome_entries
        if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}
    ]
    assert chrome_failures == [], chrome_failures


def _select_one_second_five_minute_graph(harness: Any) -> None:
    result = harness.driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        setDebugGraphRange(300);
        setDebugGraphResolutionOverride(1);
        window.__yolomuxTestWaitFor(() => {
          const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
          const generation = controller?.generation?.();
          return generation?.range_seconds === 300
            && generation?.requested_resolution === 1
            && generation?.resolution_seconds === 1
            && jsDebugHistoryReadiness?.phase === 'ready' ? true : null;
        }, {timeoutMs: 12000, description: 'real YO!stats five-minute one-second generation'})
          .then(() => done({ok: true}), error => done({error: String(error?.stack || error)}));
        """
    )
    assert result == {"ok": True}, result


def test_real_stats_cpu_value_round_trips_through_rpc_and_rendered_svg(
    authenticated_e2e_browser: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = authenticated_e2e_browser.runtime
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    runtime.app.stats_current_runtime.stop()
    client = runtime.app.stats_current_client
    assert client.ensure_started() is True
    now = int(time.time())
    first_observed_at = now - CPU_GRAPH_SAMPLE_COUNT - 2
    source = "cpu:positive-graph-gate"
    epoch = "positive-graph-gate"
    appended = client.append(
        observations=tuple(
            storage.Observation(
                f"positive-graph-{index}",
                "cpu",
                source,
                first_observed_at + index,
                epoch,
                1,
                {"process_percent": 11.0, "system_percent": CPU_GRAPH_PERCENT},
            )
            for index in range(CPU_GRAPH_SAMPLE_COUNT)
        ),
        coverage_epochs=(storage.CoverageEpoch(
            "cpu",
            source,
            epoch,
            first_observed_at,
            None,
            1,
            1,
        ),),
    )
    assert appended.get("ok") is True, appended
    assert int(appended.get("accepted") or 0) == CPU_GRAPH_SAMPLE_COUNT + 1, appended
    _wait_for_ring_publication(authenticated_e2e_browser, int(appended["source_generation"]))

    _open_real_stats_graph(authenticated_e2e_browser)
    _select_one_second_five_minute_graph(authenticated_e2e_browser)
    positive = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda _driver: _wait_for_cpu_points(authenticated_e2e_browser)
    )
    _assert_seeded_cpu_graph(positive)

    original_capabilities = runtime.app.stats_current_http.capabilities

    def capabilities_with_unknown_field() -> dict[str, object]:
        payload = dict(original_capabilities())
        payload["negative_control_unknown"] = True
        return payload

    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", capabilities_with_unknown_field)
    session = str(runtime.tmux.sessions[0])
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:files,__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )
    authenticated_e2e_browser.switch_session("__debug__")
    negative = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda _driver: _wait_for_stats_warning(authenticated_e2e_browser)
    )
    assert negative["lineCount"] == 0, negative
    assert any("stats capabilities fields are not exact" in warning for warning in negative["warnings"]), negative
    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", original_capabilities)
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )
    assert_live_runtime_boot_healthy(
        authenticated_e2e_browser.driver,
        authenticated_e2e_browser.test_id,
        timeout=12,
    )
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser)


def test_real_stats_interval_options_return_after_range_round_trip_and_reload(authenticated_e2e_browser: Any) -> None:
    client = authenticated_e2e_browser.runtime.app.stats_current_client
    assert client.ensure_started() is True
    observed_at = int(time.time())
    appended = client.append(observations=(storage.Observation(
        "stats-interval-real-browser",
        "cpu",
        "stats-interval-fixture",
        observed_at,
        "stats-interval-fixture",
        1,
        {"process_percent": 17.0, "system_percent": 23.0},
    ),))
    assert appended.get("ok") is True, appended

    _open_real_stats_graph(authenticated_e2e_browser)
    round_trip = _exercise_real_stats_interval_round_trip(authenticated_e2e_browser)
    assert round_trip.get("error") is None, round_trip
    assert round_trip["before"]["options"] == round_trip["expected"], round_trip
    assert round_trip["before"]["selected"] == "1", round_trip
    assert "1" not in round_trip["widened"]["options"], round_trip
    assert round_trip["widened"]["selected"] == "0", round_trip
    assert round_trip["widened"]["generation"]["requested_resolution"] == "AUTO", round_trip
    assert round_trip["widened"]["generation"]["resolution_seconds"] == 60, round_trip
    assert round_trip["returned"]["options"] == round_trip["before"]["options"], round_trip
    assert round_trip["returned"]["selected"] == "0", round_trip
    assert round_trip["returned"]["generation"]["requested_resolution"] == "AUTO", round_trip
    assert round_trip["returned"]["generation"]["resolution_seconds"] == 1, round_trip
    assert round_trip["returned"]["generation"]["buckets"] == 300, round_trip

    _open_real_stats_graph(authenticated_e2e_browser, include_finder=False)
    persisted = _real_stats_picker_state(authenticated_e2e_browser)
    assert persisted["range_seconds"] == 300, persisted
    assert persisted["options"] == round_trip["expected"], {"round_trip": round_trip, "persisted": persisted}
    assert persisted["selected"] == "0", persisted
    assert persisted["resolution_seconds"] == 1 and persisted["buckets"] == 300, persisted
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser)

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retained YO!stats range-transition regressions."""

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403


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
            [3600, 10, [10, 60, 300]],
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
            ['input', 'cache_read', 'cache_write', 'output', 'other'].map(key => [
              key,
              {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
            ]),
          );
          const costReport = () => ({
            schema_version: 2,
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
            if (Number(jsDebugGraphResolutionOverrideSeconds) !== resolutionSeconds) {
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
          jsDebugGraphRangeSeconds = 900;
          jsDebugGraphResolutionOverrideSeconds = 10;
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
          transitions.push(await widen(300, 1, 3600, 10));
          transitions.push(await widen(3600, 10, 86400, 300));
          client.stop();
          done({transitions});
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
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

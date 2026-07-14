# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import json
import time

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from yolomux_lib import statsd
from yolomux_lib.local_services import stats_store


def _morning_after_protocol_history(tmp_path):
    """Build retained before/after-sleep history through the real v20 engine."""

    now = int(time.time() // 60 * 60)
    retained_start = now - (24 * 60 * 60)
    sleep_start = now - (10 * 60 * 60)
    wake_time = now - (2 * 60 * 60)
    database = tmp_path / "morning-after" / "stats.sqlite3"
    service = statsd.PersistentStatsService(
        tmp_path / "morning-after" / "statsd.sock",
        database,
    )
    sequence = 0
    try:
        protocol = service.handle({
            "action": "ping",
            "protocol_version": statsd.STATSD_PROTOCOL_VERSION,
        })
        assert protocol["version"] == statsd.STATSD_PROTOCOL_VERSION == 21
        for segment_start, segment_end, bucket_seconds in (
            (retained_start, sleep_start, 600),
            (wake_time, now, 60),
        ):
            for bucket_start in range(segment_start, segment_end, bucket_seconds):
                sequence += 1
                duration = min(bucket_seconds, segment_end - bucket_start)
                bucket = stats_store.empty_bucket(bucket_start, duration)
                bucket.update({
                    "sequence": sequence,
                    "server_sequence": sequence,
                    "cpu_total_percent": 25.0,
                    "cpu_count": 1.0,
                    "system_cpu_total_percent": 40.0,
                    "system_cpu_count": 1.0,
                    "run_agent_total": 1.0,
                    "idle_agent_total": 1.0,
                    "active_agent_total": 1.0,
                    "inactive_agent_total": 1.0,
                    "agent_activity_samples": 1.0,
                    "tokens_per_agent_total": 120.0,
                    "agent_token_samples": 1.0,
                })
                bucket["agent_token_rates"] = {
                    "8881|0|codex": {
                        "label": "8881:0:codex",
                        "total": 120.0,
                        "samples": 1.0,
                        "tokens": 120.0,
                        "seconds": float(duration),
                        "source": "transcript",
                        "model_rates": {
                            "gpt-5.6-sol": {
                                "total": 120.0,
                                "samples": 1.0,
                                "tokens": 120.0,
                                "seconds": float(duration),
                            },
                        },
                    },
                }
                bucket["cost_summary"] = {
                    "components": [{
                        "event_id": f"usage-{bucket_start}",
                        "provider": "openai",
                        "model": "gpt-5.6-sol",
                        "direction": "output",
                        "modality": "text",
                        "cache_role": "none",
                        "unit": "tokens",
                        "quantity": 120.0,
                        "micro_usd": 120,
                        "lower_micro_usd": 120,
                        "upper_micro_usd": 120,
                        "priced": True,
                        "source": "Codex transcript",
                        "timestamp": str(bucket_start),
                    }],
                    "total_micro_usd": 120,
                    "priced_components": 1,
                    "unpriced_components": 0,
                    "lower_bound": False,
                }
                service.store.upsert_bucket(bucket)

        # Advance the production writer through two sampler owner epochs. No
        # marker is written during the virtual eight-hour suspend.
        for family in ("cpu", "agent_status", "agent_tokens"):
            for sample_time, cadence, epoch, generation in (
                (retained_start, sleep_start - retained_start, "before-sleep", 1),
                (wake_time, now - wake_time, "after-wake", 2),
            ):
                marker = service.handle({
                    "action": "merge_server_records",
                    "protocol_version": statsd.STATSD_PROTOCOL_VERSION,
                    "records": [{
                        "time": sample_time,
                        "_stats_coverage": {
                            "family": family,
                            "cadence_seconds": cadence,
                            "epoch_id": f"{epoch}:{family}",
                            "owner_generation": generation,
                        },
                    }],
                    "now": sample_time,
                    "compact": False,
                    "refresh_rollups": False,
                })
                assert marker["ok"] is True
                assert marker["version"] == statsd.STATSD_PROTOCOL_VERSION

        histories = {}
        for range_seconds in (3600, 4 * 3600, 24 * 3600):
            history = service.handle({
                "action": "history",
                "protocol_version": statsd.STATSD_PROTOCOL_VERSION,
                "start": now - range_seconds,
                "end": 0,
                "resolution_seconds": 1,
                "max_points": 6000,
                "client_id": "morning-after-browser",
                "token_resolution_seconds": 60,
                "token_history_start": now - range_seconds,
                "token_history_end": 0,
            })
            assert history["ok"] is True
            assert history["coverage"]["complete"] is (range_seconds == 3600)
            histories[str(range_seconds)] = history
    finally:
        service.store.close()

    return {
        "now": now,
        "sleepStart": sleep_start,
        "wakeTime": wake_time,
        "histories": histories,
    }


def _install_morning_after_fetch(browser, fixture):
    browser.execute_script(
        """
        const fixture = arguments[0];
        stopJsDebugStatsPolling();
        Date.now = () => Number(fixture.now) * 1000;
        window.__morningAfterFixture = fixture;
        window.__morningAfterFetchCounts = {};
        const originalFetch = window.fetch;
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), 'https://localhost');
          if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
          const requestedStart = Number(url.searchParams.get('history_start'));
          const requestedSpan = Math.max(1, Number(fixture.now) - requestedStart);
          const key = Object.keys(fixture.histories).sort(
            (left, right) => Math.abs(Number(left) - requestedSpan) - Math.abs(Number(right) - requestedSpan)
          )[0];
          window.__morningAfterFetchCounts[key] = Number(window.__morningAfterFetchCounts[key] || 0) + 1;
          return jsonResponse({
            ok: true,
            time: Number(fixture.now),
            pid: 4242,
            uptime_seconds: 7200,
            cpu_percent: 25,
            system_cpu_percent: 40,
            rss_bytes: 64 * 1024 * 1024,
            history: fixture.histories[key],
          });
        };
        """,
        fixture,
    )


def _run_morning_after_range(browser, range_seconds):
    return browser.execute_async_script(
        """
        const rangeSeconds = Number(arguments[0]);
        const done = arguments[arguments.length - 1];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugEvents();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          setDebugGraphRange(rangeSeconds, {render: false});
          renderDebugPanels({force: true});
          stopJsDebugStatsPolling();
          await pollJsDebugStatsSample({forceGraphRefresh: true});
          await window.__yolomuxTestHelpers.settle(2);
          const readiness = jsDebugHistoryReadinessSnapshot();
          const graph = document.querySelector('[data-js-debug-graph]');
          const modelDimension = graph?.querySelector('[data-js-debug-model-token-dimension-select]');
          if (modelDimension) {
            // This fixture predates attributed input/cache atoms. Exercise the
            // exact generated-output history that it actually contains.
            modelDimension.value = 'output';
            modelDimension.dispatchEvent(new Event('change', {bubbles: true}));
          }
          setDebugGraphChartVisible('modelTokens', true);
          refreshDebugGraphElement(graph, {force: true});
          await window.__yolomuxTestHelpers.settle(2);
          const domain = debugGraphDomain();
          const gaps = key => debugGraphHistoryCoverageGapRuns({key}, domain).map(range => ({
            start: Math.round(range.startMs / 1000),
            end: Math.round(range.endMs / 1000),
          }));
          const storeSpans = key => (readiness.storeCoverageIntervals[key] || []).map(interval => ({
            start: Math.round(interval.startSeconds),
            end: Math.round(interval.endSeconds),
          }));
          const summary = debugGraphBucketSummary();
          const messages = jsDebugEvents.map(event => String(event.message || ''));
          done({
            phase: readiness.phase,
            error: readiness.error,
            attempts: readiness.attemptCount,
            gaps: {
              status: gaps('activity'),
              tokens: gaps('agentTokens'),
              cost: gaps('modelTokens'),
            },
            stores: {
              status: storeSpans('agent_status'),
              tokens: storeSpans('agent_tokens'),
              cost: storeSpans('cost'),
            },
            overlays: {
              status: graph.querySelectorAll('[data-js-debug-chart="activity"] [data-js-debug-history-no-data-range], [data-js-debug-chart="activity"] [data-js-debug-agent-status-no-data-range]').length,
              tokens: graph.querySelectorAll('[data-js-debug-chart="agentTokens"] [data-js-debug-history-no-data-range]').length,
              cost: graph.querySelectorAll('[data-js-debug-chart="modelTokens"] [data-js-debug-history-no-data-range]').length,
            },
            retryButtons: graph.querySelectorAll('[data-js-debug-history-retry]').length,
            errorText: graph.textContent.includes('Could not load history'),
            degradedMessages: messages.filter(message => /retry entered|history request failed|coverage rejected/i.test(message)),
            fetchCount: Number(window.__morningAfterFetchCounts[String(rangeSeconds)] || 0),
            summary: {
              rawBuckets: summary.rawBuckets,
              rollupBuckets: summary.rollupBuckets,
              displayBuckets: summary.displayBuckets,
              agentTokenBuckets: summary.agentTokenBuckets,
              rangeSeconds: summary.rangeSeconds,
              resolutionSeconds: summary.resolutionSeconds,
            },
          });
        })().catch(error => done({scriptError: String(error?.stack || error)}));
        """,
        range_seconds,
    )


def test_morning_after_sleep_gap_protocol_history_is_honest_and_reload_idempotent(browser, tmp_path):
    fixture = _morning_after_protocol_history(tmp_path)
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof pollJsDebugStatsSample === 'function'
              && typeof debugGraphHistoryCoverageGapRuns === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    _install_morning_after_fetch(browser, fixture)

    results = {range_seconds: _run_morning_after_range(browser, range_seconds) for range_seconds in (3600, 4 * 3600, 24 * 3600)}
    for range_seconds, result in results.items():
        assert result.get("scriptError") is None, result
        assert result["phase"] == "ready", result
        assert result["error"] == "", result
        assert result["attempts"] == 1, result
        assert result["fetchCount"] == 1, result
        assert result["retryButtons"] == 0 and result["errorText"] is False, result
        assert result["degradedMessages"] == [], result
        assert result["summary"]["displayBuckets"] > 0, result
        assert result["summary"]["agentTokenBuckets"] > 0, result
        assert result["stores"]["status"] == result["stores"]["tokens"] == result["stores"]["cost"], result
        assert result["gaps"]["status"] == result["gaps"]["tokens"] == result["gaps"]["cost"], result

    assert results[3600]["gaps"]["status"] == [], results[3600]
    assert results[4 * 3600]["gaps"]["status"] == [{
        "start": fixture["now"] - (4 * 3600),
        "end": fixture["wakeTime"],
    }], results[4 * 3600]
    expected_overnight_gap = [{"start": fixture["sleepStart"], "end": fixture["wakeTime"]}]
    assert results[24 * 3600]["gaps"]["status"] == expected_overnight_gap, results[24 * 3600]
    assert results[24 * 3600]["overlays"] == {"status": 1, "tokens": 1, "cost": 1}, results[24 * 3600]

    def _honest_history(result):
        # The reload-idempotency contract is about the HONEST rendered history (coverage
        # gaps, no-data overlays, per-store spans, resolution, displayed buckets), not the
        # transient live-tail sample counts: a real SSE sample can land in one run's brief
        # settle window and not another's, so rawBuckets/rollupBuckets are wall-clock noise.
        stable = json.loads(json.dumps(result, sort_keys=True))
        summary = stable.get("summary")
        if isinstance(summary, dict):
            summary.pop("rawBuckets", None)
            summary.pop("rollupBuckets", None)
        return stable

    before_reload = _honest_history(results[24 * 3600])
    browser.refresh()
    wait_for_live_runtime_bundle(browser, expected_url=browser.current_url)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof pollJsDebugStatsSample === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
        )
    )
    _install_morning_after_fetch(browser, fixture)
    after_reload = _run_morning_after_range(browser, 24 * 3600)
    assert after_reload.get("scriptError") is None, after_reload
    assert _honest_history(after_reload) == before_reload


def _mixed_resolution_history(tmp_path):
    """Continuous 1h history: older half at 60s, recent half at 10s (no gap)."""
    now = int(time.time() // 60 * 60)
    database = tmp_path / "mixres" / "stats.sqlite3"
    service = statsd.PersistentStatsService(tmp_path / "mixres" / "statsd.sock", database)
    sequence = 0
    try:
        for segment_start, segment_end, bucket_seconds in ((now - 3600, now - 1800, 60), (now - 1800, now, 10)):
            for bucket_start in range(segment_start, segment_end, bucket_seconds):
                sequence += 1
                duration = min(bucket_seconds, segment_end - bucket_start)
                bucket = stats_store.empty_bucket(bucket_start, duration)
                bucket.update({"sequence": sequence, "server_sequence": sequence, "cpu_total_percent": 25.0, "cpu_count": 1.0, "system_cpu_total_percent": 40.0, "system_cpu_count": 1.0})
                service.store.upsert_bucket(bucket)
        for family, cadence in (("cpu", 3600),):
            service.handle({"action": "merge_server_records", "protocol_version": statsd.STATSD_PROTOCOL_VERSION, "records": [{"time": now - 3600, "_stats_coverage": {"family": family, "cadence_seconds": cadence, "epoch_id": f"e:{family}", "owner_generation": 1}}], "now": now - 3600, "compact": False, "refresh_rollups": False})
        history = service.handle({"action": "history", "protocol_version": statsd.STATSD_PROTOCOL_VERSION, "start": now - 3600, "end": 0, "resolution_seconds": 1, "max_points": 6000, "client_id": "mixres", "token_resolution_seconds": 60, "token_history_start": now - 3600, "token_history_end": 0})
        assert history["ok"] is True
    finally:
        service.store.close()
    return {"now": now, "history": history}


def test_explicit_fine_resolution_coarsens_until_the_whole_range_is_covered(browser, tmp_path):
    """A 10s override on a range whose older span is only 60s must render the FULL
    range at one coarsened resolution (60s), never a finer half-empty chart."""
    fixture = _mixed_resolution_history(tmp_path)
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(lambda d: d.execute_script("return typeof pollJsDebugStatsSample === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"))
    browser.execute_script(
        """
        const fx = arguments[0]; stopJsDebugStatsPolling(); Date.now = () => Number(fx.now) * 1000;
        const of = window.fetch; window.fetch = (i, o = {}) => { const u = new URL(String(i), 'https://localhost');
          if (u.pathname !== '/api/stats-sample') return of(i, o);
          return jsonResponse({ok: true, time: Number(fx.now), pid: 1, uptime_seconds: 10, cpu_percent: 25, rss_bytes: 1e8, history: fx.history}); };
        """,
        fixture,
    )
    out = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          stopJsDebugStatsPolling(); clearJsDebugGraphData(); resetJsDebugHistoryReadiness();
          setDebugGraphRange(3600, {render: false});
          setDebugGraphResolutionOverride(10);
          await pollJsDebugStatsSample({forceGraphRefresh: true});
          await window.__yolomuxTestHelpers.settle(3);
          const domain = debugGraphDomain();
          const coverageResSec = jsDebugHistoryCoverageResolutionForRange(Math.floor(domain.startMs / 1000), Math.ceil(domain.endMs / 1000));
          const displayResSec = debugGraphDisplayResolutionMs(domain, 0) / 1000;
          const buckets = debugGraphDisplayBuckets();
          const startsAgoMin = buckets.length ? Math.round((domain.endMs - Math.min(...buckets.map(b => b.startMs))) / 60000) : 0;
          done({coverageResSec, displayResSec, bucketCount: buckets.length, startsAgoMin});
        })().catch(error => done({scriptError: String(error?.stack || error)}));
        """
    )
    assert out.get("scriptError") is None, out
    # Coverage for the whole range is 60s; the 10s override must coarsen to 60s.
    assert out["coverageResSec"] == 60, out
    assert out["displayResSec"] >= out["coverageResSec"], out
    assert out["displayResSec"] == 60, out
    # Full range rendered (buckets reach back ~60 min), not a half-empty chart.
    assert out["startsAgoMin"] >= 55, out
    assert out["bucketCount"] >= 50, out

def test_switching_from_wide_range_does_not_coarsen_short_range_to_stale_resolution(browser, tmp_path):
    """Regression for '10s@1h shows 600s': after a wide (24h) view establishes a
    coarse coverage tail, switching to 1h with a 10s override must render at the
    1h retained tier (not the stale wide-range 600s)."""
    fixture = _morning_after_protocol_history(tmp_path)
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(lambda d: d.execute_script("return typeof pollJsDebugStatsSample === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"))
    _install_morning_after_fetch(browser, fixture)
    # Establish the wide-range coverage first (its old tail is 600s).
    _run_morning_after_range(browser, 24 * 3600)
    out = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          setDebugGraphRange(3600, {render: false});
          setDebugGraphResolutionOverride(10);
          await pollJsDebugStatsSample({forceGraphRefresh: true});
          await window.__yolomuxTestHelpers.settle(3);
          const domain = debugGraphDomain();
          done({displayResSec: debugGraphDisplayResolutionMs(domain, 0) / 1000});
        })().catch(error => done({scriptError: String(error?.stack || error)}));
        """
    )
    assert out.get("scriptError") is None, out
    # The 1h retained tier is <= 60s; it must NOT be the stale 600s wide-range value.
    assert out["displayResSec"] <= 60, out
    assert out["displayResSec"] != 600, out


def test_full_retention_prefetch_fills_cache_so_wide_range_renders_stale_without_touching_readiness(browser, tmp_path):
    """The background full-retention prefetch must silently populate the shared bucket
    cache so switching to a wide (24h) range renders cached content INSTANTLY (no blank),
    while NOT disturbing the current view's readiness/overlay state machine."""
    fixture = _morning_after_protocol_history(tmp_path)
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(lambda d: d.execute_script("return typeof prefetchJsDebugHistoryFullRetention === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"))
    _install_morning_after_fetch(browser, fixture)
    # Establish a short (1h) current view; only ~1h of buckets exist in the cache.
    _run_morning_after_range(browser, 3600)
    out = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const startsAgoMin = () => {
            const domain = debugGraphDomain();
            const buckets = debugGraphDisplayBuckets();
            return buckets.length ? Math.round((domain.endMs - Math.min(...buckets.map(b => b.startMs))) / 60000) : 0;
          };
          // maybePrefetch is a no-op before the first sample lands; after a 1h poll it is armed.
          jsDebugStatsPollState.firstSampleReceived = true;
          // Before prefetch: switching to 24h (no poll) shows only the ~1h cached tail.
          setDebugGraphRange(24 * 3600, {render: false});
          const wideBeforeMin = startsAgoMin();
          setDebugGraphRange(3600, {render: false});
          const readinessBefore = jsDebugHistoryReadinessSnapshot();
          const countBefore = Number(window.__morningAfterFetchCounts['86400'] || 0);
          // Silent full-retention cache-fill.
          const ok = await prefetchJsDebugHistoryFullRetention();
          await window.__yolomuxTestHelpers.settle(2);
          const readinessAfter = jsDebugHistoryReadinessSnapshot();
          const countAfter = Number(window.__morningAfterFetchCounts['86400'] || 0);
          // After prefetch: switching to 24h (still no poll) now renders the full ~24h from cache.
          setDebugGraphRange(24 * 3600, {render: false});
          const wideAfterMin = startsAgoMin();
          done({
            ok,
            wideBeforeMin,
            wideAfterMin,
            phaseBefore: readinessBefore.phase,
            phaseAfter: readinessAfter.phase,
            overlayBefore: readinessBefore.overlayVisible === true,
            overlayAfter: readinessAfter.overlayVisible === true,
            generationBefore: readinessBefore.generation,
            generationAfter: readinessAfter.generation,
            prefetchRequests: countAfter - countBefore,
          });
        })().catch(error => done({scriptError: String(error?.stack || error)}));
        """
    )
    assert out.get("scriptError") is None, out
    assert out["ok"] is True, out
    # The prefetch issued exactly one full-retention (24h span) request.
    assert out["prefetchRequests"] == 1, out
    # Cache-fill effect: before, the 24h view only reached back ~1h; after, ~full retention.
    assert out["wideBeforeMin"] <= 120, out
    assert out["wideAfterMin"] >= 20 * 60, out
    # Silent: the current view's readiness phase, overlay, and generation are untouched.
    assert out["phaseAfter"] == out["phaseBefore"], out
    assert out["overlayAfter"] == out["overlayBefore"], out
    assert out["generationAfter"] == out["generationBefore"], out


def test_host_charts_render_data_at_four_hour_view_in_real_browser(browser, tmp_path):
    """Screenshot-010 regression: at the 4h view, Server Load and System memory must draw
    their series (never an axes-only shell) and GPU charts must either draw device data or
    name a precise reason — never the ambiguous generic `None`."""
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(lambda d: d.execute_script(
        "return typeof debugGraphApplyServerHistory === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
    ))
    out = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        resetJsDebugHistoryReadiness();
        const nowSec = Math.floor(Date.now() / 1000 / 120) * 120;
        const records = [];
        for (let t = nowSec - (4 * 3600); t <= nowSec; t += 120) {
          records.push({
            start: t, duration: 120, sequence: records.length + 1,
            cpu_total_percent: 20 * 120, cpu_count: 120, system_cpu_total_percent: 30 * 120, system_cpu_count: 120,
            host_metrics: {
              system_memory_used_total_bytes: 48e9 * 2, system_memory_capacity_total_bytes: 64e9 * 2, system_memory_count: 2,
              service_load: {
                'web:8881': {label: 'web', cpu_total_percent: 12 * 12, cpu_samples: 12, rss_total_bytes: 2e8, rss_samples: 12},
                statsd: {label: 'statsd', cpu_total_percent: 0, cpu_samples: 12, rss_total_bytes: 1e8, rss_samples: 12},
              },
              gpu_devices: {'gpu:0': {label: 'GPU 0 (Apple M4 Pro)', util_total_percent: 0, memory_used_total_bytes: 2.9e9 * 12, memory_capacity_total_bytes: 51.5e9 * 12, samples: 12}},
            },
          });
        }
        for (const key of ['serversLoad', 'memory', 'gpuUtil', 'gpuMemory']) setDebugGraphChartVisible(key, true);
        setDebugGraphRange(4 * 3600, {render: false});
        debugGraphApplyServerHistory({sequence: records.length, records});
        renderDebugPanels({force: true});
        const graph = document.querySelector('[data-js-debug-graph]');
        const chart = key => graph.querySelector(`[data-js-debug-chart="${key}"]`);
        const visibleLine = (key, series) => {
          const node = chart(key)?.querySelector(`[data-js-debug-series="${series}"]`);
          if (!node) return 0;
          const box = node.getBoundingClientRect();
          return box.width;
        };
        return {
          memoryLineWidth: visibleLine('memory', 'systemMemory'),
          serversLoadWebWidth: visibleLine('serversLoad', 'serviceLoad:web:8881'),
          serversLoadIdleWidth: visibleLine('serversLoad', 'serviceLoad:statsd'),
          gpuUtilUnavailable: Boolean(chart('gpuUtil')?.querySelector('[data-js-debug-gpu-unavailable]')),
          gpuMemoryLine: Boolean(chart('gpuMemory')?.querySelector('[data-js-debug-series="gpu:gpuMemory:gpu:0"], [data-js-debug-area-series="gpu:gpuMemory:gpu:0"]')),
          gpuNoneText: (chart('gpuUtil')?.textContent || '').includes('None') || (chart('gpuMemory')?.textContent || '').includes('None'),
        };
        """
    )
    # Server Load and System memory draw REAL geometry across the pane, not empty axes.
    assert out["memoryLineWidth"] > 100, out
    assert out["serversLoadWebWidth"] > 100, out
    assert out["serversLoadIdleWidth"] > 100, out  # a zero-CPU service is still a drawn line
    # GPU has data: charts render (not the unavailable state) and never say generic None.
    assert out["gpuUtilUnavailable"] is False, out
    assert out["gpuMemoryLine"] is True, out
    assert out["gpuNoneText"] is False, out

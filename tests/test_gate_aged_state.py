from __future__ import annotations


import pytest

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_console import BROWSER_JOURNEY_OBSERVATION_SECONDS
from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_console import begin_browser_journey_surface_tracking
from tests.gate_harness import aged_state_root  # noqa: F401
from tests.gate_harness import assert_browser_journey_error_free
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.native_gate_fixtures import stateful_journey  # noqa: F401


pytestmark = [pytest.mark.browser, pytest.mark.socket]


def test_browser_journey_error_gate_observes_fifteen_second_late_error(browser):
    browser.get("data:text/html,<html><body>settled gate fixture</body></html>")
    browser.execute_script(
        """
            window.jsDebugEvents = [];
            window.jsDebugFailureEvents = () => window.jsDebugEvents.filter(event => ['warning', 'error'].includes(event.level));
            window.__lateErrorFixtureReceipt = null;
            window.statsWriterFence = {};
            window.flushJsDebugCurrentObservations = async () => {
              if (window.__lateErrorFixtureReceipt?.status === 'pending') {
                window.__lateErrorFixtureReceipt.status = 'accepted';
              }
            };
        window.jsDebugCurrentObservationReceiptBarrier = () => {
          const receipt = window.__lateErrorFixtureReceipt;
          const accepted = receipt?.status === 'accepted' ? 1 : 0;
          const pending = receipt?.status === 'pending' ? 1 : 0;
          return {
            epoch: 'late-error-fixture-epoch',
            accepted,
            pending,
            retrying: 0,
            rejected: 0,
            dropped: 0,
            quiescent: receipt === null || accepted === 1,
            blocking: pending ? [{...receipt}] : [],
          };
        };
        window.fetch = input => {
          const url = new URL(String(input), 'http://gate.invalid');
          if (url.pathname !== '/api/logs') return Promise.reject(new Error(`unexpected gate fetch: ${url.pathname}`));
          return Promise.resolve(new Response(JSON.stringify({
            ok: true,
            epoch: 'late-error-fixture-epoch',
            logs: [],
            sequence: 0,
            capacity: 500,
            dropped: {count: 0, first_id: null, last_id: null, by_level: {}},
          }), {status: 200, headers: {'Content-Type': 'application/json'}}));
        };
        setTimeout(() => {
          const event = {
            id: 1,
            ts: new Date().toISOString(),
            type: 'stats_history',
            level: 'error',
            message: 'GET /api/session-files?from=HEAD&to=current&session=yo7771&hours=24 | error',
            source: 'browser/api',
            endpoint: '/api/session-files',
            eventType: 'graph-refresh',
            deliveryOutcome: 'failed',
          };
          window.jsDebugEvents.push(event);
          window.__lateErrorFixtureReceipt = {
                key: `late-error-fixture-epoch:${event.id}`,
                epoch: 'late-error-fixture-epoch',
                eventId: event.id,
                requestId: 'late-error-fixture-request',
                source: '/api/session-files',
                route: event.endpoint,
                event: event.type,
                wallTime: event.ts,
                deliveryOutcome: 'failed',
                httpStatus: null,
                status: 'pending',
              };
        }, 15000);
        """
    )
    begin_browser_journey_surface_tracking(browser)
    with pytest.raises(AssertionError, match="session-files"):
        assert_browser_journey_error_free(
            browser,
            observation_seconds=BROWSER_JOURNEY_OBSERVATION_SECONDS,
        )

    late_event = browser.execute_script("return {...window.jsDebugEvents[0]};")
    retired = acknowledge_and_consume_only_expected_js_debug_failures(browser, (late_event,))
    assert [event["id"] for event in retired] == [1]
    clean = assert_browser_journey_error_free(
        browser,
        observation_seconds=BROWSER_JOURNEY_OBSERVATION_SECONDS,
    )
    assert clean["browserLocalFailures"] == []
    assert clean["observationSeconds"] == BROWSER_JOURNEY_OBSERVATION_SECONDS
    assert clean["observationSamples"] > 1

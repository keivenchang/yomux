from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_console import BROWSER_JOURNEY_OBSERVATION_SECONDS
from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_console import retire_only_nonfailure_js_debug_events
from tests.browser_helpers.browser_console import begin_browser_journey_surface_tracking
from tests.browser_helpers.browser_console import emit_js_debug_event
from tests.browser_helpers.browser_console import retire_browser_after_strict_diagnostic_gate
from tests.gate_harness import aged_state_root  # noqa: F401
from tests.gate_harness import assert_browser_journey_error_free
from tests.gate_harness import consume_only_expected_server_log_errors
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import load_gate_browser
from tests.gate_harness import open_gate_stats_surface
from tests.gate_harness import run_finder_nested_reexpand_journey
from tests.gate_harness import stateful_journey  # noqa: F401
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.stats_current import storage as stats_storage


pytestmark = [pytest.mark.browser, pytest.mark.socket]


def _wait_for_js_debug_type(driver, event_type: str) -> dict[str, object]:
    event = WebDriverWait(driver, 5).until(
        lambda current: current.execute_script(
            "return typeof jsDebugEvents === 'undefined' ? null : [...jsDebugEvents].reverse().find(event => event?.type === arguments[0]) || null;",
            event_type,
        )
    )
    assert isinstance(event, dict)
    return event


def _reset_stateful_server_log_ring(browser, journey) -> None:
    """Reset only for ring-reset regressions and advance both fixture boundaries atomically."""

    SERVER_LOGS.clear()
    boundary = SERVER_LOGS.payload()
    browser._yolomux_server_log_boundary = boundary
    journey.server_log_boundary = boundary


def _load_browser_error_gate(browser, runtime) -> None:
    session = runtime.tmux.sessions[0]
    query = urlencode({"sessions": session, "layout": "left", "tabs": f"left:{session}*"})
    load_gate_browser(browser, runtime, f"/?{query}")
    lifecycle = browser.execute_script(
        """
        return {
          activeItems: typeof activePaneItems === 'function' ? activePaneItems() : [],
          finderVisible: typeof fileExplorerTreePaneIsVisible === 'function' && fileExplorerTreePaneIsVisible(),
          channels: [...(clientEventTransportState.demand?.channels || [])],
          watchDiffRequests: performance.getEntriesByType('resource')
            .filter(entry => new URL(entry.name).pathname === '/api/fs/watch-diff').length,
        };
        """
    )
    assert lifecycle["activeItems"] == [session], lifecycle
    assert lifecycle["finderVisible"] is False, lifecycle
    assert "files" not in lifecycle["channels"], lifecycle
    assert lifecycle["watchDiffRequests"] == 0, lifecycle


@pytest.mark.parametrize("clear_boundary_marker", (False, True), ids=("marked", "marker-cleared"))
def test_stateful_journey_stop_rejects_browser_outside_fixture_origin(
    browser, stateful_journey, clear_boundary_marker
):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    SERVER_LOGS.emit("warning", "fixture-regression", "stateful fixture escaped its server origin")
    if clear_boundary_marker:
        browser._yolomux_server_log_boundary = None
    retire_browser_after_strict_diagnostic_gate(browser)

    try:
        with pytest.raises(AssertionError, match="exact live origin") as failure:
            stateful_journey.stop()
        assert "stateful fixture escaped its server origin" in str(failure.value.__cause__)
    finally:
        SERVER_LOGS.clear()


def test_browser_journey_error_gate_rejects_real_error_and_rejection(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)

    favicon_requests = browser.execute_script(
        """
        return performance.getEntriesByType('resource')
          .map(entry => entry.name)
          .filter(name => new URL(name, location.href).pathname === '/favicon.ico');
        """
    )
    assert favicon_requests == []
    browser.get_log("browser")

    clean = assert_browser_journey_error_free(browser)
    assert clean["jsDebugStoreReachable"] is True
    assert clean["jsDebugErrors"] == [] and clean["severeBrowserLogEntries"] == []

    api_status_event = emit_js_debug_event(
        browser,
        "api",
        {"url": "/synthetic-api-status", "method": "GET", "status": 500},
    )
    with pytest.raises(AssertionError, match="synthetic-api-status") as api_status_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "api"' in str(api_status_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (api_status_event,)) == (api_status_event,)

    for status in (400, 404, 424):
        readonly_event = emit_js_debug_event(
            browser,
            "api",
            {"url": "/synthetic-readonly-4xx", "method": "GET", "status": status},
        )
        with pytest.raises(AssertionError, match="synthetic-readonly-4xx") as readonly_failure:
            assert_browser_journey_error_free(browser)
        assert f'"status": {status}' in str(readonly_failure.value)
        assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (readonly_event,)) == (readonly_event,)

    api_ok_event = emit_js_debug_event(
        browser,
        "api",
        {"url": "/synthetic-api-ok", "method": "GET", "status": 200, "ok": False},
    )
    with pytest.raises(AssertionError, match="synthetic-api-ok") as api_ok_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "api"' in str(api_ok_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (api_ok_event,)) == (api_ok_event,)

    api_error_event = emit_js_debug_event(
        browser,
        "api",
        {
            "url": "/synthetic-api-error",
            "method": "GET",
            "status": 200,
            "ok": True,
            "error": "synthetic-api-error-detail",
        },
    )
    with pytest.raises(AssertionError, match="synthetic-api-error-detail") as api_error_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "api"' in str(api_error_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (api_error_event,)) == (api_error_event,)

    sse_event = emit_js_debug_event(
        browser,
        "sse",
        {"url": "/synthetic-sse", "error": "synthetic-sse-error"},
    )
    with pytest.raises(AssertionError, match="synthetic-sse-error") as sse_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "sse"' in str(sse_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (sse_event,)) == (sse_event,)

    client_failure_event = emit_js_debug_event(
        browser,
        "client_failure",
        {"message": "synthetic-client-failure", "source": "fixture-client"},
    )
    with pytest.raises(AssertionError, match="synthetic-client-failure") as client_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "client_failure"' in str(client_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(
        browser,
        (client_failure_event,),
    ) == (client_failure_event,)

    successful_api_event = emit_js_debug_event(
        browser,
        "api",
        {"url": "/synthetic-api-success", "method": "GET", "status": 200, "ok": True},
    )
    successful_api = assert_browser_journey_error_free(browser)
    assert successful_api["jsDebugErrors"] == [] and successful_api["severeBrowserLogEntries"] == []
    assert successful_api_event["url"] == "/synthetic-api-success"
    assert successful_api_event["status"] == 200
    assert successful_api_event["ok"] is True
    assert not successful_api_event.get("error")
    stats_warning_event = emit_js_debug_event(
        browser,
        "stats_history",
        {"level": "warning", "message": "synthetic-stats-warning"},
    )
    with pytest.raises(AssertionError, match="synthetic-stats-warning") as stats_warning_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "stats_history"' in str(stats_warning_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (stats_warning_event,)) == (
        stats_warning_event,
    )

    stats_error_event = emit_js_debug_event(
        browser,
        "stats_history",
        {"level": "error", "message": "synthetic-stats-error"},
    )
    with pytest.raises(AssertionError, match="synthetic-stats-error") as stats_error_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "stats_history"' in str(stats_error_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (stats_error_event,)) == (
        stats_error_event,
    )

    emit_js_debug_event(
        browser,
        "stats_history",
        {"level": "info", "message": "synthetic-stats-info"},
    )
    stats_info = assert_browser_journey_error_free(browser)
    assert stats_info["jsDebugErrors"] == []

    browser.execute_script("console.warn('gate-console-warning-sentinel');")
    with pytest.raises(AssertionError, match="gate-console-warning-sentinel") as warning_failure:
        assert_browser_journey_error_free(browser)
    assert '"level": "WARNING"' in str(warning_failure.value)

    browser.execute_script("setTimeout(() => { throw new Error('gate-real-error-sentinel'); }, 0);")
    error_event = _wait_for_js_debug_type(browser, "error")
    with pytest.raises(AssertionError, match="gate-real-error-sentinel") as error_failure:
        assert_browser_journey_error_free(browser)
    error_failure_text = str(error_failure.value)
    assert '"type": "error"' in error_failure_text
    assert '"severeBrowserLogEntries": []' not in error_failure_text
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (error_event,)) == (error_event,)

    browser.execute_script(
        """
        const script = document.createElement('script');
        script.textContent = "setTimeout(() => Promise.reject(new Error('gate-real-rejection-sentinel')), 0);";
        document.head.appendChild(script);
        script.remove();
        """
    )
    rejection_event = _wait_for_js_debug_type(browser, "unhandledrejection")
    with pytest.raises(AssertionError, match="gate-real-rejection-sentinel") as rejection_failure:
        assert_browser_journey_error_free(browser)
    assert '"type": "unhandledrejection"' in str(rejection_failure.value)
    assert acknowledge_and_consume_only_expected_js_debug_failures(browser, (rejection_event,)) == (rejection_event,)

    browser.execute_script("console.error('gate-console-severe-sentinel');")
    with pytest.raises(AssertionError, match="gate-console-severe-sentinel") as console_failure:
        assert_browser_journey_error_free(browser)
    assert '"level": "SEVERE"' in str(console_failure.value)

    clean_after_red = assert_browser_journey_error_free(browser)
    assert clean_after_red["jsDebugErrors"] == [] and clean_after_red["severeBrowserLogEntries"] == []


def test_real_client_error_upload_is_always_on_and_durable(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    assert "debug=" not in browser.current_url

    browser.execute_script(
        """
        const script = document.createElement('script');
        script.textContent = `
          const eventButton = document.createElement('button');
          eventButton.addEventListener('click', () => { throw new Error('gate-durable-event-handler-sentinel'); });
          document.body.appendChild(eventButton);
          eventButton.click();
          eventButton.remove();
          Promise.resolve().then(() => { throw new Error('gate-durable-promise-sentinel'); });
          setTimeout(async () => { throw new Error('gate-durable-async-sentinel'); }, 0);
          setTimeout(() => { throw new Error('gate-durable-timeout-sentinel'); }, 0);
        `;
        document.head.appendChild(script);
        script.remove();
        """
    )
    _wait_for_js_debug_type(browser, "error")
    _wait_for_js_debug_type(browser, "unhandledrejection")
    WebDriverWait(browser, 5).until(
        lambda current: current.execute_script(
            "return !jsDebugCurrentObservationState.inFlight;"
        )
    )
    upload = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const sentinel = 'gate-durable-';
        const failureQueued = () => jsDebugCurrentObservationState.queue.some(
          entry => String(entry?.event?.message || '').includes(sentinel),
        );
        (async () => {
          let attempts = 0;
          do {
            attempts += 1;
            await flushJsDebugCurrentObservations();
          } while (failureQueued() && attempts < 10);
          return attempts;
        })().then(
          attempts => done({
            queue: jsDebugCurrentObservationState.queue.length,
            drops: jsDebugCurrentObservationState.drops,
            attempts,
            failureQueued: failureQueued(),
          }),
          error => done({error: String(error?.message || error)}),
        );
        """
    )

    assert "error" not in upload
    assert upload["drops"] == 0
    assert upload["failureQueued"] is False
    assert 0 <= upload["queue"] <= 1000
    with stats_storage.Store.open_reader(runtime.app.stats_current_client.database_path) as reader:
        browser_observations = [
            dict(item.payload)
            for item in reader.read_snapshot().observations
            if item.family == "browser"
        ]
    failures = [item for item in browser_observations if item.get("kind") in {"error", "unhandledrejection"}]
    heartbeats = [item for item in browser_observations if item.get("kind") == "heartbeat"]
    assert any(
        {"upload_queue_depth", "upload_drops", "upload_retries"} <= set(item)
        for item in heartbeats
    ), heartbeats
    messages = [item.get("message", "") for item in failures]
    sentinels = {
        "gate-durable-event-handler-sentinel",
        "gate-durable-promise-sentinel",
        "gate-durable-async-sentinel",
        "gate-durable-timeout-sentinel",
    }
    assert sentinels <= set(messages), messages
    for message in sentinels:
        uploaded = next(item for item in failures if item.get("message") == message)
        assert uploaded["signature"].startswith("jsf-")
        assert uploaded["source"].startswith("/") and "?" not in uploaded["source"]
        assert "stack" in uploaded
        assert not ({"typed_text", "file_contents", "input_value", "document_text"} & set(uploaded))
    log_path = runtime.app.stats_current_client.database_path.with_name(
        f"{runtime.app.stats_current_client.database_path.stem}.browser-failures.jsonl"
    )
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    durable_messages = {record["message"] for record in records}
    assert sentinels <= durable_messages, durable_messages
    failure_events = tuple(browser.execute_script("return jsDebugFailureEvents();"))
    assert {event.get("message") for event in failure_events} == sentinels, failure_events
    acknowledge_and_consume_only_expected_js_debug_failures(browser, failure_events)
    browser.get_log("browser")
    clean = assert_browser_journey_error_free(browser)
    assert clean["browserLocalFailures"] == [] and clean["browserLogFailures"] == []


def test_browser_journey_error_gate_rejects_unconsumed_server_failures(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    retire_only_nonfailure_js_debug_events(browser)
    browser.get_log("browser")
    _reset_stateful_server_log_ring(browser, stateful_journey)
    live_error_inventory = (
        {
            "level": "error",
            "source": "local-service:statusd",
            "category": "transport",
            "message": "TimeoutError: timed out",
        },
        {
            "level": "error",
            "source": "local-service:statusd",
            "category": "transport",
            "message": "LocalRpcError: response exceeded deadline",
        },
        {
            "level": "error",
            "source": "browser/api",
            "category": "browser",
            "message": "GET /api/session-files?from=HEAD&to=current&session=yo7771&hours=24 | error",
        },
        {
            "level": "warning",
            "source": "yolomux_lib.stats_current.scheduler",
            "category": "python",
            "message": "current stats agent_tokens collector failed: RuntimeError: agent roster unavailable",
        },
    )

    for expected in live_error_inventory:
        SERVER_LOGS.emit(
            expected["level"],
            expected["source"],
            expected["message"],
            category=expected["category"],
        )
        with pytest.raises(AssertionError) as failure:
            assert_browser_journey_error_free(browser)
        assert expected["message"] in str(failure.value)
        assert stateful_journey.retire_expected_server_log_errors(browser, (expected,))[0]["message"] == expected["message"]
        clean = assert_browser_journey_error_free(browser)
        assert clean["browserLocalFailures"] == [] and clean["serverLogErrors"] == []


def test_browser_journey_error_gate_rejects_reused_id_after_server_log_reset(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    retire_only_nonfailure_js_debug_events(browser)
    browser.get_log("browser")
    browser._yolomux_server_log_boundary = None
    first = {
        "level": "error",
        "source": "local-service:statusd",
        "category": "transport",
        "message": "pre-reset expected transport error",
    }
    second = {
        **first,
        "message": "post-reset unconsumed transport error",
    }
    first_epoch = "server-log-epoch-before-reset"
    second_epoch = "server-log-epoch-after-reset"
    browser.execute_script(
        """
        window.__gateOriginalFetch = window.fetch;
        window.__gateServerLogPayload = arguments[0];
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/logs') {
            return Promise.resolve(new Response(JSON.stringify(window.__gateServerLogPayload), {
              status: 200,
              headers: {'Content-Type': 'application/json'},
            }));
          }
          return window.__gateOriginalFetch(input, options);
        };
        """,
        {
            "ok": True,
            "epoch": first_epoch,
            "logs": [{"id": 1, "timestamp": 1, **first}],
            "sequence": 1,
            "capacity": 500,
            "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
        },
    )
    try:
        assert consume_only_expected_server_log_errors(browser, (first,))[0]["id"] == 1
        assert assert_browser_journey_error_free(browser)["serverLogErrors"] == []

        browser.execute_script(
            "window.__gateServerLogPayload = arguments[0];",
            {
                "ok": True,
                "epoch": second_epoch,
                "logs": [{"id": 1, "timestamp": 2, **second}],
                "sequence": 1,
                "capacity": 500,
                "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
            },
        )
        with pytest.raises(AssertionError, match=second["message"]):
            assert_browser_journey_error_free(browser)
        tracking = browser.execute_script(
            "return {epoch: __yolomuxBrowserJourneyGate.serverLogEpoch, ids: [...__yolomuxBrowserJourneyGate.consumedServerLogIds]};"
        )
        assert tracking == {"epoch": second_epoch, "ids": []}
    finally:
        browser.execute_script(
            "window.fetch = window.__gateOriginalFetch; delete window.__gateOriginalFetch; delete window.__gateServerLogPayload;"
        )
        browser._yolomux_server_log_boundary = stateful_journey.server_log_boundary


def test_browser_journey_error_gate_initializes_absent_consumption_state(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    _reset_stateful_server_log_ring(browser, stateful_journey)
    retire_only_nonfailure_js_debug_events(browser)
    browser.execute_script("delete window.__yolomuxBrowserJourneyGate;")
    browser.get_log("browser")

    clean = assert_browser_journey_error_free(browser)

    assert clean["serverLogErrors"] == []
    tracking = browser.execute_script(
        "return {epoch: __yolomuxBrowserJourneyGate.serverLogEpoch, ids: [...__yolomuxBrowserJourneyGate.consumedServerLogIds]};"
    )
    assert tracking == {"epoch": SERVER_LOGS.payload()["epoch"], "ids": []}
    started = begin_browser_journey_surface_tracking(browser)
    assert started == {"malformed": False, "visitedSurfaces": []}
    assert browser.execute_script(
        "return __yolomuxBrowserJourneyGate.observer instanceof MutationObserver && typeof __yolomuxBrowserJourneyGate.observe === 'function';"
    ) is True


def test_browser_journey_error_gate_rejects_malformed_existing_consumption_state(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    _reset_stateful_server_log_ring(browser, stateful_journey)
    browser.execute_script(
        "window.__yolomuxBrowserJourneyGate = {consumedServerLogIds: {invalid: true}};"
    )
    browser.get_log("browser")

    with pytest.raises(AssertionError, match="consumed server-log IDs are malformed"):
        assert_browser_journey_error_free(browser)
    browser.execute_script("delete window.__yolomuxBrowserJourneyGate;")
    assert assert_browser_journey_error_free(browser)["serverLogErrors"] == []


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


def test_browser_journey_error_gate_fails_when_bounded_ring_drops_entries(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    for index in range(SERVER_LOGS.capacity + 1):
        SERVER_LOGS.emit("info", "synthetic-volume", f"entry {index}")

    with pytest.raises(AssertionError, match="serverLogDropped"):
        assert_browser_journey_error_free(browser)

    _reset_stateful_server_log_ring(browser, stateful_journey)
    clean = assert_browser_journey_error_free(browser)
    assert clean["browserLocalFailures"] == [] and clean["serverLogErrors"] == []


def test_browser_journey_error_gate_rejects_unvisited_claimed_surface(browser, stateful_journey):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)

    with pytest.raises(AssertionError, match="stats.*not visited"):
        assert_browser_journey_error_free(browser, claimed_clean_surfaces=("stats",))

    opened = open_gate_stats_surface(browser)
    assert opened["panelId"] == "panel-__debug__"
    assert opened["visible"] is True and opened["statsVisible"] is True
    assert "stats" in opened["visitedSurfaces"]
    clean = assert_browser_journey_error_free(browser, claimed_clean_surfaces=("stats",))
    assert clean["claimedCleanSurfaces"] == clean["visitedSurfaces"] == ["stats"]


@pytest.mark.parametrize(
    ("mode", "failure"),
    (
        ("offline", "ring is unreachable"),
        ("http", "returned HTTP 503"),
        ("json", "JSON is malformed"),
        ("shape", "logs is not an array"),
        ("epoch", "epoch is missing"),
        ("empty-epoch", "epoch is malformed"),
        ("typed-epoch", "epoch is malformed"),
    ),
)
def test_browser_journey_error_gate_fails_closed_on_unreadable_log_ring(
    browser,
    stateful_journey,
    mode,
    failure,
):
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    browser.execute_script(
        """
        window.__gateOriginalFetch = window.fetch;
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/logs') {
            if (arguments[0] === 'offline') return Promise.reject(new Error('synthetic ring offline'));
            if (arguments[0] === 'http') return Promise.resolve(new Response('{}', {status: 503}));
            if (arguments[0] === 'json') return Promise.resolve(new Response('{', {status: 200}));
            if (arguments[0] === 'shape') {
              return Promise.resolve(new Response(JSON.stringify({ok: true, logs: {}}), {status: 200}));
            }
            const payload = {
              ok: true,
              logs: [],
              sequence: 0,
              capacity: 500,
              dropped: {count: 0, first_id: null, last_id: null, by_level: {}},
            };
            if (arguments[0] === 'empty-epoch') payload.epoch = '';
            if (arguments[0] === 'typed-epoch') payload.epoch = 17;
            return Promise.resolve(new Response(JSON.stringify(payload), {status: 200}));
          }
          return window.__gateOriginalFetch(input, options);
        };
        """,
        mode,
    )
    try:
        retire_only_nonfailure_js_debug_events(browser)
        browser.get_log("browser")
        with pytest.raises(AssertionError, match=failure):
            assert_browser_journey_error_free(browser)
    finally:
        browser.execute_script(
            "window.fetch = window.__gateOriginalFetch; delete window.__gateOriginalFetch;"
        )

    clean = assert_browser_journey_error_free(browser)
    assert clean["browserLocalFailures"] == [] and clean["serverLogErrors"] == []


def test_aged_server_restart_preserves_selected_state_and_private_identity(aged_state_root, stateful_journey):
    caches = aged_state_root.apply("coexisting_transcript_caches", shared_count=4, host_count=3)
    events = aged_state_root.apply("event_history", counts={"state_changed": 5, "stale_owner_heartbeat": 2})
    before_cache_names = tuple(tuple(path.name for path in sorted(directory.glob("*.json"))) for directory in caches.paths)
    before_event_lines = events.paths[0].read_text(encoding="utf-8").splitlines()

    first = stateful_journey.start()
    assert gate_http_request(first, "/api/ping").status == 200
    second = stateful_journey.restart()

    assert second.port == first.port == stateful_journey.port
    assert stateful_journey.starts == 2
    assert gate_http_request(second, "/api/ping").status == 200
    assert tuple(tuple(path.name for path in sorted(directory.glob("*.json"))) for directory in caches.paths) == before_cache_names
    assert events.paths[0].read_text(encoding="utf-8").splitlines()[: len(before_event_lines)] == before_event_lines


def test_nested_finder_journey_retains_each_phase_without_duplicate_work(browser, aged_state_root, stateful_journey):
    finder = aged_state_root.apply("finder_resource_history", top_level_entries=99, nested_entries=8)
    aged_state_root.apply("coexisting_transcript_caches", shared_count=12, host_count=8)
    aged_state_root.apply("eof_transcript_cursor")
    aged_state_root.apply("event_history", counts={"state_changed": 40, "stale_owner_heartbeat": 8})
    runtime = stateful_journey.start()
    _load_browser_error_gate(browser, runtime)
    runtime = stateful_journey.restart()

    result = run_finder_nested_reexpand_journey(browser, runtime, finder)

    assert not result.get("error"), result
    assert result["browserErrorEvidence"]["jsDebugStoreReachable"] is True, result
    assert result["browserErrorEvidence"]["jsDebugErrors"] == [], result
    assert result["browserErrorEvidence"]["severeBrowserLogEntries"] == [], result
    phases = result["phases"]
    assert [phase["name"] for phase in phases] == [
        "expand-dev",
        "open-subdirectory",
        "collapse-dev",
        "reexpand-dev",
    ]
    assert all(isinstance(phase["elapsedMs"], (int, float)) and phase["elapsedMs"] >= 0 for phase in phases)

    dev_root = str(finder.details["dev_root"])
    subdirectory = str(finder.details["subdirectory"])
    expected_expansions = [
        [dev_root],
        [dev_root, subdirectory],
        [subdirectory],
        [dev_root, subdirectory],
    ]
    assert [phase["state"]["expanded"] for phase in phases] == expected_expansions, phases
    assert all(phase["state"]["pending"] == [] for phase in phases), phases
    assert phases[0]["requestCounts"].get(dev_root) == 1, phases
    assert phases[1]["requestCounts"].get(subdirectory) == 1, phases
    assert phases[2]["requestCounts"] == {}, phases
    assert phases[3]["requestCounts"].get(dev_root, 0) <= 1, phases
    assert phases[3]["requestCounts"].get(subdirectory, 0) == 0, phases
    assert all(phase["state"]["duplicatePaths"] == {} for phase in phases), phases
    assert phases[3]["state"]["nestedProbeVisible"] is True, phases
    assert [record for record in result["fetches"] if int(record.get("status", 0)) >= 400] == [], result["fetches"]

    metrics_path = aged_state_root.state_dir / "aged-fixture" / "finder-journey-result.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")

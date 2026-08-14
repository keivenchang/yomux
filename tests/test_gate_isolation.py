# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""O7 regression-gate guards for writable-root isolation."""

import copy
import ctypes
import ctypes.util
from http.client import HTTPConnection
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from yolomux_lib.http_routes import ALL_ROUTES
from yolomux_lib.http_routes import RESPONSE_SSE
from yolomux_lib.http_routes import RESPONSE_WEBSOCKET
from yolomux_lib.server_logs import ServerLogRing
from tests import gate_harness as gate_harness_module
from tests.browser_helpers import browser_console
from yolomux_lib import browser_diagnostic_receipts
from tests.browser_helpers import browser_layout
from tests.browser_helpers.browser_layout import _live_runtime_boot_fixture_html
from yolomux_lib.local_services import registry as local_service_registry
from tests.gate_harness import assert_no_surviving_local_service_daemons
from tests.gate_harness import assert_writable_paths_beneath
from tests.gate_harness import bootstrap_writable_paths
from tests.gate_harness import assert_fixture_inotify_returned_to_baseline
from tests.gate_harness import capture_fixture_self_baseline
from tests.gate_harness import capture_resource_ledger
from tests.gate_harness import FIXTURE_INOTIFY_NOT_RETURNED_CODE
from tests.gate_harness import gate_runtime_paths
from tests.gate_harness import inotify_instance_census
from tests.gate_harness import local_service_daemons_beneath
from tests.gate_harness import retire_local_service_daemons_beneath
from tests.gate_harness import LOCAL_SERVICE_DAEMON_SURVIVED_CODE
from tests.gate_harness import resolved_gate_writable_paths
from tests.gate_harness import stop_fixture_app_runtime
from tests.gate_harness import wait_for_fixture_api_quiescence


def clean_browser_receipt_barrier(*, accepted=0):
    return {
        "epoch": "all",
        "accepted": accepted,
        "pending": 0,
        "retrying": 0,
        "rejected": 0,
        "dropped": 0,
        "quiescent": True,
        "blocking": [],
    }


def normal_browser_receipt_blocker():
    return {
        "key": "fixture:1",
        "epoch": "fixture",
        "eventId": 1,
        "requestId": "r-fixture",
        "source": "/fixture",
        "route": "/fixture",
        "event": "fixture-error",
        "wallTime": "2026-08-06 00:00:00 PDT",
        "deliveryOutcome": "failed",
        "httpStatus": None,
        "status": "pending",
    }


def browser_receipt_barrier_with_blocker(blocker):
    barrier = clean_browser_receipt_barrier()
    barrier[blocker["status"]] = 1
    barrier["quiescent"] = False
    barrier["blocking"] = [blocker]
    return barrier


def storage_failure_browser_receipt_blocker():
    return {
        "key": "__yolomux_receipt_storage_failure__",
        "epoch": "*",
        "eventId": None,
        "requestId": "",
        "source": "/",
        "route": "/",
        "event": "receipt_storage_failure",
        "wallTime": "",
        "deliveryOutcome": "failed",
        "httpStatus": None,
        "status": "dropped",
        "globalBlocker": True,
        "storageFailure": "write_failed",
    }


def overflow_browser_receipt_blocker():
    return {
        "key": "__yolomux_receipt_journal_overflow__",
        "epoch": "*",
        "eventId": None,
        "requestId": "",
        "source": "/",
        "route": "/",
        "event": "receipt_journal_overflow",
        "wallTime": "",
        "deliveryOutcome": "dropped",
        "httpStatus": None,
        "status": "dropped",
        "globalBlocker": True,
        "journalOverflow": True,
        "omitted": 1,
    }


class _RetainedJsLifecycleDriver:
    def execute_script(self, source):
        assert "return lifecycle ? {diagnosticMode" in source
        return {"diagnosticMode": "retained-js"}


@pytest.fixture(autouse=True)
def _route_fixture_retirement_through_legacy_test_drivers(monkeypatch):
    """Keep fixture-lifecycle fakes focused on ordering; atomicity has direct tests below."""

    monkeypatch.setattr(
        gate_harness_module,
        "retire_browser_after_strict_diagnostic_gate",
        lambda driver, **_kwargs: driver.get("about:blank"),
    )


def test_collection_time_writable_roots_stay_beneath_generated_test_root():
    root, writable_paths = bootstrap_writable_paths()
    assert_writable_paths_beneath(root, writable_paths)


@pytest.mark.socket
def test_rendered_fixture_get_suppresses_process_lifetime_agent_auth_refresh(monkeypatch, gate_runtime_paths):
    refresh_calls = []
    gate_harness_module.workdir_module._clear_agent_auth_status_cache_for_tests()
    monkeypatch.setattr(
        gate_harness_module.server_module,
        "start_agent_auth_status_refresh",
        lambda *, force=False: refresh_calls.append(force),
    )
    monkeypatch.setattr(
        gate_harness_module.workdir_module,
        "start_agent_auth_status_refresh",
        lambda *, force=False: refresh_calls.append(force),
    )
    monkeypatch.setenv(gate_harness_module.TEST_AUTH_BYPASS_ENV, "1")
    app = gate_harness_module.app_module.TmuxWebtermApp([], dangerously_yolo=False)

    gate_harness_module.prepare_fixture_http_app(monkeypatch, app)
    server = gate_harness_module.TmuxWebtermHTTPServer(("127.0.0.1", 0), app)
    gate_harness_module.track_fixture_http_requests(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        assert refresh_calls == []
    finally:
        connection.close()
        gate_harness_module.stop_fixture_http_app(app, server, thread, label="rendered auth suppression")
        gate_harness_module.workdir_module._clear_agent_auth_status_cache_for_tests()


def test_gate_runtime_paths_and_imported_constants_are_fixture_owned(gate_runtime_paths):
    resolved = resolved_gate_writable_paths(gate_runtime_paths)
    assert_writable_paths_beneath(gate_runtime_paths.root, resolved)

    assert gate_runtime_paths.config_dir.is_dir()
    assert gate_runtime_paths.state_dir.is_dir()
    assert gate_runtime_paths.cache_dir.is_dir()
    assert os.environ["YOLOMUX_CONFIG_DIR"] == str(gate_runtime_paths.config_dir)
    assert os.environ["YOLOMUX_STATE_DIR"] == str(gate_runtime_paths.state_dir)
    assert os.environ["YOLOMUX_CACHE_DIR"] == str(gate_runtime_paths.cache_dir)

    patched_labels = {label for label, _path in gate_runtime_paths.patched_module_paths}
    assert "yolomux_lib.infra.common.CONFIG_DIR" in patched_labels
    assert "yolomux_lib.auth.CONFIG_DIR" in patched_labels


def test_gate_browser_boundary_waits_for_preexisting_async_api_work():
    class PendingApiDriver:
        def __init__(self):
            self.states = [
                {"available": True, "diagnosticMode": "retained-js", "pending": ["op-fixture"], "watchDiffPendingOperationIds": [], "activityRefreshing": False, "watchRootsPending": False},
                {"available": True, "diagnosticMode": "retained-js", "pending": [], "watchDiffPendingOperationIds": [], "activityRefreshing": True, "watchRootsPending": False},
                {"available": True, "diagnosticMode": "retained-js", "pending": [], "watchDiffPendingOperationIds": [], "activityRefreshing": False, "watchRootsPending": False},
            ]
            self.receipt_barriers = [
                browser_receipt_barrier_with_blocker(normal_browser_receipt_blocker()),
                clean_browser_receipt_barrier(accepted=1),
            ]

        def execute_script(self, script):
            if "statsWriterFence" in script:
                return True
            return self.states.pop(0)

        def execute_async_script(self, script):
            assert "flushJsDebugCurrentObservations" in script
            pending = self.receipt_barriers.pop(0)
            assert pending["quiescent"] is False
            return self.receipt_barriers.pop(0)

    driver = PendingApiDriver()
    settled = wait_for_fixture_api_quiescence(driver, timeout=1)

    assert settled == {
        "available": True,
        "diagnosticMode": "retained-js",
        "pending": [],
        "watchDiffPendingOperationIds": [],
        "activityRefreshing": False,
        "watchRootsPending": False,
        "browserReceiptBarrier": clean_browser_receipt_barrier(accepted=1),
    }
    assert driver.states == []
    assert driver.receipt_barriers == []


def test_gate_browser_boundary_waits_for_held_watch_root_registration():
    class HeldWatchRootsDriver:
        def __init__(self):
            self.states = [
                {
                    "available": True,
                    "diagnosticMode": "browser-console",
                    "pending": [],
                    "watchRootsPending": True,
                },
                {
                    "available": True,
                    "diagnosticMode": "browser-console",
                    "pending": [],
                    "watchRootsPending": False,
                },
            ]

        def execute_script(self, _script):
            return self.states.pop(0)

    driver = HeldWatchRootsDriver()
    settled = wait_for_fixture_api_quiescence(driver, timeout=1)

    assert settled["watchRootsPending"] is False
    assert driver.states == []


def test_gate_browser_boundary_allows_browser_console_without_watch_root_owner():
    class StatsOnlyDriver:
        def execute_script(self, _script):
            return {
                "available": True,
                "diagnosticMode": "browser-console",
                "pending": [],
            }

    settled = wait_for_fixture_api_quiescence(StatsOnlyDriver(), timeout=1)

    assert settled == {
        "available": True,
        "diagnosticMode": "browser-console",
        "pending": [],
    }


def test_gate_browser_boundary_requires_retained_js_watch_root_owner():
    class MissingWatchRootOwnerDriver:
        def execute_script(self, _script):
            return {
                "available": True,
                "diagnosticMode": "retained-js",
                "pending": [],
            }

    with pytest.raises(AssertionError, match="watch-root state is malformed"):
        wait_for_fixture_api_quiescence(MissingWatchRootOwnerDriver(), timeout=1)


def test_gate_browser_boundary_rejects_missing_full_app_lifecycle_adapter():
    class MissingLifecycleDriver:
        def execute_script(self, _script):
            return {"available": False}

    with pytest.raises(AssertionError, match="fixture lifecycle operation state is unreachable"):
        wait_for_fixture_api_quiescence(MissingLifecycleDriver(), timeout=1)


def test_gate_browser_boundary_timeout_names_the_owned_pending_work():
    class StuckApiDriver:
        def execute_script(self, _script):
            return {
                "available": True,
                "diagnosticMode": "retained-js",
                "pending": ["op-stuck"],
                "watchDiffPendingOperationIds": [],
                "batchQueued": 0,
                "batchPending": 0,
                "batchOperations": 0,
                "activityRefreshing": False,
                "watchRootsPending": False,
                "finderWatchReady": True,
            }

    with pytest.raises(AssertionError, match="fixture API work did not quiesce.*op-stuck"):
        wait_for_fixture_api_quiescence(StuckApiDriver(), timeout=0.01)


def test_minimal_fixture_finite_work_reaches_gate_retirement_and_cleanup(monkeypatch):
    calls = []
    ring = ServerLogRing(capacity=8)

    class MinimalFixtureDriver:
        current_url = "http://127.0.0.1:43210/stats"

        def __init__(self):
            self.operation_states = [
                {"available": True, "diagnosticMode": "browser-console", "pending": ["1:fetch:/api/stats-snapshot"], "watchRootsPending": False},
                {"available": True, "diagnosticMode": "browser-console", "pending": [], "watchRootsPending": False},
            ]

        def execute_script(self, source):
            if "lifecycle.operationState()" in source:
                calls.append("operation-state")
                return self.operation_states.pop(0)
            if "return lifecycle ? {diagnosticMode" in source:
                return {"diagnosticMode": "browser-console"}
            raise AssertionError(source)

        def get_log(self, kind):
            assert kind == "browser"
            return []

    driver = MinimalFixtureDriver()

    def gate(current, **options):
        assert current is driver
        calls.append(("gate", options.get("require_js_debug_store")))
        return {"serverLogCursor": ring.payload()}

    def retire(current, **options):
        assert current is driver
        calls.append(("retire", options.get("require_js_debug_store")))

    monkeypatch.setattr(gate_harness_module, "assert_browser_journey_error_free", gate)
    monkeypatch.setattr(gate_harness_module, "retire_browser_after_strict_diagnostic_gate", retire)

    browser_layout.finish_browser_fixture_boundary(
        driver,
        "http://127.0.0.1:43210",
        lambda: calls.append("cleanup"),
        server_log_reader=ring.payload,
    )

    assert calls == [
        "operation-state",
        "operation-state",
        ("gate", False),
        ("retire", False),
        "cleanup",
    ]


class BrowserLogDriver:
    def __init__(self, entries):
        self.entries = list(entries)

    def get_log(self, kind):
        assert kind == "browser"
        entries, self.entries = self.entries, []
        return entries


def browser_log_entry(level, source, message):
    return {"level": level, "source": source, "message": message, "timestamp": 1}


def test_exact_browser_warning_consumer_accepts_one_anchored_warning():
    expected = browser_log_entry(
        "WARNING",
        "console-api",
        'http://127.0.0.1:7000/static/yolomux.js 10:2 "fixture warning" Error: exact correlation\n    at fixture',
    )

    assert browser_console.assert_only_expected_browser_warning(
        BrowserLogDriver((expected,)),
        message="fixture warning",
        correlation="Error: exact correlation",
    ) == expected


def test_exact_browser_warning_consumer_rejects_an_additional_severe_failure():
    entries = (
        browser_log_entry(
            "WARNING",
            "console-api",
            'http://127.0.0.1:7000/static/yolomux.js 10:2 "fixture warning" Error: exact correlation',
        ),
        browser_log_entry("SEVERE", "network", "http://127.0.0.1:7000/unexpected - Failed to load resource"),
    )

    with pytest.raises(AssertionError, match="matching browser warning"):
        browser_console.assert_only_expected_browser_warning(
            BrowserLogDriver(entries),
            message="fixture warning",
            correlation="Error: exact correlation",
        )


def test_exact_browser_http_error_consumer_rejects_an_unrelated_warning():
    entries = (
        browser_log_entry(
            "SEVERE",
            "network",
            "http://127.0.0.1:7000/api/fixture?item=one - Failed to load resource: the server responded with a status of 409 (Conflict)",
        ),
        browser_log_entry("WARNING", "console-api", 'http://127.0.0.1:7000/ 1:1 "unrelated warning" Object'),
    )

    with pytest.raises(AssertionError, match="matching browser HTTP error"):
        browser_console.assert_only_expected_browser_http_error(
            BrowserLogDriver(entries),
            path="/api/fixture",
            status=409,
            query={"item": "one"},
        )


def test_exact_browser_network_error_consumer_rejects_an_unrelated_warning():
    entries = (
        browser_log_entry(
            "SEVERE",
            "network",
            "https://example.test/image.png - Failed to load resource: net::ERR_NAME_NOT_RESOLVED",
        ),
        browser_log_entry("WARNING", "console-api", 'http://127.0.0.1:7000/ 1:1 "unrelated warning" Object'),
    )

    with pytest.raises(AssertionError, match="matching browser network error"):
        browser_console.assert_only_expected_browser_network_error(
            BrowserLogDriver(entries),
            url="https://example.test/image.png",
            reason="net::ERR_NAME_NOT_RESOLVED",
        )


def test_full_browser_gate_keeps_network_ring_404_fatal():
    class Driver:
        def execute_script(self, source, *_args):
            if "const isArray = Array.isArray(jsDebugEvents)" in source:
                return {
                    "reachable": True,
                    "isArray": True,
                    "events": [],
                    "errors": [],
                    "receiptBarrier": clean_browser_receipt_barrier(),
                }
            if "const state = window.__yolomuxBrowserJourneyGate" in source:
                return {"reachable": False, "visitedSurfaces": []}
            if "return [...state.consumedServerLogIds]" in source:
                return []
            raise AssertionError(f"unexpected browser script: {source}")

        def execute_async_script(self, source):
            assert "/api/logs" in source
            return {"reachable": True, "status": 404, "payload": None, "parseError": ""}

        def get_log(self, kind):
            assert kind == "browser"
            return []

    with pytest.raises(AssertionError, match="returned HTTP 404"):
        browser_console.assert_browser_journey_error_free(Driver())


@pytest.mark.parametrize(
    ("label", "barrier"),
    (
        ("partial", {"quiescent": True}),
        ("missing", {key: value for key, value in clean_browser_receipt_barrier().items() if key != "accepted"}),
        ("extra", {**clean_browser_receipt_barrier(), "hidden": 1}),
        ("wrong-type", {**clean_browser_receipt_barrier(), "accepted": True}),
        ("unsafe", {**clean_browser_receipt_barrier(), "accepted": 2**53}),
        ("negative", {**clean_browser_receipt_barrier(), "accepted": -1}),
        ("count-mismatch", {**clean_browser_receipt_barrier(), "pending": 1}),
        (
            "malformed-blocker",
            {**clean_browser_receipt_barrier(), "pending": 1, "quiescent": False, "blocking": [{"status": "pending"}]},
        ),
        ("malformed-epoch", {**clean_browser_receipt_barrier(), "epoch": ""}),
        (
            "false-quiescent",
            {
                **clean_browser_receipt_barrier(),
                "pending": 1,
                "blocking": [{
                    "key": "fixture:1",
                    "epoch": "fixture",
                    "eventId": 1,
                    "requestId": "r-fixture",
                    "source": "/fixture",
                    "route": "/fixture",
                    "event": "error",
                    "wallTime": "2026-08-06 00:00:00 PDT",
                    "deliveryOutcome": "failed",
                    "httpStatus": None,
                    "status": "pending",
                }],
            },
        ),
    ),
)
def test_full_browser_gate_fails_closed_on_malformed_receipt_barrier(label, barrier):
    class Driver:
        def execute_script(self, source, *_args):
            if "const isArray = Array.isArray(jsDebugEvents)" in source:
                return {
                    "reachable": True,
                    "isArray": True,
                    "events": [],
                    "errors": [],
                    "receiptBarrier": barrier,
                }
            if "const state = window.__yolomuxBrowserJourneyGate" in source:
                return {"reachable": False, "visitedSurfaces": []}
            if "return [...state.consumedServerLogIds]" in source:
                return []
            raise AssertionError(f"unexpected browser script for {label}: {source}")

        def get_log(self, kind):
            assert kind == "browser"
            return []

    payload = {
        "ok": True,
        "epoch": "fixture-ring",
        "sequence": 0,
        "capacity": 8,
        "logs": [],
        "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
    }
    with pytest.raises(AssertionError, match="receipt barrier"):
        browser_console.assert_browser_journey_error_free(Driver(), server_log_reader=lambda: payload)


def test_browser_receipt_acknowledgement_uses_the_strict_barrier_validator():
    class Driver:
        def execute_script(self, source):
            assert "statsWriterFence" in source
            return True

        def execute_async_script(self, source):
            assert "flushJsDebugCurrentObservations" in source
            return {"quiescent": True}

    with pytest.raises(AssertionError, match="receipt barrier"):
        browser_console.acknowledge_browser_diagnostic_receipts(Driver())


def test_browser_receipt_acknowledgement_calls_the_shared_production_owner(monkeypatch):
    receipt = clean_browser_receipt_barrier()
    calls = []

    class Driver:
        def execute_script(self, source):
            assert "statsWriterFence" in source
            return True

        def execute_async_script(self, source):
            assert "flushJsDebugCurrentObservations" in source
            return receipt

    def validate(value):
        calls.append(value)
        return dict(value)

    monkeypatch.setattr(browser_diagnostic_receipts, "validate_browser_receipt_barrier", validate)

    assert browser_console.acknowledge_browser_diagnostic_receipts(Driver()) == receipt
    assert calls == [receipt]


class ExpectedJsDebugFailureDriver:
    def __init__(self, events, *, enrich=None):
        self.events = copy.deepcopy(list(events))
        self.enrich = enrich or (lambda retained: None)

    def execute_script(self, source, *args):
        if "const eventsDefined = typeof jsDebugEvents" in source:
            return {
                "reachable": True,
                "isArray": True,
                "events": copy.deepcopy(self.events),
                "errors": copy.deepcopy(self.events),
                "receiptBarrier": clean_browser_receipt_barrier(accepted=len(self.events)),
            }
        if "statsWriterFence" in source:
            return True
        if "const ids = new Set(arguments[0])" in source:
            event_ids = set(args[0])
            retired = copy.deepcopy([event for event in self.events if event.get("id") in event_ids])
            self.events = [event for event in self.events if event.get("id") not in event_ids]
            return retired
        raise AssertionError(f"unexpected browser script: {source}")

    def execute_async_script(self, source):
        assert "flushJsDebugCurrentObservations" in source
        self.enrich(self.events)
        return clean_browser_receipt_barrier(accepted=len(self.events))


def expected_js_debug_failure(**overrides):
    return {
        "id": 7,
        "type": "api",
        "method": "POST",
        "url": "/api/rename-session",
        "status": 409,
        "ok": False,
        "error": "session already exists",
        **overrides,
    }


@pytest.mark.parametrize(
    "enrichment",
    (
        {"phaseTimings": {"applyRenderMs": 19.1}},
        {"responseBytes": 128},
        {"connectionProtocol": "h2"},
        {
            "responseBytes": 128,
            "connectionProtocol": "http/1.1",
            "phaseTimings": {
                "queueMs": 1.0,
                "connectMs": 2.0,
                "tlsMs": 3.0,
                "ttfbMs": 4.0,
                "downloadMs": 5.0,
                "applyRenderMs": 6.0,
            },
        },
    ),
)
def test_expected_js_debug_failure_retirement_allows_only_product_enrichment(enrichment):
    expected = expected_js_debug_failure()
    driver = ExpectedJsDebugFailureDriver(
        (expected,),
        enrich=lambda events: events[0].update(enrichment),
    )

    retired = browser_console.acknowledge_and_consume_only_expected_js_debug_failures(driver, (expected,))

    assert retired == ({**expected, **enrichment},)
    assert driver.events == []


def test_expected_js_debug_failure_retirement_allows_enrichment_before_initial_recheck():
    expected = expected_js_debug_failure()
    enriched = {**expected, "phaseTimings": {"applyRenderMs": 19.1}}
    driver = ExpectedJsDebugFailureDriver((enriched,))

    retired = browser_console.acknowledge_and_consume_only_expected_js_debug_failures(driver, (expected,))

    assert retired == (enriched,)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda event: event.update(status=500),
        lambda event: event.update(unknownEnrichment="not-owned"),
        lambda event: event["phaseTimings"].update(queueMs=99.0),
        lambda event: event["phaseTimings"].update(otherMs=1.0),
    ),
)
def test_expected_js_debug_failure_retirement_rejects_semantic_mutation_or_unknown_enrichment(mutate):
    expected = expected_js_debug_failure(phaseTimings={"queueMs": 1.0})

    def enrich(events):
        mutate(events[0])

    driver = ExpectedJsDebugFailureDriver((expected,), enrich=enrich)

    with pytest.raises(AssertionError, match="retirement changed"):
        browser_console.acknowledge_and_consume_only_expected_js_debug_failures(driver, (expected,))


@pytest.mark.parametrize("value", (-1, float("inf"), True, "19.1", 86_400_000.1))
def test_expected_js_debug_failure_retirement_rejects_invalid_added_phase_timing(value):
    expected = expected_js_debug_failure()
    driver = ExpectedJsDebugFailureDriver(
        (expected,),
        enrich=lambda events: events[0].update(phaseTimings={"applyRenderMs": value}),
    )

    with pytest.raises(AssertionError, match="retirement changed"):
        browser_console.acknowledge_and_consume_only_expected_js_debug_failures(driver, (expected,))


def test_expected_js_debug_failure_retirement_keeps_added_unexpected_failure_blocking():
    expected = expected_js_debug_failure()

    def enrich(events):
        events.append(expected_js_debug_failure(id=8, url="/api/unexpected"))

    driver = ExpectedJsDebugFailureDriver((expected,), enrich=enrich)

    with pytest.raises(AssertionError, match="unexpected JS debug failures remained"):
        browser_console.acknowledge_and_consume_only_expected_js_debug_failures(driver, (expected,))


@pytest.mark.parametrize(
    "events",
    (
        (expected_js_debug_failure(id="7"),),
        (expected_js_debug_failure(id=7), expected_js_debug_failure(id=7, url="/api/other")),
    ),
)
def test_expected_js_debug_failure_retirement_rejects_noninteger_or_duplicate_ids(events):
    driver = ExpectedJsDebugFailureDriver(events)

    with pytest.raises(AssertionError, match="unique integer IDs"):
        browser_console.acknowledge_and_consume_only_expected_js_debug_failures(driver, events)


@pytest.mark.parametrize(
    "blocker",
    (
        {**normal_browser_receipt_blocker(), "globalBlocker": False},
        {**normal_browser_receipt_blocker(), "storageFailure": "write_failed"},
        {**normal_browser_receipt_blocker(), "journalOverflow": False},
        {**storage_failure_browser_receipt_blocker(), "journalOverflow": False},
        {**overflow_browser_receipt_blocker(), "storageFailure": "write_failed"},
    ),
)
def test_browser_receipt_barrier_rejects_hybrid_blocker_shapes(blocker):
    with pytest.raises(AssertionError, match="receipt barrier"):
        browser_diagnostic_receipts.validate_browser_receipt_barrier(browser_receipt_barrier_with_blocker(blocker))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("key", "other:1"),
        ("epoch", "*"),
        ("eventId", None),
        ("requestId", "r" * 129),
        ("requestId", "bad\nrequest"),
        ("source", ""),
        ("source", "/" + "s" * 240),
        ("route", "bad\x00route"),
        ("event", "bad event"),
        ("event", "e" * 65),
        ("wallTime", "w" * 65),
        ("deliveryOutcome", "bad outcome"),
        ("deliveryOutcome", "d" * 33),
    ),
)
def test_browser_receipt_barrier_rejects_malformed_normal_correlation(field, value):
    blocker = {**normal_browser_receipt_blocker(), field: value}
    with pytest.raises(AssertionError, match="receipt barrier"):
        browser_diagnostic_receipts.validate_browser_receipt_barrier(browser_receipt_barrier_with_blocker(blocker))


@pytest.mark.parametrize(
    "blocker",
    (
        normal_browser_receipt_blocker(),
        storage_failure_browser_receipt_blocker(),
        overflow_browser_receipt_blocker(),
    ),
)
def test_browser_receipt_barrier_accepts_exact_discriminated_blocker_shapes(blocker):
    barrier = browser_receipt_barrier_with_blocker(blocker)
    assert browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier) == barrier


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (lambda barrier: barrier.update(pending=2), "blocker_counts"),
        (lambda barrier: barrier.update(quiescent=True), "quiescence"),
        (lambda barrier: barrier.update(accepted=2**53), "counts"),
        (lambda barrier: barrier.update(accepted=2**53 - 1), "count_total"),
        (lambda barrier: barrier.update(epoch="*"), "epoch"),
        (lambda barrier: barrier.update(epoch="selected"), "blocker_epoch"),
        (lambda barrier: barrier["blocking"].append(dict(barrier["blocking"][0])), "duplicate_blocker_key"),
    ),
)
def test_browser_receipt_barrier_rejects_accounting_safe_integer_epoch_and_key_defects(mutate, code):
    barrier = browser_receipt_barrier_with_blocker(normal_browser_receipt_blocker())
    mutate(barrier)

    with pytest.raises(browser_diagnostic_receipts.BrowserReceiptBarrierValidationError) as failure:
        browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier)

    assert failure.value.code == code


def test_browser_receipt_barrier_returns_fresh_plain_copies():
    barrier = browser_receipt_barrier_with_blocker(normal_browser_receipt_blocker())

    validated = browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier)

    assert type(validated) is dict
    assert type(validated["blocking"]) is list
    assert type(validated["blocking"][0]) is dict
    assert validated is not barrier
    assert validated["blocking"] is not barrier["blocking"]
    assert validated["blocking"][0] is not barrier["blocking"][0]


def test_browser_receipt_barrier_errors_do_not_echo_payload_secrets():
    barrier = browser_receipt_barrier_with_blocker(normal_browser_receipt_blocker())
    barrier["blocking"][0]["secret"] = "do-not-echo"

    with pytest.raises(browser_diagnostic_receipts.BrowserReceiptBarrierValidationError) as failure:
        browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier)

    assert "do-not-echo" not in str(failure.value)


@pytest.mark.parametrize(
    ("source", "accepted"),
    (
        ("/" + "😀" * 119 + "a", True),
        ("/" + "😀" * 120, False),
    ),
)
def test_browser_receipt_barrier_counts_utf16_code_units_like_javascript(source, accepted):
    blocker = {**normal_browser_receipt_blocker(), "source": source}
    barrier = browser_receipt_barrier_with_blocker(blocker)
    if accepted:
        assert browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier) == barrier
    else:
        with pytest.raises(browser_diagnostic_receipts.BrowserReceiptBarrierValidationError) as failure:
            browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier)
        assert failure.value.code == "blocker_shape"


def test_browser_receipt_barrier_accepts_lone_surrogates_without_leaking_raw_unicode_errors():
    blocker = {**normal_browser_receipt_blocker(), "source": "/\ud800"}
    barrier = browser_receipt_barrier_with_blocker(blocker)

    assert browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier) == barrier


def test_browser_receipt_barrier_rejects_unsafe_reconstructed_overflow_total():
    pending = normal_browser_receipt_blocker()
    overflow = {**overflow_browser_receipt_blocker(), "omitted": 2**53 - 1}
    barrier = clean_browser_receipt_barrier()
    barrier.update(pending=1, dropped=1, quiescent=False, blocking=[pending, overflow])

    with pytest.raises(browser_diagnostic_receipts.BrowserReceiptBarrierValidationError) as failure:
        browser_diagnostic_receipts.validate_browser_receipt_barrier(barrier)

    assert failure.value.code == "overflow_total"


@pytest.mark.parametrize(
    ("events", "errors", "receipt_barrier", "failure_text"),
    (
        (
            [{"id": 7, "type": "error", "level": "warning", "message": "late browser warning"}],
            [{"id": 7, "type": "error", "level": "warning", "message": "late browser warning"}],
            clean_browser_receipt_barrier(),
            "late browser warning",
        ),
        (
            [],
            [],
            browser_receipt_barrier_with_blocker(normal_browser_receipt_blocker()),
            "fixture-error",
        ),
        (
            [],
            [],
            browser_receipt_barrier_with_blocker(storage_failure_browser_receipt_blocker()),
            "receipt_storage_failure",
        ),
    ),
)
def test_atomic_browser_retirement_snapshots_and_blanks_in_one_task(
    events,
    errors,
    receipt_barrier,
    failure_text,
):
    calls = []

    class Driver:
        retirement_urls = iter(("http://127.0.0.1:43210/app", "about:blank"))

        @property
        def current_url(self):
            calls.append(("url",))
            return next(self.retirement_urls)

        def execute_script(self, source):
            calls.append(("snapshot", source))
            return {
                "jsDebug": {
                    "reachable": True,
                    "isArray": True,
                    "events": events,
                    "errors": errors,
                    "receiptBarrier": receipt_barrier,
                },
                "journey": {"reachable": False, "visitedSurfaces": []},
            }

        def get_log(self, kind):
            assert kind == "browser"
            calls.append(("log", kind))
            return []

    with pytest.raises(AssertionError, match=failure_text):
        browser_console.retire_browser_after_strict_diagnostic_gate(Driver())

    assert [call[0] for call in calls] == ["snapshot", "url", "url", "log"]
    script = calls[0][1]
    assert script.index("jsDebugCurrentObservationReceiptBarrier()") < script.index(
        "window.location.replace('about:blank')"
    )


def test_clean_browser_baseline_snapshot_and_retirement_use_one_browser_task():
    calls = []
    event = {"id": 18, "type": "api", "level": "info", "message": "clean startup"}

    class Driver:
        def execute_async_script(self, source):
            calls.append("paint")
            assert "requestAnimationFrame" in source
            return True

        def execute_script(self, source):
            calls.append("atomic")
            assert "const snapshot = Array.from(jsDebugEvents" in source
            assert "jsDebugFailureEvents()" in source
            assert "jsDebugEvents.splice(index, 1)" in source
            return {"failures": [], "retired": [event]}

    assert browser_console.retire_only_nonfailure_js_debug_events(Driver()) == (event,)
    assert calls == ["paint", "atomic"]


def test_browser_gate_uses_strict_in_process_ring_reader_without_opening_http_logs():
    class Driver:
        ring_requests = 0

        def execute_script(self, source, *_args):
            if "const isArray = Array.isArray(jsDebugEvents)" in source:
                return {
                    "reachable": True,
                    "isArray": True,
                    "events": [],
                    "errors": [],
                    "receiptBarrier": clean_browser_receipt_barrier(),
                }
            if "return [...state.consumedServerLogIds]" in source:
                return []
            if "const state = window.__yolomuxBrowserJourneyGate" in source:
                return {"reachable": False, "visitedSurfaces": []}
            raise AssertionError(f"unexpected browser script: {source}")

        def execute_async_script(self, source):
            assert "/api/logs" in source
            self.ring_requests += 1
            return {"reachable": True, "status": 403, "payload": None, "parseError": ""}

        def get_log(self, kind):
            assert kind == "browser"
            return []

    payload = {
        "ok": True,
        "epoch": "browser-fixture-ring",
        "sequence": 1,
        "capacity": 8,
        "logs": [{
            "id": 1,
            "level": "warning",
            "source": "browser-fixture",
            "category": "server",
            "message": "browser fixture retained ring warning",
        }],
        "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
    }
    driver = Driver()
    with pytest.raises(AssertionError, match="browser fixture retained ring warning"):
        browser_console.assert_browser_journey_error_free(
            driver,
            server_log_reader=lambda: payload,
        )
    assert driver.ring_requests == 0

    with pytest.raises(AssertionError, match="returned HTTP 403"):
        browser_console.assert_browser_journey_error_free(driver)
    assert driver.ring_requests == 1


def test_full_browser_gate_scopes_shared_ring_to_server_epoch_sequence_boundary():
    ring = ServerLogRing(capacity=8)

    class Driver:
        def __init__(self):
            self.server_log_epoch = None
            self.consumed_server_log_ids = []

        def execute_script(self, source, *_args):
            if "const isArray = Array.isArray(jsDebugEvents)" in source:
                return {
                    "reachable": True,
                    "isArray": True,
                    "events": [],
                    "errors": [],
                    "receiptBarrier": clean_browser_receipt_barrier(),
                }
            if "const existing = window.__yolomuxBrowserJourneyGate" in source:
                return {"malformed": False, "visitedSurfaces": []}
            if "for (const id of arguments[1])" in source:
                epoch, event_ids = _args
                if self.server_log_epoch != epoch:
                    return False
                self.consumed_server_log_ids.extend(
                    event_id for event_id in event_ids if event_id not in self.consumed_server_log_ids
                )
                return True
            if "return [...state.consumedServerLogIds]" in source:
                epoch = _args[0]
                if self.server_log_epoch != epoch:
                    self.server_log_epoch = epoch
                    self.consumed_server_log_ids.clear()
                return list(self.consumed_server_log_ids)
            if "const state = window.__yolomuxBrowserJourneyGate" in source:
                return {"reachable": False, "visitedSurfaces": []}
            raise AssertionError(f"unexpected browser script: {source}")

        def execute_async_script(self, source):
            assert "/api/logs" in source
            return {"reachable": True, "status": 200, "payload": ring.payload(), "parseError": ""}

        def get_log(self, kind):
            assert kind == "browser"
            return []

    expected = {
        "level": "warning",
        "source": "peer-server",
        "category": "server",
        "message": "warning predating this fixture server",
    }
    ring.emit(expected["level"], expected["source"], expected["message"])
    driver = Driver()
    retired = browser_console.consume_only_expected_server_log_errors(driver, (expected,))
    assert [entry["message"] for entry in retired] == [expected["message"]]
    boundary = ring.payload()
    ring.emit("info", "fixture-server", "fixture server started")

    clean = browser_console.assert_browser_journey_error_free(
        driver,
        server_log_reader=ring.payload,
        server_log_boundary=boundary,
    )
    assert clean["serverLogEntryCount"] == 1
    assert clean["serverLogErrors"] == []

    ring.emit("warning", "fixture-server", "warning after this fixture server started")
    with pytest.raises(AssertionError, match="warning after this fixture server started"):
        browser_console.assert_browser_journey_error_free(
            driver,
            server_log_reader=ring.payload,
            server_log_boundary=boundary,
        )


@pytest.mark.parametrize(
    ("method_name", "path"),
    (
        ("do_GET", "/api/ping"),
        ("do_GET", "/fixture-unknown"),
        ("do_POST", "/api/watch/roots"),
        ("do_HEAD", "/"),
    ),
)
def test_fixture_finite_http_methods_finish_before_app_runtime_stops(monkeypatch, method_name, path):
    handler_started = threading.Event()
    release_handler = threading.Event()
    handler_finished = threading.Event()
    shutdown_called = threading.Event()
    calls = []
    stop_errors = []

    class Handler:
        close_connection = False

        def run_finite_request(self):
            handler_started.set()
            release_handler.wait()
            handler_finished.set()

        def do_GET(self):
            self.run_finite_request()

        def do_POST(self):
            self.run_finite_request()

        def do_HEAD(self):
            self.run_finite_request()

    class Server:
        RequestHandlerClass = Handler

        def shutdown(self):
            calls.append("shutdown")
            shutdown_called.set()

        def server_close(self):
            calls.append("server-close")

    class ServerThread:
        def join(self, timeout):
            calls.append(("acceptor-join", timeout))

        def is_alive(self):
            return False

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    handler = server.RequestHandlerClass()
    handler.path = path
    request_thread = threading.Thread(target=vars(type(handler))[method_name], args=(handler,), daemon=True)
    request_thread.start()
    assert handler_started.wait(timeout=1)

    def stop_app(_app, *, label):
        assert label == "finite fixture handler"
        assert handler_finished.is_set()
        calls.append("stop-app")

    def stop_server():
        try:
            gate_harness_module.stop_fixture_http_app(
                object(),
                server,
                ServerThread(),
                label="finite fixture handler",
            )
        except BaseException as error:
            stop_errors.append(error)

    monkeypatch.setattr(gate_harness_module, "stop_fixture_app_runtime", stop_app)
    stop_thread = threading.Thread(target=stop_server, daemon=True)
    stop_thread.start()
    assert shutdown_called.wait(timeout=1)
    assert "stop-app" not in calls
    release_handler.set()
    request_thread.join(timeout=1)
    stop_thread.join(timeout=1)

    assert not request_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_errors == []
    assert calls == ["shutdown", "stop-app", "server-close", ("acceptor-join", 3)]


def test_fixture_active_finite_paths_fail_closed_before_app_runtime_stops(monkeypatch):
    handler_started = threading.Event()
    release_handler = threading.Event()
    calls = []

    class Handler:
        path = "/api/ping"
        close_connection = False

        def do_GET(self):
            handler_started.set()
            release_handler.wait()

        def do_POST(self):
            return None

        def do_HEAD(self):
            return None

    class Server:
        RequestHandlerClass = Handler

        def shutdown(self):
            calls.append("shutdown")

        def server_close(self):
            calls.append("server-close")

    class ServerThread:
        def join(self, timeout):
            calls.append(("acceptor-join", timeout))

        def is_alive(self):
            return False

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    handler = server.RequestHandlerClass()
    request_thread = threading.Thread(target=handler.do_GET, daemon=True)
    request_thread.start()
    assert handler_started.wait(timeout=1)

    wait_for_quiescence = gate_harness_module.wait_for_fixture_http_quiescence
    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_http_quiescence",
        lambda current_server: wait_for_quiescence(current_server, timeout=0),
    )
    monkeypatch.setattr(
        gate_harness_module,
        "stop_fixture_app_runtime",
        lambda _app, *, label: calls.append(("stop-app", label)),
    )

    try:
        with pytest.raises(AssertionError, match=r"retained 1 active finite request.*GET /api/ping"):
            gate_harness_module.stop_fixture_http_app(
                object(),
                server,
                ServerThread(),
                label="active fixture handler",
            )
        assert calls == [
            "shutdown",
            ("stop-app", "active fixture handler"),
            "server-close",
            ("acceptor-join", 3),
        ]
    finally:
        release_handler.set()
        request_thread.join(timeout=1)

    assert not request_thread.is_alive()


@pytest.mark.parametrize(
    ("method_name", "path"),
    (
        ("do_GET", "/api/ping"),
        ("do_POST", "/api/watch/roots"),
        ("do_HEAD", "/"),
    ),
)
def test_fixture_finite_http_handler_entering_after_seal_is_refused_and_reported(
    monkeypatch,
    method_name,
    path,
):
    app_called = threading.Event()
    late_handler = None
    calls = []

    class Handler:
        close_connection = False

        def do_GET(self):
            app_called.set()

        def do_POST(self):
            app_called.set()

        def do_HEAD(self):
            app_called.set()

    class Server:
        RequestHandlerClass = Handler

        def shutdown(self):
            nonlocal late_handler
            calls.append("shutdown")
            late_handler = self.RequestHandlerClass()
            late_handler.path = path
            thread = threading.Thread(target=getattr(late_handler, method_name), daemon=True)
            thread.start()
            thread.join(timeout=1)
            assert not thread.is_alive()

        def server_close(self):
            calls.append("server-close")

    class ServerThread:
        def join(self, timeout):
            calls.append(("acceptor-join", timeout))

        def is_alive(self):
            return False

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    monkeypatch.setattr(
        gate_harness_module,
        "stop_fixture_app_runtime",
        lambda _app, *, label: calls.append(("stop-app", label)),
    )

    http_method = method_name.removeprefix("do_")
    with pytest.raises(AssertionError, match=rf"late finite request.*{http_method} {path}"):
        gate_harness_module.stop_fixture_http_app(
            object(),
            server,
            ServerThread(),
            label="late fixture handler",
        )

    assert not app_called.is_set()
    assert late_handler is not None and late_handler.close_connection is True
    assert calls == [
        "shutdown",
        ("stop-app", "late fixture handler"),
        "server-close",
        ("acceptor-join", 3),
    ]


def test_fixture_keep_alive_connection_thread_is_not_part_of_finite_request_wait(monkeypatch):
    request_finished = threading.Event()
    release_connection = threading.Event()
    calls = []

    class Handler:
        path = "/api/ping"
        close_connection = False

        def do_GET(self):
            return None

        def do_POST(self):
            return None

        def do_HEAD(self):
            return None

    class Server:
        RequestHandlerClass = Handler

        def shutdown(self):
            calls.append("shutdown")

        def server_close(self):
            calls.append("server-close")

    class ServerThread:
        def join(self, timeout):
            calls.append(("acceptor-join", timeout))

        def is_alive(self):
            return False

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    handler = server.RequestHandlerClass()

    def keep_alive_connection():
        handler.do_GET()
        request_finished.set()
        release_connection.wait()

    connection_thread = threading.Thread(target=keep_alive_connection, daemon=True)
    connection_thread.start()
    assert request_finished.wait(timeout=1)
    monkeypatch.setattr(
        gate_harness_module,
        "stop_fixture_app_runtime",
        lambda _app, *, label: calls.append(("stop-app", label)),
    )
    gate_harness_module.stop_fixture_http_app(
        object(),
        server,
        ServerThread(),
        label="keep-alive fixture handler",
    )

    assert connection_thread.is_alive()
    assert calls == [
        "shutdown",
        ("stop-app", "keep-alive fixture handler"),
        "server-close",
        ("acceptor-join", 3),
    ]
    release_connection.set()
    connection_thread.join(timeout=1)
    assert not connection_thread.is_alive()


def test_fixture_connection_retirement_wakes_idle_keep_alive_owner():
    class Handler:
        def do_GET(self):
            return None

        def do_POST(self):
            return None

        def do_HEAD(self):
            return None

    class Server:
        RequestHandlerClass = Handler

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    activity = server._fixture_http_request_activity
    server_connection, client_connection = socket.socketpair()
    owner_started = threading.Event()

    def own_connection():
        with activity.condition:
            activity.connections.add(server_connection)
            activity.condition.notify_all()
        owner_started.set()
        try:
            server_connection.recv(1)
        finally:
            server_connection.close()
            with activity.condition:
                activity.connections.discard(server_connection)
                activity.condition.notify_all()

    owner = threading.Thread(target=own_connection, daemon=True)
    owner.start()
    assert owner_started.wait(timeout=1)
    try:
        gate_harness_module.retire_fixture_http_connections(server, timeout=1)
        owner.join(timeout=1)
        assert not owner.is_alive()
        assert client_connection.recv(1) == b""
    finally:
        client_connection.close()


def test_http_port_reserve_reuses_time_wait_from_reusable_server_owner():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    server_connection, _client_address = listener.accept()
    server_connection.shutdown(socket.SHUT_RDWR)
    server_connection.close()
    assert client.recv(1) == b""
    client.close()
    listener.close()

    non_reusable = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            non_reusable.bind(("127.0.0.1", port))
    finally:
        non_reusable.close()

    lease = gate_harness_module.HttpPortLease.reserve(ports=(port,))
    try:
        assert lease.port == port
        assert lease.reserved
    finally:
        lease.release()


def test_http_port_reserve_refuses_an_active_listener_even_with_reuse_enabled():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with pytest.raises(OSError, match="no requested HTTP port could be reserved"):
            gate_harness_module.HttpPortLease.reserve(ports=(port,))
    finally:
        listener.close()


def test_fixture_connection_is_owned_before_handler_setup_starts():
    handler_release = threading.Event()
    handler_started = threading.Event()
    handler_threads = []

    class Handler:
        def do_GET(self):
            return None

        def do_POST(self):
            return None

        def do_HEAD(self):
            return None

    class Server:
        RequestHandlerClass = Handler

        def process_request(self, request_socket, _client_address):
            def paused_before_setup():
                handler_started.set()
                handler_release.wait()
                self.shutdown_request(request_socket)

            thread = threading.Thread(target=paused_before_setup, daemon=True)
            thread.start()
            handler_threads.append(thread)

        def shutdown_request(self, request_socket):
            request_socket.close()

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    server_connection, client_connection = socket.socketpair()
    server.process_request(server_connection, ("fixture", 1))
    assert handler_started.wait(timeout=1)
    assert server_connection in server._fixture_http_request_activity.connections

    retirement_errors = []

    def retire():
        try:
            gate_harness_module.retire_fixture_http_connections(server, timeout=1)
        except BaseException as error:
            retirement_errors.append(error)

    retirement = threading.Thread(target=retire, daemon=True)
    retirement.start()
    try:
        assert client_connection.recv(1) == b""
        handler_release.set()
        retirement.join(timeout=1)
        assert not retirement.is_alive()
        assert retirement_errors == []
        assert server._fixture_http_request_activity.connections == set()
    finally:
        handler_release.set()
        client_connection.close()
        for thread in handler_threads:
            thread.join(timeout=1)


def test_preseal_accepted_connection_entering_after_seal_is_refused_without_false_late_request():
    app_called = threading.Event()

    class Handler:
        def do_GET(self):
            app_called.set()

        def do_POST(self):
            app_called.set()

        def do_HEAD(self):
            app_called.set()

    class Server:
        RequestHandlerClass = Handler

        def process_request(self, _request_socket, _client_address):
            return None

        def shutdown_request(self, request_socket):
            request_socket.close()

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    server_connection, client_connection = socket.socketpair()
    try:
        server.process_request(server_connection, ("fixture", 1))
        gate_harness_module.seal_fixture_http_requests(server)
        handler = server.RequestHandlerClass()
        handler.path = "/api/client-events"
        handler.connection = server_connection
        handler.close_connection = False

        handler.do_GET()

        assert not app_called.is_set()
        assert handler.close_connection is True
        assert server._fixture_http_request_activity.late_request_paths == []
        gate_harness_module.wait_for_fixture_http_quiescence(server, timeout=1)
        server.shutdown_request(server_connection)
    finally:
        client_connection.close()


def test_fixture_connection_retirement_waits_for_handler_exit_after_socket_close():
    close_observed = threading.Event()
    handler_release = threading.Event()
    handler_threads = []

    class Handler:
        def do_GET(self):
            return None

        def do_POST(self):
            return None

        def do_HEAD(self):
            return None

    class Server:
        RequestHandlerClass = Handler

        def process_request(self, request_socket, client_address):
            def run_handler():
                try:
                    self.finish_request(request_socket, client_address)
                finally:
                    self.shutdown_request(request_socket)

            thread = threading.Thread(target=run_handler, daemon=True)
            thread.start()
            handler_threads.append(thread)

        def finish_request(self, request_socket, _client_address):
            assert request_socket.recv(1) == b""
            close_observed.set()
            handler_release.wait()

        def shutdown_request(self, request_socket):
            request_socket.close()

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    server_connection, client_connection = socket.socketpair()
    server.process_request(server_connection, ("fixture", 1))
    retirement_errors = []

    def retire():
        try:
            gate_harness_module.retire_fixture_http_connections(server, timeout=0.1)
        except BaseException as error:
            retirement_errors.append(error)

    retirement = threading.Thread(target=retire, daemon=True)
    retirement.start()
    try:
        assert close_observed.wait(timeout=1)
        retirement.join(timeout=1)
        assert not retirement.is_alive()
        assert len(retirement_errors) == 1
        assert "retained 1 accepted connection" in str(retirement_errors[0])
        assert handler_threads[0].is_alive()
    finally:
        handler_release.set()
        client_connection.close()
        for thread in handler_threads:
            thread.join(timeout=1)
    assert server._fixture_http_request_activity.connections == set()
    assert not handler_threads[0].is_alive()


def test_fixture_persistent_route_protocols_are_not_counted_as_finite_requests():
    called = []

    class Handler:
        close_connection = False

        def do_GET(self):
            called.append(self.path)

        def do_POST(self):
            called.append(self.path)

        def do_HEAD(self):
            called.append(self.path)

    class Server:
        RequestHandlerClass = Handler

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    activity = server._fixture_http_request_activity
    persistent_routes = [
        route
        for route in ALL_ROUTES
        if route.protocol in {RESPONSE_SSE, RESPONSE_WEBSOCKET}
    ]

    assert persistent_routes
    persistent_paths = [route.path.replace("*", "fixture") for route in persistent_routes]
    for route in persistent_routes:
        handler = server.RequestHandlerClass()
        handler.path = route.path.replace("*", "fixture")
        vars(type(handler))[f"do_{route.method}"](handler)
        assert activity.active_finite == 0
        assert activity.active_paths == {}

    assert called == persistent_paths

    gate_harness_module.seal_fixture_http_requests(server)
    called.clear()
    late_handlers = []
    for route in persistent_routes:
        handler = server.RequestHandlerClass()
        handler.path = route.path.replace("*", "fixture")
        late_handlers.append(handler)
        vars(type(handler))[f"do_{route.method}"](handler)

    assert called == []
    assert all(handler.close_connection is True for handler in late_handlers)
    assert activity.late_request_paths == [
        f"{route.method} {path}"
        for route, path in zip(persistent_routes, persistent_paths, strict=True)
    ]


def test_fixture_request_tracker_wraps_inherited_handler_methods():
    calls = []

    class ParentHandler:
        def do_GET(self):
            calls.append(self.path)

        def do_POST(self):
            calls.append(self.path)

        def do_HEAD(self):
            calls.append(self.path)

    class Handler(ParentHandler):
        pass

    class Server:
        RequestHandlerClass = Handler

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    handler = server.RequestHandlerClass()
    handler.path = "/api/ping"
    handler.do_GET()

    assert calls == ["/api/ping"]


def test_browser_fixture_closes_every_persistent_route_before_finite_teardown(monkeypatch):
    persistent_routes = [
        route
        for route in ALL_ROUTES
        if route.protocol in {RESPONSE_SSE, RESPONSE_WEBSOCKET}
    ]
    persistent_paths = [route.path.replace("*", "fixture") for route in persistent_routes]
    release_transports = threading.Event()
    started = {path: threading.Event() for path in persistent_paths}
    finished = {path: threading.Event() for path in persistent_paths}
    calls = []

    class Handler:
        close_connection = False

        def do_GET(self):
            started[self.path].set()
            release_transports.wait()
            finished[self.path].set()

        def do_POST(self):
            raise AssertionError("the route registry currently owns no persistent POST route")

        def do_HEAD(self):
            raise AssertionError("HEAD must remain finite")

    class Server:
        RequestHandlerClass = Handler
        server_address = ("127.0.0.1", 43210)
        app = object()

        def shutdown(self):
            calls.append("shutdown")

        def server_close(self):
            calls.append("server-close")

    class ServerThread:
        def join(self, timeout):
            calls.append(("acceptor-join", timeout))

        def is_alive(self):
            return False

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/persistent"

        def get(self, url):
            calls.append(("navigate", url))
            release_transports.set()

        def get_log(self, kind):
            assert kind == "browser"
            return []

    server = Server()
    gate_harness_module.track_fixture_http_requests(server)
    handler_threads = []
    for path in persistent_paths:
        handler = server.RequestHandlerClass()
        handler.path = path
        thread = threading.Thread(target=handler.do_GET, daemon=True)
        thread.start()
        handler_threads.append(thread)
    assert persistent_paths
    assert all(event.wait(timeout=1) for event in started.values())
    assert server._fixture_http_request_activity.active_finite == 0

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: calls.append("quiesce") or {"diagnosticMode": "retained-js"},
    )
    monkeypatch.setattr(
        gate_harness_module,
        "assert_browser_journey_error_free",
        lambda _browser: calls.append("gate"),
    )

    def stop_app(_app, *, label):
        assert label == "persistent fixture handler"
        assert all(event.wait(timeout=1) for event in finished.values())
        calls.append("stop-app")

    monkeypatch.setattr(gate_harness_module, "stop_fixture_app_runtime", stop_app)
    browser_layout.finish_browser_fixture_boundary(
        FixtureBrowser(),
        "http://127.0.0.1:43210",
        lambda: gate_harness_module.stop_fixture_http_app(
            server.app,
            server,
            ServerThread(),
            label="persistent fixture handler",
        ),
    )

    for thread in handler_threads:
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert calls == [
        "quiesce",
        "gate",
        ("navigate", "about:blank"),
        "shutdown",
        "stop-app",
        "server-close",
        ("acceptor-join", 3),
    ]


def test_browser_fixture_gate_failure_disconnects_before_app_cleanup_and_preserves_the_gate(monkeypatch):
    calls = []
    gate_failure = AssertionError("retained browser journey failure")

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/predecessor"

        def get(self, url):
            calls.append(("navigate", url))

        def get_log(self, kind):
            assert kind == "browser"
            return []

    class FixtureServer:
        server_address = ("127.0.0.1", 43210)
        app = object()

    browser = FixtureBrowser()
    server = FixtureServer()
    thread = object()

    def fail_gate(current):
        calls.append(("gate", current))
        raise gate_failure

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda current: calls.append(("quiesce", current)) or {"diagnosticMode": "retained-js"},
    )
    monkeypatch.setattr(gate_harness_module, "assert_browser_journey_error_free", fail_gate)
    monkeypatch.setattr(
        browser_layout,
        "stop_fixture_http_app",
        lambda app, current_server, current_thread, *, label: calls.append(
            ("stop", app, current_server, current_thread, label)
        ),
    )

    with pytest.raises(AssertionError) as caught:
        browser_layout.stop_browser_server(server, thread, browser=browser)

    assert caught.value is gate_failure
    assert calls == [
        ("quiesce", browser),
        ("gate", browser),
        ("navigate", "about:blank"),
        ("stop", server.app, server, thread, "isolated browser"),
    ]


def test_browser_fixture_finish_reads_ring_before_stop_and_rejects_late_warning(monkeypatch):
    calls = []
    ring = ServerLogRing(capacity=8)

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/live"

        def get(self, url):
            calls.append(("navigate", url))

        def get_log(self, kind):
            assert kind == "browser"
            return []

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: calls.append("quiesce") or {"diagnosticMode": "retained-js"},
    )
    monkeypatch.setattr(
        gate_harness_module,
        "assert_browser_journey_error_free",
        lambda _browser, **_kwargs: calls.append("full-gate"),
    )

    def cleanup():
        calls.append("stop")
        ring.emit("warning", "fixture-cleanup", "warning emitted while fixture owners stopped")

    with pytest.raises(AssertionError, match="warning emitted while fixture owners stopped"):
        browser_layout.finish_browser_fixture_boundary(
            FixtureBrowser(),
            "http://127.0.0.1:43210",
            cleanup,
            server_log_reader=ring.payload,
        )

    assert calls == ["quiesce", "full-gate", ("navigate", "about:blank"), "stop"]


def test_browser_fixture_finish_retires_page_before_settling_delayed_app_work(monkeypatch):
    calls = []
    settlement_started = threading.Event()
    release_settlement = threading.Event()

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/live"

        def get(self, url):
            calls.append(("navigate", url))

        def get_log(self, kind):
            assert kind == "browser"
            return []

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: calls.append("browser-quiescent") or {"diagnosticMode": "retained-js"},
    )
    monkeypatch.setattr(
        gate_harness_module,
        "assert_browser_journey_error_free",
        lambda _browser, **_kwargs: calls.append("strict-evidence"),
    )

    def settle_app():
        calls.append("settlement-started")
        settlement_started.set()
        assert release_settlement.wait(timeout=1)
        calls.append("settlement-complete")

    worker = threading.Thread(
        target=browser_layout.finish_browser_fixture_boundary,
        args=(FixtureBrowser(), "http://127.0.0.1:43210", lambda: calls.append("cleanup")),
        kwargs={"settle_app": settle_app},
    )
    worker.start()
    assert settlement_started.wait(timeout=1)
    assert calls[:3] == [
        "browser-quiescent",
        "strict-evidence",
        ("navigate", "about:blank"),
    ]
    release_settlement.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert calls == [
        "browser-quiescent",
        "strict-evidence",
        ("navigate", "about:blank"),
        "settlement-started",
        "settlement-complete",
        "cleanup",
    ]


def test_fixture_evidence_settlement_joins_watcher_then_session_files_then_jobd():
    calls = []
    watcher_stop = threading.Event()

    def watcher_worker():
        watcher_stop.wait()
        calls.append("tmux-signal-joined")

    watcher_thread = threading.Thread(target=watcher_worker)
    watcher_thread.start()

    class SessionFilesService:
        def wait_for_idle(self, timeout):
            calls.append(("session-files", timeout))
            return True

    class FixtureApp:
        def __init__(self):
            self.tmux_signal_event_watcher = SimpleNamespace(thread=watcher_thread)
            self.session_files_service = SessionFilesService()
            self.queued_delivery_ledger = SimpleNamespace()

        def stop_client_event_watcher(self):
            calls.append("client-event-watcher")
            watcher_stop.set()

        def wait_for_jobd_operations_terminal(self, timeout):
            calls.append(("jobd-operations", timeout))

    gate_harness_module.settle_fixture_app_evidence_boundary(
        FixtureApp(),
        label="fixture evidence",
    )

    assert calls == [
        "client-event-watcher",
        "tmux-signal-joined",
        ("session-files", 3),
        ("jobd-operations", 3),
    ]


def test_browser_fixture_finish_rejects_warning_between_full_gate_and_pre_stop_snapshot(monkeypatch):
    clean = {
        "ok": True,
        "epoch": "fixture-ring",
        "sequence": 0,
        "capacity": 8,
        "logs": [],
        "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
    }
    warning = {
        **clean,
        "sequence": 1,
        "logs": [{
            "id": 1,
            "level": "warning",
            "source": "fixture-server",
            "category": "server",
            "message": "warning after full gate before pre-stop snapshot",
        }],
    }
    reads = iter((clean, warning, warning))

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/app"

        def get(self, url):
            assert url == "about:blank"

        def get_log(self, kind):
            assert kind == "browser"
            return []

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: {"diagnosticMode": "retained-js"},
    )

    def gate(_browser, **options):
        payload = options["server_log_reader"]()
        return {"serverLogCursor": payload}

    monkeypatch.setattr(gate_harness_module, "assert_browser_journey_error_free", gate)
    with pytest.raises(AssertionError, match="warning after full gate before pre-stop snapshot"):
        browser_layout.finish_browser_fixture_boundary(
            FixtureBrowser(),
            "http://127.0.0.1:43210",
            lambda: None,
            server_log_reader=lambda: next(reads),
        )


def test_browser_fixture_finish_rejects_browser_warning_at_atomic_retirement(monkeypatch):
    calls = []
    late_warning = AssertionError("browser warning after full gate before retirement")

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/app"

        def get(self, url):
            calls.append(("legacy-navigate", url))

        def get_log(self, kind):
            assert kind == "browser"
            return []

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: calls.append("quiesce") or {"diagnosticMode": "retained-js"},
    )
    monkeypatch.setattr(
        gate_harness_module,
        "assert_browser_journey_error_free",
        lambda _browser, **_kwargs: calls.append("full-gate"),
    )

    def retire(_browser):
        calls.append("atomic-retirement")
        raise late_warning

    monkeypatch.setattr(
        gate_harness_module,
        "retire_browser_after_strict_diagnostic_gate",
        retire,
        raising=False,
    )

    with pytest.raises(AssertionError) as raised:
        browser_layout.finish_browser_fixture_boundary(
            FixtureBrowser(),
            "http://127.0.0.1:43210",
            lambda: calls.append("stop"),
        )

    assert raised.value is late_warning
    assert calls == ["quiesce", "full-gate", "atomic-retirement", "stop"]


def test_browser_fixture_finish_gates_and_blanks_every_owned_driver(monkeypatch):
    calls = []
    viewer_failure = AssertionError("viewer retained JS failure")
    ring = ServerLogRing(capacity=8)

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        def __init__(self, name):
            self.name = name
            self.current_url = f"http://127.0.0.1:43210/{name}"

        def get(self, url):
            calls.append(("navigate", self.name, url))

        def get_log(self, kind):
            assert kind == "browser"
            return []

    host = FixtureBrowser("host")
    viewer = FixtureBrowser("viewer")
    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda current: calls.append(("quiesce", current.name)) or {"diagnosticMode": "retained-js"},
    )

    def gate(current, **_kwargs):
        calls.append(("gate", current.name))
        if current is viewer:
            raise viewer_failure

    monkeypatch.setattr(gate_harness_module, "assert_browser_journey_error_free", gate)

    with pytest.raises(AssertionError) as raised:
        browser_layout.finish_browser_fixture_boundary(
            (host, viewer),
            "http://127.0.0.1:43210",
            lambda: calls.append("stop"),
            server_log_reader=ring.payload,
        )

    assert raised.value is viewer_failure
    assert calls == [
        ("quiesce", "host"),
        ("quiesce", "viewer"),
        ("gate", "host"),
        ("gate", "viewer"),
        ("navigate", "host", "about:blank"),
        ("navigate", "viewer", "about:blank"),
        "stop",
    ]


def test_browser_fixture_finish_uses_process_ring_and_never_fetches_for_off_origin_browser():
    ring = ServerLogRing(capacity=8)
    boundary = ring.payload()
    ring.emit("warning", "fixture-server", "fixture teardown retained process warning")

    class FixtureViewer:
        current_url = "http://127.0.0.1:43210/external/fixture#t=secret"
        ring_requests = 0

        def execute_script(self, source, *_args):
            if "return lifecycle ? {diagnosticMode" in source:
                return {"diagnosticMode": "retained-js"}
            if "const isArray = Array.isArray(jsDebugEvents)" in source:
                return {
                    "reachable": True,
                    "isArray": True,
                    "events": [],
                    "errors": [],
                    "receiptBarrier": clean_browser_receipt_barrier(),
                }
            if "return [...state.consumedServerLogIds]" in source:
                return []
            if "const state = window.__yolomuxBrowserJourneyGate" in source:
                return {"reachable": False, "visitedSurfaces": []}
            raise AssertionError(f"unexpected browser script: {source}")

        def execute_async_script(self, source):
            assert "/api/logs" in source
            self.ring_requests += 1
            return {"reachable": True, "status": 403, "payload": None, "parseError": ""}

        def get_log(self, kind):
            assert kind == "browser"
            return []

        def get(self, url):
            assert url == "about:blank"

    viewer = FixtureViewer()
    stopped = []
    with pytest.raises(AssertionError, match="fixture teardown retained process warning"):
        browser_layout.finish_browser_fixture_boundary(
            viewer,
            "http://127.0.0.1:43210",
            lambda: stopped.append(True),
            server_log_reader=ring.payload,
            server_log_boundary=boundary,
            wait_for_api_quiescence=False,
        )
    assert viewer.ring_requests == 0
    assert stopped == [True]


def test_browser_fixture_origin_ownership_requires_exact_port(monkeypatch):
    calls = []

    class BrowserOnDifferentPort:
        current_url = "http://127.0.0.1:43210/app"

        def get(self, url):
            calls.append(("navigate", url))

    monkeypatch.setattr(
        gate_harness_module,
        "assert_browser_journey_error_free",
        lambda *_args, **_kwargs: pytest.fail("different-port browser must not be gated"),
    )
    browser_layout.finish_browser_fixture_boundary(
        BrowserOnDifferentPort(),
        "http://127.0.0.1:4321",
        lambda: calls.append("stop"),
    )
    assert calls == ["stop"]


def test_browser_fixture_start_boundary_warning_is_aggregated_with_exact_origin_failure():
    ring = ServerLogRing(capacity=8)
    boundary = ring.payload()
    ring.emit("warning", "fixture-regression", "start-boundary warning survives off-origin browser")

    class BrowserOutsideFixtureOrigin:
        current_url = "data:text/html,off-origin"

    with pytest.raises(AssertionError, match="exact live origin") as caught:
        browser_layout.finish_browser_fixture_boundary(
            BrowserOutsideFixtureOrigin(),
            "http://127.0.0.1:43210",
            lambda: None,
            server_log_reader=ring.payload,
            server_log_boundary=boundary,
            require_owned_browsers=True,
        )

    assert caught.value.__cause__ is not None
    assert "start-boundary warning survives off-origin browser" in str(caught.value.__cause__)


def test_stop_browser_server_rejects_singular_and_plural_browser_inputs_after_cleanup(monkeypatch):
    cleaned = []

    class Server:
        server_address = ("127.0.0.1", 43210)
        app = object()

    def finish(_browsers, _base_url, cleanup, **_kwargs):
        cleanup()

    monkeypatch.setattr(browser_layout, "finish_browser_fixture_boundary", finish)
    monkeypatch.setattr(
        browser_layout,
        "stop_fixture_http_app",
        lambda app, server, thread, *, label: cleaned.append((app, server, thread, label)),
    )
    with pytest.raises(ValueError, match="either browser or browsers"):
        browser_layout.stop_browser_server(
            Server(),
            object(),
            browser=object(),
            browsers=(object(),),
        )
    assert len(cleaned) == 1
    assert cleaned[0][0] is Server.app
    assert cleaned[0][3] == "isolated browser"


@pytest.mark.parametrize("failure_stage", ("paths", "tmux", "app"))
def test_fixture_runtime_start_rolls_back_each_acquired_owner(monkeypatch, tmp_path, failure_stage):
    calls = []
    paths = object()
    tmux = SimpleNamespace(sessions=["fixture"])

    def isolate(*_args):
        calls.append("paths-start")
        if failure_stage == "paths":
            raise RuntimeError("injected paths failure")
        return paths

    def start_tmux(*_args, **_kwargs):
        calls.append("tmux-start")
        if failure_stage == "tmux":
            raise RuntimeError("injected tmux failure")
        return tmux

    def app(*_args, **_kwargs):
        calls.append("app-start")
        if failure_stage == "app":
            raise RuntimeError("injected app failure")
        return object()

    monkeypatch.setattr(browser_layout, "isolate_browser_runtime_paths", isolate)
    monkeypatch.setattr(browser_layout, "start_isolated_tmux_runtime", start_tmux)
    monkeypatch.setattr(browser_layout, "TmuxWebtermApp", app)
    monkeypatch.setattr(
        browser_layout,
        "stop_isolated_tmux_runtime",
        lambda runtime: calls.append(("tmux-stop", runtime)),
    )
    monkeypatch.setattr(
        browser_layout,
        "cleanup_isolated_browser_runtime_paths",
        lambda runtime_paths: calls.append(("paths-stop", runtime_paths)),
    )

    with pytest.raises(RuntimeError, match=f"injected {failure_stage} failure"):
        browser_layout.start_fixture_runtime(
            monkeypatch,
            tmp_path,
            browser_layout.FixtureRuntimeOptions(),
        )

    expected = ["paths-start"]
    if failure_stage != "paths":
        expected.append("tmux-start")
    if failure_stage == "app":
        expected.append("app-start")
    expected.extend((
        ("tmux-stop", tmux if failure_stage == "app" else None),
        ("paths-stop", paths if failure_stage != "paths" else None),
    ))
    assert calls == expected


@pytest.mark.parametrize("failure_stage", ("bind", "request-tracker", "thread-start"))
def test_browser_server_start_rolls_back_each_acquired_owner(monkeypatch, tmp_path, failure_stage):
    calls = []

    class App:
        def stop_client_event_watcher(self):
            calls.append("client-watcher")

        def stop_jobd_operation_service(self):
            calls.append("jobd-operations")

        def demote_background_owner(self):
            calls.append("background-owner")

        def stop_auto_approve_all(self):
            calls.append("auto-approve")

    class Server:
        def __init__(self, *_args, **_kwargs):
            if failure_stage == "bind":
                raise OSError("injected bind failure")
            self._fixture_http_request_activity = object()

        def server_close(self):
            calls.append("server-close")

        def serve_forever(self):
            raise AssertionError("injected thread must fail before serving")

    class Thread:
        def __init__(self, **_kwargs):
            pass

        def is_alive(self):
            return False

        def start(self):
            if failure_stage == "thread-start":
                raise RuntimeError("injected thread start failure")

    def track(_server):
        if failure_stage == "request-tracker":
            raise RuntimeError("injected request tracker failure")

    monkeypatch.setattr(gate_harness_module, "TmuxWebtermHTTPServer", Server)
    monkeypatch.setattr(gate_harness_module, "track_fixture_http_requests", track)
    monkeypatch.setattr(gate_harness_module.threading, "Thread", Thread)
    monkeypatch.setattr(gate_harness_module, "prepare_fixture_http_app", lambda *_args: None)

    with pytest.raises((OSError, RuntimeError), match=failure_stage.replace("-", " ")):
        browser_layout.start_browser_server(monkeypatch, tmp_path, App())

    assert calls[:4] == ["client-watcher", "jobd-operations", "background-owner", "auto-approve"]
    assert ("server-close" in calls) is (failure_stage != "bind")


def test_stateful_journey_stop_preserves_gate_failure_when_port_reacquire_fails(monkeypatch):
    gate_failure = AssertionError("fixture gate failure")
    reacquire_failure = OSError("port became unavailable")
    journey = object.__new__(gate_harness_module.GateStatefulJourney)
    journey.app = object()
    journey.server = object()
    journey.thread = object()
    journey.server_log_boundary = {"epoch": "fixture"}
    journey.port = 43210
    journey.request = SimpleNamespace(node=SimpleNamespace(funcargs={"browser": None}))
    journey.port_lease = SimpleNamespace(reacquire=lambda: (_ for _ in ()).throw(reacquire_failure))

    monkeypatch.setattr(
        gate_harness_module,
        "finish_browser_fixture_boundary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(gate_failure),
    )

    with pytest.raises(AssertionError) as caught:
        journey.stop()

    assert caught.value is gate_failure
    assert caught.value.__cause__ is reacquire_failure


def test_stateful_journey_start_rolls_back_background_owner_failure_and_reacquires_port(monkeypatch):
    calls = []

    class App:
        def start_background_owner(self, **_kwargs):
            raise RuntimeError("injected background owner failure")

        def stop_client_event_watcher(self):
            calls.append("client-watcher")

        def stop_jobd_operation_service(self):
            calls.append("jobd-operations")

        def demote_background_owner(self):
            calls.append("background-owner")

        def stop_auto_approve_all(self):
            calls.append("auto-approve")

    class Server:
        def __init__(self, *_args):
            pass

        def server_close(self):
            calls.append("server-close")

    journey = object.__new__(gate_harness_module.GateStatefulJourney)
    journey.request = SimpleNamespace()
    journey.monkeypatch = monkeypatch
    journey.tmux = SimpleNamespace(sessions=("fixture",))
    journey.port_lease = SimpleNamespace(
        release=lambda: calls.append("release"),
        reacquire=lambda: calls.append("reacquire"),
    )
    journey.port = 43210
    journey.app = None
    journey.server = None
    journey.thread = None
    journey.server_log_boundary = None
    journey.starts = 0

    monkeypatch.setattr(gate_harness_module.app_module, "TmuxWebtermApp", lambda _sessions: App())
    monkeypatch.setattr(gate_harness_module, "TmuxWebtermHTTPServer", Server)
    monkeypatch.setattr(gate_harness_module, "track_fixture_http_requests", lambda _server: None)
    monkeypatch.setattr(gate_harness_module, "prepare_fixture_http_app", lambda *_args: None)

    with pytest.raises(RuntimeError, match="injected background owner failure"):
        journey.start()

    assert calls == [
        "release", "client-watcher", "jobd-operations", "background-owner", "auto-approve", "server-close", "reacquire",
    ]
    assert journey.app is None and journey.server is None and journey.thread is None


def test_gate_server_start_rolls_back_thread_creation_failure_and_reacquires_port(monkeypatch):
    calls = []

    class App:
        def __init__(self):
            # The shared fixture scheduler-lease seam: setup pins this exact client and teardown's
            # demote releases it, so the rollback path can prove exactly-once release of the pin.
            self.job_client = gate_harness_module.RecordingSchedulerClient()

        def stop_client_event_watcher(self):
            calls.append("client-watcher")

        def stop_jobd_operation_service(self):
            calls.append("jobd-operations")

        def demote_background_owner(self):
            # Production's ``demote_background_owner`` releases the jobd scheduler lease here; the
            # fake models that release so the rollback teardown cannot leak the pinned lease.
            self.job_client.stop_for_scheduler()
            calls.append("background-owner")

        def stop_auto_approve_all(self):
            # ``stop_auto_approve_all`` also calls ``stop_for_scheduler`` in production; the fake
            # models that second, idempotent call so the test proves it does NOT double-release.
            self.job_client.stop_for_scheduler()
            calls.append("auto-approve")

    class Server:
        def __init__(self, *_args):
            pass

        def server_close(self):
            calls.append("server-close")

        def serve_forever(self):
            raise AssertionError("thread construction must fail first")

    class FailingThread:
        def __init__(self, **_kwargs):
            raise RuntimeError("injected thread construction failure")

    port_lease = SimpleNamespace(
        address=("127.0.0.1", 43210),
        release=lambda: calls.append("release"),
        reacquire=lambda: calls.append("reacquire"),
    )
    created_apps = []
    monkeypatch.setattr(gate_harness_module, "TmuxWebtermHTTPServer", Server)
    monkeypatch.setattr(gate_harness_module, "track_fixture_http_requests", lambda _server: None)
    monkeypatch.setattr(gate_harness_module, "prepare_fixture_http_app", lambda *_args: None)
    monkeypatch.setattr(gate_harness_module.threading, "Thread", FailingThread)

    def make_app(_sessions):
        app = App()
        created_apps.append(app)
        return app

    generator = gate_harness_module._serve_gate_live_server(
        SimpleNamespace(node=SimpleNamespace(funcargs={})),
        monkeypatch,
        make_app,
        SimpleNamespace(),
        port_lease,
        SimpleNamespace(sessions=("fixture",)),
    )
    with pytest.raises(RuntimeError, match="injected thread construction failure"):
        next(generator)

    assert calls == [
        "release", "client-watcher", "jobd-operations", "background-owner", "auto-approve", "server-close", "reacquire",
    ]
    # The pin was taken before the thread-construction failure, and the rollback released it
    # exactly once -- no leaked lease, no double release -- even though two teardown owners each
    # call the idempotent release.
    [app] = created_apps
    assert app.job_client.start_for_scheduler_calls == 1
    assert app.job_client.stop_for_scheduler_calls == 2
    assert app.job_client.releases == 1
    assert app.job_client.holds_scheduler_lease is False


def test_browser_fixture_ring_rejects_changed_logs_without_sequence_increment(monkeypatch):
    calls = []
    clean = {
        "ok": True,
        "epoch": "fixture-ring",
        "sequence": 1,
        "capacity": 8,
        "logs": [{
            "id": 1,
            "level": "info",
            "source": "fixture-server",
            "category": "server",
            "message": "clean sequence one",
        }],
        "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
    }
    changed = {
        **clean,
        "logs": [{
            "id": 1,
            "level": "warning",
            "source": "fixture-cleanup",
            "category": "server",
            "message": "warning reused sequence one",
        }],
    }
    reads = iter((clean, clean, changed))

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/app"

        def get(self, url):
            calls.append(("navigate", url))

        def get_log(self, kind):
            assert kind == "browser"
            return []

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: calls.append("quiesce") or {"diagnosticMode": "retained-js"},
    )
    monkeypatch.setattr(
        gate_harness_module,
        "assert_browser_journey_error_free",
        lambda _browser, **options: options["server_log_reader"](),
    )
    with pytest.raises(AssertionError, match="without advancing sequence"):
        browser_layout.finish_browser_fixture_boundary(
            FixtureBrowser(),
            "http://127.0.0.1:43210",
            lambda: calls.append("stop"),
            server_log_reader=lambda: next(reads),
        )
    assert calls == ["quiesce", ("navigate", "about:blank"), "stop"]


def test_browser_fixture_ring_rejects_mutated_retained_entry_when_sequence_advances(monkeypatch):
    clean = {
        "ok": True,
        "epoch": "fixture-ring",
        "sequence": 1,
        "capacity": 8,
        "logs": [{
            "id": 1,
            "level": "info",
            "source": "fixture-server",
            "category": "server",
            "message": "immutable retained entry",
        }],
        "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
    }
    mutated = {
        **clean,
        "sequence": 2,
        "logs": [
            {
                "id": 1,
                "level": "warning",
                "source": "fixture-server",
                "category": "server",
                "message": "mutated retained entry",
            },
            {
                "id": 2,
                "level": "info",
                "source": "fixture-server",
                "category": "server",
                "message": "new retained entry",
            },
        ],
    }
    reads = iter((clean, clean, mutated))

    class FixtureBrowser(_RetainedJsLifecycleDriver):
        current_url = "http://127.0.0.1:43210/app"

        def get(self, url):
            assert url == "about:blank"

        def get_log(self, kind):
            assert kind == "browser"
            return []

    monkeypatch.setattr(
        gate_harness_module,
        "wait_for_fixture_api_quiescence",
        lambda _browser: {"diagnosticMode": "retained-js"},
    )

    def gate(_browser, **options):
        payload = options["server_log_reader"]()
        return {"serverLogCursor": payload}

    monkeypatch.setattr(gate_harness_module, "assert_browser_journey_error_free", gate)
    with pytest.raises(AssertionError, match="mutated retained entry"):
        browser_layout.finish_browser_fixture_boundary(
            FixtureBrowser(),
            "http://127.0.0.1:43210",
            lambda: None,
            server_log_reader=lambda: next(reads),
        )


@pytest.mark.parametrize(
    "payload",
    (
        {
            "ok": True,
            "epoch": "ring",
            "sequence": 2,
            "capacity": 1,
            "logs": [{"id": 1}, {"id": 2}],
            "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
        },
        {
            "ok": True,
            "epoch": "ring",
            "sequence": 2,
            "capacity": 4,
            "logs": [{"id": 2}, {"id": 2}],
            "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
        },
        {
            "ok": True,
            "epoch": "ring",
            "sequence": 2,
            "capacity": 4,
            "logs": [{"id": 3}],
            "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
        },
        {
            "ok": True,
            "epoch": "ring",
            "sequence": 4,
            "capacity": 4,
            "logs": [{"id": 4}],
            "dropped": {"count": 2, "first_id": 1, "last_id": 3, "by_level": {"warning": 1}},
        },
        {
            "ok": True,
            "epoch": "ring",
            "sequence": 3,
            "capacity": 4,
            "logs": [{"id": 1}, {"id": 3}],
            "dropped": {"count": 0, "first_id": None, "last_id": None, "by_level": {}},
        },
    ),
)
def test_server_ring_validator_rejects_malformed_continuity(payload):
    with pytest.raises(AssertionError, match="continuity"):
        browser_console.validate_server_log_ring_payload(payload)


def test_fixture_stops_accepted_jobd_operations_before_demoting_local_services():
    calls = []

    class FixtureApp:
        def __init__(self):
            self.queued_delivery_ledger = SimpleNamespace()

        def stop_client_event_watcher(self):
            calls.append("client-watcher")

        def wait_for_jobd_operations_terminal(self, timeout):
            assert timeout == 3
            calls.append("jobd-operations-terminal")

        def stop_jobd_operation_service(self):
            calls.append("jobd-operations")

        def demote_background_owner(self):
            calls.append("background-demotion")

        def stop_auto_approve_all(self):
            self.stop_jobd_operation_service()
            calls.append("auto-approve")

    stop_fixture_app_runtime(FixtureApp(), label="fixture accepted-operation ordering")

    assert calls.index("jobd-operations-terminal") < calls.index("jobd-operations")
    assert calls.index("jobd-operations") < calls.index("background-demotion")


def test_fixture_joins_metadata_product_worker_before_demoting_local_services():
    stop_event = threading.Event()
    worker_finished = threading.Event()

    def metadata_product_worker():
        stop_event.wait()
        worker_finished.set()

    worker = threading.Thread(target=metadata_product_worker, name="fixture-metadata-product")
    worker.start()

    class MetadataWarmRecord:
        pass

    record = MetadataWarmRecord()
    record.stop_event = stop_event
    record.worker = worker

    class FixtureApp:
        def __init__(self):
            self.metadata_warm_lock = threading.Lock()
            self.metadata_warm_record = record

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            assert worker_finished.is_set()

        def stop_auto_approve_all(self):
            pass

    try:
        stop_fixture_app_runtime(FixtureApp(), label="fixture metadata-product ordering")
    finally:
        stop_event.set()
        worker.join(timeout=1)

    assert not worker.is_alive()


def _fixture_spawn_ownership(pid, *members):
    identities = members or ((pid, pid + 1000),)
    return SimpleNamespace(
        leader_pid=pid,
        process_group=pid,
        session_id=pid,
        generation_marker=f"{pid:032x}",
        member_identities=tuple((member_pid, f"proc:{start_time}") for member_pid, start_time in identities),
    )


def _fixture_process_table(pid, *members):
    identities = members or ((pid, pid + 1000),)
    return {
        member_pid: SimpleNamespace(
            pgid=pid,
            session_id=pid,
            start_time=start_time,
            start_identity=f"proc:{start_time}",
            command="python fixture-local-service",
            spawn_generation=f"{pid:032x}",
        )
        for member_pid, start_time in identities
    }


def test_fixture_app_writers_stop_before_single_pass_root_removal(tmp_path, monkeypatch):
    root = tmp_path / "owned-runtime"
    root.mkdir()
    release_writer = threading.Event()
    writer_finished = threading.Event()

    def late_write():
        release_writer.wait()
        (root / "last-owned-write").write_text("complete", encoding="utf-8")
        writer_finished.set()

    writer = threading.Thread(target=late_write, name="fixture-late-writer", daemon=True)
    writer.start()

    class FixtureProcess:
        pid = 43211

        def poll(self):
            return 0 if writer_finished.is_set() else None

        def wait(self, timeout):
            writer.join(timeout=timeout)
            assert not writer.is_alive()
            return 0

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()
            self.spawn_ownership = _fixture_spawn_ownership(self.process.pid)

        def _reap_exited_child(self, process):
            assert process is self.process
            assert writer_finished.is_set()
            self.process = None

    class FixtureClient:
        def __init__(self):
            self.registry = FixtureRegistry()

    class FixtureApp:
        def __init__(self):
            self.status_client = FixtureClient()

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(gate_harness_module.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(gate_harness_module, "bounded_process_table", lambda: _fixture_process_table(43211))

    def stop_group(_process_group, signal_number):
        if signal_number == 0:
            if writer_finished.is_set():
                raise ProcessLookupError
            return
        assert signal_number == gate_harness_module.signal.SIGTERM
        release_writer.set()

    monkeypatch.setattr(gate_harness_module.os, "killpg", stop_group)

    stop_fixture_app_runtime(FixtureApp(), label="fixture isolation regression")
    shutil.rmtree(root)

    assert not root.exists()


def test_fixture_runtime_seals_local_service_demand_before_late_producer_can_replace_generation(monkeypatch):
    initial_ownership = _fixture_spawn_ownership(43209)
    replacement_ownership = _fixture_spawn_ownership(43219)
    stopped_ownership = []

    class FixtureRegistry:
        def __init__(self):
            self.process = SimpleNamespace(pid=initial_ownership.leader_pid)
            self.spawn_ownership = initial_ownership
            self.starts_sealed = False

        def seal_starts(self):
            self.starts_sealed = True

        def ensure_started(self):
            if self.starts_sealed:
                return False
            self.spawn_ownership = replacement_ownership
            self.process = SimpleNamespace(pid=replacement_ownership.leader_pid)
            return True

        def refresh_spawn_ownership(self):
            return self.spawn_ownership

    registry = FixtureRegistry()

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=registry)

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            assert registry.ensure_started() is False

        def stop_auto_approve_all(self):
            pass

    def stop_service(owner, *, label):
        assert label == "fixture late local-service demand"
        stopped_ownership.append(owner.ownership)
        assert owner.ownership == initial_ownership
        assert registry.spawn_ownership == initial_ownership

    monkeypatch.setattr(gate_harness_module, "stop_fixture_local_service_process", stop_service)

    stop_fixture_app_runtime(FixtureApp(), label="fixture late local-service demand")

    assert registry.starts_sealed is True
    assert stopped_ownership == [initial_ownership]


def test_fixture_runtime_seals_every_sibling_local_service_registry_before_demoting():
    class FixtureRegistry:
        process = None
        spawn_ownership = None

        def __init__(self):
            self.starts_sealed = False

        def seal_starts(self):
            self.starts_sealed = True

    registries = [FixtureRegistry() for _index in range(6)]

    class FixtureApp:
        def __init__(self):
            self.approval_client = SimpleNamespace(registry=registries[0])
            self.job_client = SimpleNamespace(registry=registries[1])
            self.search_indexer = SimpleNamespace(registry=registries[2])
            self.status_client = SimpleNamespace(registry=registries[3])
            self.watch_client = SimpleNamespace(registry=registries[4])
            self.stats_current_client = SimpleNamespace(
                _transport=SimpleNamespace(registry=registries[5])
            )

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            assert all(registry.starts_sealed for registry in registries)

        def stop_auto_approve_all(self):
            pass

    stop_fixture_app_runtime(FixtureApp(), label="fixture sibling local-service seal")

    assert all(registry.starts_sealed for registry in registries)


def test_fixture_stops_whole_owned_local_service_group_before_waiting(monkeypatch):
    state = {"group_stopped": False, "leader_terminated": False, "signals": []}

    class FixtureProcess:
        pid = 43210
        args = ["python3", "-m", "yolomux_lib.jobd", "--serve"]

        def poll(self):
            return -15 if state["group_stopped"] else None

        def terminate(self):
            state["leader_terminated"] = True

        def wait(self, timeout):
            if not state["group_stopped"]:
                raise gate_harness_module.subprocess.TimeoutExpired(self.args, timeout)
            return -15

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()
            self.spawn_ownership = _fixture_spawn_ownership(self.process.pid)

        def _reap_exited_child(self, process):
            assert process is self.process
            assert state["group_stopped"]
            self.process = None

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=FixtureRegistry())

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(gate_harness_module.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(gate_harness_module, "bounded_process_table", lambda: _fixture_process_table(43210))

    def stop_group(process_group, signal_number):
        if signal_number == 0:
            if state["group_stopped"]:
                raise ProcessLookupError(process_group)
            return
        state["signals"].append((process_group, signal_number))
        state["group_stopped"] = True

    monkeypatch.setattr(gate_harness_module.os, "killpg", stop_group)

    stop_fixture_app_runtime(FixtureApp(), label="fixture local-service group")

    assert state["signals"] == [(43210, gate_harness_module.signal.SIGTERM)]
    assert state["leader_terminated"] is False


def test_fixture_runtime_keeps_each_processless_registry_owner(monkeypatch):
    first_registry = SimpleNamespace(process=None, spawn_ownership=_fixture_spawn_ownership(43220))
    second_registry = SimpleNamespace(process=None, spawn_ownership=_fixture_spawn_ownership(43221))
    stopped = []

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=first_registry)
            self.status_client = SimpleNamespace(registry=second_registry)

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(
        gate_harness_module,
        "stop_fixture_local_service_process",
        lambda owner, **_kwargs: stopped.append(owner.registry),
    )

    stop_fixture_app_runtime(FixtureApp(), label="fixture processless registries")

    assert stopped == [first_registry, second_registry]


def test_fixture_stops_retained_service_group_after_its_leader_already_exited(monkeypatch):
    state = {"group_exists": True, "signals": [], "reaped": False}

    class FixtureProcess:
        pid = 43212

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()
            self.spawn_ownership = _fixture_spawn_ownership(self.process.pid, (43219, 44219))

        def _reap_exited_child(self, process):
            assert process is self.process
            state["reaped"] = True

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=FixtureRegistry())

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    def stop_group(process_group, signal_number):
        if signal_number == 0:
            if not state["group_exists"]:
                raise ProcessLookupError(process_group)
            return
        state["signals"].append((process_group, signal_number))
        state["group_exists"] = False

    monkeypatch.setattr(gate_harness_module.os, "killpg", stop_group)
    monkeypatch.setattr(
        gate_harness_module,
        "bounded_process_table",
        lambda: _fixture_process_table(43212, (43219, 44219)),
    )

    stop_fixture_app_runtime(FixtureApp(), label="fixture exited leader")

    assert state["signals"] == [(43212, gate_harness_module.signal.SIGTERM)]
    assert state["reaped"] is True


def test_fixture_teardown_uses_one_snapshot_when_same_generation_member_replaces_leader(tmp_path, monkeypatch):
    generation = "a" * 32
    state = {"term_sent": False, "killed": False, "signals": [], "barriers": []}
    registry = local_service_registry.LocalServiceRegistry(
        tmp_path,
        local_service_registry.LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )

    class FixtureProcess:
        pid = 43230

        def poll(self):
            return -9 if state["killed"] else None

        def wait(self, timeout=None):
            if not state["killed"]:
                raise gate_harness_module.subprocess.TimeoutExpired("fixture-service", timeout)
            return -9

    process = FixtureProcess()
    initial_ownership = local_service_registry.SpawnProcessOwnership(
        leader_pid=process.pid,
        process_group=process.pid,
        session_id=process.pid,
        generation_marker=generation,
        member_identities=((process.pid, "proc:44230"),),
    )
    registry.process = process
    registry.spawn_ownership = initial_ownership

    leader_table = _fixture_process_table(process.pid, (process.pid, 44230))
    replacement_table = _fixture_process_table(process.pid, (43231, 44231))

    def registry_process_table():
        if state["killed"]:
            return {}
        return replacement_table if state["term_sent"] else leader_table

    monkeypatch.setattr(local_service_registry, "bounded_process_table", registry_process_table)
    monkeypatch.setattr(local_service_registry, "process_spawn_generation", lambda _pid: generation)
    monkeypatch.setattr(gate_harness_module, "bounded_process_table", lambda: replacement_table)
    monkeypatch.setattr(gate_harness_module.os, "getpgid", lambda _pid: process.pid)

    class ExitBarrier:
        def __init__(self, identities):
            self.identities = tuple(identities)

        def wait(self, timeout):
            state["barriers"].append((self.identities, timeout))
            return state["killed"]

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            pass

    monkeypatch.setattr(gate_harness_module, "FixtureMemberExitBarrier", ExitBarrier)

    def signal_group(process_group, signal_number):
        assert process_group == process.pid
        if signal_number == 0:
            return
        state["signals"].append(signal_number)
        if signal_number == gate_harness_module.signal.SIGTERM:
            state["term_sent"] = True
        elif signal_number == gate_harness_module.signal.SIGKILL:
            state["killed"] = True

    monkeypatch.setattr(gate_harness_module.os, "killpg", signal_group)

    gate_harness_module.stop_fixture_local_service_process(
        gate_harness_module.FixtureLocalServiceProcess(registry, process, initial_ownership),
        label="fixture same-generation replacement",
    )

    assert state["signals"] == [gate_harness_module.signal.SIGTERM, gate_harness_module.signal.SIGKILL]
    assert [identities for identities, _timeout in state["barriers"]] == [
        ((process.pid, "proc:44230"),),
        ((43231, "proc:44231"),),
    ]
    assert registry.spawn_ownership.member_identities == ((43231, "proc:44231"),)


def test_fixture_escalates_stubborn_owned_service_group_within_original_bound(monkeypatch):
    state = {"group_exists": True, "signals": [], "waits": [], "reaped": False}

    class FixtureProcess:
        pid = 43214

        def poll(self):
            return None if state["group_exists"] else -9

        def wait(self, timeout):
            state["waits"].append(timeout)
            if state["group_exists"]:
                raise gate_harness_module.subprocess.TimeoutExpired("fixture-service", timeout)
            return -9

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()
            self.spawn_ownership = _fixture_spawn_ownership(self.process.pid)

        def _reap_exited_child(self, process):
            assert process is self.process
            state["reaped"] = True

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=FixtureRegistry())

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(gate_harness_module.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(gate_harness_module, "bounded_process_table", lambda: _fixture_process_table(43214))

    def stop_group(process_group, signal_number):
        if signal_number == 0:
            if not state["group_exists"]:
                raise ProcessLookupError(process_group)
            return
        state["signals"].append((process_group, signal_number))
        if signal_number == gate_harness_module.signal.SIGKILL:
            state["group_exists"] = False

    monkeypatch.setattr(gate_harness_module.os, "killpg", stop_group)

    stop_fixture_app_runtime(FixtureApp(), label="fixture stubborn service")

    assert state["signals"] == [
        (43214, gate_harness_module.signal.SIGTERM),
        (43214, gate_harness_module.signal.SIGKILL),
    ]
    assert len(state["waits"]) == 1
    assert 0 < state["waits"][0] <= 2
    assert state["reaped"] is True


def test_fixture_kill_waits_for_exact_owned_descendant_exit_event(tmp_path, monkeypatch):
    generation = "a" * 32
    state = {"owned": True, "signals": [], "barriers": []}
    registry = local_service_registry.LocalServiceRegistry(
        tmp_path,
        local_service_registry.LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )

    class ExitedLeader:
        pid = 43250

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    process = ExitedLeader()
    initial_ownership = local_service_registry.SpawnProcessOwnership(
        leader_pid=process.pid,
        process_group=process.pid,
        session_id=process.pid,
        generation_marker=generation,
        member_identities=((process.pid, "proc:44250"),),
    )
    registry.process = process
    registry.spawn_ownership = initial_ownership

    def process_table():
        if not state["owned"]:
            return {}
        return _fixture_process_table(process.pid, (43251, 44251))

    class ExitBarrier:
        def __init__(self, identities):
            self.identities = tuple(identities)

        def wait(self, timeout):
            state["barriers"].append((self.identities, timeout))
            if len(state["barriers"]) == 2:
                state["owned"] = False
            return not state["owned"]

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            pass

    monkeypatch.setattr(local_service_registry, "bounded_process_table", process_table)
    monkeypatch.setattr(local_service_registry, "process_spawn_generation", lambda _pid: generation)
    monkeypatch.setattr(gate_harness_module, "FixtureMemberExitBarrier", ExitBarrier)
    monkeypatch.setattr(
        gate_harness_module,
        "signal_fixture_process_group",
        lambda process_group, signal_number: state["signals"].append((process_group, signal_number)),
    )

    gate_harness_module.stop_fixture_local_service_process(
        gate_harness_module.FixtureLocalServiceProcess(registry, process, initial_ownership),
        label="fixture delayed descendant exit",
    )

    assert state["signals"] == [
        (process.pid, gate_harness_module.signal.SIGTERM),
        (process.pid, gate_harness_module.signal.SIGKILL),
    ]
    assert [identities for identities, _timeout in state["barriers"]] == [
        ((43251, "proc:44251"),),
        ((43251, "proc:44251"),),
    ]
    assert 0 < state["barriers"][0][1] <= 2.0
    assert 0 < state["barriers"][1][1] <= 1.0


@pytest.mark.parametrize(
    "failure_phase",
    ("client-watcher", "jobd-operations", "background-demotion", "auto-approve"),
)
def test_fixture_runtime_attempts_every_later_owner_after_one_phase_fails(monkeypatch, failure_phase):
    calls = []

    class FixtureProcess:
        pid = 43213

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()
            self.spawn_ownership = _fixture_spawn_ownership(self.process.pid)

        def _reap_exited_child(self, process):
            assert process is self.process
            calls.append("local-service")

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=FixtureRegistry())

        def phase(self, name):
            calls.append(name)
            if name == failure_phase:
                raise RuntimeError(f"injected {name} failure")

        def stop_client_event_watcher(self):
            self.phase("client-watcher")

        def stop_jobd_operation_service(self):
            self.phase("jobd-operations")

        def demote_background_owner(self):
            self.phase("background-demotion")

        def stop_auto_approve_all(self):
            self.phase("auto-approve")

    monkeypatch.setattr(
        gate_harness_module.os,
        "killpg",
        lambda process_group, signal_number: (_ for _ in ()).throw(ProcessLookupError(process_group)),
    )

    with pytest.raises(RuntimeError, match=f"injected {failure_phase} failure"):
        stop_fixture_app_runtime(FixtureApp(), label="fixture phase continuation")

    assert calls == ["client-watcher", "jobd-operations", "background-demotion", "auto-approve", "local-service"]


def test_fixture_runtime_raises_structured_group_after_attempting_all_failed_phases():
    calls = []

    class FixtureApp:
        def fail(self, name):
            calls.append(name)
            raise RuntimeError(f"injected {name} failure")

        def stop_client_event_watcher(self):
            self.fail("client-watcher")

        def stop_jobd_operation_service(self):
            self.fail("jobd-operations")

        def demote_background_owner(self):
            self.fail("background-demotion")

        def stop_auto_approve_all(self):
            self.fail("auto-approve")

    with pytest.raises(BaseExceptionGroup) as failure:
        stop_fixture_app_runtime(FixtureApp(), label="fixture grouped failures")

    assert calls == ["client-watcher", "jobd-operations", "background-demotion", "auto-approve"]
    assert [str(error) for error in failure.value.exceptions] == [
        "injected client-watcher failure",
        "injected jobd-operations failure",
        "injected background-demotion failure",
        "injected auto-approve failure",
    ]


def test_fixture_runtime_attempts_each_captured_service_after_one_process_stop_fails(monkeypatch):
    calls = []

    class FixtureProcess:
        def __init__(self, pid, failure=""):
            self.pid = pid
            self.failure = failure

        def poll(self):
            return 0

        def wait(self, timeout):
            calls.append(f"wait-{self.pid}")
            if self.failure:
                raise RuntimeError(self.failure)
            return 0

    class FixtureRegistry:
        def __init__(self, process):
            self.process = process
            self.spawn_ownership = _fixture_spawn_ownership(process.pid)

        def _reap_exited_child(self, process):
            assert process is self.process
            calls.append(f"reap-{process.pid}")

    class FixtureApp:
        def __init__(self):
            self.approval_client = SimpleNamespace(
                registry=FixtureRegistry(FixtureProcess(43215, "injected approvald stop failure"))
            )
            self.job_client = SimpleNamespace(registry=FixtureRegistry(FixtureProcess(43216)))

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(
        gate_harness_module.os,
        "killpg",
        lambda process_group, signal_number: (_ for _ in ()).throw(ProcessLookupError(process_group)),
    )

    with pytest.raises(RuntimeError, match="injected approvald stop failure"):
        stop_fixture_app_runtime(FixtureApp(), label="fixture service continuation")

    assert calls == ["wait-43215", "wait-43216", "reap-43216"]


def test_fixture_refuses_recycled_foreign_service_group_without_signalling(monkeypatch):
    signals = []

    class FixtureProcess:
        pid = 43217

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()
            self.spawn_ownership = SimpleNamespace(
                leader_pid=43217,
                process_group=43217,
                session_id=43217,
                generation_marker=f"{43217:032x}",
                member_identities=((43218, "proc:111"),),
            )

        def _reap_exited_child(self, process):
            raise AssertionError("foreign group must not be reaped as fixture-owned")

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=FixtureRegistry())

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(
        gate_harness_module,
        "bounded_process_table",
        lambda: {43218: SimpleNamespace(
            pgid=43217,
            session_id=43217,
            start_time=222,
            start_identity="proc:222",
            command="python fixture-local-service",
            spawn_generation=f"{43217:032x}",
        )},
        raising=False,
    )

    def record_signal(process_group, signal_number):
        if signal_number != 0:
            signals.append((process_group, signal_number))

    monkeypatch.setattr(gate_harness_module.os, "killpg", record_signal)

    with pytest.raises(AssertionError, match="ownership"):
        stop_fixture_app_runtime(FixtureApp(), label="fixture recycled group")

    assert signals == []


def test_fixture_real_registry_refuses_foreign_generation_group_without_signalling(tmp_path, monkeypatch):
    generation = "a" * 32
    foreign_generation = "b" * 32
    signals = []
    registry = local_service_registry.LocalServiceRegistry(
        tmp_path,
        local_service_registry.LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )

    class FixtureProcess:
        pid = 43218

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    process = FixtureProcess()
    registry.process = process
    registry.spawn_ownership = local_service_registry.SpawnProcessOwnership(
        leader_pid=process.pid,
        process_group=process.pid,
        session_id=process.pid,
        generation_marker=generation,
        member_identities=((process.pid, "proc:44218"),),
    )

    foreign_table = {
        43219: local_service_registry.ProcessTableEntry(
            ppid=1,
            pgid=process.pid,
            cpu_seconds=0.0,
            command="python foreign-service",
            start_time=44219,
            session_id=process.pid,
            start_identity="proc:44219",
            spawn_generation=foreign_generation,
        ),
    }
    monkeypatch.setattr(local_service_registry, "bounded_process_table", lambda: foreign_table)
    monkeypatch.setattr(local_service_registry, "process_spawn_generation", lambda _pid: foreign_generation)
    monkeypatch.setattr(
        gate_harness_module.os,
        "killpg",
        lambda process_group, signal_number: signals.append((process_group, signal_number)),
    )

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=registry)

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    # A live occupant carrying a readable, different generation is proof the
    # group is not this fixture's. That stays a hard refusal; only the separate
    # case of an occupant that exited mid-proof was reclassified.
    with pytest.raises(AssertionError, match=gate_harness_module.LOCAL_SERVICE_OWNERSHIP_DISPROVEN_CODE):
        stop_fixture_app_runtime(FixtureApp(), label="fixture foreign-generation group")

    assert signals == []


def test_fixture_treats_zombie_only_process_group_as_settled(monkeypatch):
    ownership = _fixture_spawn_ownership(43219)

    monkeypatch.setattr(gate_harness_module, "bounded_process_table", lambda: {})
    monkeypatch.setattr(gate_harness_module.os, "killpg", lambda _process_group, _signal_number: None)

    assert not gate_harness_module.fixture_owned_process_group_exists(
        ownership,
        label="fixture zombie-only group",
    )


def test_fixture_refuses_service_process_without_spawn_ownership(monkeypatch):
    signals = []

    class FixtureProcess:
        pid = 43220

        def poll(self):
            return None

    class FixtureRegistry:
        def __init__(self):
            self.process = FixtureProcess()

    class FixtureApp:
        def __init__(self):
            self.job_client = SimpleNamespace(registry=FixtureRegistry())

        def stop_client_event_watcher(self):
            pass

        def stop_jobd_operation_service(self):
            pass

        def demote_background_owner(self):
            pass

        def stop_auto_approve_all(self):
            pass

    monkeypatch.setattr(
        gate_harness_module.os,
        "killpg",
        lambda process_group, signal_number: signals.append((process_group, signal_number)),
    )

    with pytest.raises(AssertionError, match="no durable spawn ownership"):
        stop_fixture_app_runtime(FixtureApp(), label="fixture missing ownership")

    assert signals == []


@pytest.mark.parametrize("failure_phase", ("seal", "shutdown", "quiescence", "app", "close", "join"))
def test_fixture_http_stop_attempts_every_later_boundary_after_one_phase_fails(monkeypatch, failure_phase):
    calls = []

    def phase(name):
        calls.append(name)
        if name == failure_phase:
            raise RuntimeError(f"injected {name} failure")

    class Server:
        def shutdown(self):
            phase("shutdown")

        def server_close(self):
            phase("close")

    class Thread:
        def join(self, timeout):
            phase("join")

        def is_alive(self):
            return False

    monkeypatch.setattr(gate_harness_module, "seal_fixture_http_requests", lambda _server: phase("seal"))
    monkeypatch.setattr(gate_harness_module, "wait_for_fixture_http_quiescence", lambda _server: phase("quiescence"))
    monkeypatch.setattr(gate_harness_module, "stop_fixture_app_runtime", lambda _app, *, label: phase("app"))

    with pytest.raises(RuntimeError, match=f"injected {failure_phase} failure"):
        gate_harness_module.stop_fixture_http_app(object(), Server(), Thread(), label="fixture HTTP phases")

    assert calls == ["seal", "shutdown", "quiescence", "app", "close", "join"]


def test_fixture_cleanup_phases_raise_ordered_group_after_multiple_failures():
    calls = []

    def fail(name):
        calls.append(name)
        raise RuntimeError(f"injected {name} failure")

    with pytest.raises(BaseExceptionGroup) as failure:
        gate_harness_module.run_fixture_cleanup_phases(
            "fixture grouped outer cleanup",
            (
                ("first", lambda: fail("first")),
                ("second", lambda: fail("second")),
            ),
        )

    assert calls == ["first", "second"]
    assert [str(error) for error in failure.value.exceptions] == [
        "injected first failure",
        "injected second failure",
    ]


def test_runtime_root_removal_begins_only_after_registry_reaper_settlement(tmp_path, monkeypatch):
    calls = []

    class Registry(local_service_registry.LocalServiceRegistry):
        @property
        def service_dir(self):
            return tmp_path / "services"

        def seal_starts(self):
            calls.append("seal")

        def settle_reaper_threads(self):
            calls.append("settle")

    registry = object.__new__(Registry)
    ledger = gate_harness_module.FixtureLocalServiceLedger()
    ledger.record_registry(registry)
    monkeypatch.setattr(
        gate_harness_module,
        "capture_fixture_local_service_processes",
        lambda registries: calls.append("capture") or (),
    )
    monkeypatch.setattr(
        gate_harness_module,
        "stop_fixture_local_service_processes",
        lambda owners, *, label: calls.append("stop"),
    )
    monkeypatch.setattr(
        gate_harness_module,
        "retire_local_service_daemons_beneath",
        lambda root, *, label: calls.append("retire-unowned") or (),
    )
    monkeypatch.setattr(
        gate_harness_module,
        "assert_no_surviving_local_service_daemons",
        lambda root, *, label: calls.append("prove-zero"),
    )

    gate_harness_module.retire_fixture_local_services(ledger, tmp_path, label="fixture root")
    calls.append("remove-root")

    assert calls == ["seal", "capture", "stop", "retire-unowned", "prove-zero", "settle", "remove-root"]


def test_registry_reaper_settlement_failure_precedes_runtime_root_removal(tmp_path, monkeypatch):
    removed = []

    class Registry(local_service_registry.LocalServiceRegistry):
        @property
        def service_dir(self):
            return tmp_path / "services"

        def seal_starts(self):
            pass

        def settle_reaper_threads(self):
            raise RuntimeError("injected reaper settlement failure")

    ledger = gate_harness_module.FixtureLocalServiceLedger()
    ledger.record_registry(object.__new__(Registry))
    monkeypatch.setattr(gate_harness_module, "capture_fixture_local_service_processes", lambda _registries: ())
    monkeypatch.setattr(gate_harness_module, "stop_fixture_local_service_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate_harness_module, "retire_local_service_daemons_beneath", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(gate_harness_module, "assert_no_surviving_local_service_daemons", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(gate_harness_module.shutil, "rmtree", lambda _root: removed.append("removed"))

    with pytest.raises(RuntimeError, match="injected reaper settlement failure"):
        gate_harness_module.remove_fixture_runtime_root(ledger, tmp_path, label="fixture root")

    assert removed == []


@pytest.mark.parametrize("failure_phase", ("app", "tmux", "paths"))
def test_isolated_browser_app_stop_attempts_every_later_owner_after_one_phase_fails(monkeypatch, failure_phase):
    calls = []

    def phase(name):
        calls.append(name)
        if name == failure_phase:
            raise RuntimeError(f"injected {name} failure")

    monkeypatch.setattr(browser_layout, "stop_fixture_app_runtime", lambda _app, *, label: phase("app"))
    monkeypatch.setattr(browser_layout, "stop_isolated_tmux_runtime", lambda _tmux: phase("tmux"))
    monkeypatch.setattr(browser_layout, "cleanup_isolated_browser_runtime_paths", lambda _paths: phase("paths"))
    runtime = SimpleNamespace(app=object(), tmux=object(), paths=object())

    with pytest.raises(RuntimeError, match=f"injected {failure_phase} failure"):
        browser_layout.stop_isolated_browser_app(runtime)

    assert calls == ["app", "tmux", "paths"]


def test_live_runtime_logs_fixture_has_an_epoch_without_masking_explicit_payloads():
    fixture = _live_runtime_boot_fixture_html()

    assert "epoch: 'fixture-server-log-epoch'" in fixture
    assert "jsonResponse(window.__fixtureServerLogsPayload)" in fixture
    assert "window.__fixtureServerLogsPayload ||" not in fixture


def test_browser_boot_scenario_facade_preserves_selected_fixture_bytes():
    kwargs = {
        "settings": {"appearance": {"theme": "light"}},
        "sessions": ["7", "8"],
        "auto_approve_payload": {"sessions": {"7": {"enabled": True}}},
        "access_role": "readonly",
        "wrap_app_root": True,
        "grid_width": 812,
        "grid_height": 477,
    }
    scenario = browser_layout.BrowserBootScenario(
        settings=kwargs["settings"],
        sessions=tuple(kwargs["sessions"]),
        auto_approve_payload=kwargs["auto_approve_payload"],
        access_role=kwargs["access_role"],
        wrap_app_root=kwargs["wrap_app_root"],
        grid_width=kwargs["grid_width"],
        grid_height=kwargs["grid_height"],
    )

    assert _live_runtime_boot_fixture_html(**kwargs) == browser_layout.render_browser_boot_scenario(scenario)


def test_browser_boot_route_registry_has_one_handler_and_matches_gate_contract():
    fixture = browser_layout.render_browser_boot_scenario(
        browser_layout.BROWSER_BOOT_PRESETS["default"]
    )

    assert len(browser_layout.BROWSER_BOOT_ROUTES) == 33
    assert len({route.path for route in browser_layout.BROWSER_BOOT_ROUTES}) == 33
    for route in browser_layout.BROWSER_BOOT_ROUTES:
        assert fixture.count(f"url.pathname === '{route.path}'") == 1
        assert json.dumps(route.path) in fixture
        for method in route.methods:
            assert browser_layout.route_for_request(method, route.path) is not None
    assert "fixture method not allowed" in fixture


def test_browser_boot_route_validation_rejects_missing_and_duplicate_handlers():
    routes = browser_layout.BROWSER_BOOT_ROUTES
    complete = "\n".join(f"url.pathname === '{route.path}'" for route in routes)

    browser_layout.validate_browser_boot_routes(complete)
    with pytest.raises(AssertionError, match="exactly one fake handler"):
        browser_layout.validate_browser_boot_routes(complete.replace(
            f"url.pathname === '{routes[0].path}'",
            "",
        ))
    with pytest.raises(AssertionError, match="exactly one fake handler"):
        browser_layout.validate_browser_boot_routes(
            complete + f"\nurl.pathname === '{routes[0].path}'"
        )


def test_browser_bootstrap_represents_or_names_every_production_key():
    bootstrap = browser_layout.build_browser_bootstrap(
        browser_layout.BROWSER_BOOT_PRESETS["default"]
    )

    assert browser_layout.browser_boot_production_contract_errors(bootstrap) == ()
    assert set(browser_layout.BROWSER_BOOT_PRESETS) == {"default", "readonly"}


def test_browser_boot_scenario_is_deeply_immutable_at_its_mapping_boundaries():
    settings = {"appearance": {"theme": "dark"}}
    scenario = browser_layout.BrowserBootScenario(settings=settings)

    settings["other"] = True
    assert "other" not in scenario.settings
    with pytest.raises(TypeError):
        scenario.settings["other"] = True


def _leak_one_local_service_daemon(root, service):
    """Start one real daemon under ``root`` and leave it running, as a leak would."""

    socket_path = root / "yolomux-runtime" / "services" / f"{service.rsplit('.', 1)[-1]}.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            service,
            "--serve",
            "--socket",
            str(socket_path),
            "--idle-seconds",
            "60",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _reap_leaked_daemon(process):
    """Remove the probe daemon on every path, including the failing one."""

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
    process.wait(timeout=10)


@pytest.mark.socket
def test_surviving_local_service_daemon_fails_its_own_fixture_and_names_the_owner(tmp_path):
    """A fixture that leaks a daemon must fail itself, not the next test that needs its limit."""

    root = tmp_path / "yag-leak"
    root.mkdir()
    process = _leak_one_local_service_daemon(root, "yolomux_lib.watchd")
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not local_service_daemons_beneath(root):
            time.sleep(0.05)
        survivors = local_service_daemons_beneath(root)
        assert [daemon.service for daemon in survivors] == ["yolomux_lib.watchd"], survivors
        assert survivors[0].pid == process.pid, survivors

        with pytest.raises(AssertionError) as failure:
            assert_no_surviving_local_service_daemons(root, label="negative-control")

        reason = json.loads(str(failure.value))
        assert reason["error_code"] == LOCAL_SERVICE_DAEMON_SURVIVED_CODE, reason
        assert reason["root"] == str(root), reason
        assert [entry["service"] for entry in reason["surviving"]] == ["yolomux_lib.watchd"], reason
        assert reason["surviving"][0]["pid"] == process.pid, reason
        assert reason["surviving"][0]["socket_path"].startswith(str(root)), reason
        # The ledger names the limit that refuses the watcher, not only the fd table.
        ledger = reason["ledger"]
        assert ledger["inotify_max_user_instances"] >= 1, ledger
        assert ledger["inotify_instances_user"] >= 0, ledger
        assert ledger["rlimit_nofile_soft"] >= 1, ledger

        # The assertion itself must not kill: reclaiming is a separate, named
        # teardown phase, so the guard stays a pure observation of the state.
        assert local_service_daemons_beneath(root), "assertion must not retire the daemon"

        # The reclaim phase returns the instance so one leak cannot cascade.
        retired = retire_local_service_daemons_beneath(root, label="negative-control")
        assert [daemon.pid for daemon in retired] == [process.pid], retired
        process.wait(timeout=10)
        assert local_service_daemons_beneath(root) == ()
        assert assert_no_surviving_local_service_daemons(root, label="negative-control") is None
    finally:
        _reap_leaked_daemon(process)


@pytest.mark.socket
def test_retired_local_service_root_passes_the_surviving_daemon_invariant(tmp_path):
    """The same guard stays green for a root whose daemons really did retire."""

    root = tmp_path / "yag-clean"
    (root / "yolomux-runtime" / "services").mkdir(parents=True)
    process = _leak_one_local_service_daemon(root, "yolomux_lib.watchd")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not local_service_daemons_beneath(root):
        time.sleep(0.05)
    assert local_service_daemons_beneath(root), "probe daemon never became visible"
    _reap_leaked_daemon(process)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and local_service_daemons_beneath(root):
        time.sleep(0.05)

    assert local_service_daemons_beneath(root) == ()
    assert assert_no_surviving_local_service_daemons(root, label="negative-control") is None


def test_resource_ledger_measures_the_uid_wide_inotify_limit_not_only_the_fd_table(tmp_path):
    """The ledger records the limit that actually refuses a watcher under load."""

    snapshot = capture_resource_ledger(tmp_path, phase="before")

    assert snapshot.phase == "before"
    assert snapshot.worker_pid == os.getpid()
    assert snapshot.fd_count >= 1
    assert snapshot.rlimit_nofile_soft >= 1
    assert snapshot.inotify_max_user_instances >= 1
    assert snapshot.inotify_max_user_watches >= 1
    # One snapshot must be internally consistent. The uid-wide total is shared
    # with every other process this user runs, so it genuinely changes between
    # two censuses and must never be asserted across separate measurements.
    assert snapshot.inotify_instances_user == sum(snapshot.inotify_instances_by_pid.values())
    assert snapshot.inotify_instances_self == snapshot.inotify_instances_by_pid.get(os.getpid(), 0)
    census_total, census_by_pid = inotify_instance_census()
    assert census_total == sum(census_by_pid.values())
    # An empty root owns no daemons, so the ledger must not invent one.
    assert snapshot.local_service_daemons == ()
    assert snapshot.as_reason()["inotify_max_user_instances"] == snapshot.inotify_max_user_instances


def test_worker_inotify_baseline_guard_fires_on_a_real_leaked_instance():
    """Negative control: a genuinely retained inotify instance must fail its fixture."""

    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    baseline = capture_fixture_self_baseline()
    descriptor = libc.inotify_init1(0o4000)
    assert descriptor >= 0, os.strerror(ctypes.get_errno())
    try:
        with pytest.raises(AssertionError) as failure:
            assert_fixture_inotify_returned_to_baseline(baseline, label="negative-control")
        reason = json.loads(str(failure.value))
        assert reason["error_code"] == FIXTURE_INOTIFY_NOT_RETURNED_CODE, reason
        assert reason["leaked_instances"] == 1, reason
        assert reason["after"]["inotify_instances"] == baseline.inotify_instances + 1, reason
    finally:
        os.close(descriptor)

    # And it stays green once the instance is returned.
    assert assert_fixture_inotify_returned_to_baseline(baseline, label="negative-control") is None


def _vanished_group_snapshot(pid, generation):
    """A process-table snapshot that still lists a pid which has already exited."""

    return {
        pid: local_service_registry.ProcessTableEntry(
            ppid=1,
            pgid=pid,
            cpu_seconds=0.0,
            command="python vanished-service",
            start_time=44219,
            session_id=pid,
            start_identity="",
            spawn_generation="",
        ),
    }


def test_group_whose_occupants_all_exited_mid_proof_is_retired_not_unprovable(tmp_path, monkeypatch):
    """The exact gate failure: a daemon exiting between the snapshot and the live read.

    ``group_exists`` is derived from one ``ps`` snapshot while the spawn
    generation is read from ``/proc/<pid>/environ`` at a later instant.  A daemon
    that exits in between leaves the group listed with no occupant to classify,
    which previously raised "ownership could not be proven" and failed an
    otherwise clean test at teardown.
    """

    generation = "c" * 32
    signals = []
    registry = local_service_registry.LocalServiceRegistry(
        tmp_path,
        local_service_registry.LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )
    vanished_pid = 43220
    registry.spawn_ownership = local_service_registry.SpawnProcessOwnership(
        leader_pid=vanished_pid,
        process_group=vanished_pid,
        session_id=vanished_pid,
        generation_marker=generation,
        member_identities=((vanished_pid, "proc:44220"),),
    )
    monkeypatch.setattr(
        local_service_registry,
        "bounded_process_table",
        lambda: _vanished_group_snapshot(vanished_pid, generation),
    )
    # The process is gone, so both live reads fail.
    monkeypatch.setattr(local_service_registry, "process_spawn_generation", lambda _pid: None)
    monkeypatch.setattr(local_service_registry, "process_start_identity", lambda _pid: "")
    monkeypatch.setattr(
        gate_harness_module.os,
        "killpg",
        lambda process_group, signal_number: signals.append((process_group, signal_number)),
    )

    proof = registry.refresh_spawn_ownership_proof()

    # The stale snapshot still reports the group, and nothing was disproven.
    assert proof.group_exists is True
    assert proof.owned_member_identities == ()
    assert proof.disproven_occupants == ()

    owned = gate_harness_module.fixture_owned_process_group_exists(
        registry.spawn_ownership,
        label="fixture vanished group",
        proof=proof,
    )

    assert owned is False
    assert signals == []


def test_live_occupant_that_cannot_be_proven_still_refuses_to_signal(tmp_path, monkeypatch):
    """A still-present occupant is never signalled, and still fails closed."""

    generation = "d" * 32
    signals = []
    registry = local_service_registry.LocalServiceRegistry(
        tmp_path,
        local_service_registry.LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )
    live_pid = 43221
    registry.spawn_ownership = local_service_registry.SpawnProcessOwnership(
        leader_pid=live_pid,
        process_group=live_pid,
        session_id=live_pid,
        generation_marker=generation,
        member_identities=((live_pid, "proc:44221"),),
    )
    table = {
        43222: local_service_registry.ProcessTableEntry(
            ppid=1,
            pgid=live_pid,
            cpu_seconds=0.0,
            command="python unprovable-occupant",
            start_time=44222,
            session_id=live_pid,
            start_identity="proc:44222",
            spawn_generation="",
        ),
    }
    monkeypatch.setattr(local_service_registry, "bounded_process_table", lambda: table)
    # Generation unreadable, but the occupant is demonstrably still present.
    monkeypatch.setattr(local_service_registry, "process_spawn_generation", lambda _pid: None)
    monkeypatch.setattr(local_service_registry, "process_start_identity", lambda _pid: "proc:44222")
    monkeypatch.setattr(
        gate_harness_module.os,
        "killpg",
        lambda process_group, signal_number: signals.append((process_group, signal_number)),
    )

    proof = registry.refresh_spawn_ownership_proof()

    assert proof.owned_member_identities == ()
    assert proof.disproven_occupants == ((43222, "proc:44222"),)

    with pytest.raises(AssertionError) as failure:
        gate_harness_module.fixture_owned_process_group_exists(
            registry.spawn_ownership,
            label="fixture unprovable occupant",
            proof=proof,
        )

    reason = json.loads(str(failure.value))
    assert reason["error_code"] == gate_harness_module.LOCAL_SERVICE_OWNERSHIP_DISPROVEN_CODE
    assert reason["process_group"] == live_pid
    assert reason["disproven_occupants"] == [[43222, "proc:44222"]]
    assert signals == []

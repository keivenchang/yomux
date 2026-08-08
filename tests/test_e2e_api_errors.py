# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Authenticated normal-session HTTP error gate."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
import json
from pathlib import Path
import subprocess
from threading import Event
from threading import Lock
from threading import Thread
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest

from tests.e2e_browser_harness import E2EBrowserHarness
from tests.gate_harness import retire_expected_fixture_http_failures
from tests.gate_harness import wait_for_fixture_api_quiescence
from tests.browser_helpers.browser_console import retire_only_nonfailure_js_debug_events
from yolomux_lib import statusd
from yolomux_lib.http_routes import ALL_ROUTES
from yolomux_lib.server import Handler
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.statusd_client import StatusClient


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]


# Any route added here needs a user-visible reason.
NORMAL_JOURNEY_NON_2XX_ALLOWLIST: dict[tuple[str, int], str] = {
    ("GET /api/stats-snapshot", HTTPStatus.ACCEPTED): (
        "The materializer accepted this first exact view and the browser retries using retry_after_seconds."
    ),
}


@dataclass(frozen=True)
class NormalSessionLocalServiceRoute:
    """One finite JSON route whose normal-session response depends on a local service."""

    method: str
    path: str
    handler_name: str


# Keep this inventory at the router boundary.  The browser journey below proves the
# rendered paths; this list exercises every finite normal-session JSON route whose
# handler reaches statsd, jobd, or statusd.  SSE routes are excluded because their
# response is an unbounded event stream rather than one typed response body.
NORMAL_SESSION_LOCAL_SERVICE_ROUTES = (
    NormalSessionLocalServiceRoute("GET", "/api/stats-capabilities", "get_stats_capabilities"),
    NormalSessionLocalServiceRoute("GET", "/api/stats-delta?range_seconds=3600&resolution_seconds=60&client_id=e2e-api-errors&after_cache_generation=0&after_revision=0", "get_stats_delta"),
    NormalSessionLocalServiceRoute("GET", "/api/stats-snapshot?range_seconds=3600&resolution=60&client_id=e2e-api-errors", "get_stats_snapshot"),
    NormalSessionLocalServiceRoute("GET", "/api/session-metadata", "get_session_metadata"),
    NormalSessionLocalServiceRoute("GET", "/api/transcripts", "get_transcripts"),
    NormalSessionLocalServiceRoute("GET", "/api/system-status", "get_system_status"),
    NormalSessionLocalServiceRoute("GET", "/api/auto-approve?session={session}", "get_auto_approve"),
    NormalSessionLocalServiceRoute("GET", "/api/context?session={session}&messages=40", "get_context"),
    NormalSessionLocalServiceRoute("GET", "/api/context-items?session={session}&messages=40", "get_context_items"),
    NormalSessionLocalServiceRoute("GET", "/api/search?q=e2e&session={session}&limit=10", "get_search"),
    NormalSessionLocalServiceRoute("GET", "/api/run-history?session={session}", "get_run_history"),
    NormalSessionLocalServiceRoute("GET", "/api/activity?hours=24&visible=1", "get_activity"),
    NormalSessionLocalServiceRoute("GET", "/api/session-files?session={session}&hours=24", "get_session_files"),
    NormalSessionLocalServiceRoute("GET", "/api/session-files-batch?session={session}&hours=24", "get_session_files_batch"),
    NormalSessionLocalServiceRoute("GET", "/api/summary?session={session}", "get_summary"),
)


def normal_session_local_service_routes() -> tuple[NormalSessionLocalServiceRoute, ...]:
    """Resolve the declared finite local-service inventory against the real router."""
    router_handlers = {
        (route.method, route.path, route.handler.__name__)
        for route in ALL_ROUTES
        if route.normal_session_local_service
    }
    declared = {(route.method, route.path.split("?", 1)[0], route.handler_name) for route in NORMAL_SESSION_LOCAL_SERVICE_ROUTES}
    assert declared == router_handlers, {
        "missing_route_requests": sorted(router_handlers - declared),
        "stale_route_requests": sorted(declared - router_handlers),
    }
    return NORMAL_SESSION_LOCAL_SERVICE_ROUTES


def fetch_normal_session_local_service_routes(
    harness: E2EBrowserHarness,
) -> list[dict[str, Any]]:
    """Fetch every finite local-service route through the authenticated browser cookie."""
    session = str(harness.runtime.tmux.sessions[0])
    requests = [
        {"method": route.method, "path": route.path.format(session=session), "handler": route.handler_name}
        for route in normal_session_local_service_routes()
    ]
    return harness.driver.execute_async_script(
        """
        const requests = arguments[0];
        const done = arguments[arguments.length - 1];
        Promise.all(requests.map(async request => {
          try {
            const response = await fetch(request.path, {method: request.method, credentials: 'same-origin'});
            const text = await response.text();
            let body = null;
            try { body = JSON.parse(text); } catch (_) {}
            return {...request, status: response.status, contentType: response.headers.get('content-type') || '', body, text: text.slice(0, 512)};
          } catch (error) {
            return {...request, status: 0, contentType: '', body: null, text: String(error?.stack || error)};
          }
        })).then(done, error => done([{status: 0, text: String(error?.stack || error)}]));
        """,
        requests,
    )


def assert_typed_non_5xx_route_responses(responses: list[dict[str, Any]]) -> None:
    """Reject transport, 5xx, and success-shaped/untyped route failures."""
    failures = [
        response for response in responses
        if int(response.get("status") or 0) >= 500
        or int(response.get("status") or 0) == 0
        or not isinstance(response.get("body"), dict)
        or (int(response.get("status") or 0) >= 400 and not response["body"].get("error"))
    ]
    assert not failures, {"untyped_or_5xx_local_service_routes": failures}


@contextmanager
def capture_non_2xx_responses(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Capture actual server responses, including browser-initiated requests outside fetch()."""
    deliveries: list[dict[str, Any]] = []
    lock = Lock()
    original_record = Handler.record_http_response_bytes
    original_json = Handler.write_json
    original_text = Handler.write_text

    def record(self, status, body_bytes, content_type="", performance_details=None):
        original_record(self, status, body_bytes, content_type, performance_details)
        if 200 <= int(status) < 300:
            return
        with lock:
            deliveries.append({
                "route": self.http_endpoint_metric_key(),
                "status": int(status),
                "body": None,
                "content_type": content_type,
                "body_bytes": int(body_bytes),
            })

    def attach_body(self, status, body):
        if 200 <= int(status) < 300:
            return
        route = self.http_endpoint_metric_key()
        with lock:
            for delivery in reversed(deliveries):
                if delivery["route"] == route and delivery["status"] == int(status) and delivery["body"] is None:
                    delivery["body"] = body
                    return
            deliveries.append({"route": route, "status": int(status), "body": body, "content_type": "", "body_bytes": None})

    def write_json(self, value, status=HTTPStatus.OK):
        original_json(self, value, status=status)
        attach_body(self, status, value)

    def write_text(self, body, status=HTTPStatus.OK):
        original_text(self, body, status=status)
        attach_body(self, status, body)

    monkeypatch.setattr(Handler, "record_http_response_bytes", record)
    monkeypatch.setattr(Handler, "write_json", write_json)
    monkeypatch.setattr(Handler, "write_text", write_text)
    yield deliveries


def make_finder_repo(harness: E2EBrowserHarness) -> tuple[Path, Path, Path]:
    repo = harness.runtime.paths.home_dir / "dev" / "normal-journey"
    child = repo / "child"
    child.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", "-b", "main", str(repo)), check=True, capture_output=True, text=True, timeout=10)
    return repo.parent, repo, child


def switch_stats_range(harness: E2EBrowserHarness) -> dict[str, Any]:
    """Dispatch the real YO!stats range control and wait for its next selection."""
    session = str(harness.runtime.tmux.sessions[0])
    harness.switch_session("__debug__")
    return harness.driver.execute_async_script(
        """
        const session = arguments[0];
        const done = arguments[arguments.length - 1];
        const slider = document.querySelector('[data-js-debug-range-slider]');
        if (!slider) { done({error: 'YO!stats range slider is absent'}); return; }
        const before = String(slider.value);
        const next = Number(slider.max) > Number(before) ? Number(before) + 1 : Math.max(0, Number(before) - 1);
        slider.value = String(next);
        slider.dispatchEvent(new Event('input', {bubbles: true}));
        slider.dispatchEvent(new Event('change', {bubbles: true}));
        window.__yolomuxTestWaitFor(
          () => String(document.querySelector('[data-js-debug-range-slider]')?.value || '') === String(next),
          {timeoutMs: 12000, description: 'YO!stats range switch'},
        ).then(() => done({before, next: String(next), session}), error => done({error: String(error?.stack || error)}));
        """,
        session,
    )


def test_authenticated_normal_session_has_no_unexpected_non_2xx_responses(
    authenticated_e2e_browser: E2EBrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load, Finder, Differ, YO!stats, and terminal attach stay free of 4xx/5xx responses."""
    dev_root, repo, child = make_finder_repo(authenticated_e2e_browser)
    with capture_non_2xx_responses(monkeypatch) as deliveries:
        authenticated_e2e_browser.load(tabs=("files", "diff", "__debug__", str(authenticated_e2e_browser.runtime.tmux.sessions[0])))
        authenticated_e2e_browser.expand(dev_root, child_path=repo)
        authenticated_e2e_browser.re_expand(repo, child_path=child)
        differ = authenticated_e2e_browser.open_differ()
        assert authenticated_e2e_browser.assert_reaches_terminal_state(differ, bound=12)["terminal"] is True
        switched = switch_stats_range(authenticated_e2e_browser)
        assert not switched.get("error"), switched
        terminal = authenticated_e2e_browser.switch_session(str(authenticated_e2e_browser.runtime.tmux.sessions[0]))
        assert authenticated_e2e_browser.assert_reaches_terminal_state(terminal, bound=12)["terminal"] is True

    assert all(reason.strip() for reason in NORMAL_JOURNEY_NON_2XX_ALLOWLIST.values())
    unexpected = [
        delivery for delivery in deliveries
        if (delivery["route"], delivery["status"]) not in NORMAL_JOURNEY_NON_2XX_ALLOWLIST
    ]
    assert not unexpected, {"unexpected": unexpected, "allowlist": NORMAL_JOURNEY_NON_2XX_ALLOWLIST}


def test_authenticated_normal_session_local_service_routes_are_typed_and_not_5xx(
    authenticated_e2e_browser: E2EBrowserHarness,
) -> None:
    """Every finite router-declared local-service route gives the browser a typed result."""
    authenticated_e2e_browser.load(tabs=("files", "__debug__", str(authenticated_e2e_browser.runtime.tmux.sessions[0])))
    wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver)
    responses = fetch_normal_session_local_service_routes(authenticated_e2e_browser)
    assert_typed_non_5xx_route_responses(responses)
    boundary_sequence = int(authenticated_e2e_browser.runtime.server_log_boundary["sequence"])
    deliberate = [
        entry for entry in SERVER_LOGS.payload()["logs"]
        if int(entry["id"]) > boundary_sequence
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    failures = [response for response in responses if int(response.get("status") or 0) >= 400]
    expected = tuple({
        "method": response["method"],
        "path": urlsplit(response["path"]).path,
        "query": {key: values[-1] for key, values in parse_qs(urlsplit(response["path"]).query).items() if values},
        "status": int(response["status"]),
        "code": response["body"]["error"]["code"],
        "request_id": response["body"]["request"]["id"],
    } for response in failures)
    retired = retire_expected_fixture_http_failures(
        authenticated_e2e_browser.driver,
        authenticated_e2e_browser.runtime,
        expected,
    )
    assert len(retired["browser"]) == len(retired["server"]) == len(expected)


def test_statusd_refreshing_snapshot_is_not_a_browser_api_failure(
    authenticated_e2e_browser: E2EBrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A divergent live statusd build must leave the existing browser status intact."""
    build_started = Event()
    release_build = Event()
    active_session = str(authenticated_e2e_browser.runtime.tmux.sessions[0])
    blocked_sessions = ["blocked"]
    block_active_snapshot = False

    class ActivityController:
        def load_yoagent_session_summaries(self):
            return None

    class BlockingStatusApp:
        def __init__(self, sessions, **_kwargs):
            self.sessions = list(sessions)
            self.yoagent_controller = ActivityController()

        def build_auto_approve_status(self, *, timings, sync_workers):
            assert sync_workers is False
            if block_active_snapshot and self.sessions == blocked_sessions:
                build_started.set()
                assert release_build.wait(timeout=5)
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200

        def assemble_activity_summary_payload(self, **kwargs):
            return {
                "generated_at": "2026-08-04T00:00:00+00:00",
                "generated_ts": 1785801600.0,
                "session_order": list(self.sessions),
                "sessions": {},
                "session_info": {},
                "agents": [],
                "global": {},
                "capabilities": {},
                "errors": [],
                "locale": kwargs["locale"],
                "session_scope": kwargs["session_scope"],
                "session_file_hours": kwargs["hours"],
                "yoagent_summaries": {},
            }

    monkeypatch.setattr(statusd, "TmuxWebtermApp", BlockingStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    service_thread = Thread(target=service.run, daemon=True)
    service_thread.start()
    client = StatusClient(socket_path)
    original_status_client = authenticated_e2e_browser.runtime.app.status_client
    monkeypatch.setattr(authenticated_e2e_browser.runtime.app, "merge_shared_attention_acks", lambda: False)
    try:
        wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver)
        initial, _initial_body = client.snapshot([active_session], timeout=1.0)
        if initial.get("ok") is not True:
            assert initial == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}
            started = client.wait_generation(0, timeout=1.0)
            assert started["changed"] is True, started
            initial, _initial_body = client.snapshot([active_session], timeout=1.0)
        assert initial["ok"] is True, initial
        requested, _requested_body = client.snapshot(["other"], timeout=1.0)
        assert requested == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}
        changed = client.wait_generation(initial["generation"], timeout=1.0)
        assert changed["changed"] is True, changed
        other, _other_body = client.snapshot(["other"], timeout=1.0)
        assert other["ok"] is True, other
        authenticated_e2e_browser.runtime.app.status_client = client

        block_active_snapshot = True
        rebuilding = Thread(target=lambda: client.snapshot(blocked_sessions, timeout=2.0), daemon=True)
        rebuilding.start()
        assert build_started.wait(timeout=2), "statusd did not enter the fixture-owned blocked build"
        result = authenticated_e2e_browser.driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            loadAutoStatuses({force: true, render: false}).then(value => done({
              value,
              failures: jsDebugFailureEvents('error').filter(event => String(event.url || '').includes('/api/auto-approve')),
            }), error => done({error: String(error?.stack || error)}));
            """
        )
        assert not result.get("error"), result
        assert result["value"]["applied"] is False, result
        assert result["value"]["sessionsChanged"] is False, result
        assert active_session in result["value"]["previousActive"], result
        assert result["failures"] == [], result
        assert service.status()["snapshot_build_conflicts"] >= 1
    finally:
        release_build.set()
        if "rebuilding" in locals():
            rebuilding.join(timeout=3)
        wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver)
        authenticated_e2e_browser.runtime.app.status_client = original_status_client
        client.request({"action": "shutdown"})
        service_thread.join(timeout=2)
    assert service_thread.is_alive() is False


def test_statsd_deadline_snapshot_retries_24h_without_browser_api_failure(
    authenticated_e2e_browser: E2EBrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out statsd read becomes pending and the real 24-hour panel recovers."""
    client = authenticated_e2e_browser.runtime.app.stats_current_http.client
    original_snapshot = client.snapshot
    injected_ranges: list[int] = []

    def deadline_once(request):
        if request.range_seconds == 86400 and not injected_ranges:
            injected_ranges.append(request.range_seconds)
            return {
                "ok": False,
                "_transport_error": "timeout",
                "error": "timed out",
            }, b""
        return original_snapshot(request)

    monkeypatch.setattr(client, "snapshot", deadline_once)
    authenticated_e2e_browser.load(tabs=(
        "files",
        "__debug__",
        str(authenticated_e2e_browser.runtime.tmux.sessions[0]),
    ))
    authenticated_e2e_browser.switch_session("__debug__")
    retire_only_nonfailure_js_debug_events(authenticated_e2e_browser.driver)
    result = authenticated_e2e_browser.driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          document.querySelector('.js-debug-panel [data-js-debug-subtab="graph"]')?.click();
          await window.__yolomuxTestWaitFor(
            () => document.querySelector('.js-debug-panel [data-js-debug-range-slider]'),
            {timeoutMs: 12000, description: 'YO!stats 24-hour slider'},
          );
          const options = [...document.querySelectorAll('.js-debug-panel #js-debug-range-options option')];
          const index = options.findIndex(option => Number(option.dataset.jsDebugRange) === 86400);
          if (index < 0) throw new Error('24-hour range is absent');
          const slider = document.querySelector('.js-debug-panel [data-js-debug-range-slider]');
          slider.value = String(index);
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          slider.dispatchEvent(new Event('change', {bubbles: true}));
          await window.__yolomuxTestWaitFor(() => {
            const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
            const generation = controller?.generation?.();
            return generation?.range_seconds === 86400
              && generation?.resolution_seconds === 300
              && Array.isArray(generation?.buckets)
              && generation.buckets.length > 0
              && jsDebugHistoryReadiness?.phase === 'ready'
              && document.querySelectorAll('.js-debug-panel .js-debug-chart svg').length > 0;
          }, {timeoutMs: 20000, description: 'recovered 24-hour YO!stats panel'});
          const controller = jsDebugCurrentStatsClientState.client.controller();
          const generation = controller.generation();
          const failures = jsDebugFailureEvents('error');
          const rejections = jsDebugFailureEvents('rejection');
          return {
            rangeSeconds: generation.range_seconds,
            resolutionSeconds: generation.resolution_seconds,
            buckets: generation.buckets.length,
            charts: document.querySelectorAll('.js-debug-panel .js-debug-chart svg').length,
            failures,
            rejections,
          };
        })().then(done, error => done({error: String(error?.stack || error)}));
        """
    )

    assert not result.get("error"), result
    assert injected_ranges == [86400]
    assert result["rangeSeconds"] == 86400
    assert result["resolutionSeconds"] == 300
    assert result["buckets"] > 0 and result["charts"] > 0
    assert result["failures"] == [] and result["rejections"] == [], result


def test_normal_session_local_service_route_guard_fails_on_forced_route_exception(
    authenticated_e2e_browser: E2EBrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the route-complete guard is red when a normal local-service route raises."""
    assert authenticated_e2e_browser.runtime.server_log_boundary["sequence"] == 0
    assert authenticated_e2e_browser.runtime.server_log_boundary["logs"] == []
    authenticated_e2e_browser.load(tabs=("files", "__debug__", str(authenticated_e2e_browser.runtime.tmux.sessions[0])))
    wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver)

    def force_stats_capabilities_failure() -> dict[str, Any]:
        raise RuntimeError("forced stats-capabilities route failure")

    monkeypatch.setattr(
        authenticated_e2e_browser.runtime.app.stats_current_http,
        "capabilities",
        force_stats_capabilities_failure,
    )
    responses = fetch_normal_session_local_service_routes(authenticated_e2e_browser)
    with pytest.raises(AssertionError, match="untyped_or_5xx_local_service_routes"):
        assert_typed_non_5xx_route_responses(responses)
    forced = next(response for response in responses if response.get("handler") == "get_stats_capabilities")
    assert int(forced["status"]) == 0 or int(forced["status"]) >= 500, forced
    boundary_sequence = int(authenticated_e2e_browser.runtime.server_log_boundary["sequence"])
    deliberate = [
        entry for entry in SERVER_LOGS.payload()["logs"]
        if int(entry["id"]) > boundary_sequence
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    failures = [response for response in responses if int(response.get("status") or 0) >= 400]
    expected = tuple({
        "method": response["method"],
        "path": urlsplit(response["path"]).path,
        "query": {key: values[-1] for key, values in parse_qs(urlsplit(response["path"]).query).items() if values},
        "status": int(response["status"]),
        "code": response["body"]["error"]["code"],
        "request_id": response["body"]["request"]["id"],
    } for response in failures)
    retired = retire_expected_fixture_http_failures(
        authenticated_e2e_browser.driver,
        authenticated_e2e_browser.runtime,
        expected,
    )
    assert len(retired["browser"]) == len(retired["server"]) == len(expected)

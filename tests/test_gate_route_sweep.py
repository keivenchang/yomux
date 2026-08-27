# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Derived authenticated HTTP route sweep for the live server registry."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from dataclasses import replace
import hashlib
from http import HTTPStatus
from http.client import HTTPConnection
from http.client import RemoteDisconnected
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode
import uuid

import pytest

from tests.gate_harness import GateAuthCredentials
from tests.gate_harness import GateLiveServer
from tests.gate_harness import gate_auth_credentials  # noqa: F401
from tests.gate_harness import gate_authenticated_live_server  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import wait_for_fixture_http_quiescence
from tests.helpers.http_routes import login_cookie as _login_cookie
from tests.browser_helpers.browser_console import validate_server_log_ring_payload
from tests.browser_helpers.browser_console import validate_server_log_ring_transition
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from yolomux_lib import http_routes
from yolomux_lib import server as server_module
from yolomux_lib.server_logs import SERVER_LOGS


pytestmark = [pytest.mark.socket, pytest.mark.e2e]

REPORT_ENV = "YOLOMUX_ROUTE_SWEEP_OUTPUT"
EXCLUDED_ROUTES = {
    ("GET", "/api/session-files"): "Owned by yo7775; response-contract semantics are intentionally outside this sweep.",
    ("GET", "/api/activity-summary"): "The synchronous endpoint remains disabled until its asynchronous replacement exists.",
    # `/readyz` and `/livez` FAIL CLOSED when they cannot read the statsd process. That is the
    # behaviour they exist to provide, not a bug to route around. This sweep exercises the route
    # registry against a web server started WITHOUT a statsd daemon, so the handler resolves pid 0
    # and reports `FileNotFoundError: [Errno 2] ... '/proc/0/status'` with
    # `failures: ["process: not readable", "control: resource_state unavailable"]`. The 503 is a
    # correct answer about a genuinely absent subject, and a 200 from either endpoint in that state
    # would be the real defect - it would mean the health surface reported ready while blind.
    #
    # Do NOT "fix" this by relaxing the handlers, and do NOT give the fixture a live daemon: a
    # route-registry sweep that starts a real statsd is no longer a route-registry sweep. The
    # handlers are correct; only their exercisability under this fixture is out of scope.
    ("GET", "/readyz"): "Fails closed by design when the statsd process is unreadable. This sweep's fixture has no statsd, so 503 is the correct answer and a 200 would be the defect.",
    ("GET", "/livez"): "Same reason as /readyz. It answers from the cached prior readiness result, and with no statsd there has never been one, so it fails closed. 503 is correct under this fixture.",
}
TRACEBACK_MARKERS = ("Traceback (most recent call last):", "Exception in thread")
# The same bound the fixture teardown already holds accepted operations to in
# tests/gate_harness.py; the sweep asserts the identical contract, so it reuses the identical bound.
ROUTE_SWEEP_OPERATION_SETTLE_SECONDS = 3.0


@dataclass(frozen=True)
class RouteFixture:
    repo: Path
    text_file: Path
    html_file: Path
    commit: str
    session: str


@dataclass(frozen=True)
class RouteRequest:
    path: str
    body: bytes | None = None
    content_type: str = "application/json"
    stream: bool = False
    websocket: bool = False
    extra_headers: tuple[tuple[str, str], ...] = ()


def _registry_routes() -> tuple[http_routes.Route, ...]:
    """Read the exact registry consumed by server dispatch; never maintain a route copy."""

    routes = tuple(http_routes.ALL_ROUTES)
    assert routes
    assert len({(route.method, route.path) for route in routes}) == len(routes)
    assert server_module.route_for_request is http_routes.route_for_request
    return routes


def _json_body(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _route_request(route: http_routes.Route, fixture: RouteFixture, credentials: GateAuthCredentials) -> RouteRequest:
    """Build safe fixture-scoped inputs after deriving each route from the registry."""

    path = route.path.replace("*", "route-sweep-missing")
    handler = route.handler.__name__
    session = quote(fixture.session, safe="")
    repo = quote(str(fixture.repo), safe="")
    text_file = quote(str(fixture.text_file), safe="")
    html_file = quote(str(fixture.html_file), safe="")
    commit = quote(fixture.commit, safe="")
    query_by_handler = {
        "get_stats_delta": "range_seconds=300&resolution_seconds=1&client_id=route-sweep&after_cache_generation=0&after_revision=0",
        "get_stats_snapshot": "range_seconds=300&resolution=AUTO&client_id=route-sweep&since_generation=0",
        "get_stats_stream": "range_seconds=invalid&resolution_seconds=1&client_id=route-sweep&after_cache_generation=0&after_revision=0",
        "get_update_status": "dryrun=1",
        "get_dev_reload": "bundle_revision=route-sweep",
        "get_client_events": "channels=core&client_id=route-sweep",
        "get_preview_popout": f"path={text_file}",
        "get_pane_popout": "item=files",
        "get_agent_auth": "force=0",
        "get_auto_approve": f"session={session}",
        "get_events": f"session={session}&limit=5",
        "get_search": f"session={session}&q=route-sweep&limit=5",
        "get_run_history": f"session={session}",
        "get_activity": "hours=1&visible=1",
        "get_session_files_batch": f"session={session}&hours=1&from=HEAD&to=current",
        "get_summary": f"session={session}",
        "get_tmux_session_exists": f"session={session}",
        "get_chat_bootstrap": "browser_instance_id=route-sweep",
        "get_chat_page": "limit=5",
        "get_chat_delta": "after=0&limit=5",
        "get_chat_context": "message_id=route-sweep-missing&before=1&after=1",
        "get_chat_search": "query=route-sweep-missing&limit=5",
        "get_fs_list": f"path={repo}",
        "get_fs_search": f"root={repo}&query=fixture&limit=10",
        "get_fs_index_status": f"root={repo}",
        "get_fs_read": f"path={text_file}",
        "get_fs_info": f"path={text_file}",
        "get_fs_diff": f"path={text_file}&from=HEAD&to=current",
        "get_fs_git_history": f"path={repo}&limit=1",
        "get_fs_git_commit": f"path={repo}&commit={commit}&head={commit}",
        "get_fs_watch_diff": "full=1",
        "get_blame": f"path={text_file}",
        "get_fs_raw": f"path={text_file}",
        "get_fs_zip": f"path={repo}",
        "get_fs_count": f"path={repo}",
        "get_fs_html_preview": f"path={html_file}",
        "get_tmux": f"session={session}&lines=5",
        "get_tmux_signals": f"session={session}&force=0",
        "get_tmux_status": f"session={session}",
        "get_transcript": f"session={session}&lines=5",
        "get_context": f"session={session}&messages=5",
        "get_context_items": f"session={session}&messages=5",
        "get_context_stream": f"session={session}&messages=5",
        "get_summary_stream": "session=route-sweep-missing",
        "get_websocket": f"session={session}&client=route-sweep",
        "post_self_update": "dryrun=1",
        "post_ensure_session": f"session={session}",
        # Unknown agents intentionally fall back to Claude, so they are not a safe negative fixture:
        # the sweep would launch a real process. An unavailable explicit terminal refuses pre-spawn.
        "post_create_session": "agent=term&terminal=route-sweep-unavailable",
        "post_rename_session": "session=route-sweep-missing&new_name=route-sweep-renamed",
        "post_kill_session": "session=route-sweep-missing",
        "post_upload": f"session={session}",
        "post_auto_approve": "session=route-sweep-missing&enabled=0",
        "post_notify": "enabled=0",
        "post_tmux_next": "session=route-sweep-missing",
        "post_tmux_status": "session=route-sweep-missing",
        "post_tmux_window": "session=route-sweep-missing&window=0",
        "post_tmux_copy_selection": "session=route-sweep-missing",
    }
    query = query_by_handler.get(handler, "")
    if handler == "get_static_asset":
        path = "/static/brand.css"
    if query:
        path = f"{path}?{query}"

    body_by_handler: dict[str, bytes] = {
        "post_login": urlencode({
            "username": credentials.username,
            "password": credentials.password,
            "next": "/api/ping",
        }).encode("utf-8"),
        "post_stats_observations": _json_body({}),
        "post_attention_ack": _json_body({}),
        "post_settings": _json_body({"settings": {}}),
        "post_watch_roots": _json_body({"client_id": "route-sweep", "roots": [str(fixture.repo)]}),
        "post_drop_action": _json_body({}),
        "post_yoagent_chat": _json_body({}),
        "post_yoagent_chat_cancel": _json_body({}),
        "post_yoagent_preview_send": _json_body({}),
        "post_yoagent_execute_send": _json_body({}),
        "post_yoagent_intent": _json_body({}),
        "post_yoagent_jobs": _json_body({}),
        "post_yoagent_jobs_cancel_session": _json_body({"session": "route-sweep-missing"}),
        "post_yoagent_job_confirm": _json_body({}),
        "post_yoagent_job_cancel": _json_body({}),
        "post_yoagent_wait_clear": _json_body({}),
        "post_yoagent_skill_file_upsert": _json_body({}),
        "post_yoagent_skill_file_delete": _json_body({}),
        "post_yoagent_prewarm": _json_body({}),
        "post_chat_send": _json_body({}),
        "post_chat_yoagent": _json_body({}),
        "post_chat_typing": _json_body({}),
        "post_chat_read": _json_body({}),
        "post_event": _json_body({}),
        "post_fs_batch": _json_body([{"id": "route-sweep-read", "op": "read", "path": str(fixture.text_file)}]),
        "post_fs_write": _json_body({}),
        "post_fs_delete": _json_body({}),
        "post_fs_unindex": _json_body({}),
        "post_fs_rename": _json_body({}),
        "post_fs_mkdir": _json_body({}),
    }
    body = body_by_handler.get(handler)
    if body is None and route.method == "POST" and route.body_limit is not None:
        body = _json_body({})
    content_type = "application/x-www-form-urlencoded" if handler == "post_login" else "application/json"
    websocket = route.path.startswith("/ws")
    stream = handler in {"get_client_events", "get_context_stream", "get_summary_stream"}
    return RouteRequest(path, body, content_type, stream, websocket)


def _json_shape(value: Any) -> str:
    if isinstance(value, dict):
        fields = ",".join(f"{key}:{type(item).__name__}" for key, item in sorted(value.items()))
        return f"json-object{{{fields}}}"
    if isinstance(value, list):
        element_types = sorted({type(item).__name__ for item in value})
        return f"json-array[{len(value)}]<{','.join(element_types)}>"
    return f"json-{type(value).__name__}"


def _body_shape(content_type: str, body: bytes, *, stream: bool, websocket: bool) -> tuple[str, Any | None]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if websocket:
        return "websocket-handshake", None
    if media_type == "application/json":
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "invalid-json", None
        return _json_shape(parsed), parsed
    if stream or media_type == "text/event-stream":
        events = sorted({
            line.split(":", 1)[1].strip()
            for line in body.decode("utf-8", errors="replace").splitlines()
            if line.startswith("event:")
        })
        return f"sse-events[{','.join(events) or 'none-observed'}]", None
    if media_type in {"text/html", "text/plain", "text/css"}:
        return f"{media_type};bytes={len(body)}", None
    return f"{media_type or 'unknown'};bytes={len(body)}", None


def _contract_envelope_kind(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    request = payload.get("request")
    if (
        state not in {"ready", "queued", "failed"}
        or not isinstance(request, dict)
        or not isinstance(request.get("id"), str)
        or not request["id"]
    ):
        return None
    if state == "ready":
        return "contract-ready" if "data" in payload and "operation" not in payload and "error" not in payload else None
    if state == "queued":
        operation = payload.get("operation")
        return (
            "contract-queued"
            if isinstance(operation, dict)
            and isinstance(operation.get("id"), str)
            and bool(operation["id"])
            and "data" not in payload
            and "error" not in payload
            else None
        )
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    stack = error.get("stack")
    if (
        not isinstance(error.get("code"), str)
        or not error["code"]
        or not isinstance(message, dict)
        or not isinstance(message.get("key"), str)
        or not message["key"]
        or not isinstance(message.get("fallback"), str)
        or not isinstance(message.get("params"), dict)
        or not isinstance(error.get("origin"), str)
        or not error["origin"]
        or not isinstance(error.get("retryable"), bool)
        or not isinstance(stack, list)
        or not stack
        or "data" in payload
        or "operation" in payload
    ):
        return None
    if not all(
        isinstance(frame, dict)
        and isinstance(frame.get("component"), str)
        and bool(frame["component"])
        and isinstance(frame.get("operation"), str)
        and bool(frame["operation"])
        and isinstance(frame.get("code"), str)
        and bool(frame["code"])
        for frame in stack
    ):
        return None
    return "contract-error"


def _legacy_typed_error_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    descriptor = payload.get("user_message")
    if isinstance(descriptor, dict):
        return (
            isinstance(payload.get("error"), str)
            and isinstance(descriptor.get("key"), str)
            and bool(descriptor.get("key"))
            and isinstance(descriptor.get("fallback"), str)
            and isinstance(descriptor.get("params"), dict)
        )
    return (
        isinstance(payload.get("status"), str)
        and bool(payload.get("status"))
        and isinstance(payload.get("reason") or payload.get("error") or payload.get("error_code"), str)
    )


def _response_envelope_kind(payload: Any, *, is_api_error: bool) -> str:
    contract_kind = _contract_envelope_kind(payload)
    if contract_kind is not None:
        return contract_kind
    if is_api_error:
        return "legacy-error" if _legacy_typed_error_payload(payload) else "untyped-error"
    if isinstance(payload, (dict, list)):
        return "legacy-json"
    return "non-json"


def _read_stream_prefix(response: Any) -> bytes:
    chunks: list[bytes] = []
    for _index in range(12):
        try:
            line = response.fp.readline(4096)
        except (OSError, TimeoutError, socket.timeout):
            break
        if not line:
            break
        chunks.append(line)
        if line in {b"\n", b"\r\n"} and any(chunk.startswith(b"event:") for chunk in chunks):
            break
    return b"".join(chunks)


def _exercise_route(
    runtime: GateLiveServer,
    route: http_routes.Route,
    fixture: RouteFixture,
    credentials: GateAuthCredentials,
    cookie: str,
    capfd: pytest.CaptureFixture[str],
    index: int,
) -> dict[str, Any]:
    excluded_reason = EXCLUDED_ROUTES.get((route.method, route.path))
    if excluded_reason:
        return {
            "index": index,
            "method": route.method,
            "route": route.path,
            "handler": route.handler.__name__,
            "outcome": "excluded",
            "reason": excluded_reason,
            "status": None,
            "body_shape": "not-exercised",
            "request_id": "",
            "request_path": "",
            "traceback": False,
            "typed_error": None,
            "response_envelope": "not-exercised",
        }
    request = _route_request(route, fixture, credentials)
    marker = f"capture-{uuid.uuid4().hex}"
    marker_digest = hashlib.sha256(marker.encode("ascii")).hexdigest()
    measurement_request_id = marker_digest[:16]
    request_id = f"r-{marker_digest[:24]}"
    headers = {
        "Accept": "application/json, text/event-stream;q=0.9, */*;q=0.1",
        "Cookie": cookie,
        "Connection": "close",
        "X-YOLOmux-Measurement": marker,
        "X-YOLOmux-Request-ID": request_id,
        **dict(request.extra_headers),
    }
    if request.body is not None:
        headers["Content-Type"] = request.content_type
        headers["Content-Length"] = str(len(request.body))
    if request.websocket:
        headers.update({
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode("ascii"),
            "Sec-WebSocket-Version": "13",
        })
    capfd.readouterr()
    connection = HTTPConnection("127.0.0.1", runtime.port, timeout=4)
    status = 0
    response_headers: dict[str, str] = {}
    body = b""
    transport_error = ""
    try:
        connection.request(route.method, request.path, body=request.body, headers=headers)
        response = connection.getresponse()
        status = int(response.status)
        response_headers = {str(name).lower(): str(value) for name, value in response.getheaders()}
        if status == 101:
            body = b""
        elif request.stream or response_headers.get("content-type", "").startswith("text/event-stream"):
            body = _read_stream_prefix(response)
        else:
            body = response.read(256 * 1024 + 1)
    except (ConnectionError, OSError, RemoteDisconnected, TimeoutError) as error:
        transport_error = f"{type(error).__name__}: {error}"
    finally:
        connection.close()
    server_stderr = capfd.readouterr().err
    content_type = response_headers.get("content-type", "")
    shape, parsed = _body_shape(content_type, body, stream=request.stream, websocket=status == 101)
    is_api_error = route.path.startswith("/api/") and status >= 400
    response_envelope = _response_envelope_kind(parsed, is_api_error=is_api_error)
    typed_error = response_envelope in {"contract-error", "legacy-error"} if is_api_error else None
    response_request = parsed.get("request") if isinstance(parsed, dict) else None
    response_error = parsed.get("error") if isinstance(parsed, dict) else None
    traceback_found = any(marker_text in server_stderr for marker_text in TRACEBACK_MARKERS)
    failures = []
    if status == 0:
        failures.append("transport-failure")
    if status >= 500:
        failures.append("5xx")
    if is_api_error and not typed_error:
        failures.append("untyped-error")
    if traceback_found:
        failures.append("traceback")
    return {
        "index": index,
        "method": route.method,
        "route": route.path,
        "handler": route.handler.__name__,
        "outcome": "failed" if failures else "exercised",
        "reason": ", ".join(failures),
        "status": status,
        "body_shape": shape,
        "request_id": request_id,
        "measurement_request_id": measurement_request_id,
        "response_request_id": response_request.get("id") if isinstance(response_request, dict) else "",
        "response_error_code": response_error.get("code") if isinstance(response_error, dict) else "",
        "request_path": request.path,
        "traceback": traceback_found,
        "typed_error": typed_error,
        "response_envelope": response_envelope,
        "transport_error": transport_error,
        "body_excerpt": body[:4096].decode("utf-8", errors="replace") if status >= 400 else "",
        "server_log_excerpt": server_stderr[-4000:] if traceback_found else "",
    }


def _assert_sweep_passes(outcomes: list[dict[str, Any]]) -> None:
    failures = [outcome for outcome in outcomes if outcome.get("outcome") == "failed"]
    if failures:
        raise AssertionError(f"route sweep failures: {json.dumps(failures, sort_keys=True)}")


def _assert_correlated_request_id(runtime: GateLiveServer, outcome: dict[str, Any]) -> None:
    wait_for_fixture_http_quiescence(runtime.server, timeout=2.0)
    records = runtime.app.performance_metrics_payload(measurement_scope="capture")["recent"]
    matching = [
        record
        for record in records
        if isinstance(record.get("details"), dict)
        and record["details"].get("measurement_request_id") == outcome["measurement_request_id"]
    ]
    assert matching, {"measurement_request_id": outcome["measurement_request_id"], "recent": records}
    assert matching[-1]["surface"] == f"{outcome['method']} {outcome['route']}"


def _retire_exact_server_failures(
    runtime: GateLiveServer,
    expected: tuple[dict[str, str], ...],
) -> None:
    start = validate_server_log_ring_payload(runtime.server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    failures = tuple(
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    )
    actual = []
    for entry in failures:
        message = json.loads(str(entry["message"]))
        stack = message.get("stack") if isinstance(message.get("stack"), list) else []
        exception = next(
            (
                frame.get("exception")
                for frame in stack
                if isinstance(frame, dict) and isinstance(frame.get("exception"), dict)
            ),
            {},
        )
        request = message.get("request") if isinstance(message.get("request"), dict) else {}
        actual.append({
            "level": str(entry.get("level") or ""),
            "source": str(entry.get("source") or ""),
            "category": str(entry.get("category") or ""),
            "code": str(message.get("code") or ""),
            "request_id": str(request.get("id") or ""),
            "exception": str(exception.get("message") or ""),
        })
    assert transition["droppedCount"] == 0, transition
    assert tuple(actual) == expected, {"expected": expected, "actual": actual, **dict(transition)}
    runtime.server_log_boundary = current


def _retire_correlated_route_failures(
    runtime: GateLiveServer,
    outcome: dict[str, Any],
    expected_owners: tuple[tuple[str, str], ...],
) -> None:
    start = validate_server_log_ring_payload(runtime.server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    failures = tuple(
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    )
    decoded = tuple(json.loads(str(entry["message"])) for entry in failures)
    assert transition["droppedCount"] == 0, transition
    assert tuple((str(entry["source"]), str(entry["category"])) for entry in failures) == expected_owners, {
        "expected": expected_owners,
        "failures": failures,
    }
    assert all(
        isinstance(payload.get("request"), dict)
        and payload["request"].get("id") == outcome["response_request_id"]
        for payload in decoded
    ), {"outcome": outcome, "failures": failures}
    assert decoded[-1]["code"] == outcome["response_error_code"], {"outcome": outcome, "failures": failures}
    runtime.server_log_boundary = current


def _assert_route_sweep_jobd_transport_failure(entry: dict[str, Any]) -> None:
    event = str(entry.get("event") or "")
    request_id = str(entry.get("requestId") or "")
    message = str(entry.get("message") or "")
    assert entry.get("delivery") == "timeout" and event in {"produce", "product"}, entry
    assert entry.get("route") == "local-service:jobd" and request_id, entry
    assert f"action={event} request_id={request_id}" in message, entry
    assert "TimeoutError: timed out" in message, entry


def _retire_route_sweep_server_failures(runtime: GateLiveServer, outcomes: list[dict[str, Any]]) -> None:
    # The filesystem routes answer with an accepted receipt BEFORE the completion owner terminalizes
    # the work - that is the design, not a defect. Taking the open-operations assertion the instant the
    # last HTTP response returns therefore races legitimate in-flight reads (GET /api/fs/list, /diff,
    # /watch-diff) and fails under load. Hold the same bounded barrier the fixture teardown holds, then
    # assert. Do NOT "simplify" this away: a genuinely lost completion still fails here deterministically,
    # because the barrier itself raises with the open-operation dump when the bound expires; a legitimate
    # backlog settles inside the bound. The assertion keeps its full strength - only the instant at which
    # it is taken becomes correct. It runs before the server-log interval is collected as well, because a
    # completion that terminalizes after `current` is sampled leaves the ring boundary stale and resurfaces
    # every unretired entry at teardown.
    runtime.app.wait_for_jobd_operations_terminal(ROUTE_SWEEP_OPERATION_SETTLE_SECONDS)
    start = validate_server_log_ring_payload(runtime.server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    failures = tuple(
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    )
    outcomes_by_response_request_id = {
        str(outcome.get("response_request_id") or ""): outcome
        for outcome in outcomes
        if outcome.get("typed_error") is True and str(outcome.get("response_request_id") or "")
    }
    exercised_surfaces = {
        f"{outcome['method']} {outcome['route']}"
        for outcome in outcomes
        if outcome.get("outcome") == "exercised"
    }
    api_request_ids: list[str] = []
    jobd_failures: list[tuple[str, str]] = []
    transport_phases: list[str] = []
    assert transition["droppedCount"] == 0, transition
    for entry in failures:
        source = str(entry.get("source") or "")
        category = str(entry.get("category") or "")
        if (source, category) == ("api-response", "api"):
            payload = json.loads(str(entry["message"]))
            request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
            request_id = str(request.get("id") or "")
            outcome = outcomes_by_response_request_id.get(request_id)
            assert outcome is not None, {"entry": entry, "outcomes": outcomes}
            assert payload.get("code") == outcome.get("response_error_code"), {"entry": entry, "outcome": outcome}
            api_request_ids.append(request_id)
            continue
        if (source, category) == ("jobd-operation", "operation"):
            payload = json.loads(str(entry["message"]))
            stack = payload.get("stack") if isinstance(payload.get("stack"), list) else []
            route_frame = stack[0] if stack and isinstance(stack[0], dict) else {}
            assert route_frame.get("operation") in exercised_surfaces, {"entry": entry, "outcomes": outcomes}
            assert payload.get("origin") == "local_services.jobd", entry
            assert isinstance(payload.get("request"), dict) and payload["request"].get("id"), entry
            assert isinstance(payload.get("operation"), dict) and payload["operation"].get("id"), entry
            failure_frame = stack[-1] if stack and isinstance(stack[-1], dict) else {}
            jobd_failures.append((
                str(payload.get("code") or ""),
                str(failure_frame.get("operation") or ""),
            ))
            continue
        if (source, category) == ("local-service:jobd", "transport"):
            _assert_route_sweep_jobd_transport_failure(entry)
            transport_phases.append(str(entry["event"]))
            continue
        raise AssertionError({"unexpected_route_sweep_server_failure": entry})
    assert sorted(api_request_ids) == sorted(outcomes_by_response_request_id), {
        "actual": api_request_ids,
        "expected": sorted(outcomes_by_response_request_id),
    }
    if transport_phases:
        terminal_operation_by_phase = {
            "produce": "jobd.produce",
            "product": "jobd.result",
        }
        for phase in set(transport_phases):
            operation = terminal_operation_by_phase[phase]
            assert sum(
                code == "service_unavailable" and observed_operation == operation
                for code, observed_operation in jobd_failures
            ) >= transport_phases.count(phase), {
                "transport_phases": transport_phases,
                "jobd_failures": jobd_failures,
            }
        expected_terminal_operations = {
            terminal_operation_by_phase[phase]
            for phase in transport_phases
        }
        assert all(
            code != "service_unavailable" or operation in expected_terminal_operations
            for code, operation in jobd_failures
        ), {
            "transport_phases": transport_phases,
            "jobd_failures": jobd_failures,
        }
    assert runtime.app.queued_delivery_ledger.open_operations() == [], {
        "open_operations": runtime.app.queued_delivery_ledger.open_operations(),
    }
    runtime.server_log_boundary = current


def _write_report_artifact(outcomes: list[dict[str, Any]], *, base_sha: str) -> None:
    output = str(os.environ.get(REPORT_ENV) or "").strip()
    if not output:
        return
    envelope_kinds = sorted({str(outcome["response_envelope"]) for outcome in outcomes})
    payload = {
        "base_sha": base_sha,
        "registry_owner": "yolomux_lib.http_routes.ALL_ROUTES via yolomux_lib.server.dispatch_http_route",
        "route_count": len(_registry_routes()),
        "api_route_count": sum(route.path.startswith("/api/") for route in _registry_routes()),
        "response_envelope_counts": {
            kind: sum(outcome["response_envelope"] == kind for outcome in outcomes)
            for kind in envelope_kinds
        },
        "outcomes": outcomes,
    }
    Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def route_fixture(gate_runtime_paths, gate_tmux, gate_authenticated_live_server: GateLiveServer) -> RouteFixture:
    repo = gate_runtime_paths.home_dir / "dev" / "route-sweep-repo"
    repo.mkdir(parents=True)
    text_file = repo / "fixture.txt"
    html_file = repo / "fixture.html"
    text_file.write_text("committed route sweep content\n", encoding="utf-8")
    html_file.write_text("<!doctype html><title>route sweep</title>\n", encoding="utf-8")
    for command in (
        ("git", "init", "-q", "-b", "main", str(repo)),
        ("git", "-C", str(repo), "config", "user.name", "Route Sweep Fixture"),
        ("git", "-C", str(repo), "config", "user.email", "route-sweep@example.invalid"),
        ("git", "-C", str(repo), "add", "fixture.txt", "fixture.html"),
        ("git", "-C", str(repo), "commit", "-q", "-m", "fixture baseline"),
    ):
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    text_file.write_text("working route sweep content\n", encoding="utf-8")
    commit = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    session = str(gate_tmux.sessions[0])
    marker = f"route-sweep-cwd-{os.getpid()}"
    command = f"cd {shlex.quote(str(repo))} && printf '{marker}\\n'"
    sent = run_isolated_tmux(gate_tmux, "send-keys", "-t", f"{session}:", command, "Enter", timeout=5)
    assert sent.returncode == 0, sent.stderr or sent.stdout
    observed, panes = wait_for_isolated_tmux_panes(
        gate_tmux,
        (session,),
        lambda captured: marker in captured.get(session, ""),
        timeout=8,
        join_wrapped_lines=True,
    )
    assert observed, panes
    return RouteFixture(
        repo=repo,
        text_file=text_file,
        html_file=html_file,
        commit=commit,
        session=session,
    )


def test_route_sweep_gate_proves_a_raised_route_is_red(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    gate_authenticated_live_server: GateLiveServer,
    gate_auth_credentials: GateAuthCredentials,
    route_fixture: RouteFixture,
) -> None:
    route = next(route for route in _registry_routes() if (route.method, route.path) == ("GET", "/api/ping"))

    def forced_failure(*_args: Any) -> None:
        raise RuntimeError("forced route sweep failure")

    replacement = replace(route, handler=forced_failure)
    patched = tuple(replacement if candidate is route else candidate for candidate in http_routes.ROUTES_BY_METHOD["GET"])
    monkeypatch.setitem(http_routes.ROUTES_BY_METHOD, "GET", patched)
    cookie = _login_cookie(gate_authenticated_live_server, gate_auth_credentials)
    outcome = _exercise_route(
        gate_authenticated_live_server,
        replacement,
        route_fixture,
        gate_auth_credentials,
        cookie,
        capfd,
        1,
    )
    assert outcome["outcome"] == "failed", outcome
    assert outcome["typed_error"] is True, outcome
    assert outcome["response_envelope"] == "contract-error", outcome
    assert outcome["response_error_code"] == "internal_error", outcome
    assert outcome["response_request_id"] == outcome["request_id"], outcome
    with pytest.raises(AssertionError, match="forced_failure") as failure:
        _assert_sweep_passes([outcome])
    failure_text = str(failure.value)
    assert outcome["request_id"] in failure_text
    assert "GET" in failure_text and "/api/ping" in failure_text
    _retire_exact_server_failures(
        gate_authenticated_live_server,
        ({
            "level": "error",
            "source": "api-response",
            "category": "api",
            "code": "internal_error",
            "request_id": outcome["request_id"],
            "exception": "forced route sweep failure",
        },),
    )


def test_route_sweep_accepts_contract_errors_and_rejects_genuinely_untyped_errors() -> None:
    contract_error = {
        "state": "failed",
        "request": {"id": "r-fixture"},
        "error": {
            "code": "operation_not_found",
            "message": {
                "key": "common.notFound",
                "params": {},
                "fallback": "operation not found",
            },
            "origin": "server.http",
            "retryable": False,
            "details": {"operation_id": "route-sweep-missing"},
            "stack": [{
                "component": "server.http",
                "operation": "GET /api/operations/{id}",
                "code": "operation_not_found",
            }],
        },
    }
    assert _response_envelope_kind(contract_error, is_api_error=True) == "contract-error"

    untyped_error = {
        "state": "failed",
        "request": {"id": "r-fixture"},
        "error": {"message": "no typed code or causal stack"},
    }
    assert _response_envelope_kind(untyped_error, is_api_error=True) == "untyped-error"
    with pytest.raises(AssertionError, match="untyped-error"):
        _assert_sweep_passes([{
            "method": "GET",
            "route": "/api/synthetic-untyped",
            "outcome": "failed",
            "reason": "untyped-error",
            "response_envelope": "untyped-error",
        }])


@pytest.mark.parametrize("event", ("produce", "product"))
def test_route_sweep_accepts_only_correlated_jobd_timeout_phases(event: str) -> None:
    entry = {
        "delivery": "timeout",
        "event": event,
        "route": "local-service:jobd",
        "requestId": "fixture-jobd-request",
        "message": f"action={event} request_id=fixture-jobd-request\nTimeoutError: timed out",
    }

    _assert_route_sweep_jobd_transport_failure(entry)

    with pytest.raises(AssertionError):
        _assert_route_sweep_jobd_transport_failure({**entry, "event": "relay"})


def test_authenticated_private_server_exercises_every_registered_route(
    capfd: pytest.CaptureFixture[str],
    gate_authenticated_live_server: GateLiveServer,
    gate_auth_credentials: GateAuthCredentials,
    route_fixture: RouteFixture,
) -> None:
    routes = _registry_routes()
    cookie = _login_cookie(gate_authenticated_live_server, gate_auth_credentials)
    outcomes = [
        _exercise_route(
            gate_authenticated_live_server,
            route,
            route_fixture,
            gate_auth_credentials,
            cookie,
            capfd,
            index,
        )
        for index, route in enumerate(routes, 1)
    ]
    outcomes.append({
        "index": None,
        "method": "GET",
        "route": "/favicon.ico",
        "handler": "browser implicit request (not in router)",
        "outcome": "excluded",
        "reason": "Owned by yo7774 and not present in the programmatic route registry.",
        "status": None,
        "body_shape": "not-exercised",
        "request_id": "",
        "request_path": "",
        "traceback": False,
        "typed_error": None,
        "response_envelope": "not-exercised",
    })
    assert len(outcomes) == len(routes) + 1
    operation = next(
        outcome
        for outcome in outcomes
        if (outcome["method"], outcome["route"]) == ("GET", "/api/operations/*")
    )
    assert operation["typed_error"] is True, operation
    assert operation["response_envelope"] == "contract-error", operation
    activity_summary = next(
        outcome
        for outcome in outcomes
        if (outcome["method"], outcome["route"]) == ("GET", "/api/activity-summary")
    )
    assert activity_summary["outcome"] == "excluded", activity_summary
    assert activity_summary["status"] is None, activity_summary
    create_session = next(
        outcome
        for outcome in outcomes
        if (outcome["method"], outcome["route"]) == ("POST", "/api/create-session")
    )
    assert create_session["status"] == HTTPStatus.NOT_FOUND, create_session
    assert create_session["typed_error"] is True, create_session
    sessions_after_sweep = run_isolated_tmux(
        gate_authenticated_live_server.tmux,
        "list-sessions",
        "-F",
        "#{session_name}",
        timeout=5,
    )
    assert sessions_after_sweep.returncode == 0, sessions_after_sweep.stderr or sessions_after_sweep.stdout
    assert sessions_after_sweep.stdout.splitlines() == [route_fixture.session]
    _write_report_artifact(outcomes, base_sha="9e4940e2fa5c7372d5e0744a078bb82ea0bf848f")
    try:
        _assert_sweep_passes(outcomes)
    finally:
        _retire_route_sweep_server_failures(gate_authenticated_live_server, outcomes)


@pytest.mark.parametrize(
    "failure_case,method,path",
    (
        ("statsd-down", "GET", "/api/stats-snapshot"),
        ("statsd-down", "GET", "/api/stats-delta"),
        ("statsd-down", "POST", "/api/stats-retry"),
        ("statusd-down", "GET", "/api/auto-approve"),
        ("indexd-slow", "GET", "/api/fs/search"),
    ),
)
def test_high_risk_local_service_failure_routes_are_typed_bounded_and_correlated(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    gate_authenticated_live_server: GateLiveServer,
    gate_auth_credentials: GateAuthCredentials,
    route_fixture: RouteFixture,
    failure_case: str,
    method: str,
    path: str,
) -> None:
    app = gate_authenticated_live_server.app
    slow_call_completed = threading.Event()
    if failure_case == "statsd-down":
        unavailable = {
            "ok": False,
            "status": "unavailable",
            "reason": "forced statsd outage",
            "terminal": True,
        }
        monkeypatch.setattr(app.stats_current_client, "ensure_started", lambda: False)
        monkeypatch.setattr(app.stats_current_client, "status", lambda: dict(unavailable))
        monkeypatch.setattr(app.stats_current_client, "retry", lambda: False)
    elif failure_case == "statusd-down":
        monkeypatch.setattr(app, "merge_shared_attention_acks", lambda: False)
        monkeypatch.setattr(
            app.status_client,
            "snapshot",
            lambda sessions, session=None, timeout=1.0: (
                {
                    "ok": False,
                    "status": 503,
                    "error": "forced statusd outage",
                    "terminal": True,
                },
                b"",
            ),
        )
    else:
        def slow_index_request(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], bytes]:
            try:
                threading.Event().wait(0.5)
                return ({
                    "ok": False,
                    "status": "failed",
                    "failure": {
                        "status": int(HTTPStatus.FAILED_DEPENDENCY),
                        "filesystem_error": {
                            "error": "indexd deadline expired",
                            "status": int(HTTPStatus.FAILED_DEPENDENCY),
                            "path": str(route_fixture.repo),
                            "user_message": {
                                "key": "common.requestFailed",
                                "params": {},
                                "fallback": "indexd deadline expired",
                            },
                        },
                    },
                }, b"")
            finally:
                slow_call_completed.set()

        monkeypatch.setattr(app.job_client, "produce", slow_index_request)

    route = next(route for route in _registry_routes() if (route.method, route.path) == (method, path))
    cookie = _login_cookie(gate_authenticated_live_server, gate_auth_credentials)
    started = time.perf_counter()
    outcome = _exercise_route(
        gate_authenticated_live_server,
        route,
        route_fixture,
        gate_auth_credentials,
        cookie,
        capfd,
        1,
    )
    elapsed = time.perf_counter() - started
    if failure_case == "indexd-slow":
        assert slow_call_completed.wait(timeout=2), outcome

    assert 0 < outcome["status"] < 500, json.dumps({"elapsed": elapsed, **outcome}, sort_keys=True)
    assert outcome["typed_error"] is True, json.dumps(outcome, sort_keys=True)
    assert elapsed < 2.0, {"elapsed": elapsed, **outcome}
    _assert_correlated_request_id(gate_authenticated_live_server, outcome)
    _retire_correlated_route_failures(
        gate_authenticated_live_server,
        outcome,
        (("api-response", "api"),),
    )

import errno
import fcntl
import inspect
import io
import json
import os
import socket
from http import HTTPStatus
from pathlib import Path
from types import MethodType
from types import SimpleNamespace
from urllib.parse import quote
from urllib.parse import urlparse

import pytest


from yolomux_lib import app as app_module
from yolomux_lib import http_routes
from yolomux_lib import jobd
from yolomux_lib import server as server_module
from yolomux_lib import server_auth as server_auth_module
from yolomux_lib import web
from yolomux_lib.filesystem import FilesystemError
from yolomux_lib.observability.queued_delivery import QueuedDeliveryLedger
from yolomux_lib import server_logs
from yolomux_lib.workspace import settings as settings_module
from yolomux_lib.tmux import process_group_ownership
from yolomux_lib.common import ACTIVITY_MAX_HOURS
from yolomux_lib.common import error_payload
from yolomux_lib.server import Handler
from yolomux_lib.server import parse_query_float
from yolomux_lib.server import parse_query_int
from yolomux_lib.server import parse_repo_refs_param
from yolomux_lib.server import ws_resize_dimensions
from yolomux_lib.web import html_page


SOURCE_STATIC_DIR = Path(__file__).resolve().parents[1] / "static_src"


def valid_settings_payload() -> dict:
    """Build the normal readable-settings response used by route test doubles."""
    settings = settings_module.default_settings()
    return {
        "settings": settings,
        "defaults": settings_module.default_settings(),
        "choices": settings_module.settings_payload_choices(),
        "catalog": settings_module.settings_catalog(settings),
        "path": "/fixture/settings.yaml",
        "display_path": "~/.config/yolomux/settings.yaml",
        "mtime_ns": 0,
    }


def _record_fixture_process_group(monkeypatch, process):
    monkeypatch.setattr(process_group_ownership.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(process_group_ownership, "process_start_identity", lambda pid: "fixture-start")
    identity = process_group_ownership.record_owned_process_group(process)
    assert identity is not None


def server_ws_json(frame: bytes) -> dict:
    payload_length = frame[1] & 0x7F
    offset = 2
    if payload_length == 126:
        payload_length = int.from_bytes(frame[offset:offset + 2], "big")
        offset += 2
    elif payload_length == 127:
        payload_length = int.from_bytes(frame[offset:offset + 8], "big")
        offset += 8
    return json.loads(frame[offset:offset + payload_length].decode("utf-8"))


def test_expected_client_disconnect_is_counted_without_traceback(monkeypatch):
    recorded = []
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    server.app = SimpleNamespace(record_performance_sample=lambda *args, **kwargs: recorded.append((args, kwargs)))
    stderr = io.StringIO()
    monkeypatch.setattr(server_module.sys, "stderr", stderr)

    try:
        raise BrokenPipeError(errno.EPIPE, "fixture disconnected")
    except BrokenPipeError:
        server.handle_error(None, ("127.0.0.1", 4321))

    assert stderr.getvalue() == "127.0.0.1 - - client disconnected: BrokenPipeError\n"
    assert "Traceback" not in stderr.getvalue()
    assert recorded == [(('http-endpoint', 'expected-disconnect'), {
        'trigger': 'BrokenPipeError',
        'count': 1,
        'details': {'client': '127.0.0.1'},
    })]


def test_request_uri_too_long_logs_framing_evidence_without_changing_the_414():
    class Connection:
        def getsockname(self):
            return ("127.0.0.1", 7771)

        def fileno(self):
            return 91

    handler = object.__new__(Handler)
    handler.client_address = ("127.0.0.1", 43123)
    handler.connection = Connection()
    handler.raw_requestline = b'{"sequence":0,"payload":"body-arrived-as-a-request-line"}\r\n'
    logs = []
    handler.log_error = lambda fmt, *args: logs.append(fmt % args)

    Handler.log_request_uri_too_long(handler)

    assert len(logs) == 1
    prefix, payload = logs[0].split(" ", 1)
    assert prefix == "request-line-capture"
    assert json.loads(payload) == {
        "status": HTTPStatus.REQUEST_URI_TOO_LONG.value,
        "client": "127.0.0.1:43123",
        "connection": {"local": ["127.0.0.1", 7771], "fd": 91},
        "method": "invalid",
        "request_line": '{"sequence":0,"payload":"body-arrived-as-a-request-line"}',
        "request_line_complete": True,
        "request_line_bytes": len(handler.raw_requestline),
    }


def test_request_line_capture_joins_the_immediately_buffered_remainder():
    handler = object.__new__(Handler)
    handler.raw_requestline = b"GET /" + (b"a" * 65532)
    handler.rfile = io.BufferedReader(io.BytesIO(b"tail HTTP/1.1\r\n"))

    captured, complete = Handler.request_line_capture(handler)

    assert complete is True
    assert captured == handler.raw_requestline + b"tail HTTP/1.1\r\n"


def test_send_error_keeps_the_request_uri_too_long_response(monkeypatch):
    handler = object.__new__(Handler)
    captured = []
    delegated = []
    handler.log_request_uri_too_long = lambda: captured.append("framing")
    monkeypatch.setattr(
        server_module.BaseHTTPRequestHandler,
        "send_error",
        lambda request, code, message=None, explain=None: delegated.append((request, code, message, explain)),
    )

    Handler.send_error(handler, HTTPStatus.REQUEST_URI_TOO_LONG)

    assert captured == ["framing"]
    assert delegated == [(handler, HTTPStatus.REQUEST_URI_TOO_LONG, None, None)]


def test_route_micro_helpers_keep_session_scope_body_and_client_address_contracts():
    assert http_routes.session_param({"session": ["alpha"]}) == "alpha"
    assert http_routes.session_param({}, None) is None
    assert http_routes.session_param({"session": [""]}) == ""

    assert http_routes.client_ip(SimpleNamespace(client_address=("203.0.113.4", 443))) == "203.0.113.4"
    assert http_routes.client_ip(SimpleNamespace(client_address=None)) == ""
    assert http_routes.client_ip(SimpleNamespace(client_address=())) == ""

    calls = []
    request = SimpleNamespace(read_json_body=lambda limit, **kwargs: calls.append((limit, kwargs)) or {"ok": True})
    route = http_routes.Route(
        "POST",
        "/api/test",
        "admin",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
        body_limit=123,
    )
    assert http_routes.require_json_body(request, route) == {"ok": True}
    assert calls == [(123, {})]


def test_dev_reload_stream_waits_for_its_low_frequency_poll_before_rechecking_signature(monkeypatch):
    handler = object.__new__(Handler)
    signatures = iter(("bundle-a", "bundle-b"))
    sleeps = []
    events = []
    handler.send_response = lambda _status: None
    handler.send_header = lambda *_args: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None
    handler.dev_bundle_signature = lambda: next(signatures)

    def write_sse_json(event, payload):
        events.append((event, payload))
        if event == "reload" and payload["signature"] == "bundle-b":
            raise OSError("fixture client disconnected")

    handler.write_sse_json = write_sse_json
    monkeypatch.setattr(server_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    handler.stream_dev_reload("bundle-a")

    assert sleeps == [server_module.DEV_RELOAD_POLL_SECONDS]
    assert events == [
        ("ready", {"signature": "bundle-a"}),
        ("reload", {"signature": "bundle-b"}),
    ]


def test_unexpected_server_error_keeps_the_standard_traceback_path(monkeypatch):
    delegated = []
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    monkeypatch.setattr(
        server_module.ThreadingHTTPServer,
        "handle_error",
        lambda self, request, address: delegated.append((request, address)),
    )

    request = object()
    try:
        raise RuntimeError("real handler bug")
    except RuntimeError:
        server.handle_error(request, ("127.0.0.1", 4321))

    assert delegated == [(request, ("127.0.0.1", 4321))]


def test_get_agent_auth_honors_force_query():
    writes = []
    calls = []
    app = SimpleNamespace(agent_auth_payload=lambda force=False: calls.append(force) or {"ok": True, "force": force})
    request = SimpleNamespace(server=SimpleNamespace(app=app), write_json=lambda payload, status=HTTPStatus.OK: writes.append((status, payload)))

    http_routes.get_agent_auth(request, SimpleNamespace(query="force=1"), None)

    assert calls == [True]
    assert writes == [(HTTPStatus.OK, {"ok": True, "force": True})]


def test_request_query_is_request_scoped_and_routes_use_the_shared_accessor(monkeypatch):
    calls = []
    real_parse_qs = http_routes.parse_qs

    def counted_parse_qs(query):
        calls.append(query)
        return real_parse_qs(query)

    monkeypatch.setattr(http_routes, "parse_qs", counted_parse_qs)
    request = SimpleNamespace()
    parsed = SimpleNamespace(query="session=one&session=two")

    first = http_routes.request_query(request, parsed)
    second = http_routes.request_query(request, parsed)

    assert first is second
    assert first == {"session": ["one", "two"]}
    assert calls == ["session=one&session=two"]
    source = inspect.getsource(http_routes)
    assert source.count("request_query(request, parsed)") == 35
    assert source.count("parse_qs(parsed.query)") == 1


def test_chat_send_route_uses_authenticated_username_and_allows_readonly():
    writes = []
    calls = []
    app = SimpleNamespace(
        settings_payload=valid_settings_payload,
        chat_send=lambda username, payload, locale, sender_ip="": calls.append((username, sender_ip, payload, locale)) or {
            "message": {"id": 1, "username": username, "body": payload["body"]},
            "revision": 1,
            "created": True,
        }
    )
    payload = {
        "browser_instance_id": "browser-a", "client_message_uuid": "message-a", "body": "<b>exact text</b>",
        "username": "mallory", "created_at_utc": 0, "is_question": True,
    }
    request = SimpleNamespace(
        server=SimpleNamespace(app=app),
        client_address=("10.1.2.3", 12345),
        auth_identity=lambda: SimpleNamespace(username="readonly-user", role="readonly"), request_locale_pref=lambda: "en",
        read_json_body=lambda _limit: payload, write_json=lambda value, status=HTTPStatus.OK: writes.append((status, value)),
    )
    route = route_by_path("POST", "/api/chat/send")

    http_routes.post_chat_send(request, SimpleNamespace(query=""), route)

    assert route.role == "readonly"
    assert calls == [("readonly-user", "10.1.2.3", payload, "en")]
    assert writes[0][0] == HTTPStatus.CREATED
    assert writes[0][1]["message"] == {"id": 1, "username": "readonly-user", "body": "<b>exact text</b>"}


def test_chat_bootstrap_includes_server_observed_client_ip():
    writes = []
    calls = []
    app = SimpleNamespace(
        settings_payload=valid_settings_payload,
        chat_bootstrap=lambda username, browser_instance_id: calls.append((username, browser_instance_id)) or {
            "revision": 0, "messages": [], "typing": [],
        }
    )
    request = SimpleNamespace(
        server=SimpleNamespace(app=app),
        client_address=("10.1.123.12", 54321),
        auth_identity=lambda: SimpleNamespace(username="alice", role="readonly"),
        write_json=lambda value, status=HTTPStatus.OK: writes.append((status, value)),
    )

    http_routes.get_chat_bootstrap(
        request,
        SimpleNamespace(query="reader_id=ignored-reader&browser_instance_id=browser-a"),
        route_by_path("GET", "/api/chat/bootstrap"),
    )

    assert calls == [("alice", "browser-a")]
    assert writes == [(HTTPStatus.OK, {"revision": 0, "messages": [], "typing": [], "client_ip": "10.1.123.12"})]


def test_chat_yoagent_route_uses_authenticated_identity_and_stored_source():
    writes = []
    calls = []
    payload = {"browser_instance_id": "browser-a", "message_id": 17, "message": "spoofed query"}
    app = SimpleNamespace(
        settings_payload=valid_settings_payload,
        chat_yoagent=lambda username, role, body, locale: calls.append((username, role, body, locale)) or {
            "message": {"id": 18, "username": "YO!agent", "body": "answer"}, "revision": 18, "created": True,
        }
    )
    request = SimpleNamespace(
        server=SimpleNamespace(app=app),
        auth_identity=lambda: SimpleNamespace(username="guest", role="readonly"), request_locale_pref=lambda: "en",
        read_json_body=lambda _limit: payload, write_json=lambda value, status=HTTPStatus.OK: writes.append((status, value)),
    )

    http_routes.post_chat_yoagent(request, SimpleNamespace(query=""), route_by_path("POST", "/api/chat/yoagent"))

    assert calls == [("guest", "readonly", payload, "en")]
    assert writes[0][0] == HTTPStatus.CREATED


def test_get_home_records_html_page_compute_time(monkeypatch):
    writes = []
    html_calls = []
    clock = iter([100.0, 100.037])

    def fake_html_page(sessions, access_role="admin", dev=False, dangerously_yolo=False, accept_language="", auth_username="", recent_sessions=None):
        html_calls.append((sessions, access_role, dev, dangerously_yolo, accept_language, auth_username, recent_sessions))
        return "<html>boot</html>"

    monkeypatch.setattr(http_routes, "html_page", fake_html_page)
    monkeypatch.setattr(http_routes.time, "perf_counter", lambda: next(clock))
    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(sessions=["5"], dangerously_yolo=True, tmux_recency_ordered_sessions=lambda sessions: list(sessions)), dev=True),
        auth_identity=lambda: SimpleNamespace(role="admin", username="alice"),
        write_html=lambda body: writes.append(body),
    )

    http_routes.get_home(request, SimpleNamespace(query=""), route_by_path("GET", "/"))

    assert writes == ["<html>boot</html>"]
    assert html_calls == [(["5"], "admin", True, True, "", "alice", ["5"])]
    assert request._http_response_compute_ms == pytest.approx(37.0)
    assert request._http_response_performance_details == {
        "html_page": True,
        "bootstrap_bytes": len("<html>boot</html>".encode("utf-8")),
        "session_count": 1,
    }


def test_record_http_response_bytes_includes_route_compute_details():
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.path = "/"
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    handler._http_response_compute_ms = 37.0
    handler._http_response_performance_details = {"html_page": True, "bootstrap_bytes": 17}

    Handler.record_http_response_bytes(handler, HTTPStatus.OK, 17, "text/html; charset=utf-8")

    assert len(records) == 1
    args, kwargs = records[0]
    assert args == ("http-endpoint", "GET /")
    assert kwargs["compute_ms"] == 37.0
    assert kwargs["payload_bytes"] == 17
    assert kwargs["details"]["html_page"] is True
    assert kwargs["details"]["bootstrap_bytes"] == 17


def test_record_http_response_bytes_keeps_capture_marker_out_of_metrics():
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.path = "/api/ping"
    handler.headers = {
        "X-YOLOmux-Measurement": "capture-0123456789abcdef0123456789abcdef",
        "X-YOLOmux-Request-ID": "r-web-page-7",
    }
    handler._api_request_id = ""
    handler.client_address = ("127.0.0.1", 43123)
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    handler._http_response_compute_ms = None
    handler._http_response_performance_details = None
    handler._http_request_started_at = None
    handler._http_request_dispatch_started_at = None

    Handler.record_http_response_bytes(handler, HTTPStatus.OK, 17, "application/json")

    assert records[0][1]["details"]["measurement_scope"] == "capture"
    assert records[0][1]["details"]["measurement_request_id"] == server_module.hashlib.sha256(
        b"capture-0123456789abcdef0123456789abcdef"
    ).hexdigest()[:16]
    assert records[0][1]["details"]["measurement_connection_id"] == server_module.hashlib.sha256(
        b"capture-0123456789abcdef0123456789abcdef:43123"
    ).hexdigest()[:16]
    assert records[0][1]["details"]["request_id"] == "r-web-page-7"
    assert records[0][1]["details"]["transport_request_id"] == "r-web-page-7"
    assert "capture-" not in repr(records[0][1])
    handler.headers = {"X-YOLOmux-Measurement": "capture-not-a-random-marker"}
    assert Handler.measurement_scope(handler) == ""


def test_capture_metrics_keep_transport_request_id_when_response_identity_changes():
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.path = "/api/fs/watch-diff"
    handler.headers = {
        "X-YOLOmux-Measurement": "capture-0123456789abcdef0123456789abcdef",
        "X-YOLOmux-Request-ID": "r-browser-issued",
    }
    handler._api_request_id = ""
    handler._http_transport_request_id = ""
    assert Handler.api_request_id(handler) == "r-browser-issued"
    handler._api_request_id = "r-retained-product"
    handler.client_address = ("127.0.0.1", 43123)
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    handler._http_response_compute_ms = None
    handler._http_response_performance_details = None
    handler._http_request_started_at = None
    handler._http_request_dispatch_started_at = None

    Handler.record_http_response_bytes(handler, HTTPStatus.OK, 17, "application/json")

    details = records[0][1]["details"]
    assert details["request_id"] == "r-retained-product"
    assert details["transport_request_id"] == "r-browser-issued"


def test_capture_json_body_retains_only_bounded_salted_identity():
    marker = "capture-0123456789abcdef0123456789abcdef"

    def read(body):
        handler = object.__new__(Handler)
        handler.headers = {
            "Content-Length": str(len(body)),
            "X-YOLOmux-Measurement": marker,
        }
        handler.rfile = io.BytesIO(body)
        assert Handler.read_json_body(handler, 4096) is not None
        return handler._http_request_body_bytes, handler._http_request_body_identity_v1

    first_body = b'{"roots":["/repo"],"client_id":"one"}'
    second_body = b'{"roots":["/other"],"client_id":"one"}'
    first = read(first_body)
    repeated = read(first_body)
    distinct = read(second_body)

    assert first[0] == len(first_body)
    assert len(first[1]) == 32
    assert first == repeated
    assert first[1] != distinct[1]
    assert first_body.decode() not in repr(first)
    assert marker not in repr(first)


def test_dispatch_starts_request_thread_cpu_attribution(monkeypatch):
    request = SimpleNamespace(
        path="/missing",
        redirect_plaintext_to_https_if_needed=lambda _parsed: False,
        require_auth=lambda _role: False,
    )
    monkeypatch.setattr(http_routes.time, "perf_counter", lambda: 10.0)
    monkeypatch.setattr(http_routes.time, "thread_time_ns", lambda: 12_000_000)
    monkeypatch.setattr(http_routes.threading, "get_native_id", lambda: 73)

    http_routes.dispatch_http_route(request, "GET")

    assert request._http_request_dispatch_started_at == 10.0
    assert request._http_request_thread_cpu_started_ns == 12_000_000
    assert request._http_request_thread_native_id == 73


def test_capture_response_records_request_thread_cpu_and_payload_identity(monkeypatch):
    records = []
    handler = object.__new__(Handler)
    handler.command = "POST"
    handler.path = "/api/fs/batch"
    handler.headers = {
        "X-YOLOmux-Measurement": "capture-0123456789abcdef0123456789abcdef",
        "X-YOLOmux-Request-ID": "r-web-batch-1",
    }
    handler._api_request_id = ""
    handler.client_address = ("127.0.0.1", 43123)
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    handler._http_response_compute_ms = None
    handler._http_response_performance_details = None
    handler._http_request_started_at = None
    handler._http_request_dispatch_started_at = 10.0
    handler._http_request_thread_cpu_started_ns = 12_000_000
    handler._http_request_thread_native_id = 73
    handler._http_request_body_bytes = 481
    handler._http_request_body_identity_v1 = "0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(server_module.time, "perf_counter", lambda: 10.025)
    monkeypatch.setattr(server_module.time, "thread_time_ns", lambda: 15_500_000)
    monkeypatch.setattr(server_module.threading, "get_native_id", lambda: 73)

    Handler.record_http_response_bytes(handler, HTTPStatus.OK, 17, "application/json")

    details = records[0][1]["details"]
    assert details["process_pid"] == os.getpid()
    assert details["thread_native_id"] == 73
    assert details["request_thread_cpu_ms"] == pytest.approx(3.5)
    assert details["dispatch_to_record_wall_ms"] == pytest.approx(25.0)
    assert details["request_body_bytes"] == 481
    assert details["request_body_identity_v1"] == "0123456789abcdef0123456789abcdef"


def test_keepalive_request_profile_is_reset_before_each_dispatch(monkeypatch):
    handler = object.__new__(Handler)
    handler._http_response_compute_ms = 37.0
    handler._http_response_performance_details = {"html_page": True}
    observed = []

    def fake_handle_one_request(request):
        observed.append((
            request._http_response_compute_ms,
            request._http_response_performance_details,
            request._http_request_line_read_at,
            request._http_request_parse_completed_at,
            request._http_request_dispatch_started_at,
            request._http_request_thread_cpu_started_ns,
            request._http_request_thread_native_id,
            request._http_request_body_bytes,
            request._http_request_body_identity_v1,
        ))

    monkeypatch.setattr(server_module.BaseHTTPRequestHandler, "handle_one_request", fake_handle_one_request)

    Handler.handle_one_request(handler)
    handler._http_response_compute_ms = 12.0
    handler._http_response_performance_details = {"stats_build_ms": 12.0}
    Handler.handle_one_request(handler)

    assert observed[0][:2] == (None, None)
    assert observed[1][:2] == (None, None)
    assert all(item[2:] == (None, None, None, None, None, None, None) for item in observed)


def test_parse_request_profiles_request_line_and_headers(monkeypatch):
    handler = object.__new__(Handler)
    ticks = iter((10.0, 10.004))
    monkeypatch.setattr(server_module.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(server_module.BaseHTTPRequestHandler, "parse_request", lambda _request: True)

    assert Handler.parse_request(handler) is True
    assert handler._http_request_line_read_at == pytest.approx(10.0)
    assert handler._http_request_parse_completed_at == pytest.approx(10.004)


def test_keepalive_homepage_profile_cannot_leak_to_api_endpoints(monkeypatch):
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    paths = iter(["/", "/api/ping", "/api/settings", "/api/stats-sample"])

    def fake_handle_one_request(request):
        request.path = next(paths)
        request._http_request_dispatch_started_at = server_module.time.perf_counter()
        if request.path == "/":
            request._http_response_compute_ms = 37.0
            request._http_response_performance_details = {"html_page": True}
        Handler.record_http_response_bytes(request, HTTPStatus.OK, 17, "application/json")

    monkeypatch.setattr(server_module.BaseHTTPRequestHandler, "handle_one_request", fake_handle_one_request)

    for _ in range(4):
        Handler.handle_one_request(handler)

    assert [args[1] for args, _kwargs in records] == ["GET /", "GET /api/ping", "GET /api/settings", "GET /api/stats-sample"]
    assert records[0][1]["compute_ms"] == 37.0
    assert all(kwargs["compute_ms"] != 37.0 for _args, kwargs in records[1:])
    assert all("html_page" not in kwargs["details"] for _args, kwargs in records[1:])


def test_generic_route_profile_uses_this_request_dispatch_timer(monkeypatch):
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.path = "/api/ping"
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    handler._http_response_compute_ms = None
    handler._http_response_performance_details = None
    handler._http_request_started_at = 100.0
    handler._http_request_line_read_at = 100.007
    handler._http_request_parse_completed_at = 100.01
    handler._http_request_dispatch_started_at = 100.01
    monkeypatch.setattr(server_module.time, "perf_counter", lambda: 100.035)

    Handler.record_http_response_bytes(handler, HTTPStatus.OK, 17, "application/json")

    _args, kwargs = records[0]
    assert kwargs["compute_ms"] == pytest.approx(25.0)
    assert kwargs["details"]["request_line_wait_ms"] == pytest.approx(7.0)
    assert kwargs["details"]["request_header_parse_ms"] == pytest.approx(3.0)
    assert kwargs["details"]["request_parse_to_route_ms"] == pytest.approx(0.0)
    assert kwargs["details"]["accept_to_route_ms"] == pytest.approx(10.0)
    assert kwargs["details"]["request_total_ms"] == pytest.approx(35.0)


def test_route_to_representation_ready_is_stamped_at_the_pre_write_boundary(monkeypatch):
    # W9: the shared response writer must stamp route_to_representation_ready_ms once the final wire
    # bytes exist but BEFORE any header/body byte is written. The number is route entry (dispatch
    # start) -> representation ready, so it must exclude the body write and the whole-request total.
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.path = "/api/system-status"
    handler.headers = {}  # no Accept-Encoding: keep the body identity-encoded, one compression tick
    handler.close_connection = False
    handler.wfile = io.BytesIO()
    handler._api_request_id = "r-fixture"
    handler._http_response_compute_ms = None
    handler._http_response_performance_details = None
    handler._http_request_started_at = 100.0
    handler._http_request_line_read_at = None
    handler._http_request_parse_completed_at = None
    handler._http_request_dispatch_started_at = 100.0
    handler.server = SimpleNamespace(app=SimpleNamespace(
        record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs)),
    ))
    handler.send_response = lambda *args, **kwargs: None
    handler.send_header = lambda *args, **kwargs: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None

    # dispatch=100.0; compression_started=100.005; representation_ready=100.012 (=> 12ms boundary);
    # write_started=100.020; write end=100.050 (write_ms=30); record response_started=100.060.
    clock = iter([100.005, 100.012, 100.020, 100.050, 100.060])
    monkeypatch.setattr(server_module.time, "perf_counter", lambda: next(clock))

    body = b'{"ok":true}'
    Handler._write_product_representation(
        handler,
        body,
        status=HTTPStatus.OK,
        content_type="application/json; charset=utf-8",
        disposition="inline",
        filename="",
    )

    assert len(records) == 1
    _args, kwargs = records[0]
    details = kwargs["details"]
    assert details["route_to_representation_ready_ms"] == pytest.approx(12.0)
    # The boundary number excludes the body write (30ms) and is not the whole-request compute (60ms).
    assert details["write_ms"] == pytest.approx(30.0)
    assert kwargs["compute_ms"] == pytest.approx(60.0)
    assert details["route_to_representation_ready_ms"] < details["write_ms"]
    assert details["route_to_representation_ready_ms"] < kwargs["compute_ms"]


def test_route_to_representation_ready_is_absent_without_a_dispatch_timer(monkeypatch):
    # A response written on a path that never set the dispatch timer must not fabricate a zero; the
    # boundary field is simply absent so no reader can cite an unmeasured latency as measured.
    records = []
    handler = object.__new__(Handler)
    handler.command = "GET"
    handler.path = "/api/system-status"
    handler.headers = {}
    handler.close_connection = False
    handler.wfile = io.BytesIO()
    handler._api_request_id = "r-fixture"
    handler._http_response_compute_ms = None
    handler._http_response_performance_details = None
    handler._http_request_started_at = None
    handler._http_request_line_read_at = None
    handler._http_request_parse_completed_at = None
    handler._http_request_dispatch_started_at = None
    handler.server = SimpleNamespace(app=SimpleNamespace(
        record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs)),
    ))
    handler.send_response = lambda *args, **kwargs: None
    handler.send_header = lambda *args, **kwargs: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None
    monkeypatch.setattr(server_module.time, "perf_counter", lambda: 100.0)

    Handler._write_product_representation(
        handler,
        b'{"ok":true}',
        status=HTTPStatus.OK,
        content_type="application/json; charset=utf-8",
        disposition="inline",
        filename="",
    )

    assert len(records) == 1
    _args, kwargs = records[0]
    assert "route_to_representation_ready_ms" not in kwargs["details"]


def test_parse_repo_refs_param_decodes_per_repo_overrides():
    # C6: decode the per-repo FROM/TO JSON map; keep only well-formed string ref pairs.
    raw = json.dumps({"/repo/a": {"from": "abc123", "to": "current"}, "/repo/b": {"from": "  ", "to": "HEAD"}})
    parsed = parse_repo_refs_param(raw)
    assert parsed == {"/repo/a": {"from": "abc123", "to": "current"}, "/repo/b": {"to": "HEAD"}}


def test_error_payload_normalizes_status_and_context():
    assert error_payload("bad", path="/tmp/a", session="6", status=HTTPStatus.BAD_REQUEST) == {
        "error": "bad",
        "user_message": {"key": "", "params": {}, "fallback": "bad"},
        "path": "/tmp/a",
        "session": "6",
        "status": 400,
    }


def test_error_payload_reuses_typed_message_metadata_and_keeps_diagnostic():
    error = FilesystemError(
        "filesystem operation failed",
        status=403,
        message_key="fs.error.operationFailed",
        diagnostic="raw permission detail",
    )

    assert error.payload(path="/private") == {
        "error": "filesystem operation failed",
        "user_message": {
            "key": "fs.error.operationFailed",
            "params": {},
            "fallback": "filesystem operation failed",
        },
        "diagnostic": "raw permission detail",
        "path": "/private",
        "status": 403,
    }


def test_parse_repo_refs_param_rejects_garbage():
    assert parse_repo_refs_param(None) is None
    assert parse_repo_refs_param("") is None
    assert parse_repo_refs_param("not json") is None
    assert parse_repo_refs_param(json.dumps([1, 2, 3])) is None
    # an entry with no usable string refs is dropped; an all-empty map collapses to None
    assert parse_repo_refs_param(json.dumps({"/repo/a": {"from": 5}})) is None


def test_parse_query_int_defaults_and_valid_values():
    assert parse_query_int({}, "lines", 90) == (90, "")
    assert parse_query_int({"lines": ["12"]}, "lines", 90) == (12, "")
    assert parse_query_int({"lines": ["999999"]}, "lines", 90, max_value=500) == (500, "")


def test_parse_query_int_reports_bad_values():
    value, error = parse_query_int({"messages": ["many"]}, "messages", 40)

    assert value is None
    assert error == "messages must be an integer"
    value, error = parse_query_int({"messages": ["-1"]}, "messages", 40)
    assert value is None
    assert error == "messages must be at least 1"


def test_parse_query_float_rejects_non_finite_and_negative_values():
    assert parse_query_float({}, "hours", 24.0) == (24.0, "")
    assert parse_query_float({"hours": ["9999"]}, "hours", 24.0, max_value=48.0) == (48.0, "")
    assert parse_query_float({"hours": ["nan"]}, "hours", 24.0) == (None, "hours must be finite")
    assert parse_query_float({"hours": ["inf"]}, "hours", 24.0) == (None, "hours must be finite")
    assert parse_query_float({"hours": ["-1"]}, "hours", 24.0) == (None, "hours must be at least 0")


def test_write_validated_float_result_centralizes_activity_hours_validation():
    handler = object.__new__(Handler)
    writes = []
    handler.write_app_result = lambda result: writes.append(("app", result))
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))

    Handler.write_validated_float_result(
        handler,
        {"hours": ["8761"]},
        "hours",
        24.0,
        ACTIVITY_MAX_HOURS,
        lambda value: ({"hours": value}, HTTPStatus.OK),
    )
    assert writes == [("app", ({"hours": ACTIVITY_MAX_HOURS}, HTTPStatus.OK))]

    writes.clear()
    Handler.write_validated_float_result(
        handler,
        {"hours": ["nope"]},
        "hours",
        24.0,
        ACTIVITY_MAX_HOURS,
        lambda value: ({"hours": value}, HTTPStatus.OK),
    )
    assert writes == [("json", HTTPStatus.BAD_REQUEST, {
        "error": "hours must be a number",
        "user_message": {
            "key": "request.error.number",
            "params": {"field": "hours"},
            "fallback": "hours must be a number",
        },
        "status": HTTPStatus.BAD_REQUEST,
    })]


def test_activity_hours_routes_share_float_validation_owner():
    for handler in [http_routes.get_activity, http_routes.get_session_files_batch, http_routes.get_session_files]:
        body = inspect.getsource(handler)
        assert "write_validated_float_result" in body
        assert "ACTIVITY_MAX_HOURS" in body
        assert "parse_query_float(qs, \"hours\"" not in body
        assert "24.0 * 365.0" not in body


def test_write_int_query_app_result_parses_and_validates_once():
    handler = object.__new__(Handler)
    writes = []
    handler.write_app_result = lambda result: writes.append(("app", result))
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))

    Handler.write_int_query_app_result(
        handler,
        SimpleNamespace(query="session=6&limit=7"),
        "limit",
        100,
        500,
        lambda qs, limit: ({"session": qs["session"][0], "limit": limit}, HTTPStatus.OK),
    )
    Handler.write_int_query_app_result(
        handler,
        SimpleNamespace(query="limit=bad"),
        "limit",
        100,
        500,
        lambda qs, limit: ({"limit": limit}, HTTPStatus.OK),
    )

    assert writes == [
        ("app", ({"session": "6", "limit": 7}, HTTPStatus.OK)),
        ("json", HTTPStatus.BAD_REQUEST, {
            "error": "limit must be an integer",
            "user_message": {
                "key": "request.error.integer",
                "params": {"field": "limit"},
                "fallback": "limit must be an integer",
            },
            "status": 400,
        }),
    ]


@pytest.mark.parametrize(
    ("outcome", "process_returncode", "expected_terminations"),
    (("close", None, 1), ("read-error", None, 1), ("exited", 1, 0)),
)
def test_websocket_bridge_terminates_live_tmux_process_group_once(
    outcome: str,
    process_returncode: int | None,
    expected_terminations: int,
    monkeypatch,
):
    """Bridge teardown owns live tmux children for close and read-failure paths."""

    class FakeProcess:
        def poll(self):
            return process_returncode

    master_fd, slave_fd = os.pipe()
    process = FakeProcess()
    terminated = []
    handler = object.__new__(Handler)
    handler.connection = object()
    handler.rfile = object()
    handler.server = SimpleNamespace(host_pty_dimensions_for_session=lambda _session: (24, 80))
    handler.read_initial_ws_payloads = lambda: (24, 80, False, [])
    handler.read_ws_frame_with_timeout = lambda: (8, b"")
    monkeypatch.setattr(server_module.pty, "openpty", lambda: (master_fd, slave_fd))
    monkeypatch.setattr(server_module, "set_pty_size", lambda *_args: None)
    monkeypatch.setattr(server_module, "tmux_client_name_for_fd", lambda _fd: "fixture-client")
    monkeypatch.setattr(server_module, "configure_session_tmux_options", lambda _session: None)
    monkeypatch.setattr(server_module, "tmux_attach_command", lambda *, readonly: ["tmux", "attach-session"])
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(server_module, "record_owned_process_group", lambda _process: None)
    monkeypatch.setattr(server_module, "refresh_tmux_session_clients_after_attach", lambda _session: None)
    monkeypatch.setattr(server_module, "wait_for_ws_frame", lambda *_args: False)
    monkeypatch.setattr(server_module, "terminate_process_group", terminated.append)
    if outcome == "read-error":
        monkeypatch.setattr(server_module.select, "select", lambda *_args: (_ for _ in ()).throw(OSError("fixture read error")))
    else:
        monkeypatch.setattr(server_module.select, "select", lambda _read, _write, _error, _timeout: ([handler.connection], [], []))

    Handler.bridge_tmux(handler, "1")

    assert terminated == ([process] if expected_terminations else [])


def test_websocket_bridge_treats_close_before_initial_resize_as_normal_disconnect(monkeypatch):
    handler = object.__new__(Handler)
    handler.read_initial_ws_payloads = lambda: (_ for _ in ()).throw(ConnectionError("websocket closed"))
    monkeypatch.setattr(server_module.pty, "openpty", lambda: pytest.fail("PTY must not open after the client closes"))

    Handler.bridge_tmux(handler, "1")


def test_websocket_bridge_reclaims_resize_authority_on_first_attached_pty_frame(monkeypatch):
    """The initial browser resize can precede tmux client registration; first PTY output retries it."""

    class FakeProcess:
        def poll(self):
            return None

    master_fd, slave_fd = os.pipe()
    process = FakeProcess()
    claims = []
    connection = SimpleNamespace(sendall=lambda _data: None)
    selections = iter([([master_fd], [], []), ([connection], [], [])])
    handler = object.__new__(Handler)
    handler.connection = connection
    handler.rfile = object()
    handler.server = SimpleNamespace(
        host_pty_dimensions_for_session=lambda _session: (24, 80),
        record_host_pty_dimensions=lambda *_args: None,
        claim_resize_authority=lambda *args: claims.append(args) or False,
    )
    handler.read_initial_ws_payloads = lambda: (84, 110, True, [])
    handler.read_ws_frame_with_timeout = lambda: (8, b"")
    monkeypatch.setattr(server_module.pty, "openpty", lambda: (master_fd, slave_fd))
    monkeypatch.setattr(server_module, "set_pty_size", lambda *_args: None)
    monkeypatch.setattr(server_module, "tmux_client_name_for_fd", lambda _fd: "/dev/pts/fixture")
    monkeypatch.setattr(server_module, "configure_session_tmux_options", lambda _session: None)
    monkeypatch.setattr(server_module, "tmux_attach_command", lambda *, readonly: ["tmux", "attach-session"])
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(server_module, "record_owned_process_group", lambda _process: None)
    monkeypatch.setattr(server_module, "refresh_tmux_session_clients_after_attach", lambda _session: None)
    monkeypatch.setattr(server_module, "wait_for_ws_frame", lambda *_args: False)
    monkeypatch.setattr(server_module.select, "select", lambda *_args: next(selections))
    monkeypatch.setattr(server_module.os, "read", lambda *_args: b"attached frame")
    monkeypatch.setattr(server_module, "make_ws_frame", lambda data, opcode: data)
    monkeypatch.setattr(server_module, "terminate_process_group", lambda *_args: None)

    Handler.bridge_tmux(handler, "1", resize_client_id="browser-1")

    assert claims == [
        ("1", "/dev/pts/fixture", "browser-1", 110, 84),
        ("1", "/dev/pts/fixture", "browser-1", 110, 84),
    ]


def test_websocket_frame_reads_are_timeout_wrapped():
    # A blocked WS frame read must not hang the handler thread forever, so the read is bounded by a
    # timeout constant and goes through the timeout-wrapped helper.
    assert server_module.WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS == 5.0
    assert callable(getattr(Handler, "read_ws_frame_with_timeout", None))


def test_websocket_resize_dimensions_are_clamped():
    assert ws_resize_dimensions({"rows": 9999, "cols": 0}, 36, 120) == (1000, 1)
    assert ws_resize_dimensions({"rows": 24, "cols": 80}, 36, 120) == (24, 80)
    assert ws_resize_dimensions({"rows": True, "cols": 80}, 36, 120) is None
    assert ws_resize_dimensions({"rows": "24", "cols": 80}, 36, 120) is None


def test_accept_websocket_rejects_non_ascii_key_cleanly():
    writes = []
    handler = object.__new__(Handler)
    handler.headers = {"Sec-WebSocket-Key": "bad-\N{SNOWMAN}"}
    handler.write_text = lambda value, status=HTTPStatus.OK: writes.append((status, value))

    assert Handler.accept_websocket(handler) is False
    assert writes == [(HTTPStatus.BAD_REQUEST, "invalid Sec-WebSocket-Key\n")]


def test_accept_websocket_prevents_http_reparse_of_upgraded_frames():
    """Masked WebSocket bytes must never become a second HTTP request line."""
    responses = []
    headers = []
    handler = object.__new__(Handler)
    handler.headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
    handler.close_connection = False
    handler.send_response = responses.append
    handler.send_header = lambda name, value: headers.append((name, value))
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None

    assert Handler.accept_websocket(handler) is True
    assert handler.close_connection is True
    assert responses == [HTTPStatus.SWITCHING_PROTOCOLS]
    assert ("Upgrade", "websocket") in headers


def test_configure_session_tmux_options_uses_active_surface_authority(monkeypatch):
    # Two browser surfaces on one session attach as two differently-sized tmux clients. Under the
    # default `latest` policy the most-recently-active (often smaller) client keeps resizing the
    # shared window; when it shrinks below a larger client's height that client's xterm smears the
    # green tmux status line across the orphaned rows. YOLOmux keeps `largest`, starts each attach
    # as ignore-size, and lets the active browser surface clear ignore-size for its own client while
    # silencing every wider client on the session (see claim_tmux_resize_authority).
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    calls: list[list[str]] = []
    monkeypatch.setattr(server_module, "tmux", lambda args: calls.append(list(args)))
    monkeypatch.setattr(server_module, "tmux_supports_ignore_size_flag", lambda: True)

    server_module.configure_session_tmux_options("3")

    assert ["set-option", "-s", "set-clipboard", "on"] in calls
    assert ["set-option", "-t", "=3:", "window-size", "largest"] in calls
    assert ["set-option", "-wg", "aggressive-resize", "on"] in calls
    assert server_module.tmux_attach_command(readonly=False) == ["tmux", "attach-session", "-f", "ignore-size"]
    assert server_module.tmux_attach_command(readonly=True) == ["tmux", "attach-session", "-r", "-f", "ignore-size"]


def test_tmux_attach_command_falls_back_when_client_flags_are_unsupported(monkeypatch):
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    monkeypatch.setattr(server_module, "tmux_supports_ignore_size_flag", lambda: False)

    assert server_module.tmux_attach_command(readonly=False) == ["tmux", "attach-session"]
    assert server_module.tmux_attach_command(readonly=True) == ["tmux", "attach-session", "-r"]


def test_configure_session_tmux_options_skips_newer_window_size_option_on_legacy_tmux(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(server_module, "tmux", lambda args: calls.append(list(args)))
    monkeypatch.setattr(server_module, "tmux_supports_ignore_size_flag", lambda: False)

    server_module.configure_session_tmux_options("3")

    assert calls == [
        ["set-option", "-s", "set-clipboard", "on"],
        ["set-option", "-wg", "aggressive-resize", "on"],
    ]


def _client_list_runner(stdout, calls):
    # `#{client_name}\t#{client_session}\t#{client_width}\t#{client_height}\t#{client_flags}` per row.
    def fake_run(cmd, **kwargs):
        if "list-clients" in cmd:
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run


def test_claim_tmux_resize_authority_silences_clients_exceeding_either_dimension(monkeypatch):
    # A 110x84 browser must take ownership back from a 112x34 sibling in both dimensions. Width-only
    # ownership left the larger browser displaying a 33-row shared screen until page reload.
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    calls: list[list[str]] = []
    stdout = (
        "/dev/pts/1\t6\t110\t84\tattached,ignore-size,UTF-8\n"  # active surface, wrongly ignored
        "/dev/pts/2\t6\t112\t34\tattached,UTF-8\n"              # wider -> silence
        "/dev/pts/3\t6\t90\t90\tattached,UTF-8\n"               # taller -> silence
        "/dev/pts/4\t6\t100\t60\tattached,UTF-8\n"              # smaller in both -> harmless
        "/dev/pts/5\t7\t200\t100\tattached,UTF-8\n"             # other session -> not our concern
    )
    monkeypatch.setattr(server_module.subprocess, "run", _client_list_runner(stdout, calls))

    assert server_module.claim_tmux_resize_authority("6", "/dev/pts/1", 110, 84) is True

    assert ["tmux", "refresh-client", "-t", "/dev/pts/1", "-f", "!ignore-size"] in calls
    assert ["tmux", "refresh-client", "-t", "/dev/pts/2", "-f", "ignore-size"] in calls
    assert ["tmux", "refresh-client", "-t", "/dev/pts/3", "-f", "ignore-size"] in calls
    assert all("/dev/pts/4" not in call for call in calls)
    assert all("/dev/pts/5" not in call for call in calls)


def test_claim_tmux_resize_authority_noop_when_active_dominates_both_dimensions(monkeypatch):
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    calls: list[list[str]] = []
    stdout = (
        "/dev/pts/1\t6\t120\t80\tattached,UTF-8\n"
        "/dev/pts/2\t6\t100\t60\tattached,UTF-8\n"
        "/dev/pts/3\t6\t130\t90\tattached,ignore-size,UTF-8\n"
    )
    monkeypatch.setattr(server_module.subprocess, "run", _client_list_runner(stdout, calls))

    assert server_module.claim_tmux_resize_authority("6", "/dev/pts/1", 120, 80) is False
    assert calls == []


def test_tmux_attach_routes_through_shared_options():
    # The browser attach path must go through the shared option helper so the window-size fix
    # cannot regress by re-introducing an inline set-clipboard block.
    bridge_body = inspect.getsource(Handler.bridge_tmux)

    assert "configure_session_tmux_options(session)" in bridge_body
    assert "set-clipboard" not in bridge_body


def test_tmux_attach_refreshes_clients_after_attach():
    bridge_body = inspect.getsource(Handler.bridge_tmux)

    assert "refresh_tmux_session_clients_after_attach(session)" in bridge_body


def test_configure_session_tmux_options_uses_bounded_tmux_helper():
    body = inspect.getsource(server_module.configure_session_tmux_options)

    assert "tmux(args)" in body
    assert "subprocess.run" not in body


def test_html_uses_browser_highlight_js_bundle():
    html = html_page(["6"], "admin")

    assert "highlight.js@11.9.0/lib/common.min.js" not in html
    assert "/static/vendor/highlight.min.js" in html
    assert "/static/vendor/highlight-github-dark.min.css" in html
    assert "/static/vendor/marked.min.js" in html
    assert web.static_content_type("vendor/highlight.min.js") == "application/javascript; charset=utf-8"
    assert web.static_content_type("vendor/highlight-github-dark.min.css") == "text/css; charset=utf-8"
    assert web.static_content_type("vendor/marked.min.js") == "application/javascript; charset=utf-8"
    assert web.static_asset_path("vendor/highlight.min.js").is_file()


def test_handle_upload_enforces_live_app_size_limit():
    app = SimpleNamespace(file_transfer_max_bytes=lambda: 5, upload_files=lambda *_args: (_ for _ in ()).throw(AssertionError("upload_files should not run")))
    handler = SimpleNamespace(
        headers={"Content-Length": "6", "Content-Type": "multipart/form-data; boundary=x"},
        rfile=io.BytesIO(b"123456"),
        server=SimpleNamespace(app=app),
        close_connection=False,
        file_transfer_max_bytes=lambda: app.file_transfer_max_bytes(),
    )

    payload, status = Handler.handle_upload(handler, "6")

    assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert payload == {
        "session": "6",
        "error": "upload is too large; limit is 5 bytes",
        "user_message": {
            "key": "request.error.contentTooLarge",
            "params": {"max": 5},
            "fallback": "upload is too large; limit is 5 bytes",
        },
        "status": 413,
    }
    assert handler.close_connection is True


def test_handle_upload_threads_authenticated_yolomux_user_to_all_upload_entry_points():
    boundary = "upload-test"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"one.png\"\r\n\r\n".encode()
        + b"png\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    calls = []
    app = SimpleNamespace(
        file_transfer_max_bytes=lambda: 1024,
        upload_files=lambda session, files, **kwargs: calls.append(("terminal", session, files, kwargs)) or ({"ok": True}, HTTPStatus.OK),
        upload_editor_files=lambda files, **kwargs: calls.append(("editor", "", files, kwargs)) or ({"ok": True}, HTTPStatus.OK),
    )
    handler = SimpleNamespace(
        headers={"Content-Length": str(len(body)), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        rfile=io.BytesIO(body),
        server=SimpleNamespace(app=app),
        close_connection=False,
        file_transfer_max_bytes=lambda: app.file_transfer_max_bytes(),
        auth_identity=lambda: SimpleNamespace(username="alice"),
    )

    Handler.handle_upload(handler, "6")
    handler.rfile = io.BytesIO(body)
    Handler.handle_upload(handler, "", editor_path="/repo/note.md")

    assert calls[0][0:2] == ("terminal", "6")
    assert calls[0][3] == {"auth_username": "alice"}
    assert calls[1][0] == "editor"
    assert calls[1][3] == {"editor_path": "/repo/note.md", "base_dir": "", "auth_username": "alice", "session": "editor"}


def test_request_body_reader_owns_content_length_validation():
    # RA6: every POST body reader should route Content-Length parsing through one helper so missing,
    # invalid, non-positive, and oversized bodies cannot drift by route.
    assert "Content-Length" in inspect.getsource(Handler.read_request_body)
    for method in [Handler.read_json_body, Handler.read_urlencoded_form, Handler.handle_client_event, Handler.handle_upload]:
        body = inspect.getsource(method)
        assert "read_request_body" in body
        assert "self.headers.get(\"Content-Length" not in body
    assert "read_json_body" in inspect.getsource(http_routes.require_json_body)


@pytest.mark.parametrize(
    ("headers", "body", "status", "reason", "reason_key"),
    (
        ({"Content-Length": "bad"}, b"", HTTPStatus.LENGTH_REQUIRED, "missing or invalid Content-Length", "request.error.contentLengthInvalid"),
        ({"Content-Length": "1"}, b"{", HTTPStatus.BAD_REQUEST, "invalid JSON", "request.error.invalidJson"),
    ),
)
def route_handler(path, app=None, readonly=False):
    calls = []
    writes = []
    handler = object.__new__(Handler)
    handler.path = path
    handler.server = SimpleNamespace(app=app or SimpleNamespace(), dev=False)
    handler.close_connection = False
    handler.require_auth = lambda role="readonly": calls.append(("require_auth", role)) or True
    handler.auth_readonly = lambda: readonly
    handler.auth_identity = lambda: SimpleNamespace(role="readonly" if readonly else "admin")
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
    handler.write_json_bytes = lambda value, status=HTTPStatus.OK: writes.append(("json_bytes", status, value))
    handler.write_product_bytes = lambda data, product, promise=None: writes.append(("product", HTTPStatus.OK, data))
    handler.write_text = lambda value, status=HTTPStatus.OK, content_type="text/plain; charset=utf-8": writes.append(("text", status, value, content_type))
    handler.write_html = lambda value: writes.append(("html", HTTPStatus.OK, value))
    handler.write_app_result = lambda result: handler.write_json(result[0], status=result[1])
    handler.reject_forbidden = lambda identity, required_role: writes.append(("forbidden", HTTPStatus.FORBIDDEN, identity.role, required_role))
    return handler, calls, writes


def route_by_path(method, path):
    for route in http_routes.routes_for_method(method):
        if route.path == path:
            return route
    raise AssertionError(f"missing route: {method} {path}")


def test_unknown_get_localizes_plain_text_from_accept_language(monkeypatch):
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    web.bootstrap_locale_catalogs.cache_clear()
    writes = []
    request = SimpleNamespace(
        require_auth=lambda role: role == "readonly",
        request_locale_pref=lambda: "system",
        headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
        write_text=lambda body, status=HTTPStatus.OK: writes.append((status, body)),
    )

    try:
        http_routes._write_not_found_after_default_auth(request, "GET")
        assert writes == [
            (HTTPStatus.NOT_FOUND, web.server_string("zh-Hans", "common.notFound") + "\n"),
        ]
    finally:
        web.bootstrap_locale_catalogs.cache_clear()


def test_http_route_registry_groups_dispatch_and_keeps_verbs_thin():
    get_body = inspect.getsource(Handler.do_GET)
    post_body = inspect.getsource(Handler.do_POST)

    assert 'dispatch_http_route(self, "GET")' in get_body
    assert 'dispatch_http_route(self, "POST")' in post_body
    assert "if parsed.path" not in get_body
    assert "if parsed.path" not in post_body
    assert set(http_routes.ROUTE_GROUPS) == {"core", "yoagent", "chat", "filesystem", "tmux"}
    assert route_by_path("GET", "/api/activity-summary").group == "core"
    assert http_routes.route_for_request("GET", "/api/stats-sample") is None
    assert route_by_path("GET", "/api/system-status").handler is http_routes.get_system_status
    assert route_by_path("GET", "/api/system-status").role == "readonly"
    assert route_by_path("GET", "/api/logs").handler is http_routes.get_server_logs
    assert route_by_path("GET", "/pane-popout").handler is http_routes.get_pane_popout
    assert http_routes.route_for_request("POST", "/api/stats-history") is None
    assert route_by_path("POST", "/api/yoagent/jobs/cancel-session").handler is http_routes.post_yoagent_jobs_cancel_session
    assert route_by_path("POST", "/api/yoagent/jobs/*/confirm").handler is http_routes.post_yoagent_job_confirm
    assert route_by_path("POST", "/api/yoagent/waits/*/clear").handler is http_routes.post_yoagent_wait_clear
    assert route_by_path("POST", "/api/fs/batch").role == "admin"
    assert route_by_path("GET", "/api/fs/fast/list").handler is http_routes.get_fs_fast_list
    assert route_by_path("GET", "/api/fs/fast/list").role == "readonly"
    assert route_by_path("POST", "/api/operations/ack").role == "readonly"
    assert route_by_path("GET", "/api/fs/watch-diff").handler is http_routes.get_fs_watch_diff
    assert route_by_path("GET", "/api/fs/zip").handler is http_routes.get_fs_zip
    assert route_by_path("GET", "/api/fs/count").handler is http_routes.get_fs_count
    assert route_by_path("GET", "/api/tmux-session-exists").role == "readonly"


def test_do_get_routes_authenticated_json_and_stream_handlers():
    app = SimpleNamespace(
        session_metadata_payload=lambda force=False: {"sessions": {}, "force": force},
        activity_summary_bytes=lambda force=False, locale="en", session_scope="configured", hours="24": (
            json.dumps({"force": force, "locale": locale}, separators=(",", ":")).encode("utf-8"),
            HTTPStatus.OK,
        ),
        activity_payload=lambda hours=24.0, visible=True: ({"hours": hours, "visible": visible}, HTTPStatus.OK),
        tmux_session_exists_payload=lambda session: ({"session": session, "exists": session == "2"}, HTTPStatus.OK),
    )

    handler, calls, writes = route_handler("/api/session-metadata?force=1", app)
    handler._api_request_id = "r-session-metadata-test"
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [(
        "json",
        HTTPStatus.OK,
        {
            "state": "ready",
            "request": {"id": "r-session-metadata-test"},
            "data": {"sessions": {}, "force": True},
        },
    )]

    handler, calls, writes = route_handler("/api/tmux-session-exists?session=2", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"session": "2", "exists": True})]

    handler, calls, writes = route_handler("/api/transcripts?force=1", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"sessions": {}, "force": True})]

    app.activity_summary_bytes = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("disabled activity-summary route must not enter app work")
    )
    handler, calls, writes = route_handler("/api/activity-summary?force=1&locale=ja", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [(
        "json_bytes",
        HTTPStatus.SERVICE_UNAVAILABLE,
        b'{"status":"feature_disabled","code":"feature_disabled","reason":"async_replacement_required","retryable":false,"terminal":true}',
    )]

    handler, calls, writes = route_handler("/api/activity?hours=0.5&visible=0", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"hours": 0.5, "visible": False})]

    app = SimpleNamespace(background_owner_status_payload=lambda: ({"status": "owner"}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/background/status", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"status": "owner"})]

    # The route reads a published snapshot and writes its bytes; it never calls a payload builder.
    system_status_body = b'{"ok":true,"server":{"pid":123}}'
    app = SimpleNamespace(system_status_snapshot_response=lambda advanced=False: (system_status_body, {"length": len(system_status_body)}))
    handler, calls, writes = route_handler("/api/system-status", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("product", HTTPStatus.OK, system_status_body)]

    app = SimpleNamespace(background_owner_claim_payload=lambda: ({"ok": True, "claimed": True, "was_owner": False}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/background/claim", app)
    handler.headers = {"Content-Length": "0"}
    Handler.do_POST(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "claimed": True, "was_owner": False})]

    app = SimpleNamespace(tmux_signals_payload=lambda force=False, session="": ({"force": force, "session": session}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/tmux-signals?force=1&session=5", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"force": True, "session": "5"})]

    app = SimpleNamespace(yoagent_skills_payload=lambda: {"skills": []})
    handler, calls, writes = route_handler("/api/yoagent/skills", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"skills": []})]

    app = SimpleNamespace(yoagent_skill_files_payload=lambda kind="", name="": ({"kind": kind, "name": name}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/skill-files?kind=skill&name=local-checks", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"kind": "skill", "name": "local-checks"})]

    app = SimpleNamespace(yoagent_conversation_payload=lambda: {"messages": []})
    handler, calls, writes = route_handler("/api/yoagent/conversation", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"messages": []})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(yoagent_jobs_payload=lambda: ({"jobs": []}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/jobs", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"jobs": []})]

    handler, calls, writes = route_handler("/api/client-events?channels=files,status&client_id=client-a&operations=op-a,op-b", app)
    handler.stream_client_events = lambda **kwargs: writes.append(("client-events", handler.path, kwargs))
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("client-events", "/api/client-events?channels=files,status&client_id=client-a&operations=op-a,op-b", {
        "channels": "files,status",
        "client_id": "client-a",
        "operation_id": "",
        "replay_operation_ids": ("op-a", "op-b"),
    })]

    app = SimpleNamespace(operation_status_payload=lambda operation_id: ({"state": "queued", "operation": {"id": operation_id}}, HTTPStatus.ACCEPTED))
    handler, calls, writes = route_handler("/api/operations/op-fixture", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.ACCEPTED, {"state": "queued", "operation": {"id": "op-fixture"}})]

    handler, calls, writes = route_handler("/api/fs/fast/list?path=/repo", app)
    handler.handle_fs_fast_list = lambda parsed: writes.append(("fs-fast-list", parsed.path))
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("fs-fast-list", "/api/fs/fast/list")]

    handler, calls, writes = route_handler("/api/summary-stream", app)
    handler.stream_codex_summary = lambda parsed: writes.append(("summary-stream", parsed.path))
    Handler.do_GET(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("summary-stream", "/api/summary-stream")]


def test_do_get_fs_routes_reject_readonly_before_file_handlers():
    handler, calls, writes = route_handler("/api/fs/list?path=/repo", readonly=True)
    handler.handle_fs_list = lambda parsed: writes.append(("fs-list", parsed.path))

    Handler.do_GET(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("forbidden", HTTPStatus.FORBIDDEN, "readonly", "admin")]

    handler, calls, writes = route_handler("/api/fs/zip?path=/repo", readonly=True)
    handler.handle_fs_zip = lambda parsed: writes.append(("fs-zip", parsed.path))

    Handler.do_GET(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("forbidden", HTTPStatus.FORBIDDEN, "readonly", "admin")]

    handler, calls, writes = route_handler("/api/fs/count?path=/repo", readonly=True)
    handler.handle_fs_count = lambda parsed: writes.append(("fs-count", parsed.path))

    Handler.do_GET(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("forbidden", HTTPStatus.FORBIDDEN, "readonly", "admin")]


def test_do_get_fs_watch_diff_uses_client_since_token_without_tracking_clients():
    requests = []
    app = SimpleNamespace(
        filesystem_watch_diff_http_payload=lambda since_token="", force_full=False, request_id="": (
            requests.append((since_token, force_full, request_id)) or {
                "since": since_token,
                "force_full": force_full,
                "request_id": request_id,
            },
            HTTPStatus.ACCEPTED,
        )
    )
    handler, calls, writes = route_handler("/api/fs/watch-diff?since=old-token", app)
    handler.api_request_id = lambda: "r-web-watch-diff"

    Handler.do_GET(handler)

    assert calls == [("require_auth", "readonly")]
    assert requests == [("old-token", False, "r-web-watch-diff")]
    assert writes == [("json", HTTPStatus.ACCEPTED, {
        "since": "old-token",
        "force_full": False,
        "request_id": "r-web-watch-diff",
    })]


def test_tmux_signal_event_watcher_is_owned_by_client_event_lifecycle():
    app_start_body = inspect.getsource(app_module.WatchBridge.start_client_event_watcher)
    app_event_body = inspect.getsource(app_module.WatchBridge.handle_tmux_signal_event)
    stream_body = inspect.getsource(server_module.Handler.stream_client_events)
    server_init_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.__init__)
    server_close_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.server_close)

    assert "app.start_tmux_signal_event_watcher()" in app_start_body
    assert "app.tmux_signal_cache.clear()" in app_event_body
    assert "TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS" in app_event_body
    assert "record.tmux_signal_refresh_at" in app_event_body
    assert "record.wake_event.set()" in app_event_body
    assert "self.server.app.start_client_event_watcher()" in stream_body
    assert "self.server.app.client_events.ready_snapshot(subscriber_id)" in stream_body
    assert "self.server.app.stop_client_event_watcher_if_idle()" in stream_body
    assert "self.app.start_client_event_watcher()" not in server_init_body
    assert "self.app.stop_client_event_watcher()" in server_close_body


def test_operation_filtered_client_event_stream_releases_under_unrelated_event_load(monkeypatch):
    broker = app_module.ClientEventBroker()
    clock = [0.0]
    consumed = {}
    disconnected = []

    def next_event(subscriber_id, timeout):
        del timeout
        consumed[subscriber_id] = consumed.get(subscriber_id, 0) + 1
        if consumed[subscriber_id] > 4:
            raise AssertionError("unrelated events starved the operation-stream liveness write")
        clock[0] += 5.0
        return {"type": "fs_changed", "payload": {"operation": {"id": "other-operation"}}}

    broker.next_event = next_event
    app = SimpleNamespace(
        client_events=broker,
        start_client_event_watcher=lambda: None,
        wake_client_event_watcher=lambda: None,
        stop_client_event_watcher_if_idle=lambda: None,
        touch_client_watch_descriptor=lambda _client_id: None,
        client_event_subscriber_disconnected=disconnected.append,
        operation_replay_payload=lambda _operation_id: None,
    )
    monkeypatch.setattr(server_module.time, "monotonic", lambda: clock[0])

    for index in range(8):
        server_connection, client_connection = socket.socketpair()
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(app=app)
        handler.connection = server_connection
        handler.send_response = lambda _status: None
        handler.send_header = lambda *_args: None
        handler.send_auth_cookie_if_needed = lambda: None
        handler.end_headers = lambda: None

        def write_sse_json(event, _payload):
            if event == "ping":
                raise BrokenPipeError("fixture client disconnected")

        handler.write_sse_json = write_sse_json
        try:
            Handler.stream_client_events(handler, client_id=f"client-{index}", operation_id=f"operation-{index}")
        finally:
            server_connection.close()
            client_connection.close()

    assert broker.snapshot()["subscribers"] == 0
    assert disconnected == [f"client-{index}" for index in range(8)]
    assert max(consumed.values()) <= 3


def test_client_event_stream_successful_terminal_write_does_not_acknowledge_browser_consumption():
    broker = app_module.ClientEventBroker()
    terminal = {
        "operation": {"id": "operation-wanted", "cursor": {"epoch": "epoch-a", "seq": 1}},
        "result": {"state": "ready", "data": {"entries": []}},
        "status": HTTPStatus.OK,
    }
    events = iter([
        {"type": "operation_terminal", "payload": {
            "operation": {"id": "operation-other", "cursor": {"epoch": "epoch-a", "seq": 1}},
            "result": {"state": "ready", "data": {"entries": ["wrong"]}},
            "status": HTTPStatus.OK,
        }},
        {"type": "operation_terminal", "payload": terminal},
        {"type": "fs_changed", "payload": {}},
    ])
    broker.next_event = lambda _subscriber_id, timeout: next(events)
    acknowledged = []
    app = SimpleNamespace(
        client_events=broker,
        start_client_event_watcher=lambda: None,
        wake_client_event_watcher=lambda: None,
        stop_client_event_watcher_if_idle=lambda: None,
        touch_client_watch_descriptor=lambda _client_id: None,
        client_event_subscriber_disconnected=lambda _client_id: None,
        operation_replay_payload=lambda _operation_id: None,
        acknowledge_operation_delivery=lambda operation_id, cursor: acknowledged.append((operation_id, cursor)),
    )
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=app)
    handler.connection = SimpleNamespace()
    handler.send_response = lambda _status: None
    handler.send_header = lambda *_args: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None
    handler.client_event_peer_disconnected = lambda: sum(event == "operation_terminal" for event, _payload in writes) >= 2
    writes = []

    def write_sse_json(event, payload):
        writes.append((event, payload))

    handler.write_sse_json = write_sse_json

    Handler.stream_client_events(
        handler,
        client_id="client-a",
        replay_operation_ids=("operation-wanted",),
    )

    assert [payload["payload"]["operation"]["id"] for event, payload in writes if event == "operation_terminal"] == [
        "operation-other",
        "operation-wanted",
    ]
    assert acknowledged == []


def test_global_client_event_stream_delivers_live_operation_terminal_without_replacement_filter():
    broker = app_module.ClientEventBroker()
    terminal = {
        "operation": {"id": "operation-live", "cursor": {"epoch": "epoch-live", "seq": 1}},
        "result": {"state": "ready", "data": {"entries": []}},
        "status": HTTPStatus.OK,
    }
    events = iter([
        {"type": "operation_terminal", "payload": terminal},
        {"type": "fs_changed", "payload": {}},
    ])
    broker.next_event = lambda _subscriber_id, timeout: next(events)
    app = SimpleNamespace(
        client_events=broker,
        start_client_event_watcher=lambda: None,
        wake_client_event_watcher=lambda: None,
        stop_client_event_watcher_if_idle=lambda: None,
        touch_client_watch_descriptor=lambda _client_id: None,
        client_event_subscriber_disconnected=lambda _client_id: None,
        operation_replay_payload=lambda _operation_id: None,
    )
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=app)
    handler.connection = SimpleNamespace()
    handler.send_response = lambda _status: None
    handler.send_header = lambda *_args: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None
    writes = []
    handler.write_sse_json = lambda event, payload: writes.append((event, payload))
    handler.client_event_peer_disconnected = lambda: any(event == "operation_terminal" for event, _payload in writes)

    Handler.stream_client_events(handler, client_id="client-live")

    assert [payload["payload"] for event, payload in writes if event == "operation_terminal"] == [terminal]


def test_client_event_stream_failed_terminal_write_keeps_exact_replay_unacknowledged():
    broker = app_module.ClientEventBroker()
    terminal = {
        "operation": {"id": "operation-replay", "cursor": {"epoch": "epoch-b", "seq": 1}},
        "result": {"state": "ready", "data": {"entries": []}},
        "status": HTTPStatus.OK,
    }
    acknowledged = []
    app = SimpleNamespace(
        client_events=broker,
        start_client_event_watcher=lambda: None,
        wake_client_event_watcher=lambda: None,
        stop_client_event_watcher_if_idle=lambda: None,
        client_event_subscriber_disconnected=lambda _client_id: None,
        operation_replay_payload=lambda _operation_id: terminal,
        acknowledge_operation_delivery=lambda operation_id, cursor: acknowledged.append((operation_id, cursor)),
    )
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=app)
    handler.send_response = lambda _status: None
    handler.send_header = lambda *_args: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None

    def fail_terminal_write(event, _payload):
        if event == "operation_terminal":
            raise BrokenPipeError("terminal frame was not written")

    handler.write_sse_json = fail_terminal_write

    Handler.stream_client_events(handler, replay_operation_ids=("operation-replay",))

    assert acknowledged == []


def test_replacement_stream_replays_large_exact_terminal_until_browser_ack(tmp_path):
    ledger = QueuedDeliveryLedger(state_path=tmp_path / "operations.json")
    terminals = []
    for suffix in ("a", "b"):
        receipt = ledger.accept_operation(
            request_id=f"r-replacement-{suffix}",
            route="GET /api/fs/watch-diff",
            deadline_at=4102444800.0,
            progress={"phase": "waiting_for_product"},
            producer={"service": "jobd", "job_id": f"job-replacement-{suffix}"},
        )
        terminal = ledger.terminalize_operation(
            receipt["operation"]["id"],
            {"state": "ready", "request": receipt["request"], "data": {"blob": suffix * (512 * 1024)}},
            HTTPStatus.OK,
        )
        terminals.append(terminal)
    broker = app_module.ClientEventBroker()
    app = SimpleNamespace(
        client_events=broker,
        start_client_event_watcher=lambda: None,
        wake_client_event_watcher=lambda: None,
        stop_client_event_watcher_if_idle=lambda: None,
        client_event_subscriber_disconnected=lambda _client_id: None,
        operation_replay_payload=ledger.operation_replay_event,
    )

    def replay(operation_ids):
        writes = []
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(app=app)
        handler.send_response = lambda _status: None
        handler.send_header = lambda *_args: None
        handler.send_auth_cookie_if_needed = lambda: None
        handler.end_headers = lambda: None
        handler.write_sse_json = lambda event, payload: writes.append((event, payload))
        handler.client_event_peer_disconnected = lambda: sum(event == "operation_terminal" for event, _payload in writes) >= len(operation_ids)
        Handler.stream_client_events(handler, replay_operation_ids=tuple(operation_ids))
        return [payload["payload"] for event, payload in writes if event == "operation_terminal"]

    first = replay([terminals[0]["operation"]["id"]])
    assert first == [terminals[0]]
    assert ledger.operation_replay_event(terminals[0]["operation"]["id"]) == terminals[0]

    replacement = replay([terminal["operation"]["id"] for terminal in terminals])
    assert replacement == terminals
    assert replacement[0]["result"]["data"]["blob"] == "a" * (512 * 1024)
    acknowledgments = [
        {"id": terminal["operation"]["id"], "cursor": terminal["operation"]["cursor"]}
        for terminal in replacement
    ]
    assert ledger.acknowledge_operation_deliveries(acknowledgments) == [item["id"] for item in acknowledgments]
    assert ledger.operation_replay_event(acknowledgments[0]["id"])["result"]["error"]["code"] == "operation_replay_evicted"


def test_operation_filtered_client_event_stream_releases_on_peer_half_close_under_unrelated_event_load():
    broker = app_module.ClientEventBroker()
    consumed = []
    disconnected = []

    def next_event(subscriber_id, timeout):
        del timeout
        consumed.append(subscriber_id)
        if len(consumed) > 4:
            raise AssertionError("peer half-close did not release the operation stream")
        return {"type": "fs_changed", "payload": {"operation": {"id": "other-operation"}}}

    broker.next_event = next_event
    app = SimpleNamespace(
        client_events=broker,
        start_client_event_watcher=lambda: None,
        wake_client_event_watcher=lambda: None,
        stop_client_event_watcher_if_idle=lambda: None,
        touch_client_watch_descriptor=lambda _client_id: None,
        client_event_subscriber_disconnected=disconnected.append,
        operation_replay_payload=lambda _operation_id: None,
    )

    server_connection, client_connection = socket.socketpair()
    try:
        client_connection.shutdown(socket.SHUT_WR)
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(app=app)
        handler.connection = server_connection
        handler.send_response = lambda _status: None
        handler.send_header = lambda *_args: None
        handler.send_auth_cookie_if_needed = lambda: None
        handler.end_headers = lambda: None
        # TCP permits writes after the peer's read-side FIN. The stream must
        # inspect the socket instead of treating a successful ping as liveness.
        handler.write_sse_json = lambda *_args: None

        Handler.stream_client_events(handler, client_id="half-closed-client", operation_id="wanted-operation")
    finally:
        server_connection.close()
        client_connection.close()

    assert broker.snapshot()["subscribers"] == 0
    assert disconnected == ["half-closed-client"]
    assert len(consumed) <= 1


@pytest.mark.parametrize("payload", [
    {},
    {"acks": []},
    {"acks": [{"id": "", "cursor": {"epoch": "epoch", "seq": 1}}]},
    {"acks": [{"id": "op-a", "cursor": {"epoch": "", "seq": 1}}]},
    {"acks": [{"id": "op-a", "cursor": {"epoch": "epoch", "seq": True}}]},
    {"acks": [{"id": "op-a", "cursor": {"epoch": "epoch", "seq": 0}}]},
    {"acks": [
        {"id": "op-a", "cursor": {"epoch": "epoch", "seq": 1}},
        {"id": "op-a", "cursor": {"epoch": "epoch", "seq": 1}},
    ]},
])
def test_operation_acknowledgment_route_rejects_invalid_batches(payload):
    writes = []
    request = SimpleNamespace(
        read_json_body=lambda _limit: payload,
        write_json=lambda value, status=HTTPStatus.OK: writes.append((value, status)),
        server=SimpleNamespace(app=SimpleNamespace(
            acknowledge_operation_deliveries=lambda _items: pytest.fail("invalid acknowledgments reached the app"),
        )),
    )

    http_routes.post_operation_acknowledgments(
        request,
        urlparse("/api/operations/ack"),
        route_by_path("POST", "/api/operations/ack"),
    )

    assert writes[0][1] == HTTPStatus.BAD_REQUEST


def test_server_bind_failure_preserves_original_os_error():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        with pytest.raises(OSError) as info:
            server_module.TmuxWebtermHTTPServer(("127.0.0.1", port), object())

    assert info.value.errno == errno.EADDRINUSE


def test_do_post_routes_event_with_readonly_auth_and_fs_handlers():
    handler, calls, writes = route_handler("/api/event")
    handler.handle_client_event = lambda: ({"ok": True}, HTTPStatus.ACCEPTED)

    Handler.do_POST(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.ACCEPTED, {"ok": True})]

    handler, calls, writes = route_handler("/api/upload?session=1&editor_path=%2Frepo%2Fdocs%2Fnote.md")
    handler.handle_upload = lambda session, **kwargs: ({"session": session, **kwargs}, HTTPStatus.CREATED)

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.CREATED, {"session": "1", "editor_path": "/repo/docs/note.md", "base_dir": ""})]

    handler, calls, writes = route_handler("/api/fs/delete")
    handler.handle_fs_delete = lambda parsed: writes.append(("fs-delete", parsed.path))

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("fs-delete", "/api/fs/delete")]

    handler, calls, writes = route_handler("/api/fs/batch")
    handler.handle_fs_batch = lambda parsed: writes.append(("fs-batch", parsed.path))

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("fs-batch", "/api/fs/batch")]

    app = SimpleNamespace(update_client_watch_roots=lambda roots: {"ok": True, "roots": roots})
    handler, calls, writes = route_handler("/api/watch/roots", app)
    handler.read_json_body = lambda limit: {"roots": ["/repo"]}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "roots": {"roots": ["/repo"]}})]

    validation_error = getattr(app_module, "ClientWatchRootValidationError", ValueError)
    app = SimpleNamespace(update_client_watch_roots=lambda _roots: (_ for _ in ()).throw(validation_error("invalid root surfaces")))
    handler, calls, writes = route_handler("/api/watch/roots", app)
    handler.read_json_body = lambda limit: {"roots": ["/repo"], "root_surfaces_version": 1, "root_surfaces": []}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes[0][0:2] == ("json", HTTPStatus.BAD_REQUEST)
    assert writes[0][2]["state"] == "failed"
    assert writes[0][2]["error"]["code"] == "invalid_request"
    assert writes[0][2]["error"]["stack"] == [{
        "component": "server.http",
        "operation": "POST /api/watch/roots",
        "code": "invalid_request",
    }]

    app = SimpleNamespace(run_file_drop_action=lambda payload: ({"ok": True, "action": payload["action"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/drop-action/run", app)
    handler.read_json_body = lambda limit: {"action": "server-info", "paths": ["/repo/README.md"]}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "action": "server-info"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(preview_yoagent_send_action=lambda payload: ({"ok": True, "preview": payload["session"]}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/actions/preview-send", app)
    handler.read_json_body = lambda limit: {"session": "6", "text": "date"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "preview": "6"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(execute_yoagent_send_action=lambda payload: ({"ok": True, "preview_id": payload["preview_id"]}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/actions/execute-send", app)
    handler.read_json_body = lambda limit: {"preview_id": "ya_1"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "preview_id": "ya_1"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(yoagent_intent=lambda payload: ({"ok": True, "intent": payload["type"]}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/intent", app)
    handler.read_json_body = lambda limit: {"type": "notify_session_idle"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "intent": "notify_session_idle"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(create_yoagent_job=lambda payload: ({"ok": True, "job": payload["type"]}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/jobs", app)
    handler.read_json_body = lambda limit: {"type": "notify_session_idle"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "job": "notify_session_idle"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(confirm_yoagent_job=lambda job_id: ({"ok": True, "id": job_id}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/jobs/yj_1/confirm", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "id": "yj_1"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(cancel_yoagent_jobs_for_session=lambda session: ({"ok": True, "session": session, "count": 2}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/jobs/cancel-session", app)
    handler.read_json_body = lambda limit: {"session": "6"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "session": "6", "count": 2})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(cancel_yoagent_job=lambda job_id: ({"ok": True, "id": job_id}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/jobs/yj_1/cancel", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "id": "yj_1"})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(cancel_yoagent_chat=lambda request_id: ({"ok": True, "request_id": request_id, "cancelled": True}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/chat/chat-abc/cancel", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "request_id": "chat-abc", "cancelled": True})]

    app = SimpleNamespace(yoagent_controller=SimpleNamespace(clear_yoagent_action_wait=lambda wait_id: ({"ok": True, "id": wait_id}, HTTPStatus.OK)))
    handler, calls, writes = route_handler("/api/yoagent/waits/yw_1/clear", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "id": "yw_1"})]

    app = SimpleNamespace(upsert_yoagent_skill_file=lambda payload: ({"ok": True, "name": payload["name"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/skill-files/upsert", app)
    handler.read_json_body = lambda limit: {"kind": "skill", "name": "local-checks", "text": "name: local-checks\n"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "name": "local-checks"})]

    app = SimpleNamespace(delete_yoagent_skill_file=lambda payload: ({"ok": True, "name": payload["name"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/skill-files/delete", app)
    handler.read_json_body = lambda limit: {"kind": "skill", "name": "local-checks"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "name": "local-checks"})]

    app = SimpleNamespace(tmux_copy_selection=lambda session: ({"session": session, "copied": True}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/tmux-copy-selection?session=6", app)

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"session": "6", "copied": True})]


def batch_handler(payload, app=None):
    body = json.dumps(payload).encode("utf-8")
    writes = []
    handler = object.__new__(Handler)
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    if app is not None:
        handler.server = SimpleNamespace(app=app)
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append((status, value))
    return handler, writes


def test_handle_fs_fast_list_returns_one_level_without_jobd_or_git(monkeypatch, tmp_path):
    (tmp_path / "first").mkdir()
    (tmp_path / "first" / "nested.txt").write_text("nested\n", encoding="utf-8")
    (tmp_path / "top.txt").write_text("top\n", encoding="utf-8")
    real_list_directory = server_module.filesystem.list_directory
    calls = []

    def direct_list(path, *, include_repo_info=True):
        calls.append((path, include_repo_info))
        return real_list_directory(path, include_repo_info=include_repo_info)

    monkeypatch.setattr(server_module.filesystem, "list_directory", direct_list)
    handler, _auth_calls, writes = route_handler(
        f"/api/fs/fast/list?path={quote(str(tmp_path), safe='')}",
        SimpleNamespace(),
    )

    Handler.handle_fs_fast_list(handler, urlparse(handler.path))

    assert calls == [(str(tmp_path), False)]
    assert len(writes) == 1
    assert writes[0][0:2] == ("json", HTTPStatus.OK)
    payload = writes[0][2]
    assert {entry["name"] for entry in payload["entries"]} == {"first", "top.txt"}
    assert all(entry["name"] != "nested.txt" for entry in payload["entries"])


def test_handle_fs_list_preserves_legacy_jobd_request_without_repo_opt_out():
    calls = []
    handler = object.__new__(Handler)
    handler.submit_filesystem_operation = lambda *args: calls.append(args)

    Handler.handle_fs_list(handler, urlparse("/api/fs/list?path=%2Frepo"))

    assert calls == [("GET /api/fs/list", "list", "/repo")]


def test_handle_fs_git_history_and_commit_validate_and_submit_retained_reads():
    calls = []
    writes = []
    handler = object.__new__(Handler)
    handler.submit_filesystem_operation = lambda *args: calls.append(args)
    handler.write_json = lambda payload, status: writes.append((status, payload))

    Handler.handle_fs_git_history(
        handler,
        urlparse("/api/fs/git-history?path=%2Frepo%2Fsrc&limit=12&cursor=opaque-cursor"),
    )
    Handler.handle_fs_git_commit(
        handler,
        urlparse(f"/api/fs/git-commit?path=%2Frepo%2Fsrc&commit={'a' * 40}&head={'b' * 40}"),
    )

    assert calls == [
        ("GET /api/fs/git-history", "git_history", "/repo/src", {"limit": 12, "cursor": "opaque-cursor"}),
        ("GET /api/fs/git-commit", "git_commit", "/repo/src", {"commit": "a" * 40, "head": "b" * 40}),
    ]
    assert writes == []

    for raw_limit in ("0", "-999"):
        Handler.handle_fs_git_history(
            handler,
            urlparse(f"/api/fs/git-history?path=%2Frepo&limit={raw_limit}"),
        )
        assert calls[-1] == ("GET /api/fs/git-history", "git_history", "/repo", {"limit": 1, "cursor": ""})

    Handler.handle_fs_git_history(handler, urlparse("/api/fs/git-history?path=%2Frepo&limit=many"))
    assert len(writes) == 1
    assert writes[0][0] == HTTPStatus.BAD_REQUEST
    assert len(calls) == 4


def test_git_history_routes_are_registered_as_readonly_json():
    routes = {(route.method, route.path): route for route in http_routes.ALL_ROUTES}

    for path, handler_name in (
        ("/api/fs/git-history", "get_fs_git_history"),
        ("/api/fs/git-commit", "get_fs_git_commit"),
    ):
        route = routes[("GET", path)]
        assert route.role == "readonly"
        assert route.protocol == http_routes.RESPONSE_JSON
        assert route.handler.__name__ == handler_name


@pytest.mark.parametrize(
    ("args", "expected_include_repo_info"),
    [({}, True), ({"include_repo_info": False}, False)],
)
def test_jobd_list_preserves_repo_metadata_by_default_and_allows_explicit_opt_out(
    monkeypatch,
    args,
    expected_include_repo_info,
):
    calls = []

    def list_directory(path, *, include_repo_info=True):
        calls.append((path, include_repo_info))
        return {"path": path, "include_repo_info": include_repo_info}

    monkeypatch.setattr(jobd.filesystem, "list_directory", list_directory)

    descriptor = app_module.filesystem_operation_descriptor("list", "/repo", args=args)
    result = jobd._filesystem_operation_authorized(descriptor)

    assert calls == [("/repo", expected_include_repo_info)]
    assert json.loads(result) == {"path": "/repo", "include_repo_info": expected_include_repo_info}


def test_handle_fs_batch_submits_one_product_without_request_thread_filesystem(monkeypatch):
    calls = []
    receipt = {
        "state": "queued",
        "request": {"id": "r-batch"},
        "operation": {
            "id": "op-batch",
            "kind": "fs_batch",
            "status_url": "/api/operations/op-batch",
            "events_url": "/api/client-events?operation_id=op-batch",
            "cursor": {"epoch": "epoch", "seq": 0},
        },
    }
    app = SimpleNamespace(
        fs_batch_http_payload=lambda payload, **kwargs: calls.append((payload, kwargs)) or (receipt, HTTPStatus.ACCEPTED),
    )
    handler, writes = batch_handler({
        "requests": [
            {"id": "list", "type": "list", "path": "/repo", "trigger_counts": {"tree-render": 1}},
            {"id": "info", "type": "info", "path": "/repo", "trigger_counts": {"tree-render": 1}},
        ],
        "client_scope": "browser",
    }, app=app)
    monkeypatch.setattr(
        server_module.filesystem,
        "list_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP handler must not list")),
    )
    monkeypatch.setattr(
        server_module.filesystem,
        "path_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP handler must not stat")),
    )

    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes == [(HTTPStatus.ACCEPTED, receipt)]
    assert calls == [({
        "requests": [
            {"id": "list", "type": "list", "path": "/repo", "trigger_counts": {"tree-render": 1}},
            {"id": "info", "type": "info", "path": "/repo", "trigger_counts": {"tree-render": 1}},
        ],
        "client_scope": "browser",
    }, {})]


def test_filesystem_batch_product_returns_per_item_results(monkeypatch):
    monkeypatch.setattr(
        server_module.filesystem,
        "list_directory",
        lambda path, *, performance_details=None, watch_signature_child_limit=0, include_repo_info=True: {"path": path, "entries": [{"name": "a"}]},
    )

    def path_info(path, *, operation, repo_info_cache=None):
        assert repo_info_cache == {}
        assert operation == "fs_batch.info"
        if path == "/missing":
            raise FilesystemError.path_not_found("/missing")
        return {"path": path, "kind": "dir"}

    monkeypatch.setattr(server_module.filesystem, "path_info", path_info)
    result = server_module.filesystem.filesystem_batch_result({
        "requests": [
            {"id": "root", "type": "list", "path": "/repo"},
            {"id": "info", "type": "info", "path": "/repo"},
            {"id": "missing", "type": "info", "path": "/missing"},
            {"id": "bad", "type": "read", "path": "/repo/README.md"},
            {"id": "write", "type": "write", "path": "/repo/README.md"},
            {"id": "delete", "type": "delete", "path": "/repo/README.md"},
            {"id": "rename", "type": "rename", "path": "/repo/README.md"},
            {"id": "mkdir", "type": "mkdir", "path": "/repo/new"},
            {"id": "unindex", "type": "unindex", "path": "/repo"},
        ],
        server_module.filesystem.FS_ACCESS_POLICY_FIELD: server_module.filesystem.access_policy_descriptor(),
    })

    responses = result["responses"]
    assert responses[:2] == [
        {"id": "root", "ok": True, "status": 200, "payload": {"path": "/repo", "entries": [{"name": "a"}]}},
        {"id": "info", "ok": True, "status": 200, "payload": {"path": "/repo", "kind": "dir"}},
    ]
    assert responses[2]["user_message"] == {
        "key": "common.pathNotFound",
        "params": {"path": "/missing"},
        "fallback": "path not found: /missing",
    }
    assert all(
        response["user_message"]["key"] == "request.error.unsupportedFsBatchOperation"
        for response in responses[3:]
    )


@pytest.mark.parametrize(
    ("request_options", "expected_include_repo_info"),
    [({}, True), ({"include_repo_info": False}, False)],
)
def test_filesystem_batch_list_preserves_repo_metadata_by_default_and_allows_explicit_opt_out(
    monkeypatch,
    request_options,
    expected_include_repo_info,
):
    calls = []

    def list_directory(path, *, performance_details=None, watch_signature_child_limit=0, include_repo_info=True):
        calls.append((path, include_repo_info))
        return {"path": path, "entries": []}

    monkeypatch.setattr(server_module.filesystem, "list_directory", list_directory)
    result = server_module.filesystem.filesystem_batch_result({
        "requests": [{"id": "root", "type": "list", "path": "/repo", **request_options}],
        server_module.filesystem.FS_ACCESS_POLICY_FIELD: server_module.filesystem.access_policy_descriptor(),
    })

    assert result["responses"][0]["ok"] is True
    assert calls == [("/repo", expected_include_repo_info)]


def test_filesystem_batch_product_returns_typed_permission_failure_without_raising(monkeypatch):
    def denied_path_info(_raw_path, *, operation, repo_info_cache=None):
        assert repo_info_cache == {}
        assert operation == "fs_batch.info"
        raise PermissionError(13, "permission denied", "/restricted/item")

    monkeypatch.setattr(server_module.filesystem.io_ops, "path_info", denied_path_info)
    result = server_module.filesystem.filesystem_batch_result({
        "requests": [{"id": "denied", "type": "info", "path": "/restricted/item"}],
        server_module.filesystem.FS_ACCESS_POLICY_FIELD: server_module.filesystem.access_policy_descriptor(),
    })

    assert result["responses"] == [{
        "id": "denied",
        "ok": False,
        "path": "/restricted/item",
        "status": 403,
        "error": "filesystem operation failed",
        "user_message": {
            "key": "fs.error.operationFailed",
            "params": {},
            "fallback": "filesystem operation failed",
        },
        "diagnostic": "[Errno 13] permission denied: '/restricted/item'",
    }]


def test_handle_fs_batch_sets_one_privacy_safe_endpoint_record():
    path_canary = "/repo/private-7f1c8b4d-credential.txt"
    receipt = {
        "state": "queued",
        "request": {"id": "r-batch"},
        "operation": {"id": "op-batch", "kind": "fs_batch"},
    }
    app = SimpleNamespace(fs_batch_http_payload=lambda payload, **kwargs: (receipt, HTTPStatus.ACCEPTED))
    handler, writes = batch_handler({
        "client_revision": "1234-5678",
        "client_scope": "browser",
        "requests": [{"id": "root", "type": "list", "path": path_canary, "trigger": "watch-diff-fallback"}],
    }, app=app)
    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes == [(HTTPStatus.ACCEPTED, receipt)]
    assert handler._http_response_compute_ms >= 0
    expected_details = {
        "fs_batch": True,
        "fs_batch_offloaded": True,
        "fs_batch_size": 1,
        "fs_batch_body_read_ms": pytest.approx(handler._http_response_compute_ms, abs=10),
        "fs_batch_operation_ms": pytest.approx(0, abs=10),
        "fs_batch_operations": '{"list": 1}',
        "fs_batch_path_hashes": f'["{server_module.hashlib.sha256(path_canary.encode()).hexdigest()[:16]}"]',
        "fs_batch_triggers": '{"watch-diff-fallback": 1}',
        "fs_batch_client_revision": "1234-5678",
        "fs_batch_client_scope": "browser",
        "fs_batch_list_ms": 0.0,
        "fs_batch_info_ms": 0.0,
    }
    assert handler._http_response_performance_details == expected_details
    records = []
    handler.command = "POST"
    handler.path = "/api/fs/batch"
    handler.server = SimpleNamespace(app=SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs))))
    Handler.record_http_response_bytes(handler, HTTPStatus.ACCEPTED, 123, "application/json")
    assert records[0][0] == ("http-endpoint", "POST /api/fs/batch")
    assert records[0][1]["payload_bytes"] == 123
    assert records[0][1]["compute_ms"] == pytest.approx(handler._http_response_compute_ms)
    request_id = records[0][1]["details"]["request_id"]
    transport_request_id = records[0][1]["details"]["transport_request_id"]
    assert request_id.startswith("r-")
    assert transport_request_id.startswith("r-")
    assert records[0][1]["details"] == {
        "method": "POST",
        "path": "/api/fs/batch",
        "status": HTTPStatus.ACCEPTED,
        "content_type": "application/json",
        "request_id": request_id,
        "transport_request_id": transport_request_id,
        **expected_details,
    }


def test_filesystem_batch_product_preserves_bounded_coalesced_trigger_counts(monkeypatch):
    monkeypatch.setattr(
        server_module.filesystem,
        "list_directory",
        lambda path, *, performance_details=None, watch_signature_child_limit=0, include_repo_info=True: {"path": path, "entries": []},
    )
    result = server_module.filesystem.filesystem_batch_result({
        "requests": [{"id": "root", "type": "list", "path": "/repo", "trigger_counts": {"tree-render": 2, "watch-diff-fallback": 3}}],
        server_module.filesystem.FS_ACCESS_POLICY_FIELD: server_module.filesystem.access_policy_descriptor(),
    })

    assert result["responses"][0]["ok"] is True
    assert result["performance"]["triggers"] == {"tree-render": 2, "watch-diff-fallback": 3}
    assert result["performance"]["client_revision"] == "unknown"
    assert result["performance"]["client_scope"] == "legacy"


def test_filesystem_batch_accepts_deferred_repo_enrichment_info(monkeypatch):
    repo = {
        "root": "/repo",
        "name": "repo",
        "branch": "feature/backfill",
        "dirty_count": 2,
        "upstream": "origin/main",
        "ahead": 1,
        "behind": 0,
    }
    monkeypatch.setattr(
        server_module.filesystem,
        "path_info",
        lambda path, *, operation, repo_info_cache=None: {"path": path, "kind": "dir", "repo": repo},
    )

    result = server_module.filesystem.filesystem_batch_result({
        "requests": [{
            "id": "repo",
            "type": "info",
            "path": "/repo",
            "trigger_counts": {"repo-enrichment": 1},
        }],
        server_module.filesystem.FS_ACCESS_POLICY_FIELD: server_module.filesystem.access_policy_descriptor(),
    })

    assert result["responses"] == [{
        "id": "repo",
        "ok": True,
        "status": HTTPStatus.OK,
        "payload": {"path": "/repo", "kind": "dir", "repo": repo},
    }]
    assert result["performance"]["operations"] == {"info": 1}
    assert result["performance"]["triggers"] == {"repo-enrichment": 1}


def test_filesystem_batch_product_rejects_arbitrary_trigger_without_recording_it(monkeypatch):
    monkeypatch.setattr(server_module.filesystem, "list_directory", lambda path: {"path": path, "entries": []})
    path_canary = "/repo/private-42d9a7c1"
    trigger_canary = "secret=do-not-log-5b3e1c8f"
    result = server_module.filesystem.filesystem_batch_result({
        "client_revision": trigger_canary,
        "client_scope": trigger_canary,
        "password": "do-not-log-this-body-8e2f6a4d",
        "requests": [{"id": "root", "type": "list", "path": path_canary, "trigger": trigger_canary}],
        server_module.filesystem.FS_ACCESS_POLICY_FIELD: server_module.filesystem.access_policy_descriptor(),
    })

    assert result["responses"] == [{
        "id": "root",
        "ok": False,
        "status": HTTPStatus.BAD_REQUEST,
        "path": path_canary,
        "error": "invalid fs batch trigger",
        "user_message": {
            "key": "request.error.unsupportedFsBatchOperation",
            "params": {"operation": "trigger"},
            "fallback": "invalid fs batch trigger",
        },
    }]
    assert result["performance"]["operations"] == {"list": 1}
    assert result["performance"]["path_fingerprints"] == []
    assert result["performance"]["triggers"] == {"invalid": 1}
    assert result["performance"]["client_revision"] == "unknown"
    assert result["performance"]["client_scope"] == "legacy"


def test_handle_fs_batch_rejects_invalid_shape():
    """The handler owns no batch rule of its own; it renders the app's one typed rejection.

    The rejection is canonical, so it carries the causal frame the API response parent requires.
    Without that frame the parent rejects its own 400 and route dispatch emits an internal 500.
    """

    webapp = app_module.TmuxWebtermApp([])
    handler, writes = batch_handler({"requests": "nope"}, app=webapp)

    try:
        Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    status, payload = writes[0]
    assert status == HTTPStatus.BAD_REQUEST
    assert payload["state"] == "failed"
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == {
        "key": "request.error.list",
        "params": {"field": "requests"},
        "fallback": "requests must be a list",
    }
    assert payload["error"]["stack"] == [{
        "component": "server.http",
        "operation": "POST /api/fs/batch",
        "code": "invalid_request",
    }]


def test_handle_ws_payload_readonly_discards_input_and_scroll(monkeypatch):
    writes = []
    scrolls = []
    process = SimpleNamespace(pid=123)
    handler = SimpleNamespace(server=SimpleNamespace(app=SimpleNamespace(tmux_scroll=lambda *args: scrolls.append(args))))
    monkeypatch.setattr(server_module.os, "write", lambda fd, data: writes.append((fd, data)))

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "input", "data": "ls\n"}).encode(), readonly=True)
    Handler.handle_ws_payload(handler, "6", 10, 11, process, b"raw-bytes", readonly=True)
    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "tmux-scroll", "direction": "up", "lines": 5}).encode(), readonly=True)

    assert writes == []
    assert scrolls == []


def test_handle_ws_payload_refreshes_tmux_session_even_when_readonly(monkeypatch):
    refreshes = []
    process = SimpleNamespace(pid=123)
    handler = SimpleNamespace(server=SimpleNamespace(app=SimpleNamespace(tmux_scroll=lambda *_args: None)))
    monkeypatch.setattr(server_module, "refresh_tmux_session_clients", lambda session: refreshes.append(session) or True)

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "refresh", "reason": "blank-screen"}).encode(), readonly=True)

    assert refreshes == ["6"]


def test_handle_ws_payload_refresh_with_transaction_id_acks_as_text_frame(monkeypatch):
    refreshes = []
    sent_frames = []
    process = SimpleNamespace(pid=123)
    handler = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(tmux_scroll=lambda *_args: None)),
        connection=SimpleNamespace(sendall=lambda data: sent_frames.append(data)),
    )
    monkeypatch.setattr(server_module, "refresh_tmux_session_clients", lambda session: refreshes.append(session) or True)

    # Legacy refresh without an id keeps the silent behavior (no control frame back).
    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "refresh", "reason": "blank-screen"}).encode(), readonly=False)
    assert refreshes == ["6"]
    assert sent_frames == []

    # A refresh carrying the switch transaction id is acknowledged with a structured TEXT frame
    # (opcode 1) after issuing the refresh, while PTY output stays on binary frames.
    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "refresh", "reason": "tmux-window-switch", "txn": 7}).encode(), readonly=False)
    assert refreshes == ["6", "6"]
    assert len(sent_frames) == 1
    frame = sent_frames[0]
    assert frame[0] & 0x0F == 1, "refresh acknowledgement must be a websocket text frame"
    payload_length = frame[1] & 0x7F
    assert payload_length < 126
    message = json.loads(frame[2:2 + payload_length].decode("utf-8"))
    assert message == {"type": "refresh-ack", "txn": 7}

    # Readonly bridges may also refresh; the acknowledgement still flows back.
    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "refresh", "txn": 9}).encode(), readonly=True)
    assert len(sent_frames) == 2
    assert json.loads(sent_frames[1][2:].decode("utf-8"))["txn"] == 9

    # Malformed ids never crash the bridge and never emit a bogus acknowledgement.
    for bad_txn in (0, -3, "seven", None, True):
        Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "refresh", "txn": bad_txn}).encode(), readonly=False)
    assert len(sent_frames) == 2


def test_handle_ws_payload_resize_sets_pty_and_signals_for_admin_only(monkeypatch):
    calls = []
    process = SimpleNamespace(pid=123, poll=lambda: None)
    _record_fixture_process_group(monkeypatch, process)
    handler = SimpleNamespace(server=SimpleNamespace(app=SimpleNamespace(tmux_scroll=lambda *_args: None)))
    monkeypatch.setattr(server_module, "set_pty_size", lambda fd, rows, cols: calls.append(("size", fd, rows, cols)))
    monkeypatch.setattr(server_module.os, "killpg", lambda pid, sig: calls.append(("signal", pid, sig)))

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "resize", "rows": 24, "cols": 80}).encode(), readonly=False)

    assert calls == [("size", 11, 24, 80), ("signal", 123, server_module.signal.SIGWINCH)]
    calls.clear()

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "resize", "rows": 12, "cols": 40, "foreground": False}).encode(), readonly=False)

    assert calls == []

    dead_process = SimpleNamespace(pid=456, poll=lambda: 0)
    Handler.handle_ws_payload(handler, "6", 10, 11, dead_process, json.dumps({"type": "resize", "rows": 31, "cols": 101}).encode(), readonly=False)

    assert calls == [("size", 11, 31, 101)]
    calls.clear()

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "resize", "rows": 30, "cols": 100}).encode(), readonly=True)

    assert calls == []


def test_write_sse_json_formats_event_stream():
    handler = SimpleNamespace(wfile=io.BytesIO())

    Handler.write_sse_json(handler, "delta", {"text": "hello"})

    assert handler.wfile.getvalue() == b'event: delta\ndata: {"text": "hello"}\n\n'


def test_stream_codex_summary_uses_settings_and_raw_auth_status(monkeypatch):
    writes = []
    responses = []
    headers = []
    logs = []
    calls = []
    summary_settings = {
        "backend": "codex",
        "codex_model": "gpt-5.4-mini",
        "codex_effort": "high",
        "codex_service_tier": "fast",
        "lookback_seconds": 7200,
        "timeout_seconds": 42,
    }

    app = SimpleNamespace(
        summary_settings=lambda: dict(summary_settings),
        require_known_session=lambda _session: None,
        codex_summary_prompt=lambda session, lookback: calls.append(("prompt", session, lookback)) or ({"session": session, "path": "/tmp/codex.jsonl", "prompt": "summarize", "items": 2}, HTTPStatus.OK),
        log_event=lambda *args, **kwargs: logs.append((args, kwargs)),
    )
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=app)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: responses.append(status)
    handler.send_header = lambda name, value: headers.append((name, value))
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
    handler.run_codex_summary = lambda prompt, settings: calls.append(("run", prompt, dict(settings)))
    monkeypatch.setattr(server_module, "agent_auth_status", lambda: {"codex": {"installed": True, "logged_in": True}})

    Handler.stream_codex_summary(handler, SimpleNamespace(query="session=5"))

    assert writes == []
    assert responses == [HTTPStatus.OK]
    assert ("Content-Type", "text/event-stream; charset=utf-8") in headers
    assert calls == [
        ("prompt", "5", 7200),
        ("run", "summarize", summary_settings),
    ]
    stream = handler.wfile.getvalue().decode("utf-8")
    assert '"summary_model": "gpt-5.4-mini"' in stream
    assert '"summary_effort": "high"' in stream
    assert '"summary_service_tier": "fast"' in stream
    assert logs[0][0][1] == "summary_started"
    assert logs[0][0][3] == {"lookback_seconds": 7200, "model": "gpt-5.4-mini"}
    assert logs[0][1] == {"message_key": "events.message.summary.started"}
    assert logs[1][0][1] == "summary_finished"
    assert logs[1][1] == {"message_key": "events.message.summary.finished"}


def test_stream_codex_summary_rejects_logged_out_codex_before_prompt(monkeypatch):
    writes = []
    app = SimpleNamespace(
        summary_settings=lambda: {
            "backend": "codex",
            "codex_model": "gpt-5.4-mini",
            "codex_effort": "low",
            "codex_service_tier": "fast",
            "lookback_seconds": 3600,
            "timeout_seconds": 600,
        },
        require_known_session=lambda _session: None,
        codex_summary_prompt=lambda *_args: (_ for _ in ()).throw(AssertionError("summary prompt should not be built when Codex is unavailable")),
    )
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=app)
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
    monkeypatch.setattr(server_module, "agent_auth_status", lambda: {"codex": {"installed": True, "logged_in": False}})

    Handler.stream_codex_summary(handler, SimpleNamespace(query="session=5"))

    assert writes == [(
        "json",
        HTTPStatus.SERVICE_UNAVAILABLE,
        {
            "error": "Codex summary provider is unavailable because the codex CLI is not logged in. Run `codex login`.",
            "user_message": {
                "key": "summary.error.codexLoginRequired",
                "params": {"command": "codex login"},
                "fallback": "Codex summary provider is unavailable because the codex CLI is not logged in. Run `codex login`.",
            },
            "provider": "codex",
            "login_command": "codex login",
        },
    )]


def test_codex_summary_allows_unknown_auth_state(monkeypatch):
    handler = object.__new__(Handler)
    monkeypatch.setattr(server_module, "agent_auth_status", lambda: {
        "codex": {"installed": True, "logged_in": None, "unavailable_reason": "auth-unknown"},
    })

    assert Handler.codex_summary_availability_error(handler, {"backend": "codex"}) is None


def test_stream_codex_summary_rejects_unknown_session_before_provider_availability(monkeypatch):
    writes = []
    diagnostic = "unknown session: missing"
    app = SimpleNamespace(
        summary_settings=lambda: {"lookback_seconds": 3600},
        require_known_session=lambda session: (
            {"error": diagnostic, "user_message": {"key": "status.sessionEnded", "params": {"session": session}, "fallback": diagnostic}},
            HTTPStatus.NOT_FOUND,
        ),
    )
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=app)
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
    monkeypatch.setattr(
        Handler,
        "codex_summary_availability_error",
        lambda *_args: (_ for _ in ()).throw(AssertionError("provider availability should not mask an unknown session")),
    )

    Handler.stream_codex_summary(handler, SimpleNamespace(query="session=missing"))

    assert writes == [(
        "json",
        HTTPStatus.NOT_FOUND,
        {"error": diagnostic, "user_message": {"key": "status.sessionEnded", "params": {"session": "missing"}, "fallback": diagnostic}},
    )]


def test_stream_codex_process_missing_stdout_has_localizable_error_descriptor():
    events = []
    handler = object.__new__(Handler)
    handler.write_sse_json = lambda event, value: events.append((event, value))

    Handler.stream_codex_process(handler, SimpleNamespace(stdout=None))

    assert events == [(
        "summary_error",
        {
            "error": "missing Codex stdout",
            "user_message": {
                "key": "summary.error.missingStdout",
                "params": {},
                "fallback": "missing Codex stdout",
            },
        },
    )]


def test_write_codex_summary_error_event_has_localizable_descriptor():
    events = []
    handler = object.__new__(Handler)
    handler.write_sse_json = lambda event, value: events.append((event, value))
    provider_event = {"type": "turn.failed", "message": "provider failed"}
    diagnostic = json.dumps(provider_event, ensure_ascii=False)

    Handler.write_codex_summary_line(handler, diagnostic)

    assert events == [(
        "summary_error",
        {
            "error": diagnostic,
            "user_message": {
                "key": "summary.stream.failed",
                "params": {},
                "fallback": diagnostic,
            },
        },
    )]


def test_codex_summary_completed_usage_submits_structured_cost_atoms_without_emitting_text():
    submitted = []
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(app=SimpleNamespace(record_owned_usage_atoms=lambda **kwargs: submitted.append(kwargs) or True))
    handler._codex_summary_usage_context = {"model": "gpt-5.6", "effort": "high", "service_tier": "flex"}
    handler.write_sse_json = lambda *_args: pytest.fail("completed usage must not be rendered as summary text")

    Handler.write_codex_summary_line(handler, json.dumps({
        "type": "turn.completed", "turn_id": "turn-1", "usage": {"input_tokens": 12, "cached_input_tokens": 4, "output_tokens": 7},
    }))

    assert submitted == [{
        "provider": "openai", "model": "gpt-5.6", "usage": {"input_tokens": 12, "cached_input_tokens": 4, "output_tokens": 7},
        "source": "AI Summary", "event_id": submitted[0]["event_id"], "effort": "high", "service_tier": "flex", "endpoint": "codex-exec",
    }]
    assert submitted[0]["event_id"].startswith("ai-summary:turn-1:")


def test_run_codex_summary_uses_configured_model_effort_service_tier_and_timeout(monkeypatch):
    calls = []
    stream_calls = []

    class FakeStdin:
        def __init__(self):
            self.data = b""
            self.closed = False

        def write(self, data):
            self.data += data

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = io.BytesIO()
            self.pid = 123

        def poll(self):
            return 0

    fake_process = FakeProcess()

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return fake_process

    handler = object.__new__(Handler)
    handler.write_sse_json = lambda event, value: stream_calls.append((event, value))
    handler.stream_codex_process = lambda process, timeout_seconds=None: stream_calls.append(("stream", process, timeout_seconds))
    monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_module, "terminate_process_group", lambda process: stream_calls.append(("terminated", process)))

    Handler.run_codex_summary(handler, "summarize", {
        "codex_model": "gpt-5.4-mini",
        "codex_effort": "xhigh",
        "codex_service_tier": "fast",
        "timeout_seconds": 42,
    })

    assert fake_process.stdin.data == b"summarize"
    assert fake_process.stdin.closed is True
    args, kwargs = calls[0]
    assert args[:3] == ["codex", "exec", "--json"]
    assert args[args.index("-m") + 1] == "gpt-5.4-mini"
    assert 'model_reasoning_effort="xhigh"' in args
    assert 'service_tier="fast"' in args
    assert "--ephemeral" in args
    assert kwargs["cwd"] == str(server_module.PROJECT_ROOT)
    assert kwargs["env"]["TERM"] == "xterm-256color"
    assert kwargs["env"]["NO_COLOR"] == "1"
    assert ("stream", fake_process, 42) in stream_calls
    assert stream_calls[-1] == ("terminated", fake_process)


def test_stream_codex_process_decodes_utf8_across_chunks(monkeypatch):
    chunks = [b"caf\xc3", b"\xa9\n", b""]
    events = []

    class FakeStdout:
        def fileno(self) -> int:
            return 123

    class FakeProcess:
        stdout = FakeStdout()

        def poll(self):
            return None if chunks and chunks[0] else 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(server_module.select, "select", lambda read, _write, _error, _timeout: (read, [], []))
    monkeypatch.setattr(server_module.os, "read", lambda _fd, _size: chunks.pop(0))
    handler = object.__new__(Handler)
    handler.write_sse_json = lambda event, value: events.append((event, value))

    Handler.stream_codex_process(handler, FakeProcess())

    assert ("log", {"text": "café"}) in events
    assert events[-1] == ("done", {"return_code": 0})


def test_server_source_wires_routing_ws_readonly_and_pty_setup():
    # Scoped per owner: POST routes in the registry, the read-only WS attach in websocket(),
    # and the readonly `-r` + pty sizing in bridge_tmux.
    upload_route = route_by_path("POST", "/api/upload")
    event_route = route_by_path("POST", "/api/event")
    ws_body = inspect.getsource(Handler.websocket)
    bridge_body = inspect.getsource(Handler.bridge_tmux)
    initial_payload_body = inspect.getsource(Handler.read_initial_ws_payloads)
    payload_body = inspect.getsource(Handler.handle_ws_payload)

    assert upload_route.handler is http_routes.post_upload
    assert upload_route.role == "admin"
    assert event_route.handler is http_routes.post_event
    assert event_route.role == "readonly"
    assert "resize_client_id = clean_resize_authority_client_id" in ws_body
    assert "self.bridge_tmux(session, readonly=self.auth_readonly(), resize_client_id=resize_client_id)" in ws_body
    assert "tmux_attach_command(readonly=readonly)" in bridge_body
    assert 'tmux(["has-session", "-t", target])' in bridge_body
    assert "set_pty_size(slave_fd, initial_rows, initial_cols)" in bridge_body
    assert "saw_initial_resize" in bridge_body
    assert "host_pty_dimensions_for_session(session)" in bridge_body
    assert "record_host_pty_dimensions(session, initial_rows, initial_cols)" in bridge_body
    assert "session, tmux_client_name, resize_client_id, initial_cols, initial_rows" in bridge_body
    assert 'message.get("foreground") is False' in initial_payload_body
    assert "saw_resize = True" in initial_payload_body
    assert 'message.get("foreground") is False' in payload_body
    assert 'message.get("activate") is True' in payload_body


@pytest.mark.parametrize(
    ("https", "mode", "expected"),
    ((True, "rw", b"date\n"), (True, "ro", b""), (False, "rw", b""), (False, "ro", b"")),
)
@pytest.mark.skipif(not hasattr(server_module, "wait_for_ws_frame"), reason="requires buffered websocket readiness")
@pytest.mark.parametrize(("mode", "readonly"), (("ro", True), ("rw", False)))
def _api_response_capturing_handler(method: str = "GET", path: str = "/api/fs/list"):
    """Return one Handler whose real ``write_api_response`` writes through a capture."""

    route = http_routes.Route(
        method,
        path,
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler = Handler.__new__(Handler)
    handler._route_response = route
    handler._route_response_written = False
    handler._api_request_id = ""
    handler.headers = {}
    handler.server = SimpleNamespace(app=SimpleNamespace(
        observe_http_commit=lambda *_args: None,
        observe_http_receipt=lambda *_args: None,
    ))
    writes = []

    def capture(_self, data, status=HTTPStatus.OK, *, json_encode_ms=0.0, product_metadata=None):
        del json_encode_ms, product_metadata
        writes.append((json.loads(data), HTTPStatus(int(status))))

    handler._write_json_representation = MethodType(capture, handler)
    return handler, writes


def _terminal_filesystem_envelope(status: HTTPStatus, code: str, message_key: str) -> dict:
    """Build the terminal failure the operation ledger replays for a filesystem operation."""

    return {
        "state": "failed",
        "request": {"id": "r-fixture-terminal"},
        "error": {
            "code": code,
            "message": {"key": message_key, "params": {"path": "/tmp/yo-deleted-worktree"}, "fallback": "File not found"},
            "origin": "local_services.jobd",
            "retryable": False,
            "details": {
                "status": int(status),
                "path": "/tmp/yo-deleted-worktree",
                "operation_id": "op-fixture",
                "diagnostic": "path not found",
            },
            "stack": [
                {"component": "server.http", "operation": "GET /api/fs/list", "code": "dependency_failed"},
                {"component": "local_services.jobd", "operation": "jobd.result", "code": code},
            ],
        },
    }


@pytest.mark.parametrize(
    ("status", "code", "message_key", "expected_level"),
    (
        (HTTPStatus.NOT_FOUND, "path_not_found", "common.pathNotFound", "info"),
        (HTTPStatus.FORBIDDEN, "permission_denied", "fs.error.operationFailed", "info"),
        (HTTPStatus.INTERNAL_SERVER_ERROR, "dependency_failed", "common.requestFailed", "error"),
        (HTTPStatus.BAD_REQUEST, "invalid_request", "common.requestFailed", "error"),
    ),
)
def test_write_api_response_records_expected_outcomes_and_faults_at_one_severity_rule(
    status,
    code,
    message_key,
    expected_level,
):
    """The synchronous writer must reach the same verdict as the asynchronous recorder.

    A terminal filesystem failure is replayed to the browser here, so if this writer kept its own
    hardcoded ``level="error"`` the same 404 would be an operator error on one path and an ordinary
    outcome on the other -- one rule in two places, which is the defect being removed.
    """

    handler, writes = _api_response_capturing_handler()
    envelope = _terminal_filesystem_envelope(status, code, message_key)
    before = server_logs.SERVER_LOGS.payload()["sequence"]

    handler.write_json(envelope, status=status)

    payload, written_status = writes[0]
    assert written_status == status and payload["error"]["code"] == code, payload
    rows = [entry for entry in server_logs.SERVER_LOGS.payload()["logs"] if entry["id"] > before]
    assert [entry["source"] for entry in rows] == ["api-response"], rows
    assert rows[0]["level"] == expected_level, rows
    assert rows[0]["category"] == "api", rows
    assert json.loads(rows[0]["message"])["code"] == code, rows


def test_write_api_response_keeps_a_malformed_outcome_record_an_error():
    """An outcome code inside a record missing its causal stack is still a fault."""

    handler, _writes = _api_response_capturing_handler()
    envelope = _terminal_filesystem_envelope(HTTPStatus.NOT_FOUND, "path_not_found", "common.pathNotFound")
    envelope["error"]["details"] = {}
    envelope["error"]["stack"] = [{"component": "server.http", "operation": "GET /api/fs/list", "code": ""}]
    before = server_logs.SERVER_LOGS.payload()["sequence"]

    with pytest.raises(ValueError, match="invalid HTTP status or error shape"):
        handler.write_json(envelope, status=HTTPStatus.NOT_FOUND)

    assert [entry for entry in server_logs.SERVER_LOGS.payload()["logs"] if entry["id"] > before] == []

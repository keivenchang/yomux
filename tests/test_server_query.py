import inspect
import io
import json
import os
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

import pytest


from yolomux_lib import app as app_module
from yolomux_lib import http_routes
from yolomux_lib import server as server_module
from yolomux_lib import server_auth as server_auth_module
from yolomux_lib import web
from yolomux_lib.common import ACTIVITY_MAX_HOURS
from yolomux_lib.common import error_payload
from yolomux_lib.server import Handler
from yolomux_lib.server import parse_query_float
from yolomux_lib.server import parse_query_int
from yolomux_lib.server import parse_repo_refs_param
from yolomux_lib.server import ws_resize_dimensions
from yolomux_lib.web import html_page


SOURCE_STATIC_DIR = Path(__file__).resolve().parents[1] / "static_src"


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


def test_get_agent_auth_honors_force_query():
    writes = []
    calls = []
    app = SimpleNamespace(agent_auth_payload=lambda force=False: calls.append(force) or {"ok": True, "force": force})
    request = SimpleNamespace(server=SimpleNamespace(app=app), write_json=lambda payload, status=HTTPStatus.OK: writes.append((status, payload)))

    http_routes.get_agent_auth(request, SimpleNamespace(query="force=1"), None)

    assert calls == [True]
    assert writes == [(HTTPStatus.OK, {"ok": True, "force": True})]


def test_get_home_records_html_page_compute_time(monkeypatch):
    writes = []
    html_calls = []
    clock = iter([100.0, 100.037])

    def fake_html_page(sessions, access_role="admin", dev=False, dangerously_yolo=False, share=None, accept_language=""):
        html_calls.append((sessions, access_role, dev, dangerously_yolo, share, accept_language))
        return "<html>boot</html>"

    monkeypatch.setattr(http_routes, "html_page", fake_html_page)
    monkeypatch.setattr(http_routes.time, "perf_counter", lambda: next(clock))
    request = SimpleNamespace(
        server=SimpleNamespace(app=SimpleNamespace(sessions=["5"], dangerously_yolo=True), dev=True),
        share_sessions=lambda: [],
        share_record=lambda: None,
        share_bootstrap_payload=lambda record: {"record": record},
        auth_identity=lambda: SimpleNamespace(role="admin"),
        write_html=lambda body: writes.append(body),
    )

    http_routes.get_home(request, SimpleNamespace(query=""), route_by_path("GET", "/"))

    assert writes == ["<html>boot</html>"]
    assert html_calls == [(["5"], "admin", True, True, None, "")]
    assert request._http_response_compute_ms == pytest.approx(37.0)
    assert request._http_response_performance_details == {
        "html_page": True,
        "bootstrap_bytes": len("<html>boot</html>".encode("utf-8")),
        "session_count": 1,
        "share": False,
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


def test_get_stats_sample_uses_app_payload():
    writes = []
    calls = []
    app = SimpleNamespace(stats_sample_payload=lambda since=0, client_id="", token_consumer=False, token_since=0, token_resolution_seconds=0, history_start=0: calls.append((since, client_id, token_consumer, token_since, token_resolution_seconds, history_start)) or {"ok": True, "cpu_percent": 12.5, "pid": 123})
    request = SimpleNamespace(server=SimpleNamespace(app=app), write_json=lambda payload, status=HTTPStatus.OK: writes.append((status, payload)))

    http_routes.get_stats_sample(request, SimpleNamespace(query="since=9&client_id=client-a"), None)
    http_routes.get_stats_sample(request, SimpleNamespace(query="since=10&client_id=client-a&token_consumer=1&token_since=7&token_resolution=120&history_start=900"), None)

    assert calls == [(9, "client-a", False, 0, 0, 0), (10, "client-a", True, 7, 120, 900)]
    assert writes == [
        (HTTPStatus.OK, {"ok": True, "cpu_percent": 12.5, "pid": 123}),
        (HTTPStatus.OK, {"ok": True, "cpu_percent": 12.5, "pid": 123}),
    ]


class FakeShareConnection:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, frame: bytes) -> None:
        self.sent.append(frame)


class TimeoutShareConnection(FakeShareConnection):
    def __init__(self) -> None:
        super().__init__()
        self.timeout = 1.25
        self.timeouts: list[float | None] = []

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout
        self.timeouts.append(timeout)


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
    error = server_module.FilesystemError(
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


def test_share_ui_frame_redacts_share_urls_and_tokens():
    frame = server_module.share_ui_frame({
        "type": "popup-layer",
        "payload": {
            "token": "secret-token",
            "html": '<input value="https://host.example/share/abc123#t=secret-token">',
            "items": [{"url": "/share/abc123#t=secret-token", "share_token": "secret-token"}],
        },
    })

    assert b"secret-token" not in frame
    assert b"/share/abc123" not in frame
    assert b"..." in frame


def test_verify_share_token_snapshots_viewer_ids_before_status_iteration():
    app = object.__new__(app_module.TmuxWebtermApp)
    app.sessions = ["6"]
    app.share_tokens_lock = app_module.threading.Lock()
    app.share_tokens = {
        "share-token": {
            "session": "6",
            "sessions": ["6"],
            "created_at": app_module.time.time(),
            "expires_at": app_module.time.time() + 60,
            "revoked": False,
            "mode": "ro",
            "scheme": "http",
            "short_id": "abc",
            "max_viewers": 5,
            "viewer_ids": {"viewer-1": {"count": 1, "connected_at": 10.0, "last_seen_at": 10.0}},
            "ui_state": {},
        },
    }

    snapshot = app.verify_share_token("share-token")
    app.share_tokens["share-token"]["viewer_ids"]["viewer-2"] = {"count": 1, "connected_at": 20.0, "last_seen_at": 20.0}
    payload = app.share_status_frame_for_record(snapshot)

    assert snapshot is not app.share_tokens["share-token"]
    assert snapshot["viewer_ids"] is not app.share_tokens["share-token"]["viewer_ids"]
    assert sorted(snapshot["viewer_ids"]) == ["viewer-1"]
    assert payload["viewers"] == 1


def test_share_viewer_send_frame_restores_bounded_timeout():
    connection = TimeoutShareConnection()
    viewer = server_module.ShareViewerConnection(connection, "viewer-timeout")

    viewer.send_frame(b"frame")

    assert connection.sent == [b"frame"]
    assert connection.timeouts == [server_module.SHARE_VIEWER_SEND_TIMEOUT_SECONDS, 1.25]
    assert connection.timeout == 1.25


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


def test_session_files_route_validates_hours_before_share_scope():
    app = SimpleNamespace(session_files_payload=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("app should not run")))
    handler, _calls, writes = route_handler("/api/session-files", app)
    handler.share_sessions = lambda: ["allowed"]

    http_routes.get_session_files(handler, SimpleNamespace(query="session=blocked&hours=nope"), None)

    assert writes[0][2]["user_message"] == {
        "key": "request.error.number",
        "params": {"field": "hours"},
        "fallback": "hours must be a number",
    }


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


def test_websocket_bridge_terminates_tmux_process_group():
    # Scoped to the bridge_tmux method (inspect, not full-file string slicing): the tmux attach child
    # must be torn down via its process GROUP, never a bare terminate/kill that can orphan the group.
    bridge_body = inspect.getsource(Handler.bridge_tmux)

    assert "terminate_process_group(process)" in bridge_body
    assert "process.terminate()" not in bridge_body
    assert "process.kill()" not in bridge_body


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
    assert ["set-option", "-t", "3:", "window-size", "largest"] in calls
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
    # `#{client_name}\t#{client_session}\t#{client_width}\t#{client_flags}` per row.
    def fake_run(cmd, **kwargs):
        if "list-clients" in cmd:
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run


def test_claim_tmux_resize_authority_silences_wider_clients(monkeypatch):
    # The active surface owns the column width: every WIDER client on its session is flagged
    # ignore-size so `window-size largest` collapses to the active width — including a foreign,
    # hand-attached terminal, not just sibling browser surfaces. Clients at/below the active width
    # never overflow it and are left untouched.
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    calls: list[list[str]] = []
    stdout = (
        "/dev/pts/1\t6\t100\tattached,ignore-size,UTF-8\n"  # active surface, wrongly ignored
        "/dev/pts/2\t6\t120\tattached,UTF-8\n"              # wider browser surface -> silence
        "/dev/pts/3\t6\t90\tattached,UTF-8\n"               # narrower co-viewer -> leave alone
        "/dev/pts/4\t6\t130\tattached,ignore-size,UTF-8\n"  # wider but already silent -> no call
        "/dev/pts/5\t7\t200\tattached,UTF-8\n"              # other session -> not our concern
    )
    monkeypatch.setattr(server_module.subprocess, "run", _client_list_runner(stdout, calls))

    assert server_module.claim_tmux_resize_authority("6", "/dev/pts/1", 100) is True

    assert ["tmux", "refresh-client", "-t", "/dev/pts/1", "-f", "!ignore-size"] in calls
    assert ["tmux", "refresh-client", "-t", "/dev/pts/2", "-f", "ignore-size"] in calls
    assert all("/dev/pts/3" not in call for call in calls)
    assert all("/dev/pts/4" not in call for call in calls)
    assert all("/dev/pts/5" not in call for call in calls)


def test_claim_tmux_resize_authority_noop_when_active_is_widest(monkeypatch):
    # The "current width already == active width" fast path: when no other voting client is wider
    # and the active surface already counts, claiming makes zero tmux calls so frequent focus/resize
    # events stay cheap.
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    calls: list[list[str]] = []
    stdout = (
        "/dev/pts/1\t6\t120\tattached,UTF-8\n"              # active surface, already widest
        "/dev/pts/2\t6\t100\tattached,UTF-8\n"              # narrower -> harmless
        "/dev/pts/3\t6\t130\tattached,ignore-size,UTF-8\n"  # wider but already silenced
    )
    monkeypatch.setattr(server_module.subprocess, "run", _client_list_runner(stdout, calls))

    assert server_module.claim_tmux_resize_authority("6", "/dev/pts/1", 120) is False
    assert calls == []


def test_both_attach_paths_route_through_shared_tmux_options():
    # The host-browser and share-upstream attach paths must both go through the one shared option
    # helper, so the window-size smear fix can't regress by re-introducing an inline set-clipboard
    # block (which omits window-size) on either path.
    bridge_body = inspect.getsource(Handler.bridge_tmux)
    upstream_body = inspect.getsource(server_module.ShareTerminalUpstream.start_locked)

    assert "configure_session_tmux_options(session)" in bridge_body
    assert "configure_session_tmux_options(self.session)" in upstream_body
    assert "set-clipboard" not in bridge_body
    assert "set-clipboard" not in upstream_body


def test_tmux_attach_paths_refresh_clients_after_attach():
    bridge_body = inspect.getsource(Handler.bridge_tmux)
    upstream_body = inspect.getsource(server_module.ShareTerminalUpstream.start_locked)

    assert "refresh_tmux_session_clients_after_attach(session)" in bridge_body
    assert "refresh_tmux_session_clients_after_attach(self.session)" in upstream_body


def test_configure_session_tmux_options_uses_bounded_tmux_helper():
    body = inspect.getsource(server_module.configure_session_tmux_options)

    assert "tmux(args)" in body
    assert "subprocess.run" not in body


def test_html_uses_browser_highlight_js_bundle():
    html = html_page(["6"], "admin")

    assert "highlight.js@11.9.0/lib/common.min.js" not in html
    assert "highlightjs/cdn-release@11.9.0/build/highlight.min.js" in html
    assert "highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css" in html


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


def test_request_body_reader_owns_content_length_validation():
    # RA6: every POST body reader should route Content-Length parsing through one helper so missing,
    # invalid, non-positive, and oversized bodies cannot drift by route.
    assert "Content-Length" in inspect.getsource(Handler.read_request_body)
    for method in [Handler.read_json_body, Handler.read_urlencoded_form, Handler.handle_client_event, Handler.handle_upload]:
        body = inspect.getsource(method)
        assert "read_request_body" in body
        assert "self.headers.get(\"Content-Length" not in body
    assert "read_json_body" in inspect.getsource(http_routes._json_body)
    assert "Content-Length" not in inspect.getsource(http_routes.post_share_stop)


def test_do_post_share_stop_rejects_invalid_content_length_without_value_error():
    app_calls = []
    app = SimpleNamespace(stop_active_share=lambda target="": app_calls.append(target) or ({"ok": True}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/share/stop", app)
    handler.headers = {"Content-Length": "bad"}
    handler.rfile = io.BytesIO(b"")
    handler.server.close_inactive_share_upstreams = lambda: app_calls.append("closed-upstreams")

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [(
        "json",
        HTTPStatus.LENGTH_REQUIRED,
        {
            "error": "missing or invalid Content-Length",
            "user_message": {
                "key": "request.error.contentLengthInvalid",
                "params": {},
                "fallback": "missing or invalid Content-Length",
            },
            "status": 411,
        },
    )]
    assert app_calls == []


def route_handler(path, app=None, readonly=False):
    calls = []
    writes = []
    handler = object.__new__(Handler)
    handler.path = path
    handler.server = SimpleNamespace(app=app or SimpleNamespace(), dev=False)
    handler.close_connection = False
    handler.require_auth = lambda role="readonly": calls.append(("require_auth", role)) or True
    handler.require_auth_for_post = lambda path: calls.append(("require_auth_for_post", path)) or True
    handler.auth_readonly = lambda: readonly
    handler.auth_identity = lambda: SimpleNamespace(role="readonly" if readonly else "admin")
    handler.share_readonly_api_allowed = lambda parsed: False
    handler.share_token_text = lambda: ""
    handler.share_token = lambda: ""
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
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
            (HTTPStatus.NOT_FOUND, web.server_string("zh-Hans", "request.error.notFound") + "\n"),
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
    assert set(http_routes.ROUTE_GROUPS) == {"core", "share", "yoagent", "filesystem", "tmux"}
    assert route_by_path("GET", "/api/activity-summary").group == "core"
    assert route_by_path("GET", "/api/stats-sample").handler is http_routes.get_stats_sample
    assert route_by_path("GET", "/pane-popout").handler is http_routes.get_pane_popout
    assert route_by_path("POST", "/api/stats-history").role == "readonly"
    assert route_by_path("POST", "/api/stats-history").body_limit == 128 * 1024
    assert route_by_path("GET", "/ws/share-ui").handler is http_routes.get_share_ui_websocket
    assert route_by_path("POST", "/api/share/extend").body_limit == 4096
    assert route_by_path("POST", "/api/yoagent/jobs/cancel-session").handler is http_routes.post_yoagent_jobs_cancel_session
    assert route_by_path("POST", "/api/yoagent/jobs/*/confirm").handler is http_routes.post_yoagent_job_confirm
    assert route_by_path("POST", "/api/yoagent/waits/*/clear").handler is http_routes.post_yoagent_wait_clear
    assert route_by_path("POST", "/api/fs/batch").role is http_routes.share_readonly_post_role
    assert route_by_path("GET", "/api/fs/watch-diff").handler is http_routes.get_fs_watch_diff
    assert route_by_path("GET", "/api/fs/zip").handler is http_routes.get_fs_zip
    assert route_by_path("GET", "/api/fs/count").handler is http_routes.get_fs_count
    assert route_by_path("GET", "/api/tmux-session-exists").role == "readonly"


def test_do_get_routes_authenticated_json_and_stream_handlers():
    app = SimpleNamespace(
        session_metadata_payload=lambda force=False: {"sessions": {}, "force": force},
        activity_summary_payload=lambda force=False, locale="en", session_scope="configured", hours="24": {"force": force, "locale": locale},
        activity_payload=lambda hours=24.0, visible=True: ({"hours": hours, "visible": visible}, HTTPStatus.OK),
        stats_sample_payload=lambda since=0, client_id="", token_consumer=False, token_since=0, token_resolution_seconds=0, history_start=0: {"ok": True, "cpu_percent": 1.25, "since": since, "client_id": client_id, "token_consumer": token_consumer, "token_since": token_since, "token_resolution_seconds": token_resolution_seconds, "history_start": history_start},
        tmux_session_exists_payload=lambda session: ({"session": session, "exists": session == "2"}, HTTPStatus.OK),
    )

    handler, calls, writes = route_handler("/api/stats-sample?since=2&client_id=client-a&tokens=1&token_since=3&token_resolution=120", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "cpu_percent": 1.25, "since": 2, "client_id": "client-a", "token_consumer": True, "token_since": 3, "token_resolution_seconds": 120, "history_start": 0})]

    handler, calls, writes = route_handler("/api/session-metadata?force=1", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"sessions": {}, "force": True})]

    handler, calls, writes = route_handler("/api/tmux-session-exists?session=2", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"session": "2", "exists": True})]

    handler, calls, writes = route_handler("/api/transcripts?force=1", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"sessions": {}, "force": True})]

    handler, calls, writes = route_handler("/api/activity-summary?force=1&locale=ja", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"force": True, "locale": "ja"})]

    handler, calls, writes = route_handler("/api/activity?hours=0.5&visible=0", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"hours": 0.5, "visible": False})]

    app = SimpleNamespace(background_owner_status_payload=lambda: ({"status": "owner"}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/background/status", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"status": "owner"})]

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

    app = SimpleNamespace(yoagent_jobs_payload=lambda: ({"jobs": []}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/jobs", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"jobs": []})]

    handler, calls, writes = route_handler("/api/client-events", app)
    handler.stream_client_events = lambda: writes.append(("client-events", handler.path))
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("client-events", "/api/client-events")]

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


def test_do_get_fs_routes_allow_share_scoped_readonly_file_handlers():
    handler, calls, writes = route_handler("/api/fs/diff?path=/repo/README.md&token=share", readonly=True)
    handler.share_readonly_api_allowed = lambda parsed: parsed.path == "/api/fs/diff"
    handler.handle_fs_diff = lambda parsed: writes.append(("fs-diff", parsed.path))

    Handler.do_GET(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("fs-diff", "/api/fs/diff")]


def test_do_get_fs_watch_diff_uses_client_since_token_without_tracking_clients():
    app = SimpleNamespace(
        filesystem_watch_diff_payload=lambda since_token="", force_full=False: {
            "since": since_token,
            "force_full": force_full,
        }
    )
    handler, calls, writes = route_handler("/api/fs/watch-diff?since=old-token", app)

    Handler.do_GET(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"since": "old-token", "force_full": False})]


def test_do_post_stats_history_records_browser_deltas():
    app_calls = []
    app = SimpleNamespace(
        record_stats_history_payload=lambda payload: app_calls.append(payload) or ({"ok": True, "history": {"sequence": 3}}, HTTPStatus.OK),
    )
    handler, calls, writes = route_handler("/api/stats-history", app)
    handler.headers = {"Content-Length": "64"}
    handler.read_json_body = lambda limit: {"since": 2, "records": [{"start": 10, "api_count": 1}]}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "history": {"sequence": 3}})]
    assert app_calls == [{"since": 2, "records": [{"start": 10, "api_count": 1}]}]


def test_share_scoped_transcripts_payload_filters_to_shared_sessions():
    handler, _calls, _writes = route_handler("/", SimpleNamespace())
    handler.share_sessions = lambda: ["6", "8"]
    payload = {
        "session_order": ["5", "6", "7", "8"],
        "sessions": {"5": {"target": "5"}, "6": {"target": "6"}, "8": {"target": "8"}},
        "cache": {"hit": True},
    }

    scoped = Handler.share_scoped_transcripts_payload(handler, payload)

    assert scoped["session_order"] == ["6", "8"]
    assert scoped["sessions"] == {"6": {"target": "6"}, "8": {"target": "8"}}
    assert scoped["cache"] == {"hit": True}


def share_token_auth_handler(path, method="POST"):
    writes = []
    record = {"token": "share-token", "mode": "ro", "session": "5", "sessions": ["5"]}
    app = SimpleNamespace(verify_share_token=lambda token: record if token == "share-token" else None)
    handler = object.__new__(Handler)
    handler.path = path
    handler.command = method
    handler.headers = {"X-Share-Token": "share-token", "Content-Length": "12", "User-Agent": "pytest"}
    handler.server = SimpleNamespace(app=app, tls_context=object(), server_address=("127.0.0.1", 19001))
    handler.close_connection = False
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
    handler.write_html = lambda value: writes.append(("html", HTTPStatus.OK, value))
    handler.write_redirect = lambda value, status=HTTPStatus.SEE_OTHER: writes.append(("redirect", status, value))
    handler.send_header = lambda *_args, **_kwargs: None
    return handler, writes


def test_share_token_post_auth_allows_only_readonly_fs_batch_and_debug_profile(monkeypatch):
    monkeypatch.setattr(server_auth_module, "auth_setup_required", lambda: False)

    allowed_handler, allowed_writes = share_token_auth_handler("/api/fs/batch")
    assert Handler.require_auth_for_post(allowed_handler, "/api/fs/batch") is True
    assert allowed_writes == []
    assert allowed_handler.auth_identity().role == "readonly"
    assert allowed_handler.share_token() == "share-token"
    assert allowed_handler.close_connection is False

    debug_handler, debug_writes = share_token_auth_handler("/api/share/debug-profile")
    assert Handler.require_auth_for_post(debug_handler, "/api/share/debug-profile") is True
    assert debug_writes == []
    assert debug_handler.auth_identity().role == "readonly"
    assert debug_handler.share_token() == "share-token"
    assert debug_handler.close_connection is False

    mutating_paths = [
        "/api/event",
        "/api/settings",
        "/api/share",
        "/api/share/stop",
        "/api/share/extend",
        "/api/upload",
        "/api/tmux-window",
        "/api/watch/roots",
        "/api/drop-action/run",
        "/api/yoagent/chat",
        "/api/yoagent/intent",
        "/api/yoagent/jobs",
        "/api/fs/write",
        "/api/fs/delete",
        "/api/fs/rename",
        "/api/fs/mkdir",
        "/api/fs/unindex",
    ]
    for path in mutating_paths:
        handler, writes = share_token_auth_handler(path)
        assert Handler.require_auth_for_post(handler, path) is False, path
        assert writes and writes[0][0] == "json" and writes[0][1] == HTTPStatus.FORBIDDEN, (path, writes)
        assert handler.close_connection is True, path


def test_test_auth_bypass_does_not_escalate_share_token_to_admin(monkeypatch):
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")

    handler, writes = share_token_auth_handler("/api/settings")

    assert Handler.require_auth(handler, "admin") is False
    assert writes == [(
        "json",
        HTTPStatus.FORBIDDEN,
        {
            "error": "share token is limited to the shared page and websocket",
            "user_message": {
                "key": "share.error.pageScope",
                "params": {},
                "fallback": "share token is limited to the shared page and websocket",
            },
            "role": "readonly",
        },
    )]
    assert handler.auth_identity().role == "readonly"
    assert handler.share_token() == "share-token"


def test_tmux_signal_event_watcher_is_owned_by_client_event_lifecycle():
    app_start_body = inspect.getsource(app_module.TmuxWebtermApp.start_client_event_watcher)
    app_event_body = inspect.getsource(app_module.TmuxWebtermApp.handle_tmux_signal_event)
    stream_body = inspect.getsource(server_module.Handler.stream_client_events)
    server_init_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.__init__)
    server_close_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.server_close)

    assert "self.start_tmux_signal_event_watcher()" in app_start_body
    assert "self.tmux_signal_cache.clear()" in app_event_body
    assert "self.client_event_next_tmux_signal_poll_at = 0.0" in app_event_body
    assert "self.client_watch_wake_event.set()" in app_event_body
    assert "self.server.app.start_client_event_watcher()" in stream_body
    assert "self.server.app.stop_client_event_watcher_if_idle()" in stream_body
    assert "self.app.start_client_event_watcher()" not in server_init_body
    assert "self.app.stop_client_event_watcher()" in server_close_body


def test_share_request_allowed_route_matrix(monkeypatch):
    monkeypatch.setattr(server_auth_module, "auth_setup_required", lambda: False)
    allowed_paths = [
        "/",
        "/share/abc",
        "/static/yolomux.js",
        "/api/ping",
        "/api/activity",
        "/api/fs/batch",
        "/api/share/debug-profile",
        "/api/fs/diff?path=/repo/README.md",
        "/api/fs/watch-diff?since=old-token",
        "/api/fs/read?path=/repo/README.md",
        "/api/fs/raw?path=/repo/README.md",
        "/api/share-stream",
        "/api/session-metadata",
        "/api/session-files",
        "/api/transcripts",
        "/ws/share-ui",
        "/ws/share-view",
    ]
    denied_paths = [
        "/api/event",
        "/api/settings",
        "/api/upload",
        "/api/watch/roots",
        "/api/drop-action/run",
        "/api/yoagent/chat",
        "/api/yoagent/intent",
        "/api/yoagent/jobs",
        "/api/fs/write",
        "/api/fs/delete",
        "/api/fs/rename",
        "/api/fs/mkdir",
        "/api/fs/unindex",
    ]

    for path in allowed_paths:
        handler, _writes = share_token_auth_handler(path, method="GET")
        assert Handler.share_request_allowed(handler) is True, path
    for path in denied_paths:
        handler, _writes = share_token_auth_handler(path, method="POST")
        assert Handler.share_request_allowed(handler) is False, path


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

    app = SimpleNamespace(run_file_drop_action=lambda payload: ({"ok": True, "action": payload["action"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/drop-action/run", app)
    handler.read_json_body = lambda limit: {"action": "server-info", "paths": ["/repo/README.md"]}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "action": "server-info"})]

    app = SimpleNamespace(preview_yoagent_send_action=lambda payload: ({"ok": True, "preview": payload["session"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/actions/preview-send", app)
    handler.read_json_body = lambda limit: {"session": "6", "text": "date"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "preview": "6"})]

    app = SimpleNamespace(execute_yoagent_send_action=lambda payload: ({"ok": True, "preview_id": payload["preview_id"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/actions/execute-send", app)
    handler.read_json_body = lambda limit: {"preview_id": "ya_1"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "preview_id": "ya_1"})]

    app = SimpleNamespace(yoagent_intent=lambda payload: ({"ok": True, "intent": payload["type"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/intent", app)
    handler.read_json_body = lambda limit: {"type": "notify_session_idle"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "intent": "notify_session_idle"})]

    app = SimpleNamespace(create_yoagent_job=lambda payload: ({"ok": True, "job": payload["type"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/jobs", app)
    handler.read_json_body = lambda limit: {"type": "notify_session_idle"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "job": "notify_session_idle"})]

    app = SimpleNamespace(confirm_yoagent_job=lambda job_id: ({"ok": True, "id": job_id}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/jobs/yj_1/confirm", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "id": "yj_1"})]

    app = SimpleNamespace(cancel_yoagent_jobs_for_session=lambda session: ({"ok": True, "session": session, "count": 2}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/jobs/cancel-session", app)
    handler.read_json_body = lambda limit: {"session": "6"}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "session": "6", "count": 2})]

    app = SimpleNamespace(cancel_yoagent_job=lambda job_id: ({"ok": True, "id": job_id}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/jobs/yj_1/cancel", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "id": "yj_1"})]

    app = SimpleNamespace(cancel_yoagent_chat=lambda request_id: ({"ok": True, "request_id": request_id, "cancelled": True}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/yoagent/chat/chat-abc/cancel", app)
    handler.read_json_body = lambda limit: {}

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "request_id": "chat-abc", "cancelled": True})]

    app = SimpleNamespace(clear_yoagent_action_wait=lambda wait_id: ({"ok": True, "id": wait_id}, HTTPStatus.OK))
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


def test_do_post_share_stop_passes_query_target_to_app():
    app_calls = []
    app = SimpleNamespace(stop_active_share=lambda target="": app_calls.append(target) or ({"ok": True, "stopped": 1}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/share/stop?id=short123", app)
    handler.headers = {"Content-Length": "0"}
    handler.server.close_inactive_share_upstreams = lambda: app_calls.append("closed-upstreams")
    handler.write_app_result = lambda result: writes.append(("app", result))

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("app", ({"ok": True, "stopped": 1}, HTTPStatus.OK))]
    assert app_calls == ["short123", "closed-upstreams"]


def test_do_post_share_extend_broadcasts_status():
    app_calls = []
    app = SimpleNamespace(
        extend_share_token=lambda target, add_seconds, **kwargs: app_calls.append((target, add_seconds, kwargs)) or ({"ok": True, "token": "share-token-1"}, HTTPStatus.OK),
    )
    handler, calls, writes = route_handler("/api/share/extend", app)
    handler.headers = {"Content-Length": "52"}
    handler.read_json_body = lambda limit: {"token": "share-token-1", "add_seconds": 600}
    handler.request_base_url = lambda: "http://127.0.0.1:9998"
    handler.server.broadcast_share_status = lambda token: app_calls.append(("broadcast", token))
    handler.write_app_result = lambda result: writes.append(("app", result))

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("app", ({"ok": True, "token": "share-token-1"}, HTTPStatus.OK))]
    assert app_calls == [("share-token-1", 600, {"base_url": "http://127.0.0.1:9998"}), ("broadcast", "share-token-1")]


def test_do_post_share_debug_profile_records_with_share_token():
    app_calls = []
    app = SimpleNamespace(
        record_share_debug_profile=lambda token, payload, **kwargs: app_calls.append((token, payload, kwargs)) or ({"ok": True, "stored": True}, HTTPStatus.OK),
    )
    handler, calls, writes = route_handler("/api/share/debug-profile", app)
    handler.headers = {"Content-Length": "35", "User-Agent": "Safari"}
    handler.client_address = ("203.0.113.9", 4444)
    handler.share_token = lambda: "share-token-1"
    handler.read_json_body = lambda limit: {"kind": "share-replay-health"}
    handler.write_app_result = lambda result: writes.append(("app", result))

    Handler.do_POST(handler)

    assert calls == [("require_auth", "admin")]
    assert writes == [("app", ({"ok": True, "stored": True}, HTTPStatus.OK))]
    assert app_calls == [("share-token-1", {"kind": "share-replay-health"}, {"ip": "203.0.113.9", "user_agent": "Safari"})]


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


def test_handle_fs_batch_returns_per_item_results(monkeypatch):
    monkeypatch.setattr(server_module.filesystem, "list_directory", lambda path: {"path": path, "entries": [{"name": "a"}]})

    def path_info(path):
        if path == "/missing":
            raise server_module.FilesystemError.path_not_found("/missing")
        return {"path": path, "kind": "dir"}

    monkeypatch.setattr(server_module.filesystem, "path_info", path_info)
    handler, writes = batch_handler({
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
    })

    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes[0][0] == HTTPStatus.OK
    responses = writes[0][1]["responses"]
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


def test_handle_fs_batch_records_performance(monkeypatch):
    records = []
    app = SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs)))
    monkeypatch.setattr(server_module.filesystem, "list_directory", lambda path: {"path": path, "entries": []})
    handler, writes = batch_handler({"requests": [{"id": "root", "type": "list", "path": "/repo"}]}, app=app)

    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes == [(HTTPStatus.OK, {"responses": [{"id": "root", "ok": True, "status": 200, "payload": {"path": "/repo", "entries": []}}]})]
    assert len(records) == 1
    args, kwargs = records[0]
    assert args == ("fs-batch", "api")
    assert kwargs["trigger"] == "POST /api/fs/batch"
    assert kwargs["cache_key"] == {"kind": "fs-batch"}
    assert kwargs["cache_status"] == "computed"
    assert kwargs["owner_role"] == "client"
    assert kwargs["count"] == 1
    assert kwargs["details"]["ops"] == '{"list": 1}'


def test_handle_fs_batch_rejects_invalid_shape():
    handler, writes = batch_handler({"requests": "nope"})

    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes[0][0] == HTTPStatus.BAD_REQUEST
    assert writes[0][1]["user_message"] == {
        "key": "request.error.list",
        "params": {"field": "requests"},
        "fallback": "requests must be a list",
    }


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


def test_handle_ws_payload_resize_sets_pty_and_signals_for_admin_only(monkeypatch):
    calls = []
    process = SimpleNamespace(pid=123, poll=lambda: None)
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


def test_websocket_rejects_share_token_for_other_session():
    writes = []
    handler = SimpleNamespace(
        share_sessions=lambda: ["6"],
        server=SimpleNamespace(app=SimpleNamespace(sessions=["6", "7"])),
        write_text=lambda value, status=HTTPStatus.OK: writes.append((status, value)),
    )

    Handler.websocket(handler, SimpleNamespace(query="session=7"))

    assert writes == [(HTTPStatus.FORBIDDEN, "share token is scoped to a different session\n")]


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
    assert "self.server.claim_resize_authority(session, tmux_client_name, resize_client_id)" in bridge_body
    assert 'message.get("foreground") is False' in initial_payload_body
    assert "saw_resize = True" in initial_payload_body
    assert 'message.get("foreground") is False' in payload_body
    assert 'message.get("activate") is True' in payload_body


def test_share_write_mode_stays_terminal_input_only():
    upstream_start = inspect.getsource(server_module.ShareTerminalUpstream.start_locked)
    upstream_write = inspect.getsource(server_module.ShareTerminalUpstream.write_input)
    bridge_body = inspect.getsource(Handler.bridge_shared_tmux)
    share_stop_body = inspect.getsource(http_routes.post_share_stop)

    assert 'record = self.server.app.verify_share_token(self.token)' in upstream_start
    assert 'str((record or {}).get("mode") or "ro") != "rw"' in upstream_start
    assert "tmux_attach_command(readonly=readonly)" in upstream_start
    assert 'message.get("type") != "input"' in upstream_write
    assert 'record_user_input(self.session, len(filtered), source="share", data=filtered)' in upstream_write
    assert 'self.request_is_https() and str(current_record.get("mode") or "ro") == "rw"' in bridge_body
    assert "upstream.write_input(payload)" in bridge_body
    assert "request.server.close_inactive_share_upstreams()" in share_stop_body


def test_share_pointer_events_are_coalesced_server_side():
    ui_body = inspect.getsource(Handler.handle_share_ui_message)
    queue_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.queue_share_pointer)
    loop_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.share_pointer_loop)
    hz_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.share_pointer_hz)

    assert "if msg_type == SHARE_MIRROR_FRAME_POINTER:" in ui_body
    assert "self.server.queue_share_pointer(token, payload, sender=sender)" in ui_body
    assert "self.share_pointer_latest[clean_token]" in queue_body
    assert "SHARE_POINTER_CLICK_QUEUE_LIMIT" in queue_body
    assert "threading.Thread(target=self.share_pointer_loop" in queue_body
    assert "signature != last_sent" in loop_body
    assert '"type": SHARE_MIRROR_FRAME_POINTER' in loop_body
    assert "skip_client_id=sender" in loop_body
    assert "SHARE_POINTER_MAX_WRITES_PER_SECOND / viewers" in hz_body
    assert "self.share_ui_client_count(token)" in hz_body
    assert server_module.SHARE_POINTER_MAX_HZ == 30


def test_share_ui_socket_wires_write_clients_and_host_broadcasts():
    share_ui_route = route_by_path("GET", "/ws/share-ui")
    share_status_route = route_by_path("GET", "/api/share")
    host_body = inspect.getsource(Handler.websocket_share_host)
    viewer_body = inspect.getsource(Handler.websocket_share_ui)
    bridge_body = inspect.getsource(Handler.bridge_share_ui_socket)
    handle_body = inspect.getsource(Handler.handle_share_ui_message)
    server_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.broadcast_share_ui)

    assert share_ui_route.handler is http_routes.get_share_ui_websocket
    assert http_routes.route_required_role(share_status_route, SimpleNamespace(share_token_text=lambda: "share-token"), SimpleNamespace(path="/api/share")) == "readonly"
    assert http_routes.route_required_role(share_status_route, SimpleNamespace(share_token_text=lambda: ""), SimpleNamespace(path="/api/share")) == "admin"
    assert "self.bridge_share_ui_socket(token, client_id)" in host_body
    assert 'write_enabled = self.request_is_https() and str(record.get("mode") or "ro") == "rw"' in viewer_body
    assert 'registered_viewer_id = ""' in viewer_body
    assert 'register(token, "", viewer_id, client_ip, self.headers.get("User-Agent", ""))' in viewer_body
    assert "receive_only=not write_enabled" in viewer_body
    assert "viewer_id=registered_viewer_id" in viewer_body
    assert "self.server.register_share_ui_client(token, client)" in bridge_body
    assert "self.server.enqueue_share_replay_frames_for_viewer(token, client)" in bridge_body
    assert "threading.Thread(target=client.write_loop" in bridge_body
    assert "select.select([self.connection]" in bridge_body
    assert "accept_semantic_state=False" in viewer_body
    assert "self.handle_share_ui_message(token, message, clean_client_id, accept_semantic_state=accept_semantic_state)" in bridge_body
    assert "receive_only and not share_replay_viewer_control_frame_allowed" in bridge_body
    assert "not accept_semantic_state and share_viewer_semantic_mutation_frame_disallowed(msg_type)" in handle_body
    assert bridge_body.index('json.loads(payload.decode("utf-8"))') < bridge_body.index("receive_only and not share_replay_viewer_control_frame_allowed")
    assert "self.server.broadcast_share_status(token)" in inspect.getsource(Handler.websocket_share_view)
    assert "self.server.broadcast_share_status(token)" in inspect.getsource(Handler.bridge_shared_tmux)
    assert "self.server.app.unregister_share_viewer(token, viewer_id)" in bridge_body
    assert "skip_client_id=sender" in handle_body
    assert "share_mirror_frame_type_allowed(msg_type)" in handle_body
    assert "msg_type == SHARE_MIRROR_FRAME_INPUT_INTENT" in handle_body
    assert "self.normalize_share_input_intent_for_handler(token, payload)" in handle_body
    assert "self.server.record_share_replay_keyframe(token, relay_message)" in handle_body
    assert "self.server.record_share_replay_delta(token, relay_message)" in handle_body
    assert "msg_type in {SHARE_MIRROR_FRAME_VIEWPORT, SHARE_MIRROR_FRAME_APPEARANCE}" in handle_body
    assert '"uiStatePatch": {msg_type: payload}' in handle_body
    assert "msg_type == SHARE_MIRROR_FRAME_SCROLL" in handle_body
    assert '"uiStateScroll": payload' in handle_body
    assert callable(getattr(server_module.TmuxWebtermHTTPServer, "broadcast_share_status", None))
    assert "self.share_ui_clients" in server_body
    assert "ui_client_ids" in server_body
    assert "viewer.client_id in ui_client_ids" in server_body
    assert "self.request_share_replay_keyframe(str(token or \"\"), reason=\"backpressure\")" in server_body


def test_share_ui_message_relay_preserves_mirror_metadata():
    updates = []
    broadcasts = []
    app = SimpleNamespace(update_share_record_ui_state=lambda token, payload: updates.append((token, payload)))
    handler = SimpleNamespace(
        server=SimpleNamespace(
            app=app,
            record_share_replay_keyframe=lambda _token, _message: None,
            broadcast_share_ui=lambda token, message, skip_client_id="": broadcasts.append((token, message, skip_client_id)),
        )
    )

    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "layout",
        "payload": {"layout": "left", "tabs": "left:1"},
        "sender": "host-browser",
        "epoch": 7,
        "sequence": 12,
        "reason": "layout-after-resize",
    }, "client-a")

    assert updates == [("share-token", {"layout": "left", "tabs": "left:1"})]
    assert broadcasts == [("share-token", {
        "type": "layout",
        "payload": {"layout": "left", "tabs": "left:1"},
        "sender": "host-browser",
        "epoch": 7,
        "sequence": 12,
        "reason": "layout-after-resize",
    }, "host-browser")]


def test_share_ui_message_rejects_viewer_authored_semantic_state_frames():
    updates = []
    broadcasts = []
    app = SimpleNamespace(update_share_record_ui_state=lambda token, payload: updates.append((token, payload)))
    handler = SimpleNamespace(
        server=SimpleNamespace(
            app=app,
            record_share_replay_keyframe=lambda _token, _message: None,
            record_share_replay_delta=lambda _token, _message: None,
            broadcast_share_ui=lambda token, message, skip_client_id="": broadcasts.append((token, message, skip_client_id)),
        )
    )

    for message in [
        {"type": "layout", "payload": {"layout": "left", "tabs": "left:1"}, "sender": "writer-a"},
        {"type": "ui-state", "payload": {"layout": "left", "tabs": "left:1", "finder": {"root": "/tmp/client"}, "editor": {"modes": [{"item": "client"}]}}, "sender": "writer-a"},
        {"type": "popup-layer", "payload": {"items": [{"html": "<div>client popup</div>"}]}, "sender": "writer-a"},
    ]:
        Handler.handle_share_ui_message(handler, "share-token", message, "writer-a", accept_semantic_state=False)

    assert updates == []
    assert broadcasts == []

    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "layout",
        "payload": {"layout": "left", "tabs": "left:1"},
        "sender": "host-browser",
    }, "host-browser", accept_semantic_state=True)

    assert updates == [("share-token", {"layout": "left", "tabs": "left:1"})]
    assert broadcasts == [("share-token", {
        "type": "layout",
        "payload": {"layout": "left", "tabs": "left:1"},
        "sender": "host-browser",
    }, "host-browser")]


def test_share_replay_protocol_constants_and_frame_allowlist():
    assert server_module.SHARE_MIRROR_PROTOCOL_VERSION == 1
    assert server_module.SHARE_MIRROR_REPLAY_FRAME_TYPES == frozenset({
        "dom-keyframe",
        "dom-delta",
        "dom-keyframe-request",
        "dom-keyframe-ack",
        "dom-replay-error",
        "terminal-host-resize",
    })
    assert server_module.SHARE_MIRROR_REPLAY_VIEWER_CONTROL_FRAME_TYPES == frozenset({
        "dom-keyframe-request",
        "dom-keyframe-ack",
        "dom-replay-error",
    })
    assert {"ui-state", "layout", "popup-layer"} <= server_module.SHARE_MIRROR_VIEWER_SEMANTIC_MUTATION_FRAME_TYPES
    assert server_module.share_viewer_semantic_mutation_frame_disallowed("ui-state")
    assert not server_module.share_viewer_semantic_mutation_frame_disallowed("input-intent")
    assert server_module.SHARE_MIRROR_SEQUENCE_FIELDS == ("epoch", "sequence", "baseSequence")
    assert server_module.SHARE_MIRROR_KEYFRAME_REASONS == frozenset({"join", "gap", "digest", "replay-error", "backpressure", "topology", "manual-debug"})
    assert server_module.SHARE_TERMINAL_PLACEHOLDER_FIELDS == ("placeholderId", "session", "rows", "cols", "terminalEpoch")
    assert server_module.SHARE_MIRROR_REDACTION_POLICY_VERSION == 1
    assert server_module.SHARE_REPLAY_DELTA_RING_LIMIT == 128
    assert server_module.share_replay_frame_type_allowed("dom-keyframe")
    assert server_module.share_replay_frame_type_allowed("dom-keyframe-request")
    assert not server_module.share_replay_frame_type_allowed("dom-keyframe-evil")
    assert server_module.share_replay_viewer_control_frame_allowed("dom-keyframe-request")
    assert not server_module.share_replay_viewer_control_frame_allowed("dom-delta")


def test_share_input_intent_protocol_constants_and_normalization():
    assert server_module.SHARE_MIRROR_FRAME_INPUT_INTENT == "input-intent"
    assert server_module.SHARE_INPUT_INTENT_TYPES == frozenset({
        "terminal-input",
        "terminal-paste",
        "terminal-scroll",
        "tab-activate",
        "menu-command",
        "host-command",
    })
    assert server_module.share_mirror_frame_type_allowed("input-intent")
    assert server_module.share_input_intent_type_allowed("terminal-input")
    assert not server_module.share_input_intent_type_allowed("filesystem-write")

    sessions = ["6", "8"]
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-input", "session": "6", "data": "ls\x00\n"}, sessions) == {"intent": "terminal-input", "session": "6", "data": "ls\n"}
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-paste", "session": "8", "data": "paste"}, sessions) == {"intent": "terminal-paste", "session": "8", "data": "paste"}
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-scroll", "session": "6", "direction": "up", "lines": 5000}, sessions) == {"intent": "terminal-scroll", "session": "6", "direction": "up", "lines": server_module.SHARE_INPUT_INTENT_MAX_LINES}
    assert server_module.normalize_share_input_intent_payload({"intent": "tab-activate", "session": "6", "item": "file:/repo/README.md"}, sessions) == {"intent": "tab-activate", "item": "file:/repo/README.md", "session": "6"}
    assert server_module.normalize_share_input_intent_payload({"intent": "menu-command", "session": "6", "command": "tab-close", "target": "6"}, sessions) == {"intent": "menu-command", "command": "tab-close", "target": "6", "session": "6"}
    assert server_module.normalize_share_input_intent_payload({"intent": "host-command", "command": "request-keyframe"}, sessions) == {"intent": "host-command", "command": "request-keyframe"}
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-input", "session": "9", "data": "ls"}, sessions) is None
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-input", "session": "6", "data": ""}, sessions) is None
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-scroll", "session": "6", "direction": "left", "lines": 5}, sessions) is None
    assert server_module.normalize_share_input_intent_payload({"intent": "terminal-scroll", "session": "6", "direction": "up", "lines": True}, sessions) is None
    assert server_module.normalize_share_input_intent_payload({"intent": "tab-activate", "item": "bad\nitem"}, sessions) is None
    assert server_module.normalize_share_input_intent_payload({"intent": "menu-command", "command": "run-arbitrary-js"}, sessions) is None
    assert server_module.normalize_share_input_intent_payload({"intent": "host-command", "command": "shutdown"}, sessions) is None


def test_share_ui_message_relay_accepts_only_valid_write_input_intents():
    broadcasts = []
    applied = []
    record = {"mode": "rw", "session": "6", "sessions": ["6", "8"]}
    app = SimpleNamespace(
        verify_share_token=lambda token: record if token == "share-token" else None,
        share_record_sessions=lambda share_record: share_record.get("sessions", []),
        update_share_record_ui_state=lambda _token, _payload: None,
    )
    handler = SimpleNamespace(
        server=SimpleNamespace(
            app=app,
            record_share_replay_keyframe=lambda _token, _message: None,
            record_share_replay_delta=lambda _token, _message: None,
            broadcast_share_ui=lambda token, message, skip_client_id="": broadcasts.append((token, message, skip_client_id)),
        ),
        request_is_https=lambda: True,
    )
    handler.share_record_sessions_for_handler = lambda share_record: Handler.share_record_sessions_for_handler(handler, share_record)
    handler.normalize_share_input_intent_for_handler = lambda token, payload: Handler.normalize_share_input_intent_for_handler(handler, token, payload)
    handler.apply_share_input_intent_for_handler = lambda token, payload: applied.append((token, payload)) or True

    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "input-intent",
        "payload": {"intent": "terminal-input", "session": "6", "data": "date\x00\n"},
        "sender": "writer-a",
        "epoch": 3,
        "sequence": 4,
    }, "client-a")
    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "input-intent",
        "payload": {"intent": "host-command", "command": "request-keyframe"},
        "sender": "writer-a",
    }, "client-a")
    Handler.handle_share_ui_message(handler, "share-token", {"type": "input-intent", "payload": {"intent": "terminal-input", "session": "9", "data": "bad"}, "sender": "writer-a"}, "client-a")
    Handler.handle_share_ui_message(handler, "bad-token", {"type": "input-intent", "payload": {"intent": "terminal-input", "session": "6", "data": "bad"}, "sender": "writer-a"}, "client-a")

    record["mode"] = "ro"
    Handler.handle_share_ui_message(handler, "share-token", {"type": "input-intent", "payload": {"intent": "terminal-input", "session": "6", "data": "readonly"}, "sender": "writer-a"}, "client-a")
    record["mode"] = "rw"
    handler.request_is_https = lambda: False
    Handler.handle_share_ui_message(handler, "share-token", {"type": "input-intent", "payload": {"intent": "terminal-input", "session": "6", "data": "http"}, "sender": "writer-a"}, "client-a")

    assert broadcasts == [
        ("share-token", {
            "type": "input-intent",
            "payload": {"intent": "terminal-input", "session": "6", "data": "date\n"},
            "sender": "writer-a",
            "epoch": 3,
            "sequence": 4,
        }, "writer-a"),
        ("share-token", {
            "type": "input-intent",
            "payload": {"intent": "host-command", "command": "request-keyframe"},
            "sender": "writer-a",
        }, "writer-a"),
    ]
    assert applied == [
        ("share-token", {"intent": "terminal-input", "session": "6", "data": "date\n"}),
        ("share-token", {"intent": "host-command", "command": "request-keyframe"}),
    ]


def test_share_input_intent_applies_terminal_paths_to_existing_share_upstream():
    writes = []
    scrolls = []
    upstream = SimpleNamespace(write_input=lambda payload: writes.append(json.loads(payload.decode("utf-8"))) or True)
    app = SimpleNamespace(tmux_scroll=lambda session, direction, lines: scrolls.append((session, direction, lines)))
    handler = SimpleNamespace(server=SimpleNamespace(
        app=app,
        share_terminal_upstream=lambda token, session: upstream if (token, session) == ("share-token", "6") else None,
    ))

    assert Handler.apply_share_input_intent_for_handler(handler, "share-token", {"intent": "terminal-input", "session": "6", "data": "abc"}) is True
    assert Handler.apply_share_input_intent_for_handler(handler, "share-token", {"intent": "terminal-paste", "session": "6", "data": "paste"}) is True
    assert Handler.apply_share_input_intent_for_handler(handler, "share-token", {"intent": "terminal-scroll", "session": "6", "direction": "up", "lines": 12}) is True
    assert Handler.apply_share_input_intent_for_handler(handler, "share-token", {"intent": "tab-activate", "item": "6"}) is False
    assert writes == [{"type": "input", "data": "abc"}, {"type": "input", "data": "paste"}]
    assert scrolls == [("6", "up", 12)]


def test_share_ui_message_relay_accepts_known_replay_frames_only():
    broadcasts = []
    recorded = []
    app = SimpleNamespace(update_share_record_ui_state=lambda _token, _payload: None)
    handler = SimpleNamespace(
        server=SimpleNamespace(
            app=app,
            record_share_replay_keyframe=lambda token, message: recorded.append((token, message)),
            record_share_replay_delta=lambda token, message: recorded.append((token, message)),
            broadcast_share_ui=lambda token, message, skip_client_id="": broadcasts.append((token, message, skip_client_id)),
        )
    )

    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "dom-keyframe",
        "version": 1,
        "payload": {"root": {"nodeId": 1}},
        "sender": "host-browser",
        "epoch": 9,
        "sequence": 42,
        "reason": "join",
    }, "client-a")
    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "dom-delta",
        "version": 1,
        "payload": {"mutations": []},
        "sender": "host-browser",
        "epoch": 9,
        "sequence": 43,
        "baseSequence": 42,
        "reason": "gap",
    }, "client-a")
    Handler.handle_share_ui_message(handler, "share-token", {
        "type": "dom-keyframe-evil",
        "payload": {"root": {"nodeId": 2}},
        "sender": "host-browser",
    }, "client-a")

    assert broadcasts == [
        ("share-token", {
            "type": "dom-keyframe",
            "payload": {"root": {"nodeId": 1}},
            "sender": "host-browser",
            "version": 1,
            "epoch": 9,
            "sequence": 42,
            "reason": "join",
        }, "host-browser"),
        ("share-token", {
            "type": "dom-delta",
            "payload": {"mutations": []},
            "sender": "host-browser",
            "version": 1,
            "epoch": 9,
            "sequence": 43,
            "baseSequence": 42,
            "reason": "gap",
        }, "host-browser"),
    ]
    assert recorded == [("share-token", broadcasts[0][1]), ("share-token", broadcasts[1][1])]


def test_share_replay_keyframe_cache_fans_out_to_late_viewers_and_prunes():
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    active_tokens = {"share-token"}
    server.app = SimpleNamespace(verify_share_token=lambda token: {"token": token} if token in active_tokens else None)
    server.share_replay_keyframes_lock = server_module.threading.Lock()
    server.share_replay_keyframes = {}
    server.share_replay_deltas_lock = server_module.threading.Lock()
    server.share_replay_deltas = {}

    server.record_share_replay_keyframe("share-token", {
        "type": "dom-keyframe",
        "version": 1,
        "payload": {
            "digest": "sha256:abc",
            "root": {"nodeId": 1, "tag": "div", "attrs": {"id": "appRoot"}, "children": []},
            "url": "/share/abc123#t=secret-token",
        },
        "sender": "host-browser",
        "epoch": 4,
        "sequence": 9,
        "reason": "join",
    })

    latest = server.latest_share_replay_keyframe("share-token")
    assert latest["type"] == "dom-keyframe"
    assert latest["version"] == 1
    assert latest["epoch"] == 4
    assert latest["sequence"] == 9
    assert latest["payload"]["digest"] == "sha256:abc"
    assert latest["payload"]["url"] == "..."

    viewer = server_module.ShareViewerConnection(object(), "viewer-b")
    assert server.enqueue_latest_share_replay_keyframe("share-token", viewer)
    queued = viewer.frames.get_nowait()
    assert b'"ch":"ui"' in queued
    assert b'"type":"dom-keyframe"' in queued
    assert b'"digest":"sha256:abc"' in queued
    assert b"secret-token" not in queued
    assert b"/share/abc123" not in queued

    active_tokens.clear()
    assert server.latest_share_replay_keyframe("share-token") is None
    assert server.share_replay_keyframes == {}

    active_tokens.add("share-token")
    server.record_share_replay_keyframe("share-token", {"type": "dom-keyframe", "payload": {"digest": "sha256:def"}})
    active_tokens.clear()
    server.prune_inactive_share_replay_keyframes()
    assert server.share_replay_keyframes == {}


def test_share_replay_delta_ring_replays_contiguous_frames_to_late_viewer():
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    active_tokens = {"share-token"}
    server.app = SimpleNamespace(verify_share_token=lambda token: {"token": token} if token in active_tokens else None)
    server.share_replay_keyframes_lock = server_module.threading.Lock()
    server.share_replay_keyframes = {}
    server.share_replay_deltas_lock = server_module.threading.Lock()
    server.share_replay_deltas = {}
    requests = []
    server.broadcast_share_ui = lambda token, message, skip_client_id="": requests.append((token, message, skip_client_id))

    server.record_share_replay_keyframe("share-token", {
        "type": "dom-keyframe",
        "version": 1,
        "payload": {"digest": "sha256:key", "root": {"nodeId": 1}},
        "sender": "host-browser",
        "epoch": 7,
        "sequence": 10,
    })
    server.record_share_replay_delta("share-token", {
        "type": "dom-delta",
        "version": 1,
        "payload": {"mutations": [{"type": "text", "value": "/share/abc123#t=secret-token"}]},
        "sender": "host-browser",
        "epoch": 7,
        "sequence": 11,
        "baseSequence": 10,
    })
    server.record_share_replay_delta("share-token", {
        "type": "dom-delta",
        "version": 1,
        "payload": {"mutations": [{"type": "attribute", "name": "class", "value": "ready"}]},
        "sender": "host-browser",
        "epoch": 7,
        "sequence": 12,
        "baseSequence": 11,
    })

    viewer = server_module.ShareViewerConnection(object(), "viewer-b")
    assert server.enqueue_share_replay_frames_for_viewer("share-token", viewer)
    queued = [server_ws_json(viewer.frames.get_nowait()) for _ in range(3)]

    assert [frame["type"] for frame in queued] == ["dom-keyframe", "dom-delta", "dom-delta"]
    assert [frame.get("sequence") for frame in queued] == [10, 11, 12]
    assert queued[1]["payload"]["mutations"][0]["value"] == "..."
    assert requests == []


def test_share_replay_delta_ring_overflow_requests_keyframe_without_partial_deltas():
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    active_tokens = {"share-token"}
    server.app = SimpleNamespace(verify_share_token=lambda token: {"token": token} if token in active_tokens else None)
    server.share_replay_keyframes_lock = server_module.threading.Lock()
    server.share_replay_keyframes = {}
    server.share_replay_deltas_lock = server_module.threading.Lock()
    server.share_replay_deltas = {}
    requests = []
    server.broadcast_share_ui = lambda token, message, skip_client_id="": requests.append((token, message, skip_client_id))

    server.record_share_replay_keyframe("share-token", {
        "type": "dom-keyframe",
        "version": 1,
        "payload": {"digest": "sha256:key", "root": {"nodeId": 1}},
        "sender": "host-browser",
        "epoch": 3,
        "sequence": 0,
    })
    for sequence in range(1, server_module.SHARE_REPLAY_DELTA_RING_LIMIT + 3):
        server.record_share_replay_delta("share-token", {
            "type": "dom-delta",
            "version": 1,
            "payload": {"mutations": [{"type": "text", "value": str(sequence)}]},
            "sender": "host-browser",
            "epoch": 3,
            "sequence": sequence,
            "baseSequence": sequence - 1,
        })

    viewer = server_module.ShareViewerConnection(object(), "viewer-overflow")
    assert not server.enqueue_share_replay_frames_for_viewer("share-token", viewer)
    assert viewer.frames.empty()
    assert requests == [("share-token", {
        "type": "dom-keyframe-request",
        "payload": {"reason": "join", "viewerId": "viewer-overflow"},
        "sender": "__server__",
        "version": 1,
        "reason": "join",
    }, "viewer-overflow")]


def test_share_replay_delta_ring_prunes_stopped_shares():
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    active_tokens = {"share-token"}
    server.app = SimpleNamespace(verify_share_token=lambda token: {"token": token} if token in active_tokens else None)
    server.share_replay_keyframes_lock = server_module.threading.Lock()
    server.share_replay_keyframes = {}
    server.share_replay_deltas_lock = server_module.threading.Lock()
    server.share_replay_deltas = {}

    server.record_share_replay_keyframe("share-token", {
        "type": "dom-keyframe",
        "payload": {"digest": "sha256:key"},
        "epoch": 1,
        "sequence": 1,
    })
    server.record_share_replay_delta("share-token", {
        "type": "dom-delta",
        "payload": {"mutations": []},
        "epoch": 1,
        "sequence": 2,
        "baseSequence": 1,
    })
    assert "share-token" in server.share_replay_deltas

    active_tokens.clear()
    server.prune_inactive_share_replay_keyframes()
    server.prune_inactive_share_replay_deltas()

    assert server.share_replay_keyframes == {}
    assert server.share_replay_deltas == {}


def test_share_replay_live_delta_overflow_requests_keyframe():
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    active_tokens = {"share-token"}
    server.app = SimpleNamespace(verify_share_token=lambda token: {"token": token} if token in active_tokens else None)
    server.share_ui_clients_lock = server_module.threading.Lock()
    viewer = server_module.ShareViewerConnection(object(), "viewer-slow")
    viewer.queued_bytes = server_module.SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES - 4
    server.share_ui_clients = {"share-token": {viewer}}
    server.share_upstreams_lock = server_module.threading.Lock()
    upstream = server_module.ShareTerminalUpstream(server, "share-token", "6")
    terminal_viewer = server_module.ShareViewerConnection(object(), "viewer-slow")
    upstream.viewers.add(terminal_viewer)
    server.share_upstreams = {("share-token", "6"): upstream}
    requests = []
    server.request_share_replay_keyframe = lambda token, reason="gap", skip_client_id="", viewer_id="": requests.append((token, reason, skip_client_id, viewer_id))

    server_module.TmuxWebtermHTTPServer.broadcast_share_ui(server, "share-token", {
        "type": "dom-delta",
        "payload": {"mutations": [{"type": "text", "value": "large-enough"}]},
        "sender": "host-browser",
        "epoch": 1,
        "sequence": 2,
        "baseSequence": 1,
    })

    assert viewer.frames.empty()
    assert requests == [("share-token", "backpressure", "", "")]
    assert terminal_viewer.frames.empty()
    assert not terminal_viewer.is_closed()

    upstream.broadcast(b"terminal bytes continue")

    assert not terminal_viewer.frames.empty()
    assert not terminal_viewer.is_closed()


def test_share_viewer_connection_coalesces_latest_pointer_frames():
    connection = FakeShareConnection()
    viewer = server_module.ShareViewerConnection(connection, "viewer-pointer")
    first = server_module.share_ui_frame({"type": "pointer", "payload": {"x": 1, "y": 1}})
    second = server_module.share_ui_frame({"type": "pointer", "payload": {"x": 2, "y": 2}})

    assert viewer.enqueue(first) == "queued"
    assert viewer.enqueue(second) == "queued"
    viewer.close("done")
    viewer.write_loop()

    assert [server_ws_json(frame)["payload"] for frame in connection.sent] == [{"x": 2, "y": 2}]
    assert viewer.queued_bytes == 0

    replay_viewer = server_module.ShareViewerConnection(object(), "viewer-replay-pointer")
    replay_first = server_module.share_ui_frame({"type": "dom-delta", "payload": {"pointer": {"x": 3, "y": 3}}, "sequence": 3})
    replay_second = server_module.share_ui_frame({"type": "dom-delta", "payload": {"pointer": {"x": 4, "y": 4}}, "sequence": 4})
    assert replay_viewer.enqueue(replay_first) == "queued"
    assert replay_viewer.enqueue(replay_second) == "queued"
    assert [server_ws_json(replay_viewer.frames.get_nowait())["sequence"] for _ in range(2)] == [3, 4]

    click_connection = FakeShareConnection()
    click_viewer = server_module.ShareViewerConnection(click_connection, "viewer-pointer-click")
    click_frame = server_module.share_ui_frame({"type": "pointer", "payload": {"x": 5, "y": 5, "click": True}})
    move_frame = server_module.share_ui_frame({"type": "pointer", "payload": {"x": 6, "y": 6}})
    assert click_viewer.enqueue(click_frame) == "queued"
    assert click_viewer.enqueue(move_frame) == "queued"
    click_viewer.close("done")
    click_viewer.write_loop()
    assert [server_ws_json(frame)["payload"]["x"] for frame in click_connection.sent] == [5, 6]


def test_share_viewer_connection_prioritizes_latest_pointer_around_keyframes():
    connection = FakeShareConnection()
    viewer = server_module.ShareViewerConnection(connection, "viewer-priority-pointer")
    keyframe = server_module.share_ui_frame({
        "type": "dom-keyframe",
        "payload": {"digest": "sha256:pending", "root": {"nodeId": 1}},
        "epoch": 4,
        "sequence": 23,
    })
    pointer = server_module.share_ui_frame({"type": "pointer", "payload": {"x": 7, "y": 8}})

    assert viewer.enqueue_reset_frame(keyframe) == "queued"
    assert viewer.enqueue(pointer) == "queued"
    assert viewer.queued_bytes == len(keyframe)
    viewer.close("done")
    viewer.write_loop()

    sent = [server_ws_json(frame) for frame in connection.sent]
    assert [frame["type"] for frame in sent] == ["pointer", "dom-keyframe"]
    assert sent[0]["payload"] == {"x": 7, "y": 8}
    assert sent[1]["payload"]["digest"] == "sha256:pending"


def test_share_replay_keyframe_broadcast_resets_backlogged_viewer_queue():
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    server.share_ui_clients_lock = server_module.threading.Lock()
    viewer = server_module.ShareViewerConnection(object(), "viewer-backlogged")
    assert viewer.enqueue(b"x" * (server_module.SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES - 4)) == "queued"
    server.share_ui_clients = {"share-token": {viewer}}
    server.share_upstreams_lock = server_module.threading.Lock()
    server.share_upstreams = {}
    requests = []
    server.request_share_replay_keyframe = lambda token, reason="gap", skip_client_id="", viewer_id="": requests.append((token, reason, skip_client_id, viewer_id))

    server_module.TmuxWebtermHTTPServer.broadcast_share_ui(server, "share-token", {
        "type": "dom-keyframe",
        "payload": {
            "digest": "sha256:key",
            "root": {"nodeId": 1, "tag": "div", "attrs": {"id": "appRoot"}, "children": []},
        },
        "sender": "host-browser",
        "epoch": 4,
        "sequence": 22,
        "reason": "gap",
    })

    queued = server_ws_json(viewer.frames.get_nowait())
    assert queued["type"] == "dom-keyframe"
    assert queued["sequence"] == 22
    assert queued["payload"]["digest"] == "sha256:key"
    assert viewer.frames.empty()
    assert requests == []
    assert not viewer.is_closed()

    protected_connection = FakeShareConnection()
    protected_viewer = server_module.ShareViewerConnection(protected_connection, "viewer-protected-keyframe")
    keyframe = server_module.share_ui_frame({
        "type": "dom-keyframe",
        "payload": {"digest": "sha256:pending", "root": {"nodeId": 1}},
        "epoch": 4,
        "sequence": 23,
    })
    replacement_keyframe = server_module.share_ui_frame({
        "type": "dom-keyframe",
        "payload": {"digest": "sha256:replacement", "root": {"nodeId": 1}},
        "epoch": 5,
        "sequence": 24,
    })
    assert protected_viewer.enqueue_reset_frame(keyframe) == "queued"
    assert protected_viewer.enqueue_reset_frame(replacement_keyframe) == "queued"
    pointer_frame = server_module.share_ui_frame({"type": "pointer", "payload": {"x": 9, "y": 10}})
    assert protected_viewer.enqueue(pointer_frame) == "queued"
    assert protected_viewer.enqueue(b"x" * server_module.SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES) == "overflow"
    queued = server_ws_json(protected_viewer.frames.get_nowait())
    assert queued["type"] == "dom-keyframe"
    assert queued["sequence"] == 24
    assert queued["payload"]["digest"] == "sha256:replacement"
    protected_viewer.close("done")
    protected_viewer.write_loop()
    sent = [server_ws_json(frame) for frame in protected_connection.sent]
    assert [frame["type"] for frame in sent] == ["pointer"]
    assert sent[0]["payload"] == {"x": 9, "y": 10}


def test_share_viewers_receive_host_terminal_dimensions():
    bootstrap_body = inspect.getsource(Handler.share_bootstrap_payload)
    resize_body = inspect.getsource(server_module.TmuxWebtermHTTPServer.record_host_pty_dimensions)
    upstream_resize_body = inspect.getsource(server_module.ShareTerminalUpstream.update_dimensions)

    assert "host_pty_dimensions_for_session(session)" in bootstrap_body
    assert '"hostDims": {"rows": rows, "cols": cols}' in bootstrap_body
    assert '"type": SHARE_MIRROR_FRAME_TERMINAL_HOST_RESIZE' in resize_body
    assert '"rows": dimensions[0]' in resize_body
    assert '"cols": dimensions[1]' in resize_body
    assert "upstream.update_dimensions(*dimensions, refresh=False)" in resize_body
    assert "upstream.request_refresh_client()" in resize_body
    assert resize_body.index("self.broadcast_share_ui") < resize_body.index("upstream.request_refresh_client()")
    assert "refresh: bool = True" in upstream_resize_body
    assert "self.request_refresh_client()" in upstream_resize_body
    server = object.__new__(server_module.TmuxWebtermHTTPServer)
    server.host_pty_dimensions_lock = server_module.threading.Lock()
    server.host_pty_dimensions = {}
    server.share_upstreams_lock = server_module.threading.Lock()
    order = []
    upstream = SimpleNamespace(
        update_dimensions=lambda rows, cols, refresh=True: order.append(("update", rows, cols, refresh)),
        request_refresh_client=lambda: order.append(("refresh",)),
    )
    server.share_upstreams = {("share-token", "6"): upstream}
    server.broadcast_share_ui = lambda token, message: order.append(("broadcast", token, message["type"], message["payload"]))

    server_module.TmuxWebtermHTTPServer.record_host_pty_dimensions(server, "6", 33, 111)

    assert order == [
        ("update", 33, 111, False),
        ("broadcast", "share-token", "terminal-host-resize", {"session": "6", "rows": 33, "cols": 111}),
        ("refresh",),
    ]


def test_share_terminal_upstream_resize_uses_shared_live_process_signal_guard(monkeypatch):
    calls = []
    upstream = server_module.ShareTerminalUpstream(SimpleNamespace(), "share-token", "6")
    upstream.slave_fd = 11
    upstream.process = SimpleNamespace(pid=123, poll=lambda: None)
    upstream.request_refresh_client = lambda: calls.append(("refresh",))
    monkeypatch.setattr(server_module, "set_pty_size", lambda fd, rows, cols: calls.append(("size", fd, rows, cols)))
    monkeypatch.setattr(server_module.os, "killpg", lambda pid, sig: calls.append(("signal", pid, sig)))

    upstream.update_dimensions(24, 80)

    assert calls == [("size", 11, 24, 80), ("signal", 123, server_module.signal.SIGWINCH), ("refresh",)]
    calls.clear()
    upstream.process = SimpleNamespace(pid=456, poll=lambda: 0)

    upstream.update_dimensions(31, 101, refresh=False)

    assert calls == [("size", 11, 31, 101)]


def test_share_terminal_upstream_refreshes_existing_viewer_attach():
    upstream = server_module.ShareTerminalUpstream(SimpleNamespace(), "token", "6")
    upstream.reader_thread = object()
    calls = []
    upstream.request_refresh_client = lambda: calls.append("refresh")
    viewer = object()

    upstream.add_viewer(viewer)

    assert viewer in upstream.viewers
    assert calls == ["refresh"]


def test_share_terminal_upstream_start_closes_openpty_fds_on_setup_error(monkeypatch):
    master_fd = os.open(os.devnull, os.O_RDONLY)
    slave_fd = os.open(os.devnull, os.O_RDONLY)
    upstream = server_module.ShareTerminalUpstream(
        SimpleNamespace(host_pty_dimensions_for_session=lambda _session: (24, 80)),
        "token",
        "6",
    )
    monkeypatch.setattr(server_module.pty, "openpty", lambda: (master_fd, slave_fd))
    monkeypatch.setattr(server_module, "set_pty_size", lambda *_args: (_ for _ in ()).throw(OSError("pty failed")))

    with pytest.raises(OSError):
        upstream.start_locked()

    assert upstream.master_fd is None
    assert upstream.slave_fd is None
    for fd in (master_fd, slave_fd):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_share_terminal_reader_uses_owned_fd_duplicate_before_reading():
    body = inspect.getsource(server_module.ShareTerminalUpstream.reader_loop)

    assert "reader_fd = os.dup(master_fd)" in body
    assert "if self.stop_event.is_set():" in body
    assert "os.read(reader_fd" in body
    assert "os.close(reader_fd)" in body


def test_bridge_shared_tmux_cleans_registration_when_add_viewer_fails():
    calls = []
    upstream = SimpleNamespace(add_viewer=lambda _viewer: (_ for _ in ()).throw(OSError("openpty failed")))
    handler = object.__new__(Handler)
    handler.connection = FakeShareConnection()
    handler.server = SimpleNamespace(
        share_terminal_upstream=lambda token, session: upstream,
        release_share_terminal_upstream=lambda token, session, viewer: calls.append(("release", token, session, viewer.client_id)),
        app=SimpleNamespace(unregister_share_viewer=lambda token, viewer_id: calls.append(("unregister", token, viewer_id))),
        broadcast_share_status=lambda token: calls.append(("broadcast", token)),
    )

    Handler.bridge_shared_tmux(handler, "share-token", "6", "viewer-1")

    assert calls == [
        ("release", "share-token", "6", "viewer-1"),
        ("unregister", "share-token", "viewer-1"),
        ("broadcast", "share-token"),
    ]

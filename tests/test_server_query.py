import inspect
import io
import json
import os
from http import HTTPStatus
from types import SimpleNamespace


from yolomux_lib import server as server_module
from yolomux_lib.common import error_payload
from yolomux_lib.server import Handler
from yolomux_lib.server import parse_query_float
from yolomux_lib.server import parse_query_int
from yolomux_lib.server import parse_repo_refs_param
from yolomux_lib.server import ws_resize_dimensions
from yolomux_lib.web import html_page


def test_parse_repo_refs_param_decodes_per_repo_overrides():
    # C6: decode the per-repo FROM/TO JSON map; keep only well-formed string ref pairs.
    raw = json.dumps({"/repo/a": {"from": "abc123", "to": "current"}, "/repo/b": {"from": "  ", "to": "HEAD"}})
    parsed = parse_repo_refs_param(raw)
    assert parsed == {"/repo/a": {"from": "abc123", "to": "current"}, "/repo/b": {"to": "HEAD"}}


def test_error_payload_normalizes_status_and_context():
    assert error_payload("bad", path="/tmp/a", session="6", status=HTTPStatus.BAD_REQUEST) == {
        "error": "bad",
        "path": "/tmp/a",
        "session": "6",
        "status": 400,
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
        ("json", HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer", "status": 400}),
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


def test_html_uses_browser_highlight_js_bundle():
    html = html_page(["6"], "admin")

    assert "highlight.js@11.9.0/lib/common.min.js" not in html
    assert "highlightjs/cdn-release@11.9.0/build/highlight.min.js" in html
    assert "highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css" in html


def test_handle_upload_enforces_live_app_size_limit():
    app = SimpleNamespace(upload_max_bytes=lambda: 5, upload_files=lambda *_args: (_ for _ in ()).throw(AssertionError("upload_files should not run")))
    handler = SimpleNamespace(
        headers={"Content-Length": "6", "Content-Type": "multipart/form-data; boundary=x"},
        rfile=io.BytesIO(b"123456"),
        server=SimpleNamespace(app=app),
        close_connection=False,
    )

    payload, status = Handler.handle_upload(handler, "6")

    assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert payload == {"session": "6", "error": "upload is too large; limit is 5 bytes"}
    assert handler.close_connection is True


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
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append(("json", status, value))
    handler.write_text = lambda value, status=HTTPStatus.OK, content_type="text/plain; charset=utf-8": writes.append(("text", status, value, content_type))
    handler.write_html = lambda value: writes.append(("html", HTTPStatus.OK, value))
    handler.write_app_result = lambda result: handler.write_json(result[0], status=result[1])
    handler.reject_forbidden = lambda identity, required_role: writes.append(("forbidden", HTTPStatus.FORBIDDEN, identity.role, required_role))
    return handler, calls, writes


def test_do_get_routes_authenticated_json_and_stream_handlers():
    app = SimpleNamespace(
        transcripts_payload=lambda force=False: {"transcripts": [], "force": force},
        activity_summary_payload=lambda force=False, locale="en": {"force": force, "locale": locale},
    )

    handler, calls, writes = route_handler("/api/transcripts?force=1", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"transcripts": [], "force": True})]

    handler, calls, writes = route_handler("/api/activity-summary?force=1&locale=ja", app)
    Handler.do_GET(handler)
    assert calls == [("require_auth", "readonly")]
    assert writes == [("json", HTTPStatus.OK, {"force": True, "locale": "ja"})]

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


def test_do_post_routes_event_with_readonly_auth_and_fs_handlers():
    handler, calls, writes = route_handler("/api/event")
    handler.handle_client_event = lambda: ({"ok": True}, HTTPStatus.ACCEPTED)

    Handler.do_POST(handler)

    assert calls == [("require_auth_for_post", "/api/event")]
    assert writes == [("json", HTTPStatus.ACCEPTED, {"ok": True})]

    handler, calls, writes = route_handler("/api/fs/delete")
    handler.handle_fs_delete = lambda parsed: writes.append(("fs-delete", parsed.path))

    Handler.do_POST(handler)

    assert calls == [("require_auth_for_post", "/api/fs/delete")]
    assert writes == [("fs-delete", "/api/fs/delete")]

    handler, calls, writes = route_handler("/api/fs/batch")
    handler.handle_fs_batch = lambda parsed: writes.append(("fs-batch", parsed.path))

    Handler.do_POST(handler)

    assert calls == [("require_auth_for_post", "/api/fs/batch")]
    assert writes == [("fs-batch", "/api/fs/batch")]

    app = SimpleNamespace(update_client_watch_roots=lambda roots: {"ok": True, "roots": roots})
    handler, calls, writes = route_handler("/api/watch/roots", app)
    handler.read_json_body = lambda limit: {"roots": ["/repo"]}

    Handler.do_POST(handler)

    assert calls == [("require_auth_for_post", "/api/watch/roots")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "roots": {"roots": ["/repo"]}})]

    app = SimpleNamespace(run_file_drop_action=lambda payload: ({"ok": True, "action": payload["action"]}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/drop-action/run", app)
    handler.read_json_body = lambda limit: {"action": "server-info", "paths": ["/repo/README.md"]}

    Handler.do_POST(handler)

    assert calls == [("require_auth_for_post", "/api/drop-action/run")]
    assert writes == [("json", HTTPStatus.OK, {"ok": True, "action": "server-info"})]

    app = SimpleNamespace(tmux_copy_selection=lambda session: ({"session": session, "copied": True}, HTTPStatus.OK))
    handler, calls, writes = route_handler("/api/tmux-copy-selection?session=6", app)

    Handler.do_POST(handler)

    assert calls == [("require_auth_for_post", "/api/tmux-copy-selection")]
    assert writes == [("json", HTTPStatus.OK, {"session": "6", "copied": True})]


def batch_handler(payload):
    body = json.dumps(payload).encode("utf-8")
    writes = []
    handler = object.__new__(Handler)
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.write_json = lambda value, status=HTTPStatus.OK: writes.append((status, value))
    return handler, writes


def test_handle_fs_batch_returns_per_item_results(monkeypatch):
    monkeypatch.setattr(server_module.filesystem, "list_directory", lambda path: {"path": path, "entries": [{"name": "a"}]})

    def path_info(path):
        if path == "/missing":
            raise server_module.FilesystemError("path not found: /missing", status=404)
        return {"path": path, "kind": "dir"}

    monkeypatch.setattr(server_module.filesystem, "path_info", path_info)
    handler, writes = batch_handler({
        "requests": [
            {"id": "root", "type": "list", "path": "/repo"},
            {"id": "info", "type": "info", "path": "/repo"},
            {"id": "missing", "type": "info", "path": "/missing"},
            {"id": "bad", "type": "read", "path": "/repo/README.md"},
        ],
    })

    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes == [(HTTPStatus.OK, {"responses": [
        {"id": "root", "ok": True, "status": 200, "payload": {"path": "/repo", "entries": [{"name": "a"}]}},
        {"id": "info", "ok": True, "status": 200, "payload": {"path": "/repo", "kind": "dir"}},
        {"id": "missing", "ok": False, "status": 404, "error": "path not found: /missing", "path": "/missing"},
        {"id": "bad", "ok": False, "status": 400, "error": "unsupported fs batch operation", "path": "/repo/README.md"},
    ]})]


def test_handle_fs_batch_rejects_invalid_shape():
    handler, writes = batch_handler({"requests": "nope"})

    Handler.handle_fs_batch(handler, SimpleNamespace(path="/api/fs/batch"))

    assert writes == [(HTTPStatus.BAD_REQUEST, {"error": "requests must be a list", "status": 400})]


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


def test_handle_ws_payload_resize_sets_pty_and_signals_for_admin_only(monkeypatch):
    calls = []
    process = SimpleNamespace(pid=123)
    handler = SimpleNamespace(server=SimpleNamespace(app=SimpleNamespace(tmux_scroll=lambda *_args: None)))
    monkeypatch.setattr(server_module, "set_pty_size", lambda fd, rows, cols: calls.append(("size", fd, rows, cols)))
    monkeypatch.setattr(server_module.os, "killpg", lambda pid, sig: calls.append(("signal", pid, sig)))

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "resize", "rows": 24, "cols": 80}).encode(), readonly=False)

    assert calls == [("size", 11, 24, 80), ("signal", 123, server_module.signal.SIGWINCH)]
    calls.clear()

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "resize", "rows": 30, "cols": 100}).encode(), readonly=True)

    assert calls == []


def test_websocket_rejects_share_token_for_other_session():
    writes = []
    handler = SimpleNamespace(
        share_session=lambda: "6",
        server=SimpleNamespace(app=SimpleNamespace(sessions=["6", "7"])),
        write_text=lambda value, status=HTTPStatus.OK: writes.append((status, value)),
    )

    Handler.websocket(handler, SimpleNamespace(query="session=7"))

    assert writes == [(HTTPStatus.FORBIDDEN, "share token is scoped to a different session\n")]


def test_write_sse_json_formats_event_stream():
    handler = SimpleNamespace(wfile=io.BytesIO())

    Handler.write_sse_json(handler, "delta", {"text": "hello"})

    assert handler.wfile.getvalue() == b'event: delta\ndata: {"text": "hello"}\n\n'


def test_server_source_wires_routing_ws_readonly_and_pty_setup():
    # Scoped per method (inspect, not full-file string offsets): POST routing in do_POST, the
    # read-only WS attach in websocket(), and the readonly `-r` + pty sizing in bridge_tmux.
    post_body = inspect.getsource(Handler.do_POST)
    ws_body = inspect.getsource(Handler.websocket)
    bridge_body = inspect.getsource(Handler.bridge_tmux)

    assert 'if parsed.path == "/api/upload":' in post_body
    assert 'if parsed.path == "/api/event":' in post_body
    assert "self.bridge_tmux(session, readonly=self.auth_readonly())" in ws_body
    assert "if readonly:" in bridge_body and 'attach_args.append("-r")' in bridge_body
    assert "set_pty_size(slave_fd, initial_rows, initial_cols)" in bridge_body

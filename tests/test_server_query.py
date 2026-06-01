import io
import json
import os
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")

from yolomux_lib import server as server_module
from yolomux_lib.server import Handler
from yolomux_lib.server import parse_query_float
from yolomux_lib.server import parse_query_int
from yolomux_lib.server import ws_resize_dimensions


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


def test_websocket_bridge_terminates_tmux_process_group():
    source = Path("yolomux_lib/server.py").read_text(encoding="utf-8")
    bridge_start = source.index("    def bridge_tmux")
    bridge_end = source.index("    def read_initial_ws_payloads")
    bridge_body = source[bridge_start:bridge_end]

    assert "terminate_process_group(process)" in bridge_body
    assert "process.terminate()" not in bridge_body
    assert "process.kill()" not in bridge_body


def test_websocket_frame_reads_are_timeout_wrapped():
    source = Path("yolomux_lib/server.py").read_text(encoding="utf-8")

    assert "WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS = 5.0" in source
    assert "def read_ws_frame_with_timeout" in source
    assert "self.connection.settimeout(WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS)" in source
    assert source.count("read_ws_frame(self.rfile)") == 1
    assert source.count("self.read_ws_frame_with_timeout()") == 2


def test_websocket_resize_dimensions_are_clamped():
    assert ws_resize_dimensions({"rows": 9999, "cols": 0}, 36, 120) == (1000, 1)
    assert ws_resize_dimensions({"rows": 24, "cols": 80}, 36, 120) == (24, 80)
    assert ws_resize_dimensions({"rows": True, "cols": 80}, 36, 120) is None
    assert ws_resize_dimensions({"rows": "24", "cols": 80}, 36, 120) is None


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


def test_handle_ws_payload_resize_sets_pty_and_signals(monkeypatch):
    calls = []
    process = SimpleNamespace(pid=123)
    handler = SimpleNamespace(server=SimpleNamespace(app=SimpleNamespace(tmux_scroll=lambda *_args: None)))
    monkeypatch.setattr(server_module, "set_pty_size", lambda fd, rows, cols: calls.append(("size", fd, rows, cols)))
    monkeypatch.setattr(server_module.os, "killpg", lambda pid, sig: calls.append(("signal", pid, sig)))

    Handler.handle_ws_payload(handler, "6", 10, 11, process, json.dumps({"type": "resize", "rows": 24, "cols": 80}).encode(), readonly=True)

    assert calls == [("size", 11, 24, 80), ("signal", 123, server_module.signal.SIGWINCH)]


def test_write_sse_json_formats_event_stream():
    handler = SimpleNamespace(wfile=io.BytesIO())

    Handler.write_sse_json(handler, "delta", {"text": "hello"})

    assert handler.wfile.getvalue() == b'event: delta\ndata: {"text": "hello"}\n\n'


def test_server_source_wires_routing_ws_readonly_and_pty_setup():
    source = Path("yolomux_lib/server.py").read_text(encoding="utf-8")
    post_start = source.index("    def do_POST")
    post_end = source.index("    def read_urlencoded_form", post_start)
    post_body = source[post_start:post_end]
    bridge_start = source.index("    def bridge_tmux")
    bridge_end = source.index("    def read_initial_ws_payloads", bridge_start)
    bridge_body = source[bridge_start:bridge_end]

    assert 'if parsed.path == "/api/upload":' in post_body
    assert 'if parsed.path == "/api/event":' in post_body
    assert "self.bridge_tmux(session, readonly=self.auth_readonly())" in source
    assert "if readonly:\n            attach_args.append(\"-r\")" in bridge_body
    assert "set_pty_size(slave_fd, initial_rows, initial_cols)" in bridge_body

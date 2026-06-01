import os
from pathlib import Path

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")

from yolomux_lib.server import parse_query_int
from yolomux_lib.server import ws_resize_dimensions


def test_parse_query_int_defaults_and_valid_values():
    assert parse_query_int({}, "lines", 90) == (90, "")
    assert parse_query_int({"lines": ["12"]}, "lines", 90) == (12, "")


def test_parse_query_int_reports_bad_values():
    value, error = parse_query_int({"messages": ["many"]}, "messages", 40)

    assert value is None
    assert error == "messages must be an integer"


def test_websocket_bridge_terminates_tmux_process_group():
    source = Path("yolomux_lib/server.py").read_text(encoding="utf-8")
    bridge_start = source.index("    def bridge_tmux")
    bridge_end = source.index("    def read_initial_ws_payloads")
    bridge_body = source[bridge_start:bridge_end]

    assert "terminate_process_group(process)" in bridge_body
    assert "process.terminate()" not in bridge_body
    assert "process.kill()" not in bridge_body


def test_websocket_resize_dimensions_are_clamped():
    assert ws_resize_dimensions({"rows": 9999, "cols": 0}, 36, 120) == (1000, 1)
    assert ws_resize_dimensions({"rows": 24, "cols": 80}, 36, 120) == (24, 80)
    assert ws_resize_dimensions({"rows": True, "cols": 80}, 36, 120) is None
    assert ws_resize_dimensions({"rows": "24", "cols": 80}, 36, 120) is None

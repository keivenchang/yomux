# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import importlib.util
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "yostats_active_browser_window.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("yostats_active_browser_window", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_active_browser_window_requires_normal_login_and_tmp_output():
    tool = load_tool_module()

    args = tool.parse_args(["--output", "/tmp/window.json"])

    assert args.username is None
    assert args.duration == 60
    assert args.output == Path("/tmp/window.json")
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "YOLOMUX_TEST_AUTH_BYPASS" not in source
    assert 'parser.add_argument("--password",' not in source
    assert "password-env" not in source
    assert "auth_cookie_value" in source
    assert "install_local_auth_cookie" in source
    assert "refreshFileExplorerPanelTree(finderPanel, {force: true})" in source
    assert "ensureDirectoryRowExpanded" in source
    assert "panelSelector" in source
    assert "await new Promise(requestAnimationFrame)" in source
    assert "sourceTab" in source
    assert "sourceGroup?.querySelector('.pane-drag-handle')" in source
    assert "settle_browser_frames" in source
    assert "Drag YO!cost and retain only the interaction's own paint evidence" in source
    assert 'workload["drag"] = drag_yocost_pane(driver)' in source
    assert "sessions.filter(isTmuxSession)" in source
    assert "selectSession(yocostItemId" in source
    assert "clearClientPerfCounters(); performance.clearResourceTimings()" in source
    assert "runtime_service_pids" in source
    assert "X-YOLOmux-Measurement" in source
    assert "capture_measurement_metrics" in source


def test_active_browser_window_uses_configured_admin_without_plaintext_credentials(monkeypatch):
    tool = load_tool_module()
    readonly = tool.AuthUser(username="reader", password="stored-hash", role="readonly")
    admin = tool.AuthUser(username="operator", password="stored-hash", role="admin")
    monkeypatch.setattr(tool, "read_auth_users", lambda _path: (readonly, admin))

    assert tool.capture_auth_user(None) == admin
    assert tool.capture_auth_user("reader") == readonly
    with pytest.raises(RuntimeError, match="not configured"):
        tool.capture_auth_user("missing")


def test_active_browser_window_reads_service_records_without_starting_another_app(monkeypatch, tmp_path):
    tool = load_tool_module()
    services = tmp_path / "services"
    services.mkdir()
    (services / "statsd.service.json").write_text('{"service":"statsd","pid":0,"socket":"/tmp/statsd.sock"}\n', encoding="utf-8")
    (services / "jobd.service.json").write_text('{"service":"jobd","pid":31,"socket":"/tmp/jobd.sock"}\n', encoding="utf-8")
    monkeypatch.setattr(tool, "STATE_DIR", tmp_path)
    monkeypatch.setattr(tool, "process_is_alive", lambda pid: pid == 31)
    monkeypatch.setattr(tool, "service_pid_for_socket", lambda socket_path: 47 if socket_path == "/tmp/statsd.sock" else 0)

    assert tool.runtime_service_pids() == {"jobd": 31, "statsd": 47}
    assert "--print-runtime-report" not in TOOL_PATH.read_text(encoding="utf-8")


def test_active_browser_window_reads_only_generic_capture_metrics(monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "read_background_owner_debug_status", lambda: {"current_owner": {"control_socket": "/tmp/control.sock"}})
    monkeypatch.setattr(tool, "send_yolomux_control_request", lambda owner, request: {"ok": True, "performance": {"summary": []}})

    assert tool.capture_measurement_metrics() == {"summary": []}


def test_active_browser_window_bounds_resource_evidence_and_strips_query_values():
    tool = load_tool_module()

    resources = tool.bounded_api_resources(
        [
            {"name": "https://localhost:8881/api/fs/batch?session=private", "duration": 2.5, "transferSize": 42},
            {"name": "https://localhost:8881/static/yolomux.js", "duration": 4.0, "transferSize": 4},
            {"name": "https://localhost:8881/api/stats-snapshot?client=private", "duration": 3.0, "transferSize": 84},
        ],
        limit=1,
    )

    assert resources == [{"path": "/api/fs/batch", "duration": 2.5, "transferSize": 42}]


@pytest.mark.parametrize("value", ["0", "-1"])
def test_active_browser_window_rejects_nonpositive_duration(value):
    tool = load_tool_module()

    with pytest.raises(SystemExit):
        tool.parse_args(["--username", "operator", "--output", "/tmp/window.json", "--duration", value])


def test_benchmark_child_runs_in_its_own_process_group_and_is_group_stopped():
    tool = load_tool_module()
    source = TOOL_PATH.read_text(encoding="utf-8")

    # The benchmark subprocess must be isolated in its own session so a driver
    # exception or signal can stop the whole subtree without touching services.
    assert "start_new_session=True" in source
    assert "stop_benchmark_group" in source

    calls = []

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))

        def terminate(self):
            calls.append(("terminate",))

    monkey_calls = []
    original_killpg = tool.os.killpg
    tool.os.killpg = lambda pid, sig: monkey_calls.append((pid, sig))
    try:
        tool.stop_benchmark_group(FakeProcess())
    finally:
        tool.os.killpg = original_killpg
    assert monkey_calls[0] == (4242, tool.signal.SIGTERM)
    assert ("wait", 5) in calls


def test_main_installs_signal_handlers_deadline_and_selenium_timeouts():
    source = TOOL_PATH.read_text(encoding="utf-8")

    # A SIGTERM/SIGINT to the tool, or a wall-clock overrun, must route through
    # the same cleanup instead of orphaning Chrome + the benchmark child.
    assert "signal.signal(signal.SIGTERM, on_signal)" in source
    assert "signal.signal(signal.SIGINT, on_signal)" in source
    assert "signal.alarm(int(args.duration) + 180)" in source
    assert "set_page_load_timeout" in source
    assert "set_script_timeout" in source
    # The capture window arms the tracked-group overload watchdog.
    assert "GroupOverloadWatchdog" in source
    # The capture proves the pre-existing service ledger is unchanged.
    assert "ledger_snapshot()" in source
    assert "capture changed the service ledger" in source


def test_bounded_driver_quit_falls_back_to_killing_the_chromedriver_tree():
    tool = load_tool_module()
    kills = []

    class HangingDriver:
        class service:  # noqa: N801 - mirrors selenium attribute shape
            class process:  # noqa: N801
                pid = 5000

        def quit(self):
            time.sleep(60)

    original_kill = tool.os.kill
    original_descendants = tool.descendants_of
    tool.os.kill = lambda pid, sig: kills.append((pid, sig))
    tool.descendants_of = lambda pid: [5001, 5002]
    try:
        tool.bounded_driver_quit(HangingDriver(), quit_timeout=0.05)
    finally:
        tool.os.kill = original_kill
        tool.descendants_of = original_descendants

    assert kills == [(5000, tool.signal.SIGKILL), (5001, tool.signal.SIGKILL), (5002, tool.signal.SIGKILL)]

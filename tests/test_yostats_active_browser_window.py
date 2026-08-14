# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import importlib.util
import os
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import yostats_capture_common


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "yostats_active_browser_window.py"
CONTENTION_TOOL_PATH = ROOT / "tools" / "yostats_contention_benchmark.py"
FIXTURE_PORT = 41771
FIXTURE_BASE_URL = f"https://localhost:{FIXTURE_PORT}"


def test_capture_tools_share_proc_cpu_reader_and_positive_validators():
    active_source = TOOL_PATH.read_text(encoding="utf-8")
    contention_source = CONTENTION_TOOL_PATH.read_text(encoding="utf-8")

    assert "from tools.yostats_capture_common import positive_int, process_cpu_seconds" in active_source
    assert "from tools.yostats_capture_common import positive_float, positive_int, process_cpu_seconds" in contention_source
    assert "def process_cpu_seconds" not in active_source
    assert "def process_cpu_time_seconds" not in contention_source
    assert "process_cpu_seconds(pid)" in contention_source


def test_process_cpu_seconds_falls_back_to_portable_ps_without_procfs(tmp_path):
    def run(command, **_kwargs):
        assert command == ["ps", "-p", "4242", "-o", "time="]
        return subprocess.CompletedProcess(command, 0, "1-02:03:04.50\n", "")

    assert yostats_capture_common.process_cpu_seconds(
        4242,
        proc_root=tmp_path / "missing-proc",
        runner=run,
    ) == 93784.5


def load_tool_module():
    spec = importlib.util.spec_from_file_location("yostats_active_browser_window", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_active_browser_window_workload_source_contract():
    tool = load_tool_module()

    args = tool.parse_args(["--output", "/tmp/window.json"])

    assert args.username is None
    assert args.duration == 60
    assert args.workload == "active"
    assert args.output == Path("/tmp/window.json")
    idle_args = tool.parse_args(["--workload", "idle-yostats", "--duration", "60", "--output", "/tmp/idle-window.json"])
    assert idle_args.workload == "idle-yostats"
    assert idle_args.duration == 60
    source = TOOL_PATH.read_text(encoding="utf-8")
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
    assert "a slower earlier request cannot overwrite the final 5m/1s state" in source
    assert source.index('wait_for_exact_history(driver, 300, 1)') < source.index('driver.execute_script("setDebugGraphRange(1800); setDebugGraphResolutionOverride(10)")')
    assert "clearClientPerfCounters(); performance.clearResourceTimings()" in source
    assert "runtime_service_pids" in source
    assert "X-YOLOmux-Measurement" in source
    assert "capture_measurement_metrics" in source
    assert "install_measurement_fetch_header" in source
    assert "window.fetch =" in source
    assert 'choices=("active", "idle-yostats")' in source
    assert "prepare_idle_yostats_workload" in source
    assert "install_ticker_callback_counter" in source
    assert '"ticker_callbacks"' in source
    assert '"renderer"' in source


@pytest.mark.parametrize("flag", [
    "--password",
    "--password-env",
    "--token",
    "--token-env",
    "--auth-token",
    "--cookie",
    "--cookie-env",
    "--api-key",
    "--api-key-env",
])
def test_active_browser_window_rejects_credential_bearing_flags_before_any_browser_work(monkeypatch, flag):
    tool = load_tool_module()
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")

    with pytest.raises(SystemExit):
        tool.parse_args(["--output", "/tmp/window.json", flag, "plaintext"])
    assert set(vars(tool.parse_args(["--output", "/tmp/window.json"]))) == {
        "duration",
        "output",
        "port",
        "username",
        "workload",
    }


def test_active_browser_window_bypass_environment_does_not_skip_configured_cookie_install(monkeypatch):
    tool = load_tool_module()
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")
    user = tool.AuthUser(username="operator", password="stored-hash", role="admin")
    selected = []
    installed = []
    app_waits = []
    driver = SimpleNamespace(
        current_url=f"{FIXTURE_BASE_URL}/",
        get=lambda _url: None,
        execute_script=lambda script: ["one", "two"] if "sessions.filter(isTmuxSession)" in script else None,
    )

    class ImmediateWait:
        def until(self, predicate):
            return predicate(driver)

    monkeypatch.setattr(tool, "WebDriverWait", lambda *_args: ImmediateWait())
    monkeypatch.setattr(tool, "capture_auth_user", lambda username: selected.append(username) or user)
    monkeypatch.setattr(tool, "install_local_auth_cookie", lambda current, base_url, port, current_user: installed.append((current, base_url, port, current_user)))
    monkeypatch.setattr(tool, "wait_for_app", lambda current, sessions, timeout: app_waits.append((current, sessions, timeout)))

    assert tool.authenticate_and_open(driver, FIXTURE_BASE_URL, FIXTURE_PORT, None, timeout=20) == ["one", "two"]
    assert selected == [None]
    assert installed == [(driver, FIXTURE_BASE_URL, FIXTURE_PORT, user)]
    assert app_waits == [(driver, ["one", "two"], 20)]


@pytest.mark.parametrize("output", [Path("/outside/window.json"), Path("/tmp/../outside-window.json")])
def test_active_browser_window_rejects_non_tmp_output_before_chrome_starts(monkeypatch, capsys, output):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "parse_args", lambda: SimpleNamespace(output=output, port=FIXTURE_PORT, duration=60, workload="active", username=None))
    monkeypatch.setattr(tool, "find_chrome", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(tool, "listener_pid", lambda _port: 1)
    monkeypatch.setattr(tool, "runtime_service_pids", lambda: {})
    monkeypatch.setattr(tool, "ledger_snapshot", lambda: {})
    monkeypatch.setattr(tool.webdriver, "Chrome", lambda **_kwargs: pytest.fail("Chrome must not start for an unsafe output path"))

    assert tool.main() == 2
    assert "output must be under /tmp" in capsys.readouterr().err


def test_active_browser_window_sigterm_cleans_up_once(monkeypatch):
    tool = load_tool_module()
    signals = {}
    cleanup_calls = []
    driver = SimpleNamespace(
        service=SimpleNamespace(process=SimpleNamespace(pid=4321)),
        execute_cdp_cmd=lambda *_args: None,
        set_page_load_timeout=lambda _seconds: None,
        set_script_timeout=lambda _seconds: None,
    )
    monkeypatch.setattr(tool, "parse_args", lambda: SimpleNamespace(output=Path("/tmp/window.json"), port=FIXTURE_PORT, duration=60, workload="active", username=None))
    monkeypatch.setattr(tool, "find_chrome", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(tool, "listener_pid", lambda _port: 1)
    monkeypatch.setattr(tool, "runtime_service_pids", lambda: {})
    monkeypatch.setattr(tool, "ledger_snapshot", lambda: {})
    monkeypatch.setattr(tool.webdriver, "Chrome", lambda **_kwargs: driver)
    monkeypatch.setattr(tool, "bounded_driver_quit", lambda candidate: cleanup_calls.append(candidate))
    monkeypatch.setattr(tool.signal, "signal", lambda signum, callback: signals.__setitem__(signum, callback))
    monkeypatch.setattr(tool.signal, "alarm", lambda _seconds: None)
    monkeypatch.setattr(tool, "GroupOverloadWatchdog", lambda **_kwargs: SimpleNamespace(run=lambda _deadline: None))

    class SignalThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            for _attempt in range(2):
                try:
                    signals[tool.signal.SIGTERM](tool.signal.SIGTERM, None)
                except SystemExit:
                    pass
            raise SystemExit(143)

    monkeypatch.setattr(tool.threading, "Thread", SignalThread)

    with pytest.raises(SystemExit, match="143"):
        tool.main()
    assert cleanup_calls == [driver]


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


def test_active_browser_window_reads_only_generic_capture_metrics_from_the_target_server(monkeypatch):
    tool = load_tool_module()
    stale_owner = {"port": 7770, "control_socket": "/tmp/stale-owner.sock"}
    target_server = {"port": 7772, "control_socket": "/tmp/target-server.sock"}
    monkeypatch.setattr(tool, "read_background_owner_debug_status", lambda: {"current_owner": stale_owner, "generations": [stale_owner, target_server]})
    requests = []
    monkeypatch.setattr(tool, "send_yolomux_control_request", lambda server, request: requests.append((server, request)) or {"ok": True, "performance": {"summary": []}})

    assert tool.capture_measurement_metrics(7772) == {"summary": []}
    assert requests == [(target_server, {"action": "runtime_measurement_metrics", "scope": "capture"})]


def test_active_browser_window_rejects_a_missing_target_server_generation(monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "read_background_owner_debug_status", lambda: {"generations": [{"port": 7770, "control_socket": "/tmp/stale-owner.sock"}]})

    with pytest.raises(RuntimeError, match="port 7772"):
        tool.capture_measurement_metrics(7772)


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


def test_bounded_driver_quit_retires_via_the_shared_lease_and_proof_sweeps_the_renderer_tree():
    """Migrated from the bare SIGKILL fallback to the one shared lease's proof-guarded retirement.

    A wedged chromedriver is retired by the WebDriverLease owner (bounded quit -> TERM -> KILL), and
    the orphan renderer subtree is swept only for PIDs whose captured start-key proof still holds. A
    descendant that exited or was reparented (no key) is NEVER signalled - the reuse/reparent guard.
    """

    tool = load_tool_module()
    signals = []
    # An injectable process world: chromedriver 5000 and renderers 5001/5002 are proven live; 5003 is
    # a descendant that has already exited (no key) and must never be signalled.
    alive = {5000: "cd", 5001: "r1", 5002: "r2"}

    def identity(pid):
        return alive.get(pid)

    def fake_signal(pid, sig):
        signals.append((pid, sig))
        if pid not in alive:
            raise ProcessLookupError(pid)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            del alive[pid]

    class HangingDriver:
        class service:  # noqa: N801 - mirrors selenium attribute shape
            class process:  # noqa: N801
                pid = 5000

        def quit(self):
            time.sleep(60)

    original_descendants = tool.descendants_of
    tool.descendants_of = lambda pid: [5001, 5002, 5003]
    try:
        tool.bounded_driver_quit(HangingDriver(), quit_timeout=0.05, identity_fn=identity, signal_fn=fake_signal)
    finally:
        tool.descendants_of = original_descendants

    # The lease retired chromedriver 5000 through its proof-guarded escalation (TERM cleared it).
    assert (5000, signal.SIGTERM) in signals, signals
    # The two proven renderers were swept; the exited descendant 5003 was never signalled.
    assert (5001, signal.SIGKILL) in signals and (5002, signal.SIGKILL) in signals, signals
    assert not any(pid == 5003 for pid, _sig in signals), signals


def test_idle_yostats_counter_is_limited_to_the_two_live_ticker_callback_names():
    tool = load_tool_module()

    class FakeDriver:
        script = ""

        def execute_script(self, script):
            self.script = script

    driver = FakeDriver()
    tool.install_ticker_callback_counter(driver)

    assert "debugGraphLiveFrameTick" in driver.script
    assert "debugGraphLiveTimerTick" in driver.script
    assert "counter.requestAnimationFrame += 1" in driver.script
    assert "counter.timeout += 1" in driver.script
    assert "setTimeout =" in driver.script

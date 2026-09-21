# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import importlib.util
import inspect
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import yostats_capture_common
from yolomux_lib.infra import listener_census


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


def test_active_browser_window_uses_two_second_listener_census(monkeypatch):
    tool = load_tool_module()
    observed = []

    class CensusObserved(Exception):
        pass

    def census(port, *, timeout_seconds, strict):
        # The caller must name its mode. Default is target-scoped operational identity; strict
        # whole-host visibility cannot succeed on a shared host and is not what this measures.
        assert strict is False, strict
        observed.append((port, timeout_seconds))
        raise CensusObserved

    monkeypatch.setattr(tool, "parse_args", lambda: SimpleNamespace(output=Path("/tmp/window.json"), port=FIXTURE_PORT))
    monkeypatch.setattr(tool, "find_chrome", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(tool, "unique_listener_pid", census)

    with pytest.raises(CensusObserved):
        tool.main()
    assert observed == [(FIXTURE_PORT, 2.0)]


def test_active_browser_window_rejects_raw_fork_parent_and_child(monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "parse_args", lambda: SimpleNamespace(output=Path("/tmp/window.json"), port=FIXTURE_PORT))
    monkeypatch.setattr(tool, "find_chrome", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(listener_census, "listener_census", lambda *_a, **_k: listener_census.ListenerCensus(pids=(101, 202)))

    with pytest.raises(RuntimeError, match=r"found \[101, 202\]"):
        tool.main()


def test_active_browser_window_resolves_requested_instance_before_product_imports():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert source.index("apply_early_instance_environment(sys.argv[1:])") < source.index(
        "from yolomux_lib.auth import AUTH_CONFIG_PATH"
    )

    environment = dict(os.environ)
    for key in (
        "YOLOMUX_INSTANCE",
        "YOLOMUX_ROOT",
        "YOLOMUX_RUNTIME_DIR",
        "YOLOMUX_CONFIG_DIR",
        "YOLOMUX_STATE_DIR",
        "YOLOMUX_CACHE_DIR",
        "YOLOMUX_CODEX_HOME",
        "YOLOMUX_HOST_ARTIFACT_DIR",
        "YOLOMUX_START_LOCK_DIR",
        "YOLOMUX_LOG_DIR",
        "YOLOMUX_CA_DIR",
        "YOLOMUX_TOOL_LOCK_PATH",
        "YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX",
        "CODEX_HOME",
        "PYTHONPYCACHEPREFIX",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
    ):
        environment.pop(key, None)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from tools import yostats_active_browser_window as tool; "
                "print(json.dumps({'auth': str(tool.AUTH_CONFIG_PATH), "
                "'state': str(tool.STATE_DIR), "
                "'runtime': str(tool.RUNTIME_DIR), "
                "'managed': tool.is_managed_instance_port(7771)}))"
            ),
            "--port",
            "7771",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    expected_root = Path("/tmp") / f"y{os.getuid()}" / "p7771"
    assert json.loads(probe.stdout) == {
        "auth": str(expected_root / "config" / "auth.yaml"),
        "state": str(expected_root / "state"),
        "runtime": str(expected_root / "runtime"),
        "managed": True,
    }


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
    assert "const source = sourceTab;" in source
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
    assert "Network.setExtraHTTPHeaders" not in source
    assert 'choices=("active", "idle-yostats", "deterministic-fanout")' in source
    assert "prepare_idle_yostats_workload" in source
    assert "install_ticker_callback_counter" in source
    assert '"ticker_callbacks"' in source
    assert '"renderer"' in source


def test_deterministic_fanout_contract_freezes_every_step_owner_and_profiler_limit():
    tool = load_tool_module()

    contract = tool.deterministic_fanout_workload_contract(75)

    assert contract["schema_version"] == 1
    assert contract["steps"] == {
        "authenticated_cold_load": 1,
        "identical_watch_root_renewals": 10,
        "operation_add_remove_cycles": 10,
        "unchanged_watchd_revisions": 10,
        "client_event_source_reconnects": 1,
        "producer_restarts": 1,
    }
    assert contract["source_generation_owners"] == {
        "watch_roots": "deterministic-watch-roots",
        "operation_cycles": "deterministic-operation-cycle",
        "watchd_revisions": "filesystem-watch-diff",
        "client_events": "client-event-transport",
        "producer_restart": "watchd",
    }
    assert contract["owner_counter_names"] == [
        "session_discovery",
        "transcript_tail_scan",
        "session_files_materialization",
        "batchd_work_graph_rebuild",
        "provider_metadata_rebuild",
        "statsd_unchanged_cell_materialization",
        "statusd_unchanged_pane_capture",
    ]
    assert contract["profiler"] == {
        "tool": "py-spy",
        "rate_hz": 99,
        "duration_seconds": 75,
        "threads": True,
        "gil_only": True,
        "sample_error_ceiling": 0,
    }


def test_deterministic_final_acceptance_requires_ui_convergence_and_zero_browser_errors():
    tool = load_tool_module()
    final_ui = {
        "settled": True,
        "source_generation": {"epoch": "server-one", "generation": 12},
        "rendered_generation": {"epoch": "server-one", "generation": 12},
        "owners": {
            "client_event_connected": True,
            "client_event_candidate": False,
            "client_event_reconnect_pending": False,
            "startup_active": 0,
            "startup_queued": 0,
            "watch_roots_in_flight": False,
            "operations_pending": 0,
            "operation_waiters": 0,
            "acknowledgments_pending": 0,
            "acknowledgment_in_flight": False,
        },
        "dom": {"grid_connected": True},
    }
    diagnostics = {
        "js_debug_store_reachable": True,
        "js_debug_event_count": 42,
        "browser_local_failures": [],
        "browser_log_failures": [],
        "warning_or_error_count": 0,
        "receipt_quiescent": True,
    }

    assert tool.validate_deterministic_final_acceptance(final_ui, diagnostics) == {
        "ui_convergence": final_ui,
        "browser_diagnostics": diagnostics,
    }
    with pytest.raises(RuntimeError, match="source generation"):
        tool.validate_deterministic_final_acceptance(
            {**final_ui, "rendered_generation": {"epoch": "server-one", "generation": 11}},
            diagnostics,
        )
    with pytest.raises(RuntimeError, match="Warning/Error"):
        tool.validate_deterministic_final_acceptance(
            final_ui,
            {**diagnostics, "warning_or_error_count": 1},
        )


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
        "session",
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


def test_deterministic_app_wait_accepts_explicit_sessions_anywhere_in_the_roster(monkeypatch):
    tool = load_tool_module()
    responses = iter([True, ["unrelated", "ant", "another", "yo7771-b"]])
    driver = SimpleNamespace(execute_script=lambda _script: next(responses))

    class ImmediateWait:
        def until(self, predicate):
            return predicate(driver)

    monkeypatch.setattr(tool, "WebDriverWait", lambda *_args: ImmediateWait())

    tool.wait_for_app(driver, ["yo7771-b", "ant"], timeout=20)


def test_deterministic_app_wait_rejects_a_missing_explicit_session(monkeypatch):
    tool = load_tool_module()
    responses = iter([True, ["unrelated", "ant"]])
    driver = SimpleNamespace(execute_script=lambda _script: next(responses))

    class ImmediateWait:
        def until(self, predicate):
            return predicate(driver)

    monkeypatch.setattr(tool, "WebDriverWait", lambda *_args: ImmediateWait())

    with pytest.raises(RuntimeError, match=r"missing \['yo7771-b'\]"):
        tool.wait_for_app(driver, ["yo7771-b", "ant"], timeout=20)


@pytest.mark.parametrize("output", [Path("/outside/window.json"), Path("/tmp/../outside-window.json")])
def test_active_browser_window_rejects_non_tmp_output_before_chrome_starts(monkeypatch, capsys, output):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "parse_args", lambda: SimpleNamespace(output=output, port=FIXTURE_PORT, duration=60, workload="active", username=None))
    monkeypatch.setattr(tool, "find_chrome", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(tool, "unique_listener_pid", lambda _port, **_kwargs: 1)
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
    monkeypatch.setattr(tool, "unique_listener_pid", lambda _port, **_kwargs: 1)
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
    (services / "batchd.service.json").write_text('{"service":"batchd","pid":31,"socket":"/tmp/batchd.sock"}\n', encoding="utf-8")
    monkeypatch.setattr(tool, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(tool, "process_is_alive", lambda pid: pid == 31)
    monkeypatch.setattr(tool, "service_pid_for_socket", lambda socket_path: 47 if socket_path == "/tmp/statsd.sock" else 0)

    assert tool.runtime_service_pids() == {"batchd": 31, "statsd": 47}
    assert "--print-runtime-report" not in TOOL_PATH.read_text(encoding="utf-8")


def test_active_browser_window_reads_only_generic_capture_metrics_from_the_authenticated_browser_server(monkeypatch):
    tool = load_tool_module()
    source = inspect.getsource(tool.capture_measurement_metrics)
    for forbidden in (
        "ensure_started",
        "runtime_status",
        "service_status",
        "send_yolomux_control_request",
        "read_background_scheduler_debug_status",
    ):
        assert forbidden not in source
    scripts = []
    driver = SimpleNamespace(
        execute_async_script=lambda script, *_arguments: scripts.append(script) or {
            "ok": True,
            "perf": {"summary": []},
            "performanceDiagnostics": {
                "browser_observation_status": {
                    "owner_counters": {"statsd_unchanged_cell_materialization": 7},
                },
            },
            "systemStatus": {
                "local_services": {
                    "services": [
                        {
                            "service": "batchd",
                            "owner_invocations": {
                                "batchd_work_graph_rebuild": 3,
                                "provider_metadata_rebuild": 4,
                            },
                        },
                        {
                            "service": "statusd",
                            "owner_invocations": {"statusd_unchanged_pane_capture": 5},
                        },
                    ],
                },
            },
            "systemStatusAdvanced": {
                "refresh": {
                    "owner_invocations": {
                        "session_discovery": 10,
                        "transcript_tail_scan": 11,
                        "session_files_materialization": 12,
                        "batchd_work_graph_rebuild": 2,
                    },
                },
            },
        },
    )
    assert not hasattr(tool, "read_background_scheduler_debug_status")
    assert not hasattr(tool, "send_yolomux_control_request")

    assert tool.capture_measurement_metrics(driver, require_owner_counters=True) == {
        "summary": [],
        "owner_counters": {
            "session_discovery": 10,
            "transcript_tail_scan": 11,
            "session_files_materialization": 12,
            "batchd_work_graph_rebuild": 5,
            "provider_metadata_rebuild": 4,
            "statsd_unchanged_cell_materialization": 7,
            "statusd_unchanged_pane_capture": 5,
        },
        "owner_counter_sources": {
            "watchd_refresh": {
                "session_discovery": 10,
                "transcript_tail_scan": 11,
                "session_files_materialization": 12,
                "batchd_work_graph_rebuild": 2,
            },
            "batchd": {
                "batchd_work_graph_rebuild": 3,
                "provider_metadata_rebuild": 4,
            },
            "statsd": {"statsd_unchanged_cell_materialization": 7},
            "statusd": {"statusd_unchanged_pane_capture": 5},
        },
    }
    assert len(scripts) == 1
    assert "/api/diagnostics/performance?measurement_scope=capture" in scripts[0]
    assert "'/api/diagnostics/performance'" in scripts[0]
    assert "'/api/system-status'" in scripts[0]
    assert "'/api/system-status/advanced'" in scripts[0]
    assert "originalFetch" in scripts[0]


def test_active_browser_window_rejects_an_invalid_target_server_metrics_response():
    tool = load_tool_module()
    driver = SimpleNamespace(execute_async_script=lambda _script, *_arguments: {"ok": False, "error": "forbidden"})

    with pytest.raises(RuntimeError, match="forbidden"):
        tool.capture_measurement_metrics(driver)


def test_non_deterministic_capture_does_not_require_lane_owner_diagnostics():
    tool = load_tool_module()
    arguments = []
    driver = SimpleNamespace(
        execute_async_script=lambda _script, *args: arguments.extend(args) or {
            "ok": True,
            "perf": {"summary": []},
        },
    )

    assert tool.capture_measurement_metrics(driver) == {"summary": []}
    assert arguments == [False]


def test_deterministic_capture_identifies_only_typed_demand_driven_snapshot_refusals_as_pending():
    tool = load_tool_module()
    driver = SimpleNamespace(
        execute_async_script=lambda _script, *_arguments: {
            "ok": True,
            "perf": {"summary": []},
            "performanceDiagnostics": {},
            "systemStatus": {
                "ok": False,
                "snapshot": {"reason_code": "system_status_snapshot_unavailable"},
            },
            "systemStatusAdvanced": {
                "ok": False,
                "snapshot": {"reason_code": "system_status_snapshot_stale"},
            },
        },
    )

    with pytest.raises(
        tool.MeasurementSnapshotPending,
        match="system-status:system_status_snapshot_unavailable, system-status-advanced:system_status_snapshot_stale",
    ):
        tool.capture_measurement_metrics(driver, require_owner_counters=True)


def test_deterministic_baseline_waits_for_typed_snapshot_publication(monkeypatch):
    tool = load_tool_module()
    expected = {"owner_counters": {"session_discovery": 1}}
    calls = []

    def capture(driver, *, require_owner_counters=False):
        calls.append((driver, require_owner_counters))
        if len(calls) == 1:
            raise tool.MeasurementSnapshotPending("publishing")
        return expected

    class PublicationWait:
        def __init__(self, driver, timeout, ignored_exceptions):
            assert driver == "driver"
            assert timeout == 20
            assert ignored_exceptions == (tool.MeasurementSnapshotPending,)
            self.ignored_exceptions = ignored_exceptions

        def until(self, predicate):
            try:
                predicate("driver")
            except self.ignored_exceptions:
                pass
            return predicate("driver")

    monkeypatch.setattr(tool, "capture_measurement_metrics", capture)
    monkeypatch.setattr(tool, "WebDriverWait", PublicationWait)

    assert tool.wait_for_deterministic_measurement_baseline("driver") == expected
    assert calls == [("driver", True), ("driver", True)]


def test_active_browser_window_joins_every_issued_request_exactly_once():
    tool = load_tool_module()
    marker = "capture-0123456789abcdef0123456789abcdef"
    digest = tool.measurement_marker_digest(marker)
    issued = [
        {"request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 200},
        {"request_id": "r-two", "method": "POST", "path": "/api/fs/batch", "status": 202},
    ]
    performance = {
        "capture": {"capacity": 2048, "retained": 31, "total": 31, "evicted": 0, "first_sequence": 1, "last_sequence": 31},
        "recent": [
            {"surface": "POST /api/watch/roots", "details": {"measurement_request_id": digest, "request_id": "r-response-one", "transport_request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 200}},
            {"surface": "POST /api/fs/batch", "details": {"measurement_request_id": digest, "request_id": "r-response-two", "transport_request_id": "r-two", "method": "POST", "path": "/api/fs/batch", "status": 202}},
            {"surface": "GET /api/ping", "details": {"measurement_request_id": "another-run", "request_id": "r-old", "status": 200}},
        ],
    }

    result = tool.validate_capture_request_join(marker, issued, performance)

    assert result["join"] == {
        "issued": 2,
        "server_records": 2,
        "missing": [],
        "duplicate": [],
        "unexpected": [],
    }
    assert [row["details"]["transport_request_id"] for row in result["records"]] == ["r-one", "r-two"]
    assert result["capture_store"] == performance["capture"]


@pytest.mark.parametrize(
    ("issued", "server_ids", "message"),
    [
        (["r-one", "r-two"], ["r-one"], "missing"),
        (["r-one"], ["r-one", "r-one"], "duplicate"),
        (["r-one"], ["r-one", "r-extra"], "unexpected"),
    ],
)
def test_active_browser_window_rejects_incomplete_request_joins(issued, server_ids, message):
    tool = load_tool_module()
    marker = "capture-0123456789abcdef0123456789abcdef"
    digest = tool.measurement_marker_digest(marker)
    performance = {
        "capture": {"capacity": 2048, "retained": len(server_ids), "total": len(server_ids), "evicted": 0},
        "recent": [
            {"surface": "GET /api/ping", "details": {"measurement_request_id": digest, "request_id": f"r-response-{index}", "transport_request_id": request_id, "status": 200}}
            for index, request_id in enumerate(server_ids)
        ],
    }
    issued_rows = [{"request_id": request_id, "method": "GET", "path": "/api/ping", "status": 200} for request_id in issued]

    with pytest.raises(RuntimeError, match=message):
        tool.validate_capture_request_join(marker, issued_rows, performance)


@pytest.mark.parametrize(
    ("issued", "server", "message"),
    [
        (
            {"request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 0},
            {"request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 200},
            "browser status 0",
        ),
        (
            {"request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 200},
            {"request_id": "r-one", "method": "POST", "path": "/api/fs/batch", "status": 200},
            "mismatch",
        ),
        (
            {"request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 200},
            {"request_id": "r-one", "method": "POST", "path": "/api/watch/roots", "status": 202},
            "mismatch",
        ),
    ],
)
def test_active_browser_window_rejects_request_join_field_mismatches(issued, server, message):
    tool = load_tool_module()
    marker = "capture-0123456789abcdef0123456789abcdef"
    digest = tool.measurement_marker_digest(marker)
    performance = {
        "capture": {"capacity": 2048, "retained": 1, "total": 1, "evicted": 0},
        "recent": [{"surface": f"{server['method']} {server['path']}", "details": {"measurement_request_id": digest, "request_id": "r-response", "transport_request_id": server["request_id"], **server}}],
    }

    with pytest.raises(RuntimeError, match=message):
        tool.validate_capture_request_join(marker, [issued], performance)


def test_active_browser_window_measurement_fetch_ledger_is_bounded_and_body_free():
    tool = load_tool_module()
    scripts = []
    driver = SimpleNamespace(execute_script=lambda script, *args: scripts.append((script, args)))

    tool.install_measurement_fetch_header(driver, "capture-0123456789abcdef0123456789abcdef")

    script, args = scripts[0]
    assert args == ("capture-0123456789abcdef0123456789abcdef",)
    assert "request_id" in script
    assert "method" in script
    assert "path" in script
    assert "status" in script
    assert "dropped" in script
    assert "peak_api_fetches" in script
    assert "activeApiRequests" in script
    assert "4096" in script
    assert "body:" not in script
    assert "request.clone" not in script


def test_deterministic_cold_navigation_starts_after_every_cpu_and_counter_baseline():
    tool = load_tool_module()
    source = inspect.getsource(tool.main)

    assert source.index("prepare_deterministic_cold_navigation(") < source.index("measurement_before =")
    assert source.index("measurement_before =") < source.index("process = subprocess.Popen(")
    assert source.index("profiler_process = subprocess.Popen(") < source.index("open_deterministic_cold_navigation(")
    assert source.index("open_deterministic_cold_navigation(") < source.index("perform_deterministic_fanout_browser_workload(")
    assert source.count("wait_for_deterministic_measurement_baseline(driver)") == 2


def test_deterministic_operation_batch_quiesces_existing_operations_before_freezing_ack_flushes():
    tool = load_tool_module()
    source = inspect.getsource(tool.perform_deterministic_fanout_browser_workload)

    assert "if (operationTerminalAckState.request)" in source
    assert "const capturedAckIds = [...operationTerminalAckState.pending.keys()]" in source
    assert "const retainedAckIds = capturedAckIds.filter" in source
    assert "if (!apiOperationState.pending.size) return" in source
    assert "record?.phase === 'accepted' && apiOperationState.pending.has" in source
    assert "record?.phase === 'terminal'" in source
    assert "apiOperationState.terminal.has(pending.operationId)" in source
    assert source.index("await awaitPreexistingOperationQuiescence()") < source.index(
        "operationTerminalAckState.timer = -1"
    )
    assert "const missingIds = operationIds.filter" in source
    assert "unrelatedAckIds = pendingAckIds.filter" in source
    assert "acknowledged.length !== pendingAckIds.length" in source
    assert "pendingAckIds.some(id => !acknowledged.includes(id)" in source


def test_deterministic_preload_retains_bounded_fanout_and_event_source_evidence_without_bodies():
    tool = load_tool_module()
    script = tool.deterministic_measurement_preload_script("capture-0123456789abcdef0123456789abcdef")

    assert "max_concurrent_api_fetches" in script
    assert "fs_batch_item_count" in script
    assert "requests.length" in script
    assert "body:" not in script
    assert "event_sources" in script
    assert "max_live" in script
    assert "new Proxy" in script


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


def test_deterministic_fanout_requires_two_explicit_sessions():
    tool = load_tool_module()

    with pytest.raises(SystemExit):
        tool.parse_args(["--workload", "deterministic-fanout", "--output", "/tmp/window.json"])
    args = tool.parse_args([
        "--workload", "deterministic-fanout",
        "--session", "one",
        "--session", "two",
        "--duration", "75",
        "--output", "/tmp/window.json",
    ])
    assert args.session == ["one", "two"]
    assert args.duration == 75


def test_deterministic_browser_workload_executes_each_owned_transition():
    tool = load_tool_module()

    class FakeDriver:
        script = ""
        arguments = ()

        def execute_async_script(self, script, *arguments):
            self.script = script
            self.arguments = arguments
            return {
                "ok": True,
                "steps": {
                    "identical_watch_root_renewals": 10,
                    "operation_add_remove_cycles": 10,
                    "unchanged_watchd_revisions": 10,
                    "client_event_source_reconnects": 1,
                },
                "source_generation_keys": {},
                "owner_invocations": {},
            }

    driver = FakeDriver()
    result = tool.perform_deterministic_fanout_browser_workload(
        driver, "capture-0123456789abcdef0123456789abcdef", "session-one"
    )

    assert result["steps"]["identical_watch_root_renewals"] == 10
    assert driver.arguments == ("capture-0123456789abcdef0123456789abcdef", 10, "session-one")
    assert "forceSourceOwner: 'deterministic-watch-roots'" in driver.script
    assert "registerApiOperationReceipt" in driver.script
    assert "fetchFilesystemWatchDiff" in driver.script
    assert "closeClientEventStream" in driver.script
    assert "scheduleClientEventDisconnectEpisode(null)" in driver.script
    assert "installClientEventStream" in driver.script


def test_deterministic_operation_cycles_use_real_session_files_receipts_and_exact_acknowledgments():
    tool = load_tool_module()

    class FakeDriver:
        script = ""

        def execute_async_script(self, script, *_arguments):
            self.script = script
            return {
                "ok": True,
                "steps": {
                    "identical_watch_root_renewals": 10,
                    "operation_add_remove_cycles": 10,
                    "unchanged_watchd_revisions": 10,
                    "client_event_source_reconnects": 1,
                },
                "source_generation_keys": {},
                "owner_invocations": {},
            }

    driver = FakeDriver()
    tool.perform_deterministic_fanout_browser_workload(
        driver, "capture-0123456789abcdef0123456789abcdef", "session-one"
    )

    assert "/api/session-files?" in driver.script
    assert "force=1" in driver.script
    assert "kind: 'session_files'" in driver.script
    assert "apiFetchJson(sessionFilesUrl" in driver.script
    assert "waitForApiOperationResult(pending" in driver.script
    assert "registerApiOperationReceipt({" not in driver.script
    assert "operationTerminalAckState.pending" in driver.script
    assert "acknowledged" in driver.script
    assert "ignored" in driver.script


def test_deterministic_producer_restart_refuses_a_shared_instance_before_signalling(monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "is_managed_instance_port", lambda _port: False)
    monkeypatch.setattr(tool.os, "kill", lambda *_args: pytest.fail("shared producer must not be signalled"))

    with pytest.raises(RuntimeError, match="managed isolated instance"):
        tool.restart_managed_watchd_producer(SimpleNamespace(), FIXTURE_PORT)


def test_deterministic_producer_restart_proves_exact_old_and_new_identities(monkeypatch):
    tool = load_tool_module()
    pids = iter(({"watchd": 41}, {"watchd": 42}))
    signals = []
    driver = SimpleNamespace(execute_async_script=lambda _script: {"ok": True})

    class ImmediateWait:
        def until(self, predicate):
            return predicate(driver)

    monkeypatch.setattr(tool, "is_managed_instance_port", lambda _port: True)
    monkeypatch.setattr(tool, "runtime_service_pids", lambda: next(pids))
    monkeypatch.setattr(tool, "process_start_key", lambda pid: (pid, f"start-{pid}"))
    monkeypatch.setattr(tool, "process_is_alive", lambda _pid: False)
    monkeypatch.setattr(tool.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(tool, "WebDriverWait", lambda *_args: ImmediateWait())

    result = tool.restart_managed_watchd_producer(driver, FIXTURE_PORT)

    assert signals == [(41, signal.SIGTERM)]
    assert result == {
        "service": "watchd",
        "before_pid": 41,
        "before_start_key": [41, "start-41"],
        "after_pid": 42,
        "after_start_key": [42, "start-42"],
        "restarts": 1,
        "source_generation_key": "watchd:42:(42, 'start-42')",
    }


def test_deterministic_profile_counts_samples_and_enforces_the_predeclared_error_ceiling(tmp_path):
    tool = load_tool_module()
    raw = tmp_path / "profile.raw"
    raw.write_text("thread (1);one 3\nthread (2);two 2\n", encoding="utf-8")
    command = ["py-spy", "record", "--duration", "75", "--threads", "--gil"]

    profile = tool.validate_deterministic_profile(raw, "py-spy> wrote profile\n", 0, command)

    assert profile["sample_count"] == 5
    assert profile["sample_error_count"] == 0
    assert profile["sample_error_ceiling"] == 0
    assert profile["admissible"] is True
    with pytest.raises(RuntimeError, match="errors=1 ceiling=0"):
        tool.validate_deterministic_profile(raw, "Error: one sampling failure\n", 0, command)


def test_deterministic_owner_counter_composition_fails_closed_on_each_diagnostics_source():
    tool = load_tool_module()
    performance_diagnostics = {
        "browser_observation_status": {
            "owner_counters": {"statsd_unchanged_cell_materialization": 7},
        },
    }
    system_status = {
        "local_services": {
            "services": [
                {
                    "service": "batchd",
                    "owner_invocations": {
                        "batchd_work_graph_rebuild": 3,
                        "provider_metadata_rebuild": 4,
                    },
                },
                {
                    "service": "statusd",
                    "owner_invocations": {"statusd_unchanged_pane_capture": 5},
                },
            ],
        },
    }
    system_status_advanced = {
        "refresh": {
            "owner_invocations": {
                "session_discovery": 10,
                "transcript_tail_scan": 11,
                "session_files_materialization": 12,
                "batchd_work_graph_rebuild": 2,
            },
        },
    }

    assert tool.compose_deterministic_owner_counters(
        performance_diagnostics,
        system_status,
        system_status_advanced,
    ) == {
        "totals": {
            "session_discovery": 10,
            "transcript_tail_scan": 11,
            "session_files_materialization": 12,
            "batchd_work_graph_rebuild": 5,
            "provider_metadata_rebuild": 4,
            "statsd_unchanged_cell_materialization": 7,
            "statusd_unchanged_pane_capture": 5,
        },
        "sources": {
            "watchd_refresh": {
                "session_discovery": 10,
                "transcript_tail_scan": 11,
                "session_files_materialization": 12,
                "batchd_work_graph_rebuild": 2,
            },
            "batchd": {
                "batchd_work_graph_rebuild": 3,
                "provider_metadata_rebuild": 4,
            },
            "statsd": {"statsd_unchanged_cell_materialization": 7},
            "statusd": {"statusd_unchanged_pane_capture": 5},
        },
    }

    system_status["local_services"]["services"][1]["owner_invocations"] = {}
    with pytest.raises(RuntimeError, match="statusd_unchanged_pane_capture"):
        tool.compose_deterministic_owner_counters(
            performance_diagnostics,
            system_status,
            system_status_advanced,
        )

    with pytest.raises(RuntimeError, match="watchd_refresh.session_discovery"):
        tool.compose_deterministic_owner_counters(
            performance_diagnostics,
            {"ok": False, "state": "unpublished"},
            {"ok": False, "state": "stale"},
        )


def test_deterministic_measurement_schema_retains_exact_ids_generations_and_owner_counter_samples():
    tool = load_tool_module()
    contract = tool.deterministic_fanout_workload_contract(75)
    receipts = [
        {
            "id": f"op-{index}",
            "request_id": f"request-{index}",
            "accepted_cursor": {"epoch": "server-epoch", "seq": 0},
            "terminal_cursor": {"epoch": "server-epoch", "seq": 1},
        }
        for index in range(10)
    ]
    operation_generations = [
        {"id": row["id"], "epoch": "server-epoch", "accepted_seq": 0, "terminal_seq": 1}
        for row in receipts
    ]
    browser_workload = {
        "steps": {
            "identical_watch_root_renewals": 10,
            "operation_add_remove_cycles": 10,
            "unchanged_watchd_revisions": 10,
            "client_event_source_reconnects": 1,
        },
        "source_generation_keys": {
            "watch_roots": "capture:roots:1",
            "operation_cycles": operation_generations,
        },
        "owner_invocations": {"deterministic_watch_roots": 10},
        "operation_cycles": {
            "route": "/api/session-files",
            "receipts": receipts,
            "acknowledgment_batch_ids": [row["id"] for row in receipts],
            "unrelated_acknowledgment_ids": [],
            "acknowledgments": {
                "ok": True,
                "acknowledged": [row["id"] for row in receipts],
                "ignored": [],
            },
        },
    }
    producer = {"restarts": 1, "source_generation_key": "watchd:42:start-42"}
    request_join = {
        "join": {"issued": 3, "server_records": 3, "missing": [], "duplicate": [], "unexpected": []},
        "issued": [
            {"request_id": "r-roots", "method": "POST", "path": "/api/watch/roots", "status": 200},
            {"request_id": "r-diff", "method": "GET", "path": "/api/fs/watch-diff", "status": 200},
            {"request_id": "r-batch", "method": "POST", "path": "/api/fs/batch", "status": 202},
        ],
        "browser_ledger": {
            "max_concurrent_api_fetches": 8,
            "peak_api_fetches": [],
            "fs_batch_requests": [{"request_id": "r-batch", "item_count": 64}],
            "event_sources": {
                "created": ["client-event-source-1", "client-event-source-2"],
                "closed": ["client-event-source-1"],
                "live": ["client-event-source-2"],
                "max_live": 1,
                "replacements": 1,
            },
        },
    }
    performance = {
        "summary": [],
        "owner_counters": {
            "session_discovery": 10,
            "transcript_tail_scan": 11,
            "session_files_materialization": 12,
            "batchd_work_graph_rebuild": 13,
            "provider_metadata_rebuild": 14,
            "statsd_unchanged_cell_materialization": 42,
            "statusd_unchanged_pane_capture": 16,
        },
        "owner_counter_sources": {
            "watchd_refresh": {
                "session_discovery": 10,
                "transcript_tail_scan": 11,
                "session_files_materialization": 12,
                "batchd_work_graph_rebuild": 5,
            },
            "batchd": {
                "batchd_work_graph_rebuild": 8,
                "provider_metadata_rebuild": 14,
            },
            "statsd": {"statsd_unchanged_cell_materialization": 42},
            "statusd": {"statusd_unchanged_pane_capture": 16},
        },
    }
    performance_before = {
        "summary": [],
        "owner_counters": {
            "session_discovery": 10,
            "transcript_tail_scan": 11,
            "session_files_materialization": 12,
            "batchd_work_graph_rebuild": 13,
            "provider_metadata_rebuild": 14,
            "statsd_unchanged_cell_materialization": 41,
            "statusd_unchanged_pane_capture": 16,
        },
        "owner_counter_sources": {
            "watchd_refresh": {
                "session_discovery": 10,
                "transcript_tail_scan": 11,
                "session_files_materialization": 12,
                "batchd_work_graph_rebuild": 5,
            },
            "batchd": {
                "batchd_work_graph_rebuild": 8,
                "provider_metadata_rebuild": 14,
            },
            "statsd": {"statsd_unchanged_cell_materialization": 41},
            "statusd": {"statusd_unchanged_pane_capture": 16},
        },
    }
    profiler = {"admissible": True, "sample_count": 100, "sample_error_count": 0, "sample_error_ceiling": 0}

    schema = tool.deterministic_measurement_schema(
        contract, browser_workload, producer, request_join, performance, profiler, 12.3456,
        performance_before=performance_before,
    )

    assert schema["observed_steps"] == contract["steps"]
    assert schema["exact_request_ids_by_route"] == {
        "POST /api/watch/roots": ["r-roots"],
        "GET /api/fs/watch-diff": ["r-diff"],
        "POST /api/fs/batch": ["r-batch"],
    }
    assert schema["source_generation_keys"] == {
        "watch_roots": "capture:roots:1",
        "operation_cycles": operation_generations,
        "producer_restart": "watchd:42:start-42",
    }
    assert schema["operation_cycle_join"]["accepted"] == 10
    assert schema["operation_cycle_join"]["terminal"] == 10
    assert schema["operation_cycle_join"]["acknowledged"] == 10
    assert schema["operation_cycle_join"]["batch_acknowledged"] == 10
    assert schema["operation_cycle_join"]["unrelated_acknowledged"] == 0
    assert schema["operation_cycle_join"]["ignored"] == 0
    assert schema["browser_fanout"]["max_concurrent_api_fetches"] == 8
    assert schema["browser_fanout"]["fs_batch_max_item_count"] == 64
    assert schema["browser_fanout"]["event_sources"]["replacements"] == 1
    assert schema["owner_invocations"]["session_discovery"] == 0
    assert schema["owner_invocations"]["transcript_tail_scan"] == 0
    assert schema["owner_invocations"]["statsd_unchanged_cell_materialization"] == 1
    assert schema["owner_counter_samples"] == {
        "before": performance_before["owner_counters"],
        "after": performance["owner_counters"],
        "delta": {
            "session_discovery": 0,
            "transcript_tail_scan": 0,
            "session_files_materialization": 0,
            "batchd_work_graph_rebuild": 0,
            "provider_metadata_rebuild": 0,
            "statsd_unchanged_cell_materialization": 1,
            "statusd_unchanged_pane_capture": 0,
        },
        "sources": {
            "before": performance_before["owner_counter_sources"],
            "after": performance["owner_counter_sources"],
        },
    }
    assert schema["elapsed_seconds"] == 12.346


def test_deterministic_operation_join_preserves_ten_owned_cycles_inside_a_larger_live_ack_batch():
    tool = load_tool_module()
    receipts = [
        {
            "id": f"op-owned-{index}",
            "request_id": f"request-owned-{index}",
            "accepted_cursor": {"epoch": "owned-epoch", "seq": 0},
            "terminal_cursor": {"epoch": "owned-epoch", "seq": 1},
        }
        for index in range(10)
    ]
    generation_rows = [
        {
            "id": row["id"],
            "epoch": "owned-epoch",
            "accepted_seq": 0,
            "terminal_seq": 1,
        }
        for row in receipts
    ]
    owned_ids = [row["id"] for row in receipts]
    batch_ids = [*owned_ids, "op-background-one", "op-background-two"]
    workload = {
        "source_generation_keys": {"operation_cycles": generation_rows},
        "operation_cycles": {
            "route": "/api/session-files",
            "receipts": receipts,
            "acknowledgment_batch_ids": batch_ids,
            "unrelated_acknowledgment_ids": batch_ids[10:],
            "acknowledgments": {
                "acknowledged": batch_ids,
                "ignored": [],
            },
        },
    }

    result = tool.validate_deterministic_operation_cycle_join(workload)

    assert result["accepted"] == 10
    assert result["acknowledged"] == 10
    assert result["batch_acknowledged"] == 12
    assert result["unrelated_acknowledged"] == 2


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        ({}, {}, "missing before"),
        (
            {name: 1 for name in (
                "session_discovery",
                "transcript_tail_scan",
                "session_files_materialization",
                "batchd_work_graph_rebuild",
                "provider_metadata_rebuild",
                "statsd_unchanged_cell_materialization",
                "statusd_unchanged_pane_capture",
            )},
            {},
            "missing after",
        ),
        (
            {name: 1 for name in (
                "session_discovery",
                "transcript_tail_scan",
                "session_files_materialization",
                "batchd_work_graph_rebuild",
                "provider_metadata_rebuild",
                "statsd_unchanged_cell_materialization",
                "statusd_unchanged_pane_capture",
            )},
            {name: (0 if name == "session_discovery" else 1) for name in (
                "session_discovery",
                "transcript_tail_scan",
                "session_files_materialization",
                "batchd_work_graph_rebuild",
                "provider_metadata_rebuild",
                "statsd_unchanged_cell_materialization",
                "statusd_unchanged_pane_capture",
            )},
            "moved backwards",
        ),
    ],
)
def test_deterministic_owner_counter_samples_require_complete_monotonic_before_and_after(before, after, message):
    tool = load_tool_module()

    def sources(values):
        if not values:
            return {}
        return {
            "watchd_refresh": {
                "session_discovery": values["session_discovery"],
                "transcript_tail_scan": values["transcript_tail_scan"],
                "session_files_materialization": values["session_files_materialization"],
                "batchd_work_graph_rebuild": 0,
            },
            "batchd": {
                "batchd_work_graph_rebuild": values["batchd_work_graph_rebuild"],
                "provider_metadata_rebuild": values["provider_metadata_rebuild"],
            },
            "statsd": {
                "statsd_unchanged_cell_materialization": values["statsd_unchanged_cell_materialization"],
            },
            "statusd": {
                "statusd_unchanged_pane_capture": values["statusd_unchanged_pane_capture"],
            },
        }

    with pytest.raises(RuntimeError, match=message):
        tool.deterministic_owner_counter_snapshot(
            {"owner_counters": after, "owner_counter_sources": sources(after)},
            {"owner_counters": before, "owner_counter_sources": sources(before)},
        )


def test_deterministic_owner_counter_samples_reject_a_hidden_component_reset():
    tool = load_tool_module()
    totals = {name: 1 for name in tool.DETERMINISTIC_OWNER_COUNTER_NAMES}
    totals["batchd_work_graph_rebuild"] = 5
    baseline_sources = {
        "watchd_refresh": {
            "session_discovery": 1,
            "transcript_tail_scan": 1,
            "session_files_materialization": 1,
            "batchd_work_graph_rebuild": 2,
        },
        "batchd": {"batchd_work_graph_rebuild": 3, "provider_metadata_rebuild": 1},
        "statsd": {"statsd_unchanged_cell_materialization": 1},
        "statusd": {"statusd_unchanged_pane_capture": 1},
    }
    after_sources = {
        **baseline_sources,
        "watchd_refresh": {**baseline_sources["watchd_refresh"], "batchd_work_graph_rebuild": 1},
        "batchd": {**baseline_sources["batchd"], "batchd_work_graph_rebuild": 4},
    }

    with pytest.raises(RuntimeError, match=r"watchd_refresh\.batchd_work_graph_rebuild"):
        tool.deterministic_owner_counter_snapshot(
            {"owner_counters": totals, "owner_counter_sources": after_sources},
            {"owner_counters": totals, "owner_counter_sources": baseline_sources},
        )


@pytest.mark.parametrize(
    ("ledger", "message"),
    [
        (
            {
                "max_concurrent_api_fetches": 9,
                "fs_batch_requests": [{"request_id": "r-batch", "item_count": 1}],
                "event_sources": {"created": ["one", "two"], "closed": ["one"], "live": ["two"], "max_live": 1, "replacements": 1},
            },
            "concurrency",
        ),
        (
            {
                "max_concurrent_api_fetches": 1,
                "fs_batch_requests": [{"request_id": "r-batch", "item_count": 65}],
                "event_sources": {"created": ["one", "two"], "closed": ["one"], "live": ["two"], "max_live": 1, "replacements": 1},
            },
            "cardinality",
        ),
        (
            {
                "max_concurrent_api_fetches": 1,
                "fs_batch_requests": [{"request_id": "r-batch", "item_count": 1}],
                "event_sources": {"created": ["one", "two", "three"], "closed": ["one", "two"], "live": ["three"], "max_live": 1, "replacements": 2},
            },
            "EventSource",
        ),
    ],
)
def test_deterministic_browser_fanout_evidence_fails_closed(ledger, message):
    tool = load_tool_module()

    with pytest.raises(RuntimeError, match=message):
        tool.validate_deterministic_browser_fanout({"browser_ledger": ledger})


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
    assert 'service_dir=RUNTIME_DIR / "services"' in source
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

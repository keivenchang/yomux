# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import errno
from http import HTTPStatus
from http.client import HTTPConnection
import inspect
import json
import os
import re
import socket
import stat
import subprocess
import sys
import threading
import tempfile
import time
from urllib.parse import urlencode
import uuid
from pathlib import Path

import pytest

from yolomux_lib.local_services import client as local_service_client_module
from yolomux_lib.local_services import registry as local_services_registry
from yolomux_lib.local_services import rpc as local_service_rpc_module
from yolomux_lib.approval.approvald import ApprovalClient
from yolomux_lib.infra import batchd as batchd_module
from yolomux_lib.filesystem import git_ops
from yolomux_lib.infra.batchd import BatchClient
from yolomux_lib.infra.batchd import PersistentJobBroker
from yolomux_lib.local_services.client import LocalServiceClient
from yolomux_lib.local_services.client import local_service_failure_is_transient
from yolomux_lib.local_services.rpc import local_service_traffic_ledger
from yolomux_lib.local_services.rpc import reset_local_service_traffic
from yolomux_lib.observability.pricing_catalog import PricingRefreshCoordinator
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.stats_current.http import SnapshotHttpResult
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import WebDriverWait
from tests.gate_harness import RepeatFailure
from tests.gate_harness import GateHttpResponse
from tests.gate_harness import assert_fixture_client_event_demand_claimed
from tests.gate_harness import assert_computed_style
from tests.gate_harness import assert_counter_delta
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_http_port
from tests.gate_harness import gate_http_port_candidates
from tests.gate_harness import gate_live_server
from tests.gate_harness import gate_runtime_paths
from tests.gate_harness import gate_tmux
from tests.gate_harness import claim_fixture_client_event_demand
from tests.gate_harness import release_fixture_client_event_demand
from tests.gate_harness import load_gate_browser
from tests.gate_harness import load_gate_terminal_only_browser
from tests.gate_harness import repeat
from tests.gate_harness import run_when_browser_ready
from tests.helpers.browser_contracts import send_native_key as _send_native_key
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from tests.tmux_runtime import run_isolated_tmux
from tools.test_plan import PYTEST_LANE_NAMES


ENDPOINT_COMPUTE_BUDGET_MS = 750.0
TERMINAL_KEYPRESS_BUDGET_SECONDS = 3.5


def test_gate_runtime_paths_are_fixture_owned(gate_runtime_paths):
    assert gate_runtime_paths.home_dir.is_dir()
    assert gate_runtime_paths.config_dir.is_dir()
    assert gate_runtime_paths.state_dir.is_dir()
    assert os.environ["HOME"] == str(gate_runtime_paths.home_dir)
    assert os.environ["YOLOMUX_CONFIG_DIR"] == str(gate_runtime_paths.config_dir)
    assert os.environ["YOLOMUX_STATE_DIR"] == str(gate_runtime_paths.state_dir)
    assert os.environ["TMPDIR"] == str(gate_runtime_paths.tmp_dir)
    assert Path(tempfile.gettempdir()).resolve() == gate_runtime_paths.tmp_dir.resolve()
    assert gate_runtime_paths.chrome_profile_dir.is_dir()
    assert git_ops._git_loose_object_cache_root().is_relative_to(gate_runtime_paths.tmp_dir)
    child_tempdir = subprocess.run(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert Path(child_tempdir).resolve() == gate_runtime_paths.tmp_dir.resolve()
    assert gate_runtime_paths.auth_config_path == gate_runtime_paths.config_dir / "auth.yaml"
    patched_paths = dict(gate_runtime_paths.patched_module_paths)
    assert patched_paths["yolomux_lib.infra.common.AUTH_CONFIG_PATH"] == gate_runtime_paths.auth_config_path
    assert patched_paths["yolomux_lib.auth.AUTH_CONFIG_PATH"] == gate_runtime_paths.auth_config_path


def _leave_predecessor_time_wait(host: str, port: int) -> None:
    """Retain one predecessor TIME_WAIT on this exact ``(host, port)``, deterministically.

    A server-side active close of an established connection leaves the server's local
    endpoint -- here the fixture-owned candidate port -- in TIME_WAIT. That is the retained
    kernel state a plain bind rejects with ``EADDRINUSE`` and a ``SO_REUSEADDR`` bind (the
    real ``HttpPortLease`` reuse owner) must tolerate. Creating it in-sequence removes any
    dependency on ambient port history, which is what let the old oracle pass alone and fail
    after real gate traffic.
    """

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((host, port))
        server_conn, _peer = listener.accept()
        server_conn.close()  # server-side active close -> (host, port) enters TIME_WAIT
    finally:
        client.close()
        listener.close()


def test_gate_http_port_is_reserved_until_release(gate_http_port):
    host, _reserved_port = gate_http_port.address
    # A competitor with the SAME reuse semantics as the real subject server
    # (TmuxWebtermHTTPServer.allow_reuse_address == 1) still cannot take the port while the lease
    # holds it. The lease is an exclusive LISTENING reservation, so a like-for-like SO_REUSEADDR
    # competitor's bind is refused -- the negative must exclude a reuse-enabled server, not merely
    # a plain binder, or it would not model what actually races for the port under gate load.
    competing = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    competing.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            competing.bind(gate_http_port.address)
    finally:
        competing.close()
    port = gate_http_port.release()
    # The real defect this oracle now covers: the post-release rebind must tolerate retained
    # kernel state. Deliberately leave a predecessor TIME_WAIT on this exact port, then prove the
    # real reuse owner (HttpPortLease.reacquire, the SO_REUSEADDR path the subject server uses)
    # rebinds immediately. The old oracle rebound with a plain socket, which false-negatived on
    # ambient TIME_WAIT from prior gate traffic -- passing alone and failing after real load.
    _leave_predecessor_time_wait(host, port)
    try:
        assert gate_http_port.reacquire() == port
        assert gate_http_port.reserved is True
    finally:
        gate_http_port.release()


def test_gate_http_port_candidates_are_partitioned_by_xdist_worker():
    worker_zero = gate_http_port_candidates(worker="gw0", worker_count=3, lane="")
    worker_one = gate_http_port_candidates(worker="gw1", worker_count=3, lane="")
    worker_two = gate_http_port_candidates(worker="gw2", worker_count=3, lane="")

    assert worker_zero
    assert worker_one
    assert worker_two
    assert set(worker_zero).isdisjoint(worker_one)
    assert set(worker_zero).isdisjoint(worker_two)
    assert set(worker_one).isdisjoint(worker_two)
    assert tuple(sorted((*worker_zero, *worker_one, *worker_two))) == tuple(range(7900, 8000))
    assert gate_http_port_candidates(worker="gw1", worker_count=3, lane="") == worker_one


def test_gate_http_port_candidates_are_partitioned_across_parallel_check_lanes_and_workers():
    owners = {
        (lane, worker): set(gate_http_port_candidates(worker=f"gw{worker}", worker_count=3, lane=lane))
        for lane in PYTEST_LANE_NAMES
        for worker in range(3)
    }
    assert all(owners.values())
    for owner, ports in owners.items():
        assert all(ports.isdisjoint(other_ports) for other, other_ports in owners.items() if other != owner), owner
    assert set().union(*owners.values()) == set(range(7900, 8000))


def test_gate_tmux_uses_private_socket_and_fixture_session_name(gate_tmux):
    assert re.fullmatch(rf"yt-{os.getpid()}-[0-9a-f]+-1", gate_tmux.sessions[0])
    assert os.environ["YOLOMUX_TMUX_SOCKET"] == str(gate_tmux.socket_path)
    listed = run_isolated_tmux(gate_tmux, "list-sessions", "-F", "#{session_name}")
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.splitlines() == gate_tmux.sessions


def test_repeat_reports_the_exact_failing_iteration():
    def assertion(iteration):
        assert iteration != 2, "injected repeat defect"

    with pytest.raises(RepeatFailure, match=r"iteration 2/3 failed") as captured:
        repeat(3, assertion)

    assert captured.value.iteration == 2
    assert captured.value.total == 3


def test_counter_sampler_asserts_only_the_two_sample_delta():
    samples = iter((800, 802))
    observed = []
    result = assert_counter_delta(
        lambda: next(samples),
        lambda: observed.append("quiet-window-closed"),
        label="transport teardowns",
        exactly=2,
    )

    assert observed == ["quiet-window-closed"]
    assert result.before == 800
    assert result.after == 802
    assert result.delta == 2


def test_counter_sampler_failure_reports_both_samples_and_delta():
    samples = iter((400, 403))
    with pytest.raises(AssertionError, match=r"delta was 3.*before=400, after=403"):
        assert_counter_delta(lambda: next(samples), lambda: None, at_most=2)


class FakeBrowserDriver:
    def __init__(self):
        self.calls = []

    def execute_async_script(self, script, *args):
        self.calls.append(("async", args))
        return {
            "ready": True,
            "elapsedMs": 4,
            "missingGlobals": [],
            "missingAnchors": [],
            "readyState": "complete",
            "url": "http://127.0.0.1/",
        }

    def execute_script(self, script, *args):
        self.calls.append(("sync", args))
        if "getComputedStyle" in script:
            return {"display": "block", "visibility": "visible"}
        return "script-result"


def test_browser_script_waits_for_its_exact_dependencies_before_execution():
    driver = FakeBrowserDriver()
    result = run_when_browser_ready(
        driver,
        "return fileEditorItemFor(arguments[0]);",
        "session-1",
        globals_required={"fileEditorItemFor": "function", "applyLayoutSlots": "function"},
        dom_anchors=("#grid",),
        timeout=7,
    )

    assert result == "script-result"
    assert [kind for kind, _args in driver.calls] == ["async", "sync"]
    assert driver.calls[0][1] == (
        {"fileEditorItemFor": "function", "applyLayoutSlots": "function"},
        ["#grid"],
        7000,
    )
    assert driver.calls[1][1] == ("session-1",)


def test_computed_style_assertion_reads_rendered_style_values():
    driver = FakeBrowserDriver()
    actual = assert_computed_style(
        driver,
        "#status",
        {"display": "block", "visibility": lambda value: value != "hidden"},
    )

    assert actual == {"display": "block", "visibility": "visible"}


def _performance_payload(runtime):
    response = gate_http_request(runtime, "/api/diagnostics/performance")
    assert response.status == HTTPStatus.OK
    return response.json()


def _transport_teardown_total(runtime):
    transport = _performance_payload(runtime).get("transport")
    assert isinstance(transport, dict), "L1 requires transport counters at the HTTP diagnostics boundary"
    total = transport.get("teardowns_total")
    assert isinstance(total, int) and not isinstance(total, bool), transport
    return total


def test_l1_transport_teardown_delta_is_zero_for_one_healthy_request(gate_runtime_paths, monkeypatch):
    socket_path = gate_runtime_paths.state_dir / "services" / "l1-healthy.sock"
    client = LocalServiceClient("gate-l1-healthy", "gate.module", socket_path, service_dir=socket_path.parent)
    before = local_services_registry.transport_diagnostics()
    with monkeypatch.context() as synthetic_transport:
        synthetic_transport.setattr(
            local_service_client_module,
            "local_service_request",
            lambda *_args, **_kwargs: ({"ok": True}, b""),
        )
        response, body = client.request_with_binary({"action": "status"})
    after = local_services_registry.transport_diagnostics()

    assert (response, body) == ({"ok": True}, b"")
    assert after == before


def test_l1_failed_transport_increments_the_teardown_counter_once(
    gate_runtime_paths,
    monkeypatch,
):
    socket_path = gate_runtime_paths.state_dir / "services" / "l1-failure.sock"
    client = LocalServiceClient("gate-l1", "gate.module", socket_path, service_dir=socket_path.parent)

    def fail_request(*_args, **_kwargs):
        raise TimeoutError("fixture L1 transport teardown")

    before_payload = local_services_registry.transport_diagnostics()
    before_total = before_payload["teardowns_total"]
    before_by_type = before_payload["teardowns_by_exception"].get("TimeoutError", 0)
    with monkeypatch.context() as synthetic_transport:
        synthetic_transport.setattr(local_service_client_module, "local_service_request", fail_request)
        response, body = client.request_with_binary({"action": "status"})
    after_payload = local_services_registry.transport_diagnostics()

    assert response["exception_type"] == "TimeoutError"
    assert body == b""
    assert after_payload["teardowns_total"] - before_total == 1
    assert after_payload["teardowns_by_exception"]["TimeoutError"] - before_by_type == 1


def test_l1_transport_diagnostics_record_maps_through_http(gate_live_server, monkeypatch):
    expected = {
        "teardowns_total": 7,
        "teardowns_by_exception": {"FileNotFoundError": 2, "TimeoutError": 5},
    }
    monkeypatch.setattr(local_services_registry, "transport_diagnostics", lambda: expected)

    assert _performance_payload(gate_live_server)["transport"] == expected


def _follow_operation_terminal(runtime, response):
    if response.status != HTTPStatus.ACCEPTED:
        return response
    receipt = response.json()
    assert receipt["state"] == "queued" and receipt.get("terminal") is not True, receipt
    operation = receipt["operation"]
    operation_id = operation["id"]
    status_url = operation["status_url"]
    assert status_url == f"/api/operations/{operation_id}", receipt
    terminal_payload, terminal_status = WebDriverWait(
        runtime,
        5.0,
        poll_frequency=0.02,
    ).until(
        lambda app_runtime: (
            result
            if (result := app_runtime.app.operation_status_payload(operation_id))[1] != HTTPStatus.ACCEPTED
            else False
        )
    )
    terminal = gate_http_request(runtime, status_url)
    assert terminal.status == terminal_status, (terminal.status, terminal.body, terminal_payload)
    assert terminal.json() == terminal_payload, (terminal.json(), terminal_payload)
    assert terminal_payload["state"] in {"ready", "failed"}, terminal_payload
    assert terminal_payload["request"]["id"] == receipt["request"]["id"], terminal_payload
    return terminal


def _endpoint_compute_sample(runtime, path, surface):
    request_id = f"r-gate-budget-{uuid.uuid4().hex}"
    connection = HTTPConnection("127.0.0.1", runtime.port, timeout=8.0)
    try:
        connection.request("GET", path, headers={"X-YOLOmux-Request-ID": request_id})
        response = connection.getresponse()
        endpoint_response = GateHttpResponse(
            status=int(response.status),
            headers={str(name): str(value) for name, value in response.getheaders()},
            body=response.read(),
        )
        endpoint_payload = endpoint_response.json()
        response_request = endpoint_payload.get("request") if isinstance(endpoint_payload, dict) else None
        correlation_id = str(response_request.get("id") or request_id) if isinstance(response_request, dict) else request_id
        terminal = _follow_operation_terminal(runtime, endpoint_response)
        assert terminal.status == HTTPStatus.OK, (path, terminal.status, terminal.body)
        # The handler records performance after writing the endpoint body. Reusing its HTTP/1.1
        # connection makes this diagnostics request an exact completion barrier for that handler.
        connection.request("GET", "/api/diagnostics/performance")
        diagnostics = connection.getresponse()
        diagnostics_body = diagnostics.read()
        assert diagnostics.status == HTTPStatus.OK, (diagnostics.status, diagnostics_body)
        recent = json.loads(diagnostics_body).get("perf", {}).get("recent", [])
    finally:
        connection.close()
    matches = [
        row
        for row in recent
        if row.get("role") == "http-endpoint"
        and row.get("surface") == surface
        and isinstance(row.get("details"), dict)
        and row["details"].get("request_id") == correlation_id
    ]
    assert matches, f"no performance record for {surface}"
    compute_ms = matches[-1].get("compute_ms")
    assert isinstance(compute_ms, (int, float)) and not isinstance(compute_ms, bool), matches[-1]
    return float(compute_ms)


def test_l2_healthy_endpoint_compute_stays_within_budget(gate_live_server, monkeypatch, tmp_path):
    readable = tmp_path / "endpoint-budget.txt"
    readable.write_text("fixture-owned endpoint budget\n", encoding="utf-8")
    monkeypatch.setattr(
        gate_live_server.app.stats_current_http,
        "delta",
        lambda *_args, **_kwargs: SnapshotHttpResult(HTTPStatus.OK, body=b"{}"),
    )
    endpoints = (
        ("GET /api/session-metadata", "/api/session-metadata"),
        ("GET /api/fs/read", f"/api/fs/read?{urlencode({'path': str(readable)})}"),
        (
            "GET /api/stats-delta",
            "/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=gate"
            "&after_cache_generation=0&after_revision=0",
        ),
    )
    filesystem_response = gate_http_request(gate_live_server, endpoints[1][1])
    filesystem_terminal = _follow_operation_terminal(gate_live_server, filesystem_response)
    assert filesystem_terminal.status == HTTPStatus.OK, (
        filesystem_terminal.status,
        filesystem_terminal.body,
    )
    # /api/lost-sessions is not a v0.6.10 route, so its baseline is explicitly N/A.
    samples = {
        surface: tuple(_endpoint_compute_sample(gate_live_server, path, surface) for _sample in range(2))
        for surface, path in endpoints
    }
    assert all(value <= ENDPOINT_COMPUTE_BUDGET_MS for values in samples.values() for value in values), samples


def test_l2_unreadable_descendant_keeps_endpoints_available_and_within_budget(
    gate_live_server,
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "endpoint-budget-tree"
    root.mkdir()
    readable = root / "visible.txt"
    readable.write_text("fixture-owned unreadable-descendant budget\n", encoding="utf-8")
    restricted = root / "restricted"
    restricted.mkdir()
    (restricted / "hidden.txt").write_text("unreadable fixture\n", encoding="utf-8")
    restricted.chmod(0)
    monkeypatch.setattr(
        gate_live_server.app.stats_current_http,
        "delta",
        lambda *_args, **_kwargs: SnapshotHttpResult(HTTPStatus.OK, body=b"{}"),
    )
    endpoints = (
        ("GET /api/session-metadata", "/api/session-metadata"),
        ("GET /api/fs/read", f"/api/fs/read?{urlencode({'path': str(readable)})}"),
        (
            "GET /api/stats-delta",
            "/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=gate-unreadable"
            "&after_cache_generation=0&after_revision=0",
        ),
    )
    try:
        assert stat.S_IMODE(restricted.stat().st_mode) == 0
        registered = gate_live_server.app.update_client_watch_roots(
            {"client_id": "gate-unreadable", "roots": [str(root)]}
        )
        signatures = [gate_live_server.app.filesystem_watch_signature_for_roots([str(root)])]
        filesystem_response = gate_http_request(gate_live_server, endpoints[1][1])
        filesystem_terminal = _follow_operation_terminal(gate_live_server, filesystem_response)
        assert filesystem_terminal.status == HTTPStatus.OK, (
            filesystem_terminal.status,
            filesystem_terminal.body,
        )
        samples = {
            surface: tuple(_endpoint_compute_sample(gate_live_server, path, surface) for _sample in range(2))
            for surface, path in endpoints
        }
        signatures.append(gate_live_server.app.filesystem_watch_signature_for_roots([str(root)]))
        visible = _follow_operation_terminal(
            gate_live_server,
            gate_http_request(gate_live_server, endpoints[1][1]),
        )
    finally:
        restricted.chmod(0o700)

    assert registered["roots"] == [str(root.resolve())], registered
    assert signatures[0] == signatures[1], signatures
    assert visible.status == HTTPStatus.OK, (visible.status, visible.body)
    assert b"fixture-owned unreadable-descendant budget" in visible.body, visible.body
    assert all(value <= ENDPOINT_COMPUTE_BUDGET_MS for values in samples.values() for value in values), samples


@pytest.mark.browser
@pytest.mark.socket
def test_l3_keypress_reaches_terminal_data_within_budget(browser, gate_live_server):
    session = load_gate_terminal_only_browser(browser, gate_live_server)
    ownership = claim_fixture_client_event_demand(browser)
    assert ownership["bound"]["sourceOrigin"] == gate_live_server.base_url
    samples = []
    for sample in range(2):
        marker = f"gatekeypress{os.getpid()}{sample}"
        started = time.monotonic()
        for character in marker:
            _send_native_key(browser, character)
        observed, panes = wait_for_isolated_tmux_panes(
            gate_live_server.tmux,
            (session,),
            lambda captured: marker in captured.get(session, ""),
            timeout=TERMINAL_KEYPRESS_BUDGET_SECONDS,
            join_wrapped_lines=True,
        )
        samples.append(time.monotonic() - started)
        assert observed, {"sample": sample + 1, "elapsed_seconds": samples[-1], "panes": panes}
    measurements = {
        "samples_seconds": samples,
        "max_seconds": max(samples),
        "spread_seconds": max(samples) - min(samples),
        "budget_seconds": TERMINAL_KEYPRESS_BUDGET_SECONDS,
    }
    print(f"L3 baseline: {measurements}")
    assert max(samples) <= TERMINAL_KEYPRESS_BUDGET_SECONDS, {**measurements, "panes": panes}
    assert_fixture_client_event_demand_claimed(browser)
    released = release_fixture_client_event_demand(browser)
    assert set(released["released"]["pendingBefore"]).issubset(released["released"]["demandOperations"]), released
    assert released["settled"]["pending"] == [], released
    assert released["settled"]["batchQueued"] == 0, released
    assert released["settled"]["batchPending"] == 0, released
    assert released["settled"]["batchOperations"] == 0, released


@pytest.mark.browser
@pytest.mark.socket
def test_l4_one_rename_mutation_issues_one_forced_metadata_refresh(browser, gate_live_server):
    session = gate_live_server.tmux.sessions[0]
    renamed = f"yt-{os.getpid()}-{uuid.uuid4().hex[:10]}-1"
    load_gate_browser(browser, gate_live_server)
    result = browser.execute_async_script(
        """
        const session = arguments[0];
        const renamed = arguments[1];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch;
        const metadataRequests = [];
        window.fetch = (url, options) => {
          if (String(url).startsWith('/api/session-metadata?force=1')) metadataRequests.push(String(url));
          return originalFetch(url, options);
        };
        renameTmuxSession(session, renamed)
          .then(async mutationResult => {
            if (transcriptMetadataState.request) await transcriptMetadataState.request;
            done({mutationResult, metadataRequests});
          })
          .catch(error => done({error: String(error?.stack || error), metadataRequests}))
          .finally(() => { window.fetch = originalFetch; });
        """,
        session,
        renamed,
    )
    assert "error" not in result, result
    assert result["mutationResult"] is True, result
    assert result["metadataRequests"] == ["/api/session-metadata?force=1"], result


def test_l5_runtime_process_ledger_keeps_launcher_and_finds_untracked_orphan(
    gate_live_server,
    gate_runtime_paths,
    monkeypatch,
):
    launcher_pid = os.getpid()
    tracked_pid = launcher_pid * 10 + 1
    orphan_pid = launcher_pid * 10 + 2
    tracked_pgid = tracked_pid
    orphan_pgid = orphan_pid
    tracked_socket = gate_runtime_paths.state_dir / "services" / "tracked.sock"
    orphan_socket = gate_runtime_paths.state_dir / "services" / "orphan.sock"
    process_table = {
        tracked_pid: local_services_registry.ProcessTableEntry(
            launcher_pid,
            tracked_pgid,
            0.25,
            f"python3 -m yolomux_lib.gate_tracked --socket {tracked_socket}",
        ),
        orphan_pid: local_services_registry.ProcessTableEntry(
            orphan_pid + 1000,
            orphan_pgid,
            4.5,
            f"python3 -m yolomux_lib.gate_orphan --socket {orphan_socket}",
        ),
    }
    monkeypatch.setattr(local_services_registry, "bounded_process_table", lambda: process_table)
    monkeypatch.setattr(local_services_registry, "tracked_port_process_group", lambda *_args: {})
    monkeypatch.setattr(
        local_services_registry,
        "tracked_local_service_groups",
        lambda *_args: [{
            "service": "gate-service",
            "pid": tracked_pid,
            "pgid": tracked_pgid,
            "member_pids": [tracked_pid],
            "launcher_pid": launcher_pid,
            "launcher_port": gate_live_server.port,
        }],
    )
    ledger = gate_live_server.app.runtime_process_ledger()
    group = ledger["service_groups"][0]
    orphans = ledger.get("untracked_orphans")
    launcher_alive = False
    if isinstance(group.get("launcher_pid"), int):
        try:
            os.kill(group["launcher_pid"], 0)
            launcher_alive = True
        except OSError:
            pass
    observed_orphans = orphans if isinstance(orphans, list) else []
    assert {
        "launcher_pid": group.get("launcher_pid"),
        "launcher_alive": launcher_alive,
        "orphan_pids": [row.get("pid") for row in observed_orphans],
        "orphan_pgids": [row.get("pgid") for row in observed_orphans],
        "orphan_sockets": [row.get("socket") for row in observed_orphans],
        "tracked_pid_misclassified": any(row.get("pid") == tracked_pid for row in observed_orphans),
    } == {
        "launcher_pid": launcher_pid,
        "launcher_alive": True,
        "orphan_pids": [orphan_pid],
        "orphan_pgids": [orphan_pgid],
        "orphan_sockets": [str(orphan_socket)],
        "tracked_pid_misclassified": False,
    }


@pytest.mark.parametrize(
    ("error", "expected_type"),
    (
        (FileNotFoundError(errno.ENOENT, "fixture socket absent"), "FileNotFoundError"),
        (TimeoutError("fixture transport timed out"), "TimeoutError"),
    ),
)
def test_l6_transport_error_records_name_a_varying_exception_type(
    gate_live_server,
    gate_runtime_paths,
    monkeypatch,
    error,
    expected_type,
):
    socket_path = gate_runtime_paths.state_dir / "services" / "missing.sock"
    client = LocalServiceClient("gate", "gate.module", socket_path, service_dir=socket_path.parent)

    def fail_request(*_args, **_kwargs):
        raise error

    boundary = SERVER_LOGS.payload()["sequence"]
    with monkeypatch.context() as synthetic_transport:
        synthetic_transport.setattr(local_service_client_module, "local_service_request", fail_request)
        payload, body = client.request_with_binary({"action": "status"})
    response = gate_http_request(gate_live_server, "/api/logs")
    assert response.status == HTTPStatus.OK, (response.status, response.body)
    visible_logs = [
        row for row in response.json().get("logs", [])
        if int(row.get("id") or 0) > boundary
        and row.get("source") == "local-service:gate"
        and row.get("event") == "status"
    ]
    assert isinstance(visible_logs, list), visible_logs
    visible_text = "\n".join(str(row.get("message") or "") for row in visible_logs)
    assert {
        "body": body,
        "ok": payload.get("ok"),
        "exception_type": payload.get("exception_type"),
        "cause_exception_type": payload.get("cause", {}).get("exception", {}).get("type"),
        "cause_frames": bool(payload.get("cause", {}).get("frames")),
        "logs_name_exception_type": expected_type in visible_text,
        "logs_name_cause": str(error) in visible_text,
        "logs_include_traceback": "Traceback (most recent call last)" in visible_text,
    } == {
        "body": b"",
        "ok": False,
        "exception_type": expected_type,
        "cause_exception_type": expected_type,
        "cause_frames": True,
        "logs_name_exception_type": True,
        "logs_name_cause": True,
        "logs_include_traceback": True,
    }, {"payload": payload, "visible_logs": visible_logs}
    gate_live_server.server_log_boundary = SERVER_LOGS.payload()


def test_l6_absent_socket_recovery_does_not_emit_a_transport_error(
    gate_live_server,
    gate_runtime_paths,
    monkeypatch,
):
    socket_path = gate_runtime_paths.state_dir / "services" / "recoverable.sock"
    client = LocalServiceClient("gate-recover", "gate.module", socket_path, service_dir=socket_path.parent)
    attempts = []
    emitted = []

    def request_after_restart(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise FileNotFoundError(errno.ENOENT, "fixture socket absent")
        return {"ok": True, "recovered": True}, b""

    boundary = SERVER_LOGS.payload()["sequence"]
    with monkeypatch.context() as synthetic_transport:
        synthetic_transport.setattr(local_service_client_module, "local_service_request", request_after_restart)
        synthetic_transport.setattr(client.registry, "ensure_started", lambda: True)
        synthetic_transport.setattr(local_service_client_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))
        payload, body = client.request_with_binary({"action": "status"})
    response = gate_http_request(gate_live_server, "/api/logs")
    visible_logs = [row for row in response.json().get("logs", []) if int(row.get("id") or 0) > boundary]
    visible_text = "\n".join(str(row.get("message") or "") for row in visible_logs)

    assert response.status == HTTPStatus.OK, (response.status, response.body)
    assert (payload, body, len(attempts)) == ({"ok": True, "recovered": True}, b"", 2)
    assert emitted == []
    assert "local-service:gate-recover" not in visible_text


@pytest.mark.parametrize(
    ("service", "action"),
    (
        pytest.param("batchd", "status", id="class-1-batchd-absent"),
        pytest.param("approvald", "status", id="class-2-approvald-absent"),
    ),
)
def test_l6_idle_exited_service_request_recovers_without_transport_error(
    gate_runtime_paths,
    monkeypatch,
    service,
    action,
):
    monkeypatch.setenv(local_services_registry.LOCAL_SERVICE_IDLE_SECONDS_ENV, "0.1")
    socket_path = gate_runtime_paths.runtime_dir / "services" / f"{service}-{action}.sock"
    client = BatchClient(socket_path) if service == "batchd" else ApprovalClient(socket_path)
    request = {"action": action}

    assert client.ensure_started() is True
    exited_process = client.registry.process
    assert exited_process is not None
    WebDriverWait(client, 4.0, poll_frequency=0.05).until(
        lambda _client: exited_process.poll() is not None and not client.socket_path.exists()
    )

    red_boundary = SERVER_LOGS.payload()["sequence"]
    with monkeypatch.context() as disabled_recovery:
        disabled_recovery.setattr(client.registry, "ensure_started", lambda: False)
        failed = client.request(request, timeout=0.5)
    red_errors = [
        entry for entry in SERVER_LOGS.payload()["logs"]
        if int(entry.get("id") or 0) > red_boundary
        and entry["level"] == "error"
        and entry["source"] == f"local-service:{service}"
        and entry.get("event") == action
    ]
    assert failed.get("ok") is False
    assert len(red_errors) == 1
    assert failed.get("exception_type") == "FileNotFoundError"
    assert failed.get("_transport_error") == "absent"
    assert "FileNotFoundError" in red_errors[0]["message"]

    recovery_boundary = SERVER_LOGS.payload()["sequence"]
    try:
        recovered = client.request(request, timeout=0.5)
        assert recovered.get("ok") is True, recovered
        assert [
            entry for entry in SERVER_LOGS.payload()["logs"]
            if int(entry.get("id") or 0) > recovery_boundary
            and entry["level"] == "error"
            and entry["source"] == f"local-service:{service}"
            and entry.get("event") == action
        ] == []
    finally:
        if client.socket_path.exists():
            client.request({"action": "shutdown"}, timeout=0.5)
        replacement_process = client.registry.process
        if replacement_process is not None:
            WebDriverWait(client, 4.0, poll_frequency=0.05).until(
                lambda _client: replacement_process.poll() is not None
            )


@pytest.mark.parametrize(
    ("handler_limit", "served_while_occupied"),
    (
        pytest.param(
            batchd_module.BATCHD_CONCURRENT_HANDLER_LIMIT,
            True,
            id="shipped-capacity-serves-a-second-client",
        ),
        pytest.param(0, False, id="negative-control-serial-listener-cannot"),
    ),
)
def test_l6_batchd_serves_a_product_read_while_another_handler_is_occupied(
    gate_runtime_paths,
    monkeypatch,
    handler_limit,
    served_while_occupied,
):
    """A cheap last-known-good `product` read must not be charged another client's handler.

    batchd's `_relay` blocks on its job's completion event for up to BATCHD_MAX_DEADLINE_MS.  On a
    serial listener that wait is charged to every other client while staying invisible on the
    wire, because `accept_to_read_ms` starts after `accept()` returns and the envelope omits its
    queue/capacity fields while `capacity_limit` is 0 -- which is exactly why the caller's only
    available diagnosis is the literally unattributed `LocalRpcError: unattributed_latency`.
    """
    socket_path = gate_runtime_paths.runtime_dir / "services" / f"batchd-capacity-{handler_limit}.sock"
    monkeypatch.setattr(batchd_module, "BATCHD_CONCURRENT_HANDLER_LIMIT", handler_limit)
    service = PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    handle = service.handle
    occupied = threading.Event()
    release = threading.Event()

    def occupy_one_handler(request, request_binary=b""):
        if request.get("coalesce_key") == "gate-capacity-contender":
            occupied.set()
            release.wait(30.0)
            return {"ok": True, "state": "none", "generation": 0, "inflight": False}, b""
        return handle(request, request_binary)

    service.handle = occupy_one_handler
    service_thread = threading.Thread(target=service.run, daemon=True)
    service_thread.start()
    client = BatchClient(socket_path)
    WebDriverWait(client, 4.0, poll_frequency=0.02).until(lambda _client: client.registry.healthy())

    read: list[tuple[dict, bytes]] = []
    contender = threading.Thread(
        target=lambda: client.product("gate-capacity-contender", timeout=30.0),
        daemon=True,
    )
    second_client = threading.Thread(
        target=lambda: read.append(client.product("gate-capacity-second-client", timeout=30.0)),
        daemon=True,
    )
    try:
        contender.start()
        assert occupied.wait(4.0) is True, "the contender never reached batchd's handler"
        second_client.start()
        second_client.join(timeout=2.0)
        served = not second_client.is_alive()
        # The observation is only meaningful while the first handler is still held, so prove
        # the contender had not been released before the second client's outcome was read.
        assert release.is_set() is False
        assert served is served_while_occupied, (handler_limit, served, read)
        if served:
            metadata, body = read[0]
            assert metadata.get("ok") is True, metadata
            assert metadata.get("state") == "none", metadata
            assert body == b""
    finally:
        release.set()
        second_client.join(timeout=4.0)
        contender.join(timeout=4.0)
        service.stop_event.set()
        WebDriverWait(service_thread, 4.0, poll_frequency=0.02).until(lambda thread: not thread.is_alive())


def test_l6_local_service_over_budget_response_is_delivered_not_raised():
    """A complete, request-id-matched response past the budget is DELIVERED, never a raised error.

    The deadline is a telemetry budget, not a correctness bound. The former behavior raised
    `peer_handler_slow`/`unattributed_latency` post-response, turning a few milliseconds of jitter
    into a hard 503 on a slow product poll. That post-response error vocabulary is retired: the
    over-budget attribution is now a diagnostic label on the delivered record, decided by the
    peer's own handler duration versus the budget.
    """
    # No obsolete post-response error vocabulary: rpc no longer RAISES either over-budget label.
    emitted = set(re.findall(r'LocalRpcError\("([^"]+)"\)', inspect.getsource(local_service_rpc_module)))
    assert local_service_rpc_module.LOCAL_RPC_OVER_BUDGET_HANDLER not in emitted
    assert local_service_rpc_module.LOCAL_RPC_OVER_BUDGET_UNATTRIBUTED not in emitted
    # Oracle preserved: a wrong response (request_id mismatch) IS still a genuine raised failure.
    assert "response request_id mismatch" in emitted

    # The over-budget attribution separates a slow handler from latency before the handler ran,
    # from the one measurement the delivery path already holds.
    envelope = local_service_rpc_module.new_envelope("testd", "history", {"action": "history"}, timeout_seconds=0.01)

    def response_with_service_ms(service_duration_ms):
        return local_service_rpc_module.LocalRpcEnvelope(
            service="testd",
            method="history",
            request_id=envelope.request_id,
            trace_id=envelope.trace_id,
            deadline_ms=envelope.deadline_ms,
            priority=envelope.priority,
            owner_generation=envelope.owner_generation,
            config_generation=envelope.config_generation,
            payload={"ok": True},
            service_duration_ms=service_duration_ms,
        )

    slow_handler = response_with_service_ms(envelope.deadline_ms + 5.0)
    pre_handler_latency = response_with_service_ms(0.0)
    assert (
        local_service_rpc_module._over_budget_attribution(slow_handler, envelope.deadline_ms)
        == local_service_rpc_module.LOCAL_RPC_OVER_BUDGET_HANDLER
    )
    assert (
        local_service_rpc_module._over_budget_attribution(pre_handler_latency, envelope.deadline_ms)
        == local_service_rpc_module.LOCAL_RPC_OVER_BUDGET_UNATTRIBUTED
    )

    # A real connect/send/receive timeout BEFORE any response envelope exists remains the only
    # transport failure, and it is still transient for a bounded retry.
    assert local_service_failure_is_transient(
        {"ok": False, "_transport_error": "timeout", "error": "timed out"}
    ) is True
    # Negative controls: a terminal or protocol failure is not retryable.
    assert local_service_failure_is_transient(
        {"ok": False, "_transport_error": "rpc", "error": "unsupported RPC version"}
    ) is False
    assert local_service_failure_is_transient(
        {"ok": False, "terminal": True, "_transport_error": "timeout", "error": "timed out"}
    ) is False


def test_l6_batchd_product_response_past_deadline_is_delivered_not_retried(
    gate_runtime_paths,
    monkeypatch,
):
    """A real over-budget product response off the real transport is DELIVERED, not retried.

    The peer handler runs past the envelope's telemetry budget (10 ms) but well inside the socket
    receive timeout (0.5 s), so a complete, request-id-matched response arrives. The former
    behavior raised `peer_handler_slow`, logged an Error, and a slow product poll collapsed into a
    503 on GET /api/fs/read. Now the late-valid bytes are simply returned: there is no error to
    retry, no Error is logged, and the budget breach is visible only as telemetry.
    """
    socket_path = gate_runtime_paths.runtime_dir / "services" / "batchd-product-deadline.sock"
    service = PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    handle = service.handle

    def delayed_product(request, request_binary=b""):
        response = handle(request, request_binary)
        if request.get("action") == "product":
            time.sleep(0.05)
        return response

    service.handle = delayed_product
    service_thread = threading.Thread(target=service.run, daemon=True)
    service_thread.start()
    client = BatchClient(socket_path)
    WebDriverWait(client, 2.0, poll_frequency=0.02).until(lambda _client: client.registry.healthy())

    envelope_factory = local_service_client_module.new_envelope

    def deadline_before_transport_timeout(*args, **kwargs):
        kwargs["timeout_seconds"] = 0.01
        return envelope_factory(*args, **kwargs)

    recovery_attempts = []
    monkeypatch.setattr(local_service_client_module, "new_envelope", deadline_before_transport_timeout)
    monkeypatch.setattr(client.registry, "ensure_started", lambda: recovery_attempts.append(True) or True)
    boundary = SERVER_LOGS.payload()["sequence"]
    # Measure only this call's telemetry: the health warmup ran above, before the reset.
    reset_local_service_traffic()
    try:
        payload, body = client.product("gate-product-deadline", timeout=0.5)
        errors = [
            entry for entry in SERVER_LOGS.payload()["logs"]
            if int(entry.get("id") or 0) > boundary
            and entry["level"] == "error"
            and entry["source"] == "local-service:batchd"
        ]

        # The complete late-valid response is returned: no product for the key means state none.
        assert body == b""
        assert payload.get("ok") is True
        assert payload.get("state") == "none"
        # There is no error to retry and no post-response error vocabulary on the delivered record.
        assert local_service_failure_is_transient(payload) is False
        assert recovery_attempts == []
        assert errors == []
        assert "exception_type" not in payload and "_transport_error" not in payload

        # The budget breach is visible only as telemetry: a completion carrying a diagnostic label.
        work = local_service_traffic_ledger("batchd").snapshot()["work"]
        assert (work["completed"], work["errors"]) == (1, 0)
        assert work["over_budget"] == 1
        assert work["over_budget_by_reason"] == {"peer_handler_slow": 1}
    finally:
        service.stop_event.set()
        WebDriverWait(service_thread, 2.0, poll_frequency=0.02).until(lambda thread: not thread.is_alive())


def test_l7_every_http_accepted_ticket_is_visible_as_outstanding(gate_live_server, monkeypatch):
    ticket = {
        "ok": True,
        "status": "queued",
        "ticket": "pricing-ticket-7",
        "key": "pricing-refresh",
        "epoch": 7,
    }
    monkeypatch.setattr(gate_live_server.app, "pricing_catalog_refresh_start", lambda: ticket)
    response = gate_http_request(gate_live_server, "/api/pricing-catalog/refresh", method="POST")
    assert response.status == HTTPStatus.ACCEPTED

    queued_diagnostics = _performance_payload(gate_live_server)
    outstanding = queued_diagnostics.get("outstanding_queued")
    assert isinstance(outstanding, list), "L7 requires outstanding QUEUED diagnostics"
    assert len(outstanding) == 1, outstanding
    assert outstanding[0]["key"] == ticket["key"]
    assert outstanding[0]["epoch"] == ticket["epoch"]
    assert float(outstanding[0]["issued_at"]) > 0
    queued_frames = queued_diagnostics.get("queued_delivery_frames")
    assert isinstance(queued_frames, list), "L7 requires observable queued-delivery frames"
    matching_frames = [
        frame
        for frame in queued_frames
        if frame.get("stream") == ticket["key"] and frame.get("epoch") == ticket["epoch"]
    ]
    assert len(matching_frames) == 1, matching_frames
    assert matching_frames[0]["state"] == "open", matching_frames
    assert matching_frames[0]["seq"] == 0, matching_frames
    assert matching_frames[0]["stream"] != ticket["ticket"], matching_frames
    assert all("epoch" in frame and "seq" in frame for frame in matching_frames), matching_frames

    monkeypatch.setattr(
        gate_live_server.app,
        "pricing_catalog_status_payload",
        lambda: {"ok": True, "status": "ready", "key": ticket["key"], "epoch": ticket["epoch"]},
    )
    completed = gate_http_request(gate_live_server, "/api/pricing-catalog")
    assert completed.status == HTTPStatus.OK
    completed_diagnostics = _performance_payload(gate_live_server)
    assert completed_diagnostics["outstanding_queued"] == []
    completed_frames = [
        frame
        for frame in completed_diagnostics["queued_delivery_frames"]
        if frame.get("stream") == ticket["key"] and frame.get("epoch") == ticket["epoch"]
    ]
    terminals = [frame for frame in completed_frames if frame.get("state") in {"done", "error"}]
    assert len(terminals) == 1, completed_frames
    assert terminals[0]["stream"] != ticket["ticket"], terminals
    assert all("epoch" in frame and "seq" in frame for frame in completed_frames), completed_frames


def test_l7_real_pricing_ticket_identity_survives_until_terminal():
    class ImmediateCatalog:
        def refresh(self, _adapters):
            return {"ok": True, "status": "unchanged"}

    coordinator = PricingRefreshCoordinator(ImmediateCatalog(), adapters=())
    accepted = coordinator.start()
    coordinator._thread.join(timeout=2)
    terminal = coordinator.status()

    assert accepted["status"] == "running"
    assert terminal["status"] == "done"
    assert {
        "accepted_key": accepted.get("key"),
        "accepted_epoch": accepted.get("epoch"),
        "accepted_ticket": accepted.get("ticket"),
        "terminal_key": terminal.get("key"),
        "terminal_epoch": terminal.get("epoch"),
        "terminal_ticket": terminal.get("ticket"),
    } == {
        "accepted_key": "pricing-refresh",
        "accepted_epoch": 1,
        "accepted_ticket": "pricing-refresh-1",
        "terminal_key": "pricing-refresh",
        "terminal_epoch": 1,
        "terminal_ticket": "pricing-refresh-1",
    }

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

from yomux_lib.local_services import client as local_service_client_module
from yomux_lib.local_services import registry as local_services_registry
from yomux_lib.local_services import rpc as local_service_rpc_module
from yomux_lib.filesystem import git_ops
from yomux_lib.local_services.client import LocalServiceClient
from yomux_lib.local_services.client import local_service_failure_is_transient
from yomux_lib.local_services.rpc import local_service_traffic_ledger
from yomux_lib.local_services.rpc import reset_local_service_traffic
from yomux_lib.observability.pricing_catalog import PricingRefreshCoordinator
from yomux_lib.server_logs import SERVER_LOGS
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
from tests.native_gate_fixtures import gate_live_server
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


@pytest.mark.shared_tooling
def test_gate_runtime_paths_are_fixture_owned(gate_runtime_paths):
    assert gate_runtime_paths.home_dir.is_dir()
    assert gate_runtime_paths.config_dir.is_dir()
    assert gate_runtime_paths.state_dir.is_dir()
    assert os.environ["HOME"] == str(gate_runtime_paths.home_dir)
    assert os.environ["YOMUX_CONFIG_DIR"] == str(gate_runtime_paths.config_dir)
    assert os.environ["YOMUX_STATE_DIR"] == str(gate_runtime_paths.state_dir)
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
    assert patched_paths["yomux_lib.infra.common.AUTH_CONFIG_PATH"] == gate_runtime_paths.auth_config_path
    assert patched_paths["yomux_lib.auth.AUTH_CONFIG_PATH"] == gate_runtime_paths.auth_config_path


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
    # (FixtureHTTPServer.allow_reuse_address == 1) still cannot take the port while the lease
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
    assert os.environ["YOMUX_TMUX_SOCKET"] == str(gate_tmux.socket_path)
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
        connection.request("GET", path, headers={"X-YOmux-Request-ID": request_id})
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

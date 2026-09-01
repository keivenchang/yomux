import os
import time
from threading import Event
from threading import Thread

import pytest

from yolomux_lib import batchd
from yolomux_lib.local_services import client as local_service_client_mod
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec


def test_lease_release_terminal_upgrade_response_is_recorded_without_retry(monkeypatch):
    release_calls = []
    emitted = []
    terminal_response = {
        "ok": False,
        "terminal": True,
        "error_code": "upgrade_required",
        "required_protocol_version": 8,
    }

    def release(lease_id):
        release_calls.append(lease_id)
        return terminal_response

    monkeypatch.setattr(
        local_service_client_mod,
        "emit_server_log",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )

    owner = local_service_client_mod.release_local_service_lease_eventually(
        release,
        "lease-1",
        retry_seconds=60.0,
    )

    assert release_calls == ["lease-1"]
    assert owner.terminal_response == terminal_response
    assert owner.completed.is_set() is True
    assert owner._thread is None
    assert emitted == [
        (
            ("error", "local-service:lease-release", "lease release stopped: upgrade_required"),
            {
                "category": "lifecycle",
                "dedupe_key": "local-service:lease-release:upgrade_required",
                "dedupe_seconds": 5.0,
                "route": "local-service:lease-release",
                "event": "lease-release",
                "delivery": "terminal",
            },
        ),
    ]


@pytest.mark.parametrize("method", ("result", "product"))
def test_job_client_observation_does_not_launch_an_absent_service(method, tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    attempts = []
    launch_calls = []

    def request_once(payload, timeout, request_binary=b"", *, probe=False):
        attempts.append((dict(payload), timeout, request_binary, probe))
        return {"ok": False, "error": "socket is absent", "_transport_error": "absent"}, b"", None

    monkeypatch.setattr(client, "_request_once", request_once)
    monkeypatch.setattr(client.registry, "ensure_started", lambda: launch_calls.append(True) or True)

    if method == "result":
        response = client.result("job-1", timeout=0.04)
        expected_payload = {"action": "result", "job_id": "job-1"}
    else:
        response, body = client.product("product-1", timeout=0.04)
        expected_payload = {"action": "product", "coalesce_key": "product-1"}
        assert body == b""

    assert response["_transport_error"] == "absent"
    assert attempts == [(expected_payload, pytest.approx(0.04), b"", False)]
    assert launch_calls == []


@pytest.mark.parametrize("method", ("submit", "produce"))
def test_job_client_retries_explicit_prehandler_busy_within_one_rpc_budget(method, tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    clock = [100.0]
    waits = []
    attempts = []
    responses = iter((
        ({"ok": False, "error": "service busy", "capacity_rejected": True}, b""),
        ({"ok": True, "job": {"job_id": "accepted", "status": "queued"}}, b"accepted"),
    ))

    def request_once(payload, timeout, request_binary=b"", *, probe=False):
        attempts.append((dict(payload), timeout, request_binary, probe))
        response, body = next(responses)
        return response, body, None

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(client, "_request_once", request_once)
    monkeypatch.setattr(local_service_client_mod, "monotonic_clock", lambda: clock[0])
    monkeypatch.setattr(local_service_client_mod, "sleep_for", wait)

    if method == "submit":
        response = client.submit("json_compact", {"value": 1})
        body = b""
    else:
        response, body = client.produce("json_compact", {"value": 1})

    assert response["ok"] is True
    assert body == (b"accepted" if method == "produce" else b"")
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]
    assert attempts[0][1] == pytest.approx(0.5)
    assert attempts[1][1] == pytest.approx(0.495)
    assert waits == [pytest.approx(rpc.LOCAL_SERVICE_BUSY_RETRY_INITIAL_SECONDS)]


def test_job_client_retries_shutdown_admission_refusal_within_one_rpc_budget(tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    clock = [100.0]
    attempts = []
    responses = iter((
        ({"ok": False, "error": "service busy", "admission_rejected": True}, b""),
        ({"ok": True, "job": {"job_id": "accepted", "status": "queued"}}, b""),
    ))

    def request_once(payload, timeout, request_binary=b"", *, probe=False):
        attempts.append((dict(payload), timeout, request_binary, probe))
        response, body = next(responses)
        return response, body, None

    monkeypatch.setattr(client, "_request_once", request_once)
    monkeypatch.setattr(local_service_client_mod, "monotonic_clock", lambda: clock[0])
    monkeypatch.setattr(
        local_service_client_mod,
        "sleep_for",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    response = client.submit("json_compact", {"value": 1})

    assert response["ok"] is True
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]


def test_job_client_does_not_retry_generic_busy_without_prehandler_provenance(tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    attempts = []

    def request_once(payload, timeout, request_binary=b"", *, probe=False):
        attempts.append((dict(payload), timeout, request_binary, probe))
        return {"ok": False, "error": "service busy"}, b"", None

    monkeypatch.setattr(client, "_request_once", request_once)

    assert client.submit("json_compact", {"value": 1}) == {"ok": False, "error": "service busy"}
    assert len(attempts) == 1


@pytest.mark.parametrize("operation", ("ping", "status", "lease", "release", "shutdown"))
def test_registry_retries_real_unix_socket_capacity_refusal(operation, tmp_path, monkeypatch):
    socket_path = tmp_path / "fixture.sock"
    stop_event = Event()
    holding = Event()
    release = Event()
    holder_failures = []

    def handle(request, _body):
        action = request.get("action")
        if action == "hold":
            holding.set()
            if not release.wait(timeout=2.0):
                holder_failures.append("holder release timed out")
            return {"ok": True}, b""
        if action == "shutdown":
            stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        if action == "lease":
            return {"ok": True, "lease_id": "lease-1", "pid": os.getpid(), "leases": 1}, b""
        if action == "release":
            return {"ok": True, "leases": 0}, b""
        return {"ok": True, "version": 7, "pid": os.getpid(), "started_at": 1}, b""

    worker = Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path,
            lock_path=tmp_path / "fixture.lock",
            service_name="fixture",
            stop_event=stop_event,
            handle=handle,
            on_idle=lambda: False,
            on_client=lambda: None,
            concurrent_handlers=1,
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 2.0
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert socket_path.exists() is True

    holder = Thread(
        target=lambda: rpc.request(
            socket_path,
            rpc.new_envelope("fixture", "hold", {"action": "hold", "protocol_version": 7}),
            timeout_seconds=2.0,
        ),
        daemon=True,
    )
    holder.start()
    assert holding.wait(timeout=1.0) is True
    retry_waits = []

    def retry_sleep(seconds):
        retry_waits.append(seconds)
        release.set()
        time.sleep(seconds)

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", socket_path.name, 7),
        socket_path=socket_path,
        service_dir=tmp_path,
        sleep=retry_sleep,
    )
    try:
        if operation == "ping":
            assert registry.healthy() is True
        elif operation == "status":
            assert registry.status()["healthy"] is True
        elif operation == "lease":
            monkeypatch.setattr(registry, "ensure_started", lambda: True)
            assert registry.acquire_lease()["lease_id"] == "lease-1"
        elif operation == "release":
            assert registry.release_lease("lease-1") == {"ok": True, "leases": 0}
        else:
            assert registry._request("shutdown", timeout=0.25) == {"ok": True, "shutdown": True}
        assert retry_waits
    finally:
        release.set()
        stop_event.set()
        holder.join(timeout=2.0)
        worker.join(timeout=2.0)
    assert holder.is_alive() is False
    assert worker.is_alive() is False
    assert holder_failures == []


def test_registry_acquire_lease_retries_state_lock_busy_then_publishes_status(tmp_path, monkeypatch):
    clock = [100.0]
    waits = []
    attempts = []
    responses = iter((
        {"ok": False, "error": "service busy", "state_lock_rejected": True},
        {"ok": True, "lease_id": "lease-1", "pid": os.getpid(), "leases": 1},
        {"ok": True, "version": 7, "pid": os.getpid(), "started_at": 1},
    ))
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 7),
        clock=lambda: clock[0],
        sleep=lambda seconds: (waits.append(seconds), clock.__setitem__(0, clock[0] + seconds)),
    )
    monkeypatch.setattr(registry, "ensure_started", lambda: True)

    def request(_path, envelope, **kwargs):
        attempts.append((envelope.method, dict(envelope.payload), kwargs["timeout_seconds"]))
        return next(responses), b""

    monkeypatch.setattr(registry_mod, "request", request)

    assert registry.acquire_lease()["lease_id"] == "lease-1"
    assert [attempt[0] for attempt in attempts] == ["lease", "lease", "status"]
    assert attempts[0][2] == pytest.approx(0.25)
    assert attempts[1][2] == pytest.approx(0.245)
    assert waits == [pytest.approx(rpc.LOCAL_SERVICE_BUSY_RETRY_INITIAL_SECONDS)]


def test_registry_release_lease_retries_capacity_busy_then_succeeds(tmp_path, monkeypatch):
    clock = [100.0]
    waits = []
    attempts = []
    responses = iter((
        {"ok": False, "error": "service busy", "capacity_rejected": True},
        {"ok": True, "leases": 0},
    ))
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 7),
        clock=lambda: clock[0],
        sleep=lambda seconds: (waits.append(seconds), clock.__setitem__(0, clock[0] + seconds)),
    )

    def request(_path, envelope, **kwargs):
        attempts.append((envelope.method, dict(envelope.payload), kwargs["timeout_seconds"]))
        return next(responses), b""

    monkeypatch.setattr(registry_mod, "request", request)

    assert registry.release_lease("lease-1") == {"ok": True, "leases": 0}
    assert [attempt[0] for attempt in attempts] == ["release", "release"]
    assert attempts[0][1]["lease_id"] == attempts[1][1]["lease_id"] == "lease-1"
    assert attempts[0][2] == pytest.approx(0.25)
    assert attempts[1][2] == pytest.approx(0.245)
    assert waits == [pytest.approx(rpc.LOCAL_SERVICE_BUSY_RETRY_INITIAL_SECONDS)]


def test_job_client_does_not_retry_ambiguous_service_unavailable_response(tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    attempts = []

    def request_once(payload, timeout, request_binary=b"", *, probe=False):
        attempts.append((dict(payload), timeout, request_binary, probe))
        return {"ok": False, "status": 503, "error": "downstream unavailable"}, b"", None

    monkeypatch.setattr(client, "_request_once", request_once)

    response = client.submit("json_compact", {"value": 1})

    assert response == {"ok": False, "status": 503, "error": "downstream unavailable"}
    assert len(attempts) == 1

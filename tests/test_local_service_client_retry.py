import os
import threading
from threading import BoundedSemaphore
from threading import Event
from threading import Thread
from types import SimpleNamespace

import pytest

from yomux_lib.local_services import client as local_service_client_mod
from yomux_lib.local_services import registry as registry_mod
from yomux_lib.local_services import rpc
from yomux_lib.local_services import runtime
from yomux_lib.local_services.registry import LocalServiceRegistry
from yomux_lib.local_services.registry import LocalServiceSpec


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










@pytest.mark.parametrize("operation", ("ping", "status", "lease", "release", "shutdown"))
def test_registry_retries_real_unix_socket_capacity_refusal(operation, tmp_path, monkeypatch):
    socket_path = tmp_path / "fixture.sock"
    stop_event = Event()
    listener_ready = Event()
    holding = Event()
    release = Event()
    holder_failures = []
    handler_released = Event()
    clock = [0.0]

    class HandlerSlots(BoundedSemaphore):
        def __init__(self, value=1):
            super().__init__(value)
            self._fixture_owned = threading.current_thread() is worker

        def release(self, n=1):
            super().release(n)
            if self._fixture_owned:
                handler_released.set()

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
            on_start=listener_ready.set,
            concurrent_handlers=1,
        ),
        daemon=True,
    )
    holder = Thread(
        target=lambda: rpc.request(
            socket_path,
            rpc.new_envelope("fixture", "hold", {"action": "hold", "protocol_version": 7}),
            timeout_seconds=2.0,
        ),
        daemon=True,
    )
    # Keep the injected factory local to the runtime, and signal only this listener's slot.
    runtime_threading = SimpleNamespace(**vars(runtime.threading))
    runtime_threading.BoundedSemaphore = HandlerSlots
    monkeypatch.setattr(runtime, "threading", runtime_threading)
    assert threading.BoundedSemaphore is BoundedSemaphore
    retry_waits = []

    def retry_sleep(seconds):
        retry_waits.append(seconds)
        release.set()
        # Fixture scheduling is independent of the retry budget; wait for its owned slot.
        assert handler_released.wait(timeout=2.0) is True
        clock[0] += seconds

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", socket_path.name, 7),
        socket_path=socket_path,
        service_dir=tmp_path,
        clock=lambda: clock[0],
        sleep=retry_sleep,
    )
    try:
        worker.start()
        assert listener_ready.wait(timeout=2.0) is True
        assert socket_path.exists() is True
        holder.start()
        assert holding.wait(timeout=1.0) is True
        foreign_slots = runtime.threading.BoundedSemaphore(1)
        assert foreign_slots.acquire(blocking=False) is True
        foreign_slots.release()
        assert handler_released.is_set() is False, "an unrelated semaphore released the fixture's owned handler slot"
        if operation == "ping":
            assert registry.healthy() is True
        elif operation == "status":
            status = registry.status()
            assert status["healthy"] is True, status
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
        if holder.ident is not None:
            holder.join(timeout=2.0)
        if worker.ident is not None:
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

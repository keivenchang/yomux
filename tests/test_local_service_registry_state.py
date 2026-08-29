# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Characterization of split local-service registry lifecycle state."""

import os
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from yolomux_lib.local_services import registry as registry_module
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime
from yolomux_lib.local_services.registry import ChildOwnershipState
from yolomux_lib.local_services.registry import HealthProbeCache
from yolomux_lib.local_services.registry import LOCAL_SERVICE_HEALTH_CACHE_SECONDS
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.local_services.registry import StartupFailureState


def test_startup_failure_state_resets_one_complete_episode():
    state = StartupFailureState(
        failures=3,
        next_start_at=42.0,
        start_exit_count=2,
        last_exit_code=7,
        failure_reason="failed",
        record_refusal_reason="refused",
        terminal_failure=True,
    )

    state.reset()

    assert state == StartupFailureState()


def test_health_probe_cache_success_expiry_and_invalidation():
    cache = HealthProbeCache()

    cache.note_success(10.0)

    assert cache.healthy_until == 10.0 + LOCAL_SERVICE_HEALTH_CACHE_SECONDS
    assert cache.is_recent(cache.healthy_until - 0.001) is True
    assert cache.is_recent(cache.healthy_until) is False
    cache.invalidate()
    assert cache == HealthProbeCache()


def test_child_ownership_state_starts_without_a_child_or_reaper():
    state = ChildOwnershipState()

    assert state.process is None
    assert state.spawn_ownership is None
    assert state.adopted_reaper_pid == 0


def _fixture_registry(tmp_path):
    return LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )


def test_child_reaper_handle_is_retained_until_settlement(tmp_path, monkeypatch):
    registry = _fixture_registry(tmp_path)
    release = threading.Event()
    monkeypatch.setattr(registry, "_reap_exited_child", lambda _process: release.wait())

    registry._start_child_reaper(SimpleNamespace())

    assert tuple(thread.name for thread in registry._child_ownership.reaper_threads) == ("fixture-reaper",)
    with pytest.raises(RuntimeError, match="fixture-reaper"):
        registry.settle_reaper_threads(timeout=0)
    release.set()
    registry.settle_reaper_threads()
    assert registry._child_ownership.reaper_threads == set()


def test_adopted_reaper_handle_is_retained_until_settlement(tmp_path, monkeypatch):
    registry = _fixture_registry(tmp_path)
    release = threading.Event()
    monkeypatch.setattr(registry, "_read_record", lambda: {"pid": 43210})
    monkeypatch.setattr(
        registry_module,
        "process_record_diagnostic",
        lambda *_args, **_kwargs: SimpleNamespace(current=True),
    )
    # Arming this reaper is arming `waitpid`, so the product proves live
    # parentage before it starts a thread. Pid 43210 is a fixture number; 0 is
    # the refusal value the reader returns for anything this process did not
    # parent, never a default.
    monkeypatch.setattr(
        registry_module,
        "process_parent_id",
        lambda pid: os.getpid() if int(pid) == 43210 else 0,
    )
    monkeypatch.setattr(registry, "_reap_adopted_child", lambda _pid: release.wait())

    registry._arm_adopted_reaper()

    assert tuple(thread.name for thread in registry._child_ownership.reaper_threads) == (
        "fixture-adopted-reaper",
    )
    release.set()
    registry.settle_reaper_threads()
    assert registry._child_ownership.reaper_threads == set()


# ---------------------------------------------------------------------------
# Two queue-named lifecycle cases that previously lived only in
# tests/test_local_services_rpc.py, brought into the four-file owner set the
# queue's Done Criterion names: SELF-CONNECTION and LAST-CLIENT DISCONNECT.
# ---------------------------------------------------------------------------


class _ClaimState:
    """The two attributes ``claim_gated_idle_due`` reads and writes.

    Every real service (watchd, batchd, statusd, approvald, search_indexer,
    statsd) drives that one shared owner against its own ``last_client_at`` /
    ``idle_seconds``, so this stand-in is the whole of the state it touches.
    """

    def __init__(self, *, idle_seconds: float, last_client_at: float):
        self.idle_seconds = idle_seconds
        self.last_client_at = last_client_at


@pytest.mark.socket
@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="peer-pid credentials are Linux-only")
def test_a_self_connection_never_counts_as_demand_but_a_real_client_does(tmp_path):
    """The two halves of one rule, measured against the real accept loop.

    A connection whose peer PID is the service's own PID is the service talking
    to itself -- a wake-up, a health probe, a socket liveness check -- and must
    never refresh the demand clock. The external subprocess connect in the same
    test is the POSITIVE CONTROL: it proves ``on_client`` really does fire on
    this exact code path, so the empty self-connection observation is a decision
    the product made, not a callback that was never wired.

    Synchronisation is event-driven throughout; there is no sleep anywhere.
    """
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    listener_ready = threading.Event()
    client_observed = threading.Event()
    observations: list[str] = []

    def on_client():
        observations.append("external")
        client_observed.set()

    worker = threading.Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path,
            lock_path=lock_path,
            service_name="testd",
            stop_event=stop_event,
            handle=lambda _request, _request_binary: ({"ok": True}, b""),
            on_idle=lambda: False,
            on_client=on_client,
            on_start=listener_ready.set,
        ),
        daemon=True,
    )
    worker.start()
    client = None
    try:
        assert listener_ready.wait(timeout=5.0)

        # Same-process connection: a full, successful request/response round trip
        # so this cannot be dismissed as a connection that never reached the loop.
        envelope = rpc.new_envelope("testd", "echo", {"action": "echo"}, timeout_seconds=1.0)
        response, _binary = rpc.request(service_socket_path, envelope, timeout_seconds=1.0)
        assert response.get("ok") is True, "the self-connection never completed a real request"
        assert observations == [], "a same-process connection satisfied external-demand observation"

        client = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import socket, sys; s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); "
                f"s.connect({str(service_socket_path)!r}); "
                "sys.stdout.write('connected\\n'); sys.stdout.flush(); "
                "sys.stdin.readline(); s.close()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert client.stdout.readline() == "connected\n"
        assert client_observed.wait(timeout=5.0) is True, (
            "positive control failed: a genuinely external client never reached on_client, so the "
            "self-connection assertion above proves nothing"
        )
        assert observations == ["external"]
    finally:
        if client is not None:
            try:
                client.stdin.write("exit\n")
                client.stdin.flush()
                client.stdin.close()
            except OSError:
                pass
            if client.poll() is None:
                client.terminate()
                try:
                    client.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    client.kill()
                    client.wait()
            if client.stdout is not None and not client.stdout.closed:
                client.stdout.close()
        stop_event.set()
        worker.join(timeout=5.0)


@pytest.mark.socket
def test_the_last_client_disconnecting_retires_the_service_through_the_real_loop(tmp_path):
    """The accept loop itself must terminate once the last claim departs.

    The idle clock is virtual: it advances only when this test moves it, so the
    deadline is exact and nothing sleeps waiting for it. Two negative controls
    bracket the positive one -- the loop does NOT exit while the claim is held,
    and does NOT exit merely because time passed before the claim departed.
    """
    socket_path = tmp_path / "service.sock"
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    listener_ready = threading.Event()
    idle_observed = threading.Event()
    clock = [0.0]
    state = _ClaimState(idle_seconds=5.0, last_client_at=0.0)
    claim = {"present": True}

    def idle_probe():
        idle_observed.set()
        return runtime.claim_gated_idle_due(state, claim["present"], now=lambda: clock[0])

    worker = threading.Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path,
            lock_path=lock_path,
            service_name="testd",
            stop_event=stop_event,
            handle=lambda _request, _request_binary: ({"ok": True}, b""),
            on_idle=idle_probe,
            on_client=lambda: None,
            on_start=listener_ready.set,
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert listener_ready.wait(timeout=5.0)
        assert idle_observed.wait(timeout=5.0) is True, "the accept loop never ran its idle maintenance probe"

        # Negative control 1: a held claim keeps the service alive no matter how
        # far past the idle deadline the clock is pushed.
        clock[0] = state.idle_seconds * 10
        assert stop_event.wait(timeout=0.5) is False, "a service with a live claim retired anyway"
        assert state.last_client_at == clock[0], "the held claim did not refresh the deadline"

        # Negative control 2: the claim departs, but the deadline restarts from
        # the moment it was last seen -- elapsed time before departure is spent.
        claim["present"] = False
        clock[0] += state.idle_seconds - 0.1
        assert stop_event.wait(timeout=0.5) is False, "the service retired before its idle deadline"

        # Positive case: the deadline elapses after the last claim departed.
        clock[0] += 0.2
        assert stop_event.wait(timeout=5.0) is True, "the real accept loop never observed its idle deadline"
        worker.join(timeout=5.0)
        assert worker.is_alive() is False
        assert socket_path.exists() is False, "normal idle exit did not unlink its socket"
    finally:
        stop_event.set()
        worker.join(timeout=5.0)

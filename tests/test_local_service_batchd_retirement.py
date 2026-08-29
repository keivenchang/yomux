import signal

import pytest

from tests.helpers.local_service_records import FixtureProcessRecordBuilder
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec


def test_registry_waits_for_long_running_accepted_batchd_work_before_code_replacement(
    tmp_path,
    monkeypatch,
):
    current_protocol = 25
    service_protocol = current_protocol
    service_pid = 4242
    source_epoch = "retained-batchd"
    clock = [100.0]
    drain_completed_at = clock[0] + 1.2
    state = {"alive": True, "shutdown": False}
    signals = []
    actions = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "batchd",
            "yolomux_lib.batchd",
            "batchd.sock",
            current_protocol,
            code_revision="current-revision",
            build_revision=1,
        ),
        clock=lambda: clock[0],
        sleep=lambda seconds: (
            clock.__setitem__(0, clock[0] + seconds),
            state.__setitem__(
                "alive",
                state["alive"] and not (state["shutdown"] and clock[0] >= drain_completed_at),
            ),
        ),
    )
    registry._write_record({
        **FixtureProcessRecordBuilder(pid=service_pid).build(),
        "service": "batchd",
        "socket": str(registry.socket_path),
        "protocol_version": service_protocol,
        "source_epoch": source_epoch,
    })
    registry.socket_path.touch()

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        del timeout
        selected_protocol = current_protocol if protocol_version is None else protocol_version
        actions.append((method, selected_protocol))
        if method == "ping":
            return {
                "ok": True,
                "version": service_protocol,
                "build": 1,
                "code_revision": "stale-revision",
                "pid": service_pid,
                "source_epoch": source_epoch,
            }
        if method == "shutdown":
            assert payload == {
                "retirement_handshake": True,
                "expected_source_epoch": source_epoch,
            }
            state["shutdown"] = True
            return {
                "ok": True,
                "shutdown": True,
                "draining": True,
                "pid": service_pid,
                "version": service_protocol,
                "source_epoch": source_epoch,
            }
        if method == "status":
            return {
                "ok": True,
                "version": service_protocol,
                "pid": service_pid,
                "source_epoch": source_epoch,
                "active_records": [{"job_id": "accepted-job", "status": "running"}],
                "queues": {"freshness": 0},
            }
        return {}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: state["alive"])
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if state["alive"] else None,
    )
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))

    assert registry._retire_incompatible_service() is True

    assert clock[0] >= drain_completed_at
    assert signals == []
    assert [action for action, _protocol in actions] == ["ping", "shutdown"]
    assert {protocol for action, protocol in actions if action in {"status", "shutdown"}} == {
        service_protocol,
    }
    assert registry.record_path.exists() is False
    assert registry.socket_path.exists() is False


@pytest.mark.parametrize("active", (True, False))
def test_registry_legacy_batchd_shutdown_requires_observable_idle(active, tmp_path, monkeypatch):
    current_protocol = 25
    service_protocol = 24
    service_pid = 4242
    source_epoch = "retained-batchd"
    state = {"alive": True}
    actions = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "batchd",
            "yolomux_lib.batchd",
            "batchd.sock",
            current_protocol,
            code_revision="current-revision",
            build_revision=1,
        ),
    )
    registry._write_record({
        **FixtureProcessRecordBuilder(pid=service_pid).build(),
        "service": "batchd",
        "socket": str(registry.socket_path),
        "protocol_version": service_protocol,
        "source_epoch": source_epoch,
    })
    registry.socket_path.touch()

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        del timeout
        selected_protocol = current_protocol if protocol_version is None else protocol_version
        actions.append((method, selected_protocol))
        if method == "ping":
            return {
                "ok": False,
                "error": "upgrade_required",
                "required_protocol_version": service_protocol,
            }
        if method == "status":
            return {
                "ok": True,
                "version": service_protocol,
                "pid": service_pid,
                "source_epoch": source_epoch,
                "active_records": ([{"job_id": "accepted-job", "status": "running"}] if active else []),
                "queues": {"freshness": 0},
            }
        if method == "shutdown":
            assert payload == {
                "retirement_handshake": True,
                "expected_source_epoch": source_epoch,
            }
            state["alive"] = False
            return {"ok": True, "shutdown": True}
        return {}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: state["alive"])
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if state["alive"] else None,
    )
    monkeypatch.setattr(
        registry_mod.os,
        "kill",
        lambda _pid, _signum: pytest.fail("legacy idle retirement should not require a signal"),
    )

    assert registry._retire_incompatible_service() is (not active)

    assert [action for action, _protocol in actions] == (
        ["ping", "status"] if active else ["ping", "status", "shutdown"]
    )
    assert {protocol for action, protocol in actions if action in {"status", "shutdown"}} == {
        service_protocol,
    }
    assert registry.record_path.exists() is active
    assert registry.socket_path.exists() is active


def test_registry_bounds_batchd_drain_before_signalling_stuck_replacement(tmp_path, monkeypatch):
    service_pid = 4242
    service_protocol = 25
    source_epoch = "retained-batchd"
    spawn_generation = "1" * 32
    clock = [100.0]
    state = {"alive": True}
    signals = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "batchd",
            "yolomux_lib.batchd",
            "batchd.sock",
            service_protocol,
            code_revision="current-revision",
            build_revision=1,
        ),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    registry._write_record({
        **FixtureProcessRecordBuilder(pid=service_pid).build(),
        "service": "batchd",
        "socket": str(registry.socket_path),
        "protocol_version": service_protocol,
        "source_epoch": source_epoch,
        "namespace": str(registry.service_dir),
        "spawn_generation": spawn_generation,
    })
    registry.socket_path.touch()

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        del timeout, protocol_version
        if method == "ping":
            return {
                "ok": True,
                "version": service_protocol,
                "build": 1,
                "code_revision": "stale-revision",
                "pid": service_pid,
                "source_epoch": source_epoch,
            }
        if method == "shutdown":
            assert payload == {
                "retirement_handshake": True,
                "expected_source_epoch": source_epoch,
            }
            return {
                "ok": True,
                "shutdown": True,
                "draining": True,
                "pid": service_pid,
                "version": service_protocol,
                "source_epoch": source_epoch,
            }
        if method == "status":
            return {
                "ok": True,
                "version": service_protocol,
                "pid": service_pid,
                "source_epoch": source_epoch,
                "active_records": [{"job_id": "stuck-job", "status": "running"}],
                "queues": {"freshness": 0},
            }
        return {}

    def kill(pid, signum):
        signals.append((pid, signum))
        state["alive"] = False

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: state["alive"])
    monkeypatch.setattr(registry_mod, "process_state", lambda _pid: "S" if state["alive"] else "")
    monkeypatch.setattr(registry_mod, "process_spawn_generation", lambda _pid: spawn_generation)
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if state["alive"] else None,
    )
    monkeypatch.setattr(
        registry_mod,
        "process_spawn_generation",
        lambda _pid: spawn_generation if state["alive"] else None,
    )
    monkeypatch.setattr(registry_mod.os, "kill", kill)

    assert registry._retire_incompatible_service() is True

    assert clock[0] >= 100.0 + registry_mod.LOCAL_SERVICE_BATCHD_DRAIN_GRACE_SECONDS
    assert clock[0] < 100.1 + registry_mod.LOCAL_SERVICE_BATCHD_DRAIN_GRACE_SECONDS
    assert signals == [(service_pid, signal.SIGTERM)]
    assert registry.record_path.exists() is False
    assert registry.socket_path.exists() is False


def test_registry_refuses_batchd_shutdown_receipt_from_another_source_epoch(tmp_path, monkeypatch):
    service_pid = 4242
    service_protocol = 25
    source_epoch = "retained-batchd"
    state = {"alive": True}
    signals = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "batchd",
            "yolomux_lib.batchd",
            "batchd.sock",
            service_protocol,
            code_revision="current-revision",
            build_revision=1,
        ),
    )
    registry._write_record({
        **FixtureProcessRecordBuilder(pid=service_pid).build(),
        "service": "batchd",
        "socket": str(registry.socket_path),
        "protocol_version": service_protocol,
        "source_epoch": source_epoch,
    })
    registry.socket_path.touch()

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        del timeout, protocol_version
        if method == "ping":
            return {
                "ok": True,
                "version": service_protocol,
                "build": 1,
                "code_revision": "stale-revision",
                "pid": service_pid,
                "source_epoch": source_epoch,
            }
        if method == "shutdown":
            assert payload == {
                "retirement_handshake": True,
                "expected_source_epoch": source_epoch,
            }
            return {
                "ok": True,
                "shutdown": True,
                "draining": False,
                "pid": service_pid,
                "version": service_protocol,
                "source_epoch": "replacement-batchd",
            }
        return {}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: state["alive"])
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if state["alive"] else None,
    )
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))

    assert registry._retire_incompatible_service() is False
    assert signals == []
    assert registry.record_path.exists() is True
    assert registry.socket_path.exists() is True


def test_registry_stops_batchd_drain_wait_when_same_pid_identity_is_reused(tmp_path, monkeypatch):
    service_pid = 4242
    service_protocol = 25
    source_epoch = "retained-batchd"
    retained_start = f"proc:{service_pid + 1000}"
    clock = [100.0]
    current_start = [retained_start]
    signals = []

    def advance(seconds):
        clock[0] += seconds
        current_start[0] = "proc:replacement"

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "batchd",
            "yolomux_lib.batchd",
            "batchd.sock",
            service_protocol,
            code_revision="current-revision",
            build_revision=1,
        ),
        clock=lambda: clock[0],
        sleep=advance,
    )
    registry._write_record({
        **FixtureProcessRecordBuilder(pid=service_pid).build(),
        "service": "batchd",
        "socket": str(registry.socket_path),
        "protocol_version": service_protocol,
        "source_epoch": source_epoch,
    })
    registry.socket_path.touch()

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        del timeout, protocol_version
        if method == "ping":
            return {
                "ok": True,
                "version": service_protocol,
                "build": 1,
                "code_revision": "stale-revision",
                "pid": service_pid,
                "source_epoch": source_epoch,
            }
        if method == "shutdown":
            assert payload == {
                "retirement_handshake": True,
                "expected_source_epoch": source_epoch,
            }
            return {
                "ok": True,
                "shutdown": True,
                "draining": True,
                "pid": service_pid,
                "version": service_protocol,
                "source_epoch": source_epoch,
            }
        return {}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(registry_mod, "process_start_identity", lambda _pid: current_start[0])
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))

    assert registry._retire_incompatible_service() is False
    assert clock[0] < 100.1
    assert signals == []
    assert registry.record_path.exists() is True
    assert registry.socket_path.exists() is True

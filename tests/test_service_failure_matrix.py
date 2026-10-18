import pytest
import os
import threading
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

from yomux_lib.infra.host_identity import current_host_identity
from yomux_lib.local_services.registry import LOCAL_SERVICE_BACKOFF_SECONDS
from yomux_lib.local_services.registry import LocalServiceRegistry
from yomux_lib.local_services.registry import LocalServiceSpec


SERVICE_FAILURE_MATRIX = [
    ("not_started", "registry", "missing socket reports unhealthy without fallback work"),
    ("connect_failure", "registry", "failed spawn records backoff and visible status"),
    ("crash_before_work", "registry", "exited child does not spin restarts"),
    ("crash_during_work", "batchd", "broken executor marks the job failed"),
    ("hang", "batchd", "timed-out running work keeps its slot until worker exit"),
    ("deadline", "batchd", "expired queued work fails before dispatch"),
    ("queue_full", "batchd", "saturated queue rejects new work"),
    ("stale_generation", "batchd", "newer generation supersedes stale queued work"),
    ("protocol_config_mismatch", "registry", "wrong protocol version stays unhealthy"),
    ("parent_death", "registry", "dead service record is removed before restart"),
    ("sleep_time_jump", "registry", "backoff uses monotonic deadlines"),
    ("approval_no_replay", "approvald", "shutdown/restart does not recreate target workers"),
    ("status_visibility", "all", "status/profile stays bounded and secret-free"),
]


class ExitedProcess:
    def poll(self):
        return 1


class FakeWorker:
    created = []

    def __init__(self, target, **kwargs):
        self.target = target
        self.kwargs = kwargs
        self.stopped = False
        FakeWorker.created.append(self)

    def start(self):
        return True, None

    def alive(self):
        return not self.stopped

    def stop(self):
        self.stopped = True
        return True

    def status(self):
        return {"target": self.target, "enabled": self.alive(), "approved": 0, "blocked": 0}

    def has_pending_prompt(self):
        return False


def test_service_failure_matrix_names_every_required_cell():
    required = {
        "not_started",
        "connect_failure",
        "crash_before_work",
        "crash_during_work",
        "hang",
        "deadline",
        "queue_full",
        "stale_generation",
        "protocol_config_mismatch",
        "parent_death",
        "sleep_time_jump",
        "approval_no_replay",
        "status_visibility",
    }

    assert {cell for cell, _service, _expectation in SERVICE_FAILURE_MATRIX} == required


def test_registry_failure_rows_are_visible_bounded_and_backed_off(tmp_path, monkeypatch):
    clock = [100.0]
    spawned = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("failingd", "missing.module", "failing.sock", 7),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)) or ExitedProcess(),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert registry.status()["healthy"] is False
    assert registry.ensure_started() is False
    first_status = registry.status()
    assert first_status["healthy"] is False
    assert first_status["failures"] == 1
    assert first_status["next_start_at"] == 100.0 + LOCAL_SERVICE_BACKOFF_SECONDS
    assert len(spawned) == 1
    assert registry.ensure_started() is False
    assert len(spawned) == 1
    clock[0] = first_status["next_start_at"] + 0.001
    assert registry.ensure_started() is False
    assert len(spawned) == 2

    record = registry.record_path
    registry._write_record({
        **current_host_identity().process_record_fields(pid=999999999, start_identity="proc:1"),
        "service": "failingd",
    })
    registry._remove_stale_record()
    assert record.exists() is False

    class VersionMismatchRegistry(LocalServiceRegistry):
        def _request(self, method, payload=None, timeout=0.2):
            if method in {"ping", "status"}:
                return {"ok": True, "pid": os.getpid(), "version": 999, "socket": str(self.socket_path)}
            return {}

    mismatch = VersionMismatchRegistry(tmp_path, LocalServiceSpec("mismatchd", "missing.module", "mismatch.sock", 7))
    assert mismatch.healthy() is False
    assert mismatch.status()["healthy"] is False


def test_registry_refuses_incompatible_socket_owner_without_sidecar_record(tmp_path, monkeypatch):
    clock = [200.0]
    stale_alive = {424_242: True}
    shutdowns = []
    spawned = []

    monkeypatch.setattr("yomux_lib.local_services.registry.pid_is_alive", lambda pid: stale_alive.get(pid, False))

    class RunningProcess:
        def poll(self):
            return None

    class MissingRecordMismatchRegistry(LocalServiceRegistry):
        def _request(self, method, payload=None, timeout=0.2):
            if stale_alive[424_242]:
                if method in {"ping", "status"}:
                    return {"ok": True, "pid": 424_242, "version": 6, "socket": str(self.socket_path)}
                if method == "shutdown":
                    shutdowns.append(method)
                    stale_alive[424_242] = False
                    return {"ok": True}
                return {}
            if spawned and method in {"ping", "status"}:
                return {"ok": True, "pid": 777, "version": 7, "socket": str(self.socket_path)}
            return {}

    registry = MissingRecordMismatchRegistry(
        tmp_path,
        LocalServiceSpec("mismatchd", "missing.module", "mismatch.sock", 7),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)) or RunningProcess(),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert registry.record_path.exists() is False
    assert registry.ensure_started() is False
    assert shutdowns == []
    assert spawned == []
    assert registry.status()["process_diagnostic"]["reason"] == "missing_host_identity"
    assert registry.failure_response()["reason_code"] == "missing_host_identity"
    lease = registry.acquire_lease()
    assert lease["error_code"] == "missing_host_identity"
    assert lease["process_diagnostic"]["reason"] == "missing_host_identity"
    assert shutdowns == []
    assert spawned == []

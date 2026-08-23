# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Failure ownership regressions for the jobd control plane."""

import os
import threading

from yolomux_lib.infra import jobd
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec


def test_state_lock_contention_returns_typed_busy_without_entering_the_handler(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    acquire_calls = []

    class ContendedLock:
        def acquire(self, *, blocking=True):
            acquire_calls.append(blocking)
            return False

        def release(self):
            raise AssertionError("an unacquired state lock must not be released")

    broker.state_lock = ContendedLock()

    response, body = broker.handle({
        "action": "result",
        "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
        "job_id": "missing",
    })

    assert acquire_calls == [False]
    assert response == {"ok": False, "error": "service busy", "state_lock_rejected": True}
    assert body == b""


def test_state_lock_contention_records_a_pending_shutdown_request(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)

    class ContendedLock:
        def acquire(self, *, blocking=True):
            return False

        def release(self):
            raise AssertionError("an unacquired state lock must not be released")

    broker.state_lock = ContendedLock()

    response, body = broker.handle({
        "action": "shutdown",
        "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
    })

    assert response == {"ok": True, "shutdown": True}
    assert body == b""
    assert broker.shutdown_requested.is_set() is True
    assert broker.stop_event.is_set() is False


def test_contended_shutdown_reports_draining_when_cached_status_predates_accepted_work(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    submitted, submitted_body = broker.handle({
        "action": "submit",
        "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
        "task": "json_compact",
        "payload": {"value": 1},
        "coalesce_key": "stale-status-drain",
    })
    assert submitted["ok"] is True
    assert submitted_body == b""

    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_state_lock() -> None:
        with broker.state_lock:
            lock_held.set()
            assert release_lock.wait(timeout=2.0)

    holder = threading.Thread(target=hold_state_lock, name="jobd-test-stale-status-holder")
    holder.start()
    assert lock_held.wait(timeout=1.0) is True
    try:
        status, status_body = broker.handle({
            "action": "status",
            "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
        })
        assert status["busy"] is True
        assert status["active_records"] == []
        assert status["queues"] == {priority: 0 for priority in jobd.JOBD_PRIORITIES}
        assert status_body == b""

        shutdown, shutdown_body = broker.handle({
            "action": "shutdown",
            "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
            "retirement_handshake": True,
        })
        assert shutdown == {
            "ok": True,
            "version": jobd.JOBD_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "started_at": broker.started_at,
            "source_epoch": broker.source_epoch,
            "shutdown": True,
            "draining": True,
        }
        assert shutdown_body == b""
        assert broker.shutdown_requested.is_set() is True
        assert broker.stop_event.is_set() is False
    finally:
        release_lock.set()
        holder.join(timeout=2.0)

    assert holder.is_alive() is False


def test_shutdown_that_wins_admission_refuses_the_overlapping_submit(tmp_path, monkeypatch):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    validation_entered = threading.Event()
    release_validation = threading.Event()
    submit_result = {}
    original_validate = broker._validated_submission

    def blocked_validate(request):
        validation_entered.set()
        assert release_validation.wait(timeout=2.0)
        return original_validate(request)

    monkeypatch.setattr(broker, "_validated_submission", blocked_validate)

    def submit() -> None:
        response, body = broker.handle({
            "action": "submit",
            "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
            "task": "json_compact",
            "payload": {"value": 1},
            "coalesce_key": "shutdown-wins",
        })
        submit_result.update(response=response, body=body)

    submitter = threading.Thread(target=submit, name="jobd-test-overlapping-submit")
    submitter.start()
    assert validation_entered.wait(timeout=1.0) is True
    shutdown, shutdown_body = broker.handle({
        "action": "shutdown",
        "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
    })
    assert shutdown == {"ok": True, "shutdown": True}
    assert shutdown_body == b""
    assert broker.shutdown_requested.is_set() is True
    assert broker.stop_event.is_set() is False

    release_validation.set()
    submitter.join(timeout=2.0)

    assert submitter.is_alive() is False
    assert submit_result == {
        "response": {
            "ok": False,
            "error": "service busy",
            "admission_rejected": True,
        },
        "body": b"",
    }
    assert broker.records == {}
    assert broker._idle_should_stop() is True
    assert broker.stop_event.is_set() is True


def test_submit_that_wins_admission_drains_before_overlapping_shutdown_stops(tmp_path, monkeypatch):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    acceptance_completed = threading.Event()
    release_submit = threading.Event()
    submit_result = {}
    original_submit = broker._submit

    def accepted_then_blocked(request):
        response = original_submit(request)
        acceptance_completed.set()
        assert release_submit.wait(timeout=2.0)
        return response

    monkeypatch.setattr(broker, "_submit", accepted_then_blocked)

    def submit() -> None:
        response, body = broker.handle({
            "action": "submit",
            "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
            "task": "json_compact",
            "payload": {"value": 1},
            "coalesce_key": "submit-wins",
        })
        submit_result.update(response=response, body=body)

    submitter = threading.Thread(target=submit, name="jobd-test-accepted-submit")
    submitter.start()
    assert acceptance_completed.wait(timeout=1.0) is True
    shutdown, shutdown_body = broker.handle({
        "action": "shutdown",
        "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
    })
    assert shutdown == {"ok": True, "shutdown": True}
    assert shutdown_body == b""
    assert broker.shutdown_requested.is_set() is True
    assert broker.stop_event.is_set() is False

    release_submit.set()
    submitter.join(timeout=2.0)

    assert submitter.is_alive() is False
    assert submit_result["response"]["ok"] is True
    record = next(iter(broker.records.values()))
    assert record.status == "queued"
    assert broker._idle_should_stop() is False
    with broker.state_lock:
        broker._mark_terminal(record, "completed")
        assert broker._finish_requested_shutdown_if_drained() is True
    assert broker.stop_event.is_set() is True


def test_first_contended_status_is_complete_and_contention_is_counted(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    lock_held = threading.Event()
    release_lock = threading.Event()
    holder_failures = []

    def hold_state_lock() -> None:
        with broker.state_lock:
            lock_held.set()
            if not release_lock.wait(timeout=2.0):
                holder_failures.append("state lock release timed out")

    holder = threading.Thread(target=hold_state_lock, name="jobd-test-first-status-holder")
    holder.start()
    assert lock_held.wait(timeout=1.0) is True
    try:
        status, status_body = broker.handle({"action": "status", "protocol_version": jobd.JOBD_PROTOCOL_VERSION})
        ping, ping_body = broker.handle({"action": "ping", "protocol_version": jobd.JOBD_PROTOCOL_VERSION})
        busy, busy_body = broker.handle({
            "action": "result",
            "protocol_version": jobd.JOBD_PROTOCOL_VERSION,
            "job_id": "missing",
        })

        assert status["ok"] is True
        assert status["busy"] is True
        assert status["worker_count"] == sum(
            broker._lane_capacity(lane) for lane in jobd.JOBD_LANE_PRIORITIES
        )
        assert status["lanes"] == {
            "point": {"capacity": jobd.JOBD_POINT_WORKERS, "active": 0, "queued": 0},
            "mutation": {"capacity": jobd.JOBD_MUTATION_WORKERS, "active": 0, "queued": 0},
            "interactive": {"capacity": jobd.JOBD_INTERACTIVE_WORKERS, "active": 0, "queued": 0},
            "bulk": {"capacity": 1, "active": 0, "queued": 0},
        }
        assert status["queues"] == {priority: 0 for priority in jobd.JOBD_PRIORITIES}
        assert status["scheduler_pump"] == {"failures": 0, "last_failure": {}}
        assert status["request_counters"] == {"status": 1}
        assert status["contention_counters"] == {"status": 1}
        assert status_body == b""
        assert ping["ok"] is True and ping_body == b""
        assert busy == {"ok": False, "error": "service busy", "state_lock_rejected": True} and busy_body == b""
    finally:
        release_lock.set()
        holder.join(timeout=2.0)

    assert holder.is_alive() is False
    assert holder_failures == []
    current = broker.common_status()
    assert current["request_counters"] == {"status": 1, "ping": 1, "result": 1}
    assert current["contention_counters"] == {"status": 1, "ping": 1, "result": 1}


def test_real_state_lock_contention_keeps_registry_control_plane_healthy(tmp_path, monkeypatch):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    baseline_status, baseline_body = broker.handle({"action": "status", "protocol_version": jobd.JOBD_PROTOCOL_VERSION})
    assert baseline_status["ok"] is True
    assert baseline_body == b""
    lock_held = threading.Event()
    release_lock = threading.Event()
    holder_failures = []

    def hold_state_lock():
        with broker.state_lock:
            lock_held.set()
            if not release_lock.wait(timeout=2.0):
                holder_failures.append("state lock release timed out")

    holder = threading.Thread(target=hold_state_lock, name="jobd-test-state-lock-holder")
    holder.start()
    assert lock_held.wait(timeout=1.0) is True
    try:
        ping, ping_body = broker.handle({"action": "ping", "protocol_version": jobd.JOBD_PROTOCOL_VERSION})
        status, status_body = broker.handle({"action": "status", "protocol_version": jobd.JOBD_PROTOCOL_VERSION})
        busy, busy_body = broker.handle({"action": "result", "protocol_version": jobd.JOBD_PROTOCOL_VERSION, "job_id": "missing"})

        assert ping["ok"] is True
        assert ping["version"] == jobd.JOBD_PROTOCOL_VERSION
        assert ping["pid"] > 1
        assert ping_body == b""
        assert status["ok"] is True
        assert status["version"] == jobd.JOBD_PROTOCOL_VERSION
        assert status["pid"] == ping["pid"]
        assert status["started_at"] == ping["started_at"]
        assert status["source_epoch"] == ping["source_epoch"]
        assert status["busy"] is True
        assert status["request_counters"] == {
            **baseline_status["request_counters"],
            "ping": 1,
            "status": baseline_status["request_counters"]["status"] + 1,
        }
        assert status["contention_counters"] == {"ping": 1, "status": 1}
        assert status["worker_count"] == baseline_status["worker_count"]
        assert status_body == b""
        assert busy == {"ok": False, "error": "service busy", "state_lock_rejected": True}
        assert busy_body == b""

        registry = LocalServiceRegistry(
            tmp_path,
            LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
            socket_path=broker.socket_path,
            service_dir=tmp_path,
        )

        def broker_request(method, payload=None, timeout=0.2, protocol_version=None):
            del timeout
            selected_version = jobd.JOBD_PROTOCOL_VERSION if protocol_version is None else protocol_version
            response, _body = broker.handle({"action": method, "protocol_version": selected_version, **(payload or {})})
            return response

        monkeypatch.setattr(registry, "_request", broker_request)
        assert registry.healthy() is True
        assert registry.status()["healthy"] is True
        shutdown, shutdown_body = broker.handle({"action": "shutdown", "protocol_version": jobd.JOBD_PROTOCOL_VERSION})
        assert shutdown == {"ok": True, "shutdown": True}
        assert shutdown_body == b""
        assert broker.shutdown_requested.is_set() is True
        assert broker.stop_event.is_set() is False
    finally:
        release_lock.set()
        holder.join(timeout=2.0)
    assert holder.is_alive() is False
    assert holder_failures == []
    assert broker._idle_should_stop() is True
    assert broker.stop_event.is_set() is True

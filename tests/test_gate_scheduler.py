from __future__ import annotations

import threading
import time

from yolomux_lib import batchd
from yolomux_lib.local_services import rpc


def _batchd_request(socket_path, action, **payload):
    envelope = rpc.new_envelope("batchd", action, {"action": action, **payload}, timeout_seconds=1.0)
    return rpc.request(socket_path, envelope, timeout_seconds=1.0)[0]


def test_p6_failed_idle_pump_is_visible_and_the_broker_keeps_draining_work(tmp_path, monkeypatch):
    """One failed idle pump must not kill batchd or hide its reason from status."""

    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=60, workers=1)
    original_pump = broker._pump
    original_record_failure = broker._record_scheduler_pump_failure
    pump_calls = 0
    injected_failure = threading.Event()
    failure_recorded = threading.Event()

    def fail_once_then_pump():
        nonlocal pump_calls
        pump_calls += 1
        if pump_calls == 1:
            injected_failure.set()
            raise RuntimeError("injected scheduler pump failure")
        return original_pump()

    def record_failure(exc, traceback_text):
        original_record_failure(exc, traceback_text)
        failure_recorded.set()

    monkeypatch.setattr(broker, "_pump", fail_once_then_pump)
    monkeypatch.setattr(broker, "_record_scheduler_pump_failure", record_failure)
    worker = threading.Thread(target=broker.run, name="p6-batchd", daemon=True)
    worker.start()
    try:
        assert injected_failure.wait(2.0), "idle pump never ran"
        assert failure_recorded.wait(2.0), "idle pump failure was not recorded"
        status = _batchd_request(broker.socket_path, "status")
        assert status["ok"] is True
        pump_status = status["scheduler_pump"]
        assert pump_status["failures"] == 1
        assert pump_status["last_failure"]["exception_type"] == "RuntimeError"
        assert pump_status["last_failure"]["reason"] == "injected scheduler pump failure"
        assert "Traceback" in pump_status["last_failure"]["traceback"]

        accepted = _batchd_request(
            broker.socket_path,
            "submit",
            task="text_facts",
            payload={"text": "one\ntwo\n"},
            priority="interactive",
            coalesce_key="p6-progress",
        )
        assert accepted["ok"] is True
        job_id = accepted["job"]["job_id"]
        deadline = time.monotonic() + 5.0
        record = None
        while time.monotonic() < deadline:
            result = _batchd_request(broker.socket_path, "result", job_id=job_id)
            record = result.get("job")
            if record and record["status"] == "completed":
                break
            time.sleep(0.02)
        assert record and record["status"] == "completed", record
        assert record["result"] == {"bytes": 8, "lines": 2, "nonempty_lines": 2}

        final_status = _batchd_request(broker.socket_path, "status")
        assert final_status["scheduler_pump"]["failures"] == 1
        assert sum(final_status["queues"].values()) == 0
    finally:
        broker.stop_event.set()
        worker.join(timeout=2.0)
        broker._on_shutdown()
    assert worker.is_alive() is False

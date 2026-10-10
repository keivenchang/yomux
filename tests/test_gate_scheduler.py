from __future__ import annotations

import pytest

import threading
import time

from yomux_lib import batchd
from yomux_lib.local_services import rpc

# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
pytestmark = pytest.mark.deprecated_python_server


def _batchd_request(socket_path, action, **payload):
    envelope = rpc.new_envelope("batchd", action, {"action": action, **payload}, timeout_seconds=1.0)
    return rpc.request(socket_path, envelope, timeout_seconds=1.0)[0]


def test_p6_failed_idle_pump_is_visible_and_the_broker_keeps_draining_work(tmp_path, monkeypatch):
    """One failed idle pump must not kill batchd or hide its reason from status."""

    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=60, workers=1)
    worker = threading.Thread(target=broker.run, name="p6-batchd", daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 2.0
        while not broker.socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert broker.socket_path.exists(), "batchd listener did not start"

        def submit_text_facts(key):
            accepted = _batchd_request(
                broker.socket_path,
                "submit",
                task="text_facts",
                payload={"text": "one\ntwo\n"},
                priority="interactive",
                coalesce_key=key,
            )
            assert accepted["ok"] is True
            return accepted["job"]["job_id"]

        def wait_for_completion(job_id, timeout):
            deadline = time.monotonic() + timeout
            record = None
            while time.monotonic() < deadline:
                result = _batchd_request(broker.socket_path, "result", job_id=job_id)
                record = result.get("job")
                if record and record["status"] == "completed":
                    return record
                time.sleep(0.02)
            return record

        # Warm the broker-owned process-pool slot before injecting the idle-pump failure. The
        # regression is about scheduler recovery; cold spawn latency is independently unbounded.
        warmup = submit_text_facts("p6-warmup")
        warm_record = wait_for_completion(warmup, 30.0)
        assert warm_record and warm_record["status"] == "completed", warm_record

        original_pump = broker._pump
        original_record_failure = broker._record_scheduler_pump_failure
        injected_failure = threading.Event()
        failure_recorded = threading.Event()

        def fail_once_then_pump():
            if not injected_failure.is_set():
                injected_failure.set()
                raise RuntimeError("injected scheduler pump failure")
            return original_pump()

        def record_failure(exc, traceback_text):
            original_record_failure(exc, traceback_text)
            failure_recorded.set()

        monkeypatch.setattr(broker, "_pump", fail_once_then_pump)
        monkeypatch.setattr(broker, "_record_scheduler_pump_failure", record_failure)
        assert injected_failure.wait(2.0), "idle pump never ran"
        assert failure_recorded.wait(2.0), "idle pump failure was not recorded"
        status = _batchd_request(broker.socket_path, "status")
        assert status["ok"] is True
        pump_status = status["scheduler_pump"]
        assert pump_status["failures"] == 1
        assert pump_status["last_failure"]["exception_type"] == "RuntimeError"
        assert pump_status["last_failure"]["reason"] == "injected scheduler pump failure"
        assert "Traceback" in pump_status["last_failure"]["traceback"]

        job_id = submit_text_facts("p6-progress")
        record = wait_for_completion(job_id, 5.0)
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

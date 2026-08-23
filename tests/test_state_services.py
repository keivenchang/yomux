# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Lifecycle regressions for app state owners."""

import threading

from yolomux_lib import app as app_module
from yolomux_lib.infra.state_services import JobdOperationService, SessionFilesService


def test_session_files_service_owns_worker_until_target_returns_and_stop_joins_it():
    class App:
        def __init__(self):
            self._session_files_coordinator = app_module.SessionFilesCoordinator(self)

        @staticmethod
        def background_can_run(_role):
            return True

        @staticmethod
        def session_files_disk_cache_path(_key):
            return None, "owned-worker-signature"

    app = App()
    coordinator = app._session_files_coordinator
    service = coordinator.state
    key = ("payload", "owned-worker")
    worker_started = threading.Event()
    release_worker = threading.Event()
    stop_returned = threading.Event()
    late_work = threading.Event()

    def target(cache_key):
        worker_started.set()
        assert release_worker.wait(timeout=2)
        if stop_returned.is_set():
            late_work.set()
        record = service.work_records[cache_key]
        service.finish_work(key, record)

    assert coordinator.start_session_files_cache_refresh(app, key, target) is True
    assert worker_started.wait(timeout=1)
    assert service.wait_for_idle(timeout=0.01) is False

    def stop_service():
        coordinator.stop()
        stop_returned.set()

    stopper = threading.Thread(target=stop_service, name="session-files-stop-regression")
    stopper.start()
    assert service.accepting_work is False
    returned_before_worker_finished = stop_returned.wait(timeout=0.1)
    release_worker.set()
    stopper.join(timeout=2)

    assert not stopper.is_alive()
    assert returned_before_worker_finished is False
    assert late_work.is_set() is False
    assert service.wait_for_idle(timeout=0) is True
    assert service.work_records == {}
    assert service.reserve_work(("payload", "late"), "late-signature") is None
    coordinator.stop()


def test_jobd_operation_service_wait_for_idle_keeps_completion_service_running():
    service = JobdOperationService(worker_limit=1, operation_limit=1)
    operation_started = threading.Event()
    release_operation = threading.Event()

    def accepted_operation():
        operation_started.set()
        assert release_operation.wait(timeout=2)

    reservation = service.reserve("bulk")
    assert reservation is not None
    assert service.submit_reserved(reservation, accepted_operation) is True
    assert operation_started.wait(timeout=1)
    assert service.wait_for_idle(timeout=0.01) is False
    assert service.stop_event.is_set() is False
    release_operation.set()
    assert service.wait_for_idle(timeout=1) is True
    assert service.stop_event.is_set() is False
    assert service.futures == set()
    service.stop()


def test_jobd_operation_service_stop_joins_running_accepted_operation_before_returning():
    service = JobdOperationService(worker_limit=1, operation_limit=1)
    operation_started = threading.Event()
    release_operation = threading.Event()
    stop_returned = threading.Event()
    late_rpc = threading.Event()

    def accepted_operation():
        operation_started.set()
        assert release_operation.wait(timeout=2)
        if stop_returned.is_set():
            late_rpc.set()

    reservation = service.reserve("bulk")
    assert reservation is not None
    assert service.submit_reserved(reservation, accepted_operation) is True
    assert operation_started.wait(timeout=1)

    def stop_service():
        service.stop()
        stop_returned.set()

    stopper = threading.Thread(target=stop_service, name="jobd-operation-stop-regression")
    stopper.start()
    assert service.stop_event.wait(timeout=1)
    returned_before_operation_finished = stop_returned.wait(timeout=0.1)
    release_operation.set()
    stopper.join(timeout=2)

    assert not stopper.is_alive()
    assert returned_before_operation_finished is False
    assert late_rpc.is_set() is False
    assert service.futures == set()


def test_jobd_product_wait_stops_before_another_rpc_when_its_owner_is_cancelled():
    stop_event = threading.Event()
    product_calls = []

    class Client:
        def product(self, key, timeout=0.5):
            product_calls.append(key)
            stop_event.set()
            return {"ok": True, "state": "pending", "generation": 1}, b""

    result = app_module.wait_for_jobd_product(
        Client(),
        "metadata-product",
        2,
        20.0,
        stop_event=stop_event,
    )

    assert result == (None, None, "stopped")
    assert product_calls == ["metadata-product"]

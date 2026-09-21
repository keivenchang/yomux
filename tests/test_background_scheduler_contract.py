# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression contracts for process-local background scheduling."""

import threading
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.infra.background_scheduler import BACKGROUND_ROLE_SESSION_FILES, BackgroundScheduler


def test_app_constructs_exactly_one_local_scheduler(monkeypatch):
    created: list[BackgroundScheduler] = []

    class RecordingScheduler(BackgroundScheduler):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setattr(app_module, "BackgroundScheduler", RecordingScheduler)
    app = app_module.TmuxWebtermApp([], status_service_mode=True)

    assert len(created) == 1
    assert app.background_scheduler is created[0]
    assert app.background_scheduler.lifecycle == "not_started"


def test_scheduler_start_rolls_back_when_thread_creation_fails(monkeypatch, tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))

    def fail_start(_thread):
        raise RuntimeError("thread admission refused")

    monkeypatch.setattr(threading.Thread, "start", fail_start)

    with pytest.raises(RuntimeError, match="thread admission refused"):
        scheduler.start()

    assert scheduler.lifecycle == "not_started"
    assert scheduler._work_thread is None
    assert scheduler.stop_event.is_set() is False


def test_app_scheduler_start_rolls_back_when_service_startup_raises(monkeypatch):
    app = app_module.TmuxWebtermApp([], status_service_mode=True)

    def fail_startup():
        raise RuntimeError("service startup failed")

    monkeypatch.setattr(app, "handle_background_scheduler_started", fail_startup)

    try:
        with pytest.raises(RuntimeError, match="service startup failed"):
            app.start_background_scheduler()
        assert app.background_scheduler.lifecycle == "stopped"
        assert app.background_scheduler._work_thread is None
    finally:
        app.stop_auto_approve_all()

def test_non_deferred_request_reports_atomic_acceptance_race(monkeypatch, tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True

    # Simulate two callers both observing the same empty queue before either reaches
    # the atomic acceptance step. The acceptance result, not the observation, owns
    # the coalescing decision.
    monkeypatch.setattr(scheduler, "refresh_is_pending", lambda _role, _payload: False)
    first = scheduler.request_refresh("stats-sampler", {"family": "cpu"})
    second = scheduler.request_refresh("stats-sampler", {"family": "cpu"})

    assert first.get("coalesced", False) is False
    assert second["coalesced"] is True
    assert second["already_pending"] is True
    assert scheduler.refresh_queue_payload()["recent_pending_count"] == 1


def test_request_refresh_rechecks_lifecycle_after_pending_check(monkeypatch, tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True

    def stop_before_acceptance(_role, _payload):
        scheduler.stop()
        return False

    monkeypatch.setattr(scheduler, "refresh_is_pending", stop_before_acceptance)
    result = scheduler.request_refresh("stats-sampler", {"family": "cpu"})

    assert result["accepted"] is False
    assert result["fallback"] is True
    assert scheduler.refresh_queue_payload()["recent_pending_count"] == 0


def test_deferred_refresh_is_not_pending_until_downstream_accepts(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True

    admission = scheduler.request_refresh("search-index", {"root": "/repo"}, defer_acceptance=True)
    retry = scheduler.request_refresh("search-index", {"root": "/repo"}, defer_acceptance=True)

    assert admission["deferred_acceptance"] is True
    assert admission["accepted"] is False
    assert retry.get("coalesced") is not True
    assert scheduler.refresh_queue_payload()["recent_pending_count"] == 0

    accepted = scheduler.accept_refresh("search-index", {"root": "/repo"})
    assert accepted == {"accepted": True, "coalesced": False}
    assert scheduler.refresh_queue_payload()["recent_pending_count"] == 1


def test_direct_session_files_refresh_respects_scheduler_shutdown_fence(tmp_path):
    calls = []

    class FakeApp:
        def __init__(self):
            self.background_scheduler = SimpleNamespace(lock=threading.RLock())

        def scheduler_can_run(self, role):
            assert role == BACKGROUND_ROLE_SESSION_FILES
            return False

        def session_files_disk_cache_path(self, _cache_key):
            return tmp_path / "session-files.json", "stable"

    app = FakeApp()
    coordinator = app_module.SessionFilesCoordinator(app)
    coordinator.start()
    try:
        accepted = coordinator.start_session_files_cache_refresh(
            app,
            ("cache",),
            lambda *_args: calls.append("started"),
        )
        assert accepted is False
        assert calls == []
        assert coordinator.state.work_records == {}
    finally:
        coordinator.stop()


def test_external_worker_admission_is_fenced_by_scheduler_shutdown(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True
    entered = threading.Event()
    release = threading.Event()

    def worker() -> None:
        with scheduler.external_work_admission("watch-roots") as admitted:
            assert admitted is True
            entered.set()
            assert release.wait(2.0)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(1.0)

    stop_thread = threading.Thread(target=scheduler.stop)
    stop_thread.start()
    assert stop_thread.is_alive()
    with scheduler.external_work_admission("watch-roots") as admitted:
        assert admitted is False

    release.set()
    thread.join(timeout=2.0)
    stop_thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert not stop_thread.is_alive()


def test_scheduler_keeps_admitted_external_work_running_before_final_stop(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True
    entered = threading.Event()
    release = threading.Event()
    observed: list[dict[str, bool]] = []

    def worker() -> None:
        with scheduler.external_work_admission("search-index") as admitted:
            assert admitted is True
            entered.set()
            assert release.wait(2.0)
            observed.append(scheduler.accept_refresh("search-index", {"root": "/repo"}, allow_stopping=True))

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(1.0)

    stop_thread = threading.Thread(target=scheduler.stop)
    stop_thread.start()
    assert stop_thread.is_alive()
    release.set()
    thread.join(timeout=2.0)
    stop_thread.join(timeout=2.0)

    assert observed == [{"accepted": True, "coalesced": False}]
    assert scheduler.lifecycle == "stopped"

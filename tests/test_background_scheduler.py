# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for process-local background work coordination."""

from __future__ import annotations

import threading

from yolomux_lib.infra.background_scheduler import BackgroundScheduler


def test_coordinator_reports_all_roles_as_local(tmp_path):
    coordinator = BackgroundScheduler(project_root=str(tmp_path))
    assert coordinator.start() is True

    payload = coordinator.status_payload()

    assert payload["status"] == "local"
    assert payload["process"]["scope"] == "local"
    assert payload["search_index"]["mode"] == "indexing-server"
    assert all(role["status"] == "local" for role in payload["roles"].values())


def test_coordinator_coalesces_identical_refreshes(tmp_path):
    coordinator = BackgroundScheduler(project_root=str(tmp_path))
    assert coordinator.start() is True

    accepted = coordinator.request_refresh("stats-sampler", {"family": "cpu"})
    coalesced = coordinator.request_refresh("stats-sampler", {"family": "cpu"})

    assert accepted["accepted"] is True
    assert accepted["local"] is True
    assert accepted["fallback"] is False
    assert coalesced["coalesced"] is True
    assert coalesced["local"] is True
    assert coalesced["fallback"] is False
    assert coordinator.refresh_queue_payload()["recent_pending_count"] == 1


def test_scheduler_status_has_no_distributed_server_state():
    status = BackgroundScheduler().status_payload()
    assert "current_owner" not in status
    assert all("stale_cache_reads" not in role for role in status["roles"].values())
    assert "process" in status


def test_stopping_the_scheduler_fences_future_work(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    scheduler.start()
    scheduler.stop()

    assert scheduler.can_run("search-index") is False
    result = scheduler.request_refresh("search-index", {"root": str(tmp_path)})
    assert result["accepted"] is False
    assert result["fallback"] is True
    assert scheduler.status_payload()["status"] == "stopped"


def test_stopping_during_deferred_work_fences_late_acceptance(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True

    admission = scheduler.request_refresh("search-index", {"root": str(tmp_path)}, defer_acceptance=True)
    scheduler.stop()
    accepted = scheduler.accept_refresh("search-index", {"root": str(tmp_path)})

    assert admission["deferred_acceptance"] is True
    assert accepted["accepted"] is False
    assert accepted["fallback"] is True
    assert scheduler.refresh_queue_payload()["recent_pending_count"] == 0


def test_stop_waits_for_running_work_and_fences_queued_work(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))
    assert scheduler.start() is True
    entered = threading.Event()
    release = threading.Event()
    finished: list[str] = []

    assert scheduler.submit_work("running", lambda: (entered.set(), release.wait(2.0), finished.append("running")))["queued"]
    assert entered.wait(timeout=1.0)
    assert scheduler.submit_work("queued", lambda: finished.append("queued"))["queued"]

    stop_thread = threading.Thread(target=scheduler.stop)
    stop_thread.start()
    assert stop_thread.is_alive()
    release.set()
    stop_thread.join(timeout=3.0)

    assert not stop_thread.is_alive()
    assert finished == ["running"]
    assert scheduler.status_payload()["work_queue"]["pending"] == 0


def test_scheduler_does_not_advertise_work_before_start(tmp_path):
    scheduler = BackgroundScheduler(project_root=str(tmp_path))

    assert scheduler.can_run("search-index") is False
    assert scheduler.status_payload()["status"] == "not_started"
    result = scheduler.request_refresh("search-index", {"root": str(tmp_path)})
    assert result["accepted"] is False
    assert result["fallback"] is True

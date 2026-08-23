# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import threading
import time

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.infra.state_services import ClientEventWatcherRecord
from yolomux_lib.infra.state_services import ClientWatchDescriptor
from yolomux_lib.local_services import client as local_service_client


class _WatchdDemandRetirementClient:
    def __init__(self, stop_event=None, on_first_release=None):
        self.stop_event = stop_event
        self.on_first_release = on_first_release
        self.acquire_count = 0
        self.wait_count = 0
        self.first_wait_entered = threading.Event()
        self.replacement_wait_entered = threading.Event()
        self.wait_release = threading.Event()
        self.lease_released = threading.Event()
        self.released_leases = []

    def acquire_lease(self):
        self.acquire_count += 1
        return {
            "ok": True,
            "lease_id": f"lease-{self.acquire_count}",
            "pid": 100 + self.acquire_count,
            "epoch": f"epoch-{self.acquire_count}",
            "watch_generation": self.acquire_count,
            "active_watch_generation": self.acquire_count,
        }

    def upsert(self, _lease_id, _descriptor_id, _descriptor, *, reconfiguring=False):
        del reconfiguring
        return {"ok": True, "watch_generation": self.acquire_count, "active_watch_generation": self.acquire_count}

    def remove(self, _lease_id, _descriptor_id, *, reconfiguring=False):
        del reconfiguring
        return {"ok": True, "watch_generation": self.acquire_count, "active_watch_generation": self.acquire_count}

    def wait_revision(self, _epoch, _revision, *, timeout, reconfiguring=False):
        del timeout, reconfiguring
        self.wait_count += 1
        if self.acquire_count == 1:
            self.first_wait_entered.set()
        else:
            self.replacement_wait_entered.set()
        if self.stop_event is not None:
            self.stop_event.wait(1.0)
        else:
            self.wait_release.wait(1.0)
            self.wait_release.clear()
        return {"ok": True, "changed": False, "revision": {}}

    def release_lease(self, lease_id):
        self.released_leases.append(lease_id)
        if len(self.released_leases) == 1 and self.on_first_release is not None:
            self.on_first_release()
        self.lease_released.set()
        return {"ok": True}


def test_watchd_retires_after_final_descriptor_while_other_sse_demand_remains(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    status_subscriber, _ = webapp.client_events.subscribe(channels="status", client_id="status-browser")
    files_subscriber, _ = webapp.client_events.subscribe(channels="files", client_id="files-browser")
    monkeypatch.setattr(webapp, "start_client_event_watcher", lambda: None)
    monkeypatch.setattr(webapp, "start_client_watch_snapshot_publish", lambda: True)
    webapp.update_client_watch_roots({"client_id": "files-browser", "roots": ["/repo"]})
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    client = _WatchdDemandRetirementClient()
    webapp.watch_client = client
    try:
        assert webapp.start_watchd_revision_watcher(record) is True
        assert client.first_wait_entered.wait(1.0)

        webapp.client_events.unsubscribe(files_subscriber)
        webapp.client_event_subscriber_disconnected("files-browser")
        assert webapp.client_events.has_client_id("status-browser") is True
        client.wait_release.set()

        assert client.lease_released.wait(1.0), "watchd retained its lease after final filesystem demand disappeared"
        assert client.wait_count == 1
        assert record.watchd_worker is None
    finally:
        record.stop_event.set()
        record.watchd_stop_event.set()
        client.wait_release.set()
        worker = record.watchd_worker
        if worker is not None:
            worker.join(timeout=1.0)
        webapp.client_events.unsubscribe(status_subscriber)


def test_watchd_release_timeout_keeps_retry_ownership_until_the_daemon_acknowledges(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    daemon_leases = {"lease-1"}
    release_calls = []
    settled = threading.Event()

    class Client:
        def acquire_lease(self):
            return {"ok": True, "lease_id": "lease-1", "pid": 101, "epoch": "epoch-1"}

        def release_lease(self, lease_id):
            release_calls.append(lease_id)
            if len(release_calls) == 1:
                return {"ok": False, "_transport_error": "timeout"}
            daemon_leases.discard(lease_id)
            settled.set()
            return {"ok": True}

    monkeypatch.setattr(local_service_client, "LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(webapp, "watchd_descriptor_payloads", lambda: {})
    webapp.watch_client = Client()
    record.watchd_worker = threading.current_thread()

    webapp.watchd_revision_loop(record)

    assert settled.wait(1.0), "the retry owner forgot a timed-out watchd lease release"
    assert release_calls == ["lease-1", "lease-1"]
    assert daemon_leases == set()
    assert record.watchd_lease_id == ""
    assert record.watchd_worker is None


def test_watchd_descriptor_arriving_during_retirement_starts_one_replacement(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    restart_attempts = []

    def descriptor_arrives():
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.descriptors["new-browser"] = ClientWatchDescriptor(
                expires_at=time.monotonic() + 60.0,
                roots=("/repo",),
            )
        restart_attempts.append(webapp.start_watchd_revision_watcher(record))

    client = _WatchdDemandRetirementClient(on_first_release=descriptor_arrives)
    webapp.watch_client = client
    try:
        assert webapp.start_watchd_revision_watcher(record) is True
        assert client.replacement_wait_entered.wait(1.0), "descriptor demand racing with retirement did not start a replacement"
        assert restart_attempts == [False], "the retiring worker must still own the slot during lease release"
        assert client.acquire_count == 2
    finally:
        record.stop_event.set()
        record.watchd_stop_event.set()
        client.wait_release.set()
        worker = record.watchd_worker
        if worker is not None:
            worker.join(timeout=1.0)


def test_explicit_client_event_shutdown_never_restarts_demanded_watchd():
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    webapp.client_watch_service.descriptors["files-browser"] = ClientWatchDescriptor(
        expires_at=time.monotonic() + 60.0,
        roots=("/repo",),
    )
    client = _WatchdDemandRetirementClient(stop_event=record.watchd_stop_event)
    webapp.watch_client = client
    try:
        assert webapp.start_watchd_revision_watcher(record) is True
        assert client.first_wait_entered.wait(1.0)

        webapp.stop_client_event_watcher()

        assert client.lease_released.wait(1.0)
        assert client.acquire_count == 1
        assert webapp.client_watch_service.event_watcher_record is not record
        assert webapp.client_watch_service.event_watcher_record.watchd_worker is None
    finally:
        record.stop_event.set()
        record.watchd_stop_event.set()


def test_watchd_launch_rollback_does_not_latch_the_retained_parent_record(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    webapp.client_watch_service.descriptors["files-browser"] = ClientWatchDescriptor(
        expires_at=time.monotonic() + 60.0,
        roots=("/repo",),
    )
    client = _WatchdDemandRetirementClient()
    webapp.watch_client = client
    real_start = app_module.common.start_thread_with_rollback
    starts = [0]

    def fail_first_start(worker, rollback):
        starts[0] += 1
        if starts[0] == 1:
            rollback()
            raise RuntimeError("watchd thread launch failed")
        return real_start(worker, rollback)

    monkeypatch.setattr(app_module.common, "start_thread_with_rollback", fail_first_start)
    try:
        with pytest.raises(RuntimeError, match="watchd thread launch failed"):
            webapp.start_watchd_revision_watcher(record)
        assert record.watchd_worker is None
        assert record.watchd_stop_event.is_set()

        assert webapp.start_watchd_revision_watcher(record) is True
        assert client.first_wait_entered.wait(1.0), "retry inherited the failed child's stop fence"
        assert client.acquire_count == 1
    finally:
        record.stop_event.set()
        record.watchd_stop_event.set()
        client.wait_release.set()
        worker = record.watchd_worker
        if worker is not None:
            worker.join(timeout=1.0)

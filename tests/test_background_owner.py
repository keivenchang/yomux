from http import HTTPStatus

import json
from pathlib import Path
import queue
import threading
import time

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import background_owner as background_owner_module
from yolomux_lib import control as control_module
from yolomux_lib import file_index
from yolomux_lib import filesystem
from yolomux_lib.background_owner import BACKGROUND_ROLE_SESSION_FILES
from yolomux_lib.background_owner import BACKGROUND_ROLE_SEARCH_INDEX
from yolomux_lib.background_owner import BACKGROUND_ROLE_STATS_SAMPLER
from yolomux_lib.background_owner import BACKGROUND_ROLE_TABBER_ACTIVITY
from yolomux_lib.background_owner import BACKGROUND_ROLE_WATCH_ROOTS
from yolomux_lib.background_owner import BackgroundOwnerRegistry
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo

from _git_helpers import git


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_detached_local_services(monkeypatch):
    """Ownership unit tests must not leave five-minute service daemons behind."""

    monkeypatch.setattr(app_module.JobClient, "start_for_scheduler", lambda self: False)
    monkeypatch.setattr(app_module.StatsClient, "ensure_started", lambda self: True)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "start_stats_metric_scheduler", lambda self: True)


class FollowerOwner:
    def __init__(self):
        self.refresh_requests = []
        self.fallbacks = []

    def can_run(self, role):
        return False

    def record_refresh_request(self, role):
        self.refresh_requests.append(role)

    def record_fallback(self, role):
        self.fallbacks.append(role)

    def record_avoided_recompute(self, role):
        return None

    def record_follower_stale_read(self, role):
        return None

    def status_payload(self):
        return {"owner": False, "status": "follower"}

    def owner_payload(self):
        return {"status": "follower"}

    def is_owner(self):
        return False

    def live_generation_records(self):
        return []

    def release_owner(self, reason="release"):
        return None

    def stop(self):
        return None


class UnresponsiveFollowerOwner(FollowerOwner):
    def request_owner_refresh(self, role, payload=None):
        self.refresh_requests.append(role)
        return {"ok": False, "accepted": False, "role": role, "error": "timeout", "fallback": True}


def test_background_owner_latest_generation_wins(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    older = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    newer = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, clock=lambda: 100.0)
    older.started_at_ns = 10
    newer.started_at_ns = 20
    older.publish_generation()
    newer.publish_generation()

    assert older.is_latest_live_generation() is False
    assert newer.is_latest_live_generation() is True


def test_background_owner_preferred_priority_survives_later_follower_start(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    preferred = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, priority=100, clock=lambda: 100.0)
    later_follower = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, priority=0, clock=lambda: 100.0)
    preferred.started_at_ns = 10
    later_follower.started_at_ns = 20
    preferred.publish_generation()
    later_follower.publish_generation()

    assert preferred.is_latest_live_generation() is True
    assert later_follower.is_latest_live_generation() is False


def test_background_owner_priority_reads_configured_primary_port():
    configured = {background_owner_module.BACKGROUND_OWNER_PRIMARY_PORT_ENV: "8882"}

    assert background_owner_module.background_owner_priority(8882, configured) == 100
    assert background_owner_module.background_owner_priority(8881, configured) == 0
    assert background_owner_module.background_owner_priority(8882, {background_owner_module.BACKGROUND_OWNER_PRIMARY_PORT_ENV: "invalid"}) == 0


def test_background_owner_prunes_dead_generation_records(monkeypatch, tmp_path):
    registry = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    registry.generations_dir.mkdir(parents=True)
    dead_path = registry.generations_dir / "dead.json"
    live_path = registry.generations_dir / "live.json"
    dead_path.write_text(json.dumps({"generation_id": "dead", "pid": 99, "last_heartbeat": 100.0}), encoding="utf-8")
    live_path.write_text(json.dumps({"generation_id": "live", "pid": 100, "last_heartbeat": 100.0}), encoding="utf-8")
    monkeypatch.setattr(background_owner_module, "pid_is_alive", lambda pid: pid == 100)

    records = registry.live_generation_records()

    assert [record["generation_id"] for record in records] == ["live"]
    assert dead_path.exists() is False
    assert live_path.exists() is True


def test_background_owner_generation_index_recovers_from_corruption_without_losing_live_record(monkeypatch, tmp_path):
    monkeypatch.setattr(background_owner_module, "pid_is_alive", lambda pid: pid == 100)
    registry = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    registry.publish_generation()
    registry.generation_index_path.write_text("{broken", encoding="utf-8")

    records = registry.live_generation_records()

    assert [record["generation_id"] for record in records] == [registry.generation_id]
    recovered = json.loads(registry.generation_index_path.read_text(encoding="utf-8"))
    assert recovered["records"][registry.generation_id]["pid"] == 100


def test_background_owner_takeover_requests_release_then_acquires(monkeypatch, tmp_path):
    registry = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=200, clock=lambda: 100.0)
    registry.started_at_ns = 20
    registry.publish_generation()
    registry.owner_dir.mkdir(parents=True, exist_ok=True)
    registry.owner_path.write_text(
        json.dumps({"generation_id": "old", "started_at_ns": 10, "pid": 199, "control_socket": "/tmp/old.sock"}),
        encoding="utf-8",
    )
    acquire_results = iter([False, True])
    release_requests = []
    monkeypatch.setattr(registry, "acquire_owner_lock", lambda: next(acquire_results))
    monkeypatch.setattr(background_owner_module, "send_yolomux_control_request", lambda owner, request, timeout=2.0: release_requests.append((owner, request, timeout)) or {"ok": True})

    assert registry.attempt_takeover() is True

    assert registry.is_owner() is True
    assert registry.status == "owner"
    assert release_requests[0][1]["action"] == "background_release_owner"


def test_background_owner_claim_payload_takeover_demotes_live_owner(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    follower = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, clock=lambda: 100.0)
    owner.started_at_ns = 10
    follower.started_at_ns = 20
    owner.publish_generation()
    follower.publish_generation()
    assert owner.acquire_owner_lock() is True
    owner.owner = True
    owner.status = "owner"
    owner.write_owner_record()

    release_requests = []

    def release_owner(current, request, timeout=2.0):
        release_requests.append((current, request, timeout))
        owner.release_owner("control_release")
        return {"ok": True}

    monkeypatch.setattr(background_owner_module, "send_yolomux_control_request", release_owner)
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.background_owner = follower
    webapp.performance_metrics_payload = lambda: {"record_count": 0}

    payload, status = webapp.background_owner_claim_payload()

    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["claimed"] is True
    assert payload["was_owner"] is False
    assert follower.is_owner() is True
    assert owner.is_owner() is False
    assert release_requests[0][1]["action"] == "background_release_owner"
    follower.release_owner("test-cleanup")


def test_lower_priority_server_cannot_release_preferred_owner(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, priority=100, clock=lambda: 100.0)
    assert owner.attempt_takeover() is True
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.background_owner = owner

    payload = webapp.background_release_owner({"pid": 101, "priority": 0})

    assert payload["ok"] is False
    assert payload["owner"] is True
    assert owner.is_owner() is True
    owner.release_owner("test-cleanup")


def test_background_owner_reports_blocked_unreachable_owner(monkeypatch, tmp_path):
    registry = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=200, clock=lambda: 100.0)
    registry.started_at_ns = 20
    registry.publish_generation()
    registry.owner_dir.mkdir(parents=True, exist_ok=True)
    registry.owner_path.write_text(
        json.dumps({"generation_id": "old", "started_at_ns": 10, "pid": 199, "control_socket": "/tmp/missing.sock"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "acquire_owner_lock", lambda: False)
    monkeypatch.setattr(background_owner_module, "send_yolomux_control_request", lambda *_args, **_kwargs: {"ok": False, "error": "connect failed"})

    assert registry.attempt_takeover() is False

    assert registry.status == "blocked_by_unreachable_owner"
    assert "connect failed" in registry.last_error


def test_background_owner_self_demotes_when_newer_generation_appears(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    demotions = []
    older = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, on_demote=lambda: demotions.append("demoted"), clock=lambda: 100.0)
    newer = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, clock=lambda: 100.0)
    older.started_at_ns = 10
    newer.started_at_ns = 20
    older.owner = True
    older.status = "owner"
    older.publish_generation()
    newer.publish_generation()

    older.heartbeat_once()

    assert older.is_owner() is False
    assert older.status == "follower"
    assert demotions == ["demoted"]


def test_background_owner_older_follower_does_not_reacquire_with_newer_live(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    older = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    newer = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, clock=lambda: 100.0)
    older.started_at_ns = 10
    newer.started_at_ns = 20
    older.status = "follower"
    newer.owner = True
    newer.status = "owner"
    older.publish_generation()
    newer.publish_generation()
    takeover_calls = []
    monkeypatch.setattr(older, "attempt_takeover", lambda: takeover_calls.append(True) or False)

    older.heartbeat_once()

    assert older.status == "follower"
    assert takeover_calls == []


def test_background_owner_dead_owner_released_lock_allows_newer_acquire(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda pid: pid != 100)
    old_owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    newer = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, clock=lambda: 100.0)
    old_owner.started_at_ns = 10
    newer.started_at_ns = 20
    old_owner.publish_generation()
    newer.publish_generation()
    assert old_owner.acquire_owner_lock() is True
    old_owner.owner = True
    old_owner.status = "owner"
    old_owner.write_owner_record()
    old_owner.release_owner_lock()

    assert newer.attempt_takeover() is True

    assert newer.is_owner() is True
    assert newer.status == "owner"
    newer.release_owner("test-cleanup")


def test_background_owner_runtime_takeover_notifies_acquire_callback(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda pid: pid != 100)
    old_owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=100, clock=lambda: 100.0)
    follower_events = []
    follower = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", pid=101, clock=lambda: 100.0, on_acquire=lambda status: follower_events.append(status))
    old_owner.started_at_ns = 20
    follower.started_at_ns = 10
    old_owner.publish_generation()
    follower.publish_generation()
    assert old_owner.acquire_owner_lock() is True
    old_owner.owner = True
    old_owner.status = "owner"
    old_owner.write_owner_record()
    old_owner.release_owner_lock()

    follower.heartbeat_once()

    assert follower.is_owner() is True
    assert follower.status == "owner"
    assert follower_events
    assert follower_events[-1]["search_index"]["owner"] is True
    assert follower_events[-1]["search_index"]["mode"] == "indexing-server"
    follower.release_owner("test-cleanup")


def test_background_owner_recovers_from_missing_or_corrupt_state(monkeypatch, tmp_path):
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: True)
    owner_dir = tmp_path / "owner"
    (owner_dir / "generations").mkdir(parents=True)
    (owner_dir / "owner.json").write_text("{not json", encoding="utf-8")
    (owner_dir / "generations" / "bad.json").write_text("{not json", encoding="utf-8")
    registry = BackgroundOwnerRegistry(owner_dir=owner_dir, pid=100, clock=lambda: 100.0)
    registry.started_at_ns = 10

    assert registry.attempt_takeover() is True

    assert registry.is_owner() is True
    assert registry.status == "owner"
    assert registry.read_owner_record()["generation_id"] == registry.generation_id
    registry.release_owner("test-cleanup")


def test_two_app_instances_same_state_dir_elect_newer_owner(monkeypatch, tmp_path):
    real_registry = app_module.BackgroundOwnerRegistry
    started_at = iter([10, 20])

    def registry_factory(**kwargs):
        registry = real_registry(owner_dir=tmp_path / "owner", **kwargs)
        registry.started_at_ns = next(started_at)
        return registry

    monkeypatch.setattr(app_module, "BackgroundOwnerRegistry", registry_factory)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", tmp_path / "control")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    first = app_module.TmuxWebtermApp(["1"])
    second = app_module.TmuxWebtermApp(["1"])
    try:
        assert first.start_background_owner(port=9901) is True
        assert second.start_background_owner(port=9903) is True

        assert first.background_owner.is_owner() is False
        assert first.background_owner.status == "follower"
        assert second.background_owner.is_owner() is True
        assert second.background_owner.status == "owner"
    finally:
        first.background_owner.stop()
        second.background_owner.stop()
        first.control_server.stop()
        second.control_server.stop()


def test_background_refresh_done_fanout_reaches_follower_client_broker(monkeypatch, tmp_path):
    real_registry = app_module.BackgroundOwnerRegistry
    started_at = iter([10, 20])

    def registry_factory(**kwargs):
        registry = real_registry(owner_dir=tmp_path / "owner", **kwargs)
        registry.started_at_ns = next(started_at)
        return registry

    monkeypatch.setattr(app_module, "BackgroundOwnerRegistry", registry_factory)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", tmp_path / "control")
    monkeypatch.setattr(app_module, "BACKGROUND_CLIENT_EVENTS_PATH", tmp_path / "background-events.json")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    first = app_module.TmuxWebtermApp(["1"])
    second = app_module.TmuxWebtermApp(["1"])
    subscriber_id, subscriber_queue = first.client_events.subscribe()
    try:
        assert first.start_background_owner(port=9901) is True
        assert second.start_background_owner(port=9903) is True
        assert first.background_owner.status == "follower"
        assert second.background_owner.status == "owner"

        second.publish_background_refresh_done(BACKGROUND_ROLE_SESSION_FILES, {"session": "1", "cache_key": "session:1"})
        delivered = []
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not delivered:
            try:
                event = subscriber_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if event.get("type") == "background_refresh_done":
                delivered.append(event)
    finally:
        first.client_events.unsubscribe(subscriber_id)
        first.background_owner.stop()
        second.background_owner.stop()
        first.control_server.stop()
        second.control_server.stop()

    assert delivered
    assert delivered[0]["payload"]["role"] == BACKGROUND_ROLE_SESSION_FILES
    assert delivered[0]["payload"]["session"] == "1"
    manifest = json.loads((tmp_path / "background-events.json").read_text(encoding="utf-8"))
    assert manifest["events"][-1]["type"] == "background_refresh_done"
    assert manifest["events"][-1]["payload"]["role"] == BACKGROUND_ROLE_SESSION_FILES


def test_background_owner_startup_order_latest_port_wins(monkeypatch, tmp_path):
    real_registry = app_module.BackgroundOwnerRegistry
    started_at = iter([10, 20, 30, 40, 50])

    def registry_factory(**kwargs):
        registry = real_registry(owner_dir=tmp_path / "owner", **kwargs)
        registry.started_at_ns = next(started_at)
        return registry

    monkeypatch.setattr(app_module, "BackgroundOwnerRegistry", registry_factory)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", tmp_path / "control")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    apps = []
    try:
        for port in (9910, 9911, 9912, 9913):
            app = app_module.TmuxWebtermApp(["1"])
            apps.append(app)
            assert app.start_background_owner(port=port) is True
        assert [app.background_owner.status for app in apps] == ["follower", "follower", "follower", "owner"]
        assert apps[-1].background_owner.port == 9913

        restarted_9911 = app_module.TmuxWebtermApp(["1"])
        apps.append(restarted_9911)
        assert restarted_9911.start_background_owner(port=9911) is True

        assert [app.background_owner.status for app in apps] == ["follower", "follower", "follower", "follower", "owner"]
        assert restarted_9911.background_owner.port == 9911
    finally:
        for app in apps:
            app.background_owner.stop()
            app.control_server.stop()


def test_preferred_owner_stays_stable_while_later_follower_starts(monkeypatch, tmp_path):
    real_registry = app_module.BackgroundOwnerRegistry
    started_at = iter([10, 20])

    def registry_factory(**kwargs):
        registry = real_registry(owner_dir=tmp_path / "owner", **kwargs)
        registry.started_at_ns = next(started_at)
        return registry

    monkeypatch.setattr(app_module, "BackgroundOwnerRegistry", registry_factory)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", tmp_path / "control")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    preferred = app_module.TmuxWebtermApp(["1"])
    follower = app_module.TmuxWebtermApp(["1"])
    try:
        assert preferred.start_background_owner(port=8882, priority=100) is True
        assert follower.start_background_owner(port=8883, priority=0) is False

        assert preferred.background_owner.is_owner() is True
        assert follower.background_owner.is_owner() is False
        assert preferred.background_owner.counters["owner_acquired"] == 1
        assert preferred.background_owner.counters["owner_released"] == 0
    finally:
        preferred.background_owner.stop()
        follower.background_owner.stop()
        preferred.control_server.stop()
        follower.control_server.stop()


def test_follower_has_no_expensive_worker_threads_after_takeover(monkeypatch, tmp_path):
    real_registry = app_module.BackgroundOwnerRegistry
    started_at = iter([10, 20])

    def registry_factory(**kwargs):
        registry = real_registry(owner_dir=tmp_path / "owner", **kwargs)
        registry.started_at_ns = next(started_at)
        return registry

    monkeypatch.setattr(app_module, "BackgroundOwnerRegistry", registry_factory)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    first = app_module.TmuxWebtermApp(["1"])
    second = app_module.TmuxWebtermApp(["1"])
    try:
        assert first.start_background_owner(port=9901) is True
        assert second.start_background_owner(port=9903) is True
        assert first.background_owner.status == "follower"
        assert first.start_tabber_activity_cache_warmer() is False
        assert first.start_session_files_cache_refresh(("payload", "1"), lambda *_args: None) is False
        file_index.set_background_owner_checker(first.background_can_run)
        monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not start file-index workers")))
        filesystem.index_status(str(tmp_path))

        names = {thread.name for thread in threading.enumerate()}
    finally:
        file_index.set_background_owner_checker(None)
        first.background_owner.stop()
        second.background_owner.stop()
        first.control_server.stop()
        second.control_server.stop()

    assert "tabber-activity-cache" not in names
    assert "session-files-warm" not in names
    assert all(not name.startswith("file-index-") for name in names)


def test_background_release_owner_stops_background_worker_state(no_control_socket, monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", on_demote=webapp.demote_background_owner, clock=lambda: 100.0)
    owner.owner = True
    owner.status = "owner"
    webapp.background_owner = owner
    webapp.activity_transcript_service.tabber_warmer_record.running = True
    webapp.activity_transcript_service.tabber_cache_record.refresh_worker = threading.Thread()
    webapp.session_files_service.work_records[("payload", "1")] = app_module.SessionFilesWorkRecord()
    cleared_indexes = []
    monkeypatch.setattr(file_index, "clear_memory_indexes", lambda: cleared_indexes.append(True))
    try:
        response = webapp.background_release_owner({"generation_id": "newer"})
        events = webapp.event_log.tail(limit=5)
    finally:
        webapp.control_server.stop()

    assert response["ok"] is True
    assert owner.is_owner() is False
    assert owner.status == "follower"
    assert webapp.activity_transcript_service.tabber_warmer_record.running is False
    assert webapp.activity_transcript_service.tabber_cache_record.refresh_worker is None
    assert webapp.session_files_service.work_records == {}
    assert cleared_indexes == [True]
    assert "background_owner_released" in {event["type"] for event in events}


def test_background_owner_required_log_event_names_have_emitters():
    source = (REPO_ROOT / "yolomux_lib" / "app.py").read_text(encoding="utf-8")

    assert "on_acquire=self.handle_background_owner_acquired" in source
    assert "background_refresh_event_log_records" in source
    assert "background_refresh_event_log_counts" not in source
    assert "background_refresh_event_log_last_emit_counts" not in source
    for event_type in (
        "background_owner_acquired",
        "background_owner_released",
        "background_owner_takeover",
        "background_owner_blocked",
        "background_refresh_started",
        "background_refresh_done",
    ):
        assert f'"{event_type}"' in source


def test_background_refresh_event_log_is_sampled_and_sanitized(no_control_socket, monkeypatch):
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    monkeypatch.setattr(app_module, "BACKGROUND_REFRESH_EVENT_LOG_SAMPLE_EVERY", 3)
    webapp = app_module.TmuxWebtermApp(["1"])
    raw_cache_key = ("payload", "1", "/tmp/repo", {"branch": "main"})
    events = []
    try:
        for _index in range(5):
            event = webapp.log_sampled_background_refresh_event(
                "background_refresh_started",
                BACKGROUND_ROLE_SESSION_FILES,
                "Session-files background refresh started",
                webapp.background_refresh_event_details(BACKGROUND_ROLE_SESSION_FILES, {"session": "1"}, cache_key=raw_cache_key),
                message_key="events.message.backgroundRefresh.started",
                message_params={
                    "target": {
                        "key": "backgroundOwner.sessionFiles",
                        "params": {},
                        "fallback": "Session files",
                    },
                },
            )
            if event:
                events.append(event)
    finally:
        webapp.control_server.stop()

    assert len(events) == 2
    first_details = events[0]["details"]
    second_details = events[1]["details"]
    assert first_details["sample_count"] == 1
    assert second_details["sample_count"] == 3
    assert second_details["suppressed_since_last"] == 1
    sample_record = webapp.background_refresh_event_log_records[("background_refresh_started", BACKGROUND_ROLE_SESSION_FILES)]
    assert sample_record.count == 5
    assert sample_record.last_emit_count == 3
    assert first_details["cache_key_kind"] == "payload"
    assert "cache_key_hash" in first_details
    assert "cache_key" not in first_details
    assert repr(raw_cache_key) not in json.dumps(events, sort_keys=True)
    assert all(event["message_key"] == "events.message.backgroundRefresh.started" for event in events)
    assert all(event["message_params"]["target"]["key"] == "backgroundOwner.sessionFiles" for event in events)


def test_background_owner_status_reports_required_counters(tmp_path):
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", clock=lambda: 100.0)
    owner.record_refresh_request(BACKGROUND_ROLE_SESSION_FILES)
    owner.record_fallback(BACKGROUND_ROLE_SESSION_FILES)
    owner.record_avoided_recompute(BACKGROUND_ROLE_SESSION_FILES)
    owner.record_follower_stale_read(BACKGROUND_ROLE_SESSION_FILES)
    owner.record_search_index_bytes_written(123)

    payload = owner.status_payload()

    assert payload["roles"][BACKGROUND_ROLE_SESSION_FILES]["refresh_requests"] == 1
    assert payload["roles"][BACKGROUND_ROLE_SESSION_FILES]["fallback_count"] == 1
    assert payload["roles"][BACKGROUND_ROLE_STATS_SAMPLER]["role"] == BACKGROUND_ROLE_STATS_SAMPLER
    assert payload["counters"]["owner_refresh_requests"] == 1
    assert payload["counters"]["avoided_recomputes"] == 1
    assert payload["counters"]["follower_stale_reads"] == 1
    assert payload["counters"]["search_index_bytes_written"] == 123
    assert payload["search_index"]["mode"] == "read-server"
    assert payload["search_index"]["current_server"]["generation_id"] == owner.generation_id


def test_background_owner_coalesces_duplicate_local_refresh_requests(tmp_path):
    now = [10.0]
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", clock=lambda: 100.0, monotonic=lambda: now[0])
    owner.owner = True
    owner.status = "owner"

    first = owner.request_owner_refresh(BACKGROUND_ROLE_SESSION_FILES, {"cache_key": "same", "reason": "stale-read", "requester": {"pid": 1}})
    second = owner.request_owner_refresh(BACKGROUND_ROLE_SESSION_FILES, {"cache_key": "same", "reason": "watch-state", "requester": {"pid": 2}})
    now[0] += background_owner_module.BACKGROUND_REFRESH_COALESCE_SECONDS + 0.1
    third = owner.request_owner_refresh(BACKGROUND_ROLE_SESSION_FILES, {"cache_key": "same"})

    assert first["accepted"] is True
    assert not first.get("coalesced")
    assert second["accepted"] is True
    assert second["coalesced"] is True
    assert third["accepted"] is True
    assert not third.get("coalesced")
    payload = owner.status_payload()
    assert payload["roles"][BACKGROUND_ROLE_SESSION_FILES]["refresh_requests"] == 2
    assert payload["counters"]["owner_refresh_requests"] == 2
    assert payload["counters"]["coalesced_refresh_requests"] == 1


def test_background_owner_coalesces_duplicate_follower_control_refreshes(monkeypatch, tmp_path):
    now = [10.0]
    follower = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", clock=lambda: 100.0, monotonic=lambda: now[0])
    follower.owner_dir.mkdir(parents=True)
    follower.owner_path.write_text(
        json.dumps({"generation_id": "owner", "pid": 123, "last_heartbeat": 100.0, "control_socket": "/tmp/owner.sock"}),
        encoding="utf-8",
    )
    control_requests = []
    monkeypatch.setattr(
        background_owner_module,
        "send_yolomux_control_request",
        lambda owner, request, timeout: control_requests.append((owner, request, timeout)) or {"ok": True, "accepted": True},
    )

    first = follower.request_owner_refresh(BACKGROUND_ROLE_WATCH_ROOTS, {"cache_key": "watch:/repo", "roots": ["/repo"], "reason": "poll"})
    second = follower.request_owner_refresh(BACKGROUND_ROLE_WATCH_ROOTS, {"cache_key": "watch:/repo", "roots": ["/repo"], "reason": "watch-state"})

    assert first["accepted"] is True
    assert not first.get("coalesced")
    assert second["accepted"] is True
    assert second["coalesced"] is True
    assert len(control_requests) == 1
    payload = follower.status_payload()
    assert payload["roles"][BACKGROUND_ROLE_WATCH_ROOTS]["refresh_requests"] == 1
    assert payload["counters"]["owner_refresh_requests"] == 1
    assert payload["counters"]["coalesced_refresh_requests"] == 1


def test_background_owner_heartbeat_updates_while_worker_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(background_owner_module, "BACKGROUND_OWNER_HEARTBEAT_SECONDS", 0.01)
    clock_value = [100.0]
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", clock=lambda: clock_value[0])
    owner.started_at_ns = 10
    assert owner.start() is True
    worker_release = threading.Event()
    worker_started = threading.Event()

    def busy_worker():
        worker_started.set()
        worker_release.wait(timeout=1.0)

    worker = threading.Thread(target=busy_worker, name="file-index-test-worker")
    worker.start()
    try:
        worker_started.wait(timeout=1.0)
        first_heartbeat = json.loads(owner.owner_path.read_text(encoding="utf-8"))["last_heartbeat"]
        clock_value[0] = 101.0
        owner.heartbeat_once()
        second_heartbeat = json.loads(owner.owner_path.read_text(encoding="utf-8"))["last_heartbeat"]
    finally:
        worker_release.set()
        worker.join(timeout=1.0)
        owner.stop()

    assert first_heartbeat == 100.0
    assert second_heartbeat == 101.0


def test_follower_does_not_start_tabber_activity_warmer(no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    try:
        assert webapp.start_tabber_activity_cache_warmer() is False
        assert webapp.activity_transcript_service.tabber_warmer_record.running is False
        assert owner.refresh_requests == [BACKGROUND_ROLE_TABBER_ACTIVITY]
    finally:
        webapp.control_server.stop()


def test_metadata_warmer_stops_between_sessions_after_owner_demotion(monkeypatch, no_control_socket):
    class MutableOwner(FollowerOwner):
        def __init__(self):
            super().__init__()
            self.allowed = True

        def can_run(self, role):
            return self.allowed

    webapp = app_module.TmuxWebtermApp([])
    owner = MutableOwner()
    webapp.background_owner = owner
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()
    second_release = threading.Event()
    calls = []
    no_network_after_demote = []

    def graph(info, _cache, allow_network=False):
        if not allow_network:
            if not owner.allowed:
                no_network_after_demote.append(info.session)
            return {}
        calls.append(info.session)
        if info.session == "first":
            first_started.set()
            first_release.wait(timeout=2.0)
        elif info.session == "second":
            second_started.set()
            second_release.wait(timeout=2.0)
        return {}

    monkeypatch.setattr(app_module, "session_work_graph", graph)
    sessions = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("first", "second")
    }
    try:
        webapp.warm_metadata_cache_async(sessions)
        with webapp.metadata_warm_lock:
            first_worker = webapp.metadata_warm_record.worker
        assert first_worker is not None
        assert first_started.wait(timeout=1.0)

        owner.allowed = False
        webapp.demote_background_owner()
        with webapp.metadata_warm_lock:
            assert webapp.metadata_warm_record.stop_event.is_set() is True
        first_release.set()
        first_worker.join(timeout=1.0)

        assert calls == ["first"]
        assert no_network_after_demote == []
        with webapp.metadata_warm_lock:
            assert webapp.metadata_warm_record.worker is None
        assert not hasattr(webapp, "metadata_warm_running")

        owner.allowed = True
        webapp.warm_metadata_cache_async({"second": sessions["second"]})
        assert second_started.wait(timeout=1.0)
        with webapp.metadata_warm_lock:
            second_worker = webapp.metadata_warm_record.worker
        assert second_worker is not None
        second_release.set()
        second_worker.join(timeout=1.0)
        assert calls == ["first", "second"]
    finally:
        first_release.set()
        second_release.set()
        webapp.control_server.stop()


def test_follower_activity_payload_without_cache_returns_empty_refreshing(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    monkeypatch.setattr(webapp, "get_tabber_activity_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(webapp, "build_activity_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not build activity payloads")))
    try:
        payload, status = webapp.activity_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["activity"] == {}
    assert payload["session_scope"] == "configured"
    assert payload["cache"]["refreshing_elsewhere"] is True
    assert owner.refresh_requests == [BACKGROUND_ROLE_TABBER_ACTIVITY]


def test_follower_serves_stale_session_files_without_recomputing(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    key = webapp.session_files_cache_key("payload", {"1": info}, "1", 24.0, None, None, None)
    stale_payload = {"files": [{"path": "old.py"}], "repos": [], "errors": []}
    webapp.set_session_files_memory_cache(key, stale_payload, HTTPStatus.OK, stored_at=app_module.time.monotonic() - 999.0)
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not recompute stale session-files payloads")))
    try:
        payload = webapp.cached_session_files_payload_for_info(info)
    finally:
        webapp.control_server.stop()

    assert payload == stale_payload
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES]
    assert owner.fallbacks == []


def test_follower_missing_session_files_returns_empty_without_recomputing(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not compute missing session-files payloads")))
    try:
        payload = webapp.cached_session_files_payload_for_info(info)
    finally:
        webapp.control_server.stop()

    assert payload == {"files": [], "repos": [], "errors": [], "refreshing_elsewhere": True}
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES]
    assert owner.fallbacks == []


def test_follower_batch_session_files_does_not_spawn_warm_threads(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    infos = {
        "1": SessionInfo(session="1", panes=[], selected_pane=None, agents=[]),
        "2": SessionInfo(session="2", panes=[], selected_pane=None, agents=[]),
    }

    assert not hasattr(app_module, "ThreadPoolExecutor")
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not compute missing session-files payloads")))
    try:
        payloads = webapp.cached_session_files_payloads_for_infos(infos)
    finally:
        webapp.control_server.stop()

    assert sorted(payloads) == ["1", "2"]
    assert all(payload["refreshing_elsewhere"] is True for payload in payloads.values())
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES, BACKGROUND_ROLE_SESSION_FILES]


def test_follower_missing_session_files_uses_bounded_fallback_when_owner_unresponsive(monkeypatch, no_control_socket, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = UnresponsiveFollowerOwner()
    webapp.background_owner = owner
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    compute_calls = []

    def compute_payload(*_args, **_kwargs):
        compute_calls.append(True)
        return {"files": [{"path": "fallback.py"}], "repos": [], "errors": []}

    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", compute_payload)
    try:
        payload = webapp.cached_session_files_payload_for_info(info)
        events = webapp.event_log.tail(limit=5)
    finally:
        file_index.set_background_owner_refresh_requester(None)
        webapp.control_server.stop()

    assert payload["files"] == [{"path": "fallback.py"}]
    assert compute_calls == [True]
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES]
    assert owner.fallbacks == [BACKGROUND_ROLE_SESSION_FILES]
    assert "background_refresh_fallback" in {event["type"] for event in events}


def test_follower_session_files_http_payload_returns_refreshing_elsewhere(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module.session_files, "session_files_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not compute missing session-files HTTP payloads")))
    try:
        payload, status = webapp.session_files_payload_for_infos("1", {"1": info}, 24.0)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["files"] == []
    assert payload["repos"] == []
    assert payload["cache"]["stale"] is True
    assert payload["cache"]["refreshing_elsewhere"] is True
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES]
    assert owner.fallbacks == []


def test_follower_session_files_http_placeholder_includes_clean_numbered_workdir_repo(monkeypatch, no_control_socket, tmp_path):
    webapp = app_module.TmuxWebtermApp(["8002"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    home = tmp_path / "home"
    repo = tmp_path / "yolomux.dev8002"
    home.mkdir()
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    pane = PaneInfo(
        session="8002",
        window="0",
        pane="0",
        pane_id="%1",
        target="8002:0.0",
        current_path=str(home),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="8002", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(app_module.session_files, "session_workdir", lambda session: repo if session == "8002" else home)
    monkeypatch.setattr(app_module.session_files, "session_files_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not compute missing session-files HTTP payloads")))
    try:
        payload, status = webapp.session_files_payload_for_infos("8002", {"8002": info}, 24.0)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["files"] == []
    assert payload["repos"] == [{
        "repo": str(repo),
        "count": 0,
        "touched_count": 0,
        "added": 0,
        "removed": 0,
        "from_ref": "default",
        "to_ref": "base",
        "error": "",
    }]
    assert payload["refreshing_elsewhere"] is True
    assert payload["cache"]["refreshing_elsewhere"] is True
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES]
    assert owner.fallbacks == []


def test_follower_session_files_http_payload_stale_marks_refreshing_elsewhere(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = FollowerOwner()
    webapp.background_owner = owner
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    key = webapp.session_files_cache_key("payload", {"1": info}, "1", 24.0, None, None, None)
    webapp.set_session_files_memory_cache(key, {"files": [{"path": "old.py"}], "repos": [], "errors": []}, HTTPStatus.OK, stored_at=app_module.time.monotonic() - 999.0)
    monkeypatch.setattr(app_module.session_files, "session_files_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not recompute stale HTTP payloads")))
    try:
        payload, status = webapp.session_files_payload_for_infos("1", {"1": info}, 24.0)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["files"] == [{"path": "old.py"}]
    assert payload["cache"]["stale"] is True
    assert payload["cache"]["refreshing_elsewhere"] is True
    assert "refreshing" not in payload["cache"]
    assert owner.refresh_requests == [BACKGROUND_ROLE_SESSION_FILES]


def test_follower_activity_payload_uses_bounded_fallback_when_owner_unresponsive(monkeypatch, no_control_socket):
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = UnresponsiveFollowerOwner()
    webapp.background_owner = owner
    monkeypatch.setattr(webapp, "get_tabber_activity_cache", lambda *_args, **_kwargs: None)
    build_calls = []

    def build_payload(*_args, **_kwargs):
        build_calls.append(True)
        return {"activity": {"1": {"state": "ready"}}, "agents": [], "agent_windows": {}, "errors": [], "session_scope": "configured", "session_file_hours": 24.0}

    monkeypatch.setattr(webapp, "build_activity_payload", build_payload)
    try:
        payload, status = webapp.activity_payload()
    finally:
        file_index.set_background_owner_refresh_requester(None)
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["activity"] == {"1": {"state": "ready"}}
    assert payload["cache"]["fallback"] is True
    assert build_calls == [True]
    assert owner.refresh_requests == [BACKGROUND_ROLE_TABBER_ACTIVITY]
    assert owner.fallbacks == [BACKGROUND_ROLE_TABBER_ACTIVITY]


def test_search_index_follower_does_not_walk_missing_index(monkeypatch, tmp_path):
    (tmp_path / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()
    file_index.set_background_owner_checker(lambda _role: False)
    file_index.set_background_owner_refresh_requester(None)
    monkeypatch.setattr(filesystem.search, "_search_full_tree", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follower must not walk full roots")))
    try:
        payload = filesystem.search_files(str(tmp_path), query="target", recursive=True)
        status = filesystem.index_status(str(tmp_path))
    finally:
        file_index.set_background_owner_checker(None)

    assert payload["index_state"] == "follower"
    assert payload["refreshing_elsewhere"] is True
    assert payload["files"] == []
    assert status["state"] == "follower"
    assert status["refreshing_elsewhere"] is True


def test_search_index_follower_missing_index_timing_regression(monkeypatch, tmp_path):
    (tmp_path / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()
    file_index.set_background_owner_checker(lambda _role: False)
    file_index.set_background_owner_refresh_requester(None)
    slow_walk_calls = []

    def slow_full_tree(*_args, **_kwargs):
        slow_walk_calls.append(True)
        time.sleep(0.35)
        return 1, 1, False

    monkeypatch.setattr(filesystem.search, "_search_full_tree", slow_full_tree)
    try:
        started = time.perf_counter()
        payload = filesystem.search_files(str(tmp_path), query="target", recursive=True)
        elapsed = time.perf_counter() - started
    finally:
        file_index.set_background_owner_checker(None)

    assert payload["index_state"] == "follower"
    assert slow_walk_calls == []
    # The contract is that the follower does no filesystem work (asserted above). Keep a bounded
    # scheduling allowance for the concurrent full gate; either deliberately slow helper takes 350ms.
    assert elapsed < 0.3


def test_watch_roots_follower_poll_skips_directory_signature_timing_regression(monkeypatch, tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})
    slow_signature_calls = []
    list_calls = []

    def slow_watch_signature(path):
        slow_signature_calls.append(path)
        time.sleep(0.35)
        return (str(path), "slow")

    def slow_list_directory(path):
        list_calls.append(path)
        time.sleep(0.35)
        return {"entries": []}

    monkeypatch.setattr(app_module, "filesystem_watch_signature", slow_watch_signature)
    monkeypatch.setattr(app_module.filesystem, "list_directory", slow_list_directory)
    webapp = app_module.TmuxWebtermApp([])
    owner = FollowerOwner()
    webapp.background_owner = owner
    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: ("settings",))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: ("transcripts",))
    try:
        webapp.update_client_watch_roots({"roots": [str(watched)]})
        assert webapp.client_watch_roots_snapshot() == [str(watched)]
        started = time.perf_counter()
        events = webapp.poll_client_events_once()
        elapsed = time.perf_counter() - started
    finally:
        webapp.control_server.stop()

    assert events == []
    assert slow_signature_calls == []
    assert list_calls == []
    assert owner.refresh_requests == [BACKGROUND_ROLE_WATCH_ROOTS]
    # The assertion above proves the expensive full-index JSON load was not
    # used. Leave scheduler headroom for an xdist-saturated development host.
    assert elapsed < 0.35


def test_search_index_follower_uses_one_shot_fallback_when_owner_unresponsive(monkeypatch, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()
    file_index.set_background_owner_checker(lambda _role: False)
    refresh_requests = []
    walk_calls = []

    def request_refresh(role, payload):
        refresh_requests.append((role, payload))
        return {"ok": False, "accepted": False, "role": role, "fallback": True, "error": "timeout"}

    def fallback_walk(root, _current, _tokens, results, _skip_dirs=None):
        walk_calls.append(root)
        results.append({
            "name": "target.py",
            "path": str(target),
            "relative_path": "target.py",
            "kind": "file",
            "_sort_key": (0, 0, 0, 0, 0, "target.py"),
        })
        return 1, 1, False

    file_index.set_background_owner_refresh_requester(request_refresh)
    monkeypatch.setattr(filesystem, "_search_full_tree", fallback_walk)
    try:
        payload = filesystem.search_files(str(tmp_path), query="target", recursive=True)
    finally:
        file_index.set_background_owner_checker(None)
        file_index.set_background_owner_refresh_requester(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert [entry["relative_path"] for entry in payload["files"]] == ["target.py"]
    assert refresh_requests[0][0] == BACKGROUND_ROLE_SEARCH_INDEX
    assert walk_calls == [tmp_path]


def test_search_index_build_publishes_background_refresh_done(monkeypatch, tmp_path):
    (tmp_path / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    done_events = []
    file_index.set_background_owner_done_notifier(lambda role, payload: done_events.append((role, payload)))
    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.build_now(
            tmp_path,
            filesystem.search.SEARCH_SKIP_DIRS,
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
    finally:
        file_index.set_background_owner_done_notifier(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert done_events
    role, payload = done_events[-1]
    assert role == BACKGROUND_ROLE_SEARCH_INDEX
    assert payload["root"] == str(tmp_path)
    assert payload["state"] == "ready"
    assert payload["entries"] >= 1


def test_directory_rename_invalidates_and_rebuilds_search_index(monkeypatch, tmp_path):
    root = tmp_path / "root"
    old_dir = root / "migration-tools"
    old_dir.mkdir(parents=True)
    (old_dir / "manifest.yaml").write_text("name: old\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")

    def synchronous_start(index, skip_dirs, exclude_path=None, exclude_signature=""):
        file_index._run_build(index, set(skip_dirs), exclude_path=exclude_path, exclude_signature=exclude_signature)

    monkeypatch.setattr(file_index, "_start_build", synchronous_start)
    file_index.set_background_owner_checker(lambda _role: True)
    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.build_now(
            root,
            filesystem.SEARCH_SKIP_DIRS,
            exclude_path=filesystem._path_is_secret,
            exclude_signature=filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        before = filesystem.search_files(str(root), query="migration-tools", recursive=True)
        renamed = filesystem.rename_path(str(old_dir), "home-manifest")
        after_new = filesystem.search_files(str(root), query="home-manifest", recursive=True)
        after_old = filesystem.search_files(str(root), query="migration-tools", recursive=True)
    finally:
        file_index.set_background_owner_checker(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert [entry["relative_path"] for entry in before["files"]] == ["migration-tools/manifest.yaml"]
    assert renamed["reindex_roots"] == [str(root)]
    assert [entry["relative_path"] for entry in after_new["files"]] == ["home-manifest/manifest.yaml"]
    assert after_old["files"] == []


def test_directory_rename_follower_requests_owner_index_rebuild(monkeypatch, tmp_path):
    root = tmp_path / "root"
    old_dir = root / "migration-tools"
    old_dir.mkdir(parents=True)
    (old_dir / "manifest.yaml").write_text("name: old\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    requests = []
    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.build_now(root, filesystem.SEARCH_SKIP_DIRS)
        file_index.set_background_owner_checker(lambda _role: False)
        file_index.set_background_owner_refresh_requester(lambda role, payload: requests.append((role, payload)) or {"ok": True, "accepted": True})
        renamed = filesystem.rename_path(str(old_dir), "home-manifest")
    finally:
        file_index.set_background_owner_checker(None)
        file_index.set_background_owner_refresh_requester(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert renamed["reindex_roots"] == [str(root)]
    assert requests == [(BACKGROUND_ROLE_SEARCH_INDEX, {
        "root": str(root),
        "paths": [str(root / "home-manifest"), str(old_dir)],
        "path": str(root / "home-manifest"),
        "reason": "fs-rename",
    })]


def test_local_owner_search_index_refresh_request_enqueues_persistent_indexer(no_control_socket, monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp(["1"])
    enqueued = []
    monkeypatch.setattr(webapp.background_owner, "request_owner_refresh", lambda role, payload: {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False})
    monkeypatch.setattr(
        webapp.search_indexer,
        "enqueue",
        lambda root, paths, reason: enqueued.append((root, paths, reason)) or {"ok": True, "accepted": True},
    )
    try:
        result = webapp.request_background_refresh(BACKGROUND_ROLE_SEARCH_INDEX, {"root": str(tmp_path), "reason": "fs-rename"})
    finally:
        webapp.control_server.stop()

    assert enqueued == [(str(tmp_path), [], "fs-rename")]
    assert result["indexer"] == {"ok": True, "accepted": True}


def test_local_owner_search_index_unindex_uses_persistent_indexer(no_control_socket, monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp(["1"])
    removed = []
    monkeypatch.setattr(webapp.background_owner, "request_owner_refresh", lambda role, payload: {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False})
    monkeypatch.setattr(webapp.search_indexer, "unindex", lambda root: removed.append(root) or {"ok": True, "accepted": True})
    try:
        result = webapp.request_background_refresh(BACKGROUND_ROLE_SEARCH_INDEX, {"root": str(tmp_path), "operation": "unindex", "reason": "unindex"})
    finally:
        webapp.control_server.stop()

    assert removed == [str(tmp_path)]
    assert result["indexer"] == {"ok": True, "accepted": True}


def test_local_owner_session_files_refresh_request_starts_requested_cache_key(no_control_socket, monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    owner_key = webapp.session_files_cache_key("payload", {"1": info}, "1", 24.0, "HEAD", "current", None)
    requested_key = (*owner_key[:-1], (("1", ("follower-snapshot",)),))
    starts = []
    monkeypatch.setattr(webapp.background_owner, "request_owner_refresh", lambda role, payload: {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False})
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(webapp, "start_session_files_cache_refresh", lambda *args: starts.append(args) or True)
    try:
        result = webapp.request_background_refresh(
            BACKGROUND_ROLE_SESSION_FILES,
            {
                "session": "1",
                "hours": 24.0,
                "from_ref": "HEAD",
                "to_ref": "current",
                "repo_refs": {},
                "cache_key": repr(requested_key),
                "cache_key_data": requested_key,
            },
        )
    finally:
        webapp.control_server.stop()

    assert result["refreshing"] is True
    assert len(starts) == 1
    cache_key, target, session, infos, hours, from_ref, to_ref, repo_refs = starts[0]
    assert cache_key == requested_key
    assert target == webapp.refresh_session_files_payload_cache
    assert session == "1"
    assert infos == {"1": info}
    assert hours == 24.0
    assert from_ref == "HEAD"
    assert to_ref == "current"
    assert repo_refs == {}


def test_local_owner_tabber_refresh_request_starts_worker(no_control_socket, monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    starts = []
    monkeypatch.setattr(webapp.background_owner, "request_owner_refresh", lambda role, payload: {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False})
    monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: starts.append(True) or True)
    try:
        result = webapp.request_background_refresh(BACKGROUND_ROLE_TABBER_ACTIVITY, {"reason": "follower-refresh"})
    finally:
        webapp.control_server.stop()

    assert result["refreshing"] is True
    assert starts == [True]


def test_search_index_warm_takeover_loads_disk_without_rebuild_timing_regression(monkeypatch, tmp_path):
    (tmp_path / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    file_index.set_background_owner_done_notifier(None)
    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.build_now(
            tmp_path,
            filesystem.search.SEARCH_SKIP_DIRS,
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.set_background_owner_checker(lambda _role: True)
        monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("warm takeover must not rebuild search index")))

        started = time.perf_counter()
        index = file_index.ensure_index(
            tmp_path,
            filesystem.search.SEARCH_SKIP_DIRS,
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        elapsed = time.perf_counter() - started
    finally:
        file_index.set_background_owner_checker(None)
        file_index.set_background_owner_done_notifier(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert index.ready is True
    assert any(entry[2] == "target.py" for entry in index.entries)
    assert elapsed < 0.5


def test_search_index_follower_status_uses_manifest_without_full_json_timing_regression(monkeypatch, tmp_path):
    (tmp_path / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    file_index.set_background_owner_done_notifier(None)
    load_calls = []

    def slow_load_disk(*_args, **_kwargs):
        load_calls.append(True)
        time.sleep(0.35)
        raise AssertionError("follower status must not load the full search-index JSON")

    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.build_now(
            tmp_path,
            filesystem.search.SEARCH_SKIP_DIRS,
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.set_background_owner_checker(lambda _role: False)
        monkeypatch.setattr(file_index, "_load_disk", slow_load_disk)

        started = time.perf_counter()
        status = filesystem.index_status(str(tmp_path))
        elapsed = time.perf_counter() - started
    finally:
        file_index.set_background_owner_checker(None)
        file_index.set_background_owner_done_notifier(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert status["state"] == "follower"
    assert status["ready_elsewhere"] is True
    assert status["count"] >= 1
    assert load_calls == []
    assert elapsed < 0.2


def test_search_index_follower_large_sqlite_metadata_status_and_streaming_search_regression(monkeypatch, tmp_path):
    entry_count = 5000
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    file_index.set_background_owner_done_notifier(None)
    index = file_index.RootIndex(tmp_path)
    index.entries = [
        (str(tmp_path / f"dir-{number % 100}" / f"target-{number:05d}.py"), f"target-{number:05d}.py", f"dir-{number % 100}/target-{number:05d}.py", number + 1, number + 10)
        for number in range(entry_count)
    ]
    index.built_at = time.time()
    index.ready = True
    index.signature = file_index._skip_signature(filesystem.search.SEARCH_SKIP_DIRS, filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE)
    index.disk_entry_count = entry_count
    index.disk_metadata_ready = True
    index.truncated = False
    file_index._persist(
        index,
        filesystem.search.SEARCH_SKIP_DIRS,
        exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
    )
    load_calls = []

    def slow_load_disk(*_args, **_kwargs):
        load_calls.append(True)
        time.sleep(0.35)
        raise AssertionError("follower status/search must not deserialize SQLite entries")

    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.set_background_owner_checker(lambda _role: False)
        monkeypatch.setattr(file_index, "_load_disk", slow_load_disk)

        started = time.perf_counter()
        status = filesystem.index_status(str(tmp_path))
        payload = filesystem.search_files(str(tmp_path), query="target-04999", recursive=True)
        elapsed = time.perf_counter() - started
    finally:
        file_index.set_background_owner_checker(None)
        file_index.set_background_owner_done_notifier(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert status["state"] == "follower"
    assert status["ready_elsewhere"] is True
    assert status["count"] == entry_count
    assert payload["index_state"] == "follower-ready"
    assert [entry["relative_path"] for entry in payload["files"]] == ["dir-99/target-04999.py"]
    assert load_calls == []
    # The no-deserialization contract above is deterministic.  Keep a generous
    # wall-clock ceiling for an 8-core macOS runner persisting/searching 5,000
    # SQLite rows while the full gate may use the remaining cores.
    assert elapsed < 1.0


def test_search_index_follower_refetches_manifest_after_owner_build(monkeypatch, tmp_path):
    (tmp_path / "target.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.set_background_owner_checker(lambda _role: False)
        missing = filesystem.index_status(str(tmp_path))
        assert missing["state"] == "follower"
        assert missing["ready_elsewhere"] is False

        owner_index = file_index.RootIndex(tmp_path)
        owner_index.stop_event = threading.Event()
        file_index._run_build(
            owner_index,
            set(filesystem.search.SEARCH_SKIP_DIRS),
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        file_index.set_background_owner_checker(lambda _role: False)
        refreshed = filesystem.index_status(str(tmp_path))
        payload = filesystem.search_files(str(tmp_path), query="target", recursive=True)
    finally:
        file_index.set_background_owner_checker(None)
        file_index.set_background_owner_done_notifier(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert refreshed["state"] == "follower"
    assert refreshed["ready_elsewhere"] is True
    assert refreshed["count"] >= 1
    assert [entry["relative_path"] for entry in payload["files"]] == ["target.py"]


def test_only_owner_starts_search_index_walk_for_shared_root(monkeypatch, tmp_path):
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    walk_calls = []

    def fake_walk_root(root, *_args, **_kwargs):
        walk_calls.append(root)
        return [], False

    def synchronous_start_build(index, skip_dirs, exclude_path=None, exclude_signature=""):
        file_index.walk_root(index.root, skip_dirs, exclude_path=exclude_path)

    monkeypatch.setattr(file_index, "walk_root", fake_walk_root)
    monkeypatch.setattr(file_index, "_start_build", synchronous_start_build)
    try:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.set_background_owner_checker(lambda _role: True)
        file_index.ensure_index(
            tmp_path,
            filesystem.search.SEARCH_SKIP_DIRS,
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        assert walk_calls == [tmp_path]

        walk_calls.clear()
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()
        file_index.set_background_owner_checker(lambda _role: False)
        file_index.ensure_index(
            tmp_path,
            filesystem.search.SEARCH_SKIP_DIRS,
            exclude_path=filesystem.search.paths._path_is_secret,
            exclude_signature=filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE,
        )
        assert walk_calls == []
    finally:
        file_index.set_background_owner_checker(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()


def test_search_index_stale_ready_queries_do_not_start_rebuild_until_owner_scheduler_runs(monkeypatch, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()
        index = file_index.RootIndex(tmp_path)
        index.entries = [(str(target), target.name, target.name, target.stat().st_size, int(target.stat().st_mtime))]
        index.built_at = time.time() - file_index.INDEX_TTL_SECONDS - 1.0
        index.ready = True
        index.signature = file_index._skip_signature(filesystem.search.SEARCH_SKIP_DIRS, filesystem.search.SEARCH_SECRET_EXCLUDE_SIGNATURE)
        file_index._REGISTRY[str(tmp_path)] = index
    file_index.set_background_owner_checker(lambda _role: True)
    build_calls = []
    monkeypatch.setattr(file_index, "_start_build", lambda index, *_args, **_kwargs: build_calls.append(index.root))
    try:
        payload = filesystem.search_files(str(tmp_path), query="target", recursive=True)
        repeated = filesystem.search_files(str(tmp_path), query="target", recursive=True)
        assert build_calls == []
        file_index.schedule_refreshes()
    finally:
        file_index.set_background_owner_checker(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert [entry["relative_path"] for entry in payload["files"]] == ["target.py"]
    assert [entry["relative_path"] for entry in repeated["files"]] == ["target.py"]
    assert build_calls == [tmp_path]


def test_many_search_index_events_and_queries_schedule_one_refresh(monkeypatch, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()
    index = file_index.build_now(
        tmp_path,
        filesystem.SEARCH_SKIP_DIRS,
        exclude_path=filesystem.search.paths._path_is_secret,
        exclude_signature=filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE,
    )
    starts = []

    def record_start(root_index, *_args, **_kwargs):
        starts.append(root_index.root)
        with root_index.lock:
            root_index.building = True

    monkeypatch.setattr(file_index, "_start_build", record_start)
    file_index.set_background_owner_checker(lambda _role: True)
    try:
        for _ in range(20):
            file_index.mark_path_dirty(target)
            payload = filesystem.search_files(str(tmp_path), query="target", recursive=True)
            assert [entry["relative_path"] for entry in payload["files"]] == ["target.py"]
        assert starts == []
        for _ in range(20):
            file_index.schedule_refreshes()
    finally:
        file_index.set_background_owner_checker(None)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY.clear()

    assert starts == [tmp_path]
    assert len(index.dirty_paths) == 1


def test_owner_acquisition_starts_sampler_scheduler_even_when_statsd_is_down(no_control_socket, monkeypatch, tmp_path):
    """Never-hard-gate regression (2026-07-15): a stale statsd daemon held the socket at
    boot, ensure_started() failed once at owner acquisition, and the old one-shot gate
    skipped start_stats_metric_scheduler forever — YO!stats stayed blank until the next
    owner handoff. The scheduler must start regardless; its family loops already survive
    per-cycle statsd failures and self-heal when the daemon returns."""
    webapp = app_module.TmuxWebtermApp(["1"])
    owner = BackgroundOwnerRegistry(owner_dir=tmp_path / "owner", clock=lambda: 100.0)
    owner.owner = True
    owner.status = "owner"
    webapp.background_owner = owner
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_tabber_activity_cache", lambda self: None)
    monkeypatch.setattr(webapp.pricing_refresh_coordinator, "start_periodic", lambda: None)
    monkeypatch.setattr(app_module.StatsClient, "ensure_started", lambda self: False)
    scheduler_starts = []
    monkeypatch.setattr(app_module.TmuxWebtermApp, "start_stats_metric_scheduler", lambda self: scheduler_starts.append(True) or True)
    try:
        webapp.handle_background_owner_acquired({"last_transition": "acquired"})
        events = webapp.event_log.tail(limit=10)
    finally:
        webapp.control_server.stop()

    assert scheduler_starts == [True], "statsd being down must not gate the sampler scheduler"
    assert "statsd_sampler_unavailable" in {event["type"] for event in events}

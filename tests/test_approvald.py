from pathlib import Path
import os
import socket
import threading
import time

from yolomux_lib import approvald
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime as local_service_runtime


class FakeEventLog:
    def __init__(self):
        self.events = []

    def append(self, session, event_type, message, details, *, message_key="", message_params=None):
        event = {
            "session": session,
            "type": event_type,
            "message": message,
            "details": dict(details),
            "message_key": message_key,
            "message_params": dict(message_params or {}),
        }
        self.events.append(event)
        return event


class FakeWorker:
    start_owner = None
    created = []

    def __init__(self, target, **kwargs):
        self.target = target
        self.kwargs = kwargs
        self.stopped = False
        self.approved = 0
        self.blocked = 0
        self.pending = False
        FakeWorker.created.append(self)

    def start(self):
        if FakeWorker.start_owner is not None:
            return False, FakeWorker.start_owner
        return True, None

    def alive(self):
        return not self.stopped

    def stop(self):
        self.stopped = True
        return True

    def status(self):
        return {
            "target": self.target,
            "enabled": self.alive(),
            "approved": self.approved,
            "blocked": self.blocked,
            "last_action": f"watching {self.target}",
        }

    def has_pending_prompt(self):
        return self.pending


def service(tmp_path: Path, monkeypatch):
    FakeWorker.start_owner = None
    FakeWorker.created = []
    monkeypatch.setattr(approvald, "AutoApproveWorker", FakeWorker)
    item = approvald.PersistentApprovalService(tmp_path / "approvald.sock", idle_seconds=10.0)
    item.event_log = FakeEventLog()
    return item


def test_approvald_starts_statuses_and_stops_target_workers(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)

    response, _binary = item.handle({
        "action": "start_worker",
        "session": "6",
        "target": "%11",
        "owner_extra": {"control_socket": "/tmp/yolo.sock"},
        "dangerously_yolo": True,
    })
    status_response, _binary = item.handle({"action": "status_session", "session": "6"})
    pending_response, _binary = item.handle({"action": "has_pending_prompt", "target": "%11"})
    stop_response, _binary = item.handle({"action": "stop_session", "session": "6"})

    assert response["ok"] is True
    assert response["status"]["target"] == "%11"
    assert FakeWorker.created[0].kwargs["owner_extra"]["session"] == "6"
    assert FakeWorker.created[0].kwargs["dangerously_yolo"] is True
    assert status_response["statuses"][0]["enabled"] is True
    assert pending_response == {"ok": True, "pending": False}
    assert stop_response["ok"] is True
    assert item.records == {}


def test_approvald_exposes_common_profile_and_drain_actions(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)

    profile, _binary = item.handle({"action": "profile"})
    drain, _binary = item.handle({"action": "drain"})

    assert profile["ok"] is True
    assert profile["profile"]["service"] == "approvald"
    assert drain == {"ok": True, "drained": True, "targets": 0}


def test_approvald_aggregates_worker_recurring_work_without_target_names(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)
    response, _binary = item.handle({"action": "start_worker", "session": "6", "target": "%11"})
    assert response["ok"] is True
    FakeWorker.created[0].status = lambda: {
        "target": "%11", "enabled": True, "recurring_work": {
            "attempts": 5, "useful": 2, "no_change": 2, "failures": 1,
            "last_attempt_at": 20.0, "last_useful_at": 10.0,
        },
    }

    recurring = item.status()["recurring_work"]

    assert recurring == {
        "class": "sample", "cadence_seconds": approvald.approval_interval_seconds(), "demanded": True,
        "attempts": 5, "useful": 2, "no_change": 2, "failures": 1,
        "last_attempt_at": 20.0, "last_useful_at": 10.0,
    }
    assert "%11" not in str(recurring)


def test_approvald_reports_lock_owner_without_recording_duplicate_worker(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)
    FakeWorker.start_owner = {"pid": 123, "session": "6"}

    response, _binary = item.handle({"action": "start_worker", "session": "6", "target": "%11"})

    assert response["ok"] is False
    assert response["locked"] is True
    assert response["owner"] == {"pid": 123, "session": "6"}
    assert item.records == {}


def test_approvald_handler_failure_is_typed_and_the_service_stays_available(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)
    monkeypatch.setattr(item, "_start_worker", lambda _request: (_ for _ in ()).throw(FileNotFoundError("retired approval root")))
    worker = threading.Thread(target=item.run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 2.0
    while not item.socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    client = approvald.ApprovalClient(item.socket_path)
    try:
        failed = client.request({"action": "start_worker", "session": "6", "target": "%11"}, timeout=1.0)
        assert failed == {
            "ok": False,
            "error": "service request failed",
            "error_code": "handler_failed",
            "exception_type": "FileNotFoundError",
        }
        assert client.request({"action": "ping"}, timeout=1.0)["ok"] is True
    finally:
        client.request({"action": "shutdown"}, timeout=1.0)
        worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_approvald_event_callback_writes_session_event_with_target(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)
    response, _binary = item.handle({"action": "start_worker", "session": "6", "target": "%11"})
    assert response["ok"] is True

    FakeWorker.created[0].kwargs["event_callback"]("%11", "approval_approved", "approved", {"message_key": "events.message.yolo.approved"})

    assert item.event_log.events == [{
        "session": "6",
        "type": "approval_approved",
        "message": "approved",
        "details": {"target": "%11"},
        "message_key": "events.message.yolo.approved",
        "message_params": {},
    }]


def test_approvald_status_probe_does_not_reset_the_idle_clock(tmp_path):
    """``handle()`` must not restamp the idle clock on every dispatched RPC.

    The listener's ``on_client`` callback (wired in ``run()``) already excludes
    same-process connections before ``handle()`` runs; a redundant stamp inside
    ``handle()`` would defeat that exclusion for a same-process diagnostic call.
    """
    item = approvald.PersistentApprovalService(tmp_path / "approvald.sock", idle_seconds=5.0)

    assert not item.leases
    assert not item.records
    item.last_client_at = time.monotonic() - 6.0
    assert item.idle_due() is True, "baseline: no leases/records and idle_seconds elapsed must already report idle"

    item.last_client_at = time.monotonic() - 6.0
    response, _body = item.handle({"action": "status"})
    assert response["ok"] is True

    assert item.idle_due() is True, "a status probe reset the idle clock via handle()"


def test_approvald_external_status_probe_never_refreshes_demand_but_a_real_lease_does(tmp_path, monkeypatch):
    """Cross the real listener boundary (not a direct ``handle()`` call) to
    prove an external health/status poller with zero leases/records cannot
    refresh the idle deadline, while acquiring a real lease does.
    """
    socket_path = tmp_path / "approvald.sock"
    item = approvald.PersistentApprovalService(socket_path, idle_seconds=5.0)
    worker = threading.Thread(target=item.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 2.0
        while not item.socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert item.socket_path.exists()

        monkeypatch.setattr(local_service_runtime, "peer_pid", lambda _connection: os.getpid() + 999_000)

        item.last_client_at = time.monotonic() - 6.0
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(item.socket_path))
            envelope = rpc.new_envelope("approvald", "status", {"action": "status"})
            rpc.write_message(client, envelope, envelope.payload)
            _envelope, response, _binary, _legacy = rpc.read_message(client)
        assert response["ok"] is True
        assert item.idle_due() is True, "an external status probe with no lease/record refreshed the idle clock"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(item.socket_path))
            envelope = rpc.new_envelope("approvald", "lease", {"action": "lease", "client_pid": os.getpid()})
            rpc.write_message(client, envelope, envelope.payload)
            _envelope, response, _binary, _legacy = rpc.read_message(client)
        assert response["ok"] is True
        lease_id = str(response["lease_id"])
        assert item.idle_due() is False, "acquiring a real lease did not refresh demand"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(item.socket_path))
            envelope = rpc.new_envelope("approvald", "release", {"action": "release", "lease_id": lease_id})
            rpc.write_message(client, envelope, envelope.payload)
            _envelope, response, _binary, _legacy = rpc.read_message(client)
        assert response["ok"] is True
        item.last_client_at = time.monotonic() - 6.0
        assert item.idle_due() is True, "idle grace window did not elapse after the final lease released"
    finally:
        item.stop_event.set()
        worker.join(timeout=3.0)

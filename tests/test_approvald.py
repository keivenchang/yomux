from pathlib import Path

from yolomux_lib import approvald


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


def test_approvald_reports_lock_owner_without_recording_duplicate_worker(tmp_path, monkeypatch):
    item = service(tmp_path, monkeypatch)
    FakeWorker.start_owner = {"pid": 123, "session": "6"}

    response, _binary = item.handle({"action": "start_worker", "session": "6", "target": "%11"})

    assert response["ok"] is False
    assert response["locked"] is True
    assert response["owner"] == {"pid": 123, "session": "6"}
    assert item.records == {}


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

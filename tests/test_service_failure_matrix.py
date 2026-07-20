import os
import threading
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

from yolomux_lib import approvald
from yolomux_lib import jobd
from yolomux_lib.local_services.registry import LOCAL_SERVICE_BACKOFF_SECONDS
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec


SERVICE_FAILURE_MATRIX = [
    ("not_started", "registry", "missing socket reports unhealthy without fallback work"),
    ("connect_failure", "registry", "failed spawn records backoff and visible status"),
    ("crash_before_work", "registry", "exited child does not spin restarts"),
    ("crash_during_work", "jobd", "broken executor marks the job failed"),
    ("hang", "jobd", "timed-out running work keeps its slot until worker exit"),
    ("deadline", "jobd", "expired queued work fails before dispatch"),
    ("queue_full", "jobd", "saturated queue rejects new work"),
    ("stale_generation", "jobd", "newer generation supersedes stale queued work"),
    ("protocol_config_mismatch", "registry", "wrong protocol version stays unhealthy"),
    ("parent_death", "registry", "dead service record is removed before restart"),
    ("sleep_time_jump", "registry", "backoff uses monotonic deadlines"),
    ("approval_no_replay", "approvald", "shutdown/restart does not recreate target workers"),
    ("status_visibility", "all", "status/profile stays bounded and secret-free"),
]


class ExitedProcess:
    def poll(self):
        return 1


class FakeWorker:
    created = []

    def __init__(self, target, **kwargs):
        self.target = target
        self.kwargs = kwargs
        self.stopped = False
        FakeWorker.created.append(self)

    def start(self):
        return True, None

    def alive(self):
        return not self.stopped

    def stop(self):
        self.stopped = True
        return True

    def status(self):
        return {"target": self.target, "enabled": self.alive(), "approved": 0, "blocked": 0}

    def has_pending_prompt(self):
        return False


def test_service_failure_matrix_names_every_required_cell():
    required = {
        "not_started",
        "connect_failure",
        "crash_before_work",
        "crash_during_work",
        "hang",
        "deadline",
        "queue_full",
        "stale_generation",
        "protocol_config_mismatch",
        "parent_death",
        "sleep_time_jump",
        "approval_no_replay",
        "status_visibility",
    }

    assert {cell for cell, _service, _expectation in SERVICE_FAILURE_MATRIX} == required


def test_registry_failure_rows_are_visible_bounded_and_backed_off(tmp_path, monkeypatch):
    clock = [100.0]
    spawned = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("failingd", "missing.module", "failing.sock", 7),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)) or ExitedProcess(),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert registry.status()["healthy"] is False
    assert registry.ensure_started() is False
    first_status = registry.status()
    assert first_status["healthy"] is False
    assert first_status["failures"] == 1
    assert first_status["next_start_at"] == 100.0 + LOCAL_SERVICE_BACKOFF_SECONDS
    assert len(spawned) == 1
    assert registry.ensure_started() is False
    assert len(spawned) == 1
    clock[0] = first_status["next_start_at"] + 0.001
    assert registry.ensure_started() is False
    assert len(spawned) == 2

    record = registry.record_path
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"pid":999999999,"service":"failingd"}\n', encoding="utf-8")
    registry._remove_stale_record()
    assert record.exists() is False

    class VersionMismatchRegistry(LocalServiceRegistry):
        def _request(self, method, payload=None, timeout=0.2):
            if method in {"ping", "status"}:
                return {"ok": True, "pid": os.getpid(), "version": 999, "socket": str(self.socket_path)}
            return {}

    mismatch = VersionMismatchRegistry(tmp_path, LocalServiceSpec("mismatchd", "missing.module", "mismatch.sock", 7))
    assert mismatch.healthy() is False
    assert mismatch.status()["healthy"] is False


def test_registry_retires_incompatible_socket_owner_without_sidecar_record(tmp_path, monkeypatch):
    clock = [200.0]
    stale_alive = {424_242: True}
    shutdowns = []
    spawned = []

    monkeypatch.setattr("yolomux_lib.local_services.registry.pid_is_alive", lambda pid: stale_alive.get(pid, False))

    class RunningProcess:
        def poll(self):
            return None

    class MissingRecordMismatchRegistry(LocalServiceRegistry):
        def _request(self, method, payload=None, timeout=0.2):
            if stale_alive[424_242]:
                if method in {"ping", "status"}:
                    return {"ok": True, "pid": 424_242, "version": 6, "socket": str(self.socket_path)}
                if method == "shutdown":
                    shutdowns.append(method)
                    stale_alive[424_242] = False
                    return {"ok": True}
                return {}
            if spawned and method in {"ping", "status"}:
                return {"ok": True, "pid": 777, "version": 7, "socket": str(self.socket_path)}
            return {}

    registry = MissingRecordMismatchRegistry(
        tmp_path,
        LocalServiceSpec("mismatchd", "missing.module", "mismatch.sock", 7),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)) or RunningProcess(),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert registry.record_path.exists() is False
    assert registry.ensure_started() is True
    assert shutdowns == ["shutdown"]
    assert len(spawned) == 1
    assert registry.healthy() is True


def test_jobd_failure_rows_keep_status_and_do_not_fallback_to_main_work(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    stale = service._queue_record("text_facts", {"text": "old"}, "maintenance", 1, "same")
    service.latest_generation["same"] = 2
    service._supersede_stale_queued("same", 2)
    expired = service._queue_record("text_facts", {"text": "expired"}, "freshness", 1, "expired", deadline_at=time.monotonic() - 1.0)
    running = service._queue_record("text_facts", {"text": "hang"}, "interactive", 1, "hang", deadline_at=time.monotonic() - 1.0)
    running.status = "running"
    running.future = Future()
    waiting = service._queue_record("text_facts", {"text": "wait"}, "freshness", 1, "wait")
    crashed = service._queue_record("text_facts", {"text": "crash"}, "interactive", 1, "crash")
    crashed.status = "running"
    crashed.future = Future()
    crashed.future.set_exception(BrokenProcessPool("worker died"))

    class BrokenExecutor:
        def shutdown(self, **_kwargs):
            return None

    service.executor = BrokenExecutor()  # type: ignore[assignment]
    service._pump()
    for number in range(jobd.JOBD_MAX_QUEUE):
        queued = service._queue_record("text_facts", {"text": str(number)}, "freshness", number, f"queue-{number}")
        queued.status = "queued"
    client = jobd.JobClient(tmp_path / "missing-jobd.sock")

    assert stale.status == "superseded"
    assert expired.status == "timed_out"
    assert running.status == "timed_out"
    assert waiting.status == "queued"
    assert crashed.status == "failed"
    assert service.common_status()["last_failure"] == "worker crashed"
    assert service._submit({"task": "text_facts", "payload": {"text": "overflow"}}) == {"ok": False, "error": "queue full"}
    missing_response = client.submit("text_facts", {"text": "queued"})
    assert missing_response["ok"] is False
    assert missing_response["error"]
    running.future.set_result(b'{"bytes":4,"lines":1,"nonempty_lines":1}')
    service._pump()
    service._on_shutdown()


def test_approvald_live_socket_status_and_shutdown_do_not_replay_actions(tmp_path, monkeypatch):
    FakeWorker.created = []
    monkeypatch.setattr(approvald, "AutoApproveWorker", FakeWorker)
    service = approvald.PersistentApprovalService(tmp_path / "approvald.sock", idle_seconds=10.0)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = approvald.ApprovalClient(tmp_path / "approvald.sock")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not client.request({"action": "ping"}, timeout=0.1).get("ok"):
        time.sleep(0.01)

    handle, status = client.start_worker(session="6", target="%11", owner_extra={"control_socket": "/tmp/yolo.sock"}, dangerously_yolo=True)
    live_status = client.service_status()
    shutdown = client.request({"action": "shutdown"}, timeout=1.0)
    worker.join(timeout=2.0)
    restarted = approvald.PersistentApprovalService(tmp_path / "approvald.sock", idle_seconds=10.0)

    assert handle is not None
    assert status["target"] == "%11"
    assert live_status["target_count"] == 1
    assert shutdown == {"ok": True, "shutdown": True}
    assert worker.is_alive() is False
    assert FakeWorker.created[0].stopped is True
    assert restarted.status()["target_count"] == 0

import json
import threading
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from yolomux_lib import jobd
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime


def _wait_for_result(client: jobd.JobClient, job_id: str) -> dict:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        response = client.request({"action": "result", "job_id": job_id})
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        if job.get("status") in {"completed", "failed", "cancelled", "superseded"}:
            return response
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not settle")


def test_jobd_has_a_bounded_spawn_worker_pool_and_registered_tasks_only(tmp_path):
    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert client.registry.healthy() is True
    rejected = client.submit("not-registered", {"value": 1})
    first = client.submit("json_compact", {"z": 1, "a": [2]}, priority="interactive", generation=3, coalesce_key="fixture")
    duplicate = client.submit("json_compact", {"z": 1, "a": [2]}, priority="interactive", generation=3, coalesce_key="fixture")
    result = _wait_for_result(client, first["job"]["job_id"])
    status = client.request({"action": "status"})

    assert rejected == {"ok": False, "error": "unknown task"}
    assert first["ok"] is True and first["coalesced"] is False
    assert duplicate["ok"] is True and duplicate["coalesced"] is True
    assert result["job"]["status"] == "completed"
    assert result["job"]["result"] == {"a": [2], "z": 1}
    assert status["queues"] == {"interactive": 0, "freshness": 0, "maintenance": 0}
    assert status["cache"]["records"] == 1
    assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_transcript_view_returns_bounded_compact_facts_without_raw_text(tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-07-10T00:00:00Z", "payload": {"type": "user_message", "message": "Inspect this shared CPU path"}}),
                '{"timestamp":"2026-07-10T00:00:01Z",',
                json.dumps({"timestamp": "2026-07-10T00:00:02Z", "message": {"role": "assistant", "content": "Codex result", "stop_reason": "end_turn"}}),
                "\udcffnot-valid-utf8-is-replaced",
            ]
        ) + "\n",
        encoding="utf-8",
        errors="surrogatepass",
    )
    result = json.loads(
        jobd.run_registered_task(
            "transcript_view",
                json.dumps({"path": str(transcript), "line_limit": 100, "item_limit": 20, "kind": "codex"}).encode("utf-8"),
        )
    )

    assert result["items"] == [
        {"role": "user", "timestamp": "2026-07-10T00:00:00Z", "cwd": "", "text": "Inspect this shared CPU path"},
        {"role": "assistant", "timestamp": "2026-07-10T00:00:02Z", "cwd": "", "text": "Codex result"},
    ]
    assert result["compact_lines"] == []
    assert result["newest_timestamp"] == "2026-07-10T00:00:02+00:00"
    assert "text" not in result
    assert "Inspect this shared CPU path" not in json.dumps({key: value for key, value in result.items() if key != "items"})


def test_indexed_repo_discovery_runs_as_a_registered_worker_task(tmp_path):
    outer = tmp_path / "indexed"
    repo = outer / "group" / "repo"
    (repo / ".git").mkdir(parents=True)
    (outer / "ignored" / "node_modules" / "not-a-repo" / ".git").mkdir(parents=True)

    result = json.loads(jobd.run_registered_task(
        "indexed_repo_roots",
        json.dumps({"indexed_dirs": [str(outer)]}).encode("utf-8"),
    ))

    assert result == {"roots": [str(repo.resolve())]}


def test_transcript_view_rejects_relative_path_and_stays_bounded_on_sparse_large_file(tmp_path):
    with (tmp_path / "large.jsonl").open("wb") as handle:
        handle.truncate(100 * 1024 * 1024)
        handle.seek(-1024, 2)
        handle.write(b"\n" + json.dumps({"timestamp": "2026-07-10T00:00:00Z", "payload": {"type": "agent_message", "message": "tail-only"}}).encode("utf-8") + b"\n")
    large = tmp_path / "large.jsonl"
    result = jobd.run_registered_task(
        "transcript_view",
        json.dumps({"path": str(large), "line_limit": 4, "item_limit": 4}).encode("utf-8"),
    )

    assert len(result) < jobd.JOBD_MAX_RESULT_BYTES
    assert json.loads(result)["items"][-1]["text"] == "tail-only"
    try:
        jobd.run_registered_task("transcript_view", b'{"path":"relative.jsonl"}')
    except ValueError as exc:
        assert str(exc) == "transcript path must be absolute"
    else:
        raise AssertionError("relative transcript path must be rejected")


def test_transcript_view_rejects_traversal_and_symlink_paths_at_worker(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    transcript = root / "codex.jsonl"
    transcript.write_text(json.dumps({"timestamp": "2026-07-10T00:00:00Z", "payload": {"type": "user_message", "message": "safe"}}) + "\n", encoding="utf-8")

    for candidate, expected in (
        (root / ".." / "root" / "codex.jsonl", "transcript path must be normalized"),
        (tmp_path / "linked.jsonl", "transcript path must not be a symlink"),
    ):
        if not candidate.exists() and candidate.name == "linked.jsonl":
            candidate.symlink_to(transcript)
        try:
            jobd.run_registered_task("transcript_view", json.dumps({"path": str(candidate)}).encode("utf-8"))
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(f"{candidate} must be rejected")


def test_jobd_supersedes_stale_queued_generations_and_keeps_payloads_bounded(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    old_record = service._queue_record("text_facts", {"text": "old"}, "maintenance", 1, "same")
    service.latest_generation["same"] = 2
    service._supersede_stale_queued("same", 2)
    new_record = service._queue_record("text_facts", {"text": "new"}, "interactive", 2, "same")
    service._pump()

    assert old_record.status == "superseded"
    assert new_record.status == "running"
    assert service.latest_generation["same"] == 2
    assert len(json.dumps({"text": "x" * (jobd.JOBD_MAX_PAYLOAD_BYTES + 1)}).encode("utf-8")) > jobd.JOBD_MAX_PAYLOAD_BYTES
    oversized = service._submit({"task": "text_facts", "payload": {"text": "x" * (jobd.JOBD_MAX_PAYLOAD_BYTES + 1)}, "priority": "interactive"})
    assert oversized == {"ok": False, "error": "payload too large"}


def test_jobd_prevents_maintenance_starvation_and_times_out_before_worker_start(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    for number in range(jobd.JOBD_MAX_CONSECUTIVE_HIGH_PRIORITY + 1):
        service._queue_record("text_facts", {"text": f"interactive-{number}"}, "interactive", number, f"interactive-{number}")
    maintenance = service._queue_record("text_facts", {"text": "maintenance"}, "maintenance", 1, "maintenance")

    selected = [service._next_queued_record() for _ in range(jobd.JOBD_MAX_CONSECUTIVE_HIGH_PRIORITY + 1)]

    assert [record.priority for record in selected if record is not None] == ["interactive"] * jobd.JOBD_MAX_CONSECUTIVE_HIGH_PRIORITY + ["maintenance"]
    assert selected[-1] is maintenance

    expired = service._queue_record("text_facts", {"text": "expired"}, "freshness", 1, "expired", deadline_at=time.monotonic() - 1.0)
    service._pump()

    assert expired.status == "timed_out"
    assert expired.error == "deadline exceeded before execution"
    assert service._submit({"task": "text_facts", "payload": {"text": "late"}, "deadline_ms": jobd.JOBD_MAX_DEADLINE_MS + 1}) == {"ok": False, "error": "deadline too large"}


def test_jobd_rejects_malformed_worker_result_and_bounds_retained_records(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    malformed = service._queue_record("text_facts", {"text": "bad"}, "interactive", 1, "bad")
    malformed.status = "running"
    malformed.future = Future()
    malformed.future.set_result(b"not-json")
    secret_failure = service._queue_record("text_facts", {"text": "secret"}, "interactive", 2, "secret")
    secret_failure.status = "running"
    secret_failure.future = Future()
    secret_failure.future.set_exception(ValueError("token=super-secret-value"))
    for number in range(jobd.JOBD_MAX_RECORDS + 5):
        record = service._queue_record("text_facts", {"text": str(number)}, "maintenance", number, f"finished-{number}")
        record.status = "completed"
        record.completed_at = float(number + 1)
        record.result = b'{"ok":true}'

    service._pump()

    assert malformed.status == "failed"
    assert "Expecting value" in malformed.error
    assert secret_failure.status == "failed"
    assert secret_failure.error == "[redacted]"
    assert len(service.records) <= jobd.JOBD_MAX_RECORDS


def test_jobd_enforces_queue_saturation_deadlines_and_recovers_a_broken_executor(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    occupying = service._queue_record("text_facts", {"text": "active"}, "interactive", 1, "active")
    occupying.status = "running"
    occupying.future = Future()
    for number in range(jobd.JOBD_MAX_QUEUE):
        queued = service._queue_record("text_facts", {"text": str(number)}, "freshness", number, f"queue-{number}")
        queued.status = "queued"

    assert service._submit({"task": "text_facts", "payload": {"text": "overflow"}}) == {"ok": False, "error": "queue full"}
    assert service._submit({"task": "text_facts", "payload": {"text": "invalid"}, "deadline_ms": "tomorrow"}) == {"ok": False, "error": "invalid generation or deadline"}
    assert service._submit({"task": "text_facts", "payload": {"text": "negative"}, "deadline_ms": -1}) == {"ok": False, "error": "invalid deadline"}
    service.leases = {str(number): number for number in range(runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES)}
    lease_response, _binary = service.handle({"action": "lease", "client_pid": 123})
    assert lease_response == {"ok": False, "error": "too many clients", "leases": runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES, "version": jobd.JOBD_PROTOCOL_VERSION}

    broken = service._queue_record("text_facts", {"text": "crash"}, "interactive", 999, "crash")
    broken.status = "running"
    broken.future = Future()
    broken.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def shutdown(self, **_kwargs):
            return None

    service.executor = BrokenExecutor()  # type: ignore[assignment]
    service._pump()

    assert broken.status == "failed"
    assert broken.error == "worker crashed"
    assert service.executor is None


def test_jobd_clients_share_one_registry_and_coalesce_across_ports(tmp_path):
    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    first = jobd.JobClient(socket_path)
    second = jobd.JobClient(socket_path)

    deadline = time.monotonic() + 2.0
    while not first.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    first_submission = first.submit("json_compact", {"z": 1, "a": 2}, priority="interactive", generation=7, coalesce_key="two-ports")
    second_submission = second.submit("json_compact", {"z": 1, "a": 2}, priority="interactive", generation=7, coalesce_key="two-ports")

    expected_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-jobd")
    assert first.registry.socket_path == second.registry.socket_path == expected_socket_path
    assert first.registry.spec.name == second.registry.spec.name == "jobd"
    assert first_submission["coalesced"] is False
    assert second_submission["coalesced"] is True
    assert second_submission["job"]["job_id"] == first_submission["job"]["job_id"]
    assert _wait_for_result(first, first_submission["job"]["job_id"])["job"]["result"] == {"a": 2, "z": 1}
    assert first.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_jobd_submit_never_creates_a_process_in_the_request_path(tmp_path, monkeypatch):
    client = jobd.JobClient(tmp_path / "jobd.sock")
    calls = []

    def unexpected_start():
        raise AssertionError("submit must not create jobd")

    monkeypatch.setattr(client, "ensure_started", unexpected_start)
    monkeypatch.setattr(client, "request", lambda payload: calls.append(payload) or {"ok": False, "error": "jobd unavailable"})

    assert client.submit("text_facts", {"text": "queued"}) == {"ok": False, "error": "jobd unavailable"}
    assert calls == [{"action": "submit", "task": "text_facts", "payload": {"text": "queued"}, "priority": "freshness", "generation": 0, "coalesce_key": "", "deadline_ms": 0}]


def test_jobd_timed_out_running_work_keeps_its_slot_and_recovers_after_worker_exit(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    timed_out = service._queue_record("text_facts", {"text": "slow"}, "interactive", 1, "slow", deadline_at=time.monotonic() - 1.0)
    timed_out.status = "running"
    timed_out.future = Future()
    waiting = service._queue_record("text_facts", {"text": "wait"}, "freshness", 1, "wait")

    service._pump()

    assert timed_out.status == "timed_out"
    assert waiting.status == "queued"
    timed_out.future.set_result(b'{"bytes":4,"lines":1,"nonempty_lines":1}')
    service._pump()

    assert waiting.status == "running"


def test_jobd_cancels_queued_work_without_dispatching_it(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    blocking = service._queue_record("text_facts", {"text": "active"}, "interactive", 1, "active")
    blocking.status = "running"
    blocking.future = Future()
    queued = service._queue_record("text_facts", {"text": "cancel"}, "freshness", 1, "cancel")

    response, _binary = service.handle({"action": "cancel", "job_id": queued.job_id})
    blocking.future.set_result(b'{"bytes":6,"lines":1,"nonempty_lines":1}')
    service._pump()

    assert response["job"]["status"] == "cancelled"
    assert queued.status == "cancelled"
    assert queued.future is None


def test_jobd_respawns_after_worker_crash_and_restart_accepts_new_work(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    crashed = service._queue_record("text_facts", {"text": "crash"}, "interactive", 1, "crashed")
    crashed.status = "running"
    crashed.future = Future()
    crashed.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def shutdown(self, **_kwargs):
            return None

    service.executor = BrokenExecutor()  # type: ignore[assignment]
    service._pump()
    recovered = service._queue_record("json_compact", {"z": 1, "a": 2}, "interactive", 2, "recovered")
    deadline = time.monotonic() + 5.0
    while recovered.status not in {"completed", "failed"} and time.monotonic() < deadline:
        service._pump()
        time.sleep(0.02)
    service._on_shutdown()

    assert crashed.status == "failed"
    assert recovered.status == "completed"
    assert json.loads(recovered.result) == {"a": 2, "z": 1}


def test_jobd_task_registry_generation_is_independent_from_transport_version():
    assert jobd.JOBD_PROTOCOL_VERSION == 2
    assert jobd.JOBD_PROTOCOL_VERSION != jobd.LOCAL_RPC_VERSION

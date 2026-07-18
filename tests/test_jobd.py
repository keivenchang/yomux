import json
import os
import threading
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from pathlib import Path

import pytest

from yolomux_lib import jobd
from yolomux_lib import session_files
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import TmuxPaneInfo
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime

from _git_helpers import git
from _git_helpers import init_repo


def _session_info_json(session, repo, transcript=None, kind="claude"):
    pane = TmuxPaneInfo(
        session=session, window="0", pane="0", pane_id="%1", target=f"{session}:0.0",
        current_path=str(repo), command="zsh", active=True, window_active=True, title="", pid=11,
    )
    agents = []
    if transcript is not None:
        agents.append(AgentInfo(
            session=session, kind=kind, pid=1, pane_target="%1", command=kind, cwd=str(repo),
            status=None, session_id=None, transcript=str(transcript), error=None,
        ))
    return asdict(SessionInfo(session=session, panes=[pane], selected_pane=pane, agents=agents))


def _init_repo_with_commit(repo):
    repo.mkdir()
    init_repo(repo)
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")


def test_session_files_view_task_returns_bounded_payload_without_raw_transcript_text(tmp_path):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 2\n", encoding="utf-8")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "__raw_sentinel__": "SENTINEL_MUST_NOT_LEAK",
            "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "one.py"}}]},
        }) + "\n",
        encoding="utf-8",
    )
    payload = {
        "session": "s1",
        "infos": {"s1": _session_info_json("s1", repo, transcript)},
        "hours": 24.0,
        "include_cross_session_attribution": False,
    }
    result_bytes = jobd.run_registered_task("session_files_view", json.dumps(payload).encode("utf-8"))
    assert len(result_bytes) <= jobd.JOBD_MAX_RESULT_BYTES
    result = json.loads(result_bytes.decode("utf-8"))
    assert set(result) >= {"payload", "status", "truncated"}
    assert result["status"] == 200
    assert result["truncated"] is False
    # The git-tracked modification is attributed to the editing agent.
    entries = {Path(item["path"]).name: item for item in result["payload"]["files"]}
    assert "one.py" in entries
    assert entries["one.py"]["agents"] == ["claude"]
    # The bounded product carries structured facts only; no raw transcript bytes ever cross the wire.
    assert "SENTINEL_MUST_NOT_LEAK" not in result_bytes.decode("utf-8")
    assert "tool_use" not in result_bytes.decode("utf-8")


def test_session_files_view_task_rejects_malformed_or_oversized_payload():
    with pytest.raises(ValueError):
        jobd.run_registered_task("session_files_view", json.dumps({"infos": "not-an-object"}).encode("utf-8"))
    # infos over the bounded session limit is rejected before any git/discovery work runs.
    too_many = {str(index): {} for index in range(session_files.SESSION_FILES_VIEW_MAX_SESSIONS + 1)}
    with pytest.raises(ValueError):
        jobd.run_registered_task("session_files_view", json.dumps({"infos": too_many}).encode("utf-8"))
    # A payload larger than the broker's input ceiling is rejected by run_registered_task itself.
    with pytest.raises(ValueError):
        jobd.run_registered_task("session_files_view", b"{" + b" " * (jobd.JOBD_MAX_PAYLOAD_BYTES + 1))


def test_session_files_view_memoizes_git_snapshot_per_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 3\n", encoding="utf-8")
    calls: list[str] = []
    real_build = session_files.build_git_snapshot

    def counting_build(path, from_ref=None, to_ref=None):
        calls.append(str(path))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files, "build_git_snapshot", counting_build)
    # Two sessions whose panes sit in the SAME repo, cross-session pass: the memoizing provider must
    # build that repo's git snapshot exactly once for the whole task.
    payload = {
        "session": "",
        "infos": {
            "a": _session_info_json("a", repo),
            "b": _session_info_json("b", repo),
        },
        "hours": 24.0,
        "include_cross_session_attribution": True,
    }
    result = session_files.session_files_view_result(payload, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
    assert result["status"] == 200
    assert len(calls) == 1


def test_session_files_view_bounding_trims_files_and_sets_truncated_flag():
    payload = {"files": [{"path": f"/repo/file{index}.py", "blob": "y" * 256} for index in range(200)], "repos": []}
    truncated = session_files.bound_session_files_view_payload(payload, 4096)
    assert truncated is True
    assert len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) <= 4096
    assert len(payload["files"]) < 200


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


def test_transcript_view_reports_file_identity_separate_from_byte_generation(tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"timestamp": "2026-07-10T00:00:00Z", "payload": {"type": "user_message", "message": "identity"}}) + "\n", encoding="utf-8")
    stat = transcript.stat()
    result = json.loads(jobd.run_registered_task("transcript_view", json.dumps({"path": str(transcript), "line_limit": 100, "item_limit": 20}).encode("utf-8")))

    # The device+inode identity is a separate field so a replaced inode cannot satisfy an old key,
    # while the existing [mtime_ns, size] generation shape is preserved for existing consumers.
    assert result["identity"] == [stat.st_dev, stat.st_ino]
    assert result["generation"] == [stat.st_mtime_ns, stat.st_size]
    assert len(result["generation"]) == 2
    # A file whose device+inode differs (a replaced file) would report a different identity, so a
    # consumer keyed to the original identity rejects it even if [mtime, size] coincidentally match.
    assert result["identity"] != [stat.st_dev + 1, stat.st_ino + 1]


def test_two_ports_coalesce_one_worker_run_and_read_identical_product_bytes(tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"timestamp": "2026-07-10T00:00:00Z", "payload": {"type": "user_message", "message": "shared product"}}) + "\n", encoding="utf-8")
    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    port_a = jobd.JobClient(socket_path)
    port_b = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not port_a.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    payload = {"path": str(transcript), "line_limit": 100, "item_limit": 20, "kind": "codex"}
    product_key = "transcript:v1:shared"
    first = port_a.submit("transcript_view", payload, generation=1, coalesce_key=product_key)
    second = port_b.submit("transcript_view", payload, generation=1, coalesce_key=product_key)
    _wait_for_result(port_a, first["job"]["job_id"])
    meta_a, body_a = port_a.product(product_key)
    meta_b, body_b = port_b.product(product_key)
    status = port_a.request({"action": "status"})
    port_a.request({"action": "shutdown"})
    worker.join(timeout=2.0)

    assert first["coalesced"] is False
    # The second port's identical product key coalesces onto the first job: one worker run only.
    assert second["coalesced"] is True
    assert status["product_counters"]["transcript_view"]["completed"] == 1
    assert meta_a["state"] == "ready" and meta_b["state"] == "ready"
    # Both ports read byte-identical last-known-good product bytes for the shared key.
    assert body_a == body_b and body_a != b""
    assert json.loads(body_a)["items"][-1]["text"] == "shared product"


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
    service.leases = {
        str(number): os.getpid()
        for number in range(runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES)
    }
    lease_response, _binary = service.handle({"action": "lease", "client_pid": os.getpid()})
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
    # v3 added the materialized-product layer (product RPC + last-known-good store + counters).
    # v4 registered the `session_files_view` task; the version fence retires a v3 daemon that lacks it.
    assert jobd.JOBD_PROTOCOL_VERSION == 4
    assert "session_files_view" in jobd.REGISTERED_TASKS
    assert jobd.JOBD_PROTOCOL_VERSION != jobd.LOCAL_RPC_VERSION


def test_jobd_product_serves_last_known_good_bytes_across_the_state_taxonomy(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)

    # none: nothing produced, nothing in flight.
    meta, body = service._product({"coalesce_key": "k"})
    assert meta["state"] == "none" and body == b""

    # pending: a first-generation job is building, no product yet.
    record = service._queue_record("json_compact", {"a": 1}, "freshness", 1, "k")
    record.status = "running"
    record.future = Future()
    meta, body = service._product({"coalesce_key": "k"})
    assert meta["state"] == "pending" and body == b""

    # ready: the job completes and its bytes become the last-known-good product.
    record.future.set_result(b'{"a":1}')
    service._pump()
    meta, body = service._product({"coalesce_key": "k"})
    assert meta["state"] == "ready" and meta["generation"] == 1 and body == b'{"a":1}'

    # stale: a newer generation is building; the prior complete bytes are still served.
    newer = service._queue_record("json_compact", {"a": 2}, "freshness", 2, "k")
    newer.status = "running"
    newer.future = Future()
    service.latest_generation["k"] = 2
    meta, body = service._product({"coalesce_key": "k"})
    assert meta["state"] == "stale" and meta["generation"] == 1 and body == b'{"a":1}'


def test_jobd_older_or_failed_completion_cannot_overwrite_a_newer_product(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    older = service._queue_record("json_compact", {"gen": 1}, "freshness", 1, "k")
    older.status = "running"
    older.future = Future()
    newer = service._queue_record("json_compact", {"gen": 2}, "freshness", 2, "k")
    newer.status = "running"
    newer.future = Future()
    service.latest_generation["k"] = 2

    # The newer generation completes first and becomes the product.
    newer.future.set_result(b'{"gen":2}')
    service._pump()
    assert service.latest_product["k"][0] == 2

    # A slow OLDER-generation completion must not replace the newer complete product.
    older.future.set_result(b'{"gen":1}')
    service._pump()
    assert service.latest_product["k"][0] == 2
    assert json.loads(service.latest_product["k"][1]) == {"gen": 2}

    # A failed refresh must not replace it either.
    failing = service._queue_record("json_compact", {"gen": 3}, "freshness", 3, "k")
    failing.status = "running"
    failing.future = Future()
    failing.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def shutdown(self, **_kwargs):
            return None

    service.executor = BrokenExecutor()  # type: ignore[assignment]
    service._pump()
    assert failing.status == "failed"
    assert json.loads(service.latest_product["k"][1]) == {"gen": 2}


def test_jobd_product_counters_track_accepted_coalesced_superseded_and_completed(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    # Occupy the only worker slot so submitted jobs stay queued (no real subprocess dispatch).
    block = service._queue_record("json_compact", {"x": 1}, "interactive", 1, "block")
    block.status = "running"
    block.future = Future()

    accepted = service._submit({"action": "submit", "task": "json_compact", "payload": {"a": 1}, "priority": "freshness", "generation": 1, "coalesce_key": "k"})
    assert accepted["coalesced"] is False
    coalesced = service._submit({"action": "submit", "task": "json_compact", "payload": {"a": 1}, "priority": "freshness", "generation": 1, "coalesce_key": "k"})
    assert coalesced["coalesced"] is True
    service._submit({"action": "submit", "task": "json_compact", "payload": {"a": 2}, "priority": "freshness", "generation": 2, "coalesce_key": "k"})

    counters = service.product_counters["json_compact"]
    assert counters["accepted"] == 2  # the block record is queued directly (not via _submit); k gen1 + k gen2
    assert counters["coalesced"] == 1
    assert counters["superseded"] == 1

    done = service._queue_record("json_compact", {"a": 9}, "freshness", 9, "done")
    done.status = "running"
    done.future = Future()
    done.future.set_result(b'{"a":9}')
    service._pump()
    assert service.product_counters["json_compact"]["completed"] == 1
    assert service.common_status()["product_counters"]["json_compact"]["completed"] == 1


def test_jobd_tracks_per_task_runtime_count_total_and_max(tmp_path, monkeypatch):
    # Per-product runtime totals/maxima (checkbox 10): pure execution duration, excluding queue
    # wait, tracked per task name and surfaced through common_status/runtime_status.
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    clock_state = {"now": 100.0}
    monkeypatch.setattr(jobd.time, "monotonic", lambda: clock_state["now"])

    fast = service._queue_record("json_compact", {"a": 1}, "freshness", 1, "fast")
    fast.status = "running"
    fast.running_started_at = clock_state["now"]  # 100.0
    fast.future = Future()
    fast.future.set_result(b'{"a":1}')
    clock_state["now"] = 100.05  # 50ms of pure execution
    service._pump()

    slow = service._queue_record("json_compact", {"a": 2}, "freshness", 2, "slow")
    slow.status = "running"
    slow.running_started_at = clock_state["now"]  # 100.05
    slow.future = Future()
    slow.future.set_result(b'{"a":2}')
    clock_state["now"] = 100.25  # 200ms of pure execution
    service._pump()

    stats = service.product_runtime_ms["json_compact"]
    assert stats["count"] == 2
    assert stats["max_ms"] == pytest.approx(200.0, abs=1.0)
    assert stats["total_ms"] == pytest.approx(250.0, abs=1.0)

    status_stats = service.common_status()["product_runtime_ms"]["json_compact"]
    assert status_stats["count"] == 2
    assert status_stats["avg_ms"] == pytest.approx(125.0, abs=1.0)


def test_jobd_product_store_evicts_oldest_completion_past_the_bound(tmp_path):
    # The last-known-good product store is bounded independently of the job-record
    # ring (removal/tombstone behavior): once JOBD_MAX_PRODUCTS distinct coalesce
    # keys have a stored product, completing one more evicts the OLDEST-STORED
    # entry so the store cannot grow unbounded across many distinct products.
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    original_max = jobd.JOBD_MAX_PRODUCTS
    try:
        jobd.JOBD_MAX_PRODUCTS = 3
        for index in range(3):
            record = service._queue_record("json_compact", {"i": index}, "freshness", 1, f"key-{index}")
            record.status = "running"
            record.future = Future()
            record.future.set_result(f'{{"i":{index}}}'.encode())
            service._pump()
        assert set(service.latest_product) == {"key-0", "key-1", "key-2"}

        overflow = service._queue_record("json_compact", {"i": 3}, "freshness", 1, "key-3")
        overflow.status = "running"
        overflow.future = Future()
        overflow.future.set_result(b'{"i":3}')
        service._pump()

        assert len(service.latest_product) == 3
        assert "key-0" not in service.latest_product  # the oldest-stored entry was evicted
        assert "key-3" in service.latest_product
        meta, body = service._product({"coalesce_key": "key-0"})
        assert meta["state"] == "none" and body == b""  # a tombstoned key reports honestly, not stale data
    finally:
        jobd.JOBD_MAX_PRODUCTS = original_max

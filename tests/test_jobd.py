import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from pathlib import Path

import pytest

from yolomux_lib import activity_summary
from yolomux_lib import app as app_module
from yolomux_lib import github_client
from yolomux_lib import jobd
from yolomux_lib import metadata as metadata_module
from yolomux_lib import session_files
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import TmuxPaneInfo
from yolomux_lib.filesystem import FilesystemError
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


def _build_repository_snapshot_in_child(repo_text, state_dir_text, counter_text, ready, in_builder, release):
    """Exercise the private cache from an independent spawned worker process.

    Readiness is signalled in two decoupled phases so the parent's single-flight oracle never
    hangs on cold fork+import latency:

    * ``ready`` fires once this process has spawned and imported ``yolomux_lib`` and is about to
      enter ``cached_repository_snapshot``.  Everything before this point -- the fork and the cold
      import -- scales unboundedly with host load, so the parent waits on ``ready`` conditioned on
      this process staying alive rather than against a wall clock.
    * ``in_builder`` fires from inside the single-flight builder, i.e. once this worker has won the
      cross-process ``file_lock``.  The parent bounds only this post-readiness product step, which
      is load-independent, so a stall here is a genuine lock deadlock rather than a slow start.

    ``YOLOMUX_TEST_WORKER_COLD_DELAY`` is an opt-in load-simulation seam (default ``0`` -- inert in
    every normal run): it injects a deterministic pre-readiness delay so the acceptance harness can
    reproduce arbitrary fork+import latency without depending on real host contention.
    """
    session_files.common.STATE_DIR = Path(state_dir_text)
    cold_delay = float(os.environ.get("YOLOMUX_TEST_WORKER_COLD_DELAY", "0") or "0")
    if cold_delay:
        time.sleep(cold_delay)
    ready.set()

    def build(_repo, _from_ref, _to_ref):
        with Path(counter_text).open("a", encoding="utf-8") as handle:
            handle.write("build\n")
        in_builder.set()
        assert release.wait(timeout=10.0), "builder was never released by the parent"
        return {"statuses": {}}

    session_files.cached_repository_snapshot(Path(repo_text), None, None, 9, build)


def _await_worker_ready(process, ready):
    """Block until a spawned worker signals readiness, tolerating unbounded cold-start latency.

    This absorbs fork+cold-import time (which scales with host load) without a patience budget: it
    waits as long as the worker is alive and fails immediately only if the worker crashed before
    reaching the builder -- a genuine defect, not a slow start.
    """
    while not ready.wait(timeout=0.2):
        if not process.is_alive():
            raise AssertionError(f"worker exited before readiness (exitcode={process.exitcode})")


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
    assert set(result) >= {"payload", "status", "truncated", "profile"}
    assert result["status"] == 200
    assert result["truncated"] is False
    # The git-tracked modification is attributed to the editing agent.
    entries = {Path(item["path"]).name: item for item in result["payload"]["files"]}
    assert "one.py" in entries
    assert entries["one.py"]["agents"] == ["claude"]
    # The bounded product carries structured facts only; no raw transcript bytes ever cross the wire.
    assert "SENTINEL_MUST_NOT_LEAK" not in result_bytes.decode("utf-8")
    assert "tool_use" not in result_bytes.decode("utf-8")
    assert set(result["profile"]) == {"phases", "work", "source"}
    assert set(result["profile"]["phases"]) <= session_files.SESSION_FILES_VIEW_PHASES
    assert result["profile"]["work"]["sessions"] == 1
    assert result["profile"]["work"]["git_snapshots"] == 1


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


def test_jobd_product_exposes_uniform_framing_metadata(tmp_path):
    server = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    body = b'{"retained":true}'
    product = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": "af7e0e60ef1cb6299c9cf719e651eac394d2005ba01ea0028f5b8c88c6ef992d",
        "disposition": "inline",
        "filename": "",
    }
    server.product_store.store_inline(key="framing-key", generation=1, body=body, product=product, schedule={}, stored_at=123.0)

    response, returned = server._product({"coalesce_key": "framing-key"})

    assert returned == body
    assert response["product"] == {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": "af7e0e60ef1cb6299c9cf719e651eac394d2005ba01ea0028f5b8c88c6ef992d",
        "disposition": "inline",
        "filename": "",
    }


def test_jobd_source_epoch_is_opaque_and_per_broker_start(tmp_path):
    first = jobd.PersistentJobBroker(tmp_path / "first.sock", workers=1)
    second = jobd.PersistentJobBroker(tmp_path / "second.sock", workers=1)

    first_epoch = first.common_status()["source_epoch"]

    assert isinstance(first_epoch, str)
    assert len(first_epoch) == 32
    assert first_epoch == first.common_status()["source_epoch"]
    assert first_epoch != second.common_status()["source_epoch"]


def test_registered_task_result_preserves_opaque_body_and_metadata(monkeypatch):
    body = b"\x00opaque\xff"
    product = {
        "format": "opaque_bytes",
        "content_type": "application/octet-stream",
        "length": len(body),
        "sha256": "ec37a96514e60e745734819845f20428b2244b2a190abf32227302b706328122",
        "disposition": "attachment",
        "filename": "payload.bin",
    }
    monkeypatch.setitem(jobd.REGISTERED_TASKS, "opaque-test", lambda _payload: jobd.JobdTaskResult(body, product))

    result = jobd.run_registered_task_result("opaque-test", b"{}")

    assert result.body == body
    assert result.product == product


def _fs_descriptor(**fields):
    """One filesystem job descriptor carrying this process's captured access policy.

    The shared daemon refuses a descriptor without one, so tests must build them the way a real
    accepting server does rather than hand-rolling `{"op": ..., "path": ...}`.
    """
    return {**fields, jobd.filesystem.FS_ACCESS_POLICY_FIELD: jobd.filesystem.access_policy_descriptor()}


def _fs_batch_payload(**fields):
    """One filesystem batch payload carrying this process's captured access policy."""
    return {**fields, jobd.filesystem.FS_ACCESS_POLICY_FIELD: jobd.filesystem.access_policy_descriptor()}


def test_filesystem_operation_task_reads_in_jobd(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("jobd owns this read\n", encoding="utf-8")

    result = json.loads(jobd.run_registered_task("filesystem_operation", json.dumps(_fs_descriptor(
        op="read",
        path=str(path),
        args={},
    )).encode("utf-8")))

    assert result["content"] == "jobd owns this read\n"


def test_filesystem_operation_task_dispatches_git_history_and_commit(tmp_path):
    repo = tmp_path / "history"
    _init_repo_with_commit(repo)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    history = json.loads(jobd.run_registered_task("filesystem_operation", json.dumps(_fs_descriptor(
        op="git_history",
        path=str(repo),
        args={"limit": 1, "cursor": ""},
    )).encode("utf-8")))
    detail = json.loads(jobd.run_registered_task("filesystem_operation", json.dumps(_fs_descriptor(
        op="git_commit",
        path=str(repo),
        args={"commit": head, "head": head},
    )).encode("utf-8")))

    assert history["head"] == head
    assert [item["sha"] for item in history["commits"]] == [head]
    assert detail["sha"] == head
    assert detail["from_ref"]
    assert [item["path"] for item in detail["files"]] == ["one.py"]


def test_filesystem_operation_task_preserves_raw_bytes(tmp_path):
    path = tmp_path / "payload.bin"
    body = b"\x00raw\xff"
    path.write_bytes(body)

    result = jobd.run_registered_task_result("filesystem_operation", json.dumps(_fs_descriptor(
        op="raw",
        path=str(path),
        args={"download": True},
    )).encode("utf-8"))

    assert isinstance(result, jobd.JobdArtifactResult)
    assert (jobd.artifact_root() / result.basename).read_bytes() == body
    (jobd.artifact_root() / result.basename).unlink()
    assert result.product["format"] == "opaque_bytes"
    assert result.product["disposition"] == "attachment"
    assert result.product["filename"] == "payload.bin"


def test_filesystem_operation_task_preserves_raw_bytes_above_generic_json_budget(tmp_path):
    path = tmp_path / "preview.png"
    body = b"\x89PNG\r\n\x1a\n" + (b"x" * (jobd.JOBD_MAX_RESULT_BYTES + 1024))
    assert len(body) < jobd.LOCAL_RPC_MAX_BINARY_BYTES
    path.write_bytes(body)

    result = jobd.run_registered_task_result("filesystem_operation", json.dumps(_fs_descriptor(
        op="raw",
        path=str(path),
        args={"max_bytes": len(body) + 1},
    )).encode("utf-8"))

    assert isinstance(result, jobd.JobdArtifactResult)
    assert (jobd.artifact_root() / result.basename).read_bytes() == body
    (jobd.artifact_root() / result.basename).unlink()
    assert result.product["format"] == "opaque_bytes"
    assert result.product["content_type"] == "image/png"
    assert result.product["disposition"] == "inline"


def test_filesystem_operation_task_frames_html_preview_as_opaque_bytes(tmp_path):
    path = tmp_path / "preview.html"
    path.write_text("<h1>ok</h1><script>window.answer = 42;</script>\n", encoding="utf-8")

    result = jobd.run_registered_task_result("filesystem_operation", json.dumps(_fs_descriptor(
        op="html_preview",
        path=str(path),
        args={"locale": "he"},
    )).encode("utf-8"))

    assert result.product["format"] == "opaque_bytes"
    assert result.product["content_type"] == "text/html; charset=utf-8"
    assert result.product["disposition"] == "inline"
    assert result.product["filename"] == ""
    document = result.body.decode("utf-8")
    assert '<html lang="he" dir="rtl">' in document
    assert 'sandbox="allow-scripts allow-forms allow-popups"' in document
    assert "allow-same-origin" not in document
    assert "&lt;script&gt;window.answer = 42;&lt;/script&gt;" in document


def test_jobd_broker_past_its_idle_window_stays_up_while_a_client_lease_is_held(tmp_path):
    """A held client lease pins the broker across a slow interaction; without one it idle-exits.

    This is the ownership seam behind the full-gate e2e differ flake
    (`test_e2e_browser_harness.py::test_direct_internal_differ_fixture_path_reaches_terminal_state`):
    the broker is per-test isolated, but its socket is removed when it decides it is idle, and a
    saturated gate can stretch the gap between two `/api/fs/batch` calls past the idle window while
    the browser boots and clicks. `_idle_should_stop` and the `shutdown_if_idle` action are the
    exact guards that keep the broker alive -- but ONLY while a lease is held. Pin both directions
    deterministically by forcing the clock past the window rather than by waiting under load.
    """
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", idle_seconds=5.0, workers=1)
    # Force the broker well past its idle window with no queued or running work.
    broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
    assert not broker.leases and broker._queued_count() == 0
    # With no client holding it, an idle broker is free to remove its own socket.
    assert broker._idle_should_stop() is True
    idle_response, _ = broker.handle({"action": "shutdown_if_idle"})
    assert idle_response == {"ok": True, "shutdown": True}
    # A held client lease is what a request in flight leaves behind; it must veto both guards, so
    # the broker cannot vanish out from under a slow browser between two filesystem calls.
    broker.stop_event = multiprocessing.get_context("spawn").Event()
    broker.leases["lease-1"] = {"client_pid": os.getpid()}
    assert broker._idle_should_stop() is False
    leased_response, _ = broker.handle({"action": "shutdown_if_idle"})
    assert leased_response == {"ok": True, "shutdown": False, "leases": 1}
    assert broker.stop_event.is_set() is False


def test_jobd_idle_reaps_a_dead_client_lease_before_deciding_to_stay_up(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", idle_seconds=5.0, workers=1)
    broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
    broker.leases["dead-client"] = runtime.current_host_identity().process_record_fields(
        pid=999_999_999,
        start_identity="proc:1",
    )

    assert broker._idle_should_stop() is True
    assert broker.leases == {}


def test_jobd_status_reaps_dead_client_leases_for_startup_reconciliation(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", idle_seconds=5.0, workers=1)
    broker.leases["dead-client"] = runtime.current_host_identity().process_record_fields(
        pid=999_999_999,
        start_identity="proc:1",
    )

    status = broker.common_status()

    assert status["clients"] == 0
    assert broker.leases == {}


@pytest.mark.gate_serial
def test_fs_batch_completion_holds_a_jobd_lease_across_the_broker_idle_window(tmp_path, monkeypatch):
    """The fs-batch/differ completion worker pins the broker with a client lease while it polls.

    W15 #4 root cause: under a saturated gate the completion worker's product poll is starved past
    the broker's idle window, so between two ``/api/fs/batch`` calls the broker removes its own
    socket, the next relay fails with ``LocalRpcError: unattributed_latency``, and the Finder shows
    "request failed". Prove the completion path holds ONE registry client lease that vetoes idle
    shutdown at the exact moment it polls -- even with the broker forced well past its idle window --
    and releases it at the end so idle shutdown is NOT weakened (an unheld broker still idles out).
    """
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", idle_seconds=5.0, workers=1)

    class BrokerLeaseRegistry:
        """Exercise the lease handlers synchronously; transport timing is not this contract."""

        def __init__(self):
            self.acquired: list[str] = []
            self.released: list[str] = []

        def acquire_lease(self, existing_lease_id=""):
            response = broker.handle({
                "action": "lease",
                "client_pid": os.getpid(),
                "lease_id": existing_lease_id,
            })[0]
            self.acquired.append(str(response.get("lease_id") or ""))
            return response

        def release_lease(self, lease_id):
            self.released.append(lease_id)
            return broker.handle({"action": "release", "lease_id": lease_id})[0]

    registry = BrokerLeaseRegistry()
    app = app_module.TmuxWebtermApp([], status_service_mode=True)
    app.jobd_fs_batch_lease = app_module.JobdInteractionLease(type("JobClient", (), {"registry": registry})())
    try:
        assert broker.handle({"action": "status"})[0]["clients"] == 0

        observed: dict[str, object] = {}

        def poll_probe(_producer, _deadline_at):
            # At the poll the lease MUST be held. Force the broker well past its idle window and prove
            # it refuses to shut down because of the held lease, not because the clock is fresh.
            broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
            observed["held_during_poll"] = app.jobd_fs_batch_lease.held
            observed["clients_during_poll"] = broker.handle({"action": "status"})[0]["clients"]
            observed["idle_should_stop"] = broker._idle_should_stop()
            observed["shutdown_if_idle"] = broker.handle({"action": "shutdown_if_idle"})[0]
            return {"responses": [{"id": 0, "ok": True}]}

        monkeypatch.setattr(app, "wait_for_jobd_operation_product", poll_probe)
        monkeypatch.setattr(app, "terminalize_operation", lambda *args, **kwargs: None)

        producer = app_module.JobdProductOperation(job_id="job-1", product_key="key-1", generation=1)
        app.complete_filesystem_batch_operation("op-1", "req-1", (0,), producer, time.time() + 5.0)

        assert observed["held_during_poll"] is True
        assert observed["clients_during_poll"] == 1
        assert observed["idle_should_stop"] is False, "a held lease must veto idle shutdown mid-poll"
        assert observed["shutdown_if_idle"] == {"ok": True, "shutdown": False, "leases": 1}

        # Released at the end: idle shutdown is NOT weakened -- an unheld broker still idles out.
        assert app.jobd_fs_batch_lease.held is False
        assert broker.handle({"action": "status"})[0]["clients"] == 0
        assert registry.acquired == registry.released
        broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
        assert broker._idle_should_stop() is True
    finally:
        app.stop_jobd_operation_service()


def test_watch_diff_completion_holds_a_jobd_lease_across_the_broker_idle_window(tmp_path, monkeypatch):
    """The watch-diff completion worker pins the broker with a client lease while it polls.

    Same Seam-B lease mechanism as
    ``test_fs_batch_completion_holds_a_jobd_lease_across_the_broker_idle_window`` -- ``GET
    /api/fs/watch-diff`` simply was not covered. The watch-diff completion worker submits every
    child batch and then polls each product under one deadline; under a saturated gate the gap
    between the submit ``produce`` and the product poll can exceed the broker's idle window, so the
    broker removes its own socket mid-interaction and the poll fails with a jobd 404 (the live
    ``GET /api/fs/watch-diff`` failure). Prove the completion path holds ONE registry client lease
    that vetoes idle shutdown at the exact moment it polls -- even with the broker forced well past
    its idle window -- and releases it at the end so idle shutdown is NOT weakened (an unheld broker
    still idles out).
    """
    socket_path = tmp_path / "jobd.sock"
    broker = jobd.PersistentJobBroker(socket_path, idle_seconds=5.0, workers=1)
    worker = threading.Thread(target=broker.run, daemon=True)
    worker.start()
    try:
        app = app_module.TmuxWebtermApp([], status_service_mode=True)
        app.job_client = jobd.JobClient(socket_path)
        # The app's watch-diff path holds this exact lease owner -- the SAME one fs/batch holds --
        # so bind it to the test broker's client.
        app.jobd_fs_batch_lease = app_module.JobdInteractionLease(app.job_client)
        deadline = time.monotonic() + 2.0
        while not app.job_client.registry.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.job_client.registry.healthy() is True
        # No interaction yet: the broker holds no client lease.
        assert broker.handle({"action": "status"})[0]["clients"] == 0

        # A receipt-only child batch forces `resolve_filesystem_watch_batches` to poll the broker
        # (mirrors a cold submit that returned a receipt, not a warm product). The completion
        # worker's real acquire/release around submit+resolve is the code under test.
        producer = app_module.JobdProductOperation(job_id="job-1", product_key="watch-key-0", generation=1)
        batch = app_module.FilesystemWatchBatchProduct(
            producer=producer,
            ready_product=None,
            root_offset=0,
            root_count=1,
        )
        monkeypatch.setattr(app, "submit_filesystem_watch_batches", lambda *args, **kwargs: (batch,))
        monkeypatch.setattr(app, "materialize_filesystem_watch_products", lambda *args, **kwargs: {})
        monkeypatch.setattr(app, "terminalize_operation", lambda *args, **kwargs: None)

        observed: dict[str, object] = {}

        def poll_probe(_producer, _deadline_at, *, cancel_event=None):
            # At the poll the lease MUST be held. Force the broker well past its idle window and prove
            # it refuses to shut down because of the held lease, not because the clock is fresh.
            broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
            observed["held_during_poll"] = app.jobd_fs_batch_lease.held
            observed["clients_during_poll"] = broker.handle({"action": "status"})[0]["clients"]
            observed["idle_should_stop"] = broker._idle_should_stop()
            observed["shutdown_if_idle"] = broker.handle({"action": "shutdown_if_idle"})[0]
            return {"responses": [{"id": 0, "ok": True}]}

        monkeypatch.setattr(app, "wait_for_jobd_operation_product", poll_probe)

        flight = app_module.JobdOperationFlight(
            lane="bulk",
            key="watch-key-0",
            deadline_at=time.time() + 5.0,
        )
        flight.accept_owner("op-1")
        app.complete_filesystem_watch_diff_operation(
            flight,
            {},
            ["/tmp/watch-root"],
            "seed-1",
        )

        assert observed["held_during_poll"] is True
        assert observed["clients_during_poll"] == 1
        assert observed["idle_should_stop"] is False, "a held lease must veto idle shutdown mid-poll"
        assert observed["shutdown_if_idle"] == {"ok": True, "shutdown": False, "leases": 1}

        # Released at the end: idle shutdown is NOT weakened -- an unheld broker still idles out.
        assert app.jobd_fs_batch_lease.held is False
        assert broker.handle({"action": "status"})[0]["clients"] == 0
        broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
        assert broker._idle_should_stop() is True
    finally:
        broker.handle({"action": "shutdown"})
        worker.join(timeout=2.0)
    assert worker.is_alive() is False


def _poll_broker_product(broker, coalesce_key, *, wait_seconds=5.0):
    """Poll one broker's product store the way the web side now does (no blocking `relay`)."""
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        response, returned = broker.handle({"action": "product", "coalesce_key": coalesce_key})
        if response.get("artifact") is True and response.get("state") in {"ready", "stale"}:
            opened, _empty = broker.handle({
                "action": "artifact_open",
                "coalesce_key": coalesce_key,
                "generation": response["generation"],
            })
            chunks = []
            offset = 0
            try:
                while offset < opened["product"]["length"]:
                    _metadata, chunk = broker.handle({
                        "action": "artifact_chunk",
                        "lease_id": opened["lease_id"],
                        "offset": offset,
                    })
                    chunks.append(chunk)
                    offset += len(chunk)
            finally:
                broker.handle({"action": "artifact_close", "lease_id": opened["lease_id"]})
            return response, b"".join(chunks)
        if returned and response.get("state") in {"ready", "stale"}:
            return response, returned
        if response.get("state") == "none" and response.get("inflight") is not True:
            return response, returned
        time.sleep(0.02)
    raise AssertionError("broker product never became ready")


@pytest.mark.gate_serial
def test_zero_wait_produce_returns_a_browser_opaque_byte_product_without_a_relay(tmp_path, monkeypatch):
    """The retired `relay` action's job -- a browser byte download -- is served by zero-wait produce.

    `produce` submits and inspects the store atomically (no handler blocks), and the web side polls
    `product` for the bytes.  The former blocking `relay` action must no longer exist.
    """
    path = tmp_path / "payload.bin"
    body = b"\x00raw\xff"
    path.write_bytes(body)
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    submitted: list[tuple[Future, object, tuple[object, ...]]] = []

    class Executor:
        def submit(self, function, *args):
            future = Future()
            submitted.append((future, function, args))
            return future

    monkeypatch.setattr(broker, "_executor", lambda *_args: Executor())
    assert "relay" not in jobd.JOBD_REQUEST_ACTIONS
    unknown, _empty = broker.handle({"action": "relay"})
    assert unknown == {"ok": False, "error": "unknown jobd action"}

    response, returned = broker.handle({
        "action": "produce",
        "task": "filesystem_operation",
        "payload": _fs_descriptor(op="raw", path=str(path), args={"download": True}),
        "priority": "interactive",
        "coalesce_key": "relay-raw",
        "generation": 1,
        "deadline_ms": 5_000,
        "delivery": "ready_or_receipt",
    })
    assert returned == b""
    assert response["state"] == "queued"

    broker._pump()
    future, function, args = submitted.pop()
    future.set_result(function(*args))
    broker._pump()
    response, returned = broker.handle({"action": "product", "coalesce_key": "relay-raw"})

    assert response["state"] == "ready"
    assert response["product"]["format"] == "opaque_bytes"
    assert returned == b""
    assert response["artifact"] is True
    opened, _empty = broker.handle({"action": "artifact_open", "coalesce_key": "relay-raw", "generation": 1})
    chunked = bytearray()
    offset = 0
    while offset < len(body):
        chunk_meta, chunk = broker.handle({
            "action": "artifact_chunk", "lease_id": opened["lease_id"], "offset": offset,
        })
        assert len(chunk) <= jobd.LOCAL_RPC_MAX_BINARY_BYTES
        assert chunk_meta["sha256"] == hashlib.sha256(chunk).hexdigest()
        chunked.extend(chunk)
        offset += len(chunk)
    assert bytes(chunked) == body
    closed, _empty = broker.handle({"action": "artifact_close", "lease_id": opened["lease_id"]})
    assert closed == {"ok": True, "closed": True}
    assert broker.product_store.lease_count() == 0


@pytest.mark.parametrize("operation", ("raw", "zip"))
def test_large_filesystem_transfer_uses_bounded_artifact_chunks(operation, tmp_path, monkeypatch):
    monkeypatch.setattr(jobd, "RUNTIME_DIR", tmp_path / "runtime")
    source = tmp_path / "source"
    expected = b"z" * (jobd.LOCAL_RPC_MAX_BINARY_BYTES + 257)
    if operation == "raw":
        source.write_bytes(expected)
        args = {"max_bytes": len(expected) + 1024}
    else:
        source.mkdir()
        (source / "payload.bin").write_bytes(expected)
        args = {"max_bytes": len(expected) + 1024, "filename": "source.zip"}
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    result = jobd.run_registered_task_result(
        "filesystem_operation",
        json.dumps(_fs_descriptor(op=operation, path=str(source), args=args)).encode("utf-8"),
    )
    assert isinstance(result, jobd.JobdArtifactResult)
    artifact_path = jobd.artifact_root() / result.basename
    record = broker._queue_record("filesystem_operation", {}, "interactive", 1, f"large-{operation}")
    record.product = broker.product_store.store_artifact(
        key=record.coalesce_key, generation=record.generation, result=result, schedule=broker._record_schedule(record),
    )
    assert artifact_path.exists() is False, "broker adoption must unlink the worker pathname"
    assert record.result == b""
    assert broker.product_store.inline_bytes() == 0
    opened, _empty = broker.handle({"action": "artifact_open", "coalesce_key": f"large-{operation}", "generation": 1})
    chunks = []
    offset = 0
    while offset < opened["product"]["length"]:
        metadata, chunk = broker.handle({"action": "artifact_chunk", "lease_id": opened["lease_id"], "offset": offset})
        assert 0 < len(chunk) <= jobd.LOCAL_RPC_MAX_BINARY_BYTES
        chunks.append(chunk)
        offset += len(chunk)
        assert metadata["offset"] + metadata["length"] == offset
    broker.handle({"action": "artifact_close", "lease_id": opened["lease_id"]})
    if operation == "raw":
        assert b"".join(chunks) == expected
    else:
        archive_path = tmp_path / "returned.zip"
        archive_path.write_bytes(b"".join(chunks))
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("source/payload.bin") == expected
    broker._on_shutdown()
    assert broker.product_store.open_descriptor_count() == 0 and broker.product_store.lease_count() == 0


def test_filesystem_transfer_cap_plus_one_is_typed_413(tmp_path, monkeypatch):
    monkeypatch.setattr(jobd, "RUNTIME_DIR", tmp_path / "runtime")
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * 1025)
    with pytest.raises(jobd.JobdFilesystemOperationFailure) as failure:
        jobd.run_registered_task_result(
            "filesystem_operation",
            json.dumps(_fs_descriptor(op="raw", path=str(source), args={"max_bytes": 1024})).encode("utf-8"),
        )
    assert failure.value.status == 413
    assert failure.value.payload["user_message"]["key"] == "fs.error.tooLarge"


def test_zero_wait_produce_and_shared_product_poll_do_not_hold_former_relay_handler_slots(tmp_path, monkeypatch):
    """Cold byte work occupies the worker, not one RPC handler for its lifetime.

    A controlled executor keeps the first regular-file transfer cold without weakening the raw
    file contract to admit FIFOs. Both zero-wait produce calls and a product poll must nevertheless
    complete over the real Unix listener before that release. The retired relay would have parked
    one handler per request at this point and exhausted a two-slot listener.
    """
    socket_path = tmp_path / "jobd.sock"
    first_path = tmp_path / "first.bin"
    second_path = tmp_path / "second.bin"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    broker = jobd.PersistentJobBroker(socket_path, workers=1)
    submitted = []

    class ControlledExecutor:
        def submit(self, function, *args):
            future = Future()
            submitted.append((future, function, args))
            return future

    controlled_executor = ControlledExecutor()
    monkeypatch.setattr(broker, "_executor", lambda *_args: controlled_executor)
    worker = threading.Thread(target=broker.run, daemon=True)
    worker.start()
    client = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.registry.healthy() is True
    try:
        receipts = []
        for index, path in enumerate((first_path, second_path), start=1):
            response, body = client.produce(
                "filesystem_operation",
                _fs_descriptor(op="raw", path=str(path), args={"download": True}),
                priority="interactive",
                coalesce_key=f"former-relay-{index}",
                generation=1,
                deadline_ms=5_000,
                delivery="receipt",
            )
            assert body == b""
            receipts.append(response)
        assert [row["job"]["status"] for row in receipts] == ["queued", "queued"]

        pending, body = client.product("former-relay-1")
        assert body == b""
        assert pending["ok"] is True
        assert pending["state"] in {"pending", "none"}
        assert pending.get("inflight") is True

        deadline = time.monotonic() + 2.0
        while not submitted and time.monotonic() < deadline:
            time.sleep(0.01)
        first_future, first_function, first_args = submitted.pop(0)
        first_future.set_result(first_function(*first_args))
        ready, returned = _poll_broker_product(broker, "former-relay-1")
        assert ready["state"] in {"ready", "stale"}
        assert returned == b"first"
    finally:
        deadline = time.monotonic() + 2.0
        while not submitted and time.monotonic() < deadline:
            time.sleep(0.01)
        if submitted:
            second_future, second_function, second_args = submitted.pop(0)
            second_future.set_result(second_function(*second_args))
        broker.stop_event.set()
        worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_artifact_adoption_does_not_hold_the_broker_state_lock(tmp_path, monkeypatch):
    """A completed large transfer must not strand unrelated zero-wait RPCs while it is verified."""
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    record = broker._queue_record("filesystem_operation", {}, "interactive", 1, "adopting-artifact")
    record.status = "running"
    record.future = Future()
    record.future.set_result(jobd.JobdArtifactResult(
        basename="transfer-controlled",
        device=1,
        inode=2,
        product={
            "format": "opaque_bytes",
            "content_type": "image/png",
            "length": 1_957_801,
            "sha256": "0" * 64,
            "disposition": "inline",
            "filename": "",
        },
    ))
    adoption_started = threading.Event()
    release_adoption = threading.Event()

    def blocked_adoption(_result):
        adoption_started.set()
        assert release_adoption.wait(timeout=2.0)
        raise ValueError("controlled adoption stop")

    monkeypatch.setattr(broker.product_store, "prepare_artifact", blocked_adoption)
    pump = threading.Thread(target=broker._pump, name="jobd-controlled-artifact-adoption")
    pump.start()
    assert adoption_started.wait(timeout=1.0)

    responses = []
    requests = (
        {"action": "produce", "task": "json_compact", "payload": {"value": 1}, "coalesce_key": "unrelated-produce"},
        {"action": "result", "job_id": "unknown"},
        {"action": "product", "coalesce_key": "unrelated-product"},
    )
    callers = [
        threading.Thread(
            target=lambda request=request: responses.append((request["action"], broker.handle(request))),
            name=f"jobd-unrelated-{request['action']}",
        )
        for request in requests
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=0.1)
    served_while_adopting = all(not caller.is_alive() for caller in callers)
    release_adoption.set()
    for caller in callers:
        caller.join(timeout=2.0)
    pump.join(timeout=2.0)

    assert served_while_adopting is True
    by_action = dict(responses)
    assert by_action["produce"][0]["ok"] is True
    assert by_action["produce"][0]["state"] == "queued"
    assert by_action["result"] == ({"ok": False, "error": "unknown job"}, b"")
    assert by_action["product"] == ({"ok": True, "state": "none", "generation": 0, "inflight": False}, b"")
    assert record.status == "failed"


def test_zero_wait_produce_preserves_typed_filesystem_failure(tmp_path):
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    broker._start_scheduler()
    try:
        submit, _empty = broker.handle({
            "action": "produce",
            "task": "filesystem_operation",
            "payload": _fs_descriptor(op="raw", path=str(tmp_path / "missing.bin"), args={}),
            "priority": "interactive",
            "coalesce_key": "relay-missing",
            "generation": 1,
            "deadline_ms": 5_000,
            "delivery": "receipt",
        })
        job_id = submit["job"]["job_id"]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            result = broker.handle({"action": "result", "job_id": job_id})[0]
            if result["job"]["status"] == "failed":
                break
            time.sleep(0.02)
    finally:
        broker.stop_event.set()
        broker._on_shutdown()

    assert result["job"]["status"] == "failed"
    assert result["job"]["failure"]["status"] == 404
    assert result["job"]["failure"]["filesystem_error"]["path"].endswith("missing.bin")


@pytest.mark.parametrize(
    ("operation", "contents", "maximum", "expected_status", "expected_key"),
    (
        ("read", None, None, 404, "common.pathNotFound"),
        ("diff", None, None, 400, "fs.error.notGitRepo"),
        ("read", b"abc\0def", None, 415, "fs.error.binary"),
        ("read", b"x" * 100, 10, 413, "fs.error.tooLarge"),
    ),
)
def test_filesystem_operation_parent_preserves_typed_failures(monkeypatch, tmp_path, operation, contents, maximum, expected_status, expected_key):
    path = tmp_path / "typed.txt"
    if contents is not None:
        path.write_bytes(contents)
    if maximum is not None:
        monkeypatch.setattr(jobd.filesystem, "MAX_READ_BYTES", maximum)
    payload = json.dumps(_fs_descriptor(op=operation, path=str(path), args={})).encode("utf-8")

    with pytest.raises(jobd.JobdFilesystemOperationFailure) as failure:
        jobd._filesystem_operation(payload)

    assert failure.value.status == expected_status
    assert failure.value.payload["status"] == expected_status
    assert failure.value.payload["user_message"]["key"] == expected_key


def test_session_files_view_skips_deleted_root_from_durable_transcript_cache(tmp_path, monkeypatch):
    """A cache entry mentioning a retired worktree is a typed partial result, never a worker crash."""
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    retired_root = tmp_path / "retired-worktree"
    retired_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": str(retired_root / "gone.py")}}]},
        "padding": "x" * (session_files._TRANSCRIPT_SCAN_PERSIST_MIN_BYTES + 1),
    }) + "\n", encoding="utf-8")
    assert str(retired_root / "gone.py") in session_files.scan_claude_transcript(transcript, str(retired_root))
    cache_key = session_files.claude_transcript_scan_cache_key(transcript)
    assert cache_key is not None
    cache_path = session_files.transcript_scan_store_path(cache_key)
    assert cache_path.exists()
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    retired_root.rmdir()

    result = session_files.session_files_view_result({
        "session": "s1",
        "infos": {"s1": _session_info_json("s1", retired_root, transcript)},
        "hours": 24.0,
        "include_cross_session_attribution": False,
    }, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)

    assert result["status"] == 200
    # A retired worktree crosses the worker boundary as ONE typed repo row, not as a warning and
    # not as one row per remembered file.
    assert result["payload"]["warnings"] == []
    assert result["payload"]["files"] == []
    gone = [repo for repo in result["payload"]["repos"] if repo.get("missing") is True]
    assert len(gone) == 1, result["payload"]["repos"]
    assert gone[0]["repo"] == str(retired_root)
    assert gone[0]["touched_count"] == 1
    assert cache_path.exists()


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


def test_session_files_view_reuses_watcher_generation_across_metadata_only_products(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 4\n", encoding="utf-8")
    calls: list[str] = []
    real_build = session_files.build_git_snapshot

    def counting_build(path, from_ref=None, to_ref=None):
        calls.append(str(path))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "build_git_snapshot", counting_build)
    base = {"session": "a", "infos": {"a": _session_info_json("a", repo)}, "hours": 24.0, "include_cross_session_attribution": False, "repository_states": [{"path": str(repo), "generation": 7}]}
    first = session_files.session_files_view_result(base, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
    changed_metadata = {**base, "infos": {"a": _session_info_json("a", repo, kind="codex")}}
    second = session_files.session_files_view_result(changed_metadata, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
    assert first["status"] == second["status"] == 200
    assert calls == [str(repo)]

    changed_repository = {**base, "repository_states": [{"path": str(repo), "generation": 8}]}
    third = session_files.session_files_view_result(changed_repository, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
    assert third["status"] == 200
    assert calls == [str(repo), str(repo)]


def test_session_files_view_canonicalizes_repository_state_keys_across_worktree_aliases(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 4\n", encoding="utf-8")
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    calls: list[str] = []
    real_build = session_files.build_git_snapshot

    def counting_build(path, from_ref=None, to_ref=None):
        calls.append(str(path.resolve()))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "build_git_snapshot", counting_build)
    base = {"session": "a", "infos": {"a": _session_info_json("a", repo)}, "hours": 24.0, "include_cross_session_attribution": False}
    canonical = {**base, "repository_states": [{"path": str(repo), "generation": 7}]}
    via_alias = {**base, "repository_states": [{"path": str(alias), "generation": 7}]}

    assert session_files.session_files_view_result(canonical, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert session_files.session_files_view_result(via_alias, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert calls == [str(repo.resolve())]


def test_session_files_view_keeps_topology_and_ref_overrides_separate_per_repository(tmp_path, monkeypatch):
    """Candidate roots/ref overrides are repository planning inputs, not volatile view metadata."""
    first_repo = tmp_path / "first"
    second_repo = tmp_path / "second"
    _init_repo_with_commit(first_repo)
    _init_repo_with_commit(second_repo)
    for repo in (first_repo, second_repo):
        (repo / "one.py").write_text("x = 2\n", encoding="utf-8")
        git(repo, "add", "one.py")
        git(repo, "commit", "-m", "next")
    first_alias = tmp_path / "first-alias"
    first_alias.symlink_to(first_repo, target_is_directory=True)
    calls: list[tuple[str, str | None, str | None]] = []
    real_build = session_files.build_git_snapshot

    def counting_build(path, from_ref=None, to_ref=None):
        calls.append((str(path.resolve()), from_ref, to_ref))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "build_git_snapshot", counting_build)
    payload = {
        "session": "",
        "infos": {
            "first": _session_info_json("first", first_repo),
            "second": _session_info_json("second", second_repo),
        },
        "hours": 24.0,
        "include_cross_session_attribution": True,
        "repository_states": [
            {"path": str(first_alias), "generation": 7},
            {"path": str(second_repo), "generation": 11},
        ],
        "repo_refs": {
            str(first_alias): {"from": "HEAD~1", "to": "HEAD"},
            str(second_repo): {"from": "HEAD", "to": "HEAD"},
        },
    }
    assert session_files.session_files_view_result(payload, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert session_files.session_files_view_result(payload, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert calls == [
        (str(first_repo.resolve()), "HEAD~1", "HEAD"),
        (str(second_repo.resolve()), "HEAD", "HEAD"),
    ]

    changed_second_ref = json.loads(json.dumps(payload))
    changed_second_ref["repo_refs"][str(second_repo)]["from"] = "HEAD~1"
    assert session_files.session_files_view_result(changed_second_ref, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert calls[-1] == (str(second_repo.resolve()), "HEAD~1", "HEAD")
    assert len(calls) == 3


def test_session_files_view_regression_matrix_reuses_git_snapshot_until_repo_generation_changes(tmp_path, monkeypatch):
    """Volatile browser/watch inputs may rebuild attribution, never the Git snapshot.

    This is the CPU-regression matrix for repeated harmless filesystem notifications, rapid
    agent status/transcript churn, and Finder selection toggles.  They deliberately produce
    distinct view products; the shared repository snapshot must remain one build until the
    watcher reports a real repository generation change, when it must rebuild exactly once.
    """
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 4\n", encoding="utf-8")
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": "first"}) + "\n", encoding="utf-8")
    calls: list[str] = []
    real_build = session_files.build_git_snapshot

    def counting_build(path, from_ref=None, to_ref=None):
        calls.append(str(path))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "build_git_snapshot", counting_build)
    base = {
        "session": "a",
        "infos": {"a": _session_info_json("a", repo, transcript)},
        "hours": 24.0,
        "include_cross_session_attribution": False,
        "repository_states": [{"path": str(repo), "generation": 7}],
    }

    # Same repository generation, but each pass represents an independent volatile view input:
    # duplicate watcher event, a status change, transcript append, and Finder's selected session.
    unchanged_watch = json.loads(json.dumps(base))
    status_changed = json.loads(json.dumps(base))
    status_changed["infos"]["a"]["agents"][0]["status"] = "working"
    transcript.write_text(transcript.read_text(encoding="utf-8") + json.dumps({"type": "user", "message": "second"}) + "\n", encoding="utf-8")
    transcript_changed = json.loads(json.dumps(status_changed))
    finder_toggle = json.loads(json.dumps(transcript_changed))
    finder_toggle["session"] = ""
    finder_toggle["include_cross_session_attribution"] = True
    for payload in (base, unchanged_watch, status_changed, transcript_changed, finder_toggle, status_changed):
        result = session_files.session_files_view_result(payload, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
        assert result["status"] == 200
    assert calls == [str(repo)]

    changed_repository = {**base, "repository_states": [{"path": str(repo), "generation": 8}]}
    first_after_change = session_files.session_files_view_result(changed_repository, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
    second_after_change = session_files.session_files_view_result(changed_repository, max_bytes=jobd.JOBD_MAX_RESULT_BYTES - 4096)
    assert first_after_change["status"] == second_after_change["status"] == 200
    assert calls == [str(repo), str(repo)]


def test_repository_snapshot_cache_single_flights_across_spawned_workers(tmp_path):
    """Single-flight holds across real processes on the real cross-process ``file_lock``.

    The oracle is decoupled from cold spawn/import latency by a two-phase readiness handshake (see
    ``_build_repository_snapshot_in_child``).  The parent waits for the first worker's ``ready`` and
    its ``in_builder`` before it even starts the second worker, so the second is guaranteed to
    contend against a lock the first already holds -- genuine cross-process contention -- while no
    wall-clock budget is placed on fork+import.  A stalled worker still fails the test: a crash
    surfaces via ``_await_worker_ready``/``exitcode``, and a real lock deadlock surfaces via the
    bounded ``in_builder`` wait, which measures only the load-independent, post-readiness product
    step.  ``builds == ["build"]`` remains the single-flight invariant and is not weakened.
    """
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    context = multiprocessing.get_context("spawn")
    first_ready = context.Event()
    second_ready = context.Event()
    in_builder = context.Event()
    release = context.Event()
    counter = tmp_path / "build-count.txt"
    state_dir = str(tmp_path / "state")
    counter_text = str(counter)
    first = context.Process(
        target=_build_repository_snapshot_in_child,
        args=(str(repo), state_dir, counter_text, first_ready, in_builder, release),
    )
    second = context.Process(
        target=_build_repository_snapshot_in_child,
        args=(str(repo), state_dir, counter_text, second_ready, in_builder, release),
    )
    # Phase 1: the first worker becomes the builder and holds the cross-process lock.
    first.start()
    _await_worker_ready(first, first_ready)
    assert in_builder.wait(timeout=30.0), "first worker never reached the single-flight builder (deadlock)"
    # Phase 2: only now start the second worker, so it must contend against the held lock rather
    # than race a not-yet-locked cache.  Its readiness proves it imported and reached the builder
    # entry; single-flight must then keep it out of the builder entirely.
    second.start()
    _await_worker_ready(second, second_ready)
    release.set()
    first.join(timeout=30.0)
    second.join(timeout=30.0)
    assert not first.is_alive() and not second.is_alive(), "a worker never retired"
    assert first.exitcode == second.exitcode == 0
    assert counter.read_text(encoding="utf-8").splitlines() == ["build"]


def test_repository_snapshot_cache_single_flights_concurrent_callers_deterministically(tmp_path, monkeypatch):
    """Single-flight, proven without a spawn-latency race: a concurrent second caller waits.

    `test_..._across_spawned_workers` proves the SAME collapse across real processes, but its
    5s `started.wait` bound is a patience surface -- under a saturated full gate a spawned worker
    can take longer than that merely to import, which is a scheduling artifact, not a single-flight
    defect. This companion pins the invariant with in-process threads and an explicit barrier, so
    ordering is fixed by the handshake rather than by timing: while the first caller holds
    `file_lock` inside the builder, the second caller blocks on that same lock and, once released,
    is served the freshly written cache instead of launching a second build.
    """
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    builds: list[int] = []
    builds_lock = threading.Lock()
    first_in_builder = threading.Event()
    release_first = threading.Event()

    def build(_repo, _from_ref, _to_ref):
        with builds_lock:
            builds.append(1)
            ordinal = len(builds)
        if ordinal == 1:
            first_in_builder.set()
            assert release_first.wait(timeout=10.0), "first caller was never released"
        return {"statuses": {}}

    results: dict[str, tuple[dict, bool]] = {}

    def call(name):
        results[name] = session_files.cached_repository_snapshot(repo, None, None, 9, build)

    first = threading.Thread(target=call, args=("first",))
    second = threading.Thread(target=call, args=("second",))
    first.start()
    assert first_in_builder.wait(timeout=10.0), "first caller never entered the builder"
    # The first caller now holds the cross-caller lock inside the builder; the second must block on
    # it rather than start its own build. Give it a beat to reach the lock, then release the first.
    second.start()
    second.join(timeout=2.0)
    assert second.is_alive(), "second caller did not block behind the single-flight lock"
    release_first.set()
    first.join(timeout=10.0)
    second.join(timeout=10.0)
    assert not first.is_alive() and not second.is_alive()
    assert builds == [1], builds
    assert results["first"][1] is False, results["first"]
    assert results["second"][1] is True, results["second"]


def test_repository_snapshot_cache_keeps_ref_comparisons_separate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    calls = []

    def build(path, from_ref, to_ref):
        calls.append((str(path), from_ref, to_ref))
        return {"statuses": {}}

    session_files.cached_repository_snapshot(repo, "HEAD~1", "HEAD", 9, build)
    session_files.cached_repository_snapshot(repo, "HEAD", "current", 9, build)
    session_files.cached_repository_snapshot(repo, "HEAD~1", "HEAD", 9, build)
    assert calls == [
        (str(repo), "HEAD~1", "HEAD"),
        (str(repo), "HEAD", "current"),
    ]


def test_repository_snapshot_cache_revalidates_after_the_healthy_watcher_safety_window(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    now = [1000.0]
    monkeypatch.setattr(session_files.time, "time", lambda: now[0])
    calls = []

    def build(path, from_ref, to_ref):
        calls.append((str(path), from_ref, to_ref))
        return {"statuses": {}}

    session_files.cached_repository_snapshot(repo, None, None, 9, build)
    now[0] += session_files._REPOSITORY_SNAPSHOT_CACHE_MAX_AGE_SECONDS - 1
    session_files.cached_repository_snapshot(repo, None, None, 9, build)
    now[0] += 2
    session_files.cached_repository_snapshot(repo, None, None, 9, build)
    assert calls == [(str(repo), None, None), (str(repo), None, None)]


def test_repository_snapshot_cache_rebuilds_corrupt_records_and_propagates_git_failures(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    path = session_files.repository_snapshot_cache_path(repo, None, None, 1)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    calls = []

    def build(path, from_ref, to_ref):
        calls.append(str(path))
        return {"statuses": {}}

    snapshot, hit = session_files.cached_repository_snapshot(repo, None, None, 1, build)
    assert snapshot == {"statuses": {}}
    assert hit is False
    assert calls == [str(repo)]

    def fail(path, from_ref, to_ref):
        raise RuntimeError("git failed")

    with pytest.raises(RuntimeError, match="git failed"):
        session_files.cached_repository_snapshot(repo, None, None, 2, fail)


def test_repository_snapshot_cache_prunes_only_expired_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "_repository_snapshot_cache_last_pruned_at", 0.0)
    directory = session_files.host_partitioned_state_dir(tmp_path / "state") / session_files._REPOSITORY_SNAPSHOT_CACHE_DIRNAME
    directory.mkdir(parents=True)
    expired = directory / "expired.json"
    current = directory / "current.json"
    expired.write_text("{}", encoding="utf-8")
    current.write_text("{}", encoding="utf-8")
    now = 10_000.0
    os.utime(expired, (now - session_files._REPOSITORY_SNAPSHOT_CACHE_PRUNE_MAX_AGE_SECONDS - 1, now - session_files._REPOSITORY_SNAPSHOT_CACHE_PRUNE_MAX_AGE_SECONDS - 1))
    os.utime(current, (now - 1, now - 1))

    assert session_files.prune_repository_snapshot_cache(now) == 1
    assert not expired.exists()
    assert current.exists()
    assert session_files.prune_repository_snapshot_cache(now + 1) == 0


def test_session_files_view_bounding_trims_files_and_sets_truncated_flag():
    payload = {"files": [{"path": f"/repo/file{index}.py", "blob": "y" * 256} for index in range(200)], "repos": []}
    truncated = session_files.bound_session_files_view_payload(payload, 4096)
    assert truncated is True
    assert len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) <= 4096
    assert len(payload["files"]) < 200


def _sample_gathered_agent(session, *, screen_text=""):
    return {
        "kind": "claude", "state": "idle", "window": "0", "window_index": 0, "window_name": "w", "window_label": "0:claude",
        "pane": "0", "pane_target": f"{session}:0.0", "pid": 1, "window_is_current": True, "paths": [], "path_entries": [], "fallback_path": "",
        "git": None, "transcript": "", "transcript_id": "", "agent_session_id": "", "elapsed": -1.0, "last_active_ts": 0.0,
        "working_stopped_ts": 0.0, "observed_ts": 1.0, "screen_text": screen_text, "status_tokens": None, "agent_index": 0,
        "attention_key": "", "attention_acknowledged": None, "attention_acknowledged_at": None,
        "cooldown_attention_key": "", "cooldown_acknowledged": None, "cooldown_acknowledged_at": None, "owned": None,
    }


def _sample_tabber_session_payload(session):
    pane = PaneInfo(session=session, window="0", window_name="w", pane="0", pane_id=f"%{session}", target=f"{session}:0.0", current_path="/repo", command="claude", active=True, window_active=True, title="claude", pid=1)
    agent = AgentInfo(session, "claude", 1, f"{session}:0.0", "claude", "/repo", None, None, None, None)
    info = SessionInfo(session=session, panes=[pane], selected_pane=pane, agents=[agent])
    return {
        "info": asdict(info),
        "gathered_agents": [_sample_gathered_agent(session, screen_text="secret prompt text should never leak into diagnostics")],
        "files_payload": {},
        "transcript_views_by_path": {},
    }


def test_tabber_activity_view_task_is_pure_and_produces_deterministic_rows():
    payload = {"sessions": {"1": _sample_tabber_session_payload("1")}, "locale": "en", "snapshot_revision": 7}
    result = json.loads(jobd.run_registered_task("tabber_activity_view", json.dumps(payload).encode("utf-8")))

    assert result["truncated"] is False
    assert set(result["session_rows"]) == {"1"}
    assert result["session_rows"]["1"]["agent_windows"][0]["kind"] == "claude"
    assert len(result["session_rows"]["1"]["agents"]) == 1
    # Running it again with identical input is byte-for-byte identical (pure function).
    again = json.loads(jobd.run_registered_task("tabber_activity_view", json.dumps(payload).encode("utf-8")))
    assert again == result


def test_tabber_activity_view_task_rejects_malformed_or_oversized_payload():
    with pytest.raises(ValueError):
        jobd.run_registered_task("tabber_activity_view", json.dumps({"sessions": "not-an-object"}).encode("utf-8"))
    too_many = {str(index): _sample_tabber_session_payload(str(index)) for index in range(activity_summary.TABBER_ACTIVITY_VIEW_MAX_SESSIONS + 1)}
    with pytest.raises(ValueError):
        jobd.run_registered_task("tabber_activity_view", json.dumps({"sessions": too_many}).encode("utf-8"))
    with pytest.raises(ValueError):
        jobd.run_registered_task("tabber_activity_view", b"{" + b" " * (jobd.JOBD_MAX_PAYLOAD_BYTES + 1))


def test_tabber_activity_view_task_never_leaks_live_screen_text_beyond_its_own_field():
    # The worker is pure assembly: it must not fabricate or duplicate screen text into any other
    # field, and must not require/perform any tmux/attention read of its own.
    payload = {"sessions": {"1": _sample_tabber_session_payload("1")}, "locale": "en", "snapshot_revision": 1}
    result = json.loads(jobd.run_registered_task("tabber_activity_view", json.dumps(payload).encode("utf-8")))
    row = result["session_rows"]["1"]["agent_windows"][0]
    assert row["screen_text"] == "secret prompt text should never leak into diagnostics"
    # The recent-agents row (a different display surface) must not carry the raw screen text.
    assert "secret prompt text" not in json.dumps(result["session_rows"]["1"]["agents"])


def test_tabber_activity_view_task_bounds_result_by_evicting_whole_sessions():
    sessions = {str(index): _sample_tabber_session_payload(str(index)) for index in range(20)}
    payload = {"sessions": sessions, "locale": "en", "snapshot_revision": 1}
    result = activity_summary.tabber_activity_view_result(payload, max_bytes=2048)
    assert result["truncated"] is True
    assert len(result["session_rows"]) < 20
    assert len(json.dumps(result, separators=(",", ":")).encode("utf-8")) <= 2048


def test_metadata_warm_view_task_populates_cache_entries_from_a_real_session_work_graph(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    git(repo, "remote", "add", "origin", "git@github.com:acme/repo.git")
    git(repo, "checkout", "-b", "feature/one")

    def fake_branch_payload(repo_dict, branch):
        if branch != "feature/one":
            return []
        return [{"number": 5, "state": "open", "draft": True, "title": "a PR", "html_url": ""}]

    monkeypatch.setattr(github_client, "github_pull_requests_by_branch_payload", fake_branch_payload)
    payload = {"sessions": {"1": _session_info_json("1", repo)}}
    result = json.loads(jobd.run_registered_task("metadata_warm_view", json.dumps(payload).encode("utf-8")))

    assert result["truncated"] is False
    matches = {key: value for key, value in result["entries"].items() if key.startswith("github-pr-branch:acme/repo:feature/one")}
    assert matches
    entry = next(iter(matches.values()))
    assert entry["value"][0]["number"] == 5
    assert 0 < entry["ttl_remaining"] <= metadata_module.METADATA_CACHE_TTL_SECONDS
    assert result["profile"]["work"]["sessions"] == 1
    assert result["profile"]["work"]["jobd_work_graph_rebuild"] == 1
    assert result["profile"]["work"]["provider_metadata_rebuild"] == 1
    assert result["profile"]["work"]["git_spawns"] > 0
    assert result["profile"]["work"]["github_http_calls"] == 0
    assert result["profile"]["work"]["linear_http_calls"] == 0
    # Running it again with the same fake network response reproduces the same materialized value
    # (a fresh worker-local cache each run, never carried over from a prior invocation).
    again = json.loads(jobd.run_registered_task("metadata_warm_view", json.dumps(payload).encode("utf-8")))
    again_matches = {key: value for key, value in again["entries"].items() if key.startswith("github-pr-branch:acme/repo:feature/one")}
    assert next(iter(again_matches.values()))["value"] == entry["value"]


def test_metadata_warm_view_task_rejects_malformed_or_oversized_payload():
    with pytest.raises(ValueError):
        jobd.run_registered_task("metadata_warm_view", json.dumps({"sessions": "not-an-object"}).encode("utf-8"))
    too_many = {str(index): {} for index in range(metadata_module.METADATA_WARM_VIEW_MAX_SESSIONS + 1)}
    with pytest.raises(ValueError):
        jobd.run_registered_task("metadata_warm_view", json.dumps({"sessions": too_many}).encode("utf-8"))
    with pytest.raises(ValueError):
        jobd.run_registered_task("metadata_warm_view", b"{" + b" " * (jobd.JOBD_MAX_PAYLOAD_BYTES + 1))


def test_metadata_warm_view_bounds_result_by_evicting_lowest_ttl_entries_first(monkeypatch):
    def fake_session_work_graph(info, cache, allow_network=True):
        for index in range(200):
            cache.set(f"github-pr:acme/repo:{info.session}:{index}", {"number": index, "title": "x" * 128}, ttl=10.0 + index)
        return {}

    monkeypatch.setattr(metadata_module, "session_work_graph", fake_session_work_graph)
    payload = {"sessions": {"1": _session_info_json("1", "/repo")}}
    result = metadata_module.metadata_warm_view_result(payload, max_bytes=2048)

    assert result["truncated"] is True
    assert len(result["entries"]) < 200
    assert len(json.dumps(result, separators=(",", ":")).encode("utf-8")) <= 2048
    # The lowest-remaining-TTL entries (index 0, 1, ...) are the ones evicted first.
    assert "github-pr:acme/repo:1:0" not in result["entries"]


def _wait_for_result(client: jobd.JobClient, job_id: str, *, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.request({"action": "result", "job_id": job_id})
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        if job.get("status") in {"completed", "failed", "cancelled", "superseded"}:
            return response
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not settle")


@pytest.mark.gate_serial
def test_jobd_control_plane_is_ready_before_blocked_data_plane_setup(tmp_path, monkeypatch):
    socket_path = tmp_path / "jobd.sock"
    executor_setup_started = threading.Event()
    release_executor_setup = threading.Event()
    priority_calls = []
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)

    def blocked_executor_setup(_worker_count):
        executor_setup_started.set()
        assert release_executor_setup.wait(5.0)
        raise RuntimeError("fixture executor setup failure")

    monkeypatch.setattr(service, "_new_executor", blocked_executor_setup)
    monkeypatch.setattr(jobd, "apply_service_process_priority", lambda: priority_calls.append(threading.current_thread().name) or True)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not client.registry.healthy():
        time.sleep(0.01)
    assert client.registry.healthy() is True
    deadline = time.monotonic() + 1.0
    while service.scheduler_thread is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.scheduler_thread is not None
    assert priority_calls == ["jobd-scheduler"]

    submitted = client.submit("json_compact", {"ready": True}, priority="interactive", coalesce_key="blocked-setup")
    assert submitted["ok"] is True
    assert executor_setup_started.wait(1.0)
    assert client.registry.healthy() is True
    assert priority_calls == ["jobd-scheduler"]

    release_executor_setup.set()
    assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


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
    produced, produced_body = client.produce(
        "json_compact",
        {"z": 1, "a": [2]},
        priority="interactive",
        generation=3,
        coalesce_key="fixture",
        deadline_ms=5_000,
    )
    status = client.request({"action": "status"})

    assert rejected == {"ok": False, "error": "unknown task"}
    assert first["ok"] is True and first["coalesced"] is False
    assert duplicate["ok"] is True and duplicate["coalesced"] is True
    assert result["job"]["status"] == "completed"
    assert result["job"]["result"] == {"a": [2], "z": 1}
    assert produced["state"] == "ready"
    assert json.loads(produced_body) == {"a": [2], "z": 1}
    assert status["queues"] == {"point": 0, "mutation": 0, "interactive": 0, "freshness": 0, "maintenance": 0}
    assert status["lanes"] == {
        "point": {"capacity": jobd.JOBD_POINT_WORKERS, "active": 0, "queued": 0},
        "mutation": {"capacity": jobd.JOBD_MUTATION_WORKERS, "active": 0, "queued": 0},
        "interactive": {"capacity": jobd.JOBD_INTERACTIVE_WORKERS, "active": 0, "queued": 0},
        "bulk": {"capacity": 1, "active": 0, "queued": 0},
    }
    assert status["cache"]["records"] == 1
    assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_registry_launched_jobd_executes_a_spawn_worker(tmp_path):
    """The daemon's redirected stdio must remain valid for macOS spawn workers."""
    client = jobd.JobClient(tmp_path / "jobd.sock")
    assert client.start_for_scheduler() is True
    coalesce_key = "registry-spawn-worker"
    try:
        submitted = client.submit(
            "json_compact", {"z": 1, "a": [2]}, priority="interactive", generation=1, coalesce_key=coalesce_key,
        )
        assert submitted["ok"] is True
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            metadata, body = client.product(coalesce_key)
            if body:
                assert metadata["state"] == "ready"
                assert json.loads(body) == {"a": [2], "z": 1}
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"registry-launched jobd did not complete: {client.request({'action': 'status'})}")
    finally:
        assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}


def test_scheduler_started_jobd_holds_a_lease_until_scheduler_stop(tmp_path):
    client = jobd.JobClient(tmp_path / "jobd.sock")
    assert client.start_for_scheduler() is True
    try:
        assert client.request({"action": "status"})["clients"] == 1
        assert client.start_for_scheduler() is True
        assert client.request({"action": "status"})["clients"] == 1
        assert client.stop_for_scheduler() is True
        assert client.request({"action": "status"})["clients"] == 0
    finally:
        client.request({"action": "shutdown"})


def test_registry_launched_jobd_spawn_worker_survives_closed_parent_stdin(tmp_path):
    """A nohup/launchd-style closed stdin must not crash a macOS spawn worker."""
    socket_path = tmp_path / "closed-stdin-jobd.sock"
    script = """
import json
import os
import sys
import time
from pathlib import Path
from yolomux_lib import jobd

os.close(0)
client = jobd.JobClient(Path(sys.argv[1]))
if not client.start_for_scheduler():
    raise SystemExit("jobd did not start")
try:
    response = client.submit("json_compact", {"z": 1, "a": [2]}, priority="interactive", generation=1, coalesce_key="closed-stdin")
    if not response.get("ok"):
        raise SystemExit(f"submit failed: {response}")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        metadata, body = client.product("closed-stdin")
        if body:
            print(json.dumps({"metadata": metadata, "result": json.loads(body)}))
            break
        time.sleep(0.02)
    else:
        raise SystemExit(f"product did not complete: {client.request({'action': 'status'})}")
finally:
    client.request({"action": "shutdown"})
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(socket_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["metadata"].items() >= {"ok": True, "state": "ready", "generation": 1, "inflight": False}.items()
    assert result["result"] == {"a": [2], "z": 1}


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


def test_two_ports_coalesce_one_session_files_snapshot_product(tmp_path):
    """Two web ports submit one session-files product and share one Git snapshot worker run."""
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 9\n", encoding="utf-8")
    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    port_a = jobd.JobClient(socket_path)
    port_b = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not port_a.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    payload = {
        "session": "a",
        "infos": {"a": _session_info_json("a", repo)},
        "hours": 24.0,
        "include_cross_session_attribution": False,
        "repository_states": [{"path": str(repo), "generation": 4}],
    }
    product_key = "session-files:v1:two-ports"
    first = port_a.submit("session_files_view", payload, generation=4, coalesce_key=product_key)
    second = port_b.submit("session_files_view", payload, generation=4, coalesce_key=product_key)
    _wait_for_result(port_a, first["job"]["job_id"], timeout_seconds=20.0)
    meta_a, body_a = port_a.product(product_key)
    meta_b, body_b = port_b.product(product_key)
    status = port_a.request({"action": "status"})
    assert port_a.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)

    assert first["coalesced"] is False
    assert second["coalesced"] is True
    assert status["product_counters"]["session_files_view"]["completed"] == 1
    assert meta_a["state"] == meta_b["state"] == "ready"
    assert body_a == body_b and body_a is not None
    assert json.loads(body_a)["profile"]["work"]["git_snapshots"] == 1


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


def test_jobd_submission_encodes_payload_once_and_preserves_exact_boundary_and_default_key(tmp_path, monkeypatch):
    empty = json.dumps(
        {"text": ""},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = {"text": "x" * (jobd.JOBD_MAX_PAYLOAD_BYTES - len(empty))}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    oversized_payload = {"text": payload["text"] + "x"}
    assert len(encoded) == jobd.JOBD_MAX_PAYLOAD_BYTES

    original_dumps = jobd.json.dumps
    payload_encodes = 0

    def counted_dumps(value, *args, **kwargs):
        nonlocal payload_encodes
        if value is payload:
            payload_encodes += 1
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(jobd.json, "dumps", counted_dumps)
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)

    accepted = service._submit({
        "task": "text_facts",
        "payload": payload,
        "priority": "interactive",
    })
    rejected = service._submit({
        "task": "text_facts",
        "payload": oversized_payload,
        "priority": "interactive",
    })

    assert accepted["ok"] is True and accepted["coalesced"] is False
    assert rejected == {"ok": False, "error": "payload too large"}
    assert payload_encodes == 1
    assert len(service.records) == 1
    record = next(iter(service.records.values()))
    assert record.payload == encoded
    assert record.coalesce_key == f"text_facts:{encoded.hex()}"[:256]


def test_jobd_prevents_maintenance_starvation_and_times_out_before_worker_start(tmp_path, monkeypatch):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    interactive = [
        service._queue_record("text_facts", {"text": f"interactive-{number}"}, "interactive", number, f"interactive-{number}")
        for number in range(jobd.JOBD_INTERACTIVE_WORKERS + 1)
    ]
    maintenance = service._queue_record("text_facts", {"text": "maintenance"}, "maintenance", 1, "maintenance")
    expired = service._queue_record("text_facts", {"text": "expired"}, "freshness", 1, "expired", deadline_at=time.monotonic() - 1.0)

    class Executor:
        def submit(self, *_args):
            return Future()

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    service._pump()

    assert interactive[0].status == "running"
    assert interactive[1].status == "queued"
    assert maintenance.status == "running"
    assert expired.status == "timed_out"
    assert expired.error == "deadline exceeded before execution"
    assert service._submit({"task": "text_facts", "payload": {"text": "late"}, "deadline_ms": jobd.JOBD_MAX_DEADLINE_MS + 1}) == {"ok": False, "error": "deadline too large"}


def test_jobd_general_saturation_does_not_block_interactive_dispatch(tmp_path, monkeypatch):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    for number in range(service.general_worker_count):
        blocking = service._queue_record("text_facts", {"text": f"background-{number}"}, "freshness", number, f"background-{number}")
        blocking.status = "running"
        blocking.future = Future()
    submitted_future = Future()

    class Executor:
        def submit(self, *_args):
            return submitted_future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    interactive = service._queue_record("json_compact", {"interactive": True}, "interactive", 1, "interactive")

    started = time.monotonic()
    service._pump()

    assert interactive.status == "running"
    assert interactive.future is submitted_future
    assert time.monotonic() - started < jobd.JOBD_SCHEDULER_POLL_SECONDS


def test_jobd_interactive_saturation_queues_until_reserved_capacity_is_released(tmp_path, monkeypatch):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    submitted_futures: list[Future] = []

    class Executor:
        def submit(self, *_args):
            future = Future()
            submitted_futures.append(future)
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    first = service._queue_record("json_compact", {"order": 1}, "interactive", 1, "interactive-1")
    second = service._queue_record("json_compact", {"order": 2}, "interactive", 1, "interactive-2")

    service._pump()

    assert first.status == "running"
    assert second.status == "queued"
    assert len(submitted_futures) == 1

    submitted_futures[0].set_result(b'{"order":1}')
    service._pump()

    assert first.status == "completed"
    assert second.status == "running"
    assert len(submitted_futures) == 2


def test_jobd_point_lane_dispatches_while_every_bulk_and_interactive_slot_is_held(tmp_path, monkeypatch):
    """A held bulk job must not put an editor open or an index probe behind it."""
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    holders = []
    for number in range(service.general_worker_count):
        holder = service._queue_record("text_facts", {"text": f"bulk-{number}"}, "freshness", number, f"bulk-{number}")
        holder.status = "running"
        holder.future = Future()
        holders.append(holder)
    for number in range(jobd.JOBD_INTERACTIVE_WORKERS):
        holder = service._queue_record("text_facts", {"text": f"batch-{number}"}, "interactive", number, f"batch-{number}")
        holder.status = "running"
        holder.future = Future()
        holders.append(holder)
    lanes_by_submission: list[str] = []

    class Executor:
        def submit(self, *_args):
            return Future()

    monkeypatch.setattr(service, "_executor", lambda priority="freshness": (
        lanes_by_submission.append(jobd.PersistentJobBroker._lane_for_priority(priority)) or Executor()
    ))
    read = service._queue_record("filesystem_operation", {"op": "read"}, "point", 1, "point-read")
    index_status = service._queue_record("filesystem_operation", {"op": "index_status"}, "point", 1, "point-index")

    service._pump()

    assert [holder.status for holder in holders] == ["running"] * len(holders)
    assert read.status == "running"
    assert index_status.status == "running"
    assert lanes_by_submission == ["point", "point"]
    status = service.common_status()
    assert status["lanes"]["point"] == {"capacity": jobd.JOBD_POINT_WORKERS, "active": 2, "queued": 0}
    assert status["lanes"]["bulk"]["active"] == service.general_worker_count
    assert status["lanes"]["interactive"]["active"] == jobd.JOBD_INTERACTIVE_WORKERS


def test_jobd_point_lane_capacity_is_bounded_and_releases_in_order(tmp_path, monkeypatch):
    """Point capacity is explicitly bounded: one slow point read cannot strand the rest, and
    point work cannot become unbounded process capacity of its own."""
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    submitted_futures: list[Future] = []

    class Executor:
        def submit(self, *_args):
            future = Future()
            submitted_futures.append(future)
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    points = [
        service._queue_record("json_compact", {"order": order}, "point", 1, f"point-{order}")
        for order in range(jobd.JOBD_POINT_WORKERS + 1)
    ]

    service._pump()

    assert [record.status for record in points[:jobd.JOBD_POINT_WORKERS]] == ["running"] * jobd.JOBD_POINT_WORKERS
    assert points[-1].status == "queued"
    assert len(submitted_futures) == jobd.JOBD_POINT_WORKERS
    assert service.common_status()["lanes"]["point"] == {
        "capacity": jobd.JOBD_POINT_WORKERS,
        "active": jobd.JOBD_POINT_WORKERS,
        "queued": 1,
    }

    submitted_futures[0].set_result(b'{"order":0}')
    service._pump()

    assert points[0].status == "completed"
    assert points[-1].status == "running"
    assert len(submitted_futures) == jobd.JOBD_POINT_WORKERS + 1


def test_jobd_every_declared_priority_is_owned_by_exactly_one_bounded_lane(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)

    assert set(jobd.JOBD_PRIORITIES) == set(jobd.JOBD_PRIORITY_LANES)
    assert jobd.JOBD_PRIORITIES == tuple(jobd.JOBD_PRIORITY_LANES)
    assert set(jobd.JOBD_PRIORITY_LANES.values()) == set(jobd.JOBD_LANE_PRIORITIES)
    assert all(service._lane_capacity(lane) >= 1 for lane in jobd.JOBD_LANE_PRIORITIES)
    with pytest.raises(ValueError, match="no jobd lane owns priority"):
        jobd.PersistentJobBroker._lane_for_priority("nonexistent")
    assert service._submit({"task": "text_facts", "payload": {"text": "x"}, "priority": "nonexistent"}) == {
        "ok": False,
        "error": "invalid priority",
    }


def test_point_read_admits_against_its_own_lane_while_the_bulk_queue_is_full(tmp_path):
    """A full bulk/freshness queue must not refuse an idle point read as `queue full`.

    Before the per-lane cap, one global `JOBD_MAX_QUEUE` sat ahead of every lane: 64 queued
    freshness records made a fresh point submission return `queue full` while the point lane read
    capacity 2, active 0.  The cap is per-lane now, so each lane stays bounded (the backpressure
    intent) without one lane's queue starving another's admission.
    """
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    for number in range(jobd.JOBD_MAX_QUEUE):
        submission, error = jobd.PersistentJobBroker._validated_submission({
            "task": "session_files_view", "priority": "freshness",
            "payload": {"session": f"s{number}"}, "generation": 1,
            "coalesce_key": f"freshness-{number}", "deadline_ms": 60_000,
        })
        assert error is None, error
        assert service._submit_validated(submission)["ok"] is True

    # The freshness (bulk) lane's queue is full; the point lane is idle.
    assert service._queued_count(lane="bulk") >= jobd.JOBD_MAX_QUEUE
    assert service._queued_count(lane="point") == 0
    assert service._future_slots(lane="point") == 0

    submission, error = jobd.PersistentJobBroker._validated_submission({
        "task": "session_files_view", "priority": "point",
        "payload": {"session": "point-read"}, "generation": 1,
        "coalesce_key": "point-read", "deadline_ms": 60_000,
    })
    assert error is None, error
    result = service._submit_validated(submission)
    assert result["ok"] is True, result
    assert result["coalesced"] is False
    # And the point lane's own cap still holds against a flood of point submissions.
    assert service._queued_count(lane="point") == 1


def test_jobd_fresh_only_joins_in_flight_work_but_never_serves_a_stored_product(tmp_path, monkeypatch):
    """The mtime-granularity case: one coalesce key, two different contents.

    A stat identity is only as fine as the filesystem timestamp tick, so a rewrite inside one tick
    that keeps the same size produces an identical key for different bytes.  A `fresh_only`
    submission must therefore refuse the stored product while still joining in-flight work.
    """
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    submitted_futures: list[Future] = []

    class Executor:
        def submit(self, *_args):
            future = Future()
            submitted_futures.append(future)
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    key = "filesystem-operation:same-tick-same-size"
    submission = {
        "task": "json_compact", "payload": {"op": "read", "path": "/repo/note.md"},
        "priority": "point", "generation": 1, "coalesce_key": key, "delivery": "ready_or_receipt",
    }

    # First read completes and stores a product under the key.
    first, _first_body = service._produce(dict(submission))
    service._pump()
    submitted_futures[0].set_result(b'{"content":"before"}')
    service._pump()
    stored_metadata, stored_body = service._product({"coalesce_key": key})
    assert first["coalesced"] is False
    assert stored_metadata["state"] == "ready" and stored_body == b'{"content":"before"}'

    # The file is rewritten inside the same mtime tick with the same size, so the key is unchanged.
    reused, reused_body = service._produce(dict(submission))
    assert reused["state"] == "ready", "the default path deliberately reuses a retained product"
    assert reused_body == b'{"content":"before"}'

    fresh, fresh_body = service._produce(dict(submission, fresh_only=True))
    assert fresh_body == b"", "fresh_only must not hand back the retained product"
    assert fresh["state"] == "queued"
    assert fresh["coalesced"] is False, "a completed record must not satisfy a fresh_only submission"
    service._pump()
    assert len(submitted_futures) == 2, "fresh_only must run the work again"

    # A second fresh_only submission while that work is in flight still coalesces: in-flight work
    # has produced nothing, so it cannot be stale.
    joined, _joined_body = service._produce(dict(submission, fresh_only=True))
    assert joined["coalesced"] is True
    assert joined["job"]["job_id"] == fresh["job"]["job_id"]
    service._pump()
    assert len(submitted_futures) == 2

    # While the fresh job is in flight the waiter must not accept the older stored bytes.
    inflight_metadata, _inflight_body = service._product({"coalesce_key": key})
    assert inflight_metadata["state"] == "stale" and inflight_metadata["inflight"] is True

    submitted_futures[1].set_result(b'{"content":"after"}')
    service._pump()
    final_metadata, final_body = service._product({"coalesce_key": key})
    assert final_metadata["state"] == "ready"
    assert final_body == b'{"content":"after"}'


def test_jobd_coalesces_identical_in_flight_point_reads_into_one_execution(tmp_path, monkeypatch):
    """Repeated identical point reads share one execution and every receipt names that job."""
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    submitted_futures: list[Future] = []

    class Executor:
        def submit(self, *_args):
            future = Future()
            submitted_futures.append(future)
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    submission = {
        "task": "json_compact",
        "payload": {"op": "read", "path": "/repo/note.md"},
        "priority": "point",
        "generation": 1,
        "coalesce_key": "filesystem-operation:content-identity",
        "delivery": "receipt",
    }

    first, _first_body = service._produce(dict(submission))
    service._pump()
    second, _second_body = service._produce(dict(submission))
    third, _third_body = service._produce(dict(submission))

    assert first["coalesced"] is False
    assert second["coalesced"] is True and third["coalesced"] is True
    job_ids = {response["job"]["job_id"] for response in (first, second, third)}
    assert len(job_ids) == 1
    assert len(submitted_futures) == 1
    assert service.product_counters["json_compact"]["accepted"] == 1
    assert service.product_counters["json_compact"]["coalesced"] == 2

    submitted_futures[0].set_result(b'{"content":"body"}')
    service._pump()

    metadata, body = service._product({"coalesce_key": submission["coalesce_key"]})
    assert metadata["state"] == "ready"
    assert body == b'{"content":"body"}'
    assert metadata["schedule"]["lane"] == "point"
    assert metadata["schedule"]["task"] == "json_compact"
    assert metadata["schedule"]["queue_wait_ms"] >= 0.0
    assert metadata["schedule"]["execution_ms"] >= 0.0
    assert metadata["schedule"]["running_started_at"] > 0.0

    # A changed content identity is a different key, so it can never be answered by the retained
    # product above -- coalescing never serves bytes for content that has since changed.
    changed = dict(submission, coalesce_key="filesystem-operation:content-identity-2")
    changed_metadata, changed_body = service._product({"coalesce_key": changed["coalesce_key"]})
    assert changed_metadata["state"] == "none" and changed_body == b""


@pytest.mark.parametrize("task", ["session_files_view", "metadata_warm_view"])
def test_jobd_completion_validates_and_aggregates_json_result_with_one_parse(tmp_path, monkeypatch, task):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    result = json.dumps({
        "profile": {"phases": {}, "work": {"sessions": 1}},
    }).encode("utf-8")
    decoded_inputs = []
    real_loads = jobd.json.loads

    def counted_loads(value, *args, **kwargs):
        decoded_inputs.append(value)
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(jobd.json, "loads", counted_loads)
    completed = service._queue_record(task, {}, "maintenance", 1, f"{task}:completed")
    completed.status = "running"
    completed.future = Future()
    completed.future.set_result(result)
    service._pump()

    malformed = service._queue_record(task, {}, "maintenance", 2, f"{task}:malformed")
    malformed.status = "running"
    malformed.future = Future()
    malformed.future.set_result(b"not-json")
    service._pump()

    assert completed.status == "completed"
    assert malformed.status == "failed"
    assert "Expecting value" in malformed.error
    assert decoded_inputs == [result.decode("utf-8"), "not-json"]
    assert service.product_counters[task]["completed"] == 1
    assert service.product_counters[task]["failed"] == 1
    assert service.product_work_totals[task] == {"sessions": 1}


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


def test_jobd_marks_filesystem_worker_failure_terminal_and_continues_serving(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    poisoned = service._queue_record("session_files_view", {}, "interactive", 1, "retired-root")
    poisoned.status = "running"
    poisoned.future = Future()
    poisoned.future.set_exception(FilesystemError.path_not_found("/gone/worktree"))

    service._pump()

    assert poisoned.status == "failed"
    assert poisoned.error == "FilesystemError: path not found: /gone/worktree"
    assert service.common_status()["scheduler_pump"]["failures"] == 0
    survivor = service._queue_record("text_facts", {"text": "still-serving"}, "interactive", 2, "survivor")
    survivor.status = "running"
    survivor.future = Future()
    survivor.future.set_result(b'{"bytes":13,"lines":1,"nonempty_lines":1}')
    service._pump()
    assert survivor.status == "completed"


def test_jobd_enforces_queue_saturation_deadlines_and_recovers_a_broken_executor(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    occupying = service._queue_record("text_facts", {"text": "active"}, "freshness", 1, "active")
    occupying.status = "running"
    occupying.future = Future()
    for number in range(jobd.JOBD_MAX_QUEUE):
        queued = service._queue_record("text_facts", {"text": str(number)}, "freshness", number, f"queue-{number}")
        queued.status = "queued"

    assert service._submit({"task": "text_facts", "payload": {"text": "overflow"}}) == {"ok": False, "error": "queue full"}
    assert service._submit({"task": "text_facts", "payload": {"text": "invalid"}, "deadline_ms": "tomorrow"}) == {"ok": False, "error": "invalid generation or deadline"}
    assert service._submit({"task": "text_facts", "payload": {"text": "negative"}, "deadline_ms": -1}) == {"ok": False, "error": "invalid deadline"}
    lease_record = runtime.current_host_identity().process_record_fields()
    service.leases = {str(number): dict(lease_record) for number in range(runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES)}
    lease_response, _binary = service.handle({"action": "lease", "client_pid": os.getpid()})
    assert lease_response == {"ok": False, "error": "too many clients", "leases": runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES, "version": jobd.JOBD_PROTOCOL_VERSION}

    broken = service._queue_record("text_facts", {"text": "crash"}, "interactive", 999, "crash")
    broken.status = "running"
    broken.future = Future()
    broken.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def shutdown(self, **_kwargs):
            return None

    service.executors["interactive"] = BrokenExecutor()  # type: ignore[assignment]
    service._pump()

    assert broken.status == "failed"
    assert broken.error == "worker crashed"
    assert service.executors["interactive"] is None


def test_jobd_rejects_newer_protocol_before_dispatch(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)

    response, binary = service.handle({"action": "ping", "protocol_version": jobd.JOBD_PROTOCOL_VERSION + 1})

    assert binary == b""
    assert response == {
        "ok": False,
        "error": "upgrade_required",
        "required_protocol_version": jobd.JOBD_PROTOCOL_VERSION,
    }


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


@pytest.mark.parametrize("priority", ["interactive", "freshness"])
def test_jobd_timed_out_running_work_keeps_its_slot_and_recovers_after_worker_exit(tmp_path, priority):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    timed_out = service._queue_record("text_facts", {"text": "slow"}, priority, 1, "slow", deadline_at=time.monotonic() - 1.0)
    timed_out.status = "running"
    timed_out.future = Future()
    waiting = service._queue_record("text_facts", {"text": "wait"}, priority, 1, "wait")

    service._pump()

    assert timed_out.status == "timed_out"
    assert service.common_status()["product_counters"]["text_facts"]["timed_out"] == 1
    # A timed-out job is HISTORICAL work failure and must not read as a CURRENT daemon
    # failure. Publishing it as `last_failure` pinned a healthy, serving jobd to
    # degraded/terminal_failure in the health observer - permanently, because nothing
    # clears it. Two meanings, two names; both directions asserted.
    _status = service.common_status()
    assert _status["last_job_failure"] == "deadline exceeded while executing"
    assert not _status.get("last_failure"), _status.get("last_failure")
    assert waiting.status == "queued"
    timed_out.future.set_result(b'{"bytes":4,"lines":1,"nonempty_lines":1}')
    service._pump()

    assert timed_out.future is None
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

    service.executors["interactive"] = BrokenExecutor()  # type: ignore[assignment]
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
    # v5 registered the `tabber_activity_view` task; the fence retires a v4 daemon that lacks it.
    # v6 registered the `metadata_warm_view` task; v7 adds bounded session-files phase diagnostics;
    # v8 bounds snapshot expiry, v9 adds bounded requester attribution, v10 adds metadata-warm work totals, v11 exposes timeouts, v12 records requester attribution at acceptance, v13 projects bounded recent paths for Tabber, v14 adds zero-wait ready-or-receipt products, v15 registers bounded filesystem batches, v16 keeps cold worker starts out of RPC handlers, v17 moves session-files cache pruning out of the web process, v18 adds byte-product relay requests for browser filesystem consumers, v19 adds the bounded `point` scheduler lane that a v18 daemon would reject as an invalid priority, v20 binds filesystem execution to the accepting server's access policy, which a v19 daemon ignores while authorizing every port with its launcher's roots, v21 adds the bounded `mutation` scheduler lane that a v20 daemon would likewise reject as an invalid priority, v22 retires the blocking `relay` action, v23 adds private file-backed artifacts, and v24 registers queued-delivery compaction.
    assert jobd.JOBD_PROTOCOL_VERSION == 24
    assert "relay" not in jobd.JOBD_REQUEST_ACTIONS
    assert "filesystem_batch" in jobd.REGISTERED_TASKS
    assert "session_files_cache_prune" in jobd.REGISTERED_TASKS
    assert "session_files_view" in jobd.REGISTERED_TASKS
    assert "tabber_activity_view" in jobd.REGISTERED_TASKS
    assert "metadata_warm_view" in jobd.REGISTERED_TASKS
    assert "queued_delivery_compact" in jobd.REGISTERED_TASKS
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
    # The diagnostics surface (checkbox 10 age/stale-state) counts this honestly.
    assert service.common_status()["cache"]["products_stale"] == 1

    # Once the newer generation completes, the stored product is current again.
    newer.future.set_result(b'{"a":2}')
    service._pump()
    assert service.common_status()["cache"]["products_stale"] == 0


def test_jobd_produce_preserves_one_bounded_batch_product_and_caller_delivery_mode(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    service._pump = lambda: None
    items = [{"id": f"item-{index}", "type": "info", "path": f"/repo/{index}"} for index in range(64)]
    ready_body = json.dumps({"results": items}, separators=(",", ":")).encode("utf-8")
    product = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(ready_body),
        "sha256": hashlib.sha256(ready_body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    service.product_store.store_inline(key="fs-batch", generation=7, body=ready_body, product=product, schedule={})

    ready_meta, forwarded = service._produce({
        "task": "filesystem_batch",
        "payload": {"requests": items},
        "priority": "interactive",
        "generation": 7,
        "coalesce_key": "fs-batch",
        "deadline_ms": 15_000,
        "delivery": "ready_or_receipt",
    })
    receipt_meta, receipt_body = service._produce({
        "task": "filesystem_batch",
        "payload": {"requests": items},
        "priority": "interactive",
        "generation": 8,
        "coalesce_key": "fs-batch-new",
        "deadline_ms": 15_000,
        "delivery": "receipt",
    })

    assert ready_meta["state"] == "ready"
    assert ready_meta["job"]["generation"] == 7
    assert ready_meta["product"] == service.product_store.inline_metadata("fs-batch")
    assert json.loads(forwarded)["results"] == items
    assert receipt_meta["state"] == "queued"
    assert receipt_meta["job"]["generation"] == 8
    assert receipt_body == b""
    assert json.loads(service.records[receipt_meta["job"]["job_id"]].payload)["requests"] == items


def test_filesystem_batch_task_preserves_64_item_ids_and_results(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "entry.txt").write_text("one\n", encoding="utf-8")
    requests = [
        {
            "id": f"request-{index}",
            "type": "list" if index % 2 == 0 else "info",
            "path": str(root),
            "trigger_counts": {"tree-render": 1},
            "include_watch_signature": index == 0,
        }
        for index in range(64)
    ]

    result = json.loads(jobd.run_registered_task(
        "filesystem_batch",
        json.dumps(_fs_batch_payload(requests=requests, client_scope="browser")).encode("utf-8"),
    ))

    assert [response["id"] for response in result["responses"]] == [request["id"] for request in requests]
    assert all(response["ok"] is True for response in result["responses"])
    assert all(response["payload"]["path"] == str(root) for response in result["responses"])
    assert result["responses"][0]["watch_signature"][0] == str(root)
    assert all("watch_signature" not in response for response in result["responses"][1:])
    with pytest.raises(ValueError, match="at most 64"):
        jobd.run_registered_task(
            "filesystem_batch",
            json.dumps(_fs_batch_payload(requests=[*requests, {"id": "overflow", "type": "info", "path": str(root)}])).encode("utf-8"),
        )


def test_session_files_cache_prune_task_removes_expired_payload_and_manifest(tmp_path):
    cache_dir = tmp_path / "session-files-cache"
    cache_dir.mkdir()
    payload_path = cache_dir / "expired.json"
    manifest_path = cache_dir / "expired.manifest.json"
    payload_path.write_text("payload", encoding="utf-8")
    manifest_path.write_text("manifest", encoding="utf-8")
    os.utime(payload_path, (10.0, 10.0))
    os.utime(manifest_path, (10.0, 10.0))

    result = json.loads(jobd.run_registered_task(
        "session_files_cache_prune",
        json.dumps({
            "cache_dir": str(cache_dir),
            "max_age_seconds": 1.0,
            "max_bytes": 1024,
            "batch_size": 8,
        }).encode("utf-8"),
    ))

    assert result["entries"] == 1
    assert result["removed_entries"] == 1
    assert result["removed_files"] == 2
    assert payload_path.exists() is False
    assert manifest_path.exists() is False


def test_filesystem_batch_task_uses_the_bounded_binary_product_budget(monkeypatch):
    large_payload = "x" * (jobd.JOBD_MAX_RESULT_BYTES + 1024)
    monkeypatch.setattr(
        jobd.filesystem,
        "filesystem_batch_result",
        lambda _payload: {"responses": [{"id": "large", "ok": True, "payload": {"text": large_payload}}]},
    )

    result = jobd.run_registered_task("filesystem_batch", json.dumps(_fs_batch_payload(requests=[])).encode("utf-8"))

    assert len(result) > jobd.JOBD_MAX_RESULT_BYTES
    assert len(result) <= jobd.JOBD_MAX_FILESYSTEM_BATCH_RESULT_BYTES


def test_jobd_produce_executes_one_typed_64_item_filesystem_batch(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    requests = [
        {"id": f"item-{index}", "type": "info", "path": str(root), "trigger_counts": {"tree-render": 1}}
        for index in range(64)
    ]
    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    metadata, body = client.produce(
        "filesystem_batch",
        _fs_batch_payload(requests=requests, client_scope="browser"),
        priority="interactive",
        generation=1,
        coalesce_key="filesystem-batch-integration",
        deadline_ms=15_000,
        delivery="receipt",
    )
    assert metadata["state"] == "queued"
    assert body == b""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        product, body = client.product("filesystem-batch-integration")
        if product.get("state") == "ready" and body:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"filesystem batch did not settle: {client.request({'action': 'status'})}")

    result = json.loads(body)
    assert [response["id"] for response in result["responses"]] == [request["id"] for request in requests]
    assert all(response["ok"] is True for response in result["responses"])
    assert len([record for record in service.records.values() if record.task == "filesystem_batch"]) == 1
    assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_jobd_produce_receipt_does_not_wait_for_cold_executor_start(tmp_path, monkeypatch):
    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    executor_start_entered = threading.Event()
    release_executor_start = threading.Event()
    original_executor = service._executor

    def delayed_executor_start(priority="freshness"):
        executor_start_entered.set()
        assert release_executor_start.wait(2.0)
        return original_executor(priority)

    monkeypatch.setattr(service, "_executor", delayed_executor_start)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        started = time.monotonic()
        metadata, body = client.produce(
            "filesystem_batch",
            {"requests": [{"id": "root", "type": "info", "path": str(tmp_path)}]},
            priority="interactive",
            generation=1,
            coalesce_key="cold-filesystem-batch",
            deadline_ms=5_000,
            delivery="receipt",
            timeout=0.1,
        )
        elapsed = time.monotonic() - started

        assert metadata["ok"] is True
        assert metadata["state"] == "queued"
        assert body == b""
        assert elapsed < 0.1
        assert executor_start_entered.wait(1.0)
        product_started = time.monotonic()
        product, product_body = client.product("cold-filesystem-batch", timeout=0.1)
        assert product["state"] == "pending"
        assert product_body == b""
        assert time.monotonic() - product_started < 0.1
    finally:
        release_executor_start.set()
        deadline = time.monotonic() + 2.0
        while not client.registry.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)
        client.request({"action": "shutdown"}, timeout=2.0)
        worker.join(timeout=2.0)


def test_filesystem_batch_large_real_rpc_uses_binary_product_not_result_metadata(tmp_path):
    root = tmp_path / "workspace" / "projects" / "long-provider-repository-name" / "nested-components"
    root.mkdir(parents=True)
    requests = []
    for index in range(64):
        directory = root / f"component-{index:02d}-with-a-realistic-long-directory-name"
        directory.mkdir()
        for entry_index in range(48):
            (directory / f"generated-client-artifact-{entry_index:03d}-with-descriptive-name.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
        requests.append({
            "id": f"directory-{index:02d}",
            "type": "list",
            "path": str(directory),
            "trigger_counts": {"tree-render": 1},
        })
    payload = _fs_batch_payload(requests=requests, client_scope="browser")
    request_bytes = len(json.dumps({
        "action": "produce",
        "task": "filesystem_batch",
        "payload": payload,
        "priority": "interactive",
        "generation": 1,
        "coalesce_key": "large-filesystem-batch",
        "deadline_ms": 15_000,
        "delivery": "receipt",
        "allow_stale": False,
    }, separators=(",", ":")).encode("utf-8"))
    assert request_bytes < rpc.LOCAL_RPC_MAX_METADATA_BYTES

    socket_path = tmp_path / "jobd.sock"
    service = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = jobd.JobClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        metadata, body = client.produce(
            "filesystem_batch",
            payload,
            priority="interactive",
            generation=1,
            coalesce_key="large-filesystem-batch",
            deadline_ms=15_000,
            delivery="receipt",
            timeout=2.0,
        )
        assert metadata["state"] == "queued"
        assert body == b""
        job_id = metadata["job"]["job_id"]
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            product, body = client.product("large-filesystem-batch", timeout=2.0)
            if product.get("state") == "ready" and body:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"filesystem batch did not settle: {client.request({'action': 'status'}, timeout=2.0)}")

        result = json.loads(body)
        assert len(body) > rpc.LOCAL_RPC_MAX_METADATA_BYTES
        assert len(body) <= rpc.LOCAL_RPC_MAX_BINARY_BYTES
        assert [response["id"] for response in result["responses"]] == [request["id"] for request in requests]
        assert all(len(response["payload"]["entries"]) == 48 for response in result["responses"])
        oversized_result = client.result(job_id)
        assert oversized_result == {"ok": False, "error": "response too large"}
        assert service.request_counters["produce"] == 1
        assert service.request_counters["product"] >= 1
        assert service.request_counters["result"] == 1
    finally:
        client.request({"action": "shutdown"}, timeout=2.0)
        worker.join(timeout=2.0)


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
    assert service.product_store.inline_generation("k") == 2

    # A slow OLDER-generation completion must not replace the newer complete product.
    older.future.set_result(b'{"gen":1}')
    service._pump()
    assert service.product_store.inline_generation("k") == 2
    assert json.loads(service.product_store.inline_body("k")) == {"gen": 2}

    # A failed refresh must not replace it either.
    failing = service._queue_record("json_compact", {"gen": 3}, "freshness", 3, "k")
    failing.status = "running"
    failing.future = Future()
    failing.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def shutdown(self, **_kwargs):
            return None

    service.executors["bulk"] = BrokenExecutor()  # type: ignore[assignment]
    service._pump()
    assert failing.status == "failed"
    assert json.loads(service.product_store.inline_body("k")) == {"gen": 2}


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


def test_jobd_status_lists_all_running_records_without_product_payloads(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    first = service._queue_record("json_compact", {"first": True}, "interactive", 1, "first")
    second = service._queue_record("text_facts", {"second": True}, "freshness", 2, "second")
    for record in (first, second):
        record.status = "running"
        record.future = Future()

    status = service.common_status()

    assert status["active_task"] == "json_compact"
    assert [{key: item[key] for key in ("task", "priority", "generation", "status")} for item in status["active_records"]] == [
        {"task": "json_compact", "priority": "interactive", "generation": 1, "status": "running"},
        {"task": "text_facts", "priority": "freshness", "generation": 2, "status": "running"},
    ]
    assert status["worker_pids"] == []


def test_jobd_status_and_shutdown_cover_every_scheduler_lane_executor(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    shutdown_pids: list[int] = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

    class Executor:
        def __init__(self, pid):
            self.pid = pid
            self._processes = {pid: Process(pid)}

        def shutdown(self, **_kwargs):
            shutdown_pids.append(self.pid)

    service.executors["bulk"] = Executor(101)  # type: ignore[assignment]
    service.executors["interactive"] = Executor(102)  # type: ignore[assignment]
    service.executors["point"] = Executor(103)  # type: ignore[assignment]
    service.executors["mutation"] = Executor(104)  # type: ignore[assignment]

    status = service.common_status()
    service._on_shutdown()

    assert status["worker_count"] == (
        2 + jobd.JOBD_INTERACTIVE_WORKERS + jobd.JOBD_POINT_WORKERS + jobd.JOBD_MUTATION_WORKERS
    )
    assert status["worker_pids"] == [101, 102, 103, 104]
    assert sorted(shutdown_pids) == [101, 102, 103, 104]
    assert set(service.executors) == set(jobd.JOBD_LANE_PRIORITIES)
    assert all(executor is None for executor in service.executors.values())


def test_jobd_status_exposes_bounded_request_action_counters(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock")

    service.handle({"action": "ping"})
    service.handle({"action": "status"})
    service.handle({"action": "status"})
    service.handle({"action": "unbounded-client-input"})

    assert service.common_status()["request_counters"] == {"ping": 1, "status": 2, "unknown": 1}


def test_jobd_runtime_status_aggregates_broker_and_reported_workers(tmp_path, monkeypatch):
    client = jobd.JobClient(tmp_path / "jobd.sock")
    monkeypatch.setattr(client.registry, "status", lambda: {
        "healthy": True,
        "status": {
            "pid": 100,
            "started_at": 123.0,
            "worker_count": 2,
            "worker_pids": [101, 102],
            "owner_invocations": {"jobd_work_graph_rebuild": 7, "provider_metadata_rebuild": 3},
        },
    })
    captured = {}
    monkeypatch.setattr(
        client.registry,
        "resources_for_pids",
        lambda parent_pid, worker_pids: captured.update(parent_pid=parent_pid, worker_pids=worker_pids) or {
            "cpu_percent": 12.5, "rss_bytes": 300, "process_count": 3,
        },
    )

    status = client.runtime_status()

    assert captured == {"parent_pid": 100, "worker_pids": [101, 102]}
    assert status["started_at"] == 123.0
    assert status["worker_count"] == 2
    assert status["owner_invocations"] == {"jobd_work_graph_rebuild": 7, "provider_metadata_rebuild": 3}
    assert status["resources"] == {"cpu_percent": 12.5, "rss_bytes": 300, "process_count": 3}


def test_jobd_tracks_per_task_runtime_count_total_and_max(tmp_path, monkeypatch):
    # Per-product runtime totals/maxima (checkbox 10): pure execution duration, excluding queue
    # wait, tracked per task name and surfaced through common_status/runtime_status.
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=2)
    clock_state = {"now": 100.0}
    monkeypatch.setattr(jobd.time, "monotonic", lambda: clock_state["now"])

    fast = service._queue_record("json_compact", {"a": 1}, "freshness", 1, "fast")
    fast.status = "running"
    fast.running_started_monotonic = clock_state["now"]  # 100.0
    fast.future = Future()
    fast.future.set_result(b'{"a":1}')
    clock_state["now"] = 100.05  # 50ms of pure execution
    service._pump()

    slow = service._queue_record("json_compact", {"a": 2}, "freshness", 2, "slow")
    slow.status = "running"
    slow.running_started_monotonic = clock_state["now"]  # 100.05
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


def test_jobd_future_completion_wakes_scheduler_before_poll_interval(tmp_path, monkeypatch):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    future = Future()

    class Executor:
        def submit(self, *_args):
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    record = service._queue_record("json_compact", {"a": 1}, "interactive", 1, "wake-on-done")
    service.scheduler_event.clear()
    service._pump()

    assert record.status == "running"
    assert service.scheduler_event.is_set() is False
    future.set_result(b'{"a":1}')
    assert service.scheduler_event.wait(0.01), "completed future waited for the 50 ms scheduler poll"


def test_jobd_result_exposes_wall_clock_running_start_separate_from_runtime_clock(tmp_path, monkeypatch):
    wall_clock = {"now": 1_800_000_000.0}
    runtime_clock = {"now": 100.0}
    monkeypatch.setattr(jobd.time, "time", lambda: wall_clock["now"])
    monkeypatch.setattr(jobd.time, "monotonic", lambda: runtime_clock["now"])
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    future = Future()

    class Executor:
        def submit(self, *_args):
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    record = service._queue_record("json_compact", {"a": 1}, "interactive", 1, "phase-clock")
    wall_clock["now"] += 0.25
    runtime_clock["now"] += 0.5

    service._pump()

    result = service._record_payload(record)
    assert result["submitted_at"] == 1_800_000_000.0
    assert result["running_started_at"] == 1_800_000_000.25
    assert record.running_started_monotonic == 100.5


def test_jobd_records_only_bounded_session_files_phase_aggregates(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    record = service._queue_record("session_files_view", {}, "freshness", 1, "session-files")
    record.status = "running"
    record.future = Future()
    record.future.set_result(json.dumps({
        "payload": {}, "status": 200, "truncated": False,
        "profile": {"phases": {
            "git-snapshot": {"count": 2, "total_ms": 30.0, "max_ms": 20.0},
            "unknown": {"count": 99, "total_ms": 1.0, "max_ms": 1.0},
        }, "work": {"sessions": 2, "repositories": 1, "files": 4, "git_snapshots": 1, "result_bytes": 512}, "source": {"requester": "api-session-files", "stable_view": "stable", "info_signature": "one", "repo_signature": "one", "repo_dirty_generation_count": 1, "repo_dirty_generation_max": 4}},
    }).encode("utf-8"))
    service._pump()

    phases = service.common_status()["product_phase_runtime_ms"]["session_files_view"]
    assert phases == {"git-snapshot": {"count": 2, "total_ms": 30.0, "max_ms": 20.0, "avg_ms": 15.0}}
    status = service.common_status()
    assert status["product_work_totals"]["session_files_view"] == {"sessions": 2, "repositories": 1, "files": 4, "git_snapshots": 1, "result_bytes": 512}
    assert status["source_change_counters"] == {"initial": 1}
    assert status["session_files_requester_counters"] == {"api-session-files": 1}

    service._record_phase_runtime_ms("metadata_warm_view", {
        "profile": {"work": {"sessions": 2, "entries": 5, "git_spawns": 7, "github_http_calls": 3, "linear_http_calls": 1, "result_bytes": 256, "jobd_work_graph_rebuild": 2, "provider_metadata_rebuild": 1, "unbounded": 99}},
    })
    assert service.common_status()["product_work_totals"]["metadata_warm_view"] == {
        "sessions": 2, "entries": 5, "git_spawns": 7, "github_http_calls": 3, "linear_http_calls": 1, "result_bytes": 256,
    }
    assert service.common_status()["owner_invocations"] == {
        "jobd_work_graph_rebuild": 2,
        "provider_metadata_rebuild": 1,
    }

    changed = service._queue_record("session_files_view", {}, "freshness", 2, "session-files-changed")
    changed.status = "running"
    changed.future = Future()
    changed.future.set_result(json.dumps({
        "payload": {}, "status": 200, "truncated": False,
        "profile": {"phases": {}, "work": {}, "source": {"requester": "not-a-public-label", "stable_view": "stable", "info_signature": "one", "repo_signature": "two", "repo_dirty_generation_count": 1, "repo_dirty_generation_max": 5}},
    }).encode("utf-8"))
    service._pump()
    assert service.common_status()["source_change_counters"] == {"initial": 1, "repository-state": 1, "dirty-generation-changed": 1}
    assert service.common_status()["session_files_requester_counters"] == {"api-session-files": 1, "unknown": 1}


def test_jobd_metadata_owner_invocations_do_not_advance_for_ten_unchanged_submissions(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    result = json.dumps({
        "entries": {},
        "profile": {"work": {
            "sessions": 1,
            "jobd_work_graph_rebuild": 1,
            "provider_metadata_rebuild": 1,
        }},
    }).encode("utf-8")

    first = service._queue_record("metadata_warm_view", {"sessions": {}}, "maintenance", 1, "metadata:same")
    first.status = "running"
    first.future = Future()
    first.future.set_result(result)
    service._pump()
    baseline = service.common_status()["owner_invocations"]

    for _revision in range(10):
        response = service._submit({
            "task": "metadata_warm_view",
            "payload": {"sessions": {}},
            "priority": "maintenance",
            "generation": 1,
            "coalesce_key": "metadata:same",
        })
        assert response["coalesced"] is True
    assert service.common_status()["owner_invocations"] == baseline

    changed = service._queue_record("metadata_warm_view", {"sessions": {}}, "maintenance", 2, "metadata:changed")
    changed.status = "running"
    changed.future = Future()
    changed.future.set_result(result)
    service._pump()
    assert service.common_status()["owner_invocations"] == {
        "jobd_work_graph_rebuild": baseline["jobd_work_graph_rebuild"] + 1,
        "provider_metadata_rebuild": baseline["provider_metadata_rebuild"] + 1,
    }


def test_jobd_records_session_files_requester_when_product_is_accepted(tmp_path):
    service = jobd.PersistentJobBroker(tmp_path / "jobd.sock", workers=1)
    block = service._queue_record("json_compact", {"x": 1}, "interactive", 1, "block")
    block.status = "running"
    block.future = Future()

    accepted = service._submit({
        "action": "submit",
        "task": "session_files_view",
        "payload": {"source": {"requester": "api-session-files-batch"}},
        "priority": "freshness",
        "generation": 1,
        "coalesce_key": "session-files-accepted",
    })
    assert accepted["ok"] is True and accepted["coalesced"] is False
    assert service.common_status()["session_files_accepted_requester_counters"] == {"api-session-files-batch": 1}
    assert service.common_status()["session_files_requester_counters"] == {}

    service._submit({
        "action": "submit",
        "task": "session_files_view",
        "payload": {"source": {"requester": "not-a-public-label"}},
        "priority": "freshness",
        "generation": 2,
        "coalesce_key": "session-files-unknown",
    })
    assert service.common_status()["session_files_accepted_requester_counters"] == {"api-session-files-batch": 1, "unknown": 1}


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
        assert service.product_store.inline_keys() == {"key-0", "key-1", "key-2"}

        overflow = service._queue_record("json_compact", {"i": 3}, "freshness", 1, "key-3")
        overflow.status = "running"
        overflow.future = Future()
        overflow.future.set_result(b'{"i":3}')
        service._pump()

        assert service.product_store.inline_count() == 3
        assert "key-0" not in service.product_store.inline_keys()  # the oldest-stored entry was evicted
        assert "key-3" in service.product_store.inline_keys()
        meta, body = service._product({"coalesce_key": "key-0"})
        assert meta["state"] == "none" and body == b""  # a tombstoned key reports honestly, not stale data
    finally:
        jobd.JOBD_MAX_PRODUCTS = original_max

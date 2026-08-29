import contextlib
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.helpers.external_lease_client import assert_daemon_refuses_a_self_lease
from tests.helpers.external_lease_client import external_lease_client
from yolomux_lib import activity_summary
from yolomux_lib import app as app_module
from yolomux_lib import github_client
from yolomux_lib import batchd
from yolomux_lib import metadata as metadata_module
from yolomux_lib import session_files
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import TmuxPaneInfo
from yolomux_lib.filesystem import FilesystemError
from yolomux_lib.filesystem import io_ops
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime

from _git_helpers import git
from _git_helpers import init_repo


def _blocking_worker_task():
    """Module-level so the spawn context can pickle it; simulates a worker mid-task at shutdown."""
    time.sleep(30)


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


def _empty_repository_snapshot():
    return {
        "branch": "main",
        "statuses": {},
        "numstat": {},
        "file_identities": {},
        "selected_from": "",
        "selected_to": "",
        "status_error": "",
        "repo_error": "",
        "repo_error_message": {"key": "", "params": {}, "fallback": ""},
        "recent_refs": [],
        "ahead_behind": {},
    }


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
        return _empty_repository_snapshot()

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
    result_bytes = batchd.run_registered_task("session_files_view", json.dumps(payload).encode("utf-8"))
    assert len(result_bytes) <= batchd.BATCHD_MAX_RESULT_BYTES
    result = json.loads(result_bytes.decode("utf-8"))
    assert set(result) >= {"payload", "status", "truncated", "profile", "repository_identities"}; assert str(repo.resolve()) in result["repository_identities"]
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
        batchd.run_registered_task("session_files_view", json.dumps({"infos": "not-an-object"}).encode("utf-8"))
    # infos over the bounded session limit is rejected before any git/discovery work runs.
    too_many = {str(index): {} for index in range(session_files.SESSION_FILES_VIEW_MAX_SESSIONS + 1)}
    with pytest.raises(ValueError):
        batchd.run_registered_task("session_files_view", json.dumps({"infos": too_many}).encode("utf-8"))
    # A payload larger than the broker's input ceiling is rejected by run_registered_task itself.
    with pytest.raises(ValueError):
        batchd.run_registered_task("session_files_view", b"{" + b" " * (batchd.BATCHD_MAX_PAYLOAD_BYTES + 1))


def test_batchd_product_exposes_uniform_framing_metadata(tmp_path):
    server = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_source_epoch_is_opaque_and_per_broker_start(tmp_path):
    first = batchd.PersistentJobBroker(tmp_path / "first.sock", workers=1)
    second = batchd.PersistentJobBroker(tmp_path / "second.sock", workers=1)

    first_epoch = first.common_status()["source_epoch"]

    assert isinstance(first_epoch, str)
    assert len(first_epoch) == 32
    assert first_epoch == first.common_status()["source_epoch"]
    assert first_epoch != second.common_status()["source_epoch"]


@pytest.mark.parametrize("state_lock_contended", (False, True))
def test_batchd_retirement_epoch_mismatch_does_not_close_admission(
    state_lock_contended,
    tmp_path,
    monkeypatch,
):
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    broker.source_epoch = "replacement-epoch-b"
    shutdown_calls = []
    monkeypatch.setattr(broker, "_request_shutdown", lambda: shutdown_calls.append(True))
    if state_lock_contended:
        assert broker.state_lock.acquire(blocking=False) is True

    try:
        response, body = broker.handle({
            "action": "shutdown",
            "protocol_version": batchd.BATCHD_PROTOCOL_VERSION,
            "retirement_handshake": True,
            "expected_source_epoch": "retained-epoch-a",
        })
    finally:
        if state_lock_contended:
            broker.state_lock.release()

    assert body == b""
    assert response == {
        **broker._control_plane_identity(),
        "ok": False,
        "error": "source_epoch_mismatch",
        "shutdown": False,
    }
    assert shutdown_calls == []
    assert broker.shutdown_requested.is_set() is False
    assert broker.stop_event.is_set() is False


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
    monkeypatch.setitem(batchd.REGISTERED_TASKS, "opaque-test", lambda _payload: batchd.BatchedTaskResult(body, product))

    result = batchd.run_registered_task_result("opaque-test", b"{}")

    assert result.body == body
    assert result.product == product


def _fs_descriptor(**fields):
    """One filesystem job descriptor carrying this process's captured access policy.

    The shared daemon refuses a descriptor without one, so tests must build them the way a real
    accepting server does rather than hand-rolling `{"op": ..., "path": ...}`.
    """
    return {**fields, batchd.filesystem.FS_ACCESS_POLICY_FIELD: batchd.filesystem.access_policy_descriptor()}


def _fs_batch_payload(**fields):
    """One filesystem batch payload carrying this process's captured access policy."""
    return {**fields, batchd.filesystem.FS_ACCESS_POLICY_FIELD: batchd.filesystem.access_policy_descriptor()}


def test_filesystem_operation_task_reads_in_batchd(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("batchd owns this read\n", encoding="utf-8")

    result = json.loads(batchd.run_registered_task("filesystem_operation", json.dumps(_fs_descriptor(
        op="read",
        path=str(path),
        args={},
    )).encode("utf-8")))

    assert result["content"] == "batchd owns this read\n"


def test_filesystem_operation_task_dispatches_git_history_and_commit(tmp_path):
    repo = tmp_path / "history"
    _init_repo_with_commit(repo)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    history = json.loads(batchd.run_registered_task("filesystem_operation", json.dumps(_fs_descriptor(
        op="git_history",
        path=str(repo),
        args={"limit": 1, "cursor": ""},
    )).encode("utf-8")))
    detail = json.loads(batchd.run_registered_task("filesystem_operation", json.dumps(_fs_descriptor(
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

    result = batchd.run_registered_task_result("filesystem_operation", json.dumps(_fs_descriptor(
        op="raw",
        path=str(path),
        args={"download": True},
    )).encode("utf-8"))

    assert isinstance(result, batchd.BatchedArtifactResult)
    assert (batchd.artifact_root() / result.basename).read_bytes() == body
    (batchd.artifact_root() / result.basename).unlink()
    assert result.product["format"] == "opaque_bytes"
    assert result.product["disposition"] == "attachment"
    assert result.product["filename"] == "payload.bin"


def test_filesystem_operation_task_preserves_raw_bytes_above_generic_json_budget(tmp_path):
    path = tmp_path / "preview.png"
    body = b"\x89PNG\r\n\x1a\n" + (b"x" * (batchd.BATCHD_MAX_RESULT_BYTES + 1024))
    assert len(body) < batchd.LOCAL_RPC_MAX_BINARY_BYTES
    path.write_bytes(body)

    result = batchd.run_registered_task_result("filesystem_operation", json.dumps(_fs_descriptor(
        op="raw",
        path=str(path),
        args={"max_bytes": len(body) + 1},
    )).encode("utf-8"))

    assert isinstance(result, batchd.BatchedArtifactResult)
    assert (batchd.artifact_root() / result.basename).read_bytes() == body
    (batchd.artifact_root() / result.basename).unlink()
    assert result.product["format"] == "opaque_bytes"
    assert result.product["content_type"] == "image/png"
    assert result.product["disposition"] == "inline"


def test_filesystem_operation_task_frames_html_preview_as_opaque_bytes(tmp_path):
    path = tmp_path / "preview.html"
    path.write_text("<h1>ok</h1><script>window.answer = 42;</script>\n", encoding="utf-8")

    result = batchd.run_registered_task_result("filesystem_operation", json.dumps(_fs_descriptor(
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


def test_batchd_broker_past_its_idle_window_stays_up_while_a_client_lease_is_held(tmp_path):
    """A held client lease pins the broker across a slow interaction; without one it idle-exits.

    This is the ownership seam behind the full-gate e2e differ flake
    (`test_e2e_browser_harness.py::test_direct_internal_differ_fixture_path_reaches_terminal_state`):
    the broker is per-test isolated, but its socket is removed when it decides it is idle, and a
    saturated gate can stretch the gap between two `/api/fs/batch` calls past the idle window while
    the browser boots and clicks. `_idle_should_stop` and the `shutdown_if_idle` action are the
    exact guards that keep the broker alive -- but ONLY while a lease is held. Pin both directions
    deterministically by forcing the clock past the window rather than by waiting under load.
    """
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=5.0, workers=1)
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
    broker.shutdown_requested.clear()
    broker.leases["lease-1"] = {"client_pid": os.getpid()}
    assert broker._idle_should_stop() is False
    leased_response, _ = broker.handle({"action": "shutdown_if_idle"})
    assert leased_response == {"ok": True, "shutdown": False, "leases": 1}
    assert broker.stop_event.is_set() is False


def test_batchd_status_probe_does_not_reset_the_idle_clock(tmp_path):
    """``handle()`` must never restamp the idle clock; only a real lease or
    active work, observed by ``_idle_should_stop`` itself, may do that.
    """
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=5.0, workers=1)
    assert not broker.leases and broker._queued_count() == 0
    broker.last_client_at = time.monotonic() - 6.0
    assert broker._idle_should_stop() is True, "baseline: no claims and idle_seconds elapsed must already report idle"

    broker.last_client_at = time.monotonic() - 6.0
    response, _body = broker.handle({"action": "status"})
    assert response["ok"] is True
    assert broker._idle_should_stop() is True, "a status probe reset the idle clock via handle()"


def test_batchd_external_status_probe_never_refreshes_demand_but_a_real_lease_does(tmp_path, monkeypatch):
    """Cross the real listener boundary (not a direct ``handle()`` call) to
    prove an external health/status poller with zero leases/active work
    cannot refresh batchd's idle deadline, while acquiring a real lease does
    and blocks retirement until it is released.
    """
    socket_path = tmp_path / "batchd.sock"
    broker = batchd.PersistentJobBroker(socket_path, idle_seconds=5.0, workers=1)
    worker = threading.Thread(target=broker.run, daemon=True)
    worker.start()
    try:
        client = batchd.BatchClient(socket_path)
        deadline = time.monotonic() + 2.0
        while not client.registry.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.registry.healthy() is True

        # A genuinely foreign peer PID, so this exercises the same connection
        # this broker would see from an unrelated observer/health-check
        # process (e.g. BackendHealthObserver's periodic status poll), not a
        # same-process self-connection.
        monkeypatch.setattr(runtime, "peer_pid", lambda _connection: os.getpid() + 999_000)

        broker.last_client_at = time.monotonic() - 6.0
        status_response = client.registry.status()
        assert status_response.get("healthy") is True
        assert broker._idle_should_stop() is True, "an external status probe with no lease/work refreshed the idle clock"

        lease_response = client.registry.acquire_lease()
        assert lease_response.get("ok") is True
        lease_id = str(lease_response["lease_id"])
        assert broker._idle_should_stop() is False, "acquiring a real lease did not refresh demand"

        release_response = client.registry.release_lease(lease_id)
        assert release_response.get("ok") is True
        broker.last_client_at = time.monotonic() - 6.0
        assert broker._idle_should_stop() is True, "idle grace window did not elapse after the final lease released"
    finally:
        broker.stop_event.set()
        worker.join(timeout=3.0)


def test_batchd_idle_reaps_a_dead_client_lease_before_deciding_to_stay_up(tmp_path):
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=5.0, workers=1)
    broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
    broker.leases["dead-client"] = runtime.current_host_identity().process_record_fields(
        pid=999_999_999,
        start_identity="proc:1",
    )

    assert broker._idle_should_stop() is True
    assert broker.leases == {}


def test_batchd_status_reaps_dead_client_leases_for_startup_reconciliation(tmp_path):
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=5.0, workers=1)
    broker.leases["dead-client"] = runtime.current_host_identity().process_record_fields(
        pid=999_999_999,
        start_identity="proc:1",
    )

    status = broker.common_status()

    assert status["clients"] == 0
    assert broker.leases == {}


@pytest.mark.gate_serial
def test_fs_batch_completion_holds_a_batchd_lease_across_the_broker_idle_window(tmp_path, monkeypatch):
    """The fs-batch/differ completion worker pins the broker with a client lease while it polls.

    W15 #4 root cause: under a saturated gate the completion worker's product poll is starved past
    the broker's idle window, so between two ``/api/fs/batch`` calls the broker removes its own
    socket, the next relay fails with ``LocalRpcError: unattributed_latency``, and the Finder shows
    "request failed". Prove the completion path holds ONE registry client lease that vetoes idle
    shutdown at the exact moment it polls -- even with the broker forced well past its idle window --
    and releases it at the end so idle shutdown is NOT weakened (an unheld broker still idles out).
    """
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=5.0, workers=1)
    external_client = contextlib.ExitStack()
    client_pid = external_client.enter_context(external_lease_client())

    class BrokerLeaseRegistry:
        """Exercise the lease handlers synchronously; transport timing is not this contract.

        ``client_pid`` is a REAL separate process, because in production this
        caller is the web server and batchd is a separate daemon. See
        ``external_lease_client``.
        """

        def __init__(self):
            self.acquired: list[str] = []
            self.released: list[str] = []

        def acquire_lease(self, existing_lease_id=""):
            response = broker.handle({
                "action": "lease",
                "client_pid": client_pid,
                "lease_id": existing_lease_id,
            })[0]
            assert response["ok"] is True, f"the external client could not lease the broker: {response}"
            self.acquired.append(str(response.get("lease_id") or ""))
            return response

        def release_lease(self, lease_id):
            self.released.append(lease_id)
            return broker.handle({"action": "release", "lease_id": lease_id})[0]

    registry = BrokerLeaseRegistry()
    app = app_module.TmuxWebtermApp([], status_service_mode=True)
    app.batchd_fs_batch_lease = app_module.BatchedInteractionLease(type("BatchClient", (), {"registry": registry})())
    try:
        assert broker.handle({"action": "status"})[0]["clients"] == 0
        assert_daemon_refuses_a_self_lease(broker)
        assert broker.handle({"action": "status"})[0]["clients"] == 0, (
            "the refused self-lease pinned the broker anyway"
        )

        observed: dict[str, object] = {}

        def poll_probe(_producer, _deadline_at):
            # At the poll the lease MUST be held. Force the broker well past its idle window and prove
            # it refuses to shut down because of the held lease, not because the clock is fresh.
            broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
            observed["held_during_poll"] = app.batchd_fs_batch_lease.held
            observed["clients_during_poll"] = broker.handle({"action": "status"})[0]["clients"]
            observed["idle_should_stop"] = broker._idle_should_stop()
            observed["shutdown_if_idle"] = broker.handle({"action": "shutdown_if_idle"})[0]
            return {"responses": [{"id": 0, "ok": True}]}

        monkeypatch.setattr(app, "wait_for_batchd_operation_product", poll_probe)
        monkeypatch.setattr(app, "terminalize_operation", lambda *args, **kwargs: None)

        producer = app_module.BatchedProductOperation(job_id="job-1", product_key="key-1", generation=1)
        app.complete_filesystem_batch_operation("op-1", "req-1", (0,), producer, time.time() + 5.0)

        assert observed["held_during_poll"] is True
        assert observed["clients_during_poll"] == 1
        assert observed["idle_should_stop"] is False, "a held lease must veto idle shutdown mid-poll"
        assert observed["shutdown_if_idle"] == {"ok": True, "shutdown": False, "leases": 1}

        # Released at the end: idle shutdown is NOT weakened -- an unheld broker still idles out.
        assert app.batchd_fs_batch_lease.held is False
        assert broker.handle({"action": "status"})[0]["clients"] == 0
        assert registry.acquired == registry.released
        broker.last_client_at = time.monotonic() - (broker.idle_seconds * 10)
        assert broker._idle_should_stop() is True
    finally:
        app.stop_batchd_operation_service()
        external_client.close()


def test_watch_diff_completion_holds_a_batchd_lease_across_the_broker_idle_window(tmp_path, monkeypatch):
    """The watch-diff completion worker pins the broker with a client lease while it polls.

    Same Seam-B lease mechanism as
    ``test_fs_batch_completion_holds_a_batchd_lease_across_the_broker_idle_window`` -- ``GET
    /api/fs/watch-diff`` simply was not covered. The watch-diff completion worker submits every
    child batch and then polls each product under one deadline; under a saturated gate the gap
    between the submit ``produce`` and the product poll can exceed the broker's idle window, so the
    broker removes its own socket mid-interaction and the poll fails with a batchd 404 (the live
    ``GET /api/fs/watch-diff`` failure). Prove the completion path holds ONE registry client lease
    that vetoes idle shutdown at the exact moment it polls -- even with the broker forced well past
    its idle window -- and releases it at the end so idle shutdown is NOT weakened (an unheld broker
    still idles out).
    """
    socket_path = tmp_path / "batchd.sock"
    broker = batchd.PersistentJobBroker(socket_path, idle_seconds=5.0, workers=1)
    worker = threading.Thread(target=broker.run, daemon=True)
    worker.start()
    try:
        app = app_module.TmuxWebtermApp([], status_service_mode=True)
        app.job_client = batchd.BatchClient(socket_path)
        # The app's watch-diff path holds this exact lease owner -- the SAME one fs/batch holds --
        # so bind it to the test broker's client.
        app.batchd_fs_batch_lease = app_module.BatchedInteractionLease(app.job_client)
        deadline = time.monotonic() + 2.0
        while not app.job_client.registry.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.job_client.registry.healthy() is True
        # No interaction yet: the broker holds no client lease.
        assert broker.handle({"action": "status"})[0]["clients"] == 0

        # A receipt-only child batch forces `resolve_filesystem_watch_batches` to poll the broker
        # (mirrors a cold submit that returned a receipt, not a warm product). The completion
        # worker's real acquire/release around submit+resolve is the code under test.
        producer = app_module.BatchedProductOperation(job_id="job-1", product_key="watch-key-0", generation=1)
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
            observed["held_during_poll"] = app.batchd_fs_batch_lease.held
            observed["clients_during_poll"] = broker.handle({"action": "status"})[0]["clients"]
            observed["idle_should_stop"] = broker._idle_should_stop()
            observed["shutdown_if_idle"] = broker.handle({"action": "shutdown_if_idle"})[0]
            return {"responses": [{"id": 0, "ok": True}]}

        monkeypatch.setattr(app, "wait_for_batchd_operation_product", poll_probe)

        flight = app_module.BatchedOperationFlight(
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
        assert app.batchd_fs_batch_lease.held is False
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
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    submitted: list[tuple[Future, object, tuple[object, ...]]] = []

    class Executor:
        def submit(self, function, *args):
            future = Future()
            submitted.append((future, function, args))
            return future

    monkeypatch.setattr(broker, "_executor", lambda *_args: Executor())
    assert "relay" not in batchd.BATCHD_REQUEST_ACTIONS
    unknown, _empty = broker.handle({"action": "relay"})
    assert unknown == {"ok": False, "error": "unknown batchd action"}

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
        assert len(chunk) <= batchd.LOCAL_RPC_MAX_BINARY_BYTES
        assert chunk_meta["sha256"] == hashlib.sha256(chunk).hexdigest()
        chunked.extend(chunk)
        offset += len(chunk)
    assert bytes(chunked) == body
    closed, _empty = broker.handle({"action": "artifact_close", "lease_id": opened["lease_id"]})
    assert closed == {"ok": True, "closed": True}
    assert broker.product_store.lease_count() == 0


@pytest.mark.parametrize("operation", ("raw", "zip"))
def test_large_filesystem_transfer_uses_bounded_artifact_chunks(operation, tmp_path, monkeypatch):
    monkeypatch.setattr(batchd, "RUNTIME_DIR", tmp_path / "runtime")
    source = tmp_path / "source"
    expected = b"z" * (batchd.LOCAL_RPC_MAX_BINARY_BYTES + 257)
    if operation == "raw":
        source.write_bytes(expected)
        args = {"max_bytes": len(expected) + 1024}
    else:
        source.mkdir()
        (source / "payload.bin").write_bytes(expected)
        args = {"max_bytes": len(expected) + 1024, "filename": "source.zip"}
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    result = batchd.run_registered_task_result(
        "filesystem_operation",
        json.dumps(_fs_descriptor(op=operation, path=str(source), args=args)).encode("utf-8"),
    )
    assert isinstance(result, batchd.BatchedArtifactResult)
    artifact_path = batchd.artifact_root() / result.basename
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
        assert 0 < len(chunk) <= batchd.LOCAL_RPC_MAX_BINARY_BYTES
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
    monkeypatch.setattr(batchd, "RUNTIME_DIR", tmp_path / "runtime")
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * 1025)
    with pytest.raises(batchd.BatchedFilesystemOperationFailure) as failure:
        batchd.run_registered_task_result(
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
    socket_path = tmp_path / "batchd.sock"
    first_path = tmp_path / "first.bin"
    second_path = tmp_path / "second.bin"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    broker = batchd.PersistentJobBroker(socket_path, workers=1)
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
    client = batchd.BatchClient(socket_path)
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
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = broker._queue_record("filesystem_operation", {}, "interactive", 1, "adopting-artifact")
    record.status = "running"
    record.future = Future()
    record.future.set_result(batchd.BatchedArtifactResult(
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
    pump = threading.Thread(target=broker._pump, name="batchd-controlled-artifact-adoption")
    pump.start()
    assert adoption_started.wait(timeout=1.0)

    requests = (
        {"action": "produce", "task": "json_compact", "payload": {"value": 1}, "coalesce_key": "unrelated-produce"},
        {"action": "result", "job_id": "unknown"},
        {"action": "product", "coalesce_key": "unrelated-product"},
    )
    assert broker.state_lock.acquire(blocking=False), "artifact adoption retained the broker state lock"
    broker.state_lock.release()
    responses = [(request["action"], broker.handle(request)) for request in requests]
    served_while_adopting = not release_adoption.is_set()
    release_adoption.set()
    pump.join(timeout=2.0)

    assert served_while_adopting is True
    by_action = dict(responses)
    assert by_action["produce"][0]["ok"] is True
    assert by_action["produce"][0]["state"] == "queued"
    assert by_action["result"] == ({"ok": False, "error": "unknown job"}, b"")
    assert by_action["product"] == ({"ok": True, "state": "none", "generation": 0, "inflight": False}, b"")
    assert record.status == "failed"


def test_zero_wait_produce_preserves_typed_filesystem_failure(tmp_path):
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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
        monkeypatch.setattr(batchd.filesystem, "MAX_READ_BYTES", maximum)
    payload = json.dumps(_fs_descriptor(op=operation, path=str(path), args={})).encode("utf-8")

    with pytest.raises(batchd.BatchedFilesystemOperationFailure) as failure:
        batchd._filesystem_operation(payload)

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
    }, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)

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
    repo = tmp_path / "repo"; _init_repo_with_commit(repo)
    (repo / "one.py").write_text("x = 3\n", encoding="utf-8")
    calls = Mock(wraps=session_files._build_git_snapshot_from_scope)
    monkeypatch.setattr(session_files, "_build_git_snapshot_from_scope", calls)
    # Two sessions whose panes sit in the SAME repo, cross-session pass: the memoizing provider must
    # build that repo's git snapshot exactly once for the whole task.
    payload = {"session": "", "infos": {"a": _session_info_json("a", repo), "b": _session_info_json("b", repo)}, "hours": 24.0, "include_cross_session_attribution": True}
    result = session_files.session_files_view_result(payload, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
    assert result["status"] == 200
    assert calls.call_count == 1


def test_session_files_view_retries_a_snapshot_that_changes_identity(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; _init_repo_with_commit(repo); real_identity = session_files.git_worktree_signature_from_scope; identity_calls = [0]
    def changing_identity(scope):
        identity_calls[0] += 1; identity = real_identity(scope)
        return "changed" if identity_calls[0] == 2 else identity
    monkeypatch.setattr(session_files, "git_worktree_signature_from_scope", changing_identity)
    result = session_files.session_files_view_result({"session": "s1", "infos": {"s1": _session_info_json("s1", repo)}, "hours": 24.0, "include_cross_session_attribution": False}, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
    assert result["status"] == 200; assert identity_calls == [4]; assert str(repo.resolve()) in result["repository_identities"]


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
    first = session_files.session_files_view_result(base, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
    changed_metadata = {**base, "infos": {"a": _session_info_json("a", repo, kind="codex")}}
    second = session_files.session_files_view_result(changed_metadata, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
    assert first["status"] == second["status"] == 200
    assert calls == [str(repo)]

    changed_repository = {**base, "repository_states": [{"path": str(repo), "generation": 8}]}
    third = session_files.session_files_view_result(changed_repository, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
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

    assert session_files.session_files_view_result(canonical, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert session_files.session_files_view_result(via_alias, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)["status"] == 200
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
    assert session_files.session_files_view_result(payload, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert session_files.session_files_view_result(payload, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)["status"] == 200
    assert calls == [
        (str(first_repo.resolve()), "HEAD~1", "HEAD"),
        (str(second_repo.resolve()), "HEAD", "HEAD"),
    ]

    changed_second_ref = json.loads(json.dumps(payload))
    changed_second_ref["repo_refs"][str(second_repo)]["from"] = "HEAD~1"
    assert session_files.session_files_view_result(changed_second_ref, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)["status"] == 200
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
        result = session_files.session_files_view_result(payload, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
        assert result["status"] == 200
    assert calls == [str(repo)]

    changed_repository = {**base, "repository_states": [{"path": str(repo), "generation": 8}]}
    first_after_change = session_files.session_files_view_result(changed_repository, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
    second_after_change = session_files.session_files_view_result(changed_repository, max_bytes=batchd.BATCHD_MAX_RESULT_BYTES - 4096)
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
        return _empty_repository_snapshot()

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
        return _empty_repository_snapshot()

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
        return _empty_repository_snapshot()

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
        return _empty_repository_snapshot()

    snapshot, hit = session_files.cached_repository_snapshot(repo, None, None, 1, build)
    assert snapshot == _empty_repository_snapshot()
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
    payload = {"files": [{"path": f"/repo/file{index}.py", "blob": "y" * 256} for index in range(200)], "repos": []}; response = {"payload": payload, "repository_identities": {f"/repo/{index}": ["x" * 64] for index in range(64)}}
    truncated = session_files.bound_session_files_view_payload(response, 8192)
    assert truncated is True
    assert len(json.dumps(response, separators=(",", ":")).encode("utf-8")) <= 8192
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
    result = json.loads(batchd.run_registered_task("tabber_activity_view", json.dumps(payload).encode("utf-8")))

    assert result["truncated"] is False
    assert set(result["session_rows"]) == {"1"}
    assert result["session_rows"]["1"]["agent_windows"][0]["kind"] == "claude"
    assert len(result["session_rows"]["1"]["agents"]) == 1
    # Running it again with identical input is byte-for-byte identical (pure function).
    again = json.loads(batchd.run_registered_task("tabber_activity_view", json.dumps(payload).encode("utf-8")))
    assert again == result


def test_tabber_activity_view_task_rejects_malformed_or_oversized_payload():
    with pytest.raises(ValueError):
        batchd.run_registered_task("tabber_activity_view", json.dumps({"sessions": "not-an-object"}).encode("utf-8"))
    too_many = {str(index): _sample_tabber_session_payload(str(index)) for index in range(activity_summary.TABBER_ACTIVITY_VIEW_MAX_SESSIONS + 1)}
    with pytest.raises(ValueError):
        batchd.run_registered_task("tabber_activity_view", json.dumps({"sessions": too_many}).encode("utf-8"))
    with pytest.raises(ValueError):
        batchd.run_registered_task("tabber_activity_view", b"{" + b" " * (batchd.BATCHD_MAX_PAYLOAD_BYTES + 1))


def test_tabber_activity_view_task_never_leaks_live_screen_text_beyond_its_own_field():
    # The worker is pure assembly: it must not fabricate or duplicate screen text into any other
    # field, and must not require/perform any tmux/attention read of its own.
    payload = {"sessions": {"1": _sample_tabber_session_payload("1")}, "locale": "en", "snapshot_revision": 1}
    result = json.loads(batchd.run_registered_task("tabber_activity_view", json.dumps(payload).encode("utf-8")))
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
    result = json.loads(batchd.run_registered_task("metadata_warm_view", json.dumps(payload).encode("utf-8")))

    assert result["truncated"] is False
    matches = {key: value for key, value in result["entries"].items() if key.startswith("github-pr-branch:acme/repo:feature/one")}
    assert matches
    entry = next(iter(matches.values()))
    assert entry["value"][0]["number"] == 5
    assert 0 < entry["ttl_remaining"] <= metadata_module.METADATA_CACHE_TTL_SECONDS
    assert result["profile"]["work"]["sessions"] == 1
    assert result["profile"]["work"]["batchd_work_graph_rebuild"] == 1
    assert result["profile"]["work"]["provider_metadata_rebuild"] == 1
    assert result["profile"]["work"]["git_spawns"] > 0
    assert result["profile"]["work"]["github_http_calls"] == 0
    assert result["profile"]["work"]["linear_http_calls"] == 0
    # Running it again with the same fake network response reproduces the same materialized value
    # (a fresh worker-local cache each run, never carried over from a prior invocation).
    again = json.loads(batchd.run_registered_task("metadata_warm_view", json.dumps(payload).encode("utf-8")))
    again_matches = {key: value for key, value in again["entries"].items() if key.startswith("github-pr-branch:acme/repo:feature/one")}
    assert next(iter(again_matches.values()))["value"] == entry["value"]


def test_metadata_warm_view_task_rejects_malformed_or_oversized_payload():
    with pytest.raises(ValueError):
        batchd.run_registered_task("metadata_warm_view", json.dumps({"sessions": "not-an-object"}).encode("utf-8"))
    too_many = {str(index): {} for index in range(metadata_module.METADATA_WARM_VIEW_MAX_SESSIONS + 1)}
    with pytest.raises(ValueError):
        batchd.run_registered_task("metadata_warm_view", json.dumps({"sessions": too_many}).encode("utf-8"))
    with pytest.raises(ValueError):
        batchd.run_registered_task("metadata_warm_view", b"{" + b" " * (batchd.BATCHD_MAX_PAYLOAD_BYTES + 1))


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


def _wait_for_result(client: batchd.BatchClient, job_id: str, *, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.request({"action": "result", "job_id": job_id})
        job = response.get("job") if isinstance(response.get("job"), dict) else {}
        if job.get("status") in {"completed", "failed", "cancelled", "superseded"}:
            return response
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not settle")


@pytest.mark.gate_serial
def test_batchd_control_plane_is_ready_before_blocked_data_plane_setup(tmp_path, monkeypatch):
    socket_path = tmp_path / "batchd.sock"
    executor_setup_started = threading.Event()
    release_executor_setup = threading.Event()
    priority_calls = []
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)

    def blocked_executor_setup(_worker_count):
        executor_setup_started.set()
        assert release_executor_setup.wait(5.0)
        raise RuntimeError("fixture executor setup failure")

    monkeypatch.setattr(service, "_new_executor", blocked_executor_setup)
    monkeypatch.setattr(batchd, "apply_service_process_priority", lambda: priority_calls.append(threading.current_thread().name) or True)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = batchd.BatchClient(socket_path)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not client.registry.healthy():
        time.sleep(0.01)
    assert client.registry.healthy() is True
    deadline = time.monotonic() + 1.0
    while not priority_calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.scheduler_thread is not None
    assert priority_calls == ["batchd-scheduler"]

    submitted = client.submit("json_compact", {"ready": True}, priority="interactive", coalesce_key="blocked-setup")
    assert submitted["ok"] is True
    assert executor_setup_started.wait(1.0)
    assert client.registry.healthy() is True
    assert priority_calls == ["batchd-scheduler"]

    release_executor_setup.set()
    assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_batchd_has_a_bounded_spawn_worker_pool_and_registered_tasks_only(tmp_path):
    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = batchd.BatchClient(socket_path)
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
        "point": {"capacity": batchd.BATCHD_POINT_WORKERS, "active": 0, "queued": 0},
        "mutation": {"capacity": batchd.BATCHD_MUTATION_WORKERS, "active": 0, "queued": 0},
        "interactive": {"capacity": batchd.BATCHD_INTERACTIVE_WORKERS, "active": 0, "queued": 0},
        "bulk": {"capacity": 1, "active": 0, "queued": 0},
    }
    assert status["cache"]["records"] == 1
    assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_registry_launched_batchd_executes_a_spawn_worker(tmp_path):
    """The daemon's redirected stdio must remain valid for macOS spawn workers."""
    client = batchd.BatchClient(tmp_path / "batchd.sock")
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
            pytest.fail(f"registry-launched batchd did not complete: {client.request({'action': 'status'})}")
    finally:
        assert client.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}


def test_scheduler_started_batchd_holds_a_lease_until_scheduler_stop(tmp_path):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    assert client.start_for_scheduler() is True
    try:
        assert client.request({"action": "status"})["clients"] == 1
        assert client.start_for_scheduler() is True
        assert client.request({"action": "status"})["clients"] == 1
        assert client.stop_for_scheduler() is True
        assert client.request({"action": "status"})["clients"] == 0
    finally:
        client.request({"action": "shutdown"})


def test_registry_launched_batchd_spawn_worker_survives_closed_parent_stdin(tmp_path):
    """A nohup/launchd-style closed stdin must not crash a macOS spawn worker."""
    socket_path = tmp_path / "closed-stdin-batchd.sock"
    script = """
import json
import os
import sys
import time
from pathlib import Path
from yolomux_lib import batchd

os.close(0)
client = batchd.BatchClient(Path(sys.argv[1]))
if not client.start_for_scheduler():
    raise SystemExit("batchd did not start")
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
        batchd.run_registered_task(
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

    result = json.loads(batchd.run_registered_task(
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
    result = batchd.run_registered_task(
        "transcript_view",
        json.dumps({"path": str(large), "line_limit": 4, "item_limit": 4}).encode("utf-8"),
    )

    assert len(result) < batchd.BATCHD_MAX_RESULT_BYTES
    assert json.loads(result)["items"][-1]["text"] == "tail-only"
    try:
        batchd.run_registered_task("transcript_view", b'{"path":"relative.jsonl"}')
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
            batchd.run_registered_task("transcript_view", json.dumps({"path": str(candidate)}).encode("utf-8"))
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(f"{candidate} must be rejected")


def test_transcript_view_reports_file_identity_separate_from_byte_generation(tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"timestamp": "2026-07-10T00:00:00Z", "payload": {"type": "user_message", "message": "identity"}}) + "\n", encoding="utf-8")
    stat = transcript.stat()
    result = json.loads(batchd.run_registered_task("transcript_view", json.dumps({"path": str(transcript), "line_limit": 100, "item_limit": 20}).encode("utf-8")))

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
    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    port_a = batchd.BatchClient(socket_path)
    port_b = batchd.BatchClient(socket_path)
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
    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    port_a = batchd.BatchClient(socket_path)
    port_b = batchd.BatchClient(socket_path)
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


def test_batchd_supersedes_stale_queued_generations_and_keeps_payloads_bounded(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    old_record = service._queue_record("text_facts", {"text": "old"}, "maintenance", 1, "same")
    service.latest_generation["same"] = 2
    service._supersede_stale_queued("same", 2)
    new_record = service._queue_record("text_facts", {"text": "new"}, "interactive", 2, "same")
    service._pump()

    assert old_record.status == "superseded"
    assert new_record.status == "running"
    assert service.latest_generation["same"] == 2
    assert len(json.dumps({"text": "x" * (batchd.BATCHD_MAX_PAYLOAD_BYTES + 1)}).encode("utf-8")) > batchd.BATCHD_MAX_PAYLOAD_BYTES
    oversized = service._submit({"task": "text_facts", "payload": {"text": "x" * (batchd.BATCHD_MAX_PAYLOAD_BYTES + 1)}, "priority": "interactive"})
    assert oversized == {"ok": False, "error": "payload too large"}


def test_batchd_submission_encodes_payload_once_and_preserves_exact_boundary_and_default_key(tmp_path, monkeypatch):
    empty = json.dumps(
        {"text": ""},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = {"text": "x" * (batchd.BATCHD_MAX_PAYLOAD_BYTES - len(empty))}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    oversized_payload = {"text": payload["text"] + "x"}
    assert len(encoded) == batchd.BATCHD_MAX_PAYLOAD_BYTES

    original_dumps = batchd.json.dumps
    payload_encodes = 0

    def counted_dumps(value, *args, **kwargs):
        nonlocal payload_encodes
        if value is payload:
            payload_encodes += 1
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(batchd.json, "dumps", counted_dumps)
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)

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


def test_batchd_prevents_maintenance_starvation_and_times_out_before_worker_start(tmp_path, monkeypatch):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    interactive = [
        service._queue_record("text_facts", {"text": f"interactive-{number}"}, "interactive", number, f"interactive-{number}")
        for number in range(batchd.BATCHD_INTERACTIVE_WORKERS + 1)
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
    assert service._submit({"task": "text_facts", "payload": {"text": "late"}, "deadline_ms": batchd.BATCHD_MAX_DEADLINE_MS + 1}) == {"ok": False, "error": "deadline too large"}


def test_batchd_general_saturation_does_not_block_interactive_dispatch(tmp_path, monkeypatch):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
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
    assert time.monotonic() - started < batchd.BATCHD_SCHEDULER_POLL_SECONDS


def test_batchd_interactive_saturation_queues_until_reserved_capacity_is_released(tmp_path, monkeypatch):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
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


def test_batchd_point_lane_dispatches_while_every_bulk_and_interactive_slot_is_held(tmp_path, monkeypatch):
    """A held bulk job must not put an editor open or an index probe behind it."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
    holders = []
    for number in range(service.general_worker_count):
        holder = service._queue_record("text_facts", {"text": f"bulk-{number}"}, "freshness", number, f"bulk-{number}")
        holder.status = "running"
        holder.future = Future()
        holders.append(holder)
    for number in range(batchd.BATCHD_INTERACTIVE_WORKERS):
        holder = service._queue_record("text_facts", {"text": f"batch-{number}"}, "interactive", number, f"batch-{number}")
        holder.status = "running"
        holder.future = Future()
        holders.append(holder)
    lanes_by_submission: list[str] = []

    class Executor:
        def submit(self, *_args):
            return Future()

    monkeypatch.setattr(service, "_executor", lambda priority="freshness": (
        lanes_by_submission.append(batchd.PersistentJobBroker._lane_for_priority(priority)) or Executor()
    ))
    read = service._queue_record("filesystem_operation", {"op": "read"}, "point", 1, "point-read")
    index_status = service._queue_record("filesystem_operation", {"op": "index_status"}, "point", 1, "point-index")

    service._pump()

    assert [holder.status for holder in holders] == ["running"] * len(holders)
    assert read.status == "running"
    assert index_status.status == "running"
    assert lanes_by_submission == ["point", "point"]
    status = service.common_status()
    assert status["lanes"]["point"] == {"capacity": batchd.BATCHD_POINT_WORKERS, "active": 2, "queued": 0}
    assert status["lanes"]["bulk"]["active"] == service.general_worker_count
    assert status["lanes"]["interactive"]["active"] == batchd.BATCHD_INTERACTIVE_WORKERS


def test_batchd_point_lane_capacity_is_bounded_and_releases_in_order(tmp_path, monkeypatch):
    """Point capacity is explicitly bounded: one slow point read cannot strand the rest, and
    point work cannot become unbounded process capacity of its own."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
    submitted_futures: list[Future] = []

    class Executor:
        def submit(self, *_args):
            future = Future()
            submitted_futures.append(future)
            return future

    monkeypatch.setattr(service, "_executor", lambda *_args: Executor())
    points = [
        service._queue_record("json_compact", {"order": order}, "point", 1, f"point-{order}")
        for order in range(batchd.BATCHD_POINT_WORKERS + 1)
    ]

    service._pump()

    assert [record.status for record in points[:batchd.BATCHD_POINT_WORKERS]] == ["running"] * batchd.BATCHD_POINT_WORKERS
    assert points[-1].status == "queued"
    assert len(submitted_futures) == batchd.BATCHD_POINT_WORKERS
    assert service.common_status()["lanes"]["point"] == {
        "capacity": batchd.BATCHD_POINT_WORKERS,
        "active": batchd.BATCHD_POINT_WORKERS,
        "queued": 1,
    }

    submitted_futures[0].set_result(b'{"order":0}')
    service._pump()

    assert points[0].status == "completed"
    assert points[-1].status == "running"
    assert len(submitted_futures) == batchd.BATCHD_POINT_WORKERS + 1


def test_batchd_every_declared_priority_is_owned_by_exactly_one_bounded_lane(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)

    assert set(batchd.BATCHD_PRIORITIES) == set(batchd.BATCHD_PRIORITY_LANES)
    assert batchd.BATCHD_PRIORITIES == tuple(batchd.BATCHD_PRIORITY_LANES)
    assert set(batchd.BATCHD_PRIORITY_LANES.values()) == set(batchd.BATCHD_LANE_PRIORITIES)
    assert all(service._lane_capacity(lane) >= 1 for lane in batchd.BATCHD_LANE_PRIORITIES)
    with pytest.raises(ValueError, match="no batchd lane owns priority"):
        batchd.PersistentJobBroker._lane_for_priority("nonexistent")
    assert service._submit({"task": "text_facts", "payload": {"text": "x"}, "priority": "nonexistent"}) == {
        "ok": False,
        "error": "invalid priority",
    }


def test_point_read_admits_against_its_own_lane_while_the_bulk_queue_is_full(tmp_path):
    """A full bulk/freshness queue must not refuse an idle point read as `queue full`.

    Before the per-lane cap, one global `BATCHD_MAX_QUEUE` sat ahead of every lane: 64 queued
    freshness records made a fresh point submission return `queue full` while the point lane read
    capacity 2, active 0.  The cap is per-lane now, so each lane stays bounded (the backpressure
    intent) without one lane's queue starving another's admission.
    """
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
    for number in range(batchd.BATCHD_MAX_QUEUE):
        submission, error = batchd.PersistentJobBroker._validated_submission({
            "task": "session_files_view", "priority": "freshness",
            "payload": {"session": f"s{number}"}, "generation": 1,
            "coalesce_key": f"freshness-{number}", "deadline_ms": 60_000,
        })
        assert error is None, error
        assert service._submit_validated(submission)["ok"] is True

    # The freshness (bulk) lane's queue is full; the point lane is idle.
    assert service._queued_count(lane="bulk") >= batchd.BATCHD_MAX_QUEUE
    assert service._queued_count(lane="point") == 0
    assert service._future_slots(lane="point") == 0

    submission, error = batchd.PersistentJobBroker._validated_submission({
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


def test_batchd_fresh_only_joins_in_flight_work_but_never_serves_a_stored_product(tmp_path, monkeypatch):
    """The mtime-granularity case: one coalesce key, two different contents.

    A stat identity is only as fine as the filesystem timestamp tick, so a rewrite inside one tick
    that keeps the same size produces an identical key for different bytes.  A `fresh_only`
    submission must therefore refuse the stored product while still joining in-flight work.
    """
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
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


def test_batchd_coalesces_identical_in_flight_point_reads_into_one_execution(tmp_path, monkeypatch):
    """Repeated identical point reads share one execution and every receipt names that job."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
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
def test_batchd_completion_validates_and_aggregates_json_result_with_one_parse(tmp_path, monkeypatch, task):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    result = json.dumps({"profile": {"phases": {}, "work": {"sessions": 1}}}).encode("utf-8")
    decoded_inputs, owner_thread = [], threading.get_ident()
    real_loads = batchd.json.loads

    def counted_loads(value, *args, **kwargs):
        if threading.get_ident() == owner_thread:
            decoded_inputs.append(value)
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(batchd.json, "loads", counted_loads)
    with ThreadPoolExecutor(max_workers=1) as executor: foreign_decode = executor.submit(json.loads, '{"foreign":true}').result()
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
    assert foreign_decode == {"foreign": True}
    assert decoded_inputs == [result.decode("utf-8"), "not-json"]
    assert service.product_counters[task]["completed"] == 1
    assert service.product_counters[task]["failed"] == 1
    assert service.product_work_totals[task] == {"sessions": 1}


def test_batchd_rejects_malformed_worker_result_and_bounds_retained_records(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    malformed = service._queue_record("text_facts", {"text": "bad"}, "interactive", 1, "bad")
    malformed.status = "running"
    malformed.future = Future()
    malformed.future.set_result(b"not-json")
    secret_failure = service._queue_record("text_facts", {"text": "secret"}, "interactive", 2, "secret")
    secret_failure.status = "running"
    secret_failure.future = Future()
    secret_failure.future.set_exception(ValueError("token=super-secret-value"))
    for number in range(batchd.BATCHD_MAX_RECORDS + 5):
        record = service._queue_record("text_facts", {"text": str(number)}, "maintenance", number, f"finished-{number}")
        record.status = "completed"
        record.completed_at = float(number + 1)
        record.result = b'{"ok":true}'

    service._pump()

    assert malformed.status == "failed"
    assert "Expecting value" in malformed.error
    assert secret_failure.status == "failed"
    assert secret_failure.error == "[redacted]"
    assert len(service.records) <= batchd.BATCHD_MAX_RECORDS


def test_batchd_marks_filesystem_worker_failure_terminal_and_continues_serving(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_enforces_queue_saturation_deadlines_and_recovers_a_broken_executor(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    occupying = service._queue_record("text_facts", {"text": "active"}, "freshness", 1, "active")
    occupying.status = "running"
    occupying.future = Future()
    for number in range(batchd.BATCHD_MAX_QUEUE):
        queued = service._queue_record("text_facts", {"text": str(number)}, "freshness", number, f"queue-{number}")
        queued.status = "queued"

    assert service._submit({"task": "text_facts", "payload": {"text": "overflow"}}) == {"ok": False, "error": "queue full"}
    assert service._submit({"task": "text_facts", "payload": {"text": "invalid"}, "deadline_ms": "tomorrow"}) == {"ok": False, "error": "invalid generation or deadline"}
    assert service._submit({"task": "text_facts", "payload": {"text": "negative"}, "deadline_ms": -1}) == {"ok": False, "error": "invalid deadline"}
    lease_record = runtime.current_host_identity().process_record_fields()
    service.leases = {str(number): dict(lease_record) for number in range(runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES)}
    # The saturated-table refusal is only reachable for a caller the fence would
    # otherwise admit, so the client here has to be a real separate process. A
    # harness naming ``os.getpid()`` is the daemon itself and is refused one step
    # earlier -- correctly, and for a completely different reason.
    with external_lease_client() as client_pid:
        lease_response, _binary = service.handle({"action": "lease", "client_pid": client_pid})
        assert lease_response == {"ok": False, "error": "too many clients", "leases": runtime.LOCAL_SERVICE_MAX_CLIENT_LEASES, "version": batchd.BATCHD_PROTOCOL_VERSION}
        # NEGATIVE CONTROL: the external stand-in is not a way around the fence.
        # A real self-lease is refused for being a self-connection, not for the
        # full table, so the two refusals cannot be confused for one another.
        assert_daemon_refuses_a_self_lease(service)

    broken = service._queue_record("text_facts", {"text": "crash"}, "interactive", 999, "crash")
    broken.status = "running"
    broken.future = Future()
    broken.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def __init__(self):
            self._processes = {}

        def shutdown(self, **_kwargs):
            return None

    service.executors["interactive"] = BrokenExecutor()  # type: ignore[assignment]
    service._pump()

    assert broken.status == "failed"
    assert broken.error == "worker crashed"
    assert service.executors["interactive"] is None


def test_batchd_rejects_newer_protocol_before_dispatch(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)

    response, binary = service.handle({"action": "ping", "protocol_version": batchd.BATCHD_PROTOCOL_VERSION + 1})

    assert binary == b""
    assert response == {
        "ok": False,
        "error": "upgrade_required",
        "required_protocol_version": batchd.BATCHD_PROTOCOL_VERSION,
    }


def test_batchd_clients_share_one_registry_and_coalesce_across_ports(tmp_path):
    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    first = batchd.BatchClient(socket_path)
    second = batchd.BatchClient(socket_path)

    deadline = time.monotonic() + 2.0
    while not first.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    first_submission = first.submit("json_compact", {"z": 1, "a": 2}, priority="interactive", generation=7, coalesce_key="two-ports")
    second_submission = second.submit("json_compact", {"z": 1, "a": 2}, priority="interactive", generation=7, coalesce_key="two-ports")

    expected_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-batchd")
    assert first.registry.socket_path == second.registry.socket_path == expected_socket_path
    assert first.registry.spec.name == second.registry.spec.name == "batchd"
    assert first_submission["coalesced"] is False
    assert second_submission["coalesced"] is True
    assert second_submission["job"]["job_id"] == first_submission["job"]["job_id"]
    assert _wait_for_result(first, first_submission["job"]["job_id"])["job"]["result"] == {"a": 2, "z": 1}
    assert first.request({"action": "shutdown"}) == {"ok": True, "shutdown": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_batchd_submit_never_creates_a_process_in_the_request_path(tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    calls = []

    def unexpected_start():
        raise AssertionError("submit must not create batchd")

    monkeypatch.setattr(client, "ensure_started", unexpected_start)
    monkeypatch.setattr(client, "request", lambda payload: calls.append(payload) or {"ok": False, "error": "batchd unavailable"})

    assert client.submit("text_facts", {"text": "queued"}) == {"ok": False, "error": "batchd unavailable"}
    assert client.submit("text_facts", {"text": "fresh"}, fresh_only=True) == {"ok": False, "error": "batchd unavailable"}
    assert calls == [
        {"action": "submit", "task": "text_facts", "payload": {"text": "queued"}, "priority": "freshness", "generation": 0, "coalesce_key": "", "deadline_ms": 0},
        {"action": "submit", "task": "text_facts", "payload": {"text": "fresh"}, "priority": "freshness", "generation": 0, "coalesce_key": "", "deadline_ms": 0, "fresh_only": True},
    ]


@pytest.mark.parametrize("priority", ["interactive", "freshness"])
def test_batchd_timed_out_running_work_keeps_its_slot_and_recovers_after_worker_exit(tmp_path, priority):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    # Past the BACKSTOP, not merely past the deadline.  A running job now carries its deadline into
    # its worker and is given BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS to answer for itself; this row
    # is about work that never does, which is the only case the broker still terminalizes blind.
    timed_out = service._queue_record(
        "text_facts", {"text": "slow"}, priority, 1, "slow",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    timed_out.status = "running"
    timed_out.future = Future()
    waiting = service._queue_record("text_facts", {"text": "wait"}, priority, 1, "wait")

    service._pump()

    assert timed_out.status == "timed_out"
    assert service.common_status()["product_counters"]["text_facts"]["timed_out"] == 1
    # A timed-out job is HISTORICAL work failure and must not read as a CURRENT daemon
    # failure. Publishing it as `last_failure` pinned a healthy, serving batchd to
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


def test_batchd_cancels_queued_work_without_dispatching_it(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_respawns_after_worker_crash_and_restart_accepts_new_work(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    crashed = service._queue_record("text_facts", {"text": "crash"}, "interactive", 1, "crashed")
    crashed.status = "running"
    crashed.future = Future()
    crashed.future.set_exception(BrokenProcessPool("child exited"))

    class BrokenExecutor:
        def __init__(self):
            self._processes = {}

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


def test_batchd_task_registry_generation_is_independent_from_transport_version():
    # v3 added the materialized-product layer (product RPC + last-known-good store + counters).
    # v4 registered the `session_files_view` task; the version fence retires a v3 daemon that lacks it.
    # v5 registered the `tabber_activity_view` task; the fence retires a v4 daemon that lacks it.
    # v6 registered the `metadata_warm_view` task; v7 adds bounded session-files phase diagnostics;
    # v8 bounds snapshot expiry, v9 adds bounded requester attribution, v10 adds metadata-warm work totals, v11 exposes timeouts, v12 records requester attribution at acceptance, v13 projects bounded recent paths for Tabber, v14 adds zero-wait ready-or-receipt products, v15 registers bounded filesystem batches, v16 keeps cold worker starts out of RPC handlers, v17 moves session-files cache pruning out of the web process, v18 adds byte-product relay requests for browser filesystem consumers, v19 adds the bounded `point` scheduler lane that a v18 daemon would reject as an invalid priority, v20 binds filesystem execution to the accepting server's access policy, which a v19 daemon ignores while authorizing every port with its launcher's roots, v21 adds the bounded `mutation` scheduler lane that a v20 daemon would likewise reject as an invalid priority, v22 retires the blocking `relay` action, v23 adds private file-backed artifacts, v24 registers queued-delivery compaction, and v25 classifies shutdown admission refusal as retryable pre-handler busy.
    assert batchd.BATCHD_PROTOCOL_VERSION == 26
    assert "relay" not in batchd.BATCHD_REQUEST_ACTIONS
    assert "filesystem_batch" in batchd.REGISTERED_TASKS
    assert "session_files_cache_prune" in batchd.REGISTERED_TASKS
    assert "session_files_view" in batchd.REGISTERED_TASKS
    assert "tabber_activity_view" in batchd.REGISTERED_TASKS
    assert "metadata_warm_view" in batchd.REGISTERED_TASKS
    assert "queued_delivery_compact" in batchd.REGISTERED_TASKS
    assert batchd.BATCHD_PROTOCOL_VERSION != batchd.LOCAL_RPC_VERSION


def test_batchd_product_serves_last_known_good_bytes_across_the_state_taxonomy(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)

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


def test_batchd_produce_preserves_one_bounded_batch_product_and_caller_delivery_mode(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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

    result = json.loads(batchd.run_registered_task(
        "filesystem_batch",
        json.dumps(_fs_batch_payload(requests=requests, client_scope="browser")).encode("utf-8"),
    ))

    assert [response["id"] for response in result["responses"]] == [request["id"] for request in requests]
    assert all(response["ok"] is True for response in result["responses"])
    assert all(response["payload"]["path"] == str(root) for response in result["responses"])
    assert result["responses"][0]["watch_signature"][0] == str(root)
    assert all("watch_signature" not in response for response in result["responses"][1:])
    with pytest.raises(ValueError, match="at most 64"):
        batchd.run_registered_task(
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

    result = json.loads(batchd.run_registered_task(
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
    large_payload = "x" * (batchd.BATCHD_MAX_RESULT_BYTES + 1024)
    monkeypatch.setattr(
        batchd.filesystem,
        "filesystem_batch_result",
        lambda _payload: {"responses": [{"id": "large", "ok": True, "payload": {"text": large_payload}}]},
    )

    result = batchd.run_registered_task("filesystem_batch", json.dumps(_fs_batch_payload(requests=[])).encode("utf-8"))

    assert len(result) > batchd.BATCHD_MAX_RESULT_BYTES
    assert len(result) <= batchd.BATCHD_MAX_FILESYSTEM_BATCH_RESULT_BYTES


def test_batchd_produce_executes_one_typed_64_item_filesystem_batch(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    requests = [
        {"id": f"item-{index}", "type": "info", "path": str(root), "trigger_counts": {"tree-render": 1}}
        for index in range(64)
    ]
    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = batchd.BatchClient(socket_path)
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


def test_batchd_produce_receipt_does_not_wait_for_cold_executor_start(tmp_path, monkeypatch):
    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
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
    client = batchd.BatchClient(socket_path)
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

    socket_path = tmp_path / "batchd.sock"
    service = batchd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = batchd.BatchClient(socket_path)
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


def test_batchd_older_or_failed_completion_cannot_overwrite_a_newer_product(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
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
        def __init__(self):
            self._processes = {}

        def shutdown(self, **_kwargs):
            return None

    service.executors["bulk"] = BrokenExecutor()  # type: ignore[assignment]
    service._pump()
    assert failing.status == "failed"
    assert json.loads(service.product_store.inline_body("k")) == {"gen": 2}


def test_batchd_product_counters_track_accepted_coalesced_superseded_and_completed(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_status_lists_all_running_records_without_product_payloads(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
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


def test_batchd_status_and_shutdown_cover_every_scheduler_lane_executor(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
    shutdown_pids: list[int] = []

    class Process:
        def __init__(self, pid):
            self.pid = pid
            self._alive = False

        def is_alive(self):
            return self._alive

        def terminate(self):
            pass

        def join(self, timeout=None):
            del timeout

        def kill(self):
            pass

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
        2 + batchd.BATCHD_INTERACTIVE_WORKERS + batchd.BATCHD_POINT_WORKERS + batchd.BATCHD_MUTATION_WORKERS
    )
    assert status["worker_pids"] == [101, 102, 103, 104]
    assert sorted(shutdown_pids) == [101, 102, 103, 104]
    assert set(service.executors) == set(batchd.BATCHD_LANE_PRIORITIES)
    assert all(executor is None for executor in service.executors.values())


def test_batchd_shutdown_waits_for_scheduler_dispatch_before_retiring_executor(tmp_path, monkeypatch):
    """Shutdown cannot sweep a pool while the scheduler is still submitting into it."""

    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    entered_submit = threading.Event()
    release_submit = threading.Event()
    executor_shutdown = threading.Event()
    shutdown_finished = threading.Event()

    class Executor:
        _processes = {}

        def submit(self, *_args):
            entered_submit.set()
            assert release_submit.wait(timeout=2.0), "shutdown never released scheduler dispatch"
            return Future()

        def shutdown(self, **_kwargs):
            executor_shutdown.set()

    executor = Executor()
    monkeypatch.setattr(service, "_executor", lambda *_args: executor)
    service.executor_slots["interactive"][0].executor = executor  # type: ignore[assignment]
    service._queue_record("text_facts", {"text": "late dispatch"}, "interactive", 1, "late-dispatch")
    service._start_scheduler()
    service.scheduler_event.set()
    assert entered_submit.wait(timeout=2.0), "scheduler never reached executor dispatch"

    service.stop_event.set()

    def stop() -> None:
        service._on_shutdown()
        shutdown_finished.set()

    shutdown = threading.Thread(target=stop, name="batchd-shutdown-test")
    shutdown.start()
    assert not executor_shutdown.wait(timeout=0.7), "executor retired before scheduler dispatch stopped"
    release_submit.set()
    shutdown.join(timeout=2.0)

    assert shutdown_finished.is_set()
    assert executor_shutdown.is_set()


def test_shutdown_executor_terminates_a_worker_stuck_mid_task_without_hanging(tmp_path):
    """Forces the real hang red: a worker mid-task at shutdown must not survive `_shutdown_executor`.

    Reproduces the exact defect found via `sample`/`py-spy` on a live macOS batchd: a
    `ProcessPoolExecutor` worker still running when its lane is told to shut down was left
    alive by `shutdown(wait=False, ...)`, so Python's own `multiprocessing.util._exit_function`
    atexit hook later blocked forever inside `wait_for_thread_shutdown` trying to join it, with
    the listening socket already unlinked. Before the fix this worker stays alive well past a
    bounded `join(timeout=2.0)`, since nothing terminates it and its task sleeps 30s. After the
    fix `_shutdown_executor` terminates (then kills, if needed) every process it owns, so the
    worker is provably dead within the bound below.
    """
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
    executor = service._executor("freshness")
    future = executor.submit(_blocking_worker_task)
    deadline = time.monotonic() + 10.0
    while not executor._processes and time.monotonic() < deadline:
        time.sleep(0.05)
    assert executor._processes, "worker process never started"
    workers = list(executor._processes.values())
    assert any(worker.is_alive() for worker in workers), "worker process did not report alive"

    service._shutdown_executor(lane=service._lane_for_priority("freshness"))

    for worker in workers:
        worker.join(timeout=2.0)
        assert not worker.is_alive(), "worker process survived a bounded join after shutdown"
    assert not executor._processes, "executor still tracks a worker process after shutdown"
    future.cancel()


def test_batchd_status_exposes_bounded_request_action_counters(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock")

    service.handle({"action": "ping"})
    service.handle({"action": "status"})
    service.handle({"action": "status"})
    service.handle({"action": "unbounded-client-input"})

    assert service.common_status()["request_counters"] == {"ping": 1, "status": 2, "unknown": 1}


def test_batchd_runtime_status_aggregates_broker_and_reported_workers(tmp_path, monkeypatch):
    client = batchd.BatchClient(tmp_path / "batchd.sock")
    monkeypatch.setattr(client.registry, "status", lambda: {
        "healthy": True,
        "status": {
            "pid": 100,
            "started_at": 123.0,
            "worker_count": 2,
            "worker_pids": [101, 102],
            "owner_invocations": {"batchd_work_graph_rebuild": 7, "provider_metadata_rebuild": 3},
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
    assert status["owner_invocations"] == {"batchd_work_graph_rebuild": 7, "provider_metadata_rebuild": 3}
    assert status["resources"] == {"cpu_percent": 12.5, "rss_bytes": 300, "process_count": 3}


def test_batchd_tracks_per_task_runtime_count_total_and_max(tmp_path, monkeypatch):
    # Per-product runtime totals/maxima (checkbox 10): pure execution duration, excluding queue
    # wait, tracked per task name and surfaced through common_status/runtime_status.
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=2)
    clock_state = {"now": 100.0}
    monkeypatch.setattr(batchd.time, "monotonic", lambda: clock_state["now"])

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


def test_batchd_future_completion_wakes_scheduler_before_poll_interval(tmp_path, monkeypatch):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_result_exposes_wall_clock_running_start_separate_from_runtime_clock(tmp_path, monkeypatch):
    wall_clock = {"now": 1_800_000_000.0}
    runtime_clock = {"now": 100.0}
    monkeypatch.setattr(batchd.time, "time", lambda: wall_clock["now"])
    monkeypatch.setattr(batchd.time, "monotonic", lambda: runtime_clock["now"])
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_records_only_bounded_session_files_phase_aggregates(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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
        "profile": {"work": {"sessions": 2, "entries": 5, "git_spawns": 7, "github_http_calls": 3, "linear_http_calls": 1, "result_bytes": 256, "batchd_work_graph_rebuild": 2, "provider_metadata_rebuild": 1, "unbounded": 99}},
    })
    assert service.common_status()["product_work_totals"]["metadata_warm_view"] == {
        "sessions": 2, "entries": 5, "git_spawns": 7, "github_http_calls": 3, "linear_http_calls": 1, "result_bytes": 256,
    }
    assert service.common_status()["owner_invocations"] == {
        "batchd_work_graph_rebuild": 2,
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


def test_batchd_metadata_owner_invocations_do_not_advance_for_ten_unchanged_submissions(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    result = json.dumps({
        "entries": {},
        "profile": {"work": {
            "sessions": 1,
            "batchd_work_graph_rebuild": 1,
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
        "batchd_work_graph_rebuild": baseline["batchd_work_graph_rebuild"] + 1,
        "provider_metadata_rebuild": baseline["provider_metadata_rebuild"] + 1,
    }


def test_batchd_records_session_files_requester_when_product_is_accepted(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
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


def test_batchd_product_store_evicts_oldest_completion_past_the_bound(tmp_path):
    # The last-known-good product store is bounded independently of the job-record
    # ring (removal/tombstone behavior): once BATCHD_MAX_PRODUCTS distinct coalesce
    # keys have a stored product, completing one more evicts the OLDEST-STORED
    # entry so the store cannot grow unbounded across many distinct products.
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    original_max = batchd.BATCHD_MAX_PRODUCTS
    try:
        batchd.BATCHD_MAX_PRODUCTS = 3
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
        batchd.BATCHD_MAX_PRODUCTS = original_max


# --- recursive-delete deadline control ----------------------------------------------------------
# A recursive delete used to run to completion no matter what the broker decided: batchd's delete arm
# called `filesystem.delete_path(path, recursive=...)` with neither of the two controls that owner
# already accepts, so `_raise_if_delete_stopped` was a no-op on every entry.  The broker meanwhile
# published `timed_out` while the worker kept unlinking.  These rows pin the control end to end.


class _ControlledMonotonic:
    """Replace only `io_ops`'s view of `time`, so a deadline crossing is exact and not timed.

    Patching the real `time.monotonic` would reach pytest and every other module in this process.
    `io_ops` reads its clock through its own module attribute, so replacing that attribute alone
    controls the delete walk and nothing else.  Everything but `monotonic` proxies to the real module.
    """

    def __init__(self, start: float) -> None:
        self.now = float(start)

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def _ordered_tree(tmp_path, count=5):
    """A directory whose entries the delete walk visits in a known order.

    `_delete_directory_contents` sorts by `name.lower()`, so zero-padded names make "the entry it
    stopped at" and "the entries after it" exact rather than incidental.
    """
    root = tmp_path / "tree"
    root.mkdir()
    names = [f"{index:02d}.txt" for index in range(1, count + 1)]
    for name in names:
        (root / name).write_text(name, encoding="utf-8")
    return root, names


def _delete_descriptor(path, *, recursive):
    return json.dumps(_fs_descriptor(op="delete", path=str(path), args={"recursive": recursive})).encode("utf-8")


def test_recursive_delete_stops_at_the_deadline_and_every_later_entry_survives(monkeypatch, tmp_path):
    """The held-worker regression: one entry removed, deadline crossed, nothing after it touched."""
    root, names = _ordered_tree(tmp_path)
    clock = _ControlledMonotonic(1000.0)
    deadline = 1000.5
    monkeypatch.setattr(io_ops, "time", clock)
    real_unlink = os.unlink

    def unlink_then_cross_the_deadline(*args, **kwargs):
        # Hold the walk at exactly one completed removal: the next cooperative check is past the
        # deadline, so the stop point is a decision and not a race with wall-clock time.
        result = real_unlink(*args, **kwargs)
        clock.now = deadline
        return result

    monkeypatch.setattr(os, "unlink", unlink_then_cross_the_deadline)

    with batchd.active_task_control(batchd.BatchedTaskControl(deadline_monotonic=deadline)):
        with pytest.raises(batchd.BatchedFilesystemOperationFailure) as failure:
            batchd._filesystem_operation(_delete_descriptor(root, recursive=True))

    body = failure.value.payload
    assert body["partial"] is True
    assert body["delete_reason"] == "deadline_exceeded"
    assert body["deleted_paths"] == [str(root / names[0])]
    assert body["failed_path"] == str(root / names[1])
    assert not (root / names[0]).exists()
    for name in names[1:]:
        assert (root / name).exists(), f"{name} disappeared after the deadline"
    assert root.exists()


def test_batchd_delete_arm_forwards_the_active_deadline_to_the_filesystem_owner(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def record_delete(path, *, recursive=False, cancel_event=None, deadline_monotonic=None):
        captured.update(
            path=path, recursive=recursive, cancel_event=cancel_event, deadline_monotonic=deadline_monotonic,
        )
        return {"path": path, "deleted": True, "kind": "dir"}

    monkeypatch.setattr(batchd.filesystem, "delete_path", record_delete)

    with batchd.active_task_control(batchd.BatchedTaskControl(deadline_monotonic=1234.5)):
        batchd._filesystem_operation(_delete_descriptor(tmp_path / "subtree", recursive=True))

    assert captured["recursive"] is True
    assert captured["deadline_monotonic"] == 1234.5


def test_a_bounded_delete_carries_no_deadline_because_it_is_one_syscall(monkeypatch, tmp_path):
    """A bounded delete must stay byte-identical: a deadline check could only refuse it."""
    captured: dict[str, object] = {}

    def record_delete(path, *, recursive=False, cancel_event=None, deadline_monotonic=None):
        captured.update(recursive=recursive, deadline_monotonic=deadline_monotonic)
        return {"path": path, "deleted": True, "kind": "file"}

    monkeypatch.setattr(batchd.filesystem, "delete_path", record_delete)

    with batchd.active_task_control(batchd.BatchedTaskControl(deadline_monotonic=1234.5)):
        batchd._filesystem_operation(_delete_descriptor(tmp_path / "one.txt", recursive=False))

    assert captured["recursive"] is False
    assert captured["deadline_monotonic"] is None


def test_no_active_control_leaves_the_recursive_delete_exactly_as_it_was(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def record_delete(path, *, recursive=False, cancel_event=None, deadline_monotonic=None):
        captured.update(deadline_monotonic=deadline_monotonic, cancel_event=cancel_event)
        return {"path": path, "deleted": True, "kind": "dir"}

    monkeypatch.setattr(batchd.filesystem, "delete_path", record_delete)

    batchd._filesystem_operation(_delete_descriptor(tmp_path / "subtree", recursive=True))

    assert captured["deadline_monotonic"] is None
    assert captured["cancel_event"] is None


def test_the_active_task_control_is_installed_and_cleared_around_one_task(tmp_path):
    """One worker runs one task at a time, so the process-local control must not outlive it."""
    seen: list[object] = []

    def observe(_payload: bytes) -> bytes:
        seen.append(batchd.current_task_control().deadline_monotonic)
        return b'{"ok":true}'

    original = dict(batchd.REGISTERED_TASKS)
    batchd.REGISTERED_TASKS["text_facts"] = observe
    try:
        assert batchd.current_task_control().deadline_monotonic is None
        batchd.run_registered_task_result("text_facts", b"{}", batchd.BatchedTaskControl(deadline_monotonic=77.5))
        assert seen == [77.5]
        assert batchd.current_task_control().deadline_monotonic is None
        # A raising task must clear it too, or the next task on this worker inherits a dead deadline.
        def explode(_payload: bytes) -> bytes:
            raise ValueError("task failed")
        batchd.REGISTERED_TASKS["text_facts"] = explode
        with pytest.raises(ValueError):
            batchd.run_registered_task_result("text_facts", b"{}", batchd.BatchedTaskControl(deadline_monotonic=88.5))
        assert batchd.current_task_control().deadline_monotonic is None
    finally:
        batchd.REGISTERED_TASKS.clear()
        batchd.REGISTERED_TASKS.update(original)


@pytest.mark.parametrize("task,payload,expected_key", (
    ("text_facts", {"text": "one two"}, "bytes"),
    ("filesystem_operation", None, "entries"),
))
def test_unrelated_task_types_are_byte_identical_with_and_without_a_control(tmp_path, task, payload, expected_key):
    """The control must be inert for every task that does not read it."""
    if task == "filesystem_operation":
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        encoded = json.dumps(_fs_descriptor(op="list", path=str(tmp_path), args={})).encode("utf-8")
    else:
        encoded = json.dumps(payload).encode("utf-8")

    without = batchd.run_registered_task_result(task, encoded).body
    with_control = batchd.run_registered_task_result(
        task, encoded, batchd.BatchedTaskControl(deadline_monotonic=time.monotonic() + 600.0),
    ).body

    assert json.loads(without.decode("utf-8")).keys() >= {expected_key}
    assert without == with_control


def test_the_dispatched_control_carries_the_absolute_broker_deadline(tmp_path, monkeypatch):
    """Not a relative budget: a budget restarts after cold pool startup and stops too late."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    submitted: list[tuple] = []

    class _CapturingExecutor:
        def submit(self, function, *args):
            submitted.append((function, args))
            return Future()

    monkeypatch.setattr(service, "_executor", lambda _priority="freshness": _CapturingExecutor())
    deadline = time.monotonic() + 30.0
    record = service._queue_record(
        "filesystem_operation", {"op": "delete"}, "interactive", 1, "abs-deadline", deadline_at=deadline,
    )

    service._pump()

    assert len(submitted) == 1
    function, args = submitted[0]
    assert function is batchd.run_registered_task_result
    assert args[0] == record.task
    control = args[2]
    assert isinstance(control, batchd.BatchedTaskControl)
    # Equal to the record's own absolute instant, not a delta derived from it.
    assert control.deadline_monotonic == deadline
    assert control.deadline_monotonic == record.deadline_at


def test_a_deadline_free_record_dispatches_a_control_with_no_deadline(tmp_path, monkeypatch):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    submitted: list[tuple] = []

    class _CapturingExecutor:
        def submit(self, function, *args):
            submitted.append(args)
            return Future()

    monkeypatch.setattr(service, "_executor", lambda _priority="freshness": _CapturingExecutor())
    service._queue_record("text_facts", {"text": "x"}, "freshness", 1, "no-deadline")

    service._pump()

    assert submitted[0][2].deadline_monotonic is None


def test_monotonic_is_comparable_across_the_spawned_worker_boundary():
    """The absolute deadline works only if this platform's monotonic clock is shared, not per-process.

    Deliberately NOT asserted by implementation name.  Linux reports
    `clock_gettime(CLOCK_MONOTONIC)` and Darwin reports `mach_absolute_time()`; pinning the string
    would fail the canonical Darwin gate while proving nothing extra, because the name is not the
    invariant.  The invariant is that a spawned worker's reading falls inside an interval bracketed
    by the parent, which is exactly what an absolute cross-process deadline needs -- and it is
    measured here on whatever platform is running, with no skip and no weaker assertion.
    """
    assert time.get_clock_info("monotonic").monotonic is True
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        pool.submit(time.monotonic).result()  # pay cold-start once, outside the measurement
        readings = []
        for _ in range(3):
            before = time.monotonic()
            inside_worker = pool.submit(time.monotonic).result()
            after = time.monotonic()
            assert before <= inside_worker <= after, (before, inside_worker, after)
            readings.append((before, inside_worker, after))
    # Strengthened beyond the original pin: the bracket must be tight, so a per-process clock whose
    # epoch merely happened to land inside a wide window cannot pass. Each round trip is sub-second,
    # so a worker clock with an independent origin would sit outside the interval, not inside it.
    assert all(after - before < 1.0 for before, _worker, after in readings), readings
    # And the readings must advance with the parent's own clock across rounds.
    assert [worker for _b, worker, _a in readings] == sorted(worker for _b, worker, _a in readings)


def test_a_recursive_delete_honors_its_deadline_across_a_real_process_boundary(tmp_path):
    """End to end through a real spawned worker: an already-expired deadline deletes nothing."""
    root, names = _ordered_tree(tmp_path)
    context = multiprocessing.get_context("spawn")
    control = batchd.BatchedTaskControl(deadline_monotonic=time.monotonic() - 1.0)
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        future = pool.submit(
            batchd.run_registered_task_result, "filesystem_operation", _delete_descriptor(root, recursive=True), control,
        )
        with pytest.raises(batchd.BatchedFilesystemOperationFailure) as failure:
            future.result(timeout=60)

    assert failure.value.payload["delete_reason"] == "deadline_exceeded"
    assert failure.value.payload["deleted_paths"] == []
    for name in names:
        assert (root / name).exists(), f"{name} was deleted after its deadline had already passed"


def test_a_running_record_is_not_terminal_while_its_worker_is_still_inside_the_backstop(tmp_path):
    """No terminal state may be published while the filesystem is still being changed."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "filesystem_operation", {"op": "delete"}, "interactive", 1, "still-deleting",
        deadline_at=time.monotonic() - (batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS / 2.0),
    )
    record.status = "running"
    record.future = Future()

    service._pump()

    assert record.status == "running"
    assert service._record_payload(record)["status"] == "running"

    # The worker's own cooperative stop lands first and owns the terminal state.
    record.future.set_exception(batchd.BatchedFilesystemOperationFailure(409, {
        "partial": True, "delete_reason": "deadline_exceeded", "deleted_paths": ["/tmp/one"],
    }))
    service._pump()

    assert record.status == "failed"
    assert record.failure["filesystem_error"]["deleted_paths"] == ["/tmp/one"]


def test_the_running_backstop_fires_only_after_the_measured_stop_bound(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "filesystem_operation", {"op": "delete"}, "interactive", 1, "wedged",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    record.status = "running"
    record.future = Future()

    service._pump()

    assert record.status == "timed_out"
    assert service.common_status()["product_counters"]["filesystem_operation"]["timed_out"] == 1
    # Capacity accounting is unchanged: the future still holds its slot until the worker exits.
    assert service._future_slots(lane=service._lane_for_priority(record.priority)) == 1


def test_queued_deadline_expiry_stays_exact_and_takes_no_backstop(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "text_facts", {"text": "late"}, "freshness", 1, "queued-late", deadline_at=time.monotonic() - 0.001,
    )

    service._pump()

    assert record.status == "timed_out"
    assert record.error == "deadline exceeded before execution"


def test_a_backstop_timeout_still_publishes_the_paths_the_worker_removed(tmp_path):
    """The backstop can beat the worker's stop; its partial evidence must not be discarded."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "filesystem_operation", {"op": "delete"}, "interactive", 1, "backstopped",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    record.status = "running"
    record.future = Future()

    service._pump()
    assert record.status == "timed_out"

    record.future.set_exception(batchd.BatchedFilesystemOperationFailure(409, {
        "partial": True,
        "delete_reason": "deadline_exceeded",
        "failed_path": "/tmp/tree/03.txt",
        "deleted_paths": ["/tmp/tree/01.txt", "/tmp/tree/02.txt"],
    }))
    service._pump()

    assert record.status == "timed_out", "the backstop already owned the terminal state"
    assert record.future is None, "the future must still be released"
    assert record.failure["filesystem_error"]["deleted_paths"] == ["/tmp/tree/01.txt", "/tmp/tree/02.txt"]
    assert record.failure["status"] == 409
    assert service._record_payload(record)["failure"]["filesystem_error"]["partial"] is True


def test_a_backstop_timeout_still_releases_an_ordinary_abandoned_result(tmp_path):
    """Retaining partial evidence must not change how a plain abandoned result is drained."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "text_facts", {"text": "slow"}, "freshness", 1, "abandoned",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    record.status = "running"
    record.future = Future()

    service._pump()
    assert record.status == "timed_out"

    record.future.set_result(b'{"bytes":4,"lines":1,"nonempty_lines":1}')
    service._pump()

    assert record.future is None
    assert record.status == "timed_out"
    assert not record.failure


def test_running_cancel_is_still_honestly_refused(tmp_path):
    """The deadline mechanism cannot revoke work already dispatched, so cancel must not claim it can.

    `Future.cancel()` always returns False once a task is running, and reaching into the worker
    would need a Manager/Event/shared-memory channel this deliberately does not add.  Refusing is
    the honest answer; a `{"ok": True}` here would tell a browser the delete stopped when it had not.
    """
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record("filesystem_operation", {"op": "delete"}, "interactive", 1, "running-delete")
    record.status = "running"
    record.future = Future()
    record.future.set_running_or_notify_cancel()

    response, _binary = service.handle({"action": "cancel", "job_id": record.job_id})

    assert response["ok"] is False
    assert response["error"] == "job already executing"
    assert record.status == "running"


def test_a_done_worker_result_owns_terminal_state_even_on_a_late_pump(tmp_path):
    """An answer that already arrived is not a timeout, however late the broker looks at it.

    The backstop exists for work that CANNOT stop.  A cooperative delete that already stopped and
    published which entries it removed has answered; if the broker happens not to pump until past
    `deadline_at + backstop`, expiring it first would relabel a real 409 partial result as a
    timeout, and the requester would read "we never heard back" about a delete it did hear back
    about.  So finished futures are processed before the running backstop, always.
    """
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "filesystem_operation", {"op": "delete"}, "interactive", 1, "done-then-late-pump",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    record.status = "running"
    record.future = Future()
    # The worker answered BEFORE this pump; only the broker's look is late.
    record.future.set_exception(batchd.BatchedFilesystemOperationFailure(409, {
        "partial": True,
        "delete_reason": "deadline_exceeded",
        "failed_path": "/tmp/tree/03.txt",
        "deleted_paths": ["/tmp/tree/01.txt", "/tmp/tree/02.txt"],
    }))

    service._pump()

    assert record.status == "failed", "a delivered worker answer must not be relabelled timed_out"
    assert record.failure["status"] == 409
    assert record.failure["filesystem_error"]["partial"] is True
    assert record.failure["filesystem_error"]["deleted_paths"] == ["/tmp/tree/01.txt", "/tmp/tree/02.txt"]
    assert service.common_status()["product_counters"]["filesystem_operation"]["failed"] == 1
    assert service.common_status()["product_counters"]["filesystem_operation"].get("timed_out", 0) == 0
    assert record.future is None, "the slot must be released in the same pump"


def test_a_done_worker_success_also_owns_terminal_state_on_a_late_pump(tmp_path):
    """The same ordering for the ordinary outcome: a completed result is not a timeout either."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "text_facts", {"text": "done"}, "freshness", 1, "done-success-late-pump",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    record.status = "running"
    record.future = Future()
    record.future.set_result(b'{"bytes":4,"lines":1,"nonempty_lines":1}')

    service._pump()

    assert record.status == "completed"
    assert service.common_status()["product_counters"]["text_facts"]["completed"] == 1
    assert record.future is None


def test_work_that_never_answers_still_hits_the_backstop_after_the_reorder(tmp_path):
    """Reordering must not disarm the backstop for a future that is genuinely still running."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    record = service._queue_record(
        "filesystem_operation", {"op": "delete"}, "interactive", 1, "never-answers",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    record.status = "running"
    record.future = Future()

    service._pump()

    assert record.status == "timed_out"
    assert record.future is not None, "an unfinished future keeps holding its slot"
    assert service._future_slots(lane=service._lane_for_priority(record.priority)) == 1


def test_batchd_quarantines_one_slot_and_fences_its_late_result(tmp_path):
    """A kernel-stuck predecessor cannot overwrite the replacement generation's product."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)

    class Executor:
        def __init__(self):
            self._processes = {}
            self.shutdown_calls = 0

        def shutdown(self, **_kwargs):
            self.shutdown_calls += 1

    slot = service.executor_slots["point"][0]
    slot.executor = Executor()  # type: ignore[assignment]
    held = service._queue_record(
        "text_facts", {"text": "held"}, "point", 1, "held",
        deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
    )
    held.status = "running"
    held.future = Future()
    held.executor_slot = 0
    held.executor_generation = 0

    service._pump()

    assert held.status == "timed_out"
    assert slot.executor is None
    assert slot.generation == 1
    assert len(slot.predecessors) == 1
    held.future.set_result(b'{"bytes":4,"lines":1,"nonempty_lines":1}')
    service._pump()
    assert held.result == b""
    assert held.status == "timed_out"


def test_batchd_status_drops_a_shutdown_quarantined_predecessor(tmp_path):
    """A quarantined executor has no process map after shutdown, but status stays available."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    executor = ProcessPoolExecutor(max_workers=1)
    executor.shutdown()
    assert executor._processes is None
    service.executor_slots["point"][0].predecessors.append((0, executor))

    status = service.common_status()

    assert status["ok"] is True
    assert service.executor_slots["point"][0].predecessors == []


def test_batchd_replacement_budget_is_daemon_wide(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)

    class Executor:
        def __init__(self):
            self._processes = {}

        def shutdown(self, **_kwargs):
            return None

    for index, lane in enumerate(("point", "mutation", "bulk")):
        slot = service.executor_slots[lane][0]
        slot.executor = Executor()  # type: ignore[assignment]
        priority = batchd.BATCHD_LANE_PRIORITIES[lane][0]
        record = service._queue_record(
            "text_facts", {"text": lane}, priority, index, lane,
            deadline_at=time.monotonic() - batchd.BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS - 1.0,
        )
        record.status = "running"
        record.future = Future()
        record.executor_slot = 0
        record.executor_generation = 0

    service._pump()

    assert service._quarantined_predecessor_count() == batchd.BATCHD_MAX_QUARANTINED_PREDECESSORS
    assert service.executor_slots["bulk"][0].generation == 0


def test_batchd_broken_slot_does_not_fence_a_healthy_point_sibling(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    failed = service._queue_record("text_facts", {"text": "failed"}, "point", 1, "failed")
    sibling = service._queue_record("text_facts", {"text": "healthy"}, "point", 1, "healthy")
    failed.status = sibling.status = "running"
    failed.future = Future()
    sibling.future = Future()
    failed.executor_slot = 0
    sibling.executor_slot = 1
    failed.future.set_exception(BrokenProcessPool("slot zero exited"))
    sibling.future.set_result(b'{"bytes":7,"lines":1,"nonempty_lines":1}')

    service._pump()

    assert failed.status == "failed"
    assert sibling.status == "completed"
    assert service.executor_slots["point"][0].generation == 1
    assert service.executor_slots["point"][1].generation == 0


def test_batchd_submit_time_broken_slot_does_not_fence_a_healthy_point_sibling(tmp_path, monkeypatch):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    healthy_future = Future()

    class BrokenExecutor:
        def submit(self, *_args):
            raise BrokenProcessPool("slot zero exited before submit")

    class HealthyExecutor:
        def submit(self, *_args):
            return healthy_future

    def executor_for_slot(_priority, slot_index):
        return BrokenExecutor() if slot_index == 0 else HealthyExecutor()

    monkeypatch.setattr(service, "_executor", executor_for_slot)
    failed = service._queue_record("text_facts", {"text": "failed"}, "point", 1, "failed")
    sibling = service._queue_record("text_facts", {"text": "healthy"}, "point", 1, "healthy")

    service._pump()
    healthy_future.set_result(b'{"bytes":7,"lines":1,"nonempty_lines":1}')
    service._pump()

    assert failed.status == "failed"
    assert sibling.status == "completed"
    assert service.executor_slots["point"][0].generation == 1
    assert service.executor_slots["point"][1].generation == 0


def test_batchd_quarantined_predecessor_blocks_idle_and_is_shutdown(tmp_path):
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)

    class Process:
        pid = 4321

        def __init__(self):
            self.alive = True
            self.terminated = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def kill(self):
            self.alive = False

        def join(self, timeout=None):
            del timeout

    class Executor:
        def __init__(self, process):
            self._processes = {process.pid: process}

        def shutdown(self, **_kwargs):
            return None

    process = Process()
    service.executor_slots["point"][0].predecessors.append((0, Executor(process)))  # type: ignore[arg-type]

    assert service._has_active_work() is True
    assert service._idle_should_stop() is False
    service._on_shutdown()
    assert process.terminated is True


def test_queued_expiry_is_unchanged_by_processing_finished_futures_first(tmp_path):
    """A queued record has no future, so the reorder cannot move its exact deadline."""
    service = batchd.PersistentJobBroker(tmp_path / "batchd.sock", workers=1)
    exact = service._queue_record(
        "text_facts", {"text": "late"}, "freshness", 1, "queued-exact", deadline_at=time.monotonic() - 0.001,
    )
    inside = service._queue_record(
        "text_facts", {"text": "early"}, "freshness", 1, "queued-inside", deadline_at=time.monotonic() + 30.0,
    )

    service._pump()

    assert exact.status == "timed_out"
    assert exact.error == "deadline exceeded before execution"
    assert inside.status in {"queued", "running"}

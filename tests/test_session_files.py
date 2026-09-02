import contextlib
import copy as copy_module
import ctypes
import dataclasses
import json
import os
import shutil
import struct
import tempfile
import time
import tracemalloc
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from typing import get_args
from typing import get_origin
from typing import get_type_hints
from urllib.parse import quote

import pytest

import threading as threading_module

import yolomux_lib.app as app_module
from yolomux_lib import common as common_module
from yolomux_lib import sessions as sessions_module
from yolomux_lib import watchd
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib import session_files
from yolomux_lib.sessions import CODEX_TRANSCRIPT_SCAN_LIMIT
from yolomux_lib.types import RepoPayload
from yolomux_lib.types import SessionFileEntry
from yolomux_lib.types import SessionFilesPayload
from yolomux_lib.watchd_protocol import EffectiveWatchConfiguration

from _git_helpers import git
from _git_helpers import init_repo
from tests.browser_helpers.browser_console import validate_server_log_ring_payload
from tests.browser_helpers.browser_console import validate_server_log_ring_transition
from tests.browser_helpers.browser_layout import WebDriverWait
from tests.browser_helpers.browser_layout import new_chrome_driver
from tests.browser_helpers.browser_layout import register_browser_new_document_script
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.gate_harness import gate_http_port
from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.isolated_dev_server import stop_and_reap_daemons
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from tests.terminal_state_guard import assert_terminal_transition
from yolomux_lib.local_services.registry import process_state
from yolomux_lib.observability.queued_delivery import QueuedDeliveryLedger
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.filesystem import exclusions
from yolomux_lib.filesystem import paths as filesystem_paths
from yolomux_lib.infra import batchd as batchd_module
from yolomux_lib.infra.common import runtime_root
from yolomux_lib.infra.host_partition import host_partitioned_state_dir


@contextlib.contextmanager
def pinned_test_snapshot_runner(repo: Path):
    with session_files.pinned_session_git_scope(repo, operation="test_session_files_git") as scope:
        yield session_files.pinned_snapshot_runner(scope, operation="testSessionFilesGit")


def agent(kind, transcript, cwd, session="s1"):
    return AgentInfo(
        session=session,
        kind=kind,
        pid=1,
        pane_target="%1",
        command=kind,
        cwd=str(cwd),
        status=None,
        session_id=None,
        transcript=str(transcript),
        error=None,
    )


def tuple_return_args(value):
    assert get_origin(value) is tuple
    return get_args(value)


def dict_return_args(value):
    assert get_origin(value) is dict
    return get_args(value)


def assert_e3_causal_ceilings(observed: dict[str, int], ceilings: dict[str, int]) -> None:
    """Fail closed on the frozen E3 physical-fixture budgets, including the loop control."""
    assert set(observed) == set(ceilings), (observed, ceilings)
    overruns = {name: observed[name] for name, limit in ceilings.items() if observed[name] > limit}
    assert not overruns, f"session-files causal ceiling exceeded: observed={observed} ceilings={ceilings} overruns={overruns}"


class SessionFilesDiskEventObserver:
    """Read the kernel events for the two E3 durable owners, without product instrumentation."""

    _EVENT = struct.Struct("iIII")
    _IN_CLOSE_WRITE = 0x00000008
    _IN_MOVED_FROM = 0x00000040
    _IN_MOVED_TO = 0x00000080
    _IN_DELETE = 0x00000200

    def __init__(self, *directories: Path):
        if os.name != "posix" or not Path("/proc").is_dir():
            pytest.skip("E3 physical disk gate requires Linux inotify")
        libc = ctypes.CDLL(None, use_errno=True)
        self._init = libc.inotify_init1
        self._init.argtypes = [ctypes.c_int]
        self._init.restype = ctypes.c_int
        self._add_watch = libc.inotify_add_watch
        self._add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._add_watch.restype = ctypes.c_int
        self.fd = self._init(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            error = ctypes.get_errno()
            pytest.skip(f"E3 physical disk gate could not allocate inotify: {os.strerror(error)}")
        self.names: dict[int, Path] = {}
        mask = self._IN_CLOSE_WRITE | self._IN_MOVED_FROM | self._IN_MOVED_TO | self._IN_DELETE
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                descriptor = self._add_watch(self.fd, os.fsencode(directory), mask)
                if descriptor < 0:
                    error = ctypes.get_errno()
                    pytest.skip(f"E3 physical disk gate could not watch {directory}: {os.strerror(error)}")
                self.names[descriptor] = directory
        except BaseException:
            os.close(self.fd)
            raise

    def clear(self) -> None:
        self.snapshot()

    def snapshot(self) -> dict[str, int]:
        close_writes = renames = unlinks = payload_writes = metadata_writes = event_writes = 0
        closed_paths: set[Path] = set()
        pending_moves: dict[int, bool] = {}
        while True:
            try:
                data = os.read(self.fd, 64 * 1024)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(data):
                descriptor, mask, cookie, length = self._EVENT.unpack_from(data, offset)
                offset += self._EVENT.size
                name = data[offset:offset + length].rstrip(b"\\0").decode("utf-8", errors="replace")
                offset += length
                directory = self.names.get(descriptor, Path())
                path = directory / name
                if mask & self._IN_CLOSE_WRITE:
                    close_writes += 1
                    closed_paths.add(path)
                if mask & self._IN_MOVED_FROM:
                    pending_moves[cookie] = path in closed_paths
                if mask & self._IN_MOVED_TO and pending_moves.pop(cookie, False):
                    renames += 1
                    # Atomic writers close a randomized sibling then rename it. Classify the
                    # durable write by its destination, not that temporary sibling's name.
                    if path.name == "client-events.json":
                        event_writes += 1
                    elif path.name.endswith(".manifest.json") or path.name.startswith("cache-index"):
                        metadata_writes += 1
                    elif path.name.endswith(".json"):
                        payload_writes += 1
                if mask & self._IN_DELETE:
                    unlinks += 1
        return {
            "close_writes": close_writes,
            "renames": renames,
            "unlinks": unlinks,
            "payload_writes": payload_writes,
            "metadata_writes": metadata_writes,
            "event_writes": event_writes,
        }

    def close(self) -> None:
        os.close(self.fd)


def operation_terminal_response(server, status_url, timeout=10):
    deadline = time.monotonic() + timeout
    port = server.port if hasattr(server, "port") else server.server_address[1]
    while True:
        connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.request("GET", status_url)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        if response.status != HTTPStatus.ACCEPTED:
            return response.status, payload
        if time.monotonic() >= deadline:
            raise AssertionError(f"operation did not become terminal: {status_url}")
        time.sleep(0.02)


@pytest.fixture
def isolated_real_batchd_runtime(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "batchd-runtime"
    monkeypatch.setattr(batchd_module, "RUNTIME_DIR", runtime_dir)
    return runtime_dir


def retire_expected_session_files_failure_logs(
    server,
    *,
    request_id,
    operation_id,
    stack_operation,
    expect_transport,
    expected_code="service_unavailable",
):
    """Retire one exact correlated session-files failure from this fixture boundary."""

    start = validate_server_log_ring_payload(server._fixture_server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    failures = [
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    owners = [(entry.get("source"), entry.get("category")) for entry in failures]
    structured_owners = [
        ("batchd-operation", "operation"),
        ("api-response", "api"),
    ]
    assert transition["droppedCount"] == 0, transition
    if expect_transport:
        # The transport owner deliberately deduplicates identical submit failures for five
        # seconds. The typed operation and API owners are never deduplicated and must remain exact.
        assert owners in [
            [("local-service:batchd", "transport"), *structured_owners],
            structured_owners,
        ], failures
    else:
        assert owners == structured_owners, failures

    if owners[0] == ("local-service:batchd", "transport"):
        transport = failures[0]
        message = str(transport.get("message") or "")
        assert message.startswith("action=submit request_id="), transport
        transport_request_id = message.removeprefix("action=submit request_id=").split(maxsplit=1)[0]
        assert len(transport_request_id) == 32 and all(
            character in "0123456789abcdef" for character in transport_request_id
        ), transport
        assert "FileNotFoundError" in message, transport

    structured = [json.loads(entry["message"]) for entry in failures[-2:]]
    for payload in structured:
        assert payload["request"]["id"] == request_id, payload
        assert payload["code"] == expected_code, payload
        assert payload["origin"] == "local_services.batchd", payload
        assert payload["stack"][0]["operation"] == "GET /api/session-files", payload
        assert payload["stack"][-1]["operation"] == stack_operation, payload
    assert str(structured[0]["operation"]["id"] or "") == str(operation_id or ""), structured[0]
    assert structured[1].get("operation") is None, structured[1]
    server._fixture_server_log_boundary = current
    return tuple(failures)


def test_session_files_payload_types_cover_builder_shapes_and_annotations():
    assert {
        "session",
        "agent",
        "agent_windows",
        "abs_path",
        "size",
        "missing",
        "source",
        "added",
        "removed",
        "diff_tracked",
        "uploaded",
    } <= set(SessionFileEntry.__annotations__)
    assert {"branch", "from_ref", "to_ref", "error", "error_message", "ahead", "behind"} <= set(RepoPayload.__annotations__)
    assert {"hours", "warnings", "cache", "error", "refreshing_elsewhere"} <= set(SessionFilesPayload.__annotations__)

    assert get_type_hints(session_files.session_file_entry)["return"] == SessionFileEntry | None
    assert get_type_hints(session_files.session_files_payload_for_info)["return"] is SessionFilesPayload
    assert tuple_return_args(get_type_hints(session_files.session_files_payload)["return"]) == (SessionFilesPayload, HTTPStatus)

    assert get_type_hints(TmuxWebtermApp.cached_session_files_payload_for_info)["return"] is SessionFilesPayload
    assert dict_return_args(get_type_hints(TmuxWebtermApp.cached_session_files_payloads_for_infos)["return"]) == (str, SessionFilesPayload)
    assert tuple_return_args(get_type_hints(TmuxWebtermApp.session_files_payload_for_infos)["return"]) == (SessionFilesPayload, HTTPStatus)
    assert tuple_return_args(get_type_hints(TmuxWebtermApp.session_files_payload)["return"]) == (SessionFilesPayload, HTTPStatus)


def test_session_files_scheduler_lease_keeps_batchd_alive_through_next_demand(
    isolated_real_batchd_runtime, monkeypatch, tmp_path,
):
    monkeypatch.setenv("YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS", "0.1")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    webapp = TmuxWebtermApp([])
    webapp.refresh_sessions = lambda: []
    assert batchd_module.RUNTIME_DIR == isolated_real_batchd_runtime
    assert webapp.job_client.socket_path == batchd_module.default_socket_path()
    assert webapp.job_client.start_for_scheduler()
    first_pid = int(webapp.job_client.registry._read_record()["pid"])
    server = thread = None
    try:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            record = webapp.job_client.registry._read_record()
            recorded_pid = int(record.get("pid") or 0)
            if process_state(first_pid) in {"", "Z"} or recorded_pid != first_pid:
                break
            time.sleep(0.02)
        assert process_state(first_pid) not in {"", "Z"}
        assert int(webapp.job_client.registry._read_record().get("pid") or 0) == first_pid
        assert webapp.job_client.socket_path.exists()

        server, thread = start_browser_server(monkeypatch, tmp_path, webapp, auth_bypass=True)
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        connection.request("GET", "/api/session-files?fresh_git=1")
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == HTTPStatus.ACCEPTED
        terminal_status, terminal = operation_terminal_response(server, body["operation"]["status_url"])
        assert terminal_status == HTTPStatus.OK
        assert isinstance(terminal["data"].get("files"), list)
        retained_pid = int(webapp.job_client.registry._read_record()["pid"])
        assert retained_pid == first_pid
        assert process_state(first_pid) not in {"", "Z"}
        assert webapp.job_client.socket_path.exists()
        assert webapp.job_client.registry.healthy()
    finally:
        if server is not None:
            stop_browser_server(server, thread)
        process = webapp.job_client.registry.process
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        webapp.control_server.stop()


def test_session_files_public_start_failure_is_typed_terminal_not_queued(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.BatchClient, "start_for_scheduler", lambda self: False)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    webapp = TmuxWebtermApp([])
    webapp.refresh_sessions = lambda: []
    webapp.job_client = app_module.BatchClient(tmp_path / "services" / "batchd.sock")
    monkeypatch.setattr(webapp.job_client.registry, "_spawn", lambda: None)
    server = thread = None
    try:
        server, thread = start_browser_server(monkeypatch, tmp_path, webapp, auth_bypass=True)
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        connection.request("GET", "/api/session-files?fresh_git=1")
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
        assert body["state"] == "failed"
        assert body["request"]["id"].startswith("r-")
        assert body["error"]["code"] == "service_unavailable"
        assert body["error"]["origin"] == "local_services.batchd"
        assert body["error"]["retryable"] is False
        assert body["error"]["stack"][-1]["operation"] == "batchd.submit"
        assert "operation" not in body
        retired = retire_expected_session_files_failure_logs(
            server,
            request_id=body["request"]["id"],
            operation_id=None,
            stack_operation="batchd.submit",
            expect_transport=True,
        )
        assert len(retired) in {2, 3}
    finally:
        if server is not None:
            stop_browser_server(server, thread)
        webapp.control_server.stop()


def test_session_files_stale_background_refresh_publishes_materialized_payload(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    fresh_payload = {"session": "5", "loaded": True, "repos": [{"repo": "/fresh"}], "files": [], "errors": []}
    published = []
    monkeypatch.setattr(webapp, "background_refresh_event_details", lambda *_args, **_kwargs: {"session": "5", "cache_key_hash": "ready"})
    monkeypatch.setattr(webapp, "log_sampled_background_refresh_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(webapp, "compute_session_files_cache_entry", lambda *_args, **_kwargs: (fresh_payload, HTTPStatus.OK, False, 0.0))
    monkeypatch.setattr(webapp, "publish_session_files_ready_payload", lambda request, payload, status, **kwargs: published.append((request, payload, status, kwargs)) or True)
    monkeypatch.setattr(webapp, "publish_background_refresh_done", lambda *_args, **_kwargs: None)
    try:
        webapp.refresh_session_files_cache(("payload",), "5", {}, 24.0, "HEAD", "current", {}, "background-refresh", "background-refresh")
    finally:
        webapp.control_server.stop()

    assert published == [
        ({"session": "5", "hours": 24.0, "from_ref": "HEAD", "to_ref": "current", "repo_refs": {}}, fresh_payload, HTTPStatus.OK,
         {"trigger": "background-refresh", "compute_ms": pytest.approx(published[0][3]["compute_ms"])})
    ]


def test_session_files_refresh_callers_share_one_publication_owner():
    webapp = app_module.TmuxWebtermApp(["5"])
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))

    webapp._session_files_coordinator.refresh_session_files_cache = capture
    try:
        webapp.refresh_session_files_cache(("payload",), "5", {"5": info}, 24.0, "HEAD", "current", {}, "background-refresh", "background-refresh")
        webapp.refresh_session_files_cache(("info",), "5", {"5": info}, 24.0, "HEAD", "current", {}, "background-info-refresh", "background-info-refresh")
    finally:
        webapp.control_server.stop()

    assert [kwargs["requester"] for _args, kwargs in calls] == ["background-refresh", "background-info-refresh"]
    assert [kwargs["trigger"] for _args, kwargs in calls] == ["background-refresh", "background-info-refresh"]
    assert calls[0][0][2:4] == ("5", {"5": info})
    assert calls[1][0][2:4] == ("5", {"5": info})


def test_session_files_http_payload_issues_canonical_descriptor_for_symlinked_repo_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    webapp = app_module.TmuxWebtermApp([])
    payload = {"session": "1", "loaded": True, "repos": [], "files": [], "errors": []}
    webapp.session_files_payload = lambda *_args, **_kwargs: (payload, HTTPStatus.OK)
    try:
        result, status = webapp.session_files_http_payload(
            "1", 24.0, "HEAD", "current", {str(alias): {"from": "HEAD~1", "to": "current"}},
        )
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    descriptor = result["data"]["cache"]["request_descriptor"]
    assert descriptor == webapp.session_files_request_descriptor(
        "1", 24.0, "HEAD", "current", {str(repo): {"from": "HEAD~1", "to": "current"}},
    )
    assert len(descriptor) == 64 and str(alias) not in descriptor


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="requires two fixture-owned server processes")
def test_session_files_cache_view_crosses_real_owner_follower_processes(monkeypatch, tmp_path, gate_http_port):
    """A follower reads only the owner-published opaque cache view through its own HTTP server."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "changed.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "changed.py")
    git(repo, "commit", "-m", "seed")
    (repo / "changed.py").write_text("value = 2\n", encoding="utf-8")
    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path / "tmux", session_count=1, session_cwd=repo)
    shared_state = tmp_path / "shared" / "state"
    shared_runtime = tmp_path / "shared" / "runtime"
    shared_runtime.mkdir(parents=True)
    owner_paths = build_paths(tmp_path / "owner", state_dir=shared_state)
    follower_paths = build_paths(tmp_path / "follower", state_dir=shared_state)

    def git_view_footprint(*roots: Path) -> tuple[int, int]:
        entries = [
            entry
            for root in roots
            for entry in root.rglob("yolomux-git-view-*")
            if entry.is_file()
        ]
        return len(entries), sum(entry.stat().st_blocks * 512 for entry in entries)

    def process_io(pid: int) -> dict[str, int]:
        values = {}
        for line in Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines():
            name, value = line.split(":", 1)
            if name in {"write_bytes", "cancelled_write_bytes"}:
                values[name] = int(value.strip())
        return values

    def process_environment_contains(pid: int, name: str, value: Path) -> bool:
        return f"{name}={value}".encode("utf-8") in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")

    frozen_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    frozen_object_count = int(next(
        line.split(":", 1)[1].strip()
        for line in git(repo, "count-objects", "-v").stdout.splitlines()
        if line.startswith("count:")
    ))
    owner = follower = None
    try:
        owner_port = gate_http_port.release()
        owner = start_isolated_dev_server(
            "session-files-owner",
            Path(__file__).resolve().parents[1],
            owner_paths,
            runtime,
            env_overrides={"YOLOMUX_RUNTIME_DIR": str(shared_runtime)},
            port=owner_port,
        )
        follower = start_isolated_dev_server(
            "session-files-follower",
            Path(__file__).resolve().parents[1],
            follower_paths,
            runtime,
            env_overrides={
                "YOLOMUX_RUNTIME_DIR": str(shared_runtime),
                "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": str(owner.port),
            },
        )
        assert process_environment_contains(owner.process.pid, "TMPDIR", owner_paths.tmp_dir), "owner child did not inherit its fixture TMPDIR"
        assert process_environment_contains(follower.process.pid, "TMPDIR", follower_paths.tmp_dir), "follower child did not inherit its fixture TMPDIR"
        temp_before = git_view_footprint(owner_paths.tmp_dir, follower_paths.tmp_dir)
        io_before = {
            name: process_io(owner.process.pid).get(name, 0) + process_io(follower.process.pid).get(name, 0)
            for name in ("write_bytes", "cancelled_write_bytes")
        }
        session = runtime.sessions[0]
        request_path = f"/api/session-files?session={quote(session, safe='')}&hours=24&force=1"
        connection = HTTPConnection("127.0.0.1", owner.port, timeout=10)
        connection.request("GET", request_path)
        receipt_response = connection.getresponse()
        receipt = json.loads(receipt_response.read().decode("utf-8"))
        connection.close()
        assert receipt_response.status == HTTPStatus.ACCEPTED
        terminal_status, terminal = operation_terminal_response(owner, receipt["operation"]["status_url"], timeout=20)
        assert terminal_status == HTTPStatus.OK
        assert terminal["state"] == "ready"
        views = sorted(
            path for path in shared_state.rglob("session-files-cache/*.json")
            if not path.name.endswith(".manifest.json") and path.name not in {"index.json", "cache-index.json"}
        )
        assert len(views) == 1
        view_id = views[0].stem
        assert len(view_id) == 64
        # Mutate the fixture-owned repository only after the first accepted operation has
        # terminalized. A real changed worktree must produce one newer canonical generation,
        # rather than reusing the prior completion or merely replaying its event.
        (repo / "new-after-terminal.py").write_text("value = 3\n", encoding="utf-8")
        connection = HTTPConnection("127.0.0.1", owner.port, timeout=10)
        connection.request("GET", f"{request_path}&fresh_git=1")
        changed_receipt_response = connection.getresponse()
        changed_receipt = json.loads(changed_receipt_response.read().decode("utf-8"))
        connection.close()
        assert changed_receipt_response.status == HTTPStatus.ACCEPTED
        changed_terminal_status, changed_terminal = operation_terminal_response(owner, changed_receipt["operation"]["status_url"], timeout=20)
        assert changed_terminal_status == HTTPStatus.OK and changed_terminal["state"] == "ready"
        changed_record = json.loads(views[0].read_text(encoding="utf-8"))
        assert any(file.get("path") == "new-after-terminal.py" for file in changed_terminal["data"].get("files", []))
        connection = HTTPConnection("127.0.0.1", follower.port, timeout=10)
        connection.request("GET", f"/api/session-files?session={quote(session, safe='')}&hours=24&cache_only=1&cache_view={view_id}")
        follower_response = connection.getresponse()
        follower_payload = json.loads(follower_response.read().decode("utf-8"))
        connection.close()
        assert follower_response.status == HTTPStatus.OK
        assert follower_payload["state"] == "ready"
        expected_payload = dict(changed_terminal["data"])
        expected_payload.pop("cache", None)
        follower_payload["data"].pop("cache", None)
        assert follower_payload["data"] == expected_payload
        follower.restart()
        replay_connection = HTTPConnection("127.0.0.1", follower.port, timeout=10)
        replay_connection.request("GET", f"/api/session-files?session={quote(session, safe='')}&hours=24&cache_only=1&cache_view={view_id}")
        replay_response = replay_connection.getresponse()
        replay_payload = json.loads(replay_response.read().decode("utf-8"))
        replay_connection.close()
        assert replay_response.status == HTTPStatus.OK
        replay_payload["data"].pop("cache", None)
        assert replay_payload["data"] == expected_payload
        connection = HTTPConnection("127.0.0.1", follower.port, timeout=10)
        connection.request("GET", f"/api/session-files?session={quote(session, safe='')}&hours=24&from=other&cache_only=1&cache_view={view_id}")
        mismatch_response = connection.getresponse()
        mismatch = json.loads(mismatch_response.read().decode("utf-8"))
        connection.close()
        assert mismatch_response.status == HTTPStatus.ACCEPTED
        assert mismatch["state"] == "queued" and mismatch["status"] == "pending"
        # This is a causal fixture, not an ambient disk probe: its repository identity and all
        # server-written paths are frozen below tmp_path. One logical generation leaves no private
        # Git view behind, cannot mutate the repository, and remains below a deliberately generous
        # process-I/O ceiling that would catch the original rapid rewrite loop.
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == frozen_head
        assert int(next(line.split(":", 1)[1].strip() for line in git(repo, "count-objects", "-v").stdout.splitlines() if line.startswith("count:"))) == frozen_object_count
        assert git_view_footprint(owner_paths.tmp_dir, follower_paths.tmp_dir) == temp_before
        io_after = {
            name: process_io(owner.process.pid).get(name, 0) + process_io(follower.process.pid).get(name, 0)
            for name in ("write_bytes", "cancelled_write_bytes")
        }
        temp_after = git_view_footprint(owner_paths.tmp_dir, follower_paths.tmp_dir)
        assert_e3_causal_ceilings(
            {
                # The fixture performs two owner generations (before and after the owned repo
                # mutation), one follower opaque read, one stale owner revalidation, and one
                # mismatched follower refusal. Each is independently
                # required by this causal sequence; another request is a loop.
                "http_requests": 5,
                "canonical_views": len(views),
                "temporary_view_files": temp_after[0],
                "temporary_view_allocated_bytes": temp_after[1],
                "write_bytes": io_after["write_bytes"] - io_before["write_bytes"],
                "cancelled_write_bytes": io_after["cancelled_write_bytes"] - io_before["cancelled_write_bytes"],
            },
            {
                "http_requests": 5,
                "canonical_views": 1,
                "temporary_view_files": temp_before[0],
                "temporary_view_allocated_bytes": temp_before[1],
                "write_bytes": 64 * 1024 * 1024,
                "cancelled_write_bytes": 64 * 1024 * 1024,
            },
        )
    finally:
        if follower is not None:
            stop_and_reap_daemons(follower)
        if owner is not None:
            stop_and_reap_daemons(owner)
        stop_isolated_tmux_runtime(runtime)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="requires fixture-owned Linux processes")
def test_session_files_one_generation_physical_disk_gate(monkeypatch, tmp_path, gate_http_port):
    """One unchanged generation has one producer and bounded durable disk effects."""

    fixture_root = Path(tempfile.mkdtemp(prefix="e3-"))
    repo = fixture_root / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "changed.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "changed.py")
    git(repo, "commit", "-m", "seed")
    (repo / "changed.py").write_text("value = 2\n", encoding="utf-8")
    runtime = start_isolated_tmux_runtime(monkeypatch, fixture_root / "tmux", session_count=1, session_cwd=repo)
    runtime_base = fixture_root / "runtime"
    shared_state = fixture_root / "state"
    owner_paths = build_paths(fixture_root / "owner", state_dir=shared_state)
    follower_paths = build_paths(fixture_root / "follower", state_dir=shared_state)
    fixture_tmp = fixture_root / "tmp"
    fixture_tmp.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(fixture_tmp))
    monkeypatch.setenv("YOLOMUX_RUNTIME_DIR", str(runtime_base))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = None

    def process_io(pid: int) -> dict[str, int]:
        values = {"write_bytes": 0, "cancelled_write_bytes": 0}
        for line in Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines():
            name, value = line.split(":", 1)
            if name in values:
                values[name] = int(value.strip())
        return values

    def process_tmpdir(pid: int) -> Path:
        values = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        entry = next(value for value in values if value.startswith(b"TMPDIR="))
        return Path(entry.removeprefix(b"TMPDIR=").decode("utf-8"))

    def qualified_pids(status: dict[str, object]) -> tuple[int, ...]:
        pids = (owner.process.pid, follower.process.pid, int(status["pid"]), *(int(pid) for pid in status["worker_pids"]))
        assert len(pids) == len(set(pids)), pids
        assert all(pid > 0 and process_state(pid) not in {"", "Z"} for pid in pids)
        return pids

    def temp_footprint() -> tuple[int, int]:
        files = [path for path in fixture_root.rglob("yolomux-git-view-*") if path.is_file()]
        return len(files), sum(path.stat().st_blocks * 512 for path in files)

    def warm_batchd_lane(priority: str) -> None:
        response = job_client.submit(
            "json_compact", {"e3": "warm", "priority": priority}, priority=priority,
            generation=1, coalesce_key=f"e3-physical-warm-{priority}", deadline_ms=5_000,
        )
        assert response["ok"] is True
        deadline = time.monotonic() + 10.0
        while True:
            result = job_client.result(response["job"]["job_id"], timeout=1.0)
            if result.get("job", {}).get("status") == "completed":
                return
            assert time.monotonic() < deadline, result
            time.sleep(0.02)

    owner = follower = observer = job_client = differ = finder = None
    try:
        effective_runtime = runtime_root(environ={"YOLOMUX_RUNTIME_DIR": str(runtime_base)})
        job_client = batchd_module.BatchClient(effective_runtime / "services" / batchd_module.BATCHD_SOCKET_NAME)
        # The scheduler lease establishes the exact broker before the boundary, so the worker
        # process counters are comparable rather than born halfway through the measurement.
        assert job_client.start_for_scheduler()
        # Both session-files lanes have one stable worker before the physical boundary. The warm
        # products are a fixture lifecycle cost, not part of the session-files counters below.
        warm_batchd_lane("interactive")
        warm_batchd_lane("freshness")
        owner = start_isolated_dev_server(
            "session-files-physical-owner", Path(__file__).resolve().parents[1], owner_paths, runtime,
            env_overrides={"YOLOMUX_RUNTIME_DIR": str(runtime_base)}, port=gate_http_port.release(),
        )
        follower = start_isolated_dev_server(
            "session-files-physical-follower", Path(__file__).resolve().parents[1], follower_paths, runtime,
            env_overrides={"YOLOMUX_RUNTIME_DIR": str(runtime_base), "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": str(owner.port)},
        )
        status_before = job_client.runtime_status()
        pids_before = qualified_pids(status_before)
        assert Path(tempfile.gettempdir()).is_relative_to(fixture_root)
        assert process_tmpdir(owner.process.pid).is_relative_to(fixture_root)
        assert process_tmpdir(follower.process.pid).is_relative_to(fixture_root)
        assert all(process_tmpdir(pid).is_relative_to(fixture_root) for pid in pids_before[2:])
        cache_dir = host_partitioned_state_dir(shared_state) / "session-files-cache"
        events_dir = host_partitioned_state_dir(shared_state) / "background-owner"
        frozen_head = git(repo, "rev-parse", "HEAD").stdout.strip()
        session = runtime.sessions[0]
        # The measured boundary starts before the sole ordinary accepted request. Do not fabricate a
        # disk-only expiry while this real server retains a valid memory entry: users cannot make
        # that split state through the API.
        request_path = f"/api/session-files?session={quote(session, safe='')}&hours=24&from=HEAD&to=current"
        observer = SessionFilesDiskEventObserver(cache_dir, events_dir)
        observer.clear()
        io_before = {name: sum(process_io(pid)[name] for pid in pids_before) for name in ("write_bytes", "cancelled_write_bytes")}
        git_before = temp_footprint()
        direct_session_files_requests = [0]
        connection = HTTPConnection("127.0.0.1", owner.port, timeout=10)
        direct_session_files_requests[0] += 1
        connection.request("GET", request_path)
        fresh_response = connection.getresponse()
        fresh_receipt = json.loads(fresh_response.read().decode("utf-8"))
        connection.close()
        assert fresh_response.status == HTTPStatus.ACCEPTED
        terminal_status, terminal = operation_terminal_response(owner, fresh_receipt["operation"]["status_url"], timeout=20)
        assert terminal_status == HTTPStatus.OK and terminal["state"] == "ready"
        views = sorted(path for path in cache_dir.glob("*.json") if not path.name.endswith(".manifest.json") and path.name not in {"index.json", "cache-index.json"})
        assert len(views) == 1
        view_id = views[0].stem

        connection = HTTPConnection("127.0.0.1", follower.port, timeout=10)
        direct_session_files_requests[0] += 1
        connection.request("GET", f"/api/session-files?session={quote(session, safe='')}&hours=24&from=HEAD&to=current&cache_only=1&cache_view={view_id}")
        follower_response = connection.getresponse()
        follower_payload = json.loads(follower_response.read().decode("utf-8"))
        connection.close()
        assert follower_response.status == HTTPStatus.OK and follower_payload["state"] == "ready"
        for name, destination, panel, state_expression in (
            ("differ", "differ", "#panel-__differ__", "fileExplorerSessionFilesState"),
            ("finder", "finder", "#panel-__finder__", "fileExplorerFinderSessionFilesState"),
        ):
            surface = new_chrome_driver(profile_dir=fixture_root / name)
            register_browser_new_document_script(
                surface,
                "window.__e3SessionFilesRequests = [];"
                "window.__e3SessionFilesApplications = 0;"
                "const originalFetch = window.fetch.bind(window);"
                "const originalApply = setSessionFilesPayloadForDestination;"
                "setSessionFilesPayloadForDestination = (...args) => {"
                " window.__e3SessionFilesApplications += 1;"
                " return originalApply(...args);"
                "};"
                "window.fetch = (...args) => {"
                " const target = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.href);"
                " if (target.pathname === '/api/session-files') window.__e3SessionFilesRequests.push(target.search);"
                " return originalFetch(...args);"
                "};",
            )
            surface.get(f"http://127.0.0.1:{follower.port}/?sessions=__{destination}__,{quote(session, safe='')}&layout=left&tabs=left:__{destination}__")
            WebDriverWait(surface, 12).until(lambda driver, panel=panel: driver.execute_script(f"return Boolean(document.querySelector('{panel}'));"))
            WebDriverWait(surface, 12).until(lambda driver, state_expression=state_expression: driver.execute_script(f"return {state_expression}?.payload?.loaded === true;"))
            WebDriverWait(surface, 12).until(lambda driver: driver.execute_script("return clientEventTransportState?.connected === true;"))
            requests = surface.execute_script("return [...window.__e3SessionFilesRequests];")
            initial_read = f"?from=HEAD&to=current&session={quote(session, safe='')}&hours=24"
            cache_read = f"{initial_read}&cache_only=1&cache_view={view_id}"
            # The initial HTML may already contain the follower's coherent cache view.  In that
            # case zero browser fetches is strictly better than an opaque revalidation; otherwise
            # permit the initial read and its one completion-view read.
            assert requests in ([], [initial_read], [initial_read, cache_read]), requests
            if destination == "differ":
                differ = surface
            else:
                finder = surface
        for surface in (differ, finder):
            surface.execute_script("window.__e3SessionFilesApplications = 0;")
        for surface, state_expression in ((differ, "fileExplorerSessionFilesState"), (finder, "fileExplorerFinderSessionFilesState")):
            before_requests = surface.execute_script("return [...window.__e3SessionFilesRequests];")
            before_signature = surface.execute_script(f"return {state_expression}.signature;")
            assert surface.execute_script(
                "const prior = clientEventTransportState.source;"
                "prior.onerror();"
                "closeClientEventStream();"
                "syncClientEventDemand({immediate: true});"
                "return clientEventTransportState.source !== prior;"
            )
            WebDriverWait(surface, 12).until(lambda driver: driver.execute_script("return clientEventTransportState?.connected === true;"))
            assert surface.execute_script("return [...window.__e3SessionFilesRequests];") == before_requests
            assert surface.execute_script(f"return {state_expression}.signature;") == before_signature
            assert surface.execute_script("return window.__e3SessionFilesApplications;") == 0
        status_after = job_client.runtime_status()
        pids_after = qualified_pids(status_after)
        assert set(pids_before).issubset(pids_after), "physical E3 gate refuses a worker replacement inside one generation"
        assert len(pids_after) <= len(pids_before) + 1, "browser cache delivery may activate one pre-warmed batchd lane, not accumulate workers"
        product_before = status_before["product_counters"].get("session_files_view", {})
        product_after = status_after["product_counters"].get("session_files_view", {})
        work_before = status_before["product_work_totals"].get("session_files_view", {})
        work_after = status_after["product_work_totals"].get("session_files_view", {})
        events = observer.snapshot()
        io_after = {name: sum(process_io(pid)[name] for pid in pids_after) for name in ("write_bytes", "cancelled_write_bytes")}
        observed = {
            "direct_session_files_requests": direct_session_files_requests[0],
            "browser_session_files_requests": sum(
                len(surface.execute_script("return [...window.__e3SessionFilesRequests];"))
                for surface in (differ, finder)
            ),
            "producers": int(product_after.get("completed", 0)) - int(product_before.get("completed", 0)),
            "coalesced": int(product_after.get("coalesced", 0)) - int(product_before.get("coalesced", 0)),
            "git_snapshots": int(work_after.get("git_snapshots", 0)) - int(work_before.get("git_snapshots", 0)),
            "payload_bytes": int(work_after.get("result_bytes", 0)) - int(work_before.get("result_bytes", 0)),
            "cache_payload_writes": events["payload_writes"],
            "metadata_writes": events["metadata_writes"],
            "event_writes": events["event_writes"],
            "close_writes": events["close_writes"],
            "renames": events["renames"],
            "unlinks": events["unlinks"],
            "temporary_view_files": temp_footprint()[0] - git_before[0],
            "temporary_view_allocated_bytes": temp_footprint()[1] - git_before[1],
            "write_bytes": io_after["write_bytes"] - io_before["write_bytes"],
            "cancelled_write_bytes": io_after["cancelled_write_bytes"] - io_before["cancelled_write_bytes"],
        }
        ceilings = {
            "direct_session_files_requests": 2,
            # Each visible surface may perform its initial read and one opaque completion read.
            # Reconnect replays the completion but must not repeat either read.
            "browser_session_files_requests": 4,
            "producers": 1,
            "coalesced": 0,
            "git_snapshots": 1,
            "payload_bytes": 256 * 1024,
            "cache_payload_writes": 4,
            "metadata_writes": 4,
            "event_writes": 4,
            "close_writes": 17,
            "renames": 8,
            "unlinks": 8,
            "temporary_view_files": 0,
            "temporary_view_allocated_bytes": 0,
            "write_bytes": 16 * 1024 * 1024,
            "cancelled_write_bytes": 16 * 1024 * 1024,
        }
        assert_e3_causal_ceilings(observed, ceilings)
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == frozen_head
        # This is a real repeated completion read, remeasured against the same frozen ceiling.
        connection = HTTPConnection("127.0.0.1", follower.port, timeout=10)
        direct_session_files_requests[0] += 1
        connection.request("GET", f"/api/session-files?session={quote(session, safe='')}&hours=24&from=HEAD&to=current&cache_only=1&cache_view={view_id}")
        control_response = connection.getresponse()
        control_payload = json.loads(control_response.read().decode("utf-8"))
        connection.close()
        assert control_response.status == HTTPStatus.OK and control_payload["state"] == "ready"
        with pytest.raises(AssertionError, match="causal ceiling exceeded"):
            assert_e3_causal_ceilings({**observed, "direct_session_files_requests": direct_session_files_requests[0]}, ceilings)
    finally:
        if finder is not None:
            finder.quit()
        if differ is not None:
            differ.quit()
        if observer is not None:
            observer.close()
        if job_client is not None:
            job_client.stop_for_scheduler()
        if follower is not None:
            stop_and_reap_daemons(follower)
        if owner is not None:
            stop_and_reap_daemons(owner)
        tempfile.tempdir = previous_tempdir
        stop_isolated_tmux_runtime(runtime)
        shutil.rmtree(fixture_root)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="requires fixture-owned server processes")
def test_session_files_browser_completion_is_bounded_to_one_opaque_cache_read(monkeypatch, tmp_path, gate_http_port):
    """A real Differ browser applies a completion with no repeated cache-read loop."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "changed.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "changed.py")
    git(repo, "commit", "-m", "seed")
    (repo / "changed.py").write_text("value = 2\n", encoding="utf-8")
    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path / "tmux", session_count=1, session_cwd=repo)
    shared_state = tmp_path / "shared" / "state"
    shared_runtime = tmp_path / "shared" / "runtime"
    shared_runtime.mkdir(parents=True)
    owner_paths = build_paths(tmp_path / "owner", state_dir=shared_state)
    follower_paths = build_paths(tmp_path / "follower", state_dir=shared_state)
    owner = follower = browser = finder = None
    try:
        owner = start_isolated_dev_server(
            "session-files-browser-owner", Path(__file__).resolve().parents[1], owner_paths, runtime,
            env_overrides={"YOLOMUX_RUNTIME_DIR": str(shared_runtime)}, port=gate_http_port.release(),
        )
        follower = start_isolated_dev_server(
            "session-files-browser-follower", Path(__file__).resolve().parents[1], follower_paths, runtime,
            env_overrides={"YOLOMUX_RUNTIME_DIR": str(shared_runtime), "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": str(owner.port)},
        )
        session = runtime.sessions[0]
        request_path = f"/api/session-files?session={quote(session, safe='')}&hours=24&from=HEAD&to=current&force=1&fresh_git=1"
        connection = HTTPConnection("127.0.0.1", owner.port, timeout=10)
        connection.request("GET", request_path)
        receipt_response = connection.getresponse()
        receipt = json.loads(receipt_response.read().decode("utf-8"))
        connection.close()
        assert receipt_response.status == HTTPStatus.ACCEPTED
        terminal_status, terminal = operation_terminal_response(owner, receipt["operation"]["status_url"], timeout=20)
        assert terminal_status == HTTPStatus.OK and terminal["state"] == "ready"
        views = [path for path in shared_state.rglob("session-files-cache/*.json") if not path.name.endswith(".manifest.json") and path.name not in {"index.json", "cache-index.json"}]
        assert len(views) == 1
        view_id = views[0].stem
        browser = new_chrome_driver(profile_dir=tmp_path / "chrome-profile")
        assert (tmp_path / "chrome-profile").is_relative_to(tmp_path)
        browser.get(f"http://127.0.0.1:{follower.port}/?sessions=__differ__,{quote(session, safe='')}&layout=left&tabs=left:__differ__")
        WebDriverWait(browser, 12).until(lambda driver: driver.execute_script("return Boolean(document.querySelector('#panel-__differ__'));"))
        WebDriverWait(browser, 12).until(lambda driver: driver.execute_script("return fileExplorerSessionFilesState?.payload?.loaded === true;"))
        WebDriverWait(browser, 12).until(lambda driver: driver.execute_script("return clientEventTransportState?.connected === true;"))
        browser_descriptor = browser.execute_script("return sessionFilesDescriptorForDestination('differ', sessionFilesRequestForDestination('differ'));" )
        assert browser_descriptor == json.loads(views[0].read_text(encoding="utf-8"))["request_descriptor"]
        finder = new_chrome_driver(profile_dir=tmp_path / "finder-profile")
        finder.get(f"http://127.0.0.1:{follower.port}/?sessions=__finder__,{quote(session, safe='')}&layout=left&tabs=left:__finder__")
        WebDriverWait(finder, 12).until(lambda driver: driver.execute_script("return Boolean(document.querySelector('#panel-__finder__'));"))
        WebDriverWait(finder, 12).until(lambda driver: driver.execute_script("return fileExplorerFinderSessionFilesState?.payload?.loaded === true;"))
        WebDriverWait(finder, 12).until(lambda driver: driver.execute_script("return clientEventTransportState?.connected === true;"))
        finder_descriptor = finder.execute_script("return sessionFilesDescriptorForDestination('finder', sessionFilesRequestForDestination('finder'));" )
        assert finder_descriptor == browser_descriptor
        for surface in (browser, finder):
            surface.execute_script(
            "window.__e3SessionFilesRequests = [];"
            "window.__e3SessionFilesApplications = 0;"
            "const originalFetch = window.fetch.bind(window);"
            "const originalApply = setSessionFilesPayloadForDestination;"
            "setSessionFilesPayloadForDestination = (...args) => {"
            " window.__e3SessionFilesApplications += 1;"
            " return originalApply(...args);"
            "};"
            "window.fetch = (...args) => {"
            " const target = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.href);"
            " if (target.pathname === '/api/session-files') window.__e3SessionFilesRequests.push(target.search);"
            " return originalFetch(...args);"
            "};"
            )
        for cache_record in (views[0], views[0].with_name(f"{view_id}.manifest.json")):
            record = json.loads(cache_record.read_text(encoding="utf-8"))
            record["stored_at"] = 0.0
            cache_record.write_text(json.dumps(record), encoding="utf-8")
        # An expired durable view after an owner restart is a recoverable state.  Do not pair the
        # expired record with a still-fresh owner-memory entry: that split state cannot happen to
        # a user and used to leave this EventSource assertion waiting for an event that was never
        # eligible to publish.
        owner.restart()
        connection = HTTPConnection("127.0.0.1", owner.port, timeout=10)
        connection.request("GET", f"/api/session-files?session={quote(session, safe='')}&hours=24&from=HEAD&to=current")
        stale_response = connection.getresponse()
        assert stale_response.status == HTTPStatus.OK
        stale_response.read()
        connection.close()
        def completion_is_published():
            manifests = list(shared_state.rglob("background-owner/client-events.json"))
            if len(manifests) != 1:
                return False
            events = json.loads(manifests[0].read_text(encoding="utf-8")).get("events", [])
            return any(
                event.get("type") == "background_refresh_done"
                and event.get("payload", {}).get("role") == "session-files"
                and event.get("payload", {}).get("cache_view_id") == view_id
                for event in events
            )
        WebDriverWait(browser, 12).until(lambda _driver: completion_is_published())
        WebDriverWait(browser, 12).until(lambda driver: driver.execute_script("return clientEventTransportState?.connected === true;"))
        expected_cache_read = f"?from=HEAD&to=current&session={quote(session, safe='')}&hours=24&cache_only=1&cache_view={view_id}"
        # This must be the follower's delivered EventSource completion, not merely a
        # persisted event that a disconnected browser never handled.
        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script("return window.__e3SessionFilesRequests.length === 1;")
        )
        WebDriverWait(finder, 12).until(
            lambda driver: driver.execute_script("return window.__e3SessionFilesRequests.length === 1;")
        )
        requests = browser.execute_script("return [...window.__e3SessionFilesRequests];")
        assert requests == [expected_cache_read]
        assert finder.execute_script("return [...window.__e3SessionFilesRequests];") == [expected_cache_read]
        assert browser.execute_script("return window.__e3SessionFilesApplications;") <= 1
        assert finder.execute_script("return window.__e3SessionFilesApplications;") <= 1
        completion = next(
            event["payload"]
            for event in json.loads(next(shared_state.rglob("background-owner/client-events.json")).read_text(encoding="utf-8")).get("events", [])
            if event.get("type") == "background_refresh_done"
            and event.get("payload", {}).get("role") == "session-files"
            and event.get("payload", {}).get("cache_view_id") == view_id
        )
        browser.execute_script("handleClientPushEventNowByType('background_refresh_done', arguments[0]);", completion)
        finder.execute_script("handleClientPushEventNowByType('background_refresh_done', arguments[0]);", completion)
        browser.execute_async_script("const done = arguments[0]; requestAnimationFrame(() => requestAnimationFrame(done));")
        assert browser.execute_script("return [...window.__e3SessionFilesRequests];") == [expected_cache_read]
        assert finder.execute_script("return [...window.__e3SessionFilesRequests];") == [expected_cache_read]
        assert browser.execute_script("return window.__e3SessionFilesApplications;") <= 1
        assert finder.execute_script("return window.__e3SessionFilesApplications;") <= 1
        assert browser.execute_script("return document.querySelector('#panel-__differ__')?.dataset.fileExplorerMode === 'diff';")
        assert finder.execute_script("return fileExplorerFinderSessionFilesState?.payload?.loaded === true;")
        for surface, state_expression in (
            (browser, "fileExplorerSessionFilesState"),
            (finder, "fileExplorerFinderSessionFilesState"),
        ):
            before_signature = surface.execute_script(f"return {state_expression}.signature;")
            replaced = surface.execute_script(
                "const prior = clientEventTransportState.source;"
                "window.__e3ReplayedCompletions = 0;"
                "prior.onerror();"
                "closeClientEventStream();"
                "syncClientEventDemand({immediate: true});"
                "const replacement = clientEventTransportState.source;"
                "return replacement !== prior;"
            )
            assert replaced
            WebDriverWait(surface, 12).until(lambda driver: driver.execute_script("return clientEventTransportState?.connected === true;"))
            assert surface.execute_script("return [...window.__e3SessionFilesRequests];") == [expected_cache_read]
            assert surface.execute_script(f"return {state_expression}.signature;") == before_signature
            assert surface.execute_script("return window.__e3SessionFilesApplications;") <= 1
    finally:
        if finder is not None:
            finder.quit()
        if browser is not None:
            browser.quit()
        if follower is not None:
            stop_and_reap_daemons(follower)
        if owner is not None:
            stop_and_reap_daemons(owner)
        stop_isolated_tmux_runtime(runtime)


def test_session_files_route_returns_operation_receipt_then_publishes_and_replays_ready(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(); init_repo(repo)
    pane = PaneInfo(session="5", window="0", pane="0", pane_id="%1", target="5:0.0", current_path=str(repo), command="zsh", active=True, window_active=True, title="", pid=11)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, [])); monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache"); monkeypatch.setattr(app_module, "SESSION_FILES_BATCHD_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations" / "session-files.json", raising=False)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    release_result, terminal_published, changed_terminal_published = threading_module.Event(), threading_module.Event(), threading_module.Event()
    published, submissions, submission_requesters, result_calls, durable_writes, result_path = [], [], [], [], [], ["done.py"]
    class ControlledBatchClient:
        def start_for_scheduler(self):
            return None
        def stop_for_scheduler(self):
            return None
        def submit(self, *_args, **kwargs):
            submissions.append(kwargs)
            payload = _args[1] if len(_args) > 1 and isinstance(_args[1], dict) else {}
            source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
            submission_requesters.append(str(source.get("requester") or ""))
            count = len(submissions)
            job_id = "job-session-files-ready" if count <= 2 or kwargs["fresh_only"] is False else f"job-session-files-fresh-{count}"
            return {"ok": True, "job": {"job_id": job_id, "generation": 7, "status": "queued"}}
        def product(self, *_args, **_kwargs):
            return {"ok": True, "state": "pending", "generation": 7}, b""
        def result(self, job_id, *, timeout):
            assert 0 < timeout <= app_module.BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS; assert release_result.wait(2.0), "test did not release the accepted job"
            result_calls.append(job_id)
            path = "done.py" if job_id == "job-session-files-ready" else result_path[0]
            payload = {"session": "5", "loaded": True, "files": [{"path": path}], "repos": [], "errors": []}
            return {"ok": True, "job": {"job_id": job_id, "status": "completed", "result": {"payload": payload, "status": int(HTTPStatus.OK), "repository_identities": {str(repo.resolve()): ["producer-derived"]}}}}
    webapp = TmuxWebtermApp(["5"]); webapp.job_client = ControlledBatchClient(); webapp.refresh_sessions = lambda: []
    def producer_derived_git_identity(*_args):
        assert release_result.is_set(), "accepted HTTP request performed unwatched Git identity work before 202"
        return ("producer-derived",), "test-derived"

    monkeypatch.setattr(webapp, "shared_git_identity", producer_derived_git_identity)
    original_write = webapp.write_session_files_disk_cache_unlocked
    def capture_durable_write(path, signature, payload, status, source_generation="", request_descriptor=""):
        durable_writes.append((path, source_generation))
        return original_write(path, signature, payload, status, source_generation, request_descriptor)

    webapp.write_session_files_disk_cache_unlocked = capture_durable_write
    webapp.request_session_files_disk_cache_prune = lambda *_args, **_kwargs: None
    original_publish = webapp.publish_client_event
    def capture_publish(event_type, payload, **kwargs):
        event = original_publish(event_type, payload, **kwargs)
        if event_type == "operation_terminal":
            published.append(payload)
            if len(published) == 2: terminal_published.set()
            elif len(published) == 3: changed_terminal_published.set()
        return event

    webapp.publish_client_event = capture_publish; webapp.start_client_event_watcher = lambda: None
    webapp.wake_client_event_watcher = lambda: None; webapp.stop_client_event_watcher_if_idle = lambda: True
    server = thread = None
    try:
        server, thread = start_browser_server(monkeypatch, tmp_path, webapp, auth_bypass=True)
        refs = {"/repo/z": {"to": " current ", "from": " HEAD~2 "}}
        canonical_refs = {"/repo/z": {"from": "HEAD~2", "to": "current"}}  # The HTTP boundary trims these before canonical publication.
        encoded_refs = quote(json.dumps(refs, separators=(",", ":")), safe="")
        request_path = f"/api/session-files?session=5&hours=7.5&from=HEAD~3&to=current&refs={encoded_refs}"
        def request_session_files(path=request_path, expected_status=HTTPStatus.ACCEPTED):
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("GET", path); response = connection.getresponse()
            receipt = json.loads(response.read().decode("utf-8")); connection.close()
            assert response.status == expected_status
            return receipt
        receipt, duplicate_receipt = request_session_files(), request_session_files()
        assert receipt["state"] == "queued"; assert receipt["request"]["id"].startswith("r-")
        operation = receipt["operation"]
        assert operation["id"].startswith("op-"); assert operation["status_url"] == f"/api/operations/{operation['id']}"
        assert operation["events_url"] == f"/api/client-events?operation_id={operation['id']}"; assert operation["cursor"]["seq"] == 0
        assert operation["context"] == {"session": "5", "from_ref": "HEAD~3", "to_ref": "current", "hours": 7.5, "repo_refs": {"/repo/z": {"from": "HEAD~2", "to": "current"}}}
        assert operation["progress"] == {"phase": "waiting_for_product", "producer": "batchd", "producer_state": "queued"}
        assert duplicate_receipt["operation"]["id"] != operation["id"]; assert len(submissions) == 2
        assert submissions[0]["coalesce_key"] == submissions[1]["coalesce_key"]; assert submissions[0]["generation"] == submissions[1]["generation"]
        assert submissions[0]["fresh_only"] is submissions[1]["fresh_only"] is False
        release_result.set(); assert terminal_published.wait(2.0), "accepted operations did not publish terminal results"
        terminals = {item["operation"]["id"]: item for item in published}
        assert set(terminals) == {operation["id"], duplicate_receipt["operation"]["id"]}
        terminal = terminals[operation["id"]]
        assert terminal["operation"]["id"] == operation["id"]; assert terminal["operation"]["cursor"]["seq"] == 1
        assert terminal["result"]["state"] == "ready"; assert terminal["result"]["data"]["files"] == [{"path": "done.py"}]
        assert [item["state"] for item in terminal["result"]["producer"]["chain"]] == ["completed"]
        assert_terminal_transition(
            contract_id="session-files-http-operation-completion",
            pending_observed=receipt["state"] == "queued",
            terminal_observed=terminal["result"]["state"] == "ready",
            evidence={"operation": operation["id"], "terminal": terminal["operation"]["cursor"]},
        )
        assert result_calls == ["job-session-files-ready"]
        canonical_key = webapp.session_files_cache_key("payload", {"5": info}, "5", 7.5, "HEAD~3", "current", canonical_refs)
        path, cache_view = webapp.session_files_disk_cache_path(canonical_key)
        canonical_source_generation = webapp.session_files_source_generation(canonical_key)
        assert durable_writes == [(path, canonical_source_generation)]
        record = json.loads(path.read_text(encoding="utf-8")); assert record["source_generation"] == canonical_source_generation
        assert record["request_descriptor"] == webapp.session_files_request_descriptor("5", 7.5, "HEAD~3", "current", canonical_refs)
        assert len(record["request_descriptor"]) == 64
        assert str(repo) not in record["request_descriptor"]
        # This route-level test owns the request/operation/product chain. Browser application
        # belongs to the browser fixture below; keeping a fake cross-surface ledger here hid
        # which boundary had actually been measured.
        assert len(result_calls) == 1
        refresh_starts, original_start_refresh = [], webapp.start_session_files_cache_refresh
        webapp.start_session_files_cache_refresh = lambda cache_key, target, *args: (refresh_starts.append(cache_key) or original_start_refresh(cache_key, target, *args))
        snapshot, snapshot_status = webapp.session_files_payload_for_infos("5", {"5": info}, 7.5, "HEAD~3", "current", canonical_refs, requester="descriptor-snapshot")
        assert (snapshot_status, snapshot["files"], snapshot["cache"]["stale"]) == (HTTPStatus.OK, [{"path": "done.py"}], False)
        assert refresh_starts == []; assert webapp.session_files_service.wait_for_idle(0.1)
        second_ordinary = request_session_files(expected_status=HTTPStatus.OK)
        assert second_ordinary["request"]["id"] not in {receipt["request"]["id"], duplicate_receipt["request"]["id"]}
        assert second_ordinary["data"]["files"] == [{"path": "done.py"}]
        assert len(submissions) == 2
        assert result_calls == ["job-session-files-ready"]
        forced_without_fresh_git = request_session_files(f"{request_path}&force=1", expected_status=HTTPStatus.OK)
        assert forced_without_fresh_git["data"]["files"] == [{"path": "done.py"}]
        assert len(submissions) == 2
        assert result_calls == ["job-session-files-ready"]
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", f"{request_path}&cache_only=1&cache_view={cache_view}"); cache_only_response = connection.getresponse()
        cache_only_payload = json.loads(cache_only_response.read().decode("utf-8")); connection.close()
        assert cache_only_response.status == HTTPStatus.OK
        assert cache_only_payload["data"]["files"] == [{"path": "done.py"}]
        assert len(submissions) == 2, f"a follower cache-only revalidation must not submit another producer: {submission_requesters}"
        assert webapp.read_session_files_cache_view(cache_view, "5", 7.5, "other", "current", None) is None
        assert webapp.batchd_operation_service.wait_for_idle(5)
        submissions_before_mismatch = len(submissions)
        received_http_requests = []
        cache_view_reads = []
        original_http_payload = webapp.session_files_http_payload
        original_cache_view_read = webapp._session_files_coordinator.read_session_files_cache_view
        def capture_http_payload(*args, **kwargs):
            received_http_requests.append((args, kwargs))
            result = original_http_payload(*args, **kwargs)
            received_http_requests[-1] = (*received_http_requests[-1], result)
            return result
        def capture_cache_view_read(*args, **kwargs):
            result = original_cache_view_read(*args, **kwargs)
            cache_view_reads.append((args, kwargs, result))
            return result
        webapp.session_files_http_payload = capture_http_payload
        webapp._session_files_coordinator.read_session_files_cache_view = capture_cache_view_read
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", f"/api/session-files?session=5&hours=7.5&from=other&to=current&cache_only=1&cache_view={cache_view}"); mismatch_response = connection.getresponse()
        mismatch_payload = json.loads(mismatch_response.read().decode("utf-8")); connection.close()
        assert received_http_requests == [
            (("5", 7.5), {"from_ref": "other", "to_ref": "current", "repo_refs": None, "force": False, "cache_only": True, "cache_view": cache_view}, ({"session": "5", "status": "pending", "retry_after_seconds": 1, "reason": "the requested session-files cache view is not ready"}, HTTPStatus.ACCEPTED))
        ]
        assert cache_view_reads == [( (webapp, cache_view, "5", 7.5, "other", "current", None), {}, None)]
        assert mismatch_response.status == HTTPStatus.ACCEPTED
        assert mismatch_payload["state"] == "queued"
        assert mismatch_payload["status"] == "pending"
        assert mismatch_payload["retry_after_seconds"] == 1
        assert mismatch_payload["session"] == "5"
        assert mismatch_payload["ok"] is True and mismatch_payload["terminal"] is False
        assert mismatch_payload["request"]["id"].startswith("r-")
        assert "api-session-files" not in submission_requesters[submissions_before_mismatch:], "a mismatched cache-only request must not submit session-files work"
        submissions_before_changed = len(submissions)
        result_path[0] = "changed.py"; changed_receipt = request_session_files(f"{request_path}&fresh_git=1")
        assert changed_receipt["state"] == "queued"
        assert changed_terminal_published.wait(2.0), "changed operation did not publish a terminal result"
        changed_terminal = published[-1]
        assert changed_terminal["operation"]["id"] == changed_receipt["operation"]["id"]; assert changed_terminal["result"]["data"]["files"] == [{"path": "changed.py"}]
        changed_submissions = submissions[submissions_before_changed:]
        fresh_submissions = [submission for submission in changed_submissions if submission["fresh_only"] is True]
        assert len(fresh_submissions) == 1
        assert fresh_submissions[0]["coalesce_key"] == submissions[0]["coalesce_key"]
        assert [item["job_id"] for item in changed_terminal["result"]["producer"]["chain"]][0].startswith("job-session-files-fresh-")
        assert [item["state"] for item in changed_terminal["result"]["producer"]["chain"]] == ["completed"]
        submissions_before_forced_fresh = len(submissions)
        result_path[0] = "forced-fresh.py"
        forced_fresh_receipt = request_session_files(f"{request_path}&force=1&fresh_git=1")
        assert forced_fresh_receipt["state"] == "queued"
        assert webapp.batchd_operation_service.wait_for_idle(5)
        forced_fresh_terminal = published[-1]
        assert forced_fresh_terminal["operation"]["id"] == forced_fresh_receipt["operation"]["id"]
        assert forced_fresh_terminal["result"]["data"]["files"] == [{"path": "forced-fresh.py"}]
        assert len(submissions) == submissions_before_forced_fresh + 1
        assert submissions[-1]["fresh_only"] is True
        watch_record = webapp.client_watch_service.event_watcher_record
        watch_record.filesystem_healthy = True
        watch_record.filesystem_roots = (str(repo.resolve()),)
        result_path[0] = "watched.py"
        watched_receipt = request_session_files(f"{request_path}&fresh_git=1")
        assert webapp.batchd_operation_service.wait_for_idle(5)
        watched_terminal = published[-1]
        assert watched_terminal["operation"]["id"] == watched_receipt["operation"]["id"]
        assert watched_terminal["result"]["data"]["files"] == [{"path": "watched.py"}]
        assert submissions[-1]["fresh_only"] is True
        assert [item["job_id"] for item in watched_terminal["result"]["producer"]["chain"]][0].startswith("job-session-files-fresh-")
        assert [item["state"] for item in watched_terminal["result"]["producer"]["chain"]] == ["completed"]
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", operation["status_url"]); status_response = connection.getresponse()
        replayed_status = json.loads(status_response.read().decode("utf-8")); connection.close()
        assert status_response.status == HTTPStatus.OK; assert replayed_status == terminal["result"]
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", operation["events_url"]); replay_response = connection.getresponse()
        assert replay_response.status == HTTPStatus.OK
        assert replay_response.readline().decode("utf-8") == "event: ready\n"
        replay_response.readline(); replay_response.readline()
        assert replay_response.readline().decode("utf-8") == "event: operation_terminal\n"
        replay_payload = json.loads(replay_response.readline().decode("utf-8").removeprefix("data: ")); connection.close()
        assert replay_payload["payload"] == terminal
        operation_state_path = tmp_path / "operations" / "session-files.json"
        assert operation_state_path.is_file()
        persisted = QueuedDeliveryLedger(state_path=operation_state_path).operation_status(operation["id"])
        assert persisted == (terminal["result"], HTTPStatus.OK)
        def unexpected_cache_only_work(*_args, **_kwargs):
            raise AssertionError("a malformed cache view must not discover sessions or inspect Git")
        webapp.refresh_sessions = unexpected_cache_only_work
        webapp.shared_git_identity = unexpected_cache_only_work
        submission_count = len(submissions)
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/api/session-files?session=5&cache_only=1&cache_view=not-a-cache-view"); malformed_response = connection.getresponse()
        malformed_payload = json.loads(malformed_response.read().decode("utf-8")); connection.close()
        assert malformed_response.status == HTTPStatus.ACCEPTED
        assert malformed_payload["state"] == "queued" and malformed_payload["status"] == "pending"
        assert len(submissions) == submission_count, "a malformed cache view must not submit batchd work"
    finally:
        release_result.set()
        if server is not None: stop_browser_server(server, thread)
        webapp.control_server.stop()


def test_session_files_completion_before_receipt_persistence_terminalizes_after_registration(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json", raising=False)
    webapp = TmuxWebtermApp([])
    webapp.submit_session_files_job = lambda *_args, **_kwargs: (
        {"ok": True, "job": {"job_id": "job-completed-before-accept", "status": "queued", "generation": 1}},
        "completed-before-accept",
        1,
    )
    accept_entered = threading_module.Event()
    allow_accept = threading_module.Event()
    product_completed = threading_module.Event()
    result = {}

    def complete(flight, *_args):
        flight.future.set_result(({"files": [{"path": "ready.py"}], "repos": [], "errors": []}, HTTPStatus.OK, None))
        product_completed.set()
        flight.wait_for_owner()
        webapp.batchd_operation_service.release_flight(flight)

    original_accept = webapp.queued_delivery_ledger.accept_operation

    def accept_after_product(**kwargs):
        accept_entered.set()
        assert allow_accept.wait(timeout=5)
        return original_accept(**kwargs)

    webapp.complete_session_files_operation = complete
    webapp.queued_delivery_ledger.accept_operation = accept_after_product
    starter = threading_module.Thread(
        target=lambda: result.setdefault(
            "value",
            webapp.start_session_files_operation(
                None,
                {},
                24.0,
                None,
                None,
                None,
                ("request", ()),
                priority="freshness",
                requester="test",
            ),
        ),
        name="session-files-delayed-accept",
    )
    try:
        starter.start()
        assert accept_entered.wait(timeout=5)
        assert product_completed.wait(timeout=5)
        assert starter.is_alive()
        allow_accept.set()
        starter.join(timeout=5)
        assert not starter.is_alive()

        receipt, status = result["value"]
        operation_id = receipt["operation"]["id"]
        terminal, terminal_status = webapp.queued_delivery_ledger.operation_status(operation_id)
        assert status == HTTPStatus.ACCEPTED
        assert terminal_status == HTTPStatus.OK
        assert terminal["state"] == "ready"
        assert terminal["data"]["files"] == [{"path": "ready.py"}]
        terminal_event = webapp.queued_delivery_ledger.operation_replay_event(operation_id)
        assert terminal_event is not None
        assert receipt["request"]["id"]
        assert terminal_event["operation"]["cursor"]["seq"] == 1
        assert webapp.queued_delivery_ledger.open_operations() == []
        assert webapp.batchd_operation_service.wait_for_idle(5)
        assert webapp.batchd_operation_service.flights == {}
    finally:
        allow_accept.set()
        starter.join(timeout=5)
        webapp.batchd_operation_service.wait_for_idle(5)
        webapp.control_server.stop()


def test_session_files_operation_completion_separates_replacement_intent(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json", raising=False)
    webapp = TmuxWebtermApp([])
    started = []
    first_started = threading_module.Event()
    both_started = threading_module.Event()
    release = threading_module.Event()

    def submit(*_args, **_kwargs):
        return {"ok": True, "job": {"job_id": "job-shared", "status": "queued", "generation": 1}}, "same-coalesce", 1

    def complete(flight, _job_id, _session, _infos, _hours, _from_ref, _to_ref, _repo_refs, _cache_key, _deadline_at, replace, _priority, _requester):
        started.append(replace)
        first_started.set()
        if len(started) == 2:
            both_started.set()
        assert release.wait(timeout=5)
        flight.future.set_result(({"files": [{"path": f"replace-{replace}.py"}], "repos": [], "errors": []}, HTTPStatus.OK, None))
        flight.wait_for_owner()
        webapp.batchd_operation_service.release_flight(flight)

    webapp.submit_session_files_job, webapp.complete_session_files_operation = submit, complete
    try:
        first, first_status = webapp.start_session_files_operation(None, {}, 24.0, None, None, None, ("request-a", ()), priority="freshness", requester="test")
        assert first_started.wait(timeout=1)
        second, second_status = webapp.start_session_files_operation(None, {}, 24.0, None, None, None, ("request-b", ()), priority="freshness", requester="test", replace=True)
        assert (first_status, second_status) == (HTTPStatus.ACCEPTED, HTTPStatus.ACCEPTED)
        assert both_started.wait(timeout=1), started
        release.set(); assert webapp.batchd_operation_service.wait_for_idle(5)
        first_result, _ = webapp.queued_delivery_ledger.operation_status(first["operation"]["id"])
        second_result, _ = webapp.queued_delivery_ledger.operation_status(second["operation"]["id"])
        assert first_result["data"]["files"] == [{"path": "replace-False.py"}]
        assert second_result["data"]["files"] == [{"path": "replace-True.py"}]
    finally:
        release.set()
        webapp.batchd_operation_service.wait_for_idle(5)
        webapp.control_server.stop()


def test_forced_synchronous_session_files_replaces_a_fresh_cache(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = TmuxWebtermApp([])
    old_payload = {"files": [{"path": "old.py"}], "repos": [], "errors": []}
    new_payload = {"files": [{"path": "new.py"}], "repos": [], "errors": []}
    cache_key = webapp.session_files_cache_key("payload", {}, None, 24.0, None, None, None)
    calls = []

    def forced_batchd(*_args, **kwargs):
        calls.append(kwargs["replace"])
        return new_payload, HTTPStatus.OK

    try:
        webapp.compute_session_files_cache_entry(cache_key, lambda: (old_payload, HTTPStatus.OK))
        webapp.compute_session_files_payload_via_batchd = forced_batchd

        payload, status = webapp.session_files_payload_for_infos(
            None,
            {},
                24.0,
                force=True,
                fresh_git=True,
                accepted_operation=False,
        )

        assert status == HTTPStatus.OK
        assert payload["files"] == new_payload["files"]
        assert calls == [True]
    finally:
        webapp.control_server.stop()


def test_session_files_post_accept_failure_terminalizes_receipt_and_releases_owner(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json", raising=False)
    webapp = TmuxWebtermApp([])
    webapp.submit_session_files_job = lambda *_args, **_kwargs: (
        {"ok": True, "job": {"job_id": "job-invalid-receipt", "status": "queued", "generation": 1}},
        "invalid-receipt",
        1,
    )

    def complete(flight, *_args):
        flight.future.set_result(({"files": [], "repos": [], "errors": []}, HTTPStatus.OK, None))
        flight.wait_for_owner()
        webapp.batchd_operation_service.release_flight(flight)

    webapp.complete_session_files_operation = complete
    original_accept = webapp.queued_delivery_ledger.accept_operation

    def accept_nonqueued(**kwargs):
        receipt = original_accept(**kwargs)
        receipt["state"] = "invalid"
        return receipt

    webapp.queued_delivery_ledger.accept_operation = accept_nonqueued
    try:
        result, status = webapp.start_session_files_operation(
            None, {}, 24.0, None, None, None, ("request", ()), priority="freshness", requester="test",
        )
        operation_id = result["error"]["details"]["operation_id"]
        assert status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert result["state"] == "failed"
        assert webapp.queued_delivery_ledger.operation_status(operation_id) == (result, status)
        assert webapp.queued_delivery_ledger.open_operations() == []
        assert webapp.batchd_operation_service.wait_for_idle(5)
        assert webapp.batchd_operation_service.flights == {}
    finally:
        webapp.batchd_operation_service.wait_for_idle(5)
        webapp.control_server.stop()


def test_session_files_producer_journal_failure_terminalizes_and_releases_flight(no_control_socket, monkeypatch, tmp_path):
    state_path = tmp_path / "operations.json"
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", state_path, raising=False)
    webapp = TmuxWebtermApp([])
    product_waiting = threading_module.Event()
    release_product = threading_module.Event()
    webapp.submit_session_files_job = lambda *_args, **_kwargs: (
        {"ok": True, "job": {"job_id": "job-journal-failure", "status": "queued", "generation": 1}},
        "journal-failure",
        1,
    )

    def wait_for_product(*_args):
        product_waiting.set()
        assert release_product.wait(timeout=5)
        return {"files": [{"path": "done.py"}], "repos": [], "errors": []}, HTTPStatus.OK

    webapp.wait_for_session_files_operation_job = wait_for_product
    try:
        receipt, status = webapp.start_session_files_operation(
            None,
            {},
            24.0,
            None,
            None,
            None,
            ("request", ()),
            priority="freshness",
            requester="test",
        )
        operation_id = receipt["operation"]["id"]
        assert status == HTTPStatus.ACCEPTED
        assert product_waiting.wait(timeout=1)
        monkeypatch.setattr(
            webapp.queued_delivery_ledger,
            "update_operation_producers",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )

        release_product.set()
        assert webapp.batchd_operation_service.wait_for_idle(5)

        result, terminal_status = QueuedDeliveryLedger(state_path=state_path).operation_status(operation_id)
        assert terminal_status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert result["state"] == "failed"
        assert result["error"]["code"] == "producer_failed"
        assert result["error"]["stack"][-1]["exception"] == {
            "type": "OSError",
            "message": "disk full",
        }
        assert webapp.queued_delivery_ledger.open_operations() == []
        assert webapp.batchd_operation_service.flights == {}
    finally:
        release_product.set()
        webapp.batchd_operation_service.wait_for_idle(5)
        webapp.control_server.stop()


@pytest.mark.parametrize("change", ["watcher", "policy"])
def test_session_files_operation_publishes_under_immutable_producer_identity(change, no_control_socket, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(); init_repo(repo)
    pane = PaneInfo(session="s1", window="0", pane="0", pane_id="%1", target="s1:0.0", current_path=str(repo), command="zsh", active=True, window_active=True, title="", pid=1)
    infos = {"s1": SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])}
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache"); monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json", raising=False)
    webapp = TmuxWebtermApp([])
    settings = {"index_exclude_dir_names": [".git"], "index_exclude_paths": []}
    webapp.settings_payload = lambda: {"settings": {"file_explorer": settings}}
    watch_record = webapp.client_watch_service.event_watcher_record
    watch_record.filesystem_healthy, watch_record.filesystem_roots = True, (str(tmp_path.resolve()),)
    producer_started, release = threading_module.Event(), threading_module.Event()
    old_payload = {"files": [{"path": "old.py"}], "repos": [], "errors": []}
    webapp.submit_session_files_job = lambda *_args, **_kwargs: ({"ok": True, "job": {"job_id": "job-old", "status": "queued", "generation": 1}}, "coalesce-old", 1)
    def wait_for_product(*_args):
        producer_started.set(); assert release.wait(timeout=5)
        return old_payload, HTTPStatus.OK

    webapp.wait_for_session_files_operation_job = wait_for_product
    original_key = webapp.session_files_cache_key("payload", infos, "s1", 24.0, None, None, None)
    try:
        _receipt, status = webapp.start_session_files_operation("s1", infos, 24.0, None, None, None, original_key, priority="freshness", requester="test")
        assert status == HTTPStatus.ACCEPTED; assert producer_started.wait(timeout=5)
        if change == "watcher":
            webapp.mark_repo_state_dirty([repo / "changed.py"])
        else:
            settings["index_exclude_dir_names"].append("vendorcache")
        current_key = webapp.session_files_cache_key("payload", infos, "s1", 24.0, None, None, None)
        assert current_key != original_key
        release.set(); assert webapp.batchd_operation_service.wait_for_idle(5)
        assert original_key in webapp.session_files_service.cache; assert current_key not in webapp.session_files_service.cache
    finally:
        release.set(); webapp.batchd_operation_service.wait_for_idle(5); webapp.control_server.stop()


@pytest.mark.parametrize("failure_stage", ["result", "deadline"])
def test_session_files_failure_attributes_one_terminal_producer(failure_stage, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    pane = PaneInfo(session="5", window="0", pane="0", pane_id="%1", target="5:0.0", current_path=str(repo), command="zsh", active=True, window_active=True, title="", pid=11)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module, "SESSION_FILES_BATCHD_WAIT_SECONDS", 0.0)
    if failure_stage == "deadline":
        monkeypatch.setattr(app_module, "SESSION_FILES_BATCHD_JOB_DEADLINE_MS", 25)
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations" / "session-files.json", raising=False)
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    terminal_published = threading_module.Event()
    published, submissions = [], []
    root_cause = {"exception": {"type": "FileNotFoundError", "message": "service socket is absent"}, "frames": [{"file": "yolomux_lib/local_services/rpc.py", "line": 272, "function": "request"}]}

    class FailingBatchClient:
        def start_for_scheduler(self):
            return None

        def stop_for_scheduler(self):
            return None

        def submit(self, *_args, **kwargs):
            submissions.append(kwargs)
            return {"ok": True, "job": {"job_id": "job-session-files-failed", "generation": 9, "status": "queued"}}

        def product(self, *_args, **_kwargs):
            return {"ok": True, "state": "pending", "generation": 9}, b""

        def result(self, job_id, *, timeout):
            assert 0 < timeout <= app_module.BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS
            assert job_id == "job-session-files-failed"
            if failure_stage == "deadline":
                return {"ok": True, "job": {"job_id": job_id, "status": "queued"}}
            return {
                "ok": False,
                "error": "service socket is absent",
                "exception_type": "FileNotFoundError",
                "_transport_error": "absent",
                "cause": root_cause,
            }

    webapp = TmuxWebtermApp(["5"])
    webapp.job_client = FailingBatchClient()
    webapp.refresh_sessions = lambda: []
    webapp.shared_git_identity = lambda *_args: (("canonical",), "canonical")
    original_publish = webapp.publish_client_event

    def capture_publish(event_type, payload, **kwargs):
        event = original_publish(event_type, payload, **kwargs)
        if event_type == "operation_terminal":
            published.append(payload)
            terminal_published.set()
        return event

    webapp.publish_client_event = capture_publish
    server = thread = None
    try:
        server, thread = start_browser_server(monkeypatch, tmp_path, webapp, auth_bypass=True)
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/api/session-files?session=5&force=1")
        response = connection.getresponse()
        receipt = json.loads(response.read().decode("utf-8"))
        connection.close()
        assert response.status == HTTPStatus.ACCEPTED
        assert terminal_published.wait(2.0), "accepted failure did not publish a terminal result"

        terminal = published[-1]
        result = terminal["result"]
        assert result["state"] == "failed"
        assert result["request"] == receipt["request"]
        expected_status = HTTPStatus.GATEWAY_TIMEOUT if failure_stage == "deadline" else HTTPStatus.SERVICE_UNAVAILABLE
        expected_code = "deadline_expired" if failure_stage == "deadline" else "service_unavailable"
        assert terminal["status"] == expected_status
        assert result["error"]["code"] == expected_code
        expected_operation = "batchd.result"
        assert result["error"]["stack"][-1]["operation"] == expected_operation
        if failure_stage == "result":
            assert result["error"]["stack"][-1]["exception"] == root_cause["exception"]
            assert result["error"]["stack"][-1]["frames"] == root_cause["frames"]
        assert len(submissions) == 1
        assert [item["job_id"] for item in result["producer"]["chain"]] == ["job-session-files-failed"]
        assert result["producer"]["chain"][0]["state"] == "failed"
        assert result["producer"]["chain"][0]["code"] == expected_code
        operation_id = receipt["operation"]["id"]
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", f"/api/operations/{operation_id}")
        status_response = connection.getresponse()
        replayed = json.loads(status_response.read().decode("utf-8"))
        connection.close()
        assert status_response.status == expected_status
        assert replayed == result
        assert QueuedDeliveryLedger(state_path=tmp_path / "operations" / "session-files.json").operation_status(operation_id) == (result, expected_status)
        retired = retire_expected_session_files_failure_logs(
            server,
            request_id=result["request"]["id"],
            operation_id=operation_id,
            stack_operation=expected_operation,
            expect_transport=False,
            expected_code=expected_code,
        )
        assert len(retired) == 2
        webapp.demote_background_owner()
    finally:
        if server is not None:
            stop_browser_server(server, thread)
        webapp.control_server.stop()


def test_session_files_recovered_receipt_replays_producer_abandoned(monkeypatch, tmp_path):
    operation_state_path = tmp_path / "operations" / "session-files.json"
    ledger = QueuedDeliveryLedger(state_path=operation_state_path)
    initial_producer = {"service": "batchd", "chain": [{"stage": "requested", "job_id": "job-abandoned", "state": "completed"}]}
    receipt = ledger.accept_operation(
        request_id="r-recovered-session-files",
        route="GET /api/session-files",
        deadline_at=time.time() + 30,
        progress={"phase": "waiting_for_product", "producer": "batchd", "producer_state": "queued"},
        producer=initial_producer,
        kind="session_files",
        context={"session": "5"},
    )
    operation_id = receipt["operation"]["id"]
    canonical_producer = {"service": "batchd", "chain": [*initial_producer["chain"], {"stage": "canonical", "job_id": "job-canonical-abandoned", "state": "running"}]}
    assert ledger.update_operation_producer(operation_id, canonical_producer)
    queued, queued_status = QueuedDeliveryLedger(state_path=operation_state_path).operation_status(operation_id)
    assert queued_status == HTTPStatus.ACCEPTED
    assert queued["operation"]["progress"]["producer_stage"] == "canonical"
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", operation_state_path)
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)

    webapp = TmuxWebtermApp([])
    try:
        result, status = webapp.operation_status_payload(operation_id)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result["state"] == "failed"
    assert result["request"] == {"id": "r-recovered-session-files"}
    assert result["error"]["code"] == "producer_abandoned"
    assert result["error"]["stack"][-1]["code"] == "producer_abandoned"
    assert result["producer"] == canonical_producer
    assert QueuedDeliveryLedger(state_path=operation_state_path).operation_status(operation_id) == (
        result,
        HTTPStatus.SERVICE_UNAVAILABLE,
    )


def test_session_files_public_deleted_root_cache_keeps_batchd_serving(
    isolated_real_batchd_runtime, monkeypatch, tmp_path,
):
    """A retired worktree cached by transcript scanning must not crash batchd or poison later demands."""
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
    assert cache_key is not None and session_files.transcript_scan_store_path(cache_key).exists()
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    retired_root.rmdir()
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[agent("claude", transcript, retired_root, session="5")])
    monkeypatch.setattr(app_module, "discover_sessions", lambda _sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    webapp = TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda: []
    assert batchd_module.RUNTIME_DIR == isolated_real_batchd_runtime
    assert webapp.job_client.socket_path == batchd_module.default_socket_path()
    server = thread = None
    try:
        assert webapp.job_client.start_for_scheduler()
        server, thread = start_browser_server(monkeypatch, tmp_path, webapp, auth_bypass=True)
        for hours in (1, 2, 3):
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=15)
            connection.request("GET", f"/api/session-files?session=5&force=1&hours={hours}")
            response = connection.getresponse()
            receipt = json.loads(response.read().decode("utf-8"))
            connection.close()
            assert response.status == HTTPStatus.ACCEPTED
            terminal_status, terminal = operation_terminal_response(server, receipt["operation"]["status_url"])
            assert terminal_status == HTTPStatus.OK
            data = terminal["data"]
            # A retired worktree is Differ DATA, not a diagnostic. Assert the positive form: any
            # future change routing this back to a warning, an error or a log record goes red here,
            # on the real receipt -> operation -> terminal path rather than on a direct call.
            assert data["warnings"] == [], data["warnings"]
            assert data["errors"] == [], data["errors"]
            assert data["files"] == [], data["files"]
            assert len(data["repos"]) == 1, data["repos"]
            gone = data["repos"][0]
            assert gone["repo"] == str(retired_root)
            assert gone["missing"] is True
            assert gone["touched_count"] == 1
            assert gone["from_ref"] == ""
            assert gone["to_ref"] == ""
            assert gone["error"] == ""
        pid = int(webapp.job_client.registry._read_record()["pid"])
        assert process_state(pid) != "Z"
        assert webapp.job_client.socket_path.exists()
        assert webapp.job_client.registry.healthy()
        assert webapp.job_client.runtime_status()["product_counters"]["session_files_view"]["completed"] >= 3
    finally:
        if server is not None:
            stop_browser_server(server, thread)
        process = webapp.job_client.registry.process
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        webapp.control_server.stop()


def test_shared_git_snapshot_reuses_one_worktree_build_and_invalidates_every_state_input(no_control_socket, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "one.py").write_text("one = 1\n", encoding="utf-8")
    (repo / "two.py").write_text("two = 1\n", encoding="utf-8")
    git(repo, "add", "one.py", "two.py")
    git(repo, "commit", "-m", "base")
    (repo / "one.py").write_text("one = 2\n", encoding="utf-8")
    (repo / "two.py").write_text("two = 2\n", encoding="utf-8")

    transcript_one = tmp_path / "one.jsonl"
    transcript_two = tmp_path / "two.jsonl"
    transcript_one.write_text('{"msg":"*** Begin Patch\\n*** Update File: one.py\\n"}\n', encoding="utf-8")
    transcript_two.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "two.py"}}]}}) + "\n",
        encoding="utf-8",
    )
    info_one = SessionInfo("one", [], None, [agent("codex", transcript_one, repo, session="one")])
    info_two = SessionInfo("two", [], None, [agent("claude", transcript_two, repo, session="two")])

    real_build = session_files.build_git_snapshot
    builds = []

    def counted_build(path, from_ref=None, to_ref=None):
        builds.append((str(path), from_ref, to_ref))
        return real_build(path, from_ref, to_ref)

    monkeypatch.setattr(session_files, "build_git_snapshot", counted_build)
    webapp = TmuxWebtermApp(["one", "two"])
    try:
        payload_one = webapp.compute_session_files_payload_for_info(info_one, 24.0, None, None, None)
        payload_two = webapp.compute_session_files_payload_for_info(info_two, 24.0, None, None, None)
        assert len(builds) == 1
        one_files = {item["path"]: item for item in payload_one["files"]}
        two_files = {item["path"]: item for item in payload_two["files"]}
        assert one_files["one.py"]["agents"] == ["codex"]
        assert one_files["two.py"]["agents"] == []
        assert two_files["one.py"]["agents"] == []
        assert two_files["two.py"]["agents"] == ["claude"]

        (repo / "untracked.py").write_text("new = True\n", encoding="utf-8")
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(builds) == 2
        git(repo, "add", "one.py")
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(builds) == 3
        git(repo, "add", "two.py", "untracked.py")
        git(repo, "commit", "-m", "next")
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(builds) == 4
        assert webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, "HEAD~1", "HEAD", None) != webapp.session_files_cache_key("payload", {"one": info_one}, "one", 24.0, None, None, None)
        webapp.shared_session_files_git_snapshot(repo, "HEAD~1", "HEAD")
        assert len(builds) == 5

        other = tmp_path / "other-worktree"
        git(repo, "worktree", "add", "-b", "other", str(other))
        (other / "worktree-only.py").write_text("other = True\n", encoding="utf-8")
        webapp.shared_session_files_git_snapshot(other, None, None)
        assert len(builds) == 6

        key = ("phase-fixture",)
        webapp.compute_session_files_cache_entry(key, lambda: (payload_one, HTTPStatus.OK))
        webapp.compute_session_files_cache_entry(key, lambda: (_ for _ in ()).throw(AssertionError("fresh cache must win")))
        webapp.record_session_files_phase("bounded-details", 1.0, {"repo": "x" * 1000, "nested": {"drop": True}})
        recent = webapp.performance_metrics_payload()["recent"]
    finally:
        webapp.control_server.stop()

    phase_names = {item["surface"] for item in recent if item["role"] == "session-files"}
    assert {"phase:transcript-attribution", "phase:repository-discovery", "phase:git-snapshot", "phase:session-merge-render", "phase:cache-serialization"} <= phase_names
    hit_rows = [item for item in recent if item["surface"] == "phase:git-snapshot" and item["cache_status"] == "hit:fresh"]
    assert hit_rows and all(item["compute_ms"] == 0 for item in hit_rows)
    bounded = next(item for item in reversed(recent) if item["surface"] == "phase:bounded-details")
    assert len(bounded["details"]["repo"]) <= 512
    assert "nested" not in bounded["details"]


def test_newer_session_files_generation_cannot_be_overwritten_by_delayed_old_work(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = TmuxWebtermApp([])
    old_started = threading_module.Event()
    release_old = threading_module.Event()
    results = {}
    logical = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "s1", 24.0, "", "", ())
    old_key = (*logical, (("s1", "old-info"),), (("repo", "old-repo"),))
    new_key = (*logical, (("s1", "new-info"),), (("repo", "new-repo"),))

    def old_compute():
        old_started.set()
        assert release_old.wait(timeout=5)
        return {"files": [{"path": "old.py"}], "repos": [], "errors": []}, HTTPStatus.OK

    def run_old():
        results["old"] = webapp.compute_session_files_cache_entry(old_key, old_compute)

    def run_new():
        results["new"] = webapp.compute_session_files_cache_entry(
            new_key,
            lambda: ({"files": [{"path": "new.py"}], "repos": [], "errors": []}, HTTPStatus.OK),
        )

    old_thread = threading_module.Thread(target=run_old)
    new_thread = threading_module.Thread(target=run_new)
    try:
        old_thread.start(); assert old_started.wait(timeout=5)
        new_thread.start()
        release_old.set()
        old_thread.join(timeout=5)
        new_thread.join(timeout=5)
        assert not old_thread.is_alive()
        assert not new_thread.is_alive()
        path, _signature = webapp.session_files_disk_cache_path(new_key)
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["source_generation"] == webapp.session_files_source_generation(new_key)
        assert record["payload"]["files"] == [{"path": "new.py"}]
        assert old_key not in webapp.session_files_service.cache
        assert new_key in webapp.session_files_service.cache
        assert results["old"][0]["files"] == [{"path": "old.py"}]
        assert results["new"][0]["files"] == [{"path": "new.py"}]
    finally:
        release_old.set()
        old_thread.join(timeout=1)
        new_thread.join(timeout=1)
        webapp.control_server.stop()


def test_replace_waits_for_inflight_owner_then_publishes_fresh_payload(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = TmuxWebtermApp([])
    key = ("replace-inflight",)
    old_started, release_old, fresh_computed = threading_module.Event(), threading_module.Event(), threading_module.Event()
    results = {}
    def old_compute():
        old_started.set(); assert release_old.wait(timeout=5)
        return {"files": [{"path": "old.py"}], "repos": [], "errors": []}, HTTPStatus.OK
    def fresh_compute():
        fresh_computed.set()
        return {"files": [{"path": "fresh.py"}], "repos": [], "errors": []}, HTTPStatus.OK
    old_thread = threading_module.Thread(target=lambda: results.setdefault("old", webapp.compute_session_files_cache_entry(key, old_compute)))
    fresh_thread = threading_module.Thread(target=lambda: results.setdefault("fresh", webapp.compute_session_files_cache_entry(key, fresh_compute, replace=True)))
    try:
        old_thread.start()
        assert old_started.wait(timeout=5)
        fresh_thread.start(); release_old.set()
        old_thread.join(timeout=5); fresh_thread.join(timeout=5)
        assert not old_thread.is_alive() and not fresh_thread.is_alive()
        assert fresh_computed.is_set(); assert results["old"][0]["files"] == [{"path": "old.py"}]
        assert results["fresh"][0]["files"] == [{"path": "fresh.py"}]
    finally:
        release_old.set(); old_thread.join(timeout=1); fresh_thread.join(timeout=1); webapp.control_server.stop()


def test_background_reservation_order_not_delayed_worker_start_controls_stable_cache(no_control_socket, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = TmuxWebtermApp([])
    logical = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "s1", 24.0, "", "", ())
    old_key = (*logical, (("s1", "old-info"),), (("repo", "old-repo"),))
    new_key = (*logical, (("s1", "new-info"),), (("repo", "new-repo"),))
    old_path, stable_signature = webapp.session_files_disk_cache_path(old_key)
    try:
        old_record = webapp.session_files_service.reserve_work(old_key, stable_signature)
        assert old_record is not None
        assert old_record.stable_generation > 0

        new_payload = {"files": [{"path": "new.py"}], "repos": [], "errors": []}
        webapp.compute_session_files_cache_entry(new_key, lambda: (new_payload, HTTPStatus.OK))
        old_payload = {"files": [{"path": "old.py"}], "repos": [], "errors": []}
        old_result = webapp.compute_session_files_cache_entry(old_key, lambda: (old_payload, HTTPStatus.OK), reserved=True)

        record = json.loads(old_path.read_text(encoding="utf-8"))
        assert record["payload"]["files"] == [{"path": "new.py"}]
        assert record["source_generation"] == webapp.session_files_source_generation(new_key)
        assert old_result[0]["files"] == [{"path": "old.py"}]
        assert old_key not in webapp.session_files_service.cache
    finally:
        webapp.control_server.stop()


def test_scans_claude_and_codex_tool_changes(tmp_path):
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}},
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/new.md"}},
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text(
        '{"msg":"*** Begin Patch\\n*** Add File: src/new.py\\n*** Update File: src/app.py\\n*** Delete File: old.py\\n"}\n',
        encoding="utf-8",
    )

    claude = session_files.scan_claude_transcript(claude_path, str(tmp_path))
    codex = session_files.scan_codex_transcript(codex_path, str(tmp_path))

    assert claude[str(tmp_path / "src" / "app.py")] == {"M"}
    assert claude["/tmp/new.md"] == {"A"}
    assert codex[str(tmp_path / "src" / "new.py")] == {"A"}
    assert codex[str(tmp_path / "src" / "app.py")] == {"M"}
    assert codex[str(tmp_path / "old.py")] == {"D"}


def test_transcript_scans_collect_generated_usage_with_changes(tmp_path):
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 11},
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 500,
                        "output_tokens": 17,
                        "reasoning_output_tokens": 3,
                        "total_tokens": 1017,
                    }
                }
            },
        }) + "\n",
        encoding="utf-8",
    )

    claude_details = session_files.scan_claude_transcript_details(claude_path, str(tmp_path))
    codex_details = session_files.scan_codex_transcript_details(codex_path, str(tmp_path))

    assert claude_details["changes"][str(tmp_path / "src" / "app.py")] == {"M"}
    assert claude_details["usage"]["generated_tokens"] == 11
    assert codex_details["usage"]["generated_tokens"] == 17
    assert session_files.transcript_generated_tokens(claude_path, "claude", str(tmp_path)) == 11
    assert session_files.transcript_generated_tokens(codex_path, "codex", str(tmp_path)) == 17


def test_generated_usage_tokens_treats_reasoning_as_output_subset():
    assert session_files.generated_usage_tokens({"output_tokens": 17, "reasoning_output_tokens": 3}) == 17
    assert session_files.generated_usage_tokens({"reasoning_output_tokens": 3}) == 3
    assert session_files.generated_usage_tokens({"outputTokens": 17, "completion_tokens": 17}) == 17


def test_codex_generated_tokens_reads_latest_cumulative_usage_from_the_tail(tmp_path, monkeypatch):
    transcript = tmp_path / "rollout.jsonl"

    def usage_line(tokens):
        return json.dumps({"payload": {"info": {"total_token_usage": {"output_tokens": tokens}}}}) + "\n"

    transcript.write_text(
        usage_line(11)
        + json.dumps({"payload": "x" * (session_files._TRANSCRIPT_REVERSE_SCAN_BYTES * 3)}) + "\n"
        + usage_line(17)
        + '{"partial":',
        encoding="utf-8",
    )
    real_loads = session_files.json.loads
    parsed = []

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", tracking_loads)

    assert session_files.transcript_generated_tokens(transcript, "codex") == 17
    # Family discovery reads the rollout metadata once before preserving the tail-only fast path.
    assert len(parsed) <= 5


def test_codex_generated_tokens_sum_spawned_rollouts_into_the_parent(tmp_path, monkeypatch):
    parent = tmp_path / "rollout-parent.jsonl"
    child = tmp_path / "rollout-child.jsonl"
    grandchild = tmp_path / "rollout-grandchild.jsonl"
    unrelated = tmp_path / "rollout-unrelated.jsonl"

    def lines(thread_id, totals, model, parent_thread_id=""):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        records = [{"type": "session_meta", "payload": meta}, {"type": "turn_context", "payload": {"model": model}}]
        records.extend({"timestamp": index + 1, "payload": {"info": {"total_token_usage": {"output_tokens": total}}}} for index, total in enumerate(totals))
        return "".join(json.dumps(record) + "\n" for record in records)

    parent.write_text(lines("parent", [100, 160], "gpt-5.5"), encoding="utf-8")
    child.write_text(lines("child", [20, 50], "gpt-5.4-mini", "parent"), encoding="utf-8")
    grandchild.write_text(lines("grandchild", [5, 11], "gpt-5.5", "child"), encoding="utf-8")
    unrelated.write_text(lines("unrelated", [999], "gpt-unrelated", "other"), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda path: [parent, child, grandchild] if path == parent else [path])

    assert session_files.transcript_generated_tokens(parent, "codex") == 221
    assert session_files.transcript_generated_tokens_by_model(parent, "codex") == {"gpt-5.5": 171, "gpt-5.4-mini": 50}
    assert {event.source for event in session_files.transcript_generated_token_events(parent, "codex")} == {str(parent), str(child), str(grandchild)}


def test_transcript_usage_identity_changes_after_in_place_replacement(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"session":"first"}\n', encoding="utf-8")
    first = session_files.transcript_usage_identity(transcript, "codex")

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write('{"event":"append"}\n')
    appended = session_files.transcript_usage_identity(transcript, "codex")

    transcript.write_text('{"session":"replacement"}\n', encoding="utf-8")
    replacement = session_files.transcript_usage_identity(transcript, "codex")

    assert first
    assert appended == first
    assert replacement
    assert replacement != first


def test_claude_transcript_usage_deduplicates_repeated_message_ids(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "claude.jsonl"

    def line(message_id, output_tokens):
        return json.dumps({
            "type": "assistant",
            "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    transcript.write_text(line("msg-1", 11), encoding="utf-8")
    first = session_files.scan_claude_transcript_details(transcript)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(line("msg-1", 11) + line("msg-1", 13) + line("msg-2", 7))

    details = session_files.scan_claude_transcript_details(transcript)

    assert first["usage"]["generated_tokens"] == 11
    assert details["usage"]["generated_tokens"] == 20


def test_claude_generated_tokens_include_subagent_transcript_family(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "session-id.jsonl"
    subagent = tmp_path / "session-id" / "subagents" / "agent-a.jsonl"
    nested_subagent = tmp_path / "session-id" / "subagents" / "nested" / "agent-b.jsonl"
    subagent.parent.mkdir(parents=True)
    nested_subagent.parent.mkdir(parents=True)

    def line(message_id, output_tokens):
        return json.dumps({
            "type": "assistant",
            "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    transcript.write_text(line("parent", 11), encoding="utf-8")
    subagent.write_text(line("child-a", 17), encoding="utf-8")
    nested_subagent.write_text(line("child-b", 23), encoding="utf-8")

    identity = session_files.transcript_usage_identity(transcript, "claude")
    assert session_files.transcript_generated_tokens(transcript, "claude") == 51

    with subagent.open("a", encoding="utf-8") as handle:
        handle.write(line("child-a-more", 7))

    assert session_files.transcript_usage_identity(transcript, "claude") == identity
    assert session_files.transcript_generated_tokens(transcript, "claude") == 58


def test_transcript_generated_token_events_preserve_codex_counters_and_claude_subagents(tmp_path):
    codex = tmp_path / "rollout.jsonl"
    claude = tmp_path / "session.jsonl"
    subagent = tmp_path / "session" / "subagents" / "agent.jsonl"
    subagent.parent.mkdir(parents=True)

    def codex_line(timestamp, total):
        return json.dumps({"timestamp": timestamp, "payload": {"info": {"total_token_usage": {"output_tokens": total}}}}) + "\n"

    def claude_line(timestamp, message_id, output_tokens):
        return json.dumps({"timestamp": timestamp, "type": "assistant", "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []}}) + "\n"

    codex.write_text(codex_line(100, 11) + codex_line(160, 31), encoding="utf-8")
    claude.write_text(claude_line(100, "parent", 7) + claude_line(160, "parent", 9), encoding="utf-8")
    subagent.write_text(claude_line(130, "child", 13), encoding="utf-8")

    codex_events = session_files.transcript_generated_token_events(codex, "codex")
    claude_events = session_files.transcript_generated_token_events(claude, "claude")

    assert [(event.timestamp, event.tokens) for event in codex_events] == [(100.0, 11.0), (160.0, 20.0)]
    assert sorted((event.timestamp, event.tokens) for event in claude_events) == [(100.0, 7.0), (130.0, 13.0), (160.0, 2.0)]
    assert len({event.source for event in claude_events}) == 2


def test_normalized_codex_usage_atoms_subtract_cached_input_and_keep_effort_with_following_usage(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in [
        {"type": "session_meta", "payload": {"id": "root"}},
        {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "low"}},
        {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10, "reasoning_output_tokens": 4}}}},
        {"timestamp": 3, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}},
        {"timestamp": 4, "payload": {"info": {"total_token_usage": {"input_tokens": 150, "cached_input_tokens": 70, "output_tokens": 25, "reasoning_output_tokens": 9}}}},
    ]) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "codex")
    by_time = {
        timestamp: {(atom.direction, atom.cache_role): atom for atom in atoms if atom.timestamp == timestamp}
        for timestamp in {atom.timestamp for atom in atoms}
    }

    assert {(key, atom.quantity) for key, atom in by_time[2.0].items()} == {
        (("input", "none"), 60.0), (("input", "read"), 40.0), (("output", "none"), 10.0),
    }
    assert {(key, atom.quantity) for key, atom in by_time[4.0].items()} == {
        (("input", "none"), 20.0), (("input", "read"), 30.0), (("output", "none"), 15.0),
    }
    assert {atom.effort for atom in by_time[2.0].values()} == {"low"}
    assert {atom.effort for atom in by_time[4.0].values()} == {"high"}
    assert sum(atom.quantity for atom in atoms if atom.direction == "output") == 25.0


def test_codex_thread_settings_attribute_token_count_before_first_turn_context(tmp_path):
    transcript = tmp_path / "rollout-thread-settings.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in [
        {"type": "session_meta", "timestamp": 1, "payload": {"id": "thread-settings"}},
        {"type": "response_item", "timestamp": 2, "payload": {"text": "pretend model gpt-prose"}},
        {
            "type": "event_msg",
            "timestamp": 3,
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {
                    "model": "gpt-explicit",
                    "reasoning_effort": "xhigh",
                    "service_tier": "default",
                },
            },
        },
        {
            "type": "event_msg",
            "timestamp": 4,
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}},
            },
        },
        {"type": "turn_context", "timestamp": 5, "payload": {"model": "gpt-later", "effort": "low"}},
    ]) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "codex")

    assert {atom.model for atom in atoms} == {"gpt-explicit"}
    assert {atom.model_evidence for atom in atoms} == {
        "thread_settings_applied.thread_settings.model",
    }
    assert {atom.effort for atom in atoms} == {"xhigh"}
    assert {atom.service_tier for atom in atoms} == {"default"}


def test_codex_usage_atom_iterator_yields_before_reading_the_rest_of_one_large_file(monkeypatch, tmp_path):
    def records(*_args, **_kwargs):
        yield {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}}
        yield {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}}
        raise AssertionError("iterator materialized the rest of the transcript before yielding")

    monkeypatch.setattr(session_files, "transcript_json_records", records)
    atoms = session_files.iter_codex_transcript_usage_atoms(tmp_path / "large.jsonl")

    first = next(atoms)
    assert first.timestamp == 2
    assert first.model == "gpt-5.6"


def test_codex_usage_atom_iterator_keeps_one_large_real_file_memory_bounded(tmp_path):
    transcript = tmp_path / "large-rollout.jsonl"
    records = [{"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6"}}]
    records.extend(
        {"timestamp": index + 2, "payload": {"info": {"total_token_usage": {"input_tokens": index + 1, "output_tokens": index + 1}}}}
        for index in range(20_000)
    )
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    del records

    tracemalloc.start()
    count = sum(1 for _atom in session_files.iter_codex_transcript_usage_atoms(transcript))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert count == 40_000
    assert peak < 8 * 1024 * 1024


def test_codex_usage_atoms_keep_explicit_pricing_profile_and_service_tier(tmp_path):
    transcript = tmp_path / "codex-pricing-context.jsonl"
    transcript.write_text("\n".join(json.dumps(item) for item in [
        {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high", "pricing_profile": "batch", "service_tier": "flex"}},
        {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ]) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "codex")

    assert {atom.pricing_profile for atom in atoms} == {"batch"}
    assert {atom.service_tier for atom in atoms} == {"flex"}


def test_usage_component_delta_resets_all_reported_components_and_leaves_missing_unknown():
    previous = {
        ("input", "text", "none", "tokens"): 100.0,
        ("input", "text", "read", "tokens"): 40.0,
        ("output", "text", "none", "tokens"): 20.0,
    }
    current = {
        ("input", "text", "none", "tokens"): 9.0,
        ("input", "text", "read", "tokens"): None,
        ("output", "text", "none", "tokens"): 4.0,
    }

    # A single counter rollback is a provider rollover, not a negative delta;
    # both reported classes restart together and an omitted class stays absent.
    assert session_files.usage_component_delta(current, previous) == {
        ("input", "text", "none", "tokens"): 9.0,
        ("output", "text", "none", "tokens"): 4.0,
    }


def test_normalized_claude_usage_atoms_dedupe_components_and_preserve_cache_write_duration(tmp_path):
    transcript = tmp_path / "claude.jsonl"

    def record(timestamp, input_tokens, output_tokens, write_5m, write_1h):
        return {
            "timestamp": timestamp,
            "type": "assistant",
            "message": {
                "id": "message-1",
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": write_5m,
                    "cache_creation_input_tokens_1h": write_1h,
                    "output_tokens": output_tokens,
                },
            },
        }

    transcript.write_text("\n".join(json.dumps(item) for item in [
        record(1, 10, 5, 30, 40), record(2, 12, 7, 35, 44),
    ]) + "\n", encoding="utf-8")
    atoms = session_files.transcript_usage_atoms(transcript, "claude")
    quantities = {}
    for atom in atoms:
        key = (atom.direction, atom.cache_role)
        quantities[key] = quantities.get(key, 0.0) + atom.quantity

    assert quantities == {
        ("input", "none"): 12.0,
        ("input", "read"): 20.0,
        ("input", "write_5m"): 35.0,
        ("input", "write_1h"): 44.0,
        ("output", "none"): 7.0,
    }


def test_claude_usage_atoms_prefer_nested_cache_creation_duration_split(tmp_path):
    transcript = tmp_path / "claude-nested-cache.jsonl"
    transcript.write_text(json.dumps({
        "timestamp": 1,
        "type": "assistant",
        "message": {
            "id": "message-1",
            "model": "claude-opus-4-8",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 999,
                "cache_creation": {"ephemeral_5m_input_tokens": 30, "ephemeral_1h_input_tokens": 40},
                "output_tokens": 5,
            },
        },
    }) + "\n", encoding="utf-8")

    atoms = session_files.transcript_usage_atoms(transcript, "claude")
    quantities = {(atom.direction, atom.cache_role): atom.quantity for atom in atoms}

    assert quantities[("input", "write_5m")] == 30
    assert quantities[("input", "write_1h")] == 40
    assert sum(atom.quantity for atom in atoms if atom.cache_role.startswith("write")) == 70
    assert {atom.model for atom in atoms} == {"claude-opus-4-8"}


def test_normalized_usage_atoms_keep_codex_subagents_structurally_separate(tmp_path, monkeypatch):
    parent = tmp_path / "parent.jsonl"
    child = tmp_path / "child.jsonl"

    def rollout(thread_id, parent_thread_id, model, effort, output):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(item) for item in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 1, "type": "turn_context", "payload": {"model": model, "effort": effort}},
            {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": output}}}},
        ]) + "\n"

    parent.write_text(rollout("parent", "", "gpt-parent", "med", 5), encoding="utf-8")
    child.write_text(rollout("child", "parent", "gpt-child", "high", 7), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda _path: [parent, child])

    atoms = session_files.transcript_usage_atoms(parent, "codex")
    output_atoms = [atom for atom in atoms if atom.direction == "output"]

    assert {(atom.model, atom.root_thread_id, atom.agent_thread_id, atom.parent_thread_id, atom.depth, atom.quantity) for atom in output_atoms} == {
        ("gpt-parent", "parent", "parent", "", 0, 5.0),
        ("gpt-child", "parent", "child", "parent", 1, 7.0),
    }
    # Provider transcript usage is self-only for each rollout. If a provider
    # starts reporting parent counters cumulatively including child work, this
    # invariant must be revisited before cost atoms can remain exact.
    assert sum(atom.quantity for atom in output_atoms) == 12.0


def test_usage_atom_family_event_ids_are_stable_and_distinct_across_subagents(tmp_path, monkeypatch):
    claude = tmp_path / "session.jsonl"
    claude_child = tmp_path / "session" / "subagents" / "agent.jsonl"
    claude_child.parent.mkdir(parents=True)
    codex = tmp_path / "rollout-parent.jsonl"
    codex_child = tmp_path / "rollout-child.jsonl"

    def claude_line(output_tokens):
        return json.dumps({
            "timestamp": 100,
            "type": "assistant",
            "message": {"id": "provider-message-1", "model": "claude-opus-4-8", "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    def codex_lines(thread_id, parent_thread_id, output_tokens):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 100, "type": "turn_context", "payload": {"model": "gpt-5.6"}},
            {"timestamp": 101, "payload": {"info": {"total_token_usage": {"output_tokens": output_tokens}}}},
        ]) + "\n"

    claude.write_text(claude_line(11), encoding="utf-8")
    claude_child.write_text(claude_line(13), encoding="utf-8")
    codex.write_text(codex_lines("root", "", 17), encoding="utf-8")
    codex_child.write_text(codex_lines("child", "root", 19), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda _path: [codex, codex_child])

    first = session_files.transcript_usage_atoms(claude, "claude") + session_files.transcript_usage_atoms(codex, "codex")
    second = session_files.transcript_usage_atoms(claude, "claude") + session_files.transcript_usage_atoms(codex, "codex")

    assert [(atom.event_id, atom.direction, atom.cache_role, atom.quantity) for atom in first] == [
        (atom.event_id, atom.direction, atom.cache_role, atom.quantity) for atom in second
    ]
    identities = [(atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit) for atom in first]
    assert len(identities) == len(set(identities))
    assert sum(atom.quantity for atom in first if atom.direction == "output") == 60.0


def test_codex_child_outside_recent_candidate_window_is_documented_under_count(tmp_path):
    parent = tmp_path / "rollout-parent.jsonl"
    child = tmp_path / "rollout-child.jsonl"

    def rollout(thread_id, parent_thread_id, output_tokens):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-5.6"}},
            {"timestamp": 2, "payload": {"info": {"total_token_usage": {"output_tokens": output_tokens}}}},
        ]) + "\n"

    parent.write_text(rollout("parent", "", 5), encoding="utf-8")
    child.write_text(rollout("child", "parent", 7), encoding="utf-8")

    family = session_files.codex_transcript_family_paths(parent, candidates=[parent])
    atoms = session_files.transcript_usage_atoms(parent, "codex", family_paths=family)

    assert family == [parent.resolve()]
    assert sum(atom.quantity for atom in atoms if atom.direction == "output") == 5.0


def test_normalized_usage_atoms_keep_parent_child_grandchild_model_efforts_separate(tmp_path, monkeypatch):
    parent, child, grandchild = (tmp_path / name for name in ("parent.jsonl", "child.jsonl", "grandchild.jsonl"))

    def rollout(thread_id, parent_id, effort, output):
        meta = {"id": thread_id}
        if parent_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 1, "type": "turn_context", "payload": {"model": "gpt-shared", "effort": effort}},
            {"timestamp": 2, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": output}}}},
        ]) + "\n"

    parent.write_text(rollout("parent", "", "low", 3), encoding="utf-8")
    child.write_text(rollout("child", "parent", "high", 5), encoding="utf-8")
    grandchild.write_text(rollout("grandchild", "child", "xhigh", 7), encoding="utf-8")
    monkeypatch.setattr(session_files, "codex_transcript_family_paths", lambda _path: [parent, child, grandchild])

    output = [atom for atom in session_files.transcript_usage_atoms(parent, "codex") if atom.direction == "output"]
    assert {(atom.model, atom.effort, atom.root_thread_id, atom.agent_thread_id, atom.parent_thread_id, atom.depth, atom.quantity) for atom in output} == {
        ("gpt-shared", "low", "parent", "parent", "", 0, 3.0),
        ("gpt-shared", "high", "parent", "child", "parent", 1, 5.0),
        ("gpt-shared", "xhigh", "parent", "grandchild", "child", 2, 7.0),
    }


def test_direct_image_usage_atoms_require_a_correlated_request_model_and_do_not_add_total_tokens():
    response = {
        "id": "img-response",
        "usage": {
            "total_tokens": 100,
            "input_tokens": 50,
            "output_tokens": 50,
            "input_tokens_details": {"text_tokens": 10, "image_tokens": 40},
        },
    }
    atoms = session_files.direct_image_usage_atoms(
        request={"model": "gpt-image-2"}, response=response, timestamp=100, source="direct-image", request_id="request-1", root_thread_id="root", agent_thread_id="child", parent_thread_id="root", depth=1,
    )

    assert {(atom.direction, atom.modality, atom.quantity) for atom in atoms} == {
        ("input", "text", 10.0), ("input", "image", 40.0), ("output", "image", 50.0),
    }
    assert all(atom.model == "gpt-image-2" for atom in atoms)
    assert {(atom.root_thread_id, atom.agent_thread_id, atom.parent_thread_id, atom.depth) for atom in atoms} == {("root", "child", "root", 1)}
    assert session_files.direct_image_usage_atoms(request={}, response=response, timestamp=100, source="direct-image") == []


def test_opaque_responses_image_tool_is_visible_but_has_no_invented_model_or_token_usage():
    atoms = session_files.opaque_responses_image_tool_atoms(timestamp=100, source="responses", call_id="call-1", root_thread_id="root", agent_thread_id="child")

    assert len(atoms) == 1
    atom = atoms[0]
    assert (atom.provider, atom.model, atom.modality, atom.unit, atom.quantity) == ("openai", "unknown", "image", "requests", 1)
    assert atom.tool_name == "image_generation_call"
    assert atom.telemetry_complete is False
    assert session_files.opaque_responses_image_tool_atoms(timestamp=100, source="responses") == []


def test_codex_transcript_scan_uses_incremental_append_cache(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens):
        return json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"last_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    first_line = line("a.py", 5)
    second_line = line("b.py", 7)
    transcript.write_text(first_line, encoding="utf-8")
    first_key = session_files.codex_transcript_scan_cache_key(transcript, str(tmp_path), True)

    first = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert first["changes"] == {str(tmp_path / "a.py"): {"M"}}
    assert first["usage"]["generated_tokens"] == 5

    transcript.write_text(first_line + second_line, encoding="utf-8")
    second_key = session_files.codex_transcript_scan_cache_key(transcript, str(tmp_path), True)
    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    second = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    parsed_top_level_lines = [value for value in parsed_lines if isinstance(value, str) and value.endswith("\n")]

    assert first_key == second_key
    assert parsed_top_level_lines == [second_line]
    assert second["changes"] == {str(tmp_path / "a.py"): {"M"}, str(tmp_path / "b.py"): {"M"}}
    assert second["usage"]["generated_tokens"] == 12


def test_codex_transcript_raw_scan_is_shared_across_cwds_and_derives_paths(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    line = json.dumps({
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "git add relative.py"}),
        },
    }) + "\n"
    transcript.write_text(line, encoding="utf-8")
    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    first = session_files.scan_codex_transcript_details(transcript, str(first_cwd), include_patch_text=False)
    second = session_files.scan_codex_transcript_details(transcript, str(second_cwd), include_patch_text=False)

    assert session_files.codex_transcript_scan_cache_key(transcript, str(first_cwd), False) == session_files.codex_transcript_scan_cache_key(transcript, str(second_cwd), True)
    assert first["changes"] == {str(first_cwd / "relative.py"): {"M"}}
    assert second["changes"] == {str(second_cwd / "relative.py"): {"M"}}
    assert [value for value in parsed_lines if isinstance(value, str) and value.endswith("\n")] == [line]


def test_historical_codex_index_reuses_warm_raw_candidates_without_decoding(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    session_files._HISTORICAL_CODEX_TRANSCRIPT_INDEX.clear()
    first_repo = tmp_path / "first"
    second_repo = tmp_path / "second"
    first_repo.mkdir()
    second_repo.mkdir()

    def transcript(path, repo, name):
        path.write_text(json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {name}", "workdir": str(repo)}),
            },
        }) + "\n", encoding="utf-8")

    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    transcript(first, first_repo, "one.py")
    transcript(second, second_repo, "two.py")
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda _cwd: None)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [first, second])

    assert session_files.historical_codex_transcript_for_cwd(str(first_repo), cutoff=0) == first
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    monkeypatch.setattr(session_files.json, "loads", lambda _value: (_ for _ in ()).throw(AssertionError("warm historical lookup must use the index")))
    assert session_files.historical_codex_transcript_for_cwd(str(second_repo), cutoff=0) == second


def test_historical_codex_index_skips_candidates_older_than_cutoff_before_decoding(tmp_path, monkeypatch):
    session_files._HISTORICAL_CODEX_TRANSCRIPT_INDEX.clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    old = tmp_path / "old.jsonl"
    recent = tmp_path / "recent.jsonl"
    old.write_text("old\n", encoding="utf-8")
    recent.write_text("recent\n", encoding="utf-8")
    os.utime(old, (100, 100))
    os.utime(recent, (200, 200))
    scanned = []

    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda _cwd: None)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [old, recent])

    def raw_changes(path):
        scanned.append(path)
        return {str(repo / "changed.py"): {"M"}}

    monkeypatch.setattr(session_files, "codex_transcript_raw_shell_changes", raw_changes)

    assert session_files.historical_codex_transcript_for_cwd(str(repo), cutoff=150) == recent
    assert scanned == [recent]


def test_codex_transcript_scan_cache_holds_full_recent_candidate_window(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    line = json.dumps({"type": "session_meta", "payload": {"cwd": str(tmp_path)}}) + "\n"
    transcripts = []
    for index in range(CODEX_TRANSCRIPT_SCAN_LIMIT * 2):
        transcript = tmp_path / f"rollout-{index:03d}.jsonl"
        transcript.write_text(line, encoding="utf-8")
        transcripts.append(transcript)

    for transcript in transcripts:
        session_files.scan_codex_transcript_details(transcript, str(tmp_path), include_patch_text=False)

    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    for transcript in transcripts:
        session_files.scan_codex_transcript_details(transcript, str(tmp_path), include_patch_text=False)

    assert parsed_lines == []
    session_files._TRANSCRIPT_SCAN_CACHE.clear()


def test_transcript_scan_store_survives_cold_reload_and_resumes_append(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES", 0)
    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_APPEND_BYTES", 0)
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens, secret):
        return json.dumps({
            "type": "response_item",
            "secret_blob": secret,
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"last_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    first_line = line("a.py", 5, "RAW_SECRET_FIRST")
    second_line = line("b.py", 7, "RAW_SECRET_SECOND")
    third_line = line("c.py", 11, "RAW_SECRET_THIRD")
    transcript.write_text(first_line + second_line, encoding="utf-8")
    first = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    cache_key = session_files.codex_transcript_scan_cache_key(transcript, str(tmp_path), True)
    assert cache_key is not None
    cache_path = session_files.transcript_scan_store_path(cache_key)
    assert cache_path.exists()
    persisted = cache_path.read_text(encoding="utf-8")
    assert "RAW_SECRET" not in persisted
    assert "git add" not in persisted
    assert oct(cache_path.stat().st_mode & 0o777) == "0o600"

    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    real_loads = session_files.json.loads
    parsed = []

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", tracking_loads)
    cold = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert [value for value in parsed if isinstance(value, str) and value.endswith("\n")] == []
    assert cold == first

    transcript.write_text(first_line + second_line + third_line, encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    parsed.clear()
    appended = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert [value for value in parsed if isinstance(value, str) and value.endswith("\n")] == [third_line]
    assert appended["changes"] == {
        str(tmp_path / "a.py"): {"M"},
        str(tmp_path / "b.py"): {"M"},
        str(tmp_path / "c.py"): {"M"},
    }
    assert appended["usage"]["generated_tokens"] == 23


def test_transcript_scan_store_rejects_schema_tail_and_same_inode_prefix_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES", 0)
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "claude.jsonl"

    def line(path_name, padding):
        return json.dumps({
            "type": "assistant",
            "padding": padding,
            "message": {"usage": {"output_tokens": 5}, "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": path_name}}]},
        }) + "\n"

    original = line("a.py", "x" * 5000)
    replacement = line("b.py", "x" * 5000)
    stable_tail = line("tail.py", "y" * 1000)
    assert len(original) == len(replacement)
    transcript.write_text(original + stable_tail, encoding="utf-8")
    assert str(tmp_path / "a.py") in session_files.scan_claude_transcript_details(transcript, str(tmp_path))["changes"]
    cache_key = session_files.claude_transcript_scan_cache_key(transcript)
    assert cache_key is not None
    cache_path = session_files.transcript_scan_store_path(cache_key)

    record = json.loads(cache_path.read_text(encoding="utf-8"))
    record["schema_version"] = 999
    cache_path.write_text(json.dumps(record), encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    reparsed = session_files.scan_claude_transcript_details(transcript, str(tmp_path))
    assert str(tmp_path / "a.py") in reparsed["changes"]

    transcript.write_text(replacement + stable_tail, encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    replaced = session_files.scan_claude_transcript_details(transcript, str(tmp_path))
    assert str(tmp_path / "a.py") not in replaced["changes"]
    assert str(tmp_path / "b.py") in replaced["changes"]

    transcript.write_text(replacement, encoding="utf-8")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    truncated = session_files.scan_claude_transcript_details(transcript, str(tmp_path))
    assert str(tmp_path / "tail.py") not in truncated["changes"]


def test_transcript_scan_store_is_bounded_and_atomic_failure_is_nonfatal(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    store_dir = session_files.transcript_scan_store_dir()
    store_dir.mkdir(parents=True)
    for index in range(4):
        path = store_dir / f"{index}.json"
        path.write_text("x" * 10, encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))
    session_files.prune_transcript_scan_store(max_entries=2, max_bytes=100)
    assert sorted(path.name for path in store_dir.glob("*.json")) == ["2.json", "3.json"]
    session_files.prune_transcript_scan_store(max_entries=2, max_bytes=10)
    assert len(list(store_dir.glob("*.json"))) == 1

    monkeypatch.setattr(session_files, "_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES", 0)
    monkeypatch.setattr(session_files, "atomic_write_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": str(tmp_path)}}) + "\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        details = session_files.scan_codex_transcript_details(transcript, str(tmp_path))
    assert details["changes"] == {}
    assert "failed to persist transcript scan cache" in caplog.text


def test_transcript_scan_store_keeps_incomplete_stats_backfill_cursors(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    store_dir = session_files.transcript_scan_store_dir()
    store_dir.mkdir(parents=True)

    def write_cursor(name, identity, state, mtime):
        path = store_dir / name
        path.write_text(json.dumps({
            "schema_version": session_files._TRANSCRIPT_SCAN_STORE_VERSION,
            "identity": identity,
            "state": state,
        }), encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    incomplete = write_cursor(
        "incomplete.json",
        ["stats-current-codex", 3, 1, 2, "/tmp/incomplete.jsonl"],
        {"offset": 10, "size": 20},
        100,
    )
    completed = write_cursor(
        "completed.json",
        ["stats-current-codex", 3, 1, 3, "/tmp/completed.jsonl"],
        {"offset": 20, "size": 20},
        102,
    )
    generic = write_cursor(
        "generic.json",
        ["codex", 7, 1, 4, "/tmp/generic.jsonl"],
        {"offset": 20, "size": 20},
        101,
    )

    session_files.prune_transcript_scan_store(max_entries=1, max_bytes=10_000)

    assert incomplete.exists()
    assert completed.exists()
    assert generic.exists() is False

    session_files.prune_transcript_scan_store(max_entries=1, max_bytes=1)
    assert incomplete.exists() is False
    assert completed.exists() is False


def test_transcript_scan_store_silently_accepts_file_vanishing_before_sort_metadata_read(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    store_dir = session_files.transcript_scan_store_dir()
    store_dir.mkdir(parents=True)
    cache_path = store_dir / "vanishing-before-sort.json"
    cache_path.write_text("{}", encoding="utf-8")
    original_stat = Path.stat
    target_stat_calls = 0

    def vanish_before_first_stat(path, *args, **kwargs):
        nonlocal target_stat_calls
        if path == cache_path:
            target_stat_calls += 1
            if target_stat_calls == 1:
                path.unlink()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanish_before_first_stat)
    with caplog.at_level("WARNING"):
        session_files.prune_transcript_scan_store(max_entries=1, max_bytes=10_000)

    assert target_stat_calls == 1
    assert "failed to inspect transcript scan cache" not in caplog.text
    assert "failed to prune transcript scan cache" not in caplog.text


@pytest.mark.parametrize("protected", [False, True])
def test_transcript_scan_store_silently_accepts_file_vanishing_before_size_read(tmp_path, monkeypatch, caplog, protected):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    store_dir = session_files.transcript_scan_store_dir()
    store_dir.mkdir(parents=True)
    cache_path = store_dir / "vanishing.json"
    payload = {}
    if protected:
        payload = {
            "schema_version": session_files._TRANSCRIPT_SCAN_STORE_VERSION,
            "identity": ["stats-current-codex", 3, 1, 2, "/tmp/vanishing.jsonl"],
            "state": {"offset": 10, "size": 20},
        }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    original_stat = Path.stat
    target_stat_calls = 0

    def vanish_before_second_stat(path, *args, **kwargs):
        nonlocal target_stat_calls
        if path == cache_path:
            target_stat_calls += 1
            if target_stat_calls == 2:
                path.unlink()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanish_before_second_stat)
    with caplog.at_level("WARNING"):
        session_files.prune_transcript_scan_store(max_entries=1, max_bytes=10_000)

    assert target_stat_calls == 2
    assert "failed to prune transcript scan cache" not in caplog.text


def test_transcript_scan_store_keeps_stat_errors_loud(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    store_dir = session_files.transcript_scan_store_dir()
    store_dir.mkdir(parents=True)
    cache_path = store_dir / "unreadable.json"
    cache_path.write_text("{}", encoding="utf-8")
    original_stat = Path.stat
    target_stat_calls = 0

    def deny_second_stat(path, *args, **kwargs):
        nonlocal target_stat_calls
        if path == cache_path:
            target_stat_calls += 1
            if target_stat_calls == 2:
                raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_second_stat)
    with caplog.at_level("WARNING"):
        session_files.prune_transcript_scan_store(max_entries=1, max_bytes=10_000)

    assert target_stat_calls == 2
    assert "failed to prune transcript scan cache unreadable.json: denied" in caplog.text


def test_transcript_scan_cache_has_one_owner_and_bounds_claude_message_ids():
    state = session_files.new_claude_transcript_scan_state()
    for index in range(session_files._TRANSCRIPT_SCAN_MESSAGE_ID_MAX + 3):
        session_files.update_claude_transcript_scan_state(state, json.dumps({
            "type": "assistant",
            "message": {"id": f"message-{index}", "usage": {"output_tokens": 1}, "content": []},
        }))
    assert len(state["usage_tokens_by_message_id"]) == session_files._TRANSCRIPT_SCAN_MESSAGE_ID_MAX
    assert "message-0" not in state["usage_tokens_by_message_id"]
    source = Path(session_files.__file__).read_text(encoding="utf-8")
    assert "_CODEX_TRANSCRIPT_SCAN_CACHE" not in source
    assert "_CLAUDE_TRANSCRIPT_SCAN_CACHE" not in source
    assert source.count("_TRANSCRIPT_SCAN_CACHE: dict") == 1


def test_transcript_change_maps_keep_only_the_newest_bounded_paths():
    assert session_files.TRANSCRIPT_CHANGE_PATH_LIMIT == 256

    claude_state = session_files.new_claude_transcript_scan_state()
    codex_state = session_files.new_codex_transcript_scan_state()
    for index in range(session_files.TRANSCRIPT_CHANGE_PATH_LIMIT + 3):
        claude_path = f"/tmp/claude-{index:03d}.py"
        session_files.update_claude_transcript_scan_state(claude_state, json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": claude_path}}],
            },
        }))
        codex_path = f"codex-{index:03d}.py"
        session_files.update_codex_transcript_scan_state(
            codex_state,
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": f"git add {codex_path}", "workdir": "/tmp"}),
                },
            }),
        )
        session_files.update_codex_transcript_scan_state(
            codex_state,
            f"*** Update File: {codex_path}",
        )

    expected_first_claude = "/tmp/claude-003.py"
    expected_first_codex_suffix = "codex-003.py"
    assert len(claude_state["raw_changes"]) == session_files.TRANSCRIPT_CHANGE_PATH_LIMIT
    assert next(iter(claude_state["raw_changes"])) == expected_first_claude
    assert len(codex_state["shell_changes"]) == session_files.TRANSCRIPT_CHANGE_PATH_LIMIT
    assert next(iter(codex_state["shell_changes"])).endswith(expected_first_codex_suffix)
    assert len(codex_state["patch_changes"]) == session_files.TRANSCRIPT_CHANGE_PATH_LIMIT
    assert next(iter(codex_state["patch_changes"])).endswith(expected_first_codex_suffix)

    oversized = {
        f"/tmp/persisted-{index:03d}.py": {"M"}
        for index in range(session_files.TRANSCRIPT_CHANGE_PATH_LIMIT + 2)
    }
    serialized = session_files.serialized_transcript_marker_map(oversized)
    assert len(serialized) == session_files.TRANSCRIPT_CHANGE_PATH_LIMIT
    assert next(iter(serialized)) == "/tmp/persisted-002.py"


def test_codex_transcript_scan_restarts_after_truncation(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens):
        return json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"last_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    original_line = line("very-long-original-name.py", 5)
    replacement_line = line("b.py", 3)
    assert len(replacement_line) < len(original_line)
    transcript.write_text(original_line, encoding="utf-8")
    assert session_files.scan_codex_transcript_details(transcript, str(tmp_path))["changes"] == {str(tmp_path / "very-long-original-name.py"): {"M"}}

    transcript.write_text(replacement_line, encoding="utf-8")
    refreshed = session_files.scan_codex_transcript_details(transcript, str(tmp_path))

    assert refreshed["changes"] == {str(tmp_path / "b.py"): {"M"}}
    assert refreshed["usage"]["generated_tokens"] == 3


def test_codex_transcript_scan_restarts_when_existing_bytes_change(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"

    def line(path_name, generated_tokens):
        return json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": f"git add {path_name}", "workdir": str(tmp_path)}),
                "info": {"total_token_usage": {"output_tokens": generated_tokens}},
            },
        }) + "\n"

    original_line = line("a.py", 5)
    replacement_line = line("b.py", 3000)
    assert len(replacement_line) >= len(original_line)
    transcript.write_text(original_line, encoding="utf-8")
    assert session_files.scan_codex_transcript_details(transcript, str(tmp_path))["changes"] == {str(tmp_path / "a.py"): {"M"}}

    transcript.write_text(replacement_line, encoding="utf-8")
    refreshed = session_files.scan_codex_transcript_details(transcript, str(tmp_path))

    assert refreshed["changes"] == {str(tmp_path / "b.py"): {"M"}}
    assert refreshed["usage"]["generated_tokens"] == 3000


def test_session_touched_dirs_collects_edited_dirs(tmp_path):
    # session_touched_dirs returns the unique containing dirs of files the agents EDITED (not read),
    # so repo detection can find the real repo even when the live cwd is a non-repo.
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tmp_path / "repo" / "a" / "x.py")}},
                {"type": "tool_use", "name": "Write", "input": {"file_path": str(tmp_path / "repo" / "a" / "y.py")}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tmp_path / "repo" / "b" / "z.py")}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(tmp_path / "other" / "r.py")}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, tmp_path)])
    dirs = set(session_files.session_touched_dirs(info))
    # dir 'a' deduped across two edited files; 'b' included; the Read-only 'other' dir is excluded.
    assert dirs == {str(tmp_path / "repo" / "a"), str(tmp_path / "repo" / "b")}


def test_session_files_hours_controls_transcript_cutoff(tmp_path):
    touched = tmp_path / "older.py"
    touched.write_text("print('old edit')\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(touched)}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    transcript_mtime = 10_000
    now = transcript_mtime + 2 * 3600
    os.utime(transcript, (transcript_mtime, transcript_mtime))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, tmp_path)])

    one_hour = session_files.session_files_payload_for_info(info, hours=1, now=now)
    four_hours = session_files.session_files_payload_for_info(info, hours=4, now=now)

    assert [item["path"] for item in one_hour["files"]] == []
    assert [item["path"] for item in four_hours["files"]] == [str(touched)]


def test_session_files_payload_keeps_boundary_touched_repo_stable_for_grace(tmp_path):
    primary = tmp_path / "yolomux.dev8001"
    secondary = tmp_path / "ai-config"
    for repo in (primary, secondary):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "base")
    transcript = tmp_path / "claude.jsonl"
    touched = secondary / "tracked.txt"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(touched)}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    transcript_mtime = 10_000.0
    os.utime(transcript, (transcript_mtime, transcript_mtime))
    touched.write_text("changed\n", encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, primary)])

    before_boundary = session_files.session_files_payload_for_info(info, hours=1, now=transcript_mtime + 3600 - 0.5)
    after_boundary = session_files.session_files_payload_for_info(info, hours=1, now=transcript_mtime + 3600 + 0.5)

    before_repos = {item["repo"] for item in before_boundary["repos"]}
    after_repos = {item["repo"] for item in after_boundary["repos"]}
    assert before_repos == after_repos == {str(secondary)}
    assert str(primary) not in after_repos


def test_session_files_payload_includes_zero_change_live_pane_repos_from_rendered_repo_set(tmp_path):
    primary = tmp_path / "yolomux.dev8001"
    sibling = tmp_path / "yolomux.dev8002"
    changed = tmp_path / "ai-config"
    for repo in (primary, sibling, changed):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "base")
    (changed / "tracked.txt").write_text("changed\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(primary / "tracked.txt")}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(changed / "tracked.txt")}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (1500, 1500))
    panes = [
        PaneInfo(session="s1", window="0", pane="0", pane_id="%1", target="s1:0.0", current_path=str(primary), command="zsh", active=True, window_active=True, title="", pid=11),
        PaneInfo(session="s1", window="0", pane="1", pane_id="%2", target="s1:0.1", current_path=str(sibling), command="zsh", active=False, window_active=True, title="", pid=12),
    ]
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[agent("claude", transcript, primary)])

    samples = [session_files.session_files_payload_for_info(info, hours=24, now=1600 + index) for index in range(3)]

    assert [[repo["repo"] for repo in sample["repos"]] for sample in samples] == [
        [str(changed), str(primary), str(sibling)],
        [str(changed), str(primary), str(sibling)],
        [str(changed), str(primary), str(sibling)],
    ]
    for payload in samples:
        rendered_repos = {item["repo"] for item in payload["files"] if item["status"] != "T" and item["repo"]}
        assert rendered_repos == {str(changed)}
        by_repo = {item["repo"]: item for item in payload["repos"]}
        assert by_repo[str(changed)]["count"] == sum(1 for item in payload["files"] if item["status"] != "T" and item["repo"] == str(changed))
        assert by_repo[str(primary)]["count"] == 0
        assert by_repo[str(primary)]["touched_count"] == 1
        assert by_repo[str(sibling)]["count"] == 0
        assert by_repo[str(sibling)]["touched_count"] == 0
        assert any(item["repo"] == str(primary) and item["status"] == "T" for item in payload["files"])


def test_session_files_payload_includes_clean_numbered_workdir_repo_when_pane_is_home(tmp_path, monkeypatch):
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
    monkeypatch.setattr(session_files, "session_workdir", lambda session: repo if session == "8002" else home)

    payload = session_files.session_files_payload_for_info(info, hours=24, now=1600)

    assert payload["files"] == []
    assert payload["repos"] == [{
        "repo": str(repo),
        "branch": "master",
        "count": 0,
        "touched_count": 0,
        "added": 0,
        "removed": 0,
        "from_ref": "default",
        "to_ref": "base",
        "error": "",
    }]


def test_session_files_payload_carries_agent_window_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    touched = repo / "app.py"
    touched.write_text("print('hi')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "base")

    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(
        '{"msg":"*** Begin Patch\\n*** Update File: app.py\\n"}\n',
        encoding="utf-8",
    )
    os.utime(transcript, (time.time(), time.time()))
    panes = [
        PaneInfo(session="s1", window="0", pane="0", pane_id="%10", target="s1:0.0", current_path=str(repo), command="codex", active=True, window_active=True, title="", pid=10, process_label="codex"),
        PaneInfo(session="s1", window="1", pane="0", pane_id="%11", target="s1:1.0", current_path=str(tmp_path), command="bash", active=True, window_active=False, title="", pid=11, process_label="bash"),
    ]
    info = SessionInfo(
        session="s1",
        panes=panes,
        selected_pane=panes[0],
        agents=[AgentInfo("s1", "codex", 10, "s1:0.0", "codex", str(repo), "running", "sid", str(transcript), None)],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    item = next(row for row in payload["files"] if row["path"] == "app.py")

    assert item["agent_windows"] == [{"kind": "codex", "window": "0", "window_index": 0, "pane": "0", "pane_target": "s1:0.0"}]


def test_scan_claude_transcript_incrementally_scans_complete_appends_and_reuses_raw_parse_for_cwds(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "c.jsonl"
    first_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 5}, "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]}}) + "\n"
    second_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 7}, "content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}}]}}) + "\n"
    transcript.write_text(first_line, encoding="utf-8")
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    first = session_files.scan_claude_transcript_details(transcript, str(root_a))
    assert first["changes"] == {str(root_a / "a.py"): {"M"}}
    assert first["usage"]["generated_tokens"] == 5

    transcript.write_text(first_line + second_line, encoding="utf-8")
    real_loads = session_files.json.loads
    parsed_lines = []

    def counting_loads(value):
        parsed_lines.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", counting_loads)
    second = session_files.scan_claude_transcript_details(transcript, str(root_a))
    same_raw_parse = session_files.scan_claude_transcript_details(transcript, str(root_b))

    assert [value for value in parsed_lines if isinstance(value, str) and value.endswith("\n")] == [second_line]
    assert second["changes"] == {str(root_a / "a.py"): {"M"}, str(root_a / "b.py"): {"A"}}
    assert same_raw_parse["changes"] == {str(root_b / "a.py"): {"M"}, str(root_b / "b.py"): {"A"}}
    assert second["usage"]["generated_tokens"] == 12


def test_transcript_scan_streams_complete_lines_without_reading_the_full_file(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("".join(
        json.dumps({
            "payload": {"info": {"total_token_usage": {"output_tokens": index + 1}}},
            "padding": "x" * 4096,
        }) + "\n"
        for index in range(256)
    ), encoding="utf-8")
    real_open = session_files.Path.open
    readline_calls = 0

    class TrackingFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def seek(self, *args):
            return self.handle.seek(*args)

        def readline(self, *args):
            nonlocal readline_calls
            readline_calls += 1
            return self.handle.readline(*args)

        def read(self, size=-1):
            assert size >= 0, "transcript scans must not materialize the full unread file"
            return self.handle.read(size)

    def tracking_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        return TrackingFile(handle) if path == transcript and args and args[0] == "rb" else handle

    monkeypatch.setattr(session_files.Path, "open", tracking_open)
    details = session_files.scan_codex_transcript_details(transcript)

    assert details["usage"]["generated_tokens"] == 256
    assert readline_calls == 258  # one bounded prefix-identity read plus 256 records and EOF


def test_transcript_scanners_skip_json_for_records_without_usage_or_changes(monkeypatch):
    def unexpected_loads(_value):
        raise AssertionError("irrelevant transcript records must not be decoded into object trees")

    monkeypatch.setattr(session_files.json, "loads", unexpected_loads)
    claude_state = session_files.new_claude_transcript_scan_state()
    codex_state = session_files.new_codex_transcript_scan_state()

    session_files.update_claude_transcript_scan_state(claude_state, json.dumps({"type": "user", "message": "x" * 1000}))
    session_files.update_codex_transcript_scan_state(codex_state, json.dumps({"type": "response_item", "payload": {"text": "please run git add big-file " + ("x" * 1000)}}), None, False)

    assert claude_state["generated_tokens"] == 0
    assert codex_state["last_token_total"] is None


def test_codex_change_scan_does_not_parse_usage_only_records(tmp_path, monkeypatch):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "rollout.jsonl"
    usage_line = json.dumps({"payload": {"info": {"total_token_usage": {"output_tokens": 17}}}})
    shell_line = json.dumps({
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "git add tracked.txt", "workdir": str(tmp_path)}),
        },
    })
    transcript.write_text(usage_line + "\n" + shell_line + "\n", encoding="utf-8")
    real_loads = session_files.json.loads
    parsed = []

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(session_files.json, "loads", tracking_loads)

    assert session_files.scan_codex_transcript(transcript, str(tmp_path), include_patch_text=False) == {str(tmp_path / "tracked.txt"): {"M"}}
    assert all("total_token_usage" not in str(value) for value in parsed)


def test_scan_claude_transcript_waits_for_partial_lines_and_resets_after_replacement(tmp_path):
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    transcript = tmp_path / "c.jsonl"
    partial_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 5}, "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/a.py"}}]}})
    transcript.write_text(partial_line, encoding="utf-8")
    assert session_files.scan_claude_transcript_details(transcript)["changes"] == {}

    transcript.write_text(partial_line + "\n", encoding="utf-8")
    complete = session_files.scan_claude_transcript_details(transcript)
    assert complete["changes"] == {"/tmp/a.py": {"M"}}
    assert complete["usage"]["generated_tokens"] == 5

    replacement_line = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 3}, "content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/b.py"}}]}}) + "\n"
    transcript.write_text(replacement_line, encoding="utf-8")
    replacement = session_files.scan_claude_transcript_details(transcript)
    assert replacement["changes"] == {"/tmp/b.py": {"A"}}
    assert replacement["usage"]["generated_tokens"] == 3


def test_session_files_payload_merges_tool_attribution_with_git_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")
    untracked = repo / "new.txt"
    untracked.write_text("new\n", encoding="utf-8")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        '{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n*** Add File: new.txt\\n"}\n',
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["tracked.txt"]["repo"] == str(repo)
    assert by_path["tracked.txt"]["agent"] == "codex"
    assert by_path["tracked.txt"]["agents"] == ["codex"]  # C5: full agent list, scalar `agent` is an alias
    assert by_path["tracked.txt"]["size"] == (repo / "tracked.txt").stat().st_size  # C5: size for image-preview gating
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 1
    assert by_path["tracked.txt"]["diff_tracked"] is True
    # new.txt is untracked (never `git add`ed) -> "?", distinct from a staged/committed add "A".
    assert by_path["new.txt"]["status"] == "?"
    assert by_path["new.txt"]["added"] == 1
    assert by_path["new.txt"]["removed"] == 0
    assert by_path["new.txt"]["diff_tracked"] is False
    assert payload["repos"] == [{"repo": str(repo), "branch": "master", "count": 2, "touched_count": 2, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_keeps_transcript_paths_when_branch_is_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "branch", "-M", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("merged\n", encoding="utf-8")
    git(repo, "commit", "-am", "merged change")
    os.utime(tracked, (1400, 1400))

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    os.utime(rollout, (1500, 1500))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=2000)

    assert len(payload["files"]) == 1
    item = payload["files"][0]
    assert item["session"] == "s1"
    assert item["agents"] == ["codex"]
    assert item["agent"] == "codex"
    assert item["status"] == "T"
    assert item["repo"] == str(repo)
    assert item["path"] == "tracked.txt"
    assert item["abs_path"] == str(tracked)
    assert item["mtime"] == 1400
    assert item["source"] == "transcript"
    assert item["added"] is None
    assert item["removed"] is None
    assert item["uploaded"] is False

    assert payload["repos"] == []


def test_scan_codex_transcript_uses_exec_command_workdir_for_git_add(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({
                    "cmd": "git add rust/Cargo.toml -- vllm/envs.py",
                    "workdir": str(repo),
                }),
            },
        }) + "\n",
        encoding="utf-8",
    )

    changes = session_files.scan_codex_transcript(transcript, cwd="/elsewhere")

    assert changes[str(repo / "rust" / "Cargo.toml")] == {"M"}
    assert changes[str(repo / "vllm" / "envs.py")] == {"M"}


def test_scan_shell_command_changes_stops_git_add_at_shell_separator(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    changes = session_files.scan_shell_command_changes("git add tracked.txt && git commit -m done", str(repo))

    assert changes == {str(repo / "tracked.txt"): {"M"}}


def test_scan_shell_command_changes_tracks_cd_before_git_add(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    changes = session_files.scan_shell_command_changes("bash -lc 'cd repo && git add src/app.py'", str(tmp_path))

    assert changes == {str(repo / "src" / "app.py"): {"M"}}


def test_session_files_payload_uses_historical_codex_transcript_for_clean_pane_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    os.utime(tracked, (1400, 1400))
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({
                    "cmd": "git add tracked.txt",
                    "workdir": str(repo),
                }),
            },
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (1500, 1500))
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda cwd: transcript if cwd == str(repo) else None)

    payload = session_files.session_files_payload_for_info(info, hours=24, now=1600)

    assert len(payload["files"]) == 1
    item = payload["files"][0]
    assert item["status"] == "T"
    assert item["source"] == "transcript"
    assert item["repo"] == str(repo)
    assert item["path"] == "tracked.txt"
    assert item["agents"] == ["codex"]
    assert payload["repos"] == [{
        "repo": str(repo),
        "branch": "master",
        "count": 0,
        "touched_count": 1,
        "added": 0,
        "removed": 0,
        "from_ref": "default",
        "to_ref": "base",
        "error": "",
    }]


def test_historical_codex_transcript_prefers_recent_transcript_with_repo_changes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    mentioned = tmp_path / "mentioned.jsonl"
    mentioned.write_text(json.dumps({"message": f"look at {repo}"}) + "\n", encoding="utf-8")
    changed = tmp_path / "changed.jsonl"
    changed.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "git add changed.txt", "workdir": str(repo)}),
            },
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(mentioned, (2000, 2000))
    os.utime(changed, (1900, 1900))
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda cwd: mentioned)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [mentioned, changed])

    assert session_files.historical_codex_transcript_for_cwd(str(repo), cutoff=0) == changed


def test_historical_codex_candidates_ignore_home_cwd_that_contains_other_repos(tmp_path, monkeypatch):
    home = tmp_path / "home"
    other = home / "yolomux.dev8003"
    home.mkdir()
    other.mkdir()
    git(other, "init")
    git(other, "config", "user.email", "test@example.com")
    git(other, "config", "user.name", "Test User")
    tracked = other / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(other, "add", "tracked.txt")
    git(other, "commit", "-m", "base")
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "git add tracked.txt", "workdir": str(other)}),
            },
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (1500, 1500))
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
    monkeypatch.setattr(session_files, "session_workdir", lambda _session: home)
    monkeypatch.setattr(session_files, "find_recent_codex_transcript", lambda _cwd: None)
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", lambda: [transcript])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=1600)

    assert session_files.historical_codex_candidate_cwds(info) == []
    assert payload["files"] == []
    assert payload["repos"] == []


def test_file_mtime_or_fallback_preserves_epoch_mtime(tmp_path):
    path = tmp_path / "epoch.txt"
    path.write_text("old\n", encoding="utf-8")
    os.utime(path, (0, 0))

    assert session_files.file_mtime_or_fallback(path, fallback=1234) == 0


def test_file_mtime_or_fallback_uses_fallback_for_missing_path(tmp_path):
    assert session_files.file_mtime_or_fallback(tmp_path / "missing.txt", fallback=1234) == 1234


def test_session_files_payload_marks_statless_touched_path_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "README.md"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    (repo / "docs" / "specs").mkdir(parents=True)
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"msg":"*** Begin Patch\\n*** Update File: docs/specs/GUI.md\\n"}\n', encoding="utf-8")
    os.utime(transcript, (2000, 2000))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", transcript, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=2500)

    item = next(file for file in payload["files"] if file["path"] == "docs/specs/GUI.md")
    assert item["missing"] is True
    assert item["source"] == "transcript"


def test_session_files_payload_collects_multiple_agents_for_one_file(tmp_path):
    # C5: when both Claude and Codex touch the same file, the entry lists BOTH (no overwrite), so the UI
    # can render two agent icons.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tracked)}},
        ]}}) + "\n",
        encoding="utf-8",
    )
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(
        session="s1", panes=[], selected_pane=None,
        agents=[agent("claude", claude_path, repo), agent("codex", rollout, repo)],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert sorted(by_path["tracked.txt"]["agents"]) == ["claude", "codex"]
    assert by_path["tracked.txt"]["agent"] in {"claude", "codex"}  # scalar alias is just the first


def test_selected_session_rows_include_cross_session_agent_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    codex_path = tmp_path / "rollout.jsonl"
    codex_path.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    claude_path = tmp_path / "claude.jsonl"
    claude_path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(tracked)}},
        ]}}) + "\n",
        encoding="utf-8",
    )
    info1 = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", codex_path, repo, session="s1")])
    info2 = SessionInfo(session="s2", panes=[], selected_pane=None, agents=[agent("claude", claude_path, repo, session="s2")])

    payload, status = session_files.session_files_payload("s1", {"s1": info1, "s2": info2}, hours=24)

    assert status == 200
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["tracked.txt"]["session"] == "s1"
    assert sorted(by_path["tracked.txt"]["agents"]) == ["claude", "codex"]


def test_session_files_payload_includes_non_repo_transcript_files_without_counting_them(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")
    tmp_artifact = tmp_path / "scratch.txt"
    tmp_artifact.write_text("scratch\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        f'{{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n*** Add File: {tmp_artifact}\\n"}}\n',
        encoding="utf-8",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    by_abs = {item["abs_path"]: item for item in payload["files"]}
    assert str(tracked) in by_abs
    assert str(tmp_artifact) in by_abs
    assert by_abs[str(tmp_artifact)]["repo"] == ""
    assert by_abs[str(tmp_artifact)]["path"] == str(tmp_artifact)
    assert by_abs[str(tmp_artifact)]["added"] == 1
    assert by_abs[str(tmp_artifact)]["removed"] == 0
    assert by_abs[str(tmp_artifact)]["diff_tracked"] is False
    by_repo = {item["repo"]: item for item in payload["repos"]}
    assert by_repo[str(repo)]["added"] == 1
    assert by_repo[str(repo)]["removed"] == 1
    assert by_repo[""]["added"] == 0
    assert by_repo[""]["removed"] == 0


def test_session_files_payload_demotes_missing_transcript_to_per_agent_warning(tmp_path):
    # D2: a multi-agent session where ONE Codex pane has no discoverable transcript (AgentInfo.error set,
    # e.g. an inactive background pane) must NOT read as a session-level Differ failure. The valid agent's
    # changed file/repo must still render, and the missing-transcript message must be demoted to a
    # non-blocking per-agent warning (out of the blocking `errors` list the Differ renders as red rows).
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("two\n", encoding="utf-8")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")

    valid_codex = agent("codex", rollout, repo)
    missing_codex = AgentInfo(
        session="s1",
        kind="codex",
        pid=2,
        pane_target="%2",
        command="codex",
        cwd=str(tmp_path / "vllm-0.22.0"),
        status=None,
        session_id=None,
        transcript=None,
        error="codex transcript not found by process fd or cwd",
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[valid_codex, missing_codex])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    # The valid agent's changed file and its repo are still present.
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["tracked.txt"]["repo"] == str(repo)
    assert by_path["tracked.txt"]["status"] == "M"
    assert str(repo) in {repo_summary["repo"] for repo_summary in payload["repos"]}

    # The missing-transcript error is NOT a blocking/session-level error: the Differ renders payload["errors"]
    # as red failure rows, so the message must be absent there.
    assert "codex transcript not found by process fd or cwd" not in payload["errors"]
    assert payload["errors"] == []

    # It is surfaced as a non-blocking, per-agent warning instead.
    assert payload["warnings"] == [{
        "key": "diff.warning.agentDiscovery",
        "params": {"error": "codex transcript not found by process fd or cwd"},
        "fallback": "codex transcript not found by process fd or cwd",
    }]


def test_git_status_parses_renames_and_tab_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    old_name = "old\tname.txt"
    new_name = "new\tname.txt"
    (repo / old_name).write_text("one\n", encoding="utf-8")
    git(repo, "add", old_name)
    git(repo, "commit", "-m", "base")
    git(repo, "mv", old_name, new_name)

    with pinned_test_snapshot_runner(repo) as runner:
        statuses, error = session_files.git_name_status(repo, runner, "HEAD")
        counts = session_files.git_numstat(repo, runner, "HEAD")

    assert error == ""
    assert old_name not in statuses
    assert statuses[new_name] == "R"
    assert counts[new_name] == {"added": 0, "removed": 0}


def test_git_status_labels_untracked_question_distinct_from_staged_add_A(tmp_path):
    # An untracked working-tree file must read as "?" (git's own untracked marker), while a genuinely
    # staged add reads as "A", so the changes pane can tell "git is tracking this add" apart from
    # "this file isn't tracked yet".
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "loose.txt").write_text("loose\n", encoding="utf-8")  # untracked, never added

    with pinned_test_snapshot_runner(repo) as runner:
        statuses, error = session_files.git_name_status(repo, runner, "HEAD")

    assert error == ""
    assert statuses["staged.txt"] == "A"
    assert statuses["loose.txt"] == "?"


def test_session_files_payload_counts_staged_added_file_as_tracked_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    staged = repo / "staged.txt"
    staged.write_text("one\ntwo\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Add File: staged.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    item = {entry["path"]: entry for entry in payload["files"]}["staged.txt"]

    assert item["status"] == "A"
    assert item["added"] == 2
    assert item["removed"] == 0
    assert item["diff_tracked"] is True
    assert payload["repos"][0]["added"] == 2
    assert payload["repos"][0]["removed"] == 0


def test_session_files_payload_preserves_untracked_symlink_paths(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    target = repo / "lib" / "parsers" / "REASONING_CASES.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Reasoning\n", encoding="utf-8")
    git(repo, "add", "lib/parsers/REASONING_CASES.md")
    git(repo, "commit", "-m", "base")
    staged_link = repo / ".stage-v2" / "lib" / "parsers" / "REASONING_CASES.md"
    staged_link.parent.mkdir(parents=True)
    staged_link.symlink_to(target)
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    linux_path_flag = getattr(os, "O_PATH", 0)
    if linux_path_flag:
        darwin_symlink_flag = 1 << 29
        real_open = os.open

        def darwin_open(path, flags, *args, **kwargs):
            if flags & darwin_symlink_flag:
                assert not flags & filesystem_paths.nofollow_flag()
                flags = flags & ~darwin_symlink_flag | linux_path_flag | filesystem_paths.nofollow_flag()
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.delattr(os, "O_PATH")
        monkeypatch.setattr(os, "O_SYMLINK", darwin_symlink_flag, raising=False)
        monkeypatch.setattr(os, "open", darwin_open)

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert "lib/parsers/REASONING_CASES.md" not in by_path
    assert by_path[".stage-v2/lib/parsers/REASONING_CASES.md"]["status"] == "?"
    assert by_path[".stage-v2/lib/parsers/REASONING_CASES.md"]["abs_path"] == str(staged_link)


def test_git_numstat_parses_paths_with_tabs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    name = "tab\tpath.txt"
    (repo / name).write_text("one\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", "base")
    (repo / name).write_text("one\ntwo\n", encoding="utf-8")

    with pinned_test_snapshot_runner(repo) as runner:
        counts = session_files.git_numstat(repo, runner, "HEAD")

    assert counts[name] == {"added": 1, "removed": 0}


def test_session_files_payload_marks_generated_upload_names(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    upload = repo / "20260531-001-diagram.png"
    upload.write_bytes(b"png")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "20260531-001-diagram.png"
    assert payload["files"][0]["uploaded"] is True


def test_session_files_payload_counts_branch_commits_since_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "branch", "-M", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "feature")
    tracked.write_text("feature\n", encoding="utf-8")
    git(repo, "commit", "-am", "feature change")

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"*** Begin Patch\\n*** Update File: tracked.txt\\n"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())
    by_path = {item["path"]: item for item in payload["files"]}

    assert by_path["tracked.txt"]["status"] == "M"
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 1
    assert payload["repos"] == [{"repo": str(repo), "branch": "feature", "count": 1, "touched_count": 1, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_accepts_explicit_commit_refs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "two")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=older, to_ref=newer)

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["added"] == 1
    assert payload["files"][0]["removed"] == 1
    assert payload["from_ref"] == older
    assert payload["to_ref"] == newer
    assert payload["repos"][0]["behind"] == 0
    assert payload["repos"][0]["ahead"] == 1


def test_explicit_commit_refs_keep_rows_missing_from_the_current_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "deleted.txt").write_text("deleted\n", encoding="utf-8")
    (repo / "old.txt").write_text("rename\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "deleted.txt").unlink()
    git(repo, "mv", "old.txt", "renamed.txt")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "change paths")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()

    for checkout in (older, newer):
        git(repo, "checkout", checkout)
        snapshot = session_files.build_git_snapshot(repo, older, newer)
        assert snapshot["statuses"] == {"added.txt": "A", "deleted.txt": "D", "renamed.txt": "R"}
        assert set(snapshot["numstat"]) == set(snapshot["statuses"])


def test_session_files_payload_explicit_current_ref_includes_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    untracked = repo / "lib" / "llm" / "src" / "protocols" / "openai" / "chat_completions" / "qwen3_coder_v2.rs"
    untracked.parent.mkdir(parents=True)
    untracked.write_text("one\ntwo\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        '{"msg":"*** Begin Patch\\n*** Add File: lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs\\n"}\n',
        encoding="utf-8",
    )
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=older, to_ref="current")

    by_path = {item["path"]: item for item in payload["files"]}
    qwen_path = "lib/llm/src/protocols/openai/chat_completions/qwen3_coder_v2.rs"
    assert set(by_path) == {qwen_path, "tracked.txt"}
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["removed"] == 0
    assert by_path["tracked.txt"]["diff_tracked"] is True
    assert by_path[qwen_path]["status"] == "?"
    assert by_path[qwen_path]["added"] == 2
    assert by_path[qwen_path]["removed"] == 0
    assert by_path[qwen_path]["diff_tracked"] is False
    assert payload["repos"][0]["count"] == 2
    assert payload["repos"][0]["added"] == 1
    assert payload["repos"][0]["removed"] == 0


def test_git_numstat_does_not_use_copy_detection_for_plain_diff_counts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    source = repo / "source.txt"
    source.write_text("a\nb\nc\nd\ne\nf\ng\nh\n", encoding="utf-8")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "base")
    copied = repo / "copied.txt"
    copied.write_text("a\nb\nc\nd\ne\nf\ng\nchanged\nnew\n", encoding="utf-8")
    git(repo, "add", "copied.txt")

    with pinned_test_snapshot_runner(repo) as runner:
        counts = session_files.git_numstat(repo, runner, "HEAD")

    assert counts["copied.txt"] == {"added": 9, "removed": 0}


def test_session_files_payload_falls_back_when_requested_ref_is_unknown_in_repo(tmp_path):
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "one")
        tracked.write_text("two\n", encoding="utf-8")
    repo1_from = git(repo1, "rev-parse", "HEAD").stdout.strip()
    panes = []
    for index, repo in enumerate((repo1, repo2)):
        panes.append(
            PaneInfo(
                session="s1",
                window="0",
                pane=str(index),
                pane_id=f"%{index}",
                target=f"s1:0.{index}",
                current_path=str(repo),
                command="zsh",
                active=index == 0,
                window_active=True,
                title="",
                pid=11 + index,
            )
        )
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=repo1_from, to_ref="current")

    assert payload["errors"] == []
    assert {item["repo"] for item in payload["files"]} == {str(repo1), str(repo2)}
    assert all(item["path"] == "tracked.txt" for item in payload["files"])


def test_session_files_payload_applies_per_repo_refs_independently(tmp_path):
    # C6: a FROM/TO override scoped to repo1 must NOT change repo2's comparison — each repo reports its
    # own effective refs.
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        tracked = repo / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "one")
        tracked.write_text("two\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", "two")
        tracked.write_text("three\n", encoding="utf-8")
    repo1_from = git(repo1, "rev-parse", "HEAD~1").stdout.strip()
    panes = []
    for index, repo in enumerate((repo1, repo2)):
        panes.append(
            PaneInfo(
                session="s1", window="0", pane=str(index), pane_id=f"%{index}",
                target=f"s1:0.{index}", current_path=str(repo), command="zsh",
                active=index == 0, window_active=True, title="", pid=11 + index,
            )
        )
    info = SessionInfo(session="s1", panes=panes, selected_pane=panes[0], agents=[])

    payload = session_files.session_files_payload_for_info(
        info, hours=24, now=time.time(),
        repo_refs={str(repo1): {"from": repo1_from, "to": "current"}},
    )
    by_repo = {item["repo"]: item for item in payload["repos"]}

    assert by_repo[str(repo1)]["from_ref"] == repo1_from
    assert by_repo[str(repo1)]["to_ref"] == "current"
    assert by_repo[str(repo1)]["error"] == ""
    # repo2 had no override, so it stays on the default comparison and is not affected by repo1's SHA.
    assert by_repo[str(repo2)]["from_ref"] == "default"
    assert by_repo[str(repo2)]["to_ref"] == "base"
    assert payload["errors"] == []


def test_git_recent_refs_exposes_more_than_twenty_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    for index in range(25):
        tracked.write_text(f"{index}\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-m", f"commit {index}")
    git(repo, "branch", "main")
    git(repo, "branch", "same-head")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/topic", "HEAD")

    with pinned_test_snapshot_runner(repo) as runner:
        refs = session_files.git_recent_refs(repo, runner)
    head_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    head_short = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()

    assert refs[0]["ref"] == "HEAD"
    assert refs[0]["commit"] == head_commit
    assert refs[0]["short"].startswith(f"{head_short}/HEAD")
    assert "origin/main" in refs[0]["short"]
    assert "same-head" in refs[0]["short"]
    assert refs[0]["aliases"][0] == "HEAD"
    assert {"origin/main", "origin/topic", "main", "same-head"}.issubset(set(refs[0]["aliases"]))
    head_commit_ref = next(item for item in refs if item["ref"] == head_commit)
    assert head_commit_ref["short"].startswith(f"{head_short}/origin/main")
    assert {"origin/main", "origin/topic", "main", "same-head"}.issubset(set(head_commit_ref["aliases"]))
    assert refs[1]["ref"] == "current"
    assert len(refs) >= 27
    assert any(item["subject"] == "commit 0" for item in refs)


def test_session_files_payload_reports_invalid_ref_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "one")
    older = git(repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "two")
    newer = git(repo, "rev-parse", "HEAD").stdout.strip()
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), from_ref=newer, to_ref=older)

    assert payload["files"] == []
    assert payload["errors"] == []
    assert payload["repos"][0]["branch"] == "master"
    assert payload["repos"][0]["error_message"] == {
        "key": "diff.warning.refsFallback",
        "params": {"repo": "repo"},
        "fallback": "requested refs not found in this repo; showing default",
    }

    aggregate, status = session_files.session_files_payload(
        None,
        {"s1": info, "s2": info},
        hours=24,
        from_ref=newer,
        to_ref=older,
    )
    assert status == HTTPStatus.OK
    assert aggregate["repos"][0]["branch"] == "master"
    assert aggregate["repos"][0]["error_message"] == payload["repos"][0]["error_message"]


def test_diff_ref_issue_uses_one_structured_classifier():
    assert session_files.diff_ref_issue("unknown FROM ref: missing", "missing", "current") == {
        "key": "common.unknownFromRef",
        "params": {"ref": "missing"},
        "fallback": "unknown FROM ref: missing",
    }
    assert session_files.diff_ref_issue("unknown TO ref: future", "HEAD", "future") == {
        "key": "common.unknownToRef",
        "params": {"ref": "future"},
        "fallback": "unknown TO ref: future",
    }


def test_session_files_payload_uses_session_repo_without_ai_attribution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("working\n", encoding="utf-8")
    pane = PaneInfo(
        session="s1",
        window="0",
        pane="0",
        pane_id="%1",
        target="s1:0.0",
        current_path=str(repo),
        command="zsh",
        active=True,
        window_active=True,
        title="",
        pid=11,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["source"] == "git"
    assert payload["repos"] == [{"repo": str(repo), "branch": "master", "count": 1, "touched_count": 0, "added": 1, "removed": 1, "from_ref": "default", "to_ref": "base", "error": ""}]


def test_session_files_payload_does_not_invent_agent_for_repo_only_change(tmp_path):
    # C5: a git change with NO transcript attribution (the rollout never mentions this file) must render
    # zero agent icons — earlier the code invented a fallback to the session's agent, falsely implying
    # the agent touched a file the user changed by hand.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    tracked.write_text("working\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"msg":"no patch path here"}\n', encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("codex", rollout, repo)])

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"][0]["path"] == "tracked.txt"
    assert payload["files"][0]["agents"] == []
    assert payload["files"][0]["agent"] == ""
    assert payload["files"][0]["source"] == "git"


def test_untracked_line_counts_cache_by_identity_and_invalidate_on_change(tmp_path):
    """Repeated payload assembly must not re-read unchanged untracked files; a
    changed file (size/mtime) is re-read and re-counted."""
    target = tmp_path / "notes.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    session_files._UNTRACKED_LINE_COUNT_CACHE.clear()

    reads_before = session_files.RUNTIME_COUNTS["untracked_line_count_reads"]
    assert session_files.untracked_added_line_count(target) == 3
    assert session_files.untracked_added_line_count(target) == 3
    assert session_files.RUNTIME_COUNTS["untracked_line_count_reads"] == reads_before + 1

    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert session_files.untracked_added_line_count(target) == 4  # identity changed -> re-read
    assert session_files.RUNTIME_COUNTS["untracked_line_count_reads"] == reads_before + 2


def test_untracked_line_counts_invalidate_same_size_rewrite_with_restored_mtime(tmp_path):
    """A caller can preserve mtime while replacing content; ctime keeps the count correct."""
    target = tmp_path / "notes.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")
    session_files._UNTRACKED_LINE_COUNT_CACHE.clear()
    original_mtime_ns = target.stat().st_mtime_ns
    reads_before = session_files.RUNTIME_COUNTS["untracked_line_count_reads"]
    assert session_files.untracked_added_line_count(target) == 2

    # Preserve both byte length and mtime. The filesystem still changes ctime on
    # the replacement, so the cache must not reuse the old two-line result.
    target.write_text("one two\n", encoding="utf-8")
    os.utime(target, ns=(original_mtime_ns, original_mtime_ns))
    assert target.stat().st_mtime_ns == original_mtime_ns
    assert session_files.untracked_added_line_count(target) == 1
    assert session_files.RUNTIME_COUNTS["untracked_line_count_reads"] == reads_before + 2


def test_untracked_line_count_never_reopens_a_repointed_path(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("safe\n", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    secret = blocked / "id_rsa"
    secret.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    parked = tmp_path / "notes-authorized.txt"
    session_files._UNTRACKED_LINE_COUNT_CACHE.clear()
    repointed = False

    def swap_to_secret():
        nonlocal repointed
        if repointed:
            return
        repointed = True
        target.rename(parked)
        target.symlink_to(secret)

    class RepointAfterPin:
        def name_observed(self, operation, requested_path):
            del operation, requested_path

        def authority_pinned(self, operation, requested_path):
            if operation == "session_files_untracked_line_count" and requested_path == target:
                swap_to_secret()

    with filesystem_paths.observe_authorization(RepointAfterPin()):
        assert session_files.untracked_added_line_count(target) == 1

    assert repointed is True
    assert secret.read_text(encoding="utf-8") == "one\ntwo\nthree\nfour\nfive\n"


def test_session_file_entry_metadata_stays_bound_to_the_authorized_generation(tmp_path):
    target = tmp_path / "safe.txt"
    target.write_text("x", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    secret = blocked / "id_rsa"
    secret.write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE" * 4, encoding="utf-8")
    parked = tmp_path / "safe-authorized.txt"
    swapped = False

    class RepointAfterPin:
        def name_observed(self, operation, requested_path):
            del operation, requested_path

        def authority_pinned(self, operation, requested_path):
            nonlocal swapped
            if operation == "session_files_entry" and requested_path == target and not swapped:
                target.rename(parked)
                target.symlink_to(secret)
                swapped = True

    with filesystem_paths.observe_authorization(RepointAfterPin()):
        entry = session_files.session_file_entry("s1", [], "M", target, tmp_path, "git")

    assert swapped is True
    assert entry is not None
    assert entry["missing"] is False
    assert entry["size"] == 1
    assert entry["mtime"] == parked.stat().st_mtime
    assert entry["size"] != secret.stat().st_size


def _repository_snapshot_fixture(*, statuses=None, numstat=None, file_identities=None):
    return {
        "branch": "main",
        "statuses": dict(statuses or {}),
        "numstat": dict(numstat or {}),
        "file_identities": dict(file_identities or {path: None for path in (statuses or {})}),
        "selected_from": "",
        "selected_to": "",
        "status_error": "",
        "repo_error": "",
        "repo_error_message": {"key": "", "params": {}, "fallback": ""},
        "recent_refs": [
            {"ref": "HEAD", "short": "HEAD", "subject": "base commit"},
            {"ref": "current", "short": "current", "subject": "working tree"},
        ],
        "ahead_behind": {},
    }


def test_repository_snapshot_and_cache_never_publish_blocked_git_metadata(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    blocked_paths = [
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".ssh/id_rsa",
        ".aws/credentials",
    ]
    for relative_path in [*blocked_paths, "safe.txt"]:
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("base\n", encoding="utf-8")
    git(repo, "add", "--", *blocked_paths, "safe.txt")
    git(repo, "commit", "-m", "baseline")
    for relative_path in [*blocked_paths, "safe.txt"]:
        (repo / relative_path).write_text("changed\n", encoding="utf-8")

    snapshot = session_files.build_git_snapshot(repo)

    assert snapshot["statuses"] == {"safe.txt": "M"}
    assert snapshot["numstat"] == {"safe.txt": {"added": 1, "removed": 1}}
    for relative_path in blocked_paths:
        assert relative_path not in repr(snapshot)

    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    session_files._repository_snapshot_cache_last_pruned_at = 0.0
    cache_path = session_files.repository_snapshot_cache_path(repo, None, None, 17)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({
            "schema_version": session_files._REPOSITORY_SNAPSHOT_CACHE_SCHEMA_VERSION,
            "generation": 17,
            "verified_at": time.time(),
            "root_identity": [repo.stat().st_dev, repo.stat().st_ino],
            "snapshot": {"statuses": {blocked_paths[0]: "M"}, "numstat": {blocked_paths[0]: {"added": 9, "removed": 9}}},
        }),
        encoding="utf-8",
    )
    cached, hit = session_files.cached_repository_snapshot(repo, None, None, 17)

    assert hit is False
    assert cached == snapshot
    cache_record = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache_record["snapshot"]["statuses"] == {"safe.txt": "M"}
    assert cache_record["snapshot"]["numstat"] == {"safe.txt": {"added": 1, "removed": 1}}
    cached_again, hit_again = session_files.cached_repository_snapshot(repo, None, None, 17)
    assert hit_again is True
    assert cached_again == snapshot


def test_repository_snapshot_cache_key_includes_authorized_root_identity(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "old-only.txt").write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    session_files._repository_snapshot_cache_last_pruned_at = 0.0
    builds = []

    def build(path, _from_ref, _to_ref):
        names = sorted(child.name for child in path.iterdir())
        builds.append(names)
        return _repository_snapshot_fixture(statuses={name: "M" for name in names})

    first, first_hit = session_files.cached_repository_snapshot(repo, None, None, 9, build)
    old_repo = tmp_path / "old-repo"
    repo.rename(old_repo)
    repo.mkdir()
    (repo / "new-only.txt").write_text("new\n", encoding="utf-8")
    second, second_hit = session_files.cached_repository_snapshot(repo, None, None, 9, build)

    assert first_hit is False
    assert first["statuses"] == {"old-only.txt": "M"}
    assert second_hit is False
    assert second["statuses"] == {"new-only.txt": "M"}
    assert builds == [["old-only.txt"], ["new-only.txt"]]


def test_cached_repository_snapshot_stays_authorized_through_session_row_render(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("safe\n", encoding="utf-8")
    parked = tmp_path / "repo-authorized"
    replacement_bytes = "BLOCKED_SENTINEL_DO_NOT_EXPOSE" * 4
    snapshot = _repository_snapshot_fixture(
        statuses={"tracked.txt": "M"},
        numstat={"tracked.txt": {"added": 1, "removed": 0}},
        file_identities={"tracked.txt": [tracked.stat().st_dev, tracked.stat().st_ino]},
    )
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    session_files.cached_repository_snapshot(repo, None, None, 9, lambda *_args: snapshot)
    swapped = False

    def cached_then_replace(repo_path, from_ref, to_ref):
        nonlocal swapped
        cached, hit = session_files.cached_repository_snapshot(repo_path, from_ref, to_ref, 9, lambda *_args: snapshot)
        assert hit is True
        repo.rename(parked)
        repo.mkdir()
        (repo / "tracked.txt").write_text(replacement_bytes, encoding="utf-8")
        swapped = True
        return cached

    pane = PaneInfo(
        session="s1", window="0", pane="0", pane_id="%1", target="s1:0.0",
        current_path=str(repo), command="bash", active=True, window_active=True, title="", pid=1,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(
        info,
        hours=24,
        now=time.time(),
        git_snapshot_provider=cached_then_replace,
    )

    assert swapped is True
    assert len(payload["files"]) == 1
    assert payload["files"][0]["size"] == (parked / "tracked.txt").stat().st_size
    assert payload["files"][0]["size"] != len(replacement_bytes)
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in repr(payload)


def test_cached_repository_snapshot_refuses_a_replaced_child_before_row_render(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("safe\n", encoding="utf-8")
    parked = repo / "tracked-authorized.txt"
    replacement_bytes = "BLOCKED_SENTINEL_DO_NOT_EXPOSE" * 4
    snapshot = _repository_snapshot_fixture(
        statuses={"tracked.txt": "M"},
        numstat={"tracked.txt": {"added": 1, "removed": 0}},
        file_identities={"tracked.txt": [tracked.stat().st_dev, tracked.stat().st_ino]},
    )
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    session_files.cached_repository_snapshot(repo, None, None, 9, lambda *_args: snapshot)

    def cached_then_replace(repo_path, from_ref, to_ref):
        cached, hit = session_files.cached_repository_snapshot(repo_path, from_ref, to_ref, 9, lambda *_args: snapshot)
        assert hit is True
        tracked.rename(parked)
        tracked.write_text(replacement_bytes, encoding="utf-8")
        return cached

    pane = PaneInfo(
        session="s1", window="0", pane="0", pane_id="%1", target="s1:0.0",
        current_path=str(repo), command="bash", active=True, window_active=True, title="", pid=1,
    )
    info = SessionInfo(session="s1", panes=[pane], selected_pane=pane, agents=[])

    payload = session_files.session_files_payload_for_info(
        info,
        hours=24,
        now=time.time(),
        git_snapshot_provider=cached_then_replace,
    )

    assert payload["files"] == []
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in repr(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda snapshot: snapshot["statuses"].__setitem__("tracked.txt", "BLOCKED_SENTINEL_DO_NOT_EXPOSE"),
        lambda snapshot: snapshot["statuses"].__setitem__("tracked.txt", []),
        lambda snapshot: snapshot.pop("numstat"),
        lambda snapshot: snapshot["numstat"]["tracked.txt"].__setitem__("added", -1),
        lambda snapshot: snapshot.__setitem__("recent_refs", ["BLOCKED_SENTINEL_DO_NOT_EXPOSE"]),
        lambda snapshot: snapshot["repo_error_message"].__setitem__("params", "BLOCKED_SENTINEL_DO_NOT_EXPOSE"),
        lambda snapshot: snapshot.__setitem__("unknown", "BLOCKED_SENTINEL_DO_NOT_EXPOSE"),
    ],
    ids=["status", "unhashable-status", "missing-field", "negative-numstat", "recent-ref", "message", "unknown-field"],
)
def test_repository_snapshot_cache_rejects_malformed_current_schema(tmp_path, monkeypatch, mutate):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    canonical = _repository_snapshot_fixture(
        statuses={"tracked.txt": "M"},
        numstat={"tracked.txt": {"added": 1, "removed": 0}},
    )
    malformed = copy_module.deepcopy(canonical)
    mutate(malformed)
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    session_files._repository_snapshot_cache_last_pruned_at = 0.0
    cache_path = session_files.repository_snapshot_cache_path(repo, None, None, 17)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({
            "schema_version": session_files._REPOSITORY_SNAPSHOT_CACHE_SCHEMA_VERSION,
            "generation": 17,
            "verified_at": time.time(),
            "root_identity": [repo.stat().st_dev, repo.stat().st_ino],
            "snapshot": malformed,
        }),
        encoding="utf-8",
    )
    builds = []

    def build(*_args):
        builds.append(1)
        return canonical

    result, hit = session_files.cached_repository_snapshot(repo, None, None, 17, build)

    assert hit is False
    assert builds == [1]
    assert result == canonical
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in cache_path.read_text(encoding="utf-8")


def test_pinned_git_index_preserves_racy_clean_detection_for_same_stat_rewrite(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    target = repo / "tracked.txt"
    target.write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "baseline")
    entry_mtime_ns = target.stat().st_mtime_ns
    index_path = repo / ".git" / "index"
    os.utime(index_path, ns=(entry_mtime_ns, entry_mtime_ns))
    target.write_text("two\n", encoding="utf-8")
    os.utime(target, ns=(entry_mtime_ns, entry_mtime_ns))

    snapshot = session_files.build_git_snapshot(repo)

    assert snapshot["statuses"] == {"tracked.txt": "M"}
    assert snapshot["numstat"] == {"tracked.txt": {"added": 1, "removed": 1}}


def test_repository_snapshot_drops_an_admitted_name_repointed_to_a_blocked_file(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    target = repo / "safe.txt"
    target.write_text("base\n", encoding="utf-8")
    git(repo, "add", "safe.txt")
    git(repo, "commit", "-m", "baseline")
    target.write_text("changed\n", encoding="utf-8")
    secret = repo / ".ssh" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE\n", encoding="utf-8")
    real_git_numstat = session_files.git_numstat
    swapped = False

    def numstat_then_swap(*args, **kwargs):
        nonlocal swapped
        result = real_git_numstat(*args, **kwargs)
        target.unlink()
        target.symlink_to(secret)
        swapped = True
        return result

    monkeypatch.setattr(session_files, "git_numstat", numstat_then_swap)

    snapshot = session_files.build_git_snapshot(repo)

    assert swapped is True
    assert snapshot["statuses"] == {}
    assert snapshot["numstat"] == {}
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in repr(snapshot)


def test_concurrent_views_share_one_git_identity_run(tmp_path, monkeypatch):
    """Six concurrent session-files views of one repo must pay the expensive
    `git status --untracked-files=all` signature ONCE (in-flight single-flight),
    while sequential calls still recompute so freshness is never delayed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")

    real_identity = session_files.git_snapshot_identity
    calls = []
    owner_entered = threading_module.Event()
    allow_finish = threading_module.Event()

    def gated_counted_identity(path, from_ref=None, to_ref=None):
        calls.append(str(path))
        owner_entered.set()
        # Hold the in-flight window open until the test has parked every other
        # caller on the shared future, making the coalesce deterministic.
        assert allow_finish.wait(timeout=10)
        return real_identity(path, from_ref, to_ref)

    monkeypatch.setattr(session_files, "git_snapshot_identity", gated_counted_identity)
    webapp = TmuxWebtermApp(["one"])
    try:
        results = []

        errors = []
        lined_up = threading_module.Barrier(6, timeout=10)
        identity_gate = threading_module.Barrier(6, timeout=10)
        identity_gate_lock = threading_module.Lock()
        identity_gate_calls = 0
        real_shared_identity = webapp.shared_git_identity

        def synchronized_shared_identity(*args, **kwargs):
            # Every caller reaches the same handoff before the owner begins its expensive
            # identity read, replacing the former timing guess with an actual coalesce fence.
            nonlocal identity_gate_calls
            with identity_gate_lock:
                identity_gate_calls += 1
                use_gate = identity_gate_calls <= 6
            if use_gate:
                identity_gate.wait()
            return real_shared_identity(*args, **kwargs)

        monkeypatch.setattr(webapp, "shared_git_identity", synchronized_shared_identity)

        def view():
            try:
                lined_up.wait()  # all six are running before any calls: no startup latency race
                results.append(webapp.shared_session_files_git_snapshot(repo, None, None))
            except BaseException as error:  # surface thread failures in the assertion
                errors.append(repr(error))

        threads = [threading_module.Thread(target=view) for _ in range(6)]
        for thread in threads:
            thread.start()
        assert owner_entered.wait(timeout=10)
        allow_finish.set()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == [], errors
        assert len(results) == 6 and all(isinstance(item, dict) for item in results)
        # Exactly TWO identity runs for a six-view cold burst: one pre-build
        # signature shared by all six callers (the single-flight under test) plus
        # the snapshot owner's post-build freshness re-validation. Before the
        # single-flight this burst paid seven (six pre + one post).
        assert len(calls) == 2, f"six concurrent views ran {len(calls)} identity computations"

        # A sequential follow-up recomputes its own pre-build signature (no
        # staleness window) and hits the cached snapshot record (no post run).
        webapp.shared_session_files_git_snapshot(repo, None, None)
        assert len(calls) == 3
    finally:
        webapp.close() if hasattr(webapp, "close") else None


def test_session_files_runtime_counters_cover_the_bounded_accounting_dimensions(tmp_path):
    """The accounting snapshot exposes cumulative (monotonic) work counters for
    git spawns per verb, transcript-catalog traversal, append bytes parsed, and
    untracked stat/line-count work, without a second profiler."""

    before = session_files.session_files_runtime_counters()
    for key in ("append_bytes_parsed", "untracked_line_count_hits", "untracked_line_count_reads", "git_commands", "transcript_catalog"):
        assert key in before

    # untracked read then identity hit
    target = tmp_path / "untracked.py"
    target.write_text("one\ntwo\n", encoding="utf-8")
    assert session_files.untracked_added_line_count(target) == 2
    assert session_files.untracked_added_line_count(target) == 2
    # append bytes
    state: dict[str, object] = {}
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"a":1}\n', encoding="utf-8")
    session_files.scan_transcript_append(transcript, 0, lambda line: None, state)
    # one git spawn and one catalog traversal
    common_module.git(["version"], cwd=str(tmp_path))
    sessions_module._cataloged_jsonl_files(tmp_path)

    after = session_files.session_files_runtime_counters()
    assert after["untracked_line_count_reads"] == before["untracked_line_count_reads"] + 1
    assert after["untracked_line_count_hits"] == before["untracked_line_count_hits"] + 1
    assert after["append_bytes_parsed"] == before["append_bytes_parsed"] + len('{"a":1}\n')
    assert after["git_commands"].get("version", 0) == before["git_commands"].get("version", 0) + 1
    assert after["transcript_catalog"]["calls"] == before["transcript_catalog"]["calls"] + 1
    assert after["transcript_catalog"]["dirs_statted"] > before["transcript_catalog"]["dirs_statted"]


def test_repo_state_record_warm_hit_runs_zero_git_commands_and_dirty_event_recomputes(tmp_path, monkeypatch):
    """Repository-state record (native-watcher backed): with the watcher healthy
    and no event for the repo, a warm identity request runs ZERO Git commands;
    a worktree or .git-metadata event bumps the dirty generation and exactly the
    next request recomputes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")

    monkeypatch.setattr(TmuxWebtermApp, "discover_and_start", lambda self: None, raising=False)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = TmuxWebtermApp(["1"])
    try:
        resolved_repo = repo.resolve()
        # Simulate a healthy native watcher covering this repo.
        record = webapp.client_watch_service.event_watcher_record
        record.filesystem_healthy = True
        record.filesystem_roots = (str(tmp_path.resolve()),)
        record.filesystem_watch_paths = (str(tmp_path.resolve()),)

        identity_one, status_one = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_one == "computed"

        # Warm: identical result, zero additional git spawns.
        spawns_before = dict(common_module.GIT_COMMAND_COUNTS)
        identity_two, status_two = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_two == "watcher-cached"
        assert identity_two == identity_one
        assert dict(common_module.GIT_COMMAND_COUNTS) == spawns_before

        # A worktree event dirties the record; the next request recomputes and
        # sees the change immediately.
        (repo / "one.py").write_text("x = 2\n", encoding="utf-8")
        webapp.mark_repo_state_dirty([resolved_repo / "one.py"])
        identity_three, status_three = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_three == "computed"
        assert identity_three != identity_one
        # One dirty event causes AT MOST one follow-up: the next request is
        # served from the record again with zero Git commands.
        spawns_after_recompute = dict(common_module.GIT_COMMAND_COUNTS)
        _identity, status_again = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_again == "watcher-cached"
        assert dict(common_module.GIT_COMMAND_COUNTS) == spawns_after_recompute

        # A pure commit (only .git metadata changes) also dirties the record.
        git(repo, "add", "one.py")
        git(repo, "commit", "-m", "second")
        webapp.mark_repo_state_dirty([resolved_repo / ".git" / "HEAD"])
        identity_four, status_four = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_four == "computed"
        assert identity_four != identity_three

        # Watcher unhealthy -> fail open: always compute.
        record.filesystem_healthy = False
        _identity, status_five = webapp.shared_git_identity(resolved_repo, None, None)
        assert status_five == "computed"
    finally:
        webapp.control_server.stop()


def test_watchd_git_metadata_event_filter_admits_no_git_internal_at_all(tmp_path):
    """No ``.git`` path is admitted, and the floor holds without configuration.

    This test previously pinned the opposite: HEAD, index, packed-refs, config,
    MERGE_HEAD and refs/** were admitted so a branch change could be delivered
    through them.  That made an ignored pathname the transport signal for the
    branch/status UI.  ``.git`` is now ignored like any other ignored directory,
    and because version-control metadata is never user content it stays excluded
    even though this configuration declares no ``skip_dirs`` at all.
    """

    service = object.__new__(watchd.PersistentWatchService)
    configuration = EffectiveWatchConfiguration(
        configured_roots=(str(tmp_path),),
        watch_paths=(str(tmp_path),),
    )
    assert configuration.skip_dirs == ()
    git_dir = tmp_path / "repo" / ".git"
    for candidate in (
        git_dir / "HEAD",
        git_dir / "index",
        git_dir / "packed-refs",
        git_dir / "config",
        git_dir / "MERGE_HEAD",
        git_dir / "refs" / "heads" / "main",
        git_dir / "objects" / "ab" / "cdef",
        git_dir / "logs" / "HEAD",
        Path("/outside/.git/HEAD"),
    ):
        assert service._path_allowed(candidate, configuration) is False, candidate
    assert service._path_allowed(tmp_path / "repo" / "src" / "main.py", configuration) is True


def _new_git_verbs(before):
    return {
        verb: common_module.GIT_COMMAND_COUNTS[verb] - before.get(verb, 0)
        for verb in common_module.GIT_COMMAND_COUNTS
        if common_module.GIT_COMMAND_COUNTS[verb] - before.get(verb, 0) > 0
    }


def test_session_files_cache_key_uses_watcher_generation_and_skips_git_identity(tmp_path, monkeypatch):
    """DOIT.offload-web-refreshers item 4, step 3: when the native watcher covers a repo the cache
    KEY carries its dirty-generation int, so the heavy `git status`/`for-each-ref` identity commands
    do NOT run on the key path. An unhealthy watcher falls back to the git-spawn identity."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")
    resolved_repo = repo.resolve()
    real_git_snapshot_identity = session_files.git_snapshot_identity
    identity_calls = 0

    def counted_git_snapshot_identity(*args, **kwargs):
        nonlocal identity_calls
        identity_calls += 1
        return real_git_snapshot_identity(*args, **kwargs)

    monkeypatch.setattr(session_files, "git_snapshot_identity", counted_git_snapshot_identity)

    monkeypatch.setattr(TmuxWebtermApp, "discover_and_start", lambda self: None, raising=False)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = TmuxWebtermApp(["1"])
    try:
        pane = PaneInfo(
            session="1", window="0", pane="0", pane_id="%1", target="1:0.0",
            current_path=str(resolved_repo), command="zsh", active=True, window_active=True, title="", pid=11,
        )
        infos = {"1": SessionInfo(session="1", panes=[pane], selected_pane=pane, agents=[])}

        record = webapp.client_watch_service.event_watcher_record
        record.filesystem_healthy = True
        record.filesystem_roots = (str(tmp_path.resolve()),)
        record.filesystem_watch_paths = (str(tmp_path.resolve()),)

        original_shared_git_identity = webapp.shared_git_identity
        monkeypatch.setattr(
            webapp,
            "shared_git_identity",
            lambda *_args: pytest.fail("healthy watcher cache key must not request a Git identity"),
        )
        key_one = webapp.session_files_cache_key("payload", infos, "1", 24.0, None, None, None)
        monkeypatch.setattr(webapp, "shared_git_identity", original_shared_git_identity)

        repo_signatures = dict(key_one[-1])
        assert len(repo_signatures) == 1
        (repo_text, signature), = repo_signatures.items()
        assert isinstance(signature, int)
        assert signature == webapp.repo_dirty_generation(repo_text)
        # The int must NOT be misread as a reusable git identity by the snapshot-provider bridge.
        assert webapp.session_files_git_identity_for_cache_key(key_one, resolved_repo) is None

        # A watched change bumps the generation, so the cache key changes with still-no identity spawn.
        (repo / "one.py").write_text("x = 2\n", encoding="utf-8")
        webapp.mark_repo_state_dirty([resolved_repo / "one.py"])
        key_two = webapp.session_files_cache_key("payload", infos, "1", 24.0, None, None, None)
        assert key_two != key_one
        assert identity_calls == 0

        # The bounded TTL is the no-watcher backstop; key construction still never spawns Git.
        record.filesystem_healthy = False
        key_unhealthy = webapp.session_files_cache_key("payload", infos, "1", 24.0, None, None, None)
        assert key_unhealthy == key_two
        assert identity_calls == 0
    finally:
        webapp.control_server.stop()


def test_session_files_cache_key_canonicalizes_ref_override_paths(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    monkeypatch.setattr(TmuxWebtermApp, "discover_and_start", lambda self: None, raising=False)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = TmuxWebtermApp(["1"])
    try:
        pane = PaneInfo(
            session="1", window="0", pane="0", pane_id="%1", target="1:0.0",
            current_path=str(repo), command="zsh", active=True, window_active=True, title="", pid=11,
        )
        infos = {"1": SessionInfo(session="1", panes=[pane], selected_pane=pane, agents=[])}
        record = webapp.client_watch_service.event_watcher_record
        record.filesystem_healthy = True
        record.filesystem_roots = (str(tmp_path.resolve()),)
        record.filesystem_watch_paths = (str(tmp_path.resolve()),)
        canonical_refs = {str(repo): {"from": "HEAD~1", "to": "HEAD"}}
        alias_refs = {str(alias): {"from": "HEAD~1", "to": "HEAD"}}
        assert webapp.session_files_cache_key("payload", infos, "1", 24.0, None, None, canonical_refs) == webapp.session_files_cache_key(
            "payload", infos, "1", 24.0, None, None, alias_refs,
        )
    finally:
        webapp.control_server.stop()


def test_session_files_view_coalesce_identity_is_stable_and_source_scoped(tmp_path, monkeypatch):
    """Two apps sharing one batchd socket + disk-cache dir must derive the SAME product coalesce_key
    for the same view (cross-port single execution), and a source-generation change must move it."""
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp_a = TmuxWebtermApp([])
    webapp_b = TmuxWebtermApp([])
    try:
        stable_key = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "1", 24.0, "", "", "", (), ())
        key_gen_x = (*stable_key[:-1], (("repoX", 1),))
        key_gen_y = (*stable_key[:-1], (("repoX", 2),))
        coalesce_a, generation_a = webapp_a.session_files_view_coalesce_identity(key_gen_x)
        coalesce_b, generation_b = webapp_b.session_files_view_coalesce_identity(key_gen_x)
        assert coalesce_a == coalesce_b
        assert generation_a == generation_b
        coalesce_y, generation_y = webapp_a.session_files_view_coalesce_identity(key_gen_y)
        assert coalesce_y != coalesce_a
    finally:
        webapp_a.control_server.stop()
        webapp_b.control_server.stop()


# --- Differ: a deleted file is diff content; a retired worktree is one fact ----------------------
# Keiven opened Differ on `yo7771` and got ~900 identical `path not found` rows for a /tmp worktree
# deleted the day before, which pushed the five repos that still existed off the pane.  The single
# distinction these tests pin is `session_repository_resolution`: an EXISTING repo holding a missing
# leaf keeps that leaf as an ordinary deleted child, while a root that is entirely gone collapses to
# one entry that states its own count.

def _claude_transcript_touching(path, targets):
    """Write a Claude transcript whose Edit calls name ``targets``."""

    path.write_text("".join(
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": str(target)}}]},
        }) + "\n"
        for target in targets
    ), encoding="utf-8")
    return path


def _differ_payload(tmp_path, cwd, targets, session="s1"):
    transcript = _claude_transcript_touching(tmp_path / f"{session}-transcript.jsonl", targets)
    info = SessionInfo(session=session, panes=[], selected_pane=None, agents=[agent("claude", transcript, cwd, session=session)])
    return session_files.session_files_payload_for_info(info, hours=24, now=time.time())


def _missing_repo_payloads(payload):
    return [repo for repo in payload["repos"] if repo.get("missing") is True]


def test_deleted_file_in_a_live_repo_stays_a_visible_deleted_child(tmp_path):
    """An existing repo holding a missing file lists that file as a deleted child, not a warning."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "kept.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "gone.py").write_text("y = 2\n", encoding="utf-8")
    git(repo, "add", "kept.py", "gone.py")
    git(repo, "commit", "-m", "seed")
    (repo / "gone.py").unlink()

    payload = _differ_payload(tmp_path, repo, [repo / "gone.py"])

    rows = {entry["path"]: entry for entry in payload["files"]}
    assert "gone.py" in rows, payload["files"]
    assert rows["gone.py"]["status"] == "D"
    assert rows["gone.py"]["repo"] == str(repo)
    assert rows["gone.py"]["missing"] is True
    assert _missing_repo_payloads(payload) == []


def test_every_deleted_file_in_a_live_repo_stays_visible(tmp_path):
    """Many deletions do not collapse: the repo exists, so each deleted file is its own row."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    deleted = [repo / "src" / f"gone_{index}.py" for index in range(12)]
    (repo / "src").mkdir()
    for path in deleted:
        path.write_text("x = 1\n", encoding="utf-8")
    (repo / "live.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "seed")
    for path in deleted:
        path.unlink()
    (repo / "live.py").write_text("x = 2\n", encoding="utf-8")

    payload = _differ_payload(tmp_path, repo, [*deleted, repo / "live.py"])

    statuses = {entry["path"]: entry["status"] for entry in payload["files"]}
    assert sorted(path for path, status in statuses.items() if status == "D") == sorted(
        f"src/gone_{index}.py" for index in range(12)
    ), statuses
    assert statuses["live.py"] == "M"
    assert _missing_repo_payloads(payload) == []


def test_absent_worktree_collapses_to_one_root_entry(tmp_path):
    """A root that no longer exists reads as ONE entry carrying its count, not one row per file."""

    retired = tmp_path / "yo7771-browser-p0-candidate"
    (retired / "tests").mkdir(parents=True)
    (retired / "yolomux_lib").mkdir()
    remembered = [retired / "tests" / f"test_{index}.py" for index in range(120)]
    remembered.append(retired / "yolomux_lib" / "app.py")
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", remembered)
    assert str(remembered[0]) in session_files.scan_claude_transcript(transcript, str(retired))
    for path in remembered:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    for path in remembered:
        path.unlink()
    (retired / "tests").rmdir()
    (retired / "yolomux_lib").rmdir()
    retired.rmdir()

    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, retired)])
    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"] == []
    missing = _missing_repo_payloads(payload)
    assert len(missing) == 1, payload["repos"]
    assert missing[0]["repo"] == str(retired)
    assert missing[0]["touched_count"] == len(remembered)
    assert payload["warnings"] == []


def test_absent_root_does_not_hide_the_repos_that_still_exist(tmp_path):
    """One retired worktree may not cost the user the repos they opened Differ to see."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "live.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "live.py")
    git(repo, "commit", "-m", "seed")
    (repo / "live.py").write_text("x = 2\n", encoding="utf-8")
    retired = tmp_path / "retired"
    retired.mkdir()
    remembered = [retired / f"gone_{index}.py" for index in range(40)]
    live_transcript = _claude_transcript_touching(tmp_path / "live.jsonl", [repo / "live.py"])
    retired_transcript = _claude_transcript_touching(tmp_path / "retired.jsonl", remembered)
    assert str(remembered[0]) in session_files.scan_claude_transcript(retired_transcript, str(retired))
    retired.rmdir()

    info = SessionInfo(
        session="s1",
        panes=[],
        selected_pane=None,
        agents=[agent("claude", live_transcript, repo), agent("claude", retired_transcript, retired)],
    )
    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert [entry["path"] for entry in payload["files"]] == ["live.py"]
    assert [item["repo"] for item in payload["repos"] if not item.get("missing")] == [str(repo)]
    missing = _missing_repo_payloads(payload)
    assert len(missing) == 1, payload["repos"]
    assert missing[0]["repo"] == str(retired)
    assert missing[0]["touched_count"] == len(remembered)


def test_unexpanded_template_paths_are_refused_at_the_recording_boundary(tmp_path):
    """`${d}` and `$(...)` name no file, so they never become a change path."""

    assert session_files.resolved_change_path("${d}/reply-prose.out", str(tmp_path)) is None
    assert session_files.resolved_change_path("$(pwd)/tmux.log", str(tmp_path)) is None
    assert session_files.resolved_change_path(f"{tmp_path}/${{d}}/tell-goal.out", None) is None
    assert session_files.resolved_change_path("reply-prose.out", str(tmp_path)) == tmp_path / "reply-prose.out"


def test_unexpanded_template_path_produces_no_repo_or_file_row(tmp_path):
    """The transcript path Keiven saw yields no row and no invented absent root."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "live.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "live.py")
    git(repo, "commit", "-m", "seed")
    (repo / "live.py").write_text("x = 2\n", encoding="utf-8")

    payload = _differ_payload(tmp_path, repo, ["${d}/reply-prose.out", "${d}/tmux.log", repo / "live.py"])

    assert [entry["path"] for entry in payload["files"]] == ["live.py"]
    assert all("${d}" not in entry["abs_path"] for entry in payload["files"]), payload["files"]
    assert _missing_repo_payloads(payload) == []


def test_file_deleted_between_transcript_scan_and_snapshot_is_a_deleted_child(tmp_path):
    """A file that disappears after it was remembered stays attached to its still-existing repo."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "seed.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "seed.py")
    git(repo, "commit", "-m", "seed")
    scratch = repo / "scratch.py"
    scratch.write_text("x = 1\n", encoding="utf-8")
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", [scratch])
    assert str(scratch) in session_files.scan_claude_transcript(transcript, str(repo))
    scratch.unlink()

    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, repo)])
    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    rows = {entry["path"]: entry for entry in payload["files"]}
    assert rows["scratch.py"]["repo"] == str(repo)
    assert rows["scratch.py"]["missing"] is True
    assert _missing_repo_payloads(payload) == []


def test_absent_root_counts_do_not_survive_repeated_or_concurrent_builds(tmp_path):
    """Absent-root counting is per-build state: repeats do not accumulate and peers do not mix."""

    def retired_case(name, count):
        retired = tmp_path / name
        retired.mkdir()
        remembered = [retired / f"gone_{index}.py" for index in range(count)]
        transcript = _claude_transcript_touching(tmp_path / f"{name}.jsonl", remembered)
        assert str(remembered[0]) in session_files.scan_claude_transcript(transcript, str(retired))
        retired.rmdir()
        return retired, SessionInfo(
            session=name,
            panes=[],
            selected_pane=None,
            agents=[agent("claude", transcript, retired, session=name)],
        )

    retired_a, info_a = retired_case("alpha", 7)
    retired_b, info_b = retired_case("beta", 3)

    def build(info):
        return session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    repeats = [_missing_repo_payloads(build(info_a)) for _ in range(3)]
    assert [rows[0]["touched_count"] for rows in repeats] == [7, 7, 7], repeats

    results = {}
    threads = [
        threading_module.Thread(target=lambda name=name, info=info: results.__setitem__(name, build(info)))
        for name, info in (("alpha", info_a), ("beta", info_b))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [row["repo"] for row in _missing_repo_payloads(results["alpha"])] == [str(retired_a)]
    assert [row["repo"] for row in _missing_repo_payloads(results["beta"])] == [str(retired_b)]
    assert _missing_repo_payloads(results["alpha"])[0]["touched_count"] == 7
    assert _missing_repo_payloads(results["beta"])[0]["touched_count"] == 3


def test_deleted_nested_directory_does_not_retire_its_live_repository(tmp_path):
    """A repo stays a repo when a nested directory is deleted along with the file inside it.

    `git_root_for_path` probes only the missing file's DIRECT parent. When `nested/` went with
    `nested/deep.txt`, that probe failed and the classifier declared the whole repository absent,
    collapsing a live repo -- and every real change in it -- into one gone row.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    nested = repo / "nested"
    nested.mkdir()
    (nested / "deep.txt").write_text("x\n", encoding="utf-8")
    (repo / "live.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "seed")
    (repo / "live.py").write_text("x = 2\n", encoding="utf-8")
    (nested / "deep.txt").unlink()
    nested.rmdir()

    payload = _differ_payload(tmp_path, repo, [nested / "deep.txt", repo / "live.py"])

    assert session_files.session_repository_resolution(nested / "deep.txt", [str(repo)]) == (str(repo), "")
    rows = {entry["path"]: entry for entry in payload["files"]}
    assert rows["nested/deep.txt"]["repo"] == str(repo)
    assert rows["nested/deep.txt"]["status"] == "D"
    assert rows["live.py"]["status"] == "M"
    assert _missing_repo_payloads(payload) == []


def test_duplicate_session_candidates_do_not_inflate_the_gone_repo_count(tmp_path):
    """The same cwd arrives through the agent, the selected pane and the pane list exactly once."""

    retired = tmp_path / "retired"
    retired.mkdir()
    remembered = [retired / f"gone_{index}.py" for index in range(5)]
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", remembered)
    assert str(remembered[0]) in session_files.scan_claude_transcript(transcript, str(retired))
    retired.rmdir()
    pane = PaneInfo(
        session="s1", window="0", pane="0", pane_id="%1", target="s1:0.0",
        current_path=str(retired), command="zsh", active=True, window_active=True, title="", pid=11,
    )
    info = SessionInfo(
        session="s1",
        panes=[pane, pane],
        selected_pane=pane,
        agents=[agent("claude", transcript, retired)],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    missing = _missing_repo_payloads(payload)
    assert len(missing) == 1, payload["repos"]
    assert missing[0]["repo"] == str(retired)
    assert missing[0]["touched_count"] == len(remembered)


def test_missing_file_outside_any_repo_stays_one_deleted_file(tmp_path):
    """A remembered path under no repository is one deleted file, not proof of a retired repo.

    The session ALSO has a genuinely retired root, and the orphan lives outside it. Without that,
    there is no absent candidate for the orphan to be misattributed to, and this test cannot
    detect a containment check that stopped working.
    """

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    orphan = scratch / "notes.txt"
    orphan.write_text("x\n", encoding="utf-8")
    retired = tmp_path / "retired"
    retired.mkdir()
    retired_file = retired / "gone.py"
    orphan_transcript = _claude_transcript_touching(tmp_path / "orphan.jsonl", [orphan])
    retired_transcript = _claude_transcript_touching(tmp_path / "retired.jsonl", [retired_file])
    assert str(orphan) in session_files.scan_claude_transcript(orphan_transcript, str(scratch))
    assert str(retired_file) in session_files.scan_claude_transcript(retired_transcript, str(retired))
    orphan.unlink()
    retired.rmdir()

    info = SessionInfo(
        session="s1",
        panes=[],
        selected_pane=None,
        agents=[agent("claude", orphan_transcript, scratch), agent("claude", retired_transcript, retired)],
    )
    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    rows = {entry["abs_path"]: entry for entry in payload["files"]}
    assert str(orphan) in rows, payload["files"]
    assert rows[str(orphan)]["missing"] is True
    assert rows[str(orphan)]["repo"] == ""
    # The retired root exists as its own row and must NOT have absorbed the unrelated orphan.
    missing = _missing_repo_payloads(payload)
    assert [row["repo"] for row in missing] == [str(retired)], payload["repos"]
    assert missing[0]["touched_count"] == 1, missing


def test_missing_repo_row_carries_no_comparison_inputs_and_no_error(tmp_path):
    """A gone repo is one plain row: nothing to compare, nothing to open, and not a failure."""

    retired = tmp_path / "retired"
    retired.mkdir()
    remembered = [retired / f"gone_{index}.py" for index in range(3)]
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", remembered)
    assert str(remembered[0]) in session_files.scan_claude_transcript(transcript, str(retired))
    retired.rmdir()

    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, retired)])
    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    missing = _missing_repo_payloads(payload)
    assert len(missing) == 1, payload["repos"]
    assert missing[0]["from_ref"] == ""
    assert missing[0]["to_ref"] == ""
    assert missing[0]["error"] == ""
    assert missing[0]["count"] == 0
    assert "error_message" not in missing[0]
    assert payload["errors"] == []
    assert payload["warnings"] == []
    assert payload["files"] == []


def test_scope_all_merge_keeps_a_gone_repo_gone(tmp_path):
    """The cross-session merge must not turn a retired root back into an ordinary empty repo."""

    retired = tmp_path / "retired"
    retired.mkdir()
    remembered = [retired / "a.py", retired / "b.py"]
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", remembered)
    assert str(remembered[0]) in session_files.scan_claude_transcript(transcript, str(retired))
    retired.rmdir()
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, retired)])

    payload, status = session_files.session_files_payload(None, {"s1": info}, hours=24)

    assert status == HTTPStatus.OK
    missing = _missing_repo_payloads(payload)
    assert len(missing) == 1, payload["repos"]
    assert missing[0]["repo"] == str(retired)
    assert missing[0]["touched_count"] == len(remembered)
    assert missing[0]["from_ref"] == ""
    assert missing[0]["to_ref"] == ""
    assert payload["errors"] == []
    assert payload["warnings"] == []


def _prepared_repo_payload(session, repo, *, missing, count, touched_count):
    return {
        "session": session, "hours": 24.0, "files": [], "refs_by_repo": {},
        "from_ref": "default", "to_ref": "base", "errors": [], "warnings": [],
        "repos": [{
            "repo": str(repo), "missing": missing, "count": count, "touched_count": touched_count,
            "added": 0, "removed": 0,
            "from_ref": "" if missing else "default", "to_ref": "" if missing else "base",
            "error": "", "branch": "" if missing else "master",
        }],
    }


@pytest.mark.parametrize("order", [("stale", "live"), ("live", "stale")])
def test_scope_all_merge_lets_live_evidence_beat_a_missing_contributor(monkeypatch, tmp_path, order):
    """The REAL all-sessions aggregator must let a live contributor outrank a missing one.

    Both orders run: a merge rule that depends on which session is visited first is its own bug.
    Marking the shared key missing would hide the live session's real changes, which is the more
    dangerous direction of this defect.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    prepared = {
        "stale": _prepared_repo_payload("stale", repo, missing=True, count=0, touched_count=2),
        "live": _prepared_repo_payload("live", repo, missing=False, count=3, touched_count=5),
    }
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in order
    }
    monkeypatch.setattr(
        session_files, "session_files_payload_for_info",
        lambda info, *args, **kwargs: copy_module.deepcopy(prepared[info.session]),
    )

    payload, status = session_files.session_files_payload(None, infos, hours=24)

    assert status == HTTPStatus.OK
    assert len(payload["repos"]) == 1, payload["repos"]
    merged = payload["repos"][0]
    assert merged["missing"] is False, merged
    assert merged["from_ref"] == "default" and merged["to_ref"] == "base", merged
    # Each contributor is counted exactly once.
    assert merged["count"] == 3
    assert merged["touched_count"] == 7


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_scope_all_merge_keeps_an_all_missing_key_missing(monkeypatch, tmp_path, order):
    """When every contributing row is missing, the merged row stays missing in either order."""

    repo = tmp_path / "retired"
    prepared = {
        "a": _prepared_repo_payload("a", repo, missing=True, count=0, touched_count=2),
        "b": _prepared_repo_payload("b", repo, missing=True, count=0, touched_count=4),
    }
    infos = {name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[]) for name in order}
    monkeypatch.setattr(
        session_files, "session_files_payload_for_info",
        lambda info, *args, **kwargs: copy_module.deepcopy(prepared[info.session]),
    )

    payload, status = session_files.session_files_payload(None, infos, hours=24)

    assert status == HTTPStatus.OK
    merged = payload["repos"][0]
    assert merged["missing"] is True, merged
    assert merged["from_ref"] == "" and merged["to_ref"] == ""
    assert merged["touched_count"] == 6


def test_transcript_paths_route_through_the_one_exclusion_owner(tmp_path):
    """Version-control metadata never reaches Differ, and Differ keeps its own uploads rows.

    `path_exclusion_verdict` is the repository's single exclusion owner and its docstring is
    explicit that there is no exception for Git control files. Differ deliberately stops at that
    owner's unconditional floor: the Finder Quick Open policy layered on top of it also excludes
    `.uploads`, `build`, `dist`, `target` and `venv`, and hiding an edit an agent really made is
    the opposite of what this view is for.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "src").mkdir()
    (repo / ".uploads").mkdir()
    touched_paths = [
        repo / ".git" / "yolomux-probe.txt",   # harmless probe, never a real git control file
        repo / ".cache" / "data",
        repo / "node_modules" / "pkg.js",
        repo / "__pycache__" / "x.pyc",
        repo / "dist" / "bundle.js",
        repo / ".uploads" / "shot.png",
        repo / "src" / "live.py",
    ]
    for path in touched_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", touched_paths)
    assert str(repo / ".git" / "yolomux-probe.txt") in session_files.scan_claude_transcript(transcript, str(repo))

    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, repo)])
    touched = session_files.touched_files_for_info(info, session_files.session_files_cutoff(24, time.time()))
    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    # Assert ABSENCE explicitly on BOTH doors. A constructed-but-unasserted case is a check that
    # cannot fail: these paths reach Differ through the transcript scan AND through `git status`
    # as untracked rows, so each is checked in `touched` and in the rendered payload.
    rendered = sorted(entry["path"] for entry in payload["files"])
    for excluded in (".git/yolomux-probe.txt", ".cache/data", "node_modules/pkg.js", "__pycache__/x.pyc", "dist/bundle.js"):
        assert not any(path.endswith("/" + excluded) for path in touched), (excluded, sorted(touched))
        assert excluded not in rendered, (excluded, rendered)
    # `.uploads` is Differ's one documented exception: it renders uploaded files on purpose.
    assert ".uploads/shot.png" in rendered, rendered
    assert "src/live.py" in rendered, rendered
    assert _missing_repo_payloads(payload) == []


def test_deleted_nested_agent_cwd_yields_one_collapsed_root_not_a_crash(tmp_path):
    """A retired worktree whose nested agent cwd also vanished is one row, and never an exception.

    `historical_codex_candidate_cwds` called `git_root_for_path` directly instead of the safe
    resolution owner, so when a candidate cwd AND its parent were both gone the 404 escaped and
    killed the whole payload before classification ran -- no rows at all, not even wrong ones.
    Nested absent candidates are also ONE retired worktree, not one gone row per level.
    """

    retired = tmp_path / "retired"
    (retired / "src").mkdir(parents=True)
    remembered = [retired / "root.py", retired / "src" / "child.py"]
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", remembered)
    assert str(remembered[0]) in session_files.scan_claude_transcript(transcript, str(retired))
    (retired / "src").rmdir()
    retired.rmdir()
    info = SessionInfo(
        session="s1",
        panes=[],
        selected_pane=None,
        agents=[agent("claude", transcript, retired), agent("claude", transcript, retired / "src")],
    )

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time())

    assert payload["files"] == []
    assert payload["warnings"] == []
    assert payload["errors"] == []
    missing = _missing_repo_payloads(payload)
    assert [row["repo"] for row in missing] == [str(retired)], payload["repos"]
    assert missing[0]["touched_count"] == len(remembered)


def test_session_files_disk_cache_version_rejects_prior_records_and_round_trips_missing(monkeypatch, tmp_path):
    """The record version gate must reject the old shape AND admit the new one unchanged.

    `repos[].missing` changed what a serialized session-files record MEANS. A record written
    before that could still satisfy the signature check, so a retired worktree would keep
    rendering the pre-fix way for up to a week after the fix shipped -- correct code, stale
    screen. A gate that never rejects is the same as no gate, so both directions are asserted.
    """

    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    webapp = TmuxWebtermApp([])
    try:
        key = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "s1", 24.0, "", "", "", (), ())
        payload = {
            "session": "s1",
            "files": [],
            "repos": [{"repo": "/tmp/retired", "missing": True, "count": 0, "touched_count": 7,
                       "added": 0, "removed": 0, "from_ref": "", "to_ref": "", "error": ""}],
            "errors": [],
            "warnings": [],
        }
        webapp.write_session_files_disk_cache(key, payload, HTTPStatus.OK)
        restored = webapp.read_session_files_disk_cache(key)
        assert restored is not None
        assert restored[0]["repos"][0]["missing"] is True
        assert restored[0]["repos"][0]["touched_count"] == 7

        path, _signature = webapp.session_files_disk_cache_path(key)
        record = json.loads(path.read_text(encoding="utf-8"))
        # Pin the BUMP itself, not just the gate: reverting the constant must go red, or nothing
        # stops a future shape change from shipping behind a version an old record still matches.
        assert app_module.SESSION_FILES_CACHE_VERSION >= 2, (
            "repos[].missing changed the serialized record; the version must move with it"
        )
        assert record["version"] == app_module.SESSION_FILES_CACHE_VERSION
        record["version"] = app_module.SESSION_FILES_CACHE_VERSION - 1
        path.write_text(json.dumps(record), encoding="utf-8")
        assert webapp.read_session_files_disk_cache(key) is None
    finally:
        webapp.control_server.stop()


# --- Configured exclusion policy: Keivenc's "ALL ignore paths, not just git" ---------------------

_POLICY_MATRIX_SETTINGS = {
    "index_exclude_dir_names": [
        ".git", ".cache", "node_modules",
        "vendorcache",  # a configured directory NAME that is not in the shipped defaults
        ".uploads",     # configured, but Differ's one documented exception
    ],
    "index_exclude_paths": [
        "glob:**/generated/**",       # a configured GLOB rule
        "regex:(^|/)snapshots(/|$)",  # a configured REGEX rule
    ],
}


def _policy_from(settings):
    return exclusions.ExclusionPolicy.from_settings(settings, ())


def _empty_git_snapshot(repo, from_ref=None, to_ref=None):
    """A snapshot provider that supplies NO rows, so only the transcript door can populate files."""

    return {
        "branch": "master", "statuses": {}, "numstat": {},
        "selected_from": "", "selected_to": "", "status_error": "", "repo_error": "",
        "repo_error_message": {"key": "", "params": {}, "fallback": ""},
        "recent_refs": [], "ahead_behind": {},
    }


def _policy_matrix_repo(tmp_path):
    """A repo holding one file per matrix rule, plus the exact-path rule that needs a real path."""

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    excluded_rel = [
        # A harmless probe below `.git`, NOT a real git control file: writing over `.git/config`
        # mutilated the very repository whose `git status` the git-door half of this matrix reads.
        ".git/yolomux-probe.txt",   # built-in floor
        ".cache/data",              # shipped default NAME
        "node_modules/pkg.js",      # shipped default NAME
        "vendorcache/blob.bin",     # configured NAME
        "src/generated/api.py",     # configured GLOB rule
        "snapshots/golden.json",    # configured REGEX rule
        "secrets/exact.env",        # configured EXACT path rule
    ]
    admitted_rel = ["src/live.py", ".uploads/shot.png"]
    for rel in [*excluded_rel, *admitted_rel]:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    settings = {
        "index_exclude_dir_names": [".git", ".cache", "node_modules", "vendorcache", ".uploads"],
        "index_exclude_paths": [
            "glob:**/generated/**",
            "regex:(^|/)snapshots(/|$)",
            str(repo / "secrets"),  # an EXACT path rule
        ],
    }
    # The fixture may not damage what it measures: prove the repo is still intact and usable.
    assert git(repo, "config", "user.email").stdout.strip() == "test@example.com"
    assert git(repo, "status", "--porcelain").returncode == 0
    return repo, excluded_rel, admitted_rel, _policy_from(settings)


def test_configured_exclusion_policy_applies_at_the_transcript_door(tmp_path):
    """Door one in isolation: git supplies NOTHING, so every row here came from the transcript.

    The earlier version wrote the candidates into one repo as untracked content, so `git status`
    also produced them -- filtering either door alone made it green. An empty snapshot provider
    removes the git door entirely, so this can only pass if the transcript door filters.
    """

    repo, excluded_rel, admitted_rel, policy = _policy_matrix_repo(tmp_path)
    transcript = _claude_transcript_touching(
        tmp_path / "transcript.jsonl", [repo / rel for rel in [*excluded_rel, *admitted_rel]],
    )
    assert str(repo / ".git" / "yolomux-probe.txt") in session_files.scan_claude_transcript(transcript, str(repo))
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, repo)])

    payload = session_files.session_files_payload_for_info(
        info, hours=24, now=time.time(),
        git_snapshot_provider=_empty_git_snapshot,
        exclusion_policy=policy,
    )

    rendered = sorted(entry["path"] for entry in payload["files"])
    for rel in excluded_rel:
        assert rel not in rendered, (rel, rendered)
    for rel in admitted_rel:
        assert rel in rendered, (rel, rendered)


def test_configured_exclusion_policy_applies_at_the_git_status_door(tmp_path):
    """Door two in isolation: the transcript supplies NOTHING, so every row came from git status."""

    repo, excluded_rel, admitted_rel, policy = _policy_matrix_repo(tmp_path)
    empty_transcript = tmp_path / "empty.jsonl"
    empty_transcript.write_text("", encoding="utf-8")
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", empty_transcript, repo)])
    assert session_files.touched_files_for_info(
        info, session_files.session_files_cutoff(24, time.time()),
    ) == {}, "this half must not receive any transcript-attributed path"
    # Assert against the door's ACTUAL input -- what `git_name_status` hands the snapshot -- not
    # raw `git status --porcelain`, which collapses untracked directories to `?? vendorcache/`
    # and would make the excluded-file assertions below vacuous.
    with pinned_test_snapshot_runner(repo) as runner:
        door_input = session_files.git_name_status(repo, runner, None)[0]
    assert "vendorcache/blob.bin" in door_input, door_input
    assert "src/live.py" in door_input, door_input
    # Git never reports paths inside `.git`, so that one row is structurally out of reach at THIS
    # door. State it, rather than letting a vacuous assertion below imply it was observed here.
    assert not any(rel.startswith(".git/") for rel in door_input), door_input

    payload = session_files.session_files_payload_for_info(info, hours=24, now=time.time(), exclusion_policy=policy)

    rendered = sorted(entry["path"] for entry in payload["files"])
    for rel in excluded_rel:
        assert rel not in rendered, (rel, rendered)
    for rel in admitted_rel:
        assert rel in rendered, (rel, rendered)


def test_default_policy_admits_what_only_the_configured_policy_excludes(tmp_path):
    """The matrix must fail for the right reason: these paths are excluded BY CONFIGURATION.

    Without this, the door tests would still pass if the configured policy were ignored and only
    the shipped defaults applied, because the defaults already cover `.git` and `.cache`.
    """

    repo, _excluded, _admitted, policy = _policy_matrix_repo(tmp_path)
    configured_only = ["vendorcache/blob.bin", "src/generated/api.py", "snapshots/golden.json", "secrets/exact.env"]
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", [repo / rel for rel in configured_only])
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, repo)])

    default_rendered = sorted(
        entry["path"] for entry in
        session_files.session_files_payload_for_info(info, hours=24, now=time.time())["files"]
    )
    configured_rendered = sorted(
        entry["path"] for entry in
        session_files.session_files_payload_for_info(info, hours=24, now=time.time(), exclusion_policy=policy)["files"]
    )

    for rel in configured_only:
        assert rel in default_rendered, (rel, default_rendered)
        assert rel not in configured_rendered, (rel, configured_rendered)


def test_exclusion_policy_travels_from_the_real_submit_into_the_real_worker(monkeypatch, tmp_path):
    """Capture what `submit_session_files_job` ACTUALLY sends, then feed it to the real worker.

    The earlier version hand-built the payload and called the local builder, so it passed whether
    or not submit included the policy and whether or not the worker read it. This asserts the
    production producer and the production consumer, with nothing in between reimplemented.
    """

    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    repo, _excluded, _admitted, policy = _policy_matrix_repo(tmp_path)
    transcript = _claude_transcript_touching(
        tmp_path / "transcript.jsonl", [repo / "vendorcache" / "blob.bin", repo / "src" / "live.py"],
    )
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, repo)])
    configured = {
        "index_exclude_dir_names": [".git", "vendorcache"],
        "index_exclude_paths": ["glob:**/generated/**"],
    }
    monkeypatch.setattr(
        app_module.TmuxWebtermApp, "settings_payload",
        lambda self: {"settings": {"file_explorer": configured}},
    )
    webapp = TmuxWebtermApp([])
    submitted: list[dict] = []
    try:
        monkeypatch.setattr(
            webapp.job_client, "submit",
            lambda kind, payload, **kwargs: submitted.append(copy_module.deepcopy(payload)) or {"ok": False},
        )
        cache_key = webapp.session_files_cache_key("payload", {"s1": info}, "s1", 24.0, None, None, None)
        webapp.submit_session_files_job("s1", {"s1": info}, 24.0, None, None, None, cache_key)
    finally:
        webapp.control_server.stop()

    assert len(submitted) == 1, submitted
    shipped = submitted[0]
    assert shipped["exclusion_policy"] == _policy_from(configured).as_payload(), shipped.get("exclusion_policy")

    # The real worker entry point, fed exactly what the real submit produced.
    result = session_files.session_files_view_result(copy_module.deepcopy(shipped), max_bytes=8 * 1024 * 1024)
    assert result["status"] == 200
    assert result["profile"]["work"]["exclusion_policy_source"] == "payload"
    rendered = sorted(entry["path"] for entry in result["payload"]["files"])
    assert "src/live.py" in rendered, rendered
    assert "vendorcache/blob.bin" not in rendered, rendered


def test_worker_reuses_task_local_snapshot_while_deriving_isolated_exact_output(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    snapshot = {
        "branch": "main",
        "statuses": {"tracked.txt": "M"},
        "numstat": {"tracked.txt": {"added": 2, "removed": 1}},
        "selected_from": "",
        "selected_to": "",
        "status_error": "",
        "repo_error": "",
        "repo_error_message": {"key": "", "params": {}, "fallback": ""},
        "recent_refs": [{"name": "main", "commit": "abc"}],
        "ahead_behind": {"ahead": 1, "behind": 0},
    }
    real_deepcopy = copy_module.deepcopy
    snapshot_before = real_deepcopy(snapshot)
    snapshot_calls = []

    def cached_snapshot(repo_path, from_ref, to_ref, generation, builder):
        snapshot_calls.append((repo_path, from_ref, to_ref, generation, builder))
        return snapshot, False

    def info_for(session, pane_id):
        pane = PaneInfo(
            session=session,
            window="0",
            pane="0",
            pane_id=pane_id,
            target=f"{session}:0.0",
            current_path=str(repo),
            command="bash",
            active=True,
            window_active=True,
            title="",
            pid=1,
        )
        return SessionInfo(session=session, panes=[pane], selected_pane=pane, agents=[])

    request = {
        "session": "",
        "infos": {
            "s1": dataclasses.asdict(info_for("s1", "%1")),
            "s2": dataclasses.asdict(info_for("s2", "%2")),
        },
        "hours": 24.0,
        "include_cross_session_attribution": False,
    }
    monkeypatch.setattr(session_files, "cached_repository_snapshot", cached_snapshot)
    monkeypatch.setattr(session_files.time, "perf_counter", lambda: 100.0)

    expected = session_files.session_files_view_result(real_deepcopy(request), max_bytes=8 * 1024 * 1024)
    assert len(snapshot_calls) == 1
    snapshot_calls.clear()
    guarded_request = real_deepcopy(request)

    def reject_whole_snapshot_copy(value, memo=None):
        assert value is not snapshot, "the task-local provider must return its memoized snapshot directly"
        return real_deepcopy(value, memo)

    monkeypatch.setattr(session_files.copy, "deepcopy", reject_whole_snapshot_copy)
    result = session_files.session_files_view_result(guarded_request, max_bytes=8 * 1024 * 1024)

    assert result == expected
    assert len(snapshot_calls) == 1
    assert result["profile"]["work"]["repositories"] == 1
    assert result["profile"]["work"]["git_snapshots"] == 1
    assert snapshot == snapshot_before

    rows = {row["session"]: row for row in result["payload"]["files"]}
    assert rows.keys() == {"s1", "s2"}
    assert rows["s1"] is not rows["s2"]
    second_row_before = real_deepcopy(rows["s2"])
    rows["s1"]["added"] = 999
    rows["s1"]["agents"].append("mutated")
    assert rows["s2"] == second_row_before
    result["payload"]["refs_by_repo"][str(repo)][0]["name"] = "mutated"
    assert snapshot == snapshot_before


def test_worker_snapshot_memo_key_includes_authorized_root_identity(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "tracked.txt").write_text("safe\n", encoding="utf-8")
    parked = tmp_path / "repo-authorized"
    replacement_bytes = "BLOCKED_SENTINEL_DO_NOT_EXPOSE" * 4
    identities = []

    def cached_snapshot(_repo_path, _from_ref, _to_ref, _generation, _builder):
        handle = session_files._AUTHORIZED_REPOSITORY_SNAPSHOT_HANDLE.get()
        identities.append((handle.stat_result.st_dev, handle.stat_result.st_ino))
        child_stat = os.stat("tracked.txt", dir_fd=handle.descriptor, follow_symlinks=False)
        snapshot = _repository_snapshot_fixture(
            statuses={"tracked.txt": "M"},
            numstat={"tracked.txt": {"added": 1, "removed": 0}},
            file_identities={"tracked.txt": [child_stat.st_dev, child_stat.st_ino]},
        )
        if len(identities) == 1:
            repo.rename(parked)
            repo.mkdir()
            init_repo(repo)
            (repo / "tracked.txt").write_text(replacement_bytes, encoding="utf-8")
        return snapshot, False

    def info_for(session, pane_id):
        pane = PaneInfo(
            session=session, window="0", pane="0", pane_id=pane_id, target=f"{session}:0.0",
            current_path=str(repo), command="bash", active=True, window_active=True, title="", pid=1,
        )
        return SessionInfo(session=session, panes=[pane], selected_pane=pane, agents=[])

    monkeypatch.setattr(session_files, "cached_repository_snapshot", cached_snapshot)
    request = {
        "session": "",
        "infos": {
            "s1": dataclasses.asdict(info_for("s1", "%1")),
            "s2": dataclasses.asdict(info_for("s2", "%2")),
        },
        "hours": 24.0,
        "include_cross_session_attribution": False,
    }

    result = session_files.session_files_view_result(request, max_bytes=8 * 1024 * 1024)

    assert len(identities) == 2
    assert identities[0] != identities[1]
    rows = {row["session"]: row for row in result["payload"]["files"]}
    assert rows["s1"]["size"] == (parked / "tracked.txt").stat().st_size
    assert rows["s2"]["size"] == len(replacement_bytes)
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in repr(result)


def test_worker_falls_back_to_shipped_defaults_when_no_policy_arrives(tmp_path):
    """A missing or malformed policy must fail CLOSED to the defaults, and say that it did.

    An empty policy admits everything. An older queued job, a truncated payload or any
    deserialization failure would otherwise revert Differ to listing `.cache` and `node_modules`
    with nothing reporting it.
    """


    # The wrapper shapes.
    for unusable in (None, {}, {"skip_dir_names": "oops"}, {"skip_dir_names": []}, []):
        assert exclusions.ExclusionPolicy.from_payload(unusable) is None, unusable
    # THE MEMBER shapes. Validating the container is not validating the value: a well-formed list
    # holding one bad member used to drop it silently, leaving a MORE PERMISSIVE policy than the
    # one the web owner signed -- so the worker's answer and its cache identity disagreed. Both
    # fields go through the same validator, so both are enumerated.
    bad_members = (123, None, "", "   ", ["nested"], {"a": 1}, True, b".git")
    for member in bad_members:
        assert exclusions.ExclusionPolicy.from_payload(
            {"skip_dir_names": [member], "exclude_rules": []}
        ) is None, ("skip_dir_names", member)
        assert exclusions.ExclusionPolicy.from_payload(
            {"skip_dir_names": [".git"], "exclude_rules": [member]}
        ) is None, ("exclude_rules", member)
    # One bad member poisons the whole payload; the good ones must NOT survive on their own.
    assert exclusions.ExclusionPolicy.from_payload(
        {"skip_dir_names": [".git", 123], "exclude_rules": []}
    ) is None
    # A bare string is a sequence; it must be refused, not iterated into characters.
    for field in ("skip_dir_names", "exclude_rules"):
        payload = {"skip_dir_names": [".git"], "exclude_rules": []}
        payload[field] = ".git"
        assert exclusions.ExclusionPolicy.from_payload(payload) is None, field
    # A configuration that legitimately excludes nothing is still a policy, not an absence.
    empty_but_real = exclusions.ExclusionPolicy.from_payload({"skip_dir_names": [], "exclude_rules": []})
    assert empty_but_real == exclusions.ExclusionPolicy(), empty_but_real

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    for rel in (".cache/data", "node_modules/pkg.js", "src/live.py"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    transcript = _claude_transcript_touching(tmp_path / "transcript.jsonl", [repo / rel for rel in (".cache/data", "node_modules/pkg.js", "src/live.py")])
    info = SessionInfo(session="s1", panes=[], selected_pane=None, agents=[agent("claude", transcript, repo)])
    request = {
        "session": "s1",
        "infos": {"s1": dataclasses.asdict(info)},
        "hours": 24.0,
        "include_cross_session_attribution": False,
    }

    absent = session_files.session_files_view_result(dict(request), max_bytes=8 * 1024 * 1024)
    malformed = session_files.session_files_view_result(
        {**request, "exclusion_policy": {"skip_dir_names": "oops"}}, max_bytes=8 * 1024 * 1024,
    )

    for label, result in (("absent", absent), ("malformed", malformed)):
        rendered = sorted(entry["path"] for entry in result["payload"]["files"])
        assert ".cache/data" not in rendered, (label, rendered)
        assert "node_modules/pkg.js" not in rendered, (label, rendered)
        assert "src/live.py" in rendered, (label, rendered)
        # The fallback is visible in the product, not silent.
        assert result["profile"]["work"]["exclusion_policy_source"] == "default", (label, result["profile"]["work"])


def test_changing_only_the_configured_policy_changes_the_cache_and_coalesce_identity(monkeypatch, tmp_path):
    """A settings-only change must move the identity, or the old payload is served forever.

    This is the same staleness class as the record-version defect: a correct new answer that
    nobody asks for because the key still matches the old one.
    """

    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
    webapp = TmuxWebtermApp([])
    try:
        infos = {"s1": SessionInfo(session="s1", panes=[], selected_pane=None, agents=[])}

        def use(settings):
            monkeypatch.setattr(
                app_module.TmuxWebtermApp, "settings_payload",
                lambda self, _s=settings: {"settings": {"file_explorer": _s}},
            )
            return webapp.session_files_cache_key("payload", infos, "s1", 24.0, None, None, None)

        baseline = use({"index_exclude_dir_names": [".git"], "index_exclude_paths": []})
        same_again = use({"index_exclude_dir_names": [".git"], "index_exclude_paths": []})
        more_names = use({"index_exclude_dir_names": [".git", "vendorcache"], "index_exclude_paths": []})
        more_rules = use({"index_exclude_dir_names": [".git"], "index_exclude_paths": ["glob:**/generated/**"]})

        assert baseline == same_again, "an unchanged policy must not churn the identity"
        assert more_names != baseline, "a configured directory name must move the cache identity"
        assert more_rules != baseline, "a configured path rule must move the cache identity"
        assert more_names != more_rules

        identities = {webapp.session_files_view_coalesce_identity(key)[0] for key in (baseline, more_names, more_rules)}
        assert len(identities) == 3, identities
    finally:
        webapp.control_server.stop()

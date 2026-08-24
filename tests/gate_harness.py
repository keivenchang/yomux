"""Shared resources and assertions for the v0.6.10 regression gate.

Test modules opt in with ``pytest_plugins = ("tests.gate_harness",)`` or import
the fixtures they use.  The harness keeps mutable runtime resources owned by the
requesting fixture and keeps repeated/rate assertions at observable boundaries.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import resource
import select
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from yolomux_lib import app as app_module
from yolomux_lib import common
from yolomux_lib import auth as auth_module
from yolomux_lib import server as server_module
from yolomux_lib import server_auth
from yolomux_lib.auth import TEST_AUTH_BYPASS_ENV
from yolomux_lib.http_routes import RESPONSE_SSE
from yolomux_lib.http_routes import RESPONSE_WEBSOCKET
from yolomux_lib.http_routes import route_for_request
from yolomux_lib.infra.inotify_capacity import INOTIFY_FD_TARGET
from yolomux_lib.infra.inotify_capacity import INOTIFY_MAX_USER_INSTANCES_PATH
from yolomux_lib.infra.inotify_capacity import INOTIFY_MAX_USER_WATCHES_PATH
from yolomux_lib.infra.inotify_capacity import inotify_instance_census
from yolomux_lib.infra.inotify_capacity import process_fd_owners
from yolomux_lib.infra.inotify_capacity import read_kernel_limit
from yolomux_lib.infra.worktree_writer import child_process_artifact_environment
from yolomux_lib.observability.failure_severity import EXPECTED_OUTCOME_LOG_LEVEL
from yolomux_lib.local_services.registry import bounded_process_table
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import SpawnProcessOwnership
from yolomux_lib.local_services.registry import SpawnOwnershipProof
from yolomux_lib.local_services.registry import process_spawn_generation
from yolomux_lib.host_identity import process_start_identity
from yolomux_lib.server import TmuxWebtermHTTPServer
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.workspace import uploads as uploads_module
from yolomux_lib.workspace import workdir as workdir_module
from tests.browser_helpers.browser_console import assert_browser_journey_error_free
from tests.browser_helpers.browser_console import acknowledge_browser_diagnostic_receipts
from tests.browser_helpers.browser_console import begin_browser_journey_surface_tracking
from tests.browser_helpers.browser_console import consume_only_expected_server_log_errors
from tests.browser_helpers.browser_console import read_browser_console_log
from tests.browser_helpers.browser_console import retire_browser_after_strict_diagnostic_gate
from tests.browser_helpers.browser_console import validate_server_log_ring_payload
from tests.browser_helpers.browser_console import validate_server_log_ring_transition
from tests.aged_state import AgedStateRecipeResult
from tests.aged_state import AgedStateRoot
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from tests.gate_helpers import CounterDelta
from tests.gate_helpers import RepeatFailure
from tests.gate_helpers import assert_counter_delta
from tests.gate_helpers import repeat
from tests.gate_helpers import sample_counter_delta
from tools.test_plan import CHECK_LANE_ENV
from tools.test_plan import PYTEST_LANE_NAMES


UNIX_SOCKET_PATH_LIMIT_BYTES = 107
GATE_UNIX_SOCKET_PATH_BUDGET_BYTES = 100
GATE_HTTP_PORT_RANGE = range(7900, 8000)
GATE_LOCAL_SERVICE_IDLE_SECONDS = "60"


def gate_http_port_candidates(
    *,
    worker: str | None = None,
    worker_count: int | None = None,
    lane: str | None = None,
) -> tuple[int, ...]:
    """Return the 7900s ports owned by one check lane and xdist worker."""

    active_lane = os.environ.get(CHECK_LANE_ENV) if lane is None else lane
    candidates = tuple(GATE_HTTP_PORT_RANGE)
    if active_lane:
        if active_lane not in PYTEST_LANE_NAMES:
            raise ValueError(f"invalid YOLOmux check lane: {active_lane!r}")
        candidates = candidates[PYTEST_LANE_NAMES.index(active_lane)::len(PYTEST_LANE_NAMES)]
    active_worker = os.environ.get("PYTEST_XDIST_WORKER") if worker is None else worker
    if active_worker is None:
        return candidates
    if not active_worker.startswith("gw") or not active_worker[2:].isdigit():
        raise ValueError(f"invalid pytest-xdist worker id: {active_worker!r}")
    worker_index = int(active_worker[2:])
    if worker_count is None:
        raw_worker_count = os.environ.get("PYTEST_XDIST_WORKER_COUNT")
        if raw_worker_count is None or not raw_worker_count.isdigit():
            raise ValueError("PYTEST_XDIST_WORKER_COUNT must identify the active xdist worker pool")
        worker_count = int(raw_worker_count)
    if isinstance(worker_count, bool) or not isinstance(worker_count, int) or worker_count <= 0:
        raise ValueError(f"invalid pytest-xdist worker count: {worker_count!r}")
    if worker_index >= worker_count:
        raise ValueError(
            f"pytest-xdist worker {active_worker!r} is outside worker count {worker_count}"
        )
    candidates = candidates[worker_index::worker_count]
    if not candidates:
        raise ValueError(
            f"pytest-xdist worker count {worker_count} exceeds the {len(GATE_HTTP_PORT_RANGE)}-port gate range"
        )
    return candidates


@dataclass
class HttpPortLease:
    """A loopback TCP port reserved until its future server is ready to bind it."""

    host: str
    port: int
    _socket: socket.socket | None

    @classmethod
    def reserve(
        cls,
        host: str = "127.0.0.1",
        *,
        ports: Iterable[int] | None = None,
    ) -> "HttpPortLease":
        """Reserve the first available requested port, or any ephemeral port."""

        candidates = (0,) if ports is None else tuple(ports)
        if not candidates:
            raise ValueError("HTTP port candidate collection must not be empty")
        failures: list[tuple[int, OSError]] = []
        for candidate in candidates:
            if isinstance(candidate, bool) or not isinstance(candidate, int) or not 0 <= candidate <= 65535:
                raise ValueError(f"invalid HTTP port candidate: {candidate!r}")
            try:
                listener = cls._bind_listen(host, candidate)
            except OSError as error:
                failures.append((candidate, error))
                continue
            port = int(listener.getsockname()[1])
            return cls(host=host, port=port, _socket=listener)
        detail = ", ".join(f"{port}: {error}" for port, error in failures)
        raise OSError(f"no requested HTTP port could be reserved on {host}: {detail}")

    @staticmethod
    def _bind_listen(host: str, candidate: int) -> socket.socket:
        """The one reservation primitive: an exclusive listening hold on the port.

        The reservation must exclude the same thing the real subject excludes. The gate's
        subject server (`TmuxWebtermHTTPServer.allow_reuse_address == 1`) binds AND listens
        with SO_REUSEADDR, so a bind-only reservation does not hold the port against a
        like-for-like reuse-enabled server -- two SO_REUSEADDR sockets can both bind the same
        NON-listening address. Binding and then listening makes the hold exclusive against a
        reuse-enabled competitor (a second SO_REUSEADDR bind to an actively listening address
        is refused), while SO_REUSEADDR still lets the owner rebind through a predecessor
        TIME_WAIT. Both `reserve` and `reacquire` route through here so they cannot drift.
        """

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, candidate))
            listener.listen(1)
        except OSError:
            listener.close()
            raise
        return listener

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def reserved(self) -> bool:
        return self._socket is not None

    def release(self) -> int:
        """Release the reservation immediately before binding the subject server."""

        if self._socket is not None:
            self._socket.close()
            self._socket = None
        return self.port

    def reacquire(self) -> int:
        """Reserve the same owned port while a restartable fixture is stopped."""

        if self._socket is not None:
            return self.port
        self._socket = self._bind_listen(self.host, self.port)
        return self.port

    def close(self) -> None:
        self.release()


@dataclass(frozen=True)
class GateRuntimePaths:
    root: Path
    home_dir: Path
    config_dir: Path
    auth_config_path: Path
    state_dir: Path
    cache_dir: Path
    runtime_dir: Path
    codex_home: Path
    start_lock_dir: Path
    tool_lock_path: Path
    ca_dir: Path
    log_dir: Path
    workspace_dir: Path
    upload_dir: Path
    patched_module_paths: tuple[tuple[str, Path], ...]


GATE_WRITABLE_ENV_VARS = (
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "YOLOMUX_CONFIG_DIR",
    "YOLOMUX_STATE_DIR",
    "YOLOMUX_CACHE_DIR",
    "YOLOMUX_RUNTIME_DIR",
    "YOLOMUX_CODEX_HOME",
    "CODEX_HOME",
    "YOLOMUX_START_LOCK_DIR",
    "YOLOMUX_TOOL_LOCK_PATH",
    "YOLOMUX_CA_DIR",
    "YOLOMUX_LOG_DIR",
    "YOLOMUX_WORKSPACE_BASE",
)
GATE_TMUX_SOCKET_ENV_VAR = "YOLOMUX_TMUX_SOCKET"
GATE_HOME_ENV_VAR = "HOME"
GATE_ROOT_ENV_VAR = "YOLOMUX_ROOT"
GATE_BOOTSTRAP_PATH_ENV_VARS = (*GATE_WRITABLE_ENV_VARS, GATE_TMUX_SOCKET_ENV_VAR)


def assert_unix_socket_path_fits(path: Path) -> None:
    """Reject socket paths too close to Linux's fixed Unix-socket limit."""

    path_bytes = len(os.fsencode(path))
    if path_bytes > GATE_UNIX_SOCKET_PATH_BUDGET_BYTES:
        raise AssertionError(
            f"Unix socket path is {path_bytes} bytes; gate budget is "
            f"{GATE_UNIX_SOCKET_PATH_BUDGET_BYTES} bytes and Linux "
            f"sockaddr_un.sun_path allows {UNIX_SOCKET_PATH_LIMIT_BYTES} usable bytes: {path}"
        )


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def assert_writable_paths_beneath(root: Path, paths: Mapping[str, Path]) -> None:
    """Fail with every writable path that escapes the owning test root."""

    escaped = {
        label: path.resolve(strict=False)
        for label, path in paths.items()
        if not _is_beneath(path, root)
    }
    if escaped:
        details = ", ".join(f"{label}={path}" for label, path in sorted(escaped.items()))
        raise AssertionError(f"writable paths escape fixture root {root.resolve(strict=False)}: {details}")


def bootstrap_writable_paths() -> tuple[Path, dict[str, Path]]:
    """Return the collection-time isolation root and all environment paths."""

    root = Path(os.environ["YOLOMUX_TEST_ROOT"])
    return root, {name: Path(os.environ[name]) for name in GATE_BOOTSTRAP_PATH_ENV_VARS}


def resolved_gate_writable_paths(paths: GateRuntimePaths) -> dict[str, Path]:
    """Return every environment and imported-module path owned by one gate test."""

    resolved = {f"env:{name}": Path(os.environ[name]) for name in GATE_WRITABLE_ENV_VARS}
    resolved[f"env:{GATE_HOME_ENV_VAR}"] = Path(os.environ[GATE_HOME_ENV_VAR])
    resolved[f"env:{GATE_ROOT_ENV_VAR}"] = Path(os.environ[GATE_ROOT_ENV_VAR])
    resolved.update({f"module:{label}": path for label, path in paths.patched_module_paths})
    resolved["module:yolomux_lib.workspace.uploads.UPLOAD_TMP_BASE"] = uploads_module.UPLOAD_TMP_BASE
    return resolved


def patch_imported_writable_constants(
    monkeypatch: pytest.MonkeyPatch,
    old_to_new_roots: Mapping[Path, Path],
) -> tuple[tuple[str, Path], ...]:
    """Move imported YOLOmux Path constants from bootstrap roots to this test."""

    mappings = sorted(
        ((old.resolve(strict=False), new) for old, new in old_to_new_roots.items()),
        key=lambda item: len(item[0].parts),
        reverse=True,
    )
    patched: list[tuple[str, Path]] = []
    for module_name, module in tuple(sys.modules.items()):
        if not module_name.startswith("yolomux_lib") or module is None:
            continue
        for attribute, value in tuple(vars(module).items()):
            if not isinstance(value, Path):
                continue
            resolved = value.resolve(strict=False)
            for old_root, new_root in mappings:
                try:
                    relative = resolved.relative_to(old_root)
                except ValueError:
                    continue
                replacement = new_root / relative
                monkeypatch.setattr(module, attribute, replacement)
                patched.append((f"{module_name}.{attribute}", replacement))
                break
    return tuple(patched)


@pytest.fixture
def gate_runtime_paths(monkeypatch: pytest.MonkeyPatch) -> Iterable[GateRuntimePaths]:
    """Own every writable runtime root and imported path constant for one test."""

    root = Path(tempfile.mkdtemp(prefix="yag-", dir="/tmp"))
    ledger = install_fixture_local_service_ledger(monkeypatch)
    self_baseline = capture_fixture_self_baseline()
    home_dir = root / "home"
    config_dir = root / "config"
    auth_config_path = config_dir / "auth.yaml"
    state_dir = root / "state"
    cache_dir = root / "cache"
    runtime_base_dir = root / "runtime"
    codex_home = root / "codex-home"
    start_lock_dir = root / "locks" / "start.lock"
    tool_lock_path = root / "locks" / "expensive-tools.lock"
    ca_dir = root / "ca"
    log_dir = root / "logs"
    workspace_dir = root / "workspaces"
    upload_dir = root / "uploads"
    for directory in (
        home_dir,
        config_dir,
        state_dir,
        cache_dir,
        runtime_base_dir,
        codex_home,
        start_lock_dir.parent,
        ca_dir,
        log_dir,
        workspace_dir,
        upload_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    old_roots = {name: Path(os.environ[name]) for name in GATE_WRITABLE_ENV_VARS}
    tmux_socket_path = Path(os.environ[GATE_TMUX_SOCKET_ENV_VAR])
    assert_unix_socket_path_fits(tmux_socket_path)
    per_test_env = {
        "XDG_CONFIG_HOME": root / "xdg-config",
        "XDG_STATE_HOME": root / "xdg-state",
        "XDG_CACHE_HOME": root / "xdg-cache",
        "YOLOMUX_CONFIG_DIR": config_dir,
        "YOLOMUX_STATE_DIR": state_dir,
        "YOLOMUX_CACHE_DIR": cache_dir,
        "YOLOMUX_RUNTIME_DIR": runtime_base_dir,
        "YOLOMUX_CODEX_HOME": codex_home,
        "CODEX_HOME": codex_home,
        "YOLOMUX_START_LOCK_DIR": start_lock_dir,
        "YOLOMUX_TOOL_LOCK_PATH": tool_lock_path,
        "YOLOMUX_CA_DIR": ca_dir,
        "YOLOMUX_LOG_DIR": log_dir,
        "YOLOMUX_WORKSPACE_BASE": workspace_dir,
        GATE_TMUX_SOCKET_ENV_VAR: tmux_socket_path,
    }
    for name, path in per_test_env.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv(GATE_ROOT_ENV_VAR, str(root))
    # Keep the default Finder-root lookup and its native filesystem watcher
    # inside this test's owned HOME tree.
    monkeypatch.setenv(GATE_HOME_ENV_VAR, str(home_dir))

    # Package import installed one bootstrap artifact root before this fixture
    # selected its per-test product root. Rebase the package-owned paths before
    # the fixture starts tmux or any other child that imports yolomux_lib.
    child_environment = child_process_artifact_environment(Path(__file__).resolve().parents[1])
    for name in set(os.environ) | set(child_environment):
        current = os.environ.get(name)
        replacement = child_environment.get(name)
        if current == replacement:
            continue
        if replacement is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, replacement)

    patched_module_paths = patch_imported_writable_constants(
        monkeypatch,
        {old_roots[name]: per_test_env[name] for name in GATE_WRITABLE_ENV_VARS},
    )
    patched_paths = dict(patched_module_paths)
    for label, module in (
        ("yolomux_lib.infra.common.AUTH_CONFIG_PATH", common),
        ("yolomux_lib.auth.AUTH_CONFIG_PATH", auth_module),
    ):
        monkeypatch.setattr(module, "AUTH_CONFIG_PATH", auth_config_path)
        patched_paths[label] = auth_config_path
    patched_module_paths = tuple(sorted(patched_paths.items()))
    monkeypatch.setattr(uploads_module, "UPLOAD_TMP_BASE", upload_dir)

    # F2 product follow-up: CONFIG_DIR has independent import-time owners in
    # auth.py and infra/common.py, so the gate must patch and check both copies.
    assert common.CONFIG_DIR == config_dir
    assert auth_module.CONFIG_DIR == config_dir
    assert common.AUTH_CONFIG_PATH == auth_config_path
    assert auth_module.AUTH_CONFIG_PATH == auth_config_path

    paths = GateRuntimePaths(
        root=root,
        home_dir=home_dir,
        config_dir=config_dir,
        auth_config_path=auth_config_path,
        state_dir=state_dir,
        cache_dir=cache_dir,
        runtime_dir=runtime_base_dir,
        codex_home=codex_home,
        start_lock_dir=start_lock_dir,
        tool_lock_path=tool_lock_path,
        ca_dir=ca_dir,
        log_dir=log_dir,
        workspace_dir=workspace_dir,
        upload_dir=upload_dir,
        patched_module_paths=patched_module_paths,
    )
    assert_writable_paths_beneath(root, resolved_gate_writable_paths(paths))
    try:
        yield paths
    finally:
        # Removing the root is the last act of this fixture, so every writer it
        # owns has to be retired first.  A local-service daemon spawned straight
        # from gate_runtime_paths (JobClient/ApprovalClient, no app) unlinks its
        # socket and record inside runtime/services WHILE it exits, and no test
        # body joins that exit: shutil.rmtree then walks a directory another
        # process is still mutating and fails with a masked ENOENT.  Route the
        # retirement through the same owner the app fixtures use.
        run_fixture_cleanup_phases("gate_runtime_paths", (
            ("runtime root retirement", lambda: remove_fixture_runtime_root(
                ledger,
                root,
                label="gate_runtime_paths",
            )),
            ("worker inotify baseline", lambda: assert_fixture_inotify_returned_to_baseline(self_baseline, label="gate_runtime_paths")),
        ))


@pytest.fixture
def gate_http_port() -> Iterable[HttpPortLease]:
    """Reserve a fixture-owned HTTP port in the browser-E2E 7900s range."""

    lease = HttpPortLease.reserve(ports=gate_http_port_candidates())
    try:
        yield lease
    finally:
        lease.close()


@pytest.fixture
def gate_tmux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gate_runtime_paths: GateRuntimePaths):
    """Start one session on a fixture-owned private tmux socket.

    ``tests.tmux_runtime`` owns process creation and teardown.  Its generated
    session name is ``yt-<pid>-<uuid>-1`` and never reaches the default server.
    """

    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path, session_count=1)
    try:
        assert_unix_socket_path_fits(runtime.socket_path)
        yield runtime
    finally:
        stop_isolated_tmux_runtime(runtime)


@pytest.fixture
def aged_state_root(gate_runtime_paths: GateRuntimePaths) -> Iterable[AgedStateRoot]:
    """Provide an empty recipe root; tests explicitly select every aged condition they need."""

    root = AgedStateRoot(
        gate_runtime_paths.root,
        home_dir=gate_runtime_paths.home_dir,
        state_dir=gate_runtime_paths.state_dir,
        cache_dir=gate_runtime_paths.cache_dir,
        runtime_dir=gate_runtime_paths.runtime_dir,
    )
    try:
        yield root
    finally:
        root.close()


@pytest.fixture
def stateful_journey(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    aged_state_root: AgedStateRoot,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port: HttpPortLease,
    gate_tmux,
) -> Iterable[GateStatefulJourney]:
    """Provide a restartable private server without resetting the selected aged recipes."""

    del aged_state_root
    monkeypatch.setenv(TEST_AUTH_BYPASS_ENV, "1")
    journey = GateStatefulJourney(request, monkeypatch, gate_runtime_paths, gate_tmux, gate_http_port)
    try:
        yield journey
    finally:
        journey.stop()


@dataclass
class GateLiveServer:
    app: Any
    server: TmuxWebtermHTTPServer
    thread: threading.Thread
    tmux: Any
    paths: GateRuntimePaths
    server_log_boundary: Mapping[str, Any]
    options: "GateLiveServerOptions" = field(default_factory=lambda: GateLiveServerOptions())

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def finish(
        self,
        browsers: Any = None,
        *,
        server_log_reader: Callable[[], Mapping[str, Any]] | None = None,
        wait_for_api_quiescence: bool = True,
        require_owned_browsers: bool = False,
    ) -> None:
        finish_browser_fixture_boundary(
            browsers,
            self.base_url,
            lambda: stop_fixture_http_app(
                self.app,
                self.server,
                self.thread,
                label=self.options.label,
            ),
            settle_app=lambda: settle_fixture_app_evidence_boundary(
                self.app,
                label=self.options.label,
            ),
            server_log_reader=server_log_reader,
            server_log_boundary=getattr(
                self.server,
                "_fixture_server_log_boundary",
                self.server_log_boundary,
            ),
            wait_for_api_quiescence=wait_for_api_quiescence,
            require_owned_browsers=require_owned_browsers,
        )

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        self.finish()


@dataclass(frozen=True)
class GateLiveServerOptions:
    """Typed differences between fixture-owned HTTP server lifecycles."""

    address: tuple[str, int] = ("127.0.0.1", 0)
    tls_context: Any = None
    thread_name: str = "fixture-http-server"
    label: str = "fixture-owned gate"
    clear_server_logs: bool = False
    pin_jobd_scheduler: bool = False


def start_fixture_live_server(
    monkeypatch: pytest.MonkeyPatch,
    app: Any,
    options: GateLiveServerOptions,
    *,
    tmux: Any = None,
    paths: GateRuntimePaths | None = None,
    port_lease: HttpPortLease | None = None,
) -> GateLiveServer:
    """Acquire one app/server/thread runtime and roll back every partial start."""

    if options.clear_server_logs:
        SERVER_LOGS.clear()
    server_log_boundary = SERVER_LOGS.payload()
    prepare_fixture_http_app(monkeypatch, app)
    if port_lease is not None:
        port_lease.release()
    server = None
    thread = None
    try:
        if options.pin_jobd_scheduler:
            pin_fixture_jobd_scheduler(app)
        if options.tls_context is None:
            server = TmuxWebtermHTTPServer(options.address, app)
        else:
            server = TmuxWebtermHTTPServer(options.address, app, tls_context=options.tls_context)
        track_fixture_http_requests(server)
        thread = threading.Thread(
            target=server.serve_forever,
            name=options.thread_name,
            daemon=True,
        )
        thread.start()
    except BaseException as start_error:
        try:
            rollback_failed_fixture_http_start(
                app,
                server,
                thread,
                label=options.label,
                port_lease=port_lease,
            )
        except BaseException as rollback_error:
            raise start_error.with_traceback(start_error.__traceback__) from rollback_error
        raise
    return GateLiveServer(
        app=app,
        server=server,
        thread=thread,
        tmux=tmux,
        paths=paths,
        server_log_boundary=server_log_boundary,
        options=options,
    )


def retire_expected_fixture_server_log_errors(
    driver: Any,
    runtime: GateLiveServer,
    expected: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Consume and retire only the exact expected failures since this server began."""

    retired = consume_only_expected_server_log_errors(
        driver,
        expected,
        server_log_boundary=runtime.server_log_boundary,
    )
    start = validate_server_log_ring_payload(runtime.server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    expected_shapes = [
        {
            "level": str(entry.get("level") or "").lower(),
            "source": str(entry.get("source") or ""),
            "category": str(entry.get("category") or ""),
            "message": str(entry.get("message") or ""),
        }
        for entry in expected
    ]
    actual_shapes = [
        {
            "level": str(entry.get("level") or "").lower(),
            "source": str(entry.get("source") or ""),
            "category": str(entry.get("category") or ""),
            "message": str(entry.get("message") or ""),
        }
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    if actual_shapes != expected_shapes or transition["droppedCount"]:
        raise AssertionError(
            "fixture server-log retirement requires exactly the expected warning/error transition: "
            f"{json.dumps({'expected': expected_shapes, 'actual': actual_shapes, **dict(transition)}, sort_keys=True)}"
        )
    runtime.server_log_boundary = current
    driver._yolomux_server_log_boundary = current
    return retired


def retire_expected_fixture_http_failures(
    driver: Any,
    runtime: GateLiveServer,
    expected: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Retire one exact correlated set of deliberate browser/server HTTP failures."""

    expected_rows = tuple({
        "method": str(item.get("method") or "GET").upper(),
        "path": str(item.get("path") or ""),
        "query": dict(item.get("query") or {}),
        "status": int(item.get("status") or 0),
        "source": str(item.get("source") or "api-response"),
        "category": str(item.get("category") or "api"),
        "code": str(item.get("code") or ""),
        "request_id": str(item.get("request_id") or ""),
    } for item in expected)
    if (
        not expected_rows
        or any(not row["path"] or not row["code"] or not row["request_id"] for row in expected_rows)
        or len({row["request_id"] for row in expected_rows}) != len(expected_rows)
    ):
        raise AssertionError(f"expected HTTP failures require unique correlated rows: {expected_rows!r}")

    browser_entries = read_browser_console_log(driver)
    browser_failures = tuple(
        entry
        for entry in browser_entries
        if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}
    )
    actual_browser_rows = []
    for entry in browser_failures:
        message = str(entry.get("message") or "")
        parsed = urlsplit(message.split(" - Failed to load resource:", 1)[0])
        status = next(
            (row["status"] for row in expected_rows if f"status of {row['status']}" in message),
            None,
        )
        actual_browser_rows.append({
            "path": parsed.path,
            "query": {key: values[-1] for key, values in parse_qs(parsed.query).items() if values},
            "status": status,
        })
    expected_browser_rows = [
        {"path": row["path"], "query": row["query"], "status": row["status"]}
        for row in expected_rows
    ]
    canonical_browser = lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
    if sorted(map(canonical_browser, actual_browser_rows)) != sorted(map(canonical_browser, expected_browser_rows)):
        raise AssertionError(
            "fixture HTTP retirement requires exactly the expected Chrome failures: "
            f"{json.dumps({'expected': expected_browser_rows, 'actual': actual_browser_rows, 'entries': browser_failures}, sort_keys=True)}"
        )

    start = validate_server_log_ring_payload(runtime.server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    server_failures = tuple(
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    )
    actual_server_rows = []
    for entry in server_failures:
        payload = json.loads(str(entry.get("message") or ""))
        request = payload.get("request") if isinstance(payload.get("request"), Mapping) else {}
        stack = payload.get("stack") if isinstance(payload.get("stack"), list) else []
        route_frame = stack[0] if stack and isinstance(stack[0], Mapping) else {}
        actual_server_rows.append({
            "route": str(route_frame.get("operation") or ""),
            "source": str(entry.get("source") or ""),
            "category": str(entry.get("category") or ""),
            "code": str(payload.get("code") or ""),
            "request_id": str(request.get("id") or ""),
        })
    expected_server_rows = [{
        "route": f"{row['method']} {row['path']}",
        "source": row["source"],
        "category": row["category"],
        "code": row["code"],
        "request_id": row["request_id"],
    } for row in expected_rows]
    canonical_server = lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
    if (
        transition["droppedCount"]
        or sorted(map(canonical_server, actual_server_rows)) != sorted(map(canonical_server, expected_server_rows))
    ):
        raise AssertionError(
            "fixture HTTP retirement requires exactly the correlated server failures: "
            f"{json.dumps({'expected': expected_server_rows, 'actual': actual_server_rows, **dict(transition)}, sort_keys=True)}"
        )
    retired = consume_only_expected_server_log_errors(
        driver,
        tuple({
            "level": entry.get("level"),
            "source": entry.get("source"),
            "category": entry.get("category"),
            "message": entry.get("message"),
        } for entry in server_failures),
        server_log_boundary=start,
    )
    runtime.server_log_boundary = current
    driver._yolomux_server_log_boundary = current
    return {"browser": tuple(dict(entry) for entry in browser_failures), "server": retired}


def retire_expected_fixture_typed_api_failure(
    driver: Any,
    server: TmuxWebtermHTTPServer,
    api_event: Mapping[str, Any],
    *,
    method: str,
    path: str,
    source: str,
    code: str,
) -> dict[str, Any]:
    """Correlate and retire one browser-observed typed caller outcome from the fixture ring.

    ``code`` names an EXPECTED outcome of what the fixture asked for -- a file the test itself
    deleted, a directory the user browsed to that is not there.  ``failure_record_level``
    (``yolomux_lib/observability/failure_severity.py``) is the one owner of that distinction and
    records such a row at ``info``, because ``{"warning", "error"}`` is the release-blocking set the
    live soak and every retirement helper here collect.  So this retires an INFO row, and it holds
    the rule from both sides: the transition must contain no release-blocking row at all, and it
    must contain exactly one info row carrying this source, category and code, correlated to the
    browser's own API event.  An expected outcome that silently becomes an error again fails the
    first check; a genuine fault that appears alongside it fails the same check; a fault downgraded
    into this fixture's window arrives as a second info row and fails the second.
    """

    start = validate_server_log_ring_payload(server._fixture_server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    blocking = [
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    outcomes = [
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() == EXPECTED_OUTCOME_LOG_LEVEL
        and (str(entry.get("source") or ""), str(entry.get("category") or "")) == (source, "operation")
    ]
    if blocking or len(outcomes) != 1 or transition["droppedCount"]:
        raise AssertionError(
            "typed API outcome retirement requires exactly one retained info row and no release-blocking row: "
            f"{json.dumps({'blocking': blocking, 'outcomes': outcomes, **dict(transition)}, sort_keys=True)}"
        )
    entry = outcomes[0]
    payload = json.loads(str(entry.get("message") or ""))
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    stack = payload.get("stack") if isinstance(payload.get("stack"), list) else []
    route_frame = stack[0] if stack and isinstance(stack[0], dict) else {}
    expected_route = f"{str(method).upper()} {path}"
    if (
        (str(entry.get("source") or ""), str(entry.get("category") or "")) != (source, "operation")
        or payload.get("code") != code
        or not str(api_event.get("requestId") or "")
        or request.get("id") != api_event.get("requestId")
        or route_frame.get("operation") != expected_route
    ):
        raise AssertionError(
            "typed API failure browser/server correlation mismatch: "
            f"{json.dumps({'apiEvent': dict(api_event), 'serverEntry': entry}, sort_keys=True)}"
        )
    # Nothing is consumed here: an info row is not release-blocking, so it never had to be excused.
    # Asking the browser-visible ring for an EMPTY exact list is what proves that -- it fails if any
    # unconsumed warning/error row reached /api/logs in this window, including one produced by the
    # very outcome being retired, which is the check the ring at /api/logs and this transition would
    # otherwise be able to disagree about.
    unretired = consume_only_expected_server_log_errors(driver, (), server_log_boundary=start)
    if unretired:
        raise AssertionError(f"typed API outcome retirement consumed unexpected rows: {unretired!r}")
    server._fixture_server_log_boundary = current
    driver._yolomux_server_log_boundary = current
    return dict(entry)


def prepare_fixture_http_app(monkeypatch: pytest.MonkeyPatch, app: Any) -> None:
    """Disable process-lifetime work that has no fixture teardown boundary."""

    monkeypatch.setenv("YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS", GATE_LOCAL_SERVICE_IDLE_SECONDS)
    monkeypatch.setattr(app, "start_update_check_thread", lambda: False, raising=False)
    # The production auth cache is process-global and probes external CLIs on a daemon thread.
    # A fixture server cannot join that owner, so never let it outlive the fixture HOME.
    monkeypatch.setattr(server_module, "start_agent_auth_status_refresh", lambda *, force=False: False)
    monkeypatch.setattr(workdir_module, "start_agent_auth_status_refresh", lambda *, force=False: False)
    for method_name in (
        "stop_client_event_watcher",
        "stop_jobd_operation_service",
        "demote_background_owner",
        "stop_auto_approve_all",
    ):
        if not callable(getattr(app, method_name, None)):
            monkeypatch.setattr(app, method_name, lambda: None, raising=False)
    if not callable(getattr(app, "record_performance_sample", None)):
        monkeypatch.setattr(app, "record_performance_sample", lambda *_args, **_kwargs: None, raising=False)


@runtime_checkable
class FixtureSchedulerClient(Protocol):
    """The jobd scheduler-lease seam the gate fixture pins on setup and releases on teardown.

    Both halves act on one object: the real ``JobClient`` (``yolomux_lib/infra/jobd.py``) and any
    fixture stand-in must expose the SAME two calls, so setup pins and teardown releases can never
    diverge onto different owners.  ``start_for_scheduler`` takes the lease that pins jobd up;
    ``stop_for_scheduler`` releases it; ``holds_scheduler_lease`` reports whether the lease is held.
    """

    def start_for_scheduler(self) -> bool: ...

    def stop_for_scheduler(self) -> bool: ...

    @property
    def holds_scheduler_lease(self) -> bool: ...


class FixtureSchedulerApp(Protocol):
    """The app-ownership seam ``pin_fixture_jobd_scheduler`` requires of any fixture app.

    Both the real gate ``TmuxWebtermApp`` and the rollback fake app satisfy this one contract, so
    the pin routes through a typed seam rather than an ad-hoc attribute assumption, and the same
    ``job_client`` that setup pins is the one teardown's ``demote_background_owner`` releases.
    """

    job_client: FixtureSchedulerClient


class RecordingSchedulerClient:
    """A jobd scheduler-lease stand-in that records every pin and release for fixture tests.

    It satisfies ``FixtureSchedulerClient`` so a fixture app with no real broker still exercises
    the exact pin/release seam, and its counters prove exactly-once release on the rollback
    teardown path.  It models the real ``JobClient.stop_for_scheduler`` idempotence: teardown
    calls the release from two owners (``demote_background_owner`` and ``stop_auto_approve_all``),
    but only the first, while a lease is held, actually releases -- so ``releases`` counts the one
    effective release, not the two idempotent calls.
    """

    def __init__(self) -> None:
        self.start_for_scheduler_calls = 0
        self.stop_for_scheduler_calls = 0
        self.releases = 0
        self._leased = False

    def start_for_scheduler(self) -> bool:
        self.start_for_scheduler_calls += 1
        self._leased = True
        return True

    def stop_for_scheduler(self) -> bool:
        self.stop_for_scheduler_calls += 1
        if not self._leased:
            return True
        self._leased = False
        self.releases += 1
        return True

    @property
    def holds_scheduler_lease(self) -> bool:
        return self._leased


def pin_fixture_jobd_scheduler(app: FixtureSchedulerApp) -> None:
    """Pin jobd for the whole fixture window, exactly as the elected owner does in production.

    The gate app is a local background owner: ``DisabledBackgroundOwner.is_owner()`` and
    ``can_run(role)`` both return True, so a Finder/session-files interaction starts the
    owner-side session-files background refresh worker, and that worker submits
    ``session_files_view`` to jobd (``submit_session_files_job`` -> ``job_client.submit``).
    In production the owner first takes the scheduler lease
    (``handle_background_owner_acquired`` -> ``job_client.start_for_scheduler``), which spawns
    jobd and keeps its Unix socket present and warm before any refresh worker submits.  Without
    this pin the fixture served those owner-side producers against an unpinned jobd, so every
    jobd interaction was an on-demand cold start that, under -n16 CPU contention, raced an
    absent socket (``FileNotFoundError`` at ``client.connect``) or timed out on a 0.5s per-call
    budget -- and the strict browser-journey gate caught the emitted ``local-service:jobd``
    transport error.  The 5s spawn budget of this single setup pin, plus the 60s idle the
    fixture sets, guarantees the socket stays present for the bounded window.  Teardown already
    releases the lease symmetrically via ``demote_background_owner`` -> ``stop_for_scheduler``;
    only setup was missing its half.  ``start_for_scheduler`` is the same primitive the stateful
    journey reaches through ``start_background_owner``, so there is one jobd-pin owner, not two.
    """

    app.job_client.start_for_scheduler()


@dataclass
class FixtureHttpRequestActivity:
    condition: threading.Condition = field(default_factory=threading.Condition)
    sealed: bool = False
    active_finite: int = 0
    active_paths: dict[str, int] = field(default_factory=dict)
    late_request_paths: list[str] = field(default_factory=list)
    connections: set[socket.socket] = field(default_factory=set)
    preseal_connections: set[socket.socket] = field(default_factory=set)
    connection_paths: dict[socket.socket, str] = field(default_factory=dict)
    connection_threads: dict[socket.socket, threading.Thread] = field(default_factory=dict)
    handler_threads: set[threading.Thread] = field(default_factory=set)


def fixture_http_request_is_finite(method: str, target: str) -> bool:
    """Classify fixture requests from the production route registry."""

    normalized_method = method.upper()
    if normalized_method not in {"GET", "POST"}:
        return True
    route = route_for_request(normalized_method, urlsplit(target).path)
    return route is None or route.protocol not in {RESPONSE_SSE, RESPONSE_WEBSOCKET}


def track_fixture_http_requests(server: TmuxWebtermHTTPServer) -> None:
    """Track finite work and every accepted fixture connection through handler ownership."""

    server_state = vars(server)
    if "_fixture_http_request_activity" in server_state:
        return
    activity = FixtureHttpRequestActivity()
    base_handler_class = server.RequestHandlerClass
    original_process_request = getattr(server, "process_request", None)
    original_finish_request = getattr(server, "finish_request", None)
    original_shutdown_request = getattr(server, "shutdown_request", None)

    def tracked_process_request(request_socket, client_address):
        with activity.condition:
            activity.connections.add(request_socket)
            if not activity.sealed:
                activity.preseal_connections.add(request_socket)
            activity.connection_paths[request_socket] = "<accepted; handler not entered>"
        try:
            return original_process_request(request_socket, client_address)
        except BaseException:
            with activity.condition:
                activity.connections.discard(request_socket)
                activity.preseal_connections.discard(request_socket)
                activity.connection_paths.pop(request_socket, None)
                activity.connection_threads.pop(request_socket, None)
                activity.condition.notify_all()
            raise

    def tracked_finish_request(request_socket, client_address):
        current_thread = threading.current_thread()
        with activity.condition:
            activity.connection_threads[request_socket] = current_thread
            activity.handler_threads.add(current_thread)
        return original_finish_request(request_socket, client_address)

    def tracked_shutdown_request(request_socket):
        try:
            return original_shutdown_request(request_socket)
        finally:
            with activity.condition:
                activity.connections.discard(request_socket)
                activity.preseal_connections.discard(request_socket)
                activity.connection_paths.pop(request_socket, None)
                activity.connection_threads.pop(request_socket, None)
                activity.condition.notify_all()

    def tracked_method(method_name: str):
        original_method = getattr(base_handler_class, method_name)
        if not callable(original_method):
            raise TypeError(f"fixture HTTP handler method is not callable: {method_name}")
        http_method = method_name.removeprefix("do_")

        def run(handler):
            target = str(handler.path or "")
            path = urlsplit(target).path or "<unreadable>"
            finite = fixture_http_request_is_finite(http_method, target)
            request_label = f"{http_method} {path}"
            with activity.condition:
                request_socket = getattr(handler, "connection", None)
                if request_socket in activity.connections:
                    current_thread = threading.current_thread()
                    activity.connection_paths[request_socket] = request_label
                    activity.connection_threads[request_socket] = current_thread
                    activity.handler_threads.add(current_thread)
                if activity.sealed:
                    # Once teardown seals the accept boundary, refuse every late handler. Persistent
                    # handlers stay outside the finite completion count but may not reacquire demand.
                    # A socket accepted before seal can enter its handler thread afterward; refuse it
                    # without misreporting that pre-seal accept as a new late request.
                    if request_socket not in activity.preseal_connections:
                        activity.late_request_paths.append(request_label)
                    handler.close_connection = True
                    activity.condition.notify_all()
                    return None
                if finite:
                    activity.active_finite += 1
                    activity.active_paths[request_label] = activity.active_paths.get(request_label, 0) + 1
            if not finite:
                return original_method(handler)
            try:
                return original_method(handler)
            finally:
                with activity.condition:
                    activity.active_finite -= 1
                    remaining = activity.active_paths[request_label] - 1
                    if remaining:
                        activity.active_paths[request_label] = remaining
                    else:
                        del activity.active_paths[request_label]
                    activity.condition.notify_all()

        return run

    handler_overrides = {
        method_name: tracked_method(method_name)
        for method_name in ("do_GET", "do_POST", "do_HEAD")
    }
    server_state["_fixture_http_request_activity"] = activity
    if callable(original_process_request) and callable(original_shutdown_request):
        server.process_request = tracked_process_request
        server.shutdown_request = tracked_shutdown_request
    if callable(original_finish_request):
        server.finish_request = tracked_finish_request
    server.RequestHandlerClass = type(
        f"FixtureTracked{base_handler_class.__name__}",
        (base_handler_class,),
        handler_overrides,
    )


def seal_fixture_http_requests(server: TmuxWebtermHTTPServer) -> None:
    """Refuse every handler entry before the fixture stops accepting requests."""

    activity = vars(server).get("_fixture_http_request_activity")
    if activity is None:
        return
    with activity.condition:
        activity.sealed = True
        activity.condition.notify_all()


def wait_for_fixture_http_quiescence(server: TmuxWebtermHTTPServer, *, timeout: float = 3.0) -> None:
    """Wait only for pre-seal finite handlers and surface any post-seal entry."""

    activity = vars(server).get("_fixture_http_request_activity")
    if activity is None:
        return
    with activity.condition:
        stopped = activity.condition.wait_for(lambda: activity.active_finite == 0, timeout=timeout)
        assert stopped, (
            f"fixture HTTP server retained {activity.active_finite} active finite request handler(s): "
            f"{dict(sorted(activity.active_paths.items()))}"
        )
        assert not activity.late_request_paths, (
            "fixture HTTP server observed late finite request handler(s) after seal: "
            f"{activity.late_request_paths}"
        )


def retire_fixture_http_connections(server: TmuxWebtermHTTPServer, *, timeout: float = 3.0) -> None:
    """Wake and join every idle or persistent fixture connection after finite work settles."""

    activity = vars(server).get("_fixture_http_request_activity")
    if activity is None:
        return
    with activity.condition:
        connections = tuple(activity.connections)
    for connection in connections:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    with activity.condition:
        retired = activity.condition.wait_for(lambda: not activity.connections, timeout=timeout)
        if not retired:
            frames = sys._current_frames()
            retained = []
            for connection in activity.connections:
                owner_thread = activity.connection_threads.get(connection)
                thread_id = owner_thread.ident if owner_thread is not None else None
                frame = frames.get(thread_id)
                retained.append({
                    "fd": connection.fileno(),
                    "path": activity.connection_paths.get(connection, "<unknown>"),
                    "thread_id": thread_id,
                    "thread_name": owner_thread.name if owner_thread is not None else "<unknown>",
                    "thread_alive": owner_thread.is_alive() if owner_thread is not None else None,
                    "stack": "".join(traceback.format_stack(frame)) if frame is not None else "<no live frame>",
                })
            raise AssertionError(
                f"fixture HTTP server retained {len(activity.connections)} accepted connection(s): {retained}"
            )
        handler_threads = tuple(activity.handler_threads)
    for handler_thread in handler_threads:
        if handler_thread is threading.current_thread():
            continue
        handler_thread.join(timeout=max(0.0, deadline - time.monotonic()))
    live_threads = [
        f"{handler_thread.name} ({handler_thread.ident})"
        for handler_thread in handler_threads
        if handler_thread is not threading.current_thread() and handler_thread.is_alive()
    ]
    assert not live_threads, f"fixture HTTP server retained handler thread(s): {live_threads}"


@dataclass(frozen=True)
class FixtureLocalServiceProcess:
    registry: Any
    process: subprocess.Popen[Any] | None
    ownership: SpawnProcessOwnership | None


class FixtureMemberExitBarrier:
    """Wait for exact fixture-owned process identities without polling sleeps."""

    def __init__(self, member_identities: Sequence[tuple[int, str]]):
        self.pidfds: dict[int, int] = {}
        self.kqueue = None
        self.kqueue_pids: set[int] = set()
        identities = tuple((int(pid), str(identity)) for pid, identity in member_identities)
        self.identities = dict(identities)
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            for pid, identity in identities:
                if process_start_identity(pid) != identity:
                    continue
                try:
                    descriptor = os.pidfd_open(pid)
                except OSError:
                    continue
                if process_start_identity(pid) != identity:
                    os.close(descriptor)
                    continue
                self.pidfds[descriptor] = pid
            return
        if hasattr(select, "kqueue"):
            queue = select.kqueue()
            for pid, identity in identities:
                if process_start_identity(pid) != identity:
                    continue
                event = select.kevent(
                    pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
                try:
                    queue.control((event,), 0, 0)
                except OSError:
                    continue
                if process_start_identity(pid) == identity:
                    self.kqueue_pids.add(pid)
            if self.kqueue_pids:
                self.kqueue = queue
            else:
                queue.close()

    @property
    def unanchored_identities(self) -> tuple[tuple[int, str], ...]:
        anchored = set(self.pidfds.values())
        return tuple(sorted(
            (pid, identity)
            for pid, identity in self.identities.items()
            if pid not in anchored
        ))

    @property
    def can_wait_exact(self) -> bool:
        return bool(self.pidfds or (self.kqueue is not None and self.kqueue_pids))

    def signal_exact(
        self,
        signal_number: int,
        authorize: Callable[[int, str], bool],
    ) -> tuple[int, ...]:
        """Signal only kernel-anchored member identities authorized by the caller."""

        sent: list[int] = []
        if self.pidfds:
            for descriptor, pid in self.pidfds.items():
                identity = self.identities[pid]
                if not authorize(pid, identity):
                    continue
                try:
                    signal.pidfd_send_signal(descriptor, signal_number)
                except ProcessLookupError:
                    continue
                sent.append(pid)
            return tuple(sorted(sent))
        # A Darwin kqueue knote can wait for one exact process exit, but it cannot
        # deliver a signal. Numeric os.kill after a start-identity check would
        # reintroduce the PID-reuse race this barrier exists to prevent.
        return tuple(sent)

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        if self.pidfds:
            poller = select.poll()
            for descriptor in self.pidfds:
                poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
            pending = set(self.pidfds)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                events = poller.poll(max(1, int(remaining * 1000)))
                if not events:
                    return False
                for descriptor, _event in events:
                    pending.discard(descriptor)
                    poller.unregister(descriptor)
            return True
        if self.kqueue is not None and self.kqueue_pids:
            pending = set(self.kqueue_pids)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                events = self.kqueue.control(None, len(pending), remaining)
                if not events:
                    return False
                pending.difference_update(int(event.ident) for event in events)
            return True
        return True

    def close(self) -> None:
        for descriptor in self.pidfds:
            os.close(descriptor)
        self.pidfds.clear()
        if self.kqueue is not None:
            self.kqueue.close()
            self.kqueue = None
        self.kqueue_pids.clear()

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        self.close()


def fixture_local_service_registries(app: Any) -> tuple[Any, ...]:
    """Return each local-service lifecycle owner attached to one fixture app."""

    registries: list[Any] = []
    app_state = vars(app)
    for client_name in ("approval_client", "job_client", "search_indexer", "status_client", "watch_client"):
        client = app_state.get(client_name)
        registry = vars(client).get("registry") if client is not None else None
        if registry is not None:
            registries.append(registry)
    stats_client = app_state.get("stats_current_client")
    stats_transport = vars(stats_client).get("_transport") if stats_client is not None else None
    stats_registry = vars(stats_transport).get("registry") if stats_transport is not None else None
    if stats_registry is not None:
        registries.append(stats_registry)
    unique: list[Any] = []
    seen: set[int] = set()
    for registry in registries:
        identity = id(registry)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(registry)
    return tuple(unique)


def capture_fixture_local_service_processes(
    registries: Iterable[Any],
) -> tuple[FixtureLocalServiceProcess, ...]:
    """Capture exact fixture-spawned children before their reapers discard ownership."""

    captured: list[FixtureLocalServiceProcess] = []
    seen: set[int] = set()
    for registry in registries:
        identity = id(registry)
        if identity in seen:
            continue
        seen.add(identity)
        if hasattr(registry, "refresh_spawn_ownership"):
            registry.refresh_spawn_ownership()
        if isinstance(registry, LocalServiceRegistry):
            process = registry.process
            ownership = registry.spawn_ownership
        else:
            process = vars(registry).get("process")
            ownership = vars(registry).get("spawn_ownership")
        if process is None and ownership is None:
            continue
        captured.append(FixtureLocalServiceProcess(registry, process, ownership))
    return tuple(captured)


def fixture_local_service_processes(app: Any) -> tuple[FixtureLocalServiceProcess, ...]:
    """Capture the app-attached share of one fixture's local-service children."""

    return capture_fixture_local_service_processes(fixture_local_service_registries(app))


def seal_fixture_local_service_starts(registries: Iterable[Any]) -> None:
    """Fence replacement generations before any owned child is retired."""

    for registry in registries:
        if hasattr(registry, "seal_starts"):
            registry.seal_starts()


def fixture_process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    return True


def signal_fixture_process_group(process_group: int, signal_number: int) -> None:
    try:
        os.killpg(process_group, signal_number)
    except ProcessLookupError:
        pass


def fixture_owned_process_group_exists(
    ownership: SpawnProcessOwnership,
    *,
    label: str,
    require_proof: bool = True,
    proof: SpawnOwnershipProof | None = None,
) -> bool:
    """Prove one retained member still owns the recorded group before signaling it."""

    process_group = ownership.process_group
    if proof is not None:
        assert proof.ownership == ownership, f"{label} local-service ownership proof does not match its snapshot"
        if not proof.group_exists:
            return False
        if proof.owned_member_identities:
            return True
        if not require_proof:
            return False
        if not proof.disproven_occupants:
            # ``group_exists`` came from a process-table snapshot; membership is
            # read live from /proc. An owned daemon that exits between the two
            # leaves the group listed but no occupant to classify, and nothing
            # has been disproven. There is nothing left to signal, so this is a
            # retired group, not an unprovable one. Refusing to signal is
            # unchanged; only the misclassification as a violation is removed.
            return False
        raise AssertionError(json.dumps({
            "error_code": LOCAL_SERVICE_OWNERSHIP_DISPROVEN_CODE,
            "label": label,
            "process_group": process_group,
            "leader_pid": ownership.leader_pid,
            "session_id": ownership.session_id,
            "recorded_members": [list(member) for member in ownership.member_identities],
            "disproven_occupants": [list(occupant) for occupant in proof.disproven_occupants],
        }, sort_keys=True))
    if not fixture_process_group_exists(process_group):
        return False
    table = bounded_process_table()
    if not any(entry.pgid == process_group for entry in table.values()):
        return False
    retained_member = any(
        (entry := table.get(pid)) is not None
        and (entry.start_identity or process_start_identity(pid)) == start_identity
        and entry.pgid == process_group
        and entry.session_id == ownership.session_id
        and (entry.spawn_generation or process_spawn_generation(pid)) == ownership.generation_marker
        for pid, start_identity in ownership.member_identities
    )
    if not retained_member:
        if not require_proof:
            return False
        disproven = [
            [pid, entry.start_identity or process_start_identity(pid)]
            for pid, entry in table.items()
            if entry.pgid == process_group
            and (
                (entry.spawn_generation or process_spawn_generation(pid)) is not None
                or process_start_identity(pid)
            )
        ]
        if not disproven:
            # Every occupant of the group left between the snapshot and the
            # live reads. Nothing remains to signal, so this is retirement
            # under contention rather than a group owned by someone else.
            return False
        raise AssertionError(json.dumps({
            "error_code": LOCAL_SERVICE_OWNERSHIP_DISPROVEN_CODE,
            "label": label,
            "process_group": process_group,
            "leader_pid": ownership.leader_pid,
            "session_id": ownership.session_id,
            "recorded_members": [list(member) for member in ownership.member_identities],
            "disproven_occupants": disproven,
        }, sort_keys=True))
    return True


def stop_fixture_local_service_process(owner: FixtureLocalServiceProcess, *, label: str) -> None:
    """Stop one exact fixture-spawned service group before waiting on its leader."""

    registry = owner.registry
    process = owner.process
    ownership = owner.ownership
    if ownership is None:
        assert process is not None
        raise AssertionError(
            f"fixture local-service child {process.pid} has no durable spawn ownership"
        )
    immutable_ownership = ownership
    process_group = immutable_ownership.process_group
    assert process_group > 1 and process_group == ownership.leader_pid, (
        f"{label} local-service child {ownership.leader_pid} has invalid spawn-time process-group identity"
    )
    assert process_group != os.getpgrp(), (
        f"{label} local-service child {ownership.leader_pid} shares the fixture runner process group"
    )
    if process is not None and process.poll() is None:
        try:
            discovered_group = os.getpgid(process.pid)
        except ProcessLookupError:
            pass
        else:
            assert discovered_group == process_group, (
                f"{label} local-service child {process.pid} is not its dedicated process-group leader"
            )
    def refresh_ownership() -> tuple[SpawnProcessOwnership, SpawnOwnershipProof | None]:
        proof = registry.refresh_spawn_ownership_proof() if hasattr(registry, "refresh_spawn_ownership_proof") else None
        refreshed = proof.ownership if proof is not None else (
            registry.refresh_spawn_ownership() if hasattr(registry, "refresh_spawn_ownership") else immutable_ownership
        )
        refreshed = refreshed or immutable_ownership
        assert (
            refreshed.leader_pid,
            refreshed.process_group,
            refreshed.session_id,
            refreshed.generation_marker,
        ) == (
            immutable_ownership.leader_pid,
            immutable_ownership.process_group,
            immutable_ownership.session_id,
            immutable_ownership.generation_marker,
        ), f"{label} local-service spawn authority changed during teardown"
        return refreshed, proof

    def owned_members(
        current_ownership: SpawnProcessOwnership,
        current_proof: SpawnOwnershipProof | None,
    ) -> tuple[tuple[int, str], ...]:
        if current_proof is not None:
            return current_proof.owned_member_identities
        return current_ownership.member_identities

    ownership, proof = refresh_ownership()
    term_sent = False
    term_deadline = time.monotonic() + 2.0
    term_barrier = FixtureMemberExitBarrier(owned_members(ownership, proof))
    if fixture_owned_process_group_exists(ownership, label=label, proof=proof):
        signal_fixture_process_group(process_group, signal.SIGTERM)
        term_sent = True
    if term_sent:
        with term_barrier:
            term_barrier.wait(max(0.0, term_deadline - time.monotonic()))
    else:
        term_barrier.close()
    if process is not None:
        try:
            process.wait(timeout=max(0.0, term_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    ownership, proof = refresh_ownership()
    kill_sent = False
    kill_deadline = time.monotonic() + 1.0
    kill_barrier = FixtureMemberExitBarrier(owned_members(ownership, proof))
    ownership, proof = refresh_ownership()
    if fixture_owned_process_group_exists(ownership, label=label, require_proof=False, proof=proof):
        signal_fixture_process_group(process_group, signal.SIGKILL)
        kill_sent = True
    if kill_sent:
        with kill_barrier:
            kill_barrier.wait(max(0.0, kill_deadline - time.monotonic()))
    else:
        kill_barrier.close()
    if process is not None and process.poll() is None:
        try:
            process.wait(timeout=max(0.0, kill_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    ownership, proof = refresh_ownership()
    if fixture_owned_process_group_exists(ownership, label=label, require_proof=False, proof=proof):
        raise AssertionError(
            f"{label} local-service process group {process_group} remained after TERM/KILL settlement"
        )
    if process is not None:
        registry._reap_exited_child(process)


def stop_fixture_local_service_processes(
    owners: Iterable[FixtureLocalServiceProcess],
    *,
    label: str,
) -> None:
    """Retire every captured local-service group and keep each original failure."""

    errors: list[BaseException] = []
    for owner in owners:
        try:
            stop_fixture_local_service_process(owner, label=label)
        except BaseException as error:
            errors.append(error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(f"{label} local-service retirement failed", errors)


LOCAL_SERVICE_DAEMON_SURVIVED_CODE = "fixture_local_service_daemon_survived_retirement"
LOCAL_SERVICE_OWNERSHIP_DISPROVEN_CODE = "fixture_local_service_process_group_ownership_disproven"
FIXTURE_INOTIFY_NOT_RETURNED_CODE = "fixture_inotify_instances_not_returned_to_baseline"


@dataclass(frozen=True)
class FixtureSelfResourceBaseline:
    """One worker's own descriptor state at a fixture boundary.

    Scoped to this process on purpose: it is cheap enough to take on every gate
    test, and it is the only share of the uid-wide inotify budget this fixture
    can be held responsible for in-process.  Daemons the fixture started are
    covered separately by the surviving-daemon invariant.
    """

    worker_pid: int
    inotify_instances: int
    fd_count: int

    def as_reason(self) -> dict[str, Any]:
        return {
            "worker_pid": self.worker_pid,
            "inotify_instances": self.inotify_instances,
            "fd_count": self.fd_count,
        }


def capture_fixture_self_baseline() -> FixtureSelfResourceBaseline:
    """Measure this worker's own descriptors before or after one fixture."""

    worker_pid = os.getpid()
    owners = process_fd_owners(worker_pid)
    return FixtureSelfResourceBaseline(
        worker_pid=worker_pid,
        inotify_instances=owners.get(INOTIFY_FD_TARGET, 0),
        fd_count=sum(owners.values()),
    )


def assert_fixture_inotify_returned_to_baseline(
    baseline: FixtureSelfResourceBaseline,
    *,
    label: str,
) -> None:
    """Fail the fixture that kept an inotify instance it opened in this worker.

    Raising the kernel ceiling is mitigation and must never turn a leak green,
    so this reads no limit at all: it compares the worker's own instance count
    against the count measured before the fixture started.  The descriptor total
    is recorded for the report but not asserted, because a gate test legitimately
    leaves cached module and log descriptors open and no measurement yet shows
    exact descriptor parity is achievable here.
    """

    current = capture_fixture_self_baseline()
    if current.inotify_instances <= baseline.inotify_instances:
        return
    raise AssertionError(json.dumps({
        "error_code": FIXTURE_INOTIFY_NOT_RETURNED_CODE,
        "label": label,
        "before": baseline.as_reason(),
        "after": current.as_reason(),
        "leaked_instances": current.inotify_instances - baseline.inotify_instances,
        "fd_delta": current.fd_count - baseline.fd_count,
    }, sort_keys=True))


@dataclass(frozen=True)
class LocalServiceDaemonIdentity:
    """One live local-service daemon resolved by its own socket argument."""

    pid: int
    ppid: int
    process_group: int
    service: str
    socket_path: str
    spawn_generation: str
    inotify_instances: int

    def as_reason(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "process_group": self.process_group,
            "service": self.service,
            "socket_path": self.socket_path,
            "spawn_generation": self.spawn_generation,
            "inotify_instances": self.inotify_instances,
        }


def _daemon_socket_argument(command: str) -> Path | None:
    fields = command.split()
    for index, field_value in enumerate(fields):
        if field_value == "--socket" and index + 1 < len(fields):
            return Path(fields[index + 1])
    return None


def _daemon_service_name(command: str) -> str:
    fields = command.split()
    for index, field_value in enumerate(fields):
        if field_value == "-m" and index + 1 < len(fields):
            return fields[index + 1]
    return "unknown"


def local_service_daemons_beneath(
    root: Path,
    *,
    inotify_by_pid: Mapping[int, int] | None = None,
) -> tuple[LocalServiceDaemonIdentity, ...]:
    """Return every live local-service daemon whose socket this root owns.

    Membership is resolved from the daemon's own ``--socket`` argument, never
    from a command-name pattern: a ``pgrep -f``-style match also matches the
    shell that runs it, and a per-test root is the only thing that proves the
    daemon belongs to this test rather than a concurrent worker's.

    The inotify census is not taken here.  It reads every descriptor of every
    process this uid owns, which costs seconds on a loaded gate box, and this
    runs in the teardown of every gate test.  Callers that need per-daemon
    instance counts pass them in, and only the failure path pays for them.
    """

    counts = inotify_by_pid if inotify_by_pid is not None else {}
    found: list[LocalServiceDaemonIdentity] = []
    for pid, entry in bounded_process_table().items():
        command = entry.command
        if "--serve" not in command:
            continue
        socket_path = _daemon_socket_argument(command)
        if socket_path is None or not _is_beneath(socket_path, root):
            continue
        found.append(
            LocalServiceDaemonIdentity(
                pid=pid,
                ppid=entry.ppid,
                process_group=entry.pgid,
                service=_daemon_service_name(command),
                socket_path=str(socket_path),
                spawn_generation=process_spawn_generation(pid),
                inotify_instances=counts.get(pid, 0),
            )
        )
    return tuple(sorted(found, key=lambda daemon: (daemon.service, daemon.pid)))


@dataclass(frozen=True)
class ResourceLedgerSnapshot:
    """One measured resource census taken at a fixture boundary.

    Captured before a fixture starts and after it retires so a difference is
    attributable to a named owner instead of ambient machine load.
    """

    phase: str
    worker_pid: int
    worker_id: str
    rlimit_nofile_soft: int
    rlimit_nofile_hard: int
    fd_count: int
    fd_owners: Mapping[str, int]
    inotify_instances_self: int
    inotify_instances_user: int
    inotify_instances_by_pid: Mapping[int, int]
    inotify_max_user_instances: int
    inotify_max_user_watches: int
    local_service_daemons: tuple[LocalServiceDaemonIdentity, ...]

    def as_reason(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "worker_pid": self.worker_pid,
            "worker_id": self.worker_id,
            "rlimit_nofile_soft": self.rlimit_nofile_soft,
            "rlimit_nofile_hard": self.rlimit_nofile_hard,
            "fd_count": self.fd_count,
            "inotify_instances_self": self.inotify_instances_self,
            "inotify_instances_user": self.inotify_instances_user,
            "inotify_max_user_instances": self.inotify_max_user_instances,
            "inotify_max_user_watches": self.inotify_max_user_watches,
            "local_service_daemons": [daemon.as_reason() for daemon in self.local_service_daemons],
        }


def capture_resource_ledger(root: Path, *, phase: str) -> ResourceLedgerSnapshot:
    """Measure every resource a gate fixture can leak, at one boundary."""

    worker_pid = os.getpid()
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    fd_owners = process_fd_owners(worker_pid)
    user_total, by_pid = inotify_instance_census()
    return ResourceLedgerSnapshot(
        phase=phase,
        worker_pid=worker_pid,
        worker_id=os.environ.get("PYTEST_XDIST_WORKER", "master"),
        rlimit_nofile_soft=soft,
        rlimit_nofile_hard=hard,
        fd_count=sum(fd_owners.values()),
        fd_owners=fd_owners,
        inotify_instances_self=by_pid.get(worker_pid, 0),
        inotify_instances_user=user_total,
        inotify_instances_by_pid=by_pid,
        inotify_max_user_instances=read_kernel_limit(INOTIFY_MAX_USER_INSTANCES_PATH),
        inotify_max_user_watches=read_kernel_limit(INOTIFY_MAX_USER_WATCHES_PATH),
        local_service_daemons=local_service_daemons_beneath(root, inotify_by_pid=by_pid),
    )


def assert_no_surviving_local_service_daemons(root: Path, *, label: str) -> None:
    """Fail the test that leaked a daemon, not the next test that needs its limit.

    A surviving daemon holds its uid-wide inotify instance for as long as it
    runs, so leaving it alive would refuse a watcher inside some later, innocent
    test.  The survivor is killed here so one leak cannot cascade, and the leak
    is still reported as this test's failure with the owner named.
    """

    survivors = local_service_daemons_beneath(root)
    if not survivors:
        return
    # Only now, on the failure path, is the uid-wide census worth its cost.
    survivors = local_service_daemons_beneath(root, inotify_by_pid=inotify_instance_census()[1])
    snapshot = capture_resource_ledger(root, phase="after")
    reason = {
        "error_code": LOCAL_SERVICE_DAEMON_SURVIVED_CODE,
        "label": label,
        "root": str(root),
        "surviving": [daemon.as_reason() for daemon in survivors],
        "ledger": snapshot.as_reason(),
    }
    raise AssertionError(json.dumps(reason, sort_keys=True))


def _daemon_still_running(daemon: LocalServiceDaemonIdentity) -> bool:
    try:
        os.kill(daemon.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return process_spawn_generation(daemon.pid) == daemon.spawn_generation


def retire_local_service_daemons_beneath(root: Path, *, label: str) -> tuple[LocalServiceDaemonIdentity, ...]:
    """Retire every daemon this root owns, including ones no registry recorded.

    The in-process ledger only sees registries built inside the pytest worker.
    A gate fixture also starts a tmux runtime whose agent processes build their
    own registries and spawn their own daemons; those are reparented away when
    the agent exits, so nothing in this worker holds their ownership and the
    daemon outlives the root it was rooted in.  Resolving them from the process
    table by socket path is what makes them retirable at all.

    Identity is re-proved immediately before each signal: the pid is only
    signalled while it still carries the spawn generation recorded for it, so a
    reused pid can never be signalled by this helper.
    """

    retired: list[LocalServiceDaemonIdentity] = []
    for daemon in local_service_daemons_beneath(root):
        if not _daemon_still_running(daemon):
            continue
        retired.append(daemon)
        if daemon.process_group == daemon.pid:
            signal_fixture_process_group(daemon.process_group, signal.SIGTERM)
        else:
            try:
                os.kill(daemon.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
    if not retired:
        return ()
    barrier = FixtureMemberExitBarrier(
        tuple(
            (daemon.pid, process_start_identity(daemon.pid))
            for daemon in retired
            if process_start_identity(daemon.pid)
        )
    )
    with barrier:
        barrier.wait(2.0)
    for daemon in retired:
        if _daemon_still_running(daemon):
            if daemon.process_group == daemon.pid:
                signal_fixture_process_group(daemon.process_group, signal.SIGKILL)
            else:
                try:
                    os.kill(daemon.pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
    return tuple(retired)


class FixtureLocalServiceLedger:
    """Record every local-service writer created while one gate test owns its root.

    The runtime-root owner cannot discover these from an app: gate tests build
    ``JobClient``/``ApprovalClient`` directly against ``gate_runtime_paths``, so
    the registry never reaches an app attribute and nothing retires the daemon
    it spawned.  Recording every registry at construction gives the root owner
    the one list it needs to retire before removing the tree.
    """

    def __init__(self) -> None:
        self._registries: list[Any] = []
        self._registry_identities: set[int] = set()

    def record_registry(self, registry: Any) -> None:
        identity = id(registry)
        if identity in self._registry_identities:
            return
        self._registry_identities.add(identity)
        self._registries.append(registry)

    def registries_beneath(self, root: Path) -> tuple[Any, ...]:
        """Return only the registries whose service directory this root owns."""

        return tuple(
            registry
            for registry in self._registries
            if _is_beneath(Path(registry.service_dir), root)
        )


def install_fixture_local_service_ledger(monkeypatch: pytest.MonkeyPatch) -> FixtureLocalServiceLedger:
    """Track every local-service registry built for the duration of one test."""

    ledger = FixtureLocalServiceLedger()
    original_init = LocalServiceRegistry.__init__

    def tracked_init(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        ledger.record_registry(self)

    monkeypatch.setattr(LocalServiceRegistry, "__init__", tracked_init)
    return ledger


def retire_fixture_local_services(
    ledger: FixtureLocalServiceLedger,
    root: Path,
    *,
    label: str,
) -> None:
    """Retire every local-service writer owned by one runtime root before removal."""

    registries = ledger.registries_beneath(root)
    run_fixture_cleanup_phases(f"{label} local-service", (
        ("seal starts", lambda: seal_fixture_local_service_starts(registries)),
        ("stop processes", lambda: stop_fixture_local_service_processes(
            capture_fixture_local_service_processes(registries),
            label=label,
        )),
        # Retiring the registries this process recorded is not proof that no
        # daemon owned by this root is still running: a daemon spawned by a
        # tmux-hosted agent process is recorded by no registry here, and it
        # keeps its uid-wide inotify instance for as long as it runs. Reclaim
        # those from the process table, then prove none is left.
        ("unowned daemons", lambda: retire_local_service_daemons_beneath(root, label=label)),
        ("surviving daemons", lambda: assert_no_surviving_local_service_daemons(root, label=label)),
        ("reaper settlement", lambda: settle_fixture_local_service_reapers(registries, label=label)),
    ))


def remove_fixture_runtime_root(
    ledger: FixtureLocalServiceLedger,
    root: Path,
    *,
    label: str,
) -> None:
    """Remove one root only after every registry writer has settled successfully."""

    retire_fixture_local_services(ledger, root, label=label)
    shutil.rmtree(root)


def settle_fixture_local_service_reapers(registries: Iterable[Any], *, label: str) -> None:
    """Settle registry-owned record writers before their runtime directories are removed."""

    errors: list[BaseException] = []
    for registry in registries:
        if not isinstance(registry, LocalServiceRegistry):
            continue
        try:
            registry.settle_reaper_threads()
        except BaseException as error:
            errors.append(error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(f"{label} local-service reaper settlement failed", errors)


def run_fixture_cleanup_phases(label: str, phases: Sequence[tuple[str, Callable[[], None]]]) -> None:
    """Attempt ordered cleanup boundaries and preserve their original failures."""

    errors: list[BaseException] = []
    for _phase_name, callback in phases:
        try:
            callback()
        except BaseException as error:
            errors.append(error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(f"{label} teardown failed", errors)


def stop_fixture_app_runtime(app: Any, *, label: str) -> None:
    """Join every app-owned writer before its fixture root becomes removable."""

    if vars(app).get("_fixture_runtime_stopped") is True:
        return
    errors: list[BaseException] = []
    local_service_processes: list[FixtureLocalServiceProcess] = []
    seen_local_service_processes: set[int] = set()
    tabber_thread = None
    tabber_wake = None
    metadata_thread = None
    metadata_stop = None

    def attempt(callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException as error:
            errors.append(error)

    def capture_thread_owners() -> None:
        nonlocal tabber_thread, tabber_wake, metadata_thread, metadata_stop
        activity_service = vars(app).get("activity_transcript_service")
        if activity_service is not None:
            with activity_service.tabber_cache_lock:
                tabber_record = activity_service.tabber_warmer_record
                tabber_thread = tabber_record.thread
                tabber_wake = tabber_record.wake
        metadata_record = vars(app).get("metadata_warm_record")
        metadata_lock = vars(app).get("metadata_warm_lock")
        if metadata_record is not None and metadata_lock is not None:
            with metadata_lock:
                metadata_thread = metadata_record.worker
                metadata_stop = metadata_record.stop_event

    def capture_local_services() -> None:
        for owner in fixture_local_service_processes(app):
            process_identity = id(owner.registry)
            if process_identity in seen_local_service_processes:
                continue
            seen_local_service_processes.add(process_identity)
            local_service_processes.append(owner)

    def signal_metadata_warmer() -> None:
        if metadata_stop is not None:
            metadata_stop.set()

    def seal_local_service_starts() -> None:
        seal_fixture_local_service_starts(fixture_local_service_registries(app))

    def join_metadata_warmer() -> None:
        if metadata_thread is not None and metadata_thread is not threading.current_thread():
            metadata_thread.join(timeout=2)
            assert not metadata_thread.is_alive(), f"{label} metadata warmer did not stop"

    def stop_tabber_warmer() -> None:
        if tabber_wake is not None:
            tabber_wake.set()
        if tabber_thread is not None and tabber_thread is not threading.current_thread():
            tabber_thread.join(timeout=2)
            assert not tabber_thread.is_alive(), f"{label} Tabber activity warmer did not stop"

    attempt(capture_thread_owners)
    attempt(app.stop_client_event_watcher)
    attempt(signal_metadata_warmer)
    if vars(app).get("queued_delivery_ledger") is not None:
        attempt(lambda: app.wait_for_jobd_operations_terminal(3))
    attempt(app.stop_jobd_operation_service)
    attempt(join_metadata_warmer)
    attempt(seal_local_service_starts)
    attempt(capture_local_services)
    attempt(app.demote_background_owner)
    attempt(capture_local_services)
    attempt(stop_tabber_warmer)
    attempt(app.stop_auto_approve_all)
    attempt(capture_local_services)
    attempt(lambda: stop_fixture_local_service_processes(local_service_processes, label=label))
    attempt(lambda: settle_fixture_local_service_reapers(
        fixture_local_service_registries(app),
        label=label,
    ))
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(f"{label} fixture runtime teardown failed", errors)
    app._fixture_runtime_stopped = True


def settle_fixture_app_evidence_boundary(app: Any, *, label: str) -> None:
    """Join app-owned producers before strict fixture diagnostics are sampled."""

    tmux_signal_watcher = vars(app).get("tmux_signal_event_watcher")
    app.stop_client_event_watcher()
    tmux_signal_thread = vars(tmux_signal_watcher).get("thread") if tmux_signal_watcher is not None else None
    if tmux_signal_thread is not None and tmux_signal_thread is not threading.current_thread():
        tmux_signal_thread.join(timeout=2)
        assert not tmux_signal_thread.is_alive(), (
            f"{label} tmux-signal watcher did not settle before the browser evidence boundary"
        )
    session_files_service = vars(app).get("session_files_service")
    if session_files_service is not None:
        assert session_files_service.wait_for_idle(3), (
            f"{label} session-files work did not settle before the browser evidence boundary"
        )
    if vars(app).get("queued_delivery_ledger") is not None:
        app.wait_for_jobd_operations_terminal(3)


def stop_fixture_http_app(app: Any, server: TmuxWebtermHTTPServer, thread: threading.Thread, *, label: str) -> None:
    """Stop every app/server owner before a fixture may delete its runtime root."""

    def join_server_thread() -> None:
        thread.join(timeout=3)
        assert not thread.is_alive(), f"{label} HTTP server did not stop within three seconds"

    run_fixture_cleanup_phases(label, (
        ("seal", lambda: seal_fixture_http_requests(server)),
        ("server shutdown", server.shutdown),
        ("HTTP quiescence", lambda: wait_for_fixture_http_quiescence(server)),
        ("HTTP connection retirement", lambda: retire_fixture_http_connections(server)),
        ("app runtime", lambda: stop_fixture_app_runtime(app, label=label)),
        ("server close", server.server_close),
        ("thread join", join_server_thread),
    ))


def rollback_failed_fixture_http_start(
    app: Any,
    server: TmuxWebtermHTTPServer | None,
    thread: threading.Thread | None,
    *,
    label: str,
    port_lease: HttpPortLease | None = None,
) -> None:
    """Release every acquired startup owner without blocking on an unstarted server."""

    errors = []
    if server is not None and thread is not None and thread.is_alive():
        try:
            stop_fixture_http_app(app, server, thread, label=label)
        except BaseException as error:
            errors.append(error)
    else:
        try:
            stop_fixture_app_runtime(app, label=label)
        except BaseException as error:
            errors.append(error)
        if server is not None:
            try:
                server.server_close()
            except BaseException as error:
                errors.append(error)
    if port_lease is not None:
        try:
            port_lease.reacquire()
        except BaseException as error:
            errors.append(error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(f"{label} startup rollback failed", errors)


class GateStatefulJourney:
    """Restart one private server while retaining the same state, cache, tmux, and port."""

    def __init__(
        self,
        request: pytest.FixtureRequest,
        monkeypatch: pytest.MonkeyPatch,
        paths: GateRuntimePaths,
        tmux: Any,
        port_lease: HttpPortLease,
    ) -> None:
        self.request = request
        self.monkeypatch = monkeypatch
        self.paths = paths
        self.tmux = tmux
        self.port_lease = port_lease
        self.port = port_lease.port
        self.app: Any | None = None
        self.server: TmuxWebtermHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.server_log_boundary: Mapping[str, Any] | None = None
        self.starts = 0

    @property
    def running(self) -> bool:
        return self.server is not None and self.thread is not None and self.thread.is_alive()

    @property
    def runtime(self) -> GateLiveServer:
        if self.app is None or self.server is None or self.thread is None or not self.thread.is_alive():
            raise RuntimeError("stateful journey server is not running")
        if self.server_log_boundary is None:
            raise RuntimeError("stateful journey server log boundary is unavailable")
        return GateLiveServer(
            app=self.app,
            server=self.server,
            thread=self.thread,
            tmux=self.tmux,
            paths=self.paths,
            server_log_boundary=self.server_log_boundary,
        )

    def retire_expected_server_log_errors(
        self,
        driver: Any,
        expected: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Advance this journey's teardown boundary only after exact error retirement."""

        runtime = self.runtime
        retired = retire_expected_fixture_server_log_errors(driver, runtime, expected)
        self.server_log_boundary = runtime.server_log_boundary
        return retired

    def start(self) -> GateLiveServer:
        if self.running:
            raise RuntimeError("stateful journey server is already running")
        server_log_boundary = SERVER_LOGS.payload()
        app = app_module.TmuxWebtermApp(list(self.tmux.sessions))
        prepare_fixture_http_app(self.monkeypatch, app)
        server = None
        thread = None
        self.port_lease.release()
        try:
            server = TmuxWebtermHTTPServer(("127.0.0.1", self.port), app)
            track_fixture_http_requests(server)
            app.start_background_owner(port=self.port, managed_instance=True)
            thread = threading.Thread(
                target=server.serve_forever,
                name=f"aged-state-http-{self.starts + 1}",
                daemon=True,
            )
            thread.start()
        except BaseException as start_error:
            try:
                rollback_failed_fixture_http_start(
                    app,
                    server,
                    thread,
                    label="stateful journey",
                    port_lease=self.port_lease,
                )
            except BaseException as rollback_error:
                raise start_error.with_traceback(start_error.__traceback__) from rollback_error
            raise
        self.app = app
        self.server = server
        self.thread = thread
        self.server_log_boundary = server_log_boundary
        self.starts += 1
        return self.runtime

    def stop(self) -> None:
        server = self.server
        thread = self.thread
        app = self.app
        server_log_boundary = self.server_log_boundary
        self.server = None
        self.thread = None
        self.app = None
        self.server_log_boundary = None
        if app is not None and server is not None and thread is not None:
            fixture_browser = self.request.node.funcargs.get("browser")
            boundary_error = None
            try:
                finish_browser_fixture_boundary(
                    fixture_browser,
                    f"http://127.0.0.1:{self.port}",
                    lambda: stop_fixture_http_app(app, server, thread, label="stateful journey"),
                    settle_app=lambda: settle_fixture_app_evidence_boundary(
                        app,
                        label="stateful journey",
                    ),
                    server_log_boundary=server_log_boundary,
                    require_owned_browsers=fixture_browser is not None,
                )
            except BaseException as error:
                boundary_error = error
            try:
                self.port_lease.reacquire()
            except BaseException as reacquire_error:
                if boundary_error is not None:
                    raise boundary_error.with_traceback(boundary_error.__traceback__) from reacquire_error
                raise
            if boundary_error is not None:
                raise boundary_error.with_traceback(boundary_error.__traceback__)

    def restart(self) -> GateLiveServer:
        if not self.running:
            raise RuntimeError("stateful journey restart requires a running server")
        self.stop()
        return self.start()


@dataclass(frozen=True)
class GateAuthCredentials:
    """Fixture-owned credentials used only by the real form-login path."""

    username: str
    password: str
    role: str


@dataclass(frozen=True)
class GateHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(frozen=True)
class GateBundleVmResult:
    """Serializable observations returned by one real-bundle VM operation."""

    value: Any
    fetches: tuple[dict[str, Any], ...]
    batch_flushes: int
    js_debug_events: tuple[dict[str, Any], ...]
    js_debug_errors: tuple[dict[str, Any], ...]
    console_errors: tuple[str, ...]
    operation_error: str | None


_GATE_BUNDLE_VM_RUNNER = r"""
const fs = require('fs');
const helpers = require('./tests/browser_helpers/layout_test_helper');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const api = helpers.loadYolomux(
  String(input.search || ''),
  input.sessions,
  String(input.protocol || 'http:'),
  String(input.navigatorPlatform || 'Linux x86_64'),
  String(input.accessRole || 'admin'),
  {bootstrapOverrides: input.bootstrapOverrides || {}},
);
api.clearJsDebugEventsForTest();

const fetches = [];
if (input.fsBatchItem !== null) {
  api.setFetchForTest(async (url, options = {}) => {
    const rawBody = typeof options.body === 'string' ? options.body : '';
    let body = null;
    try {
      body = rawBody ? JSON.parse(rawBody) : null;
    } catch (error) {
      body = {parse_error: String(error?.message || error), raw: rawBody};
    }
    const request = {
      url: String(url),
      method: String(options.method || 'GET').toUpperCase(),
      body,
    };
    fetches.push(request);
    if (!String(url).startsWith('/api/fs/batch')) {
      return helpers.jsonResponse({error: `unexpected VM fetch: ${String(url)}`}, 500);
    }
    const requests = Array.isArray(body?.requests) ? body.requests : [];
    return helpers.jsonResponse({
      responses: requests.map(item => ({id: item.id, ...input.fsBatchItem})),
    }, 200);
  });
}

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
let value = null;
let operationError = null;
let settled = false;
let batchFlushes = 0;
const operation = new AsyncFunction('api', 'helpers', input.script)(api, helpers);
operation.then(
  () => { settled = true; },
  () => { settled = true; },
);

(async () => {
  try {
    if (input.flushFileExplorerBatch) {
      while (!settled && batchFlushes < input.maxBatchFlushes) {
        await api.flushFileExplorerFsBatchForTest();
        batchFlushes += 1;
        await helpers.flushAsyncWork();
      }
      if (!settled) {
        throw new Error(`VM operation did not settle after ${batchFlushes} filesystem batch flushes`);
      }
    }
    value = await operation;
  } catch (error) {
    operationError = String(error?.stack || error?.message || error);
  }
  const jsDebugEvents = api.jsDebugEventsForTest();
  const jsDebugErrors = api.jsDebugFailureEventsForTest();
  process.stdout.write(JSON.stringify({
    value: value === undefined ? null : value,
    fetches,
    batchFlushes,
    jsDebugEvents,
    jsDebugErrors,
    consoleErrors: api.vmConsoleErrorsForTest(),
    operationError,
  }));
})().catch(error => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
"""


@dataclass(frozen=True)
class GateBundleVm:
    """Run a caller-supplied operation against the checked-in v0 browser bundle."""

    repo_root: Path

    def execute(
        self,
        script: str,
        *,
        sessions: Iterable[str] = ("1",),
        search: str = "",
        bootstrap_overrides: Mapping[str, Any] | None = None,
        fs_batch_item: Mapping[str, Any] | None = None,
        flush_file_explorer_batch: bool = False,
        max_batch_flushes: int = 16,
        timeout: float = 20.0,
    ) -> GateBundleVmResult:
        """Execute one async JS body and return its result plus fetch/debug evidence.

        ``fs_batch_item`` replaces ``fetch`` with a real HTTP-200
        ``/api/fs/batch`` envelope.  Its fields are copied onto every requested
        item, so a caller can model rejected terminal-file candidates with a
        typed item such as ``{"ok": False, "status": 503, "error": "..."}``.
        """

        if not isinstance(script, str) or not script.strip():
            raise ValueError("bundle VM script must be a non-empty string")
        session_names = tuple(str(session) for session in sessions)
        if not session_names or any(not session for session in session_names):
            raise ValueError("bundle VM sessions must contain at least one non-empty name")
        if isinstance(max_batch_flushes, bool) or not isinstance(max_batch_flushes, int) or max_batch_flushes < 1:
            raise ValueError("max_batch_flushes must be a positive integer")
        request = {
            "script": script,
            "sessions": session_names,
            "search": str(search),
            "protocol": "http:",
            "navigatorPlatform": "Linux x86_64",
            "accessRole": "admin",
            "bootstrapOverrides": dict(bootstrap_overrides or {}),
            "fsBatchItem": dict(fs_batch_item) if fs_batch_item is not None else None,
            "flushFileExplorerBatch": flush_file_explorer_batch,
            "maxBatchFlushes": max_batch_flushes,
        }
        completed = subprocess.run(
            ("node", "-e", _GATE_BUNDLE_VM_RUNNER),
            cwd=self.repo_root,
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"bundle VM exited {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"bundle VM returned invalid JSON: stdout={completed.stdout!r}, stderr={completed.stderr!r}"
            ) from exc
        return GateBundleVmResult(
            value=payload.get("value"),
            fetches=tuple(payload.get("fetches") or ()),
            batch_flushes=int(payload.get("batchFlushes") or 0),
            js_debug_events=tuple(payload.get("jsDebugEvents") or ()),
            js_debug_errors=tuple(payload.get("jsDebugErrors") or ()),
            console_errors=tuple(str(error) for error in payload.get("consoleErrors") or ()),
            operation_error=str(payload["operationError"]) if payload.get("operationError") else None,
        )


@pytest.fixture
def gate_bundle_vm() -> GateBundleVm:
    """Provide a stateless runner for the checked-in v0 browser bundle."""

    return GateBundleVm(repo_root=Path(__file__).resolve().parents[1])


def gate_http_request(
    runtime: GateLiveServer,
    path: str,
    *,
    method: str = "GET",
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 8.0,
) -> GateHttpResponse:
    """Issue one bounded request against a fixture-owned live gate server."""

    connection = HTTPConnection("127.0.0.1", runtime.port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=dict(headers or {}))
        response = connection.getresponse()
        return GateHttpResponse(
            status=int(response.status),
            headers={str(name): str(value) for name, value in response.getheaders()},
            body=response.read(),
        )
    finally:
        connection.close()


def wait_for_fixture_client_event_demand(driver, timeout: float = 4.0, *, expected_enabled: bool = True) -> dict[str, Any]:
    """Wait until the current page owns the expected settled client-event transport state."""

    timeout_ms = max(0, round(float(timeout) * 1000))
    result = driver.execute_async_script(
        """
        const timeoutMs = arguments[0];
        const expectedEnabled = arguments[1];
        const done = arguments[arguments.length - 1];
        const started = performance.now();
        const inspect = () => {
          if (typeof clientEventTransportState !== 'object' || clientEventTransportState === null) {
            return {available: false};
          }
          const state = clientEventTransportState;
          const source = state.source || null;
          const replacementSource = state.replacementSource || null;
          const tracked = Array.isArray(window.__eventSources) ? window.__eventSources : null;
          const sourceUrl = String(source?.url || '');
          const replacementSourceUrl = String(replacementSource?.url || '');
          let sourceOrigin = '';
          let replacementSourceOrigin = '';
          try { sourceOrigin = sourceUrl ? new URL(sourceUrl, location.href).origin : ''; } catch (_error) {}
          try { replacementSourceOrigin = replacementSourceUrl ? new URL(replacementSourceUrl, location.href).origin : ''; } catch (_error) {}
          return {
            available: true,
            enabled: state.enabled === true,
            timerPending: state.demandTimer !== null,
            sourcePresent: source !== null,
            sourceOrigin,
            pageOrigin: location.origin,
            sourceTracked: tracked === null || tracked.includes(source),
            replacementSourcePresent: replacementSource !== null,
            replacementSourceOrigin,
            replacementSourceTracked: tracked === null || replacementSource === null || tracked.includes(replacementSource),
            openTrackedSources: tracked === null ? null : tracked.filter(item => (
              item && item.closed !== true && Number(item.readyState) !== 2
            )).length,
            demandPresent: state.demand !== null,
            demandChannels: Array.isArray(state.demand?.channels) ? [...state.demand.channels] : [],
            demandSignatureEmpty: state.demandSignature === '',
            signatureMatches: typeof clientEventDemandSignature === 'function'
              && state.demandSignature === clientEventDemandSignature(state.demand),
          };
        };
        const activeOwned = state => state.available === true
          && state.enabled === true
          && state.timerPending === false
          && state.sourcePresent === true
          && state.sourceOrigin === state.pageOrigin
          && state.sourceTracked === true
          && (!state.replacementSourcePresent || (
            state.replacementSourceOrigin === state.pageOrigin
            && state.replacementSourceTracked === true
          ))
          && (state.openTrackedSources === null || state.openTrackedSources === (state.replacementSourcePresent ? 2 : 1))
          && state.demandChannels.length > 0
          && state.signatureMatches === true;
        const disabledOwned = state => state.available === true
          && state.enabled === false
          && state.timerPending === false
          && state.sourcePresent === false
          && (state.openTrackedSources === null || state.openTrackedSources === 0)
          && state.demandPresent === false
          && state.demandChannels.length === 0
          && state.demandSignatureEmpty === true;
        const owned = state => expectedEnabled ? activeOwned(state) : disabledOwned(state);
        const check = () => {
          const state = inspect();
          if (owned(state)) { done({ok: true, state}); return; }
          if (performance.now() - started >= timeoutMs) { done({ok: false, state}); return; }
          requestAnimationFrame(check);
        };
        check();
        """,
        timeout_ms,
        expected_enabled,
    )
    assert result.get("ok") is True, f"client-event transport is not owned by the current browser fixture: {result.get('state')}"
    return result["state"]


# A genuinely stuck operation must still fail closed, but an in-flight full watch-diff baseline is a
# known completion receipt, not a hang. Under browser-lane concurrency (many chromedrivers doing real
# filesystem-watch work) a fresh POST /api/watch/roots can start a /api/fs/watch-diff?full=1 baseline
# right at the teardown boundary, leaving watchRootsBaselinePending true past the general quiescence
# timeout. Waiting on that one promise as a receipt is correct; raising the general timeout to paper
# over it is not. This bound is the fail-closed limit on a baseline that never delivers its receipt.
_WATCH_DIFF_BASELINE_RECEIPT_SECONDS = 20.0


def _read_fixture_operation_state(driver) -> dict[str, Any]:
    """Read and validate the fixture lifecycle operation state; raise on an unreachable adapter."""

    state = driver.execute_script(
        """
        const lifecycle = window.__yolomuxFixtureLifecycle;
        if (!lifecycle || typeof lifecycle.operationState !== 'function') {
          return {available: false};
        }
        return {available: true, diagnosticMode: lifecycle.diagnosticMode, ...lifecycle.operationState()};
        """
    )
    if not isinstance(state, Mapping) or state.get("available") is not True:
        raise AssertionError(f"fixture lifecycle operation state is unreachable: {state}")
    if state.get("diagnosticMode") not in {"retained-js", "browser-console"}:
        raise AssertionError(f"fixture lifecycle diagnostic mode is invalid: {state}")
    pending = state.get("pending")
    if not isinstance(pending, list) or not all(isinstance(operation_id, str) for operation_id in pending):
        raise AssertionError(f"fixture lifecycle pending operations are malformed: {state}")
    pending_details = state.get("pendingDetails")
    if pending_details is not None:
        if not isinstance(pending_details, list) or not all(isinstance(detail, Mapping) for detail in pending_details):
            raise AssertionError(f"fixture lifecycle pending operation details are malformed: {state}")
        detail_ids = [detail.get("id") for detail in pending_details]
        if not all(isinstance(operation_id, str) for operation_id in detail_ids):
            raise AssertionError(f"fixture lifecycle pending operation detail IDs are malformed: {state}")
        if not set(detail_ids) <= set(pending):
            raise AssertionError(f"fixture lifecycle pending operation details are not a subset of pending: {state}")
        truncated = state.get("pendingDetailsTruncated")
        if not isinstance(truncated, bool):
            raise AssertionError(f"fixture lifecycle pending operation truncation state is malformed: {state}")
        if not truncated and sorted(detail_ids) != sorted(pending):
            raise AssertionError(f"fixture lifecycle pending operation details are incomplete: {state}")
    if (
        state.get("diagnosticMode") == "retained-js"
        and not isinstance(state.get("watchRootsPending"), bool)
    ) or (
        "watchRootsPending" in state
        and not isinstance(state.get("watchRootsPending"), bool)
    ):
        raise AssertionError(f"fixture lifecycle watch-root state is malformed: {state}")
    # watchDiffPendingOperationIds partitions `pending` into the baseline's own parked operation IDs
    # and everything else. operationState() builds both from one Map synchronously, so for retained-JS
    # it is REQUIRED and every owned ID must be a real pending ID; an owned ID absent from `pending` is
    # a malformed contradiction, not a race. Browser-console omits this retained-JS-only field, but a
    # malformed present value fails closed in any mode.
    owned_ids = state.get("watchDiffPendingOperationIds")
    if state.get("diagnosticMode") == "retained-js" and owned_ids is None:
        raise AssertionError(f"fixture lifecycle watch-diff pending ownership is missing: {state}")
    if owned_ids is not None:
        if not isinstance(owned_ids, list) or not all(isinstance(operation_id, str) for operation_id in owned_ids):
            raise AssertionError(f"fixture lifecycle watch-diff pending ownership is malformed: {state}")
        if not set(owned_ids) <= set(pending):
            raise AssertionError(f"fixture lifecycle watch-diff pending ownership is not a subset of pending: {state}")
    owned_batch_fields = (
        ("batchQueued", "watchDiffBatchQueued"),
        ("batchPending", "watchDiffBatchPending"),
        ("batchOperations", "watchDiffBatchOperations"),
    )
    if state.get("diagnosticMode") == "retained-js":
        for total_field, owned_field in owned_batch_fields:
            total = state.get(total_field)
            owned = state.get(owned_field)
            if not isinstance(total, int) or not isinstance(owned, int) or owned < 0 or owned > total:
                raise AssertionError(f"fixture lifecycle watch-diff batch ownership is malformed: {state}")
        batch_operation_ids = state.get("watchDiffBatchOperationIds")
        if not isinstance(batch_operation_ids, list) or not all(
            isinstance(operation_id, str) for operation_id in batch_operation_ids
        ):
            raise AssertionError(f"fixture lifecycle watch-diff batch operation ownership is malformed: {state}")
        if not set(batch_operation_ids) <= set(pending):
            raise AssertionError(f"fixture lifecycle watch-diff batch operation ownership is not pending: {state}")
    for field in ("startupActive", "startupQueued"):
        value = state.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise AssertionError(f"fixture lifecycle startup coordinator state is malformed: {state}")
    return dict(state)


def _fixture_operation_state_quiescent(state: Mapping[str, Any]) -> bool:
    """Return whether every owned operation surface has reached terminal state."""

    return (
        not state.get("pending")
        and int(state.get("batchQueued") or 0) == 0
        and int(state.get("batchPending") or 0) == 0
        and int(state.get("batchOperations") or 0) == 0
        and int(state.get("startupActive") or 0) == 0
        and int(state.get("startupQueued") or 0) == 0
        and state.get("activityRefreshing") is not True
        and state.get("watchRootsPending", False) is False
        and state.get("finderWatchReady", True) is True
    )


def _blocked_only_by_watch_diff_baseline(state: Mapping[str, Any]) -> bool:
    """The single non-terminal condition is a known in-flight full watch-diff baseline receipt.

    A held watch-root timer/registration/in-flight registration is not a baseline receipt: those are
    ordinary pending work that must fail closed at the general timeout. The baseline parks its own
    operation record in `pending` while it awaits a 202 result, and its tree application may enqueue
    repo-info batch descendants before that promise settles. Disregard ONLY operation IDs and batch
    lifecycle counts attributed to the same terminal owner; any unrelated work still fails closed.
    """

    if state.get("diagnosticMode") != "retained-js":
        return False
    if state.get("watchRootsBaselinePending", False) is not True:
        return False
    if (
        state.get("watchRootsTimerPending", False) is True
        or state.get("watchRootsRegistrationPending", False) is True
        or state.get("watchRootsInFlight", False) is True
    ):
        return False
    baseline_owned = set(state.get("watchDiffPendingOperationIds") or [])
    unrelated_pending = [
        operation_id for operation_id in (state.get("pending") or []) if operation_id not in baseline_owned
    ]
    if unrelated_pending:
        return False
    batch_ownership = (
        ("batchQueued", "watchDiffBatchQueued"),
        ("batchPending", "watchDiffBatchPending"),
        ("batchOperations", "watchDiffBatchOperations"),
    )
    if any(int(state.get(total) or 0) != int(state.get(owned) or 0) for total, owned in batch_ownership):
        return False
    # Every other surface must be terminal. The shared receipt path waits for the baseline plus these
    # explicitly attributed batch descendants within one fail-closed bound.
    return _fixture_operation_state_quiescent({
        **state,
        "pending": [],
        "batchQueued": 0,
        "batchPending": 0,
        "batchOperations": 0,
        "watchRootsPending": False,
    })


def _await_in_flight_watch_diff_baseline(driver, timeout: float) -> Mapping[str, Any]:
    """Wait on the in-flight full watch-diff promise as a completion receipt, bounded fail-closed."""

    receipt = driver.execute_async_script(
        """
        const boundMs = arguments[0];
        const done = arguments[arguments.length - 1];
        const state = (typeof serverWatchRootsState === 'undefined') ? null : serverWatchRootsState;
        const promise = state ? state.watchDiffPromise : null;
        if (!promise) { done({hadPromise: false, settled: true}); return; }
        let finished = false;
        const finish = (result) => { if (finished) { return; } finished = true; done(result); };
        const timer = setTimeout(() => finish({hadPromise: true, settled: false, timedOut: true}), boundMs);
        Promise.resolve(promise).then(
          () => { clearTimeout(timer); finish({hadPromise: true, settled: true, rejected: false}); },
          () => { clearTimeout(timer); finish({hadPromise: true, settled: true, rejected: true}); },
        );
        """,
        int(max(0.0, float(timeout)) * 1000),
    )
    if not isinstance(receipt, Mapping):
        raise AssertionError(f"watch-diff baseline receipt is malformed: {receipt}")
    return receipt


def _wait_out_watch_diff_baseline_receipt(driver, blocked_state: Mapping[str, Any]) -> dict[str, Any]:
    """Wait for the in-flight baseline receipt, honoring its outcome; fail closed on a real hang.

    Only an actually in-flight async promise consumes the fail-closed bound. Once that promise settles,
    work it scheduled may still be delivering its own terminal receipt. Keep that descendant work inside
    the same bound; a baseline flag that remains set after its promise is gone is still a contradiction.
    """

    frozen_blocked = dict(blocked_state)
    deadline = time.monotonic() + _WATCH_DIFF_BASELINE_RECEIPT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise AssertionError(
                "in-flight full watch-diff baseline did not deliver its completion receipt before the "
                f"fail-closed bound: {json.dumps(frozen_blocked, sort_keys=True)}"
            )
        receipt = _await_in_flight_watch_diff_baseline(driver, remaining)
        if receipt.get("hadPromise") is not True:
            # The promise may settle between the operation-state snapshot and the separate WebDriver
            # await call. Re-read before classifying any remaining work.
            state = _read_fixture_operation_state(driver)
            if state.get("watchRootsBaselinePending", False) is True:
                raise AssertionError(
                    "watch-diff baseline was reported pending with no in-flight promise to await: "
                    f"receipt={json.dumps(dict(receipt), sort_keys=True)} "
                    f"state={json.dumps(state, sort_keys=True)}"
                )
            break
        if receipt.get("settled") is not True:
            # The promise is genuinely still in flight; this await consumed its slice of the bound.
            continue
        # The promise resolved or rejected. A still-set baseline flag is contradictory; other pending
        # work may be a concrete descendant (for example deferred Finder repo-info enrichment).
        state = _read_fixture_operation_state(driver)
        if state.get("watchRootsBaselinePending", False) is True:
            raise AssertionError(
                "watch-diff baseline receipt settled but its lifecycle flag is still pending: "
                f"receipt={json.dumps(dict(receipt), sort_keys=True)} "
                f"state={json.dumps(state, sort_keys=True)}"
            )
        break

    if not _fixture_operation_state_quiescent(state):
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise AssertionError(
                "watch-diff baseline descendants did not quiesce before the receipt bound: "
                f"receipt={json.dumps(dict(receipt), sort_keys=True)} "
                f"state={json.dumps(state, sort_keys=True)}"
            )

        last_state = state

        def descendants_settled(current):
            nonlocal last_state
            last_state = _read_fixture_operation_state(current)
            return last_state if _fixture_operation_state_quiescent(last_state) else False

        try:
            state = WebDriverWait(driver, remaining).until(descendants_settled)
        except TimeoutException as error:
            raise AssertionError(
                "watch-diff baseline descendants did not quiesce before the receipt bound: "
                f"receipt={json.dumps(dict(receipt), sort_keys=True)} "
                f"state={json.dumps(last_state, sort_keys=True)}"
            ) from error
    state["watchDiffBaselineReceipt"] = dict(receipt)
    return state


def wait_for_fixture_api_quiescence(driver, timeout: float = 12.0) -> dict[str, Any]:
    """Wait until product work and its diagnostic receipts reach owned terminal state."""

    last_state = None

    def settled(current):
        nonlocal last_state
        last_state = _read_fixture_operation_state(current)
        return last_state if _fixture_operation_state_quiescent(last_state) else False

    try:
        settled_state = WebDriverWait(driver, float(timeout)).until(settled)
    except TimeoutException as error:
        if last_state is not None and _blocked_only_by_watch_diff_baseline(last_state):
            # The one remaining surface is a known in-flight full watch-diff baseline. Wait on its
            # completion receipt rather than reporting a hang that is not one.
            settled_state = _wait_out_watch_diff_baseline_receipt(driver, last_state)
        else:
            raise AssertionError(
                "fixture API work did not quiesce before the owned boundary: "
                f"{json.dumps(last_state, sort_keys=True)}"
            ) from error
    if settled_state.get("diagnosticMode") == "retained-js":
        settled_state["browserReceiptBarrier"] = dict(acknowledge_browser_diagnostic_receipts(driver))
    return settled_state


def _browser_fixture_origin(url):
    parsed = urlsplit(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.lower(), port


def finish_browser_fixture_boundary(
    browser,
    base_url,
    cleanup,
    *,
    settle_app=None,
    server_log_reader=None,
    server_log_boundary=None,
    wait_for_api_quiescence=True,
    require_owned_browsers=False,
):
    gate_errors = []
    cleanup_errors = []
    browsers = browser if isinstance(browser, (tuple, list)) else (browser,)
    owned_browsers = []
    known_browser_ids = set()
    gated_server_ring_cursors = []
    server_ring_boundary = None
    fixture_start_boundary = None
    expected_origin = _browser_fixture_origin(base_url)
    for current_browser in browsers:
        if current_browser is None or id(current_browser) in known_browser_ids:
            continue
        known_browser_ids.add(id(current_browser))
        try:
            if expected_origin is not None and _browser_fixture_origin(current_browser.current_url) == expected_origin:
                owned_browsers.append(current_browser)
        except BaseException as error:
            gate_errors.append(error)
    if require_owned_browsers and len(owned_browsers) != len(known_browser_ids):
        gate_errors.append(
            AssertionError(
                "browser fixture shutdown did not receive every driver at its exact live origin: "
                f"expected={expected_origin!r}, owned={len(owned_browsers)}, supplied={len(known_browser_ids)}"
            )
        )
    ring_reader = SERVER_LOGS.payload if server_log_reader is None else server_log_reader
    if server_log_boundary is not None:
        try:
            fixture_start_boundary = validate_server_log_ring_payload(server_log_boundary)
        except BaseException as error:
            gate_errors.append(error)
    lifecycle_states = []
    for current_browser in owned_browsers:
        try:
            if wait_for_api_quiescence:
                lifecycle_state = wait_for_fixture_api_quiescence(current_browser)
            else:
                lifecycle_state = current_browser.execute_script(
                    """
                    const lifecycle = window.__yolomuxFixtureLifecycle;
                    return lifecycle ? {diagnosticMode: lifecycle.diagnosticMode} : null;
                    """
                )
                if not isinstance(lifecycle_state, Mapping):
                    raise AssertionError(f"fixture lifecycle adapter is unreachable: {lifecycle_state}")
            diagnostic_mode = lifecycle_state.get("diagnosticMode")
            if diagnostic_mode not in {"retained-js", "browser-console"}:
                raise AssertionError(f"fixture lifecycle diagnostic mode is invalid: {lifecycle_state}")
            lifecycle_states.append((current_browser, diagnostic_mode))
        except BaseException as error:
            gate_errors.append(error)
    for current_browser, diagnostic_mode in lifecycle_states:
        try:
            gate_options = {}
            if server_log_reader is not None:
                gate_options["server_log_reader"] = server_log_reader
            if server_log_boundary is not None:
                gate_options["server_log_boundary"] = server_log_boundary
            if diagnostic_mode == "browser-console":
                gate_options["require_js_debug_store"] = False
            gate_evidence = assert_browser_journey_error_free(current_browser, **gate_options)
            if isinstance(gate_evidence, Mapping) and "serverLogCursor" in gate_evidence:
                gated_server_ring_cursors.append(
                    validate_server_log_ring_payload(gate_evidence["serverLogCursor"])
                )
        except BaseException as error:
            gate_errors.append(error)
    for current_browser in owned_browsers:
        try:
            lifecycle_state = current_browser.execute_script(
                """
                const lifecycle = window.__yolomuxFixtureLifecycle;
                return lifecycle ? {diagnosticMode: lifecycle.diagnosticMode} : null;
                """
            )
            if not isinstance(lifecycle_state, Mapping):
                raise AssertionError(f"fixture lifecycle adapter is unreachable at retirement: {lifecycle_state}")
            diagnostic_mode = lifecycle_state.get("diagnosticMode")
            if diagnostic_mode not in {"retained-js", "browser-console"}:
                raise AssertionError(f"fixture lifecycle diagnostic mode is invalid at retirement: {lifecycle_state}")
            if diagnostic_mode == "browser-console":
                retire_browser_after_strict_diagnostic_gate(current_browser, require_js_debug_store=False)
            else:
                retire_browser_after_strict_diagnostic_gate(current_browser)
        except BaseException as error:
            gate_errors.append(error)
        finally:
            current_browser._yolomux_server_log_boundary = None
    # Retire every page before joining app-owned producers. A page that remains live can enqueue an
    # 8 ms filesystem batch after its quiescence sample, racing a newly accepted jobd operation into
    # the gap between wait_for_idle() and the ledger read. Atomic retirement closes that admission
    # source; the final ring snapshot below still catches any warning/error emitted while app work
    # settles.
    if settle_app is not None:
        try:
            settle_app()
        except BaseException as error:
            gate_errors.append(error)
    if fixture_start_boundary is not None or owned_browsers or gated_server_ring_cursors:
        try:
            server_ring_boundary = validate_server_log_ring_payload(ring_reader())
            if fixture_start_boundary is not None:
                transition = validate_server_log_ring_transition(fixture_start_boundary, server_ring_boundary)
                new_failures = [
                    entry
                    for entry in transition["newLogs"]
                    if str(entry.get("level") or "").lower() in {"warning", "error"}
                ]
                if new_failures or transition["droppedCount"]:
                    raise AssertionError(
                        "browser fixture server ring emitted errors after the fixture start boundary before the pre-stop boundary: "
                        f"{json.dumps({'newFailures': new_failures, **dict(transition)}, sort_keys=True)}"
                    )
            for gated_cursor in gated_server_ring_cursors:
                transition = validate_server_log_ring_transition(gated_cursor, server_ring_boundary)
                new_failures = [
                    entry
                    for entry in transition["newLogs"]
                    if str(entry.get("level") or "").lower() in {"warning", "error"}
                ]
                if new_failures or transition["droppedCount"]:
                    raise AssertionError(
                        "browser fixture server ring emitted errors after the full gate before the pre-stop boundary: "
                        f"{json.dumps({'newFailures': new_failures, **dict(transition)}, sort_keys=True)}"
                    )
        except BaseException as error:
            gate_errors.append(error)
    try:
        cleanup()
    except BaseException as error:
        cleanup_errors.append(error)
    if server_ring_boundary is not None:
        try:
            server_ring_after = validate_server_log_ring_payload(ring_reader())
            boundary_sequence = int(server_ring_boundary["sequence"])
            transition = validate_server_log_ring_transition(server_ring_boundary, server_ring_after)
            late_logs = [
                entry
                for entry in transition["newLogs"]
                if str(entry.get("level") or "").lower() in {"warning", "error"}
            ]
            if late_logs or transition["droppedCount"]:
                raise AssertionError(
                    "browser fixture server ring emitted errors during cleanup: "
                    f"{json.dumps({'beforeSequence': boundary_sequence, 'lateLogs': late_logs, **dict(transition)}, sort_keys=True)}"
                )
        except BaseException as error:
            cleanup_errors.append(error)
    for current_browser in owned_browsers:
        try:
            browser_log_failures = [
                entry
                for entry in read_browser_console_log(current_browser)
                if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}
            ]
            if browser_log_failures:
                raise AssertionError(
                    "browser fixture console emitted errors during cleanup: "
                    f"{json.dumps(browser_log_failures, sort_keys=True)}"
                )
        except BaseException as error:
            cleanup_errors.append(error)
    if gate_errors:
        gate_error = gate_errors[0]
        gate_traceback = gate_error.__traceback__
        secondary_errors = [*gate_errors[1:], *cleanup_errors]
        if secondary_errors:
            secondary_error = (
                secondary_errors[0]
                if len(secondary_errors) == 1
                else BaseExceptionGroup("browser fixture cleanup failed", secondary_errors)
            )
            raise gate_error.with_traceback(gate_traceback) from secondary_error
        raise gate_error.with_traceback(gate_traceback)
    if len(cleanup_errors) == 1:
        raise cleanup_errors[0]
    if cleanup_errors:
        raise BaseExceptionGroup("browser fixture cleanup failed", cleanup_errors)


def assert_fixture_client_event_demand_claimed(driver) -> dict[str, Any]:
    """Prove automatic demand cannot reacquire a stream claimed by a focused fixture."""

    state = driver.execute_script(
        """
        const scheduled = syncClientEventDemand();
        return {
          scheduled,
          enabled: clientEventTransportState.enabled,
          timerPending: clientEventTransportState.demandTimer !== null,
          sourcePresent: clientEventTransportState.source !== null,
        };
        """
    )
    expected = {"scheduled": False, "enabled": False, "timerPending": False, "sourcePresent": False}
    assert state == expected, f"client-event demand escaped fixture ownership: {state}"
    return state


def claim_fixture_client_event_demand(driver) -> dict[str, Any]:
    """Give a focused browser fixture exclusive ownership of client-event demand state."""

    bound = wait_for_fixture_client_event_demand(driver)
    driver.execute_script(
        """
        if (clientEventTransportState.demandTimer !== null) {
          clearTimeout(clientEventTransportState.demandTimer);
          clientEventTransportState.demandTimer = null;
        }
        closeClientEventStream();
        clientEventTransportState.enabled = false;
        """
    )
    claimed = assert_fixture_client_event_demand_claimed(driver)
    return {"bound": bound, "claimed": claimed}


def release_fixture_client_event_demand(driver, timeout: float = 8.0) -> dict[str, Any]:
    """Restore the product transport owner and settle work held during an exclusive claim."""

    released = driver.execute_script(
        """
        const pendingBefore = Array.from(apiOperationState.pending.keys()).sort();
        clientEventTransportState.enabled = true;
        const scheduled = syncClientEventDemand({immediate: true});
        return {
          scheduled,
          pendingBefore,
          demandOperations: Array.isArray(clientEventTransportState.demand?.operations)
            ? [...clientEventTransportState.demand.operations]
            : [],
        };
        """
    )
    if not isinstance(released, Mapping):
        raise AssertionError(f"client-event demand release returned malformed state: {released}")
    pending_before = released.get("pendingBefore")
    demanded_operations = released.get("demandOperations")
    if not isinstance(pending_before, list) or not isinstance(demanded_operations, list):
        raise AssertionError(f"client-event demand release operation state is malformed: {released}")
    if not set(pending_before).issubset(demanded_operations):
        raise AssertionError(f"client-event demand release omitted pending operation IDs: {released}")
    bound = wait_for_fixture_client_event_demand(driver, timeout=timeout)
    settled = wait_for_fixture_api_quiescence(driver, timeout=timeout)
    return {"released": dict(released), "bound": bound, "settled": settled}


def load_gate_browser(driver, runtime: GateLiveServer, path: str = "/") -> None:
    """Load the fixture-owned server and wait for the exact shared API globals."""

    target = str(path or "/")
    if not target.startswith("/"):
        raise ValueError("gate browser path must start with /")
    driver._yolomux_server_log_boundary = runtime.server_log_boundary
    driver.get(f"{runtime.base_url}{target}")
    run_when_browser_ready(
        driver,
        "return true;",
        globals_required={"apiFetch": "function", "apiFetchJson": "function"},
        dom_anchors=("#grid",),
        timeout=8,
    )
    begin_browser_journey_surface_tracking(driver)
    wait_for_fixture_client_event_demand(driver)
    wait_for_fixture_api_quiescence(driver)
    assert runtime.app.session_files_service.wait_for_idle(3), (
        "fixture session-files work did not settle before the browser evidence boundary"
    )


def load_gate_terminal_only_browser(driver, runtime: GateLiveServer, *, timeout: float = 8.0) -> str:
    """Load and focus one terminal without admitting unrelated Finder/watch work."""

    session = runtime.tmux.sessions[0]
    encoded_session = quote(session)
    load_gate_browser(
        driver,
        runtime,
        f"/?sessions={encoded_session}&layout=left&tabs=left:{encoded_session}",
    )
    run_when_browser_ready(
        driver,
        "return terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN"
        " && Boolean(document.querySelector(`#term-${CSS.escape(arguments[0])} .xterm-screen`))"
        " && !fileExplorerPaneIsOpen()"
        " && !fileExplorerTreePaneIsVisible()"
        " && !fileExplorerSessionFilesPaneIsVisible();",
        session,
        globals_required={
            "fileExplorerPaneIsOpen": "function",
            "fileExplorerTreePaneIsVisible": "function",
            "fileExplorerSessionFilesPaneIsVisible": "function",
        },
        dom_anchors=("#grid",),
        timeout=timeout,
    )
    driver.find_element("css selector", f"#term-{session} .xterm-screen").click()
    run_when_browser_ready(
        driver,
        "return document.activeElement === document.querySelector(`#term-${CSS.escape(arguments[0])} textarea`);",
        session,
        dom_anchors=(f"#term-{session} .xterm-screen",),
        timeout=timeout,
    )
    return session


def open_gate_stats_surface(driver, *, timeout: float = 8.0) -> dict[str, Any]:
    """Open YO!stats through its real File-menu action and prove the panel is rendered."""

    begin_browser_journey_surface_tracking(driver)
    activated = WebDriverWait(driver, timeout).until(
        lambda current: current.execute_script(
            """
            const menuButton = document.querySelector(
              '.app-menu[data-app-menu="file"] > .app-menu-button'
            );
            if (!menuButton?.isConnected) return null;
            menuButton.click();
            const command = document.querySelector(
              '.app-menu[data-app-menu="file"] .app-menu-command[data-menu-target-item="__debug__"]'
            );
            if (!command?.isConnected) return null;
            command.click();
            return {menuActivated: true, commandActivated: true};
            """
        )
    )
    if activated != {"menuActivated": True, "commandActivated": True}:
        raise AssertionError(f"stats menu activation returned malformed state: {activated}")

    def visible_stats_panel(current):
        evidence = current.execute_script(
            """
            const panel = document.querySelector('.js-debug-panel');
            if (!panel?.isConnected) return null;
            const style = getComputedStyle(panel);
            const rect = panel.getBoundingClientRect();
            const visible = style.display !== 'none' && style.visibility !== 'hidden'
              && Number.parseFloat(style.opacity || '1') > 0 && rect.width > 0 && rect.height > 0;
            return {
              panelId: panel.id || '',
              visible,
              width: rect.width,
              height: rect.height,
              statsVisible: typeof jsDebugStatsPanelVisible === 'function' && jsDebugStatsPanelVisible(),
              graphPresent: panel.querySelector('[data-js-debug-graph]') !== null,
              visitedSurfaces: Array.isArray(window.__yolomuxBrowserJourneyGate?.visitedSurfaces)
                ? [...window.__yolomuxBrowserJourneyGate.visitedSurfaces]
                : [],
            };
            """
        )
        if not evidence or evidence.get("visible") is not True or evidence.get("statsVisible") is not True:
            return False
        if "stats" not in evidence.get("visitedSurfaces", ()):
            return False
        return evidence

    return WebDriverWait(driver, timeout).until(visible_stats_panel)


def assert_api_journey_error_free(
    observations: Iterable[Mapping[str, Any]],
    *,
    allowlist: Mapping[tuple[str, str, int], str] | None = None,
) -> dict[str, Any]:
    """Gate a derived browser journey on every observed API response."""

    allowed_reasons = {
        (str(method).upper(), str(path), int(status)): str(reason).strip()
        for (method, path, status), reason in dict(allowlist or {}).items()
    }
    empty_reasons = sorted(
        f"{method} {path} {status}"
        for (method, path, status), reason in allowed_reasons.items()
        if not reason
    )
    if empty_reasons:
        raise ValueError(f"API journey allowlist entries require written reasons: {empty_reasons}")
    records = [dict(record) for record in observations if str(record.get("path") or "").startswith("/api/")]
    failures = []
    allowed = []
    in_flight_streams = []
    for record in records:
        method = str(record.get("method") or "GET").upper()
        path = str(record.get("path") or "")
        raw_status = record.get("status")
        try:
            status = int(raw_status or 0)
        except (TypeError, ValueError):
            status = 0
        if 200 <= status < 300:
            continue
        failure = {
            "method": method,
            "path": path,
            "status": status,
            "body": str(record.get("body") or ""),
            "contentType": str(record.get("contentType") or ""),
            "transport": str(record.get("transport") or ""),
            "error": str(record.get("error") or ""),
        }
        # A long-lived SSE/WebSocket route that has neither settled nor reported an error is still
        # connecting, not a failed response. Classify it from the same production route registry the
        # fixture HTTP tracker uses so streaming and finite requests never diverge between the two.
        if raw_status is None and not failure["error"] and not fixture_http_request_is_finite(method, path):
            in_flight_streams.append(failure)
            continue
        reason = allowed_reasons.get((method, path, status))
        if reason:
            allowed.append({**failure, "reason": reason})
        else:
            failures.append(failure)
    evidence = {
        "observedResponseCount": len(records),
        "observedRoutes": sorted({f"{str(record.get('method') or 'GET').upper()} {record.get('path')}" for record in records}),
        "allowedNon2xx": allowed,
        "inFlightStreams": in_flight_streams,
        "unexpectedNon2xx": failures,
    }
    if failures:
        raise AssertionError(f"API journey emitted unexpected non-2xx responses: {json.dumps(evidence, sort_keys=True)}")
    return evidence


FINDER_JOURNEY_INSTRUMENT_SOURCE = """
(() => {
  if (window.__gateFinderJourney) return;
  const originalFetch = window.fetch;
  const fetches = [];
  const outstanding = new Set();
  const parseBody = options => {
    try { return options?.body ? JSON.parse(options.body) : null; } catch (_error) { return null; }
  };
  window.fetch = (input, options = {}) => {
    const url = new URL(String(input), location.href);
    const started = performance.now();
    const record = {path: url.pathname, search: url.search, body: parseBody(options), startedMs: started};
    fetches.push(record);
    const settled = originalFetch(input, options).then(response => {
      record.status = Number(response.status || 0);
      record.elapsedMs = performance.now() - started;
      return response;
    }, error => {
      record.error = String(error && error.message ? error.message : error);
      record.elapsedMs = performance.now() - started;
      throw error;
    });
    outstanding.add(settled);
    // Retire the tracking entry only once the product's own continuation chain has
    // had its microtask turn, so settlement cannot be observed between a resolved
    // response and the expansion state it produces.
    settled.then(() => {}, () => {}).then(() => outstanding.delete(settled));
    return settled;
  };
  const panel = () => document.querySelector('#panel-__finder__');
  const row = path => panel()?.querySelector(`.file-tree-row[data-path="${CSS.escape(path)}"]`) || null;
  const beneath = (path, root) => String(path || '').startsWith(root);
  const listRequests = () => {
    const counts = {};
    for (const fetchRecord of fetches) {
      if (fetchRecord.path === '/api/fs/batch') {
        for (const request of Array.isArray(fetchRecord.body?.requests) ? fetchRecord.body.requests : []) {
          if (request?.type !== 'list') continue;
          const path = String(request.path || '');
          counts[path] = (counts[path] || 0) + 1;
        }
      } else if (fetchRecord.path === '/api/fs/list' || fetchRecord.path === '/api/fs/fast/list') {
        const path = new URLSearchParams(fetchRecord.search).get('path') || '';
        counts[path] = (counts[path] || 0) + 1;
      }
    }
    return counts;
  };
  const resourceRecords = root => {
    const rows = [];
    for (const [key, record] of fileExplorerFsResourceRecords.entries()) {
      const separator = key.indexOf(String.fromCharCode(31));
      const type = separator < 0 ? key : key.slice(0, separator);
      const path = separator < 0 ? '' : key.slice(separator + 1);
      if (!beneath(path, root)) continue;
      rows.push({
        name: `${type}:${path}`,
        generation: Number(record.generation || 0),
        inFlight: record.request !== null && record.request !== undefined,
      });
    }
    rows.sort((left, right) => (left.name < right.name ? -1 : left.name > right.name ? 1 : 0));
    return rows;
  };
  window.__gateFinderJourney = Object.freeze({
    fetches,
    listRequests,
    restore() { window.fetch = originalFetch; },
    rowPresent(path) { return Boolean(row(path)); },
    rowExpansion(path) {
      const target = row(path);
      return target ? String(target.getAttribute('aria-expanded') || '') : null;
    },
    click(path) {
      const target = row(path);
      if (!target) throw new Error(`Finder row is absent: ${path}`);
      target.click();
      return true;
    },
    // Explicit settlement: every fetch this journey issued has resolved, every fs
    // resource beneath the Finder root has accepted its generation (no in-flight
    // request), no expansion is pending, no row is still loading children, and the
    // product's own operation ledger is idle. None of these depend on a rendered frame.
    settlement(root) {
      const lifecycle = window.__yolomuxFixtureLifecycle;
      const operations = lifecycle && typeof lifecycle.operationState === 'function'
        ? lifecycle.operationState()
        : null;
      const records = resourceRecords(root);
      return {
        available: operations !== null,
        outstandingFetches: outstanding.size,
        pendingExpansions: Array.from(fileExplorerPendingExpansions).filter(path => beneath(path, root)).sort(),
        inFlightResources: records.filter(record => record.inFlight).map(record => record.name),
        acceptedGenerations: Object.fromEntries(records.map(record => [record.name, record.generation])),
        interactionGeneration: fileWorkspaceState.interactionGeneration(),
        openGeneration: fileWorkspaceState.fileExplorerOpenGeneration,
        loadingRows: Array.from(panel()?.querySelectorAll('.file-tree-row.loading-children[data-path]') || [])
          .map(node => node.dataset.path)
          .sort(),
        apiPending: operations ? operations.pending : null,
        batchQueued: operations ? Number(operations.batchQueued || 0) : -1,
        batchPending: operations ? Number(operations.batchPending || 0) : -1,
        batchOperations: operations ? Number(operations.batchOperations || 0) : -1,
      };
    },
    snapshot(root, devRoot, subdirectory, nestedProbe) {
      const rows = Array.from(panel()?.querySelectorAll('.file-tree-row[data-path]') || []);
      const counts = {};
      for (const item of rows) counts[item.dataset.path] = (counts[item.dataset.path] || 0) + 1;
      return {
        expanded: Array.from(fileExplorerExpanded).filter(path => beneath(path, root)).sort(),
        pending: Array.from(fileExplorerPendingExpansions).filter(path => beneath(path, root)).sort(),
        syncTargetRecords: Array.from(fileExplorerSyncTargetRecords.entries()).map(([key, record]) => ({
          key,
          expandedPaths: Array.isArray(record?.expandedPaths) ? [...record.expandedPaths] : [],
        })),
        directoryRecordPaths: Array.from(fileExplorerDirectoryRecords.keys()).filter(path => beneath(path, root)).sort(),
        rows: rows.length,
        duplicatePaths: Object.fromEntries(Object.entries(counts).filter(([, count]) => count > 1)),
        devExpanded: row(devRoot)?.getAttribute('aria-expanded') || '',
        subdirectoryExpanded: row(subdirectory)?.getAttribute('aria-expanded') || '',
        devVisible: Boolean(row(devRoot)),
        subdirectoryVisible: Boolean(row(subdirectory)),
        nestedProbeVisible: Boolean(row(nestedProbe)),
      };
    },
  });
})();
"""


def finder_journey_settlement(driver, root: str) -> dict[str, Any]:
    """Read one explicit Finder settlement sample from the instrumented page."""

    state = driver.execute_script("return window.__gateFinderJourney.settlement(arguments[0]);", root)
    if not isinstance(state, Mapping) or state.get("available") is not True:
        raise AssertionError(f"Finder journey instrument is unreachable: {state}")
    return dict(state)


def finder_journey_is_settled(state: Mapping[str, Any]) -> bool:
    """Whether every explicit Finder completion signal has reached its terminal value."""

    return (
        int(state["outstandingFetches"]) == 0
        and state["pendingExpansions"] == []
        and state["inFlightResources"] == []
        and state["loadingRows"] == []
        and state["apiPending"] == []
        and int(state["batchQueued"]) == 0
        and int(state["batchPending"]) == 0
        and int(state["batchOperations"]) == 0
    )


def wait_for_finder_journey_settlement(driver, root: str, *, description: str, timeout: float) -> dict[str, Any]:
    """Settle one Finder interaction on fetch completion and accepted generations.

    The deadline is enforced from Python, so it is reachable no matter how the
    page's frame source behaves; an in-page rAF watchdog is not, because it is
    only evaluated when a frame is delivered and the WebDriver script timeout
    fires first with no journey evidence at all.
    """

    last_state: dict[str, Any] | None = None

    def settled(current):
        nonlocal last_state
        state = finder_journey_settlement(current, root)
        last_state = state
        return state if finder_journey_is_settled(state) else False

    try:
        return WebDriverWait(driver, float(timeout), poll_frequency=0.02).until(settled)
    except TimeoutException as error:
        raise AssertionError(
            f"Finder journey did not settle after {description}: {json.dumps(last_state, sort_keys=True)}"
        ) from error


def run_finder_nested_reexpand_journey(
    driver,
    runtime: GateLiveServer,
    recipe: AgedStateRecipeResult,
    *,
    timeout: float = 35.0,
) -> dict[str, Any]:
    """Drive expand -> nested expand -> collapse -> re-expand without resetting browser caches."""

    root = str(recipe.details["root"])
    dev_root = str(recipe.details["dev_root"])
    subdirectory = str(recipe.details["subdirectory"])
    nested_probe = str(recipe.details["nested_probe"])
    session = str(runtime.tmux.sessions[0])
    state = json.dumps({"finder": {"mode": "files", "rootMode": "fixed", "root": root}}, separators=(",", ":"))
    query = f"/?sessions=files,{quote(session)}&layout=left&tabs=left:files&state={quote(state)}"
    load_gate_browser(driver, runtime, query)
    driver.execute_script(FINDER_JOURNEY_INSTRUMENT_SOURCE)

    def list_requests() -> dict[str, int]:
        return dict(driver.execute_script("return window.__gateFinderJourney.listRequests();") or {})

    def snapshot() -> dict[str, Any]:
        return dict(driver.execute_script(
            "return window.__gateFinderJourney.snapshot(arguments[0], arguments[1], arguments[2], arguments[3]);",
            root,
            dev_root,
            subdirectory,
            nested_probe,
        ))

    def phase(
        name: str,
        target: str,
        expected_rows: Mapping[str, bool],
        expected_expansion: Mapping[str, str | None],
    ) -> dict[str, Any]:
        before = list_requests()
        started = time.monotonic()
        driver.execute_script("window.__gateFinderJourney.click(arguments[0]);", target)
        settlement = wait_for_finder_journey_settlement(driver, root, description=name, timeout=timeout)
        rows = {
            path: bool(driver.execute_script("return window.__gateFinderJourney.rowPresent(arguments[0]);", path))
            for path in expected_rows
        }
        expansion = {
            path: driver.execute_script("return window.__gateFinderJourney.rowExpansion(arguments[0]);", path)
            for path in expected_expansion
        }
        observed = {"rows": rows, "expansion": expansion}
        expected = {"rows": dict(expected_rows), "expansion": dict(expected_expansion)}
        if observed != expected:
            raise AssertionError(
                f"Finder phase {name} settled in the wrong end state: "
                f"{json.dumps({'expected': expected, 'observed': observed, 'settlement': settlement}, sort_keys=True)}"
            )
        after = list_requests()
        return {
            "name": name,
            "elapsedMs": (time.monotonic() - started) * 1000.0,
            "requestCounts": {
                path: count - int(before.get(path, 0))
                for path, count in after.items()
                if count - int(before.get(path, 0)) != 0
            },
            "settlement": settlement,
            "state": snapshot(),
        }

    try:
        boot = wait_for_finder_journey_settlement(driver, root, description="initial Finder root", timeout=timeout)
        if not driver.execute_script("return window.__gateFinderJourney.rowPresent(arguments[0]);", dev_root):
            raise AssertionError(f"Finder root never rendered its dev row: {json.dumps(boot, sort_keys=True)}")
        phases = [
            phase(
                "expand-dev",
                dev_root,
                {dev_root: True, subdirectory: True},
                {dev_root: "true"},
            ),
            phase(
                "open-subdirectory",
                subdirectory,
                {subdirectory: True, nested_probe: True},
                {dev_root: "true", subdirectory: "true"},
            ),
            phase(
                "collapse-dev",
                dev_root,
                {dev_root: True, subdirectory: False, nested_probe: False},
                {dev_root: "false", subdirectory: None},
            ),
            phase(
                "reexpand-dev",
                dev_root,
                {dev_root: True, subdirectory: True, nested_probe: True},
                {dev_root: "true", subdirectory: "true"},
            ),
        ]
        journey_result: dict[str, Any] = {
            "phases": phases,
            "fetches": list(driver.execute_script("return window.__gateFinderJourney.fetches;") or []),
            "final": snapshot(),
        }
    finally:
        driver.execute_script("window.__gateFinderJourney.restore();")
    journey_result["browserErrorEvidence"] = assert_browser_journey_error_free(driver)
    return journey_result


def _serve_gate_live_server(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    make_tmux_webterm_app,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port: HttpPortLease,
    gate_tmux,
) -> Iterable[GateLiveServer]:
    """Shared server lifecycle for bypassed and form-authenticated fixtures."""

    app = make_tmux_webterm_app(tuple(gate_tmux.sessions))
    runtime = start_fixture_live_server(
        monkeypatch,
        app,
        GateLiveServerOptions(
            address=gate_http_port.address,
            thread_name="gate-http-server",
            label="fixture-owned gate",
            clear_server_logs=True,
            pin_jobd_scheduler=True,
        ),
        tmux=gate_tmux,
        paths=gate_runtime_paths,
        port_lease=gate_http_port,
    )
    try:
        yield runtime
    finally:
        fixture_browser = request.node.funcargs.get("browser")
        runtime.finish(
            fixture_browser,
            require_owned_browsers=fixture_browser is not None,
        )


@pytest.fixture
def gate_live_server(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    make_tmux_webterm_app,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port: HttpPortLease,
    gate_tmux,
) -> Iterable[GateLiveServer]:
    """Serve the real app with the existing explicit test-auth bypass."""

    monkeypatch.setenv(TEST_AUTH_BYPASS_ENV, "1")
    yield from _serve_gate_live_server(
        request,
        monkeypatch,
        make_tmux_webterm_app,
        gate_runtime_paths,
        gate_http_port,
        gate_tmux,
    )


@pytest.fixture
def gate_auth_credentials() -> GateAuthCredentials:
    """Provide non-secret credentials scoped to one throwaway gate runtime."""

    return GateAuthCredentials(username="e2e-admin", password="fixture-only-password", role="admin")


@pytest.fixture
def gate_authenticated_live_server(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    make_tmux_webterm_app,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port: HttpPortLease,
    gate_tmux,
    gate_auth_credentials: GateAuthCredentials,
) -> Iterable[GateLiveServer]:
    """Serve the real app with auth enabled and one fixture-owned account."""

    monkeypatch.delenv(TEST_AUTH_BYPASS_ENV, raising=False)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    auth_path = gate_runtime_paths.auth_config_path
    assert common.AUTH_CONFIG_PATH == auth_path
    assert auth_module.AUTH_CONFIG_PATH == auth_path
    user = auth_module.AuthUser(
        username=gate_auth_credentials.username,
        password=gate_auth_credentials.password,
        role=gate_auth_credentials.role,
    )
    auth_module.write_auth_config(auth_path, auth_module.auth_config_text((user,)))
    initialized = auth_module.initialize_auth_config(auth_path)
    assert len(initialized) == 1 and initialized[0].username == gate_auth_credentials.username
    assert auth_module.auth_password_is_hash(initialized[0].password)
    assert not auth_module.test_auth_bypass_enabled()
    yield from _serve_gate_live_server(
        request,
        monkeypatch,
        make_tmux_webterm_app,
        gate_runtime_paths,
        gate_http_port,
        gate_tmux,
    )


def _browser_global_requirements(globals_required: Iterable[str] | Mapping[str, str]) -> dict[str, str]:
    if isinstance(globals_required, Mapping):
        requirements = {str(name): str(expected_type) for name, expected_type in globals_required.items()}
    else:
        requirements = {str(name): "defined" for name in globals_required}
    if any(not name for name in requirements):
        raise ValueError("browser global names must be non-empty")
    return requirements


def wait_for_browser_boot(
    driver,
    *,
    globals_required: Iterable[str] | Mapping[str, str] = (),
    dom_anchors: Iterable[str] = (),
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Wait in-browser until every requested global and DOM anchor exists.

    A mapping may pin JavaScript ``typeof`` values, for example
    ``{"fileEditorItemFor": "function"}``.  The single async browser call uses
    animation frames, so Python does not race bundle boot or add polling sleeps.
    """

    globals_map = _browser_global_requirements(globals_required)
    selectors = tuple(str(selector) for selector in dom_anchors)
    if any(not selector for selector in selectors):
        raise ValueError("DOM anchor selectors must be non-empty")
    timeout_ms = max(0, round(float(timeout) * 1000))
    result = driver.execute_async_script(
        """
        const globalsRequired = arguments[0];
        const selectors = arguments[1];
        const timeoutMs = arguments[2];
        const done = arguments[arguments.length - 1];
        const started = performance.now();
        const resolveGlobal = name => {
          const parts = String(name).split('.').filter(Boolean);
          let value = window;
          if (parts[0] === 'window') parts.shift();
          for (const part of parts) {
            if (value === null || value === undefined || !(part in Object(value))) return {defined: false, type: 'undefined'};
            value = value[part];
          }
          return {defined: value !== undefined, type: typeof value};
        };
        const inspect = () => {
          const missingGlobals = [];
          for (const [name, expectedType] of Object.entries(globalsRequired)) {
            const value = resolveGlobal(name);
            if (!value.defined || (expectedType !== 'defined' && value.type !== expectedType)) {
              missingGlobals.push({name, expectedType, actualType: value.type});
            }
          }
          const missingAnchors = selectors.filter(selector => !document.querySelector(selector));
          return {missingGlobals, missingAnchors, readyState: document.readyState, url: location.href};
        };
        const poll = () => {
          const state = inspect();
          if (!state.missingGlobals.length && !state.missingAnchors.length) {
            done({...state, ready: true, elapsedMs: performance.now() - started});
            return;
          }
          if (performance.now() - started >= timeoutMs) {
            done({...state, ready: false, elapsedMs: performance.now() - started});
            return;
          }
          requestAnimationFrame(poll);
        };
        poll();
        """,
        globals_map,
        list(selectors),
        timeout_ms,
    )
    if not result.get("ready"):
        raise AssertionError(
            "browser boot readiness timed out "
            f"after {result.get('elapsedMs', timeout_ms):.0f}ms: "
            f"missing globals={result.get('missingGlobals', [])}, "
            f"missing DOM anchors={result.get('missingAnchors', [])}, "
            f"readyState={result.get('readyState')!r}, url={result.get('url')!r}"
        )
    return result


def run_when_browser_ready(
    driver,
    script: str,
    *script_args: Any,
    globals_required: Iterable[str] | Mapping[str, str] = (),
    dom_anchors: Iterable[str] = (),
    timeout: float = 20.0,
):
    """Wait for a script's exact dependencies, then execute it."""

    wait_for_browser_boot(
        driver,
        globals_required=globals_required,
        dom_anchors=dom_anchors,
        timeout=timeout,
    )
    return driver.execute_script(script, *script_args)


def computed_styles(driver, target, properties: Iterable[str], *, pseudo: str | None = None) -> dict[str, str]:
    """Read named computed-style properties from a selector or WebElement."""

    property_names = tuple(str(name) for name in properties)
    if not property_names or any(not name for name in property_names):
        raise ValueError("at least one non-empty computed-style property is required")
    result = driver.execute_script(
        """
        const target = arguments[0];
        const properties = arguments[1];
        const pseudo = arguments[2];
        const node = typeof target === 'string' ? document.querySelector(target) : target;
        if (!node) return null;
        const style = getComputedStyle(node, pseudo || null);
        return Object.fromEntries(properties.map(name => [name, style.getPropertyValue(name)]));
        """,
        target,
        list(property_names),
        pseudo,
    )
    if result is None:
        raise AssertionError(f"computed-style target was not found: {target!r}")
    return {str(name): str(value) for name, value in result.items()}


def assert_computed_style(
    driver,
    target,
    expected: Mapping[str, str | Callable[[str], bool]],
    *,
    pseudo: str | None = None,
) -> dict[str, str]:
    """Assert actual computed styles, accepting exact values or predicates."""

    if not expected:
        raise ValueError("at least one expected computed style is required")
    actual = computed_styles(driver, target, expected, pseudo=pseudo)
    mismatches = []
    for name, expectation in expected.items():
        value = actual[name]
        if callable(expectation):
            matches = bool(expectation(value))
            wanted = getattr(expectation, "__name__", repr(expectation))
        else:
            matches = value == expectation
            wanted = repr(expectation)
        if not matches:
            mismatches.append(f"{name}={value!r}, expected {wanted}")
    if mismatches:
        raise AssertionError(f"computed style mismatch for {target!r}: {'; '.join(mismatches)}")
    return actual

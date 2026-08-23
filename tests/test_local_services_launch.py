import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event

import pytest

from yolomux_lib.infra.host_identity import LocalProcessReason
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib import approvald
from yolomux_lib import jobd
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services.client import LocalServiceClient
from yolomux_lib.local_services.client import TransportFailure
from yolomux_lib.local_services import client as local_service_client_mod
from yolomux_lib.local_services import runtime
from yolomux_lib.local_services.registry import LOCAL_SERVICE_RETIRE_FORCE_SECONDS
from yolomux_lib.local_services.registry import LOCAL_SERVICE_RETIRE_GRACE_SECONDS
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.local_services.registry import parse_ps_cpu_seconds
from yolomux_lib.stats_current import client as stats_current_client
from yolomux_lib.stats_current import service as stats_current_service
from yolomux_lib.stats_current import storage as stats_current_storage
from tests.gate_harness import FixtureLocalServiceProcess
from tests.gate_harness import stop_fixture_local_service_process
from tests.serving_process import pid_is_serving
from tests.helpers.local_service_records import FixtureLeaseRecordBuilder
from tests.helpers.local_service_records import FixtureLocalServiceRecordBuilder
from tests.helpers.local_service_records import FixtureProcessRecordBuilder
from tests.helpers.local_service_records import rmtree_within


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_local_rpc_service_exits_when_its_launching_process_dies(tmp_path):
    socket_path = tmp_path / "parent-bound.sock"
    lock_path = tmp_path / "parent-bound.lock"
    child_pid_path = tmp_path / "child.pid"
    service_script = """
import multiprocessing
import sys
from pathlib import Path
from yolomux_lib.local_services.runtime import run_local_rpc_service

stop = multiprocessing.get_context("spawn").Event()
raise SystemExit(run_local_rpc_service(
    socket_path=Path(sys.argv[1]),
    lock_path=Path(sys.argv[2]),
    service_name="parent-bound-test",
    stop_event=stop,
    handle=lambda _request, _body: ({"ok": True}, b""),
    on_idle=lambda: False,
    on_client=lambda: None,
))
"""
    launcher_script = """
import subprocess
import signal
import sys
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]])
Path(sys.argv[4]).write_text(str(child.pid), encoding="utf-8")
signal.pause()
"""
    launcher = subprocess.Popen(
        [sys.executable, "-c", launcher_script, service_script, str(socket_path), str(lock_path), str(child_pid_path)],
        cwd=REPO_ROOT,
    )
    child_pid = 0
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if child_pid_path.exists() and socket_path.exists():
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.01)
        assert child_pid > 1 and socket_path.exists()

        launcher.kill()
        launcher.wait(timeout=2.0)
        deadline = time.monotonic() + 3.0
        while registry_mod.pid_is_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert registry_mod.pid_is_alive(child_pid) is False
        assert socket_path.exists() is False
    finally:
        if launcher.poll() is None:
            launcher.kill()
            launcher.wait(timeout=2.0)
        if child_pid > 1 and registry_mod.pid_is_alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_registry_prunes_only_unlocked_stale_runtime_lock_generations(tmp_path):
    service_dir = tmp_path / "services"
    active_socket = service_dir / "statsd.p24s7.active.sock"
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", active_socket.name, 24),
        socket_path=active_socket,
        service_dir=service_dir,
    )
    registry._socket_path = active_socket
    service_dir.mkdir(parents=True)
    active_lock = active_socket.with_suffix(".lock")
    stale_lock = service_dir / "statsd.p24s7.stale.lock"
    held_lock = service_dir / "statsd.p24s7.held.lock"
    foreign_lock = service_dir / "jobd.p31.foreign.lock"
    for path in (active_lock, stale_lock, held_lock, foreign_lock, registry.lock_path):
        path.write_text("", encoding="utf-8")
    held_fd = os.open(held_lock, os.O_RDWR)
    fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        removed = registry._prune_stale_runtime_locks()
    finally:
        fcntl.flock(held_fd, fcntl.LOCK_UN)
        os.close(held_fd)

    assert removed == [stale_lock]
    assert active_lock.exists()
    assert held_lock.exists()
    assert foreign_lock.exists()
    assert registry.lock_path.exists()


def test_registry_prunes_runtime_locks_before_adopting_a_recently_healthy_service(tmp_path, monkeypatch):
    service_dir = tmp_path / "services"
    active_socket = service_dir / "statsd.p24s7.active.sock"
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", active_socket.name, 24),
        socket_path=active_socket,
        service_dir=service_dir,
    )
    service_dir.mkdir(parents=True)
    active_socket.with_suffix(".lock").write_text("", encoding="utf-8")
    stale_lock = service_dir / "statsd.p24s7.stale.lock"
    stale_lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(registry, "recently_healthy", lambda: True)
    monkeypatch.setattr(registry, "_arm_adopted_reaper", lambda: None)

    assert registry.ensure_started() is True
    assert stale_lock.exists() is False

    later_stale_lock = service_dir / "statsd.p24s7.later.lock"
    later_stale_lock.write_text("", encoding="utf-8")
    assert registry.ensure_started() is True
    assert later_stale_lock.exists() is True


def test_stats_current_client_prunes_runtime_locks_before_its_healthy_shortcut(tmp_path, monkeypatch):
    service_dir = tmp_path / "services"
    socket_path = service_dir / "statsd.p24s7.active.sock"
    client = stats_current_client.StatsCurrentClient(socket_path, tmp_path / "stats-v7.sqlite3")
    registry = client._transport.registry
    service_dir.mkdir(parents=True)
    socket_path.with_suffix(".lock").write_text("", encoding="utf-8")
    stale_lock = service_dir / "statsd.p24s7.stale.lock"
    stale_lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(registry, "recently_healthy", lambda: True)

    assert client.ensure_started() is True
    assert stale_lock.exists() is False


def test_query_if_running_does_not_launch_an_absent_local_service(tmp_path, monkeypatch):
    client = LocalServiceClient("fixture", "tests.fixture", tmp_path / "fixture.sock")
    absent_error = FileNotFoundError(2, "socket is absent")
    launch_calls = []

    monkeypatch.setattr(
        client,
        "_request_once",
        lambda *_args, **_kwargs: (
            {"ok": False, "error": "socket is absent", "_transport_error": "absent"},
            b"",
            TransportFailure(absent_error, "traceback", "status", "r-status", 1.0),
        ),
    )
    monkeypatch.setattr(client.registry, "ensure_started", lambda: launch_calls.append(True) or True)

    response = client.request_if_running({"action": "status"})

    assert response["_transport_error"] == "absent"
    assert launch_calls == []


def test_sealed_local_service_client_rejects_late_demand_without_rpc_or_respawn(tmp_path, monkeypatch):
    client = LocalServiceClient("fixture", "tests.fixture", tmp_path / "fixture.sock")
    rpc_calls = []
    spawn_calls = []
    logged_errors = []
    absent_error = FileNotFoundError(2, "socket is absent")

    def absent_rpc(*_args, **_kwargs):
        rpc_calls.append(True)
        return {
            "ok": False,
            "error": "socket is absent",
            "_transport_error": "absent",
        }, b"", TransportFailure(absent_error, "traceback", "late-submit", "r-late", 1.0)

    monkeypatch.setattr(client, "_request_once", absent_rpc)
    monkeypatch.setattr(client, "_emit_transport_error", logged_errors.append)
    monkeypatch.setattr(client.registry, "_spawn", lambda: spawn_calls.append(True))

    client.registry.seal_starts()
    response = client.request({"action": "late-submit"})

    assert response == {
        "ok": False,
        "error": "fixture is stopping",
        "status": "unavailable",
        "terminal": True,
        "_transport_error": "stopped",
    }
    assert client.ensure_started() is False
    assert rpc_calls == [True]
    assert spawn_calls == []
    assert logged_errors == []


@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_process_record_diagnostic_rejects_a_real_unreaped_zombie():
    child = os.fork()
    if child == 0:
        os._exit(0)
    try:
        deadline = time.monotonic() + 2
        while registry_mod.process_state(child) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert registry_mod.process_state(child) == "Z"
        identity = current_host_identity()
        record = identity.process_record_fields(pid=child, start_identity=registry_mod.process_start_identity(child))
        assert registry_mod.process_record_diagnostic(record).current is False
        assert registry_mod.process_record_diagnostic(record, table=registry_mod.bounded_process_table()).current is False
    finally:
        os.waitpid(child, 0)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_serving_predicate_rejects_a_real_unreaped_zombie():
    """The teardown liveness oracle must read an unreaped zombie as NOT serving.

    This is the class that reddens the post-TERM teardown test under load: a child
    that has exited but not been reaped keeps its ``/proc/<pid>/stat`` start ticks,
    so the retired ``process_start_identity`` read returns a truthy identity and a
    dead child reads as alive. Forge that state directly and prove the old read
    mis-classifies it while the shared serving-member predicate does not, then prove
    a genuinely live process still reads as serving so a real survivor is not
    ignored.
    """

    child = os.fork()
    if child == 0:  # pragma: no cover - child never returns
        os._exit(0)
    try:
        deadline = time.monotonic() + 2.0
        while registry_mod.process_state(child) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert registry_mod.process_state(child) == "Z", registry_mod.process_state(child)

        # Linux retains readable start ticks for a zombie; macOS libproc deliberately does not.
        if sys.platform != "darwin":
            assert registry_mod.process_start_identity(child)
        # Green for the shared predicate: a zombie is not serving.
        assert pid_is_serving(child) is False
        # Fails closed: this live test process is still serving.
        assert pid_is_serving(os.getpid()) is True
    finally:
        os.waitpid(child, 0)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="adopted-child reaping is POSIX-only")
def test_adopted_demand_daemon_is_reaped_not_left_a_zombie(tmp_path):
    """A daemon adopted over a healthy socket, holding no Popen, must still be wait()-ed.

    The generation that adopts a running daemon by pinging its socket never held a Popen for
    it -- an earlier generation spawned it and dropped the handle. Before this fix the
    healthy-socket early returns in ``ensure_started`` armed no reaper, so when the daemon
    idle-exited nothing wait()-ed it and it lingered as an unreaped zombie: the live 7771
    signature where an idle-exited demand daemon read as "errored". Arm the adopted reaper
    against a real child, idle-exit it, and prove the web process reaps it and retires its
    record instead of leaving a zombie behind.
    """
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - child never returns
        os.close(write_fd)
        try:
            os.read(read_fd, 1)  # block until the parent closes the write end
        finally:
            os._exit(0)
    os.close(read_fd)
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("watchd", "yolomux_lib.watchd", "watchd.sock", 1),
    )
    registry._write_record({
        **current_host_identity().process_record_fields(
            pid=child,
            start_identity=registry_mod.process_start_identity(child),
        ),
        "service": "watchd",
        "socket": str(registry.socket_path),
        "protocol_version": 1,
        "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
    })
    try:
        # No Popen held: exactly the adopted case the fresh-spawn reaper never covers.
        assert registry.process is None
        registry._arm_adopted_reaper()
        assert registry._adopted_reaper_pid == child
        # Idle-exit the adopted child; the parked reaper must reap it and retire the record.
        os.close(write_fd)
        deadline = time.monotonic() + 5.0
        while registry.record_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not registry.record_path.exists(), "the adopted reaper must retire the record it named"
        with pytest.raises(ChildProcessError):
            os.waitpid(child, 0)  # already reaped by the adopted reaper
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass


@pytest.mark.skipif(not hasattr(os, "fork"), reason="adopted-child recovery is POSIX-only")
def test_ensure_started_reaps_an_adopted_child_that_already_exited(tmp_path, monkeypatch):
    """A daemon that exits before post-reexec adoption must not block replacement."""

    child = os.fork()
    if child == 0:  # pragma: no cover - child never returns
        os._exit(0)
    spawned = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 24),
        popen=lambda *args, **kwargs: spawned.append(True) or _NeverExitingProcess(),
    )
    try:
        deadline = time.monotonic() + 2.0
        while registry_mod.process_state(child) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert registry_mod.process_state(child) == "Z"
        registry._write_record({
            **current_host_identity().process_record_fields(
                pid=child,
                start_identity=registry_mod.process_start_identity(child),
            ),
            "service": "statsd",
            "socket": str(registry.socket_path),
            "protocol_version": 24,
            "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
            "launcher_pid": os.getpid(),
        })
        monkeypatch.setattr(registry, "_request", lambda *args, **kwargs: {})

        assert registry.ensure_started() is False
        assert spawned == [True]
        assert not registry.record_path.exists()
        with pytest.raises(ChildProcessError):
            os.waitpid(child, os.WNOHANG)
    finally:
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass


def test_process_spawn_generation_reads_exact_named_environment_value(tmp_path):
    marker = "a" * 32
    proc_root = tmp_path / "proc"
    process_root = proc_root / "43221"
    process_root.mkdir(parents=True)
    (process_root / "environ").write_bytes(
        b"LONG_PREFIX=" + (b"x" * 4096) + b"\0"
        + f"{registry_mod.LOCAL_SERVICE_SPAWN_GENERATION_ENV}={marker}".encode("ascii")
        + b"\0"
    )

    assert registry_mod.process_spawn_generation(43221, proc_root=proc_root) == marker
    key = registry_mod.LOCAL_SERVICE_SPAWN_GENERATION_ENV.encode("ascii") + b"="
    for environ in (
        key + (b"a" * 31) + b"\xff\0",
        key + marker.encode("ascii") + b"\0" + key + marker.encode("ascii") + b"\0",
        key + marker.encode("ascii") + b"\0" + key + (b"c" * 32) + b"\0",
    ):
        (process_root / "environ").write_bytes(environ)
        assert registry_mod.process_spawn_generation(43221, proc_root=proc_root) is None


def test_process_spawn_generation_rejects_invalid_duplicates_and_uses_structured_portable_fallback(tmp_path):
    marker = "b" * 32
    proc_root = tmp_path / "missing-proc"
    key = registry_mod.LOCAL_SERVICE_SPAWN_GENERATION_ENV.encode("ascii") + b"="

    assert registry_mod.process_spawn_generation(
        43222,
        proc_root=proc_root,
        darwin_environment_reader=lambda _pid: (key + marker.encode("ascii") + b"-foreign",),
    ) is None
    assert registry_mod.process_spawn_generation(
        43222,
        proc_root=proc_root,
        darwin_environment_reader=lambda _pid: (b"OTHER=value", key + marker.encode("ascii")),
    ) == marker
    for entries in (
        (key + (b"a" * 31) + b"\xff",),
        (key + marker.encode("ascii"), key + marker.encode("ascii")),
        (key + marker.encode("ascii"), key + (b"c" * 32)),
    ):
        assert registry_mod.process_spawn_generation(
            43222,
            proc_root=proc_root,
            darwin_environment_reader=lambda _pid, values=entries: values,
        ) is None


def test_process_spawn_generation_readable_proc_without_marker_never_falls_back(tmp_path):
    proc_root = tmp_path / "proc"
    process_root = proc_root / "43223"
    process_root.mkdir(parents=True)
    (process_root / "environ").write_bytes(b"OTHER=value\0")
    fallback_calls = []

    assert registry_mod.process_spawn_generation(
        43223,
        proc_root=proc_root,
        darwin_environment_reader=lambda pid: fallback_calls.append(pid) or (
            f"{registry_mod.LOCAL_SERVICE_SPAWN_GENERATION_ENV}={'d' * 32}".encode("ascii"),
        ),
    ) is None
    assert fallback_calls == []


def test_darwin_process_environment_parser_excludes_argv_generation_token(tmp_path):
    owned_marker = b"d" * 32
    argv_marker = b"e" * 32
    key = registry_mod.LOCAL_SERVICE_SPAWN_GENERATION_ENV.encode("ascii") + b"="
    environment = (
        b"LONG_PREFIX=" + (b"x" * 4096),
        key + owned_marker,
        b"OTHER=value",
    )
    argv = (b"python3", b"", key + argv_marker)
    int_size = registry_mod.ctypes.sizeof(registry_mod.ctypes.c_int)
    raw = (
        len(argv).to_bytes(int_size, registry_mod.sys.byteorder, signed=True)
        + b"/usr/bin/python3\0\0\0"
        + b"\0".join(argv)
        + b"\0\0"
        + b"\0".join(environment)
        + b"\0"
    )

    parsed = registry_mod.parse_darwin_process_environment(raw)

    assert parsed == environment
    assert key + argv_marker not in parsed
    assert registry_mod.process_spawn_generation(
        43224,
        proc_root=tmp_path / "missing-proc",
        darwin_environment_reader=lambda _pid: parsed,
    ) == owned_marker.decode("ascii")


@pytest.mark.parametrize(
    ("environment", "expected"),
    (
        ((b"YOLOMUX_LOCAL_SERVICE_SPAWN_GENERATION_PREFIX=" + (b"f" * 32),), None),
        ((b"YOLOMUX_LOCAL_SERVICE_SPAWN_GENERATION=" + (b"a" * 32),) * 2, None),
        ((b"YOLOMUX_LOCAL_SERVICE_SPAWN_GENERATION=" + (b"a" * 32), b"OTHER=" + (b"z" * 8192)), "a" * 32),
    ),
)
def test_darwin_process_environment_generation_rows_fail_closed(tmp_path, environment, expected):
    int_size = registry_mod.ctypes.sizeof(registry_mod.ctypes.c_int)
    raw = (
        (1).to_bytes(int_size, registry_mod.sys.byteorder, signed=True)
        + b"/usr/bin/python3\0\0"
        + b"python3\0\0"
        + b"\0".join(environment)
        + b"\0"
    )
    parsed = registry_mod.parse_darwin_process_environment(raw)

    assert registry_mod.process_spawn_generation(
        43225,
        proc_root=tmp_path / "missing-proc",
        darwin_environment_reader=lambda _pid: parsed,
    ) == expected


@pytest.mark.parametrize(
    "raw",
    (
        b"\x00",
        (-1).to_bytes(registry_mod.ctypes.sizeof(registry_mod.ctypes.c_int), registry_mod.sys.byteorder, signed=True) + b"raw-secret\0",
        (2).to_bytes(registry_mod.ctypes.sizeof(registry_mod.ctypes.c_int), registry_mod.sys.byteorder, signed=True) + b"/bin/tool\0\0arg-zero\0raw-secret",
        (1).to_bytes(registry_mod.ctypes.sizeof(registry_mod.ctypes.c_int), registry_mod.sys.byteorder, signed=True) + b"/bin/tool\0\0arg-zero\0\0RAW_SECRET=value",
    ),
)
def test_darwin_process_environment_malformed_buffers_do_not_leak_contents(raw):
    with pytest.raises(ValueError) as failure:
        registry_mod.parse_darwin_process_environment(raw)

    assert "raw-secret" not in str(failure.value).lower()


def test_pyproject_package_discovery_includes_local_service_subpackages():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.packages.find]" in pyproject
    assert 'py-modules = ["yolomux"]' in pyproject
    assert 'include = ["tools*", "yolomux_lib*"]' in pyproject
    assert 'packages = ["yolomux_lib"]' not in pyproject
    for module_name in (
        "tools.auto_approve_tmux",
        "tools.tmux_wall",
        "yolomux_lib.local_services",
        "yolomux_lib.local_services.rpc",
        "yolomux_lib.stats_current.service",
        "yolomux_lib.jobd",
        "yolomux_lib.approvald",
    ):
        assert importlib.util.find_spec(module_name) is not None


def test_registry_spawn_uses_current_interpreter_module_and_quoted_args(tmp_path, monkeypatch):
    monkeypatch.delenv("YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS", raising=False)
    starts = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        starts.append((args, kwargs))
        return FakeProcess()

    socket_path = tmp_path / ("state with spaces " * 8).strip() / "jobd.sock"
    registry = LocalServiceRegistry(
        socket_path.parent,
        LocalServiceSpec(
            "jobd",
            "yolomux_lib.jobd",
            socket_path.name,
            jobd.JOBD_PROTOCOL_VERSION,
            idle_seconds=12.5,
            extra_args=("--workers", "1"),
        ),
        socket_path=socket_path,
        popen=fake_popen,
    )

    assert registry._spawn() is not None
    args, kwargs = starts[0]

    assert args[:3] == [sys.executable, "-m", "yolomux_lib.jobd"]
    assert kwargs["env"][registry_mod.LOCAL_SERVICE_SPAWN_GENERATION_ENV]
    inherited_paths = kwargs["env"]["PYTHONPATH"].split(os.pathsep)
    assert all(path in inherited_paths for path in sys.path if path)
    assert args[args.index("--socket") + 1] == str(registry.socket_path)
    assert args[args.index("--idle-seconds") + 1] == "12.5"
    assert args[-2:] == ["--workers", "1"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert Path(kwargs["stdout"].name) == registry.stderr_path
    assert kwargs["stdout"].closed is True
    assert kwargs["stderr"] is subprocess.STDOUT


@pytest.mark.parametrize(
    "artifact_location",
    ("generated-outside", "generated-inside", "contained-explicit", "outside-explicit", "forged-exact"),
)
def test_registry_spawn_rebases_only_generated_artifacts_outside_current_root(tmp_path, monkeypatch, artifact_location):
    starts = []

    class FakeProcess:
        def poll(self):
            return None

    root = Path("/tmp") / f"yls-{os.getpid()}"
    generated_prefix = str(tmp_path / "bootstrap-python-cache")
    for key in (
        "YOLOMUX_CONFIG_DIR",
        "YOLOMUX_STATE_DIR",
        "YOLOMUX_CACHE_DIR",
        "YOLOMUX_RUNTIME_DIR",
        "YOLOMUX_CODEX_HOME",
        "CODEX_HOME",
        "YOLOMUX_HOST_ARTIFACT_DIR",
        "PYTHONPYCACHEPREFIX",
        "YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("YOLOMUX_ROOT", str(root))
    if artifact_location == "generated-outside":
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", generated_prefix)
        monkeypatch.setenv("YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX", generated_prefix)
        monkeypatch.setattr(sys, "pycache_prefix", generated_prefix)
    elif artifact_location == "generated-inside":
        contained_generated = str(root / "generated-python-cache")
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", contained_generated)
        monkeypatch.setenv("YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX", contained_generated)
        monkeypatch.setattr(sys, "pycache_prefix", contained_generated)
    elif artifact_location == "contained-explicit":
        monkeypatch.setenv("YOLOMUX_HOST_ARTIFACT_DIR", str(root / "artifacts"))
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(root / "explicit-python-cache"))
        monkeypatch.delenv("YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX", raising=False)
    elif artifact_location == "outside-explicit":
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", generated_prefix)
        monkeypatch.setenv("YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX", str(tmp_path / "different-prefix"))
    else:
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", generated_prefix)
        monkeypatch.setenv("YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX", generated_prefix)
        monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "different-prefix"))
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
        popen=lambda args, **kwargs: starts.append((args, kwargs)) or FakeProcess(),
    )

    if artifact_location in {"outside-explicit", "forged-exact"}:
        with pytest.raises(ValueError, match="PYTHONPYCACHEPREFIX resolves outside YOLOMUX_ROOT"):
            registry._spawn()
        assert starts == []
        return

    assert registry._spawn() is not None
    child = starts[0][1]["env"]
    assert Path(child["PYTHONPYCACHEPREFIX"]).is_relative_to(root)
    assert Path(child["PIP_CACHE_DIR"]).is_relative_to(root)
    assert Path(child["NPM_CONFIG_CACHE"]).is_relative_to(root)
    assert Path(child["COVERAGE_FILE"]).is_relative_to(root)
    if artifact_location == "generated-outside":
        assert child["PYTHONPYCACHEPREFIX"] != generated_prefix
        assert child["YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX"] == child["PYTHONPYCACHEPREFIX"]
    elif artifact_location == "generated-inside":
        assert child["PYTHONPYCACHEPREFIX"] == str(root / "generated-python-cache")
        assert child["YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX"] == child["PYTHONPYCACHEPREFIX"]
    else:
        assert child["YOLOMUX_HOST_ARTIFACT_DIR"] == str(root / "artifacts")
        assert child["PYTHONPYCACHEPREFIX"] == str(root / "explicit-python-cache")
        assert "YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX" not in child


def test_registry_spawn_honors_isolated_idle_override(tmp_path, monkeypatch):
    starts = []

    class FakeProcess:
        def poll(self):
            return None

    monkeypatch.setenv("YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS", "0.5")
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION, idle_seconds=60),
        popen=lambda args, **kwargs: starts.append((args, kwargs)) or FakeProcess(),
    )

    assert registry._spawn() is not None
    args, _kwargs = starts[0]
    assert args[args.index("--idle-seconds") + 1] == "0.5"


def test_registry_spawn_captures_durable_session_ownership(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 43230

        def poll(self):
            return None

    monkeypatch.setattr(registry_mod.uuid, "uuid4", lambda: type("Generation", (), {"hex": "a" * 32})())
    monkeypatch.setattr(registry_mod, "process_start_identity", lambda pid: "ps:portable-start" if pid == 43230 else None)
    monkeypatch.setattr(registry_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(registry_mod.os, "getsid", lambda pid: pid)
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
        popen=lambda _args, **_kwargs: FakeProcess(),
    )

    process = registry._spawn()

    assert process is not None
    assert registry.spawn_ownership == registry_mod.SpawnProcessOwnership(
        leader_pid=43230,
        process_group=43230,
        session_id=43230,
        generation_marker="a" * 32,
        member_identities=((43230, "ps:portable-start"),),
    )


def test_registry_refreshes_spawn_members_only_while_original_leader_matches(tmp_path, monkeypatch):
    generation_marker = "a" * 32
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
    )
    registry.spawn_ownership = registry_mod.SpawnProcessOwnership(
        leader_pid=43231,
        process_group=43231,
        session_id=43231,
        generation_marker=generation_marker,
        member_identities=((43231, "proc:1235"),),
    )
    current = {
        43231: registry_mod.ProcessTableEntry(1, 43231, 0.0, f"python -X{generation_marker}", 1235, 43231, "proc:1235"),
        43232: registry_mod.ProcessTableEntry(43231, 43231, 0.0, f"python -X{generation_marker}", 1236, 43231, "proc:1236"),
    }
    monkeypatch.setattr(registry_mod, "bounded_process_table", lambda: current)
    monkeypatch.setattr(
        registry_mod,
        "process_spawn_generation",
        lambda pid: generation_marker if pid in current and "foreign" not in current[pid].command else "b" * 32,
    )

    refreshed = registry.refresh_spawn_ownership()
    assert refreshed is not None
    assert refreshed.member_identities == ((43231, "proc:1235"), (43232, "proc:1236"))

    current = {
        43231: registry_mod.ProcessTableEntry(1, 43231, 0.0, "foreign leader", 9999, 43231, "proc:9999"),
        43233: registry_mod.ProcessTableEntry(43231, 43231, 0.0, "foreign worker", 9998, 43231, "proc:9998"),
    }
    retained = registry.refresh_spawn_ownership()
    assert retained is refreshed
    assert retained.member_identities == ((43231, "proc:1235"), (43232, "proc:1236"))


def test_registry_first_descendant_discovery_survives_leader_exit(tmp_path, monkeypatch):
    generation_marker = "a" * 32
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
    )
    registry.spawn_ownership = registry_mod.SpawnProcessOwnership(
        leader_pid=43234,
        process_group=43234,
        session_id=43234,
        generation_marker=generation_marker,
        member_identities=((43234, "proc:1237"),),
    )
    monkeypatch.setattr(registry_mod, "bounded_process_table", lambda: {
        43235: registry_mod.ProcessTableEntry(1, 43234, 0.0, f"python -X{generation_marker} worker", 1238, 43234, "proc:1238"),
        43236: registry_mod.ProcessTableEntry(43235, 43234, 0.0, f"python -X{generation_marker} grandchild", 1239, 43234, "proc:1239"),
        43237: registry_mod.ProcessTableEntry(1, 43234, 0.0, f"python -X{generation_marker} wrong-session", 1240, 99999, "proc:1240"),
        43238: registry_mod.ProcessTableEntry(1, 99999, 0.0, f"python -X{generation_marker} wrong-group", 1241, 43234, "proc:1241"),
    })
    monkeypatch.setattr(registry_mod, "process_spawn_generation", lambda pid: generation_marker)

    refreshed = registry.refresh_spawn_ownership()

    assert refreshed is not None
    assert refreshed.member_identities == ((43235, "proc:1238"), (43236, "proc:1239"))


def test_registry_absent_leader_rejects_numeric_group_reuse_by_foreign_generation(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
    )
    ownership = registry_mod.SpawnProcessOwnership(
        leader_pid=43239,
        process_group=43239,
        session_id=43239,
        generation_marker="a" * 32,
        member_identities=((43239, "proc:1242"),),
    )
    registry.spawn_ownership = ownership
    monkeypatch.setattr(registry_mod, "bounded_process_table", lambda: {
        43240: registry_mod.ProcessTableEntry(
            1,
            43239,
            0.0,
            "python -Xyolomux_local_service_generation=generation-b foreign-worker",
            1243,
            43239,
            "proc:1243",
        ),
    })
    monkeypatch.setattr(registry_mod, "process_spawn_generation", lambda _pid: "b" * 32)

    proof = registry.refresh_spawn_ownership_proof()
    assert proof is not None
    assert proof.ownership is ownership
    assert proof.group_exists is True
    assert proof.owned_member_identities == ()
    assert registry.refresh_spawn_ownership() is ownership
    assert registry.spawn_ownership.member_identities == ((43239, "proc:1242"),)


def test_registry_rejects_recycled_retained_child_identity(tmp_path, monkeypatch):
    generation_marker = "a" * 32
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", jobd.JOBD_PROTOCOL_VERSION),
    )
    ownership = registry_mod.SpawnProcessOwnership(
        leader_pid=43241,
        process_group=43241,
        session_id=43241,
        generation_marker=generation_marker,
        member_identities=((43242, "proc:1244"),),
    )
    registry.spawn_ownership = ownership
    monkeypatch.setattr(registry_mod, "bounded_process_table", lambda: {
        43242: registry_mod.ProcessTableEntry(1, 43241, 0.0, f"python -X{generation_marker}", 9999, 43241, "proc:9999"),
    })
    monkeypatch.setattr(registry_mod, "process_spawn_generation", lambda _pid: generation_marker)

    assert registry.refresh_spawn_ownership() is ownership
    assert registry.spawn_ownership.member_identities == ((43242, "proc:1244"),)


def test_registry_first_refresh_after_real_leader_exit_adopts_only_inherited_generation(tmp_path):
    child_pid_file = tmp_path / "retained-child.pid"
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "generation-fixture",
            "tests.fixtures.local_service_generation_descendant",
            "generation.sock",
            1,
            extra_args=("--child-pid-file", str(child_pid_file)),
        ),
    )
    process = None
    ownership = None
    try:
        process = registry._spawn()
        assert process is not None
        initial_ownership = registry.spawn_ownership
        assert initial_ownership is not None
        assert initial_ownership.member_identities == ((process.pid, registry_mod.process_start_identity(process.pid)),)
        assert process.wait(timeout=5) == 0
        child_pid, grandchild_pid = (
            int(value)
            for value in child_pid_file.read_text(encoding="utf-8").split(",")
        )
        assert child_pid != process.pid
        assert grandchild_pid not in {process.pid, child_pid}

        ownership = registry.refresh_spawn_ownership()

        assert ownership is not None
        member_pids = {pid for pid, _start_identity in ownership.member_identities}
        assert process.pid not in member_pids
        assert child_pid in member_pids
        assert grandchild_pid in member_pids
        assert all(
            registry_mod.process_spawn_generation(pid) == ownership.generation_marker
            for pid in member_pids
        )
        stop_fixture_local_service_process(
            FixtureLocalServiceProcess(registry, process, ownership),
            label="retained inherited-generation descendant",
        )
    finally:
        if registry.spawn_ownership is not None:
            ownership = registry.refresh_spawn_ownership() or registry.spawn_ownership
        if ownership is not None:
            live_members = [
                pid
                for pid, start_identity in ownership.member_identities
                if registry_mod.process_start_identity(pid) == start_identity
                and registry_mod.process_spawn_generation(pid) == ownership.generation_marker
            ]
            if live_members:
                os.killpg(ownership.process_group, signal.SIGKILL)


def test_registry_forced_failure_before_first_refresh_cleans_inherited_generation(tmp_path):
    child_pid_file = tmp_path / "forced-failure-child.pid"
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "generation-fixture",
            "tests.fixtures.local_service_generation_descendant",
            "generation.sock",
            1,
            extra_args=("--child-pid-file", str(child_pid_file)),
        ),
    )
    process = registry._spawn()
    assert process is not None
    initial_ownership = registry.spawn_ownership
    assert initial_ownership is not None
    marker = initial_ownership.generation_marker
    process_group = initial_ownership.process_group
    assert process.wait(timeout=5) == 0

    with pytest.raises(RuntimeError, match="forced before refresh"):
        try:
            raise RuntimeError("forced before refresh")
        finally:
            cleanup_ownership = registry.refresh_spawn_ownership() or registry.spawn_ownership
            assert cleanup_ownership is not None
            stop_fixture_local_service_process(
                FixtureLocalServiceProcess(registry, process, cleanup_ownership),
                label="forced pre-refresh inherited-generation cleanup",
            )

    survivors = [
        pid
        for pid, entry in registry_mod.bounded_process_table().items()
        if entry.pgid == process_group
        and registry_mod.process_spawn_generation(pid) == marker
    ]
    assert survivors == []


@pytest.mark.skipif(not hasattr(os, "fork"), reason="post-TERM descendant fixture is POSIX-only")
def test_fixture_teardown_refreshes_generation_authority_after_term(tmp_path):
    child_pid_file = tmp_path / "post-term-child.pid"
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "generation-fixture",
            "tests.fixtures.local_service_generation_descendant",
            "generation.sock",
            1,
            extra_args=("--child-pid-file", str(child_pid_file), "--spawn-on-term"),
        ),
    )
    process = registry._spawn()
    assert process is not None
    ownership = registry.spawn_ownership
    assert ownership is not None
    assert ownership.member_identities == ((process.pid, registry_mod.process_start_identity(process.pid)),)
    deadline = time.monotonic() + 2.0
    while (
        not child_pid_file.is_file()
        or child_pid_file.read_text(encoding="utf-8") != "ready"
    ) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child_pid_file.read_text(encoding="utf-8") == "ready"

    stop_fixture_local_service_process(
        FixtureLocalServiceProcess(registry, process, ownership),
        label="post-TERM inherited-generation descendant",
    )

    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    # A raw ``process_start_identity`` read keeps returning the child's start ticks
    # while it lingers as an unreaped zombie, so under load teardown finishes before
    # the reaper runs and a dead child reads as alive. Route the liveness check
    # through the shared serving-member predicate, which excludes zombies exactly as
    # production's ``bounded_process_table`` does. A genuinely live child still reads
    # as serving, so a real survivor is not ignored.
    assert not pid_is_serving(child_pid)


def test_transport_diagnostics_returns_total_and_per_exception_counters(monkeypatch):
    monkeypatch.setattr(registry_mod, "_TRANSPORT_TEARDOWNS_TOTAL", 0)
    monkeypatch.setattr(registry_mod, "_TRANSPORT_TEARDOWNS_BY_EXCEPTION", {})

    registry_mod.record_transport_teardown("TimeoutError")
    registry_mod.record_transport_teardown("FileNotFoundError")
    registry_mod.record_transport_teardown("TimeoutError")

    assert registry_mod.transport_diagnostics() == {
        "teardowns_total": 3,
        "teardowns_by_exception": {"FileNotFoundError": 1, "TimeoutError": 2},
    }


def test_registry_real_ensure_started_preserves_generation_proof_through_cleanup(tmp_path, monkeypatch):
    monkeypatch.delenv("YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS", raising=False)
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "jobd",
            "yolomux_lib.jobd",
            "jobd.sock",
            jobd.JOBD_PROTOCOL_VERSION,
            idle_seconds=30,
            extra_args=("--workers", "1"),
        ),
    )

    process = None
    ownership = None
    cleanup_complete = False
    try:
        assert registry.ensure_started() is True
        process = registry.process
        ownership = registry.refresh_spawn_ownership()
        assert process is not None
        assert ownership is not None
        assert len(ownership.generation_marker) == 32
        assert ownership.member_identities
        process_entry = registry_mod.bounded_process_table().get(process.pid)
        assert process_entry is not None, {
            "poll": process.poll(),
            "state": registry_mod.process_state(process.pid),
            "identity": registry_mod.process_start_identity(process.pid),
        }
        assert registry_mod.process_spawn_generation(process.pid) == ownership.generation_marker

        stop_fixture_local_service_process(
            FixtureLocalServiceProcess(registry, process, ownership),
            label="real local-service generation lifecycle",
        )
        cleanup_complete = True
        assert process.poll() is not None
    finally:
        if not cleanup_complete and registry.spawn_ownership is not None:
            ownership = registry.refresh_spawn_ownership() or registry.spawn_ownership
        if not cleanup_complete and process is not None and ownership is not None:
            stop_fixture_local_service_process(
                FixtureLocalServiceProcess(registry, process, ownership),
                label="real local-service generation lifecycle fallback",
            )


def test_long_default_socket_fallback_keeps_registry_lock_out_of_tmp(tmp_path, monkeypatch):
    state_dir = tmp_path / ("long-state-segment-" * 8)
    monkeypatch.setattr(approvald.common, "RUNTIME_DIR", state_dir)

    client = approvald.ApprovalClient()

    # `safe_socket_path`'s length fallback nests inside its own private digest-named
    # directory now, never a bare file directly under `/tmp` -- a caller deriving a
    # sibling path (e.g. a `.service.json` record) must never inherit `/tmp` itself as
    # `.parent`. See yolomux_lib/local_services/rpc.py:safe_socket_path.
    assert client.socket_path.parent != Path("/tmp")
    assert client.socket_path.parent.parent == Path("/tmp")
    expected_service_dir = state_dir / "services"
    assert client.registry.service_dir == expected_service_dir
    assert client.registry.lock_path.parent == expected_service_dir


def test_registry_names_the_guard_that_blocked_a_start_instead_of_failing_silently(tmp_path):
    """A start refused by the record guards must still report why.

    Live signature this covers: watchd absent with a 0-byte ``watchd.stderr.log``
    and no ``watchd.service.json``. The record named a process the registry may
    not retire, so ``ensure_started`` returned False before ``_spawn`` and left
    no reason anywhere a caller could read.
    """
    spawns = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.missing", "fixture.sock", 1),
        popen=lambda *args, **kwargs: spawns.append(args),
    )
    identity = current_host_identity()
    live_pid = os.getpid()
    record = identity.process_record_fields(
        pid=live_pid,
        start_identity=registry_mod.process_start_identity(live_pid),
    )
    record.update({
        "service": "fixture",
        "module": "tests.missing",
        "socket": str(registry.socket_path),
        "protocol_version": 1,
        "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
        "pid": live_pid,
    })
    registry.record_path.parent.mkdir(parents=True, exist_ok=True)
    registry.record_path.write_text(json.dumps(record), encoding="utf-8")

    # The recorded process is alive and current, so the stale-record guard
    # refuses removal and no child may be spawned.
    assert registry.healthy() is False
    assert registry._remove_stale_record() is False
    assert registry.ensure_started() is False
    assert spawns == []
    assert registry.stderr_path.exists() is False

    status = registry.status()
    assert status["failure_reason"] == (
        f"fixture start blocked by remove_stale_record "
        f"(record_pid={live_pid}, reason=current_local_process)"
    )
    assert registry.failure_response()["reason"] == status["failure_reason"]
    # A blocked start ran no child, so the spawn-exit latch stays untouched.
    assert status["terminal_failure"] is False
    assert status["start_exit_count"] == 0
    assert status["last_exit_code"] is None


def test_registry_captures_bounded_stderr_and_latches_repeated_start_exits(tmp_path):
    starts = []
    now = [100.0]

    class FailedProcess:
        def poll(self):
            return 2

    def failing_popen(args, **kwargs):
        starts.append(args)
        kwargs["stdout"].write(b"Traceback\nMigrationError: unsupported retired database\n")
        kwargs["stdout"].flush()
        return FailedProcess()

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "missing.module", "statsd.sock", 1),
        popen=failing_popen,
        clock=lambda: now[0],
        sleep=lambda _seconds: None,
    )

    for expected in range(1, registry_mod.LOCAL_SERVICE_START_EXIT_LIMIT + 1):
        assert registry.ensure_started() is False
        status = registry.status()
        assert status["start_exit_count"] == expected
        assert status["last_exit_code"] == 2
        assert status["failure_reason"] == (
            "statsd exited (2): MigrationError: unsupported retired database"
        )
        now[0] = status["next_start_at"] + 0.001

    assert registry.stderr_path.read_text(encoding="utf-8").splitlines() == [
        "Traceback",
        "MigrationError: unsupported retired database",
    ]
    assert registry.status()["terminal_failure"] is True
    assert registry.failure_response()["terminal"] is True
    assert registry.ensure_started() is False
    assert len(starts) == registry_mod.LOCAL_SERVICE_START_EXIT_LIMIT

    registry.retry()

    assert registry.status()["terminal_failure"] is False
    assert registry.status()["start_exit_count"] == 0
    assert registry.status()["failure_reason"] == ""
    assert registry.ensure_started() is False
    assert len(starts) == registry_mod.LOCAL_SERVICE_START_EXIT_LIMIT + 1


def _identity_probe_registry(tmp_path, monkeypatch, *, leader_pid, identities, status_response):
    """A registry whose spawned child stays not-ready inside its (zero-length) startup window so

    ensure_started reaches the ONE final identity-bearing status probe. ``identities`` is consumed
    once per ``process_start_identity`` read: the first read is the spawn-time capture, the next is
    the probe -- so a two-value sequence models a reused-pid imposter (the pid is alive but its
    start-identity changed) and a one-value constant models a pid that is still exactly what we
    spawned. ``status_response`` is what the single post-deadline ``status`` RPC returns.

    Returns the registry plus three recorders: the spawn log, the list of every ``_request`` made
    AFTER spawn -- so the negative-count assertion isolates the post-deadline probe from the
    pre-spawn pings -- and the list of reasons passed to the REAL Error producer ``_mark_failure``.
    """
    starts = []
    now = [100.0]
    spawned = [False]
    post_spawn_calls = []
    mark_failure_reasons = []

    class SlowChild:
        pid = leader_pid

        def poll(self):
            return None

        def wait(self):
            Event().wait()

    reads = list(identities)

    def next_identity(pid):
        if pid != leader_pid:
            return None
        return reads[0] if len(reads) == 1 else reads.pop(0)

    monkeypatch.setattr(registry_mod, "process_start_identity", next_identity)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: pid == leader_pid)
    monkeypatch.setattr(registry_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(registry_mod.os, "getsid", lambda pid: pid)

    def popen(*_args, **_kwargs):
        starts.append(True)
        spawned[0] = True
        return SlowChild()

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 1, start_timeout_seconds=0.0),
        popen=popen,
        clock=lambda: now[0],
        sleep=lambda _seconds: None,
    )

    # Record every _request made after spawn. A `ping` stays not-ready so the freshly spawned
    # child never becomes ready inside the zero-length window; the single `status` returns the
    # crafted identity-bearing response the final probe classifies. Because the window is empty
    # and no pre-deadline path issues a `status` (an unhealthy ping short-circuits publication),
    # the recorded post-spawn calls are exactly the one post-deadline probe.
    def spy_request(method, payload=None, timeout=0.2, protocol_version=None):
        if spawned[0]:
            post_spawn_calls.append((method, timeout))
        if method == "status":
            return dict(status_response)
        return {"ok": False}

    monkeypatch.setattr(registry, "_request", spy_request)

    # Assert on the terminal-episode lifecycle via the REAL producer, not a bare counter: wrap
    # `_mark_failure` so a duplicated or absent Error is provable, while its real side effects
    # (start_exit_count, terminal latch, backoff) still run.
    real_mark_failure = registry._mark_failure

    def spy_mark_failure(reason="", *, exit_code=None, exited_before_ready=False):
        mark_failure_reasons.append(reason)
        real_mark_failure(reason, exit_code=exit_code, exited_before_ready=exited_before_ready)

    monkeypatch.setattr(registry, "_mark_failure", spy_mark_failure)
    return registry, starts, post_spawn_calls, mark_failure_reasons


def test_registry_final_identity_probe_accepts_late_valid_and_fails_only_a_reused_pid_imposter(tmp_path, monkeypatch):
    """W7 clause 4: ONE post-deadline identity-bearing `status` probe, classified three ways.

    A live-past-deadline child whose single `status` response is valid for the EXACT spawned
    pid/start-identity/protocol is late-valid startup -- accepted (ensure_started True), record
    published, NO Error, exactly one generation, and exactly ONE post-deadline RPC which is
    `status` (never `ping`+`status`). A status that is otherwise healthy but carries the WRONG
    pid/start-identity -- a reused-pid imposter, or a peer answering our socket -- is a terminal
    episode: ensure_started False, exactly one Error via the real producer, and no duplicate Error
    or respawn on re-entry while backoff ownership holds. A merely not-ok/dropped status from our
    own still-alive leader is transient not-ready: held for retry with NO Error.
    """
    # --- Late-valid: the ONE status response is valid AND names the exact spawned leader. ---
    late_valid, late_starts, late_calls, late_failures = _identity_probe_registry(
        tmp_path / "late",
        monkeypatch,
        leader_pid=44100,
        identities=["ps:spawned"],
        status_response={
            "ok": True,
            "version": 1,
            "service": "fixture",
            "pid": 44100,
            "process_start_identity": "ps:spawned",
        },
    )
    assert late_valid.ensure_started() is True
    # Exactly ONE post-deadline RPC and it is `status` -- no ping first.
    assert late_calls == [("status", 0.2)]
    # The valid identity-bearing response was published as the durable record.
    assert int(late_valid._read_record().get("pid") or 0) == 44100
    # No Error via the real producer, no terminal/backoff latch, exactly one generation spawned.
    assert late_failures == []
    assert late_valid.failures == 0
    assert late_valid._failure_reason == ""
    assert late_valid._terminal_failure is False
    assert late_starts == [True]

    # --- Wrong-identity: a peer answers with a VALID healthy status naming a DIFFERENT pid. ---
    # The OS leader we spawned is alive and ours (constant identity), so this exercises the new
    # status-response identity gate, not merely the OS-pid proof.
    peer, peer_starts, peer_calls, peer_failures = _identity_probe_registry(
        tmp_path / "peer",
        monkeypatch,
        leader_pid=44300,
        identities=["ps:spawned"],
        status_response={
            "ok": True,
            "version": 1,
            "service": "fixture",
            "pid": 44999,
            "process_start_identity": "ps:foreign",
        },
    )
    assert peer.ensure_started() is False
    # Exactly ONE post-deadline `status` RPC.
    assert peer_calls == [("status", 0.2)]
    # Exactly one terminal-episode Error via the real producer.
    assert len(peer_failures) == 1
    assert "status identity mismatch" in peer_failures[0]
    assert peer.failures == 1
    assert peer._terminal_failure is True
    assert peer_starts == [True]
    # Re-entry is forbidden by backoff ownership: no second Error, no second generation.
    assert peer.ensure_started() is False
    assert len(peer_failures) == 1
    assert peer.failures == 1
    assert peer_starts == [True]

    # --- Reused-pid imposter: the pid is alive but now carries a DIFFERENT start-identity. ---
    imposter, imposter_starts, imposter_calls, imposter_failures = _identity_probe_registry(
        tmp_path / "imposter",
        monkeypatch,
        leader_pid=44200,
        identities=["ps:spawned", "ps:reused"],
        status_response={"ok": True, "version": 1, "service": "fixture", "pid": 44200},
    )
    assert imposter.ensure_started() is False
    assert imposter_calls == [("status", 0.2)]
    assert len(imposter_failures) == 1
    assert "start-identity mismatch (reused-pid imposter)" in imposter_failures[0]
    assert imposter.failures == 1
    assert imposter_starts == [True]

    # --- Transient not-ready: our own leader is alive but the status is simply not-ok. ---
    transient, transient_starts, transient_calls, transient_failures = _identity_probe_registry(
        tmp_path / "transient",
        monkeypatch,
        leader_pid=44400,
        identities=["ps:spawned"],
        status_response={"ok": False},
    )
    assert transient.ensure_started() is False
    assert transient_calls == [("status", 0.2)]
    # No Error: the child is ours and alive, just not published yet -- held for bounded retry.
    assert transient_failures == []
    assert transient.failures == 0
    assert transient._failure_reason == ""
    assert transient._terminal_failure is False
    assert transient.next_start_at == 100.0 + registry_mod.LOCAL_SERVICE_BACKOFF_SECONDS
    assert transient_starts == [True]


@pytest.mark.parametrize(
    ("status_response", "reason"),
    [
        ({"ok": True, "version": 1, "service": "otherd", "pid": 44500, "process_start_identity": "ps:spawned"}, "service_name_mismatch"),
        ({"ok": True, "version": 2, "service": "fixture", "pid": 44500, "process_start_identity": "ps:spawned"}, "protocol_version_mismatch"),
    ],
)
def test_registry_final_status_classifies_explicit_wire_identity_mismatch_as_terminal(
    tmp_path, monkeypatch, status_response, reason,
):
    registry, starts, calls, failures = _identity_probe_registry(
        tmp_path,
        monkeypatch,
        leader_pid=44500,
        identities=["ps:spawned"],
        status_response=status_response,
    )

    assert registry.ensure_started() is False
    assert calls == [("status", 0.2)]
    assert failures == [f"fixture startup status identity mismatch: {reason}"]
    assert registry.failure_response()["terminal"] is True
    assert starts == [True]


def test_registry_final_status_requires_response_identity_and_does_not_substitute_os_identity(tmp_path, monkeypatch):
    registry, starts, calls, failures = _identity_probe_registry(
        tmp_path,
        monkeypatch,
        leader_pid=44600,
        identities=["ps:spawned"],
        status_response={"ok": True, "version": 1, "service": "fixture", "pid": 44600},
    )

    assert registry.ensure_started() is False
    assert calls == [("status", 0.2)]
    assert registry._read_record() == {}
    assert failures == []
    assert registry.failure_response()["terminal"] is False
    assert starts == [True]


def test_local_service_client_emits_one_error_for_one_terminal_startup_episode(tmp_path, monkeypatch):
    registry, starts, calls, _failures = _identity_probe_registry(
        tmp_path,
        monkeypatch,
        leader_pid=44700,
        identities=["ps:spawned"],
        status_response={
            "ok": True,
            "version": 1,
            "service": "otherd",
            "pid": 44700,
            "process_start_identity": "ps:spawned",
        },
    )
    client = LocalServiceClient("fixture", "tests.fixture", tmp_path / "fixture.sock")
    client.registry = registry
    emitted = []
    monkeypatch.setattr(local_service_client_mod, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))

    assert client.ensure_started() is False
    assert client.ensure_started() is False

    assert starts == [True]
    assert calls == [("status", 0.2)]
    assert len(emitted) == 1
    args, kwargs = emitted[0]
    assert args[:2] == ("error", "local-service:fixture")
    assert "service_name_mismatch" in args[2]
    assert (kwargs["category"], kwargs["event"], kwargs["delivery"]) == ("startup", "startup", "terminal")


def test_parse_ps_cpu_seconds_covers_ps_time_shapes():
    assert parse_ps_cpu_seconds("0:00.00") == 0.0
    assert parse_ps_cpu_seconds("1:30") == 90.0
    assert parse_ps_cpu_seconds("2:03:04") == 7384.0
    assert parse_ps_cpu_seconds("1-02:03:04") == 93784.0
    assert parse_ps_cpu_seconds("") is None
    assert parse_ps_cpu_seconds("garbage") is None


def test_registry_resources_reads_cpu_and_rss_via_ps_without_proc(tmp_path, monkeypatch):
    # macOS/BSD have no /proc; the per-service CPU/RSS probe was Linux-only, so
    # every service reported `—` and the Daemons load chart was empty on macOS.
    monkeypatch.setattr(registry_mod.platform, "system", lambda: "Darwin")
    outputs = iter(["  2048   0:01.00\n", "  4096   0:03.00\n"])

    class FakeCompleted:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **_kwargs):
        assert args[0] == "ps" and args[-1] == "4321"
        return FakeCompleted(next(outputs))

    monkeypatch.setattr(registry_mod.subprocess, "run", fake_run)
    clock_values = iter([100.0, 101.0])
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", 1),
        clock=lambda: next(clock_values),
    )

    first = registry.resources(4321)
    assert first == {"cpu_percent": None, "rss_bytes": 2048 * 1024}
    second = registry.resources(4321)
    # 2 cumulative CPU seconds elapsed over 1 wall-clock second -> 200%.
    assert second["cpu_percent"] == 200.0
    assert second["rss_bytes"] == 4096 * 1024
    assert registry.resources(0) == {"cpu_percent": None, "rss_bytes": None}


def test_registry_resources_returns_none_when_ps_reports_no_such_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod.platform, "system", lambda: "Darwin")

    class FakeCompleted:
        stdout = ""

    monkeypatch.setattr(registry_mod.subprocess, "run", lambda *_args, **_kwargs: FakeCompleted())
    registry = LocalServiceRegistry(tmp_path, LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", 1))

    assert registry.resources(999999) == {"cpu_percent": None, "rss_bytes": None}


def test_registry_resources_for_pids_aggregates_verified_workers_and_resets_on_membership_change(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod.platform, "system", lambda: "Darwin")
    outputs = iter([
        "100 1 10 00:01.00\n101 100 20 00:02.00\n102 999 40 00:50.00\n",
        "100 1 11 00:02.00\n101 100 21 00:04.00\n",
        "100 1 12 00:03.00\n",
    ])

    class FakeCompleted:
        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(registry_mod.subprocess, "run", lambda *_args, **_kwargs: FakeCompleted(next(outputs)))
    clock_values = iter([100.0, 101.0, 102.0])
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", 1),
        clock=lambda: next(clock_values),
    )

    first = registry.resources_for_pids(100, [101, 102])
    second = registry.resources_for_pids(100, [101])
    third = registry.resources_for_pids(100, [])

    assert first == {"cpu_percent": None, "rss_bytes": 30 * 1024, "process_count": 2}
    # Parent + direct worker gained three cumulative CPU seconds in one wall second.
    assert second == {"cpu_percent": 300.0, "rss_bytes": 32 * 1024, "process_count": 2}
    # The worker exited, so a different membership deliberately starts a fresh CPU baseline.
    assert third == {"cpu_percent": None, "rss_bytes": 12 * 1024, "process_count": 1}


def test_registry_health_request_identifies_expected_service_protocol(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 5),
    )
    captured = {}

    def fake_request(_path, envelope, **_kwargs):
        captured.update(envelope.payload)
        return {"ok": True, "version": 5, "pid": 1}, b""

    monkeypatch.setattr("yolomux_lib.local_services.registry.request", fake_request)

    assert registry.healthy() is True
    assert captured == {"action": "ping", "protocol_version": 5}


def test_registry_recent_health_cache_removes_per_action_ping_status_fanout(tmp_path, monkeypatch):
    now = [100.0]
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 5),
        clock=lambda: now[0],
    )
    requests = []

    # A service answers with its own live pid; a pid that names no process
    # cannot publish an identity record, so the fixture uses a real one.
    service_pid = os.getpid()

    def fake_request(_path, envelope, **_kwargs):
        requests.append(envelope.method)
        if envelope.method == "ping":
            return {"ok": True, "version": 5, "pid": service_pid}, b""
        return {"ok": True, "version": 5, "pid": service_pid, "started_at": 1}, b""

    monkeypatch.setattr("yolomux_lib.local_services.registry.request", fake_request)

    assert registry.ensure_started() is True
    first_requests = list(requests)
    assert registry.ensure_started() is True
    assert requests == first_requests
    now[0] += 1.1
    assert registry.ensure_started() is True
    assert requests.count("ping") == 2


class _NeverExitingProcess:
    """A spawn stand-in with no pid, so `_spawn` returns it before ownership capture."""

    def poll(self):
        return None

    def wait(self):
        # A live replacement daemon: `_reap_exited_child`/`_start_child_reaper`
        # block here until the child exits, which this fixture never does, so
        # model the real Popen contract instead of leaving `.wait()` missing.
        Event().wait()


def _publication_registry(tmp_path, *, clock, spawned, protocol_version=1):
    return LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", protocol_version),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        popen=lambda *args, **kwargs: spawned.append(True) or _NeverExitingProcess(),
    )


def test_registry_never_publishes_a_record_from_a_lost_post_ping_status(tmp_path, monkeypatch):
    """One dropped status RPC after a successful ping must not brick the service.

    Shipping 0.7.0 wrote `_record_from_status({})` -- a durable record carrying
    pid 0 -- and still told the caller the service had started.  `invalid_pid`
    is not removable on the same host and boot, so every later start was
    refused by `remove_stale_record` forever.  The publication validator must
    refuse that record, invalidate health, and continue through bounded startup.
    """
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    live = [True]

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        if not live[0]:
            return {}
        if method == "ping":
            return {"ok": True, "version": 1, "pid": 4242}
        return {}

    monkeypatch.setattr(registry, "_request", fake_request)

    first = registry.ensure_started()
    published = registry._read_record()
    # The daemon that answered ping is gone; only the durable record survives.
    live[0] = False
    registry.invalidate_rpc_health()
    second = registry.ensure_started()
    failure_reason = registry.status()["failure_reason"]

    measured = {
        "first_reported_started": first,
        "record_published": registry.record_path.exists(),
        "record_pid": int(published.get("pid") or 0),
        "second_reported_started": second,
        "permanently_blocked": "blocked by remove_stale_record" in failure_reason,
    }
    assert measured == {
        "first_reported_started": False,
        "record_published": False,
        "record_pid": 0,
        "second_reported_started": False,
        "permanently_blocked": False,
    }, {**measured, "failure_reason": failure_reason}
    assert spawned == [True]


def test_registry_recovers_a_lost_status_on_the_next_attempt_with_a_real_record(tmp_path, monkeypatch):
    """A transient status loss costs one attempt, never the service."""
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    status_losses = [1]

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        if method == "ping":
            return {"ok": True, "version": 1, "pid": os.getpid()}
        if status_losses[0] > 0:
            status_losses[0] -= 1
            return {}
        return {"ok": True, "version": 1, "pid": os.getpid(), "started_at": 1.0}

    monkeypatch.setattr(registry, "_request", fake_request)

    assert registry.ensure_started() is True
    record = registry._read_record()
    assert record["pid"] == os.getpid()
    assert record["service"] == "fixture"
    assert record["protocol_version"] == 1
    assert registry._record_process_diagnostic(record).current is True


@pytest.mark.parametrize(
    "status",
    (
        {},
        {"ok": False, "version": 1, "pid": os.getpid()},
        {"ok": True, "version": 1, "pid": 0},
        {"ok": True, "version": 1, "pid": 1},
        {"ok": True, "version": 2, "pid": os.getpid()},
        {"ok": True, "version": 1, "pid": os.getpid(), "service": "jobd"},
        {"ok": True, "version": 1, "pid": 4242},
    ),
    ids=(
        "lost_status",
        "status_not_ok",
        "invalid_pid_zero",
        "invalid_pid_init",
        "wrong_protocol_version",
        "wrong_service_name",
        "unusable_start_identity",
    ),
)
def test_registry_publication_validator_refuses_every_unprovable_status(tmp_path, status):
    """No unprovable status may reach the durable record, and none may report success."""
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)

    assert registry._publish_record(status) is False
    assert registry.record_path.exists() is False
    assert registry.recently_healthy() is False


@pytest.mark.parametrize(
    "status",
    (
        {"ok": True, "version": 1, "pid": os.getpid(), "started_at": 2.0},
        # statsd reports a nested `service` diagnostics object, not a service name.
        {"ok": True, "version": 1, "pid": os.getpid(), "service": {"pid": os.getpid(), "healthy": True}},
        {"ok": True, "version": 1, "pid": os.getpid(), "service": "fixture"},
    ),
    ids=("plain", "statsd_shaped_service_object", "matching_service_name"),
)
def test_registry_publication_validator_accepts_a_proven_status(tmp_path, status):
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)

    assert registry._publish_record(status) is True
    record = registry._read_record()
    assert record["pid"] == os.getpid()
    assert record["service"] == "fixture"
    assert registry._record_process_diagnostic(record).current is True


def test_registry_writes_the_service_record_through_exactly_one_validator(tmp_path):
    """Divergent copies of this write are the defect; keep one publication owner."""
    source = (REPO_ROOT / "yolomux_lib" / "local_services" / "registry.py").read_text(encoding="utf-8")
    call_lines = [
        line.strip()
        for line in source.splitlines()
        if "_write_record(" in line and "def _write_record" not in line
    ]
    assert call_lines == ["self._write_record(record)"]
    assert source.count("def _publish_record(") == 1


def test_registry_write_record_refuses_typed_when_its_directory_vanishes_mid_write(tmp_path, monkeypatch):
    """Reproduce the current first failing boundary, not the retracted theory.

    A historical incident reached ``atomic_write_text(...); path.chmod(mode)``
    after a pytest temp directory had already been deleted by test teardown.
    That is a plain same-process synchronous write race with no child process
    involved anywhere in this path -- not evidence of an orphan child that
    needed reaping. ``_write_record`` must refuse typed through the existing
    refusal surface instead of letting a raw ``OSError`` escape.
    """
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    record = {
        **current_host_identity().process_record_fields(
            pid=os.getpid(),
            start_identity=registry_mod.process_start_identity(os.getpid()),
        ),
        "service": "fixture",
        "socket": str(registry.socket_path),
        "protocol_version": 1,
        "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
    }

    real_replace = registry_mod.os.replace

    def replace_then_remove_directory(src, dst):
        # Simulates a directory disappearing between the temp-file publish
        # (os.replace) and the trailing os.chmod inside atomic_write_text --
        # the exact historical incident line, reproduced deterministically.
        real_replace(src, dst)
        rmtree_within(registry.record_path.parent, tmp_path)

    monkeypatch.setattr(registry_mod.os, "replace", replace_then_remove_directory)

    assert registry._write_record(record) is False
    assert "record write failed" in registry._record_refusal_reason
    assert registry.record_path.exists() is False
    assert list(tmp_path.glob("**/*.tmp")) == []


def test_rmtree_within_refuses_the_shared_system_temp_root(tmp_path):
    # Regression: the three "directory vanishes mid-write" tests above previously called
    # bare `shutil.rmtree(registry.record_path.parent, ignore_errors=True)`. A real
    # `safe_socket_path` length-fallback bug made `record_path.parent` resolve to the
    # literal system temp root instead of somewhere owned, and `ignore_errors=True` let
    # that wrong target delete silently, wiping every other worker's shared basetemp.
    # `rmtree_within` must refuse loudly instead of ever touching that root or a
    # known-shared directory (pytest's own `pytest-of-<user>` basetemp, this repo's own
    # `yop-*` per-process TMPDIR root) even when the target is not literally under `tmp_path`
    # -- `safe_socket_path`'s own private digest-named fallback directory is NOT one of
    # these shared names and remains a legitimate, safe deletion target (covered by the
    # three sibling tests above, which construct exactly that case under deep xdist paths).
    system_temp_root = Path(tempfile.gettempdir())

    with pytest.raises(AssertionError, match="shared directory"):
        rmtree_within(system_temp_root, tmp_path)

    assert system_temp_root.exists() is True


def test_rmtree_within_refuses_a_named_shared_directory_outside_owned_root(tmp_path):
    shared_lookalike = Path(tempfile.gettempdir()) / "pytest-of-someone-else"

    with pytest.raises(AssertionError, match="shared directory"):
        rmtree_within(shared_lookalike, tmp_path)


def test_registry_publish_record_surfaces_the_same_write_race_as_a_typed_refusal(tmp_path, monkeypatch):
    """The same race surfaces through ``_publish_record()`` as a typed
    refusal, not a raw uncaught exception escaping the caller.
    """
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    status = {"ok": True, "version": 1, "pid": os.getpid(), "started_at": 2.0}

    real_replace = registry_mod.os.replace
    replace_calls = []

    def replace_then_remove_directory(src, dst):
        real_replace(src, dst)
        replace_calls.append(True)
        rmtree_within(registry.record_path.parent, tmp_path)

    monkeypatch.setattr(registry_mod.os, "replace", replace_then_remove_directory)

    result = registry._publish_record(status)

    assert result is False
    assert replace_calls == [True]
    assert registry.record_path.exists() is False
    assert "record write failed" in registry.status()["failure_reason"]
    assert registry.recently_healthy() is False


def test_registry_fences_a_real_child_after_its_fixture_directory_is_removed(tmp_path, monkeypatch):
    """A genuinely live child (real PID, real process), not a mock, whose
    service directory is removed while it is still running.

    The two tests above reproduce the same-process write-race boundary with
    ``os.getpid()`` standing in for the record's identity; this test spawns a
    real, separate child process to prove two properties the historical
    "orphan child" theory conflated: (1) no further ``atomic_write_text`` call
    happens for this registry once its directory is gone, and (2) the real
    child does not silently vanish -- it must surface through the existing
    untracked-process diagnostic surface rather than being invisible, and
    must never be signalled without ledger-proven identity (Rejected
    Shortcuts: no broad host sweeper, no signal without authority).
    """
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    # `untracked_local_service_processes` (the only surface with kill
    # authority feeding it) requires the live command to literally carry a
    # `yolomux_lib.` `-m` module plus `--socket <path>`, so a bare sleep
    # child would always be invisible to it for reasons unrelated to this
    # regression -- mirror the real launch argv shape instead (the trailing
    # tokens after `-c SCRIPT` become inert extra sys.argv entries to the
    # interpreter, so this still just sleeps).
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "-m",
            "yolomux_lib.fixture_stand_in",
            "--socket",
            str(registry.socket_path),
        ]
    )
    try:
        record = {
            **current_host_identity().process_record_fields(
                pid=child.pid,
                start_identity=registry_mod.process_start_identity(child.pid),
            ),
            "service": "fixture",
            "socket": str(registry.socket_path),
            "protocol_version": 1,
            "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
            "launcher_pid": os.getpid(),
        }
        assert registry._write_record(record) is True
        assert registry.record_path.exists() is True

        # The directory is gone before the *next* write attempt starts (not
        # mid-write like the two tests above) -- the real child keeps running
        # underneath a service directory that no longer exists.
        rmtree_within(registry.record_path.parent, tmp_path)
        assert registry.record_path.parent.exists() is False

        write_calls: list[str] = []
        real_atomic_write_text = registry_mod.atomic_write_text

        def counting_atomic_write_text(path, text, mode=None):
            write_calls.append(str(path))
            return real_atomic_write_text(path, text, mode=mode)

        monkeypatch.setattr(registry_mod, "atomic_write_text", counting_atomic_write_text)

        status = {"ok": True, "version": 1, "pid": child.pid, "started_at": 1.0}
        result = registry._publish_record(status)

        # Untracked-process discovery is diagnostics-only and must find the
        # real child by process-table + module/socket evidence alone, since
        # the ledger record that would have proven authority is gone.
        table = registry_mod.bounded_process_table()
        tracked = registry_mod.tracked_local_service_groups(registry.record_path.parent, table)
        untracked = registry_mod.untracked_local_service_processes(registry.record_path.parent, table, tracked)
        untracked_pids = {int(row["pid"]) for row in untracked if "pid" in row}

        measured = {
            "publish_after_removal_result": result,
            "write_calls_after_removal": write_calls,
            "child_still_alive": child.poll() is None,
            "tracked_groups_after_removal": tracked,
        }
        # This directly answers the coordinator's rejected-audit correction:
        # measure, don't assume, whether `_write_record` silently resurrects
        # the removed directory and writes into it, or whether the removal is
        # durably fenced. This owner already proved one successful publish
        # into this directory before it was removed, so a later write from
        # the SAME owner must refuse rather than `mkdir` the directory back
        # into existence -- zero atomic_write_text calls, zero chmod, and the
        # directory stays gone.
        assert write_calls == [], measured
        assert result is False, measured
        assert registry.record_path.parent.exists() is False, measured

        # Whichever branch above is true in this codebase, the real live
        # child must never be dropped from the untracked-process diagnostic
        # surface, and tracked_local_service_groups (the only surface with
        # kill authority) must not fabricate authority over a record it did
        # not itself just prove -- i.e. no signal escapes this test.
        assert child.pid in untracked_pids or child.pid in {
            int(pid) for group in tracked for pid in group.get("member_pids", ())
        }, measured
    finally:
        child.terminate()
        deadline = time.monotonic() + 5.0
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_registry_reclaims_a_poisoned_invalid_pid_record_as_record_only_cleanup(tmp_path, monkeypatch):
    """Already-poisoned 0.7.0 installs must recover without any process action.

    A pid of 0 or 1 cannot name a service process, so discarding the record can
    neither orphan nor kill anything: no signal, no adoption, no socket unlink.
    """
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    registry._write_record({
        **current_host_identity().process_record_fields(pid=0, start_identity=""),
        "service": "fixture",
        "socket": str(registry.socket_path),
        "protocol_version": 1,
        "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
    })
    registry.socket_path.parent.mkdir(parents=True, exist_ok=True)
    registry.socket_path.touch()
    signals = []
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))
    monkeypatch.setattr(registry, "_request", lambda *args, **kwargs: {})

    assert registry._remove_stale_record() is True
    assert registry.record_path.exists() is False
    assert registry.socket_path.exists() is True
    assert signals == []

    assert registry.ensure_started() is False
    assert spawned == [True]
    assert "blocked by remove_stale_record" not in registry.status()["failure_reason"]


def test_registry_never_reclaims_a_genuinely_live_service_record(tmp_path, monkeypatch):
    """Safety control: a current record must stay, whatever the reclaim path allows."""
    now = [100.0]
    spawned = []
    registry = _publication_registry(tmp_path, clock=now, spawned=spawned)
    record = {
        **current_host_identity().process_record_fields(
            pid=os.getpid(),
            start_identity=registry_mod.process_start_identity(os.getpid()),
        ),
        "service": "fixture",
        "socket": str(registry.socket_path),
        "protocol_version": 1,
        "version": registry_mod.LOCAL_SERVICE_REGISTRY_VERSION,
    }
    registry._write_record(record)
    signals = []
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))

    diagnostic = registry._record_process_diagnostic(record)
    assert diagnostic.current is True
    assert diagnostic.may_remove_stale_record is False
    assert diagnostic.may_remove_unidentifiable_record is False
    assert registry._remove_stale_record() is False
    assert registry._read_record()["pid"] == os.getpid()
    # Signal 0 is the liveness probe; nothing may deliver a real signal here.
    assert [item for item in signals if item[1] != 0] == []


def test_registry_does_not_retire_or_replace_a_newer_service(tmp_path, monkeypatch):
    spawned = []
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 21),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)),
    )
    actions = []

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        return {"ok": False, "error_code": "upgrade_required", "version": 22, "pid": 4242}

    monkeypatch.setattr(registry, "_request", fake_request)

    assert registry.ensure_started() is False
    assert registry.ensure_started() is False
    assert "shutdown" not in actions
    assert spawned == []
    assert registry.status()["upgrade_required"]["required_protocol_version"] == 22
    assert actions == ["ping"]
    assert registry.acquire_lease()["error_code"] == "upgrade_required"
    assert actions == ["ping"]


@pytest.mark.parametrize("stale_version", [21, 22])
def test_registry_reclaims_service_left_by_a_dead_web_launcher(tmp_path, monkeypatch, stale_version):
    spawned = []

    class FakeProcess:
        def poll(self):
            return None

        def wait(self):
            # A live replacement daemon: `_start_child_reaper` waits here until the child
            # exits, which for this fixture never happens, so model the real Popen contract
            # by blocking rather than fabricating an exit the test never produces.
            Event().wait()

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 21),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)) or FakeProcess(),
    )
    stale_pid, dead_launcher, replacement_pid = 4242, 1111, 5252
    registry._write_record({
        **_process_record(stale_pid),
        "service": "statsd", "pgid": stale_pid,
        "socket": str(registry.socket_path), "launcher_pid": dead_launcher,
    })
    # The replacement daemon is live: its record cannot be published otherwise.
    alive = {stale_pid: True, dead_launcher: False, replacement_pid: True}
    actions = []

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        if method == "shutdown":
            alive[stale_pid] = False
            return {"ok": True}
        if spawned:
            return {"ok": True, "version": 21, "pid": replacement_pid, "started_at": 1}
        if stale_version > 21:
            return {"ok": False, "error_code": "upgrade_required", "version": stale_version, "pid": stale_pid}
        return {"ok": True, "version": stale_version, "pid": stale_pid}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if alive.get(pid, False) else None,
    )
    monkeypatch.setattr(
        registry_mod,
        "tracked_local_service_groups",
        lambda _service_dir: [{
            "service": "statsd", "pid": stale_pid, "pgid": stale_pid,
            "socket": str(registry.socket_path),
        }],
    )

    assert registry.ensure_started() is True
    assert "shutdown" in actions
    assert len(spawned) == 1
    assert registry._upgrade_required is None


def test_registry_retires_an_older_service_that_rejects_the_new_protocol(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 23),
    )
    actions = []
    alive = {4242: True}
    registry._write_record({
        **_process_record(4242),
        "service": "statsd",
        "socket": str(registry.socket_path),
        "protocol_version": 22,
    })
    registry.socket_path.touch()

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        if method == "shutdown":
            alive[4242] = False
            return {"ok": True}
        return {
            "ok": False,
            "error_code": "upgrade_required",
            "version": 22,
            "required_protocol_version": 22,
            "pid": 4242,
        }

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if alive.get(pid, False) else None,
    )

    assert registry._retire_incompatible_service() is True

    assert actions == ["ping", "shutdown"]
    assert registry._upgrade_required is None
    assert registry.record_path.exists() is False
    assert registry.socket_path.exists() is False


class _VirtualRetirementClock:
    """One retirement run driven entirely by an injected clock.

    Nothing here touches the wall clock: ``clock()`` only ever moves because the
    product called ``sleep()``, so every elapsed value asserted below is the
    product's own declared budget rather than a machine-speed measurement.
    """

    def __init__(self, tmp_path, monkeypatch, *, service_pid=4242, spec_version=23, service_version=22):
        self.now = [100.0]
        self.sleeps: list[float] = []
        self.actions: list[str] = []
        self.signals: list[int] = []
        self.marks: dict[str, float] = {}
        self.alive = {service_pid: True}
        self.registry = LocalServiceRegistry(
            tmp_path,
            LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", spec_version),
            clock=lambda: self.now[0],
            sleep=self._sleep,
        )
        record = {
            **_process_record(service_pid),
            "service": "statsd",
            "socket": str(self.registry.socket_path),
            "protocol_version": service_version,
        }
        self.retained_start_identity = str(record["process_start_identity"])
        # The identity the live PID currently reports. Flipping it models the exact
        # race the retirement guards exist for: the retained generation exits and an
        # unrelated process is handed the same PID before this loop looks again.
        self.live_start_identity = [self.retained_start_identity]
        self.registry._write_record(record)
        self.registry.socket_path.touch()

        def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
            self.actions.append(method)
            self.on_request(method)
            if method == "shutdown":
                return {"ok": True}
            return {
                "ok": False,
                "error_code": "upgrade_required",
                "version": service_version,
                "required_protocol_version": service_version,
                "pid": service_pid,
            }

        def fake_kill(pid, signum):
            self.signals.append(signum)
            self.on_signal(signum, pid)

        monkeypatch.setattr(self.registry, "_request", fake_request)
        monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: self.alive.get(pid, False))
        monkeypatch.setattr(
            registry_mod,
            "process_start_identity",
            lambda pid: self.live_start_identity[0] if self.alive.get(pid, False) else None,
        )
        # Keep the identity fence reading only this fixture's state: without it,
        # process_record_diagnostic would consult the real /proc for `service_pid`.
        monkeypatch.setattr(registry_mod, "process_state", lambda pid: "")
        monkeypatch.setattr(registry_mod.os, "kill", fake_kill)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now[0] += seconds

    def on_request(self, method):
        if method == "shutdown":
            self.marks["shutdown"] = self.now[0]

    def on_signal(self, signum, pid):
        self.marks[signum] = self.now[0]

    def run(self):
        result = self.registry._retire_incompatible_service()
        self.marks["returned"] = self.now[0]
        return result

    @property
    def poll_seconds(self) -> float:
        """The product's own observed poll step, never a literal re-spelled here."""
        assert self.sleeps, "no poll step was observed; nothing waited"
        assert len(set(self.sleeps)) == 1, f"retirement polled at inconsistent steps: {sorted(set(self.sleeps))}"
        return self.sleeps[0]


def test_registry_retire_incompatible_service_escalates_to_sigkill_when_wedged(tmp_path, monkeypatch):
    """A generation that answers the RPC shutdown request but never actually exits
    (ignores SIGTERM) must be force-terminated, not left running under the shared
    socket forever. This is the same graceful-then-forced contract
    ``shutdown_owned_local_services`` already proves for the multi-service path,
    applied here to the single-service incompatible-generation retirement path.

    This is also the differential control for
    ``test_registry_retirement_yields_to_a_replacement_holding_the_same_pid``: the
    only variable that differs between them is whether the live PID's start identity
    still matches the retained record. Here it does, so every assertion is the exact
    inverse -- signals are sent, and the record and socket are reclaimed.
    """
    harness = _VirtualRetirementClock(tmp_path, monkeypatch)
    base_on_signal = harness.on_signal

    def on_signal(signum, pid):
        base_on_signal(signum, pid)
        # SIGTERM is deliberately a no-op here: the wedged process ignores it.
        if signum == signal.SIGKILL:
            harness.alive[pid] = False

    harness.on_signal = on_signal

    assert harness.run() is True

    assert harness.actions == ["ping", "shutdown"]
    assert harness.signals == [signal.SIGTERM, signal.SIGKILL]
    assert harness.registry.record_path.exists() is False
    assert harness.registry.socket_path.exists() is False


@pytest.mark.parametrize(
    "replace_at, expected_signals",
    [
        ("shutdown", []),
        (signal.SIGTERM, [signal.SIGTERM]),
        (signal.SIGKILL, [signal.SIGTERM, signal.SIGKILL]),
    ],
    ids=["before_any_signal", "during_sigterm_grace", "during_sigkill_force"],
)
def test_registry_retirement_yields_to_a_replacement_holding_the_same_pid(
    tmp_path, monkeypatch, replace_at, expected_signals
):
    """A live PID whose start identity no longer matches the retained record is a
    DIFFERENT process, so retirement owns no authority over it.

    ``retained_process_state()`` calls that ``"replaced"``, and each of the three
    guards that consume it must abandon the retirement immediately: no further
    signal, no record removal, no socket unlink. Signalling here would kill an
    unrelated process that merely inherited the PID, and unlinking here would
    destroy the incoming generation's own socket and ledger row.

    This is NOT the watchd in-process worker-slot handoff covered by
    ``test_watchd_demand_lifecycle.py`` -- this is the OS-level PID handoff seen
    by the registry's retirement loop.
    """
    harness = _VirtualRetirementClock(tmp_path, monkeypatch)
    replacement_identity = f"{harness.retained_start_identity}-replacement"
    assert replacement_identity != harness.retained_start_identity

    def replace_now():
        harness.live_start_identity[0] = replacement_identity

    if replace_at == "shutdown":
        harness.on_request = lambda method: replace_now() if method == "shutdown" else None
    else:
        base_on_signal = harness.on_signal

        def on_signal(signum, pid):
            base_on_signal(signum, pid)
            if signum == replace_at:
                replace_now()

        harness.on_signal = on_signal

    record_before = harness.registry.record_path.read_bytes()

    assert harness.run() is False, "retirement claimed success against a process it never proved it owned"

    assert harness.signals == expected_signals, (
        "the replacement generation was signalled, or an earlier escalation step was skipped"
    )
    # Positive control that these assertions are not vacuous: the same harness with
    # the start identity left UNCHANGED signals, removes the record and unlinks the
    # socket -- see test_registry_retire_incompatible_service_escalates_to_sigkill_when_wedged.
    assert harness.registry.record_path.exists() is True
    assert harness.registry.record_path.read_bytes() == record_before, (
        "the incoming generation's ledger row was rewritten or deleted by the outgoing retirement"
    )
    assert harness.registry.socket_path.exists() is True, "the replacement's socket was unlinked"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the replacement guards return False silently: no typed diagnostic names the PID handoff, "
        "so status() still reports the stale current_local_process reason. Needs the product change."
    ),
)
def test_registry_retirement_publishes_a_typed_diagnostic_for_a_pid_handoff(tmp_path, monkeypatch):
    """Yielding to a replacement must be visible, not merely silent.

    ``LocalProcessReason.PROCESS_IDENTITY_REUSED`` is the exact typed reason for
    "this PID is alive but is no longer the process the record names", and the
    diagnostic carries both the recorded and the observed start identity. A caller
    that only sees ``False`` cannot tell a PID handoff apart from a permission
    failure or a wedged daemon.
    """
    harness = _VirtualRetirementClock(tmp_path, monkeypatch)
    replacement_identity = f"{harness.retained_start_identity}-replacement"
    harness.on_request = lambda method: (
        harness.live_start_identity.__setitem__(0, replacement_identity) if method == "shutdown" else None
    )

    assert harness.run() is False

    diagnostic = harness.registry.status()["process_diagnostic"]
    assert diagnostic, "positive control: a typed diagnostic dict is exposed at all"
    assert diagnostic["reason"] == LocalProcessReason.PROCESS_IDENTITY_REUSED.value
    assert diagnostic["recorded_start_identity"] == harness.retained_start_identity
    assert diagnostic["observed_start_identity"] == replacement_identity


def test_registry_retirement_spends_exactly_the_declared_grace_and_force_budgets(tmp_path, monkeypatch):
    """Each escalation step waits its own DECLARED budget, not a hardcoded number.

    A generation that ignores both SIGTERM and SIGKILL (uninterruptible, not merely
    slow) exercises all three waits back to back. Every bound below is derived from
    the constants the product declares and from the poll step the product actually
    used, so replacing either constant with a literal, or reusing one budget for the
    other step, turns this red. The clock is virtual: it only advances because the
    product asked to sleep.
    """
    harness = _VirtualRetirementClock(tmp_path, monkeypatch)

    assert harness.run() is False, "a process that survived SIGKILL was declared retired"

    assert harness.actions == ["ping", "shutdown"]
    assert harness.signals == [signal.SIGTERM, signal.SIGKILL], (
        "escalation must be bounded at exactly one SIGTERM then one SIGKILL"
    )
    poll = harness.poll_seconds
    graceful = harness.marks[signal.SIGTERM] - harness.marks["shutdown"]
    forced_wait = harness.marks[signal.SIGKILL] - harness.marks[signal.SIGTERM]
    final_wait = harness.marks["returned"] - harness.marks[signal.SIGKILL]

    assert LOCAL_SERVICE_RETIRE_GRACE_SECONDS <= graceful < LOCAL_SERVICE_RETIRE_GRACE_SECONDS + poll, (
        f"the post-shutdown wait was {graceful}s, not the declared "
        f"{LOCAL_SERVICE_RETIRE_GRACE_SECONDS}s grace budget"
    )
    assert LOCAL_SERVICE_RETIRE_GRACE_SECONDS <= forced_wait < LOCAL_SERVICE_RETIRE_GRACE_SECONDS + poll, (
        f"the post-SIGTERM wait was {forced_wait}s, not the declared "
        f"{LOCAL_SERVICE_RETIRE_GRACE_SECONDS}s grace budget"
    )
    assert LOCAL_SERVICE_RETIRE_FORCE_SECONDS <= final_wait < LOCAL_SERVICE_RETIRE_FORCE_SECONDS + poll, (
        f"the post-SIGKILL wait was {final_wait}s, not the declared "
        f"{LOCAL_SERVICE_RETIRE_FORCE_SECONDS}s force budget"
    )
    # Positive control on the three bounds above: they are not all satisfied by one
    # shared number -- the force window is measurably longer than the grace window.
    assert final_wait > graceful

    # An unkillable generation is never declared retired, and nothing it still owns
    # is removed on its behalf.
    assert harness.registry.record_path.exists() is True
    assert harness.registry.socket_path.exists() is True


# Written as a strict xfail while `verified_orphan_diagnostics` spelled attempted_action,
# result AND reason as literals inside a list comprehension, so every survivor got a
# byte-identical row. It now passes because `reason` is derived from real data -- it varies
# across untracked_no_ledger_record, unreadable_service_record, superseded_by_recorded_generation
# and identity_<LocalProcessReason>.
#
# Be precise about what that does and does not establish, because the difference matters to
# anyone reading this as evidence: `attempted_action` and `result` are STILL constants, so this
# test proves survivors are now distinguishable, NOT that a bounded repair is ever attempted.
# The queue's "attempted action, result, and failure reason" requirement remains open.
def test_verified_orphan_diagnostics_must_distinguish_a_recorded_survivor(tmp_path):
    """The three reported fields must be derived from the survivor, not fixed literals.

    ``verified_orphan_diagnostics`` currently spells ``attempted_action``,
    ``result`` and ``reason`` as constants inside a list comprehension, so two
    materially different survivors get byte-identical rows. One of those literals
    is also simply untrue: a pre-identity ("legacy") service record on disk names
    pid 7001 and its socket, but the tracked-group resolver drops it for missing
    host/boot proof, so 7001 surfaces as "untracked" while its ledger record is
    sitting right there. Reporting ``untracked_no_ledger_record`` for it hides the
    one survivor whose identity a bounded repair could actually verify.
    """
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    recorded_socket = service_dir / "legacy.sock"
    recordless_socket = service_dir / "ghost.sock"
    record_path = service_dir / "legacy.service.json"
    record_path.write_text(
        registry_mod.json.dumps(
            {"service": "legacy", "socket": str(recorded_socket), "pid": 7001, "version": 1}
        ),
        encoding="utf-8",
    )
    table = _table([
        (7001, 1, 7001, 1.0, f"python3 -m yolomux_lib.jobd --serve --socket {recorded_socket}", 8001),
        (7002, 1, 7002, 1.0, f"python3 -m yolomux_lib.jobd --serve --socket {recordless_socket}", 8002),
    ])

    rows = registry_mod.verified_orphan_diagnostics(service_dir, table)
    by_pid = {row["pid"]: row for row in rows}

    # Positive controls: the two survivors really are both reported, and the record
    # really is on disk -- neither assertion below is comparing two empty things.
    assert set(by_pid) == {7001, 7002}
    assert record_path.exists() is True
    assert registry_mod.tracked_local_service_groups(service_dir, table) == [], (
        "positive control: the legacy record must NOT produce a tracked group"
    )

    recorded_row = (by_pid[7001]["attempted_action"], by_pid[7001]["result"], by_pid[7001]["reason"])
    recordless_row = (by_pid[7002]["attempted_action"], by_pid[7002]["result"], by_pid[7002]["reason"])
    assert by_pid[7001]["reason"] != "untracked_no_ledger_record", (
        f"pid 7001 has a ledger record at {record_path}; the reported reason denies it exists"
    )
    assert recorded_row != recordless_row, (
        "a survivor with a ledger record and one without produced identical action/result/reason: "
        "these three fields cannot vary, so no repair outcome can ever be reported through them"
    )


def _directory_snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_registry_two_private_roots_never_cross_talk(tmp_path, monkeypatch):
    """Two managed, auto-derived roots never share election, records, or files.

    Nothing in the registry discovers or merges across two different
    ``YOLOMUX_ROOT`` values -- each root is private by construction, so
    starting or retiring a service in one must leave the other byte-for-byte
    unchanged.
    """
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    clock_a, clock_b = [100.0], [100.0]
    spawned_a: list[bool] = []
    spawned_b: list[bool] = []
    registry_a = _publication_registry(root_a, clock=clock_a, spawned=spawned_a)
    registry_b = _publication_registry(root_b, clock=clock_b, spawned=spawned_b)

    def _fake_request_after_spawn(spawned):
        # Ping/status only succeed once this registry's own popen has actually
        # run, so ensure_started must take the real bounded-startup path
        # (spawn, then observe healthy) instead of short-circuiting on an
        # already-healthy cache hit that never spawns anything.
        def fake_request(_method, payload=None, timeout=0.2, protocol_version=None):
            if not spawned:
                return {}
            return {"ok": True, "version": 1, "pid": os.getpid(), "started_at": 1.0}

        return fake_request

    monkeypatch.setattr(registry_a, "_request", _fake_request_after_spawn(spawned_a))
    monkeypatch.setattr(registry_b, "_request", _fake_request_after_spawn(spawned_b))

    assert registry_a.ensure_started() is True
    snapshot_a_before = _directory_snapshot(root_a)
    assert snapshot_a_before, "sanity: registry_a actually wrote something"
    assert root_b.exists() is False, "starting registry_a must not create anything under root_b"

    assert registry_b.ensure_started() is True
    snapshot_a_after = _directory_snapshot(root_a)
    assert snapshot_a_after == snapshot_a_before, "starting registry_b changed root_a's files"

    # Retire registry_b's service; root_a must still be untouched.
    monkeypatch.setattr(registry_b, "_request", lambda *a, **k: {"ok": False, "error_code": "upgrade_required", "version": 2, "pid": os.getpid()})
    registry_b.invalidate_rpc_health()
    assert registry_b.ensure_started() is False
    assert _directory_snapshot(root_a) == snapshot_a_before, "an operation on registry_b changed root_a's files"

    # Each registry took the real bounded-startup path (popen, then observe
    # healthy) exactly once for its own root -- never for the other's.
    assert spawned_a == [True]
    assert spawned_b == [True]
    assert _directory_snapshot(root_b), "sanity: registry_b actually wrote something under its own root"


def test_registry_incompatible_generations_share_one_root_both_directions(tmp_path, monkeypatch):
    """A deliberately caller-shared root retains exactly one compatible owner.

    An older generation meeting a live newer service must refuse typed and
    leave the newer generation's record untouched (no adoption, no
    corruption); a newer generation meeting a live older service must retire
    it cleanly and publish its own record in the same shared directory.
    """
    older_pid = 4242

    # Direction 1: an older registry (protocol 1) meets a live newer service
    # (protocol 2) it never wrote itself -- must refuse typed, never adopt.
    registry_old = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statusd", "yolomux_lib.statusd", "statusd.sock", 1),
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("an older generation must never spawn over a live newer one")),
    )
    actions_old: list[str] = []
    monkeypatch.setattr(
        registry_old,
        "_request",
        lambda method, payload=None, timeout=0.2: actions_old.append(method) or {"ok": False, "error_code": "upgrade_required", "version": 2, "pid": older_pid + 1},
    )

    assert registry_old.ensure_started() is False
    assert "shutdown" not in actions_old
    assert registry_old.status()["upgrade_required"]["required_protocol_version"] == 2
    assert registry_old.record_path.exists() is False

    # Direction 2: a newer registry (protocol 2), sharing the SAME service_dir,
    # meets a real persisted record written by a genuinely separate
    # older-generation registry object (protocol 1).
    registry_seed = LocalServiceRegistry(tmp_path, LocalServiceSpec("statusd", "yolomux_lib.statusd", "statusd.sock", 1))
    registry_seed._write_record({
        **_process_record(older_pid),
        "service": "statusd",
        "socket": str(registry_seed.socket_path),
        "protocol_version": 1,
    })
    registry_seed.socket_path.touch()

    registry_new = LocalServiceRegistry(tmp_path, LocalServiceSpec("statusd", "yolomux_lib.statusd", "statusd.sock", 2))
    assert registry_new.socket_path == registry_seed.socket_path
    assert registry_new.record_path == registry_seed.record_path

    actions_new: list[str] = []
    alive = {older_pid: True}

    def fake_request_new(method, payload=None, timeout=0.2):
        actions_new.append(method)
        if method == "shutdown":
            alive[older_pid] = False
            return {"ok": True}
        return {"ok": False, "error_code": "upgrade_required", "version": 1, "required_protocol_version": 1, "pid": older_pid}

    monkeypatch.setattr(registry_new, "_request", fake_request_new)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(registry_mod, "process_start_identity", lambda pid: f"proc:{pid + 1000}" if alive.get(pid, False) else None)

    assert registry_new._retire_incompatible_service() is True

    assert actions_new == ["ping", "shutdown"]
    assert registry_new._upgrade_required is None
    assert registry_new.record_path.exists() is False, "the old generation's record must be fully removed"
    assert registry_new.socket_path.exists() is False, "the old generation's socket artifact must be fully removed"
    # Only in this (retirement) direction was the shared record actually
    # touched -- direction 1 above proved a refusal never writes anything.


def test_registry_two_callers_share_one_root_with_compatible_generations(tmp_path, monkeypatch):
    """Two callers deliberately pointed at the same root, same protocol, reuse
    the one live service; the second caller neither spawns a duplicate nor
    retires the first caller's record.

    Distinct from test_registry_incompatible_generations_share_one_root_both_directions
    above (mismatched protocol): here both callers report the SAME
    protocol_version, so caller-shared-root-retain requires exactly one
    compatible owner to be reused, never replaced.
    """
    pid = 4242
    registry_a = LocalServiceRegistry(tmp_path, LocalServiceSpec("statusd", "yolomux_lib.statusd", "statusd.sock", 3))
    registry_a._write_record({
        **_process_record(pid),
        "service": "statusd",
        "socket": str(registry_a.socket_path),
        "protocol_version": 3,
    })
    registry_a.socket_path.touch()

    registry_b = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statusd", "yolomux_lib.statusd", "statusd.sock", 3),
        popen=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("a compatible-generation caller must reuse the live service, never spawn a second one")
        ),
    )
    assert registry_b.socket_path == registry_a.socket_path
    assert registry_b.record_path == registry_a.record_path

    actions_b: list[str] = []

    def fake_request_b(method, payload=None, timeout=0.2):
        actions_b.append(method)
        if method == "shutdown":
            raise AssertionError("a compatible-generation caller must never retire the live service it shares a root with")
        return {"ok": True, "version": 3, "pid": pid, "started_at": 1.0}

    monkeypatch.setattr(registry_b, "_request", fake_request_b)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda p: p == pid)
    monkeypatch.setattr(registry_mod, "process_start_identity", lambda p: f"proc:{p + 1000}" if p == pid else None)

    assert registry_b.ensure_started() is True
    assert "shutdown" not in actions_b
    republished = json.loads(registry_b.record_path.read_text())
    assert republished["pid"] == pid, "the second caller must keep reusing the first caller's live pid"
    assert republished["protocol_version"] == 3


def test_registry_retires_ledger_proven_older_service_with_its_recorded_protocol(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statusd", "yolomux_lib.statusd", "statusd.sock", 2),
    )
    old_pid = 4242
    registry._write_record({
        **_process_record(old_pid),
        "service": "statusd",
        "socket": str(registry.socket_path),
        "protocol_version": 1,
    })
    registry.socket_path.touch()
    alive = {old_pid: True}
    actions = []

    def fake_request(method, payload=None, timeout=0.2, protocol_version=None):
        version = registry.spec.protocol_version if protocol_version is None else protocol_version
        actions.append((method, version))
        if method == "ping":
            return {"ok": False, "error": "upgrade_required", "required_protocol_version": 1}
        if method == "status":
            return {"ok": True, "pid": old_pid, "version": 1}
        if method == "shutdown":
            alive[old_pid] = False
            return {"ok": True}
        return {}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(
        registry_mod,
        "process_start_identity",
        lambda pid: f"proc:{pid + 1000}" if alive.get(pid, False) else None,
    )

    assert registry._retire_incompatible_service() is True

    assert actions == [("ping", 2), ("status", 1), ("shutdown", 1)]
    assert registry.record_path.exists() is False
    assert registry.socket_path.exists() is False


def test_registry_reclaims_dead_legacy_record_for_inert_socket_without_signalling(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("indexd", "yolomux_lib.search.search_indexer", "indexer.sock", 1),
    )
    registry._write_record({"pid": 999_999_999, "service": "indexd"})
    registry.socket_path.write_text("stale", encoding="utf-8")
    signals = []

    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))

    assert registry._remove_stale_record() is True
    assert registry.record_path.exists() is False
    assert registry.socket_path.read_text(encoding="utf-8") == "stale"
    assert registry.status()["process_diagnostic"]["reason"] == "missing_host_identity"
    assert signals == []


@pytest.mark.parametrize(
    ("record_case", "reason"),
    (
        ("foreign", "foreign_host"),
        ("previous-boot", "previous_boot"),
        ("missing-host", "missing_host_identity"),
        ("recycled", "process_identity_reused"),
    ),
)
def test_registry_reclaim_fence_refuses_typed_noncurrent_records(tmp_path, monkeypatch, record_case, reason):
    service_pid = 4242
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 23),
    )
    record = {
        **_process_record(service_pid),
        "service": "statsd",
        "socket": str(registry.socket_path),
        "protocol_version": 22,
    }
    if record_case == "foreign":
        record["stable_host_id"] = "fixture-foreign-host"
    elif record_case == "previous-boot":
        record["boot_id"] = "fixture-previous-boot"
    elif record_case == "missing-host":
        del record["stable_host_id"]
    registry._write_record(record)
    registry.socket_path.touch()
    actions = []
    signals = []

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        return {"ok": True, "version": 22, "pid": service_pid}

    observed_start = "proc:9999" if record_case == "recycled" else f"proc:{service_pid + 1000}"
    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(registry_mod, "process_start_identity", lambda _pid: observed_start)
    monkeypatch.setattr(registry_mod.os, "kill", lambda pid, signum: signals.append((pid, signum)))

    assert registry._retire_incompatible_service() is False
    assert actions == ["ping"]
    assert registry.record_path.exists() is True
    assert registry.socket_path.exists() is True
    assert registry.status()["process_diagnostic"]["reason"] == reason
    assert signals == []


def test_registry_does_not_retire_newer_same_protocol_build(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec(
            "statsd", "yolomux_lib.stats_current.service", "statsd.sock", 24,
            code_revision="old-revision", build_revision=2,
        ),
    )
    actions = []

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        return {
            "ok": True,
            "version": 24,
            "build": 3,
            "code_revision": "new-revision",
            "pid": 4242,
        }

    monkeypatch.setattr(registry, "_request", fake_request)

    registry._retire_incompatible_service()

    assert actions == ["ping"]


@pytest.mark.parametrize(
    ("module", "service_name", "client_factory", "extra_args"),
    (
        (jobd, "jobd", jobd.JobClient, ()),
        (approvald, "approvald", approvald.ApprovalClient, ()),
        (
            stats_current_service,
            "statsd",
            lambda socket_path: stats_current_client.StatsCurrentClient(
                socket_path,
                socket_path.parent / stats_current_storage.DATABASE_FILENAME,
            )._transport,
            ("--database", "{database}"),
        ),
    ),
)
def test_service_module_entrypoint_exits_cleanly_on_sigterm(tmp_path, module, service_name, client_factory, extra_args):
    socket_path = tmp_path / "state with spaces" / f"{service_name}.sock"
    argv = [
        sys.executable,
        "-m",
        module.__name__,
        "--serve",
        "--socket",
        str(socket_path),
        "--idle-seconds",
        "30",
    ]
    for item in extra_args:
        argv.append(str(socket_path.parent / stats_current_storage.DATABASE_FILENAME) if item == "{database}" else item)
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    client = client_factory(socket_path)
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not client.registry.healthy():
            time.sleep(0.02)
        assert client.registry.healthy() is True

        process.terminate()
        stdout, stderr = process.communicate(timeout=3.0)

        assert process.returncode == 0
        assert stdout == ""
        assert stderr == ""
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and socket_path.exists():
            time.sleep(0.02)
        assert socket_path.exists() is False
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3.0)


def test_service_runtime_signal_handlers_set_stop_event_and_restore(monkeypatch):
    stop_event = Event()
    installed = {}
    restored = {}

    def fake_getsignal(signum):
        return f"old-{signum}"

    def fake_signal(signum, handler):
        if callable(handler):
            installed[signum] = handler
        else:
            restored[signum] = handler

    monkeypatch.setattr(runtime.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(runtime.signal, "signal", fake_signal)

    previous = runtime.install_stop_signal_handlers(stop_event)
    assert previous
    next(iter(installed.values()))(0, None)
    runtime.restore_signal_handlers(previous)

    assert stop_event.is_set() is True
    assert set(restored.values()) == {f"old-{signum}" for signum, _handler in previous}


def test_service_priority_is_best_effort(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.os, "nice", lambda increment: calls.append(increment))
    assert runtime.apply_service_process_priority(7) is True
    assert calls == [7]

    def raise_os_error(_increment):
        raise OSError("unsupported")

    monkeypatch.setattr(runtime.os, "nice", raise_os_error)
    assert runtime.apply_service_process_priority(7) is False


def _table(rows):
    return {
        pid: registry_mod.ProcessTableEntry(ppid, pgid, cpu_seconds, command, start_time)
        for pid, ppid, pgid, cpu_seconds, command, *start in rows
        for start_time in [start[0] if start else pid + 1000]
    }


def _process_record(pid):
    return FixtureProcessRecordBuilder(pid=pid).build()


def _write_service_record(service_dir, name, pid, socket_path):
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / f"{name}.service.json").write_text(
        registry_mod.json.dumps(FixtureLocalServiceRecordBuilder(service=name, socket_path=socket_path, pid=pid).build()),
        encoding="utf-8",
    )


def test_shutdown_owned_local_services_escalates_gracefully_and_spares_unrelated_launcher_groups(tmp_path):
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    target_socket = service_dir / "jobd.sock"
    bystander_socket = service_dir / "statusd.sock"

    target_record = FixtureLocalServiceRecordBuilder(
        service="jobd", socket_path=target_socket, pid=500,
        fields={"launcher_pid": 700, "launcher_port": 8881},
    ).build()
    (service_dir / "jobd.service.json").write_text(registry_mod.json.dumps(target_record), encoding="utf-8")

    bystander_record = FixtureLocalServiceRecordBuilder(
        service="statusd", socket_path=bystander_socket, pid=600,
        fields={"launcher_pid": 999, "launcher_port": 9999},
    ).build()
    (service_dir / "statusd.service.json").write_text(registry_mod.json.dumps(bystander_record), encoding="utf-8")

    initial = _table([
        (500, 1, 500, 1.0, f"python3 -m yolomux_lib.jobd --serve --socket {target_socket} --idle-seconds 60", 1500),
        (501, 500, 500, 1.0, "python3 -c multiprocessing-spawn-worker", 1501),
        (600, 1, 600, 1.0, f"python3 -m yolomux_lib.statusd --serve --socket {bystander_socket} --idle-seconds 60", 1600),
        (601, 600, 600, 1.0, "python3 -c multiprocessing-spawn-worker", 1601),
    ])
    # After the SIGTERM pass: the jobd leader (500) has exited; its worker (501) is a
    # wedged holdout that must be force-killed. The unrelated launcher's group (600/601)
    # is completely untouched -- still alive with an unchanged identity.
    survivors = _table([
        (501, 1, 500, 1.0, "python3 -c multiprocessing-spawn-worker", 1501),
        (600, 1, 600, 1.0, f"python3 -m yolomux_lib.statusd --serve --socket {bystander_socket} --idle-seconds 60", 1600),
        (601, 600, 600, 1.0, "python3 -c multiprocessing-spawn-worker", 1601),
    ])

    tables = [initial, survivors]

    def table_reader():
        return tables.pop(0) if len(tables) > 1 else tables[0]

    kills = []

    def kill(pid, signum):
        kills.append((pid, signum))

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)

    result = registry_mod.shutdown_owned_local_services(
        8881,
        service_dir,
        launcher_pid=700,
        table_reader=table_reader,
        kill=kill,
        sleep=sleep,
        grace_seconds=2.5,
    )

    assert sleeps == [2.5], "the caller's exact grace budget must reach sleep(), not a hardcoded or dropped value"
    assert result == {"signalled": [500], "terminated": [501]}
    assert kills == [(500, signal.SIGTERM), (501, signal.SIGKILL)], "graceful must precede forced, and only the owned group is touched"
    assert 600 not in [pid for pid, _signum in kills] and 601 not in [pid for pid, _signum in kills], (
        "an unrelated launcher's process group must never be signalled"
    )


def test_ledger_record_identity_requires_the_exact_socket_marker(tmp_path):
    socket_path = tmp_path / "services" / "jobd.sock"
    record = FixtureLocalServiceRecordBuilder(service="jobd", socket_path=socket_path, pid=100).build()
    with_marker = _table([(100, 1, 100, 5.0, f"python3 -m yolomux_lib.jobd --serve --socket {socket_path} --idle-seconds 60")])
    unrelated_python = _table([(100, 1, 100, 5.0, "python3 some_other_tool.py --socket /tmp/elsewhere.sock")])
    defender_shaped = _table([(100, 1, 100, 5.0, "/Applications/Microsoft Defender.app/Contents/MacOS/wdavdaemon unprivileged")])

    assert registry_mod.service_record_identity_matches(record, with_marker) is True
    # PID reuse: the recycled PID belongs to another python process — rejected.
    assert registry_mod.service_record_identity_matches(record, unrelated_python) is False
    # A system/security process can never satisfy the socket marker — rejected.
    assert registry_mod.service_record_identity_matches(record, defender_shaped) is False
    assert registry_mod.service_record_identity_matches(record, {}) is False


def test_tracked_local_service_groups_membership_is_exact_process_group(tmp_path):
    service_dir = tmp_path / "services"
    jobd_socket = service_dir / "jobd.sock"
    stale_socket = service_dir / "statsd.sock"
    _write_service_record(service_dir, "jobd", 200, jobd_socket)
    _write_service_record(service_dir, "statsd", 300, stale_socket)
    table = _table(
        [
            (200, 1, 200, 10.0, f"python3 -m yolomux_lib.jobd --serve --socket {jobd_socket} --idle-seconds 60"),
            (201, 200, 200, 90.0, "python3 -c multiprocessing-spawn-worker"),
            (202, 200, 200, 80.0, "python3 -c multiprocessing-spawn-worker"),
            # Same-name stranger in ANOTHER process group: never a member.
            (250, 1, 250, 999.0, "python3 -c multiprocessing-spawn-worker"),
            # statsd record's PID was recycled by an unrelated process: no group at all.
            (300, 1, 300, 5.0, "python3 unrelated.py"),
        ]
    )

    groups = registry_mod.tracked_local_service_groups(service_dir, table)

    assert [group["service"] for group in groups] == ["jobd"]
    assert groups[0]["pid"] == 200
    assert groups[0]["pgid"] == 200
    assert groups[0]["member_pids"] == (200, 201, 202)


def test_tracked_local_service_groups_preserve_darwin_member_identity(tmp_path):
    service_dir = tmp_path / "services"
    socket_path = service_dir / "jobd.sock"
    _write_service_record(service_dir, "jobd", 200, socket_path)
    table = {
        200: registry_mod.ProcessTableEntry(1, 200, 1.0, f"python3 -m yolomux_lib.jobd --socket {socket_path}", 1200, 200, "proc:1200"),
        201: registry_mod.ProcessTableEntry(200, 200, 1.0, "python3 worker", 1201, 200, "darwin:1201"),
    }

    groups, diagnostics = registry_mod.resolve_tracked_local_service_groups(service_dir, table)

    assert diagnostics == []
    assert groups[0]["member_records"][201]["process_start_identity"] == "darwin:1201"
    assert registry_mod.process_record_diagnostic(groups[0]["member_records"][201], table=table).current is True


def test_tracked_port_process_group_requires_lease_and_port_identity(tmp_path):
    FixtureLeaseRecordBuilder(pid=400, pgid=400, port=8881).write(tmp_path)
    good = _table(
        [
            (400, 1, 400, 50.0, "python3 -u yolomux.py 8880 /tmp/log --host 0.0.0.0 --port 8881 --dang --dev"),
            (401, 400, 400, 5.0, "tmux -C attach-session"),
        ]
    )
    prefix_collision = _table([(400, 1, 400, 50.0, "python3 yolomux.py --port 888 --dev")])
    recycled = _table([(400, 1, 400, 50.0, "python3 unrelated.py")])

    group = registry_mod.tracked_port_process_group(8881, tmp_path, good)
    assert {key: group[key] for key in ("port", "pid", "pgid", "member_pids")} == {
        "port": 8881,
        "pid": 400,
        "pgid": 400,
        "member_pids": (400, 401),
    }
    assert group["process_record"] == {
        key: _process_record(400)[key]
        for key in ("stable_host_id", "boot_id", "pid", "process_start_identity", "process_start_ticks")
    }
    assert set(group["member_records"]) == {400, 401}
    # Another YOLOmux port (or a --port prefix collision) never enters this ledger.
    assert registry_mod.tracked_port_process_group(8881, tmp_path, prefix_collision) == {}
    # A recycled lease PID fails the command identity check.
    assert registry_mod.tracked_port_process_group(8881, tmp_path, recycled) == {}
    assert registry_mod.tracked_port_process_group(9999, tmp_path, good) == {}


def test_tracked_port_process_group_preserves_darwin_member_identity(tmp_path):
    FixtureLeaseRecordBuilder(pid=400, pgid=400, port=8881).write(tmp_path)
    table = {
        400: registry_mod.ProcessTableEntry(1, 400, 1.0, "python3 yolomux.py --port 8881 --dang", 1400, 400, "proc:1400"),
        401: registry_mod.ProcessTableEntry(400, 400, 1.0, "tmux -C attach-session", 1401, 400, "darwin:1401"),
    }

    group, diagnostic = registry_mod.resolve_tracked_port_process_group(8881, tmp_path, table)

    assert diagnostic is not None and diagnostic.current is True
    assert group["member_records"][401]["process_start_identity"] == "darwin:1401"
    assert registry_mod.process_record_diagnostic(group["member_records"][401], table=table).current is True


def test_service_record_carries_pgid_launcher_and_bounded_worker_pids(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod, "process_group_id", lambda pid: 700 if pid == 700 else 0)
    registry_mod.set_local_service_launch_context(8881)
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("jobd", "yolomux_lib.jobd", "jobd.sock", protocol_version=3),
    )

    record = registry._record_from_status(
        {"pid": 700, "version": 3, "worker_pids": [701, 702, 0, -4, "junk"], "started_at": 1.0}
    )

    assert record["pgid"] == 700
    assert record["launcher_pid"] == registry_mod.os.getpid()
    assert record["launcher_port"] == 8881
    assert record["worker_pids"] == [701, 702]


def test_publish_record_clears_a_blocked_start_latch_on_healthy_adoption(tmp_path):
    """A blocked-start guard reason must not outlive the healthy daemon it wrongly describes.

    ``remove_stale_record`` correctly refuses to evict a live current-local daemon (e.g. statusd),
    and ``_record_blocked_start`` latches "start blocked by ... (reason=current_local_process)" on
    ``_failure_reason`` WITHOUT touching ``_record_refusal_reason``. When the SAME registry then
    validates and publishes that healthy, identity-proven daemon, the stale latch must clear --
    otherwise ``runtime_status`` keeps emitting it and the Daemons row shows a permanent Issue for a
    service that is up and serving. Before the fix, ``_publish_record`` cleared the latch only when
    it equalled the refusal reason, so a blocked-start latch survived every healthy publish.
    """
    now = [100.0]
    registry = _publication_registry(tmp_path, clock=now, spawned=[])
    registry._process_diagnostic = {"reason": "current_local_process"}
    registry._record_blocked_start("remove_stale_record")
    assert "start blocked by remove_stale_record" in registry._failure_reason
    # The bug precondition: a blocked start latches _failure_reason but NOT _record_refusal_reason,
    # so the old equality-gated clear could never fire.
    assert registry._record_refusal_reason == ""

    assert registry._publish_record({"ok": True, "version": 1, "pid": os.getpid(), "service": "fixture"}) is True

    assert registry._failure_reason == "", "a published, identity-proven daemon must clear the stale blocked-start latch"
    assert "start blocked" not in registry.failure_response()["reason"]

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from yolomux_lib import approvald
from yolomux_lib import jobd
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services import runtime
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.local_services.registry import parse_ps_cpu_seconds
from yolomux_lib.stats_current import client as stats_current_client
from yolomux_lib.stats_current import service as stats_current_service
from yolomux_lib.stats_current import storage as stats_current_storage


REPO_ROOT = Path(__file__).resolve().parents[1]


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
    assert args[args.index("--socket") + 1] == str(registry.socket_path)
    assert args[args.index("--idle-seconds") + 1] == "12.5"
    assert args[-2:] == ["--workers", "1"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert Path(kwargs["stdout"].name) == registry.stderr_path
    assert kwargs["stdout"].closed is True
    assert kwargs["stderr"] is subprocess.STDOUT


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


def test_long_default_socket_fallback_keeps_registry_lock_out_of_tmp(tmp_path, monkeypatch):
    state_dir = tmp_path / ("long-state-segment-" * 8)
    monkeypatch.setattr(approvald.common, "STATE_DIR", state_dir)

    client = approvald.ApprovalClient()

    assert client.socket_path.parent == Path("/tmp")
    assert client.registry.service_dir == state_dir / "services"
    assert client.registry.lock_path.parent == state_dir / "services"


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

    def fake_request(_path, envelope, **_kwargs):
        requests.append(envelope.method)
        if envelope.method == "ping":
            return {"ok": True, "version": 5, "pid": 42}, b""
        return {"ok": True, "version": 5, "pid": 42, "started_at": 1}, b""

    monkeypatch.setattr("yolomux_lib.local_services.registry.request", fake_request)

    assert registry.ensure_started() is True
    first_requests = list(requests)
    assert registry.ensure_started() is True
    assert requests == first_requests
    now[0] += 1.1
    assert registry.ensure_started() is True
    assert requests.count("ping") == 2


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


def test_registry_reclaims_newer_service_left_by_a_dead_web_launcher(tmp_path, monkeypatch):
    spawned = []

    class FakeProcess:
        def poll(self):
            return None

    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.stats_current.service", "statsd.sock", 21),
        popen=lambda *args, **kwargs: spawned.append((args, kwargs)) or FakeProcess(),
    )
    stale_pid, dead_launcher = 4242, 1111
    registry._write_record({
        "service": "statsd", "pid": stale_pid, "pgid": stale_pid,
        "socket": str(registry.socket_path), "launcher_pid": dead_launcher,
    })
    alive = {stale_pid: True, dead_launcher: False}
    actions = []

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        if method == "shutdown":
            alive[stale_pid] = False
            return {"ok": True}
        if spawned:
            return {"ok": True, "version": 21, "pid": 5252, "started_at": 1}
        return {"ok": False, "error_code": "upgrade_required", "version": 22, "pid": stale_pid}

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr(registry_mod, "pid_is_alive", lambda pid: alive.get(pid, False))
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

    def fake_request(method, payload=None, timeout=0.2):
        actions.append(method)
        return {
            "ok": False,
            "error_code": "upgrade_required",
            "version": 22,
            "required_protocol_version": 22,
            "pid": 4242,
        }

    monkeypatch.setattr(registry, "_request", fake_request)
    monkeypatch.setattr("yolomux_lib.local_services.registry.pid_is_alive", lambda _pid: False)

    registry._retire_incompatible_service()

    assert actions == ["ping", "shutdown"]
    assert registry._upgrade_required is None


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
        pid: registry_mod.ProcessTableEntry(ppid, pgid, cpu_seconds, command)
        for pid, ppid, pgid, cpu_seconds, command in rows
    }


def _write_service_record(service_dir, name, pid, socket_path):
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / f"{name}.service.json").write_text(
        registry_mod.json.dumps({"service": name, "pid": pid, "socket": str(socket_path)}),
        encoding="utf-8",
    )


def test_ledger_record_identity_requires_the_exact_socket_marker(tmp_path):
    socket_path = tmp_path / "services" / "jobd.sock"
    record = {"service": "jobd", "pid": 100, "socket": str(socket_path)}
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


def test_tracked_port_process_group_requires_lease_and_port_identity(tmp_path):
    lease_dir = tmp_path / "server-leases"
    lease_dir.mkdir(parents=True)
    (lease_dir / "8881.lock").write_text(registry_mod.json.dumps({"pid": 400, "port": 8881}), encoding="utf-8")
    good = _table(
        [
            (400, 1, 400, 50.0, "python3 -u yolomux.py 8880 /tmp/log --host 0.0.0.0 --port 8881 --dang --dev"),
            (401, 400, 400, 5.0, "tmux -C attach-session"),
        ]
    )
    prefix_collision = _table([(400, 1, 400, 50.0, "python3 yolomux.py --port 888 --dev")])
    recycled = _table([(400, 1, 400, 50.0, "python3 unrelated.py")])

    group = registry_mod.tracked_port_process_group(8881, tmp_path, good)
    assert group == {"port": 8881, "pid": 400, "pgid": 400, "member_pids": (400, 401)}
    # Another YOLOmux port (or a --port prefix collision) never enters this ledger.
    assert registry_mod.tracked_port_process_group(8881, tmp_path, prefix_collision) == {}
    # A recycled lease PID fails the command identity check.
    assert registry_mod.tracked_port_process_group(8881, tmp_path, recycled) == {}
    assert registry_mod.tracked_port_process_group(9999, tmp_path, good) == {}


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

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from yolomux_lib import approvald
from yolomux_lib import jobd
from yolomux_lib import statsd
from yolomux_lib.local_services import runtime
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_package_discovery_includes_local_service_subpackages():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.packages.find]" in pyproject
    assert 'py-modules = ["auto_approve_tmux", "tmux_wall", "yolomux"]' in pyproject
    assert 'include = ["yolomux_lib*"]' in pyproject
    assert 'packages = ["yolomux_lib"]' not in pyproject
    for module_name in (
        "auto_approve_tmux",
        "tmux_wall",
        "yolomux_lib.local_services",
        "yolomux_lib.local_services.rpc",
        "yolomux_lib.statsd",
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
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


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


def test_registry_health_request_identifies_expected_service_protocol(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("statsd", "yolomux_lib.statsd", "statsd.sock", 5),
    )
    captured = {}

    def fake_request(_path, envelope, **_kwargs):
        captured.update(envelope.payload)
        return {"ok": True, "version": 5, "pid": 1}, b""

    monkeypatch.setattr("yolomux_lib.local_services.registry.request", fake_request)

    assert registry.healthy() is True
    assert captured == {"action": "ping", "protocol_version": 5}


@pytest.mark.parametrize(
    ("module", "service_name", "client_factory", "extra_args"),
    (
        (jobd, "jobd", jobd.JobClient, ()),
        (approvald, "approvald", approvald.ApprovalClient, ()),
        (statsd, "statsd", lambda socket_path: statsd.StatsClient(socket_path, socket_path.with_suffix(".sqlite3")), ("--database", "{database}")),
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
        argv.append(str(socket_path.with_suffix(".sqlite3")) if item == "{database}" else item)
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

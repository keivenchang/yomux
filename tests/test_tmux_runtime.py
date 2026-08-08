from pathlib import Path
import os
import shlex
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tests import tmux_runtime


def test_stop_isolated_tmux_runtime_declares_private_socket_for_kill_server(monkeypatch, tmp_path):
    calls = []
    socket_dir = tmp_path / "tmux-runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "private.sock"
    runtime = SimpleNamespace(tmux_binary="tmux", socket_path=socket_path, socket_dir=socket_dir, stopped=False)

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(tmux_runtime.subprocess, "run", fake_run)

    tmux_runtime.stop_isolated_tmux_runtime(runtime)

    assert calls == [
        ["tmux", "-S", str(socket_path), "list-panes", "-a", "-F", "#{pane_pid}"],
        ["tmux", "-S", str(socket_path), "kill-server"],
    ]


def test_stop_isolated_tmux_runtime_refuses_failed_kill_with_live_private_socket(monkeypatch, tmp_path):
    socket_dir = tmp_path / "tmux-runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "private.sock"
    socket_path.touch()
    runtime = SimpleNamespace(
        tmux_binary="tmux",
        socket_path=socket_path,
        socket_dir=socket_dir,
        stopped=False,
    )
    results = iter((
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"),
    ))
    monkeypatch.setattr(tmux_runtime.subprocess, "run", lambda *_args, **_kwargs: next(results))

    with pytest.raises(AssertionError, match="isolated tmux kill-server failed: permission denied"):
        tmux_runtime.stop_isolated_tmux_runtime(runtime)

    assert socket_dir.exists()
    assert runtime.stopped is False


def test_stop_isolated_tmux_runtime_refuses_live_pane_without_start_identity(monkeypatch, tmp_path):
    socket_dir = tmp_path / "tmux-runtime"
    socket_dir.mkdir()
    socket_path = socket_dir / "private.sock"
    socket_path.touch()
    runtime = SimpleNamespace(
        tmux_binary="tmux",
        socket_path=socket_path,
        socket_dir=socket_dir,
        stopped=False,
    )
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="43210\n", stderr="")

    monkeypatch.setattr(tmux_runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux_runtime, "process_start_identity", lambda _pid: None)
    monkeypatch.setattr(tmux_runtime, "pid_is_alive", lambda _pid: True)

    with pytest.raises(AssertionError, match="cannot establish isolated tmux pane start identity for PID 43210"):
        tmux_runtime.stop_isolated_tmux_runtime(runtime)

    assert calls == [
        ["tmux", "-S", str(socket_path), "list-panes", "-a", "-F", "#{pane_pid}"],
    ]
    assert socket_dir.exists()
    assert runtime.stopped is False


def test_wait_for_isolated_tmux_pane_exit_refuses_unavailable_identity_for_live_pid(monkeypatch):
    monkeypatch.setattr(tmux_runtime, "process_start_identity", lambda _pid: None)
    monkeypatch.setattr(tmux_runtime, "pid_is_alive", lambda _pid: True)

    with pytest.raises(AssertionError, match="cannot prove isolated tmux pane PID 43211 exited"):
        tmux_runtime.wait_for_isolated_tmux_pane_exit(((43211, "proc:123"),))


def test_wait_for_isolated_tmux_pane_exit_accepts_proven_absent_pid(monkeypatch):
    monkeypatch.setattr(tmux_runtime, "process_start_identity", lambda _pid: None)
    monkeypatch.setattr(tmux_runtime, "pid_is_alive", lambda _pid: False)

    tmux_runtime.wait_for_isolated_tmux_pane_exit(((43212, "proc:124"),))


def test_stop_isolated_tmux_runtime_waits_for_the_exact_pane_exit_side_effect(monkeypatch, tmp_path):
    entered_fifo = tmp_path / "pane-retiring.fifo"
    release_fifo = tmp_path / "pane-release.fifo"
    os.mkfifo(entered_fifo)
    os.mkfifo(release_fifo)
    command_source = "\n".join((
        "import signal",
        f"entered = {str(entered_fifo)!r}",
        f"release = {str(release_fifo)!r}",
        "def retire(*_args):",
        "    with open(entered, 'wb', buffering=0) as stream:",
        "        stream.write(b'1')",
        "    with open(release, 'rb', buffering=0) as stream:",
        "        stream.read(1)",
        "signal.signal(signal.SIGHUP, retire)",
        "print('READY', flush=True)",
        "signal.pause()",
    ))
    session = "fixture-retirement"
    runtime = tmux_runtime.start_isolated_tmux_runtime(
        monkeypatch,
        tmp_path,
        session_commands={session: f"{shlex.quote(sys.executable)} -c {shlex.quote(command_source)}"},
    )
    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        (session,),
        lambda values: "READY" in values[session],
        timeout=5,
    )
    assert ready, panes

    errors = []
    stopper = threading.Thread(
        target=lambda: _capture_stop_error(runtime, errors),
        name="fixture-tmux-stop",
        daemon=True,
    )
    stopper.start()
    with entered_fifo.open("rb", buffering=0) as stream:
        assert stream.read(1) == b"1"
    returned_before_release = not stopper.is_alive()
    with release_fifo.open("wb", buffering=0) as stream:
        stream.write(b"1")
    stopper.join(timeout=5)

    assert returned_before_release is False
    assert not stopper.is_alive()
    assert errors == []


def _capture_stop_error(runtime, errors):
    try:
        tmux_runtime.stop_isolated_tmux_runtime(runtime)
    except BaseException as error:
        errors.append(error)


def test_adaptive_tmux_poll_interval_keeps_a_fast_observation_window_then_caps():
    intervals = [tmux_runtime.adaptive_tmux_poll_interval(index) for index in range(10)]

    assert intervals[:5] == [0.05] * 5
    assert intervals[5:] == [0.1, 0.2, 0.4, 0.4, 0.4]


def test_wait_for_isolated_tmux_panes_returns_immediately_without_sleep(monkeypatch):
    captures = []
    sleeps = []
    runtime = SimpleNamespace()
    monkeypatch.setattr(
        tmux_runtime,
        "capture_isolated_tmux_pane",
        lambda _runtime, session, *, join_wrapped_lines: captures.append(session) or "ready",
    )

    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        ["one"],
        lambda values: values["one"] == "ready",
        clock=lambda: 0.0,
        sleeper=sleeps.append,
    )

    assert ready is True
    assert panes == {"one": "ready"}
    assert captures == ["one"]
    assert sleeps == []


def test_wait_for_isolated_tmux_panes_adapts_then_caps_and_captures_all_sessions_once_per_pass(monkeypatch):
    now = [0.0]
    sleeps = []
    capture_count = [0]
    passes = [0]
    runtime = SimpleNamespace()

    def capture(_runtime, session, *, join_wrapped_lines):
        capture_count[0] += 1
        if session == "one":
            passes[0] += 1
        return "ready" if passes[0] >= 9 else f"waiting-{session}"

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(tmux_runtime, "capture_isolated_tmux_pane", capture)
    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        ["one", "two"],
        lambda values: values["one"] == values["two"] == "ready",
        timeout=10,
        clock=lambda: now[0],
        sleeper=sleep,
    )

    assert ready is True
    assert panes == {"one": "ready", "two": "ready"}
    assert sleeps == [0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.2, 0.4]
    assert capture_count[0] == 18


def test_wait_for_isolated_tmux_panes_honors_a_fixed_interval_and_returns_last_capture_on_timeout(monkeypatch):
    now = [0.0]
    sleeps = []
    captures = []
    runtime = SimpleNamespace()

    def capture(_runtime, session, *, join_wrapped_lines):
        captures.append(session)
        return "still-waiting"

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(tmux_runtime, "capture_isolated_tmux_pane", capture)
    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        ["one"],
        lambda _values: False,
        timeout=0.3,
        poll_interval=0.2,
        clock=lambda: now[0],
        sleeper=sleep,
    )

    assert ready is False
    assert panes == {"one": "still-waiting"}
    assert captures == ["one", "one", "one"]
    assert sleeps == pytest.approx([0.2, 0.1])


def test_e2e_auto_approve_routes_tmux_waits_through_the_selenium_free_shared_owner():
    source = Path(__file__).with_name("test_e2e_auto_approve.py").read_text(encoding="utf-8")

    assert "from tests.tmux_runtime import wait_for_isolated_tmux_panes" in source
    assert "def _wait_until(" not in source
    assert "time.sleep(0.4)" not in source

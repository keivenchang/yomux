"""Selenium-free private tmux runtime and observable wait helpers for integration tests."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from yolomux_lib.infra.background_owner import pid_is_alive
from yolomux_lib.host_identity import process_start_identity
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV


TMUX_WAIT_INITIAL_POLL_SECONDS = 0.05
TMUX_WAIT_MAX_POLL_SECONDS = 0.4
TMUX_WAIT_FAST_ATTEMPTS = 4
TMUX_PROCESS_EXIT_TIMEOUT_SECONDS = 5.0
TMUX_PROCESS_EXIT_POLL_SECONDS = 0.01
_TMUX_PROCESS_EXIT_POLL = threading.Event()


def adaptive_tmux_poll_interval(attempt: int, *, initial: float = TMUX_WAIT_INITIAL_POLL_SECONDS, maximum: float = TMUX_WAIT_MAX_POLL_SECONDS, fast_attempts: int = TMUX_WAIT_FAST_ATTEMPTS) -> float:
    """Return a bounded fast-then-backoff interval for test-only tmux observations."""

    safe_initial = max(0.0, float(initial))
    safe_maximum = max(safe_initial, float(maximum))
    exponent = max(0, int(attempt) - max(0, int(fast_attempts)))
    return min(safe_maximum, safe_initial * (2**exponent))


def run_isolated_tmux(runtime, *args: str, timeout: float = 8, declared_socket: bool = False):
    tmux_args = ["-S", str(runtime.socket_path)] if declared_socket else runtime.tmux_args
    return subprocess.run(
        [runtime.tmux_binary, *tmux_args, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def create_isolated_tmux_session(
    runtime,
    session: str,
    *,
    columns: int,
    rows: int,
    command: str,
    session_cwd: str | Path | None = None,
) -> None:
    """Create one fixture-owned session beneath the runtime's declared cwd."""

    owned_cwd = Path(session_cwd) if session_cwd is not None else runtime.session_cwd
    result = run_isolated_tmux(
        runtime,
        "new-session",
        "-d",
        "-s",
        session,
        "-x",
        str(columns),
        "-y",
        str(rows),
        "-c",
        str(owned_cwd),
        command,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"isolated tmux session failed: {result.stderr or result.stdout}")


def capture_isolated_tmux_pane_identities(runtime) -> tuple[tuple[int, str], ...]:
    """Capture the exact live pane processes whose exit effects teardown must own."""

    result = run_isolated_tmux(
        runtime,
        "list-panes",
        "-a",
        "-F",
        "#{pane_pid}",
        timeout=5,
        declared_socket=True,
    )
    if result.returncode != 0:
        if not runtime.socket_path.exists() and "no server running" in str(result.stderr or "").lower():
            return ()
        raise AssertionError(f"isolated tmux pane inventory failed: {result.stderr or result.stdout}")
    captured = []
    for raw_pid in str(result.stdout or "").splitlines():
        try:
            pid = int(raw_pid.strip())
        except ValueError as error:
            raise AssertionError(f"isolated tmux returned an invalid pane PID: {raw_pid!r}") from error
        identity = process_start_identity(pid)
        if identity is None:
            if pid_is_alive(pid):
                raise AssertionError(f"cannot establish isolated tmux pane start identity for PID {pid}")
            continue
        captured.append((pid, identity))
    return tuple(captured)


def wait_for_isolated_tmux_pane_exit(
    identities: Iterable[tuple[int, str]],
    *,
    timeout: float = TMUX_PROCESS_EXIT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until each captured pane process has completed its exact exit lifecycle."""

    expected = tuple(identities)
    deadline = clock() + max(0.0, float(timeout))
    while True:
        retained = []
        for pid, identity in expected:
            current_identity = process_start_identity(pid)
            if current_identity == identity:
                retained.append((pid, identity))
            elif current_identity is None and pid_is_alive(pid):
                if sys.platform == "darwin":
                    # libproc stops exposing birth identity once an exited child is a zombie;
                    # the matching identity was already captured before fixture shutdown.
                    continue
                raise AssertionError(
                    f"cannot prove isolated tmux pane PID {pid} exited because its start identity is unavailable"
                )
        if not retained:
            return
        remaining = deadline - clock()
        if remaining <= 0:
            raise AssertionError(f"isolated tmux retained pane process identities after kill-server: {tuple(retained)}")
        _TMUX_PROCESS_EXIT_POLL.wait(min(TMUX_PROCESS_EXIT_POLL_SECONDS, remaining))


def remove_isolated_tmux_socket_dir(socket_dir: Path) -> None:
    try:
        shutil.rmtree(socket_dir)
    except FileNotFoundError:
        return
    assert not socket_dir.exists(), f"isolated tmux socket directory remained after cleanup: {socket_dir}"


def capture_isolated_tmux_pane(runtime, session: str, timeout: float = 8, *, join_wrapped_lines: bool = False) -> str:
    args = ["capture-pane", "-p"]
    if join_wrapped_lines:
        args.append("-J")
    return run_isolated_tmux(runtime, *args, "-t", f"{session}:", timeout=timeout).stdout or ""


def wait_for_isolated_tmux_panes(
    runtime,
    sessions: Iterable[str],
    predicate: Callable[[dict[str, str]], bool],
    timeout: float = 20,
    poll_interval: float | None = None,
    *,
    join_wrapped_lines: bool = False,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], object] = time.sleep,
) -> tuple[bool, dict[str, str]]:
    """Capture all requested panes once per pass until their observable state is ready.

    The default starts with four 50ms observations, then backs off to the historic 400ms cap.
    A fixed ``poll_interval`` remains available for tests whose elapsed cadence is itself relevant.
    """

    session_names = list(sessions)
    deadline = clock() + max(0.0, float(timeout))
    panes: dict[str, str] = {}
    attempt = 0
    while True:
        panes = {
            session: capture_isolated_tmux_pane(runtime, session, join_wrapped_lines=join_wrapped_lines)
            for session in session_names
        }
        if predicate(panes):
            return True, panes
        remaining = deadline - clock()
        if remaining <= 0:
            return False, panes
        delay = float(poll_interval) if poll_interval is not None else adaptive_tmux_poll_interval(attempt)
        sleeper(min(max(0.0, delay), remaining))
        attempt += 1


def start_isolated_tmux_runtime(
    monkeypatch,
    tmp_path: Path,
    session_count: int = 1,
    *,
    session_commands: dict[str, str] | None = None,
    columns: int = 120,
    rows: int = 36,
    session_cwd: str | Path | None = None,
):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    socket_dir = Path(os.environ["YOLOMUX_TEST_ROOT"]) / "tmux" / f"e-{os.getpid()}-{uuid.uuid4().hex[:4]}"
    socket_dir.parent.mkdir(mode=0o700, exist_ok=True)
    socket_dir.mkdir(mode=0o700)
    socket_path = socket_dir / "s"
    commands = dict(session_commands or {})
    session_names = list(commands) if session_commands is not None else [f"yt-{os.getpid()}-{uuid.uuid4().hex[:10]}-{index + 1}" for index in range(session_count)]
    if not session_names:
        remove_isolated_tmux_socket_dir(socket_dir)
        raise ValueError("at least one isolated tmux session is required")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    monkeypatch.setenv("HISTFILE", os.devnull)
    owned_cwd = Path(session_cwd) if session_cwd is not None else tmp_path
    runtime = SimpleNamespace(tmux_binary=tmux_binary, tmux_args=["-S", str(socket_path)], socket_path=socket_path, socket_dir=socket_dir, sessions=session_names, session_cwd=owned_cwd, stopped=False)
    try:
        for session in session_names:
            command = commands.get(session)
            create_isolated_tmux_session(
                runtime,
                session,
                columns=columns,
                rows=rows,
                command=command if command is not None else "exec /bin/bash --noprofile --norc",
            )
            if command is None:
                run_isolated_tmux(runtime, "send-keys", "-t", f"{session}:", f"printf 'isolated {session}\\n'", "Enter", timeout=5)
        return runtime
    except Exception:
        stop_isolated_tmux_runtime(runtime)
        raise


def start_isolated_default_tmux_runtime(monkeypatch, tmp_path: Path, session_count: int = 1, *, columns: int = 120, rows: int = 36):
    """Start fixture-owned tmux's default server without exposing the user's default server.

    ``TMUX_TMPDIR`` changes tmux's default socket directory. The watcher under
    test therefore invokes exactly ``tmux -C attach-session`` with neither an
    inline socket nor ``YOLOMUX_TMUX_SOCKET``, while every client remains under
    this fixture's private directory.
    """

    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    socket_dir = Path(os.environ["YOLOMUX_TEST_ROOT"]) / "tmux" / f"d-{os.getpid()}-{uuid.uuid4().hex[:4]}"
    socket_dir.parent.mkdir(mode=0o700, exist_ok=True)
    socket_dir.mkdir(mode=0o700)
    session_names = [f"yt-{os.getpid()}-{uuid.uuid4().hex[:10]}-{index + 1}" for index in range(session_count)]
    monkeypatch.delenv(YOLOMUX_TMUX_SOCKET_ENV, raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(socket_dir))
    monkeypatch.setenv("HISTFILE", os.devnull)
    socket_path = socket_dir / f"tmux-{os.getuid()}" / "default"
    runtime = SimpleNamespace(tmux_binary=tmux_binary, tmux_args=[], socket_path=socket_path, socket_dir=socket_dir, sessions=session_names, session_cwd=tmp_path, stopped=False)
    try:
        for session in session_names:
            create_isolated_tmux_session(
                runtime,
                session,
                columns=columns,
                rows=rows,
                command="exec /bin/bash --noprofile --norc",
            )
        return runtime
    except Exception:
        stop_isolated_tmux_runtime(runtime)
        raise


def stop_isolated_tmux_runtime(runtime) -> None:
    if runtime is None:
        return
    if runtime.stopped:
        return
    # Finalizers can run after monkeypatch restores TMUX_TMPDIR, so cleanup must
    # name the fixture-owned socket instead of resolving a new ambient default.
    pane_identities = capture_isolated_tmux_pane_identities(runtime)
    result = run_isolated_tmux(runtime, "kill-server", timeout=5, declared_socket=True)
    if result.returncode != 0:
        server_absent = (
            not runtime.socket_path.exists()
            and "no server running" in str(result.stderr or "").lower()
        )
        assert server_absent, f"isolated tmux kill-server failed: {result.stderr or result.stdout}"
    wait_for_isolated_tmux_pane_exit(pane_identities)
    remove_isolated_tmux_socket_dir(runtime.socket_dir)
    runtime.stopped = True

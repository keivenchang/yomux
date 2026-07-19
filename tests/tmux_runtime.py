"""Selenium-free private tmux runtime and observable wait helpers for integration tests."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
import os
import shutil
import subprocess
import time
from types import SimpleNamespace
import uuid

import pytest

from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV


TMUX_WAIT_INITIAL_POLL_SECONDS = 0.05
TMUX_WAIT_MAX_POLL_SECONDS = 0.4
TMUX_WAIT_FAST_ATTEMPTS = 4


def adaptive_tmux_poll_interval(attempt: int, *, initial: float = TMUX_WAIT_INITIAL_POLL_SECONDS, maximum: float = TMUX_WAIT_MAX_POLL_SECONDS, fast_attempts: int = TMUX_WAIT_FAST_ATTEMPTS) -> float:
    """Return a bounded fast-then-backoff interval for test-only tmux observations."""

    safe_initial = max(0.0, float(initial))
    safe_maximum = max(safe_initial, float(maximum))
    exponent = max(0, int(attempt) - max(0, int(fast_attempts)))
    return min(safe_maximum, safe_initial * (2**exponent))


def run_isolated_tmux(runtime, *args: str, timeout: float = 8):
    return subprocess.run(
        [runtime.tmux_binary, "-S", str(runtime.socket_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def capture_isolated_tmux_pane(runtime, session: str, timeout: float = 8) -> str:
    return run_isolated_tmux(runtime, "capture-pane", "-p", "-t", f"{session}:", timeout=timeout).stdout or ""


def wait_for_isolated_tmux_panes(
    runtime,
    sessions: Iterable[str],
    predicate: Callable[[dict[str, str]], bool],
    timeout: float = 20,
    poll_interval: float | None = None,
    *,
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
        panes = {session: capture_isolated_tmux_pane(runtime, session) for session in session_names}
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
):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    socket_dir = Path("/tmp") / f"yts-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    socket_dir.mkdir(mode=0o700)
    socket_path = socket_dir / "s"
    commands = dict(session_commands or {})
    session_names = list(commands) if session_commands is not None else [f"yt-{os.getpid()}-{uuid.uuid4().hex[:10]}-{index + 1}" for index in range(session_count)]
    if not session_names:
        shutil.rmtree(socket_dir, ignore_errors=True)
        raise ValueError("at least one isolated tmux session is required")
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    runtime = SimpleNamespace(tmux_binary=tmux_binary, socket_path=socket_path, socket_dir=socket_dir, sessions=session_names)
    try:
        for session in session_names:
            args = ["new-session", "-d", "-s", session, "-x", str(columns), "-y", str(rows)]
            command = commands.get(session)
            if command is not None:
                args.append(command)
            result = run_isolated_tmux(runtime, *args, timeout=10)
            if result.returncode != 0:
                raise AssertionError(f"isolated tmux session failed: {result.stderr or result.stdout}")
            if command is None:
                run_isolated_tmux(runtime, "send-keys", "-t", f"{session}:", f"printf 'isolated {session}\\n'", "Enter", timeout=5)
        return runtime
    except Exception:
        stop_isolated_tmux_runtime(runtime)
        raise


def stop_isolated_tmux_runtime(runtime) -> None:
    if runtime is None:
        return
    run_isolated_tmux(runtime, "kill-server", timeout=5)
    shutil.rmtree(runtime.socket_dir, ignore_errors=True)

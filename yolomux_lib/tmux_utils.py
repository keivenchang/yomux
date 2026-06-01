# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""tmux and process helpers without auth/config import side effects."""

from __future__ import annotations

import re
import subprocess
import time


def run_cmd(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")


def tmux(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return run_cmd(["tmux", *args], timeout=timeout)


def tmux_run(*args: str, check: bool = True, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    result = run_cmd(["tmux", *args], timeout=timeout)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
    return result


def tmux_session_target(session: str) -> str:
    return f"{session}:"


def session_sort_key(session: str) -> tuple[int, str, int]:
    match = re.fullmatch(r"yolomux(\d+)", session)
    if match:
        return 0, "yolomux", int(match.group(1))
    match = re.fullmatch(r"project(\d+)", session)
    if match:
        return 1, "project", int(match.group(1))
    return 2, session.lower(), 0


def list_tmux_session_names() -> tuple[list[str], str | None]:
    result = tmux(["list-sessions", "-F", "#{session_name}"], timeout=3.0)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "tmux list-sessions failed").strip()
        return [], error
    sessions = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(set(sessions), key=session_sort_key), None


def tmux_session_names() -> list[str]:
    sessions, error = list_tmux_session_names()
    return [] if error else sessions


def tmux_list_sessions() -> str | None:
    result = tmux_run("list-sessions", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def tmux_has_exact_session(session: str) -> bool:
    sessions, error = list_tmux_session_names()
    return error is None and session in sessions


def tmux_has_session(session: str) -> bool:
    return session in tmux_session_names()


def tmux_exact_target_from_sessions(target: str, sessions: list[str]) -> str:
    """Return a tmux target that cannot confuse a numeric session with a window."""
    if not target or target.startswith("%"):
        return target
    if target in sessions:
        return f"{target}:"
    return target


def tmux_exact_target(target: str) -> str:
    return tmux_exact_target_from_sessions(target, tmux_session_names())


def unique_session_names(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        session = value.strip()
        if not session or session in seen:
            continue
        seen.add(session)
        result.append(session)
    return sorted(result, key=session_sort_key)


def tmux_capture_pane(target: str, lines: int = 80, visible_only: bool = False) -> str | None:
    """Capture a tmux pane, using visible_only=True for prompt presence checks."""
    exact_target = tmux_exact_target(target)
    if visible_only:
        result = tmux_run("capture-pane", "-t", exact_target, "-p", check=False)
    else:
        result = tmux_run("capture-pane", "-t", exact_target, "-p", "-S", f"-{lines}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def tmux_send_enter(target: str) -> None:
    tmux_run("send-keys", "-t", tmux_exact_target(target), "Enter", check=False)


def tmux_send_option(target: str, option: int, selected_option: int | None = None) -> None:
    exact_target = tmux_exact_target(target)
    selected = selected_option if selected_option and selected_option > 0 else 1
    delta = option - selected
    key = "Down" if delta > 0 else "Up"
    for _ in range(min(abs(delta), 6)):
        tmux_run("send-keys", "-t", exact_target, key, check=False)
        time.sleep(0.1)
    tmux_send_enter(exact_target)


def tmux_send_option1(target: str, selected_option: int | None = None) -> None:
    tmux_send_option(target, 1, selected_option)


def tmux_send_option2(target: str, selected_option: int | None = None) -> None:
    tmux_send_option(target, 2, selected_option)

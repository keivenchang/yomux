# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""tmux and process helpers without auth/config import side effects."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time

from .cache import TtlCache

YOLOMUX_TMUX_SOCKET_ENV = "YOLOMUX_TMUX_SOCKET"


def run_cmd(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")


def tmux_command(args: list[str] | tuple[str, ...]) -> list[str]:
    socket_path = os.environ.get(YOLOMUX_TMUX_SOCKET_ENV, "").strip()
    command = ["tmux"]
    if socket_path:
        command.extend(["-S", socket_path])
    command.extend(str(arg) for arg in args)
    return command


def tmux(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return run_cmd(tmux_command(args), timeout=timeout)


def cmd_error(result: subprocess.CompletedProcess, fallback: str) -> str:
    """The stderr-or-stdout-or-fallback error message shared by every checked tmux/git/ps call site.

    `(result.stderr or result.stdout or "X").strip()` was written ~17 times.
    """
    return (result.stderr or result.stdout or fallback).strip()


def tmux_run(*args: str, check: bool = True, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    result = run_cmd(tmux_command(args), timeout=timeout)
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
        error = cmd_error(result, "tmux list-sessions failed")
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


# tmux_exact_target ran `tmux list-sessions` on EVERY capture, so the inline N×2-3 captures
# in prompt_and_screen_status each paid a list-sessions subprocess (a +3s hang point if tmux wedged).
# Cache the session-name resolution for a short window so a poll's captures reuse one resolution.
_SESSION_NAMES_TTL = 1.0
_session_names_cache = TtlCache(_SESSION_NAMES_TTL, max_entries=1)


def cached_session_names() -> list[str]:
    cached = _session_names_cache.get("names")
    if cached is not None:
        return list(cached)
    names = tmux_session_names()
    _session_names_cache.set("names", names)
    return names


def tmux_exact_target(target: str) -> str:
    # Skip the list-sessions resolution entirely for unambiguous targets (pane ids / already-qualified).
    if not target or target.startswith("%") or ":" in target:
        return target
    return tmux_exact_target_from_sessions(target, cached_session_names())


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


def tmux_capture_pane(target: str, lines: int = 80, visible_only: bool = False, timeout: float = 3.0) -> str | None:
    """Capture a tmux pane, using visible_only=True for prompt presence checks.

    an explicit short timeout so a wedged tmux fails the capture fast instead of blocking the
    request thread (the synchronous /api/auto-approve path runs several captures inline).
    """
    exact_target = tmux_exact_target(target)
    # -J rejoins lines that tmux wrapped across visual rows, so a command that wraps is
    # captured as one logical line. Without it, extract_command joins wrapped rows with a space and can
    # insert a spurious space mid-token (e.g. "rm -r"+"f /path" -> "rm -r f /path"), flipping a verdict.
    if visible_only:
        result = tmux_run("capture-pane", "-t", exact_target, "-p", "-J", check=False, timeout=timeout)
    else:
        result = tmux_run("capture-pane", "-t", exact_target, "-p", "-J", "-S", f"-{lines}", check=False, timeout=timeout)
    if result.returncode != 0:
        return None
    return result.stdout


def tmux_send_enter(target: str) -> None:
    tmux_run("send-keys", "-t", tmux_exact_target(target), "Enter", check=False)


def tmux_clear_input(target: str, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    """Clear the current prompt input line in a target pane without submitting it."""
    return tmux_run("send-keys", "-t", tmux_exact_target(target), "C-u", check=False, timeout=timeout)


def tmux_paste_text(target: str, text: str, submit: bool = False, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    """Paste exact text into a pane via a tmux buffer.

    This is the visible-send path for YO!agent actions. It avoids shell quoting and avoids sending user text as
    tmux key names; submission is a real Enter key after the paste, not a pasted newline.
    """
    exact_target = tmux_exact_target(target)
    buffer_name = f"yolomux-{secrets.token_hex(8)}"
    payload = str(text or "")
    load = subprocess.run(
        tmux_command(["load-buffer", "-b", buffer_name, "-"]),
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if load.returncode != 0:
        return load
    try:
        paste = tmux_run("paste-buffer", "-p", "-t", exact_target, "-b", buffer_name, check=False, timeout=timeout)
        if paste.returncode != 0 or not submit:
            return paste
        enter = tmux_run("send-keys", "-t", exact_target, "Enter", check=False, timeout=timeout)
        return enter if enter.returncode != 0 else paste
    finally:
        tmux_run("delete-buffer", "-b", buffer_name, check=False, timeout=1.0)


def tmux_move_to_option(target: str, option: int, selected_option: int | None = None) -> None:
    # walk the highlight to `option` WITHOUT pressing Enter, so the caller can re-verify the
    # highlight actually landed on the target before confirming (the menu can redraw/move during a walk).
    exact_target = tmux_exact_target(target)
    selected = selected_option if selected_option and selected_option > 0 else 1
    delta = option - selected
    key = "Down" if delta > 0 else "Up"
    for _ in range(min(abs(delta), 6)):
        tmux_run("send-keys", "-t", exact_target, key, check=False)
        time.sleep(0.1)


def tmux_send_option(target: str, option: int, selected_option: int | None = None) -> None:
    tmux_move_to_option(target, option, selected_option)
    tmux_send_enter(tmux_exact_target(target))


def tmux_send_option1(target: str, selected_option: int | None = None) -> None:
    tmux_send_option(target, 1, selected_option)


def tmux_send_option2(target: str, selected_option: int | None = None) -> None:
    tmux_send_option(target, 2, selected_option)

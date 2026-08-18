# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""tmux and process helpers without auth/config import side effects."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
from collections.abc import Sequence
from typing import Any

from ..cache import TtlCache

YOLOMUX_TMUX_SOCKET_ENV = "YOLOMUX_TMUX_SOCKET"
YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV = "YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER"
TMUX_CONTROL_MODE_FLAG = "-C"
TMUX_CLIENT_ATTACH_VERB = "attach-session"


class TmuxSocketTargetError(RuntimeError):
    """A tmux command would otherwise target an implicit server."""

    def __init__(self, verb: str, argv: Sequence[str]) -> None:
        self.verb = str(verb)
        self.argv = [str(item) for item in argv]
        super().__init__(
            f"refusing tmux {self.verb}: no tmux server was explicitly chosen; "
            f"refused argv: {self.argv}"
        )


def tmux_explicit_socket_argument(args: Sequence[str]) -> str:
    """Return an inline ``-S`` or ``-L`` target, if the caller chose one."""

    values = [str(item) for item in args]
    for index, value in enumerate(values[:-1]):
        if value in {"-S", "-L"}:
            return values[index + 1].strip()
    return ""


def tmux_readonly_control_attach(args: Sequence[str]) -> bool:
    """Whether a control-mode attach is restricted to observation by tmux itself."""

    values = [str(item) for item in args]
    if TMUX_CONTROL_MODE_FLAG not in values or TMUX_CLIENT_ATTACH_VERB not in values:
        return False
    return any(
        value == "-f" and "read-only" in values[index + 1].split(",")
        for index, value in enumerate(values[:-1])
    )


def tmux_guarded_verb(args: Sequence[str]) -> str:
    """Return a default-server command that policy must refuse.

    A server kill has no default-server opt-in. A session kill is allowed only
    when the deployment deliberately sets the exact D6 opt-in value; every
    missing, malformed, or false-ish value stays denied.

    A read-only control-mode attach is observational, so it remains available
    on the shared default server for the normal watcher configuration. A
    writable control-mode attach can mutate through tmux commands and remains
    guarded.
    """

    values = [str(item) for item in args]
    if "kill-server" in values:
        return "kill-server"
    if "kill-session" in values and os.environ.get(YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV) != "1":
        return "kill-session"
    if (
        TMUX_CONTROL_MODE_FLAG in values
        and TMUX_CLIENT_ATTACH_VERB in values
        and not tmux_readonly_control_attach(values)
    ):
        return f"{TMUX_CONTROL_MODE_FLAG} {TMUX_CLIENT_ATTACH_VERB}"
    return ""


def run_cmd(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")


def tmux_command(args: list[str] | tuple[str, ...]) -> list[str]:
    values = [str(arg) for arg in args]
    socket_path = os.environ.get(YOLOMUX_TMUX_SOCKET_ENV, "").strip()
    if not socket_path and not tmux_explicit_socket_argument(values):
        verb = tmux_guarded_verb(values)
        if verb:
            raise TmuxSocketTargetError(verb, values)
    command = ["tmux"]
    if socket_path:
        command.extend(["-S", socket_path])
    command.extend(values)
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


def tmux_session_client_rows(session: str) -> list[dict[str, Any]]:
    """Every client attached to `session`, with its dimensions and flags.

    Deliberately NOT limited to yolomux-spawned clients: under `window-size largest` any wider or
    taller client pins the shared window beyond the focused browser surface, so the active surface
    must be able to see -- and silence -- a hand-attached terminal too, not just sibling browsers.
    """
    fmt = "\t".join(("#{client_name}", "#{client_session}", "#{client_width}", "#{client_height}", "#{client_flags}"))
    result = tmux(["list-clients", "-F", fmt])
    if result.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    clean_session = str(session or "")
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 5 or parts[1] != clean_session:
            continue
        try:
            width = int(parts[2])
            height = int(parts[3])
        except ValueError:
            continue
        rows.append({"name": parts[0], "session": parts[1], "width": width, "height": height, "flags": parts[4]})
    return rows


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


def list_tmux_session_activity() -> tuple[dict[str, int], str | None]:
    """Read every session's last tmux activity timestamp in one subprocess."""

    fmt = "\t".join(("#{session_name}", "#{session_activity}"))
    result = tmux(["list-sessions", "-F", fmt], timeout=3.0)
    if result.returncode != 0:
        return {}, cmd_error(result, "tmux list-sessions failed")
    activity: dict[str, int] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0].strip():
            continue
        try:
            activity[parts[0].strip()] = int(parts[1])
        except ValueError:
            continue
    return activity, None


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


def tmux_capture_pane_styled(target: str, lines: int = 80, visible_only: bool = False, timeout: float = 3.0) -> str | None:
    """Capture pane text with SGR attributes preserved for UI-state checks that need dim/ghost text."""
    exact_target = tmux_exact_target(target)
    if visible_only:
        result = tmux_run("capture-pane", "-e", "-t", exact_target, "-p", "-J", check=False, timeout=timeout)
    else:
        result = tmux_run("capture-pane", "-e", "-t", exact_target, "-p", "-J", "-S", f"-{lines}", check=False, timeout=timeout)
    if result.returncode != 0:
        return None
    return result.stdout


def tmux_send_enter(target: str) -> None:
    tmux_run("send-keys", "-t", tmux_exact_target(target), "Enter", check=False)


def tmux_clear_input(target: str, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    """Clear the current prompt input line in a target pane without submitting it."""
    return tmux_run("send-keys", "-t", tmux_exact_target(target), "C-e", "C-u", check=False, timeout=timeout)


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

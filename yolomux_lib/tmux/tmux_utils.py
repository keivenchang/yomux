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


YOLOMUX_TMUX_SOCKET_ENV = "YOLOMUX_TMUX_SOCKET"
YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV = "YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER"
TMUX_CONTROL_MODE_FLAG = "-C"
TMUX_CLIENT_ATTACH_VERB = "attach-session"
TMUX_KILL_SESSION_VERB = "kill-session"
TMUX_KILL_SERVER_VERB = "kill-server"
TMUX_TARGET_FLAG = "-t"
# tmux resolves a bare `name:` target by exact name, then prefix, then fnmatch. `=name`
# forces the exact match only, so a kill can never walk from a dead `1` onto a live `12`.
TMUX_EXACT_TARGET_PREFIX = "="
# kill-session -a kills every session EXCEPT the target; it can never be a scoped kill.
TMUX_KILL_ALL_BUT_TARGET_FLAG = "-a"


class TmuxSocketTargetError(RuntimeError):
    """A tmux command would otherwise target an implicit server or an inexact session."""

    IMPLICIT_SERVER_REASON = "no tmux server was explicitly chosen"

    def __init__(self, verb: str, argv: Sequence[str], reason: str = IMPLICIT_SERVER_REASON) -> None:
        self.verb = str(verb)
        self.argv = [str(item) for item in argv]
        self.reason = str(reason)
        super().__init__(f"refusing tmux {self.verb}: {self.reason}; refused argv: {self.argv}")


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


def tmux_target_values(args: Sequence[str]) -> list[str]:
    """Every ``-t`` value in argv, in order."""

    values = [str(item) for item in args]
    return [values[index + 1] for index, value in enumerate(values[:-1]) if value == TMUX_TARGET_FLAG]


def tmux_exact_session_target(target: str) -> bool:
    """Whether `target` names exactly one session and cannot be prefix- or glob-resolved."""

    text = str(target)
    if not text.startswith(TMUX_EXACT_TARGET_PREFIX):
        return False
    session = text[len(TMUX_EXACT_TARGET_PREFIX):].split(":", 1)[0]
    return bool(session) and session == session.strip()


def tmux_guarded_refusal(args: Sequence[str], *, server_is_explicit: bool) -> tuple[str, str]:
    """The (verb, reason) a tmux command must be refused for, or ``("", "")`` when it is allowed.

    One owner, two independent authorities:

    * Target precision, on EVERY server: a session kill must name exactly one session in the
      exact ``=name:`` form. tmux otherwise resolves ``1:`` by prefix onto ``12``, and
      ``kill-session -a`` kills every session except the target.
    * Server choice, on the default server only: a server kill has no opt-in at all. A session
      kill is allowed only when the deployment sets the exact D6 opt-in value; every missing,
      malformed, or false-ish value stays denied.

    A read-only control-mode attach is observational, so it remains available on the shared
    default server for the normal watcher configuration. A writable control-mode attach can
    mutate through tmux commands and remains guarded.
    """

    values = [str(item) for item in args]
    if TMUX_KILL_SESSION_VERB in values:
        if TMUX_KILL_ALL_BUT_TARGET_FLAG in values:
            return TMUX_KILL_SESSION_VERB, f"{TMUX_KILL_SESSION_VERB} {TMUX_KILL_ALL_BUT_TARGET_FLAG} kills every other session"
        targets = tmux_target_values(values)
        if len(targets) != 1:
            return TMUX_KILL_SESSION_VERB, f"{TMUX_KILL_SESSION_VERB} needs exactly one {TMUX_TARGET_FLAG} target, found {len(targets)}"
        if not tmux_exact_session_target(targets[0]):
            return TMUX_KILL_SESSION_VERB, (
                f"{TMUX_KILL_SESSION_VERB} target {targets[0]!r} is prefix-resolvable; "
                f"it must be the exact '{TMUX_EXACT_TARGET_PREFIX}session:' form"
            )
    if server_is_explicit:
        return "", ""
    if TMUX_KILL_SERVER_VERB in values:
        return TMUX_KILL_SERVER_VERB, TmuxSocketTargetError.IMPLICIT_SERVER_REASON
    if TMUX_KILL_SESSION_VERB in values and os.environ.get(YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV) != "1":
        return TMUX_KILL_SESSION_VERB, TmuxSocketTargetError.IMPLICIT_SERVER_REASON
    if (
        TMUX_CONTROL_MODE_FLAG in values
        and TMUX_CLIENT_ATTACH_VERB in values
        and not tmux_readonly_control_attach(values)
    ):
        return f"{TMUX_CONTROL_MODE_FLAG} {TMUX_CLIENT_ATTACH_VERB}", TmuxSocketTargetError.IMPLICIT_SERVER_REASON
    return "", ""


def run_cmd(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")


def tmux_command(args: list[str] | tuple[str, ...]) -> list[str]:
    values = [str(arg) for arg in args]
    socket_path = os.environ.get(YOLOMUX_TMUX_SOCKET_ENV, "").strip()
    server_is_explicit = bool(socket_path or tmux_explicit_socket_argument(values))
    verb, reason = tmux_guarded_refusal(values, server_is_explicit=server_is_explicit)
    if verb:
        raise TmuxSocketTargetError(verb, values, reason)
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
    """The exact-match tmux target for `session`.

    Sessions are named `1`, `2`, `12`, so the bare `1:` form lets tmux prefix-resolve a target
    aimed at `1` onto `12` whenever `1` is gone or renamed. `=` accepts the exact name only.
    """
    return f"{TMUX_EXACT_TARGET_PREFIX}{session}:"


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


def tmux_exact_target(target: str) -> str:
    """Pin `target`'s session to the exact `=name:` form tmux cannot prefix- or glob-resolve.

    tmux resolves `1:` by exact name, then prefix, then fnmatch, so a capture or send-keys aimed
    at a dead `1` silently lands on the live `12`. Sessions here are literally named `1`, `2`,
    `12`. Pinning does not depend on the session existing, so an unknown name fails closed
    instead of walking onto a neighbour -- there is nothing to look up and no session list to read.

    Pane ids (`%3`), the current-session target (`:`) and the empty target carry no session name.
    """
    if not target or target.startswith("%") or target.startswith(TMUX_EXACT_TARGET_PREFIX):
        return target
    session, separator, rest = target.partition(":")
    if not session:
        return target
    # tmux_session_target is the one owner of the exact form; `rest` keeps any window/pane suffix.
    return f"{tmux_session_target(session)}{rest}" if separator else tmux_session_target(session)


def tmux_exact_target_from_sessions(target: str, sessions: list[str]) -> str:
    """Back-compat entry point for the auto-approve CLI; `sessions` is no longer consulted.

    Pinning used to append a bare `:` only for names present in `sessions` and let every other
    name through unpinned. The exact form is correct for a live session and fails closed for a
    dead one, so the session list decides nothing. Kept only because tools/auto_approve_tmux.py
    re-exports this name.
    """
    del sessions
    return tmux_exact_target(target)


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

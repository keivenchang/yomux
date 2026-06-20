# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Server-wide tmux signal snapshots.

The snapshot is intentionally read-only: it queries tmux formats and pane state
without attaching clients, so it cannot resize user windows.
"""

from __future__ import annotations

import ctypes
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

from .common import AGENT_COMMANDS
from .tmux_utils import cmd_error
from .tmux_utils import tmux_command
from .tmux_utils import session_sort_key
from .tmux_utils import tmux
from .tmux_utils import tmux_session_target


TMUX_SIGNAL_FIELD_SEPARATOR = "\t"

TMUX_WINDOW_SIGNAL_FIELDS = (
    "session_name",
    "session_id",
    "session_activity",
    "session_last_attached",
    "session_attached",
    "session_attached_list",
    "window_index",
    "window_id",
    "window_name",
    "window_active",
    "window_activity",
    "window_activity_flag",
    "window_bell_flag",
    "window_silence_flag",
    "window_active_clients",
    "window_active_clients_list",
    "window_panes",
    "window_width",
    "window_height",
    "window_zoomed_flag",
    "window_layout",
    "window_visible_layout",
)

TMUX_PANE_SIGNAL_FIELDS = (
    "session_name",
    "window_index",
    "window_id",
    "pane_index",
    "pane_id",
    "pane_active",
    "pane_current_path",
    "pane_current_command",
    "pane_title",
    "pane_dead",
    "pane_dead_status",
    "pane_dead_signal",
    "pane_dead_time",
    "alternate_on",
    "pane_in_mode",
    "pane_mode",
    "pane_input_off",
    "pane_synchronized",
    "pane_pid",
    "pane_width",
    "pane_height",
    "history_size",
    "history_bytes",
)

TMUX_CLIENT_SIGNAL_FIELDS = (
    "client_name",
    "client_session",
    "client_activity",
    "client_width",
    "client_height",
    "client_flags",
    "client_control_mode",
    "client_readonly",
    "client_user",
)

TMUX_SIGNAL_SUBSCRIPTIONS = (
    (
        "yolomux-window-activity",
        "#{session_name}:#{window_index}:#{window_activity}:#{window_activity_flag}:#{window_bell_flag}:#{window_silence_flag}:#{window_active_clients}",
    ),
    (
        "yolomux-window-layout",
        "#{session_name}:#{window_index}:#{window_zoomed_flag}:#{window_layout}:#{window_visible_layout}",
    ),
)

TMUX_SIGNAL_CONTROL_EVENTS = frozenset({
    "client-detached",
    "client-session-changed",
    "layout-change",
    "output",
    "extended-output",
    "pane-mode-changed",
    "session-changed",
    "session-renamed",
    "session-window-changed",
    "sessions-changed",
    "subscription-changed",
    "window-add",
    "window-close",
    "window-pane-changed",
    "window-renamed",
})

TMUX_SIGNAL_HOOKS = (
    "pane-exited",
    "pane-died",
    "alert-activity",
    "alert-silence",
    "alert-bell",
    "client-active",
    "client-resized",
    "window-resized",
)
TMUX_SIGNAL_HOOK_INDEX = 7717
TMUX_SIGNAL_MONITOR_SILENCE_SECONDS = 60
TMUX_SIGNAL_EVENT_RETRY_SECONDS = 2.003


def tmux_signal_format(fields: tuple[str, ...]) -> str:
    return TMUX_SIGNAL_FIELD_SEPARATOR.join(f"#{{{field}}}" for field in fields)


def tmux_control_attach_command(session: str) -> list[str]:
    return tmux_command([
        "-C",
        "attach-session",
        "-f",
        "read-only,ignore-size",
        "-t",
        tmux_session_target(session),
    ])


# PR_SET_PDEATHSIG (linux/prctl.h) — value is stable across Linux releases.
_PR_SET_PDEATHSIG = 1
try:
    _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:
    _LIBC = None


def set_control_client_parent_death_signal() -> None:
    """preexec_fn: ask the kernel to SIGTERM this control client when the yolomux parent dies.

    The tmux control-mode signal client is a child of the yolomux server. A graceful SIGTERM
    lets run_control_client's finally terminate it, but a hard SIGKILL or crash skips that
    teardown, orphaning the `tmux -C attach-session` client on the shared socket where it lingers
    forever — one leaked read-only/ignore-size client per hard kill. PR_SET_PDEATHSIG makes the
    kernel reap it together with the parent. Runs in the forked child before exec, so it does
    nothing but one prctl syscall on the pre-loaded libc to stay fork-safe; Linux-only and
    best-effort (no-op when libc/prctl is unavailable).
    """
    if _LIBC is not None:
        _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


def tmux_signal_subscription_commands() -> list[list[str]]:
    return [["refresh-client", "-B", f"{name}:{fmt}"] for name, fmt in TMUX_SIGNAL_SUBSCRIPTIONS]


def install_tmux_signal_control_subscriptions(process: subprocess.Popen[str]) -> None:
    if process.stdin is None:
        return
    try:
        for command in tmux_signal_subscription_commands():
            process.stdin.write(" ".join(command) + "\n")
        process.stdin.flush()
    except OSError:
        return


def tmux_control_event_type(line: str) -> str:
    text = str(line or "").strip()
    if not text.startswith("%"):
        return ""
    return text[1:].split(None, 1)[0]


def tmux_control_event_relevant(line: str) -> bool:
    return tmux_control_event_type(line) in TMUX_SIGNAL_CONTROL_EVENTS


def install_tmux_signal_monitoring(sessions: Sequence[str], timeout: float = 1.5) -> list[str]:
    errors: list[str] = []
    for session in [str(item or "").strip() for item in sessions if str(item or "").strip()]:
        target = tmux_session_target(session)
        for option, value in (
            ("monitor-activity", "on"),
            ("monitor-silence", str(TMUX_SIGNAL_MONITOR_SILENCE_SECONDS)),
        ):
            result = tmux(["set-window-option", "-t", target, option, value], timeout=timeout)
            if result.returncode != 0:
                errors.append(cmd_error(result, f"tmux set-window-option {option} failed"))
    for hook in TMUX_SIGNAL_HOOKS:
        result = tmux([
            "set-hook",
            "-g",
            f"{hook}[{TMUX_SIGNAL_HOOK_INDEX}]",
            "refresh-client",
        ], timeout=timeout)
        if result.returncode != 0:
            errors.append(cmd_error(result, f"tmux set-hook {hook} failed"))
    return errors


class TmuxSignalEventWatcher:
    def __init__(
        self,
        sessions: Callable[[], Sequence[str]],
        on_event: Callable[[dict[str, Any]], None],
        on_error: Callable[[str], None] | None = None,
        retry_seconds: float = TMUX_SIGNAL_EVENT_RETRY_SECONDS,
    ) -> None:
        self.sessions = sessions
        self.on_event = on_event
        self.on_error = on_error
        self.retry_seconds = max(0.25, float(retry_seconds))
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> bool:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, name="tmux-signal-events", daemon=True)
            self.thread.start()
            return True

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()

    def emit_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)

    def run(self) -> None:
        while not self.stop_event.is_set():
            sessions = [str(item or "").strip() for item in self.sessions() if str(item or "").strip()]
            if not sessions:
                self.stop_event.wait(self.retry_seconds)
                continue
            for error in install_tmux_signal_monitoring(sessions):
                self.emit_error(error)
            self.run_control_client(sessions[0])
            self.stop_event.wait(self.retry_seconds)

    def run_control_client(self, session: str) -> None:
        command = tmux_control_attach_command(session)
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                preexec_fn=set_control_client_parent_death_signal,
            )
        except OSError as exc:
            self.emit_error(f"tmux control-mode start failed: {exc}")
            return
        with self.lock:
            self.process = process
        install_tmux_signal_control_subscriptions(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if self.stop_event.is_set():
                    break
                if not tmux_control_event_relevant(line):
                    continue
                self.on_event({
                    "type": tmux_control_event_type(line),
                    "line": line.strip(),
                    "time": time.time(),
                })
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()


def int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def int_value(value: Any, default: int = 0) -> int:
    parsed = int_or_none(value)
    return default if parsed is None else parsed


def bool_value(value: Any) -> bool:
    return str(value).strip() == "1"


def window_key(session: str, window_index: str) -> str:
    return f"{session}:{window_index}"


def row_map(line: str, fields: tuple[str, ...]) -> dict[str, str] | None:
    parts = line.split(TMUX_SIGNAL_FIELD_SEPARATOR)
    if len(parts) != len(fields):
        return None
    return dict(zip(fields, parts, strict=True))


def parse_window_signal_row(line: str) -> dict[str, Any] | None:
    raw = row_map(line, TMUX_WINDOW_SIGNAL_FIELDS)
    if raw is None:
        return None
    session = raw["session_name"]
    window_index = raw["window_index"]
    activity_ts = int_value(raw["window_activity"])
    return {
        "key": window_key(session, window_index),
        "session": session,
        "session_id": raw["session_id"],
        "session_activity_ts": int_value(raw["session_activity"]),
        "session_last_attached_ts": int_value(raw["session_last_attached"]),
        "session_attached": int_value(raw["session_attached"]),
        "session_attached_list": raw["session_attached_list"],
        "window_index": window_index,
        "window_id": raw["window_id"],
        "window_name": raw["window_name"],
        "active": bool_value(raw["window_active"]),
        "activity_ts": activity_ts,
        "activity_age_seconds": max(0.0, time.time() - activity_ts) if activity_ts > 0 else None,
        "activity_flag": bool_value(raw["window_activity_flag"]),
        "bell_flag": bool_value(raw["window_bell_flag"]),
        "silence_flag": bool_value(raw["window_silence_flag"]),
        "active_clients": int_value(raw["window_active_clients"]),
        "active_clients_list": raw["window_active_clients_list"],
        "pane_count": int_value(raw["window_panes"]),
        "width": int_value(raw["window_width"]),
        "height": int_value(raw["window_height"]),
        "zoomed": bool_value(raw["window_zoomed_flag"]),
        "layout": raw["window_layout"],
        "visible_layout": raw["window_visible_layout"],
        "panes": [],
    }


def parse_pane_signal_row(line: str) -> dict[str, Any] | None:
    raw = row_map(line, TMUX_PANE_SIGNAL_FIELDS)
    if raw is None:
        return None
    session = raw["session_name"]
    window_index = raw["window_index"]
    command = raw["pane_current_command"]
    return {
        "key": f"{window_key(session, window_index)}.{raw['pane_index']}",
        "window_key": window_key(session, window_index),
        "session": session,
        "window_index": window_index,
        "window_id": raw["window_id"],
        "pane_index": raw["pane_index"],
        "pane_id": raw["pane_id"],
        "target": raw["pane_id"] or f"{session}:{window_index}.{raw['pane_index']}",
        "active": bool_value(raw["pane_active"]),
        "current_path": raw["pane_current_path"],
        "current_command": command,
        "title": raw["pane_title"],
        "agent": command if command in AGENT_COMMANDS else "",
        "dead": bool_value(raw["pane_dead"]),
        "dead_status": int_or_none(raw["pane_dead_status"]),
        "dead_signal": int_or_none(raw["pane_dead_signal"]),
        "dead_time": int_or_none(raw["pane_dead_time"]),
        "alternate_on": bool_value(raw["alternate_on"]),
        "in_mode": bool_value(raw["pane_in_mode"]),
        "mode": raw["pane_mode"],
        "input_off": bool_value(raw["pane_input_off"]),
        "synchronized": bool_value(raw["pane_synchronized"]),
        "pid": int_or_none(raw["pane_pid"]),
        "width": int_value(raw["pane_width"]),
        "height": int_value(raw["pane_height"]),
        "history_size": int_value(raw["history_size"]),
        "history_bytes": int_value(raw["history_bytes"]),
    }


def parse_client_signal_row(line: str) -> dict[str, Any] | None:
    raw = row_map(line, TMUX_CLIENT_SIGNAL_FIELDS)
    if raw is None:
        return None
    return {
        "name": raw["client_name"],
        "session": raw["client_session"],
        "activity_ts": int_value(raw["client_activity"]),
        "width": int_value(raw["client_width"]),
        "height": int_value(raw["client_height"]),
        "flags": raw["client_flags"],
        "control_mode": bool_value(raw["client_control_mode"]),
        "readonly": bool_value(raw["client_readonly"]),
        "user": raw["client_user"],
    }


def tmux_signal_list_items(value: Any) -> list[str]:
    return [item for item in str(value or "").replace(",", " ").split() if item]


def client_area(client: dict[str, Any]) -> int:
    return max(0, int_value(client.get("width"))) * max(0, int_value(client.get("height")))


def active_client_details_for_window(window: dict[str, Any], clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = set(tmux_signal_list_items(window.get("active_clients_list", "")))
    if not names:
        return []
    return [
        client
        for client in clients
        if client.get("name") in names
    ]


def authoritative_client_for_window(window: dict[str, Any], clients: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        client
        for client in active_client_details_for_window(window, clients)
        if client.get("control_mode") is not True and int_value(client.get("width")) > 0 and int_value(client.get("height")) > 0
    ]
    if not candidates:
        return None
    winner = max(candidates, key=lambda client: (int_value(client.get("activity_ts")), client_area(client), str(client.get("name") or "")))
    return {
        "client_name": winner.get("name", ""),
        "client_user": winner.get("user", ""),
        "activity_ts": int_value(winner.get("activity_ts")),
        "width": int_value(winner.get("width")),
        "height": int_value(winner.get("height")),
        "readonly": bool(winner.get("readonly")),
        "flags": winner.get("flags", ""),
        "reason": "most-recent-active-viewer",
    }


def parse_tmux_signal_snapshot(
    windows_stdout: str,
    panes_stdout: str,
    clients_stdout: str = "",
    *,
    errors: list[str] | None = None,
    generated_at: float | None = None,
    compute_ms: float = 0.0,
) -> dict[str, Any]:
    parse_errors: list[str] = list(errors or [])
    windows: list[dict[str, Any]] = []
    windows_by_key: dict[str, dict[str, Any]] = {}
    sessions: dict[str, dict[str, Any]] = {}
    for line in windows_stdout.splitlines():
        if not line.strip():
            continue
        window = parse_window_signal_row(line)
        if window is None:
            parse_errors.append("invalid tmux window signal row")
            continue
        windows.append(window)
        windows_by_key[window["key"]] = window
        sessions.setdefault(window["session"], {
            "name": window["session"],
            "session_id": window["session_id"],
            "activity_ts": window["session_activity_ts"],
            "last_attached_ts": window["session_last_attached_ts"],
            "attached": window["session_attached"],
            "attached_list": window["session_attached_list"],
            "windows": [],
        })["windows"].append(window["key"])

    panes: list[dict[str, Any]] = []
    orphan_panes: list[dict[str, Any]] = []
    for line in panes_stdout.splitlines():
        if not line.strip():
            continue
        pane = parse_pane_signal_row(line)
        if pane is None:
            parse_errors.append("invalid tmux pane signal row")
            continue
        panes.append(pane)
        window = windows_by_key.get(pane["window_key"])
        if window is None:
            orphan_panes.append(pane)
            continue
        window["panes"].append(pane)

    clients: list[dict[str, Any]] = []
    for line in clients_stdout.splitlines():
        if not line.strip():
            continue
        client = parse_client_signal_row(line)
        if client is None:
            parse_errors.append("invalid tmux client signal row")
            continue
        clients.append(client)

    windows.sort(key=lambda item: (session_sort_key(item["session"]), int_value(item["window_index"])))
    for window in windows:
        window["panes"].sort(key=lambda item: int_value(item["pane_index"]))
        active_details = active_client_details_for_window(window, clients)
        window["active_client_details"] = active_details
        window["authoritative_client"] = authoritative_client_for_window(window, clients)
    sorted_sessions = {
        name: sessions[name]
        for name in sorted(sessions, key=session_sort_key)
    }
    agents = [
        {
            "session": pane["session"],
            "window_index": pane["window_index"],
            "pane_id": pane["pane_id"],
            "target": pane["target"],
            "agent": pane["agent"],
            "current_path": pane["current_path"],
            "alternate_on": pane["alternate_on"],
            "dead": pane["dead"],
        }
        for pane in panes
        if pane.get("agent")
    ]
    return {
        "ok": not parse_errors,
        "generated_at": time.time() if generated_at is None else float(generated_at),
        "compute_ms": round(max(0.0, float(compute_ms)), 1),
        "window_count": len(windows),
        "pane_count": len(panes),
        "client_count": len(clients),
        "agent_count": len(agents),
        "sessions": sorted_sessions,
        "windows": windows,
        "clients": clients,
        "orphan_panes": orphan_panes,
        "agents": agents,
        "errors": parse_errors,
    }


def fetch_tmux_signal_snapshot(timeout: float = 3.0) -> dict[str, Any]:
    started = time.perf_counter()
    errors: list[str] = []
    windows_result = tmux(["list-windows", "-a", "-F", tmux_signal_format(TMUX_WINDOW_SIGNAL_FIELDS)], timeout=timeout)
    if windows_result.returncode != 0:
        errors.append(cmd_error(windows_result, "tmux list-windows failed"))
        windows_stdout = ""
    else:
        windows_stdout = windows_result.stdout
    panes_result = tmux(["list-panes", "-a", "-F", tmux_signal_format(TMUX_PANE_SIGNAL_FIELDS)], timeout=timeout)
    if panes_result.returncode != 0:
        errors.append(cmd_error(panes_result, "tmux list-panes failed"))
        panes_stdout = ""
    else:
        panes_stdout = panes_result.stdout
    clients_result = tmux(["list-clients", "-F", tmux_signal_format(TMUX_CLIENT_SIGNAL_FIELDS)], timeout=timeout)
    if clients_result.returncode != 0:
        errors.append(cmd_error(clients_result, "tmux list-clients failed"))
        clients_stdout = ""
    else:
        clients_stdout = clients_result.stdout
    return parse_tmux_signal_snapshot(
        windows_stdout,
        panes_stdout,
        clients_stdout,
        errors=errors,
        compute_ms=(time.perf_counter() - started) * 1000,
    )

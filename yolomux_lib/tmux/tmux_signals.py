# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Server-wide tmux signal snapshots.

The snapshot is intentionally read-only: it queries tmux formats and pane state
without attaching clients, so it cannot resize user windows.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..common import AGENT_COMMANDS
from ..common import STATE_DIR
from ..infra.process_claims import CLAIM_RESULT_CLAIM_REMOVE_FAILED
from ..infra.process_claims import CLAIM_RESULT_SIGNAL_REFUSED
from ..infra.process_claims import CLAIM_RESULT_SIGNALLED
from ..infra.process_claims import ProcessClaim
from ..infra.process_claims import ProcessClaimError
from ..infra.process_claims import ProcessClaimLedger
from .tmux_utils import TmuxSocketTargetError
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
    "pane-died",
    "pane-exited",
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
TMUX_SIGNAL_HOOK_EVENT_PREFIX = "yolomux-tmux-signal-hook:"
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


TMUX_CONTROL_CLIENT_CLAIM_KIND = "tmux-control-client"

# Sweep outcomes an operator must see.  A retained claim whose supervisor is alive
# and a routine record-only cleanup are normal and stay out of the error surface.
_CLAIM_SWEEP_REPORTABLE_RESULTS = frozenset({
    CLAIM_RESULT_SIGNALLED,
    CLAIM_RESULT_SIGNAL_REFUSED,
    CLAIM_RESULT_CLAIM_REMOVE_FAILED,
})


def tmux_control_client_claims(root: Path | None = None) -> ProcessClaimLedger:
    """Return the one claim ledger that owns control-client reap authority."""

    return ProcessClaimLedger(Path(root) if root is not None else STATE_DIR, TMUX_CONTROL_CLIENT_CLAIM_KIND)


def reap_unsupervised_tmux_control_clients(
    *,
    ledger: ProcessClaimLedger | None = None,
    signal_process: Callable[[int, int], None] = os.kill,
) -> list[dict[str, Any]]:
    """Terminate only control clients whose spawning YOLOmux server is provably gone.

    Linux closes this at the source with PR_SET_PDEATHSIG above; macOS has no
    equivalent, so a hard-killed server leaks its ``tmux -C attach-session``
    client on the shared socket.  The previous sweep decided that from a ``ps``
    scrape keyed on ``PPID == 1`` plus argv substrings, which is exactly the
    authority the lifetime-supervision contract rejects: PPID, PGID, hostname,
    and command text prove nothing about *who* created a process, so an
    unrelated user's read-only monitor matched the same pattern.

    Authority now comes from a claim this server wrote when it spawned the
    client, carrying host, boot, PID, process-start identity, kind, namespace,
    generation, and the spawning supervisor's own identity.  A claim whose
    supervisor is still alive is deliberately retained and names that surviving
    supervisor; everything ambiguous is reported and never signalled.  The
    platform gate is gone with the scrape: a claim is provable on every host, so
    a Linux server that lost PDEATHSIG (libc unavailable) is covered too.
    """

    return (ledger or tmux_control_client_claims()).reap_unsupervised(signal_process=signal_process)


def tmux_signal_subscription_commands() -> list[list[str]]:
    return [["refresh-client", "-B", f"{name}:{fmt}"] for name, fmt in TMUX_SIGNAL_SUBSCRIPTIONS]


def tmux_signal_hook_command(_hook: str) -> str:
    # `display-message -p` writes to whichever tmux client executes a global
    # hook. That can be an interactive browser-backed attach, leaking the
    # internal event token into a visible terminal. Existing control-mode
    # subscriptions observe the refresh without terminal output.
    return "refresh-client"


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
    if text.startswith(TMUX_SIGNAL_HOOK_EVENT_PREFIX):
        return text[len(TMUX_SIGNAL_HOOK_EVENT_PREFIX):].split(":", 1)[0]
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
            tmux_signal_hook_command(hook),
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
        self.lock = threading.Condition(threading.RLock())
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.status_state = "never-started"
        self.status_sessions: list[str] = []
        self.status_error = ""
        self._claim_ledger: ProcessClaimLedger | None = None
        self.claim: ProcessClaim | None = None

    @staticmethod
    def status_details(state: str, error: str = "") -> tuple[bool | None, str, str]:
        """Return the watcher-owned typed state without inferring health from a caller."""

        details = {
            "never-started": (False, "not_started", "Tmux signal watcher has not been started"),
            "attaching": (None, "attaching", "Tmux control client is attaching"),
            "no-sessions": (True, "no_sessions", "No tmux sessions are configured to watch"),
            "attached": (True, "", ""),
            "exited": (False, "control_client_exited", "Tmux control client exited"),
        }
        healthy, reason_code, reason = details.get(state, details["exited"])
        return healthy, reason_code, error or reason

    @classmethod
    def never_started_status(cls) -> dict[str, Any]:
        healthy, reason_code, reason = cls.status_details("never-started")
        return {
            "state": "never-started",
            "healthy": healthy,
            "reason_code": reason_code,
            "reason": reason,
            "sessions": [],
            "thread_alive": False,
            "process_pid": 0,
        }

    def status_payload(self) -> dict[str, Any]:
        with self.lock:
            state = self.status_state
            sessions = list(self.status_sessions)
            error = self.status_error
            thread = self.thread
            process = self.process
        healthy, reason_code, reason = self.status_details(state, error)
        return {
            "state": state,
            "healthy": healthy,
            "reason_code": reason_code,
            "reason": reason,
            "sessions": sessions,
            "thread_alive": thread is not None and thread.is_alive(),
            "process_pid": int(process.pid) if process is not None and process.poll() is None else 0,
        }

    def _set_status(self, state: str, *, sessions: Sequence[str] | None = None, error: str = "") -> None:
        with self.lock:
            self.status_state = state
            if sessions is not None:
                self.status_sessions = [str(session) for session in sessions]
            self.status_error = error
            self.lock.notify_all()

    def wait_for_status(self, state: str, timeout: float) -> bool:
        """Wait on the watcher-owned lifecycle transition rather than a process proxy."""

        with self.lock:
            return self.lock.wait_for(
                lambda: self.status_state == state,
                timeout=max(0.0, float(timeout)),
            )

    def claim_ledger(self) -> ProcessClaimLedger:
        """Resolve the reap-authority ledger once per watcher, lazily.

        Built on first use rather than in ``__init__`` because resolving the host
        identity touches the filesystem, and a watcher is constructed during
        server import where that read must not run.
        """

        with self.lock:
            if self._claim_ledger is None:
                self._claim_ledger = tmux_control_client_claims()
            return self._claim_ledger

    def publish_client_claim(self, pid: int, session: str) -> ProcessClaim | None:
        """Persist reap authority over the client just spawned, or say why it has none.

        A refused claim is not fatal to monitoring: the client still runs and this
        server still owns it through its live handle.  What is lost is the ability
        of a LATER server to reap it after a hard kill, so the refusal is surfaced
        rather than defaulted away.
        """

        try:
            return self.claim_ledger().publish(int(pid), generation=str(session))
        except ProcessClaimError as exc:
            self.emit_error(f"tmux control-client claim refused ({exc.reason_code}): {exc}")
            return None

    def reap_unsupervised_clients(self) -> list[dict[str, Any]]:
        """Supervisor boundary for the claim sweep: never let it stop the watcher."""

        try:
            rows = reap_unsupervised_tmux_control_clients(ledger=self.claim_ledger())
        except (OSError, ProcessClaimError) as exc:
            self.emit_error(f"tmux control-client claim sweep failed: {type(exc).__name__}: {exc}")
            return []
        for row in rows:
            if row.get("result") in _CLAIM_SWEEP_REPORTABLE_RESULTS:
                self.emit_error(
                    "tmux control-client claim "
                    f"{row.get('result')} (pid={row.get('pid')}, reason={row.get('reason')})"
                )
        return rows

    def start(self) -> bool:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.reap_unsupervised_clients()
            self.stop_event.clear()
            self._set_status("attaching")
            self.thread = threading.Thread(target=self.run, name="tmux-signal-events", daemon=True)
            self.thread.start()
            return True

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            process = self.process
            if process is not None:
                self._set_status("exited")
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
                self._set_status("no-sessions", sessions=[])
                self.stop_event.wait(self.retry_seconds)
                continue
            self._set_status("attaching", sessions=sessions)
            for error in install_tmux_signal_monitoring(sessions):
                self.emit_error(error)
            self.run_control_client(sessions[0])
            self.stop_event.wait(self.retry_seconds)

    def run_control_client(self, session: str) -> None:
        # The control client has not existed at either exception boundary below.
        # Preserve that distinction for operators: ``exited`` is only for a
        # client that was spawned and then stopped.
        try:
            command = tmux_control_attach_command(session)
        except TmuxSocketTargetError as exc:
            error = f"tmux control-mode attach refused: {exc}"
            self._set_status("never-started", error=error)
            self.emit_error(error)
            return
        with self.lock:
            if self.stop_event.is_set():
                return
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    errors="replace",
                    bufsize=1,
                    preexec_fn=set_control_client_parent_death_signal,
                )
            except OSError as exc:
                error = f"tmux control-mode start failed: {exc}"
                self._set_status("never-started", error=error)
                self.emit_error(error)
                return
            self.process = process
        claim = None
        try:
            claim = self.publish_client_claim(process.pid, session)
            with self.lock:
                self.claim = claim
                if self.stop_event.is_set():
                    return
                self._set_status("attached")
            install_tmux_signal_control_subscriptions(process)
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
                released_claim = self.claim if claim is not None and self.claim is claim else None
                if released_claim is not None:
                    self.claim = None
            self._set_status("exited")
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
            # Release only after this owner has stopped its own client: the claim is
            # the sole authority a later server has to reap it, so dropping it while
            # the client could still be alive would strand the client permanently.
            if released_claim is not None and not self.claim_ledger().release(released_claim):
                self.emit_error(f"tmux control-client claim release failed: {released_claim.path}")


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


def window_record_key(window: dict[str, Any]) -> str:
    key = str(window.get("key") or "").strip()
    if key:
        return key
    session = str(window.get("session") or window.get("session_name") or "").strip()
    window_index = str(window.get("window_index") if window.get("window_index") is not None else window.get("window") or "").strip()
    return window_key(session, window_index) if session and window_index else ""


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
        # pane_current_command is only a label. OpenCode is deliberately omitted here because
        # process identity is owned by session discovery; an arbitrary pane running a command
        # named ``opencode`` must not become an agent from this signal-only snapshot.
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
            parse_errors.append("invalid tmux sub-window signal row")
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


def fetch_tmux_signal_snapshot(timeout: float = 3.0, session: str = "") -> dict[str, Any]:
    started = time.perf_counter()
    errors: list[str] = []
    target = str(session or "").strip()
    target_args = ["-t", tmux_session_target(target)] if target else []
    windows_args = ["list-windows", *target_args, *([] if target else ["-a"]), "-F", tmux_signal_format(TMUX_WINDOW_SIGNAL_FIELDS)]
    panes_args = ["list-panes", *target_args, *([] if target else ["-a"]), "-F", tmux_signal_format(TMUX_PANE_SIGNAL_FIELDS)]
    clients_args = ["list-clients", *target_args, "-F", tmux_signal_format(TMUX_CLIENT_SIGNAL_FIELDS)]
    windows_result = tmux(windows_args, timeout=timeout)
    if windows_result.returncode != 0:
        errors.append(cmd_error(windows_result, "tmux list-windows failed"))
        windows_stdout = ""
    else:
        windows_stdout = windows_result.stdout
    panes_result = tmux(panes_args, timeout=timeout)
    if panes_result.returncode != 0:
        errors.append(cmd_error(panes_result, "tmux list-panes failed"))
        panes_stdout = ""
    else:
        panes_stdout = panes_result.stdout
    clients_result = tmux(clients_args, timeout=timeout)
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

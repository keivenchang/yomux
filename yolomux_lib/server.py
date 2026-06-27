from __future__ import annotations

import base64
import codecs
import copy
import gzip
import hashlib
import html
import json
import math
import os
import pty
import queue
import re
import select
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlparse

import yaml

from . import filesystem
from . import yolo_rules
from .app import TmuxWebtermApp
from .common import DEFAULT_COLS
from .common import DEFAULT_ROWS
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import PROJECT_ROOT
from .common import WEBSOCKET_GUID
from .common import codex_event_kind
from .common import codex_exec_argv
from .common import codex_runtime_env
from .common import error_payload
from .common import terminate_process_group
from .filesystem import FilesystemError
from .http_routes import dispatch_http_route
from .http_routes import parse_query_float
from .http_routes import parse_query_int
from .http_routes import parse_repo_refs_param
from .http_routes import query_bool
from .http_routes import query_list
from .http_routes import query_one
from .tmux_utils import tmux
from .tmux_utils import tmux_command
from .tmux_utils import tmux_session_client_rows
from .tmux_utils import tmux_session_target
from .transcripts import codex_event_text
from .transcripts import compact_transcript_items
from .transcripts import strip_terminal_query_responses
from .transcripts import transcript_items_from_raw_line
from .uploads import parse_multipart_upload
from .server_auth import AuthMixin
from .settings import SUMMARY_DEFAULT_CODEX_TIMEOUT_SECONDS
from .settings import SUMMARY_DEFAULT_LOOKBACK_SECONDS
from .web import html_page
from .web import static_asset_path
from .web import static_content_type
from .websocket import make_ws_frame
from .websocket import read_ws_frame
from .websocket import set_pty_size
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_status


PTY_DIMENSION_MIN = 1
PTY_DIMENSION_MAX = 1000
WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS = 5.0
RESIZE_AUTHORITY_CLIENT_ID_MAX = 128
SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES = 256 * 1024
SHARE_VIEWER_POINTER_WAKE_FRAME = b""
SHARE_VIEWER_OVERFLOW_LIMIT = 3
SHARE_VIEWER_OVERFLOW_WINDOW_SECONDS = 60.0
SHARE_VIEWER_SEND_TIMEOUT_SECONDS = 5.0
SHARE_REFRESH_CLIENT_MIN_SECONDS = 1.0
TMUX_ATTACH_REFRESH_DELAYS_SECONDS = (0.1, 0.5)
SHARE_POINTER_MAX_WRITES_PER_SECOND = 1500
SHARE_POINTER_MAX_HZ = 30
SHARE_POINTER_CLICK_QUEUE_LIMIT = 32
SHARE_MIRROR_PROTOCOL_VERSION = 1
SHARE_MIRROR_FRAME_UI_STATE = "ui-state"
SHARE_MIRROR_FRAME_LAYOUT = "layout"
SHARE_MIRROR_FRAME_VIEWPORT = "viewport"
SHARE_MIRROR_FRAME_APPEARANCE = "appearance"
SHARE_MIRROR_FRAME_POPUP_LAYER = "popup-layer"
SHARE_MIRROR_FRAME_GEOMETRY_DIGEST = "geometry-digest"
SHARE_MIRROR_FRAME_HOST_RESIZE = "host-resize"
SHARE_MIRROR_FRAME_POINTER = "pointer"
SHARE_MIRROR_FRAME_SCROLL = "scroll"
SHARE_MIRROR_FRAME_FILE_VERSION = "file-version"
SHARE_MIRROR_FRAME_ACTIVE_TAB = "active-tab"
SHARE_MIRROR_FRAME_FOCUS = "focus"
SHARE_MIRROR_FRAME_FINDER_MODE = "finder-mode"
SHARE_MIRROR_FRAME_MENU = "menu"
SHARE_MIRROR_FRAME_SHARE_STATUS = "share-status"
SHARE_MIRROR_FRAME_DOM_KEYFRAME = "dom-keyframe"
SHARE_MIRROR_FRAME_DOM_DELTA = "dom-delta"
SHARE_MIRROR_FRAME_DOM_KEYFRAME_REQUEST = "dom-keyframe-request"
SHARE_MIRROR_FRAME_DOM_KEYFRAME_ACK = "dom-keyframe-ack"
SHARE_MIRROR_FRAME_DOM_REPLAY_ERROR = "dom-replay-error"
SHARE_MIRROR_FRAME_TERMINAL_HOST_RESIZE = "terminal-host-resize"
SHARE_MIRROR_FRAME_TEXT_WRAP_METRICS = "text-wrap-metrics"
SHARE_MIRROR_FRAME_INPUT_INTENT = "input-intent"
SHARE_MIRROR_REPLAY_FRAME_TYPES = frozenset({
    SHARE_MIRROR_FRAME_DOM_KEYFRAME,
    SHARE_MIRROR_FRAME_DOM_DELTA,
    SHARE_MIRROR_FRAME_DOM_KEYFRAME_REQUEST,
    SHARE_MIRROR_FRAME_DOM_KEYFRAME_ACK,
    SHARE_MIRROR_FRAME_DOM_REPLAY_ERROR,
    SHARE_MIRROR_FRAME_TERMINAL_HOST_RESIZE,
})
SHARE_MIRROR_REPLAY_VIEWER_CONTROL_FRAME_TYPES = frozenset({
    SHARE_MIRROR_FRAME_DOM_KEYFRAME_REQUEST,
    SHARE_MIRROR_FRAME_DOM_KEYFRAME_ACK,
    SHARE_MIRROR_FRAME_DOM_REPLAY_ERROR,
})
SHARE_MIRROR_FRAME_TYPES = frozenset({
    SHARE_MIRROR_FRAME_UI_STATE,
    SHARE_MIRROR_FRAME_LAYOUT,
    SHARE_MIRROR_FRAME_VIEWPORT,
    SHARE_MIRROR_FRAME_APPEARANCE,
    SHARE_MIRROR_FRAME_POPUP_LAYER,
    SHARE_MIRROR_FRAME_GEOMETRY_DIGEST,
    SHARE_MIRROR_FRAME_HOST_RESIZE,
    SHARE_MIRROR_FRAME_POINTER,
    SHARE_MIRROR_FRAME_SCROLL,
    SHARE_MIRROR_FRAME_FILE_VERSION,
    SHARE_MIRROR_FRAME_ACTIVE_TAB,
    SHARE_MIRROR_FRAME_FOCUS,
    SHARE_MIRROR_FRAME_FINDER_MODE,
    SHARE_MIRROR_FRAME_MENU,
    SHARE_MIRROR_FRAME_SHARE_STATUS,
    SHARE_MIRROR_FRAME_TEXT_WRAP_METRICS,
    SHARE_MIRROR_FRAME_INPUT_INTENT,
    *SHARE_MIRROR_REPLAY_FRAME_TYPES,
})
SHARE_MIRROR_VIEWER_SEMANTIC_MUTATION_FRAME_TYPES = frozenset({
    SHARE_MIRROR_FRAME_UI_STATE,
    SHARE_MIRROR_FRAME_LAYOUT,
    SHARE_MIRROR_FRAME_VIEWPORT,
    SHARE_MIRROR_FRAME_APPEARANCE,
    SHARE_MIRROR_FRAME_POPUP_LAYER,
    SHARE_MIRROR_FRAME_GEOMETRY_DIGEST,
    SHARE_MIRROR_FRAME_HOST_RESIZE,
    SHARE_MIRROR_FRAME_SCROLL,
    SHARE_MIRROR_FRAME_FILE_VERSION,
    SHARE_MIRROR_FRAME_ACTIVE_TAB,
    SHARE_MIRROR_FRAME_FOCUS,
    SHARE_MIRROR_FRAME_FINDER_MODE,
    SHARE_MIRROR_FRAME_MENU,
    SHARE_MIRROR_FRAME_TEXT_WRAP_METRICS,
})
SHARE_INPUT_INTENT_TERMINAL_INPUT = "terminal-input"
SHARE_INPUT_INTENT_TERMINAL_PASTE = "terminal-paste"
SHARE_INPUT_INTENT_TERMINAL_SCROLL = "terminal-scroll"
SHARE_INPUT_INTENT_TAB_ACTIVATE = "tab-activate"
SHARE_INPUT_INTENT_MENU_COMMAND = "menu-command"
SHARE_INPUT_INTENT_HOST_COMMAND = "host-command"
SHARE_INPUT_INTENT_TYPES = frozenset({
    SHARE_INPUT_INTENT_TERMINAL_INPUT,
    SHARE_INPUT_INTENT_TERMINAL_PASTE,
    SHARE_INPUT_INTENT_TERMINAL_SCROLL,
    SHARE_INPUT_INTENT_TAB_ACTIVATE,
    SHARE_INPUT_INTENT_MENU_COMMAND,
    SHARE_INPUT_INTENT_HOST_COMMAND,
})
SHARE_INPUT_INTENT_TERMINAL_TYPES = frozenset({
    SHARE_INPUT_INTENT_TERMINAL_INPUT,
    SHARE_INPUT_INTENT_TERMINAL_PASTE,
    SHARE_INPUT_INTENT_TERMINAL_SCROLL,
})
SHARE_INPUT_INTENT_SCROLL_DIRECTIONS = frozenset({"up", "down"})
SHARE_INPUT_INTENT_MENU_COMMANDS = frozenset({"tab-pin-toggle", "tab-close", "terminal-copy", "terminal-paste", "open-command-palette"})
SHARE_INPUT_INTENT_HOST_COMMANDS = frozenset({"request-keyframe", "focus-terminal", "fit-contain", "fit-cover", "fit-toggle"})
SHARE_INPUT_INTENT_MAX_TEXT_LENGTH = 65536
SHARE_INPUT_INTENT_MAX_TARGET_LENGTH = 512
SHARE_INPUT_INTENT_MAX_LINES = 1000
SHARE_MIRROR_KEYFRAME_REASONS = frozenset({"join", "gap", "digest", "replay-error", "backpressure", "topology", "manual-debug"})
SHARE_MIRROR_SEQUENCE_FIELDS = ("epoch", "sequence", "baseSequence")
SHARE_MIRROR_RELAY_NUMBER_FIELDS = ("version", *SHARE_MIRROR_SEQUENCE_FIELDS)
SHARE_MIRROR_REDACTION_POLICY_VERSION = 1
SHARE_MIRROR_REDACTION_METADATA_FIELDS = ("policyVersion", "removedCount")
SHARE_TERMINAL_PLACEHOLDER_FIELDS = ("placeholderId", "session", "rows", "cols", "terminalEpoch")
SHARE_REPLAY_DELTA_RING_LIMIT = 128
SHARE_REPLAY_SERVER_SENDER = "__server__"
SHARE_MIRROR_DEBUG_NAMES = {
    SHARE_MIRROR_FRAME_DOM_KEYFRAME: "DOM keyframe",
    SHARE_MIRROR_FRAME_DOM_DELTA: "DOM delta",
    SHARE_MIRROR_FRAME_DOM_KEYFRAME_REQUEST: "DOM keyframe request",
    SHARE_MIRROR_FRAME_DOM_REPLAY_ERROR: "DOM replay error",
    SHARE_MIRROR_FRAME_TERMINAL_HOST_RESIZE: "terminal host resize",
}
SHARE_INPUT_INTENT_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
MAX_FS_BATCH_REQUESTS = 64
TOKEN_LOG_RE = re.compile(r"([?&]token=)[^&\s\"]+")
SHARE_URL_SECRET_RE = re.compile(r"(?:https?://[^\"'\s<>]+)?/share/[A-Za-z0-9_-]+(?:#[^\"'\s<>]*)?")
STATIC_CACHE_CONTROL_VERSIONED = "public, max-age=31536000, immutable"
STATIC_CACHE_CONTROL_UNVERSIONED = "no-store"
STATIC_GZIP_MIN_BYTES = 1024
STATIC_GZIP_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "text/css",
    "text/html",
    "text/plain",
}


def content_disposition_attachment(raw_path: str) -> str:
    name = Path(str(raw_path or "")).name or "download"
    safe = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";", "/"} else "_" for char in name).strip()
    return f'attachment; filename="{safe or "download"}"'


def content_type_base(content_type: str) -> str:
    return str(content_type or "").split(";", 1)[0].strip().lower()


def static_content_type_supports_gzip(content_type: str) -> bool:
    base = content_type_base(content_type)
    return base in STATIC_GZIP_CONTENT_TYPES or base.startswith("text/")


def accept_encoding_allows_gzip(accept_encoding: str | None) -> bool:
    gzip_q: float | None = None
    wildcard_q: float | None = None
    for raw_part in str(accept_encoding or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        token, *raw_params = part.split(";")
        encoding = token.strip().lower()
        if encoding not in {"gzip", "*"}:
            continue
        q = 1.0
        for raw_param in raw_params:
            name, separator, value = raw_param.strip().partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 0.0
        if encoding == "gzip":
            gzip_q = q
        else:
            wildcard_q = q
    if gzip_q is not None:
        return gzip_q > 0
    return bool(wildcard_q is not None and wildcard_q > 0)


def static_asset_cache_control(request_path: str) -> str:
    qs = parse_qs(urlparse(request_path or "").query)
    if any(str(value).strip() for value in qs.get("v", [])):
        return STATIC_CACHE_CONTROL_VERSIONED
    return STATIC_CACHE_CONTROL_UNVERSIONED


def static_asset_response_body(data: bytes, content_type: str, accept_encoding: str | None) -> tuple[bytes, str | None]:
    if (
        len(data) >= STATIC_GZIP_MIN_BYTES
        and static_content_type_supports_gzip(content_type)
        and accept_encoding_allows_gzip(accept_encoding)
    ):
        return gzip.compress(data, compresslevel=6, mtime=0), "gzip"
    return data, None


def clamp_pty_dimension(value: int) -> int:
    return max(PTY_DIMENSION_MIN, min(value, PTY_DIMENSION_MAX))


def ws_resize_dimensions(message: dict[str, Any], default_rows: int, default_cols: int) -> tuple[int, int] | None:
    cols = message.get("cols")
    rows = message.get("rows")
    if not isinstance(cols, int) or isinstance(cols, bool) or not isinstance(rows, int) or isinstance(rows, bool):
        return None
    return clamp_pty_dimension(rows), clamp_pty_dimension(cols)


def clean_resize_authority_client_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", text)[:RESIZE_AUTHORITY_CLIENT_ID_MAX]


def tmux_attach_command(readonly: bool = False) -> list[str]:
    args = tmux_command(["attach-session"])
    if readonly:
        args.append("-r")
    args.extend(["-f", "ignore-size"])
    return args


def resize_pty_and_signal_process(fd: int, process: subprocess.Popen[Any] | None, rows: int, cols: int) -> None:
    set_pty_size(fd, rows, cols)
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGWINCH)
        except OSError:
            pass


def tmux_client_name_for_fd(fd: int) -> str:
    try:
        return os.ttyname(fd)
    except OSError:
        return ""


def tmux_client_has_flag(row: dict[str, Any], flag: str) -> bool:
    return flag in {item.strip() for item in str(row.get("flags") or "").split(",") if item.strip()}


def refresh_tmux_client_ignore_size(client_name: str, ignore_size: bool) -> bool:
    if not client_name:
        return False
    result = tmux(["refresh-client", "-t", client_name, "-f", "ignore-size" if ignore_size else "!ignore-size"])
    return result.returncode == 0


def refresh_tmux_session_clients(session: str) -> bool:
    clean_session = str(session or "").strip()
    if not clean_session:
        return False
    result = tmux(["refresh-client", "-t", tmux_session_target(clean_session)])
    return result.returncode == 0


def refresh_tmux_session_clients_after_attach(session: str) -> bool:
    clean_session = str(session or "").strip()
    if not clean_session:
        return False
    refreshed = refresh_tmux_session_clients(clean_session)
    for delay in TMUX_ATTACH_REFRESH_DELAYS_SECONDS:
        timer = threading.Timer(float(delay), refresh_tmux_session_clients, args=(clean_session,))
        timer.daemon = True
        timer.start()
    return refreshed


def claim_tmux_resize_authority(session: str, client_name: str, active_cols: int | None = None) -> bool:
    """Make `client_name` the column authority for `session`: silence every WIDER client.

    Called when a browser surface activates a pane. Under `window-size largest` the shared window
    is as wide as the widest non-`ignore-size` client, so a wider sibling (a second browser surface
    OR a hand-attached terminal) makes the focused surface's content overflow its viewport. Flag
    those wider clients `ignore-size` so they stop voting and the window collapses to the active
    width; `active_cols` is the width the surface is asking for, preferred over its last-reported
    width since the pty resize for this same message lands just after this call.

    Width-only by design (per the column-overflow symptom): clients at or below the active width
    don't inflate it and are left untouched, so the blast radius is exactly the clients that would
    break the active surface. Idempotent -- when nothing is wider and the active client already
    counts, it issues no tmux calls (the "current width already == active width" fast path).
    """
    clean_client_name = str(client_name or "").strip()
    if not clean_client_name:
        return False
    rows = tmux_session_client_rows(session)
    current = next((row for row in rows if str(row.get("name") or "") == clean_client_name), None)
    if current is None:
        # The active client is not listed yet (just attached); best-effort make it count.
        return refresh_tmux_client_ignore_size(clean_client_name, False)
    width = active_cols if isinstance(active_cols, int) and active_cols > 0 else int(current.get("width") or 0)
    active_ignored = tmux_client_has_flag(current, "ignore-size")
    wider = [
        row for row in rows
        if str(row.get("name") or "") != clean_client_name
        and not tmux_client_has_flag(row, "ignore-size")
        and int(row.get("width") or 0) > width
    ]
    if not active_ignored and not wider:
        return False
    changed = False
    if active_ignored:
        changed = refresh_tmux_client_ignore_size(clean_client_name, False) or changed
    for row in wider:
        changed = refresh_tmux_client_ignore_size(str(row.get("name") or ""), True) or changed
    return changed


def configure_session_tmux_options(session: str) -> None:
    """Set the shared tmux options every YOLOmux attach needs, idempotent and best-effort.

    Runs before each attach so it self-heals across tmux restarts. All three are no-ops when
    only one client views the session, and only change behavior in the multi-client case:

    - set-clipboard on: tmux's default `external` IGNORES application OSC 52, so Claude's copy
      would never reach the browser; `on` forwards it to this client.
    - window-size largest + aggressive-resize on: YOLOmux spawns one `attach-session` client per
      WebSocket, so two browser surfaces on one session attach as differently-sized clients. The
      attach itself starts with `ignore-size`; activating a browser surface clears that flag for
      its own client and sets it on every WIDER client on the session — a second browser surface OR
      a hand-attached terminal — so they stop voting on the column width. Keeping `largest` avoids
      the old tmux `latest` status-line smear while active-surface authority keeps a wider client
      from stretching the focused surface.
    """
    target = tmux_session_target(session)
    for args in (
        ["set-option", "-s", "set-clipboard", "on"],
        ["set-option", "-t", target, "window-size", "largest"],
        ["set-option", "-wg", "aggressive-resize", "on"],
    ):
        tmux(args)


def share_terminal_frame(session: str, data: bytes) -> bytes:
    payload = {
        "ch": "term",
        "pane": session,
        "data": base64.b64encode(data).decode("ascii"),
    }
    return make_ws_frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"), opcode=1)


def redact_share_ui_value(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: redact_share_ui_value(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_share_ui_value(item, key) for item in value]
    if isinstance(value, str):
        if key.lower() in {"token", "sharetoken", "share_token"}:
            return "..."
        return SHARE_URL_SECRET_RE.sub("...", value)
    return value


def share_ui_frame(message: dict[str, Any]) -> bytes:
    clean_message = redact_share_ui_value(message)
    return make_ws_frame(json.dumps({"ch": "ui", **clean_message}, separators=(",", ":")).encode("utf-8"), opcode=1)


def share_frame_is_dom_keyframe(frame: bytes | None) -> bool:
    return bool(frame and b'"type":"dom-keyframe"' in frame)


def share_frame_is_latest_pointer(frame: bytes | None) -> bool:
    if not frame or b'"click":true' in frame:
        return False
    return b'"type":"pointer"' in frame


def share_mirror_frame_type_allowed(frame_type: str) -> bool:
    return str(frame_type or "") in SHARE_MIRROR_FRAME_TYPES


def share_replay_frame_type_allowed(frame_type: str) -> bool:
    return str(frame_type or "") in SHARE_MIRROR_REPLAY_FRAME_TYPES


def share_replay_viewer_control_frame_allowed(frame_type: str) -> bool:
    return str(frame_type or "") in SHARE_MIRROR_REPLAY_VIEWER_CONTROL_FRAME_TYPES


def share_viewer_semantic_mutation_frame_disallowed(frame_type: str) -> bool:
    return str(frame_type or "") in SHARE_MIRROR_VIEWER_SEMANTIC_MUTATION_FRAME_TYPES


def share_input_intent_type_allowed(intent: str) -> bool:
    return str(intent or "") in SHARE_INPUT_INTENT_TYPES


def share_input_intent_session_allowed(session: str, share_sessions: list[str]) -> bool:
    clean_session = str(session or "").strip()
    return bool(clean_session and clean_session in {str(item or "").strip() for item in share_sessions})


def share_input_intent_clean_text(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.replace("\x00", "")
    if not text:
        return ""
    return text[:max_length]


def share_input_intent_clean_target(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > SHARE_INPUT_INTENT_MAX_TARGET_LENGTH or "\r" in text or "\n" in text:
        return ""
    return text


def share_input_intent_clean_command(value: Any, allowed: frozenset[str]) -> str:
    if not isinstance(value, str):
        return ""
    command = value.strip()
    if command not in allowed:
        return ""
    if not SHARE_INPUT_INTENT_COMMAND_RE.match(command):
        return ""
    return command


def normalize_share_input_intent_payload(payload: dict[str, Any], share_sessions: list[str]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    intent = str(payload.get("intent") or payload.get("action") or "").strip()
    if not share_input_intent_type_allowed(intent):
        return None
    session = str(payload.get("session") or "").strip()
    if intent in SHARE_INPUT_INTENT_TERMINAL_TYPES and not share_input_intent_session_allowed(session, share_sessions):
        return None
    if intent in {SHARE_INPUT_INTENT_TERMINAL_INPUT, SHARE_INPUT_INTENT_TERMINAL_PASTE}:
        data = share_input_intent_clean_text(payload.get("data"), SHARE_INPUT_INTENT_MAX_TEXT_LENGTH)
        if not data:
            return None
        return {"intent": intent, "session": session, "data": data}
    if intent == SHARE_INPUT_INTENT_TERMINAL_SCROLL:
        direction = str(payload.get("direction") or "").strip()
        lines = payload.get("lines")
        if direction not in SHARE_INPUT_INTENT_SCROLL_DIRECTIONS or not isinstance(lines, int) or isinstance(lines, bool):
            return None
        bounded_lines = max(1, min(int(lines), SHARE_INPUT_INTENT_MAX_LINES))
        return {"intent": intent, "session": session, "direction": direction, "lines": bounded_lines}
    if intent == SHARE_INPUT_INTENT_TAB_ACTIVATE:
        item = share_input_intent_clean_target(payload.get("item", payload.get("target")))
        if not item:
            return None
        if session and not share_input_intent_session_allowed(session, share_sessions):
            return None
        result = {"intent": intent, "item": item}
        if session:
            result["session"] = session
        return result
    if intent == SHARE_INPUT_INTENT_MENU_COMMAND:
        command = share_input_intent_clean_command(payload.get("command"), SHARE_INPUT_INTENT_MENU_COMMANDS)
        if not command:
            return None
        target = share_input_intent_clean_target(payload.get("target", ""))
        result = {"intent": intent, "command": command}
        if target:
            result["target"] = target
        if session:
            if not share_input_intent_session_allowed(session, share_sessions):
                return None
            result["session"] = session
        return result
    if intent == SHARE_INPUT_INTENT_HOST_COMMAND:
        command = share_input_intent_clean_command(payload.get("command"), SHARE_INPUT_INTENT_HOST_COMMANDS)
        if not command:
            return None
        result = {"intent": intent, "command": command}
        if session:
            if not share_input_intent_session_allowed(session, share_sessions):
                return None
            result["session"] = session
        return result
    return None


def share_replay_frame_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return int(value)


class ShareViewerConnection:
    def __init__(self, connection: socket.socket, client_id: str = ""):
        self.connection = connection
        self.client_id = str(client_id or "")
        self.frames: queue.Queue[bytes | None] = queue.Queue()
        self.lock = threading.Lock()
        self.queued_bytes = 0
        self.closed = False
        self.overflow_times: list[float] = []
        self.close_reason = ""
        self.queued_replay_keyframe = False
        self.latest_pointer_frame: bytes | None = None
        self.pointer_wakeup_queued = False

    def clear_frames_locked(self) -> None:
        while True:
            try:
                frame = self.frames.get_nowait()
            except queue.Empty:
                break
            if frame is not None:
                self.queued_bytes = max(0, self.queued_bytes - len(frame))
        self.queued_replay_keyframe = False
        self.pointer_wakeup_queued = False

    def queue_latest_pointer_frame_locked(self, frame: bytes) -> None:
        self.latest_pointer_frame = frame
        if not self.pointer_wakeup_queued:
            self.pointer_wakeup_queued = True
            self.frames.put(SHARE_VIEWER_POINTER_WAKE_FRAME)

    def pop_latest_pointer_frame(self) -> bytes | None:
        with self.lock:
            frame = self.latest_pointer_frame
            self.latest_pointer_frame = None
            return frame

    def mark_pointer_wakeup_drained(self) -> None:
        with self.lock:
            self.pointer_wakeup_queued = False

    def enqueue(self, frame: bytes) -> str:
        with self.lock:
            if self.closed:
                return "closed"
            if share_frame_is_latest_pointer(frame):
                self.queue_latest_pointer_frame_locked(frame)
                return "queued"
            if self.queued_bytes + len(frame) > SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES:
                if self.queued_replay_keyframe:
                    return "overflow"
                self.clear_frames_locked()
                now = time.monotonic()
                cutoff = now - SHARE_VIEWER_OVERFLOW_WINDOW_SECONDS
                self.overflow_times = [moment for moment in self.overflow_times if moment >= cutoff] + [now]
                if len(self.overflow_times) >= SHARE_VIEWER_OVERFLOW_LIMIT:
                    self.closed = True
                    self.close_reason = "too-slow"
                    self.frames.put(None)
                    return "too-slow"
                return "overflow"
            self.queued_bytes += len(frame)
            self.frames.put(frame)
            if share_frame_is_dom_keyframe(frame):
                self.queued_replay_keyframe = True
            return "queued"

    def enqueue_reset_frame(self, frame: bytes) -> str:
        with self.lock:
            if self.closed:
                return "closed"
            self.clear_frames_locked()
            self.overflow_times = []
            self.queued_bytes += len(frame)
            self.frames.put(frame)
            if share_frame_is_dom_keyframe(frame):
                self.queued_replay_keyframe = True
            return "queued"

    def close(self, reason: str = "") -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if reason:
                self.close_reason = reason
            self.frames.put(None)

    def is_closed(self) -> bool:
        with self.lock:
            return self.closed

    def send_frame(self, frame: bytes) -> None:
        previous_timeout = None
        timeout_supported = hasattr(self.connection, "gettimeout") and hasattr(self.connection, "settimeout")
        if timeout_supported:
            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(SHARE_VIEWER_SEND_TIMEOUT_SECONDS)
        try:
            self.connection.sendall(frame)
        finally:
            if timeout_supported:
                self.connection.settimeout(previous_timeout)

    def write_loop(self) -> None:
        try:
            while True:
                frame = self.frames.get()
                if frame is None:
                    break
                if frame == SHARE_VIEWER_POINTER_WAKE_FRAME:
                    self.mark_pointer_wakeup_drained()
                    pointer_frame = self.pop_latest_pointer_frame()
                    if pointer_frame is not None:
                        self.send_frame(pointer_frame)
                    continue
                pointer_frame = self.pop_latest_pointer_frame() if share_frame_is_dom_keyframe(frame) else None
                if pointer_frame is not None:
                    self.send_frame(pointer_frame)
                with self.lock:
                    self.queued_bytes = max(0, self.queued_bytes - len(frame))
                    if share_frame_is_dom_keyframe(frame):
                        self.queued_replay_keyframe = False
                self.send_frame(frame)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
            self.close("writer-closed")


class ShareTerminalUpstream:
    def __init__(self, server: "TmuxWebtermHTTPServer", token: str, session: str):
        self.server = server
        self.token = token
        self.session = session
        self.lock = threading.Lock()
        self.viewers: set[ShareViewerConnection] = set()
        self.stop_event = threading.Event()
        self.master_fd: int | None = None
        self.slave_fd: int | None = None
        self.process: subprocess.Popen[Any] | None = None
        self.reader_thread: threading.Thread | None = None
        self.last_refresh_at = 0.0

    def add_viewer(self, viewer: ShareViewerConnection) -> None:
        refresh_existing_upstream = False
        with self.lock:
            self.viewers.add(viewer)
            if self.reader_thread is None:
                self.start_locked()
            else:
                refresh_existing_upstream = True
        if refresh_existing_upstream:
            self.request_refresh_client()

    def remove_viewer(self, viewer: ShareViewerConnection) -> None:
        with self.lock:
            self.viewers.discard(viewer)

    def has_viewers(self) -> bool:
        with self.lock:
            return bool(self.viewers)

    def start_locked(self) -> None:
        rows, cols = self.server.host_pty_dimensions_for_session(self.session)
        master_fd, slave_fd = pty.openpty()
        process: subprocess.Popen[Any] | None = None
        try:
            set_pty_size(slave_fd, rows, cols)
            self.master_fd = master_fd
            self.slave_fd = slave_fd
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            configure_session_tmux_options(self.session)
            record = self.server.app.verify_share_token(self.token)
            readonly = str((record or {}).get("mode") or "ro") != "rw"
            attach_args = tmux_attach_command(readonly=readonly)
            attach_args.extend(["-t", tmux_session_target(self.session)])
            process = subprocess.Popen(
                attach_args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=env,
                start_new_session=True,
            )
            refresh_tmux_session_clients_after_attach(self.session)
            self.process = process
            self.reader_thread = threading.Thread(target=self.reader_loop, name=f"share-terminal-{self.session}", daemon=True)
            self.reader_thread.start()
        except (OSError, subprocess.SubprocessError):
            self.master_fd = None
            self.slave_fd = None
            self.process = None
            for fd in (master_fd, slave_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass
            if process is not None and process.poll() is None:
                terminate_process_group(process)
            raise

    def write_input(self, payload: bytes) -> bool:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if message.get("type") != "input":
            return False
        data = message.get("data")
        if not isinstance(data, str):
            return False
        filtered = strip_terminal_query_responses(data)
        if not filtered:
            return False
        with self.lock:
            master_fd = self.master_fd
            if master_fd is None:
                return False
            os.write(master_fd, filtered.encode("utf-8"))
        self.server.app.record_user_input(self.session, len(filtered), source="share", data=filtered)
        return True

    def update_dimensions(self, rows: int, cols: int, *, refresh: bool = True) -> None:
        refresh_needed = False
        with self.lock:
            slave_fd = self.slave_fd
            process = self.process
            if slave_fd is None:
                return
            resize_pty_and_signal_process(slave_fd, process, rows, cols)
            refresh_needed = True
        if refresh_needed and refresh:
            self.request_refresh_client()

    def reader_loop(self) -> None:
        reader_fd: int | None = None
        try:
            with self.lock:
                master_fd = self.master_fd
                process = self.process
                if master_fd is None or process is None:
                    return
                reader_fd = os.dup(master_fd)
            while not self.stop_event.is_set():
                with self.lock:
                    process = self.process
                if process is None:
                    break
                if process.poll() is not None:
                    break
                readable, _, _ = select.select([reader_fd], [], [], 0.1)
                if reader_fd not in readable:
                    continue
                if self.stop_event.is_set():
                    break
                data = os.read(reader_fd, 65536)
                if not data:
                    break
                self.broadcast(data)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
            if reader_fd is not None:
                try:
                    os.close(reader_fd)
                except OSError:
                    pass
            self.stop()

    def broadcast(self, data: bytes) -> None:
        frame = share_terminal_frame(self.session, data)
        with self.lock:
            viewers = list(self.viewers)
        for viewer in viewers:
            status = viewer.enqueue(frame)
            if status == "overflow":
                self.request_refresh_client()
            elif status == "too-slow":
                self.request_refresh_client()
                viewer.close("too-slow")

    def request_refresh_client(self) -> None:
        now = time.monotonic()
        if now - self.last_refresh_at < SHARE_REFRESH_CLIENT_MIN_SECONDS:
            return
        self.last_refresh_at = now
        tmux(["refresh-client", "-t", tmux_session_target(self.session)])

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            master_fd = self.master_fd
            slave_fd = self.slave_fd
            process = self.process
            self.master_fd = None
            self.slave_fd = None
            self.process = None
            viewers = list(self.viewers)
            self.viewers.clear()
        for viewer in viewers:
            viewer.close("upstream-closed")
        for fd in (master_fd, slave_fd):
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
        if process is not None and process.poll() is None:
            terminate_process_group(process)


class Handler(AuthMixin, BaseHTTPRequestHandler):
    server: "TmuxWebtermHTTPServer"
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        preparer = getattr(self.server, "prepare_request_socket", None)
        if callable(preparer):
            self.request = preparer(self.request)
        self._request_is_https = isinstance(self.request, ssl.SSLSocket)
        super().setup()

    def log_message(self, fmt: str, *args: Any) -> None:
        message = TOKEN_LOG_RE.sub(r"\1[redacted]", fmt % args)
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), message))

    def plaintext_share_scope_allowed(self, parsed: Any) -> bool:
        if not getattr(self.server, "tls_context", None) or self.request_is_https():
            return True
        path = str(parsed.path or "")
        def http_share_token_allowed() -> bool:
            verifier = getattr(self.server.app, "verify_share_token", None)
            record = verifier(self.share_token_text()) if callable(verifier) else None
            return bool(record and record.get("http_allowed"))

        if path.startswith("/share/"):
            short_id = path.removeprefix("/share/").strip("/")
            finder = getattr(self.server.app, "share_record_for_short_id", None)
            record = finder(short_id) if short_id and "/" not in short_id and callable(finder) else None
            return bool(record and record.get("http_allowed"))
        if path.startswith("/static/"):
            checker = getattr(self.server.app, "http_allowed_share_is_active", None)
            return bool(checker()) if callable(checker) else False
        share_api_paths = {
            "/",
            "/ws/share-ui",
            "/ws/share-view",
            "/api/ping",
            "/api/share",
            "/api/share-stream",
            *self.SHARE_READONLY_GET_PATHS,
            *self.SHARE_SCOPED_FILE_GET_PATHS,
            *self.SHARE_READONLY_POST_PATHS,
        }
        if path in share_api_paths:
            return http_share_token_allowed()
        return False

    def redirect_plaintext_to_https_if_needed(self, parsed: Any) -> bool:
        if not getattr(self.server, "tls_context", None) or self.request_is_https() or self.plaintext_share_scope_allowed(parsed):
            return False
        host = str(self.headers.get("Host") or self.server.server_name_with_port()).strip()
        if not host or "\r" in host or "\n" in host:
            host = self.server.server_name_with_port()
        location = f"https://{host}{self.path or '/'}"
        body = f"Use HTTPS for this YOLOmux server: {location}\n".encode("utf-8")
        self.send_response(HTTPStatus.PERMANENT_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True
        return True

    def do_GET(self) -> None:
        dispatch_http_route(self, "GET")

    def share_bootstrap_payload(self, record: dict[str, Any] | None) -> dict[str, Any] | None:
        if not record:
            return None
        mode = str(record.get("mode") or "ro")
        if not self.request_is_https():
            mode = "ro"
        max_viewers = int(record.get("max_viewers") or 0)
        viewers = int(record.get("viewers") or 0)
        sessions = self.share_record_sessions_for_handler(record)
        session = sessions[0] if sessions else str(record.get("session") or "")
        host_dims = {
            current: {
                "rows": self.server.host_pty_dimensions_for_session(current)[0],
                "cols": self.server.host_pty_dimensions_for_session(current)[1],
            }
            for current in sessions
        }
        rows, cols = self.server.host_pty_dimensions_for_session(session)
        ui_state = record.get("ui_state") if isinstance(record.get("ui_state"), dict) else {}
        return {
            "view": True,
            "id": str(record.get("short_id") or ""),
            "session": session,
            "sessions": sessions,
            "mode": mode,
            "readOnly": mode != "rw",
            "scheme": "https" if self.request_is_https() else "http",
            "expiresAt": float(record.get("expires_at") or 0.0),
            "createdBy": str(record.get("created_by") or ""),
            "maxViewers": max_viewers,
            "viewers": viewers,
            "debugProfile": bool(record.get("debug_profile")),
            "hostDims": {"rows": rows, "cols": cols},
            "hostDimsBySession": host_dims,
            "layout": str(record.get("layout") or ""),
            "tabs": str(record.get("tabs") or ""),
            "finder": record.get("finder") if isinstance(record.get("finder"), dict) else {},
            "viewport": ui_state.get("viewport") if isinstance(ui_state.get("viewport"), dict) else {},
            "appearance": ui_state.get("appearance") if isinstance(ui_state.get("appearance"), dict) else {},
            "uiState": ui_state,
            "tokenInFragment": True,
        }

    def share_record_at_viewer_cap(self, record: dict[str, Any]) -> bool:
        max_viewers = int(record.get("max_viewers") or 0)
        viewers = int(record.get("viewers") or 0)
        return max_viewers > 0 and viewers >= max_viewers

    def share_record_sessions_for_handler(self, record: dict[str, Any]) -> list[str]:
        session_reader = getattr(self.server.app, "share_record_sessions", None)
        if callable(session_reader):
            raw_sessions = session_reader(record)
        elif isinstance(record.get("sessions"), list):
            raw_sessions = record.get("sessions")
        else:
            raw_sessions = [record.get("session")]
        result: list[str] = []
        for raw_session in raw_sessions or []:
            session = str(raw_session or "").strip()
            if session and session not in result:
                result.append(session)
        return result

    def normalize_share_input_intent_for_handler(self, token: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        verifier = getattr(self.server.app, "verify_share_token", None)
        record = verifier(str(token or "")) if callable(verifier) else None
        if not isinstance(record, dict):
            return None
        if str(record.get("mode") or "ro") != "rw" or not self.request_is_https():
            return None
        return normalize_share_input_intent_payload(payload, self.share_record_sessions_for_handler(record))

    def apply_share_input_intent_for_handler(self, token: str, payload: dict[str, Any]) -> bool:
        intent = str(payload.get("intent") or "")
        if intent in {SHARE_INPUT_INTENT_TERMINAL_INPUT, SHARE_INPUT_INTENT_TERMINAL_PASTE}:
            upstream_getter = getattr(self.server, "share_terminal_upstream", None)
            if not callable(upstream_getter):
                return False
            upstream = upstream_getter(str(token or ""), str(payload.get("session") or ""))
            data = json.dumps({"type": "input", "data": str(payload.get("data") or "")}).encode("utf-8")
            return bool(upstream.write_input(data))
        if intent == SHARE_INPUT_INTENT_TERMINAL_SCROLL:
            scroller = getattr(self.server.app, "tmux_scroll", None)
            if not callable(scroller):
                return False
            scroller(str(payload.get("session") or ""), str(payload.get("direction") or ""), int(payload.get("lines") or 0))
            return True
        return False

    def handle_share_shell(self, parsed: Any) -> bool:
        short_id = parsed.path.removeprefix("/share/").strip("/")
        if not short_id or "/" in short_id:
            return False
        finder = getattr(self.server.app, "share_record_for_short_id", None)
        record = finder(short_id) if callable(finder) else None
        if not record:
            return False
        if self.share_record_at_viewer_cap(record):
            self.write_text("share viewer limit reached\n", status=HTTPStatus.FORBIDDEN)
            return True
        sessions = self.share_record_sessions_for_handler(record)
        if not sessions:
            return False
        self.write_html(html_page(
            sessions,
            "readonly",
            dev=getattr(self.server, 'dev', False),
            dangerously_yolo=self.server.app.dangerously_yolo,
            share=self.share_bootstrap_payload(record),
        ))
        return True

    def handle_fs_list(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "/") or "/")
        self.write_filesystem_json(raw_path, lambda: filesystem.list_directory(raw_path))

    def handle_fs_search(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_root = str(query_one(qs, "root", query_one(qs, "path", "/")) or "/")
        query = str(query_one(qs, "query", "") or "")
        limit = str(query_one(qs, "limit", "400") or "400")
        recursive = query_bool(qs, "recursive")
        self.write_filesystem_json(raw_root, lambda: filesystem.search_files(raw_root, query, limit, recursive=recursive))

    def handle_fs_index_status(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_root = str(query_one(qs, "root", query_one(qs, "path", "/")) or "/")
        self.write_filesystem_json(raw_root, lambda: filesystem.index_status(raw_root))

    def handle_fs_read(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        self.write_filesystem_json(raw_path, lambda: filesystem.read_file(raw_path))

    def handle_fs_info(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        self.write_filesystem_json(raw_path, lambda: filesystem.path_info(raw_path))

    def handle_fs_diff(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        from_ref = query_one(qs, "from", None)
        to_ref = query_one(qs, "to", None)
        self.write_filesystem_json(raw_path, lambda: filesystem.diff_file(raw_path, from_ref=from_ref, to_ref=to_ref))

    def handle_blame(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        ref = query_one(qs, "ref", None)
        self.write_filesystem_json(raw_path, lambda: filesystem.blame_file(raw_path, ref=ref))

    def write_filesystem_json(self, raw_path: str, build_payload: Any) -> None:
        try:
            payload = build_payload()
        except FilesystemError as exc:
            self.write_json(error_payload(str(exc), path=raw_path, status=exc.status), status=HTTPStatus(exc.status))
            return
        self.write_json(payload)

    def handle_fs_raw(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        download = query_bool(qs, "download")
        try:
            data, mime = filesystem.read_raw(raw_path)
        except FilesystemError as exc:
            self.write_json(error_payload(str(exc), path=raw_path, status=exc.status), status=HTTPStatus(exc.status))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition", content_disposition_attachment(raw_path))
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def handle_fs_html_preview(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        if not raw_path.lower().endswith((".html", ".htm")):
            self.write_json(error_payload("path must be an HTML file", path=raw_path, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = filesystem.read_file(raw_path)
        except FilesystemError as exc:
            self.write_json(error_payload(str(exc), path=raw_path, status=exc.status), status=HTTPStatus(exc.status))
            return
        source = html.escape(str(payload.get("content", "")), quote=True)
        title = html.escape(Path(raw_path).name or "HTML preview")
        body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    html, body, iframe {{ width: 100%; height: 100%; margin: 0; border: 0; background: #fff; }}
    iframe {{ display: block; }}
  </style>
</head>
<body>
  <iframe title="{title}" sandbox="allow-scripts allow-forms allow-popups" srcdoc="{source}"></iframe>
</body>
</html>"""
        self.write_html(body)

    def handle_preview_popout_placeholder(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        title = html.escape(f"{Path(raw_path).name or 'Preview'} preview")
        body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
</head>
<body></body>
</html>"""
        self.write_html(body)

    def read_request_body(
        self,
        max_length: int,
        *,
        allow_empty: bool = False,
        allow_missing: bool = False,
        missing_message: str = "missing Content-Length",
        invalid_message: str = "invalid Content-Length",
        empty_message: str = "invalid Content-Length",
        too_large_message: str = "content too large",
        missing_status: HTTPStatus = HTTPStatus.LENGTH_REQUIRED,
        invalid_status: HTTPStatus = HTTPStatus.BAD_REQUEST,
        empty_status: HTTPStatus = HTTPStatus.BAD_REQUEST,
        too_large_status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        close_on_too_large: bool = True,
    ) -> tuple[bytes | None, dict[str, Any] | None, HTTPStatus]:
        length_text = self.headers.get("Content-Length", "")
        if not length_text and allow_missing:
            return b"", None, HTTPStatus.OK
        try:
            length = int(length_text)
        except (TypeError, ValueError):
            return None, error_payload(missing_message if not length_text else invalid_message, status=missing_status if not length_text else invalid_status), missing_status if not length_text else invalid_status
        if length < 0 or (length == 0 and not allow_empty):
            return None, error_payload(empty_message, status=empty_status), empty_status
        if length > max_length:
            if close_on_too_large:
                self.close_connection = True
            return None, error_payload(too_large_message, status=too_large_status), too_large_status
        return self.rfile.read(length), None, HTTPStatus.OK

    def read_json_body(self, max_length: int, *, allow_empty: bool = False, allow_missing: bool = False) -> dict[str, Any] | None:
        body, error, status = Handler.read_request_body(
            self,
            max_length,
            allow_empty=allow_empty,
            allow_missing=allow_missing,
            missing_message="missing or invalid Content-Length",
            invalid_message="missing or invalid Content-Length",
            empty_message="content too large",
            missing_status=HTTPStatus.LENGTH_REQUIRED,
            invalid_status=HTTPStatus.LENGTH_REQUIRED,
            empty_status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            too_large_message="content too large",
        )
        if error is not None:
            self.write_json(error, status=status)
            return None
        if body == b"" and (allow_empty or allow_missing):
            return {}
        try:
            text = (body or b"").decode("utf-8")
        except UnicodeDecodeError:
            self.write_json(error_payload("request body must be utf-8 JSON", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self.write_json(error_payload(f"invalid JSON: {exc}", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(payload, dict):
            self.write_json(error_payload("request body must be a JSON object", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return None
        return payload

    def handle_fs_write(self, parsed: Any) -> None:
        payload = self.read_json_body(filesystem.MAX_WRITE_BYTES + 4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        content = payload.get("content", "")
        expected_mtime = payload.get("expected_mtime")
        if expected_mtime is not None:
            try:
                expected_mtime = int(expected_mtime)
            except (TypeError, ValueError):
                self.write_json(error_payload("expected_mtime must be an integer", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
                return
        if yolo_rules.is_rules_file_path(raw_path):
            try:
                yolo_rules.validate_rule_file_text(str(content), path=yolo_rules.active_rule_path())
            except (ValueError, yaml.YAMLError) as exc:
                self.write_json(error_payload(f"YOLO rules invalid: {exc}", path=raw_path, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
                return
            self.write_filesystem_json(
                raw_path,
                lambda: {
                    **filesystem.write_file(raw_path, content, expected_mtime=expected_mtime),
                    "yolo_rules": yolo_rules.reload_rules(),
                },
            )
            return
        self.write_filesystem_json(raw_path, lambda: filesystem.write_file(raw_path, content, expected_mtime=expected_mtime))

    def handle_fs_delete(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        self.write_filesystem_json(raw_path, lambda: filesystem.delete_path(raw_path))

    def handle_fs_unindex(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", payload.get("root", ""))
        self.write_filesystem_json(raw_path, lambda: filesystem.unindex_root(raw_path))

    def handle_fs_rename(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        new_name = payload.get("new_name", "")
        self.write_filesystem_json(raw_path, lambda: filesystem.rename_path(raw_path, new_name))

    def handle_fs_mkdir(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        self.write_filesystem_json(raw_path, lambda: filesystem.create_directory(raw_path))

    def do_POST(self) -> None:
        dispatch_http_route(self, "POST")

    def request_base_url(self, scheme: str | None = None) -> str:
        host = str(self.headers.get("Host") or self.server.server_name_with_port()).strip()
        if not host or "\r" in host or "\n" in host:
            host = self.server.server_name_with_port()
        scheme_text = str(scheme or "").strip().lower()
        url_scheme = scheme_text if scheme_text in {"http", "https"} else "https" if self.request_is_https() else "http"
        return f"{url_scheme}://{host}"

    def share_scoped_activity_result(self, result: tuple[dict[str, Any], HTTPStatus]) -> tuple[dict[str, Any], HTTPStatus]:
        allowed = set(self.share_sessions())
        if not allowed:
            return result
        payload, status = result
        activity = payload.get("activity") if isinstance(payload, dict) else None
        if not isinstance(activity, dict):
            return result
        scoped = dict(payload)
        scoped["activity"] = {
            key: value
            for key, value in activity.items()
            if str(key).split(":", 1)[0] in allowed
        }
        return scoped, status

    def share_scoped_session_metadata_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = set(self.share_sessions())
        if not allowed or not isinstance(payload, dict):
            return payload
        scoped = dict(payload)
        sessions = payload.get("sessions")
        if isinstance(sessions, dict):
            scoped["sessions"] = {session: info for session, info in sessions.items() if session in allowed}
        order = payload.get("session_order")
        if isinstance(order, list):
            scoped["session_order"] = [session for session in order if session in allowed]
        return scoped

    def share_scoped_transcripts_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.share_scoped_session_metadata_payload(payload)

    def handle_share_create(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        session = str(payload.get("session") or "").strip()
        sessions = payload.get("sessions", None)
        ttl_seconds = payload.get("ttl_seconds", payload.get("ttl", None))
        layout = str(payload.get("layout") or "")
        tabs = str(payload.get("tabs") or "")
        finder = payload.get("finder", None)
        ui_state = payload.get("ui_state", payload.get("uiState", None))
        scheme = payload.get("scheme", payload.get("protocol", None))
        return self.server.app.create_share_token(
            session,
            ttl_seconds,
            base_url=self.request_base_url(),
            created_by=self.auth_identity().username,
            layout=layout,
            tabs=tabs,
            finder=finder,
            ui_state=ui_state,
            sessions=sessions,
            mode=payload.get("mode", None),
            read_only=payload.get("read_only", payload.get("readonly", None)),
            scheme=scheme,
            max_viewers=payload.get("max_viewers", None),
            debug_profile=payload.get("debug_profile", payload.get("debugProfile", False)),
            request_is_https=self.request_is_https(),
            tls_available=self.server.tls_context is not None,
        )

    def handle_fs_batch(self, parsed: Any) -> None:
        started = time.perf_counter()
        payload = self.read_json_body(64 * 1024)
        if payload is None:
            return
        requests = payload.get("requests", [])
        if not isinstance(requests, list):
            self.write_json(error_payload("requests must be a list", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        if len(requests) > MAX_FS_BATCH_REQUESTS:
            self.write_json(error_payload(f"requests must contain at most {MAX_FS_BATCH_REQUESTS} items", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        responses = []
        op_counts: dict[str, int] = {}
        path_samples: list[str] = []
        for index, item in enumerate(requests):
            request_id = item.get("id", index) if isinstance(item, dict) else index
            if not isinstance(item, dict):
                responses.append({"id": request_id, "ok": False, "status": 400, "error": "request must be an object"})
                continue
            op = str(item.get("type", item.get("op", "")) or "")
            raw_path = str(item.get("path", "") or "")
            op_counts[op] = op_counts.get(op, 0) + 1
            if raw_path and len(path_samples) < 8:
                path_samples.append(raw_path)
            if op not in {"list", "info"}:
                responses.append({"id": request_id, "ok": False, "status": 400, "error": "unsupported fs batch operation", "path": raw_path})
                continue
            try:
                result = filesystem.list_directory(raw_path) if op == "list" else filesystem.path_info(raw_path)
            except FilesystemError as exc:
                responses.append({"id": request_id, "ok": False, "status": exc.status, "error": str(exc), "path": raw_path})
                continue
            responses.append({"id": request_id, "ok": True, "status": 200, "payload": result})
        response_payload = {"responses": responses}
        recorder = getattr(getattr(getattr(self, "server", None), "app", None), "record_performance_sample", None)
        if callable(recorder):
            recorder(
                "fs-batch",
                "api",
                trigger="POST /api/fs/batch",
                compute_ms=(time.perf_counter() - started) * 1000,
                payload=response_payload,
                cache_key={"kind": "fs-batch"},
                cache_status="computed",
                owner_role="client",
                count=len(requests),
                details={"ops": json.dumps(op_counts, sort_keys=True), "paths": json.dumps(path_samples)},
            )
        self.write_json(response_payload)

    def read_urlencoded_form(self) -> dict[str, list[str]]:
        body, error, _status = Handler.read_request_body(self, 16 * 1024, allow_empty=True, allow_missing=True)
        if error is not None:
            self.close_connection = True
            return {}
        return parse_qs((body or b"").decode("utf-8", errors="replace"), keep_blank_values=True)

    def handle_client_event(self) -> tuple[dict[str, Any], HTTPStatus]:
        body, error, status = Handler.read_request_body(self, 64 * 1024, too_large_message="event is too large")
        if error is not None:
            return error, status
        try:
            event = json.loads((body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return error_payload(f"invalid JSON: {exc}", status=HTTPStatus.BAD_REQUEST), HTTPStatus.BAD_REQUEST
        if not isinstance(event, dict):
            return error_payload("event must be an object", status=HTTPStatus.BAD_REQUEST), HTTPStatus.BAD_REQUEST
        return self.server.app.client_event(event)

    def handle_upload(self, session: str, *, editor_path: str = "", base_dir: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        upload_max_bytes = self.server.app.upload_max_bytes()
        body, error, status = Handler.read_request_body(self, upload_max_bytes, too_large_message=f"upload is too large; limit is {upload_max_bytes} bytes")
        if error is not None:
            return {"session": session, "error": str(error.get("error") or "")}, status
        try:
            files = parse_multipart_upload(self.headers.get("Content-Type", ""), body or b"", max_part_bytes=upload_max_bytes)
        except ValueError as exc:
            return {"session": session, "error": str(exc)}, HTTPStatus.BAD_REQUEST
        if editor_path or base_dir:
            return self.server.app.upload_editor_files(files, editor_path=editor_path, base_dir=base_dir)
        return self.server.app.upload_files(session, files)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if self.redirect_plaintext_to_https_if_needed(parsed):
            return
        if parsed.path.startswith("/static/"):
            asset = parsed.path.removeprefix("/static/")
            content_type = static_content_type(asset)
            if content_type:
                self.write_static_head(asset, content_type)
                return
        if not self.require_auth():
            return
        if parsed.path == "/":
            data = html_page(self.server.app.sessions, self.auth_identity().role, dev=getattr(self.server, 'dev', False), dangerously_yolo=self.server.app.dangerously_yolo).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_auth_cookie_if_needed()
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def dev_bundle_signature(self) -> str:
        # mtime_ns of the served bundle + css; changes the instant static_build.py rewrites them.
        from .web import static_asset_path

        parts = []
        for asset in ("yolomux.js", "yolomux.css"):
            path = static_asset_path(asset)
            try:
                parts.append(str(path.stat().st_mtime_ns) if path else "0")
            except OSError:
                parts.append("0")
        return ".".join(parts)

    def stream_dev_reload(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_auth_cookie_if_needed()
        self.end_headers()
        last = self.dev_bundle_signature()
        try:
            self.write_sse_json("ready", {"signature": last})
            while True:
                time.sleep(0.5)
                current = self.dev_bundle_signature()
                if current != last:
                    last = current
                    self.write_sse_json("reload", {"signature": current})
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            return

    def stream_client_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_auth_cookie_if_needed()
        self.end_headers()
        subscriber_id, subscriber_queue = self.server.app.client_events.subscribe()
        if hasattr(self.server.app, "start_client_event_watcher"):
            self.server.app.start_client_event_watcher()
        try:
            self.write_sse_json("ready", {"time": time.time()})
            while True:
                try:
                    event = subscriber_queue.get(timeout=15.0)
                except queue.Empty:
                    self.write_sse_json("ping", {"time": time.time()})
                    continue
                self.write_sse_json(str(event.get("type") or "event"), event)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            return
        finally:
            self.server.app.client_events.unsubscribe(subscriber_id)
            if hasattr(self.server.app, "stop_client_event_watcher_if_idle"):
                self.server.app.stop_client_event_watcher_if_idle()

    def stream_context_items(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = str(query_one(qs, "session", "") or "")
        messages, error = parse_query_int(qs, "messages", 40, max_value=MAX_COMPACT_TRANSCRIPT_ITEMS)
        if error:
            self.write_json(error_payload(error, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        message_limit = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        payload, status = self.server.app.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            self.write_json(payload, status=status)
            return
        path_text = payload.get("path")
        text = payload.get("text")
        if not isinstance(path_text, str) or not isinstance(text, str):
            self.write_json(error_payload("missing transcript text", session=session, status=HTTPStatus.NOT_FOUND), status=HTTPStatus.NOT_FOUND)
            return

        path = Path(path_text)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_auth_cookie_if_needed()
        self.end_headers()

        try:
            self.write_sse_json(
                "reset",
                {
                    "session": session,
                    "path": str(path),
                    "items": compact_transcript_items(text, message_limit),
                    "agent": payload.get("agent"),
                    "errors": payload.get("errors", []),
                },
            )
            self.follow_transcript_file(path)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            return

    def stream_codex_summary(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = str(query_one(qs, "session", "") or "")
        summary_settings = self.server.app.summary_settings()
        default_lookback = int(summary_settings.get("lookback_seconds") or SUMMARY_DEFAULT_LOOKBACK_SECONDS)
        lookback_seconds, error = parse_query_int(qs, "lookback", default_lookback, max_value=24 * 3600)
        if error:
            self.write_json(error_payload(error, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        availability_error = self.codex_summary_availability_error(summary_settings)
        if availability_error:
            self.write_json(availability_error, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        payload, status = self.server.app.codex_summary_prompt(session, lookback_seconds)
        if status != HTTPStatus.OK:
            self.write_json(payload, status=status)
            return
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            self.write_json({"session": session, "error": "missing Codex prompt"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_auth_cookie_if_needed()
        self.end_headers()

        meta = {key: value for key, value in payload.items() if key != "prompt"}
        meta["summary_model"] = summary_settings["codex_model"]
        meta["summary_effort"] = summary_settings["codex_effort"]
        meta["summary_service_tier"] = summary_settings["codex_service_tier"]
        self.server.app.log_event(
            session,
            "summary_started",
            "AI summary started",
            {"lookback_seconds": lookback_seconds, "model": summary_settings["codex_model"]},
        )
        try:
            self.write_sse_json("meta", meta)
            self.run_codex_summary(prompt, summary_settings)
            self.server.app.log_event(session, "summary_finished", "AI summary finished", {"model": summary_settings["codex_model"]})
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            self.server.app.log_event(session, "summary_disconnected", "AI summary stream disconnected", {})
            return

    def codex_summary_availability_error(self, summary_settings: dict[str, Any]) -> dict[str, Any] | None:
        provider = str(summary_settings.get("backend") or "").strip().lower()
        if provider != "codex":
            return {
                "error": "AI summary provider is disabled",
                "provider": provider or "disabled",
            }
        status = agent_auth_status()
        codex_status = status.get("codex") if isinstance(status, dict) else {}
        codex_status = codex_status if isinstance(codex_status, dict) else {}
        if not codex_status.get("installed"):
            return {
                "error": "Codex summary provider is unavailable because the codex CLI is not on PATH",
                "provider": "codex",
                "login_command": AGENT_LOGIN_COMMANDS["codex"],
            }
        if not codex_status.get("logged_in"):
            return {
                "error": f"Codex summary provider is unavailable because the codex CLI is not logged in. Run `{AGENT_LOGIN_COMMANDS['codex']}`.",
                "provider": "codex",
                "login_command": AGENT_LOGIN_COMMANDS["codex"],
            }
        return None

    def run_codex_summary(self, prompt: str, summary_settings: dict[str, Any]) -> None:
        repo_root = PROJECT_ROOT
        args = codex_exec_argv(
            ephemeral=True,
            model=str(summary_settings.get("codex_model") or "").strip() or None,
            effort=str(summary_settings.get("codex_effort") or "").strip() or None,
            service_tier=str(summary_settings.get("codex_service_tier") or "").strip() or None,
        )
        env = codex_runtime_env()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                args,
                cwd=str(repo_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None:
                self.write_sse_json("summary_error", {"error": "failed to open Codex pipes"})
                return
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
            self.stream_codex_process(process, timeout_seconds=summary_settings.get("timeout_seconds"))
        except OSError as exc:
            self.write_sse_json("summary_error", {"error": str(exc)})
        finally:
            if process is not None:
                terminate_process_group(process)

    def stream_codex_process(self, process: subprocess.Popen[bytes], timeout_seconds: Any = SUMMARY_DEFAULT_CODEX_TIMEOUT_SECONDS) -> None:
        if process.stdout is None:
            self.write_sse_json("summary_error", {"error": "missing Codex stdout"})
            return
        fd = process.stdout.fileno()
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        last_ping = time.monotonic()
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            timeout = float(SUMMARY_DEFAULT_CODEX_TIMEOUT_SECONDS)
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            now = time.monotonic()
            if now > deadline:
                self.write_sse_json("summary_error", {"error": "Codex summary timed out"})
                return
            running = process.poll() is None
            timeout = 0.2 if running else 0.0
            readable, _, _ = select.select([fd], [], [], timeout)
            if readable:
                chunk = os.read(fd, 4096)
                if chunk:
                    buffer += decoder.decode(chunk)
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        self.write_codex_summary_line(line)
                    continue
                if not running:
                    break
            if running:
                if now - last_ping >= 5:
                    self.write_sse_json("ping", {"time": time.strftime("%Y-%m-%d %H:%M:%S %Z")})
                    last_ping = now
                continue
            if not readable:
                break

        buffer += decoder.decode(b"", final=True)
        if buffer.strip():
            self.write_codex_summary_line(buffer)
        return_code = process.wait(timeout=1.0)
        self.write_sse_json("done", {"return_code": return_code})

    def write_codex_summary_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            self.write_sse_json("log", {"text": stripped})
            return
        event_kind = codex_event_kind(event)
        if event_kind == "log":
            self.write_sse_json("log", {"text": str(event.get("type") or "").replace(".", " ")})
            return
        if event_kind == "completed":
            return
        if event_kind == "error":
            self.write_sse_json("summary_error", {"error": json.dumps(event, ensure_ascii=False)})
            return

        text = codex_event_text(event)
        if text:
            self.write_sse_json("delta", {"text": text})

    def follow_transcript_file(self, path: Path) -> None:
        last_ping = time.monotonic()
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    items = transcript_items_from_raw_line(line)
                    if items:
                        self.write_sse_json("items", {"items": items})
                    continue
                now = time.monotonic()
                if now - last_ping >= 15:
                    self.write_sse_json("ping", {"time": time.strftime("%Y-%m-%d %H:%M:%S %Z")})
                    last_ping = now
                time.sleep(0.2)

    def write_sse_json(self, event: str, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False)
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        for line in data.splitlines() or [""]:
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def write_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def write_redirect(self, location: str, status: HTTPStatus = HTTPStatus.SEE_OTHER, clear_auth: bool = False) -> None:
        self.send_response(status)
        self.send_header("Location", self.safe_next_path(location))
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if clear_auth:
            for header in self.clear_auth_cookie_headers():
                self.send_header("Set-Cookie", header)
            self.send_header("Set-Cookie", self.logout_marker_cookie_header())
        else:
            self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def write_static_asset(self, asset: str, content_type: str) -> None:
        path = static_asset_path(asset)
        if path is None:
            self.write_text(f"missing static asset: {asset}\n", status=HTTPStatus.NOT_FOUND)
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.write_text(f"failed to read static asset: {exc}\n", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        body, content_encoding = static_asset_response_body(data, content_type, self.headers.get("Accept-Encoding"))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", static_asset_cache_control(self.path))
        if static_content_type_supports_gzip(content_type):
            self.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def write_static_head(self, asset: str, content_type: str) -> None:
        path = static_asset_path(asset)
        if path is None:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.write_text(f"failed to read static asset: {exc}\n", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        body, content_encoding = static_asset_response_body(data, content_type, self.headers.get("Accept-Encoding"))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", static_asset_cache_control(self.path))
        if static_content_type_supports_gzip(content_type):
            self.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_auth_cookie_if_needed()
        self.end_headers()

    def write_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def write_app_result(self, result: tuple[Any, HTTPStatus]) -> None:
        # Every app method returns a (payload, HTTPStatus) pair; the unpack-then-write_json dance was
        # written ~19 times verbatim. This is the one place that convention is spelled out.
        payload, status = result
        self.write_json(payload, status=status)

    def write_validated_int_result(self, qs: dict, name: str, default: int, max_value: int, make_result) -> None:
        # The "?<name>=<int>" routes (tmux/transcript/context/context-items/events/search) all parsed +
        # range-checked one int the same way, emitting an identical 400 on a bad value before calling the
        # app. Centralized so the bad-int response stays uniform; make_result(value) -> (payload, status).
        value, error = parse_query_int(qs, name, default, max_value=max_value)
        if error:
            self.write_json(error_payload(error, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        self.write_app_result(make_result(value))

    def write_validated_float_result(self, qs: dict, name: str, default: float, max_value: float, make_result) -> None:
        # The activity/session-files routes share one bounded float query parameter. Keep the bad-float
        # response and cap in one path so the three handlers cannot drift.
        value, error = parse_query_float(qs, name, default, max_value=max_value)
        if error:
            self.write_json(error_payload(error, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        self.write_app_result(make_result(value))

    def write_int_query_app_result(self, parsed: Any, name: str, default: int, max_value: int, make_result) -> None:
        # Own parse_qs + int validation for GET routes whose only validation is one bounded integer.
        qs = parse_qs(parsed.query)
        self.write_validated_int_result(qs, name, default, max_value, lambda value: make_result(qs, value))

    def write_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def websocket(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = qs.get("session", [""])[0]
        resize_client_id = clean_resize_authority_client_id(qs.get("client", [""])[0])
        share_sessions = self.share_sessions()
        if share_sessions and session not in share_sessions:
            self.write_text("share token is scoped to a different session\n", status=HTTPStatus.FORBIDDEN)
            return
        if session not in self.server.app.sessions:
            self.write_text(f"unknown session: {session}\n", status=HTTPStatus.NOT_FOUND)
            return
        if not self.accept_websocket():
            return
        self.bridge_tmux(session, readonly=self.auth_readonly(), resize_client_id=resize_client_id)

    def accept_websocket(self) -> bool:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.write_text("missing Sec-WebSocket-Key\n", status=HTTPStatus.BAD_REQUEST)
            return False
        try:
            accept_source = (key + WEBSOCKET_GUID).encode("ascii")
        except UnicodeEncodeError:
            self.write_text("invalid Sec-WebSocket-Key\n", status=HTTPStatus.BAD_REQUEST)
            return False
        accept = base64.b64encode(hashlib.sha1(accept_source).digest()).decode("ascii")
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.send_auth_cookie_if_needed()
        self.end_headers()
        return True

    def websocket_share_view(self, parsed: Any) -> None:
        token = self.share_token()
        qs = parse_qs(parsed.query)
        requested_session = qs.get("session", [""])[0]
        viewer_id = qs.get("viewer", [""])[0]
        if not self.headers.get("Sec-WebSocket-Key"):
            self.write_text("missing Sec-WebSocket-Key\n", status=HTTPStatus.BAD_REQUEST)
            return
        register = getattr(self.server.app, "register_share_viewer", None)
        if not callable(register):
            self.write_text("share transport unavailable\n", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        client_ip = self.client_address[0] if isinstance(self.client_address, tuple) and self.client_address else ""
        record, status = register(token, requested_session, viewer_id, client_ip, self.headers.get("User-Agent", ""))
        if status != HTTPStatus.OK:
            self.write_json(record, status=status)
            return
        share_sessions = self.share_record_sessions_for_handler(record)
        session = requested_session if requested_session in share_sessions else share_sessions[0] if share_sessions else self.share_session()
        if not session or session not in self.server.app.sessions:
            self.server.app.unregister_share_viewer(token, viewer_id)
            self.write_text(f"unknown session: {session}\n", status=HTTPStatus.NOT_FOUND)
            return
        if not self.accept_websocket():
            self.server.app.unregister_share_viewer(token, viewer_id)
            return
        self.server.broadcast_share_status(token)
        self.bridge_shared_tmux(token, session, viewer_id)

    def bridge_tmux(self, session: str, readonly: bool = False, resize_client_id: str = "") -> None:
        initial_rows, initial_cols, saw_initial_resize, pending_payloads = self.read_initial_ws_payloads()
        if not saw_initial_resize:
            initial_rows, initial_cols = self.server.host_pty_dimensions_for_session(session)
        if not readonly and saw_initial_resize:
            self.server.record_host_pty_dimensions(session, initial_rows, initial_cols)
        target = tmux_session_target(session)
        resize_state = {"rows": initial_rows, "cols": initial_cols}
        tmux_client_name = ""
        master_fd: int | None = None
        slave_fd: int | None = None
        process: subprocess.Popen[Any] | None = None

        def session_exists() -> bool:
            return tmux(["has-session", "-t", target]).returncode == 0

        def attach_tmux() -> subprocess.Popen:
            if slave_fd is None:
                raise OSError("tmux attach pty is closed")
            attached = subprocess.Popen(
                attach_args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=env,
                start_new_session=True,
            )
            refresh_tmux_session_clients_after_attach(session)
            return attached

        try:
            master_fd, slave_fd = pty.openpty()
            set_pty_size(slave_fd, initial_rows, initial_cols)
            tmux_client_name = tmux_client_name_for_fd(slave_fd)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            configure_session_tmux_options(session)
            attach_args = tmux_attach_command(readonly=readonly)
            attach_args.extend(["-t", target])
            process = attach_tmux()
            if not readonly and saw_initial_resize:
                self.server.claim_resize_authority(session, tmux_client_name, resize_client_id)
            for payload in pending_payloads:
                self.handle_ws_payload(
                    session,
                    master_fd,
                    slave_fd,
                    process,
                    payload,
                    readonly=readonly,
                    resize_state=resize_state,
                    tmux_client_name=tmux_client_name,
                    resize_client_id=resize_client_id,
                )
            connected = True
            while connected:
                while process.poll() is None:
                    readable, _, _ = select.select([master_fd, self.connection], [], [], 0.1)
                    if master_fd in readable:
                        data = os.read(master_fd, 65536)
                        if not data:
                            break
                        self.connection.sendall(make_ws_frame(data, opcode=2))
                    if self.connection in readable:
                        opcode, payload = self.read_ws_frame_with_timeout()
                        if opcode == 8:
                            connected = False
                            break
                        if opcode == 9:
                            self.connection.sendall(make_ws_frame(payload, opcode=10))
                            continue
                        if opcode not in {1, 2}:
                            continue
                        self.handle_ws_payload(
                            session,
                            master_fd,
                            slave_fd,
                            process,
                            payload,
                            readonly=readonly,
                            resize_state=resize_state,
                            tmux_client_name=tmux_client_name,
                            resize_client_id=resize_client_id,
                        )
                if not connected:
                    break
                returncode = process.poll()
                if returncode == 0 and session_exists():
                    process = attach_tmux()
                    continue
                break
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
            for fd in (master_fd, slave_fd):
                if fd is None:
                    continue
                try:
                    os.close(fd)
                except OSError:
                    pass
            if process is not None and process.poll() is None:
                terminate_process_group(process)

    def bridge_shared_tmux(self, token: str, session: str, viewer_id: str = "") -> None:
        viewer = ShareViewerConnection(self.connection, viewer_id)
        upstream = self.server.share_terminal_upstream(token, session)
        writer: threading.Thread | None = None
        try:
            upstream.add_viewer(viewer)
            writer = threading.Thread(target=viewer.write_loop, name=f"share-viewer-{session}", daemon=True)
            writer.start()
            while not viewer.is_closed():
                record = self.server.app.verify_share_token(token)
                if record is None:
                    break
                readable, _, _ = select.select([self.connection], [], [], 0.5)
                if self.connection not in readable:
                    continue
                opcode, payload = self.read_ws_frame_with_timeout()
                if opcode == 8:
                    break
                if opcode == 9:
                    viewer.enqueue(make_ws_frame(payload, opcode=10))
                    continue
                if opcode in {1, 2}:
                    current_record = self.server.app.verify_share_token(token)
                    if current_record is None:
                        break
                    if self.request_is_https() and str(current_record.get("mode") or "ro") == "rw":
                        upstream.write_input(payload)
                    continue
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
            viewer.close("viewer-closed")
            self.server.release_share_terminal_upstream(token, session, viewer)
            self.server.app.unregister_share_viewer(token, viewer_id)
            self.server.broadcast_share_status(token)
            if writer is not None:
                writer.join(timeout=1.0)

    def websocket_share_host(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        token = qs.get("share", [""])[0].strip()
        client_id = qs.get("client", qs.get("viewer", [""]))[0].strip()
        if not token or self.server.app.verify_share_token(token) is None:
            self.write_text("unknown active share\n", status=HTTPStatus.NOT_FOUND)
            return
        if not self.accept_websocket():
            return
        self.bridge_share_ui_socket(token, client_id)

    def websocket_share_ui(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        token = self.share_token() or qs.get("token", [""])[0].strip()
        client_id = qs.get("client", qs.get("viewer", [""]))[0].strip()
        viewer_id = qs.get("viewer", [client_id])[0].strip() or client_id
        if not self.headers.get("Sec-WebSocket-Key"):
            self.write_text("missing Sec-WebSocket-Key\n", status=HTTPStatus.BAD_REQUEST)
            return
        record = self.server.app.verify_share_token(token)
        if record is None:
            self.write_text("unknown active share\n", status=HTTPStatus.NOT_FOUND)
            return
        registered_viewer_id = ""
        register = getattr(self.server.app, "register_share_viewer", None)
        if callable(register):
            client_ip = self.client_address[0] if isinstance(self.client_address, tuple) and self.client_address else ""
            registered, status = register(token, "", viewer_id, client_ip, self.headers.get("User-Agent", ""))
            if status != HTTPStatus.OK:
                self.write_json(registered, status=status)
                return
            registered_viewer_id = viewer_id
        if not self.accept_websocket():
            if registered_viewer_id:
                self.server.app.unregister_share_viewer(token, registered_viewer_id)
            return
        self.server.broadcast_share_status(token)
        write_enabled = self.request_is_https() and str(record.get("mode") or "ro") == "rw"
        self.bridge_share_ui_socket(token, client_id, receive_only=not write_enabled, viewer_id=registered_viewer_id, accept_semantic_state=False)

    def handle_share_ui_message(self, token: str, message: dict[str, Any], client_id: str = "", *, accept_semantic_state: bool = True) -> None:
        if not isinstance(message, dict):
            return
        msg_type = str(message.get("type") or "")
        if not msg_type:
            return
        if not share_mirror_frame_type_allowed(msg_type):
            return
        if not accept_semantic_state and share_viewer_semantic_mutation_frame_disallowed(msg_type):
            return
        data = message.get("payload")
        payload = data if isinstance(data, dict) else {}
        if msg_type == SHARE_MIRROR_FRAME_INPUT_INTENT:
            normalized_intent = self.normalize_share_input_intent_for_handler(token, payload)
            if normalized_intent is None:
                return
            payload = normalized_intent
            self.apply_share_input_intent_for_handler(token, payload)
        sender = str(message.get("sender") or client_id or "")
        if msg_type == SHARE_MIRROR_FRAME_POINTER:
            self.server.queue_share_pointer(token, payload, sender=sender)
            return
        updater = getattr(self.server.app, "update_share_record_ui_state", None)
        if callable(updater):
            if msg_type == SHARE_MIRROR_FRAME_LAYOUT:
                updater(token, payload)
            elif msg_type == SHARE_MIRROR_FRAME_UI_STATE:
                updater(token, {
                    "uiState": payload,
                    "finder": payload.get("finder", {}),
                    "layout": payload.get("layout", ""),
                    "tabs": payload.get("tabs", ""),
                })
            elif msg_type in {SHARE_MIRROR_FRAME_VIEWPORT, SHARE_MIRROR_FRAME_APPEARANCE}:
                updater(token, {"uiStatePatch": {msg_type: payload}})
            elif msg_type == SHARE_MIRROR_FRAME_SCROLL:
                updater(token, {"uiStateScroll": payload})
        relay_message = {"type": msg_type, "payload": payload, "sender": sender}
        for key in SHARE_MIRROR_RELAY_NUMBER_FIELDS:
            value = message.get(key)
            if isinstance(value, (int, float)):
                relay_message[key] = int(value)
        reason = message.get("reason")
        if isinstance(reason, str) and reason.strip():
            relay_message["reason"] = reason.strip()[:120]
        if msg_type == SHARE_MIRROR_FRAME_DOM_KEYFRAME:
            self.server.record_share_replay_keyframe(token, relay_message)
        elif msg_type == SHARE_MIRROR_FRAME_DOM_DELTA:
            self.server.record_share_replay_delta(token, relay_message)
        self.server.broadcast_share_ui(token, relay_message, skip_client_id=sender)

    def bridge_share_ui_socket(self, token: str, client_id: str = "", receive_only: bool = False, viewer_id: str = "", accept_semantic_state: bool = True) -> None:
        clean_client_id = str(client_id or "")
        client = ShareViewerConnection(self.connection, clean_client_id)
        self.server.register_share_ui_client(token, client)
        if viewer_id:
            self.server.enqueue_share_replay_frames_for_viewer(token, client)
        writer = threading.Thread(target=client.write_loop, name="share-ui", daemon=True)
        writer.start()
        try:
            while self.server.app.verify_share_token(token) is not None:
                readable, _, _ = select.select([self.connection], [], [], 0.5)
                if self.connection not in readable:
                    continue
                opcode, payload = self.read_ws_frame_with_timeout()
                if opcode == 8:
                    break
                if opcode == 9:
                    client.enqueue(make_ws_frame(payload, opcode=10))
                    continue
                if opcode not in {1, 2}:
                    continue
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if receive_only and not share_replay_viewer_control_frame_allowed(str(message.get("type") or "")):
                    continue
                self.handle_share_ui_message(token, message, clean_client_id, accept_semantic_state=accept_semantic_state)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
            client.close("ui-closed")
            self.server.release_share_ui_client(token, client)
            if viewer_id:
                self.server.app.unregister_share_viewer(token, viewer_id)
                self.server.broadcast_share_status(token)
            writer.join(timeout=1.0)

    def read_initial_ws_payloads(self) -> tuple[int, int, bool, list[bytes]]:
        rows = DEFAULT_ROWS
        cols = DEFAULT_COLS
        saw_resize = False
        pending_payloads: list[bytes] = []
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            timeout = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([self.connection], [], [], timeout)
            if self.connection not in readable:
                break
            opcode, payload = self.read_ws_frame_with_timeout()
            if opcode == 8:
                raise ConnectionError("websocket closed")
            if opcode == 9:
                self.connection.sendall(make_ws_frame(payload, opcode=10))
                continue
            if opcode not in {1, 2}:
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pending_payloads.append(payload)
                continue
            if message.get("type") == "resize":
                if message.get("foreground") is False:
                    continue
                dimensions = ws_resize_dimensions(message, rows, cols)
                if dimensions:
                    rows, cols = dimensions
                    saw_resize = True
                continue
            pending_payloads.append(payload)
            break
        return rows, cols, saw_resize, pending_payloads

    def read_ws_frame_with_timeout(self) -> tuple[int, bytes]:
        previous_timeout = self.connection.gettimeout()
        self.connection.settimeout(WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS)
        try:
            return read_ws_frame(self.rfile)
        except TimeoutError as exc:
            raise ConnectionError("websocket frame read timed out") from exc
        finally:
            self.connection.settimeout(previous_timeout)

    def handle_ws_payload(
        self,
        session: str,
        master_fd: int,
        resize_fd: int,
        process: subprocess.Popen[Any],
        payload: bytes,
        readonly: bool = False,
        resize_state: dict[str, int] | None = None,
        tmux_client_name: str = "",
        resize_client_id: str = "",
    ) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            if readonly:
                return
            os.write(master_fd, payload)
            return
        msg_type = message.get("type")
        if msg_type == "refresh":
            refresh_tmux_session_clients(session)
        elif msg_type == "input":
            if readonly:
                return
            data = message.get("data")
            if isinstance(data, str):
                filtered = strip_terminal_query_responses(data)
                if filtered:
                    os.write(master_fd, filtered.encode("utf-8"))
                    # DOIT.58 Phase 1: one user-input heartbeat (readonly already returned above).
                    self.server.app.record_user_input(session, len(filtered), data=filtered)
        elif msg_type == "resize":
            if readonly:
                return
            if message.get("foreground") is False:
                return
            dimensions = ws_resize_dimensions(message, DEFAULT_ROWS, DEFAULT_COLS)
            if dimensions:
                rows, cols = dimensions
                authority_changed = False
                if message.get("foreground") is True or message.get("activate") is True:
                    claimer = getattr(self.server, "claim_resize_authority", None)
                    if callable(claimer):
                        authority_changed = bool(claimer(session, tmux_client_name, resize_client_id, cols))
                previous = (
                    resize_state.get("rows"),
                    resize_state.get("cols"),
                ) if isinstance(resize_state, dict) else (None, None)
                size_changed = previous != (rows, cols)
                if size_changed:
                    resize_pty_and_signal_process(resize_fd, process, rows, cols)
                    if isinstance(resize_state, dict):
                        resize_state["rows"] = rows
                        resize_state["cols"] = cols
                recorder = getattr(self.server, "record_host_pty_dimensions", None)
                if callable(recorder) and (size_changed or authority_changed):
                    recorder(session, rows, cols)
        elif msg_type == "tmux-scroll":
            if readonly:
                return
            direction = message.get("direction")
            lines = message.get("lines")
            if isinstance(direction, str) and isinstance(lines, int):
                self.server.app.tmux_scroll(session, direction, lines)


TLS_FIRST_BYTES = {0x16, 0x80}
HTTP_METHOD_PREFIXES = (b"GET ", b"HEAD ", b"POST ", b"PUT ", b"DELETE ", b"OPTIONS ", b"PATCH ", b"TRACE ", b"CONNECT ")


def parse_http_request_target(request_bytes: bytes) -> tuple[str, str]:
    text = request_bytes.decode("iso-8859-1", errors="replace")
    lines = text.splitlines()
    request_line = lines[0] if lines else ""
    parts = request_line.split()
    target = parts[1] if len(parts) >= 2 and parts[1].startswith("/") else "/"
    host = ""
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator and name.lower() == "host":
            host = value.strip()
            break
    return host, target


def https_redirect_response(request_bytes: bytes, fallback_host: str) -> bytes:
    host, target = parse_http_request_target(request_bytes)
    location = f"https://{host or fallback_host}{target}"
    body = f"Use HTTPS for this YOLOmux server: {location}\n".encode("utf-8")
    headers = [
        b"HTTP/1.1 308 Permanent Redirect",
        f"Location: {location}".encode("utf-8"),
        b"Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(body)}".encode("ascii"),
        b"Connection: close",
        b"",
        b"",
    ]
    return b"\r\n".join(headers) + body


class TmuxWebtermHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    tls_peek_timeout_seconds = 2.0

    def __init__(self, server_address: tuple[str, int], app: TmuxWebtermApp, tls_context: ssl.SSLContext | None = None, dev: bool = False):
        super().__init__(server_address, Handler)
        self.app = app
        self.tls_context = tls_context
        self.dev = dev  # dev-velocity #1b: enables the /api/dev-reload SSE channel + the bootstrap dev flag
        self.host_pty_dimensions_lock = threading.Lock()
        self.host_pty_dimensions: dict[str, tuple[int, int]] = {}
        self.share_upstreams_lock = threading.Lock()
        self.share_upstreams: dict[tuple[str, str], ShareTerminalUpstream] = {}
        self.share_ui_clients_lock = threading.Lock()
        self.share_ui_clients: dict[str, set[ShareViewerConnection]] = {}
        self.share_replay_keyframes_lock = threading.Lock()
        self.share_replay_keyframes: dict[str, dict[str, Any]] = {}
        self.share_replay_deltas_lock = threading.Lock()
        self.share_replay_deltas: dict[str, dict[int, list[dict[str, Any]]]] = {}
        self.share_pointer_lock = threading.Lock()
        self.share_pointer_latest: dict[str, dict[str, Any]] = {}
        self.share_pointer_clicks: dict[str, list[dict[str, Any]]] = {}
        self.share_pointer_threads: dict[str, threading.Thread] = {}
        self.share_pointer_stop = threading.Event()
        if hasattr(self.app, "start_tabber_activity_cache_warmer"):
            self.app.start_tabber_activity_cache_warmer()
        if hasattr(self.app, "start_stats_history_sampler"):
            self.app.start_stats_history_sampler()
        if hasattr(self.app, "start_update_check_thread"):
            self.app.start_update_check_thread()

    def server_close(self) -> None:
        self.share_pointer_stop.set()
        if hasattr(self.app, "stop_stats_history_sampler"):
            self.app.stop_stats_history_sampler()
        if hasattr(self.app, "stop_client_event_watcher"):
            self.app.stop_client_event_watcher()
        super().server_close()

    def record_host_pty_dimensions(self, session: str, rows: int, cols: int) -> None:
        clean_session = str(session or "")
        if not clean_session:
            return
        dimensions = (clamp_pty_dimension(rows), clamp_pty_dimension(cols))
        with self.host_pty_dimensions_lock:
            self.host_pty_dimensions[clean_session] = dimensions
        with self.share_upstreams_lock:
            upstream_entries = [(token, upstream) for (token, upstream_session), upstream in self.share_upstreams.items() if upstream_session == clean_session]
        for _token, upstream in upstream_entries:
            upstream.update_dimensions(*dimensions, refresh=False)
        for token in {token for token, _upstream in upstream_entries}:
            self.broadcast_share_ui(token, {
                "type": SHARE_MIRROR_FRAME_TERMINAL_HOST_RESIZE,
                "payload": {"session": clean_session, "rows": dimensions[0], "cols": dimensions[1]},
            })
        for _token, upstream in upstream_entries:
            upstream.request_refresh_client()

    def host_pty_dimensions_for_session(self, session: str) -> tuple[int, int]:
        with self.host_pty_dimensions_lock:
            return self.host_pty_dimensions.get(str(session or ""), (DEFAULT_ROWS, DEFAULT_COLS))

    def claim_resize_authority(self, session: str, tmux_client_name: str, resize_client_id: str = "", active_cols: int | None = None) -> bool:
        del resize_client_id
        return claim_tmux_resize_authority(session, tmux_client_name, active_cols)

    def share_terminal_upstream(self, token: str, session: str) -> ShareTerminalUpstream:
        key = (str(token or ""), str(session or ""))
        with self.share_upstreams_lock:
            upstream = self.share_upstreams.get(key)
            if upstream is None:
                upstream = ShareTerminalUpstream(self, key[0], key[1])
                self.share_upstreams[key] = upstream
            return upstream

    def release_share_terminal_upstream(self, token: str, session: str, viewer: ShareViewerConnection) -> None:
        key = (str(token or ""), str(session or ""))
        stop_upstream: ShareTerminalUpstream | None = None
        with self.share_upstreams_lock:
            upstream = self.share_upstreams.get(key)
            if upstream is None:
                return
            upstream.remove_viewer(viewer)
            if not upstream.has_viewers():
                stop_upstream = upstream
                self.share_upstreams.pop(key, None)
        if stop_upstream is not None:
            stop_upstream.stop()

    def register_share_ui_client(self, token: str, client: ShareViewerConnection) -> None:
        clean_token = str(token or "")
        if not clean_token:
            return
        with self.share_ui_clients_lock:
            self.share_ui_clients.setdefault(clean_token, set()).add(client)

    def release_share_ui_client(self, token: str, client: ShareViewerConnection) -> None:
        clean_token = str(token or "")
        if not clean_token:
            return
        with self.share_ui_clients_lock:
            clients = self.share_ui_clients.get(clean_token)
            if clients is None:
                return
            clients.discard(client)
            if not clients:
                self.share_ui_clients.pop(clean_token, None)

    def close_inactive_share_upstreams(self) -> None:
        stale: list[ShareTerminalUpstream] = []
        with self.share_upstreams_lock:
            for key, upstream in list(self.share_upstreams.items()):
                token, _session = key
                if self.app.verify_share_token(token) is None:
                    stale.append(upstream)
                    self.share_upstreams.pop(key, None)
        for upstream in stale:
            upstream.stop()
        stale_clients: list[ShareViewerConnection] = []
        with self.share_ui_clients_lock:
            for token, clients in list(self.share_ui_clients.items()):
                if self.app.verify_share_token(token) is not None:
                    continue
                stale_clients.extend(clients)
                self.share_ui_clients.pop(token, None)
        for client in stale_clients:
            client.close("share-inactive")
        self.prune_inactive_share_replay_keyframes()
        self.prune_inactive_share_replay_deltas()

    def record_share_replay_keyframe(self, token: str, message: dict[str, Any]) -> None:
        clean_token = str(token or "")
        if not clean_token:
            return
        if self.app.verify_share_token(clean_token) is None:
            with self.share_replay_keyframes_lock:
                self.share_replay_keyframes.pop(clean_token, None)
            with self.share_replay_deltas_lock:
                self.share_replay_deltas.pop(clean_token, None)
            return
        if str((message or {}).get("type") or "") != SHARE_MIRROR_FRAME_DOM_KEYFRAME:
            return
        payload = message.get("payload") if isinstance(message, dict) else None
        if not isinstance(payload, dict):
            return
        stored = redact_share_ui_value({
            "type": SHARE_MIRROR_FRAME_DOM_KEYFRAME,
            "payload": payload,
            "sender": str(message.get("sender") or ""),
        })
        for key in SHARE_MIRROR_RELAY_NUMBER_FIELDS:
            value = message.get(key)
            if isinstance(value, (int, float)):
                stored[key] = int(value)
        reason = message.get("reason")
        if isinstance(reason, str) and reason.strip():
            stored["reason"] = reason.strip()[:120]
        with self.share_replay_keyframes_lock:
            self.share_replay_keyframes[clean_token] = stored
        epoch = share_replay_frame_int(stored.get("epoch"))
        sequence = share_replay_frame_int(stored.get("sequence"))
        if epoch is not None and sequence is not None:
            with self.share_replay_deltas_lock:
                epoch_map = self.share_replay_deltas.get(clean_token)
                if epoch_map is not None:
                    for existing_epoch in list(epoch_map):
                        if existing_epoch != epoch:
                            epoch_map.pop(existing_epoch, None)
                    epoch_deltas = epoch_map.get(epoch)
                    if epoch_deltas is not None:
                        epoch_map[epoch] = [
                            delta for delta in epoch_deltas
                            if (share_replay_frame_int(delta.get("sequence")) or -1) > sequence
                        ]
                        if not epoch_map[epoch]:
                            epoch_map.pop(epoch, None)
                    if not epoch_map:
                        self.share_replay_deltas.pop(clean_token, None)

    def record_share_replay_delta(self, token: str, message: dict[str, Any]) -> bool:
        clean_token = str(token or "")
        if not clean_token:
            return False
        if self.app.verify_share_token(clean_token) is None:
            with self.share_replay_deltas_lock:
                self.share_replay_deltas.pop(clean_token, None)
            return False
        if str((message or {}).get("type") or "") != SHARE_MIRROR_FRAME_DOM_DELTA:
            return False
        payload = message.get("payload") if isinstance(message, dict) else None
        if not isinstance(payload, dict):
            return False
        epoch = share_replay_frame_int(message.get("epoch"))
        sequence = share_replay_frame_int(message.get("sequence"))
        base_sequence = share_replay_frame_int(message.get("baseSequence"))
        if epoch is None or sequence is None or base_sequence is None:
            return False
        stored = redact_share_ui_value({
            "type": SHARE_MIRROR_FRAME_DOM_DELTA,
            "payload": payload,
            "sender": str(message.get("sender") or ""),
            "epoch": epoch,
            "sequence": sequence,
            "baseSequence": base_sequence,
        })
        version = share_replay_frame_int(message.get("version"))
        if version is not None:
            stored["version"] = version
        reason = message.get("reason")
        if isinstance(reason, str) and reason.strip():
            stored["reason"] = reason.strip()[:120]
        with self.share_replay_deltas_lock:
            epoch_map = self.share_replay_deltas.setdefault(clean_token, {})
            epoch_deltas = epoch_map.setdefault(epoch, [])
            epoch_deltas[:] = [
                delta for delta in epoch_deltas
                if share_replay_frame_int(delta.get("sequence")) != sequence
            ]
            epoch_deltas.append(stored)
            epoch_deltas.sort(key=lambda delta: share_replay_frame_int(delta.get("sequence")) or 0)
            del epoch_deltas[:-SHARE_REPLAY_DELTA_RING_LIMIT]
            for existing_epoch in list(epoch_map):
                if existing_epoch != epoch and not epoch_map[existing_epoch]:
                    epoch_map.pop(existing_epoch, None)
        return True

    def latest_share_replay_keyframe(self, token: str) -> dict[str, Any] | None:
        clean_token = str(token or "")
        if not clean_token:
            return None
        if self.app.verify_share_token(clean_token) is None:
            with self.share_replay_keyframes_lock:
                self.share_replay_keyframes.pop(clean_token, None)
            with self.share_replay_deltas_lock:
                self.share_replay_deltas.pop(clean_token, None)
            return None
        with self.share_replay_keyframes_lock:
            frame = self.share_replay_keyframes.get(clean_token)
            return copy.deepcopy(frame) if frame else None

    def share_replay_deltas_after_keyframe(self, token: str, keyframe: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        clean_token = str(token or "")
        epoch = share_replay_frame_int((keyframe or {}).get("epoch"))
        cursor = share_replay_frame_int((keyframe or {}).get("sequence"))
        if not clean_token or epoch is None or cursor is None:
            return [], True
        with self.share_replay_deltas_lock:
            epoch_map = self.share_replay_deltas.get(clean_token, {})
            deltas = [copy.deepcopy(delta) for delta in epoch_map.get(epoch, [])]
        deltas.sort(key=lambda delta: share_replay_frame_int(delta.get("sequence")) or 0)
        contiguous: list[dict[str, Any]] = []
        for delta in deltas:
            sequence = share_replay_frame_int(delta.get("sequence"))
            base_sequence = share_replay_frame_int(delta.get("baseSequence"))
            if sequence is None or base_sequence is None:
                return contiguous, False
            if sequence <= cursor:
                continue
            if base_sequence != cursor or sequence != cursor + 1:
                return contiguous, False
            contiguous.append(delta)
            cursor = sequence
        return contiguous, True

    def enqueue_latest_share_replay_keyframe(self, token: str, client: ShareViewerConnection) -> bool:
        frame = self.latest_share_replay_keyframe(token)
        if not frame:
            return False
        return client.enqueue_reset_frame(share_ui_frame(frame)) == "queued"

    def enqueue_share_replay_frames_for_viewer(self, token: str, client: ShareViewerConnection) -> bool:
        frame = self.latest_share_replay_keyframe(token)
        if not frame:
            self.request_share_replay_keyframe(token, reason="join", skip_client_id=client.client_id, viewer_id=client.client_id)
            return False
        deltas, contiguous = self.share_replay_deltas_after_keyframe(token, frame)
        if not contiguous:
            self.request_share_replay_keyframe(token, reason="join", skip_client_id=client.client_id, viewer_id=client.client_id)
            return False
        frames = [frame] + (deltas if contiguous else [])
        for index, replay_frame in enumerate(frames):
            status = (
                client.enqueue_reset_frame(share_ui_frame(replay_frame))
                if index == 0 and str(replay_frame.get("type") or "") == SHARE_MIRROR_FRAME_DOM_KEYFRAME
                else client.enqueue(share_ui_frame(replay_frame))
            )
            if status != "queued":
                self.request_share_replay_keyframe(token, reason="backpressure", skip_client_id=client.client_id, viewer_id=client.client_id)
                return False
        return True

    def request_share_replay_keyframe(self, token: str, reason: str = "gap", *, skip_client_id: str = "", viewer_id: str = "") -> None:
        clean_token = str(token or "")
        if not clean_token or self.app.verify_share_token(clean_token) is None:
            return
        clean_reason = str(reason or "gap").strip()
        if clean_reason not in SHARE_MIRROR_KEYFRAME_REASONS:
            clean_reason = "gap"
        payload: dict[str, Any] = {"reason": clean_reason}
        if viewer_id:
            payload["viewerId"] = str(viewer_id)
        self.broadcast_share_ui(
            clean_token,
            {
                "type": SHARE_MIRROR_FRAME_DOM_KEYFRAME_REQUEST,
                "payload": payload,
                "sender": SHARE_REPLAY_SERVER_SENDER,
                "version": SHARE_MIRROR_PROTOCOL_VERSION,
                "reason": clean_reason,
            },
            skip_client_id=skip_client_id,
        )

    def prune_inactive_share_replay_keyframes(self) -> None:
        with self.share_replay_keyframes_lock:
            for token in list(self.share_replay_keyframes):
                if self.app.verify_share_token(token) is None:
                    self.share_replay_keyframes.pop(token, None)

    def prune_inactive_share_replay_deltas(self) -> None:
        with self.share_replay_deltas_lock:
            for token in list(self.share_replay_deltas):
                if self.app.verify_share_token(token) is None:
                    self.share_replay_deltas.pop(token, None)

    def share_viewer_count(self, token: str) -> int:
        count = 0
        with self.share_upstreams_lock:
            upstreams = [upstream for (share_token, _session), upstream in self.share_upstreams.items() if share_token == token]
        for upstream in upstreams:
            with upstream.lock:
                count += len(upstream.viewers)
        return count

    def share_ui_client_count(self, token: str) -> int:
        with self.share_ui_clients_lock:
            return len(self.share_ui_clients.get(str(token or ""), set()))

    def share_pointer_hz(self, token: str) -> int:
        viewers = max(1, self.share_viewer_count(token), self.share_ui_client_count(token))
        return max(1, min(SHARE_POINTER_MAX_HZ, math.ceil(SHARE_POINTER_MAX_WRITES_PER_SECOND / viewers)))

    def queue_share_pointer(self, token: str, payload: dict[str, Any], sender: str = "") -> None:
        clean_token = str(token or "")
        if not clean_token:
            return
        clean_payload = dict(payload or {})
        clean_payload["sender"] = str(sender or clean_payload.get("sender") or "")
        click = clean_payload.get("click") is True
        with self.share_pointer_lock:
            self.share_pointer_latest[clean_token] = {key: value for key, value in clean_payload.items() if key != "click"}
            if click:
                clicks = self.share_pointer_clicks.setdefault(clean_token, [])
                clicks.append(clean_payload | {"click": True})
                del clicks[:-SHARE_POINTER_CLICK_QUEUE_LIMIT]
            thread = self.share_pointer_threads.get(clean_token)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(target=self.share_pointer_loop, args=(clean_token,), name="share-pointer", daemon=True)
                self.share_pointer_threads[clean_token] = thread
                thread.start()

    def share_pointer_loop(self, token: str) -> None:
        last_sent = ""
        while not self.share_pointer_stop.is_set():
            if self.app.verify_share_token(token) is None:
                break
            hz = self.share_pointer_hz(token)
            time.sleep(1.0 / max(1, hz))
            messages: list[dict[str, Any]] = []
            with self.share_pointer_lock:
                latest = self.share_pointer_latest.get(token)
                clicks = self.share_pointer_clicks.pop(token, [])
                if latest:
                    signature = json.dumps(latest, sort_keys=True, separators=(",", ":"))
                    if signature != last_sent:
                        messages.append(latest)
                        last_sent = signature
                messages.extend(clicks)
            for payload in messages:
                sender = str(payload.get("sender") or "")
                self.broadcast_share_ui(
                    token,
                    {"type": SHARE_MIRROR_FRAME_POINTER, "payload": payload, "sender": sender},
                    skip_client_id=sender,
                )
        with self.share_pointer_lock:
            self.share_pointer_latest.pop(token, None)
            self.share_pointer_clicks.pop(token, None)
            self.share_pointer_threads.pop(token, None)

    def broadcast_share_ui(self, token: str, message: dict[str, Any], *, skip_client_id: str = "") -> None:
        clean_message = dict(message or {})
        if skip_client_id and not clean_message.get("sender"):
            clean_message["sender"] = skip_client_id
        frame = share_ui_frame(clean_message)
        delta_overflow = False
        keyframe_frame = clean_message.get("type") == SHARE_MIRROR_FRAME_DOM_KEYFRAME
        with self.share_ui_clients_lock:
            clients = list(self.share_ui_clients.get(str(token or ""), set()))
        ui_client_ids = {client.client_id for client in clients if client.client_id}
        with self.share_upstreams_lock:
            upstreams = [upstream for (share_token, _session), upstream in self.share_upstreams.items() if share_token == token]
        for upstream in upstreams:
            with upstream.lock:
                viewers = list(upstream.viewers)
            for viewer in viewers:
                if skip_client_id and viewer.client_id == skip_client_id:
                    continue
                if viewer.client_id and viewer.client_id in ui_client_ids:
                    continue
                status = viewer.enqueue_reset_frame(frame) if keyframe_frame else viewer.enqueue(frame)
                if status == "overflow" and clean_message.get("type") == SHARE_MIRROR_FRAME_DOM_DELTA:
                    delta_overflow = True
                if status == "too-slow":
                    viewer.close("too-slow")
        for client in clients:
            if skip_client_id and client.client_id == skip_client_id:
                continue
            status = client.enqueue_reset_frame(frame) if keyframe_frame else client.enqueue(frame)
            if status == "overflow" and clean_message.get("type") == SHARE_MIRROR_FRAME_DOM_DELTA:
                delta_overflow = True
            if status == "too-slow":
                client.close("too-slow")
        if delta_overflow:
            self.request_share_replay_keyframe(str(token or ""), reason="backpressure")

    def broadcast_share_status(self, token: str) -> None:
        payload_builder = getattr(self.app, "share_status_frame_payload", None)
        payload = payload_builder(token) if callable(payload_builder) else None
        if not payload:
            return
        self.broadcast_share_ui(token, {"type": SHARE_MIRROR_FRAME_SHARE_STATUS, "payload": payload})

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        return self.socket.accept()

    def prepare_request_socket(self, request: socket.socket) -> socket.socket:
        if not self.tls_context or isinstance(request, ssl.SSLSocket):
            return request
        previous_timeout = request.gettimeout()
        request.settimeout(self.tls_peek_timeout_seconds)
        try:
            first = request.recv(1, socket.MSG_PEEK)
        except (socket.timeout, BlockingIOError):
            # Idle preconnect socket. This runs in the per-connection worker thread, so this longer
            # wait does not block accept(); wrapping keeps browser TLS preconnects from surfacing as
            # ERR_CONNECTION_CLOSED.
            request.settimeout(previous_timeout)
            return self.tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
        if first and first[0] in TLS_FIRST_BYTES:
            request.settimeout(previous_timeout)
            return self.tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
        request.settimeout(previous_timeout)
        return request

    def server_name_with_port(self) -> str:
        host, port = self.server_address[:2]
        if host in {"0.0.0.0", "::"}:
            host = "localhost"
        return f"{host}:{port}"

    def handle_error(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, ssl.SSLError):
            host = client_address[0] if client_address else "unknown"
            reason = getattr(error, "reason", None) or str(error)
            sys.stderr.write(f"{host} - - TLS handshake closed: {reason}\n")
            return
        super().handle_error(request, client_address)

from __future__ import annotations

import base64
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
from .common import MAX_EVENT_TAIL_LINES
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import PROJECT_ROOT
from .common import SUMMARY_CODEX_EFFORT
from .common import SUMMARY_CODEX_MODEL
from .common import SUMMARY_CODEX_SERVICE_TIER
from .common import SUMMARY_CODEX_TIMEOUT_SECONDS
from .common import SUMMARY_LOOKBACK_SECONDS
from .common import WEBSOCKET_GUID
from .common import auth_setup_required
from .common import codex_event_kind
from .common import codex_exec_argv
from .common import error_payload
from .common import parse_bool
from .common import terminate_process_group
from .filesystem import FilesystemError
from .tmux_utils import tmux_session_target
from .transcripts import codex_event_text
from .transcripts import compact_transcript_items
from .transcripts import strip_terminal_query_responses
from .transcripts import transcript_items_from_raw_line
from .uploads import parse_multipart_upload
from .server_auth import AuthMixin
from .web import html_page
from .web import static_asset_path
from .web import static_content_type
from .websocket import make_ws_frame
from .websocket import read_ws_frame
from .websocket import set_pty_size


PTY_DIMENSION_MIN = 1
PTY_DIMENSION_MAX = 1000
WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS = 5.0
SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES = 256 * 1024
SHARE_VIEWER_OVERFLOW_LIMIT = 3
SHARE_VIEWER_OVERFLOW_WINDOW_SECONDS = 60.0
SHARE_REFRESH_CLIENT_MIN_SECONDS = 1.0
SHARE_POINTER_MAX_WRITES_PER_SECOND = 1500
SHARE_POINTER_MAX_HZ = 30
SHARE_POINTER_CLICK_QUEUE_LIMIT = 32
MAX_FS_BATCH_REQUESTS = 64
TOKEN_LOG_RE = re.compile(r"([?&]token=)[^&\s\"]+")
SHARE_URL_SECRET_RE = re.compile(r"(?:https?://[^\"'\s<>]+)?/share/[A-Za-z0-9_-]+(?:#[^\"'\s<>]*)?")


def query_one(qs: dict[str, list[str]], name: str, default: str | None = "") -> str | None:
    values = qs.get(name)
    return values[0] if values else default


def query_bool(qs: dict[str, list[str]], name: str, default: bool = False) -> bool:
    raw_default = "1" if default else "0"
    return parse_bool(str(query_one(qs, name, raw_default) or ""))


def content_disposition_attachment(raw_path: str) -> str:
    name = Path(str(raw_path or "")).name or "download"
    safe = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";", "/"} else "_" for char in name).strip()
    return f'attachment; filename="{safe or "download"}"'


def parse_query_int(
    qs: dict[str, list[str]],
    name: str,
    default: int,
    *,
    min_value: int = 1,
    max_value: int | None = None,
) -> tuple[int | None, str]:
    raw = qs.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be an integer"
    if value < min_value:
        return None, f"{name} must be at least {min_value}"
    if max_value is not None:
        value = min(value, max_value)
    return value, ""


def parse_query_float(
    qs: dict[str, list[str]],
    name: str,
    default: float,
    *,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> tuple[float | None, str]:
    raw = qs.get(name, [str(default)])[0]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be a number"
    if not math.isfinite(value):
        return None, f"{name} must be finite"
    if value < min_value:
        return None, f"{name} must be at least {min_value:g}"
    if max_value is not None:
        value = min(value, max_value)
    return value, ""


def parse_repo_refs_param(raw: str | None) -> dict[str, dict[str, str]] | None:
    # C6: decode the optional per-repo FROM/TO override map sent as URL-encoded JSON
    # ({repo_path: {"from": <ref>, "to": <ref>}}). Returns None for absent/malformed input so the caller
    # falls back to the scalar from/to; only well-formed string ref pairs survive.
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    result: dict[str, dict[str, str]] = {}
    for repo, refs in decoded.items():
        if not isinstance(repo, str) or not isinstance(refs, dict):
            continue
        entry: dict[str, str] = {}
        for key in ("from", "to"):
            value = refs.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
        if entry:
            result[repo] = entry
    return result or None


def clamp_pty_dimension(value: int) -> int:
    return max(PTY_DIMENSION_MIN, min(value, PTY_DIMENSION_MAX))


def ws_resize_dimensions(message: dict[str, Any], default_rows: int, default_cols: int) -> tuple[int, int] | None:
    cols = message.get("cols")
    rows = message.get("rows")
    if not isinstance(cols, int) or isinstance(cols, bool) or not isinstance(rows, int) or isinstance(rows, bool):
        return None
    return clamp_pty_dimension(rows), clamp_pty_dimension(cols)


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

    def clear_frames_locked(self) -> None:
        while True:
            try:
                frame = self.frames.get_nowait()
            except queue.Empty:
                break
            if frame is not None:
                self.queued_bytes = max(0, self.queued_bytes - len(frame))

    def enqueue(self, frame: bytes) -> str:
        with self.lock:
            if self.closed:
                return "closed"
            if self.queued_bytes + len(frame) > SHARE_VIEWER_QUEUE_HIGH_WATER_BYTES:
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

    def write_loop(self) -> None:
        try:
            while True:
                frame = self.frames.get()
                if frame is None:
                    break
                with self.lock:
                    self.queued_bytes = max(0, self.queued_bytes - len(frame))
                self.connection.sendall(frame)
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
        set_pty_size(slave_fd, rows, cols)
        self.master_fd = master_fd
        self.slave_fd = slave_fd
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        subprocess.run(
            ["tmux", "set-option", "-s", "set-clipboard", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        record = self.server.app.verify_share_token(self.token)
        readonly = str((record or {}).get("mode") or "ro") != "rw"
        attach_args = ["tmux", "attach-session"]
        if readonly:
            attach_args.append("-r")
        attach_args.extend(["-t", tmux_session_target(self.session)])
        self.process = subprocess.Popen(
            attach_args,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
            start_new_session=True,
        )
        self.reader_thread = threading.Thread(target=self.reader_loop, name=f"share-terminal-{self.session}", daemon=True)
        self.reader_thread.start()

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
        self.server.app.record_user_input(self.session, len(filtered), source="share")
        return True

    def update_dimensions(self, rows: int, cols: int, *, refresh: bool = True) -> None:
        refresh_needed = False
        with self.lock:
            slave_fd = self.slave_fd
            process = self.process
            if slave_fd is None:
                return
            set_pty_size(slave_fd, rows, cols)
            refresh_needed = True
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGWINCH)
                except OSError:
                    pass
        if refresh_needed and refresh:
            self.request_refresh_client()

    def reader_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                master_fd = self.master_fd
                process = self.process
                if master_fd is None or process is None:
                    break
                if process.poll() is not None:
                    break
                readable, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd not in readable:
                    continue
                data = os.read(master_fd, 65536)
                if not data:
                    break
                self.broadcast(data)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
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
        subprocess.run(
            ["tmux", "refresh-client", "-t", tmux_session_target(self.session)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

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
        if path.startswith("/share/"):
            short_id = path.removeprefix("/share/").strip("/")
            finder = getattr(self.server.app, "share_record_for_short_id", None)
            record = finder(short_id) if short_id and "/" not in short_id and callable(finder) else None
            return bool(record and record.get("http_allowed"))
        if path.startswith("/static/"):
            checker = getattr(self.server.app, "http_allowed_share_is_active", None)
            return bool(checker()) if callable(checker) else False
        if path in {"/", "/ws/share-view", "/api/ping", "/api/share-stream"}:
            verifier = getattr(self.server.app, "verify_share_token", None)
            record = verifier(self.share_token_text()) if callable(verifier) else None
            return bool(record and record.get("http_allowed"))
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
        parsed = urlparse(self.path)
        if self.redirect_plaintext_to_https_if_needed(parsed):
            return
        if parsed.path.startswith("/static/"):
            asset = parsed.path.removeprefix("/static/")
            content_type = static_content_type(asset)
            if content_type:
                self.write_static_asset(asset, content_type)
                return
        if parsed.path == "/api/auth-setup":
            self.write_json({"setup_required": auth_setup_required()})
            return
        if parsed.path == "/login":
            self.handle_login_page(parsed)
            return
        if parsed.path == "/logout":
            self.write_redirect("/login", clear_auth=True)
            return
        if parsed.path.startswith("/share/") and self.handle_share_shell(parsed):
            return
        admin_only_paths = {"/api/summary-stream", "/api/share", "/ws/share-host"}
        if parsed.path == "/api/share" and self.share_token_text():
            required_role = "readonly"
        else:
            required_role = "admin" if parsed.path in admin_only_paths else "readonly"
        if not self.require_auth(required_role):
            return
        # blame reads repository file history, so it is admin-only like the rest of the
        # file/repo API (the /api/fs/* reads) — a readonly identity must not read file content/history.
        if (parsed.path.startswith("/api/fs/") or parsed.path == "/api/blame") and self.auth_readonly() and not self.share_readonly_api_allowed(parsed):
            self.reject_forbidden(self.auth_identity(), "admin")
            return
        if parsed.path == "/api/ping":
            self.write_json({"ok": True, "time": time.time()})
            return
        if parsed.path == "/api/update-status":
            if self.auth_readonly():
                self.reject_forbidden(self.auth_identity(), "admin")
                return
            self.write_json(self.server.app.update_status_payload(dryrun=query_bool(parse_qs(parsed.query), "dryrun")))
            return
        if parsed.path == "/api/dev-reload":
            # Dev-velocity #1b: an SSE stream that emits the static-bundle signature whenever it changes,
            # so a dev page reloads itself on rebuild (ends the "is the bundle stale?" misdiagnoses). Only
            # active under --dev; a 404 otherwise so production never exposes it.
            if not getattr(self.server, "dev", False):
                self.write_json(error_payload("not found", status=HTTPStatus.NOT_FOUND), status=HTTPStatus.NOT_FOUND)
                return
            self.stream_dev_reload()
            return
        if parsed.path == "/api/client-events":
            self.stream_client_events()
            return
        if parsed.path == "/":
            sessions = self.share_sessions() if self.share_sessions() else self.server.app.sessions
            self.write_html(html_page(
                sessions,
                self.auth_identity().role,
                dev=getattr(self.server, 'dev', False),
                dangerously_yolo=self.server.app.dangerously_yolo,
                share=self.share_bootstrap_payload(self.share_record()) if self.share_record() else None,
            ))
            return
        if parsed.path == "/preview-popout":
            self.handle_preview_popout_placeholder(parsed)
            return
        if parsed.path == "/api/transcripts":
            qs = parse_qs(parsed.query)
            self.write_json(self.share_scoped_transcripts_payload(self.server.app.transcripts_payload(force=query_bool(qs, "force"))))
            return
        if parsed.path == "/api/activity-summary":
            qs = parse_qs(parsed.query)
            self.write_json(self.server.app.activity_summary_payload(
                force=query_bool(qs, "force"),
                locale=str(query_one(qs, "locale", "en") or "en"),
            ))
            return
        if parsed.path == "/api/tmux":
            self.write_int_query_app_result(
                parsed,
                "lines",
                90,
                MAX_TRANSCRIPT_TAIL_LINES,
                lambda qs, lines: self.server.app.tmux_snapshot(str(query_one(qs, "session", "") or ""), lines),
            )
            return
        if parsed.path == "/api/transcript":
            self.write_int_query_app_result(
                parsed,
                "lines",
                120,
                MAX_TRANSCRIPT_TAIL_LINES,
                lambda qs, lines: self.server.app.transcript_tail(str(query_one(qs, "session", "") or ""), lines),
            )
            return
        if parsed.path == "/api/context":
            self.write_int_query_app_result(
                parsed,
                "messages",
                40,
                MAX_COMPACT_TRANSCRIPT_ITEMS,
                lambda qs, messages: self.server.app.context_tail(str(query_one(qs, "session", "") or ""), messages),
            )
            return
        if parsed.path == "/api/context-items":
            self.write_int_query_app_result(
                parsed,
                "messages",
                40,
                MAX_COMPACT_TRANSCRIPT_ITEMS,
                lambda qs, messages: self.server.app.context_items(str(query_one(qs, "session", "") or ""), messages),
            )
            return
        if parsed.path == "/api/context-stream":
            self.stream_context_items(parsed)
            return
        if parsed.path == "/api/summary-stream":
            self.stream_codex_summary(parsed)
            return
        if parsed.path == "/api/auto-approve":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            self.write_app_result(self.server.app.auto_approve_status(session))
            return
        if parsed.path == "/api/notify":
            self.write_json(self.server.app.notify_status())
            return
        if parsed.path == "/api/settings":
            self.write_json(self.server.app.settings_payload())
            return
        if parsed.path == "/api/share":
            if self.share_token():
                self.write_app_result(self.server.app.share_status_payload(self.share_token(), base_url=self.request_base_url()))
            else:
                self.write_app_result(self.server.app.active_share_payload(base_url=self.request_base_url()))
            return
        if parsed.path == "/api/watched-prs":
            self.write_json(self.server.app.watched_prs_payload())
            return
        if parsed.path == "/api/yolo-rules":
            self.write_json(self.server.app.yolo_rules_payload())
            return
        if parsed.path == "/api/events":
            self.write_int_query_app_result(
                parsed,
                "limit",
                100,
                MAX_EVENT_TAIL_LINES,
                lambda qs, limit: self.server.app.events_payload(query_one(qs, "session", None), limit),
            )
            return
        if parsed.path == "/api/search":
            self.write_int_query_app_result(
                parsed,
                "limit",
                100,
                MAX_EVENT_TAIL_LINES,
                lambda qs, limit: self.server.app.search_payload(str(query_one(qs, "q", "") or ""), query_one(qs, "session", None), limit),
            )
            return
        if parsed.path == "/api/run-history":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            self.write_app_result(self.server.app.run_history_payload(session))
            return
        if parsed.path == "/api/activity":
            # DOIT.58 Phase 1: per-session/window activity ledger (metadata; readonly-allowed).
            self.write_app_result(self.share_scoped_activity_result(self.server.app.activity_payload()))
            return
        if parsed.path == "/api/session-files":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            hours, error = parse_query_float(qs, "hours", 24.0, max_value=24.0 * 365.0)
            if error:
                self.write_json(error_payload(error, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
                return
            from_ref = query_one(qs, "from", None)
            to_ref = query_one(qs, "to", None)
            force = query_bool(qs, "force")
            if self.share_sessions() and session not in self.share_sessions():
                self.write_json(error_payload("share token is scoped to a different session", status=HTTPStatus.FORBIDDEN), status=HTTPStatus.FORBIDDEN)
                return
            # C6: optional per-repo override map ({repo_path: {"from","to"}}) as URL-encoded JSON, so each
            # repo can compare its own commit graph. Malformed JSON falls back to the scalar from/to.
            repo_refs = parse_repo_refs_param(query_one(qs, "refs", None))
            self.write_app_result(self.server.app.session_files_payload(session, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, force=force))
            return
        if parsed.path == "/api/summary":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_app_result(self.server.app.summary(session))
            return
        if parsed.path == "/api/fs/list":
            self.handle_fs_list(parsed)
            return
        if parsed.path == "/api/fs/search":
            self.handle_fs_search(parsed)
            return
        if parsed.path == "/api/fs/index-status":
            self.handle_fs_index_status(parsed)
            return
        if parsed.path == "/api/fs/read":
            self.handle_fs_read(parsed)
            return
        if parsed.path == "/api/fs/info":
            self.handle_fs_info(parsed)
            return
        if parsed.path == "/api/fs/diff":
            self.handle_fs_diff(parsed)
            return
        if parsed.path == "/api/blame":
            self.handle_blame(parsed)
            return
        if parsed.path == "/api/fs/raw":
            self.handle_fs_raw(parsed)
            return
        if parsed.path == "/api/fs/html-preview":
            self.handle_fs_html_preview(parsed)
            return
        if parsed.path == "/ws/share-host":
            self.websocket_share_host(parsed)
            return
        if parsed.path == "/ws/share-ui":
            self.websocket_share_ui(parsed)
            return
        if parsed.path == "/ws/share-view":
            self.websocket_share_view(parsed)
            return
        if parsed.path == "/ws":
            self.websocket(parsed)
            return
        self.write_text("not found\n", status=HTTPStatus.NOT_FOUND)

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

    def read_json_body(self, max_length: int) -> dict[str, Any] | None:
        length_text = self.headers.get("Content-Length", "")
        try:
            length = int(length_text)
        except ValueError:
            self.write_json(error_payload("missing or invalid Content-Length", status=HTTPStatus.LENGTH_REQUIRED), status=HTTPStatus.LENGTH_REQUIRED)
            return None
        if length <= 0 or length > max_length:
            self.write_json(error_payload("content too large", status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE), status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        try:
            body = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            self.write_json(error_payload("request body must be utf-8 JSON", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return None
        try:
            payload = json.loads(body)
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
        parsed = urlparse(self.path)
        if self.redirect_plaintext_to_https_if_needed(parsed):
            return
        if parsed.path == "/login":
            self.handle_login_submit(parsed)
            return
        if not self.require_auth_for_post(parsed.path):
            return
        if parsed.path == "/api/self-update":
            if self.auth_readonly():
                self.reject_forbidden(self.auth_identity(), "admin")
                return
            self.write_json(self.server.app.perform_self_update(dryrun=query_bool(parse_qs(parsed.query), "dryrun")))
            return
        if parsed.path == "/api/ensure-session":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_app_result(self.server.app.ensure_session(session))
            return
        if parsed.path == "/api/create-session":
            qs = parse_qs(parsed.query)
            agent = str(query_one(qs, "agent", "claude") or "claude")
            self.write_app_result(self.server.app.create_next_session(agent))
            return
        if parsed.path == "/api/rename-session":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            new_name = str(query_one(qs, "new_name", "") or "")
            self.write_app_result(self.server.app.rename_session(session, new_name))
            return
        if parsed.path == "/api/kill-session":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_app_result(self.server.app.kill_session(session))
            return
        if parsed.path == "/api/upload":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_app_result(self.handle_upload(session))
            return
        if parsed.path == "/api/auto-approve":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            enabled = query_bool(qs, "enabled")
            self.write_app_result(self.server.app.set_auto_approve(session, enabled))
            return
        if parsed.path == "/api/notify":
            qs = parse_qs(parsed.query)
            enabled = query_bool(qs, "enabled")
            self.write_json(self.server.app.set_notify(enabled))
            return
        if parsed.path == "/api/settings":
            payload = self.read_json_body(64 * 1024)
            if payload is None:
                return
            self.write_json(self.server.app.save_settings(payload.get("settings", payload)))
            return
        if parsed.path == "/api/share":
            payload = self.read_json_body(16 * 1024)
            if payload is None:
                return
            self.write_app_result(self.handle_share_create(payload))
            return
        if parsed.path == "/api/share/stop":
            qs = parse_qs(parsed.query)
            token_or_short_id = str(query_one(qs, "token", "") or query_one(qs, "id", "") or "")
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            if not token_or_short_id and content_length > 0:
                payload = self.read_json_body(4096)
                if payload is None:
                    return
                token_or_short_id = str(payload.get("token") or payload.get("short_id") or payload.get("id") or "")
            result = self.server.app.stop_active_share(token_or_short_id)
            self.server.close_inactive_share_upstreams()
            self.write_app_result(result)
            return
        if parsed.path == "/api/share/extend":
            payload = self.read_json_body(4096)
            if payload is None:
                return
            token_or_short_id = str(payload.get("token") or payload.get("short_id") or payload.get("id") or "")
            add_seconds = payload.get("add_seconds", 600)
            result = self.server.app.extend_share_token(token_or_short_id, add_seconds, base_url=self.request_base_url())
            if result[1] == HTTPStatus.OK:
                token = str(result[0].get("token") or token_or_short_id)
                self.server.broadcast_share_status(token)
            self.write_app_result(result)
            return
        if parsed.path == "/api/watch/roots":
            payload = self.read_json_body(64 * 1024)
            if payload is None:
                return
            self.write_json(self.server.app.update_client_watch_roots(payload))
            return
        if parsed.path == "/api/drop-action/run":
            payload = self.read_json_body(64 * 1024)
            if payload is None:
                return
            self.write_app_result(self.server.app.run_file_drop_action(payload))
            return
        if parsed.path == "/api/yoagent/chat":
            payload = self.read_json_body(64 * 1024)
            if payload is None:
                return
            response, status = self.server.app.yoagent_chat(payload)
            self.write_json(response, status=status)
            return
        if parsed.path == "/api/yoagent/prewarm":
            payload = self.read_json_body(64 * 1024)
            if payload is None:
                return
            response, status = self.server.app.yoagent_prewarm(payload)
            self.write_json(response, status=status)
            return
        if parsed.path == "/api/yoagent/reset":
            self.write_json(self.server.app.reset_yoagent_chat())
            return
        if parsed.path == "/api/yolo-rules/reload":
            self.write_json(self.server.app.reload_yolo_rules())
            return
        if parsed.path == "/api/yolo-rules/open":
            self.write_json(self.server.app.ensure_yolo_rules_file())
            return
        if parsed.path == "/api/tmux-next":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            self.write_app_result(self.server.app.tmux_next_window(session))
            return
        if parsed.path == "/api/tmux-window":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            window = qs.get("window", [""])[0]
            payload, status = self.server.app.tmux_select_window(session, window)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/tmux-copy-selection":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_app_result(self.server.app.tmux_copy_selection(session))
            return
        if parsed.path == "/api/event":
            self.write_app_result(self.handle_client_event())
            return
        if parsed.path == "/api/fs/batch":
            self.handle_fs_batch(parsed)
            return
        if parsed.path == "/api/fs/write":
            self.handle_fs_write(parsed)
            return
        if parsed.path == "/api/fs/delete":
            self.handle_fs_delete(parsed)
            return
        if parsed.path == "/api/fs/unindex":
            self.handle_fs_unindex(parsed)
            return
        if parsed.path == "/api/fs/rename":
            self.handle_fs_rename(parsed)
            return
        if parsed.path == "/api/fs/mkdir":
            self.handle_fs_mkdir(parsed)
            return
        self.write_json(error_payload("not found", status=HTTPStatus.NOT_FOUND), status=HTTPStatus.NOT_FOUND)

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

    def share_scoped_transcripts_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            request_is_https=self.request_is_https(),
            tls_available=self.server.tls_context is not None,
        )

    def handle_fs_batch(self, parsed: Any) -> None:
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
        for index, item in enumerate(requests):
            request_id = item.get("id", index) if isinstance(item, dict) else index
            if not isinstance(item, dict):
                responses.append({"id": request_id, "ok": False, "status": 400, "error": "request must be an object"})
                continue
            op = str(item.get("type", item.get("op", "")) or "")
            raw_path = str(item.get("path", "") or "")
            if op not in {"list", "info"}:
                responses.append({"id": request_id, "ok": False, "status": 400, "error": "unsupported fs batch operation", "path": raw_path})
                continue
            try:
                result = filesystem.list_directory(raw_path) if op == "list" else filesystem.path_info(raw_path)
            except FilesystemError as exc:
                responses.append({"id": request_id, "ok": False, "status": exc.status, "error": str(exc), "path": raw_path})
                continue
            responses.append({"id": request_id, "ok": True, "status": 200, "payload": result})
        self.write_json({"responses": responses})

    def read_urlencoded_form(self) -> dict[str, list[str]]:
        content_length_text = self.headers.get("Content-Length")
        if not content_length_text:
            return {}
        try:
            content_length = int(content_length_text)
        except ValueError:
            self.close_connection = True
            return {}
        if content_length > 16 * 1024:
            self.close_connection = True
            return {}
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        return parse_qs(body, keep_blank_values=True)

    def handle_client_event(self) -> tuple[dict[str, Any], HTTPStatus]:
        content_length_text = self.headers.get("Content-Length")
        if not content_length_text:
            return error_payload("missing Content-Length", status=HTTPStatus.LENGTH_REQUIRED), HTTPStatus.LENGTH_REQUIRED
        try:
            content_length = int(content_length_text)
        except ValueError:
            return error_payload("invalid Content-Length", status=HTTPStatus.BAD_REQUEST), HTTPStatus.BAD_REQUEST
        # reject a non-positive length — `read(-1)` blocks until disconnect and `read(-5)`
        # raises (-> 500); only the upper bound was checked.
        if content_length <= 0:
            return error_payload("invalid Content-Length", status=HTTPStatus.BAD_REQUEST), HTTPStatus.BAD_REQUEST
        if content_length > 64 * 1024:
            self.close_connection = True
            return error_payload("event is too large", status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE), HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        body = self.rfile.read(content_length)
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return error_payload(f"invalid JSON: {exc}", status=HTTPStatus.BAD_REQUEST), HTTPStatus.BAD_REQUEST
        if not isinstance(event, dict):
            return error_payload("event must be an object", status=HTTPStatus.BAD_REQUEST), HTTPStatus.BAD_REQUEST
        return self.server.app.client_event(event)

    def handle_upload(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        content_length_text = self.headers.get("Content-Length")
        if not content_length_text:
            return {"session": session, "error": "missing Content-Length"}, HTTPStatus.LENGTH_REQUIRED
        try:
            content_length = int(content_length_text)
        except ValueError:
            return {"session": session, "error": "invalid Content-Length"}, HTTPStatus.BAD_REQUEST
        # reject a non-positive length (read(-1) blocks until disconnect; read(-5) -> 500).
        if content_length <= 0:
            return {"session": session, "error": "invalid Content-Length"}, HTTPStatus.BAD_REQUEST
        upload_max_bytes = self.server.app.upload_max_bytes()
        if content_length > upload_max_bytes:
            self.close_connection = True
            return {
                "session": session,
                "error": f"upload is too large; limit is {upload_max_bytes} bytes",
            }, HTTPStatus.REQUEST_ENTITY_TOO_LARGE

        body = self.rfile.read(content_length)
        try:
            files = parse_multipart_upload(self.headers.get("Content-Type", ""), body, max_part_bytes=upload_max_bytes)
        except ValueError as exc:
            return {"session": session, "error": str(exc)}, HTTPStatus.BAD_REQUEST
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
        lookback_seconds, error = parse_query_int(qs, "lookback", SUMMARY_LOOKBACK_SECONDS, max_value=SUMMARY_LOOKBACK_SECONDS * 24)
        if error:
            self.write_json(error_payload(error, status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
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
        meta["summary_model"] = SUMMARY_CODEX_MODEL
        meta["summary_effort"] = SUMMARY_CODEX_EFFORT
        meta["summary_service_tier"] = SUMMARY_CODEX_SERVICE_TIER
        self.server.app.log_event(
            session,
            "summary_started",
            "AI summary started",
            {"lookback_seconds": lookback_seconds, "model": SUMMARY_CODEX_MODEL},
        )
        try:
            self.write_sse_json("meta", meta)
            self.run_codex_summary(prompt)
            self.server.app.log_event(session, "summary_finished", "AI summary finished", {"model": SUMMARY_CODEX_MODEL})
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            self.server.app.log_event(session, "summary_disconnected", "AI summary stream disconnected", {})
            return

    def run_codex_summary(self, prompt: str) -> None:
        repo_root = PROJECT_ROOT
        args = codex_exec_argv(ephemeral=True)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["NO_COLOR"] = "1"
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
            self.stream_codex_process(process)
        except OSError as exc:
            self.write_sse_json("summary_error", {"error": str(exc)})
        finally:
            if process is not None:
                terminate_process_group(process)

    def stream_codex_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            self.write_sse_json("summary_error", {"error": "missing Codex stdout"})
            return
        fd = process.stdout.fileno()
        buffer = ""
        last_ping = time.monotonic()
        deadline = time.monotonic() + SUMMARY_CODEX_TIMEOUT_SECONDS
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
                    buffer += chunk.decode("utf-8", errors="replace")
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
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def write_static_head(self, asset: str, content_type: str) -> None:
        path = static_asset_path(asset)
        if path is None:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_auth_cookie_if_needed()
        self.end_headers()

    def write_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
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
        session = parse_qs(parsed.query).get("session", [""])[0]
        share_sessions = self.share_sessions()
        if share_sessions and session not in share_sessions:
            self.write_text("share token is scoped to a different session\n", status=HTTPStatus.FORBIDDEN)
            return
        if session not in self.server.app.sessions:
            self.write_text(f"unknown session: {session}\n", status=HTTPStatus.NOT_FOUND)
            return
        if not self.accept_websocket():
            return
        self.bridge_tmux(session, readonly=self.auth_readonly())

    def accept_websocket(self) -> bool:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.write_text("missing Sec-WebSocket-Key\n", status=HTTPStatus.BAD_REQUEST)
            return False
        accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
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

    def bridge_tmux(self, session: str, readonly: bool = False) -> None:
        initial_rows, initial_cols, pending_payloads = self.read_initial_ws_payloads()
        if not readonly:
            self.server.record_host_pty_dimensions(session, initial_rows, initial_cols)
        master_fd, slave_fd = pty.openpty()
        set_pty_size(slave_fd, initial_rows, initial_cols)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        # the browser copy bridge needs tmux to FORWARD application OSC 52 clipboard escapes
        # (Claude's copy) to this client. tmux's default `set-clipboard external` IGNORES application
        # OSC 52 entirely (verified empirically on tmux 3.4: external drops it, on forwards it), so
        # ensure `on` before attaching. Idempotent, best-effort, self-healing across tmux restarts.
        subprocess.run(
            ["tmux", "set-option", "-s", "set-clipboard", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        attach_args = ["tmux", "attach-session"]
        if readonly:
            attach_args.append("-r")
        target = tmux_session_target(session)
        attach_args.extend(["-t", target])

        def session_exists() -> bool:
            return subprocess.run(
                ["tmux", "has-session", "-t", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0

        def attach_tmux() -> subprocess.Popen:
            return subprocess.Popen(
                attach_args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=env,
                start_new_session=True,
            )

        process = attach_tmux()

        try:
            for payload in pending_payloads:
                self.handle_ws_payload(session, master_fd, slave_fd, process, payload, readonly=readonly)
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
                        self.handle_ws_payload(session, master_fd, slave_fd, process, payload, readonly=readonly)
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
            try:
                os.close(master_fd)
            except OSError:
                pass
            try:
                os.close(slave_fd)
            except OSError:
                pass
            if process.poll() is None:
                terminate_process_group(process)

    def bridge_shared_tmux(self, token: str, session: str, viewer_id: str = "") -> None:
        viewer = ShareViewerConnection(self.connection, viewer_id)
        upstream = self.server.share_terminal_upstream(token, session)
        upstream.add_viewer(viewer)
        writer = threading.Thread(target=viewer.write_loop, name=f"share-viewer-{session}", daemon=True)
        writer.start()
        try:
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
        self.bridge_share_ui_socket(token, client_id, receive_only=not write_enabled, viewer_id=registered_viewer_id)

    def handle_share_ui_message(self, token: str, message: dict[str, Any], client_id: str = "") -> None:
        if not isinstance(message, dict):
            return
        msg_type = str(message.get("type") or "")
        if not msg_type:
            return
        data = message.get("payload")
        payload = data if isinstance(data, dict) else {}
        sender = str(message.get("sender") or client_id or "")
        if msg_type == "pointer":
            self.server.queue_share_pointer(token, payload, sender=sender)
            return
        updater = getattr(self.server.app, "update_share_record_ui_state", None)
        if callable(updater):
            if msg_type == "layout":
                updater(token, payload)
            elif msg_type == "ui-state":
                updater(token, {
                    "uiState": payload,
                    "finder": payload.get("finder", {}),
                    "layout": payload.get("layout", ""),
                    "tabs": payload.get("tabs", ""),
                })
            elif msg_type in {"viewport", "appearance"}:
                updater(token, {"uiStatePatch": {msg_type: payload}})
            elif msg_type == "scroll":
                updater(token, {"uiStateScroll": payload})
        self.server.broadcast_share_ui(
            token,
            {"type": msg_type, "payload": payload, "sender": sender},
            skip_client_id=sender,
        )

    def bridge_share_ui_socket(self, token: str, client_id: str = "", receive_only: bool = False, viewer_id: str = "") -> None:
        clean_client_id = str(client_id or "")
        client = ShareViewerConnection(self.connection, clean_client_id)
        self.server.register_share_ui_client(token, client)
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
                if receive_only:
                    continue
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                self.handle_share_ui_message(token, message, clean_client_id)
        except (BrokenPipeError, ConnectionError, ConnectionResetError, OSError):
            pass
        finally:
            client.close("ui-closed")
            self.server.release_share_ui_client(token, client)
            if viewer_id:
                self.server.app.unregister_share_viewer(token, viewer_id)
                self.server.broadcast_share_status(token)
            writer.join(timeout=1.0)

    def read_initial_ws_payloads(self) -> tuple[int, int, list[bytes]]:
        rows = DEFAULT_ROWS
        cols = DEFAULT_COLS
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
                dimensions = ws_resize_dimensions(message, rows, cols)
                if dimensions:
                    rows, cols = dimensions
                continue
            pending_payloads.append(payload)
            break
        return rows, cols, pending_payloads

    def read_ws_frame_with_timeout(self) -> tuple[int, bytes]:
        previous_timeout = self.connection.gettimeout()
        self.connection.settimeout(WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS)
        try:
            return read_ws_frame(self.rfile)
        except TimeoutError as exc:
            raise ConnectionError("websocket frame read timed out") from exc
        finally:
            self.connection.settimeout(previous_timeout)

    def handle_ws_payload(self, session: str, master_fd: int, resize_fd: int, process: subprocess.Popen[Any], payload: bytes, readonly: bool = False) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            if readonly:
                return
            os.write(master_fd, payload)
            return
        msg_type = message.get("type")
        if msg_type == "input":
            if readonly:
                return
            data = message.get("data")
            if isinstance(data, str):
                filtered = strip_terminal_query_responses(data)
                if filtered:
                    os.write(master_fd, filtered.encode("utf-8"))
                    # DOIT.58 Phase 1: one user-input heartbeat (readonly already returned above).
                    self.server.app.record_user_input(session, len(filtered))
        elif msg_type == "resize":
            if readonly:
                return
            dimensions = ws_resize_dimensions(message, DEFAULT_ROWS, DEFAULT_COLS)
            if dimensions:
                rows, cols = dimensions
                set_pty_size(resize_fd, rows, cols)
                recorder = getattr(self.server, "record_host_pty_dimensions", None)
                if callable(recorder):
                    recorder(session, rows, cols)
                try:
                    os.killpg(process.pid, signal.SIGWINCH)
                except OSError:
                    pass
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
        self.share_pointer_lock = threading.Lock()
        self.share_pointer_latest: dict[str, dict[str, Any]] = {}
        self.share_pointer_clicks: dict[str, list[dict[str, Any]]] = {}
        self.share_pointer_threads: dict[str, threading.Thread] = {}
        self.share_pointer_stop = threading.Event()
        if hasattr(self.app, "start_client_event_watcher"):
            self.app.start_client_event_watcher()
        if hasattr(self.app, "start_update_check_thread"):
            self.app.start_update_check_thread()

    def server_close(self) -> None:
        self.share_pointer_stop.set()
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
                "type": "host-resize",
                "payload": {"session": clean_session, "rows": dimensions[0], "cols": dimensions[1]},
            })
        for _token, upstream in upstream_entries:
            upstream.request_refresh_client()

    def host_pty_dimensions_for_session(self, session: str) -> tuple[int, int]:
        with self.host_pty_dimensions_lock:
            return self.host_pty_dimensions.get(str(session or ""), (DEFAULT_ROWS, DEFAULT_COLS))

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
                    {"type": "pointer", "payload": payload, "sender": sender},
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
                status = viewer.enqueue(frame)
                if status == "too-slow":
                    viewer.close("too-slow")
        for client in clients:
            if skip_client_id and client.client_id == skip_client_id:
                continue
            status = client.enqueue(frame)
            if status == "too-slow":
                client.close("too-slow")

    def broadcast_share_status(self, token: str) -> None:
        payload_builder = getattr(self.app, "share_status_frame_payload", None)
        payload = payload_builder(token) if callable(payload_builder) else None
        if not payload:
            return
        self.broadcast_share_ui(token, {"type": "share-status", "payload": payload})

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

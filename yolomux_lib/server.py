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
MAX_FS_BATCH_REQUESTS = 64
TOKEN_LOG_RE = re.compile(r"([?&]token=)[^&\s\"]+")


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


class Handler(AuthMixin, BaseHTTPRequestHandler):
    server: "TmuxWebtermHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        message = TOKEN_LOG_RE.sub(r"\1[redacted]", fmt % args)
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), message))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
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
        required_role = "admin" if parsed.path == "/api/summary-stream" else "readonly"
        if not self.require_auth(required_role):
            return
        # blame reads repository file history, so it is admin-only like the rest of the
        # file/repo API (the /api/fs/* reads) — a readonly identity must not read file content/history.
        if (parsed.path.startswith("/api/fs/") or parsed.path == "/api/blame") and self.auth_readonly():
            self.reject_forbidden(self.auth_identity(), "admin")
            return
        if parsed.path == "/api/ping":
            self.write_json({"ok": True, "time": time.time()})
            return
        if parsed.path == "/api/dev-reload":
            # Dev-velocity #1b: an SSE stream that emits the static-bundle signature whenever it changes,
            # so a dev page reloads itself on rebuild (ends the "is the bundle stale?" misdiagnoses). Only
            # active under --dev; a 404 otherwise so production never exposes it.
            if not getattr(self.server, "dev", False):
                self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self.stream_dev_reload()
            return
        if parsed.path == "/api/client-events":
            self.stream_client_events()
            return
        if parsed.path == "/":
            sessions = [self.share_session()] if self.share_session() else self.server.app.sessions
            self.write_html(html_page(sessions, self.auth_identity().role, dev=getattr(self.server, 'dev', False), dangerously_yolo=self.server.app.dangerously_yolo))
            return
        if parsed.path == "/preview-popout":
            self.handle_preview_popout_placeholder(parsed)
            return
        if parsed.path == "/api/transcripts":
            qs = parse_qs(parsed.query)
            self.write_json(self.server.app.transcripts_payload(force=query_bool(qs, "force")))
            return
        if parsed.path == "/api/activity-summary":
            qs = parse_qs(parsed.query)
            self.write_json(self.server.app.activity_summary_payload(
                force=query_bool(qs, "force"),
                locale=str(query_one(qs, "locale", "en") or "en"),
            ))
            return
        if parsed.path == "/api/tmux":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_validated_int_result(qs, "lines", 90, MAX_TRANSCRIPT_TAIL_LINES, lambda lines: self.server.app.tmux_snapshot(session, lines))
            return
        if parsed.path == "/api/transcript":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_validated_int_result(qs, "lines", 120, MAX_TRANSCRIPT_TAIL_LINES, lambda lines: self.server.app.transcript_tail(session, lines))
            return
        if parsed.path == "/api/context":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_validated_int_result(qs, "messages", 40, MAX_COMPACT_TRANSCRIPT_ITEMS, lambda messages: self.server.app.context_tail(session, messages))
            return
        if parsed.path == "/api/context-items":
            qs = parse_qs(parsed.query)
            session = str(query_one(qs, "session", "") or "")
            self.write_validated_int_result(qs, "messages", 40, MAX_COMPACT_TRANSCRIPT_ITEMS, lambda messages: self.server.app.context_items(session, messages))
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
        if parsed.path == "/api/watched-prs":
            self.write_json(self.server.app.watched_prs_payload())
            return
        if parsed.path == "/api/yolo-rules":
            self.write_json(self.server.app.yolo_rules_payload())
            return
        if parsed.path == "/api/events":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            self.write_validated_int_result(qs, "limit", 100, MAX_EVENT_TAIL_LINES, lambda limit: self.server.app.events_payload(session, limit))
            return
        if parsed.path == "/api/search":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            query = str(query_one(qs, "q", "") or "")
            self.write_validated_int_result(qs, "limit", 100, MAX_EVENT_TAIL_LINES, lambda limit: self.server.app.search_payload(query, session, limit))
            return
        if parsed.path == "/api/run-history":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            self.write_app_result(self.server.app.run_history_payload(session))
            return
        if parsed.path == "/api/activity":
            # DOIT.58 Phase 1: per-session/window activity ledger (metadata; readonly-allowed).
            self.write_app_result(self.server.app.activity_payload())
            return
        if parsed.path == "/api/session-files":
            qs = parse_qs(parsed.query)
            session = query_one(qs, "session", None)
            hours, error = parse_query_float(qs, "hours", 24.0, max_value=24.0 * 365.0)
            if error:
                self.write_json({"error": error}, status=HTTPStatus.BAD_REQUEST)
                return
            from_ref = query_one(qs, "from", None)
            to_ref = query_one(qs, "to", None)
            force = query_bool(qs, "force")
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
        if parsed.path == "/ws":
            self.websocket(parsed)
            return
        self.write_text("not found\n", status=HTTPStatus.NOT_FOUND)

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
            self.write_json({"error": str(exc), "path": raw_path}, status=HTTPStatus(exc.status))
            return
        self.write_json(payload)

    def handle_fs_raw(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        download = query_bool(qs, "download")
        try:
            data, mime = filesystem.read_raw(raw_path)
        except FilesystemError as exc:
            self.write_json({"error": str(exc), "path": raw_path}, status=HTTPStatus(exc.status))
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
            self.write_json({"error": "path must be an HTML file", "path": raw_path}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = filesystem.read_file(raw_path)
        except FilesystemError as exc:
            self.write_json({"error": str(exc), "path": raw_path}, status=HTTPStatus(exc.status))
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
            self.write_json({"error": "missing or invalid Content-Length"}, status=HTTPStatus.LENGTH_REQUIRED)
            return None
        if length <= 0 or length > max_length:
            self.write_json({"error": "content too large"}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        try:
            body = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            self.write_json({"error": "request body must be utf-8 JSON"}, status=HTTPStatus.BAD_REQUEST)
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            self.write_json({"error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(payload, dict):
            self.write_json({"error": "request body must be a JSON object"}, status=HTTPStatus.BAD_REQUEST)
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
                self.write_json({"error": "expected_mtime must be an integer"}, status=HTTPStatus.BAD_REQUEST)
                return
        if yolo_rules.is_rules_file_path(raw_path):
            try:
                yolo_rules.validate_rule_file_text(str(content), path=yolo_rules.active_rule_path())
            except (ValueError, yaml.YAMLError) as exc:
                self.write_json({"error": f"YOLO rules invalid: {exc}", "path": raw_path}, status=HTTPStatus.BAD_REQUEST)
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
        if parsed.path == "/login":
            self.handle_login_submit(parsed)
            return
        if not self.require_auth_for_post(parsed.path):
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
        if parsed.path == "/api/watch/roots":
            payload = self.read_json_body(64 * 1024)
            if payload is None:
                return
            self.write_json(self.server.app.update_client_watch_roots(payload))
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
        self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def request_base_url(self) -> str:
        host = str(self.headers.get("Host") or self.server.server_name_with_port()).strip()
        if not host or "\r" in host or "\n" in host:
            host = self.server.server_name_with_port()
        scheme = "https" if self.request_is_https() else "http"
        return f"{scheme}://{host}"

    def handle_share_create(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        session = str(payload.get("session") or "").strip()
        ttl_seconds = payload.get("ttl_seconds", payload.get("ttl", None))
        layout = str(payload.get("layout") or "")
        tabs = str(payload.get("tabs") or "")
        return self.server.app.create_share_token(
            session,
            ttl_seconds,
            base_url=self.request_base_url(),
            created_by=self.auth_identity().username,
            layout=layout,
            tabs=tabs,
        )

    def handle_fs_batch(self, parsed: Any) -> None:
        payload = self.read_json_body(64 * 1024)
        if payload is None:
            return
        requests = payload.get("requests", [])
        if not isinstance(requests, list):
            self.write_json({"error": "requests must be a list"}, status=HTTPStatus.BAD_REQUEST)
            return
        if len(requests) > MAX_FS_BATCH_REQUESTS:
            self.write_json({"error": f"requests must contain at most {MAX_FS_BATCH_REQUESTS} items"}, status=HTTPStatus.BAD_REQUEST)
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
            return {"error": "missing Content-Length"}, HTTPStatus.LENGTH_REQUIRED
        try:
            content_length = int(content_length_text)
        except ValueError:
            return {"error": "invalid Content-Length"}, HTTPStatus.BAD_REQUEST
        # reject a non-positive length — `read(-1)` blocks until disconnect and `read(-5)`
        # raises (-> 500); only the upper bound was checked.
        if content_length <= 0:
            return {"error": "invalid Content-Length"}, HTTPStatus.BAD_REQUEST
        if content_length > 64 * 1024:
            self.close_connection = True
            return {"error": "event is too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        body = self.rfile.read(content_length)
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"error": f"invalid JSON: {exc}"}, HTTPStatus.BAD_REQUEST
        if not isinstance(event, dict):
            return {"error": "event must be an object"}, HTTPStatus.BAD_REQUEST
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
            self.write_json({"error": error}, status=HTTPStatus.BAD_REQUEST)
            return
        message_limit = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        payload, status = self.server.app.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            self.write_json(payload, status=status)
            return
        path_text = payload.get("path")
        text = payload.get("text")
        if not isinstance(path_text, str) or not isinstance(text, str):
            self.write_json({"session": session, "error": "missing transcript text"}, status=HTTPStatus.NOT_FOUND)
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
            self.write_json({"error": error}, status=HTTPStatus.BAD_REQUEST)
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
            self.write_json({"error": error}, status=HTTPStatus.BAD_REQUEST)
            return
        self.write_app_result(make_result(value))

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
        share_session = self.share_session()
        if share_session and session != share_session:
            self.write_text("share token is scoped to a different session\n", status=HTTPStatus.FORBIDDEN)
            return
        if session not in self.server.app.sessions:
            self.write_text(f"unknown session: {session}\n", status=HTTPStatus.NOT_FOUND)
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.write_text("missing Sec-WebSocket-Key\n", status=HTTPStatus.BAD_REQUEST)
            return
        accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.send_auth_cookie_if_needed()
        self.end_headers()
        self.bridge_tmux(session, readonly=self.auth_readonly())

    def bridge_tmux(self, session: str, readonly: bool = False) -> None:
        initial_rows, initial_cols, pending_payloads = self.read_initial_ws_payloads()
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
    tls_peek_timeout_seconds = 0.05

    def __init__(self, server_address: tuple[str, int], app: TmuxWebtermApp, tls_context: ssl.SSLContext | None = None, dev: bool = False):
        super().__init__(server_address, Handler)
        self.app = app
        self.tls_context = tls_context
        self.dev = dev  # dev-velocity #1b: enables the /api/dev-reload SSE channel + the bootstrap dev flag
        if hasattr(self.app, "start_client_event_watcher"):
            self.app.start_client_event_watcher()

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, client_address = self.socket.accept()
        if not self.tls_context:
            return request, client_address
        request.settimeout(self.tls_peek_timeout_seconds)
        try:
            first = request.recv(1, socket.MSG_PEEK)
        except (socket.timeout, BlockingIOError):
            # Idle preconnect socket — client hasn't sent yet. Wrap as TLS and
            # defer the handshake to the worker thread; closing here would
            # surface as ERR_CONNECTION_CLOSED in remote browsers.
            request.settimeout(None)
            return self.tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False), client_address
        if first and first[0] in TLS_FIRST_BYTES:
            request.settimeout(None)
            return self.tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False), client_address
        try:
            request_bytes = request.recv(4096)
            response = https_redirect_response(request_bytes, self.server_name_with_port())
            request.sendall(response)
        except (OSError, ssl.SSLError):
            pass
        finally:
            request.close()
        raise OSError("redirected plain HTTP request to HTTPS")

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

from __future__ import annotations

import base64
import codecs
import copy
from functools import lru_cache
import gzip
import hashlib
import hmac
import html
import json
import logging
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
import uuid
from collections.abc import Callable
from datetime import datetime
from email.errors import FirstHeaderLineIsContinuationDefect
from email.errors import InvalidHeaderDefect
from email.errors import MisplacedEnvelopeHeaderDefect
from email.errors import MissingHeaderBodySeparatorDefect
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlparse

import yaml

from . import filesystem
from .approval import yolo_rules
from .app import TmuxWebtermApp
from .common import DEFAULT_COLS
from .common import DEFAULT_ROWS
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import PROJECT_ROOT
from .common import PACIFIC_TIME
from .common import ProductMetadata
from .common import UPLOAD_MAX_BYTES
from .common import WEBSOCKET_GUID
from .common import codex_event_kind
from .common import codex_exec_argv
from .common import codex_runtime_env
from .common import error_payload
from .common import product_filename
from .common import ready_response_envelope_bytes
from .common import terminate_process_group
from .common import validated_product_metadata
from .common import yolomux_dev_bundle_revision
from .http_routes import dispatch_http_route
from .http_routes import parse_query_float
from .http_routes import parse_query_int
from .http_routes import parse_repo_refs_param  # noqa: F401 - compatibility re-export
from .http_routes import query_bool
from .http_routes import query_one
from .http_routes import route_for_request
from .http_routes import RESPONSE_JSON
from .http_routes import RESPONSE_JSON_BATCH
from .http_routes import RESPONSE_BINARY
from .local_services.runtime import local_service_exception_cause
from .local_services.client import local_service_failure_is_transient
from .tmux.tmux_utils import tmux
from .tmux.tmux_utils import tmux_command
from .tmux.tmux_utils import tmux_session_client_rows
from .tmux.tmux_utils import tmux_session_target
from .tmux.process_group_ownership import record_owned_process_group
from .tmux.process_group_ownership import signal_recorded_process_group
from .observability.failure_severity import failure_record_level
from .observability.transcripts import codex_event_text
from .observability.transcripts import strip_terminal_query_responses
from .observability.transcripts import transcript_items_from_raw_line
from .uploads import parse_multipart_upload
from .server_auth import AuthMixin
from .settings import SUMMARY_DEFAULT_CODEX_TIMEOUT_SECONDS
from .server_logs import emit_server_log
from .settings import SUMMARY_DEFAULT_LOOKBACK_SECONDS
from .stats_current import http as stats_current_http
from .stats_current import protocol as stats_current_protocol
from .locales import resolve_locale_preference
from .locales import user_message_payload
from .web import html_page
from .web import html_lang_dir_attrs
from .web import MOBILE_VIEWPORT_META
from .web import server_string
from .web import static_asset_path
from .web import static_content_type
from .websocket import make_ws_frame
from .websocket import read_ws_frame
from .websocket import set_pty_size
from .websocket import wait_for_ws_frame
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_entry_available
from .workdir import agent_auth_status
from .workdir import start_agent_auth_status_refresh


logger = logging.getLogger(__name__)

PTY_DIMENSION_MIN = 1
PTY_DIMENSION_MAX = 1000
WEBSOCKET_FRAME_READ_TIMEOUT_SECONDS = 5.0
RESIZE_AUTHORITY_CLIENT_ID_MAX = 128
DEV_RELOAD_POLL_SECONDS = 2.0
CLIENT_EVENT_HEARTBEAT_SECONDS = 15.0
CLIENT_EVENT_DISCONNECT_POLL_SECONDS = 1.0
TMUX_ATTACH_REFRESH_DELAYS_SECONDS = (0.1, 0.5)
MAX_FS_BATCH_REQUESTS = filesystem.MAX_BATCH_REQUESTS
TOKEN_LOG_RE = re.compile(r"([?&](?:token|client_id)=)[^&\s\"]+")
HTTP_HEADER_NAME_RE = re.compile(r"[-!#$%&'*+.^_`|~0-9A-Za-z]+")
STATIC_CACHE_CONTROL_VERSIONED = "public, max-age=31536000, immutable"
STATIC_CACHE_CONTROL_UNVERSIONED = "no-store"
HTTP_REQUEST_LINE_CAPTURE_LIMIT = 1024 * 1024
HTTP_REQUEST_BODY_INACTIVITY_TIMEOUT_SECONDS = 2.0
HTTP_MAX_DECLARED_BODY_BYTES = (1 << 63) - 1
RESPONSE_GZIP_MIN_BYTES = 1024
STATIC_GZIP_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "text/css",
    "text/html",
    "text/plain",
}
FS_ZIP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def content_disposition_attachment(raw_path: str) -> str:
    name = Path(str(raw_path or "")).name or "download"
    safe = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";", "/"} else "_" for char in name).strip()
    return f'attachment; filename="{safe or "download"}"'


def fs_zip_attachment_filename(raw_path: str) -> str:
    name = Path(os.path.expanduser(str(raw_path or ""))).name or "folder"
    stamp = datetime.now(PACIFIC_TIME).strftime(FS_ZIP_TIMESTAMP_FORMAT)
    return f"{name}.{stamp}.zip"


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


def gzip_response_body(data: bytes, content_type: str, accept_encoding: str | None) -> tuple[bytes, str | None]:
    if (
        len(data) >= RESPONSE_GZIP_MIN_BYTES
        and static_content_type_supports_gzip(content_type)
        and accept_encoding_allows_gzip(accept_encoding)
    ):
        return gzip.compress(data, compresslevel=6, mtime=0), "gzip"
    return data, None


def static_asset_response_body(data: bytes, content_type: str, accept_encoding: str | None) -> tuple[bytes, str | None]:
    return gzip_response_body(data, content_type, accept_encoding)


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


@lru_cache(maxsize=1)
def tmux_supports_ignore_size_flag() -> bool:
    """Whether this tmux accepts client flags on attach-session.

    tmux 1.9a, still shipped on the ITSS VDI image, predates both
    ``attach-session -f`` and the ``ignore-size`` client flag. Probe against a
    deliberately nonexistent target: supported tmux versions reject only the
    target, while older versions explicitly reject the option or flag.
    """
    result = tmux([
        "attach-session",
        "-f",
        "ignore-size",
        "-t",
        "__yolomux_ignore_size_capability_probe__",
    ], timeout=1.0)
    output = f"{result.stdout}\n{result.stderr}".lower()
    return "unknown option" not in output and "unknown flag" not in output


def tmux_attach_command(readonly: bool = False) -> list[str]:
    args = tmux_command(["attach-session"])
    if readonly:
        args.append("-r")
    if tmux_supports_ignore_size_flag():
        args.extend(["-f", "ignore-size"])
    return args


def resize_pty_and_signal_process(fd: int, process: subprocess.Popen[Any] | None, rows: int, cols: int) -> None:
    set_pty_size(fd, rows, cols)
    if process is not None and process.poll() is None:
        outcome = signal_recorded_process_group(process, signal.SIGWINCH)
        if not outcome["signalled"] and outcome["reason"] != "nothing_to_kill":
            logger.warning("refused SIGWINCH for process group %s: %s", process.pid, outcome["reason"])


def tmux_client_name_for_fd(fd: int) -> str:
    try:
        return os.ttyname(fd)
    except OSError:
        return ""


def tmux_client_has_flag(row: dict[str, Any], flag: str) -> bool:
    return flag in {item.strip() for item in str(row.get("flags") or "").split(",") if item.strip()}


def refresh_tmux_client_ignore_size(client_name: str, ignore_size: bool) -> bool:
    if not client_name or not tmux_supports_ignore_size_flag():
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


def claim_tmux_resize_authority(
    session: str,
    client_name: str,
    active_cols: int | None = None,
    active_rows: int | None = None,
) -> bool:
    """Make `client_name` the size authority for `session`.

    Called when a browser surface activates a pane. Under `window-size largest` the shared window
    follows the largest non-`ignore-size` client. A wider OR taller sibling therefore makes the
    focused surface overflow or leaves a short screen inside a tall viewport. Flag clients that
    exceed either active dimension so the window converges to the foreground surface.
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
    height = active_rows if isinstance(active_rows, int) and active_rows > 0 else int(current.get("height") or 0)
    active_ignored = tmux_client_has_flag(current, "ignore-size")
    conflicting = [
        row for row in rows
        if str(row.get("name") or "") != clean_client_name
        and not tmux_client_has_flag(row, "ignore-size")
        and (int(row.get("width") or 0) > width or int(row.get("height") or 0) > height)
    ]
    if not active_ignored and not conflicting:
        return False
    changed = False
    if active_ignored:
        changed = refresh_tmux_client_ignore_size(clean_client_name, False) or changed
    for row in conflicting:
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
    commands = [
        ["set-option", "-s", "set-clipboard", "on"],
        ["set-option", "-wg", "aggressive-resize", "on"],
    ]
    if tmux_supports_ignore_size_flag():
        commands.insert(1, ["set-option", "-t", target, "window-size", "largest"])
    for args in commands:
        tmux(args)


class _HandlerAdapter:
    """Composition seam that keeps request state owned by the HTTP handler."""

    def __init__(self, handler: Any) -> None:
        object.__setattr__(self, "_handler", handler)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handler, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._handler, name, value)


class FilesystemHttpAdapter(_HandlerAdapter):
    """Composed owner for FilesystemHttpAdapter."""

    def handle_fs_fast_list(self, parsed: Any) -> None:
        """Return one non-recursive directory snapshot without entering jobd."""
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "/") or "/")
        try:
            payload = filesystem.list_directory(raw_path, include_repo_info=False)
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))
            return
        self.write_json(payload, status=HTTPStatus.OK)

    def handle_fs_list(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "/") or "/")
        try:
            self.write_json(filesystem.list_directory(raw_path), status=HTTPStatus.OK)
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def handle_fs_search(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_root = str(query_one(qs, "root", query_one(qs, "path", "/")) or "/")
        query = str(query_one(qs, "query", "") or "")
        limit = str(query_one(qs, "limit", "400") or "400")
        recursive = query_bool(qs, "recursive")
        if not recursive and not query_one(qs, "cursor", ""):
            try:
                self.write_json(
                    filesystem.search_files(
                        raw_root,
                        query=query,
                        limit=limit,
                        recursive=False,
                        direct_only=True,
                    ),
                    status=HTTPStatus.OK,
                )
            except filesystem.FilesystemError as error:
                self.write_json(error.payload(), status=HTTPStatus(error.status))
            return
        # Recursive work and cursor deltas are batch work. They use the existing filesystem-operation
        # descriptor so the authenticated read path, safe-root containment, and exclusion policy are
        # identical to the other batch operations.
        cursor = str(query_one(qs, "cursor", "") or "")
        self.submit_filesystem_operation(
            "GET /api/batch/search",
            "search",
            raw_root,
            {"query": query, "limit": limit, "recursive": recursive, "cursor": cursor},
        )

    def handle_batch_search(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_root = str(query_one(qs, "root", query_one(qs, "path", "/")) or "/")
        query = str(query_one(qs, "query", "") or "")
        limit = str(query_one(qs, "limit", "400") or "400")
        cursor = str(query_one(qs, "cursor", "") or "")
        self.submit_filesystem_operation(
            "GET /api/batch/search",
            "search",
            raw_root,
            {"query": query, "limit": limit, "recursive": True, "cursor": cursor, "indexed_only": True},
        )

    def handle_fs_index_status(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_root = str(query_one(qs, "root", query_one(qs, "path", "/")) or "/")
        try:
            self.write_json(filesystem.index_status(raw_root), status=HTTPStatus.OK)
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def handle_fs_read(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        if query_bool(qs, "include_git"):
            self.submit_filesystem_operation("GET /api/fs/read", "read", raw_path, {"include_git": True})
            return
        # A first editor open needs only one authorized descriptor walk, stat, bounded read, and
        # binary check. Keep that small operation in this request thread instead of sending it
        # through jobd's point queue and receipt polling. Git is explicitly deferred to the
        # include_git path because repository snapshots can be arbitrarily slow.
        try:
            payload = filesystem.read_file(raw_path, include_git=False)
        except filesystem.FilesystemError as error:
            payload = error.payload()
            # Preserve the long-standing file-open contract. The generic HTTP envelope maps a
            # bare 404 to ``not_found``; callers use this finer code to keep an expected missing
            # file distinct from a transport failure.
            if error.status == HTTPStatus.NOT_FOUND:
                payload["error_code"] = "path_not_found"
                payload["path"] = raw_path
            self.write_json(payload, status=HTTPStatus(error.status))
            return
        self.write_json(payload, status=HTTPStatus.OK)

    def handle_fs_info(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        include_git = query_bool(qs, "include_git")
        if include_git:
            self.submit_filesystem_operation("GET /api/fs/info", "info", raw_path, {"include_git": True})
            return
        try:
            self.write_json(filesystem.path_info(raw_path, include_git=include_git), status=HTTPStatus.OK)
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def handle_fs_diff(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        from_ref = query_one(qs, "from", None)
        to_ref = query_one(qs, "to", None)
        try:
            self.write_json(
                filesystem.diff_file(raw_path, from_ref=from_ref, to_ref=to_ref),
                status=HTTPStatus.OK,
            )
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def handle_fs_git_history(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        limit, error = parse_query_int(qs, "limit", 40, max_value=40, clamp_min=True)
        if error:
            self.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
            return
        cursor = str(query_one(qs, "cursor", "") or "")
        try:
            self.write_json(
                filesystem.git_history(raw_path, limit=limit, cursor=cursor or None),
                status=HTTPStatus.OK,
            )
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def handle_fs_git_commit(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        commit = str(query_one(qs, "commit", "") or "")
        head = str(query_one(qs, "head", "") or "")
        try:
            self.write_json(
                filesystem.git_commit(raw_path, commit=commit, head=head),
                status=HTTPStatus.OK,
            )
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def handle_blame(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        ref = query_one(qs, "ref", None)
        self.submit_filesystem_operation("GET /api/blame", "blame", raw_path, {"ref": ref})

    def submit_filesystem_operation(
        self,
        route: str,
        operation: str,
        raw_path: str,
        args: dict[str, Any] | None = None,
        *,
        reload_yolo_rules: bool = False,
    ) -> None:
        """Accept one serializable filesystem descriptor without invoking it in the web thread."""
        identity = self.auth_identity()
        scope = f"user:{identity.role}:{identity.username}"
        response = self.server.app.filesystem_operation_http_payload(
            route=route,
            operation=operation,
            path=raw_path,
            args=args,
            reload_yolo_rules=reload_yolo_rules,
            scope=scope,
        )
        if response.product is not None:
            self.write_product_bytes(response.body, response.product)
            return
        self.write_json(response.payload, status=response.status)

    def handle_fs_raw(self, parsed: Any) -> None:
        """Serve one bounded authorized file directly; raw media must not wait behind jobd."""
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        download = query_bool(qs, "download")
        try:
            body, content_type = filesystem.read_raw(raw_path, max_bytes=self.file_transfer_max_bytes())
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))
            return
        disposition = "attachment" if download else "inline"
        self.write_product_bytes(body, {
            "format": "opaque_bytes",
            "content_type": content_type,
            "length": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "disposition": disposition,
            "filename": product_filename(Path(raw_path).name, fallback="download") if disposition == "attachment" else "",
        })

    def handle_fs_zip(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        self.submit_filesystem_relay(
            "GET /api/fs/zip",
            "zip",
            raw_path,
            {"filename": fs_zip_attachment_filename(raw_path), "max_bytes": self.file_transfer_max_bytes()},
        )

    def handle_fs_count(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = str(query_one(qs, "path", "") or "")
        self.submit_filesystem_operation("GET /api/fs/count", "count", raw_path)

    def handle_fs_html_preview(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        if not raw_path.lower().endswith((".html", ".htm")):
            self.write_json(
                error_payload(
                    "path must be an HTML file",
                    message_key="fs.error.htmlFileRequired",
                    message_params={"path": raw_path},
                    path=raw_path,
                    status=HTTPStatus.BAD_REQUEST,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        locale = resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", ""))
        self.submit_filesystem_relay("GET /api/fs/html-preview", "html_preview", raw_path, {"locale": locale})

    def submit_filesystem_relay(self, route: str, operation: str, raw_path: str, args: dict[str, Any]) -> None:
        """Forward browser-owned filesystem bytes through the shared product writer."""
        response = self.server.app.filesystem_operation_relay(
            route=route,
            operation=operation,
            path=raw_path,
            args=args,
        )
        if response.transfer is not None:
            self.api_response_writer.write_product_stream(response.transfer)
            return
        if response.product is not None:
            self.write_product_bytes(response.body, response.product)
            return
        self.write_json(response.payload, status=response.status)

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
            self.request_body_consumed = True
            return b"", None, HTTPStatus.OK
        cached_length = getattr(self, "_request_content_length", None)
        cached_length_present = bool(getattr(self, "_request_content_length_present", False))
        try:
            length = cached_length if cached_length_present and isinstance(cached_length, int) else int(length_text)
        except (TypeError, ValueError):
            missing = not length_text
            status = missing_status if missing else invalid_status
            return None, error_payload(
                missing_message if missing else invalid_message,
                message_key="request.error.contentLengthRequired" if missing else "request.error.contentLengthInvalid",
                status=status,
            ), status
        if length < 0 or (length == 0 and not allow_empty):
            return None, error_payload(
                empty_message,
                message_key="request.error.contentLengthInvalid",
                status=empty_status,
            ), empty_status
        if length > max_length:
            if close_on_too_large:
                self.close_connection = True
            return None, error_payload(
                too_large_message,
                message_key="request.error.contentTooLarge",
                message_params={"max": max_length},
                status=too_large_status,
            ), too_large_status
        connection = getattr(self, "connection", None)
        gettimeout = getattr(connection, "gettimeout", None)
        settimeout = getattr(connection, "settimeout", None)
        previous_timeout = gettimeout() if callable(gettimeout) else None
        try:
            if callable(settimeout):
                settimeout(HTTP_REQUEST_BODY_INACTIVITY_TIMEOUT_SECONDS)
            body = self.rfile.read(length)
        except TimeoutError:
            self.close_connection = True
            return None, error_payload(
                "request body read timed out",
                status=HTTPStatus.REQUEST_TIMEOUT,
            ), HTTPStatus.REQUEST_TIMEOUT
        finally:
            if callable(settimeout):
                settimeout(previous_timeout)
        if len(body) != length:
            self.close_connection = True
            return None, error_payload(
                "incomplete request body",
                status=HTTPStatus.BAD_REQUEST,
            ), HTTPStatus.BAD_REQUEST
        # This is the only place a declared request body leaves the socket, so it is the only place
        # that may report the connection re-framed for the next request.
        self.request_body_consumed = True
        return body, None, HTTPStatus.OK

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
        marker = self.measurement_marker()
        if marker and body is not None:
            self._http_request_body_bytes = len(body)
            self._http_request_body_identity_v1 = hmac.new(
                marker.encode("ascii"),
                b"yolomux.capture.request-body.v1\0" + body,
                hashlib.sha256,
            ).hexdigest()[:32]
        if body == b"" and (allow_empty or allow_missing):
            return {}
        try:
            text = (body or b"").decode("utf-8")
        except UnicodeDecodeError as exc:
            self.write_json(
                error_payload(
                    "request body must be utf-8 JSON",
                    message_key="request.error.jsonUtf8",
                    diagnostic=exc,
                    status=HTTPStatus.BAD_REQUEST,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self.write_json(
                error_payload(
                    "invalid JSON",
                    message_key="request.error.invalidJson",
                    diagnostic=exc,
                    status=HTTPStatus.BAD_REQUEST,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return None
        if not isinstance(payload, dict):
            self.write_json(
                error_payload(
                    "request body must be a JSON object",
                    message_key="request.error.jsonObject",
                    status=HTTPStatus.BAD_REQUEST,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
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
                self.write_json(
                    error_payload(
                        "expected_mtime must be an integer",
                        message_key="request.error.integer",
                        message_params={"field": "expected_mtime"},
                        status=HTTPStatus.BAD_REQUEST,
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
        try:
            rules_file_path = yolo_rules.is_rules_file_path(raw_path)
        except OSError as exc:
            self.write_json(
                error_payload(
                    "YOLO rules path is unavailable",
                    message_key="yolo.error.invalidRules",
                    diagnostic=exc,
                    path=raw_path,
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                ),
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        if rules_file_path:
            try:
                yolo_rules.validate_rule_file_text(str(content), path=yolo_rules.active_rule_path())
            except (ValueError, yaml.YAMLError) as exc:
                self.write_json(
                    error_payload(
                        "YOLO rules are invalid",
                        message_key="yolo.error.invalidRules",
                        diagnostic=exc,
                        path=raw_path,
                        status=HTTPStatus.BAD_REQUEST,
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self.submit_filesystem_operation(
                "POST /api/fs/write",
                "write",
                raw_path,
                {"content": content, "expected_mtime": expected_mtime},
                reload_yolo_rules=True,
            )
            return
        self.submit_filesystem_operation(
            "POST /api/fs/write",
            "write",
            raw_path,
            {"content": content, "expected_mtime": expected_mtime},
        )

    def handle_fs_delete(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        self.submit_filesystem_operation("POST /api/fs/delete", "delete", raw_path)

    def handle_fs_unindex(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", payload.get("root", ""))
        self.submit_filesystem_operation("POST /api/fs/unindex", "unindex", raw_path)

    def handle_fs_rename(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        new_name = payload.get("new_name", "")
        self.submit_filesystem_operation("POST /api/fs/rename", "rename", raw_path, {"new_name": new_name})

    def handle_fs_mkdir(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        self.submit_filesystem_operation("POST /api/fs/mkdir", "mkdir", raw_path)

    def handle_fs_batch(self, parsed: Any) -> None:
        del parsed
        started = time.perf_counter()
        payload = self.read_json_body(64 * 1024)
        body_read_ms = (time.perf_counter() - started) * 1000
        if payload is None:
            return
        # filesystem.validated_batch_requests is the one owner of what a batch may contain, and
        # the app owns the one typed rejection built from it.  Re-checking the same two rules here
        # is how the handler and the app payload used to disagree about the failure shape.
        try:
            summary = filesystem.filesystem_batch_request_summary(payload)
        except ValueError as error:
            self.write_json(
                self.server.app.fs_batch_invalid_request_result(payload, error),
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        operation_started = time.perf_counter()
        response, status = self.server.app.fs_batch_http_payload(payload)
        operation_ms = max(0.0, (time.perf_counter() - operation_started) * 1000)
        self._http_response_compute_ms = max(0.0, (time.perf_counter() - started) * 1000)
        self._http_response_performance_details = {
            "fs_batch": True,
            "fs_batch_offloaded": True,
            "fs_batch_size": summary["batch_size"],
            "fs_batch_body_read_ms": round(body_read_ms, 3),
            "fs_batch_operation_ms": round(operation_ms, 3),
            "fs_batch_list_ms": 0.0,
            "fs_batch_info_ms": 0.0,
            "fs_batch_operations": json.dumps(summary["operations"], sort_keys=True),
            "fs_batch_path_hashes": json.dumps(summary["path_fingerprints"]),
            "fs_batch_triggers": json.dumps(summary["triggers"], sort_keys=True),
            "fs_batch_client_revision": summary["client_revision"],
            "fs_batch_client_scope": summary["client_scope"],
        }
        self.write_json(response, status=status)

    def handle_fs_resolve_file_candidates(self, parsed: Any) -> None:
        del parsed
        payload = self.read_json_body(8 * 4096)
        if payload is None:
            return
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 8 or any(not isinstance(path, str) for path in raw_paths):
            self.write_json(error_payload("paths must contain 1 to 8 strings", message_key="request.error.jsonObject", status=HTTPStatus.BAD_REQUEST), status=HTTPStatus.BAD_REQUEST)
            return
        try:
            self.write_json(filesystem.resolve_file_candidates(raw_paths), status=HTTPStatus.OK)
        except filesystem.FilesystemError as error:
            self.write_json(error.payload(), status=HTTPStatus(error.status))

    def file_transfer_max_bytes(self) -> int:
        getter = getattr(self.server.app, "file_transfer_max_bytes", None)
        if callable(getter):
            return int(getter())
        getter = getattr(self.server.app, "upload_max_bytes", None)
        if callable(getter):
            return int(getter())
        return UPLOAD_MAX_BYTES

    def handle_upload(self, session: str, *, editor_path: str = "", base_dir: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        upload_max_bytes = self.file_transfer_max_bytes()
        body, error, status = Handler.read_request_body(self, upload_max_bytes, too_large_message=f"upload is too large; limit is {upload_max_bytes} bytes")
        if error is not None:
            return {**error, "session": session}, status
        try:
            files = parse_multipart_upload(self.headers.get("Content-Type", ""), body or b"", max_part_bytes=upload_max_bytes)
        except ValueError as exc:
            return error_payload(
                "invalid upload data",
                message_key="upload.error.invalidMultipart",
                diagnostic=exc,
                session=session,
                status=HTTPStatus.BAD_REQUEST,
            ), HTTPStatus.BAD_REQUEST
        auth_username = self.auth_identity().username
        if editor_path or base_dir:
            return self.server.app.upload_editor_files(
                files,
                editor_path=editor_path,
                base_dir=base_dir,
                auth_username=auth_username,
                session=session or "editor",
            )
        return self.server.app.upload_files(session, files, auth_username=auth_username)

# SSE carries an optional `id:` line that `EventSource` hands the browser as
# `event.lastEventId`. It sits outside the JSON payload, so the frame body key sets stay
# byte-identical and the browser's `exactFields` validators never see it -- which is the whole
# reason the emit timestamp travels here instead of inside the body, where adding a key would
# make every browser on a stale bundle reject the frame. An empty id writes no line at all, so
# every route that does not opt in is byte-identical on the wire.
def sse_id_line(event_id: str) -> bytes:
    text = str(event_id or "")
    if not text:
        return b""
    if not text.isascii() or any(character.isspace() or ord(character) < 33 for character in text):
        raise ValueError("SSE event id is invalid")
    return f"id: {text}\n".encode("ascii")


# The emit timestamp itself. `time.monotonic()` cannot go backwards, so successive frames on one
# connection carry non-decreasing ids and the browser can compare emit spacing against arrival
# spacing to tell "the server never sent it" from "the server sent it and it arrived late". It is
# process-relative on purpose: a wall clock can step, and an absolute epoch is not needed to
# compare two frames from the same server. Integer milliseconds keeps it about ten bytes a frame.
def stats_stream_emit_id() -> str:
    return str(int(time.monotonic() * 1000))


# One record per boundary per client per this window. The operator ring is capacity-bounded and
# its drop counter is a browser-test failure gate, so a per-cadence record would evict unrelated
# diagnostics and turn a slow host into a new flake. A stall needs ONE record naming the boundary,
# not one per second, so the window is deliberately much coarser than the cadence.
_STATS_STREAM_BOUNDARY_DEDUPE_SECONDS = 30.0


# Diagnostic capture for the YO!stats stall investigation. Every record is anomaly-only and
# deduped per client, so a healthy stream writes nothing and one client can never evict the
# bounded operator ring. `info` on purpose: a late tick is evidence, not a
# release-blocking failure, and must not change any test's pass/fail outcome. The bound client
# id is used only as a dedupe key and is never retained in the entry.
def _record_stats_stream_boundary(
    boundary: str,
    event: str,
    client_key: str,
    cadence_seconds: float,
    details: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {"boundary": boundary, "cadence_seconds": cadence_seconds}
    payload.update(details)
    # `status` is the discriminator between two records of the same kind, so it belongs in the
    # dedupe key: without it a CONFLICT arriving within a cadence of an ACCEPTED is suppressed
    # by it, and the transition that actually ended the stream is the one that goes missing.
    status = details.get("status")
    dedupe_status = "" if status is None else f":{int(status)}"
    emit_server_log(
        "info",
        "stats-stream",
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        category="stats_stream",
        route="/api/stats-stream",
        event=event,
        dedupe_key=f"stats-stream:{boundary}:{event}{dedupe_status}:{client_key}",
        dedupe_seconds=max(_STATS_STREAM_BOUNDARY_DEDUPE_SECONDS, float(cadence_seconds)),
    )


class ApiResponseWriter(_HandlerAdapter):
    """Composed owner for ApiResponseWriter."""

    def write_sse_json(self, event: str, value: Any, *, event_id: str = "") -> None:
        data = json.dumps(value, ensure_ascii=False)
        self.wfile.write(sse_id_line(event_id))
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        for line in data.splitlines() or [""]:
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def write_sse_bytes(self, event: str, value: bytes, *, event_id: str = "") -> None:
        """Write an already-validated compact JSON payload without decoding it."""

        if not isinstance(value, bytes) or not value:
            raise ValueError("SSE byte payload must be non-empty")
        event_name = str(event or "").strip()
        if not event_name.isascii() or not event_name or any(
            character.isspace() or ord(character) < 33 for character in event_name
        ):
            raise ValueError("SSE event name is invalid")
        self.wfile.write(sse_id_line(event_id))
        self.wfile.write(f"event: {event_name}\n".encode("ascii"))
        for line in value.splitlines() or [b""]:
            self.wfile.write(b"data: " + line + b"\n")
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
        self.record_http_response_bytes(status, len(data), "text/html; charset=utf-8")

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
        self.record_http_response_bytes(status, 0)

    def write_static_asset(self, asset: str, content_type: str) -> None:
        path = static_asset_path(asset)
        if path is None:
            locale = resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", ""))
            self.write_text(
                server_string(locale, "request.error.staticAssetMissing", asset=asset) + "\n",
                status=HTTPStatus.NOT_FOUND,
            )
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("failed to read static asset %s: %s", asset, exc)
            locale = resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", ""))
            self.write_text(
                server_string(locale, "request.error.staticAssetReadFailed", asset=asset) + "\n",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
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
        self.record_http_response_bytes(HTTPStatus.OK, len(body), content_type)

    def write_static_head(self, asset: str, content_type: str) -> None:
        path = static_asset_path(asset)
        if path is None:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            self.record_http_response_bytes(HTTPStatus.NOT_FOUND, 0, content_type)
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("failed to read static asset %s: %s", asset, exc)
            locale = resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", ""))
            self.write_text(
                server_string(locale, "request.error.staticAssetReadFailed", asset=asset) + "\n",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
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
        self.record_http_response_bytes(HTTPStatus.OK, 0, content_type)

    def api_request_id(self) -> str:
        """Return one validated browser correlation ID or a server-minted replacement."""
        if self._api_request_id:
            return self._api_request_id
        headers = getattr(self, "headers", {})
        proposed = str(headers.get("X-YOLOmux-Request-ID") or "").strip()
        if re.fullmatch(r"r-[A-Za-z0-9._-]{1,120}", proposed):
            self._api_request_id = proposed
        else:
            self._api_request_id = f"r-{uuid.uuid4().hex}"
        if not getattr(self, "_http_transport_request_id", ""):
            self._http_transport_request_id = self._api_request_id
        return self._api_request_id

    def write_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.write_api_response(value, status=status)

    def write_json_bytes(self, data: bytes, status: HTTPStatus = HTTPStatus.OK, *, json_encode_ms: float = 0.0) -> None:
        self.write_api_response(data, status=status, json_bytes=True, json_encode_ms=json_encode_ms)

    def write_product_bytes(
        self,
        data: bytes,
        product: ProductMetadata,
        *,
        promise: tuple[str, int] | None = None,
    ) -> None:
        """Frame or forward one trusted producer's opaque product bytes."""

        self.write_api_response(data, product_metadata=product, product_promise=promise)

    def write_product_stream(self, transfer: Any) -> None:
        """Forward one verified file-backed artifact without retaining its body in this process."""
        product = validated_product_metadata(transfer.product, body_length=int(transfer.product["length"]))
        route = self._route_response
        if route is None or route.protocol != RESPONSE_BINARY or product["format"] != "opaque_bytes":
            transfer.close()
            raise ValueError("streamed product has invalid route or format")
        self._route_response_written = True
        head_only = str(getattr(self, "command", "GET") or "GET").upper() == "HEAD"
        digest = hashlib.sha256()
        offset = 0
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", product["content_type"])
            self.send_header("Content-Length", str(product["length"]))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Accept-Encoding")
            if product["disposition"] == "attachment":
                self.send_header("Content-Disposition", f'attachment; filename="{product["filename"]}"')
            self.send_auth_cookie_if_needed()
            self.end_headers()
            if not head_only:
                while offset < product["length"]:
                    chunk = transfer.read(offset)
                    if not chunk or offset + len(chunk) > product["length"]:
                        raise OSError("artifact stream ended outside its declared length")
                    written = self.wfile.write(chunk)
                    if written != len(chunk):
                        raise OSError(f"response writer emitted {written} of {len(chunk)} artifact bytes")
                    digest.update(chunk)
                    offset += len(chunk)
                if digest.hexdigest() != product["sha256"]:
                    raise OSError("artifact stream integrity mismatch")
            self.record_http_response_bytes(
                HTTPStatus.OK,
                0 if head_only else offset,
                product["content_type"],
                {
                    "uncompressed_bytes": product["length"],
                    "wire_bytes": 0 if head_only else offset,
                    "representation_bytes": product["length"],
                    "content_encoding": "identity",
                    "head_only": head_only,
                    "product_format": product["format"],
                    "product_bytes": product["length"],
                    "product_sha256": product["sha256"],
                    "product_disposition": product["disposition"],
                },
            )
        finally:
            transfer.close()

    def write_api_response(
        self,
        value: Any,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_bytes: bool = False,
        product_metadata: ProductMetadata | None = None,
        product_promise: tuple[str, int] | None = None,
        json_encode_ms: float = 0.0,
    ) -> None:
        """Own the canonical API envelope, causal failure record, and correlated log line."""
        route = self._route_response
        status_code = int(status)
        if status_code in {HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED}:
            if value is not None and value != "" and value != b"":
                raise ValueError(f"bodyless API response {status_code} cannot carry a payload")
            self._route_response_written = True
            self._write_bodyless_api_response(HTTPStatus(status_code))
            return
        if product_metadata is not None:
            if not isinstance(value, bytes):
                raise ValueError("opaque product body must be bytes")
            product = validated_product_metadata(product_metadata, body_length=len(value))
            expected_protocols = (
                {RESPONSE_JSON, RESPONSE_JSON_BATCH}
                if product["format"] == "json"
                else {RESPONSE_BINARY}
            )
            if route is None or route.protocol not in expected_protocols or status_code != HTTPStatus.OK:
                raise ValueError("opaque product has invalid status, format, or route")
            if product_promise is not None:
                if (
                    not isinstance(product_promise, tuple)
                    or len(product_promise) != 2
                    or not isinstance(product_promise[0], str)
                    or not product_promise[0].strip()
                    or isinstance(product_promise[1], bool)
                    or not isinstance(product_promise[1], int)
                    or product_promise[1] < 0
                ):
                    raise ValueError("product promise must be a non-empty key and non-negative epoch")
                self.server.app.observe_http_product_delivery(product_promise[0], product_promise[1])
            self._route_response_written = True
            if product["format"] == "json":
                framed = ready_response_envelope_bytes(value, self.api_request_id())
                self._write_json_representation(
                    framed,
                    status=HTTPStatus.OK,
                    json_encode_ms=0.0,
                    product_metadata=product,
                )
            else:
                self._write_product_representation(
                    value,
                    status=HTTPStatus.OK,
                    content_type=product["content_type"],
                    disposition=product["disposition"],
                    filename=product["filename"],
                    product_metadata=product,
                )
            return
        if route is None:
            if json_bytes:
                self._write_json_representation(value, status=status, json_encode_ms=json_encode_ms)
                return
            encode_started = time.perf_counter()
            data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            # Commit the queued ticket BEFORE the bytes reach the client. With a threaded server the
            # client can issue a causally-later read that reaches the app ledger before this writer
            # thread resumes, so committing after the flush lets an accepted ticket be momentarily
            # invisible as outstanding. Commit reflects server-side queued state and is honest
            # regardless of whether the bytes land; the client-receipt outcome is a separate step.
            if hasattr(self.server.app, "observe_http_commit"):
                self.server.app.observe_http_commit(value, status)
            self._write_json_representation(
                data,
                status=status,
                json_encode_ms=(time.perf_counter() - encode_started) * 1000,
            )
            # Record the client receipt only AFTER the write returns: a failed flush (BrokenPipe/
            # OSError propagates from here) must not claim the accepted receipt reached the client.
            if hasattr(self.server.app, "observe_http_receipt"):
                self.server.app.observe_http_receipt(value, status)
            return

        if json_bytes:
            try:
                payload = json.loads(value)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("cached JSON response is invalid") from error
        else:
            payload = value

        request_id = self.api_request_id()
        canonical = (
            isinstance(payload, dict)
            and payload.get("state") in {"ready", "queued", "failed"}
            and isinstance(payload.get("request"), dict)
        )
        transient_read_failure = (
            not canonical
            and route.method == "GET"
            and status_code >= HTTPStatus.BAD_REQUEST
            and isinstance(payload, dict)
            and (
                local_service_failure_is_transient(payload)
                or (
                    status_code == HTTPStatus.FAILED_DEPENDENCY
                    and payload.get("terminal") is not True
                )
            )
        )
        if transient_read_failure:
            status_code = HTTPStatus.ACCEPTED
            payload = {
                "status": "pending",
                "retry_after_seconds": 1,
                "reason": "upstream service is refreshing",
            }
        if canonical:
            envelope = copy.deepcopy(payload)
            existing_request_id = str(envelope["request"].get("id") or "").strip()
            envelope["request"]["id"] = (
                existing_request_id
                if re.fullmatch(r"r-[A-Za-z0-9._-]{1,120}", existing_request_id)
                else request_id
            )
            self._api_request_id = envelope["request"]["id"]
        elif status_code == HTTPStatus.ACCEPTED:
            legacy = payload if isinstance(payload, dict) else {}
            legacy_state = str(legacy.get("state") or legacy.get("status") or "").strip().lower()
            operation_id = str(legacy.get("key") or legacy.get("operation_id") or "").strip()
            if legacy_state in {"queued", "pending"} and operation_id:
                envelope = {
                    "state": "queued",
                    "request": {"id": request_id},
                    "operation": {
                        "id": operation_id,
                        "progress": {
                            "phase": "accepted",
                            "legacy": copy.deepcopy(legacy),
                        },
                    },
                    "ok": True,
                    "terminal": False,
                }
                reserved = {"data", "error", "operation", "request", "state"}
                for key, item in legacy.items():
                    if key not in reserved and key not in envelope:
                        envelope[key] = copy.deepcopy(item)
            elif legacy_state == "pending" and route.method == "GET":
                try:
                    retry_after_seconds = int(legacy.get("retry_after_seconds") or 0)
                except (TypeError, ValueError):
                    retry_after_seconds = 0
                if not 1 <= retry_after_seconds <= 60:
                    raise ValueError("pending read response requires a bounded retry interval")
                envelope = {
                    "state": "queued",
                    "request": {"id": request_id},
                    "ok": True,
                    "terminal": False,
                }
                reserved = {"data", "error", "operation", "request", "state"}
                for key, item in legacy.items():
                    if key not in reserved and key not in envelope:
                        envelope[key] = copy.deepcopy(item)
            elif legacy_state in {"queued", "pending"}:
                status_code = HTTPStatus.CONFLICT
                envelope = error_payload(
                    "operation was not accepted by a durable owner",
                    message_key="common.requestFailed",
                    canonical=True,
                    code="operation_not_accepted",
                    origin="server.http",
                    retryable=True,
                    details={"legacy": copy.deepcopy(legacy)},
                    stack=[{
                        "component": "server.http",
                        "operation": f"{route.method} {route.path}",
                        "code": "operation_not_accepted",
                    }],
                    request_id=request_id,
                )
            else:
                status_code = HTTPStatus.OK
                envelope = {
                    "state": "ready",
                    "request": {"id": request_id},
                    "data": copy.deepcopy(payload),
                    "ok": True,
                    "terminal": True,
                }
        elif status_code >= HTTPStatus.BAD_REQUEST:
            legacy = payload if isinstance(payload, dict) else {}
            raw_error = legacy.get("error") or legacy.get("reason") or legacy.get("message") or f"HTTP {status_code}"
            descriptor = legacy.get("user_message") if isinstance(legacy.get("user_message"), dict) else {}
            legacy_code = str(legacy.get("reason_code") or legacy.get("error_code") or legacy.get("code") or "").strip().lower()
            normalized_code = re.sub(r"[^a-z0-9_]+", "_", legacy_code).strip("_")
            status_codes = {
                HTTPStatus.BAD_REQUEST: "invalid_request",
                HTTPStatus.UNAUTHORIZED: "authentication_required",
                HTTPStatus.FORBIDDEN: "forbidden",
                HTTPStatus.NOT_FOUND: "not_found",
                HTTPStatus.CONFLICT: "conflict",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "request_too_large",
                HTTPStatus.TOO_MANY_REQUESTS: "rate_limited",
                HTTPStatus.FAILED_DEPENDENCY: "dependency_failed",
                HTTPStatus.SERVICE_UNAVAILABLE: "service_unavailable",
                HTTPStatus.GATEWAY_TIMEOUT: "deadline_expired",
                HTTPStatus.UPGRADE_REQUIRED: "upgrade_required",
            }
            code = normalized_code or status_codes.get(status_code, "request_failed")
            cause = legacy.get("cause") if isinstance(legacy.get("cause"), dict) else None
            root = {
                "component": "server.http",
                "operation": f"{route.method} {route.path}",
                "code": code,
            }
            if cause is not None:
                root.update(copy.deepcopy(cause))
            excluded = {"cause", "code", "diagnostic", "error", "error_code", "origin", "reason_code", "retryable", "status", "user_message"}
            details = {key: copy.deepcopy(item) for key, item in legacy.items() if key not in excluded}
            if legacy.get("diagnostic"):
                details["diagnostic"] = str(legacy["diagnostic"])
            envelope = error_payload(
                raw_error,
                message_key=str(descriptor.get("key") or "common.requestFailed"),
                message_params=descriptor.get("params") if isinstance(descriptor.get("params"), dict) else {},
                canonical=True,
                code=code,
                origin=str(legacy.get("origin") or "server.http"),
                retryable=(
                    legacy["retryable"]
                    if isinstance(legacy.get("retryable"), bool)
                    else status_code in {
                        HTTPStatus.REQUEST_TIMEOUT,
                        HTTPStatus.TOO_MANY_REQUESTS,
                        HTTPStatus.BAD_GATEWAY,
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        HTTPStatus.GATEWAY_TIMEOUT,
                    }
                ),
                details=details,
                stack=[root],
                request_id=request_id,
            )
            envelope["user_message"] = copy.deepcopy(envelope["error"]["message"])
            envelope["legacy_error"] = str(raw_error)
            if legacy.get("reason"):
                envelope["error"]["reason"] = str(legacy["reason"])
            if legacy.get("diagnostic"):
                envelope["diagnostic"] = str(legacy["diagnostic"])
            if legacy.get("reason_code"):
                envelope["reason_code"] = str(legacy["reason_code"])
            envelope["status"] = status_code
            envelope["ok"] = False
            envelope["terminal"] = True
        else:
            envelope = {
                "state": "ready",
                "request": {"id": request_id},
                "data": copy.deepcopy(payload),
                "ok": True,
                "terminal": True,
            }

        state = str(envelope.get("state") or "")
        if state == "ready":
            if not canonical:
                data_record = envelope.get("data")
                if isinstance(data_record, dict):
                    reserved = {"data", "error", "operation", "request", "state"}
                    for key, item in data_record.items():
                        if key not in reserved and key not in envelope:
                            envelope[key] = copy.deepcopy(item)
                envelope["ok"] = True
                envelope["terminal"] = True
        elif state == "queued":
            if not canonical:
                envelope["ok"] = True
                envelope["terminal"] = False
        elif state == "failed":
            error_record = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
            if not canonical:
                message_record = error_record.get("message") if isinstance(error_record.get("message"), dict) else {}
                details_record = error_record.get("details") if isinstance(error_record.get("details"), dict) else {}
                reserved = {"data", "error", "operation", "request", "state"}
                for key, item in details_record.items():
                    if key not in reserved and key not in envelope:
                        envelope[key] = copy.deepcopy(item)
                envelope.setdefault("user_message", copy.deepcopy(message_record))
                envelope.setdefault("legacy_error", str(message_record.get("fallback") or "request failed"))
                envelope["status"] = status_code
                envelope["ok"] = False
                envelope["terminal"] = True

        request_record = envelope.get("request") if isinstance(envelope.get("request"), dict) else {}
        if not re.fullmatch(r"r-[A-Za-z0-9._-]{1,120}", str(request_record.get("id") or "")):
            raise ValueError("API response requires a validated request.id")
        if state == "ready" and (
            not (HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES)
            or status_code == HTTPStatus.ACCEPTED
            or "data" not in envelope
            or "error" in envelope
            or "operation" in envelope
        ):
            raise ValueError(f"ready API response has invalid HTTP status {status_code}")
        bounded_read_pending = (
            state == "queued"
            and route.method == "GET"
            and str(envelope.get("status") or "").strip().lower() == "pending"
            and isinstance(envelope.get("retry_after_seconds"), int)
            and 1 <= envelope["retry_after_seconds"] <= 60
            and "operation" not in envelope
        )
        if state == "queued" and (
            status_code != HTTPStatus.ACCEPTED
            or (
                not bounded_read_pending
                and (
                    not isinstance(envelope.get("operation"), dict)
                    or not str(envelope["operation"].get("id") or "")
                )
            )
            or "data" in envelope
            or "error" in envelope
        ):
            raise ValueError("queued API response requires HTTP 202 and operation.id or bounded read pending state")
        if state == "failed":
            error_record = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
            message_record = error_record.get("message") if isinstance(error_record.get("message"), dict) else {}
            stack_record = error_record.get("stack") if isinstance(error_record.get("stack"), list) else []
            valid_stack = bool(stack_record) and all(
                isinstance(frame, dict)
                and bool(str(frame.get("component") or ""))
                and bool(str(frame.get("operation") or ""))
                and bool(str(frame.get("code") or ""))
                for frame in stack_record
            )
            valid_error = (
                HTTPStatus.BAD_REQUEST <= status_code <= 599
                and bool(str(error_record.get("code") or ""))
                and bool(str(error_record.get("origin") or ""))
                and isinstance(error_record.get("retryable"), bool)
                and isinstance(error_record.get("details"), dict)
                and bool(str(message_record.get("key") or ""))
                and isinstance(message_record.get("params"), dict)
                and isinstance(message_record.get("fallback"), str)
                and valid_stack
                and "data" not in envelope
                and "operation" not in envelope
            )
            if not valid_error:
                raise ValueError(f"failed API response has invalid HTTP status or error shape {status_code}")

        if state == "failed":
            error_record = envelope["error"]
            # The severity rule is owned by `failure_record_level`, not by this writer: a terminal
            # operation replayed here carries the same typed code the asynchronous recorder saw, so
            # the two writers must not be able to disagree about whether it is an error.
            emit_server_log(
                failure_record_level(error_record, status=status_code),
                "api-response",
                json.dumps({
                    "request": envelope["request"],
                    "operation": envelope.get("operation"),
                    "code": error_record.get("code"),
                    "origin": error_record.get("origin"),
                    "stack": error_record.get("stack"),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                category="api",
            )

        encode_started = time.perf_counter()
        data = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._route_response_written = True
        # Commit the queued ticket before the flush (see the note in the route-None branch): a
        # causally-later client read must not out-race this writer thread's ledger update.
        if hasattr(self.server.app, "observe_http_commit"):
            self.server.app.observe_http_commit(envelope, HTTPStatus(status_code))
        self._write_json_representation(
            data,
            status=HTTPStatus(status_code),
            json_encode_ms=(time.perf_counter() - encode_started) * 1000,
        )
        # Record the client receipt only after a successful write, never on a failed flush.
        if hasattr(self.server.app, "observe_http_receipt"):
            self.server.app.observe_http_receipt(envelope, HTTPStatus(status_code))

    def _write_bodyless_api_response(self, status: HTTPStatus) -> None:
        """Write an established bodyless protocol result from the shared response parent."""
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.record_http_response_bytes(status, 0)

    def _write_json_representation(
        self,
        data: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_encode_ms: float = 0.0,
        product_metadata: ProductMetadata | None = None,
    ) -> None:
        """Write already-validated JSON bytes without decoding/re-encoding them.

        Only ``write_api_response`` calls this representation writer. Local
        services may supply cached bytes to the parent without bypassing the
        envelope, metrics, compression, or auth-cookie owners.
        """
        self._write_product_representation(
            data,
            status=status,
            content_type="application/json; charset=utf-8",
            disposition="inline",
            filename="",
            json_encode_ms=json_encode_ms,
            product_metadata=product_metadata,
        )

    def _write_product_representation(
        self,
        data: bytes,
        *,
        status: HTTPStatus,
        content_type: str,
        disposition: str,
        filename: str,
        json_encode_ms: float = 0.0,
        product_metadata: ProductMetadata | None = None,
    ) -> None:
        """Write one final representation and verify the actual boundary byte count."""

        compression_started = time.perf_counter()
        headers = getattr(self, "headers", {})
        accept_encoding = headers.get("Accept-Encoding") if hasattr(headers, "get") else None
        body, content_encoding = gzip_response_body(data, content_type, accept_encoding)
        compression_ms = (time.perf_counter() - compression_started) * 1000 if content_encoding else 0.0
        # W9: the final wire representation is now fully prepared -- `body` holds the exact bytes the
        # client will receive, already compressed -- but not one header byte has left the socket yet.
        # This is the stated boundary for `route_to_representation_ready_ms`: route entry (dispatch
        # start) to the representation being ready. Stamping it HERE, before `send_response`, keeps it
        # free of header/body write and the client round trip, which is what makes it a server-side
        # assembly number rather than an end-to-end one. The compute/compression/write metrics below
        # are retained beside it as diagnostics, not replaced by it.
        representation_ready_at = time.perf_counter()
        dispatch_started = getattr(self, "_http_request_dispatch_started_at", None)
        route_to_representation_ready_ms = (
            round(max(0.0, (representation_ready_at - dispatch_started) * 1000), 3)
            if isinstance(dispatch_started, (int, float)) else None
        )
        head_only = str(getattr(self, "command", "GET") or "GET").upper() == "HEAD"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if disposition == "attachment":
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_auth_cookie_if_needed()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        write_started = time.perf_counter()
        if not head_only:
            written = self.wfile.write(body)
            if written != len(body):
                raise OSError(f"response writer emitted {written} of {len(body)} framed bytes")
        write_ms = (time.perf_counter() - write_started) * 1000
        wire_bytes = 0 if head_only else len(body)
        details = {
            "uncompressed_bytes": len(data),
            "wire_bytes": wire_bytes,
            "representation_bytes": len(body),
            "content_encoding": content_encoding or "identity",
            "json_encode_ms": round(json_encode_ms, 3),
            "compression_ms": round(compression_ms, 3),
            "write_ms": round(write_ms, 3),
            "head_only": head_only,
        }
        if route_to_representation_ready_ms is not None:
            # Measured at the boundary above (before headers/body write), not here after the write.
            details["route_to_representation_ready_ms"] = route_to_representation_ready_ms
        if product_metadata is not None:
            details["product_format"] = product_metadata["format"]
            details["product_bytes"] = product_metadata["length"]
            details["product_sha256"] = product_metadata["sha256"]
            details["product_disposition"] = product_metadata["disposition"]
        self.record_http_response_bytes(
            status,
            wire_bytes,
            content_type,
            details,
        )

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
            self.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
            return
        self.write_app_result(make_result(value))

    def write_validated_float_result(self, qs: dict, name: str, default: float, max_value: float, make_result) -> None:
        # The activity/session-files routes share one bounded float query parameter. Keep the bad-float
        # response and cap in one path so the three handlers cannot drift.
        value, error = parse_query_float(qs, name, default, max_value=max_value)
        if error:
            self.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
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
        self.record_http_response_bytes(status, len(data), "text/plain; charset=utf-8")


class Handler(AuthMixin, BaseHTTPRequestHandler):
    @property
    def filesystem_http_adapter(self) -> FilesystemHttpAdapter:
        adapter = self.__dict__.get("_filesystem_http_adapter")
        if adapter is None:
            adapter = FilesystemHttpAdapter(self)
            object.__setattr__(self, "_filesystem_http_adapter", adapter)
        return adapter

    @property
    def api_response_writer(self) -> ApiResponseWriter:
        adapter = self.__dict__.get("_api_response_writer")
        if adapter is None:
            adapter = ApiResponseWriter(self)
            object.__setattr__(self, "_api_response_writer", adapter)
        return adapter

    server: "TmuxWebtermHTTPServer"
    protocol_version = "HTTP/1.1"
    _route_response: Any = None
    _route_response_written = False
    _api_request_id = ""

    def handle_one_request(self) -> None:
        # BaseHTTPRequestHandler reuses this instance for HTTP/1.1 keep-alive requests. Route
        # handlers attach optional timing/details while building a response, so clear them at the
        # request boundary instead of letting a homepage sample become a later API sample.
        self._http_response_compute_ms = None
        self._http_response_performance_details = None
        self._http_request_started_at = time.perf_counter()
        self._http_request_line_read_at = None
        self._http_request_parse_completed_at = None
        self._http_request_dispatch_started_at = None
        self._http_request_thread_cpu_started_ns = None
        self._http_request_thread_native_id = None
        self._http_request_body_bytes = None
        self._http_request_body_identity_v1 = None
        self.request_body_consumed = False
        self._request_content_length = None
        self._request_content_length_present = False
        self._request_framing_accepted = False
        self._route_response = None
        self._route_response_written = False
        self._api_request_id = ""
        self._http_transport_request_id = ""
        super().handle_one_request()

    def dispatch_route_response(self, route: Any, operation: Callable[[], None]) -> None:
        """Run one registered route under the response boundary declared by its registry entry."""
        self._route_response = route
        self._route_response_written = False
        try:
            operation()
        except Exception as error:
            if self._route_response_written or route.protocol not in {RESPONSE_JSON, RESPONSE_JSON_BATCH}:
                raise
            cause = local_service_exception_cause(error)
            root = {
                "component": "server.http",
                "operation": f"{route.method} {route.path}",
                "code": "internal_error",
                **cause,
            }
            self.write_api_response(
                error_payload(
                    "request failed",
                    message_key="common.requestFailed",
                    canonical=True,
                    code="internal_error",
                    origin="server.http",
                    retryable=False,
                    details={"exception_type": type(error).__name__},
                    stack=[root],
                    request_id=self.api_request_id(),
                ),
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        finally:
            self._route_response = None

    def parse_request(self) -> bool:
        """Mark BaseHTTPRequestHandler's request-line and header boundary for timing.

        This is the one place the request line and the header block are both finished, so it is
        also the only place that can refuse a header block whose framing this parser and its peer
        would not agree on.
        """
        self._http_request_line_read_at = time.perf_counter()
        try:
            if not super().parse_request():
                return False
            return self.accept_header_framing()
        finally:
            self._http_request_parse_completed_at = time.perf_counter()

    def accept_header_framing(self) -> bool:
        """Accept one unambiguous request-body framing before route or auth dispatch.

        RFC 7230 3.2.4 deprecated obs-fold -- a header value continued on a line beginning with SP
        or HTAB -- and allows a server either to reject the message or to replace the fold with
        spaces before reading the value.  Python's email parser does neither: it unfolds the
        continuation into the PREVIOUS header's value, raw CRLF and all.  A folded
        ``Content-Length`` therefore never reaches ``self.headers.get("Content-Length")``, which is
        the only input ``request_has_unread_body`` has, so the connection is kept alive with the
        declared body still on the socket and those bytes are read as the next request line.  A peer
        that unfolds instead frames that same byte range as a body.  Two framings of one socket is a
        desync, so refuse the message: rejecting is fail-closed, and normalizing would still leave
        this server and the peer disagreeing about which header the folded bytes belonged to.

        A continuation on the FIRST header line belongs to no header at all; Python drops it and
        records only a defect, so the request would otherwise be served with bytes nobody parsed.
        Header-only parsing also reports MIME-body defects for valid multipart requests because it
        never receives the multipart body. Those are not HTTP syntax defects; only positive evidence
        that a header line was folded, discarded, or reclassified belongs to this boundary.

        BaseHTTPRequestHandler does not decode Transfer-Encoding, so every such declaration is
        refused. Content-Length must be singular, decimal, and representable by the bounded body
        owner. Route-specific size limits remain in ``read_request_body``.
        """
        if getattr(self, "_request_framing_accepted", False):
            return True
        headers = getattr(self, "headers", None)
        if headers is None:
            return True
        raw_items = getattr(headers, "raw_items", None)
        header_items = list(raw_items()) if callable(raw_items) else list(headers.items())
        folded = any("\n" in str(value) or "\r" in str(value) for _name, value in header_items)
        invalid_name = any(HTTP_HEADER_NAME_RE.fullmatch(str(name)) is None for name, _value in header_items)
        invalid_value = any(
            any((ord(character) < 32 and character != "\t") or ord(character) == 127 for character in str(value))
            for _name, value in header_items
        )
        get_unixfrom = getattr(headers, "get_unixfrom", None)
        misplaced_envelope = bool(get_unixfrom()) if callable(get_unixfrom) else False
        header_framing_defects = (
            FirstHeaderLineIsContinuationDefect,
            InvalidHeaderDefect,
            MisplacedEnvelopeHeaderDefect,
            MissingHeaderBodySeparatorDefect,
        )
        framing_defects = tuple(
            defect
            for defect in getattr(headers, "defects", ())
            if isinstance(defect, header_framing_defects)
        )
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            transfer_encoding_values = list(get_all("Transfer-Encoding", []))
            content_length_values = list(get_all("Content-Length", []))
        else:
            transfer_encoding = headers.get("Transfer-Encoding")
            content_length = headers.get("Content-Length")
            transfer_encoding_values = [] if transfer_encoding is None else [transfer_encoding]
            content_length_values = [] if content_length is None else [content_length]
        invalid_content_length = len(content_length_values) > 1
        declared_length = 0
        if len(content_length_values) == 1:
            length_text = str(content_length_values[0]).strip()
            normalized_length = length_text.lstrip("0") or "0"
            invalid_content_length = (
                re.fullmatch(r"[0-9]+", length_text) is None
                or len(normalized_length) > len(str(HTTP_MAX_DECLARED_BODY_BYTES))
                or (
                    len(normalized_length) == len(str(HTTP_MAX_DECLARED_BODY_BYTES))
                    and normalized_length > str(HTTP_MAX_DECLARED_BODY_BYTES)
                )
            )
            if not invalid_content_length:
                declared_length = int(normalized_length)
        if (
            not folded
            and not invalid_name
            and not invalid_value
            and not misplaced_envelope
            and not framing_defects
            and not transfer_encoding_values
            and not invalid_content_length
        ):
            self._request_content_length = declared_length
            self._request_content_length_present = bool(content_length_values)
            self._request_framing_accepted = True
            return True
        # The parsed message does not have one framing both peers can safely reuse.
        self.close_connection = True
        self.send_error(HTTPStatus.BAD_REQUEST, "Bad request header framing")
        return False

    def handle_expect_100(self) -> bool:
        """Validate framing before inviting the peer to send a declared request body."""
        if not self.accept_header_framing():
            return False
        return super().handle_expect_100()

    def setup(self) -> None:
        preparer = getattr(self.server, "prepare_request_socket", None)
        if callable(preparer):
            self.request = preparer(self.request)
        self._request_is_https = isinstance(self.request, ssl.SSLSocket)
        super().setup()

    def log_message(self, fmt: str, *args: Any) -> None:
        message = TOKEN_LOG_RE.sub(r"\1[redacted]", fmt % args)
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), message))

    def request_line_capture(self) -> tuple[bytes, bool]:
        """Return the raw request line and whether the complete line was already buffered.

        BaseHTTPRequestHandler reads 65,537 bytes before it emits a 414.  The remainder is
        usually already buffered for a browser request, so consume through its newline only when
        it is immediately available.  Never wait for more client input just to diagnose an error.
        """
        captured = bytearray(bytes(getattr(self, "raw_requestline", b"") or b""))
        if captured.endswith(b"\n"):
            return bytes(captured), True
        request_file = getattr(self, "rfile", None)
        peek = getattr(request_file, "peek", None)
        readline = getattr(request_file, "readline", None)
        if not callable(peek) or not callable(readline) or len(captured) >= HTTP_REQUEST_LINE_CAPTURE_LIMIT:
            return bytes(captured), False
        connection = getattr(self, "connection", None)
        gettimeout = getattr(connection, "gettimeout", None)
        setblocking = getattr(connection, "setblocking", None)
        settimeout = getattr(connection, "settimeout", None)
        timeout = gettimeout() if callable(gettimeout) else None
        remaining = HTTP_REQUEST_LINE_CAPTURE_LIMIT - len(captured)
        try:
            if callable(setblocking):
                setblocking(False)
            # BufferedReader.peek() may return more than requested.  Do not let an oversized-line
            # diagnostic cross its own evidence bound into a folded header or pipelined request.
            buffered = bytes(peek(remaining))[:remaining]
        except (BlockingIOError, OSError, ValueError):
            return bytes(captured), False
        finally:
            if callable(settimeout):
                settimeout(timeout)
        newline = buffered.find(b"\n")
        if newline < 0:
            return bytes(captured), False
        try:
            captured.extend(readline(newline + 1))
        except (OSError, ValueError):
            return bytes(captured), False
        return bytes(captured), captured.endswith(b"\n")

    def log_request_uri_too_long(self) -> None:
        """Write the framing evidence for a 414 without changing its HTTP outcome."""
        raw_line, complete = self.request_line_capture()
        line = raw_line.rstrip(b"\r\n").decode("latin-1")
        candidate_method = line.split(" ", 1)[0]
        method = candidate_method if re.fullmatch(r"[A-Z]+", candidate_method) else "invalid"
        address = self.client_address if isinstance(self.client_address, tuple) else ()
        client = f"{address[0]}:{address[1]}" if len(address) > 1 else str(address[0]) if address else "unknown"
        connection = getattr(self, "connection", None)
        try:
            local = connection.getsockname() if connection is not None else None
        except OSError:
            local = None
        try:
            descriptor = connection.fileno() if connection is not None else None
        except OSError:
            descriptor = None
        self.log_error(
            "request-line-capture %s",
            json.dumps({
                "status": HTTPStatus.REQUEST_URI_TOO_LONG.value,
                "client": client,
                "connection": {"local": local, "fd": descriptor},
                "method": method,
                "request_line": line,
                "request_line_complete": complete,
                "request_line_bytes": len(raw_line),
            }, ensure_ascii=True, separators=(",", ":")),
        )

    def send_error(self, code: int | HTTPStatus, message: str | None = None, explain: str | None = None) -> None:
        if int(code) == HTTPStatus.REQUEST_URI_TOO_LONG:
            self.log_request_uri_too_long()
        super().send_error(code, message, explain)

    def send_response(self, code: int | HTTPStatus, message: str | None = None) -> None:
        """Mark the response committed for every JSON and non-JSON protocol family.

        Every response owner reaches this line before it emits a single header, so this is the one
        place that can decide connection reuse for all of them: 404 for a deleted route, the 500
        from ``dispatch_route_response``, the auth-setup redirect, the Content-Length rejections in
        ``read_request_body``, and the POST handlers that answer from the query string alone.  A
        response committed while the declared body is still on the socket must end the connection,
        otherwise the leftover bytes become the next request line.
        """
        self._route_response_written = True
        self.close_after_unread_body()
        super().send_response(code, message)

    def http_endpoint_metric_key(self) -> str:
        method = str(getattr(self, "command", "") or "GET").upper()
        try:
            path = urlparse(str(getattr(self, "path", "") or "/")).path or "/"
        except ValueError:
            path = str(getattr(self, "path", "") or "/").split("?", 1)[0] or "/"
        return f"{method} {path}"[:120]

    def measurement_marker(self) -> str:
        """Return only a validated capture marker; callers must not retain it verbatim."""
        headers = getattr(self, "headers", {})
        marker = str(headers.get("X-YOLOmux-Measurement") or "") if hasattr(headers, "get") else ""
        if marker.startswith("capture-") and len(marker) == 40 and all(char in "0123456789abcdef" for char in marker[8:]):
            return marker
        return ""

    def measurement_scope(self) -> str:
        """Return a generic in-memory scope without retaining a browser marker."""
        return "capture" if self.measurement_marker() else ""

    def measurement_request_id(self) -> str:
        """Return an opaque, bounded join key for one validated capture request."""
        marker = self.measurement_marker()
        return hashlib.sha256(marker.encode("ascii")).hexdigest()[:16] if marker else ""

    def measurement_connection_id(self) -> str:
        """Return a capture-scoped opaque join key for this request's TCP peer."""
        marker = self.measurement_marker()
        address = self.client_address if isinstance(self.client_address, tuple) else ()
        peer_port = address[1] if len(address) > 1 else None
        if not marker or not isinstance(peer_port, int):
            return ""
        return hashlib.sha256(f"{marker}:{peer_port}".encode("ascii")).hexdigest()[:16]

    def record_http_response_bytes(
        self,
        status: HTTPStatus | int,
        body_bytes: int,
        content_type: str = "",
        performance_details: dict[str, Any] | None = None,
    ) -> None:
        app = getattr(getattr(self, "server", None), "app", None)
        recorder = getattr(app, "record_performance_sample", None)
        if not callable(recorder):
            return
        status_code = int(status)
        endpoint = self.http_endpoint_metric_key()
        details = {
            "status": status_code,
            "method": endpoint.split(" ", 1)[0],
            "path": endpoint.split(" ", 1)[1] if " " in endpoint else "",
            "content_type": content_type_base(content_type),
        }
        if str(details["path"]).startswith("/api/"):
            details["request_id"] = self.api_request_id()
            details["transport_request_id"] = str(
                getattr(self, "_http_transport_request_id", "") or details["request_id"]
            )
        measurement_scope = self.measurement_scope()
        if measurement_scope:
            details["measurement_scope"] = measurement_scope
            # The browser keeps the raw capture marker; metrics retain only this opaque digest so
            # a slow request can be joined to that click without exposing a browser identifier.
            details["measurement_request_id"] = self.measurement_request_id()
            details["measurement_connection_id"] = self.measurement_connection_id()
            details["process_pid"] = os.getpid()
            request_thread_native_id = getattr(self, "_http_request_thread_native_id", None)
            if isinstance(request_thread_native_id, int):
                details["thread_native_id"] = request_thread_native_id
            request_thread_cpu_started_ns = getattr(self, "_http_request_thread_cpu_started_ns", None)
            if isinstance(request_thread_cpu_started_ns, int) and request_thread_native_id == threading.get_native_id():
                details["request_thread_cpu_ms"] = round(
                    max(0, time.thread_time_ns() - request_thread_cpu_started_ns) / 1_000_000,
                    3,
                )
            request_body_bytes = getattr(self, "_http_request_body_bytes", None)
            body_identity = getattr(self, "_http_request_body_identity_v1", None)
            if isinstance(request_body_bytes, int) and isinstance(body_identity, str):
                details["request_body_bytes"] = request_body_bytes
                details["request_body_identity_v1"] = body_identity
        request_started = getattr(self, "_http_request_started_at", None)
        request_line_read_at = getattr(self, "_http_request_line_read_at", None)
        request_parse_completed_at = getattr(self, "_http_request_parse_completed_at", None)
        dispatch_started = getattr(self, "_http_request_dispatch_started_at", None)
        response_started = time.perf_counter()
        if measurement_scope and isinstance(dispatch_started, (int, float)):
            details["dispatch_to_record_wall_ms"] = round(max(0.0, (response_started - dispatch_started) * 1000), 3)
        if isinstance(request_started, (int, float)):
            details["request_total_ms"] = round(max(0.0, (response_started - request_started) * 1000), 3)
        if isinstance(request_started, (int, float)) and isinstance(request_line_read_at, (int, float)):
            # For HTTP/1.1 keep-alives this includes the idle wait before the browser sends the
            # next request line. It is deliberately separate from server-side parsing or routing.
            details["request_line_wait_ms"] = round(max(0.0, (request_line_read_at - request_started) * 1000), 3)
        if isinstance(request_line_read_at, (int, float)) and isinstance(request_parse_completed_at, (int, float)):
            details["request_header_parse_ms"] = round(max(0.0, (request_parse_completed_at - request_line_read_at) * 1000), 3)
        if isinstance(request_parse_completed_at, (int, float)) and isinstance(dispatch_started, (int, float)):
            details["request_parse_to_route_ms"] = round(max(0.0, (dispatch_started - request_parse_completed_at) * 1000), 3)
        if isinstance(request_started, (int, float)) and isinstance(dispatch_started, (int, float)):
            # Keep the aggregate for existing consumers. The stage fields above distinguish an
            # HTTP/1.1 request-line wait from parsing and actual route entry.
            details["accept_to_route_ms"] = round(max(0.0, (dispatch_started - request_started) * 1000), 3)
        extra_details = getattr(self, "_http_response_performance_details", None)
        if isinstance(extra_details, dict):
            details.update(extra_details)
        if isinstance(performance_details, dict):
            details.update(performance_details)
        recorder(
            "http-endpoint",
            endpoint,
            trigger=endpoint,
            compute_ms=getattr(self, "_http_response_compute_ms", None) if getattr(self, "_http_response_compute_ms", None) is not None else (
                max(0.0, (response_started - dispatch_started) * 1000) if isinstance(dispatch_started, (int, float)) else None
            ),
            payload_bytes=max(0, int(body_bytes or 0)),
            cache_key={"kind": endpoint},
            cache_status=str(status_code),
            owner_role="server",
            count=1,
            details=details,
        )

    def redirect_plaintext_to_https_if_needed(self, parsed: Any) -> bool:
        del parsed
        if not getattr(self.server, "tls_context", None) or self.request_is_https():
            return False
        host = str(self.headers.get("Host") or self.server.server_name_with_port()).strip()
        if not host or "\r" in host or "\n" in host:
            host = self.server.server_name_with_port()
        location = f"https://{host}{self.path or '/'}"
        body = https_redirect_body(location, resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", "")))
        self.send_response(HTTPStatus.PERMANENT_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.record_http_response_bytes(HTTPStatus.PERMANENT_REDIRECT, len(body), "text/plain; charset=utf-8")
        else:
            self.record_http_response_bytes(HTTPStatus.PERMANENT_REDIRECT, 0, "text/plain; charset=utf-8")
        self.close_connection = True
        return True

    def do_GET(self) -> None:
        dispatch_http_route(self, "GET")

    def handle_fs_list(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_list(self, parsed)

    def handle_fs_fast_list(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_fast_list(self, parsed)

    def handle_fs_search(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_search(self, parsed)

    def handle_fs_index_status(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_index_status(self, parsed)

    def handle_fs_read(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_read(self, parsed)

    def handle_fs_info(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_info(self, parsed)

    def handle_fs_resolve_file_candidates(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_resolve_file_candidates(self, parsed)

    def handle_fs_diff(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_diff(self, parsed)

    def handle_fs_git_history(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_git_history(self, parsed)

    def handle_fs_git_commit(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_git_commit(self, parsed)

    def handle_blame(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_blame(self, parsed)

    def submit_filesystem_operation(
        self,
        route: str,
        operation: str,
        raw_path: str,
        args: dict[str, Any] | None = None,
        *,
        reload_yolo_rules: bool = False,
    ) -> None:
        return FilesystemHttpAdapter.submit_filesystem_operation(self, route, operation, raw_path, args, reload_yolo_rules=reload_yolo_rules)

    def handle_fs_raw(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_raw(self, parsed)

    def handle_fs_zip(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_zip(self, parsed)

    def handle_fs_count(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_count(self, parsed)

    def handle_fs_html_preview(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_html_preview(self, parsed)

    def submit_filesystem_relay(self, route: str, operation: str, raw_path: str, args: dict[str, Any]) -> None:
        return FilesystemHttpAdapter.submit_filesystem_relay(self, route, operation, raw_path, args)

    def handle_preview_popout_placeholder(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        locale = resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", ""))
        title = html.escape(server_string(locale, "preview.popout.title", name=Path(raw_path).name or server_string(locale, "common.preview")))
        body = f"""<!doctype html>
<html {html_lang_dir_attrs(locale)}>
<head>
  <meta charset="utf-8">
  {MOBILE_VIEWPORT_META}
  <title>{title}</title>
</head>
<body></body>
</html>"""
        self.write_html(body)

    def handle_pane_popout_placeholder(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_item = qs.get("item", [""])[0]
        locale = resolve_locale_preference(self.request_locale_pref(), self.headers.get("Accept-Language", ""))
        title = html.escape(server_string(locale, "pane.popout.title", name=raw_item or server_string(locale, "app.documentTitle")))
        body = f"""<!doctype html>
<html {html_lang_dir_attrs(locale)}>
<head>
  <meta charset="utf-8">
  {MOBILE_VIEWPORT_META}
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
        return FilesystemHttpAdapter.read_request_body(self, max_length, allow_empty=allow_empty, allow_missing=allow_missing, missing_message=missing_message, invalid_message=invalid_message, empty_message=empty_message, too_large_message=too_large_message, missing_status=missing_status, invalid_status=invalid_status, empty_status=empty_status, too_large_status=too_large_status, close_on_too_large=close_on_too_large)

    def read_json_body(self, max_length: int, *, allow_empty: bool = False, allow_missing: bool = False) -> dict[str, Any] | None:
        # FilesystemHttpAdapter.read_json_body routes through read_request_body on this facade.
        return FilesystemHttpAdapter.read_json_body(self, max_length, allow_empty=allow_empty, allow_missing=allow_missing)

    def handle_fs_write(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_write(self, parsed)

    def handle_fs_delete(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_delete(self, parsed)

    def handle_fs_unindex(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_unindex(self, parsed)

    def handle_fs_rename(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_rename(self, parsed)

    def handle_fs_mkdir(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_mkdir(self, parsed)

    def do_POST(self) -> None:
        dispatch_http_route(self, "POST")

    def request_base_url(self, scheme: str | None = None) -> str:
        host = str(self.headers.get("Host") or self.server.server_name_with_port()).strip()
        if not host or "\r" in host or "\n" in host:
            host = self.server.server_name_with_port()
        scheme_text = str(scheme or "").strip().lower()
        url_scheme = scheme_text if scheme_text in {"http", "https"} else "https" if self.request_is_https() else "http"
        return f"{url_scheme}://{host}"

    def handle_fs_batch(self, parsed: Any) -> None:
        return FilesystemHttpAdapter.handle_fs_batch(self, parsed)

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
            return error_payload(
                "invalid JSON",
                message_key="request.error.invalidJson",
                diagnostic=exc,
                status=HTTPStatus.BAD_REQUEST,
            ), HTTPStatus.BAD_REQUEST
        if not isinstance(event, dict):
            return error_payload(
                "event must be an object",
                message_key="request.error.object",
                message_params={"field": "event"},
                status=HTTPStatus.BAD_REQUEST,
            ), HTTPStatus.BAD_REQUEST
        return self.server.app.client_event(event)

    def file_transfer_max_bytes(self) -> int:
        return FilesystemHttpAdapter.file_transfer_max_bytes(self)

    def handle_upload(self, session: str, *, editor_path: str = "", base_dir: str = "") -> tuple[dict[str, Any], HTTPStatus]:
        # FilesystemHttpAdapter.handle_upload routes through read_request_body on this facade.
        return FilesystemHttpAdapter.handle_upload(self, session, editor_path=editor_path, base_dir=base_dir)

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
            sessions = self.server.app.sessions
            data = html_page(
                sessions,
                self.auth_identity().role,
                dev=getattr(self.server, 'dev', False),
                dangerously_yolo=self.server.app.dangerously_yolo,
                accept_language=self.headers.get("Accept-Language", ""),
                auth_username=self.auth_identity().username,
                recent_sessions=self.server.app.tmux_recency_ordered_sessions(sessions),
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_auth_cookie_if_needed()
            self.end_headers()
            self.record_http_response_bytes(HTTPStatus.OK, 0, "text/html; charset=utf-8")
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()
        self.record_http_response_bytes(HTTPStatus.NOT_FOUND, 0)

    def dev_bundle_signature(self) -> str:
        return yolomux_dev_bundle_revision()

    def stream_dev_reload(self, client_bundle_revision: str = "") -> None:
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
            # Old clients did not identify their bundle. Treat that as stale once, so the reload
            # listener already present in the old bundle repairs it after a server restart.
            if str(client_bundle_revision or "") != last:
                self.write_sse_json("reload", {"signature": last})
            while True:
                time.sleep(DEV_RELOAD_POLL_SECONDS)
                current = self.dev_bundle_signature()
                if current != last:
                    last = current
                    self.write_sse_json("reload", {"signature": current})
        except OSError:
            return

    def client_event_peer_disconnected(self) -> bool:
        """Detect a client read-side close even while SSE writes still succeed."""
        connection = self.connection
        try:
            readable, _, exceptional = select.select([connection], [], [connection], 0)
        except (OSError, ValueError):
            return True
        if exceptional:
            return True
        if not readable:
            return False
        previous_timeout = connection.gettimeout()
        try:
            connection.setblocking(False)
            flags = 0 if isinstance(connection, ssl.SSLSocket) else socket.MSG_PEEK
            return connection.recv(1, flags) == b""
        except (BlockingIOError, InterruptedError, ssl.SSLWantReadError):
            return False
        except OSError:
            return True
        finally:
            try:
                connection.settimeout(previous_timeout)
            except OSError:
                pass

    def stream_client_events(
        self,
        channels: str = "",
        client_id: str = "",
        operation_id: str = "",
        replay_operation_ids: tuple[str, ...] = (),
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_auth_cookie_if_needed()
        self.end_headers()
        subscriber_id, _subscriber_queue = self.server.app.client_events.subscribe(
            channels=channels or None,
            client_id=client_id,
        )
        if hasattr(self.server.app, "start_client_event_watcher"):
            self.server.app.start_client_event_watcher()
        if hasattr(self.server.app, "wake_client_event_watcher"):
            self.server.app.wake_client_event_watcher()
        demanded_operation_ids = {value for value in (operation_id, *replay_operation_ids) if value}
        global_operation_stream = bool(client_id) and not operation_id

        def write_operation_terminal(event_payload: dict[str, Any]) -> None:
            self.write_sse_json("operation_terminal", {
                "type": "operation_terminal",
                "time": time.time(),
                "payload": event_payload,
            })

        try:
            client_event_snapshot = self.server.app.client_events.ready_snapshot(subscriber_id)
            self.write_sse_json("ready", {
                "time": time.time(),
                "epoch": client_event_snapshot["epoch"],
                "resource_revisions": client_event_snapshot["resource_revisions"],
            })
            if operation_id and hasattr(self.server.app, "operation_replay_payload"):
                replay = self.server.app.operation_replay_payload(operation_id)
                if isinstance(replay, dict):
                    write_operation_terminal(replay)
            if hasattr(self.server.app, "operation_replay_payload"):
                for replay_operation_id in replay_operation_ids:
                    if replay_operation_id == operation_id:
                        continue
                    replay = self.server.app.operation_replay_payload(replay_operation_id)
                    if isinstance(replay, dict):
                        write_operation_terminal(replay)
            next_heartbeat_at = time.monotonic() + CLIENT_EVENT_HEARTBEAT_SECONDS
            while True:
                try:
                    event = self.server.app.client_events.next_event(
                        subscriber_id,
                        timeout=min(
                            CLIENT_EVENT_DISCONNECT_POLL_SECONDS,
                            max(0.0, next_heartbeat_at - time.monotonic()),
                        ),
                    )
                except queue.Empty:
                    event = None
                if self.client_event_peer_disconnected():
                    return
                if time.monotonic() >= next_heartbeat_at:
                    if hasattr(self.server.app, "touch_client_watch_descriptor"):
                        self.server.app.touch_client_watch_descriptor(client_id)
                    self.write_sse_json("ping", {"time": time.time()})
                    self.server.app.client_events.record_heartbeat()
                    next_heartbeat_at = time.monotonic() + CLIENT_EVENT_HEARTBEAT_SECONDS
                if event is None:
                    continue
                if str(event.get("type") or "") == "operation_terminal":
                    event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                    event_operation = event_payload.get("operation") if isinstance(event_payload.get("operation"), dict) else {}
                    if not global_operation_stream and str(event_operation.get("id") or "") not in demanded_operation_ids:
                        continue
                    write_operation_terminal(event_payload)
                    continue
                if operation_id:
                    continue
                self.write_sse_json(str(event.get("type") or "event"), event)
        except OSError:
            return
        finally:
            self.server.app.client_events.unsubscribe(subscriber_id)
            if hasattr(self.server.app, "client_event_subscriber_disconnected"):
                self.server.app.client_event_subscriber_disconnected(client_id)
            if hasattr(self.server.app, "wake_client_event_watcher"):
                self.server.app.wake_client_event_watcher()
            if hasattr(self.server.app, "stop_client_event_watcher_if_idle"):
                self.server.app.stop_client_event_watcher_if_idle()

    def stream_stats_current(
        self,
        raw_query: str,
        *,
        authenticated_username: str,
    ) -> None:
        try:
            cursor = stats_current_http.parse_http_snapshot_query(raw_query)
        except stats_current_protocol.UnsupportedRequest as error:
            self.write_json(error.response, status=HTTPStatus.BAD_REQUEST)
            return
        result = self.server.app.stats_current_http.snapshot_stream(
            raw_query,
            authenticated_username=authenticated_username,
        )
        persistent_stream = result.status in {HTTPStatus.OK, HTTPStatus.NOT_MODIFIED}
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive" if persistent_stream else "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_auth_cookie_if_needed()
        self.end_headers()
        if result.status == HTTPStatus.ACCEPTED:
            self.write_sse_json("pending", result.metadata, event_id=stats_stream_emit_id())
            self.close_connection = True
            return
        if result.status == HTTPStatus.UPGRADE_REQUIRED:
            self.write_sse_json("upgrade_required", result.metadata, event_id=stats_stream_emit_id())
            self.close_connection = True
            return
        if result.status not in {HTTPStatus.OK, HTTPStatus.NOT_MODIFIED}:
            self.write_sse_json("unavailable", result.metadata, event_id=stats_stream_emit_id())
            self.close_connection = True
            return

        cache_generation = int(result.metadata.get("cache_generation") or 0)
        chunk_count = int(result.metadata.get("chunk_count") or 1)
        self.write_sse_json("ack", {
            "cache_generation": cache_generation,
            "chunk_count": chunk_count,
            "not_modified": result.status == HTTPStatus.NOT_MODIFIED,
            "range_seconds": cursor.range_seconds,
            "requested_resolution": cursor.resolution,
            "resolution_seconds": cursor.resolution_seconds,
        }, event_id=stats_stream_emit_id())
        if result.status == HTTPStatus.OK:
            self.write_sse_bytes("snapshot", result.body, event_id=stats_stream_emit_id())
            for chunk_index in range(1, chunk_count):
                chunk_query = urlencode({
                    "range_seconds": cursor.range_seconds,
                    "resolution": cursor.resolution,
                    "client_id": cursor.client_id,
                    "since_generation": cursor.since_generation or 0,
                    "chunk_index": chunk_index,
                    "chunk_generation": cache_generation,
                })
                chunk = self.server.app.stats_current_http.snapshot_stream(
                    chunk_query,
                    authenticated_username=authenticated_username,
                )
                if chunk.status != HTTPStatus.OK:
                    event = "pending" if chunk.status == HTTPStatus.ACCEPTED else "unavailable"
                    self.write_sse_json(event, chunk.metadata, event_id=stats_stream_emit_id())
                    return
                self.write_sse_bytes("snapshot", chunk.body, event_id=stats_stream_emit_id())
        self.write_sse_json("ready", {
            "cache_generation": cache_generation,
            "revision": 0,
        }, event_id=stats_stream_emit_id())

        revision_number = 0
        cadence_seconds = stats_current_protocol.live_cadence_seconds(
            cursor.resolution_seconds,
        )
        next_deadline = time.monotonic() + cadence_seconds
        stream_started_at = time.monotonic()
        last_anomalous_status = 0
        client_key = str(cursor.client_id or "")
        try:
            while True:
                if self.server.persistent_request_stop.wait(
                    max(0.0, next_deadline - time.monotonic())
                ):
                    return
                now = time.monotonic()
                scheduled_deadline = next_deadline
                while next_deadline <= now:
                    next_deadline += cadence_seconds
                # The browser's stall watchdog can only report that nothing arrived; it cannot
                # say whether this emit loop stopped producing. A tick that woke a whole cadence
                # late means the frame the browser was waiting for was never produced here.
                slip_seconds = now - scheduled_deadline
                if slip_seconds >= cadence_seconds:
                    _record_stats_stream_boundary(
                        "frame_production",
                        "tick-late",
                        client_key,
                        cadence_seconds,
                        {
                            "slip_seconds": round(slip_seconds, 3),
                            "cache_generation": cache_generation,
                            "revision": revision_number,
                            "stream_age_seconds": round(now - stream_started_at, 3),
                        },
                    )
                query = urlencode({
                    "range_seconds": cursor.range_seconds,
                    "resolution_seconds": cursor.resolution_seconds,
                    "client_id": cursor.client_id,
                    "after_cache_generation": cache_generation,
                    "after_revision": revision_number,
                })
                rpc_started_at = time.monotonic()
                result = self.server.app.stats_current_http.delta_stream(
                    query,
                    authenticated_username=authenticated_username,
                )
                rpc_seconds = time.monotonic() - rpc_started_at
                # An RPC that outruns the cadence is the statsd boundary going quiet, not this
                # loop and not the transport, so it separates the two upstream suspects.
                if rpc_seconds >= cadence_seconds:
                    _record_stats_stream_boundary(
                        "statsd_delta_rpc",
                        "rpc-slow",
                        client_key,
                        cadence_seconds,
                        {
                            "rpc_seconds": round(rpc_seconds, 3),
                            "status": int(result.status),
                            "cache_generation": cache_generation,
                            "revision": revision_number,
                        },
                    )
                # OK and NOT_MODIFIED alternate on a healthy stream, so recording every tick
                # would flood the bounded ring. Only entry into an unusual status is retained.
                if result.status not in {HTTPStatus.OK, HTTPStatus.NOT_MODIFIED}:
                    if int(result.status) != last_anomalous_status:
                        last_anomalous_status = int(result.status)
                        _record_stats_stream_boundary(
                            "delta_stream_status",
                            "status-change",
                            client_key,
                            cadence_seconds,
                            {
                                "status": int(result.status),
                                "cache_generation": cache_generation,
                                "revision": revision_number,
                            },
                        )
                else:
                    last_anomalous_status = 0
                if result.status == HTTPStatus.CONFLICT:
                    self.write_sse_json("repair", result.metadata, event_id=stats_stream_emit_id())
                    _record_stats_stream_boundary(
                        "frame_production",
                        "repair",
                        client_key,
                        cadence_seconds,
                        {
                            "cache_generation": cache_generation,
                            "revision": revision_number,
                            "stream_age_seconds": round(time.monotonic() - stream_started_at, 3),
                        },
                    )
                    return
                if result.status not in {
                    HTTPStatus.OK,
                    HTTPStatus.NOT_MODIFIED,
                    HTTPStatus.ACCEPTED,
                }:
                    self.write_sse_json("unavailable", result.metadata, event_id=stats_stream_emit_id())
                    _record_stats_stream_boundary(
                        "frame_production",
                        "unavailable",
                        client_key,
                        cadence_seconds,
                        {
                            "status": int(result.status),
                            "cache_generation": cache_generation,
                            "revision": revision_number,
                            "stream_age_seconds": round(time.monotonic() - stream_started_at, 3),
                        },
                    )
                    return
                if result.status == HTTPStatus.OK:
                    self.write_sse_bytes("delta", result.body, event_id=stats_stream_emit_id())
                    cache_generation = int(result.metadata["cache_generation"])
                    revision_number = int(result.metadata["revision"])
                elif result.status == HTTPStatus.NOT_MODIFIED:
                    cache_generation = int(
                        result.metadata.get("cache_generation") or cache_generation
                    )
                    self.write_sse_json("ready", {
                        "cache_generation": cache_generation,
                        "revision": revision_number,
                    }, event_id=stats_stream_emit_id())
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            return

    def stream_context_items(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = str(query_one(qs, "session", "") or "")
        messages, error = parse_query_int(qs, "messages", 40, max_value=MAX_COMPACT_TRANSCRIPT_ITEMS)
        if error:
            self.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
            return
        message_limit = max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS))
        payload, status = self.server.app.context_items(session, message_limit, accept_pending=False)
        if status != HTTPStatus.OK:
            self.write_json(payload, status=status)
            return
        path_text = payload.get("path")
        items = payload.get("items")
        if not isinstance(path_text, str) or not isinstance(items, list):
            diagnostic = "missing transcript items"
            self.write_json(
                {"session": session, **user_message_payload("transcript.error.missingText", diagnostic)},
                status=HTTPStatus.NOT_FOUND,
            )
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
                    "items": items,
                    "pending": bool(payload.get("pending")),
                    "stale": bool(payload.get("stale")),
                    "agent": payload.get("agent"),
                    "errors": payload.get("errors", []),
                },
            )
            self.follow_transcript_file(path)
        except OSError:
            return

    def stream_codex_summary(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = str(query_one(qs, "session", "") or "")
        summary_settings = self.server.app.summary_settings()
        default_lookback = int(summary_settings.get("lookback_seconds") or SUMMARY_DEFAULT_LOOKBACK_SECONDS)
        lookback_seconds, error = parse_query_int(qs, "lookback", default_lookback, max_value=24 * 3600)
        if error:
            self.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
            return
        unknown = self.server.app.require_known_session(session)
        if unknown:
            payload, status = unknown
            self.write_json(payload, status=status)
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
            diagnostic = "missing Codex prompt"
            self.write_json(
                {"session": session, **user_message_payload("summary.error.missingPrompt", diagnostic)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
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
            message_key="events.message.summary.started",
        )
        try:
            self.write_sse_json("meta", meta)
            self.run_codex_summary(prompt, summary_settings)
            self.server.app.log_event(
                session,
                "summary_finished",
                "AI summary finished",
                {"model": summary_settings["codex_model"]},
                message_key="events.message.summary.finished",
            )
        except OSError:
            self.server.app.log_event(
                session,
                "summary_disconnected",
                "AI summary stream disconnected",
                {},
                message_key="events.message.summary.disconnected",
            )
            return

    def codex_summary_availability_error(self, summary_settings: dict[str, Any]) -> dict[str, Any] | None:
        provider = str(summary_settings.get("backend") or "").strip().lower()
        if provider != "codex":
            diagnostic = "AI summary provider is disabled"
            return {
                **user_message_payload("summary.error.providerDisabled", diagnostic),
                "provider": provider or "disabled",
            }
        status = agent_auth_status()
        codex_status = status.get("codex") if isinstance(status, dict) else {}
        codex_status = codex_status if isinstance(codex_status, dict) else {}
        if not codex_status.get("installed"):
            diagnostic = "Codex summary provider is unavailable because the codex CLI is not on PATH"
            return {
                **user_message_payload("summary.error.codexUnavailable", diagnostic),
                "provider": "codex",
                "login_command": AGENT_LOGIN_COMMANDS["codex"],
            }
        if not agent_auth_entry_available(codex_status):
            command = AGENT_LOGIN_COMMANDS["codex"]
            diagnostic = f"Codex summary provider is unavailable because the codex CLI is not logged in. Run `{command}`."
            return {
                **user_message_payload("summary.error.codexLoginRequired", diagnostic, command=command),
                "provider": "codex",
                "login_command": command,
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
        previous_usage_context = getattr(self, "_codex_summary_usage_context", None)
        self._codex_summary_usage_context = {
            "model": str(summary_settings.get("codex_model") or "").strip(),
            "effort": str(summary_settings.get("codex_effort") or "").strip() or "unknown",
            "service_tier": str(summary_settings.get("codex_service_tier") or "").strip() or "default",
        }
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
            record_owned_process_group(process)
            if process.stdin is None or process.stdout is None:
                diagnostic = "failed to open Codex pipes"
                self.write_sse_json("summary_error", user_message_payload("summary.error.openPipes", diagnostic))
                return
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
            self.stream_codex_process(process, timeout_seconds=summary_settings.get("timeout_seconds"))
        except OSError as exc:
            diagnostic = str(exc)
            self.write_sse_json("summary_error", user_message_payload("summary.error.runtime", diagnostic, error=diagnostic))
        finally:
            if previous_usage_context is None:
                self.__dict__.pop("_codex_summary_usage_context", None)
            else:
                self._codex_summary_usage_context = previous_usage_context
            if process is not None:
                terminate_process_group(process)

    def stream_codex_process(self, process: subprocess.Popen[bytes], timeout_seconds: Any = SUMMARY_DEFAULT_CODEX_TIMEOUT_SECONDS) -> None:
        if process.stdout is None:
            diagnostic = "missing Codex stdout"
            self.write_sse_json("summary_error", user_message_payload("summary.error.missingStdout", diagnostic))
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
                diagnostic = "Codex summary timed out"
                self.write_sse_json("summary_error", user_message_payload("summary.error.timedOut", diagnostic))
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
            self.record_codex_summary_usage(event)
            return
        if event_kind == "error":
            diagnostic = json.dumps(event, ensure_ascii=False)
            self.write_sse_json("summary_error", user_message_payload("summary.stream.failed", diagnostic))
            return

        text = codex_event_text(event)
        if text:
            self.write_sse_json("delta", {"text": text})

    def record_codex_summary_usage(self, event: dict[str, Any]) -> None:
        """Submit only direct structured completion usage, never summary text."""
        context = getattr(self, "_codex_summary_usage_context", None)
        if not isinstance(context, dict):
            return
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        if not usage:
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        if not usage:
            return
        event_identity = str(event.get("id") or event.get("turn_id") or event.get("turnId") or "summary")
        digest = hashlib.sha256(json.dumps(usage, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
        try:
            self.server.app.record_owned_usage_atoms(
                provider="openai",
                model=str(context.get("model") or ""),
                usage=usage,
                source="AI Summary",
                event_id=f"ai-summary:{event_identity}:{digest}",
                effort=str(context.get("effort") or "unknown"),
                service_tier=str(context.get("service_tier") or "default"),
                endpoint="codex-exec",
            )
        except (AttributeError, OSError, RuntimeError, ValueError):
            # Cost telemetry must not turn a successfully generated summary
            # into a failed user-visible summary response.
            return

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

    def write_sse_json(self, event: str, value: Any, *, event_id: str = "") -> None:
        return ApiResponseWriter.write_sse_json(self, event, value, event_id=event_id)

    def write_sse_bytes(self, event: str, value: bytes, *, event_id: str = "") -> None:
        return ApiResponseWriter.write_sse_bytes(self, event, value, event_id=event_id)

    def write_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        return ApiResponseWriter.write_html(self, body, status)

    def write_redirect(self, location: str, status: HTTPStatus = HTTPStatus.SEE_OTHER, clear_auth: bool = False) -> None:
        return ApiResponseWriter.write_redirect(self, location, status, clear_auth)

    def write_static_asset(self, asset: str, content_type: str) -> None:
        return ApiResponseWriter.write_static_asset(self, asset, content_type)

    def write_static_head(self, asset: str, content_type: str) -> None:
        return ApiResponseWriter.write_static_head(self, asset, content_type)

    def api_request_id(self) -> str:
        return ApiResponseWriter.api_request_id(self)

    def write_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        return ApiResponseWriter.write_json(self, value, status)

    def write_json_bytes(self, data: bytes, status: HTTPStatus = HTTPStatus.OK, *, json_encode_ms: float = 0.0) -> None:
        return ApiResponseWriter.write_json_bytes(self, data, status, json_encode_ms=json_encode_ms)

    def write_product_bytes(
        self,
        data: bytes,
        product: ProductMetadata,
        *,
        promise: tuple[str, int] | None = None,
    ) -> None:
        return ApiResponseWriter.write_product_bytes(self, data, product, promise=promise)

    def write_api_response(
        self,
        value: Any,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_bytes: bool = False,
        product_metadata: ProductMetadata | None = None,
        product_promise: tuple[str, int] | None = None,
        json_encode_ms: float = 0.0,
    ) -> None:
        return ApiResponseWriter.write_api_response(self, value, status, json_bytes=json_bytes, product_metadata=product_metadata, product_promise=product_promise, json_encode_ms=json_encode_ms)

    def _write_bodyless_api_response(self, status: HTTPStatus) -> None:
        return ApiResponseWriter._write_bodyless_api_response(self, status)

    def _write_json_representation(
        self,
        data: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_encode_ms: float = 0.0,
        product_metadata: ProductMetadata | None = None,
    ) -> None:
        return ApiResponseWriter._write_json_representation(self, data, status, json_encode_ms=json_encode_ms, product_metadata=product_metadata)

    def _write_product_representation(
        self,
        data: bytes,
        *,
        status: HTTPStatus,
        content_type: str,
        disposition: str,
        filename: str,
        json_encode_ms: float = 0.0,
        product_metadata: ProductMetadata | None = None,
    ) -> None:
        return ApiResponseWriter._write_product_representation(self, data, status=status, content_type=content_type, disposition=disposition, filename=filename, json_encode_ms=json_encode_ms, product_metadata=product_metadata)

    def write_app_result(self, result: tuple[Any, HTTPStatus]) -> None:
        return ApiResponseWriter.write_app_result(self, result)

    def write_validated_int_result(self, qs: dict, name: str, default: int, max_value: int, make_result) -> None:
        return ApiResponseWriter.write_validated_int_result(self, qs, name, default, max_value, make_result)

    def write_validated_float_result(self, qs: dict, name: str, default: float, max_value: float, make_result) -> None:
        return ApiResponseWriter.write_validated_float_result(self, qs, name, default, max_value, make_result)

    def write_int_query_app_result(self, parsed: Any, name: str, default: int, max_value: int, make_result) -> None:
        return ApiResponseWriter.write_int_query_app_result(self, parsed, name, default, max_value, make_result)

    def write_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        return ApiResponseWriter.write_text(self, body, status)

    def websocket(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = qs.get("session", [""])[0]
        resize_client_id = clean_resize_authority_client_id(qs.get("client", [""])[0])
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
        # A successful upgrade owns this connection until the WebSocket bridge returns.
        # Never let BaseHTTPRequestHandler parse subsequent masked frame bytes as HTTP.
        self.close_connection = True
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.send_auth_cookie_if_needed()
        self.end_headers()
        return True

    def bridge_tmux(self, session: str, readonly: bool = False, resize_client_id: str = "") -> None:
        try:
            initial_rows, initial_cols, saw_initial_resize, pending_payloads = self.read_initial_ws_payloads()
        except OSError:
            return
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
        authority_claim_pending = not readonly and saw_initial_resize

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
            record_owned_process_group(attached)
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
                self.server.claim_resize_authority(
                    session, tmux_client_name, resize_client_id, initial_cols, initial_rows,
                )
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
                    connection_ready = wait_for_ws_frame(self.connection, self.rfile, 0)
                    readers = [master_fd] if connection_ready else [master_fd, self.connection]
                    readable, _, _ = select.select(readers, [], [], 0.1)
                    if master_fd in readable:
                        # Popen can return before tmux exposes the new client in list-clients, so the
                        # eager claim above may miss. Readable PTY output proves the attach exists;
                        # claim once more at that transition before forwarding its first frame.
                        if authority_claim_pending:
                            self.server.claim_resize_authority(
                                session,
                                tmux_client_name,
                                resize_client_id,
                                resize_state["cols"],
                                resize_state["rows"],
                            )
                            authority_claim_pending = False
                        data = os.read(master_fd, 65536)
                        if not data:
                            break
                        self.connection.sendall(make_ws_frame(data, opcode=2))
                    if connection_ready or self.connection in readable:
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
                    authority_claim_pending = not readonly
                    continue
                break
        except OSError:
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

    def read_initial_ws_payloads(self) -> tuple[int, int, bool, list[bytes]]:
        rows = DEFAULT_ROWS
        cols = DEFAULT_COLS
        saw_resize = False
        pending_payloads: list[bytes] = []
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            timeout = max(0.0, deadline - time.monotonic())
            if not wait_for_ws_frame(self.connection, self.rfile, timeout):
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
        if readonly and msg_type != "refresh":
            return
        if msg_type == "refresh":
            refresh_tmux_session_clients(session)
            # A refresh carrying a window-switch transaction id gets a structured TEXT-frame
            # acknowledgement after the refresh is issued, so the browser's post-confirmation
            # paint barrier can wait for a subsequent refreshed BINARY frame. Normal PTY output
            # stays on binary frames (opcode 2); legacy refreshes without an id stay silent.
            txn = message.get("txn")
            if isinstance(txn, (int, float)) and not isinstance(txn, bool) and txn > 0:
                ack = json.dumps({"type": "refresh-ack", "txn": int(txn)}, separators=(",", ":")).encode("utf-8")
                try:
                    self.connection.sendall(make_ws_frame(ack, opcode=1))
                except OSError:
                    pass
        elif msg_type == "input":
            data = message.get("data")
            if isinstance(data, str):
                filtered = strip_terminal_query_responses(data)
                if filtered:
                    os.write(master_fd, filtered.encode("utf-8"))
                    # Queue input activity outside the PTY echo loop (readonly already returned above).
                    self.server.app.record_user_input(session, len(filtered), data=filtered)
        elif msg_type == "resize":
            if message.get("foreground") is False:
                return
            dimensions = ws_resize_dimensions(message, DEFAULT_ROWS, DEFAULT_COLS)
            if dimensions:
                rows, cols = dimensions
                authority_changed = False
                if message.get("foreground") is True or message.get("activate") is True:
                    claimer = getattr(self.server, "claim_resize_authority", None)
                    if callable(claimer):
                        authority_changed = bool(claimer(session, tmux_client_name, resize_client_id, cols, rows))
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
            direction = message.get("direction")
            lines = message.get("lines")
            if isinstance(direction, str) and isinstance(lines, int):
                self.server.app.tmux_scroll(session, direction, lines)


TLS_FIRST_BYTES = {0x16, 0x80}
HTTP_METHOD_PREFIXES = (b"GET ", b"HEAD ", b"POST ", b"PUT ", b"DELETE ", b"OPTIONS ", b"PATCH ", b"TRACE ", b"CONNECT ")


def https_redirect_body(location: str, locale: str = "en") -> bytes:
    return (server_string(locale, "server.useHttps", location=location) + "\n").encode("utf-8")


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
    body = https_redirect_body(location)
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
        self.persistent_request_stop = threading.Event()
        if hasattr(self.app, "start_input_heartbeat_worker"):
            self.app.start_input_heartbeat_worker()
        if hasattr(self.app, "start_tabber_activity_cache_warmer"):
            self.app.start_tabber_activity_cache_warmer()
        if hasattr(self.app, "start_update_check_thread"):
            self.app.start_update_check_thread()
        # The system-status snapshot producer belongs to the serving process, not to background
        # ownership: every server answers /api/system-status about itself, owner or not.
        if hasattr(self.app, "start_system_status_snapshot_owner"):
            self.app.start_system_status_snapshot_owner()
        start_agent_auth_status_refresh(force=True)

    def shutdown(self) -> None:
        self.persistent_request_stop.set()
        # Retire the snapshot producer with the first shutdown signal, not at close: a build that
        # started after a fixture sealed local-service starts would look like the product starting
        # a service during teardown. `stop` is idempotent, so `server_close` may call it again.
        if hasattr(self.app, "stop_system_status_snapshot_owner"):
            self.app.stop_system_status_snapshot_owner()
        super().shutdown()

    def server_close(self) -> None:
        if hasattr(self, "persistent_request_stop"):
            self.persistent_request_stop.set()
        if hasattr(self, "app") and hasattr(self.app, "stop_jobd_operation_service"):
            self.app.stop_jobd_operation_service()
        if hasattr(self, "app") and hasattr(self.app, "stop_client_event_watcher"):
            self.app.stop_client_event_watcher()
        if hasattr(self, "app") and hasattr(self.app, "stop_input_heartbeat_worker"):
            self.app.stop_input_heartbeat_worker()
        if hasattr(self, "app") and hasattr(self.app, "stop_system_status_snapshot_owner"):
            self.app.stop_system_status_snapshot_owner()
        super().server_close()

    def record_host_pty_dimensions(self, session: str, rows: int, cols: int) -> None:
        clean_session = str(session or "")
        if not clean_session:
            return
        dimensions = (clamp_pty_dimension(rows), clamp_pty_dimension(cols))
        with self.host_pty_dimensions_lock:
            self.host_pty_dimensions[clean_session] = dimensions

    def host_pty_dimensions_for_session(self, session: str) -> tuple[int, int]:
        with self.host_pty_dimensions_lock:
            return self.host_pty_dimensions.get(str(session or ""), (DEFAULT_ROWS, DEFAULT_COLS))

    def claim_resize_authority(
        self,
        session: str,
        tmux_client_name: str,
        resize_client_id: str = "",
        active_cols: int | None = None,
        active_rows: int | None = None,
    ) -> bool:
        del resize_client_id
        return claim_tmux_resize_authority(session, tmux_client_name, active_cols, active_rows)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        return self.socket.accept()

    def prepare_request_socket(self, request: socket.socket) -> socket.socket:
        if not self.tls_context or isinstance(request, ssl.SSLSocket):
            return request
        # Tests may retain a context-shaped redirect sentinel so Handler can issue HTTP policy
        # responses. Only a real SSLContext is authorized to transform a connection.
        if not isinstance(self.tls_context, ssl.SSLContext):
            return request
        previous_timeout = request.gettimeout()
        request.settimeout(self.tls_peek_timeout_seconds)
        try:
            first = request.recv(1, socket.MSG_PEEK)
        except (socket.timeout, BlockingIOError):
            # No protocol byte means this is only an idle preconnect, not evidence of TLS. Wrapping it
            # guessed wrong for delayed plaintext preconnects and killed later HTTP requests. Definite
            # TLS records still take the branch below; a real client has sent its first byte by this
            # deliberately generous classifier timeout.
            request.settimeout(previous_timeout)
            return request
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
        if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            host = client_address[0] if client_address else "unknown"
            error_name = type(error).__name__
            sys.stderr.write(f"{host} - - client disconnected: {error_name}\n")
            self.app.record_performance_sample(
                "http-endpoint",
                "expected-disconnect",
                trigger=error_name,
                count=1,
                details={"client": host},
            )
            return
        if isinstance(error, ssl.SSLError):
            host = client_address[0] if client_address else "unknown"
            reason = getattr(error, "reason", None) or str(error)
            sys.stderr.write(f"{host} - - TLS handshake closed: {reason}\n")
            return
        super().handle_error(request, client_address)

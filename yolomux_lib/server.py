from __future__ import annotations

import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qsl

from .app import TmuxWebtermApp
from .core import *
from . import filesystem
from .filesystem import FilesystemError
from .web import html_page
from .web import login_html
from .web import setup_auth_html
from .web import static_asset_path
from .web import static_content_type


def content_disposition_attachment(raw_path: str) -> str:
    name = Path(str(raw_path or "")).name or "download"
    safe = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";", "/"} else "_" for char in name).strip()
    return f'attachment; filename="{safe or "download"}"'


class Handler(BaseHTTPRequestHandler):
    server: "TmuxWebtermHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def basic_auth_identity(self) -> AuthIdentity | None:
        header = self.headers.get("Authorization", "")
        scheme, separator, encoded = header.partition(" ")
        if not separator or scheme.lower() != "basic":
            return None
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        username, separator, password = decoded.partition(":")
        if not separator:
            return None
        identity = auth_identity_for_credentials(username, password)
        if identity is not None:
            return identity
        return None

    def cookie_auth_identity(self) -> AuthIdentity | None:
        cookie_name = self.auth_cookie_name()
        for item in self.headers.get("Cookie", "").split(";"):
            name, separator, value = item.strip().partition("=")
            if not separator or name != cookie_name:
                continue
            for user in current_auth_users():
                expected = auth_cookie_value(user.username, user.password)
                if hmac.compare_digest(value, expected):
                    return AuthIdentity(username=user.username, password=user.password, role=user.role)
        return None

    def has_logout_marker(self) -> bool:
        for item in self.headers.get("Cookie", "").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == AUTH_LOGOUT_COOKIE_NAME and value == "1":
                return True
        return False

    def auth_cookie_name(self) -> str:
        return f"{AUTH_COOKIE_NAME}_{self.server.server_address[1]}"

    def auth_cookie_suffix(self) -> str:
        secure = "; Secure" if self.request_is_https() else ""
        return f"; Path=/; Max-Age={AUTH_COOKIE_MAX_AGE_SECONDS}; HttpOnly; SameSite=Lax{secure}"

    def auth_cookie_header(self, identity: AuthIdentity) -> str:
        for user in current_auth_users():
            if user.username == identity.username and user.password == identity.password:
                return f"{self.auth_cookie_name()}={auth_cookie_value(user.username, user.password)}{self.auth_cookie_suffix()}"
        return self.clear_auth_cookie_header()

    def clear_auth_cookie_header(self, cookie_name: str | None = None, secure: bool | None = None) -> str:
        name = cookie_name or self.auth_cookie_name()
        use_secure = self.request_is_https() if secure is None else secure
        secure_attr = "; Secure" if use_secure else ""
        return f"{name}=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax{secure_attr}"

    def clear_auth_cookie_headers(self) -> list[str]:
        names = [self.auth_cookie_name(), AUTH_COOKIE_NAME]
        headers = []
        for name in dict.fromkeys(names):
            headers.append(self.clear_auth_cookie_header(name, secure=False))
            if self.request_is_https():
                headers.append(self.clear_auth_cookie_header(name, secure=True))
        return headers

    def logout_marker_cookie_header(self) -> str:
        secure = "; Secure" if self.request_is_https() else ""
        return f"{AUTH_LOGOUT_COOKIE_NAME}=1; Path=/; Max-Age={AUTH_COOKIE_MAX_AGE_SECONDS}; HttpOnly; SameSite=Lax{secure}"

    def clear_logout_marker_cookie_header(self) -> str:
        secure = "; Secure" if self.request_is_https() else ""
        return f"{AUTH_LOGOUT_COOKIE_NAME}=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax{secure}"

    def request_is_https(self) -> bool:
        return self.server.tls_context is not None

    def send_auth_cookie_if_needed(self) -> None:
        identity = getattr(self, "_auth_cookie_identity", None)
        if identity is not None:
            self.send_header("Set-Cookie", self.auth_cookie_header(identity))
            self.send_header("Set-Cookie", self.clear_logout_marker_cookie_header())

    def request_has_unread_body(self) -> bool:
        return self.command in {"POST", "PUT", "PATCH"} and bool(self.headers.get("Content-Length"))

    def close_after_unread_body(self) -> None:
        if self.request_has_unread_body():
            self.close_connection = True

    def role_allows(self, identity: AuthIdentity, required_role: str) -> bool:
        if required_role == "readonly":
            return identity.role in {"admin", "readonly"}
        if required_role == "admin":
            return identity.role == "admin"
        return False

    def reject_forbidden(self, identity: AuthIdentity, required_role: str) -> None:
        self.close_after_unread_body()
        self.write_json({"error": f"{required_role} access required", "role": identity.role}, status=HTTPStatus.FORBIDDEN)

    def safe_next_path(self, value: str | None) -> str:
        text = str(value or "/").strip()
        if not text.startswith("/") or text.startswith("//") or "\r" in text or "\n" in text:
            return "/"
        return text

    def login_url(self, next_path: str | None = None) -> str:
        next_value = self.safe_next_path(next_path or self.path)
        return f"/login?{urlencode({'next': next_value})}"

    def login_success_path(self, next_path: str | None) -> str:
        safe = self.safe_next_path(next_path)
        parsed = urlparse(safe)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "layout" and value.lower() == "empty" for key, value in params):
            return safe
        filtered = [(key, value) for key, value in params if key not in {"layout", "tabs"}]
        query = urlencode(filtered)
        path = parsed.path or "/"
        return f"{path}?{query}" if query else path

    def wants_html(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/login"}:
            return True
        accept = self.headers.get("Accept", "")
        return "text/html" in accept and "application/json" not in accept

    def reject_unauthorized(self) -> None:
        self.close_after_unread_body()
        if self.wants_html():
            self.write_redirect(self.login_url(), status=HTTPStatus.SEE_OTHER)
            return
        self.write_json(
            {"error": "authentication required", "login_url": self.login_url("/")},
            status=HTTPStatus.UNAUTHORIZED,
        )

    def require_auth(self, required_role: str = "readonly") -> bool:
        if auth_setup_required():
            self.write_html(setup_auth_html())
            return False
        self._auth_cookie_identity = None
        identity = self.cookie_auth_identity()
        if identity is not None:
            # Authenticate against the existing cookie but do not re-mint it.
            # Re-minting here races with /logout: polling requests in flight when
            # the user clicks logout would re-set the auth cookie and clear the
            # logout marker, leaking the user back into the app on refresh.
            self._auth_identity = identity
            if self.role_allows(identity, required_role):
                return True
            self.reject_forbidden(identity, required_role)
            return False
        if self.has_logout_marker():
            self._auth_identity = None
            self.reject_unauthorized()
            return False
        identity = self.basic_auth_identity()
        if identity is not None:
            self._auth_identity = identity
            self._auth_cookie_identity = identity
            if self.role_allows(identity, required_role):
                return True
            self.reject_forbidden(identity, required_role)
            return False
        self._auth_identity = None
        self.reject_unauthorized()
        return False

    def auth_identity(self) -> AuthIdentity:
        identity = getattr(self, "_auth_identity", None)
        if identity is not None:
            return identity
        return AuthIdentity(username="", password="", role="readonly")

    def auth_readonly(self) -> bool:
        return self.auth_identity().role == "readonly"

    def require_auth_for_post(self, path: str) -> bool:
        if path == "/api/event":
            return self.require_auth("readonly")
        return self.require_auth("admin")

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
        if parsed.path == "/api/ping":
            self.write_json({"ok": True, "time": time.time()})
            return
        if parsed.path == "/":
            self.write_html(html_page(self.server.app.sessions, self.auth_identity().role))
            return
        if parsed.path == "/api/transcripts":
            self.write_json(self.server.app.transcripts_payload())
            return
        if parsed.path == "/api/tmux":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            lines = int(qs.get("lines", ["90"])[0])
            payload, status = self.server.app.tmux_snapshot(session, lines)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/transcript":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            lines = int(qs.get("lines", ["120"])[0])
            payload, status = self.server.app.transcript_tail(session, lines)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/context":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            messages = int(qs.get("messages", ["40"])[0])
            payload, status = self.server.app.context_tail(session, messages)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/context-items":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            messages = int(qs.get("messages", ["40"])[0])
            payload, status = self.server.app.context_items(session, messages)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/context-stream":
            self.stream_context_items(parsed)
            return
        if parsed.path == "/api/summary-stream":
            self.stream_codex_summary(parsed)
            return
        if parsed.path == "/api/auto-approve":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [None])[0]
            payload, status = self.server.app.auto_approve_status(session)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/notify":
            self.write_json(self.server.app.notify_status())
            return
        if parsed.path == "/api/events":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [None])[0]
            try:
                limit = int(qs.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            payload, status = self.server.app.events_payload(session, limit)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/summary":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            payload, status = self.server.app.summary(session)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/fs/list":
            self.handle_fs_list(parsed)
            return
        if parsed.path == "/api/fs/read":
            self.handle_fs_read(parsed)
            return
        if parsed.path == "/api/fs/info":
            self.handle_fs_info(parsed)
            return
        if parsed.path == "/api/fs/raw":
            self.handle_fs_raw(parsed)
            return
        if parsed.path == "/ws":
            self.websocket(parsed)
            return
        self.write_text("not found\n", status=HTTPStatus.NOT_FOUND)

    def handle_fs_list(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", ["/"])[0]
        self.write_filesystem_json(raw_path, lambda: filesystem.list_directory(raw_path))

    def handle_fs_read(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        self.write_filesystem_json(raw_path, lambda: filesystem.read_file(raw_path))

    def handle_fs_info(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        self.write_filesystem_json(raw_path, lambda: filesystem.path_info(raw_path))

    def write_filesystem_json(self, raw_path: str, build_payload: Any) -> None:
        try:
            payload = build_payload()
        except FilesystemError as exc:
            self.write_json({"error": str(exc), "path": raw_path}, status=HTTPStatus(exc.status))
            return
        self.write_json(payload)

    def handle_fs_raw(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        raw_path = qs.get("path", [""])[0]
        download = qs.get("download", [""])[0] in {"1", "true", "yes"}
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
        self.write_filesystem_json(raw_path, lambda: filesystem.write_file(raw_path, content, expected_mtime=expected_mtime))

    def handle_fs_delete(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        self.write_filesystem_json(raw_path, lambda: filesystem.delete_path(raw_path))

    def handle_fs_rename(self, parsed: Any) -> None:
        payload = self.read_json_body(4096)
        if payload is None:
            return
        raw_path = payload.get("path", "")
        new_name = payload.get("new_name", "")
        self.write_filesystem_json(raw_path, lambda: filesystem.rename_path(raw_path, new_name))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self.handle_login_submit(parsed)
            return
        if not self.require_auth_for_post(parsed.path):
            return
        if parsed.path == "/api/ensure-session":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            payload, status = self.server.app.ensure_session(session)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/create-session":
            qs = parse_qs(parsed.query)
            agent = qs.get("agent", ["claude"])[0]
            payload, status = self.server.app.create_next_session(agent)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/upload":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            payload, status = self.handle_upload(session)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/auto-approve":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            enabled = parse_bool(qs.get("enabled", ["0"])[0])
            payload, status = self.server.app.set_auto_approve(session, enabled)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/notify":
            qs = parse_qs(parsed.query)
            enabled = parse_bool(qs.get("enabled", ["0"])[0])
            self.write_json(self.server.app.set_notify(enabled))
            return
        if parsed.path == "/api/tmux-next":
            qs = parse_qs(parsed.query)
            session = qs.get("session", [""])[0]
            payload, status = self.server.app.tmux_next_window(session)
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/event":
            payload, status = self.handle_client_event()
            self.write_json(payload, status=status)
            return
        if parsed.path == "/api/fs/write":
            self.handle_fs_write(parsed)
            return
        if parsed.path == "/api/fs/delete":
            self.handle_fs_delete(parsed)
            return
        if parsed.path == "/api/fs/rename":
            self.handle_fs_rename(parsed)
            return
        self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def handle_login_page(self, parsed: Any) -> None:
        if auth_setup_required():
            self.write_html(setup_auth_html())
            return
        qs = parse_qs(parsed.query)
        next_path = self.safe_next_path(qs.get("next", ["/"])[0])
        if not self.has_logout_marker() and self.cookie_auth_identity() is not None:
            self.write_redirect(self.login_success_path(next_path))
            return
        self.write_html(login_html(next_path=next_path, secure=self.request_is_https()))

    def handle_login_submit(self, parsed: Any) -> None:
        if auth_setup_required():
            self.write_html(setup_auth_html())
            return
        form = self.read_urlencoded_form()
        next_path = self.safe_next_path(form.get("next", ["/"])[0])
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        identity = auth_identity_for_credentials(username, password)
        if identity is None:
            self.close_after_unread_body()
            self.write_html(
                login_html(next_path=next_path, error="Invalid username or password.", secure=self.request_is_https()),
                status=HTTPStatus.UNAUTHORIZED,
            )
            return
        self._auth_cookie_identity = identity
        self.write_redirect(self.login_success_path(next_path))

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
        if content_length > UPLOAD_MAX_BYTES:
            self.close_connection = True
            return {
                "session": session,
                "error": f"upload is too large; limit is {UPLOAD_MAX_BYTES} bytes",
            }, HTTPStatus.REQUEST_ENTITY_TOO_LARGE

        body = self.rfile.read(content_length)
        try:
            files = parse_multipart_upload(self.headers.get("Content-Type", ""), body)
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
            data = html_page(self.server.app.sessions, self.auth_identity().role).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_auth_cookie_if_needed()
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def stream_context_items(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        session = qs.get("session", [""])[0]
        messages = int(qs.get("messages", ["40"])[0])
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
        session = qs.get("session", [""])[0]
        try:
            lookback_seconds = int(qs.get("lookback", [str(SUMMARY_LOOKBACK_SECONDS)])[0])
        except ValueError:
            lookback_seconds = SUMMARY_LOOKBACK_SECONDS

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
        args = [
            "codex",
            "exec",
            "--json",
            "-m",
            SUMMARY_CODEX_MODEL,
            "-c",
            f'model_reasoning_effort="{SUMMARY_CODEX_EFFORT}"',
            "-c",
            f'service_tier="{SUMMARY_CODEX_SERVICE_TIER}"',
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-rules",
            "--cd",
            str(repo_root),
            "-",
        ]
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
        event_type = str(event.get("type") or "")
        if event_type == "thread.started":
            self.write_sse_json("log", {"text": "thread started"})
            return
        if event_type == "turn.started":
            self.write_sse_json("log", {"text": "turn started"})
            return
        if event_type == "turn.completed":
            return
        if event_type in {"error", "turn.failed"}:
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
        attach_args = ["tmux", "attach-session"]
        if readonly:
            attach_args.append("-r")
        attach_args.extend(["-t", tmux_session_target(session)])
        process = subprocess.Popen(
            attach_args,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
            start_new_session=True,
        )

        try:
            for payload in pending_payloads:
                self.handle_ws_payload(session, master_fd, slave_fd, process, payload, readonly=readonly)
            while process.poll() is None:
                readable, _, _ = select.select([master_fd, self.connection], [], [], 0.1)
                if master_fd in readable:
                    data = os.read(master_fd, 65536)
                    if not data:
                        break
                    self.connection.sendall(make_ws_frame(data, opcode=2))
                if self.connection in readable:
                    opcode, payload = read_ws_frame(self.rfile)
                    if opcode == 8:
                        break
                    if opcode == 9:
                        self.connection.sendall(make_ws_frame(payload, opcode=10))
                        continue
                    if opcode not in {1, 2}:
                        continue
                    self.handle_ws_payload(session, master_fd, slave_fd, process, payload, readonly=readonly)
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
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()

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
            opcode, payload = read_ws_frame(self.rfile)
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
                next_cols = message.get("cols")
                next_rows = message.get("rows")
                if isinstance(next_cols, int) and isinstance(next_rows, int):
                    cols = next_cols
                    rows = next_rows
                continue
            pending_payloads.append(payload)
            break
        return rows, cols, pending_payloads

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
        elif msg_type == "resize":
            cols = message.get("cols")
            rows = message.get("rows")
            if isinstance(cols, int) and isinstance(rows, int):
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

    def __init__(self, server_address: tuple[str, int], app: TmuxWebtermApp, tls_context: ssl.SSLContext | None = None):
        super().__init__(server_address, Handler)
        self.app = app
        self.tls_context = tls_context

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

import base64
import json
import threading
from http import HTTPStatus
from http.client import HTTPConnection
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.parse import urlparse

import pytest

from yolomux_lib import common
from yolomux_lib import server_auth
from yolomux_lib import web
from yolomux_lib.server import Handler
from yolomux_lib.server import TmuxWebtermHTTPServer

pytestmark = pytest.mark.socket


def active_auth_yaml() -> str:
    return """users:
  - username: "keivenc"
    password: "random-password"
    role: "admin"
  - username: "guest"
    password: "guest"
    role: "readonly"
"""


def request(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    conn.close()
    return result


def request_header_list(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    result = response.status, response.getheaders(), data
    conn.close()
    return result


def auth_header(username, password):
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def start_server(monkeypatch, tmp_path, app=None, tls_context=None):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(active_auth_yaml(), encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    # C2: the pre-auth login/setup screens localize via the saved general.language (read at import time
    # from the real ~/.config/yolomux). Force "system" (-> English) so English literal assertions don't
    # depend on the developer's saved locale or on test import order. request_locale_pref resolves the
    # name in server_auth's namespace, so patch it there; the yolomux_locale cookie branch stays live.
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    app = app or SimpleNamespace(sessions=[], dangerously_yolo=False)
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app, tls_context=tls_context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_login_page_sets_auth_cookie(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        status, headers, _body = request(port, "GET", "/", headers={"Accept": "text/html"})
        assert status == HTTPStatus.SEE_OTHER
        assert headers["Location"].startswith("/login?next=")

        status, _headers, body = request(port, "GET", "/login")
        assert status == HTTPStatus.OK
        assert b'<form method="post" action="/login"' in body
        assert b'id="togglePassword"' in body
        # Derive the English label from the catalog (locale forced to system -> en by start_server) so
        # the assertion tracks the catalog rather than a copy literal.
        show_password = web.server_string("en", "login.showPassword")
        assert show_password == "Show password"
        assert f'aria-label="{show_password}"'.encode("utf-8") in body

        bad_body = urlencode({"username": "keivenc", "password": "wrong", "next": "/"})
        status, _headers, body = request(
            port,
            "POST",
            "/login",
            body=bad_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == HTTPStatus.UNAUTHORIZED
        assert b"Invalid username or password" in body

        good_body = urlencode({"username": "keivenc", "password": "random-password", "next": "/api/ping"})
        status, header_items, _body = request_header_list(
            port,
            "POST",
            "/login",
            body=good_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        headers = dict(header_items)
        set_cookie_headers = [value for name, value in header_items if name.lower() == "set-cookie"]
        assert status == HTTPStatus.SEE_OTHER
        assert headers["Location"] == "/api/ping"
        auth_cookie_header = next(value for value in set_cookie_headers if value.startswith(f"{common.AUTH_COOKIE_NAME}_{port}="))
        assert "Max-Age=7776000" in auth_cookie_header
        cookie = auth_cookie_header.split(";", 1)[0]

        status, header_items, body = request_header_list(port, "GET", "/api/ping", headers={"Cookie": cookie})
        set_cookie_headers = [value for name, value in header_items if name.lower() == "set-cookie"]
        assert status == HTTPStatus.OK
        # Cookie-auth must NOT re-mint the cookie. Re-minting here races with
        # /logout: polling requests in flight when the user clicks logout would
        # re-set the auth cookie and clear the logout marker.
        auth_re_mints = [v for v in set_cookie_headers if v.startswith(f"{common.AUTH_COOKIE_NAME}_{port}=") and "Max-Age=0" not in v]
        assert not auth_re_mints, f"cookie auth should not re-mint: {auth_re_mints}"
        assert json.loads(body)["ok"] is True

        wrong_port_cookie = cookie.replace(f"{common.AUTH_COOKIE_NAME}_{port}=", f"{common.AUTH_COOKIE_NAME}_{port + 1}=")
        status, _headers, body = request(port, "GET", "/api/ping", headers={"Cookie": wrong_port_cookie})
        assert status == HTTPStatus.UNAUTHORIZED
        assert json.loads(body)["error"] == "authentication required"

        empty_layout_body = urlencode({"username": "keivenc", "password": "random-password", "next": "/?layout=empty"})
        status, headers, _body = request(
            port,
            "POST",
            "/login",
            body=empty_layout_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == HTTPStatus.SEE_OTHER
        assert headers["Location"] == "/"
    finally:
        stop_server(server, thread)


def test_login_page_localizes_via_locale_cookie(monkeypatch, tmp_path):
    # C2: a yolomux_locale cookie localizes the pre-auth login screen regardless of the saved/forced
    # default locale — proves the localization path works (request_locale_pref's cookie branch wins) and
    # that the English-default test above is isolation, not a localization regression.
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/login", headers={"Cookie": "yolomux_locale=zh-Hant"})
        assert status == HTTPStatus.OK
        localized = web.server_string("zh-Hant", "login.showPassword")
        assert localized == "顯示密碼"
        assert f'aria-label="{localized}"'.encode("utf-8") in body
        localized_system = web.server_string("zh-Hant", "pref.general.language.system")
        assert f">{localized_system}<".encode("utf-8") in body
        # The English default must NOT appear when the cookie selects another locale.
        assert b'aria-label="Show password"' not in body
        assert b">System<" not in body
    finally:
        stop_server(server, thread)


def test_basic_auth_still_works_without_browser_challenge(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        status, headers, body = request(port, "GET", "/api/ping")
        assert status == HTTPStatus.UNAUTHORIZED
        assert "WWW-Authenticate" not in headers
        assert json.loads(body)["login_url"] == "/login?next=%2F"

        status, headers, body = request(port, "GET", "/api/ping", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.OK
        assert "Set-Cookie" in headers
        assert json.loads(body)["ok"] is True

        status, headers, body = request(port, "GET", f"/api/fs/list?{urlencode({'path': str(tmp_path)})}", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body)["error"] == "admin access required"

        # blame reads repository file history, so a readonly identity is forbidden — same as
        # the rest of the file/repo API (it must NOT bypass the readonly guard at /api/blame).
        status, headers, body = request(port, "GET", f"/api/blame?{urlencode({'path': str(tmp_path)})}", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body)["error"] == "admin access required"

        # Dev-velocity #1b: the /api/dev-reload SSE channel is 404 unless the server runs with --dev, so
        # production never exposes it (the test server is constructed without dev).
        status, headers, body = request(port, "GET", "/api/dev-reload", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.NOT_FOUND
    finally:
        stop_server(server, thread)


def test_html_preview_route_runs_scripts_in_sandboxed_wrapper(monkeypatch, tmp_path):
    target = tmp_path / "preview.html"
    target.write_text("<h1>ok</h1><script>window.answer = 42;</script>\n", encoding="utf-8")
    written = {}
    handler = SimpleNamespace(
        write_html=lambda body, status=HTTPStatus.OK: written.update({"html_status": status, "html": body}),
        write_json=lambda value, status=HTTPStatus.OK: written.update({"json_status": status, "json": value}),
    )

    Handler.handle_fs_html_preview(handler, urlparse(f"/api/fs/html-preview?{urlencode({'path': str(target)})}"))

    assert written["html_status"] == HTTPStatus.OK
    assert 'sandbox="allow-scripts allow-forms allow-popups"' in written["html"]
    assert "allow-same-origin" not in written["html"]
    assert "&lt;script&gt;window.answer = 42;&lt;/script&gt;" in written["html"]

    Handler.handle_fs_html_preview(handler, urlparse(f"/api/fs/html-preview?{urlencode({'path': str(tmp_path / 'plain.txt')})}"))

    assert written["json_status"] == HTTPStatus.BAD_REQUEST
    assert written["json"]["error"] == "path must be an HTML file"


def test_preview_popout_placeholder_route_returns_same_origin_shell():
    written = {}
    handler = SimpleNamespace(
        write_html=lambda body, status=HTTPStatus.OK: written.update({"html_status": status, "html": body}),
    )

    Handler.handle_preview_popout_placeholder(
        handler,
        urlparse(f"/preview-popout?{urlencode({'path': '/home/test/README.md'})}"),
    )

    assert written["html_status"] == HTTPStatus.OK
    assert "<title>README.md preview</title>" in written["html"]
    assert "<body></body>" in written["html"]


def test_html_preview_route_accepts_logged_in_cookie(monkeypatch, tmp_path):
    target = tmp_path / "preview.html"
    target.write_text("<h1>ok</h1><script>window.answer = 42;</script>\n", encoding="utf-8")
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _login_and_extract_auth_cookie(port)
        status, _headers, body = request(
            port,
            "GET",
            f"/api/fs/html-preview?{urlencode({'path': str(target)})}",
            headers={"Cookie": cookie},
        )
        assert status == HTTPStatus.OK
        assert b'sandbox="allow-scripts allow-forms allow-popups"' in body
        assert b"authentication required" not in body
    finally:
        stop_server(server, thread)


def test_readonly_identity_cannot_call_mutating_post(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        status, _headers, body = request(
            port,
            "POST",
            "/api/create-session?agent=term",
            headers=auth_header("guest", "guest"),
        )

        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body) == {"error": "admin access required", "role": "readonly"}
    finally:
        stop_server(server, thread)


def test_share_token_is_limited_to_root_and_websocket(monkeypatch, tmp_path):
    app = SimpleNamespace(
        sessions=["6", "7"],
        dangerously_yolo=False,
        verify_share_token=lambda token: {"session": "6", "sessions": ["6", "7"]} if token == "valid-share-token" else None,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/?token=valid-share-token")
        assert status == HTTPStatus.OK
        assert b'"accessRole":"readonly"' in body
        assert b'"sessions":["6","7"]' in body

        status, _headers, body = request(port, "GET", "/api/ping?token=valid-share-token")
        assert status == HTTPStatus.OK
        assert json.loads(body)["ok"] is True

        status, _headers, body = request(port, "POST", "/api/upload?token=valid-share-token")
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body)["error"] == "share token is limited to the shared page and websocket"

        status, _headers, body = request(port, "GET", "/api/ping?token=wrong")
        assert status == HTTPStatus.UNAUTHORIZED
        assert json.loads(body)["error"] == "authentication required"
    finally:
        stop_server(server, thread)


def test_share_token_allows_only_shared_editor_file_reads(monkeypatch, tmp_path):
    shared_file = tmp_path / "shared.md"
    private_file = tmp_path / "private.md"
    shared_file.write_text("# Shared\n", encoding="utf-8")
    private_file.write_text("# Private\n", encoding="utf-8")
    shared_path = str(shared_file)
    record = {"session": "6", "sessions": ["6"], "tabs": f"slot1:file:{shared_path}"}
    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        verify_share_token=lambda token: record if token == "valid-share-token" else None,
        share_record_allows_file_path=lambda share_record, raw_path: share_record == record and raw_path == shared_path,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", f"/api/fs/read?{urlencode({'path': shared_path, 'token': 'valid-share-token'})}")
        assert status == HTTPStatus.OK
        assert json.loads(body)["content"] == "# Shared\n"

        status, _headers, body = request(port, "GET", f"/api/fs/read?{urlencode({'path': str(private_file), 'token': 'valid-share-token'})}")
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body) == {"error": "admin access required", "role": "readonly"}
    finally:
        stop_server(server, thread)


def test_share_short_id_shell_boots_without_login(monkeypatch, tmp_path):
    record = {
        "session": "6",
        "sessions": ["6", "7"],
        "short_id": "share123",
        "mode": "ro",
        "scheme": "http",
        "expires_at": 1234567890.0,
        "max_viewers": 5,
        "viewers": 0,
        "layout": "row@50(left,slot1)",
        "tabs": "slot1:6",
        "finder": {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"},
        "ui_state": {"finder": {"mode": "tabber"}, "editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
    }
    app = SimpleNamespace(
        sessions=["6", "7"],
        dangerously_yolo=False,
        share_record_for_short_id=lambda short_id: record if short_id == "share123" else None,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/share/share123")

        assert status == HTTPStatus.OK
        assert b'"accessRole":"readonly"' in body
        assert b'"sessions":["6","7"]' in body
        assert b'"share":{"view":true,"id":"share123","session":"6","sessions":["6","7"],"mode":"ro"' in body
        assert b'"finder":{"root":"/home/keivenc/yolomux.dev1","rootMode":"fixed","mode":"tabber","session":"7"}' in body
        assert b'"uiState":{"finder":{"mode":"tabber"},"editor":{"modes":[{"path":"/tmp/a.md","mode":"split"}]}}' in body
        assert b'"tokenInFragment":true' in body
    finally:
        stop_server(server, thread)


def test_share_short_id_shell_rejects_when_viewer_cap_is_full(monkeypatch, tmp_path):
    record = {
        "session": "6",
        "short_id": "share123",
        "mode": "ro",
        "expires_at": 1234567890.0,
        "max_viewers": 1,
        "viewers": 1,
    }
    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        share_record_for_short_id=lambda short_id: record if short_id == "share123" else None,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/share/share123")

        assert status == HTTPStatus.FORBIDDEN
        assert body == b"share viewer limit reached\n"
    finally:
        stop_server(server, thread)


class FakeTlsContext:
    def wrap_socket(self, *_args, **_kwargs):
        raise AssertionError("plain HTTP request should not be TLS-wrapped")


def test_plaintext_on_tls_server_redirects_non_share_requests(monkeypatch, tmp_path):
    app = SimpleNamespace(sessions=["6"], dangerously_yolo=False)
    server, thread = start_server(monkeypatch, tmp_path, app=app, tls_context=FakeTlsContext())
    port = server.server_address[1]
    try:
        status, headers, body = request(port, "GET", "/api/ping")

        assert status == HTTPStatus.PERMANENT_REDIRECT
        assert headers["Location"] == f"https://127.0.0.1:{port}/api/ping"
        assert b"Use HTTPS" in body
    finally:
        stop_server(server, thread)


def test_plaintext_on_tls_server_serves_http_share_shell(monkeypatch, tmp_path):
    record = {
        "session": "6",
        "short_id": "share123",
        "mode": "ro",
        "scheme": "http",
        "expires_at": 1234567890.0,
        "max_viewers": 5,
        "viewers": 0,
        "http_allowed": True,
    }
    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        share_record_for_short_id=lambda short_id: record if short_id == "share123" else None,
        http_allowed_share_is_active=lambda: True,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app, tls_context=FakeTlsContext())
    port = server.server_address[1]
    try:
        status, headers, body = request(port, "GET", "/share/share123")

        assert status == HTTPStatus.OK
        assert "Location" not in headers
        assert b'"share":{"view":true,"id":"share123","session":"6","sessions":["6"],"mode":"ro"' in body
    finally:
        stop_server(server, thread)


def test_share_create_endpoint_passes_layout_seed(monkeypatch, tmp_path):
    calls = []

    def create_share_token(session, ttl_seconds=None, **kwargs):
        calls.append((session, ttl_seconds, kwargs))
        return {"ok": True, "url": "http://127.0.0.1/share", "token": "token", "session": session}, HTTPStatus.OK

    app = SimpleNamespace(sessions=["6"], dangerously_yolo=False, create_share_token=create_share_token)
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        body = json.dumps({
            "session": "6",
            "sessions": ["6", "7"],
            "ttl_seconds": 900,
            "layout": "row@50(left,slot1)",
            "tabs": "left:6;slot1:7",
            "finder": {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"},
            "ui_state": {"finder": {"mode": "tabber"}, "editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
            "mode": "ro",
            "scheme": "http",
            "max_viewers": 7,
        })
        status, _headers, response = request(
            port,
            "POST",
            "/api/share",
            body=body,
            headers={**auth_header("keivenc", "random-password"), "Content-Type": "application/json"},
        )

        assert status == HTTPStatus.OK
        assert json.loads(response)["token"] == "token"
        assert calls == [("6", 900, {
            "base_url": f"http://127.0.0.1:{port}",
            "created_by": "keivenc",
            "layout": "row@50(left,slot1)",
            "tabs": "left:6;slot1:7",
            "finder": {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"},
            "ui_state": {"finder": {"mode": "tabber"}, "editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
            "sessions": ["6", "7"],
            "mode": "ro",
            "read_only": None,
            "scheme": "http",
            "max_viewers": 7,
            "request_is_https": False,
            "tls_available": False,
        })]
    finally:
        stop_server(server, thread)


def test_share_status_is_admin_or_share_scoped_and_stop_is_admin_only(monkeypatch, tmp_path):
    calls = []
    record = {"session": "6", "sessions": ["6"], "mode": "ro", "expires_at": 1234567890.0}

    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        active_share_payload=lambda **kwargs: calls.append(("status", kwargs)) or ({"ok": True, "active": False}, HTTPStatus.OK),
        verify_share_token=lambda token: record | {"token": token} if token == "valid-share-token" else None,
        share_status_payload=lambda token, **kwargs: calls.append(("share-status", token, kwargs)) or ({"ok": True, "active": True, "token": token}, HTTPStatus.OK),
        stop_active_share=lambda token_or_short_id="": calls.append(("stop", token_or_short_id)) or ({"ok": True, "active": False, "stopped": 1}, HTTPStatus.OK),
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/api/share", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body)["error"] == "admin access required"

        status, _headers, body = request(port, "GET", "/api/share", headers=auth_header("keivenc", "random-password"))
        assert status == HTTPStatus.OK
        assert json.loads(body) == {"ok": True, "active": False}

        status, _headers, body = request(port, "GET", "/api/share", headers={"X-Share-Token": "valid-share-token"})
        assert status == HTTPStatus.OK
        assert json.loads(body) == {"ok": True, "active": True, "token": "valid-share-token"}

        status, _headers, body = request(port, "POST", "/api/share/stop", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body)["error"] == "admin access required"

        status, _headers, body = request(
            port,
            "POST",
            "/api/share/stop",
            body=json.dumps({"token": "share-token-1"}),
            headers={**auth_header("keivenc", "random-password"), "Content-Type": "application/json"},
        )
        assert status == HTTPStatus.OK
        assert json.loads(body) == {"ok": True, "active": False, "stopped": 1}
        assert calls == [
            ("status", {"base_url": f"http://127.0.0.1:{port}"}),
            ("share-status", "valid-share-token", {"base_url": f"http://127.0.0.1:{port}"}),
            ("stop", "share-token-1"),
        ]
    finally:
        stop_server(server, thread)


def test_share_view_websocket_rejects_missing_key_before_registering(monkeypatch, tmp_path):
    calls = []
    record = {
        "session": "6",
        "short_id": "share123",
        "mode": "ro",
        "expires_at": 1234567890.0,
        "max_viewers": 5,
        "viewers": 0,
    }

    def verify_share_token(token):
        return record | {"token": token} if token == "valid-share-token" else None

    def register_share_viewer(token, session="", viewer_id="", ip="", user_agent=""):
        calls.append(("register", token, session, viewer_id, ip, user_agent))
        return record | {"token": token, "viewers": 1, "viewer_id": viewer_id or "legacy"}, HTTPStatus.OK

    def unregister_share_viewer(token, viewer_id=""):
        calls.append(("unregister", token, viewer_id))
        return 0

    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        verify_share_token=verify_share_token,
        register_share_viewer=register_share_viewer,
        unregister_share_viewer=unregister_share_viewer,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/ws/share-view?token=valid-share-token")

        assert status == HTTPStatus.BAD_REQUEST
        assert body == b"missing Sec-WebSocket-Key\n"
        assert calls == []
    finally:
        stop_server(server, thread)


def test_share_host_websocket_is_admin_only_and_verifies_share(monkeypatch, tmp_path):
    calls = []
    record = {
        "session": "6",
        "short_id": "share123",
        "mode": "ro",
        "expires_at": 1234567890.0,
        "max_viewers": 5,
        "viewers": 0,
    }

    def verify_share_token(token):
        calls.append(token)
        return record | {"token": token} if token == "valid-share-token" else None

    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        verify_share_token=verify_share_token,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/ws/share-host?share=valid-share-token", headers=auth_header("guest", "guest"))
        assert status == HTTPStatus.FORBIDDEN
        assert json.loads(body)["error"] == "admin access required"

        status, _headers, body = request(port, "GET", "/ws/share-host?share=wrong", headers=auth_header("keivenc", "random-password"))
        assert status == HTTPStatus.NOT_FOUND
        assert body == b"unknown active share\n"

        status, _headers, body = request(port, "GET", "/ws/share-host?share=valid-share-token", headers=auth_header("keivenc", "random-password"))
        assert status == HTTPStatus.BAD_REQUEST
        assert body == b"missing Sec-WebSocket-Key\n"
        assert calls == ["wrong", "valid-share-token"]
    finally:
        stop_server(server, thread)


def test_readonly_share_ui_websocket_accepts_receive_only_viewer(monkeypatch, tmp_path):
    record = {
        "session": "6",
        "short_id": "share123",
        "mode": "ro",
        "expires_at": 1234567890.0,
        "max_viewers": 5,
        "viewers": 0,
    }
    app = SimpleNamespace(
        sessions=["6"],
        dangerously_yolo=False,
        verify_share_token=lambda token: record | {"token": token} if token == "valid-share-token" else None,
    )
    server, thread = start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        status, _headers, body = request(port, "GET", "/ws/share-ui?token=valid-share-token&client=viewer-a")
        assert status == HTTPStatus.BAD_REQUEST
        assert body == b"missing Sec-WebSocket-Key\n"
    finally:
        stop_server(server, thread)


def test_forged_auth_cookie_is_rejected(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _login_and_extract_auth_cookie(port)
        forged_cookie = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")

        status, _headers, body = request(port, "GET", "/api/ping", headers={"Cookie": forged_cookie})

        assert status == HTTPStatus.UNAUTHORIZED
        assert json.loads(body)["error"] == "authentication required"
    finally:
        stop_server(server, thread)


def test_login_next_path_blocks_open_redirects(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        for unsafe_next in ("//evil.example/path", "/safe\r\nLocation: //evil.example"):
            body = urlencode({"username": "keivenc", "password": "random-password", "next": unsafe_next})
            status, headers, _body = request(
                port,
                "POST",
                "/login",
                body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            assert status == HTTPStatus.SEE_OTHER
            assert headers["Location"] == "/"
    finally:
        stop_server(server, thread)


def test_logout_clears_current_and_legacy_auth_cookies(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        body = urlencode({"username": "keivenc", "password": "random-password", "next": "/"})
        status, headers, _body = request(
            port,
            "POST",
            "/login",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == HTTPStatus.SEE_OTHER
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, header_items, _body = request_header_list(port, "GET", "/logout", headers={"Cookie": cookie})
        set_cookie_headers = [value for name, value in header_items if name.lower() == "set-cookie"]

        assert status == HTTPStatus.SEE_OTHER
        assert dict(header_items)["Location"] == "/login"
        assert any(value.startswith(f"{common.AUTH_COOKIE_NAME}_{port}=") for value in set_cookie_headers)
        assert any(value.startswith(f"{common.AUTH_COOKIE_NAME}=") for value in set_cookie_headers)
        assert any(value.startswith(f"{common.AUTH_LOGOUT_COOKIE_NAME}=1;") for value in set_cookie_headers)
        clear_headers = [value for value in set_cookie_headers if not value.startswith(f"{common.AUTH_LOGOUT_COOKIE_NAME}=1;")]
        assert all("Max-Age=0" in value for value in clear_headers)
        assert all("Expires=Thu, 01 Jan 1970 00:00:00 GMT" in value for value in clear_headers)
    finally:
        stop_server(server, thread)


def _login_and_extract_auth_cookie(port: int) -> str:
    body = urlencode({"username": "keivenc", "password": "random-password", "next": "/"})
    status, header_items, _body = request_header_list(
        port,
        "POST",
        "/login",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == HTTPStatus.SEE_OTHER
    set_cookies = [v for k, v in header_items if k.lower() == "set-cookie"]
    auth_cookie_header = next(v for v in set_cookies if v.startswith(f"{common.AUTH_COOKIE_NAME}_{port}=") and "Max-Age=0" not in v)
    return auth_cookie_header.split(";", 1)[0]


def test_cookie_auth_does_not_remint_to_avoid_logout_race(monkeypatch, tmp_path):
    # Regression: a polling request that arrives at the server while the user
    # is clicking logout used to re-mint the auth cookie and clear the logout
    # marker. The /logout response's clear-cookie headers got overwritten by
    # the polling response, leaking the user back into the app on the next
    # /login visit. Cookie auth must validate without re-issuing the cookie.
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _login_and_extract_auth_cookie(port)
        status, header_items, _body = request_header_list(port, "GET", "/api/ping", headers={"Cookie": cookie})
        set_cookie_headers = [value for name, value in header_items if name.lower() == "set-cookie"]
        assert status == HTTPStatus.OK
        live_auth_cookies = [v for v in set_cookie_headers if v.startswith(f"{common.AUTH_COOKIE_NAME}_{port}=") and "Max-Age=0" not in v]
        assert not live_auth_cookies, f"cookie auth re-minted: {live_auth_cookies}"
        marker_clears = [v for v in set_cookie_headers if v.startswith(f"{common.AUTH_LOGOUT_COOKIE_NAME}=;")]
        assert not marker_clears, f"cookie auth cleared the logout marker: {marker_clears}"
    finally:
        stop_server(server, thread)


def test_login_page_with_marker_ignores_stale_cookie(monkeypatch, tmp_path):
    # Defense in depth: if a stale auth cookie somehow survives logout AND the
    # logout marker is present, /login must still show the form rather than
    # redirecting into the app.
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _login_and_extract_auth_cookie(port)
        combined_cookie = f"{cookie}; {common.AUTH_LOGOUT_COOKIE_NAME}=1"
        status, _headers, body = request(port, "GET", "/login", headers={"Cookie": combined_cookie})
        assert status == HTTPStatus.OK
        assert b'<form method="post" action="/login"' in body
    finally:
        stop_server(server, thread)


def test_logout_marker_blocks_cached_basic_auth_until_form_login(monkeypatch, tmp_path):
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        logout_cookie = f"{common.AUTH_LOGOUT_COOKIE_NAME}=1"
        status, _headers, body = request(
            port,
            "GET",
            "/api/ping",
            headers={**auth_header("keivenc", "random-password"), "Cookie": logout_cookie},
        )
        assert status == HTTPStatus.UNAUTHORIZED
        assert json.loads(body)["error"] == "authentication required"

        body = urlencode({"username": "keivenc", "password": "random-password", "next": "/"})
        status, header_items, _body = request_header_list(
            port,
            "POST",
            "/login",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": logout_cookie},
        )
        set_cookie_headers = [value for name, value in header_items if name.lower() == "set-cookie"]

        assert status == HTTPStatus.SEE_OTHER
        assert any(value.startswith(f"{common.AUTH_COOKIE_NAME}_{port}=") for value in set_cookie_headers)
        assert any(value.startswith(f"{common.AUTH_LOGOUT_COOKIE_NAME}=;") for value in set_cookie_headers)
    finally:
        stop_server(server, thread)


def test_fs_raw_download_sets_attachment_header(monkeypatch, tmp_path):
    target = tmp_path / 'report "a";.txt'
    target.write_bytes(b"hello")
    server, thread = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        status, headers, body = request(
            port,
            "GET",
            f"/api/fs/raw?{urlencode({'path': str(target), 'download': '1'})}",
            headers=auth_header("keivenc", "random-password"),
        )

        assert status == HTTPStatus.OK
        assert body == b"hello"
        assert headers["Content-Disposition"] == 'attachment; filename="report _a__.txt"'
    finally:
        stop_server(server, thread)

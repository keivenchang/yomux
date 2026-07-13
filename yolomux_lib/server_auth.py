# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Authentication mixin for the YOLOmux HTTP handler."""

from __future__ import annotations

import base64
import hmac
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import parse_qsl
from urllib.parse import unquote
from urllib.parse import urlencode
from urllib.parse import urlparse

from .common import AUTH_COOKIE_MAX_AGE_SECONDS
from .common import AUTH_COOKIE_NAME
from .common import AUTH_LOGOUT_COOKIE_NAME
from .common import AuthIdentity
from .common import auth_cookie_value
from .common import auth_identity_for_credentials
from .common import auth_setup_required
from .common import current_auth_users
from .common import error_payload
from .common import test_auth_bypass_enabled
from .locales import normalize_locale
from .locales import resolve_locale_preference
from .http_routes import route_for_request
from .http_routes import SHARE_ACCESS_NONE
from .http_routes import SHARE_ACCESS_READONLY
from .http_routes import SHARE_ACCESS_SCOPED_FILE
from .web import _LOGIN_LOCALE_VALUES
from .web import current_language_pref
from .web import login_html
from .web import save_login_locale
from .web import server_string
from .web import setup_auth_html


class AuthMixin:
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
        return auth_identity_for_credentials(username, password)

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

    def request_locale_pref(self) -> str:
        """Resolve the pre-auth UI locale for the setup/login screens: the short-lived
        `yolomux_locale` cookie (set by the setup/login language picker, which must NOT write settings
        pre-auth) wins, else the saved general.language. The permanent write happens post-auth via
        save_login_locale; the cookie is a carrier with a Max-Age backstop."""
        for item in self.headers.get("Cookie", "").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "yolomux_locale":
                candidate = unquote(value.strip())
                if candidate in _LOGIN_LOCALE_VALUES:
                    return candidate
        return current_language_pref()

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
        marker = getattr(self, "_request_is_https", None)
        if marker is not None:
            return bool(marker)
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

    def share_token_text(self) -> str:
        header_token = self.headers.get("X-Share-Token", "").strip()
        if header_token:
            return header_token
        parsed = urlparse(self.path)
        return parse_qs(parsed.query).get("token", [""])[0].strip()

    def share_request_allowed(self) -> bool:
        parsed = urlparse(self.path)
        route = route_for_request(str(getattr(self, "command", "GET") or "GET"), parsed.path)
        return bool(route and route.share_access != SHARE_ACCESS_NONE)

    def share_readonly_api_allowed(self, parsed: Any) -> bool:
        if not self.share_token():
            return False
        route = route_for_request(str(getattr(self, "command", "GET") or "GET"), parsed.path)
        if route is None:
            return False
        if route.share_access == SHARE_ACCESS_READONLY:
            return True
        if route.share_access != SHARE_ACCESS_SCOPED_FILE:
            return False
        raw_path = parse_qs(parsed.query).get("path", [""])[0].strip()
        checker = getattr(self.server.app, "share_record_allows_file_path", None)
        return callable(checker) and checker(self.share_record(), raw_path)

    def reject_share_forbidden(self) -> None:
        self.close_after_unread_body()
        self.write_json(
            error_payload(
                "share token is limited to the shared page and websocket",
                message_key="share.error.pageScope",
                role="readonly",
            ),
            status=HTTPStatus.FORBIDDEN,
        )

    def reject_forbidden(self, identity: AuthIdentity, required_role: str) -> None:
        self.close_after_unread_body()
        self.write_json(
            error_payload(
                f"{required_role} access required",
                message_key="auth.error.accessRequired",
                message_params={"role": required_role},
                role=identity.role,
            ),
            status=HTTPStatus.FORBIDDEN,
        )

    def safe_next_path(self, value: str | None) -> str:
        text = str(value or "/").strip()
        # also reject backslashes — a browser normalizes `/\evil.com` to `//evil.com`, an
        # external open redirect after login. Protocol-relative `//` and CR/LF are rejected too.
        if not text.startswith("/") or text.startswith("//") or "\\" in text or "\r" in text or "\n" in text:
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
            error_payload(
                "authentication required",
                message_key="auth.error.authenticationRequired",
                login_url=self.login_url("/"),
            ),
            status=HTTPStatus.UNAUTHORIZED,
        )

    def require_auth(self, required_role: str = "readonly") -> bool:
        self._auth_cookie_identity = None
        self._share_session = None
        self._share_sessions = []
        self._share_token = None
        self._share_record = None
        self._share_mode = "ro"
        bypass_auth = test_auth_bypass_enabled()
        if auth_setup_required() and not bypass_auth:
            self.write_html(setup_auth_html(self.request_locale_pref(), self.headers.get("Accept-Language", ""), secure=self.request_is_https()))
            return False
        share_token = self.share_token_text()
        if share_token:
            verifier = getattr(self.server.app, "verify_share_token", None)
            record = verifier(share_token) if callable(verifier) else None
            if record is None:
                self._auth_identity = None
                self.reject_unauthorized()
                return False
            identity = AuthIdentity(username="share", password="", role="readonly")
            self._auth_identity = identity
            raw_sessions = record.get("sessions") if isinstance(record.get("sessions"), list) else []
            share_sessions = [str(session or "").strip() for session in raw_sessions if str(session or "").strip()]
            if not share_sessions and record.get("session"):
                share_sessions = [str(record.get("session") or "")]
            self._share_sessions = share_sessions
            self._share_session = share_sessions[0] if share_sessions else ""
            self._share_token = share_token
            self._share_record = dict(record)
            self._share_mode = str(record.get("mode") or "ro") if self.request_is_https() else "ro"
            if not self.share_request_allowed():
                self.reject_share_forbidden()
                return False
            if self.role_allows(identity, required_role):
                return True
            self.reject_forbidden(identity, required_role)
            return False
        if bypass_auth:
            identity = AuthIdentity(username="test-auth-bypass", password="", role="admin")
            self._auth_identity = identity
            if self.role_allows(identity, required_role):
                return True
            self.reject_forbidden(identity, required_role)
            return False
        identity = self.cookie_auth_identity()
        if identity is not None:
            # Do not re-mint an existing cookie. Polling requests in flight
            # during logout would otherwise re-set the auth cookie.
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

    def share_session(self) -> str:
        return str(getattr(self, "_share_session", "") or "")

    def share_sessions(self) -> list[str]:
        sessions = getattr(self, "_share_sessions", [])
        return list(sessions) if isinstance(sessions, list) else []

    def share_token(self) -> str:
        return str(getattr(self, "_share_token", "") or "")

    def share_record(self) -> dict[str, Any] | None:
        record = getattr(self, "_share_record", None)
        return dict(record) if isinstance(record, dict) else None

    def share_mode(self) -> str:
        return str(getattr(self, "_share_mode", "ro") or "ro")

    def handle_login_page(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        next_path = self.safe_next_path(qs.get("next", ["/"])[0])
        if test_auth_bypass_enabled():
            self.write_redirect(self.login_success_path(next_path))
            return
        if auth_setup_required():
            self.write_html(setup_auth_html(self.request_locale_pref(), self.headers.get("Accept-Language", ""), secure=self.request_is_https()))
            return
        if not self.has_logout_marker() and self.cookie_auth_identity() is not None:
            self.write_redirect(self.login_success_path(next_path))
            return
        self.write_html(login_html(
            next_path=next_path,
            secure=self.request_is_https(),
            current_locale=self.request_locale_pref(),
            accept_language=self.headers.get("Accept-Language", ""),
        ))

    def handle_login_submit(self, parsed: Any) -> None:
        form = self.read_urlencoded_form()
        next_path = self.safe_next_path(form.get("next", ["/"])[0])
        if test_auth_bypass_enabled():
            self.write_redirect(self.login_success_path(next_path))
            return
        if auth_setup_required():
            self.write_html(setup_auth_html(self.request_locale_pref(), self.headers.get("Accept-Language", ""), secure=self.request_is_https()))
            return
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        identity = auth_identity_for_credentials(username, password)
        if identity is None:
            self.close_after_unread_body()
            locale = normalize_locale(
                form.get("locale", [""])[0],
                default=self.request_locale_pref(),
                allow_system=True,
            )
            display_locale = resolve_locale_preference(locale, self.headers.get("Accept-Language", ""))
            self.write_html(
                login_html(
                    next_path=next_path,
                    error=server_string(display_locale, "login.error.invalid"),
                    secure=self.request_is_https(),
                    current_locale=locale,
                    accept_language=self.headers.get("Accept-Language", ""),
                ),
                status=HTTPStatus.UNAUTHORIZED,
            )
            return
        # Phase 1: persist the login-screen language choice to general.language now that auth
        # succeeded, so the picked language carries into the app (the same setting the topbar/Preferences
        # switchers write). Done post-auth so an unauthenticated POST can't mutate settings.
        save_login_locale(form.get("locale", [""])[0])
        self._auth_cookie_identity = identity
        self.write_redirect(self.login_success_path(next_path))

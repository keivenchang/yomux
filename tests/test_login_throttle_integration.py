# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""HTTP-level coverage that the login throttle is wired into BOTH password paths
(browser POST /login and HTTP Basic) with a generic, fact-free 429; that setup mode
and the test-auth bypass are preserved; and that a blocked attempt never runs PBKDF2."""
from __future__ import annotations

import base64
import threading
from http import HTTPStatus
from http.client import HTTPConnection
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from yolomux_lib import common
from yolomux_lib import server_auth
from yolomux_lib import web
from yolomux_lib import login_rate_limit as lrl
from yolomux_lib.login_rate_limit import BucketPolicy
from yolomux_lib.login_rate_limit import LoginRatePolicy
from yolomux_lib.login_rate_limit import LoginRateLimiter
from yolomux_lib.server import TmuxWebtermHTTPServer

pytestmark = pytest.mark.socket

VALID_USER = "keivenc"
VALID_PASSWORD = "random-password"


def active_auth_yaml() -> str:
    return f"""users:
  - username: "{VALID_USER}"
    password: "{VALID_PASSWORD}"
    role: "admin"
"""


def request(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    conn.close()
    return result


def auth_header(username, password):
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def loose_network_policy(**overrides):
    """Network buckets wide open so a test exercises the username backoff without the
    shared loopback IP bucket interfering; username allowance small to trip quickly."""
    base = dict(
        exact_bucket=BucketPolicy(10_000, 10_000),
        nearby_bucket=BucketPolicy(10_000, 10_000),
        broad_bucket=BucketPolicy(20_000, 20_000),
        global_bucket=BucketPolicy(50_000, 50_000),
        username_initial_allowance=3,
    )
    base.update(overrides)
    return LoginRatePolicy(**base)


def start_server(monkeypatch, tmp_path, *, policy=None, extra_app=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(active_auth_yaml(), encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    limiter = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", policy=policy or loose_network_policy())
    app = SimpleNamespace(sessions=[], dangerously_yolo=False, login_rate_limiter=limiter)
    if extra_app:
        for key, value in extra_app.items():
            setattr(app, key, value)
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app, tls_context=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, limiter


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def post_login(port, username, password):
    body = urlencode({"username": username, "password": password, "next": "/"})
    return request(port, "POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})


def test_repeated_bad_browser_logins_return_a_generic_429(monkeypatch, tmp_path):
    server, thread, _limiter = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        # First few bad attempts get the ordinary 401 invalid-credentials page.
        for _ in range(3):
            status, _headers, body = post_login(port, VALID_USER, "wrong")
            assert status == HTTPStatus.UNAUTHORIZED
            assert b"Invalid username or password" in body
        # Once the username backoff trips, further attempts are throttled with a generic
        # message. The 429 body must NOT reveal the bucket, whether the user exists, the
        # attempt count, or an exact reset time.
        rate_limited_text = web.server_string("en", "login.error.rateLimitedMinutes").encode("utf-8")
        saw_429 = False
        for _ in range(5):
            status, headers, body = post_login(port, VALID_USER, "wrong")
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                saw_429 = True
                assert rate_limited_text in body
                assert b"Invalid username or password" not in body
                # No internal scope name, attempt count, or scheduler-friendly precise
                # reset hint may leak (the word "username" is fine — it's the form field).
                for leak in (b"ip_exact", b"ip_nearby", b"ip_broad", b"attempts remaining", b"consecutive"):
                    assert leak not in body.lower()
                assert "retry-after" not in {name.lower() for name in headers}
        assert saw_429, "repeated bad logins must eventually be throttled"
    finally:
        stop_server(server, thread)


def test_basic_auth_is_throttled_with_a_generic_429(monkeypatch, tmp_path):
    server, thread, _limiter = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        saw_429 = False
        for _ in range(12):
            status, _headers, body = request(port, "GET", "/api/ping", headers={**auth_header(VALID_USER, "wrong"), "Accept": "application/json"})
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                saw_429 = True
                assert b"ip_exact" not in body.lower() and b"consecutive" not in body.lower()
        assert saw_429, "repeated bad Basic-auth attempts must be throttled"
    finally:
        stop_server(server, thread)


def test_valid_credentials_are_not_throttled_before_the_limit(monkeypatch, tmp_path):
    server, thread, _limiter = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        status, headers, _body = post_login(port, VALID_USER, VALID_PASSWORD)
        # A correct first login succeeds (redirect), never a 429.
        assert status == HTTPStatus.SEE_OTHER
        assert headers["Location"] == "/"
    finally:
        stop_server(server, thread)


def test_test_auth_bypass_skips_the_throttle(monkeypatch, tmp_path):
    monkeypatch.setenv(common.TEST_AUTH_BYPASS_ENV, "1")
    server, thread, limiter = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        # Many bad attempts under bypass never throttle and never touch the limiter.
        for _ in range(20):
            status, _headers, _body = post_login(port, VALID_USER, "wrong")
            assert status == HTTPStatus.SEE_OTHER  # bypass redirects to success
        assert limiter.diagnostics()["decisions"] == 0
    finally:
        stop_server(server, thread)


def test_setup_mode_is_preserved_and_never_throttles(monkeypatch, tmp_path):
    # No users configured -> setup mode: the login POST renders the setup page and must
    # not consult the limiter (an operator setting up must never be locked out).
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text("users: []\n", encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    limiter = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", policy=loose_network_policy())
    app = SimpleNamespace(sessions=[], dangerously_yolo=False, login_rate_limiter=limiter)
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app, tls_context=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        for _ in range(10):
            status, _headers, _body = post_login(port, "whoever", "whatever")
            assert status == HTTPStatus.OK  # setup page, not 401/429
        assert limiter.diagnostics()["decisions"] == 0
    finally:
        stop_server(server, thread)


def test_blocked_attempt_never_runs_pbkdf2(monkeypatch, tmp_path):
    calls = {"verify": 0}
    real_verify = server_auth.auth_identity_for_credentials

    def counting_verify(username, password):
        calls["verify"] += 1
        return real_verify(username, password)

    monkeypatch.setattr(server_auth, "auth_identity_for_credentials", counting_verify)
    server, thread, _limiter = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        statuses = [post_login(port, VALID_USER, "wrong")[0] for _ in range(12)]
        verified_before_block = calls["verify"]
        blocked_count = statuses.count(HTTPStatus.TOO_MANY_REQUESTS)
        assert blocked_count > 0
        # Every admitted attempt hashed exactly once; blocked attempts added zero hashes.
        admitted = statuses.count(HTTPStatus.UNAUTHORIZED)
        assert verified_before_block == admitted, "a throttled attempt must not reach the verifier"
    finally:
        stop_server(server, thread)


def test_unknown_and_known_usernames_are_throttled_identically(monkeypatch, tmp_path):
    server, thread, _limiter = start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        # Drive the shared loopback network buckets down using a tiny-exact-bucket server
        # would differ; here both names share the username-independent 401 then 429 shape.
        known = [post_login(port, VALID_USER, "wrong")[0] for _ in range(12)]
        stop_server(server, thread)
        server2, thread2, _l2 = start_server(monkeypatch, tmp_path / "second")
        port2 = server2.server_address[1]
        unknown = [post_login(port2, "ghost", "wrong")[0] for _ in range(12)]
        # Same status progression: some 401s then 429s, identical set of status codes.
        assert set(known) == set(unknown) == {HTTPStatus.UNAUTHORIZED, HTTPStatus.TOO_MANY_REQUESTS}
    finally:
        stop_server(server2, thread2)

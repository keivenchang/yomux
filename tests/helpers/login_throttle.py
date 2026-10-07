# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared real HTTP fixture for login-throttle contracts."""
from __future__ import annotations

from types import SimpleNamespace

from tests.helpers.fixture_http_server import FixtureHttpServer
from yomux_lib import common, server_auth
from yomux_lib.login_rate_limit import BucketPolicy, LoginRateLimiter, LoginRatePolicy

VALID_USER = "keivenc"
VALID_PASSWORD = "random-password"


def active_auth_yaml() -> str:
    return f"""users:
  - username: "{VALID_USER}"
    password: "{VALID_PASSWORD}"
    role: "admin"
"""


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
    runtime = FixtureHttpServer.start(app, label="login-throttle fixture HTTP server")
    return runtime.server, runtime.thread, limiter


def stop_server(server, thread):
    FixtureHttpServer(server, thread, "login-throttle fixture HTTP server").close()

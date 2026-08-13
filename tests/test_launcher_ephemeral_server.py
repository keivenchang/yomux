# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""W1: a row's authenticated probe validates against ITS OWN root, not a foreign cookie.

The end-to-end managed-self-owner path (exec launch + real owner probe bound to the unique
listener PID + exact-PID teardown) lives in `tests/test_launcher_row_wiring.py`. This adds the
one W1 verification point that file does not cover: the WRONG-COOKIE case. The launcher's probe
mints its admin cookie from the row's OWN auth config and per-root cookie secret, so a cookie
minted anywhere else (a foreign root, or the wrong password) authenticates against nothing here.

Uses `tests/isolated_dev_server.py`, which refuses the operator's live 777x/888x ports.
"""

from __future__ import annotations

import hashlib
import hmac
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path

from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yolomux_lib.auth import AUTH_COOKIE_NAME
from yolomux_lib.auth import read_auth_users


def _get(port: int, path: str, *, cookie: str | None = None) -> int:
    connection = HTTPConnection("127.0.0.1", port, timeout=5.0)
    try:
        connection.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        return int(connection.getresponse().status)
    finally:
        connection.close()


def test_row_probe_endpoint_authenticates_against_its_own_root_not_a_foreign_cookie(monkeypatch, tmp_path):
    root = tmp_path / "harness"
    paths = build_paths(root)
    # This row REQUIRES auth (no bypass). The auth config and the per-root cookie secret both live
    # INSIDE this row's own config dir; a cookie minted anywhere else is meaningless here.
    paths.auth_config_path.write_text(
        'users:\n  - username: "rowadmin"\n    password: "row-secret-password"\n    role: "admin"\n',
        encoding="utf-8",
    )
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)
    server = None
    try:
        server = start_isolated_dev_server(
            "row-auth",
            Path(__file__).resolve().parents[1],
            paths,
            tmux_runtime,
            auth_bypass=False,
        )
        # No assert_serving: this row requires auth, so an unauthenticated /api/ping is a 401 by
        # design. start_isolated_dev_server already blocked on the server's own serving line.
        port = server.port

        # Mint the correct cookie the way the row's own probe does: HMAC over the row's own
        # secret (stored hex) and the row's own (now hashed) stored password.
        secret = bytes.fromhex((paths.config_dir / "auth-cookie-secret").read_text(encoding="utf-8").strip())
        users = read_auth_users(paths.auth_config_path)
        assert users, "row auth config produced no users"
        user = users[0]
        correct = hmac.new(secret, f"{user.username}:{user.password}".encode("utf-8"), hashlib.sha256).hexdigest()
        assert _get(port, "/api/background/status", cookie=f"{AUTH_COOKIE_NAME}_{port}={correct}") == HTTPStatus.OK

        # A wrong cookie (same user, wrong password -> wrong HMAC) is refused, not silently
        # accepted. A cookie minted from a foreign root's secret would fail identically.
        wrong = hmac.new(secret, f"{user.username}:not-the-password".encode("utf-8"), hashlib.sha256).hexdigest()
        assert _get(port, "/api/background/status", cookie=f"{AUTH_COOKIE_NAME}_{port}={wrong}") != HTTPStatus.OK
    finally:
        if server is not None:
            server.stop()
        stop_isolated_tmux_runtime(tmux_runtime)

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The process supervisor's restart probe must not manufacture authentication ERRORs.

boot.sh polls a restarted server before any operator cookie exists. While it polled the
``readonly`` route ``/api/ping``, every ordinary restart wrote five ``authentication_required``
ERROR rows into the operator log ring - one readiness probe plus four stability probes - and a
restart before a release soak seeded the ring the soak requires to be empty.

The 401 logging is correct and stays. The fix is one exact ``GET /healthz`` route registered
``PUBLIC`` plus a supervisor that probes it. These tests replay boot.sh's probe sequence against a
TLS fixture server and read the server's own log ring; the negative control aims the identical
sequence at the old protected route and proves the zero-ERROR assertion still fails when it should.
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import subprocess
import threading
from dataclasses import replace
from http import HTTPStatus
from http.client import HTTPConnection
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any

import pytest

from yolomux_lib import common
from yolomux_lib import http_routes
from yolomux_lib import server_auth
from yolomux_lib import server_logs
from yolomux_lib.login_rate_limit import BucketPolicy
from yolomux_lib.login_rate_limit import LoginRateLimiter
from yolomux_lib.login_rate_limit import LoginRatePolicy
from yolomux_lib.server import TmuxWebtermHTTPServer

pytestmark = pytest.mark.socket

ROOT = Path(__file__).resolve().parents[1]
BOOT_SH = ROOT / "boot.sh"

VALID_USER = "healthz-admin"
VALID_PASSWORD = "healthz-password"

# The one liveness route the supervisor is allowed to poll unauthenticated.
LIVENESS_PATH = "/healthz"
# The protected route the supervisor used to poll; kept here only as the negative control.
PROTECTED_PROBE_PATH = "/api/ping"

# boot.sh performs exactly one readiness probe followed by four stability probes.
READINESS_PROBES = 1
STABILITY_PROBES = 4
BOOT_PROBE_COUNT = READINESS_PROBES + STABILITY_PROBES

# The public set as shipped in 0.7.0, before this change. Frozen so the auth boundary can only be
# widened deliberately.
PUBLIC_ROUTES_BEFORE_LIVENESS = {
    ("GET", "/static/*"),
    ("GET", "/api/auth-setup"),
    ("GET", "/login"),
    ("GET", "/logout"),
    ("POST", "/login"),
    ("GET", "/share/*"),
}
EXPECTED_PUBLIC_ROUTES = PUBLIC_ROUTES_BEFORE_LIVENESS | {("GET", "/healthz")}

# Attribute lookups the shared response parent makes on the app for every request. A liveness
# probe must never grow this: anything else means /healthz started consulting a subsystem.
RESPONSE_PARENT_APP_ATTRIBUTES = {"observe_http_delivery", "record_performance_sample"}


def auth_yaml() -> str:
    return f"""users:
  - username: "{VALID_USER}"
    password: "{VALID_PASSWORD}"
    role: "admin"
"""


def basic_auth_header(username: str, password: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def request(port: int, method: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


class ProbeApp:
    """An app that records and refuses every attribute the request path did not already own.

    Refusing by AttributeError is what makes this a measurement rather than a mock: a handler
    that reached for tmux, jobd, watchd, statusd, the filesystem, or a local-service client would
    both be recorded here and fail the request.
    """

    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.dangerously_yolo = False
        self.login_rate_limiter: LoginRateLimiter | None = None
        self.touched: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.touched.append(name)
        raise AttributeError(name)


def generous_rate_policy() -> LoginRatePolicy:
    """Wide-open buckets so a credential test measures auth logging, not the throttle."""
    return LoginRatePolicy(
        exact_bucket=BucketPolicy(10_000, 10_000),
        nearby_bucket=BucketPolicy(10_000, 10_000),
        broad_bucket=BucketPolicy(20_000, 20_000),
        global_bucket=BucketPolicy(50_000, 50_000),
    )


@pytest.fixture
def probe_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ProbeApp:
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(auth_yaml(), encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    app = ProbeApp()
    app.login_rate_limiter = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", policy=generous_rate_policy())
    return app


@pytest.fixture
def tls_context(tmp_path: Path) -> ssl.SSLContext:
    """A throwaway certificate so the real boot.sh https:// probes can reach the fixture."""
    cert_path = tmp_path / "probe.crt"
    key_path = tmp_path / "probe.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=localhost",
            "-keyout", str(key_path), "-out", str(cert_path),
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return context


class RunningServer:
    def __init__(self, app: ProbeApp, tls: ssl.SSLContext | None) -> None:
        self.app = app
        self.server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app, tls_context=tls)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        app.touched.clear()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def plain_server(probe_app: ProbeApp):
    runtime = RunningServer(probe_app, None)
    try:
        yield runtime
    finally:
        runtime.close()


@pytest.fixture
def tls_server(probe_app: ProbeApp, tls_context: ssl.SSLContext):
    runtime = RunningServer(probe_app, tls_context)
    try:
        yield runtime
    finally:
        runtime.close()


def boot_shell_function(name: str) -> str:
    """Return one boot.sh function verbatim so these tests run the shipped supervisor code."""
    source = BOOT_SH.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", source, re.S | re.M)
    assert match, f"boot.sh no longer defines {name}()"
    return match.group(0)


def run_probe_shape(port: int, path: str) -> list[int]:
    """Issue boot.sh's probe request BOOT_PROBE_COUNT times at ``path`` and return the codes.

    The test container ships no curl, so this reproduces the request `curl -sk https://...` sends:
    an unverified TLS connection, no cookie or Authorization header, and ``Accept: */*``. That
    Accept value is load-bearing - it is why the server answered the old probe with a JSON 401
    through the API response parent instead of an HTML login redirect, and therefore why every
    probe wrote an ERROR row. The only variable between this test and its negative control is
    the probed route.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    codes = []
    for _ in range(BOOT_PROBE_COUNT):
        connection = HTTPSConnection("localhost", port, timeout=10, context=context)
        connection.request("GET", path, headers={"Accept": "*/*", "User-Agent": "curl/8.0.0"})
        response = connection.getresponse()
        response.read()
        codes.append(int(response.status))
        connection.close()
    return codes


def count_route_requests(monkeypatch: pytest.MonkeyPatch, method: str, path: str) -> list[str]:
    """Count requests that actually reach one registered route, without changing its role."""
    route = next(
        candidate for candidate in http_routes.ALL_ROUTES
        if (candidate.method, candidate.path) == (method, path)
    )
    seen: list[str] = []

    def counting_handler(request: Any, parsed: Any, current: http_routes.Route) -> Any:
        seen.append(path)
        return route.handler(request, parsed, current)

    counted = replace(route, handler=counting_handler)
    patched = tuple(
        counted if candidate is route else candidate
        for candidate in http_routes.ROUTES_BY_METHOD[method]
    )
    monkeypatch.setitem(http_routes.ROUTES_BY_METHOD, method, patched)
    return seen


def authentication_failure_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the ERROR rows the API response parent writes for a rejected authentication."""
    rows = []
    for entry in entries:
        if entry["level"] != "error" or entry["source"] != "api-response":
            continue
        if json.loads(entry["message"]).get("code") == "authentication_required":
            rows.append(entry)
    return rows


@pytest.fixture
def log_ring() -> Any:
    server_logs.SERVER_LOGS.clear()
    yield server_logs.SERVER_LOGS
    server_logs.SERVER_LOGS.clear()


def test_boot_probe_sequence_records_no_authentication_failures(monkeypatch, tls_server, log_ring):
    """Acceptance: one readiness plus four stability probes seed zero authentication ERRORs.

    The release soak accepts only zero serverLogErrors, so an ordinary restart before a soak must
    leave the ring untouched.
    """
    probes = count_route_requests(monkeypatch, "GET", LIVENESS_PATH)

    codes = run_probe_shape(tls_server.port, LIVENESS_PATH)

    assert codes == [int(HTTPStatus.OK)] * BOOT_PROBE_COUNT
    assert len(probes) == BOOT_PROBE_COUNT, probes
    entries = log_ring.payload()["logs"]
    assert authentication_failure_rows(entries) == []
    assert [entry for entry in entries if entry["level"] == "error"] == []


def test_boot_probe_sequence_on_the_protected_route_records_five_authentication_failures(
    monkeypatch, tls_server, log_ring
):
    """Negative control and defect reproduction.

    The identical five-probe driver aimed at /api/ping - the route the shipped 0.7.0 supervisor
    polled - produces exactly the five ERROR rows measured on a real restart. This is what proves
    the zero-row assertion above is falsifiable rather than vacuous.
    """
    probes = count_route_requests(monkeypatch, "GET", PROTECTED_PROBE_PATH)

    codes = run_probe_shape(tls_server.port, PROTECTED_PROBE_PATH)

    assert codes == [int(HTTPStatus.UNAUTHORIZED)] * BOOT_PROBE_COUNT
    assert len(probes) == 0, "an unauthenticated probe must never reach the protected handler"
    assert len(authentication_failure_rows(log_ring.payload()["logs"])) == BOOT_PROBE_COUNT


def test_healthz_answers_two_hundred_unauthenticated_and_consults_no_subsystem(plain_server, log_ring):
    status, body = request(plain_server.port, "GET", LIVENESS_PATH, headers={"Accept": "*/*"})

    assert status == HTTPStatus.OK
    envelope = json.loads(body)
    assert envelope["state"] == "ready"
    assert envelope["data"] == {"ok": True}
    assert set(plain_server.app.touched) <= RESPONSE_PARENT_APP_ATTRIBUTES, plain_server.app.touched
    assert authentication_failure_rows(log_ring.payload()["logs"]) == []


def test_only_exact_get_healthz_is_public(plain_server, log_ring):
    assert http_routes.route_for_request("POST", LIVENESS_PATH) is None
    assert http_routes.route_for_request("GET", f"{LIVENESS_PATH}/anything") is None

    post_status, _post_body = request(plain_server.port, "POST", LIVENESS_PATH, headers={"Accept": "application/json"})
    sub_status, _sub_body = request(
        plain_server.port, "GET", f"{LIVENESS_PATH}/anything", headers={"Accept": "application/json"}
    )

    assert post_status == HTTPStatus.UNAUTHORIZED
    assert sub_status == HTTPStatus.UNAUTHORIZED


def test_public_route_set_contains_exactly_one_liveness_route():
    public = {
        (route.method, route.path)
        for route in http_routes.ALL_ROUTES
        if route.role == http_routes.PUBLIC
    }

    assert public == EXPECTED_PUBLIC_ROUTES
    # The public set grew by exactly one exact-match route and lost none.
    assert public - PUBLIC_ROUTES_BEFORE_LIVENESS == {("GET", LIVENESS_PATH)}
    assert PUBLIC_ROUTES_BEFORE_LIVENESS - public == set()
    assert len(public) == len(PUBLIC_ROUTES_BEFORE_LIVENESS) + 1

    liveness = [route for route in http_routes.ALL_ROUTES if route.path == LIVENESS_PATH]
    assert len(liveness) == 1
    assert liveness[0].method == "GET"
    assert liveness[0].role == http_routes.PUBLIC
    assert "*" not in liveness[0].path
    assert liveness[0].share_access == http_routes.SHARE_ACCESS_NONE
    assert liveness[0].normal_session_local_service is False


def test_api_ping_remains_protected_and_still_logs_an_authentication_error(plain_server, log_ring):
    route = next(
        candidate for candidate in http_routes.ALL_ROUTES
        if (candidate.method, candidate.path) == ("GET", PROTECTED_PROBE_PATH)
    )
    assert route.role == "readonly"

    status, _body = request(plain_server.port, "GET", PROTECTED_PROBE_PATH, headers={"Accept": "application/json"})

    assert status == HTTPStatus.UNAUTHORIZED
    assert len(authentication_failure_rows(log_ring.payload()["logs"])) == 1


def test_missing_and_wrong_credentials_both_still_record_authentication_errors(plain_server, log_ring):
    """The property worth preserving: neither absent nor bad credentials go unrecorded."""
    missing_status, _missing = request(
        plain_server.port, "GET", PROTECTED_PROBE_PATH, headers={"Accept": "application/json"}
    )
    after_missing = len(authentication_failure_rows(log_ring.payload()["logs"]))

    wrong_status, _wrong = request(
        plain_server.port,
        "GET",
        PROTECTED_PROBE_PATH,
        headers={**basic_auth_header(VALID_USER, "not-the-password"), "Accept": "application/json"},
    )
    after_wrong = len(authentication_failure_rows(log_ring.payload()["logs"]))

    assert missing_status == HTTPStatus.UNAUTHORIZED
    assert wrong_status == HTTPStatus.UNAUTHORIZED
    assert after_missing == 1
    assert after_wrong == 2


def test_boot_sh_probes_only_the_public_liveness_route():
    """Keep the shell supervisor and the server's public set from drifting apart."""
    for name in ("wait_for_port", "verify_port_stable"):
        source = boot_shell_function(name)
        assert f"{LIVENESS_PATH}\"" in source, source
        assert PROTECTED_PROBE_PATH not in source, source
        assert "401" not in source, source
        assert "200" in source, source

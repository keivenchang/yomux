"""Regression gate E: authentication remains correct under immediate and concurrent use."""
from __future__ import annotations

import multiprocessing
import threading
import time
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from yolomux_lib import auth
from yolomux_lib import common
from yolomux_lib import server_auth
from yolomux_lib.login_rate_limit import BucketPolicy
from yolomux_lib.login_rate_limit import LoginRateLimiter
from yolomux_lib.login_rate_limit import LoginRatePolicy
from yolomux_lib.server import TmuxWebtermHTTPServer


pytestmark = pytest.mark.socket

_USERNAME = "gate-admin"
_PASSWORD = "gate-password"


def _active_auth_yaml() -> str:
    return f'''users:
  - username: "{_USERNAME}"
    password: "{_PASSWORD}"
    role: "admin"
'''


def _request(port: int, method: str, path: str, *, body: str | None = None, headers: dict[str, str] | None = None) -> tuple[int, list[tuple[str, str]], bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, response.getheaders(), response.read()
    connection.close()
    return result


def _start_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TmuxWebtermHTTPServer, threading.Thread]:
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(_active_auth_yaml(), encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), SimpleNamespace(sessions=[], dangerously_yolo=False))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: TmuxWebtermHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _limiter_policy() -> LoginRatePolicy:
    return LoginRatePolicy(
        exact_bucket=BucketPolicy(1, 0.001),
        nearby_bucket=BucketPolicy(10, 0.001),
        broad_bucket=BucketPolicy(10, 0.001),
        global_bucket=BucketPolicy(10, 0.001),
    )


def _reserve_last_token(database_path: str, start: multiprocessing.synchronize.Event, results: multiprocessing.queues.Queue) -> None:
    start.wait()
    decision = LoginRateLimiter(database_path, policy=_limiter_policy()).check_and_reserve("203.0.113.7", "gate-user")
    results.put(decision.admitted)


def _normalize_plaintext_auth_config(auth_path: str, start: multiprocessing.synchronize.Event, results: multiprocessing.queues.Queue) -> None:
    start.wait()
    users = auth.initialize_auth_config(Path(auth_path))
    results.put(users[0].password)


def _process_results(processes: list[multiprocessing.Process], results: multiprocessing.queues.Queue) -> list[object]:
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0, f"worker {process.pid} exited {process.exitcode}"
    return [results.get(timeout=1) for _ in processes]


def test_gate_e1_login_then_immediate_authenticated_request_never_logs_out(monkeypatch, tmp_path):
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        for iteration in range(10):
            started_at = time.monotonic()
            body = urlencode({"username": _USERNAME, "password": _PASSWORD, "next": "/api/ping"})
            status, headers, _ = _request(port, "POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            assert status == HTTPStatus.SEE_OTHER, f"login iteration {iteration} returned {status}"
            cookie = next(value.split(";", 1)[0] for name, value in headers if name.lower() == "set-cookie" and value.startswith(f"{common.AUTH_COOKIE_NAME}_{port}="))
            status, response_headers, _ = _request(port, "GET", "/api/ping", headers={"Cookie": cookie})
            assert time.monotonic() - started_at < 1, f"iteration {iteration} did not act within one second"
            assert status == HTTPStatus.OK, f"iteration {iteration} lost authentication with {status}"
            assert not any(name.lower() == "set-cookie" and value.startswith(f"{common.AUTH_LOGOUT_COOKIE_NAME}_{port}=") for name, value in response_headers)
    finally:
        _stop_server(server, thread)


def test_gate_e2_two_processes_cannot_both_spend_last_login_token(tmp_path):
    database_path = tmp_path / "login-throttle.sqlite3"
    limiter = LoginRateLimiter(database_path, policy=_limiter_policy())
    limiter._initialize()
    limiter._load_secret()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [context.Process(target=_reserve_last_token, args=(str(database_path), start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    admissions = _process_results(processes, results)
    assert admissions.count(True) == 1
    assert admissions.count(False) == 1


def test_gate_e3_one_plaintext_credential_normalizes_to_one_stored_hash(tmp_path):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(_active_auth_yaml(), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [context.Process(target=_normalize_plaintext_auth_config, args=(str(auth_path), start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    returned_hashes = _process_results(processes, results)
    stored_users = auth.read_auth_users(auth_path)
    assert len(stored_users) == 1
    assert auth.auth_password_is_hash(stored_users[0].password)
    assert set(returned_hashes) == {stored_users[0].password}

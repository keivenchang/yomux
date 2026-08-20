"""A terminal response to a request whose body was never read must close the connection.

BaseHTTPRequestHandler frames the next request at the byte after the current response.  When a
handler answers a POST without reading its declared Content-Length body, those body bytes stay in
the socket and the parser reads them as the next request line.  A large leftover body produces a
bogus ``414 Request-URI Too Long``; a small leftover body without a newline hangs the connection
until the peer gives up.  Both are framing defects, not parser-limit defects, so the regression is
the connection state after the response and never a widened request-line limit.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from yolomux_lib import common
from yolomux_lib import http_routes
from yolomux_lib import server as server_module
from yolomux_lib import server_auth
from yolomux_lib.server import Handler
from yolomux_lib.server import TmuxWebtermHTTPServer


pytestmark = pytest.mark.socket

_USERNAME = "framing-admin"
_PASSWORD = "framing-password"

# Larger than the 65,537 bytes BaseHTTPRequestHandler reads before it emits 414, so a leftover body
# of this size reproduces the bogus 414 rather than blocking on a missing newline.
_LARGE_BODY_BYTES = 98_258
# Smaller than that limit and free of newlines, so a leftover body of this size reproduces the hang.
_SMALL_BODY_BYTES = 2_000


def _auth_yaml() -> str:
    return f'''users:
  - username: "{_USERNAME}"
    password: "{_PASSWORD}"
    role: "admin"
'''


def _start_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app: SimpleNamespace | None = None,
    *,
    auth_yaml: str | None = None,
):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(_auth_yaml() if auth_yaml is None else auth_yaml, encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    server = TmuxWebtermHTTPServer(
        ("127.0.0.1", 0),
        app if app is not None else SimpleNamespace(sessions=[], dangerously_yolo=False),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: TmuxWebtermHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _admin_cookie(port: int) -> str:
    body = urlencode({"username": _USERNAME, "password": _PASSWORD, "next": "/api/ping"})
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        response = connection.getresponse()
        response.read()
        assert response.status == HTTPStatus.SEE_OTHER, response.status
        headers = response.getheaders()
    finally:
        connection.close()
    return next(
        value.split(";", 1)[0]
        for name, value in headers
        if name.lower() == "set-cookie" and value.startswith(f"{common.AUTH_COOKIE_NAME}_{port}=")
    )


def _recv_exactly(client: socket.socket, count: int, buffer: bytearray) -> None:
    while len(buffer) < count:
        chunk = client.recv(65536)
        if not chunk:
            raise AssertionError(f"connection ended after {len(buffer)} of {count} expected bytes")
        buffer.extend(chunk)


def _read_one_response(client: socket.socket) -> tuple[str, dict[str, str]]:
    """Read exactly one HTTP/1.1 response, leaving anything after it on the socket."""
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = client.recv(65536)
        if not chunk:
            raise AssertionError(f"connection ended before response headers: {bytes(buffer)[:200]!r}")
        buffer.extend(chunk)
    head, _, rest = bytes(buffer).partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    remainder = bytearray(rest)
    _recv_exactly(client, int(headers.get("content-length", "0")), remainder)
    return status_line, headers


def _trailing_bytes_after_response(client: socket.socket, *, timeout: float = 5.0) -> bytes:
    """Return whatever the server sends after its response, or b"" when it closes the connection.

    A browser does not pipeline: it reads the response, then reuses the idle connection later.  So
    the observable that matters is the connection state once the response is complete.  b"" means
    the server closed and the next request will be framed correctly on a fresh connection.
    """
    client.settimeout(timeout)
    chunks = bytearray()
    while True:
        try:
            chunk = client.recv(65536)
        except socket.timeout as exc:
            raise AssertionError(
                f"connection hung open for {timeout}s with an unread body still in the socket; "
                f"trailing bytes so far: {bytes(chunks)[:200]!r}"
            ) from exc
        except ConnectionResetError:
            # A reset still ends the connection, but it can discard bytes the peer never read.
            return bytes(chunks) + b"<connection reset by peer>"
        if not chunk:
            return bytes(chunks)
        chunks.extend(chunk)


def _post_with_unread_body(
    port: int,
    cookie: str,
    path: str,
    body: bytes,
    *,
    read_delay: float = 0.0,
    content_length: str | None = None,
) -> tuple[str, dict[str, str], bytes]:
    client = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body) if content_length is None else content_length}\r\n"
            "\r\n"
        ).encode("ascii") + body
        client.sendall(request)
        if read_delay:
            # Closing a socket that still holds unread bytes can reset the connection and discard a
            # response the peer has not collected yet.  A slow reader must still get the response.
            time.sleep(read_delay)
        status_line, headers = _read_one_response(client)
        return status_line, headers, _trailing_bytes_after_response(client)
    finally:
        client.close()


def _request_line_capture_logs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    logs: list[str] = []

    def record_request_line_capture(_handler, fmt: str, *args: object) -> None:
        logs.append(fmt % args)

    monkeypatch.setattr(Handler, "log_error", record_request_line_capture)
    return logs


def _request_line_capture_payload(logs: list[str]) -> dict[str, object]:
    captures = [log for log in logs if log.startswith("request-line-capture ")]
    assert len(captures) == 1, logs
    prefix, payload = captures[0].split(" ", 1)
    assert prefix == "request-line-capture"
    return json.loads(payload)


def _oversized_request_line(method: str, target_bytes: int) -> bytes:
    return f"{method} /".encode("ascii") + (b"a" * target_bytes) + b" HTTP/1.1\r\n"


def test_oversized_request_line_capture_stops_at_its_own_evidence_bound(monkeypatch, tmp_path):
    """A diagnostic 414 records only the bytes its own limit allows, never the rest of the line."""
    logs = _request_line_capture_logs(monkeypatch)
    # Keep the production limit unchanged while making a two-byte remainder observable.  Buffered
    # readers may return more than requested from peek(), so the capture must clip it before finding
    # a newline or it consumes the folded header that starts the next parser context.
    monkeypatch.setattr(server_module, "HTTP_REQUEST_LINE_CAPTURE_LIMIT", 65_539)
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    request_line = _oversized_request_line("PATCH", 65_540)
    # Trailing bytes only exist to give peek() more than `remaining` to hand back.  The oversized
    # line reaches its own newline long before them, so this test proves the clip and nothing about
    # header parsing -- obs-fold framing is proven separately below, against the real parser.
    trailing_input = b"X-After-The-Line: never-captured\r\nHost: ignored\r\n\r\n"
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            client.sendall(request_line + trailing_input)
            status_line, headers = _read_one_response(client)
            trailing = _trailing_bytes_after_response(client)
        finally:
            client.close()
        payload = _request_line_capture_payload(logs)
        assert str(HTTPStatus.REQUEST_URI_TOO_LONG.value) in status_line, status_line
        assert headers.get("connection") == "close", headers
        assert trailing == b"", trailing
        assert payload["method"] == "PATCH"
        assert payload["request_line"] == request_line[:65_537].decode("latin-1")
        assert payload["request_line_complete"] is False
        assert payload["request_line_bytes"] == 65_537
    finally:
        _stop_server(server, thread)


def test_slow_valid_oversized_request_line_reports_method_and_socket_context(monkeypatch, tmp_path):
    """A slow peer cannot block 414 logging, and the partial line still retains its method/context."""
    logs = _request_line_capture_logs(monkeypatch)
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    # Exactly what BaseHTTPRequestHandler reads before 414, cut from an otherwise valid GET line.
    # Its remaining target/version bytes represent a slow peer; the diagnostic must not wait for them.
    complete_request = _oversized_request_line("GET", 65_540)
    partial_request = complete_request[:65_537]
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            started = time.monotonic()
            client.sendall(partial_request)
            status_line, headers = _read_one_response(client)
            elapsed = time.monotonic() - started
            trailing = _trailing_bytes_after_response(client)
        finally:
            client.close()
        payload = _request_line_capture_payload(logs)
        assert elapsed < 1.0, elapsed
        assert str(HTTPStatus.REQUEST_URI_TOO_LONG.value) in status_line, status_line
        assert headers.get("connection") == "close", headers
        assert trailing == b"", trailing
        assert payload["method"] == "GET"
        assert payload["client"].startswith("127.0.0.1:"), payload
        assert isinstance(payload["connection"], dict), payload
        assert payload["connection"]["local"][0] == "127.0.0.1", payload
        assert isinstance(payload["connection"]["fd"], int), payload
        assert payload["request_line"] == partial_request.decode("latin-1")
        assert payload["request_line_complete"] is False
        assert payload["request_line_bytes"] == len(partial_request)
    finally:
        _stop_server(server, thread)


# --- obs-fold framing --------------------------------------------------------
# RFC 7230 3.2.4 deprecated obs-fold: a header value continued on a following line that starts
# with SP or HTAB.  Python's email parser still unfolds it INTO THE PREVIOUS header value, so a
# folded `Content-Length` never reaches ``self.headers.get("Content-Length")`` -- the one input
# ``request_has_unread_body`` uses to decide whether the connection can be reused.  The server then
# keeps the connection alive with the declared body still on the socket, and those bytes are read
# as the next request line.  A peer that unfolds instead (as the RFC's other option allows) frames
# that same byte range as a body.  Two parties, two framings, one socket: that is a desync, so this
# parser fails closed and refuses the message rather than guessing which framing the peer meant.

# A complete second request, so a desync shows up as a whole extra response rather than a hang.
_SMUGGLED_REQUEST = b"GET /api/ping HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"


def _raw_exchange(port: int, raw: bytes) -> tuple[str, dict[str, str], bytes]:
    """Send one byte stream and return its first response plus everything after it."""
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        client.sendall(raw)
        status_line, headers = _read_one_response(client)
        return status_line, headers, _trailing_bytes_after_response(client)
    finally:
        client.close()


@pytest.mark.parametrize("line_end", ["\r\n", "\n"], ids=["crlf", "lf"])
@pytest.mark.parametrize("continuation", [" ", "\t"], ids=["space", "tab"])
@pytest.mark.parametrize("expect_continue", [False, True], ids=["no-expect", "expect-100"])
def test_obs_folded_content_length_cannot_smuggle_a_second_request(
    monkeypatch,
    tmp_path,
    line_end,
    continuation,
    expect_continue,
):
    """A Content-Length hidden in a fold must not leave its declared bytes to be read as a request."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"POST /api/ping HTTP/1.1{line_end}"
        f"Host: 127.0.0.1:{port}{line_end}"
        f"Content-Type: application/json{line_end}"
        f"X-Fold-Bait: bait{line_end}"
        f"{continuation}Content-Length: {len(_SMUGGLED_REQUEST)}{line_end}"
        f"{'Expect: 100-continue' + line_end if expect_continue else ''}"
        f"{line_end}"
    ).encode("ascii") + _SMUGGLED_REQUEST
    try:
        status_line, headers, trailing = _raw_exchange(port, raw)
    finally:
        _stop_server(server, thread)
    # 400, not the 401/404 the route and auth layers would answer: the message is refused at the
    # parser, so the fold is never normalized into X-Fold-Bait and acted on downstream.
    assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
    assert headers.get("connection") == "close", headers
    # The smuggled GET must never be answered.  On the unfixed parser this held a second response.
    assert trailing == b"", trailing


@pytest.mark.parametrize("line_end", ["\r\n", "\n"], ids=["crlf", "lf"])
@pytest.mark.parametrize("continuation", [" ", "\t"], ids=["space", "tab"])
def test_obs_fold_continuing_content_length_itself_is_refused(monkeypatch, tmp_path, line_end, continuation):
    """A folded value carries a raw CRLF into Content-Length; it must be refused, not parsed."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"POST /api/ping HTTP/1.1{line_end}"
        f"Host: 127.0.0.1:{port}{line_end}"
        f"Content-Length: 0{line_end}"
        f"{continuation}Content-Length: {len(_SMUGGLED_REQUEST)}{line_end}"
        f"{line_end}"
    ).encode("ascii") + _SMUGGLED_REQUEST
    try:
        status_line, headers, trailing = _raw_exchange(port, raw)
    finally:
        _stop_server(server, thread)
    assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
    assert headers.get("connection") == "close", headers
    assert trailing == b"", trailing


@pytest.mark.parametrize("line_end", ["\r\n", "\n"], ids=["crlf", "lf"])
@pytest.mark.parametrize("continuation", [" ", "\t"], ids=["space", "tab"])
def test_leading_header_continuation_is_refused_instead_of_silently_dropped(monkeypatch, tmp_path, line_end, continuation):
    """A first header line that is a continuation belongs to nothing; Python drops it as a defect."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"GET /api/ping HTTP/1.1{line_end}"
        f"{continuation}X-Orphan-Continuation: dropped-without-a-trace{line_end}"
        f"Host: 127.0.0.1:{port}{line_end}"
        f"{line_end}"
    ).encode("ascii")
    try:
        status_line, headers, trailing = _raw_exchange(port, raw)
    finally:
        _stop_server(server, thread)
    # The unfixed parser answered 401 here: it dropped the orphan line and served the request.
    assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
    assert headers.get("connection") == "close", headers
    assert trailing == b"", trailing


def test_ordinary_headers_still_parse_after_the_obs_fold_refusal(monkeypatch, tmp_path):
    """The refusal must key on folded framing only, not on every header block."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"GET /api/ping HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Accept: application/json\r\n"
        "X-Not-Folded: one line, one value; punctuation: colons, and  double  spaces\r\n"
        "\r\n"
    ).encode("ascii")
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        client.sendall(raw)
        status_line, headers = _read_one_response(client)
    finally:
        client.close()
        _stop_server(server, thread)
    # Unauthenticated, so 401 is the correct answer -- the point is that it reached auth at all.
    # Nothing is read after the response here: an unfolded GET declares no body, so keeping the
    # connection alive is the correct framing and would look like a hang to the trailing-byte probe.
    assert str(HTTPStatus.UNAUTHORIZED.value) in status_line, status_line
    assert headers.get("connection") != "close", headers


def test_valid_multipart_upload_header_defects_do_not_trigger_framing_rejection(monkeypatch, tmp_path):
    """Header-only MIME parsing must not turn a valid browser upload into malformed HTTP."""
    uploads: list[tuple[str, list[object], str]] = []

    def upload_files(session, files, *, auth_username=""):
        uploads.append((session, files, auth_username))
        return {"ok": True, "session": session}, HTTPStatus.OK

    app = SimpleNamespace(sessions=[], dangerously_yolo=False, upload_files=upload_files)
    server, thread = _start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    boundary = "----WebKitFormBoundaryYOLOmuxGateWitness"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="mobile-upload.txt"\r\n'
        "Content-Type: text/plain\r\n"
        "\r\n"
        "mobile browser upload\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    try:
        cookie = _admin_cookie(port)
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/upload?session=1",
                body=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Cookie": cookie,
                },
            )
            response = connection.getresponse()
            response.read()
            assert response.status == HTTPStatus.OK, response.status
            assert response.getheader("Connection") != "close", response.getheaders()
        finally:
            connection.close()
        assert len(uploads) == 1, uploads
        assert uploads[0][0] == "1"
        assert uploads[0][2] == _USERNAME
    finally:
        _stop_server(server, thread)


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_declared_body_ownership_is_independent_of_method(method):
    """Every method with a positive Content-Length owns bytes before connection reuse."""
    request = SimpleNamespace(
        command=method,
        headers={"Content-Length": "1"},
        request_body_consumed=False,
    )
    assert server_auth.AuthMixin.request_has_unread_body(request) is True


@pytest.mark.parametrize(
    "framing_headers",
    [
        "Transfer-Encoding: chunked\r\n",
        "Content-Length: 0\r\nTransfer-Encoding: chunked\r\n",
        "Transfer-Encoding:\r\nTransfer-Encoding: chunked\r\n",
    ],
    ids=["transfer-encoding", "content-length-plus-transfer-encoding", "empty-first-duplicate-transfer-encoding"],
)
def test_unsupported_transfer_encoding_is_refused_before_dispatch(monkeypatch, tmp_path, framing_headers):
    """BaseHTTPRequestHandler cannot decode transfer coding, so it must never dispatch one."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"POST /api/ping HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"{framing_headers}"
        "\r\n"
        "0\r\n\r\n"
    ).encode("ascii") + _SMUGGLED_REQUEST
    try:
        status_line, headers, trailing = _raw_exchange(port, raw)
    finally:
        _stop_server(server, thread)
    assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
    assert headers.get("connection") == "close", headers
    assert trailing == b"", trailing


def test_invalid_framing_is_refused_before_expect_100_continue(monkeypatch, tmp_path):
    """A framing rejection is the first response; invalid input is never invited to send a body."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"POST /api/settings HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Content-Length: 2\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Expect: 100-continue\r\n"
        "\r\n"
    ).encode("ascii")
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        client.sendall(raw)
        status_line, headers = _read_one_response(client)
        trailing = _trailing_bytes_after_response(client)
    finally:
        client.close()
        _stop_server(server, thread)
    assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
    assert headers.get("connection") == "close", headers
    assert trailing == b"", trailing


def test_valid_framing_receives_expect_100_before_the_body(monkeypatch, tmp_path):
    """The early validator preserves the standard 100-continue path for an unambiguous body."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    body = json.dumps({"settings": {}}).encode("utf-8")
    try:
        cookie = _admin_cookie(port)
        raw = (
            f"POST /api/settings HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Expect: 100-continue\r\n"
            "\r\n"
        ).encode("ascii")
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            client.sendall(raw)
            interim_status, _interim_headers = _read_one_response(client)
            assert str(HTTPStatus.CONTINUE.value) in interim_status, interim_status
            client.sendall(body)
            final_status, final_headers = _read_one_response(client)
        finally:
            client.close()
        assert str(HTTPStatus.CONTINUE.value) not in final_status, final_status
        assert str(HTTPStatus.BAD_REQUEST.value) not in final_status, final_status
        assert final_headers.get("connection") != "close", final_headers
    finally:
        _stop_server(server, thread)


@pytest.mark.parametrize(
    "content_length_headers",
    [
        "Content-Length: 1\r\nContent-Length: 1\r\n",
        "Content-Length: 1\r\nContent-Length: 2\r\n",
        "Content-Length: +1\r\n",
        "Content-Length: 1, 1\r\n",
        f"Content-Length: {'9' * 30}\r\n",
    ],
    ids=["duplicate-same", "duplicate-different", "signed", "comma-list", "unrepresentable"],
)
def test_ambiguous_or_unrepresentable_content_length_is_refused(monkeypatch, tmp_path, content_length_headers):
    """One strict decimal field is the only fixed-length framing this server can own."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    raw = (
        f"POST /api/settings HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"{content_length_headers}"
        "\r\n"
    ).encode("ascii")
    try:
        status_line, headers, trailing = _raw_exchange(port, raw)
    finally:
        _stop_server(server, thread)
    assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
    assert headers.get("connection") == "close", headers
    assert trailing == b"", trailing


@pytest.mark.parametrize("method, expected_status", [("GET", HTTPStatus.OK), ("HEAD", HTTPStatus.NOT_FOUND)])
def test_positive_content_length_closes_get_and_head_before_body_reparse(
    monkeypatch,
    tmp_path,
    method,
    expected_status,
):
    """Read ownership follows Content-Length, including methods that normally carry no body."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        raw = (
            f"{method} /api/ping HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            f"Content-Length: {len(_SMUGGLED_REQUEST)}\r\n"
            "\r\n"
        ).encode("ascii") + _SMUGGLED_REQUEST
        status_line, headers, trailing = _raw_exchange(port, raw)
    finally:
        _stop_server(server, thread)
    assert str(expected_status.value) in status_line, status_line
    if method != "HEAD":
        assert headers.get("connection") == "close", headers
    assert trailing == b"", trailing


def test_stalled_declared_body_has_a_bounded_read(monkeypatch, tmp_path):
    """One partial body cannot hold a request thread forever."""
    monkeypatch.setattr(server_module, "HTTP_REQUEST_BODY_INACTIVITY_TIMEOUT_SECONDS", 0.2, raising=False)
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        raw = (
            f"POST /api/settings HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 20\r\n"
            "\r\n"
            "{"
        ).encode("ascii")
        client = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            client.sendall(raw)
            status_line, headers = _read_one_response(client)
        finally:
            client.close()
        assert str(HTTPStatus.REQUEST_TIMEOUT.value) in status_line, status_line
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_required_body_route_without_content_length_keeps_length_required_semantics(monkeypatch, tmp_path):
    """The shared framing cache must not collapse an absent Content-Length into a declared zero."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        raw = (
            f"POST /api/settings HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
        ).encode("ascii")
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            client.sendall(raw)
            status_line, _headers = _read_one_response(client)
        finally:
            client.close()
        assert str(HTTPStatus.LENGTH_REQUIRED.value) in status_line, status_line
    finally:
        _stop_server(server, thread)


def test_short_declared_body_is_not_reported_consumed(monkeypatch, tmp_path):
    """EOF before Content-Length is a terminal framing error, never a successful short body."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        raw = (
            f"POST /api/settings HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 20\r\n"
            "\r\n"
            "{"
        ).encode("ascii")
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            client.sendall(raw)
            client.shutdown(socket.SHUT_WR)
            status_line, headers = _read_one_response(client)
        finally:
            client.close()
        assert str(HTTPStatus.BAD_REQUEST.value) in status_line, status_line
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_slow_valid_body_may_exceed_the_inactivity_window_in_total(monkeypatch, tmp_path):
    """The body bound is inactivity-based, not a total deadline that rejects steady progress."""
    monkeypatch.setattr(server_module, "HTTP_REQUEST_BODY_INACTIVITY_TIMEOUT_SECONDS", 0.5)
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    body = json.dumps({"settings": {}}).encode("utf-8")
    pieces = [body[:4], body[4:8], body[8:12], body[12:]]
    try:
        cookie = _admin_cookie(port)
        raw = (
            f"POST /api/settings HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {cookie}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode("ascii")
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            client.sendall(raw + pieces[0])
            for piece in pieces[1:]:
                time.sleep(0.2)
                client.sendall(piece)
            first_status, first_headers = _read_one_response(client)
            assert str(HTTPStatus.REQUEST_TIMEOUT.value) not in first_status, first_status
            assert str(HTTPStatus.BAD_REQUEST.value) not in first_status, first_status
            assert first_headers.get("connection") != "close", first_headers
            client.sendall(
                (
                    f"GET /api/ping HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    f"Cookie: {cookie}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            second_status, _second_headers = _read_one_response(client)
        finally:
            client.close()
        assert str(HTTPStatus.OK.value) in second_status, second_status
    finally:
        _stop_server(server, thread)


def test_unread_large_body_on_a_deleted_route_does_not_frame_a_bogus_414(monkeypatch, tmp_path):
    """A 404 for an authenticated POST must not leave a 98 KB body to be parsed as a request line."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        body = json.dumps({"samples": [{"cpu": index} for index in range(9_000)]}).encode("utf-8")
        body = body + b" " * max(0, _LARGE_BODY_BYTES - len(body))
        assert len(body) >= _LARGE_BODY_BYTES
        assert http_routes.route_for_request("POST", "/api/stats-history") is None
        status_line, headers, trailing = _post_with_unread_body(port, cookie, "/api/stats-history", body)
        assert str(HTTPStatus.NOT_FOUND.value) in status_line, status_line
        assert trailing == b"", f"server framed the leftover body as a request: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_unread_large_body_on_the_internal_error_path_does_not_frame_a_bogus_414(monkeypatch, tmp_path):
    """The 500 written by dispatch_route_response also answers before any body read."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("route failed before reading the request body")

    app = SimpleNamespace(sessions=[], dangerously_yolo=False, set_notify=explode)
    server, thread = _start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        body = b"x" * _LARGE_BODY_BYTES
        status_line, headers, trailing = _post_with_unread_body(port, cookie, "/api/notify?enabled=1", body)
        assert str(HTTPStatus.INTERNAL_SERVER_ERROR.value) in status_line, status_line
        assert trailing == b"", f"server framed the leftover body as a request: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_unread_small_body_does_not_hang_the_connection(monkeypatch, tmp_path):
    """A leftover body below the 414 threshold and without a newline blocks the parser forever."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        body = b"y" * _SMALL_BODY_BYTES
        assert b"\n" not in body
        status_line, headers, trailing = _post_with_unread_body(port, cookie, "/api/stats-history", body)
        assert str(HTTPStatus.NOT_FOUND.value) in status_line, status_line
        assert trailing == b"", f"server framed the leftover body as a request: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_a_post_that_reads_its_body_keeps_the_connection_alive(monkeypatch, tmp_path):
    """The fix must close only unread-body responses; a consumed body still gets keep-alive."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps({"settings": {}}).encode("utf-8")
            connection.request(
                "POST",
                "/api/settings",
                body=body,
                headers={"Content-Type": "application/json", "Cookie": cookie},
            )
            first = connection.getresponse()
            first.read()
            assert first.status in {HTTPStatus.OK, HTTPStatus.INTERNAL_SERVER_ERROR}, first.status
            assert first.getheader("Connection") != "close", first.getheaders()
        finally:
            connection.close()
    finally:
        _stop_server(server, thread)


def test_a_slow_client_still_receives_the_response_before_the_unread_body_close(monkeypatch, tmp_path):
    """Closing on an unread body must send a clean FIN, never a reset that eats the response."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        body = b"z" * _LARGE_BODY_BYTES
        status_line, headers, trailing = _post_with_unread_body(
            port, cookie, "/api/stats-history", body, read_delay=0.5
        )
        assert str(HTTPStatus.NOT_FOUND.value) in status_line, status_line
        assert trailing == b"", f"connection was not closed cleanly: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_unread_body_on_a_successful_query_only_post_closes_the_connection(monkeypatch, tmp_path):
    """A 200 desyncs exactly like an error: post_notify answers from the query string alone."""
    app = SimpleNamespace(
        sessions=[],
        dangerously_yolo=False,
        set_notify=lambda enabled: {"notify": bool(enabled)},
    )
    server, thread = _start_server(monkeypatch, tmp_path, app=app)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        status_line, headers, trailing = _post_with_unread_body(
            port, cookie, "/api/notify?enabled=1", b"w" * _LARGE_BODY_BYTES
        )
        assert str(HTTPStatus.OK.value) in status_line, status_line
        assert trailing == b"", f"server framed the leftover body as a request: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_invalid_content_length_rejection_closes_the_connection(monkeypatch, tmp_path):
    """read_request_body rejects an unparsable Content-Length without taking the bytes off the socket."""
    server, thread = _start_server(monkeypatch, tmp_path)
    port = server.server_address[1]
    try:
        cookie = _admin_cookie(port)
        status_line, headers, trailing = _post_with_unread_body(
            port, cookie, "/api/settings", b"v" * _LARGE_BODY_BYTES, content_length="not-a-number"
        )
        assert " 4" in status_line, status_line
        assert trailing == b"", f"server framed the leftover body as a request: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)


def test_auth_setup_page_for_an_unread_post_body_closes_the_connection(monkeypatch, tmp_path):
    """require_auth's auth-setup branch answers 200 HTML before any handler reads the body."""
    server, thread = _start_server(monkeypatch, tmp_path, auth_yaml="users: []\n")
    port = server.server_address[1]
    try:
        status_line, headers, trailing = _post_with_unread_body(
            port, "", "/api/settings", b"u" * _LARGE_BODY_BYTES
        )
        assert str(HTTPStatus.OK.value) in status_line, status_line
        assert "text/html" in headers.get("content-type", ""), headers
        assert trailing == b"", f"server framed the leftover body as a request: {trailing[:200]!r}"
        assert headers.get("connection") == "close", headers
    finally:
        _stop_server(server, thread)

import json
import os
import time

from yolomux_lib import control
from yolomux_lib.local_services import rpc


class FakeConnection:
    def __init__(self, incoming: bytes):
        self.incoming = [incoming]
        self.sent = b""

    def recv(self, _size):
        return self.incoming.pop(0) if self.incoming else b""

    def sendall(self, data):
        self.sent += data


class FakeClientSocket(FakeConnection):
    def __init__(self, incoming: bytes):
        super().__init__(incoming)
        self.timeout = None
        self.connected_to = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        self.connected_to = path

    def sendall(self, data):
        self.sent += data


class BrokenPipeConnection(FakeConnection):
    def sendall(self, data):
        raise BrokenPipeError("client disconnected")


class ResponseConnection:
    def __init__(self, incoming):
        self.incoming = [incoming]

    def recv(self, _size):
        return self.incoming.pop(0) if self.incoming else b""


def response_from_fake_connection(conn):
    _envelope, payload, _binary, _legacy = rpc.read_message(ResponseConnection(conn.sent))
    return payload


def test_control_server_does_not_leak_unexpected_handler_errors(caplog):
    server = control.YolomuxControlServer(lambda _request: (_ for _ in ()).throw(RuntimeError("secret token")))
    conn = FakeConnection(b'{"action":"boom"}\n')

    server.serve_connection(conn)

    assert response_from_fake_connection(conn) == {"ok": False, "error": "internal control handler error"}
    assert "secret token" in caplog.text


def test_control_server_returns_expected_control_request_error():
    server = control.YolomuxControlServer(lambda _request: (_ for _ in ()).throw(control.ControlRequestError("unknown action")))
    conn = FakeConnection(b'{"action":"bad"}\n')

    server.serve_connection(conn)

    assert response_from_fake_connection(conn) == {"ok": False, "error": "unknown action"}


def test_control_server_ignores_broken_pipe_during_response():
    server = control.YolomuxControlServer(lambda _request: {"ok": True})
    conn = BrokenPipeConnection(b'{"action":"ping"}\n')

    server.serve_connection(conn)


def test_control_socket_path_falls_back_for_long_unix_paths(monkeypatch, tmp_path):
    long_dir = tmp_path
    for index in range(8):
        long_dir = long_dir / f"very-long-control-dir-{index}"
    monkeypatch.setattr(control, "CONTROL_SOCKET_DIR", long_dir)

    path = control.control_socket_path(token="abcdef", pid=12345)

    assert path.name == "yolomux-12345-abcdef.sock"
    assert str(path).startswith("/tmp/")
    assert len(os.fsencode(str(path))) < control.CONTROL_SOCKET_PATH_LIMIT


def test_send_yolomux_control_request_round_trips(monkeypatch):
    response = {"ok": True, "echo": {"action": "ping"}}
    fake_socket = FakeClientSocket(b"")
    fake_socket.incoming.clear()
    original_write_message = rpc.write_message

    def fake_sendall(data):
        fake_socket.sent += data
        request_envelope, _payload, _binary, _legacy = rpc.read_message(ResponseConnection(data))
        response_envelope = rpc.LocalRpcEnvelope(
            service="control",
            method="ping",
            request_id=request_envelope.request_id,
            trace_id=request_envelope.trace_id,
            deadline_ms=request_envelope.deadline_ms,
            priority=request_envelope.priority,
            owner_generation=0,
            config_generation=0,
            payload=response,
        )
        response_connection = FakeConnection(b"")
        original_write_message(response_connection, response_envelope, response)
        fake_socket.incoming.append(response_connection.sent)

    fake_socket.sendall = fake_sendall
    monkeypatch.setattr(control.socket, "socket", lambda *_args: fake_socket)

    response = control.send_yolomux_control_request({"control_socket": "/tmp/yolomux.sock"}, {"action": "ping"})

    assert response == {"ok": True, "echo": {"action": "ping"}}
    assert fake_socket.connected_to == "/tmp/yolomux.sock"
    request_envelope, request_payload, _binary, legacy = rpc.read_message(ResponseConnection(fake_socket.sent))
    assert legacy is False
    assert request_envelope is not None
    assert request_payload == {"action": "ping"}


def test_control_server_current_rpc_socket_round_trip():
    server = control.YolomuxControlServer(lambda request: {"ok": True, "echo": request})
    server.start()
    try:
        deadline = time.monotonic() + 1.0
        response = {"ok": False}
        while time.monotonic() < deadline:
            response = control.send_yolomux_control_request(server.owner_payload(), {"action": "ping"}, timeout=0.1)
            if response.get("ok"):
                break
            time.sleep(0.01)
        assert response == {"ok": True, "echo": {"action": "ping"}}
        assert os.stat(server.path).st_mode & 0o777 == 0o600
    finally:
        server.stop()
    assert server.path.exists() is False

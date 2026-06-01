import json

from yolomux_lib import control


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


def response_from_fake_connection(conn):
    return json.loads(conn.sent.decode("utf-8").splitlines()[0])


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


def test_send_yolomux_control_request_round_trips(monkeypatch):
    fake_socket = FakeClientSocket(b'{"ok": true, "echo": {"action": "ping"}}\n')
    monkeypatch.setattr(control.socket, "socket", lambda *_args: fake_socket)

    response = control.send_yolomux_control_request({"control_socket": "/tmp/yolomux.sock"}, {"action": "ping"})

    assert response == {"ok": True, "echo": {"action": "ping"}}
    assert fake_socket.connected_to == "/tmp/yolomux.sock"
    assert json.loads(fake_socket.sent.decode("utf-8")) == {"action": "ping"}

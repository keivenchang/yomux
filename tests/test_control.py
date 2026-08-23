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


# ---------------------------------------------------------------------------
# Predecessor control-socket cleanup.
#
# `YolomuxControlServer.__init__` (control.py:52) names its socket
# `yolomux-<pid>-<id(self):x>.sock`. `id(self)` is a CPython object address: it
# is reused as soon as a previous server object is collected, and it carries no
# information about which generation owns the file. Nothing anywhere enumerates
# `control/yolomux-*.sock` -- the only unlink is `self.path`, in `start()` and
# `stop()` -- so any server that did not run `stop()` leaks its socket forever,
# and a successor that happens to reuse the address silently deletes a file it
# never proved it owned.
#
# CONTRACT:
#   1. The token must come from a durable, non-reusable identity (the process
#      start identity / instance nonce / generation id), not a memory address.
#   2. `start()` must remove predecessor control sockets belonging to THIS exact
#      process identity that no live server holds -- and nothing else. Never a
#      broad sweep of other pids' sockets or of unrelated files.
# ---------------------------------------------------------------------------


def test_control_socket_name_is_not_a_reusable_memory_address(monkeypatch, tmp_path):
    """The socket name must not encode `id(self)`.

    A memory address is reused the moment the previous object is collected, so
    two different generations can name the same path -- which is why the blind
    `unlink()` in `start()` can destroy a live predecessor's socket.
    """
    monkeypatch.setattr(control, "CONTROL_SOCKET_DIR", tmp_path)

    server = control.YolomuxControlServer(lambda request: {"ok": True, "echo": request})

    # Positive control: the name really is derived from this process at all.
    assert str(os.getpid()) in server.path.name, "the control socket no longer names its owning process"
    assert f"{id(server):x}" not in server.path.name, (
        "the control socket token is this object's memory address; a later server object handed the "
        "same address names the same socket path and unlinks its predecessor's file blind"
    )


def test_control_server_start_reclaims_only_its_own_stale_predecessor_socket(monkeypatch, tmp_path):
    """Bounded, same-identity-only predecessor cleanup -- never a broad sweep.

    Three seeded files differ in exactly one dimension each. The own-identity
    stale socket must go; the other pid's socket and the unrelated file must
    stay. The two survivors are the POSITIVE CONTROL that this is a targeted
    reclaim and not a directory wipe, and the survivors set is non-empty, so no
    assertion here compares two empty collections.
    """
    monkeypatch.setattr(control, "CONTROL_SOCKET_DIR", tmp_path)
    own_stale = tmp_path / f"yolomux-{os.getpid()}-stalegeneration.sock"
    other_process = tmp_path / f"yolomux-{os.getpid() + 1}-othergeneration.sock"
    unrelated = tmp_path / "not-a-control-socket.txt"
    for path in (own_stale, other_process):
        path.write_bytes(b"leftover-socket-artifact")
    unrelated.write_bytes(b"unrelated")

    server = control.YolomuxControlServer(lambda request: {"ok": True, "echo": request})
    server.start()
    try:
        survivors = {path.name for path in tmp_path.iterdir()} - {server.path.name}
    finally:
        server.stop()

    assert other_process.name in survivors, "another process's control socket was swept"
    assert unrelated.name in survivors, "an unrelated file in the control directory was removed"
    assert own_stale.name not in survivors, (
        "this process's own stale predecessor control socket was left behind; nothing enumerates "
        "the control directory, so it leaks for the lifetime of the machine"
    )

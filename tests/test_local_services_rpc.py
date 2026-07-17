import json
import socket
import threading
import fcntl
import os
import time

import pytest

from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime


class FragmentedConnection:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""

    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks[0]
        result, remainder = chunk[:size], chunk[size:]
        if remainder:
            self.chunks[0] = remainder
        else:
            self.chunks.pop(0)
        return result

    def sendall(self, data):
        self.sent += data


class CountingConnection(FragmentedConnection):
    def __init__(self, chunks):
        super().__init__(chunks)
        self.recv_sizes = []

    def recv(self, size):
        self.recv_sizes.append(size)
        return super().recv(size)


def _current_frame(payload, binary=b""):
    envelope = rpc.new_envelope("testd", "echo", payload)
    connection = FragmentedConnection([])
    rpc.write_message(connection, envelope, payload, binary)
    return envelope, connection.sent


def test_current_rpc_round_trip_handles_fragmented_header_metadata_and_binary():
    envelope, frame = _current_frame({"answer": 42}, b"bytes")
    connection = FragmentedConnection([frame[:1], frame[1:3], frame[3:8], frame[8:17], frame[17:]])

    received, payload, binary, legacy = rpc.read_message(connection)

    assert legacy is False
    assert received == envelope
    assert payload == {"answer": 42}
    assert binary == b"bytes"


def test_current_rpc_rejects_bad_version_oversize_and_malformed_utf8():
    envelope, frame = _current_frame({"answer": 42})
    assert envelope.version == rpc.LOCAL_RPC_VERSION
    bad_version = frame.replace(b'"version":1', b'"version":999', 1)
    for candidate in (bad_version, (rpc.LOCAL_RPC_MAX_METADATA_BYTES + 1).to_bytes(4, "big"), b"\x00\x00\x00\x02\xff\xff"):
        with pytest.raises(rpc.LocalRpcError):
            rpc.read_message(FragmentedConnection([candidate]))


def test_current_rpc_rejects_oversize_header_before_reading_metadata():
    connection = CountingConnection([(rpc.LOCAL_RPC_MAX_METADATA_BYTES + 1).to_bytes(4, "big"), b"x" * 64])

    with pytest.raises(rpc.LocalRpcError):
        rpc.read_message(connection)

    assert connection.recv_sizes == [rpc.LOCAL_RPC_HEADER_BYTES]


def test_current_rpc_caps_response_binary_before_sending():
    envelope = rpc.new_envelope("testd", "too-large", {})
    connection = FragmentedConnection([])

    with pytest.raises(rpc.LocalRpcError):
        rpc.write_message(connection, envelope, {}, b"x" * (rpc.LOCAL_RPC_MAX_BINARY_BYTES + 1))

    assert connection.sent == b""


def test_current_rpc_accepts_legacy_newline_requests_for_a_rolling_restart():
    received, payload, binary, legacy = rpc.read_message(FragmentedConnection([b'{"action":"ping"}\n']))

    assert received is None
    assert payload == {"action": "ping"}
    assert binary == b""
    assert legacy is True


def test_current_rpc_handles_multiple_frames_on_one_socket():
    first_envelope, first = _current_frame({"sequence": 1})
    second_envelope, second = _current_frame({"sequence": 2})
    connection = FragmentedConnection([first + second])

    first_received, first_payload, _binary, first_legacy = rpc.read_message(connection)
    second_received, second_payload, _binary, second_legacy = rpc.read_message(connection)

    assert (first_received, first_payload, first_legacy) == (first_envelope, {"sequence": 1}, False)
    assert (second_received, second_payload, second_legacy) == (second_envelope, {"sequence": 2}, False)


def test_current_rpc_socketpair_round_trip_preserves_request_id_and_deadline():
    client, server = socket.socketpair()
    request_envelope = rpc.new_envelope("testd", "status", {"one": True}, timeout_seconds=0.3)

    def serve():
        incoming, payload, binary, legacy = rpc.read_message(server)
        assert legacy is False
        assert binary == b""
        response_envelope = rpc.LocalRpcEnvelope(
            service="testd",
            method="status",
            request_id=incoming.request_id,
            trace_id=incoming.trace_id,
            deadline_ms=incoming.deadline_ms,
            priority=incoming.priority,
            owner_generation=incoming.owner_generation,
            config_generation=incoming.config_generation,
            payload={"ok": True, "echo": payload},
        )
        rpc.write_message(server, response_envelope, response_envelope.payload)
        server.close()

    worker = threading.Thread(target=serve)
    worker.start()
    rpc.write_message(client, request_envelope, request_envelope.payload)
    response_envelope, response, _binary, legacy = rpc.read_message(client)
    client.close()
    worker.join(timeout=1.0)

    assert worker.is_alive() is False
    assert legacy is False
    assert response_envelope.request_id == request_envelope.request_id
    assert response == {"ok": True, "echo": {"one": True}}


def test_current_rpc_timeout_never_replays_work_through_legacy_fallback(tmp_path, monkeypatch):
    legacy_calls = []

    class TimedOutSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _seconds):
            pass

        def connect(self, _path):
            raise TimeoutError("busy peer")

    monkeypatch.setattr(rpc.socket, "socket", lambda *_args, **_kwargs: TimedOutSocket())
    monkeypatch.setattr(rpc, "legacy_request", lambda *_args, **_kwargs: legacy_calls.append(True) or {})
    envelope = rpc.new_envelope("testd", "history", {"action": "history"})

    with pytest.raises(TimeoutError, match="busy peer"):
        rpc.request(tmp_path / "testd.sock", envelope, timeout_seconds=0.1, fallback_legacy=True)

    assert legacy_calls == []


def test_local_service_runtime_peer_uid_is_safe_when_unsupported_or_matching(monkeypatch):
    client, server = socket.socketpair()
    try:
        monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
        assert runtime.peer_uid(server) == os.getuid()
        monkeypatch.setattr(runtime, "peer_uid", lambda _connection: None)
        assert runtime.peer_uid(server) is None
    finally:
        client.close()
        server.close()


def _run_echo_service(socket_path, lock_path, stop_event, *, monkeypatch=None, peer_uid=None):
    if monkeypatch is not None:
        monkeypatch.setattr(runtime, "peer_uid", lambda _connection: peer_uid)

    def handle(request):
        if request.get("action") == "shutdown":
            stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        if request.get("action") == "oversize_response":
            return {"ok": True, "blob": "x" * (rpc.LOCAL_RPC_MAX_METADATA_BYTES + 1)}, b""
        return {"ok": True, "echo": request}, b""

    worker = threading.Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path,
            lock_path=lock_path,
            service_name="testd",
            stop_event=stop_event,
            handle=handle,
            on_idle=lambda: False,
            on_client=lambda: None,
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if socket_path.exists() and (socket_path.stat().st_mode & 0o777) == 0o600:
            break
        time.sleep(0.01)
    else:
        mode = oct(socket_path.stat().st_mode & 0o777) if socket_path.exists() else "missing"
        pytest.fail(f"local service socket did not become ready with mode 0600: {mode}")
    return worker


def test_local_service_runtime_uses_mode_0600_unix_socket_and_survives_slow_clients(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())

    assert oct(socket_path.stat().st_mode & 0o777) == "0o600"
    assert oct(lock_path.stat().st_mode & 0o777) == "0o600"

    slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    slow.connect(str(service_socket_path))
    envelope = rpc.new_envelope("testd", "echo", {"action": "echo"}, timeout_seconds=2.0)
    response, _binary = rpc.request(service_socket_path, envelope, timeout_seconds=2.0)
    slow.close()

    assert response == {"ok": True, "echo": {"action": "echo"}}
    shutdown = rpc.new_envelope("testd", "shutdown", {"action": "shutdown"})
    assert rpc.request(service_socket_path, shutdown, timeout_seconds=1.0)[0] == {"ok": True, "shutdown": True}
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_rejects_wrong_peer_uid_where_supported(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid() + 1)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(str(service_socket_path))
        response = json.loads(client.makefile("rb").readline())
    stop_event.set()
    worker.join(timeout=1.0)

    assert response == {"ok": False, "error": "peer uid mismatch"}
    assert worker.is_alive() is False


def test_local_service_runtime_caps_oversize_responses_without_exiting(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())

    oversize = rpc.new_envelope("testd", "oversize_response", {"action": "oversize_response"})
    echo = rpc.new_envelope("testd", "echo", {"action": "echo"})

    assert rpc.request(service_socket_path, oversize, timeout_seconds=1.0)[0] == {"ok": False, "error": "response too large"}
    assert rpc.request(service_socket_path, echo, timeout_seconds=1.0)[0] == {"ok": True, "echo": {"action": "echo"}}
    stop_event.set()
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_never_opens_a_network_listener(tmp_path, monkeypatch):
    families = []
    original_socket = runtime.socket.socket

    def tracked_socket(family, kind, *args, **kwargs):
        families.append(family)
        return original_socket(family, kind, *args, **kwargs)

    monkeypatch.setattr(runtime.socket, "socket", tracked_socket)
    stop_event = threading.Event()
    stop_event.set()

    runtime.run_local_rpc_service(
        socket_path=tmp_path / "service.sock",
        lock_path=tmp_path / "service.lock",
        service_name="testd",
        stop_event=stop_event,
        handle=lambda _request: ({"ok": True}, b""),
        on_idle=lambda: False,
        on_client=lambda: None,
    )

    assert families == [socket.AF_UNIX]


def test_local_service_runtime_loser_does_not_run_stateful_startup(tmp_path):
    lock_path = tmp_path / "service.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    starts = []
    try:
        result = runtime.run_local_rpc_service(
            socket_path=tmp_path / "service.sock",
            lock_path=lock_path,
            service_name="testd",
            stop_event=threading.Event(),
            handle=lambda _request: ({"ok": True}, b""),
            on_idle=lambda: False,
            on_client=lambda: None,
            on_start=lambda: starts.append("opened-database"),
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert result == 0
    assert starts == []


def test_local_service_transport_has_no_pickle_or_decompression_surface():
    sources = [
        "yolomux_lib/local_services/rpc.py",
        "yolomux_lib/local_services/runtime.py",
        "yolomux_lib/local_services/registry.py",
        "yolomux_lib/jobd.py",
        "yolomux_lib/stats_current/service.py",
        "yolomux_lib/approvald.py",
    ]
    combined = "\n".join(open(path, encoding="utf-8").read() for path in sources)

    assert "import pickle" not in combined
    assert "pickle.loads" not in combined
    assert "import gzip" not in combined
    assert "import zlib" not in combined

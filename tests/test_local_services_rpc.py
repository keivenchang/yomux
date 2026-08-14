import errno
import json
import select
import socket
import threading
import fcntl
import os
import time

import pytest

from yolomux_lib.local_services import registry as registry_module
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services import runtime
from yolomux_lib.local_services.client import LocalServiceClient


@pytest.fixture(autouse=True)
def _isolated_local_service_traffic():
    """Every traffic assertion measures only its own requests."""
    rpc.reset_local_service_traffic()
    yield
    rpc.reset_local_service_traffic()


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


@pytest.mark.parametrize(
    ("service_duration_ms", "expected_attribution"),
    [
        (15.0, "peer_handler_slow"),
        (3.0, "unattributed_latency"),
    ],
)
def test_current_rpc_deadline_returns_the_delivered_response_with_a_budget_breach_label(
    tmp_path, monkeypatch, service_duration_ms, expected_attribution,
):
    """A complete, request-id-matched response past the telemetry budget is DELIVERED, not raised.

    The deadline is a telemetry budget, not a correctness bound: the response is returned and the
    measured attribution (a slow handler versus latency before the handler ran) is recorded as a
    diagnostic on the delivered record, never as an error.
    """
    envelope = rpc.new_envelope("testd", "status", {"action": "status"}, timeout_seconds=0.01)
    response_envelope = rpc.LocalRpcEnvelope(
        service="testd",
        method="status",
        request_id=envelope.request_id,
        trace_id=envelope.trace_id,
        deadline_ms=envelope.deadline_ms,
        priority=envelope.priority,
        owner_generation=envelope.owner_generation,
        config_generation=envelope.config_generation,
        payload={"ok": True},
        service_duration_ms=service_duration_ms,
    )
    response = FragmentedConnection([])
    rpc.write_message(response, response_envelope, response_envelope.payload)

    class DelayedResponseSocket(FragmentedConnection):
        def __init__(self):
            super().__init__([response.sent])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _seconds):
            pass

        def connect(self, _path):
            pass

    clock = iter((10.0, 10.012))
    monkeypatch.setattr(rpc.socket, "socket", lambda *_args, **_kwargs: DelayedResponseSocket())
    monkeypatch.setattr(rpc, "monotonic_clock", lambda: next(clock))

    delivered_envelope, payload, binary = rpc.request_with_envelope(
        tmp_path / "testd.sock", envelope, timeout_seconds=0.1
    )

    # The past-budget response is returned intact, not raised.
    assert delivered_envelope is not None
    assert delivered_envelope.request_id == envelope.request_id
    assert payload == {"ok": True}
    assert binary == b""

    # The budget breach is telemetry on the delivered record, not a failure. `status` is a probe
    # method, so the completion lands in the probe class.
    probe = rpc.local_service_traffic_ledger("testd").snapshot()["probe"]
    assert (probe["completed"], probe["errors"]) == (1, 0)
    assert probe["errors_by_reason"] == {}
    assert probe["over_budget"] == 1
    assert probe["over_budget_by_reason"] == {expected_attribution: 1}


def test_current_rpc_absent_socket_never_replays_work_through_legacy_fallback(tmp_path, monkeypatch):
    legacy_calls = []

    class MissingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _seconds):
            pass

        def connect(self, _path):
            raise FileNotFoundError(errno.ENOENT, "fixture socket absent")

    monkeypatch.setattr(rpc.socket, "socket", lambda *_args, **_kwargs: MissingSocket())
    monkeypatch.setattr(rpc, "legacy_request", lambda *_args, **_kwargs: legacy_calls.append(True) or {})
    envelope = rpc.new_envelope("testd", "history", {"action": "history"})

    with pytest.raises(FileNotFoundError, match="fixture socket absent"):
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


def _wait_for_service_socket(socket_path, expected_mode=0o600):
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if socket_path.exists() and (socket_path.stat().st_mode & 0o777) == expected_mode:
            return
        time.sleep(0.01)
    mode = oct(socket_path.stat().st_mode & 0o777) if socket_path.exists() else "missing"
    pytest.fail(f"local service socket did not become ready with mode {oct(expected_mode)}: {mode}")


def _connect_to_service(service_socket_path, *, timeout=1.0, deadline_seconds=2.0):
    """Connect to a starting local service, which publishes its socket file before it listens.

    ``run_local_rpc_service`` creates the path with ``bind()`` -- already mode 0600 under its umask
    -- and calls ``listen()`` afterwards, so the file's existence and mode are true for a service
    that cannot accept yet. A connect issued in that window fails with ECONNREFUSED against a
    service starting normally, which is what a loaded gate saw. The product never treats the file
    as readiness either: the registry waits for a real ping and retries. This is the test-side
    equivalent, and it retries the connection the caller actually wants rather than probing with an
    extra one that would consume a handler slot and change what the capacity tests observe.
    """

    deadline = time.monotonic() + deadline_seconds
    while True:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(str(service_socket_path))
        except ConnectionRefusedError:
            client.close()
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
            continue
        return client


def _run_echo_service(socket_path, lock_path, stop_event, *, monkeypatch=None, peer_uid=None):
    if monkeypatch is not None:
        monkeypatch.setattr(runtime, "peer_uid", lambda _connection: peer_uid)

    def handle(request, request_binary):
        if request.get("action") == "shutdown":
            stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        if request.get("action") == "raise":
            raise FileNotFoundError("retired service root")
        if request.get("action") == "oversize_response":
            return {"ok": True, "blob": "x" * (rpc.LOCAL_RPC_MAX_METADATA_BYTES + 1)}, b""
        return {"ok": True, "echo": request, "request_binary": request_binary.decode("utf-8")}, b""

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
    _wait_for_service_socket(socket_path)
    return worker


def test_local_service_runtime_uses_mode_0600_unix_socket_and_survives_slow_clients(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())

    assert oct(socket_path.stat().st_mode & 0o777) == "0o600"
    assert oct(lock_path.stat().st_mode & 0o777) == "0o600"

    slow = _connect_to_service(service_socket_path)
    envelope = rpc.new_envelope("testd", "echo", {"action": "echo"}, timeout_seconds=2.0)
    response, _binary = rpc.request(service_socket_path, envelope, timeout_seconds=2.0)
    slow.close()

    assert response == {"ok": True, "echo": {"action": "echo"}, "request_binary": ""}
    shutdown = rpc.new_envelope("testd", "shutdown", {"action": "shutdown"})
    assert rpc.request(service_socket_path, shutdown, timeout_seconds=1.0)[0] == {"ok": True, "shutdown": True}
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_forwards_bounded_request_binary_to_handler(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())
    envelope = rpc.new_envelope("testd", "echo", {"action": "echo"})

    response, response_binary = rpc.request(
        service_socket_path,
        envelope,
        binary=b"bounded request",
        timeout_seconds=1.0,
    )

    assert response == {
        "ok": True,
        "echo": {"action": "echo"},
        "request_binary": "bounded request",
    }
    assert response_binary == b""
    shutdown = rpc.new_envelope("testd", "shutdown", {"action": "shutdown"})
    assert rpc.request(service_socket_path, shutdown, timeout_seconds=1.0)[0] == {"ok": True, "shutdown": True}
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_carries_accept_read_and_handler_phases(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())
    envelope = rpc.new_envelope("testd", "echo", {"action": "echo"})

    with _connect_to_service(service_socket_path) as client:
        rpc.write_message(client, envelope, envelope.payload)
        response_envelope, response, _binary, legacy = rpc.read_message(client)

    assert legacy is False
    assert response == {"ok": True, "echo": {"action": "echo"}, "request_binary": ""}
    assert response_envelope is not None
    assert response_envelope.accept_to_read_ms >= 0.0
    assert response_envelope.read_complete_ms >= 0.0
    assert response_envelope.service_duration_ms >= 0.0
    observed_envelope, observed_response, observed_binary = rpc.request_with_envelope(
        service_socket_path,
        rpc.new_envelope("testd", "echo", {"action": "echo"}),
        timeout_seconds=1.0,
    )
    assert observed_response["ok"] is True
    assert observed_response["echo"] == {"action": "echo"}
    assert observed_binary == b""
    assert observed_envelope is not None
    assert observed_envelope.queue_wait_ms >= 0.0
    assert observed_envelope.service_duration_ms >= 0.0
    shutdown = rpc.new_envelope("testd", "shutdown", {"action": "shutdown"})
    assert rpc.request(service_socket_path, shutdown, timeout_seconds=1.0)[0] == {"ok": True, "shutdown": True}
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_reports_bounded_capacity_rejection(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    started = threading.Event()
    release = threading.Event()

    def handle(request, _request_binary):
        if request.get("action") == "hold":
            started.set()
            release.wait(timeout=1.0)
        if request.get("action") == "shutdown":
            stop_event.set()
        return {"ok": True}, b""

    worker = threading.Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path, lock_path=lock_path, service_name="testd", stop_event=stop_event,
            handle=handle, on_idle=lambda: False, on_client=lambda: None, concurrent_handlers=1,
        ), daemon=True,
    )
    worker.start()
    _wait_for_service_socket(socket_path)
    first = _connect_to_service(service_socket_path)
    hold = rpc.new_envelope("testd", "hold", {"action": "hold"})
    rpc.write_message(first, hold, hold.payload)
    assert started.wait(timeout=1.0)
    with _connect_to_service(service_socket_path) as second:
        busy = json.loads(second.makefile("rb").readline())
    release.set()
    response_envelope, response, _binary, legacy = rpc.read_message(first)
    first.close()
    assert legacy is False
    assert response == {"ok": True}
    assert response_envelope is not None
    assert response_envelope.capacity_limit == 1
    assert response_envelope.capacity_saturated is True
    assert response_envelope.capacity_rejections == 1
    assert busy == {"ok": False, "error": "service busy", "queue_wait_ms": pytest.approx(busy["queue_wait_ms"]), "queue_depth": 0, "capacity_limit": 1, "capacity_saturated": True, "capacity_rejected": True, "capacity_rejections": 1}
    # Response bytes are written before the handler thread releases its bounded slot, so an
    # immediate shutdown RPC may correctly observe the same saturated capacity. Stop the fixture
    # through its owned event after all rejection and response-envelope assertions are complete.
    stop_event.set()
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_does_not_idle_shutdown_with_active_handler(tmp_path):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    started = threading.Event()
    release = threading.Event()
    idle_checked = threading.Event()

    def handle(_request, _request_binary):
        started.set()
        release.wait(timeout=2.0)
        return {"ok": True}, b""

    def idle_due():
        idle_checked.set()
        return True

    worker = threading.Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path, lock_path=lock_path, service_name="testd", stop_event=stop_event,
            handle=handle, on_idle=idle_due, on_client=lambda: None, concurrent_handlers=1,
        ), daemon=True,
    )
    worker.start()
    _wait_for_service_socket(socket_path)
    client = _connect_to_service(service_socket_path)
    hold = rpc.new_envelope("testd", "hold", {"action": "hold"})
    rpc.write_message(client, hold, hold.payload)
    try:
        assert started.wait(timeout=1.0)
        assert stop_event.wait(timeout=0.5) is False
        assert idle_checked.is_set() is False
        assert worker.is_alive() is True
    finally:
        release.set()
        stop_event.set()
        client.close()
        worker.join(timeout=1.0)


def test_local_service_runtime_rejects_wrong_peer_uid_where_supported(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid() + 1)
    with _connect_to_service(service_socket_path) as client:
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
    assert rpc.request(service_socket_path, echo, timeout_seconds=1.0)[0] == {"ok": True, "echo": {"action": "echo"}, "request_binary": ""}
    stop_event.set()
    worker.join(timeout=1.0)
    assert worker.is_alive() is False


def test_local_service_runtime_returns_typed_handler_failure_and_keeps_serving(tmp_path, monkeypatch):
    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())

    failed = rpc.new_envelope("testd", "raise", {"action": "raise"})
    echo = rpc.new_envelope("testd", "echo", {"action": "echo"})

    assert rpc.request(service_socket_path, failed, timeout_seconds=1.0)[0] == {
        "ok": False,
        "error": "service request failed",
        "error_code": "handler_failed",
        "exception_type": "FileNotFoundError",
    }
    assert rpc.request(service_socket_path, echo, timeout_seconds=1.0)[0] == {"ok": True, "echo": {"action": "echo"}, "request_binary": ""}
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
        handle=lambda _request, _request_binary: ({"ok": True}, b""),
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
            handle=lambda _request, _request_binary: ({"ok": True}, b""),
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
        "yolomux_lib/infra/jobd.py",
        "yolomux_lib/stats_current/service.py",
        "yolomux_lib/approvald.py",
    ]
    combined = "\n".join(open(path, encoding="utf-8").read() for path in sources)

    assert "import pickle" not in combined
    assert "pickle.loads" not in combined
    assert "import gzip" not in combined
    assert "import zlib" not in combined


# --- M6: retained per-service request/error/latency aggregate -----------------------------


def _traffic(service):
    return rpc.local_service_traffic_ledger(service).snapshot()


def _run_traffic_service(
    socket_path,
    lock_path,
    stop_event,
    *,
    service_pid=4242,
    concurrent_handlers=8,
    slow_seconds=0.0,
    on_slow=None,
):
    def handle(request, _request_binary):
        action = request.get("action")
        if action == "shutdown":
            stop_event.set()
            return {"ok": True}, b""
        if action in ("ping", "status"):
            return {"ok": True, "version": rpc.LOCAL_RPC_VERSION, "pid": service_pid}, b""
        if action == "slow":
            if on_slow is not None:
                # Lets a caller hold the handler slot for exactly as long as it needs,
                # instead of guessing a sleep long enough to stay saturated.
                on_slow()
            time.sleep(slow_seconds)
            return {"ok": True}, b""
        if action == "refuse":
            return {"ok": False, "error": "fixture refused", "error_code": "fixture_refused"}, b""
        return {"ok": True, "echo": action}, b""

    worker = threading.Thread(
        target=lambda: runtime.run_local_rpc_service(
            socket_path=socket_path,
            lock_path=lock_path,
            service_name="trafficd",
            stop_event=stop_event,
            handle=handle,
            on_idle=lambda: False,
            on_client=lambda: None,
            concurrent_handlers=concurrent_handlers,
        ),
        daemon=True,
    )
    worker.start()
    _wait_for_service_socket(socket_path)
    return worker


def _stop_traffic_service(worker, stop_event, socket_path):
    stop_event.set()
    worker.join(timeout=2.0)
    assert worker.is_alive() is False
    deadline = time.monotonic() + 2.0
    while socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert socket_path.exists() is False


def _fan_out(count, work, workers=16):
    """Run ``work(index)`` ``count`` times across concurrent threads and return every result.

    A failure inside a worker is re-raised here.  Leaving it in the worker thread turned a
    real product failure into a ``None`` result and an ``AttributeError`` on the next line,
    which is how the gate reported ``BrokenPipeError`` as ``'NoneType' has no attribute 'get'``.
    """
    results = [None] * count
    failures = []
    barrier = threading.Barrier(workers)
    indexes = list(range(count))
    cursor = threading.Lock()
    position = [0]

    def run():
        barrier.wait(timeout=10.0)
        while True:
            with cursor:
                if position[0] >= count:
                    return
                index = indexes[position[0]]
                position[0] += 1
            try:
                results[index] = work(index)
            except Exception as exc:  # re-raised in the calling thread below
                failures.append(exc)
                return

    threads = [threading.Thread(target=run, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30.0)
        assert thread.is_alive() is False
    if failures:
        raise failures[0]
    return results


def test_local_service_traffic_ledger_publishes_exact_count_total_and_max():
    ledger = rpc.LocalServiceTrafficLedger("unitd")

    for sample in (1.0, 7.0, 3.0):
        ledger.record_completion(rpc.LOCAL_SERVICE_TRAFFIC_WORK, client_elapsed_ms=sample, service_duration_ms=sample / 2)
    # A clock that ran backwards is not a duration; it must not lower the total or the max.
    ledger.record_completion(rpc.LOCAL_SERVICE_TRAFFIC_WORK, client_elapsed_ms=-5.0)

    work = ledger.snapshot()["work"]
    assert (work["accepted"], work["completed"], work["errors"]) == (4, 4, 0)
    assert work["client_latency_ms"] == {"count": 4, "total_ms": 11.0, "max_ms": 7.0, "avg_ms": 2.75}
    assert work["service_latency_ms"] == {"count": 4, "total_ms": 5.5, "max_ms": 3.5, "avg_ms": 1.375}
    assert ledger.snapshot()["schema_version"] == rpc.LOCAL_SERVICE_TRAFFIC_SCHEMA_VERSION


def test_local_service_traffic_ledger_bounds_reason_vocabulary_without_losing_a_request():
    ledger = rpc.LocalServiceTrafficLedger("unitd")
    distinct = rpc.LOCAL_SERVICE_TRAFFIC_MAX_REASONS + 4

    for index in range(distinct):
        ledger.record_failure(rpc.LOCAL_SERVICE_TRAFFIC_WORK, f"reason_{index}")

    work = ledger.snapshot()["work"]
    assert work["errors"] == work["accepted"] == distinct
    assert sum(work["errors_by_reason"].values()) == distinct
    assert len(work["errors_by_reason"]) <= rpc.LOCAL_SERVICE_TRAFFIC_MAX_REASONS + 1
    assert work["errors_by_reason"][rpc.LOCAL_SERVICE_REASON_OTHER] == 4


def test_local_service_traffic_ledger_counts_only_proven_epoch_changes():
    ledger = rpc.LocalServiceTrafficLedger("unitd")

    ledger.note_epoch("pid:11")
    assert ledger.snapshot()["epoch_changes"] == 0
    ledger.note_epoch("pid:11")
    assert ledger.snapshot()["epoch_changes"] == 0
    ledger.note_epoch("pid:12")
    ledger.note_epoch("")

    assert ledger.snapshot()["epoch"] == "pid:12"
    assert ledger.snapshot()["epoch_changes"] == 1


def test_local_service_traffic_classifies_every_failure_by_typed_reason():
    """Absence, refusal, both deadline expiries, identity and revision stay separable."""
    observed = {
        rpc.local_service_failure_reason(FileNotFoundError(errno.ENOENT, "gone")),
        rpc.local_service_failure_reason(ConnectionRefusedError(errno.ECONNREFUSED, "refused")),
        rpc.local_service_failure_reason(TimeoutError("slow")),
        rpc.local_service_failure_reason(rpc.LocalRpcError("peer_handler_slow")),
        rpc.local_service_failure_reason(rpc.LocalRpcError("unattributed_latency")),
        rpc.local_service_failure_reason(rpc.LocalRpcError("response request_id mismatch")),
        rpc.local_service_failure_reason(rpc.LocalRpcError("unsupported RPC version")),
        rpc.local_service_failure_reason(rpc.LocalRpcError("invalid RPC envelope")),
        rpc.local_service_response_reason({"ok": False, "error": rpc.LOCAL_SERVICE_ERROR_BUSY, "capacity_rejected": True}),
        rpc.local_service_response_reason({"ok": False, "error": "nope", "error_code": "handler_failed"}),
        rpc.local_service_response_reason({"ok": False, "error": "nope"}),
    }

    assert observed == {
        rpc.LOCAL_SERVICE_REASON_ABSENT,
        rpc.LOCAL_SERVICE_REASON_REFUSED,
        rpc.LOCAL_SERVICE_REASON_TIMEOUT,
        rpc.LOCAL_SERVICE_REASON_DEADLINE_HANDLER,
        rpc.LOCAL_SERVICE_REASON_DEADLINE_UNATTRIBUTED,
        rpc.LOCAL_SERVICE_REASON_IDENTITY_MISMATCH,
        rpc.LOCAL_SERVICE_REASON_REVISION_MISMATCH,
        rpc.LOCAL_SERVICE_REASON_PROTOCOL,
        rpc.LOCAL_SERVICE_REASON_OVERLOAD,
        "handler_failed",
        rpc.LOCAL_SERVICE_REASON_SERVICE_ERROR,
    }
    assert rpc.local_service_response_reason({"ok": True}) == ""
    assert rpc.local_service_response_reason({"result": 1}) == ""


def test_local_service_traffic_counts_one_hundred_concurrent_successes_exactly(tmp_path, monkeypatch):
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
    stop_event = threading.Event()
    worker = _run_traffic_service(socket_path, lock_path, stop_event, concurrent_handlers=32)

    def call(index):
        envelope = rpc.new_envelope("trafficd", "echo", {"action": f"echo-{index}"}, timeout_seconds=10.0)
        return rpc.request(socket_path, envelope, timeout_seconds=10.0)[0]

    responses = _fan_out(100, call, workers=8)
    _stop_traffic_service(worker, stop_event, socket_path)

    assert all(response["ok"] is True for response in responses)
    work = _traffic("trafficd")["work"]
    assert (work["accepted"], work["completed"], work["errors"]) == (100, 100, 0)
    assert work["errors_by_reason"] == {}
    assert work["client_latency_ms"]["count"] == 100
    assert work["service_latency_ms"]["count"] == 100
    assert _traffic("trafficd")["probe"]["accepted"] == 0


def test_local_service_traffic_accounts_every_attempt_under_capacity_rejection(tmp_path, monkeypatch):
    """Contended fan-out: completions plus typed overload refusals equal the attempts exactly."""
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
    stop_event = threading.Event()
    worker = _run_traffic_service(socket_path, lock_path, stop_event, concurrent_handlers=1, slow_seconds=0.005)

    def call(index):
        envelope = rpc.new_envelope("trafficd", "slow", {"action": "slow"}, timeout_seconds=10.0)
        return rpc.request(socket_path, envelope, timeout_seconds=10.0)[0]

    responses = _fan_out(100, call, workers=16)
    _stop_traffic_service(worker, stop_event, socket_path)

    rejected = sum(1 for response in responses if response.get("capacity_rejected") is True)
    work = _traffic("trafficd")["work"]
    assert work["accepted"] == 100
    assert work["completed"] + work["errors"] == 100
    assert rejected > 0, "fixture did not reach the capacity limit"
    assert work["errors_by_reason"] == {rpc.LOCAL_SERVICE_REASON_OVERLOAD: rejected}
    assert work["completed"] == 100 - rejected
    assert work["client_latency_ms"]["count"] == work["completed"]


def test_a_capacity_refusal_written_before_the_peer_closed_is_not_lost(tmp_path, monkeypatch):
    """The listener refuses at capacity on accept and closes without reading the request.

    A client descheduled between ``connect()`` and its first send therefore fails with
    EPIPE while the complete typed refusal already sits in its own receive queue.  The
    full gate hit exactly this under load: ``BrokenPipeError`` escaped ``rpc.request``
    and the attempt was counted as ``transport_error`` rather than the proven overload.
    """
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
    stop_event = threading.Event()
    holding = threading.Event()
    release = threading.Event()

    def hold_the_only_handler_slot():
        holding.set()
        assert release.wait(timeout=10.0) is True

    worker = _run_traffic_service(
        socket_path, lock_path, stop_event, concurrent_handlers=1, on_slow=hold_the_only_handler_slot,
    )
    holder = threading.Thread(
        target=lambda: rpc.request(
            socket_path, rpc.new_envelope("trafficd", "slow", {"action": "slow"}, timeout_seconds=10.0), timeout_seconds=10.0,
        ),
        daemon=True,
    )
    holder.start()
    assert holding.wait(timeout=10.0) is True

    real_write_message = rpc.write_message
    write_failures = []

    def write_after_the_peer_has_closed(connection, envelope, payload, binary=b"", *, legacy=False):
        # Stand in for the scheduler delay the loaded gate produced between connect() and
        # the first send: wait for the peer's hangup rather than sleeping and hoping.
        poller = select.poll()
        poller.register(connection, select.POLLIN | select.POLLHUP)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if any(event & select.POLLHUP for _fd, event in poller.poll(100)):
                break
        try:
            return real_write_message(connection, envelope, payload, binary, legacy=legacy)
        except ConnectionError as exc:
            write_failures.append(exc)
            raise

    monkeypatch.setattr(rpc, "write_message", write_after_the_peer_has_closed)
    envelope = rpc.new_envelope("trafficd", "echo", {"action": "echo"}, timeout_seconds=10.0)
    payload = rpc.request(socket_path, envelope, timeout_seconds=10.0)[0]
    monkeypatch.undo()

    # Without this the test could stop exercising the race and still pass.
    assert [type(failure).__name__ for failure in write_failures] == ["BrokenPipeError"]
    assert payload["capacity_rejected"] is True
    assert payload["error"] == rpc.LOCAL_SERVICE_ERROR_BUSY
    work = _traffic("trafficd")["work"]
    assert work["errors_by_reason"] == {rpc.LOCAL_SERVICE_REASON_OVERLOAD: 1}
    # The holder is still inside its handler, so only the refused attempt has an outcome yet.
    assert (work["accepted"], work["completed"], work["errors"]) == (1, 0, 1)

    release.set()
    holder.join(timeout=10.0)
    assert holder.is_alive() is False
    _stop_traffic_service(worker, stop_event, socket_path)
    settled = _traffic("trafficd")["work"]
    assert (settled["accepted"], settled["completed"], settled["errors"]) == (2, 1, 1)


def test_a_peer_that_closed_without_writing_still_reports_the_write_failure(tmp_path, monkeypatch):
    """Negative control for the recovery read: nothing on the wire means nothing to recover."""
    requested_socket_path = tmp_path / "silentd.sock"
    socket_path = rpc.safe_socket_path(requested_socket_path, prefix="yolomux-silentd")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(8)
    accepted = threading.Event()

    def close_without_writing():
        connection, _address = listener.accept()
        connection.close()
        accepted.set()

    closer = threading.Thread(target=close_without_writing, daemon=True)
    closer.start()

    real_write_message = rpc.write_message

    def write_after_the_peer_has_closed(connection, envelope, payload, binary=b"", *, legacy=False):
        assert accepted.wait(timeout=10.0) is True
        poller = select.poll()
        poller.register(connection, select.POLLIN | select.POLLHUP)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if any(event & select.POLLHUP for _fd, event in poller.poll(100)):
                break
        return real_write_message(connection, envelope, payload, binary, legacy=legacy)

    monkeypatch.setattr(rpc, "write_message", write_after_the_peer_has_closed)
    envelope = rpc.new_envelope("silentd", "echo", {"action": "echo"}, timeout_seconds=10.0)
    with pytest.raises(BrokenPipeError):
        rpc.request(requested_socket_path, envelope, timeout_seconds=10.0)
    monkeypatch.undo()

    closer.join(timeout=10.0)
    listener.close()
    work = _traffic("silentd")["work"]
    assert work["errors_by_reason"] == {rpc.LOCAL_SERVICE_REASON_TRANSPORT: 1}
    assert (work["accepted"], work["completed"], work["errors"]) == (1, 0, 1)


@pytest.mark.parametrize(
    ("service_duration_ms", "expected_attribution"),
    [
        (15.0, rpc.LOCAL_RPC_OVER_BUDGET_HANDLER),
        (3.0, rpc.LOCAL_RPC_OVER_BUDGET_UNATTRIBUTED),
    ],
)
def test_local_service_traffic_labels_a_delivered_over_budget_response_as_diagnostics(
    tmp_path, monkeypatch, service_duration_ms, expected_attribution,
):
    """A delivered response past the budget is a COMPLETION that carries a diagnostic label.

    It separates a slow handler from latency before the handler ran, but it is never an error:
    the deadline is a telemetry budget, not a correctness bound.
    """
    envelope = rpc.new_envelope("trafficd", "history", {"action": "history"}, timeout_seconds=0.01)
    response_envelope = rpc.LocalRpcEnvelope(
        service="trafficd",
        method="history",
        request_id=envelope.request_id,
        trace_id=envelope.trace_id,
        deadline_ms=envelope.deadline_ms,
        priority=envelope.priority,
        owner_generation=envelope.owner_generation,
        config_generation=envelope.config_generation,
        payload={"ok": True},
        service_duration_ms=service_duration_ms,
    )
    frame = FragmentedConnection([])
    rpc.write_message(frame, response_envelope, response_envelope.payload)

    class DelayedResponseSocket(FragmentedConnection):
        def __init__(self):
            super().__init__([frame.sent])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _seconds):
            pass

        def connect(self, _path):
            pass

    clock = iter((10.0, 10.012))
    monkeypatch.setattr(rpc.socket, "socket", lambda *_args, **_kwargs: DelayedResponseSocket())
    monkeypatch.setattr(rpc, "monotonic_clock", lambda: next(clock))

    payload, _binary = rpc.request(tmp_path / "trafficd.sock", envelope, timeout_seconds=0.1)
    assert payload == {"ok": True}

    work = _traffic("trafficd")["work"]
    # Delivered, so it is a completion with zero errors -- and it carries a diagnostic breach label.
    assert (work["accepted"], work["completed"], work["errors"]) == (1, 1, 0)
    assert work["errors_by_reason"] == {}
    assert work["over_budget"] == 1
    assert work["over_budget_by_reason"] == {expected_attribution: 1}
    # A delivered response contributes latency, unlike the former raised expiry.
    assert work["client_latency_ms"]["count"] == 1
    assert work["client_latency_ms"]["max_ms"] == pytest.approx(12.0, abs=0.5)


def test_local_service_traffic_counts_every_transport_failure_attempt(tmp_path):
    absent = tmp_path / "never-served.sock"

    def call(index):
        envelope = rpc.new_envelope("trafficd", "echo", {"action": f"echo-{index}"}, timeout_seconds=1.0)
        with pytest.raises(FileNotFoundError):
            rpc.request(absent, envelope, timeout_seconds=1.0, fallback_legacy=True)
        return True

    assert _fan_out(100, call, workers=16) == [True] * 100

    work = _traffic("trafficd")["work"]
    assert (work["accepted"], work["completed"], work["errors"]) == (100, 0, 100)
    assert work["errors_by_reason"] == {rpc.LOCAL_SERVICE_REASON_ABSENT: 100}


def test_local_service_traffic_excludes_observer_probes_from_user_work(tmp_path, monkeypatch):
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
    stop_event = threading.Event()
    worker = _run_traffic_service(socket_path, lock_path, stop_event, concurrent_handlers=8)

    def send(method, action, probe=False):
        envelope = rpc.new_envelope("trafficd", method, {"action": action}, timeout_seconds=10.0)
        return rpc.request(socket_path, envelope, timeout_seconds=10.0, probe=probe)[0]

    for _ in range(7):
        assert send("echo", "echo")["ok"] is True
    # The registry's own liveness reads are monitoring traffic by method, wherever they are sent from.
    for _ in range(3):
        assert send("ping", "ping")["ok"] is True
    # The future observer wraps its whole probe cycle, including nested lifecycle RPCs it does not own.
    with rpc.local_service_probe_scope():
        for _ in range(5):
            assert send("echo", "echo")["ok"] is True
    # A probe fanned out to a thread that cannot inherit the context declares itself explicitly.
    for _ in range(2):
        assert send("echo", "echo", probe=True)["ok"] is True
    assert rpc.local_service_traffic_class("echo") == rpc.LOCAL_SERVICE_TRAFFIC_WORK
    _stop_traffic_service(worker, stop_event, socket_path)

    snapshot = _traffic("trafficd")
    assert (snapshot["work"]["accepted"], snapshot["work"]["completed"]) == (7, 7)
    assert (snapshot["probe"]["accepted"], snapshot["probe"]["completed"]) == (10, 10)
    assert snapshot["work"]["client_latency_ms"]["count"] == 7
    assert snapshot["probe"]["client_latency_ms"]["count"] == 10


def test_local_service_traffic_survives_service_restart_with_cumulative_totals(tmp_path, monkeypatch):
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())

    def send(method, action):
        envelope = rpc.new_envelope("trafficd", method, {"action": action}, timeout_seconds=10.0)
        return rpc.request(socket_path, envelope, timeout_seconds=10.0)[0]

    first_stop = threading.Event()
    first = _run_traffic_service(socket_path, lock_path, first_stop, service_pid=4242)
    assert send("ping", "ping")["pid"] == 4242
    for _ in range(5):
        assert send("echo", "echo")["ok"] is True
    _stop_traffic_service(first, first_stop, socket_path)

    for _ in range(3):
        with pytest.raises(FileNotFoundError):
            send("echo", "echo")

    second_stop = threading.Event()
    second = _run_traffic_service(socket_path, lock_path, second_stop, service_pid=5150)
    assert send("ping", "ping")["pid"] == 5150
    for _ in range(4):
        assert send("echo", "echo")["ok"] is True
    _stop_traffic_service(second, second_stop, socket_path)

    snapshot = _traffic("trafficd")
    assert snapshot["epoch"] == "pid:5150"
    assert snapshot["epoch_changes"] == 1
    assert (snapshot["work"]["accepted"], snapshot["work"]["completed"], snapshot["work"]["errors"]) == (12, 9, 3)
    assert snapshot["work"]["errors_by_reason"] == {rpc.LOCAL_SERVICE_REASON_ABSENT: 3}
    assert (snapshot["probe"]["accepted"], snapshot["probe"]["completed"]) == (2, 2)


def test_local_service_traffic_max_names_the_slowest_request(tmp_path, monkeypatch):
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
    stop_event = threading.Event()
    worker = _run_traffic_service(socket_path, lock_path, stop_event, concurrent_handlers=8, slow_seconds=0.04)

    def send(action):
        envelope = rpc.new_envelope("trafficd", "echo", {"action": action}, timeout_seconds=10.0)
        return rpc.request(socket_path, envelope, timeout_seconds=10.0)[0]

    for _ in range(5):
        assert send("echo")["ok"] is True
    assert send("slow")["ok"] is True
    _stop_traffic_service(worker, stop_event, socket_path)

    latency = _traffic("trafficd")["work"]["service_latency_ms"]
    assert latency["count"] == 6
    assert latency["max_ms"] >= 35.0
    # The one slow handler dominates the other five combined, so the maximum cannot be a
    # minimum, a mean, or the last sample.
    assert latency["total_ms"] < 2 * latency["max_ms"]
    assert latency["avg_ms"] == round(latency["total_ms"] / 6, 3)


def test_local_service_traffic_agrees_with_the_process_global_teardown_counter(tmp_path):
    """The typed per-service ledger and registry's untyped global counter cannot diverge."""
    client = LocalServiceClient("traffic-parity", "tests.fixture", tmp_path / "traffic-parity.sock")
    before = registry_module.transport_diagnostics()["teardowns_total"]

    for _ in range(9):
        response, _binary, failure = client._request_once({"action": "submit"}, 0.2)
        assert response["_transport_error"] == "absent"
        assert failure is not None

    after = registry_module.transport_diagnostics()["teardowns_total"]
    work = _traffic("traffic-parity")["work"]
    assert after - before == 9
    assert work["errors"] == 9
    assert work["errors_by_reason"] == {rpc.LOCAL_SERVICE_REASON_ABSENT: 9}


def test_local_service_traffic_snapshot_bounds_the_retained_service_count():
    for index in range(rpc.LOCAL_SERVICE_TRAFFIC_MAX_SERVICES + 5):
        rpc.local_service_traffic_ledger(f"svc-{index}").record_failure(
            rpc.LOCAL_SERVICE_TRAFFIC_WORK, rpc.LOCAL_SERVICE_REASON_TRANSPORT,
        )

    snapshot = rpc.local_service_traffic_snapshot()
    assert len(snapshot) <= rpc.LOCAL_SERVICE_TRAFFIC_MAX_SERVICES + 1
    assert snapshot[rpc.LOCAL_SERVICE_TRAFFIC_OTHER_SERVICE]["work"]["errors"] == 5
    assert sum(entry["work"]["accepted"] for entry in snapshot.values()) == rpc.LOCAL_SERVICE_TRAFFIC_MAX_SERVICES + 5


def test_local_service_traffic_keeps_probe_and_work_separate_under_concurrency(tmp_path, monkeypatch):
    """Probe attribution is per-context: concurrent probe threads cannot taint user work."""
    socket_path = tmp_path / "trafficd.sock"
    lock_path = tmp_path / "trafficd.lock"
    monkeypatch.setattr(runtime, "peer_uid", lambda _connection: os.getuid())
    stop_event = threading.Event()
    worker = _run_traffic_service(socket_path, lock_path, stop_event, concurrent_handlers=32)
    threads_per_class = 8
    calls_per_thread = 10
    barrier = threading.Barrier(threads_per_class * 2)
    failures = []

    def send():
        envelope = rpc.new_envelope("trafficd", "echo", {"action": "echo"}, timeout_seconds=10.0)
        if rpc.request(socket_path, envelope, timeout_seconds=10.0)[0].get("ok") is not True:
            failures.append("unexpected response")

    def run(as_probe):
        barrier.wait(timeout=10.0)
        if as_probe:
            with rpc.local_service_probe_scope():
                for _ in range(calls_per_thread):
                    send()
            return
        for _ in range(calls_per_thread):
            send()

    threads = [
        threading.Thread(target=run, args=(index < threads_per_class,), daemon=True)
        for index in range(threads_per_class * 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30.0)
        assert thread.is_alive() is False
    _stop_traffic_service(worker, stop_event, socket_path)

    expected = threads_per_class * calls_per_thread
    snapshot = _traffic("trafficd")
    assert failures == []
    assert (snapshot["work"]["accepted"], snapshot["work"]["completed"]) == (expected, expected)
    assert (snapshot["probe"]["accepted"], snapshot["probe"]["completed"]) == (expected, expected)
    assert snapshot["work"]["errors"] == snapshot["probe"]["errors"] == 0


def test_a_client_reaches_a_service_whose_socket_is_published_before_it_listens(tmp_path, monkeypatch):
    """The published socket file is not yet a listening socket, and a client must survive that.

    `run_local_rpc_service` binds the path -- creating it with its final 0600 mode -- and calls
    `listen()` afterwards. Every readiness predicate built on the file (existence plus mode) is
    therefore true while the service still refuses connections, and a connect issued in that window
    fails with ECONNREFUSED against a service that is starting normally. A loaded gate hit exactly
    that window; this forces it by delaying `listen()`.
    """

    socket_path = tmp_path / "service.sock"
    service_socket_path = rpc.safe_socket_path(socket_path, prefix="yolomux-testd")
    lock_path = tmp_path / "service.lock"
    stop_event = threading.Event()
    original_listen = socket.socket.listen

    def delayed_listen(self, backlog=0):
        time.sleep(0.3)
        return original_listen(self, backlog)

    monkeypatch.setattr(socket.socket, "listen", delayed_listen)
    worker = _run_echo_service(socket_path, lock_path, stop_event, monkeypatch=monkeypatch, peer_uid=os.getuid())
    try:
        # The readiness predicate the tests use is already satisfied here, before `listen()` ran.
        assert socket_path.exists() and (socket_path.stat().st_mode & 0o777) == 0o600
        # Negative control, and the exact pre-fix failure: a single connect in this window is
        # refused, so this test can never pass because the window failed to open.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as premature:
            premature.settimeout(1.0)
            with pytest.raises(ConnectionRefusedError):
                premature.connect(str(service_socket_path))
        with _connect_to_service(service_socket_path) as client:
            envelope = rpc.new_envelope("testd", "echo", {"action": "echo"})
            rpc.write_message(client, envelope, envelope.payload)
            _response_envelope, response, _binary, _legacy = rpc.read_message(client)
        assert response == {"ok": True, "echo": {"action": "echo"}, "request_binary": ""}
    finally:
        stop_event.set()
        worker.join(timeout=2.0)

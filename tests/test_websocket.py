import io
import select
import socket
import ssl
import struct

from yolomux_lib import websocket


def masked_client_frame(payload: bytes, opcode: int = 1, mask: bytes = b"\x01\x02\x03\x04", fin: bool = True) -> bytes:
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return struct.pack("!BB", (0x80 if fin else 0x00) | opcode, 0x80 | len(payload)) + mask + masked


def test_read_ws_frame_unmasks_client_payload():
    opcode, payload = websocket.read_ws_frame(io.BytesIO(masked_client_frame(b"hello", opcode=1)))

    assert opcode == 1
    assert payload == b"hello"


def test_read_ws_frame_reassembles_fragmented_client_message():
    stream = io.BytesIO(
        masked_client_frame(b'{"type":"dom-keyframe",', opcode=1, fin=False)
        + masked_client_frame(b'"payload":{"root":', opcode=0, fin=False)
        + masked_client_frame(b'{"tag":"div"}}}', opcode=0)
    )

    opcode, payload = websocket.read_ws_frame(stream)

    assert opcode == 1
    assert payload == b'{"type":"dom-keyframe","payload":{"root":{"tag":"div"}}}'


def test_wait_for_ws_frame_detects_a_second_frame_prefetched_by_buffered_reader():
    server_socket, client_socket = socket.socketpair()
    stream = server_socket.makefile("rb")
    try:
        client_socket.sendall(masked_client_frame(b"first") + masked_client_frame(b"second"))

        assert websocket.wait_for_ws_frame(server_socket, stream, 1.0) is True
        assert websocket.read_ws_frame(stream) == (1, b"first")
        assert select.select([server_socket], [], [], 0)[0] == []
        assert websocket.wait_for_ws_frame(server_socket, stream, 0) is True
        assert websocket.read_ws_frame(stream) == (1, b"second")
    finally:
        stream.close()
        server_socket.close()
        client_socket.close()


def test_wait_for_ws_frame_treats_tls_want_read_as_unbuffered(monkeypatch):
    """A TLS no-data probe is normal, not a bridge-ending socket failure."""

    class TlsConnection:
        def gettimeout(self):
            return None

        def setblocking(self, _value):
            pass

        def settimeout(self, _value):
            pass

    class TlsBufferedReader:
        def peek(self, _size):
            raise ssl.SSLWantReadError(ssl.SSL_ERROR_WANT_READ, "need more TLS bytes")

    connection = TlsConnection()
    monkeypatch.setattr(websocket.select, "select", lambda readers, _write, _error, _timeout: (readers, [], []))

    assert websocket.wait_for_ws_frame(connection, TlsBufferedReader(), 0) is True


def test_make_ws_frame_round_trips_server_binary_payload():
    payload = b"x" * 130
    frame = websocket.make_ws_frame(payload, opcode=2)

    opcode, decoded = websocket.read_ws_frame(io.BytesIO(frame))

    assert frame[:4] == struct.pack("!BBH", 0x82, 126, len(payload))
    assert opcode == 2
    assert decoded == payload


def test_make_ws_frame_uses_64_bit_length_for_large_payload():
    payload = b"x" * 66000
    frame = websocket.make_ws_frame(payload, opcode=2)

    assert frame[:10] == struct.pack("!BBQ", 0x82, 127, len(payload))


def test_read_ws_frame_reports_closed_stream():
    try:
        websocket.read_ws_frame(io.BytesIO(b"\x81"))
    except ConnectionError as exc:
        assert "websocket closed" in str(exc)
    else:
        raise AssertionError("expected ConnectionError")


def test_read_ws_frame_rejects_oversized_declared_length():
    # a 127-marker frame declaring a huge length is rejected BEFORE buffering it, so a
    # hostile/buggy client cannot OOM the shared process. Header only — no payload bytes are read.
    header = struct.pack("!BBQ", 0x82, 0x7F, websocket.MAX_WS_FRAME_BYTES + 1)
    try:
        websocket.read_ws_frame(io.BytesIO(header))
    except ConnectionError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("expected ConnectionError for an oversized frame")


def test_set_pty_size_clamps_dimensions(monkeypatch):
    calls = []
    monkeypatch.setattr(websocket.fcntl, "ioctl", lambda fd, request, payload: calls.append((fd, request, payload)))

    websocket.set_pty_size(7, rows=1, cols=999)

    fd, request, payload = calls[0]
    assert fd == 7
    assert request == websocket.termios.TIOCSWINSZ
    assert struct.unpack("HHHH", payload) == (2, 500, 0, 0)

import io
import struct

from yolomux_lib import websocket


def masked_client_frame(payload: bytes, opcode: int = 1, mask: bytes = b"\x01\x02\x03\x04") -> bytes:
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return struct.pack("!BB", 0x80 | opcode, 0x80 | len(payload)) + mask + masked


def test_read_ws_frame_unmasks_client_payload():
    opcode, payload = websocket.read_ws_frame(io.BytesIO(masked_client_frame(b"hello", opcode=1)))

    assert opcode == 1
    assert payload == b"hello"


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


def test_set_pty_size_clamps_dimensions(monkeypatch):
    calls = []
    monkeypatch.setattr(websocket.fcntl, "ioctl", lambda fd, request, payload: calls.append((fd, request, payload)))

    websocket.set_pty_size(7, rows=1, cols=999)

    fd, request, payload = calls[0]
    assert fd == 7
    assert request == websocket.termios.TIOCSWINSZ
    assert struct.unpack("HHHH", payload) == (2, 500, 0, 0)

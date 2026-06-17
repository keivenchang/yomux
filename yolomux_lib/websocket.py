from __future__ import annotations

import fcntl
import struct
import termios
from typing import Any

# hard ceiling on an inbound WebSocket frame's declared length. Browser terminal input is
# tiny; this only bounds a hostile/buggy client so it cannot OOM the shared process.
MAX_WS_FRAME_BYTES = 16 * 1024 * 1024


def set_pty_size(fd: int, rows: int, cols: int) -> None:
    rows = max(2, min(rows, 300))
    cols = max(20, min(cols, 500))
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

def read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ConnectionError("websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

def read_ws_frame_once(stream: Any) -> tuple[bool, int, bytes]:
    header = read_exact(stream, 2)
    first, second = header
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(stream, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(stream, 8))[0]
    # #65: reject an oversized declared length BEFORE buffering it (memory-exhaustion DoS guard).
    if length > MAX_WS_FRAME_BYTES:
        raise ConnectionError(f"websocket frame too large: {length} > {MAX_WS_FRAME_BYTES}")
    mask = read_exact(stream, 4) if masked else b""
    payload = read_exact(stream, length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return fin, opcode, payload

def read_ws_frame(stream: Any) -> tuple[int, bytes]:
    fin, opcode, payload = read_ws_frame_once(stream)
    if opcode == 0:
        raise ConnectionError("unexpected websocket continuation frame")
    if fin or opcode not in {1, 2}:
        return opcode, payload

    chunks = [payload]
    total_length = len(payload)
    while True:
        next_fin, next_opcode, next_payload = read_ws_frame_once(stream)
        if next_opcode == 8:
            return next_opcode, next_payload
        if next_opcode in {9, 10}:
            continue
        if next_opcode != 0:
            raise ConnectionError("unexpected websocket frame during fragmented message")
        total_length += len(next_payload)
        if total_length > MAX_WS_FRAME_BYTES:
            raise ConnectionError(f"websocket frame too large: {total_length} > {MAX_WS_FRAME_BYTES}")
        chunks.append(next_payload)
        if next_fin:
            break
    return opcode, b"".join(chunks)

def make_ws_frame(payload: bytes, opcode: int = 2) -> bytes:
    first = 0x80 | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, 126, length)
    else:
        header = struct.pack("!BBQ", first, 127, length)
    return header + payload

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any
from typing import Callable

from .common import CONTROL_SOCKET_DIR


CONTROL_MAX_BYTES = 65536
LOGGER = logging.getLogger(__name__)


class ControlRequestError(Exception):
    pass


def control_socket_path(token: str | None = None, pid: int | None = None) -> Path:
    suffix = f"-{token}" if token else ""
    return CONTROL_SOCKET_DIR / f"yolomux-{pid or os.getpid()}{suffix}.sock"


def send_yolomux_control_request(owner: dict[str, Any] | None, request: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    socket_path = owner.get("control_socket") if isinstance(owner, dict) else None
    if not isinstance(socket_path, str) or not socket_path:
        return {"ok": False, "error": "owner has no control socket"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(socket_path)
            client.sendall((json.dumps(request, sort_keys=True) + "\n").encode("utf-8"))
            chunks: list[bytes] = []
            while sum(len(chunk) for chunk in chunks) < CONTROL_MAX_BYTES:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8").splitlines()[0])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"invalid control response: {exc}"}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "invalid control response"}


class YolomuxControlServer:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]):
        self.handler = handler
        self.path = control_socket_path(token=f"{id(self):x}")
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="yolomux-control", daemon=True)
        self.socket: socket.socket | None = None

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        server.listen(16)
        server.settimeout(0.5)
        self.socket = server
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.1)
                client.connect(str(self.path))
        except OSError:
            pass
        self.thread.join(timeout=1.0)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def owner_payload(self) -> dict[str, Any]:
        return {"control_socket": str(self.path)}

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                assert self.socket is not None
                conn, _addr = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stop_event.is_set():
                    break
                continue
            with conn:
                self.serve_connection(conn)

    def serve_connection(self, conn: socket.socket) -> None:
        request = self.read_request(conn)
        if request is None:
            self.write_response(conn, {"ok": False, "error": "invalid control request"})
            return
        try:
            response = self.handler(request)
        except ControlRequestError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            LOGGER.exception("yolomux control handler failed")
            response = {"ok": False, "error": "internal control handler error"}
        self.write_response(conn, response)

    def read_request(self, conn: socket.socket) -> dict[str, Any] | None:
        chunks: list[bytes] = []
        while sum(len(chunk) for chunk in chunks) < CONTROL_MAX_BYTES:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8").splitlines()[0])
        except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def write_response(self, conn: socket.socket, response: dict[str, Any]) -> None:
        conn.sendall((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))

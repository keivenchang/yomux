from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any
from typing import Callable

from .common import CONTROL_SOCKET_DIR
from .local_services.rpc import LOCAL_RPC_MAX_METADATA_BYTES
from .local_services.rpc import LocalRpcError
from .local_services.rpc import LocalRpcEnvelope
from .local_services.rpc import new_envelope
from .local_services.rpc import read_message
from .local_services.rpc import request as local_service_request
from .local_services.rpc import safe_socket_path
from .local_services.rpc import write_message


CONTROL_MAX_BYTES = LOCAL_RPC_MAX_METADATA_BYTES
CONTROL_SOCKET_PATH_LIMIT = 96
LOGGER = logging.getLogger(__name__)


class ControlRequestError(Exception):
    pass


def control_socket_path(token: str | None = None, pid: int | None = None) -> Path:
    suffix = f"-{token}" if token else ""
    filename = f"yolomux-{pid or os.getpid()}{suffix}.sock"
    return safe_socket_path(CONTROL_SOCKET_DIR / filename, prefix="ycs", fallback_name=filename)


def send_yolomux_control_request(owner: dict[str, Any] | None, request: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    socket_path = owner.get("control_socket") if isinstance(owner, dict) else None
    if not isinstance(socket_path, str) or not socket_path:
        return {"ok": False, "error": "owner has no control socket"}
    try:
        envelope = new_envelope("control", str(request.get("action") or "request"), request, timeout_seconds=timeout)
        payload, _binary = local_service_request(socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
    except (OSError, LocalRpcError) as exc:
        return {"ok": False, "error": str(exc)}
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
        try:
            envelope, request, _binary, legacy = read_message(conn)
        except LocalRpcError:
            self.write_response(conn, None, {"ok": False, "error": "invalid control request"}, legacy=True)
            return
        try:
            response = self.handler(request)
        except ControlRequestError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            LOGGER.exception("yolomux control handler failed")
            response = {"ok": False, "error": "internal control handler error"}
        response_envelope = None if legacy or envelope is None else LocalRpcEnvelope(
            service="control",
            method=envelope.method,
            request_id=envelope.request_id,
            trace_id=envelope.trace_id,
            deadline_ms=envelope.deadline_ms,
            priority=envelope.priority,
            owner_generation=envelope.owner_generation,
            config_generation=envelope.config_generation,
            payload=response,
        )
        self.write_response(conn, response_envelope, response, legacy=legacy)

    def write_response(
        self,
        conn: socket.socket,
        envelope: LocalRpcEnvelope | None,
        response: dict[str, Any],
        *,
        legacy: bool,
    ) -> None:
        try:
            write_message(conn, envelope, response, legacy=legacy)
        except (BrokenPipeError, LocalRpcError):
            return

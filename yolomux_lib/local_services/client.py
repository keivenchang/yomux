"""Shared client parent for versioned local Unix services."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

from .registry import LocalServiceRegistry
from .registry import LocalServiceSpec
from .rpc import LOCAL_RPC_VERSION
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request as local_service_request
from .rpc import safe_socket_path
from .runtime import redact_local_service_text


class LocalServiceClient:
    """Thin typed client that owns shared registry/RPC behavior once."""

    def __init__(self, service: str, module: str, socket_path: Path, protocol_version: int = LOCAL_RPC_VERSION, *, idle_seconds: float = 60.0, extra_args: tuple[str, ...] = (), code_revision: str = ""):
        self.service = service
        self.socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{service}")
        self.registry = LocalServiceRegistry(
            self.socket_path.parent,
            LocalServiceSpec(service, module, self.socket_path.name, protocol_version, idle_seconds=idle_seconds, extra_args=extra_args, code_revision=code_revision),
            socket_path=self.socket_path,
        )

    def request_with_binary(self, payload: dict[str, Any], timeout: float = 0.5) -> tuple[dict[str, Any], bytes]:
        try:
            envelope = new_envelope(self.service, str(payload.get("action") or "request"), payload, timeout_seconds=timeout)
            response, binary = local_service_request(self.socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
        except (OSError, LocalRpcError) as exc:
            self.registry.note_rpc_failure()
            transport_error = "rpc"
            if isinstance(exc, TimeoutError):
                transport_error = "timeout"
            elif isinstance(exc, OSError) and exc.errno == errno.ENOENT:
                transport_error = "absent"
            elif isinstance(exc, OSError) and exc.errno == errno.ECONNREFUSED:
                transport_error = "refused"
            return {
                "ok": False,
                "error": redact_local_service_text(exc),
                "_transport_error": transport_error,
            }, b""
        self.registry.note_rpc_success()
        return (response, binary) if isinstance(response, dict) else ({"ok": False, "error": "invalid local service response"}, b"")

    def request(self, payload: dict[str, Any], timeout: float = 0.5) -> dict[str, Any]:
        response, _binary = self.request_with_binary(payload, timeout=timeout)
        return response

    def ensure_started(self) -> bool:
        return self.registry.ensure_started()

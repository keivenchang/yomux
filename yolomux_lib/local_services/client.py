"""Shared client parent for versioned local Unix services."""

from __future__ import annotations

import errno
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from time import monotonic as monotonic_clock
from typing import Any

from ..server_logs import emit_server_log
from .registry import LocalServiceRegistry
from .registry import LocalServiceSpec
from .rpc import LOCAL_RPC_DEADLINE_REASONS
from .rpc import LOCAL_RPC_VERSION
from .rpc import LOCAL_SERVICE_ERROR_BUSY
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request as local_service_request
from .rpc import safe_socket_path
from .runtime import redact_local_service_text
from .runtime import local_service_exception_cause


@dataclass(frozen=True)
class TransportFailure:
    error: OSError | LocalRpcError
    traceback_text: str
    action: str
    request_id: str
    client_elapsed_ms: float


def local_service_failure_is_transient(response: Mapping[str, object]) -> bool:
    """Return whether a local-service failure is safe for a bounded retry."""

    if response.get("ok") is True or response.get("terminal") is True:
        return False
    transport_error = str(response.get("_transport_error") or "").strip().lower()
    if transport_error in {"timeout", "absent", "refused"}:
        return True
    error = str(response.get("error") or "").strip().lower()
    # A deadline breach is the same physical event as the `timeout` above -- the peer could not
    # answer in time -- so it must be retryable for the same reason.  Matching one hand-written
    # spelling here silently made every real breach terminal, because `rpc` raises
    # `peer_handler_slow`/`unattributed_latency` and never that spelling.
    if transport_error == "rpc" and error in LOCAL_RPC_DEADLINE_REASONS:
        return True
    try:
        status = int(response.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    return status == HTTPStatus.SERVICE_UNAVAILABLE or error in {"refreshing", LOCAL_SERVICE_ERROR_BUSY}


class LocalServiceClient:
    """Thin typed client that owns shared registry/RPC behavior once."""

    def __init__(self, service: str, module: str, socket_path: Path, protocol_version: int = LOCAL_RPC_VERSION, *, idle_seconds: float = 60.0, extra_args: tuple[str, ...] = (), code_revision: str = "", build_revision: int = 0, service_dir: Path | None = None):
        requested_socket_path = Path(socket_path)
        requested_service_dir = Path(service_dir) if service_dir is not None else requested_socket_path.parent
        self.service = service
        self.socket_path = safe_socket_path(requested_socket_path, prefix=f"yolomux-{service}")
        self.registry = LocalServiceRegistry(
            requested_service_dir,
            LocalServiceSpec(service, module, self.socket_path.name, protocol_version, idle_seconds=idle_seconds, extra_args=extra_args, code_revision=code_revision, build_revision=build_revision),
            socket_path=self.socket_path,
            service_dir=requested_service_dir,
        )

    def _request_once(
        self,
        payload: dict[str, Any],
        timeout: float,
        request_binary: bytes = b"",
        *,
        probe: bool = False,
    ) -> tuple[dict[str, Any], bytes, TransportFailure | None]:
        action = str(payload.get("action") or "request")
        request_id = ""
        started = monotonic_clock()
        try:
            envelope = new_envelope(self.service, action, payload, timeout_seconds=timeout)
            request_id = envelope.request_id
            # The transport owns the retained per-service request/error/latency aggregate;
            # `probe` only forwards the caller's explicit classification for a monitoring
            # probe that cannot run inside `local_service_probe_scope()`.
            response, binary = local_service_request(
                self.socket_path,
                envelope,
                binary=request_binary,
                timeout_seconds=timeout,
                fallback_legacy=True,
                probe=probe,
            )
        except (OSError, LocalRpcError) as exc:
            self.registry.note_rpc_failure(type(exc).__name__)
            exception_type = type(exc).__name__
            redacted_error = redact_local_service_text(exc)
            transport_error = self._transport_error(exc)
            return {
                "ok": False,
                "error": redacted_error,
                "_transport_error": transport_error,
                "exception_type": exception_type,
                "cause": local_service_exception_cause(exc),
            }, b"", TransportFailure(
                error=exc,
                traceback_text=traceback.format_exc(),
                action=action,
                request_id=request_id,
                client_elapsed_ms=(monotonic_clock() - started) * 1000.0,
            )
        self.registry.note_rpc_success()
        if isinstance(response, dict):
            return response, binary, None
        return {"ok": False, "error": "invalid local service response"}, b"", None

    @staticmethod
    def _transport_error(exc: OSError | LocalRpcError) -> str:
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, OSError) and exc.errno == errno.ENOENT:
            return "absent"
        if isinstance(exc, OSError) and exc.errno == errno.ECONNREFUSED:
            return "refused"
        return "rpc"

    def _emit_transport_error(self, failure: TransportFailure) -> None:
        exc = failure.error
        traceback_text = failure.traceback_text
        exception_type = type(exc).__name__
        redacted_error = redact_local_service_text(exc)
        action = redact_local_service_text(failure.action).replace("\r", " ").replace("\n", " ")
        if redacted_error == "[redacted]":
            traceback_text = "Traceback (most recent call last):\n[redacted]"
        emit_server_log(
            "error",
            f"local-service:{self.service}",
            (
                f"action={action} request_id={failure.request_id} "
                f"client_elapsed_ms={max(0.0, failure.client_elapsed_ms):.3f}\n"
                f"{exception_type}: {redacted_error}\n{traceback_text}"
            ),
            category="transport",
            dedupe_key=(
                f"local-service:{self.service}:{action}:"
                f"{exception_type}:{self._transport_error(exc)}"
            ),
            dedupe_seconds=5.0,
            request_id=failure.request_id,
            route=f"local-service:{self.service}",
            event=action,
            delivery=self._transport_error(exc),
        )

    def request_with_binary(
        self,
        payload: dict[str, Any],
        timeout: float = 0.5,
        request_binary: bytes = b"",
        *,
        probe: bool = False,
    ) -> tuple[dict[str, Any], bytes]:
        response, binary, error = self._request_once(payload, timeout, request_binary, probe=probe)
        # A missing or refused socket is a demand against a service that may
        # have completed idle shutdown while its launcher still owns an
        # unreaped child. The shared registry is the only lifecycle owner: it
        # reaps that child, serializes replacement startup, and this one retry
        # preserves the caller's original typed transport failure if recovery
        # cannot establish a serving socket.
        if response.get("_transport_error") not in {"absent", "refused"}:
            if error is not None:
                self._emit_transport_error(error)
            return response, binary
        if not self.registry.ensure_started():
            if not self.registry.starts_allowed():
                return {
                    "ok": False,
                    "error": f"{self.service} is stopping",
                    "status": "unavailable",
                    "terminal": True,
                    "_transport_error": "stopped",
                }, b""
            if error is not None:
                self._emit_transport_error(error)
            return response, binary
        response, binary, retry_error = self._request_once(payload, timeout, request_binary, probe=probe)
        if retry_error is not None:
            self._emit_transport_error(retry_error)
        return response, binary

    def request(self, payload: dict[str, Any], timeout: float = 0.5, *, probe: bool = False) -> dict[str, Any]:
        response, _binary = self.request_with_binary(payload, timeout=timeout, probe=probe)
        return response

    def ensure_started(self) -> bool:
        return self.registry.ensure_started()

    def retry(self) -> bool:
        self.registry.retry()
        return self.registry.ensure_started()

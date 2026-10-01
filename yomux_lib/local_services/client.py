"""Shared client parent for versioned local Unix services."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from time import monotonic as monotonic_clock
from time import sleep as sleep_for
from typing import Any

from ..server_logs import emit_server_log
from .registry import LOCAL_SERVICE_START_TIMEOUT_SECONDS
from .registry import LocalServiceRegistry
from .registry import LocalServiceSpec
from .rpc import LOCAL_RPC_DEADLINE_REASONS
from .rpc import LOCAL_RPC_VERSION
from .rpc import LOCAL_SERVICE_DEADLINE_REASONS
from .rpc import LOCAL_SERVICE_LIFECYCLE_REASONS
from .rpc import LOCAL_SERVICE_REASON_TIMEOUT
from .rpc import LocalRpcError
from .rpc import local_service_failure_reason
from .rpc import local_service_response_is_prehandler_busy
from .rpc import new_envelope
from .rpc import request as local_service_request
from .rpc import retry_local_service_prehandler_busy
from .rpc import safe_socket_path
from .runtime import redact_local_service_text
from .runtime import local_service_exception_cause


LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS = 1.0


class LocalServiceLeaseRelease:
    """Keep one release owned until the peer acknowledges it or rejects it terminally."""

    def __init__(
        self,
        release: Callable[[str], Mapping[str, Any]],
        lease_id: str,
        *,
        retry_seconds: float,
        expected_errors: tuple[type[Exception], ...],
    ) -> None:
        self.release = release
        self.lease_id = lease_id
        self.retry_seconds = max(0.001, float(retry_seconds))
        self.expected_errors = expected_errors
        self.completed = threading.Event()
        self.terminal_response: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    def _attempt(self) -> bool:
        try:
            response = self.release(self.lease_id)
        except self.expected_errors:
            return False
        if response.get("ok") is True:
            self.completed.set()
            return True
        error_code = str(response.get("error_code") or "").strip().lower()
        error = str(response.get("error") or "").strip().lower()
        terminal = response.get("terminal") is True or "upgrade_required" in {error_code, error}
        if not terminal and local_service_failure_is_transient(response):
            return False
        self.terminal_response = dict(response)
        self.completed.set()
        reason = redact_local_service_text(error_code or error or response.get("status") or "request_failed")
        emit_server_log(
            "error",
            "local-service:lease-release",
            f"lease release stopped: {reason}",
            category="lifecycle",
            dedupe_key=f"local-service:lease-release:{reason}",
            dedupe_seconds=5.0,
            route="local-service:lease-release",
            event="lease-release",
            delivery="terminal",
        )
        return True

    def _retry(self) -> None:
        while not self.completed.wait(self.retry_seconds):
            if self._attempt():
                return

    def start(self) -> "LocalServiceLeaseRelease":
        """Attempt synchronously once, then retain a process-lifetime retry owner if needed."""

        if self._attempt():
            return self
        worker = threading.Thread(
            target=self._retry,
            name="local-service-lease-release",
            daemon=True,
        )
        self._thread = worker
        worker.start()
        return self


def release_local_service_lease_eventually(
    release: Callable[[str], Mapping[str, Any]],
    lease_id: str,
    *,
    retry_seconds: float | None = None,
    expected_errors: tuple[type[Exception], ...] = (OSError, LocalRpcError),
) -> LocalServiceLeaseRelease:
    """Release now or keep retrying bounded attempts for this web process's lifetime."""

    normalized = str(lease_id or "")
    if not normalized:
        raise ValueError("lease_id must be non-empty")
    return LocalServiceLeaseRelease(
        release,
        normalized,
        retry_seconds=(
            LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS
            if retry_seconds is None
            else retry_seconds
        ),
        expected_errors=expected_errors,
    ).start()


@dataclass(frozen=True)
class TransportFailure:
    error: OSError | LocalRpcError
    traceback_text: str
    action: str
    request_id: str
    client_elapsed_ms: float


class DeferredTransportErrors:
    """Operation-scoped owner for transport failures that may recover before its deadline."""

    def __init__(self, client: "LocalServiceClient") -> None:
        self.client = client
        self.failure: TransportFailure | None = None

    def capture(self, failure: TransportFailure) -> None:
        self.failure = failure

    def publish(self) -> None:
        if self.failure is not None:
            self.client._emit_transport_error(self.failure)
            self.failure = None


_DEFERRED_TRANSPORT_ERRORS: ContextVar[tuple[DeferredTransportErrors, ...]] = ContextVar(
    "local_service_deferred_transport_errors",
    default=(),
)


@contextmanager
def defer_local_service_transport_errors(client: "LocalServiceClient"):
    """Keep retryable poll diagnostics private until the operation owner publishes one."""

    scope = DeferredTransportErrors(client)
    token = _DEFERRED_TRANSPORT_ERRORS.set((*_DEFERRED_TRANSPORT_ERRORS.get(), scope))
    try:
        yield scope
    finally:
        _DEFERRED_TRANSPORT_ERRORS.reset(token)


def deferred_transport_errors(client: object):
    """Return a real deferral scope for typed clients and a no-op scope for test doubles."""

    if isinstance(client, LocalServiceClient):
        return defer_local_service_transport_errors(client)
    return nullcontext(None)


@dataclass(frozen=True)
class LocalServicePollingCapabilities:
    lifecycle_recovery: bool


def local_service_polling_capabilities(client: object) -> LocalServicePollingCapabilities:
    """Describe retry ownership without guessing from attributes on arbitrary client doubles."""

    return LocalServicePollingCapabilities(lifecycle_recovery=isinstance(client, LocalServiceClient))


def local_service_failure_is_transient(
    response: Mapping[str, Any],
    *,
    capabilities: LocalServicePollingCapabilities | None = None,
) -> bool:
    """Return whether a local-service failure is safe for a bounded retry."""

    if response.get("ok") is True or response.get("terminal") is True:
        return False
    transport_error = str(response.get("_transport_error") or "").strip().lower()
    if transport_error == LOCAL_SERVICE_REASON_TIMEOUT:
        return True
    if transport_error in LOCAL_SERVICE_LIFECYCLE_REASONS:
        return capabilities is None or capabilities.lifecycle_recovery
    # A deadline breach is the same physical event as the `timeout` above -- the peer could not
    # answer in time -- so it must be retryable for the same reason.  The shared classifier now
    # types it directly; the error-text arm stays because a peer that predates the typed reason,
    # or a payload minted by another producer, still spells it only in `error`.
    if transport_error in LOCAL_SERVICE_DEADLINE_REASONS:
        return True
    error = str(response.get("error") or "").strip().lower()
    if error in LOCAL_RPC_DEADLINE_REASONS:
        return True
    try:
        status = int(response.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    return status == HTTPStatus.SERVICE_UNAVAILABLE or error == "refreshing" or local_service_failure_is_busy(response)


def local_service_failure_is_busy(response: Mapping[str, Any]) -> bool:
    """Return whether the peer explicitly refused the request before running its handler."""

    return local_service_response_is_prehandler_busy(response)


class LocalServiceClient:
    """Thin typed client that owns shared registry/RPC behavior once."""

    def __init__(self, service: str, module: str, socket_path: Path, protocol_version: int = LOCAL_RPC_VERSION, *, idle_seconds: float = 60.0, start_timeout_seconds: float | None = None, extra_args: tuple[str, ...] = (), code_revision: str = "", build_revision: int = 0, service_dir: Path | None = None):
        requested_socket_path = Path(socket_path)
        requested_service_dir = Path(service_dir) if service_dir is not None else requested_socket_path.parent
        self.service = service
        self.socket_path = safe_socket_path(requested_socket_path, prefix=f"yolomux-{service}")
        spec_start_timeout = LOCAL_SERVICE_START_TIMEOUT_SECONDS if start_timeout_seconds is None else start_timeout_seconds
        self.registry = LocalServiceRegistry(
            requested_service_dir,
            LocalServiceSpec(service, module, self.socket_path.name, protocol_version, idle_seconds=idle_seconds, start_timeout_seconds=spec_start_timeout, extra_args=extra_args, code_revision=code_revision, build_revision=build_revision),
            socket_path=self.socket_path,
            service_dir=requested_service_dir,
        )
        self._reported_terminal_startup_reason = ""

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
        return local_service_failure_reason(exc)

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

    def _report_transport_error(self, failure: TransportFailure) -> None:
        for scope in reversed(_DEFERRED_TRANSPORT_ERRORS.get()):
            if scope.client is self:
                scope.capture(failure)
                return
        self._emit_transport_error(failure)

    def _request_until_not_busy(
        self,
        payload: dict[str, Any],
        timeout: float,
        request_binary: bytes = b"",
        *,
        probe: bool = False,
    ) -> tuple[dict[str, Any], bytes, TransportFailure | None]:
        """Retry explicit pre-handler overload only, within the caller's original RPC budget."""

        def attempt(attempt_timeout: float) -> tuple[dict[str, Any], bytes, TransportFailure | None]:
            return self._request_once(
                payload,
                attempt_timeout,
                request_binary,
                probe=probe,
            )

        return retry_local_service_prehandler_busy(
            attempt,
            lambda result: result[0],
            timeout,
            clock=monotonic_clock,
            sleep=sleep_for,
        )

    def request_with_binary(
        self,
        payload: dict[str, Any],
        timeout: float = 0.5,
        request_binary: bytes = b"",
        *,
        probe: bool = False,
    ) -> tuple[dict[str, Any], bytes]:
        response, binary, error = self._request_until_not_busy(
            payload,
            timeout,
            request_binary,
            probe=probe,
        )
        # A missing or refused socket is a demand against a service that may
        # have completed idle shutdown while its launcher still owns an
        # unreaped child. The shared registry is the only lifecycle owner: it
        # reaps that child, serializes replacement startup, and this one retry
        # preserves the caller's original typed transport failure if recovery
        # cannot establish a serving socket.
        if response.get("_transport_error") not in LOCAL_SERVICE_LIFECYCLE_REASONS:
            if error is not None:
                self._report_transport_error(error)
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
                self._report_transport_error(error)
            return response, binary
        response, binary, retry_error = self._request_until_not_busy(
            payload,
            timeout,
            request_binary,
            probe=probe,
        )
        if retry_error is not None:
            self._report_transport_error(retry_error)
        return response, binary

    def request(self, payload: dict[str, Any], timeout: float = 0.5, *, probe: bool = False) -> dict[str, Any]:
        response, _binary = self.request_with_binary(payload, timeout=timeout, probe=probe)
        return response

    def request_if_running(self, payload: dict[str, Any], timeout: float = 0.5, *, probe: bool = False) -> dict[str, Any]:
        """Query an existing service without turning observation into launch demand."""

        response, _binary = self.request_with_binary_if_running(payload, timeout=timeout, probe=probe)
        return response

    def request_with_binary_if_running(
        self,
        payload: dict[str, Any],
        timeout: float = 0.5,
        *,
        probe: bool = False,
    ) -> tuple[dict[str, Any], bytes]:
        """Query an existing service for bytes without turning observation into launch demand."""

        response, binary, error = self._request_until_not_busy(payload, timeout, probe=probe)
        if error is not None and response.get("_transport_error") not in LOCAL_SERVICE_LIFECYCLE_REASONS:
            self._report_transport_error(error)
        return response, binary

    def ensure_started(self) -> bool:
        started = self.registry.ensure_started()
        if started:
            self._reported_terminal_startup_reason = ""
            return True
        failure = self.registry.failure_response()
        reason = str(failure.get("reason") or "")
        if failure.get("terminal") is True and reason != self._reported_terminal_startup_reason:
            emit_server_log(
                "error",
                f"local-service:{self.service}",
                reason,
                category="startup",
                dedupe_key=f"local-service:{self.service}:startup:{reason}",
                dedupe_seconds=5.0,
                route=f"local-service:{self.service}",
                event="startup",
                delivery="terminal",
            )
            self._reported_terminal_startup_reason = reason
        return False

    def retry(self) -> bool:
        self.registry.retry()
        self._reported_terminal_startup_reason = ""
        return self.ensure_started()

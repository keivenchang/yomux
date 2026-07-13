"""One service-side Unix-RPC listener lifecycle for local YOLOmux services.

Clients use :mod:`registry` for discovery and spawn.  Services use this module
for the reciprocal lock/socket/accept lifecycle so stateful services do not
copy subtly different permissions, cleanup, or rolling-RPC behavior.
"""

from __future__ import annotations

import fcntl
import os
import signal
import socket
import struct
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

from .rpc import LocalRpcEnvelope
from .rpc import LocalRpcError
from .rpc import read_message
from .rpc import safe_socket_path
from .rpc import write_message


LocalServiceResponse = tuple[dict[str, object], bytes]
SignalHandlers = list[tuple[int, signal.Handlers]]
LOCAL_SERVICE_CONNECTION_TIMEOUT_SECONDS = 0.5
LOCAL_SERVICE_MAX_CLIENT_LEASES = 64
LOCAL_SERVICE_SECRET_MARKERS = ("token", "secret", "password", "cookie", "authorization", "api_key", "apikey", "bearer")


def apply_service_process_priority(increment: int = 5) -> bool:
    """Best-effort lower priority for foreground service children."""
    if increment <= 0 or not hasattr(os, "nice"):
        return False
    try:
        os.nice(increment)
    except (OSError, ValueError):
        return False
    return True


def install_stop_signal_handlers(stop_event: Event) -> SignalHandlers:
    """Set portable stop handlers where the current thread/platform allows it."""
    previous: SignalHandlers = []

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    for name in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            prior = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        except (OSError, ValueError):
            continue
        previous.append((signum, prior))
    return previous


def restore_signal_handlers(previous: SignalHandlers) -> None:
    for signum, handler in reversed(previous):
        try:
            signal.signal(signum, handler)
        except (OSError, ValueError):
            pass


def redact_local_service_text(value: object) -> str:
    """Return bounded diagnostic text without common credential material."""
    text = str(value or "")
    lower = text.lower()
    if any(marker in lower for marker in LOCAL_SERVICE_SECRET_MARKERS):
        return "[redacted]"
    return text[:256]


def acquire_client_lease(leases: dict[str, int], client_pid: object) -> dict[str, object]:
    """Bound the shared local-service lease table for every daemon."""
    if len(leases) >= LOCAL_SERVICE_MAX_CLIENT_LEASES:
        return {"ok": False, "error": "too many clients", "leases": len(leases)}
    try:
        pid = max(0, int(client_pid or 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid client pid", "leases": len(leases)}
    lease_id = f"{os.getpid()}-{time.time_ns()}-{len(leases)}"
    leases[lease_id] = pid
    return {"ok": True, "lease_id": lease_id, "pid": os.getpid(), "leases": len(leases)}


def release_client_lease(leases: dict[str, int], lease_id: object) -> dict[str, object]:
    """Release a local-service lease without exposing table internals."""
    text = str(lease_id or "")
    if text:
        leases.pop(text, None)
    return {"ok": True, "leases": len(leases)}


def peer_uid(connection: socket.socket) -> int | None:
    """Return the Unix peer UID where the platform exposes ``SO_PEERCRED``."""
    if not hasattr(socket, "SO_PEERCRED"):
        return None
    try:
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", credentials)
    except OSError:
        return None
    return int(uid)


def run_local_rpc_service(
    *,
    socket_path: Path,
    lock_path: Path,
    service_name: str,
    stop_event: Event,
    handle: Callable[[dict[str, object]], LocalServiceResponse],
    on_idle: Callable[[], bool],
    on_client: Callable[[], None],
    on_start: Callable[[], None] | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> int:
    """Run one bounded local service socket until stopped or idle.

    ``handle`` owns typed service semantics.  The common listener owns only
    Unix-domain socket permissions, singleton locking, framing, response
    correlation, and cleanup.  Returning ``True`` from ``on_idle`` requests a
    bounded idle shutdown after the listener timeout.
    """
    previous_handlers = install_stop_signal_handlers(stop_event)
    requested_socket_path = socket_path
    socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{service_name}")
    socket_alias = requested_socket_path if requested_socket_path != socket_path else None
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(socket_path.parent, 0o700)
    except OSError:
        pass
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        owns_lock = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        owns_lock = True
        if owns_lock:
            try:
                socket_path.unlink()
            except FileNotFoundError:
                pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            old_umask = os.umask(0o177)
            try:
                server.bind(str(socket_path))
            finally:
                os.umask(old_umask)
            os.chmod(socket_path, 0o600)
            if socket_alias is not None:
                socket_alias.parent.mkdir(parents=True, exist_ok=True)
                try:
                    socket_alias.unlink()
                except FileNotFoundError:
                    pass
                socket_alias.symlink_to(socket_path)
            server.listen(16)
            server.settimeout(0.1)
            if on_start is not None:
                # Stateful initialization belongs after singleton ownership and
                # listener publication.  A losing contender must never open or
                # migrate the winner's database before discovering the lock.
                on_start()
            try:
                while not stop_event.is_set():
                    try:
                        connection, _address = server.accept()
                    except TimeoutError:
                        if on_idle():
                            stop_event.set()
                        continue
                    with connection:
                        connection.settimeout(LOCAL_SERVICE_CONNECTION_TIMEOUT_SECONDS)
                        uid = peer_uid(connection)
                        if uid is not None and uid != os.getuid():
                            write_message(connection, None, {"ok": False, "error": "peer uid mismatch"}, legacy=True)
                            continue
                        on_client()
                        try:
                            envelope, payload, _binary, legacy = read_message(connection)
                        except (LocalRpcError, OSError):
                            try:
                                write_message(connection, None, {"ok": False, "error": "invalid request"}, legacy=True)
                            except OSError:
                                pass
                        else:
                            response, response_binary = handle(payload)
                            response_envelope = None if legacy or envelope is None else LocalRpcEnvelope(
                                service=service_name,
                                method=envelope.method,
                                request_id=envelope.request_id,
                                trace_id=envelope.trace_id,
                                deadline_ms=envelope.deadline_ms,
                                priority=envelope.priority,
                                owner_generation=envelope.owner_generation,
                                config_generation=envelope.config_generation,
                                payload=response,
                            )
                            try:
                                write_message(connection, response_envelope, response, response_binary, legacy=legacy)
                            except (LocalRpcError, OSError):
                                try:
                                    write_message(connection, None, {"ok": False, "error": "response too large"}, legacy=True)
                                except OSError:
                                    pass
                    if on_idle():
                        stop_event.set()
            except KeyboardInterrupt:
                stop_event.set()
        finally:
            server.close()
    finally:
        if on_shutdown is not None:
            on_shutdown()
        if owns_lock:
            if socket_alias is not None:
                try:
                    socket_alias.unlink()
                except FileNotFoundError:
                    pass
            try:
                socket_path.unlink()
            except FileNotFoundError:
                pass
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(lock_fd)
        restore_signal_handlers(previous_handlers)
    return 0

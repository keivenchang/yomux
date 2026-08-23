"""One service-side Unix-RPC listener lifecycle for local YOLOmux services.

Clients use :mod:`registry` for discovery and spawn.  Services use this module
for the reciprocal lock/socket/accept lifecycle so stateful services do not
copy subtly different permissions, cleanup, or rolling-RPC behavior.
"""

from __future__ import annotations

import fcntl
import logging
import multiprocessing
import os
import signal
import socket
import struct
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from threading import Event

from ..background_owner import pid_is_alive
from ..host_identity import HostIdentity
from ..host_identity import LocalProcessReason
from ..host_identity import current_host_identity
from ..host_identity import is_current_local_process
from ..host_identity import process_start_identity
from ..infra.filesystem_preflight import preflight_mutable_roots
from .lifetime import LOCAL_SERVICE_SPAWN_GENERATION_ENV
from .lifetime import ServiceLifetimeOwner
from .rpc import LOCAL_SERVICE_ERROR_BUSY
from .rpc import LOCAL_SERVICE_ERROR_INVALID_REQUEST
from .rpc import LOCAL_SERVICE_ERROR_PEER_UID_MISMATCH
from .rpc import LOCAL_SERVICE_ERROR_RESPONSE_TOO_LARGE
from .rpc import LocalRpcEnvelope
from .rpc import LocalRpcError
from .rpc import read_message
from .rpc import safe_socket_path
from .rpc import write_message


LocalServiceResponse = tuple[dict[str, object], bytes]
SignalHandlers = list[tuple[int, signal.Handlers]]
LOCAL_SERVICE_CONNECTION_TIMEOUT_SECONDS = 0.5
LOCAL_SERVICE_MAX_CLIENT_LEASES = 64
# A serial listener charges every waiting client the full runtime of whichever handler is
# already running, and that wait is invisible on the wire: `accepted_at` is stamped after
# `accept()` returns, and `to_dict` omits the queue/capacity fields while `capacity_limit`
# is 0.  One shared limit for every daemon so no service can silently go back to serial.
LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT = 8
LOCAL_SERVICE_STACK_FRAME_LIMIT = 32
# The listener's kernel-proven peer verdict, published to the handler running on
# this thread. Deliberately NOT stamped into the request payload: several
# handlers echo their request back to the caller, and a private key smuggled
# through the payload would leak into those responses and change the wire.
# Handlers run on the same thread that accepted the connection (serially, or on
# the worker thread the listener started for that one connection), so the thread
# is the exact scope of one connection's verdict.
_CONNECTION_VERDICT = threading.local()
LOCAL_SERVICE_SECRET_MARKERS = ("token", "secret", "password", "cookie", "authorization", "api_key", "apikey", "bearer")
logger = logging.getLogger(__name__)


class LocalRpcServiceState:
    """Shared singleton-service lifecycle state; semantics stay with each daemon."""

    def __init__(self, socket_path: Path, *, prefix: str, idle_seconds: float):
        self.socket_path = safe_socket_path(socket_path, prefix=prefix)
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = multiprocessing.get_context("spawn").Event()
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.started_at = time.time()
        self.last_client_at = time.monotonic()
        self.leases: dict[str, dict[str, object]] = {}


def reap_dead_client_leases(
    leases: dict[str, object],
    *,
    host_identity: HostIdentity | None = None,
    start_identity_reader: Callable[[int], str | None] = process_start_identity,
    pid_probe: Callable[[int], bool] = pid_is_alive,
) -> int:
    """Discard only same-host/current-boot leases whose recorded process birth is stale."""

    identity = host_identity or current_host_identity()
    dead: list[str] = []
    for lease_id, value in leases.items():
        if not isinstance(value, dict):
            continue
        diagnostic = is_current_local_process(
            value,
            host_identity=identity,
            start_identity_reader=start_identity_reader,
            pid_probe=pid_probe,
        )
        if diagnostic.may_remove_stale_record:
            dead.append(lease_id)
    for lease_id in dead:
        leases.pop(lease_id, None)
    return len(dead)


def request_is_self_connection(_request: object = None) -> bool | None:
    """Return the listener's proven self-connection verdict for the current request.

    ``None`` is not "no": it means no transport proof reached this handler at all,
    which is what a direct in-process call looks like. The caller decides what to
    do with an absent proof; nothing here defaults it away.
    """

    return getattr(_CONNECTION_VERDICT, "value", None)


def live_client_claim(
    leases: dict[str, object],
    *,
    host_identity: HostIdentity | None = None,
    start_identity_reader: Callable[[int], str | None] = process_start_identity,
    pid_probe: Callable[[int], bool] = pid_is_alive,
) -> bool:
    """Return whether any lease still names a LIVE client, dropping the ones that do not.

    ``bool(self.leases)`` is not the same question.  A client that was hard-killed
    cannot release its lease, so the table keeps an entry naming a process that no
    longer exists and the daemon's idle deadline is refreshed forever by a ghost.
    ``statusd``, ``watchd`` and ``jobd`` each reaped before deciding; ``approvald``
    and ``indexd`` did not, so those two alone could be pinned indefinitely by one
    crashed caller.  Two spellings of one predicate is what produced that gap, so
    there is now one.
    """

    reap_dead_client_leases(
        leases,
        host_identity=host_identity,
        start_identity_reader=start_identity_reader,
        pid_probe=pid_probe,
    )
    return bool(leases)


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


def local_service_failure_text(registry_status: dict[str, object], payload: dict[str, object]) -> str:
    """Return the one reason a down local service can be explained by.

    Two sources carry it and neither alone is sufficient. A service that is UP but
    unhealthy reports its own trouble in the live status payload (``last_error`` or
    ``last_failure``). A service that never started has no payload at all -- its
    reason lives only in the registry's ``failure_reason``, which is what
    ``_record_blocked_start`` and ``_mark_failure`` write.

    Every ``runtime_status`` used to spell this itself, and three of the five
    dropped the registry half: a refused ``indexd`` start recorded a specific
    reason that the Local-services row then replaced with a generic sentence.
    """
    return str(
        payload.get("last_error")
        or payload.get("last_failure")
        or registry_status.get("failure_reason")
        or ""
    )


def local_service_exception_cause(error: BaseException) -> dict[str, object]:
    """Serialize one redacted exception type and traceback for RPC callers and supervisors."""
    frames = []
    for frame in traceback.extract_tb(error.__traceback__)[-LOCAL_SERVICE_STACK_FRAME_LIMIT:]:
        path = Path(frame.filename)
        parts = path.parts
        if "yolomux_lib" in parts:
            filename = str(Path(*parts[parts.index("yolomux_lib"):]))
        elif "tests" in parts:
            filename = str(Path(*parts[parts.index("tests"):]))
        else:
            filename = path.name
        frames.append({
            "file": redact_local_service_text(filename),
            "line": int(frame.lineno),
            "function": redact_local_service_text(frame.name),
        })
    return {
        "exception": {
            "type": type(error).__name__,
            "message": redact_local_service_text(error),
        },
        "frames": frames,
    }


def acquire_client_lease(
    leases: dict[str, object],
    client_pid: object,
    existing_lease_id: object = None,
    *,
    host_identity: HostIdentity | None = None,
    start_identity_reader: Callable[[int], str | None] = process_start_identity,
    pid_probe: Callable[[int], bool] = pid_is_alive,
    service_pid: int | None = None,
    self_connection: bool | None = None,
) -> dict[str, object]:
    """Bound the shared local-service lease table for every daemon.

    A lease is the claim that keeps a daemon alive, so a daemon must never be
    able to grant one to itself.  The self-connection exclusion was closed at the
    connection level (``run_local_rpc_service`` compares ``peer_pid`` to
    ``os.getpid()`` before calling ``on_client``) and left wide open here: this
    function trusted a caller-supplied ``client_pid`` verbatim, so one self-issued
    lease request pinned the daemon's idle deadline forever and no amount of
    correctness in ``claim_gated_idle_due`` could undo it.  ``service_pid``
    defaults to this process, which is the daemon in every production call.
    """
    try:
        pid = max(0, int(client_pid or 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid client pid", "leases": len(leases)}
    own_pid = os.getpid() if service_pid is None else int(service_pid)
    if self_connection is None:
        # No transport proof reached this handler, which is what a direct
        # in-process call looks like. The claimed pid is then the only evidence
        # there is, and a caller naming this very process is claiming to be its
        # own client.
        is_self = pid > 0 and pid == own_pid
    else:
        # The request arrived over a socket and the kernel answered who was on
        # the other end. Sharing a pid with the peer is NOT sufficient on its
        # own: a daemon object hosted inside its caller's process (a service run
        # on a thread) legitimately serves that caller over a real socket and
        # would share its pid with every request. Only a process that was itself
        # spawned as a local service -- proven by the spawn marker in its own
        # environment -- can be talking to itself.
        is_self = bool(self_connection) and bool(os.environ.get(LOCAL_SERVICE_SPAWN_GENERATION_ENV))
    if is_self:
        return {
            "ok": False,
            "error": "a service may not lease itself",
            "diagnostic": {"reason": "self_connection", "pid": pid},
            "leases": len(leases),
        }
    identity = host_identity or current_host_identity()
    reap_dead_client_leases(
        leases,
        host_identity=identity,
        start_identity_reader=start_identity_reader,
        pid_probe=pid_probe,
    )
    start_identity = start_identity_reader(pid) if pid > 1 else None
    if not start_identity:
        reason = LocalProcessReason.INVALID_PID
        if pid > 1:
            try:
                reason = LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE if pid_probe(pid) else LocalProcessReason.PROCESS_NOT_FOUND
            except ProcessLookupError:
                reason = LocalProcessReason.PROCESS_NOT_FOUND
            except (PermissionError, OSError):
                reason = LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE
        return {
            "ok": False,
            "error": "client process identity unavailable",
            "diagnostic": {"reason": reason.value, "pid": pid},
            "leases": len(leases),
        }
    record = identity.process_record_fields(pid=pid, start_identity=start_identity)
    lease_id = str(existing_lease_id or "")
    existing = leases.get(lease_id)
    if lease_id and isinstance(existing, dict) and int(existing.get("pid") or 0) == pid:
        diagnostic = is_current_local_process(
            existing,
            host_identity=identity,
            start_identity_reader=start_identity_reader,
            pid_probe=pid_probe,
        )
        if diagnostic.current:
            return {"ok": True, "lease_id": lease_id, "pid": os.getpid(), "leases": len(leases)}
    if len(leases) >= LOCAL_SERVICE_MAX_CLIENT_LEASES:
        return {"ok": False, "error": "too many clients", "leases": len(leases)}
    lease_id = f"{os.getpid()}-{time.time_ns()}-{len(leases)}"
    leases[lease_id] = record
    return {"ok": True, "lease_id": lease_id, "pid": os.getpid(), "leases": len(leases)}


def release_client_lease(leases: dict[str, object], lease_id: object) -> dict[str, object]:
    """Release a local-service lease without exposing table internals."""
    text = str(lease_id or "")
    if text:
        leases.pop(text, None)
    return {"ok": True, "leases": len(leases)}


def claim_gated_idle_due(state: object, has_claim: bool, *, now: Callable[[], float] = time.monotonic) -> bool:
    """Answer the one shared claim-gated idle-shutdown decision for a service.

    ``state`` must expose a mutable ``last_client_at`` and a numeric
    ``idle_seconds``.  Every local service's ``on_idle`` maintenance probe
    runs on every listener idle tick (see ``run_local_rpc_service``'s
    accept-timeout loop) regardless of RPC traffic, so this is the ONLY place
    that may move the idle deadline: pass the service's own freshly computed
    claim predicate (a lease, a descriptor, accepted work -- whatever that
    service defines as real external demand) on every call.  A claim present
    refreshes the deadline; a claim absent lets it age toward
    ``idle_seconds``.  A bare status/ping/snapshot RPC never changes the
    claim predicate, so it can never reach the refreshing branch here --
    unlike a connection-level ``on_client`` callback, which cannot tell a
    diagnostic probe from real demand and must always be wired to a no-op for
    any service that adopts this owner.  The claim predicate is the only
    thing that varies per service; the transition/deadline algorithm does
    not, and every service must route through this one function rather than
    reimplementing it.
    """
    if has_claim:
        state.last_client_at = now()
        return False
    return now() - state.last_client_at >= state.idle_seconds


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


def peer_pid(connection: socket.socket) -> int | None:
    """Return the Unix peer PID where the platform exposes ``SO_PEERCRED``."""
    if not hasattr(socket, "SO_PEERCRED"):
        return None
    try:
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, _uid, _gid = struct.unpack("3i", credentials)
    except OSError:
        return None
    return int(pid)


def run_local_rpc_service(
    *,
    socket_path: Path,
    lock_path: Path,
    service_name: str,
    stop_event: Event,
    handle: Callable[[dict[str, object], bytes], LocalServiceResponse],
    on_idle: Callable[[], bool],
    on_client: Callable[[], None],
    on_idle_failure: Callable[[Exception, str], None] | None = None,
    on_start: Callable[[], None] | None = None,
    on_shutdown: Callable[[], None] | None = None,
    concurrent_handlers: int = 0,
    parent_pid_reader: Callable[[], int] = os.getppid,
) -> int:
    """Run one bounded local service socket until stopped or idle.

    ``handle`` owns typed service semantics.  The common listener owns only
    Unix-domain socket permissions, singleton locking, framing, response
    correlation, and cleanup.  Returning ``True`` from ``on_idle`` requests a
    bounded idle shutdown after the listener timeout. ``concurrent_handlers``
    is opt-in for services whose handler contract is explicitly lock-safe.
    Leaving it at 0 is not free: a serial listener charges every waiting client
    the full runtime of whichever handler is already running, and no field on
    the wire can express that wait, so the caller can only report it as
    unattributed latency.
    """
    previous_handlers = install_stop_signal_handlers(stop_event)
    launching_parent_pid = max(0, int(parent_pid_reader()))
    requested_socket_path = socket_path
    socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{service_name}")
    socket_alias = requested_socket_path if requested_socket_path != socket_path else None
    # ONE lifetime owner for every local service, built here rather than in each
    # daemon so no service can go back to graceful-only. The launching
    # supervisor's identity is captured now, while that process is still
    # provable; after it exits, `getppid()` would name init and prove nothing.
    lifetime_owner = ServiceLifetimeOwner.for_launching_parent(
        service_name,
        stop_event,
        parent_pid=launching_parent_pid,
    )
    lifetime_path = socket_path.with_suffix(".lifetime.json")
    preflight_mutable_roots(unix_sockets=[socket_path])
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
            # Published beside the socket, not behind the status RPC: the moment
            # "who is keeping this daemon alive" matters most is when the daemon
            # is wedged and cannot answer an RPC at all.
            publish_failure = lifetime_owner.publish(lifetime_path)
            if publish_failure:
                logger.warning(
                    "local service %s could not publish its lifetime record at %s: %s",
                    service_name,
                    lifetime_path,
                    publish_failure,
                )
            if on_start is not None:
                # Stateful initialization belongs after singleton ownership and
                # listener publication.  A losing contender must never open or
                # migrate the winner's database before discovering the lock.
                on_start()
            handler_limit = max(0, int(concurrent_handlers))
            handler_slots = threading.BoundedSemaphore(handler_limit) if handler_limit else None
            handler_threads: set[threading.Thread] = set()
            handler_threads_lock = threading.Lock()
            capacity_lock = threading.Lock()
            active_handlers = 0
            capacity_rejections = 0

            def serve_connection(connection: socket.socket, accepted_at: float, queue_wait_ms: float, capacity_saturated: bool, rejection_count: int) -> None:
                nonlocal active_handlers
                try:
                    with connection:
                        connection.settimeout(LOCAL_SERVICE_CONNECTION_TIMEOUT_SECONDS)
                        uid = peer_uid(connection)
                        if uid is not None and uid != os.getuid():
                            write_message(connection, None, {"ok": False, "error": LOCAL_SERVICE_ERROR_PEER_UID_MISMATCH}, legacy=True)
                            return
                        pid = peer_pid(connection)
                        self_connected = pid is not None and pid == os.getpid()
                        if not self_connected:
                            on_client()
                        try:
                            read_started = time.monotonic()
                            envelope, payload, request_binary, legacy = read_message(connection)
                            read_completed = time.monotonic()
                        except (LocalRpcError, OSError):
                            try:
                                write_message(connection, None, {"ok": False, "error": LOCAL_SERVICE_ERROR_INVALID_REQUEST}, legacy=True)
                            except OSError:
                                pass
                        else:
                            service_started = time.monotonic()
                            # Publish the proven verdict for this one connection. A
                            # lease is the claim that keeps a daemon alive, so the
                            # only place that can prove "this is me talking to
                            # myself" has to be the place that says so.
                            _CONNECTION_VERDICT.value = self_connected
                            try:
                                response, response_binary = handle(payload, request_binary)
                            except Exception as exc:
                                # A request failure is data-plane state. Letting it escape the
                                # serial listener kills the daemon, turns one bad path into a
                                # socket-retry loop, and hides the typed refusal from its caller.
                                logger.exception("local service %s handler failed", service_name)
                                response, response_binary = {
                                    "ok": False,
                                    "error": "service request failed",
                                    "error_code": "handler_failed",
                                    "exception_type": type(exc).__name__,
                                }, b""
                            finally:
                                _CONNECTION_VERDICT.value = None
                            with capacity_lock:
                                response_rejection_count = capacity_rejections
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
                                accept_to_read_ms=(read_started - accepted_at) * 1000.0,
                                read_complete_ms=(read_completed - read_started) * 1000.0,
                                service_duration_ms=(time.monotonic() - service_started) * 1000.0,
                                queue_wait_ms=queue_wait_ms,
                                queue_depth=0,
                                capacity_limit=handler_limit,
                                capacity_saturated=capacity_saturated,
                                capacity_rejections=response_rejection_count,
                            )
                            try:
                                write_message(connection, response_envelope, response, response_binary, legacy=legacy)
                            except (LocalRpcError, OSError):
                                try:
                                    write_message(connection, None, {"ok": False, "error": LOCAL_SERVICE_ERROR_RESPONSE_TOO_LARGE}, legacy=True)
                                except OSError:
                                    pass
                finally:
                    if handler_slots is not None:
                        handler_slots.release()
                        with capacity_lock:
                            active_handlers -= 1
                    with handler_threads_lock:
                        handler_threads.discard(threading.current_thread())

            try:
                while not stop_event.is_set():
                    if launching_parent_pid > 1 and int(parent_pid_reader()) != launching_parent_pid:
                        lifetime_owner.request_retirement("launching_supervisor_exited")
                        lifetime_owner.publish(lifetime_path)
                        continue
                    try:
                        connection, _address = server.accept()
                    except TimeoutError:
                        if handler_slots is not None:
                            with capacity_lock:
                                if active_handlers:
                                    continue
                        try:
                            if on_idle():
                                # The last valid external claim is gone. Setting
                                # `stop_event` is a REQUEST the listener may never
                                # honour -- a stuck handler, a blocking shutdown
                                # hook, or a non-daemon thread at interpreter exit
                                # all leave the daemon up. Waiting for the next
                                # launcher start to force it is the future-restart
                                # authority the supervision contract forbids, so
                                # the owner bounds this exit itself.
                                lifetime_owner.request_retirement("idle_no_external_claim")
                                lifetime_owner.publish(lifetime_path)
                        except Exception as exc:
                            traceback_text = traceback.format_exc()
                            if on_idle_failure is not None:
                                try:
                                    on_idle_failure(exc, traceback_text)
                                except Exception:
                                    logger.exception("local service %s idle-failure callback failed", service_name)
                            else:
                                logger.exception("local service %s idle hook failed", service_name)
                        continue
                    accepted_at = time.monotonic()
                    if handler_slots is None:
                        serve_connection(connection, accepted_at, 0.0, False, 0)
                    else:
                        queue_started = time.monotonic()
                        acquired = handler_slots.acquire(blocking=False)
                        queue_wait_ms = (time.monotonic() - queue_started) * 1000.0
                        with capacity_lock:
                            if acquired:
                                active_handlers += 1
                                saturated = active_handlers >= handler_limit
                                rejection_count = capacity_rejections
                            else:
                                capacity_rejections += 1
                                saturated = True
                                rejection_count = capacity_rejections
                    if handler_slots is not None and not acquired:
                        with connection:
                            try:
                                write_message(connection, None, {"ok": False, "error": LOCAL_SERVICE_ERROR_BUSY, "queue_wait_ms": queue_wait_ms, "queue_depth": 0, "capacity_limit": handler_limit, "capacity_saturated": saturated, "capacity_rejected": True, "capacity_rejections": rejection_count}, legacy=True)
                            except OSError:
                                pass
                    elif handler_slots is not None:
                        worker = threading.Thread(target=serve_connection, args=(connection, accepted_at, queue_wait_ms, saturated, rejection_count), name=f"{service_name}-rpc", daemon=True)
                        with handler_threads_lock:
                            handler_threads.add(worker)
                        worker.start()
                    # A completed request may have more clients queued behind it.
                    # Maintenance runs only after an actual accept timeout so
                    # background work cannot jump ahead of interactive RPCs.
            except KeyboardInterrupt:
                stop_event.set()
            finally:
                with handler_threads_lock:
                    workers = tuple(handler_threads)
                for worker in workers:
                    worker.join(timeout=0.1)
        finally:
            server.close()
    finally:
        if on_shutdown is not None:
            on_shutdown()
        # The listener completed on its own, so the armed escalation stands down
        # before it can signal a process that is already leaving.
        lifetime_owner.note_exited_gracefully()
        if owns_lock:
            try:
                lifetime_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                logger.warning(
                    "local service %s could not remove its lifetime record at %s: %s",
                    service_name,
                    lifetime_path,
                    type(error).__name__,
                )
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

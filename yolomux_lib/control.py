from __future__ import annotations

import itertools
import json
import logging
import os
import re
import socket
import stat
import threading
from pathlib import Path
from typing import Any
from typing import Callable

from .atomic_file import atomic_write_text
from .filesystem.io_ops import read_json_file
from .host_identity import HostIdentity
from .host_identity import current_host_identity
from .host_identity import is_current_local_process
from .infra.common import CONTROL_SOCKET_DIR
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
CONTROL_OWNER_SUFFIX = ".owner.json"
# Distinguishes two control servers that legitimately coexist in one process
# (two `TmuxWebtermApp` instances electing a background owner between them).
_CONTROL_SERVER_SEQUENCE = itertools.count(1)
LOGGER = logging.getLogger(__name__)

# Every control socket this process currently intends to serve, registered at
# construction rather than at bind time. A sibling server that has been created
# but has not finished `start()` yet owns no owner record, so without this set
# the predecessor scan below could see its not-yet-published socket as an
# unclaimed same-pid leftover and delete a path that is about to go live.
_LIVE_CONTROL_SOCKET_PATHS: set[str] = set()
_LIVE_CONTROL_SOCKET_LOCK = threading.Lock()


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


def control_socket_owner_path(socket_path: Path) -> Path:
    """Return the identity record that proves who owns one control socket."""

    return Path(str(socket_path) + CONTROL_OWNER_SUFFIX)


def reclaim_stale_control_sockets(
    directory: Path = CONTROL_SOCKET_DIR,
    *,
    host_identity: HostIdentity | None = None,
) -> list[dict[str, Any]]:
    """Remove only control sockets whose owner is provably gone, and report the rest.

    Without this the control directory grew without bound.  Every socket used to
    be named ``yolomux-<pid>-<id(self):x>.sock``, where ``id(self)`` is a CPython
    memory address: it identifies nothing outside the running interpreter, it is
    not reproducible by any later process, and it is not even unique over time
    within one process, since a freed object's address is reused.  A server that
    was hard-killed therefore left a socket no successor could name, and a
    successor that happened to reuse the same PID bound a DIFFERENT filename, so
    the predecessor's file was never the one being unlinked.

    Sockets now carry an owner record with full host, boot, PID, and
    process-start identity, so removal is a proof rather than a guess.  Anything
    that cannot be proven dead -- a foreign host, a previous boot with no proof,
    an unreadable record, an orphan socket with no record at all -- is reported
    and left alone.
    """

    identity = host_identity or current_host_identity()
    rows: list[dict[str, Any]] = []
    try:
        owner_paths = sorted(Path(directory).glob(f"*{CONTROL_OWNER_SUFFIX}"))
    except OSError as error:
        return [{"path": str(directory), "action": "none", "result": "reported_only", "reason": type(error).__name__}]
    for owner_path in owner_paths:
        socket_path = Path(str(owner_path)[: -len(CONTROL_OWNER_SUFFIX)])
        record = read_json_file(owner_path, None)
        if not isinstance(record, dict):
            rows.append({"path": str(socket_path), "action": "none", "result": "reported_only", "reason": "unreadable_owner_record"})
            continue
        diagnostic = is_current_local_process(record, host_identity=identity)
        if diagnostic.current:
            rows.append({"path": str(socket_path), "action": "none", "result": "reported_only", "reason": "owner_alive", "surviving_supervisor": diagnostic.as_dict()})
            continue
        if not diagnostic.may_remove_stale_record:
            rows.append({"path": str(socket_path), "action": "none", "result": "reported_only", "reason": diagnostic.reason.value})
            continue
        removed: list[str] = []
        failure = ""
        for path in (socket_path, owner_path):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                failure = type(error).__name__
                break
            removed.append(str(path))
        rows.append({
            "path": str(socket_path),
            "action": "unlink",
            "result": "unlink_failed" if failure else "removed",
            "reason": diagnostic.reason.value,
            "removed": removed,
            **({"error": failure} if failure else {}),
        })
    return rows


def live_control_socket_paths() -> frozenset[str]:
    """Control-socket paths that a server object in THIS process still intends to serve."""

    with _LIVE_CONTROL_SOCKET_LOCK:
        return frozenset(_LIVE_CONTROL_SOCKET_PATHS)


def register_live_control_socket(path: Path) -> None:
    with _LIVE_CONTROL_SOCKET_LOCK:
        _LIVE_CONTROL_SOCKET_PATHS.add(str(path))


def forget_live_control_socket(path: Path) -> None:
    with _LIVE_CONTROL_SOCKET_LOCK:
        _LIVE_CONTROL_SOCKET_PATHS.discard(str(path))


def own_control_socket_name_pattern(pid: int) -> re.Pattern[str]:
    """Match only `yolomux-<this pid>-<token>.sock`, the name this process itself mints.

    The pid is the proof of ownership that does not need a record: a pid is
    unique among LIVE processes, so a control socket carrying this pid was
    written either by this process or by a dead predecessor that held the pid
    before it. No third party can be the live owner of that name. Everything
    else -- another pid, a missing token, a name that is not a control socket at
    all -- fails the match and is never a removal candidate.
    """

    return re.compile(rf"^yolomux-{int(pid)}-[A-Za-z0-9][A-Za-z0-9._-]*\.sock$")


def reclaim_own_predecessor_control_sockets(
    directory: Path = CONTROL_SOCKET_DIR,
    *,
    host_identity: HostIdentity | None = None,
) -> list[dict[str, Any]]:
    """Remove this process's OWN stale predecessor control sockets, and nothing else.

    `reclaim_stale_control_sockets` can only adjudicate sockets that carry an
    owner record. A server that was hard-killed before it published one -- or
    that ran on an older build with a different token scheme -- leaves a bare
    `yolomux-<pid>-<token>.sock` that nobody ever enumerates, so it survives for
    the lifetime of the machine. Fixing the NAMING did not fix that: a
    predecessor holding this same pid under a different token is still a file no
    successor ever looks at.

    Every removal here is a proof, not a guess, and each proof is required:
      * the filename must match this exact pid's control-socket grammar, so
        another process's socket and unrelated files are never candidates;
      * no server object in this process may still claim the path;
      * a socket that has an owner record is left to the record-based pass above
        -- if it survived that pass, its owner was NOT proven dead;
      * the entry must still stat as a socket or plain file.
    Anything unreadable, unstattable, or otherwise unprovable is reported and
    left on disk. A failed unlink is reported too, never dropped.
    """

    identity = host_identity or current_host_identity()
    pattern = own_control_socket_name_pattern(identity.pid)
    rows: list[dict[str, Any]] = []
    try:
        entries = sorted(Path(directory).iterdir())
    except FileNotFoundError:
        return rows
    except OSError as error:
        return [{"path": str(directory), "action": "none", "result": "reported_only", "reason": f"unreadable_control_directory:{type(error).__name__}"}]
    live = live_control_socket_paths()
    for path in entries:
        if not pattern.match(path.name):
            continue
        if str(path) in live:
            rows.append({"path": str(path), "action": "none", "result": "reported_only", "reason": "live_control_socket_in_this_process"})
            continue
        owner_path = control_socket_owner_path(path)
        # `Path.exists()` swallows OSError and answers False, which would turn a
        # permission error into "no record, safe to delete" -- the exact
        # fail-OPEN this pass must not do. lstat keeps the three outcomes apart.
        try:
            owner_path.lstat()
        except FileNotFoundError:
            has_owner_record = False
        except OSError as error:
            rows.append({"path": str(path), "action": "none", "result": "reported_only", "reason": f"unreadable_owner_record:{type(error).__name__}"})
            continue
        else:
            has_owner_record = True
        if has_owner_record:
            rows.append({"path": str(path), "action": "none", "result": "reported_only", "reason": "owner_record_not_proven_stale"})
            continue
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            rows.append({"path": str(path), "action": "none", "result": "reported_only", "reason": f"unstattable:{type(error).__name__}"})
            continue
        if not (stat.S_ISSOCK(mode) or stat.S_ISREG(mode)):
            rows.append({"path": str(path), "action": "none", "result": "reported_only", "reason": "not_a_socket_or_regular_file"})
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            LOGGER.warning("yolomux predecessor control socket %s could not be reclaimed: %s", path, type(error).__name__)
            rows.append({"path": str(path), "action": "unlink", "result": "unlink_failed", "reason": "own_pid_stale_predecessor_socket", "removed": [], "error": type(error).__name__})
            continue
        rows.append({"path": str(path), "action": "unlink", "result": "removed", "reason": "own_pid_stale_predecessor_socket", "removed": [str(path)]})
    return rows


class YolomuxControlServer:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]], *, host_identity: HostIdentity | None = None):
        self.handler = handler
        self.host_identity = host_identity or current_host_identity()
        # The token is this process's durable instance nonce plus a monotone
        # per-process sequence, NOT `id(self)`. A CPython object address carries
        # no identity: it is meaningless to any other process, and it is reused
        # after the object is freed, so two different servers in one process
        # could be handed the same "unique" token. The nonce changes with every
        # process, so a same-PID successor never collides with a predecessor's
        # leftover socket, and the sequence keeps two live servers in one process
        # apart. Reclaiming a predecessor's file is never the filename's job:
        # `reclaim_stale_control_sockets` proves it from the owner record, and
        # `reclaim_own_predecessor_control_sockets` proves it from this pid for
        # the record-less leftovers that the first pass cannot see at all.
        self.path = control_socket_path(
            token=f"{self.host_identity.instance_nonce[:12]}-{next(_CONTROL_SERVER_SEQUENCE)}",
            pid=self.host_identity.pid,
        )
        self.owner_path = control_socket_owner_path(self.path)
        # Claimed here, not in `start()`: a sibling server that is still being
        # constructed must already be protected from this process's own
        # predecessor scan.
        register_live_control_socket(self.path)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="yolomux-control", daemon=True)
        self.socket: socket.socket | None = None
        self.reclaimed: list[dict[str, Any]] = []

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.reclaimed = reclaim_stale_control_sockets(self.path.parent, host_identity=self.host_identity)
        # The record-based pass above can only judge sockets that HAVE a record.
        # Bare same-pid leftovers are this process's own predecessors and nobody
        # else's, so this second pass is what stops them leaking forever.
        #
        # Both directories are scanned because `safe_socket_path` relocates a
        # too-long socket path into a per-name server-owned `/tmp/yolomux-server-*/` fallback: when that
        # happens `self.path.parent` is NOT the control directory, and a
        # predecessor's leftover sits in the control directory that this server
        # would otherwise never look at.
        for scan_directory in dict.fromkeys((str(CONTROL_SOCKET_DIR), str(self.path.parent))):
            self.reclaimed += reclaim_own_predecessor_control_sockets(Path(scan_directory), host_identity=self.host_identity)
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
        # Published only once the socket exists, so a record can never advertise
        # an owner for a socket that was never bound.
        atomic_write_text(
            self.owner_path,
            json.dumps(self.host_identity.process_record_fields(), sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )
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
        for path in (self.path, self.owner_path):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                LOGGER.warning("yolomux control cleanup failed for %s: %s", path, type(error).__name__)
        # Released last: until this point a concurrent `start()` in this process
        # must still treat the path as live, even if the unlink above failed.
        forget_live_control_socket(self.path)

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

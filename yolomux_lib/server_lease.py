"""Cross-process ownership lease for one YOLOmux TCP port."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .infra.common import RUNTIME_DIR
from .infra.common import ensure_runtime_root
from .infra.host_identity import HostIdentity
from .infra.host_identity import current_host_identity
from .infra.host_identity import is_current_local_process
from .infra.root_paths import YolomuxRoots


INSTANCE_FORCE_GRACE_SECONDS = 5.0
INSTANCE_FORCE_POLL_SECONDS = 0.05
INSTANCE_PATH_KEYS = ("config", "state", "cache", "runtime")


class InstanceLeaseError(RuntimeError):
    """A forced replacement could not safely terminate the verified instance."""


@dataclass
class ServerPortLease:
    """An advisory lock held for the lifetime of a server process."""

    port: int
    path: Path
    fd: int

    def release(self) -> None:
        if self.fd < 0:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = -1


def _read_unlocked_instance_record(path: Path) -> dict | None:
    try:
        payload = path.read_text(encoding="utf-8")
        record = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _verified_instance_owner(record: dict | None, identity: HostIdentity) -> bool:
    if not isinstance(record, dict):
        return False
    return is_current_local_process(record, host_identity=identity).current


def _same_process_record(left: dict, right: dict) -> bool:
    return all(
        str(left.get(field) or "") == str(right.get(field) or "")
        for field in ("stable_host_id", "boot_id", "pid", "process_start_identity", "instance_nonce")
    )


def _signal_verified_instance_owner(record: dict, identity: HostIdentity, signum: int) -> None:
    if not _verified_instance_owner(record, identity):
        raise RuntimeError("cannot force-start: existing instance identity is no longer verifiable")
    pid = _record_pid(record)
    if pid <= 1 or pid == os.getpid():
        raise RuntimeError("cannot force-start: existing instance record names the current process")
    pgid = 0
    try:
        pgid = int(record.get("pgid") or 0)
    except (TypeError, ValueError):
        pgid = 0
    if pgid <= 1:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = 0
    # A process group is useful for a setsid-launched server and its children,
    # but never signal a caller's shared group. Missing pgid data falls back to
    # the identity-fenced server PID.
    if pgid > 1 and pgid == pid and pgid != os.getpgrp():
        try:
            os.killpg(pgid, signum)
            return
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        return


def _try_lock_instance_path(path: Path) -> int | None:
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _force_stop_instance_owner(
    blocked_path: Path,
    identity: HostIdentity,
) -> int:
    record = _read_unlocked_instance_record(blocked_path)
    if not _verified_instance_owner(record, identity):
        raise RuntimeError(
            "cannot force-start: another instance owns the product root, but its host/boot/PID identity cannot be verified"
        )
    assert record is not None

    for signum in (signal.SIGTERM, signal.SIGKILL):
        _signal_verified_instance_owner(record, identity, signum)
        deadline = time.monotonic() + INSTANCE_FORCE_GRACE_SECONDS
        while time.monotonic() < deadline:
            fd = _try_lock_instance_path(blocked_path)
            if fd is not None:
                current = _read_unlocked_instance_record(blocked_path)
                if current is not None and not _same_process_record(current, record):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    raise RuntimeError("cannot force-start: a different instance acquired the product-root lease")
                return fd
            time.sleep(INSTANCE_FORCE_POLL_SECONDS)
        record = _read_unlocked_instance_record(blocked_path)
        if not _verified_instance_owner(record, identity):
            raise RuntimeError("cannot force-start: existing instance changed identity before force termination")
        assert record is not None
    raise RuntimeError("cannot force-start: existing instance did not release the product-root lease")


def _instance_path_tuple(paths_or_root: YolomuxRoots | Path) -> tuple[Path, ...]:
    if isinstance(paths_or_root, YolomuxRoots):
        return tuple(Path(path).expanduser().resolve(strict=False) for path in paths_or_root.writable_paths())
    root = Path(paths_or_root).expanduser().resolve(strict=False)
    return (root,)


def _instance_lock_path(paths: tuple[Path, ...]) -> Path:
    if len(paths) == 1:
        return paths[0] / "instance.lock"
    identity = hashlib.sha256("\0".join(str(path) for path in paths).encode("utf-8")).hexdigest()[:32]
    return paths[-1] / "instance-locks" / identity / "instance.lock"


def _path_record(paths: tuple[Path, ...]) -> dict[str, str]:
    return (
        {"root": str(paths[0])}
        if len(paths) == 1
        else dict(zip(INSTANCE_PATH_KEYS, (str(path) for path in paths), strict=True))
    )


def acquire_instance_root_lease(paths_or_root: YolomuxRoots | Path, *, force: bool = False) -> ServerPortLease | None:
    """Claim one complete YOLOmux path tuple, independent of its HTTP port.

    IDX, STATS, and SESS are process-local services. Sharing their root between
    two servers creates split-brain state, so a root is exclusive even when the
    servers chose different listener ports.
    """

    paths = _instance_path_tuple(paths_or_root)
    path = _instance_lock_path(paths)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = current_host_identity()
    fd = _try_lock_instance_path(path)
    if fd is None:
        if not force:
            return None
        try:
            fd = _force_stop_instance_owner(path, identity)
        except RuntimeError as error:
            raise InstanceLeaseError(str(error)) from error
    path_record = _path_record(paths)
    payload = json.dumps(
        {
            **identity.process_record_fields(),
            "pgid": os.getpgid(0),
            "paths": path_record,
        },
        sort_keys=True,
    ) + "\n"
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload.encode("utf-8"))
    os.fsync(fd)
    return ServerPortLease(port=0, path=path, fd=fd)


def _record_pid(record: dict) -> int:
    try:
        return int(record.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def acquire_server_port_lease(
    port: int,
    state_dir: Path = RUNTIME_DIR,
    *,
    host_identity: HostIdentity | None = None,
) -> ServerPortLease | None:
    """Claim ``port`` without relying on a racy listener probe.

    The lock survives detached launchers and is released by the kernel if the
    owning server dies.  It deliberately covers setup before ``bind()`` so a
    losing concurrent launch cannot start control/background services.
    """
    clean_port = int(port)
    identity = host_identity or current_host_identity()
    state_dir = ensure_runtime_root(state_dir)
    lease_dir = state_dir / "server-leases" / identity.stable_host_id
    lease_dir.mkdir(parents=True, exist_ok=True)
    try:
        lease_dir.chmod(0o700)
    except OSError:
        pass
    path = lease_dir / f"{clean_port}.lock"
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    payload = json.dumps(
        {
            **identity.process_record_fields(),
            "pgid": os.getpgid(0),
            "port": clean_port,
        },
        sort_keys=True,
    ) + "\n"
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload.encode("utf-8"))
    os.fsync(fd)
    return ServerPortLease(port=clean_port, path=path, fd=fd)


def _port_record_path(port: int, state_dir: Path, identity: HostIdentity) -> Path:
    return Path(state_dir) / "server-leases" / identity.stable_host_id / f"{int(port)}.lock"


def _read_port_owner_record(port: int, state_dir: Path, identity: HostIdentity) -> dict | None:
    return _read_unlocked_instance_record(_port_record_path(port, state_dir, identity))


def acquire_instance_and_port_leases(
    paths_or_root: YolomuxRoots | Path,
    port: int,
    *,
    force: bool = False,
) -> tuple[ServerPortLease | None, ServerPortLease | None]:
    """Claim the product root and requested port."""

    identity = current_host_identity()
    state_dir = paths_or_root.runtime_dir if isinstance(paths_or_root, YolomuxRoots) else RUNTIME_DIR
    if force:
        root_paths = _instance_path_tuple(paths_or_root)
        root_record = _read_unlocked_instance_record(_instance_lock_path(root_paths))
        port_record = _read_port_owner_record(port, state_dir, identity)
        if (
            _verified_instance_owner(root_record, identity)
            and _verified_instance_owner(port_record, identity)
            and not _same_process_record(root_record, port_record)
        ):
            raise InstanceLeaseError(
                f"cannot force-start: requested port {int(port)} is owned by a different YOLOmux instance"
            )
    root_lease = acquire_instance_root_lease(paths_or_root, force=force)
    if root_lease is None:
        return None, None
    port_lease = acquire_server_port_lease(port, state_dir=state_dir, host_identity=identity)
    if port_lease is None:
        root_lease.release()
        return root_lease, None
    return root_lease, port_lease

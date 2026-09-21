"""Cross-process ownership lease for one YOLOmux TCP port."""

from __future__ import annotations

import fcntl
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
    extra_fds: tuple[int, ...] = ()

    def release(self) -> None:
        fds = tuple(fd for fd in (self.fd, *self.extra_fds) if fd >= 0)
        self.fd = -1
        self.extra_fds = ()
        for fd in fds:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


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
    blocked_paths: tuple[Path, ...],
    expected_record: dict,
    identity: HostIdentity,
) -> dict[Path, int]:
    blocked_path = blocked_paths[0]
    record = _read_unlocked_instance_record(blocked_path)
    if not _same_process_record(record or {}, expected_record):
        raise RuntimeError("cannot force-start: existing instance changed identity before force termination")
    if not _verified_instance_owner(record, identity):
        raise RuntimeError(
            "cannot force-start: another instance owns the product root, but its host/boot/PID identity cannot be verified"
        )
    assert record is not None

    acquired: dict[Path, int] = {}

    def owner_exited() -> bool:
        # The old process may release its file locks from a signal handler before it has
        # actually stopped.  Do not let the replacement run beside that still-verified process;
        # the process-start identity is the fence, not merely reacquisition of the lock files.
        return not _verified_instance_owner(expected_record, identity)

    def wait_for_owner_exit(deadline: float) -> bool:
        while time.monotonic() < deadline:
            if owner_exited():
                return True
            time.sleep(INSTANCE_FORCE_POLL_SECONDS)
        return owner_exited()

    try:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            current = _read_unlocked_instance_record(blocked_path)
            if not _same_process_record(current or {}, expected_record) or not _verified_instance_owner(current, identity):
                raise RuntimeError("cannot force-start: existing instance changed identity before force termination")
            assert current is not None
            _signal_verified_instance_owner(current, identity, signum)
            deadline = time.monotonic() + INSTANCE_FORCE_GRACE_SECONDS
            while time.monotonic() < deadline:
                for path in blocked_paths:
                    if path in acquired:
                        continue
                    fd = _try_lock_instance_path(path)
                    if fd is None:
                        continue
                    current = _read_unlocked_instance_record(path)
                    if current is not None and not _same_process_record(current, expected_record):
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)
                        raise RuntimeError("cannot force-start: a different instance acquired the product-root lease")
                    acquired[path] = fd
                if len(acquired) == len(blocked_paths):
                    if wait_for_owner_exit(deadline):
                        return acquired
                    # The process released every lock but did not exit.  Continue to the
                    # SIGKILL pass before allowing the replacement to proceed.
                    break
                time.sleep(INSTANCE_FORCE_POLL_SECONDS)
            if owner_exited():
                return acquired
            current = _read_unlocked_instance_record(blocked_path)
            if not _same_process_record(current or {}, expected_record) or not _verified_instance_owner(current, identity):
                raise RuntimeError("cannot force-start: existing instance changed identity before force termination")
        raise RuntimeError("cannot force-start: existing instance did not release every product-path lease")
    except BaseException:
        _release_fds(list(acquired.values()))
        raise


def _preflight_force_stop_owners(
    blocked: list[tuple[Path, dict]],
    identity: HostIdentity,
) -> list[tuple[dict, list[Path]]]:
    """Revalidate every blocked path before signaling any existing instance."""

    owners: list[tuple[dict, list[Path]]] = []
    for path, expected_record in blocked:
        current = _read_unlocked_instance_record(path)
        if not _same_process_record(current or {}, expected_record):
            raise InstanceLeaseError("cannot force-start: existing instance changed identity before force termination")
        if not _verified_instance_owner(current, identity):
            raise InstanceLeaseError(
                "cannot force-start: another instance owns a product path, but its host/boot/PID identity cannot be verified"
            )
        assert current is not None
        for owner_record, owner_paths in owners:
            if _same_process_record(owner_record, current):
                owner_paths.append(path)
                break
        else:
            owners.append((current, [path]))
    return owners


def _instance_path_tuple(paths_or_root: YolomuxRoots | Path) -> tuple[Path, ...]:
    if isinstance(paths_or_root, YolomuxRoots):
        return tuple(Path(path).expanduser().resolve(strict=False) for path in paths_or_root.writable_paths())
    root = Path(paths_or_root).expanduser().resolve(strict=False)
    return (root,)


def _instance_lock_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return one exclusive lock for every mutable path."""

    return tuple(sorted({path / "instance.lock" for path in paths}, key=str))


def _release_fds(fds: list[int]) -> None:
    for fd in fds:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_instance_record(fds: list[int], payload: bytes) -> None:
    for fd in fds:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.fsync(fd)


def _acquire_instance_locks(
    lock_paths: tuple[Path, ...],
    *,
    force: bool,
    identity: HostIdentity,
) -> list[tuple[Path, int]] | None:
    acquired: list[tuple[Path, int]] = []
    blocked: list[tuple[Path, dict]] = []
    try:
        for path in lock_paths:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = _try_lock_instance_path(path)
            if fd is None:
                if not force:
                    _release_fds([locked_fd for _locked_path, locked_fd in acquired])
                    return None
                record = _read_unlocked_instance_record(path)
                if not _verified_instance_owner(record, identity):
                    raise InstanceLeaseError(
                        "cannot force-start: another instance owns a product path, but its host/boot/PID identity cannot be verified"
                    )
                assert record is not None
                blocked.append((path, record))
                continue
            acquired.append((path, fd))

        if blocked:
            if not force:
                _release_fds([locked_fd for _locked_path, locked_fd in acquired])
                return None
            # Every blocker is re-read and verified before any owner is signaled. A later
            # shared path may belong to a different instance; force must not partially replace
            # the request before discovering that conflict.
            owners = _preflight_force_stop_owners(blocked, identity)
            for record, owner_paths in owners:
                try:
                    owner_fds = _force_stop_instance_owner(tuple(owner_paths), record, identity)
                except RuntimeError as error:
                    raise InstanceLeaseError(str(error)) from error
                acquired.extend(owner_fds.items())
    except BaseException:
        _release_fds([locked_fd for _locked_path, locked_fd in acquired])
        raise
    return acquired


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
    identity = current_host_identity()
    lock_paths = _instance_lock_paths(paths)
    acquired = _acquire_instance_locks(lock_paths, force=force, identity=identity)
    if acquired is None:
        return None
    path, fd = acquired[0]
    all_fds = [locked_fd for _locked_path, locked_fd in acquired]
    path_record = _path_record(paths)
    payload = (json.dumps(
        {
            **identity.process_record_fields(),
            "pgid": os.getpgid(0),
            "paths": path_record,
        },
        sort_keys=True,
    ) + "\n").encode("utf-8")
    try:
        _write_instance_record(all_fds, payload)
    except BaseException:
        _release_fds(all_fds)
        raise
    return ServerPortLease(port=0, path=path, fd=fd, extra_fds=tuple(all_fds[1:]))


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


def acquire_instance_and_port_leases(
    paths_or_root: YolomuxRoots | Path,
    port: int,
    *,
    force: bool = False,
) -> tuple[ServerPortLease | None, ServerPortLease | None]:
    """Claim the complete product-root and listener lease as one lock transaction."""

    identity = current_host_identity()
    state_dir = paths_or_root.runtime_dir if isinstance(paths_or_root, YolomuxRoots) else RUNTIME_DIR
    root_paths = _instance_path_tuple(paths_or_root)
    root_lock_paths = _instance_lock_paths(root_paths)
    port_path = _port_record_path(port, state_dir, identity)
    if force:
        port_record = _read_unlocked_instance_record(port_path)
        root_records = [
            record
            for path in root_lock_paths
            if (record := _read_unlocked_instance_record(path)) is not None
        ]
        if port_record is not None:
            if not _verified_instance_owner(port_record, identity):
                raise InstanceLeaseError(
                    f"cannot force-start: requested port {int(port)} is owned by an instance whose identity cannot be verified"
                )
            if not any(
                _verified_instance_owner(root_record, identity)
                and _same_process_record(root_record, port_record)
                for root_record in root_records
            ):
                raise InstanceLeaseError(
                    f"cannot force-start: requested port {int(port)} is owned by a different YOLOmux instance"
                )
    all_lock_paths = tuple(sorted((*root_lock_paths, port_path), key=str))
    acquired = _acquire_instance_locks(all_lock_paths, force=force, identity=identity)
    if acquired is None:
        return None, None
    fd_by_path = dict(acquired)
    root_fds = [fd_by_path[path] for path in root_lock_paths]
    port_fd = fd_by_path[port_path]
    root_payload = (json.dumps(
        {
            **identity.process_record_fields(),
            "pgid": os.getpgid(0),
            "paths": _path_record(root_paths),
        },
        sort_keys=True,
    ) + "\n").encode("utf-8")
    port_payload = (json.dumps(
        {
            **identity.process_record_fields(),
            "pgid": os.getpgid(0),
            "port": int(port),
        },
        sort_keys=True,
    ) + "\n").encode("utf-8")
    try:
        _write_instance_record(root_fds, root_payload)
        _write_instance_record([port_fd], port_payload)
    except BaseException:
        _release_fds(list(fd_by_path.values()))
        raise
    return (
        ServerPortLease(port=0, path=root_lock_paths[0], fd=root_fds[0], extra_fds=tuple(root_fds[1:])),
        ServerPortLease(port=int(port), path=port_path, fd=port_fd),
    )

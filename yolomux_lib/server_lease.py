"""Cross-process ownership lease for one YOLOmux TCP port."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .infra.common import RUNTIME_DIR
from .infra.common import STATE_DIR
from .infra.common import ensure_runtime_root
from .infra.host_identity import HostIdentity
from .infra.host_identity import current_host_identity
from .infra.host_identity import is_current_local_process


@dataclass
class ServerPortLease:
    """An advisory lock held for the lifetime of a server process."""

    port: int
    path: Path
    fd: int
    reclaimed: bool = False

    def release(self) -> None:
        if self.fd < 0:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = -1


def lease_owner_status(record: dict, identity: HostIdentity):
    """Classify a persisted owner through the shared host/process fence."""

    return is_current_local_process(record, host_identity=identity)


def _read_locked_lease_record(fd: int) -> dict | None:
    """Read one bounded JSON record while its advisory lock is held."""

    try:
        size = os.fstat(fd).st_size
        if size <= 0 or size > 64 * 1024:
            return None
        os.lseek(fd, 0, os.SEEK_SET)
        record = json.loads(os.read(fd, size).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


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
    # A v0.6.10 server uses the legacy unscoped lease beside durable state.
    # Respect its live flock during rollout, but never write that path.
    legacy_path = STATE_DIR / "server-leases" / f"{clean_port}.lock"
    legacy_fd = -1
    if Path(state_dir) != STATE_DIR and legacy_path.exists():
        legacy_fd = os.open(str(legacy_path), os.O_RDWR)
        try:
            fcntl.flock(legacy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(legacy_fd)
            return None
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(str(path), os.O_RDWR)
        created = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        if legacy_fd >= 0:
            os.close(legacy_fd)
        return None
    reclaimed = False
    if not created:
        record = _read_locked_lease_record(fd)
        if record is None:
            os.close(fd)
            if legacy_fd >= 0:
                os.close(legacy_fd)
            return None
        owner = lease_owner_status(record, identity)
        if owner.current:
            if _record_pid(record) != os.getpid():
                os.close(fd)
                if legacy_fd >= 0:
                    os.close(legacy_fd)
                return None
        else:
            if not owner.may_remove_stale_record:
                os.close(fd)
                if legacy_fd >= 0:
                    os.close(legacy_fd)
                return None
            reclaimed = True
    # pgid rides along for the process-group ledger: when this owner dies, its
    # recorded group is the only identity by which stale children can be found.
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
    if legacy_fd >= 0:
        os.close(legacy_fd)
    return ServerPortLease(port=clean_port, path=path, fd=fd, reclaimed=reclaimed)

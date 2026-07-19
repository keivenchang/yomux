"""Cross-process ownership lease for one YOLOmux TCP port."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .common import STATE_DIR


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


def acquire_server_port_lease(port: int, state_dir: Path = STATE_DIR) -> ServerPortLease | None:
    """Claim ``port`` without relying on a racy listener probe.

    The lock survives detached launchers and is released by the kernel if the
    owning server dies.  It deliberately covers setup before ``bind()`` so a
    losing concurrent launch cannot start control/background services.
    """
    clean_port = int(port)
    lease_dir = state_dir / "server-leases"
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
    # pgid rides along for the process-group ledger: when this owner dies, its
    # recorded group is the only identity by which stale children can be found.
    payload = json.dumps({"pid": os.getpid(), "pgid": os.getpgid(0), "port": clean_port}) + "\n"
    os.ftruncate(fd, 0)
    os.write(fd, payload.encode("utf-8"))
    os.fsync(fd)
    return ServerPortLease(port=clean_port, path=path, fd=fd)

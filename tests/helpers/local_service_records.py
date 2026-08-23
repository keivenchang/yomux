# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Typed builders for fixture-owned host, process, lease, and service records."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra.host_identity import current_host_identity


# Directory names that identify a directory as SHARED (owned by no single test), never a
# safe deletion target, however this function is reached. `pytest-of-<user>` is pytest's own
# basetemp root; `yop-*` is this repo's `tests/conftest.py` per-process TMPDIR root -- both
# are ancestors real deletions must never cross.
_SHARED_DIRECTORY_NAME_PREFIXES = ("pytest-of-", "yop-")


def rmtree_within(target: Path, owned_root: Path) -> None:
    """Delete `target` only if it cannot be the shared system temp root or a directory tree
    other tests/processes depend on.

    A test simulating "the record directory vanishes mid-write" must never be able to
    delete the shared basetemp or the bare system temp directory -- a path-length safety
    fallback (`safe_socket_path`) can legitimately place a registry's record under a
    privately-digest-named directory directly under the system temp dir rather than under
    `owned_root` (the test's own `tmp_path`); that is still safe to delete (it is unique to
    this one candidate path, not shared), so containment is checked by BLOCKING the known
    shared ancestors rather than requiring strict containment under `owned_root` alone.
    Historically this used `shutil.rmtree(..., ignore_errors=True)` with no check at all: a
    wrong target failed silently and deleted the shared `/tmp` root itself. This raises
    loudly instead of silently skipping past a dangerous target.
    """
    resolved_target = target.resolve()
    resolved_root = owned_root.resolve()
    system_temp_dir = Path(tempfile.gettempdir()).resolve()
    contained = resolved_target.is_relative_to(resolved_root)
    is_system_temp_root = resolved_target == system_temp_dir
    is_shared_directory = resolved_target.name.startswith(_SHARED_DIRECTORY_NAME_PREFIXES)
    if not contained and (is_system_temp_root or is_shared_directory):
        raise AssertionError(
            f"refusing to rmtree {resolved_target}: a shared directory, not inside owned root {resolved_root}"
        )
    shutil.rmtree(resolved_target, ignore_errors=True)


@dataclass(frozen=True)
class FixtureHostIdentityBuilder:
    """Build a valid identity while keeping every test-varying field visible."""

    stable_host_id: str = "fixture-host"
    display_hostname: str = "fixture-host.example"
    boot_id: str = "fixture-boot"
    pid: int = 4242
    process_start_ticks: int = 5252
    instance_nonce: str = "fixture-host-instance"
    stable_host_id_source: str = "gate fixture"

    def build(self) -> HostIdentity:
        return HostIdentity(
            stable_host_id=self.stable_host_id,
            display_hostname=self.display_hostname,
            boot_id=self.boot_id,
            pid=self.pid,
            process_start_identity=f"proc:{self.process_start_ticks}",
            process_start_ticks=self.process_start_ticks,
            instance_nonce=self.instance_nonce,
            stable_host_id_source=self.stable_host_id_source,
        )


@dataclass(frozen=True)
class FixtureProcessRecordBuilder:
    """Project one host identity into the exact persisted process-record shape."""

    pid: int
    identity: HostIdentity = field(default_factory=current_host_identity)
    process_start_ticks: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def build(self) -> dict[str, Any]:
        if self.process_start_ticks is None and self.pid == self.identity.pid:
            start_identity = self.identity.process_start_identity
        else:
            ticks = self.pid + 1000 if self.process_start_ticks is None else self.process_start_ticks
            start_identity = f"proc:{ticks}"
        return {
            **self.identity.process_record_fields(pid=self.pid, start_identity=start_identity),
            **self.fields,
        }


@dataclass(frozen=True)
class FixtureLeaseRecordBuilder:
    """Build or publish one server lease without hidden process defaults."""

    pid: int = 400
    pgid: int = 400
    port: int = 18991
    identity: HostIdentity = field(default_factory=current_host_identity)
    process_start_ticks: int | None = None
    members: tuple[dict[str, Any], ...] = ()
    include_identity: bool = True

    def build(self) -> dict[str, Any]:
        record: dict[str, Any] = {"pid": self.pid, "pgid": self.pgid, "port": self.port}
        if self.include_identity:
            record = {
                **FixtureProcessRecordBuilder(
                    pid=self.pid,
                    identity=self.identity,
                    process_start_ticks=self.process_start_ticks,
                ).build(),
                **record,
            }
        if self.members:
            record["members"] = [dict(member) for member in self.members]
        return record

    def write(self, state_dir: Path, *, host_id: str = "") -> Path:
        lease_dir = state_dir / "server-leases"
        if host_id:
            lease_dir /= host_id
        lease_dir.mkdir(parents=True, exist_ok=True)
        path = lease_dir / f"{self.port}.lock"
        path.write_text(json.dumps(self.build()), encoding="utf-8")
        return path


@dataclass(frozen=True)
class FixtureLocalServiceRecordBuilder:
    """Build one persisted local-service row from the shared process parent."""

    service: str
    socket_path: Path
    pid: int = 500
    identity: HostIdentity = field(default_factory=current_host_identity)
    process_start_ticks: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def build(self) -> dict[str, Any]:
        return FixtureProcessRecordBuilder(
            pid=self.pid,
            identity=self.identity,
            process_start_ticks=self.process_start_ticks,
            fields={"service": self.service, "socket": str(self.socket_path), **self.fields},
        ).build()

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host-qualified runtime and local-service identity contracts."""

from __future__ import annotations

from pathlib import Path

from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.server_lease import acquire_server_port_lease
from yolomux_lib.infra.host_identity import HostIdentity


def _identity(name: str) -> HostIdentity:
    return HostIdentity(
        stable_host_id=f"fixture-{name}",
        display_hostname=f"{name}.example",
        boot_id=f"boot-{name}",
        pid=4242,
        process_start_identity="proc:6262",
        process_start_ticks=6262,
        instance_nonce=f"nonce-{name}",
        stable_host_id_source="test",
    )


def test_host_qualified_port_leases_do_not_collide(tmp_path: Path) -> None:
    first = acquire_server_port_lease(49175, state_dir=tmp_path, host_identity=_identity("a"))
    second = acquire_server_port_lease(49175, state_dir=tmp_path, host_identity=_identity("b"))

    assert first is not None
    assert second is not None
    assert first.path != second.path

    first.release()
    second.release()


def test_local_service_record_keeps_the_carried_process_identity(tmp_path: Path) -> None:
    identity = _identity("a")
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "fixture.module", "fixture.sock", protocol_version=1),
        host_identity=identity,
    )

    record = registry._record_from_status(
        {"pid": identity.pid, "version": 1, "process_start_identity": identity.process_start_identity}
    )

    assert record["stable_host_id"] == identity.stable_host_id
    assert record["boot_id"] == identity.boot_id
    assert record["process_start_identity"] == identity.process_start_identity

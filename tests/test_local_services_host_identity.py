# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused coverage for local-service client lease identity wiring."""

from __future__ import annotations

from yolomux_lib.host_identity import HostIdentity
from yolomux_lib.host_identity import is_current_local_process
from yolomux_lib.local_services.runtime import acquire_client_lease
from yolomux_lib.local_services.runtime import reap_dead_client_leases


def fixture_identity(*, stable_host_id: str = "host-a") -> HostIdentity:
    return HostIdentity(
        stable_host_id=stable_host_id,
        display_hostname="host-a.example",
        boot_id="boot-a",
        pid=4242,
        process_start_identity="proc:6262",
        process_start_ticks=6262,
        instance_nonce="instance-a",
        stable_host_id_source="fixture",
    )


def test_record_only_reclaim_covers_unidentifiable_records_and_nothing_else() -> None:
    """Only a same-boot record whose PID names no process may be discarded outright."""
    identity = fixture_identity()
    live = identity.process_record_fields()
    poisoned = {**identity.process_record_fields(pid=0, start_identity=""), "pid": 0}
    previous_boot = {**poisoned, "boot_id": "boot-previous"}
    foreign_host = {**poisoned, "stable_host_id": "host-b"}
    readers = {
        "start_identity_reader": lambda _pid: identity.process_start_identity,
        "pid_probe": lambda _pid: True,
    }

    reclaimable = {
        name: is_current_local_process(record, host_identity=identity, **readers)
        for name, record in (
            ("live", live),
            ("poisoned", poisoned),
            ("previous_boot", previous_boot),
            ("foreign_host", foreign_host),
        )
    }

    assert {name: item.reason.value for name, item in reclaimable.items()} == {
        "live": "current_local_process",
        "poisoned": "invalid_pid",
        "previous_boot": "previous_boot",
        "foreign_host": "foreign_host",
    }
    assert {name: item.may_remove_unidentifiable_record for name, item in reclaimable.items()} == {
        "live": False,
        "poisoned": True,
        "previous_boot": False,
        "foreign_host": False,
    }
    # The record-only reclaim must not widen the authority that lets other
    # callers act on a record's process fields.
    assert [item.may_remove_stale_record for item in reclaimable.values()] == [False, False, False, False]


def test_local_service_client_lease_records_and_rechecks_process_birth_identity() -> None:
    identity = fixture_identity()
    leases: dict[str, object] = {}

    acquired = acquire_client_lease(
        leases,
        identity.pid,
        host_identity=identity,
        start_identity_reader=lambda _pid: identity.process_start_identity,
    )

    assert acquired["ok"] is True
    lease_id = str(acquired["lease_id"])
    assert leases[lease_id] == identity.process_record_fields()
    assert reap_dead_client_leases(
        leases,
        host_identity=identity,
        start_identity_reader=lambda _pid: identity.process_start_identity,
        pid_probe=lambda _pid: True,
    ) == 0
    assert reap_dead_client_leases(
        leases,
        host_identity=identity,
        start_identity_reader=lambda _pid: None,
    ) == 1
    assert leases == {}


def test_local_service_client_lease_preserves_foreign_record_without_local_lookup() -> None:
    identity = fixture_identity()
    foreign = fixture_identity(stable_host_id="host-b")
    leases: dict[str, object] = {"foreign": foreign.process_record_fields()}
    lookups: list[int] = []

    reaped = reap_dead_client_leases(
        leases,
        host_identity=identity,
        start_identity_reader=lambda pid: lookups.append(pid) or None,
    )

    assert reaped == 0
    assert lookups == []
    assert leases == {"foreign": foreign.process_record_fields()}


def test_local_service_client_lease_fails_closed_when_live_process_birth_is_unavailable() -> None:
    identity = fixture_identity()
    record = identity.process_record_fields()
    leases: dict[str, object] = {"live-unreadable": record}
    probes: list[int] = []

    reaped = reap_dead_client_leases(
        leases,
        host_identity=identity,
        start_identity_reader=lambda _pid: None,
        pid_probe=lambda pid: probes.append(pid) or True,
    )
    acquired = acquire_client_lease(
        {},
        identity.pid,
        host_identity=identity,
        start_identity_reader=lambda _pid: None,
        pid_probe=lambda pid: probes.append(pid) or True,
    )

    assert reaped == 0
    assert leases == {"live-unreadable": record}
    assert acquired["ok"] is False
    assert acquired["diagnostic"]["reason"] == "process_identity_unavailable"
    assert probes == [identity.pid, identity.pid]

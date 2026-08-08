# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused coverage for the shared host identity and process fence."""

from __future__ import annotations

from pathlib import Path

import pytest

from yolomux_lib import host_identity as public_host_identity_module
from yolomux_lib.host_identity import HostIdentity
from yolomux_lib.host_identity import HostIdentityError
from yolomux_lib.host_identity import LateHostIdentityOverrideError
from yolomux_lib.host_identity import LocalProcessReason
from yolomux_lib.host_identity import current_host_identity
from yolomux_lib.host_identity import is_current_local_process
from yolomux_lib.host_identity import normalize_stable_host_id
from yolomux_lib.host_identity import process_start_identity
from yolomux_lib.infra import host_identity as infra_host_identity_module


def fixture_identity(
    *,
    stable_host_id: str = "host-a",
    boot_id: str = "boot-a",
    pid: int = 4242,
    start_identity: str = "proc:6262",
) -> HostIdentity:
    return HostIdentity(
        stable_host_id=stable_host_id,
        display_hostname="host-a.example",
        boot_id=boot_id,
        pid=pid,
        process_start_identity=start_identity,
        process_start_ticks=6262,
        instance_nonce="instance-a",
        stable_host_id_source="fixture",
    )


def test_explicit_host_id_is_normalized_and_recorded_with_boot_and_process_identity(tmp_path: Path) -> None:
    boot_path = tmp_path / "boot-id"
    boot_path.write_text("Boot_A\n", encoding="utf-8")

    identity = HostIdentity.from_system(
        environ={"YOLOMUX_HOST_ID": " Container_A "},
        machine_id_path=tmp_path / "missing-machine-id",
        boot_id_path=boot_path,
        hostname_reader=lambda: "display-host",
        pid_reader=lambda: 4242,
        start_identity_reader=lambda _pid: "proc:6262",
        nonce_factory=lambda: "Instance_A",
    )

    assert identity.stable_host_id == "container_a"
    assert identity.stable_host_id_source == "YOLOMUX_HOST_ID"
    assert identity.display_hostname == "display-host"
    assert identity.boot_id == "boot_a"
    assert identity.process_start_ticks == 6262
    assert identity.process_record_fields() == {
        "stable_host_id": "container_a",
        "hostname": "display-host",
        "boot_id": "boot_a",
        "pid": 4242,
        "process_start_identity": "proc:6262",
        "process_start_ticks": 6262,
        "instance_nonce": "instance_a",
    }


@pytest.mark.parametrize("value", ["", " ", ".", "..", "../host", "host/name", "host name", "host:name", "host-unicode-☃"])
def test_host_id_override_rejects_unsafe_path_text(value: str) -> None:
    with pytest.raises(HostIdentityError, match="invalid stable host ID"):
        normalize_stable_host_id(value, source="YOLOMUX_HOST_ID")


def test_host_identity_constructor_preserves_the_path_safety_invariant() -> None:
    with pytest.raises(HostIdentityError, match="invalid stable host ID"):
        fixture_identity(stable_host_id="../foreign-host")


def test_machine_id_is_the_default_stable_owner(tmp_path: Path) -> None:
    machine_path = tmp_path / "machine-id"
    boot_path = tmp_path / "boot-id"
    machine_path.write_text("ABCDEF012345\n", encoding="utf-8")
    boot_path.write_text("11111111-2222-3333-4444-555555555555\n", encoding="utf-8")

    identity = HostIdentity.from_system(
        environ={},
        machine_id_path=machine_path,
        boot_id_path=boot_path,
        hostname_reader=lambda: "renamable-display-host",
        pid_reader=lambda: 4242,
        start_identity_reader=lambda _pid: "proc:6262",
        nonce_factory=lambda: "instance-a",
    )

    assert identity.stable_host_id == "abcdef012345"
    assert identity.stable_host_id_source == str(machine_path)
    assert identity.display_hostname == "renamable-display-host"


def test_identity_that_cannot_be_established_is_not_a_late_override(tmp_path: Path) -> None:
    with pytest.raises(HostIdentityError, match="cannot read machine ID") as caught:
        HostIdentity.from_system(environ={}, machine_id_path=tmp_path / "missing-machine-id")

    assert not isinstance(caught.value, LateHostIdentityOverrideError)


def test_late_host_id_override_fails_instead_of_returning_cached_machine_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOLOMUX_HOST_ID", raising=False)
    current_host_identity.cache_clear()
    try:
        identity = current_host_identity()
        assert identity.stable_host_id_source != "YOLOMUX_HOST_ID"

        monkeypatch.setenv("YOLOMUX_HOST_ID", "some-other-machine")

        with pytest.raises(HostIdentityError, match="before host identity is resolved") as caught:
            current_host_identity()
        assert isinstance(caught.value, infra_host_identity_module.LateHostIdentityOverrideError)
        assert caught.value.reason_code == "late_host_id_override_rejected"
        assert caught.value.identity is identity
        assert caught.value.resolved_override is None
        assert caught.value.rejected_override == "some-other-machine"
    finally:
        current_host_identity.cache_clear()


def test_invalid_late_host_id_override_preserves_the_effective_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOLOMUX_HOST_ID", raising=False)
    current_host_identity.cache_clear()
    try:
        identity = current_host_identity()
        monkeypatch.setenv("YOLOMUX_HOST_ID", "../invalid-late-override")

        with pytest.raises(LateHostIdentityOverrideError) as caught:
            current_host_identity()

        assert caught.value.identity is identity
        assert caught.value.resolved_override is None
        assert caught.value.rejected_override == "../invalid-late-override"
        assert caught.value.rejected_override_valid is False
    finally:
        current_host_identity.cache_clear()


def test_public_host_identity_import_is_the_single_infra_module() -> None:
    assert public_host_identity_module is infra_host_identity_module


def test_process_start_identity_reads_proc_stat_field_22_with_parentheses_in_command(tmp_path: Path) -> None:
    pid = 4242
    proc_dir = tmp_path / str(pid)
    proc_dir.mkdir()
    fields_from_three = ["S", *(["0"] * 18), "987654", "0"]
    (proc_dir / "stat").write_text(f"{pid} (worker with ) paren) {' '.join(fields_from_three)}\n", encoding="utf-8")

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("ps fallback must not run when proc start ticks are present")

    assert process_start_identity(pid, proc_root=tmp_path, runner=unexpected_runner) == "proc:987654"


@pytest.mark.parametrize(
    ("record_updates", "reason"),
    [
        ({"stable_host_id": "host-b"}, LocalProcessReason.FOREIGN_HOST),
        ({"boot_id": "boot-b"}, LocalProcessReason.PREVIOUS_BOOT),
        ({"stable_host_id": ""}, LocalProcessReason.MISSING_HOST_IDENTITY),
        ({"boot_id": ""}, LocalProcessReason.MISSING_BOOT_IDENTITY),
        ({"pid": 0}, LocalProcessReason.INVALID_PID),
        ({"process_start_identity": "", "process_start_ticks": 0}, LocalProcessReason.MISSING_PROCESS_START_IDENTITY),
    ],
)
def test_process_fence_refuses_before_local_lookup(record_updates: dict[str, object], reason: LocalProcessReason) -> None:
    identity = fixture_identity()
    record = {**identity.process_record_fields(), **record_updates}
    lookups: list[int] = []
    probes: list[int] = []

    diagnostic = is_current_local_process(
        record,
        host_identity=identity,
        start_identity_reader=lambda pid: lookups.append(pid) or "proc:6262",
        pid_probe=lambda pid: probes.append(pid) or True,
    )

    assert diagnostic.current is False
    assert diagnostic.reason is reason
    assert lookups == []
    assert probes == []
    assert diagnostic.as_dict()["reason"] == reason.value


def test_process_fence_accepts_only_matching_live_birth_identity() -> None:
    identity = fixture_identity()
    record = identity.process_record_fields()
    probes: list[int] = []

    diagnostic = is_current_local_process(
        record,
        host_identity=identity,
        start_identity_reader=lambda _pid: "proc:6262",
        pid_probe=lambda pid: probes.append(pid) or True,
    )

    assert diagnostic.current is True
    assert diagnostic.reason is LocalProcessReason.CURRENT
    assert probes == [4242]


def test_process_fence_types_dead_and_recycled_local_records_for_safe_record_cleanup() -> None:
    identity = fixture_identity()
    record = identity.process_record_fields()

    dead = is_current_local_process(record, host_identity=identity, start_identity_reader=lambda _pid: None)
    unavailable = is_current_local_process(
        record,
        host_identity=identity,
        start_identity_reader=lambda _pid: None,
        pid_probe=lambda _pid: True,
    )
    recycled = is_current_local_process(record, host_identity=identity, start_identity_reader=lambda _pid: "proc:9999")
    previous_boot = is_current_local_process(
        {**record, "boot_id": "boot-b"},
        host_identity=identity,
        start_identity_reader=lambda _pid: "proc:6262",
    )

    assert dead.reason is LocalProcessReason.PROCESS_NOT_FOUND
    assert dead.may_remove_stale_record is True
    assert unavailable.reason is LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE
    assert unavailable.may_remove_stale_record is False
    assert recycled.reason is LocalProcessReason.PROCESS_IDENTITY_REUSED
    assert recycled.may_remove_stale_record is True
    assert previous_boot.reason is LocalProcessReason.PREVIOUS_BOOT
    assert previous_boot.may_remove_stale_record is False


def test_host_qualified_keys_and_paths_use_only_validated_stable_id(tmp_path: Path) -> None:
    host_a = fixture_identity(stable_host_id="host-a")
    host_b = fixture_identity(stable_host_id="host-b")

    assert host_a.qualify_key("tmux", "fixture:0.0") != host_b.qualify_key("tmux", "fixture:0.0")
    assert host_a.namespaced_path(tmp_path, "cache.sqlite3") == tmp_path / "host-a" / "cache.sqlite3"
    assert host_a.namespaced_path(tmp_path, "cache.sqlite3") != host_b.namespaced_path(tmp_path, "cache.sqlite3")

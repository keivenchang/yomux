# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Red contracts for host-correct server and background-owner election."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from yolomux_lib import server_lease as server_lease_module
from yolomux_lib.infra import background_owner as background_owner_module
from yolomux_lib.infra.background_owner import BackgroundOwnerRegistry
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.server_lease import acquire_server_port_lease


FIXTURE_PID = 4242
FIXTURE_PGID = 5252
FIXTURE_PORT = 49175
FIXTURE_PROCESS_START_TICKS = 6262
SHARED_PATH = "/same/absolute/shared/path"
HOST_IDENTITY_MODULE = "yolomux_lib.infra.host_identity"


def _host_identity(
    name: str,
    *,
    boot_id: str | None = None,
    instance_nonce: str | None = None,
) -> Any:
    module = importlib.import_module(HOST_IDENTITY_MODULE)
    return module.HostIdentity(
        stable_host_id=f"fixture-{name}",
        display_hostname=f"fixture-{name}.example",
        boot_id=boot_id or f"00000000-0000-0000-0000-00000000000{name[-1]}",
        pid=FIXTURE_PID,
        process_start_identity=f"proc:{FIXTURE_PROCESS_START_TICKS}",
        process_start_ticks=FIXTURE_PROCESS_START_TICKS,
        instance_nonce=instance_nonce or f"fixture-{name}-instance",
        stable_host_id_source="gate fixture",
    )


def _process_record(identity: Any, **fields: Any) -> dict[str, Any]:
    return {**identity.process_record_fields(), **fields}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), value
    return value


def _run_concurrently(callables: tuple[Any, ...]) -> list[Any]:
    barrier = Barrier(len(callables))

    def invoke(operation: Any) -> Any:
        barrier.wait(timeout=2.0)
        return operation()

    with ThreadPoolExecutor(max_workers=len(callables), thread_name_prefix="gate-owner-election") as executor:
        futures = [executor.submit(invoke, operation) for operation in callables]
        return [future.result(timeout=5.0) for future in futures]


@pytest.mark.xfail(
    strict=True,
    reason="multi-host ownership does not yet consume HostIdentity or host-local lease/owner/service roots",
)
def test_two_hosts_with_coincident_process_values_keep_leases_owners_and_services_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identities = (_host_identity("host-a"), _host_identity("host-b"))
    runtime_roots = (tmp_path / "host-a-local" / "runtime", tmp_path / "host-b-local" / "runtime")
    monkeypatch.setattr(server_lease_module.os, "getpgid", lambda _pid: FIXTURE_PGID)

    def start_host(identity: Any, runtime_root: Path) -> Any:
        lease = acquire_server_port_lease(
            FIXTURE_PORT,
            state_dir=runtime_root,
            host_identity=identity,
        )
        assert lease is not None
        owner = BackgroundOwnerRegistry(
            owner_dir=runtime_root / "background-owner",
            project_root=SHARED_PATH,
            host_identity=identity,
        )
        assert owner.acquire_owner_lock() is True
        owner.mark_owner_acquired("acquired")
        service = LocalServiceRegistry(
            runtime_root,
            LocalServiceSpec("fixture", "fixture.module", "fixture.sock", protocol_version=1),
            host_identity=identity,
        )
        service._write_record(
            service._record_from_status(
                _process_record(
                    identity,
                    pid=FIXTURE_PID,
                    pgid=FIXTURE_PGID,
                    version=1,
                    socket=str(service.socket_path),
                )
            )
        )
        return lease, owner, service

    started: list[Any] = []
    try:
        started = _run_concurrently(
            tuple(
                lambda identity=identity, runtime_root=runtime_root: start_host(identity, runtime_root)
                for identity, runtime_root in zip(identities, runtime_roots, strict=True)
            )
        )

        assert len({lease.path for lease, _owner, _service in started}) == 2
        for identity, runtime_root, (lease, owner, service) in zip(
            identities,
            runtime_roots,
            started,
            strict=True,
        ):
            assert lease.path.is_relative_to(runtime_root)
            assert owner.owner_path.is_relative_to(runtime_root)
            assert service.record_path.is_relative_to(runtime_root)
            assert owner.project_root == SHARED_PATH
            for record in (
                _read_json(lease.path),
                _read_json(owner.owner_path),
                _read_json(service.record_path),
            ):
                assert record["stable_host_id"] == identity.stable_host_id
                assert record["boot_id"] == identity.boot_id
                assert record["pid"] == FIXTURE_PID
                assert record["pgid"] == FIXTURE_PGID
                assert record["process_start_identity"] == f"proc:{FIXTURE_PROCESS_START_TICKS}"
                assert record["process_start_ticks"] == identity.process_start_ticks

        assert identities[0].stable_host_id not in json.dumps(
            [_read_json(path) for path in runtime_roots[1].rglob("*.json")],
            sort_keys=True,
        )
        assert identities[1].stable_host_id not in json.dumps(
            [_read_json(path) for path in runtime_roots[0].rglob("*.json")],
            sort_keys=True,
        )
    finally:
        for lease, owner, _service in started:
            owner.release_owner("test-cleanup")
            lease.release()


def test_foreign_generation_with_coincident_pid_is_preserved_and_cannot_block_local_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_identity = _host_identity("host-a")
    foreign_identity = _host_identity("host-b")
    registry = BackgroundOwnerRegistry(
        owner_dir=tmp_path / "host-a-local" / "runtime" / "background-owner",
        project_root=SHARED_PATH,
        host_identity=local_identity,
        clock=lambda: 100.0,
    )
    registry.generations_dir.mkdir(parents=True)
    foreign_path = registry.generations_dir / "foreign.json"
    foreign_record = _process_record(
        foreign_identity,
        generation_id="foreign",
        started_at_ns=registry.started_at_ns + 1,
        last_heartbeat=100.0,
        owner=True,
        status="owner",
    )
    foreign_path.write_text(json.dumps(foreign_record, sort_keys=True) + "\n", encoding="utf-8")
    original_foreign_bytes = foreign_path.read_bytes()
    local_pid_lookups: list[int] = []
    monkeypatch.setattr(
        background_owner_module,
        "pid_is_alive",
        lambda pid: local_pid_lookups.append(pid) or True,
    )
    rpc_calls: list[Any] = []
    monkeypatch.setattr(
        background_owner_module,
        "send_yolomux_control_request",
        lambda *args, **kwargs: rpc_calls.append((args, kwargs)) or {"ok": False},
    )

    try:
        assert registry.live_generation_records() == []
        assert local_pid_lookups == []
        assert foreign_path.read_bytes() == original_foreign_bytes

        assert registry.attempt_takeover() is True
        assert registry.is_owner() is True
        assert registry.read_owner_record()["stable_host_id"] == local_identity.stable_host_id
        assert foreign_path.read_bytes() == original_foreign_bytes
        assert rpc_calls == []
    finally:
        registry.release_owner("test-cleanup")


def test_recycled_pid_from_previous_boot_is_not_current_local_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module(HOST_IDENTITY_MODULE)
    current_identity = _host_identity(
        "host-a",
        boot_id="00000000-0000-0000-0000-0000000000a2",
        instance_nonce="current-boot-instance",
    )
    previous_boot = _host_identity(
        "host-a",
        boot_id="00000000-0000-0000-0000-0000000000a1",
        instance_nonce="previous-boot-instance",
    )
    previous_record = _process_record(
        previous_boot,
        generation_id="previous-boot-owner",
        control_socket=str(tmp_path / "previous-boot.sock"),
    )

    process_diagnostic = module.is_current_local_process(previous_record, host_identity=current_identity)
    assert process_diagnostic.current is False
    assert process_diagnostic.reason is module.LocalProcessReason.PREVIOUS_BOOT

    registry = BackgroundOwnerRegistry(
        owner_dir=tmp_path / "host-a-local" / "runtime" / "background-owner",
        host_identity=current_identity,
    )
    registry.owner_dir.mkdir(parents=True)
    registry.owner_path.write_text(json.dumps(previous_record, sort_keys=True) + "\n", encoding="utf-8")
    rpc_calls: list[Any] = []
    monkeypatch.setattr(
        background_owner_module,
        "send_yolomux_control_request",
        lambda *args, **kwargs: rpc_calls.append((args, kwargs)) or {"ok": True},
    )

    result = registry.request_current_owner_release()

    assert result["ok"] is False
    assert result["reason_code"] == module.LocalProcessReason.PREVIOUS_BOOT.value
    assert result["diagnostic"]["reason"] == module.LocalProcessReason.PREVIOUS_BOOT.value
    assert rpc_calls == []


def test_foreign_owner_release_refuses_unix_rpc_with_typed_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_identity = _host_identity("host-a")
    foreign_identity = _host_identity("host-b")
    registry = BackgroundOwnerRegistry(
        owner_dir=tmp_path / "host-a-local" / "runtime" / "background-owner",
        host_identity=local_identity,
    )
    registry.owner_dir.mkdir(parents=True)
    registry.owner_path.write_text(
        json.dumps(
            _process_record(
                foreign_identity,
                generation_id="foreign-owner",
                control_socket="/same/path/on/different/hosts/control.sock",
            ),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    rpc_calls: list[Any] = []
    monkeypatch.setattr(
        background_owner_module,
        "send_yolomux_control_request",
        lambda *args, **kwargs: rpc_calls.append((args, kwargs)) or {"ok": True},
    )

    result = registry.request_current_owner_release()

    assert result["ok"] is False
    assert result["reason_code"] == "foreign_host"
    assert result["diagnostic"]["reason"] == "foreign_host"
    assert result["diagnostic"]["record_host_id"] == foreign_identity.stable_host_id
    assert rpc_calls == []


def test_concurrent_same_host_contenders_elect_exactly_one_writer_per_host(tmp_path: Path) -> None:
    host_specs = (
        ("host-a", tmp_path / "host-a-local" / "runtime"),
        ("host-b", tmp_path / "host-b-local" / "runtime"),
    )
    contenders: list[tuple[str, BackgroundOwnerRegistry]] = []
    for host_name, runtime_root in host_specs:
        for contender_index in range(2):
            identity = _host_identity(
                host_name,
                instance_nonce=f"fixture-{host_name}-contender-{contender_index}",
            )
            contenders.append(
                (
                    host_name,
                    BackgroundOwnerRegistry(
                        owner_dir=runtime_root / "background-owner",
                        project_root=SHARED_PATH,
                        host_identity=identity,
                    ),
                )
            )

    start = Barrier(len(contenders))
    attempted = Barrier(len(contenders))

    def contend(host_name: str, registry: BackgroundOwnerRegistry) -> tuple[str, bool]:
        start.wait(timeout=2.0)
        acquired = registry.acquire_owner_lock()
        attempted.wait(timeout=2.0)
        if acquired:
            registry.mark_owner_acquired("acquired")
        return host_name, acquired

    try:
        with ThreadPoolExecutor(max_workers=len(contenders), thread_name_prefix="gate-host-writer") as executor:
            futures = [executor.submit(contend, host_name, registry) for host_name, registry in contenders]
            results = [future.result(timeout=5.0) for future in futures]

        for host_name, runtime_root in host_specs:
            assert sum(acquired for result_host, acquired in results if result_host == host_name) == 1
            owner_record = _read_json(runtime_root / "background-owner" / "owner.json")
            assert owner_record["stable_host_id"] == f"fixture-{host_name}"
            assert owner_record["status"] == "owner"
    finally:
        for _host_name, registry in contenders:
            registry.release_owner("test-cleanup")

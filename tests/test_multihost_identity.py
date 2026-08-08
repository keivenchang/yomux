# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Two-host regression harness for shared-home ownership and identity."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Callable
from typing import TypeVar

import pytest

from yolomux_lib import common
from yolomux_lib.background_owner import BackgroundOwnerRegistry
from yolomux_lib.host_identity import HostIdentity
from yolomux_lib.local_services import preflight as preflight_module
from yolomux_lib.local_services import registry as registry_module
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.server_lease import acquire_server_port_lease
from yolomux_lib.tmux.sessions import TmuxPaneInfo
from yolomux_lib.workspace.metadata import tmux_pane_graph_id


FIXTURE_PID = 4242
FIXTURE_PGID = 5252
FIXTURE_PORT = 49175
FIXTURE_TMUX_TARGET = "fixture:0.0"


@dataclass(frozen=True)
class SimulatedHost:
    stable_host_id: str
    hostname: str
    boot_id: str
    runtime_root: Path
    data_root: Path
    cache_root: Path
    pid: int = FIXTURE_PID
    pgid: int = FIXTURE_PGID
    process_start_ticks: int = 6262
    instance_nonce: str = "fixture-instance"

    def process_record(self, **fields: object) -> dict[str, object]:
        return {
            "stable_host_id": self.stable_host_id,
            "hostname": self.hostname,
            "boot_id": self.boot_id,
            "pid": self.pid,
            "pgid": self.pgid,
            "process_start_ticks": self.process_start_ticks,
            "instance_nonce": self.instance_nonce,
            **fields,
        }


@dataclass(frozen=True)
class TwoHostFixture:
    shared_root: Path
    host_a: SimulatedHost
    host_b: SimulatedHost


@pytest.fixture
def two_hosts(tmp_path: Path) -> TwoHostFixture:
    shared_root = tmp_path / "same-absolute-shared-root"
    shared_root.mkdir()

    def host(name: str) -> SimulatedHost:
        local_root = tmp_path / f"{name}-local"
        return SimulatedHost(
            stable_host_id=f"fixture-{name}",
            hostname=f"fixture-{name}.example",
            boot_id=f"00000000-0000-0000-0000-00000000000{name[-1]}",
            runtime_root=local_root / "runtime",
            data_root=local_root / "data",
            cache_root=local_root / "cache",
            instance_nonce=f"fixture-{name}-instance",
        )

    fixture = TwoHostFixture(shared_root=shared_root, host_a=host("host-a"), host_b=host("host-b"))
    assert fixture.host_a.runtime_root != fixture.host_b.runtime_root
    assert fixture.host_a.data_root != fixture.host_b.data_root
    assert fixture.host_a.cache_root != fixture.host_b.cache_root
    return fixture


T = TypeVar("T")


def run_concurrently(operation: Callable[[SimulatedHost, threading.Barrier], T], hosts: TwoHostFixture) -> list[T]:
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="two-host-harness") as executor:
        futures = [executor.submit(operation, host, barrier) for host in (hosts.host_a, hosts.host_b)]
        return [future.result(timeout=3.0) for future in futures]


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def fixture_pane() -> TmuxPaneInfo:
    return TmuxPaneInfo(
        session="fixture",
        window="0",
        window_name="fixture-window",
        pane="0",
        pane_id="%42",
        target=FIXTURE_TMUX_TARGET,
        current_path="/shared/same/path",
        command="codex",
        active=True,
        window_active=True,
        title="fixture",
        pid=FIXTURE_PID,
        process_label="codex",
    )


def simulated_identity(host: SimulatedHost) -> HostIdentity:
    return HostIdentity(
        stable_host_id=host.stable_host_id,
        display_hostname=host.hostname,
        boot_id=host.boot_id,
        pid=host.pid,
        process_start_identity=f"proc:{host.process_start_ticks}",
        process_start_ticks=host.process_start_ticks,
        instance_nonce=host.instance_nonce,
        stable_host_id_source="two-host-harness",
    )


@pytest.mark.xfail(strict=True, reason="host-local runtime layout lands after the shared identity parent")
def test_two_hosts_can_hold_the_same_port_lease_without_colliding(two_hosts: TwoHostFixture) -> None:
    leases = []

    def acquire(_host: SimulatedHost, barrier: threading.Barrier):
        barrier.wait(timeout=2.0)
        return acquire_server_port_lease(FIXTURE_PORT, state_dir=two_hosts.shared_root)

    try:
        leases = run_concurrently(acquire, two_hosts)
        assert all(lease is not None for lease in leases), leases
        assert len({lease.path for lease in leases if lease is not None}) == 2
    finally:
        for lease in leases:
            if lease is not None:
                lease.release()


def test_foreign_owner_generation_is_not_pruned_by_same_number_local_pid(two_hosts: TwoHostFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    owner_dir = two_hosts.shared_root / "background-owner"
    registry = BackgroundOwnerRegistry(
        owner_dir=owner_dir,
        pid=FIXTURE_PID,
        hostname=two_hosts.host_a.hostname,
        host_identity=simulated_identity(two_hosts.host_a),
        clock=lambda: 100.0,
    )
    foreign_path = registry.generations_dir / "foreign.json"
    write_json(
        foreign_path,
        two_hosts.host_b.process_record(
            generation_id="foreign",
            started_at_ns=1,
            last_heartbeat=100.0,
            owner=True,
        ),
    )
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", lambda _pid: False)

    def reconcile(host: SimulatedHost, barrier: threading.Barrier):
        barrier.wait(timeout=2.0)
        if host == two_hosts.host_a:
            registry.live_generation_records()
        barrier.wait(timeout=2.0)
        return foreign_path.exists()

    results = run_concurrently(reconcile, two_hosts)
    assert results == [True, True]


def test_background_owner_uses_the_injected_host_identity_process_fields(two_hosts: TwoHostFixture) -> None:
    identity = simulated_identity(two_hosts.host_a)
    registry = BackgroundOwnerRegistry(
        owner_dir=two_hosts.host_a.runtime_root / "background-owner-identity",
        host_identity=identity,
    )

    payload = registry.owner_payload()

    assert payload["pid"] == identity.pid
    assert payload["process_start_identity"] == identity.process_start_identity
    assert payload["process_start_ticks"] == identity.process_start_ticks


def test_local_service_registry_uses_the_injected_host_identity_process_fields(
    two_hosts: TwoHostFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = simulated_identity(two_hosts.host_a)
    monkeypatch.setattr(registry_module, "process_group_id", lambda _pid: FIXTURE_PGID)
    registry = LocalServiceRegistry(
        two_hosts.host_a.runtime_root,
        LocalServiceSpec("fixture", "fixture.module", "fixture.sock", protocol_version=1),
        host_identity=identity,
    )

    payload = registry._record_from_status({"pid": identity.pid, "version": 1})

    assert payload["stable_host_id"] == identity.stable_host_id
    assert payload["boot_id"] == identity.boot_id
    assert payload["pid"] == identity.pid
    assert payload["process_start_identity"] == identity.process_start_identity
    assert payload["process_start_ticks"] == identity.process_start_ticks


def test_previous_boot_owner_generation_is_not_pruned(two_hosts: TwoHostFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    owner_dir = two_hosts.shared_root / "background-owner-previous-boot"
    registry = BackgroundOwnerRegistry(
        owner_dir=owner_dir,
        pid=FIXTURE_PID,
        host_identity=simulated_identity(two_hosts.host_a),
        clock=lambda: 100.0,
    )
    previous_boot_path = registry.generations_dir / "previous-boot.json"
    write_json(
        previous_boot_path,
        {
            **two_hosts.host_a.process_record(
                generation_id="previous-boot",
                started_at_ns=1,
                last_heartbeat=100.0,
            ),
            "boot_id": "previous-boot-id",
        },
    )

    def fail_lookup(_pid: int):
        raise AssertionError("previous-boot record reached a local process lookup")

    monkeypatch.setattr("yolomux_lib.background_owner.process_start_identity", fail_lookup)
    monkeypatch.setattr("yolomux_lib.background_owner.pid_is_alive", fail_lookup)

    records = registry.live_generation_records()

    assert records == []
    assert previous_boot_path.exists() is True
    assert registry.process_diagnostics[-1]["reason"] == "previous_boot"


def test_foreign_owner_record_blocks_takeover_before_shared_lock_or_rpc(two_hosts: TwoHostFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    owner_dir = two_hosts.shared_root / "background-owner-takeover"
    registry = BackgroundOwnerRegistry(
        owner_dir=owner_dir,
        pid=FIXTURE_PID,
        host_identity=simulated_identity(two_hosts.host_a),
        clock=lambda: 100.0,
    )
    foreign_record = two_hosts.host_b.process_record(
        generation_id="foreign-owner",
        started_at_ns=1,
        last_heartbeat=100.0,
        control_socket=str(owner_dir / "foreign.sock"),
    )
    write_json(registry.owner_path, foreign_record)
    lock_attempts: list[bool] = []
    rpc_attempts: list[bool] = []
    monkeypatch.setattr(registry, "acquire_owner_lock", lambda: lock_attempts.append(True) or True)
    monkeypatch.setattr("yolomux_lib.background_owner.send_yolomux_control_request", lambda *_args, **_kwargs: rpc_attempts.append(True) or {"ok": True})

    assert registry.attempt_takeover() is False
    assert registry.status == "blocked_by_foreign_owner"
    assert registry.last_error == "foreign_host"
    assert lock_attempts == []
    assert rpc_attempts == []
    assert json.loads(registry.owner_path.read_text(encoding="utf-8")) == foreign_record


@pytest.mark.parametrize(
    ("record_identity", "reason_code"),
    [
        ("foreign", "foreign_host"),
        ("previous_boot", "previous_boot"),
    ],
)
def test_owner_release_refuses_noncurrent_process_with_typed_reason(
    two_hosts: TwoHostFixture,
    monkeypatch: pytest.MonkeyPatch,
    record_identity: str,
    reason_code: str,
) -> None:
    registry = BackgroundOwnerRegistry(
        owner_dir=two_hosts.shared_root / f"background-owner-release-{record_identity}",
        pid=FIXTURE_PID,
        host_identity=simulated_identity(two_hosts.host_a),
    )
    source_host = two_hosts.host_b if record_identity == "foreign" else two_hosts.host_a
    owner_record = source_host.process_record(
        generation_id=f"{record_identity}-owner",
        control_socket=str(registry.owner_dir / f"{record_identity}.sock"),
    )
    if record_identity == "previous_boot":
        owner_record["boot_id"] = "previous-boot-id"
    write_json(registry.owner_path, owner_record)
    rpc_attempts: list[bool] = []
    monkeypatch.setattr(
        "yolomux_lib.background_owner.send_yolomux_control_request",
        lambda *_args, **_kwargs: rpc_attempts.append(True) or {"ok": True},
    )

    result = registry.request_current_owner_release()

    assert result["ok"] is False
    assert result["reason_code"] == reason_code
    assert result["diagnostic"]["reason"] == reason_code
    assert rpc_attempts == []


def test_foreign_service_is_not_reclaimed_or_unlinked(two_hosts: TwoHostFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    service_dir = two_hosts.shared_root / "services"
    socket_path = service_dir / "fixture.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.touch()
    registry = LocalServiceRegistry(
        two_hosts.shared_root,
        LocalServiceSpec("fixture", "fixture.module", socket_path.name, protocol_version=2),
        socket_path=socket_path,
        service_dir=service_dir,
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )
    write_json(
        registry.record_path,
        two_hosts.host_b.process_record(
            service="fixture",
            socket=str(socket_path),
            protocol_version=1,
        ),
    )
    monkeypatch.setattr(registry_module, "pid_is_alive", lambda _pid: False)
    requests: list[str] = []

    def request(method: str, payload=None, timeout: float = 0.2):
        requests.append(method)
        if method == "ping":
            return {"ok": True, "pid": FIXTURE_PID, "version": 1}
        return {"ok": True}

    monkeypatch.setattr(registry, "_request", request)

    def reconcile(host: SimulatedHost, barrier: threading.Barrier):
        barrier.wait(timeout=2.0)
        if host == two_hosts.host_a:
            registry._retire_incompatible_service()
        barrier.wait(timeout=2.0)
        return registry.record_path.exists(), socket_path.exists()

    results = run_concurrently(reconcile, two_hosts)
    assert "shutdown" not in requests
    assert results == [(True, True), (True, True)]


def test_foreign_orphans_are_not_signalled_or_reaped(two_hosts: TwoHostFixture) -> None:
    lease_path = two_hosts.shared_root / "server-leases" / f"{FIXTURE_PORT}.lock"
    write_json(lease_path, two_hosts.host_b.process_record(port=FIXTURE_PORT))
    table = {
        FIXTURE_PID + 1: registry_module.ProcessTableEntry(
            ppid=1,
            pgid=FIXTURE_PGID,
            cpu_seconds=0.0,
            command="tmux client",
        )
    }
    signals: list[tuple[int, int]] = []

    def reconcile(host: SimulatedHost, barrier: threading.Barrier):
        barrier.wait(timeout=2.0)
        result = None
        if host == two_hosts.host_a:
            result = preflight_module.preflight_port(
                FIXTURE_PORT,
                two_hosts.shared_root,
                table,
                kill=lambda pid, signum: signals.append((pid, signum)),
                table_reader=lambda: table,
                sleep=lambda _seconds: None,
            )
        barrier.wait(timeout=2.0)
        return result

    results = run_concurrently(reconcile, two_hosts)
    result = results[0]
    assert result is not None
    assert signals == []
    assert result["reaped_pids"] == []
    assert result["diagnostic"]["reason"] == "foreign_host"


@pytest.mark.xfail(strict=True, reason="host-qualified persisted tmux keys land after the shared identity parent")
def test_same_tmux_target_has_distinct_host_keys(two_hosts: TwoHostFixture) -> None:
    pane = fixture_pane()

    def key(_host: SimulatedHost, barrier: threading.Barrier) -> str:
        barrier.wait(timeout=2.0)
        return tmux_pane_graph_id(pane)

    keys = run_concurrently(key, two_hosts)
    assert len(set(keys)) == 2


@pytest.mark.xfail(strict=True, reason="host-local cache and database layout is explicitly out of scope")
def test_cache_and_database_paths_are_distinct_per_host(two_hosts: TwoHostFixture) -> None:
    def paths(_host: SimulatedHost, barrier: threading.Barrier) -> tuple[Path, Path]:
        barrier.wait(timeout=2.0)
        return common.YOLOMUX_CACHE_DIR, common.MODEL_PRICING_DATABASE_PATH

    paths = run_concurrently(paths, two_hosts)
    assert paths[0][0] != paths[1][0]
    assert paths[0][1] != paths[1][1]

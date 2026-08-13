"""Focused ownership regressions for launch preflight cleanup."""

from __future__ import annotations

import json
import os
import signal

from tests.helpers.local_service_records import FixtureLeaseRecordBuilder
from tests.helpers.local_service_records import FixtureLocalServiceRecordBuilder
from tests.helpers.local_service_records import FixtureProcessRecordBuilder
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib.local_services import preflight as preflight_module
from yolomux_lib.local_services.preflight import preflight_port
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services.registry import ProcessTableEntry
from yolomux_lib.local_services.registry import ProcessTableUnavailable
from yolomux_lib.local_services.registry import shutdown_owned_local_services


def _table(rows):
    return {
        pid: ProcessTableEntry(ppid, pgid, cpu_seconds, command, start_time)
        for pid, ppid, pgid, cpu_seconds, command, *start in rows
        for start_time in [start[0] if start else pid + 1000]
    }


def _process_record(pid, start_time=None):
    return FixtureProcessRecordBuilder(pid=pid, process_start_ticks=start_time).build()


def _lease(tmp_path, port=18991, pid=400, pgid=400, host_id=None, *, include_identity=True, start_time=None):
    return FixtureLeaseRecordBuilder(
        pid=pid,
        pgid=pgid,
        port=port,
        process_start_ticks=start_time,
        include_identity=include_identity,
    ).write(tmp_path, host_id=host_id or "")


def _service_record(tmp_path, *, service, pid, socket_path, launcher_pid, launcher_port, protocol_version=1):
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / f"{service}.service.json").write_text(
        json.dumps(
            FixtureLocalServiceRecordBuilder(
                service=service,
                socket_path=socket_path,
                pid=pid,
                fields={
                    "launcher_pid": launcher_pid,
                    "launcher_port": launcher_port,
                    "protocol_version": protocol_version,
                },
            ).build()
        ),
        encoding="utf-8",
    )


def test_preflight_cli_uses_the_runtime_lease_root(monkeypatch, tmp_path):
    captured = {}

    def capture(port, state_dir):
        captured.update(port=port, state_dir=state_dir)
        return {"ok": True, "reason": "clear"}

    monkeypatch.setattr(preflight_module, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(preflight_module, "preflight_port", capture)

    assert preflight_module.main(["--port", "18991"]) == 0
    assert captured == {"port": 18991, "state_dir": tmp_path}


def test_preflight_never_kills_a_pgid_reuse_candidate_without_positive_identity(tmp_path):
    """A stale lease cannot authorize TERM for an unrelated orphaned process."""
    _lease(tmp_path)
    candidate_pid = 900
    table = _table([(candidate_pid, 1, 400, 1.0, "foreign-daemon --serve")])
    kills = []

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _seconds: None,
    )

    assert result["reaped_pids"] == []
    assert kills == []


def test_preflight_fails_closed_for_an_old_lease_without_member_identity(tmp_path):
    """Stale deployed leases predate the member snapshot and authorize no signal."""
    _lease(tmp_path, include_identity=False)
    table = _table([(410, 1, 400, 1.0, "tmux -C attach-session -t x")])
    kills = []

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _seconds: None,
    )

    assert result["reaped_pids"] == []
    assert result["ok"] is False
    assert result["reason_code"] == "process_identity_refused"
    assert result["diagnostic"]["reason"] == "missing_host_identity"
    assert kills == []


def test_preflight_refuses_launch_when_an_exact_stale_orphan_cannot_be_reaped(tmp_path):
    FixtureLeaseRecordBuilder(
        pid=400,
        pgid=400,
        port=18991,
        members=({"pid": 410, "start_time": 1000},),
    ).write(tmp_path)
    table = {410: ProcessTableEntry(1, 400, 1.0, "tmux -C attach-session -t x", 1000)}
    kills = []

    def deny_kill(pid, sig):
        kills.append((pid, sig))
        raise PermissionError("denied")

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=deny_kill,
        table_reader=lambda: table,
        sleep=lambda _seconds: None,
    )

    assert result == {
        "ok": False,
        "reason": "failed to reconcile 1 stale orphan(s) of the dead previous owner",
        "reason_code": "stale_orphan_reap_failed",
        "tracked_pids": [410],
        "reaped_pids": [],
        "failures": {"410": "stale_orphan_kill_permission_denied"},
    }
    assert kills == [(410, signal.SIGTERM), (410, signal.SIGKILL)]


def test_preflight_refuses_launch_when_stale_orphan_reconciliation_cannot_be_verified(tmp_path):
    FixtureLeaseRecordBuilder(
        pid=400,
        pgid=400,
        port=18991,
        members=({"pid": 410, "start_time": 1000},),
    ).write(tmp_path)
    table = {410: ProcessTableEntry(1, 400, 1.0, "tmux -C attach-session -t x", 1000)}

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda _pid, _sig: None,
        table_reader=lambda: (_ for _ in ()).throw(ProcessTableUnavailable("process_table_read_failed")),
        sleep=lambda _seconds: None,
    )

    assert result == {
        "ok": False,
        "reason": "process_table_read_failed",
        "reason_code": "process_table_unavailable",
        "tracked_pids": [410],
        "reaped_pids": [],
        "failures": {},
    }


def test_preflight_does_not_reap_a_reused_pid_with_a_different_start_time(tmp_path):
    """A numeric PID/PGID match cannot outlive the member's recorded start tick."""
    FixtureLeaseRecordBuilder(
        pid=400,
        pgid=400,
        port=18991,
        members=({"pid": 410, "start_time": 1000},),
    ).write(tmp_path)
    table = {410: ProcessTableEntry(1, 400, 1.0, "foreign-daemon --serve", 1001)}
    kills = []

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _seconds: None,
    )

    assert result["reaped_pids"] == []
    assert kills == []


def test_live_owner_snapshots_exact_member_start_times_into_its_lease(tmp_path):
    """The owner snapshots identity before a later death makes its group ambiguous."""
    owner_pid = os.getpid()
    member_pid = owner_pid + 1
    foreign_pid = owner_pid + 2
    lease_path = _lease(
        tmp_path,
        pid=owner_pid,
        pgid=700,
        host_id=current_host_identity().stable_host_id,
        start_time=1000,
    )
    table = {
        owner_pid: ProcessTableEntry(1, 700, 1.0, "python3 yolomux.py --port 18991", 1000),
        member_pid: ProcessTableEntry(owner_pid, 700, 1.0, "tmux -C attach-session -t x", 1001),
        foreign_pid: ProcessTableEntry(1, 900, 1.0, "foreign-daemon", 1002),
    }

    assert registry_mod.record_live_port_members(18991, tmp_path, table) is True
    record = json.loads(lease_path.read_text(encoding="utf-8"))

    assert record["members"] == [
        {"pid": owner_pid, "start_time": 1000},
        {"pid": member_pid, "start_time": 1001},
    ]


def test_preflight_reaps_exact_recorded_sidecar_after_its_launcher_dies(tmp_path):
    """A separate service session is still reclaimable from its exact socket record."""
    _lease(tmp_path)
    socket_path = tmp_path / "services" / "approvald.sock"
    _service_record(
        tmp_path,
        service="approvald",
        pid=500,
        socket_path=socket_path,
        launcher_pid=400,
        launcher_port=18991,
    )
    table = _table(
        [
            (500, 1, 500, 1.0, f"python3 -m yolomux_lib.approval.approvald --serve --socket {socket_path}"),
            (501, 500, 500, 1.0, "python3 -c multiprocessing-spawn-worker"),
            (900, 1, 900, 1.0, "foreign-daemon --serve"),
        ]
    )
    survivors = _table([(500, 1, 500, 1.0, f"python3 -m yolomux_lib.approval.approvald --serve --socket {socket_path}")])
    reads = [survivors]
    kills = []

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: reads.pop(0) if reads else survivors,
        sleep=lambda _seconds: None,
        service_status_reader=lambda group: {"ok": True, "pid": group["pid"], "clients": 0},
    )

    assert result["reaped_pids"] == [500, 501]
    assert kills == [
        (500, signal.SIGTERM),
        (501, signal.SIGTERM),
        (500, signal.SIGKILL),
    ]


def test_preflight_keeps_a_recorded_sidecar_with_a_live_client(tmp_path):
    """Launcher provenance never overrides a shared service's active lease."""
    _lease(tmp_path)
    socket_path = tmp_path / "services" / "jobd.sock"
    _service_record(
        tmp_path,
        service="jobd",
        pid=600,
        socket_path=socket_path,
        launcher_pid=400,
        launcher_port=18991,
    )
    table = _table([(600, 1, 600, 1.0, f"python3 -m yolomux_lib.infra.jobd --serve --socket {socket_path}")])
    kills = []

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _seconds: None,
        service_status_reader=lambda group: {"ok": True, "pid": group["pid"], "clients": 1},
    )

    assert result["reaped_pids"] == []
    assert kills == []


def test_preflight_keeps_an_old_sidecar_record_without_status_protocol(tmp_path):
    """An old service record is not enough evidence to signal its process group."""
    _lease(tmp_path)
    socket_path = tmp_path / "services" / "statsd.sock"
    _service_record(
        tmp_path,
        service="statsd",
        pid=700,
        socket_path=socket_path,
        launcher_pid=400,
        launcher_port=18991,
        protocol_version=0,
    )
    table = _table([(700, 1, 700, 1.0, f"python3 -m yolomux_lib.stats_current.service --serve --socket {socket_path}")])
    kills = []

    result = preflight_port(
        18991,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _seconds: None,
    )

    assert result["reaped_pids"] == []
    assert kills == []


def test_orderly_shutdown_reaps_only_current_identity_verified_sidecars(tmp_path):
    """An orderly launcher exit reaps only its exact recorded service groups."""
    own_socket = tmp_path / "services" / "statsd.sock"
    foreign_socket = tmp_path / "services" / "statusd.sock"
    _service_record(
        tmp_path,
        service="statsd",
        pid=500,
        socket_path=own_socket,
        launcher_pid=400,
        launcher_port=18991,
    )
    _service_record(
        tmp_path,
        service="statusd",
        pid=600,
        socket_path=foreign_socket,
        launcher_pid=401,
        launcher_port=18991,
    )
    initial = _table([
        (500, 400, 500, 1.0, f"python3 -m statsd --serve --socket {own_socket}"),
        (501, 500, 500, 1.0, "python3 -c worker"),
        (600, 401, 600, 1.0, f"python3 -m statusd --serve --socket {foreign_socket}"),
    ])
    survivors = _table([
        (500, 1, 500, 1.0, f"python3 -m statsd --serve --socket {own_socket}"),
        (501, 500, 500, 1.0, "python3 -c worker"),
        (600, 401, 600, 1.0, f"python3 -m statusd --serve --socket {foreign_socket}"),
    ])
    kills = []

    result = shutdown_owned_local_services(
        18991,
        tmp_path / "services",
        launcher_pid=400,
        table_reader=lambda: initial if not kills else survivors,
        kill=lambda pid, sig: kills.append((pid, sig)),
        sleep=lambda _seconds: None,
    )

    assert result["terminated"] == [500, 501]
    assert kills == [
        (500, signal.SIGTERM),
        (500, signal.SIGKILL),
        (501, signal.SIGKILL),
    ]

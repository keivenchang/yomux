import json
import signal

from tests.helpers.local_service_records import FixtureLeaseRecordBuilder
from tests.helpers.local_service_records import FixtureLocalServiceRecordBuilder
from tests.helpers.local_service_records import FixtureProcessRecordBuilder
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services.preflight import preflight_port
from yolomux_lib.local_services.watchdog import GroupOverloadWatchdog


def _table(rows):
    return {
        pid: registry_mod.ProcessTableEntry(ppid, pgid, cpu_seconds, command, start_time)
        for pid, ppid, pgid, cpu_seconds, command, *start in rows
        for start_time in [start[0] if start else pid + 1000]
    }


def _process_record(pid):
    return FixtureProcessRecordBuilder(pid=pid).build()


def _tracked_state(tmp_path, port=8881, web_pid=400, *, socket_name="jobd.sock", payload=None):
    FixtureLeaseRecordBuilder(pid=web_pid, pgid=web_pid, port=port).write(tmp_path)
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    jobd_socket = service_dir / socket_name
    record = FixtureLocalServiceRecordBuilder(service="jobd", socket_path=jobd_socket).build()
    if payload is not None:
        record["payload"] = payload
    (service_dir / "jobd.service.json").write_text(json.dumps(record), encoding="utf-8")
    return service_dir, jobd_socket


def _rows(jobd_socket, web_cpu, worker_cpu, extra=(), command_payload=""):
    return [
        (400, 1, 400, web_cpu, f"python3 -u yolomux.py 8880 /tmp/log --host 0.0.0.0 --port 8881 --dang --dev {command_payload}"),
        (500, 1, 500, 1.0, f"python3 -m yolomux_lib.jobd --serve --socket {jobd_socket} --idle-seconds 60 {command_payload}"),
        (501, 500, 500, worker_cpu, "python3 -c multiprocessing-spawn-worker"),
        # An untracked high-CPU bystander (Defender-shaped): never touched.
        (900, 1, 900, 100000.0, "/Applications/Microsoft Defender.app/Contents/MacOS/wdavdaemon"),
        *extra,
    ]


def _watchdog(tmp_path, service_dir, tables, kills, *, sustained=3, limit=250.0, max_children=32):
    clock_state = {"now": 0.0}

    def clock():
        clock_state["now"] += 1.0
        return clock_state["now"]

    def table_reader():
        return tables.pop(0) if len(tables) > 1 else tables[0]

    def kill(pid, signum):
        kills.append((pid, signum))

    return GroupOverloadWatchdog(
        port=8881,
        state_dir=tmp_path,
        service_dir=service_dir,
        cpu_percent_limit=limit,
        sustained_samples=sustained,
        grace_seconds=0.0,
        max_tracked_children=max_children,
        evidence_dir=tmp_path,
        table_reader=table_reader,
        kill=kill,
        clock=clock,
        sleep=lambda _seconds: None,
    )


def test_sustained_overload_terms_leaders_and_kills_only_stilllive_tracked_pids(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    # CPU grows 10 cpu-seconds per 1s clock tick => 1000% >> the 250% limit.
    tables = [
        _table(_rows(jobd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    # After SIGTERM + grace, only the worker survives for the targeted SIGKILL pass.
    survivors = _table([(501, 500, 500, 999.0, "python3 -c multiprocessing-spawn-worker"), (900, 1, 900, 100000.0, "wdavdaemon")])
    tables.append(survivors)
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=3)

    snapshots = [watchdog.sample_once() for _ in range(4)]

    assert watchdog.fired is True
    assert kills == [(400, signal.SIGTERM), (500, signal.SIGTERM), (501, signal.SIGKILL)]
    fired = snapshots[-1]
    assert fired["fired"] is True
    assert all(action["pid"] != 900 for action in fired["actions"])
    # Firing is once-only: further overload samples take no more actions.
    tables.append(survivors)
    assert "actions" not in watchdog.sample_once()


def test_watchdog_excludes_foreign_service_and_reports_typed_reason(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    record_path = service_dir / "jobd.service.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["stable_host_id"] = "fixture-foreign-host"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    tables = [
        _table(_rows(jobd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    tables.append(_table([]))
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    assert kills == [(400, signal.SIGTERM)]
    diagnostics = watchdog.last_snapshot["process_diagnostics"]
    assert [(row["target"], row["pid"], row["diagnostic"]["reason"]) for row in diagnostics] == [
        ("jobd", 500, "foreign_host")
    ]
    assert diagnostics[0]["record_path"] == str(record_path)


def test_watchdog_refuses_recycled_member_before_sigkill(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    tables = [
        _table(_rows(jobd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    tables.append(_table([(501, 500, 500, 999.0, "python3 -c multiprocessing-spawn-worker", 9999)]))
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    assert kills == [(400, signal.SIGTERM), (500, signal.SIGTERM)]
    refusal = watchdog.last_snapshot["actions"][-1]
    assert refusal["action"] == "fence-refused"
    assert refusal["requested_action"] == "sigkill"
    assert refusal["pid"] == 501
    assert refusal["diagnostic"]["reason"] == "process_identity_reused"


def test_below_threshold_and_fluctuating_load_never_fires(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    # 0.5 cpu-seconds per 1s tick = 100% total: below the 250% limit.
    tables = [_table(_rows(jobd_socket, web_cpu=0.5 * step, worker_cpu=0.5 * step)) for step in range(10)]
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=2)

    for _ in range(8):
        watchdog.sample_once()

    assert watchdog.fired is False
    assert kills == []
    assert watchdog.last_snapshot["over_count"] == 0


def test_short_spike_resets_the_sustained_counter(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    cpu_points = [0.0, 10.0, 10.5, 11.0]  # one 1000% spike, then ~50%
    tables = [_table(_rows(jobd_socket, web_cpu=cpu, worker_cpu=0.0)) for cpu in cpu_points]
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=2)

    for _ in range(4):
        watchdog.sample_once()

    assert watchdog.fired is False
    assert kills == []


def test_shared_service_veto_skips_daemons_when_another_web_port_is_live(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    other_port = (950, 1, 950, 5.0, "python3 -u yolomux.py 8880 /tmp/log --host 0.0.0.0 --port 8880 --dang")
    tables = [
        _table(_rows(jobd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step, extra=[other_port]))
        for step in range(4)
    ]
    tables.append(_table([other_port]))
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    assert watchdog.fired is True
    # Web leader stopped; the shared jobd group is skipped and reported, never signalled.
    assert kills == [(400, signal.SIGTERM)]
    skipped = [action for action in watchdog.last_snapshot["actions"] if action["action"] == "skipped-shared"]
    assert [action["target"] for action in skipped] == ["jobd"]


def test_membership_change_yields_honest_unknown_cpu_sample(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    tables = [
        _table(_rows(jobd_socket, web_cpu=0.0, worker_cpu=0.0)),
        _table(_rows(jobd_socket, web_cpu=100.0, worker_cpu=100.0)[:2]),  # worker vanished
    ]
    watchdog = _watchdog(tmp_path, service_dir, tables, [], sustained=2)

    watchdog.sample_once()
    snapshot = watchdog.sample_once()

    assert snapshot["cpu_percent"] is None
    assert watchdog.fired is False


def test_tracked_child_count_breach_fires_containment(tmp_path):
    service_dir, jobd_socket = _tracked_state(tmp_path)
    runaway_children = [(600 + index, 400, 400, 1.0, "python3 -c child") for index in range(40)]
    tables = [_table(_rows(jobd_socket, web_cpu=0.0, worker_cpu=0.0, extra=runaway_children))]
    kills = []
    watchdog = _watchdog(tmp_path, service_dir, tables, kills, sustained=2, max_children=32)

    watchdog.sample_once()
    watchdog.sample_once()

    assert watchdog.fired is True
    assert (400, signal.SIGTERM) in kills


def _lease(tmp_path, port=8881, pid=400, pgid=400, members=None):
    FixtureLeaseRecordBuilder(
        pid=pid,
        pgid=pgid,
        port=port,
        members=tuple(members or ()),
    ).write(tmp_path)


def test_preflight_refuses_a_wedged_live_owner(tmp_path):
    _lease(tmp_path)
    table = _table([(400, 1, 400, 50.0, "python3 -u yolomux.py 8880 /tmp/log --port 8881 --dang")])
    kills = []

    result = preflight_port(8881, tmp_path, table, kill=lambda pid, sig: kills.append((pid, sig)), table_reader=lambda: table, sleep=lambda _s: None)

    assert result["ok"] is False
    assert "wedged" in result["reason"]
    assert result["tracked_pids"] == [400]
    assert kills == []


def test_preflight_reaps_only_verified_orphans_of_a_dead_owner(tmp_path):
    # Owner 400 is dead. The member snapshot was written while it was alive,
    # so only the matching orphan 410 is eligible; 411 still has a live parent
    # and 900 was never an identified owner-group member.
    _lease(tmp_path, members=[{"pid": 410, "start_time": 1010}])
    table = _table(
        [
            (410, 1, 400, 1.0, "tmux -C attach-session -t x", 1010),
            (411, 350, 400, 1.0, "python3 helper.py", 1011),
            (900, 1, 900, 999.0, "wdavdaemon", 1012),
        ]
    )
    survivors = _table([(410, 1, 400, 1.0, "tmux -C attach-session -t x", 1010)])
    reads = [survivors]
    kills = []

    result = preflight_port(8881, tmp_path, table, kill=lambda pid, sig: kills.append((pid, sig)), table_reader=lambda: reads.pop(0) if reads else survivors, sleep=lambda _s: None)

    assert result["ok"] is True
    assert result["reaped_pids"] == [410]
    assert kills == [(410, signal.SIGTERM), (410, signal.SIGKILL)]


def test_preflight_is_clear_with_no_lease_or_leftovers(tmp_path):
    result = preflight_port(8881, tmp_path, _table([]), kill=lambda *_: None, table_reader=lambda: _table([]), sleep=lambda _s: None)

    assert result == {
        "ok": True,
        "reason": "clear to launch",
        "reason_code": "clear_to_launch",
        "tracked_pids": [],
        "reaped_pids": [],
        "failures": {},
    }


def test_preflight_leaves_a_still_serving_service_alone_when_only_its_launcher_crashed(tmp_path):
    """A service's launcher dying is not itself grounds for a restart or a kill.

    Only a genuinely idle group left by a dead launcher gets reconciled; a
    service still answering client work must be left untouched, and only
    the previous restart-hostile silent-orphan behavior would have differed.
    """
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    jobd_socket = service_dir / "jobd.sock"
    record = FixtureLocalServiceRecordBuilder(
        service="jobd",
        socket_path=jobd_socket,
        pid=500,
        fields={"launcher_pid": 700, "launcher_port": 8881, "protocol_version": 1},
    ).build()
    (service_dir / "jobd.service.json").write_text(json.dumps(record), encoding="utf-8")
    # The launcher (700) is dead -- absent from the table -- but the service
    # (500) itself is alive and, in this case, still serving a client.
    table = _table([(500, 1, 500, 1.0, f"python3 -m yolomux_lib.jobd --serve --socket {jobd_socket} --idle-seconds 60")])
    kills = []

    result = preflight_port(
        8881,
        tmp_path,
        table,
        kill=lambda pid, sig: kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _s: None,
        service_status_reader=lambda _group: {"ok": True, "pid": 500, "clients": 1},
    )

    assert result["ok"] is True
    assert result["reaped_pids"] == []
    assert kills == []

    # Companion: the same dead-launcher group, but genuinely idle this time --
    # proving the first case's silence is the "has a client" discriminator,
    # not a fixture bug that would have never reaped anything either way.
    idle_kills = []
    idle_result = preflight_port(
        8881,
        tmp_path,
        table,
        kill=lambda pid, sig: idle_kills.append((pid, sig)),
        table_reader=lambda: table,
        sleep=lambda _s: None,
        service_status_reader=lambda _group: {"ok": True, "pid": 500, "clients": 0},
    )

    assert idle_result["ok"] is True
    assert idle_result["reaped_pids"] == [500]
    assert idle_kills == [(500, signal.SIGTERM), (500, signal.SIGKILL)]


def test_evidence_summary_is_bounded_and_redacted(tmp_path):
    command_canary = "command-canary-8f1a3c7d"
    path_canary = "socket-path-canary-4b9e2d6a"
    payload_canary = "payload-canary-6c2f8a1e"
    service_dir, jobd_socket = _tracked_state(
        tmp_path,
        socket_name=f"{path_canary}.sock",
        payload=payload_canary,
    )
    tables = [
        _table(_rows(jobd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step, command_payload=command_canary))
        for step in range(4)
    ]
    tables.append(_table([]))
    watchdog = _watchdog(tmp_path, service_dir, tables, [], sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    evidence_path = watchdog.last_snapshot["evidence_path"]
    assert evidence_path
    summary = json.loads((tmp_path / evidence_path.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
    assert set(summary) == {
        "actions",
        "cpu_percent_history",
        "cpu_percent_limit",
        "port",
        "reason",
        "shared_service_veto",
        "sustained_samples",
        "version",
        "written_at",
    }
    assert summary["version"] == 1
    assert summary["port"] == 8881
    assert summary["reason"] == "sustained tracked-group overload"
    assert summary["cpu_percent_limit"] == 250.0
    assert summary["sustained_samples"] == 3
    assert summary["cpu_percent_history"] == [2000.0, 2000.0, 2000.0]
    assert summary["shared_service_veto"] is False
    assert isinstance(summary["written_at"], float)
    assert summary["actions"] == [
        {"target": "web", "pid": 400, "action": "sigterm", "result": "signalled"},
        {"target": "jobd", "pid": 500, "action": "sigterm", "result": "signalled"},
    ]

    def leaves(value):
        if isinstance(value, dict):
            for child in value.values():
                yield from leaves(child)
        elif isinstance(value, list):
            for child in value:
                yield from leaves(child)
        else:
            yield value

    output_leaves = [str(value) for value in leaves(summary)]
    for canary in (command_canary, path_canary, payload_canary):
        assert all(canary not in value for value in output_leaves)

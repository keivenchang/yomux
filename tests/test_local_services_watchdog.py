import json
import signal
import time
from pathlib import Path

import pytest

from tests.helpers.local_service_records import FixtureLeaseRecordBuilder
from tests.helpers.local_service_records import FixtureLocalServiceRecordBuilder
from tests.helpers.local_service_records import FixtureProcessRecordBuilder
from yolomux_lib.infra.process_claims import CLAIM_REASON_SUPERVISOR_ALIVE
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib.local_services import registry as registry_mod
from yolomux_lib.local_services import watchdog as watchdog_mod
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


# A pid on THIS host and boot that the shared identity fence proves is not
# running, so `may_remove_stale_record` is true and the recorded supervisor
# counts as gone. Every containment fixture in this module needs one: a service
# whose supervisor is not provably gone belongs to that supervisor, and this
# watchdog retains it.
GONE_SUPERVISOR_PID = 2_147_483_647


def _gone_supervisor():
    return current_host_identity().process_record_fields(
        pid=GONE_SUPERVISOR_PID,
        start_identity=f"proc:{GONE_SUPERVISOR_PID}",
    )


def _tracked_state(tmp_path, port=8881, web_pid=400, *, socket_name="batchd.sock", payload=None, supervisor=None):
    FixtureLeaseRecordBuilder(pid=web_pid, pgid=web_pid, port=port).write(tmp_path)
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    batchd_socket = service_dir / socket_name
    record = FixtureLocalServiceRecordBuilder(service="batchd", socket_path=batchd_socket).build()
    if payload is not None:
        record["payload"] = payload
    record["supervisor"] = _gone_supervisor() if supervisor is None else supervisor
    (service_dir / "batchd.service.json").write_text(json.dumps(record), encoding="utf-8")
    return service_dir, batchd_socket


def _rows(batchd_socket, web_cpu, worker_cpu, extra=(), command_payload=""):
    return [
        (400, 1, 400, web_cpu, f"python3 -u yolomux.py 8880 /tmp/log --host 0.0.0.0 --port 8881 --dang --dev {command_payload}"),
        (500, 1, 500, 1.0, f"python3 -m yolomux_lib.batchd --serve --socket {batchd_socket} --idle-seconds 60 {command_payload}"),
        (501, 500, 500, worker_cpu, "python3 -c multiprocessing-spawn-worker"),
        # An untracked high-CPU bystander (Defender-shaped): never touched.
        (900, 1, 900, 100000.0, "/Applications/Microsoft Defender.app/Contents/MacOS/wdavdaemon"),
        *extra,
    ]


def _install_live_process_view(monkeypatch, live_table):
    """Answer the escalation's mid-flight identity re-proof from a fixture table.

    ``GroupOverloadWatchdog._identity_replaced`` deliberately re-proves each
    authorized pid against the LIVE system rather than against the snapshot the
    decision was taken on: a pid recycled between the SIGTERM and the SIGKILL is
    a different process and must be yielded to, not force-killed. Every pid in
    this module is synthetic, so without this the answer would come from
    whatever really holds pid 400 on the machine running the suite. Only the
    no-table call is redirected -- a caller that passes its own snapshot (the
    authorization itself) still gets exactly that snapshot.
    """

    real_diagnostic = watchdog_mod.process_record_diagnostic

    def diagnostic(record, *, host_identity=None, table=None):
        return real_diagnostic(
            record,
            host_identity=host_identity,
            table=live_table if table is None else table,
        )

    monkeypatch.setattr(watchdog_mod, "process_record_diagnostic", diagnostic)


def _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, *, sustained=3, limit=250.0, max_children=32):
    clock_state = {"now": 0.0}

    def clock():
        clock_state["now"] += 1.0
        return clock_state["now"]

    def table_reader():
        return tables.pop(0) if len(tables) > 1 else tables[0]

    def kill(pid, signum):
        kills.append((pid, signum))

    # The process group the KERNEL would report for each of these synthetic pids.
    # `live_process_group` deliberately asks `os.getpgid` rather than the process
    # table the group was resolved from, so the fixture has to answer that second
    # question too. A pid this fixture never described returns None, which is
    # "unproven" and refuses -- see
    # `test_watchdog_refuses_a_target_whose_live_process_group_cannot_be_proven`.
    known_groups = {pid: entry.pgid for snapshot in tables for pid, entry in snapshot.items()}
    _install_live_process_view(monkeypatch, tables[-1])

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
        process_group_reader=lambda pid: known_groups.get(int(pid)),
        # The escalation's liveness poll. The product default (`pid_is_serving`)
        # would ask the real /proc about pids this fixture invented, so it reads
        # the same process-table snapshots every other observation here comes
        # from -- exactly what the containment loop used to do explicitly.
        liveness_reader=lambda pid: int(pid) in table_reader(),
        kill=kill,
        clock=clock,
        sleep=lambda _seconds: None,
    )


def test_sustained_overload_terms_leaders_and_kills_only_stilllive_tracked_pids(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    # CPU grows 10 cpu-seconds per 1s clock tick => 1000% >> the 250% limit.
    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    # After SIGTERM + grace, only the worker survives for the targeted SIGKILL pass.
    survivors = _table([(501, 500, 500, 999.0, "python3 -c multiprocessing-spawn-worker"), (900, 1, 900, 100000.0, "wdavdaemon")])
    tables.append(survivors)
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=3)

    snapshots = [watchdog.sample_once() for _ in range(4)]

    assert watchdog.fired is True
    assert kills == [(400, signal.SIGTERM), (500, signal.SIGTERM), (501, signal.SIGKILL)]
    fired = snapshots[-1]
    assert fired["fired"] is True
    assert all(action["pid"] != 900 for action in fired["actions"])
    # Firing is once-only: further overload samples take no more actions.
    tables.append(survivors)
    assert "actions" not in watchdog.sample_once()


def test_watchdog_excludes_foreign_service_and_reports_typed_reason(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    record_path = service_dir / "batchd.service.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["stable_host_id"] = "fixture-foreign-host"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    tables.append(_table([]))
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    assert kills == [(400, signal.SIGTERM)]
    diagnostics = watchdog.last_snapshot["process_diagnostics"]
    assert [(row["target"], row["pid"], row["diagnostic"]["reason"]) for row in diagnostics] == [
        ("batchd", 500, "foreign_host")
    ]
    assert diagnostics[0]["record_path"] == str(record_path)


def test_watchdog_refuses_recycled_member_before_sigkill(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    tables.append(_table([(501, 500, 500, 999.0, "python3 -c multiprocessing-spawn-worker", 9999)]))
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    assert kills == [(400, signal.SIGTERM), (500, signal.SIGTERM)]
    # The one destructive owner re-proves each authorized identity against the
    # LIVE view before it fires, so a member whose pid was recycled is refused
    # with zero signals rather than being force-killed. The leaders above are
    # the positive control: the same pass really does signal what it proved.
    refusal = watchdog.last_snapshot["actions"][-1]
    assert refusal["pid"] == 501
    assert refusal["target"] == "tracked-member"
    assert refusal["result"] == "refused"
    assert refusal["attempted_action"] == "none"
    assert refusal["signals"] == []
    assert refusal["confirmed_dead"] is False
    assert refusal["reason"] == "process_identity_reused"
    assert refusal["failed_dimension"] == "process_start_identity"


def test_below_threshold_and_fluctuating_load_never_fires(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    # 0.5 cpu-seconds per 1s tick = 100% total: below the 250% limit.
    tables = [_table(_rows(batchd_socket, web_cpu=0.5 * step, worker_cpu=0.5 * step)) for step in range(10)]
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=2)

    for _ in range(8):
        watchdog.sample_once()

    assert watchdog.fired is False
    assert kills == []
    assert watchdog.last_snapshot["over_count"] == 0


def test_short_spike_resets_the_sustained_counter(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    cpu_points = [0.0, 10.0, 10.5, 11.0]  # one 1000% spike, then ~50%
    tables = [_table(_rows(batchd_socket, web_cpu=cpu, worker_cpu=0.0)) for cpu in cpu_points]
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=2)

    for _ in range(4):
        watchdog.sample_once()

    assert watchdog.fired is False
    assert kills == []


def test_shared_service_veto_skips_daemons_when_another_web_port_is_live(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    other_port = (950, 1, 950, 5.0, "python3 -u yolomux.py 8880 /tmp/log --host 0.0.0.0 --port 8880 --dang")
    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step, extra=[other_port]))
        for step in range(4)
    ]
    tables.append(_table([other_port]))
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    assert watchdog.fired is True
    # Web leader stopped; the shared batchd group is skipped and reported, never signalled.
    assert kills == [(400, signal.SIGTERM)]
    # Only the veto rows still carry an "action" key; every row the destructive
    # owner produced is a `TerminationOutcome`, so this must select rather than
    # assume a shape.
    skipped = [action for action in watchdog.last_snapshot["actions"] if action.get("action") == "skipped-shared"]
    assert [action["target"] for action in skipped] == ["batchd"]


def test_membership_change_yields_honest_unknown_cpu_sample(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    tables = [
        _table(_rows(batchd_socket, web_cpu=0.0, worker_cpu=0.0)),
        _table(_rows(batchd_socket, web_cpu=100.0, worker_cpu=100.0)[:2]),  # worker vanished
    ]
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, [], sustained=2)

    watchdog.sample_once()
    snapshot = watchdog.sample_once()

    assert snapshot["cpu_percent"] is None
    assert watchdog.fired is False


def test_tracked_child_count_breach_fires_containment(monkeypatch, tmp_path):
    service_dir, batchd_socket = _tracked_state(tmp_path)
    runaway_children = [(600 + index, 400, 400, 1.0, "python3 -c child") for index in range(40)]
    tables = [_table(_rows(batchd_socket, web_cpu=0.0, worker_cpu=0.0, extra=runaway_children))]
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=2, max_children=32)

    watchdog.sample_once()
    watchdog.sample_once()

    assert watchdog.fired is True
    assert (400, signal.SIGTERM) in kills


@pytest.mark.parametrize(
    "live_group, expected_kills, expected_reason",
    [
        ("unreadable", [], "process_identity_unavailable"),
        ("foreign", [], "dimension_changed"),
        ("recorded", [(400, signal.SIGTERM), (500, signal.SIGTERM), (501, signal.SIGKILL)], "authorized"),
    ],
    ids=["unreadable_group_refuses", "changed_group_refuses", "proven_group_contains"],
)
def test_watchdog_refuses_a_target_whose_live_process_group_cannot_be_proven(
    monkeypatch, tmp_path, live_group, expected_kills, expected_reason
):
    """The substituted group dimension is REQUIRED, and it is really re-read live.

    A tracked-group target holds no socket, no record of its own and no spawn
    generation, so the group scope binds the one proof it does carry: the
    process group it still shares with a leader proven from a persisted record.
    That group is read from a DIFFERENT source than the table the group was
    resolved from, which is the only reason the dimension can vary at all -- and
    a dimension that cannot vary proves nothing.

    ``proven_group_contains`` is the POSITIVE CONTROL: with the live group
    agreeing with the record, this exact fixture really does run the full
    containment, so the two empty kill lists above are the product refusing
    rather than an inert harness.
    """
    service_dir, batchd_socket = _tracked_state(tmp_path)
    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    tables.append(_table([(501, 500, 500, 999.0, "python3 -c multiprocessing-spawn-worker")]))
    kills = []
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=3)
    recorded_groups = {pid: entry.pgid for snapshot in tables for pid, entry in snapshot.items()}
    watchdog.process_group_reader = {
        # The kernel could not answer at all: unproven, never "no group".
        "unreadable": lambda _pid: None,
        # The target left the tracked group between the snapshot the decision
        # was taken on and the moment before the signal.
        "foreign": lambda _pid: 777,
        "recorded": lambda pid: recorded_groups.get(int(pid)),
    }[live_group]

    for _ in range(4):
        watchdog.sample_once()

    assert kills == expected_kills
    reasons = {action["reason"] for action in watchdog.last_snapshot["actions"]}
    assert reasons == {expected_reason}
    if expected_kills:
        return
    assert {action["failed_dimension"] for action in watchdog.last_snapshot["actions"]} == {"process_group"}
    assert all(action["signals"] == [] for action in watchdog.last_snapshot["actions"])
    assert all(action["result"] == "refused" for action in watchdog.last_snapshot["actions"])


def _lease(tmp_path, port=8881, pid=400, pgid=400, members=None):
    FixtureLeaseRecordBuilder(
        pid=pid,
        pgid=pgid,
        port=port,
        members=tuple(members or ()),
    ).write(tmp_path)


def _file_snapshot(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root`` with its exact bytes.

    Used to prove "zero unlinks": comparing this before and after a preflight
    pass is the only way to show nothing was removed, and the assertion that
    the snapshot is non-empty keeps that comparison from being two empty dicts.
    """

    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _wedged_process_view(table, kills):
    """A process table that responds to the signals preflight actually sends.

    ``reaped_pids`` now names only a pid the escalation OBSERVED leave the
    table. Dispatching a SIGKILL is not the same as confirming a death -- that
    equation is exactly the lie this contract removed -- so a fixture whose
    table never changes can no longer stand in for a reaped orphan. This models
    the wedged target the force step exists for: it ignores SIGTERM and
    disappears on SIGKILL. A pid the product never signals stays, which is what
    keeps every "untouched" row able to fail.

    Returns ``(live, kill, process_group_reader)``; the group reader answers off
    the LIVE view, because a process that is gone proves no process group.
    """

    live = dict(table)

    def kill(pid, signum):
        kills.append((pid, signum))
        if signum == signal.SIGKILL:
            live.pop(pid, None)

    def process_group_reader(pid):
        entry = live.get(int(pid))
        return entry.pgid if entry is not None else None

    return live, kill, process_group_reader


@pytest.mark.parametrize(
    "orphan_start_time, foreign_pgid_member, expected_kills",
    [
        (1010, False, [(410, signal.SIGTERM), (410, signal.SIGKILL)]),
        (9999, False, []),
        (1010, True, [(410, signal.SIGTERM), (410, signal.SIGKILL)]),
    ],
    ids=["exact_identity_is_reaped", "reused_pid_is_untouched", "foreign_pgid_cotenant_is_untouched"],
)
def test_preflight_stale_lease_authorizes_signals_only_for_an_exact_recorded_identity(
    tmp_path, orphan_start_time, foreign_pgid_member, expected_kills
):
    """A stale server-port lease is a record, never authority.

    Queue case: *stale lease*, plus the *PID/PGID reuse* dimension it is most
    often confused with.  The lease names a dead owner (pid 400, pgid 400) and
    snapshots exactly one member (pid 410, start_time 1010).

    - ``exact_identity_is_reaped`` is the POSITIVE CONTROL: with the recorded
      start identity intact, preflight really does escalate TERM then KILL, so
      the two "no kills" rows below are not passing because the kill recorder
      is inert.
    - ``reused_pid_is_untouched``: pid 410 is alive but was born at a different
      time, so it is a different process wearing a recycled number -- zero
      signals.
    - ``foreign_pgid_cotenant_is_untouched``: pid 411 is an orphan sharing the
      recorded pgid but absent from the lease's member snapshot -- a co-tenant
      of a reused process group, never a member.  It must receive no signal
      even in the run where the genuine member 410 is reaped.

    Every row additionally proves ZERO unlinks: preflight may signal a proven
    identity, but it never removes a lease, record, or socket artifact.
    """
    FixtureLeaseRecordBuilder(
        pid=400,
        pgid=400,
        port=8881,
        members=({"pid": 410, "start_time": 1010},),
    ).write(tmp_path)
    # A record and a socket artifact that preflight has no authority to remove.
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / "batchd.sock").write_bytes(b"inert-socket-artifact")

    rows = [(410, 1, 400, 1.0, "tmux -C attach-session -t x", orphan_start_time)]
    if foreign_pgid_member:
        rows.append((411, 1, 400, 1.0, "python3 unrelated_cotenant.py", 1011))
    table = _table(rows)
    # Nothing exits on its own: the only process that ever leaves this table is
    # one the product force-terminated, so every step below is its decision
    # about identity rather than a process that happened to disappear.
    kills = []
    live, kill, process_group_reader = _wedged_process_view(table, kills)
    before = _file_snapshot(tmp_path)
    assert before, "positive control: the fixture actually wrote lease and socket files"

    result = preflight_port(
        8881,
        tmp_path,
        table,
        kill=kill,
        table_reader=lambda: live,
        sleep=time.sleep,
        process_group_reader=process_group_reader,
    )

    assert kills == expected_kills
    assert result["reaped_pids"] == [pid for pid, _signum in expected_kills][:1]
    assert result["ok"] is True
    assert 411 not in {pid for pid, _signum in kills}
    assert _file_snapshot(tmp_path) == before, "preflight removed a file it had no authority to remove"


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
    kills = []
    # Only 410 was ever a proven member, so it is the only pid this view can
    # lose: 411 and 900 stay alive throughout and their absence from `kills` is
    # the product's decision, not the fixture retiring them.
    live, kill, process_group_reader = _wedged_process_view(table, kills)

    result = preflight_port(
        8881,
        tmp_path,
        table,
        kill=kill,
        table_reader=lambda: live,
        sleep=time.sleep,
        process_group_reader=process_group_reader,
    )

    assert result["ok"] is True
    assert 411 in live and 900 in live, "the fixture retired a bystander the product never signalled"
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
    batchd_socket = service_dir / "batchd.sock"
    record = FixtureLocalServiceRecordBuilder(
        service="batchd",
        socket_path=batchd_socket,
        pid=500,
        fields={"launcher_pid": 700, "launcher_port": 8881, "protocol_version": 1},
    ).build()
    (service_dir / "batchd.service.json").write_text(json.dumps(record), encoding="utf-8")
    # The launcher (700) is dead -- absent from the table -- but the service
    # (500) itself is alive and, in this case, still serving a client.
    table = _table([(500, 1, 500, 1.0, f"python3 -m yolomux_lib.batchd --serve --socket {batchd_socket} --idle-seconds 60")])
    kills = []
    live, kill, process_group_reader = _wedged_process_view(table, kills)

    result = preflight_port(
        8881,
        tmp_path,
        table,
        kill=kill,
        table_reader=lambda: live,
        sleep=time.sleep,
        process_group_reader=process_group_reader,
        service_status_reader=lambda _group: {"ok": True, "pid": 500, "clients": 1},
    )

    assert result["ok"] is True
    assert result["reaped_pids"] == []
    assert kills == []

    # Companion: the same dead-launcher group, but genuinely idle this time --
    # proving the first case's silence is the "has a client" discriminator,
    # not a fixture bug that would have never reaped anything either way.
    idle_kills = []
    idle_live, idle_kill, idle_group_reader = _wedged_process_view(table, idle_kills)
    idle_result = preflight_port(
        8881,
        tmp_path,
        table,
        kill=idle_kill,
        table_reader=lambda: idle_live,
        sleep=time.sleep,
        process_group_reader=idle_group_reader,
        service_status_reader=lambda _group: {"ok": True, "pid": 500, "clients": 0},
    )

    assert idle_result["ok"] is True
    assert idle_result["reaped_pids"] == [500]
    assert idle_kills == [(500, signal.SIGTERM), (500, signal.SIGKILL)]


def test_evidence_summary_is_bounded_and_redacted(monkeypatch, tmp_path):
    command_canary = "command-canary-8f1a3c7d"
    path_canary = "socket-path-canary-4b9e2d6a"
    payload_canary = "payload-canary-6c2f8a1e"
    service_dir, batchd_socket = _tracked_state(
        tmp_path,
        socket_name=f"{path_canary}.sock",
        payload=payload_canary,
    )
    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step, command_payload=command_canary))
        for step in range(4)
    ]
    tables.append(_table([]))
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, [], sustained=3)

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
    # Every row is now the destructive owner's own MEASURED outcome
    # (`TerminationOutcome.as_dict()`), not a second vocabulary mapped onto it:
    # `result` says what was observed, `signals` lists what was actually sent,
    # and `confirmed_dead` is a re-read rather than "a kill was dispatched".
    # `age_seconds` is the elapsed measurement itself and is asserted by type.
    assert [
        {key: value for key, value in action.items() if key != "age_seconds"}
        for action in summary["actions"]
    ] == [
        {
            "target": "web",
            "pid": 400,
            "attempted_action": "terminate",
            "result": "confirmed_exited",
            "reason": "authorized",
            "confirmed_dead": True,
            "signals": [int(signal.SIGTERM)],
        },
        {
            "target": "batchd",
            "pid": 500,
            "attempted_action": "terminate",
            "result": "confirmed_exited",
            "reason": "authorized",
            "confirmed_dead": True,
            "signals": [int(signal.SIGTERM)],
        },
        # The batchd group's pool child. It is force-only -- its leader already
        # absorbed the graceful signal -- so it is reported through the force
        # step even though the shared grace window is what observed it gone.
        {
            "target": "tracked-member",
            "pid": 501,
            "attempted_action": "force_terminate",
            "result": "force_confirmed_exited",
            "reason": "authorized",
            "confirmed_dead": True,
            "signals": [int(signal.SIGKILL)],
        },
    ]
    assert all(isinstance(action["age_seconds"], float) for action in summary["actions"])

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




@pytest.mark.parametrize(
    "supervisor_case, expected_reason",
    [
        ("live", CLAIM_REASON_SUPERVISOR_ALIVE),
        ("absent", "missing_supervisor_record"),
        ("unreadable_record", "missing_supervisor_record"),
        ("previous_boot", "previous_boot"),
        ("no_birth_identity", "missing_process_start_identity"),
        ("gone", ""),
    ],
    ids=[
        "live_supervisor_vetoes_containment",
        "absent_supervisor_field_fails_closed",
        "unreadable_supervisor_record_fails_closed",
        "supervisor_from_a_previous_boot_fails_closed",
        "supervisor_without_birth_identity_fails_closed",
        "provably_gone_supervisor_still_contains",
    ],
)
def test_containment_never_stops_a_service_whose_supervisor_is_still_alive(
    monkeypatch, tmp_path, supervisor_case, expected_reason
):
    """A daemon another live server still supervises is not this watchdog's to stop.

    Several YOLOmux servers share one runtime directory, so this watchdog can
    SEE services it does not own. The record each daemon publishes names the
    ``supervisor`` that spawned it, and that field is what makes retention
    truthful: the claim ledger already answers "this helper is deliberately
    retained" with ``supervisor_alive`` plus a ``surviving_supervisor``
    identity. Containment has to obey the same one rule, or the watchdog becomes
    the second owner of a lifetime the supervision contract exists to keep
    single.

    ``provably_gone_supervisor_still_contains`` is the POSITIVE CONTROL: the
    identical fixture whose supervisor the shared fence proves is gone really
    does receive the full containment, so every refusal above is the product
    deciding rather than a harness that never contains anything. The web leader
    is contained in EVERY row, which keeps the pass itself observable.

    The four ambiguous rows are the fail-closed half. Absent, unreadable, from a
    previous boot, or carrying no birth identity are all "nobody proved who owns
    this", and an unproven owner may never authorize a signal. Only what the
    shared fence calls removable-stale -- this host, this boot, and the process
    proven not-found or its pid proven reused -- counts as gone, which is why
    this cannot be satisfied by a bare ``pid_is_alive``: the previous-boot row
    below names a pid that is genuinely alive right now.
    """
    identity = current_host_identity()
    supervisors = {
        # This very pytest process: really alive, and the fence proves it.
        "live": identity.process_record_fields(),
        "absent": None,
        "unreadable_record": "not-a-record",
        # A REAL live pid (this process) recorded under a different boot. A bare
        # liveness check waves it through; the fence cannot prove it is gone.
        "previous_boot": {**identity.process_record_fields(), "boot_id": "a-previous-boot"},
        "no_birth_identity": {
            key: value
            for key, value in identity.process_record_fields().items()
            if key not in {"process_start_identity", "process_start_ticks"}
        },
        "gone": _gone_supervisor(),
    }
    supervisor = supervisors[supervisor_case]

    service_dir, batchd_socket = _tracked_state(tmp_path, supervisor=supervisor)
    if supervisor is None:
        record_path = service_dir / "batchd.service.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        del record["supervisor"]
        record_path.write_text(json.dumps(record), encoding="utf-8")

    tables = [
        _table(_rows(batchd_socket, web_cpu=10.0 * step, worker_cpu=10.0 * step)) for step in range(4)
    ]
    tables.append(_table([]))
    kills = []
    before = _file_snapshot(tmp_path)
    watchdog = _watchdog(monkeypatch, tmp_path, service_dir, tables, kills, sustained=3)

    for _ in range(4):
        watchdog.sample_once()

    actions = watchdog.last_snapshot["actions"]
    batchd_rows = [row for row in actions if row["pid"] in {500, 501}]
    assert (400, signal.SIGTERM) in kills, "positive control: the pass really did contain something"

    if not expected_reason:
        assert (500, signal.SIGTERM) in kills, "a service whose supervisor is provably gone was not contained"
        assert all(row["result"] != "retained" for row in batchd_rows)
        return

    assert [pid for pid, _signum in kills if pid in {500, 501}] == [], (
        f"{supervisor_case}: a daemon whose supervisor was not proven gone was signalled"
    )
    # Leader AND pool child: a member's authority is derived from its leader, so
    # a retained leader retains its whole group rather than leaving it half torn.
    assert sorted(row["pid"] for row in batchd_rows) == [500, 501]
    for row in batchd_rows:
        assert row["result"] == "retained", f"{supervisor_case}: {row}"
        assert row["reason"] == expected_reason, f"{supervisor_case}: {row}"
        assert row["failed_dimension"] == "surviving_supervisor"
        assert row["signals"] == []
        assert row["confirmed_dead"] is False
        if supervisor_case in {"live", "previous_boot", "no_birth_identity"}:
            # A supervisor identity was actually READ, so the row must name it.
            assert row["surviving_supervisor"]["pid"] == identity.pid
    # Zero unlinks and zero rewrites. Compared key by key against the snapshot
    # rather than for equality, because the containment of the WEB leader in
    # this same pass legitimately ADDS one incident-evidence file under
    # `evidence_dir`; what must not change is anything that was already there.
    after = _file_snapshot(tmp_path)
    assert before, "positive control: the fixture wrote a lease, a record and a socket artifact"
    assert {name: after.get(name) for name in before} == before, (
        f"{supervisor_case}: an artifact was unlinked or rewritten"
    )

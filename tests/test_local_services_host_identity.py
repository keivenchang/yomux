# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused coverage for local-service client lease identity wiring."""

from __future__ import annotations

import json
import os
import time
import signal
from pathlib import Path

import pytest

from tests.helpers.local_service_records import FixtureLocalServiceRecordBuilder
from yomux_lib.host_identity import HostIdentity
from yomux_lib.host_identity import current_host_identity
from yomux_lib.host_identity import is_current_local_process
from yomux_lib.infra.process_claims import ProcessClaimLedger
from yomux_lib.local_services import registry as registry_mod
from yomux_lib.local_services.registry import ProcessTableEntry
from yomux_lib.local_services.registry import process_record_diagnostic
from yomux_lib.local_services.registry import shutdown_owned_local_services
from yomux_lib.local_services.registry import verified_orphan_diagnostics
from yomux_lib.local_services.runtime import acquire_client_lease
from yomux_lib.local_services.runtime import reap_dead_client_leases


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


SERVICE_PID = 500
SERVICE_START_TICKS = 1500
LAUNCHER_PID = 400
LAUNCHER_PORT = 8881
# The generation the fixture's record is published with. A local-service record
# carrying none is RETAINED rather than retired -- this build never wrote that
# proof -- so the actionable control row below needs one or the whole matrix
# would report zero signals for a reason unrelated to identity.
SERVICE_SPAWN_GENERATION = "fixture-spawn-generation-500"


def _service_environment(tmp_path: Path, *, record_overrides: dict, command: str | None = None):
    """One tracked batchd generation on disk plus the exact live process table row.

    The unmodified pair is provably actionable (see the ``live_claim`` control
    row below); every parametrised case perturbs exactly one identity dimension
    of that pair and nothing else.
    """
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    socket_path = service_dir / "batchd.sock"
    socket_path.write_bytes(b"inert-socket-artifact")
    record = FixtureLocalServiceRecordBuilder(
        service="batchd",
        socket_path=socket_path,
        pid=SERVICE_PID,
        process_start_ticks=SERVICE_START_TICKS,
        fields={
            "launcher_pid": LAUNCHER_PID,
            "launcher_port": LAUNCHER_PORT,
            "protocol_version": 1,
            "spawn_generation": SERVICE_SPAWN_GENERATION,
        },
    ).build()
    record.update(record_overrides)
    (service_dir / "batchd.service.json").write_text(json.dumps(record), encoding="utf-8")
    live_command = command or f"python3 -m yomux_lib.batchd --serve --socket {socket_path} --idle-seconds 60"
    table = {
        SERVICE_PID: ProcessTableEntry(1, SERVICE_PID, 1.0, live_command, SERVICE_START_TICKS),
        # A co-tenant of the SAME process group that the record never named.
        SERVICE_PID + 1: ProcessTableEntry(SERVICE_PID, SERVICE_PID, 1.0, "python3 -c worker", SERVICE_START_TICKS + 1),
    }
    return service_dir, socket_path, record, table


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


ACTIONABLE_SIGNALS = [
    (SERVICE_PID, signal.SIGTERM),
    (SERVICE_PID, signal.SIGKILL),
    (SERVICE_PID + 1, signal.SIGKILL),
]


@pytest.mark.parametrize(
    "dimension, overrides, command_override, expected_reason, expected_signals, may_remove_record",
    [
        ("live_claim", {}, None, "current_local_process", ACTIONABLE_SIGNALS, False),
        # The generation dimension proper: everything about this record is
        # actionable EXCEPT that the live process no longer proves the
        # generation the record was published with. The shared identity fence
        # cannot see that at all -- it still says `current_local_process` -- so
        # this row is the one that fails only if the destructive owner really
        # re-proves the generation before signalling.
        (
            "spawn_generation",
            {"spawn_generation": "a-superseded-generation"},
            None,
            "current_local_process",
            [],
            False,
        ),
        ("host", {"stable_host_id": "some-other-host"}, None, "foreign_host", [], False),
        ("boot", {"boot_id": "a-previous-boot"}, None, "previous_boot", [], False),
        (
            "pid_start_identity",
            {"process_start_identity": "proc:999999", "process_start_ticks": 999999},
            None,
            "process_identity_reused",
            [],
            True,
        ),
        ("generation", {"pid": SERVICE_PID + 40}, None, "process_not_found", [], True),
        (
            "service_kind",
            {},
            "python3 -m yomux_lib.statsd --serve --socket /nowhere/statsd.sock --idle-seconds 60",
            "current_local_process",
            [],
            False,
        ),
    ],
    ids=[
        "live_claim_is_actionable",
        "superseded_spawn_generation",
        "foreign_host",
        "previous_boot",
        "reused_pid_start_identity",
        "superseded_generation_pid",
        "wrong_service_kind_on_the_socket",
    ],
)
def test_every_destructive_identity_dimension_fails_closed_with_zero_signals_and_zero_unlinks(
    tmp_path, dimension, overrides, command_override, expected_reason, expected_signals, may_remove_record
):
    """Host, boot, PID start identity, service kind, generation and live claim are
    each independently sufficient to withdraw destructive authority.

    ``live_claim_is_actionable`` is the POSITIVE CONTROL for the whole matrix:
    the unmodified record/table pair really does resolve to one tracked group and
    really does receive the full TERM-then-KILL escalation, so each ``[]`` below
    is a decision the product made about identity, not an inert fixture.

    ``may_remove_record`` is asserted separately and deliberately does NOT track
    ``expected_signals``: a reused or vanished PID grants RECORD-ONLY cleanup
    (``may_remove_stale_record``) while granting no signal authority at all. That
    split is the invariant this matrix exists to protect -- collapsing the two
    would be exactly the escalation the queue forbids. Every row also proves zero
    unlinks by comparing the full file snapshot around the call.
    """
    service_dir, _socket_path, record, table = _service_environment(
        tmp_path, record_overrides=overrides, command=command_override
    )
    before = _file_snapshot(tmp_path)
    assert before, "positive control: the fixture wrote a record and a socket artifact"

    diagnostic = process_record_diagnostic(record, table=table)
    signals: list[tuple[int, int]] = []
    result = shutdown_owned_local_services(
        LAUNCHER_PORT,
        service_dir,
        launcher_pid=LAUNCHER_PID,
        table_reader=lambda: table,
        kill=lambda pid, signum: signals.append((pid, signum)),
        sleep=lambda _seconds: None,
        # The two live dimension probes, injected for the same reason
        # `table_reader` is: these pids are synthetic, so a reader that asked
        # /proc would answer about whatever really holds pid 500 on this
        # machine. Both refuse (None) for a pid this fixture never described,
        # and the generation row above proves the first one can disagree.
        generation_reader=lambda pid: SERVICE_SPAWN_GENERATION if int(pid) in table else None,
        process_group_reader=lambda pid: table[int(pid)].pgid if int(pid) in table else None,
    )

    assert diagnostic.reason.value == expected_reason
    assert signals == expected_signals, f"{dimension}: wrong destructive decision"
    assert diagnostic.may_remove_stale_record is may_remove_record, (
        f"{dimension}: record-removal authority disagrees with the identity fence"
    )
    # Integration arbitration (task 044): this originally asserted `signalled == [SERVICE_PID]`,
    # `terminated == [500, 501]`, and an exact two-key dict. That encoded the OLD accounting,
    # where `terminated` meant "a SIGKILL was sent" -- which is precisely the lie this task set
    # out to remove, because sending a kill is not the same as confirming a death. The owner now
    # reports `signalled` (received at least one signal), `terminated` (confirmed dead),
    # `unconfirmed` (signalled, death unproven) and `retained` (deliberately kept). Assert the
    # invariant that actually matters rather than echoing a shape.
    assert set(result) == {"signalled", "terminated", "unconfirmed", "retained"}
    if dimension == "live_claim":
        # Both the leader and its group member are legitimately signalled; the member never
        # receives SIGTERM, only the forced signal, which is why "signalled" is not "SIGTERMed".
        assert sorted(result["signalled"]) == [SERVICE_PID, SERVICE_PID + 1]
        # `kill` here only records, so nothing ever leaves the process table and no death can be
        # confirmed. That is the honest outcome, and it is what `unconfirmed` exists to say.
        assert result["terminated"] == [], "a recording kill cannot confirm a death"
        assert sorted(result["unconfirmed"]) == [SERVICE_PID, SERVICE_PID + 1]
    else:
        assert result["signalled"] == [], f"{dimension}: an ambiguous identity was signalled"
        assert result["terminated"] == []
        assert result["unconfirmed"] == [], f"{dimension}: an ambiguous identity was signalled"
    assert _file_snapshot(tmp_path) == before, f"{dimension}: an artifact was unlinked"


def test_one_typed_orphan_row_per_ambiguous_survivor_and_never_a_silent_one(tmp_path):
    """Every ambiguous survivor gets EXACTLY ONE typed row -- not zero, not two.

    Three survivors sit in three different authority gaps under one service
    directory: no ledger record at all, an unreadable record, and a record that
    names a superseded pid. The count assertion is what makes this a regression
    against both failure shapes the queue names: a silent orphan (zero rows) and
    a duplicated orphan (two rows for one pid).
    """
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    ghost_socket = service_dir / "ghost.sock"
    unreadable_socket = service_dir / "unreadable.sock"
    superseded_socket = service_dir / "superseded.sock"
    (service_dir / "unreadable.service.json").write_text("{not json", encoding="utf-8")
    (service_dir / "superseded.service.json").write_text(
        json.dumps({"service": "superseded", "socket": str(superseded_socket), "pid": 7999, "version": 1}),
        encoding="utf-8",
    )
    table = {
        7001: ProcessTableEntry(1, 7001, 1.0, f"python3 -m yomux_lib.batchd --serve --socket {ghost_socket}", 8001),
        7002: ProcessTableEntry(1, 7002, 1.0, f"python3 -m yomux_lib.batchd --serve --socket {unreadable_socket}", 8002),
        7003: ProcessTableEntry(1, 7003, 1.0, f"python3 -m yomux_lib.batchd --serve --socket {superseded_socket}", 8003),
    }

    ledger = registry_mod.OrphanObservationLedger()
    rows = verified_orphan_diagnostics(service_dir, table, now=1000.0, observations=ledger)
    by_pid: dict[int, list[dict]] = {}
    for row in rows:
        by_pid.setdefault(int(row["pid"]), []).append(row)

    assert sorted(by_pid) == [7001, 7002, 7003], "a live ambiguous survivor was reported silently"
    assert [len(entries) for _pid, entries in sorted(by_pid.items())] == [1, 1, 1]
    assert [by_pid[pid][0]["reason"] for pid in (7001, 7002, 7003)] == [
        registry_mod.ORPHAN_REASON_NO_LEDGER_RECORD,
        registry_mod.ORPHAN_REASON_UNREADABLE_RECORD,
        registry_mod.ORPHAN_REASON_SUPERSEDED_GENERATION,
    ]
    # Positive control that "one row" is a real bound and not an artefact of the
    # ledger deduplicating: a second pass over the same table still reports each
    # survivor exactly once, now with a strictly larger retained age.
    second = verified_orphan_diagnostics(service_dir, table, now=1004.0, observations=ledger)
    assert [int(row["pid"]) for row in second] == [7001, 7002, 7003]
    assert [row["age_seconds"] for row in second] == [4.0, 4.0, 4.0]


# ---------------------------------------------------------------------------
# Zombie blindness in the raw identity fence.
#
# `process_record_diagnostic` (registry.py:498-515) checks `process_state == "Z"`
# and forces PROCESS_NOT_FOUND. `is_current_local_process` (the shared fence it
# delegates to) does NOT, and two callers reach it raw:
#   - ProcessClaimLedger._reap_one            (infra/process_claims.py:263, 268)
#   - reap_dead_client_leases                 (local_services/runtime.py:83)
# Measured: an unreaped child reports current=True through both. That makes a
# retired helper permanently unreapable and pins a crashed client's claim
# forever, which is precisely the "last valid external claim disappeared" case
# the queue bounds.
#
# CONTRACT: `is_current_local_process` must exclude a process that has exited and
# not been reaped, using the same zombie-excluding predicate the product owns.
# ---------------------------------------------------------------------------


def _fork_unreaped_zombie() -> int:
    """A real child that has exited and deliberately has NOT been wait()-ed."""

    pid = os.fork()
    if pid == 0:  # pragma: no cover - child never returns
        os._exit(0)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if registry_mod.process_state(pid) == "Z":
            return pid
        time.sleep(0.005)
    raise AssertionError("the forked child never reached state Z")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_shared_identity_fence_treats_a_real_unreaped_zombie_as_gone() -> None:
    """One exited-but-unreaped process, read through both fences.

    ``process_record_diagnostic`` already gets this right; the raw
    ``is_current_local_process`` its two other callers use does not. The
    POSITIVE CONTROL is this same process read while it was still running, so
    the "gone" verdict cannot come from a fixture that never proved liveness.
    """
    live_pid = os.getpid()
    live_record = current_host_identity().process_record_fields(
        pid=live_pid,
        start_identity=registry_mod.process_start_identity(live_pid),
    )
    assert is_current_local_process(live_record).current is True, (
        "positive control: a genuinely running process must read as current"
    )

    zombie_pid = _fork_unreaped_zombie()
    try:
        zombie_record = current_host_identity().process_record_fields(
            pid=zombie_pid,
            start_identity=registry_mod.process_start_identity(zombie_pid),
        )
        # The zombie-aware fence, for comparison: it already answers correctly.
        assert process_record_diagnostic(zombie_record).current is False

        diagnostic = is_current_local_process(zombie_record)

        assert diagnostic.current is False, (
            "the shared identity fence reported an exited, unreaped process as a live one; "
            "every raw caller (ProcessClaimLedger._reap_one, reap_dead_client_leases) inherits that"
        )
        assert diagnostic.reason.value == "process_not_found"
    finally:
        os.waitpid(zombie_pid, 0)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_process_claim_ledger_reports_a_zombie_target_as_already_exited(tmp_path) -> None:
    """A claimed helper that exited must be reported gone, never signalled.

    ``ProcessClaimLedger`` is the only structure that carries every destructive
    dimension the queue names (kind, namespace, supervisor identity, target
    identity, generation), and it had no coverage at all. The two rows here are
    each other's control: a live target really is signalled, so the zombie row's
    empty signal list is a decision about identity.
    """
    live_pid, release_fd = None, None
    read_fd, write_fd = os.pipe()
    live_pid = os.fork()
    if live_pid == 0:  # pragma: no cover - child never returns
        os.close(write_fd)
        try:
            os.read(read_fd, 1)
        finally:
            os._exit(0)
    os.close(read_fd)
    release_fd = write_fd
    dead_supervisor = {
        **current_host_identity().process_record_fields(pid=live_pid, start_identity="proc:1"),
        "pid": 2,
        "process_start_identity": "proc:1",
        "process_start_ticks": 1,
    }
    try:
        # POSITIVE CONTROL: a live claimed target whose supervisor is gone is signalled.
        live_ledger = ProcessClaimLedger(tmp_path / "live", "fixture-helper")
        live_claim = live_ledger.publish(live_pid)
        payload = json.loads(live_claim.path.read_text(encoding="utf-8"))
        payload["supervisor"] = dead_supervisor
        live_claim.path.write_text(json.dumps(payload), encoding="utf-8")
        live_signals: list[tuple[int, int]] = []
        live_rows = live_ledger.reap_unsupervised(
            signal_process=lambda pid, signum: live_signals.append((pid, signum))
        )
        assert [row["result"] for row in live_rows] == ["signalled"]
        assert live_signals == [(live_pid, signal.SIGTERM)]

        # The same shape with a target that has exited and not been reaped.
        zombie_pid = _fork_unreaped_zombie()
        try:
            zombie_ledger = ProcessClaimLedger(tmp_path / "zombie", "fixture-helper")
            zombie_claim = zombie_ledger.publish(zombie_pid)
            payload = json.loads(zombie_claim.path.read_text(encoding="utf-8"))
            payload["supervisor"] = dead_supervisor
            zombie_claim.path.write_text(json.dumps(payload), encoding="utf-8")
            zombie_signals: list[tuple[int, int]] = []

            rows = zombie_ledger.reap_unsupervised(
                signal_process=lambda pid, signum: zombie_signals.append((pid, signum))
            )

            assert [row["pid"] for row in rows] == [zombie_pid], "the claim row vanished entirely"
            assert zombie_signals == [], "an exited, unreaped helper was signalled"
            assert rows[0]["result"] in {"claim_removed", "already_exited"}, (
                f"an exited helper was reported as {rows[0]['result']!r}; its claim is now unreapable forever"
            )
            assert zombie_claim.path.exists() is False, "the spent claim of an exited helper was retained"
        finally:
            os.waitpid(zombie_pid, 0)
    finally:
        if release_fd is not None:
            os.close(release_fd)
        if live_pid:
            try:
                os.waitpid(live_pid, 0)
            except ChildProcessError:
                pass


# ---------------------------------------------------------------------------
# Lease-level self-connection.
#
# The connection-level exclusion is closed (`serve_connection` skips `on_client`
# when the peer pid is our own). The LEASE level is open: `acquire_client_lease`
# (runtime.py:191-244) trusts the caller-supplied `client_pid` and never compares
# it to the daemon's own pid, so a daemon can mint a claim naming itself and pin
# its own idle deadline forever -- the queue's explicit rejected shortcut, "do
# not let a process count its own connection as external demand".
#
# CONTRACT: `acquire_client_lease` must refuse a client pid equal to the serving
# process's own pid with a typed diagnostic, and must accept any other pid.
# ---------------------------------------------------------------------------


def test_a_service_can_never_mint_an_external_claim_naming_itself() -> None:
    """A self-named lease is not external demand and must be refused typed."""
    identity = fixture_identity()
    leases: dict[str, object] = {}

    # POSITIVE CONTROL: a different, live pid is a real external claim.
    external = acquire_client_lease(
        leases,
        identity.pid,
        host_identity=identity,
        start_identity_reader=lambda _pid: identity.process_start_identity,
    )
    assert external["ok"] is True
    assert len(leases) == 1

    self_claim = acquire_client_lease(
        leases,
        os.getpid(),
        host_identity=identity,
        start_identity_reader=lambda _pid: identity.process_start_identity,
    )

    assert self_claim["ok"] is False, (
        "the serving process minted an external-demand lease naming itself; nothing else is "
        "required to keep a daemon alive forever"
    )
    assert self_claim["diagnostic"]["reason"] == "self_connection"
    assert len(leases) == 1, "a refused self-claim was still stored in the lease table"

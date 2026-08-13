# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Section G non-browser regression gates for stats materialization and storage."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import threading

from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.helpers.gate_stats import NOW
from yolomux_lib.stats_current import materializer
from yolomux_lib.stats_current import migration
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage


G8_COVERAGE_EPOCHS = 15_000
G8_CPU_WINDOW_SECONDS = 1.0
G8_CPU_SECONDS_MAX = 0.35
G9_CORRUPTION_BYTES = 4_096


def _published_empty_service(tmp_path: Path) -> stats_service.StatsCurrentService:
    service = stats_service.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: NOW,
    )
    service._view_demanded = lambda *args: True
    empty = storage.StoreSnapshot(storage.SchemaMetadata(5, 1, 1, 0), (), (), (), (), ())
    generation = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=1,
        generated_at=NOW,
        observed_until=NOW,
    )
    assert service._publish(generation, service._encode_generation(generation)) is True
    service._pending_full = False
    return service


def test_g3_failed_publisher_with_repair_backlog_has_no_published_generation(tmp_path, gate_runtime_paths):
    service = _published_empty_service(gate_runtime_paths.state_dir / "g3-failed")
    with service.work_lock:
        service._pending_dirty = {
            materializer.DirtyCell(resolution, NOW - index * resolution)
            for index in range(60)
            for resolution in (1, 10, 60, 300)
        }
    service._record_build_failure(RuntimeError("repair backlog materializer failed"))
    status = service._status()
    assert status["queue"]["materializer_depth"] == 240, status
    assert status["build"]["last_failure"] == "RuntimeError", status
    assert status["generations"]["by_resolution"] == {}, status
    assert status["cache_generation"] == 0, status


def test_g3_empty_payload_is_not_reported_as_published(tmp_path, gate_runtime_paths):
    service = _published_empty_service(gate_runtime_paths.state_dir / "g3-empty")
    status = service._status()
    assert status["queue"]["materializer_depth"] == 0, status
    assert status["generations"]["by_resolution"] == {}, status
    assert status["cache_generation"] == 0, status


def _materializer_cpu_worker(start_event, ready_event, done_event, stop_event, result_queue) -> None:
    coverage = tuple(
        storage.CoverageEpoch(
            "agent_tokens",
            "scan",
            f"epoch-{index}",
            float(index * 20),
            float(index * 20 + 10),
            10.0,
            1,
        )
        for index in range(G8_COVERAGE_EPOCHS)
    )
    snapshot = storage.StoreSnapshot(storage.SchemaMetadata(5, 1, 1), (), coverage, (), (), ())
    ready_event.set()
    start_event.wait()
    generation = materializer.build_generation(
        snapshot,
        source_generation=1,
        cache_generation=1,
        generated_at=float(G8_COVERAGE_EPOCHS * 20),
        observed_until=float(G8_COVERAGE_EPOCHS * 20),
    )
    result_queue.put(len(generation.layers))
    done_event.set()
    stop_event.wait()


def _process_cpu_jiffies(pid: int) -> int:
    fields = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
    return int(fields[13]) + int(fields[14])


def test_g8_fifteen_thousand_epoch_materializer_stays_under_fixed_cpu_budget(
    tmp_path, gate_runtime_paths
):
    """15,000 coverage epochs complete inside a fixed one-second /proc CPU window."""

    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    ready_event = context.Event()
    done_event = context.Event()
    stop_event = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_materializer_cpu_worker,
        args=(start_event, ready_event, done_event, stop_event, result_queue),
        name="gate-g8-materializer",
    )
    process.start()
    try:
        assert ready_event.wait(10), "materializer worker did not prepare the 15,000-epoch fixture"
        ticks_per_second = os.sysconf("SC_CLK_TCK")
        before = _process_cpu_jiffies(process.pid)
        start_event.set()
        threading.Event().wait(G8_CPU_WINDOW_SECONDS)
        after = _process_cpu_jiffies(process.pid)
        cpu_seconds = (after - before) / ticks_per_second
        assert done_event.is_set(), {
            "epochs": G8_COVERAGE_EPOCHS,
            "window_seconds": G8_CPU_WINDOW_SECONDS,
            "cpu_seconds": cpu_seconds,
        }
        assert result_queue.get(timeout=1) == 4
        assert cpu_seconds < G8_CPU_SECONDS_MAX, {
            "epochs": G8_COVERAGE_EPOCHS,
            "window_seconds": G8_CPU_WINDOW_SECONDS,
            "cpu_seconds": cpu_seconds,
            "budget_seconds": G8_CPU_SECONDS_MAX,
        }
    finally:
        stop_event.set()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        result_queue.close()
    assert process.exitcode == 0


def _valid_current_database(state_dir: Path) -> Path:
    """Build one real current database the way a first run does."""

    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / storage.DATABASE_FILENAME
    migration.migrate(
        migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW,
    )
    return target


def _corrupt_in_place(path: Path) -> bytes:
    """Overwrite mid-file pages exactly as a kill during a checkpoint leaves them."""

    size = path.stat().st_size
    assert size > G9_CORRUPTION_BYTES * 2, size
    with open(path, "r+b") as handle:
        handle.seek(size // 2)
        handle.write(b"\xff" * G9_CORRUPTION_BYTES)
    return path.read_bytes()


def test_g9_corrupt_current_database_is_quarantined_and_rebuilt(gate_runtime_paths):
    """A malformed current database is set aside and replaced, never deleted, never fatal."""

    state_dir = gate_runtime_paths.state_dir / "g9-migration"
    target = _valid_current_database(state_dir)
    damaged = _corrupt_in_place(target)

    report = migration.migrate(
        migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW,
    )

    quarantined = sorted(
        path for path in state_dir.glob(f"{storage.DATABASE_FILENAME}.*")
        if not path.name.endswith(("-wal", "-shm", "-journal"))
    )
    assert len(quarantined) == 1, [path.name for path in quarantined]
    assert quarantined[0].read_bytes() == damaged, "the damaged database was not preserved verbatim"
    assert target.exists(), "no replacement current database was activated"
    with storage.Store.open_reader(target) as store:
        assert store.read_snapshot().observations == ()
    kinds = {issue.kind for issue in report.issues}
    assert migration.UNREADABLE_CURRENT_DATABASE in kinds, report.issues


def test_g9_recovered_database_is_stable_across_restart(gate_runtime_paths):
    """Recovery runs once: a restart reuses the rebuilt database and quarantines nothing more."""

    state_dir = gate_runtime_paths.state_dir / "g9-restart"
    target = _valid_current_database(state_dir)
    _corrupt_in_place(target)
    migration.migrate(
        migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW,
    )
    after_first = sorted(path.name for path in state_dir.glob(f"{storage.DATABASE_FILENAME}.*"))

    report = migration.migrate(
        migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW + 1,
    )

    assert report.already_active is True, report
    assert sorted(path.name for path in state_dir.glob(f"{storage.DATABASE_FILENAME}.*")) == after_first


def test_g9_statsd_stays_up_when_the_current_database_is_corrupt(gate_runtime_paths):
    """statsd reaches ready instead of exiting (1) and telling the browser to retry forever."""

    state_dir = gate_runtime_paths.state_dir / "g9-service"
    target = _valid_current_database(state_dir)
    _corrupt_in_place(target)
    service = stats_service.StatsCurrentService(
        state_dir / "statsd.sock", target, clock=lambda: NOW,
    )
    try:
        service._start()
        status = service._status()
    finally:
        service._close()
    assert status["migration"]["state"] == "ready", status["migration"]
    assert status["migration"]["result"] == "recovered", status["migration"]


def test_g11_recovery_survives_a_colliding_quarantine_name(gate_runtime_paths, monkeypatch):
    """A repeated quarantine stamp must not put statsd back on the fatal path.

    The stamp is time_ns plus pid, so a backwards clock step is enough to repeat
    it. Recovery has to keep working and must still never overwrite the earlier
    quarantine.
    """

    state_dir = gate_runtime_paths.state_dir / "g11"
    target = _valid_current_database(state_dir)
    monkeypatch.setattr(migration.time, "time_ns", lambda: 1_111_111_111_111_111_111)

    digests = []
    for index in range(3):
        damaged = _corrupt_in_place(target)
        digests.append(hashlib.md5(damaged).hexdigest())
        migration.migrate(
            migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW + index,
        )
        with sqlite3.connect(target) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    quarantined = sorted(
        path for path in state_dir.glob(f"{storage.DATABASE_FILENAME}.corrupt-*")
        if not path.name.endswith(("-wal", "-shm", "-journal"))
    )
    assert len(quarantined) == 3, [path.name for path in quarantined]
    kept = sorted(hashlib.md5(path.read_bytes()).hexdigest() for path in quarantined)
    assert kept == sorted(digests), "an earlier quarantine was overwritten"


def _strand_foreign_sidecars(state_dir: Path, target: Path) -> None:
    """Leave a -wal belonging to a DIFFERENT database beside an absent target.

    This is what a killed writer leaves behind: SQLite only removes the WAL on a
    clean close of the last connection, so a crash strands it. If the main file
    is then replaced, the surviving WAL belongs to a database that no longer
    exists.
    """

    other = state_dir / "other.sqlite3"
    connection = sqlite3.connect(other)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE scratch(x)")
        connection.executemany("INSERT INTO scratch VALUES (?)", [(index,) for index in range(2_000)])
        connection.commit()
        source_wal = Path(f"{other}-wal")
        assert source_wal.exists(), "fixture did not produce a WAL to strand"
        shutil.copy2(source_wal, Path(f"{target}-wal"))
    finally:
        connection.close()


def test_g10_orphaned_sidecars_are_never_replayed_into_a_new_database(gate_runtime_paths):
    """Activation must not inherit a -wal left behind by a database that is gone."""

    state_dir = gate_runtime_paths.state_dir / "g10"
    target = _valid_current_database(state_dir)
    target.unlink()
    _strand_foreign_sidecars(state_dir, target)

    migration.migrate(
        migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW,
    )

    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "scratch" not in tables, sorted(tables)
    with storage.Store.open_reader(target) as store:
        assert store.read_snapshot().observations == ()

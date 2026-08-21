# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded, restartable v7 -> v8 migration.

Schema 8 is side-by-side, so the v7 file is a SOURCE and never a target. Every case here proves one
of two things: that the facts crossed exactly, or that an interruption left a state the next
startup can still act on. There is deliberately no case that accepts a half-migrated store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from yolomux_lib.stats_current import migration as migration_module
from yolomux_lib.stats_current import storage

V7_SCHEMA_VERSION = migration_module.V7_SCHEMA_VERSION
V7_DATABASE_FILENAME = migration_module.V7_DATABASE_FILENAME

# One frozen v7 fixture, built by explicit statements rather than by importing the current schema.
# Deriving the source shape from the code under test would make the migration tautological: a shape
# change would silently move the fixture with it and prove only that the code agrees with itself.
_V7_DDL = (
    "CREATE TABLE schema_meta (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "minimum_writer_protocol INTEGER NOT NULL, minimum_writer_build INTEGER NOT NULL, "
    "source_generation INTEGER NOT NULL, last_vacuumed_at REAL NOT NULL DEFAULT 0)",
    "CREATE TABLE observations (event_id TEXT NOT NULL, family TEXT NOT NULL, "
    "source_id TEXT NOT NULL, observed_at REAL NOT NULL, epoch_id TEXT NOT NULL, "
    "owner_generation INTEGER NOT NULL, payload_json TEXT NOT NULL, "
    "PRIMARY KEY(family, source_id, event_id)) WITHOUT ROWID",
    "CREATE TABLE coverage_epochs (family TEXT NOT NULL, source_id TEXT NOT NULL, "
    "epoch_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, "
    "native_cadence_seconds REAL NOT NULL, owner_generation INTEGER NOT NULL, "
    "PRIMARY KEY(family, source_id, epoch_id)) WITHOUT ROWID",
    "CREATE TABLE unavailable_spans (family TEXT NOT NULL, source_id TEXT NOT NULL, "
    "epoch_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, "
    "native_cadence_seconds REAL NOT NULL, reason TEXT NOT NULL, owner_generation INTEGER NOT NULL, "
    "PRIMARY KEY(family, source_id, epoch_id)) WITHOUT ROWID",
    "CREATE TABLE usage_atoms (event_id TEXT NOT NULL, direction TEXT NOT NULL, "
    "modality TEXT NOT NULL, cache_role TEXT NOT NULL, unit TEXT NOT NULL, observed_at REAL NOT NULL, "
    "payload_json TEXT NOT NULL, PRIMARY KEY(event_id, direction, modality, cache_role, unit)) "
    "WITHOUT ROWID",
    "CREATE TABLE migration_reconciliation (migration_id TEXT PRIMARY KEY, completed_at REAL NOT NULL, "
    "source_digest TEXT NOT NULL, details_json TEXT NOT NULL) WITHOUT ROWID",
    "CREATE TABLE aggregate_publication (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "ring_generation INTEGER NOT NULL, source_generation INTEGER NOT NULL, published_at REAL NOT NULL)",
    "CREATE TABLE aggregate_rings (resolution_seconds INTEGER PRIMARY KEY, slot_count INTEGER NOT NULL, "
    "newest_bucket_start INTEGER) WITHOUT ROWID",
    "CREATE TABLE aggregate_ring_slots (resolution_seconds INTEGER NOT NULL, slot_index INTEGER NOT NULL, "
    "bucket_start INTEGER, bucket_json TEXT, complete INTEGER NOT NULL DEFAULT 0, "
    "source_generation INTEGER NOT NULL DEFAULT 0, ring_generation INTEGER NOT NULL DEFAULT 0, "
    "published_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(resolution_seconds, slot_index)) WITHOUT ROWID",
)

OBSERVATION_COUNT = 250
USAGE_COUNT = 40
RING_SLOTS_POPULATED = 6


def _build_v7_fixture(path: Path, *, source_generation: int = 17, observations: int | None = None) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA application_id = {storage.APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {V7_SCHEMA_VERSION}")
        for statement in _V7_DDL:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_meta(singleton, minimum_writer_protocol, minimum_writer_build, "
            "source_generation, last_vacuumed_at) VALUES(1, ?, ?, ?, 0)",
            (migration_module.V7_MIN_WRITER_PROTOCOL, migration_module.V7_MIN_WRITER_BUILD, source_generation),
        )
        connection.executemany(
            "INSERT INTO observations(event_id, family, source_id, observed_at, epoch_id, "
            "owner_generation, payload_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [
                (f"event-{index:05d}", "cpu", f"host-{index % 3}", 1_000.0 + index,
                 "epoch-a", 1, f'{{"process_percent": {index % 97}}}')
                for index in range(OBSERVATION_COUNT if observations is None else observations)
            ],
        )
        connection.executemany(
            "INSERT INTO usage_atoms(event_id, direction, modality, cache_role, unit, observed_at, "
            "payload_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [
                (f"usage-{index:05d}", "input", "text", "none", "tokens", 2_000.0 + index,
                 f'{{"value": {index}}}')
                for index in range(USAGE_COUNT)
            ],
        )
        connection.execute(
            "INSERT INTO coverage_epochs(family, source_id, epoch_id, started_at, ended_at, "
            "native_cadence_seconds, owner_generation) VALUES('cpu', 'host-0', 'epoch-a', 900.0, NULL, 1.0, 1)"
        )
        connection.execute(
            "INSERT INTO unavailable_spans(family, source_id, epoch_id, started_at, ended_at, "
            "native_cadence_seconds, reason, owner_generation) "
            "VALUES('cpu', 'host-1', 'epoch-a', 950.0, 960.0, 1.0, 'daemon_down', 1)"
        )
        # A REAL v7 store carries the reconciliation record its own activation wrote, and
        # `_active_report` refuses a current database without one. Omitting it made the fixture
        # unrealistic in exactly the way that hid this contract.
        connection.execute(
            "INSERT INTO migration_reconciliation(migration_id, completed_at, source_digest, details_json) "
            "VALUES(?, 10.0, ?, ?)",
            (
                migration_module.MIGRATION_ID,
                "a" * 64,
                json.dumps({
                    "format": 1,
                    "sources": [],
                    "counts": {
                        "observations": OBSERVATION_COUNT if observations is None else observations,
                        "coverage_epochs": 1,
                        "usage_atoms": USAGE_COUNT,
                        "unavailable_spans": 1,
                    },
                    "issue_counts": {},
                    "issues": [],
                    "issues_truncated": 0,
                }),
            ),
        )
        connection.execute(
            "INSERT INTO aggregate_publication(singleton, ring_generation, source_generation, published_at) "
            "VALUES(1, 41, 17, 5000.0)"  # plain SQL numeric: underscore separators are Python, not SQL
        )
        connection.execute("INSERT INTO aggregate_rings(resolution_seconds, slot_count, newest_bucket_start) VALUES(60, 480, 7140)")
        connection.executemany(
            "INSERT INTO aggregate_ring_slots(resolution_seconds, slot_index, bucket_start, bucket_json, "
            "complete, source_generation, ring_generation, published_at) VALUES(60, ?, ?, ?, 1, 17, 41, 5000.0)",
            [(index, 6_000 + index * 60, '{"series": {}, "source": {}}') for index in range(RING_SLOTS_POPULATED)],
        )
        connection.commit()
    finally:
        connection.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in migration_module.V7_FACT_TABLE_COLUMNS
        }
    finally:
        connection.close()


@pytest.fixture
def v7_source(tmp_path):
    path = tmp_path / V7_DATABASE_FILENAME
    _build_v7_fixture(path)
    return path


def test_every_fact_crosses_v7_to_v8_exactly(tmp_path, v7_source):
    target = tmp_path / storage.DATABASE_FILENAME
    before = _counts(v7_source)

    migration_module.migrate_current_v7_database(tmp_path, target, v7_source)

    assert _counts(target) == before
    connection = sqlite3.connect(target)
    try:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == storage.SCHEMA_VERSION
        assert int(
            connection.execute("SELECT source_generation FROM schema_meta WHERE singleton = 1").fetchone()[0]
        ) == 17
        # Identity, not just cardinality: a copy that moved the right NUMBER of rows and the wrong
        # rows would satisfy a count check.
        rows = connection.execute(
            "SELECT event_id, source_id, observed_at, payload_json FROM observations ORDER BY event_id"
        ).fetchall()
        assert rows[0] == ("event-00000", "host-0", 1_000.0, '{"process_percent": 0}')
        assert rows[-1] == (
            f"event-{OBSERVATION_COUNT - 1:05d}",
            f"host-{(OBSERVATION_COUNT - 1) % 3}",
            1_000.0 + OBSERVATION_COUNT - 1,
            f'{{"process_percent": {(OBSERVATION_COUNT - 1) % 97}}}',
        )
        assert connection.execute(
            "SELECT reason FROM unavailable_spans"
        ).fetchone()[0] == "daemon_down"
    finally:
        connection.close()


def test_the_v7_source_is_byte_identical_and_gains_no_sidecars(tmp_path, v7_source):
    target = tmp_path / storage.DATABASE_FILENAME
    before = _digest(v7_source)

    migration_module.migrate_current_v7_database(tmp_path, target, v7_source)

    assert _digest(v7_source) == before, "the v7 source must survive as the rollback boundary"
    assert not Path(f"{v7_source}-wal").exists()
    assert not Path(f"{v7_source}-shm").exists()


def test_v7_ring_payloads_become_rebuild_work_rather_than_copied_bytes(tmp_path, v7_source):
    """A v7 payload carries no version, so it is invalidated for replay instead of trusted."""
    target = tmp_path / storage.DATABASE_FILENAME

    migration_module.migrate_current_v7_database(tmp_path, target, v7_source)

    connection = sqlite3.connect(target)
    try:
        pending = connection.execute(
            "SELECT resolution_seconds, bucket_start, reason, applied_at FROM ring_invalidations "
            "ORDER BY bucket_start"
        ).fetchall()
        assert len(pending) == RING_SLOTS_POPULATED
        assert {row[0] for row in pending} == {60}
        assert [row[1] for row in pending] == [6_000 + index * 60 for index in range(RING_SLOTS_POPULATED)]
        assert {row[2] for row in pending} == {"v7_payload_unversioned"}
        assert {row[3] for row in pending} == {None}, "every recorded rebuild must start unapplied"
        # No plausible bytes were carried over.
        assert int(
            connection.execute(
                "SELECT count(*) FROM aggregate_ring_slots WHERE bucket_json IS NOT NULL"
            ).fetchone()[0]
        ) == 0
        # Publication LINEAGE is preserved so a later generation cannot collide with an issued one.
        assert int(
            connection.execute(
                "SELECT ring_generation FROM aggregate_publication WHERE singleton = 1"
            ).fetchone()[0]
        ) == 41
        # The cursor must NOT claim anything was folded into this ring.
        assert {
            row[0] for row in connection.execute("SELECT folded_source_generation FROM ring_replay_cursor")
        } == {0}
    finally:
        connection.close()


@pytest.mark.parametrize("boundary", migration_module.V8_MIGRATION_BOUNDARIES)
def test_every_migration_boundary_leaves_an_actionable_state(tmp_path, v7_source, boundary):
    """Interrupt at each named boundary; never accept a mixed or half-written target.

    `after_commit` is deliberately included and deliberately different: by then the rename has
    already happened, so the target legitimately EXISTS and must be a complete, openable v8. Every
    earlier boundary must leave no target at all.
    """
    target = tmp_path / storage.DATABASE_FILENAME
    source_before = _digest(v7_source)

    def explode(fired: str) -> None:
        if fired == boundary:
            raise migration_module.V8MigrationFault(f"injected at {fired}")

    with pytest.raises(migration_module.V8MigrationFault):
        migration_module.migrate_current_v7_database(tmp_path, target, v7_source, fault_hook=explode)

    assert _digest(v7_source) == source_before, "an interrupted migration must not touch the source"
    if boundary == "after_commit":
        assert target.exists()
        with storage.Store.open(target) as store:
            assert store is not None
        assert _counts(target) == _counts(v7_source)
    else:
        assert not target.exists(), f"{boundary} left a partial target reachable under the real name"
    # No shadow directory survives to be mistaken for a resumable target.
    assert [entry.name for entry in tmp_path.iterdir() if entry.name.startswith(".stats-v8-migration-")] == []


def test_a_restarted_migration_after_an_interruption_completes_exactly_once(tmp_path, v7_source):
    """Restart is the resume path, and it must neither duplicate nor skip a row."""
    target = tmp_path / storage.DATABASE_FILENAME

    def explode(fired: str) -> None:
        if fired == "before_cutover":
            raise migration_module.V8MigrationFault("injected")

    with pytest.raises(migration_module.V8MigrationFault):
        migration_module.migrate_current_v7_database(tmp_path, target, v7_source, fault_hook=explode)
    assert not target.exists()

    migration_module.migrate_current_v7_database(tmp_path, target, v7_source)

    assert _counts(target) == _counts(v7_source)


def test_a_repeated_migration_refuses_rather_than_overwriting_a_live_v8(tmp_path, v7_source):
    """Negative control: the second run must not silently rebuild a store already in service."""
    target = tmp_path / storage.DATABASE_FILENAME
    migration_module.migrate_current_v7_database(tmp_path, target, v7_source)

    with pytest.raises(migration_module.MigrationError):
        migration_module.migrate_current_v7_database(tmp_path, target, v7_source)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    (
        (lambda c: c.execute(f"PRAGMA user_version = {storage.SCHEMA_VERSION}"), "user_version"),
        (lambda c: c.execute("PRAGMA user_version = 99"), "user_version"),
        (lambda c: c.execute("PRAGMA application_id = 12345"), "application id"),
        (lambda c: c.execute("DROP TABLE usage_atoms"), "missing fact tables"),
        (lambda c: c.execute("DROP TABLE schema_meta"), "schema_meta"),
    ),
)
def test_an_unsupported_or_corrupt_source_fails_closed(tmp_path, v7_source, mutate, expected):
    """A source that is not exactly v7 is refused before any shadow is created."""
    connection = sqlite3.connect(v7_source)
    try:
        mutate(connection)
        connection.commit()
    finally:
        connection.close()
    target = tmp_path / storage.DATABASE_FILENAME

    with pytest.raises(migration_module.MigrationError) as raised:
        migration_module.migrate_current_v7_database(tmp_path, target, v7_source)

    assert expected in str(raised.value)
    assert not target.exists()


def test_an_unknown_fault_boundary_is_refused(tmp_path):
    """The fault hook must not accept a name that can never fire.

    A typo'd boundary would make every interruption test pass by never interrupting anything.
    """
    with pytest.raises(migration_module.MigrationError):
        migration_module._v8_fault(lambda _name: None, "before_shadow_cretaion")


def test_the_copy_is_chunked_rather_than_one_unbounded_statement(tmp_path, v7_source, monkeypatch):
    """The fact copy must page, so the transaction is bounded and the volume is observable.

    Traced through SQLite itself rather than by patching the driver: with a chunk smaller than the
    fixture a bounded copier issues several keyed SELECTs against the attached source, while a
    single `INSERT ... SELECT` would issue exactly one and report nothing about what it moved.
    """
    monkeypatch.setattr(migration_module, "V8_MIGRATION_ROW_CHUNK", 25)
    target = tmp_path / storage.DATABASE_FILENAME
    statements: list[str] = []
    real_open = storage.Store.open

    def traced_open(path, **kwargs):
        store = real_open(path, **kwargs)
        store._connection().set_trace_callback(statements.append)
        return store

    monkeypatch.setattr(storage.Store, "open", staticmethod(traced_open))
    migration_module.migrate_current_v7_database(tmp_path, target, v7_source)
    monkeypatch.undo()

    selects = [text for text in statements if "FROM source_v7.observations" in text]
    assert len(selects) >= OBSERVATION_COUNT // 25, selects
    # Keyed pagination, not OFFSET: an OFFSET scan re-walks the prefix and turns a linear copy
    # into a quadratic one on a real store.
    assert any("WHERE (" in text for text in selects)
    assert not any("OFFSET" in text.upper() for text in selects)
    assert _counts(target) == _counts(v7_source)


# Both arms measured at this fixture size, so the bound discriminates instead of merely passing.
# In the isolated test container, which is what CI measures:
#   bounded (counts streamed during the copy) ....  0.0 MiB delta
#   whole-store (`_active_report` re-read) ....... 44.3 MiB delta
# 20 MiB therefore clears the real implementation by a wide margin and still fails the regression
# by better than 2x. A tighter 40 MiB bound was rejected: it caught the regression by only 7% in
# the container, which is inside allocator noise and would flake rather than inform.
#
# The DELTA is asserted rather than absolute RSS: absolute peak carries a ~65 MiB interpreter
# baseline that varies by build and would let the bound drift without anyone noticing.
MIGRATION_OBSERVATION_ROWS = 200_000
MIGRATION_PEAK_RSS_DELTA_BUDGET_MIB = 20.0


def test_migration_peak_memory_is_bounded_by_its_chunk_not_by_the_store(tmp_path):
    """A migration must not decode the whole store to report what it moved.

    Measured in a FRESH SUBPROCESS because `ru_maxrss` is a whole-process high-water mark: taken
    in-process it reports the largest thing any earlier test did, so it would pass for reasons
    unrelated to this code.

    The number this pins is not theoretical. Building the report with `_active_report()` -- the
    obvious call, which goes through `read_snapshot()` -- measured peak RSS 44.9 -> 1279.0 MiB on a
    representative 415 MiB v7 store, +1234 MiB in one call. Streaming the counts during the copy
    instead measured a 15.2 MiB delta for 829,020 observations.
    """
    source = tmp_path / V7_DATABASE_FILENAME
    _build_v7_fixture(source, observations=MIGRATION_OBSERVATION_ROWS)
    target = tmp_path / storage.DATABASE_FILENAME
    program = f"""
import json, resource, sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from pathlib import Path
from yolomux_lib.stats_current import migration as m
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
report = m.migrate_current_v7_database(Path({str(tmp_path)!r}), Path({str(target)!r}), Path({str(source)!r}))
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(json.dumps({{"base": base, "peak": peak, "observations": report.observations}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=900,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    measured = json.loads(completed.stdout.strip().splitlines()[-1])
    delta = measured["peak"] - measured["base"]

    assert measured["observations"] == MIGRATION_OBSERVATION_ROWS, (
        "the bound is meaningless if the copy did no work"
    )
    assert delta < MIGRATION_PEAK_RSS_DELTA_BUDGET_MIB, (
        f"migration grew peak RSS by {delta:.1f} MiB against a {MIGRATION_PEAK_RSS_DELTA_BUDGET_MIB} "
        f"MiB budget; a whole-store decode has come back"
    )


# --- v7/v8 coexistence: every companion artifact, not just the main database -------------------
# The first version of this file checked only the `.sqlite3` bytes and its -wal/-shm. That passed
# while a v8 open was rewriting the v7 build's writer fence to `schema_version: 8`, which made the
# still-running v7 build raise SchemaTooNewError against its OWN database. The rollback boundary
# was destroyed and the coverage said everything was fine.

V7_COMPANIONS = (
    "stats-writer-compat.json",
    "stats-prune.json",
)


def _v7_artifacts(directory: Path) -> dict[str, tuple]:
    """Bytes, mode, inode and mtime for every v7 artifact in one state directory."""
    captured = {}
    for name in (V7_DATABASE_FILENAME, f"{V7_DATABASE_FILENAME}-wal", f"{V7_DATABASE_FILENAME}-shm", *V7_COMPANIONS):
        path = directory / name
        if not path.exists():
            continue
        stat = path.stat()
        captured[name] = (path.read_bytes(), stat.st_mode, stat.st_ino, stat.st_mtime_ns)
    return captured


def _seed_v7_companions(directory: Path) -> None:
    (directory / "stats-writer-compat.json").write_text(
        json.dumps({
            "application_id": storage.APPLICATION_ID,
            "database_filename": V7_DATABASE_FILENAME,
            "schema_version": V7_SCHEMA_VERSION,
            "minimum_writer_protocol": migration_module.V7_MIN_WRITER_PROTOCOL,
            "minimum_writer_build": migration_module.V7_MIN_WRITER_BUILD,
        }, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (directory / "stats-prune.json").write_text(
        json.dumps({"pruned_at": 12_345.0}), encoding="utf-8"
    )
    (directory / f"{V7_DATABASE_FILENAME}-wal").write_bytes(b"v7 write-ahead log")
    (directory / f"{V7_DATABASE_FILENAME}-shm").write_bytes(b"v7 shared memory")


def test_the_v8_companion_filenames_are_versioned_and_cannot_collide_with_v7():
    """Every mutable companion carries the schema version, like the database and the socket."""
    assert storage.DATABASE_FILENAME == "stats-v8.sqlite3"
    assert storage.WRITER_FENCE_FILENAME == "stats-writer-compat-v8.json"
    assert storage.PRUNE_STATE_FILENAME == "stats-prune-v8.json"
    for name in (storage.WRITER_FENCE_FILENAME, storage.PRUNE_STATE_FILENAME):
        assert name not in V7_COMPANIONS, f"{name} still collides with a v7 companion"


def test_a_v8_open_preserves_every_v7_artifact_including_fence_and_prune_state(tmp_path, v7_source):
    """The rollback boundary, asserted over the artifacts that actually carry it.

    A v7 build reads its fence on every open and refuses a database whose fence names a NEWER
    schema. If a v8 open rewrites that shared fence, the v7 build can no longer open its own store
    and there is no rolling back.
    """
    _seed_v7_companions(tmp_path)
    before = _v7_artifacts(tmp_path)
    assert set(before) >= {V7_DATABASE_FILENAME, *V7_COMPANIONS}

    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME):
        pass

    assert _v7_artifacts(tmp_path) == before
    fence = json.loads((tmp_path / "stats-writer-compat.json").read_text())
    assert fence["schema_version"] == V7_SCHEMA_VERSION
    assert fence["database_filename"] == V7_DATABASE_FILENAME


@pytest.mark.parametrize("outcome", ("success", "refused", "failed"))
def test_no_migration_outcome_disturbs_a_pre_existing_v7_artifact(tmp_path, v7_source, outcome):
    """Success, refusal and mid-flight failure must all leave the v7 side untouched."""
    _seed_v7_companions(tmp_path)
    before = _v7_artifacts(tmp_path)
    target = tmp_path / storage.DATABASE_FILENAME

    if outcome == "success":
        migration_module.migrate_current_v7_database(tmp_path, target, v7_source)
    elif outcome == "refused":
        connection = sqlite3.connect(v7_source)
        try:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
        finally:
            connection.close()
        before = _v7_artifacts(tmp_path)
        with pytest.raises(migration_module.MigrationError):
            migration_module.migrate_current_v7_database(tmp_path, target, v7_source)
    else:
        def explode(fired: str) -> None:
            if fired == "during_population":
                raise migration_module.V8MigrationFault("injected")

        with pytest.raises(migration_module.V8MigrationFault):
            migration_module.migrate_current_v7_database(tmp_path, target, v7_source, fault_hook=explode)

    after = _v7_artifacts(tmp_path)
    assert set(after) == set(before)
    for name, expected in before.items():
        assert after[name] == expected, f"{name} changed during a {outcome} migration"


# --- real startup dispatch ---------------------------------------------------------------------
# Adding a migration function proves nothing if nothing calls it. `migrate()` is the one entry the
# service startup uses, and it dispatched v5 while ignoring v7 entirely -- so a v8 build starting
# beside a valid v7 store would have activated an EMPTY v8 and reported success.


def test_startup_dispatch_migrates_a_released_v7_instead_of_activating_an_empty_v8(tmp_path, v7_source):
    """The real entry point, not the migration function directly."""
    target = tmp_path / storage.DATABASE_FILENAME

    report = migration_module.migrate(migration_module.MigrationInputs(state_dir=tmp_path), target)

    assert target.exists()
    assert _counts(target) == _counts(v7_source)
    assert report.observations == OBSERVATION_COUNT, (
        "startup activated a store that does not contain the released v7 history"
    )


def test_a_bypassed_dispatcher_is_caught_rather_than_reported_green(tmp_path, v7_source, monkeypatch):
    """Failing control: if the v7 arm is removed, the dispatch test above must fail.

    Without this row, deleting the dispatch would leave `migrate()` happily building an empty v8
    and the suite would still be green -- which is exactly the state this slice found.
    """
    monkeypatch.setattr(migration_module, "_v7_migration_source", lambda _state_dir: None)
    target = tmp_path / storage.DATABASE_FILENAME

    report = migration_module.migrate(migration_module.MigrationInputs(state_dir=tmp_path), target)

    assert report.observations == 0, (
        "the bypass control did not actually bypass; the dispatch assertion above proves nothing"
    )
    assert _counts(target)["observations"] == 0


def test_a_released_v7_takes_precedence_over_an_older_v5_source(tmp_path, v7_source):
    """Newest released format wins. A v5 file left behind must not shadow the live v7 history."""
    (tmp_path / migration_module.V5_DATABASE_FILENAME).write_bytes(b"stale v5 leftovers")
    target = tmp_path / storage.DATABASE_FILENAME

    report = migration_module.migrate(migration_module.MigrationInputs(state_dir=tmp_path), target)

    assert report.observations == OBSERVATION_COUNT
    assert _counts(target) == _counts(v7_source)


def test_repeated_startup_leaves_one_marker_and_does_not_rewrite_the_target(tmp_path, v7_source):
    """Startup runs on every boot, so it must be a no-op once the v8 store is active."""
    target = tmp_path / storage.DATABASE_FILENAME
    migration_module.migrate(migration_module.MigrationInputs(state_dir=tmp_path), target)
    first_bytes = target.read_bytes()
    first_mtime = target.stat().st_mtime_ns

    migration_module.migrate(migration_module.MigrationInputs(state_dir=tmp_path), target)
    migration_module.migrate(migration_module.MigrationInputs(state_dir=tmp_path), target)

    assert target.read_bytes() == first_bytes
    assert target.stat().st_mtime_ns == first_mtime
    connection = sqlite3.connect(target)
    try:
        markers = connection.execute(
            "SELECT count(*) FROM migration_reconciliation WHERE migration_id = ?",
            (migration_module.MIGRATION_ID,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert markers == 1, "repeated startup must not accumulate reconciliation rows"

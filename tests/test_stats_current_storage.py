# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused contract tests for the current-only YO!stats store."""

import inspect
import json
import random
import sqlite3
import subprocess
import sys
import time
import weakref
from dataclasses import replace
from pathlib import Path

import pytest

from yomux_lib.stats_current import APPLICATION_ID
from yomux_lib.stats_current import DATABASE_FILENAME
from yomux_lib.stats_current import MIN_WRITER_BUILD
from yomux_lib.stats_current import MIN_WRITER_PROTOCOL
from yomux_lib.stats_current import MigrationReconciliation
from yomux_lib.stats_current import RETENTION_SECONDS
from yomux_lib.stats_current.storage import _RING_TABLES
from yomux_lib.stats_current import SCHEMA_VERSION
from yomux_lib.stats_current import CoverageEpoch
from yomux_lib.stats_current import Observation
from yomux_lib.stats_current import SchemaTooNewError
from yomux_lib.stats_current import SchemaMismatchError
from yomux_lib.stats_current import Store
from yomux_lib.stats_current import StatsCurrentError
from yomux_lib.stats_current import StorageValidationError
from yomux_lib.stats_current import UnavailableSpan
from yomux_lib.stats_current import UsageAtom
from yomux_lib.stats_current import UsageAtomTombstone
from yomux_lib.stats_current.storage import VACUUM_BASELINE_FILENAME
from yomux_lib.stats_current import WRITER_FENCE_FILENAME
from yomux_lib.stats_current import materializer as materializer_module
from yomux_lib.stats_current import resolution as stats_resolution
from yomux_lib.stats_current import storage as storage_module


def _observation(family: str, source_id: str, observed_at: float) -> Observation:
    return Observation(
        f"{family}:{source_id}:{observed_at}", family, source_id, observed_at, "epoch-1", 1,
        {"value": observed_at},
    )


def _usage(event_id: str, observed_at: float, *, direction: str = "input") -> UsageAtom:
    return UsageAtom(event_id, direction, "text", "none", "tokens", observed_at, {
        "quantity": 7,
        "provider": "test-provider",
        "model": "test-model",
        "agent_id": "test-agent",
        "telemetry_complete": True,
    })


def _files(path):
    files = {}
    for item in path.parent.iterdir():
        metadata = item.stat()
        files[item.name] = (
            item.read_bytes(), metadata.st_ino, metadata.st_mode, metadata.st_size,
            metadata.st_mtime_ns,
        )
    return files


def _sqlite_metadata(connection):
    return {
        name: connection.execute(f"PRAGMA {name}").fetchone()[0]
        for name in (
            "application_id", "freelist_count", "journal_mode", "page_count",
            "schema_version", "user_version",
        )
    }


def test_schema_contains_only_original_facts_and_current_metadata(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    store = Store.open(path)
    store.close()
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        # Schema 8 creates the ring extension with the database rather than on request, so the
        # exact shape is the fact tables PLUS the ring kernel. Listing both explicitly keeps this a
        # closed set: an unexpected table still fails here rather than being absorbed by a wildcard.
        assert tables == {
            "coverage_epochs",
            "migration_reconciliation",
            "observations",
            "schema_meta",
            "unavailable_spans",
            "usage_atoms",
            "aggregate_publication",
            "aggregate_rings",
            "aggregate_ring_slots",
            "ring_replay_cursor",
            "ring_invalidations",
        }
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # Only the FACT tables are pinned here. `_validate_ring_schema` is the single owner of the
        # ring extension's exact columns and already fails closed on any drift, so restating them
        # would create a second copy of one contract.
        columns = {
            table: tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
            for table in tables
            if table not in _RING_TABLES
        }
        assert columns == {
            "coverage_epochs": (
                "family", "source_id", "epoch_id", "started_at", "ended_at",
                "native_cadence_seconds", "owner_generation",
            ),
            "migration_reconciliation": (
                "migration_id", "completed_at", "source_digest", "details_json",
            ),
            "observations": (
                "event_id", "family", "source_id", "observed_at", "epoch_id", "owner_generation",
                "payload_json",
            ),
            "schema_meta": (
                "singleton", "minimum_writer_protocol", "minimum_writer_build", "source_generation",
                "last_vacuumed_at",
            ),
            "usage_atoms": (
                "event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json",
            ),
            "unavailable_spans": (
                "family", "source_id", "epoch_id", "started_at", "ended_at",
                "native_cadence_seconds", "reason", "owner_generation",
            ),
        }
        assert not any("bucket" in column for names in columns.values() for column in names)
        assert connection.execute(
            "SELECT minimum_writer_protocol, minimum_writer_build, source_generation FROM schema_meta"
        ).fetchone() == (MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, 0)
    finally:
        connection.close()


def test_vacuum_persists_its_completion_marker_only_after_success(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        assert store.last_vacuumed_at() == 0.0
        assert store.vacuum(123.0) == 123.0
        assert store.last_vacuumed_at() == 123.0

    with Store.open_reader(path) as reader:
        assert reader.last_vacuumed_at() == 123.0
        with pytest.raises(StatsCurrentError, match="cannot vacuum"):
            reader.vacuum(124.0)


def test_vacuum_reclaims_pruned_raw_table_pages(tmp_path):
    """A successful marker alone is insufficient: VACUUM must shrink raw-table churn."""

    path = tmp_path / DATABASE_FILENAME
    observations = tuple(
        Observation(
            f"fragment-{index}", "cpu", "fragmented", float(index), "epoch-1", 1,
            {"ordinal": index, "payload": "x" * 16_384},
        )
        for index in range(128)
    )
    with Store.open(path) as store:
        assert store.append_batch(observations=observations).observations_accepted == len(observations)
    with Store.open(path) as store:
        assert store.prune(now=RETENTION_SECONDS + len(observations) + 1).observations_deleted == len(observations)

    before_bytes = path.stat().st_size
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] > 0
    finally:
        connection.close()

    with Store.open(path) as store:
        assert store.vacuum(123.0) == 123.0
        assert store.last_vacuumed_at() == 123.0
        assert path.with_name(f"{path.name}-wal").stat().st_size == 0
    assert path.stat().st_size < before_bytes
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
    finally:
        connection.close()


# --- compaction benefit: the metric that decides whether a rewrite is worth doing ------------
#
# The whole point of these is the SUBTRACTION. Raw reclaimable space is never zero, because every
# schema has a natural B-tree fill, so a threshold on the raw figure rewrites the file forever and
# recovers nothing. Measured against its own post-vacuum baseline the same store reads 0.000.

BENEFIT_PAD = 64


def _benefit_observations(start, stop, pad=BENEFIT_PAD):
    return [
        Observation(
            f"e-{index:012d}", "cpu", f"s{index % 5}", float(index), "epoch-1", 1,
            {"value": index, "pad": "x" * pad},
        )
        for index in range(start, stop)
    ]


def _fill(store, total, pad=BENEFIT_PAD, chunk=25_000):
    for start in range(0, total, chunk):
        store.append_batch(observations=_benefit_observations(start, min(start + chunk, total), pad))


def _benefit(store):
    return store.reclaimable_ratio() - store.reclaimable_ratio_at_last_vacuum()


def test_the_benefit_metric_reads_zero_after_a_vacuum_while_the_raw_figure_stays_above_the_floor(tmp_path):
    """Must-skip, and the raw assertion documents exactly why the subtraction exists.

    If someone deletes the baseline term and returns the raw ratio, this fails loudly rather than
    quietly rewriting a compacted 200MB file every hour and recovering nothing. Audited on four
    real databases whose truly recoverable space was 0.0000%: raw read 3.600%, 3.864%, 3.929% and
    3.576%.
    """
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        _fill(store, 20_000)
        store.vacuum(1_000.0)

        assert _benefit(store) == 0.0
        # The floor the subtraction removes. Measured 0.038959 on this fixture, inside the audited
        # 3.5-3.9% band. A raw-figure guard at any threshold below this compacts forever.
        assert store.reclaimable_ratio() > 0.03


def test_the_benefit_metric_rises_with_random_key_appends_while_the_free_list_stays_empty(tmp_path):
    """The property this whole item exists for.

    Nothing was deleted, so `freelist_count` is 0 and any free-list-only metric reads exactly zero.
    The space is real: random primary keys split B-tree pages and leave slack inside pages that are
    still live, which only `dbstat.unused` can see.
    """
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        _fill(store, 20_000)
        store.vacuum(1_000.0)
        assert _benefit(store) == 0.0

        random_source = random.Random(7)
        store.append_batch(observations=[
            Observation(
                f"r-{random_source.getrandbits(48):014d}", "cpu",
                f"s{random_source.getrandbits(8) % 5}", float(index), "epoch-1", 1,
                {"value": index, "pad": "x" * BENEFIT_PAD},
            )
            for index in range(20_000)
        ])
        store._connection().execute("PRAGMA wal_checkpoint(TRUNCATE)")

        assert store._connection().execute("PRAGMA freelist_count").fetchone()[0] == 0
        # Measured 0.065915 on this fixture. A free-list-only metric would read 0.000 here.
        assert _benefit(store) > 0.02


def test_the_benefit_metric_predicts_the_measured_shrink_without_ever_over_predicting(tmp_path):
    """ASYMMETRIC on purpose. Over-prediction wastes a rewrite; under-prediction only delays one.

    The over bound is 0.001 and is the load-bearing half: across every fixture measured for this
    change the metric has never once over-predicted. The under bound carries the baseline term
    because that is the measured structure of the error, not a fudge -- the metric omits the slack
    the surviving rows will still carry after the rewrite, which is what the baseline measures.
    Measured errors, all under-predicting: -0.98 pp at this shape, -1.51 and -1.85 pp at a quarter
    the row count, -11.72 pp on a payload built from overflow pages.
    """
    path = tmp_path / DATABASE_FILENAME
    total = 60_000
    with Store.open(path) as store:
        _fill(store, total)
        store.vacuum(1_000.0)
        baseline = store.reclaimable_ratio_at_last_vacuum()

        store.prune(now=RETENTION_SECONDS + total * 0.7409)
        connection = store._connection()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        predicted = _benefit(store)
        pages_before = int(connection.execute("PRAGMA page_count").fetchone()[0])

        store.vacuum(2_000.0)
        pages_after = int(store._connection().execute("PRAGMA page_count").fetchone()[0])
        actual = (pages_before - pages_after) / pages_before

        assert predicted <= actual + 0.001, (
            f"the metric OVER-predicted: {predicted:.6f} against a measured {actual:.6f}"
        )
        assert predicted >= actual - baseline - 0.005, (
            f"the metric under-predicted by more than its baseline term: predicted {predicted:.6f}, "
            f"measured {actual:.6f}, baseline {baseline:.6f}"
        )
        # And the point of the fixture: this case is the one that clears the policy threshold.
        assert predicted >= 0.15


def test_a_database_matching_the_audited_page_geometry_is_still_decided_on_measured_slack(tmp_path):
    """Page geometry and an empty free list carry NO verdict, in either direction.

    Built at runtime rather than committed, because the artifact is 215MB. The geometry is the
    audited one, and on it the RAW figure reads 0.158461 -- above the 15.0% threshold, so a
    raw-figure guard would rewrite this database. It is freshly compacted and a rewrite would
    return nothing, and the metric says so.

    The `is False` below encodes the 15.0% POLICY choice, not a physical fact. A rewrite costs
    about 3.008x the post-rewrite size in writes; the threshold is where that stops being worth it
    on this cadence. Change the policy and this assertion changes with it.
    """
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        _fill(store, 48_449, pad=3_800, chunk=8_000)
        store.vacuum(1_000.0)
        connection = store._connection()

        assert int(connection.execute("PRAGMA page_size").fetchone()[0]) == 4_096
        assert abs(int(connection.execute("PRAGMA page_count").fetchone()[0]) - 54_917) <= 64
        assert int(connection.execute("PRAGMA freelist_count").fetchone()[0]) == 0
        assert abs(path.stat().st_size - 224_940_032) <= 262_144

        assert store.reclaimable_ratio() > 0.15, "the fixture no longer exercises the raw-vs-C5 gap"
        assert (_benefit(store) >= 0.15) is False


def test_an_unknown_baseline_reads_as_zero_rather_than_as_a_stale_number(tmp_path):
    """A sidecar naming a different vacuum than the database does must not be believed.

    A crash between the marker transaction and the sidecar leaves exactly that state. Reading the
    previous rewrite's baseline as though it described the current one would suppress a needed
    compaction; reading it as unknown costs at most one extra rewrite, which rewrites both halves
    and self-corrects.
    """
    path = tmp_path / DATABASE_FILENAME
    sidecar = path.parent / VACUUM_BASELINE_FILENAME
    with Store.open(path) as store:
        _fill(store, 5_000)
        assert store.reclaimable_ratio_at_last_vacuum() == 0.0, "a never-vacuumed store has none"

        store.vacuum(1_000.0)
        recorded = store.reclaimable_ratio_at_last_vacuum()
        assert recorded > 0.0 and json.loads(sidecar.read_text())["last_vacuumed_at"] == 1_000.0

        sidecar.write_text(json.dumps(
            {"last_vacuumed_at": 999.0, "reclaimable_ratio": recorded}
        ), encoding="utf-8")
        assert store.reclaimable_ratio_at_last_vacuum() == 0.0

        sidecar.write_text("{not json", encoding="utf-8")
        assert store.reclaimable_ratio_at_last_vacuum() == 0.0
        sidecar.unlink()
        assert store.reclaimable_ratio_at_last_vacuum() == 0.0


def test_a_baseline_ratio_above_one_is_rejected_like_a_negative_one(tmp_path):
    """The validator's own docstring names this hazard and did not guard it.

    A ratio is a fraction of the file, so above 1.0 is impossible. `_record_vacuum_baseline`
    cannot produce one -- it is bounded by construction -- so this is corruption of the 0o600
    sidecar. Believed, it makes the benefit permanently NEGATIVE, which is precisely the
    "fabricated high baseline would suppress a needed compaction indefinitely and leave the
    disk to fill" case the docstring calls out. Finiteness and `< 0` were checked; `> 1` was not.
    """
    path = tmp_path / DATABASE_FILENAME
    sidecar = path.parent / VACUUM_BASELINE_FILENAME
    with Store.open(path) as store:
        _fill(store, 5_000)
        store.vacuum(1_000.0)
        assert store.reclaimable_ratio_at_last_vacuum() > 0.0

        for impossible in (1.5, 2.0, 1e9):
            sidecar.write_text(json.dumps(
                {"last_vacuumed_at": 1_000.0, "reclaimable_ratio": impossible}
            ), encoding="utf-8")
            assert store.reclaimable_ratio_at_last_vacuum() == 0.0, impossible

        # Exactly 1.0 is degenerate but not impossible, and rejecting it would be a behaviour
        # change beyond the finding: an entirely empty file IS wholly reclaimable.
        sidecar.write_text(json.dumps(
            {"last_vacuumed_at": 1_000.0, "reclaimable_ratio": 1.0}
        ), encoding="utf-8")
        assert store.reclaimable_ratio_at_last_vacuum() == 1.0


def test_current_database_uses_a_versioned_path_and_publishes_the_fence_first(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    assert DATABASE_FILENAME == f"stats-v{SCHEMA_VERSION}.sqlite3"
    assert DATABASE_FILENAME != "stats-history.sqlite3"

    with Store.open(path) as store:
        connection = store._connection()
        assert connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == (
            storage_module.WAL_AUTOCHECKPOINT_PAGES
        )
        assert connection.execute("PRAGMA journal_size_limit").fetchone()[0] == (
            storage_module.WAL_ALLOCATION_CEILING_BYTES
        )
        assert path.with_name(f"{path.name}-wal").stat().st_size == 0

    fence = json.loads((tmp_path / WRITER_FENCE_FILENAME).read_text(encoding="utf-8"))
    assert fence == {
        "application_id": APPLICATION_ID,
        "database_filename": DATABASE_FILENAME,
        "schema_version": SCHEMA_VERSION,
        "minimum_writer_protocol": MIN_WRITER_PROTOCOL,
        "minimum_writer_build": MIN_WRITER_BUILD,
    }

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()


def test_writer_open_truncates_a_recycled_oversized_wal_allocation(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    wal_path = path.with_name(f"{path.name}-wal")
    with Store.open(path) as first:
        observations = tuple(
            Observation(
                f"retained-{index}", "cpu", "host", float(index), "epoch-1", 1,
                {"payload": "x" * 16_384},
            )
            for index in range(128)
        )
        assert first.append_batch(observations=observations).observations_accepted == len(observations)
        assert first._connection().execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[0] == 0
        retained_bytes = wal_path.stat().st_size
        assert retained_bytes > 0
        first.close()
        with Store.open(path):
            assert wal_path.stat().st_size == 0


def test_current_store_rejects_legacy_names_and_symbolic_link_aliases(tmp_path):
    with pytest.raises(SchemaMismatchError, match="must be named"):
        Store.open(tmp_path / "stats-history.sqlite3")
    assert not (tmp_path / "stats-history.sqlite3").exists()
    assert not (tmp_path / WRITER_FENCE_FILENAME).exists()

    real_dir = tmp_path / "real"
    alias_dir = tmp_path / "alias"
    real_dir.mkdir()
    alias_dir.mkdir()
    real_path = real_dir / DATABASE_FILENAME
    Store.open(real_path).close()
    alias_path = alias_dir / DATABASE_FILENAME
    alias_path.symlink_to(real_path)
    before = _files(real_path)

    with pytest.raises(SchemaMismatchError, match="symbolic link"):
        Store.open(alias_path)

    assert _files(real_path) == before


def test_writer_lease_resolves_parent_directory_aliases(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    alias_dir = tmp_path / "alias"
    alias_dir.symlink_to(real_dir, target_is_directory=True)
    real_path = real_dir / DATABASE_FILENAME
    alias_path = alias_dir / DATABASE_FILENAME

    with storage_module.writer_lease(alias_path):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path\n"
                "import sys\n"
                "from yomux_lib.stats_current.storage import Store\n"
                "try:\n"
                "    Store.open(Path(sys.argv[1]))\n"
                "except Exception as error:\n"
                "    print(type(error).__name__, str(error))\n"
                "else:\n"
                "    print('opened')\n",
                str(real_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        assert probe.stdout.startswith("StatsCurrentError YO!stats writer is already owned")

    with Store.open(real_path):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path\n"
                "import sys\n"
                "from yomux_lib.stats_current.storage import Store\n"
                "try:\n"
                "    Store.open(Path(sys.argv[1]))\n"
                "except Exception as error:\n"
                "    print(type(error).__name__, str(error))\n"
                "else:\n"
                "    print('opened')\n",
                str(alias_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        assert probe.stdout.startswith("StatsCurrentError YO!stats writer is already owned")


def test_writer_preflight_runs_under_the_shared_lease(tmp_path, monkeypatch):
    path = tmp_path / DATABASE_FILENAME
    observed = []
    original = Store._preflight_fence

    def probe(database_path, writer_protocol, writer_build):
        observed.append(str(Path(database_path).absolute()) in storage_module._WRITER_LEASES)
        return original(database_path, writer_protocol, writer_build)

    monkeypatch.setattr(Store, "_preflight_fence", staticmethod(probe))
    with Store.open(path):
        pass
    assert observed == [True]


def test_older_protocol_runner_cannot_create_or_open_the_current_database(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with pytest.raises(SchemaTooNewError):
        Store.open(path, writer_protocol=MIN_WRITER_PROTOCOL - 1)
    assert not path.exists()

    Store.open(path).close()
    before = _files(path)
    with pytest.raises(SchemaTooNewError):
        Store.open(path, writer_protocol=MIN_WRITER_PROTOCOL - 1)
    assert _files(path) == before


def test_open_embeds_the_current_protocol_in_a_pre_protocol_24_database(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE schema_meta SET minimum_writer_protocol = ? WHERE singleton = 1",
        (MIN_WRITER_PROTOCOL - 1,),
    )
    connection.commit()
    connection.close()
    (tmp_path / WRITER_FENCE_FILENAME).write_text(json.dumps({
        "application_id": APPLICATION_ID,
        "database_filename": DATABASE_FILENAME,
        "schema_version": SCHEMA_VERSION,
        "minimum_writer_protocol": MIN_WRITER_PROTOCOL - 1,
        "minimum_writer_build": MIN_WRITER_BUILD,
    }), encoding="utf-8")

    Store.open(path).close()

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT minimum_writer_protocol FROM schema_meta WHERE singleton = 1"
    ).fetchone()[0] == MIN_WRITER_PROTOCOL
    connection.close()
    with pytest.raises(SchemaTooNewError):
        Store._preflight(path, MIN_WRITER_PROTOCOL - 1, MIN_WRITER_BUILD)


def test_writer_compatibility_probe_never_creates_the_database_or_fence(tmp_path):
    path = tmp_path / DATABASE_FILENAME

    storage_module.require_compatible_writer(path)

    assert not path.exists()
    assert not (tmp_path / WRITER_FENCE_FILENAME).exists()


def test_fence_aware_old_runner_cannot_recreate_its_retired_database(tmp_path):
    Store.open(tmp_path / DATABASE_FILENAME).close()
    retired_path = tmp_path / "stats-history.sqlite3"

    with pytest.raises(SchemaTooNewError):
        Store._preflight_fence(
            retired_path,
            MIN_WRITER_PROTOCOL - 1,
            MIN_WRITER_BUILD - 1,
        )

    assert not retired_path.exists()


def test_current_store_accepts_legacy_revision_fence_only_as_migration_input(tmp_path):
    (tmp_path / WRITER_FENCE_FILENAME).write_text(json.dumps({
        "application_id": APPLICATION_ID,
        "schema_version": SCHEMA_VERSION - 1,
        "minimum_writer_protocol": MIN_WRITER_PROTOCOL - 1,
        "minimum_writer_build": "legacy-source-revision",
    }), encoding="utf-8")

    Store.open(tmp_path / DATABASE_FILENAME).close()

    fence = json.loads((tmp_path / WRITER_FENCE_FILENAME).read_text(encoding="utf-8"))
    assert fence["schema_version"] == SCHEMA_VERSION
    assert fence["database_filename"] == DATABASE_FILENAME


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("event_id", "event\ncontrol"),
        ("source_id", "source\x7fcontrol"),
        ("epoch_id", "epoch\tcontrol"),
        ("source_id", "x" * 193),
    ),
)
def test_invalid_current_observation_identity_is_rejected_without_a_write(
    tmp_path, field, value,
):
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        item = replace(_observation("cpu", "web", 10), **{field: value})
        with pytest.raises(StorageValidationError):
            store.append_observation(item)
        snapshot = store.read_snapshot()

    assert snapshot.schema.source_generation == 0
    assert snapshot.observations == ()


@pytest.mark.parametrize(
    "changes",
    ({"database_filename": "stats-history.sqlite3"}, {"application_id": APPLICATION_ID + 1}),
)
def test_current_fence_must_identify_the_exact_versioned_database(tmp_path, changes):
    fence = {
        "application_id": APPLICATION_ID,
        "database_filename": DATABASE_FILENAME,
        "schema_version": SCHEMA_VERSION,
        "minimum_writer_protocol": MIN_WRITER_PROTOCOL,
        "minimum_writer_build": MIN_WRITER_BUILD,
        **changes,
    }
    path = tmp_path / WRITER_FENCE_FILENAME
    path.write_text(json.dumps(fence), encoding="utf-8")
    before = _files(tmp_path / DATABASE_FILENAME)

    with pytest.raises(SchemaMismatchError, match="different database"):
        Store.open(tmp_path / DATABASE_FILENAME)

    assert _files(tmp_path / DATABASE_FILENAME) == before


def test_observation_and_usage_identity_are_deduplicated(tmp_path):
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        assert store.append_observation(_observation("cpu", "host", 10.0)) is True
        assert store.append_observation(_observation("cpu", "host", 10.0)) is False
        assert store.append_usage_atom(_usage("event-1", 11.0)) is True
        assert store.append_usage_atom(_usage("event-1", 11.0)) is False
        assert store.append_usage_atom(_usage("event-1", 12.0, direction="output")) is True
        snapshot = store.read_snapshot()
    assert len(snapshot.observations) == 1
    assert [atom.observed_at for atom in snapshot.usage_atoms] == [11.0, 12.0]


def test_observation_event_identity_allows_distinct_events_at_one_timestamp(tmp_path):
    first = Observation("event-1", "browser", "browser-a", 10.0, "epoch-1", 1, {"kind": "api"})
    second = Observation("event-2", "browser", "browser-a", 10.0, "epoch-1", 1, {"kind": "sse"})
    retried = first

    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        result = store.append_batch(observations=(first, second, retried))
        snapshot = store.read_snapshot()

    assert result.observations_accepted == 2
    assert result.observations_duplicate == 1
    assert [item.event_id for item in snapshot.observations] == ["event-1", "event-2"]


def test_reused_event_identity_with_different_data_fails_fast(tmp_path):
    first = Observation("event-1", "browser", "browser-a", 10.0, "epoch-1", 1, {"kind": "api"})
    conflict = Observation("event-1", "browser", "browser-a", 11.0, "epoch-1", 1, {"kind": "sse"})
    atom = _usage("usage-1", 10.0)
    atom_conflict = _usage("usage-1", 11.0)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_observation(first)
        store.append_usage_atom(atom)
        with pytest.raises(StorageValidationError, match="observation event identity conflicts"):
            store.append_observation(conflict)
        with pytest.raises(StorageValidationError, match="usage atom identity conflicts"):
            store.append_usage_atom(atom_conflict)


def test_usage_identity_conflict_is_typed_hashed_and_keeps_batch_atomic(tmp_path):
    first = _usage("usage-poison", 10.0)
    conflict = _usage("usage-poison", 11.0)
    clean = _usage("usage-clean", 12.0)

    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_usage_atom(first)
        with pytest.raises(storage_module.UsageAtomIdentityConflict) as caught:
            store.append_batch(usage_atoms=(clean, conflict))
        snapshot = store.read_snapshot()

    error = caught.value
    assert str(error) == "usage atom identity conflicts with stored data"
    assert error.event_id == "usage-poison"
    assert all(len(value) == 64 for value in (
        error.identity_hash,
        error.first_payload_hash,
        error.attempted_payload_hash,
    ))
    assert error.first_payload_hash != error.attempted_payload_hash
    assert not hasattr(error, "payload")
    assert [atom.event_id for atom in snapshot.usage_atoms] == ["usage-poison"]


def test_retried_usage_event_preserves_first_agent_attribution(tmp_path):
    first = _usage("usage-1", 10.0)
    moved = UsageAtom(
        first.event_id,
        first.direction,
        first.modality,
        first.cache_role,
        first.unit,
        first.observed_at,
        {**first.payload, "agent_id": "moved-window"},
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_usage_atom(first)
        result = store.append_batch(usage_atoms=(moved,))
        snapshot = store.read_snapshot()

    assert result.usage_atoms_accepted == 0
    assert result.usage_atoms_duplicate == 1
    assert result.usage_attribution_conflicts == 1
    assert len(snapshot.usage_atoms) == 1
    assert snapshot.usage_atoms[0].payload["agent_id"] == "test-agent"


def test_replayed_usage_safely_repairs_legacy_unknown_model_once(tmp_path):
    base = _usage("usage-unknown", 10.0)
    unknown = UsageAtom(
        base.event_id, base.direction, base.modality, base.cache_role, base.unit,
        base.observed_at, {**base.payload, "model": "unknown", "pricing_profile": "default"},
    )
    discovered = UsageAtom(
        base.event_id, base.direction, base.modality, base.cache_role, base.unit,
        base.observed_at, {
            **base.payload,
            "model": "gpt-recovered",
            "model_evidence": "scan_state.resumed_model",
            "agent_id": "moved-window",
            "pricing_profile": "subscription",
        },
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        first = store.append_batch(usage_atoms=(unknown,))
        repaired = store.append_batch(usage_atoms=(discovered,))
        duplicate = store.append_batch(usage_atoms=(discovered,))
        snapshot = store.read_snapshot()

    assert first.source_generation == 1
    assert repaired.source_generation == 2
    assert repaired.usage_atoms_accepted == 1
    assert repaired.accepted_original_timestamps == (10.0,)
    assert repaired.usage_attribution_conflicts == 1
    assert duplicate.usage_atoms_duplicate == 1
    assert duplicate.usage_attribution_conflicts == 1
    assert len(snapshot.usage_atoms) == 1
    assert snapshot.usage_atoms[0].payload["model"] == "gpt-recovered"
    assert snapshot.usage_atoms[0].payload["model_evidence"] == "scan_state.resumed_model"
    assert snapshot.usage_atoms[0].payload["agent_id"] == "test-agent"
    assert snapshot.usage_atoms[0].payload["pricing_profile"] == "default"


def test_replayed_usage_preserves_first_pricing_profile_as_history(tmp_path):
    base = _usage("usage-profile", 10.0)
    default = UsageAtom(
        base.event_id, base.direction, base.modality, base.cache_role, base.unit,
        base.observed_at, {**base.payload, "pricing_profile": "default"},
    )
    replayed = UsageAtom(
        base.event_id, base.direction, base.modality, base.cache_role, base.unit,
        base.observed_at, {**base.payload, "pricing_profile": "subscription"},
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_usage_atom(default)
        duplicate = store.append_batch(usage_atoms=(replayed,))
        snapshot = store.read_snapshot()

    assert duplicate.usage_atoms_accepted == 0
    assert duplicate.usage_atoms_duplicate == 1
    assert duplicate.usage_attribution_conflicts == 0
    assert snapshot.usage_atoms[0].payload["pricing_profile"] == "default"


def test_fork_history_tombstone_deletes_the_exact_model_attributed_atom(tmp_path):
    legacy = UsageAtom(
        "codex:child-thread:3", "input", "text", "none", "tokens", 99.5,
        {
            "quantity": 7,
            "provider": "openai",
            "model": "gpt-real",
            "agent_id": "yo8881|0|codex",
            "thread_id": "child-thread",
            "execution_source": "codex",
            "pricing_profile": "default",
            "telemetry_complete": True,
        },
    )
    tombstone = UsageAtomTombstone(
        legacy.event_id, legacy.direction, legacy.modality, legacy.cache_role,
        legacy.unit, legacy.observed_at, 7, "openai", "gpt-real", "child-thread",
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        first = store.append_batch(usage_atoms=(legacy,))
        removed = store.append_batch(usage_tombstones=(tombstone,))
        duplicate = store.append_batch(usage_tombstones=(tombstone,))
        snapshot = store.read_snapshot()

    assert first.source_generation == 1
    assert removed.source_generation == 2
    assert removed.usage_tombstones_accepted == 1
    assert removed.accepted_original_timestamps == (99.5,)
    assert duplicate.source_generation == 2
    assert duplicate.usage_tombstones_duplicate == 1
    assert snapshot.usage_atoms == ()


def test_fork_history_tombstone_rejects_changed_model_or_nonfork_usage(tmp_path):
    known = UsageAtom(
        "codex:child-thread:3", "input", "text", "none", "tokens", 99.5,
        {
            "quantity": 7,
            "provider": "openai",
            "model": "gpt-real",
            "agent_id": "yo8881|0|codex",
            "thread_id": "child-thread",
            "execution_source": "codex",
            "telemetry_complete": True,
        },
    )
    tombstone = UsageAtomTombstone(
        known.event_id, known.direction, known.modality, known.cache_role,
        known.unit, known.observed_at, 7, "openai", "different-model", "child-thread",
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_usage_atom(known)
        with pytest.raises(StorageValidationError, match="tombstone conflicts"):
            store.append_batch(usage_tombstones=(tombstone,))
        snapshot = store.read_snapshot()

    assert snapshot.usage_atoms == (known,)

    invalid = UsageAtomTombstone(
        "not-a-codex-fork", "input", "text", "none", "tokens", 99.5,
        7, "openai", "gpt-real", "child-thread",
    )
    with Store.open(tmp_path / "invalid" / DATABASE_FILENAME) as store:
        with pytest.raises(StorageValidationError, match="Codex fork history"):
            store.append_batch(usage_tombstones=(invalid,))


def test_fork_history_tombstone_batch_rolls_back_prior_deletes_on_conflict(tmp_path):
    def atom(sequence, model):
        return UsageAtom(
            f"codex:child-thread:{sequence}", "input", "text", "none", "tokens",
            99.5, {
                "quantity": sequence,
                "provider": "openai",
                "model": model,
                "agent_id": "yo8881|0|codex",
                "thread_id": "child-thread",
                "execution_source": "codex",
                "telemetry_complete": True,
            },
        )

    legacy, changed = atom(3, "gpt-real"), atom(4, "gpt-changed")
    tombstones = tuple(
        UsageAtomTombstone(
            item.event_id, item.direction, item.modality, item.cache_role,
            item.unit, item.observed_at, item.payload["quantity"], "openai",
            "gpt-real", "child-thread",
        )
        for item in (legacy, changed)
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(usage_atoms=(legacy, changed))
        with pytest.raises(StorageValidationError, match="tombstone conflicts"):
            store.append_batch(usage_tombstones=tombstones)
        snapshot = store.read_snapshot()

    assert {item.event_id for item in snapshot.usage_atoms} == {
        legacy.event_id, changed.event_id,
    }


def test_atomic_batch_advances_one_source_generation_only_for_new_facts(tmp_path):
    observation = _observation("cpu", "host", 10.0)
    coverage = CoverageEpoch("cpu", "host", "epoch-1", 10.0, None, 1.0, 1)
    atom = _usage("usage-1", 10.0)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        first = store.append_batch(
            observations=(observation,), coverage_epochs=(coverage,), usage_atoms=(atom,),
        )
        duplicate = store.append_batch(
            observations=(observation,), coverage_epochs=(coverage,), usage_atoms=(atom,),
        )
        snapshot = store.read_snapshot()

    assert first.source_generation == 1
    assert (first.observations_accepted, first.coverage_changed, first.usage_atoms_accepted) == (1, 1, 1)
    assert first.accepted_original_timestamps == (10.0, 10.0)
    assert duplicate.source_generation == 1
    assert (duplicate.observations_duplicate, duplicate.coverage_unchanged, duplicate.usage_atoms_duplicate) == (1, 1, 1)
    assert duplicate.accepted_original_timestamps == ()
    assert snapshot.schema.source_generation == 1


def test_explicit_unavailable_span_is_a_deduplicated_coverage_fact(tmp_path):
    span = UnavailableSpan(
        "agent_status", "legacy", "migration-1", 10.0, 20.0, 10.0,
        "legacy_aggregate_not_reconstructable", 1,
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        first = store.append_batch(unavailable_spans=(span, span))
        snapshot = store.read_snapshot()

    assert first.unavailable_spans_accepted == 1
    assert first.unavailable_spans_duplicate == 1
    assert snapshot.unavailable_spans == (span,)
    assert snapshot.schema.source_generation == 1


def test_unavailable_spans_cannot_overlap_coverage_or_each_other(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    coverage = CoverageEpoch("agent_status", "legacy", "covered", 10, 20, 10, 1)
    overlapping_coverage = UnavailableSpan(
        "agent_status", "legacy", "lost-covered", 15, 25, 10, "lost", 1,
    )
    first_gap = UnavailableSpan(
        "agent_status", "other", "lost-1", 10, 20, 10, "lost", 1,
    )
    overlapping_gap = UnavailableSpan(
        "agent_status", "other", "lost-2", 15, 25, 10, "lost", 1,
    )

    with Store.open(path) as store:
        store.append_batch(coverage_epochs=(coverage,))
        with pytest.raises(StorageValidationError, match="overlaps a coverage epoch"):
            store.append_batch(unavailable_spans=(overlapping_coverage,))
        store.append_batch(unavailable_spans=(first_gap,))
        with pytest.raises(StorageValidationError, match="unavailable spans overlap"):
            store.append_batch(unavailable_spans=(overlapping_gap,))
        snapshot = store.read_snapshot()

    assert snapshot.coverage_epochs == (coverage,)
    assert snapshot.unavailable_spans == (first_gap,)


def test_one_batch_cannot_insert_coverage_and_unavailable_for_the_same_time(tmp_path):
    coverage = CoverageEpoch("cpu", "web", "covered", 10, 20, 1, 1)
    unavailable = UnavailableSpan("cpu", "web", "lost", 15, 25, 1, "lost", 1)

    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        with pytest.raises(StorageValidationError, match="overlaps a coverage epoch"):
            store.append_batch(
                coverage_epochs=(coverage,), unavailable_spans=(unavailable,),
            )
        snapshot = store.read_snapshot()

    assert snapshot.coverage_epochs == ()
    assert snapshot.unavailable_spans == ()
    assert snapshot.schema.source_generation == 0


def test_build2_repairs_early_schema5_unavailable_rows_once_and_fences_build1(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE schema_meta SET minimum_writer_build = 1, source_generation = 7")
    connection.executemany(
        "INSERT INTO unavailable_spans VALUES(?,?,?,?,?,?,?,?)",
        (
            ("agent_status", "retired-unavailable:test", "first", 10, 20, 10, "lost", 1),
            ("agent_status", "retired-unavailable:test", "overlap", 15, 25, 10, "lost", 2),
            ("agent_status", "retired-unavailable:test", "later", 30, 40, 10, "lost", 3),
        ),
    )
    connection.commit()
    connection.close()
    fence_path = tmp_path / WRITER_FENCE_FILENAME
    fence = json.loads(fence_path.read_text(encoding="utf-8"))
    fence["minimum_writer_build"] = 1
    fence_path.write_text(json.dumps(fence), encoding="utf-8")

    with Store.open(path) as store:
        first = store.read_snapshot()
    with Store.open(path) as store:
        second = store.read_snapshot()

    assert first.schema.minimum_writer_build == MIN_WRITER_BUILD
    assert first.schema.source_generation == 8
    assert first.unavailable_spans == (
        UnavailableSpan(
            "agent_status", "retired-unavailable:test", "first", 10, 25, 10, "lost", 2,
        ),
        UnavailableSpan(
            "agent_status", "retired-unavailable:test", "later", 30, 40, 10, "lost", 3,
        ),
    )
    assert second == first
    before = _files(path)
    with pytest.raises(SchemaTooNewError):
        Store.open(path, writer_build=1)
    assert _files(path) == before


def test_build2_refuses_to_guess_when_old_unavailable_rows_overlap_exact_coverage(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE schema_meta SET minimum_writer_build = 1")
    connection.execute(
        "INSERT INTO coverage_epochs VALUES(?,?,?,?,?,?,?)",
        ("cpu", "web", "covered", 10, 20, 1, 1),
    )
    connection.execute(
        "INSERT INTO unavailable_spans VALUES(?,?,?,?,?,?,?,?)",
        ("cpu", "web", "lost", 15, 25, 1, "lost", 1),
    )
    connection.commit()
    connection.close()
    fence_path = tmp_path / WRITER_FENCE_FILENAME
    fence = json.loads(fence_path.read_text(encoding="utf-8"))
    fence["minimum_writer_build"] = 1
    fence_path.write_text(json.dumps(fence), encoding="utf-8")

    with pytest.raises(SchemaMismatchError, match="refusing lossy repair"):
        Store.open(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT minimum_writer_build, source_generation FROM schema_meta"
        ).fetchone() == (1, 0)
        assert connection.execute("SELECT COUNT(*) FROM unavailable_spans").fetchone()[0] == 1
    finally:
        connection.close()


def test_open_coverage_epoch_closes_once_without_rewriting_immutable_facts(tmp_path):
    open_epoch = CoverageEpoch("cpu", "host", "epoch-1", 10.0, None, 1.0, 1)
    closed_epoch = CoverageEpoch("cpu", "host", "epoch-1", 10.0, 20.0, 1.0, 2)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        assert store.append_coverage_epoch(open_epoch) is True
        assert store.append_coverage_epoch(closed_epoch) is True
        assert store.append_coverage_epoch(closed_epoch) is False
        with pytest.raises(StorageValidationError, match="cannot move backward"):
            store.append_coverage_epoch(open_epoch)
        snapshot = store.read_snapshot()

    assert snapshot.coverage_epochs == (closed_epoch,)
    assert snapshot.schema.source_generation == 2


def test_latest_coverage_epoch_uses_exact_source_owner_and_cadence_identity(tmp_path):
    first = CoverageEpoch("gpu", "gpu:0", "inline:42:gpu:first", 100.0, 110.0, 10.0, 42)
    latest = CoverageEpoch("gpu", "gpu:0", "inline:42:gpu:latest", 120.0, 130.0, 10.0, 42)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(coverage_epochs=(first, latest))

        assert store.latest_coverage_epoch("gpu", "gpu:0", 42, 10.0) == latest
        assert store.latest_coverage_epoch("gpu", "gpu:1", 42, 10.0) is None
        assert store.latest_coverage_epoch("gpu", "gpu:0", 43, 10.0) is None


def test_inline_coverage_source_ids_selects_exact_canonical_owner_roster(tmp_path):
    rows = (
        CoverageEpoch("gpu", "gpu:0", "inline:42:gpu:first", 100.0, 110.0, 10.0, 42),
        CoverageEpoch("gpu", "gpu:1", "inline:42:gpu:second", 100.0, 110.0, 10.0, 42),
        CoverageEpoch("gpu", "gpu:other-owner", "inline:43:gpu:third", 100.0, 110.0, 10.0, 43),
        CoverageEpoch("gpu", "gpu:scheduled", "scheduled:42:gpu:fourth", 100.0, 110.0, 10.0, 42),
        CoverageEpoch("cpu", "port:7443", "inline:42:cpu:fifth", 100.0, 101.0, 1.0, 42),
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(coverage_epochs=rows)
        sources = store.inline_coverage_source_ids("gpu", 42)

    assert sources == ("gpu:0", "gpu:1")


def test_migration_reconciliation_is_identity_deduplicated_and_visible_in_snapshot(tmp_path):
    reconciliation = MigrationReconciliation("legacy-all", 100.0, "sha256:abc", {"rows": 7})
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        assert store.record_migration_reconciliation(reconciliation) is True
        assert store.record_migration_reconciliation(reconciliation) is False
        snapshot = store.read_snapshot()
    assert snapshot.migration_reconciliation == (reconciliation,)


def test_families_keep_independent_observation_and_coverage_timestamps(tmp_path):
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_observation(_observation("cpu", "host", 101.0))
        store.append_observation(_observation("gpu", "host", 109.0))
        store.append_coverage_epoch(CoverageEpoch("cpu", "host", "cpu-e", 100.0, 102.0, 1.0, 1))
        store.append_coverage_epoch(CoverageEpoch("gpu", "host", "gpu-e", 100.0, 110.0, 10.0, 1))
        snapshot = store.read_snapshot()
    assert [(item.family, item.observed_at) for item in snapshot.observations] == [
        ("cpu", 101.0),
        ("gpu", 109.0),
    ]
    assert [(item.family, item.native_cadence_seconds) for item in snapshot.coverage_epochs] == [
        ("cpu", 1.0),
        ("gpu", 10.0),
    ]


def test_snapshot_reads_every_fact_in_one_explicit_transaction(tmp_path):
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_observation(_observation("cpu", "host", 10.0))
        store.append_coverage_epoch(CoverageEpoch("cpu", "host", "epoch-1", 9.0, None, 1.0, 1))
        store.append_usage_atom(_usage("event-1", 10.5))
        statements = []
        store._connection().set_trace_callback(statements.append)
        snapshot = store.read_snapshot()
    assert snapshot.schema.schema_version == SCHEMA_VERSION
    assert len(snapshot.observations) == len(snapshot.coverage_epochs) == len(snapshot.usage_atoms) == 1
    assert statements[0] == "BEGIN"
    assert statements[-1] == "COMMIT"


def test_read_window_bounds_history_and_preserves_overlap_predecessor_boundaries(tmp_path):
    window = (100.0, 200.0)
    coverage = (
        CoverageEpoch("cpu", "host", "too-old", 0.0, 50.0, 1.0, 7),
        CoverageEpoch("cpu", "host", "predecessor", 50.0, 100.0, 1.0, 7),
        CoverageEpoch("cpu", "host", "inside", 120.0, 180.0, 1.0, 7),
        CoverageEpoch("cpu", "open", "open", 90.0, None, 1.0, 7),
        CoverageEpoch("gpu", "ancient", "ancient-only", 0.0, 90.0, 10.0, 7),
        CoverageEpoch("gpu", "future", "future", 200.0, 220.0, 10.0, 7),
    )
    unavailable = (
        UnavailableSpan("agent", "old", "old", 0.0, 100.0, 10.0, "old", 7),
        UnavailableSpan("agent", "span", "span", 90.0, 110.0, 10.0, "span", 7),
        UnavailableSpan("agent", "inside", "inside", 100.0, 200.0, 10.0, "inside", 7),
        UnavailableSpan("agent", "future", "future", 200.0, 220.0, 10.0, "future", 7),
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        result = store.append_batch(
            observations=tuple(
                _observation("cpu", f"at-{observed_at}", observed_at)
                for observed_at in (99.0, 100.0, 199.0, 200.0, 250.0)
            ),
            usage_atoms=tuple(
                _usage(f"usage-{observed_at}", observed_at)
                for observed_at in (99.0, 100.0, 199.0, 200.0, 250.0)
            ),
            coverage_epochs=coverage,
            unavailable_spans=unavailable,
        )
        snapshot = store.read_snapshot(read_window=window)
        predecessor_plan = store._connection().execute(
            "EXPLAIN QUERY PLAN " + storage_module._COVERAGE_PREDECESSOR_SQL,
            ("cpu", "host", window[0]),
        ).fetchall()
    gaps = materializer_module._coverage_gaps(snapshot, *window)

    # Full reads use only the lower bound for point facts. A fact stamped at
    # observed_until, or slightly in the future by a skewed producer, keeps the
    # established behavior; materializer bucket ownership remains unchanged.
    assert [item.observed_at for item in snapshot.observations] == [100.0, 199.0, 200.0, 250.0]
    assert [item.observed_at for item in snapshot.usage_atoms] == [100.0, 199.0, 200.0, 250.0]
    assert [item.epoch_id for item in snapshot.coverage_epochs] == [
        "predecessor", "open", "inside",
    ]
    assert [item.epoch_id for item in snapshot.unavailable_spans] == ["span", "inside"]
    assert snapshot.schema.source_generation == result.source_generation
    predecessor_plan_text = " ".join(str(row[3]) for row in predecessor_plan)
    assert "SEARCH coverage_epochs USING INDEX coverage_epochs_end" in predecessor_plan_text
    assert "ended_at<" in predecessor_plan_text
    assert any(
        item.source_id == "host" and item.start == 100.0 and item.end == 120.0
        for item in gaps
    )
    assert all(item.source_id != "ancient" for item in gaps)


def test_dirty_snapshot_reads_only_coalesced_original_windows_but_all_coverage(tmp_path):
    reconciliation = MigrationReconciliation("migration", 1.0, "digest", {"ok": True})
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(
            observations=tuple(_observation("cpu", "host", value) for value in (10, 20, 300)),
            usage_atoms=tuple(_usage(f"usage-{value}", value) for value in (10, 20, 300)),
            coverage_epochs=(CoverageEpoch("cpu", "host", "epoch", 1, None, 1, 1),),
        )
        store.record_migration_reconciliation(reconciliation)
        snapshot = store.read_snapshot(dirty_intervals=((9, 11), (10, 21)))

    assert [item.observed_at for item in snapshot.observations] == [10, 20]
    assert [item.observed_at for item in snapshot.usage_atoms] == [10, 20]
    assert len(snapshot.coverage_epochs) == 1
    assert snapshot.migration_reconciliation == (reconciliation,)


def test_empty_dirty_snapshot_reads_no_originals_but_all_coverage_facts(tmp_path):
    unavailable = UnavailableSpan(
        "gpu", "host", "gpu-gap", 30, 40, 10, "source unavailable", 1,
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(
            observations=(_observation("cpu", "host", 10),),
            usage_atoms=(_usage("usage-10", 10),),
            coverage_epochs=(CoverageEpoch("cpu", "host", "epoch", 1, None, 1, 1),),
            unavailable_spans=(unavailable,),
        )
        snapshot = store.read_snapshot(dirty_intervals=())

    assert snapshot.observations == ()
    assert snapshot.usage_atoms == ()
    assert len(snapshot.coverage_epochs) == 1
    assert snapshot.unavailable_spans == (unavailable,)


def test_dirty_snapshot_excludes_browser_history_outside_the_dirty_interval(tmp_path):
    private = tuple(
        Observation(
            f"browser-{source}-{timestamp}",
            "browser",
            f"browser:{source}",
            timestamp,
            f"browser:{source}",
            1,
            {"kind": "api"},
        )
        for source in range(5)
        for timestamp in (10 + source, 200 + source)
    )
    cpu = _observation("cpu", "host", 100)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(observations=(cpu, *private))
        snapshot = store.read_snapshot(
            dirty_intervals=((99, 101),),
        )

    assert snapshot.observations == (cpu,)


def test_browser_observation_status_aggregates_an_error_storm_without_decoding_every_row(
    tmp_path, monkeypatch,
):
    observations = []
    provenances = (None, "controlled_probe", "confirmed_real")
    for signature_index in range(140):
        kind = "error" if signature_index % 2 == 0 else "unhandledrejection"
        provenance = provenances[signature_index % len(provenances)]
        signature = "jsf-tied" if signature_index >= 138 else f"jsf-{signature_index:08x}"
        observed_at = 13_900 if signature_index >= 138 else signature_index * 100
        for occurrence in range(10):
            payload = {
                "kind": kind,
                "signature": signature,
                "message": f"failure {signature_index}",
                "source": "/static/yomux.js",
            }
            if signature_index != 138:
                payload["code_revision"] = f"rev-{occurrence % 3}"
            if provenance is not None:
                payload["provenance"] = provenance
            observations.append(Observation(
                f"failure-{signature_index:03d}-{occurrence:02d}",
                "browser",
                "browser:test",
                float(observed_at + occurrence),
                "page-1",
                1,
                payload,
            ))

    decoded_payloads = 0
    decode_json_object = storage_module._decode_json_object

    def count_decoded_payloads(encoded, name):
        nonlocal decoded_payloads
        decoded_payloads += 1
        return decode_json_object(encoded, name)

    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        assert store.append_batch(observations=tuple(observations)).observations_accepted == 1_400
        monkeypatch.setattr(storage_module, "_decode_json_object", count_decoded_payloads)
        status = store.browser_observation_status(14_000.0)

    assert decoded_payloads <= 128
    assert status["retained_observations"] == 1_400
    assert status["retained_failures"] == 1_400
    assert status["retained_errors"] == status["retained_unhandled_rejections"] == 700
    assert status["unknown_failures"] == status["probe_failures"] == 470
    assert status["confirmed_real_failures"] == 460
    assert status["last_retained_observed_at"] == 13_909.0
    assert status["last_retained_observed_age_seconds"] == 91.0
    assert len(status["fingerprints"]) == 128
    assert status["classification_counts"] == {"open": 128, "fixed": 0, "live_verified": 0}
    assert [item["signature"] for item in status["fingerprints"]] == [
        "jsf-tied", "jsf-tied",
        *(f"jsf-{signature_index:08x}" for signature_index in range(137, 11, -1)),
    ]
    assert status["fingerprints"][0] == {
        "signature": "jsf-tied",
        "kind": "unhandledrejection",
        "provenance": "controlled_probe",
        "count": 10,
        "first_observed_at": 13_900.0,
        "last_observed_at": 13_909.0,
        "code_revisions": ("rev-0", "rev-1", "rev-2"),
        "state": "open",
        "state_reason": "no durable closure or path-execution evidence",
    }
    assert status["fingerprints"][1]["code_revisions"] == ()


def test_browser_observation_status_retains_typed_warning_fingerprints(tmp_path):
    warning = Observation(
        "stats-warning-1", "browser", "browser:test", 10.0, "page-1", 1,
        {
            "kind": "warning", "signature": "jsf-warning", "message": "stats capabilities fields are not exact",
            "source": "/api/stats-stream", "code_revision": "test-revision",
        },
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        assert store.append_batch(observations=(warning,)).observations_accepted == 1
        status = store.browser_observation_status(10.0)

    assert status["retained_failures"] == 1
    assert status["retained_errors"] == status["retained_unhandled_rejections"] == 0
    assert status["fingerprints"] == ({
        "signature": "jsf-warning", "kind": "warning", "provenance": "unknown", "count": 1,
        "first_observed_at": 10.0, "last_observed_at": 10.0,
        "code_revisions": ("test-revision",), "state": "open",
        "state_reason": "no durable closure or path-execution evidence",
    },)
def test_browser_observation_status_survives_an_unrelated_malformed_heartbeat(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        store.append_batch(observations=(
            Observation(
                "failure", "browser", "browser:test", 10.0, "page-1", 1,
                {"kind": "error", "signature": "jsf-real", "message": "boom", "source": "/"},
            ),
            Observation(
                "heartbeat", "browser", "browser:test", 11.0, "page-1", 1,
                {"kind": "heartbeat"},
            ),
        ))
        store._connection().execute(
            "UPDATE observations SET payload_json = ? WHERE event_id = ?",
            ('{"kind":"heartbeat"', "heartbeat"),
        )

    with Store.open(path) as store:
        status = store.browser_observation_status(12.0)

    assert status["retained_observations"] == 2
    assert status["retained_failures"] == 1
    assert status["fingerprints"][0]["signature"] == "jsf-real"


def test_browser_observation_status_has_a_constant_request_work_bound_under_high_volume(
    tmp_path,
):
    count = 20_000
    observations = tuple(
        Observation(
            f"failure-{index:05d}", "browser", "browser:test", float(index), "page-1", 1,
            ({
                "kind": "error",
                "signature": f"jsf-{index:08x}",
                "message": "boom",
                "source": "/",
                "code_revision": f"rev-{index % 3}",
            } if index % 2 == 0 else {
                "kind": "api", "endpoint": "/api/ping", "method": "GET",
            }),
        )
        for index in range(count)
    )
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        assert store.append_batch(observations=observations).observations_accepted == count

    with Store.open(path) as store:
        progress_calls = 0

        def count_progress():
            nonlocal progress_calls
            progress_calls += 1
            return 0

        connection = store._connection()
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 1
        connection.set_progress_handler(count_progress, 1_000)
        status_started = time.monotonic()
        try:
            status = store.browser_observation_status(float(count))
        finally:
            status_elapsed = time.monotonic() - status_started
            connection.set_progress_handler(None, 0)
        status_progress_calls = progress_calls
        progress_calls = 0
        connection.set_progress_handler(count_progress, 1_000)
        profiles_started = time.monotonic()
        try:
            profiles = store.recent_browser_profiles()
        finally:
            profiles_elapsed = time.monotonic() - profiles_started
            connection.set_progress_handler(None, 0)

    assert status["retained_failures"] == count // 2
    assert len(status["fingerprints"]) == 128
    assert len(profiles) == 128
    assert status_progress_calls <= 250
    assert progress_calls <= 250
    assert status_elapsed < 0.5
    assert profiles_elapsed < 0.5


def test_browser_diagnostics_queries_need_no_optional_sqlite_json_or_window_features():
    source = inspect.getsource(storage_module)
    for unsupported in ("json_extract(", "json_type(", "json_group_array(", "ROW_NUMBER()", "FILTER (WHERE"):
        assert unsupported not in source


def test_browser_diagnostics_indexes_follow_duplicates_reopen_and_prune(tmp_path):
    # Retention-relative so the retained window can grow without pushing the
    # oldest fixture row to a negative timestamp.
    now = RETENTION_SECONDS + 100_000.0
    cutoff = now - RETENTION_SECONDS
    path = tmp_path / DATABASE_FILENAME
    old_failure = Observation(
        "old-a", "browser", "browser:test", cutoff - 1, "page-1", 1,
        {
            "kind": "error", "signature": "jsf-a", "message": "old", "source": "/",
            "code_revision": "rev-old", "provenance": "controlled_probe",
        },
    )
    with Store.open(path) as store:
        store.append_batch(observations=(
            old_failure,
            Observation(
                "kept-a", "browser", "browser:test", cutoff, "page-1", 1,
                {
                    "kind": "error", "signature": "jsf-a", "message": "kept", "source": "/",
                    "code_revision": "rev-kept", "provenance": "controlled_probe",
                },
            ),
            Observation(
                "old-b", "browser", "browser:test", cutoff - 2, "page-1", 1,
                {
                    "kind": "unhandledrejection", "signature": "jsf-b", "message": "old",
                    "source": "/", "provenance": "confirmed_real",
                },
            ),
            Observation(
                "old-profile", "browser", "browser:test", cutoff - 3, "page-1", 1,
                {"kind": "api", "endpoint": "/api/old", "method": "GET"},
            ),
            Observation(
                "kept-profile", "browser", "browser:test", cutoff + 1, "page-1", 1,
                {"kind": "api", "endpoint": "/api/kept", "method": "GET"},
            ),
        ))
        assert store.append_observation(old_failure) is False

    with Store.open(path) as store:
        before = store.browser_observation_status(now)
        assert before["retained_observations"] == 5
        assert before["retained_failures"] == 3
        store.prune(now=now)
        after = store.browser_observation_status(now)
        profiles = store.recent_browser_profiles()

    assert after["retained_observations"] == 2
    assert after["retained_failures"] == after["retained_errors"] == 1
    assert after["retained_unhandled_rejections"] == 0
    assert after["probe_failures"] == 1
    assert after["confirmed_real_failures"] == after["unknown_failures"] == 0
    assert after["fingerprints"][0]["first_observed_at"] == cutoff
    assert after["fingerprints"][0]["code_revisions"] == ("rev-kept",)
    assert [item["endpoint"] for item in profiles] == ["/api/kept"]


def test_dirty_snapshot_chunks_widely_scattered_intervals_without_a_full_scan(tmp_path):
    selected = tuple(10 + (index * 300) for index in range(1_030))
    outside = 155
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(
            observations=tuple(
                _observation("cpu", "host", observed_at)
                for observed_at in (*selected, outside)
            ),
            usage_atoms=tuple(
                _usage(f"usage-{observed_at}", observed_at)
                for observed_at in (*selected, outside)
            ),
        )
        statements = []
        store._connection().set_trace_callback(statements.append)
        for count, expected_queries in ((32, 1), (33, 2), (1_030, 1)):
            statements.clear()
            snapshot = store.read_snapshot(
                dirty_intervals=tuple(
                    (observed_at, observed_at + 1)
                    for observed_at in selected[:count]
                ),
            )

            assert [item.observed_at for item in snapshot.observations] == list(selected[:count])
            assert [item.observed_at for item in snapshot.usage_atoms] == list(selected[:count])
            observation_reads = [
                statement for statement in statements
                if " FROM observations" in statement
            ]
            usage_reads = [
                statement for statement in statements
                if " FROM usage_atoms" in statement
            ]
            assert len(observation_reads) == len(usage_reads) == expected_queries
            assert all(
                " WHERE " in statement
                for statement in (*observation_reads, *usage_reads)
            )
            expected_predicates = count if count <= 64 else 1
            assert sum(
                statement.count("observed_at >=")
                for statement in observation_reads
            ) == expected_predicates
            assert sum(
                statement.count("observed_at >=")
                for statement in usage_reads
            ) == expected_predicates


def test_dirty_snapshot_rejects_an_invalid_interval(tmp_path):
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        with pytest.raises(StorageValidationError, match="end must follow"):
            store.read_snapshot(dirty_intervals=((10, 10),))


def test_reader_is_query_only_sees_later_commits_and_does_not_republish_fence(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    writer = Store.open(path)
    fence_path = tmp_path / WRITER_FENCE_FILENAME
    fence_before = (fence_path.read_bytes(), fence_path.stat().st_mtime_ns)
    reader = Store.open_reader(path)
    try:
        writer.append_observation(_observation("cpu", "host", 10.0))
        assert reader.read_snapshot().observations[0].observed_at == 10.0
        with pytest.raises(StatsCurrentError, match="reader cannot mutate"):
            reader.append_observation(_observation("cpu", "host", 11.0))
        assert (fence_path.read_bytes(), fence_path.stat().st_mtime_ns) == fence_before
    finally:
        reader.close()
        writer.close()


def test_pinned_snapshot_keeps_header_and_rows_on_one_wal_generation(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    writer = Store.open(path)
    reader = Store.open_reader(path)
    try:
        first = _observation("cpu", "host", 10.0)
        second = _observation("cpu", "host", 20.0)
        assert writer.append_observation(first) is True

        with reader.pinned_snapshot(dirty_intervals=((9, 11),)) as read:
            assert writer.append_observation(second) is True
            pinned = read()

        assert pinned.schema.source_generation == 1
        assert pinned.observations == (first,)
        current = reader.read_snapshot()
        assert current.schema.source_generation == 2
        assert current.observations == (first, second)
    finally:
        reader.close()
        writer.close()


def test_reader_cannot_create_a_database_or_bypass_writer_protocol(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with pytest.raises((SchemaMismatchError, sqlite3.Error)):
        Store.open_reader(path)
    assert not path.exists()
    assert not (tmp_path / WRITER_FENCE_FILENAME).exists()

    Store.open(path).close()
    before = _files(path)
    with pytest.raises(SchemaTooNewError):
        Store.open_reader(path, writer_protocol=MIN_WRITER_PROTOCOL - 1)
    assert _files(path) == before


def test_prune_retains_exactly_24_hours_and_clips_spanning_coverage(tmp_path):
    now = 200_000.0
    cutoff = now - RETENTION_SECONDS
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_observation(_observation("cpu", "old", cutoff - 0.1))
        store.append_observation(_observation("cpu", "boundary", cutoff))
        store.append_usage_atom(_usage("old", cutoff - 0.1))
        store.append_usage_atom(_usage("boundary", cutoff))
        store.append_coverage_epoch(CoverageEpoch("cpu", "old", "old", cutoff - 10.0, cutoff - 0.1, 1.0, 1))
        store.append_coverage_epoch(CoverageEpoch("cpu", "span", "span", cutoff - 10.0, cutoff + 10.0, 1.0, 1))
        store.append_coverage_epoch(CoverageEpoch("gpu", "live", "live", cutoff - 10.0, None, 10.0, 1))
        generation_before = store.read_snapshot().schema.source_generation
        result = store.prune(now=now)
        snapshot = store.read_snapshot()
    assert result.observations_deleted == result.usage_atoms_deleted == 1
    assert result.coverage_epochs_deleted == 1
    assert result.coverage_epochs_clipped == 2
    assert [item.source_id for item in snapshot.observations] == ["boundary"]
    assert [item.event_id for item in snapshot.usage_atoms] == ["boundary"]
    assert {item.started_at for item in snapshot.coverage_epochs} == {cutoff}
    assert result.source_generation == generation_before + 1
    assert snapshot.schema.source_generation == result.source_generation


def test_prune_uses_half_open_interval_boundaries_without_zero_length_rows(tmp_path):
    now = 200_000.0
    cutoff = now - RETENTION_SECONDS
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(
            observations=(
                _observation("cpu", "before", cutoff - 0.001),
                _observation("cpu", "boundary", cutoff),
            ),
            usage_atoms=(
                _usage("before", cutoff - 0.001),
                _usage("boundary", cutoff),
            ),
            coverage_epochs=(
                CoverageEpoch("cpu", "ends-at", "ends-at", cutoff - 10.0, cutoff, 1.0, 1),
                CoverageEpoch("cpu", "starts-at", "starts-at", cutoff, cutoff + 10.0, 1.0, 1),
                CoverageEpoch("cpu", "spans", "spans", cutoff - 10.0, cutoff + 10.0, 1.0, 1),
                CoverageEpoch("cpu", "open", "open", cutoff - 10.0, None, 1.0, 1),
            ),
            unavailable_spans=(
                UnavailableSpan("gpu", "ends-at", "ends-at", cutoff - 10.0, cutoff, 10.0, "test", 1),
                UnavailableSpan("gpu", "starts-at", "starts-at", cutoff, cutoff + 10.0, 10.0, "test", 1),
                UnavailableSpan("gpu", "spans", "spans", cutoff - 10.0, cutoff + 10.0, 10.0, "test", 1),
            ),
        )

        result = store.prune(now=now)
        snapshot = store.read_snapshot()

    assert result.observations_deleted == result.usage_atoms_deleted == 1
    assert result.coverage_epochs_deleted == 1
    assert result.coverage_epochs_clipped == 2
    assert result.unavailable_spans_deleted == 1
    assert result.unavailable_spans_clipped == 1
    assert [item.source_id for item in snapshot.observations] == ["boundary"]
    assert [item.event_id for item in snapshot.usage_atoms] == ["boundary"]
    assert {item.source_id for item in snapshot.coverage_epochs} == {"starts-at", "spans", "open"}
    assert {item.started_at for item in snapshot.coverage_epochs} == {cutoff}
    assert {item.source_id for item in snapshot.unavailable_spans} == {"starts-at", "spans"}
    assert {item.started_at for item in snapshot.unavailable_spans} == {cutoff}


def test_append_batch_prunes_expired_observations_in_the_same_transaction(tmp_path):
    now = 200_000.0
    cutoff = now - RETENTION_SECONDS
    prune_state = tmp_path / storage_module.PRUNE_STATE_FILENAME
    prune_state.write_text('{"last_pruned_at":123}\n', encoding="utf-8")
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(observations=(
            _observation("cpu", "expired", cutoff - 0.001),
            _observation("cpu", "boundary", cutoff),
        ))

        result = store.append_batch(
            observations=(_observation("cpu", "current", now),),
            retention_now=now,
        )
        snapshot = store.read_snapshot()

    assert [item.source_id for item in snapshot.observations] == ["boundary", "current"]
    assert result.retention_prune is not None
    assert result.retention_prune.observations_deleted == 1
    assert result.source_generation == snapshot.schema.source_generation == 2
    assert prune_state.read_text(encoding="utf-8") == '{"last_pruned_at":123}\n'


def test_append_batch_retention_fails_closed_before_mutation(tmp_path, monkeypatch):
    path = tmp_path / DATABASE_FILENAME
    now = 200_000.0
    with Store.open(path) as store:
        store.append_batch(observations=(_observation("cpu", "existing", now),))
        before = store.read_snapshot()
        monkeypatch.setattr(
            storage_module,
            "RETENTION_SECONDS",
            stats_resolution.MAX_RANGE_SECONDS - 1,
        )

        with pytest.raises(StatsCurrentError, match="refusing to prune"):
            store.append_batch(
                observations=(_observation("cpu", "rejected", now + 1),),
                retention_now=now + 1,
            )
        after = store.read_snapshot()

    assert after == before


def test_append_batch_rolls_back_when_retention_prune_fails(tmp_path, monkeypatch):
    path = tmp_path / DATABASE_FILENAME
    now = 200_000.0
    with Store.open(path) as store:
        before = store.read_snapshot()

        def fail_prune(connection, cutoff):
            connection.execute("DELETE FROM observations")
            raise RuntimeError("prune failed")

        monkeypatch.setattr(storage_module, "_prune_retained_facts", fail_prune)
        with pytest.raises(RuntimeError, match="prune failed"):
            store.append_batch(
                observations=(_observation("cpu", "rejected", now),),
                retention_now=now,
            )
        after = store.read_snapshot()

    assert after == before


def test_noop_prune_does_not_advance_source_generation(tmp_path):
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_observation(_observation("cpu", "host", RETENTION_SECONDS + 1))
        before = store.read_snapshot().schema.source_generation
        result = store.prune(now=RETENTION_SECONDS + 1)
        after = store.read_snapshot().schema.source_generation

    assert result.source_generation == before == after


@pytest.mark.parametrize(
    ("metadata_column", "minimum"),
    (("minimum_writer_protocol", MIN_WRITER_PROTOCOL + 1), ("minimum_writer_build", MIN_WRITER_BUILD + 1)),
)
def test_too_new_writer_metadata_is_rejected_without_mutation(tmp_path, metadata_column, minimum):
    path = tmp_path / DATABASE_FILENAME
    Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.execute(f"UPDATE schema_meta SET {metadata_column} = ?", (minimum,))
    connection.commit()
    connection.close()
    before = _files(path)
    with pytest.raises(SchemaTooNewError):
        Store.open(path)
    assert _files(path) == before


def test_future_schema_is_rejected_read_only_without_mutation(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()
    before = _files(path)
    with pytest.raises(SchemaTooNewError):
        Store.open(path)
    assert _files(path) == before


def test_future_fence_preserves_live_database_wal_shm_and_sqlite_metadata(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    future = sqlite3.connect(path)
    try:
        future.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        future.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        future.execute("PRAGMA journal_mode = WAL")
        future.execute("PRAGMA wal_autocheckpoint = 0")
        future.execute("CREATE TABLE future_only(value TEXT NOT NULL)")
        future.execute("INSERT INTO future_only(value) VALUES('preserve')")
        future.commit()
        (tmp_path / WRITER_FENCE_FILENAME).write_text(json.dumps({
            "application_id": APPLICATION_ID,
            "database_filename": f"stats-v{SCHEMA_VERSION + 1}.sqlite3",
            "schema_version": SCHEMA_VERSION + 1,
            "minimum_writer_protocol": MIN_WRITER_PROTOCOL + 1,
            "minimum_writer_build": MIN_WRITER_BUILD + 1,
        }), encoding="utf-8")
        before_metadata = _sqlite_metadata(future)
        before_files = _files(path)
        assert {f"{path.name}-wal", f"{path.name}-shm"} <= set(before_files)

        with pytest.raises(SchemaTooNewError):
            Store.open(path)

        assert _files(path) == before_files
        assert _sqlite_metadata(future) == before_metadata
        assert future.execute("SELECT value FROM future_only").fetchone()[0] == "preserve"
    finally:
        future.close()


def test_legacy_writer_stops_at_current_schema_without_mutation(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    Store.open(path).close()
    before = _files(path)
    with pytest.raises(SchemaTooNewError):
        storage_module.require_compatible_writer(
            path,
            writer_protocol=MIN_WRITER_PROTOCOL - 1,
            writer_build=MIN_WRITER_BUILD - 1,
        )
    assert _files(path) == before


def test_ring_replay_cursor_cardinality_is_validated_like_the_other_fixed_row_tables(tmp_path):
    """A lost cursor row must be refused, not silently replayed from the wrong fold point.

    `_validate_ring_schema` pins an exact row set for `aggregate_publication`, `aggregate_rings`
    and `aggregate_ring_slots`, but `ring_replay_cursor` was excluded and never queried. This is
    NOT an unbounded-growth defect -- one INSERT site at schema creation, no product DELETE site,
    so growth is structurally impossible. It is a DRIFT-DETECTION gap: a v8 file whose cursor
    rows an external tool removed would pass validation, and the fold point it then replays from
    is whatever the surviving rows say.
    """
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        store.initialize_ring_storage()
    expected = sorted(stats_resolution.RING_CAPACITIES)

    with sqlite3.connect(path) as raw:
        rows = [int(row[0]) for row in raw.execute(
            "SELECT resolution_seconds FROM ring_replay_cursor ORDER BY resolution_seconds")]
    assert rows == expected, "a fresh store starts with exactly one cursor row per ring"

    # An external tool drops one cursor row. Every other ring assertion still passes.
    with sqlite3.connect(path) as raw:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("DELETE FROM ring_replay_cursor WHERE resolution_seconds = ?", (expected[0],))
    with pytest.raises(SchemaMismatchError):
        Store.open(path).close()


def test_a_duplicate_ring_replay_cursor_row_is_structurally_impossible(tmp_path):
    """The other half of the finding, recorded rather than guarded: the PK already prevents it.

    `resolution_seconds` is the table's PRIMARY KEY, so a second row for the same ring cannot be
    inserted at all. The cardinality assertion above therefore covers the reachable direction --
    missing rows -- and this pins WHY the duplicate direction needs no separate guard, so a later
    reader does not add one and conclude the check was incomplete.
    """
    path = tmp_path / DATABASE_FILENAME
    with Store.open(path) as store:
        store.initialize_ring_storage()
    resolution = sorted(stats_resolution.RING_CAPACITIES)[0]
    with sqlite3.connect(path) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO ring_replay_cursor(resolution_seconds) VALUES(?)", (resolution,)
            )
# --- the bounded exact rebuild -------------------------------------------------
#
# `pinned_snapshot` decodes every observation into a tuple built from a `fetchall()` of every raw
# row, so both representations of the whole store are live at once. `pinned_snapshot_batches` is
# the bounded sibling. These tests pin the invariant it exists for, the four bounds it runs under,
# and that it reads the same facts in the same order as the method it replaces.


class _TrackedRow:
    """A raw row that can be weakly referenced, so a test can prove when it stops being reachable.

    Not a `tuple` subclass: variable-length built-ins cannot carry `__weakref__`, so the wrapper
    holds the values and forwards indexing, which is all the reader does with a row.
    """

    __slots__ = ("_values", "__weakref__")

    def __init__(self, values):
        self._values = values

    def __getitem__(self, index):
        return self._values[index]


def _seed_observations(store, count):
    store.append_batch(
        coverage_epochs=[CoverageEpoch("cpu", "probe", "epoch-1", 0.0, None, 1.0, 1)],
        observations=[_observation("cpu", "probe", float(index)) for index in range(count)],
    )


def _track_raw_rows(store, tracked):
    """Hand every raw SQLite row out as a weakly referenceable object and remember each one.

    `sqlite3.Connection.execute` is read-only, so the seam is `row_factory`: it is the one place a
    test can see the raw tuples the reader decodes from, without changing the reader.
    """

    connection = store._connection()
    tracked.clear()

    def remembering_row(_cursor, values):
        row = _TrackedRow(values)
        tracked.append(weakref.ref(row))
        return row

    connection = store._connection()
    connection.row_factory = remembering_row
    return connection


def test_a_batch_never_holds_raw_rows_and_decoded_facts_together(tmp_path):
    """The invariant, proved by reachability rather than by asserting a peak that passes today.

    A peak-memory assertion passes or fails on the chunk size of the day. This asserts the thing
    that actually bounds the rebuild: by the time a decoded batch reaches the caller, the raw
    SQLite tuples it was built from are unreachable, so the store's rows and the store's decoded
    facts are never both resident.
    """

    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 400)
        tracked: list = []
        _track_raw_rows(store, tracked)
        try:
            with store.pinned_snapshot_batches(max_rows=100) as read:
                for batch in read():
                    assert batch, "a yielded batch is never empty"
                    alive = [reference for reference in tracked if reference() is not None]
                    assert not alive, (
                        f"{len(alive)} raw rows were still reachable when a decoded batch was "
                        "yielded; rows and facts must never both be resident"
                    )
        finally:
            store._connection().row_factory = None
        assert len(tracked) >= 250, "the instrumentation must have seen every raw row"


def test_the_row_bound_ends_a_batch_rather_than_the_rebuild(tmp_path):
    """Row and byte bounds are batch-scoped: a full batch is a normal event, not a failure."""

    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 250)
        with store.pinned_snapshot_batches(max_rows=100) as read:
            sizes = [len(batch) for batch in read()]

    assert sizes == [100, 100, 50]
    assert sum(sizes) == 250


def test_the_decoded_byte_bound_also_ends_a_batch(tmp_path):
    """Whichever of the two batch bounds trips first ends the batch."""

    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 120)
        with store.pinned_snapshot_batches(max_rows=50, max_decoded_bytes=1) as read:
            sizes = [len(batch) for batch in read()]

    # A one-byte budget admits no SECOND fetch, so each batch ends at its first fetch boundary
    # rather than at the 50-row bound being reached twice. Both bounds are checked at fetch
    # boundaries, which is what keeps an over-budget batch from being decoded before anyone looks.
    assert sizes == [50, 50, 20]
    assert sum(sizes) == 120


def test_an_over_budget_rebuild_is_abandoned_with_a_reason_rather_than_truncated(tmp_path):
    """Memory is a REBUILD bound. Serving half a store as if it were whole is the worse failure.

    The reader is injected for the same reason the lifetime bound injects a clock. Growth is only
    visible when the allocator asks the kernel for pages, so in a warm process a rebuild can decode
    thousands of rows and move `RssAnon` not at all -- asserting on real growth would make this node
    pass or fail on allocator state. The readings below are a baseline and one measurement past it.

    Seeded past `_REBUILD_FETCH_ROWS` so the pre-fetch check is the one that fires here. A rebuild
    finishing in a single fetch is measured too, by the check that runs after the fetch --
    `test_a_batch_that_ends_on_the_byte_bound_still_measures_what_that_fetch_grew` is that path.
    """

    readings = iter((1_000_000, 1_000_000, 1_000_042))
    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, storage_module._REBUILD_FETCH_ROWS * 2)
        with store.pinned_snapshot_batches(max_memory_bytes=1, memory=lambda: next(readings)) as read:
            with pytest.raises(storage_module.RebuildBoundExceeded) as raised:
                list(read())

    assert raised.value.reason == "rebuild_memory_bytes"
    assert raised.value.limit == 1
    # The GROWTH, not the absolute reading: 1,000,042 against a 1,000,000 baseline.
    assert raised.value.measured == 42


def test_the_memory_bound_measures_the_rebuild_and_not_the_whole_process(tmp_path):
    """A bound on ABSOLUTE process memory is unusable, and the gate is what proved it.

    Measured on the live daemon: `RssAnon` 623,244 kB plus `VmSwap` 942,440 kB is 1,529 MiB against
    a 192 MiB budget, so an absolute check refuses every rebuild forever, from the first one after
    start. The same bound fired in an xdist worker sitting at 300 MiB while the fixture held 200
    observations, which is what surfaced it.

    So the quantity is the rebuild's own growth. A process that is already large may still rebuild;
    a rebuild that allocates past its budget still cannot.

    Injected rather than allocated. Reaching 1.6 GiB by really allocating would make this node pass
    or fail on interpreter and allocator state, which is the defect being fixed reproduced in the
    test. Every reading below is far past the 192 MiB budget in absolute terms while the growth
    between them stays under it, so an absolute check fails here and a growth check passes.
    """

    huge = storage_module.REBUILD_MAX_MEMORY_BYTES * 8
    readings = iter([huge + step for step in (0, 0, 4096, 8192, 8192, 8192, 8192, 8192)])
    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 200)
        with store.pinned_snapshot_batches(memory=lambda: next(readings)) as read:
            rows = sum(len(batch) for batch in read())

    assert rows == 200


def test_a_rebuild_that_runs_too_long_is_abandoned_with_a_reason(tmp_path):
    """Lifetime is the other rebuild bound, and it still applies where memory cannot be read.

    The memory readings are huge and FLAT on purpose. Under the retired absolute bound this node was
    pre-empted in the gate: it expected `rebuild_seconds` and got `rebuild_memory_bytes`, because a
    300 MiB worker tripped the memory check before the clock ever advanced. A growth bound cannot
    pre-empt it however large the process is, and that is what these readings pin.
    """

    ticks = iter((0.0, 0.0, 500.0, 500.0, 500.0))
    flat = storage_module.REBUILD_MAX_MEMORY_BYTES * 8
    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 200)
        with store.pinned_snapshot_batches(
            max_seconds=120.0, now=lambda: next(ticks), memory=lambda: flat,
        ) as read:
            with pytest.raises(storage_module.RebuildBoundExceeded) as raised:
                list(read())

    assert raised.value.reason == "rebuild_seconds"
    assert raised.value.limit == 120.0


def test_a_batch_that_ends_on_the_byte_bound_still_measures_what_that_fetch_grew(tmp_path):
    """No code path may hand the caller a batch whose fetch was never measured once.

    The memory bound is checked at the top of the inner loop. When a fetch pushes `decoded_bytes`
    past `max_decoded_bytes` the `while` condition is false, control goes straight to the yield,
    and that fetch's growth is never checked at all -- not "the first check sees zero", but a whole
    fetch and its decode loop unobserved. The row bound cannot reach this: 22,000 rows needs at
    least eleven fetches, so the byte exit is the path that matters.

    Nothing caps `payload_json`, so one fetch of 2,000 rows is unbounded above. That is why this is
    a hole rather than a documented limit.

    The readings sit flat across the baseline and the pre-fetch check so nothing fires early, then
    step past the budget. Before the post-fetch check existed, this yielded a batch and raised
    nothing.
    """

    readings = iter((1_000_000, 1_000_000, 1_000_512))
    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 200)
        with store.pinned_snapshot_batches(
            max_decoded_bytes=1, max_memory_bytes=256, memory=lambda: next(readings),
        ) as read:
            handed_over = []
            with pytest.raises(storage_module.RebuildBoundExceeded) as raised:
                for batch in read():
                    handed_over.append(batch)
                    break

    assert handed_over == [], "a batch reached the caller before its fetch was ever measured"
    assert raised.value.reason == "rebuild_memory_bytes"
    assert raised.value.measured == 512
    assert raised.value.limit == 256


def test_the_decoded_byte_bound_counts_utf8_bytes_and_not_characters(tmp_path):
    """`REBUILD_BATCH_MAX_DECODED_BYTES` is named and derived in bytes, so it must be counted in them.

    `len()` on a `str` counts code points. SQLite hands TEXT back as `str`, so counting characters
    against a byte budget admits a batch up to four times the size the budget was derived for.

    This store's own writer cannot produce the case: `_encode_json_object` calls `json.dumps` with
    the default `ensure_ascii=True`, so every payload it writes escapes to ASCII and the two counts
    agree. The rows below are widened by direct SQL for exactly that reason. The reader does not
    choose who wrote the database it opens, and a counter that is only correct for one writer is
    the kind of assumption that survives until it does not.

    The budget is set to exactly one fetch of byte-counted payloads. Counting bytes ends the batch
    at the first fetch boundary; counting characters carries it through a second.
    """

    wide = '{"v":"' + "\u00e9" * 100 + '"}'
    assert len(wide.encode("utf-8")) > len(wide), "the fixture must actually be non-ASCII"

    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, storage_module._REBUILD_FETCH_ROWS * 2)
        store._connection().execute("UPDATE observations SET payload_json = ?", (wide,))
        budget = storage_module._REBUILD_FETCH_ROWS * len(wide.encode("utf-8"))
        with store.pinned_snapshot_batches(max_decoded_bytes=budget) as read:
            sizes = [len(batch) for batch in read()]

    assert sizes[0] == storage_module._REBUILD_FETCH_ROWS, (
        "the batch ran past its byte budget because the payloads were counted as characters"
    )
    assert sum(sizes) == storage_module._REBUILD_FETCH_ROWS * 2


def test_the_batched_read_returns_the_same_facts_in_the_same_order_as_one_snapshot(tmp_path):
    """Equivalence with the method it replaces, across a keyset boundary rather than within one."""

    with storage_module.Store.open(tmp_path / DATABASE_FILENAME) as store:
        _seed_observations(store, 500)
        with store.pinned_snapshot() as read_whole:
            whole = read_whole().observations
        with store.pinned_snapshot_batches(max_rows=37) as read_batches:
            streamed = tuple(item for batch in read_batches() for item in batch)

    assert len(streamed) == len(whole) == 500
    assert streamed == whole
    assert len({item.event_id for item in streamed}) == 500, "keyset paging duplicated no row"


def test_the_memory_bound_reads_anonymous_plus_swap_and_never_resident_size():
    """`RssAnon + VmSwap`, because RSS and USS both FALL when the kernel pages a process out.

    Measured on the live daemon, `VmRSS` sat 61.4% below `VmHWM` while `RssAnon + VmSwap` was
    within 1.1% of it. A bound checked against RSS would admit a rebuild holding 1,583 MiB while
    reporting 618 MiB and healthy.
    """

    source = inspect.getsource(storage_module._rebuild_memory_bytes)
    # The docstring names RSS to say why it is wrong, so this reads the executable body only.
    body = source.split('"""')[2]

    assert "RssAnon:" in body
    assert "VmSwap:" in body
    assert "VmRSS" not in body
    assert "Rss:" not in body
    assert storage_module._rebuild_memory_bytes() > 0, "this Linux host reports both fields"

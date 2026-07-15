# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""DOIT.1 item 2a: the raw-observations schema and the corruption escape hatch.

Additive foundation only — the tables exist and are empty; the destructive
recovery/atomic-swap migration (item 2b) is separate. A malformed DB is
quarantined aside (never wiped) and a fresh one is created, instead of
crash-looping the writer.
"""

import sqlite3

from yolomux_lib.local_services.stats_store import StatsStore


def _tables(path):
    connection = sqlite3.connect(path)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def test_raw_tables_created_additively(tmp_path):
    db = tmp_path / "s.sqlite3"
    store = StatsStore(db)
    store.open()
    names = _tables(db)
    assert {"stats_raw_samples", "stats_usage_atoms"} <= names
    # The pre-existing tables are untouched.
    assert {"stats_buckets", "stats_coverage_intervals", "schema_meta"} <= names
    # Empty until the migration populates them.
    connection = store._connection()
    assert connection.execute("SELECT COUNT(*) FROM stats_raw_samples").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM stats_usage_atoms").fetchone()[0] == 0


def test_reopen_is_idempotent_and_preserves_data(tmp_path):
    db = tmp_path / "s.sqlite3"
    store = StatsStore(db)
    store.open()
    store._connection().execute(
        "INSERT INTO stats_raw_samples(family, source_id, sample_time, epoch_id, payload_json) VALUES(?,?,?,?,?)",
        ("cpu", "p1", 100.0, "e1", "{}"),
    )
    store._connection().commit()
    store.close()
    # Second open must not recreate/clobber; the row survives.
    store2 = StatsStore(db)
    store2.open()
    assert store2._connection().execute("SELECT COUNT(*) FROM stats_raw_samples").fetchone()[0] == 1


def test_corrupt_db_is_quarantined_not_wiped(tmp_path):
    db = tmp_path / "s.sqlite3"
    db.write_bytes(b"this is definitely not a sqlite database" * 64)
    store = StatsStore(db)
    store.open()  # must not raise
    # A fresh, usable DB now exists with the raw tables...
    assert {"stats_raw_samples", "stats_usage_atoms"} <= _tables(db)
    assert store._connection().execute("SELECT COUNT(*) FROM stats_raw_samples").fetchone()[0] == 0
    # ...and the original bytes are preserved in a .corrupt-* sidecar, not deleted.
    sidecars = list(tmp_path.glob("s.sqlite3.corrupt-*"))
    assert len(sidecars) == 1
    assert sidecars[0].read_bytes().startswith(b"this is definitely not a sqlite database")


def test_valid_db_is_not_quarantined(tmp_path):
    db = tmp_path / "s.sqlite3"
    StatsStore(db).open()  # create a valid DB
    before = list(tmp_path.glob("s.sqlite3.corrupt-*"))
    assert before == []
    # Reopen a valid DB -> no quarantine sidecar appears.
    store = StatsStore(db)
    assert store._quarantine_if_corrupt() is None
    assert list(tmp_path.glob("s.sqlite3.corrupt-*")) == []

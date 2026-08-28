#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Build a 2x-cardinality statsd store from a 1x copy, and report both provenances.

The scale question is whether steady serving memory grows linearly with retained
cardinality. Answering it needs two stores that differ in exactly one way, so this
mirrors every retained source-scoped row under a suffixed identity:

* `observations`, `coverage_epochs`, `unavailable_spans` gain a mirror per row with
  `source_id` and `epoch_id` suffixed, which doubles both the row count and the
  distinct `(family, source_id)` count.
* `usage_atoms` gain a mirror per row with `event_id` suffixed, so the usage corpus
  doubles alongside the observation corpus instead of staying at 1x and skewing the
  ratio the way a previous campaign's 0.26x usage fixture did.
* The derived ring tables (`aggregate_rings`, `aggregate_ring_slots`,
  `ring_invalidations`, `aggregate_publication`) are left alone. They are the
  daemon's own materialized output, not retained source rows; duplicating them
  would be asserting an answer rather than measuring one.

Mirroring source identity together with row count is deliberate. Doubling rows at
the SAME source and timestamp would aggregate into the same cells and could not
move serving memory at all, which would answer the question trivially and wrongly.

The suffix is a plain string because `source_id` carries no format constraint in
the schema (`TEXT NOT NULL`) and none in `families.py`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

SUFFIX = "#x2"

# (table, columns, which columns get the suffix)
MIRRORS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "observations",
        ("event_id", "family", "source_id", "observed_at", "epoch_id", "owner_generation", "payload_json"),
        ("event_id", "source_id", "epoch_id"),
    ),
    (
        "coverage_epochs",
        ("family", "source_id", "epoch_id", "started_at", "ended_at", "native_cadence_seconds", "owner_generation"),
        ("source_id", "epoch_id"),
    ),
    (
        "unavailable_spans",
        ("family", "source_id", "epoch_id", "started_at", "ended_at", "native_cadence_seconds", "reason", "owner_generation"),
        ("source_id", "epoch_id"),
    ),
    (
        "usage_atoms",
        ("event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json"),
        ("event_id",),
    ),
)


def census(path: Path) -> dict:
    """Everything needed to state this fixture's provenance without reopening it."""

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        report: dict = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "page_size": con.execute("PRAGMA page_size").fetchone()[0],
            "page_count": con.execute("PRAGMA page_count").fetchone()[0],
            "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0],
            "user_version": con.execute("PRAGMA user_version").fetchone()[0],
            "application_id": con.execute("PRAGMA application_id").fetchone()[0],
            "rows": {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables},
        }
        report["distinct_family_source"] = con.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT family, source_id FROM observations)"
        ).fetchone()[0]
        report["distinct_sources"] = con.execute("SELECT COUNT(DISTINCT source_id) FROM observations").fetchone()[0]
        report["observation_payload_bytes"] = con.execute("SELECT SUM(LENGTH(payload_json)) FROM observations").fetchone()[0]
        report["usage_payload_bytes"] = con.execute("SELECT SUM(LENGTH(payload_json)) FROM usage_atoms").fetchone()[0]
        report["observed_at_range"] = con.execute("SELECT MIN(observed_at), MAX(observed_at) FROM observations").fetchone()
    finally:
        con.close()
    wal = path.with_name(path.name + "-wal")
    report["wal_present"] = wal.exists()
    report["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
    return report


def double(source: Path, target: Path) -> dict:
    if target.exists():
        target.unlink()
    for suffix in ("-wal", "-shm"):
        stale = target.with_name(target.name + suffix)
        if stale.exists():
            stale.unlink()
    shutil.copy2(source, target)
    before = census(target)
    started = time.clock_gettime(time.CLOCK_MONOTONIC)
    con = sqlite3.connect(target)
    inserted = {}
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        for table, columns, suffixed in MIRRORS:
            projection = ", ".join(
                f'"{column}" || ?' if column in suffixed else f'"{column}"' for column in columns
            )
            statement = f'INSERT INTO "{table}" ({", ".join(columns)}) SELECT {projection} FROM "{table}"'
            cursor = con.execute(statement, tuple(SUFFIX for _ in suffixed))
            inserted[table] = cursor.rowcount
        con.commit()
        integrity = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    elapsed = time.clock_gettime(time.CLOCK_MONOTONIC) - started
    after = census(target)
    return {
        "source": census(source),
        "before": before,
        "after": after,
        "inserted": inserted,
        "integrity": integrity,
        "build_seconds": round(elapsed, 3),
        "ratios": {
            table: round(after["rows"][table] / before["rows"][table], 4) if before["rows"].get(table) else None
            for table in after["rows"]
        },
        "distinct_family_source_ratio": round(after["distinct_family_source"] / before["distinct_family_source"], 4),
        "observation_payload_ratio": round(after["observation_payload_bytes"] / before["observation_payload_bytes"], 4),
        "usage_payload_ratio": round(after["usage_payload_bytes"] / before["usage_payload_bytes"], 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("double", help="write a 2x-cardinality copy of a store")
    builder.add_argument("--source", required=True)
    builder.add_argument("--target", required=True)
    builder.add_argument("--out", default="")
    reporter = sub.add_parser("census", help="print one store's provenance")
    reporter.add_argument("--database", required=True)
    reporter.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.command == "double":
        report = double(Path(args.source).resolve(), Path(args.target).resolve())
    else:
        report = census(Path(args.database).resolve())
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure how statsd append volume responds to the two independent levers: MERGING
telemetry families into one transaction, and lengthening the COMMIT INTERVAL.

Why this exists
---------------
`queues/backlog/DOIT.p0.e3.statsd-resource-bounds.md` ranks "batch persistence across
telemetry families into one transaction every 5-10 seconds" as its top structural saving
and cites 79.8774% / 87.9971% append reductions as the decision evidence. Task
YOLO-V0717-E3-EVIDENCE-02 established that no retained harness ever ran that experiment:
`/tmp/statsd-audit-048b/measure3.py` calls `append_batch` once PER FAMILY inside its loop
and computes `commits = seconds * families`, so it varies cadence only. Those figures were
withdrawn. This harness runs the experiment that was missing.

The two levers, separated
-------------------------
Every previous attempt confounded them. This runs a full 2 x 3 grid:

    merge   in {per_family, merged}      per_family = one append_batch per family per flush
                                         merged     = one append_batch carrying every family
    interval in {1, 5, 10} seconds       how often the buffer is flushed to SQLite

Acquisition is ALWAYS one sample per family per second in every arm, so every arm retains
exactly the same facts. Only persistence batching changes. `equal_facts` in the output
proves it: identical observation counts and identical stored row counts across arms.

What is modelled
----------------
One writer, one buffer, one flush deadline. That mirrors the real structural boundary:
statsd is the sole SQLite mutator, and the two family append paths (`service.py:2077`
worker/`publisher` and `service.py:3579` listener/`self.writer`) already converge on
`storage.py:append_batch` under one `work_lock`. A real implementation adds one more
deadline to the existing `service.py:1665-1677` worker loop. No second writer, no second
scheduler, no process crossing. This harness deliberately does NOT model a two-process
design, because there is not one.

Store topology matters and is reported
--------------------------------------
The 23,407.4 B "fixed per-transaction overhead" is a property of page, index and ledger
topology, not a SQLite constant: an append writes facts, bumps
`schema_meta.source_generation`, and inserts `ring_invalidations` rows, and all three cost
more on a deep B-tree. `--topology fresh` starts from an empty store (what measure3.py
measured); `--topology copy --source PATH` starts from a byte copy of a realistically
sized store and anchors its synthetic ingest just after that store's newest observation,
so the ring-invalidation path is exercised the way live ingest exercises it.

Measurement
-----------
`write_bytes` and `syscw` deltas from `/proc/self/io`, bracketed by `os.sync()`, plus main
and WAL file sizes and per-table row counts. Per-commit bytes are reported alongside
totals, because totals alone are what let a composed baseline hide.

Arms are run serially and never concurrently: a resource baseline may be measuring this
host, and these arms write real bytes.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current import resolution as res

PACIFIC = ZoneInfo("America/Los_Angeles")

# The two real one-second families. `service_load` is sampled in the web process and reaches
# statsd over RPC; `cpu` is sampled inside statsd. Both land in the same `append_batch`.
REAL_FAMILIES = (
    ("cpu", "cpu-source", {"process_percent": 1.0, "system_percent": 2.0}),
    ("service_load", "load-source", {"running": True, "cpu_percent": 3.0, "rss_bytes": 4096.0}),
)
# measure3.py's shape: two SOURCES of the one `cpu` family, not two families. Retained so this
# harness can reproduce a figure the retained corpus already reproduces.
MEASURE3_FAMILIES = (
    ("cpu", "s0", {"process_percent": 1.0, "system_percent": 2.0}),
    ("cpu", "s1", {"process_percent": 1.0, "system_percent": 2.0}),
)

COUNTED_TABLES = (
    "observations", "coverage_epochs", "usage_atoms", "unavailable_spans",
    "ring_invalidations", "aggregate_ring_slots",
)


def io_counters():
    """Read this process's cumulative IO counters."""
    values = {}
    with open("/proc/self/io", encoding="utf-8") as handle:
        for line in handle:
            key, _, raw = line.partition(":")
            values[key.strip()] = int(raw.strip())
    return values


def now_stamps():
    return {
        "pt": datetime.now(PACIFIC).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "monotonic": time.monotonic(),
        "epoch": time.time(),
    }


def table_counts(database_path):
    """Count every table this experiment can touch, from an independent read-only handle."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        counts = {}
        for table in COUNTED_TABLES:
            try:
                counts[table] = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except sqlite3.OperationalError:
                counts[table] = None
        counts["page_count"] = int(connection.execute("PRAGMA page_count").fetchone()[0])
        counts["page_size"] = int(connection.execute("PRAGMA page_size").fetchone()[0])
        counts["freelist_count"] = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        counts["source_generation"] = int(
            connection.execute("SELECT source_generation FROM schema_meta WHERE singleton = 1").fetchone()[0]
        )
        return counts
    finally:
        connection.close()


def newest_observed_at(database_path):
    """Anchor synthetic ingest just after a copied store's newest fact, or None when empty."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT max(observed_at) FROM observations").fetchone()
        return None if row is None or row[0] is None else float(row[0])
    finally:
        connection.close()


def prepare_store(database_path, *, topology, source):
    """Materialize the arm's starting database and return its anchor timestamp."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(database_path + suffix)
        except FileNotFoundError:
            pass
    if topology == "fresh":
        return 1_750_000_000.0
    if topology != "copy":
        raise ValueError(f"unknown topology {topology!r}")
    if not source:
        raise ValueError("--topology copy requires --source")
    shutil.copyfile(source, database_path)
    # Carry a non-empty WAL because it holds committed frames the main file does not yet have.
    # Never carry `-shm`: it is shared-memory coordination state for the ORIGINAL set of
    # connections, SQLite rebuilds it on open, and a stale copy makes recovery ambiguous.
    if os.path.exists(source + "-wal") and os.path.getsize(source + "-wal") > 0:
        shutil.copyfile(source + "-wal", database_path + "-wal")
    newest = newest_observed_at(database_path)
    # Anchor one second past the copied store's newest fact so appends look like continuing
    # ingest and the ring-invalidation path resolves to real published slots.
    return 1_750_000_000.0 if newest is None else newest + 1.0


def published_generation(database_path):
    """Read the generation the ring was last published from, or 0 when never published."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT source_generation FROM aggregate_publication WHERE singleton = 1"
        ).fetchone()
        return 0 if row is None or row[0] is None else int(row[0])
    except sqlite3.OperationalError:
        return 0
    finally:
        connection.close()


def seed_ring(store, database_path, ring_head, *, source_generation):
    """Publish one full ring whose slots END at `ring_head`, as measure3.py does.

    `ring_head` decides whether appends land inside published slots. A ring published at the
    START of the append window has a head that never advances (no materializer runs here), so
    every append past the 1 s ring's 300-slot horizon intersects nothing and `_record_invalidations`
    writes nothing. Publishing at the END of the window keeps the whole window inside the ring, so
    the invalidation ledger is exercised the way live ingest exercises it. Both are measured,
    because the difference between them IS the ledger's share of per-commit cost.
    """
    writes = []
    for resolution_seconds, slots in res.RING_CAPACITIES.items():
        for index in range(slots):
            start = (int(ring_head) // resolution_seconds) * resolution_seconds - (slots - 1 - index) * resolution_seconds
            if start >= 0:
                writes.append(storage.RingBucketWrite(
                    resolution_seconds, start,
                    json.dumps({"series": {"system_cpu_percent": 1.0}}), True,
                ))
    store.publish_ring_buckets(
        buckets=tuple(writes), source_generation=source_generation, published_at=ring_head,
    )
    return len(writes)


def run_arm(*, label, database_path, topology, source, merged, interval, seconds, shape, tag, ring_cover=False):
    """Run one arm and return its complete measurement record.

    Acquisition is one sample per family per second regardless of `interval`; `interval`
    controls only how many seconds of samples share one transaction. `merged` controls
    whether all families ride in one `append_batch` or get one call each.
    """
    families = REAL_FAMILIES if shape == "real" else MEASURE3_FAMILIES
    anchor = prepare_store(database_path, topology=topology, source=source)
    started = now_stamps()

    store = storage.Store.open(
        database_path,
        writer_protocol=storage.MIN_WRITER_PROTOCOL,
        writer_build=storage.MIN_WRITER_BUILD,
    )
    # A copied store already carries a real publication at a real generation, so it needs no seed
    # unless we are deliberately moving the ring head to cover the append window. Republishing
    # must never move `source_generation` backward, so it reuses whichever generation is already
    # the higher of the store's and the publication's.
    if ring_cover:
        ring_head = anchor + seconds
        generation = max(published_generation(database_path), table_counts(database_path)["source_generation"])
        ring_writes = seed_ring(store, database_path, ring_head, source_generation=generation)
    elif topology == "fresh":
        ring_writes = seed_ring(store, database_path, anchor, source_generation=1)
    else:
        ring_writes = 0

    # Open each family's coverage epoch before measurement starts, exactly as measure3.py does,
    # so the measured window contains only steady-state ingest.
    for index, (family, source_id, payload) in enumerate(families):
        store.append_batch(
            observations=(storage.Observation(
                f"{tag}-seed-{index}", family, source_id, anchor, f"epoch{index}", 0, payload,
            ),),
            coverage_epochs=(storage.CoverageEpoch(family, source_id, f"epoch{index}", anchor, anchor, 1.0, 0),),
        )

    before_counts = table_counts(database_path)
    main_before = os.path.getsize(database_path)
    wal_before = os.path.getsize(database_path + "-wal") if os.path.exists(database_path + "-wal") else 0
    os.sync()
    io_before = io_counters()
    measure_started = now_stamps()

    commits = 0
    observations_offered = 0
    buffer_by_family = {index: [] for index in range(len(families))}
    latest_at = anchor

    def flush():
        nonlocal commits
        if not any(buffer_by_family.values()):
            return
        if merged:
            observations = tuple(
                item for index in sorted(buffer_by_family) for item in buffer_by_family[index]
            )
            coverage = tuple(
                storage.CoverageEpoch(families[index][0], families[index][1], f"epoch{index}", anchor, latest_at, 1.0, 0)
                for index in sorted(buffer_by_family) if buffer_by_family[index]
            )
            store.append_batch(observations=observations, coverage_epochs=coverage)
            commits += 1
        else:
            for index in sorted(buffer_by_family):
                if not buffer_by_family[index]:
                    continue
                family, source_id, _payload = families[index]
                store.append_batch(
                    observations=tuple(buffer_by_family[index]),
                    coverage_epochs=(storage.CoverageEpoch(
                        family, source_id, f"epoch{index}", anchor, latest_at, 1.0, 0,
                    ),),
                )
                commits += 1
        for index in buffer_by_family:
            buffer_by_family[index] = []

    for tick in range(seconds):
        latest_at = anchor + tick + 1
        for index, (family, source_id, payload) in enumerate(families):
            sample = dict(payload)
            if "process_percent" in sample:
                sample["process_percent"] = 1.0 + tick % 7
            if "cpu_percent" in sample:
                sample["cpu_percent"] = 1.0 + tick % 7
            buffer_by_family[index].append(storage.Observation(
                f"{tag}-o{index}-{tick}", family, source_id, latest_at, f"epoch{index}", 0, sample,
            ))
            observations_offered += 1
        if (tick + 1) % interval == 0:
            flush()
    flush()

    wal_after = os.path.getsize(database_path + "-wal") if os.path.exists(database_path + "-wal") else 0
    os.sync()
    io_after = io_counters()
    measure_finished = now_stamps()
    main_after = os.path.getsize(database_path)
    store.close()
    after_counts = table_counts(database_path)
    finished = now_stamps()

    write_bytes = io_after["write_bytes"] - io_before["write_bytes"]
    syscw = io_after["syscw"] - io_before["syscw"]
    wchar = io_after["wchar"] - io_before["wchar"]
    syscr = io_after["syscr"] - io_before["syscr"]
    read_bytes = io_after["read_bytes"] - io_before["read_bytes"]
    observations_stored = after_counts["observations"] - before_counts["observations"]

    return {
        "label": label,
        "shape": shape,
        "topology": topology,
        "source": source,
        "merged": merged,
        "interval_seconds": interval,
        "acquisition_seconds": seconds,
        "family_count": len(families),
        "families": [f"{family}/{source_id}" for family, source_id, _p in families],
        "anchor_observed_at": anchor,
        "ring_buckets_seeded": ring_writes,
        "ring_cover": ring_cover,
        "commits": commits,
        "observations_offered": observations_offered,
        "observations_stored": observations_stored,
        "facts_per_commit": round(observations_offered / commits, 4) if commits else None,
        "write_bytes": write_bytes,
        "write_bytes_per_commit": round(write_bytes / commits, 1) if commits else None,
        "write_bytes_per_observation": round(write_bytes / observations_offered, 1) if observations_offered else None,
        "syscw": syscw,
        "syscw_per_commit": round(syscw / commits, 2) if commits else None,
        "wchar": wchar,
        "syscr": syscr,
        "read_bytes": read_bytes,
        "main_bytes_before": main_before,
        "main_bytes_after": main_after,
        "wal_bytes_before": wal_before,
        "wal_bytes_after": wal_after,
        "counts_before": before_counts,
        "counts_after": after_counts,
        "row_delta": {
            table: (after_counts[table] - before_counts[table])
            for table in COUNTED_TABLES
            if before_counts.get(table) is not None and after_counts.get(table) is not None
        },
        "started": started,
        "measure_started": measure_started,
        "measure_finished": measure_finished,
        "finished": finished,
        "measure_wall_seconds": round(measure_finished["monotonic"] - measure_started["monotonic"], 6),
        "arm_wall_seconds": round(finished["monotonic"] - started["monotonic"], 6),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="scratch database path for this arm")
    parser.add_argument("--out", required=True, help="path to write this arm's JSON record")
    parser.add_argument("--label", required=True)
    parser.add_argument("--tag", required=True, help="unique event-id prefix so appends never dedup")
    parser.add_argument("--topology", choices=("fresh", "copy"), required=True)
    parser.add_argument("--source", default="", help="source database to copy for --topology copy")
    parser.add_argument("--shape", choices=("real", "measure3"), default="real")
    parser.add_argument("--merged", action="store_true", help="one append_batch carrying every family")
    parser.add_argument("--ring-cover", action="store_true", help="publish a ring whose head covers the whole append window, so ring_invalidations is exercised")
    parser.add_argument("--interval", type=int, required=True, help="flush interval in seconds")
    parser.add_argument("--seconds", type=int, required=True, help="acquisition seconds; one sample per family per second")
    arguments = parser.parse_args()

    record = run_arm(
        label=arguments.label,
        database_path=arguments.db,
        topology=arguments.topology,
        source=arguments.source or None,
        merged=arguments.merged,
        interval=arguments.interval,
        seconds=arguments.seconds,
        shape=arguments.shape,
        tag=arguments.tag,
        ring_cover=arguments.ring_cover,
    )
    with open(arguments.out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"{record['label']:38s} commits={record['commits']:6d} "
        f"obs={record['observations_offered']:6d} stored={record['observations_stored']:6d} "
        f"write={record['write_bytes'] / 1e6:9.3f}MB per_commit={record['write_bytes_per_commit']:9.1f}B "
        f"syscw={record['syscw']:7d} wall={record['measure_wall_seconds']:7.2f}s"
    )


if __name__ == "__main__":
    main()

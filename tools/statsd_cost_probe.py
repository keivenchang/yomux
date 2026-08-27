#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Measure the physical cost of recording a fixed number of facts, in a fresh process.

Three numbers, taken together, because separately each one is gameable: bytes
actually written to block devices per fact, read syscalls per fact, and peak
memory. A change that halves writes by holding everything in RAM should fail, and
a change that caps memory by writing more often should also fail.

Why a fresh process rather than an in-test measurement. `VmHWM` is a
process-lifetime high-water mark that cannot be reset, so reading it inside a
pytest worker that has already run other tests reports those tests, not this
workload. `/proc/self/io` has the same problem for absolute values, though its
deltas are usable. Running the workload in a process that exists only to run it
makes all three numbers attributable without subtracting a baseline anyone has to
trust.

**Store size is an input, not an assumption.** The same code path runs against a
few-hundred-row `tmp_path` store in the default lane and against a copy of a
production-sized store when one is available; only `--database` and `--facts`
change. A small store cannot demonstrate the whole-history incident class -- it
has no history to be whole -- so a red on a small store proves the gate FIRES,
never that a ceiling is correctly placed.

Modes:

* `append` -- open the store and record `--facts` observations in batches of
  `--batch-size`, in the measured production family mix. This is the path the
  ceilings govern.
* `coldstart` -- open the store and materialize as the released daemon does at
  startup, reading the whole retained history. This is the NEGATIVE CONTROL: it
  is expected to exceed the peak-memory ceiling, and a gate that passes it is not
  measuring the incident class.

Post-freeze invocation against a realistic store is one command; see
`--help` and the module docstring of the gate that consumes this.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.stats_current import materializer  # noqa: E402
from yolomux_lib.stats_current import resolution as stats_resolution  # noqa: E402
from yolomux_lib.stats_current import storage  # noqa: E402

# Measured production mix, 26,687 observations/hour across six families:
# service_load 80.69%, cpu 13.29%, browser 4.22%, remainder split across the rest.
# Weights are integers so a run of N facts is exactly reproducible for any N.
# Fixed so the workload is identical on every run; 1787000100 is divisible by 300.
PROBE_EPOCH = 1_787_000_100.0

FAMILY_MIX: tuple[tuple[str, str, int], ...] = (
    ("service_load", "statsd", 8069),
    ("cpu", "port:7771", 1329),
    ("browser", "stats-costprobe", 422),
    ("gpu", "gpu:0", 90),
    ("system_memory", "host", 60),
    ("agent_status", "port:7771", 30),
)
MIX_TOTAL = sum(weight for _family, _source, weight in FAMILY_MIX)


def _self_io() -> dict[str, int]:
    """This process's own I/O counters. `write_bytes` is block-device bytes, not page cache."""

    out: dict[str, int] = {}
    for line in Path("/proc/self/io").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in {"rchar", "wchar", "syscr", "syscw", "read_bytes", "write_bytes", "cancelled_write_bytes"}:
            out[key] = int(rest.strip())
    return out


def _self_memory() -> dict[str, int]:
    """Peak and current memory for this process, in kB, including PSS and USS."""

    out: dict[str, int] = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in {"VmRSS", "VmHWM", "VmPeak", "RssAnon", "RssFile", "VmSwap"}:
            out[key] = int(rest.split()[0])
    try:
        rollup = Path("/proc/self/smaps_rollup").read_text()
    except (FileNotFoundError, PermissionError):
        return out
    for line in rollup.splitlines():
        key, _, rest = line.partition(":")
        if key in {"Pss", "Private_Clean", "Private_Dirty"}:
            out[key] = int(rest.split()[0])
    if "Private_Clean" in out and "Private_Dirty" in out:
        out["Uss"] = out["Private_Clean"] + out["Private_Dirty"]
    return out


def _observation(index: int, now: float) -> storage.Observation:
    """One fact, drawn from the measured family mix by a deterministic weighted cycle."""

    position = index % MIX_TOTAL
    for family, source_id, weight in FAMILY_MIX:
        if position < weight:
            break
        position -= weight
    return storage.Observation(
        event_id=f"costprobe-{index}",
        family=family,
        source_id=source_id,
        observed_at=now + index,
        epoch_id=f"costprobe-{family}",
        owner_generation=1,
        payload={"value": float(index), "probe": "cost", "index": index},
    )


def _coverage(now: float) -> tuple[storage.CoverageEpoch, ...]:
    return tuple(
        storage.CoverageEpoch(
            family=family,
            source_id=source_id,
            epoch_id=f"costprobe-{family}",
            started_at=now,
            ended_at=None,
            native_cadence_seconds=1.0,
            owner_generation=1,
        )
        for family, source_id, _weight in FAMILY_MIX
    )


def _publish_ring_head(store: storage.Store, now: float, facts: int) -> dict:
    """Give the store a published ring before the write phase, as a running daemon has.

    Without this the appends intersect no published slot, write zero `ring_invalidations`
    rows, and the measurement describes a workload that does not exist. Measured on this
    fixture: 5,068.8 B/fact with no head against 6,676.5 with one, so the head-less number
    understates real append cost and does so in the LENIENT direction.

    The ring is bounded per resolution (`RING_CAPACITIES`), so a span longer than a
    resolution's window cannot be fully covered; the achieved coverage is REPORTED rather
    than assumed, because a head that covers a tenth of the writes is not a head.
    """

    buckets: list[storage.RingBucketWrite] = []
    coverage: dict[str, dict] = {}
    for resolution in stats_resolution.RESOLUTION_CHOICES:
        capacity = stats_resolution.RING_CAPACITIES[resolution]
        starts = sorted({
            int(math.floor((now + offset) / resolution) * resolution)
            for offset in range(facts)
        })
        # A live ring holds the MOST RECENT slots, so drop the oldest when the span
        # exceeds capacity rather than silently overwriting through the slot index.
        retained = starts[-capacity:]
        buckets.extend(
            storage.RingBucketWrite(
                resolution_seconds=resolution,
                bucket_start=start,
                bucket_json=json.dumps({"start": start, "resolution": resolution, "series": []}),
                complete=False,
            )
            for start in retained
        )
        covered = len(retained) * resolution
        coverage[str(resolution)] = {
            "slots": len(retained),
            "capacity": capacity,
            "seconds_covered": covered,
            "span_fraction": round(min(1.0, covered / facts), 4) if facts else None,
        }
    store.publish_ring_buckets(buckets=buckets, source_generation=1, published_at=now)
    return {"published_slots": len(buckets), "per_resolution": coverage}


def _append_workload(database: Path, facts: int, batch_size: int) -> dict:
    # A FIXED epoch, not the clock. Bucket starts are floor(observed_at / resolution), so
    # where `now` sits inside its 300-second bucket decides how many distinct ring slots the
    # writes invalidate. Measured: with time.time() the per-fact arm produced five distinct
    # write_bytes values and 521-523 invalidations; pinning only to a whole second did not
    # fix it and made the batched arm vary too, because the alignment still moved between
    # runs. The gate's resolution claim requires identical work to cost identical bytes, so
    # the clock must not enter the workload at all. Aligned to 300, the largest resolution,
    # so every layer's bucket boundary falls the same way on every run.
    now = PROBE_EPOCH
    store = storage.Store.open(database)
    try:
        store.append_batch(coverage_epochs=_coverage(now))
        ring_head = _publish_ring_head(store, now, facts)
        before_io, before_memory = _self_io(), _self_memory()
        started = time.perf_counter()
        batches = accepted = 0
        for start in range(0, facts, batch_size):
            batch = [_observation(index, now) for index in range(start, min(start + batch_size, facts))]
            result = store.append_batch(observations=batch)
            accepted += result.observations_accepted
            batches += 1
        elapsed = time.perf_counter() - started
        ring_invalidations = store._connection().execute(
            "SELECT COUNT(*) FROM ring_invalidations"
        ).fetchone()[0]
    finally:
        store.close()
    after_io, after_memory = _self_io(), _self_memory()
    return {
        "mode": "append",
        "facts": facts,
        "batch_size": batch_size,
        "ring_head": ring_head,
        "ring_invalidations": ring_invalidations,
        "batches": batches,
        "observations_accepted": accepted,
        "seconds": round(elapsed, 4),
        "io_delta": {key: after_io[key] - value for key, value in before_io.items()},
        "memory_before_kb": before_memory,
        "memory_after_kb": after_memory,
    }


def _coldstart_workload(database: Path) -> dict:
    """The released whole-history path: materialize every retained row at once.

    This is the negative control. `build_generation` is what the daemon's first
    build calls, and on a production-sized store it is the step measured at
    1,470 MiB process USS. It is expected to BREACH the peak ceiling.
    """

    before_io, before_memory = _self_io(), _self_memory()
    started = time.perf_counter()
    reader = storage.Store.open_reader(database)
    try:
        generation = materializer.build_generation(reader)
        cells = len(getattr(generation, "cells", ()) or ())
    finally:
        reader.close()
    elapsed = time.perf_counter() - started
    after_io, after_memory = _self_io(), _self_memory()
    del generation
    return {
        "mode": "coldstart",
        "cells": cells,
        "seconds": round(elapsed, 4),
        "io_delta": {key: after_io[key] - value for key, value in before_io.items()},
        "memory_before_kb": before_memory,
        "memory_after_kb": after_memory,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="store to record into; any size")
    parser.add_argument("--facts", type=int, default=2000, help="how many observations to record")
    parser.add_argument("--batch-size", type=int, default=1, help="observations per append_batch call")
    parser.add_argument("--mode", choices=("append", "coldstart"), default="append")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    database = Path(args.database)
    if args.mode == "append":
        report = _append_workload(database, args.facts, max(1, args.batch_size))
    else:
        report = _coldstart_workload(database)

    io_delta = report["io_delta"]
    facts = report.get("facts") or 0
    report["database_bytes"] = database.stat().st_size if database.exists() else 0
    report["per_fact"] = {
        "write_bytes": round(io_delta["write_bytes"] / facts, 3) if facts else None,
        "wchar": round(io_delta["wchar"] / facts, 3) if facts else None,
        "syscr": round(io_delta["syscr"] / facts, 3) if facts else None,
        "syscw": round(io_delta["syscw"] / facts, 3) if facts else None,
    }
    memory = report["memory_after_kb"]
    report["peak_kb"] = {
        "vmhwm": memory.get("VmHWM", 0),
        "pss": memory.get("Pss", 0),
        "uss": memory.get("Uss", 0),
        "rss_anon": memory.get("RssAnon", 0),
        "swap": memory.get("VmSwap", 0),
        # The quantity a memory ceiling must actually govern. RSS and USS both drop
        # when the kernel pages a process out, so a daemon can breach its budget and
        # read healthy: live statsd was measured at VmRSS 630,200 kB while holding
        # VmSwap 1,015,336 kB. RssAnon + VmSwap reconciles VmHWM to 0.46% where RSS
        # does not, and it cannot be dodged by swapping.
        "anon_plus_swap": memory.get("RssAnon", 0) + memory.get("VmSwap", 0),
    }
    report["pid"] = os.getpid()
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

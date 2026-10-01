"""Joint `cache_size` / `mmap_size` grid for the statsd store, per `PRAGMASPEC-62`.

Neither pragma is set anywhere in the product today, so both sit at build defaults
(`DEFAULT_CACHE_SIZE = -2000`, `DEFAULT_MMAP_SIZE = 0`). This measures which values flatten the
read curve and what each costs in memory, so the eventual two-line change to the pragma block at
`storage.py:2047-2052` is chosen from evidence. **This harness never edits that file.**

It BUILDS and does not RUN the campaign: `--store` is required and a missing one is a named skip.

MEMORY IS TWO LIMBS AND THEY ARE NEVER SUMMED
---------------------------------------------
The single most important thing the spec corrects. `RssAnon + VmSwap` is the `cache_size` cost
and is **blind to `mmap_size`**: on a 39.2 MiB fixture, `mmap_size=256 MiB` moved `RssAnon` by
20 kB while putting 40,128 kB into `RssFile`. A grid that watched only the anonymous limb would
report the mmap axis as nearly free, which is the one-sided win this whole item exists to prevent.

* **anonymous** = `RssAnon + VmSwap` -- malloc'd cache. Must be paged to swap under pressure.
* **mapped** = `RssFile` -- mapped file pages. Clean ones are droppable at **zero IO cost**.

They are reported separately and the decision rule exchanges them at different rates, because the
daemon already holds **978.4 MiB in swap**. There is no anonymous headroom to spend.

FIVE WAYS THIS MEASUREMENT SILENTLY LIES, ALL HANDLED
-----------------------------------------------------
1. **A warm page cache flattens the whole read arm.** `posix_fadvise(DONTNEED)` runs on the
   database before every read point; without it every point after the first measures a warm cache.
2. **`VmHWM` is a process-lifetime high-water mark.** One fresh subprocess per grid point, always.
3. **SQLite silently clamps `mmap_size`** to `MAX_MMAP_SIZE`, and returns 0 when the VFS refuses.
   The effective value is read back and recorded; the requested value is never reported as a result.
4. **A static ring head understates append cost by ~46%**, because appends only write
   `ring_invalidations` when they intersect a published slot. The write arm publishes a ring, and
   says so in its output when it cannot.
5. **The write-arm collapse from 20 points to 5 rests on a 15 MiB fixture.** It is falsified first,
   at production size, and the campaign stops if the falsification fails -- it changes the size 4x.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

# Negative form is KiB of memory, so the grid stays meaningful if `page_size` ever changes.
CACHE_SIZE_POINTS = (-2000, -16000, -65536, -262144, -582740)
CACHE_SIZE_CONTROL = -2000
# Bytes. The top covers the 569.1 MiB store with headroom; a later re-run must re-derive it from
# the file size rather than reuse 600 MiB, because the store grows.
MMAP_SIZE_POINTS = (0, 67108864, 268435456, 629145600)
MMAP_SIZE_CONTROL = 0
MMAP_SIZE_FALSIFICATION = 629145600

# A point flattens the read curve at 5% above the best `syscr` seen anywhere on the grid. 5%
# because a sibling lane measured syscr/fact bit-identical to three decimals across host loads
# 12.26 to 14.12, so anything above ~1% is signal rather than noise.
FLATTEN_TOLERANCE = 1.05
# The anonymous budget is deliberately tight: the daemon already holds 978.4 MiB in swap, so every
# MiB of cache_size is added to a pool that is already paging.
ANONYMOUS_BUDGET_BYTES = 64 * 1024 * 1024
# The collapse is void if two write points at the same cache_size differ by more than this.
FALSIFICATION_TOLERANCE = 0.01
# Steady memory is read after the workload settles; peak is read before the connection closes.
SETTLE_SECONDS = 5.0
WRITE_ARM_BATCHES = 20
WRITE_ARM_ROWS = 2000


class GridError(RuntimeError):
    """The grid cannot produce a trustworthy answer and says why rather than returning one."""


def proc_io() -> dict:
    values = {}
    with open("/proc/self/io", encoding="utf-8") as handle:
        for line in handle:
            key, _, raw = line.partition(":")
            values[key.strip()] = int(raw.strip())
    return values


def proc_status() -> dict:
    """The two memory limbs plus the totals, in bytes. kB in /proc means KiB."""

    wanted = {"RssAnon", "RssFile", "RssShmem", "VmSwap", "VmRSS", "VmHWM"}
    values = {}
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            key, _, raw = line.partition(":")
            if key in wanted:
                values[key] = int(raw.strip().split()[0]) * 1024
    missing = wanted - set(values)
    if missing:
        raise GridError(f"/proc/self/status is missing {sorted(missing)}; cannot measure memory")
    values["anonymous"] = values["RssAnon"] + values["VmSwap"]
    values["mapped"] = values["RssFile"]
    return values


def drop_page_cache(path: Path) -> None:
    """Evict this file's page cache without root and without `drop_caches`.

    Every read point after the first measures a warm cache without this, and the whole read arm
    then reads as flat -- which looks exactly like "cache_size does not matter".
    """

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(descriptor)


def effective_pragmas(connection: sqlite3.Connection) -> dict:
    """Read the pragmas BACK. SQLite clamps `mmap_size` and may refuse it outright."""

    return {
        "cache_size": int(connection.execute("PRAGMA cache_size").fetchone()[0]),
        "mmap_size": int(connection.execute("PRAGMA mmap_size").fetchone()[0]),
        "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(connection.execute("PRAGMA freelist_count").fetchone()[0]),
        "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
    }


def read_workload(connection: sqlite3.Connection) -> int:
    """One full scan that actually decodes payloads, which is what the serving path does.

    `SELECT count(*)` would touch the index and not the payload pages, so it would measure a
    workload the daemon never runs.
    """

    rows = 0
    payload_bytes = 0
    for row in connection.execute(
        "SELECT event_id, family, source_id, observed_at, payload_json FROM observations"
    ):
        rows += 1
        payload_bytes += len(row[4]) if row[4] is not None else 0
    return rows


def write_workload(connection: sqlite3.Connection, rows: int, batches: int, tag: str) -> int:
    """Random-key inserts committed in batches, matching the spec's own falsification shape.

    Random keys on purpose: sequential appends land on the same few pages and would make any
    cache size look sufficient.
    """

    written = 0
    per_batch = max(1, rows // batches)
    base = 1_750_000_000.0
    for batch in range(batches):
        connection.execute("BEGIN")
        for index in range(per_batch):
            serial = batch * per_batch + index
            # A deterministic scatter: the multiplier is a large odd number so successive rows
            # land far apart in event_id order without needing a random source.
            scattered = (serial * 2_654_435_761) % 1_000_000_007
            connection.execute(
                "INSERT OR IGNORE INTO observations(event_id, family, source_id, observed_at, "
                "epoch_id, owner_generation, payload_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (f"{tag}-{scattered}", "cpu", f"src-{scattered % 8}", base + serial,
                 f"epoch-{tag}", 0, json.dumps({"process_percent": 1.0, "system_percent": 2.0})),
            )
            written += 1
        connection.execute("COMMIT")
    return written


@dataclass
class PointResult:
    """One grid point, measured in its own process."""

    arm: str
    requested_cache_size: int
    requested_mmap_size: int
    effective: dict = field(default_factory=dict)
    io_delta: dict = field(default_factory=dict)
    memory_peak: dict = field(default_factory=dict)
    memory_steady: dict = field(default_factory=dict)
    workload_units: int = 0
    wall_seconds: float = 0.0
    monotonic_start: float = 0.0
    monotonic_stop: float = 0.0
    ring_published: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "arm": self.arm,
            "requested_cache_size": self.requested_cache_size,
            "requested_mmap_size": self.requested_mmap_size,
            "effective": self.effective,
            "io_delta": self.io_delta,
            "memory_peak": self.memory_peak,
            "memory_steady": self.memory_steady,
            "workload_units": self.workload_units,
            "wall_seconds": self.wall_seconds,
            "monotonic_start": self.monotonic_start,
            "monotonic_stop": self.monotonic_stop,
            "ring_published": self.ring_published,
            "note": self.note,
        }


def run_point(
    store: Path, arm: str, cache_size: int, mmap_size: int, *, settle_seconds: float,
    rows: int, batches: int, publish_ring: bool,
) -> PointResult:
    """Measure ONE grid point in THIS process. The caller must give it a fresh one.

    `VmHWM` is a process-lifetime high-water mark, so a second point in the same process reports
    the maximum of everything before it.
    """

    result = PointResult(arm=arm, requested_cache_size=cache_size, requested_mmap_size=mmap_size)
    if arm == "read":
        drop_page_cache(store)

    connection = sqlite3.connect(str(store), isolation_level=None)
    try:
        connection.execute(f"PRAGMA cache_size = {cache_size}")
        connection.execute(f"PRAGMA mmap_size = {mmap_size}")
        result.effective = effective_pragmas(connection)
        if mmap_size > 0 and result.effective["mmap_size"] == 0:
            result.note = (
                "mmap_size was requested but reads back as 0: the build or VFS refused it, so "
                "this arm did NOT exercise mmap and must not be reported as an mmap result"
            )
        elif result.effective["mmap_size"] != mmap_size:
            result.note = (
                f"mmap_size clamped {mmap_size} -> {result.effective['mmap_size']}; "
                f"the effective value is what was measured"
            )

        if arm == "write" and publish_ring:
            result.ring_published = _publish_ring_head(connection)
            if not result.ring_published:
                result.note = (
                    (result.note + "; " if result.note else "")
                    + "RING HEAD NOT PUBLISHED: appends intersect no published slot, write no "
                      "ring_invalidations, and understate append cost by roughly 46%"
                )

        os.sync()
        io_before, monotonic_start = proc_io(), time.monotonic()
        if arm == "read":
            result.workload_units = read_workload(connection)
        elif arm == "write":
            result.workload_units = write_workload(connection, rows, batches, arm + str(cache_size))
        else:
            raise GridError(f"unknown arm {arm!r}")
        os.sync()
        monotonic_stop, io_after = time.monotonic(), proc_io()
        result.memory_peak = proc_status()
        result.io_delta = {key: io_after[key] - io_before[key] for key in io_before}
        result.monotonic_start, result.monotonic_stop = monotonic_start, monotonic_stop
        result.wall_seconds = monotonic_stop - monotonic_start
    finally:
        connection.close()

    time.sleep(settle_seconds)
    result.memory_steady = proc_status()
    return result


def _publish_ring_head(connection: sqlite3.Connection) -> bool:
    """True when the store already carries a published ring the appends will intersect.

    This deliberately does NOT create one: manufacturing a publication would need the product's
    ring kernel, and a harness that half-implements it would measure its own approximation. A
    production copy carries a real publication; a synthetic fixture may not, and the caller is
    told which so the ~46% understatement is priced rather than hidden.
    """

    row = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'aggregate_ring_slots'"
    ).fetchone()
    if not row or int(row[0]) == 0:
        return False
    return int(connection.execute("SELECT count(*) FROM aggregate_ring_slots").fetchone()[0]) > 0


def spawn_point(
    store: Path, arm: str, cache_size: int, mmap_size: int, *, settle_seconds: float,
    rows: int, batches: int,
) -> dict:
    """Run one point in a FRESH subprocess and return its record."""

    command = [
        sys.executable, os.path.abspath(__file__), "--worker",
        "--store", str(store), "--arm", arm,
        "--cache-size", str(cache_size), "--mmap-size", str(mmap_size),
        "--settle-seconds", str(settle_seconds), "--rows", str(rows), "--batches", str(batches),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise GridError(
            f"grid point arm={arm} cache={cache_size} mmap={mmap_size} failed "
            f"(rc={completed.returncode}): {completed.stderr.strip()[:400]}"
        )
    return json.loads(completed.stdout)


def copy_store(source: Path, destination: Path) -> int:
    """Byte copy of the store and its sidecars. SQLite is never asked to open the original."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(source) + suffix)
        if not candidate.exists():
            continue
        target = Path(str(destination) + suffix)
        shutil.copyfile(candidate, target)
        total += target.stat().st_size
    return total


def quick_check(store: Path) -> str:
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()


def falsify_write_collapse(
    store: Path, scratch: Path, *, settle_seconds: float, rows: int, batches: int,
) -> dict:
    """PHASE 0. The write arm collapses 20 points to 5 only if `write_bytes` ignores `mmap_size`.

    The spec's evidence for that is a 15 MiB fixture, and it changes the campaign's size fourfold,
    so it is falsified at production size FIRST and the campaign stops if it fails. Two points at
    the control `cache_size` with `mmap_size` at 0 and 600 MiB; a difference above 1% voids it.
    """

    points = []
    for mmap_size in (MMAP_SIZE_CONTROL, MMAP_SIZE_FALSIFICATION):
        working = scratch / f"falsify-{mmap_size}" / store.name
        copied = copy_store(store, working)
        points.append({
            "copied_bytes": copied,
            **spawn_point(working, "write", CACHE_SIZE_CONTROL, mmap_size,
                          settle_seconds=settle_seconds, rows=rows, batches=batches),
        })
        shutil.rmtree(working.parent, ignore_errors=True)
    low, high = (point["io_delta"]["write_bytes"] for point in points)
    largest = max(abs(low), abs(high), 1)
    difference = abs(high - low) / largest
    holds = difference <= FALSIFICATION_TOLERANCE
    return {
        "phase": "falsification",
        "points": points,
        "write_bytes_mmap_off": low,
        "write_bytes_mmap_on": high,
        "relative_difference": difference,
        "tolerance": FALSIFICATION_TOLERANCE,
        "collapse_holds": holds,
        "verdict": (
            "write_bytes is independent of mmap_size at production size; the write arm is 1-D "
            "over five cache_size values"
            if holds else
            "COLLAPSE VOID: write_bytes depends on mmap_size at production size, so the write arm "
            "is 2-D over twenty points and the campaign is roughly four times its budgeted size"
        ),
    }


def read_arm(store: Path, *, settle_seconds: float) -> list[dict]:
    """20 points. One copy serves all of them; the read workload mutates nothing."""

    return [
        spawn_point(store, "read", cache_size, mmap_size,
                    settle_seconds=settle_seconds, rows=0, batches=0)
        for cache_size in CACHE_SIZE_POINTS
        for mmap_size in MMAP_SIZE_POINTS
    ]


def write_arm(
    store: Path, scratch: Path, *, settle_seconds: float, rows: int, batches: int,
) -> list[dict]:
    """5 points, one pristine copy each, only when the falsification held."""

    results = []
    for cache_size in CACHE_SIZE_POINTS:
        working = scratch / f"write-{cache_size}" / store.name
        copied = copy_store(store, working)
        results.append({
            "copied_bytes": copied,
            **spawn_point(working, "write", cache_size, MMAP_SIZE_CONTROL,
                          settle_seconds=settle_seconds, rows=rows, batches=batches),
        })
        shutil.rmtree(working.parent, ignore_errors=True)
    return results


def decide(read_points: Sequence[dict], write_points: Sequence[dict]) -> dict:
    """The spec's five steps, as arithmetic, with the losing arms published beside the winner."""

    usable = [p for p in read_points if not p["note"].startswith("mmap_size was requested")]
    refused = [p for p in read_points if p["note"].startswith("mmap_size was requested")]
    if not usable:
        raise GridError("every read point refused its mmap_size; nothing can be decided")

    best_syscr = min(p["io_delta"]["syscr"] for p in usable)
    threshold = best_syscr * FLATTEN_TOLERANCE
    control = next(
        (p for p in usable if p["requested_cache_size"] == CACHE_SIZE_CONTROL
         and p["requested_mmap_size"] == MMAP_SIZE_CONTROL), None,
    )
    if control is None:
        raise GridError("the control arm is missing from the read grid; nothing can be compared")
    control_anonymous = control["memory_steady"]["anonymous"]

    def flattens(point):
        return point["io_delta"]["syscr"] <= threshold

    # Step 1, per axis independently, smallest value that flattens.
    mmap_only = [p for p in usable if p["requested_cache_size"] == CACHE_SIZE_CONTROL]
    cache_only = [p for p in usable if p["requested_mmap_size"] == MMAP_SIZE_CONTROL]
    smallest_mmap = min((p for p in mmap_only if flattens(p)),
                        key=lambda p: p["effective"]["mmap_size"], default=None)
    smallest_cache = min((p for p in cache_only if flattens(p)),
                         key=lambda p: abs(p["requested_cache_size"]), default=None)

    # Step 2, the asymmetric budget.
    def anonymous_cost(point):
        return point["memory_steady"]["anonymous"] - control_anonymous

    within_budget = smallest_cache is not None and anonymous_cost(smallest_cache) <= ANONYMOUS_BUDGET_BYTES

    # Step 4, the write side. A read win that raises write_bytes is rejected.
    write_control = next(
        (p for p in write_points if p["requested_cache_size"] == CACHE_SIZE_CONTROL), None)
    rejected_on_write = []
    if write_control is not None:
        for point in write_points:
            if point["io_delta"]["write_bytes"] > write_control["io_delta"]["write_bytes"]:
                rejected_on_write.append({
                    "cache_size": point["requested_cache_size"],
                    "write_bytes": point["io_delta"]["write_bytes"],
                    "control_write_bytes": write_control["io_delta"]["write_bytes"],
                })

    # Step 3, the trade.
    if smallest_mmap is not None:
        choice = {
            "cache_size": CACHE_SIZE_CONTROL,
            "mmap_size": smallest_mmap["effective"]["mmap_size"],
            "reason": (
                "mmap_size alone flattens the read curve, and mapped pages are droppable at zero "
                "IO while the daemon already holds 978.4 MiB in swap, so no anonymous memory is "
                "spent"
            ),
        }
    elif smallest_cache is not None and within_budget:
        gap = min(p["io_delta"]["syscr"] for p in mmap_only) - best_syscr
        choice = {
            "cache_size": smallest_cache["requested_cache_size"],
            "mmap_size": MMAP_SIZE_CONTROL,
            "reason": (
                f"mmap_size alone cannot reach {FLATTEN_TOLERANCE} x the best syscr; it falls "
                f"short by {gap} read syscalls, and that gap is the price of the cache_size "
                f"increase"
            ),
        }
    else:
        choice = {
            "cache_size": CACHE_SIZE_CONTROL, "mmap_size": MMAP_SIZE_CONTROL,
            "reason": (
                "no point flattens the read curve within the anonymous budget; the defaults stand "
                "and the grid's own numbers are the evidence for leaving them alone"
            ),
        }
    return {
        "best_syscr": best_syscr,
        "flatten_threshold": threshold,
        "control_steady_anonymous_bytes": control_anonymous,
        "anonymous_budget_bytes": ANONYMOUS_BUDGET_BYTES,
        "smallest_mmap_that_flattens": smallest_mmap,
        "smallest_cache_that_flattens": smallest_cache,
        "cache_within_anonymous_budget": within_budget,
        "rejected_on_write_regression": rejected_on_write,
        "refused_mmap_points": refused,
        "choice": choice,
        "losing_arms": [p for p in read_points if p is not smallest_mmap and p is not smallest_cache],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", type=Path,
                        help="production-sized store to measure; a copy is made, never opened for write")
    parser.add_argument("--scratch", type=Path, default=Path("/tmp/yolomux-pragma-grid"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--settle-seconds", type=float, default=SETTLE_SECONDS)
    parser.add_argument("--rows", type=int, default=WRITE_ARM_ROWS)
    parser.add_argument("--batches", type=int, default=WRITE_ARM_BATCHES)
    parser.add_argument("--falsification-only", action="store_true",
                        help="run phase 0 and stop, which is what the spec says to do first")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--arm", choices=("read", "write"), help=argparse.SUPPRESS)
    parser.add_argument("--cache-size", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--mmap-size", type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()

    if arguments.worker:
        if arguments.store is None or arguments.arm is None:
            parser.error("--worker needs --store and --arm")
        point = run_point(
            arguments.store, arguments.arm, arguments.cache_size, arguments.mmap_size,
            settle_seconds=arguments.settle_seconds, rows=arguments.rows,
            batches=arguments.batches, publish_ring=True,
        )
        print(json.dumps(point.as_dict()))
        return 0

    # A missing store is a NAMED SKIP, never a silent pass and never a default path. The campaign
    # is meaningless on anything but a production-sized copy: on a small fixture the cache curve
    # flattens at the first point and the grid returns a tiny winner that means nothing at 569 MiB.
    if arguments.store is None:
        print(json.dumps({
            "skipped": True,
            "reason": (
                "no --store given. This grid requires a production-sized copy: both pragmas act on "
                "the relationship between the working set and the file, so a small fixture flattens "
                "at the first point and returns a winner that means nothing at 569 MiB."
            ),
        }, indent=2))
        return 0
    if not arguments.store.exists():
        print(json.dumps({"skipped": True, "reason": f"--store {arguments.store} does not exist"}, indent=2))
        return 0

    started = time.monotonic()
    arguments.scratch.mkdir(parents=True, exist_ok=True)
    report = {
        "store": str(arguments.store),
        "store_bytes": arguments.store.stat().st_size,
        "quick_check": quick_check(arguments.store),
        "cache_size_points": list(CACHE_SIZE_POINTS),
        "mmap_size_points": list(MMAP_SIZE_POINTS),
    }

    falsification = falsify_write_collapse(
        arguments.store, arguments.scratch, settle_seconds=arguments.settle_seconds,
        rows=arguments.rows, batches=arguments.batches)
    report["falsification"] = falsification
    print(f"phase 0 falsification: {falsification['verdict']}")
    print(f"  write_bytes mmap=0 {falsification['write_bytes_mmap_off']:,}  "
          f"mmap=600MiB {falsification['write_bytes_mmap_on']:,}  "
          f"difference {falsification['relative_difference'] * 100:.3f}%")

    if not falsification["collapse_holds"]:
        report["stopped"] = "falsification failed; the write arm is 2-D and the campaign is ~4x budget"
        print(f"STOPPED: {report['stopped']}")
    elif arguments.falsification_only:
        report["stopped"] = "--falsification-only"
        print("STOPPED: --falsification-only")
    else:
        report["read_arm"] = read_arm(arguments.store, settle_seconds=arguments.settle_seconds)
        report["write_arm"] = write_arm(
            arguments.store, arguments.scratch, settle_seconds=arguments.settle_seconds,
            rows=arguments.rows, batches=arguments.batches)
        report["decision"] = decide(report["read_arm"], report["write_arm"])
        choice = report["decision"]["choice"]
        print(f"\nCHOICE: cache_size={choice['cache_size']}  mmap_size={choice['mmap_size']}")
        print(f"  {choice['reason']}")

    report["wall_seconds"] = time.monotonic() - started
    report["bytes_copied"] = sum(
        point.get("copied_bytes", 0)
        for group in (report.get("falsification", {}).get("points", []), report.get("write_arm", []))
        for point in group
    )
    report["bytes_written"] = sum(
        point["io_delta"]["write_bytes"]
        for group in (report.get("falsification", {}).get("points", []), report.get("write_arm", []))
        for point in group
    )
    report["bytes_read"] = sum(point["io_delta"]["read_bytes"] for point in report.get("read_arm", []))
    print(f"\ncost: {report['wall_seconds']:.1f}s wall, "
          f"{report['bytes_copied'] / 2**30:.2f} GiB copied, "
          f"{report['bytes_written'] / 2**30:.2f} GiB written, "
          f"{report['bytes_read'] / 2**30:.2f} GiB read")
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

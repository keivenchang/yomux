"""Compare a post-change statsd resource window against the historical baseline.

Preserves historical measurement tooling from the v0.7.22 P0 release decision. The former
`DOIT.p0.e3.statsd-resource-bounds.md` scope was closed for release bookkeeping; post-release
statsd follow-up work has separate active owners. This tool records the historical requirement:
*"Run a post-change six-hour A/B under
the same host and gate envelope. Compare block writes, statsd CPU, WAL growth, `VACUUM` writes,
and UI sample freshness against the baseline."* The baseline it compares against is line 334,
whose authoritative window is 2026-08-25 22:00:00 PT -> 2026-08-26 04:00:00 PT, epochs
1787720400.0 -> 1787742000.0.

Line 334 names four quantities line 583 does not -- database size, transaction counts,
reclaimable-page ratio, and per-family cadence -- so this computes all nine. A comparison that
answered only the five would leave the baseline's own recorded quantities unexamined.

What this reads
---------------
Whatever the recorders actually wrote, not an invented format:

* `raw/fast5.jsonl` from `collect_fast_v4.py` -- one sample per second, 85 fields, carrying the
  statsd process's `/proc/<pid>/io`, CPU, PSS/RSS, the SQLite `db_bytes`/`db_pages`/
  `freelist_pages`/`change_counter` header trio, and the WAL's `wal_bytes`/`wal_cum_frames`/
  `wal_cum_commits`/`wal_resets`.
* `raw/fast5.events.jsonl` -- `wal_reset`, `subject_epoch`, `chain_resume`.
* `raw/vacuum_events.jsonl` -- `vacuum_detected`, `acquisition_start`, `acquisition_end`.
* `raw/acquisitions.jsonl` from `slow_lane_v3.py` -- `per_family_cadence`,
  `reclaimable_ratio_dbstat`, `observations_total`, `dbstat_totals`.

THE TRAP THIS MODULE EXISTS TO AVOID
------------------------------------
statsd runs `--idle-seconds 60.0` and respawns from disk, so `/proc/<pid>/io` restarts at zero.
`collect_fast_v4.py` emits BOTH the raw per-process counter (`io_write_bytes`, reset by a
respawn) and an offset-corrected one (`io_cum_write_bytes`, continuous across respawns), and the
same pair for CPU. **Every cumulative metric here reads the `_cum_` series.** Differencing the
raw series across a respawn yields a large negative number, which a mean would quietly absorb.
`subject_epoch_index` records how many respawns a window contains; the report states it so a
reader can see whether the correction was load-bearing for that window.

WHY THE STATISTICS ARE BLOCKED, NOT PER-SAMPLE
---------------------------------------------
A six-hour 1 Hz window holds ~21,600 samples, and treating them as 21,600 independent
observations would shrink every confidence interval by about 17x against the truth: consecutive
one-second samples of a write rate are strongly autocorrelated, and a VACUUM or a checkpoint
moves a whole run of them together. This aggregates into non-overlapping blocks (default 300 s,
so 72 blocks per six-hour arm) and compares block means. That is the honest denominator, and it
is the difference between "we can detect a 3% change" and "we can detect a 25% change".

Every comparison therefore reports its own MINIMUM DETECTABLE EFFECT at 80% power, and a caller
is expected to read it before reading the difference. A metric whose MDE exceeds the observed
difference has not detected anything, and `significant` stays False.

WHAT DOES NOT TRANSFER FROM `merged_transaction_append_cost.py`
---------------------------------------------------------------
That grid modelled two families at 1 Hz against a static ring head. The live mix is roughly
26,687 observations/hour across six families with `service_load` alone at 80.69%, and a static
ring head understates append cost by about 46% because appends only write `ring_invalidations`
when they intersect a published slot. **Its ratios transfer; its absolute MB figures do not.**
This module measures the live daemon, so neither caveat applies to its own numbers -- but it
must not be asked to reproduce that grid's absolutes.
"""

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# Two-sided alpha 0.05 and 80% power, the conventional pair. Stated as constants because the
# minimum detectable effect is only meaningful alongside the pair that produced it.
Z_ALPHA_HALF = 1.959963984540054
Z_POWER = 0.8416212335729143
# A normal approximation to Welch's t is accurate to well under a percentage point by 30 degrees
# of freedom. Below that this refuses to report rather than printing a confident wrong p-value.
MIN_BLOCKS_PER_ARM = 30
DEFAULT_BLOCK_SECONDS = 300.0
# A rewrite lands in one burst. The retained audit measured 652,726,272 bytes for a single run,
# so a two-minute bracket around the detected instant captures it without swallowing a
# neighbouring checkpoint.
# A compaction's whole cost lands in a sharp five-to-six second burst against 0.6-17 MiB/s
# either side, so its cost must be BURST-bounded, not window-bounded. A fixed bracket clips the
# tail: `[t-30, t+3]` reports 2.020x the post-vacuum size where the true relationship is
# `write_bytes ~= 3.008 x post-vacuum size`, confirmed eleven times. The search starts at the
# detected instant and grows while the rate stays above this multiple of the local background.
VACUUM_BURST_BACKGROUND_MULTIPLE = 3.0
# How far either side of the instant the burst is searched for at all. Generous: the bound that
# matters is the rate falling back to background, not this.
VACUUM_BURST_SEARCH_SECONDS = 300.0
# Background is the median rate over the search span, which a five-second burst cannot move.
VACUUM_BURST_MIN_BACKGROUND_BYTES = 1.0
# NOTHING SETTLES INSIDE A COMPACTION CYCLE, and this replaces the constant that assumed it did.
# `E3-RECOVERY-118` measured three matched cycles on an UNCHANGED subject: write rate drifts -21.7%
# and -24.0% from minute 10 to cycle end, and `wchar` per commit -31.0% and -35.1%. Neither
# complete cycle ever flattens. The fixed settling exclusion this replaces therefore
# admitted roughly 25 minutes of still-drifting data as settled and would have reported a confident
# wrong comparison -- worse than refusing. **There is no correct replacement constant**, so
# acceptance no longer depends on one: arms are matched by PHASE within a measured cycle, and every
# difference is judged against that metric's own demonstrated cycle-to-cycle reproducibility.
#
# Measured spread at matched phase across those three cycles. A difference smaller than its
# metric's floor is not distinguishable from the next cycle of the same unchanged subject,
# whatever the t-statistic says.
REPRODUCIBILITY_FLOOR: dict[str, float | None] = {
    # The reproducible comparison owner: an order of magnitude tighter than anything else here.
    "commits": 0.001,
    # WAL frames and checkpoint behaviour, ~3%.
    "wal_frames": 0.03,
    "wal_bytes": 0.03,
    # `wchar`, `syscw`, CPU and database growth all landed in the 4-6% band; the floor takes the
    # loose end of the band, because a floor that is too tight reports noise as signal.
    "write_syscalls": 0.06,
    "statsd_cpu": 0.06,
    "db_bytes": 0.06,
    # 18% was OBSERVED between cycles; ~25% is the demonstrated floor. Anything under it is inside
    # the spread of an unchanged subject.
    "block_write_bytes": 0.25,
    # FAIL CLOSED. `None` means no floor has been established, so no difference in this metric may
    # be called a change. An unknown floor is not a zero floor, and assuming one is exactly how a
    # confident wrong comparison gets published.
    "read_syscalls": None,
    "main_file_change_counter": None,
    "statsd_rss_bytes": None,
}
# Arms must sit at comparable points in their own compaction cycle. This is ALIGNMENT, not
# settling: it says the two measurements are taken at the same place on the same shape, which is
# the only comparison the recovery finding leaves available. The cycle length is MEASURED from the
# data rather than assumed, so this fraction is the only tuned quantity and it is a tolerance on a
# ratio, not a claim about when anything settles.
PHASE_MATCH_TOLERANCE = 0.10

CUMULATIVE = "cumulative"
LEVEL = "level"


@dataclass(frozen=True)
class Metric:
    """One comparable quantity, and how to read it out of a fast-lane sample."""

    name: str
    field: str
    unit: str
    kind: str
    source: str
    scale: float = 1.0

    def value(self, sample: dict) -> float:
        raw = sample[self.field]
        if raw is None:
            raise ValueError(f"{self.name}: {self.field} is null in a sample that must carry it")
        return float(raw) * self.scale


# `_cum_` for everything counter-shaped: see the module docstring. `change_counter` is SQLite's
# own header counter and is a property of the FILE, not of any process, so it needs no respawn
# correction -- it is the transaction count line 334 asks for.
FAST_LANE_METRICS = (
    Metric("block_write_bytes", "io_cum_write_bytes", "bytes/s", CUMULATIVE, "line 583: block writes"),
    Metric("statsd_cpu", "statsd_cpu_seconds_cum", "cores", CUMULATIVE, "line 583: statsd CPU"),
    Metric("wal_frames", "wal_cum_frames", "frames/s", CUMULATIVE, "line 583: WAL growth"),
    Metric("wal_bytes", "wal_bytes", "bytes", LEVEL, "line 583: WAL growth"),
    Metric("write_syscalls", "io_cum_syscw", "calls/s", CUMULATIVE, "supporting"),
    Metric("read_syscalls", "io_cum_syscr", "calls/s", CUMULATIVE, "supporting"),
    # `wal_cum_commits`, NEVER `change_counter`. The WAL scanner at `collect_fast_v4.py:217`
    # counts committed write transactions by walking WAL frame headers; that is the commit count.
    Metric("commits", "wal_cum_commits", "commits/s", CUMULATIVE, "line 334: transaction counts"),
    # NOT a transaction rate, and named so nobody reads it as one again. `collect_fast_v4.py:210`
    # reads SQLite header bytes 24..27 of the MAIN database file, which is the file change
    # counter, and the collector's own docstring at `collect_fast_v4.py:193-197` says these values
    # are "a last-checkpoint view, not the live one" because in WAL mode page 1 may be newer
    # inside the WAL. Retained because it is a real signal about checkpoint behaviour -- but it is
    # a diagnostic, never the answer to "how many transactions".
    Metric("main_file_change_counter", "change_counter", "increments/s", CUMULATIVE,
           "diagnostic: main-file header, last-checkpoint view; NOT a transaction rate"),
    Metric("db_bytes", "db_bytes", "bytes", LEVEL, "line 334: database size"),
    Metric("statsd_rss_bytes", "mem_Rss_kb", "bytes", LEVEL, "supporting", scale=1024.0),
)


@dataclass(frozen=True)
class Window:
    """A closed epoch interval, named for the report."""

    label: str
    start_epoch: float
    end_epoch: float

    def __post_init__(self) -> None:
        if not self.end_epoch > self.start_epoch:
            raise ValueError(f"{self.label}: window end must follow its start")

    @property
    def seconds(self) -> float:
        return self.end_epoch - self.start_epoch

    def contains(self, epoch: float) -> bool:
        return self.start_epoch <= epoch <= self.end_epoch


def read_jsonl(path: Path) -> tuple[dict, ...]:
    """Read a recorder's JSONL output, refusing a truncated final line rather than dropping it.

    A recorder still running can leave a partial last line. That is an expected outcome with a
    reason, so it is reported as one: the caller learns the file is live, instead of silently
    comparing one fewer sample than it thinks.
    """

    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as error:
                if number == sum(1 for _ in path.open(encoding="utf-8")):
                    raise ValueError(
                        f"{path}: final line {number} is truncated; the recorder is still writing"
                    ) from error
                raise ValueError(f"{path}: line {number} is not valid JSON") from error
    return tuple(rows)


def samples_in(samples: Sequence[dict], window: Window) -> tuple[dict, ...]:
    return tuple(sample for sample in samples if window.contains(float(sample["epoch"])))


def blocks(samples: Sequence[dict], window: Window, block_seconds: float) -> tuple[tuple[dict, ...], ...]:
    """Partition a window into non-overlapping fixed-length blocks of samples.

    Blocks, not samples, are the unit of comparison. A partial trailing block is dropped rather
    than kept at a shorter duration, because a rate computed over a shorter span is not
    exchangeable with its neighbours and would bias the variance estimate the MDE rests on.
    """

    if block_seconds <= 0:
        raise ValueError("block_seconds must be positive")
    edges = []
    start = window.start_epoch
    while start + block_seconds <= window.end_epoch:
        edges.append((start, start + block_seconds))
        start += block_seconds
    grouped = []
    for low, high in edges:
        members = tuple(s for s in samples if low <= float(s["epoch"]) < high)
        # Two samples is the minimum from which a cumulative counter yields one difference.
        if len(members) >= 2:
            grouped.append(members)
    return tuple(grouped)


def pooled_blocks(
    samples: Sequence[dict], windows: Sequence[Window], block_seconds: float,
) -> tuple[tuple[dict, ...], ...]:
    """Blocks from several disjoint windows, pooled, with no block straddling a gap.

    Retained for pooling PHASE-MATCHED spans across cycles, which is what the recovery finding
    leaves available. It no longer serves an exclusion rule: the requirement to drop the first
    ~100 minutes after each compaction is WITHDRAWN, because `E3-RECOVERY-118` showed nothing
    settles inside a cycle and so there is no span to keep. What survives is that a block must
    never straddle a gap between spans -- averaging across a discontinuity invents a rate that
    neither span had.
    """

    grouped: list[tuple[dict, ...]] = []
    for window in windows:
        grouped.extend(blocks(samples_in(samples, window), window, block_seconds))
    return tuple(grouped)


def block_value(metric: Metric, block: Sequence[dict]) -> float:
    """Reduce one block to one number, per the metric's kind."""

    if metric.kind == CUMULATIVE:
        first, last = block[0], block[-1]
        span = float(last["epoch"]) - float(first["epoch"])
        if span <= 0:
            raise ValueError(f"{metric.name}: block spans no time")
        delta = metric.value(last) - metric.value(first)
        if delta < 0:
            raise ValueError(
                f"{metric.name}: cumulative counter went backwards by {abs(delta):.0f} across a "
                f"block, which means a respawn was not offset-corrected; read the _cum_ series"
            )
        return delta / span
    if metric.kind == LEVEL:
        return statistics.fmean(metric.value(sample) for sample in block)
    raise ValueError(f"{metric.name}: unknown metric kind {metric.kind!r}")


@dataclass(frozen=True)
class Comparison:
    """One metric's before/after result, with the resolution that qualifies it."""

    metric: str
    unit: str
    source: str
    baseline_mean: float
    candidate_mean: float
    baseline_blocks: int
    candidate_blocks: int
    difference: float
    percent_change: float | None
    minimum_detectable_effect: float
    minimum_detectable_percent: float | None
    reproducibility_floor: float | None
    relative_difference: float | None
    p_value: float | None
    significant: bool
    note: str

    def as_dict(self) -> dict:
        return {
            "metric": self.metric, "unit": self.unit, "source": self.source,
            "baseline_mean": self.baseline_mean, "candidate_mean": self.candidate_mean,
            "baseline_blocks": self.baseline_blocks, "candidate_blocks": self.candidate_blocks,
            "difference": self.difference, "percent_change": self.percent_change,
            "minimum_detectable_effect": self.minimum_detectable_effect,
            "minimum_detectable_percent": self.minimum_detectable_percent,
            "reproducibility_floor": self.reproducibility_floor,
            "relative_difference": self.relative_difference,
            "p_value": self.p_value, "significant": self.significant, "note": self.note,
        }


def compare_series(
    metric: Metric, baseline: Sequence[float], candidate: Sequence[float]
) -> Comparison:
    """Welch two-sample comparison on block means, reporting its own resolution first.

    `significant` requires BOTH a p-value under alpha and an observed difference at least as
    large as the minimum detectable effect. The second condition is not redundant: a marginally
    significant p on a metric whose MDE exceeds the difference is the exact shape of a result
    that does not replicate, and this refuses to call it one.
    """

    n_a, n_b = len(baseline), len(candidate)
    if n_a < 2 or n_b < 2:
        raise ValueError(f"{metric.name}: each arm needs at least two blocks, got {n_a} and {n_b}")
    mean_a, mean_b = statistics.fmean(baseline), statistics.fmean(candidate)
    var_a, var_b = statistics.variance(baseline), statistics.variance(candidate)
    standard_error = math.sqrt(var_a / n_a + var_b / n_b)
    difference = mean_b - mean_a
    minimum_detectable = (Z_ALPHA_HALF + Z_POWER) * standard_error

    scale = max(abs(mean_a), abs(mean_b), 1.0)
    negligible = abs(difference) <= NEGLIGIBLE_RELATIVE_DIFFERENCE * scale

    if negligible:
        # Checked BEFORE the test, not after, because the test is exactly what gets it wrong here.
        p_value = 1.0
        note = (
            f"difference is {abs(difference):.3g} against a magnitude of {scale:.3g}, below the "
            f"{NEGLIGIBLE_RELATIVE_DIFFERENCE:g} noise floor; not tested"
        )
    elif standard_error == 0.0:
        p_value = 0.0
        note = "both arms are perfectly constant and differ beyond the noise floor"
    else:
        degrees = (var_a / n_a + var_b / n_b) ** 2 / (
            (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
        )
        if degrees < MIN_BLOCKS_PER_ARM:
            p_value = None
            note = (
                f"REFUSED: Welch df {degrees:.1f} is below {MIN_BLOCKS_PER_ARM}, so the normal "
                f"approximation this uses is not trustworthy; lengthen the window or the blocks"
            )
        else:
            p_value = 2.0 * (1.0 - statistics.NormalDist().cdf(abs(difference) / standard_error))
            note = f"Welch df {degrees:.1f}, normal approximation"

    # THE REPRODUCIBILITY FLOOR, and it is checked alongside the statistical test rather than
    # instead of it. `E3-RECOVERY-118` measured an unchanged subject differing 17.7-18% in
    # `block_write_bytes` between compaction cycles, which any t-test on enough blocks will call
    # significant. Statistical significance answers "is this difference bigger than the noise
    # WITHIN these arms"; the floor answers "is it bigger than the spread BETWEEN cycles of the
    # same unchanged subject". A comparison needs both.
    floor = REPRODUCIBILITY_FLOOR.get(metric.name, None)
    relative = abs(difference) / abs(mean_a) if mean_a else None
    if floor is None:
        below_floor = True
        note += (
            f"; REFUSED as a change: `{metric.name}` has no established reproducibility floor, and "
            f"an unknown floor is not a zero floor"
        )
    else:
        below_floor = relative is not None and relative < floor
        if below_floor:
            note += (
                f"; inside the {floor:.1%} cycle-to-cycle reproducibility floor for "
                f"`{metric.name}`, so it is not distinguishable from the same unchanged subject "
                f"one compaction cycle later"
            )

    detected = (
        not negligible
        and not below_floor
        and p_value is not None and p_value < 0.05
        and abs(difference) >= minimum_detectable
    )
    if p_value is not None and p_value < 0.05 and not detected:
        note += "; p is under alpha but the difference is below the MDE, so this is NOT called"
    return Comparison(
        metric=metric.name, unit=metric.unit, source=metric.source,
        baseline_mean=mean_a, candidate_mean=mean_b,
        baseline_blocks=n_a, candidate_blocks=n_b,
        difference=difference,
        percent_change=(100.0 * difference / mean_a) if mean_a else None,
        minimum_detectable_effect=minimum_detectable,
        minimum_detectable_percent=(100.0 * minimum_detectable / mean_a) if mean_a else None,
        reproducibility_floor=floor, relative_difference=relative,
        p_value=p_value, significant=detected, note=note,
    )


def completed_vacuums(vacuum_events: Sequence[dict], window: Window) -> tuple[dict, ...]:
    """Every compaction that COMPLETED in the window, counted by `service_timestamp` only.

    `byte_step` must never be used to count. It re-reports one compaction on successive polls --
    five records for a single rewrite with identical `db_bytes_before`/`after` -- so any per-run
    cost divided by that count understates roughly threefold. `service_timestamp` reads the
    service's own `last_vacuumed_at`, and the service records one timestamp per completed rewrite.

    `last_vacuumed_at` is carried through so a reader can tell which limb of the recovery
    transient a measurement sits on, which is requirement (4) of the A/B contract.
    """

    completed = []
    for event in vacuum_events:
        if event["kind"] != "vacuum_detected" or event["detector"] != "service_timestamp":
            continue
        if not window.contains(float(event["epoch"])):
            continue
        completed.append({
            "detected_epoch": float(event["epoch"]),
            "last_vacuumed_at": float(event["last_vacuumed_at"]),
            "last_vacuumed_at_pt": event["last_vacuumed_at_pt"],
            "previous_last_vacuumed_at": float(event["previous_last_vacuumed_at"]),
        })
    completed.sort(key=lambda item: item["last_vacuumed_at"])
    return tuple(completed)


def _burst_bounds(samples: Sequence[dict], instant: float) -> tuple[int, int, float] | None:
    """Grow a window outward from `instant` while the write rate stays above background.

    Returns the inclusive sample index pair and the background rate, or None when the instant is
    not covered by enough samples to judge.
    """

    search = Window("burst", instant - VACUUM_BURST_SEARCH_SECONDS, instant + VACUUM_BURST_SEARCH_SECONDS)
    inside = samples_in(samples, search)
    if len(inside) < 5:
        return None
    rates = []
    for index in range(len(inside) - 1):
        span = float(inside[index + 1]["epoch"]) - float(inside[index]["epoch"])
        delta = float(inside[index + 1]["io_cum_write_bytes"]) - float(inside[index]["io_cum_write_bytes"])
        rates.append(delta / span if span > 0 else 0.0)
    background = max(statistics.median(rates), VACUUM_BURST_MIN_BACKGROUND_BYTES)
    threshold = background * VACUUM_BURST_BACKGROUND_MULTIPLE
    centre = min(range(len(rates)), key=lambda i: abs(float(inside[i]["epoch"]) - instant))
    # The detected instant can sit just after the burst, so find the nearest above-threshold
    # sample before committing to a centre.
    peak = max(range(len(rates)), key=lambda i: rates[i] if abs(float(inside[i]["epoch"]) - instant)
               <= VACUUM_BURST_SEARCH_SECONDS else -1.0)
    if rates[peak] < threshold:
        return None
    centre = peak
    low = centre
    while low > 0 and rates[low - 1] >= threshold:
        low -= 1
    high = centre
    while high < len(rates) - 1 and rates[high + 1] >= threshold:
        high += 1
    return low, high + 1, background


def vacuum_write_bytes(
    samples: Sequence[dict], vacuum_events: Sequence[dict], window: Window,
) -> dict:
    """Cost of each completed compaction, BURST-bounded and counted by `service_timestamp`.

    Two corrections over the first version, both from measurement rather than reasoning.

    COUNTING. `byte_step` re-reports one rewrite five times; `service_timestamp` reports it once.
    Counting is now `service_timestamp` only, and `byte_step` is retained purely as a
    cross-check whose disagreement is reported rather than reconciled.

    BOUNDING. The cost lands in a five-to-six second burst against a background three orders of
    magnitude lower, so a fixed bracket clips the tail: `[t-30, t+3]` gives 2.020x the
    post-vacuum size against a true 3.008x, and that clipping is where the previously reported
    1,092 MB low end came from. The burst is now found by growing outward from the instant while
    the write rate stays above `VACUUM_BURST_BACKGROUND_MULTIPLE` times the local median, so the
    bound is the rate returning to background rather than a guessed span.
    """

    completed = completed_vacuums(vacuum_events, window)
    byte_step = [
        event for event in vacuum_events
        if event["kind"] == "vacuum_detected" and event["detector"] == "byte_step"
        and window.contains(float(event["end_epoch"]))
    ]

    attributed = []
    for entry in completed:
        instant = entry["last_vacuumed_at"]
        bounds = _burst_bounds(samples, instant)
        if bounds is None:
            attributed.append({**entry, "write_bytes": None,
                               "reason": "no burst above background near the instant"})
            continue
        low, high, background = bounds
        inside = samples_in(samples, Window(
            "burst", instant - VACUUM_BURST_SEARCH_SECONDS, instant + VACUUM_BURST_SEARCH_SECONDS))
        first, last = inside[low], inside[high]
        cost = float(last["io_cum_write_bytes"]) - float(first["io_cum_write_bytes"])
        attributed.append({
            **entry,
            "burst_start_pt": first["pt"], "burst_end_pt": last["pt"],
            "burst_seconds": float(last["epoch"]) - float(first["epoch"]),
            "background_bytes_per_second": background,
            "write_bytes": cost,
            "db_bytes_after": float(last["db_bytes"]),
            "write_bytes_over_post_size": (
                cost / float(last["db_bytes"]) if float(last["db_bytes"]) else None
            ),
            "reason": "",
        })
    known = [item["write_bytes"] for item in attributed if item["write_bytes"] is not None]
    return {
        "events": len(completed),
        "byte_step_detections": len(byte_step),
        "counting_detector": "service_timestamp",
        "vacuums": attributed,
        "total_bytes": sum(known) if known else 0,
        "bounding": "burst: grown from the instant while the rate exceeds 3x the local median",
        "expected_ratio_note": "write_bytes ~= 3.008 x post-vacuum size, confirmed eleven times",
    }


def compaction_cycle_seconds(vacuums: Sequence[dict]) -> float | None:
    """The measured interval between consecutive completed compactions, or None below two.

    MEASURED, never assumed. Phase only means anything relative to a cycle length, and the cycle
    length is a property of the host and the store rather than a constant anyone can write down.
    Fewer than two completed compactions means the cycle is unknown, and unknown fails closed.
    """

    instants = sorted(float(entry["last_vacuumed_at"]) for entry in vacuums)
    if len(instants) < 2:
        return None
    gaps = [b - a for a, b in zip(instants, instants[1:])]
    return statistics.median(gaps)


def vacuum_alignment(
    baseline_vacuums: Sequence[dict], baseline_window: Window,
    candidate_vacuums: Sequence[dict], candidate_window: Window,
) -> dict:
    """Requirement (1), restated after `E3-RECOVERY-118`: arms must be PHASE-MATCHED.

    The earlier version asked whether each arm sat on a fixed-duration "recovery limb" and treated
    anything past it as settled. Three matched cycles on an unchanged subject showed nothing
    settles inside a cycle at all -- write rate still drifting -21.7% and -24.0% at cycle end --
    so "settled" was never a state either arm could reach and the question was unanswerable as
    posed.

    What survives is phase-matching, which was always the real design. Each arm is placed at a
    FRACTION of its own measured compaction cycle, and the two must sit at comparable fractions.
    A post-change window opening just after a compaction against a baseline opening late in one
    compares two different points on the same decaying shape and reports the difference between
    those points as though it were the change.
    """

    def offset(vacuums, window):
        prior = [float(v["last_vacuumed_at"]) for v in vacuums
                 if float(v["last_vacuumed_at"]) <= window.start_epoch]
        return None if not prior else window.start_epoch - max(prior)

    base_offset = offset(baseline_vacuums, baseline_window)
    cand_offset = offset(candidate_vacuums, candidate_window)
    base_cycle = compaction_cycle_seconds(baseline_vacuums)
    cand_cycle = compaction_cycle_seconds(candidate_vacuums)

    problems = []
    base_phase = cand_phase = None
    if base_offset is None or cand_offset is None:
        problems.append(
            "at least one arm has no completed compaction before it, so its phase is unknown; "
            "extend the recording backwards or state the assumption"
        )
    if base_cycle is None or cand_cycle is None:
        problems.append(
            "the compaction cycle length is not measurable -- fewer than two completed "
            "compactions in an arm -- so phase cannot be computed and no comparison is offered"
        )
    if not problems:
        base_phase = (base_offset % base_cycle) / base_cycle
        cand_phase = (cand_offset % cand_cycle) / cand_cycle
        # Circular distance: phase 0.98 and phase 0.02 are 0.04 apart, not 0.96.
        raw = abs(base_phase - cand_phase)
        separation = min(raw, 1.0 - raw)
        if separation > PHASE_MATCH_TOLERANCE:
            problems.append(
                f"arms are not phase-matched: baseline sits at {base_phase:.3f} of its compaction "
                f"cycle and candidate at {cand_phase:.3f}, {separation:.3f} apart against a "
                f"{PHASE_MATCH_TOLERANCE:.2f} tolerance. Nothing settles inside a cycle, so two "
                f"different phases measure two different points on the same decaying shape"
            )
    return {
        "baseline_seconds_since_vacuum": base_offset,
        "candidate_seconds_since_vacuum": cand_offset,
        "baseline_cycle_seconds": base_cycle,
        "candidate_cycle_seconds": cand_cycle,
        "baseline_phase": base_phase,
        "candidate_phase": cand_phase,
        "phase_match_tolerance": PHASE_MATCH_TOLERANCE,
        "aligned": not problems,
        "problems": problems,
        "requirement": (
            "phase-match the arms against a MEASURED compaction cycle. There is no settled state "
            "to wait for: three matched cycles on an unchanged subject never flattened"
        ),
    }


STATIONARITY_BINS = 8
# A metric whose bins move one way in at least this share of adjacent steps is trending, not
# noisy. 7 of 7 adjacent steps in one direction has probability 2 * 0.5**7 = 1.6% under a random
# walk of signs, so 0.85 is a deliberate, statable threshold rather than a tuned one.
MONOTONE_SHARE = 0.85
# Below this relative spread a trend is real but too small to distort a comparison.
DRIFT_RELATIVE_RANGE = 0.10
# A NOISE FLOOR, and it is load-bearing rather than defensive. A numerically constant metric does
# not produce zero variance: the same rate recomputed per block differs in the last bits, giving a
# variance around 1e-32, a minimum detectable effect around 1e-16, and a confident "CHANGED"
# verdict on a difference of 3.5e-16. No resource quantity here is resolved to one part in a
# billion, so a difference under this fraction of the magnitude being compared is not a
# difference, whatever the t-statistic says.
NEGLIGIBLE_RELATIVE_DIFFERENCE = 1e-9


def stationarity(samples: Sequence[dict], window: Window, bins: int = STATIONARITY_BINS) -> dict:
    """Ask whether each metric is stationary across the window BEFORE anything is compared.

    This exists because the recorded baseline is not stationary and a naive A/B would blame the
    change for it. Binning the pre-change window into eighths shows `transactions` falling
    monotonically from 1.617 to 0.620 per second -- a 2.6x decay with every one of seven adjacent
    steps in the same direction -- while `statsd_cpu` stays flat. Differencing a post-change mean
    against a baseline mean that is still decaying attributes the decay to the change.

    Three verdicts. `drifting` means the bins move consistently one way and the spread is large
    enough to matter. `transient` means the spread is large but the direction is not consistent.
    `stationary` means neither.

    **Do NOT read `transient` as a warm-up that settles.** Measured over three recovery cycles,
    nothing settles inside a ~125-minute cycle, and `block_write_bytes` spreads 18%
    cycle-to-cycle at matched phase. **A `transient` verdict on a disk-bytes metric means the
    comparison is not safe, not that it will become safe if you wait.** An earlier version of this
    docstring said the opposite -- that `block_write_bytes` drops over the first quarter and then
    flattens -- and that sentence steered the reader toward treating real drift as benign.
    """

    if bins < 3:
        raise ValueError("stationarity needs at least three bins to judge a direction")
    width = window.seconds / bins
    grouped = blocks(samples, window, width)
    if len(grouped) < bins:
        return {"bins": len(grouped), "verdict": {}, "note": "too few bins to judge"}
    report = {}
    for metric in FAST_LANE_METRICS:
        series = [block_value(metric, block) for block in grouped]
        mean = statistics.fmean(series)
        if mean == 0.0:
            report[metric.name] = {"verdict": "stationary", "note": "series is zero throughout"}
            continue
        steps = [series[i + 1] - series[i] for i in range(len(series) - 1)]
        rising = sum(1 for step in steps if step > 0)
        falling = sum(1 for step in steps if step < 0)
        monotone_share = max(rising, falling) / len(steps)
        relative_range = (max(series) - min(series)) / mean
        # DIRECTION IS TESTED BEFORE MAGNITUDE, and the order matters. A database that only
        # grows moves 2.86% across two hours -- under the relative-range gate -- yet every
        # adjacent bin steps the same way, so it genuinely differs between any two halves.
        # Judging magnitude first called that stationary and then reported the difference it
        # necessarily produces as a false positive.
        if monotone_share >= MONOTONE_SHARE:
            verdict = "drifting"
        elif relative_range >= DRIFT_RELATIVE_RANGE:
            verdict = "transient"
        else:
            verdict = "stationary"
        report[metric.name] = {
            "verdict": verdict,
            "first_bin": series[0], "last_bin": series[-1],
            "relative_range": relative_range,
            "monotone_share": monotone_share,
            "direction": "falling" if falling > rising else "rising",
        }
    return {"bins": len(grouped), "bin_seconds": width, "verdict": report}


def family_freshness(acquisitions: Sequence[dict], window: Window) -> dict:
    """UI sample freshness, per family, from the slow lane's own cadence measurement.

    `per_family_cadence.mean_gap_seconds` IS sample freshness: the mean interval between
    consecutive distinct `observed_at` values a family produced. Freshness is what a batched
    commit is accused of degrading, so it is read per family rather than pooled -- pooling would
    let `service_load`, at roughly 80.69% of rows, hide a regression in `cpu`.
    """

    inside = [
        record for record in acquisitions
        if window.contains(float(record["finished_epoch"]))
    ]
    by_family: dict[str, list[float]] = {}
    for record in inside:
        for entry in record["per_family_cadence"]:
            by_family.setdefault(entry["family"], []).append(float(entry["mean_gap_seconds"]))
    return {
        "acquisitions": len(inside),
        "families": {
            family: {
                "mean_gap_seconds": statistics.fmean(gaps),
                "samples": len(gaps),
                "min": min(gaps),
                "max": max(gaps),
            }
            for family, gaps in sorted(by_family.items())
        },
    }


def reclaimable_ratio(acquisitions: Sequence[dict], window: Window) -> dict:
    """Line 334's reclaimable-page ratio, read from the slow lane's dbstat acquisition."""

    values = [
        float(record["reclaimable_ratio_dbstat"])
        for record in acquisitions
        if window.contains(float(record["finished_epoch"]))
        and record["reclaimable_ratio_dbstat"] is not None
    ]
    if not values:
        return {"samples": 0, "mean": None, "min": None, "max": None}
    return {
        "samples": len(values), "mean": statistics.fmean(values),
        "min": min(values), "max": max(values),
    }


def respawn_count(samples: Sequence[dict]) -> int:
    """How many statsd respawns a window spans, so a reader can judge the `_cum_` correction."""

    indexes = {int(sample["subject_epoch_index"]) for sample in samples}
    return max(indexes) - min(indexes)


@dataclass(frozen=True)
class Lane:
    """One arm's recorded files."""

    label: str
    fast: tuple[dict, ...]
    events: tuple[dict, ...]
    vacuum: tuple[dict, ...]
    acquisitions: tuple[dict, ...]


def load_lane(directory: Path, label: str, fast_name: str) -> Lane:
    raw = directory / "raw"
    return Lane(
        label=label,
        fast=read_jsonl(raw / fast_name),
        events=read_jsonl(raw / f"{Path(fast_name).stem}.events.jsonl"),
        vacuum=read_jsonl(raw / "vacuum_events.jsonl"),
        acquisitions=read_jsonl(raw / "acquisitions.jsonl"),
    )


def compare_lanes(
    baseline: Lane, baseline_window: Window,
    candidate: Lane, candidate_window: Window,
    block_seconds: float,
) -> dict:
    """The whole comparison: nine metrics, VACUUM attribution, freshness, and the resolutions."""

    base_samples = samples_in(baseline.fast, baseline_window)
    cand_samples = samples_in(candidate.fast, candidate_window)
    base_vacuums_early = completed_vacuums(baseline.vacuum, Window("all", 0.0, 2e9))
    cand_vacuums_early = completed_vacuums(candidate.vacuum, Window("all", 0.0, 2e9))
    del base_vacuums_early, cand_vacuums_early
    base_spans = (baseline_window,)
    cand_spans = (candidate_window,)
    base_blocks = blocks(base_samples, baseline_window, block_seconds)
    cand_blocks = blocks(cand_samples, candidate_window, block_seconds)

    base_vacuums = completed_vacuums(baseline.vacuum, baseline_window)
    cand_vacuums = completed_vacuums(candidate.vacuum, candidate_window)
    alignment = vacuum_alignment(base_vacuums, baseline_window, cand_vacuums, candidate_window)

    underpowered = []
    for label, group in ((baseline.label, base_blocks), (candidate.label, cand_blocks)):
        if len(group) < MIN_BLOCKS_PER_ARM:
            underpowered.append(
                f"{label}: {len(group)} blocks of {block_seconds:.0f}s, below the "
                f"{MIN_BLOCKS_PER_ARM} this reports p-values at"
            )

    # "This window cannot be compared" is an OUTCOME with a reason, not an exception. Raising here
    # made the retired steady-state selection traceback on the recorded window, where a compaction's
    # recovery swallows the whole first half and leaves it with zero blocks -- which is a true and
    # useful answer that a stack trace buries.
    if len(base_blocks) < 2 or len(cand_blocks) < 2:
        return {
            "refused": (
                f"not enough blocks to compare: baseline {len(base_blocks)}, candidate "
                f"{len(cand_blocks)}, at {block_seconds:.0f}s blocks"
            ),
            "vacuum_alignment": alignment, "block_seconds": block_seconds,
            "baseline_blocks": len(base_blocks), "candidate_blocks": len(cand_blocks),
            "comparisons": [], "significant_metrics": [], "underpowered": underpowered,
        }

    comparisons = [
        compare_series(
            metric,
            [block_value(metric, block) for block in base_blocks],
            [block_value(metric, block) for block in cand_blocks],
        ).as_dict()
        for metric in FAST_LANE_METRICS
    ]
    return {
        "vacuum_alignment": alignment,
        "baseline": {
            "label": baseline.label, "window_pt": baseline_window.label,
            "start_epoch": baseline_window.start_epoch, "end_epoch": baseline_window.end_epoch,
            "window_seconds": baseline_window.seconds,
            "samples": len(base_samples), "blocks": len(base_blocks),
            "statsd_respawns": respawn_count(base_samples),
            "wal_resets": sum(1 for e in baseline.events if e["kind"] == "wal_reset"
                              and baseline_window.contains(float(e["epoch"]))),
            "vacuum": vacuum_write_bytes(base_samples, baseline.vacuum, baseline_window),
            "freshness": family_freshness(baseline.acquisitions, baseline_window),
            "reclaimable_ratio": reclaimable_ratio(baseline.acquisitions, baseline_window),
            "stationarity": stationarity(base_samples, baseline_window),
            "completed_vacuums": list(base_vacuums),
        },
        "candidate": {
            "label": candidate.label, "window_pt": candidate_window.label,
            "start_epoch": candidate_window.start_epoch, "end_epoch": candidate_window.end_epoch,
            "window_seconds": candidate_window.seconds,
            "samples": len(cand_samples), "blocks": len(cand_blocks),
            "statsd_respawns": respawn_count(cand_samples),
            "wal_resets": sum(1 for e in candidate.events if e["kind"] == "wal_reset"
                              and candidate_window.contains(float(e["epoch"]))),
            "vacuum": vacuum_write_bytes(cand_samples, candidate.vacuum, candidate_window),
            "freshness": family_freshness(candidate.acquisitions, candidate_window),
            "reclaimable_ratio": reclaimable_ratio(candidate.acquisitions, candidate_window),
            "stationarity": stationarity(cand_samples, candidate_window),
            "completed_vacuums": list(cand_vacuums),
        },
        "block_seconds": block_seconds,
        "blocked_spans": {
            "baseline": [{"start_epoch": w.start_epoch, "end_epoch": w.end_epoch} for w in base_spans],
            "candidate": [{"start_epoch": w.start_epoch, "end_epoch": w.end_epoch} for w in cand_spans],
        },
        "underpowered": underpowered,
        "comparisons": comparisons,
        "significant_metrics": [c["metric"] for c in comparisons if c["significant"]],
    }


def self_test(lane: Lane, window: Window, block_seconds: float) -> dict:
    """Split one recorded window in half and compare the halves as a fake A/B.

    Both halves are the same condition, so a correct harness reports NO significant difference.
    A significant result here is a defect in the harness, not a finding about statsd -- with one
    honest exception this reports rather than hides: a level metric that genuinely drifts across
    the window, such as a database that only grows, WILL differ between halves and should.
    """

    midpoint = window.start_epoch + window.seconds / 2.0
    first = Window(f"{window.label} first half", window.start_epoch, midpoint)
    second = Window(f"{window.label} second half", midpoint, window.end_epoch)
    # Relabel the second arm so an underpowered warning names the half it describes; passing the
    # same Lane twice would otherwise print "baseline" for both and hide which half is short.
    second_lane = Lane(f"{lane.label} second half", lane.fast, lane.events, lane.vacuum, lane.acquisitions)
    result = compare_lanes(
        Lane(f"{lane.label} first half", lane.fast, lane.events, lane.vacuum, lane.acquisitions),
        first, second_lane, second, block_seconds,
    )
    # A metric the stationarity check independently calls non-stationary SHOULD differ between
    # halves -- detecting a real trend is the harness working, not failing. The self-test fails
    # only on a metric that is stationary across the window and still reads as changed, which is
    # the false positive autocorrelation would produce.
    if "refused" in result:
        result["self_test"] = {"passed": None, "refused": result["refused"],
                               "significant": [], "explained_by_real_drift": [],
                               "false_positives": []}
        return result
    trends = stationarity(samples_in(lane.fast, window), window)["verdict"]
    false_positives = [
        metric for metric in result["significant_metrics"]
        if trends.get(metric, {}).get("verdict", "stationary") == "stationary"
    ]
    explained = [
        {"metric": metric, "verdict": trends[metric]["verdict"],
         "first_bin": trends[metric]["first_bin"], "last_bin": trends[metric]["last_bin"]}
        for metric in result["significant_metrics"] if metric in trends
        and trends[metric].get("verdict") != "stationary"
    ]
    result["self_test"] = {
        "expectation": (
            "two halves of one condition differ only where the window is genuinely non-stationary"
        ),
        "significant": result["significant_metrics"],
        "explained_by_real_drift": explained,
        "false_positives": false_positives,
        "passed": not false_positives,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--baseline-fast", default="fast5.jsonl")
    parser.add_argument("--baseline-start", type=float, required=True, help="epoch seconds")
    parser.add_argument("--baseline-end", type=float, required=True, help="epoch seconds")
    parser.add_argument("--candidate-dir", type=Path, help="omit with --self-test")
    parser.add_argument("--candidate-fast", default="fast5.jsonl")
    parser.add_argument("--candidate-start", type=float)
    parser.add_argument("--candidate-end", type=float)
    parser.add_argument("--block-seconds", type=float, default=DEFAULT_BLOCK_SECONDS)
    parser.add_argument("--self-test", action="store_true",
                        help="split the baseline window in half and compare it against itself")
    parser.add_argument("--out", type=Path, help="write the full result as JSON")
    arguments = parser.parse_args()

    baseline = load_lane(arguments.baseline_dir, "baseline", arguments.baseline_fast)
    window = Window("baseline", arguments.baseline_start, arguments.baseline_end)

    if arguments.self_test:
        result = self_test(baseline, window, arguments.block_seconds)
    else:
        if arguments.candidate_dir is None or arguments.candidate_start is None or arguments.candidate_end is None:
            parser.error("a real comparison needs --candidate-dir, --candidate-start and --candidate-end")
        candidate = load_lane(arguments.candidate_dir, "candidate", arguments.candidate_fast)
        result = compare_lanes(
            baseline, window, candidate,
            Window("candidate", arguments.candidate_start, arguments.candidate_end),
            arguments.block_seconds,
        )

    print(f"block seconds        : {result['block_seconds']:.0f}")
    if "refused" in result:
        print(f"REFUSED              : {result['refused']}")
        alignment = result["vacuum_alignment"]
        for problem in alignment["problems"]:
            print(f"                       {problem}")
        if arguments.out is not None:
            arguments.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {arguments.out}")
        return 0
    for side in ("baseline", "candidate"):
        info = result[side]
        print(f"{side:<21}: {info['samples']} samples, {info['blocks']} blocks, "
              f"{info['statsd_respawns']} respawns, {info['wal_resets']} WAL resets, "
              f"{info['vacuum']['events']} VACUUMs ({info['vacuum']['total_bytes'] / 1e6:.1f} MB attributed)")
    alignment = result["vacuum_alignment"]
    for side in ("baseline", "candidate"):
        seconds = alignment[f"{side}_seconds_since_vacuum"]
        phase = alignment[f"{side}_phase"]
        text = "unknown" if seconds is None else f"{seconds / 60:.1f} min"
        phase_text = "phase unknown" if phase is None else f"phase {phase:.3f} of its cycle"
        print(f"{side + ' since VACUUM':<21}: {text}  ({phase_text})")
    if not alignment["aligned"]:
        for problem in alignment["problems"]:
            print(f"NOT VACUUM-ALIGNED   : {problem}")
        print(f"                       {alignment['requirement']}")
    for warning in result["underpowered"]:
        print(f"UNDERPOWERED         : {warning}")
    print()
    print(f"{'metric':<20} {'baseline':>14} {'candidate':>14} {'change %':>10} {'MDE %':>9} {'p':>9}  verdict")
    for row in result["comparisons"]:
        change = "n/a" if row["percent_change"] is None else f"{row['percent_change']:+.2f}"
        mde = "n/a" if row["minimum_detectable_percent"] is None else f"{row['minimum_detectable_percent']:.2f}"
        p_text = "refused" if row["p_value"] is None else f"{row['p_value']:.4f}"
        verdict = "CHANGED" if row["significant"] else "no change detected"
        print(f"{row['metric']:<20} {row['baseline_mean']:>14.4g} {row['candidate_mean']:>14.4g} "
              f"{change:>10} {mde:>9} {p_text:>9}  {verdict}")
    print()
    print("stationarity of the baseline window (judge every difference against this):")
    for name, verdict in sorted(result["baseline"]["stationarity"]["verdict"].items()):
        if verdict["verdict"] != "stationary":
            print(f"  {name:<20} {verdict['verdict'].upper():<10} "
                  f"{verdict['first_bin']:.4g} -> {verdict['last_bin']:.4g} "
                  f"({verdict['direction']}, monotone {verdict['monotone_share']:.0%})")
    if "self_test" in result:
        print()
        print(f"SELF-TEST passed={result['self_test']['passed']}")
        print(f"  explained by real drift: {[e['metric'] for e in result['self_test']['explained_by_real_drift']]}")
        print(f"  FALSE POSITIVES        : {result['self_test']['false_positives']}")
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nwrote {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

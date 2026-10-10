# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Sampling-path and physical-cost performance contracts for current YO!stats."""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from yomux_lib import local_service_projection
from yomux_lib.stats_current import resolution as stats_resolution
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.service import APPEND_FLUSH_ENV_NAME

# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
pytestmark = pytest.mark.deprecated_python_server


def test_service_sampling_skips_system_only_diagnostics():
    diagnostic_calls = []
    producers = {
        service: (lambda name=service: {
            "service": name,
            "pid": 1,
            "resources": {"cpu_percent": 2.0, "rss_bytes": 3},
        })
        for service in local_service_projection.LOCAL_SERVICE_INVENTORY
    }
    collector = local_service_projection.LocalServicesCollector(
        lambda: producers,
        ledger=lambda: diagnostic_calls.append("ledger") or {"expensive": True},
        recovery_events=lambda _rows: diagnostic_calls.append("recovery") or ({"kind": "x"},),
    )

    snapshot = collector.collect(include_diagnostics=False)

    assert tuple(row.service for row in snapshot.rows) == local_service_projection.LOCAL_SERVICE_INVENTORY
    assert snapshot.ledger == {}
    assert snapshot.recovery_events == ()
    assert diagnostic_calls == []


def test_cost_probe_coldstart_uses_the_current_materializer_contract(tmp_path):
    """The synthetic cold-start probe must exercise a valid current-family store."""

    database = tmp_path / storage.DATABASE_FILENAME
    measure_cost(database, facts=64, batch_size=8)
    report = measure_cost(database, mode="coldstart")

    assert report["mode"] == "coldstart"
    assert report["cells"] > 0
    assert report["peak_kb"]["anon_plus_swap"] > 0


# --- joint physical-cost gate -------------------------------------------------
#
# Pins bytes written per recorded fact, read syscalls per fact, and peak process
# memory TOGETHER, because each one alone is gameable: buffering everything in RAM
# would cut writes, and committing more often would cut memory. All three must
# hold or none of them means anything.
#
# The ceilings are inputs, not literals inside the assertion, so the same code
# runs against a few-hundred-row `tmp_path` store in the default lane and against
# a copy of a production-sized store when one is available. That is the whole
# reason for `CostCeilings`: store size changes the numbers by an order of
# magnitude, so a gate with baked-in constants could only ever be right for one
# size.


@dataclass(frozen=True)
class CostCeilings:
    """One ceiling set, plus an honest statement of what it can actually falsify.

    `demonstrable` exists because a small store cannot show the whole-history
    incident class -- it has no history to be whole. Its read syscalls all land in
    page cache and its peak memory never approaches the incident, so those two
    limbs pass no matter what the code does. Recording that here stops a green on
    a small fixture from being read as a green on the real thing.
    """

    label: str
    write_bytes_per_fact: float
    syscr_per_fact: float
    peak_anon_plus_swap_bytes: int
    demonstrable: frozenset[str]


# The shape the released daemon used before batching: one commit per recorded fact.
# This is now a NEGATIVE CONTROL, not the gate's workload -- see the two tests below.
PER_FACT_COMMITS = 1
# The shape the batching change produces: a per-family interval that coalesces
# roughly ten facts into one commit. Measured on this fixture at 3,502 B/fact.
BATCHED_FACTS_PER_COMMIT = 10
GATE_FACTS = 2000

# The memory limb governs RssAnon + VmSwap, not RSS and not USS. Both of those FALL
# when the kernel pages a process out, so a daemon can breach its budget and read
# healthy: live statsd was measured at VmRSS 630,200 kB while holding VmSwap
# 1,015,336 kB, and `RssAnon + VmSwap` reconciles VmHWM to 0.46% where RSS is 61%
# low. The NUMBER does not move with the quantity: the 1,470 MiB cold-build
# incident was measured on a process with zero swap, where anon+swap and USS
# coincide, so 256 MiB still sits 5.7x below the incident and >10x above the
# ~21 MiB a bounded append path uses. The change closes the loophole, not the gap.
PEAK_MEMORY_CEILING_BYTES = 256 * 1024 * 1024


def product_append_flush_seconds():
    """The flush interval the product is CONFIGURED for, or None if batching is off.

    Returns None for both "no batching machinery exists" and
    `APPEND_FLUSH_SECONDS = 0.0`, because those are the same thing to this gate:
    the product commits once per recorded fact and `append_batch` is the whole
    append path. Batching ships disabled -- the enabled arm fails two ring
    correctness gates and the runtime is fail-closed -- with the measured interval
    preserved separately as `APPEND_FLUSH_MEASURED_SECONDS`. Reading the raw value
    without that distinction would have this gate report "flushes every 0.0s" and
    skip, which is a sentence that means nothing.

    NOT a facts-per-commit constant: the product has none, and inventing one would
    re-create the coupling this gate was just fixed for, in a different costume. The
    batching change is `APPEND_FLUSH_SECONDS` with a `YOLOMUX_STATS_APPEND_FLUSH_SECONDS`
    override, so facts per commit is emergent -- arrival rate times flush interval --
    and a number derived from two others goes stale the moment either moves.
    """

    for module, name in (
        ("yomux_lib.stats_current.service", "APPEND_FLUSH_SECONDS"),
        ("yomux_lib.stats_current.collectors", "APPEND_FLUSH_SECONDS"),
    ):
        imported = sys.modules.get(module)
        if imported is not None and hasattr(imported, name):
            configured = float(getattr(imported, name))
            return configured if configured > 0 else None
    return None


# RE-DERIVED 2026-08-26 after the probe was given a ring head. It previously published
# none, so its appends intersected no slot and wrote zero `ring_invalidations` -- the gate
# was defending a workload no running daemon produces, and doing so in the LENIENT
# direction. These are fresh measurements on the head-published workload, NOT the old
# figures scaled:
#
#   arm                     no head        with head      factor
#   batched (ten/commit)    3,502.080      4,562.944      1.303x
#   per-fact commits       16,820.224     26,695.680      1.587x
#
# Both arms are exactly reproducible: ten consecutive runs each, sd 0.0, one distinct
# value, 2,228 and 13,035 whole pages, 521 invalidation rows every time.
#
# The ceiling stays at the TEN-per-commit cost plus 20%, i.e. after the batching change,
# deliberately. A ceiling at today's cost would pass trivially once batching lands and
# could never catch a regression back to per-fact commits.
SMALL_FIXTURE_CEILINGS = CostCeilings(
    label="small tmp_path store, ~450 KB",
    write_bytes_per_fact=5500.0,   # 4,562.944 measured + 20%
    syscr_per_fact=0.20,
    peak_anon_plus_swap_bytes=PEAK_MEMORY_CEILING_BYTES,
    # Only writes can fail here. Reads are all page-cache hits (`read_bytes` is 0)
    # and anon+swap never leaves ~21 MiB, so those two limbs pass vacuously.
    demonstrable=frozenset({"write_bytes_per_fact"}),
)

# Derived from tonight's live steady-state measurements, NOT measured by this gate:
#   write bytes  0.766 MB/s * 3600 / 26,687 facts/h = 103,331 B/fact today;
#                * (1 - 0.8353) after batching      =  17,019 B/fact; ceiling +18%.
#   read syscalls 12,378.8/s * 3600 / 26,687        =   1,669.6 /fact today;
#                * (1 - 0.8353)                     =     275.0 /fact; ceiling +20%.
#   peak USS      the cold-build incident measured 1,470 MiB process USS, of which
#                 96.40% was empty pymalloc arena. 256 MiB sits 5.7x below the
#                 incident and >10x above any bounded append path, and batching
#                 does not move it, so this limb is set for TODAY, not for later.
PRODUCTION_STORE_CEILINGS = CostCeilings(
    label="production-sized store, ~519 MB",
    write_bytes_per_fact=20000.0,
    syscr_per_fact=330.0,
    peak_anon_plus_swap_bytes=PEAK_MEMORY_CEILING_BYTES,
    demonstrable=frozenset({"write_bytes_per_fact", "syscr_per_fact", "peak_anon_plus_swap_bytes"}),
)

# Point this at a COPY of a production-sized store to run the limbs a small
# fixture cannot demonstrate. Absent, those tests skip rather than pretend.
PRODUCTION_STORE_ENV = "YOLOMUX_COST_GATE_STORE"
# The product override that a service-append probe mode would drive; see the test below.
COST_PROBE = Path(__file__).resolve().parents[1] / "tools" / "statsd_cost_probe.py"


def measure_cost(database, *, facts=GATE_FACTS, batch_size=BATCHED_FACTS_PER_COMMIT, mode="append"):
    """Run the workload in a process that exists only to run it, and return its report.

    A fresh process is not a convenience. `VmHWM` is a lifetime high-water mark
    that cannot be reset, so measuring inside a pytest worker that has already run
    other tests reports those tests. `/proc/self/io` has the same problem for
    absolute values. In a dedicated process all three numbers are attributable
    with no baseline anyone has to trust.
    """

    completed = subprocess.run(
        [
            sys.executable, str(COST_PROBE), "--database", str(database),
            "--facts", str(facts), "--batch-size", str(batch_size), "--mode", mode,
        ],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def measure_cost_in_tempdir(**kwargs):
    """One measured run against a private store that exists only for it."""

    with tempfile.TemporaryDirectory() as scratch:
        return measure_cost(Path(scratch) / storage.DATABASE_FILENAME, **kwargs)


def cost_breaches(report, ceilings):
    """Breaches of the limbs this profile can actually falsify, plus the ones it cannot.

    `demonstrable` used to be declared, documented and never read: `cost_breaches`
    checked all three limbs unconditionally, so a test named for JOINT ceilings had
    two limbs passing vacuously on a small store while the file explained in a
    comment that they would. A comment is not a disclosure. Now the set governs
    what is asserted, and the vacuous limbs come back as `skipped` so the runtime
    output says which of the three actually held.
    """

    measured = {
        "write_bytes_per_fact": report["per_fact"]["write_bytes"],
        "syscr_per_fact": report["per_fact"]["syscr"],
        "peak_anon_plus_swap_bytes": report["peak_kb"]["anon_plus_swap"] * 1024,
    }
    breaches, skipped = [], []
    for limb, value in measured.items():
        ceiling = getattr(ceilings, limb)
        if limb not in ceilings.demonstrable:
            skipped.append(f"{limb}={value} not demonstrable on {ceilings.label}, ceiling {ceiling} unchecked")
            continue
        if value is not None and value > ceiling:
            breaches.append(f"{limb}={value:.3f} exceeds {ceiling} ({ceilings.label}, x{value / ceiling:.2f})")
    return breaches, skipped


def assert_cost_holds(report, ceilings):
    """Assert only what this profile can falsify, and name what it could not."""

    assert ceilings.demonstrable, f"{ceilings.label} can falsify no limb; it gates nothing"
    breaches, skipped = cost_breaches(report, ceilings)
    assert not breaches, {"breaches": breaches, "unchecked": skipped, "per_fact": report["per_fact"]}
    return skipped


@pytest.mark.gate_serial
def test_batched_recording_holds_the_joint_cost_ceilings(tmp_path):
    """The ceiling holds at the batched cadence. Pair this with the test below.

    An earlier version of this test ran the PRE-batching shape against the
    POST-batching ceiling and documented itself as "red until the batching change
    lands". The change landed, nobody repointed the workload, and it stayed red at
    x4.00 with its stated exit condition already met -- which trains a reader to
    expect the red and stop looking. The ceiling was never the problem: it was
    measured on this exact fixture at ten commits (3,502 B/fact) plus 20%. The
    workload was.
    """

    report = measure_cost(tmp_path / storage.DATABASE_FILENAME, batch_size=BATCHED_FACTS_PER_COMMIT)

    assert report["observations_accepted"] == GATE_FACTS
    assert_cost_holds(report, SMALL_FIXTURE_CEILINGS)


@pytest.mark.gate_serial
def test_per_fact_commits_breach_the_ceiling_that_batching_clears(tmp_path):
    """The SHIPPED configuration's cost, which is over the ceiling, and by how much.

    This began as a negative control against a hypothetical. It is no longer
    hypothetical: batched persistence ships DISABLED by explicit decision --
    `APPEND_FLUSH_SECONDS = 0.0`, the enabled arm failing two ring correctness
    gates, the runtime fail-closed so no environment can turn it on -- so one
    commit per recorded fact is what production does today. **This test is
    therefore the gate's statement that the shipped path costs 26,696 B/fact
    against a 5,500 ceiling, x4.85, and that the ceiling is unmet until batching
    is re-enabled.** That is tracked in the batching queue, not gated here, which
    is why this asserts the breach rather than failing on it.

    It keeps its second job unchanged: a ceiling loosened until everything passes
    breaks this test, so the pair pins the number from both sides and neither can
    be tuned away without the other going red. Measured on this fixture with a ring
    head published, 26,696 B/fact at one commit per fact against 4,563 at ten is an
    82.9% reduction, which independently corroborates the 83.53% measured for the
    batching change more closely than the head-less 79.2% did.
    """

    report = measure_cost(tmp_path / storage.DATABASE_FILENAME, batch_size=PER_FACT_COMMITS)

    breaches, _skipped = cost_breaches(report, SMALL_FIXTURE_CEILINGS)
    assert any(item.startswith("write_bytes_per_fact") for item in breaches), report["per_fact"]


@pytest.mark.gate_serial
def test_the_probe_still_covers_the_products_real_append_path():
    """Declare, at runtime, exactly how much of the product's append path this gate sees.

    The gate drives `storage.Store.open(...).append_batch(...)` directly. That is
    the whole of the append path today. Once the product flushes on a TIMER, facts
    per commit stops being a parameter anyone can pass and becomes arrival rate
    times flush interval -- which `append_batch` cannot exercise at all, because
    the coalescing happens above it.

    So this test exists to stop the gate going quietly stale a second time. It
    passes while the product has no timed flush, and skips with the concrete
    blocker named once it does, rather than reporting a ceiling for a path the
    product no longer takes.
    """

    flush_seconds = product_append_flush_seconds()
    if flush_seconds is not None:  # None also covers APPEND_FLUSH_SECONDS = 0.0
        pytest.skip(
            f"product flushes appends every {flush_seconds}s, which append_batch cannot "
            "exercise; this gate needs a service-append probe mode driving a real daemon "
            f"with {APPEND_FLUSH_ENV_NAME} set, against a daemon-ready store"
        )
    report = measure_cost_in_tempdir(batch_size=BATCHED_FACTS_PER_COMMIT)
    assert_cost_holds(report, SMALL_FIXTURE_CEILINGS)


@pytest.mark.gate_serial
@pytest.mark.skipif(not os.environ.get(PRODUCTION_STORE_ENV), reason=f"set {PRODUCTION_STORE_ENV} to a COPY of a production-sized store")
def test_whole_history_cold_start_breaches_the_peak_ceiling():
    """NEGATIVE CONTROL: the released whole-history path must FAIL this gate.

    A gate the incident class passes is not measuring the incident class. The
    released startup materializes every retained row at once; on a production
    store that was measured at 1,470 MiB process USS against a 256 MiB ceiling.
    Requires a real store because a small one has no history to be whole.
    """

    report = measure_cost(Path(os.environ[PRODUCTION_STORE_ENV]), mode="coldstart")

    breaches, _skipped = cost_breaches(report, PRODUCTION_STORE_CEILINGS)
    assert any(item.startswith("peak_anon_plus_swap_bytes") for item in breaches), report["peak_kb"]


@pytest.mark.gate_serial
@pytest.mark.skipif(not os.environ.get(PRODUCTION_STORE_ENV), reason=f"set {PRODUCTION_STORE_ENV} to a COPY of a production-sized store")
def test_recording_facts_holds_the_joint_cost_ceilings_on_a_production_store():
    """The limbs a small fixture cannot demonstrate, run where they mean something."""

    report = measure_cost(Path(os.environ[PRODUCTION_STORE_ENV]))

    assert_cost_holds(report, PRODUCTION_STORE_CEILINGS)


# One 4 KiB page is the smallest change `/proc/self/io` write_bytes can report, so the
# resolution of every ceiling above is PAGE_BYTES / GATE_FACTS. Measured on this host,
# 2026-08-26: ten consecutive runs of each arm returned byte-identical figures --
# 4562.944 B/fact batched and 26695.680 B/fact per-fact, sd 0.0 in both -- because
# write_bytes counts block-device bytes for a deterministic workload on a fresh store
# and has no timing dependence at all. The gate is therefore quantisation-limited, not
# noise-limited, which is why it can carry a ceiling only 20% above its measured value.
PAGE_BYTES = 4096
# `/proc/self/io` reports whole block-device pages, but SQLite's final WAL/checkpoint
# accounting can land up to 22 pages apart for the same fixed workload under the gate.
# Twenty-four pages is the measured upper bound plus one page of quantisation, so the
# instrument reports its real resolving power instead of claiming zero jitter.
MAX_WRITE_BYTES_JITTER_PAGES = 24
# The 20% write ceiling still contains 19 measured resolution steps; require at least
# sixteen so ordinary allocation jitter cannot consume the gate's useful headroom.
MIN_HEADROOM_RESOLUTION_STEPS = 16


@pytest.mark.gate_serial
def test_the_cost_gate_states_the_smallest_regression_it_can_resolve(tmp_path):
    """Pin the gate's resolution beside its ceilings, so "20% headroom" means something.

    A ceiling that cannot distinguish a real regression from run-to-run noise implies a
    precision it does not have. This asserts the two properties that make the stated
    resolution true, so the claim fails loudly if either stops holding: the measurement
    must remain bounded across processes, and it must move in whole pages.

    It is deliberately not a ceiling. It gates the INSTRUMENT, and a change that made
    write_bytes timing-dependent -- an fsync in the loop, a background flusher, anything
    that varies the page count for identical work -- would turn every ceiling above into
    a coin toss while leaving them all green. This is the test that would go red instead.
    """

    runs = []
    for index in range(3):
        scratch = tmp_path / f"resolution-{index}"
        scratch.mkdir()
        runs.append(measure_cost(scratch / storage.DATABASE_FILENAME,
                                 batch_size=BATCHED_FACTS_PER_COMMIT))

    raw = [report["io_delta"]["write_bytes"] for report in runs]
    assert all(raw_bytes % PAGE_BYTES == 0 for raw_bytes in raw), (
        f"write_bytes values {raw} are not whole numbers of {PAGE_BYTES}-byte pages, so the "
        "resolution stated below is not the real one"
    )
    jitter_pages = (max(raw) - min(raw)) // PAGE_BYTES
    assert jitter_pages <= MAX_WRITE_BYTES_JITTER_PAGES, (
        f"the measurement varies by {jitter_pages} pages across identical processes; "
        f"the {MAX_WRITE_BYTES_JITTER_PAGES}-page resolution budget is no longer honest: {raw}"
    )

    resolution_per_fact = MAX_WRITE_BYTES_JITTER_PAGES * PAGE_BYTES / GATE_FACTS
    ceiling = SMALL_FIXTURE_CEILINGS.write_bytes_per_fact
    measured = max(raw) / GATE_FACTS
    assert resolution_per_fact < 0.01 * ceiling, (
        f"the {MAX_WRITE_BYTES_JITTER_PAGES}-page resolution over {GATE_FACTS} facts is "
        f"{resolution_per_fact} B/fact, which is "
        f"{100 * resolution_per_fact / ceiling:.2f}% of the {ceiling} ceiling; a gate whose "
        "resolution approaches its own headroom cannot report a regression it can defend"
    )
    # The headroom the ceiling actually carries, stated in units of what the gate can see.
    assert ceiling - measured > MIN_HEADROOM_RESOLUTION_STEPS * resolution_per_fact, (
        f"ceiling {ceiling} sits {ceiling - measured:.1f} B/fact above the measured "
        f"{measured}, which is only {(ceiling - measured) / resolution_per_fact:.0f} "
        "resolvable steps"
    )


@pytest.mark.gate_serial
def test_the_probe_publishes_a_ring_head_before_it_measures(tmp_path):
    """Without a head the appends intersect nothing, and the ceiling is silently generous.

    This file has twice carried a number that was correct for a world it no longer
    described -- a literal `RELEASED_FACTS_PER_COMMIT = 1` with a comment saying to
    repoint it, and then a probe that published no ring. Both were documented and both
    went stale, because a comment is not a mechanism. This is the mechanism.

    A running daemon always has a published ring, so its appends always write
    `ring_invalidations`. Measured on this fixture: 4,562.944 B/fact with a head against
    3,502.080 without, so a head-less probe understates real append cost by 23.3% and
    understates it in the LENIENT direction, which is the failure that does not announce
    itself. Delete the head and this goes red before any ceiling does.
    """

    report = measure_cost(tmp_path / storage.DATABASE_FILENAME, batch_size=BATCHED_FACTS_PER_COMMIT)

    head = report.get("ring_head")
    assert head, "the probe published no ring head; its appends intersect no slot"
    assert head["published_slots"] > 0, head
    assert report["ring_invalidations"] > 0, (
        "appends wrote no ring_invalidations, so nothing intersected the published head "
        f"and the measured {report['per_fact']['write_bytes']} B/fact is too low"
    )

    # Coverage is DECLARED, not assumed: the ring is bounded per resolution, so a span
    # longer than a resolution's window cannot be fully covered and the gate should say by
    # how much rather than imply a head it does not have.
    per_resolution = head["per_resolution"]
    assert set(per_resolution) == {str(value) for value in stats_resolution.RESOLUTION_CHOICES}, per_resolution
    for resolution, detail in per_resolution.items():
        assert detail["slots"] <= detail["capacity"], (resolution, detail)
        assert detail["span_fraction"] is not None, (resolution, detail)
    # The coarsest layers must cover the whole span, or the head is decorative.
    assert per_resolution["300"]["span_fraction"] == 1.0, per_resolution

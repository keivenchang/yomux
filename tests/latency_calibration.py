# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Calibration, host qualification and fixed-ceiling verdicts for the exclusive certification phase.

One owner for three ideas that used to be fused or copied:

* the fixed independent browser work unit and its declared reference envelope,
* host resource qualification - may this box certify anything at all right now,
* the fixed-ceiling verdict - a ceiling is a product budget and is never scaled by a host.

Calibration decides WHETHER, and which statistic, may be certified. It never decides how slow the
product may be. A host outside the declared envelope produces ``NOT CERTIFIABLE`` with its raw
evidence; it never produces a skip that passes and never produces a multiplied ceiling.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import re
import sqlite3
from statistics import median
import time
import uuid
from typing import Any

import pytest


CALIBRATION_SAMPLE_COUNT = 7
# Two thresholds on ONE measurement, deliberately distinct, both owned here.
#
# CALIBRATION_REFERENCE_MS is the STRICTNESS selector: at or below it the renderer is at the
# recorded reference, so even a per-key maximum is a meaningful statistic. Its value is one vsync
# frame, because the probe crosses a requestAnimationFrame boundary and 16.7 ms is that floor - it
# says the fixed work unit fitted inside a single frame, not that the CPU ran at a given speed.
#
# CALIBRATION_ADMISSION_MS is the ADMISSION envelope: beyond it no verdict may be reached at all
# and the unit returns NOT CERTIFIABLE. It is 1.5 frames. Measured 2026-08-08 on keivenc-linux1
# across nine exclusive-phase runs (63 raw samples): quiet raw samples 15.4-19.4 ms, per-round p75
# 16.6-19.2 ms, so a frame-quantised probe routinely lands just past one frame and the reference
# value alone refused five of nine otherwise-qualified runs. No observed sample reached two frames
# (33.3 ms). The envelope therefore admits a host that spills partially into a second frame and
# refuses one that loses a whole frame.
CALIBRATION_REFERENCE_MS = 16.7
CALIBRATION_ADMISSION_MS = 25.0
CALIBRATION_REFERENCE_CONDITIONS = (
    "2026-08-02 keivenc-linux1; isolated test container; headless Chromium at 1200x700; "
    "maximum 16.7 ms across four quiet seven-sample p75 probes (16.6-16.7 ms); no synthetic renderer pressure. "
    "2026-08-08 re-measurement in the exclusive phase, same box and image, nine runs / 63 raw samples: "
    "raw 15.4-19.4 ms, per-round p75 16.6-19.2 ms, none at two frames; admission set to 1.5 frames"
)

# The literal every unqualified result must carry, in the phase runner and inside a test alike.
NOT_CERTIFIABLE = "NOT CERTIFIABLE"

# Both certification units are admitted by env flag. docker/run-tests.sh forwards only a fixed
# allowlist, so every name here must also appear in that allowlist or the node is silently skipped.
LATENCY_CERTIFICATION_ENV = "YOLOMUX_LATENCY_CERTIFICATION"
CHAT_LATENCY_CERTIFICATION_ENV = "YOLOMUX_CHAT_LATENCY_CERTIFICATION"
CERTIFICATION_ENV_NAMES = (LATENCY_CERTIFICATION_ENV, CHAT_LATENCY_CERTIFICATION_ENV)


class NotCertifiableError(AssertionError):
    """An unqualified host or an unrunnable unit. Never a skip: this reds whatever observes it."""

    def __init__(self, evidence: dict[str, Any]) -> None:
        self.evidence = evidence
        super().__init__(f"{NOT_CERTIFIABLE}: {json.dumps(evidence, sort_keys=True, default=str)}")


_CALIBRATION_SCRIPT = r"""
const done = arguments[arguments.length - 1];
const sampleCount = Number(arguments[0]);
const root = document.createElement('div');
root.id = 'yolomux-latency-calibration';
root.style.cssText = 'position:fixed;left:0;top:0;width:640px;height:360px;contain:strict;display:grid;grid-template-columns:repeat(12,1fr);background:#111;pointer-events:none';
const cells = [];
for (let index = 0; index < 96; index += 1) {
  const cell = document.createElement('span');
  cell.textContent = String(index);
  cell.style.cssText = 'display:block;height:12px;border:1px solid transparent;background:#222;opacity:.99';
  root.appendChild(cell);
  cells.push(cell);
}
document.body.appendChild(root);
let checksum = 0;
const samples = [];
const fixedWork = round => {
  let value = 0x9e3779b9 ^ round;
  for (let index = 0; index < 120000; index += 1) {
    value = Math.imul(value ^ index, 2654435761) >>> 0;
    value = ((value << 7) | (value >>> 25)) >>> 0;
  }
  for (let index = 0; index < cells.length; index += 1) {
    const cell = cells[index];
    const phase = (value + index + round) % 17;
    cell.style.transform = `translate3d(${phase}px,${phase % 5}px,0)`;
    cell.style.borderColor = `rgb(${phase * 11},${phase * 7},${phase * 3})`;
    cell.style.backgroundColor = `rgb(${phase * 3},${phase * 7},${phase * 11})`;
    checksum += cell.getBoundingClientRect().right;
  }
  checksum += root.getBoundingClientRect().height + value;
};
const sample = round => new Promise(resolve => {
  requestAnimationFrame(() => {
    const startedAt = performance.now();
    fixedWork(round);
    requestAnimationFrame(() => resolve(performance.now() - startedAt));
  });
});
(async () => {
  try {
    for (let round = 0; round < sampleCount; round += 1) samples.push(await sample(round));
    const ordered = samples.slice().sort((left, right) => left - right);
    const p75Index = Math.ceil(ordered.length * .75) - 1;
    done({
      calibrationNowMs: ordered[Math.max(0, p75Index)],
      samplesMs: samples,
      statistic: 'p75',
      checksum,
      workUnit: '120000 integer iterations + 96 visible style/layout/paint cells + one rAF boundary',
    });
  } catch (error) {
    done({error: String(error?.stack || error)});
  } finally {
    root.remove();
  }
})();
"""


def run_browser_latency_calibration(browser: Any, *, reset_page: bool = True) -> dict[str, Any]:
    """Measure the fixed independent work unit before any product page is loaded."""

    browser.set_window_size(1200, 700)
    if reset_page:
        browser.get("about:blank")
    result = browser.execute_async_script(_CALIBRATION_SCRIPT, CALIBRATION_SAMPLE_COUNT)
    assert isinstance(result, dict) and not result.get("error"), result
    samples = result.get("samplesMs")
    calibration_now = result.get("calibrationNowMs")
    assert isinstance(samples, list) and len(samples) == CALIBRATION_SAMPLE_COUNT, result
    assert all(isinstance(sample, (int, float)) and math.isfinite(sample) and sample > 0 for sample in samples), result
    assert isinstance(calibration_now, (int, float)) and math.isfinite(calibration_now) and calibration_now > 0, result
    return result


def start_independent_browser_pressure(browser: Any, *, busy_ms: float = 24.0) -> None:
    """Create renderer scheduling contention without changing the calibration work unit."""

    browser.execute_script(
        r"""
        const busyMs = Number(arguments[0]);
        window.__yolomuxCalibrationPressureActive = true;
        window.__yolomuxCalibrationPressureTicks = 0;
        const burn = () => {
          if (!window.__yolomuxCalibrationPressureActive) return;
          const deadline = performance.now() + busyMs;
          let value = 1;
          while (performance.now() < deadline) value = Math.imul(value + 17, 2654435761) >>> 0;
          window.__yolomuxCalibrationPressureChecksum = value;
          window.__yolomuxCalibrationPressureTicks += 1;
          window.__yolomuxCalibrationPressureFrame = requestAnimationFrame(burn);
        };
        window.__yolomuxCalibrationPressureFrame = requestAnimationFrame(burn);
        """,
        busy_ms,
    )


def stop_independent_browser_pressure(browser: Any) -> None:
    browser.execute_script(
        "window.__yolomuxCalibrationPressureActive = false; cancelAnimationFrame(window.__yolomuxCalibrationPressureFrame);",
    )


# ---------------------------------------------------------------------------
# Host resource qualification
# ---------------------------------------------------------------------------

HOST_SAMPLE_SECONDS = 1.5
HOST_CPU_WORK_ITERATIONS = 100_000
HOST_CPU_WORK_SAMPLES = 15
HOST_STORAGE_PROBE_ROWS = 20_000
HOST_STORAGE_PROBE_BODY_BYTES = 400
HOST_STORAGE_WORK_SAMPLES = 15
HOST_STORAGE_WORK_SPAN_ROWS = 4_000
# procs_running and disk in-flight depth are read in the trailing loop, which is whatever is left of
# the window after the two work units finish - so it shrinks exactly when the host is busiest. A
# 2026-08-08 probe under 96-worker saturation returned ONE procs_running sample, which makes its
# "p75" an instantaneous read of a single moment. The loop therefore runs until the window has
# elapsed AND this many instantaneous READS have been taken, so the nearest-rank p75 below always
# keeps at least a quarter of its samples above the selected index on a host that exposes the
# signal, and terminates on one that does not.
HOST_INSTANT_SAMPLE_MINIMUM = 20

# The population a limit is answerable to is the one the phase actually runs in, and that is
# `post_lane`: `tools/check.py` measures the host AFTER `retire_owned_processes()` has joined every
# lane descendant and every test container and called `os.sync()`. Calibrating against an idle
# `baseline` measured what no certification unit ever sees, and against a shared box where nothing
# holds the expensive-tool lock, so another agent's gate lands inside it. Every asserted limit
# below is placed by ONE rule, HOST_QUALIFICATION_GUARD_MARGIN, against `post_lane`.
#
# Deliberately NOT thresholds: the 1/5/15-minute load averages and the PSI avg10/avg60/avg300
# fields. Those are decaying estimators of a state that has already ended, so immediately after
# the parallel lanes retire they still report the retired load - the post-lane preflight that
# measured cpu some-stall 0.0038 and procs_running p75 6 read a 1-minute load average of 26.8.
# They are recorded as evidence. Everything asserted below is either instantaneous or a delta
# across this probe's own window.
#
# Trailing comments carry each limit's two margins: how far it sits above the highest value the
# statistic reached in `post_lane`, and how far below the lowest value it reached under the
# saturation the phase's own negative control drives.
HOST_QUALIFICATION_LIMITS: dict[str, float] = {
    "procs_running_p75": 34.0,  # 2.00x post-lane max 17; 2.79x below saturated min 95
    "cpu_stall_some_fraction": 0.067,  # 2.00x post-lane max 0.033485; 7.21x below saturated min 0.483
    "io_stall_some_fraction": 0.056,  # 2.03x post-lane max 0.027649; 1.51x below saturated min 0.0846
    # A guard, not a discriminator: saturation drove io full-stall as low as 0.00656 and as high as
    # 0.1053, so this refuses 1 of 12 saturated probes. It is placed only so it stops refusing the
    # post-lane host, where it was the single most frequent false refusal - 6 of 84 probes.
    "io_stall_full_fraction": 0.051,  # 2.02x post-lane max 0.025212; reachable below saturated max 0.1053
    # 153x the highest value 84 post-lane probes produced, and the 96-worker negative control never
    # moved it off 0.0 at all. Kept because it fails closed on a mode nothing else here measures -
    # a thrashing host - not because it separates these populations.
    "memory_stall_full_fraction": 0.010,  # 153.85x post-lane max 6.5e-05; not exercised by saturation
    "disk_busy_fraction_max": 0.900,  # 2.01x post-lane max 0.448324; 1.02x below saturated min 0.9177
    "cpu_work_median_ms": 33.0,  # 2.02x post-lane max 16.318; reachable below saturated max 60.8
    "storage_work_median_ms": 13.0,  # 2.25x post-lane max 5.778; reachable below saturated max 16.0
}

# Measured, reported in every qualification and artifact, and deliberately never asserted - with
# the machine-readable reason travelling beside the signal, because a signal dropped silently is
# the same defect as a failure swallowed silently.
HOST_QUALIFICATION_EVIDENCE_ONLY: dict[str, str] = {
    "disk_in_flight_max": (
        "populations_inverted: a fit host reaches HIGHER values than a saturated one, so no limit "
        "can sit above the post-lane maximum and still be reachable by real saturation. Measured "
        "2026-08-08 on keivenc-linux1: post-lane 0-621 against 11-68 under the 96-worker CPU+fsync "
        "load, and 0-379 on the retired ambient baseline. The statistic is a literal maximum over "
        "instantaneous queue depths, so it reports whether a bulk writeback burst happened to "
        "overlap one of ~25 reads taken 50 ms apart: in the same probes that produced 621 and 697, "
        "the p75 of every one was 0. It is also not a hardware queue depth here - across 24 483 "
        "consecutive reads during a live gate, nvme0n1 and sda reported in_flight 0 every single "
        "time while the stacked device-mapper volumes dm-0 and dm-1 reported 0-38, so on this "
        "kernel the field is a device-mapper bio count. Worse, the gate manufactures the burst it "
        "then refuses on: `retire_owned_processes()` ends in os.sync(), which is a bulk writeback "
        "flush by definition, and the preflight starts immediately after it. Across two gates the "
        "FIRST post-lane probe recorded 621 and 23 while all 82 probes after it in the same two "
        "windows never exceeded 5. It alone refused 2 of the 5 gate attempts measured on "
        "ac0f27bfa on 2026-08-08 - one at 13.0 and one at 21.0, the latter on a probe whose other "
        "signal sat between 4x and 12x inside its own limit - while a genuinely saturated host "
        "measured 13.0 on the same statistic. Nothing is left uncovered by the drop: on the 12 "
        "host-side saturated probes disk_busy_fraction_max (post-lane 0.034-0.448 against "
        "0.918-0.980) and io_stall_some_fraction (0.001-0.028 against 0.085-0.220) each refuse 12 "
        "of 12, and when the same load runs inside the test image - where the workers' scratch "
        "writes land on a different filesystem from the host volumes /proc/diskstats reports - the "
        "disk axis is carried by storage_work_median_ms, which refused 6 of 6 in-container runs."
    ),
}

# The measured populations every limit above is answerable to. Ranges are [min, max] of the named
# statistic across that population's probes, so a future threshold move has to face the data rather
# than the red it wants to remove. Recorded machine-readable, not only in prose, because the
# retired `cpu_work_p75_ms` limit was raised and lowered against remembered numbers.
#
# `post_lane` is the one the limits are placed against, because it is the only one measured in the
# condition the exclusive phase runs in. The other three are kept as the record a future move has
# to argue with.
HOST_QUALIFICATION_MEASURED_POPULATIONS: dict[str, dict[str, Any]] = {
    "post_lane": {
        "description": (
            "the condition the exclusive phase actually runs in: 84 probes taken every ~1.6 s "
            "inside the certification window of two complete `python3 tools/check.py` runs on "
            "2026-08-08, between `Retiring lane processes` and the phase verdict, with the "
            "expensive-tool lock held so no second gate can run. Probes overlapping the phase's "
            "OWN negative control are excluded - it starts 96 CPU+fsync workers on purpose, and "
            "folding that in would calibrate the qualifier against the load it exists to refuse. "
            "It is the LAST unit in CERTIFICATION_NODE_IDS, so the cut runs from the first probe "
            "that saw its workers to the end of the window, which also removes the probe still "
            "draining its fsync backlog. The boundary is unambiguous: the highest "
            "procs_running_max among the 84 kept probes is 28, and the first excluded probe of "
            "each gate read 118 and 86. The ranges also carry the two "
            "canonical gate refusals measured on this same box before this change - "
            "cpu_stall_some_fraction 0.033485 and disk_in_flight_max 21.0 - because a recorded "
            "post-lane refusal is a post-lane observation, and calibrating against a population "
            "that omits it reproduces the same red"
        ),
        "probes": 84,
        "cpu_work_median_ms": [9.013, 16.318],
        "storage_work_median_ms": [2.738, 5.778],
        "procs_running_p75": [4.0, 17.0],
        "cpu_stall_some_fraction": [0.001764, 0.033485],
        "io_stall_some_fraction": [0.00108, 0.027649],
        "io_stall_full_fraction": [0.001017, 0.025212],
        "memory_stall_full_fraction": [0.0, 0.000065],
        "disk_busy_fraction_max": [0.033922, 0.448324],
        "disk_in_flight_max": [0.0, 621.0],
    },
    "baseline": {
        "description": "ambient box, no gate running, 30 probes at nice 5",
        "probes": 30,
        "cpu_work_median_ms": [9.000, 16.394],
        "storage_work_median_ms": [2.767, 6.417],
        "procs_running_p75": [4.0, 22.0],
        "cpu_stall_some_fraction": [0.001, 0.211],
        "io_stall_some_fraction": [0.001, 0.007],
        "io_stall_full_fraction": [0.001, 0.007],
        "memory_stall_full_fraction": [0.0, 0.002],
        "disk_busy_fraction_max": [0.047, 0.355],
        "disk_in_flight_max": [0.0, 379.0],
        "retired_cpu_work_p75_7_ms": [9.054, 16.599],
        "retired_storage_work_p75_7_ms": [2.929, 15.092],
    },
    "gate_loaded": {
        "description": "220 probes through one complete 398 s `python3 tools/check.py` run",
        "probes": 220,
        "cpu_work_median_ms": [9.012, 25.408],
        "storage_work_median_ms": [2.763, 7.450],
        "procs_running_p75": [4.0, 97.0],
        "cpu_stall_some_fraction": [0.001, 0.667],
        "io_stall_some_fraction": [0.002, 0.148],
        "io_stall_full_fraction": [0.0, 0.058],
        "memory_stall_full_fraction": [0.0, 0.0],
        "disk_busy_fraction_max": [0.035, 0.916],
        "disk_in_flight_max": [0.0, 343.0],
        "retired_cpu_work_p75_7_ms": [9.095, 100.103],
        "retired_storage_work_p75_7_ms": [2.917, 25.288],
    },
    "saturated": {
        "description": (
            "31 probes under the negative control's own 96-worker CPU+fsync load: the 19 recorded "
            "on 2026-08-08 plus 12 re-measured the same day with the current sampler, using the "
            "identical worker count and load body as "
            "test_certification_host_qualifier_refuses_a_genuinely_loaded_host. Ranges are the "
            "union, so a limit answers to the widest saturation actually observed - the "
            "re-measurement is what showed cpu some-stall reaching down to 0.483 and io full-stall "
            "up to 0.1053, both outside the earlier record"
        ),
        "probes": 31,
        "cpu_work_median_ms": [16.194, 64.983],
        "storage_work_median_ms": [5.024, 26.246],
        "procs_running_p75": [95.0, 120.0],
        "cpu_stall_some_fraction": [0.482971, 0.815],
        "io_stall_some_fraction": [0.084627, 0.220091],
        "io_stall_full_fraction": [0.003, 0.105317],
        "memory_stall_full_fraction": [0.0, 0.0],
        "disk_busy_fraction_max": [0.917673, 0.988],
        "disk_in_flight_max": [2.0, 68.0],
        "retired_cpu_work_p75_7_ms": [22.683, 75.727],
        "retired_storage_work_p75_7_ms": [5.213, 30.387],
    },
}

# ONE placement rule for every asserted limit, and the rule is what decides which signals may be
# asserted at all. A guard is placed at this multiple of the highest value the statistic reached in
# the condition the phase runs in - never between a red and the number that removes it. A signal
# whose guard would land at or above the highest value real saturation produces cannot both stop
# refusing a fit host and still refuse a loaded one; that signal is measured and reported through
# HOST_QUALIFICATION_EVIDENCE_ONLY and asserted nowhere.
HOST_QUALIFICATION_GUARD_MARGIN = 2.0

HOST_QUALIFICATION_REFERENCE_CONDITIONS = (
    "2026-08-08 keivenc-linux1: Intel i9-14900K, cpufreq reporting 8 P-cores / 16 SMT threads "
    "capped at 3.2 GHz and 16 E-cores capped at 2.4 GHz, 125 GiB, /tmp on LVM over NVMe, "
    "Microsoft Defender permanently resident "
    "at ~1.2 cores, live YOLOmux servers, nice 5. Four populations, raw ranges in "
    "HOST_QUALIFICATION_MEASURED_POPULATIONS, and the limits answer to `post_lane`. "
    "The phase is NOT measuring a host the gate degraded. `retire_owned_processes()` joins on a "
    "measured predicate - zero surviving lane descendants and zero live test containers - and then "
    "calls os.sync(), and that IS the settling condition: the preflight taken the instant it "
    "returns measured cpu some-stall 0.0038, procs_running p75 6 and disk busy 0.153 while the "
    "1-minute load average still read 26.8. Across 84 post-lane probes from two complete gates, "
    "cpu some-stall stayed at or below 0.0100 and procs_running p75 at or below 17. No settling "
    "wait was added because there is nothing left to settle for; the lagging averages are the only "
    "thing that still reports the retired load, and they are recorded, never asserted. "
    "What the limits were answerable to before this change was an idle `baseline` no unit ever "
    "runs in, measured with nothing holding the expensive-tool lock, so another agent's gate lands "
    "inside it. Against the post-lane population the retired limit set refused 12 of 84 probes - "
    "io full-stall 6, disk busy 5, io some-stall 4, procs_running 2, disk in-flight 2 - which is a "
    "14% chance of refusing any single qualifier call and about a 34% chance of surviving the "
    "seven a gate makes. Five canonical attempts on ac0f27bfa certified twice. The re-placed set "
    "refuses 0 of the same 84 and still refuses 12 of 12 saturated probes. "
    "Held out from that calibration, three further complete gates on ac0f27bfa with no external "
    "probe running certified 5 of 5 units each - 15 of 15 recoverable qualifier evaluations "
    "qualified - and replaying the retired limits over those same 15 refuses 2 of them: a "
    "preflight at procs_running p75 14.0 against 12.0 and a postflight at disk in-flight 17 "
    "against 8.0, which is two of those three gates lost to EXIT 4. "
    "The two fixed work units report a MEDIAN, not a rank near the maximum. The retired "
    "`cpu_work_p75_ms` took nearest-rank p75 of 7 samples, which selects the 6th of 7 and is moved "
    "by any 2 of them; on 2026-08-08 it refused this box twice on raw samples whose median was "
    "baseline - 21.515 ms from [12.28, 12.26, 12.28, 12.48, 16.29, 21.52, 22.42] (median 12.48) "
    "and 20.578 ms during a live gate whose other signals were all inside their limits. The "
    "retired `storage_work_p75_ms` did the same: one of 30 baseline probes reported 15.092 ms "
    "against its 12.0 limit while that probe's median was 6.42 ms. "
    "The CPU work unit does NOT discriminate gate load on this box and its limit is a saturation "
    "guard only. Pinning the same unit to one logical CPU measured 9.0-9.7 ms on P-cores, "
    "15.8-16.2 ms on E-cores and 12.3-17.2 ms on a P-core whose SMT sibling became busy, so core "
    "placement alone spans 1.7x with no change in host load - wider than the shift a real gate "
    "produces. Measured medians: baseline 9.0-16.4 ms, real gate 9.0-25.4 ms (overlapping the "
    "baseline over its whole lower range), 96-worker saturation 17.1-65.0 ms. "
    "io full-stall and the storage work unit likewise did NOT separate the populations - their "
    "limits are broad guards against a device-bound or cold-cache read (a cold page_before "
    "measured 365 ms against 58-72 ms warm), not discriminators of gate load. "
    "The signals that DO separate are procs_running p75 (baseline 4-22 against 95-120 saturated; "
    "it refuses 171 of the 220 gate-loaded probes and 19 of 19 saturated ones), cpu some-stall "
    "(0.001-0.211 against 0.744-0.815, refusing 144 of 220), disk busy (0.047-0.355 against "
    "0.950-0.988, refusing 106 of 220) and io some-stall (0.001-0.007 against 0.089-0.114, "
    "refusing 82 of 220). Replaying all three populations through the retired and the current "
    "configuration refuses exactly the same probes - 8 of 30 baseline, 173 of 220 gate-loaded, 19 "
    "of 19 saturated - so the median costs no refusal of a loaded host and removes a false one. "
    "`disk_in_flight_max` now carries no threshold at all; the measured reason is in "
    "HOST_QUALIFICATION_EVIDENCE_ONLY and it is reported beside its own p75 and per-device split "
    "in every measurement so the next reader can see why. The windowed statistic the retired note "
    "asked for was measured and does not rescue it: over the same probes the per-device p75 is 0 "
    "post-lane and 1-3 saturated, which is a threshold of exactly 1 on an integer that is 0 in "
    "more than three quarters of every probe's reads, and it adds nothing that "
    "disk_busy_fraction_max and io_stall_some_fraction do not already carry with 2.0x and 1.5x "
    "margins on both sides."
)

_PARTITION_DEVICE = re.compile(r"^(?:sd[a-z]+|hd[a-z]+|vd[a-z]+)[0-9]+$|^nvme[0-9]+n[0-9]+p[0-9]+$")
_VIRTUAL_DEVICE_PREFIXES = ("loop", "ram", "zram", "sr", "fd")


def _read_pressure_totals() -> dict[str, int] | None:
    """Return cumulative PSI stall microseconds, or None when the kernel does not expose PSI."""

    totals: dict[str, int] = {}
    for resource_name in ("cpu", "io", "memory"):
        path = Path(f"/proc/pressure/{resource_name}")
        if not path.exists():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if not fields:
                continue
            for field in fields[1:]:
                key, _, value = field.partition("=")
                if key == "total":
                    totals[f"{resource_name}_{fields[0]}"] = int(value)
    return totals or None


def _read_disk_counters() -> dict[str, dict[str, int]]:
    """Return whole-device counters only; partitions duplicate their parent device's time."""

    counters: dict[str, dict[str, int]] = {}
    path = Path("/proc/diskstats")
    if not path.exists():
        return counters
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        name = fields[2]
        if name.startswith(_VIRTUAL_DEVICE_PREFIXES) or _PARTITION_DEVICE.match(name):
            continue
        counters[name] = {
            "reads_completed": int(fields[3]),
            "writes_completed": int(fields[7]),
            "in_flight": int(fields[11]),
            "io_ticks_ms": int(fields[12]),
        }
    return counters


def _read_procs_running() -> int | None:
    path = Path("/proc/stat")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("procs_running "):
            return int(line.split()[1])
    return None


def _cpu_work_samples_ms() -> list[float]:
    """One fixed integer work unit, independent of core count and of any product code."""

    samples: list[float] = []
    for _round in range(HOST_CPU_WORK_SAMPLES):
        started = time.perf_counter()
        value = 0x9E3779B9
        for index in range(HOST_CPU_WORK_ITERATIONS):
            value = (value ^ index) * 2654435761 & 0xFFFFFFFF
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def host_storage_probe_path(evidence_root: Path | None = None) -> Path:
    return _latency_evidence_root(evidence_root) / "host-storage-probe.sqlite3"


def _storage_work_samples_ms(probe_path: Path) -> list[float]:
    """One fixed warm-cache storage work unit on the filesystem the certification units use.

    Built once and re-read, so a later sample reports whether those pages are still served from
    the page cache. A host churning large files evicts them and this rises toward device latency.
    """

    probe_path.parent.mkdir(parents=True, exist_ok=True)
    if not probe_path.exists():
        connection = sqlite3.connect(probe_path)
        connection.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany(
            "INSERT INTO probe(id, body) VALUES(?, ?)",
            ((index, "x" * HOST_STORAGE_PROBE_BODY_BYTES) for index in range(HOST_STORAGE_PROBE_ROWS)),
        )
        connection.commit()
        connection.close()
    samples: list[float] = []
    for round_index in range(HOST_STORAGE_WORK_SAMPLES):
        first = (round_index * 137) % (HOST_STORAGE_PROBE_ROWS - HOST_STORAGE_WORK_SPAN_ROWS)
        started = time.perf_counter()
        connection = sqlite3.connect(f"file:{probe_path}?mode=ro", uri=True)
        total = 0
        for row in connection.execute("SELECT id, body FROM probe WHERE id BETWEEN ? AND ?", (first, first + HOST_STORAGE_WORK_SPAN_ROWS)):
            total += len(row[1])
        connection.close()
        assert total == (HOST_STORAGE_WORK_SPAN_ROWS + 1) * HOST_STORAGE_PROBE_BODY_BYTES, (total, first)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def outliers_required_to_move(sample_count: int, selected_index: int) -> int:
    """How many of `sample_count` samples must be outliers before the statistic moves.

    The one definition of "this is effectively a maximum". `nearest_rank` at quantile q selects
    index ceil(q*n)-1, leaving n-1-index samples above it, so it takes one more outlier than that
    to move. A statistic a minority of its own samples can move reports the worst instants of a
    probe rather than what the probe measured.
    """

    return max(1, sample_count - 1 - selected_index) + 1


def work_unit_statistic(samples: list[float]) -> float:
    """The one statistic BOTH fixed work units report: the median.

    Never a rank near the maximum. A work unit is deliberately fixed so that its duration reports
    the host, and the host signal is the level the whole probe ran at - not the two rounds the
    scheduler interrupted. The median needs a majority of the samples to move it (8 of 15 here);
    `nearest_rank(samples, 0.75)` over 7 samples selected the 6th of 7 and needed only 2, which is
    how a box whose other signals were all at baseline was refused twice on 2026-08-08.
    """

    return median(samples)


def measure_host_resources(*, evidence_root: Path | None = None, sample_seconds: float = HOST_SAMPLE_SECONDS) -> dict[str, Any]:
    """Measure this host once. Windowed deltas and instantaneous reads only, never decaying averages."""

    probe_path = host_storage_probe_path(evidence_root)
    pressure_before = _read_pressure_totals()
    disk_before = _read_disk_counters()
    started_monotonic = time.monotonic()
    procs_running_samples: list[int] = []
    in_flight_samples: list[int] = []

    per_device_in_flight: dict[str, list[int]] = {}

    def sample_instantaneous() -> None:
        """One owner for every instantaneous read, so both signals share one sample count."""

        running = _read_procs_running()
        if running is not None:
            procs_running_samples.append(running)
        for name, counter in _read_disk_counters().items():
            per_device_in_flight.setdefault(name, []).append(counter["in_flight"])
            in_flight_samples.append(counter["in_flight"])

    instant_reads = 0
    sample_instantaneous()
    instant_reads += 1
    cpu_samples = _cpu_work_samples_ms()
    storage_samples = _storage_work_samples_ms(probe_path)
    # Both conditions, never just the deadline: the work units above take longer the busier the
    # host is, so a deadline-only loop takes the fewest instantaneous samples exactly when they
    # matter most, and its nearest-rank p75 degenerates towards a single read. The count is of
    # READS, not of collected values: a host that does not expose /proc/stat at all must leave this
    # loop and be refused for `signal_unavailable`, never spin here waiting for a sample it can
    # never take.
    while time.monotonic() - started_monotonic < sample_seconds or instant_reads < HOST_INSTANT_SAMPLE_MINIMUM:
        sample_instantaneous()
        instant_reads += 1
        time.sleep(0.05)

    window_seconds = time.monotonic() - started_monotonic
    pressure_after = _read_pressure_totals()
    disk_after = _read_disk_counters()

    stalls: dict[str, float] | None = None
    if pressure_before is not None and pressure_after is not None:
        stalls = {
            key: round((pressure_after[key] - pressure_before[key]) / (window_seconds * 1_000_000), 6)
            for key in sorted(pressure_before)
            if key in pressure_after
        }
    disk_busy = {
        name: round((disk_after[name]["io_ticks_ms"] - counters["io_ticks_ms"]) / (window_seconds * 1000), 6)
        for name, counters in disk_before.items()
        if name in disk_after
    }
    disk_iops = {
        name: round(
            (
                (disk_after[name]["reads_completed"] - counters["reads_completed"])
                + (disk_after[name]["writes_completed"] - counters["writes_completed"])
            )
            / window_seconds,
            3,
        )
        for name, counters in disk_before.items()
        if name in disk_after
    }
    load_average = os.getloadavg() if hasattr(os, "getloadavg") else (None, None, None)
    return {
        "platform": platform.system(),
        "cpu_count": os.cpu_count(),
        "window_seconds": round(window_seconds, 6),
        # p75, not max: over a 1.5 s window the maximum is a transient and reports the scheduler's
        # worst instant rather than the demand the certification units will actually compete with.
        # HOST_INSTANT_SAMPLE_MINIMUM keeps this an actual p75: with >=20 samples the selected index
        # still has a quarter of them above it, so no pair of instants can move it.
        "procs_running_p75": nearest_rank([float(value) for value in procs_running_samples], 0.75) if procs_running_samples else None,
        "procs_running_max": max(procs_running_samples) if procs_running_samples else None,
        "procs_running_samples": len(procs_running_samples),
        "pressure_available": stalls is not None,
        "cpu_stall_some_fraction": None if stalls is None else stalls.get("cpu_some"),
        "cpu_stall_full_fraction": None if stalls is None else stalls.get("cpu_full"),
        "io_stall_some_fraction": None if stalls is None else stalls.get("io_some"),
        "io_stall_full_fraction": None if stalls is None else stalls.get("io_full"),
        "memory_stall_some_fraction": None if stalls is None else stalls.get("memory_some"),
        "memory_stall_full_fraction": None if stalls is None else stalls.get("memory_full"),
        "disk_devices": sorted(disk_busy),
        "disk_busy_fraction": disk_busy,
        "disk_busy_fraction_max": max(disk_busy.values()) if disk_busy else None,
        "disk_iops": disk_iops,
        # Evidence, never asserted - see HOST_QUALIFICATION_EVIDENCE_ONLY. The p75 and the
        # per-device split are recorded beside the maximum because they are what disqualified it:
        # on this box the maximum is a lone instant on a stacked device-mapper volume while three
        # quarters of the same probe's reads are zero, and the physical devices never report a
        # non-zero depth at all.
        "disk_in_flight_max": max(in_flight_samples) if in_flight_samples else None,
        "disk_in_flight_p75": nearest_rank([float(value) for value in in_flight_samples], 0.75) if in_flight_samples else None,
        "disk_in_flight_samples": len(in_flight_samples),
        "disk_in_flight_max_per_device": {name: max(values) for name, values in sorted(per_device_in_flight.items())},
        "cpu_work_samples_ms": [round(sample, 3) for sample in cpu_samples],
        "cpu_work_median_ms": round(work_unit_statistic(cpu_samples), 3),
        "storage_work_samples_ms": [round(sample, 3) for sample in storage_samples],
        "storage_work_median_ms": round(work_unit_statistic(storage_samples), 3),
        "storage_probe_path": str(probe_path),
        # Recorded because check.py lowers its own priority when live YOLOmux servers are present,
        # and a niced measurement is a different measurement.
        "process_nice": os.nice(0),
        # Recorded, never asserted: these lag the state they describe by 1-5 minutes.
        "lagging_load_average": [None if value is None else round(value, 3) for value in load_average],
    }


def host_qualification(
    measurement: dict[str, Any] | None = None,
    *,
    evidence_root: Path | None = None,
    limits: dict[str, float] | None = None,
    sample_seconds: float = HOST_SAMPLE_SECONDS,
) -> dict[str, Any]:
    """Decide whether this host may certify anything, and say exactly why when it may not.

    The ONE owner of that decision. `tools/check.py` calls it for the phase preflight and
    postflight and every certification node calls it through `certification_phase_fixture`, so a
    node run standalone and a node run inside the phase are qualified by identical thresholds
    against identical signals. No caller may keep a second, private notion of a fit host.
    """

    measured = measure_host_resources(evidence_root=evidence_root, sample_seconds=sample_seconds) if measurement is None else measurement
    applied = HOST_QUALIFICATION_LIMITS if limits is None else limits
    reasons: list[dict[str, Any]] = []
    for signal, limit in sorted(applied.items()):
        value = measured.get(signal)
        if value is None:
            # Fail closed. An unavailable signal is an unqualified host, never a silent pass.
            reasons.append({"signal": signal, "measured": None, "limit": limit, "reason": "signal_unavailable"})
            continue
        if float(value) > float(limit):
            reasons.append({"signal": signal, "measured": float(value), "limit": float(limit), "reason": "over_limit"})
    return {
        "qualified": not reasons,
        "reasons": reasons,
        "limits": dict(applied),
        # A signal that is measured but deliberately not asserted travels with its machine-readable
        # reason in every qualification, so a reader of one artifact can see which signals were
        # dropped and why without going back to the module. Dropping one silently is the same
        # defect as swallowing a failure. Derived from the limits actually applied, never from the
        # module constant alone: a caller that passes its own `limits` covering one of these names
        # did assert it, and reporting it as unasserted would be a false machine-readable claim.
        "evidence_only": {signal: reason for signal, reason in HOST_QUALIFICATION_EVIDENCE_ONLY.items() if signal not in applied},
        "reference_conditions": HOST_QUALIFICATION_REFERENCE_CONDITIONS,
        "measurement": measured,
    }


def browser_calibration_qualification(calibration: dict[str, Any], *, admission_ms: float = CALIBRATION_ADMISSION_MS) -> dict[str, Any]:
    """Qualify the renderer against the declared admission envelope. Never returns a factor.

    `at_reference` reports the separate, stricter decision a unit may use to choose which statistic
    it certifies. Neither value ever changes a ceiling.
    """

    calibration_now_ms = float(calibration["calibrationNowMs"])
    qualified = calibration_now_ms <= float(admission_ms)
    return {
        "qualified": qualified,
        "reasons": []
        if qualified
        else [{"signal": "browser_calibration_p75_ms", "measured": calibration_now_ms, "limit": float(admission_ms), "reason": "over_limit"}],
        "at_reference": calibration_now_ms <= CALIBRATION_REFERENCE_MS,
        "calibration_now_ms": calibration_now_ms,
        "calibration_admission_ms": float(admission_ms),
        "calibration_reference_ms": CALIBRATION_REFERENCE_MS,
        "calibration_samples_ms": calibration.get("samplesMs"),
        "calibration_statistic": calibration.get("statistic"),
        "calibration_work_unit": calibration.get("workUnit"),
        "calibration_reference_conditions": CALIBRATION_REFERENCE_CONDITIONS,
    }


def merged_qualification(*qualifications: dict[str, Any]) -> dict[str, Any]:
    """Combine independent qualifiers; any unqualified input makes the whole decision unqualified."""

    reasons = [reason for qualification in qualifications for reason in qualification["reasons"]]
    return {"qualified": not reasons, "reasons": reasons, "parts": list(qualifications)}


# ---------------------------------------------------------------------------
# Fixed-ceiling verdicts
# ---------------------------------------------------------------------------


def fixed_ceiling_verdict(*, label: str, raw_measured_ms: float, ceiling_ms: float, statistic: str = "max") -> dict[str, Any]:
    """Judge one measurement against the product's fixed ceiling. No host input, ever."""

    if not all(math.isfinite(float(value)) and float(value) > 0 for value in (raw_measured_ms, ceiling_ms)):
        raise ValueError(f"fixed ceiling inputs must be finite and positive: {(raw_measured_ms, ceiling_ms)!r}")
    return {
        "label": label,
        "statistic": statistic,
        "raw_measured_ms": round(float(raw_measured_ms), 6),
        "ceiling_ms": round(float(ceiling_ms), 6),
        "passed": float(raw_measured_ms) < float(ceiling_ms),
    }


# ---------------------------------------------------------------------------
# Probe sensitivity
# ---------------------------------------------------------------------------

# The probe must read higher with independent renderer pressure applied than without it. The margin
# is unchanged from the unit that used to compute this inline; only the two statistics it compares.
CALIBRATION_PRESSURE_MOVE_RATIO = 1.15


def calibration_pressure_verdict(
    *,
    quiet_samples_ms: list[float],
    busy_samples_ms: list[float],
    move_ratio: float = CALIBRATION_PRESSURE_MOVE_RATIO,
) -> dict[str, Any]:
    """Judge whether the probe moved when the renderer was put under independent pressure.

    ONE statistic, the median, on BOTH sides. This unit asks only whether the central reading moves;
    it is not a ceiling and it never decides how slow the product may be, so an extreme order
    statistic is the wrong instrument for it. Comparing a maximum-over-rounds on the quiet side with
    a single round's p75 on the busy side, as this comparison used to, is not a comparison at all:
    on a contended host the quiet side then reports the machine's worst preemption while the busy
    side reports its own middle, and the ratio inverts with the product unchanged.

    Measured 2026-08-09 on keivenc-linux1, headless Chromium at 1200x700, 42 samples per side. Idle
    host: quiet median 16.6 ms, quiet maximum 20.0 ms, busy median 28.6 ms. Same box under 32
    concurrent CPU burners: quiet median 16.6 ms -- unchanged -- quiet maximum 41.9 ms, busy median
    34.2 ms, busy minimum 28.0 ms. The signal survives a saturated host at every matched statistic,
    and only the quiet tail moves, which is why the tail may not be the statistic.
    """

    if not quiet_samples_ms or not busy_samples_ms:
        raise ValueError("the pressure verdict needs samples on both sides")
    quiet_median_ms = float(median(quiet_samples_ms))
    busy_median_ms = float(median(busy_samples_ms))
    if not all(math.isfinite(value) and value > 0 for value in (quiet_median_ms, busy_median_ms)):
        raise ValueError(f"pressure verdict medians must be finite and positive: {(quiet_median_ms, busy_median_ms)!r}")
    ratio = busy_median_ms / quiet_median_ms
    return {
        "statistic": "median",
        "quiet_median_ms": round(quiet_median_ms, 6),
        "busy_median_ms": round(busy_median_ms, 6),
        "ratio": ratio,
        "move_ratio": float(move_ratio),
        "quiet_sample_count": len(quiet_samples_ms),
        "busy_sample_count": len(busy_samples_ms),
        "quiet_samples_ms": list(quiet_samples_ms),
        "busy_samples_ms": list(busy_samples_ms),
        "passed": ratio >= float(move_ratio),
    }


def _latency_evidence_root(explicit_root: Path | None = None) -> Path:
    root = explicit_root or Path(os.environ.get("YOLOMUX_E2E_EVIDENCE_DIR", "/tmp/yolomux-latency-evidence"))
    resolved = root.resolve()
    if not resolved.is_relative_to(Path("/tmp").resolve()):
        raise ValueError(f"latency evidence root must stay under /tmp: {root}")
    return root / "latency-calibration"


def write_latency_evidence(
    *,
    nodeid: str,
    label: str,
    payload: dict[str, Any],
    evidence_root: Path | None = None,
) -> Path:
    """Persist one collision-free machine-readable browser-latency artifact."""

    root = _latency_evidence_root(evidence_root)
    root.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^a-z0-9_.-]+", "-", label.lower()).strip("-") or "latency"
    artifact = root / f"{safe_label}-{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    artifact.write_text(json.dumps({"nodeid": nodeid, **payload}, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return artifact


def certify_verdicts(
    *,
    nodeid: str,
    label: str,
    verdicts: list[dict[str, Any]],
    qualification: dict[str, Any],
    extra_evidence: dict[str, Any] | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    """Resolve one certification node's three possible outcomes. One owner for all of them.

    NOT CERTIFIABLE (raise), ceiling breach (assert), certified (return). Every branch persists the
    same raw evidence artifact first, so a refusal and a breach are equally auditable and neither
    can be reached by measuring less. The ceiling is identical in every branch: qualification
    decides whether a verdict may be reached, never what the verdict is.
    """

    payload = {"verdicts": list(verdicts), "qualification": qualification, **(extra_evidence or {})}
    artifact = write_latency_evidence(nodeid=nodeid, label=label, payload=payload, evidence_root=evidence_root)
    if not qualification["qualified"]:
        raise NotCertifiableError(
            {"nodeid": nodeid, "label": label, "reasons": qualification["reasons"], "artifact": str(artifact), "verdicts": list(verdicts)}
        )
    breaches = [verdict for verdict in verdicts if not verdict["passed"]]
    assert not breaches, (
        f"{label} breached its fixed ceiling: {json.dumps({'breaches': breaches, 'evidence': payload}, sort_keys=True, default=str)}; artifact={artifact}"
    )
    return {"nodeid": nodeid, "label": label, "verdicts": list(verdicts), "artifact": str(artifact)}


def assert_fixed_ceiling(
    *,
    nodeid: str,
    label: str,
    raw_measured_ms: float,
    ceiling_ms: float,
    qualification: dict[str, Any],
    statistic: str = "max",
    extra_evidence: dict[str, Any] | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    """Certify ONE measurement, or refuse. The single-verdict spelling of `certify_verdicts`."""

    verdict = fixed_ceiling_verdict(label=label, raw_measured_ms=raw_measured_ms, ceiling_ms=ceiling_ms, statistic=statistic)
    certified = certify_verdicts(
        nodeid=nodeid,
        label=label,
        verdicts=[verdict],
        qualification=qualification,
        extra_evidence=extra_evidence,
        evidence_root=evidence_root,
    )
    return {**verdict, "artifact": certified["artifact"]}


def require_qualified_host(
    *,
    nodeid: str,
    label: str,
    evidence_root: Path | None = None,
    measurement: dict[str, Any] | None = None,
    sample_seconds: float = HOST_SAMPLE_SECONDS,
) -> dict[str, Any]:
    """Ask the one host owner whether this box may certify, and refuse with evidence when it may not.

    Called before a certification node builds any fixture, so the measurement describes the host the
    unit is about to run on rather than the load the unit itself creates. An unqualified host raises
    NotCertifiableError - never `pytest.skip`, because a skip is a green a reader cannot distinguish
    from a certification.
    """

    qualification = host_qualification(measurement=measurement, evidence_root=evidence_root, sample_seconds=sample_seconds)
    if not qualification["qualified"]:
        artifact = write_latency_evidence(
            nodeid=nodeid,
            label=f"{label} host qualification",
            payload={"qualification": qualification, "stage": "host_qualification"},
            evidence_root=evidence_root,
        )
        raise NotCertifiableError(
            {
                "nodeid": nodeid,
                "label": label,
                "stage": "host_qualification",
                "reasons": qualification["reasons"],
                "artifact": str(artifact),
            }
        )
    return qualification


def certification_phase_requested(request: Any, *, env_name: str = LATENCY_CERTIFICATION_ENV) -> bool:
    """True only when this unit was asked for deliberately, by env flag or by explicit node id.

    Two admissions, one owner. docker/run-tests.sh forwards a fixed env allowlist, so a host-side
    variable never reaches the containerized run; naming the node id works from either side and is
    what an exclusive phase does anyway. `env_name` selects which unit's admission flag applies;
    every accepted name must also appear in that allowlist or the node is silently skipped.
    """

    if os.environ.get(env_name) == "1":
        return True
    return any(request.node.name in str(argument) for argument in request.config.invocation_params.args)


def certification_phase_skip_reason(env_name: str = LATENCY_CERTIFICATION_ENV) -> str:
    return (
        f"exclusive latency-certification phase: name this node id explicitly, or set "
        f"{env_name}=1 inside the run. A shared parallel lane oversubscribes the renderer and the "
        f"disk and would measure the machine, not the product. docker/run-tests.sh must also "
        f"forward the variable with `-e {env_name}`: pytest re-executes itself inside the "
        f"container, which passes through only the names listed there, so setting it on the host "
        f"alone leaves this node silently skipped."
    )


def certification_phase_fixture(env_name: str = LATENCY_CERTIFICATION_ENV) -> Any:
    """Build a module's `certification_phase_only` fixture. One owner for both admission and fitness.

    Assign the result to a module-level `certification_phase_only` and request it FIRST in the node
    signature, ahead of any browser or server fixture: the two decisions it makes - may this unit run
    at all, and is this host fit to certify - both have to be made before the unit's own fixtures put
    load on the machine they describe.

    The two outcomes are deliberately different. Not asked for is a skip, which the phase runner
    reports as `certification_unit_did_not_run` and never as green. An unqualified host is
    NOT CERTIFIABLE with its raw evidence. Yields the host qualification so the node can merge it
    into whatever else it qualifies on and carry the whole measurement in its evidence artifact.
    """

    @pytest.fixture
    def certification_phase_only(request: Any) -> dict[str, Any]:
        if not certification_phase_requested(request, env_name=env_name):
            pytest.skip(certification_phase_skip_reason(env_name))
        return require_qualified_host(nodeid=request.node.nodeid, label=request.node.name)

    return certification_phase_only


def main(argv: list[str] | None = None) -> int:
    """Emit one machine-readable host qualification. Exit 0 qualified, 4 NOT CERTIFIABLE."""

    parser = argparse.ArgumentParser(description="Measure and report host certification qualification.")
    parser.add_argument("--evidence-root", default=None, help="directory under /tmp for the storage probe and artifacts")
    arguments = parser.parse_args(argv)
    qualification = host_qualification(evidence_root=Path(arguments.evidence_root) if arguments.evidence_root else None)
    print(json.dumps(qualification, indent=2, sort_keys=True, default=str))
    if not qualification["qualified"]:
        print(f"{NOT_CERTIFIABLE}: {json.dumps(qualification['reasons'], sort_keys=True)}")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

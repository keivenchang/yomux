# DOIT.p0.e2.gate-tiering-and-serialization.md - Find why the gate is red before changing how it is scheduled

## Priority

P0. The gate reaches all-lane-green on **34.7%** of full runs (66 of 190), so one green gate costs **2.88 attempts and about 28 minutes**. That is the release bottleneck. What is **not** established is why, and an independent audit refuted the first four explanations this queue proposed.

## Status: the original plan was audited and mostly refuted

The first version of this queue argued that the heavy lanes fail because they contend, that a 40-second fast tier should gate them, and that serialization would cut time-to-green from 28 to 15 minutes. Task `gate-audit-01`, a read-only audit run on 2026-08-24 against `35e765675`, refuted the causal claim, the cost model, and the assumed mechanism. Its scratch evidence is at `/tmp/gate-audit-collect.py`, `/tmp/gate-audit-analyze.py`, `/tmp/gate-audit-analysis.txt`, `/tmp/gate-audit-full-lane-medians.txt`, and `/tmp/gate-audit-collections/`.

Do not restore the original argument. What follows is what survived.

### REFUTED: `pytest-gate-serial`'s 0.5% failure rate proves concurrency is the cause

Normalizing by collected node count destroys the comparison. Per 1,000 node opportunities: `pytest` 0.0273, `pytest-browser` 0.7166, `pytest-gate-serial` 0.5612, `pytest-e2e` 2.4561. The serial lane is **20.55x worse than `pytest`** per test opportunity, not better. It is 2.16x-3.14x better per lane-second, which measures exposure window rather than concurrency. Node counts are `pytest` 18,352, browser 658, E2E 126, gate-serial 9. The raw 0.5% against 50.1% comparison cannot carry a causal conclusion.

### REFUTED: co-failure means load

Recurring identical failures exist inside the co-failing population, at **different SHAs**:

- `test_ring_landing_real_page_restart_and_zero_gap` failed in 3 runs at `6c360ec3`, `8adc6108`, and `35e76567`. The assertion is exact product state: rendered total cost `0` where the fixture seeded `12`, with `incomplete_persisted_bucket` in the captured state.
- `test_real_stats_cpu_value_round_trips_through_rpc_and_rendered_svg` failed in 2 runs.
- `test_standalone_probe_drives_an_ephemeral_authenticated_daemon` failed twice.
- `test_older_deferred_completion_cannot_replace_newer_forced_canonical_cache` failed twice.

Co-failure rates re-derived by the audit are `pytest` 46.74%/33.70%, browser 67.19%/39.06%, E2E 75.61%/60.98% - close to the original numbers, but the interpretation was wrong. **How much of the remainder is contention is UNPROVEN and currently unmeasurable**: retained outputs with identifiable node IDs exist for only 5 of 53 co-failing `pytest` runs, 2 of 47 browser, and 4 of 35 E2E.

### REFUTED: the cost model

Matched full-run medians, computed from the same 190 runs rather than from all lane samples: fast tier **43.18s**, heavy lanes **1,126.79s**, gate-serial 7.78s, serial total **1,177.74s** against a parallel wall of **587.40s**. The corrected delta is **+590.34s per attempt** with a **1.44-attempt** break-even; the earlier +308s and 1.89-attempt figures came from mixing focused-lane medians into a full-gate model. One nominal serial attempt is 19.6 minutes against a current expected 28.2 minutes.

Existing `mode=serial` reports cannot settle it either: 3 serial `pytest` runs, 1 browser, 1 E2E, and `--serial` calls `lanes(serial=True)`, which also sets every pytest worker count to 1 and removes xdist. Those runs measure lane serialization **plus** within-lane de-parallelization.

### REFUTED: `EXPENSIVE_TOOL_LANES` already provides an exclusive lane scheduler

It decides whether one whole `check.py` invocation takes a cross-process file lock against other invocations. It does not serialize lanes inside an invocation. `run_parallel()` still starts up to 8 lane threads, and `run_serial()` is the only internal serializer - coupled to worker-count 1 and no xdist. The mechanism this queue assumed exists does not; scheduling policy and within-lane worker policy must be separated first.

### REFUTED as attribution: `procs_running_p75 = 36` proves external work during certification

Phase retirement reported 0 survivors and 0 containers in 0.0448s. Preflight qualified at p75 5, postflight at 12. Across the 15 confirmation windows for i3b, i3c, and chat-store, **p75 36 occurs once**; the rest are mostly disk-busy refusals at 0.9174, 0.9120, 0.9199. `procs_running` is a single host-wide integer from `/proc/stat` with no PID census, so it proves aggregate runnable demand exceeded the limit, not where the demand came from. Host contention in that window is CONFIRMED; its owner is not.

### UNPROVEN, and it understates the cost: the fast tier

The fast tier failed in 56 of 190 runs, but only **4** were fast-only. The other **52 (92.86%)** also had a heavy failure. A hard fast gate would suppress same-run heavy evidence in those 52 and defer it to another attempt, so the original "8.4 hours reclaimed" counted deferred work as eliminated work and omitted the extra discovery round. `static` alone accounts for 49 of the 56 fast reds and has a 25.32s full-run median.

### CONFIRMED, with narrower wording: the focused selections are duplicated

Exact node-ID set differences:

- `pytest` vs `pytest-unit`: 18,172 intersection, 180 `pytest`-only, **0 unit-only**. `pytest-unit` is an exact subset.
- `pytest` vs `pytest-socket`: 180 intersection, 96 socket-only - and all 96 are inside the 126-node E2E collection, so the 276-node socket lane is wholly covered by default `pytest` plus E2E.
- browser vs golden: boot 6, main browser 636, golden 16, union 658. All 16 golden nodes are already in the default browser lane.

**But none of these are default lanes**, and golden already runs once inside the default browser lane. Retiring them removes redundant opt-in entry points; it does **not** reduce the default 9-lane wall time. The original queue implied otherwise.

## Corrected baseline

Re-derived by the audit from **767** root reports (the earlier count of 698 was stale), 0 parse errors, **190** exact 9-lane runs with wall greater than 60s.

| Lane | Runs | Fails | Fail rate | Nodes collected | Full-run median s |
| --- | ---: | ---: | ---: | ---: | ---: |
| `pytest` | 467 | 234 | 50.1% | 18,352 | - |
| `pytest-browser` | 509 | 240 | 47.2% | 658 (incl. boot, golden) | - |
| `pytest-e2e` | 475 | 147 | 30.9% | 126 | - |
| `static` | 499 | 127 | 25.5% | - | 25.32 |
| `pytest-gate-serial` | 198 | 1 | 0.5% | 9 | 7.78 |

Fast tier 43.18s, heavy lanes 1,126.79s, parallel wall 587.40s, green rate 34.7%, expected time to green 1,690.99s.

## The corrected plan

The audit's conclusion, which this queue now adopts: **named product defects exist in the failing population, and no scheduling change should be evaluated until they are removed from the sample.** Otherwise any A/B comparison measures those defects, not the scheduler.

### Current execution order

The scheduling-policy, serialization, cancellation, and A/B items remain blocked on `DOIT.p0.e1.stability-recurring-gate-defects.md`. The failed-node-ID persistence item is independently runnable and is the current focus because P0 e1 now needs its in-gate failure signatures and denominator to classify Defects 2-4. Implement and verify only that evidence owner first, re-read the resulting gate data in P0 e1, and keep every scheduling behavior unchanged until the recurring defects are classified and owned.

- [ ] **Blocked on `DOIT.p0.e1.stability-recurring-gate-defects.md`, which now owns the four recurring defects.** Do not duplicate that work here. One of them is already reproduced in isolation on a quiet host at 1 failure in 8 runs, so it is confirmed as a real race rather than contention. This queue's experiment cannot start until that queue's defects are out of the sample, or the A/B will measure them instead of the scheduler.
- [x] **Fix the evidence gap first, because it is why the contention share is unmeasurable.** Retained outputs carry identifiable failed node IDs for under 11% of failing lanes. Make every lane failure persist its failed node IDs into the run report itself, so a future analysis is one query instead of an archaeology exercise across 767 files. Without this, the central question of this queue cannot be answered at all. DONE (2026-08-25, uncommitted subject at HEAD `4a0e94be2`; `tools/check.py` sha256 `7338d3dbf117`, `tests/test_check_runner.py` sha256 `7589d272f3cb`, architecture manifest sha256 `5349032d13e1`): the preserved first canonical attempt wrote schema 5 with the exact browser failure ID and two exact non-browser failure IDs, zero unresolved rows, and no truncation. The initial task-owned static failure remains preserved at `/tmp/v0716-p0e1-current-subject-gate-20260825-0623/`; its two source-shape assertions were replaced by behavioral and AST owner checks, duplicated test fixtures were compacted, and exactly one of 1,715 architecture-manifest leaves changed to record the deliberate 3,214-line test owner. Coordinator verification passed the architecture node, static lane, and 25 focused owner/regression nodes; an independent isolated-container full-file run passed 135 with 1 skipped in 367.54 seconds at `/tmp/p0e2-coordinator-full-container-02.log`, exit 0. The implementer run independently passed the same 135 with 1 skipped in 376.09 seconds. The earlier rejected duplicate-helper subject remains at `/tmp/p0e2-final/full-container.log`. Scheduling behavior is unchanged.
- [ ] **Separate lane scheduling from within-lane worker policy in `tools/check.py`.** Today `run_serial()` couples them and `EXPENSIVE_TOOL_LANES` is a cross-process lock, not a scheduler. Introduce a scheduling policy that can run lanes one at a time while each lane keeps its current xdist worker count. This is a prerequisite for measuring anything, not a fix in itself.
- [ ] **Run a matched fixed-SHA A/B experiment** once the defects above are owned: same SHA, same worker counts, changing only cross-lane scheduling, at least 20 runs per arm. Record per-lane failure rate, per-lane duration, wall, and green rate for both arms. The measured delta is +590.34s per attempt and break-even is 1.44 attempts; the experiment decides whether serialization reaches that, and it may show starved lanes run faster alone, which would move the number.
- [ ] **Prototype cancellation-after-fast-red rather than a hard fast gate.** Since 52 of 56 fast reds also had heavy failures, preserve heavy evidence already produced and cancel only work not yet started. Compare against a narrow `static`-only preflight, since `static` alone explains 49 of 56 fast reds at a 25.32s median. Measure both against the 587.40s baseline before choosing.
- [ ] **Retire the duplicated focused selections** - `pytest-unit` (exact subset, 0 unique nodes) and `pytest-socket` (fully covered by default `pytest` plus E2E) - or narrow them to a genuinely disjoint set. State the decision and the measured set difference here. Do not claim this improves default wall time; it removes misleading opt-in entry points.
- [ ] **Give certification an attribution census, not just a scheduling change.** `procs_running_p75` is a host-wide integer with no owner. Record a PID census alongside it in the qualification artifact so a future refusal names the demand instead of implying it. Only then can "impossible by construction" mean anything.

## Forbidden remedies

Do not raise any host-qualification limit, add retries or sleeps, lower concurrency inside a lane to mask an order-dependent test, or weaken an assertion. Do not delete a lane because it is slow or red; `pytest-unit` is proposed for retirement because its node set is a measured exact subset.

Do not evaluate a scheduling change while the four named defects are still in the sample, and do not quote any number from the original version of this queue.

## Gotchas

- **Normalize before comparing lanes.** The original argument died on this: raw failure rate across lanes with 9 versus 18,352 nodes and 7.78s versus 1,126.79s of exposure compares nothing.
- **Do not mix focused-run lane medians into a full-gate cost model.** That single error produced a 2x-wrong delta and a wrong break-even.
- `--serial` today also sets worker counts to 1 and removes xdist, so existing serial reports cannot be used as the serialized arm of any experiment.
- `slowest_first()` at `tools/check.py:853` exists on purpose to minimize single-run makespan. If the experiment justifies changing it, amend the comment rather than deleting the rationale.
- 767 artifacts include partial and interrupted runs. Analyse only exact 9-lane runs with wall greater than 60s, or the rates are wrong.
- A fast-tier gate defers heavy evidence rather than eliminating it. Any saving claim must model the extra discovery round.

## Done Criteria

- The four recurring defects each have an isolated reproduction and a classification, and are fixed or filed with an owner.
- Lane failures persist their failed node IDs into the run report, and the contention share of co-failure is either measured or explicitly declared unmeasurable with the reason.
- Lane scheduling is separable from within-lane worker counts, proven by a run that serializes lanes while preserving xdist.
- A matched fixed-SHA A/B of at least 20 runs per arm is recorded here, with per-lane failure rate, duration, wall, and green rate for both arms, against the 34.7% and 1,690.99s baselines.
- The duplicated focused selections are retired or narrowed, with the measured set difference recorded.
- Certification refusals name the demand through a PID census rather than a bare host-wide integer.

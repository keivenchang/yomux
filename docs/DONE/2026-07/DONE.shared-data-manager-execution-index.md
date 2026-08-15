# DOIT — shared data manager execution index

This index replaces the monolithic `DOIT.shared-data-manager-architecture.md`. It carries ordering and session discipline only; active requirements live in the track files below.

## Execution plan to completion

Phase 0 — COMPLETE 2026-07-27. The dev7773 attach-path guard landed, the pile was rebased and landed, and the unmodified eight-lane gate now passes end to end. Runtime lanes are usable from dev7772 again. Do not re-derive this phase.

STANDING DIRECTIVE — finish every remaining checkbox in this queue. Do not stop after one item: close a box, land it, then immediately select the next by the phase order below. Stop only when every box is checked, or when genuinely blocked on something an agent cannot do (Phase 5 needs the user driving a real browser). Report blockers rather than working around them.

Phase 1 — keep the queue split and current. Superseded, historical, and stale findings belong in `docs/DONE.md`; every open item stays below about 5,000 characters and carries only its current requirement, current blocker, and acceptance gate.

Phase 2 — close the three sweeps through budget gates: error propagation, typed-attribute defaults, and client deadlines.

Phase 3 — complete the serial canonical-roster -> cold-metadata -> browser-transaction -> session-surface chain, while independent deployment, metrics, diagnostics, and endpoint/browser tracks proceed only where file/resource ownership is disjoint.

Phase 4 — complete the nine-item two-daemon migration through source retirement and net removal, not compatibility shells.

Phase 5 — collect authenticated real-browser and live rollout evidence with the user.

## Rules learned the expensive way on 2026-07-27 — these are not optional

Audit every recorded claim against current source BEFORE working the item. Three items in a row carried numbers that were wrong by large margins: the typed-attribute item claimed 110 sites and source had 49 with zero violations; the client-deadline item claimed a "single literal owner" and source had 30; the shared-state track's entire premise was disproved by measurement. Trust source over this queue, and correct the item in place when they disagree.

CO-TENANCY, not isolation, is the discriminator for a runtime failure. Three consecutive isolated passes prove nothing: the whole recovery family passed alone at `-n 4` and still failed under the gate. Reproduce with a co-tenant suite (recovery + `tests/test_browser_layout.py` was the discriminating pair) before calling anything a flake, and never defer an ID you have not reproduced that way.

Current contention-retry observability gap (2026-07-28): `tests/contention_retry_plugin.py` reports retries only through `terminalreporter`, which is `None` in xdist workers. A parallel gate log therefore cannot prove which marked test attempts retried; retry exhaustion also reaches xdist as an internal error rather than a normal test report. This belongs to the retry-plugin owner (dev7771), not to a product test.

Never reach green by weakening the measurement. No growing an allowlist, no raising `XDIST_BROWSER_WAIT_FLOOR_SECONDS`, no raising a deadline, no retries, no sleeps, no serializing tests. AMENDED 2026-07-28 by the user: lane concurrency for the Chrome-driving pools IS now halved, in `tools/check.py` `pytest_worker_counts()` (browser and e2e each take `budget // 6` instead of `budget // 3` and the remainder). This is a deliberate rule change, not a workaround: measured, one Chrome instance costs ~10 OS processes and several tests open more than one, so browser+e2e at 4+2 workers produced 113 Chromium processes and 11 concurrent instances on 24 cores, each worker also spawning a daemon, storaged and up to three webservers. The result was load 20+ and 39 `child failed to become ready (exit=None)` failures - children alive but too slow to meet a fixed readiness deadline. Right-sizing a pool to the hardware is not the same as relaxing an assertion to hide a defect. A daemon returning `unavailable` is the bug; waiting longer only waits longer for an answer that never comes. Fix the production owner, not the test.

Write the acceptance test FIRST for any sweep, so the count cannot grow. That single change took sweep closures from hours to minutes.

Re-check `git rev-parse --short=8 main` before every gate run and rebase onto local `main` whenever at a safe point — `main` moves under this worktree from other tracks, and it has been rewritten as well as advanced.

Tee every gate run to a file (`python3 tools/check.py 2>&1 | tee /tmp/yolomux-gate-<time>.log`). Reading a gate log directly moved the diagnosis twice; a result that exists only inside an agent transcript cannot be checked by anyone.

Before starting Phase 4, capture a workload snapshot — per-process CPU-seconds, peak RSS, and RPC latency on the existing scripted workloads. The migration's efficiency item and E1 are both unstarted, the package is currently net-negative on removal, and Phase 4 is the largest remaining block. Measure before spending it.

## Per-session discipline

One checkbox per fresh session, reading only that track file and directly referenced sources/specs. Restart after compaction or roughly an hour on one goal. Re-read the checkbox before editing; reproduce one concrete input; name the existing shared parent; record every measurement in the item; delete superseded findings in the same edit; build/test after the item; and land each stable state per the checkpoint rules once Phase 0 unblocks. Queue files are the status owner, not replies or internal task lists.

## Tracks

Ten track files completed and were retired on 2026-07-28 (browser-reliability, session-surfaces, deployment-lifecycle, diagnostics-health, and earlier: phase0-land, error-propagation, typed-attributes, client-deadlines, metrics-attribution, shared-state-ownership) (phase0-land, error-propagation, typed-attributes, client-deadlines, metrics-attribution, shared-state-ownership); their full text and evidence are archived in `docs/DONE.md` under "Retired DOIT track files". Do not re-create them.

| Phase | Track |
| --- | --- |
| 5 | [`DONE.release-evidence.md`](DONE.release-evidence.md) |

## Durable constraints

- `docs/specs/BACKEND_ARCHITECTURE.md` owns process topology and the non-blocking storage contract; `docs/specs/BACKEND_TEST_CONTRACT.md` owns cohort resources, first-delivery accounting, and acceptance.
- Parallel work is allowed only for complete disjoint file and mutable-resource manifests. A parallel-only failure is an ownership defect until the resource is named and disproved.
- Never serialize, retry, sleep, lower concurrency, stop unrelated services, or accept perpetual QUEUED to hide a collision or hang.
- Preserve unrelated dirty/staged work; never `git add -A`; use explicit path lists for any future commit only after user authorization and Phase 0 entry conditions.
- Completion means every track checkbox is checked, remaining evidence is in `docs/DONE.md`, the canonical gate and authorized live evidence are green, and every DOIT track plus this index is deleted in the same landed change.

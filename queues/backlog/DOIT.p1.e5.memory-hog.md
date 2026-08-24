# DOIT.p1.e5.memory-hog.md - Bound YO!stats and Filesystem memory

## Queue Lineage

- Authoritative queue: this file in `/home/keivenc/dev/yolomux.dev7771-unified`, branch `integration/v0.7.12-one-ai`, HEAD `2c1d0954ca9f6017e84189dc7db45b93f833fa62` when consolidated on 2026-08-23.
- Worked source: `/tmp/yolomux-0710-integration.2203800`, branch `integration/v0.7.12-20260821`, HEAD `929085bd7b4f708633683bc921bf8f8cb81e9ddf`; the source and unified queue bodies matched byte-for-byte before this lineage note and remain at 8/23.
- Status: unfinished and paused. The old source queue is removed after transfer; its dirty implementation worktree remains untouched.

## Goal

Keep YO!stats and Filesystem daemon memory bounded by their serving and watch configuration rather than by raw retained-history cardinality or indexed-tree size. A production-shaped cold start must publish usable results without a whole-history Python graph, and a large indexed root must not become an unbounded recursive native-watch topology. Release evidence must measure peak and steady PSS/USS, native watch cardinality, readiness, recovery, and user-visible correctness on one frozen identity.

## Measured incident baseline

The 2026-08-11 live 7771 measurement resolved the current service descriptors and process identities before sampling. The released processes ran from `/home/keivenc/dev/yolomux.stable7771` at `926e4a16621c6f96de319a441f8692742a97d856`.

| Service | Confirmed scale factor | RSS | PSS | USS/private | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: |
| YO!stats / `statsd` | 417.2 MiB SQLite; 609,688 primary fact rows; 149.0 MiB raw JSON | 574.0 MiB | 561.0 MiB | 560.9 MiB | 1,116.6 MiB |
| Filesystem / `watchd` | one recursive registration with 126,028 inotify watch descriptors | 155.5 MiB | 142.4 MiB | 142.0 MiB | 155.4 MiB |
| Combined | non-double-counting process denominator | 729.5 MiB | 703.5 MiB | 702.9 MiB | — |

For `statsd`, 560.8 MiB of PSS was anonymous and SQLite `mmap_size` was zero. The released cold path uses `fetchall()`, JSON-decodes all retained observations and usage atoms, builds a complete materialized generation, and retains the published generation and encoded views (`yolomux_lib/stats_current/storage.py`, `yolomux_lib/stats_current/service.py`). The exact reachable-object versus allocator-slack split is not yet measured, but the row-scaled whole-history build and 1.1 GiB high-water mark are confirmed.

For `watchd`, the released configuration unions every `indexed_dir` into recursive native `watch_paths`; `/home/keivenc/dev` therefore created 126,028 inotify descriptors. Event exclusion runs after native registration and cannot bound that topology (`yolomux_lib/watchd.py`). Kernel inotify memory is additional to process PSS. The current dirty candidate contains non-recursive/capped work in this area, but source intent is not test, deployment, or release proof.

## 2026-08-18 - statsd CPU Defect FOUND AND FIXED (uncommitted); Memory Scope Still Open

RESCOPED per audit: statsd first. The CPU half is done and measured; the MEMORY half of this queue is untouched.

HOT LOOP NAMED WITH ATTRIBUTION: `_append_uncovered_gap` (`yolomux_lib/stats_current/materializer.py:1685-1695`), reached via `_coverage_gaps` -> `_build` -> `update_generation`. Live 7772 statsd PID 842011 measured by `/proc/<pid>/stat` utime+stime deltas, never `ps %cpu`: 14.51 CPU-s over 17.79s wall = 81.6% of one core. A 40s py-spy profile on the pure incremental path (`build_generation` 0.00s) attributed `_build` 27.47s, `_coverage_gaps` 20.82s (52%), and `_append_uncovered_gap` 15.59s (39%) - the largest single owner. Fold path 4.69s, storage read 1.08s.

HYPOTHESIS EXPLICITLY DISPROVEN: the decode-amplification theory carried over from earlier memory work is WRONG for CPU. `storage.py:2402-2530` JSON decode is about 2.7% of profile, not the driver. Do not re-open it as a CPU cause.

CAUSE: the helper linearly rescanned every explicit span for every coverage epoch, O(epochs x spans) per build, at a live shape of 6 sources x 633 epochs x 589 spans and growing - measured N^1.66 with the retained window. `normalize_unavailable_spans` (`storage.py:301-346`) already guarantees spans are start-ordered and non-overlapping per `(family, source_id)`, an invariant clipping preserves, so the scan was unnecessary.

FIX: `bisect` seek over a per-source starts index. Two files, uncommitted in an isolated worktree at `7cb75e3a5`: `materializer.py` (+76/-24) and `tests/test_stats_current_materializer.py` (+164). Frozen-fixture equivalence: 3625 gaps, output byte-identical (`cmp` rc=0).

RED/GREEN VERIFIED FIRST-HAND BY THE MAIN AGENT: patched, 8 passed. With the product fix stashed and the tests kept, `test_coverage_gap_cost_stays_linear_in_retained_coverage_history` FAILS. On a pristine base it reports cost growing 3.81x for 2x history (0.1946s -> 0.7420s). The 6-case public `_coverage_gaps` matrix passes on BOTH base and patched, which is the point: it proves no semantic change rather than serving as the red.

BEFORE/AFTER, whole service, real `service.py`, frozen 381 MB copy of the live DB, readiness-gated, calibrated to the measured live rate of 4.00 appends/s with `source_generation` delta exactly +240 and zero RPC timeouts: base 55.9% / 55.7% / 55.3%, patched 27.4% / 26.9% / 28.4%. About 2x, 33.4 -> 16.5 CPU-s per 60s. Residual `_append_uncovered_gap` 0.19s, down from 15.59s.

INVALID EARLIER NUMBERS, WITHDRAWN: the first whole-service runs reporting 99.2% / 92.8% CPU and RSS 307->947 MiB are DISCARDED. `Observation` fields were passed in the wrong order so every append was rejected with `unknown current stats family 'jobd'`, and the response was never checked - only the startup full build was measured. The harness now asserts `accepted==2`, `duplicates==0`, and the generation delta.

REMAINING CPU, NAMED NOT HAND-WAVED: inside `_coverage_gaps`, `normalize_coverage_model` 3.01s and `identity_text` 3.39s per 60s, both re-run over the full 4023-epoch / 3534-span model every build. `_build_once` already caches NORMALIZED coverage and `_coverage_gaps` then re-normalizes it; that double-normalization is the next target. Builds DO coalesce (`_take_work` drains all pending), so about 2 builds/s is a consequence of build cost, not a queue defect.

STILL OPEN IN THIS QUEUE: the entire memory scope. Startup still schedules a full build; observations and usage still use whole-result `fetchall()`; RSS about 950 MiB with 1113 MiB high-water set by the startup full build, flat during steady state so NOT a leak; startup-to-ready 54-71s. The 24h read bound landed earlier in `f32ffd898` and must be preserved. Watchd and search work is separate and untouched.

NO LIVE CLAIM: 7772 was never restarted and is not fixed. Landing requires commit authorization, an authorized restart, and a post-restart measurement.

## LANDING BLOCKER 2026-08-18 - The statsd Regression Is A Load-Sensitive Timing Gate

The bisect production fix is sound and red-on-base still holds. The REGRESSION is the problem, and it blocks landing.

`test_coverage_gap_cost_stays_linear_in_retained_coverage_history` measures `_coverage_gaps` at 1500 and 3000 epochs with `time.process_time()`, takes `min` of 3 samples, and asserts `double / single < 2.8`. A perfectly linear algorithm scores 2.0, so the entire tolerance is 2.0 to 2.8, and the absolute durations are only about 0.015-0.018s, where scheduler and cache-contention noise dominate.

Observed on the PATCHED tree: a full-module run gave 61 passed / 1 failed at 2.80x against the strict `< 2.8`, and five isolated reruns gave 4 pass / 1 fail with the failure at 2.88x (0.0157s -> 0.0453s). Raw outputs retained at `/tmp/statsd-p0-materializer-pytest-20260818.txt` and `/tmp/statsd-p0-scaling-repeat-20260818.txt`.

A separate five-run measurement by the main context on a quieter box returned 5 pass / 0 fail. THAT DOES NOT CLEAR THE GATE. Non-reproduction on a quieter host is exactly what a load-sensitive threshold predicts, and it must not be used to reclassify the observed red. An earlier report of "8 passed" was a SINGLE run of a gate that is flaky by construction; any module-green claim from that run is withdrawn.

- [x] DONE: replaced the timing pass/fail with a DETERMINISTIC OPERATION-COUNT BOUND. The gate is now `_SeekOnlyGaps`, an explicit-span sequence whose `__iter__` raises outright and whose `__getitem__` accesses are counted; the count at 256/1024/4096/16384 retained spans must equal the count at 64 for a candidate overlapping two spans, under an absolute bound of 8. No clock is read anywhere in it, so it cannot flake with host load.
- [x] DONE: full module re-run independently by the main context at host loadavg 34 - 64 passed, rc=0. Exact new tests 3 passed / 61 deselected, rc=0. Forced-red against the old full-scan implementation: 3 failed, rc=1, so the new gate can fail.
- [ ] Refresh the patch backup and its hash after the test change.

FORBIDDEN REMEDIES, explicitly: raising the 2.8 threshold, retries, sleeps, serialization, lowering concurrency, or weakening the assertion. The gate must become deterministic, not more tolerant.

Integration order is unchanged: statsd remains first because it is disjoint. After the CPU patch is clean and integrated, profile the PATCHED service before choosing the next CPU target. Memory and startup remain a separate unresolved phase. Live 7772 must not be restarted as part of this correction; it remains old and hot at 85.0% user-heavy CPU with zero physical reads.

## Plan

- [ ] **Freeze production-scale reproductions and derive explicit budgets.** Create content-addressed fixtures and isolated subprocess harnesses for (a) the measured stats shape: approximately 417 MiB SQLite, 369,312 observations, 240,376 usage atoms, 149 MiB raw payload JSON, real indexes, aggregate ring, and WAL; and (b) a large nested indexed root that makes the released recursive watch owner exceed the intended registration bound. First prove the released behavior exceeds the proposed peak/steady PSS/USS and native-watch budgets. Measure stats phase-by-phase PSS/USS, allocated blocks, cache object/byte counts, and optional isolated `tracemalloc`/`malloc_info`; measure watchd at 0, 1k, 10k, and 100k directories plus kernel slab deltas. Derive budgets from the intended bounded architecture, not by rounding above the 703.5 MiB incident baseline. Keep raw outputs under `/tmp`; retain only fixture identities, summarized measurements, and exact repro commands.

PARTIAL 2026-08-19: independently verified raw evidence retained under `/tmp/status-7771b-budget-freeze.c44dhv` measured whole-history candidate PSS/USS at 1,264.6/1,264.4 MiB, an after-read slope of 1.454 KiB per observation plus 66.504 MiB, ring-only decode at 82.20 MiB PSS, and released recursive watch registrations of 1/1,001/10,001/100,001 descriptors. This does NOT close the item: source changed across the five-point run; the fixture has 2.24x observations, 0.26x usage atoms, 0.62x payload bytes, and no WAL; the harness retained `generation` and `store` after dropping the snapshot; ring decode did not exercise service caches/readiness; current watch behavior was modeled with `paths[:512]` instead of the product's `native_capacity_exceeded` refusal; and service cache counts, kernel slab deltas, actual readiness, exact source manifests, and daemon fallback remain missing. Proposed 128/160/256 MiB, 5 s, 64 MiB, and 1 s values are design targets, not accepted budgets.

- [ ] **Make the bounded aggregate ring the YO!stats cold serving owner.** On daemon start, validate and load the fixed aggregate-ring publication, publish a bounded usable generation, and catch up only facts newer than its durable cursor. Remove the requirement to decode the entire two-day raw store before first readiness. Preserve exact generation/cursor identity, atomic publication, raw-retention semantics, schema compatibility, corrupt-ring recovery, writer recovery, and last-known-good serving. Add exact tests for empty state, valid ring plus tail, missing/corrupt/stale ring, concurrent append, crash before/after publication, prune/VACUUM, and restart; assert first-ready correctness and latency without whole-history `fetchall()`.

- [ ] **Bound every exact YO!stats rebuild by rows, decoded bytes, memory, and lifetime.** If an exact raw-history rebuild remains necessary, fold it in bounded chunks and discard each decoded chunk after aggregation; never hold SQLite row tuples, decoded fact objects, the complete build graph, and the published generation simultaneously. Prefer a short-lived worker that publishes one identity-fenced bounded generation and exits so its allocator is reclaimed while the daemon serves last-known-good data. Enforce hard row/byte/memory/cancellation budgets, stale-worker rejection, exception cleanup, and atomic adoption. Prove 1x versus 2x retained-row cardinality does not make steady serving memory grow linearly, peak decoded rows/bytes stay within the configured chunk bound, no post-response error is emitted after valid publication, and Linux-only allocator trimming is at most a cleanup optimization rather than the fix.

- [ ] **Separate shallow native freshness from large indexed-root reconciliation.** Keep immediate native notification only for visible/open Finder directories and exact-file parents. Do not recursively register configured Quick Open/indexed roots; cover them through the existing BFS/frontier, mutation evidence, and bounded periodic reconciliation owners. Apply exclusion before registration where a native subtree is eligible, enforce one daemon-wide registration union and explicit cap (the current candidate uses 512), and provide a typed bounded fallback when the cap or platform facility is unavailable. Audit the current dirty `watchd`/protocol candidate rather than creating a parallel owner. Verify overlapping descriptors, symlinks, VCS/cache/dependency exclusions, root add/remove/repoint, policy changes, Linux and Darwin behavior, native failure/recovery, daemon restart, and no lost visible-directory or exact-file updates.

- [ ] **Expose honest per-daemon memory and registration readiness.** Add one side-effect-free measured projection for peak and steady PSS/USS, anonymous/file PSS, swap, threads, FDs, backing DB/WAL/TEMP sizes, stats build phase/cursor/bounded-work counters, watchd native-registration count/cap/fallback, and process/start/composition identity. Reconcile internal retained-owner accounting to process USS within a documented tolerance rather than reporting only encoded wire-cache bytes. Make `/livez` the narrow process-progress check and `/readyz` fail closed until every required daemon has the correct identity and its serving generation, memory budget, registration cap or typed fallback, and recovery state are healthy. Do not make readiness depend only on a listener or process existing.

- [ ] **Add daemon-specific regression and resource gates.** Extend the isolated gate to fail on statsd cold peak/steady PSS/USS, watchd native watch-descriptor count, swap, readiness latency, and a positive post-settle memory/watch slope. Count `inotify wd:` registrations, not only inotify instances or host maxima. Include negative controls that run the released whole-history/recursive-registration paths and prove the new gates fail for the incident class. Cover startup, incremental append/change, idle, prune, crash/restart, stale descriptor cleanup, failed native registration with bounded fallback, web restart while daemons exist, and full owned-process/FD/watch retirement. Do not weaken the test with retries, sleeps, serialization, wider budgets, or manual cache deletion.

- [ ] **Certify the all-daemon release on one frozen identity.** After the implementation groups are complete, create the separately authorized freeze commit and run focused owner tests, `python3 tools/static_build.py --check`, and default `python3 tools/check.py` from fresh clean Linux and real Darwin checkouts of the same full SHA. Use empty and production-sized stats state plus maximum allowed indexed-root state. Prove generated/source content identity, install, rollback/backout, daemon crash/recovery, all owned-process retirement, every daemon ready before browser launch, and no foreign-process interference. Deploy only the authorized development target, prove PID/cwd/SHA and daemon composition identity, wait beyond 90 seconds, then run at least a 600-second authenticated soak that records absolute ceilings and near-zero slope for PSS/USS, watch count, caches, TEMP/WAL, FDs, threads, and readiness. Exercise exact YO!stats and Filesystem user paths with a real negative control. Any relevant post-freeze edit invalidates downstream evidence.

- [ ] **Document the bounded ownership and archive the completed queue.** Update `README.md`, `docs/DEVELOPMENT.md`, the relevant YO!stats/watchd/search specifications, and the GUI coverage map with the serving-ring owner, rebuild lifecycle, shallow native-watch versus reconciliation split, memory/watch budgets, `/livez` versus `/readyz`, operator diagnostics, and supported recovery behavior. Record measured before/after outcomes and the frozen release identity in `docs/DONE/`, then remove this queue only after every checkbox and release rung above is proven.

## Gotchas

- PSS is the aggregate release denominator; summing RSS double-counts shared pages. Track peak and steady USS separately because both incident processes were almost entirely private anonymous memory.
- A 417.2 MiB SQLite file does not explain statsd RSS by mmap: the measured `mmap_size` was zero and file PSS was only 0.2 MiB. Do not “fix” this by changing mmap or cache size without reproducing the Python object graph.
- The three deleted SQLite TEMP files totaled 64.3 MiB but were disk-backed and unmapped. They affect disk/I/O, not the 560.8 MiB anonymous statsd PSS.
- A short observation established stable retention, not absence of a slow leak. Release proof needs absolute budgets plus slope after settle.
- Event filtering is not registration pruning. A single inotify instance with 126,028 `inotify wd:` entries is not healthy because instance count is one.
- Kernel inotify memory is outside process PSS/USS. Measure both process memory and host slab changes in the isolated cardinality experiment.
- Do not treat the current non-recursive/capped `watchd` diff as closed until its exact scale regression, full gate, same-SHA deployment, and live registration reduction are proven.
- Do not add a second stats builder, watch service, health bus, or readiness owner. Extend the existing ring/materialization, `watchd`, local-service projection, and launcher/readiness paths.
- No live profiler attachment or diagnostic signal is required. Perform heap and cardinality attribution in isolated subprocesses using frozen fixtures.

## Done Criteria

- The production-shaped released-path reproductions fail the new resource gates, and the final frozen candidate passes them with recorded fixture and artifact identities.
- Statsd first readiness and steady serving memory are bounded by aggregate-ring/view/chunk configuration rather than raw retained-row cardinality; exact rebuild peak work is bounded and atomically published.
- Watchd native registrations stay within the declared cap and scale with shallow visible/exact parents rather than total indexed-tree directories, while indexed-root freshness and mutation convergence remain correct.
- `/readyz` proves every required daemon's identity, bounded serving state, memory/watch contract, and recovery state before browser launch; `/livez` remains a distinct progress signal.
- Fresh clean Linux and Darwin checks pass on the same frozen SHA; the authorized development deployment proves identity, settle, authenticated soak, negative control, exact YO!stats/Filesystem paths, and retirement without post-freeze edits.
- Documentation and `docs/DONE/` record the before/after measurements, budgets, architecture, and release evidence; this queue is removed only after those records are complete.

NOTE ON METHOD: the owner attempted to prove load-independence by spawning 16 orphaned busy loops (PIDs 419050-419066, PPID 1, 150s) after an isolation guard refused its first saturation attempt. Those were terminated externally and none remain. That approach was both unsafe on a shared machine hosting live servers and unnecessary, because the replacement proof is structural and cannot vary with load. The independent re-measurement above was taken at loadavg 34 without generating any load.

## 2026-08-18 LIVE EVIDENCE ON THE PATCHED PROCESS - Seek Fix Confirmed, Residual Reattributed

RUNNING MODULE IDENTITY PROVEN, not inferred from start time. The `.pyc` that import consumed timestamp-validates against the on-disk source: embedded source mtime `1787100057` equals disk mtime, embedded size `70123` equals disk size, flags 0, magic equals `importlib.util.MAGIC_NUMBER`, VALIDATES True. The compiled `_append_uncovered_gap` has argcount 4 with `bisect`/`bisect_right` in `co_names`. Chain: source 17:40:57 < pyc write 18:00:26.516 < process start 18:00:32.

WHY IT REACHED A LIVE PROCESS WITHOUT A RESTART: statsd runs `--idle-seconds 60.0`, so it exits when idle and RESPAWNS FROM DISK, picking up new module code with no listener restart. The listener PID 840893 is separate, started 15:40:39 before the patched file at 17:40:57, still runs old in-process code, and was measured at 111.6% and 45.4% of a core - currently a LARGER consumer than statsd and untouched by this patch. Consequently a 7772 restart changes the LISTENER's code, not statsd's; any post-restart improvement must NOT be attributed to this patch, since restart hygiene alone drops caches, resets the WAL, and re-derives generations. The clean separator is a restart onto a SHA without `10b44118b`, sampled identically, requiring separate authorization.

VERSION PROOF CANNOT USE SERVED ARTIFACTS: the patch's own correctness evidence is byte-identical fixture output across 3625 gaps, and `/api/ping` carries no version field, so no response can distinguish patched from unpatched.

45-SECOND py-spy RECORD of live PID 2216107 (1943 samples, 226 errors, ~10% loss inherent to `--nonblocking`; loadavg 39.86 falling to 30.93, so SHARES are usable and absolute CPU is coarse):

| function | cumulative share | s per 60s CPU |
| --- | ---: | ---: |
| `_worker_loop` | 90.6% | 26.0 |
| `_build_once` | 74.3% | 21.3 |
| `_build` | 55.4% | 15.9 |
| `_coverage_gaps` | 37.1% | 10.6 |
| `identity_text` | 13.5% | 3.87 |
| `normalize_coverage_model` | 12.0% | 3.44 |
| `_clip_gaps` | 11.1% | 3.18 |
| `_flush_ring_if_due` | 10.8% | 3.09 |
| `_updated_layer_buckets` | 8.0% | 2.29 |
| `iterencode` | 4.7% | 1.35 |
| `_append_uncovered_gap` | 0.7% | 0.20 |

THE SEEK PATCH IS CONFIRMED IN SITU: `_append_uncovered_gap` is 0.7% of process CPU, down from 39% pre-patch. It is no longer a contributor.

DOUBLE-NORMALIZATION IS CONFIRMED REAL AND REDUNDANT BUT REFUTED AS DOMINANT. `_build_once` (`service.py:2066`) normalizes and caches, then hands the same snapshot to `_build` -> `_coverage_gaps`, which normalizes again at `materializer.py:1580`. Proven idempotent on the frozen 4023-epoch / 3534-span fixture: pass1 12.3 ms, pass2 9.0 ms, identical output, so the second pass is pure waste and safe to delete. But the caller split of 232 `normalize_coverage_model` samples is 106 under `_coverage_gaps` - the redundant one, about 5.5% or 1.6s per 60s - versus 126 under `_merge_cached_coverage`, a separate NON-redundant path. Deleting the redundancy buys about 5.5%.

THE BIGGER RECOVERABLE ITEM IS `identity_text` REVALIDATION: 221 of 261 `identity_text` samples have immediate parent `_coverage_gaps` at `materializer.py:1679`, the post-hoc loop re-validating family, source_id, epoch_id, and reason on EVERY produced `NoData` after the gaps are already built - 11.4% of process CPU, about 3.3s per 60s, re-validating strings validated on the way in. `identity_text` under `normalize_coverage_model` is ZERO samples, so the two costs are INDEPENDENT and any framing that billed identity to normalization was wrong.

- [ ] Delete the redundant `_coverage_gaps` normalization pass with byte-identical generation proof. Worth about 5.5%.
- [ ] Validate identities ONCE at the owning ingestion or normalization boundary and drop the `materializer.py:1679` revalidation sweep, preserving control-character and UTF-8 byte-limit semantics. Worth about 11.4%.
- [ ] Then attack `_fold_bucket` refolding and the remaining `_clip_gaps`/`_append_gap` cost, which still holds roughly 20% after both.

OWNERSHIP: `materializer.py` gets exactly ONE writer; every other agent stays read-only and hands over tests and design only.

MEASUREMENT RULE: match A/B by ACCEPTED APPENDS AND BUILDS, not wall time alone. An earlier harness ran 18.8 appends/s against a live rate of about 4/s and its headline numbers were withdrawn.

NOT ESTABLISHED: the 2.05x isolated A/B was not re-run and no timing conclusion is drawn at load 31-40; whether other statsd instances at 31.8-83.2% run the same code, since identity was proven only for PID 2216107; and in-memory code-object verification, since the proof is the validated pyc plus profile line-number agreement.

## 2026-08-18 SECOND INDEPENDENT RESIDUAL AUDIT - No Root Cause Is Established Yet

An independent audit at snapshot `77adf861a` supersedes the confidence of the attribution above. Two prior framings are now qualified: neither double-normalization NOR the identity revalidation sweep is established as the primary residual, and the largest sampled owner is bucket folding.

REDUNDANT NORMALIZATION IS PROVEN DETERMINISTICALLY, WITHOUT TIMING. A normalization counter over the large-coverage fixture records `[(22243, 0), (6066, 0), (6066, 0)]`: the raw cold coverage pass, then the compacted model normalized TWICE. That is proof of the duplicate pass independent of any profiler.

BUT IT IS NOT PROVEN TO BE THE ROOT CAUSE. In a bounded 15-second `py-spy` capture of live PID 2216107 (1150 samples, 2 read errors, post-capture CPU 68.8%): `_build_once` 81.48%, `_fold_bucket` 33.22%, `_coverage_gaps` 19.30%, `identity_text` 12.52%, `storage.read` 11.04%. Normalization is 103 of 1150 samples, about 9.0%, while BUCKET FOLDING is 382 of 1150 at 33.22%. Top leaves: `identity.py:31` 8.17%, `storage.py:2412` 6.78%.

REJECTED RECONCILIATIONS, each measured rather than argued:
- Materializer backlog and an inflated build rate are REJECTED: 39 incremental builds in 20.002s is 1.95/s, with full/stale/failed at 1/0/0 and queue depth, dirty, and building at 0/0/false.
- Ring flushes are REJECTED as an explanation: 420-481 ms at a 10 s cadence is at most 4.2-4.81 CPU points, nowhere near the observed burn.
- Data shape and cadence are REJECTED as sufficient: the live model is 5315 coverage epochs and 5046 unavailable spans, 1.355x and 1.38x the harness shapes. Scaling the 27.15% patched-harness mean linearly gives 37.5% before cadence and 26.2% after the measured live cadence of 2.79/s - it cannot reach the observed 76.2%.

SAFETY CORRECTION TO THE EARLIER RECOMMENDATION: `identity_text` validates four strings per final no-data gap, and REMOVING IT IS UNSAFE until the durable-storage trust boundary is explicitly changed. The earlier note here proposing to drop the `materializer.py:1679` revalidation sweep must not be actioned as written.

RECOMMENDED FIRST CHANGE, once ONE owner is assigned - do not implement before then. Introduce a materializer-owned typed `CoverageModel` whose only constructor is `normalize_coverage_model`; raw public `build_generation` and `update_generation` keep their normalization adapter; service `_build_once` caches and passes the typed normalized model; and `_coverage_gaps_from_model` consumes it without normalizing again. Do NOT use an untyped boolean flag, which can falsely claim raw coverage is normalized.

- [x] Extend `test_incremental_build_reuses_compacted_legacy_coverage_model` in `tests/test_stats_current_service.py` to spy `_coalesce_coverage_epochs` across one cold build and one observation-only warm incremental build. Required count `[22243]`; current code fails with `[22243, 6066, 6066]`. Compare a fixed selected `CacheEntry.binary` against the raw public-builder reference so WIRE BYTES, not only decoded values, stay identical. DONE: the typed `CoverageModel` path records exactly `[22243]`, and the fixed 300s/1s `CacheEntry.binary` matches the raw public-builder reference byte-for-byte; independent verification ran the exact 10-test subset and the 7-test service coverage subset, both exit 0.
- [x] Retain the raw-overlapping unavailable-span matrix and the direct raw-builder normalization tests in `tests/test_stats_current_materializer.py`; they protect callers that do not come through the service cache. DONE: the direct raw build/update normalization tests and six-case overlapping/touching/duplicate/nesting unavailable-span matrix remain unchanged and passed in the independent 10-test subset.
- [x] Measure ONE live-equivalent paired ablation before reporting any CPU resolution: exact live model and cadence, current versus typed-cache path, requested and obtained profile samples with errors, per-build counters and timing for normalization, `_coverage_gaps`, and `identity_text`, plus output-byte equality. DONE: independent audit verified one exact B/C/C/B block over an immutable 435,871,744-byte fixture, 39/39 accepted appends per arm at the live-equivalent cadence, one cold plus 39 incremental builds, and byte-identical output for all 40 selected generations. The typed path removed all 39 warm normalization calls; requested 100 Hz profiles obtained 4,227 samples/1 error on baseline and 4,155/3 on candidate. The nominal 5.4078% warm CPU reduction is explicitly NOT accepted as CPU-resolution evidence because CPU 13 was not isolated or `nohz_full`, competitor exclusion was absent, selected-CPU non-idle was 99.24-99.83%, and host load rose 11.394 to 19.063.
- [x] Only after that measurement, evaluate a request-local prepared-observation cache in materializer `_build`, so one stored observation selected for four resolutions calls `_observation_samples` and `validate_payload` once while preserving malformed-persisted-data failure. It must be shared by full and incremental builders; do NOT add an incremental-only cache. DONE: both public full and incremental builders route through the same `_build`-local `_PreparedObservationCache`; one observation selected at four resolutions now calls `_observation_samples` and `validate_payload` exactly 1/1 per build instead of forced-legacy 4/4. Independent verification matched selected 300s/1s wire bytes to the uncached fold, proved malformed payloads still raise before insertion, and forced an identity collision to fail closed. The 3 exact regressions, all 67 materializer tests, and all 116 service tests passed; negative search found no persistent, global, service, or incremental-only sibling cache.

WHAT REMAINS UNVERIFIED, stated by the auditor: no candidate change was implemented or tested red-to-green; the profile is a single 15-second interval with two read errors at host load above 40, so it cannot establish exclusive CPU shares or wall-clock causality. A matched live database and cadence ablation is still required to decide whether duplicate normalization, global coverage and gap filtering, bucket folding, or another listener-owned path is the primary remaining owner.

## 2026-08-18 ADVERSARIAL AUDIT v3 - THE BOUND IS NOT ACHIEVED AND THE ACCOUNTING IS FALSE-SAFE

Three P1 findings against the uncommitted bounded-read candidate. Any earlier framing here that the leak question is answered or that the read is bounded is WITHDRAWN.

P1 - THE STATED BOUND EXCLUDES UNBOUNDED PATHS. `Store.pinned_snapshot()` still materializes the ENTIRE 24-hour decoded `StoreSnapshot`: the new cursor fold releases each raw observation and usage row, but every DECODED atom is retained in lists before the snapshot is constructed. That is an unmeasured peak proportional to the window, not a steady bound. Coverage epochs, predecessor rows, and unavailable spans remain whole-result `fetchall()` reads whose schemas enforce NO source or row cardinality cap - an adversarial store returned 129 coverage, 129 unavailable, and 129 migration rows inside one bounded-window snapshot. The candidate's own comment calls coverage and unavailable deliberate exceptions, but the asserted source-count and outage-count rationale is NOT an enforceable bound. `migration_reconciliation.details_json` is likewise decoded and retained without pruning; converting it from `fetchall()` to cursor iteration removed the whole-result raw list but not the unbounded decoded field, and the materializer does not even consume it.

- [x] Define an ENFORCEABLE cardinality and byte policy for coverage, unavailable, and reconciliation data, then test that the policy rejects or truncates adversarial source, outage, and reconciliation churn. Then measure copied-live-DB cold and incremental build PEAK separately from steady RETAINED graph size. DONE: typed row/byte policies reject oversized raw rows before decode, amplification-only overflow after one decode, invalid limits, and predecessor reads that exceed either limit; focused 31/31, storage 96/96, and service 116/116 passed, while forced legacy failed 7/31 and fixed build artifacts remained byte-identical. This credits only the three metadata families; the broader copied-live-DB cold/incremental peak and retained-graph measurement remains open below.

P1 - `last_read` IS A FALSE-SAFE ACCOUNTING CONTRACT. It counts only observation and usage rows and their payload widths. A snapshot with NO facts but substantial coverage and unavailable data reports `raw_rows_fetched=0` and `max_resident_raw_bytes=0` while those whole-result rows are resident. Reconciliation `details_json` bypasses `_fold_rows` entirely, so its bytes can be decoded while neither counter reports the widest raw payload. The telemetry therefore CANNOT prove the bound it is offered as evidence for.

- [x] Extend the contract to cover header and schema metadata, observations, usage, reconciliation, and the conditional coverage, unavailable, and predecessor reads. Record deliberate skips for `include_coverage=False`, record per-table attempted, success, and failed outcomes, and never advance a complete-read marker on a SQL or decode failure. Red regression: use a SQLite authorizer to deny one table at a time and corrupt one JSON payload. Ring and diagnostic APIs must not mutate these snapshot counters without a separately named scope. DONE: one `SnapshotReadAccounting` owner now records all eight read scopes, explicit conditional skips, consumed rows/bytes on failures, and fresh completion state per callable invocation; SQLite authorizer denials, corrupt JSON, predecessor failure, and immutable ring/diagnostic non-mutation are covered. Independent review found and fixed four initial gaps, then 18 focused, 114 storage, 116 service, and 8 stats non-browser gate tests passed; a forced false-complete mutation failed all 11 critical rows before exact hashes were restored. The accounting remains Store-local and does not establish the still-open whole-snapshot memory bound.

P1 - CACHE BYTE COUNTERS OMIT THE RETAINED OBJECT GRAPHS. `StoreSnapshot` is local to `_build_once` and is not permanently retained after a successful build, which is a point in the candidate's favour. But `PublishedCache` retains its `Generation` and entries, `Generation` retains materialized layers and private overlays, and ring views retain snapshot, base, and delta entries. `status.cache.shared_bytes` and `private_bytes` sum only encoded `CacheEntry.binary` lengths, so they do not quantify retained statsd memory and cannot support any conclusion that this change removed its memory cost.

- [ ] On a copied representative database, capture `tracemalloc` current and peak around cold and repeated incremental `_build_once` calls, then run one visited-id deep-size traversal rooted at `_cache`, `_delta_entries`, `_ring_views`, and `_snapshot_body_decoration_cache`. Report ENCODED bytes and RETAINED Python object bytes separately.

P2 - THE ARENA CAP TEST PROVES ORDERING, NOT EFFECT. No fresh-exec defect was found: `cap_malloc_arenas()` is called before `StatsCurrentService` construction and worker startup. But the cap is NOT retroactive - a 32-thread contention probe created 33 heaps, and a later successful `mallopt(M_ARENA_MAX, 2)` left all 33 in place. The current mock verifies call-before-constructor only.

- [ ] Add a fresh-subprocess `service.main` regression with an allocation-contending constructor asserting at most two heaps, plus a negative pre-main control that remains above two.

REVIEWED AND CLEAN: `_DirtyIntervalGate` ordering, `None` and empty handling, SQL exactness, and its greater-than-64-interval envelope path - no defect found. `tests/test_gate_stats_bounded_read.py` 26 passed in 6.05s; focused allocator tests 9 passed, 112 deselected; focused storage checks 3/3.

NOT RUN IN THIS AUDIT, so none of the residual findings above are settled: the full suite, the copied-live-DB `tracemalloc` measurement, the cache deep-size walk, the schema-cap implementation, and the injected read-failure test.

## 2026-08-18 - TWO DIFFERENT QUESTIONS, NEITHER SETTLED

The plateau result and the adversarial P1 findings look contradictory and are not. They answer different questions, and conflating them is how a premature close would happen.

QUESTION A, GROWTH TREND OVER TIME - "does RSS climb without bound?" The plateau evidence addresses this: a two-level oscillation flat across cycles 20-290, `allocated_blocks` +0.19% over 170 cycles, capped and uncapped arms matched in shape with the capped arm a few MiB lower. Its own stated limits: the fixture is SYNTHETIC at 245 MiB rather than the 436 MB live-shaped database; the late step in the uncapped arm from cycle ~300 could not be separated from external machine load; and the capped arm ended at exit 143, a deliberate SIGTERM to free a loaded box, not a failure.

QUESTION B, PEAK BOUND PER READ - "is a single read bounded?" The adversarial audit addresses this and the answer is NO. The decoded `StoreSnapshot` is still materialized whole for the 24h window; coverage, predecessor, and unavailable-span reads remain whole-result `fetchall()` with no enforceable schema cap; and `last_read` cannot even detect a violation because it counts only observation and usage rows.

NEITHER RESULT SETTLES THE OTHER. A workload can plateau in steady state while every individual read still has an unbounded peak - that is exactly the shape here. The queue must not be closed, and the word "bounded" must not be used, until Question B has an enforceable policy with a red regression AND Question A has been re-measured on the copied live-shaped database rather than the synthetic fixture.

## 2026-08-18 PAIRED ABLATION v5 - ACCEPTED RESULT, ISOLATED PATH ONLY

The double-normalization removal is measured at last, with a real paired design. QUALIFIED FOR IMPLEMENTATION REVIEW, NOT DEPLOYMENT.

RESULT: three warm incremental builds went from mean process CPU 0.196865 s to 0.179935 s per trial - a 0.016930 s reduction, 8.60%. Across 12 paired ABBA blocks and 24 fresh-process trials, the paired mean reduction 95% t interval was 0.008834 to 0.025026 s. ONE PAIR FAVOURED BASELINE by 0.012099 s and is reported rather than dropped; the prespecified paired aggregate stayed positive.

ADMISSION WAS EARNED, NOT ASSUMED: lin2 passed three five-second preflight AND postflight samples with every CPU 0-23 below 20% non-benchmark utilization. Each trial pinned to CPU 0, private copy of the sealed snapshot (SHA-256 `ac6e1dc7...642ad`, 435,871,744 bytes), one full build then three timed incremental append/build cycles, process CPU time as the primary metric.

EQUIVALENCE PROVEN: all 24 trials produced byte-identical cache, delta, ring, and request-body digests, with matching counters - one full plus three incremental builds, and a final incremental read of 414 observations, 20 usage atoms, 1 reconciliation row, 435 raw rows fetched, 33,239 decoded payload bytes, 2,167 max resident raw bytes. The candidate touched only duplicate normalization: service-produced normalized coverage tuples are typed tags and the second `_coverage_gaps` normalization returns them unchanged, while raw public builder inputs stay untagged on the original adapter.

REJECTION LEDGER, kept rather than buried:
- v3 was rejected BEFORE any trial: the temporary runner lacked the remote `PYTHONPATH`, then used the wrong private database filename. Corrected and independently smoke-tested before v5.
- v4 was deliberately ABORTED at rc=143 when a separate non-benchmark triage process appeared on the pinned CPU during trial 6. Its timings are EXCLUDED. That contaminating process was the coordinator's own read-only ablation triage running on the same host - a self-inflicted contamination, recorded so the exclusion is not mistaken for an unexplained anomaly.

SCOPE LIMITS, stated by the runner: this was an in-memory temporary harness change under `/tmp` and `agent-comm/jobs`. IT IS NOT A SOURCE PATCH. No claim is made about live daemon CPU, wall-clock responsiveness, allocator or RSS effects, deployment, or a production source change.

CONSISTENCY CHECK: 8.60% on the isolated warm-incremental path sits close to the earlier sample-share attribution of about 5.5% for the redundant normalization pass, so the two independent methods agree in magnitude. It does NOT resolve the residual - bucket folding at about 33% and identity validation remain the larger owners, and those are untouched by this candidate.

- [ ] Convert the harness-proven typed-tag approach into a reviewed SOURCE change with byte-identical generation proof, then re-measure. The measurement is done; the implementation is not.

## 2026-08-18 v5 ABLATION REJECTED - IT SPANS SOURCE REVISIONS. THE PRECEDING SECTION IS WITHDRAWN.

The 8.60% result recorded immediately above is INVALID PERFORMANCE EVIDENCE and must not be accepted, implemented from, or cited. Its `accepted: true` flag and its arithmetic are both beside the point: the measurement was not of a fixed subject.

WHY, verified independently: the runner imports the LIVE `agent-a48c...` worktree on every trial rather than an immutable copy, and `yolomux_lib/stats_current/storage.py` has mtime 19:16:19 PT while the v5 run launched at 19:07:22 PT. The source changed UNDERNEATH the running sweep. No launch-time diff or source hash was sealed, so no trial can be attributed to a known revision. Baseline and candidate trials may have executed different code.

SECOND DEFECT: the run used housekeeping CPU 0, where a single 10-second trial recorded 185 INVOLUNTARY CONTEXT SWITCHES. Pinning to a housekeeping CPU does not isolate; it guarantees interference.

REQUIREMENTS FOR v6, all of them, before any result is considered:
- IMMUTABLE COPIED SOURCE for the whole sweep. Never import a live worktree an owner is still editing.
- Base, diff, and runner HASHES captured pre-run AND post-run, and compared. A run whose source hash moved is discarded, not adjusted.
- ONE NON-HOUSEKEEPING CPU, with same-CPU pre and post admission samples and PER-TRIAL contamination evidence rather than only preflight and postflight.
- EXPLICIT statistical acceptance criteria fixed before the run.

WHAT SURVIVES FROM v5: nothing quantitative. The byte-identical digest equivalence across trials is also unreliable, because it too was produced against a moving source. The earlier sample-share attribution of roughly 5.5% for the redundant normalization pass stands on its own separate evidence and is unaffected by this rejection.

The self-inflicted v4 contamination recorded above remains accurate and is unrelated to this rejection.

## 2026-08-18 P1 RESOLUTIONS - TWO FIXED, ONE HONESTLY ENCODED AS UNFIXED

COORDINATOR CORRECTION FIRST: the paired-ablation runner, its `PYTHONPATH` self-assignment, the swallowed `CalledProcessError`, and the `/tmp/statsd-paired-ablation-v*` directories belong to a DIFFERENT agent on keivenc-linux2. The memory-scope owner is on keivenc-linux1, wrote six named harness scripts with no subprocess-per-trial or ABBA design, and uses a 256,860,160-byte fixture at `/tmp/statsmem/fixture/`. The 435,871,744-byte database is `/tmp/yolomux-statsd-paired-ablation.mA4gZN/stats-v7.sqlite3`, another owner's. The coordinator misrouted those harness fixes; the ablation defects are not this owner's to fix.

P1 FINDING 2, UNCAPPED `fetchall()` ON COVERAGE, PREDECESSORS, AND UNAVAILABLE SPANS - FIXED. The owner accepted it as worse than an oversight: a comment had ASSERTED their "cardinality is bounded by source count and outage count" with nothing enforcing it, while `coverage_epochs` mints a row per owner-generation change and `unavailable_spans` grows with outages across 48h retention. All three now stream and decode row by row. Verified independently: `pinned_snapshot` contains ZERO `.fetchall()` calls, and that count is pinned by a negative-control test rather than left to review. Coverage dedup and sort now run on decoded objects with predecessor-wins precedence preserved.

P1 FINDING 3, `last_read` COULD NOT DETECT A VIOLATION - FIXED, and fixed in the right shape. Accounting now carries per-table counts for `coverage_epochs`, `coverage_predecessors`, `unavailable_spans`, and `migration_reconciliation`, and `test_every_read_in_the_closure_reports_its_own_row_count` asserts those per-table counts SUM EXACTLY to `raw_rows_fetched`. An uncounted future read therefore fails the test rather than passing silently - the detector can now detect its own blind spot. Verified live on the production-shaped fixture: 184,649 + 120,188 + 15 + 0 + 0 + 1 = 304,853 = `raw_rows_fetched`.

P1 FINDING 1, THE DECODED SNAPSHOT IS STILL MATERIALIZED WHOLE - NOT FIXED, and now honestly encoded. It requires the serving-ring owner that does not exist. Rather than leaving the word ambiguous, the owner removed the claim everywhere: `tests/test_gate_stats_bounded_read.py` is renamed `tests/test_gate_stats_streamed_read.py`, four test names dropped "bounded", and the module header plus `last_read_accounting()` docstring now state that the counters describe what the read DID and are NOT evidence that it was bounded. A new `test_the_decoded_snapshot_is_explicitly_not_bounded` asserts that doubling rows doubles decoded payload, between 1.9x and 2.1x - encoding the unfixed defect as a PASSING test that must be DELETED, not relaxed, when the ring owner lands. That is the correct way to carry a known gap: a green test whose removal is the signal.

WHAT IS ACTUALLY ENFORCED: exactly one property - raw SQLite rows are never resident beyond one at a time, for every table. Nothing more should be claimed.

EXIT CODES: unpatched base 24 failed / 7 passed, rc=1, with the four new coverage, predecessor, unavailable, and accounting tests all red. Patched 31 passed, rc=0. Full suite 488 passed, 1 skipped, 1 xfailed, rc=0. Transient peak unchanged by the rewrite at 525,000 / 524,932 / 524,944 KiB against base 644,540 / 646,152 / 646,952.

DURABILITY: `tests/test_gate_stats_streamed_read.py` is UNTRACKED and is the entire proof of this change. It needs an explicit `git add` at integration or it vanishes from a file-list commit. Base `07c0e075e`, uncommitted.

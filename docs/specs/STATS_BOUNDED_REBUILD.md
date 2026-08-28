# Bounded exact rebuild — specification

Status: specification only, nothing here is implemented. Written for `YOLO-V0717-E3-REBUILD-28` against `3d1fe4da8`.

## What this fixes, in one paragraph

A cold statsd rebuild ends with a process holding **1,470 MiB of private memory to serve a 53 MiB answer**. The memory is not leaked and it is not held by any cache: `sys._debugmallocstats()` shows **1,411 pymalloc arenas retained for 49.8 MiB of allocated blocks, with exactly one arena ever returned out of a 1,412 high-water**. The build allocates a ~1.2 GiB transient graph, keeps ~570,000 small survivor objects scattered through it, and CPython cannot return a 1 MiB arena while a single live object sits in it. This specification bounds the rebuild so the survivors are never scattered across more arenas than they need.

## Two corrections to the queue item, both measured

### 1. Chunked folding works. The short-lived worker is not needed.

The queue prefers "a short-lived worker that publishes one identity-fenced bounded generation and exits", on the theory that chunking might not help because survivors are interleaved with transients regardless. **That theory is false.** A probe that reproduces the exact shape — a large transient graph of small objects, with survivors born among them — gives:

| design | peak | retained | arenas | live | **arena MiB per live MiB** | arenas reclaimed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| interleaved, unchunked (today's shape) | 397.50 MiB | 385.56 MiB | 393 | 22.98 MiB | **17.10** | **0** |
| chunked, 10,000 rows/chunk | 32.74 MiB | 25.38 MiB | 29 | 22.01 MiB | **1.32** | 4 |
| chunked, 2,500 rows/chunk | 28.12 MiB | 20.77 MiB | 25 | 21.98 MiB | **1.14** | — |
| survivors allocated alone (ideal floor) | 22.55 MiB | 15.37 MiB | 19 | 18.19 MiB | **1.04** | 0 |

Chunking takes retained memory from 385.56 MiB to 25.38 MiB — **93.4% of the fragmentation removed** — and lands within 27% of the floor that a separate process could reach. The unchunked probe reclaims **zero** arenas out of 393, matching the production observation of one out of 1,412.

**The mechanism is arena high-water, not peak transient bytes.** pymalloc allocates from partially-used arenas in preference to new ones. When the transients are freed every chunk, the high-water never grows (29 arenas versus 393), so each chunk's survivors are packed into the same small set of arenas instead of being sprinkled across hundreds. That is why chunking helps even though survivors are still interleaved with transients *inside* a chunk.

**Consequence: do not build the short-lived worker.** It buys the last 1.32 → 1.04 of overhead ratio in exchange for a second process, a serialization boundary, a crash-before-publish state machine and a new supervision surface. That is not a good trade, and section "Cost" prices it.

### 2. The acceptance criterion as written cannot be met, and a correct implementation would fail it

The item asks to "prove 1x versus 2x retained-row cardinality does not make steady serving memory grow linearly". Measured at both cardinalities:

| design | 1x retained | 2x retained | ratio |
| --- | ---: | ---: | ---: |
| interleaved, unchunked | 192.78 MiB | 385.48 MiB | **2.00x** |
| chunked | 11.07 MiB | 20.72 MiB | **1.87x** |

**Both are linear.** They must be: the published generation is a function of the store's bucket and series cardinality, so twice the retained rows really is twice the answer to hold. A design that made steady serving memory flat in cardinality would be dropping data.

What chunking changes is the **constant**, by **17.4x**: from 192.78 MiB of steady memory per unit of cardinality to 11.07 MiB. So the criterion should be restated, and section "The falsifiable test" states it as a slope and a ratio rather than as an exponent. **Left as written, the item would reject a correct implementation.**

## Design

One process. No worker. The rebuild becomes a streamed fold whose working set is bounded by the number of *simultaneously open* cells, not by the number of stored rows.

### What is wrong today, at `file:line`

- `yolomux_lib/stats_current/storage.py:3306` `pinned_snapshot` yields a callable that returns one whole `StoreSnapshot`.
- Inside it, `observation_rows = tuple(... .fetchall())` materialises every raw SQLite row tuple, and then `StoreSnapshot(observations=tuple(Observation(...) for row in observation_rows))` materialises every decoded `Observation` **while the raw rows are still live**. Both generations of the same data exist at once. This is the measured 880 MiB.
- `yolomux_lib/stats_current/storage.py:421` `StoreSnapshot.observations: tuple[Observation, ...]` is the type that forces it.
- `yolomux_lib/stats_current/service.py:2372` `snapshot = read_snapshot()` is the caller that pays for it.
- `yolomux_lib/stats_current/materializer.py:505` `observation_cells.setdefault(cell, []).append(projected)` accumulates a `_ProjectedObservation` for **every** observation into a dict keyed by cell, and only folds afterwards. So even if the snapshot were streamed, `_build` would rebuild the same full-size graph.

### The change

**A. A batched reader.** Add a sibling to `storage.py:3306` — `pinned_snapshot_batches(...)` — that pins the same WAL generation and yields decoded batches instead of one snapshot. It must use the existing `ORDER BY observed_at` and a keyset cursor (`WHERE observed_at > ? OR (observed_at = ? AND event_id > ?)`), never `OFFSET`, so cost stays constant per batch. Each batch is a tuple of `Observation` built from rows that are dropped before the next batch is fetched. Coverage epochs, unavailable spans and usage atoms stay whole: measured at 2,037 / 1,471 / 74,011 rows they are three orders of magnitude smaller than observations and are not the problem.

**B. Incremental folding.** Replace `materializer.py:505`'s `dict[cell, list[_ProjectedObservation]]` with `dict[cell, _PartialFold]`, where `_PartialFold` holds one accumulator per series. Every fold operation at `materializer.py:732-753` is already incrementally computable:

| operation | accumulator |
| --- | --- |
| `sum`, `rate`, `rate_per_minute` | running total, count |
| `minimum`, `maximum` | running extremum |
| `average` | running total, count |
| `gauge`, `status` | current best by `(observed_at, source_id)` |
| `average_sources`, `rate_average_sources`, `sum_average_sources` | per-source total and count |

`materializer.py:1173` `_build_bucket_cost_detail` is the one that needs proving rather than assuming: it builds dimension and attribution maps from a tuple of cost atoms. Those maps look additive, but ranking at `materializer.py:1074` `_ranked_cost_keys` takes a top-N over the finished scores, so the accumulator must retain enough per-key state to rank correctly at close. **Implementers must show a whole-input versus every-split equivalence test for this function before anything else lands.**

**C. Close cells as the cursor passes them.** The observation query at `storage.py:3331` is `ORDER BY observed_at, family, source_id`, so for a **full** rebuild each resolution's cells arrive contiguously in time and at most **one cell per resolution** is open at any moment. With four resolutions that is four open cells.

**This holds only for the full rebuild, and an implementer must not assume otherwise.** `storage.py:3329` iterates `for time_clause, time_parameters in time_clauses`, concatenating one ordered run per clause. When `dirty_intervals` is set there is one clause per coalesced interval, so the stream is ordered *within* each run but not *across* runs, and the open-cell count becomes one per resolution per run. That is fine, because the dirty path is the incremental build and the incremental build has no memory problem to solve: measured over fifty consecutive incremental builds, traced memory moved 78.15 to 78.55 MiB and process USS did not move at two decimal places. **Scope this design to the full rebuild.** If it is ever extended to the dirty path, either sort the concatenated runs or bound open cells per run explicitly — do not inherit the four-cell assertion. At the measured density of ~82 series per bucket, peak partial-fold state is roughly **330 accumulators on the shared layer**, against the 617,243 `_ProjectedObservation` objects held today. **Both figures are the production store**, not the 60,000-row slice the branch measures at 307 accumulators against 240,000 objects — the two fixtures differ by about twenty times and their ratios are not comparable. The ~330 is a model-shaped count of `(series, source_id)` pairs, of the same lineage as the retired 358; the branch's 307 instruments live state. **Neither figure here has been re-derived**: the fixture is named so a reader can tell which world each number describes, which is a separate thing from the number having been re-checked. **Every accumulator and open-cell count in this document is a shared-layer figure.** That is the whole figure today, because no private overlay is constructed; it is stated anyway, because a number that does not say what it counts is how a reader reaches the wrong ceiling. A closed cell's `Bucket` is appended to the layer and its accumulators are dropped.

That is the structural result worth stating plainly: **the transient working set stops being O(rows) and becomes O(open cells x series)**, which is O(1) in store size.

### Identity fence and publication

Unchanged from today, and this is why no worker is needed. `service.py:2365` already pins one WAL generation for the whole read through `reader.pinned_snapshot`, and `materializer.py:364` `accept_generation` already rejects a candidate built from a stale source. Batched reading happens **inside** that same pinned transaction, so every batch sees one consistent generation. Publication stays a single assignment of the completed `PublishedCache` under `service.py`'s `cache_lock`, so a reader either sees the whole previous generation or the whole new one.

**Serving during a rebuild is already correct today** and needs no change: the build reads through its own connection while `service.py:2484` serves from `self._cache`, which is only replaced at the end. Last-known-good serving is the existing behaviour, not something this design has to add.

### Crash behaviour

- **Crash mid-fold, before publication.** Nothing was assigned to `self._cache`, so the previous generation still serves. The pinned read transaction is rolled back by SQLite on connection loss. On restart the build is retried from `_pending_full`. No partial generation can be observed, because a partial generation is never assigned.
- **Crash after publication.** Identical to today: the generation is in memory and in the ring; the durable ledger already governs republication.
- **Crash of the whole daemon.** Unchanged. This design adds no new persistent state and no new file.

**This is the strongest argument against the worker.** A worker introduces a *third* crash state — child died after writing some of its output but before signalling completion — that the single-process design simply does not have.

## The bounds

| bound | value | why this number |
| --- | ---: | --- |
| **decoded bytes per batch** | **32 MiB** | Measured cost of a decoded observation is ~1.46 KiB of USS (880.32 MiB held for 617,243 observations **on the production store**). 32 MiB is past the knee of the chunk-size sweep, where peak stops falling (397 → 32.7 → 28.1 → 27.2 MiB at 600k / 10k / 2.5k / 1k rows per chunk), and it is smaller than the 53 MiB retained generation, so a batch can never dominate the thing it is building. |
| **rows per batch** | **22,000** | 32 MiB ÷ 1.46 KiB. Enforced as a secondary guard so one pathologically wide payload cannot overshoot the byte bound between checks. Whichever bound trips first ends the batch. |
| **rebuild growth** | **192 MiB** | How much a rebuild may GROW the process, measured from the reading taken when the rebuild starts. 106 MiB (measured 53 MiB retained generation, doubled for headroom) + 32 MiB live batch + 24 MiB encode buffer (measured 33 MiB encoded, held transiently) = **162 MiB derived**, plus **30 MiB of headroom retained on purpose**. There is no interpreter term and no term for anything the process already held: growth is the only part a rebuild is responsible for. Every derived term is a measurement, not a guess; the 30 MiB is not, and is not presented as one. |
| **rebuild lifetime** | **120 s** | A cold build measured 26.80 s at 1.19 M observations on a host at loadavg 9-10, and 2x cardinality measured 2.054x readiness, giving ~55 s at 2x. 120 s is roughly twice that, so it fires on genuine pathology and never on a slow but healthy host. |
| **partial-fold state** | **`len(RESOLUTIONS)` open cells** | One per resolution, guaranteed by the `ORDER BY observed_at` read on the single-clause full-rebuild path. **Assert the formula rather than the literal 4.** The bound is `len(RESOLUTIONS)` because no private browser overlay is ever constructed — `PrivateOverlay` has no construction site and `_private_browser_sources` has no caller — so the shared layer is the only layer built. **If an overlay is ever built, the bound becomes `len(RESOLUTIONS) × (1 + MAX_PRIVATE_BROWSER_CLIENTS)`**, because `_build_layer` keys private cells as `(private_source_id, resolution, bucket_start)` against the shared `(resolution, bucket_start)`. A violation today means the read stopped being ordered and the design's memory bound is void. Not valid on the multi-clause dirty path — see C. |

**What this bound does not promise.** Total process memory at rebuild entry is not bounded, by this design or by anything else. An earlier version of the row above specified *peak process memory during rebuild* at 192 MiB, and the implementation does not provide that — deliberately. On the live daemon, `RssAnon + VmSwap` measured 623,244 kB + 942,440 kB = 1,529 MiB before any rebuild begins, so a peak bound of 192 MiB would refuse every rebuild forever, from the first one after start. The same bound fired in a gate worker sitting at 300 MiB while its fixture held 200 observations. The shipped guard therefore permits a peak of 1,721 MiB on that daemon: 1,529 MiB already held plus 192 MiB of admitted growth. That is the contract, and a reader who needs a ceiling on total process memory will not find one here.

The bound is checked at every fetch boundary, before and after, so no batch reaches a caller with its growth unmeasured. It is measured against `RssAnon + VmSwap` and never RSS, because RSS falls when the kernel pages a process out and would admit a rebuild that had already grown past its budget.

**This guard ships present and unwired.** `pinned_snapshot_batches` has no production caller; `_build_once` still reads `reader.pinned_snapshot`. Correcting the guard is not evidence for enabling it, and the owning queue item stays open.

Exceeding a bound is a **typed failure with a reason code**, not a silent truncation and not a crash: the rebuild abandons the candidate, the previous generation keeps serving, and the reason is recorded against the build. A rebuild that quietly served half a store would be worse than one that refused.

## What crosses the boundary

Not applicable in the recommended design — there is no boundary, which is the point.

For completeness, since the queue asked: **if a worker were ever built, the parent must not deserialize the child's generation into its own heap.** Measured, that is not because deserialization is expensive but because of *when* it happens. Allocating 57,000 survivors alone gives 19 arenas and a 1.04 overhead ratio — perfectly dense. The fragmentation comes from allocating survivors *while a large transient graph shares the arenas*. So a parent that only unpickles is fine; a parent that unpickles while also building is not. The blob-only variant (parent holds one `bytes` object, never decodes) measured 5 arenas and 3.75 MiB retained, and would be the right shape only if the daemon could serve the encoded form directly — which it cannot, because `service.py` needs the `Generation` object graph for delta computation.

## The falsifiable test

Restated so it can pass, and so it can fail.

**Claim under test:** steady serving memory after a cold rebuild is linear in the *retained generation*, not in the *transient graph*.

**Procedure.** Build two fixtures whose bucket-and-series cardinality differs by 2.000x and whose other properties match (same families, same source count, same payload widths — the 2026-08-19 attempt was rejected partly for a fixture at 2.24x observations but 0.26x usage atoms, so ratios must be reported per table). For each, in a fresh subprocess: run one cold rebuild, drop the read, `gc.collect()`, then record

1. process USS from `/proc/self/smaps_rollup`,
2. retained-owner bytes by visited-id deep traversal from `_cache`, `_delta_entries`, `_ring_views`, `_snapshot_body_decoration_cache`, reporting encoded and Python-object bytes separately,
3. `arenas allocated current` and `bytes in allocated blocks` from `sys._debugmallocstats()` captured on fd 2.

**Pass:** the ratio *USS ÷ retained-owner bytes* is **≤ 2.0 at both cardinalities**, and does not itself grow between 1x and 2x. Today that ratio is **27.8** (1,470.00 MiB over 52.96 MiB). Equivalently, `arenas_current x 1 MiB ÷ live_bytes ≤ 2.0`; today it is **28.3**.

**Fail — and these are the results that would falsify the design:**

- The ratio stays above 2.0 at 1x. Chunking did not bound the arena high-water; the survivors are still scattered and something other than the observation graph is pinning arenas.
- The ratio is under 2.0 at 1x but rises at 2x. The bound is cardinality-dependent, which means the batch bound is not actually capping the working set — most likely because open-cell count is not really 4.
- USS falls but retained-owner bytes rise by more than the generation genuinely grew. Memory was moved into a cache rather than freed.
- `arenas reclaimed` stays near zero while `arenas_current` falls. The measurement is being satisfied by allocating less rather than by returning arenas, and it will regress on a store shaped differently.

**Do not accept "USS went down" on its own.** USS falls for many uninteresting reasons, including a smaller fixture. The ratio is the claim.

**Do not use absolute MiB targets as the criterion.** The 128 / 160 / 256 MiB figures elsewhere in this plan are design targets, not accepted budgets, and a ratio survives a change of store size where an absolute number does not.

## Cost

| | chunked fold (recommended) | short-lived worker |
| --- | --- | --- |
| fragmentation removed (probe) | 93.4% | ~96% |
| overhead ratio reached (probe) | 1.32 | 1.04 |
| new processes | 0 | 1 |
| new serialization boundary | none | full generation |
| new crash states | 0 | 1 (died after partial output, before completion signal) |
| new supervision surface | none | liveness, timeout, orphan reaping, zombie handling |
| files needing another lane's ownership | `storage.py`, `service.py` | `storage.py`, `service.py`, plus a new module |
| risk concentrated in | `_build_bucket_cost_detail` split-equivalence | all of the above, plus the above |

Against the ~1.42 GiB of arena space currently pinned, chunking should recover roughly **1.3 GiB** and the worker perhaps **80 MiB more**. **The worker is not worth building.** If a future measurement shows chunking stuck above the 2.0 ratio, revisit — but revisit with that measurement in hand, not on the current theory, which this document has already falsified once.

## Interaction with decisions already taken

- **Readiness fix as a precondition for the cold-serving item.** This design *helps* readiness rather than competing with it: the cold build's 26.80 s is dominated by decode and validation work that batching does not remove, but the memory the build itself adds during that window drops from ~1,470 MiB to a bounded 192 MiB of growth, which removes the allocation pressure a readiness probe has to survive. It bounds what the build adds, not what the process already holds. It changes no readiness interface.
- **The cold-serving item as question two's approved `CHANGE` owner.** Unaffected by construction: last-known-good serving during a rebuild is the *existing* behaviour at `service.py:2484` and this design does not touch it. The one thing to watch is that the cold-serving owner and this owner both want to edit `service.py:2372`, so they must not land concurrently.
- **Ownership, which does gate implementation.** The change lives in `storage.py:3306` / `storage.py:421` and `service.py:2372`, both owned by `YOLO-V0717-E3-BATCH-16`, plus `materializer.py:441-505`, which is free. **This cannot be implemented by a lane that owns only the materializer.** Sequence it after `BATCH-16` or give one owner all three files.

## Evidence

Probe: `/tmp/yolomux-e3-profile-04/rebuild/arena_probe.py`. Pure RAM, no store copy, no disk over the freeze threshold. Each variant runs in a fresh subprocess because pymalloc arena state is process-global. Shape scaled to ~10% of the real cold build: ~120 MiB transient peak against 57,000 survivors, versus the real ~1,238 MiB against ~570,000.

The probe reproduces the mechanism at a 17.10 arena-to-live ratio with zero arenas reclaimed, against production's 28.3 with one reclaimed out of 1,412. It reproduces the direction and the order of magnitude, not the exact severity — production has more size classes and a longer build, both of which make fragmentation worse. Conclusions here are therefore stated as ratios and directions, and the acceptance test above re-measures on the real store rather than trusting the probe.

One design question the probe settled and the spec should not re-litigate: an explicit `gc.collect()` per batch is **not** required. Chunked with and without it measured identically (28.23 MiB peak, 20.80 MiB retained, 24 versus 25 arenas). The transient rows form no reference cycle, so refcounting frees them at the `del`. Do not put a per-batch collect in the implementation; on a large heap it is pure cost.

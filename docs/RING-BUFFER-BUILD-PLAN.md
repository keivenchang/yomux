# Ring-buffer build plan

> **STATUS 2026-09-03: HISTORICAL.** This was the pre-activation plan for durable aggregate rings. Current code uses schema 9, persists `aggregate_publication`, `aggregate_rings`, `aggregate_ring_slots`, and the invalidation ledger, and serves cold starts from the persisted ring. The remaining sections are the design and measurement record, not pending implementation work. [`specs/STATS_API.md`](specs/STATS_API.md) is the current storage and serving contract.

This plan recorded the intended move from a `PublishedCache` read path to durable per-resolution rings without a flag day. It is retained for migration history only.

The transition is:

```text
v7 cache reads
    -> v8 cache reads plus shadow ring writes
    -> v8 cache reads plus aligned semantic comparison
    -> v8 ring reads plus shadow cache comparison
    -> v8 ring reads without PublishedCache
```

The first useful standalone landing is Step 1: one capacity policy derives the existing range/resolution matrix before any persistence code exists. Rings begin writing in Step 4. Reads switch in Step 7. `PublishedCache`, restart warming, `cache_ready_event`, and G8 disappear together in Step 8.

## Landing sequence

| step | independently useful result | live response owner after landing | effort |
| ---: | --- | --- | ---: |
| 1 | One capacity map derives the existing sixteen range/resolution views. | `PublishedCache` on v7 | 0.5 day |
| 2 | An inert, tested fixed-slot persistence kernel exists for migration and service work. | `PublishedCache` on v7 | 2–3 days |
| 3 | A tested v7-to-v8 shadow builder can create and verify a complete v8 candidate without activation. | `PublishedCache` on v7 | 4–6 days |
| 4 | v8 activates and publishes durable rings every 10 seconds while cache responses remain unchanged. | `PublishedCache` on v8 | 3–4 days |
| 5 | Aligned semantic shadow comparison proves ring and cache answers agree. | `PublishedCache` on v8 | 2–3 days plus a 24-hour soak |
| 6 | Measured ring reads meet the existing latency contract, with phase-level evidence for any tuning. | `PublishedCache` on v8 | 2–4 days |
| 7 | Snapshot, delta, and SSE reads come from rings while the cache remains an explicit rollback path. | Rings on v8 | 3–5 days |
| 8 | Cache materialization and warm-only machinery are deleted after the switch has proved stable. | Rings on v8 | 2–3 days |

The implementation estimate is roughly 19–29 engineer-days plus at least one uninterrupted 24-hour shadow soak. Migration and the read switch are the largest uncertainty; neither should be split across a landing that leaves an unverified live schema or a half-switched response path.

## Step 1: centralize range capacity policy

Add `RING_CAPACITIES = {1: 300, 10: 180, 60: 480, 300: 288}` beside `explicit_resolutions()` and derive availability with `MIN_BUCKETS <= range_seconds / resolution_seconds <= RING_CAPACITIES[resolution_seconds]`. Update `auto_resolution()` and every policy test to consume the same mapping. This reproduces the existing sixteen views, including the exclusion of `3600/10`, without a special case.

The 300-slot 1-second ring deliberately retains only five minutes. Any future view that asks for longer 1-second history must increase that capacity through a schema migration rather than a runtime setting.

`MAX_BUCKETS` can remain the wire and delta payload safety ceiling while ring capacity becomes the sole maximum-history owner for each resolution. Those are different constraints; storage initialization must later import `RING_CAPACITIES` rather than copy its values. Coordinate this landing after the in-flight `resolution.py` edit owned by yo7772.

Acceptance is the current resolution, capabilities, protocol, browser, and all-pairs aggregation coverage with no public behavior change, plus promotion of `test_ring_capacities_and_minimum_density_derive_the_current_view_matrix` from strict XFAIL to pass. This landing is useful even if rings are never implemented because it removes the manually duplicated availability ladder.

Rollback is a direct revert to the current `MAX_BUCKETS` and special-case calculation. No database or persisted state changes.

## Step 2: add the inert ring persistence kernel

Add the v8 aggregate schema builder, exact capacity seeding, slot addressing, update-only publication, and exact-timestamp reads behind an explicit storage helper that production v7 startup does not call. The kernel owns `RingBucketWrite`, one-transaction `publish_ring_buckets(...)`, and `read_ring_window(...)` returning ordered rows plus exact `missing_bucket_starts`, as pinned by the red contracts.

Initialization creates one publication row, four ring metadata rows, and exactly 1,248 slots before installing insert/delete rejection triggers. Publication validates alignment, horizon, monotonic source generation, read-only ownership, and a measured `MAX_RING_BUCKET_BYTES` bound, then updates slots, ring heads, and publication metadata in one transaction. A read pins one SQLite snapshot, validates every requested `bucket_start`, rejects stale laps, distinguishes explicit zero from absence, and returns at most 480 rows.

This landing includes connection-scoped unit tests for creation, row-count invariance, stale-lap rejection, quiet zero, write-head crossing, one-transaction publication, update-only enforcement, oversize rejection, corruption detection, and read-only publication rejection. The production constants remain schema v7 and no runtime caller writes aggregate tables, so the landing is independently green and gives the migrator a stable storage boundary.

Rollback is removal or dormancy of the unused helper and tests. Existing v7 files have never been opened by the kernel.

## Step 3: add the v7-to-v8 shadow migrator and verifier

Implement the one-time upgrader while production still declares schema v7. It opens v7 read-only, takes a consistent SQLite backup into a private v8 candidate, copies the retained raw facts, seeds all 1,248 aggregate slots, and performs one initial four-resolution fold at a single pinned `source_generation` and `observed_until`. It then validates application and schema metadata, exact tables and columns, four capacities, contiguous slot indexes, triggers, row counts, generation monotonicity, payload bounds, timestamp alignment, and all sixteen derived views before atomically activating `stats-v8.sqlite3`.

The migration tests must cover a live-WAL source, interruption at each phase, an existing valid v8 target, an invalid or partial v8 candidate, restart after activation, and byte-for-byte preservation of v7. Activation never renames, deletes, truncates, checkpoints, or opens a write connection to the old database or its sidecars. Failed private candidates are never published. The old v7 database is historical fallback, not the place where v8 state is staged.

Migration parity uses deterministic 24-hour facts, private overlays, cost evidence, coverage gaps, idle periods, late facts, and all sixteen range/resolution views. This proves the initial slots were folded correctly but does not yet authorize live v8 reads.

Rollback is to leave the migrator uncalled or disable candidate activation. No live response references v8, and v7 remains byte-for-byte intact. A completed dormant v8 candidate can be left for diagnosis or removed by the migration owner; rollback never edits v7.

## Step 4: activate v8 and start shadow ring writes

Bump the schema-specific database, socket, and writer-fence identities to v8 and wire startup through the verified shadow migrator. This is the first landing that writes rings. The service still builds and serves the existing `PublishedCache`; snapshot, delta, and SSE behavior do not switch here.

After each accepted raw commit, a bounded coalescer records affected `(resolution_seconds, bucket_start)` cells. On each monotonic 10-second tick, the worker swaps the dirty set, pins one raw generation and `observed_until`, folds only those cells, persists newly elapsed quiet cells as explicit zeros, and publishes the batch in one SQLite transaction. Facts accepted after the pin remain dirty for the next flush. Late facts can rewrite an exact cell only while it remains inside that ring's horizon. Downtime remains a gap rather than a synthesized zero warm pass.

The writer exposes ring generation, source generation, last successful flush, flush duration, oldest dirty age, dirty-cell count, last typed failure, and publication-disabled state. A fold or transaction failure keeps the dirty identities queued and clears any healthy 10-second freshness claim. Include an internal publication kill switch; it stops shadow ring publication without stopping v8 raw ingest or cache reads.

Acceptance includes the fixed-row, update-only, stale-lap, quiet-zero, write-head, restart-store, read-only-owner, coexistence, downtime-gap, crash-before-flush, late-fact, and clean-shutdown contracts. It also includes the full current cache response and service suites because the cache is still the only response owner.

Rollback keeps the process and database on v8, disables ring publication, and continues serving the cache from retained v8 raw facts. Once v8 has accepted new facts, an old v7 binary is not a lossless rollback and must not open v8. Port 7770 and its versioned files remain untouched throughout.

## Step 5: prove cache and ring semantic parity

Add shadow reads and comparison while the cache still serves every response. A comparison is valid only when both candidates use the same `source_generation` and `observed_until`; comparing a current cache generation with a ring publication up to 10 seconds behind would manufacture mismatches. At each ring flush, retain or build the corresponding legacy materializer result from the same pinned raw snapshot so every ring publication has an aligned reference. Unaligned work is counted as skipped and cannot satisfy the parity gate.

Compare canonical response meaning rather than JSON bytes. The comparison covers window bounds, concrete resolution, bucket timestamps and durations, open/complete state, series values and counts, first/last timestamps, no-data identities, cost totals, pricing evidence and omission counts, public data, bounded private-client overlays, AUTO aliases, and monotonic source/publication semantics. Cache and ring generation numbers or JSON key order need not be identical.

For delta and SSE behavior, apply each cache candidate and each ring candidate to the same canonical baseline and compare the resulting snapshots. Exact delta batching, revision, and generation numbers may differ; missing buckets, tombstones, no-data changes, cost results, and final state may not. Bounded diagnostics name the first mismatching identity and field without retaining unbounded payloads.

Deterministic coverage includes all sixteen views, multiple ring laps, a window straddling the write head, quiet zero, never-written and downtime gaps, stale slots, open buckets, late facts, migration, restart with an empty overlay, public data, maximum private overlays, and cost truncation limits. The live exit gate is an uninterrupted 24-hour soak with every view and AUTO alias exercised and zero unexplained aligned mismatches.

Rollback disables comparison and dark reads. Cache responses and ring writes continue unchanged.

## Step 6: measure and tune ring-read latency

Resolve the design's unmeasured SQLite read cost before changing response ownership. Use the existing 8,929-row realistic fixture and keep the recorded v0.6.10 G4b table as the baseline: medians 87.5–102.5 ms, p95 values 89.1–107.4 ms, and maxima 90.4–109.3 ms across the six scale transitions. The enforced contract remains a maximum below 350 ms for every transition; do not widen it.

Run dark ring reads for all sixteen views while returning cache bytes. Measure at least 30 forced first-hit reads per view so browser cache reuse cannot hide SQLite work, then run the existing 30-sample browser transition sweep to preserve end-to-end selection-through-DOM evidence. Repeat cold post-restart reads with an empty overlay and no materializer warm. Report median, p95, and maximum for SQLite snapshot acquisition/query, bucket payload decoding, no-data clipping, cost-report reduction, dirty overlay, JSON encoding, RPC/HTTP delivery, and the full browser transition.

If a ring read breaches 350 ms, optimize the measured phase: query shape and indexes, bounded decode, cost reduction, encoding, or a small response-encoding cache whose miss always falls through to rings. Do not add retries, sleeps, serialized execution, lower concurrency, or a wider ceiling. Keep raw measurement output under `/tmp`; only the concise distributions belong in review evidence.

Acceptance requires all forced ring-read distributions and every G4b transition below 350 ms, correct rendered completion, cold-restart availability, and no materializer call on the ring path. This step resolves the SQLite latency open item. It does not make the whole database fixed-size.

Rollback disables dark reads and instrumentation or reverts the measured tuning. Cache responses remain unchanged.

## Step 7: switch reads to rings and retain the cache rollback path

Switch the explicit v8 read mode only after the migration suite, ring-writer health gates, aligned 24-hour parity soak, restart contracts, and latency gates pass. In ring mode, snapshot, delta, and SSE assembly starts from one pinned ring publication, clips persisted no-data spans, applies the bounded dirty overlay where allowed, and emits the existing wire contract. On restart it serves the last committed ring immediately even if the shadow cache is still warming.

Keep `PublishedCache` building in shadow and keep aligned comparison active for this landing. Status must name `ring` as the response owner, report the ring publication generation and lag, and report the shadow cache only as verification and rollback state. Do not silently fall back to cache on a ring mismatch or read error; surface the typed failure so the switch is observable.

Exercise all sixteen views, AUTO aliases, public and private reads, full snapshots, generation-zero recovery, deltas, SSE resume, slow clients, restart, crash, downtime, and range widening. The exit gate includes a live ring-read canary period with no operator rollback, no unexplained mismatch, no freshness breach, and the existing latency ceiling intact.

Rollback is an explicit read-mode change back to cache in the same v8 binary against the same v8 database. Ring publication may continue for diagnosis. Never roll back this step by pointing an old v7 binary at v8 or by replacing v8 with the untouched historical v7 database after v8 has accepted new facts.

## Step 8: remove cache and startup-warm machinery

Collect the deletion only after Step 7 has passed its full canary and restart gates. Remove `PublishedCache`, its encoded snapshot and delta maps, startup `full_builder` work, `_pending_full` and its repair path, cache-generation bookkeeping used only by that path, warm/pending response behavior, `cache_ready_event`, and the warm/materializer status fields. A restart must open the ring reader and serve persisted rows without scheduling a 24-hour fold.

Retain the pure bucket-fold helpers needed by the 10-second dirty-cell writer and the one-time v7-to-v8 migrator; delete only the continuous full-generation cache owner. Replace G8's 15,000-coverage-epoch startup-materializer CPU budget with fixed-slot seed validation, bounded dirty-flush CPU and duration budgets, cold ring-read latency coverage, and proof that restart performs no full fold. G8 is retired in this step, not weakened earlier.

Acceptance requires the full ring, service, HTTP, SSE, browser, migration, restart, ownership, and latency gates with cache symbols and warm waits statically absent. The service must still expose typed ring failures and dirty age, and a persisted readable ring must never return `pending` because an encoding cache is empty.

Rollback redeploys the Step 7 v8 build, which understands the same v8 schema and can rebuild its cache from the still-retained v8 raw facts. It does not restore or mutate v7.

## Raw retention decision

Keep `RETENTION_SECONDS` at 24 hours throughout this plan. Durable rings remove raw rows as a restart aggregate source, but they do not remove the raw tables' append-correctness roles.

| raw family | correctness still owned by retained rows | failure if retention shrinks to the 10-second write-behind window |
| --- | --- | --- |
| `observations` | Exact event-identity retry deduplication and historical dirty-cell recomputation. | A delayed retry is accepted as new, so later publication no longer has the retained identity needed to preserve exactly-once ingest. |
| `usage_atoms` | Exact retry deduplication, attribution conflict and repair checks, and fork-history tombstone lookup. | A later tombstone finds no contributor row, so it cannot validate and remove that contributor's value from a 24-hour ring. |
| `coverage_epochs` | Epoch continuity, overlap rejection, and coverage/no-data calculation across the longest view. | A later close or conflicting span loses the interval state needed to validate the transition and calculate truthful gaps. |
| `unavailable_spans` | Deduplicated explicit gaps, overlap rejection, and clipped no-data annotations. | A 24-hour read loses durable gap meaning, and later coverage can no longer be checked against the pruned span. |

The existing contracts make these dependencies concrete: observation and usage identity deduplication, accepted-tombstone replay after a lost acknowledgment, old EOF fork repair, exact model-attributed tombstone deletion, coverage/unavailable overlap rejection, measured-zero versus no-data behavior, and exact 24-hour prune clipping all depend on retained contributor or interval state.

A 10-second raw window is therefore unsafe. If a usage atom is pruned while its contribution remains in a ring, the aggregate has outlived the identity required to correct it. Making raw storage fixed-capacity would require a separate schema design for bounded contributor identities, tombstone and deduplication state, attribution repair, and coverage interval state with explicit capacity and eviction semantics. That project is outside the read-path switch.

The aggregate tables have a fixed 1,248-row shape after Step 2, but the whole SQLite database remains variable-sized after Step 8 because the four raw families retain 24 hours, payload bytes vary within their bounds, WAL checkpoints vary, and SQLite page high-water marks do not shrink on every prune. No step in this plan claims or delivers a byte-for-byte fixed database.

## Release gates

No read switch is allowed until all of the following are true:

- The v7 source remains untouched through successful, failed, interrupted, and repeated v8 migration.
- Ring publication has fixed row counts, update-only aggregate writes, exact timestamp validation, typed failure reporting, and bounded dirty lag.
- Aligned cache/ring comparison covers all sixteen views, AUTO, overlays, migration, restart, laps, gaps, and deltas with zero unexplained mismatches for 24 hours.
- Forced first-hit, cold-restart, and end-to-end browser distributions remain below the unchanged 350 ms G4b ceiling.
- Same-v8 cache rollback is exercised before ring mode becomes the default.

No cache deletion is allowed until ring mode has also passed its live canary, restart, SSE, range-widening, ownership, and latency gates without using the rollback path.

# DOIT.p1.e4.statsd-batched-persistence-generation-key.md - resolve the generation-key collision that blocks batched persistence

## Priority

P1, e4. Created 2026-08-25 as the named follow-up owner for design work **explicitly deferred out of the v0.7.17 release by Keiven**, so that the deferral has an owner rather than living only in a decision note. It is P1 rather than P0 because the release ships without it and nothing regresses; it is e4 rather than e2 because the blocking problem is a genuine invariant conflict, not a bug with a known fix.

**This queue does not belong to the v0.7.17 STATUS-scoped inventory.** It is post-release work. The former P0 resource queue was closed by the v0.7.22 release decision, which did not claim that its batch-persistence measurement passed. This queue remains the owner for any future generation-key work.

## What is already built, and must not be rebuilt

The batched-persistence implementation exists and is correct apart from the one problem below. It lives on `wq/v0717-e3-batch-persistence`. **Start by reading it, not by reimplementing it.**

Landed and proven, all red-first: one in-statsd buffer under the existing `work_lock`; a `_next_append_flush_at` entry in the existing multi-deadline worker loop, with no second scheduler and no second writer; whole-batch staging so no batch is split across durability regimes; `browser` excluded write-through for a traced durability reason - its retry entry is spliced out on acknowledgement, so a provisional ack transfers custody; ack-time dispositions probed through a **single shared parent** that both the applier and the probe route through; quarantine-and-retry-once on flush failure with a bounded second attempt and a `quarantined_facts` counter; the served coverage cache dropped on quarantine; `_last_source_commit_at` moved to commit time; and a ring-flush fix so a bucket is never published from overlaid uncommitted facts tagged with the older durable generation.

**One-second in-memory UI freshness is preserved and proven** by a test asserting both halves at once: the fact is visible in the one-second layer while `_rows(path)` is empty.

### Already closed before this item is picked up - do not re-derive

Three data-loss paths in the buffered flush were found and fixed while the arm was disabled, each with a **mutation-checked** regression in `tests/test_stats_batched_persistence.py`:

- the coverage probe modelled two of the five rules `_apply_coverage_epochs` enforces, so facts the commit would reject were acknowledged `ok: True` (`_coverage_conflict_reason` is now the sole owner both sides route through);
- a rejected record discarded the WHOLE buffer, losing every other family's acknowledged facts (now quarantines only named offenders and retries the rest, bounded);
- the retry was gated on an offender having been *named*, so a transient `database is locked` - which leaves the probes answering normally and correctly reporting nothing conflicting - was never retried and the buffer was cleared with the counter reporting zero. Fixed in `c4811420b` by testing the state (`separated is None or separated[2] == 0`) rather than how it was reached.

The flush degrades to write-through on failure so the caller keeps custody of its retry, and every discarded fact is counted in `append_persistence.quarantined_facts`. **Re-enabling the arm does not re-enable these paths.** What remains is only the `source_generation` collision recorded at `service.py:59-77`, which is why the arm is off.

**Caveat the fixing lane volunteered, and it is the right one to carry:** two of those three loss paths were found by other people's audits rather than by the implementer, and the whole failure lattice of the flush handler was never re-derived. Do not assume the third was the last. **A related warning from the same lane: its first mutation check patched the wrong function, so all three tests passed under a mutation that changed nothing.** A mutation check that mutates the wrong thing is indistinguishable from a passing one; name the exact line you inverted.


## The problem

**The overlay serves buffered facts without advancing `source_generation`, so content changes while the freshness key stands still.** Measured:

```
buffering OFF : committed_source_generation 1, cache.generation.source_generation 1
buffering ON  : committed_source_generation 0, cache.generation.source_generation 0, buffered_facts 2
```

With the arm enabled the served cache carries **`source_generation 0` while showing real data** - exactly the state the ring freshness floor exists to refuse. Two ring correctness gates fail as a result, and pass with the arm at `0`:

- `test_seeded_slow_ring_view_cannot_fall_back_to_the_startup_zero_cache`
- `test_leader_writer_coalesces_ingest_for_ten_seconds_and_matches_materializer`

**The obvious fix is not available.** Advancing the generation for uncommitted facts would violate *"no cursor, watermark or generation leads durability"* - an invariant the same change was built to preserve, which its own passing test pins, and which exists because a crash between advance and commit leaves a slot the replay cursor folds from with facts that never landed.

**That tension is the whole problem. Any proposal must say which side it gives on and why that is safe.**

## Plan

- [ ] **State the invariant that replaces the current one, before writing code.** Today's rule is that no key may lead durability. A serving overlay needs *some* way for a reader to distinguish "content changed" from "durable state changed". Name the replacement, name where it is enforced, and say what a crash between overlay and commit must leave behind.
- [ ] **Reproduce both ring gate failures with the arm enabled** and keep them as the red controls. Do not start from the passing state.
- [ ] **Decide the key design.** Candidates, none endorsed: a separate volatile serving key alongside the durable one; a generation that advances only on commit with the overlay carrying an explicit pending marker readers must handle; refusing to overlay any fact whose cell has a published ring slot. Each has a different failure mode; price all three rather than picking the first that compiles.
- [ ] **Prove no cursor, watermark or generation leads durability under the new design**, with the existing test kept or its replacement stated.
- [ ] **Re-run both ring gates green with the arm enabled**, plus the full statsd owner set.
- [ ] **Remove the fail-closed guard** that currently prevents a shipped runtime from enabling the arm, and delete the pin test that asserts the disabled default - both were added specifically because of this defect and must not outlive it.
- [ ] **Re-measure the saving on the ring-covered harness** at `tools/measurement/`. The 83.53% figure was measured before any of this and must be re-established, not assumed.

## Gotchas

- **Do not re-enable the arm by editing the default.** The default is `APPEND_FLUSH_SECONDS = 0.0` and the measured interval survives separately as `APPEND_FLUSH_MEASURED_SECONDS = 10.0`, deliberately - the second is the value to *select*, not a default. A pin test names both gates and the blocking invariant in its docstring so re-enabling means reading why not to.
- **The runtime is fail-closed by design**, so a nonzero `APPEND_FLUSH_ENV_NAME` cannot enable the arm in a shipped process. That guard exists because a default is not a guard, and it must stay until the gates pass.
- **`_status()` reports both the shipped default and the measured interval** so a reader can tell them apart. Preserve that.
- The absolute MB figures from the measurement grid do **not** transfer: it modelled 2 families at 1 Hz, while the real mix is 26,687 observations/hour across six families with `service_load` at 80.69%. Ratios transfer; absolutes do not.
- Any harness measuring append cost with a **static ring head** understates by about 46% - appends only write `ring_invalidations` when they intersect a published slot.

## Done Criteria

Both named ring gates pass with the arm enabled; the durability-ordering invariant is stated, enforced and tested; the fail-closed guard and the disabled-default pin test are removed together with the defect that justified them; and the append saving is re-measured on the ring-covered harness rather than carried forward.

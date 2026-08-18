# DOIT.p2.e5.stats-ring-followups.md - Measure And Decide Stats Storage Follow-Ups

Source provenance: `DOIT.unprioritized.md` U-F, Bugs 16-17 from `EVIDENCE-ARCHIVE.md`, the former `DOIT.stats-ring-followups.md`, and the archived storaged startup/retention queue.

## Goal

Measure the current schema-7 ring path and make separate evidence-backed `KEEP` or `CHANGE` decisions for schema migration, `PublishedCache`, startup warming/readiness, raw retention, and coverage-epoch representation without implementing any of those changes in this decision queue.

## Context

- Schema-7 `stats-v7.sqlite3` with 1,248 fixed ring slots is shipped. `PublishedCache` still owns bounded fallback, startup reconciliation, and delta/SSE generations; raw evidence still owns deduplication, tombstone/pricing repair, coverage, migration, and rollback.
- Historical evidence found quadratic coverage-gap work around 15,000 epochs and later periodic CPU excursions. It also found a fixed 3-second storaged startup wait racing a growing 349 MB older-schema database, discarded startup causes, and retention exceeding its configured window. The topology and schema have since changed, so these are probes to reproduce, not current facts.
- Already-landed cost-renderer fixes for unpriced/absent state and wrapping are archived separately and are not storage-change justification.

## Ownership And Parallel Lanes

- One measurement owner freezes the canonical Range x Resolution matrix, cold/settled definitions, fixture sizes, stage schema, thresholds, and raw evidence layout before any run.
- Five read-only audit agents may work in parallel after that freeze: schema coexistence; cache/delta ownership; startup/readiness and error propagation; raw retention/repair; coverage representation. They do not edit product code.
- One decision integrator reconciles all responsibilities and records five literal decisions. Every `CHANGE` outcome creates its own separately approved implementation queue and conflict group; no schema/cache/startup/raw/coverage implementation lands here.

## Plan

- [ ] Freeze the current schema-7 baseline, capability-derived Range x AUTO/explicit Resolution matrix, numeric latency/resource ceilings, ten-cold/thirty-settled sample plan per row, 15,000-epoch fixture, exact stage schema, target machine, commands, and `/tmp` evidence directory before measurement.
- [ ] Measure every matrix row and current startup path, attributing SQLite read/open, validation, migration/preflight, decode, cost reduction, encoding, compression, transport, browser apply/render, CPU seconds, RSS, source/ring/cache bytes, success/failure, and original startup cause; slow and dead startup must be distinguishable without a fixed literal racing database size.
- [ ] Inventory every responsibility and consumer of schema versioning, persisted rings, `PublishedCache`, `_pending_full`, `cache_ready_event`, startup materialization/readiness, retained originals, usage atoms, pruning, coverage, unavailable spans, repair, migration, rollback, and old-database retention.
- [ ] Record one literal `KEEP` or `CHANGE` decision for each of: schema 8, `PublishedCache`, startup full materialization/readiness, raw retention, and coverage-epoch representation; apply the declared thresholds independently and name the replacement owner for every responsibility before any `CHANGE` is allowed.
- [ ] Update the Stats API/ring/build decision docs and, for each `CHANGE`, create one separately approved test-first queue with migration, coexistence, rollback, gate, restart, and runtime acceptance; if all five are `KEEP`, create no implementation queue.

## Decision Rules

- Default is `KEEP`. `CHANGE` requires either a reproduced correctness defect or at least 25% median saving in one predeclared bounded resource, no more than 10% p95 regression in every other measured stage, and a named replacement for every inventoried responsibility.
- A schema change must use a new versioned database with v7/v8 side-by-side operation, read-only preflight, fencing, validated shadow migration, atomic activation, retained forensic source, rollback, and old-runner refusal; v7 is never rewritten in place.
- Cache/startup/raw/coverage follow-ons must preserve snapshot fallback, delta/SSE generations, cold/restart repair, deduplication, tombstone/pricing repair, exact zero/gap/partial geometry, conflicting-atom quarantine, forensic recovery, and one-change backout.

## Done Criteria

- [ ] The decision record contains baseline HEAD, capabilities-derived matrix, commands, target machine, numeric ceilings, cold definition as first request after statsd restart, settled definition, ten cold and thirty settled samples per row, startup fixture sizes, and `/tmp` raw evidence paths; undefined “existing ceiling” language is absent.
- [ ] Every sample reports success/failure and every named stage/resource; each row reports count, p50/p95/max, exact zero/gap/partial geometry, active-window coverage volume, and 15,000-epoch behavior. Startup records retain the original cause and distinguish slow progress from a dead owner.
- [ ] The responsibility inventory has no unowned row, and schema 8, `PublishedCache`, startup warming/readiness, raw retention, and coverage representation each have exactly one literal `KEEP` or `CHANGE` result with threshold arithmetic and named decision authority.
- [ ] Every `CHANGE` has exactly one separately named queue whose acceptance includes failing-first evidence, all affected responsibilities, coexistence/migration/backout, focused tests, canonical gate, restart, and live runtime proof; this queue contains zero product implementation for those decisions.
- [ ] `python3 -m pytest -q tests/test_stats_current_storage.py tests/test_stats_current_service.py tests/test_stats_current_http.py tests/test_stats_current_protocol.py tests/test_browser_stats_coverage.py` exits 0 on the measured baseline, affected decision docs are updated, and the final matrix smoke returns the expected key/resolution/capacity/geometry after the last controlled statsd restart.

## Completion

Summarize the five decisions and links to any follow-on queues in `docs/DONE/`, then remove this decision queue. Do not keep the archived storaged startup file as a second active plan.

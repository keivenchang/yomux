# DOIT: per-product payload TTL on the daemon scheduler

Goal: stop the scheduler retaining payloads nobody needs, and let a product declare how long its result stays reusable. `ttl = 0` means "hand it back and flush". `ttl = N` means "keep it N seconds in case the same request repeats".

Separate from `DOIT.daemon-pause.md`. Do not interleave them.

## The waste, measured 2026-07-29

Live daemon pid `2591351` plateaued at **1,343 MB** holding scheduler records. Ten RSS samples 20 s apart were flat, so this is a bounded working set, not a leak -- the real leak (`prune_records` had zero callers) was fixed in `94383799`.

What fills it:

- `ScheduledWork` retains `request.payload` **and** `result.payload` (`scheduler.py:150-161`).
- `prune_records` bounds **count** (`SCHEDULER_MAX_RECORDS = 1_024`), never bytes.
- Per-record cost spans ~40 B to **4 MB + 4 MB** (`SESSION_METADATA`), so 1,024 records is a memory budget between a few MB and ~8 GB depending only on workload mix. Nobody chose 1.3 GB; someone chose 1,024.

And nothing reads those payloads after delivery:

- Delivery is **at-most-once per ticket**. `products.py:686` and `fs/coordinator.py:147` poll `scheduler.snapshot(ticket)` and pop their pending entry the instant they publish (`coordinator.py:152-153`). Three call sites total; none re-reads.
- `submit()` refuses to coalesce onto a finished record: `if current is not None and current.status not in TERMINAL_STATUSES` (`scheduler.py:253`). So a repeat request re-runs the work today, and the retained result is never consulted.
- There is **no TTL of any kind** on terminal records. Grep for `retention|ttl|max_age|expire_after` in `scheduler.py` returns only a docstring. `deadline_at` bounds execution, not retention.

Required payload lifetime today is therefore "until the next `reconcile()`" -- one pump cycle, sub-second.

## The minimum design

One field and one relaxed condition. Nothing else.

- [ ] **`ttl_seconds: float = 0.0` on `ProductRoute`** (`products.py`), beside `max_request_bytes` / `max_result_bytes` / `lane` / `execution`. That struct is already the single owner of per-product policy, so this adds no new registry, no config file, no lookup table.
- [ ] **`ScheduledWork.release_payloads()`** -- sets `request.payload = None` and `result = None`, keeping the tombstone: ticket, key, status, timings, failure reason. That is exactly what `TaskDiagnostics` and `status_snapshot()` read; neither touches a payload.
- [ ] **`ttl == 0`: release on delivery.** In `snapshot()`, after building the returned projection, release the payloads if the record is terminal. This is the default and it is the whole fix for the 1.3 GB.
- [ ] **`ttl > 0`: release when `now - completed_at >= ttl`**, evaluated in the pump alongside the existing `_expire`. Monotonic clock, never wall clock.
- [ ] **`ttl > 0` also relaxes coalescing**, and this is the only reason a non-zero TTL is worth anything: extend `submit()` so a terminal record whose payloads are still intact and still inside its TTL may be returned instead of re-running the work. Without this a TTL saves memory but not work, because `scheduler.py:253` excludes terminal records.
- [ ] **A released ticket returns a typed status, never `None`.** `reconcile()` treats `snapshot(ticket) is None` as `product_missing` and cancels the product (`products.py:687-693`), so a bare `None` turns a release into a spurious cancellation.

## Safety rules that cannot be dropped

Only two, and both are correctness rather than features:

- [ ] **Identity is not in the key.** `products.py:544` builds `TaskKey(route.owner, "product", product_key)`; `source_identity` is carried separately in `_PendingProduct` and checked at reconcile (`:696`). Sharing a cached result across identities would leak one caller's data to another. **Any product with `ttl > 0` must include identity in its key, or it stays at 0.** No exceptions.
- [ ] **`product_key` is caller-supplied** and the scheduler trusts it (`:541-544`). If a key omits an input that affects the result, coalescing already returns a wrong answer for in-flight work; a TTL extends that wrongness in time. **Audit a product's key before giving it a non-zero TTL.**

## Rollout

- [ ] **Every product starts at `ttl_seconds = 0`.** Ship the whole change with zero behavioural difference beyond releasing dead payloads.
- [ ] Promote individually, each with its key audit and identity decision recorded here, and each justified by a measurement showing repeat requests actually cost something. Candidates in rough order: `TMUX_STATUS` / `TABBER_ACTIVITY` (several panes ask within one frame), `FINDER_DIRECTORY` (same path from multiple clients), then the MAINTENANCE-lane products `WATCHED_PRS` / `UPDATE_STATUS` / `METADATA_WARM` / `INDEXED_REPO_DISCOVERY` whose data changes on the order of minutes.
- [ ] Do **not** promote `SESSION_METADATA` without separate evidence. It is the only 4 MB + 4 MB route and carries the entire memory risk.
- [ ] Do **not** lower `SCHEDULER_MAX_RECORDS`. Once records are tombstones it costs almost nothing and it is the diagnostic window.

## Tests

- [ ] A terminal record's payloads are released after the first `snapshot()`, and a second `snapshot()` returns the tombstone with a typed released status -- **not** `None`.
- [ ] With `ttl = 0`, retained bytes after N completed products stay flat as N grows.
- [ ] With `ttl > 0`, a repeat `submit()` inside the window returns the existing record and does not re-run the handler; outside the window it re-runs.
- [ ] Diagnostics and `status_snapshot()` still report correctly against tombstones.
- [ ] Watch each test fail before trusting it.

## Out of scope

No invalidation wiring, no byte budget, no per-route cache store. Those exist to make broad reuse safe; with every product at `ttl = 0` there is no reuse to make safe. Revisit only if a promotion needs it.

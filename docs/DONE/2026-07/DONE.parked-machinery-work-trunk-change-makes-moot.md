# DOIT.parked-machinery.md — work the trunk change makes moot

**Do not work items in this file.** It exists so that four queues could be deleted without losing the record of what they asked for and why they stopped mattering.

Parked 2026-07-31 by the trunk decision in `MASTERPLAN.md`: `v0.6.10` becomes the trunk and the `storaged` / `daemon` / `local_services` mux architecture is not re-applied. Every item below targets that architecture. If Phase 5 of `MASTERPLAN.md` ever re-introduces a background process, re-read this file first — it is the accumulated knowledge of what that costs.

Absorbed from `DOIT.scheduler-payload-ttl.md` (17 open), `DOIT.daemon-pause.md` (9 open of 58), `DOIT.storaged-stats-startup-budget.md` (4 open), and `DOIT.land-and-close.md` (7 open). Those files were untracked, so they were moved to `docs/archive/doit/` rather than deleted — the full originals are there if a detail below is too compressed.

---

## Items that SURVIVE the trunk change — moved, not parked

These were filed against daemon-era files but state general properties. They are re-homed rather than dropped.

- **`static/brand.css` is rewritten during container boot / E1**, gaining a trailing space and losing its final newline, which fails the `git diff --check` lane. This is a generated-asset/tooling defect, unrelated to the daemon. → Track in `DOIT.md`.
- **A wrapped error must carry the original exception chained (`__cause__`)**, not be reduced to a timeout message. → Encoded as gate box **L6** in `DOIT.regression-gate.md`.
- **A slow worker must not be reported the same way as a dead one.** → Encoded as gate box **M3**.
- **Retention actually runs: after prune, no row older than `RETENTION_SECONDS` remains**, with a test that fails today if prune never runs on the composed deployment. → Re-file against whatever owns retention on the trunk; this property is architecture-independent and must not be lost.
- **A released/expired record returns a typed status, never `None`.** `None` was being read as `product_missing`. The general rule — an absent value must be typed, never bare — is gate box **M4**.
- **Concurrent start must converge on one owner**, and a second server must adopt rather than restart an existing backend. Applies only if Phase 5 re-introduces a background process.

---

## Parked — scheduler payload TTL

Goal was to stop the daemon scheduler retaining product request/result payloads forever: add `ttl_seconds` to `ProductRoute`, add `ScheduledWork.release_payloads()` keeping a tombstone (ticket, key, status, timings, failure reason), release on delivery at `ttl == 0`, release at `now - completed_at >= ttl` on a monotonic clock otherwise, and let a non-zero TTL relax coalescing so a repeat `submit()` inside the window reuses the terminal record.

Two findings worth keeping regardless of architecture:

- **Identity is not in the coalescing key.** `products.py:544` builds `TaskKey(route.owner, "product", product_key)` while `source_identity` is carried separately in `_PendingProduct`. Any cache keyed on a partial identity returns a wrong answer for in-flight requests.
- **`product_key` is caller-supplied and trusted.** If a key omits an input that affects the result, coalescing is already wrong before any TTL is added.

Rejected shortcuts recorded at the time: do not promote `SESSION_METADATA` (the only 4 MB + 4 MB route, and the entire memory risk); do not lower `SCHEDULER_MAX_RECORDS` (tombstones are cheap and it is the diagnostic window); ship every product at `ttl_seconds = 0` first so the change has no behavioural difference beyond releasing dead payloads.

## Parked — daemon deployment lifecycle (daemon-pause Phase D)

47 of 58 boxes shipped; Phases A, 0–5, B and C are complete and were already marked superseded on 2026-07-29. The open remainder was an architectural refactor, not a safety fix — the outage risk was already closed by Phase C (`ee51ca52`).

What it asked for: move backend lifecycle out of `cli.py main()` so starting services is an explicit deployer step rather than a side effect of serving HTTP; make the server a **client** that fails fast naming the mismatching component instead of spawning anything; add a real healthcheck gate between start and serve where each service reports deployment identity plus readiness; preserve shared-backend adoption on identity match; preserve one-command `./boot.sh <port>` ergonomics; decide and record who owns services across a reboot; handle the concurrent-start race explicitly; and test the upgrade path end to end (backend at revision A, deploy revision B, assert old services stop, new pass healthchecks, server starts).

This is a good design for a system that needs backend processes. Under the trunk decision it has no subject.

## Parked — storaged stats startup budget

Startup must succeed when the writer thread takes materially longer than the 3.0 s literal, with the delay injected rather than simulated by a large fixture DB, and without simply raising the literal. Chained-cause and slow-versus-dead survive above.

## Parked — land-and-close

A branch-landing sequence: give the identity builder an override parameter so start and join read one source (callers at `cli.py:838` and `:881`), require three previously-contradictory tests to pass together in one run, with an explicit stop rule (`git checkout -- yolomux_lib/cli.py` and move on after one focused attempt), then rebase onto `main` and report the gate result without moving `main`.

Superseded by `MASTERPLAN.md` Phase 4, which retires the branches rather than landing them — nothing is cherry-picked, and features are rebuilt from ideas. The stop rule is the one thing worth carrying forward, and Phase 3 adopts it verbatim: **one focused attempt, then revert and move on.**

## Parked — the deployment-mismatch operational cost

Recorded during Bug 13 and worth remembering: under the daemon architecture, **every commit to `yolomux_lib/` during a live session could wedge that deployment**, because deployment identity was a sha256 over `rglob("*.py")`. That is why 7770 could not be safely restarted. A trunk without deployment fingerprinting does not have this problem — do not re-introduce it casually.

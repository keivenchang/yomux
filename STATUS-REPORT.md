# Progress

Updated: 2026-08-16 07:43 AM PT
Worktree: `/home/keivenc/dev/yolomux.dev7771`

**Goal:** Eliminate the urgent refresh-fanout and background-CPU regression, then finish the remaining v0.7.8 queues

**Goal totals:** 4/7 done (57%); 3 TODO.

## Goal checklist

- [x] 1. Bound single-browser refresh fan-out and eliminate recurring GIL-bound web CPU with timing-attributed, generation-keyed owners and matched live Chrome evidence
- [x] 2. Fix relative product-root resolution and prove a restarted server writes no unexpected product state under $HOME
- [ ] 3. Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone
- [x] 4. Decide whether multi-machine connectivity has a justified bounded workflow, recording NO_BUILD or one separately approved implementation queue
- [x] 5. Remove the standalone YO!cost panel and place Cost immediately after Graphs inside YO!stats, migrating legacy saved references without breaking restored layouts
- [ ] 6. Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb
- [ ] 7. Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts

**Supporting queue totals:** 18/29 done; 11 pending.

## Active queues

| queue | done | pending | complete |
| --- | ---: | ---: | ---: |
| `queues/backlog/DOIT.p1.e2.merge-unify-quick-open-exclusions.md` | 11 | 2 | 85% |
| `queues/backlog/DOIT.p1.e3.statsd-wal-growth.md` | 5 | 7 | 42% |
| `queues/backlog/DOIT.p2.e1.license-history-remote-finalization.md` | 2 | 2 | 50% |

## All queue checkboxes

### `queues/backlog/DOIT.p1.e2.merge-unify-quick-open-exclusions.md` (11/13)
- [x] Settle the bare-name claim before merging. Run a bare directory name through `index_exclude_paths` on the branch and assert whether Quick Open actually exclude…
- [x] Do not merge into a dirty tree. Wait until the in-flight work touching `server.py`, `tools/check.py`, and `registry.py` is committed, then re-run `merge-tree`…
- [x] Re-derive `tests/fixtures/architecture_budgets/v1.json` after the merge rather than taking either side. Two independently regenerated ratchets cannot be reconc…
- [x] Rebuild `static/yolomux.js` and the generated locales from source after the merge; both sides regenerate them, so whichever survives is wrong. DONE: rebuilt fr…
- [ ] Verify the macOS fixes on real macOS hardware, since that is what they are for. Confirm a Finder repo opened through `/tmp` resolves against its `/private/tmp`…
- [x] Confirm the xterm helper still rethrows non-teardown errors. Its value is that it narrows a swallowed exception; a merge that widens it back to a bare catch si…
- [x] Give the landed work an accurate description in `docs/DONE/`. Neither the branch name nor the commit message covers this change, and the next person searching…
- [x] `git log HEAD..origin/fix/unify-quick-open-exclusions` is empty. DONE: `59676f11b` is the second parent of merge `a544089e2`; the log range is empty and `merge…
- [x] A bare directory name in `index_exclude_paths` either excludes as documented, covered by a test seen to fail first, or the help text is corrected to match real…
- [x] `tests/fixtures/architecture_budgets/v1.json` regenerated post-merge, not inherited. DONE: regenerated after the final xterm regression and passed the ratchet…
- [x] Generated assets rebuilt from source; the generated-asset check exits 0. DONE: current post-merge check exited 0.
- [ ] macOS acceptance on real hardware, or explicitly recorded as blocked with the reason. BLOCKED: the real Mac at `ereview.com:8882` serves the exact merged bundl…
- [x] Canonical gate green, no new Warnings or Errors. DONE: at `1406566f9`, all nine functional lanes passed in `/tmp/yolomux-check-runs/check-1786882610409235988-5…

### `queues/backlog/DOIT.p1.e3.statsd-wal-growth.md` (5/12)
- [x] Prove or disprove the WAL-size-to-CPU link before changing anything. Checkpoint the WAL to truncation on a live instance and measure `statsd` CPU before and af…
- [x] Establish why the automatic checkpoint never completes. Identify which reader holds the oldest snapshot and for how long; a long-lived read transaction, not ju…
- [x] Give the WAL a bounded owner. Whatever the fix — periodic explicit `wal_checkpoint(TRUNCATE)`, releasing read snapshots between cycles, or a tighter `wal_autoc…
- [ ] Fix the retention overrun so the retained span matches `RETENTION_SECONDS` rather than exceeding it by ~20 hours, and confirm the daily prune completes. REOPEN…
- [ ] Close the second compaction gate, or record why it is acceptable. If `pending` can be non-empty every time the max-defer cap elapses, then `VACUUM_MAX_DEFER_SE…
- [ ] Re-measure over a full day. This defect is only visible across many hours, so a short post-fix sample proves nothing; the acceptance evidence is a flat `statsd…
- [ ] `statsd` CPU is flat over 24 hours under comparable load, shown as a before/after chart or sampled series rather than asserted.
- [ ] The WAL has a stated ceiling and stays under it during sustained load, verified on a live instance.
- [ ] Retained `observations` span is within `RETENTION_SECONDS`. FAILED on the pre-`3f22391f7` soak: maximum measured span was 172,918.85 seconds against 172,800 se…
- [x] If the WAL turns out not to drive the CPU climb, the real cause is named and this queue is re-scoped, not closed. DONE 2026-08-16 02:45 AM PT: production's old…
- [ ] No stats data is lost by any change made here; row counts and coverage before and after are compared.
- [x] Canonical gate green, no new Warnings or Errors. DONE 2026-08-16 06:45 AM PT: after regenerating the three stale architecture-budget counts in `d1167f4f6`, a c…

### `queues/backlog/DOIT.p2.e1.license-history-remote-finalization.md` (2/4)
- [x] Reverify the local rewritten history, target remote branch, lease SHA, current tree license, key historical searches, and fresh-clone procedure without changin…
- [x] Verify a fresh branch-only clone from remote `main` against every key license search and current-tree file identity. DONE: `/tmp/yolomux-078-license-clone.8sAR…
- [ ] Obtain explicit authorization to atomically delete or replace only remote tags `v0.2.0`, `v0.3.0`, and `v0.4.5`, leased respectively at tag objects `f3a8a71e33…
- [ ] Every remote ref is free of the old license lineage, and a fresh all-ref clone verifies the result. Live re-verification found 29 remote refs and exactly these…

## Pending goal items

- 3. Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone
- 6. Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb
- 7. Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts

## Hourly history (last 24 hours)

| PT hour | done | pending | delta done | note |
| --- | ---: | ---: | ---: | --- |
| 2026-08-15 12:00 PT | 0 | 3 | - | Queued the three score <=3 tasks for v0.7.8 after verified 0.7.7 promotion on ports 7770 and 7771 |
| 2026-08-15 13:00 PT | 1 | 2 | +1 | Recorded NO_BUILD for a duplicate multi-machine connector; root safety is 10/11 and tag cleanup remains authorization-gated |
| 2026-08-15 14:00 PT | 1 | 2 | +0 | Completed root-path implementation and focused validation; exact-SHA certification and tag authorization remained open |
| 2026-08-15 15:00 PT | 1 | 2 | +0 | Fixed the architecture scanner race with live document-lock heartbeat files and started the fresh canonical gate |
| 2026-08-15 16:00 PT | 1 | 3 | +0 | Accepted a 208/208 exact request join with zero evictions; proved watch/roots repeats byte-identical, fs/batch bodies distinct, and operations/ack the top request-thread CPU route |
| 2026-08-15 17:00 PT | 1 | 3 | +0 |  |
| 2026-08-15 18:00 PT | 1 | 3 | +0 | Statsd dirty-cell/no-data materialization reached zero unchanged fragments across ten revisions; current focused matrix passed 359/359 |
| 2026-08-15 19:00 PT | 1 | 4 | +0 | Added the YO!cost-to-YO!stats migration to v0.7.8 and fixed the E2E fixture receipt race exposed by the integrated browser journey |
| 2026-08-15 21:00 PT | 1 | 4 | +0 |  |
| 2026-08-15 22:00 PT | 1 | 4 | +0 |  |
| 2026-08-15 23:00 PT | 1 | 4 | +0 | Cost live/browser/function gates are green after aligning the EventSource fixture with canonical demand identity; exact-SHA certification remains blocked by dirty checkout and host I/O admission |
| 2026-08-16 00:00 PT | 1 | 4 | +0 | Background owner lanes are green: Statusd 588/588, Jobd 105/105, Watchd/session-files 302/302, and Browser/refresh 164/164 plus 62/62 with live 89/89 joins and concurrency eight |
| 2026-08-16 02:00 PT | 1 | 5 | +0 | Bound statsd WAL allocation at 8 MiB, restored one-minute retention sweeps and the one-hour max-defer guarantee, named growing unchanged-cell materialization as the CPU owner, and started the 24-hour acceptance window |
| 2026-08-16 04:00 PT | 1 | 6 | +0 | Landed merge a544089e2 after bare-name red-first fix 325bfb094; regenerated assets and budgets, 19 Node shards and 269-test merge matrix green; real macOS and canonical gate remain |
| 2026-08-16 05:00 PT | 1 | 6 | +0 | Exact-SHA certification passed all seven units on 1406566f9; the merged Quick Open/macOS/xterm queue remains blocked only on two real-macOS checks |
| 2026-08-16 06:00 PT | 4 | 3 | +3 | Exact-SHA canonical gate passed at d1167f4f6 in 539.65s with all seven certification units; archived the completed refresh/CPU queue and kept Statsd live-soak acceptance open |
| 2026-08-16 07:00 PT | 4 | 3 | +0 | Fixed fractional Statsd prune deadlines and cross-app watchd test attribution; all nine canonical functional lanes passed at 7dd4c7633, while certification tests passed but host postflight I/O admission refused the loaded soak host |

<!-- progress-report-goal: {"goal":"Eliminate the urgent refresh-fanout and background-CPU regression, then finish the remaining v0.7.8 queues","items":[{"id":1,"text":"Bound single-browser refresh fan-out and eliminate recurring GIL-bound web CPU with timing-attributed, generation-keyed owners and matched live Chrome evidence","done":true},{"id":2,"text":"Fix relative product-root resolution and prove a restarted server writes no unexpected product state under $HOME","done":true},{"id":3,"text":"Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone","done":false},{"id":4,"text":"Decide whether multi-machine connectivity has a justified bounded workflow, recording NO_BUILD or one separately approved implementation queue","done":true},{"id":5,"text":"Remove the standalone YO!cost panel and place Cost immediately after Graphs inside YO!stats, migrating legacy saved references without breaking restored layouts","done":true},{"id":6,"text":"Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb","done":false},{"id":7,"text":"Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts","done":false}],"queues":["queues/backlog/DOIT.p1.e3.statsd-wal-growth.md","queues/backlog/DOIT.p2.e1.license-history-remote-finalization.md","queues/backlog/DOIT.p1.e2.merge-unify-quick-open-exclusions.md"]} -->
<!-- progress-report-history: [{"hour":"2026-08-15 12:00 PT","done":0,"pending":3,"note":"Queued the three score <=3 tasks for v0.7.8 after verified 0.7.7 promotion on ports 7770 and 7771"},{"hour":"2026-08-15 13:00 PT","done":1,"pending":2,"note":"Recorded NO_BUILD for a duplicate multi-machine connector; root safety is 10/11 and tag cleanup remains authorization-gated"},{"hour":"2026-08-15 14:00 PT","done":1,"pending":2,"note":"Completed root-path implementation and focused validation; exact-SHA certification and tag authorization remained open"},{"hour":"2026-08-15 15:00 PT","done":1,"pending":2,"note":"Fixed the architecture scanner race with live document-lock heartbeat files and started the fresh canonical gate"},{"hour":"2026-08-15 16:00 PT","done":1,"pending":3,"note":"Accepted a 208/208 exact request join with zero evictions; proved watch/roots repeats byte-identical, fs/batch bodies distinct, and operations/ack the top request-thread CPU route"},{"hour":"2026-08-15 17:00 PT","done":1,"pending":3},{"hour":"2026-08-15 18:00 PT","done":1,"pending":3,"note":"Statsd dirty-cell/no-data materialization reached zero unchanged fragments across ten revisions; current focused matrix passed 359/359"},{"hour":"2026-08-15 19:00 PT","done":1,"pending":4,"note":"Added the YO!cost-to-YO!stats migration to v0.7.8 and fixed the E2E fixture receipt race exposed by the integrated browser journey"},{"hour":"2026-08-15 21:00 PT","done":1,"pending":4},{"hour":"2026-08-15 22:00 PT","done":1,"pending":4},{"hour":"2026-08-15 23:00 PT","done":1,"pending":4,"note":"Cost live/browser/function gates are green after aligning the EventSource fixture with canonical demand identity; exact-SHA certification remains blocked by dirty checkout and host I/O admission"},{"hour":"2026-08-16 00:00 PT","done":1,"pending":4,"note":"Background owner lanes are green: Statusd 588/588, Jobd 105/105, Watchd/session-files 302/302, and Browser/refresh 164/164 plus 62/62 with live 89/89 joins and concurrency eight"},{"hour":"2026-08-16 02:00 PT","done":1,"pending":5,"note":"Bound statsd WAL allocation at 8 MiB, restored one-minute retention sweeps and the one-hour max-defer guarantee, named growing unchanged-cell materialization as the CPU owner, and started the 24-hour acceptance window"},{"hour":"2026-08-16 04:00 PT","done":1,"pending":6,"note":"Landed merge a544089e2 after bare-name red-first fix 325bfb094; regenerated assets and budgets, 19 Node shards and 269-test merge matrix green; real macOS and canonical gate remain"},{"hour":"2026-08-16 05:00 PT","done":1,"pending":6,"note":"Exact-SHA certification passed all seven units on 1406566f9; the merged Quick Open/macOS/xterm queue remains blocked only on two real-macOS checks"},{"hour":"2026-08-16 06:00 PT","done":4,"pending":3,"note":"Exact-SHA canonical gate passed at d1167f4f6 in 539.65s with all seven certification units; archived the completed refresh/CPU queue and kept Statsd live-soak acceptance open"},{"hour":"2026-08-16 07:00 PT","done":4,"pending":3,"note":"Fixed fractional Statsd prune deadlines and cross-app watchd test attribution; all nine canonical functional lanes passed at 7dd4c7633, while certification tests passed but host postflight I/O admission refused the loaded soak host"}] -->

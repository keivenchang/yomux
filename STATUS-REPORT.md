# Progress

Updated: 2026-08-18 09:17 AM PT
Worktree: `/home/keivenc/dev/yolomux.dev7771`

**Goal:** Eliminate the urgent refresh-fanout and background-CPU regression, then finish the remaining v0.7.8 queues

**Goal totals:** 8/8 done (100%); 0 TODO.

## Goal checklist

- [x] 1. Bound single-browser refresh fan-out and eliminate recurring GIL-bound web CPU with timing-attributed, generation-keyed owners and matched live Chrome evidence
- [x] 2. Fix relative product-root resolution and prove a restarted server writes no unexpected product state under $HOME
- [x] 3. Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone
- [x] 4. Decide whether multi-machine connectivity has a justified bounded workflow, recording NO_BUILD or one separately approved implementation queue
- [x] 5. Remove the standalone YO!cost panel and place Cost immediately after Graphs inside YO!stats, migrating legacy saved references without breaking restored layouts
- [x] 6. Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb
- [x] 7. Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts
- [x] 8. Restore usable Preview touch scrolling and native xterm typing on real mobile devices, then deliver responsive single-pane focus behavior without desktop regressions

**Supporting queue totals:** 25/25 done; 0 pending. All four supporting queues are archived.

## Supporting queue set

| queue | done | pending | complete |
| --- | ---: | ---: | ---: |
| `docs/DONE/2026-08/DONE.stats-snapshot-401-no-backoff.md` | 6 | 0 | 100% |
| `docs/DONE/2026-08/DONE.orphaned-local-service-daemons.md` | 9 | 0 | 100% |
| `docs/DONE/2026-08/DONE.jobd-reload-fanout-admission.md` | 6 | 0 | 100% |
| `docs/DONE/2026-08/DONE.transcript-prune-stat-race.md` | 4 | 0 | 100% |

## All queue checkboxes

- [x] Stop snapshot polling on `authentication_required` and surface signed-out state.
- [x] Route ping, client-events, stats-capabilities, and stats-stream 401s through the same terminal owner.
- [x] Add a red-first snapshot 401 no-further-request regression.
- [x] Prove a 401 permits at most one further request.
- [x] Confirm signed-out state in a real browser.
- [x] Pass all canonical functional lanes for the 401 queue and record the exact-SHA certification refusal.
- [x] Establish whether the 05:01 `jobd.produce` burst and orphan daemons are related.
- [x] Bind every local-service daemon lifetime to its owning server.
- [x] Reap matching prior-generation orphans on startup.
- [x] Prune accumulated local-service lock files.
- [x] Decide and implement ownership for YO!agent-spawned `yag-*` daemons.
- [x] Prove daemon exit when its owning server dies.
- [x] Prove fresh startup leaves no prior-generation orphan.
- [x] Clear current proven orphans and record before/after counts.
- [x] Pass all canonical functional lanes for the daemon queue and record the exact-SHA certification refusal.
- [x] Reproduce the exact reload fanout and identify the first incorrect transition.
- [x] Coalesce only semantically identical reload-triggered filesystem work through the shared owner.
- [x] Keep live jobd admission and operation completion available under bounded reload fanout.
- [x] Add exact and ordering-varied red-first reload regressions.
- [x] Prove the exact reload journey has no jobd 503 and every accepted operation terminates.
- [x] Pass the focused pytest/browser lanes for the split reload-fanout queue.
- [x] Treat missing transcript cache files as successful prune outcomes at every stat site.
- [x] Add missing-at-sort and missing-at-size regressions.
- [x] Prove missing files log nothing while genuine OSErrors remain loud.
- [x] Pass all canonical functional lanes for the transcript-prune queue and record the exact-SHA certification refusal.

## Pending goal items

- None.

## Landing evidence

- Candidate `80b4bf8ce` passed all nine functional lanes: py-compile, static, node-syntax, node-layout, non-browser pytest, browser pytest, E2E pytest, timing-sensitive serial pytest, and whitespace.
- Exact-SHA certification did not pass. Two clean-checkout certification-only attempts exited 4 after measured shared-host qualification refusals: first I/O stall fractions 0.069948/0.071954 exceeded 0.051/0.056; second I/O full stall 0.051087/0.051029 exceeded 0.051 and one CPU-stall sample 0.074572 exceeded 0.067. This is recorded under Keiven's tiered evidence exception; no tag, push, certification pass, or production restart is claimed.

## Hourly history (last 24 hours)

| PT hour | done | pending | delta done | note |
| --- | ---: | ---: | ---: | --- |
| 2026-08-17 09:00 PT | 7 | 1 | - | Keiven's real-iPad screenshots exposed the 44 px editor controls overlapping the absolutely centered Preview-font group and then showed the corrected row was too tall; the 577 CSS-pixel regression failed first for both states, coarse-pointer controls now remain 44 px wide but use a 36 px toolbar height, and restarted 7771 PID 528747 serves byte-identical corrected CSS pending iPad retest |
| 2026-08-17 10:00 PT | 7 | 1 | +0 | Keiven rejected special iPad editor button sizing after the 36 px row still consumed excessive space; the 577 CSS-pixel regression failed first, then passed with desktop-identical control geometry while retaining non-overlapping flex ownership for the Preview-font and action groups; restarted 7771 PID 1333059 serves byte-identical corrected CSS pending iPad retest |
| 2026-08-17 11:00 PT | 8 | 0 | +1 | Keiven accepted Preview scrolling, terminal input, and copy/paste on iPadOS 26.6 in a split pane; the final compact-control pins and architecture ratchet passed the static and mobile boot lanes, the disk-busy certification refusal was recorded under the reduced evidence bar, and v0.7.8 reached 8/8 |
| 2026-08-17 12:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 13:00 PT | 8 | 0 | +0 | Fixed 300s Daemons load extrema at the server fold instead of fabricating min/max from average; focused materializer and 77/77 panel tests plus the node-layout lane passed, and restarted 7772 PID 55514 serves a live 300s approvald bucket at 0.0% min, 3.331% avg, 14.844% max. |
| 2026-08-17 14:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 15:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 16:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 17:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 18:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 19:00 PT | 8 | 0 | +0 | Added a real mouse-driven Daemons Avg/Max/Min browser regression, replaced Markdown source preprocessing with a post-parse numbered-task presentation transform, fixed Working/footer classification plus current-question precedence, passed 179 detector tests and the 26-test architecture suite, and raised the supporting denominator from 10/11 to 28/31; only the three landing-gate/archive boxes remain. |
| 2026-08-17 20:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 21:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 22:00 PT | 8 | 0 | +0 |  |
| 2026-08-17 23:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 00:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 01:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 02:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 03:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 04:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 05:00 PT | 8 | 0 | +0 |  |
| 2026-08-18 06:00 PT | 8 | 0 | +0 | Finished the scheduled-prune ownership and bounded-coverage-read items: 180 focused service/storage tests passed, the frozen production-shaped startup read fell from 108,146 to 57,312 coverage rows, and eight accelerated 1 Hz appends performed zero coverage rescans. |
| 2026-08-18 07:00 PT | 8 | 0 | +0 | Advanced the three audit queues to 16/19: all nine functional gate lanes passed; exact-SHA certification refused the dirty checkout and a measured I/O-stall host sample, so the three gate boxes remain open pending authorized landing. Five stale jobd groups plus one legacy approvald were removed, production statsd locks fell from 27 to one active generation, and restarted 7771 PID 321291 serves the verified bundle with valid service parentage. |
| 2026-08-18 08:00 PT | 8 | 0 | +0 | Completed and archived the 6/6 reload-fanout split queue. The first defect was duplicate cold watch-diff requests reserving completion capacity before jobd coalescing; one keyed JobdOperationService flight now shares raw work while preserving separate receipts. Exact/property tests, 169 Node checks, the two-client browser reload journey, and restarted-7771 deterministic live fanout passed with ten accepted operations, ten terminals, ten acknowledgments, and zero jobd/watch-diff 503s. |
| 2026-08-18 09:00 PT | 8 | 0 | +3 | Closed and archived the three requested queues at 25/25 supporting checkboxes after candidate `80b4bf8ce` passed all nine functional lanes. The adversarial audit also fixed a cache-recheck follower that could retain a never-terminal 202 and a missing-at-sort transcript prune race. Exact-SHA certification was attempted twice and refused on measured shared-host I/O/CPU pressure; no certification pass is claimed. |

<!-- progress-report-goal: {"goal":"Eliminate the urgent refresh-fanout and background-CPU regression, then finish the remaining v0.7.8 queues","items":[{"id":1,"text":"Bound single-browser refresh fan-out and eliminate recurring GIL-bound web CPU with timing-attributed, generation-keyed owners and matched live Chrome evidence","done":true},{"id":2,"text":"Fix relative product-root resolution and prove a restarted server writes no unexpected product state under $HOME","done":true},{"id":3,"text":"Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone","done":true},{"id":4,"text":"Decide whether multi-machine connectivity has a justified bounded workflow, recording NO_BUILD or one separately approved implementation queue","done":true},{"id":5,"text":"Remove the standalone YO!cost panel and place Cost immediately after Graphs inside YO!stats, migrating legacy saved references without breaking restored layouts","done":true},{"id":6,"text":"Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb","done":true},{"id":7,"text":"Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts","done":true},{"id":8,"text":"Restore usable Preview touch scrolling and native xterm typing on real mobile devices, then deliver responsive single-pane focus behavior without desktop regressions","done":true}],"queues":[]} -->
<!-- progress-report-history: [{"hour":"2026-08-17 09:00 PT","done":7,"pending":1,"note":"Keiven's real-iPad screenshots exposed the 44 px editor controls overlapping the absolutely centered Preview-font group and then showed the corrected row was too tall; the 577 CSS-pixel regression failed first for both states, coarse-pointer controls now remain 44 px wide but use a 36 px toolbar height, and restarted 7771 PID 528747 serves byte-identical corrected CSS pending iPad retest"},{"hour":"2026-08-17 10:00 PT","done":7,"pending":1,"note":"Keiven rejected special iPad editor button sizing after the 36 px row still consumed excessive space; the 577 CSS-pixel regression failed first, then passed with desktop-identical control geometry while retaining non-overlapping flex ownership for the Preview-font and action groups; restarted 7771 PID 1333059 serves byte-identical corrected CSS pending iPad retest"},{"hour":"2026-08-17 11:00 PT","done":8,"pending":0,"note":"Keiven accepted Preview scrolling, terminal input, and copy/paste on iPadOS 26.6 in a split pane; the final compact-control pins and architecture ratchet passed the static and mobile boot lanes, the disk-busy certification refusal was recorded under the reduced evidence bar, and v0.7.8 reached 8/8"},{"hour":"2026-08-17 12:00 PT","done":8,"pending":0},{"hour":"2026-08-17 13:00 PT","done":8,"pending":0,"note":"Fixed 300s Daemons load extrema at the server fold instead of fabricating min/max from average; focused materializer and 77/77 panel tests plus the node-layout lane passed, and restarted 7772 PID 55514 serves a live 300s approvald bucket at 0.0% min, 3.331% avg, 14.844% max."},{"hour":"2026-08-17 14:00 PT","done":8,"pending":0},{"hour":"2026-08-17 15:00 PT","done":8,"pending":0},{"hour":"2026-08-17 16:00 PT","done":8,"pending":0},{"hour":"2026-08-17 17:00 PT","done":8,"pending":0},{"hour":"2026-08-17 18:00 PT","done":8,"pending":0},{"hour":"2026-08-17 19:00 PT","done":8,"pending":0,"note":"Added a real mouse-driven Daemons Avg/Max/Min browser regression, replaced Markdown source preprocessing with a post-parse numbered-task presentation transform, fixed Working/footer classification plus current-question precedence, passed 179 detector tests and the 26-test architecture suite, and raised the supporting denominator from 10/11 to 28/31; only the three landing-gate/archive boxes remain."},{"hour":"2026-08-17 20:00 PT","done":8,"pending":0},{"hour":"2026-08-17 21:00 PT","done":8,"pending":0},{"hour":"2026-08-17 22:00 PT","done":8,"pending":0},{"hour":"2026-08-17 23:00 PT","done":8,"pending":0},{"hour":"2026-08-18 00:00 PT","done":8,"pending":0},{"hour":"2026-08-18 01:00 PT","done":8,"pending":0},{"hour":"2026-08-18 02:00 PT","done":8,"pending":0},{"hour":"2026-08-18 03:00 PT","done":8,"pending":0},{"hour":"2026-08-18 04:00 PT","done":8,"pending":0},{"hour":"2026-08-18 05:00 PT","done":8,"pending":0},{"hour":"2026-08-18 06:00 PT","done":8,"pending":0,"note":"Finished the scheduled-prune ownership and bounded-coverage-read items: 180 focused service/storage tests passed, the frozen production-shaped startup read fell from 108,146 to 57,312 coverage rows, and eight accelerated 1 Hz appends performed zero coverage rescans."},{"hour":"2026-08-18 07:00 PT","done":8,"pending":0,"note":"Advanced the three audit queues to 16/19: all nine functional gate lanes passed; exact-SHA certification refused the dirty checkout and a measured I/O-stall host sample, so the three gate boxes remain open pending authorized landing. Five stale jobd groups plus one legacy approvald were removed, production statsd locks fell from 27 to one active generation, and restarted 7771 PID 321291 serves the verified bundle with valid service parentage."},{"hour":"2026-08-18 08:00 PT","done":8,"pending":0,"note":"Completed and archived the 6/6 reload-fanout split queue. The first defect was duplicate cold watch-diff requests reserving completion capacity before jobd coalescing; one keyed JobdOperationService flight now shares raw work while preserving separate receipts. Exact/property tests, 169 Node checks, the two-client browser reload journey, and restarted-7771 deterministic live fanout passed with ten accepted operations, ten terminals, ten acknowledgments, and zero jobd/watch-diff 503s."},{"hour":"2026-08-18 09:00 PT","done":8,"pending":0}] -->

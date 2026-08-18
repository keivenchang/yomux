# Progress

Updated: 2026-08-17 09:26 PM PT
Worktree: `/home/keivenc/dev/yolomux.dev7771`

**Goal:** Eliminate the urgent refresh-fanout and background-CPU regression, then finish the remaining v0.7.8 queues

**Feature goal totals:** 8/8 implemented (100%). **Release closure:** 31/31 supporting queue checkboxes done (100%); 0 pending.

## Goal checklist

- [x] 1. Bound single-browser refresh fan-out and eliminate recurring GIL-bound web CPU with timing-attributed, generation-keyed owners and matched live Chrome evidence
- [x] 2. Fix relative product-root resolution and prove a restarted server writes no unexpected product state under $HOME
- [x] 3. Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone
- [x] 4. Decide whether multi-machine connectivity has a justified bounded workflow, recording NO_BUILD or one separately approved implementation queue
- [x] 5. Remove the standalone YO!cost panel and place Cost immediately after Graphs inside YO!stats, migrating legacy saved references without breaking restored layouts
- [x] 6. Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb
- [x] 7. Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts
- [x] 8. Restore usable Preview touch scrolling and native xterm typing on real mobile devices, then deliver responsive single-pane focus behavior without desktop regressions

**Supporting queue totals:** 31/31 done; 0 pending.

## Active queues

- None for v0.7.8. The final three queues are archived in `docs/DONE/2026-08/`.

## Completed queue closure

- [x] Working/footer detector: 10/10, archived as `DONE.0-7-8-working-detector-footer-fragility.md`.
- [x] Daemons Min/Avg/Max: 11/11, archived as `DONE.0-7-8-daemon-load-min-max-average.md`.
- [x] Markdown numbered-task continuity: 10/10, archived as `DONE.0-7-8-markdown-task-list-continuity.md`.

## Pending goal items

- None. The local v0.7.8 goal and its supporting queues are complete.

## External authorization required

- Push/CPS to origin remains unauthorized.
- Production 7770 remains on unfixed `0da574142`; its Statsd was measured at 54.5% of one core. Restarting or promoting 7770 requires Keiven's separate explicit authorization.

## Hourly history (last 24 hours)

| PT hour | done | pending | delta done | note |
| --- | ---: | ---: | ---: | --- |
| 2026-08-16 21:00 PT | 6 | 2 | - | Corrected the certification blocker to disk busy, added `gputest` through 7770's production-compatible bare-name exclusion field without restarting it, and measured a qualified post-change host at disk busy 0.032300; the pre-change crawl had already gone idle, so this is not claimed as a controlled causal A/B |
| 2026-08-16 22:00 PT | 6 | 2 | +0 | Live frontier evidence corrected the disk owner from already-excluded `gputest` to `.migration-backup` and `commits`; production-compatible absolute exclusions and an in-place index refresh cut indexd writes from 89.8 MB to 36.9 KB per 15 seconds, and a fresh host qualifier passed at disk busy 0.089936 without restarting 7770. Current 7771 PID 1547423 serves the debug-armed mobile fixes byte-for-byte at JavaScript SHA-256 `9106f279133bcbcfd42440c3ad94f0ac4f31ae6d2697f3d6b908116bbbf428b1`; real-iPad acceptance is handed to Keiven at `https://10.110.40.68:7771/?debug=1` and remains open pending his capture. |
| 2026-08-16 23:00 PT | 6 | 2 | +0 | Accepted Statsd's 35.5x production-cardinality regression and 16,205-sample four-hour live run in place of the waived 24-hour wait; mobile journey now passes Preview open, xterm input, pan, and first pane return, with the remaining focused failure narrowed to the global startup toast covering a shifted mobile tab row. |
| 2026-08-17 00:00 PT | 7 | 1 | +1 | Accepted accelerated Statsd cardinality evidence, archived its 14/14 queue, fixed seven mobile/browser expectation drifts plus two real touch-target defects, passed static and all 19 Node shards, and deployed byte-identical debug-armed source to 7771 PID 1593400; real-iPad confirmation remains |
| 2026-08-17 01:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 02:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 03:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 04:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 05:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 06:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 07:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 08:00 PT | 7 | 1 | +0 |  |
| 2026-08-17 09:00 PT | 7 | 1 | +0 | Keiven's real-iPad screenshots exposed the 44 px editor controls overlapping the absolutely centered Preview-font group and then showed the corrected row was too tall; the 577 CSS-pixel regression failed first for both states, coarse-pointer controls now remain 44 px wide but use a 36 px toolbar height, and restarted 7771 PID 528747 serves byte-identical corrected CSS pending iPad retest |
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
| 2026-08-17 21:00 PT | 8 | 0 | +0 | Committed v0.7.8 candidate `6de915001`, then ran the 25%-CPU full gate: static, compile, syntax, whitespace, all Node shards, 17,230+ non-browser tests, the 887.61-second browser lane, and timing-sensitive serial passed. E2E passed 119/121 before two YO!agent nodes exposed Claude's startup `1M context` line being misread as a live one-minute counter; fixed at shared detector owner `0400dbe8a`, then 11/11 parser checks and both exact E2E nodes passed alone. Certification-only certified 7/7 units on qualified clean SHA `3be481152` in 103.17 seconds. The final three queues reached 31/31 and were archived; push/CPS and production 7770 restart remain separately unauthorized. |

<!-- progress-report-goal: {"goal":"Eliminate the urgent refresh-fanout and background-CPU regression, then finish the remaining v0.7.8 queues","items":[{"id":1,"text":"Bound single-browser refresh fan-out and eliminate recurring GIL-bound web CPU with timing-attributed, generation-keyed owners and matched live Chrome evidence","done":true},{"id":2,"text":"Fix relative product-root resolution and prove a restarted server writes no unexpected product state under $HOME","done":true},{"id":3,"text":"Remove the three remote tags that still expose old license history, only after explicit tag-mutation authorization, then verify all refs from a fresh clone","done":true},{"id":4,"text":"Decide whether multi-machine connectivity has a justified bounded workflow, recording NO_BUILD or one separately approved implementation queue","done":true},{"id":5,"text":"Remove the standalone YO!cost panel and place Cost immediately after Graphs inside YO!stats, migrating legacy saved references without breaking restored layouts","done":true},{"id":6,"text":"Bound statsd WAL growth, restore retention/compaction guarantees, and prove whether WAL size causes the reported multi-hour CPU climb","done":true},{"id":7,"text":"Land the macOS path-alias and xterm teardown fixes with truthful Quick Open exclusion behavior and preserved generated artifacts","done":true},{"id":8,"text":"Restore usable Preview touch scrolling and native xterm typing on real mobile devices, then deliver responsive single-pane focus behavior without desktop regressions","done":true}],"queues":[]} -->
<!-- progress-report-history: [{"hour":"2026-08-16 21:00 PT","done":6,"pending":2,"note":"Corrected the certification blocker to disk busy, added `gputest` through 7770's production-compatible bare-name exclusion field without restarting it, and measured a qualified post-change host at disk busy 0.032300; the pre-change crawl had already gone idle, so this is not claimed as a controlled causal A/B"},{"hour":"2026-08-16 22:00 PT","done":6,"pending":2,"note":"Live frontier evidence corrected the disk owner from already-excluded `gputest` to `.migration-backup` and `commits`; production-compatible absolute exclusions and an in-place index refresh cut indexd writes from 89.8 MB to 36.9 KB per 15 seconds, and a fresh host qualifier passed at disk busy 0.089936 without restarting 7770. Current 7771 PID 1547423 serves the debug-armed mobile fixes byte-for-byte at JavaScript SHA-256 `9106f279133bcbcfd42440c3ad94f0ac4f31ae6d2697f3d6b908116bbbf428b1`; real-iPad acceptance is handed to Keiven at `https://10.110.40.68:7771/?debug=1` and remains open pending his capture."},{"hour":"2026-08-16 23:00 PT","done":6,"pending":2,"note":"Accepted Statsd's 35.5x production-cardinality regression and 16,205-sample four-hour live run in place of the waived 24-hour wait; mobile journey now passes Preview open, xterm input, pan, and first pane return, with the remaining focused failure narrowed to the global startup toast covering a shifted mobile tab row."},{"hour":"2026-08-17 00:00 PT","done":7,"pending":1,"note":"Accepted accelerated Statsd cardinality evidence, archived its 14/14 queue, fixed seven mobile/browser expectation drifts plus two real touch-target defects, passed static and all 19 Node shards, and deployed byte-identical debug-armed source to 7771 PID 1593400; real-iPad confirmation remains"},{"hour":"2026-08-17 01:00 PT","done":7,"pending":1},{"hour":"2026-08-17 02:00 PT","done":7,"pending":1},{"hour":"2026-08-17 03:00 PT","done":7,"pending":1},{"hour":"2026-08-17 04:00 PT","done":7,"pending":1},{"hour":"2026-08-17 05:00 PT","done":7,"pending":1},{"hour":"2026-08-17 06:00 PT","done":7,"pending":1},{"hour":"2026-08-17 07:00 PT","done":7,"pending":1},{"hour":"2026-08-17 08:00 PT","done":7,"pending":1},{"hour":"2026-08-17 09:00 PT","done":7,"pending":1,"note":"Keiven's real-iPad screenshots exposed the 44 px editor controls overlapping the absolutely centered Preview-font group and then showed the corrected row was too tall; the 577 CSS-pixel regression failed first for both states, coarse-pointer controls now remain 44 px wide but use a 36 px toolbar height, and restarted 7771 PID 528747 serves byte-identical corrected CSS pending iPad retest"},{"hour":"2026-08-17 10:00 PT","done":7,"pending":1,"note":"Keiven rejected special iPad editor button sizing after the 36 px row still consumed excessive space; the 577 CSS-pixel regression failed first, then passed with desktop-identical control geometry while retaining non-overlapping flex ownership for the Preview-font and action groups; restarted 7771 PID 1333059 serves byte-identical corrected CSS pending iPad retest"},{"hour":"2026-08-17 11:00 PT","done":8,"pending":0,"note":"Keiven accepted Preview scrolling, terminal input, and copy/paste on iPadOS 26.6 in a split pane; the final compact-control pins and architecture ratchet passed the static and mobile boot lanes, the disk-busy certification refusal was recorded under the reduced evidence bar, and v0.7.8 reached 8/8"},{"hour":"2026-08-17 12:00 PT","done":8,"pending":0},{"hour":"2026-08-17 13:00 PT","done":8,"pending":0,"note":"Fixed 300s Daemons load extrema at the server fold instead of fabricating min/max from average; focused materializer and 77/77 panel tests plus the node-layout lane passed, and restarted 7772 PID 55514 serves a live 300s approvald bucket at 0.0% min, 3.331% avg, 14.844% max."},{"hour":"2026-08-17 14:00 PT","done":8,"pending":0},{"hour":"2026-08-17 15:00 PT","done":8,"pending":0},{"hour":"2026-08-17 16:00 PT","done":8,"pending":0},{"hour":"2026-08-17 17:00 PT","done":8,"pending":0},{"hour":"2026-08-17 18:00 PT","done":8,"pending":0},{"hour":"2026-08-17 19:00 PT","done":8,"pending":0,"note":"Added a real mouse-driven Daemons Avg/Max/Min browser regression, replaced Markdown source preprocessing with a post-parse numbered-task presentation transform, fixed Working/footer classification plus current-question precedence, passed 179 detector tests and the 26-test architecture suite, and raised the supporting denominator from 10/11 to 28/31; only the three landing-gate/archive boxes remain."},{"hour":"2026-08-17 20:00 PT","done":8,"pending":0},{"hour":"2026-08-17 21:00 PT","done":8,"pending":0}] -->

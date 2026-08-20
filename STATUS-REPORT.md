# Progress

Updated: 2026-08-19 07:31 PM PT
Worktree: `/home/keivenc/dev/yolomux.dev7771`

**Goal:** Ship v0.7.9 from the two nearly complete queues; defer every other active outcome and queue to v0.7.10.

## Release split

| release | outcome progress | supporting queue progress | scope |
| --- | ---: | ---: | --- |
| v0.7.9 | 2/2 | 16/16 | Exact tmux session targeting and HTTP request-framing attribution are complete. |
| v0.7.10 | 0/1 declared integration; broader goal not opened | 30/303 | Every other 35 active queue files from the split snapshot, plus merge `dev/finder-diff-process-stats-7773`. |

The outcome and supporting-queue denominators are independent. v0.7.9 is closed and its two drained queue files are archived under `docs/DONE/2026-08/`. Immediately before archival, the source queues still displayed 7/11 and 4/5 because five final verification boxes had not been flipped; the two DONE records explicitly reconcile those five criteria with final evidence, so queue deletion is not used as proof of 16/16. v0.7.10 has one explicitly declared integration outcome and contains every other active queue from the split snapshot, but its broader release-goal denominator has not been declared; 30/303 is a queue-only backlog snapshot, not a promise that every checkbox ships together.

## v0.7.9 completed ledger

- [x] 1. Exact tmux session targeting: closed 11/11. The shared target owner emits `=<session>:`; a missing exact target refuses instead of selecting a prefix sibling. Archived in `docs/DONE/2026-08/DONE.0-7-9-tmux-exact-session-target.md`.
- [x] 2. HTTP request-framing attribution: closed 5/5. A response over an unread request body commits `Connection: close`; the observed 414 was body bytes parsed after the terminal response, not a long URL. Archived in `docs/DONE/2026-08/DONE.0-7-9-request-uri-too-long-attribution.md`.

Runtime candidate `71ef69fac` passed the unmodified exact-SHA canonical gate: 9/9 functional lanes and 7/7 certification units in 602.86 seconds, then fast-forwarded into clean local main. Its fresh first-attempt authenticated acceptance measured a 90.142-second settle and 603.504-second clean observation with 118 samples, no final integrity failures, and identical start/end SHA, PID, CWD, and served bundle hash. The controlled 30-second negative phase attributed one accepted and rendered `/api/yolo-rules` HTTP 500 as the sole cause, with zero unrelated failures and successful DOM, clipboard, retained-state, upload, and storage redaction. On the same identity, exact absent-target refusal returned typed 404, rename and kill returned 200 while sibling `soak` survived, and one authenticated 98,258-byte unread-body POST returned one 404 in 0.001650 seconds with `Connection: close`, EOF, zero trailing bytes, zero 414 responses, and no hang. The focused HTTP selection collected 164 tests: 162 passed and 2 skipped. `781536ce` remains the historical simplified Debug Logs fix.

## v0.7.10 explicit integration

- [ ] Merge `dev/finder-diff-process-stats-7773` into the v0.7.10 integration line and validate the composed result. At the split snapshot, the branch was clean and pointed at local-main SHA `c651e091f`, so there was no merge delta yet.

## v0.7.10 deferred queues

Partially complete:

- `queues/backlog/DOIT.p1.e2.merge-macos-boot-tmux-env.md` (8/12)
- `queues/backlog/DOIT.p1.e3.filesystem-delete-lane-split.md` (7/10)
- `queues/backlog/DOIT.p1.e5.memory-hog.md` (8/23)
- `queues/backlog/DOIT.p2.e2.filesystem-descriptor-authorization.md` (7/13)

Untouched:

- `queues/backlog/DOIT.p1.e2.background-owner-live-fleet-acceptance.md` (0/5)
- `queues/backlog/DOIT.p1.e3.operation-journal-port-scope.md` (0/8)
- `queues/backlog/DOIT.p1.e3.queued-operation-pending-indicator.md` (0/8)
- `queues/backlog/DOIT.p1.e4.differ-deadline-attribution.md` (0/11)
- `queues/backlog/DOIT.p1.e4.native-watch-network-fs.md` (0/17)
- `queues/backlog/DOIT.p1.e5.activity-summary-async-replacement.md` (0/12)
- `queues/backlog/DOIT.p1.e5.backend-lifetime-supervision.md` (0/14)
- `queues/backlog/DOIT.p1.e5.structured-agent-control.md` (0/6)
- `queues/backlog/DOIT.p2.e1.agent-tokens-chart-top-n.md` (0/12)
- `queues/backlog/DOIT.p2.e2.cross-host-read-views.md` (0/6)
- `queues/backlog/DOIT.p2.e2.jobd-bulk-fairness.md` (0/6)
- `queues/backlog/DOIT.p2.e3.agent-tui-golden-frames.md` (0/5)
- `queues/backlog/DOIT.p2.e3.app-menu-and-finder-metadata.md` (0/6)
- `queues/backlog/DOIT.p2.e3.editor-power-keys.md` (0/5)
- `queues/backlog/DOIT.p2.e3.hidden-terminal-websocket-suspension.md` (0/5)
- `queues/backlog/DOIT.p2.e3.session-summary-context.md` (0/5)
- `queues/backlog/DOIT.p2.e3.session-vitals.md` (0/5)
- `queues/backlog/DOIT.p2.e3.working-path-inference.md` (0/5)
- `queues/backlog/DOIT.p2.e3.yoagent-artifact-handoff.md` (0/5)
- `queues/backlog/DOIT.p2.e3.yoagent-watch-jobs.md` (0/5)
- `queues/backlog/DOIT.p2.e4.latency-boundaries.md` (0/10)
- `queues/backlog/DOIT.p2.e4.launch-resume-reply-worktree.md` (0/7)
- `queues/backlog/DOIT.p2.e4.localization-completion.md` (0/5)
- `queues/backlog/DOIT.p2.e4.multi-server-common-service-decision.md` (0/11)
- `queues/backlog/DOIT.p2.e4.worktree-task-hub.md` (0/5)
- `queues/backlog/DOIT.p2.e4.yolo-policy-and-approval.md` (0/7)
- `queues/backlog/DOIT.p2.e5.js-framework.md` (0/9)
- `queues/backlog/DOIT.p2.e5.layout-render-reconciliation.md` (0/8)
- `queues/backlog/DOIT.p2.e5.sse-payload-delivery.md` (0/9)
- `queues/backlog/DOIT.p2.e5.stats-ring-followups.md` (0/10)
- `queues/backlog/DOIT.p2.e5.tmux-session-destruction.md` (0/13)

## Historical context

P0 queue decisions are recorded, and production 7770 runs the previously shipped daemon-lifetime fixes. Both are outside the v0.7.9 and v0.7.10 denominators.

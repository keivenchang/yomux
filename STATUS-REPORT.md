# Progress

Updated: 2026-08-24 05:16 PM PT
Worktree: `/tmp/yolomux-v0714-gate.gPcswA`

**Goal:** Publish YOLOmux v0.7.14 from the v0.7.13 boundary with the reconciled authorization, lifetime, Stats, gate, and release-record fixes.

**Goal totals:** 5/6 done (83%); 1 TODO.

## Goal checklist

- [x] 1. Reconcile v0.7.13 into v0.7.14 without losing newer ring/watchd behavior.
- [x] 2. Produce one v0.7.14 commit after v0.7.13 with both version owners aligned.
- [x] 3. Pass focused ring/watchd/process/Node gates.
- [x] 4. Pass the canonical functional gate on the exact release subject.
- [x] 5. Record the accepted certification outcome and obtain independent audit.
- [ ] 6. Land, tag, and push the release refs.

**DOIT inventory:** 5 scoped files — 23/63 checkboxes done and 40 pending.

## DOIT inventory

### Scoped queues

| DOIT | checkboxes | complete | next pending item |
| --- | ---: | ---: | --- |
| `queues/backlog/DOIT.p1.e3.filesystem-delete-lane-split.md` | 4/10 | 40% | Add cross-class isolation, recursive receipt, cancellation, partial-failure, file/symlink/directory, and namespace-replacement regressions.… |
| `queues/backlog/DOIT.p1.e5.backend-lifetime-supervision.md` | 0/14 | 0% | Freeze the current topology and authority matrix: record every backend/sidecar, launcher/client claim, self-connection, idle rule, service… |
| `queues/backlog/DOIT.p1.e5.memory-hog.md` | 8/23 | 35% | Refresh the patch backup and its hash after the test change. |
| `queues/backlog/DOIT.p2.e2.descriptor-residuals.md` | 0/3 | 0% | Reconstruct and preserve the historical pre-fix descriptor failures only if a future release audit requires the literal `BLOCKED_SENTINEL_D… |
| `queues/backlog/DOIT.p2.e2.filesystem-descriptor-authorization.md` | 11/13 | 85% | Update `docs/specs/GUI.md`, `docs/DEVELOPMENT.md`, and operator-facing error text for the descriptor-bound contract; run focused filesystem… |

## Focused queue checkboxes

- No focused queue. The STATUS-scoped inventory above is still current.

## Pending goal items

- 6. Land, tag, and push the release refs.

## Hourly history (last 24 hours; STATUS-scoped DOIT inventory)

| PT hour | done | doing | DOIT inventory | delta | note |
| --- | ---: | --- | --- | ---: | --- |
| 2026-08-24 16:00 PT | 3/6 | None | 5 files; 23/63 checkboxes | - | Reconciled v0.7.14 is one commit after the v0.7.13 boundary with both runtime version owners at 0.7.14; focused ring, watchd/process, Node layout, and Stats panel checks passed. Exact-SHA canonical gate, independent audit, and release landing remain. |
| 2026-08-24 17:00 PT | 5/6 | None | 5 files; 23/63 checkboxes | +2 | Reconciled v0.7.14 is one commit after v0.7.13. The clean exact-SHA canonical gate passed all functional lanes and certified 7/7 units on a qualified host; independent audit returned GO with no findings. Release landing remains reserved to the coordinator. |

<!-- progress-report-goal: {"schema":2,"goal":"Publish YOLOmux v0.7.14 from the v0.7.13 boundary with the reconciled authorization, lifetime, Stats, gate, and release-record fixes.","items":[{"id":1,"text":"Reconcile v0.7.13 into v0.7.14 without losing newer ring/watchd behavior.","done":true},{"id":2,"text":"Produce one v0.7.14 commit after v0.7.13 with both version owners aligned.","done":true},{"id":3,"text":"Pass focused ring/watchd/process/Node gates.","done":true},{"id":4,"text":"Pass the canonical functional gate on the exact release subject.","done":true},{"id":5,"text":"Record the accepted certification outcome and obtain independent audit.","done":true},{"id":6,"text":"Land, tag, and push the release refs.","done":false}],"inventory_queues":["queues/backlog/DOIT.p1.e3.filesystem-delete-lane-split.md","queues/backlog/DOIT.p1.e5.backend-lifetime-supervision.md","queues/backlog/DOIT.p1.e5.memory-hog.md","queues/backlog/DOIT.p2.e2.descriptor-residuals.md","queues/backlog/DOIT.p2.e2.filesystem-descriptor-authorization.md"],"focused_queue":null} -->
<!-- progress-report-history: [{"schema":2,"hour":"2026-08-24 16:00 PT","done":3,"goal_total":6,"doing":null,"inventory":{"queues":["queues/backlog/DOIT.p1.e3.filesystem-delete-lane-split.md","queues/backlog/DOIT.p1.e5.backend-lifetime-supervision.md","queues/backlog/DOIT.p1.e5.memory-hog.md","queues/backlog/DOIT.p2.e2.descriptor-residuals.md","queues/backlog/DOIT.p2.e2.filesystem-descriptor-authorization.md"],"files":5,"done":23,"pending":40},"delta":null,"note":"Reconciled v0.7.14 is one commit after the v0.7.13 boundary with both runtime version owners at 0.7.14; focused ring, watchd/process, Node layout, and Stats panel checks passed. Exact-SHA canonical gate, independent audit, and release landing remain."},{"schema":2,"hour":"2026-08-24 17:00 PT","done":5,"goal_total":6,"doing":null,"inventory":{"queues":["queues/backlog/DOIT.p1.e3.filesystem-delete-lane-split.md","queues/backlog/DOIT.p1.e5.backend-lifetime-supervision.md","queues/backlog/DOIT.p1.e5.memory-hog.md","queues/backlog/DOIT.p2.e2.descriptor-residuals.md","queues/backlog/DOIT.p2.e2.filesystem-descriptor-authorization.md"],"files":5,"done":23,"pending":40},"delta":2,"note":"Reconciled v0.7.14 is one commit after v0.7.13. The clean exact-SHA canonical gate passed all functional lanes and certified 7/7 units on a qualified host; independent audit returned GO with no findings. Release landing remains reserved to the coordinator."}] -->

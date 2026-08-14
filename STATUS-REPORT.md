# v0.7.5 Progress

Updated: 2026-08-13 04:59 PM PT
Worktree: `/home/keivenc/dev/yolomux.dev7771`

This file is the sole source of truth for v0.7.5 release order and progress. Active `DOIT.075.*.md` files own their detailed requirements; completed queues move to `docs/DONE/` as historical evidence. This report does not mirror them, and retired planning files must not be recreated.

**Goal:** Drain all five v0.7.5 queues in order, pass release gates, archive the queues, and complete live acceptance using only 7772 while keeping recovered 7771 stable

**Goal totals:** 1/7 done (14%); 6 TODO.

## Goal checklist

- [x] 1. Markdown preview image failure fixed from real same-page browser evidence
- [ ] 2. macOS branch merged with Linux and real-macOS acceptance plus named review
- [ ] 3. YO!share removed completely with a zero-hit reappearance guard
- [ ] 4. Thirteen 0.7.4 cleanup follow-ups completed
- [ ] 5. First-launch Codex auth, tri-state banner, and rotating YO completed
- [ ] 6. Release-level gates passed and all five queues archived
- [ ] 7. Restarted 7772 passed final authenticated real-browser acceptance; recovered 7771 remained unchanged after 11:32:22 AM PT

**Supporting active-queue totals:** 10/51 done; 41 pending. Queue 0 completed at 11/11 and moved to `docs/DONE/2026-08/DONE.0-7-5-markdown-image-401.md`. Queue 1 is 10/12: signed merge `87b2b4143` and reviewed Darwin fence correction `fd37d7707` fully contain `origin/fix/macos-v0.7.3`; Linux acceptance is complete, both remaining checkboxes require one clean exact-SHA canonical gate, and real-macOS acceptance is explicitly blocked because this host has no supported command-capable Mac path.

## Release scope, in execution order

The order is a dependency chain. Queue 0 is complete. Queues 1 through 4 retain their existing numbers and continue in order.

| # | queue | work | scope |
| ---: | --- | --- | ---: |
| 0 | `DOIT.075.0.markdown-image-401.md` | Authenticated /api/fs/raw returns 401 for preview images | 5 items |
| 1 | `DOIT.075.1.macos-merge.md` | Merge `origin/fix/macos-v0.7.3` | 12 items |
| 2 | `DOIT.075.2.yoshare-removal.md` | Delete YO!share completely | 13 items |
| 3 | `DOIT.075.3.cleanup-followups.md` | Close the 0.7.4 refactor leftovers | 13 items |
| 4 | `DOIT.075.4.first-launch-auth.md` | Codex auth on first launch, tri-state banner, rotating YO | 13 items |

### Why this order

- **Item 0 goes first** because it is a small user-visible break on the live server, not a refactor, and it does not touch any file the other four items touch.
- **Markdown image failure completed first.** Same-page evidence on 7772 showed both `/api/fs/raw` and `/api/ping` carried the auth cookie; ping returned 200 and raw reached authenticated jobd handling but returned a typed 503. Bounded artifact transfer plus the shared authenticated Blob owner now pass all eight functional gate lanes and real-browser acceptance. The historical 7771 response remains unexplained. At 11:27:58 AM PT, temporary merge markers in this watched worktree triggered the former 7771 dev process to re-exec and exit with `SyntaxError`; it was restored from the clean checkout at 11:32:22 AM PT as PID 3613344 and has remained unchanged since. All later test-runtime work uses 7772.
- **macOS merge second.** It is the only item with an external branch that keeps diverging. It overlaps the 0.7.4 work and has one real conflict in `yolomux_lib/local_services/registry.py`; resolve it by intent before deleting YO!share.
- **YO!share removal third.** It is the largest change and primarily deletes. Doing it on a merged green tree makes breakage attributable to the deletion.
- **Cleanup follow-ups fourth.** They touch files also affected by YO!share removal, so they apply to the smaller post-deletion tree.
- **First-launch auth last.** It is independent and user-visible, with the least overlap with the other queues.

## Hourly history (last 24 hours)

| PT hour | done | pending | delta done | note |
| --- | ---: | ---: | ---: | --- |
| 2026-08-12 12:00 PT | 2 | 3 | - | Package split reclosed after exact ordered 735/735 parity, zero imported test symbols, 12 focused passes, and no reviewed-test growth; queue 23/26 |
| 2026-08-12 13:00 PT | 4 | 1 | +2 | App-domain decomposition and architecture ratchets verified; queue 25/26. Extension docs written; browser/full-gate/live archive rung remains prohibited in this session. |
| 2026-08-12 14:00 PT | 4 | 1 | +0 |  |
| 2026-08-12 15:00 PT | 4 | 1 | +0 | Wave 1 assigned: fixture parents, local-service/file-index lifecycle, and gate architecture; 13 follow-ups had been appended before the P0 queue freeze. |
| 2026-08-12 16:00 PT | 4 | 1 | +0 |  |
| 2026-08-12 17:00 PT | 4 | 1 | +0 |  |
| 2026-08-12 21:00 PT | 4 | 1 | +0 | Clean checkpoint 907db7ac40c097c193122f25a707742ac88b3d49 created; first exact-SHA gate found one load-sensitive rename failure and an unqualified 0.978951 disk-busy preflight; exact focused rename journey then passed 5/5. |
| 2026-08-12 22:00 PT | 4 | 1 | +0 | Exact HEAD 69f7c8d93 was canonically certified (all eight lanes + 5/5 units, 436.60s) and restarted on 7771 as PID 458296 with bundle 04779d68. Authenticated soak then exposed one expected stale Finder-root retirement logged at warning after 103.5s. The shared reindex owner now records expected 403/404/credential-blocked change evidence at info; exact regression passed 1/1. This uncommitted source fix supersedes the certified/live candidate, so gate, restart, 600s soak, negative probe, manual browser journey, and archive remain open. |
| 2026-08-12 23:00 PT | 4 | 1 | +0 | Exact HEAD 8ac6110b3 passed focused functional recovery (non-browser 17,596 tests; browser lane 378.63s) and exact 5/5 certification in 86.75s. Restarted 7771 is PID 2107857 from dev7771 at 8ac6110b3 with bundle 04779d68. Clean authenticated soak passed 603.29s with zero server/browser/integrity failures; negative injection durably detected its sole HTTP 500 with full redaction proof and zero unrelated failures. Manual real-browser journey, queue 26/26, archive, and final closure-SHA canonical gate/restart remain open. |
| 2026-08-13 00:00 PT | 4 | 1 | +0 | P0 reached 26/26 in 81c6d9fe1 and was archived/deleted in clean closure SHA 7f1a89863. Exact committed-SHA browser acceptance r22 passed on PID 4189697 / bundle d784f475. The unmodified closure-SHA gate then failed one browser retirement-delta classification test (635 passed, 17 skipped, 9 xfailed); every other lane and exact 5/5 certification passed. Item 5 remains open pending root-cause classification and any required correction, a green canonical rerun, restart, and final live-browser verification. |
| 2026-08-13 01:00 PT | 4 | 1 | +0 | Root cause corrected and frozen in candidate 9b58faac0. Its canonical gate finished in 339.83s with 7/8 lanes green; pytest-browser returned 1 after 338.14s, while the retained report omitted the exact failed test output. Certification ran 0/5 units because host preflight exceeded both I/O-stall thresholds, and exact-SHA admission rejected the tracked status-report edit. Artifacts were retained; no restart or 0.7.5 work began. |
| 2026-08-13 02:00 PT | 4 | 1 | +0 | Candidate f3b6f8925 passed 7/8 canonical lanes plus qualified-host certification 5/5 with exact-SHA admission; 11 browser failures shared one stats fixture that omitted the new lexical owner. The shared fixture now evaluates 09_transport_lifecycle.js plus 84_stats_current.js together, and all affected modules pass 25/25 in 53.15s. Archived queue 26/26; release 4/5; 7771 was not restarted. |
| 2026-08-13 03:00 PT | 4 | 1 | +0 | Corrected worktree passed all 8/8 canonical functional lanes in 354.40s after deterministic Dockview and retirement-lifecycle regressions. Host I/O-stall preflight refused certification; independently, exact-SHA admission was false on seven tracked modifications. Release remains 4/5; 7771 was not restarted and no 0.7.5 work began. |
| 2026-08-13 04:00 PT | 4 | 1 | +0 | Status reconciled to the archived P0 queue at 26/26 and release closure at 4/5. Current corrected worktree remains functionally green 8/8 but lacks one clean exact-SHA qualified certification, restart, and live-browser acceptance. No 0.7.5 work began. |
| 2026-08-13 08:00 PT | 4 | 1 | +0 | Committed candidate 48fd32744 ran one clean exact-SHA gate: 7/8 functional lanes, with 635 browser passes and one 54.6 ms cold 24h stats snapshot over the unchanged 50 ms ceiling; certification ran 0/5 units because host preflight failed I/O qualification. The fixed wall verdict is being moved from the parallel semantic lane to the existing exclusive certification phase; release remains 4/5 and 7771 was not restarted. |
| 2026-08-13 09:00 PT | 4 | 1 | +0 | Candidate 73471e668 passed all 8/8 functional lanes in 430.71 seconds. Four of six exclusive certification units passed; chat-store and stats correctly refused before measuring when I/O PSI rose, so the exact-SHA gate is not certifiable and no ceiling breach was recorded. Release remains 4/5; 7771 was not restarted and no 0.7.5 work began. |
| 2026-08-13 10:00 PT | 5 | 0 | +1 | Exact release SHA 0d0af221a passed 8/8 functional lanes plus qualified 6/6 certification, restarted on 7771 as PID 1755199, passed the authenticated browser journey, 603.43-second clean soak, and sole-cause redacted negative probe, then landed as signed annotated v0.7.4 on origin. |
| 2026-08-13 12:00 PT | 0 | 7 | -5 | Queue 0 cookie branches closed from same-page evidence; Archimedes found and verified Darwin process-identity fence fix (120 focused tests passed) |
| 2026-08-13 14:00 PT | 1 | 6 | +1 | Queue 0 reached 11/11 and was archived after all eight functional gate lanes passed; certification correctly refused on host disk load and dirty-merge exact-SHA admission. Recovered 7771 remained unchanged after 11:32:22 AM PT; all test-runtime work used 7772. |
| 2026-08-13 15:00 PT | 1 | 6 | +0 | Queue 1 reached 9/12. Current-tree Linux gate passed all eight functional lanes and all six qualified-host certification units but exact-SHA admission correctly rejected the dirty merge. Restarted 7772 PID 3485809 passed the exact-identity real-Chrome six-service journey and 603.13-second clean soak; 7771 remained PID 3613344. Real-macOS acceptance is explicitly blocked because this Linux host has no supported command-capable Mac path and the uncommitted candidate is not transferable. |
| 2026-08-13 16:00 PT | 1 | 6 | +0 | Rewrote the published v0.7.0-v0.7.4 release line to one signed commit per consecutive boundary, preserving every released and branch-tip tree exactly. Queue 1 then moved from the old equivalent identities to rewritten `origin/main` a66b91d2c and `origin/fix/macos-v0.7.3` 03657d9f0 without changing its audited index tree 3ad69f999, final worktree tree cdda18156, 30 staged/35 unstaged/two untracked path layers, or either runtime PID. The unresolved merge remains uncommitted; 7771 remains PID 3613344 and 7772 remains PID 3485809. |

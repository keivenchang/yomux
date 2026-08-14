# PLAN.0.7.5.md - Release Plan For v0.7.5

Written 2026-08-12 PT in `yolomux.dev7771`. Every number below was measured, not estimated from memory.

## Theme

**Subtract.** 0.7.4 was a large behavior-preserving refactor. 0.7.5 finishes what it started: one platform gets merged in, one dead feature gets deleted, the refactor's own leftovers get closed, and one first-launch bug gets fixed. No new product surface.

## Scope, in execution order

The order is a dependency chain, not a preference. Each item edits files the next one also touches.

| # | Queue | What | Size |
| --- | --- | --- | --- |
| 1 | `DOIT.075.1.macos-merge.md` | Merge `origin/fix/macos-v0.7.3` | 2 commits, 29 files, +730/-170 |
| 2 | `DOIT.075.2.yoshare-removal.md` | Delete YO!share completely | 19,066 lines in 10 dedicated files, plus 15 JS + 8 CSS + 5 backend files edited |
| 3 | `DOIT.075.3.cleanup-followups.md` | Close the 0.7.4 refactor leftovers | 13 items |
| 4 | `DOIT.075.4.first-launch-auth.md` | Codex auth on first launch, tri-state banner, rotating YO | 3 items |

### Why this order

- **macOS first.** It is the only item with an external branch that keeps diverging. Every day it waits, the merge gets harder. It already overlaps 9 files with the 0.7.4 work and has one real conflict (`yolomux_lib/local_services/registry.py`, measured with `git merge-tree`). Merging it after the YO!share deletion would mean resolving that conflict against a tree that just lost 19k lines.
- **YO!share second.** It is the largest single change and it only deletes. Doing it on a merged, green tree means any breakage is unambiguously the deletion's fault. Doing it before the merge would make the macOS conflict set impossible to reason about.
- **Cleanup followups third.** They are refactor debt from 0.7.4 and touch the same files YO!share removal touches. Running them after the deletion means 13 items applied to a smaller tree, some of which may no longer exist.
- **First-launch auth last.** It is independent, small, and user-visible. It is the one item that can slip to 0.7.6 without blocking anything, so it goes where slipping is cheapest.

## Preconditions before item 1 starts

- [x] `DOIT.p0.0.7.4-cleanup.md` reached 26/26 in checkpoint `81c6d9fe1` and is archived in `docs/DONE/2026-08/`.
- [x] The exact 0.7.4 release SHA `0d0af221a451360937732b35e7849422264316d6` passed one unmodified `python3 tools/check.py`: all 8/8 functional lanes and all 6/6 exclusive certification units were green on a qualified host. The signed annotated `v0.7.4` tag and remote `main` both resolve to that SHA; gate evidence is retained at `/tmp/yolomux-check-runs/check-1786640138948169718-1621465.json` and `/tmp/yolomux-certification/cert-1786640138948306025-1621465`.
- [x] Restarted 7771 passed authenticated real-browser acceptance on release SHA `0d0af221a` as PID 1755199 with served bundle `4d3d6fd2841b53d81d11692b7cc0273c9922b66128649b723a7501267af255ec`, followed by a 603.43-second clean soak and the sole-cause redacted negative probe. Evidence is retained at `/tmp/yolomux-074-manual-0d0af221a-final-r2.json`, `/tmp/yolomux-074-clean-0d0af221a-final.json`, and `/tmp/yolomux-074-negative-0d0af221a-final.json`.

Do not start item 1 on a red or unverified 0.7.4 tree. A macOS merge conflict resolved on top of unknown breakage cannot be attributed to anything.

## Release gates

Every item, without exception:

- Canonical gate green, no new Warnings or Errors, no test deleted or skipped to get there.
- Generated assets (`static/yolomux.js`, `static/yolomux.css`) rebuilt from `static_src/` and matching.
- Live acceptance on a restarted dev server in a real browser. Not tests alone.

Release-level, once at the end:

- [ ] macOS acceptance on real hardware, or an explicit recorded statement that 0.7.5 ships without it and why.
- [ ] `git grep -iE "yoshare|share_view|shareView|share_token|shareToken|/api/share|/ws/share-"` returns zero hits outside git history and `docs/DONE/`.
- [ ] Version bumped, `docs/DONE/` updated, all four 0.7.5 queues deleted.

## Known risks

1. **`registry.py`.** macOS changed 131 lines of it; 0.7.4 split its state into three records. This is the single conflicted file in the trial merge and the most likely place for a silent macOS process-ownership regression. Resolve by intent, never by picking a side of the text.
2. **The word "shared".** `yolomux_lib/infra/shared_config_lock.py` and `tests/test_shared_config_lock.py` are not YO!share. A careless grep-and-delete during item 2 breaks something unrelated.
3. **Scope growth.** The 0.7.4 cleanup queue grew from 26 items to 39 mid-flight while completions stalled for three hours. 0.7.5 has four queues and 4+13+3 items plus one merge. New findings go into `queues/backlog/`, never into an in-flight 0.7.5 queue.

## Out of scope - deferred to `queues/backlog/`

21 queues, 225 open items, none of them started. They are unchanged, priority prefix intact.

**Worth your attention: 6 of them are p0.**

| queue | items |
| --- | ---: |
| `DOIT.p0.filesystem-descriptor-authorization.md` | 13 |
| `DOIT.p0.tmux-session-destruction.md` | 13 |
| `DOIT.p0.interactive-api-lanes.md` | 12 |
| `DOIT.p0.test-gate-no-hidden-retries.md` | 11 |
| `DOIT.p0.activity-summary-disable.md` | 10 |
| `DOIT.p0.js-framework.md` | 8 |

Deferring 67 p0 items is a decision, not an accident. If `filesystem-descriptor-authorization` or `tmux-session-destruction` is genuinely a security or data-loss p0, it belongs in 0.7.5 and this plan is wrong — say so and it moves back. Otherwise these are p0 in name only and the prefixes should be corrected so the label keeps meaning something.

The other 15 backlog queues: 7 p1 (82 items), 8 p2 (76 items).

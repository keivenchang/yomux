# DOIT — index

**PHASE 1 COMPLETE 2026-08-01.** Landed as branch **`phase1-regression-gate`** (5 commits, tree identical to `trunk-v0610`, zero product files changed). Gate: **141 tests · 44 pass · 60 fail · 36 xfail(strict) · 1 skip · 0 errors**. Per-section baseline is in `DOIT.regression-gate.md`.

**NEXT: Phase 2** in `MASTERPLAN.md` — stand up the trunk baseline formally, port the 0.6.12 specs (`RECOVERY.md` and `BACKEND_TEST_CONTRACT.md` intact; `BACKEND_ARCHITECTURE.md` **split** — keep the contracts, delete the target topology), then agree with the user which features come back.

Reorganised 2026-07-31. Thirteen queues with 175 scattered open boxes became eight files with one order of work. **Start at `MASTERPLAN.md`.**

## The decision that reorganised everything

`v0.6.10` (`aae9c157e`) becomes the trunk. It is not a memory of stability — PID 1227992 serves port 7770 from that exact tree right now, on the pre-migration sidecars, and no `storaged_process` or `daemon.process` runs anywhere on this host.

Everything after it is treated as untested and is **not salvaged**. Features are *rebuilt* on the trunk behind a test that was written first; the 509 post-`v0.6.10` commits are **reference material only**, never a source of cherry-picks. The rearchitecture is a destination reached one measured boundary at a time, not a starting move.

`origin/main` was hard-reset to `v0.6.10` and the `v0.6.11` tag deleted on 2026-07-31, so the published history contains none of it. Everything removed is preserved in a verified bundle at `/home/keivenc/dev/yolomux-backup-20260731-2044`.

Full evidence and phases: `MASTERPLAN.md`.

## Where work happens

| File | Role | Status |
|---|---|---|
| **`MASTERPLAN.md`** | The master plan. Trunk decision, six phases, evidence. | **Read first** |
| **`DOIT.regression-gate.md`** | **Phase 1, and it blocks everything else.** Every failure since `v0.6.10` as a gating test at the HTTP/DOM boundary. | Active |
| `DOIT.optimistic-ui-acknowledgement.md` | 53 of 55 mutations never say "I heard you". Feeds gate section **K**. | Active |
| `DOIT.kill-session-guard.md` | tmux kill authority and fail-closed opt-in. Feeds gate box **D6**. | Active |
| `DOIT.release-evidence.md` | Phase 6. Both boxes name the user as approver. | Blocked on user |
| `DOIT.reported-bugs.md` | **Evidence archive — do not work from it.** 1,145 lines of measurements, ruled-out hypotheses and corrections for Bugs 1–17. Read it for *why*. | Archive |
| `DOIT.parked-machinery.md` | What the trunk change made moot, kept for the record. | Do not work |
| `DOIT.md` | This index. | — |

Moved to `docs/archive/doit/` in the reorganisation — they were untracked, so they were archived rather than deleted and nothing is recoverable from git: `DOIT.differ-errors-and-product-recovery.md` and `DOIT.metrics-rollback-runtimeerror.md` (fully drained), `DOIT.shared-data-manager-index.md` (stale index; its rules are preserved below), and `DOIT.scheduler-payload-ttl.md`, `DOIT.daemon-pause.md`, `DOIT.storaged-stats-startup-budget.md`, `DOIT.land-and-close.md` (absorbed into `DOIT.parked-machinery.md`).

## Where this file's old items went

The four open boxes formerly tracked here were all acceptance criteria for defects, so they became gate boxes rather than free-floating work: Cost summary rendering non-zero → **G2**; Cost Range/Resolution caching, `60→300→60→300→60` counting exactly two fetches → **G4**; chart-summary wrap regression → **H4**; dependent-capability reconciliation → **H8**.

## Still open here — not a defect, not parked

- [ ] Something rewrites `static/brand.css` during container boot / E1, adding a trailing space and removing the final newline, which fails the `git diff --check` lane. Find the writer. Generated assets are not source: edit `static_src/`, then run `python3 tools/static_build.py`.

## KNOWN-INHERITED gate failures — do not edit around them

These predate the current work. Do not "fix" them to make a branch gate green.

- `tests/test_browser_layout.py::test_ellipsis_and_disabled_control_families_share_computed_state` — inherited from `main`; the 18:04 fast-forward landed `dd7d7d18` without its paired `ebb215c5`. Fails on `main` in isolation in 1.78 s.
- `tests/test_browser_recovery.py::test_tmux_server_loss_marks_browser_shell_red_then_recovers` — genuinely load-flaky under gate concurrency. Do not reclassify as a branch regression without an isolated reproduction.
- `tests/test_browser_share.py::test_generated_share_link_mirrors_interactive_ui_surface_matrix[resilience]` — same.

## Rules that cost something to learn

- **Audit every recorded claim against source before working an item.** Three consecutive items once carried numbers wrong by large margins — a "110 sites" claim where source had 49 with zero violations, a "single literal owner" claim where source had 30, and a whole track whose premise measurement disproved. Trust source over any queue, and correct the item in place.
- **Co-tenancy, not isolation, is the discriminator for a runtime failure.** Three consecutive isolated passes prove nothing: the recovery family passed alone at `-n 4` and still failed under the full gate. Reproduce with a co-tenant suite before calling anything a flake.
- **Keep every open item under about 5,000 characters**, carrying only its current requirement, current blocker and acceptance gate. Superseded and historical findings belong in `docs/DONE.md`.
- **Never repair a test collision** with `--serial`, retries, sleeps, or `@pytest.mark.contention_prone`. Name the conflicting resource and its owning process, then fix ownership.
- **Ports 7770–7773 are off-limits to automated tests.** 7770 is live production on `v0.6.10` and must not be restarted by an agent.

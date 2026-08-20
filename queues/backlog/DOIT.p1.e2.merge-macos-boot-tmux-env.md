# DOIT.p1.e2.merge-macos-boot-tmux-env.md - Land origin/fix/macos-boot-tmux-env

Queued 2026-08-18 for the v0.7.9 goal (item 8). A land, not a build.

## What it fixes

A long-lived tmux server retains environment across launches, so a new server could inherit a **stale `YOLOMUX_ROW_PLAN_FILE`** and therefore a stale root or a stale plan-file path. The branch's own comment states the fix:

> "The plan JSON is a positional argument, not inherited state: a long-lived tmux server cannot substitute a stale root or stale plan-file path."

`tools/startup_common.sh` stops passing `--plan-file "$YOLOMUX_ROW_PLAN_FILE"` and passes `--plan-json "$plan_json"` as a positional argument instead.

This is the same family as the product-root leak already fixed in 0.7.8, where a run derived roots from ambient state and wrote `~/state`, `~/runtime`, `~/config`, `~/cache` and `~/codex` into `$HOME`. Environment inheritance across a persistent tmux server is exactly how that happens.

## Second win: it removes a divergent copy

The old launcher carried **two** launch paths behind one `if`:

- DIRECT (boot.sh, no `YOLOMUX_ROW_PLAN_FILE`) — export the primary port and exec the server
- EXEC PLAN (the supported multi-row launcher) — apply the captured RowPlan, then exec

The branch collapses both onto one exec path. Two divergent launch paths that must never drift is the defect shape this project keeps paying for, so this is worth landing on its own merits.

## State

- LANDED 2026-08-18. `6bf61d5e6` merged as `1691743b9`; both are ancestors of HEAD `7cb75e3a5`. `git log HEAD..origin/fix/macos-boot-tmux-env` is empty.
- 6 files, +272/-35: `tools/startup_common.sh`, `tools/instance_isolation.py`, plus `tests/test_dev_restart_script.py` (+131), `tests/test_instance_isolation.py` (+62), `tests/test_launcher_row_wiring.py` (+13), and the architecture-budgets ratchet.
- Focused suites pass: `tests/test_instance_isolation.py`, `tests/test_launcher_row_wiring.py`, `tests/test_dev_restart_script.py`, `tests/test_architecture_budgets.py` at 68 passed / 3 skipped in-container, and 5/5 on the host where the real launcher exists.
- REMAINING SCOPE IS ACCEPTANCE ONLY: real-macOS verification and a clean-tree exact-SHA certification. Implementation and landing are complete; do not re-open the merge.

## Plan

- [x] Re-run `merge-tree` against the real HEAD immediately before merging and record the result; the clean result above is a snapshot. DONE: `git merge-tree --write-tree a94311a1e origin/fix/macos-boot-tmux-env` exited 0 with tree `7c88d4760` and no conflict output, re-run against the real HEAD immediately before the merge.
- [x] Merge, then regenerate `tests/fixtures/architecture_budgets/v1.json` from the merged tree rather than taking either side. DONE: merged as `1691743b9`; ratchet regenerated from the merged tree with `python3 tools/architecture_budgets.py --write-current`, then re-verified exit 0. The six growing entries were attributable to `c59a8dd64`, not to this merge; `test_browser_dockview.py 9144 -> 9147` needed a second regeneration after a later comment edit.
- [ ] Prove the defect is actually fixed: launch from inside a tmux session that has a stale `YOLOMUX_ROW_PLAN_FILE` exported and confirm the new server does not inherit it. A passing test suite is not the same as proving a persistent tmux server can no longer poison a launch. PARTIAL, BLOCKED ON macOS: the mechanism was driven and the fix confirmed at the shell-logic level. A long-lived tmux server born with `YOLOMUX_ROW_PLAN_FILE` set was proven to leak it into a session created from a clean shell. With that poisoned server, `startup_common.sh` from `a94311a1e` produced `YOLOMUX_ROOT=/tmp/POISON-STALE` while the merged version produced the caller's `/tmp/GOOD-CALLER`, and with a real `plan-direct` plan the child saw the caller's root with `YOLOMUX_ROW_PLAN_FILE` unset. This is NOT production evidence: `yolomux_macos_server_launcher` and `yolomux_submit_macos_server` are reached only via `[[ "$(uname -s)" == "Darwin" ]]` at `boot.sh:472` and `boot.sh:487`, so on this Linux host they are dead code that was invoked by hand.
- [x] Confirm both launch paths still work after the collapse — `boot.sh` direct start and the supported multi-row launcher — since the branch merges two behaviours into one. DONE: `boot.sh` direct start verified live on Linux by five successful restarts (7770 twice, 7771, 7772, plus a later 7770), each reaching `port ready: /healthz -> 200`. The multi-row launcher path was driven with a caller-exported plan file and produced `YOLOMUX_ROOT=/tmp/ROWPLAN-CALLER` with primary port 7779 passed through. `tests/test_launcher_row_wiring.py` passes 5/5 on the host against the real launcher (in-container it skips 3 for a missing `~/dev/ai-config`).
- [ ] Verify on real macOS hardware, since that is the platform named. If no host is available, say so and mark that item blocked rather than passing it on Linux evidence. BLOCKED: no Darwin host. This box is Linux/x86_64 and `~/dev/ai-config/claude/private/hosts.env` declares no macOS host. The changed launcher is Darwin-gated, so its production path has never executed. Explicitly not passed on Linux evidence.
- [x] Check no product root is written under `$HOME` after the change, using the same check as the 0.7.8 root-leak work. DONE: `~/state`, `~/runtime`, and `~/.yolomux` are absent. `~/config`, `~/cache`, and `~/codex` exist but carry mtime 2026-08-04 21:10, pre-dating this work by 14 days; they are residue of the original 0.7.8 leak and nothing was written under `$HOME` by this change. They were left in place, not deleted.

## Done Criteria

- [x] `git log HEAD..origin/fix/macos-boot-tmux-env` is empty. DONE: verified empty after merge `1691743b9`; both `6bf61d5e6` and the merge commit are ancestors of HEAD `7cb75e3a5`.
- [ ] A launch from a tmux session carrying a stale plan-file variable does not inherit it, proven by driving it, not by test alone. PARTIAL: driven and confirmed at shell-logic level (old takes `/tmp/POISON-STALE`, merged takes the caller's plan; with a real plan the stale variable is unset in the child). Not closable here because the driven functions are Darwin-gated and never execute on this host. Separately, the Linux-live half IS proven: `yolomux_validate_instance_isolation` runs on every platform via `boot.sh:461`, and a stale/missing `YOLOMUX_ROW_PLAN_FILE` was silently ACCEPTED at `a94311a1e` (rc=0) but is REFUSED at the merged HEAD (rc=2, typed `invalid row plan` error).
- [x] Both direct and multi-row launch paths verified working post-collapse. DONE: see the plan item above; direct start proven by live restarts reaching `/healthz -> 200`, multi-row proven by driving the caller-supplied plan through the collapsed exec path.
- [x] Architecture-budgets ratchet regenerated post-merge. DONE: regenerated from the merged tree; `python3 tools/architecture_budgets.py` exits 0 and the `static source checks` lane passed in the final gate run.
- [x] macOS acceptance, or explicitly recorded as blocked with the reason. DONE by explicit block: recorded as BLOCKED with the reason above — no Darwin host available, and the changed launcher is Darwin-gated so it is dead code on this Linux host. Not passed on Linux evidence.
- [ ] Canonical gate green. PARTIAL: all nine functional lanes pass at `7cb75e3a5` (`git diff --check`, node syntax, py_compile, node layout, static source checks, pytest e2e, pytest non-browser, pytest browser, pytest timing-sensitive serial). Exact-SHA certification returned RC=4 `exact_sha_certification_rejected` for `dirty_start_checkout` caused by a concurrent `STATUS-REPORT.md` rewrite, not by any test failure. A clean functional gate is not exact-SHA certification, so this stays open pending a clean-tree certification run.

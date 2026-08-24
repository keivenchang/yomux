# Land origin/fix/macos-boot-tmux-env

Queued 2026-08-18 for the v0.7.9 goal (item 8). Landed and accepted on real macOS hardware by 2026-08-20.

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
- Real-macOS verification closed 2026-08-20 (see Plan/Done Criteria below). Implementation, landing, and macOS acceptance are complete; do not re-open the merge.

## Plan

- [x] Re-run `merge-tree` against the real HEAD immediately before merging and record the result; the clean result above is a snapshot. DONE: `git merge-tree --write-tree a94311a1e origin/fix/macos-boot-tmux-env` exited 0 with tree `7c88d4760` and no conflict output, re-run against the real HEAD immediately before the merge.
- [x] Merge, then regenerate `tests/fixtures/architecture_budgets/v1.json` from the merged tree rather than taking either side. DONE: merged as `1691743b9`; ratchet regenerated from the merged tree with `python3 tools/architecture_budgets.py --write-current`, then re-verified exit 0. The six growing entries were attributable to `c59a8dd64`, not to this merge; `test_browser_dockview.py 9144 -> 9147` needed a second regeneration after a later comment edit.
- [x] Prove the defect is actually fixed: launch from inside a tmux session that has a stale `YOLOMUX_ROW_PLAN_FILE` exported and confirm the new server does not inherit it. A passing test suite is not the same as proving a persistent tmux server can no longer poison a launch. DONE, ON REAL macOS (2026-08-20 22:51 PT, worktree `yolomux.dev8882`, Darwin/arm64): started a real tmux server on an isolated test socket (`yolomux-services-planproof`) whose CLIENT process exported `YOLOMUX_ROW_PLAN_FILE=/tmp/macos-planproof/POISON-STALE.json`; confirmed the poison genuinely propagates to the server's base environment (a fresh window on that same server showed `YOLOMUX_ROW_PLAN_FILE=/tmp/macos-planproof/POISON-STALE.json`). From inside that SAME poisoned session's shell (which itself echoed `CALLER_SHELL_SEES_ROW_PLAN_FILE=/tmp/macos-planproof/POISON-STALE.json`, proving the poisoning was real, not simulated), ran the actual unmodified `yolomux_macos_server_launcher`/tmux-submission shell logic from `tools/startup_common.sh` to submit a new session on the same server. The exec'd child's real, measured environment (dumped by the launched process itself, not inferred) was `{"YOLOMUX_ROW_PLAN_FILE": null, "YOLOMUX_ROOT": "/private/tmp/y502/p8998"}` — the poison was stripped and the correct port-derived root was used. Separately drove the full, completely unmodified `yolomux_submit_macos_server` function (not a diagnostic stand-in) to launch a real `yolomux.py` on scratch port 8996 through the same poisoned tmux server; it reached `/healthz -> 200` with `YOLOMUX_ROOT=/private/tmp/y502/p8996` in its own log, no poison leak. Test tmux server and scratch state cleaned up afterward (`tmux -L yolomux-services-planproof kill-server`; `/private/tmp/y502/p8996`, `p8997`, `p8998`, and `/tmp/macos-planproof` removed).
- [x] Confirm both launch paths still work after the collapse — `boot.sh` direct start and the supported multi-row launcher — since the branch merges two behaviours into one. DONE: `boot.sh` direct start verified live on Linux by five successful restarts (7770 twice, 7771, 7772, plus a later 7770), each reaching `port ready: /healthz -> 200`. The multi-row launcher path was driven with a caller-exported plan file and produced `YOLOMUX_ROOT=/tmp/ROWPLAN-CALLER` with primary port 7779 passed through. `tests/test_launcher_row_wiring.py` passes 5/5 on the host against the real launcher (in-container it skips 3 for a missing `~/dev/ai-config`).
- [x] Verify on real macOS hardware, since that is the platform named. If no host is available, say so and mark that item blocked rather than passing it on Linux evidence. DONE ON REAL macOS (2026-08-20, Darwin/arm64, worktree `yolomux.dev8882`, port 8882's host): the Darwin-gated `yolomux_macos_server_launcher`/`yolomux_submit_macos_server` path in `tools/startup_common.sh` executed for real (not by hand-invoking dead code) for the first time — see the stale-plan-file proof above and the real `/healthz -> 200` launch through the unmodified `yolomux_submit_macos_server` function.
- [x] Check no product root is written under `$HOME` after the change, using the same check as the 0.7.8 root-leak work. DONE: `~/state`, `~/runtime`, and `~/.yolomux` are absent. `~/config`, `~/cache`, and `~/codex` exist but carry mtime 2026-08-04 21:10, pre-dating this work by 14 days; they are residue of the original 0.7.8 leak and nothing was written under `$HOME` by this change. They were left in place, not deleted.

## Done Criteria

- [x] `git log HEAD..origin/fix/macos-boot-tmux-env` is empty. DONE: verified empty after merge `1691743b9`; both `6bf61d5e6` and the merge commit are ancestors of HEAD `7cb75e3a5`.
- [x] A launch from a tmux session carrying a stale plan-file variable does not inherit it, proven by driving it, not by test alone. DONE: driven for real on Darwin (see the plan-item proof above) with a genuinely poisoned tmux server and a genuinely poisoned caller shell inside that same session, through the real unmodified launcher/submission code, not a synthetic simulation. The Linux-live half was already proven separately: `yolomux_validate_instance_isolation` runs on every platform via `boot.sh:461`, and a stale/missing `YOLOMUX_ROW_PLAN_FILE` was silently ACCEPTED at `a94311a1e` (rc=0) but is REFUSED at the merged HEAD (rc=2, typed `invalid row plan` error).
- [x] Both direct and multi-row launch paths verified working post-collapse. DONE: see the plan item above; direct start proven by live restarts reaching `/healthz -> 200`, multi-row proven by driving the caller-supplied plan through the collapsed exec path.
- [x] Architecture-budgets ratchet regenerated post-merge. DONE: regenerated from the merged tree; `python3 tools/architecture_budgets.py` exits 0 and the `static source checks` lane passed in the final gate run.
- [x] macOS acceptance, or explicitly recorded as blocked with the reason. DONE, no longer blocked: a real Darwin host (`yolomux.dev8882`, this worktree) became available and the Darwin-gated launcher was driven end-to-end for real — see the stale-plan-file proof above.
- [x] Canonical gate green. DONE: all nine functional lanes passed for product candidate `59f80020b`. After the docs-only blocker audit was committed, one clean-tree `python3 tools/check.py --certification-only` attempt at exact SHA `aaf4c23e5` passed all 7/7 units in 95.28 seconds; preflight and postflight both qualified, start/end checkout state was clean, and the certified generated bundle hash `448a37cedc6c1e11754db9cb482b8a4577904cb96348a215e1884cac7618eb68` matches the source and live 7771 bundle.

# Phase 2 gate census

## Current measured state — 2026-08-01

**Commit measured:** `a94b63804` (the rebased `phase2-infrastructure` pre-launch sweep). **Selection:** the sweep's non-browser collection and its separately selected browser collection; the latter is the full browser-marked gate collection, not the default boot-smoke browser lane. `python3 tools/static_build.py --check` passed before the sweep. This selection and commit are part of every verdict below: a count without them is not a dispatchable baseline.

| Selection | Verdict at the sweep | Current interpretation |
| --- | --- | --- |
| Non-browser collection | 74 passed, 3 failed, 1 xfailed | The three failures were the merge regressions subsequently fixed; their three focused groups are now 25/25 green. |
| Browser collection | 60 passed, 19 failed, 9 xfailed; 275.03s pytest / 305.56s wall | No setup blocks. The 19 failures are active work or an explicit pending decision, listed below. |
| Tests absent from `tools/check.py` lane lists | 221 tests absent; direct run: 133 passed, 28 strict-xfailed, 3 failed | The manual catalog was retired. The catalog now derives from `PYTEST_PHASE_FILES`, so this selection cannot silently drift again. |

## Browser failures and owners

| Failure group | Count | Owner / state |
| --- | ---: | --- |
| `test_gate_contract.py` K0 ×4 and `test_gate_interaction.py` K1–K7 | 12 | yo7775 |
| `test_gate_agent_state.py` F3, F5, F6 | 3 | yo7771 |
| `test_gate_budgets.py` L3, `test_gate_launch.py` R1, `test_gate_panels.py` H6 | 3 | yo7774 |

## Corrections to the superseded census

- **P10 is green, 6/6.** It is not a deterministic failure. The earlier P10 state was falsified at `79b2bb107^`, where both P10 boxes were genuinely red before the previous fix; that establishes that the new green result is a repaired behavior, not a changed selection.
- **D6 is a missing contract, not a regression.** `test_gate_tmux.py` expects `tmux_utils.TmuxSocketTargetError`, but no such symbol exists anywhere in `yolomux_lib/`. Mark D6 as a strict xfail until that policy and its typed error are implemented, so it does not read as a broken pre-existing behavior.
- **The three non-browser merge regressions are closed.** The targeted orphans, host-diagnostics, and stats-preflight groups are 25/25 green. The stats-preflight issue was a stale `stats-v6.sqlite3` test literal; production already used `DATABASE_FILENAME`, so the schema-v6-to-v7 change exposed test hardcoding rather than a product defect.

## Supersession note

The previous census was superseded on 2026-08-01. It reported five sequential default `tools/check.py` runs from an earlier worktree and treated P10 as a deterministic failure; later merges and focused falsification proved that claim stale. Phase 2 is changing quickly enough that a census must name its commit, date, and test selection, and must be refreshed after merge batches rather than treated as a durable failure inventory.

## Unknowns and limits

- The 19 browser failures above are the measured browser-selection result at `a94b63804`; do not infer a current all-green or all-red gate verdict from the ownership table while those fixes are in flight.
- The direct run of the 221 tests absent from `tools/check.py` recorded three failures, but this census does not assign them because the measured brief does not identify their nodeids. They require a fresh, scoped measurement before dispatch.
- This document does not replace a post-merge full gate. It records the pre-launch sweep that was actually run and the focused evidence available after it.

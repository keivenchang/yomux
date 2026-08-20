# v0.7.9 exact tmux session targeting

Completed 2026-08-19. The queue closed at 11/11.

## Root cause and fix

tmux treats a bare target such as `1:` as an exact name first, then a prefix, then a pattern. If session `1` was absent while session `12` existed, `kill-session -t "1:"` could select and destroy `12`. The shared target owner now emits `=<session>:` for every session-scoped command, including kill, rename, select-window, set-option, send-keys, attach, and list paths. A missing exact target is refused and a similarly named sibling survives.

## Evidence

- The pre-fix private-socket regression reproduced the prefix-selection defect; the exact `=1:` target refused while preserving session `12`.
- Focused tmux runtime and gate tests passed, and the default-socket session inventory remained byte-identical before and after the work.
- The final runtime candidate `71ef69fac` passed the unmodified canonical gate with 9/9 functional lanes and 7/7 certification units in 602.86 seconds, then fast-forwarded into clean local main.
- Fresh authenticated final-SHA acceptance started with private sessions `1`, `12`, and `soak`: after removing exact `=1:`, the stale target returned typed 404 and preserved `12`; renaming `12` to `renamed-12` returned 200; killing `renamed-12` returned 200; `soak` survived every operation.
- The same identity completed a 90.142-second authenticated settle and 603.504-second clean observation with 118 samples and no final integrity failures. Its controlled negative phase attributed one accepted and rendered browser Error as the sole cause, with zero unrelated failures and all five redaction channels clean.
- `781536ce` is the simplified browser proof fix: it awaits the existing Debug Logs activation/poll promise without adding a second poll owner, helper, timer, or state path.

## Closure reconciliation

The source queue still showed four unchecked criteria immediately before archival. They closed as follows: `rename-session` uses the same exact target owner and passed the final live rename; `tmux_exact_target()` now emits the exact form through `tmux_session_target()`; the four-commit runtime chain is integrated into local main with the final gate green; and the auto-approve compatibility producer `tmux_exact_target_from_sessions()` now delegates to `tmux_exact_target()` without consulting a divergent session list.

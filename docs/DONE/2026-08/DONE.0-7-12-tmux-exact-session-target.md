# v0.7.12 exact tmux target revalidation

Completed 2026-08-22. The stale active queue closed at 11/11; the original shipped behavior remains recorded in [`DONE.0-7-9-tmux-exact-session-target.md`](DONE.0-7-9-tmux-exact-session-target.md).

## Closure

The exact-target source is committed in current `origin/main` at `929085bd7`. `rename-session` and the destructive lifecycle verbs use the shared `tmux_session_target()` owner, `tmux_exact_target()` emits the exact `=<session>:` form, and the compatibility producer `tmux_exact_target_from_sessions()` delegates to that same owner.

A dedicated private-socket regression reproduced the failure boundary with session `12` present and session `1` absent: bare `1:` capture and send operations reached `12`, while the compatibility producer returned `=1:` and both operations failed closed without touching the sibling. The focused regression passed 1/1. The independent Codex audit found no actionable issue.

Canonical attempt 3 passed all 9 functional lanes, the final timing-sensitive serial lane, and 7/7 certification tests. The command exited 4 only because exact-SHA admission rejected the intentionally dirty integration candidate. The tmux regression moved into its own 80-line test owner, the completed diff-history tests were split back under their fixed owner cap, and the architecture verifier then exited 0.

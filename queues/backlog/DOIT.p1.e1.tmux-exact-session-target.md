# DOIT.p1.e1.tmux-exact-session-target.md - Enforce Exact tmux Session Targets

Split from `DOIT.p2.e5.tmux-session-destruction.md` on 2026-08-18. That queue's fixture-leak mechanism was disproven and its seven-day criterion is unbuildable, but this gap is real, independently deliverable, and turned out to be a live data-loss defect.

## Goal (narrowed 2026-08-18)

DESTRUCTIVE LIFECYCLE TARGETS ONLY. Every tmux verb that can destroy or rename a session must use an exact session target so a prefix or `fnmatch` pattern can never resolve onto a session the caller did not name.

The non-destructive auto-approve path is DELIBERATELY EXCLUDED from this goal and tracked as its own open item below, because it has a second producer with different consumers. This narrowing is explicit so the queue does not claim more completion than it delivers.

## The Defect, Reproduced Live Twice

tmux resolves a bare `name:` target by exact match, then prefix, then `fnmatch`. When the named session is gone or renamed, the target silently walks onto a different session. YOLOmux names sessions `1`, `2`, `12`, so this is reachable with ordinary names.

Independently reproduced by the main agent on a private socket, with session `12` present and session `1` absent:

- `tmux kill-session -t "1:"` returned rc=0 and DESTROYED session `12`.
- `tmux kill-session -t "=1:"` returned rc=1 `can't find session: 1`, and `12` survived.

The implementing agent reproduced the same class first: `kill-session -t yt-b0fb7fe3:` returned rc=0 and destroyed `yt-b0fb7fe3-sibling`.

A first attempt at reproduction that kept the exact session present did NOT reproduce it, because exact match wins when the name exists. The hazard requires the named session to be absent; any regression must encode that precondition or it cannot fail.

## Plan

- [x] Capture red first: a regression proving a prefix target resolves onto the wrong session and that an exact target refuses. DONE: 16 new assertions failed before the change and 0 after, including a live prefix-vs-exact kill on a private socket.
- [x] Change `tmux_session_target()` to the exact `=<session>:` form, routed through the existing shared owner with no second guard and no new route. DONE: `tmux_utils.py` `tmux_session_target()` returns `f"={session}:"`; every caller (kill, rename, select-window, set-option, send-keys, control-attach, list-*) inherits exactness, and each verb was probed against a live private socket including `=name:0` window targets to confirm the form is valid where used.
- [x] Migrate every assertion of the old prefix form repo-wide in the same change. DONE by repo-wide scan for non-`=` `-t` literals: `test_gate_tmux.py` (the known blocker, now at :138/:142, flipped to exact and extended with a prefix-refusal case), `test_tmux_utils.py`, `test_session_actions.py` (5), `test_app.py` (9), `test_tmux_theme.py` (5), `test_tmux_signals.py` (3), `test_server_query.py` (1).
- [x] Cover the matrix: no socket and no opt-in fails closed; default socket with explicit opt-in permits only an exact target; private socket works; `kill-server` refused in every mode; malformed and prefix targets refused. DONE: `tmux_guarded_verb` became `tmux_guarded_refusal(args, *, server_is_explicit) -> (verb, reason)` carrying two independent authorities - target precision on every server (exactly one `-t` in `=name:` form; `-a` refused outright because it kills every other session) and the pre-existing server-choice rules. `tmux_command` now always consults it, and `TmuxSocketTargetError` carries a `reason` so a refusal names the authority that denied it instead of misreporting the socket. Opt-in plus `yt-gate:` is still refused; `kill-server` refused across 6 opt-in values.

## Done Criteria

- [ ] `rename-session` carries the IDENTICAL prefix exposure and is still unfixed. `app.py:15049` targets a session for rename through the same bare form, so a rename aimed at an absent name can retarget a prefix sibling. It must be fixed in the same change as `kill-session`, not deferred.
- [ ] `tmux_exact_target` is MISNAMED and does not do what its name claims: it emits `name:`, not `=name`. Either make it emit the exact form or rename it so no future caller trusts it for precision.
- [x] A prefix target is refused with a typed error and an exact target succeeds, proven by a regression that fails before the change. DONE, see above.
- [x] `python3 -m pytest -q tests/test_tmux_runtime.py tests/test_gate_tmux.py` exits 0. DONE: 31 passed, 1 skipped, 5 xfailed. Also measured: `test_app.py` 552 passed; `test_tmux_utils`/`signals`/`theme`/`session_actions`/`server_query` 187 passed; `test_gate_tmux_identity`/`recovery`/`yoagent_actions`/`activity_summary`/`server_lease`/`server_logs` 54 passed 1 xfailed; `test_mock_agents.py` 165 passed; `test_statusd.py` 41 passed.
- [x] Default-socket session count is recorded before and after the work and is unchanged. DONE: before/after session lists are byte-identical by `diff`, 30 sessions both times, zero `yt-*`. Independently re-confirmed by the main agent after integration review: 30 sessions, zero `yt-*`. Every destructive probe ran on private sockets created and torn down by the agent.
- [ ] The change is integrated into the primary checkout and the canonical gate is green there. NOT DONE: the patch is uncommitted in an isolated worktree and integration is deferred pending commit authorization.
- [ ] The second producer is reconciled or explicitly split out. NOT DONE: `tmux_exact_target_from_sessions` (`tmux_utils.py:259`) still emits the bare `f"{target}:"` form on the auto-approve path feeding send-keys and capture-pane. It carries the same prefix hazard but is non-destructive - the wrong session receives a keystroke rather than a kill. It is a divergent second copy of one value and must be either routed through the shared owner or split into its own queue with a recorded reason. Its output doubles as a CLI-compatibility export and a prompt-detector cache key, so it needs its own live test.

## Deliberately Not Done - Recorded For A Follow-Up

`tmux_exact_target_from_sessions` (`tmux_utils.py:259`) is a SECOND producer of the bare `f"{target}:"` form, feeding send-keys and capture-pane on the auto-approve path. It carries the same prefix hazard but is non-destructive: the wrong session receives a keystroke rather than a kill. It was left alone because its output doubles as a CLI-compatibility export and a prompt-detector cache key, so changing it needs its own live test. This is a divergent second copy of one value and should be reconciled with the shared owner.

`app.py` `kill_session` is guarded by `@requires_known_session`, so the new precision check should never trip in production; if it did, `TmuxSocketTargetError` propagates to the HTTP layer exactly as the existing opt-in refusal does, which is fail-closed.

## Completion

Archive to `docs/DONE/` after integration and a green canonical gate in the primary checkout, then remove this queue.

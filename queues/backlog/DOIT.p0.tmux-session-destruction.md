# DOIT.p0.tmux-session-destruction.md - Stop Random tmux Session Destruction

Source provenance: `DOIT.unprioritized.md` U-B, the former `DOIT.fix-tmux-random-kill.md`, and the default-server `kill-session` guard queue formerly duplicated in sibling worktrees.

## Goal

No test or stale checkout can create `yt-*` sessions on the ambient tmux server or destroy a real session, the exact invoker is attributable, and legitimate per-session deletion works only through an explicit authority boundary.

## Evidence

- On 2026-08-04, ten real sessions vanished; session `2` died three times. Across 4,903 archived snapshots there were 73 one-session disappearance events while the default tmux server stayed alive.
- `yt-<pid>-<hex10>-1` is created by `tests/tmux_runtime.py`, and those names appeared on the default socket. Main already has private-socket and exact-target hardening, but stale checkouts can still run older helpers.
- The earlier production `POST /api/kill-session` 503 had the opposite boundary error: `kill-session` was grouped with `kill-server`. The intended policy is blast-radius based: `kill-server` is always refused on an ambient/default socket, while exact-target `kill-session -t =<session>` is allowed on the default server only when `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1` explicitly grants that deployment authority.
- A kill has not yet been caught in the act. Updating likely checkouts is mitigation; closure requires invoker attribution, a proven tripwire, a leak-free gate, and seven clean PT calendar days.

## Ownership And Parallel Lanes

- Lane A, immediate mitigation, owns only invoker/CWD discovery and safe checkout routing. It may inspect all worktrees in parallel but must not rebase or discard a dirty branch without its owner.
- Lane B, one code writer, owns `tmux_command`/`tmux_guarded_verb`, exact target construction, fixture-private sockets, the API control path, and the tripwire. No other lane adds a second destructive-verb guard.
- Lane C is observation only and starts after A and B pass: run the canonical gate, then collect seven clean days. The queue cannot close while this lane is pending.

## Plan

- [ ] Resolve the leaking invoker by executable, PID/start identity, parent, argv, exact CWD, start source, socket environment, and PT minute; immediately stop or redirect that invocation to a checkout containing the guarded helper without changing unrelated dirty worktrees.
- [ ] Inventory `~/dev/yolomux` and `~/dev/yolomux.dev7771` through `~/dev/yolomux.dev7776`; for each stale checkout, record pre-HEAD/status and either safely fast-forward/rebase after its owner clears it, retire its invocation path, or record a named blocker. A stale but unreachable checkout is not an active hazard.
- [ ] Keep one shared destructive-authority owner: refuse ambient/default-socket `kill-server` unconditionally; permit exact-target `kill-session -t =<session>` on a private socket, or on the default socket only with `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1`; no prefix target is ever accepted.
- [ ] Add failing-first unit and API regressions for no socket/no opt-in, default socket with opt-in, private socket, `kill-server` in every mode, malformed/prefix targets, and `POST /api/kill-session` removing exactly one fixture session.
- [ ] Extend the shared `tmux_state.sh` snapshot owner to emit one retained JSONL event for a vanished session that cannot join an allowlisted teardown by exact session/socket/PT minute; record prior/current snapshots and invoker correlation fields and never write the monitor to `/dev/null`.
- [ ] Run the focused tmux suites and an unmodified canonical gate while capturing default-socket session/history state, then begin the seven-day retained tripwire observation.

## Rejected Shortcuts

- Do not update all checkouts blindly, reset dirty work, infer closure from mitigation, use prefix targets, or allow `kill-server` because a deployment owns the default socket.
- Do not thread a different socket into the production per-session API if that changes which server the deployment deliberately owns.
- Do not re-investigate OOM or a tmux server crash unless new correlated evidence contradicts the retained finding that the server stayed alive.

## Done Criteria

- [ ] The DONE note identifies the leaking invoker with executable, PID/start identity, parent, argv, exact CWD, start source, resolved socket, and PT minute; that resolved code path contains the guarded owner and can no longer place a fixture session on the default socket.
- [ ] The seven permanent checkouts each have a recorded pre/post HEAD and status plus one explicit disposition: `updated`, `retired-from-invocation`, or `blocked-by-named-owner`; every checkout that remains invokable passes the destructive-guard negative search, and no unrelated dirty file changed.
- [ ] `python3 -m pytest -q tests/test_tmux_runtime.py tests/test_gate_tmux.py` exits 0 and proves: no opt-in fails closed; explicit default-server opt-in permits only exact-target `kill-session`; private sockets permit exact-target fixture cleanup; `kill-server` remains refused on ambient/default sockets in all cases; malformed and prefix targets receive zero tmux calls.
- [ ] The API fixture starts two sessions on one private socket, deletes one through `POST /api/kill-session`, receives 200, proves the exact named session is gone, and proves the sibling remains alive; the pre-fix guard classification fails this test for the intended 503 reason.
- [ ] A deliberate unexplained scratch-session disappearance creates exactly one retained tripwire row with session, socket, prior/current snapshot, PT minute, and `classification=unexplained`; an allowlisted teardown creates zero unexplained rows, and every event can join to a process/CWD or remains explicitly unattributed.
- [ ] An unmodified `python3 tools/check.py` exits 0 and produces zero `yt-*` sessions or history entries on the default socket before, during, and after the gate.
- [ ] Seven complete consecutive PT calendar days, each with at least one retained snapshot per minute except explicitly recorded monitor downtime, contain zero unexplained disappearances; every deliberate disappearance joins exactly one allowlisted teardown by exact session, socket, and PT minute.

## Completion

After the seven-day criterion passes, summarize the guard, invoker, gate, and inclusive observation range in `docs/DONE/`, remove this queue, and keep no sibling `DOIT.kill-session-guard.md` copy.

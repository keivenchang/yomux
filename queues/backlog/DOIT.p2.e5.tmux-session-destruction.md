# DOIT.p2.e5.tmux-session-destruction.md - Stop Random tmux Session Destruction

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

## Downgrade p0.e5 -> p2.e5 (2026-08-18) - Rationale, Retained Scope, And What Is NOT Claimed

Reprioritized, NOT closed. Every requirement below is retained; none were deleted.

WHY THE PRIORITY DROPS: the queue's stated mechanism — `yt-*` fixture sessions leaking onto the default socket — was contradicted by its own 2026-08-04 tripwire watch, which observed two real disappearances while the `yt-*` count was 0 and neither vanished name was a prefix of the other. The guard owner already exists (`tmux_utils.py:55-101`: ambient `kill-server` refused unconditionally, `kill-session` refused without an exact `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER == "1"` opt-in, `TmuxSocketTargetError` when no socket is declared), and fixtures have used private sockets since `bbc178590`.

WHAT IS EXPLICITLY NOT CLAIMED: no stability claim is made for the period after 2026-08-04. A 2026-08-18 read of the default socket shows 30 sessions with zero `yt-*`, and processes whose start times date to 2026-08-01 and 2026-08-07. That proves those particular sessions are alive now; it does NOT prove that no disappearance occurred in between, because no retained monitoring data exists after 2026-08-04. Any earlier phrasing suggesting "17 days of stability" is unsupported and withdrawn.

HARD BLOCKER, UNCHANGED: `tools/tmux_state.sh` does not exist anywhere in the repository. There is no snapshot archive, no JSONL writer, and no allowlisted-teardown join. Day 1 of the required seven cannot have started.

RETAINED SCOPE — all of the following remain in force and must be checked off with evidence, never deleted: leaking-invoker attribution and its stop-or-redirect action; the seven permanent-checkout dispositions; the full retained tripwire schema including the deliberate unexplained-disappearance test and the allowlisted-teardown case; the canonical-gate zero-`yt-*` default-socket proof before, during, and after; the live `POST /api/kill-session` proof; and the seven-day observation with explicit monitor-downtime accounting.

SPLIT OUT AS p1: exact-target enforcement is independently deliverable and does not depend on any monitoring. `tmux_session_target()` (`tmux_utils.py:124`) returns `f"{session}:"`, a prefix/fnmatch-resolvable target. CORRECTION: it does NOT have a single production caller. A repo-wide grep finds 20 call sites across `app.py`, `server.py`, `tmux/tmux_signals.py`, `tmux/tmux_theme.py`, and `yoagent/controller.py`, covering kill, rename, select-window, set-option, send-keys, control-attach, and list verbs. Any earlier statement naming `app.py:15081` as the sole caller is false and is withdrawn; the blast radius is every targeted tmux verb, not one route. It must become the exact `=<session>` form routed through the existing shared owner. Note `tests/test_gate_tmux.py:134` currently asserts the non-exact `yt-gate:` form and must flip in the same change, along with every other assertion of the old form found by a repo-wide grep. That work is tracked in `DOIT.p1.e1.tmux-exact-session-target.md`; the corresponding boxes below stay open here until it lands.

## 2026-08-18 - A REAL DESTRUCTION MECHANISM WAS FOUND (candidate cause for this queue)

The fixture-leak hypothesis stays disproven, but a genuine, reproducible session-destruction mechanism was found while doing the split-out exact-target work, and it is a live candidate cause for the unexplained disappearances this queue exists to explain.

tmux resolves a bare `name:` target by exact match, then prefix, then `fnmatch`. `tmux_session_target()` produced exactly that bare form, and `app.py:15081` `POST /api/kill-session` used it. When the named session is absent or renamed, the kill walks onto a different session. YOLOmux names sessions `1`, `2`, `12`, so this is reachable with ordinary names.

Reproduced independently, on private sockets, with session `12` present and `1` absent:

- `tmux kill-session -t "1:"` returned rc=0 and DESTROYED session `12`.
- `tmux kill-session -t "=1:"` returned rc=1 `can't find session: 1`; `12` survived.

The implementing agent reproduced the same class: `kill-session -t yt-b0fb7fe3:` destroyed `yt-b0fb7fe3-sibling`.

WHY THIS MATTERS HERE: it produces exactly the signature this queue recorded - a real session vanishing while the tmux server stays alive and no `yt-*` fixture session is present. It also explains why session `2` could appear to die repeatedly without any fixture involvement. This is a CANDIDATE cause, not a proven attribution: no retained snapshot exists for the 2026-08-04 window, so it cannot be matched to those specific events after the fact. Attribution still requires the retained tripwire.

The fix is tracked in `DOIT.p1.e1.tmux-exact-session-target.md` and is implemented but not yet integrated. If the retained tripwire is ever built, it should specifically test whether unexplained disappearances stop after that fix lands, which is a far cheaper discriminator than a blind seven-day soak.

## 2026-08-18 REFUTED AND RECOMMENDED FOR RETIREMENT - The Retained Archive Exists

The premise of this queue is FALSIFIED, and the prefix-kill mechanism recorded above as a "candidate cause" is REFUTED as the explanation. That candidate hypothesis is withdrawn.

THE HARD BLOCKER WAS FALSE. `tools/tmux_state.sh` is absent from this repo, but the snapshot owner it describes exists at `~/dev/ai-config/claude/skills/tmux-capture/scripts/tmux_state.sh` and has been writing per-minute snapshots to `~/.cache/tmux-state/history/` for 15 days. Measured coverage is 1440 rows/day across 2026-08-04 through 2026-08-18 - complete per-minute coverage of the incident window and the fortnight after it. Each row carries session, window, proc, pane, cwd, provider, transcript id, and argv, which is enough to join a session across a rename by identity. There was never a need to build a tripwire or run a seven-day soak.

TIME-CRITICAL: that archive is expiring. `HIST_DAYS=14` prunes on a rolling basis and the oldest retained snapshot advanced three times during this session alone. 2026-08-04 is pruned within roughly 24 hours. A preserved copy of 2026-08-03 through 2026-08-18 is at `/home/keivenc/.cache/tmux-state-incident-20260804/history-20260803-to-20260818.tgz`, 4,285,446 bytes, verified to sit outside the prune scope, which targets only `$HIST_DIR/snapshot-*.tsv`.

WHAT THE DATA SHOWS across 21,531 consecutive snapshot pairs, 156 vanished-name events:

- 34 were RENAMES, not deaths, verified by identical transcript UUID and cwd within the same minute. A name-keyed differ reads every ticket-prefix rename as a disappearance.
- 122 were true terminations: 56 bullpen scratch, 23 yolomux dev/agent scratch, 20 named work sessions, 12 `yt-*` fixtures, 7 numeric scratch, 4 gate scratch. 110 of 122 were single-pane.
- Session `2`, this queue's marquee "died three times" symptom, is a single-window single-pane session whose only process is `claude`, with a DIFFERENT transcript UUID each incarnation. The agent exited, the last pane exited, tmux ended the session. No kill was involved.

WHY THE PREFIX MECHANISM CANNOT BE THE CAUSE, structurally:
- A prefix kill can only destroy a session whose name strictly EXTENDS the target. `2` has no nonempty proper prefix, so it can never be a prefix victim. The headline symptom is mechanically impossible under this cause.
- A sweep of all 122 true terminations for the prefix signature found ZERO matches.
- tmux's prefix resolution returns NULL on ambiguity, and the real naming distribution makes short numeric targets ambiguous rather than lethal.
- `kill_session` was already `@requires_known_session(refresh=True)` in the code running that day (verified at `aae9c157e`), so a stale client receives 404, not a sibling kill.

AGAINST THAT CONCLUSION, recorded honestly: the mechanism WAS maximally exposed on 2026-08-04. `tmux_guarded_verb` did not land until `d4caf7fe1` on 2026-08-08, and production explicitly opts in via `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1` (`tools/startup_common.sh:7-9`). The fix remains justified on its own merits; it is simply not this queue's cause.

BOX 1 IS SATISFIED BY THE ARCHIVE. The `yt-*` leak was real on 2026-08-04: 9 distinct `yt-<pid>-<hex10>-1` sessions on the default socket between 11:11 and 11:46 PT, every one with `cwd=/home/keivenc/dev/yolomux.dev7774`. That checkout no longer exists. `yt-*` count is 0 on every other day.

WHAT CANNOT BE DETERMINED: snapshot granularity is one minute, so a kill-and-recreate inside a single minute is invisible, and the archive records no invoker PID or argv for the killer - only for the victim's panes. It is therefore possible but unevidenced that one of the 20 named-session terminations was an unwanted kill; none carries the prefix signature and all are consistent with `dyn-cleanup`, `tmux-remove`, or agent-exit shapes.

RECOMMENDED DISPOSITION - RETIRE this queue. Box 1 satisfied (invoker identified, checkout deleted). Box 2 satisfied (only 4 `~/dev/yolomux*` checkouts remain, zero fixture-shaped `yt-*` after 08-04). Boxes 3 and 4 MOVE to `DOIT.p1.e1.tmux-exact-session-target.md` rather than duplicating there. Box 5 retire, the tripwire exists and rebuilding it in-repo would duplicate a working shared parent. Box 6 retire, 14 days of retained per-minute data already answer what a forward soak would ask.

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

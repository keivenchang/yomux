# DOIT: `Kill tmux session` always 503s on the default-server deployment

Goal: make the menu action work again on a deployment that legitimately owns the shared default tmux server, without weakening the guard that was added after the 2026-07-26 `kill-server` incident.

Separate from `DOIT.scheduler-payload-ttl.md` and `DOIT.daemon-pause.md`. Do not interleave them.

## Reproduced on live 7770, 2026-07-29

The user clicked `Kill tmux session 'deleteme'` twice. Both requests failed:

```
10.2.55.226 - - [29/Jul/2026 18:03:03] "POST /api/kill-session?session=deleteme&socket_id=socket-01f99858fe9d9b41b780810b HTTP/1.1" 503 -
10.2.55.226 - - [29/Jul/2026 18:03:13] "POST /api/kill-session?session=deleteme&socket_id=socket-01f99858fe9d9b41b780810b HTTP/1.1" 503 -
```

The session was alive the whole time (`tmux has-session -t deleteme` -> exists), and the neighbouring daemon-backed endpoint was healthy in the same window (`GET /api/tmux-session-exists?session=deleteme` -> `200 {"session":"deleteme","exists":true,"ok":true}`). So this is not daemon unavailability, and not the frontend.

Direct reproduction against the production checkout, with the daemon's own environment (no `YOLOMUX_TMUX_SOCKET` set):

```
configured socket:                    ''
tmux_command(['list-sessions'])    -> ['tmux', 'list-sessions']            OK
tmux_command(['kill-session','-t','deleteme:'])
    -> TmuxSocketTargetError: refusing tmux kill-session: no tmux server was
       explicitly chosen, so it would reach whatever $TMUX or the default
       socket points at.
```

## Mechanism

1. `yolomux_lib/daemon/tmux/runtime.py:319` -- the daemon's `kill_session` action calls `tmux(["kill-session", "-t", tmux_session_target(session)], timeout=3.0)`.
2. `yolomux_lib/tmux/tmux_utils.py:146 tmux_command()` refuses any verb in `TMUX_DESTRUCTIVE_VERBS` when `configured_tmux_socket_path()` is empty and no explicit `-S`/`-L` is present, raising `TmuxSocketTargetError`.
3. `TMUX_DESTRUCTIVE_VERBS = frozenset({"kill-server", "kill-session"})` (`tmux_utils.py:54`).
4. The raise propagates out of the control action; `app.py:15811 daemon_tmux_control` catches it at the boundary and returns `http_status: 503`, which is the 503 above.

Production runs the shared default server on purpose: `YOLOMUX_TMUX_SOCKET` is unset and the daemon environment carries only `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1`. So on this deployment the action can *never* succeed. It is deterministic, not intermittent, and unrelated to the 2026-07-29 restarts or the scheduler leak.

## Why it is a misclassification, not a missing feature

The guard is correct and should stay -- its comment records that on 2026-07-26 a probe that inherited `$TMUX` ran `tmux kill-server` and destroyed every live session at once.

The same comment states the intended dividing line: *"Per-target reads and writes keep the historical fallback because the host deployment legitimately owns the shared default server and refusing them would break it."* The tier is supposed to be chosen by **blast radius**.

`kill-session -t <name>` is per-target. Its blast radius is one named session -- the same as `rename-session`, which is allowed on the fallback path today. It appears to have been grouped with `kill-server` by name similarity rather than by blast radius, and that is what broke the button.

## The change

- [ ] Move `kill-session` out of the absolute-refusal tier and into the **opt-in** tier already gated by `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1`, which is the same tier control-mode attach uses. `kill-server` stays absolutely refused -- do not touch it.
- [ ] A harness that never declares the opt-in must still fail closed. That is the exact case the guard was written for, and it is the case the regression test must pin.
- [ ] Do not "fix" this by threading an explicit socket into the daemon's kill path if that changes which server production talks to. Production owning the default server is the intended deployment, not a bug.

## Tests

- [ ] With no socket configured and no opt-in: `kill-session` still raises `TmuxSocketTargetError`. (fails closed -- the incident case)
- [ ] With no socket configured and `YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1`: `kill-session` builds argv and is permitted.
- [ ] `kill-server` raises in BOTH of the above. It must not ride along with the reclassification.
- [ ] End to end: `POST /api/kill-session` returns 200 and the session is actually gone, on a scoped test socket.

Each of these must fail against current `main` before the change.

## Conflict warning

`yolomux_lib/tmux/` is being touched by other worktrees right now -- yo7771 has lock/isolation work on `keivenchang/isolate-per-run-config-state`, and yo7774 has a DOIT covering `tools/startup_common.sh` and daemon/storaged ownership. Check those diffs before editing so this does not become a divergent second copy of a shared fix.

## Ground rules

`python3 -m pytest`, never bare `pytest`. Gate is `python3 tools/check.py` in the foreground with a tee, and it takes a shared flock at `~/.cache/yolomux/expensive-tools.lock` -- wait rather than bypass. Run `--lane pytest-unit` before committing. Commit locally with explicit paths, `--signoff`; do not push, do not merge, do not run cps. Do not restart 7770 -- it is the user's live deployment, currently up on main `9cc9e871` / `0.6.12`.

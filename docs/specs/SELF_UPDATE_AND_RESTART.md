# Self-update, restart, and the worktree deployment model

How YOLOmux updates its own running code, when it auto-restarts vs asks for a manual restart, and the multi-worktree checkout model those rules depend on. Written after a session that landed a batch of lifecycle fixes and hit every one of these conditions.

## The worktree model (what "this checkout" means)

YOLOmux is developed across several git **worktrees** that share one repository and one `origin`:

- The production checkout holds the canonical `main` branch — the local integration and production checkout. The production server runs from here. Code is never edited here; `main` advances only through a fast-forward merge or pull.
- Development worktrees hold their own branch handles and run on independently configured ports. All edits happen in a development worktree.

Because `main` is checked out in the production checkout, it cannot also be checked out in a dev worktree. Two consequences that are easy to get wrong:

- **Landing work on main** means: commit on the dev branch, `git rebase main` (or `origin/main`), then fast-forward `main` from the production checkout. You do not commit to `main` directly from a dev worktree.
- **Advancing `main` does not move the other worktrees' branch handles.** After main moves, a dev worktree can simply be *behind* main (a strict ancestor), not diverged. Realign it from that worktree with `git merge --ff-only main`. This is why two worktrees can legitimately show different HEADs right after a landing — it is staleness, not divergence.

`common.PROJECT_ROOT` is the directory the running server was launched from, i.e. which worktree this process is. Several behaviors below branch on whether `PROJECT_ROOT` equals the configured production root.

## Self-update: when the toast fires and what each message means

`TmuxWebtermApp.perform_self_update` (`app.py:2041`) runs the plan `git pull --ff-only origin main` -> `python3 tools/static_build.py` -> restart. The conditions:

- **Update available / notification.** `update_check_loop` (`app.py:2092`) polls on an interval (default 60 min, re-read live from settings each tick) and publishes `update_available` once per new target version when the configured `notify_level` allows it. A notify level of `none` idles the loop.
- **Pull must be a clean fast-forward.** If `git pull --ff-only` fails (the checkout is dirty or diverged — a "read-only" checkout), the update is **blocked**, nothing is pulled, and the message is `update blocked: checkout is not a clean fast-forward; sync it manually`. YOLOmux never force-updates; a dirty/diverged worktree is left untouched on purpose.
- **After a successful pull**, `static_build.py` regenerates `static/yolomux.js` from `static_src/`, then `_spawn_self_restart()` decides whether to bounce the process.

The toast text reports the restart outcome:

- `updated; restarting now` — the auto-restart helper was spawned for the checkout that handled the update request.
- `updated; restart spawn failed; restart the server manually` — the pull+build happened but YOLOmux could not spawn the detached restart helper, so the running process is now serving stale code until you restart it yourself.

## Auto-restart condition: running checkout only

`_spawn_self_restart` (`app.py:2063`) auto-restarts the checkout that is running the current process. If the server was started from `<dev-worktree>/yolomux.py`, update pulls and builds in that worktree, then the helper restarts that same checkout. Dev worktrees must never restart prod; the safety rule is that the helper only kills its own PID and relaunches the resolved current argv from the same `PROJECT_ROOT`.

The mechanism, when it does fire, is intentionally portable — no systemd, no broad `pkill`:

- It records a restart context: resolved checkout root, effective Python argv, current PID, and the env values needed for agent discovery and stripped launchers (`PATH`, `TERM`, `PYTHONUNBUFFERED`, `MALLOC_ARENA_MAX`, YOLOmux config/state overrides, test-auth bypass, and virtualenv when present).
- It resolves `python3 yolomux.py ...`, `/abs/path/yolomux.py ...`, and `python3 -m yolomux ...` into a launcher that can be replayed after the request process exits. Script launchers use the resolved script path inside the checkout; module launchers stay `python -m yolomux` after the helper `cd`s into `PROJECT_ROOT`.
- It runs a detached `nohup bash -lc` helper with stdio redirected to `/dev/null`, `start_new_session=True`, and output appended to `/tmp/yolomux-self-update-restart.log`.
- The helper kills only the current server PID, waits, force-kills only that same PID if needed, then relaunches the preserved argv under `nohup env ... < /dev/null &`.
- It only ever kills its **own** PID and relaunches from its **own** `PROJECT_ROOT`. That makes auto-restart safe for prod and dev worktrees because the update, build, kill, and relaunch all target the checkout that served the request.

## Browser reload behavior after self-update

The browser owns a separate self-update reload flow instead of relying on the generic server-version banner:

- Clicking `Update Now` dismisses the yellow update-available toast immediately after confirmation and hides the topbar update badge for that in-flight update.
- When `/api/self-update` reports success with `restarting: true`, the client marks the update target as handled, polls `/api/ping` until the restarted server answers, then reloads automatically.
- The generic `New YOLOmux version available` / `Reload` banner is suppressed for the self-update target owned by that client. A later unrelated server version can still use the normal banner path.
- Auto-reload uses the same safety gate as the generic reload path. Dirty editor buffers or active typing defer the reload and show a self-update-specific `Software Update` notification; YOLOmux keeps polling and reloads once it is safe.

Manual restart, when the toast asks for it (or any time you change source under a running server): use **kill-by-PID + nohup**, never `systemd-run --user` (denied by the harness D-Bus in this environment). Build the kill pattern around the explicit `$port` so you never match the relaunch command itself, exclude `$$`, and keep the kill and the relaunch as separate commands. A running server does NOT pick up edited `.py`/bundle files until restarted.

## Contributor requirements learned (mistakes to not repeat)

These are hard requirements when landing changes, learned by hitting them:

- **Rebuild the bundle after any `static_src/` edit.** `static/yolomux.js` is generated by `python3 tools/static_build.py`; `tools/check.py`'s `static_build --check` lane fails if it is stale. Never hand-edit `static/yolomux.js`, including when resolving a merge conflict in it — resolve the conflict in `static_src/` and regenerate. A frontend fix is not done from source inspection alone.
- **`main` and dev worktrees refactor the same hot paths in parallel, so rebases conflict — resolve as the UNION of both sides.** When the resize-authority work on `main` and the lifecycle fixes on a dev branch both touched `bridge_tmux`/`start_locked`/`sendRemoteResize`, the correct resolution kept BOTH features (e.g. the fd-leak `try/except` *around* the `tmux_attach_command` helper, `claim_resize_authority` folded into the new try block; `sendRemoteResize` keeping main's `activate`/`shareClientId` message fields AND the new boolean return). Taking either side alone drops a real feature.
- **Always rerun `python3 tools/check.py` after resolving a rebase/merge, and let it arbitrate.** A green full suite is the proof the union resolution is correct.
- **A test that pins to a moving file will rot.** `test_diff_overview_matches_actual_todo_codemirror_rows` read the live `docs/TODO.md` and asserted a single diff chunk; once the doc was rewritten the diff grew to many chunks and the test failed for a data reason, not a code bug. Fixtures that need a realistic large diff must freeze BOTH sides (see EDITOR-CODEMIRROR.md) instead of reading a file that changes.
- **`tools/check.py` runs py_compile, `static_build --check`, node syntax, the node layout suite (`tests/layout_url.test.js`), pytest, and a whitespace check.** Run it parallel (`-n auto` style) — it is the single gate before landing.
- **No `clean-commit.sh` here.** That is a dynamo-only tool; yolomux commits do not run it.

The commit/land workflow itself (LOCAL vs ORIGIN cps) is an operator workflow kept outside the repo. In short: LOCAL `cps` = rebase + fast-forward to local `main`, no version bump, no push; ORIGIN `cps` = rebase onto `origin/main`, bump `YOLOMUX_VERSION` in `yolomux_lib/infra/common.py`, then push. The version bump belongs only to the ORIGIN/publish path, never to local integration.

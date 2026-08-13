# DOIT: land what is green, close out cleanly

Replaces the open remainder of `DOIT.daemon-pause.md` (47/58 done). That file stays as the record of what was built; **do not work from it any more**. One backlog.

## Where we actually are

- Branch `agent/yostats-pricing-uploads-reliability` at `a9c33803`, **15 ahead and 5 behind** `main` (`9cc9e871`). Main moved under us: another agent landed tmux-signal and alternate-screen work. A fast-forward is no longer possible.
- One uncommitted file: `yolomux_lib/cli.py`, holding an **incomplete** Phase D start/join split.
- Everything else is committed and green as of the last full gate.

## What is already delivered and needs no further work

Phases A, 0, 1, 2, 3, 4, 5, B and C are done. The outage risk that started Phase C/D **is already closed by Phase C**: `ee51ca52` makes preflight compare deployment identity, reap only on mismatch, name the differing component, and escalate SIGTERM to SIGKILL. An upgrade no longer needs a manual SIGKILL.

**Phase D is an architectural refactor, not a safety fix.** Treat it as optional from here.

## Step 1 — decide the uncommitted cli.py (ONE attempt, then stop)

The split is one parameter from working. `cli.py:163 production_backend_deployments()` takes no arguments and hardcodes `PRODUCTION_DAEMON_DOMAINS` / `PRODUCTION_STORAGED_DOMAINS`, while `start_or_join_shared_backend_processes(subsystem_override)` at `:613` threads the override into `DaemonProcessSupervisor` at `:637`. Starter and joiner therefore derive different expectations, and all seven identity fields mismatch under a test profile.

- [ ] Give the identity builder the same override parameter so start and join read one source. Callers are `:838` and `:881`; both have the override in scope. Rename it if `production_` no longer fits.
- [ ] Acceptance is mechanical — these three must pass **together** in one run, because they were previously in contradiction:
  - `tests/test_tls_config.py::test_main_maps_cli_flags_to_app_and_server`
  - `tests/test_tls_config.py::test_normal_cli_startup_does_not_enter_background_owner_election`
  - `tests/test_three_webserver_process_topology.py::test_three_normal_cli_webservers_share_one_external_daemon_and_storaged`
- [ ] **Stop rule: if those three are not green together after ONE focused attempt, run `git checkout -- yolomux_lib/cli.py` and move to Step 2.** Phase D's remaining work is then deferred to a fresh session. Do not iterate on it further tonight.

## Step 2 — rebase onto current main

- [ ] `git fetch` is not needed (local only). Re-read `main` immediately before rebasing; it has moved once already tonight.
- [ ] `git rebase main`. If a conflict appears, stop and report it — do not resolve by guessing. The incoming commits touch tmux signals and alternate-screen routing, which is adjacent to our tmux work.

## Step 3 — verify

- [x] `python3 tools/check.py --lane pytest-unit` green. DONE 2026-07-29: post-rebase lane passed in 128.16s (`/tmp/yolomux-land-close-pytest-unit-20260729-174058.log`).
- [x] Full eight-lane gate, FOREGROUND, tee'd. Report the exact log path. DONE 2026-07-29: `/tmp/yolomux-land-close-final-20260729-175654.log`; browser and E2E passed and all six normal-CLI cohort regressions were absent after reverting `822e17b1`. One non-browser node remained red: `tests/test_daemon_fs_cross_app.py::test_daemon_fs_real_storaged_mux_shares_ready_bytes_and_watch_invalidations`.
- [x] A red gate with 1-3 failures and moving nodeids is load starvation; confirm by isolated re-run before calling it a regression. A red gate with 4+ concentrated in one family is a regression. DONE 2026-07-29: the sole fresh-gate survivor, `tests/test_daemon_fs_cross_app.py::test_daemon_fs_real_storaged_mux_shares_ready_bytes_and_watch_invalidations`, passed 3/3 isolated in 0.18s, 0.08s, and 0.07s; it is load-flaky, not a deterministic regression.

## Step 4 — report, do not land

- [ ] Report the gate result and stop. The user decides whether `main` moves.
- [ ] **Both `DOIT.release-evidence.md` boxes stay UNCHECKED.** They name the user as approver.

## Process rules — these exist because each cost real time today

- **Never background a job.** Every one of six lost stretches today came from backgrounding something and then waiting on it after it had exited. Run gates and lanes in the FOREGROUND with a tee; a foreground command blocks correctly and cannot be waited on wrongly. This is structural: it removes the failure mode rather than asking you to handle it.
- **Never end a turn on a named next step.** Fourteen turns today ended with a correct plan and no action. If you can name the command, run it in the same turn.
- **Fix production code, not the test.** When a test fails after your change, the test usually encodes a property worth keeping. Today: profiles were closed over consumers rather than tests widened, exception paths were removed rather than allowlisted, and the shared-backend regression was fixed in `cli.py` rather than by editing six tests. Keep that instinct.
- **Record only completed runs.** A measurement loop must refuse to record a run that exited in under 60s or with a lock-refusal code, and fail loudly rather than append it.
- **Verify before reporting blocked.** Check the pid is alive and the log mtime advanced. Six times today a "still running" job had already finished.

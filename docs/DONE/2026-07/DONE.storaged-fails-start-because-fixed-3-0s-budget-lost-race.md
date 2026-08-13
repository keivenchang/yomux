# DOIT: storaged fails to start because a fixed 3.0s budget lost a race with a growing stats DB

Goal: make 7770 restarts deterministic again. Right now a restart fails roughly half the time, and the failure is a race between a constant and a database that grows every day, so it gets worse on its own.

Separate from `DOIT.scheduler-payload-ttl.md`, `DOIT.daemon-pause.md` and `DOIT.kill-session-guard.md`. Do not interleave them.

## Not already addressed

Checked before writing this. The timeout was introduced by `4f27865a` ("Move shared backend ownership into storaged and daemon") and has never been modified since. `5411670f` ("Type storaged stats owner failures") is the only other commit to touch the file; it typed the *dispatch* failures and left both defects below intact. This branch is 12 commits ahead of `main` and does not touch it. `DOIT.reported-bugs.md` mentions `stats-v6.sqlite3` only for empty-chart data, not startup.

## Measured on live 7770, 2026-07-30

A restart failed, then the identical retry succeeded. That is the tell.

```
port 7770 did not become ready: /api/ping -> 000
RuntimeError: storaged child unavailable or deployment mismatch      cli.py:686
  caused by: RuntimeError: storaged stats owner did not start        stats_current/storaged.py:156
```

Running storaged by hand to get the real cause, then timing the thing it waits on:

```
stats-v6.sqlite3                349 MB
StatsCurrentService.start()     2.86 s      measured directly
budget                          3.00 s      hardcoded, stats_current/storaged.py:155
headroom                        0.14 s      95% consumed
```

The whole launch aborts on this. `StoragedStatsOwner.start()` waits a fixed 3.0s for its writer thread; `_start()` does `require_compatible_writer` -> `migration_runner` -> `store_opener` against a 349 MB SQLite file (`stats_current/service.py:496-517`). Under any load it exceeds the budget, storaged exits non-zero, and `cli.py` reports the generic deployment-mismatch message.

## [ ] 1. The budget is a constant racing a growing database

`if not self._started.wait(timeout=3.0)` at `stats_current/storaged.py:155`.

3.0s was presumably ample when the DB was small. It is not a property of the work being waited on, and nothing re-evaluates it as the database grows. Growth is not hypothetical -- `observations` went from 163,260 to 308,165 in about a day on this host.

Required: the wait must be sized against the work, not a literal, or startup must stop being a fixed-deadline wait at all. Options worth weighing rather than picking blindly: a budget derived from database size; a wait that only fails when the thread is *proven* dead rather than merely slow; or moving migration/open off the readiness path so "started" means the thread is alive and the expensive work reports progress separately. Whatever is chosen, say in the commit body why it cannot silently re-break when the DB doubles again.

## [ ] 2. The timeout path discards the real cause

```python
if not self._started.wait(timeout=3.0):
    raise RuntimeError("storaged stats owner did not start")   # raises FIRST
if self._startup_error is not None:                            # never reached on timeout
    raise RuntimeError("storaged stats owner failed to start") from self._startup_error
```

`_serve` records the genuine exception in `self._startup_error` and sets `_started` (`storaged.py:184-193`), but the timeout branch raises before the error is ever consulted. When the thread fails for a real reason, the operator gets "did not start" and the cause is thrown away. Diagnosing this required running `yolomux_lib.storaged_process` by hand outside the supervisor; that should not have been necessary. Violates the workspace rule that a failure may never be discarded (CLAUDE.md 3.1) -- the cause must be chained.

## [ ] 3. Retention is not keeping up, which is what feeds defect 1

```
RETENTION_SECONDS   = 24 * 60 * 60          storage.py:34
observations         308,165 rows
observed_at span     2.0 days               min 1785286580 -> max 1785456990
page freelist        0 MB (0% reclaimable)  349 MB is live data, not VACUUM debt
```

The store keeps twice its own retention window. `Store.prune()` exists (`storage.py:1437`) and `service.py:1894` calls it, so the machinery is there -- establish whether it is running at all on this deployment, running too rarely, or being starved. Do not "fix" this by shortening `RETENTION_SECONDS`; that hides whether prune runs. Note 0% reclaimable pages means a VACUUM would not help either -- the rows are really there.

## [ ] 4. Decide whether `stats-v5.sqlite3` is dead

`stats-v5.sqlite3` (55 MB, 160,686 observations) still sits beside v6 (349 MB). If v5 is a completed migration source it is dead weight; if something still reads it, that should be written down. Cheap to answer, and it is 55 MB of a disk that is already under pressure.

## Tests

- [ ] Startup succeeds when the writer thread takes materially longer than today's 3.0s. Inject the delay; do not depend on a large fixture DB, and do not simply raise the literal and assert the literal.
- [ ] When the writer thread raises, the error surfaced to the caller carries the original exception chained (`__cause__`), not the timeout message.
- [ ] `kill -0`-style liveness: a *slow* thread must not be reported the same way as a *dead* one.
- [ ] Retention: after prune runs, no row older than `RETENTION_SECONDS` remains; and a test that fails today if prune never runs on the composed deployment.

Each must fail against current `main` before the change.

## Ground rules

`python3 -m pytest`, never bare `pytest`. Gate is `python3 tools/check.py` in the foreground with a tee; it takes a shared flock at `~/.cache/yolomux/expensive-tools.lock` -- wait rather than bypass. Run `--lane pytest-unit` before committing. Commit locally with explicit paths and `--signoff`; do not push, do not merge, do not run cps.

Do not restart 7770 as part of this work -- it is the user's live deployment and it is currently up on `01d462f2` / `0.6.12`. If you need a server, use 7772 from this worktree.

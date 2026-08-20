# DONE - NFS Configuration-Lock Two-Host Acceptance

Release: v0.7.8. Completed 2026-08-18. Queue: `DOIT.p1.e2.nfs-configuration-lock-acceptance.md` (7/7).

## Result

All five real two-host scenarios passed on the first run at an unmodified HEAD. No product defect was reproduced, so there is no failing-first node or fix HEAD to name. Cross-host POSIX record locking over NFSv4.2 with `local_lock=none` is mutually exclusive in both directions, releases on holder death without deleting the lock file, and the revisioned writer preserves unrelated keys while rejecting stale revisions.

This queue had previously been treated as blocked for lack of a second host. That assumption was wrong: the exporter and client were both already present and already at the same HEAD.

## Preflight

- Exporter `keivenc-linux1` (10.77.0.1), `/etc/exports`: `/home/keivenc 10.77.0.2(rw,sync,no_subtree_check,root_squash)`.
- Client `keivenc-linux2` (10.77.0.2), `findmnt -T /nfs/keivenc`: `10.77.0.1:/home/keivenc nfs4 rw,relatime,vers=4.2,...,sec=sys,local_lock=none`.
- Identical full HEAD on both hosts: `7cb75e3a5de0c81c31b1f4a40f2b98aba2a689c4`. Same uid `1776734304` on both, which matters under `root_squash`.
- One shared disposable `auth.yaml` reachable as `/home/keivenc/yolomux-nfs-lock-accept/auth.yaml` on the exporter and `/nfs/keivenc/yolomux-nfs-lock-accept/auth.yaml` on the client, SHA-256 `36e97635819267718931f56ac0b7f0d1cfbcf7f67dbae60684fbdb12742fc451` matching on both.
- Harness `--help` exit 0 on both hosts, each importing `yolomux_lib.infra.shared_config_lock` from its own checkout at that HEAD. Lock file `.auth.yaml.shared-config.lock` via `fcntl.lockf(LOCK_EX)` (`yolomux_lib/infra/shared_config_lock.py:85-91`).
- `git ls-files --error-unmatch docs/NFS-LOCK-ACCEPTANCE.md` exits 0, so the operative procedure is durable.

## Scenarios

| # | Scenario | Overlap proven | Result |
| --- | --- | --- | --- |
| 1 | exporter-local holder blocks NFS-client waiter | yes | PASS - waiter `lock_acquired` recorded after holder `lock_releasing`, and 20.0 s after its own `wait_started` (that 20.0 s is a single-host elapsed measurement; the sub-millisecond cross-host figure is NOT clock-sync-proven and is withdrawn as a precise delta) |
| 2 | NFS-client holder blocks exporter-local waiter | yes | PASS - waiter acquired after release (ordering proven; the exact cross-host millisecond delta is not clock-sync-proven and is not claimed) |
| 3 | crash release without deleting the lock file | yes | PASS - `kill -KILL` on the exact argv-matched holder PID gave exit 137; waiter acquired at ~2.5 s, on holder death rather than at the 120 s hold |
| 4 | independent keys, same base revision | yes | PASS - exporter merge `revision_conflict=False`, client merge `revision_conflict=True` (typed stale base) 0.4 ms later; final `base`/`exporter_key`/`client_key` all preserved; both monitors `monitor_passed` across 3400 reads with zero `parse_failed` |
| 5 | same key, typed revision conflict | yes | PASS - exactly one `replace_finished` and exactly one `SharedConfigRevisionConflict`, one final `winner`, no torn YAML in either monitor |

In every scenario the waiter's `wait_started` landed during the holder's hold, so exclusion is proven by overlap rather than by sequencing alone.

## Deliberate Deviation

The already-live `/home/keivenc` export was used instead of creating the dedicated `/srv/yolomux-nfs-acceptance` export described in the procedure, because a second export requires sudo and `exportfs` and would perturb a live NFS configuration. The two properties the procedure actually gates on, NFSv4.2 and `local_lock=none`, are satisfied and are quoted in the retained `lin2-mount.txt`. Consequently `exportfs -v` and `nfsstat -m` for a dedicated export were not captured.

## Evidence

Retained at `/tmp/yolomux-nfs-lock-acceptance-7cb75e3a5de0c81c31b1f4a40f2b98aba2a689c4/`: `scenario1.log` through `scenario5.log`, `events.lin1.jsonl`, `events.lin2.jsonl`, `final-auth.yaml`, `lin1-exports.txt`, `lin2-mount.txt`. Being under `/tmp`, this evidence is not durable across cleanup; the durable record is this note plus the tracked `docs/NFS-LOCK-ACCEPTANCE.md` procedure.

## Isolation

No YOLOmux server was started, stopped, or contacted. Ports 7770-7773 were untouched and no live `~/.config/yolomux` was read or written. All disposable artifacts were removed from both hosts and no harness processes remain. Nothing was committed or pushed.

## Timing Caveat

No cross-host clock synchronization proof was retained. Event ordering across the two hosts is established by the blocking semantics themselves - a waiter cannot record `lock_acquired` until the holder releases or dies - and by within-host elapsed measurements such as the 20.0 s hold and the ~2.5 s crash-release acquisition. Exact sub-millisecond cross-host deltas quoted in earlier reporting are NOT supported by retained evidence and are withdrawn. The acceptance conclusions do not depend on them.

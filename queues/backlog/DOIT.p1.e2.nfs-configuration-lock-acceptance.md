# DOIT.p1.e2.nfs-configuration-lock-acceptance.md - Prove Shared Configuration Locking Across NFS

Source provenance: `DOIT.unprioritized.md` U-C, the former `DOIT.multi-host-state-isolation.md`, and the M-LOCK requirement retained from `REGRESSION-GATE.md`.

## Goal

Prove the revisioned shared-configuration writer remains mutually exclusive and merge-safe between an exporter-local process and an NFSv4 client.

## Context

- The safe current model is one private local `YOLOMUX_ROOT` per instance. Mutable WAL databases and Unix sockets never belong on NFS/CIFS/FUSE/9p or an undetermined filesystem.
- Shared configuration uses one revisioned locked-write parent, but only same-host exclusion/merge tests exist. Same-host unit tests are not acceptance evidence for this queue.
- Current evidence and procedure live in `docs/NFS-LOCK-ACCEPTANCE.md`, `docs/MUTABLE-PATH-INVENTORY.md`, `docs/PHASE2_GATE_CENSUS.md`, README, DEVELOPMENT, and DONE.

## Ownership Boundary

This lane owns the two-host locking experiment and any defect it reproduces in the existing shared configuration writer. It does not design cross-host read views, shared services, or mutable network-filesystem storage.

## Execution Order And Ownership

- One operator owns both hosts and the disposable shared file so scenario ordering and evidence cannot race. A second read-only reviewer may audit the harness/events after each run.
- This is acceptance, not a speculative implementation queue. If a scenario fails, freeze its exact evidence and create one focused writer queue for the shared configuration owner; rerun all five scenarios only after that fix lands.

## Plan

- [ ] Run the actual exporter-local plus NFSv4-client configuration-lock acceptance in both directions. Prove mutual exclusion, crash release, same-key conflict behavior, different-key merge preservation, and no torn YAML/secret files; keep raw evidence under `/tmp`.

## Required Invariants

- Never infer host identity or lock scope from an absolute path, hostname, PID, port, tmux target, or Unix-socket pathname alone.
- Shared configuration writes preserve unrelated fields and reject stale revisions.

## Done Criteria

- [ ] `git ls-files --error-unmatch docs/NFS-LOCK-ACCEPTANCE.md` succeeds so the operative procedure is durable, and the DONE note records both host names, identical full YOLOmux HEAD, export path, client mount path, NFS server/version, `local_lock=none`, and a `/tmp/yolomux-nfs-lock-acceptance-<HEAD>/` evidence directory containing mount output, events, and final disposable YAML.
- [ ] Preflight proves both hosts read one shared disposable `auth.yaml`, their SHA-256 values match, both harness `--help` calls succeed from the same HEAD, and every scenario's event log proves waiter `wait_started` occurred between holder `lock_acquired` and `lock_releasing`; a non-overlapping run is recorded INCONCLUSIVE and cannot check this box.
- [ ] Scenario 1 passes exporter-local holder to NFS-client waiter exclusion, and scenario 2 passes the reverse direction; in both cases waiter `lock_acquired` occurs only after holder `lock_releasing`, with no parse/assertion failure.
- [ ] Scenario 3 passes crash release without deleting the lock file: after the blocked waiter records `wait_started`, killing the exact holder PID is followed by waiter `lock_acquired`; a still-blocked waiter is FAIL.
- [ ] Scenario 4 records both merge completions, a typed stale-revision conflict for the second base, final `base`/`exporter_key`/`client_key` values, and `monitor_passed`; scenario 5 records exactly one replace completion, one `SharedConfigRevisionConflict`, one final `winner`, and `monitor_passed`, with no torn/unparseable YAML in either host monitor.
- [ ] If any scenario first reproduced a product defect, the DONE note names its failing-first node and fix HEAD, the focused regression exits 0, and all five real two-host scenarios are rerun on the fixed HEAD; same-host multiprocessing tests alone never close this queue.

## Completion

Record the five conclusive two-host outcomes in `docs/DONE/` and remove this queue. A product defect blocks closure through its separately named fix queue; it is not repaired ad hoc during the acceptance run.

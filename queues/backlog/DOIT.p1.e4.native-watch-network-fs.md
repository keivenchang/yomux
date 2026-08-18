# DOIT.p1.e4.native-watch-network-fs.md - Never Native-Watch A Network Filesystem

Source provenance: `DOIT.unprioritized.md` U-E and the former `DOIT.native-watch-network-fs.md`.

## Goal

Classify every watched root from its actual mount, send only local roots to the native OS watch backend, poll network/undetermined roots with equivalent semantics, and remove the `--no-native-watch` `PYTHONPATH` shim.

## Context

- On keivenc-linux2, the first page-load `/api/watch/roots` registered NFS-backed home paths with watchfiles; the Rust thread entered `rpc_wait_bit_killable`, every other thread blocked behind it, the accept queue climbed, and local curl timed out. This reproduced twice while NFS itself remained healthy.
- `yolo-dev-start.sh --no-native-watch` currently masks the defect by shadowing `watchfiles` with an `ImportError` shim. It disables native watching for every root on the host, including safe local roots, and is not the product fix.
- The Phase 2/release lineage contains a mount-based filesystem classifier for mutable-root refusal. Reuse it; do not create a second classifier based on path prefixes, hostname, or configuration strings.

## Ownership Boundary

This lane owns mount classification at watch admission, native/polled partitioning, diagnostics, launcher cleanup, and real NFS-host acceptance. It must reuse the existing filesystem classifier and must not change descriptor authorization or shared-configuration locking.

## Execution Order And Parallel Ownership

- One watch implementation owner controls classifier consumption, native/polled partitioning, event equivalence, and launcher cleanup. A read-only classifier auditor and a real-host acceptance operator may prepare matrices/scripts in parallel.
- Product code and synthetic tests land before any workaround is removed. Real keivenc-linux2 acceptance is serial; only its success authorizes removing the launcher/cron shim.
- This may run in parallel with NFS configuration-lock acceptance because they touch different owners. Coordinate shared mount-classifier changes with the filesystem authorization owner rather than copying classification logic.

## Plan

- [ ] Add or adapt one filesystem classifier that reports local versus network for an arbitrary path from the actual mount and covers NFS/CIFS/FUSE/9p plus undetermined types; reuse the existing multi-host classifier when it meets the contract.
- [ ] Partition native watch configuration into native and polled sets. Start watchfiles only for local roots, and skip the native watcher thread entirely when that set is empty.
- [ ] Give polled roots the same correctness semantics as native roots, including overflow/loss reconciliation, so callers observe only a bounded latency difference.
- [ ] Add a documented `YOLOMUX_DISABLE_NATIVE_WATCH` operator escape hatch without import shims.
- [ ] Surface per-root native/polled classification and its reason in bounded runtime diagnostics.
- [ ] Remove `--no-native-watch` from the launcher and skill, remove it from the keivenc-linux2 `@reboot` entry, and remove the generated shim directory only after the product path and real-host acceptance pass.
- [ ] Add classifier unit tests with a synthetic mount table covering nfs4, cifs, fuse, 9p, tmpfs, ext4, and undetermined; undetermined must poll.
- [ ] Prove with a fake watch callable that watchfiles never receives a network root.
- [ ] Add a mixed-root test proving the native backend receives exactly the local root and polling receives exactly the network root.
- [ ] Add an all-network regression proving no watcher thread starts and the server remains responsive.
- [ ] Run real acceptance on keivenc-linux2 without `--no-native-watch`: load the page and hold 15 minutes with listener accept queue 0, no `rpc_wait_bit_killable` watcher thread, and responsive host curl; keep raw evidence under `/tmp`.

## Rejected Shortcuts

- Do not disable native watching globally, retain the import shim, classify by pathname/hostname, leave a timed-out RPC thread parked, remount the home tree soft, or treat restart as a fix.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, exact test node IDs, commands/exit codes, the synthetic mount table, configured polling interval in seconds, keivenc-linux2 server PID/CWD/HEAD/port, the authenticated curl command with `--max-time 2`, and `/tmp` evidence paths.
- [ ] `python3 -m pytest -q tests/test_watchd.py tests/test_filesystem.py` exits 0 and the classifier matrix maps `ext4` and `tmpfs` to native, `nfs`/`nfs4`/`cifs`/`fuse.*`/`9p` to polling, and missing/undetermined mount types to polling.
- [ ] Fake-watch tests assert watchfiles receives exactly the local-root set and never a network/undetermined root, a mixed set routes each root to exactly one backend, an all-network set starts zero native watcher threads, and create/modify/delete plus overflow/loss reconciliation produce one equivalent logical revision on both backends.
- [ ] An unmodified `python3 tools/check.py` exits 0 before launcher cleanup; keivenc-linux2 then starts without `--no-native-watch`, without the `PYTHONPATH` shim, and with diagnostics that list each watched root, backend, mount type, and classification reason.
- [ ] Real-host acceptance runs for 15 continuous minutes and records 180 five-second samples: every authenticated `curl --fail --silent --show-error --max-time 2` probe succeeds, listener accept queue is 0 in every sample, no watcher thread stack contains `rpc_wait_bit_killable`, local-root changes arrive through native watch, and network-root changes arrive through polling within one configured polling interval plus one second.
- [ ] Only after that acceptance, negative searches of the launcher, `yolo-dev-start` skill, current crontab, environment, and generated shim path find zero active `--no-native-watch`/shim references; the DONE note records the exact files/entry removed and repeats one successful restart and 60-second smoke without the workaround.

## Completion

Summarize the classifier, equivalent native/poll semantics, real-host evidence, and removed workaround in `docs/DONE/`, then remove this queue.

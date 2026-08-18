# DOIT.p1.e3.operation-journal-port-scope.md - Prevent Sibling Ports From Abandoning Accepted Operations

## Goal

One YOLOmux server must not terminalize another live server's accepted operation as `producer_abandoned` merely because both processes use the same host-scoped state root.

## Current Evidence

- The accepted-operation journal is derived from the host-partitioned state directory, not an origin-server identity.
- Startup recovery terminalizes every loaded open record as `producer_abandoned`.
- The resulting cross-port failure was reproduced historically but has not been re-reproduced on current main. Same-host shared `STATE_DIR` is therefore a required precondition to establish before editing.

## Plan

- [ ] Reproduce with two fixture-owned servers sharing one host state root: hold an accepted operation open on the origin, start the sibling, and prove whether the sibling terminalizes it while the origin still owns production.
- [ ] Give every accepted-operation record one stable origin/owner identity and scope recovery to records whose exact owner generation is proven dead, or centralize the journal under one cross-process owner.
- [ ] Preserve restart replay, exactly-once terminalization, stale-owner repair, authorization, bounded retention, and missed-SSE recovery without adding a second journal.
- [ ] Add current sibling-port, origin restart, sibling restart, PID reuse, stale generation, and simultaneous recovery regressions; run focused operation/session-files tests and the canonical gate.

## Done Criteria

- [ ] The red fixture records both PIDs, ports, root, owner generations, journal bytes, origin `202`, sibling startup, and the false terminal event on the pre-fix baseline.
- [ ] Starting or stopping a live sibling produces zero state changes for the origin's open record; only a proven-dead exact origin generation may recover it as `producer_abandoned`.
- [ ] Every accepted receipt terminates exactly once across normal completion, origin restart, sibling restart, stale record, and simultaneous recovery, with no unowned journal copy.
- [ ] Focused tests and an unmodified `python3 tools/check.py` exit 0; restarted isolated servers prove the same behavior on one unchanged HEAD.

## Completion

Record the owner identity, red/green sibling-port evidence, exact tests, and runtime proof in `docs/DONE/`, then remove this queue.

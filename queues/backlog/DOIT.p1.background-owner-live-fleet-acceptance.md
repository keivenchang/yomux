# DOIT.p1.background-owner-live-fleet-acceptance.md - Verify Shared Background Ownership On 7770-7773

## Goal

Prove the shipped single-owner background indexing and cache model on the real Linux fleet without confusing isolated fixture evidence with multi-port acceptance.

## Plan

- [ ] With explicit permission to restart or drive 7770-7773, record each port's PID, CWD, HEAD, state root, owner generation, service roster, and served bundle before mutation.
- [ ] Verify startup ownership order, 7771 takeover after restart, multi-port Tabber/Finder cache writes, shared-root Quick Open/search indexing, follower worker-thread absence, and UI responsiveness during a large index rebuild.
- [ ] Restore the requested fleet topology and retain only bounded evidence under `/tmp`; unrelated ports and state must remain unchanged.

## Done Criteria

- [ ] One current same-SHA run proves exactly one background owner, deterministic takeover, shared cache convergence, no follower worker, and responsive authenticated UI on every requested port.
- [ ] The DONE record links the already-complete isolated 8004-8007 implementation evidence and separately records the real-fleet acceptance result.

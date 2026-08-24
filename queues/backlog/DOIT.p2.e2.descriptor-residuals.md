# DOIT.p2.e2.descriptor-residuals.md - Deferred Descriptor and Stats Evidence

## Goal

Retain evidence work deliberately removed from the filesystem descriptor landing path on 2026-08-23. This queue is not part of the current `STATUS-REPORT.md` inventory and must not block landing `DOIT.p2.e2.filesystem-descriptor-authorization.md`.

## Queue

- [ ] Reconstruct and preserve the historical pre-fix descriptor failures only if a future release audit requires the literal `BLOCKED_SENTINEL_DO_NOT_EXPOSE` payload in a red artifact; do not recreate an old vulnerable implementation merely to manufacture this evidence.
- [ ] Fix the independent YO!stats range-selection drift exposed by the first composed canonical gate: a `3600/AUTO` selection can request 60-second readiness while the controller resolves the snapshot to 300 seconds, and cached 3600-second selections refetch instead of reusing their generation. Reproduce in the full parallel resource manifest before changing product code.

## Done Criteria

- [ ] Each item has a current reproducer, a named owner, focused tests, and an unmodified canonical gate result before archival.

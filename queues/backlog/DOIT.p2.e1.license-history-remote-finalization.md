# DOIT.p2.e1.license-history-remote-finalization.md - Remove Remaining Old-License Remote Tags

## Goal

Finish remote all-ref license cleanup. Remote `main` is already finalized at the rewritten local SHA, but three remote tags still expose the old lineage and require separate explicit tag-mutation authorization.

## Plan

- [x] Reverify the local rewritten history, target remote branch, lease SHA, current tree license, key historical searches, and fresh-clone procedure without changing the remote. DONE: remote `main`, local `main`, and this checkout all resolve to `0da574142addd1ba27d0cf66e91496b32a2412d9`; 1,017 commits passed 6,102 license-identity checks with zero mismatches.
- [x] Verify a fresh branch-only clone from remote `main` against every key license search and current-tree file identity. DONE: `/tmp/yolomux-078-license-clone.8sARgj` was cloned with `--no-tags --single-branch --branch main`; root `f4f57046733b942db3ca362bfcd5272456c6d766` and all 6,102 checks matched.
- [ ] Obtain explicit authorization to atomically delete or replace only remote tags `v0.2.0`, `v0.3.0`, and `v0.4.5`, leased respectively at tag objects `f3a8a71e3327e677e40e02647e67dd75219c95ab`, `aa66797b304482d23e038eee73f0a9f5d27a60cc`, and `a251d7a219074f8c899c432d96e5486139839d87`. A `main` force-push would be a no-op and must not be substituted for tag authorization. Atomic lease-protected deletion is the smallest mutation; preserving the SSH-signed tag names requires signing three new annotated tag objects that target the rewritten commits.

## Done Criteria

- [ ] Every remote ref is free of the old license lineage, and a fresh all-ref clone verifies the result. Live re-verification found 29 remote refs and exactly these three old-lineage tags: `v0.2.0` peels to `902ab8482f5062bf6d46aa50e0323d1e4eab54df`, `v0.3.0` to `cebdee36fa3c030adc4e4ef77d7fbf8c295461a2`, and `v0.4.5` to `3ee9abfab6e2d9dc4ce4cb00b6f6f141efae361f`. No additive or branch-only action can satisfy the all-ref requirement.

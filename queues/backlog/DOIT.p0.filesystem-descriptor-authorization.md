# DOIT.p0.filesystem-descriptor-authorization.md - Close Filesystem Authorization/Use Races

Source provenance: `DOIT.unprioritized.md` U-A, the former `DOIT.filesystem-descriptor-authorization.md`, and the filesystem-security findings retained from `REGRESSION-GATE.md`.

## Goal

Make every filesystem API consume the same object generation that passed authorization, so a namespace replacement cannot expose blocked content, metadata, link targets, archive bytes, Git diff bytes, or indexed-search annotations.

## Context

- This is the high-priority security follow-up split from the former `MASTERPLAN.md`; this file is now its only active queue owner. Deterministic probes on 2026-08-02 showed that authorizing a canonical path string before use is not sufficient.
- The shared policy owner is `yolomux_lib/filesystem/paths.py`; reads and mutations are in `io_ops.py`, listings in `listing.py`, Git operations in `git_ops.py`, and search/index producers span `filesystem/search.py`, `search_indexer.py`, and daemon filesystem ports. Extend one descriptor-bound parent instead of adding route-specific guards.
- Direct reads and some parent mutations have partial protection in the Phase 2/release lineage, but listing, recursive zip, diff, and indexed-search metadata still have known generation gaps. Reproduce each current failure on the actual implementation baseline before editing.
- The Phase 2 evidence is pinned to historical `a2046ea37`, but implementation must start from current `origin/main` in this worktree. Reproduce every race on that current baseline before editing; historical branch or worktree names are evidence provenance, not implementation authority.

## Ownership Boundary

This lane owns descriptor-bound filesystem authorization across all filesystem consumers. It may coordinate with Differ, search, and watch owners for tests, but no other queue should add a route-specific path authorization copy.

## Execution Order And Parallel Ownership

- Do not split implementation across independent writers: every consumer must use one descriptor-bound security parent, and partial route-specific owners would recreate the vulnerability. One writer owns `filesystem/paths.py` and the shared descriptor lifetime API through final composition.
- Before that API is frozen, three read-only agents may audit consumers in parallel: direct/list/recursive reads; Git/search/index metadata; and create/write/rename/delete. Each returns a complete call-site matrix and failing barriers without editing the shared owner.
- After the parent lands red-to-green, consumer migrations may be assigned by those three conflict groups, but the parent owner integrates shared files and an independent adversarial reviewer reruns the whole matrix before the canonical gate.

## Plan

- [ ] Build deterministic failing probes for each known race: replace an authorized file before read consumption, replace a listed regular child with a blocked symlink, replace a recursive zip descendant, replace a diff target after authorization but before Git consumption, change indexed-search identity between canonicalization and metadata annotation, and swap a parent before a namespace mutation. Record the exact leaked content or metadata and the violated generation invariant.
- [ ] Add one shared descriptor-bound authorization owner in the filesystem package that opens without following unintended links, validates policy and identity from the live descriptor, retains the descriptor through consumption, and fails with a typed error when the platform cannot provide the required descriptor semantics. Do not let a cached path string or prior `realpath` become authority.
- [ ] Migrate single-file reads, raw/media reads, path info, directory listing, symlink text/metadata, recursive count/zip, and search/index metadata to consume the authorized descriptor generation. Every recursive child must be opened relative to its pinned parent generation; no descendant may be reopened later by absolute path.
- [ ] Migrate `diff_file()` and other Git-backed file consumers so the authorized descriptor remains live until Git or the replacement bounded consumer has finished. Prove a namespace swap cannot make Git read a different file or return blocked working-tree content; preserve committed/deleted/untracked/ref-fallback behavior.
- [ ] Route create, write, rename, and delete through an authorized parent descriptor plus exactly one validated basename. Preserve configured-root refusal, expected-mtime conflict behavior, Git-aware moves, atomic replacement, and path-policy cache invalidation without re-resolving an attacker-controlled parent.
- [ ] Add an exact regression for every reproduced race plus a property-derived matrix across regular files, symlinks, hardlinks, missing targets, parent replacement, unsupported descriptor paths, allowed-root boundaries, blocked-secret paths, and concurrent namespace changes. Tests must prove the descriptor stays live through consumption, not merely inspect source text.
- [ ] Update `docs/specs/GUI.md`, `docs/DEVELOPMENT.md`, and operator-facing error text for the descriptor-bound contract; run focused filesystem, route, search/index, upload, and browser tests, then `python3 tools/check.py` on the implementation baseline and restart its active dev server before marking the section complete.

## Gotchas

- `resolve()`, `realpath()`, `stat()`, and an allowlist check followed by `open(path)` are still check/use races.
- `/proc/self/fd` is not a portable permission boundary. If a consumer or platform reopens the pathname rather than using the held descriptor, fail closed or use a bounded descriptor-native adapter.
- Directory enumeration and recursive walking need generation pinning for every child; pinning only the root descriptor does not authorize later path reopens.
- Do not weaken the secret-path policy, follow blocked symlinks for convenience, serialize the tests, add sleeps, or replace deterministic race barriers with retries.

## Done Criteria

- [ ] Before editing, the DONE note names the implementation worktree and current `origin/main` HEAD; every deterministic barrier test is captured red before the fix with the literal blocked payload `BLOCKED_SENTINEL_DO_NOT_EXPOSE` visible in the failing result.
- [ ] The focused command `python3 -m pytest -q tests/test_filesystem.py tests/test_filesystem_access_policy.py tests/test_filesystem_authorize_repoint.py tests/test_finder_fs_repro.py tests/test_browser_finder_fs_repro.py` exits 0 and covers file-read replacement, listed-child-to-blocked-symlink replacement, recursive-zip descendant replacement, diff-target replacement, indexed-search identity replacement, and parent replacement before create/write/rename/delete.
- [ ] For every race, the post-fix result contains zero `BLOCKED_SENTINEL_DO_NOT_EXPOSE` bytes and zero blocked metadata fields; it either returns data from the originally authorized descriptor generation or the exact typed descriptor/policy error pinned by the test, and descriptor-lifetime assertions prove the descriptor remains open through the final consumer read.
- [ ] The property matrix covers regular files, symlinks, hardlinks, missing targets, allowed-root boundaries, blocked-secret paths, unsupported descriptor semantics, parent replacement, and concurrent namespace changes for read/info/list/count/zip/search/index/diff/create/write/rename/delete; every row records expected bytes/metadata/error and actual result.
- [ ] A consumer inventory under `/tmp` names every filesystem route, daemon producer, search/index annotator, Git consumer, and namespace mutation, and its negative search finds zero post-authorization absolute-path reopen authorities outside the one shared descriptor-bound owner.
- [ ] `python3 tools/static_build.py --check` when frontend assets are touched and an unmodified `python3 tools/check.py` both exit 0; after restart, the DONE note records active PID/CWD/HEAD and repeats the six live race probes against the authenticated implementation server with the same zero-leak oracle.

## Completion

Only the full consumer matrix can close this P0. Summarize the one shared parent, every migrated consumer, negative search, and live zero-leak evidence in `docs/DONE/`, then remove this queue; never close or split off a subset while another absolute-path reopen remains.

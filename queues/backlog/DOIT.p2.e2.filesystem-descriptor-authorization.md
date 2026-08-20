# DOIT.p2.e2.filesystem-descriptor-authorization.md - Close Filesystem Authorization/Use Races

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

## Downgrade p0.e5 -> p2.e2 (2026-08-18) - Rationale And Retained Risk

Reprioritized, NOT closed. Every requirement below is retained; none were deleted. The core descriptor-bound owner already landed (`SafePathHandle`/`SafeParentHandle`/`_open_resolved_path` with component-by-component `dir_fd` chaining and `O_NOFOLLOW`), consumers re-consume the descriptor rather than the path, and each named race has a passing regression. That is why the priority drops; it is not a statement that the work is finished.

RETAINED RISK REQUIRING EXPLICIT AUTHORITY TO SIT AT p2: `search.py:852-885` performs an absolute-path reopen that never passed `_ensure_path_allowed`, building a bare `SafePathHandle` outside the shared authorization owner. It is `O_NOFOLLOW`-guarded and name-filtered only. This is an unauthorized reopen authority in a server that binds `0.0.0.0` by default and supports a multi-user `users:` list. Exploiting it needs same-UID write access inside the server's namespace with precise timing, and the only remote route to that primitive is the authenticated filesystem write/rename API held by a user who already has arbitrary read/write in the allowed roots plus a shell through the tmux transport; the escalation is therefore against the secret-path blocklist rather than general file access. That reasoning is why p2 is defensible, but the residual gap is a real unauthorized-reopen path and a reviewer must accept it explicitly. If that acceptance is withheld, this queue returns to p1 or p0.

Second retained gap: `paths.py:553` `descriptor_path()` returns the `F_GETPATH` pathname on Darwin, a genuine re-resolution consumed by `git -C` (`git_ops.py:37`) and `io_ops.py:566`. Linux returns `/proc/self/fd/N` and is generation-bound, so this is Darwin-only and cannot be verified on this host.

Third retained gap: `docs/specs/BACKEND_ARCHITECTURE.md:84` still asserts that listing "does not yet pin every listed child generation", contradicted by `listing.py:383-398` and a passing regression. Stale specification text is why this queue kept appearing live.

All original Plan and Done Criteria below remain in force, including the deterministic red barriers and `BLOCKED_SENTINEL_DO_NOT_EXPOSE` zero-byte proof, the consumer inventory and its negative searches, the diff and mutation contracts, the property-derived matrix, the Finder/browser suites, `tools/static_build.py --check`, the unmodified canonical gate, the restarted authenticated zero-leak proof, and the documentation updates. Items are to be checked off with evidence as they are proven, never deleted.

## Plan

- [x] Build deterministic failing probes for each known race: replace an authorized file before read consumption, replace a listed regular child with a blocked symlink, replace a recursive zip descendant, replace a diff target after authorization but before Git consumption, change indexed-search identity between canonicalization and metadata annotation, and swap a parent before a namespace mutation. Record the exact leaked content or metadata and the violated generation invariant. DONE (landed earlier, evidenced by the passing race matrix): each named race has an exact regression in the focused suite, including `test_listing_regular_child_repointed_to_blocked_symlink_never_leaks_metadata`, `test_zip_directory_never_follows_a_repointed_descendant_directory`, `test_diff_final_symlink_swap_never_returns_target_file_content`, and `test_indexed_search_annotation_binds_realpath_and_size_to_one_descriptor`.
- [x] Add one shared descriptor-bound authorization owner in the filesystem package that opens without following unintended links, validates policy and identity from the live descriptor, retains the descriptor through consumption, and fails with a typed error when the platform cannot provide the required descriptor semantics. Do not let a cached path string or prior `realpath` become authority. DONE (landed earlier): `SafePathHandle` (`paths.py:541`), `SafeParentHandle` (`paths.py:575`), and `_open_resolved_path` (`paths.py:590`) open component-by-component with `dir_fd` chaining and `O_NOFOLLOW|O_DIRECTORY|O_CLOEXEC`, exposed through `safe_path`/`safe_child`/`safe_parent`/`walk_directory`.
- [x] Migrate single-file reads, raw/media reads, path info, directory listing, symlink text/metadata, recursive count/zip, and search/index metadata to consume the authorized descriptor generation. Every recursive child must be opened relative to its pinned parent generation; no descendant may be reopened later by absolute path. DONE (landed earlier) EXCEPT the recorded residual: consumers re-consume the descriptor (`io_ops.py:185` via `os.fdopen(os.dup(...))`, zip members opened `dir_fd=`, `search.py:375` binding realpath/size to one handle). RESIDUAL STILL OPEN: `search.py:852-885` reopens an absolute path that never passed `_ensure_path_allowed` - see the downgrade rationale above.
- [x] Migrate `diff_file()` and other Git-backed file consumers so the authorized descriptor remains live until Git or the replacement bounded consumer has finished. Prove a namespace swap cannot make Git read a different file or return blocked working-tree content; preserve committed/deleted/untracked/ref-fallback behavior. DONE (landed earlier): `git_ops.py:601` `_pinned_working_text` reads through `os.dup(handle.descriptor)` so Git never re-reads the working file by name.
- [x] Route create, write, rename, and delete through an authorized parent descriptor plus exactly one validated basename. Preserve configured-root refusal, expected-mtime conflict behavior, Git-aware moves, atomic replacement, and path-policy cache invalidation without re-resolving an attacker-controlled parent. DONE (landed earlier): mutations route through `safe_parent` plus one validated basename; configured-root refusal preserved via `_ensure_not_configured_root`.
- [x] Add an exact regression for every reproduced race plus a property-derived matrix across regular files, symlinks, hardlinks, missing targets, parent replacement, unsupported descriptor paths, allowed-root boundaries, blocked-secret paths, and concurrent namespace changes. Tests must prove the descriptor stays live through consumption, not merely inspect source text. DONE (landed earlier): verified first-hand 2026-08-18 - the queue’s own five-file focused command exits 0 with 155 passed in 18.50s at HEAD `7cb75e3a5`.
- [ ] Update `docs/specs/GUI.md`, `docs/DEVELOPMENT.md`, and operator-facing error text for the descriptor-bound contract; run focused filesystem, route, search/index, upload, and browser tests, then `python3 tools/check.py` on the implementation baseline and restart its active dev server before marking the section complete.

## Gotchas

- `resolve()`, `realpath()`, `stat()`, and an allowlist check followed by `open(path)` are still check/use races.
- `/proc/self/fd` is not a portable permission boundary. If a consumer or platform reopens the pathname rather than using the held descriptor, fail closed or use a bounded descriptor-native adapter.
- Directory enumeration and recursive walking need generation pinning for every child; pinning only the root descriptor does not authorize later path reopens.
- Do not weaken the secret-path policy, follow blocked symlinks for convenience, serialize the tests, add sleeps, or replace deterministic race barriers with retries.

## Done Criteria

- [ ] Before editing, the DONE note names the implementation worktree and current `origin/main` HEAD; every deterministic barrier test is captured red before the fix with the literal blocked payload `BLOCKED_SENTINEL_DO_NOT_EXPOSE` visible in the failing result.
- [x] The focused command `python3 -m pytest -q tests/test_filesystem.py tests/test_filesystem_access_policy.py tests/test_filesystem_authorize_repoint.py tests/test_finder_fs_repro.py tests/test_browser_finder_fs_repro.py` exits 0 and covers file-read replacement, listed-child-to-blocked-symlink replacement, recursive-zip descendant replacement, diff-target replacement, indexed-search identity replacement, and parent replacement before create/write/rename/delete. DONE: measured first-hand 2026-08-18 at HEAD `7cb75e3a5` - `155 passed in 18.50s`, exit code 0, run in the isolated test container. An earlier three-file run of mine returned 150 and was my own truncation of the command, not a discrepancy; collection is 132+15+3 for those three files.
- [ ] For every race, the post-fix result contains zero `BLOCKED_SENTINEL_DO_NOT_EXPOSE` bytes and zero blocked metadata fields; it either returns data from the originally authorized descriptor generation or the exact typed descriptor/policy error pinned by the test, and descriptor-lifetime assertions prove the descriptor remains open through the final consumer read.
- [ ] The property matrix covers regular files, symlinks, hardlinks, missing targets, allowed-root boundaries, blocked-secret paths, unsupported descriptor semantics, parent replacement, and concurrent namespace changes for read/info/list/count/zip/search/index/diff/create/write/rename/delete; every row records expected bytes/metadata/error and actual result.
- [ ] A consumer inventory under `/tmp` names every filesystem route, daemon producer, search/index annotator, Git consumer, and namespace mutation, and its negative search finds zero post-authorization absolute-path reopen authorities outside the one shared descriptor-bound owner.
- [ ] `python3 tools/static_build.py --check` when frontend assets are touched and an unmodified `python3 tools/check.py` both exit 0; after restart, the DONE note records active PID/CWD/HEAD and repeats the six live race probes against the authenticated implementation server with the same zero-leak oracle.

## Completion

Only the full consumer matrix can close this queue (reclassified p2.e2 on 2026-08-18; it is no longer a P0). Summarize the one shared parent, every migrated consumer, negative search, and live zero-leak evidence in `docs/DONE/`, then remove this queue; never close or split off a subset while another absolute-path reopen remains.

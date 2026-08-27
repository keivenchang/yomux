# DOIT.p2.e2.filesystem-descriptor-authorization.md - Close Filesystem Authorization/Use Races

## Queue Lineage

- Authoritative queue: this file in `/home/keivenc/dev/yolomux.dev7771-unified`, branch `integration/v0.7.13-one-ai`, HEAD `faacdcfb36545f9585d954fafe1ba662d81fa357` when retargeted on 2026-08-24.
- Worked source: `/tmp/yolomux-0710-integration.2203800`, branch `integration/v0.7.12-20260821`, HEAD `929085bd7b4f708633683bc921bf8f8cb81e9ddf`; the source and unified queue bodies matched byte-for-byte before this lineage note and remain at 7/13.
- Status: unfinished and paused. The old source queue is removed after transfer; its dirty implementation worktree remains untouched.

Source provenance: `DOIT.unprioritized.md` U-A, the former `DOIT.filesystem-descriptor-authorization.md`, and the filesystem-security findings retained from `REGRESSION-GATE.md`.

## Goal

Make every filesystem read and metadata API consume the same object generation that passed authorization, so a namespace replacement cannot expose blocked content, metadata, link targets, archive bytes, Git diff bytes, or indexed-search annotations. Namespace mutations pin the authorized parent and basename, then preserve rename/delete behavior by treating the final basename syscall as the linearization point.

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

The previously retained `search.py` absolute-path reopen is closed in this candidate. The non-recursive multi-repository scan now opens every child relative to the pinned scan-root descriptor through `paths.safe_child()`; the negative search finds no bare `SafePathHandle` construction outside `filesystem/paths.py` and no post-authorization absolute reopen authority.

The previously retained Darwin gap is also closed. `descriptor_path()` returns only a live per-descriptor magic path (`/proc/self/fd/N` or `/dev/fd/N`) and fails closed when neither is available; the forced-Darwin regression proves the old `F_GETPATH` pathname branch is not consulted.

The stale backend specification is corrected: it now states that listing authorizes and opens every child relative to the pinned parent descriptor through `paths.safe_child()`.

## Approved Namespace Mutation Contract (2026-08-23)

- Keiven's decision: preserve rename and delete behavior. The final `renameat2`/`renameatx_np`, `unlinkat`, or `rmdir` syscall is the linearization point.
- The implementation must keep the authorized parent descriptor live, validate exactly one basename, preserve the pre-syscall source-identity check, and use no-replace destination semantics for rename. Linux and macOS do not provide a namespace mutation that is conditional on a previously opened source inode, so a same-UID writer can replace the basename after the final validation and before the syscall.
- This residual basename race is documented and accepted for rename/delete. Do not fail these operations closed merely because the syscall cannot be conditional on the pinned inode, and do not claim the residual race is closed.
- The tests `test_rename_fails_closed_before_the_final_name_based_syscall` and `test_delete_fails_closed_before_the_final_name_based_syscall` encoded the rejected fail-closed contract and are removed. Earlier descriptor-pin replacement tests remain required because they exercise races before the linearization point.

All original Plan and Done Criteria below remain in force, including the deterministic red barriers and `BLOCKED_SENTINEL_DO_NOT_EXPOSE` zero-byte proof, the consumer inventory and its negative searches, the diff and mutation contracts, the property-derived matrix, the Finder/browser suites, `tools/static_build.py --check`, the unmodified canonical gate, the restarted authenticated zero-leak proof, and the documentation updates. Items are to be checked off with evidence as they are proven, never deleted.

## Plan

- [x] Build deterministic failing probes for each known race: replace an authorized file before read consumption, replace a listed regular child with a blocked symlink, replace a recursive zip descendant, replace a diff target after authorization but before Git consumption, change indexed-search identity between canonicalization and metadata annotation, and swap a parent before a namespace mutation. Record the exact leaked content or metadata and the violated generation invariant. DONE (landed earlier, evidenced by the passing race matrix): each named race has an exact regression in the focused suite, including `test_listing_regular_child_repointed_to_blocked_symlink_never_leaks_metadata`, `test_zip_directory_never_follows_a_repointed_descendant_directory`, `test_diff_final_symlink_swap_never_returns_target_file_content`, and `test_indexed_search_annotation_binds_realpath_and_size_to_one_descriptor`.
- [x] Add one shared descriptor-bound authorization owner in the filesystem package that opens without following unintended links, validates policy and identity from the live descriptor, retains the descriptor through consumption, and fails with a typed error when the platform cannot provide the required descriptor semantics. Do not let a cached path string or prior `realpath` become authority. DONE (landed earlier): `SafePathHandle` (`paths.py:541`), `SafeParentHandle` (`paths.py:575`), and `_open_resolved_path` (`paths.py:590`) open component-by-component with `dir_fd` chaining and `O_NOFOLLOW|O_DIRECTORY|O_CLOEXEC`, exposed through `safe_path`/`safe_child`/`safe_parent`/`walk_directory`.
- [x] Migrate single-file reads, raw/media reads, path info, directory listing, symlink text/metadata, recursive count/zip, and search/index metadata to consume the authorized descriptor generation. Every recursive child must be opened relative to its pinned parent generation; no descendant may be reopened later by absolute path. DONE: consumers re-consume the descriptor (`io_ops.py` via `os.fdopen(os.dup(...))`, zip members open relative to pinned parents, and `search.py` binds realpath/size to one handle); the former non-recursive multi-repository absolute child reopen now routes through `paths.safe_child()`.
- [x] Migrate `diff_file()` and other Git-backed file consumers so the authorized descriptor remains live until Git or the replacement bounded consumer has finished. Prove a namespace swap cannot make Git read a different file or return blocked working-tree content; preserve committed/deleted/untracked/ref-fallback behavior. DONE (landed earlier): `git_ops.py:601` `_pinned_working_text` reads through `os.dup(handle.descriptor)` so Git never re-reads the working file by name.
- [ ] Route create, write, rename, and delete through an authorized parent descriptor plus exactly one validated basename. Preserve configured-root refusal, expected-mtime conflict behavior, Git-aware moves, atomic replacement, and path-policy cache invalidation without re-resolving an attacker-controlled parent. REOPENED: direct mutations still use `safe_parent`, but the recursive-delete escalation in `DOIT.p1.e3.filesystem-delete-lane-split.md` re-produces a raw pathname after the original descriptor probe. Reconcile that consumer through this shared authorization owner before restoring the broad mutation claim.
- [ ] Add an exact regression for every reproduced race plus a property-derived matrix across regular files, symlinks, hardlinks, missing targets, parent replacement, unsupported descriptor paths, allowed-root boundaries, blocked-secret paths, and concurrent namespace changes. Tests must prove the descriptor stays live through consumption, not merely inspect source text. REOPENED: the retained matrix predates the recursive-escalation namespace-replacement dimension and therefore does not prove the full current consumer set.
- [ ] Open a readable file before optional Git enrichment completes. Git status/history metadata may update the open file later, but Git failure, timeout, or absence must not delay or disable Open. Add the exact terminal-file regression through the shared file-open path and verify the generated bundle plus browser-visible behavior.
- [ ] Update `docs/specs/GUI.md`, `docs/DEVELOPMENT.md`, and operator-facing error text for the descriptor-bound contract; run focused filesystem, route, search/index, upload, and browser tests, then `python3 tools/check.py` on the implementation baseline and restart its active dev server before marking the section complete.

## Gotchas

- `resolve()`, `realpath()`, `stat()`, and an allowlist check followed by `open(path)` are still check/use races.
- `/proc/self/fd` is not a portable permission boundary. A read or metadata consumer that would reopen the pathname rather than use the held descriptor must fail closed or use a bounded descriptor-native adapter. Rename/delete follow the approved final-syscall contract above.
- Directory enumeration and recursive walking need generation pinning for every child; pinning only the root descriptor does not authorize later path reopens.
- Do not weaken the secret-path policy, follow blocked symlinks for convenience, serialize the tests, add sleeps, or replace deterministic race barriers with retries.

## Done Criteria

- [x] Scope cut: the retroactive red-before-fix artifact requirement moved to `DOIT.p2.e2.descriptor-residuals.md`; the current landing does not recreate an old vulnerable implementation to manufacture evidence.
- [x] The focused command `python3 -m pytest -q tests/test_filesystem.py tests/test_filesystem_access_policy.py tests/test_filesystem_authorize_repoint.py tests/test_finder_fs_repro.py tests/test_browser_finder_fs_repro.py` exits 0 and covers file-read replacement, listed-child-to-blocked-symlink replacement, recursive-zip descendant replacement, diff-target replacement, indexed-search identity replacement, and parent replacement before create/write/rename/delete. DONE: measured first-hand 2026-08-18 at HEAD `7cb75e3a5` - `155 passed in 18.50s`, exit code 0, run in the isolated test container. An earlier three-file run of mine returned 150 and was my own truncation of the command, not a discrepancy; collection is 132+15+3 for those three files.
- [x] For every read or metadata race, the post-fix result contains zero `BLOCKED_SENTINEL_DO_NOT_EXPOSE` bytes and zero blocked metadata fields; it either returns data from the originally authorized descriptor generation or the exact typed descriptor/policy error pinned by the test, and descriptor-lifetime assertions prove the descriptor remains open through the final consumer read. Rename/delete races before the final syscall still reject a changed source; the final syscall itself follows the approved linearization contract. DONE: the 153-test descriptor module passed; its 122 data-row matrix contained zero sentinel matches, and the five-file focused descriptor/browser suite passed 443/443.
- [ ] The property matrix covers regular files, symlinks, hardlinks, missing targets, allowed-root boundaries, blocked-secret paths, unsupported descriptor semantics, parent replacement, and concurrent namespace changes for read/info/list/count/zip/search/index/diff/create/write/rename/delete; every row records expected bytes/metadata/error and actual result. Rename/delete rows must distinguish a replacement observed before the final syscall from the accepted basename generation selected by the final syscall. REOPENED: the 122-row artifact predates the recursive-escalation namespace-replacement case and cannot prove the current delete consumer until that row is added and verified.
- [x] A consumer inventory under `/tmp` names every filesystem route, daemon producer, search/index annotator, Git consumer, and namespace mutation, and its negative search finds zero post-authorization absolute-path reopen authorities outside the one shared descriptor-bound owner. DONE: `/tmp/yolomux-descriptor-consumer-inventory.tsv` records 23 audited consumers; negative searches found zero bare `SafePathHandle` construction outside `filesystem/paths.py` and zero post-authorization absolute reopen authorities after excluding private cache/lock files.
- [ ] `python3 tools/static_build.py --check` when frontend assets are touched and an unmodified `python3 tools/check.py` both exit 0; after restart, the DONE note records active PID/CWD/HEAD and repeats the six live race probes against the authenticated implementation server with the same zero-leak oracle.

## Completion

Only the full consumer matrix can close this queue (reclassified p2.e2 on 2026-08-18; it is no longer a P0). Summarize the one shared parent, every migrated consumer, negative search, and live zero-leak evidence in `docs/DONE/`, then remove this queue; never close or split off a subset while another absolute-path reopen remains.

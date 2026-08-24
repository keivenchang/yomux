# DOIT.p1.e3.filesystem-delete-lane-split.md - Classify Bounded And Recursive Delete Work

## Queue Lineage

- Authoritative queue: this file in `/home/keivenc/dev/yolomux.dev7771-unified`, branch `integration/v0.7.12-one-ai`, HEAD `2c1d0954ca9f6017e84189dc7db45b93f833fa62` when consolidated on 2026-08-23.
- Worked source: `/tmp/yolomux-0710-integration.2203800`, branch `integration/v0.7.12-20260821`, HEAD `929085bd7b4f708633683bc921bf8f8cb81e9ddf`, dirty and stopped at the independently accepted 4/10 state below.
- Status: unfinished and paused. The later 7/10 unified copy was not accepted because the stopped audit reopened three claims; the old source queue is removed after this exact stopped state is transferred, while its implementation worktree remains untouched.

## Goal

A bounded single-entry unlink must not wait behind recursive filesystem work, while subtree deletion remains outside the bounded mutation lane and retains one authorization and receipt owner.

## 2026-08-23 - PAUSED AT USER REQUEST

Stopped at 8:39 AM PT with 4/10 checkboxes verified. Both active Codex subagents were interrupted; no task-owned check or gate process remains. Unvalidated source/test edits are preserved in the dirty integration worktree on `integration/v0.7.12-20260821` at base HEAD `929085bd7b4f708633683bc921bf8f8cb81e9ddf`; no commit, push, gate, or runtime restart was performed. The latest read-only boundary audit found the current source already carries delete-specific namespace identity, bounded exact progress paging, forced-retirement reindex, receipt progress discovery, cancel/deadline distinction, and wrong-lane rejection, but those claims still need focused verification after the interrupted edits; surrogate-escaped `PartialDeleteError.payload` remained the confirmed delete-specific defect at stop. Generic accepted-operation settlement, jobd protocol numbering, process-pool shutdown, and registry ownership proof overlap 7773's backend-lifetime focus and must not be extended here. Resume by auditing the preserved source/test diff, separating any generic-lifetime hunk, running the focused delete suites without overlapping 7773 load, and only then reconsidering the four reopened correctness boxes plus the canonical gate and 7771 runtime boxes.

## Current Evidence

- `write`, `rename`, and `mkdir` use the dedicated mutation lane.
- `delete` remains excluded because directory deletion can descend an input-sized subtree. Treating every delete as bounded would recreate interactive-lane blocking.

## 2026-08-18 - Implemented (uncommitted), Plus Two Real Defects Found Outside This Scope

IMPLEMENTED in an isolated worktree, uncommitted, based at `7cb75e3a5`; patch also exported to `/tmp/filesystem-descriptor-residuals-and-delete-lane-split.patch`. Landing still requires commit authorization, `python3 tools/check.py`, and live-server verification.

Shape as specified and singular: `io_ops.delete_path(raw_path, *, recursive=False)` is one function with one signature. Non-directory unlinks. Directory without `recursive` does exactly one `rmdir` and, on `ENOTEMPTY`/`EEXIST`, returns `{"path", "deleted": False, "kind": "dir", "pending": "subtree"}` WITHOUT ever reaching `os.scandir`. Directory with `recursive` runs today's descriptor-pinned walk byte-identically. The facade forwards `recursive` and returns early on `pending`, so `invalidate_path_policy_caches()` and `_reindex_after_mutation` fire only on terminal results. `jobd.py:442` keeps one `"delete"` arm. `complete_filesystem_operation` gained a `delete_escalation` arg; on `pending == "subtree"` it reserves `bulk`, re-produces the same descriptor with `recursive=True` under the SAME `operation_id`/`request_id`, and returns without terminalizing, which is what frees the mutation reservation. If `bulk` is full it raises the typed `service_busy`. The deadline is not extended: one receipt, one deadline. `/api/fs/delete` and the Finder JS are untouched; the browser never sends `recursive`.

MEASUREMENT CORRECTION: a non-recursive delete of a FLAT 20,000-entry directory performs `{'scandir': 1, 'unlink': 20000, 'rmdir': 1}` = 20,001 destructive syscalls. The 20,401 figure quoted earlier came from a NESTED fixture; both are correct for their own fixture and the defect class is identical.

DEFECT FOUND, OUT OF SCOPE, MUST NOT BE LOST - cooperative cancellation: `_delete_directory_contents` has no cancellation or deadline check. When an escalated operation's deadline expires, the completion terminalizes `deadline_expired` while the walk KEEPS DELETING. Deletion continues after the operation has been reported terminal, so the user is told the work stopped when it has not. This is a correctness defect, not a nicety.

DEFECT FOUND, OUT OF SCOPE, MUST NOT BE LOST - partial failure: `_delete_directory_contents` raises on the first failing entry, and because the exception propagates through the facade, `invalidate_path_policy_caches()` and the reindex NEVER FIRE. A partially deleted subtree therefore leaves the search index stale, still advertising files that no longer exist. The typed error also carries only a bare basename, not the full path or the deleted-so-far set.

VERIFIED NOT A HOLE: `delete` is absent from `FILESYSTEM_RETAINED_READ_OPERATIONS`, so it always gets a fresh `uuid` coalesce key, and jobd's inline-ready branch only fires on an already-stored product. A `pending` payload can therefore never escape through the synchronous HTTP return, so the escalation owner is genuinely singular.

STILL FLAGGED NOT MADE: deleting a symlink still reports `kind: "file"`. Introducing `kind: "symlink"` is a UI-visible payload change and was deliberately not made.

## Plan

- [x] Reproduce a one-entry unlink delayed behind held interactive/bulk work and a recursive delete whose work exceeds the mutation-lane bound. DONE red-first: `filesystem_operation_priority("delete")` returned `interactive` and the one-entry unlink probe sat `queued` while held work ran (`assert 'queued' == 'running'`). Direct measurement of one non-recursive delete on a flat 20,000-entry directory: `{'scandir': 1, 'unlink': 20000, 'rmdir': 1}` = 20,001 destructive syscalls.
- [x] Split the existing delete operation into an authorized bounded unlink case and a subtree case without adding a second path policy or route. DONE: `io_ops.delete_path(raw_path, *, recursive=False)` - one function, one signature, no second path policy and no second route. Non-directory unlinks; directory without `recursive` does exactly one `rmdir` and returns `pending: "subtree"` on ENOTEMPTY/EEXIST without ever reaching `os.scandir`; directory with `recursive` runs the existing descriptor-pinned walk byte-identically.
- [x] Route bounded unlink through the mutation lane and recursive deletion through a bulk receipt, preserving symlink safety, exact target authority, conflict behavior, Git/index invalidation, failure normalization, and UI state. DONE: `FILESYSTEM_BOUNDED_MUTATIONS` now includes `delete`; `delete` with `recursive is True` maps to `interactive`. `complete_filesystem_operation` gained a `delete_escalation` arg that reserves `bulk` and re-produces the same descriptor with `recursive=True` under the SAME `operation_id`/`request_id`, returning without terminalizing so the mutation reservation frees. Typed `service_busy` when `bulk` is full; deadline not extended. Symlink safety, exact target authority, and conflict behaviour preserved; `/api/fs/delete` and the Finder JS untouched.
- [ ] Add cross-class isolation, recursive receipt, cancellation, partial-failure, file/symlink/directory, and namespace-replacement regressions. REOPENED: blind audit reproduced namespace replacement after escalation, non-UTF-8 progress serialization failure, byte-page duplication/omission, missing forced-retirement reindex, and missing receipt progress discovery. The earlier focused runs did not cover those transitions.

## Done Criteria

- [x] The red/green tests prove a held bulk/interactive lane cannot delay bounded unlink and prove recursive deletion never occupies the mutation lane. DONE, verified first-hand by the main agent: `test_bounded_unlink_dispatches_while_recursive_deletes_hold_the_shared_lane` and its siblings run green (4 passed, 556 deselected), and the bound proof patches `os.scandir`/`os.unlink`/`os.rmdir` over 20,000 entries asserting zero scandir, zero unlink, exactly one rmdir.
- [ ] File, symlink, empty directory, nonempty directory, missing target, blocked path, namespace replacement, cancellation, and partial failure each have one typed expected result. REOPENED: the current escalation recaptures a pathname after the original descriptor probe, and permanent cancellation timeouts have no terminal bound. Exact typed outcomes remain unproved for those cases.
- [ ] The route, job descriptor, authorization owner, and operation result remain singular; focused tests and an unmodified `python3 tools/check.py` exit 0. PARTIAL: singular ownership remains verified and the stale descriptor allowlist plus raw-driver teardown regressions found by canonical v11 are corrected at 2/2 focused. V11 remains a failed first attempt: one Stats ring journey stalled under parallel load, the descriptor-owner pin was stale, the browser teardown mock bypassed the lease owner, and certification was unqualified under the gate load. A new full subject gate must exit 0 before this box closes.
- [ ] Restarted runtime evidence records bounded unlink responsiveness and honest recursive pending/terminal behavior on one unchanged HEAD. NOT DONE: no live-server verification. Requires commit authorization and a shared port, neither of which the implementing agent held.

## Completion

Record the classification boundary, red/green evidence, tests, and runtime proof in `docs/DONE/`, then remove this queue.

## Open Correctness Requirements (2026-08-18) - NOT Closed By The Lane Split

These are correctness requirements of this queue, not merely observations. Both were reproduced against the prior source and are now fixed and independently verified.

- [ ] Cooperative cancellation: `_delete_directory_contents` now receives the broker's absolute monotonic deadline through one frozen per-task control and checks it through the existing recursive delete owner; bounded one-syscall deletes remain unchanged. REOPENED: permanent transient cancellation waits indefinitely, initial handoff failure leaves the producer running, and explicit cancel is currently reported as deadline timeout without exact retained partial progress.
- [ ] Partial-failure reporting: `_delete_directory_contents` raises on the first failing entry and the exception propagates through the facade, so `invalidate_path_policy_caches()` and the reindex NEVER FIRE. A partially deleted subtree leaves the search index advertising files that no longer exist, and the typed error carries only a bare basename rather than the full path and the deleted-so-far set. REOPENED: oversized/non-UTF-8 deleted paths can fail before typed receipt delivery, forced retirement skips the facade reindex owner, and receipt framing omits the fields needed to retrieve paged progress.

Either both are delivered here, or they are split into their own queue with a recorded reason and this queue's Done Criteria are amended to say so. They must not be closed by implication from the lane split.

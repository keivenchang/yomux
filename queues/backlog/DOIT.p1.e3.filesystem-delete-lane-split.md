# DOIT.p1.e3.filesystem-delete-lane-split.md - Classify Bounded And Recursive Delete Work

## Goal

A bounded single-entry unlink must not wait behind recursive filesystem work, while subtree deletion remains outside the bounded mutation lane and retains one authorization and receipt owner.

## Current Evidence

- `write`, `rename`, and `mkdir` use the dedicated mutation lane.
- `delete` remains excluded because directory deletion can descend an input-sized subtree. Treating every delete as bounded would recreate interactive-lane blocking.

## Plan

- [ ] Reproduce a one-entry unlink delayed behind held interactive/bulk work and a recursive delete whose work exceeds the mutation-lane bound.
- [ ] Split the existing delete operation into an authorized bounded unlink case and a subtree case without adding a second path policy or route.
- [ ] Route bounded unlink through the mutation lane and recursive deletion through a bulk receipt, preserving symlink safety, exact target authority, conflict behavior, Git/index invalidation, failure normalization, and UI state.
- [ ] Add cross-class isolation, recursive receipt, cancellation, partial-failure, file/symlink/directory, and namespace-replacement regressions.

## Done Criteria

- [ ] The red/green tests prove a held bulk/interactive lane cannot delay bounded unlink and prove recursive deletion never occupies the mutation lane.
- [ ] File, symlink, empty directory, nonempty directory, missing target, blocked path, namespace replacement, cancellation, and partial failure each have one typed expected result.
- [ ] The route, job descriptor, authorization owner, and operation result remain singular; focused tests and an unmodified `python3 tools/check.py` exit 0.
- [ ] Restarted runtime evidence records bounded unlink responsiveness and honest recursive pending/terminal behavior on one unchanged HEAD.

## Completion

Record the classification boundary, red/green evidence, tests, and runtime proof in `docs/DONE/`, then remove this queue.

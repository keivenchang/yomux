# DOIT.p2.license-history-remote-finalization.md - Publish The Rewritten License History

## Goal

Finalize the already-complete PolyForm Noncommercial history rewrite on the remote only after explicit force-push authorization.

## Plan

- [ ] Reverify the local rewritten history, target remote branch, lease SHA, current tree license, key historical searches, and fresh-clone procedure without changing the remote.
- [ ] After explicit authorization, push with `--force-with-lease` against the verified remote SHA and record the result.
- [ ] Verify a fresh clone from the remote against every key license search and current-tree file identity.

## Done Criteria

- [ ] The force push is explicitly authorized, lease-protected, and followed by a fresh-clone verification; a local rewrite alone cannot close this queue.

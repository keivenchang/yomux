# DOIT.p2.e3.working-path-inference.md - Reuse Working-Directory Inference Across Surfaces

## Goal

Extend the existing transcript/file-activity working-directory inference to Finder sync, per-tab jump-to-working-path, and summary context.

## Plan

- [ ] Characterize `candidate_session_cwds` and current repo-metadata behavior for home-launched, multi-repo, missing, replaced, and resumed sessions.
- [ ] Add one ranked working-path projection consumed by Finder sync, per-tab jump, and summary context; no surface may implement a second inference rule.
- [ ] Preserve explicit user roots, authorization, stale-generation fencing, missing-path state, worktree identity, and multi-repo choice.

## Done Criteria

- [ ] The three surfaces select the same authorized path and generation for the same fixture; explicit user choice wins and stale inference cannot redirect a newer target.
- [ ] Focused backend/Node/browser tests, generated assets, the canonical gate, and restarted home-launched session journeys pass.

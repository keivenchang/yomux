# DOIT.p2.e3.yoagent-artifact-handoff.md - Add Safe Artifact Handoffs

## Goal

Choose a safe project-local path, ask one target to write an artifact, validate its existence, size, and type, and pass the path or bounded content to the next target.

## Plan

- [ ] Define project-root authority, collision handling, expected artifact contract, size/type bounds, symlink/secret rejection, timeout, cancellation, and cleanup.
- [ ] Implement through the existing YO!agent job/handoff owner and filesystem authorization parent.
- [ ] Test missing, late, replaced, symlinked, oversized, wrong-type, blocked, partial, cancelled, and valid artifacts plus restart replay.

## Done Criteria

- [ ] Only one validated project-local artifact generation is handed off, and invalid paths or bytes never reach the recipient.
- [ ] Focused security/job/browser tests, the canonical gate, and a restarted two-target handoff pass.

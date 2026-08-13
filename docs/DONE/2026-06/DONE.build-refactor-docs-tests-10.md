# Build, Refactor, Docs, and Tests

## Refactor split large files and structural guards
- Completed and removed `DOIT.refactor_split_large_files.md`. HTTP routes now use a grouped route registry; YO!agent flow moved into controller/backend/session-summary owners; share/replay/drop, editor/preview/popout, Info/YO!agent/preferences/debug, DOM action, and timing code have separate source owners; filesystem moved into a package; browser tests and JS layout tests were split; repeated CSS colors and z-index values moved to shared tokens. Verification: recursive py-compile guard now includes nested split packages, static assets were rebuilt, and final `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 44.88s`).

## DOIT index cleanup
- Removed `DOIT.00_index.md` after all descriptive DOIT queue files were archived; it was only the stale queue map and had no standalone product change.

## Archived verified done queues
- Completed and removed `DOIT.done_differ_codex_transcript.md`: multi-Codex missing-transcript data is warning-only when valid repo/file data exists; the original live state no longer reproduces, and the conditional transcript-discovery follow-up is not needed unless a future warning blocks valid data again.
- Completed and removed `DOIT.done_yoagent_no_backend.md`: YO!agent backend diagnostics are precondition-specific and CLI-backed chat already exists; exposing managed SDK transports to non-managed visible targets remains a product decision, not active queue work.
- Completed and removed `DOIT.done_autoapprove_mock_agents.md`: mock Claude/Codex yes/no approval handling is verified through detector and real-tmux mock E2E coverage; the file had no active unchecked boxes, only non-blocking cursor-placement polish noted as `[~]`.
- Verification: the latest `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 42.84s`); the archived files' audits name the existing focused tests for warning demotion, backend diagnostics, and mock-agent approval.

## Self-update restart and browser reload UX
- Completed and removed `DOIT.self_update_restart_and_ux.md`. Self-update now records a restart context for the running checkout, resolves script/module launchers, preserves argv/env needed for the active server, relaunches from `PROJECT_ROOT` with a detached helper that kills only its own PID, and documents the launcher-agnostic contract. The browser side dismisses the update toast immediately, hides the badge, starts self-update-specific ping polling after `restarting: true`, reloads when safe, suppresses the generic reload banner for the owned target, and defers with a clear Software Update toast when dirty editors or active typing would be interrupted.
- Verification: pytest covers relative script, absolute script, module launcher, stripped-env/nohup-style env, current-PID-only kill, helper detachment/stdio/log behavior, and no systemd/pkill; node tests cover immediate toast removal, reload polling, banner suppression, dirty-editor deferral, and active-typing deferral; full `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 40.25s`).

---

Completed 2026-06-19. Extracted from the 2026-06-19 daily log.

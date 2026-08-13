# Mixed and Cross-Cutting

## DOIT.debug1 / DOIT.refresh-state-unknown flaky sync and file-state fixes
- Completed and removed `DOIT.debug1.md` and `DOIT.refresh-state-unknown.md`. Finder Sync now invalidates stale in-flight root opens when the explicit tmux target changes, schedules the new explicit target even when the root path already matches, and refuses to apply an old forced tmux sync after the explicit session moved elsewhere. This fixes the 5 -> 6 -> 5 race where session 6 could finish late and leave Finder rooted at the wrong worktree.
- The high-contention Markdown scroll regression test now waits for the live bundle globals and grid before running its async editor script, so it no longer reports a misleading `ReferenceError: fileEditorItemFor is not defined` when Chrome is still exposing the page under load.
- File inspection status now preserves backend/API error detail through one shared formatter, including `payload.error`, `error`, `message`, and HTTP status before falling back to `Cannot inspect <path>`. A valid existing preview sample lookup is covered so an inspectable file does not get marked `file state unknown`.
- Verification: `python3 tools/static_build.py`, `python3 tools/static_build.py --check`, `node tests/layout_url.test.js`, focused Selenium regressions for stale sync, hover sync, existing file inspection, and long Markdown scroll (`4 passed` then `5 passed` after narrowing the tmux-only guard), 10 serial reruns of `test_sync_mode_does_not_follow_hovered_tmux_session` (`10 passed`), full `python3 tools/check.py` outside the sandbox (`CHECK PASSED in 23.87s`), and dev3 restart/smoke on port 8003 (`ping: 401 0.044468s`).

---

Completed 2026-06-13. Extracted from the 2026-06-13 daily log.

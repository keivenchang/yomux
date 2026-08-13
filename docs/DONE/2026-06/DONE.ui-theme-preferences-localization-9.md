# UI, Theme, Preferences, and Localization

## DOIT.62 topbar language popup stability
- Completed and removed `DOIT.62.md`. Passive topbar refreshes no longer detach a focused native topbar control, so the topbar language picker stays open while background transcript/session-file/SSE refreshes update the session buttons. The implementation added one pending-render state, a topbar-focused-control guard, and a blur flush so deferred renders still land after the user leaves the picker.
- `docs/specs/GUI.md` now records the topbar native-control rule, and README documents the user-visible language-picker behavior. Verification for this archive entry: `node tests/layout_url.test.js`, `python3 -m pytest tests/test_browser_layout.py::test_diff_overview_matches_actual_todo_codemirror_rows -q`, and `python3 tools/check.py` (`CHECK PASSED in 14.55s`).

---

Completed 2026-06-12. Extracted from the 2026-06-12 daily log.

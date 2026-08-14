# UI, Theme, Preferences, and Localization

## Dockview drag/drop hardening and GUI behavior spec
- Tightened Dockview and legacy drop validation so previews are suppressed when a target pane is too small, Finder/Differ is a reserved target except for a roomy bottom split, file drags use the same validator as tab drags, and directory drags still keep terminal path insertion behavior on normal panes.
- Restored right-root drops for stacked panes without stealing normal local pane-edge splits by using a wider cross-gutter tolerance for full-span root drops. Left and right root-edge previews now create full-height panes beside stacked content, while local pane-edge drops still split only the target pane.
- Added `docs/specs/GUI.md` as the durable pane/tab/Finder/Differ behavior spec and linked it from `README.md`. The spec includes current behavior, test coverage, and an audit backlog for future GUI specs/tests.
- Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py -q -k dockview`.

---

Completed 2026-06-10. Extracted from the 2026-06-10 daily log.

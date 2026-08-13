# Editor, Finder, Differ, and Files

## DOIT.38 diff overview and inactive-pane gradient archived
- Completed and removed `DOIT.38.md`. The shipped work replaces chunk tick DOM with one CodeMirror-derived diff overview gradient, hides the overview when unchanged regions are collapsed, keeps red and green row bands non-overlapping, and tests the actual `TODO.md` diff repro plus generated large replacement chunks.
- Finished the follow-up pane behavior from the same DOIT: Finder, Differ, CodeMirror, and terminal scrollbars stay neutral until their own pane/scroll surface is hovered or focused; terminal WebSocket close now roster-confirms and prunes confirmed-dead sessions immediately.
- Added the remaining inactive-pane gradient feature: an Appearance toggle, default-on setting, localized Preferences row, slot-based direction helper, setting-gated CSS override over the existing flat dim fallback, and regression tests for the direction map, disabled state, and no-focus fallback.

---

Completed 2026-06-05. Extracted from the 2026-06-05 daily log.

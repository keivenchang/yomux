# UI, Theme, Preferences, and Localization

## DOIT.37 GUI consistency batch archived
- Completed the DOIT.37 cleanup/refactor batch and removed `DOIT.37.md`. The shipped work covers timing/token consolidation, fetch/storage helpers, file-state helpers, split-pane tokens, shared button bases, transcript normalization, duplicate-function linting, typed payload schemas, compact notifications, and TLS redirect cleanup.
- Brought Finder, Differ, and Modified-files closer to one behavior model: shared date-cycle vocabulary (`None`, `Date`, `Ago`), shared sort vocabulary (`A-Z`, `Z-A`, `new`, `old`), stable status/date placement, aligned tree-row icon columns, and Finder/Differ toolbar controls using the same visual rules.
- Tightened git editor affordances: blame and diff controls now appear together only when useful git history exists; untracked/no-history files such as `DOIT.37.md` do not show misleading blame/diff actions; same-ref/no-diff views render the file normally instead of a dead "No diff" pane.
- Fixed pane-focus and editor regressions from the live GUI audit: active green borders are stronger in both themes, dark inactive panes are lighter without changing the accepted light-mode dim, tab back-history collapses A→B→A→B into A→B, and CodeMirror Word Wrap reconfigures live without blanking the editor.

---

Completed 2026-06-05. Extracted from the 2026-06-05 daily log.

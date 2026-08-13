# Mixed and Cross-Cutting

## DOIT.40 correction: inactive-pane gradient removed
- Removed the inactive-pane directional gradient after live review showed it still did not read as a gradient. The app keeps the flat inactive-pane dim, inactive-pane opacity slider, and stable visual-active pane state, but the gradient setting, Preferences row, CSS gradient tokens/rule, body class, JS direction helper, and gradient-specific tests are gone. This is deferred until it can be debugged with an explicit overlay visual. Verification: standard gate green.

---

Completed 2026-06-06. Extracted from the 2026-06-06 daily log.

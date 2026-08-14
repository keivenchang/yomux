# Build, Refactor, Docs, and Tests

## Build / structural
- `10_topbar_menus.css` had a TRUNCATED `.notify-toggle.active {` rule whose body was split into the next partial — it only rebalanced by accident in the bundle (DOIT.12 B1). Completed the rule, removed the orphaned body, and added a `check_css_braces()` build step that fails on any brace-unbalanced CSS partial (with a regression test). The check immediately caught the latent split.

---

Completed 2026-06-03. Extracted from the 2026-06-03 daily log.

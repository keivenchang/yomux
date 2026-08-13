# UI, Theme, Preferences, and Localization

## DOIT.41 choosable "Active color" preference
- Centralized the active/focused accent (previously one named-token salad plus ~59 raw `#76b900`/`rgba(118,185,0,α)` literals across six CSS files) into a small `--active-accent` token set (`accent`/`rgb`/`bright`/`text` + derived `dim`/`soft`) and an `--active-control-*` layer, with the raw literals swept to route through it; Green stays the exact default so the refactor is a visual no-op. Added Preferences → Appearance → Active color (`green|blue|orange|yellow|purple|white`) as a live-applied `select`: `applyActiveColor` writes the per-theme accent vars on the active theme and re-applies on theme switch, with i18n across all 13 locales + en-XA. The YOLO marker stays exactly green (not routed through the accent) and editor syntax greens stay out of scope. White is the one preset whose light-mode ring (`#9aa5b3`) diverges from its fill (`#dfe5ec`) so the focused pane still reads on a white panel. Shipped on `main` as `9b0f5d5 Recolor active chrome and polish preferences`. (The standalone `doit-41-active-color` worktree branch was a less-complete parallel take — its Phase-1 literal sweep was unfinished — and is superseded by the mainline version.) Verification beyond the standard gate: the hardened active-accent browser tests.

## DOIT.38 Q2 dark active pane tab-container hover cue
- Dark mode now gives the active/hover-selected pane tab container a lighter token-derived background while light mode keeps the existing strip color. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_active_pane_tab_container_lightens_in_dark_only -q`.

---

Completed 2026-06-06. Extracted from the 2026-06-06 daily log.

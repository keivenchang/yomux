# Mixed and Cross-Cutting

## DOIT.58 pinned tab cross-pane drops land first
- Completed and removed `DOIT.58.md`. Dragging a pinned tab into another pane now routes through one pinned cross-pane move intent and commits via `moveSessionToSlot(..., insertIndex=0)`, so the pinned tab becomes the first tab in the target pane instead of landing behind non-pinned tabs or splitting the pane. Invalid native Dockview tab/content previews for that gesture are hidden while preserving the final drop.
- Verification for this archive entry was focused rather than the full standard gate: `python3 tools/static_build.py --check`, `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `python3 -m pytest tests/test_browser_layout.py -k 'pinned_tab_dragged_to_other_pane_lands_first or non_pinned_tab_cannot_drop_between_pinned_tabs or pinned_tabs_render_first_after_pin_toggle or drag_reorders_two_pinned_tabs or first_pinned_tab_drags_after_second_pinned_tab' -q` (5 passed), and `git diff --check`.

---

Completed 2026-06-11. Extracted from the 2026-06-11 daily log.

# DOIT.p2.e3.app-menu-and-finder-metadata.md - Finish Menu Controls And Git-Aware Finder Rows

## Goal

Complete the remaining app-menu actions and show cached repository identity in Finder without one Git process per hover.

## Plan

- [ ] Inventory panel-tab visibility, inactive-tab tray/show-all, remaining tmux YOLO controls, and pane peek/reply actions; route each through the existing shared action and accessibility builders.
- [ ] Add repo name, branch, dirty/ahead/behind, and remote/GitHub URL to repository/root rows through the existing cached server metadata owner.
- [ ] Preserve menu placement, toggle semantics, keyboard/touch behavior, hidden-tab state, stale/error rendering, and Finder/Differ/Tabber shared-row parity.

## Done Criteria

- [ ] Every menu gap has one action owner and accurate title/aria/pressed/disabled state; peek/reply exists only once across menu and session surfaces.
- [ ] Repeated hover/refresh on unchanged rows causes zero Git spawns, while source-generation changes update the exact row once.
- [ ] Focused Node/browser/backend tests, generated assets, the canonical gate, and restarted menu/Finder journeys pass.

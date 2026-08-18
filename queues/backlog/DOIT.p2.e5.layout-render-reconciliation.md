# DOIT.p2.e5.layout-render-reconciliation.md - Reconcile Layout And Edge-Pinned Tabs

## Goal

Move the grid, topbar, tab strips, virtual tabs, and pane chrome toward one keyed layout renderer; express Finder, Differ, and Tabber through one declarative pinned-edge model; and close the remaining pinned-state persistence and keyboard-only layout-operation gaps without adding parallel layout owners.

## Plan

- [ ] Freeze current state ownership, DOM identity, URL restoration, adoption, hidden-by-user, minimum-size, drag/drop, focus, and teardown behavior with characterization tests.
- [ ] Define one keyed renderer and one pinned-edge descriptor covering placement, hidden state, minimum size, adoption, and inactive-tab behavior without parallel legacy copies.
- [ ] Migrate bounded surfaces incrementally, preserving Dockview/xterm/CodeMirror identity and rollback after each slice.
- [ ] Define one pinned-tab persistence contract that chooses shareable URL state or a server-side preference, migrates the current `yolomux.pinnedTabs.v1` browser-local state, and preserves pin order, identity changes, reload, reset, stale-state rejection, and multi-tab convergence.
- [ ] Add keyboard-only tab reorder and pane/root-edge split operations through the existing move/split authority, with visible focus, announced destination, cancellation, impossible-target refusal, and no pointer-only duplicate path.

## Done Criteria

- [ ] Negative searches find one layout reconciliation owner and one pinned-edge model; every migrated surface preserves URL, focus, selection, scroll, drag/drop, accessibility, and lifecycle behavior.
- [ ] Pinned state survives the selected share/reset/multi-tab lifecycle, and keyboard-only tests reorder within a pane, move across panes, split every eligible edge, cancel safely, and refuse undersized or incompatible targets.
- [ ] Focused Node/browser geometry tests, generated assets, the canonical gate, and restarted exact-layout browser journeys pass.

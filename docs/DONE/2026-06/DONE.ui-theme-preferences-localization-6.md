# UI, Theme, Preferences, and Localization

## DOIT.54 Dockview pane polish and file-drag parity
- Dockview pane separators now use the shared skinny sash line instead of a fat group border; the line thickens only on hover/drag. Inactive Dockview tabs inherit the pane tab-strip background, and Dockview/root/tab/file drag previews use the configurable pane separator color.
- Dockview now lays out to the real host size, renders YOLOmux pane controls in the Dockview header row, shows a full-span root preview before root-edge drops, preserves the root-docked Finder width when tabs move between content panes, and restores Finder/Differ file drags into panes with the same dashed split preview/open-in-editor behavior as the legacy layout.
- Follow-up: default pane spacing is now 3px; the active Dockview tab container is slightly brighter; Dockview tab hover details are restored; root top/bottom tab-drag previews no longer span into the docked Finder/Differ column; and Dockview group padding reserves pane-spacing width so the active ring does not paint over terminal/xterm content.
- Follow-up after rebase: dragging a tab over another Dockview tab now shows a 24px dashed insertion box between tabs instead of a half-tab overlay on top of the target tab; the shared separator hover line is now 5px; pane-edge drops split only the target pane while root-edge/cross-gutter drops still create full-span panes.
- Follow-up after live resize review: Dockview pane-content edge drops now route through YOLOmux `splitSessionAtSlot`, so a second same-axis split preserves `1/2 | 1/4 | 1/4` instead of flattening into equal thirds. Dockview Finder/Differ sash adoption now accepts the new root Finder percentage while preserving nested content split percentages, so moving the Finder/Differ column resizes Pane1/Pane2 proportionally.
- Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py -q -k dockview`.

---

Completed 2026-06-09. Extracted from the 2026-06-09 daily log.

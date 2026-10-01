# User-interface contract

This page keeps the durable user-visible rules. The selected backend's capability catalog determines which product areas are available. Exact geometry, event ordering, and regression assertions belong in the current browser and Node tests.

## Layout and navigation

- Keep panes and tabs usable across desktop, narrow-window, and touch layouts. Preserve the user's active selection and restored layout when switching between surfaces.
- When a tmux session exits outside YOmux, remove its tab and reconcile its pane within five seconds while preserving the saved layout placeholder.
- When a tmux session is renamed outside YOmux, preserve its tab and pane under the new name even if metadata or activity reaches the browser before the roster event.
- A control's label, tooltip, accessibility state, and action must agree. Menus and popovers must work with pointer, keyboard, and focus changes.
- Shared browser code does not imply that every backend supports every route. Hide or disable features according to `/api/capabilities`.

## Terminal and status

- Terminal input, selection, copy, paste, links, and touch gestures must preserve the terminal application's own behavior where it owns the interaction.
- Agent status comes from the selected pane or window. Do not create duplicate rows or carry activity across a reused tmux target.
- Notifications identify their target without stealing focus. Repeated notices for the same target should update one visible item.

## Finder, Differ, and editor

- Finder and Differ share tree-row, sorting, status, selection, and toolbar behavior.
- If a restored Finder directory expansion fails to load, stop its spinner, show the listing error, and remove that expansion from saved layout state. Retry uses the shared directory-listing path.
- Read-only users can open a repository's Repo Diff tab directly, expand commits and the Dirty row, and view file comparisons without editing. The Dirty summary shares the commit-list grid tracks so each visible field aligns vertically with commit rows. General filesystem actions retain their backend permissions.
- Repo Diff commit rows use a 20 CSS-pixel minimum height, no vertical padding, and a 1:1 line height. Metadata columns size to their content to preserve room for refs and subjects. Dates and file-change summaries use 80% of row text size; compact signature codes have plain-language tooltips and accessible labels.
- Show file content as soon as the bounded read completes; optional Git decoration may arrive later. Keep authorization and filesystem failures visible instead of painting stale or replacement content.
- Text editing stays distinct from preview. Markdown ViewEdit uses the supported ProseMirror path; if parsing fails, keep the source preview read-only and show the error. Saving, mode changes, close, and blur must not discard pending edits.
- Uploads and pasted images use the active editor's file-relative path and the original selection or caret captured for that operation.

## Statistics and diagnostics

- Render only values supplied by the active backend. Keep loading, pending, unavailable, failed, and valid no-data states distinct.
- Range and resolution follow server capabilities and accepted snapshots. The browser does not aggregate data or fabricate missing values.
- System -> Daemons exposes the shared capability-backed Range and Resolution controls; changes apply to the shared YO!stats history selection.
- Graph hover lines, selection highlights, and drag zoom use the shared interaction owner. The chart under the pointer owns its gray vertical line and zoom domain throughout the gesture.
- Keep Daemons Load out of Graphs; show runtime-owner occupancy only in System -> Daemons, with process RSS in its separate memory chart.
- Go process history distinguishes host, boot, PID, process start, and owner so concurrent instances and restarted processes do not merge. Labels and details show PID, port, executable, and parent PID when available. Keep older samples without recoverable process identity visible as legacy; do not assign them a current PID. Process CPU and elapsed collector occupancy remain separate measurements.
- Daemons charts use cumulative filled areas with distinct color/pattern pairs and fully visible, wrapping legends. Repeated colors use vertical lines or dots that remain visible in the legend; do not use horizontal or diagonal hatch marks. Cards grow with their legends instead of scrolling or clipping them. Nested occupancy measurements must not be presented as additional independent work.
- Use bright, fully opaque fills for CPU, System memory, Daemons Load, Daemon memory, and Go memory detail areas. Each plotted area and its legend swatch must use identical base colors and infill marks, with no opacity reduction on the area, swatch, pattern, or ancestor. Paint comparison lines above those fills. Keep each same-named `yomux-go` host CPU and RSS process separate, with its PID in the legend label.
- Go memory diagnostics show runtime memory classes and sampled retained allocation stacks as cumulative filled areas, separately from process RSS. Size both plots to fill the available card width with a readable height, and keep their wrapping legends visible. Label memory chart axes in bytes and capture time, and make each complete sample's class values available on hover. Plot individual stack history only when its stack key appears in every retained capture; include a short key in each legend label and the full stack in its title because the backend retains only twelve ranked stacks per capture. Rank individual stacks and show capture age, process identity, units, unavailable reasons, and retained versus cumulative allocation bytes; never label sampled stacks as exact struct sizes or claim they account for native memory. Heap capture is bounded and restricted to administrators.
- Diagnostics must identify unavailable backend data instead of inventing Python service rows for Go.

## Verification

- Reproduce the reported URL, layout, theme, or pointer state before changing a visual rule.
- Use real Chromium for geometry, focus, keyboard, touch, and drag behavior. Assert the interaction state as well as the final layout when an intermediate preview or held state matters.
- Rebuild `static/` after frontend source edits. A live-port fix requires a restart and proof that the server serves the rebuilt bundle.
- Keep this page short. Move exact values, implementation details, and test matrices into source comments or focused tests so this document does not become a second copy of the code.

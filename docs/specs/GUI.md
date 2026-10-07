# User-interface contract

This page keeps the durable user-visible rules. The selected backend's capability catalog determines which product areas are available. Exact geometry, event ordering, and regression assertions belong in the current browser and Node tests.

## Layout and navigation

- Keep panes and tabs usable across desktop, narrow-window, and touch layouts. Preserve the user's active selection and restored layout when switching between surfaces.
- When a tmux session exits outside YOmux, remove its tab and reconcile its pane within five seconds while preserving the saved layout placeholder.
- Session rename accepts one submission while its reply is pending. The submit button visibly disables, the existing tab displays the proposed name, and rejection restores the original name and re-enables submission. Accepted renames preserve the new tab, active pane, and roster when an older roster arrives.
- When a tmux session is renamed outside YOmux, preserve its tab and pane under the new name even if metadata or activity reaches the browser before the roster event.
- A control's label, tooltip, accessibility state, and action must agree. Menus and popovers must work with pointer, keyboard, and focus changes.
- Shared browser code does not imply that every backend supports every route. Hide or disable features according to `/api/capabilities`.

## Terminal and status

- Terminal input, selection, copy, paste, links, and touch gestures must preserve the terminal application's own behavior where it owns the interaction. Keyboard delivery must remain available during resize, attach, and secondary pane updates. Network ping is distinct from native-key-to-visible-character latency.
- Terminal URL and file-reference discovery stays within the visible viewport and at most eight physical rows per wrapped token. Redraw scheduling does no buffer scans, and a token that crosses the row limit remains unlinked.
- Agent status comes from the selected pane or window. Do not create duplicate rows or carry activity across a reused tmux target.
- A visible Codex `Working (... esc to interrupt)` footer is the current state even when the idle composer and older command output remain on screen.
- Notifications identify their target without stealing focus. Repeated notices for the same target should update one visible item.

## Finder, Differ, and editor

- Finder and Differ share tree-row, sorting, status, selection, and toolbar behavior. Each surface keeps its own session-file payload; Finder metadata must not change Differ or Repo Diff rows.
- Selecting a terminal tab immediately clears the previous tab highlight and selects the new tab in a pending color halfway between the current theme's inactive and active tab colors. Show and focus its terminal immediately so keyboard input can start without waiting for Finder or Differ work. Apply the normal active theme color after xterm consumes output queued at activation, renders the current viewport, and crosses a paint opportunity; continuous output does not keep the tab pending. Session-side synchronization and file-surface activation run after the terminal has had a paint opportunity; superseded updates stop before rendering. Hidden Differ retains the latest payload and renders it when selected, with visible tree updates yielding to input.
- Authoritative tree replacements retire obsolete rows, placeholders, and child wrappers before replacement rows become visible. Keep expanded children and failed-listing errors when their directory remains present. Repository stream chunks preserve arrival order and leave unchanged previously published rows and controls intact. A delayed directory expansion cannot attach children after that row becomes a file.
- Collapsing a Finder directory preserves its nested expansion state across reload. Reopening the parent restores those descendants through the shared listing and expansion lifecycle.
- Finder refreshes and asynchronous expansion preserve the viewport. Native scrolling owns the viewport throughout a gesture and its settling motion; an older refresh cannot rewind it or transfer its saved position to a replacement tree.
- Finder listing and metadata refreshes preserve all rows, automatic expansion, selection, and column geometry. Metadata updates must finish for the current mode and session even when the user changes either during a refresh; an older transaction cannot replace the current target.
- If a restored Finder directory expansion fails to load, stop its spinner, show the listing error, and remove that expansion from saved layout state. Retry uses the shared directory-listing path.
- Read-only users can open a repository's Repo Diff tab directly, expand commits and the Dirty row, and view file comparisons without editing. The collapsed Dirty summary stays on one line from first paint and keeps its fields aligned with commit rows while history statistics load. General filesystem actions retain their backend permissions.
- Repo Diff commit rows use a 20 CSS-pixel minimum height, no vertical padding, and a 1:1 line height. Metadata columns size to their content to preserve room for refs and subjects. Dates and file-change summaries use 80% of row text size; compact signature codes have plain-language tooltips and accessible labels.
- Show file content as soon as the bounded read completes; optional Git decoration may arrive later. Keep authorization and filesystem failures visible instead of painting stale or replacement content.
- Text editing stays distinct from preview. Markdown ViewEdit uses the supported ProseMirror path; if parsing fails, keep the source preview read-only and show the error. Saving, mode changes, close, and blur must not discard pending edits.
- In file-editor Markdown previews, relative and root-relative file links open through the YOmux editor from both clicks and link context menus; explicit external links open in a browser tab.
- Zoomed images and diagrams can be panned by dragging their content; the pan gesture continues across pointer movement outside the viewport and ends on release or cancellation.
- Uploads and pasted images use the active editor's file-relative path and the original selection or caret captured for that operation.

## Statistics and diagnostics

- Render only values supplied by the active backend. Keep loading, pending, unavailable, failed, and valid no-data states distinct.
- Range and resolution follow server capabilities and accepted snapshots. The browser does not aggregate data or fabricate missing values.
- YO!stats cost reports show the top 16 models and agents and up to 32 pricing evidence rows; aggregate totals still include omitted details, and omission counts remain visible.
- Explicit Retry starts a fresh full history request immediately. Keep loading, pending, ready, and error states distinct; silent history requests must end in a visible error, and a later valid response can restore ready state.
- System -> Daemons exposes the shared capability-backed Range and Resolution controls; changes apply to the shared YO!stats history selection.
- Graph hover lines, selection highlights, and drag zoom use the shared interaction owner. The chart under the pointer owns its gray vertical line and zoom domain throughout the gesture.
- Graphs CPU shows cumulative host-process CPU with total System CPU; keep per-process daemon CPU lines out of that chart. System -> Daemons shows Go process CPU as a comparison line alongside runtime-owner occupancy, never as another stacked component; process RSS stays in its separate memory chart.
- Graphs places the hidden-by-default Space used % chart immediately after GPU memory. When enabled, it plots one 0–100% line for each filesystem listed by the latest `df -kP` sample, sampled every 5 minutes, and labels each line with its mountpoint; history stays visible for those paths, while no longer listed paths disappear from the chart. Missing or inaccessible capacity samples remain gaps in that filesystem's line.
- Place the Refill last 24 hours action after all Graphs chart cards.
- Go process history distinguishes host, boot, PID, process start, and owner so concurrent instances and restarted processes do not merge. Labels and details show PID, port, executable, and parent PID when available. Keep older samples without recoverable process identity visible as legacy; do not assign them a current PID. Process CPU and elapsed collector occupancy remain separate measurements.
- Daemons charts use cumulative filled areas with distinct color/pattern pairs and fully visible, wrapping legends. Repeated colors use vertical lines or dots that remain visible in the legend; do not use horizontal or diagonal hatch marks. Cards grow with their legends instead of scrolling or clipping them. Nested occupancy measurements must not be presented as additional independent work.
- Use bright, fully opaque fills for CPU, System memory, Daemons Load, Daemon memory, and Go memory detail areas. Each plotted area and its legend swatch must use identical base colors and infill marks, with no opacity reduction on the area, swatch, pattern, or ancestor. Paint comparison lines above those fills. Keep each same-named `yomux-go` host CPU and RSS process separate, with its PID in the legend label.
- Go memory diagnostics show runtime memory classes and sampled retained allocation stacks as cumulative filled areas, separately from process RSS. Size both plots to fill the available card width with a readable height, and keep their wrapping legends visible. Label memory chart axes in bytes and capture time, and make each complete sample's class values available on hover. Plot individual stack history only when its stack key appears in every retained capture; include a short key in each legend label and the full stack in its title because the backend retains only twelve ranked stacks per capture. Rank individual stacks and show capture age, process identity, units, unavailable reasons, and retained versus cumulative allocation bytes; never label sampled stacks as exact struct sizes or claim they account for native memory. Heap capture is bounded and restricted to administrators.
- Diagnostics must identify unavailable backend data instead of inventing Python service rows for Go.

- Agent stats provides one administrator-only "Refill last 24 hours" control beside its chart controls. Confirm with "Refill the last 24 hours?", the CPU/slowness/deleted-history warning, and Cancel/Refill actions. Freeze the preceding 24-hour window at acceptance; continue collecting new activity and preserve recorded Run/Ask/Idle history.
- During refill, disable its button and replace session/model token charts and usage/cost with "Refilling agent history… Please wait." Show actual reading/rebuilding phases without percentages. Leaving, reopening, reloading, and other tabs attach to the same host job. Completion reports success or names unavailable history sources; failed scans leave previous data intact and offer Try again through the same confirmation. Preserve the lightweight chart Retry action.

## Verification

- Reproduce the reported URL, layout, theme, or pointer state before changing a visual rule.
- Use real Chromium for geometry, focus, keyboard, touch, and drag behavior. Assert the interaction state as well as the final layout when an intermediate preview or held state matters.
- Rebuild `static/` after frontend source edits. A live-port fix requires a restart and proof that the server serves the rebuilt bundle.
- Keep this page short. Move exact values, implementation details, and test matrices into source comments or focused tests so this document does not become a second copy of the code.
- Markdown ViewEditor preserves standalone raw HTML images inside and outside details blocks. Split scrolling uses source anchors across decoded tall images and details expansion; headings retain their original source lines.

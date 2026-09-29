# User-interface contract

This page keeps the durable user-visible rules. The selected backend's capability catalog determines which product areas are available. Exact geometry, event ordering, and regression assertions belong in the current browser and Node tests.

## Layout and navigation

- Keep panes and tabs usable across desktop, narrow-window, and touch layouts. Preserve the user's active selection and restored layout when switching between surfaces.
- When a tmux session exits outside YOLOmux, remove its tab and reconcile its pane within five seconds while preserving the saved layout placeholder.
- When a tmux session is renamed outside YOLOmux, preserve its tab and pane under the new name even if metadata or activity reaches the browser before the roster event.
- A control's label, tooltip, accessibility state, and action must agree. Menus and popovers must work with pointer, keyboard, and focus changes.
- Shared browser code does not imply that every backend supports every route. Hide or disable features according to `/api/capabilities`.

## Terminal and status

- Terminal input, selection, copy, paste, links, and touch gestures must preserve the terminal application's own behavior where it owns the interaction.
- Agent status comes from the selected pane or window. Do not create duplicate rows or carry activity across a reused tmux target.
- Notifications identify their target without stealing focus. Repeated notices for the same target should update one visible item.

## Finder, Differ, and editor

- Finder and Differ share tree-row, sorting, status, selection, and toolbar behavior.
- Read-only users can open a repository's Repo Diff tab directly, expand commits and the Dirty row, and view file comparisons without editing. General filesystem actions retain their backend permissions.
- Show file content as soon as the bounded read completes; optional Git decoration may arrive later. Keep authorization and filesystem failures visible instead of painting stale or replacement content.
- Text editing stays distinct from preview. Markdown ViewEdit uses the supported ProseMirror path; if parsing fails, keep the source preview read-only and show the error. Saving, mode changes, close, and blur must not discard pending edits.
- Uploads and pasted images use the active editor's file-relative path and the original selection or caret captured for that operation.

## Statistics and diagnostics

- Render only values supplied by the active backend. Keep loading, pending, unavailable, failed, and valid no-data states distinct.
- Range and resolution follow server capabilities and accepted snapshots. The browser does not aggregate data or fabricate missing values.
- Diagnostics must identify unavailable backend data instead of inventing Python service rows for Go.

## Verification

- Reproduce the reported URL, layout, theme, or pointer state before changing a visual rule.
- Use real Chromium for geometry, focus, keyboard, touch, and drag behavior. Assert the interaction state as well as the final layout when an intermediate preview or held state matters.
- Rebuild `static/` after frontend source edits. A live-port fix requires a restart and proof that the server serves the rebuilt bundle.
- Keep this page short. Move exact values, implementation details, and test matrices into source comments or focused tests so this document does not become a second copy of the code.

# Editor, Finder, Differ, and Files

## DOIT Search formatting and editable Markdown tasks
- Quick Search now normalizes a trailing punctuation query such as `DOIT:` to `DOIT` for file search, ranks basename matches ahead of path-only fuzzy hits, and renders image hits in Popular IDE-style form such as `[Image #1] '/home/keivenc/yolomux.dev1/20260609-001.png'`.
- Markdown Preview now wires rendered task-list checkboxes back to the open file model: clicking a `- [ ]` / `- [x]` checkbox toggles the Markdown source, refreshes linked preview panes/popouts, updates CodeMirror documents, and uses the existing dirty/autosave path.
- Verification: standard gate green.

## DOIT.53 Differ selection/delete and non-git diff counts
- Unified Differ file-row selection and file context menus onto the Finder shared parent: single-click, shift-click, and cmd/ctrl-click now use `updateFileTreeSelectionFromClick`, right-click file rows use `showFileTreeContextMenu`, and the shared delete path refreshes session-files so deleted Differ rows disappear immediately. Removed the Differ-only single selected-path state and safe-only file context menu.
- Added `diff_tracked` to session-files payloads. Counts from real `git numstat` stay green and contribute to repo totals; raw full-file counts for untracked/no-repo files stay visible per row but render neutral and are excluded from added/removed totals. Transcript-touched files outside any git repo now appear under the `Outside repo` section instead of being silently dropped.
- Verification beyond the standard gate: `python3 -m pytest tests/test_session_files.py -q`. Full `python3 -m pytest tests -n auto -q` ran 454 passing tests with two failures: the YO!agent rolling-summary test passed when rerun serially, and the remaining browser failure is the pre-existing dirty-`TODO.md` diff-overview row-offset drift.

## DOIT.52 tab-drag and Finder sync perf
- Fixed the slow tab-drop path by moving layout render decisions into one shared scheduler (`requestLayoutRender` / `performLayoutRender`) and replacing the old `pendingPanelsRender` boolean with a structured `pendingLayoutRender` request. Same-shape drag drops now keep the cheap `syncActivePanelsInPlace` + tab-strip path, while metadata-driven `renderPanels()` calls during drag still record an explicit forced-full render request.
- Reduced the Finder sync re-render floor by adding one sync-plan parent (`fileExplorerSyncPlanKey`, `fileExplorerSyncPlanAlreadyApplied`, `markFileExplorerSyncPlanApplied`) and skipping automatic repeated root+expand-path plans after they have already applied. Explicit Sync still forces a re-apply, and manual expand/collapse/root-mode changes reset the applied key.
- Aligned Finder refresh defaults by changing the JS fallback to `5` seconds and migrating exact stale saved poll defaults (`file_explorer.refresh_seconds: 1`, old round performance poll values) to the current defaults without changing nearby custom values. Did not add the optional B3 mtime sweep because `/api/fs/info` is not a cheap stat-only endpoint today; adding it to every watched directory poll would likely make the path worse unless a dedicated stat endpoint is introduced.
- Verification beyond the standard gate: `python3 -m pytest tests/test_settings.py -q`. Full `python3 -m pytest tests -n auto -q` ran 454 passing tests and one unrelated failure in `test_diff_overview_matches_actual_todo_codemirror_rows` because the already-dirty `TODO.md` changed the hard-coded CodeMirror row offset from `45531` to `45544`.

## DOIT.51 blame popover, quick-open dedupe, worktree identity, risk labels, and drag timing
- Inline blame now uses a styled hover popover with author, absolute date, sha, summary, and optional commit body instead of a native tooltip; blame payloads include a deduped commit-body map and skip uncommitted/empty bodies.
- Quick Search collapses an already-open file to one Tabs row with edit/preview view chips, the editor navigation stack is capped at 50 entries, linked git worktrees are identified separately from their parent checkout in session metadata, YOLO risk labels normalize to the canonical display set, and tab-drag timing instrumentation is available behind the opt-in `yolomux.debugDragTiming` local-storage flag.
- README documents the `0.0.0.0` default host behavior, the local-only `--host 127.0.0.1` opt-in, and the canonical YOLO risk-label vocabulary; CLI help also reflects the host default.
- Verification beyond the standard gate: `python3 -m pytest tests/test_filesystem.py tests/test_metadata.py tests/test_yolo_rules.py -q`.

---

Completed 2026-06-09. Extracted from the 2026-06-09 daily log.

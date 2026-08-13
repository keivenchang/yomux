# Editor, Finder, Differ, and Files

## DOIT.45 editor diff + FROM/TO ref picker on clean files
- Fixed the recurring bug where pressing `Differ` on a git-tracked file with history but a clean working tree showed no FROM/TO sha picker. The clean file's default HEAD-vs-working diff is empty, so `openFileDiffAvailable()` was false and `renderFileEditorPanel` force-exited diff mode back to edit, hiding `.file-editor-diff-ref-panel`. Extracted the force-exit into `diffModeShouldFallBackToEdit(path, state, item)` and gated it on `!fileStateHasUsefulGitHistory(state)`: a file with useful history now stays in diff mode so the picker is reachable and the user can compare arbitrary refs, while untracked / creation-only / outside-repo files still fall back to the editor. Added a JS guard for clean-with-history, no-history, and real-diff states plus a source-grep guard requiring the useful-history clause.

## Finder/Differ quick roots and summary wording
- Kept the Finder quick-root buttons `~`, `/*`, and `/tmp` present whether Sync is pressed or not, with a browser regression that checks the buttons in both Sync and fixed-root states. The Differ comparison summary now says `{repo count} repos, {file count} files changed in '{session}'` (for example `1 repo, 84 files changed in '5'`) while keeping the existing `+/-/file-count` totals on the right; locale catalogs include the new repo-count phrase.

## DOIT.39 correction: Finder Sync and diff toolbar cleanup
- Finder Sync now treats the focused tmux session's git root as the fallback working root when changed-file payloads have not arrived yet, ignores stale same-session payloads whose repo set no longer overlaps the focused git root, then expands and highlights affected repo roots/touched dirs once the current session-files payload lands. The Finder root toggle now displays `No Sync` or `Sync`, not `Root`. Finder diff mode now has only one visible Session select, Sort select, date-mode button, and Reload button, and identical session labels no longer render as `5 5`. Long changed-file rows now force filenames to ellipsize before the agent/diff/status/date metadata clips. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_legacy_changes_url_opens_finder_diff_mode tests/test_browser_layout.py::test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs tests/test_browser_layout.py::test_sync_mode_empty_session_opens_home_not_stale_payload tests/test_browser_layout.py::test_finder_diff_mode_toggle_fills_pane tests/test_browser_layout.py::test_differ_long_filename_ellipsizes_before_date_column -q`.

## DOIT.39 Phase 3.3 Finder Sync empty session home fallback
- Finder Sync now treats a tmux session with no cwd as an explicit home-root case instead of leaving the previous Finder root or trusting a stale session-files payload. The Sync plan keeps session identity even when `current_path` is empty, falls back to `homePath`, and clears stale affected-repo highlights. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs tests/test_browser_layout.py::test_sync_mode_empty_session_opens_home_not_stale_payload -q`.

## DOIT.39 Phase 3.2 Finder Sync affected-repo highlight
- Finder Sync now highlights affected repo root rows with a stronger token-routed green tint and changed descendant directories with a lighter tint, derived from the same session-files affected set used for Sync expansion. The classes clear when Sync is turned off or the payload no longer matches the active session. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs -q`.

## DOIT.39 Phase 3.1 Finder Sync multi-dir expand
- Finder Sync now computes affected directories from the session-files `repos[]` plus changed-file parent dirs, roots at their nearest common ancestor, expands each affected path, and falls back to the focused repo instead of `/` when a scattered set crosses above `~`. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs -q`.

## DOIT.39 Phase 2 standalone Differ retired
- Removed the standalone Differ/Changes tab wrapper, tab type, menu/palette listing, drag/split references, duplicate session-files state, and standalone-only CSS/locale key. Legacy `changes` / `__changes__` URL tokens now resolve to the Finder pane and preselect diff mode, including virtual-only old bookmarks. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_finder_diff_mode_toggle_fills_pane tests/test_browser_layout.py::test_legacy_changes_url_opens_finder_diff_mode -q`.

## DOIT.39 Phase 1 Finder `Differ` mode
- Finder now has a real `files` / `diff` mode flag instead of a hidden stacked Modified-files section. The `Differ` button is prominent, persists the mode, fills the pane with the full Differ chrome in diff mode, hides file-creation controls there, keeps file mode as a full-height tree, and switches changed-directory context-menu actions back to Finder mode before expanding. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_finder_path_is_first_and_readable_in_wrapped_toolbar tests/test_browser_layout.py::test_finder_diff_mode_toggle_fills_pane -q`.

## DOIT.38 Q5 Differ long-name date clipping
- Differ/Finder shared file rows now keep the filename as the explicit flex grow/shrink ellipsis column when agent metadata is present, and the agent slot no longer uses an auto margin that can push diff/status/date columns out of view. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_differ_long_filename_ellipsizes_before_date_column tests/test_browser_layout.py::test_finder_differ_status_badges_share_one_column -q` and.

## DOIT.38 Q4 blank README diff regression
- Diff panes now wait for the requested diff payload and build CodeMirror in one serialized pass instead of flashing edit mode and racing a re-entrant rebuild. The diff build also catches construction failures and falls back to raw read-only text instead of leaving an empty pane, and explicit FROM/TO refs are pinned for later refreshes so Modified-files diffs do not silently fall back to default refs. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_readme_diff_waits_for_payload_before_building_codemirror -q`, the Q1-Q4 focused browser test set.

## DOIT.38 Q3 dark diff added-line fill consistency
- Dark editor diff lines now use opaque red/green fills over the editor background, and the diff-scoped active-line rule keeps the cursor line the same shade as adjacent changed lines. Verified by first reproducing the mismatch in `tests/test_browser_layout.py::test_diff_added_active_line_uses_same_fill_as_neighbor`, then passing that test plus `python3 tools/static_build.py --check` and `node tests/layout_url.test.js`.

## DOIT.38 Q1 Finder date/reload toolbar alignment
- Finder now groups the shared date-mode toggle and Reload control as a right-aligned `Ago`/Reload cluster, matching Differ while keeping the path and creation/sort controls left-aligned. Verification beyond the standard gate: `python3 -m pytest tests/test_browser_layout.py::test_finder_path_is_first_and_readable_in_wrapped_toolbar -q`.

---

Completed 2026-06-06. Extracted from the 2026-06-06 daily log.

# DONE - Finder right-click is immediate

Release: v0.7.20.

## Completed

- [x] Reproduced the delay in the shared Finder context-menu owner: it waited for path-info and relative-copy resolution before mounting any menu DOM.
- [x] Kept `showFileTreeContextMenu` as the shared Finder/Differ entry point, but split it into an immediate render/mount and a guarded deferred refresh. A stale, dismissed, or reselected menu cannot be repainted by an older response.
- [x] Used the existing listing-time repository marker for one directory's immediate `ΔShow Diff` action. Directories without that marker still omit it.
- [x] Kept the existing `openGitDiffTab` lifecycle: clicking `ΔShow Diff` creates or activates the tab immediately, and its history request owns the shared animated `Loading...` ellipsis.
- [x] Added a Node contract for immediate paint before deferred refresh and a real-browser held-request regression that proves native-menu suppression, immediate enabled Diff, active tab, and animated loading before path metadata or history returns.
- [x] Rebuilt the static bundle and updated the Finder behavior contract and user guide.

## Validation

- `python3 tools/static_build.py` and `python3 tools/static_build.py --check` passed.
- `node tests/cross_surface_state.test.js` passed: 56 passed, 0 failed.
- `python3 -m pytest tests/test_browser_finder.py -k 'repo_context_menu_and_diff_tab_are_immediate or context_diff_repo_eligibility_and_touch_long_press'` passed: 2 passed, 47 deselected.
- The final canonical gate and requested 7771 live acceptance are recorded in the v0.7.20 release evidence.

## Ownership audit

The existing parent is `showFileTreeContextMenu` with `fileContextMenu`; Finder and Differ continue to share tree selection and menu builders. `rg` found no second Finder context-menu route or parallel promise/timer owner. The change adds 189 non-generated source/test lines and removes 20, excluding rebuilt `static/yolomux.js` and documentation.

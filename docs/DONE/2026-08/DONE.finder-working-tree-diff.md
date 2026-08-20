# DONE - Finder Working-Tree Diff

Release: v0.7.9.

## Goal

Finder always shows live uncommitted `HEAD` to current changes while Differ alone owns arbitrary FROM/TO comparisons. Changing Differ refs does not change, clear, or reload Finder annotations.

## Completed

- [x] Added distinct Finder-working-tree and Differ-comparison session-files records and caches. Finder badges, ancestor totals, repository `+/-`, Sync planning, and highlights read the fixed `HEAD`/`current` record; Differ rows and controls read the selected comparison record.
- [x] Routed watch, HTTP, operation-result, invalidation, Reload, render, and filesystem-mutation refreshes to the correct record. Identical Finder/Differ requests deduplicate, while a Differ ref change invalidates and fetches only Differ.
- [x] Added red-first Node coverage. Before the split, `tests/cross_surface_state.test.js` reported 49 passed and one exact failure because a historical Differ request was the only session-files request; after the fix it reports 50/50.
- [x] Updated `README.md` and `docs/specs/GUI.md`, rebuilt generated assets, and preserved the concurrent Finder repository-SHA hover work in shared source, tests, CSS, and documentation.
- [x] Proved Finder remains `HEAD`/`current` across the historical response and multiple render frames while Differ accepts the requested historical SHA.
- [x] Proved one repository's Differ FROM change preserves Finder payload/cache ownership, visible file status, and repository totals without issuing a Finder refresh solely for the comparison change.
- [x] Verified `python3 tools/static_build.py --check`, `node tests/cross_surface_state.test.js` at 50/50, `node tests/layout_restore.test.js` at 112/112, the exact Selenium regression at 1/1, two affected Differ browser cases at 2/2, `python3 tools/check.py --lane node-layout --cpu-percent 25`, and `git diff --check`. The full gate remains the v0.7.9 landing-batch check.
- [x] Restarted only 7771. PID 1869701 runs from `/home/keivenc/dev/yolomux.dev7771`, discovery reported all tmux sessions, and served bundle SHA-256 `1710ee18b7d358bf1edddcc246352e03735d14429fb9d0de577812a63340650a` matched the rebuilt file. An authenticated live browser measured Finder at `+676 -308` before and after changing Differ FROM to `f32ffd898cfc07563cf112913a51e15337b8adc3`; Differ changed independently to `+702 -309`.

## Implementation Notes

- Reused the existing session-files request, cache, watcher-generation, and renderer parents; no Git endpoint or per-row Git computation was added.
- Negative searches removed Differ-state defaults from Finder Sync root planning, old Finder destinations from Differ gate helpers, and the shared Reload handler's one-surface routing.
- The current combined non-generated diff in the touched feature/test/doc paths is +433/-157 (net +276); that count includes the preserved concurrent repository-SHA hover edits in shared files.

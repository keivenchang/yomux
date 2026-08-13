# Editor, Finder, Differ, and Files

## DOIT.72/73 file-tree diff stats and shared watch-root index
- Completed and removed `DOIT.72.md`. Finder/Differ repo-root and changed-directory line counts now use the same right-aligned `.file-tree-diff` slot as files, with separate `changes-diff-add` and `changes-diff-remove` spans; the old inline combined `+N/-N` repo/directory formatter is gone, while branch/sync metadata and gray directory file counts stay separate.
- Completed and removed `DOIT.73.md`. Directory watch roots now persist to the shared `STATE_DIR/watch-index.json` store with read-modify-write `file_lock` + `atomic_write_text` writes, per-server/source/session ownership, TTL expiry, lock-free tolerant reads, fair capped snapshots, and automatic selected-pane/git-root indexing without waiting for `/api/watch/roots`.
- Verification: `python3 tools/static_build.py`, `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `python3 tools/static_build.py --check`, `python3 -m pytest tests/test_browser_layout.py -k 'platform_controls_use_pc_glyphs' -q`, `python3 -m py_compile yolomux_lib/app.py yolomux_lib/common.py`, focused watch-root pytest (`6 passed`), `python3 -m pytest tests/test_app.py -q` (`108 passed`), `python3 -m pytest tests/test_server_query.py -q` (`34 passed`), and dev3 restart/smoke on port `8003` (`ping: 401`). `python3 tools/check.py` was not green only because of the unrelated pre-existing `test_diff_overview_matches_actual_todo_codemirror_rows` offset pin (`485 != 744`); all other lanes passed and full pytest otherwise reported `782 passed`. Prod was not restarted because this run was explicitly scoped to dev3 only.

---

Completed 2026-06-14. Extracted from the 2026-06-14 daily log.

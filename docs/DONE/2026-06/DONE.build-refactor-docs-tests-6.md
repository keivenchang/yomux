# Build, Refactor, Docs, and Tests

## DOIT.61 refactor audit follow-up
- Completed and removed the refactor-audit `DOIT.61.md`. Fixed the activity heartbeat rotation leak, preserved epoch mtimes in session-file fallback timestamps, hardened corrupt activity-ledger startup, removed dead Tabber path/detail render paths, kept the odd `1501ms` file-index-building poll cap, and added source/runtime tests for those cases.
- Consolidated shared parents for Finder/Differ/Tabber row behavior: dataset set/delete, tree indentation/depth, treeitem ARIA, stale row handler clearing, git-status normalization, Tabber session-files state, and Tabber active-window marker data. CSS audit fixes added theme tokens for file icons, soft links, menu accents, light drop-outline, and drop-suggestion z-index; routed light text through `--lt-text`; and converted the audited editor edge pins to logical inline properties.
- Backend cleanup added `error_payload(...)`, a typed error payload shape, one GET int-query route wrapper, and `sessions.active_window_for_panes(...)`. Docs now cover upload destination behavior, terminal drop suggestions, Cmd-P absolute-path targeting, Tabber non-collapsible session rows/touched-path leaves, and `GET /api/activity-summary`.
- Verification for this archive entry was focused rather than the full standard gate: `python3 tools/static_build.py`, `python3 tools/static_build.py --check`, `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `python3 -m py_compile yolomux_lib/common.py yolomux_lib/server.py yolomux_lib/sessions.py yolomux_lib/app.py`, and `python3 -m pytest tests/test_server_query.py tests/test_sessions.py tests/test_activity.py tests/test_app.py tests/test_session_files.py -q` (117 passed).

## Final active DOIT cleanup archived
- Completed and removed the last active `DOIT.51.md` / `DOIT.53.md` files. `DOIT.51` closed the small follow-ups: kept native blame hover by product decision, validated stale-command auto-approve behavior against the live prompt command, standardized YOLO risk labels, documented the `0.0.0.0` host default, added scoped share-token auth/revocation/layout seeding, and filtered Quick Open exact filename searches so indexed-root fuzzy noise does not bury the local match. `DOIT.53` moved the tmux sub-window bar into the pane Info Bar, rendered `index:name` labels from process-aware names, kept click-to-switch on the existing `/api/tmux-window` route, and made Finder path errors path-keyed so stale red states do not bleed into a new root.
- This archive entry is bookkeeping for the already-shipped 0.2.82 batch (`50cbd0a`); the raw DOIT notes were removed after confirming both files had no unchecked tasks.

---

Completed 2026-06-11. Extracted from the 2026-06-11 daily log.

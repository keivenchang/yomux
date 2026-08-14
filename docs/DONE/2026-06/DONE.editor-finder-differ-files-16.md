# Editor, Finder, Differ, and Files

## Shared Finder/Differ/Tabber tree controller
- Completed and removed `DOIT.shared_tree_finder_differ_tabber.md`. Finder, Differ, and Tabber now register through one shared tree interaction controller for row discovery, lead/selection state, range/select-all, disclosure/expand-collapse, activation, current-row sync, aria, and scroll reveal. Finder keeps Finder-only commands for Return rename, Cmd-open, Space preview, typeahead, and enclosing-folder navigation while cursor movement and expansion use the shared parent; Tabber has Finder-style keyboard navigation plus active session/window sync; Differ has the same keyboard and mouse selection path while diff-ref inputs keep priority. Source guards now prevent bespoke Tabber row builders and Differ selected/current style forks. Verification: `node tests/layout_url.test.js` passed `162 passed, 0 failed`; final `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 45.73s`).

## Finder Ago recency brightness and pulse
- Completed and removed `DOIT.finder_ago_recency_brightness.md`. Finder rows in Ago mode now color only the date cell by one shared mtime-to-recency helper, use dark/light recency tokens, keep old rows muted, and gently pulse files modified within about a minute for 10 seconds without restarting on same-mtime refreshes. Date/None modes and Differ rows keep their previous styling.
- Verification: node regression `Finder Ago recency brightness and pulse are scoped to relative Finder rows` covers bucket mapping, Date/None clearing, pulse start/expiry/restart, and Differ scoping; full `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 40.25s`).

## Editor save hygiene, reload, and status counts
- Completed and removed `DOIT.editor_polish.md`. The editor now has opt-in save hygiene settings for trailing-whitespace trim and final newline, a reload-from-disk action that preserves the dirty-buffer confirmation path, and a status count segment showing live line/word/character counts alongside cursor/selection status.
- Verification: node tests cover helper/default behavior, save-path behavior, dirty reload cancel/confirm, and live count updates; settings tests cover defaults/sanitization; full `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 40.25s`).

---

Completed 2026-06-19. Extracted from the 2026-06-19 daily log.

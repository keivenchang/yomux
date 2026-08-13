# Finder Sync

- Completed and removed `DOIT.finder_home_base_sync_per_tab_tree.md`. Finder Sync now keeps home-scoped tab work rooted at `~`, expands down to the active tab's working folders, uses the absolute common ancestor for mixed home/outside-home work, and swaps the in-memory expanded/collapsed state per tmux session when focus changes. Manual collapses remain per Tab and fixed-root mode stays unchanged. Focused verification: `node tests/layout_async.test.js`, `node tests/share_theme.test.js`, and `python3 tools/static_build.py --check`.

---

Completed 2026-06-28. Extracted from the 2026-06-28 daily log.

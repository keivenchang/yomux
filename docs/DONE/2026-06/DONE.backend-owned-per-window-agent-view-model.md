# Backend-owned per-window agent view model

- Completed and removed `DOIT.per_window_viewmodel_backend_source.md`. `agent_window_status_payloads` now owns each Claude/Codex tmux sub-window record (`pid`, `active`, state, touched repo roots, path entries, and git facts), `/api/activity?hours=...` serves those records from the cached activity/session-files path, and the frontend `sessionAgentWindowStatusPayloads` / `windowViewModel` accessors feed popover, Tabber, Info Bar, and tmux sub-window bar rendering. The old frontend per-sub-window path resolvers (`sessionWindowMetadataRows`, `tabberRepoEntriesForWindow` session-files parse, selected-pane path use for agent metadata) were retired. Verification: `node tests/tabber.test.js`, `node tests/editor_preview.test.js`, `node tests/layout_async.test.js`, `node tests/layout_url.test.js`, `node tests/share_theme.test.js`, focused backend/browser-share pytest, and full `python3 tools/check.py` (`CHECK PASSED in 96.19s`).

---

Completed 2026-06-22. Extracted from the 2026-06-22 daily log.

# Mixed and Cross-Cutting

## UI tree and CLI follow-ups
- Completed and removed `DOIT.ui_tree_cli_followups.md`. Refresh now resizes visible tmux tabs, Finder/Differ/Tabber timestamp recency styling covers Ago and Date modes, Tabber shell/process rows use neutral process affordances instead of checkbox-looking squares, and Claude/Codex text-client startup flags plus slash-command help are pinned to docs and versioned real-client fixture lists.
- Verification: focused frontend/browser tests passed for refresh, Tabber row shape, recency styling, and process-row affordances; `python3 -m pytest tests/test_text_client_common_metadata.py` passed (`15 passed`) for CLI docs/fixture parity; full `python3 tools/check.py` passed (`CHECK PASSED in 45.44s`).

---

Completed 2026-06-20. Extracted from the 2026-06-20 daily log.

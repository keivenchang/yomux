# UI, Theme, Preferences, and Localization

## Search and run history foundation
- Completed and removed `DOIT.search_and_run_history.md`. YOLOmux now has scan-on-query full-text search across captured events and summaries, `/api/search`, compact persisted run history rows with prompt/cwd/agent/timestamps/final state/PR/latest summary, `/api/run-history`, and a Search & Runs UI tab/menu entry that renders search results plus compact run rows without scraping terminal text. Verification: observability pytest covers search/result shape/run rows, node layout tests cover frontend fetch/rendering, static assets are rebuilt, and final `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 43.83s`).

---

Completed 2026-06-19. Extracted from the 2026-06-19 daily log.

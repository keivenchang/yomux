# Agent activity status refactor

- Completed and removed `DOIT.refactor_agent_activity_status.md`. Agent activity UI now routes pulse cadence, status dot/glyph animation, agent icon sizing, popover host behavior, and per-window activity rendering through shared parents instead of duplicated Tabber/Info Bar/popover selectors. A real Claude Code 2.1.186 capture (`working_empty_prompt_below_counter__claude-code-2.1.186_20260623.yaml`) now proves a visible working counter remains working even when a bare empty composer prompt sits below it. Verification: `python3 -m pytest tests/test_agent_tui.py -q` (`96 passed`), `node tests/editor_preview.test.js` (`100 passed`), `node tests/tabber.test.js` (`38 passed`), focused parity/browser pytest, `python3 tools/static_build.py --check`, full `python3 tools/check.py` (`CHECK PASSED in 56.13s`), and dev8001 restart/ping (`pid 2126896`, unauthenticated `/api/ping` returned 401).

---

Completed 2026-06-23. Extracted from the 2026-06-23 daily log.

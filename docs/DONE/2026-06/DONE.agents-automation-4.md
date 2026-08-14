# Agents and Automation

## Agent prompt attention attention
- Completed and removed `DOIT.agent_prompt_attention_ask.md`. Claude/Codex approvals and questions now raise a clearable red `attention` cue in session tabs, pane chrome, and global activity; prompt detection is backed by a fixture inventory, positive/negative corpus, capture harness, mock-agent E2E, and real-agent smoke. Verification: detector corpus tests, browser attention tests, mock Claude/Codex tmux tests, real Claude/Codex smoke, rebuilt static assets, and final `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 44.88s`). The inventory still records bell-only live-signal coverage as an explicit uncaptured gap, not a captured fixture claim.

---

Completed 2026-06-19. Extracted from the 2026-06-19 daily log.

# Agents and Automation

## DOIT.44 pending-approval roster badge
- Fixed the auto-approve-off roster path so pending permission prompts are visible without focusing the session. The cheap all-session status path now captures the discovered agent pane instead of the bare tmux session, derives `approval_prompt_state()` from the already-captured visible pane text, and still avoids the expensive hybrid transcript / full-pane prompt fan-out. The session-state classifier also treats `screen.key === "approval"` as `needs-approval`, so the roster lights `EXEC?` even if a future payload misses `prompt.visible`. Added backend coverage for cheap roster prompt visibility and non-active agent-pane targeting, plus a JS classifier regression for screen-only approval state. README now documents the pending-prompt attention badge behavior.

---

Completed 2026-06-06. Extracted from the 2026-06-06 daily log.

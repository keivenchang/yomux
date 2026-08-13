# YO!agent

## Claude/Codex goal-active prompt detection
- Completed and removed `DOIT.goal_active_extraction_claude_and_codex.md`. Claude `/goal active (<duration>)` now shares the same goal-duration parser path as Codex `Pursuing goal (<duration>)`, feeding `goal_elapsed_seconds` and `display_elapsed_seconds` so displayed working time can prefer the cumulative goal timer instead of only the current spinner step.
- Verification: real prompt-corpus captures `goal_active__claude-code-2.1.185_20260621.yaml` and `goal_active__codex-cli-0.141.0_20260621.yaml` are registered together, `test_real_goal_active_captures_prefer_goal_elapsed_for_display` covers both, and the focused detector run covering goal-active, attention, interrupted, and corpus classification passed (`44 passed`).

## YO!agent send orchestration and prompt answers
- Completed and removed `DOIT.yoagent_send_orchestration.md`. YO!agent now treats the tmux session number/label as the handle, routes explicit send/ask/tell requests through server-verified tmux sends instead of model refusals, answers visible approval/question prompts through the shared selector path without pasting free text into menus, and decomposes same-session dependent asks into send -> wait -> derive -> follow-up send.
- Verification: focused parser/action/skill/app tests passed, `python3 tools/static_build.py` passed, full `python3 tools/check.py` passed during implementation and again after live proof (`CHECK PASSED in 83.75s`), 8001 restart/ping returned the expected 401, and live 8001 API checks against disposable mock tmux sessions proved ready send, sequential first-send decomposition, and selector answer (`/api/yoagent/actions/preview-send` + `/execute-send` selected option 2 in `so10ask`, and the pane showed `● You picked: Roger Federer`).

## Stream tool-call coalescing
- Completed and removed `DOIT.yoagent_combine_consecutive_tool_calls.md`. Consecutive YO!agent `tool` stream items now coalesce into one Tool call details block while assistant/thinking rows still split separate tool runs; thinking summaries show the full word count for the coalesced thinking block.
- Verification: `python3 tools/static_build.py`, `python3 tools/check.py`, 8001 restart/ping, and a live 8001 Selenium verifier rendered three adjacent tool items through `yoagentMessageStreamItemsHtml` and found one `.yoagent-toolcall-details` containing every command/output line.

## Claude backend availability and stream-json CLI flags
- Completed and removed `DOIT.yoagent_claude_stale_availability_deterministic.md`. YO!agent no longer treats transient Claude auth probe failures as sticky logged-out state, refreshes frontend agent availability via `/api/agent-auth`, keeps explicit Claude/Codex selections explicit when the CLI is installed, and surfaces provider-specific fallback reasons instead of silently downgrading to deterministic.
- Fixed the live restart verification failure exposed by Claude Code 2.1.185: the shared Claude stream-json argv no longer passes the stale `--show-thinking` flag, which the installed CLI rejects. The transport still uses `--include-partial-messages` with `--output-format stream-json`.
- Verification: `python3 -m pytest tests/test_yoagent_transports.py::test_claude_stream_json_argv_includes_partials_and_optional_tools -q` passed, final `python3 tools/check.py` passed, 8001 was restarted with `tools/yolomux-restart-dev1.sh`, unauthenticated `/api/ping` returned the expected 401, and an authenticated live `/api/yoagent/chat` request with `yoagent.backend=claude` returned `backend_used: "claude"` / `fallback: false` / `answer: "YOAGENT_BACKEND_CHECK"`.

---

Completed 2026-06-21. Extracted from the 2026-06-21 daily log.

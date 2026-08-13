# Agents and Automation

## attention no-selector and interrupted prompts
- Completed and removed `DOIT.ask_detection_gaps.md` and the now-empty `DOIT.00_index.md`. Claude AskUserQuestion menus now classify as `needs-input`/attention from the visible question, numbered options, and footer even when no selector is highlighted; accessible screen-reader menu rows such as `menu: 1. Pane capture — ...` are parsed without treating descriptions as labels. Claude interrupted prompts such as `Interrupted · What should Claude do instead?` use the same attention path and never enter the approval/auto-answer path.
- Verification: real Claude Code 2.1.185 captures for no-selector accessible AskUserQuestion, highlighted AskUserQuestion, and interrupted state are registered in the prompt corpus; detector tests prove `agent_screen_state`/`visible_choice_prompt_text` return `needs-input` with the prompt text while `detect_prompt` and `approval_prompt_state` stay non-approval; live 8001 `/api/auto-approve` against `yoreal-no-selector-ax`, `yoreal-no-selector-proof`, and `yoreal-interrupt-proof` reported `screen.key == "needs-input"` and `prompt.visible == false`; final `python3 tools/check.py` passed (`CHECK PASSED in 118.75s`).

## Summary provider settings and availability gate
- Added a real `summary.*` settings group and backend catalog entries for AI summary provider, Codex model, effort, service tier, lookback, and timeout. `/api/summary-stream` now reads those settings for metadata, `codex exec` argv, and timeout, and refuses to build a prompt or launch Codex when the provider is disabled, Codex is missing, or Codex is installed but not logged in.
- Documentation now names `summary.*` as the owner of summary defaults; the old `YOLOMUX_SUMMARY_*` env vars only seed defaults when they match valid catalog values. Verification beyond the standard gate: `python3 -m pytest tests/test_settings.py tests/test_server_query.py -q` passed (`100 passed`).

## Mock agents moved to tools and corpus replay
- Completed and removed `DOIT.mock_agents.md`. Mock Claude/Codex now live under `tools/`, declare Claude Code 2.1.183 and Codex CLI 0.141.0, render current startup chrome/status counters/approval labels, and expose `mockcase list` plus `mockcase <case>` to replay every real and synthetic prompt-corpus family from `tests/fixtures/prompt_corpus/` inside tmux.
- Verification: `python3 -m pytest tests/test_mock_agents.py tests/test_auto_approve_detector.py tests/test_agent_tui.py tests/test_e2e_auto_approve.py tests/test_browser_layout.py::test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser -q` passed (`250 passed`), and full `python3 tools/check.py` passed (`CHECK PASSED in 49.44s`).

## Activity detection from live status counters
- Completed and removed `DOIT.activity_elapsed_counter_detection.md`. Visible Claude/Codex status-counter rows now count as live activity when their elapsed/token counters advance, even with arbitrary status words and trailing tip/composer chrome, so pane tabs, YO rings/markers, Tabber, activity APIs, YO!agent status, wait-then-send, and auto-approve refusal all share the same `visible-counter` evidence.
- Verification: focused detector/app tests passed (`44 passed`), full `python3 tools/check.py` passed, and live 8001 verification against Claude `2.1.183 (Claude Code)` on pane `%20` saw advancing rows such as `✽ Hashing… (3s · ↓ 26 tokens)` through `/api/auto-approve` and `agent_screen_state(..., pane_target='%20')` as `working` with `activity_source='visible-counter'`.

## Claude/Codex text-client slash-command parity
- Completed and removed `DOIT.cli_add.md`. `tools/claude.py` and `tools/codex.py` now derive slash-command names, aliases, help rows, completion, and compatibility notes from the shared `tools/text_client_common.py` registry; Codex gained Claude-style permission aliases, reasoning/thinking controls, `/context`, real `/usage`, and conversation-clearing `/clear`; Claude gained `/reasoning`, and both clients use `/cls` for terminal clearing.
- Verification: `python3 -m pytest tests/test_text_client_common_metadata.py` passed (`13 passed`) and covers registry/export/help parity, compatibility notes, thinking/reasoning aliases, `/clear` vs `/cls`, and Codex `/usage`.

---

Completed 2026-06-20. Extracted from the 2026-06-20 daily log.

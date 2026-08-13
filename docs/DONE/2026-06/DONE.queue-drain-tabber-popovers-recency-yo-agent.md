# Queue drain — Tabber, popovers, recency, and YO!agent

## Tabber status, chrome, sort, popover parity, and per-window paths
- Completed and removed `DOIT.tabber_status_color_system.md`, `DOIT.tabber_row_icon_and_context_menu.md`, `DOIT.tabber_session_tab_equal_width.md`, `DOIT.tabber_sort_control.md`, `DOIT.tabber_tab_popover_parity.md`, `DOIT.per_window_path_one_source.md`, and `DOIT.popover_id_label_and_copy.md`. First-level Tabber session rows now reuse the real pane-tab chrome/popover at the full available width, keep disclosure/row tree ownership, expose the shared A-Z/Z-A/recent/oldest sort select, use the shared tab context menu on session/window/tab rows and the file context menu on repo rows, show PIDs/session IDs with shared copy buttons, and use the same per-window touched-repo resolver as the popover path rows.
- Status text/dots now share the common tone system: attention is a filled red attention dot/text pulse, working is green, cooldown/settled are static, `active` and sub-minute plain recency use the max-contrast token, and static/localized bundles carry `branch.current=active` plus `popover.sessionId`.

## Recency and Claude working detection
- Completed and removed `DOIT.tabber_recency_stuck_under_15sec.md` and `DOIT.claude_working_shows_black_dot.md`. Terminal protocol/report-only input no longer pegs activity recency at `<15 sec ago`; genuine terminal input still records just-active state. Claude/Codex visible duration/token-counter working screens are covered by detector tests/captures, and the activity-dot state machine can return from settled/cooldown to green when working is detected again.

## YO!agent stream and orchestration tests
- Completed and removed `DOIT.yoagent_claude_thinking_words_recurring.md` and `DOIT.yoagent_orchestration_tests.md`. YO!agent now preserves ordered assistant/thinking/tool stream items, keeps real Claude thinking through heartbeat and turn-done updates instead of replacing it with the `thinking` placeholder, counts words from the real auxiliary text, renders expanded auxiliary details without an inner scroll box, and adds repeatable orchestration coverage for server-verified sends, sequential dependent sends, wait-then-send, and prompt-answer selector routing.
- Verification: `python3 tools/check.py` passed (`CHECK PASSED in 118.35s`) after rebuilt static assets and the new/updated node, browser, backend, YO!agent, and detector regressions were in place. `DOIT.00_index.md` was removed after all queue files were drained.

---

Completed 2026-06-21. Extracted from the 2026-06-21 daily log.

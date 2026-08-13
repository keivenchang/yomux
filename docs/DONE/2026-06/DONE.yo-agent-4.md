# YO!agent

## YO!agent suggestion prompts, waits, and editor image paste
- Completed and removed `DOIT.refix.md`. YO!agent now treats Claude NBSP and Codex ANSI-dim bottom-composer suggestions as idle placeholder UI, detects real typed drafts separately, clears real drafts through the verified target-send path when sending, sends to suggestion-only Claude/Codex panes without clearing prompt text, records transcript/edited-file evidence before visible-pane fallback, always clears pending wait rows on success/partial/no-output timeout, and exposes a Clear control backed by the existing action wait store. Markdown editor image paste now uploads beside the edited Markdown file and inserts relative `.uploads/...` links while preserving terminal paste behavior and preview resolution.
- Verification: raw live Claude/Codex suggestion captures were added as tests; focused YO!agent/upload route tests passed (`38 passed` in `tests/test_app.py`, `3 passed` in `tests/test_server_query.py`), `node tests/layout_async.test.js`, `node tests/editor_preview.test.js`, and final `python3 tools/check.py` passed (`CHECK PASSED in 49.16s`). Live smoke sent marker/date prompts to session `1:0` Claude and `1:1` Codex, both returned marker-bearing YO!agent result messages with `pending_waits: []`; user completed the 8001 manual visual gate after restart on version `0.4.19`.

## YO!agent central command
- Completed and removed `DOIT.yoagent_central_command.md`. Claude `Try ...` placeholders no longer block sends; explicit target sends default to result capture with opt-outs; server-owned sends revalidate target pane, prompt state, drafts, and submission; pending waits, multiple sends, wait-then-send, handoffs, visible jobs, and work-next ranking all use the shared YO!agent/all-session path. Verification: parser/app/layout/job tests, mock Claude/Codex real-tmux E2E, real `7777` smoke, rebuilt static assets, and final `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 44.88s`).

## YO!agent chat scrollbar ownership
- Completed and removed `DOIT.yoagent_chat_scrollbars.md`. YO!agent thinking/activity now has one normal vertical scroll owner: `.yoagent-chat-history`; the outer YO!agent list keeps vertical overflow hidden, the chat history owns stable vertical scrolling, active-pane hover keeps the rail neutral, and direct scrollbar hover/drag still gets the active thumb. Streaming/busy refreshes respect manual scrollback and keep the composer separated from the history.
- Verification: `tests/test_browser_layout.py::test_yoagent_busy_chat_uses_one_vertical_scroll_owner` builds a busy + streaming YO!agent state at desktop and narrow widths, captures screenshots, asserts only history overflows vertically, and verifies scrollback is not yanked to bottom; `node tests/layout_url.test.js` passed 154/154; final `python3 tools/check.py` passed all 7 lanes (`CHECK PASSED in 42.84s`).

---

Completed 2026-06-19. Extracted from the 2026-06-19 daily log.

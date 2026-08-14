# YO!agent oversized conversation recovery

- Completed and removed `DOIT.yoagent_chat_413_request_entity_too_large.md`. YO!agent now caps persisted auxiliary/tool stream data through one shared sanitizer for conversation JSONL and live stream state, so megabyte `rg`/tool output lines cannot wedge future turns. Codex app-server sends detect HTTP 413 / request-too-large failures, drop the poisoned resume thread, start a fresh Codex thread, and retry once before surfacing an actionable error. The YO!agent chat UI now shows a specific “conversation too large to resume” message instead of raw `chat failed: Request Entity Too Large`, and the YO!agent prompt now tells the model to keep shell output bounded with `rg -M 2000 --max-columns 2000`, targeted paths, and summaries. Verification: `python3 -m pytest tests/test_yoagent_stream_state.py tests/test_yoagent_transports.py tests/test_activity_summary.py -q`, `python3 -m pytest tests/test_app.py -k 'reset_yoagent_chat_clears_cli_sessions or yoagent_codex_backend_falls_back_to_exec_when_app_server_fails' -q`, `python3 -m pytest tests/test_app.py -k 'yoagent_model_chat_appends_history_and_skips_activity_for_simple_followup' -q`, `python3 tools/static_build.py`, and `node tests/layout_async.test.js`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

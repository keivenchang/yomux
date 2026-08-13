# Slow API/SSE payload optimization

- Completed and removed `DOIT.optimize_slow_api_calls.md`. Auto-approve roster status now has a single cached stale-while-refresh owner with single-flight refreshes; session-scoped auto-approve calls bypass the full roster cache; timer `auto_approve_changed` pushes compact `{refresh: true, signature}` invalidations instead of the all-session roster; auto-approve window rows skip path/git inventory and reuse selected-pane screen classification; Debug shows auto-approve phase timings. Client-event payloads are smaller: `fs_changed` sends compact refresh invalidations, watch-state transcript pushes send signature-only refreshes, unchanged session-files payloads do not republish across repeated `fs_changed` triggers, watch-root registration is debounced, and tmux signal pushes use changed-window patches with full-payload fallback. Startup control-socket `BrokenPipeError` is treated as a benign disconnect. Verification: focused `python3 -m pytest` slices for auto-approve/control/client-event paths, `python3 -m pytest tests/test_app.py -k 'auto_approve_payload_includes_agent_window_statuses' -q`, `python3 tools/static_build.py --check`, and `node tests/layout_async.test.js`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

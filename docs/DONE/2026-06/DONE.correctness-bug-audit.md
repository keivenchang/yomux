# Correctness bug audit

- Completed and removed `DOIT.bug-audit.md`. Verified the current tree covers all 13 audited backend/frontend correctness bugs: share viewer snapshots, hidden-tab terminal resize recovery, PTY/share cleanup, bounded tmux option calls, owned share-reader fds, vanished auto-approve session cleanup, incremental UTF-8 stream decoding, tmux replacement decoding, SIGKILL wait, bounded share sends, late websocket-frame guards, terminal fit callback teardown, and non-ASCII websocket-key rejection. Final focused verification for the remaining items: `python3 -m pytest tests/test_server_query.py::test_share_viewer_send_frame_restores_bounded_timeout tests/test_server_query.py::test_accept_websocket_rejects_non_ascii_key_cleanly -q` and `node tests/editor_preview.test.js`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

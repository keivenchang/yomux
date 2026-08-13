# Browser reload on client/server mismatch

- Completed and removed `DOIT.client_server_version_reload_prompt.md`. The existing server-version reload path now treats enabled semantic differences as client/server mismatches in either direction, asks "Do you want to reload the browser?", and shows the existing Reload plus Keep controls while preserving the same metadata poll, safe auto-reload gate, idempotence guard, and self-update suppression. Verification: `node tests/layout_async.test.js`, `node tests/editor_preview.test.js`, `node tests/layout_url.test.js`, and full `python3 tools/check.py` (`CHECK PASSED in 99.34s`).

---

Completed 2026-06-22. Extracted from the 2026-06-22 daily log.

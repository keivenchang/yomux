# Startup and Runtime Diagnostics

- Completed and removed `DOIT.slow_load.md`. Initial page load no longer blocks on cold Claude/Codex auth probes: the bootstrap uses cached/stale/unknown auth state and kicks a coalesced background refresh, with server startup warming auth status and transient refresh failures preserving prior known login state. `GET /` performance samples now record `html_page` compute time and bootstrap bytes. The first HTML document is smaller because Preferences-only settings metadata (`catalog`/`choices`) is deferred to a silent post-paint `/api/settings` fetch; measured dev bootstrap size dropped from about 176 KB to about 96 KB. Runtime reports now include `top_background_work` so 7777-style contention can be attributed to background roles; the current report showed stats sampling and watch-root signatures as the largest background compute rows, plus a 1.07 GB session-files cache. Focused verification: `python3 -m pytest tests/test_workdir.py tests/test_server_query.py tests/test_auth_config.py -q`, `python3 -m pytest tests/test_app.py::test_runtime_report_payload_reports_owner_cache_endpoints_events_and_transcripts -q`, `node tests/editor_preview.test.js`, `python3 tools/static_build.py --check`, and `/tmp/yolomux-runtime-7777-after.json`.

---

Completed 2026-06-28. Extracted from the 2026-06-28 daily log.

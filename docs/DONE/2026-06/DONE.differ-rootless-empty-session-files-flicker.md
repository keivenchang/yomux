# Differ rootless-empty session-files flicker

- Fixed the Differ panel flashing between `~/yolomux.dev8002` and an empty result for Tab `8002`. The frontend accepted a transient `session_files_ready` payload with `repos: []` and `files: []` for the selected session, so a weak session-discovery snapshot could blank an already-loaded rooted Differ payload until the next rooted payload arrived. Background fetches and SSE pushes now preserve the current same-session Differ payload when the incoming update is rootless and empty; a real clean result still applies because it carries the live repo root with `count: 0`. Added regression coverage for the exact `8002` / `~/yolomux.dev8002` shape and rebuilt `static/yolomux.js`. Focused verification: `node tests/share_theme.test.js` and `python3 -m pytest tests/test_node_suite.py`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

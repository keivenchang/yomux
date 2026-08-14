# Filesystem watch diff/keyframe frames

- Implemented MPEG-style filesystem watch frames. The server now records a bounded shared filesystem snapshot history, sends full frames for the first/keyframe/manual paths, sends compact `fs_changed` invalidations for ordinary watch changes, and answers stateless client `GET /api/fs/watch-diff?since=<token>` requests with changed-root diffs or a full stale-token fallback. Browser clients keep their own token, request `full=1` from the top-right refresh path, invalidate removed roots, and no per-client server state is needed for multiple clients per server. Verification: focused filesystem watch and route tests, `node tests/layout_async.test.js`, and full `python3 tools/check.py`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

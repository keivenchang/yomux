# CPU bursts, YO!cost drag jank, and malformed history coverage

## Goal

Attribute and remove the API-process filesystem burst, make schema/backfill convergence an honest retryable history state, suppress chart/table repaint work throughout pane drags, and remove expected-disconnect noise and stale stats artifacts.

## Queue

- [x] Attribute the lstat/scandir burst by name.
  - DONE (2026-07-13): read-only 8881 runtime profile captured all 44 samples of the active request thread in `os.walk -> metadata._discover_indexed_repo_roots -> indexed_repo_roots -> indexed_repo_summaries -> build_session_metadata_payload`; the native 5-second sample recorded 2,010 `os_scandir` and 786 `os_lstat` samples. This is indexed-repository metadata discovery in the API process, not Quick Open/indexd.

- [ ] Off-source the indexed-repository walker and prove the live web process stays below 10% during a full refresh.
  - IMPLEMENTED (isolated worktree): registered maintenance-priority `jobd` task `indexed_repo_roots`; `ActivityTranscriptService` owns one in-flight job plus the last completed snapshot; metadata requests return that snapshot immediately and never call the recursive walker. Worker and snapshot tests pass. REMAINS: restart/integrate outside this isolated task, capture an after-profile during a full refresh, and record web/jobd CPU separately.

- [ ] Fix and reproduce the historical mid-backfill coverage response.
  - IMPLEMENTED: the browser recognizes explicit `coverage: {pending: true, reason, retry_after_seconds}`, displays `Backfill in progress`, suppresses retained-history requests until retry is due, then auto-recovers; absent/malformed coverage still reaches the explicit error+Retry path. The regression passes. REMAINS: the current exact 15-minute payload is valid (933,967 encoded bytes, two epoch coverage intervals, usage atom backfill complete with 407 sources/0 missing), so the screenshot-era malformed response cannot be captured from the current runtime. Capture a future failing body before closing this item.

- [ ] Remove YO!cost pane-drag repaint jank and record live long-task evidence.
  - IMPLEMENTED: both scheduled and direct YO!stats/YO!cost refresh paths defer while `dragState.item` is active, coalesce force state, and flush once from `endSessionDrag`; SSE/sample application remains independent. Node regression passes. REMAINS: after integration/restart, record the before/after client long-task counters with a 5-minute chart open and prove no render-attributed task exceeds 50 ms.

- [x] Silence expected-disconnect tracebacks and count them.
  - DONE (2026-07-13): `BrokenPipeError`, `ConnectionResetError`, and `ConnectionAbortedError` now emit one INFO-style line and increment `http-endpoint/expected-disconnect`; unrelated handler exceptions retain the superclass traceback. Before evidence: the existing 8881 log contained 992 `BrokenPipeError` entries. Focused regression passes.

- [ ] Cleanup, docs, full gate, restart, remeasure, archive, and delete this queue.
  - IMPLEMENTED: `StatsClient` safely removes only a zero-byte, non-symlink `services/stats-history.sqlite3` when it is not the configured DB; non-empty/configured files are preserved. README, DEVELOPMENT, and GUI document jobd ownership, no API tree walks, pending coverage, expected-disconnect counting, and drag repaint suppression. REMAINS: the primary 8881 runtime/worktree was explicitly out of bounds for this isolated task, so its existing zero-byte file was not deleted and it was not restarted. Run the full gate after integration, restart 8881, remeasure, archive completed evidence in `docs/DONE.md`, then delete this file.

## Focused validation

- `python3 tools/static_build.py`
- `python3 -m pytest -q tests/test_jobd.py tests/test_statsd.py::test_stats_client_removes_only_legacy_zero_byte_service_database tests/test_server_query.py::test_expected_client_disconnect_is_counted_without_traceback tests/test_app.py::test_indexed_repo_discovery_is_submitted_to_jobd_and_consumed_as_a_snapshot` — 17 passed
- `node tests/editor_preview_core.test.js` — 76 passed

## Safety boundary

This batch was implemented in `/Users/keivenc/Documents/bin/yolomux-wq-cpu-bursts`. It performed read-only profiling/capture against 8881 and did not stop, mutate, or restart the primary runtime.

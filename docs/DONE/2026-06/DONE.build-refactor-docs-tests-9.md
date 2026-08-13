# Build, Refactor, Docs, and Tests

## Gate speedup: stop double-running the node suite
- The default `tools/check.py` gate ran the node layout suite twice concurrently — once as the always-on `node-layout` lane and again inside the `pytest` lane via `test_node_suite.py` (marker `node_bridge`), which shells out to the identical `node tests/layout_url.test.js`. It was the single slowest pytest item (~32s under load) and the two node processes thrashed the cores the 32 xdist browser workers needed. Excluded it from the default pytest lane with `-m "not node_bridge"` (node coverage stays via the `node-layout` lane; a bare `python3 -m pytest tests` still runs the bridge) and updated the `test_check_runner.py` guard. Full gate wall-clock dropped from ~47s to ~33s, verified green across 8 consecutive runs. Tuning `-n` did not help — the gate is bound by its single longest test. `docs/DEVELOPMENT.md` and `AGENTS.md` updated. Commit `dbfd981`.

## Portable self-update restart
- Self-update restart no longer depends on `systemd-run`/`systemctl`/`pkill` (non-portable on macOS/non-systemd Linux, and the server failed to come back). It now spawns a detached `nohup bash -lc` helper that does a best-effort `kill`, waits 2s, a best-effort `kill -9`, then relaunches the same Python argv. Guard `test_self_update_restart_uses_portable_nohup_helper` asserts the portable `nohup bash -lc` shape and that `systemd-run`/`systemctl`/`pkill`/`setsid` are absent. Commit `f9e5758`.

---

Completed 2026-06-17. Extracted from the 2026-06-17 daily log.

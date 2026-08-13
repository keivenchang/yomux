# Performance research and runtime load reduction

- Completed and removed `DOIT.yolomux-perf-research.md`. The queue added per-role/background instrumentation, incremental Codex transcript scanning, slower visibility-gated token sampling, stable session-files cache identities, disk-cache pruning, owner refresh coalescing, sampled/sanitized refresh events, per-owner watch-root storage, visibility-gated Tabber/debug/fs work, per-endpoint response-byte accounting, a no-listener `--print-runtime-report` JSON diagnostic, and an expensive local-tool guard for full/default and browser/e2e check lanes. Verification included focused backend/node tests for each slice, live runtime-report smoke, focused `tests/test_check_runner.py` coverage for the tool guard, and full `python3 tools/check.py` passing after the guard detected active ports `7777`, `8001`, `8002`, and `8003` and lowered priority.
- Moved and removed `/tmp/DOIT.valign.md`. It contained vertical-header alignment findings for `conformance/utils/src/...`, but this checkout has no matching conformance files and the note had no unchecked queue items, so there was no YOLOmux implementation work to perform.

---

Completed 2026-06-27. Extracted from the 2026-06-27 daily log.

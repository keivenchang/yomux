# Editor, Finder, Differ, and Files

## Differ zero-change repo filtering
- Completed and removed `DOIT.differ_zero_change_repos_and_count.md`. Differ repo summaries now count and render only Differ-visible rows (`status != "T"`), so zero-change candidate repos, including sibling worktrees such as `~/yolomux.dev8002`, stay out of the rendered repo group list while transcript-only rows remain available in the raw file rows. The header count and rendered repo list now come from the same filtered set.
- Verification: `python3 -m pytest tests/test_session_files.py -q` passed with the new zero-change sibling/focused-anchor regression, including repeated samples and count/render agreement; after restart, direct backend samples for sessions `1` and `8001` were stable across three reads and did not include `~/yolomux.dev8002`; the final `python3 tools/check.py` passed (`CHECK PASSED in 56.29s`).

## Editor preview capability cleanup
- Completed and removed `DOIT.preview_drop_when_identical_to_editor.md`. The preview registry now distinguishes renderer identity from preview availability: generic text/code uses `previewable: false`, so `.txt` and code files open editor-only with no Preview/split/popout affordance, while distinct renderers such as markdown, images/media, structured JSON/YAML/TOML/env, and CSV/TSV remain previewable.
- Verification: `python3 tools/static_build.py`, `node --test tests/layout_restore.test.js`, `node --test tests/editor_preview.test.js`, `python3 -m pytest tests/test_browser_editor.py::test_editor_preview_direct_media_formats_use_shared_dispatch -q`, and full `python3 tools/check.py` passed (`CHECK PASSED in 49.98s`).

## Differ repo-set cutoff stability
- Completed and removed `DOIT.differ_repo_flicker_ai_config.md`. Session-files repo selection now applies a short shared cutoff grace around the lookback boundary, so a secondary repo touched exactly near the 24h cutoff does not appear on one poll and disappear on the next; the fix is generic and does not special-case `ai-config` or add a frontend debounce.
- Verification: current live 8001 no longer reproduced the original `~/ai-config` blink, five authenticated forced `/api/session-files?session=1&hours=24&force=1` samples all returned only `~/yolomux.dev8001`, the boundary regression passed, full `python3 tools/check.py` passed (`CHECK PASSED in 43.15s`), and 8001 restarted from `/home/keivenc/yolomux.dev8001`.

---

Completed 2026-06-20. Extracted from the 2026-06-20 daily log.

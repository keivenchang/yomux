# DOIT.p1.differ-deadline-attribution.md - Preserve Differ Data And Reject False Changes

Source provenance: `DOIT.p2.md` P2-B, the former `DOIT.p2.differ-deadline-attribution.md`, and screenshots 002-003. This is P1 because it displays confidently wrong or empty user data, not merely slow diagnostics.

## Goal

Differ retains valid prior data after a deadline and lists only real, relevant changed files rather than replacing success with empty state or expanding transcript artifacts into full repository snapshots.

## Context

- The five-second deadline currently replaces a prior result with `0 repos, 0 files changed`.
- Raw historical JSONL/tool output containing patch markers can become false edits; expired agent paths become user-facing root-gone noise.
- Screenshot 002 captured the deadline fallback replacing content with empty state. Screenshot 003's 229 files came from historical transcript attribution expanding to repository snapshots; later session-files calls succeeded, so this did not establish watchd corruption.

## Ownership And Parallel Lanes

- Lane A owns only last-good/error state and rendering. Lane B owns only transcript attribution, patch extraction, and repository/path filtering. They may be implemented and tested by separate agents because their source owners are distinct.
- One integrator owns the final Differ payload contract and composed browser fixture. Filesystem descriptor authorization remains in `DOIT.p0.filesystem-descriptor-authorization.md`; generic timing instrumentation remains in `DOIT.p2.latency-boundaries.md`.

## Plan

- [ ] Last-good lane: on a typed deadline or non-pending failure, retain/render the last valid Differ payload with an error; render empty only when no payload has ever succeeded, and reject stale late success by request/source generation.
- [ ] Attribution lane: restrict dirty-snapshot expansion to live pane/CWD or explicit selected/ref repositories; transcript-only roots may show only existing attributed touched paths.
- [ ] Parser lane: parse Codex JSON before patch extraction and accept patch markers only from actual patch-tool input, never arbitrary tool output, diagnostic text, or fork history.
- [ ] Path lane: suppress expired transcript-only infrastructure paths while preserving actual user-file changes, refs, repository authorization, and committed/deleted/untracked fallback behavior.
- [ ] Compose the two lanes in deterministic Node/pytest/browser fixtures, rebuild generated assets, run the canonical gate, restart the active dev server, and reproduce the former screenshot geometries.

## Gotchas

- Do not convert a deadline into a successful empty payload, reopen every historical repository, scrape raw patch-looking text, or blame watchd without a matching revision/request correlation.
- Do not add a second filesystem authorization owner in Differ; consume the descriptor-bound contract when that P0 lands.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, exact Node/pytest node IDs, commands/exit codes, fixture repositories/transcripts, source/request generations, and `/tmp` browser artifacts; each red-first fixture captures the wrong pre-fix row set.
- [ ] A deterministic deadline fixture first renders one repository with one file, then returns the typed deadline; the same row remains visible with an error, while a cold deadline renders zero files plus the typed error, and a stale late success cannot overwrite a newer payload.
- [ ] A fixture with one existing transcript-attributed touched path and twenty unrelated dirty paths renders exactly one path; actual patch-tool input adds its named path, while identical markers in arbitrary tool output, diagnostic text, and fork history add zero.
- [ ] Historical transcript-only missing roots produce zero root-gone errors and zero rows, while live pane/CWD and explicitly selected/ref repositories preserve their exact authorized changed-path, committed/deleted/untracked, and refs-fallback rows.
- [ ] Each lane passes its focused regression independently; `node tests/layout_url.test.js`, `python3 -m pytest -q tests/test_browser_finder.py tests/test_browser_finder_fs_repro.py`, `python3 tools/check.py --lane pytest-browser-behavior`, `python3 tools/static_build.py --check`, and an unmodified `python3 tools/check.py` all exit 0 on the composed HEAD.
- [ ] After restarting the active dev server, record PID/CWD/HEAD/served bundle and reproduce both former states at 1440x900: the deadline view retains its one-row last-good payload plus error, and the historical-attribution view contains only the exact expected path set with no 229-file expansion.

## Completion

Summarize the two independently landed lanes and the composed screenshot evidence in `docs/DONE/`, then remove this queue.

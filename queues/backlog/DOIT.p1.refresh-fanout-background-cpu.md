# DOIT.p1.refresh-fanout-background-cpu.md - Bound Chrome Fan-Out And Background CPU

Source provenance: `DOIT.p1.md` P1-B, the former `DOIT.p1.refresh-fanout-and-background-cpu.md`, and the valid measurement/refresh items from the former sibling `DOIT.performance-fixes.md`.

## Goal

One browser demand produces one bounded owner action per qualified key and source generation, and unchanged page/watch activity consumes no recurring Finder, transcript, metadata, stats, or pane-capture CPU.

**Scope note 2026-08-15:** pane-capture *cadence* (backing off polling on quiet sessions) shipped in v0.7.6 and is archived in `docs/DONE/2026-08/DONE.0-7-6-performance-release.md`. What remains here is capture de-duplication and the other named owners.

## Context

- The captured page load opened 22 calls with concurrency 13; one hot window recorded 488 API observations, 440 SSE frames, and 117 operation waits while web/jobd/statsd/statusd each reached substantial CPU.
- Exact request-ID joins ruled out route-local CPU for two slow roots requests, so client wall time alone must not name a server owner. A previous profile was also inadmissible because it recorded 162 samples and 222 sampling errors.
- Known candidate owners are startup fan-out, repeated roots renewal, watchd revision follow-up, jobd work graphs/transcripts/provider metadata, statsd unchanged coverage/no-data cells, statusd pane capture/classification, response `deepcopy`, and an uncapped Finder batch that falls back to one request per directory above 64 entries.
- Activity-summary production remains disabled by the shipped baseline and its replacement belongs only to `DOIT.p1.activity-summary-async-replacement.md`. Network-filesystem watch admission belongs only to `DOIT.p1.native-watch-network-fs.md`. Generic push-body protocol changes belong only to `DOIT.p2.sse-payload-delivery.md`.

## Parallel Ownership

- First, one coordinator freezes the workload, source-generation keys, owner counters, and admissible profiling command. No optimization starts from the old unmatched profile.
- After that baseline, four code lanes may run in parallel: browser/refresh coordination; watchd/session-files invalidation; jobd metadata/transcripts; statsd dirty cells; and statusd pane snapshots. Each lane owns only its named module family. One integrator owns shared `app.py`, HTTP wiring, and final frontend composition so agents do not concurrently edit the shared parent.
- Each lane must land and pass its focused tests independently. The composed Chrome/gate evidence is serial and is invalidated by any later relevant edit.

## Plan

- [ ] Freeze one deterministic workload and measurement schema: authenticated cold page load, ten identical roots renewals, ten operation add/remove cycles, ten unchanged watchd revisions, one EventSource reconnect, one producer restart, exact request IDs, source-generation keys, per-owner invocation counters, and an on-CPU capture whose sample-error ceiling is declared before collection.
- [ ] Browser/refresh lane: add one startup/refresh coordinator that deduplicates equivalent roots/metadata/Finder/terminal/stats demand, bounds startup API concurrency, keeps one global EventSource identity, classifies every declared watch root by the surface that requested it, and chunks Finder filesystem batches to at most `MAX_FS_BATCH_REQUESTS = 64` without per-directory fallback fan-out.
- [ ] Watchd/session-files lane: explain and fix the measured roughly 12-second polling when the local native watcher should select the 300-second reconcile path, advance the native event/touched-root generation before any child-enumeration reduction, and make unchanged revisions perform zero session discovery, transcript-tail scans, or session-files payload builds while preserving bounded loss reconciliation.
- [ ] Jobd lane: cache or incrementally update work graphs, transcript discovery, and provider metadata by explicit source generation; ten unchanged revisions invoke none of those owners and one changed input invokes only its owner once.
- [ ] Statsd lane: narrow materialization to dirty coverage/no-data cells without changing ring schema, raw retention, or cache ownership; representation/storage decisions remain in `DOIT.p2.stats-ring-followups.md`.
- [ ] Statusd lane: reuse one pane capture/classification snapshot across status consumers by source signature; unchanged panes cause zero recaptures and one changed pane causes exactly one recapture. This item is de-duplication only — one capture serving many consumers. The complementary activity-tiered cadence shipped in v0.7.6 and must remain green.
- [ ] Integration lane: audit correctness-sensitive `deepcopy` and encoding on retained response bodies, migrate only proven duplicate copies to one immutable/copy-on-write owner, compose all lane counters, and run focused owner tests, browser tests, the canonical gate, and the matched 75-second Chrome measurement.

## Rejected Shortcuts

- Do not serialize all browser work, add sleeps, weaken concurrency, replace push with unbounded polling, or call lower request latency a CPU reduction.
- Do not cache the final `_path_is_secret` result by lexical path, globally replace `_path_is_within` with string prefixes, or pass `child_limit=0` before native events advance the watch token. Those earlier proposals can widen a security boundary or suppress real content changes.
- Do not drop a declared watch root before mapping it to its requesting surface; Modified-files repositories and displayed-file parents are deliberate demand.
- Nested profiling bytes/timing are not network totals, and browser queue/connect time is not route CPU.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, workload, profiler command/rate/duration/thread flags, sample/error counts and admissibility threshold, every producer key/source generation, exact node IDs, commands/exit codes, net non-generated lines, and `/tmp` request/SSE/CPU evidence.
- [ ] The deterministic workload asserts one global EventSource identity, startup API concurrency at most eight, at most one in-flight owner per qualified key/generation, no stale overwrite, complete reconnect/restart repair, and Finder batches of 64 or fewer with zero one-request-per-directory fallback.
- [ ] Across ten unchanged watchd revisions, counters for session discovery, transcript-tail scan, session-files materialization, jobd work-graph rebuild, provider-metadata rebuild, statsd unchanged-cell materialization, and statusd unchanged-pane capture each increase by exactly zero; one changed input increments only its named owner once.
- [ ] Native-watch tests prove content edits with unchanged directory mtime still advance and publish one logical revision before child enumeration is reduced, and the unhealthy/network fallback still detects the same edit; `DOIT.p1.native-watch-network-fs.md` remains the owner of mount classification and real NFS acceptance.
- [ ] Each parallel lane has a focused red-first regression and independent green result before composition; `node tests/layout_url.test.js`, `python3 -m pytest -q tests/test_browser_boot.py tests/test_browser_finder.py tests/test_browser_layout.py tests/test_metadata.py tests/test_activity.py`, `python3 tools/check.py --lane pytest-browser-behavior`, and an unmodified `python3 tools/check.py` all exit 0 on the unchanged composed HEAD.
- [ ] After restarting the active dev server, a matched 75-second authenticated Chrome run records PID/CWD/HEAD/served bundle, exact joins and unmatched denominators, startup API concurrency at most eight, no duplicate in-flight owner, no process at or above 100% CPU for six consecutive five-second samples, one stable EventSource except during the deliberate reconnect, final UI state equal to the last source generation, and zero new unallowlisted Warning/Error records.

## Completion

Archive one composed result in `docs/DONE/` and remove this umbrella only after every lane and the final composed evidence pass. A lane that discovers a different conflict group creates a separately named queue instead of expanding this file.

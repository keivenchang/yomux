# DOIT.p1.e3.jobd-reload-fanout-admission.md - Reload Fanout Temporarily Rejects Live Jobd Work

## Goal

Keep a full-page reload and its overlapping filesystem requests from producing `jobd.produce` `service_unavailable` errors when the browser is routed to the correct live daemon.

## Context

- The 2026-08-18 05:01:38-05:01:44 PT burst is not stale-orphan routing. The p7772 web process PID 25102 and its exact jobd PID 18465/socket `/tmp/y1776734304/p7772/runtime/services/jobd.sock` were alive throughout it.
- `/api/ping` remained 200. The failing watch-diff requests first returned 202, their operations then reported `jobd.produce` 503, and the same route recovered to 200 at 05:01:44.
- The burst begins with a full-page reload and overlapping watch-diff, session-files, and filesystem-batch work. That correlation narrows the reproducer but does not yet prove whether the first defect is browser fanout, daemon admission, worker saturation, or operation completion.
- This is separate from `DOIT.p1.e2.orphaned-local-service-daemons.md`; do not close either queue with evidence from the other.

## Plan

- [x] Reproduce the exact reload fanout against an isolated server: freeze first-attempt counts for watch-diff, session-files, filesystem-batch, job submission, and operation completion, then identify the first incorrect transition before changing code. DONE: the browser audit measured bounded owners for the distinct reload products; the isolated capacity-one backend repro then froze the causal duplicate watch-diff case at two attempts, one accepted operation, one job submission, zero terminals, and statuses `[202, 503]`. The first incorrect transition was the second cold cache miss reserving completion capacity before the existing jobd product identity could coalesce it.
- [x] Route equivalent reload-triggered filesystem work through the existing shared refresh/job owner, coalescing only semantically identical work and preserving distinct requests; do not hide an admission defect with retries, sleeps, serialization, or a larger timeout. DONE: `JobdOperationService` now owns one bounded keyed in-flight claim per lane and semantic product key; watch-diff shares only the raw producer outcome, while each caller keeps its own request ID, durable receipt, terminal result, and acknowledgment lifecycle. The existing whole-request key includes roots, token/generation identity, and the filesystem access-policy digest; distinct keys remain independent and mutations are unchanged.
- [x] Make live jobd admission and operation completion remain available under the bounded reload fanout, with typed first-attempt evidence showing no `service_unavailable` response or lost operation receipt. DONE: the formerly-red capacity-one request pair now returns `[202, 202]`, owns one flight and one job submission, publishes zero terminals before release, then publishes two distinct 200/ready terminals. The restarted 7771 deterministic workload completed ten session-files receipts with ten terminals and ten acknowledgments, and its server log contained zero jobd or watch-diff `service_unavailable` records.
- [x] Add the exact red-first regression plus a property-derived concurrent-fanout case that varies request ordering and proves immediate convergence after the reload. DONE: `test_equivalent_inflight_filesystem_watch_diff_requests_share_one_completion` failed first on the second 503; the two ordering variants then proved two semantic keys own exactly two flights/submissions while four request-specific receipts converge to the correct per-key products.
- Audit follow-up: the cache-recheck regression failed red because the owner discarded the new flight after a follower joined it; the owner now resolves the shared future before releasing the flight, and the exact cache-recheck test, 14 watch-diff tests, and 23 route-contract tests pass with every follower terminalized under its own IDs.

## Gotchas

- A later isolated pass does not make the original failed delivery pass; record first-attempt submission and completion separately from recovery.
- A failing test is real until a named contended resource or measured host limit proves otherwise.
- Do not attribute this burst to stale daemons: the correct p7772 jobd owned the socket during the failure.
- Use isolated fixture state, sockets, ports, locks, and tmux namespaces. Do not exercise or restart production 7770.

## Done Criteria

- [x] The exact reload journey produces no `jobd.produce` `service_unavailable` event and every accepted 202 operation reaches one terminal result. DONE: restarted 7771 PID 1142832 served the byte-identical bundle; `/tmp/y7771-jobd-fanout-20260818.json` recorded one authenticated cold load, ten watch-root renewals, ten unchanged watch-diff revisions, ten accepted session-files operations, ten terminal results, ten acknowledgments, zero pending operation waiters, and zero browser warning/error records. The post-journey server-log query found zero jobd/watch-diff 503s.
- [x] The covering focused pytest/browser lanes pass; the full gate remains the separately recorded landing check under the tiered evidence policy. DONE: exact and property tests passed, the cache-recheck audit test plus 14 watch-diff and 23 route-contract tests passed, the two-client reload browser journey passed 1/1, and the browser ownership shard passed 169/169. All nine functional lanes later passed on candidate `80b4bf8ce`; exact-SHA certification was NOT CERTIFIABLE under measured shared-host pressure and is not claimed as passed.

## Completion

The historical burst was not caused by stale daemons or excessive browser fanout. Equivalent cold watch-diff requests independently consumed web completion slots before reaching jobd's existing coalescing key. One generic `JobdOperationService` flight owner now shares that raw work while preserving every caller's separate durable operation lifecycle; no retry, delay, timeout increase, or browser serialization was added.

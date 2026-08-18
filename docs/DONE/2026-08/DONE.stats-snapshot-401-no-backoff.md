# DOIT.p1.e1.stats-snapshot-401-no-backoff.md - A 401 Does Not Stop The Snapshot Poller

Reported 2026-08-18 from the in-app Logs view.

## Evidence

Roughly 70 `authentication_required` errors on `GET /api/stats-snapshot`, at **2 per second for a full minute** (22:53:41 to 22:54:11 PT on 2026-08-17). A single `authentication_required` on `GET /api/ping` at 22:53:17 precedes them by 24 seconds, so the session was already dead and the poller never noticed.

## Cause

`fetchSnapshot` in `static_src/js/yolomux/84_stats_current.js:934` branches on exactly two error shapes:

```js
} catch (error) {
  if (error?.recoverableReadFence === true) await recoverReadFence(error);
  onState(error.pending === true ? 'pending' : 'error', error);
  throw error;
}
```

There is **no 401 or `authentication_required` handling anywhere in that file** (grepped). An unauthenticated client therefore re-polls at full cadence indefinitely. A 401 is not transient: retrying it at 2 Hz cannot succeed and only generates load and log noise against a server the client has no right to talk to.

## Plan

- [x] Stop the poll on `authentication_required`. Treat it as terminal for the stream, not as a transient error, and surface the signed-out state rather than looping. DONE: the existing login/auth transition now owns one terminal latch; the first typed 401 publishes the signed-out state and retires the current-stats client before any readiness or repair timer can reschedule it.
- [x] Check the sibling pollers through the same owner. `/api/ping`, `/api/client-events`, `/api/stats-capabilities` and the stats stream are all long-lived; confirm each stops or backs off on 401 rather than fixing only this call site. DONE: one `long-lived-browser-transports` retirement clears ping and debug-stats intervals; stops snapshot, stats SSE, transcript, summary, and dev-reload streams; and disables/closes active and replacement client-events sources. Active and pre-ready candidate EventSource failures each run one bounded authenticated ping probe that reaches the same latch.
- [x] Add a regression that returns 401 to the snapshot poller and asserts it issues no further request. Seen to fail first. DONE: the red test made 234 snapshot requests over 60 simulated seconds before the fix; it now makes one total request, with the focused stats UI suite passing 58/58.

## Done Criteria

- [x] A 401 produces at most one further request, proven by a test that was red before the fix. DONE: snapshot and capabilities each stop at one request, stats SSE performs one authenticated repair read before retirement, ping blocks the second caller before fetch, and active/candidate client-events each perform one bounded auth probe before all direct EventSources retire; focused Node suites passed 58/58, 79/79, and 169/169.
- [x] The user sees a signed-out state instead of a silent retry loop, confirmed in a real browser. DONE: real Chrome drove the actual `ensureJsDebugCurrentStatsClient()` through production `apiFetch`, observed one snapshot request across 1.2 seconds, and measured body state `signed-out`, visible `Authentication required.`, and `role=alert`; `python3 -m pytest tests/test_browser_stats_widen.py -k 'stats_authentication_expiry_paints_signed_out' -q` passed 1 test.
- [x] Canonical functional gate green; record exact-SHA certification separately. DONE: all nine functional lanes passed on candidate `80b4bf8ce`, including the full Node, non-browser, browser, E2E, static, syntax, compile, whitespace, and serial timing owners. Two certification-only attempts were NOT CERTIFIABLE because the shared host exceeded measured I/O/CPU stall limits; no certification pass is claimed. This recorded exception follows Keiven's tiered evidence policy and does not create a tag, push, or production restart claim.

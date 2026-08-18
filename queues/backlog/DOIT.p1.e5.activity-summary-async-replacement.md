# DOIT.p1.e5.activity-summary-async-replacement.md - Replace Summary With A Retained Asynchronous Product

Source provenance: `DOIT.p1.md` P1-A and the former `DOIT.p1.e5.activity-summary-async-replacement.md`.

## Goal

Replace the now-shipped disabled activity-summary route with a bounded, coalesced retained product that never makes an HTTP request thread wait for statusd assembly.

## Context

- Current route -> app -> statusd RPC can wait 60 seconds; statusd serializes builds under `activity_lock`.
- Reuse existing receipt/status/replay/SSE owners, but do not put summary bodies in terminal events or make normal EventSource identity depend on pending operations.
- Current activity-summary route path is `http_routes.py` -> `app.py` -> `statusd_client.py`; its client RPC deadline is 60 seconds. Statusd allows multiple RPC handlers but serializes assembly under `activity_lock`, and observed HTTP routes remained inside the route for about 60.1 seconds before `424`.

## Ownership Boundary

This lane owns the retained activity-summary producer, receipt/result protocol, and browser consumption. The disabled baseline is archived in `docs/DONE/2026-08/DONE.0-7-7-active-queue-reconciliation.md` and remains asserted until this queue's final re-enable; generic browser refresh/load work stays in `DOIT.p1.e5.refresh-fanout-background-cpu.md`.

## Execution Order And Ownership

- Start only after the P0 disable queue is complete. One protocol owner freezes selector/generation/envelopes and owns shared receipt/retention wiring; producer, browser-consumer, and adversarial-test agents may work in parallel only after that contract is fixed.
- The disable remains on through all implementation and tests. One final serial re-enable step is allowed only after every Done Criterion passes on the same HEAD; a partial producer or green focused test never enables demand.

## Plan

- [ ] Define a canonical authorized selector, exact source-generation vector, retained last-good metadata/bytes, and typed ready/queued/failed envelopes; `force` is admission intent, not a product key.
- [ ] Build one statusd coordinator that owns per-selector single-flight work, one latest replacement generation, stale-while-revalidate reads, integrity-checked retention, and failure metadata; no RPC handler may discover sessions or assemble/encode the product.
- [ ] Move only a pure bounded materialization input to jobd or a dedicated worker; forbid recursive statusd/jobd/tmux/Git/network calls from that task and add generation fencing for obsolete completions.
- [ ] Adapt HTTP to immediate retained `200` or durable `202` receipt, one shared producer follower/fanout, restart reattachment, and metadata-only `activity_summary_ready`/terminal SSE; browser fetches one retained body and guards selector/version.
- [ ] Remove the legacy statusd 60-second build-under-lock path, app future/compute duplicates, force cache clearing, and watcher production only after negative tests prove every request/watch/search/YO!agent consumer reads retained state.
- [ ] Add protocol, coalescing, fencing, last-good, failure, restart, cancellation, authorization, payload-size, browser reconnect, and composed Chrome-soak tests from `docs/specs/ASYNC_INTERACTION.md`.

## Gotchas

- `async def` around blocking RPC is not asynchronous product work.
- Running process-pool work cannot be reliably cancelled; use supersession/generation fencing.
- Share access must remain denied for activity product, operation status, and event stream.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, exact node IDs, commands/exit codes, selected selector/source-generation fixture, numeric response/body limits, and `/tmp` evidence paths; the disable queue must already be complete and its zero-demand tests must remain green until this queue's final re-enable step.
- [ ] A deterministic latch test in `python3 -m pytest -q tests/test_activity_summary.py` blocks materialization indefinitely while a cold authorized request returns a durable `202` receipt within 250 ms and a concurrent statusd health request completes within 250 ms; no HTTP/statusd RPC handler performs discovery, assembly, or encoding.
- [ ] Thirty-two simultaneous identical requests yield exactly one materializer invocation, 32 durable receipts following one qualified selector/generation, and exactly one terminal outcome per receipt; outstanding receipt count reaches zero, duplicate terminal count remains zero, and terminal SSE carries metadata only.
- [ ] Deterministic tests cover current and obsolete completions, one latest replacement generation, stale-last-good, dependency failure, invalid empty output, cancellation/supersession, producer restart and follower reattachment, missed-SSE repair, authorization denial, and payload-size rejection; old completions never overwrite current bytes and every accepted receipt terminates once.
- [ ] `node tests/layout_url.test.js`, `python3 tools/check.py --lane pytest-browser-behavior`, `python3 tools/static_build.py --check`, and an unmodified `python3 tools/check.py` all exit 0; ten operation add/remove cycles leave one unchanged global EventSource identity and cause zero reconnects.
- [ ] After restarting the active dev server, record PID/CWD/HEAD/served bundle and run a ten-minute authenticated Chrome sequence of cold request, 32 forced refreshes, disconnect/reconnect, and producer restart; every pending state clears, the final selector/version matches retained bytes, and zero new unallowlisted Warning/Error records appear before re-enable.

## Completion

After all evidence passes on one unchanged HEAD, re-enable through the shared admission owner, repeat the focused disabled-to-enabled and Chrome checks, summarize the retained asynchronous contract in `docs/DONE/`, and remove this queue.

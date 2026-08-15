# DOIT.p2.latency-boundaries.md - Attribute Common Multi-Second Delays Correctly

Source provenance: `DOIT.p2.md` P2-C and the former `DOIT.p2.latency-boundaries.md`.

## Goal

Every browser API timing can be joined to true server and browser phases so common-boundary delay is not misattributed to a route.

## Context

- Exact joins show roots/ping/background handlers can run in tens of milliseconds while Chrome sees seconds.
- Current `accept_to_route_ms` includes deliberate keep-alive request-line idle time and is not a queue metric.
- Browser request `r-web-msg9kanc-iq` waited 7806 ms while its retained filesystem operation ran 415 ms; across 118 operations median work was 34.1 ms and 108 completed within 500 ms.

## Ownership Boundary

This lane owns the phase schema, browser/server correlation, and evidence accounting. It instruments but does not implement route-specific load reduction, Differ recovery, or SSE payload redesign.

## Parallel Ownership

- One schema owner first freezes the phase names, clock domains, request-ID rules, privacy fields, and denominator accounting.
- After the schema is frozen, a server-instrumentation agent and a browser-instrumentation agent may work in parallel in separate module families; a third read-only agent may build the join oracle and adversarial fixture. One integrator owns the shared schema and final composition.
- Differ correctness can proceed independently. Refresh/CPU and SSE work may use these metrics, but neither may wait for this queue when a deterministic owner counter already proves its defect.

## Plan

- [ ] Add request-ID-correlated timestamps for socket accept, request-line complete, route start/end, first/last response byte, connection reuse, and transport failure; preserve privacy and avoid logging credentials.
- [ ] Add browser `PerformanceResourceTiming`/fetch phase capture for queue/stall, DNS/connect/TLS, request, response, decode, apply, and paint with a shared schema.
- [ ] Join the two streams with explicit coverage/unmatched accounting; identify reused connections and prove the model never treats request-line idle as queue delay.
- [ ] Add deterministic tests for warm reused connection, delayed accept/route/write, client render delay, timeout, and request-ID collision/missing cases.
- [ ] Measure `background/status`, `auto-approve`, roots, ping, watch-diff, and filesystem batch with the same joined schema before attributing their multi-second browser totals to route compute; keep `normal_session_local_service` as a test inventory marker or rename/delete it, but never turn that marker into runtime offload behavior.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, exact node IDs, commands/exit codes, phase schema/version, and `/tmp` raw server/browser/join artifacts; every summary reports total server records, total browser records, unique joins, duplicates, server-only rows, and browser-only rows.
- [ ] Deterministic fixtures inject 200 ms separately at accept-to-request-line, route execution, response write, browser queue/stall, decode/apply, and paint; each joined record attributes the injected phase within plus or minus 25 ms, every non-injected phase remains below 50 ms in the fixture, and deliberate keep-alive request-line idle is labelled idle rather than queue delay.
- [ ] Warm reused connection, timeout, transport failure, missing request ID, and duplicate/colliding request ID fixtures produce one unique join or an explicit unmatched/ambiguous row; no record is silently dropped or multiply counted, and all denominators sum back to the input counts.
- [ ] `python3 -m pytest -q tests/test_browser_layout.py tests/test_gate_contracts_q.py`, `node tests/layout_url.test.js`, `python3 tools/check.py --lane pytest-browser-behavior`, and an unmodified `python3 tools/check.py` all exit 0.
- [ ] After restarting the active dev server, record PID/CWD/HEAD/served bundle and run one controlled authenticated Chrome load with one 500 ms injected server or client phase; the joined evidence identifies that phase within plus or minus 50 ms and any recommendation names a route/service owner only when its matching phase accounts for the delay.

## Completion

When the schema, fixtures, joined live evidence, and canonical gate are complete, record the schema/version and measured owner conclusions in `docs/DONE/` and remove this queue. Route fixes discovered by the evidence require separately named implementation queues.

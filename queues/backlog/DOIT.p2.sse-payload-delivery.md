# DOIT.p2.sse-payload-delivery.md - Reduce Push Payloads And Prove Graph Delivery

Source provenance: `DOIT.p2.md` P2-D and the former `DOIT.p2.sse-payload-delivery.md`.

## Goal

Large `operation_terminal` and watch-diff push frames do not make graph or operation state stale after source work completes.

## Context

- Captured `operation_terminal` receive latency reached 1076.6 ms; watch-diff/terminal frames contributed multi-hundred-KiB transfers.
- Push health must remain visible and recorded even when YO!stats is hidden.
- Seven SSE frames exceeded 64 KiB during the hot period; six were roughly 210-246 KiB. `tmux_signals_changed` receive latency reached 798.5 ms.

## Ownership Boundary

This lane owns push payload shape, terminal cursor/repair semantics, and graph delivery under load. It consumes phase metrics from `DOIT.p2.latency-boundaries.md` and must not absorb generic page-load coordination.

## Execution Order And Parallel Ownership

- One protocol owner first freezes envelope version, cursor/generation rules, maximum frame/chunk/body sizes, retained-result identity, and repair semantics.
- After that freeze, producer agents for operation/watch-diff and tmux/auto-approve notifications plus one browser-consumer agent may work in parallel in separate module families. One integrator owns shared SSE framing and final cursor composition.
- Phase metrics from `DOIT.p2.latency-boundaries.md` improve attribution but do not block deterministic byte/cursor tests. Generic refresh deduplication remains in `DOIT.p1.refresh-fanout-background-cpu.md`.

## Plan

- [ ] Inventory terminal/watch-diff/tmux-signal/auto-approve/SSE payload producers and consumers, then replace full repeated bodies with versioned metadata, deltas, retained-result URLs, or bounded chunks through one shared protocol owner.
- [ ] Preserve exactly-once terminal cursor handling, missed-event repair, reconnect behavior, ordering, and UI state while coalescing simultaneous terminal/ready notifications into one retained fetch.
- [ ] Instrument serialized bytes, delivery latency, client apply time, dropped/coalesced frames, and result-fetch count without counting nested profiling records as network bytes.
- [ ] Add large multi-session/operation fixtures and browser tests that prove graph data arrives while YO!stats is hidden and becomes current when later opened.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, exact Node/pytest node IDs, commands/exit codes, protocol version, maximum frame/chunk/body values, and `/tmp` before/after frame plus browser-timing artifacts; nested profiling bytes are reported separately and never counted as wire bytes.
- [ ] The large deterministic fixture uses eight sessions and 32 simultaneous operations with 1 MiB retained results; no SSE frame exceeds 65,536 serialized bytes, each retained body is fetched at most once per qualified result generation, and every frame/body record reports exact serialized, representation, and wire bytes.
- [ ] Cursor tests prove exactly one terminal apply per operation, ordered version/generation handling, one bounded repair after a dropped frame, reconnect resume, stale-frame rejection, coalesced simultaneous terminal/ready notification, and final graph/operation state equal to the source generation with zero lost or duplicate applies.
- [ ] `node tests/layout_url.test.js`, `python3 -m pytest -q tests/test_browser_layout.py tests/test_activity.py`, `python3 tools/check.py --lane pytest-browser-behavior`, `python3 tools/static_build.py --check`, and an unmodified `python3 tools/check.py` all exit 0; before/after evidence includes delivery and client-apply p50/p95/max.
- [ ] After restarting the active dev server, record PID/CWD/HEAD/served bundle and run a ten-minute authenticated Chrome soak repeating the eight-session/32-operation fixture with YO!stats hidden then opened; every operation terminates once, graph state is current, no apply is duplicated, no result remains pending, no SSE delivery-plus-apply exceeds one second after source completion, and zero new unallowlisted Warning/Error records appear.

## Completion

Summarize the protocol version, byte limits, cursor/repair proof, hidden-graph delivery, and soak in `docs/DONE/`, then remove this queue. A producer-specific defect outside the shared protocol becomes its own queue.

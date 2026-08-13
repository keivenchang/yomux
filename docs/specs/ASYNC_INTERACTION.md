# Async interaction contract

This document is the normative contract for every browser request whose result is owned by a YOLOmux daemon. A route is conforming only when its server behavior, browser behavior, failure states, telemetry, and reconnect behavior satisfy this document.

## Core rule

The web process authenticates and authorizes the request, validates bounded metadata, and asks the owning daemon for an already-retained value or confirmed acceptance. The web process never waits for daemon production, polls from an HTTP handler, sleeps in an HTTP handler, or materializes a daemon-owned product as Python objects.

Daemon acceptance is a bounded control-plane round trip, not permission to perform the operation synchronously. Acceptance confirmation is required: returning a receipt before the daemon accepts would turn a daemon that is not running into an abandoned promise. The accept exchange may validate bounded metadata and enqueue work; it may not wait for the product.

A request returns a usable value now when the daemon already retains the exact matching product and can relay it without performing work. The daemon decides whether the product is warm; a web-process cache must not become a parallel product owner. A warm relay may return `200`. If the answer must be scanned, assembled, refreshed, selected by running a subprocess, or otherwise produced, the daemon accepts it and HTTP returns `202` even when that work happens to take only 5 ms. The test is whether the request waited for work, not elapsed time.

A warm relay does hold a request handler while bytes cross the local socket, so transport safety is part of correctness. Synchronous relay is allowed only when the ready-byte wait is bounded, cancellation-safe, and cannot retain handler capacity after browser disconnect. If those properties are absent or unproven, the route uses the accepted-operation path even for a retained product. A short bounded relay timeout is a typed daemon timeout; it must not fall through to synchronous production.

First boot with neither a retained product nor an eligible last-known-good product is cold work. After confirmed daemon acceptance it returns `202` and later terminalizes through the shared operation path; it never returns `200` with successful-empty data.

## Allowed route shapes

Every route maps to exactly one of these shapes:

1. **Retained snapshot relay:** one bounded local-daemon read returns already-retained opaque bytes without production, decode, defensive copy, alias materialization, or re-encoding. The daemon response declares the retained identity and readiness. The relay wait satisfies the disconnect-cancellation and handler-capacity rule above.
2. **Byte-streaming relay:** the web process forwards an established stream with bounded buffering, backpressure, disconnect cancellation, and a typed terminal.
3. **Accepted operation:** the daemon confirms enqueue, HTTP returns `202`, and the existing shared client-event stream delivers exactly one `operation_terminal` event.

Public HTML, static assets, uploads, and other explicitly non-daemon routes keep their route-specific contracts. Authentication and authorization remain inline and complete before the web process asks a daemon for retained bytes or acceptance. PTY and WebSocket byte paths remain synchronous because low-latency byte delivery is the product; they keep their bounded-buffering, backpressure, and disconnect-cancellation contracts instead of being converted to operation receipts. These routes must not be forced through an operation receipt merely to make the route inventory uniform.

## Receipt contract

An accepted operation returns HTTP `202` with the canonical queued envelope:

```json
{
  "state": "queued",
  "request": {"id": "r-..."},
  "operation": {
    "id": "op-...",
    "kind": "activity_summary",
    "deadline_at": "2026-08-04T20:00:10Z",
    "status_url": "/api/operations/op-...",
    "events_url": "/api/client-events?operation_id=op-...",
    "cursor": {"epoch": "...", "seq": 0},
    "progress": {"phase": "accepted", "producer": "statusd"}
  },
  "ok": true,
  "terminal": false
}
```

The request ID is the browser-to-web correlation ID. The operation ID is the durable completion identity. `kind` selects the existing client completion handler. `deadline_at` lets the client display an honest bound. `status_url` repairs a missed event. `events_url` names the existing shared client-event transport and must not cause a feature-local EventSource for a normal authenticated page. The cursor makes reconnect, coalescing, supersession, and stale-event rejection testable. `progress` must contain enough bounded state to render what the daemon is doing without exposing paths, queries, secrets, or an unbounded payload.

The receipt is persisted before HTTP exposes it. A `202` without a persisted operation and a completion path is a protocol defect. A daemon rejection, unavailable daemon, full acceptance queue, authorization failure, or invalid request returns a typed terminal HTTP error and never returns a receipt.

The shared response parent records the receipt or terminal metadata in `QueuedDeliveryLedger` before it frames or writes opaque bytes. Byte forwarding must not bypass the ledger, and ledger observation must not decode the retained product merely to classify the lifecycle state.

## Completion and repair

The daemon owns production and retains canonical domain/product JSON bytes. Retained product bytes do not contain the per-request HTTP envelope. The web process remains the sole owner of `request.id` and frames opaque product bytes with a bounded prefix and suffix. It may splice the retained object's interior bytes to preserve established top-level aliases, but it must not call `json.loads`, `deepcopy`, or `json.dumps` on the product.

An accepted operation is discharged exactly once by `operation_terminal` on the shared `/api/client-events` SSE connection. The terminal carries the operation ID, a cursor with the same epoch and a higher sequence, HTTP-equivalent status, and either a ready result reference or a canonical failed result. Large retained products may be fetched through the operation status/product URL after the small terminal notification; that follow-up is a retained snapshot relay and must not call the daemon from the request thread.

The operation status endpoint returns the same queued receipt before completion and the same terminal state after completion. On SSE reconnect the client includes outstanding operation IDs, the server replays retained terminals when available, and any dropped or coalesced notification records the affected resource for repair. A newer operation supersedes UI ownership of an older one; a late older terminal remains recorded but must not overwrite the newer rendered state.

## Deadline ownership

The daemon owns the authoritative production deadline: 10 seconds from confirmed acceptance unless a route specification defines a shorter bound. Only the daemon can honestly say its work took too long, so it emits the `deadline_expired` terminal when the production deadline is exceeded.

The web completion relay may use the same absolute `deadline_at` to stop awaiting a dead producer and publish the daemon-unavailable or deadline terminal already implied by daemon state; it does not restart work or extend the deadline. The browser owns a 12-second delivery watchdog measured from receipt. The extra two seconds cover event delivery and reconnect repair. A browser watchdog expiry is recorded as `delivery_timeout`; it must not be mislabeled as daemon execution time unless the daemon supplied `deadline_expired`.

## Failure taxonomy

The following states are distinct on the wire, in telemetry, and on screen:

| Condition | Wire code | HTTP or terminal status | Required user meaning |
|---|---|---:|---|
| Daemon was unavailable before acceptance or disappeared before a terminal | `service_unavailable` | `503` | `<service> is not running` |
| Daemon accepted the operation but production failed or returned an invalid product | `producer_failed` | `502` | `<service> errored` |
| Daemon exceeded its production deadline | `deadline_expired` | `504` | `<service> timed out after 10 seconds` |
| Browser did not receive or repair a terminal by its delivery watchdog | `delivery_timeout` | client-only | `Result delivery timed out` |

Canonical failures retain `request.id`, `error.code`, `error.origin`, `error.retryable`, bounded `error.details`, and a causal stack naming the HTTP route and daemon operation. Generic `500`, generic “request failed,” successful-empty data, and a pending flag without a receipt do not satisfy this contract.

## Browser state and progress

The browser renders three state classes and no others: a value, a pending marker tied to the accepted operation and cursor, or a typed failure. A pending marker without an outstanding promise and an outstanding promise without owned pending state are both defects.

The browser records the receipt immediately and owns pending state by operation ID and cursor. It delays visible progress chrome for 100 ms. A terminal received inside that grace period renders the result without a spinner flash; after 100 ms the relevant surface shows bounded progress and `aria-busy=true`. The internal pending record exists during the grace period even when progress is not yet painted.

Ready, unavailable, producer error, daemon timeout, and delivery timeout each clear pending state and cancel the delayed progress paint. Every surface rendering the operation must settle together. A push or newer request may invalidate an older operation's render ownership, but it may not erase the older operation's telemetry or terminal record.

### Browser resource extension paths

Use `createLatestResource({initial, load, apply, onState})` only for read-only resources that need the same request dedupe, generation invalidation, last-good retention, and typed failure behavior. It is not a write transaction, retry engine, cache policy, or operation-receipt replacement. A new consumer proves delayed old success/failure after a newer target, same-target dedupe, last-good failure behavior, and exact apply/render order.

Use `createLifecycleScope()` as the owner of listeners, timers, observers, EventSources, abort controllers, and other closeable resources started by one surface or state record. Register each handle when created and dispose the scope on replacement and retirement. A new resource family proves start, replace, stale late event, stop, and page retirement leave exactly one or zero live handles as appropriate and call close/disconnect/abort once.

## Frontend recording and upload

The frontend records these lifecycle facts with the existing durable browser observation mechanism:

- acceptance: endpoint, request ID, operation ID, kind, service, accepted timestamp, and deadline;
- progress display: phase and time from receipt to visible progress;
- ready terminal: daemon service duration when supplied, client elapsed time, delivery path, response bytes, and apply/render duration;
- unavailable, producer error, daemon timeout, and delivery timeout: typed code, service, client elapsed time, and bounded causal metadata;
- reconnect and repair: cursor, missed terminal, replay source, and repair duration.

Records contain endpoint names, never query strings or path detail. They carry journey ID, code revision, and browser family under the existing browser-observation schema. Upload uses the existing stats-observation ingestion and uploader, with at most 100 observations and 120 KiB per batch. Upload is periodic and also attempted on lifecycle flush opportunities supported by the existing recorder. This specification does not authorize a second uploader, endpoint, queue, or error taxonomy.

## Acceptance tests

Each converted route must prove all of the following:

1. A warm regression proves the daemon returns pre-existing retained bytes without invoking its producer, the web forwards them without materialization, and disconnect cancellation releases handler capacity.
2. A cold regression holds the daemon producer gate, proves acceptance is confirmed, and proves HTTP returns `202` without waiting for that gate.
3. A 5 ms and a 5000 ms cold producer use the same receipt and terminal path; only the latter paints progress after the 100 ms grace period.
4. The exact retained product byte sequence is present unchanged in the framed response, and web-side `json.loads`, `deepcopy`, and product re-encoding are forbidden by the regression.
5. Legacy top-level aliases required by existing clients remain present until those clients are migrated with separate evidence.
6. Daemon unavailable, producer error, `deadline_expired`, and client `delivery_timeout` are distinguishable in the browser and recorded with their exact codes.
7. Duplicate, late, superseded, dropped, and reconnect-replayed terminals do not overwrite newer state or leave pending UI behind.
8. The status URL repairs a missed terminal without request-thread daemon work.
9. The frontend batches lifecycle records through the one existing observation uploader and respects the 100-item and 120-KiB limits.

## Prohibited designs

- Waiting for completed daemon work in an HTTP handler, regardless of measured warm latency.
- Calling a route warm because a synchronous producer happens to finish quickly.
- Holding handler capacity after browser disconnect while waiting for retained daemon bytes.
- Returning `200` with `pending`, `refreshing_elsewhere`, or successful-empty data.
- Returning `202` before confirmed daemon acceptance.
- Polling or sleeping in an HTTP handler.
- Moving wait, decode, copy, or encode work to another thread in the web process and calling that an offload.
- Creating a route-local EventSource, receipt shape, completion event, failure taxonomy, progress store, or telemetry uploader.
- Decoding retained product bytes to construct the HTTP envelope or legacy aliases.
- Clearing pending UI on an unrelated event or on a terminal for an older operation.

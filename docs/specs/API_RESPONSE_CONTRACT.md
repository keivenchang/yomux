# API response contract

> **STATUS: PARTIALLY IMPLEMENTED CONTRACT.** `QueuedDeliveryLedger` now persists accepted operations, `GET /api/operations/{id}` and `POST /api/operations/ack` provide status/acknowledgment, the shared `/api/client-events` stream publishes and replays `operation_terminal`, and session/filesystem product paths use that machinery. The route catalog does not yet enforce this envelope for every JSON route: forced session metadata still builds synchronously, `Route.normal_session_local_service` remains a test-inventory marker, and the legacy activity-summary route is disabled with typed `503 feature_disabled` until its asynchronous replacement lands. Qualified browser observations are durably uploaded through `POST /api/stats-observations`, but the aggregated failure-query endpoint below does not exist. Treat the envelope, route-wide timing bound, and diagnostic query as target contract, not evidence of global conformance; current gaps are inventoried in [`V0.7.7_IMPLEMENTATION_DISCREPANCIES.md`](V0.7.7_IMPLEMENTATION_DISCREPANCIES.md).

## Why this exists

Six distinct user-visible failures on 2026-08-02 share one root: **a response that does not say what actually happened.** Each cost hours to diagnose because the symptom appeared in the browser while the cause sat in a server log with nothing linking them.

| Incident | What the API returned | What was true |
|---|---|---|
| Differ hung on `loading…` forever | `200 OK` carrying `refreshing_elsewhere: true` | not ready; still refreshing elsewhere |
| `/api/session-files` while `jobd` was a zombie | `202 QUEUED` | the job could never be scheduled |
| 24h stats repeat selection | bare `409` with no body the browser could read | a repair was required and possible |
| `/api/auto-approve` under lock contention | bare `503` | a valid retained snapshot existed |
| jobd transport failure | `FileNotFoundError` traceback every 5-8s, forever | service dead, no terminal state |
| terminal file references | 14 `client_failure` records at error severity | speculative guesses that were never files |

The pattern in one line: **success codes carrying failure, failure codes carrying no diagnosis, and no identifier connecting either end.**

## The envelope

Every JSON API response — success or failure — is exactly this shape:

```json
{
  "state":    "ready" | "queued" | "failed",
  "trace":    { "id": "t-<uuid>", "epoch": "<uuid>", "seq": 3 },
  "data":     { },
  "progress": { "phase": "scanning", "done": 41, "total": 120, "eta_ms": 900 },
  "error":    {
    "code":      "jobd_unavailable",
    "message":   "Job broker is not running.",
    "origin":    "server:local_services.registry",
    "retryable": false,
    "detail":    { "service": "jobd", "diagnostic": "process_defunct" }
  }
}
```

- `data` present **only** when `state == "ready"`.
- `progress` present **only** when `state == "queued"`.
- `error` present **only** when `state == "failed"`.

HTTP status still carries transport meaning, but **`state` is authoritative for the caller.** A `200` with `state: "failed"` is legal and preferred over a bare `5xx`, because it carries a diagnosis.

## The eight rules

### 1. Answer immediately, always

Every handler returns within **250 ms** with one of the three states. A handler may not block on a filesystem walk, a subprocess, an RPC, or a lock. Slow work returns `queued` and continues in the background.

*This is the async requirement.* It is not "make things faster" — it is "never make the caller wait to find out you are working".

### 2. `queued` is a promise, and you may only make promises you can keep

Return `queued` **only after confirming the executor can actually run the work now** — the service is live and serving, the queue accepts, capacity exists. If it cannot, return `failed` with a typed code.

> `/api/session-files?force=1` returned `202 QUEUED` while `jobd` was a defunct process holding no socket. The browser waited for a completion that could never be published. **A queue acknowledgement is a promise; an unkeepable one is worse than a refusal**, because a refusal is actionable and a false promise is a hang.

### 3. Every `queued` reaches a terminal state, on every surface

A `queued` response creates an obligation: the same `trace.id` **must** later produce `ready` or `failed`, delivered to **every** subscriber of that trace — not only the surface that happened to be active.

> The Finder spinner and the Differ panel each had one surface receive the completion while another kept waiting. Publish to all subscribers of the trace, or the bug is structural.

Terminal states are published even when the producer crashes: the supervisor converts an abandoned trace into `failed` with `code: "producer_abandoned"`.

### 4. Never encode failure inside success

`state: "ready"` means the data is present and correct. Nothing else. A field like `refreshing_elsewhere` is a `queued` state; a partial result is `queued` with `progress`; an empty result that means "unknown" is `failed`, not an empty `data`.

> An empty success is indistinguishable from a real empty answer. `$0` for an unpriced model and an empty file list for a vanished root are the same defect: a value that cannot be told apart from a measurement.

### 5. Errors are typed and never bare

`error.code` is a stable machine token from a closed vocabulary, not prose. `error.message` is for humans. `error.origin` names the component. `error.retryable` tells the client whether retrying can possibly help — a client must never invent its own retry policy.

Forbidden: a bare `4xx`/`5xx` with no body; a stack trace as the only diagnosis; `code` values invented at the call site.

### 6. One trace id spans the whole path

`trace.id` is minted at the browser, sent with the request, threaded through the server, into the worker, and returned on every response and event for that operation. It appears in **both** logs.

Diagnosing then becomes one grep: `trace.id` → the browser action, the server handler, the worker job, the exception, the terminal state.

> This is the single biggest diagnostic win. Every incident tonight required manually correlating a browser symptom with a server exception by timestamp and guesswork. Twice I attributed a `200` to the wrong request because nothing tied them together.

`epoch` and `seq` order events within a trace so a late arrival cannot overwrite a newer state.

### 7. Severity means something

- `error` — an operation the user asked for did not happen.
- `warning` — degraded but the user's request succeeded.
- `debug` — speculative work that was expected to sometimes fail.

Speculative operations declare themselves (`speculative: true`) and their failures are **never** `error`.

> All 14 `client_failure` records in a clean session were speculative filename guesses from terminal text. Logging expected-miss guesses at error severity drowns the real signal — the reason "lots of errors" was both true and uninformative.

### 8. Client-side failures are persisted server-side

The browser posts qualified error and lifecycle observations through `POST /api/stats-observations`. `jsDebugEvents` remains a bounded page-local diagnostic ring, but it is no longer the only record: accepted observations are retained through the current YO!stats storage path and survive a page reload.

Ingest is best-effort and must never itself produce a user-visible error.

## Diagnostics surface

Target contract, not a current route: one authenticated endpoint available in production and test:

```
GET /api/diagnostics/failures?since=<ts>&limit=100
  -> { "state": "ready", "data": { "failures": [ {trace, code, origin, count, first_ts, last_ts, sample} ] } }
```

Grouped by `code` + `origin` with counts, so an agent asks one question and gets typed answers instead of grepping tracebacks. It must include client-ingested failures, so one query covers both sides.

**This is the point of the whole convention:** an agent — or a person — should be able to ask a running 7771 "what is wrong with you" and get a straight answer.

## Enforcement

A convention nobody checks is a convention nobody follows. Three guards, all gating:

1. **Envelope conformance** — a contract test enumerates every route from the router (derived, never a hardcoded list) and asserts each returns a valid envelope for success, failure, and slow paths. A new route with no envelope fails the gate.
2. **No bare status** — a guard fails on any handler returning a `4xx`/`5xx` without a typed `error` body, and on any `data` returned alongside `state != "ready"`.
3. **No unterminated trace** — a test drives real operations and asserts every `queued` trace reaches a terminal state within its deadline, including when the producer is killed mid-flight.

Each guard must be proven to fail: break the contract deliberately, confirm red, restore, confirm green. An assertion never observed failing is decoration — this document's authors have shipped two of those already.

## Migration

The additive accepted-operation parent has landed so existing clients can migrate without a flag day. Route conversion, the route-derived conformance guard, and the aggregated diagnostic query remain incomplete; the 0.7.7 discrepancy guide keeps those gaps separate from already-shipped receipt/status/replay and browser-observation plumbing. Rule 2 (`queued` only when schedulable) and rule 5 (typed errors) remain the highest-value migration checks because they close the two defect classes that produced hangs.

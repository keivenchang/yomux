# Backend Test Contract — cohorts, boundaries, and acceptance

How backend process/browser work in YOLOmux is verified: resource ownership per test cohort, the first-failing-transition discipline, what may and may not count as acceptance, and when to stop. Extracted from the two-daemon migration queue on 2026-07-25.

These rules exist because specific failures cost real time: parallel-only failures traced to undeclared shared resources, and retries/reconnects masking lost first deliveries. Companion: [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md).

### Mandatory Work-Unit Preflight

Every implementation or test slice must record the following before editing source. If any field is unknown, discovery for that field is the work; do not start a speculative repair.

1. Name one work-unit ID and its existing unchecked parent requirement. Do not create a second requirement for the same behavior.
2. Name the source owner, classifier/adapter owner, storage owner, event owner, and browser consumer for the fact being changed. If two owners claim the same fact, consolidation is part of this work unit.
3. Name the existing shared parent searched before proposing a new class/helper/state/counter/fixture. Include the `rg` query and the candidate parents rejected or selected.
4. List the exact source files, test files, generated artifacts, and runtime resources the unit may touch. Another writer may run in parallel only when every entry is disjoint.
5. Fill a `BackendCohortManifest` for every process/browser topology used by the test. Fixture-owned resources are mandatory even if the same test passes alone.
6. Reproduce one concrete input with the smallest boundary-level regression. Record the first incorrect transition, not only the final HTTP/browser symptom.
7. Define the acceptance action count before running the test: number of HTTP requests, product submissions, EventSource opens, reconnects, final reads, and expected events. A test may not increase those counts after it fails.
8. Define the relevant lifecycle rows and final invariants before implementation. A narrow unit closes only its named row; a broad box closes only when every row in its matrix is current-source green.
9. Define exact cleanup ownership for every child, socket, port reservation, profile, temporary directory, lock, and stream. Cleanup runs in `finally` and verifies absence; it does not search and kill by a broad command pattern.
10. Select focused verification with `python3 -m pytest -n 2`; select the canonical parallel gate only for the conflict-group boundary. A serial pass, isolated rerun, or lowered worker count is never substitute evidence.


### Terminal-State Publication Contract

Every accepted asynchronous operation has one identity and one lifecycle owner. The identity is the narrowest stable receipt, ticket, key plus generation, request revision, or equivalent transaction token that lets the producer, transport, consumer model, and rendered state describe the same work. An owner may publish an open state such as queued, pending, loading, running, or refreshing only when it also owns a reachable terminal transition for that identity: success, typed failure, cancellation, or explicit supersession.

Completion is published only when every waiting boundary can observe it. A producer committing bytes without advancing its receipt, an HTTP 200 whose payload still means refreshing, a consumer settling while a re-keyed sidebar owner remains pending, and a UI clearing data while leaving its busy flag set are all incomplete transitions. Retention, pruning, timeout, reconnect, and supersession code must preserve the active identity until terminal publication or convert it to an explicit terminal failure; they may not silently discard it.

Every regression for an open state must prove the full transition with the shared terminal-transition assertion and a fixture-owned identity. The test must observe the open state, drive the exact producer action once, observe a terminal success or typed terminal failure for the same identity, and assert that the pending owner, loading indicator, and stale queued record are gone. A test that only enters pending, accepts READY or QUEUED indefinitely, proves a producer-side final snapshot without the consumer, or waits for a timer/reload without the exact completion identity does not count.

Static reachability across Python producers, HTTP/SSE delivery, JavaScript models, and rendered DOM is not a useful general lint: it would either miss re-keying and pruning defects or flag ordinary local booleans. The affordable guard is therefore a source-to-test contract catalog plus the shared runtime assertion. Each registered pending-state owner names its production token and proof test; the gate rejects a missing owner, missing proof, or proof that no longer calls the shared assertion. New user-visible asynchronous states must register before landing, while existing high-risk states are migrated when touched.

The shared Python assertion cannot inspect a browser promise that never returns. Every registered browser proof must therefore use a shorter in-page bounded wait and convert no settlement into a typed outcome passed to the callback before Selenium's outer script deadline; the Python assertion then reports that outcome as a missing terminal transition. A browser proof without that inner outcome remains outside the guard's protection and must not be described as covering the never-settles case.


### Backend Cohort Resource Manifest

The manifest is an executable ownership contract, not a comment. The production cohort resolver, supervisors, CLI, and tests must eventually share one typed shape. Until that parent lands, every topology test records the same fields locally and asserts that two concurrently active manifests have no overlapping mutable value.

```text
cohort_id:
deployment_environment:
source_fingerprint:
required_daemon_domains:
required_storaged_namespaces:
config_dir:
state_dir:
cache_dir:
service_dir:
upload_dir:
daemon_socket:
storaged_socket:
tmux_socket:
browser_profile_dirs:
http_listeners:
sqlite_paths:
wal_shm_paths:
lock_paths:
owned_processes:
owned_threads:
owned_streams:
```

- `cohort_id` includes the xdist worker plus a per-test unique component; worker identity alone is insufficient when one worker owns multiple simultaneous cohorts.
- Config, state, cache, service, upload, browser profile, socket, lock, SQLite, WAL, and SHM paths must be descendants of the fixture-owned root. No automated test opens a live dev/prod path.
- HTTP servers bind port zero or consume a fixture-held reservation without a close-then-rebind gap. A globally selected free port that is released before the child binds is not ownership.
- The private tmux socket is part of the cohort. Sharing visible host sessions is allowed only through an explicit read-only observation adapter; automated mutation never targets the host tmux server.
- `owned_processes` stores exact `Popen`/PID/process-group handles at spawn. Cleanup terminates only those handles, waits within a bounded deadline, escalates only those exact PIDs if needed, and verifies every descendant and listener is gone.
- A child joined rather than spawned is accepted only after exact environment, source fingerprint, domain/namespace, config, and protocol identity match. “Socket answered” or “version number matches” is insufficient.
- Failure evidence and command output go under a fixture-owned `/tmp` directory. The DOIT/DONE note records only a short result and the first failing transition.


### Parallel Ownership And Agent Rules

- A hard stop applies to the conflict group that owns the broken resource or invariant. Independent groups continue when their source files, generated artifacts, queue ownership, and complete runtime manifests are disjoint.
- Stop all groups only when the broken parent is global to every group, evidence shows data corruption or secret exposure, or continuing would mutate a live user environment. “One test failed” and “one group needs more diagnosis” are not global stop conditions.
- One writer owns each conflict group. Queue edits remain with the main agent. Agents may run read-only inspection and tests concurrently against the same source checkout only when each test has a distinct manifest.
- Two writers may proceed concurrently only from a user-authorized versioned base in separate worktrees, with disjoint files and generated artifacts. If both need `app.py`, a shared contract/catalog, a generated bundle, the same topology module, or this DOIT, they are one conflict group.
- Frontend source and its generated bundle are one ownership unit. Parallel branches do not merge generated bundles; the integration owner regenerates once from the integrated source state.
- Never stop a live or unrelated service to make a test pass. If a test observes a live socket, process, SQLite file, tmux namespace, browser profile, or port, the manifest boundary is broken and must be fixed.
- An xdist-only failure is an isolation defect until the exact conflicting resource is disproved. Passing the test alone provides a reproducer clue, not acceptance.
- If two worktrees unexpectedly conflict during integration, record the shared file/parent in this section and keep those items in one future conflict group.


### First-Failing-Transition Ledger

Every diagnosis records one row per crossed boundary. Production STATUS exposes only bounded aggregate counts; test-local evidence may retain opaque ticket/key/generation values under `/tmp` long enough to compare the exact transition, but those values and raw payloads do not enter STATUS, DOIT, DONE, or user-facing diagnostics.

| Boundary | Required success evidence | Failure classification | Next allowed action |
| --- | --- | --- | --- |
| Cohort resolution and bind | Expected environment/fingerprint/domains and a unique manifest own every path/socket/PID before bind. | Resource ownership or lifecycle defect. | Repair the manifest/resolver/supervisor and rerun two cohorts under `-n 2`; do not continue product work. |
| Client transport | One correlation is written on the expected mux; unrelated correlations remain live. | Transport connection/write defect. | Add a transport-level regression; do not change product workers or browser code. |
| Listener dispatch and reply | The named method is accepted, starts, completes within its deadline, and returns one typed reply. | Listener dispatch/deadline defect. | Inspect active/queued/expired/rejected counters for that method; do not infer saturation from an HTTP status. |
| Product state | The reply is exactly READY, QUEUED, or typed unavailable with the expected source identity and one ticket. | Product/precondition/coalescing defect. | Reproduce at the product adapter. Do not retry the HTTP route or reconnect transport. |
| Storaged completion | The exact current ticket/source generation is accepted once and advances the retained published generation or is explicitly fenced as stale. | Completion/fence/ownership defect. | Test the storage state machine directly before SSE/browser work. |
| Mux generation delivery and ACK | The subscribed exact client receives the exact event once with monotonic delivery generation and ACKs it; an unacknowledged event remains replayable. | Delivery, subscription, connection binding, or ACK defect. | Test first delivery on the original stream; do not reopen it. |
| SSE publication | The web follower forwards the same ticket/key/generation once on the already-open client-events stream. | Web event-adapter/broker defect. | Test the follower publisher directly; do not refetch the product. |
| Browser pending/accepted transaction | Only the exact pending completion triggers one final read; accepted LKG/DOM/terminal/socket/focus remain until a newer valid payload commits. | Browser transaction/classifier/rekey defect. | Test state transaction then real browser; do not accept timer refresh or destructive loading data. |

Use this record shape in test failure messages and handoffs:

```text
scenario:
cohort_id:
work_unit:
boundary:
method_or_event:
expected_outcome:
actual_outcome:
elapsed_ms:
active_count:
queued_count:
expired_count:
rejected_count:
source_generation:
published_generation:
delivery_generation:
request_count:
product_submission_count:
eventsource_open_count:
reconnect_count:
first_incorrect_transition:
```


### No-Retry Acceptance Contract

The rule forbids using a second attempt to hide a failed first attempt. It does not forbid waiting for the one asynchronous operation that the scenario intentionally started.

| Action | First-delivery acceptance | Recovery-specific test |
| --- | --- | --- |
| Wait on the original process-ready pipe, condition, event, future, socket readability, or already-open SSE stream until one fixed deadline. | Allowed. | Allowed. |
| Probe a distinct health endpoint before the subject request through one shared bounded readiness helper. | Allowed only for fixture readiness; it cannot call the endpoint/product under acceptance or mutate product state. | Allowed under the same restriction. |
| Reissue the subject HTTP request because the first returned 202/503, timed out, or produced the wrong body. | Forbidden. | Forbidden unless the product contract explicitly defines a new user action, which is then a separate scenario. |
| Submit the same daemon product/ticket again after a failure. | Forbidden. | Forbidden; recovery attaches to the retained ticket/key/generation or produces an explicit terminal error. |
| Close and reopen EventSource after a read/assertion failure. | Forbidden. | One deliberate disconnect and one deliberate replacement stream are allowed when reconnect recovery is the behavior under test; assertion-driven reopen loops remain forbidden. |
| Poll the product, metadata endpoint, or browser state until it becomes READY. | Forbidden. | Forbidden. Recovery is driven by the exact completion/generation event. |
| Perform one final bounded read after the exact matching generation event. | Allowed and required when the contract is event-then-refetch. This is not a retry because the event authorizes the state transition. | Allowed once after the matching recovery event. |
| Accept a timer, periodic refresh, stale unrelated event, any-ticket event, or eventual page reload as completion. | Forbidden. | Forbidden. |
| Use arbitrary `time.sleep()` to create ordering or allow “enough time.” | Forbidden. | Forbidden; use an owned event/condition/pipe/FD and a fixed deadline. |

The canonical queued-product first-delivery sequence is exact:

1. Start fixture-owned daemon/storaged/follower resources and wait only for their distinct readiness signals.
2. Open and verify the one subject SSE stream before issuing the subject request.
3. Issue the subject HTTP request exactly once and require the expected bounded immediate response, normally one 202/QUEUED ticket or one READY LKG with a pending ticket.
4. Complete the exact daemon work once and require storaged to accept that ticket/source generation once.
5. Read the already-open SSE stream until the exact ticket/key/generation event or the fixed deadline; unrelated events may be ignored but cannot reset or extend the deadline.
6. Perform exactly one event-authorized final read and require READY with the expected generation/body.
7. Assert request count, product submission count, EventSource open count, reconnect count, event count, browser accepted generation, LKG preservation, and cleanup.

The reconnect-recovery sequence is a different test:

1. Establish the pending ticket and original subscription.
2. Deliberately disconnect at the named boundary.
3. Open exactly one replacement stream/client.
4. Require retained subscription/event repair for the same ticket/key/generation.
5. Perform one event-authorized final read.
6. Assert there was one deliberate reconnect and no assertion-driven reopen, request retry, product resubmission, timer fallback, or polling.


### Lifecycle Acceptance Matrix

Before implementing a broad box, list every required row. Mark a row evidenced only when its exact state, action count, result, invariants, and cleanup passed against current source. A row from a fixture renderer cannot stand in for a real process/browser row.

| Lifecycle row | Initial state | Immediate result | Completion/recovery | Invariants that must remain true |
| --- | --- | --- | --- | --- |
| Cold start, no LKG | Empty fixture-owned storaged state and current daemon. | One bounded READY or QUEUED; never destructive fake data. | Exact completion event then one READY read, or explicit terminal error. | No web discovery/worker/SQLite; no infinite loading; exact cleanup. |
| Warm LKG plus refresh | Retained accepted bytes and a newer source generation. | READY stale/LKG with one pending ticket or bounded READY current. | Exact newer completion replaces LKG once. | Accepted browser rows, terminal, focus, and sockets remain until commit. |
| Daemon down | Storaged/LKG available; daemon absent. | READY LKG or typed unavailable within deadline. | No spawn from web; later recovery is a separate named action/event. | Storaged remains responsive; no local fallback owner. |
| Daemon starting | Socket/process exists but required domains are not ready. | READY LKG, QUEUED, or typed unavailable within deadline. | Event-driven transition or explicit terminal error. | No join without fingerprint/domain match; no repeated startup. |
| Daemon saturated/held work | One named lane/worker is held by the fixture. | Unrelated cached/storage work stays READY; subject work is bounded QUEUED or READY. | One coalesced completion after release. | No extra worker/process, retry, or listener starvation assumption. |
| Daemon replacement | Current LKG in storaged and old daemon deliberately stopped. | Web remains bounded. | New exact-identity daemon republishes/finishes once. | LKG preserved; old child/socket gone; no duplicate owner. |
| Storaged replacement | Durable namespace/checkpoint exists and old owner is deliberately stopped. | Clients get bounded unavailable/upgrade-required during the named window. | One exact replacement restores its durability contract. | No web/daemon SQLite fallback; stale client cannot clobber new state. |
| SSE disconnect | Pending or accepted generation exists before one deliberate disconnect. | UI preserves accepted state. | One replacement stream repairs exact generation then one final read. | No timer/poll/request retry; one reconnect only. |
| Web-only restart | Shared current children and retained storaged state remain. | New follower joins exact cohort and reads LKG/current state. | Subscriptions restore once. | Children are not duplicated/replaced; no web-owned background work. |
| Source supersession | Older work remains pending when a newer generation arrives. | Contract explicitly retains, cancels, or supersedes the older ticket. | Only the accepted current generation becomes authoritative. | Stale completion cannot overwrite; delivery sequence remains monotonic. |
| Malformed/old protocol | Invalid fields or incompatible protocol/fingerprint. | Typed invalid/upgrade-required within deadline. | Replacement/takeover only through the lifecycle owner. | No partial mutation, fallback database, hidden traceback, or socket-gap data loss. |

Every broad-box evidence note uses this compact schema:

```text
Requirement:
Lifecycle row:
Concrete input:
Source owner:
Implementation evidence:
Verification command:
Observed result:
Negative search:
Generated assets:
Cleanup evidence:
Remaining rows:
```


### Failure Triage Decision Tree

```text
Did two parallel runs share a mutable resource?
  yes -> stop that manifest-owning conflict group; fix cohort ownership first.
  no  -> did the original mux correlation receive a typed reply?
          no  -> transport/listener boundary regression.
          yes -> was the reply READY/QUEUED/typed unavailable as expected?
                  no  -> product/precondition/coalescing regression.
                  yes -> was the exact completion accepted by storaged?
                          no  -> ticket/source-generation/fence regression.
                          yes -> did the original subscribed mux client receive and ACK it?
                                  no  -> delivery/subscription/connection-binding regression.
                                  yes -> did the already-open SSE stream forward the exact event?
                                          no  -> follower event-broker regression.
                                          yes -> did the exact browser pending transaction commit once?
                                                  no  -> browser transaction/rekey regression.
                                                  yes -> the reported symptom is downstream; add that boundary without reopening earlier owners.
```

- HTTP 503 plus mux `timed_out`/`transport_failed` means inspect transport ownership.
- HTTP 503 plus a replied product `unavailable` means inspect product source identity/preconditions.
- HTTP 503 with no product request means the web adapter rejected locally; do not inspect worker saturation.
- One active listener request with zero queued/expired/rejected is not saturation evidence.
- A test that passes alone and fails under `-n 2` remains an isolation failure until the exact resource difference is named.
- A test that passes only after reconnect/retry proves recovery at most; it cannot close first-delivery acceptance.


### Test And Verification Rules

- Every pytest command is `python3 -m pytest -n 2 ...`; higher worker counts are allowed, one worker and serial markers are not.
- Focused tests use exact node IDs for the current work unit. Repetition may be used only to establish that a previously flaky regression is stable after its root cause is fixed; repeated passes never replace the required lifecycle or canonical gate.
- Tests do not add arbitrary sleeps, retry decorators, rerun plugins, ordered phases, shared ports, shared directories, shared browser profiles, shared tmux sockets, or relaxed process counts.
- When a test needs asynchronous control, production exposes an owned event/condition/clock/fanout seam that preserves the real default path. A test-only timing constant or shorter production sleep is not a deterministic seam.
- Static JS changes require `python3 tools/static_build.py`, focused Node/browser coverage, and `python3 tools/static_build.py --check`; one owner regenerates the bundle after integration.
- Rust does not apply in the current path; if an `.rs` file is introduced or changed, run `cargo fmt` immediately.
- Run `nice -n 10 python3 tools/check.py` only after the focused conflict group is green and resource cleanup is verified. A full gate failure is classified by exact owner/namespace before any rerun.
- Raw command/test/browser logs stay under `/tmp`. The DOIT records commands, counts, first failure, and final result without copying logs.
- Add a fixture boot shape through one immutable `BrowserBootScenario` preset and its route registry. Add an owned runtime through `FixtureRuntime` or `GateLiveServer`; its manifest includes app, server/thread, browser, tmux/runtime paths, log cursors, and teardown order, with injected start and cleanup failures proving zero survivors.
- Add a test phase or check lane only in `tools/test_plan.py` through `TestPhaseSpec` or `LaneSpec`. Keep argv construction in `tools/check.py`, model boot smoke as a prerequisite edge, and prove collection has one execution owner with no prerequisite cycle or copied lane selection.


### DOIT Update And Handoff Contract

- Re-read the exact checkbox immediately before editing it. The main agent owns this file; implementation agents report evidence without modifying the queue.
- Update this authoritative section in place. Do not append another “current audit,” “replan,” or competing progress denominator.
- After a requirement is fully evidenced, change only its exact `[ ]` to `[x]` and add one concise DONE note containing the concrete input, owner, verification command/result, negative search, cleanup evidence, and remaining risk. Do not paste raw logs.
- If new evidence disproves a prior cause, state `SUPERSEDED` beside the current claim here and name the actual first failing transition. Do not silently leave the old theory as an active instruction.
- Recompute literal progress from disk after every checkbox edit. Report the full-file count and name any narrower subtotal explicitly.
- A handoff must be usable without transcript archaeology:

```text
Work unit:
Existing parent checkbox:
Conflict group and file owner:
Shared parent searched/reused:
Resource manifest:
Concrete reproducer:
First incorrect transition:
Implementation state:
Focused verification:
Canonical gate state:
Cleanup state:
Files intentionally changed:
Generated artifacts rebuilt:
Remaining lifecycle rows:
Literal queue progress:
Next exact action:
Forbidden shortcuts for this action:
```


### Hard Stop Conditions

- Resource collision: if two `-n 2` cohorts share any mutable resource, stop every work unit that consumes that manifest parent and repair the fixture or production ownership boundary first. Resume only after two complete cohorts pass concurrently and exact cleanup is proven. Independent groups with disjoint manifests and files continue.
- Unknown failing boundary: if a failure cannot name the first incorrect transition among cohort bind, transport, listener reply, product state, completion, mux delivery/ACK, SSE, and browser acceptance, stop behavior changes in that conflict group and add the boundary regression/diagnostic. Resume when one row in the transition ledger is the first failure.
- Retry-dependent acceptance: if first delivery passes only after a repeated request, product submission, EventSource reopen, timer refresh, endpoint poll, or arbitrary sleep, stop acceptance work and split first-delivery from recovery. Resume when the first-delivery test uses the original request/stream and fixed action counts.
- Leaked child/listener/state: if fixture cleanup leaves a child, descendant, listener, lock, profile, temp database, or open stream, stop new spawns for that harness. Resume only after exact-handle cleanup and a failing-test cleanup regression pass under `-n 2`.
- Shared source conflict: if two writers need the same file, generated asset, contract, or queue, stop only those writers and merge them into one conflict-group owner. Other disjoint groups continue.
- Live-environment contact: if an automated test or debug command resolves a live dev/prod path, port, socket, process, browser profile, SQLite/WAL, or tmux mutation target, stop that command immediately without deleting or stopping the live owner. Resume only with a fixture-owned manifest.
- Evidence mismatch: do not close a broad box from a fixture-only browser test, backend-only test, source grep, isolated rerun, or focused subset that omits one of its lifecycle rows. Missing evidence blocks that checkbox, not unrelated work.
- Rollout authorization: do not restart 7772, clean adopted processes, measure live workloads, or soak without the existing prerequisites and explicit authorization. Fixture-owned process cleanup is required and does not need live-process authorization.


### Non-Fixes

- Do not call another web-only restart a backend rollout. Either the joined children prove the exact required deployment identity or the start fails/replaces them deliberately.
- Do not add richer placeholder sessions, preserve `self.sessions` as a second roster, or teach Tabber to invent agent facts. The daemon roster and its storaged LKG are the parent.
- Do not treat 202 as an empty successful payload, accept `READY or QUEUED forever`, increase sleeps/backoff, add request polling, or equate a presentation timer with a new stored CPU sample.
- Do not hide retired daemon names in CSS, delete stale service files by hand, or kill broad process patterns. Retire the lifecycle and verify the exact PID/socket/artifact owner.
- Do not claim release coverage from fixture fetch/EventSource/WebSocket data or `/api/tmux-session-exists`. Release assertions inspect real visible rows, real sockets, real generations, and real current child identities.

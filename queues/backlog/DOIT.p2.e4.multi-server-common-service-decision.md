# DOIT.p2.e4.multi-server-common-service-decision.md - Decide Whether Multi-Server Common Services Are Justified

Source provenance: `DOIT.unprioritized.md` U-D, the former `DOIT.multi-server-shared-service-decision.md`, and deferred design input from `PROPOSAL.better-architecture.md`.

## Goal

Make an evidence-backed product decision about whether any isolated YOLOmux instances should share a service, without weakening the current one-root-per-instance reliability model merely to remove duplicated readers or workers.

## Context

- The former `MASTERPLAN.md`, `PROPOSAL.better-architecture.md`, and `docs/specs/BACKEND_ARCHITECTURE.md` defer shared services until a measured requirement justifies the new failure domain.
- Managed non-default instances deliberately derive private roots and duplicate readers, workers, databases, and local services. Same-root compatibility coordination has its own queue; cross-host aggregation has separate authentication, WAL, clock, schema, and availability requirements.
- The proposal was pinned to historical `groupD-lifecycle` at `0fe6469137af951a4570638bbb0d844241ea65e3` and explicitly says it is not the current implementation plan. Re-verify every cited symbol against the selected implementation baseline before using it.

## Ownership Boundary

This queue owns only the measured product decision and, if justified, the definition of one narrow follow-on queue. It must not prototype or land a shared service while the decision is still open.

## Execution Order And Parallel Ownership

- Measure only after `DOIT.p1.e5.backend-lifetime-supervision.md` has frozen the private-root/caller-shared-root topology; otherwise duplicated or orphaned processes corrupt the denominator.
- One workload owner freezes the one-instance/two-instance script and denominators. Read-only agents may then profile web/jobd/statsd/statusd/watch/index families in parallel, but one integrator owns the aggregate arithmetic and decision.
- This is a decision-only queue. Co-tenant failing tests described below are specifications for a separately approved follow-on, not authorization to prototype here.

## Plan

- [ ] Measure at least two isolated instances under the same cold and settled visible workload. Report duplicated CPU, RSS, open descriptors, wakeups, database/cache bytes, reader scan bytes, service starts, and request latency by named owner; distinguish useful duplicated availability from waste.
- [ ] State the concrete multi-server requirement and a success threshold. If there is no repeated user need or the measured saving is not material, record a no-build decision, retain isolated roots, and close this section without a prototype.
- [ ] If justified, select exactly one narrow service family and define its owner, consumers, local versus cross-host boundary, authenticated transport, source-host identity, version/schema negotiation, bounded request/deadline context, and typed ready/stale/pending/unavailable/upgrade outcomes. Do not design a generic remote filesystem or publish-any-database service.
- [ ] Design local unavailable behavior, last-known-good rules, exactly-once promise discharge, dropped-notification repair, lane fairness, owner crash/restart, partition behavior, version coexistence, rollback, and one-change backout before implementation.
- [ ] Specify the failing fixture-owned co-tenant tests required of a follow-on: measured requirement, independent instance survival when the shared service is absent or incompatible, authenticated identity, promise repair, fairness, and old/new coexistence. Do not add those tests or product code in this decision queue.
- [ ] Write the decision into `docs/specs/BACKEND_ARCHITECTURE.md` and `docs/DEVELOPMENT.md`. If implementation is approved, create a separately approved implementation queue with exact migration and gate ownership rather than silently expanding this decision section.

## Deferred Implementation Input

If and only if the decision is to proceed, the follow-on queue should extend current owners rather than rewrite transports: one exhaustive executable boundary schema over existing JSON-plus-binary framing; generated Python/browser validators and adapters; a propagated `CallContext` with non-increasing `DeadlineBudget`; exhaustive Ready/Pending/Unavailable/Rejected/Failed/Cancelled outcomes; separate liveness/readiness/degraded states; bounded CLOSE/subscription/ownership protocols; typed physical and monetary units; shrink-only legacy allowlists; and lint/mutation gates that prove uncataloged routes, numeric timeout copies, bare destructive identities, missing outcome arms, unitless fields, lost trace propagation, and missing close reasons fail CI. Do not introduce gRPC, Kubernetes, Erlang, systemd, or an external telemetry collector as required runtime dependencies.

## Done Criteria

- [ ] Before measurement, the decision record pins the implementation HEAD, one scripted visible workload, cold and settled definitions, measurement commands, five independent repetitions for one instance and five for two isolated instances, named owners, and the literal outcomes `KEEP_ISOLATED` or `PROCEED_WITH_ONE_SERVICE_FAMILY`.
- [ ] Each repetition reports workload completion count plus per-owner CPU seconds, peak RSS bytes, open descriptors, wakeups, database/cache bytes, reader scan bytes, service starts, and request-latency p50/p95; denominators and failures are explicit, raw evidence stays under `/tmp`, and cold availability work is separated from settled duplicate waste.
- [ ] `PROCEED_WITH_ONE_SERVICE_FAMILY` is allowed only when a repeated user-visible requirement cannot be satisfied by isolated roots, sharing reduces at least one predeclared aggregate resource by 25% or more across the five-run median, no other measured resource or p95 latency regresses by more than 10%, and both instances remain usable when the candidate shared service is absent or incompatible; otherwise the required outcome is `KEEP_ISOLATED`.
- [ ] The written decision names the measured workload, every denominator, before/after values, requirement, threshold result, authentication/failure-domain cost, selected outcome, and decision authority; `KEEP_ISOLATED` explicitly retains private roots and lands no prototype.
- [ ] A proceed outcome selects exactly one service family and creates a separately approved test-first queue covering all eight acceptance points in `docs/specs/BACKEND_ARCHITECTURE.md`, with exact co-tenant fixture commands, unavailable/incompatible behavior, promise repair, fairness, version coexistence, rollback/backout, canonical gate, and runtime owner; no shared-service implementation belongs in this decision queue.

## Completion

Record `KEEP_ISOLATED` in `docs/DONE/` and remove this queue, or replace it with exactly one separately approved implementation queue after `PROCEED_WITH_ONE_SERVICE_FAMILY`; never keep both active.

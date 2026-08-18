# DOIT.p2.e2.cross-host-read-views.md - Define Safe Cross-Host Read Views

Source provenance: `DOIT.unprioritized.md` U-C and the former `DOIT.multi-host-state-isolation.md`.

## Goal

Define optional cross-host read views without opening foreign live WAL files or treating missing remote data as zero.

## Context

- Cross-host views must use authenticated source-host responses or closed validated immutable snapshots, never foreign live WAL files.
- Current design input lives in `docs/CROSS-HOST-VIEWS.md`, `docs/MUTABLE-PATH-INVENTORY.md`, `docs/specs/BACKEND_ARCHITECTURE.md`, and `docs/PHASE2_GATE_CENSUS.md`.

## Ownership Boundary

This is a read-view contract and decision lane. It does not implement multi-server shared services, same-root background ownership, network filesystem mutation, or a generic remote filesystem.

## Execution Order And Parallel Ownership

- One product owner must first name the concrete cross-host question. Without a question that truly requires two sources, the queue closes `NO_BUILD` without transport design.
- After a concrete need exists, read-only security/authentication, schema/coverage, and snapshot/availability audits may run in parallel. One decision owner reconciles them; no product-code writer is assigned in this queue.

## Plan

- [ ] Define cross-host read views with source-host, schema, generation, time coverage, stale/unavailable, clock-skew, retention, and upgrade semantics. Prefer authenticated HTTP/RPC; a closed validated SQLite backup snapshot is acceptable only for offline transfer.

## Required Invariants

- Never open a foreign live WAL database, create runtime sockets on a network filesystem, auto-adopt legacy stores, or write sidecars beside shared read-only inputs.
- Every result labels source, coverage, freshness, clock/schema compatibility, and unavailability explicitly.

## Done Criteria

- [ ] `git ls-files --error-unmatch docs/CROSS-HOST-VIEWS.md` succeeds and the decision record names its author/date, exact user workflow, source hosts/datasets, authentication owner, exposure boundary, and one of the literal outcomes `NO_BUILD` or `PROCEED_WITH_SEPARATE_QUEUE`; an undecided or generic “future aggregation” statement cannot close this queue.
- [ ] The default `NO_BUILD` outcome is required unless the record identifies at least one concrete answer that requires data from two or more source hosts and cannot be obtained correctly from separate authenticated tabs; `NO_BUILD` keeps one tab per machine, adds no route/socket/database, and a route/source inventory proves supported runtime opens zero foreign WAL/SHM files.
- [ ] A `PROCEED_WITH_SEPARATE_QUEUE` outcome defines one narrow dataset and pins exact schema/version, stable host ID, boot/source generation, half-open coverage, freshness/stale/unavailable states, maximum accepted clock offset, retention, integrity identity, authentication, request deadline, and upgrade behavior before implementation begins.
- [ ] The proceed decision includes table-driven expected results for online source, accepted offline snapshot, unavailable source, partial coverage, stale current view, excessive/unknown clock offset, unsupported schema, integrity conflict, and untrusted source; missing data is typed unavailable/partial and never becomes numeric zero, and no candidate reads a live foreign WAL.
- [ ] No product code is changed in this decision queue; if proceeding, a separately approved test-first queue names the exact fixture commands, canonical gate, restart/runtime owner, migration/backout, and acceptance evidence, while the no-build path closes with docs and the zero-route/zero-foreign-WAL inventory.

## Completion

Record the literal decision in `docs/DONE/` and remove this file. A proceed outcome replaces it with exactly one separately approved implementation queue for the one named dataset.

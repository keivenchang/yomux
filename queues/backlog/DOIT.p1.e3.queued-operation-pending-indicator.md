# DOIT.p1.e3.queued-operation-pending-indicator.md - Render Accepted Operation Progress

## Goal

Every visible action that receives a durable `202` receipt immediately shows one shared local pending state and clears it on success, failure, timeout, disconnect repair, or cancellation.

## Current Evidence

- `apiOperationState.pending` owns receipt transport demand and terminal removal, but transport liveness alone is not a user-visible state.
- Some surfaces await their request directly and have no shared control-local pending renderer. Existing chat pending UI is a useful presentation model, not a second operation owner.

## Plan

- [ ] Inventory every visible action that can receive an accepted operation and record its control identity, concurrent-count behavior, terminal rendering, disappearance behavior, and fallback surface.
- [ ] Add one shared pending projection driven by receipt registration and terminalization; adapters may render on persistent controls or a documented fallback when the control disappears.
- [ ] Preserve one global EventSource identity, receipt replay, exactly-once settlement, synchronous activation requirements, and typed failure details.
- [ ] Add deterministic Node and real-browser coverage for concurrent receipts, success, failure, timeout, disconnect/reconnect repair, cancellation, rerender, and control removal.

## Done Criteria

- [ ] Every accepted-operation affordance appears exactly once in the inventory and consumes the one pending projection; negative searches find no parallel pending-state owner.
- [ ] A `202` becomes visible before terminal completion, concurrent counts remain accurate, and every terminal path clears exactly once without hiding failure.
- [ ] Node, owning browser tests, generated-asset checks, and an unmodified `python3 tools/check.py` exit 0 on one HEAD.
- [ ] After active-server restart, representative filesystem and session actions prove immediate pending paint, terminal clearing, disconnect repair, and zero new unallowlisted browser errors.

## Completion

Record the inventory, shared owner, tests, and browser proof in `docs/DONE/`, then remove this queue.

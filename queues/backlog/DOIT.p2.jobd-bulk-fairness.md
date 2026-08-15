# DOIT.p2.jobd-bulk-fairness.md - Prevent Maintenance Starvation

## Goal

Continuous freshness work must not starve an already queued maintenance job in jobd's bulk lane.

## Current Evidence

- Bulk priority order is freshness before maintenance, and selection restarts at the first priority on every dispatch.
- Historical direct observation showed maintenance still queued after five freshness dispatch cycles. This has not been dynamically re-established on current main.

## Plan

- [ ] Add a deterministic current-baseline regression with a continuously replenished freshness queue and one maintenance record; record dispatch order and starvation duration.
- [ ] Add bounded aging or round-robin state within the existing bulk owner while preserving point/mutation isolation, FIFO ties, queue bounds, cancellation, and source-generation fencing.
- [ ] Cover empty/single/mixed queues, sustained freshness, sustained maintenance, cancellation, restart, stale work, and worker-capacity changes.

## Done Criteria

- [ ] The red fixture proves a maintenance record can miss the declared bound while bulk workers continue making freshness progress.
- [ ] The fixed scheduler dispatches each nonempty bulk class within the declared number of eligible dispatches, without weakening point/mutation isolation or queue caps.
- [ ] Focused jobd tests and an unmodified `python3 tools/check.py` exit 0; retained scheduler metrics make a violated fairness bound attributable.

## Completion

Record the algorithm, numeric bound, red/green order, tests, and gate evidence in `docs/DONE/`, then remove this queue.

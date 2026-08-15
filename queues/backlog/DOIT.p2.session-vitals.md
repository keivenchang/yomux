# DOIT.p2.session-vitals.md - Add Honest Session Resource And Usage Vitals

## Goal

Expose per-session token/cost/context metrics only from reliable Claude/Codex metadata, plus lightweight CPU, memory, load, process-tree, and optional GPU GPU status.

## Plan

- [ ] Inventory reliable provider metadata and record unavailable versus partial semantics; never scrape fragile terminal text for token/cost/context numbers.
- [ ] Add one bounded process-tree sampler and machine-load owner with stable process identity, source time, units, stale state, and permission failure.
- [ ] Add optional `gpu-info` data only when available; absence must not degrade non-GPU hosts or start recurring failures.

## Done Criteria

- [ ] Every displayed metric has source identity, units, coverage, freshness, and unavailable reason; missing data never becomes numeric zero.
- [ ] Sampling is bounded while hidden/idle, process reuse is fenced, and focused backend/browser/resource tests plus the canonical gate pass.

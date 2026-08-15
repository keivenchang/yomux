# DOIT.p2.yoagent-watch-jobs.md - Finish Persisted YO!agent Watch Jobs

## Goal

Add tests-finished, all-agents-status fanout, review sweep, finished-work closeout, and pause/resume controls on top of the existing persisted YO!agent job owner.

## Plan

- [ ] Define each predicate's input generation, calm/ready condition, dedupe key, timeout, cancellation, restart replay, and exact send/result contract.
- [ ] Implement through the existing persisted job state machine; do not add a second watch loop.
- [ ] Add deterministic tests for trigger, no-trigger, duplicate events, noisy inputs, pause/resume, cancel, restart, target disappearance, and fanout partial failure.

## Done Criteria

- [ ] Each predicate fires once for one qualified generation, survives restart, can be paused/cancelled, and exposes typed pending/terminal state.
- [ ] Focused backend/browser tests, the canonical gate, and restarted live jobs pass without recurring background work while idle.

# DOIT.p2.e3.session-summary-context.md - Add Event-Driven Session Summary Context

## Goal

Maintain durable incremental session-summary context without restoring a recurring background summary loop.

## Plan

- [ ] Freeze the shipped first-launch-only per-server-run behavior, transcript identity, summary schema, source cursor, failure state, and privacy/size bounds.
- [ ] Update only on first visible YO!agent launch, explicit refresh, or a bounded event-driven job, using `prior_summary + transcript delta` rather than resending full transcripts.
- [ ] Add append, replacement, truncation, resume, stale completion, provider failure, restart, cancellation, authorization, and payload-bound tests.

## Done Criteria

- [ ] Unchanged idle sessions cause zero summary work; each qualified transcript generation advances once and obsolete completions never overwrite current context.
- [ ] Focused job/transcript/browser tests, the canonical gate, and restarted incremental-summary journeys pass.

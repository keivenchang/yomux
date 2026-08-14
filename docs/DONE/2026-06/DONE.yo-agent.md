# YO!agent

## DOIT.47 YO!agent capability boundary
- Grounded YO!agent capability answers in a backend capability inventory. Activity payloads and prompt context state that YOLOmux can read tmux panes, poll sessions, monitor prompts/PRs/files, notify on configured transitions, and keep mutating sends behind admin-only, server-resolved, confirmation-gated paths. Updated default/reset prompt text and locale prompt catalogs, preserved legacy prompt migration, and added regressions for prompt context plus deterministic capability answers.

## DOIT.43 YO!agent rolling transcript summaries
- Added disabled-by-default YO!agent background transcript summaries: per-session rolling state persists in YOLOmux state, updates from transcript deltas after quiet intervals, prunes dead sessions, pins cheap CLI backends, exposes the refresh interval, and injects cached rolling summaries into YO!agent context. Current builds migrate the old `yoagent.auto_refresh` boolean into `yoagent.refresh_interval_seconds` and keep only the interval visible. Added backend/settings/UI regressions for delta-only updates, idempotent no-op ticks, disabled-by-default behavior, bounded settings, and Preferences visibility.

---

Completed 2026-06-07. Extracted from the 2026-06-07 daily log.

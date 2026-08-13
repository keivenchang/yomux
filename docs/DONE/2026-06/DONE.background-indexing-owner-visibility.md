# Background indexing owner visibility

- The client tracks whether the current browser is connected to the `search-index` owner and `stats-sampler` owner through `/api/background/status`, but YO!info no longer renders that server-role strip. The top-right ownership chip shows the compact `IDX|STATS|SESS: leader/follower` state for shared background jobs and refreshes on click, manual refresh, EventSource reconnect, and `background_owner_changed`. Runtime owner takeovers now notify the app through a background-owner acquire callback, so a follower that takes over after a dead indexing owner publishes `background_owner_changed` instead of silently changing only the on-disk owner record. `docs/DEVELOPMENT.md` now spells out the `YOLOMUX_STATE_DIR` layout: `background-owner/`, `search_index/`, `watch-index.json`, and separate per-target auto-approve locks under `locks/`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

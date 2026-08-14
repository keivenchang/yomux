# 2026-07-19 Revision-aware client events

- Completed the resource-revision contract from `DOIT.polling-to-sse.md`. Client events now use scoped revisions, demand-filtered ready summaries, explicit dropped-resource repair, a frozen browser dispatch table with server-type parity, and durable follower manifest replay.
- Verification: focused browser/broker/follower regressions plus canonical eight-lane gate passed in 467.77 seconds; port 8881 restarted and `/api/ping` returned 401.

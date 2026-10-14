# YOmux stats lifecycle and SSE on yo7111

## Lifecycle, collection, storage, and retention

### Takeaway

YO!stats runs in-process: collectors append observations to SQLite, a one-second lifecycle materializes products, and HTTP/SSE routes serve them. Stats costs CPU, but the 18:44 profile attributes 13.58% of sampled CPU to tagged stats components; Finder traversal is the dominant stack.

### Cited Findings

- Startup creates the stats service and starts statsLifecycle at a one-second cadence, with 30-second background backfill and demand priority while a stats stream is open. The lifecycle starts before the collector-disable check; that check gates collectors but not the lifecycle  - [main.go:801](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:801); [main.go:809](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:809); [main.go:840](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:840); [main.go:848](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:848).
- The disable check resolves productenv.Value("STATS_DISABLE_COLLECTORS"). Only YOMUX_ is canonical and YOLOMUX_ is legacy; bare STATS_DISABLE_COLLECTORS=1 is not an accepted alias. PID 839213 had only the bare name during inspection, so collectors were still enabled - [main.go:848](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:848); [namespace.json](/home/keivenc/dev/yomux/yo7111/internal/productenv/namespace.json); [value.go:32](/home/keivenc/dev/yomux/yo7111/internal/productenv/value.go:32).
- Host CPU collection is every five seconds, memory every minute, disk every five minutes; agent status is ten seconds active and one minute idle; token collection defaults to five seconds active and idle - [main.go:892](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:892); [main.go:946](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:946); [host_collector.go:134](/home/keivenc/dev/yomux/yo7111/internal/stats/host_collector.go:134); [agent_tokens.go:28](/home/keivenc/dev/yomux/yo7111/internal/stats/agent_tokens.go:28).
- Token collection shares the machine-wide agent inventory with status collection to avoid a duplicate process census, then scans token sources and appends results  - [main.go:879](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:879); [main.go:882](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:882); [agent_tokens.go:795](/home/keivenc/dev/yomux/yo7111/internal/stats/agent_tokens.go:795).
- Each lifecycle tick performs reconciliation, due maintenance, a SQLite snapshot and product selection/materialization, then refreshes projections and checks pending invalidations  - [lifecycle.go:490](/home/keivenc/dev/yomux/yo7111/internal/stats/lifecycle.go:490); [lifecycle.go:534](/home/keivenc/dev/yomux/yo7111/internal/stats/lifecycle.go:534); [main.go:817](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:817); [main.go:826](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:826).
- Retention is 48 hours, pruning runs every 24 hours, so data may remain almost 72 hours until the next prune  - [lifecycle.go:256](/home/keivenc/dev/yomux/yo7111/internal/stats/lifecycle.go:256); [writer.go:1184](/home/keivenc/dev/yomux/yo7111/internal/stats/writer.go:1184).
- Read-only SQLite query of /tmp/y1776734304/p7111/config/stats-v9.sqlite3 counted 2,442,942 observations, including 2,141,210 service_load rows; 1,433,708 ring invalidation rows (11 pending); 62,207 usage atoms; 1,003 coverage epochs; 802 collector checkpoints. DB file size was 1,471,250,432 bytes and WAL 5,450,792 bytes. Query used Python SQLite mode=ro; it did not mutate or checkpoint the DB.
- service_load covered 588 source IDs and 38,687 distinct timestamps. Oldest observation was 2026-10-10 19:59:23 UTC; prune marker was 2026-10-12 19:59 UTC, when that row was only seconds newer than the 48-hour cutoff. At query time it was about 54 hours old, consistent with the daily prune cadence - [stats-prune-v9.json](file:///tmp/y1776734304/p7111/config/stats-prune-v9.json).
- The 30-second profile at 2026-10-12 18:40 PDT recorded 27.01 CPU seconds: stats_lifecycle 1.49s (5.52%), agent_token_collector 0.86s (3.18%), host_collector 0.15s (0.56%), agent_status_collector 0.03s (0.11%) - [profile](/tmp/yomux-yo7111-cpu.pprof).
- The 30-second profile at 18:44 PDT recorded 39.92 CPU seconds (133.06% of one core): stats_lifecycle 2.67s (6.69%), agent_token_collector 2.11s (5.29%), host_collector 0.52s (1.30%), agent_status_collector 0.12s (0.30%). These tagged components total 5.42s or 13.58% of sampled CPU - [profile](/tmp/yomux-yo7111-no-collectors.pprof).
- The second profile's no-collectors filename is misleading: it has token, host, and status collector samples, and bare STATS_DISABLE_COLLECTORS was not a valid alias - [profile](/tmp/yomux-yo7111-no-collectors.pprof); [namespace.json](/home/keivenc/dev/yomux/yo7111/internal/productenv/namespace.json).
- runtime.cgocall led lifecycle and token-collector cost inside component tags, consistent with SQLite C-driver work. The profile does not name a specific SQL statement  - [profile](/tmp/yomux-yo7111-no-collectors.pprof).
- A fresh 10-second /proc/839213/stat delta during inspection measured 204 CPU ticks = 2.04 CPU seconds = 20.4% of one core. HTTPS health returned HTTP 200 and version 0.10.4. This later lower sample is not matched to the profile workload.

### Inferences

- Stats has recurring cost from a one-second lifecycle plus five-second token/host collection and a large retained service_load set. In the profiled peak, stats used about one-seventh of sampled CPU: a contributor, not the main cause.
- The 48-hour window and daily prune explain the older rows; their age is not evidence of failed retention.
- A controlled collectors-off run requires YOMUX_STATS_DISABLE_COLLECTORS=1 or YOLOMUX_STATS_DISABLE_COLLECTORS=1, and the lifecycle would still run. This research did not change runtime settings.

### Gaps

- Unauthenticated GET /api/stats-agent-status returned 401, so live lifecycle counters, collector durations, and pending product backlog were unavailable.
- Profiles were not matched for browser/Finder workload; no query-level profile identifies which SQL statement accounts for SQLite C time.
- Row counts measure stored data, not CPU causality.

## Browser polling and the SSE sample

### Takeaway

The supplied 53-second JS Debug capture shows 92 SSE events (1.74/s) and 218,846 bytes (4.13 KB/s), moderate overall traffic with a large ready frame and paired filesystem notifications. It lacks the EventSource URL, so it cannot show that stats SSE itself was excessive. Its 51 stats-refill GETs match the UI's one-second polling while refill is busy.

### Cited Findings

- The capture reports 92 SSE events / 218,846 bytes in about 53 seconds, zero errors, one 142,870-byte ready event (65% of SSE bytes), 40 fs_changed and 40 files_changed events, and 8 background_refresh_done events - source: JS Debug capture included in the task.
- The eight background_refresh_done values ranged from about 6.4 to 37.9 minutes (mean 23.5 minutes) under the capture's heading Slow SSE receive latency. Current debug source calls these oldest event age at receipt, and the broker replays retained events marked Replay; these are event ages, not transport delays - [85_debug_panel.js:5258](/home/keivenc/dev/yomux/yo7111/static_src/js/yomux/85_debug_panel.js:5258); [event_operations.go:1353](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/event_operations.go:1353); [event_operations.go:1362](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/event_operations.go:1362).
- General /api/events sends a ready resource-revision snapshot and a ping every 15 seconds; new subscribers may receive retained resource events - [main.go:2721](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:2721); [main.go:2727](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:2727); [main.go:2760](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:2760).
- Stats /api/stats-stream sends an acknowledgement, changed snapshot chunks, a small ready event, then product deltas/events. Heartbeats use min(resolution, 60 seconds); pending products wait for publication - [stats_runtime.go:2425](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_runtime.go:2425); [stats_runtime.go:2448](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_runtime.go:2448); [stats_runtime.go:2472](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_runtime.go:2472); [stats_runtime.go:2532](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_runtime.go:2532).
- UI polls /api/stats-refill every second while queued/running and every ten seconds while idle - [84_stats_current.js:1711](/home/keivenc/dev/yomux/yo7111/static_src/js/yomux/84_stats_current.js:1711); [84_stats_current.js:1746](/home/keivenc/dev/yomux/yo7111/static_src/js/yomux/84_stats_current.js:1746).
- Capture showed 51 GET /api/stats-refill calls in about 53 seconds, average 24.2ms, plus one POST. GET reads status/lease; POST starts refill work - [stats_refill.go:67](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_refill.go:67); [stats_refill.go:91](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_refill.go:91); [stats_refill.go:99](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_refill.go:99).
- The capture lacks stream URL. General /api/events ready includes resource revisions; stats-stream ready contains generation and revision only, so the large ready frame cannot be assigned to stats - [main.go:2727](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/main.go:2727); [stats_runtime.go:2532](/home/keivenc/dev/yomux/yo7111/cmd/yomux-go/stats_runtime.go:2532).

### Inferences

- Overall SSE rate and bandwidth are not excessive by themselves. Paired filesystem notifications were about 0.75 pairs/s in this sample; a short active window does not establish an idle baseline.
- Refill polling was frequent as designed while busy; requests averaged 24.2ms and transferred only 13,158 response bytes total, so the trace does not support it as a significant CPU source.
- The large ready frame is a one-time sample cost; a URL or server-side per-channel byte count is needed to identify its source.

### Gaps

- The capture summary lacks EventSource URL, channel, subscriber count, and replay bit, so it cannot attribute events to /api/events, /api/stats-stream, or multiple streams.
- A longer idle capture is needed to judge filesystem event volume.

## CPU diagnosis and what the measurements establish

### Takeaway

7111 was CPU-bound during the 18:44 profile, but stats explains a minority. The strongest stack is Finder's recursive directory-signature walk; investigate Finder first and treat stats as a secondary optimization target.

### Cited Findings

- The 18:44 profile showed internal/runtime/syscall/linux.Syscall6 at 17.61s flat (44.11%) and finder.(*Service).walkDescriptors.func1 at 29.75s cumulative (74.52%) - [profile](/tmp/yomux-yo7111-no-collectors.pprof); [service.go:178](/home/keivenc/dev/yomux/yo7111/internal/finder/service.go:178).
- Finder watch polling recomputes every watched path signature; directory signatures recursively call walkDescriptors  - [service.go:1325](/home/keivenc/dev/yomux/yo7111/internal/finder/service.go:1325); [service.go:1452](/home/keivenc/dev/yomux/yo7111/internal/finder/service.go:1452); [service.go:1657](/home/keivenc/dev/yomux/yo7111/internal/finder/service.go:1657).
- Stats tags were 13.58% in the same 133%-CPU profile, compared with 74.52% cumulative Finder walk time. Cumulative time includes descendants and must not be added to flat percentages  - [profile](/tmp/yomux-yo7111-no-collectors.pprof).
- Profiles, the user-provided 01:11 UTC debug capture, and the later 20.4% /proc sample occurred at different times and are not a matched experiment  - [profile](/tmp/yomux-yo7111-no-collectors.pprof); JS Debug capture in task; /proc/839213/stat measurement from this inspection.

### Inferences

- SSE traffic and refill GETs do not explain the CPU peak. The profile attributes most sampled CPU to Finder, with stats at about one-seventh.
- Stats lifecycle, five-second runtime-load sampling when enabled, and five-second token collection remain optimization targets, but tags do not identify CPU per SQL query.
- Do not treat the no-collectors-named profile as an off comparison; its collector tags and the invalid env alias show collectors were enabled.

### Gaps

- No matched profile with YOMUX_STATS_DISABLE_COLLECTORS=1 and identical Finder/browser load was captured, so collector CPU delta is unknown.
- No long idle CPU sample, endpoint CPU attribution, or SQLite query-level trace was captured.

# YOmux SSE lifecycle

## Is the measured SSE volume excessive?

### Takeaway
The supplied 53-second browser sample reports 92 SSE frames (1.74 frames/s) and 218,846 received bytes (4.13 KB/s, about 0.033 Mbit/s); the count is explainable by normal event classes and is not, by itself, excessive network traffic or evidence of high CPU. One connection frame is disproportionately large at 142,870 bytes, and its revision-map design can grow with historical resource keys, so connection/reconnection cost is a real issue even though steady-state throughput is modest.

### Cited Findings
- The provided JS Debug capture at 2026-10-13T01:11:31.791Z reports `events=200`, `api=92`, `sse=92`, `errors=0`, `removals=0`, and `sse_rx=218846B`; it shows 40 `fs_changed`, 40 `files_changed`, eight `background_refresh_done`, one `ready`, and three `ping` frames, which sum to 92. Source: user-provided JS Debug capture in this request.
- Calculated from that capture, SSE average throughput is 218,846 B / 53 s = 4,129 B/s, and mean event rate is 92 / 53 s = 1.74 frames/s; the ready frame is 65.3% of all received SSE bytes. Source: same capture.
- The browser debug recorder calculates `frameBytes` from the event name plus UTF-8 `data:` lines, and calculates displayed receive age as local `Date.now()` minus the server `envelope.time`; this is event age at browser receipt, not time spent processing the SSE request. [SSE debug recorder](../../static_src/js/yomux/99_client_event_transport.js#L243)
- I measured the current 7111 process as PID 839213, started 2026-10-12 18:44 PT, and `GET https://127.0.0.1:7111/healthz` returned HTTP 200 with Go version 0.10.4. An unauthenticated read of `/readyz` returned HTTP 401, so I did not obtain current in-memory event-broker counters or subscriber counts.

### Inferences
- The raw frame count is mostly two distinct filesystem notification types, not 92 independent expensive operations: 80 frames are 40 deliberate pairs, plus eight retained completions and the connection/heartbeat frames.
- The SSE bytes are too small to plausibly explain a process sustaining about 130% CPU on their own. The browser sample is consistent with moderate server-push traffic; it does not prove server-side event encoding/fanout is cheap under every subscriber count.
- The 142,870-byte `ready` frame is excessive for a reconnect handshake. Its one-off nature limits the steady-state bandwidth impact, but it adds JSON transfer and parsing work on each new connection.

### Gaps
- The capture does not include the exact selected channel list, current subscriber count, or server broker counters; authenticated read-only access was not available in this subtask, so per-subscriber fanout and current coalescing/drop rates remain unmeasured.
- The captured 142,870-byte `ready` map is larger than the current persisted snapshot measured in the local event journal; the exact revision-key count at the time of the browser capture is unavailable.

## Why do `fs_changed` and `files_changed` arrive as pairs?

### Takeaway
The pair is intentional: both events carry the same change payload but have separate event types and separate browser refresh owners. In the supplied capture, 40 of each means 40 notification pairs, or about 20 pairs per minute; this is moderate churn, but the log omits payload paths and watch-client IDs, so it cannot establish whether those 40 underlying notifications represent distinct filesystem changes or overlapping watches reporting the same change.

### Cited Findings
- The Finder watch poll iterates watch records independently and, for every record whose signature paths changed, builds one payload containing paths, roots, client ID, reason, token, and revision. [Finder `pollWatches`](../../internal/finder/service.go#L1394)
- For each changed watch record, the poller publishes `fs_changed`, then publishes `files_changed` if the first publication succeeds. [Finder pair publication](../../internal/finder/service.go#L1435)
- Browser handling is deliberately split: `fs_changed` calls `refreshFileExplorerFromPush`, while `files_changed` calls `refreshOpenFilesFromPush`. [Browser event handlers](../../static_src/js/yomux/99_client_event_transport.js#L701)
- Both types map to the `files` channel. The global client stream is created with an explicit selected channel set, and the broker filters publications by channel. [Event type/channel map](../../cmd/yomux-go/event_operations.go#L138) [Subscription handler](../../cmd/yomux-go/main.go#L2719)
- The broker coalesces only when the same subscriber already has a pending event with the same resource key; `fs_changed` and `files_changed` have different resource keys because the default event resource is the type. They therefore do not coalesce with one another. [Resource assignment and coalescing](../../cmd/yomux-go/event_operations.go#L1070)
- Polling emits one pair per changed watch record without calling the service's separate short-window `recordChange` coalescer. [Finder poll publication](../../internal/finder/service.go#L1416) [Native event coalescer](../../internal/finder/service.go#L1541)

### Inferences
- The two types are not duplicate wire copies by accident; they drive distinct UI refresh paths. Receiving both on a page with the Finder/files surfaces active is expected by the current contract.
- Because polling works per watch record, overlapping roots or multiple active browser watch IDs can amplify the number of pairs for a single physical edit. This is a plausible explanation for close-together same-size events, but the debug log lacks payloads and IDs needed to confirm that it happened here.
- In the sample, 40 pairs over 53 seconds correspond to 0.75 pairs/s, or 0.75 frames/s per type. The recent event-age values for `fs_changed` and `files_changed` (6.6 ms mean and 98.4/86.8 ms maxima) show they reached the browser promptly; they do not measure server compute time.

### Gaps
- Need a capture of event `payload.client_id`, `reason`, roots, and paths, or the protected server's `published_by_resource` and `coalesced_by_resource` counters, to distinguish real filesystem activity from duplicate notifications across watch records.
- The client work counters show `finderRefresh n=59` during the same debug window, but they do not map individual refreshes to event IDs; no direct one-to-one cause can be assigned.

## What do the large `ready` and old `background_refresh_done` frames mean?

### Takeaway
`ready` is a connection fence carrying a resource-to-revision snapshot, not a bulk content response. The eight old `background_refresh_done` frames are retained, replayed session-files completion notices, not events that took 6-38 minutes to deliver; however, the client currently passes their payloads to cache-only revalidation without checking the envelope's replay flag, so stale retained notices can trigger unnecessary cache reads after reconnect.

### Cited Findings
- On each `/api/client-events` request, the server subscribes first and sends `ready` with the current epoch and `resource_revisions` snapshot. The snapshot is read from all broker revision keys that match the selected channels. [Ready emission](../../cmd/yomux-go/main.go#L2719) [Filtered revision snapshot](../../cmd/yomux-go/event_operations.go#L408)
- Event publication increments a revision per resource; the in-memory revision map is not pruned when the event history is truncated to 256 entries. The event journal compaction persists both the bounded event list and the full revision map. [Revision update and bounded event history](../../cmd/yomux-go/event_operations.go#L1088) [Compaction call](../../cmd/yomux-go/event_operations.go#L1106)
- Read-only inspection of `/home/keivenc/.config/yomux/go-events.jsonl` found a persisted snapshot with 1,366 revision keys (1,282 `operation_terminal:*`, 57 `event_log_changed:*`, 19 `background:*`, plus eight fixed resources). Its compact JSON revision map alone is 68,111 bytes. This is persisted disk state, not a live authenticated broker snapshot; it demonstrates how dynamic resource keys accumulate, but does not exactly reproduce the browser's 142,870-byte ready frame.
- The server retains at most eight `background_refresh_done` events for session-files role; their resource keys include the 64-character cache-view ID. [Retention limit](../../cmd/yomux-go/main.go#L278) [Session-files retention condition](../../cmd/yomux-go/event_operations.go#L155)
- On a new subscription, the broker marks retained events `Replay=true` and queues them after filtering to selected channels. [Retained replay](../../cmd/yomux-go/event_operations.go#L1353)
- The session-files producer publishes a redacted completion with the session, cache-view ID, and completion generation. The browser listener passes both payload and envelope into the central handler, but the `background_refresh_done` branch invokes revalidation with payload only and does not test the envelope replay field before calling cache-only `fetchSessionFiles`. [Completion publisher](../../cmd/yomux-go/session_files_cache.go#L548) [Dispatch drops envelope for payload handler](../../static_src/js/yomux/99_client_event_transport.js#L1087) [Completion revalidation](../../static_src/js/yomux/99_client_event_transport.js#L604) [Cache-only read](../../static_src/js/yomux/86_changes_10_session_files.js#L815)
- The supplied sample's background completion ages range from 382,713 ms (6.4 minutes) to 2,274,087.8 ms (37.9 minutes), with an average age of 1,411,056 ms (23.5 minutes); all eight arrived together at 01:11:02Z. These ages align with replay of old publish timestamps. Source: user-provided JS Debug capture in this request.

### Inferences
- Resource revisions are durable reconnect metadata, and the implementation's dynamic keys (especially unique operation-terminal IDs) grow independently of the 256-event history limit. The 68 KB persisted map supports the conclusion that `ready` size can grow over server lifetime; the captured 142,870-byte frame likely reflects a larger or different point-in-time state, but exact reconciliation is unavailable.
- The UI should not treat the large ready byte count as a large state payload: its source is revision metadata. Still, sending every old resource key on each connection has a real transfer/parse cost; a bounded snapshot strategy or pruning/partitioning revision keys merits investigation.
- Replay latency values are misleading when read as network latency. They measure wall-clock age since original publish because `Replay` does not rewrite `Time` and the browser computes age from `envelope.time`.
- The frontend comment says replayed completions are rejected, but the current dispatch path does not pass the top-level replay flag to `refreshSessionFilesCompletionSurfaces`. Its identity map suppresses repeats only within the same live page state; on fresh load, distinct retained cache-view IDs can each schedule a cache-only read. This is a source-backed potential inefficiency, not proof that the eight reads account for CPU or the observed `/api/session-files` count.

### Gaps
- The channel list and resource keys included in the exact 142,870-byte ready frame were not captured, and the current runtime protected diagnostics endpoint returned 401 without credentials; the live frame's exact composition cannot be proven from this evidence.
- The browser sample reports eight old completions and ten `sessionFilesRefresh` calls, but debug counters do not establish that those eight replayed notices caused exactly eight of those calls.
- No measured CPU profile correlates SSE serialization, broker fanout, browser parsing, or replay-driven cache reads to the high CPU; this SSE evidence alone cannot attribute that CPU.

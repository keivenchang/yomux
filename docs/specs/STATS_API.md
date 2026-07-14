# YO!stats API contract

`GET /api/stats-sample` returns the current server sample and optional durable
history. Integer query parameters are non-negative; `history_resolution` is
at most 86,400 seconds and `history_max_points` at most 100,000. `history=0`
returns no history. `token_*` parameters select independently cursorable
Agent/Model token history. The GET reads the latest cached CPU sample and
durable history; it never invokes CPU, Agent Status, GPU, memory, or transcript
collectors in the request thread.

`POST /api/stats-history` accepts at most 1,000 records and 128 KiB.
`ack_only` returns an empty record list and durable sequence, never a retained
history response. Both HTTP surfaces use readonly authentication. A statsd
failure is HTTP 503 with `stats.error.unavailable`; there is no in-process
history fallback.

Successful history has `sequence`, `latest_sequence`, `records`, `coverage`,
and `agent_token_schema_version`. Coverage is required when history is enabled.
Its authoritative shape is a bounded `intervals` list (at most 256 entries),
where each entry has inclusive `start`, exclusive `end`, resolution, source,
epoch, and owner-generation facts. Empty is a valid list and means that none of
the requested range is known to be covered. Prefix, suffix, middle, and multiple
gaps are ordinary data shapes; clients render returned records and paint every
uncovered span as no data. They are never classified as unusable partial
responses and never cause an automatic refetch loop.

`coverage.stores` and its compact `store_intervals` projection report the same
bounded facts independently for raw/server/rollup data and the CPU, memory, GPU,
Agent Status, Agent tokens, and cost families. A chart may claim data only in
its own family's intervals. A checked token sample with no delta records covered
zero usage; an absent sample remains an uncovered gap and must not become a
silent zero. Agent Status carry-forward ends at an epoch boundary, so sleep,
restart, owner change, or a wall-clock discontinuity cannot paint a stale roster
through the outage. `epochs` describes those bounded sampler segments and
`epochs_truncated` reports list truncation.

The requested/available/covered bounds, `complete`, older-page facts,
resolution, source/returned counts, and cursor facts remain as a compatibility
envelope during migration, but they do not replace the interval list or prove
that a middle span is covered. Missing or malformed `intervals` is a contract
violation; transport failure is the other bounded Retry state. Ranges are
inclusive `start`, exclusive `end`. A legacy schema-2 read-only database derives
intervals and epochs from retained bucket gaps without mutating the database.

Range reads may use persisted rollups, but one instant has one durable source;
consumers must not sum overlapping raw and rollup records. A lower-bound or
truncated cost summary is explicitly incomplete while token counters remain
independently visible.

Pricing is an offline-first, effective-dated catalog. The packaged seed covers
current common model families with reviewed HTTPS provenance, check dates, and
rate effective dates. Provider pricing tables have first priority within a
refresh; structured LiteLLM JSON fills omitted models, and OpenRouter remains
corroboration only. Resolution priority is explicit override, exact official
evidence, visibly labeled conservative family inference, then seed. A partial
refresh never deletes older official coverage: omitted aliases retain their
original revision/source evidence and the refresh result and log identify them.

Only the elected background owner schedules catalog refresh. Seed-only or
stale state receives a jittered startup attempt; success schedules the next
daily attempt, while failure publishes status and retries with bounded
exponential backoff. Fetching and parsing run on a daemon worker and never
block an HTTP request or startup thread.

statsd is the sole SQLite writer. A separate persistent `stats-reader` service
opens the same WAL database with SQLite `mode=ro` and `PRAGMA query_only=ON`.
It accepts only `history`, `write_encoded_history`, and
`write_encoded_sample` (plus lifecycle/status actions), and returns the
history envelope or pre-encoded bounded JSON bytes plus metadata. Long-range
aggregation therefore cannot occupy the statsd listener that owns CPU writes.
Bucket queries and merges are bounded; merges accept at most 1,000 records. A
timed-out current-protocol request is not replayed through legacy transport.
Restart/failover may return unavailable, but subsequent cursors remain
monotonic and stable usage-event IDs are idempotent.

## stats RPC actions

All actions use the local framed RPC transport, require an `action` string,
and return an object with `ok` unless the table names a pre-encoded byte result.
Validation errors are bounded `{ok:false,error}` replies. SQLite and merge
errors do not fall back to a second writer or replay through a legacy request.

| Family | Actions | Request / result contract |
| --- | --- | --- |
| Lifecycle | `ping`, `status`, `profile`, `lease`, `release`, `shutdown_if_idle`, `shutdown` | Protocol/process identity, bounded diagnostics, lease identifiers, or an explicit shutdown decision. These actions never return history bytes. |
| Sampler diagnostics/demand | `set_token_consumer_until`, `update_sampler_family`, `mark_sampler_success` | A finite token-consumer deadline, named bounded family status, or compatibility success timestamp. Family status exposes cadence, attempt/success/failure, late/missed, runtime, alive/running, and failure text. There is no statsd-to-web sampler-owner RPC. |
| Bucket reads | `stats-reader: history`; `statsd: query_buckets` | The public history envelope is isolated on the read-only peer; the writer's bounded normalized bucket query remains an internal maintenance/compatibility action. `since`/`after_sequence`, inclusive `start`, exclusive `end`, resolution, client, token, and point-limit selectors are non-negative. |
| Encoded reads | `stats-reader: write_encoded_history`, `write_encoded_sample` | Metadata `{ok:true,encoding:"json",size}` plus one JSON byte frame. Sample bytes contain the public sample, optional history, and shared-owner facts. A compatibility replacement commits through statsd first, then reads the committed WAL snapshot through stats-reader. |
| Browser writes | `merge_records`, `merge_and_history` | At most 1,000 normalized browser records plus client/cursor facts. The first returns a compact changed/sequence acknowledgement; the compatibility form returns that acknowledgement plus a bounded history envelope. |
| Server writes | `upsert_bucket`, `merge_server_records`, `replace_buckets`, `retain_after` | Single-writer normalized upsert/merge, explicit maintenance replacement, or retention cutoff; returns changed/sequence/store diagnostics, never an unbounded bucket echo. |
| Token counters | `claim_agent_token_deltas`, `claim_agent_token_deltas_from_rows`, `recover_agent_token_history`, `recover_agent_token_history_from_rows`, `finish_agent_token_scan` | Validated counter/row snapshots or a scan identifier. Large filesystem work is single-flight and detached; finish/drain responses stay metadata-bounded and stable event IDs make retries idempotent. |
| Usage/cost maintenance | `migrate_usage_atom_history_from_rows`, `maybe_reproject_cost_summaries`, `reproject_cost_summaries` | Bounded source rows or explicit reprojection request; returns progress/completeness, catalog revision, changed counts, and missing-source facts. Cost truncation remains an explicit lower bound. |

The elected app runs independent non-overlapping family workers: CPU every
second, Agent Status and GPU every 10 seconds, system memory every 60 seconds,
and tokens every 10 seconds while watched or 60 seconds while idle. Each
worker merges only its partial record into statsd; a slow family cannot delay
another family's deadline. Hot family writes address the live socket directly
and do not synchronously compact history or rebuild rollups. Queued RPC writes
run before maintenance; after a real listener-idle turn, statsd round-robins
one bounded token, cost, rollup, or retention step. Browser/client uploads also
enqueue rollup and retention work instead of compacting the full store inline.
History and response encoding use stats-reader's independent read-only process;
the System view reports both services separately, including process/version,
socket, health, cache/failure, queue, and resource facts.
Retention atomically advances one expired or mis-tiered row per turn and lets
concurrent live writes schedule the next frozen pass. A timeout never triggers daemon launch
arbitration; only a structured absent/refused socket does. CPU uses a
0.9-second durable-write deadline. Token demand wakes only the token worker.

`GET /api/client-events` publishes `stats_sample` on the `stats` channel only
after the partial record is durable. Its payload has a monotonic durable
`sequence`, a current cached CPU `sample`, and one normalized one-second
partial `record`. It advances the live tail only; it does not
prove older range coverage. Reconnect or server-identity change therefore
forces a zero-cursor history read, while an SSE outage falls back to the
bounded polling cadence without inventing samples.

Delivery demand is range-scaled and does not change collection cadence. A
visible shared YO!stats/YO!cost range of 5m or 15m subscribes to `stats`; a
range of 30m or longer, or a fixed historical zoom, omits that channel and
uses one `/api/stats-sample` history request per 60 seconds. Returning to a
short live range performs an immediate bounded backfill and restores SSE.
Hidden pages have neither stats demand nor graph polling. The broker's
aggregate `stats` demand gates `publish_client_event` before event JSON is
constructed, so per-family durable merges do no per-second publish
serialization when every connected stats view is coarse or hidden.

Contract coverage lives in `tests/test_server_query.py` (HTTP auth, bounds,
503/413 taxonomy, one-attempt failure), `tests/test_app.py` (GET/POST mapping
and compact acknowledgement), `tests/test_statsd.py` (RPC shapes, coverage,
encoded bytes, cursor/idempotency, truncation, restart, and budgets), and the
debug-panel Node/browser suites (malformed/partial coverage, retry/backoff,
range ownership, restart, and SSE live-tail behavior).

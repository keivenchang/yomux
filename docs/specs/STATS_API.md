# YO!stats API contract

`GET /api/stats-sample` returns the current server sample and optional durable
history. Integer query parameters are non-negative; `history_resolution` is
at most 86,400 seconds and `history_max_points` at most 100,000. `history=0`
returns no history. `token_*` parameters select independently cursorable
Agent/Model token history.

`POST /api/stats-history` accepts at most 1,000 records and 128 KiB.
`ack_only` returns an empty record list and durable sequence, never a retained
history response. Both HTTP surfaces use readonly authentication. A statsd
failure is HTTP 503 with `stats.error.unavailable`; there is no in-process
history fallback.

Successful history has `sequence`, `latest_sequence`, `records`, `coverage`,
and `agent_token_schema_version`. Coverage is required when history is enabled:
it includes requested/available/covered bounds, `complete`, older-page facts,
resolution, source/returned counts, and cursor facts. Empty + complete means
the requested interval is fully covered, but may have no records after its
cursor; incomplete means partial data and is never a full-range success.
Missing/malformed coverage is a contract violation, not empty history. Ranges
are inclusive `start`, exclusive `end`.

Range reads may use persisted rollups, but one instant has one durable source;
consumers must not sum overlapping raw and rollup records. A lower-bound or
truncated cost summary is explicitly incomplete while token counters remain
independently visible.

statsd is the sole SQLite writer. `ping`/`status` are bounded control reads;
`history` returns this envelope, while `write_encoded_history` and
`write_encoded_sample` return pre-encoded bounded JSON bytes plus metadata.
Bucket queries and merges are bounded; merges accept at most 1,000 records. A
timed-out current-protocol request is not replayed through legacy transport.
Restart/failover may return unavailable, but subsequent cursors remain
monotonic and stable usage-event IDs are idempotent.

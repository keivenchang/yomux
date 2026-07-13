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

## statsd RPC actions

All actions use the local framed RPC transport, require an `action` string,
and return an object with `ok` unless the table names a pre-encoded byte result.
Validation errors are bounded `{ok:false,error}` replies. SQLite and merge
errors do not fall back to a second writer or replay through a legacy request.

| Family | Actions | Request / result contract |
| --- | --- | --- |
| Lifecycle | `ping`, `status`, `profile`, `lease`, `release`, `shutdown_if_idle`, `shutdown` | Protocol/process identity, bounded diagnostics, lease identifiers, or an explicit shutdown decision. These actions never return history bytes. |
| Sampler control | `set_sampler_owner`, `set_token_consumer_until`, `mark_sampler_success` | A validated owner control socket, finite consumer deadline, or sample time; returns the accepted owner/deadline/success timestamp. |
| Bucket reads | `history`, `query_buckets` | The history envelope above, or a bounded normalized bucket list. `since`/`after_sequence`, inclusive `start`, exclusive `end`, resolution, client, token, and point-limit selectors are non-negative. |
| Encoded reads | `write_encoded_history`, `write_encoded_sample`, `replace_and_write_encoded_history` | Metadata `{ok:true,encoding:"json",size}` plus one JSON byte frame. Sample bytes contain the public sample, optional history, and shared-owner facts. Replacement is a compatibility migration transaction, not an interactive read. |
| Browser writes | `merge_records`, `merge_and_history` | At most 1,000 normalized browser records plus client/cursor facts. The first returns a compact changed/sequence acknowledgement; the compatibility form returns that acknowledgement plus a bounded history envelope. |
| Server writes | `upsert_bucket`, `merge_server_records`, `replace_buckets`, `retain_after` | Single-writer normalized upsert/merge, explicit maintenance replacement, or retention cutoff; returns changed/sequence/store diagnostics, never an unbounded bucket echo. |
| Token counters | `claim_agent_token_deltas`, `claim_agent_token_deltas_from_rows`, `recover_agent_token_history`, `recover_agent_token_history_from_rows`, `finish_agent_token_scan` | Validated counter/row snapshots or a scan identifier. Large filesystem work is single-flight and detached; finish/drain responses stay metadata-bounded and stable event IDs make retries idempotent. |
| Usage/cost maintenance | `migrate_usage_atom_history_from_rows`, `maybe_reproject_cost_summaries`, `reproject_cost_summaries` | Bounded source rows or explicit reprojection request; returns progress/completeness, catalog revision, changed counts, and missing-source facts. Cost truncation remains an explicit lower bound. |

`GET /api/client-events` publishes `stats_sample` on the `stats` channel. Its
payload has a monotonic durable `sequence`, a current `sample`, and one
normalized one-second `record`. It advances the live tail only; it does not
prove older range coverage. Reconnect or server-identity change therefore
forces a zero-cursor history read, while an SSE outage falls back to the
bounded polling cadence without inventing samples.

Contract coverage lives in `tests/test_server_query.py` (HTTP auth, bounds,
503/413 taxonomy, one-attempt failure), `tests/test_app.py` (GET/POST mapping
and compact acknowledgement), `tests/test_statsd.py` (RPC shapes, coverage,
encoded bytes, cursor/idempotency, truncation, restart, and budgets), and the
debug-panel Node/browser suites (malformed/partial coverage, retry/backoff,
range ownership, restart, and SSE live-tail behavior).

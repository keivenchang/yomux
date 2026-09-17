# YO!stats Go Implementation

This document describes the current Go YO!stats implementation and its differences from the Python backend. Python remains the reference and rollback owner. The Go process owns its own storage, collectors, materialization, and HTTP/SSE serving; it does not call Python `statsd` over RPC.

## Runtime Shape

Python and Go expose the shared browser's YO!stats contract through different process topologies.

| Area | Python backend | Go backend |
| --- | --- | --- |
| Writer owner | Dedicated `statsd` local service | The one `yolomux-go` process |
| Web/API owner | Python web process authenticates and forwards statsd output | Go handlers authenticate and query/project directly |
| Database | Python current stats database and writer fence | `stats-v9.sqlite3`, schema 9, Go writer fence |
| Materialization | statsd lifecycle and persisted aggregate rings | `internal/stats` lifecycle/materializer/rings in process |
| Collectors | Elected Python web owner pushes originals to statsd | In-process Go host, agent-status, and token collectors append through the Go writer |
| Stream | Python web process forwards statsd snapshots/deltas | Go `/api/stats-stream` publishes snapshots and deltas directly |
| Service protocol | Versioned local Unix RPC | No stats RPC boundary |

The two backends intentionally do not share a live writer or service socket. Do not point Python statsd and the Go preview at the same database file. Use separate private config/state roots during comparison and tests.

## Source Map

| Source | Responsibility |
| --- | --- |
| `cmd/yolomux-go/stats_runtime.go` | Route adapters, migration, runtime collector startup/shutdown, snapshots, deltas, SSE, readiness, pricing, and diagnostics |
| `internal/stats/contracts.go` | Observation, coverage, unavailable-span, usage-atom, and tombstone validation |
| `internal/stats/writer.go` and `writer_lock.go` | Sole write connection, lease/fence, atomic append, generation changes, and lifecycle attachment |
| `internal/stats/yostats.go` and `reader.go` | Read-only compatibility checks and bounded fact reads |
| `internal/stats/materializer.go`, `projection.go`, and `snapshot.go` | Fact projection, materialized series, cost detail, no-data spans, and browser snapshot shape |
| `internal/stats/ring.go` and `lifecycle.go` | Aggregate ring publication, invalidation, prune/checkpoint lifecycle, and bounded recovery |
| `internal/stats/host_collector.go`, `host_collectors.go`, and `host_sources.go` | CPU, process, memory, GPU, and system metadata collection |
| `internal/stats/agent_inventory.go`, `agent_status.go`, and `agent_status_query.go` | Agent discovery, working/waiting classification, durable status facts, and status snapshots |
| `internal/stats/transcripts.go`, `usage_normalization.go`, and `agent_tokens.go` | Claude/Codex transcript scanning, cumulative-to-delta normalization, token facts, and pricing identities |
| `internal/stats/opencode.go` | Read-only OpenCode SQLite validation, bounded session-tree traversal, step-finish token extraction, and cursor data |
| `internal/stats/pricing.go` | Repository/configured pricing catalog and model-rate resolution |

## Registered Routes

The Go server registers:

- `GET /api/stats-capabilities`
- `GET /api/stats-agent-status`
- `POST /api/stats-observations`
- `GET /api/stats-snapshot`
- `GET /api/stats-delta`
- `GET /api/stats-stream`
- `GET|POST /api/pricing-catalog`
- authenticated `GET /readyz`

`pane.stats.v1` admits the shared browser pane. The route handlers still enforce method, authentication, role, request-size, range/resolution, and payload validation; capability admission is not a substitute for those checks.

## Database And Writer Fence

The default Go database is `<config-dir>/stats-v9.sqlite3`, or the explicit `YOLOMUX_STATS_DATABASE`/`--stats` path. Its compatibility header is:

| Field | Current Go value |
| --- | --- |
| SQLite `application_id` | `0x594F5354` (`YOST`) |
| SQLite `user_version` | `9` |
| Minimum writer protocol | `25` |
| Minimum writer build | `8` |

`OpenWriter()` validates the sidecar writer fence before SQLite mutation, acquires one process lease, opens one write-capable connection, enables WAL, foreign keys, a 5-second busy timeout, `synchronous=NORMAL`, and a 1,000-page auto-checkpoint, then publishes the writer fence only after schema validation. `OpenReadOnly()` uses `mode=ro`, enables query-only mode, validates the application id, exact schema, minimum writer protocol/build, core table shape, and all-or-nothing ring shape.

The core tables are:

- `schema_meta`
- `observations`
- `coverage_epochs`
- `unavailable_spans`
- `usage_atoms`
- `migration_reconciliation`

The ring tables are:

- `aggregate_publication`
- `aggregate_rings`
- `aggregate_ring_slots`
- `ring_replay_cursor`
- `ring_invalidations`

A partially present ring schema is incompatible. An older core-only database can be inspected for migration, but serving code does not treat missing ring tables as empty exact history.

## Fact And Append Contract

One `AppendBatch` can contain observations, coverage epochs, unavailable spans, usage atoms, and usage tombstones. Validation happens before the transaction. Text identities, timestamps, cadence, owner generations, quantities, JSON payload size, and idempotency keys are bounded. One invalid record rejects the batch; a conflicting idempotency key never overwrites the stored event.

Accepted facts update `source_generation` atomically. Exact duplicate events are counted and do not create a second row. Coverage and unavailable spans cannot overlap for the same family/source interval. Usage tombstones remove exactly matching atoms and are represented by the deletion rather than a second durable tombstone table.

The database stores originals and coverage truth. Browser-ready series are derived from those facts and the selected pricing catalog; collectors do not pre-format chart payloads.

## Aggregate Rings

Go maintains bounded rings for 1, 10, 60, and 300 second resolutions. Current capacities are 300, 180, 480, and 288 slots respectively. A ring publication atomically advances `ring_generation`, records the source generation it covers, and writes complete bucket payloads with payload version 2.

Each encoded bucket is capped at 256 KiB and may use the `YRB1` zlib representation when compression is smaller than JSON text. Reads reject malformed, oversized, stale-generation, missing, or partially published slots. Invalidations name the exact resolution/bucket/source generation owed; publication marks only the cells it actually repaired.

The lifecycle retains two days of original facts, limits retired invalidations to 5,000, checkpoints the WAL, and enforces an 8 MiB WAL allocation ceiling in its contract. A server restart reopens the database, validates the publication metadata, and continues from durable ring/source generations.

## Snapshot, Delta, And SSE Flow

The browser selects a range and resolution. `/api/stats-snapshot` validates that selection, opens a read-only view, and returns one generation-tagged exact snapshot with availability/no-data metadata. `/api/stats-delta` compares the client's generation/cursor and returns the bounded next state. `/api/stats-stream` sends SSE snapshot/delta events and heartbeats, and closes when the request context ends.

The first request owns the loading state. A later range/resolution change replaces the accepted generation instead of merging incompatible buckets. A missed generation or reconnect returns to one exact snapshot before consuming new deltas.

`/readyz` opens and validates the Go database. It returns `503` when the database cannot be served and `200` with source/cache generation, pending-cell count, migration state, materializer state, and ring failure fields when ready. `/livez` is the listener liveness path and does not certify exact stats readiness.

## Host Collection

The Go host collector owns:

- CPU deltas for the host and configured process groups.
- Process census and resident memory.
- System memory and platform metadata.
- GPU, accelerator, or macOS GPU/system-profiler inputs when the current platform source supports them.
- Coverage and explicit unavailable spans when a family cannot be sampled.

CPU needs two counter samples before it can produce a rate; the first sample is not written as a fabricated zero. Memory is an absolute read and can be written immediately. The default memory cadence is one minute; CPU and other runtime cadences are set by the runtime collector configuration in `stats_runtime.go`.

`host_sources.go` contains Linux and Darwin source types, but the current server wiring and Docker evidence are Linux-first. The existence of a Darwin adapter is not cross-platform certification.

## Agent Status

The Go inventory joins process discovery with tmux pane identity and OpenCode session state. It classifies supported clients into working, waiting, idle, or unknown states using typed sources instead of scraping one generic process label. The status collector writes durable facts and exposes a current snapshot through `/api/stats-agent-status`.

The agent-status cadence is ten seconds while active. Snapshot freshness includes revision, counts, roster, token rates, provider/model rates, and unavailable reasons. The Docker integration test starts fixture-owned fake clients, tmux, state, and HTTP ports; it does not inspect an operator's sessions.

## Claude And Codex Tokens

The scanner reads bounded JSONL transcript increments, normalizes cumulative usage counters into deltas, and creates stable usage-atom identities for input, output, reasoning, cache-read, and cache-write quantities. It handles transcript replacement/truncation, malformed tails, partial final records, source identity, and pricing metadata through explicit receipts and unavailable results.

Current Claude/Codex scan state is held in the Go process. A restart can rescan bounded transcript history and rely on durable atom idempotency, but it does not yet persist the complete per-source scanner cursor/state that Python has accumulated. This is a known restart-efficiency and edge-case parity gap.

## OpenCode

OpenCode is read from its own SQLite database in read-only/query-only mode. The adapter validates the expected schema, caps each part read at 2,048 rows, caps a session tree at 64 sessions, and extracts token/cost data only from supported `step-finish` parts. Parent/child sessions retain their own identity and are not collapsed into one pane total.

The OpenCode cursor has a durable sidecar, unlike the in-memory Claude/Codex transcript scanner state. Discovery and process/session joins currently use Linux runtime adapters. Unsupported schema or missing data produces an unavailable result rather than mutating the OpenCode database.

## Pricing And Cost

The Go server loads the repository pricing catalog or `YOLOMUX_PRICING_CATALOG`, resolves provider/model aliases, and materializes micro-USD and API-list micro-USD from the same usage atoms used for token charts. `/api/pricing-catalog` exposes the public catalog; authenticated POST refreshes the configured source in place.

Unknown or ambiguous rates stay attributed as unavailable pricing evidence. The materializer does not silently assign a nearby model's price.

## Migration And Python Interoperability

Go includes compatibility and migration tests for Python-created schema/facts and Go-created databases. Migration uses a private destination, validates the source header and digest, copies all supported source families, records reconciliation, and activates the destination only after the complete migration succeeds. A failed migration does not replace the active database.

Interoperability tests prove selected schema, append, read-only, family, and pricing shapes. They do not prove that every Python snapshot byte, daemon lifecycle transition, collector edge case, or long-history performance characteristic is identical.

Python statsd cutover is not part of the Go preview. Python continues to open and serve its own current storage path and RPC protocol. Go runs beside it with an isolated root for comparison and rollback.

## Current Gaps

- Python remains the reference and rollback owner.
- The Python statsd/local-RPC lifecycle is not replaced for Python launches; Go uses a different ownership model.
- Claude/Codex transcript scanner state is in memory except for durable usage atoms; only the OpenCode cursor has a sidecar.
- Runtime agent/OpenCode discovery is wired to Linux adapters in the current server path.
- Complete macOS collection, process discovery, and browser end-to-end parity are not established.
- Exact Python-versus-Go snapshot/delta/diagnostic byte comparison is incomplete.
- Long-history migration, corrupted-source recovery, pricing-catalog drift, and production-scale CPU/memory/latency evidence remain incomplete.
- Python has more mature service supervision, cross-port sharing, operational diagnostics, and rollback evidence. Go keeps these owners in one process and must be evaluated as that architecture, not documented as a statsd clone.

## Validation

The Bazel graph includes Go stats unit and race suites, Python interoperability tests, writer locking/integration, schema/ring/materializer/snapshot/collector tests, agent-status Docker E2E, and YO!stats Chromium Docker E2E. The canonical command is:

```bash
tools/run_bazel_tests.sh --jobs "$(getconf _NPROCESSORS_ONLN)"
```

The host wrapper runs inside Docker with read-only source, private HOME/XDG/TMP/tmux/state/ports, private per-invocation Bazel and Go writable roots, and test-runtime networking disabled. A live 7221 check is optional final human review and cannot replace this gate.

## Related Documents

- [`GO_MIGRATION.md`](GO_MIGRATION.md): complete Go backend capability and route status.
- [`docs/specs/STATS_API.md`](docs/specs/STATS_API.md): shared browser contract plus backend-specific storage/serving details.
- [`docs/specs/STATS_BOUNDED_REBUILD.md`](docs/specs/STATS_BOUNDED_REBUILD.md): Python statsd bounded-rebuild design and how it differs from Go's in-process lifecycle.
- [`docs/specs/BACKEND_ARCHITECTURE.md`](docs/specs/BACKEND_ARCHITECTURE.md): Python service topology and Go modular-monolith topology.
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): build, Docker, Bazel, and preview startup instructions.

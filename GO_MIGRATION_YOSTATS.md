# YO!stats Python Study and Go Migration Notes

This document records the current Python YO!stats implementation as found in
the `feat/golang-phase12` worktree. It is an implementation study, not a new
product design. The purpose is to preserve the important behavior while
eventually moving YO!stats into the Go `yolomux` process.

The target is one Go executable and one selected backend process, with stats
work isolated behind named goroutines and, only where justified by measured
failure or resource behavior, an internal worker process. The target is not
multiple copied Python-style daemons, nor concurrent Python and Go writers for
the same database.

## Source Map

The main owners are:

- `yolomux_lib/stats_current/storage.py`: current SQLite schema, validation,
  append transactions, pruning, ring storage, writer/read-only opening, WAL,
  fences, and vacuum.
- `yolomux_lib/stats_current/service.py`: the `statsd` local RPC service,
  single-writer lifecycle, scheduling, host collection, token collection,
  materialization, ring publication, cache state, readiness, and diagnostics.
- `yolomux_lib/stats_current/materializer.py`: pure fact-to-series and
  fact-to-cost projection, coverage gaps, layers, generations, and cost detail.
- `yolomux_lib/stats_current/families.py`: the declarative family, cadence,
  payload, fold, series, and no-data contract.
- `yolomux_lib/stats_current/collectors.py`: conversion of successful native
  samples into typed observations, coverage epochs, usage atoms, tombstones,
  and unavailable spans.
- `yolomux_lib/stats_current/host_collectors.py`: Linux/macOS CPU, process
  census aggregation, GPU, memory, and hardware metadata collection.
- `yolomux_lib/stats_current/transcripts.py`: bounded incremental transcript
  scanning and provider-independent usage atom construction.
- `yolomux_lib/workspace/session_files.py`: Claude and Codex JSONL parsing,
  provider token normalization, cumulative-counter delta handling, and parser
  state definitions.
- `yolomux_lib/stats_current/agent_tokens.py`: combines Claude/Codex transcript
  scans and OpenCode SQLite reads into one append batch.
- `yolomux_lib/stats_current/opencode.py`: read-only OpenCode SQLite access,
  identity resolution, `step-finish` token extraction, tool-history reads,
  activity state, and the cursor sidecar.
- `yolomux_lib/stats_current/usage.py`: canonical usage atom validation and
  normalization.
- `yolomux_lib/stats_current/pricing.py`: pricing catalog and cost projection
  inputs.

The route and wire owners are `stats_current/http.py`,
`stats_current/protocol.py`, `stats_current/client.py`, `http_routes.py`, and
the stats stream implementation in `server.py`.

## Runtime Shape

Python has a web process and a persistent `statsd` service. The web process
does not own the stats database. It talks to `statsd` through a local RPC
socket. `statsd` owns:

- the writable current YO!stats SQLite connection;
- the materializer reader connection used by its worker thread;
- host CPU, memory, process, and GPU sampling;
- transcript and OpenCode usage collection;
- append validation, deduplication, retention, and pruning;
- materialized public/private snapshots and deltas;
- persisted aggregate rings and repair state;
- readiness, health, and operational counters.

The service uses a singleton/lease boundary so only one compatible stats
writer owns the database. The current service has a listener-side request path
and a worker thread named `yolomux-stats-materializer`. SQLite connections are
thread-owned. The worker opens its own reader and writer handles, while
`work_lock` serializes operations that must not overlap the listener's append
or prune activity.

The worker loop performs, in order around its deadline set:

1. Flush eligible buffered appends.
2. Prune expired facts.
3. Run quiet-gated vacuum if due.
4. Collect and append host facts.
5. Collect and append agent-token facts.
6. Build pending materialization work.
7. Flush/publish the aggregate ring if due.

The important consequence is that collection, persistence, materialization,
and HTTP serving are separate responsibilities even though they are owned by
one logical service.

## Current SQLite Database

### Identity and compatibility

The current store is intentionally side-by-side and fenced:

- Application ID: `0x594F5354` (`YOST`).
- Schema version: `common.STATS_SCHEMA_VERSION`, currently the schema-8 ring
  contract in this worktree.
- Database filename is versioned by the common stats configuration.
- Writer protocol and minimum writer build are recorded in `schema_meta` and
  an atomic JSON writer-fence sidecar.
- A socket name includes protocol and schema generation, preventing a process
  from accidentally discovering an incompatible stats service.
- The database is host-partitioned. The socket is host-local because a Unix
  socket cannot safely live on NFS.

Before opening a writer, Python validates the existing file read-only,
checks the application ID, `user_version`, schema shape, and writer fence, and
performs mutable-root preflight. A newer schema or writer fence fails closed.
Migration/recovery is a separate owner; opening a malformed database is not
treated as successful compatibility.

### Core tables

The base schema is exact, validated by table and column shape, and uses
`WITHOUT ROWID` for the fact tables.

| Table | Primary purpose | Key |
| --- | --- | --- |
| `schema_meta` | Singleton writer metadata, source generation, last vacuum marker | `singleton = 1` |
| `observations` | Original sampled facts such as CPU, GPU, memory, service load, browser, and agent status | `(family, source_id, event_id)` |
| `coverage_epochs` | Intervals during which a collector claims to have sampled a source at a cadence | `(family, source_id, epoch_id)` |
| `unavailable_spans` | Explicit intervals where a source could not be measured | `(family, source_id, epoch_id, started_at, ended_at)` |
| `usage_atoms` | Provider/transcript token, image, or request facts | `(event_id, direction, modality, cache_role, unit)` |
| `migration_reconciliation` | Completed migration/recovery evidence and source digest | `migration_id` |

Payloads remain JSON because the family contract evolves and the original
fact must remain available for deterministic re-materialization. The row
columns carry identity, time, epoch, and ownership metadata; `payload_json`
contains the bounded family-specific values.

Indexes include observation, coverage-end, unavailable-end, and usage-time
indexes. The current schema also has browser diagnostic indexes derived from
browser observations. Those indexes are not a replacement for the original
observations; the originals remain authoritative.

### Ring extension

The current store adds a complete ring extension, or none of it. Partial ring
creation is rejected. Its tables are:

- `aggregate_publication`: singleton published ring generation, source
  generation, and publication time.
- `aggregate_rings`: one row per resolution and its slot count/head.
- `aggregate_ring_slots`: modular bucket slots containing bounded JSON bucket
  payloads, completeness, source generation, ring generation, and publish
  time.
- `ring_replay_cursor`: one durable cursor per resolution identifying the
  observed-time boundary and source generation folded into the ring.
- `ring_invalidations`: durable ledger of published buckets contradicted by
  later fact changes, deletions, or tombstones.

Ring bucket JSON is bounded and may be zlib-compressed with a format magic.
The ring is a cache of materialized facts, not the source of truth. A missing
or invalidated slot must be rebuilt from original observations/usage atoms.
Slot addressing is `(bucket_start / resolution) % slot_count`; this formula
must have exactly one owner in Go.

### Sidecars

The database is accompanied by versioned sidecars:

- Writer compatibility fence JSON.
- Persisted prune schedule state.
- Vacuum baseline containing the last vacuum marker and reclaimable ratio.
- OpenCode cursor state, which is separate from the YO!stats transaction.

Sidecars are not casually interchangeable with database facts. The vacuum
baseline is trusted only when its marker matches `schema_meta.last_vacuumed_at`.
The OpenCode cursor has an explicit pending state and is fail-closed after an
interrupted prepare.

## SQLite Open and Write Policy

The writer opens with `timeout=5s`, autocommit mode, WAL, `synchronous=NORMAL`,
foreign keys, a 5-second busy timeout, a bounded WAL autocheckpoint, and a WAL
size limit. The writer initializes or validates the schema before changing
journal metadata. A clean takeover truncates old WAL allocation before worker
readers exist.

Readers use a read-only URI and `PRAGMA query_only = ON`. The materializer
reader is separate from the writer connection because Python's sqlite3
connections are thread-owned. WAL allows a long read snapshot to coexist with
the sole writer without turning the read into writer latency.

Every append path enters `Store.append_batch`. It validates and normalizes
the entire typed batch, then applies the records in one transaction. The
transaction can include:

- observations;
- usage atoms;
- usage tombstones;
- coverage epochs;
- unavailable spans;
- retention deletes and ring invalidation bookkeeping.

`source_generation` advances only when durable source facts or coverage
meaningfully change. Duplicate observations, duplicate atoms, identical
coverage re-offers, and already-retired tombstones do not advance it.

### Idempotency and conflicts

- Observation identity is `(family, source_id, event_id)`. Repeating an
  identical row is a duplicate. Reusing the identity with different content
  is a hard conflict.
- Usage identity is the five-part primary key. Exact replays are duplicates.
  A narrow unknown-model-to-known-model repair is allowed. Bounded metadata
  changes may be accepted without replacing the original fact. Other changes
  produce `UsageAtomIdentityConflict`.
- Coverage is mutable by epoch identity. The service updates the end and
  owner generation only when valid. Conflicting cadence, time reversal,
  ownership, or overlap with unavailable spans is rejected.
- Tombstones delete only when the stored quantity, provider, model, thread,
  execution source, and observed time match the tombstone proof. The deleted
  time is returned so the exact ring cells can be invalidated.
- Unavailable spans are normalized to non-overlapping intervals. An absent
  measurement is never fabricated as zero.

### Retention, prune, and vacuum

Facts are retained for two days, independently of display range, with an
invariant that retention covers the longest GUI window. Pruning refuses to
run if that invariant is false. Observation, usage, coverage, and unavailable
rows are deleted or clipped in bounded policy-specific ways; retired ring
invalidations are drained in batches.

Vacuum is maintenance, not ingest. It is hourly with jitter, deferred while
the service is busy, and forced after a maximum deferral. It runs only under
the writer/worker lock, updates the completion marker after successful
`VACUUM`, truncates the WAL, and records a sidecar reclaimable baseline after
the final physical state is known.

## Fact Model and Materialization

`families.py` defines the current families:

- `cpu`: one-second active/idle cadence, average fold, system/process and
  per-binary CPU series.
- `agent_status`: 10-second watched or 60-second idle status snapshots.
- `gpu`: 10/60-second gauge samples for utilization and memory.
- `service_load`: one-second process CPU/RSS samples and min/average/max
  materialization.
- `system_memory`: 60-second memory and optional macOS pressure/process
  memory details.
- `agent_tokens`: event-derived token rate; scan cadence is not itself a
  telemetry gap.
- `cost`: usage-derived priced/unpriced token and micro-USD series.
- `browser`: event-derived latency, throughput, queue, upload, page, and
  interaction metrics.

Each family declares payload fields and types, fold kind, series names,
cadence, and whether no-data coverage is meaningful. Unknown fields and
invalid values are rejected before persistence.

The materializer reads source facts and constructs immutable generations. It
projects observations into samples, usage atoms into token and cost samples,
and coverage/unavailable spans into explicit `NoData` intervals. It supports
multiple exact range/resolution slices, public data, and bounded private
per-browser overlays. A projection cache retains only reusable projections
for the next generation and is bounded by observation count.

Token and cost detail are not inferred from chart totals. Cost output keeps
provider/model/dimension/agent attribution, priced and unpriced coverage,
pricing evidence, omitted-detail counts, and exact integer micro-USD/token
totals within JSON-safe limits.

The public cache and delta history are demand-aware. Watched views are built
at live cadence; undemanded views are refreshed less often; private browser
views age out. A request records demand so the first snapshot can be prepared.
Cache generation and source generation are distinct. A cache may be behind
the source while a build is pending, but it must not claim a newer durable
source generation than the facts it includes.

## Host CPU, Memory, and GPU Collection

The host sampler is owned by statsd, not by every HTTP request or every web
process thread.

### CPU and process census

`CpuSampler` keeps baselines for one web PID and the host:

- Linux aggregate CPU comes from `/proc/stat`.
- macOS aggregate CPU comes from Mach `host_processor_info`.
- Process census supplies PID identity, normalized binary, CPU seconds, and
  RSS. The current web PID is selected for the process percentage.
- Grouped CPU and RSS are aggregated by normalized binary, but a grouped CPU
  value is published only when the process membership set is stable between
  samples and every delta is non-negative.
- System and process percentages are deltas between readings. The first
  sample, a non-advancing clock window, a process restart, or a counter reset
  yields `None`, not an invented `0.0`.
- RSS is absolute and may be valid on the first sample.
- Values are rounded/clamped and then placed in the `cpu` or
  `system_memory` family payload.

The host CPU cadence is one second. A pushed web-process sample is considered
stale after three cadences. When unwatched, the sampler backs off to ten
seconds; after the stale window, readers publish absence rather than freezing
the last sample.

The stats process pushes the web process sample through a collector-context
control socket. The receiver checks that the sample PID is its own. The
context handshake includes owner generation and target process identity;
re-discovering a mutable global owner file is intentionally avoided.

### GPU

On Linux, Python invokes `gpu-info` with a bounded timeout and asks for GPU
index, name, utilization, used memory, and total memory. Invalid rows or a
nonzero command result produce no GPU sample. Values are clamped and emitted
as `gpu:<index>` sources.

On macOS, Python invokes `ioreg` for `IOAccelerator` and parses
`PerformanceStatistics`. Utilization and memory fields are optional and are
not fabricated when absent. Hardware labels are separately obtained from
bounded `system_profiler` JSON.

GPU collection is normally ten seconds while watched and sixty seconds while
idle, with unavailable coverage when a native query cannot produce a valid
sample.

### Go implication

The Go port needs one `HostSampler` owner with persistent baselines and
explicit sample validity. Do not create one goroutine per metric or compute
CPU percentages from instantaneous process values. Use bounded command
execution for `gpu-info`, `ioreg`, and `system_profiler`; preserve absence,
staleness, topology, and process-membership semantics.

## Claude and Codex Transcript Collection

Claude and Codex are JSONL transcript families on the filesystem. They are
not read as a complete file on every poll.

### Incremental scanner

`StatsCurrentTranscriptUsageScanner` maintains bounded per-file state:

- device/inode and mtime identity;
- byte offset and observed size;
- prefix digest and parsed tail for truncation/rewrites;
- provider parser state;
- last-seen scan and family attribution;
- a bounded receipt/checkpoint for commit or rollback.

Each scan has byte and record budgets. It reads complete appended records only,
stops before an incomplete trailing JSONL line, rotates live/historical source
priority, and reports backlog/budget exhaustion. A follow-up scan is scheduled
quickly when the budget is exhausted. File reset, replacement, shrink, or
prefix mismatch causes a bounded reset/rebuild rather than silently attaching
new records to old state.

The scanner does not advance durable parser state until the resulting facts
are durably appended. Its receipt can commit or roll back. This is necessary
because transcript files and YO!stats SQLite cannot share a transaction.

### Claude

Claude records of type `assistant` provide a timestamp, message ID, model, and
usage. Usage is normalized into:

- ordinary input;
- cache read input;
- cache creation write-5m and write-1h when the provider supplies the split;
- output.

If nested cache-creation duration fields exist, the aggregate cache creation
field is not added again. Repeated assistant records for one message are
deltaed per message ID, including whole-object reset handling. Occurrence
numbers make revised records distinct event IDs. Provider/model evidence,
effort, pricing profile, service tier, endpoint, and thread attribution are
retained. Missing components remain missing and keep `telemetry_complete`
false; they are not turned into zero.

### Codex

Codex records carry cumulative `total_token_usage` or per-event
`last_token_usage`. The parser tracks current model/effort/profile from
`session_meta`, `turn_context`, and thread-settings records. Cumulative
components are deltaed atomically across input, cached input, and output.
Cached input is a subset of input, so the ordinary input atom is
`input - cached_input`; the cached portion becomes the `read` atom.

Codex forked/subagent transcripts are special. A child transcript can contain
copied parent history. The parser suppresses inherited usage after fork
metadata until the explicit inter-agent handoff marker, then resumes child
usage. Root thread, agent thread, parent thread, and depth are retained for
attribution. Legacy replay can emit tombstones for previously attributed fork
history.

### Shared transcript rules

Both providers produce the same internal `TranscriptUsageAtom` shape and then
pass through `usage_atom_from_source`. The canonical atom includes event ID,
direction, modality, cache role, unit, quantity, provider, model, agent ID,
telemetry completeness, execution source, thread ID, pricing profile, and
service tier. Only positive finite reported quantities are persisted.

The source file is not the durable deduplication authority. Statsd's usage
atom primary key is. This permits safe replay after a crash or cursor
rollback.

## OpenCode Is a Separate Data Source

OpenCode is not a JSONL transcript equivalent to Claude or Codex. Its durable
transcript and token information lives in OpenCode's own SQLite database,
normally `~/.local/share/opencode/opencode.db`. YO!stats opens it read-only and
must never write or migrate that database.

### OpenCode schema read contract

The reader validates required columns using `PRAGMA table_info` before reading:

- `session`: `id`, `directory`, `agent`, `model`, `time_created`,
  `time_updated`, `cost`, plus cumulative token counters when present;
- `message`: `id`, `session_id`, timestamps, and JSON `data`;
- `part`: `id`, `message_id`, `session_id`, timestamps, and JSON `data`;
- optional `session_input` for recent queued/steered activity state.

Connections use a read-only URI, one-second timeout, autocommit, and
`PRAGMA query_only = ON`. Locked, busy, malformed, missing-column, and
missing-row cases become typed unavailable/schema-mismatch results. No
credential-bearing tables are opened.

### Session identity

An exact process-provided session ID is authoritative. When it is absent,
OpenCode selection requires a directory and bounded time window. The reader
accepts exactly one canonical-directory candidate. Multiple eligible sessions
are ambiguous and unavailable; it never silently chooses the newest row.

The process inventory attempts, in order:

1. Session ID in `--session`/`-s` command arguments.
2. A bounded `OC | ...` terminal title match against recent session titles and
   directory.
3. A unique recent directory/time candidate.

Unresolved or ambiguous process identity becomes an unavailable coverage span,
not an attribution guess.

### Token extraction

Usage reads select only JSON-valid `part` rows whose JSON type is
`step-finish`. They are ordered by creation time, bounded, and associated with
their `message` row. Each part must belong to an assistant message and contain
a valid token snapshot. Provider/model metadata is taken from the message or
session. The reader checks that cumulative session counters match the sum of
the available history where a full anchor is possible.

Each step produces dimensions:

- input;
- cache read;
- cache write;
- output;
- reasoning.

YO!stats currently materializes the supported atom dimensions input,
cache-read, and output. Unsupported dimensions are retained as evidence and
make telemetry incomplete rather than being silently discarded as zero.

The reader fingerprints each part's bounded source fields, dimensions,
provider, and model. A rewritten source part is detected through the revision
rather than being mistaken for the old value.

### OpenCode cursor sidecar

OpenCode reports cumulative counters, so `agent_tokens.py` converts them into
durable deltas. `OpenCodeCursorStore` persists the last committed cumulative
value per session/dimension, epochs, sequence numbers, presence, source
revisions, and the OpenCode database identity.

The cursor sidecar uses a prepare/commit/rollback boundary:

1. Read and validate the prior cursor under a file lock.
2. Reset the cursor if the OpenCode database inode changes.
3. Read OpenCode and calculate proposed deltas.
4. Prepare pending cursor state, but do not make it committed.
5. Append the corresponding usage atoms to YO!stats.
6. Commit the cursor only after the stats append succeeds.
7. Roll back pending state on append failure.

The two stores cannot share a transaction. After a crash with pending cursor
state, Python discards the pending state and allows replay; YO!stats atom
identity prevents double counting. A malformed or raced cursor makes the
OpenCode source unavailable rather than guessing.

### OpenCode activity and tool data

The same read-only database supports bounded activity state and tool-history
reads. Recent `session_input` rows identify queued or steered input; recent
message/part rows identify working versus idle state. Completed bounded edit
tools (`apply_patch`, `bash`, `edit`, `write`) can be read for attribution,
with patch/command/workdir/path fields capped. Cache invalidation uses both
the database and WAL inode/mtime/size identity.

This information is related to YO!stats and agent UI behavior but is not the
same as billable usage. The Go design should keep OpenCode token ingestion,
activity state, and tool-history reads as separate projections over one
read-only adapter.

## Combined Agent Token Append

`AgentTokenCollector` is the shared owner for all agent usage collection:

1. Discover visible tmux agents and machine-wide OpenCode processes.
2. Build stable physical-window keys for Claude/Codex and stable process keys
   for OpenCode.
3. Reset/fence OpenCode cursors to the current OpenCode database identity.
4. Scan bounded Claude/Codex transcript increments.
5. Read bounded OpenCode `step-finish` increments.
6. Reject ambiguous identity, cursor failure, schema failure, or database
   failure as unavailable spans.
7. Normalize all successful records into usage atoms.
8. Prepare OpenCode cursor and transcript scan receipts.
9. Append atoms, tombstones, coverage, and unavailable spans in one YO!stats
   batch.
10. Commit both parser/cursor receipts only after the append succeeds.

The resulting batch is processed through the same storage transaction as
other facts. Token collection is every ten seconds while watched and every
sixty seconds while idle. A scan budget exhaustion shortens the next interval
to continue bounded backfill without allowing an unbounded scan to block
terminal or HTTP work.

## What Must Be Preserved in Go

The following are correctness requirements, not implementation details:

- One named durable writer for the current YO!stats database.
- No concurrent Python and Go writes to the same SQLite database.
- Schema/application ID/writer protocol/build fences.
- WAL and read-only reader behavior, with bounded busy/deadline handling.
- Typed original facts, coverage epochs, unavailable spans, usage atoms, and
  tombstones.
- Idempotency by event identity and hard identity conflicts.
- `source_generation` ordering: no cursor, watermark, ring cursor, or cache
  generation may claim durability before the corresponding fact is committed.
- Distinct durable source generation and derived cache/ring generation.
- Durable invalidation and replay after deletes, tombstones, prune, crash, and
  restart.
- Bounded materialization, projection cache, snapshot chunks, deltas, and
  private browser demand.
- Explicit missing data instead of zero-filled failed samples.
- CPU baseline validity and process-membership rules.
- Provider-specific Claude/Codex parser state and fork suppression.
- OpenCode read-only SQLite access, schema validation, exact identity
  resolution, cumulative counter deltas, source revisions, and cursor fence.
- Commit/rollback receipts across transcript files, OpenCode cursor JSON, and
  YO!stats SQLite.
- Pricing evidence and priced/unpriced attribution rather than only chart
  totals.
- Recovery and rollback behavior with the database, WAL, and companion state
  treated as one migration unit.

## Proposed Go Ownership Model

The Go process should contain one `StatsService` with explicit sub-owners:

```text
StatsService
  +-- StatsStoreWriter       one SQLite writer goroutine/connection
  +-- StatsReadSnapshot      read-only connection and bounded queries
  +-- HostSampler             CPU/memory/process/GPU baselines
  +-- TranscriptScanner       Claude/Codex file state and receipts
  +-- OpenCodeReader          read-only external SQLite adapter
  +-- OpenCodeCursorStore     locked sidecar prepare/commit/rollback
  +-- AgentTokenCollector     combines provider facts into one batch
  +-- Materializer            pure fact projection and bounded cache
  +-- RingPublisher           durable ring cursor/invalidation owner
  +-- MaintenanceScheduler    prune, vacuum, repair, and deadlines
  +-- StatsHTTPAdapter        existing route/SSE/snapshot contract
```

These are ownership boundaries, not necessarily separate OS processes. The
writer should receive typed commands over a bounded channel or direct calls
under one service lock. Collection can run in separate goroutines only if its
results are immutable, budgeted, and committed through the writer owner.
External commands and potentially wedging work need context deadlines and
may later use a supervised internal worker process, but a process boundary
must be justified by measurements.

Do not let the materializer write source facts. Do not let the OpenCode
adapter write OpenCode's database. Do not let transcript scanners mark their
offsets durable before the stats append. Do not let an HTTP request perform a
full history scan or materialization synchronously.

## Migration Sequence

1. Freeze language-neutral fixtures for current schema rows, provider atoms,
   coverage/unavailable spans, pricing, snapshots, rings, tombstones, and
   crash boundaries.
2. Implement a Go read-only decoder for copied Python databases and compare
   schema shape, normalized facts, and exact wire output.
3. Implement pure Go family validation and materialization before enabling a
   Go writer.
4. Implement host samplers and compare Linux/macOS samples with controlled
   fixtures, including first-sample absence and process restart behavior.
5. Implement Claude/Codex parsers against the existing JSONL fixture corpus;
   test truncation, partial lines, revisions, cumulative resets, and Codex
   fork history.
6. Implement OpenCode read-only access against copied databases and schema
   variants; test ambiguous identity, WAL changes, locks, incomplete parts,
   counter-anchor mismatch, and cursor crash windows.
7. Run Go collectors in shadow mode against independent copies. Compare
   normalized atoms, coverage, unavailable spans, materialized layers, cost
   detail, and ring publication decisions.
8. Add the Go writer only after Python is fenced out as writer and the Go
   process owns the same schema/build protocol.
9. Cut over one persistence owner at a time, retaining Python rollback only
   while the schema and cursor compatibility policy explicitly allows it.

The first Go milestone should therefore be a compatibility reader and pure
materializer, not a line-by-line rewrite of the Python service. The highest
risk is not SQL syntax; it is preserving the transaction boundaries and the
meaning of absence, identity, generation, coverage, and replay.

## Open Design Questions

## Post-Migration Extensions

The `/api/stats-capabilities` response must remain an exact JavaScript-compatible
object until the Go migration and cutover are complete. After the final
Python-to-Go migration, once the shared frontend contract is intentionally
versioned, additional backend-owned fields may be added through a new protocol
revision and corresponding browser contract tests. In particular, a future
capabilities revision may expose backend diagnostics such as the stats writer
build, collector health, ring publication state, or ownership/fence details.
Those fields must not be added opportunistically during migration: older and
newer clients need an explicit compatibility policy, and the current exact
field validator must continue to protect the cutover path.

- The current append buffer is disabled by default because serving buffered
  facts without advancing durable `source_generation` collides with ring
  freshness ordering. The Go design must choose a separate volatile serving
  key or another explicit overlay contract; it must not advance durable
  generation before commit.
- Whether Go uses SQLite through `database/sql`, `modernc.org/sqlite`, or a
  cgo-backed driver must be decided with WAL, JSON, `dbstat`, FTS/compile
  options, race, and deployment constraints measured.
- Whether stats stays in the main Go process goroutines or gets a supervised
  internal worker process for native samplers and transcript parsing should be
  based on measured memory/failure isolation. The durable writer ownership
  must remain singular either way.
- OpenCode's schema is external and evolving. The adapter needs a capability
  matrix and fail-closed compatibility behavior rather than a guessed schema
  migration.
- Exact snapshot bytes versus normalized semantic parity must be specified per
  route. The current Python tests require exactness for some encodings and
  semantic equality for others.

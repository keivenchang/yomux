# Persisted ring-buffer aggregates

## Staleness rule

Every persisted slot carries the exact epoch-aligned `bucket_start` that produced its payload. The slot address is `((bucket_start // resolution_seconds) % slot_count)`, but a read accepts that slot only when the stored `bucket_start` equals the exact bucket start requested for that position in the window. Modulo addressing finds a candidate; the timestamp equality decides whether the candidate is data.

- A never-written slot has `bucket_start IS NULL` and is absent.
- A slot written one or more laps ago has a different `bucket_start` and is absent.
- A query that straddles the write head validates every expected timestamp separately, then returns accepted rows in timestamp order.
- A quiet bucket is not absent. The writer persists its exact `bucket_start` with an empty series payload and source count zero, so the chart renders zero rather than a gap.
- A partially accumulated bucket is real data with `complete = 0`. Readers may show it as open, but they must not present it as final.

This rule is not relaxed by eventual consistency. A result may be behind the latest accepted fact, but it may never substitute an older lap's value for the requested time.

## Four rings, not one ring per view

There is one ring for each concrete resolution. A range reads the newest `range_seconds / resolution_seconds` positions from that resolution's ring. The same 300-second bucket is stored once and can serve every view that offers 300-second resolution.

| resolution | slots | retained history | ranges served |
| ---: | ---: | ---: | --- |
| 1 s | 300 | 5 minutes | 300 s |
| 10 s | 180 | 30 minutes | 300 s, 900 s, 1800 s |
| 60 s | 480 | 8 hours | 900 s, 1800 s, 3600 s, 7200 s, 14400 s, 28800 s |
| 300 s | 288 | 24 hours | 3600 s, 7200 s, 14400 s, 28800 s, 57600 s, 86400 s |

The store has 1,248 bucket slots. This is 43.4 percent fewer than the corrected 2,205-slot per-view design, and one incoming point dirties at most four bucket identities instead of sixteen.

### Availability derives from capacity and minimum chart density

Ring capacity replaces `MAX_BUCKETS` and the special `(3600, 10)` exclusion as the maximum-history source of truth, but capacity alone does not reproduce the current matrix:

| pair admitted by capacity alone | buckets | why current policy rejects it |
| --- | ---: | --- |
| 300 s / 60 s | 5 | fewer than `MIN_BUCKETS = 12` |
| 300 s / 300 s | 1 | fewer than `MIN_BUCKETS = 12` |
| 900 s / 300 s | 3 | fewer than `MIN_BUCKETS = 12` |
| 1800 s / 300 s | 6 | fewer than `MIN_BUCKETS = 12` |

The exact derived rule is therefore:

```python
MIN_BUCKETS <= range_seconds / resolution_seconds <= RING_CAPACITIES[resolution_seconds]
```

`RING_CAPACITIES = {1: 300, 10: 180, 60: 480, 300: 288}` should live beside `explicit_resolutions()` in `resolution.py`, and storage initialization should consume that mapping rather than copy it. That edit must be sequenced after the current `resolution.py` change; this design and its red contract do not edit that file.

The five-minute limit on 1-second history is deliberate. Adding a longer 1-second view requires more slots and therefore a schema migration, not a runtime setting change.

## Schema

This is schema v8 in `stats-v8.sqlite3`. Schema initialization creates all aggregate rows in one transaction before it creates insert/delete rejection triggers.

```sql
CREATE TABLE aggregate_publication (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    ring_generation INTEGER NOT NULL CHECK (ring_generation >= 0),
    source_generation INTEGER NOT NULL CHECK (source_generation >= 0),
    published_at REAL NOT NULL CHECK (published_at >= 0)
);

CREATE TABLE aggregate_rings (
    resolution_seconds INTEGER PRIMARY KEY,
    slot_count INTEGER NOT NULL CHECK (slot_count > 0),
    newest_bucket_start INTEGER,
    CHECK (resolution_seconds > 0),
    CHECK (newest_bucket_start IS NULL OR newest_bucket_start >= 0),
    CHECK (newest_bucket_start IS NULL OR newest_bucket_start % resolution_seconds = 0)
) WITHOUT ROWID;

CREATE TABLE aggregate_ring_slots (
    resolution_seconds INTEGER NOT NULL,
    slot_index INTEGER NOT NULL CHECK (slot_index >= 0),
    bucket_start INTEGER,
    bucket_json TEXT,
    complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
    source_generation INTEGER NOT NULL DEFAULT 0 CHECK (source_generation >= 0),
    ring_generation INTEGER NOT NULL DEFAULT 0 CHECK (ring_generation >= 0),
    published_at REAL NOT NULL DEFAULT 0 CHECK (published_at >= 0),
    PRIMARY KEY (resolution_seconds, slot_index),
    FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds),
    CHECK (bucket_start IS NULL OR bucket_start >= 0),
    CHECK (bucket_start IS NULL OR bucket_start % resolution_seconds = 0),
    CHECK ((bucket_start IS NULL AND bucket_json IS NULL AND complete = 0 AND source_generation = 0 AND ring_generation = 0 AND published_at = 0) OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL))
) WITHOUT ROWID;
```

Initialization inserts one `aggregate_publication` row, four `aggregate_rings` rows, and all 1,248 `aggregate_ring_slots` rows. It then creates `BEFORE INSERT` and `BEFORE DELETE` triggers on all three tables that abort every later insert or delete. Schema validation checks the exact four capacities and the exact contiguous slot indexes. A capacity change requires a new schema instead of disabling the triggers.

Steady state uses `UPDATE` only. A publication updates the affected slots, the affected ring heads, and the singleton publication row in one SQLite transaction. Readers pin one SQLite snapshot by reading `aggregate_publication` first, so they cannot observe half of a committed flush.

`bucket_json` contains the canonical shared bucket fold plus the bounded private-browser overlays needed by the existing request contract. Start, duration, completion, and generation remain columns so correctness does not depend on parsing JSON. `unavailable_spans` remains the durable source for no-data annotations; a range read clips those persisted spans after it selects ring rows.

The aggregate row count is fixed, but that is not the same as a byte-for-byte fixed SQLite file. Variable-length payloads, WAL checkpoints, and SQLite page high-water marks can change file bytes. The bucket encoder must keep the existing bounded model, agent, evidence, private-client, and identity limits and add a measured `MAX_RING_BUCKET_BYTES` guard before implementation. The ring store then has a finite byte bound, but the claim proved by the gate is the stronger structural claim the brief requested: no aggregate `INSERT` or `DELETE` and exactly 1,248 slot rows after sustained ingest.

The whole stats database is also not fixed-size while raw tables remain. `observations`, `usage_atoms`, `coverage_epochs`, and `unavailable_spans` still use the existing 24-hour retention path, including `INSERT` and time-based `DELETE`. The ring redesign stops aggregate history growth; making the entire database fixed would require a separate raw-retention redesign.

## Ten-second write-behind

The flush interval is 10 seconds. Sixty seconds would leave the 10-second views six buckets behind, while ten seconds bounds normal durable lag to one 10-second bucket and coalesces any ingest rate into one publication transaction per interval.

Ingest keeps the current durable fact path: the sole stats daemon commits accepted raw facts and advances `source_generation` immediately. After that commit, it records the accepted fact identities and affected `(resolution_seconds, bucket_start)` cells in a bounded in-memory write coalescer. The coalescer retains enough canonical per-source fold state to rebuild its dirty cells and discards closed state after publication; after restart, the first new fact for an already-open cell may lazily hydrate that one cell from raw SQLite instead of scanning every ring. The coalescer is a write buffer, not the restart truth.

At each monotonic 10-second tick the worker swaps the dirty set under its lock, pins one raw SQLite generation, folds only the dirty cells, and publishes all resulting slot replacements in one transaction. Facts accepted after the pinned generation stay in the next dirty set. A failed fold or transaction keeps the dirty cells queued and records a typed failure; once failures occur, there is no false 10-second freshness promise, and status must expose the oldest dirty age.

Time advancement is itself dirty work. While the daemon is alive, each tick writes every newly elapsed 1-second cell, starts the new 10-second cell, and creates or finalizes 60-second and 300-second cells as their boundaries move. A cell with no facts is published as an explicit zero. The worker does not update unchanged closed cells.

The 10-second batch normally touches ten new 1-second slots and at most one current slot in each other ring. Late accepted facts can dirty older slots that are still inside that ring's retained horizon. Facts older than the horizon remain in raw retention but cannot overwrite a modulo slot that now belongs to a newer bucket.

A clean shutdown attempts one final flush. A process crash can lose the in-memory coalescer and leave the rings behind raw facts by up to one healthy flush interval. This loss is accepted for stats. Restart does not scan 24 hours of raw facts to repair it; the persisted rings are served immediately, and normal later ingest replaces addressable cells. Raw facts remain available for audit and for ordinary late arrivals, but they are not a startup warm source.

## Read path and live overlay

A range request begins with SQLite, selects the one resolution ring, computes every expected bucket start, and applies the exact-timestamp rule. It reads at most 480 rows. It then clips persisted `unavailable_spans` and constructs the existing snapshot wire response. No raw observation or usage aggregation runs on the request path.

The existing window-level cost report is a bounded reduction of the selected buckets' persisted cost detail. This is response assembly over at most 480 already-folded rows, not a fold of raw facts and not coarse-from-fine derivation. If even that reduction is disallowed, the current protocol would require sixteen additional fixed per-view summary rows; the four-ring correction otherwise cannot produce a range-specific cost report.

The in-memory coalescer may overlay its dirty cells after the SQLite read. The overlay uses the same `(resolution_seconds, bucket_start)` identity and the same timestamp equality rule as disk. It replaces only rows whose exact bucket starts match the request and never fills an absent historical cell with another lap's value.

This overlay is taken for the live five-minute view. A 10-second flush can contain up to ten 1-second cells, not just the wall-clock-open cell, so the overlay includes every dirty 1-second cell in the requested window. The same mechanism may overlay dirty cells at coarser resolutions because those aggregates already exist in the coalescer. In a healthy process, accepted facts can therefore appear before the next flush; the durable ring remains at most 10 seconds plus flush duration behind.

Allowing this buffer does not weaken restart behavior. After restart the buffer is empty, the disk rings are returned immediately, and there is no `PublishedCache`, `cache_ready_event`, or full materializer build on the correctness path. A small lazy response-encoding cache may be added later if measurement requires it, but a miss must always fall through to the SQLite ring and must never return `pending` while persisted rows are readable.

## Cold start, crash, and downtime

On a brand-new database every slot is never-written and reads as absent. As the live daemon advances, it writes explicit zero cells even with no input, so an observed quiet interval is zero rather than a gap.

After a crash, restart serves the last committed ring generation immediately. The visible result can be behind raw facts by up to the last flush interval, and the prior open bucket can remain partial. There is no startup rebuild and no cache-ready wait.

Downtime is different from quiet time. If the daemon is off for two hours, it does not synthesize two hours of zeros on restart. Exact timestamp checks reject stale slots, and the chart shows gaps for the missing interval. New host metrics fill forward. A transcript scanner may later append historical facts; those facts update old cells only when their bucket starts are still within the relevant ring horizon. This preserves the five-minute 1-second retention limit and avoids a bulk warm pass.

## Ownership

The stats daemon singleton remains the only process with a mutable `Store` handle. The elected background owner is the only web process that runs collectors and appends facts through its service lease; followers neither collect nor publish ring rows. Followers and request handlers read through the daemon or `Store.open_reader()`, and `publish_ring_buckets()` must reject a read-only store.

Owner demotion stops the scheduler before releasing its lease, as it does today. The daemon may still flush facts it accepted before demotion because it is the sole database writer. The host-partitioned database path, schema-specific socket identity, writer fence, and local-service singleton remain the protection against a second stats daemon; owner election alone is not the SQLite writer lock.

## Migration and coexistence

The change requires schema v8 because it adds fixed tables, fixed seed rows, triggers, and persisted aggregate payload semantics. The database and socket identities become schema-specific v8 identities. A storage-only implementation can keep the current writer protocol number if the RPC wire is unchanged; any request or response shape change must also bump the writer protocol.

An existing v7 database is migration input, never an in-place target. The migrator takes a consistent SQLite backup of v7 into a shadow `stats-v8.sqlite3`, copies the retained raw fact tables, seeds all 1,248 slots, performs the one-time initial ring fold in the shadow, validates exact row counts and staleness metadata, and atomically activates v8 only after validation. This one-time upgrade materialization is distinct from restart behavior: every later v8 restart reads persisted rings without rebuilding them.

The old versioned database, WAL sidecars, writer fence, and socket are not moved, deleted, or rewritten. The process on port 7770 continues using its old schema and socket throughout shadow construction and after v8 activation. A v8 migration failure leaves the old database untouched and does not publish a partial v8 file.

A change to ring capacity, resolution membership, slot payload format, or timestamp semantics requires schema v9 and the same shadow-build process. Rows are never inserted, deleted, or resized in an active v8 aggregate store.

## Latency trade-off

The current G4b browser measurements are about 88–102 ms, and the enforced range-shift ceiling is 350 ms. The new read performs one local SQLite snapshot, reads at most 480 fixed rows, parses bounded bucket payloads, reduces the window cost report, and optionally overlays dirty cells. That should fit the existing 247 ms or greater measured headroom, but there is no implementation measurement yet, so this document does not claim the gate will pass.

Implementation must rerun G4b with the realistic 8,929-row fixture and report median, p95, and maximum for every transition. It must also measure cold post-restart snapshots with the overlay empty. If SQLite response assembly breaches the contract, first measure statement time, payload decode, cost-report reduction, and JSON encoding separately; do not widen the 350 ms ceiling.

## Red contracts

`tests/test_gate_stats_ring_buffer.py` pins the following strict-xfail contracts before implementation:

- Four capacities plus `MIN_BUCKETS` derive the current sixteen view pairs.
- The slot table contains exactly 1,248 rows at creation and after one simulated hour of one-point-per-second ingest.
- Steady publication uses one transaction and only `UPDATE` against aggregate tables.
- A lap-stale slot is absent from a current window.
- A persisted quiet bucket is zero and distinct from a never-written gap.
- A window crossing the write head validates and orders every expected timestamp.
- A restarted read-only store serves persisted buckets while the materializer builder is forbidden.
- A read-only follower cannot publish ring rows.
- Schema v8 creation leaves an existing v7 database byte-for-byte untouched.

These tests define the storage seam as `resolution.RING_CAPACITIES`, `storage.RingBucketWrite`, `Store.publish_ring_buckets(buckets=..., source_generation=..., published_at=...)`, and `Store.read_ring_window(range_seconds=..., resolution_seconds=..., window_end=...)`. `read_ring_window()` returns rows in timestamp order plus exact `missing_bucket_starts`; absence and zero are not inferred from payload truthiness.

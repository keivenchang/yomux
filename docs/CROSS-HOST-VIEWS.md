# Cross-Host Views

## Decision

YOLOmux should not build a general cross-host view yet. The user has not asked for one, two browser tabs already preserve a clear machine boundary, and combining hosts creates authentication, availability, clock, schema, retention, and conflict behavior that the product would have to expose honestly. Host independence is a valid finished product, not a temporary limitation.

If repeated user demand appears, start with a narrow, opt-in YO!stats view. Do not globalize chat, login throttling, attention acknowledgements, watch interest, process state, errors, or notifications. Those families have different ownership and privacy semantics, and one cross-host transport must not silently turn them into shared state.

For an approved feature, build an authenticated per-host HTTP/RPC query first for live data, then add immutable snapshots only if offline or historical continuity is needed. The endpoint answers the immediate question with source-owned data and no copied database; snapshots add value only when a source is unavailable or a fixed historical range must remain readable.

## Non-negotiable boundary

A reader never opens, copies pages from, repairs, checkpoints, or attaches another host's live SQLite database. A live database may have `-wal` and `-shm` state whose correctness depends on same-host shared memory. Seeing its pathname through NFS does not make it a readable foreign database.

The source host is the only database reader used to create a snapshot. It uses SQLite's backup API against its local live database, closes the backup, validates the closed copy, and publishes immutable bytes. A remote reader opens only that published payload in read-only immutable mode.

## Proposed implementation boundary

The contracts reserve `yolomux_lib.cross_host_views` for this feature. It owns snapshot manifest validation, immutable publication, catalog selection, typed rejection reasons, and read-only payload opening. Dataset owners provide source generation, schema, coverage, and a local database path; they do not implement their own cross-host format.

The first dataset would be `stats`. Adding another dataset requires an explicit product decision, a schema adapter, a publication cadence, a retention budget, and UI states for unavailable and rejected data. A generic "publish any SQLite file" API is out of scope.

## Snapshot identity and format

One immutable generation is addressed by `(dataset, stable_host_id, boot_id, source_generation, schema_version)`. The stable host ID is the durable key. `hostname` is display metadata only. `boot_id` prevents a generation counter reset after reboot from colliding with a prior process lifetime.

The publication layout is versioned and contains no live database path:

```text
<publication-root>/v1/<dataset>/<stable-host-id>/<boot-id>/<source-generation>/
  manifest.json
  snapshot.sqlite3
```

`manifest.json` uses this logical shape:

```json
{
  "format_version": 1,
  "dataset": "stats",
  "source": {
    "stable_host_id": "machine-id",
    "hostname": "display-name",
    "boot_id": "boot-id"
  },
  "source_generation": 42,
  "schema": {
    "version": 5
  },
  "coverage": {
    "start": 1785542400,
    "end": 1785628800
  },
  "created_at": 1785628810,
  "payload": {
    "path": "snapshot.sqlite3",
    "bytes": 123456,
    "sha256": "hex-digest"
  }
}
```

`coverage` is the half-open source-time interval `[start, end)`. A dataset with internal unavailable spans keeps those spans in its own schema; the manifest range never claims that every point exists. `created_at` is diagnostic display data and never decides generation order, conflict ownership, or foreign-host liveness.

The manifest is strict: unknown or missing identity, schema, coverage, and payload fields reject the generation. `payload.path` is one relative basename contained by the generation directory; absolute paths, `..`, symlinks, and alternate SQLite sidecars are rejected. The payload byte count and SHA-256 digest detect incomplete publication and accidental mutation. Filesystem permissions and an explicit source-host allowlist are sufficient only when every publishing machine is trusted as the same user; deployments with mutually untrusted writers also need a registered host signing key because a digest alone does not authenticate who wrote the manifest.

## Publication lifecycle

1. A dataset owner requests publication only after it has committed and exposed a complete durable source generation. Request and browser threads never create snapshots.
2. The source host uses SQLite's backup API to write a temporary database on a local filesystem. It closes both backup handles before validation.
3. The publisher checks SQLite application/schema identity, `PRAGMA integrity_check`, the declared coverage, and the expected source generation. Validation failure records a typed local diagnostic and publishes nothing.
4. The publisher verifies that the closed copy has no required `-wal`, `-shm`, journal, or attached-database dependency, computes its byte length and digest, and creates the strict manifest.
5. The publisher copies the already-closed bytes to a uniquely named temporary payload in the publication directory without opening that NFS copy through SQLite, flushes it, verifies the copied digest, and atomically renames it to `snapshot.sqlite3` within the same directory.
6. The publisher writes and flushes a temporary manifest, then atomically renames `manifest.json` last. Readers discover manifests, never temporary names. Attribute-cache delay can postpone discovery but cannot expose a generation as valid before its payload exists and matches.
7. A second publication with the same logical generation and digest is a duplicate. The same logical generation with a different digest is a conflict and neither replacement is accepted as newer.

For stats, publication is triggered by a new completed source generation and coalesced to at most one snapshot every five minutes. An idle source emits nothing. This is a historical/offline cadence, not a live-update promise; live views use authenticated RPC.

## Reader lifecycle

The reader accepts an allowlisted publication root, dataset, and source host. It does not accept a live database pathname. It resolves and contains the manifest and payload below the configured root, rejects symlinks and temporary files, validates the complete manifest before opening SQLite, verifies byte length and digest, then opens the payload with `mode=ro&immutable=1` and `PRAGMA query_only=ON`.

Schema selection happens before the SQLite opener is called. Each supported `(dataset, schema_version)` has an explicit read adapter. A reader never mutates an old snapshot into a new schema and never attempts a partial query against an unknown schema.

The reader keeps the last accepted generation per source and dataset. A newly observed corrupt, conflicting, incomplete, or unsupported generation does not erase that last-known-good snapshot. Selection uses boot/generation identity and an authenticated current-boot observation when available; filesystem mtime and `created_at` do not establish which boot is current.

## Required semantics and UI states

| Condition | Reader behavior | Required UI rendering |
|---|---|---|
| Source online and accepted live RPC result | Render the source-owned response with stable host identity; do not consult a foreign database. | Show the display hostname and a normal live indicator. |
| Source offline with an accepted snapshot covering the requested historical range | Render the immutable snapshot. Offline status does not invalidate already published history. | Show `Offline - snapshot through <coverage end>` beside the source hostname. |
| Source offline with no accepted snapshot | Return `source_unavailable`; do not produce an empty success response. | Show `Unavailable - source host offline and no snapshot is available`. |
| Snapshot does not cover the requested historical range | Return `coverage_missing` and preserve any covered data as a separately labelled partial source result. | Show the missing interval; never present the host as zero activity. |
| Snapshot is behind a live/current request beyond the dataset freshness budget | Keep the last accepted data with `snapshot_stale`; do not silently label it current. | Show `Stale - through <coverage end>` and the source hostname. |
| Source clock relationship is unknown | Do not infer freshness or liveness from source wall time and do not collapse cross-host time buckets. | Show `Source time unverified`; keep per-host series separate. |
| Authenticated clock observation differs by more than 120 seconds | Return `clock_skew` for aggregation while retaining per-host data. | Show the measured offset and disable combined time-bucket totals. |
| Manifest schema is unsupported | Reject before opening the payload with `schema_mismatch`; keep the previous compatible generation if one exists. | Show `Update required` for a newer schema or `Unsupported snapshot schema` for an older unimplemented adapter. |
| Byte length, digest, SQLite identity, or integrity check fails | Reject with `integrity_mismatch`; do not repair or fall back to a live path. | Show `Snapshot rejected - integrity check failed`, with bounded admin diagnostics. |
| One logical generation has two different digests | Quarantine both candidates as `generation_conflict` and keep the prior accepted generation. | Show `Snapshot conflict`; require operator inspection rather than choosing by mtime. |
| Source identity is not allowlisted or authenticated | Reject with `source_untrusted`. | Show `Source not trusted`; expose no payload data. |

A fixed historical query is not stale merely because the snapshot is old if the accepted coverage contains the entire requested range. Staleness applies to a current/live request whose expected end advances. When clock relationship is unknown, freshness is `unknown`, not confidently fresh or stale.

Cross-host conflicts never use last-writer-wins. Rows from different stable host IDs are separate contributions. If a future all-host stats total is approved, the server may sum accepted compatible contributions only after preserving source-host attribution and excluding clock-skewed buckets; the UI must let the user inspect each host's contribution.

## Retention

The publisher prunes only generations for its own stable host ID and dataset, and only after a newer manifest and payload have been accepted by the same validation path readers use. Foreign readers never delete publication files.

Keep the newest three validated full snapshots per `(dataset, stable_host_id, schema_version)`, the newest compatible snapshot even when it is older than the normal window, and the newest snapshot of an unsupported newer schema for upgrade diagnostics. Superseded snapshots become eligible after seven days. Conflicting or corrupt candidates are quarantined for seven days with bounded metadata, then may be removed by their source publisher. These defaults bound full-database copies while retaining two rollback generations and one current generation.

Retention never deletes the only accepted snapshot merely because its source is offline or its age exceeds the live freshness budget. Age changes the UI state; it does not convert known historical data into absence.

## Snapshot versus authenticated RPC

| Approach | What it solves | Cost and failure mode |
|---|---|---|
| Authenticated HTTP/RPC query | Live current data, source-owned schema interpretation, explicit online/offline status, no copied database. | Requires host discovery, authenticated transport, key rotation, allowlists, request bounds, backpressure, and network reachability. It cannot serve an offline source. |
| Immutable snapshot | Offline historical reads, fixed audit input, no live foreign WAL access, bounded shared-mount publication. | Requires backup work, duplicate disk space, manifest/version adapters, integrity validation, retention, conflict handling, and honest stale/clock UI. It is eventually consistent and should not imitate live data. |

If user demand justifies one implementation, build authenticated RPC first for a narrow stats endpoint because it provides visible value without copying entire databases or inventing offline freshness. Add snapshots second only when users ask to keep viewing a host after it disconnects or to preserve historical cross-host reports. Building snapshots first would incur publication, schema, retention, and UI cost before proving that a combined view is wanted.

## Build/no-build recommendation

Do not schedule a general cross-host view from this design alone. Keep the host-local work already completed, document that one browser tab represents one machine, and collect evidence that users actually need a combined view.

If that evidence appears, scope the first feature to an explicit list of stats hosts and a read-only all-host selector. Success means users choose the source hosts, see which are live, offline, stale, skewed, rejected, or unavailable, and can always recover the per-host contributions. It does not mean making YOLOmux state globally shared.

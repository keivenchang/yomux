# Filesystem indexing interactivity target

> **SHIPPED IN 0.7.3.** The breadth-first Quick Open lifecycle, frontier, hot-path cadence, safety refresh, and status projection described here are implemented in `yolomux_lib/search/bfs_index.py`, `file_index.py` (`INDEX_FORMAT_VERSION=5`), `search_indexer.py`, `filesystem/search.py`, and `app.py`. The focused, gate, and release evidence is retained in [`../DONE/2026-08/DONE.fs-interactivity.md`](../DONE/2026-08/DONE.fs-interactivity.md). Two implementation caveats remain recorded inline below: a jobd-executed mutation reaching `indexd` in a multi-server topology, and the read-path connect timeout. [`SEARCH_INDEXER.md`](SEARCH_INDEXER.md) describes the same single-writer SQLite architecture.

## User outcome

When a user marks a directory as indexed, YOLOmux makes its direct children searchable first. Startup must publish that first layer from every configured indexed root as soon as possible, then fill deeper layers breadth-first without letting a deep or unusually large subtree delay useful results from shallower directories. Previously published results remain searchable while refresh work runs, with honest freshness and coverage metadata.

## Terms

- **Layer 1** is the direct contents of a configured indexed root. Scanning it lists the root once, publishes its direct files, records its direct child directories, and queues those child directories as layer 2 work.
- **Layer N** is the direct contents of directories discovered in layer N-1. One work item scans exactly one directory and never recursively descends.
- **Frontier** is the bounded queue of discovered directories that have not yet been scanned for the active root generation.
- **Published depth** is the deepest completely published layer for a root. Results from partially scanned deeper layers may also be published, but status must not call that layer complete.
- **Hot path** is a file or directory with recent concrete change evidence, such as a watch event, a successful YOLOmux file mutation, an open-file save, or a visible Finder/Differ root.
- **Safety refresh** is the low-priority full reconciliation controlled by `file_explorer.index_refresh_seconds`, 1800 seconds by default. It catches changes not covered by stronger evidence; it is not the primary freshness mechanism.

## Required lifecycle

1. The elected background scheduler loads the configured indexed roots at startup and acquires one scheduler lease on the existing `indexd`; do not add another filesystem daemon or move recursive work into an HTTP process.
2. `indexd` opens each compatible persisted snapshot immediately. A valid snapshot is available for reads before any crawl starts, even when stale or only partially covered.
3. For every configured root, `indexd` enqueues a `startup-depth-1` item that lists only that root directory. This item has the highest indexing priority and is not delayed behind a safety refresh, deep frontier work, or a cached Quick Open lookup.
4. Each root's layer-1 transaction is published atomically as soon as its root listing finishes. The transaction updates direct files, direct child-directory metadata, deletions from the previous layer-1 snapshot, frontier entries for layer 2, generation, freshness, and coverage together.
5. While a root is configured, incomplete, dirty, hot, or awaiting its safety deadline, the scheduler lease keeps `indexd` alive or wakes it at the required deadline. A 60-second client-idle timeout must not silently disable configured-root maintenance.
6. On settings removal or scheduler demotion, generation fencing cancels unpublished work, releases the scheduler lease, and preserves the single-writer and existing unindex rules.

## Breadth-first frontier

The queue entry is one bounded typed record with at least `root`, `directory`, `depth`, `generation`, `reason`, `priority`, `enqueued_at`, and retry state. Queue identity is `(root, canonical directory, generation)`, so repeated demand coalesces instead of creating parallel crawls.

For one dequeued directory, the worker must:

1. Revalidate that the root is still configured, the generation is current, the canonical directory remains beneath the root, and the shared exclusion/symlink policy permits background traversal.
2. List only that directory with the shared descriptor-bound path policy and configured entry/file limits. Listing and index metadata consumers read the descriptor generation that passed authorization: children are opened relative to the pinned parent descriptor, and `SafePathHandle.descriptor_path()` returns only a per-descriptor magic path (`/proc/self/fd/N` or `/dev/fd/N`) or fails closed, so no consumer re-resolves an authorized name.
3. Publish file rows and directory metadata for that directory in one bounded transaction; publish removals or renames from the previous directory snapshot in the same generation.
4. Enqueue eligible child directories at `depth + 1` without opening them.
5. Check cancellation and yield to higher-priority work before taking another frontier item.

Within a root and priority, lower depth always runs before higher depth and FIFO order breaks ties. Across roots, use round-robin or another explicitly tested fair scheduler so one wide root cannot block every other root's layer 1 or layer 2. A worker may publish useful rows from a partially completed layer, but it advances `published_depth` only after every frontier item at that depth reaches a terminal state. Full-coverage completion means the active frontier is empty without truncation or unresolved errors.

Do not retain a DFS compatibility path for initial builds. Existing `_walk_root_with_metrics()` and dirty-subtree recursion in `yolomux_lib/search/file_index.py` must be replaced or routed through the same directory-at-a-time frontier for configured-root builds, safety refreshes, and subtree repair. Explicit direct navigation remains separate and may list the requested directory without waiting for background coverage.

## Scheduling priorities

Use one scheduler owner and one bounded queue with these precedence classes:

| Priority | Purpose | Completion expectation |
| --- | --- | --- |
| `startup-depth-1` | Direct contents of every configured root after startup or settings addition | First indexing work; publish each root independently |
| `hot-change` | Concrete file/directory changes and recently active paths | Debounced in seconds, not 30 minutes |
| `user-visible-demand` | A Quick Open scope or visible Finder/Differ path whose layer is not covered | Promote the existing frontier item; never launch a second crawl |
| `breadth-expansion` | Normal layer-by-layer frontier completion | Fair and bounded background progress |
| `full-safety-refresh` | Periodic reconciliation for missed events | Lowest priority; resumable and preemptible |

Cached SQLite lookup is not queue work. A cache hit must execute on the bounded read path and return immediately; it must not wait behind `jobd`'s interactive worker, `indexd` crawling, or a full refresh. The read is served from a read-only WAL snapshot that never waits on the writer, and `test_cache_hit_returns_within_budget_while_crawler_and_jobd_blocked` proves a cached exact match still returns within the read-path budget while the crawler and jobd work are blocked. Open detail: this read reuses the existing `_read_sqlite_index` 30-second connect timeout unchanged rather than a shorter read-path deadline; live reads observed under 2 seconds, but the tight bound is on the query path, not the raw connect. The full-tree response nests the measured coverage under `progressive_coverage` and adds a compact `snapshot_state` (`current` when `full_coverage`, `partial` when `published_depth > 0`, else `warming`) plus `refresh_pending` (true while the frontier is non-empty), so the UI can distinguish ready/current, ready/stale, partial/warming, and unavailable without hiding already-cached matches.

## Hot-path refresh policy

One owner coalesces recent change evidence by canonical indexed subtree. Native `watchd` events, successful create/upsert/delete/rename/upload operations, editor saves, and visible Finder/Differ roots feed that owner; equivalent signals must not create separate heat maps or refresh loops.

- Debounce bursts for the same canonical subtree and submit one generation-fenced repair.
- Refresh a changed file by updating or deleting its row and parent-directory metadata. Refresh a changed directory by rescanning that directory and repairing its child frontier, not by walking the entire root.
- Promote a queued frontier item when the changed or user-visible path is already pending; do not enqueue a competing task for the same directory.
- Track heat with a bounded last-change time and score, decay it after inactivity, and remove vanished/out-of-scope paths.
- Guarantee background progress with a tested starvation bound: after a bounded number or time slice of hot items, run an eligible shallow breadth-expansion item.
- Keep the 30-minute interval as a safety net. It may enqueue a new low-priority root generation or reconciliation frontier, but it never invalidates the readable previous snapshot before replacement coverage is published.

Proven cadence and open question. For a root that already holds an `indexd` scheduler lease, item 6 live-proved that a create or delete reflects in the index in about 2.5 seconds (a 2-second debounce plus one bounded repair), against the 1800-second safety interval. Seconds-level freshness is therefore proven for lease-scheduled roots, not unconditional: a mutation driven through the full `POST /api/fs/write`→`jobd` chain did NOT refresh in-window for a root with no active indexer lease. Whether a jobd-executed mutation reliably RPCs `indexd` in a multi-server or follower topology — the same producer path the pre-existing rename reindex uses — is an open question and is not claimed as a guarantee.

## Persistence and recovery

`indexd` remains the only SQLite writer, and every live SQLite/WAL file remains host-local under `STATE_DIR/hosts/<stable-host-id>/search_index/`. The frontier and per-directory coverage must be durable and atomically generation-fenced, or reconstructible from an equally durable per-directory coverage table without a new recursive discovery pass. Restart resumes incomplete breadth-first work at the shallowest pending depth and does not restart DFS from the root.

Persisted state must distinguish configuration/policy signature, active generation, published generation, completed directories by depth, queued directories, last full-coverage completion, last progress, truncation, and errors. A policy or root-identity change creates a new generation while the last compatible snapshot remains readable as stale until the replacement publishes. Partial rows from an abandoned generation never overwrite a newer generation.

## Bounds and path policy

- Preserve the existing single-writer database fence, per-root build lock, tombstone behavior, the shared descriptor-bound path policy, exclusion signature, `index_max_files`, persistence byte/file limits, and network-filesystem rejection. Authorization stays with the one owner in `filesystem/paths.py`; do not add a route-specific path check.
- Bound frontier entries, retries, per-directory entries, transaction rows/bytes, total indexed entries, and concurrent directory scans. Report truncation explicitly instead of presenting incomplete coverage as full.
- Apply one shared exclusion and symlink predicate to startup, hot repair, breadth expansion, and safety refresh. A symlink root covers only its resolved subtree; discovered links cannot escape the configured root or create cycles.
- Treat permission failures, disappearing paths, rename races, and transient I/O errors as per-directory outcomes. They must not discard the last-known-good root snapshot or wedge later frontier work.
- Preserve multi-root fairness and a responsive read path under a very wide directory, deep directory chains, rapid mutations, and slow or blocked storage.

## Status and UI contract

`/api/fs/index-status`, `--print-runtime-report`, and YO!stats Daemons use the same lifecycle and queue projection. Per-root diagnostics expose at least state, active/published generation, snapshot age, published depth, shallowest frontier depth, frontier size, directories and files scanned, pending hot paths, last progress, last layer-1 publication, last full-coverage completion, truncation, errors, and queue reason/priority counts.

The shipped projection reads through one coverage owner, `file_index.read_index_coverage`, which reads live SQLite metadata mid-crawl (tagged `source: live`) with the atomic manifest as a tagged fallback (`source: manifest`) and is bounded to a 50-millisecond busy timeout so a status read never stalls behind a build. `/api/fs/index-status` carries the per-root `progressive_coverage` object; `SearchIndexerClient.runtime_status()` reports measured configured-root obligations rather than the retired hard-coded `demand_started=True`, and `scheduled_root_coverage()` maps each root to a lifecycle of `indexed` (full coverage), `indexing` (published depth or frontier work outstanding), or `scheduled` (leased but no snapshot yet).

With configured roots, an absent `indexd` is not labeled `Idle — Starts on demand`. When a configured root is scheduled, `runtime_status()` reports the scheduled-absence reason `configured_roots_scheduled` so the row reads starting, indexing, or idle-until-a-known-scheduled-wake from measured lease/frontier/deadline state rather than demand-only idle. `Idle — Starts on demand` appears only when no configured root, frontier, hot work, or scheduled maintenance obligation exists.

Quick Open renders last-known-good matches immediately. When coverage is incomplete or stale, it adds compact truthful status without replacing matches with `No matches`; it retries or refreshes asynchronously from producer events. A result outside published coverage is unknown, not a confirmed absence.

## Acceptance tests

- Cold startup with persisted data proves cached matches return before crawling and each configured root publishes layer 1 before any root begins layer 2.
- Cold startup without persisted data proves direct files in each configured root become searchable after one nonrecursive listing, without waiting for a deep descendant.
- Deep-chain and wide-tree fixtures record directory-open order and prove breadth-first depth ordering, multi-root fairness, bounded queue behavior, and no DFS helper use.
- A blocked crawler plus a cache-hit query proves the query returns within the read-path budget and is not queued behind `jobd` or indexing work.
- Change-event tests prove save/create/delete/rename and visible-path signals coalesce, refresh within the hot cadence, repair only the affected directory/subtree frontier, decay, and cannot starve shallow breadth progress.
- Restart, scheduler handoff, settings removal, and policy-change tests prove frontier resume, lease release/acquisition, generation fencing, stale snapshot availability, and no two writers.
- Exclusion, symlink cycle/escape, permission failure, disappearing path, truncation, and network-filesystem tests preserve the current security and durability invariants.
- Status/API/browser tests prove exact lifecycle labels, freshness and coverage fields, partial-result wording, and transition events.
- The final candidate passes focused indexer/search/watcher/browser tests and the canonical `python3 tools/check.py`, then the active dev server is restarted with `YOLOMUX_START_LOAD_WAIT_SECONDS=30` before the implementation is reported ready.

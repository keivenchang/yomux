# Backend Architecture

This document describes the backend that YOLOmux runs now. It does not retain rejected process folds or migration proposals. See [`BACKEND_TEST_CONTRACT.md`](BACKEND_TEST_CONTRACT.md) for verification requirements, [`../DEVELOPMENT.md`](../DEVELOPMENT.md) for operator commands and detailed contracts, and [`../DONE/`](../DONE/README.md) for migration history.

## Runtime topology

```mermaid
flowchart TB
    browser[Browser]
    web[YOLOmux web process]
    tmux[tmux servers and PTYs]
    filesystem[Filesystem and Git repositories]
    runtime[(Local YOLOMUX_RUNTIME_DIR)]
    state[(Local YOLOMUX_STATE_DIR)]

    browser -->|Authenticated HTTP and SSE| web
    browser <-->|Terminal WebSocket| web
    web <--> tmux
    web --> filesystem
    web <--> state

    subgraph services[Six independent local-service processes]
        indexd[indexd]
        statsd[statsd]
        jobd[jobd]
        statusd[statusd]
        watchd[watchd]
        approvald[approvald]
    end

    web <-->|Versioned Unix-socket RPC| indexd
    web <-->|Versioned Unix-socket RPC| statsd
    web <-->|Versioned Unix-socket RPC| jobd
    web <-->|Versioned Unix-socket RPC| statusd
    web <-->|Versioned Unix-socket RPC| watchd
    web <-->|Versioned Unix-socket RPC| approvald
    indexd <--> filesystem
    jobd --> workers[Bounded spawn workers]
    workers <--> filesystem
    statusd <--> tmux
    watchd --> filesystem
    approvald --> tmux
    indexd <--> runtime
    statsd <--> runtime
    jobd <--> runtime
    statusd <--> runtime
    watchd <--> runtime
    approvald <--> runtime
    indexd <--> state
    statsd <--> state
```

The browser connects only to a web process. Local services are separate processes reached through versioned, length-prefixed Unix-socket RPC; they are not modules inside one consolidated daemon. `LocalServiceRegistry` owns client-side discovery, launch, process-identity validation, crash backoff, record publication, lease RPCs, stale-record recovery, and child reaping. Each service and the shared local-service runtime own that service's idle predicate and idle exit. Service sockets and transport locks are rooted under the selected local runtime directory. A registry record follows its configured service directory: most live with runtime services, while `indexd` keeps its record with its host-partitioned index state. Durable service data such as the Quick Open indexes and stats database is rooted under the selected state directory. Neither directory is exposed to the browser.

The web server is `TmuxWebtermHTTPServer`, a `ThreadingHTTPServer`. `http_routes.py` is the route catalog and declares method, path, authorization role, response protocol, body limit, and group. `Handler` owns request state and authentication. Composed adapters such as `FilesystemHttpAdapter` own transport-specific validation and framing, while `TmuxWebtermApp` composes application owners such as `WatchBridge`, `SessionFilesCoordinator`, `ActivityCache`, and `SystemStatusProjector` behind explicit forwarding methods.

Terminal bytes remain a direct browser-WebSocket-to-web-process-to-tmux path. Shared application and operation generations use authenticated HTTP and the `/api/client-events` SSE stream. Stats generations use `/api/stats-stream`, and development reload notifications use `/api/dev-reload`.

## Local services

`LOCAL_SERVICE_INVENTORY` is the single six-service roster. Each row below is an independently launched process.

| Service | Current owner |
| --- | --- |
| `indexd` | Quick Open indexes, per-root SQLite snapshots, manifests, tombstones, and the persisted breadth-first indexing frontier. |
| `statsd` | Original metric observations and usage atoms, retention, derived in-memory layers, and encoded snapshot/delta products. |
| `jobd` | Deferred or CPU-heavy typed work, bounded queues and spawn workers, coalescing, cancellation, and last-known-good materialized products. |
| `statusd` | Shared tmux session inventory, pane classification, immutable status generations, and encoded auto-approve status bytes. |
| `watchd` | Shallow native filesystem watch descriptors, whole-configuration polling fallback after native-backend failure/unavailability, revisions, and changed-path evidence used by browser refresh and index invalidation. Per-root local/network mount partitioning is not implemented yet. |
| `approvald` | Per-target auto-approval workers, target locks, lifecycle actions, and approved tmux input. |

Services may start on demand and retire after their lease/client/work idle condition is met. Expected demand-scoped absence is not itself a failure. The service's runtime row owns that distinction; health projection must not duplicate a second rule for it.

## Lifetime ownership and root-coordination authority matrix

Every backend/sidecar process and every background-election/reuse/signal/unlink/reclaim/adoption caller has exactly one row below, one lifetime owner, and one root-coordination classification: `caller-shared-root-retain` (coordination is legitimate because the socket/port/record it guards must have exactly one owner even when a caller deliberately points two processes at the same root) or `private-root-remove` (coordination is disabled outright because the root is auto-derived and private, so no other caller could ever collide with it).

| Process/caller | Lifetime owner | Demand signal (what keeps it alive) | Root-coordination classification |
| --- | --- | --- | --- |
| `watchd` | `LocalServiceRegistry` (spawn/reclaim) + `PersistentWatchService.idle_due()` (retirement) | Lease/descriptor claims only: `self.leases` transitioning to/from empty, stamped in `_handle_lease`, `_release_locked`, `_reap_locked`. A `status`/`ping`/`snapshot` RPC never counts, whether same-process or external (fixed 2026-08-22 — see the DONE note for this queue). | `caller-shared-root-retain` |
| `jobd` | `LocalServiceRegistry` + `PersistentJobBroker._idle_should_stop()` | `not self.leases and not self._has_active_work() and idle_seconds elapsed`; queued/running jobs count as demand independent of any lease. | `caller-shared-root-retain` |
| `statusd` | `LocalServiceRegistry` + `PersistentStatusService.idle_due()` | `not self.leases and idle_seconds elapsed`; `handle()` no longer restamps the clock on every RPC (fixed 2026-08-22, same defect class as watchd). | `caller-shared-root-retain` |
| `approvald` | `LocalServiceRegistry` + inline `on_idle` predicate in `run()` | `not self.leases and not self.records and idle_seconds elapsed`; `handle()` no longer restamps the clock on every RPC (fixed 2026-08-22, same defect class as watchd). | `caller-shared-root-retain` |
| `indexd` (`search_indexer.py`) | `LocalServiceRegistry` + inline idle predicate | `not self.leases and idle_seconds elapsed`. No handler-level restamp bug found (`on_client` is the sole clock writer already). | `caller-shared-root-retain` |
| `statsd` (`stats_current/service.py`) | `LocalServiceRegistry` + `StatsCurrentService._idle()` via the shared `claim_gated_idle_due` owner | `bool(self.leases) or self._building or pending-materializer-work`, routed through `claim_gated_idle_due` (fixed 2026-08-22 — `_idle` previously reimplemented the transition/deadline algorithm inline and let `_on_client` restamp the shutdown clock on every RPC, the same defect class already fixed in the other five services). `last_rpc_at` (stamped by `_on_client`) is a distinct field used only to gate deferred SQLite vacuum quiescence; it never feeds the shutdown decision. | `caller-shared-root-retain` |
| `LocalServiceRegistry` spawn/reclaim/retire path (all six services) | Registry itself, fenced by `HostIdentity`/`process_record_diagnostic` | N/A (infrastructure, not a service) | `caller-shared-root-retain` — flock election in `run_local_rpc_service`, `_can_reclaim_dead_launcher_service`, and `_retire_incompatible_service` always require an exact host/boot-id/PID-start-identity/generation match before any signal or unlink; a private root never has a second caller to collide with, so the coordination is inert there rather than unsafe. |
| `acquire_server_port_lease` (`yolomux_lib/server_lease.py`) | Same lease file, fenced by `HostIdentity` | N/A (one TCP port is inherently OS-shared even under a private root) | `caller-shared-root-retain` |
| `GroupOverloadWatchdog` (`yolomux_lib/local_services/watchdog.py`) | Watchdog itself, fenced by `process_record_diagnostic` per signal | N/A (self-containment of one port's own tracked group) | `caller-shared-root-retain` |
| `preflight_port` (`yolomux_lib/local_services/preflight.py`), invoked from `boot.sh` startup | Preflight itself, fenced by `process_record_diagnostic`/lease-record identity before any signal | N/A (boot-time reap of a dead-owner's orphaned tracked group for one port, not a running service) | `caller-shared-root-retain` — SIGTERM then bounded-grace SIGKILL, scoped to the exact tracked group of the port being started; never a broad sweep. |
| Web-process background-owner election (`yolomux_lib/infra/background_owner.py`) | `BackgroundOwnerRegistry` (real election) or `DisabledBackgroundOwner` (no-op) | Election among web processes sharing one `YOLOMUX_STATE_DIR` | `private-root-remove` for a managed instance — `cli.py` passes `managed_instance=is_managed_instance_port(args.port)` into `app.py:start_background_owner`, which installs `DisabledBackgroundOwner` and skips election entirely. `caller-shared-root-retain` for an explicit caller-set `YOLOMUX_ROOT`, where the real `BackgroundOwnerRegistry` still applies. |

Root provenance (`managed` vs. an explicit caller-set root) is carried by `InstanceIdentity` in `tools/instance_isolation.py` and reaches exactly one consumer today (`app.py:start_background_owner`). `LocalServiceRegistry`, `server_lease.py`, and `watchdog.py` do not consult it, and should not: the resources they coordinate (one socket, one port, one signal target) need exactly-one-owner semantics under a shared root and are simply never exercised cross-instance under a private root, so gating them on `managed` would be redundant, not corrective. Threading `managed` further through those call sites was evaluated and deliberately deferred — see the DONE note for the evidence.

### Bounded host-local repair path for ambiguous survivors

`verified_orphan_diagnostics` (`yolomux_lib/local_services/registry.py`) composes `tracked_local_service_groups` and `untracked_local_service_processes` into one typed row per process that looks like a local service (a `yolomux_lib.` `-m` module plus a `--socket` under the shared services directory) but carries no ledger record proving its identity. Because identity can never be fully verified for a process with no record, every row is diagnostics-only: `attempted_action` is always `"none"` and `result` is always `"reported_only"` — this function never signals or unlinks (Rejected Shortcuts: no signal without ledger-proven authority). statusd exposes this through a new `orphan_diagnostics` RPC action (`PersistentStatusService.orphan_diagnostics`), which adds retained `age_seconds` from its own first-seen bookkeeping across supervision passes (pruned once a pid stops appearing) since a single process-table snapshot carries no wall-clock-comparable process birth time. This satisfies "each ambiguous survivor emits one typed orphan record within one supervision pass rather than remaining silent" without weakening the zero-signal/zero-unlink guarantee for unverifiable identities.

## Web-process coordination owners

The background owner is a role elected among web processes sharing one local `YOLOMUX_STATE_DIR`; it is not a seventh service. The elected process owns recurring refresh coordination, watch-root intent consumption, metric-family collectors, and warmer lifecycles. Followers serve ready or stale shared products and ask the owner to refresh rather than starting duplicate background work. Election uses a process lock plus heartbeat/generation records, while each local service retains its own service lock and writer rules.

`BackendHealthObserver` runs inside each web process. It samples the six-service roster on a bounded cadence without demand-starting absent services and writes retained per-port history through `BackendHealthStore`. The web process's own metrics remain explicitly unobserved by that service probe instead of being fabricated.

`/api/system-status` reads a pre-encoded immutable snapshot published by a background snapshot owner. The request thread never rebuilds the status document. Before the first snapshot or after its freshness deadline, the route returns a typed unavailable or stale result.

## Filesystem boundaries

`filesystem.list_directory()` is the shared listing owner. It validates the requested path through the one descriptor-bound authorization owner in `filesystem/paths.py`, filters secret or credential-blocked paths, stats direct children, applies the entry bound, sorts the result, and can omit repository enrichment. Every listed child is opened relative to the pinned parent descriptor through `paths.safe_child()`, and its metadata, symlink target, and repository enrichment are read from that pinned child generation rather than by reopening the child's name (`listing.py` child scan; regression `test_listing_symlink_metadata_stays_bound_to_the_authorized_target`). Recursive ZIP/count, Git diff, and indexed-search metadata consume the same pinned generations. Search/index exclusion rules are separate from directory listing. A listing is one directory level; it does not recursively enumerate descendants.

| Surface | Execution path | Current use |
| --- | --- | --- |
| `GET /api/fs/fast/list?path=...` | `FilesystemHttpAdapter` calls `filesystem.list_directory(..., include_repo_info=False)` in the web process and returns the snapshot directly. It does not call `jobd`. | Every Finder directory LIST, including the root and remembered descendants. A successful call is HTTP 200 and contains only that directory's direct entries and base metadata. |
| `POST /api/fs/batch` | The web process submits a bounded typed batch to `jobd`; a cold product may return an operation receipt and complete through the shared client-event/operation path. | Deferred detailed work. Finder sends Git/repository `INFO` enrichment here after base rows exist and patches mounted rows when results arrive. |
| `GET /api/fs/list` | The compatibility single-operation path submits `list` through the existing filesystem-product owner. | Existing callers outside Finder; it is not the Finder first-paint route. |

```mermaid
sequenceDiagram
    participant Browser as Finder in browser
    participant Web as Web process
    participant FS as Filesystem owner
    participant Jobd as jobd and workers

    Browser->>Web: GET /api/fs/fast/list?path=root
    Web->>FS: list_directory(root, include_repo_info=false)
    FS-->>Web: One-level entries and base metadata
    Web-->>Browser: HTTP 200
    Browser->>Browser: Paint root rows immediately

    par Restore remembered descendants progressively
        Browser->>Web: GET /api/fs/fast/list?path=child
        Web->>FS: One-level list
        FS-->>Web: Direct child entries
        Web-->>Browser: HTTP 200
        Browser->>Browser: Paint each completed subtree
    and Enrich repository details after paint
        Browser->>Web: POST /api/fs/batch with INFO items
        Web->>Jobd: Submit bounded filesystem batch
        Jobd-->>Web: Ready product or operation receipt
        Web-->>Browser: INFO results now or after completion event
        Browser->>Browser: Patch mounted rows in place
    end
```

Cold Finder Sync has no whole-tree render barrier. It awaits the fast root, paints it, and then fetches remembered descendant directories with bounded concurrency; each completed listing triggers another render. Git enrichment is independently scheduled after LIST publication, so a cold `jobd`, a queued product, or slow Git cannot delay names, types, sizes, or dates from the fast snapshot.

Quick Open indexing is separate from Finder listing. `indexd` persists a breadth-first, directory-at-a-time frontier. It publishes a complete depth before advancing `published_depth`, retains the previous readable generation while replacement work proceeds, and resumes the shallowest pending directory after restart. Finder visibility and concrete watch/mutation paths promote or repair work through the shared index invalidation owner; they do not start a second crawl.

## State and durability

`YOLOMUX_STATE_DIR` is local-host coordination and durable-state scope. Web processes that select the same state root share background-owner records, durable caches, and host-partitioned databases. `YOLOMUX_RUNTIME_DIR` separately scopes service sockets and transient runtime data; processes share local-service connectivity only when they select the same runtime root. Registry record placement follows the service owner and therefore may be runtime-rooted or state-rooted; selecting the same state root alone does not imply a shared service socket. Stateful families that must not cross hosts live below `STATE_DIR/hosts/<stable-host-id>/`. Live SQLite WAL files are not supported on a network filesystem.

The primary port uses the durable default state root. Non-primary development ports are isolated under an ephemeral per-port `/tmp` root, so their retained health, caches, and service state do not survive a reboot or `/tmp` cleanup. The UI reports retained history from the actual selected root rather than assuming that every port is durable.

## Extension rules

| Change | Extend this owner | Required proof |
| --- | --- | --- |
| Local-service action | Add one named handler to that service's `LocalServiceCommandRouter`; use shared daemon actions only when the response semantics are identical. | Preserve validation-before-dispatch, framing, error vocabulary, lease behavior, and the fixed action inventory. |
| Local-service runtime field | Add the field once to `LocalServiceRuntimeRow` and the shared projection; a service adapter supplies only its domain value. | Exercise runtime-row and backend-health projection tests and prove projection does not demand-start the service. |
| HTTP route | Register route metadata in `http_routes.py`, then put filesystem or response-framing work in the relevant adapter. | Compare the route catalog and verify auth, role, body limit, headers, framing, and typed error behavior. |
| Application domain behavior | Extend the composed domain owner that already owns its mutable state and teardown. | Characterize payload bytes, callbacks, lock order, replacement, stale completion, failure, and shutdown. |

New backend work follows explicit composition behind stable facades. Do not add a generic service locator, a parallel cache for the same product, or a second copy of service inventories, filesystem policy, runtime-row fields, or response semantics.

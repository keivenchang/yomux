# YOmux Go Backend Status

Go owns the product runtime. `make setup` builds the native server, publishes browser assets, and installs `yomux` and `yolomux` below `PREFIX` (default `~/.local`). Installed commands execute the binary and assets under `PREFIX/lib/yomux`. Their `install-source.json` records the verified Git checkout for self-update. Startup uses the installed files; updates require the recorded checkout to remain available. Python remains available for developer tools, mocks, fixture builders, and test controllers; it is not a product installation or startup prerequisite.

## Retirement Decision

`boot.sh <port>` runs the managed lifecycle. `--backend go`, `--backend auto`, and `--golang` select Go. Python selections through either environment prefix, `--deprecated-python`, and invalid backend values are rejected before product roots, leases, or listeners change. A missing or unexecutable Go binary fails without fallback. `--sessions`, Python transcript/runtime reports, the Python development reloader, optional login decoys, firewall escalation, and unavailable integrations are retired. Unsupported API routes retain their typed unavailable responses.

## Native Launch

```bash
make setup
./boot.sh --port 7111
```

One `yomux-go` process owns HTTP/TLS and authentication, terminal WebSocket and event streams, Finder/Differ, settings, stats, chat, and agent conversations, jobs, watches, skills, and reviewed sends. Finder may start a private native index worker. Product startup does not launch the retired Python web server or its daemons.

`yomux-go --operator` owns root planning, captured `unset`/`assign` plans, executable/version checks, listener census, authenticated readiness, session discovery, profile dispatch, and native asset publication. Mutable roots and existing persistence formats remain instance-owned. Explicit profiles preserve their configuration, state, certificates, and ports. Installation records the source checkout separately from the installed resource root. Self-update verifies that source, builds the selected revision with native tools, refreshes the same installation prefix, and restarts the installed binary.

Runtime JSON lives in `assets/runtime/`; built-in agent skill/context files remain packaged as data. `make build` publishes generated browser assets under the shared checkout writer declaration. The private-output `tools/staticgen` executable remains the Bazel and self-update builder.

## Architecture Decision

The Go backend is a modular monolith. It does not reproduce Python's web-process-plus-six-local-services topology.

```mermaid
flowchart LR
  browser["Shared browser bundle"] --> listener["Go HTTPS/auth/SSE/WebSocket listener"]
  listener --> tmux["tmux PTY and session control"]
  listener --> finder["internal/finder"]
  listener --> events["events and operation journal"]
  listener --> stats["internal/stats"]
  finder --> work["bounded internal/work schedulers"]
  finder --> fs["descriptor-bound filesystem and Git"]
  stats --> db["stats-v9.sqlite3"]
  stats --> collectors["host, agent-status, token, and OpenCode collectors"]
  listener --> pages["Native templates, localization, settings, and bootstrap"]
```

One `yomux-go` process owns:

* HTTPS, TLS, authentication, static files, and the shared application bootstrap.

* tmux discovery, session creation and deletion, PTY/WebSocket terminal I/O, and terminal status paths that have registered handlers.

* An SSE event broker, durable accepted-operation journal, terminal operation events, acknowledgements, cancellation, and repair after restart.

* Finder and Differ routes, descriptor-bound filesystem access, Git subprocesses, watches, uploads, previews, and bounded in-process schedulers.

* YO!stats SQLite writing/reading, snapshots/deltas/SSE, materialization, bounded rings, pricing, host metrics, agent status, transcript tokens, and OpenCode ingestion.

The Go process does not start or talk to Python `statsd`, `indexd`, `batchd`, `statusd`, `watchd`, or `approvald`. Those names describe the Python backend only.

## Source Ownership

| Owner | Responsibility |
| --- | --- |
| `cmd/yomux-go/main.go` | Listener, TLS/auth, tmux PTY/WebSocket, core routes, event broker, operation journal, diagnostics, startup, and shutdown |
| `cmd/yomux-go/finder_jobs.go` | Bounded interactive, background, bulk, and streaming Finder admission and operation completion |
| `cmd/yomux-go/differ_routes.go` | Session-file request identity, persistent cache, Git-backed Differ data, and cache refresh |
| `cmd/yomux-go/settings_native.go` | Shared-schema settings validation, migration, atomic persistence, pricing history, and POSIX writer locking |
| `cmd/yomux-go/stats_runtime.go` | YO!stats route adapters, runtime collectors, migration, snapshots, deltas, streams, pricing, readiness, and diagnostics |
| `internal/capabilities` | One capability catalog used by bootstrap, route admission, and diagnostics |
| `internal/finder` | Descriptor-bound filesystem access, listing, read/raw/info, watch, search, Git diff/history/commit/blame, mutations, upload, archive, and HTML preview |
| `internal/stats` | SQLite writer/readers, schema, observations, snapshots, rings, lifecycle materialization, host collection, agent status, token collection, pricing, and OpenCode |
| `internal/work` | Bounded worker/queue scheduler used inside the Go process |
| `static_src/` and `static/` | Browser source and generated bundle served by Go |
| `cmd/yomux-go/pages_native.go` and `pages/` | Native home/login/setup templates, escaping, localization, agent notices, and bootstrap data |
| `cmd/yomux-go/runtime_environment.go` | Shared native command discovery, child environments, and bounded command output |
| `cmd/yomux-go/launch_native.go` | Native launch plans, operator probes, profile dispatch, leases, and verified replacement |
| `internal/chat` and `cmd/yomux-go/chat_*.go` | Chat storage, history, presence, questions, events, and authenticated HTTP handlers |
| `internal/yoagent` and `cmd/yomux-go/yoagent_*.go` | Agent conversations, skills, jobs, watches, reviewed sends, and native CLI transport |
| `yomux_lib.web` | Source-only developer renderer and fixture support; not a product server |

## Settings Ownership

Go reads, saves, and reconciles settings without starting Python. `cmd/yomux-go/settings_native.go` owns validation, migration, atomic YAML persistence, and effective-dated pricing history for HTTP requests and startup reconciliation. Defaults, choices, bounds, descriptions, and migration values live in `assets/runtime/settings-schema.json`; retained Python tooling reads the same asset.

Writers use the existing adjacent POSIX record lock so independent processes preserve each other's updates. Go also serializes writers within its process. Malformed YAML is preserved and reported; it does not replace the last valid Finder index policy. The native settings contract includes fixed baseline fixtures, writes and restart reconciliation with Python absent from `PATH`, pricing history, concurrent independent patches, and lock contention. Cross-host locking and macOS runtime acceptance remain separate rollout checks.

## Capability Contract

`GET /api/capabilities` is the browser's feature-admission source. A capability is not proof that every Python route in that family exists; the registered handler list below remains authoritative for route availability.

The current catalog marks these capabilities available:

* `core.bootstrap.v1`, `core.diagnostics.v1`

* `terminal.websocket.v1`, `terminal.tmux.v1`, `terminal.status.v1`

* `session.roster.v1`, `session.create.v1`, `session.kill.v1`, `session.rename.v1`

* `pane.finder.v1`, `pane.differ.v1`, `pane.stats.v1`, `pane.tabber.v1`

* `finder.read.v1`, `finder.watch.v1`, `finder.write.v1`, `finder.search.v1`, `finder.batch.v1`, `finder.git.v1`, `finder.preview.v1`

* `yolo.auto-approve.v1`

The server enables `pane.chat.v1` and `pane.agent.v1` after their native stores initialize. `core.integrations.v1` remains unavailable. Watched pull requests and hosted GitHub/Linear enrichment are retired. Go does not register `/api/search`, `/api/run-history`, `/api/activity-summary`, or `/api/watched-prs`; the corresponding Search & Runs, background-summary, and integration services are outside the supported feature set. Native bootstrap disables background activity summaries. Terminal transcripts and per-session event logs retain their registered handlers.

Any unregistered `/api/*` request receives HTTP `501` with `error: feature_unavailable` and the mapped capability name. Unsupported product code must not silently return an empty Python-shaped payload.

## Registered Go Routes

The following routes have concrete handlers in `cmd/yomux-go`.

### Core, Authentication, Events, And Operations

* `/healthz`, `/livez`, authenticated `/readyz`

* `/login`, `/logout`, `/api/auth-setup`

* `/`, `/static/*`, `/api/ping`, `/api/capabilities`

* `/api/client-events`, `/api/event`

* `/api/operations/{id}`, `/api/operations/ack`, `/api/operations/cancel`, `/api/operations/accept`, `/api/operations/terminalize`

* `/api/background/status`, `/api/background/claim`

* `/api/system-status`, `/api/system-status/advanced`, `/api/logs`

### Terminal And Sessions

* `/ws`, `/api/tmux`, `/api/tmux-signals`, `/api/tmux-session-exists`

* `/api/session-metadata`, `/api/activity`

* `POST /api/attention-ack`

* `/api/ensure-session`, `/api/create-session-plan`, `/api/create-session`, `/api/rename-session`, `/api/kill-session`

* `/api/transcripts`, `/api/transcript`, `/api/context`, `/api/context-items`, `/api/context-stream`

* `/api/auto-approve`, `/api/yolo-rules`, `/api/yolo-rules/open`, `/api/yolo-rules/reload`

The shared browser must capability-gate unavailable terminal/session paths. Go publishes per-window completion timestamps and transition identities through session metadata and Tabber activity; `POST /api/attention-ack` stores bounded acknowledgements and publishes `attention_acks_changed` so other tabs converge. `yolo.auto-approve.v1` owns the authenticated per-session toggle and status route. Its worker uses the shared target lock, pins tmux session and pane identity plus the live agent process, and suspends when fresh status or terminal evidence is unavailable. It applies the configured `approve`, `decline`, `block`, `ask`, `notify`, and `off` actions to supported Claude and Codex prompts, honors `yolo.dry_run`, and retries only the exact current Codex capacity notice with `continue\n\n` at a minimum three-second interval. Self-update uses the `core.updates.v1` capability. Native chat and agent handlers own their browser routes after successful initialization. Go's Tabber activity route is admitted by the machine-wide tmux roster, so every live session gets one top-level row before pane metadata or agent enrichment arrives. Transcript routes accept an optional `pane_target` or `window`; without either, Go resolves the active tmux pane. This prevents a session containing multiple Claude, Codex, or OpenCode windows from returning the first unrelated transcript.

### Finder, Editor, And Differ

* `/api/fs/fast/list`, `/api/fs/list`, `/api/fs/read`, `/api/fs/info`, `/api/fs/raw`

* `/api/fs/write`, `/api/fs/delete`, `/api/fs/rename`, `/api/fs/mkdir`, `/api/upload`

* `/api/fs/search`, `/api/fs/search-stream`, `/api/fs/index-status`, `/api/fs/unindex`

* `/api/fs/watch-diff`, `/api/watch/roots`

* `/api/fs/batch`, `/api/fs/resolve-file-candidates`

* `/api/fs/diff`, `/api/fs/git-history`, `/api/fs/git-history-stats-stream`, `/api/fs/git-commit`

* `/api/batch/search`, `/api/batch/blame`, `/api/batch/zip`, `/api/batch/count`, `/api/batch/html-preview`

* `/api/session-files`, `/api/session-files-stream`, `/api/session-files-batch`

TextEdit and ViewEdit use the shared browser implementation and these Finder read/write/raw/upload/preview routes. There is no separate Go editor daemon or editor capability family.

Go Finder accepts any absolute path the process can access. It does not apply Python's configured-root allowlist or credential-name denylist. The security boundary is OS permission plus descriptor identity, bounded reads/walks/results, authorization role, and the protected product-root mutation guard. Symlink and namespace-sensitive operations revalidate descriptor identities before use; the implementation documents the remaining same-UID basename race where the operating system lacks a conditional namespace syscall.

Finder's fast path lists names without optional repository enrichment. The browser can paint the first directory immediately, then request or receive slower Git/status data. Differ history follows the same split: commit rows can load without `+N/-N`, and `stats=1` backfills numstat counts. Commit expansion uses one bounded Git detail path for metadata, file status/counts, and diff content rather than issuing one Git process per field.

### YO!stats

Tabber's Go API is separate from YO!stats storage: `/api/activity?scope=all` returns the global tmux session order plus live pane/window metadata and activity fields consumed by the shared Tabber tree. The Go command boundary clears inherited nested-tmux variables for this machine-wide read; tests use `YOMUX_TMUX_SOCKET` with synthetic sessions when isolation is required.

* `/api/stats-agent-status`, `/api/stats-capabilities`

* `/api/stats-observations`, `/api/stats-snapshot`, `/api/stats-delta`, `/api/stats-stream`

* `/api/pricing-catalog`

The Go process opens `stats-v9.sqlite3` directly and owns its writer lock. It does not use the Python statsd RPC protocol. The current storage, wire, and parity gaps are listed in this guide's YO!stats section and current-gaps list.

When the Go token collector enables database checkpoints, it upgrades that same file to schema 10 with minimum writer protocol 26. The filename stays unchanged. Parser progress, exact comparison bytes, and scheduling metadata share the writer transaction with usage facts and tombstones; a checkpoint-only update does not change graph source generation. A bare writer without token collection can still use schema 9 for interoperability fixtures. Unchanged Python cannot write the migrated database.

Before upgrading, Go holds the database writer lease and legacy cursor locks, makes a consistent SQLite backup, and copies the paired cursor files and writer fence into an adjacent `<database>.pre-checkpoints-*` directory. `cursor-manifest.json` maps original paths to backup files. The `go-checkpoints-v1` row in `migration_reconciliation` records the backup directory in `source_digest`. Pending legacy cursor work is replayed from committed progress.

The removed Python server is not a supported rollback target. For recovery to an explicitly selected compatible Go build, stop the current owner first and preserve the migrated database, WAL/SHM sidecars, writer fence, and cursor files separately. Restore a consistent backup and its complete cursor manifest only after checking the selected build's schema and writer-protocol compatibility. Keep migrated sidecars away from the restored database and retain both backup sets so recovery preserves newer facts.

### Chat And Agent

Native chat registers `/api/chat/bootstrap`, `/api/chat/page`, `/api/chat/delta`, `/api/chat/context`, `/api/chat/search`, `/api/chat/send`, `/api/chat/yoagent`, `/api/chat/typing`, and `/api/chat/read`. Native agent configuration and actions register the following routes:

* `/api/agent-auth`, `/api/yoagent/skills`, `/api/yoagent/skill-files`, `/api/yoagent/skill-files/upsert`, `/api/yoagent/skill-files/delete`

* `/api/yoagent/jobs`, `/api/yoagent/jobs/`, `/api/yoagent/conversation`, `/api/yoagent/chat`, `/api/yoagent/chat/`

* `/api/yoagent/actions/preview-send`, `/api/yoagent/actions/execute-send`, `/api/yoagent/waits/`, `/api/yoagent/prewarm`, `/api/yoagent/reset`

The server enables chat and agent capabilities only after their stores initialize. Their native owners retain conversations, jobs, watches, skills, and reviewed-send state; removing Python does not authorize deleting that state.

## Completed Delivery Slices

| Slice | Current result |
| --- | --- |
| Phase 0 | Bazel/rules_go build, Docker test image, contract fixtures, capability/bootstrap contract, and isolated launch path |
| Phase 1 | TLS/authenticated Go skeleton, shared HTML/bootstrap, tmux discovery, and first PTY/WebSocket path |
| Phase 2 | Terminal/session behavior, role checks, lifecycle, resize/input/output, and browser contract completion for the supported terminal path |
| Phase 3 | SSE events, accepted-operation receipts, durable journal/restart repair, diagnostics, and background status ownership |
| Phase 4/5 | Finder read/write/watch/search/batch/upload/preview and arbitrary absolute-path support with descriptor-bound safety |
| Phase 6/7 | Shared TextEdit/ViewEdit exercised through Go Finder APIs, including save, upload, preview, and Chromium editor workflows |
| YO!stats | Go SQLite ownership, snapshots/deltas/SSE, collectors/materialization/rings, pricing, agent status, tokens, OpenCode, and Docker browser coverage |
| Phase 8 | Differ session-file caches, quick history followed by numstat backfill, consolidated commit detail, diff, history, commit, and blame routes |

The transcript compatibility routes remain distinct wire contracts. Their current Go behavior is owned by the registered route handlers and tests; no separate consolidation queue is active.

Phase numbers are migration history, not runtime configuration. The capability catalog and registered routes determine current behavior.

## API And Async Contracts

The Go backend preserves the shared response and async rules:

* Auth and role checks run before route work.

* Immediate work returns a terminal response envelope.

* Accepted work receives a durable operation id and must publish exactly one terminal completion or failure.

* Operation acknowledgements are persisted and pruned within bounds.

* Request cancellation does not cancel work after the server has returned an accepted receipt; explicit operation cancellation owns that transition.

* Events and operations survive bounded restart repair through `go-events.jsonl`, `go-operations.json`, and `go-operations.jsonl` under the selected private state/config root.

* Unknown or unavailable routes return typed failures instead of HTTP 200 with embedded error text.

The Go service uses separate bounded schedulers for interactive Finder work, background refresh, bulk work, and streamed search. Queue saturation returns a typed busy failure. It does not create Python `batchd` workers or rely on a cross-process queue.

## Build And Test Contract

The canonical full host gate is:

```bash
python3 tools/check.py
```

Its default `bazel-go` lane invokes the standalone Go wrapper below. Run the wrapper directly for focused Go validation; do not run it after a successful full gate against the same frozen tree merely to repeat the suite.

```bash
tools/run_bazel_tests.sh --jobs "$(getconf _NPROCESSORS_ONLN)"
```

The wrapper accepts an optional positive integer `--jobs`; when the flag is omitted it computes the host CPU count. On the host it launches `docker/run-tests.sh`, mounts source read-only, creates private Bazel output/cache/GOPATH/GOCACHE roots, disables downloads and network access for test execution, and removes the invocation-owned writable root afterward. Parallel invocations use different temporary roots and fixture-owned ports.

`//:go_tests` includes:

* Go unit suites for the server, capabilities, Finder, stats, fake LLM, and scheduler.

* Race-enabled Go suites for those owners.

* Go binary/API contract and Bazel action-environment tests.

* Docker runtime contract coverage.

* Dockerized Chromium Finder/editor/Differ end-to-end coverage.

* Dockerized Chromium YO!stats coverage.

* Dockerized agent-status integration coverage.

`python3 tools/check.py` covers Python, the shared frontend, and the Dockerized Go suite once. A fixed live port is a manual smoke target only and cannot replace that gate.

## Remaining Gaps

* Native YO!chat uses `internal/chat` for persisted history, presence, questions, and events. Native YO!agent uses `internal/yoagent` and the installed Claude/Codex CLI for conversations, skills, jobs, watches, and reviewed sends. Hosted integrations are retired; final runtime acceptance remains open.

* Go update detection and managed self-update now use root `version.yaml`, the `core.updates.v1` capability, cached `/api/update-status`, persistent `/api/self-update/status`, and `boot.sh` for selected-instance replacement. The installer refuses dirty, diverged, or changed checkouts and prepares the pinned binary and browser assets before activation. Native Darwin qualification remains a release acceptance step.

* Home, login, setup, settings HTTP requests, and startup settings reconciliation use Go. Managed launch planning and installed product commands use native owners. Self-update prepares assets through the native static builder.

* Go credential admission is durable and shared across form, Basic API, and WebSocket paths. Network reservations, successful refunds, username cooldowns, hard locks, HMAC identifiers, and bounded in-flight claims live in a separate state-rooted SQLite store. Go accepts the documented policy override keys; the Python recovery CLI, throttle diagnostics, decoy, and firewall escalation are explicitly retired.

* Go consumes `YOMUX_CONFIG_DIR`, `YOMUX_STATE_DIR`, `YOMUX_ROOT`, and `YOMUX_STATS_DATABASE`, and admits its config/journal directory and auth/stats WAL stores only on supported local filesystems unless the explicit emergency override is set. Go does not implement Python's `YOMUX_RUNTIME_DIR`/`YOMUX_CACHE_DIR` service topology or automatic host partition. Event/operation journals, cookie auth material, background-owner records, and the default stats database remain config-rooted; the session-files cache and credential-admission database use the state root. A canonical config lifetime lease prevents independent event brokers from sharing those journals. Use the managed launcher so each preview gets private local roots.

* Go YO!stats owns its storage and collectors in process; it does not implement the legacy Python statsd RPC/lifecycle topology. The retired Python daemon topology is outside the supported runtime.

* Go stores Claude/Codex transcript and OpenCode parser progress in versioned checkpoints in the YO!stats database. Migration imports legacy cursor sidecars and pairs them with a database backup; unchanged Python may reject schema 10. Historical fixtures retain their provenance; the removed server is not a live test oracle.

* Runtime agent/OpenCode discovery currently uses Linux adapters. Cross-platform parity is not established.

* Exact cross-backend output comparison, long-history migration evidence, and production-scale performance evidence remain incomplete.

* The Go build/test/runtime path is Linux-first. Docker is the required certification environment for current Chromium and race coverage.

## Rollout Rules

1. Build the exact worktree with Bazel.

2. Run the complete Dockerized Bazel suite with fixture-owned state and ports.

3. Fix product failures; do not hide them with retries, sleeps, lower concurrency, or a live-port substitution.

4. Start an explicit non-production preview port through `boot.sh` only after automated checks.

5. Verify the listener, `/healthz`, capability snapshot, and exact browser workflow against the rebuilt bundle.

6. Accept retirement only after the exact final candidate passes the native launch matrix, setup/build/update without Python, supported browser journeys, package/import audit, and full repository gate. Verify the selected 7111 lifecycle first; preserve 7110 and existing agents. Broader fleet rollout requires a separate acceptance decision. A documented native owner does not establish that these checks passed.

## Related Documents

* [`README.md`](README.md): operator quickstart and native runtime overview.

* [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): prerequisites, source layout, build, tests, and restart commands.

* [`docs/specs/BACKEND_ARCHITECTURE.md`](docs/specs/BACKEND_ARCHITECTURE.md): current native process ownership and filesystem boundary.

* [`docs/specs/GUI.md`](docs/specs/GUI.md): shared user-interface behavior and verification rules.

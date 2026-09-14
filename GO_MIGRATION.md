# YOLOmux Go Migration Plan

## Decision

Build a new Go backend beside the Python backend and migrate product surfaces in vertical slices. The existing JavaScript, generated bundles, CSS, xterm.js, Dockview layouts, URL formats, HTTP paths, response envelopes, Server-Sent Events, and WebSocket messages remain the compatibility contract. Users select Python for the complete product or Go for the currently implemented subset at startup.

## Current Progress — 2026-09-14

The `go_phase5` branch contains the Phase 1 secure Go foundation, Phase 2 terminal completion, Phase 3 event and operation parity, Phase 4/5 Finder filesystem parity, Docker/Bazel validation alignment, and Phase 6/7 editor coverage after the 0.8.7 release commit. The Phase 4/5 work adds one Go capability catalog and Finder owner for bounded path authorization, listings, reads, raw responses, file metadata, watches, mutations, uploads, candidate resolution, batch work, and search; the frontend and generated bundle route these operations through the shared capability and event paths rather than a Go-specific UI fork.

The completed Phase 4/5 batch was validated inside Docker with Go race tests, `go vet`, the static-bundle check, all 21 Node layout shards, 71 focused Python tests, and Go Chromium coverage. The Chromium journey exercised real Finder listing, expansion, collapse and re-expansion, temporary-file deletion through the context menu, and the resulting API 404. Automated checks use isolated Docker state and test ports; `0.0.0.0:7221` is reserved for final human inspection, and production port 7220 is outside this migration workflow.

Phase 6 TextEdit and Phase 7 ViewEdit are complete at the shared file-contract boundary. The Go Chromium journey passed through Bazel and Docker with native source editing, save, expected-mtime conflict preservation, Markdown comments, GFM tables, safe and literal HTML, image paste/upload, serialized save, disk verification, and reopen round trips; the full Dockerized `python3 -m pytest tests/test_browser_editor.py` gate passed 62 tests, skipped 2, and xfailed 1, the uncached Bazel Go contract/race suite passed, the Bazel Chromium E2E passed, and the Dockerized Node launcher passed all 21 layout shards, including 58/58 cross-surface checks. The browser assertions were aligned with the current shared contracts for the Bright/Vanilla/Dark preview cycle, authenticated raw image URLs, ProseMirror ViewEdit serialization, and the shared terminal URL-menu cleanup owner; no Phase 6/7 editor gate failures remain.

### Next Steps

- Keep `go_phase5` as the review point; do not push or change the Python production deployment as part of this preview.
- Use `0.0.0.0:7221` only for the final human inspection after the automated gates above pass; do not connect to production port 7220.
- Retain the Docker/Bazel outputs with the review and restart the Go server from the reviewed branch on `0.0.0.0:7221` only for the final human check.
- Treat YO!stats work on `yo7222` as a separate later integration stream; it does not change the Phase 6/7 file-editor contracts or authorize using port 7220.
- After the editor contracts are stable, proceed to Phase 8 Differ against the shared Finder and Git boundaries, with a fresh independent audit and exact Chromium journeys before claiming completion.

The Go implementation should be a modular monolith: one executable, one HTTP listener, direct Go calls between modules, and one in-process event broker. Separate processes remain available for work that can wedge, consume unbounded memory, or outlive a request, but process boundaries must be justified by measured failure isolation rather than copied from the Python daemon layout.

Bazel is the build, test, dependency, and release entry point from Phase 0 onward. The migration must not begin with an ad hoc `go build` workflow and retrofit Bazel later. Go module metadata may remain where required by the Go toolchain and dependency ecosystem, but checked-in Bazel targets and toolchains are authoritative for supported builds and tests.

### Porting And Parallelization Rules

- Treat the existing Python implementation as the specification, not as inspiration. Before implementing a Go phase, inventory every browser URL/API/WebSocket message, HTTP route, auth/readonly gate, PTY lifecycle, tmux command/option/target, timer, reconnect path, and owning test.
- Port the Python owner and its tests before introducing a Go-specific abstraction or fallback. A local xterm `cols`/`rows` change is not terminal parity.
- Split phases into independently testable workstreams only at explicit contract boundaries. Parallel work may share frozen fixtures and read-only manifests, but must not create competing owners for one route, state projection, capability, or generated asset.
- Each parallel workstream must declare its inputs, owned files/routes, dependency gate, test target, and integration owner. Integration is a separate task that runs the shared browser journey after all workstreams land.
- UI workstreams may proceed in parallel with backend work only against checked-in contract fixtures or a fixture server. They cannot claim feature completion until the real backend path, restarted server, and real Chromium journey pass.
- Never parallelize two implementations of the same behavior. One named parent owns each route, wire message, tmux operation, capability, and shared UI builder; adapters pass data into that owner.
- For xterm/tmux changes, prove the full chain in real Chromium against an owned server and tmux socket: JS emits the expected message, the backend receives it, PTY dimensions change, `SIGWINCH` reaches the attach process, tmux client/window dimensions change, redraw bytes return as binary WebSocket frames, and xterm renders them.
- Treat TLS trust, wrong tmux sockets, stale binaries, missing routes, and attach-process exits as product failures until classified. E2E fixtures must own TLS trust, config/auth, tmux socket/session, server process, browser, logs, and tmux client-row evidence.
- When a live regression follows a passing test, stop speculative patching. Reproduce the exact live URL/session/role/client path, compare the live message sequence with Python, preserve the red case, and add that scenario to the owning E2E test before changing code.
- Backend parity includes exact tmux targets (`=<session>:`), configured sockets, `ignore-size` probing, attach flags, resize authority by client/PTTY, foreground filtering, refresh acknowledgements, copy-mode scroll, query-response input filtering, and attach re-entry.
- Reuse Python `login_html()`/`html_page()` and checked-in static assets instead of duplicating HTML/JS/CSS. Keep JS unchanged when the browser contract already matches; use Bazel plus real Chromium for the integration gate.

The migration order is:

1. Freeze the browser contract and inventory every startup request, stream, timer, and warm-up.
2. Build the secure Go shell, dual launcher, capability layer, and ownership fences.
3. Deliver the terminal MVP with xterm.js and basic tmux session management.
4. Complete shared events, operations, and bounded work infrastructure.
5. Deliver Finder.
6. Deliver Differ.
7. Deliver Tabber.
8. Complete remaining terminal and session features.
9. Deliver YO!stats after redesigning its persistence and materialization paths.
10. Deliver YO!chat and YO!agent much later.

Do not proxy missing Go features to Python inside one request path. That would preserve two ownership models, make failures harder to attribute, and turn the migration into a permanent distributed system. A server process owns the selected mode from startup until shutdown.

## Goals

- Preserve browser behavior with little or no JavaScript change.
- Let the same tracked frontend run against Python and Go.
- Let users choose the complete Python backend or the smaller Go backend explicitly.
- Make unavailable functionality clear before the user opens a pane or invokes an action.
- Reduce steady CPU, memory, process count, and lifecycle failures.
- Use direct function calls inside the Go process instead of local RPC.
- Keep expensive work demand-driven, bounded, cancellable, coalesced, and observable.
- Prefer Go's standard library and a small set of maintained dependencies.
- Preserve Linux and macOS behavior.
- Keep rollback to Python simple until Go reaches feature parity.
- Measure performance from the first executable, not after the architecture is fixed.
- Make Linux and macOS builds reproducible through a checked-in Bazel module, pinned rules and toolchains, and hermetic test targets from the first contract harness.

## Non-Goals

- Do not redesign the JavaScript application during the backend migration.
- Do not rewrite xterm.js, Dockview, CodeMirror, ProseMirror, or the generated frontend build.
- Do not change endpoint names merely to make the Go API look more conventional.
- Do not copy the six Python daemons into six Go daemons.
- Do not introduce gRPC, Kubernetes-style service discovery, Kafka, Redis, or a general internal message bus.
- Do not replace tmux or Git with partial Go implementations.
- Do not migrate persistent schemas and backend language in the same step.
- Do not claim parity from matching successful responses alone; reconnects, cancellation, overload, malformed input, stale generations, and shutdown are part of the contract.
- Do not make Go the default until the implemented feature set is useful, measured, and explicit to the user.

## Current Constraints

YOLOmux exposes a large existing browser contract. The Python route registry currently includes JSON, JSON batch, binary, HTML, redirect, static, SSE, and WebSocket responses. The browser also depends on an embedded `yolomux-bootstrap` JSON object, authenticated static delivery, typed pending and unavailable responses, operation acknowledgements, and revision-aware event repair.

The terminal WebSocket contract is especially important:

- The browser opens `/ws?session=<session>&client=<browser-client-id>`.
- PTY output is sent as binary WebSocket messages.
- Browser control messages are JSON text or binary messages containing `input`, `resize`, `refresh`, or `tmux-scroll` operations.
- A refresh transaction can receive a text `refresh-ack` message.
- Read-only clients receive output but cannot send input, resize the terminal, or scroll tmux.
- The server creates a PTY and attaches a normal tmux client, which means tmux modes and terminal rendering are visible exactly as they are in a real terminal.

The initial Go terminal should preserve this PTY-backed design. Tmux control mode is useful for topology notifications and later process consolidation, but it does not emit all tmux-generated copy or choose mode display output. Switching the transport and the language in one milestone would obscure parity failures.

## Target Runtime Architecture

```text
Browser: unchanged JavaScript, CSS, xterm.js, Dockview
    |
    | HTTPS, JSON, binary HTTP, SSE, WebSocket
    v
One yolomux executable
    |
    +-- HTTP/auth/static/bootstrap module
    +-- capability catalog
    +-- terminal and tmux module
    +-- session state and event broker
    +-- Finder module
    +-- Differ module
    +-- Tabber module
    +-- settings and operational status
    +-- YO!stats module, added late
    +-- bounded worker supervisor
            |
            +-- optional isolated Git/index job
            +-- optional stats rebuild job
            +-- optional transcript backfill job
```

Most requests remain inside one process and use ordinary Go calls. Each module owns its state, cache, lifecycle, and storage. Modules publish small typed domain events to one bounded broker; HTTP and SSE adapters project those events into the existing browser protocol.

The worker supervisor provides containment for operations that can hang in an external command, consume a large amount of memory, or need a hard termination deadline. Normal code continues to use direct Go calls. Workers should normally be the same `yolomux-go` binary invoked with an internal subcommand, so release and version management still involve one artifact.

## Proposed Source Layout

```text
go.mod
MODULE.bazel
BUILD.bazel
cmd/yolomux/main.go
internal/app/                 startup, wiring, shutdown, backend identity
internal/config/              flags, environment, config decoding, validation
internal/httpapi/             route declarations, middleware, response envelopes
internal/auth/                login, cookies, roles, rate limits
internal/capabilities/        one authoritative feature and protocol catalog
internal/realtime/            SSE framing, event broker, revisions, backpressure
internal/terminal/            WebSocket, PTY, tmux attach, resize, scroll
internal/tmux/                exact command adapter, formats, topology snapshots
internal/session/             roster and session lifecycle
internal/finder/              path policy, listing, reading, writes, watches, search
internal/differ/              Git status, diff, history, blame, session files
internal/tabber/              activity and session projections
internal/systemstatus/        health and operational snapshots
internal/stats/               late-stage YO!stats implementation
internal/storage/<product>/   product-specific SQLite ownership and migrations
internal/work/                bounded jobs, coalescing, cancellation, supervision
internal/observability/       slog, metrics, traces, profiles, runtime snapshots
internal/contract/            shared HTTP and wire DTOs
web/                          existing generated frontend and vendor assets
testdata/contracts/           implementation-neutral request/response fixtures
tools/                         Bazel toolchains, repository rules, and build support
```

Packages communicate through concrete calls and narrow interfaces at external boundaries. Domain packages must not depend on HTTP, WebSocket, Prometheus, or OpenTelemetry packages. Avoid a generic repository layer, generic event-sourcing system, or dependency-injection framework. Constructors and explicit fields are enough.

## Proven Components

Use the standard library unless a dependency removes substantial protocol or platform code.

| Need | Choice | Reason |
| --- | --- | --- |
| HTTP and routing | Go 1.22+ `net/http` and `http.ServeMux` | Method-aware routes and wildcards cover the current route shapes without a framework. Use `chi/v5` only if route grouping proves materially clearer. |
| WebSocket | `github.com/coder/websocket` | Maintained, context-aware, zero dependencies, Autobahn-tested, supports binary messages, close handshakes, ping/pong, limits, and concurrent writes. |
| PTY | `github.com/creack/pty` | Established Unix PTY package with Linux and macOS support. Keep process-group, resize, EOF, and shutdown logic in YOLOmux tests rather than copying examples. |
| SSE | `net/http` plus a small local encoder | SSE framing is small. A library would not remove the revision, replay, repair, and backpressure logic that YOLOmux owns. |
| Git | Installed `git` through `os/exec.CommandContext` | Preserves worktree, credential, attributes, sparse-checkout, submodule, filter, and repository-format behavior. Use machine-readable porcelain and NUL delimiters. |
| Tmux | Installed `tmux` with explicit arguments and formats | Tmux remains the terminal owner. Use exact targets and stable IDs. Use control mode later for topology events where it improves measured behavior. |
| SQLite | `database/sql`; select `mattn/go-sqlite3` or `modernc.org/sqlite` through a measured release spike | Driver choice must prove FTS5, WAL, backup, busy handling, SQLite version, compile options, reproducible Linux/macOS builds, race tests, signing, and distribution behavior before Go owns a database. |
| Filesystem notifications | `github.com/fsnotify/fsnotify` | Established Linux and macOS notification layer. Treat notifications as hints and retain reconciliation because NFS and queue overflow can miss changes. |
| Schema migrations | Product-specific; consider `github.com/pressly/goose/v3` only after proving it matches that database's migration contract | Stats, search, chat, login throttling, and pricing have different ownership and rollback rules. Do not impose one generic migration mechanism. |
| Logging | `log/slog` | Structured text and JSON logging without another framework. |
| Metrics | `github.com/prometheus/client_golang` | Official Prometheus client. Use a private registry and bounded labels. |
| Profiling | `runtime/pprof`, `net/http/pprof`, execution trace | Standard Go CPU, heap, goroutine, mutex, block, and trace tooling. Expose only on a disabled-by-default loopback diagnostics listener. |
| Tracing | OpenTelemetry Go, deferred until cross-process traces have a concrete consumer | HTTP, Git, tmux, storage, and worker spans can later share trace IDs. Keep exporters optional and use `slog` for logs. |
| Testing | `testing`, `httptest`, fuzzing, `go test -race` | Avoid a mock framework. Use temporary Git repositories, private tmux sockets, temporary SQLite files, and real HTTP/WebSocket servers. |
| Static assets | `embed`, `io/fs`, and `http.FileServer` | One binary can contain the existing generated frontend. Development mode can serve the worktree assets from disk. |

Pin every dependency in `go.mod` and the corresponding Bazel dependency declarations. Record the reason for each non-standard dependency. Review maintenance, license, supported Go versions, release activity, transitive dependencies, and security reports before adoption. Bazel must use pinned, reviewable external repositories and a pinned Go toolchain; network access is not required during an approved offline build or test.

### Build And Test From Phase 0

Phase 0 establishes Bazel before any Go production implementation:

- Add `MODULE.bazel`, root and package `BUILD.bazel` files, and the pinned Go rules/toolchain needed to build and test a minimal contract harness.
- Define stable targets for the Go binary, unit tests, black-box contract tests, generated contract fixtures, lint/format checks, and Linux/macOS release artifacts. Use target tags or explicit suites for tests that require tmux, Chromium, network sockets, or platform-specific facilities.
- Make `bazel build //...` and `bazel test //...` the canonical Go commands. Developer convenience commands may wrap Bazel but must not create a second dependency or test graph.
- Pin Bazel, rules_go, gazelle or equivalent generation tooling if adopted, the Go SDK/toolchain, and all fetched archives with integrity checks. Record supported host and target platforms in the repository.
- Prove a clean checkout can build and run the Phase 0 contract harness without undeclared local files, ambient `GOPATH` state, or an unpinned network dependency. Capture action-cache and external-download behavior in the baseline.
- Run the same Bazel targets in CI on Linux and macOS where available, including race-enabled Go tests and the contract harness against Python. Do not postpone reproducible builds, dependency review, or hermeticity until the secure shell phase.
- Generate or validate route, bootstrap, capability, and wire fixtures through declared Bazel actions so a changed source manifest cannot silently bypass contract review.

The initial Bazel graph may contain a deliberately small Go harness rather than the complete server. Its purpose is to make the build boundary, dependency policy, platform matrix, and test entry points real before Phase 1 adds runtime code.

## Backend Selection

The operator must select one backend before binding a port or opening writable state:

```text
./yolomux --backend=python ...
./yolomux --backend=go ...
./yolomux --backend=auto ...
```

Equivalent environment configuration may use `YOLOMUX_BACKEND=python|go|auto`. Use a distinct launcher entry point rather than making `yolomux.py --backend=python` execute itself recursively. The launcher should `exec` either an internal `python-server` entry point or the Go executable so there is only one long-lived server process.

Backend behavior:

- `python`: start the current backend with the complete feature set.
- `go`: require a compatible Go binary and start only the Go backend.
- `auto`: initially select Python unless the operator explicitly opts into a policy that prefers Go. Do not silently fall back after Go has opened state or accepted traffic.

Both modes must use the same address, TLS policy, URL, static bundle, state-directory rules, auth configuration, and browser contracts. The selected backend must be visible in About and diagnostics, but JavaScript behavior must depend on capabilities rather than a `python` or `go` string.

The launcher must print the selected backend, executable revision, state path, bind address, and enabled product surfaces without exposing secrets. It must forward signals, preserve exit status, and never leave the selected child running after the launcher exits.

Both direct server entry points and the launcher must honor one language-neutral ownership protocol. Specify the exact product-root lock and port lock paths, `flock` behavior, owner-record schema, file modes, host identity, boot identity, process birth identity, stale-record rules, and acquisition order. Acquire ownership before creating certificates, migrating or opening writable databases, creating caches or sockets, or launching background workers. A concurrency gate must start Python and Go against the same root and prove that exactly one reaches writable initialization.

## Capability Negotiation

The Go backend will not initially implement every pane. The server must tell the browser what exists before the browser restores layout or starts background requests.

Add a versioned capability document to the existing `yolomux-bootstrap` JSON. Capabilities must be operation-level because each pane is delivered incrementally:

```json
{
  "contract": {
    "version": 1,
    "backendBuild": "opaque-revision"
  },
  "capabilities": {
    "terminal.websocket.v1": {
      "available": true,
      "binaryOutput": true,
      "refreshAck": true,
      "resize": true,
      "tmuxScroll": false,
      "reason": ""
    },
    "session.create.v1": {
      "available": true
    },
    "session.kill.v1": {
      "available": true
    },
    "session.rename.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.sessionRenameUnavailable", "messageParams": {}}
    },
    "pane.finder.v1": {
      "available": false
    },
    "finder.read.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.finderUnavailable", "messageParams": {}}
    },
    "finder.write.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.finderWriteUnavailable", "messageParams": {}}
    },
    "pane.differ.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.differUnavailable", "messageParams": {}}
    },
    "pane.tabber.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.tabberUnavailable", "messageParams": {}}
    },
    "pane.stats.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.statsUnavailable", "messageParams": {}}
    },
    "pane.chat.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.chatUnavailable", "messageParams": {}}
    },
    "pane.agent.v1": {
      "available": false,
      "reason": {"code": "not_implemented", "messageKey": "capability.agentUnavailable", "messageParams": {}}
    }
  }
}
```

Capability rules:

- Names describe observable behavior, not implementation language.
- One Go catalog owns route registration, bootstrap declarations, command visibility, event demand, limits, health rows, and tests. A route cannot be enabled without its owning capability.
- Protocol support, runtime health, and user permission are separate. A feature can be implemented, temporarily degraded, or forbidden to a read-only user without collapsing those states.
- The capability snapshot carries an epoch and monotonic revision. A `capabilities_changed` client event invalidates the browser snapshot, and revision gaps repair through an authenticated `/api/capabilities` read. Controls update without a page reload when tmux disappears, a watch budget is exhausted, a worker fails, or the dependency recovers.
- Omitted capabilities use a conservative default. For new pane capabilities, omission means unavailable until the Python backend also emits the catalog.
- Unknown capabilities are ignored by older clients.
- Protocol families evolve independently. A Finder change must not bump the terminal protocol.
- Dynamic dependencies can change a capability from available to degraded, but a backend cannot claim a surface it does not implement.
- The backend returns a typed `feature_unavailable` error if a stale browser calls a disabled route. A missing handler must never appear as an unexplained 404.
- Reasons use stable codes and locale message keys rather than server-authored English text.
- Pane-level capabilities control whether a pane exists. Operation-level capabilities independently control create, rename, kill, read, write, search, index, diff, history, blame, preview, upload, and every other partially delivered action.

### Browser Presentation

One shared JavaScript capability layer should control all feature presentation and transport admission. This is the expected migration-related JavaScript change; product modules should not add backend-specific branches.

- Remove unavailable panes from new-layout menus and keyboard commands.
- Preserve disabled panes found in a saved layout as non-interactive placeholders instead of deleting user layout state.
- Render the placeholder title, disabled styling, reason, and a direct instruction to restart with the Python backend for the complete feature set.
- Disable toolbar actions whose individual capability is absent and show the server-provided reason in the existing tooltip/status system.
- Do not start polling, SSE channel demand, warm-up, indexing, or repair reads for unavailable features.
- Gate the existing unconditional boot requests, background-owner checks, auto-approve refresh, transcripts, terminal status, watched pull requests, update status, diagnostics, `/api/client-events`, reconnect work, timers, and visibility-driven refreshes before the first Go preview.
- Apply revisioned capability changes at runtime, disable newly unavailable controls, stop their demand and timers, preserve saved layout placeholders, and re-enable recovered operations without reconstructing unrelated panes.
- Do not show empty boxes that look broken.
- Do not invent capabilities by probing endpoints.

Hiding a feature is appropriate when it has never appeared in the current layout. Graying out a saved pane is appropriate when removing it would alter the user's layout unexpectedly. This gives new Go users a clean minimal UI and existing users an explanation instead of disappearing state.

The Python backend should emit capabilities matching actual current support, configuration, dependency health, and policy before the capability-aware JavaScript lands. Features that are intentionally disabled or degraded remain disabled or degraded. This makes the frontend change independently deployable and proves that capability gating preserves the current Python experience rather than broadening it.

Phase 0 must define the minimum bootstrap object by recording every field the current JavaScript reads, including fields that are present only to disable a feature. Browser network tests must prove unsupported routes receive no traffic during initial boot, WebSocket reconnect, SSE reconnect, visibility changes, saved-layout restoration, manual refresh, and settings changes.

## Compatibility Contract

“Compatible with JavaScript” means the same generated bundle can run against either backend. For every enabled capability, Go must match:

- HTTP method and path.
- Query and body parsing.
- Authentication and role behavior.
- Status code and headers.
- JSON key names, types, nullability, ordering where observed, and typed error shapes.
- Binary response bytes and cache behavior.
- SSE event names, IDs, heartbeats, epochs, revisions, replay, repair, and reconnect behavior.
- WebSocket URL, binary versus text messages, control messages, limits, ping/pong, close behavior, and reconnect behavior.
- Bootstrap field names, escaping, defaults, and script ordering.
- Operation IDs, pending states, acknowledgements, cancellation, and last-known-good behavior.
- Persistent file and SQLite formats while both backends support the same state.

Do not maintain these contracts by hand in two unrelated lists. Export a machine-readable contract manifest from the Python route/capability owners and consume checked-in normalized fixtures from Go tests. Longer term, one neutral schema can generate DTO validation for both implementations, but code generation is not required for the MVP.

## Observability From Day One

Every phase must add measurements before adding expensive behavior. The first Go server needs enough instrumentation to reject architecture guesses with evidence.

### Required Logs

Use `slog` with these stable fields where applicable:

- `component`
- `operation`
- `request_id`
- `trace_id`
- `backend_build`
- `duration_ms`
- `queue_wait_ms`
- `result`
- `reason_code`
- `bytes_in`
- `bytes_out`
- `tmux_socket`
- stable session, window, and pane IDs
- worker role and generation

Do not log terminal content, request bodies, credentials, cookies, environment values, complete remote URLs, or arbitrary filenames by default.

### Required Metrics

- HTTP requests, in-flight requests, duration, response bytes, and status class by bounded route template.
- WebSocket connections, input/output bytes, queue depth, dropped/resynchronized output, and close reason.
- SSE subscribers, queue depth, events, repair markers, replay counts, and slow-client disconnects.
- Goroutine count, heap, allocation rate, garbage-collection pause, open descriptors, and process CPU.
- Tmux command duration, timeout, output bytes, and result by bounded operation name.
- Git command duration, timeout, output bytes, and result by bounded operation name.
- Filesystem scan entries, duration, cancellation, overflow, and reconciliation count.
- SQLite transaction duration, busy count, rows, checkpoint time, and database size.
- Worker queue depth, queue wait, execution time, cancellation, timeout, restart, and last-known-good age.

Never use paths, session names, branch names, commit hashes, request IDs, or error strings as Prometheus labels. Put those details in sampled structured logs or traces.

### Profiles and Traces

- Support CPU, heap, goroutine, mutex, block, and execution-trace captures.
- Put `pprof` on a separate loopback-only listener, disabled by default.
- Add labels around terminal copy loops, Finder scans, Git commands, diff generation, Tabber projection, and stats materialization.
- Start with request IDs and local timing scopes at HTTP, WebSocket lifecycle, Git, tmux, SQLite, and worker boundaries. Add OpenTelemetry spans only when cross-process traces have a configured consumer.
- Keep `/metrics`, pprof, and performance diagnostics disabled by default or authenticated and loopback-only. Bound response size and request rate, and redact paths, process arguments, and user content.
- Add a low-overhead rolling operation-duration summary to the authenticated `/api/diagnostics/performance` only after the basic metrics prove insufficient for first diagnosis.

### Performance Gates

Each phase records Python and Go on the same fixtures and machine for:

- Cold startup time.
- Settled idle CPU and memory.
- Request latency distribution.
- CPU time per operation.
- Allocated bytes per operation.
- Peak memory.
- External command count and duration.
- Bytes copied and serialized.
- Shutdown time and surviving child count.

Do not accept a migration that lowers request latency by starting more uncontrolled background work. Total process-tree CPU, memory, I/O, and child activity are the result.

## Delivery Phases

Each phase ends in an independently usable Go mode. A feature is enabled only after its browser journey, failure cases, race tests, and performance gates pass.

### API Delivery Inventory

The current Python registry contains 128 routes: 78 GET and 50 POST. The inventory below assigns every registered route to exactly one Go delivery phase. Phase 0 freezes all 128 contracts but owns no runtime implementation. Phase 10 designs YO!stats storage but owns no production route. Every deferred route remains required for eventual Go parity; a later phase means “scheduled later,” not “optional” or “removed.” New migration-only routes such as `/api/capabilities` must be added to this inventory and the shared route manifest when introduced.

| Phase | Runtime routes delivered | Priority |
| --- | ---: | --- |
| Phase 0: contract | 0 | Defines fixtures for all 128 routes before implementation. |
| Phase 1: secure shell | 11 | Required to serve any browser safely. |
| Phase 2: terminal MVP | 6 | First useful Go product. |
| Phase 3: shared events and operations | 9 | Required before stateful panes. |
| Phase 4: Finder read | 6 | First repository-adjacent pane. |
| Phase 5: Finder write and search | 16 | Completes Finder mutations/search. |
| Phase 6: TextEdit | 0 new routes | Ports source-file editing as a parallel editor workstream. |
| Phase 7: ViewEdit | 0 new routes | Ports Markdown ViewEdit/ProseMirror as a parallel editor workstream. |
| Phase 8: Differ | 6 | Adds Git-backed file change workflows. |
| Phase 9: Tabber | 9 | Reuses terminal, Finder, editors, and Differ products. |
| Phase 10: infrastructure hardening | 0 new routes | Hardens routes delivered in Phase 3 under combined demand. |
| Phase 11: remaining terminal and operations | 28 | Completes the primary non-stats workflow. |
| Phase 12: YO!stats design | 0 | Resolves persistence and materialization before route cutover. |
| Phase 13: YO!stats read | 7 | Enables charts and operational readiness from committed data. |
| Phase 14: YO!stats write | 2 | Moves telemetry and pricing writes after ownership fencing. |
| Phase 15: YO!chat | 9 | Deferred, but required for parity. |
| Phase 16: YO!agent | 19 | Last scheduled surface, but required for parity. |
| **Total** | **128** | **Every current registered route has one owner.** |

Registry cross-check:

| Registry group | GET | POST | Total |
| --- | ---: | ---: | ---: |
| `core` | 43 | 21 | 64 |
| `filesystem` | 12 | 7 | 19 |
| `yoagent` | 4 | 14 | 18 |
| `tmux` | 9 | 4 | 13 |
| `chat` | 5 | 4 | 9 |
| `batch` | 5 | 0 | 5 |
| **Total** | **78** | **50** | **128** |

#### Phase 1 Routes: Secure Shell

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/static/*` | static | public |
| GET | `/api/auth-setup` | JSON | public |
| GET | `/login` | HTML | public |
| GET | `/logout` | redirect | public |
| GET | `/healthz` | JSON | public |
| GET | `/livez` | JSON | public |
| GET | `/api/ping` | JSON | readonly |
| GET | `/` | HTML | readonly |
| GET | `/preview-popout` | HTML | readonly |
| GET | `/pane-popout` | HTML | readonly |
| POST | `/login` | redirect | public |

#### Phase 2 Routes: Terminal MVP

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/tmux-session-exists` | JSON | readonly |
| GET | `/api/create-session-plan` | JSON | admin |
| POST | `/api/create-session` | JSON | admin |
| POST | `/api/kill-session` | JSON | admin |
| GET | `/api/tmux` | JSON | readonly |
| GET | `/ws` | WebSocket | readonly |

#### Phase 3 Routes: Shared Events and Operations

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/client-events` | SSE | readonly |
| GET | `/api/operations/*` | JSON | readonly |
| POST | `/api/operations/ack` | JSON | readonly |
| GET | `/api/background/status` | JSON | readonly |
| POST | `/api/background/claim` | JSON | admin |
| POST | `/api/event` | JSON | readonly |
| GET | `/api/system-status` | JSON | readonly |
| GET | `/api/system-status/advanced` | JSON | readonly |
| GET | `/api/logs` | JSON | readonly |

#### Shared Browser-Boot API Ownership

The existing shared JavaScript starts more than the Phase 2 terminal WebSocket. These requests are
not all Phase 3 work, and a Go server must not claim feature parity by returning synthetic success
payloads for a later phase. Each route has one owning phase, while Phase 3 owns the event/operation
transport used by the owner:

| API family | Owning phase | Consumer or purpose |
| --- | --- | --- |
| `/api/client-events`, `/api/operations/*`, `/api/operations/ack`, `/api/background/status`, `/api/background/claim`, `/api/event`, `/api/system-status`, `/api/system-status/advanced`, `/api/logs` | Phase 3 | Shared event stream, operation delivery, background ownership, and core diagnostics. |
| `/api/fs/index-status`, `/api/fs/search`, `/api/fs/search-stream`, `/api/fs/watch-diff`, `/api/fs/batch`, `/api/fs/resolve-file-candidates`, `/api/fs/write`, `/api/fs/delete`, `/api/fs/unindex`, `/api/fs/rename`, `/api/fs/mkdir`, `/api/batch/*`, `/api/upload` | Phase 5 | Finder search, writes, uploads, batch work, and index status. |
| `/api/session-files`, `/api/session-files-batch`, `/api/fs/diff`, `/api/fs/git-history`, `/api/fs/git-commit`, `/api/batch/blame` | Phase 8 | Differ and Git-backed session-file projections. |
| `/api/session-metadata`, `/api/transcripts`, `/api/activity-summary`, `/api/events`, `/api/search`, `/api/run-history`, `/api/activity`, `/api/summary`, `/api/tmux-signals` | Phase 9 | Tabber activity, transcript, attention, and session projections. |
| `/api/update-status`, `/api/auto-approve`, `/api/notify`, `/api/settings`, `/api/watched-prs`, `/api/yolo-rules`, `/api/tmux-status`, `/api/transcript`, `/api/context*`, `/api/tmux-next`, `/api/tmux-window`, `/api/tmux-copy-selection`, `/api/drop-action/run`, `/api/attention-ack` | Phase 11 | Remaining terminal operations, settings, context, drop actions, and cross-surface state. |
| `/readyz`, `/api/stats-capabilities`, `/api/stats-delta`, `/api/stats-snapshot`, `/api/stats-stream`, `/api/stats-retry`, `/api/pricing-catalog` | Phase 13 | YO!stats read/materialization and readiness. |
| `/api/stats-observations`, `/api/pricing-catalog/refresh` | Phase 14 | YO!stats writes and pricing refresh. |
| `/api/chat/*` | Phase 15 | YO!chat persistence, paging, send, typing, and read state. |

Phase 2 may expose only the terminal capability and should explicitly report unavailable later
capabilities. Phase 3 may provide the shared event/operation substrate and core diagnostics, but it
must not silently implement Finder, Tabber, settings, stats, or chat behavior merely because the
shared JS probes those routes during boot. Until an owning phase lands, the capability catalog must
mark the surface unavailable and the route must return the Python-shaped unavailable response.

This split allows parallel work after the Phase 3 contract is frozen: Phase 4/5 Finder, Phase 8
Differ, Phase 9 Tabber, Phase 11 terminal operations, and Phase 13-15 product surfaces each own
their route family and publish changes through the one Phase 3 event broker and operation ledger.

#### Phase 4 Routes: Finder Read

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| POST | `/api/watch/roots` | JSON | admin |
| GET | `/api/fs/fast/list` | JSON | readonly |
| GET | `/api/fs/list` | JSON | readonly |
| GET | `/api/fs/read` | JSON | readonly |
| GET | `/api/fs/info` | JSON | readonly |
| GET | `/api/fs/raw` | binary | readonly |

`/api/watch/roots` mutates watch demand, but Phase 4 owns it because Finder reads require a bounded invalidation and reconciliation lifecycle.

#### Phase 5 Routes: Finder Write and Search

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/fs/search` | JSON | readonly |
| GET | `/api/fs/search-stream` | SSE | readonly |
| GET | `/api/fs/index-status` | JSON | readonly |
| GET | `/api/fs/watch-diff` | JSON | readonly |
| POST | `/api/fs/batch` | JSON batch | admin |
| POST | `/api/fs/resolve-file-candidates` | JSON | readonly |
| POST | `/api/fs/write` | JSON | admin |
| POST | `/api/fs/delete` | JSON | admin |
| POST | `/api/fs/unindex` | JSON | admin |
| POST | `/api/fs/rename` | JSON | admin |
| POST | `/api/fs/mkdir` | JSON | admin |
| GET | `/api/batch/search` | JSON | readonly |
| GET | `/api/batch/zip` | binary | admin |
| GET | `/api/batch/count` | JSON | admin |
| GET | `/api/batch/html-preview` | binary | admin |
| POST | `/api/upload` | JSON | admin |

`/api/upload` serves both editor and terminal uploads. Phase 5 implements one shared upload owner for Finder editing; Phase 9 adds terminal-specific parity through that owner without creating a second route implementation.

#### Phase 8 Routes: Differ

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/session-files-batch` | JSON | readonly |
| GET | `/api/session-files` | JSON | readonly |
| GET | `/api/fs/diff` | JSON | readonly |
| GET | `/api/fs/git-history` | JSON | readonly |
| GET | `/api/fs/git-commit` | JSON | readonly |
| GET | `/api/batch/blame` | JSON | admin |

The session-file routes are shared products. Differ owns their repository and file computation; Tabber consumes the resulting projections in Phase 9.

#### Phase 6 Routes: TextEdit

TextEdit is a browser/editor workstream with no new backend routes. It consumes the Phase 5 file read/write, conflict, upload, and watch contracts and owns the source-file editor journey and CodeMirror configuration.

#### Phase 7 Routes: ViewEdit

ViewEdit is a browser/editor workstream with no new backend routes. It consumes the Phase 5 Markdown/file contracts and owns the ProseMirror parse/edit/serialize round trip, preview fallback behavior, and Markdown-specific browser tests. It is independently mergeable from TextEdit.

#### Phase 9 Routes: Tabber

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/session-metadata` | JSON | readonly |
| GET | `/api/transcripts` | JSON | readonly |
| GET | `/api/activity-summary` | JSON | readonly |
| GET | `/api/events` | JSON | readonly |
| GET | `/api/search` | JSON | readonly |
| GET | `/api/run-history` | JSON | readonly |
| GET | `/api/activity` | JSON | readonly |
| GET | `/api/summary` | JSON | readonly |
| GET | `/api/tmux-signals` | JSON | readonly |

These names overlap other surfaces: `/api/transcripts` is a flattened session-metadata compatibility view, `/api/search` searches event and session history rather than Finder paths or Chat messages, and `/api/tmux-signals` supplies status and attention classification rather than terminal bytes.

#### Phase 11 Routes: Remaining Terminal and Operations

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/update-status` | JSON | admin |
| GET | `/api/dev-reload` | SSE | readonly |
| GET | `/api/diagnostics/performance` | JSON | admin |
| GET | `/api/auto-approve` | JSON | readonly |
| GET | `/api/notify` | JSON | readonly |
| GET | `/api/settings` | JSON | readonly |
| GET | `/api/watched-prs` | JSON | readonly |
| GET | `/api/yolo-rules` | JSON | readonly |
| POST | `/api/self-update` | JSON | admin |
| POST | `/api/ensure-session` | JSON | admin |
| POST | `/api/rename-session` | JSON | admin |
| POST | `/api/auto-approve` | JSON | admin |
| POST | `/api/attention-ack` | JSON | admin |
| POST | `/api/notify` | JSON | admin |
| POST | `/api/settings` | JSON | admin |
| POST | `/api/drop-action/run` | JSON | admin |
| POST | `/api/yolo-rules/reload` | JSON | admin |
| POST | `/api/yolo-rules/open` | JSON | admin |
| GET | `/api/tmux-status` | JSON | readonly |
| GET | `/api/transcript` | JSON | readonly |
| GET | `/api/context` | JSON | readonly |
| GET | `/api/context-items` | JSON | readonly |
| GET | `/api/context-stream` | SSE | readonly |
| GET | `/api/summary-stream` | SSE | admin |
| POST | `/api/tmux-next` | JSON | admin |
| POST | `/api/tmux-status` | JSON | admin |
| POST | `/api/tmux-window` | JSON | admin |
| POST | `/api/tmux-copy-selection` | JSON | admin |

This phase completes cross-surface settings and operations. `/api/settings` affects all existing and future panes, `/api/drop-action/run` joins files to terminals, `/api/attention-ack` changes Tabber and terminal state, and the context/transcript routes depend on terminal and agent-client interpretation even though YO!agent itself remains deferred. Phase 2 already owns `/api/create-session-plan` because the current browser requires a plan and generation before it posts `/api/create-session`.

#### Phase 13 Routes: YO!stats Read

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/readyz` | JSON | readonly |
| GET | `/api/stats-capabilities` | JSON | readonly |
| GET | `/api/stats-delta` | JSON | readonly |
| GET | `/api/stats-snapshot` | JSON | readonly |
| GET | `/api/stats-stream` | SSE | readonly |
| POST | `/api/stats-retry` | JSON | readonly |
| GET | `/api/pricing-catalog` | JSON | readonly |

`/readyz` remains in this late phase because the current route reports YO!stats storage and materialization readiness rather than only HTTP-listener readiness. `/healthz` and `/livez` already provide early shell health. `/api/stats-retry` is a POST, but it retries read/materialization availability and does not ingest observations.

#### Phase 14 Routes: YO!stats Write

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| POST | `/api/stats-observations` | JSON | readonly |
| POST | `/api/pricing-catalog/refresh` | JSON | admin |

The readonly role on `/api/stats-observations` is intentional: authenticated read-only browsers may submit bounded telemetry even though Go's stats owner performs a storage mutation. Capability, authorization, and storage mutability remain separate decisions.

#### Phase 15 Routes: YO!chat

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/chat/bootstrap` | JSON | readonly |
| GET | `/api/chat/page` | JSON | readonly |
| GET | `/api/chat/delta` | JSON | readonly |
| GET | `/api/chat/context` | JSON | readonly |
| GET | `/api/chat/search` | JSON | readonly |
| POST | `/api/chat/send` | JSON | readonly |
| POST | `/api/chat/yoagent` | JSON | readonly |
| POST | `/api/chat/typing` | JSON | readonly |
| POST | `/api/chat/read` | JSON | readonly |

`/api/chat/yoagent` belongs to Chat because Chat owns the browser message and persistence contract. Its action remains unavailable until the corresponding YO!agent capability is healthy.

#### Phase 14 Routes: YO!agent

| Method | Path | Protocol | Role |
| --- | --- | --- | --- |
| GET | `/api/agent-auth` | JSON | readonly |
| GET | `/api/yoagent/skills` | JSON | admin |
| GET | `/api/yoagent/skill-files` | JSON | admin |
| GET | `/api/yoagent/conversation` | JSON | admin |
| GET | `/api/yoagent/jobs` | JSON | admin |
| POST | `/api/yoagent/chat` | JSON | readonly |
| POST | `/api/yoagent/chat/*/cancel` | JSON | admin |
| POST | `/api/yoagent/actions/preview-send` | JSON | admin |
| POST | `/api/yoagent/actions/execute-send` | JSON | admin |
| POST | `/api/yoagent/intent` | JSON | admin |
| POST | `/api/yoagent/jobs` | JSON | admin |
| POST | `/api/yoagent/jobs/cancel-session` | JSON | admin |
| POST | `/api/yoagent/jobs/*/confirm` | JSON | admin |
| POST | `/api/yoagent/jobs/*/cancel` | JSON | admin |
| POST | `/api/yoagent/waits/*/clear` | JSON | admin |
| POST | `/api/yoagent/skill-files/upsert` | JSON | admin |
| POST | `/api/yoagent/skill-files/delete` | JSON | admin |
| POST | `/api/yoagent/prewarm` | JSON | admin |
| POST | `/api/yoagent/reset` | JSON | admin |

`/api/agent-auth` is registered in the core group but belongs to YO!agent delivery because it reports managed agent integration state. `/api/yoagent/chat` remains readonly-compatible while cancellation, persisted jobs, sends, skills, and lifecycle operations require admin; Go must preserve that existing role split.

### Phase 0: Freeze the Browser Contract

Deliverables:

- Record the current bootstrap JSON schema and safe script escaping behavior.
- Inventory every browser startup fetch, SSE stream, WebSocket, timer, warm-up, reconnect path, visibility refresh, and saved-layout side effect.
- Define the complete minimum bootstrap object the existing bundle reads in terminal-only mode.
- Export a normalized route manifest from the Python route registry.
- Add golden fixtures for successful, invalid, unauthenticated, forbidden, unavailable, pending, and internal-error responses.
- Rely on the selected WebSocket library's conformance suite for generic frame behavior and keep a small Autobahn smoke. Add YOLOmux fixtures for binary terminal output, text control messages, resize authority, refresh acknowledgement ordering, permissions, backpressure, reconnect, limits, and malformed application messages.
- Add SSE fixtures for initial ready, heartbeat, replay, resource revision gap, epoch change, overflow repair, and reconnect.
- Identify nondeterministic fields such as timestamps, PIDs, epochs, ports, and build revisions and normalize only those fields.
- Make a black-box harness start either backend and run the same contract cases.
- Capture baseline Python CPU, memory, startup, request, WebSocket, Git, tmux, and process-tree measurements.
- Establish the Bazel build boundary: pin Bazel, Go, rules, toolchains, and external repositories; add the minimal Go contract-harness targets; and document the supported `bazel build`/`bazel test` invocations and platform matrix.
- Make route, bootstrap, capability, WebSocket, and SSE fixtures declared Bazel inputs and ensure fixture changes are visible in the build/test graph.

Exit criteria:

- The contract harness passes against the Python backend.
- A route or payload change cannot silently bypass fixture review.
- Baseline reports include real exit codes and measured process-tree totals.
- A clean checkout passes the Phase 0 Bazel build and contract test targets without undeclared local state, and the same target definitions run on supported Linux and macOS builders.
- Dependency and toolchain versions are pinned with integrity checks, and an offline rebuild/test using the approved repository cache is reproducible.
- Browser network assertions prove the capability layer suppresses unsupported work during boot, reconnect, visibility changes, layout restoration, and manual refresh.
- Capability transition tests cover available to degraded to recovered, event loss and HTTP repair, stale revisions, and an intentionally disabled Python feature.

### Phase 1: Secure Go Skeleton and Dual Launcher

Deliverables:

- Add the versioned Go executable and complete the Bazel source layout and targets established in Phase 0; retain `go.mod` only as dependency metadata required by the selected Go/Bazel integration.
- Add backend selection to the compatibility launcher.
- Implement validated flags and environment configuration using the existing names where possible.
- Implement the shared cross-language product-root and port ownership protocol before any writable initialization.
- Implement startup ownership, one-instance/port checks, signal forwarding, graceful shutdown, and build/version reporting.
- Implement setup mode, current password-hash verification, signed cookies, admin/read-only roles, login throttling, mutating-request protection, and WebSocket origin validation.
- Implement the current TLS defaults, supplied certificates, self-signed certificates, plaintext opt-in, and HTTP-to-HTTPS behavior before allowing non-loopback binding.
- Serve `/healthz`, `/livez`, `/api/ping`, `/`, and allowlisted static assets.
- Embed the generated frontend for release builds and support worktree assets in development mode.
- Add `slog`, core bounded metrics, optional loopback pprof, request IDs, panic recovery at the HTTP request boundary, and process runtime metrics. Defer OpenTelemetry and broad diagnostic APIs.
- Implement the capability catalog with only shell and diagnostic capabilities enabled.
- Keep Python as the default backend.

Exit criteria:

- `--backend=python` behaves as before.
- `--backend=go` serves the same page and static representation headers.
- The browser does not issue requests for disabled panes.
- Concurrent Python and Go startup against one root allows exactly one process to reach writable initialization.
- The server refuses non-loopback binding until authentication, origin validation, and TLS policy are active.
- TERM and interrupt stop the server within a bounded deadline with no surviving owned processes.
- Idle CPU and memory are recorded, not inferred.

### Phase 2: Terminal MVP

This is the first useful Go release.

MVP functionality:

- List existing tmux sessions.
- Render session tabs and open an xterm.js terminal.
- Attach `/ws` to a selected tmux session through a PTY.
- Forward tmux output as binary WebSocket messages.
- Accept terminal input and resize messages.
- Support WebSocket ping/pong, bounded message sizes, origin checks, authentication, and clean close.
- Create a basic shell session.
- Switch between sessions.
- Kill a session with confirmation.
- Reconcile the session roster after create, rename, exit, and external tmux changes.
- Show a clear degraded state if tmux is missing, too old, or unreachable.

Implementation:

- Use `coder/websocket` and `creack/pty`.
- Launch `tmux attach-session` with explicit arguments, `TERM=xterm-256color`, a new owned process group, and an exact session target.
- Begin with direct bounded writes and write deadlines so terminal output does not accumulate behind a slow browser. Add an outbound queue only if measurements prove it is needed.
- If output can be dropped or a slow client must be disconnected, first add an explicit terminal reset/resynchronization contract. The browser currently retains its xterm instance and scrollback across reconnect, so reconnect alone cannot prove a valid screen.
- Apply resize through the PTY and signal the verified attached process group.
- Use one tmux adapter for all commands. It accepts argument vectors, context deadlines, output limits, a terminal-specific environment policy, and exact targets. Preserve required locale, tmux socket, HOME, SSH agent, and user session values while stripping YOLOmux internals that must not leak.
- Begin with periodic roster reconciliation plus event-triggered refresh. Add a supervised tmux control-mode observer only after fixtures prove its parser and measured subprocess reduction.

Enabled capabilities:

- `terminal.websocket.v1`
- `session.roster.v1`
- `session.create.v1`
- `session.kill.v1`

Exit criteria:

- The current JavaScript xterm opens without a backend-specific terminal branch.
- Binary output, Unicode, ANSI control sequences, resize, reconnect, read-only mode, and browser refresh pass against real tmux.
- Copy mode and alternate-screen applications behave like the Python PTY implementation.
- Repeated connect/disconnect leaves no PTY descriptors or tmux client processes.
- A terminal flood remains memory-bounded and does not block `/healthz`; overflow behavior is tested in a shell, full-screen TUI, alternate screen, copy mode, and retained scrollback.
- Go race tests and WebSocket fuzz tests pass.

### Phase 3: Production Shell and Shared Event Substrate

Deliverables:

- Complete Basic authentication, logout behavior, port-scoped cookie details, current configuration compatibility, and security edge cases not required by the loopback MVP.
- Implement unread-body handling, body limits, request deadlines, typed errors, and response header parity.
- Add About, version, server logs, and basic System status. The admin-only `/api/update-status` route remains in Phase 9 with self-update.
- Preserve existing locale/bootstrap behavior needed by the minimal shell.
- Implement the complete `/api/client-events` substrate before Finder: demand channels, process epoch, resource revisions, retained latest values, overflow repair, operation replay, acknowledgements, heartbeats, demand replacement, and reconnect fences.
- Implement bounded operation lookup, acknowledgement, coalescing, cancellation, last-known-good products, and separate queue-wait/execution measurements.
- Add startup and shutdown soak tests on Linux and macOS.

Exit criteria:

- Existing credentials work in both backends.
- Switching backend does not invalidate unrelated browser state.
- Read-only users can watch terminals but cannot mutate sessions or send terminal input.
- Authentication and TLS black-box tests pass against both backends.
- Deliberate event loss, queue overflow, restart, and epoch replacement recover through the existing JavaScript repair path.

### Phase 4: Finder Read-Only Core

Start Finder with direct, bounded filesystem operations. Do not begin with a persistent index.

Deliverables:

- Enable the existing Finder pane only when its complete read-only journey works.
- Implement filesystem root policy, canonicalization, no-follow descriptor checks, and fail-closed authorization.
- Implement `/api/fs/fast/list`, `/api/fs/list`, `/api/fs/read`, `/api/fs/info`, and `/api/fs/raw` with current envelopes and limits.
- Support text, binary, too-large, image, Markdown preview, and source-editor states expected by the current JavaScript.
- Preserve cache headers, range/representation behavior, and content types.
- Add bounded directory listing with cancellation and partial/unavailable reasons.
- Add `fsnotify` invalidation for local files and periodic bounded reconciliation for missed events and unsupported filesystems.
- Make watches demand-leased and bounded globally and per root. Define recursive registration, new-directory admission, dormant-watch eviction, overflow-to-root invalidation, unsupported-filesystem behavior, and explicit degradation when inotify or descriptor limits are exhausted.
- Publish file-change events through the shared revision-aware event broker.

Architecture:

- One `finder.Service` owns path authorization, listing cache, watch registration, and read operations.
- Filesystem events invalidate state; they are not authoritative state.
- Do not put directory traversal on the HTTP goroutine after the bounded fast-list budget expires. Return the existing pending operation shape and run the work through the bounded work scheduler.
- Keep expensive preview generation disabled until its route is implemented and measured.

Exit criteria:

- Finder can navigate a large source tree, open representative files, and recover from external edits.
- Symlink, parent-replacement, concurrent namespace, permission, and root-boundary tests match Python behavior.
- Slow NFS or an unreadable directory cannot block terminal traffic or exhaust goroutines.
- Inotify exhaustion, macOS descriptor pressure, queue overflow, NFS fallback, directory replacement, multiple browsers, and layout close/reopen have explicit test coverage.
- The browser performs no unavailable Finder calls.

### Phase 5: Finder Mutations and Search

Deliverables:

- Implement mkdir, write, rename, delete, upload, batch info, and candidate resolution.
- Preserve atomic writes, observed identities, conflict detection, autosave semantics, operation IDs, and typed failures.
- Implement filesystem watch-diff repair tokens.
- Start search with bounded on-demand traversal and top-K results.
- Add persistent Quick Open indexing only after on-demand search measurements justify it.
- If a persistent index is required, use one SQLite writer, durable generations, bounded breadth-first traversal, and no full-index scoring per keystroke.
- Implement zip, count, and HTML preview as isolated bounded jobs only when their UI actions are enabled.

Exit criteria:

- Finder mutation and search journeys work without backend-specific branches.
- Autosave followed immediately by another edit survives delayed filesystem events.
- Search work is cancellable when the query changes.
- Search CPU scales with bounded candidates rather than every indexed path.
- Mutations remain unavailable to read-only users.

### Phase 6: TextEdit

TextEdit is a parallel frontend/editor workstream after the Phase 5 file contracts are frozen. It has no new backend routes.

Deliverables:

- Port source-file TextEditor/CodeMirror journeys, settings, search, diff actions, and save/conflict states using the shared Phase 5 file owners.
- Keep editor capability checks separate from Markdown ViewEdit and do not add a second filesystem write path.

Exit criteria:

- Source files open, edit, save, conflict, and reopen identically to Python.
- CodeMirror toggles exercise the real UI path and preserve visible content.
- TextEdit can merge independently from ViewEdit and Differ.

### Phase 7: ViewEdit

ViewEdit is a parallel Markdown frontend/editor workstream after the Phase 5 file contracts are frozen. It has no new backend routes.

Deliverables:

- Port Markdown ViewEdit/ProseMirror parse, edit, serialize, preview fallback, upload, and split-scroll journeys.
- Preserve comments, whitespace, supported GFM constructs, images, and actionable parser diagnostics.
- Keep ProseMirror failure read-only and visible rather than silently activating legacy editing.

Exit criteria:

- Source -> ViewEdit -> serialize -> save -> reopen round trips match Python.
- Supported and neighboring unsupported Markdown constructs have browser coverage.
- TextEdit and ViewEdit share capability and file-state owners without duplicated writes.

### Phase 8: Differ

Deliverables:

- Implement repository discovery using the installed Git executable.
- Use `git --no-optional-locks`, explicit timeouts, output bounds, a Git-specific environment policy, disabled interactive prompts, and machine-readable NUL-delimited output. Preserve required HOME, locale, SSH agent, credential-helper, and repository configuration behavior.
- Implement `/api/fs/diff`, `/api/fs/git-history`, `/api/fs/git-commit`, `/api/session-files`, `/api/session-files-batch`, and blame when each matching browser action is ready.
- Preserve untracked, added, deleted, renamed, conflicted, detached-head, no-history, and outside-repository behavior.
- Coalesce repository refreshes by descriptor-pinned repository identity.
- Cache immutable Git products by repository generation and request parameters.
- Publish repository invalidations rather than rebuilding a full work graph for every filesystem event.
- Run long Git operations in bounded jobs with typed timeout and cancellation results.

Exit criteria:

- Differ shows the same file set, statuses, diffs, history, committed snapshots, and action availability as Python on shared fixtures.
- Repositories with unusual filenames, worktrees, submodules, sparse checkouts, conflicts, and detached HEAD are covered.
- A hung Git command cannot block terminal, health, or unrelated repository work.
- The process count and Git invocation rate are measured under an active browser.

### Phase 9: Tabber

Tabber depends on reliable session topology, activity, process classification, and session-file products. It follows Finder and Differ so it can reuse those owners instead of creating another metadata graph.

Deliverables:

- Implement `/api/activity`, `/api/activity-summary`, `/api/session-metadata`, session status, and the batch session-file projections Tabber consumes.
- Build one canonical session snapshot from one tmux snapshot and one bounded process inventory.
- Reuse Differ's repository and session-file products.
- Reuse Finder's path identities and file events.
- Maintain immutable published snapshots and encode each revision once.
- Push revisioned changes to the browser; do not rebuild metadata because the browser polled.
- Implement active/recent/quiet cadence only if measurements show it is needed.
- Add current-window, attention, agent-TUI classification, and auto-approve indicators in separate capability increments.

Exit criteria:

- Tabber ordering, badges, activity windows, session selection, and external tmux changes match Python fixtures and browser behavior.
- New, renamed, and killed sessions cannot be removed by a stale snapshot.
- One browser request cannot trigger a full Git/process/tmux rebuild.
- Snapshot construction, serialization, and event publication have independent timing and allocation metrics.

### Phase 10: Complete Shared Event and Work Infrastructure

Phase 3 provides the complete correctness substrate before Finder. This phase adds mature operational projection and only the job classes justified by Finder, Differ, and Tabber measurements.

Deliverables:

- Audit and harden the full `/api/client-events` contract under combined Finder, Differ, and Tabber demand.
- Audit operation lookup and acknowledgement under cancellation, restart, and slow consumers.
- Add bounded job classes for point work, interactive search, fresh metadata, bulk work, and maintenance only where measurements show conflicting resource ownership.
- Coalesce duplicate work by stable keys and publish one immutable last-known-good product.
- Make queue wait separate from execution duration in logs, metrics, and traces.
- Implement graceful cancellation from HTTP disconnect through external process termination.
- Add System status for goroutines, queues, caches, descriptors, SQLite, tmux, Git, and worker ownership.

Exit criteria:

- Deliberate event loss, queue overflow, restart, and epoch replacement recover through the same JavaScript repair behavior as Python.
- Slow consumers are bounded and receive explicit repair behavior.
- Independent work remains concurrent; conflicting mutable resources have one named owner.
- There are no periodic wakeups without a deadline, event, or measured reconciliation need.

### Phase 11: Remaining Terminal and Session Features

Deliverables:

- Complete rename, ensure-session, window switching, copy selection, tmux scroll, refresh acknowledgements, resize authority, notifications, attention acknowledgement, uploads to terminals, drop actions, and auto-approve controls. Basic create-session planning already ships with the Phase 2 browser flow.
- Add tmux control mode for topology and format subscriptions if it reduces command count without changing terminal display semantics.
- Preserve the normal PTY attachment for xterm output unless a separate control-mode prototype passes all copy-mode and rendering gates.
- Complete settings required by terminal, Finder, Differ, and Tabber.
- Complete operational health, recovery controls, update workflow, and backend diagnostics.

Exit criteria:

- The non-stats, non-chat, non-agent daily workflow is usable in Go mode.
- Every enabled command has permission, idempotency, timeout, cancellation, and reconciliation behavior.
- Python remains available for features intentionally deferred.

### Phase 12: YO!stats Storage Design

Do not port the current implementation line by line. Resolve the known write amplification and whole-history materialization problems in the data model before Go becomes a writer.

Deliverables:

- Freeze language-neutral fixtures for observations, usage atoms, coverage epochs, unavailable spans, prices, snapshots, chunks, deltas, tombstones, repair, and writer fences.
- Measure the production-sized Python database and establish CPU, memory, write bytes, read syscalls, cold start, steady append, and query baselines.
- Specify one durable generation model that permits transaction batching without presenting uncommitted facts as durable.
- Specify bounded incremental materialization that does not load all retained history into Go objects.
- Preserve the existing SQLite schema in read-only shadow mode first.
- Build a pure Go materializer and encoder that reads a copied committed generation and compares normalized structures and exact bytes where required.
- Test every range, resolution, sparse family, no-data interval, cost attribution, restart boundary, malformed row, and clock transition.

Exit criteria:

- The design removes per-fact commits without weakening acknowledgement durability.
- Peak memory is bounded independently of retained history size, or growth is explicitly measured and accepted.
- Shadow output has no unexplained parity mismatches.
- Total Go CPU, memory, I/O, and encoded-byte cost clears predetermined gates.

### Phase 13: YO!stats Read Path

Deliverables:

- Implement `/api/stats-capabilities`, snapshot, delta, stream, retry, pricing catalog, and operational readiness from committed data.
- Preserve snapshot chunking, generation fences, delta ordering, tombstones, unavailable spans, repair states, and SSE cadence.
- Serve pre-encoded immutable products; requests must not trigger materialization.
- Run Python and Go readers against independent copies, never competing writers against one live WAL.
- Exercise current JavaScript YO!stats without backend-specific code.

Exit criteria:

- The complete browser matrix passes against the Go read path.
- Reconnect, stale generation, partial stream, restart, and upgrade-required behavior match Python.
- Visible YO!stats traffic does not delay terminal, Finder, Differ, or Tabber.

### Phase 14: YO!stats Writer and Cutover

Deliverables:

- Port collectors and transcript token ingestion incrementally.
- Make Go the sole writer only after schema, protocol, build, and process ownership fences prevent concurrent Python writes.
- Batch durable appends under the chosen generation design.
- Preserve migration, retention, pruning, checkpoint, vacuum, ring replay, invalidation, and crash recovery.
- Provide backup and rollback instructions that treat SQLite database, WAL, and shared-memory files as one state unit.
- Keep Python mode read/write-compatible for a defined rollback window, or fail closed with an explicit schema fence when compatibility ends.

Exit criteria:

- Crash-at-every-boundary tests preserve acknowledged observations.
- No cursor, generation, or watermark leads durable data.
- Production-size cold start, steady append, visible stream, repair, prune, and restart gates pass.
- Go process-tree resource use materially improves over the measured Python baseline.

### Phase 15: YO!chat

YO!chat can remain Python-only until the primary terminal and repository workflow is stable in Go.

When scheduled:

- Preserve existing messages, signed cursors, idempotent sends, typing leases, read cursors, search, retention, and identity rules.
- Reuse the Go auth and event broker.
- Adopt the existing SQLite schema before considering changes.
- Enable the pane only after bootstrap, pagination, delta, send, retry, reconnect, rate-limit, and multi-browser tests pass.

### Phase 16: YO!agent

YO!agent should be last because its Python SDK integrations change independently and do not justify blocking the core migration.

Options when scheduled:

1. Keep a small Python YO!agent sidecar with one versioned, bounded protocol while the Go server owns browser delivery and lifecycle.
2. Use stable Claude/Codex CLI or app-server protocols directly from Go if those interfaces meet feature needs.
3. Port SDK integration only if supported Go SDKs expose the required streaming, resume, sandbox, approval, and cancellation behavior.

Do not embed Python in the Go process. Do not expose the sidecar directly to the browser. Enable YO!agent only after stream ordering, cancellation, conversation persistence, job confirmation, actions, auth state, restart, and partial-output recovery match the JavaScript contract.

## Work Scheduling Rules

The Go runtime makes concurrency cheap, but unbounded goroutines can reproduce the current overload in a different form.

- Every external request has a context and deadline policy.
- Every queue has a capacity, admission rule, overload response, and metric.
- Every expensive key has one in-flight owner and optional coalesced followers.
- Every external command has an output bound and a termination sequence.
- Every persistent product has one writer.
- Every cache has a size bound, generation key, invalidation owner, and last-known-good policy.
- Every periodic task names the state it reconciles and why events cannot provide correctness.
- Every browser-triggered refresh reads a published product; it does not elect itself to rebuild the world.
- Cancellation must reach Git, tmux, filesystem traversal, SQLite query, and worker process boundaries.
- Background work must not inherit authority from whichever browser happened to start it.

Use goroutines for concurrent I/O and independent state owners. Use a bounded semaphore or worker queue for Git, filesystem scans, indexing, compression, and other resource-heavy work. Use a separate process only when cancellation cannot reliably stop the operation, memory must be reclaimed by exit, or a crash must not take down terminal service.

## Data and Migration Safety

- Python remains the owner of every existing database until the corresponding Go phase explicitly takes ownership.
- Go may read copied databases or read-only snapshots during shadow work.
- Never let Python and Go write the same SQLite database concurrently.
- Preserve current schema and sidecar formats for the first Go owner.
- Add writer protocol and build fences before cutover.
- Give each database its own owner and migration policy. Document filename and host partitioning, SQLite `application_id`, `user_version`, minimum reader/writer/build fences, WAL ownership, backup unit, crash points, rollback behavior, and whether migration is in-place, side-by-side, or rebuildable.
- Apply an embedded forward migration only after the product-specific policy selects it, before accepting traffic; fail startup on migration failure.
- Do not automatically run down migrations during rollback.
- Test upgrades from every supported Python schema fixture.
- Verify the SQLite version embedded in release binaries.
- Verify required compile options, including FTS5 where used, and expose SQLite version and compile options in authenticated diagnostics.
- Keep WAL databases on supported local filesystems and report unsupported network-backed state paths before writing.

## Test Strategy

### Shared Backend Contract Suite

Run the same black-box suite against Python and Go:

- Bootstrap extraction, required fields, capabilities, script escaping, and asset ordering.
- Route method, path, auth role, body limit, response protocol, status, headers, and envelope.
- WebSocket binary/text behavior, fragmentation, ping/pong, resize, reconnect, read-only mode, limits, and close.
- SSE ready, heartbeat, event, replay, repair, overflow, epoch change, and reconnect.
- Static gzip, content type, immutable versioning, no-store behavior, HEAD, and missing assets.
- Authentication setup, login, Basic-to-cookie, logout, role restrictions, TLS, and redirect behavior.
- Browser journeys for each enabled pane.
- Cross-language root and port ownership under concurrent startup.
- Frontend/backend skew, capability downgrade, interrupted migration, schema fences, and rollback binaries after every ownership cutover.

### Go-Specific Tests

- Table tests for parsers, validation, projections, capability dependencies, and error mapping.
- `httptest` for every handler and middleware.
- Fuzz tmux output parsing, Git NUL parsing, WebSocket control messages, SSE encoding, filesystem paths, and persisted row decoding.
- Bazel race-enabled unit and integration suites (for example, a dedicated `//:go_race_tests` suite) rather than an independent `go test` graph.
- Temporary private tmux sockets and fixture-created sessions.
- Temporary Git repositories covering unusual filenames and repository states.
- Worker crash, hang, oversized output, cancellation, parent exit, and shutdown tests.
- Linux and macOS builds and integration tests on amd64 and arm64 where available.
- Leak tests for goroutines, descriptors, child processes, sockets, and temporary state.

### Browser Acceptance

The current generated JavaScript remains the subject. For each phase:

1. Start Python and run the applicable browser journey.
2. Start Go with the same frontend and isolated equivalent state.
3. Run the same journey without changing the test based on backend identity.
4. Compare user-visible state, network contract, terminal bytes where stable, and operational measurements.
5. Verify disabled panes generate no network traffic and explain how to use Python mode.

## Release and Rollout

1. Land the capability-aware frontend while Python reports all current features available.
2. Land the dual launcher with Python as the default and Go unavailable unless explicitly selected.
3. Publish Go terminal MVP builds as an opt-in developer mode.
4. Add one feature capability at a time after its full vertical slice passes.
5. Keep one stable URL and one frontend artifact for both backends.
6. Record backend and capability set in diagnostics and issue reports.
7. Never enable an incomplete pane by default to increase a parity percentage.
8. Make Go the suggested backend after Terminal, Finder, Differ, Tabber, auth, settings, events, and operations satisfy daily use.
9. Keep Python as the complete-feature option through YO!stats cutover and the deferred chat/agent period.
10. Retire Python only after persisted-state compatibility, rollback policy, user workflows, and supported platforms have explicit closure criteria.

## Milestone Definition of Done

Every milestone must provide:

- User-visible scope and unavailable-feature behavior.
- Capability declaration and route ownership from one catalog.
- Shared Python/Go contract fixtures.
- Focused browser journey using the unchanged frontend.
- Unit, integration, race, fuzz, cancellation, shutdown, and leak coverage appropriate to the feature.
- CPU, memory, I/O, latency, allocation, process-count, and external-command measurements.
- Failure injection for the mutable resources introduced by the phase.
- Linux and macOS evidence.
- Explicit rollback behavior.
- No unexplained background polling or unbounded queue.

## Stop and Reassess Gates

Pause the migration and reconsider the design if any of these occur:

- The frontend accumulates backend-specific branches outside the shared capability layer.
- A feature requires Python and Go to write the same state concurrently.
- Internal Go modules start communicating through HTTP or generic RPC instead of direct calls.
- A new daemon exists only because Python had one.
- Go mode performs the same full scans or per-fact transactions with lower constant cost but unchanged growth.
- The contract suite normalizes enough fields that meaningful parity failures disappear.
- Aggregate process-tree CPU, memory, or I/O is not improving for the migrated workload.
- Operational failures become harder to attribute despite added instrumentation.
- The migration prevents normal Python feature maintenance for an extended period.

## Initial Backlog

1. Export the Python route manifest and bootstrap schema.
2. Add the Python capability catalog and capability-aware frontend gate with all existing features enabled.
3. Inventory and capability-gate every unconditional browser startup request, stream, timer, warm-up, reconnect path, and saved-layout side effect.
4. Add implementation-neutral terminal WebSocket fixtures.
5. Add a backend-selectable black-box test harness.
6. Capture the Python baseline for startup, idle, one terminal, terminal flood, reconnect, and shutdown.
7. Specify and test the cross-language product-root and port ownership protocol.
8. Create the Bazel module, pinned Go toolchain, source layout, build metadata, and structured logging.
9. Implement secure health, auth, TLS, static assets, bootstrap, and disabled-pane presentation.
10. Implement the PTY-backed tmux WebSocket MVP.
11. Add real tmux, Chromium, race, fuzz, leak, and process-tree performance gates.
12. Ship Go mode as an explicit terminal-only preview while Python remains the default.

## References

- [Go 1.22 routing enhancements](https://go.dev/blog/routing-enhancements)
- [Go `net/http`](https://pkg.go.dev/net/http)
- [Coder WebSocket](https://github.com/coder/websocket)
- [creack/pty](https://github.com/creack/pty)
- [tmux control mode](https://github.com/tmux/tmux/wiki/Control-Mode)
- [Git status porcelain format](https://git-scm.com/docs/git-status)
- [fsnotify](https://github.com/fsnotify/fsnotify)
- [Go SQLite driver](https://github.com/mattn/go-sqlite3)
- [SQLite WAL](https://sqlite.org/wal.html)
- [Goose migrations](https://github.com/pressly/goose)
- [Prometheus Go client](https://github.com/prometheus/client_golang)
- [OpenTelemetry Go](https://opentelemetry.io/docs/languages/go/)
- [Go pprof](https://pkg.go.dev/runtime/pprof)

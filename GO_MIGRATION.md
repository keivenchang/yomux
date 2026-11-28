# YOmux Go backend status

Go is the only supported product runtime. This page records what the Go backend serves (capabilities and routes), what was retired with the Python server, and what is still open. How the backend is built is in [Backend architecture](docs/specs/BACKEND_ARCHITECTURE.md); setup, tests, and releases are in [Development](docs/DEVELOPMENT.md).

## Launch and retirement

```bash
make setup
./boot.sh --port 7111
```

- `boot.sh <port>` runs the managed lifecycle. `--backend go`, `--backend auto`, and `--golang` select Go.
- Selecting Python in either environment prefix (`YOMUX_*` or `YOLOMUX_*`), `--deprecated-python`, or an invalid backend is rejected before any product root, lease, or listener changes. A missing or non-executable Go binary fails; there is no fallback.
- Retired with the Python server: `--sessions`, Python transcript and runtime reports, the Python development reloader, login decoys, firewall escalation, the Python recovery CLI and throttle diagnostics, Search & Runs, background activity summaries, watched pull requests, and hosted integration services. Their routes return typed unavailable responses.
- Python remains for developer tools, mocks, fixture builders, and test controllers only. Removing Python does not authorize deleting any user state.

## Capabilities

`GET /api/capabilities` is how the browser decides which features to show. The registered route list below, not the capability name, decides whether a given route exists.

Always available: `core.bootstrap.v1`, `core.diagnostics.v1`, `sharing.scoped.v1`, `terminal.websocket.v1`, `terminal.tmux.v1`, `terminal.status.v1`, `session.roster.v1`, `session.create.v1`, `session.kill.v1`, `session.rename.v1`, `pane.finder.v1`, `pane.differ.v1`, `pane.stats.v1`, `pane.tabber.v1`, `finder.read.v1`, `finder.watch.v1`, `finder.write.v1`, `finder.search.v1`, `finder.batch.v1`, `finder.git.v1`, `finder.preview.v1`.

Enabled at startup once their owner is ready: `core.settings.v1`, `core.updates.v1`, `yolo.auto-approve.v1`, and `pane.chat.v1` and `pane.agent.v1` (after the chat and agent stores initialize).

Never enabled: `core.integrations.v1`.

Any unregistered `/api/*` request gets HTTP 501 with `error: feature_unavailable` and the mapped capability name. Product code must not return an empty Python-shaped payload instead.

### Hosted links

Session metadata finds links for branches with GitHub or GitLab remotes. GitHub.com and GitLab.com are queried through their public APIs by default. To show private metadata to users who can read session metadata, list specific repositories in `YOMUX_HOSTED_AUTH_REPOSITORIES` (comma-separated HTTPS URLs); those lookups use the host's GitHub CLI login or `GITLAB_TOKEN`. Linear links come from explicit URLs, or issue IDs in branch and commit references or hosted requests; set `YOMUX_LINEAR_ISSUE_BASE_URL` to `https://linear.app/WORKSPACE/issue` to link bare IDs. Linked GitHub issues come from explicit issue URLs and, with authenticated lookup, GitHub's closing references.

## Registered routes

These routes have handlers in `cmd/yomux-go`. Roles and capabilities per route are in `internal/capabilities/catalog.go`.

### Core, authentication, events, and operations

- `/healthz`, `/livez`, authenticated `/readyz`
- `/login`, `/logout`, `/api/auth-setup`
- `/`, `/static/*`, `/preview-popout`, `/api/ping`, `/api/capabilities`, `/api/runtime/compatibility`
- `/api/client-events`, `/api/event`, `/api/events`
- `/api/operations/{id}`, `/api/operations/ack`, `/api/operations/cancel`, `/api/operations/accept`, `/api/operations/terminalize`
- `/api/background/status`, `/api/background/claim`
- `/api/system-status`, `/api/system-status/advanced`, `/api/logs`
- `/api/diagnostics/heap`, `/api/diagnostics/performance`, `/api/runtime/cpu-profile`
- `/api/settings`
- `/api/update-status`, `/api/self-update`, `/api/self-update/status`

### Terminal and sessions

- `/ws`, `/api/tmux`, `/api/tmux-signals`, `/api/tmux-session-exists`, `/api/tmux-status`, `/api/tmux-window`
- `/api/session-metadata`, `/api/activity`, `/api/activity/touch`, `POST /api/attention-ack`
- `/api/tabber-description`, `/api/tabber-view-session`
- `/api/ensure-session`, `/api/create-session-plan`, `/api/create-session`, `/api/rename-session`, `/api/kill-session`, `/api/kill-window`
- `/api/transcripts`, `/api/transcript`, `/api/context`, `/api/context-items`, `/api/context-stream`
- `/api/auto-approve`, `/api/yolo-rules`, `/api/yolo-rules/open`, `/api/yolo-rules/reload`

Notes:

- The browser must hide terminal and session features whose capability is unavailable.
- Session metadata and Tabber activity carry per-window completion times and transition IDs. `POST /api/attention-ack` stores bounded acknowledgements and publishes `attention_acks_changed` so other tabs agree.
- `/api/activity?scope=all` returns the global tmux session order with live window metadata and activity for Tabber. It covers the whole tmux roster, so every live session gets a row before pane metadata or agent details arrive. The command clears inherited nested-tmux variables; tests set `YOMUX_TMUX_SOCKET` with synthetic sessions for isolation.
- Transcript routes accept an optional `pane_target` or `window`. Without either, Go uses the active tmux pane, so a session with several Claude, Codex, or OpenCode windows does not return an unrelated transcript.
- `yolo.auto-approve.v1` covers the per-session YO toggle and status. Its worker uses the shared target lock, pins the tmux session, pane, and live agent process, and suspends when fresh status or terminal evidence is missing. It applies the `approve`, `decline`, `block`, `ask`, `notify`, and `off` actions to supported Claude and Codex prompts and honors `yolo.dry_run`. For the exact current Codex capacity notice only, it sends `continue` and Enter immediately, then every 10 seconds while the notice remains; if its own `continue` is still unsubmitted, the next retry sends only Enter.

### Finder, editors, and Differ

- `/api/fs/fast/list`, `/api/fs/list`, `/api/fs/read`, `/api/fs/info`, `/api/fs/raw`, `/api/fs/directory-size`
- `/api/fs/sqlite-preview`, `/api/fs/office-preview`, `/api/tex/compiler`, `/api/tex/compiler/install`, `/api/tex/preview`
- `/api/fs/write`, `/api/fs/delete`, `/api/fs/rename`, `/api/fs/move`, `/api/fs/mkdir`, `/api/fs/chmod`, `/api/upload`
- `/api/fs/search`, `/api/fs/search-stream`, `/api/fs/index-status`, `/api/fs/unindex`
- `/api/fs/watch-diff`, `/api/fs/review`, `/api/watch/roots`
- `/api/fs/batch`, `/api/fs/resolve-file-candidates`
- `/api/fs/diff`, `/api/fs/git-history`, `/api/fs/git-history-stats-stream`, `/api/fs/git-commit`
- `/api/batch/search`, `/api/batch/blame`, `/api/batch/zip`, `/api/batch/count`, `/api/batch/html-preview`
- `/api/session-files`, `/api/session-files-stream`, `/api/session-files-batch`

### YO!stats

- `/api/stats-agent-status`, `/api/stats-capabilities`, `/api/stats-session-aliases`
- `/api/stats-observations`, `/api/stats-snapshot`, `/api/stats-delta`, `/api/stats-stream`, `/api/stats-refill`, `/api/stats-retry`
- `/api/pricing-catalog`, `/api/pricing-catalog/refresh`

When the token collector enables database checkpoints, Go upgrades `stats-v9.sqlite3` in place to schema 10 with minimum writer protocol 26; the filename does not change. Parser progress, exact comparison bytes, and scheduling metadata are written in the same transaction as usage facts and tombstones, and a checkpoint-only update does not change the graph source generation. A writer without token collection can still use schema 9 for interoperability fixtures. Unchanged Python cannot write the migrated database.

Before upgrading, Go holds the writer lease and legacy cursor locks, makes a consistent SQLite backup, and copies the paired cursor files and writer fence into an adjacent `<database>.pre-checkpoints-*` directory. `cursor-manifest.json` maps original paths to backup files, and the `go-checkpoints-v1` row in `migration_reconciliation` records the backup directory in `source_digest`. Pending legacy cursor work is replayed from committed progress.

The removed Python server is not a rollback target. To recover to an explicitly chosen compatible Go build: stop the current owner, and keep the migrated database, WAL/SHM files, writer fence, and cursor files aside. Restore a consistent backup and its full cursor manifest only after checking that build's schema and writer-protocol compatibility. Keep the migrated sidecar files away from the restored database, and keep both backup sets so newer facts can be recovered.

### Chat and agent

- `/api/chat/` with `bootstrap`, `page`, `delta`, `context`, `search`, `send`, `yoagent`, `typing`, and `read`
- `/api/agent-auth`, `/api/yoagent/skills`, `/api/yoagent/skill-files`, `/api/yoagent/skill-files/upsert`, `/api/yoagent/skill-files/delete`
- `/api/yoagent/jobs`, `/api/yoagent/jobs/`, `/api/yoagent/conversation`, `/api/yoagent/chat`, `/api/yoagent/chat/`
- `/api/yoagent/actions/preview-send`, `/api/yoagent/actions/execute-send`, `/api/yoagent/waits/`, `/api/yoagent/prewarm`, `/api/yoagent/reset`

These handlers are active only after their stores initialize.

### Scoped sharing (YO!Share)

`sharing.scoped.v1` covers the host routes `/api/share`, `/api/share/document`, `/api/share/chat`, `/api/share/recovery`, `/api/share/editing`, and `/api/share/presence`, which require an administrator. Guests use `/share/<grant>` and its resource, join, state, and chat operations with a separate scoped cookie that never enters normal account authentication. Grants live in memory only; file-edit recovery journals are private configuration state.

- `share.go` owns grants, browser slots, pinned terminal access, publication, and share chat.
- `share_collaboration.go` owns per-file revisions, edit validation, presence, durable acceptance, guarded saves, and recovery.
- Finder's granted-file and write-reservation owners fence filesystem generations and ordinary saves.

## API and async rules

- Auth and role checks run before route work.
- Immediate work returns a terminal response envelope.
- Accepted work gets a durable operation ID and must publish exactly one completion or failure.
- Operation acknowledgements are persisted and pruned within bounds.
- Cancelling the HTTP request does not cancel work after an accepted receipt; only explicit operation cancellation does.
- Events and operations survive restart through `go-events.jsonl`, `go-operations.json`, and `go-operations.jsonl` in the config directory.
- Unknown or unavailable routes return typed failures, never HTTP 200 with error text.
- Finder work runs on separate bounded in-process schedulers; a full queue returns a typed busy failure. There are no cross-process queues.

## Migration history

| Slice | Result |
| --- | --- |
| Phase 0 | Bazel/rules_go build, Docker test image, contract fixtures, capability and bootstrap contract, isolated launch path |
| Phase 1 | TLS and authenticated Go skeleton, shared HTML bootstrap, tmux discovery, first PTY/WebSocket path |
| Phase 2 | Terminal and session behavior, role checks, lifecycle, resize, input, output, and the browser contract for terminals |
| Phase 3 | SSE events, accepted-operation receipts, durable journal and restart repair, diagnostics, background status |
| Phase 4/5 | Finder read, write, watch, search, batch, upload, preview, and arbitrary absolute paths with descriptor-bound safety |
| Phase 6/7 | Shared TextEdit and ViewEdit through Go Finder APIs, including save, upload, preview, and Chromium editor workflows |
| YO!stats | Go SQLite ownership, snapshots, deltas, SSE, collectors, materialization, rings, pricing, agent status, tokens, OpenCode, Docker browser coverage |
| Phase 8 | Differ session-file caches, quick history with line-count backfill, consolidated commit detail, diff, history, commit, and blame routes |

Phase numbers are history, not configuration. The transcript compatibility routes remain separate wire contracts owned by their handlers and tests; no consolidation work is queued.

## Remaining gaps

- Final runtime acceptance of native YO!chat and YO!agent is still open.
- Self-update works through `version.yaml`, `core.updates.v1`, `/api/update-status`, `/api/self-update/status`, and `boot.sh`; native Darwin qualification remains a release acceptance step.
- Agent and OpenCode process discovery has Linux and macOS adapters; other systems report it unavailable. Full cross-platform parity is not established.
- Exact cross-backend output comparison, long-history migration evidence, and production-scale performance evidence are incomplete.
- Docker is the required certification environment for Chromium, race, and latency coverage. macOS runs the gate natively or as qualified native.

## Rollout rules

1. Build the exact worktree with Bazel.
2. Run the complete Bazel suite with fixture-owned state and ports.
3. Fix product failures. Do not hide them with retries, sleeps, lower concurrency, or a live-port substitute.
4. Only after automated checks pass, start an explicit non-production preview port with `boot.sh`.
5. Verify the listener, `/healthz`, the capability snapshot, and the exact browser workflow against the rebuilt bundle.
6. Accept retirement only after the exact final candidate passes the native launch matrix, setup, build, and update without Python, the supported browser journeys, a package and import audit, and the full repository gate. Verify the selected 7111 lifecycle first, and keep 7110 and existing agents running. A wider fleet rollout needs a separate decision. A documented native owner does not prove these checks passed.

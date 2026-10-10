# Backend architecture

Go is the target backend and the default server for managed/operator launches. Python is a deprecated temporary fallback. Every currently supported Python-only product feature, the shared HTML renderer, runtime dependencies, and required tooling must be ported or replaced before Python can be removed; removing Python must not silently remove supported behavior. The Go server still invokes Python to render the shared HTML shell, so the Go-only runtime is not complete yet.

## Python

Each Python web server coordinates local services for YO!stats, indexing, bounded work, session status, filesystem watches, and optional auto-approval. Services use host-local runtime sockets and locks. Durable state belongs on local storage. Do not share a live runtime directory or writable database across hosts.

## Go

One `yomux-go` process owns HTTP/TLS, tmux attach, registered routes, Finder and Git work, operations/events, YO!stats, and bounded in-process schedulers. It does not start Python's local services. Go uses private config/state roots and its own storage; matching environment-variable names do not imply matching Python path validation or service behavior.

The shared browser checks `/api/capabilities` before exposing backend-dependent features. Unsupported Go routes return typed `501 feature_unavailable` responses. `GO_MIGRATION.md` owns the current route inventory and Go migration requirements; Python-only product behavior is temporary migration work, not a permanent backend split.

Go keeps auth parsing and request identity in `cmd/yomux-go/auth_config.go` and `auth_runtime.go`, durable event/operation state in `event_operations.go`, background ownership and shutdown in `server_lifecycle.go`, and roster/metadata state in `tmux_metadata.go`. The typed route policy in `internal/capabilities` supplies method-sensitive roles and capability names; the catalog supplies current availability. Server and stats tmux adapters share `internal/tmuxcmd` for socket selection, inherited environment, and locale.

`cmd/yomux-go/go_auto_approve.go` owns persisted YO enablement, worker lifecycle, shared target locks, current tmux and agent-process identity checks, and guarded terminal delivery. `internal/yolo` owns configured rule evaluation and structured Claude/Codex prompt classification. Workers consume fresh Go agent-status snapshots and visible pane captures; stale evidence suspends delivery, and Codex capacity recovery repeats only while the exact current notice remains visible.

HTML rendering retains the shared Python functions. Home, login, and setup rendering share one nonblocking admission slot, a ten-second deadline, and an 8 MiB response bound. Cancellation, malformed home responses, and oversized output terminate and reap the child; a later home request can start a replacement. Shutdown cancels rendering without waiting for a render mutex. All accepted scheduler jobs, including anonymous jobs, inherit scheduler cancellation. Stats SSE delivery ends on write or flush failure and releases demand.

Private-state replacement uses `internal/atomicfile`: a unique mode-0600 staging file must finish writing, syncing, and closing before publication. Session-file cache publication retains its generation check around the final rename. Finder mutations retain their descriptor-based ownership. Claude path lookup advances a bounded discovery cursor across requests using the same filesystem traversal as token discovery; transcript attribution remains separate.

Disk collection still runs every fifteen minutes and keeps stable source IDs derived from exact mountpoint bytes. Healthy rows survive partial `df` failures, invalid samples remain unavailable spans, and collector errors remain counted. Linux native watching uses `Poll`, so descriptors above 1023 remain supported.

## Filesystem boundary

Python applies its configured-root and credential-name policy. Go accepts absolute paths the process can access, with OS permissions, role checks, descriptor identity validation, bounded operations, and protected-root mutation checks. Do not describe these policies as equivalent.

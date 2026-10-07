# Backend architecture

Go is the default server for managed/operator launches. The Python server is deprecated and available only as an explicit temporary fallback while the remaining Go capability gaps close; removal is planned around late October 2026. The Go server still invokes Python to render the shared HTML shell, so a Python runtime dependency does not mean the Python server is selected.

## Python

Each Python web server coordinates local services for YO!stats, indexing, bounded work, session status, filesystem watches, and optional auto-approval. Services use host-local runtime sockets and locks. Durable state belongs on local storage. Do not share a live runtime directory or writable database across hosts.

## Go

One `yomux-go` process owns HTTP/TLS, tmux attach, registered routes, Finder and Git work, operations/events, YO!stats, and bounded in-process schedulers. It does not start Python's local services. Go uses private config/state roots and its own storage; matching environment-variable names do not imply matching Python path validation or service behavior.

The shared browser checks `/api/capabilities` before exposing backend-dependent features. Unsupported Go routes return typed `501 feature_unavailable` responses. `GO_MIGRATION.md` owns the current route inventory and known gaps.

Go keeps auth parsing and request identity in `cmd/yomux-go/auth_config.go` and `auth_runtime.go`, durable event/operation state in `event_operations.go`, background ownership and shutdown in `server_lifecycle.go`, and roster/metadata state in `tmux_metadata.go`. The typed route policy in `internal/capabilities` supplies method-sensitive roles and capability names; the catalog supplies current availability. Server and stats tmux adapters share `internal/tmuxcmd` for socket selection, inherited environment, and locale.

HTML rendering retains the shared Python functions. Home, login, and setup rendering share one nonblocking admission slot, a ten-second deadline, and an 8 MiB response bound. Cancellation, malformed home responses, and oversized output terminate and reap the child; a later home request can start a replacement. Shutdown cancels rendering without waiting for a render mutex. All accepted scheduler jobs, including anonymous jobs, inherit scheduler cancellation. Stats SSE delivery ends on write or flush failure and releases demand.

Private-state replacement uses `internal/atomicfile`: a unique mode-0600 staging file must finish writing, syncing, and closing before publication. Session-file cache publication retains its generation check around the final rename. Finder mutations retain their descriptor-based ownership. Claude path lookup advances a bounded discovery cursor across requests using the same filesystem traversal as token discovery; transcript attribution remains separate.

Disk collection still runs every fifteen minutes and keeps stable source IDs derived from exact mountpoint bytes. Healthy rows survive partial `df` failures, invalid samples remain unavailable spans, and collector errors remain counted. Linux native watching uses `Poll`, so descriptors above 1023 remain supported.

## Filesystem boundary

Python applies its configured-root and credential-name policy. Go accepts absolute paths the process can access, with OS permissions, role checks, descriptor identity validation, bounded operations, and protected-root mutation checks. Do not describe these policies as equivalent.

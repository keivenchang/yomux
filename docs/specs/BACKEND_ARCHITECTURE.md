# Backend architecture

Go is the default server for managed/operator launches. The Python server is deprecated and available only as an explicit temporary fallback while the remaining Go capability gaps close; removal is planned around late October 2026. The Go server still invokes Python to render the shared HTML shell, so a Python runtime dependency does not mean the Python server is selected.

## Python

Each Python web server coordinates local services for YO!stats, indexing, bounded work, session status, filesystem watches, and optional auto-approval. Services use host-local runtime sockets and locks. Durable state belongs on local storage. Do not share a live runtime directory or writable database across hosts.

## Go

One `yolomux-go` process owns HTTP/TLS, tmux attach, registered routes, Finder and Git work, operations/events, YO!stats, and bounded in-process schedulers. It does not start Python's local services. Go uses private config/state roots and its own storage; matching environment-variable names do not imply matching Python path validation or service behavior.

The shared browser checks `/api/capabilities` before exposing backend-dependent features. Unsupported Go routes return typed `501 feature_unavailable` responses. `GO_MIGRATION.md` owns the current route inventory and known gaps.

## Filesystem boundary

Python applies its configured-root and credential-name policy. Go accepts absolute paths the process can access, with OS permissions, role checks, descriptor identity validation, bounded operations, and protected-root mutation checks. Do not describe these policies as equivalent.

# Backend architecture

One `yomux-go` process owns HTTP/TLS and authentication, terminal WebSocket and event streams, Finder/Differ, settings, stats, chat, and agent conversations, jobs, watches, skills, and reviewed sends. Finder may start a private native index worker. Product startup does not launch the retired Python web server or its daemons.

`yomux-go --operator` owns root planning, captured `unset`/`assign` plans, executable/version checks, listener census, authenticated readiness, session discovery, profile dispatch, and native asset publication. Mutable roots and existing persistence formats remain instance-owned. Explicit profiles preserve their configuration, state, certificates, and ports. Installation records the source checkout separately from the installed resource root. Self-update verifies that source, builds the selected revision with native tools, refreshes the same installation prefix, and restarts the installed binary.

Runtime JSON lives in `assets/runtime/`; built-in agent skill/context files remain packaged as data. `make build` publishes generated browser assets under the shared checkout writer declaration. The private-output `tools/staticgen` executable remains the Bazel and self-update builder.

## Go

One `yomux-go` process owns HTTP/TLS, tmux attach, registered routes, Finder and Git work, operations/events, YO!stats, and bounded in-process schedulers. It does not start Python's local services. Go uses private config/state roots and its own storage; matching environment-variable names do not imply matching Python path validation or service behavior.

The shared browser checks `/api/capabilities` before exposing backend-dependent features. Unsupported Go routes return typed `501 feature_unavailable` responses. `GO_MIGRATION.md` owns the current route inventory and Go migration requirements; Python-only behavior outside the supported Go capabilities is retired.

Go keeps auth parsing and request identity in `cmd/yomux-go/auth_config.go` and `auth_runtime.go`, durable event/operation state in `event_operations.go`, background ownership and shutdown in `server_lifecycle.go`, and roster/metadata state in `tmux_metadata.go`. The typed route policy in `internal/capabilities` supplies method-sensitive roles and capability names; the catalog supplies current availability. Server and stats tmux adapters share `internal/tmuxcmd` for socket selection, inherited environment, and locale.

`cmd/yomux-go/go_auto_approve.go` owns persisted YO enablement, worker lifecycle, shared target locks, current tmux and agent-process identity checks, and guarded terminal delivery. `internal/yolo` owns configured rule evaluation and structured Claude/Codex prompt classification. Workers consume fresh Go agent-status snapshots and visible pane captures; stale evidence suspends delivery, and Codex capacity recovery repeats only while the exact current notice remains visible.

Embedded Go templates render home, login, and setup pages. They share one nonblocking admission slot, a ten-second deadline, and an 8 MiB response bound. Go settings and a shared JSON locale registry supply first-paint data. Command discovery, authentication probes, and session launches share native PATH handling; launches execute the resolved absolute path. Git metadata and agent probes have bounded output and cancellation. Shutdown closes page admission, cancels probes and renders, and joins their work. All accepted scheduler jobs, including anonymous jobs, inherit scheduler cancellation. Stats SSE delivery ends on write or flush failure and releases demand.

Private-state replacement uses `internal/atomicfile`: a unique mode-0600 staging file must finish writing, syncing, and closing before publication. Session-file cache publication retains its generation check around the final rename. Finder mutations retain their descriptor-based ownership. Claude path lookup advances a bounded discovery cursor across requests using the same filesystem traversal as token discovery; transcript attribution remains separate.

Disk collection still runs every fifteen minutes and keeps stable source IDs derived from exact mountpoint bytes. Healthy rows survive partial `df` failures, invalid samples remain unavailable spans, and collector errors remain counted. Linux native watching uses `Poll`, so descriptors above 1023 remain supported.

## Filesystem boundary

The supported Go runtime accepts absolute paths the process can access, with OS permissions, role checks, descriptor identity validation, bounded operations, and protected-root mutation checks. Historical Python policy descriptions are not the current filesystem contract.

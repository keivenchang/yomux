# Mutable path inventory

> **STATUS 2026-08-12: HISTORICAL.** This inventory was taken against the `phase2-infrastructure` baseline, which landed on 2026-08-01. Path ownership has since moved through the 0.7.3 root-isolation work and the 0.7.4 package split; `yolomux_lib/infra/root_paths.py` is the current resolver. The runtime-root corrections below record the current path family; use the remaining table for migration history rather than as a complete path catalog.

This is a source-only inventory for the `phase2-infrastructure` baseline. `STATE_DIR` currently conflates runtime, durable state, and cache data; matrix classifications below are migration targets, not claims that the current layout already meets them.

| Matrix row | Current path | Owner and writer | Evidence |
| --- | --- | --- | --- |
| shared config | `~/.config/yolomux/auth.yaml` | auth configuration creation/update | `yolomux_lib/auth.py:16`, `yolomux_lib/auth.py:17` |
| shared config | `~/.config/yolomux/settings.yaml` | settings serializer | `yolomux_lib/workspace/settings.py:29`, `yolomux_lib/workspace/settings.py:1164` |
| shared config | `~/.config/yolomux/yolo-rules.yaml` | approval rules bootstrap | `yolomux_lib/approval/yolo_rules.py:27`, `yolomux_lib/approval/yolo_rules.py:246-248` |
| shared config | `~/.config/yolomux/state.json` | event preference state | `yolomux_lib/infra/common.py:69`, `yolomux_lib/observability/events.py:36-51` |
| host-local runtime | `RUNTIME_DIR/server-leases/<host>/<port>.lock` | port lease/flock record | `yolomux_lib/server_lease.py`, `yolomux_lib/infra/common.py` |
| host-local runtime | `RUNTIME_DIR/services/*.sock`, locks, aliases | local RPC runtime | `yolomux_lib/local_services/runtime.py`, `yolomux_lib/local_services/registry.py` |
| host-local runtime | `RUNTIME_DIR/background-owner/` | generation/owner records and lock | `yolomux_lib/infra/background_owner.py` |
| host-local runtime | `RUNTIME_DIR/locks/auto-approve-*.lock` | approval target lock | `yolomux_lib/infra/common.py`, `yolomux_lib/approval/auto_approve_worker.py` |
| host-local runtime | `RUNTIME_DIR/control/` | local Unix control endpoint | `yolomux_lib/infra/common.py` |
| host-local runtime | `/tmp/yolomux.<user>/uploads/<session>/` | upload reservation and retention sweep | `yolomux_lib/workspace/uploads.py:22`, `:45-61`, `:81-128` |
| host-local durable | `STATE_DIR/hosts/<stable-host-id>/events-v*.jsonl` | event append log; legacy shared history is retained and never adopted | `yolomux_lib/infra/common.py:event_log_path`, `yolomux_lib/observability/events.py:207-213` |
| host-local durable | `STATE_DIR/hosts/<stable-host-id>/run-history.json` | run-history truncation/atomic rewrite; legacy shared history is retained and never adopted | `yolomux_lib/infra/common.py:run_history_path`, `yolomux_lib/observability/events.py:377`, `:413` |
| host-local durable | `STATE_DIR/activity.json`, `activity-heartbeats.jsonl` | activity snapshot/heartbeat; stable host ID is the durable key and hostname is display-only | `yolomux_lib/infra/common.py:72-75`, `yolomux_lib/observability/activity.py:196-206` |
| host-local durable | `STATE_DIR/tmux-AI-status.json`, `attention-acks.json`, `watch-index.json` | tmux/attention/watch state; stable host ID is the durable key and hostname is display-only | `yolomux_lib/infra/common.py:73-76` |
| host-local durable | `STATE_DIR/hosts/<stable-host-id>/yoagent/{conversation.jsonl,cli-sessions.json}` | YO!agent conversation/session state; legacy shared state is retained and never adopted | `yolomux_lib/yoagent/conversation.py:default_yoagent_state_dir`, `:302-313`, `:347-373` |
| host-local durable | `STATE_DIR/hosts/<stable-host-id>/yochat.sqlite3`, `yochat-history/`, and `chat-cursor.key` | chat store, dated history, journal lock, and cursor-signing key | `yolomux_lib/chat/chat_store.py`, `yolomux_lib/chat/chat_service.py` |
| host-local durable | `STATE_DIR/stats-v*.sqlite3`, migration sidecars | stats writer/migration | `yolomux_lib/stats_current/client.py:182`, `yolomux_lib/stats_current/storage.py:677-689`, `yolomux_lib/stats_current/migration.py:222-267` |
| host-local cache | `STATE_DIR/search_index/<digest>.sqlite3` | filesystem index | `yolomux_lib/search/file_index.py:32`, `:221`, `:407-408` |
| host-local cache | `YOLOMUX_CACHE_DIR/model-pricing/pricing.sqlite3` | pricing catalog refresh | `yolomux_lib/infra/common.py:65-67`, `yolomux_lib/observability/pricing_catalog.py:292-298` |
| host-local cache | `STATE_DIR/hosts/<stable-host-id>/transcript-scan-cache-v*` | transcript scan cache; legacy unpartitioned cursors are retained and never adopted by a fresh partition | `yolomux_lib/workspace/session_files.py:561-567` |
| host-local cache | `STATE_DIR/hosts/<stable-host-id>/session-files-repository-snapshots/*.json` | repository snapshot cache; legacy shared cache is retained and never adopted | `yolomux_lib/workspace/session_files.py:repository_snapshot_cache_path`, `:2480` |
| host-local cache | `STATE_DIR/hosts/<stable-host-id>/{session-files-cache,activity-cache,background-owner/client-events.json}` | session-files/tabber caches and per-host leader/follower replay; legacy shared cache is retained and never adopted | `yolomux_lib/app.py:default_session_files_cache_dir`, `:default_tabber_activity_cache_dir`, `:default_background_client_events_path` |
| host-local cache | `STATE_DIR/search_index` lock/metadata | search index coordination | `yolomux_lib/search/file_index.py:260-267` |
| shared read-only | `~/.claude/{sessions,projects}`, `~/.codex/sessions` | transcript readers; no writer established here | `yolomux_lib/tmux/sessions.py:479-480`, `:698-721` |
| shared read-only | repository/source trees | readers and Git metadata discovery; builds must be single-writer | `yolomux_lib/workspace/session_files.py:2337`, `yolomux_lib/workspace/metadata.py:148-187` |
| host-local cache | `PYTHONPYCACHEPREFIX`, `.pytest_cache`, pip/npm caches, coverage output | supported entrypoints suppress the first package cache, then `infra.worktree_writer.configure_host_local_artifacts()` routes later interpreter/test/package caches below one host-local artifact root | `yolomux.py`, `yolomux_lib/__init__.py`, `conftest.py`, `tools/check.py`, `tools/static_build.py`, `yolomux_lib/infra/worktree_writer.py` |
| shared generated static source | `static/yolomux.js`, `static/yolomux.css`, `static/locales/` | `tools/static_build.py` holds the physical-worktree writer declaration; generated source remains checked in and cannot be relocated | `tools/static_build.py`, `tools/static_build.py --check` |
| external build output | package metadata, wheel/build directories, browser profiles, arbitrary tool output | external tools must use a separate physical worktree, an explicit host-local output directory, or the documented writer wrapper | tooling-dependent; no safe central interception point |

## SQLite migration order

Every current default below is under `STATE_DIR` or `YOLOMUX_CACHE_DIR`; both default below the shared home (`infra/common.py:62-67`). SQLite WAL is not valid for a live multi-host database, so each is host-local durable/cache migration work.

**The two rows this inventory left unverified were resolved by the integrator on 2026-08-01, and both are WAL.** Chat opens through `atomic_file.open_wal_database`, and login throttling lives at `STATE_DIR/login-throttle.sqlite3`. Both live files on this box report `journal_mode=wal` when queried directly -- source reading alone had missed them because neither module names the pragma; the shared opener sets it. **So all six stores are WAL and all six default under the shared NFS-exported home.** There is no store in this product that is currently safe for two hosts, which is why the filesystem preflight and the host-local migration are not optional.

| Store | Default path | WAL evidence | Shared-home default |
| --- | --- | --- | --- |
| stats | `STATE_DIR/stats-v*.sqlite3` | `stats_current/storage.py:677-689` enables WAL | yes |
| chat | `STATE_DIR/hosts/<stable-host-id>/yochat.sqlite3` | **WAL** -- opens via `atomic_file.open_wal_database` (`chat/chat_store.py`); the partition prevents cross-host collision but does not make WAL safe on NFS | yes |
| search | `STATE_DIR/search_index/<digest>.sqlite3` | `search/file_index.py:407-408` enables WAL | yes |
| pricing | `YOLOMUX_CACHE_DIR/model-pricing/pricing.sqlite3` | `observability/pricing_catalog.py:294-298` enables WAL | yes |
| login throttle | `STATE_DIR/login-throttle.sqlite3` (`login_rate_limit.py:421`) | **WAL** -- live file reports `journal_mode=wal` | yes |
| atomic file lock database | caller-provided path | `infra/atomic_file.py:111-124` enables WAL | caller-dependent |

## Remaining boundaries

- Login throttling was verified after the original source pass: production opens `STATE_DIR/hosts/<stable-host-id>/login-throttle.sqlite3` through the shared WAL opener.
- Python bytecode, pytest cache, pip/npm caches, and coverage output are routed below the host-local worktree artifact root. Uploads already use `/tmp/yolomux.<user>/uploads/`, and boot logs default to `/tmp`. Package metadata, wheel/build directories, browser profiles, and arbitrary external tool output cannot be intercepted reliably; use separate physical worktrees or explicit host-local output directories and hold the writer declaration through the wrapper when the command mutates shared source or Git metadata.
- Runtime-built string paths can evade the static guard below; it protects direct persistent-root declarations and direct writes rooted in `Path.home()`, not arbitrary runtime concatenation or external tools.

## Shared physical worktree writer declaration

Each physical Git worktree has one declaration at `<gitdir>/yolomux/worktree-writer/owner.json`; linked worktrees therefore use their own per-worktree Git metadata directory instead of sharing one source-tree marker. Schema 1 records the existing stable host, hostname, boot, PID/start identity, instance nonce, a random ownership token, purpose, declaration time, and heartbeat time. The owning process refreshes the heartbeat every five seconds and removes the slot only when the record still carries its token. A same-host dead, recycled-PID, or previous-boot owner is reclaimable immediately; a foreign or unverifiable owner is reclaimable only after its 30-second heartbeat lease expires. A late release never removes a successor token.

Normal servers only inspect this declaration. A fresh foreign or unverifiable writer produces a warning and the server continues as a read-only source consumer; inspection creates, rewrites, and reclaims nothing. `tools/check.py`, direct pytest, and `tools/static_build.py` are declared writers and refuse a fresh active owner. For another source-mutating command, run `python3 -B -m yolomux_lib.infra.worktree_writer --purpose <purpose> -- <command>` so the module installs the host-local prefix before bytecode is enabled. Arbitrary editors, Git clients, package builders, and third-party tools cannot be intercepted centrally, so operators must use the wrapper or a separate physical worktree. Read-only Git commands inherit `GIT_OPTIONAL_LOCKS=0` so they do not refresh the shared index implicitly.

## Product decisions recorded

Decided by Keiven on 2026-08-01. These retire "classify before migration" rows; they are settled contracts, not defaults.

| Family | Decision | Consequence |
| --- | --- | --- |
| Alerts, errors, notification messages | **Individualized per machine.** An error is localized to the machine it happened on. Nothing in this family roams. | `attention-acks.json`, `tmux-AI-status.json`, `watch-index.json`, `activity.json` and `activity-heartbeats.jsonl` become **host-local**, keyed by stable host ID. Acknowledging an alert on lin1 must not clear the identically-named pane on lin2. The UI may still *display* the source hostname when both hosts are visible -- display uses hostname, the key uses stable host ID. |
| YO!chat conversations | **Individualized per machine.** A chat message is local to the machine where it was sent. Nothing in this family roams. | Database, dated history, journal lock, read cursors, typing leases, and cursor-signing key become **host-local**, keyed by stable host ID. Legacy unpartitioned `yochat.sqlite3`, `yochat-history/`, and `chat-cursor.key` stay in place and are never auto-adopted; old messages therefore do not appear in a new host partition. |

**Why this decision is fortunate.** Had alerts been required to roam, a shared writable store would have been needed, and the honest options were a leader-owned store reached over authenticated RPC or immutable snapshot replication -- not a shared SQLite WAL file, which is unsupported on a network filesystem. Host-local alerts avoid that entirely and match the doc's recommended default.

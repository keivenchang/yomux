"""Persistent single-writer service for Quick Open indexes.

HTTP/WebSocket servers submit coalesced dirty paths over a Unix-domain socket.
This process is the only component allowed to build or write a search index;
servers use read-only SQLite snapshots for queries.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import bfs_index
from . import file_index
from ..filesystem import paths
from ..filesystem import search
from ..local_service_projection import registry_runtime_row
from ..infra.common import RUNTIME_DIR
from ..local_services.rpc import LocalRpcError
from ..local_services.command_router import CommonDaemonActions
from ..local_services.command_router import LocalServiceCommandRouter
from ..local_services.rpc import new_envelope
from ..local_services.rpc import request as local_service_request
from ..local_services.rpc import safe_socket_path
from ..local_services.registry import LocalServiceRegistry
from ..local_services.registry import LocalServiceSpec
from ..local_services.runtime import acquire_client_lease
from ..local_services.runtime import request_is_self_connection
from ..local_services.runtime import claim_gated_idle_due
from ..local_services.runtime import live_client_claim
from ..local_services.runtime import reap_dead_client_leases
from ..local_services.runtime import redact_local_service_text
from ..local_services.runtime import release_client_lease
from ..local_services.runtime import run_local_rpc_service


# Route every configured-root FULL build through the breadth-first, directory-at-a-time frontier.
# The persistent indexer is the process that actually runs those builds, so importing it wires the
# one runner `file_index._run_build` consults; a process that never imports this module (a pure
# file_index unit test) keeps the DFS fallback. This is the injector pattern `file_index` already
# uses for the background-owner checker, not a function-local import.
file_index.set_bfs_full_build_runner(bfs_index.build_root_into_index)

# The bounded token the health observer reads when a configured/scheduled obligation, not demand,
# is what keeps `indexd` hot. It replaces the static `demand_started=True` for configured roots so
# an absent-but-scheduled indexer reads "starting", never "Idle - Starts on demand".
INDEXER_SCHEDULED_ABSENCE_REASON = "configured_roots_scheduled"

INDEXER_PROTOCOL_VERSION = 1
# Keep the wire protocol at v1 while older YOLOmux servers are alive.  A v2
# bump would make their service managers fight a newer process.  New optional
# operations are therefore negotiated by capability, and an old service is
# replaced only when a caller actually needs one.
INDEXER_CAPABILITIES = frozenset({"search"})
INDEXER_DEBOUNCE_SECONDS = 2.0
INDEXER_DEFAULT_IDLE_SECONDS = 60.0
INDEXER_SEARCH_RPC_TIMEOUT_SECONDS = 0.5
INDEXER_SOCKET_NAME = "indexer.sock"
INDEXER_LOCK_NAME = "indexer.lock"
INDEXER_COMMAND_ROUTER = LocalServiceCommandRouter({
    action: f"_handle_{action}" for action in (
        "ping", "status", "profile", "drain", "lease", "release", "shutdown_if_idle",
        "enqueue", "search", "drain_search_progress", "unindex", "promote", "shutdown",
    )
})


def default_socket_path() -> Path:
    # Index data is durable state, but its RPC endpoint is runtime state. This
    # keeps a rooted run inside its one configured root instead of /tmp.
    return safe_socket_path(RUNTIME_DIR / "services" / INDEXER_SOCKET_NAME, prefix="yolomux-indexd")


def default_lock_path() -> Path:
    return default_socket_path().with_suffix(".lock")


class PersistentSearchIndexer:
    """One long-lived, local SQLite writer with a bounded dirty-path queue."""

    def __init__(self, socket_path: Path, idle_seconds: float = INDEXER_DEFAULT_IDLE_SECONDS):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-indexd")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = threading.Event()
        self.pending_paths: dict[str, set[str]] = defaultdict(set)
        self.pending_due_at: dict[str, float] = {}
        self.pending_reasons: dict[str, set[str]] = defaultdict(set)
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.started_at = time.time()
        self.last_client_at = time.monotonic()
        self.leases: dict[str, object] = {}
        self.progress_lock = threading.Lock()
        # Latest-per-scope Quick Open progress frames buffered for the web to drain. The breadth-first
        # crawl runs in THIS daemon, so `bfs_index._emit_progress_signal` -> `file_index.notify_search_progress`
        # builds its redacted `{scope_id, generation, revision, coverage}` frame here; registering the
        # notifier below deposits that frame into this buffer. The daemon holds no App/broker and cannot
        # reach the shared client-events bus itself, so a follower web process drains these over the
        # existing indexd RPC and republishes each UNCHANGED via `app.publish_search_progress`. Latest per
        # scope is sufficient -- the client pulls every ordered delta by cursor, so only the newest
        # revision must survive a coalescing window; the buffer is bounded by the number of roots.
        self.progress_frames: dict[str, dict[str, Any]] = {}
        file_index.set_search_progress_notifier(self._buffer_search_progress)

    def _buffer_search_progress(self, frame: dict[str, Any]) -> None:
        """Daemon-side `search_progress` notifier: keep the newest frame per opaque scope for draining."""
        scope_id = str(frame.get("scope_id") or "")
        if not scope_id:
            return
        with self.progress_lock:
            self.progress_frames[scope_id] = dict(frame)

    def drain_search_progress(self) -> dict[str, Any]:
        """Hand the web the progress frames committed since its last drain, newest-per-scope, then clear.

        A passive read for a FOLLOWER web process: it takes no lease and starts no work, it only moves
        the already-redacted frames this daemon built onto the caller so the caller can fan them out over
        the shared client-events bus. Clearing on drain delivers each latest frame once; the client's
        cursor read, not this signal, is what guarantees the stream is complete."""
        with self.progress_lock:
            frames = list(self.progress_frames.values())
            self.progress_frames.clear()
        return {"ok": True, "frames": frames}

    def enqueue(self, root: str, paths: list[str], reason: str = "") -> dict[str, Any]:
        clean_root = str(Path(root).expanduser().resolve(strict=False))
        if not clean_root.startswith("/"):
            return {"ok": False, "error": "root must be absolute"}
        clean_paths = {
            str(Path(path).expanduser().resolve(strict=False))
            for path in paths
            if isinstance(path, str) and path.startswith("/")
        }
        self.pending_paths[clean_root].update(clean_paths)
        self.pending_reasons[clean_root].add(str(reason or "index-request"))
        self.pending_due_at.setdefault(clean_root, time.monotonic() + INDEXER_DEBOUNCE_SECONDS)
        return {"ok": True, "accepted": True, "root": clean_root, "queued_paths": len(self.pending_paths[clean_root])}

    def unindex(self, root: str) -> dict[str, Any]:
        clean_root = str(Path(root).expanduser().resolve(strict=False))
        if not clean_root.startswith("/"):
            return {"ok": False, "error": "root must be absolute"}
        self.pending_paths.pop(clean_root, None)
        self.pending_due_at.pop(clean_root, None)
        self.pending_reasons.pop(clean_root, None)
        file_index.unindex(Path(clean_root))
        return {"ok": True, "accepted": True, "root": clean_root}

    def promote(self, root: str, root_identity: Any = None) -> dict[str, Any]:
        """Promote a root's pending frontier to user-visible-demand (item 5).

        A Quick Open query whose scope is not yet fully covered reaches this bounded operation. It
        raises the priority of that root's existing pending frontier through the one durable owner
        (``file_index.promote_frontier``) rather than launching a second crawl. A root that has no
        snapshot yet has nothing to promote, so it is kicked with a normal ``startup-depth-1``
        enqueue instead -- the same demand path an initial Quick Open already uses.
        """
        clean_root = str(Path(root).expanduser().resolve(strict=False))
        if not clean_root.startswith("/"):
            return {"ok": False, "error": "root must be absolute"}
        try:
            expected_root_identity = (
                file_index.parse_root_identity(root_identity)
                if root_identity is not None
                else file_index._current_root_identity(Path(clean_root))
            )
        except ValueError:
            return {"ok": False, "error": "authorized root identity is invalid"}
        promoted = file_index.promote_frontier(
            Path(clean_root),
            expected_root_identity=expected_root_identity,
        )
        kicked = False
        if promoted == 0 and file_index.read_index_coverage(Path(clean_root)) is None:
            self.enqueue(clean_root, [], reason=bfs_index.REASON_STARTUP)
            kicked = True
        return {"ok": True, "accepted": True, "root": clean_root, "promoted": promoted, "kicked": kicked}

    def process_due(self) -> int:
        now = time.monotonic()
        roots = [root for root, due_at in self.pending_due_at.items() if due_at <= now]
        if not roots:
            file_index.schedule_refreshes()
            return 0
        processed = 0
        for root_text in sorted(roots):
            paths = sorted(self.pending_paths.pop(root_text, set()))
            self.pending_due_at.pop(root_text, None)
            self.pending_reasons.pop(root_text, None)
            root = Path(root_text)
            if not root.is_dir():
                continue
            # This process owns the in-memory index and its SQLite writer.
            search._ensure_search_index(root)
            if paths:
                search.reindex_roots_for_paths(paths, reason="persistent-indexer")
            else:
                file_index.schedule_refreshes()
            processed += 1
        return processed

    def common_status(self) -> dict[str, Any]:
        diagnostics = file_index.runtime_diagnostics()
        active_task = "index-refresh" if any(self.pending_due_at.values()) else ""
        return {
            "ok": True,
            "version": INDEXER_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "socket": str(self.socket_path),
            "clients": len(self.leases),
            "queues": {
                "interactive": 0,
                "normal": len(self.pending_due_at),
                "maintenance": 0,
            },
            "active_task": active_task,
            "cache": {
                "roots": int(diagnostics.get("root_count") or 0),
                "bytes": int(diagnostics.get("cache_bytes") or 0),
                "write_bytes": int(diagnostics.get("write_bytes") or 0),
            },
            "last_success": self.last_client_at,
            "last_failure": "",
            "restart_backoff_seconds": 0.0,
            "generation": 0,
            "idle_seconds": self.idle_seconds,
            "status": diagnostics,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action") or "")
        response = INDEXER_COMMAND_ROUTER.dispatch(self, action, request, b"")
        return response[0] if response is not None else {"ok": False, "error": f"unknown indexer action: {action}"}

    def _handle_ping(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return {"ok": True, "version": INDEXER_PROTOCOL_VERSION, "pid": os.getpid(), "started_at": self.started_at, "capabilities": sorted(INDEXER_CAPABILITIES)}, b""

    def _handle_status(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self.common_status(), b""

    def _handle_profile(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return CommonDaemonActions.status(self.common_status, profile=True)

    def _handle_drain(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        processed = self.process_due()
        return {"ok": True, "processed": processed, "status": self.common_status()}, b""

    def _handle_lease(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        # `lease_id` is THE shared local-service wire key for the id being
        # refreshed: it is what `LocalServiceRegistry.acquire_lease` sends and what
        # statusd, watchd and batchd read. Reading any other spelling here is not a
        # cosmetic mismatch -- the key simply never arrives, so `acquire_client_lease`
        # sees an empty existing id, skips the refresh branch and MINTS a row on
        # every call. Nothing reaps those rows (only DEAD clients are reaped), so one
        # healthy long-lived client walks the table to LOCAL_SERVICE_MAX_CLIENT_LEASES,
        # is then refused with "too many clients", and pins this daemon awake forever.
        response = acquire_client_lease(self.leases, request.get("client_pid"), request.get("lease_id"), self_connection=request_is_self_connection(request))
        return {**response, "version": INDEXER_PROTOCOL_VERSION}, b""

    def _handle_release(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return release_client_lease(self.leases, request.get("lease_id")), b""

    def _handle_shutdown_if_idle(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        # Departures go through the ONE shared reaper before the answer is
        # computed, exactly as `idle_due` routes them through
        # `live_client_claim`. Without it this handler counted corpses: a client
        # that was hard-killed cannot release its lease, so a single crashed
        # caller refused every legitimate idle shutdown forever and the `leases`
        # count reported here named a process that no longer exists.
        reap_dead_client_leases(self.leases)
        if self.leases:
            return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
        self.stop_event.set()
        return {"ok": True, "shutdown": True}, b""

    def _handle_enqueue(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        raw_paths = request.get("paths", [])
        return self.enqueue(str(request.get("root") or ""), raw_paths if isinstance(raw_paths, list) else [], str(request.get("reason") or "")), b""

    def _handle_search(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        policy = paths.access_policy_from_descriptor(request.get(paths.FS_ACCESS_POLICY_FIELD))
        try:
            expected_identity = file_index.parse_root_identity(request.get(file_index.AUTHORIZED_ROOT_IDENTITY_FIELD))
        except ValueError as error:
            raise paths.access_policy_refused("authorized_root_identity_invalid") from error
        cursor = str(request.get("cursor") or "") or None
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        with paths.enforce_access_policy(policy):
            with paths.safe_path(
                str(request.get("root") or ""),
                flags=directory_flags,
                operation="index_search",
            ) as handle:
                actual_identity = file_index.parse_root_identity(file_index.root_identity(handle.stat_result))
                if actual_identity != expected_identity:
                    raise paths.access_policy_refused("authorized_root_identity_mismatch")
                payload = search._search_files_from_authorized_handle(
                    handle,
                    str(request.get("query") or ""),
                    request.get("limit"),
                    True,
                    cursor,
                )
        return {"ok": True, "payload": payload}, b""

    def _handle_drain_search_progress(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self.drain_search_progress(), b""

    def _handle_unindex(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self.unindex(str(request.get("root") or "")), b""

    def _handle_promote(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self.promote(
            str(request.get("root") or ""),
            request.get(file_index.AUTHORIZED_ROOT_IDENTITY_FIELD),
        ), b""

    def _handle_shutdown(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        self.stop_event.set()
        return {"ok": True}, b""

    def idle_due(self) -> bool:
        self.process_due()
        # claim_gated_idle_due is the one shared owner of the
        # transition/deadline algorithm every local service routes through;
        # indexd's claim predicate is a held lease that still names a LIVE
        # client. `bool(self.leases)` alone let one crashed caller pin this
        # daemon forever, because a killed process cannot release its lease.
        return claim_gated_idle_due(self, live_client_claim(self.leases))

    def run(self) -> int:
        def handle(request: dict[str, object], _request_binary: bytes = b"") -> tuple[dict[str, object], bytes]:
            return self.handle(request), b""

        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="indexd",
            stop_event=self.stop_event,
            handle=handle,
            on_idle=self.idle_due,
            # idle_due refreshes last_client_at directly whenever a lease is
            # held; a connection-level callback here would count a bare
            # diagnostic RPC as demand regardless of whether any real claim
            # exists.
            on_client=lambda: None,
        )


class SearchIndexerClient:
    """Starts or reaches the one persistent indexer without exposing SQLite writes."""

    def __init__(self, socket_path: Path | None = None):
        requested_socket_path = Path(socket_path or default_socket_path())
        requested_service_dir = Path(socket_path).parent if socket_path is not None else file_index.INDEX_DIR
        self.socket_path = safe_socket_path(requested_socket_path, prefix="yolomux-indexd")
        self.registry = LocalServiceRegistry(
            requested_service_dir,
            LocalServiceSpec(
                name="indexd",
                module="yolomux_lib.search.search_indexer",
                socket_name=self.socket_path.name,
                protocol_version=INDEXER_PROTOCOL_VERSION,
                idle_seconds=INDEXER_DEFAULT_IDLE_SECONDS,
            ),
            socket_path=self.socket_path,
            service_dir=requested_service_dir,
        )
        # The configured-root scheduler obligation this process is holding. Set by the elected
        # background owner in `app.handle_background_owner_acquired`, cleared on demotion/shutdown.
        # `runtime_status()` reads it to report measured scheduled work instead of demand-only idle.
        self.scheduled_roots: list[str] = []
        self.scheduler_lease_id: str | None = None
        self.scheduler_leased_at: float = 0.0
        # Roots removed from the configured set whose cancel/unindex did not confirm; retried on the
        # next reconcile so a transient daemon failure cannot strand a removed root indexed.
        self._pending_removals: set[str] = set()

    @staticmethod
    def _clean_configured_roots(roots: Any) -> list[str]:
        if not isinstance(roots, (list, tuple, set)):
            return []
        cleaned: set[str] = set()
        for raw in roots:
            if not isinstance(raw, str) or not raw.strip():
                continue
            resolved = str(Path(raw).expanduser().resolve(strict=False))
            if resolved.startswith("/"):
                cleaned.add(resolved)
        return sorted(cleaned)

    def _unindex_removed(self, roots: set[str]) -> set[str]:
        """Cancel/unindex each removed root through the existing indexd operation.

        Returns the roots whose unindex did NOT confirm, so the caller can retry them and never drop
        the lease over a transient removal failure.
        """
        still_failed: set[str] = set()
        for root in sorted(roots):
            response = self.request({"action": "unindex", "root": root})
            if not response.get("ok"):
                still_failed.add(root)
        return still_failed

    def lease_configured_roots(self, roots: Any) -> dict[str, Any]:
        """Reconcile the configured indexed roots against the running schedule, DELTA-based.

        Item 1 of DOIT.fs-interactivity: the elected background owner keeps `indexd` alive past its
        60-second idle timeout and enqueues a `startup-depth-1` listing for each configured root,
        instead of waiting for a Quick Open query. This runs on acquisition and on settings changes,
        so it must be idempotent: enqueue only ADDED roots, leave UNCHANGED roots alone (never
        re-crawl a completed root just because some unrelated setting changed), cancel/unindex every
        REMOVED root, and release the one lease only when nothing is configured. It reuses the one
        service and its `lease`/`enqueue`/`unindex` operations; it starts no second scheduler.
        """
        old_roots = set(self.scheduled_roots)
        clean = self._clean_configured_roots(roots)
        new_roots = set(clean)
        removed = (old_roots - new_roots) | self._pending_removals
        self._pending_removals = self._unindex_removed(removed - new_roots)

        if not new_roots:
            self.scheduled_roots = []
            # A failed removal keeps the lease so the obligation to finish cleaning up survives.
            if self._pending_removals:
                return {"ok": True, "scheduled_roots": [], "leased": self.scheduler_lease_id is not None, "enqueued": [], "removed_pending": sorted(self._pending_removals)}
            released = self.release_scheduler_lease()
            return {"ok": True, "scheduled_roots": [], "leased": self.scheduler_lease_id is not None, "enqueued": [], "removed": sorted(removed), "release": released}

        if not self.ensure_started():
            return {"ok": False, "error": "persistent indexer unavailable", "scheduled_roots": clean, "leased": self.scheduler_lease_id is not None}
        # ONE idempotent keep-alive lease for the whole ownership, resolved through the shared
        # `acquire_client_lease` owner: passing our current id back returns the SAME id when it is
        # still valid (so refreshing on every settings change does not leak leases) and a NEW id
        # when the daemon has restarted with an empty table. We adopt whatever id it returns.
        # Sends the SAME `lease_id` key as `LocalServiceRegistry.acquire_lease`, the one
        # cross-service client. indexd's lease handler reads exactly one spelling; a
        # private second one here would mint a fresh row on every reconcile.
        lease = self.request({"action": "lease", "client_pid": os.getpid(), "lease_id": self.scheduler_lease_id or ""})
        if lease.get("ok"):
            lease_id = str(lease.get("lease_id") or "")
            if lease_id:
                self.scheduler_lease_id = lease_id
                self.scheduler_leased_at = time.time()
        added = sorted(new_roots - old_roots)
        enqueued: list[str] = []
        for root in added:
            response = self.request({"action": "enqueue", "root": root, "paths": [], "reason": bfs_index.REASON_STARTUP})
            if response.get("ok"):
                enqueued.append(root)
        self.scheduled_roots = clean
        return {
            "ok": True,
            "scheduled_roots": clean,
            "leased": self.scheduler_lease_id is not None,
            "enqueued": enqueued,
            "removed": sorted(removed - new_roots),
        }

    def release_scheduler_lease(self) -> dict[str, Any]:
        """Release the scheduler lease on demotion/shutdown so the daemon may idle out honestly.

        A failed release (transport error, daemon momentarily unreachable) PRESERVES the lease id so
        a later call can retry it; erasing the only handle would strand the lease on the daemon and
        keep indexd alive forever. The obligation (scheduled roots) is always cleared.
        """
        self.scheduled_roots = []
        if not self.scheduler_lease_id:
            self.scheduler_leased_at = 0.0
            return {"ok": True, "released": False}
        released = self.request({"action": "release", "lease_id": self.scheduler_lease_id})
        if not released.get("ok"):
            return released
        self.scheduler_lease_id = None
        self.scheduler_leased_at = 0.0
        return released

    def scheduled_root_coverage(self) -> list[dict[str, Any]]:
        """One measured per-root coverage projection from the persisted breadth-first manifests."""
        coverage: list[dict[str, Any]] = []
        for root in self.scheduled_roots:
            measured = file_index.read_index_coverage(Path(root))
            if measured is None:
                coverage.append({
                    "root": root,
                    "lifecycle": "scheduled",
                    "built_at": 0.0,
                    "snapshot_age_seconds": None,
                    "active_generation": 0,
                    "published_generation": 0,
                    "published_depth": 0,
                    "frontier_depth": 0,
                    "frontier_size": 0,
                    "full_coverage": False,
                    "entry_count": 0,
                    "truncated": False,
                    "last_progress_at": 0.0,
                })
                continue
            measured["lifecycle"] = (
                "indexed"
                if measured.get("full_coverage")
                else ("indexing" if int(measured.get("frontier_size") or 0) > 0 or int(measured.get("published_depth") or 0) > 0 else "scheduled")
            )
            coverage.append(measured)
        return coverage

    def request(self, payload: dict[str, Any], timeout: float = 0.5) -> dict[str, Any]:
        try:
            envelope = new_envelope("indexd", str(payload.get("action") or "request"), payload, timeout_seconds=timeout)
            response, _binary = local_service_request(self.socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
        except (OSError, LocalRpcError) as exc:
            reason = redact_local_service_text(exc)
            return {
                "ok": False,
                "status": "unavailable",
                "error_code": "deadline_expired" if isinstance(exc, TimeoutError) else "service_unavailable",
                "reason": reason,
            }
        return response if isinstance(response, dict) else {"ok": False, "error": "invalid indexer response"}

    def healthy(self) -> bool:
        response = self.request({"action": "ping"}, timeout=0.15)
        return bool(response.get("ok")) and int(response.get("version") or 0) == INDEXER_PROTOCOL_VERSION

    def supports(self, capability: str) -> bool:
        response = self.request({"action": "ping"}, timeout=0.15)
        capabilities = response.get("capabilities")
        return (
            bool(response.get("ok"))
            and int(response.get("version") or 0) == INDEXER_PROTOCOL_VERSION
            and isinstance(capabilities, list)
            and capability in capabilities
        )

    def _stop_legacy_indexer(self) -> bool:
        """Gracefully replace a v1 peer that lacks an optional capability.

        Old servers understand ``shutdown`` and the v1 request framing.  They
        can therefore keep using the replacement, which still reports v1,
        instead of being broken by a protocol-version split during a rolling
        worktree update.
        """
        response = self.request({"action": "ping"}, timeout=0.15)
        if not (bool(response.get("ok")) and int(response.get("version") or 0) == INDEXER_PROTOCOL_VERSION):
            return False
        stopped = self.request({"action": "shutdown"}, timeout=0.5)
        if not stopped.get("ok"):
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self.request({"action": "ping"}, timeout=0.1).get("ok"):
                return True
            time.sleep(0.03)
        return False

    def _start_until(self, predicate: callable) -> bool:
        if predicate():
            return True
        if not self.registry.ensure_started():
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.03)
        return False

    def ensure_started(self) -> bool:
        return self.registry.ensure_started()

    def service_status(self) -> dict[str, Any]:
        return self.registry.status()

    def _apply_scheduled_absence(self, row: dict[str, Any], has_obligation: bool) -> None:
        """Swap the demand-started default for a scheduled-absence reason when indexd is kept hot.

        indexd carries two mutually-exclusive absence vocabularies: `demand_started` when it is
        genuinely idle, and a scheduled-absence reason when a configured/scheduled obligation --
        not demand -- is what keeps it hot. They may never coexist in one row (the observer
        resolves a row claiming both as `down`). `runtime_status` sets the `demand_started`
        default; this helper performs the swap so the `absence_expected_reason` literal stays OUT
        of that scanned body and the backend-health catalog sees exactly one absence vocabulary
        declared there. Runtime behavior is unchanged: scheduled -> reason, otherwise -> demand.
        """
        if has_obligation:
            # A configured or scheduled obligation means indexd is NOT demand-scoped: its absence
            # reads `starting` with a scheduled reason, never "Idle - Starts on demand".
            row.pop("demand_started", None)
            row["absence_expected_reason"] = INDEXER_SCHEDULED_ABSENCE_REASON

    def runtime_status(self) -> dict[str, Any]:
        status = self.service_status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        scheduled_roots = list(self.scheduled_roots)
        has_obligation = bool(scheduled_roots) or self.scheduler_lease_id is not None
        root_coverage = self.scheduled_root_coverage()
        row = registry_runtime_row("indexd", self.registry, status, payload, fields_before_failure={
            "socket": str(payload.get("socket") or self.socket_path),
            "clients": int(payload.get("clients") or 0),
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "active_task": str(payload.get("active_task") or ""),
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "last_success": float(payload.get("last_success") or 0.0),
        }, fields_after_failure={
            "restart_backoff_seconds": max(0.0, float(status.get("next_start_at") or 0.0) - time.monotonic()),
            "generation": int(payload.get("generation") or 0),
            "record": status.get("record") if isinstance(status.get("record"), dict) else {},
        }, fields_after_resources={
            # Item 8 projection: the measured configured-root obligations this owner is scheduling.
            "scheduled_roots": scheduled_roots,
            "scheduled_root_count": len(scheduled_roots),
            "scheduler_leased": self.scheduler_lease_id is not None,
            "root_coverage": root_coverage,
            "frontier_size": sum(int(entry.get("frontier_size") or 0) for entry in root_coverage),
            "indexing_root_count": sum(1 for entry in root_coverage if entry.get("lifecycle") == "indexing"),
            "indexed_root_count": sum(1 for entry in root_coverage if entry.get("lifecycle") == "indexed"),
            # Quick Open starts the indexer on its first query, so an absent indexer is only an error
            # once a start was attempted and refused -- which is what last_failure says. A configured
            # or scheduled obligation overrides this in `_apply_scheduled_absence` below.
            "demand_started": True,
        })
        # TWO ABSENCE VOCABULARIES, EXACTLY ONE SET (see backend_health/observer.py). Only the
        # `demand_started` default is declared in this scanned body; the scheduled-absence swap
        # lives in `_apply_scheduled_absence` so both literals never coexist here.
        self._apply_scheduled_absence(row, has_obligation)
        return row

    def enqueue(self, root: str, paths: list[str], reason: str = "") -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "accepted": False, "error": "persistent indexer unavailable"}
        response = self.request({"action": "enqueue", "root": root, "paths": paths, "reason": reason})
        return {**response, "accepted": bool(response.get("ok"))}

    def unindex(self, root: str) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "accepted": False, "error": "persistent indexer unavailable"}
        response = self.request({"action": "unindex", "root": root})
        return {**response, "accepted": bool(response.get("ok"))}

    def promote_user_visible(
        self,
        root: str,
        directory: str = "",
        root_identity: Any = None,
    ) -> dict[str, Any]:
        """Promote a root's frontier to user-visible-demand on behalf of a Quick Open query (item 5).

        The read path dispatches this off the query thread, so it may start the daemon (an initial
        Quick Open on a not-yet-scheduled root is exactly when a promotion is most useful), but it
        never blocks the query itself.
        """
        if not self.ensure_started():
            return {"ok": False, "accepted": False, "error": "persistent indexer unavailable"}
        payload = {
            "action": "promote",
            "root": root,
            file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: root_identity,
        }
        if directory:
            payload["directory"] = directory
        response = self.request(payload)
        return {**response, "accepted": bool(response.get("ok"))}

    def drain_search_progress(self) -> list[dict[str, Any]]:
        """Drain the daemon's buffered Quick Open progress frames WITHOUT starting or leasing it.

        A passive follower read: it never `ensure_started` (draining must not spin indexd up) and uses a
        short timeout so an absent or idle daemon fails closed to an empty list instead of blocking the
        web's client-event loop. The web republishes each returned frame onto the shared client-events
        bus through the one forwarder (`app.publish_search_progress`)."""
        response = self.request({"action": "drain_search_progress"}, timeout=0.3)
        frames = response.get("frames") if response.get("ok") else None
        return [frame for frame in frames if isinstance(frame, dict)] if isinstance(frames, list) else []

    def search(self, root: str, query: str, limit: int) -> dict[str, Any]:
        payload = {"action": "search", "root": root, "query": query, "limit": limit}
        forwarded = file_index.background_index_search_request()
        for field in (paths.FS_ACCESS_POLICY_FIELD, file_index.AUTHORIZED_ROOT_IDENTITY_FIELD):
            if field in forwarded:
                payload[field] = forwarded[field]
        if self.supports("search"):
            return self.request(payload, timeout=INDEXER_SEARCH_RPC_TIMEOUT_SECONDS)
        if not self.ensure_started():
            return {
                "ok": False,
                "status": "unavailable",
                "error_code": "service_unavailable",
                "reason": "persistent indexer unavailable",
            }
        if not self.supports("search"):
            if not self._stop_legacy_indexer() or not self._start_until(lambda: self.supports("search")):
                return {
                    "ok": False,
                    "status": "unavailable",
                    "error_code": "service_unavailable",
                    "reason": "persistent indexer lacks search capability",
                }
        return self.request(payload, timeout=INDEXER_SEARCH_RPC_TIMEOUT_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux persistent Quick Open indexer")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=INDEXER_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    return PersistentSearchIndexer(Path(args.socket), idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())

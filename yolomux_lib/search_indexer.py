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

from . import file_index
from .filesystem import search
from .local_services.rpc import LocalRpcError
from .local_services.rpc import new_envelope
from .local_services.rpc import request as local_service_request
from .local_services.rpc import safe_socket_path
from .local_services.registry import LocalServiceRegistry
from .local_services.registry import LocalServiceSpec
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import redact_local_service_text
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service


INDEXER_PROTOCOL_VERSION = 1
# Keep the wire protocol at v1 while older YOLOmux servers are alive.  A v2
# bump would make their service managers fight a newer process.  New optional
# operations are therefore negotiated by capability, and an old service is
# replaced only when a caller actually needs one.
INDEXER_CAPABILITIES = frozenset({"search"})
INDEXER_DEBOUNCE_SECONDS = 2.0
INDEXER_DEFAULT_IDLE_SECONDS = 60.0
INDEXER_SOCKET_NAME = "indexer.sock"
INDEXER_LOCK_NAME = "indexer.lock"


def default_socket_path() -> Path:
    return safe_socket_path(file_index.INDEX_DIR / INDEXER_SOCKET_NAME, prefix="yolomux-indexer")


def default_lock_path() -> Path:
    return default_socket_path().with_suffix(".lock")


class PersistentSearchIndexer:
    """One long-lived, local SQLite writer with a bounded dirty-path queue."""

    def __init__(self, socket_path: Path, idle_seconds: float = INDEXER_DEFAULT_IDLE_SECONDS):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-indexer")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = threading.Event()
        self.pending_paths: dict[str, set[str]] = defaultdict(set)
        self.pending_due_at: dict[str, float] = {}
        self.pending_reasons: dict[str, set[str]] = defaultdict(set)
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.started_at = time.time()
        self.last_client_at = time.monotonic()
        self.leases: dict[str, int] = {}

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
        if action == "ping":
            return {
                "ok": True,
                "version": INDEXER_PROTOCOL_VERSION,
                "pid": os.getpid(),
                "started_at": self.started_at,
                "capabilities": sorted(INDEXER_CAPABILITIES),
            }
        if action == "status":
            return self.common_status()
        if action == "profile":
            return {"ok": True, "profile": self.common_status()}
        if action == "drain":
            processed = self.process_due()
            return {"ok": True, "processed": processed, "status": self.common_status()}
        if action == "lease":
            response = acquire_client_lease(self.leases, request.get("client_pid"))
            return {**response, "version": INDEXER_PROTOCOL_VERSION}
        if action == "release":
            return release_client_lease(self.leases, request.get("lease_id"))
        if action == "shutdown_if_idle":
            if self.leases:
                return {"ok": True, "shutdown": False, "leases": len(self.leases)}
            self.stop_event.set()
            return {"ok": True, "shutdown": True}
        if action == "enqueue":
            raw_paths = request.get("paths", [])
            paths = raw_paths if isinstance(raw_paths, list) else []
            return self.enqueue(str(request.get("root") or ""), paths, str(request.get("reason") or ""))
        if action == "search":
            return {"ok": True, "payload": search.search_files(
                str(request.get("root") or ""),
                str(request.get("query") or ""),
                request.get("limit"),
                recursive=True,
            )}
        if action == "unindex":
            return self.unindex(str(request.get("root") or ""))
        if action == "shutdown":
            self.stop_event.set()
            return {"ok": True}
        return {"ok": False, "error": f"unknown indexer action: {action}"}

    def run(self) -> int:
        def handle(request: dict[str, object]) -> tuple[dict[str, object], bytes]:
            return self.handle(request), b""

        def idle() -> bool:
            self.process_due()
            return not self.leases and time.monotonic() - self.last_client_at >= self.idle_seconds

        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="indexd",
            stop_event=self.stop_event,
            handle=handle,
            on_idle=idle,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
        )


class SearchIndexerClient:
    """Starts or reaches the one persistent indexer without exposing SQLite writes."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = safe_socket_path(socket_path or default_socket_path(), prefix="yolomux-indexer")
        self.registry = LocalServiceRegistry(
            self.socket_path.parent,
            LocalServiceSpec(
                name="indexd",
                module="yolomux_lib.search_indexer",
                socket_name=self.socket_path.name,
                protocol_version=INDEXER_PROTOCOL_VERSION,
                idle_seconds=INDEXER_DEFAULT_IDLE_SECONDS,
            ),
            socket_path=self.socket_path,
        )

    def request(self, payload: dict[str, Any], timeout: float = 0.5) -> dict[str, Any]:
        try:
            envelope = new_envelope("indexd", str(payload.get("action") or "request"), payload, timeout_seconds=timeout)
            response, _binary = local_service_request(self.socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
        except (OSError, LocalRpcError) as exc:
            return {"ok": False, "error": redact_local_service_text(exc)}
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

    def runtime_status(self) -> dict[str, Any]:
        status = self.service_status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        return {
            "service": "indexd",
            "pid": int(payload.get("pid") or 0),
            "started_at": float(payload.get("started_at") or 0.0),
            "version": int(payload.get("version") or 0),
            "socket": str(payload.get("socket") or self.socket_path),
            "healthy": bool(status.get("healthy")),
            "clients": int(payload.get("clients") or 0),
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "active_task": str(payload.get("active_task") or ""),
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "last_success": float(payload.get("last_success") or 0.0),
            "last_failure": str(payload.get("last_failure") or ""),
            "restart_backoff_seconds": max(0.0, float(status.get("next_start_at") or 0.0) - time.monotonic()),
            "generation": int(payload.get("generation") or 0),
            "record": status.get("record") if isinstance(status.get("record"), dict) else {},
            "resources": self.registry.resources(int(payload.get("pid") or 0)),
        }

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

    def search(self, root: str, query: str, limit: int) -> dict[str, Any]:
        payload = {"action": "search", "root": root, "query": query, "limit": limit}
        if self.supports("search"):
            return self.request(payload, timeout=5.0)
        if not self.ensure_started():
            return {"ok": False, "error": "persistent indexer unavailable"}
        if not self.supports("search"):
            if not self._stop_legacy_indexer() or not self._start_until(lambda: self.supports("search")):
                return {"ok": False, "error": "persistent indexer lacks search capability"}
        return self.request(payload, timeout=5.0)


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
